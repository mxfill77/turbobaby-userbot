"""Юниты сборщика пакетов второго мнения (ступень 1 ревью-контура).

Все фикстуры живут в свежем :class:`tempfile.TemporaryDirectory`; ни боевой
`.env`, ни логи, ни переписка клиентов, ни Bridge, ни мозг здесь не читаются
и наружу ничего не отправляется.

Запуск: ``python -m unittest test_review_pack -v``
"""

import ast
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest

import review_pack
from review_pack import (
    REVIEW_MAX_CHARS,
    TAIL_QUESTIONS,
    ReviewPackError,
    build_review_pack,
    index_line,
    pack_filename,
    render_review_pack,
)


def _write(root, rel, text):
    full = os.path.join(root, rel.replace("/", os.sep))
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(text)
    return full


def _src(path, role="evidence", lane="pc", evidence_status="reported", required=True, start=1, end=1):
    return {
        "path": path,
        "role": role,
        "lane": lane,
        "evidence_status": evidence_status,
        "required": required,
        "start_line": start,
        "end_line": end,
    }


def _case(**over):
    case = {
        "kind": "chain",
        "case_id": "demo-chain",
        "task_class": "code_green",
        "subject_date": "2026-08-31",
        "build_date": "2026-09-01",
        "active_objective": "Собрать пакет второго мнения по закрытой цепочке.",
        "summary": ["цепочка закрыта", "операционное изменение: добавлен модуль"],
        "result_packets": [
            {"task_id": "demo-task", "path": "result.json", "reported_status": "reported_done"}
        ],
        "numbers": [{"name": "тестов", "value": 31, "source": "result.json"}],
        "sources": [_src("result.json", role="evidence", start=1, end=2)],
        "coverage_plan": None,
        "route": None,
    }
    case.update(over)
    return case


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rvp_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _write(self.tmp, "result.json", '{"status": "reported_done"}\n{"tests": 31}\n')

    def root(self):
        return self.tmp

    def build(self, case=None, **kw):
        return build_review_pack(case or _case(), root=self.root(), **kw)


# ───────────────────────────── счастливый путь ─────────────────────────────


class OkPack(_Base):
    def test_schema_sections_and_hashes(self):
        pack = self.build()
        self.assertEqual(pack["schema"], "turbobaby.review_pack/v1")
        self.assertEqual(pack["status"], "ok")
        text = render_review_pack(pack)
        for heading in (
            "## СВОДКА",
            "## RESULT PACKET",
            "## ЧИСЛА И ХЕШИ ИСТОЧНИКОВ",
            "## ИСТОЧНИКИ В ПАКЕТЕ",
            "## ОПУЩЕНО (omitted)",
            "## ВОПРОСЫ РЕВЬЮЕРУ (обязательный хвост)",
        ):
            self.assertIn(heading, text)
        self.assertEqual(len(pack["sources"]), 1)
        self.assertEqual(len(pack["sources"][0]["source_sha256"]), 64)
        self.assertEqual(len(pack["manifest_sha256"]), 64)
        self.assertEqual(len(pack["context_pack_sha256"]), 64)

    def test_render_is_pure_function_of_pack(self):
        pack = self.build()
        text = render_review_pack(pack)
        self.assertEqual(len(text), pack["text_chars"])
        self.assertEqual(render_review_pack(pack), text)

    def test_repeated_build_is_byte_identical(self):
        first = self.build()
        second = self.build()
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["text_sha256"], second["text_sha256"])
        self.assertEqual(render_review_pack(first), render_review_pack(second))

    def test_absolute_root_does_not_leak_into_pack(self):
        other = tempfile.mkdtemp(prefix="rvp_other_")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        _write(other, "result.json", '{"status": "reported_done"}\n{"tests": 31}\n')
        here = self.build()
        there = build_review_pack(_case(), root=other)
        self.assertEqual(here["text_sha256"], there["text_sha256"])
        self.assertNotIn(self.root(), render_review_pack(here))

    def test_json_serializable(self):
        json.dumps(self.build(), allow_nan=False, ensure_ascii=False)

    def test_default_ceiling_is_15000(self):
        self.assertEqual(REVIEW_MAX_CHARS, 15000)
        self.assertEqual(self.build()["max_chars"], 15000)


# ───────────────────────────── обязательный хвост ─────────────────────────────


