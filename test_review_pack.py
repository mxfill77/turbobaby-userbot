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
import review_pack_build
from review_pack import (
    FRAME_DOC_NAME,
    FRAME_VERSION_UNKNOWN,
    REVIEW_MAX_CHARS,
    TAIL_QUESTIONS,
    ReviewPackError,
    build_review_pack,
    index_line,
    pack_filename,
    parse_frame_version,
    render_review_pack,
)


# Номер редакции для юнитов. Это ФИКСТУРА, а не «известная версия рамки»: код
# версию не хранит нигде, и подставлять сюда живой номер значило бы завести ту
# самую вторую копию — тест позеленел бы на устаревшем числе как на верном.
FRAME_VERSION_FIXTURE = "01.01.2026 #7"


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
        # Версия канона — ОБЯЗАТЕЛЬНЫЙ параметр сборки; у хелпера она есть по
        # умолчанию, чтобы остальные юниты мерили своё. Само требование её
        # передать проверяется отдельно (FrameVersionHeader), а не молчанием
        # этой строки.
        kw.setdefault("frame_version", FRAME_VERSION_FIXTURE)
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
        there = build_review_pack(_case(), root=other, frame_version=FRAME_VERSION_FIXTURE)
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
        # Потолок ЗАМЕРЕН, а не назначен: он обязан впускать обязательный
        # источник и не впускать необязательный. Минимальный пакет — 2547 знаков
        # (замер 05.09 после того, как в шапку встали версия канона и граница
        # ревьюера), с `extra.txt` — 3000+. Прежние 2600 теперь блокируют пакет
        # целиком, и юнит мерил бы не то, ради чего написан.
        pack = self.build(case, max_chars=2800)
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


# ───────────────── версия канона рамки в шапке (задача 223) ─────────────────


