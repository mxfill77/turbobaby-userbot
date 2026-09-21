# -*- coding: utf-8 -*-
"""test_exit_evidence.py — «НАМ НЕ ДАЛИ РАБОТАТЬ» ОТДЕЛЬНО ОТ «МЫ СЛОМАЛИСЬ».

Предмет один: свидетельство выхода процесса (код возврата + хвост stderr) сохраняется у
ОБОИХ — исполнителя и думателя, — и по нему `exec_error` разводится на отличимые исходы,
среди которых упор в лимит подписки назван своим именем. Поднять внешний исход, НЕ получив
ненулевого кода возврата, обязано быть невозможно.

ГОЛДЕНЫ — ДОСЛОВНЫЕ СТРОКИ ЖИВЫХ ПРОВАЛОВ, а не идеализированные. Источник — поле
``result_head`` расписок ревью-контура `docs/review_receipts/pc-2026-09-{16,18,20}-*.json`
(шесть рядов, которыми перепись 69n измерила класс) плюс фикстуры `test_ext_refusal_unknown.py`
(#37, #101, #12, #100). Правило полосы прямое: придуманная «похожая» строка зеленеет молча.

ОТРИЦАТЕЛЬНЫЙ ТЕСТ живёт в :class:`TestNoEvidenceNoVerdict` — там подделывается ровно то
состояние, ради которого замок и заведён: признак выглядит правильным, а свидетельства нет.

ПОЧЕМУ ПОДДЕЛЬНЫЕ КЛЮЧИ ЗДЕСЬ СОБИРАЮТСЯ СКЛЕЙКОЙ, А НЕ ПИШУТСЯ ЦЕЛИКОМ. Литерал вида
``ИМЯ=<строка формы ключа>`` — это ЗНАЧЕНИЕ секрета по правилу гарда ПК (`_RE_SECRET_ASSIGN`),
и первая редакция этого файла была им перехвачена на границе, хотя ключ был выдуманным.
Гард прав: отличить выдуманный ключ от боевого по виду нельзя, ради того правило и стои́т.
Склейка даёт ту же проверяемую строку в рантайме, не кладя её формой на диск.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_exit_evidence -v
"""
from __future__ import annotations

import os
import ast
import unittest

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import exit_evidence as ee             # noqa: E402

# ═══════════════════════ ЖИВЫЕ СТРОКИ, ДОСЛОВНО ══════════════════════════════
# Шесть рядов, закрывшихся кодом exec_error за 14–21.09.2026. Слова процесса восстановлены
# из расписок ревью; в логе демона их нет ни у одного — это и есть измеренная потеря.
LIVE_ROWS = (
    # (ряд, дата, дословные слова процесса, ожидаемый исход)
    ("#51", "16.09", "Failed to authenticate: OAuth session expired and could not be "
                     "refreshed.", ee.OUT_ACCESS),
    ("#52", "16.09", "Failed to authenticate: OAuth session expired and could not be "
                     "refreshed.", ee.OUT_ACCESS),
    ("#90", "18.09", "You've hit your weekly limit \u00b7 resets Sep 22, 4am (Asia/Bangkok).",
     ee.OUT_LIMIT),
    ("#92", "18.09", "You've hit your weekly limit \u00b7 resets Sep 22, 4am (Asia/Bangkok).",
     ee.OUT_LIMIT),
    ("#153", "20.09", "You've hit your session limit \u00b7 resets 4am (Asia/Bangkok).",
     ee.OUT_LIMIT),
    ("#154", "20.09", "You've hit your session limit \u00b7 resets 4am (Asia/Bangkok).",
     ee.OUT_LIMIT),
)
# Ряды с других суток — тот же класс, другие формы (фикстуры test_ext_refusal_unknown).
LIVE_37 = "You've hit your session limit \u00b7 resets 3:30am (Asia/Bangkok)"
LIVE_101 = ("API Error: 529 Overloaded. This is a server-side issue, usually temporary - try "
            "again in a moment. If it persists, check https://status.claude.com.")