class MandatoryTail(_Base):
    def test_tail_present_and_last_in_ok_pack(self):
        text = render_review_pack(self.build())
        for question in TAIL_QUESTIONS:
            self.assertIn(question, text)
        tail_at = text.index("## ВОПРОСЫ РЕВЬЮЕРУ")
        for other in ("## СВОДКА", "## RESULT PACKET", "## ИСТОЧНИКИ В ПАКЕТЕ", "## НЕИЗВЕСТНО"):
            self.assertLess(text.index(other), tail_at, "%s должен быть ДО хвоста" % other)

    def test_tail_present_in_blocked_pack_too(self):
        case = _case(sources=[_src("gone.json"), _src("result.json", start=1, end=2)])
        case["result_packets"] = [
            {"task_id": "demo-task", "path": "result.json", "reported_status": "reported_done"}
        ]
        pack = self.build(case)
        self.assertEqual(pack["status"], "blocked")
        text = render_review_pack(pack)
        for question in TAIL_QUESTIONS:
            self.assertIn(question, text)

    def test_tail_covers_three_named_questions(self):
        joined = " ".join(TAIL_QUESTIONS)
        self.assertEqual(len(TAIL_QUESTIONS), 3)
        self.assertIn("УПРОЩАЕМО", joined)
        self.assertIn("НЕ ДЕЛАТЬ ВОВСЕ", joined)
        self.assertIn("ПЕРЕУСЛОЖНЕНО", joined)


# ───────────────────────────── fail-closed по потолку ─────────────────────────


class Ceiling(_Base):
    def test_required_source_over_ceiling_blocks_without_truncation(self):
        _write(self.root(), "big.txt", ("x" * 500 + "\n") * 40)
        case = _case(
            sources=[
                _src("result.json", start=1, end=2),
                _src("big.txt", required=True, start=1, end=40),
            ]
        )
        pack = self.build(case)
        self.assertEqual(pack["status"], "blocked")
        self.assertIn(pack["reason"], ("context_limit_exceeded", "required_pack_exceeds_budget"))
        self.assertEqual(pack["body"], "")
        self.assertEqual(pack["sources"], [])
        text = render_review_pack(pack)
        self.assertIn("## ПАКЕТ ЗАБЛОКИРОВАН", text)
        # Ни одного знака обязательного источника в пакет не просочилось.
        self.assertNotIn("x" * 100, text)

    def test_ceiling_measures_whole_pack_not_only_body(self):
        # Тело влезает в потолок, а пакет целиком — нет: рамка с таблицами
        # хешей в окно ревьюера входит так же, как выдержки.
        _write(self.root(), "mid.txt", ("y" * 90 + "\n") * 10)
        case = _case(sources=[_src("result.json", start=1, end=2), _src("mid.txt", start=1, end=10)])
        pack = self.build(case, max_chars=1400)
        self.assertEqual(pack["status"], "blocked")
        self.assertEqual(pack["reason"], "required_pack_exceeds_budget")
        self.assertGreater(pack["required_chars"], 1400)

    def test_optional_over_ceiling_is_omitted_with_reason(self):
        _write(self.root(), "extra.txt", ("z" * 200 + "\n") * 12)
        case = _case(
            sources=[
                _src("result.json", start=1, end=2),
                _src("extra.txt", required=False, lane="hq", role="context", start=1, end=12),
            ]
        )
        pack = self.build(case, max_chars=2600)
        self.assertEqual(pack["status"], "ok")
        self.assertEqual([s["path"] for s in pack["sources"]], ["result.json"])
        self.assertEqual(len(pack["omitted"]), 1)
        self.assertEqual(pack["omitted"][0]["path"], "extra.txt")
        self.assertEqual(pack["omitted"][0]["reason"], "context_limit")
        self.assertEqual(pack["omitted"][0]["lane"], "hq")
        self.assertEqual(pack["omitted"][0]["role"], "context")
        self.assertIn("`extra.txt`", render_review_pack(pack))

    def test_optional_that_fits_is_accepted(self):
        _write(self.root(), "small.txt", "нота\n")
        case = _case(
            sources=[_src("result.json", start=1, end=2), _src("small.txt", required=False, start=1, end=1)]
        )
        pack = self.build(case)
        self.assertEqual([s["path"] for s in pack["sources"]], ["result.json", "small.txt"])
        self.assertEqual(pack["omitted"], [])

    def test_missing_optional_keeps_controlled_reason(self):
        case = _case(
            sources=[_src("result.json", start=1, end=2), _src("nope.txt", required=False, start=1, end=1)]
        )
        pack = self.build(case)
        self.assertEqual(pack["status"], "ok")
        self.assertEqual(pack["omitted"], [{"path": "nope.txt", "role": "evidence", "lane": "pc", "reason": "missing"}])


# ───────────────────────────── обязательное не теряется ───────────────────────


