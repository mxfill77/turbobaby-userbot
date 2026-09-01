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
_SENSITIVE_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|[\"']?(?:api[_-]?key|secret|password)[\"']?\s*[:=]\s*[\"']?\S+|\bsk-[A-Za-z0-9_-]{16,})",
    re.IGNORECASE,
)


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
    if _SENSITIVE_RE.search(text):
        raise UnsafeEvidenceError("sensitive_content")
    return {"path": rel, "content_type": content_type, "text": text, "sha256": expected_hash}


def _parse_json(evidence):
    try:
        value = json.loads(evidence["text"])
    except (TypeError, ValueError) as exc:
        raise VerificationInputError("invalid_json") from exc
    if not isinstance(value, dict):
        raise VerificationInputError("json_not_object")
    return value


_SEPARATOR_RE = re.compile(r"[-_/.\\]+")


def address_hit(path, text, needle):
    """Does this artifact answer a named address -- by its body or by its NAME?

    A named address ("a file ... with the words X") is answered either way, and a
    check that reads only one side reports a fact about naming style and lets it
    pass for a fact about the product.  Separators in the path are read as spaces,
    because ``x-y-z.md`` is how a file is named ``x y z``.
    """
    folded = needle.casefold()
    return folded in text.casefold() or folded in _SEPARATOR_RE.sub(" ", path).casefold()


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
        content_results = [_content_gate(spec, artifacts) for spec in content_gates]
        failed = next((gate for gate in content_results if gate["status"] == "FAIL"), None)
        if failed:
            return _output(case_id, DISPROVEN, failed["reason_code"], reported_claim, gates + [_gate("V0_ARTIFACT_READBACK", "FAIL", failed["reason_code"], failed["evidence_refs"])] + content_results, (), (), refs)
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