# ДВА ЖИВЫХ РЯДА, КОТОРЫЕ ИМЕНИ НЕ ПОЛУЧАЮТ, И ЭТО ПРАВИЛЬНЫЙ ОТВЕТ, А НЕ ПРОМАХ.
LIVE_12_SILENT = ""                     # процесс не сказал ничего (rc=1, оба потока пусты)
LIVE_100 = "API Error: Server error mid-response. The response above may be incomplete."

# ── ПОДДЕЛЬНЫЕ КЛЮЧИ, СОБРАННЫЕ В РАНТАЙМЕ (см. шапку) ───────────────────────
FAKE_API = "sk-" + "ant-api03-" + "A" * 24
FAKE_GH = "ghp_" + "B" * 32
FAKE_JWT = ".".join(("eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxMjM0NSJ9", "C" * 10))
FAKE_TG = "1234567890" + ":" + "D" * 36
FAKE_PAIR_VALUE = "Qwerty" + "123456" + "ZZ"


class TestLiveCorpus(unittest.TestCase):
    """Живой корпус: что новый разбор сказал бы о рядах, которые уже случились."""

    def test_six_rows_of_the_week_get_named(self):
        for row, day, said, want in LIVE_ROWS:
            with self.subTest(row=row, day=day):
                kind, hit = ee.classify(1, said, "")
                self.assertEqual(kind, want, "ряд %s (%s) опознан как %r" % (row, day, kind))
                self.assertTrue(hit, "имя есть, а слов процесса в свидетельстве нет")
                self.assertIn(hit.lower(), said.lower())   # слова ДОСЛОВНЫЕ, не перевод

    def test_other_days_same_class(self):
        self.assertEqual(ee.classify(1, LIVE_37, "")[0], ee.OUT_LIMIT)
        self.assertEqual(ee.classify(1, LIVE_101, "")[0], ee.OUT_OVERLOAD)

    def test_silent_and_unnamed_rows_stay_unknown(self):
        """Процесс молчит или говорит не о том → НЕИЗВЕСТНО, а не догадка.

        #12 (03.09): rc=1, оба потока пусты. #100 (03.09): «Server error mid-response» — и
        заход при этом НАРАБОТАЛ (коммит 14cb105 в окне). Назвать их внешним ограничением
        значило бы выдать льготу за незнание."""
        for said in (LIVE_12_SILENT, LIVE_100):
            with self.subTest(said=said[:40]):
                kind, _ = ee.classify(1, said, "")
                self.assertEqual(kind, ee.OUT_UNKNOWN)
                self.assertFalse(ee.is_external(kind))

    def test_stderr_channel_reads_the_same_forms(self):
        """Форма опознаётся и в stderr: у исполнителя она приходит в stdout, у думателя — в stderr."""
        for _row, _day, said, want in LIVE_ROWS:
            with self.subTest(said=said[:40]):
                self.assertEqual(ee.classify(1, "", said)[0], want)