class RequiredEvidence(_Base):
    def test_missing_required_source_blocks(self):
        case = _case(sources=[_src("result.json", start=1, end=2), _src("gone.json")])
        pack = self.build(case)
        self.assertEqual(pack["status"], "blocked")
        self.assertEqual(pack["reason"], "missing_required_source")
        self.assertEqual(pack["body"], "")

    def test_denylisted_required_source_blocks_instead_of_silent_hole(self):
        case = _case(sources=[_src("result.json", start=1, end=2), _src("secrets/note.txt")])
        pack = self.build(case)
        self.assertEqual(pack["status"], "blocked")
        self.assertEqual(pack["reason"], "required_source_denylisted")
        self.assertIn("secrets/note.txt", pack["detail"])

    def test_no_required_source_is_structural_error(self):
        case = _case(sources=[_src("result.json", required=False, start=1, end=2)])
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(case)
        self.assertEqual(ctx.exception.reason, "result_packet_not_required_source")


# ───────────────────────────── result packet ─────────────────────────────


class ResultPacket(_Base):
    def test_result_packet_carries_source_hash(self):
        pack = self.build()
        rec = pack["result_packets"][0]
        self.assertEqual(rec["path"], "result.json")
        self.assertEqual(rec["reported_status"], "reported_done")
        self.assertEqual(len(rec["source_sha256"]), 64)
        self.assertIn(rec["source_sha256"][:12], render_review_pack(pack))

    def test_result_packet_must_be_declared_source(self):
        case = _case(
            result_packets=[{"task_id": "t", "path": "elsewhere.json", "reported_status": "reported_done"}]
        )
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(case)
        self.assertEqual(ctx.exception.reason, "result_packet_not_in_manifest")

    def test_chain_requires_exactly_one_result_packet(self):
        _write(self.root(), "second.json", "{}\n")
        case = _case(
            sources=[_src("result.json", start=1, end=2), _src("second.json")],
            result_packets=[
                {"task_id": "a", "path": "result.json", "reported_status": "reported_done"},
                {"task_id": "b", "path": "second.json", "reported_status": "reported_done"},
            ],
        )
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(case)
        self.assertEqual(ctx.exception.reason, "invalid_result_packets")

    def test_digest_accepts_several_result_packets(self):
        _write(self.root(), "second.json", "{}\n")
        case = _case(
            kind="digest",
            case_id="demo-digest",
            sources=[_src("result.json", start=1, end=2), _src("second.json")],
            result_packets=[
                {"task_id": "a", "path": "result.json", "reported_status": "reported_done"},
                {"task_id": "b", "path": "second.json", "reported_status": "reported_failed"},
            ],
        )
        pack = self.build(case)
        self.assertEqual(pack["status"], "ok")
        self.assertEqual(len(pack["result_packets"]), 2)
        self.assertIn("суточный дайджест", render_review_pack(pack))


# ───────────────────────────── числа и их доказательства ──────────────────────


class Numbers(_Base):
    def test_number_gets_hash_of_its_source(self):
        pack = self.build()
        num = pack["numbers"][0]
        self.assertEqual(num["evidence"], "hashed")
        self.assertEqual(num["source"], "result.json")
        self.assertEqual(num["source_sha256"], pack["sources"][0]["source_sha256"])

    def test_number_whose_source_was_omitted_becomes_unknown(self):
        _write(self.root(), "extra.txt", ("z" * 200 + "\n") * 12)
        case = _case(
            sources=[
                _src("result.json", start=1, end=2),
                _src("extra.txt", required=False, start=1, end=12),
            ],
            numbers=[
                {"name": "тестов", "value": 31, "source": "result.json"},
                {"name": "звонков", "value": 7, "source": "extra.txt"},
            ],
        )
        pack = self.build(case, max_chars=2600)
        by_name = {n["name"]: n for n in pack["numbers"]}
        self.assertEqual(by_name["звонков"]["evidence"], "unknown")
        self.assertIsNone(by_name["звонков"]["source_sha256"])
        self.assertTrue(any(u["check"] == "number_evidence" for u in pack["unknowns"]))
        self.assertIn("## НЕИЗВЕСТНО", render_review_pack(pack))

    def test_number_source_must_be_declared(self):
        case = _case(numbers=[{"name": "n", "value": 1, "source": "unlisted.json"}])
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(case)
        self.assertEqual(ctx.exception.reason, "number_source_not_in_manifest")

    def test_non_finite_number_rejected(self):
        case = _case(numbers=[{"name": "n", "value": float("inf"), "source": "result.json"}])
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(case)
        self.assertEqual(ctx.exception.reason, "invalid_numbers")


