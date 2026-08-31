"""Fixture-only V0 shadow content-product verifier.

This module intentionally has no CLI and no integration point.  It consumes one
explicit, bounded evidence bundle and returns a deterministic three-state
verdict.  It never executes candidate code or tests, and never discovers files
outside the manifest supplied by the caller.
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
ALLOWED_CONTENT_TYPES = ("json", "text", "python")
ALLOWED_GATE_TYPES = (
    "json_field_equals",
    "json_field_present",
    "text_contains",
    "text_absent",
    "python_import_allowlist",
)
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
    full = os.path.abspath(os.path.join(root_abs, rel.replace("/", os.sep)))
    try:
        inside = os.path.commonpath((root_abs, full)) == root_abs
    except ValueError:
        inside = False
    if not inside:
        raise UnsafeEvidenceError("outside_workspace_root")
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


def _gate(gate_id, status, reason_code, evidence_refs=()):
    return {
        "gate_id": gate_id,
        "status": status,
        "reason_code": reason_code,
        "evidence_refs": list(evidence_refs),
    }


def _unsafe_verdict(reason):
    """Unreadable or secret-like evidence is uncertainty, not a contradiction."""
    return UNKNOWN if reason in ("binary_or_non_utf8", "sensitive_content") else DISPROVEN


def _output(case_id, verdict, reason_code, reported_claim, gates, unknowns=(), verified_scope=(), refs=None):
    result = {
        "schema_version": SCHEMA,
        "case_id": case_id if isinstance(case_id, str) else "",
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
        # This has no caller-controlled excerpt, so the fallback remains bounded.
        result["verdict"] = UNKNOWN
        result["reason_code"] = "output_too_large"
        result["gates"] = [_gate("V0_OUTPUT_BOUND", "UNKNOWN", "output_too_large")]
        result["unknowns"] = [{"reason_code": "output_too_large", "evidence_ref": ""}]
        result["verified_scope"] = []
        result["next_action"] = "owner_review"
    return result


def _stop(case_id, reported_claim, gate_id, verdict, reason_code, refs=None, scope=()):
    status = "FAIL" if verdict == DISPROVEN else "UNKNOWN"
    unknowns = [] if verdict == DISPROVEN else [{"reason_code": reason_code, "evidence_ref": ""}]
    return _output(case_id, verdict, reason_code, reported_claim, [_gate(gate_id, status, reason_code)], unknowns, scope, refs)


def _manifest(root, raw, name):
    if not isinstance(raw, list) or not raw:
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
    gate_id = spec.get("gate_id")
    artifact_id = spec.get("artifact_id")
    kind = spec.get("type")
    params = spec.get("params")
    if not isinstance(gate_id, str) or not gate_id or artifact_id not in artifacts or kind not in ALLOWED_GATE_TYPES or not isinstance(params, dict):
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
    if kind in ("text_contains", "text_absent"):
        needle = params.get("text")
        if not isinstance(needle, str) or not needle:
            raise VerificationInputError("invalid_gate_text")
        present = needle in evidence["text"]
        ok = present if kind == "text_contains" else not present
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
        if not isinstance(case_id, str) or not case_id:
            raise VerificationInputError("invalid_case_id")
        root = bundle.get("workspace_root")
        task = _read_declared(root, bundle.get("task_packet"), expected_types=("text", "json"))
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
        if not isinstance(required, list):
            raise VerificationInputError("invalid_required_artifacts")
        for record in required:
            if not isinstance(record, dict) or not isinstance(record.get("artifact_id"), str) or not record["artifact_id"]:
                raise VerificationInputError("invalid_artifact_id")
            if record["artifact_id"] in artifacts:
                raise VerificationInputError("duplicate_artifact_id")
            if record.get("required") is not True:
                raise VerificationInputError("artifact_not_required")
            artifacts[record["artifact_id"]] = _read_declared(root, record, expected_types=ALLOWED_CONTENT_TYPES)
        content_gates = bundle.get("content_gates")
        if not isinstance(content_gates, list):
            raise VerificationInputError("invalid_content_gates")
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
        if not isinstance(evidence_specs, list):
            raise VerificationInputError("invalid_test_evidence")
        for spec in evidence_specs:
            if not isinstance(spec, dict) or not isinstance(spec.get("test_id"), str) or not isinstance(spec.get("declared_command_id"), str):
                raise VerificationInputError("invalid_test_evidence")
            stdout = _read_declared(root, {"path": spec.get("stdout_path"), "sha256": spec.get("stdout_sha256"), "max_bytes": spec.get("max_bytes", DEFAULT_MAX_BYTES), "content_type": "text"}, expected_types=("text",))
            summary = spec.get("expected_summary")
            if not isinstance(summary, dict) or any(not isinstance(summary.get(key), int) or isinstance(summary.get(key), bool) or summary[key] < 0 for key in ("passed", "failed", "errors")):
                raise VerificationInputError("invalid_test_summary")
            if spec.get("exit_code") != 0 or summary["failed"] or summary["errors"]:
                return _output(case_id, DISPROVEN, "test_evidence_failed", reported_claim, gates + [_gate("V0_TEST_EVIDENCE", "FAIL", "test_evidence_failed", (stdout["path"],))], (), (), refs)
        gates.append(_gate("V0_TEST_EVIDENCE", "PASS", "ok"))
    except UnsafeEvidenceError as exc:
        return _output(case_id, DISPROVEN, str(exc), reported_claim, gates + [_gate("V0_TEST_EVIDENCE", "FAIL", str(exc))], (), (), refs)
    except FileNotFoundError:
        return _output(case_id, UNKNOWN, "missing_test_stdout", reported_claim, gates + [_gate("V0_TEST_EVIDENCE", "UNKNOWN", "missing_test_stdout")], [{"reason_code": "missing_test_stdout", "evidence_ref": ""}], (), refs)
    except VerificationInputError as exc:
        return _output(case_id, UNKNOWN, str(exc), reported_claim, gates + [_gate("V0_TEST_EVIDENCE", "UNKNOWN", str(exc))], [{"reason_code": str(exc), "evidence_ref": ""}], (), refs)

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