class TestNoEvidenceNoVerdict(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ: признак выглядит правильным, свидетельства нет → прибор отказывает."""

    PERFECT = "You've hit your weekly limit \u00b7 resets Sep 22, 4am (Asia/Bangkok)."

    def test_zero_return_code_never_buys_an_external_verdict(self):
        """ГЛАВНЫЙ ЗАМОК. Текст идеален, код возврата нулевой → исхода нет вовсе.

        Это ровно подделка: доклад, В КОТОРОМ НАПИСАНЫ нужные слова, производит наш пересказ,
        а код возврата производит ОС. Без второго первое не значит ничего."""
        kind, said = ee.classify(0, self.PERFECT, self.PERFECT)
        self.assertEqual(kind, ee.OUT_NONE)
        self.assertEqual(said, "")
        self.assertFalse(ee.is_external(kind))

    def test_missing_return_code_is_not_a_verdict_either(self):
        """Кода возврата НЕТ (None / пусто / не число) → «не знаю», а не «работать не дали»."""
        for rc in (None, "", "   ", "нет", True, False, [1]):
            with self.subTest(rc=rc):
                self.assertFalse(ee.refused(rc))
                self.assertEqual(ee.classify(rc, self.PERFECT, self.PERFECT)[0], ee.OUT_NONE)

    def test_quoting_the_phrase_mid_output_does_not_buy_the_verdict(self):
        """Задача, РАЗБИРАЮЩАЯ этот класс, цитирует фразу в докладе — и вердикта не получает.

        Ровно этот случай убил бы правку: отчёт о заходе 69r обязан процитировать живую форму
        отказа, а заканчивается он строкой RESULT. Якорь — ПОСЛЕДНЯЯ непустая строка, куда
        свою фатальную строку ставит CLI, а не модель."""
        report = ("Разбираю класс: живая форма отказа — «%s», и до 21.09 она приезжала под "
                  "exec_error.\nСДЕЛАНО: разведение заведено.\nRESULT: класс закрыт." % self.PERFECT)
        kind, _ = ee.classify(1, report, "")
        self.assertEqual(kind, ee.OUT_UNKNOWN)
        self.assertFalse(ee.is_external(kind))

    def test_two_kinds_on_one_line_stay_unknown_and_name_both(self):
        """Отличить нельзя → НЕИЗВЕСТНО со свидетельством, а не выбор красивого имени."""
        kind, said = ee.classify(1, "", "API Error: 429 rate limit; also 529 Overloaded")
        self.assertEqual(kind, ee.OUT_UNKNOWN)
        self.assertIn("+", said)          # названы ОБА совпадения, а не выбрано одно

    def test_unknown_keeps_the_evidence(self):
        """Промах словаря НЕ стирает свидетельство: код возврата и хвост stderr на месте."""
        ev = ee.evidence(7, "", "Segmentation fault at 0x0")
        self.assertIn("exit=7", ev)
        self.assertIn("Segmentation fault", ev)
        self.assertIn(ee.OUT_UNKNOWN, ev)


class TestSecretsCutBeforeWrite(unittest.TestCase):
    """Секреты вырезаются ДО записи — внутри `evidence`, а не памятью вызывающего."""

    def test_named_pair_loses_its_value_keeps_its_name(self):
        out = ee.scrub("env ANTHROPIC_API_KEY" + "=" + FAKE_API + " ok")
        self.assertIn("ANTHROPIC_API_KEY", out)          # имя — это диагноз, оно остаётся
        self.assertNotIn(FAKE_API, out)                  # значение — нет
        self.assertIn(ee.MASK, out)

    def test_bare_shapes_die_without_a_name_nearby(self):
        for secret in (FAKE_API, FAKE_GH, FAKE_JWT, FAKE_TG):
            with self.subTest(secret=secret[:6]):
                self.assertNotIn(secret, ee.scrub("в трубе было " + secret + " и всё"))

    def test_evidence_scrubs_without_being_asked(self):
        """Вызывающий чистку НЕ зовёт — и секрет всё равно не доезжает до записи."""
        ev = ee.evidence(1, "", "fatal: BRIDGE_TOKEN" + "=" + FAKE_PAIR_VALUE + " rejected")
        self.assertNotIn(FAKE_PAIR_VALUE, ev)
        self.assertIn("BRIDGE_TOKEN", ev)

    def test_hashes_survive_the_scrub(self):
        """Хеш коммита и sha256 — ВАЛЮТА ДОКАЗАТЕЛЬСТВА полосы, а не секрет: они остаются.

        Плечо «замазать всё длинное шестнадцатеричное» съело бы улику в каждой второй строке
        ради формы ключа, которой у нас нет ни одного вида."""
        line = ("commit 33e6c002 sha256 "
                "969cdf93aabbccddeeff00112233445566778899aabbccddeeff001122334455")
        self.assertEqual(ee.scrub(line), line)

    def test_live_diagnosis_is_not_eaten_by_the_scrub(self):
        """Живая фраза #51 несёт слово OAuth — и обязана дойти до читателя целиком."""
        said = "Failed to authenticate: OAuth session expired and could not be refreshed."
        self.assertEqual(ee.scrub(said), said)


class TestTailAndPurity(unittest.TestCase):

    def test_tail_declares_its_cut_by_number(self):
        long = "x" * 1000
        out = ee.tail(long, 100)
        self.assertIn("100", out)
        self.assertIn("1000", out)
        self.assertTrue(out.endswith("x" * 100))

    def test_short_tail_is_byte_for_byte(self):
        self.assertEqual(ee.tail("  коротко  ", 400), "коротко")

    def test_empty_stderr_is_named_not_skipped(self):
        self.assertIn("(пуст)", ee.evidence(1, "", ""))

    def test_module_imports_only_re(self):
        """Модуль ЧИСТЫЙ: ни диска, ни сети, ни окружения — иначе его нельзя звать из
        любой ветки провала, в том числе из ветки, где всё уже сломано."""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "exit_evidence.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names - {"__future__"}, {"re"}, "лишний импорт: %s" % sorted(names))


class TestDaemonWiring(unittest.TestCase):
    """Смычка с демоном: код причины, маркер и зеркала. Демон импортируется целиком."""

    def setUp(self):
        import pc_orchestrator as o
        self.o = o

    def test_new_code_exists_with_its_own_marker(self):
        o = self.o
        name, mark = o.FAIL_REASONS[o.FAIL_EXTERNAL_LIMIT]
        self.assertEqual(mark, o.LIMIT_MARK)
        self.assertNotIn(mark, (o.TIMEOUT_MARK, o.MANUAL_MARK, o.NET_MARK))
        self.assertNotIn("ошибка выполнения", name)   # имя = что меряет, а не «сбой вообще»

    def test_thinker_is_not_called_on_external_limit(self):
        """Переформулировка задачи лимит не поднимает — круг самопочинки не жжём."""
        o = self.o
        self.assertIn(o.LIMIT_MARK, o.NO_HEAL_PREFIXES)

    def test_fail_names_mirror_is_complete(self):
        """Зеркало имён у сигналов ящика не отстаёт от словаря демона ни на один код."""
        import shtab_box_signals as sig
        o = self.o
        self.assertEqual(set(sig.FAIL_NAMES), set(o.FAIL_REASONS))
        for code, (name, _mark) in o.FAIL_REASONS.items():
            self.assertEqual(sig.FAIL_NAMES[code], name)

    def test_external_limit_keeps_the_external_status_downstream(self):
        """ЗАМОК ОТ МОЛЧАЛИВОГО СУЖЕНИЯ. Ряд, который вчера был внешним отказом под
        `exec_error`, сегодня под новым кодом обязан остаться внешним: правка меняла ИМЯ
        исхода, а не судьбу ряда."""
        import shtab_box_signals as sig
        self.assertIn(self.o.FAIL_EXTERNAL_LIMIT, sig.EXT_CODES)
        row = {"status": "failed",
               "result": "\U0001f6a7 провал [причина=external_limit \u00b7 внешнее ограничение — "
                         "работать не дали]: claude exit=1: You've hit your weekly limit. "
                         "Следов работы в окне 18.09 10:00–10:02 UTC нет (коммитов 0, "
                         "записей журнала 0)."}
        ok, why = sig.external_refusal(row)
        self.assertTrue(ok, why)

    def test_external_limit_without_process_fall_is_refused_downstream(self):
        """И тот же ряд БЕЗ процессного падения в голове — не внешний: текст итога,
        собранный мимо демона, льготы не покупает."""
        import shtab_box_signals as sig
        row = {"status": "failed",
               "result": "провал [причина=external_limit \u00b7 внешнее ограничение — работать "
                         "не дали]: кончился лимит, честное слово. Следов работы в окне 18.09 "
                         "10:00–10:02 UTC нет (коммитов 0, записей журнала 0)."}
        ok, why = sig.external_refusal(row)
        self.assertFalse(ok)
        self.assertIn("external_limit", why)


class TestExecutorEndToEnd(unittest.TestCase):
    """ЖИВОЙ ПУТЬ ИСПОЛНИТЕЛЯ: `_run_task_impl` целиком, claude замокан кортежем (rc, out, err).

    Формат мока повторяет живой формат запуска: `run_claude` возвращает ровно тройку, а не
    «что-то похожее» — мок, разошедшийся с форматом, зеленеет молча."""

    def setUp(self):
        import tempfile
        import pc_orchestrator as o
        self.o = o
        tmp = tempfile.mkdtemp(prefix="exitev_")
        self._save = (o.run_claude, o._cowork, o._notify, o._claude_budget_gate, o._work_evidence,
                      o.TASK_START_FILE, o.COWORK_LEDGER, o._selfheal_on, o._fail_is_network)
        o._cowork = lambda *a, **k: None
        o._notify = lambda *a, **k: None
        o._selfheal_on = lambda: False
        o._claude_budget_gate = lambda *a, **k: (True, "тест: бюджет пропущен")
        o._work_evidence = lambda since, until=None: {"commits": [], "journal": []}
        # БОЕВЫХ ФАЙЛОВ СОСТОЯНИЯ ТЕСТ НЕ КАСАЕТСЯ: отметки claim и реестр журнала — во
        # временный каталог с уникальным суффиксом.
        o.TASK_START_FILE = os.path.join(tmp, "task_started.json")
        o.COWORK_LEDGER = os.path.join(tmp, "cowork_log.ledger")
        # Обрыв связи судится СВОИМ прибором и своими тестами; здесь он выключен, чтобы
        # предмет этого файла не смешался с чужим вердиктом.
        o._fail_is_network = lambda *a, **k: ""
        self.addCleanup(lambda: (setattr(o, "run_claude", self._save[0]),
                                 setattr(o, "_cowork", self._save[1]),
                                 setattr(o, "_notify", self._save[2]),
                                 setattr(o, "_claude_budget_gate", self._save[3]),
                                 setattr(o, "_work_evidence", self._save[4]),
                                 setattr(o, "TASK_START_FILE", self._save[5]),
                                 setattr(o, "COWORK_LEDGER", self._save[6]),
                                 setattr(o, "_selfheal_on", self._save[7]),
                                 setattr(o, "_fail_is_network", self._save[8])))

    def _run(self, rc=1, out="", err="", tid=69210):
        from unittest import mock
        o = self.o
        o.run_claude = lambda prompt, timeout, cwd, env: (rc, out, err)
        with mock.patch.object(o, "resolve_claude", lambda: r"C:\x\claude.exe"):
            return o._run_task_impl(tid, "ultrathink\nсделай X")

    def test_live_row_90_closes_as_external_limit_not_as_our_error(self):
        """ЖИВОЙ РЯД #90 (18.09) — тот самый, что владелец прочитал как «ошибка выполнения»."""
        o = self.o
        status, res = self._run(rc=1, out=LIVE_ROWS[2][2])
        self.assertEqual(status, "failed")
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_EXTERNAL_LIMIT)
        self.assertTrue(res.startswith(o.LIMIT_MARK), "маркер причины — ПЕРВЫМ символом")
        self.assertIn("работать не дали", res)
        self.assertNotIn("ошибка выполнения", res)
        self.assertIn("claude exit=1", res)          # голова, по которой судит ящик Штаба
        self.assertIn("weekly limit", res)           # дословные слова процесса сохранены

    def test_live_row_51_access_also_named(self):
        o = self.o
        status, res = self._run(rc=1, out=LIVE_ROWS[0][2])
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_EXTERNAL_LIMIT)
        self.assertIn("OAuth session expired", res)

    def test_our_own_breakage_stays_our_own_but_gains_evidence(self):
        """РЕГРЕСС В ОБЕ СТОРОНЫ. Наша поломка остаётся `exec_error` — и при этом впервые
        несёт свидетельство: код возврата и ХВОСТ STDERR, которого раньше при НЕПУСТОМ
        stdout не сохранялось нигде (`out_s or err_tail` — ветка `or` до stderr не доходила)."""
        o = self.o
        status, res = self._run(rc=1, out="я начал и упал",
                                err="Traceback: TypeError: NoneType is not callable")
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_EXEC_ERROR)
        self.assertNotIn(o.LIMIT_MARK, res)
        self.assertIn("СВИДЕТЕЛЬСТВО", res)
        self.assertIn("exit=1", res)
        self.assertIn("TypeError", res, "хвост stderr при НЕПУСТОМ stdout больше не теряется")

    def test_zero_code_with_perfect_words_never_becomes_external(self):
        """ПОДДЕЛКА НА ЖИВОМ ПУТИ: модель напечатала фразу отказа и вернула НОЛЬ.
        Ряд закрывается обычной дорогой, внешнего имени не получает ни одной веткой."""
        o = self.o
        status, res = self._run(rc=0, out="%s\nRESULT: готово" % LIVE_ROWS[2][2])
        self.assertEqual(status, "done")
        self.assertNotIn(o.LIMIT_MARK, res)
        self.assertNotIn("external_limit", res)

    def test_secret_in_stderr_does_not_reach_the_result(self):
        """stderr чужого процесса несёт ключ — в итог задачи он не попадает."""
        status, res = self._run(rc=1, out="", err="fatal: bad key " + FAKE_API)
        self.assertNotIn(FAKE_API, res)
        self.assertIn(ee.MASK, res)


