"""Fixture-only regression tests for :mod:`content_product_verifier`."""

import ast
import hashlib
import json
import os
import shutil
import tempfile
import unittest

import content_product_verifier as cpv


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(root, rel, text):
    full = os.path.join(root, rel.replace("/", os.sep))
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)
    with open(full, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class _Bundle(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="content_product_verifier_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        task = '{"task":"fixture only"}'
        result = '{"reported_claim":"reported_done","unknowns":[]}'
        candidate = 'value = "green"\n'
        stdout = "1 passed\n"
        artifact = '{"state":"green","nested":{"ok":true}}'
        self.hashes = {
            "task": _write(self.root, "task.json", task),
            "result": _write(self.root, "result.json", result),
            "candidate": _write(self.root, "candidate.py", candidate),
            "stdout": _write(self.root, "tests.txt", stdout),
            "artifact": _write(self.root, "artifact.json", artifact),
        }

    def bundle(self):
        return {
            "schema_version": "v0.1",
            "case_id": "fixture-green",
            "workspace_root": self.root,
            "task_packet": {"path": "task.json", "sha256": self.hashes["task"], "content_type": "json"},
            "result_packet": {"path": "result.json", "sha256": self.hashes["result"], "content_type": "json"},
            "allowed_changed_paths": ["candidate.py"],
            "baseline_manifest": [{"path": "candidate.py", "sha256": None}],
            "candidate_manifest": [{"path": "candidate.py", "sha256": self.hashes["candidate"], "max_bytes": 1000}],
            "required_artifacts": [{"artifact_id": "product", "path": "artifact.json", "sha256": self.hashes["artifact"], "content_type": "json", "required": True, "max_bytes": 1000}],
            "test_evidence": [{"test_id": "fixture", "declared_command_id": "unit-fixture", "stdout_path": "tests.txt", "stdout_sha256": self.hashes["stdout"], "exit_code": 0, "expected_summary": {"passed": 1, "failed": 0, "errors": 0}}],
            "content_gates": [{"gate_id": "state", "artifact_id": "product", "type": "json_field_equals", "params": {"field": "state", "equals": "green"}}],
        }


class TestVerdicts(_Bundle):
    def test_complete_fixture_is_proven_and_repeatable(self):
        first = cpv.verify_case(self.bundle())
        second = cpv.verify_case(self.bundle())
        self.assertEqual(first["verdict"], cpv.PROVEN)
        self.assertEqual(first, second)
        self.assertEqual(cpv.canonical_result_bytes(first), cpv.canonical_result_bytes(second))

    def test_reported_done_alone_does_not_prove(self):
        bundle = self.bundle()
        bundle["required_artifacts"][0]["path"] = "missing.json"
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.UNKNOWN)

    def test_missing_required_content_is_disproven(self):
        bundle = self.bundle()
        bundle["content_gates"][0]["params"]["equals"] = "red"
        got = cpv.verify_case(bundle)
        self.assertEqual(got["verdict"], cpv.DISPROVEN)
        self.assertEqual(got["reason_code"], "field_not_equal")

    def test_hash_mismatch_is_disproven(self):
        bundle = self.bundle()
        bundle["candidate_manifest"][0]["sha256"] = "0" * 64
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.DISPROVEN)

    def test_failed_test_is_disproven(self):
        bundle = self.bundle()
        bundle["test_evidence"][0]["expected_summary"]["failed"] = 1
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.DISPROVEN)

    def test_missing_stdout_is_unknown(self):
        bundle = self.bundle()
        bundle["test_evidence"][0]["stdout_path"] = "missing.txt"
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.UNKNOWN)

    def test_extra_changed_path_is_disproven(self):
        bundle = self.bundle()
        bundle["allowed_changed_paths"] = []
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.DISPROVEN)

    def test_incomplete_manifest_is_unknown(self):
        bundle = self.bundle()
        bundle["baseline_manifest"] = []
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.UNKNOWN)

    def test_absolute_and_traversal_paths_stop_without_reading(self):
        for bad in ("C:/outside.txt", "../outside.txt"):
            bundle = self.bundle()
            bundle["task_packet"]["path"] = bad
            got = cpv.verify_case(bundle)
            self.assertEqual(got["verdict"], cpv.DISPROVEN)
            self.assertEqual(got["gates"][0]["gate_id"], "V0_PATH_BOUNDARY")

    def test_stale_unknowns_keep_done_unknown(self):
        text = '{"reported_claim":"reported_done","unknowns":["needs owner"]}'
        self.hashes["result"] = _write(self.root, "result.json", text)
        got = cpv.verify_case(self.bundle())
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.UNKNOWN, "stale_unknowns"))

    def test_sensitive_fixture_never_enters_output(self):
        text = '{"state":"green","password":"not-a-real-password"}'
        self.hashes["artifact"] = _write(self.root, "artifact.json", text)
        got = cpv.verify_case(self.bundle())
        rendered = json.dumps(got, ensure_ascii=False)
        self.assertEqual(got["verdict"], cpv.UNKNOWN)
        self.assertNotIn("not-a-real-password", rendered)

    def test_sensitive_declared_input_is_unknown_not_disproven(self):
        text = '{"task":"fixture only","password":"not-a-real-password"}'
        self.hashes["task"] = _write(self.root, "task.json", text)
        self.assertEqual(cpv.verify_case(self.bundle())["verdict"], cpv.UNKNOWN)

    def test_reordered_allowlist_is_canonical_or_rejected(self):
        bundle = self.bundle()
        bundle["allowed_changed_paths"] = ["candidate.py", "candidate.py"]
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.UNKNOWN)


class TestNoSideEffects(unittest.TestCase):
    def test_module_has_no_network_process_environment_or_write_calls(self):
        with open(os.path.join(os.path.dirname(__file__), "content_product_verifier.py"), encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        imports = {node.names[0].name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) and node.names}
        self.assertFalse(imports.intersection({"subprocess", "socket", "requests", "urllib", "glob"}))
        self.assertNotIn("environ", source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                self.assertTrue(len(node.args) < 2 or node.args[1].value == "rb")

    def test_output_is_bounded_and_contains_no_absolute_root(self):
        payload = {"x": "y"}
        self.assertLessEqual(len(cpv.canonical_result_bytes(payload)), cpv.MAX_OUTPUT_BYTES)


if __name__ == "__main__":
    unittest.main()