# ───────────────────────────── валидация спецификации ─────────────────────────


class CaseValidation(_Base):
    def test_unknown_kind_rejected(self):
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(_case(kind="whatever"))
        self.assertEqual(ctx.exception.reason, "invalid_kind")

    def test_unknown_task_class_rejected(self):
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(_case(task_class="green"))
        self.assertEqual(ctx.exception.reason, "invalid_task_class")

    def test_case_id_must_be_a_slug(self):
        for bad in ("Демо", "a/b", "../evil", ""):
            with self.assertRaises(ReviewPackError) as ctx:
                self.build(_case(case_id=bad))
            self.assertEqual(ctx.exception.reason, "invalid_case_id")

    def test_dates_must_be_real_calendar_dates(self):
        for field, bad in (("build_date", "01.09.2026"), ("subject_date", "2026-02-31")):
            with self.assertRaises(ReviewPackError) as ctx:
                self.build(_case(**{field: bad}))
            self.assertEqual(ctx.exception.reason, "invalid_date")

    def test_summary_must_be_present_and_single_line(self):
        with self.assertRaises(ReviewPackError):
            self.build(_case(summary=[]))
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(_case(summary=["первая\nвторая"]))
        self.assertEqual(ctx.exception.reason, "invalid_summary")

    def test_objective_bounded(self):
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(_case(active_objective="ц" * 1001))
        self.assertEqual(ctx.exception.reason, "oversized_objective")

    def test_bad_max_chars_rejected(self):
        with self.assertRaises(ReviewPackError) as ctx:
            self.build(max_chars=0)
        self.assertEqual(ctx.exception.reason, "invalid_max_chars")


# ───────────────────────────── адрес и индекс ─────────────────────────────


class Address(_Base):
    def test_filename_uses_build_date_not_subject_date(self):
        pack = self.build()
        self.assertEqual(pack_filename(pack), "2026-09-01-demo-chain.md")

    def test_index_line_is_one_line_with_path_and_numbers(self):
        pack = self.build()
        line = index_line(pack, "docs/review_outbox/2026-09-01-demo-chain.md")
        self.assertNotIn("\n", line)
        self.assertIn("docs/review_outbox/2026-09-01-demo-chain.md", line)
        self.assertIn("вид=chain", line)
        self.assertIn("предмет=2026-08-31", line)
        self.assertIn("статус=ok", line)
        self.assertIn("знаков=%d/%d" % (pack["text_chars"], pack["max_chars"]), line)
        # Журнал — индекс, а не хранилище тел: строка обязана быть короткой.
        self.assertLessEqual(len(line), 600)

    def test_index_line_for_blocked_pack_says_blocked(self):
        case = _case(sources=[_src("result.json", start=1, end=2), _src("gone.json")])
        pack = self.build(case)
        self.assertIn("статус=blocked", index_line(pack, "docs/review_outbox/x.md"))


# ───────────────────────────── чистота ─────────────────────────────


class NoSideEffects(_Base):
    def test_build_makes_no_network_no_subprocess_no_write(self):
        before = sorted(os.listdir(self.root()))

        def _boom_socket(*a, **kw):
            raise AssertionError("сборка пакета не имеет права ходить в сеть")

        def _boom_popen(*a, **kw):
            raise AssertionError("сборка пакета не имеет права звать подпроцесс")

        real_socket, real_popen = socket.socket, subprocess.Popen
        socket.socket, subprocess.Popen = _boom_socket, _boom_popen
        try:
            pack = self.build()
        finally:
            socket.socket, subprocess.Popen = real_socket, real_popen

        self.assertEqual(pack["status"], "ok")
        self.assertEqual(sorted(os.listdir(self.root())), before)

    def test_module_calls_no_clock_and_no_randomness(self):
        # Дата сборки — ПОЛЕ ВХОДА. Часы в модуле означали бы, что один и тот
        # же пакет завтра пересобирается другим и хеш перестаёт быть адресом.
        #
        # Судим по ВЫЗОВАМ (AST), а не по подстроке: греп по тексту находит
        # `datetime.now()` в докстринге, где он назван как запрещённый, и
        # объявляет модуль виновным за собственное правило.
        with open(review_pack.__file__, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        forbidden = {"now", "utcnow", "today", "time", "time_ns", "monotonic", "random", "getenv"}
        called = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr)
            elif isinstance(func, ast.Name):
                called.add(func.id)
        self.assertEqual(called & forbidden, set(), "модуль зовёт недетерминированный источник")


if __name__ == "__main__":
    unittest.main()