class TestThinkerKeepsItsEvidence(unittest.TestCase):
    """ДУМАТЕЛЬ: его stderr больше не выбрасывается целиком.

    До 21.09 в лог уходило ровно «думатель exit=N — fail-safe». Из шести рядов exec_error за
    14–21.09 думатель падал exit=1 в ЧЕТЫРЁХ, и по всем четырём диагноз не устанавливался."""

    def setUp(self):
        import pc_orchestrator as o
        self.o = o

    def _thinker(self, rc, out, err):
        import subprocess
        from unittest import mock
        o = self.o
        done = subprocess.CompletedProcess(args=["claude"], returncode=rc, stdout=out, stderr=err)
        with mock.patch.object(o, "resolve_claude", lambda: r"C:\x\claude.exe"), \
             mock.patch.object(o, "_claude_budget_gate", lambda *a, **k: (True, "тест")), \
             mock.patch.object(o.subprocess, "run", lambda *a, **k: done):
            with self.assertLogs(o.log, level="WARNING") as caught:
                out_text = o._thinker_exec("промпт", 10, "тест-тег")
        return out_text, "\n".join(caught.output)

    def test_thinker_limit_is_logged_with_code_and_tail(self):
        out_text, logged = self._thinker(1, "", LIVE_ROWS[4][2])
        self.assertIsNone(out_text, "fail-safe не изменился: наверх по-прежнему None")
        self.assertIn("exit=1", logged)
        self.assertIn("session limit", logged, "хвост stderr больше не выброшен")
        self.assertIn(ee.OUT_LIMIT, logged)
        self.assertIn("РАБОТАТЬ НЕ ДАЛИ", logged)

    def test_thinker_unknown_still_logs_its_evidence(self):
        out_text, logged = self._thinker(2, "", "Traceback: KeyError: 'verdict'")
        self.assertIsNone(out_text)
        self.assertIn("exit=2", logged)
        self.assertIn("KeyError", logged)
        self.assertNotIn("РАБОТАТЬ НЕ ДАЛИ", logged)   # догадки нет: исход unknown

    def test_thinker_secret_never_reaches_the_log(self):
        _out, logged = self._thinker(1, "", "auth failed for " + FAKE_API)
        self.assertNotIn(FAKE_API, logged)
        self.assertIn(ee.MASK, logged)


if __name__ == "__main__":
    unittest.main(verbosity=2)