class FrameVersionHeader(_Base):
    """Шапка называет редакцию правил, по которой мы живём, — или честно молчит.

    Ревьюер спорит с нами о правилах, не имея их текста: без номера редакции
    «у вас запрещено X» и «у вас БЫЛО запрещено X» — одна и та же фраза.
    """

    def test_header_names_the_frame_version_next_to_the_build_date(self):
        text = render_review_pack(self.build())
        self.assertIn("версия канона рамки", text)
        self.assertIn(FRAME_VERSION_FIXTURE, text)
        self.assertIn(FRAME_DOC_NAME, text)
        # «рядом с датой сборки» — это про место, а не про настроение: строка
        # обязана стоять В ШАПКЕ, до первого раздела, иначе её прочитают после
        # выводов, ради которых она и нужна.
        lines = text.splitlines()
        self.assertLess(_line_no(lines, "версия канона рамки"), _line_no(lines, "## ЦЕЛЬ"))
        self.assertEqual(
            _line_no(lines, "версия канона рамки") - _line_no(lines, "пакет собран"), 1,
            "строка версии оторвалась от даты сборки",
        )

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ №1: источник недоступен ──
    def test_unavailable_source_prints_unknown_and_never_a_stale_number(self):
        text = render_review_pack(self.build(frame_version=None))
        self.assertIn("версия канона рамки: **%s**" % FRAME_VERSION_UNKNOWN, text)
        # Ровно то, чем эта ветка провалилась бы: подстановка «последней
        # известной». Числа в строке версии при отказе быть не должно вовсе.
        version_line = [ln for ln in text.splitlines() if ln.startswith("версия канона рамки")][0]
        self.assertNotRegex(version_line, r"\d{2}\.\d{2}\.\d{4}")

    def test_unavailable_source_carries_the_reason(self):
        text = render_review_pack(self.build(frame_version=None, frame_version_note="мост не ответил: timeout"))
        self.assertIn("мост не ответил: timeout", text)

    def test_blocked_pack_also_names_the_version(self):
        case = _case(
            sources=[_src("gone.json")],
            result_packets=[{"task_id": "t", "path": "gone.json", "reported_status": "reported_done"}],
            numbers=[],
        )
        pack = self.build(case, frame_version=None)
        self.assertEqual(pack["status"], "blocked")
        self.assertIn(FRAME_VERSION_UNKNOWN, render_review_pack(pack))

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ №2: без версии пакет не собирается МОЛЧА ──
    def test_build_without_the_parameter_refuses_loudly(self):
        with self.assertRaises(ReviewPackError) as ctx:
            build_review_pack(_case(), root=self.root())
        self.assertEqual(ctx.exception.reason, "missing_frame_version")

    def test_render_of_a_dict_without_the_field_refuses_loudly(self):
        pack = self.build()
        pack.pop("frame_version")
        with self.assertRaises(ReviewPackError) as ctx:
            render_review_pack(pack)
        self.assertEqual(ctx.exception.reason, "missing_frame_version")

    # ── ЗАМОК ОТ ВТОРОЙ КОПИИ (предсмертный взгляд задачи) ──
    def test_version_stored_in_the_case_file_is_refused(self):
        # Спецификации лежат файлами в docs/review_cases/ и пересобираются днями
        # позже. Версия, записанная туда, — ровно та молча стареющая копия.
        with self.assertRaises(ReviewPackError) as ctx:
            build_review_pack(_case(frame_version="02.09.2026 #1"), root=self.root(),
                              frame_version=FRAME_VERSION_FIXTURE)
        self.assertEqual(ctx.exception.reason, "frame_version_in_case")

    def test_the_module_stores_no_version_of_its_own(self):
        # Ни одной даты вида ДД.ММ.ГГГГ в исходнике сборщика: номер редакции
        # приходит извне, и захардкодить его нельзя незаметно.
        with open(review_pack.__file__, "r", encoding="utf-8") as fh:
            body = fh.read()
        # Комментарий-пример шапки канона — единственное законное вхождение;
        # мерим строки КОДА, а не текст целиком.
        code = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        self.assertNotRegex(code, r"\d{2}\.\d{2}\.20\d{2}\s*#\s*\d")

    def test_bad_version_value_is_refused(self):
        for bad in ("", "  ", 42, ["02.09.2026 #1"], "x" * 200):
            with self.assertRaises(ReviewPackError):
                build_review_pack(_case(), root=self.root(), frame_version=bad)

    # ── разбор шапки канона ──
    def test_parser_reads_the_live_header_shape(self):
        head = "═══ РАМКА ПРОЕКТА TurboBaby — ИНСТРУКЦИИ ШТАБА (версия 02.09.2026 #1) ═══\n\nтело\n"
        self.assertEqual(parse_frame_version(head), "02.09.2026 #1")

    def test_parser_says_none_instead_of_guessing(self):
        for text in ("", None, "рамка без номера редакции", "версия 5"):
            self.assertIsNone(parse_frame_version(text))

    def test_parser_ignores_a_number_far_below_the_header(self):
        # Слово «версия» встречается в прозе канона; подцепить оттуда чужое
        # число хуже, чем не найти ничего.
        body = "шапки нет\n" * 20 + "версия 01.01.1999 #9\n"
        self.assertIsNone(parse_frame_version(body))

    # ── руки: живое чтение и его отказ ──
    def test_hands_read_the_same_node_the_executors_read(self):
        seen = {}

        def reader(name=None):
            seen["name"] = name
            return "═══ РАМКА ПРОЕКТА TurboBaby (версия 03.09.2026 #2) ═══\n"

        version, note = review_pack_build.live_frame_version(reader=reader)
        self.assertEqual(seen["name"], FRAME_DOC_NAME)
        self.assertEqual((version, note), ("03.09.2026 #2", None))

    def test_hands_return_unknown_when_the_bridge_refuses(self):
        def reader(name=None):
            raise RuntimeError("мост не ответил")

        version, note = review_pack_build.live_frame_version(reader=reader)
        self.assertIsNone(version)
        self.assertIn("мост не ответил", note)

    def test_hands_do_not_touch_the_live_node_under_tests(self):
        version, note = review_pack_build.live_frame_version(test_context=True)
        self.assertIsNone(version)
        self.assertIn("тестовый контекст", note)

    def test_pure_core_still_has_no_io(self):
        # Живое чтение живёт в РУКАХ; ядро обязано остаться чистым, иначе пакет
        # перестанет пересобираться байт в байт.
        with open(review_pack.__file__, "r", encoding="utf-8") as fh:
            code = fh.read()
        for forbidden in ("import brain_writer", "urllib", "requests", "subprocess"):
            self.assertNotIn(forbidden, code)


# ───────────────── роль и граница ревьюера — ЯВНО (задача 223) ─────────────────


class ReviewerRoleAndBoundary(_Base):
    def test_role_and_boundary_are_stated_explicitly(self):
        text = render_review_pack(self.build())
        self.assertIn("РОЛЬ РЕВЬЮЕРА:", text)
        self.assertIn("ГРАНИЦА:", text)

    def test_boundary_says_he_does_not_see_live_state(self):
        text = render_review_pack(self.build())
        self.assertIn("НЕ видит", text)
        self.assertIn("НЕ утверждает", text)

    def test_boundary_stands_before_the_facts(self):
        lines = render_review_pack(self.build()).splitlines()
        self.assertLess(_line_no(lines, "ГРАНИЦА:"), _line_no(lines, "## RESULT PACKET"))


def _line_no(lines, needle):
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise AssertionError("в тексте пакета нет строки %r" % needle)


if __name__ == "__main__":
    unittest.main()
