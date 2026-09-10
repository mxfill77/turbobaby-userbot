"""V0 content-product verifier: the bounded evidence bundle, not the report, decides.

This module has no CLI.  It consumes one explicit, bounded evidence bundle and
returns a deterministic three-state verdict.  It never executes candidate code
or tests, and never discovers files outside the manifest supplied by the caller.

Freshness is proven by identity, never by time: ``PROVEN`` requires the evidence
itself to name the run being verified (``V0_RUN_BINDING``).  No clock, mtime or
"looks recent" reasoning exists here, because an artifact left by an earlier run
is as recent as the file system says and still proves nothing about this one.

01.09.2026 -- two additive changes, made so the PC lane could put V0 on the one
place that closes a task (``done_judge_pc``).  Neither relaxes an existing rule
and both are opt-in, so every bundle written before today verifies to the same
bytes:

* ``text_contains_ci`` -- a case-folded sibling of ``text_contains``.  A named
  phrase that opens a heading is the same phrase; the exact gate answered a
  question about letter case and it was being read as an answer about the
  product.
* ``run_binding: measured`` in the TASK packet -- evidence inside the exact
  changed set counts as bound to this run without declaring ``run_id`` itself.
  It is chosen by the task and never by the executor bundle, because it is worth
  precisely as much as the party that measured the baseline.  Where the baseline
  is the executor's word (the standing asymmetry named in
  ``docs/artifacts/2026-09-01-v0-negative-test.md``) it is worth nothing, and the
  default stays ``declared``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re


SCHEMA = "turbobaby.content_product_verifier/v0.1"
PROVEN = "PROVEN"
DISPROVEN = "DISPROVEN"
UNKNOWN = "UNKNOWN"
VERDICTS = (PROVEN, DISPROVEN, UNKNOWN)

MAX_OUTPUT_BYTES = 32_000
DEFAULT_MAX_BYTES = 200_000
MAX_CASE_ID_CHARS = 128
MAX_PATH_CHARS = 240
MAX_IDENTIFIER_CHARS = 80
MAX_MANIFEST_RECORDS = 64
MAX_ARTIFACTS = 32
MAX_TEST_EVIDENCE = 32
MAX_CONTENT_GATES = 32
ALLOWED_CONTENT_TYPES = ("json", "text", "python")
ALLOWED_GATE_TYPES = (
    "json_field_equals",
    "json_field_present",
    "text_contains",
    "text_contains_ci",
    "path_or_text_contains_ci",
    "text_absent",
    "python_import_allowlist",
)
RUN_ID_FIELD = "run_id"
# How the run is bound to its evidence.  ``declared`` is the default and the
# only self-contained mode: the artifact must name the run itself.  ``measured``
# says the caller compared the address before and after the run with its OWN
# baseline, so a path inside the exact changed set belongs to this run by
# construction.  Weaker in one named way and stronger in another: it cannot be
# faked by text, and it is exactly as trustworthy as whoever measured the
# baseline -- which is why only the TASK may select it, never the bundle.
RUN_BINDING_FIELD = "run_binding"
RUN_BINDING_DECLARED = "declared"
RUN_BINDING_MEASURED = "measured"
RUN_BINDINGS = (RUN_BINDING_DECLARED, RUN_BINDING_MEASURED)
# A run identity is a bounded token, long enough not to match by accident and
# compared whole: "job-1" must never bind evidence that names "job-12".
_RUN_ID_BODY = r"[0-9A-Za-z][0-9A-Za-z_.-]{3,%d}" % (MAX_IDENTIFIER_CHARS - 1)
_RUN_ID_RE = re.compile(_RUN_ID_BODY)
_RUN_DECLARATION_RE = re.compile(
    r"(?<![0-9A-Za-z_])[\"']?%s[\"']?\s*[:=]\s*[\"']?(%s)" % (RUN_ID_FIELD, _RUN_ID_BODY))
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
# TWO FACTS THAT USED TO BE ONE.  "This text CARRIES the value of a secret" and
# "this text SPEAKS about secrets" are different claims, and one pattern could
# only answer their union.  The split below is the whole of the 11.09.2026 change.
#
# ``_SECRET_VALUE_RE`` holds the shapes that ARE a value standing alone: an AWS
# id, a PEM header, an ``sk-`` token.  Each is a value by its own form -- no
# neighbouring word is needed to make it one, and prose describing a secret has
# no reason to contain one.  These keep the hard refusal they always had.
_SECRET_VALUE_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bsk-[A-Za-z0-9_-]{16,})",
    re.IGNORECASE,
)
# ``_SENSITIVE_RE`` is the union as it always was -- the alternation is
# character-for-character the old one, only named groups were added, so ``search``
# still matches exactly what it matched before.  Its extra arm is the NAMED PAIR:
# ``named`` is the key NAME plus its separator (and an opening quote if there was
# one), ``value`` is the run that follows.  That arm is the one that cannot tell
# the two facts apart: "ANTHROPIC_API_KEY: разошлись" and "ANTHROPIC_API_KEY: <a
# live key>" are the same shape.  So the name is kept, the value is dropped, and
# the question "was that value real?" is never asked -- see ``_redact_sensitive``.
_SENSITIVE_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?P<named>[\"']?(?:api[_-]?key|secret|password)[\"']?\s*[:=]\s*[\"']?)(?P<value>\S+)"
    r"|\bsk-[A-Za-z0-9_-]{16,})",
    re.IGNORECASE,
)
# What replaces a redacted span.  ONE character, and it carries NO word character
# by construction: ``_ADDR_WORD_RE`` reads an address word as an unbroken run of
# letters and digits, so a marker made of letters could bridge two neighbouring
# words into an address hit that the file does not contain.  One character also
# stays far under ``_ADDR_GAP_MAX``, so a redaction standing BETWEEN two address
# words cannot break a hit that the file does contain.
REDACTION = "·"


def _redact_sensitive(text):
    """Blank what FOLLOWS a key name, keep the name.  -> (text, spans redacted).

    Reached only after ``_SECRET_VALUE_RE`` has already refused the text, so in
    practice every span here is a named pair.

    WHY REDACT INSTEAD OF REFUSE, for this arm only.  Refusing the whole read
    keeps secrets out of the verdict and also destroys the verdict: an artifact
    that DISCUSSES a key -- naming the variable, quoting the assertion that
    caught it -- trips the named arm without containing one secret, and the run
    that wrote it gets no verdict at all.  That is not a hypothetical; it is what
    closed run 245 as a failure.  Redaction gives the same guarantee strictly
    earlier: the value never enters ``text``, so there is nothing left to leak.

    WHAT THIS DOES NOT CLAIM, said out loud because the temptation is real.  It
    does not decide that the blanked run was prose.  Deciding that would mean
    comparing it against the live secret -- holding the value in order to judge
    it, which is the event the boundary exists to prevent.  The design answer is
    to stop needing the answer: both readings are blanked, so the verdict no
    longer depends on which one it was.

    ONE THING THIS DELIBERATELY COSTS, named rather than hidden: if an address
    word happened to stand inside a redacted run, it disappears with it and the
    address goes unproven.  That failure is conservative -- it can only withhold
    a verdict, never grant one -- and it is the same outcome the refusal gave.
    """
    count = 0

    def _swap(match):
        nonlocal count
        count += 1
        named = match.group("named")
        return REDACTION if named is None else named + REDACTION

    return _SENSITIVE_RE.sub(_swap, text), count


class VerificationInputError(ValueError):
    """The caller supplied an incomplete or ambiguous bounded bundle."""


class UnsafeEvidenceError(ValueError):
    """A declared path or body crossed a V0 security boundary."""


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value):
    return _sha256_bytes(value.encode("utf-8"))


def _valid_sha256(value, nullable=False):
    return (nullable and value is None) or (isinstance(value, str) and bool(_SHA256_RE.fullmatch(value)))


def _normalise_rel(path):
    if not isinstance(path, str) or not path.strip():
        raise UnsafeEvidenceError("empty_path")
    if len(path) > MAX_PATH_CHARS:
        raise UnsafeEvidenceError("path_too_long")
    if os.path.isabs(path) or _DRIVE_RE.match(path) or path.startswith(("//", "\\\\")):
        raise UnsafeEvidenceError("absolute_path")
    parts = []
    for part in re.split(r"[\\/]", path):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafeEvidenceError("path_traversal")
        parts.append(part)
    if not parts:
        raise UnsafeEvidenceError("empty_path")
    return "/".join(parts)


def _root_path(root, rel):
    if not isinstance(root, str) or not os.path.isabs(root):
        raise VerificationInputError("invalid_workspace_root")
    root_abs = os.path.abspath(root)
    if os.path.islink(root_abs):
        raise UnsafeEvidenceError("workspace_root_symlink")
    full = os.path.abspath(os.path.join(root_abs, rel.replace("/", os.sep)))
    try:
        inside = os.path.commonpath((root_abs, full)) == root_abs
    except ValueError:
        inside = False
    if not inside:
        raise UnsafeEvidenceError("outside_workspace_root")
    current = root_abs
    for part in rel.split("/"):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise UnsafeEvidenceError("symlink")
    # ``commonpath`` above is lexical.  This second check catches a platform
    # junction/symlink that resolves outside root even when the named leaf is
    # not itself a symlink.
    root_real = os.path.realpath(root_abs)
    full_real = os.path.realpath(full)
    try:
        if os.path.commonpath((root_real, full_real)) != root_real:
            raise UnsafeEvidenceError("resolved_outside_workspace_root")
    except ValueError as exc:
        raise UnsafeEvidenceError("resolved_outside_workspace_root") from exc
    return full


def _max_bytes(record):
    value = record.get("max_bytes", DEFAULT_MAX_BYTES)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > DEFAULT_MAX_BYTES:
        raise VerificationInputError("invalid_max_bytes")
    return value


def _read_declared(root, record, *, expected_types=None):
    if not isinstance(record, dict):
        raise VerificationInputError("invalid_file_record")
    rel = _normalise_rel(record.get("path"))
    max_bytes = _max_bytes(record)
    expected_hash = record.get("sha256")
    if not _valid_sha256(expected_hash):
        raise VerificationInputError("invalid_sha256")
    content_type = record.get("content_type", "text")
    if expected_types is not None and content_type not in expected_types:
        raise VerificationInputError("invalid_content_type")
    full = _root_path(root, rel)
    if os.path.islink(full):
        raise UnsafeEvidenceError("symlink")
    if not os.path.isfile(full):
        raise FileNotFoundError(rel)
    if os.path.getsize(full) > max_bytes:
        raise UnsafeEvidenceError("max_bytes_exceeded")
    with open(full, "rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UnsafeEvidenceError("max_bytes_exceeded")
    if _sha256_bytes(data) != expected_hash:
        raise UnsafeEvidenceError("hash_mismatch")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsafeEvidenceError("binary_or_non_utf8") from exc
    if _SECRET_VALUE_RE.search(text):
        # THE HALF THAT DID NOT MOVE.  A value-shaped run is a secret whatever
        # stands around it, so this is the same refusal with the same reason code
        # the file has always raised -- and it is checked over the WHOLE text,
        # before any split, so ``api_key: sk-<token>`` is refused here rather
        # than quietly redacted by the named arm that would also have matched it.
        raise UnsafeEvidenceError("sensitive_content")
    redactions = 0
    if content_type == "json":
        # RECEIPTS AND PACKETS KEEP THE HARD BOUNDARY, and the asymmetry is
        # deliberate.  A json body here is machine evidence this verifier itself
        # parses -- a test receipt, a task packet -- and a secret inside one is a
        # real leak with no prose to save.  Redacting it would also break the
        # parse in a way that reads like a different fault.  Prose artifacts get
        # redaction; machine evidence gets the refusal it always had.
        if _SENSITIVE_RE.search(text):
            raise UnsafeEvidenceError("sensitive_content")
    else:
        text, redactions = _redact_sensitive(text)
    return {"path": rel, "content_type": content_type, "text": text,
            "sha256": expected_hash, "redactions": redactions}


def _parse_json(evidence):
    try:
        value = json.loads(evidence["text"])
    except (TypeError, ValueError) as exc:
        raise VerificationInputError("invalid_json") from exc
    if not isinstance(value, dict):
        raise VerificationInputError("json_not_object")
    return value


_SEPARATOR_RE = re.compile(r"[-_/.\\]+")
# An address word is an unbroken run of letters and digits.  EVERYTHING else --
# space, newline, any punctuation, any markup -- is a separator between words.
# The list is defined by exclusion rather than enumerated, because an enumerated
# list of punctuation is a list of the marks someone happened to think of: the
# first unlisted one (a dash from a different codepoint, a typographic quote, a
# markdown asterisk) would silently fail a proven artifact again.  ``_`` is
# forced into the separator side: in a file NAME it stands exactly where ``-``
# stands, and ``\w`` would otherwise count it as a letter.
_ADDR_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
# The gap ceiling is MEASURED, not chosen: 520 live artifacts, 5309 gaps between
# adjacent words of their headings -- the longest legitimate one is 5 chars
# (``) -- ``), while the shortest markdown scaffolding run in bodies is 12
# (``  |\n\n---\n\n## ``, a table edge).  10 sits above twice the legitimate
# maximum and below the scaffolding floor, so address words may cross a comma or
# a bold marker but never a table border or a horizontal rule.
_ADDR_GAP_MAX = 10


def _words_in_order(haystack, needle):
    """Do the address words stand CONSECUTIVELY in this text?

    Consecutive means: same words, same order, and between them nothing but
    punctuation and whitespace -- not one letter and not one digit.  That last
    clause is the whole guarantee.  Drop it and the check degrades into "are
    these words somewhere in the file", which any long report satisfies and
    which therefore proves nothing at all.
    """
    words = _ADDR_WORD_RE.findall(needle)
    if not words:
        return False
    gap = r"[\W_]{1,%d}" % _ADDR_GAP_MAX
    pattern = r"(?<![^\W_])%s(?![^\W_])" % gap.join(re.escape(word) for word in words)
    return re.search(pattern, haystack) is not None


def address_hit(path, text, needle):
    """Does this artifact answer a named address -- by its body or by its NAME?

    A named address ("a file ... with the words X") is answered either way, and a
    check that reads only one side reports a fact about naming style and lets it
    pass for a fact about the product.  Separators in the path are read as spaces,
    because ``x-y-z.md`` is how a file is named ``x y z``.

    04.09.2026 -- the words are compared as a WORD SEQUENCE, not as a literal
    substring.  Three closures (195, 208, 210) were sent to DISPROVEN with the
    work done and committed; in two of them the address words were named as a
    phrase and the heading carried the same words with a comma inside it, so a
    comma the writer put between "зелёный" and "причина" was reported as a fact
    about the product.  Nothing is loosened besides that: a word the artifact
    does not carry, or words standing apart with other words between them, still
    fail.
    """
    folded = needle.casefold()
    if folded in text.casefold() or folded in _SEPARATOR_RE.sub(" ", path).casefold():
        return True
    return _words_in_order(text.casefold(), folded) or _words_in_order(path.casefold(), folded)


def _gate(gate_id, status, reason_code, evidence_refs=()):
    return {
        "gate_id": gate_id,
        "status": status,
        "reason_code": reason_code,
        "evidence_refs": list(evidence_refs),
    }


def _identifier(value, label):
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_CHARS:
        raise VerificationInputError("invalid_%s" % label)
    return value


def _safe_case_id(value):
    return value if isinstance(value, str) and 0 < len(value) <= MAX_CASE_ID_CHARS else ""


def _unsafe_verdict(reason):
    """Unreadable or secret-like evidence is uncertainty, not a contradiction."""
    return UNKNOWN if reason in ("binary_or_non_utf8", "sensitive_content") else DISPROVEN


def _output(case_id, verdict, reason_code, reported_claim, gates, unknowns=(), verified_scope=(), refs=None):
    result = {
        "schema_version": SCHEMA,
        "case_id": _safe_case_id(case_id),
        "verdict": verdict,
        "reason_code": reason_code,
        "reported_claim": reported_claim,
        "verified_scope": sorted(verified_scope),
        "input_refs": refs or {},
        "gates": gates,
        "unknowns": list(unknowns),
        "forbidden_actions_observed": [],
        "next_action": "none" if verdict == PROVEN else "owner_review",
    }
    encoded = _canonical(result).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        # Rebuild rather than mutate: no caller-controlled gate, path or id may
        # survive this fallback and bypass the stated output bound.
        result = {
            "schema_version": SCHEMA,
            "case_id": _safe_case_id(case_id),
            "verdict": UNKNOWN,
            "reason_code": "output_too_large",
            "reported_claim": "unknown",
            "verified_scope": [],
            "input_refs": {},
            "gates": [_gate("V0_OUTPUT_BOUND", "UNKNOWN", "output_too_large")],
            "unknowns": [{"reason_code": "output_too_large", "evidence_ref": ""}],
            "forbidden_actions_observed": [],
            "next_action": "owner_review",
        }
    return result


def _stop(case_id, reported_claim, gate_id, verdict, reason_code, refs=None, scope=()):
    status = "FAIL" if verdict == DISPROVEN else "UNKNOWN"
    unknowns = [] if verdict == DISPROVEN else [{"reason_code": reason_code, "evidence_ref": ""}]
    return _output(case_id, verdict, reason_code, reported_claim, [_gate(gate_id, status, reason_code)], unknowns, scope, refs)


def _manifest(root, raw, name):
    if not isinstance(raw, list) or not raw or len(raw) > MAX_MANIFEST_RECORDS:
        raise VerificationInputError("invalid_%s_manifest" % name)
    records = []
    seen = set()
    for record in raw:
        if not isinstance(record, dict):
            raise VerificationInputError("invalid_%s_manifest_record" % name)
        rel = _normalise_rel(record.get("path"))
        if rel in seen:
            raise VerificationInputError("duplicate_%s_manifest_path" % name)
        seen.add(rel)
        digest = record.get("sha256")
        if not _valid_sha256(digest, nullable=(name == "baseline")):
            raise VerificationInputError("invalid_%s_manifest_sha256" % name)
        normal = {"path": rel, "sha256": digest}
        if name == "candidate":
            normal["max_bytes"] = _max_bytes(record)
        records.append(normal)
    return records


def _nested_field(value, field):
    if not isinstance(field, str) or not field:
        raise VerificationInputError("invalid_json_field")
    here = value
    for part in field.split("."):
        if not isinstance(here, dict) or part not in here:
            return False, None
        here = here[part]
    return True, here


def _content_gate(spec, artifacts):
    if not isinstance(spec, dict):
        raise VerificationInputError("invalid_content_gate")
    gate_id = _identifier(spec.get("gate_id"), "gate_id")
    artifact_id = _identifier(spec.get("artifact_id"), "artifact_id")
    kind = spec.get("type")
    params = spec.get("params")
    if artifact_id not in artifacts or kind not in ALLOWED_GATE_TYPES or not isinstance(params, dict):
        raise VerificationInputError("invalid_content_gate")
    evidence = artifacts[artifact_id]
    if kind.startswith("json_"):
        if evidence["content_type"] != "json":
            return _gate(gate_id, "FAIL", "content_type_mismatch", (artifact_id,))
        body = _parse_json(evidence)
        exists, actual = _nested_field(body, params.get("field"))
        if kind == "json_field_present":
            return _gate(gate_id, "PASS" if exists else "FAIL", "ok" if exists else "field_absent", (artifact_id,))
        if "equals" not in params:
            raise VerificationInputError("missing_gate_equals")
        ok = exists and actual == params["equals"]
        return _gate(gate_id, "PASS" if ok else "FAIL", "ok" if ok else "field_not_equal", (artifact_id,))
    if kind in ("text_contains", "text_contains_ci", "path_or_text_contains_ci", "text_absent"):
        needle = params.get("text")
        if not isinstance(needle, str) or not needle:
            raise VerificationInputError("invalid_gate_text")
        # Case is not evidence.  A named phrase that opens a heading is the same
        # phrase; a gate that reads it as absent reports a fact about letter case
        # and calls it a fact about the product.
        if kind == "path_or_text_contains_ci":
            present = address_hit(evidence["path"], evidence["text"], needle)
        elif kind == "text_contains_ci":
            present = needle.casefold() in evidence["text"].casefold()
        else:
            present = needle in evidence["text"]
        if kind == "text_absent" and evidence.get("redactions"):
            # ABSENCE CANNOT BE PROVEN ON TEXT WE BLANKED OURSELVES.  Redaction
            # removes exactly the shapes an absence gate is usually pointed at,
            # so reading "not found" here would report our own erasure as the
            # product's cleanliness.  Third outcome, not a pass and not a fail.
            return _gate(gate_id, "UNKNOWN", "redacted_evidence_absence_unprovable", (artifact_id,))
        ok = present if kind != "text_absent" else not present
        return _gate(gate_id, "PASS" if ok else "FAIL", "ok" if ok else "text_condition_failed", (artifact_id,))
    if evidence["content_type"] != "python":
        return _gate(gate_id, "FAIL", "content_type_mismatch", (artifact_id,))
    allowed = params.get("allowed")
    if not isinstance(allowed, list) or any(not isinstance(item, str) or not item for item in allowed):
        raise VerificationInputError("invalid_import_allowlist")
    try:
        tree = ast.parse(evidence["text"])
    except SyntaxError:
        return _gate(gate_id, "FAIL", "invalid_python", (artifact_id,))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    disallowed = sorted(imported.difference(allowed))
    return _gate(gate_id, "PASS" if not disallowed else "FAIL", "ok" if not disallowed else "forbidden_import", (artifact_id,))


def _task_gate_specs(task):
    """The task, not the executor bundle, names the checks that prove it."""
    body = _parse_json(task)
    if body.get("schema_version") != "v0.1":
        raise VerificationInputError("invalid_task_packet_schema")
    gates = body.get("required_content_gates")
    if not isinstance(gates, list) or not gates or len(gates) > MAX_CONTENT_GATES:
        raise VerificationInputError("invalid_task_content_gates")
    # A canonical deep copy rejects non-JSON values and makes the later equality
    # comparison independent of dict formatting or key order.
    try:
        return json.loads(_canonical(gates))
    except (TypeError, ValueError) as exc:
        raise VerificationInputError("invalid_task_content_gates") from exc


def _valid_run_id(value):
    return isinstance(value, str) and bool(_RUN_ID_RE.fullmatch(value))


def _task_run_id(task):
    """The task, not the executor bundle, names which run is being proven."""
    value = _parse_json(task).get(RUN_ID_FIELD)
    if not _valid_run_id(value):
        raise VerificationInputError("task_run_id_missing")
    return value


def _task_run_binding(task):
    """The task, not the executor bundle, names HOW the run is bound to evidence."""
    value = _parse_json(task).get(RUN_BINDING_FIELD, RUN_BINDING_DECLARED)
    if value not in RUN_BINDINGS:
        raise VerificationInputError("invalid_run_binding")
    return value


def _declared_run_ids(evidence):
    """Run ids the artifact itself carries.  File times are never consulted."""
    if evidence["content_type"] == "json":
        try:
            body = _parse_json(evidence)
        except VerificationInputError:
            return []
        exists, actual = _nested_field(body, RUN_ID_FIELD)
        return [actual] if exists and _valid_run_id(actual) else []
    return _RUN_DECLARATION_RE.findall(evidence["text"])


def _stdout_evidence(root, spec):
    """Read and parse the saved test receipt; bundle claims are not evidence."""
    if not isinstance(spec, dict):
        raise VerificationInputError("invalid_test_evidence")
    test_id = _identifier(spec.get("test_id"), "test_id")
    _identifier(spec.get("declared_command_id"), "declared_command_id")
    stdout = _read_declared(
        root,
        {
            "path": spec.get("stdout_path"),
            "sha256": spec.get("stdout_sha256"),
            "max_bytes": spec.get("max_bytes", DEFAULT_MAX_BYTES),
            "content_type": "json",
        },
        expected_types=("json",),
    )
    receipt = _parse_json(stdout)
    summary = spec.get("expected_summary")
    if not isinstance(summary, dict) or any(
        not isinstance(summary.get(key), int) or isinstance(summary.get(key), bool) or summary[key] < 0
        for key in ("passed", "failed", "errors")
    ):
        raise VerificationInputError("invalid_test_summary")
    if receipt.get("test_id") != test_id or receipt.get("exit_code") != spec.get("exit_code"):
        raise UnsafeEvidenceError("test_receipt_mismatch")
    actual_summary = {key: receipt.get(key) for key in ("passed", "failed", "errors")}
    if actual_summary != summary:
        raise UnsafeEvidenceError("test_summary_mismatch")
    if receipt["exit_code"] != 0 or actual_summary["failed"] or actual_summary["errors"]:
        raise UnsafeEvidenceError("test_evidence_failed")
    return stdout


def verify_case(bundle):
    """Verify one explicitly declared local evidence bundle.

    Invalid or incomplete evidence fails closed to ``UNKNOWN``; direct boundary
    and evidence contradictions return ``DISPROVEN``.  No exception is exposed
    to callers for normal verifier outcomes.
    """
    case_id = bundle.get("case_id") if isinstance(bundle, dict) else ""
    reported_claim = "unknown"
    refs = {}
    try:
        if not isinstance(bundle, dict) or bundle.get("schema_version") != "v0.1":
            raise VerificationInputError("unsupported_schema")
        if not isinstance(case_id, str) or not case_id or len(case_id) > MAX_CASE_ID_CHARS:
            raise VerificationInputError("invalid_case_id")
        root = bundle.get("workspace_root")
        task = _read_declared(root, bundle.get("task_packet"), expected_types=("json",))
        task_gates = _task_gate_specs(task)
        result = _read_declared(root, bundle.get("result_packet"), expected_types=("json",))
        result_body = _parse_json(result)
        reported_claim = result_body.get("reported_claim", "unknown")
        if reported_claim not in ("reported_done", "reported_failed", "unknown"):
            raise VerificationInputError("invalid_reported_claim")
        baseline = _manifest(root, bundle.get("baseline_manifest"), "baseline")
        candidate = _manifest(root, bundle.get("candidate_manifest"), "candidate")
        refs = {
            "task_packet_sha256": task["sha256"],
            "result_packet_sha256": result["sha256"],
            "candidate_manifest_sha256": _sha256_text(_canonical(candidate)),
        }
    except UnsafeEvidenceError as exc:
        verdict = _unsafe_verdict(str(exc))
        return _stop(case_id, reported_claim, "V0_PATH_BOUNDARY", verdict, str(exc), refs)
    except FileNotFoundError as exc:
        return _stop(case_id, reported_claim, "V0_SCHEMA", UNKNOWN, "missing_declared_file", refs)
    except VerificationInputError as exc:
        return _stop(case_id, reported_claim, "V0_SCHEMA", UNKNOWN, str(exc), refs)

    gates = [_gate("V0_SCHEMA", "PASS", "ok", (task["path"], result["path"]))]
    allowed = bundle.get("allowed_changed_paths")
    try:
        if not isinstance(allowed, list):
            raise VerificationInputError("invalid_allowed_changed_paths")
        allowed_paths = [_normalise_rel(path) for path in allowed]
        if len(set(allowed_paths)) != len(allowed_paths):
            raise VerificationInputError("duplicate_allowed_changed_path")
        base_by_path = {item["path"]: item["sha256"] for item in baseline}
        candidate_by_path = {item["path"]: item["sha256"] for item in candidate}
        changed = sorted(path for path in set(base_by_path) | set(candidate_by_path) if base_by_path.get(path) != candidate_by_path.get(path))
        if changed != sorted(allowed_paths):
            return _output(case_id, DISPROVEN, "changed_scope_mismatch", reported_claim, gates + [_gate("V0_SCOPE_EXACT", "FAIL", "changed_scope_mismatch")], (), (), refs)
        gates.append(_gate("V0_SCOPE_EXACT", "PASS", "ok", changed))
        candidates = {}
        for item in candidate:
            evidence = _read_declared(root, {**item, "content_type": "text"}, expected_types=("text",))
            candidates[item["path"]] = evidence
        gates.append(_gate("V0_HASH_INTEGRITY", "PASS", "ok", tuple(sorted(candidates))))
    except UnsafeEvidenceError as exc:
        verdict = _unsafe_verdict(str(exc))
        status = "UNKNOWN" if verdict == UNKNOWN else "FAIL"
        unknowns = [{"reason_code": str(exc), "evidence_ref": ""}] if verdict == UNKNOWN else ()
        return _output(case_id, verdict, str(exc), reported_claim, gates + [_gate("V0_HASH_INTEGRITY", status, str(exc))], unknowns, (), refs)
    except FileNotFoundError:
        return _output(case_id, UNKNOWN, "missing_candidate_file", reported_claim, gates + [_gate("V0_HASH_INTEGRITY", "UNKNOWN", "missing_candidate_file")], [{"reason_code": "missing_candidate_file", "evidence_ref": ""}], (), refs)
    except VerificationInputError as exc:
        return _output(case_id, UNKNOWN, str(exc), reported_claim, gates + [_gate("V0_SCOPE_EXACT", "UNKNOWN", str(exc))], [{"reason_code": str(exc), "evidence_ref": ""}], (), refs)

    artifacts = {}
    try:
        required = bundle.get("required_artifacts")
        if not isinstance(required, list) or len(required) > MAX_ARTIFACTS:
            raise VerificationInputError("invalid_required_artifacts")
        for record in required:
            if not isinstance(record, dict):
                raise VerificationInputError("invalid_artifact_id")
            artifact_id = _identifier(record.get("artifact_id"), "artifact_id")
            if artifact_id in artifacts:
                raise VerificationInputError("duplicate_artifact_id")
            if record.get("required") is not True:
                raise VerificationInputError("artifact_not_required")
            artifacts[artifact_id] = _read_declared(root, record, expected_types=ALLOWED_CONTENT_TYPES)
        content_gates = bundle.get("content_gates")
        if not isinstance(content_gates, list) or len(content_gates) > MAX_CONTENT_GATES:
            raise VerificationInputError("invalid_content_gates")
        if _canonical(content_gates) != _canonical(task_gates):
            return _output(case_id, DISPROVEN, "task_gate_binding_mismatch", reported_claim, gates + [_gate("V0_TASK_BINDING", "FAIL", "task_gate_binding_mismatch", (task["path"],))], (), (), refs)
        gates.append(_gate("V0_TASK_BINDING", "PASS", "task_declares_content_gates", (task["path"],)))
        # REDACTION IS SAID OUT LOUD, with a count and the artifacts it touched.
        # A verdict standing on text this verifier quietly altered would be a
        # verdict about a file nobody has.  The count is a number, never a value.
        redacted = sorted(name for name, one in artifacts.items() if one.get("redactions"))
        if redacted:
            gates.append(_gate("V0_SENSITIVE_REDACTED", "PASS",
                               "redacted_%d_spans" % sum(artifacts[name]["redactions"] for name in redacted),
                               tuple(redacted)))
        content_results = [_content_gate(spec, artifacts) for spec in content_gates]
        failed = next((gate for gate in content_results if gate["status"] == "FAIL"), None)
        if failed:
            return _output(case_id, DISPROVEN, failed["reason_code"], reported_claim, gates + [_gate("V0_ARTIFACT_READBACK", "FAIL", failed["reason_code"], failed["evidence_refs"])] + content_results, (), (), refs)
        unsure = next((gate for gate in content_results if gate["status"] == "UNKNOWN"), None)
        if unsure:
            # A content gate that could not decide is UNKNOWN, never a silent
            # PASS: the loop above only looked for FAIL, so without this branch
            # an undecided gate would have been read as satisfied.
            return _output(case_id, UNKNOWN, unsure["reason_code"], reported_claim, gates + [_gate("V0_ARTIFACT_READBACK", "UNKNOWN", unsure["reason_code"], unsure["evidence_refs"])] + content_results, [{"reason_code": unsure["reason_code"], "evidence_ref": ref} for ref in (unsure["evidence_refs"] or [""])], (), refs)
        gates.extend([_gate("V0_ARTIFACT_READBACK", "PASS", "ok", tuple(sorted(artifacts)))] + content_results)
    except UnsafeEvidenceError as exc:
        return _output(case_id, UNKNOWN if str(exc) == "sensitive_content" else DISPROVEN, str(exc), reported_claim, gates + [_gate("V0_ARTIFACT_READBACK", "UNKNOWN" if str(exc) == "sensitive_content" else "FAIL", str(exc))], [{"reason_code": str(exc), "evidence_ref": ""}] if str(exc) == "sensitive_content" else (), (), refs)
    except FileNotFoundError:
        return _output(case_id, UNKNOWN, "missing_required_artifact", reported_claim, gates + [_gate("V0_ARTIFACT_READBACK", "UNKNOWN", "missing_required_artifact")], [{"reason_code": "missing_required_artifact", "evidence_ref": ""}], (), refs)
    except VerificationInputError as exc:
        return _output(case_id, UNKNOWN, str(exc), reported_claim, gates + [_gate("V0_ARTIFACT_READBACK", "UNKNOWN", str(exc))], [{"reason_code": str(exc), "evidence_ref": ""}], (), refs)

    try:
        evidence_specs = bundle.get("test_evidence")
        if not isinstance(evidence_specs, list) or len(evidence_specs) > MAX_TEST_EVIDENCE:
            raise VerificationInputError("invalid_test_evidence")
        for spec in evidence_specs:
            _stdout_evidence(root, spec)
        gates.append(_gate("V0_TEST_EVIDENCE", "PASS", "ok"))
    except UnsafeEvidenceError as exc:
        return _output(case_id, DISPROVEN, str(exc), reported_claim, gates + [_gate("V0_TEST_EVIDENCE", "FAIL", str(exc))], (), (), refs)
    except FileNotFoundError:
        return _output(case_id, UNKNOWN, "missing_test_stdout", reported_claim, gates + [_gate("V0_TEST_EVIDENCE", "UNKNOWN", "missing_test_stdout")], [{"reason_code": "missing_test_stdout", "evidence_ref": ""}], (), refs)
    except VerificationInputError as exc:
        return _output(case_id, UNKNOWN, str(exc), reported_claim, gates + [_gate("V0_TEST_EVIDENCE", "UNKNOWN", str(exc))], [{"reason_code": str(exc), "evidence_ref": ""}], (), refs)

    # Freshness: every required artifact must name THIS run.  A product left by
    # an earlier run passes every gate above — it is whole, parsable and says
    # "green" — so without this the verdict answers "is the address occupied?"
    # instead of "did this run produce it?".
    try:
        run_id = _task_run_id(task)
        binding = _task_run_binding(task)
        declared = bundle.get("run_id")
        if not _valid_run_id(declared):
            raise VerificationInputError("bundle_run_id_missing")
        if declared != run_id:
            return _output(case_id, DISPROVEN, "run_binding_mismatch", reported_claim, gates + [_gate("V0_RUN_BINDING", "FAIL", "run_binding_mismatch", (task["path"],))], (), (), refs)
        # ``changed`` is this verifier's OWN arithmetic over the two manifests, and
        # ``V0_SCOPE_EXACT`` above already proved it equals the declared scope.  In
        # measured mode a path inside it is bound to the run without the artifact
        # having to say so; outside it, the declared rule applies unchanged.
        measured = set(changed) if binding == RUN_BINDING_MEASURED else set()
        stale, unbound = [], []
        for artifact_id in sorted(artifacts):
            if artifacts[artifact_id]["path"] in measured:
                continue
            carried = _declared_run_ids(artifacts[artifact_id])
            if run_id in carried:
                continue
            # Naming another run is a contradiction; naming none is ignorance.
            (stale if carried else unbound).append(artifact_id)
        if stale:
            return _output(case_id, DISPROVEN, "stale_run_evidence", reported_claim, gates + [_gate("V0_RUN_BINDING", "FAIL", "stale_run_evidence", stale)], (), (), refs)
        if unbound or not artifacts:
            reason = "unbound_run_evidence" if unbound else "no_run_bound_evidence"
            return _output(case_id, UNKNOWN, reason, reported_claim, gates + [_gate("V0_RUN_BINDING", "UNKNOWN", reason, unbound)], [{"reason_code": reason, "evidence_ref": ref} for ref in (unbound or [""])], (), refs)
        reason = "evidence_names_this_run" if binding == RUN_BINDING_DECLARED else "measured_change_names_this_run"
        gates.append(_gate("V0_RUN_BINDING", "PASS", reason, tuple(sorted(artifacts))))
        refs["run_id"] = run_id
        # Written only in measured mode: a declared-mode report keeps its bytes.
        if binding != RUN_BINDING_DECLARED:
            refs[RUN_BINDING_FIELD] = binding
    except VerificationInputError as exc:
        return _output(case_id, UNKNOWN, str(exc), reported_claim, gates + [_gate("V0_RUN_BINDING", "UNKNOWN", str(exc))], [{"reason_code": str(exc), "evidence_ref": ""}], (), refs)

    stale_unknowns = result_body.get("unknowns", [])
    if reported_claim == "reported_done" and stale_unknowns:
        return _output(case_id, UNKNOWN, "stale_unknowns", reported_claim, gates + [_gate("V0_STATUS_COHERENCE", "UNKNOWN", "stale_unknowns")], [{"reason_code": "stale_unknowns", "evidence_ref": "result_packet"}], (), refs)
    gates.append(_gate("V0_STATUS_COHERENCE", "PASS", "ok"))
    gates.append(_gate("V0_DETERMINISM", "PASS", "canonical_json"))
    return _output(case_id, PROVEN, "all_gates_passed", reported_claim, gates, (), changed, refs)


def canonical_result_bytes(result):
    """Canonical V0 artifact bytes, provided for deterministic wrappers/tests."""
    if not isinstance(result, dict):
        raise VerificationInputError("invalid_result")
    data = _canonical(result).encode("utf-8")
    if len(data) > MAX_OUTPUT_BYTES:
        raise VerificationInputError("output_too_large")
    return data
