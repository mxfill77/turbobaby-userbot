"""Fixture-only regression tests for :mod:`content_product_verifier`."""

import ast
import hashlib
import json
import os
import shutil
import tempfile
import unittest

import content_product_verifier as cpv


RUN_ID = "fixture-run-2"
PRIOR_RUN_ID = "fixture-run-1"
PREFIX_RUN_ID = "fixture-run-20"  # shares a prefix with RUN_ID and must not bind


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
        task = ('{"schema_version":"v0.1","run_id":"%s","required_content_gates":['
                '{"gate_id":"state","artifact_id":"product","type":"json_field_equals",'
                '"params":{"field":"state","equals":"green"}}]}' % RUN_ID)
        result = '{"reported_claim":"reported_done","unknowns":[]}'
        candidate = 'value = "green"\n'
        stdout = '{"test_id":"fixture","exit_code":0,"passed":1,"failed":0,"errors":0}'
        artifact = '{"state":"green","run_id":"%s","nested":{"ok":true}}' % RUN_ID
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
            "run_id": RUN_ID,
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
        task = {"schema_version": "v0.1", "run_id": RUN_ID, "required_content_gates": bundle["content_gates"]}
        self.hashes["task"] = _write(self.root, "task.json", json.dumps(task, separators=(",", ":")))
        bundle["task_packet"]["sha256"] = self.hashes["task"]
        got = cpv.verify_case(bundle)
        self.assertEqual(got["verdict"], cpv.DISPROVEN)
        self.assertEqual(got["reason_code"], "field_not_equal")

    def test_executor_cannot_replace_task_declared_gates(self):
        bundle = self.bundle()
        bundle["content_gates"] = []
        got = cpv.verify_case(bundle)
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.DISPROVEN, "task_gate_binding_mismatch"))

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

    def test_stdout_receipt_not_bundle_summary_is_test_evidence(self):
        text = '{"test_id":"fixture","exit_code":0,"passed":0,"failed":0,"errors":0}'
        self.hashes["stdout"] = _write(self.root, "tests.txt", text)
        got = cpv.verify_case(self.bundle())
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.DISPROVEN, "test_summary_mismatch"))

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

    def test_parent_symlink_is_disproven_before_reading_leaf(self):
        outside = tempfile.mkdtemp(prefix="content_product_outside_")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        _write(outside, "escape.json", '{"task":"outside"}')
        linked = os.path.join(self.root, "linked")
        try:
            os.symlink(outside, linked, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this Windows account")
        bundle = self.bundle()
        bundle["task_packet"]["path"] = "linked/escape.json"
        bundle["task_packet"]["sha256"] = _sha('{"task":"outside"}')
        self.assertEqual(cpv.verify_case(bundle)["verdict"], cpv.DISPROVEN)


class TestRunBinding(_Bundle):
    """A product left by an earlier run is whole, parsable and green — and proves nothing."""

    def _artifact(self, text):
        self.hashes["artifact"] = _write(self.root, "artifact.json", text)

    def _task(self, **body):
        payload = {"schema_version": "v0.1", "run_id": RUN_ID,
                   "required_content_gates": [{"gate_id": "state", "artifact_id": "product",
                                               "type": "json_field_equals",
                                               "params": {"field": "state", "equals": "green"}}]}
        payload.update(body)
        payload = {key: value for key, value in payload.items() if value is not None}
        self.hashes["task"] = _write(self.root, "task.json", json.dumps(payload, separators=(",", ":")))

    def test_proven_report_names_the_run_it_verified(self):
        got = cpv.verify_case(self.bundle())
        binding = [gate for gate in got["gates"] if gate["gate_id"] == "V0_RUN_BINDING"]
        self.assertEqual(got["verdict"], cpv.PROVEN)
        self.assertEqual([gate["status"] for gate in binding], ["PASS"])
        self.assertEqual(got["input_refs"]["run_id"], RUN_ID)

    def test_previous_run_product_claimed_new_is_not_proven(self):
        self._artifact('{"state":"green","run_id":"%s"}' % PRIOR_RUN_ID)
        got = cpv.verify_case(self.bundle())
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.DISPROVEN, "stale_run_evidence"))

    def test_previous_run_product_with_nothing_changed_is_not_proven(self):
        self._artifact('{"state":"green","run_id":"%s"}' % PRIOR_RUN_ID)
        bundle = self.bundle()
        bundle["allowed_changed_paths"] = []
        bundle["baseline_manifest"] = [{"path": "candidate.py", "sha256": self.hashes["candidate"]}]
        got = cpv.verify_case(bundle)
        self.assertNotEqual(got["verdict"], cpv.PROVEN)

    def test_recent_file_time_is_not_a_binding(self):
        """The corpse is rewritten last, so it is the newest file on disk."""
        stale = '{"state":"green","run_id":"%s"}' % PRIOR_RUN_ID
        self._artifact(stale)
        newest = max(os.path.getmtime(os.path.join(self.root, name))
                     for name in os.listdir(self.root))
        self.assertEqual(os.path.getmtime(os.path.join(self.root, "artifact.json")), newest)
        self.assertEqual(cpv.verify_case(self.bundle())["reason_code"], "stale_run_evidence")

    def test_run_id_prefix_does_not_bind(self):
        self._artifact('{"state":"green","run_id":"%s"}' % PREFIX_RUN_ID)
        got = cpv.verify_case(self.bundle())
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.DISPROVEN, "stale_run_evidence"))

    def test_artifact_naming_no_run_is_unknown(self):
        self._artifact('{"state":"green"}')
        got = cpv.verify_case(self.bundle())
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.UNKNOWN, "unbound_run_evidence"))

    def test_bundle_must_name_the_run_the_task_names(self):
        bundle = self.bundle()
        bundle["run_id"] = PRIOR_RUN_ID
        got = cpv.verify_case(bundle)
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.DISPROVEN, "run_binding_mismatch"))

    def test_bundle_without_run_id_cannot_prove(self):
        bundle = self.bundle()
        del bundle["run_id"]
        got = cpv.verify_case(bundle)
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.UNKNOWN, "bundle_run_id_missing"))

    def test_task_without_run_id_cannot_prove(self):
        self._task(run_id=None)
        got = cpv.verify_case(self.bundle())
        self.assertEqual((got["verdict"], got["reason_code"]), (cpv.UNKNOWN, "task_run_id_missing"))

    def test_text_artifact_binds_by_its_own_declaration(self):
        gates = [{"gate_id": "state", "artifact_id": "product", "type": "text_contains",
                  "params": {"text": "state: green"}}]
        self._task(required_content_gates=gates)
        bundle = self.bundle()
        bundle["content_gates"] = gates
        for text, expected in (("run_id: %s\nstate: green\n" % RUN_ID, cpv.PROVEN),
                               ("run_id: %s\nstate: green\n" % PRIOR_RUN_ID, cpv.DISPROVEN),
                               ("state: green\n", cpv.UNKNOWN),
                               # a neighbouring key that merely ends in run_id
                               ("myrun_id: %s\nstate: green\n" % RUN_ID, cpv.UNKNOWN)):
            self.hashes["artifact"] = _write(self.root, "notes.txt", text)
            bundle["required_artifacts"][0].update(
                {"path": "notes.txt", "content_type": "text", "sha256": self.hashes["artifact"]})
            self.assertEqual(cpv.verify_case(bundle)["verdict"], expected, text)


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

    def test_freshness_is_identity_and_never_a_clock(self):
        """Coincidence in time is not a binding, so no clock may exist to consult."""
        with open(os.path.join(os.path.dirname(__file__), "content_product_verifier.py"), encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        imports = {node.names[0].name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) and node.names}
        self.assertFalse(imports.intersection({"time", "datetime", "calendar"}))
        for forbidden in ("st_mtime", "getmtime", "getctime", "utcnow", "monotonic", "perf_counter"):
            self.assertNotIn(forbidden, source)

    def test_output_is_bounded_and_contains_no_absolute_root(self):
        payload = {"x": "y"}
        self.assertLessEqual(len(cpv.canonical_result_bytes(payload)), cpv.MAX_OUTPUT_BYTES)
        huge = cpv._output("x" * (cpv.MAX_OUTPUT_BYTES * 2), cpv.UNKNOWN, "x", "unknown", [])
        self.assertLessEqual(len(cpv.canonical_result_bytes(huge)), cpv.MAX_OUTPUT_BYTES)
        self.assertEqual(huge["case_id"], "")


if __name__ == "__main__":
    unittest.main()
