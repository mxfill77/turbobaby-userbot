"""Unit tests for hq_context_pack (H1, pure fixture-only).

Every fixture is written into a fresh :class:`tempfile.TemporaryDirectory`; the
tests never touch the real project ``.env``, logs, client chats, Bridge or Brain.
Run with: ``python -m unittest test_hq_context_pack -v``.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest

import hq_context_pack as hcp
from hq_context_pack import ContextPackError, build_context_pack


def _write(root, rel, text):
    full = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    return full


def _src(path, role="evidence", lane="pc", evidence_status="verified", required=True, start=1, end=1):
    return {
        "path": path,
        "role": role,
        "lane": lane,
        "evidence_status": evidence_status,
        "required": required,
        "start_line": start,
        "end_line": end,
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hcp_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def root(self):
        return self.tmp


class ValidManifest(_Base):
    def test_schema_order_body_and_repeatable_sha(self):
        _write(self.root(), "a.txt", "alpha\nbeta\ngamma\n")
        _write(self.root(), "b.txt", "one\ntwo\nthree\n")
        manifest = [
            _src("a.txt", role="objective", lane="pc", start=1, end=2),
            _src("b.txt", role="evidence", lane="hq", start=2, end=3),
        ]
        kwargs = dict(root=self.root(), coverage_plan=None, route=None, max_chars=12000)
        pack = build_context_pack("case-1", "code_green", "Assemble HQ pack", manifest, **kwargs)

        self.assertEqual(pack["schema"], "turbobaby.hq_context_pack/v1")
        self.assertEqual(pack["status"], "ok")
        # Stable source order = manifest order.
        self.assertEqual([s["path"] for s in pack["sources"]], ["a.txt", "b.txt"])
        # Labelled body carries exact excerpts, no summary.
        self.assertIn("--- source: a.txt | role=objective | lane=pc | evidence=verified | lines=1-2 ---", pack["body"])
        self.assertIn("alpha\nbeta\n", pack["body"])
        self.assertIn("two\nthree\n", pack["body"])

        again = build_context_pack("case-1", "code_green", "Assemble HQ pack", manifest, **kwargs)
        self.assertEqual(pack["sha256"], again["sha256"])
        self.assertEqual(pack, again)


class ObjectiveStatusCoverage(_Base):
    def _manifest(self):
        _write(self.root(), "pc.txt", "pc line\n")
        return [_src("pc.txt", lane="pc", evidence_status="reported")]

    def test_empty_objective_rejected(self):
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "   ", self._manifest(), root=self.root())
        self.assertEqual(ctx.exception.reason, "empty_objective")

    def test_oversized_objective_rejected(self):
        big = "x" * (hcp.OBJECTIVE_MAX_CHARS + 1)
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", big, self._manifest(), root=self.root())
        self.assertEqual(ctx.exception.reason, "oversized_objective")

    def test_unknown_task_class_rejected(self):
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "made_up", "obj", self._manifest(), root=self.root())
        self.assertEqual(ctx.exception.reason, "invalid_task_class")

    def test_evidence_status_and_lane_preserved(self):
        _write(self.root(), "v.txt", "vps content\n")
        manifest = [_src("v.txt", lane="vps", evidence_status="reported")]
        pack = build_context_pack("c", "status", "obj", manifest, root=self.root())
        self.assertEqual(pack["sources"][0]["lane"], "vps")
        self.assertEqual(pack["sources"][0]["evidence_status"], "reported")  # not promoted

    def test_coverage_checked_and_not_checked(self):
        _write(self.root(), "pc.txt", "pc line\n")
        manifest = [_src("pc.txt", lane="pc")]
        pack = build_context_pack(
            "c", "code_green", "obj", manifest, root=self.root(), coverage_plan=["pc", "vps"]
        )
        cov = {c["lane"]: c for c in pack["coverage"]}
        self.assertEqual(cov["pc"]["status"], "checked")
        self.assertTrue(cov["pc"]["checked"])
        self.assertEqual(cov["vps"]["status"], "not_checked")
        self.assertFalse(cov["vps"]["checked"])
        self.assertTrue(cov["vps"]["reason"])
        self.assertEqual(cov["vps"]["reason"], "no accepted source in lane; evidence unresolved (unknown)")

    def test_invalid_evidence_status_rejected(self):
        _write(self.root(), "x.txt", "x\n")
        bad = [_src("x.txt", evidence_status="accepted")]
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", bad, root=self.root())
        self.assertEqual(ctx.exception.reason, "invalid_evidence_status")


class RouteSeparation(_Base):
    def setUp(self):
        super().setUp()
        _write(self.root(), "a.txt", "hello\n")
        self.manifest = [_src("a.txt")]

    def test_valid_route_preserved_outside_body(self):
        route = {"target": "vps", "note": "structured only"}
        pack = build_context_pack("c", "code_green", "obj", self.manifest, root=self.root(), route=route)
        self.assertEqual(pack["route"], {"target": "vps", "note": "structured only"})
        self.assertNotIn("target", pack["body"])
        self.assertNotIn("structured only", pack["body"])

    def test_unknown_route_target_rejected(self):
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", self.manifest, root=self.root(), route={"target": "moon"})
        self.assertEqual(ctx.exception.reason, "invalid_route_target")

    def test_free_text_route_rejected(self):
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", self.manifest, root=self.root(), route="send to vps xhigh")
        self.assertEqual(ctx.exception.reason, "invalid_route")


class ManifestEvidence(_Base):
    def setUp(self):
        super().setUp()
        _write(self.root(), "a.txt", "l1\nl2\nl3\n")
        _write(self.root(), "b.txt", "x\ny\nz\n")

    def _pack(self, manifest):
        return build_context_pack("c", "code_green", "obj", manifest, root=self.root())

    def test_identical_manifest_same_hash(self):
        m1 = [_src("a.txt", start=1, end=2), _src("b.txt", start=1, end=1)]
        m2 = [_src("a.txt", start=1, end=2), _src("b.txt", start=1, end=1)]
        self.assertEqual(self._pack(m1)["manifest_sha256"], self._pack(m2)["manifest_sha256"])

    def test_altered_role_changes_hash(self):
        base = self._pack([_src("a.txt", role="evidence")])
        alt = self._pack([_src("a.txt", role="objective")])
        self.assertNotEqual(base["manifest_sha256"], alt["manifest_sha256"])

    def test_altered_required_changes_hash(self):
        base = self._pack([_src("a.txt", required=True)])
        alt = self._pack([_src("a.txt", required=False)])
        self.assertNotEqual(base["manifest_sha256"], alt["manifest_sha256"])

    def test_altered_range_changes_hash(self):
        base = self._pack([_src("a.txt", start=1, end=1)])
        alt = self._pack([_src("a.txt", start=1, end=2)])
        self.assertNotEqual(base["manifest_sha256"], alt["manifest_sha256"])


class ExcerptEvidence(_Base):
    def test_bounds_full_and_excerpt_sha_no_summary(self):
        import hashlib

        content = "first\nsecond\nthird\nfourth\n"
        _write(self.root(), "a.txt", content)
        manifest = [_src("a.txt", start=2, end=3)]
        pack = build_context_pack("c", "code_green", "obj", manifest, root=self.root())
        s = pack["sources"][0]
        self.assertEqual(s["start_line"], 2)
        self.assertEqual(s["end_line"], 3)
        self.assertEqual(s["source_sha256"], hashlib.sha256(content.encode("utf-8")).hexdigest())
        self.assertEqual(s["excerpt_sha256"], hashlib.sha256("second\nthird\n".encode("utf-8")).hexdigest())
        # No summary / rewrite introduced: body excerpt is verbatim.
        self.assertIn("second\nthird\n", pack["body"])
        self.assertNotIn("first", pack["body"])
        self.assertNotIn("fourth", pack["body"])


class TraversalAndAbsolute(_Base):
    def test_traversal_rejected(self):
        _write(self.root(), "a.txt", "x\n")
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", [_src("../a.txt")], root=self.root())
        self.assertEqual(ctx.exception.reason, "path_traversal")

    def test_absolute_rejected(self):
        abs_path = os.path.join(self.root(), "a.txt")
        _write(self.root(), "a.txt", "x\n")
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", [_src(abs_path)], root=self.root())
        self.assertEqual(ctx.exception.reason, "absolute_path")

    def test_outside_root_rejected(self):
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", [_src("sub/../../escape.txt")], root=self.root())
        self.assertIn(ctx.exception.reason, ("path_traversal", "outside_root"))


class Denylist(_Base):
    def test_denied_names_never_enter_body(self):
        for rel, token in [
            (".env", ".env"),
            ("config/secret.txt", "secret"),
            ("token_store.txt", "token"),
            ("bot.session", "session"),
            ("cookies.txt", "cookie"),
            ("credentials.json", "credentials"),
            ("logs/run.txt", "logs"),
        ]:
            with self.subTest(rel=rel):
                _write(self.root(), rel, "SENSITIVE-%s\n" % token)
                pack = build_context_pack("c", "code_green", "obj", [_src(rel)], root=self.root())
                # Denied input recorded in excluded, never read into body/sources.
                self.assertTrue(any(e["reason"] == "denylisted" for e in pack["excluded"]))
                self.assertEqual(pack["sources"], [])
                self.assertNotIn("SENSITIVE", pack["body"])


class MissingRequiredSource(_Base):
    def test_blocked_no_partial_body(self):
        _write(self.root(), "present.txt", "here\n")
        manifest = [
            _src("present.txt", required=True),
            _src("absent.txt", required=True),  # never created
        ]
        pack = build_context_pack("c", "code_green", "obj", manifest, root=self.root())
        self.assertEqual(pack["status"], "blocked")
        self.assertEqual(pack["reason"], "missing_required_source")
        self.assertNotIn("body", pack)
        self.assertNotIn("here", json.dumps(pack))


class OptionalSourceOmission(_Base):
    def test_missing_optional_appears_only_in_omitted(self):
        _write(self.root(), "req.txt", "kept\n")
        manifest = [
            _src("req.txt", required=True, lane="pc"),
            _src("opt.txt", required=False, lane="hq"),  # never created
        ]
        pack = build_context_pack("c", "code_green", "obj", manifest, root=self.root())
        self.assertEqual(pack["status"], "ok")
        self.assertEqual([o["path"] for o in pack["omitted"]], ["opt.txt"])
        self.assertEqual(pack["omitted"][0]["reason"], "missing")
        self.assertEqual([s["path"] for s in pack["sources"]], ["req.txt"])

    def test_required_never_silently_omitted(self):
        _write(self.root(), "req.txt", "kept\n")
        manifest = [_src("gone.txt", required=True)]  # never created
        pack = build_context_pack("c", "code_green", "obj", manifest, root=self.root())
        self.assertEqual(pack["status"], "blocked")


class CrossLaneNegativeVpsNotChecked(_Base):
    """PC checked with no relevant evidence; VPS declared but optional source missing."""

    def test_vps_not_checked_unknown_no_absence_verdict(self):
        _write(self.root(), "pc_notes.txt", "routine pc note, nothing about the incident\n")
        manifest = [
            _src("pc_notes.txt", role="context", lane="pc", evidence_status="verified", required=True),
            _src("vps_trace.txt", role="evidence", lane="vps", evidence_status="unknown", required=False),  # missing
        ]
        pack = build_context_pack(
            "c", "architecture", "Find whether the incident left any trace", manifest,
            root=self.root(), coverage_plan=["pc", "vps"],
        )
        self.assertEqual(pack["status"], "ok")
        cov = {c["lane"]: c for c in pack["coverage"]}
        self.assertEqual(cov["pc"]["status"], "checked")
        self.assertEqual(cov["vps"]["status"], "not_checked")
        # Explicit unknown / blind spot for the vps lane.
        self.assertTrue(any(u["source"] == "vps" and u["check"] == "lane_coverage" for u in pack["unknowns"]))
        blind = [u for u in pack["unknowns"] if u["source"] == "vps"][0]
        self.assertIn("snapshot_ref", blind)
        self.assertTrue(blind["blind_spot"])
        # VPS source omitted (not silently dropped, not in sources).
        self.assertTrue(any(o["path"] == "vps_trace.txt" for o in pack["omitted"]))
        # No absence/absent verdict field anywhere; no synthesised "no traces".
        blob = json.dumps(pack)
        self.assertNotIn("absent", blob)
        self.assertNotIn("absence", blob)
        self.assertNotIn("no trace", blob.lower())
        self.assertNotIn("absent", pack)
        self.assertNotIn("absence", pack)


class CrossLanePositiveVpsEvidence(_Base):
    """No relevant evidence in PC, but a checked VPS excerpt carries the evidence."""

    def test_vps_excerpt_preserved_not_replaced_by_pc(self):
        _write(self.root(), "pc_empty.txt", "(no vps trace observed on pc)\n")
        vps_body = "INCIDENT 51: bridge fallback served june snapshot\n"
        _write(self.root(), "vps_trace.txt", vps_body)
        manifest = [
            _src("pc_empty.txt", role="context", lane="pc", evidence_status="verified", required=True, start=1, end=1),
            _src("vps_trace.txt", role="evidence", lane="vps", evidence_status="verified", required=True, start=1, end=1),
        ]
        pack = build_context_pack(
            "c", "architecture", "Locate the incident evidence across lanes", manifest,
            root=self.root(), coverage_plan=["pc", "vps"],
        )
        self.assertEqual(pack["status"], "ok")
        cov = {c["lane"]: c for c in pack["coverage"]}
        self.assertEqual(cov["vps"]["status"], "checked")
        vps_source = [s for s in pack["sources"] if s["lane"] == "vps"][0]
        self.assertEqual(vps_source["path"], "vps_trace.txt")
        self.assertEqual(vps_source["evidence_status"], "verified")
        import hashlib

        self.assertEqual(vps_source["excerpt_sha256"], hashlib.sha256(vps_body.encode("utf-8")).hexdigest())
        # VPS evidence is present in the body and not replaced by the empty PC source.
        self.assertIn("INCIDENT 51", pack["body"])
        self.assertIn("lane=vps", pack["body"])


class DuplicateAndRoleViolation(_Base):
    def test_duplicate_path_rejected(self):
        _write(self.root(), "a.txt", "x\n")
        manifest = [_src("a.txt"), _src("./a.txt")]
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", manifest, root=self.root())
        self.assertEqual(ctx.exception.reason, "duplicate_path")

    def test_role_violation_rejected(self):
        _write(self.root(), "a.txt", "x\n")
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", [_src("a.txt", role="villain")], root=self.root())
        self.assertEqual(ctx.exception.reason, "invalid_role")

    def test_invalid_lane_rejected(self):
        _write(self.root(), "a.txt", "x\n")
        with self.assertRaises(ContextPackError) as ctx:
            build_context_pack("c", "code_green", "obj", [_src("a.txt", lane="moon")], root=self.root())
        self.assertEqual(ctx.exception.reason, "invalid_lane")


class SizeCap(_Base):
    def test_blocked_context_limit_no_truncation(self):
        big = "".join("line %d padding padding padding\n" % i for i in range(200))
        _write(self.root(), "big.txt", big)
        n = len(big.splitlines())
        manifest = [_src("big.txt", required=True, start=1, end=n)]
        pack = build_context_pack("c", "code_green", "obj", manifest, root=self.root(), max_chars=50)
        self.assertEqual(pack["status"], "blocked")
        self.assertEqual(pack["reason"], "context_limit_exceeded")
        self.assertNotIn("body", pack)

    def test_optional_over_cap_omitted_with_reason(self):
        _write(self.root(), "req.txt", "short\n")
        big = "".join("padding line %d here\n" % i for i in range(200))
        _write(self.root(), "opt.txt", big)
        n = len(big.splitlines())
        manifest = [
            _src("req.txt", required=True, start=1, end=1),
            _src("opt.txt", required=False, start=1, end=n),
        ]
        pack = build_context_pack("c", "code_green", "obj", manifest, root=self.root(), max_chars=120)
        self.assertEqual(pack["status"], "ok")
        self.assertTrue(any(o["path"] == "opt.txt" and o["reason"] == "context_limit" for o in pack["omitted"]))
        # The required excerpt is present verbatim (never truncated).
        self.assertIn("short\n", pack["body"])


class JsonSerialization(_Base):
    def test_json_dumps_allow_nan_false_succeeds(self):
        _write(self.root(), "a.txt", "alpha\nbeta\n")
        pack = build_context_pack(
            "c", "code_green", "obj", [_src("a.txt", start=1, end=2)],
            root=self.root(), coverage_plan=["pc", "vps"], route={"target": "hq"},
        )
        # Must not raise.
        text = json.dumps(pack, allow_nan=False)
        self.assertIsInstance(text, str)


class NoSideEffect(_Base):
    def test_no_network_subprocess_env_mutation_or_write(self):
        _write(self.root(), "a.txt", "alpha\nbeta\n")
        manifest = [_src("a.txt", start=1, end=2)]

        env_before = dict(os.environ)
        listing_before = sorted(os.listdir(self.root()))

        real_socket = socket.socket
        real_popen = subprocess.Popen

        def _no_socket(*a, **k):
            raise AssertionError("network access attempted")

        def _no_popen(*a, **k):
            raise AssertionError("subprocess spawn attempted")

        socket.socket = _no_socket
        subprocess.Popen = _no_popen
        try:
            pack = build_context_pack("c", "code_green", "obj", manifest, root=self.root())
        finally:
            socket.socket = real_socket
            subprocess.Popen = real_popen

        self.assertEqual(pack["status"], "ok")
        self.assertEqual(dict(os.environ), env_before)  # no env mutation
        self.assertEqual(sorted(os.listdir(self.root())), listing_before)  # no file written


if __name__ == "__main__":
    unittest.main()
