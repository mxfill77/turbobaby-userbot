# -*- coding: utf-8 -*-
"""
test_cowork_log_append.py — писатель строки-итога в мозг: РАЗОВЫЙ сбой моста не должен
терять строку. Инцидент 28.07: `read_doc` вернул 404 (флакость Apps Script: /exec → 302 →
googleusercontent, цель редиректа иногда 404), писатель счёл это фатальным и напечатал
«НЕ ЗАПИСАНО (сохрани вручную)» — строка держалась только в голове человека.

Сети здесь нет: транспорт инъектируется через фейковый fn (как _get у pricing.fleet).
"""

import io
import os
import re
import sys
import json
import datetime
import contextlib
import tempfile
import unittest
import urllib.error
from unittest import mock

import cowork_log_append as cla


def _http(code):
    return urllib.error.HTTPError("https://x/exec", code, "boom", None, None)


class TestRetry(unittest.TestCase):
    """Повтор: временное — повторяем, постоянное — нет."""

    def test_transient_404_retried_and_recovers(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise _http(404)          # ровно тот отказ, что случился 28.07
            return {"ok": True}

        r = cla.with_retry(flaky, _sleep=lambda s: None)
        self.assertTrue(r["ok"])
        self.assertEqual(len(calls), 2)   # ← одного повтора хватило

    def test_timeout_retried(self):
        calls = []

        def slow():
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError("read timed out")
            return {"ok": True}

        self.assertTrue(cla.with_retry(slow, _sleep=lambda s: None)["ok"])
        self.assertEqual(len(calls), 2)

    def test_permanent_403_not_retried(self):
        calls = []

        def denied():
            calls.append(1)
            raise _http(403)

        with self.assertRaises(urllib.error.HTTPError):
            cla.with_retry(denied, _sleep=lambda s: None)
        self.assertEqual(len(calls), 1)   # ← второй заход дал бы тот же ответ

    def test_non_transient_runtime_error_not_retried(self):
        calls = []

        def bad():
            calls.append(1)
            raise RuntimeError("read_doc не ok")

        with self.assertRaises(RuntimeError):
            cla.with_retry(bad, _sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_transient_classifier(self):
        self.assertTrue(cla.transient(_http(404)))
        self.assertTrue(cla.transient(_http(503)))
        self.assertTrue(cla.transient(urllib.error.URLError("dns")))
        self.assertFalse(cla.transient(_http(401)))
        self.assertFalse(cla.transient(RuntimeError("не ok")))


class TestSpool(unittest.TestCase):
    """Спул: строка переживает сбой на диске и дошлётся следующим успешным вызовом."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = cla.SPOOL_PATH
        cla.SPOOL_PATH = os.path.join(self._tmp.name, "pending.txt")

    def tearDown(self):
        cla.SPOOL_PATH = self._orig
        self._tmp.cleanup()

    def test_line_survives_failure(self):
        cla.spool_add("DONE 2026-07-28 09:22 UTC: первая потерянная")
        cla.spool_add("DONE 2026-07-28 09:25 UTC: вторая потерянная")
        pend = cla.spool_read()
        self.assertEqual(len(pend), 2)                    # ← ничего не потеряно
        self.assertIn("первая потерянная", pend[0])       # порядок: старые сверху
        self.assertIn("вторая потерянная", pend[1])

    def test_multiline_flattened(self):
        cla.spool_add("DONE строка\nс переносом")
        self.assertEqual(len(cla.spool_read()), 1)        # одна строка спула = одна запись

    def test_missing_spool_is_empty_not_error(self):
        self.assertEqual(cla.spool_read(), [])

    def test_clear_after_success(self):
        cla.spool_add("DONE отложенная")
        cla.spool_clear()
        self.assertEqual(cla.spool_read(), [])

    def test_resend_order_newest_first(self):
        """Блок, который уходит в док: новая строка сверху, отложенные — от новых к старым."""
        cla.spool_add("DONE старая")
        cla.spool_add("DONE поновее")
        pending = cla.spool_read()
        block = "  \n".join(["DONE самая новая"] + list(reversed(pending)))
        self.assertEqual(block.split("  \n"),
                         ["DONE самая новая", "DONE поновее", "DONE старая"])


class TestLedger(unittest.TestCase):
    """Реестр УСПЕШНЫХ записей (класс 30.07 «статус врёт»). Спул хранит провалившиеся строки,
    реестр — прошедшие: без него у ПК не было НИ ОДНОГО местного следа «журнал записан», и демон,
    закрывая задачу по таймауту, писал владельцу «провалена» поверх сделанной работы (задача 61:
    коммит a3f75dd и записи в журнал были). Реестр читает pc_orchestrator._journal_writes_between."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "cowork_log.ledger")
        self.addCleanup(self._tmp.cleanup)

    def _lines(self):
        with io.open(self.path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_success_leaves_local_trace(self):
        cla.ledger_add("DONE 2026-07-30 13:14 UTC: гард PEB закрыт, коммит a3f75dd", path=self.path)
        rec = self._lines()[0]
        self.assertIn("a3f75dd", rec["line"])
        self.assertTrue(rec["ts"])                       # без метки времени окно не построить

    def test_timestamp_is_utc_aware(self):
        cla.ledger_add("DONE что-то", path=self.path)
        ts = datetime.datetime.fromisoformat(self._lines()[0]["ts"])
        self.assertIsNotNone(ts.tzinfo)                  # наивное время дало бы окно мимо на 7 часов

    def test_one_record_per_line(self):
        cla.ledger_add("DONE строка\nс переносом", path=self.path)
        with io.open(self.path, encoding="utf-8") as f:
            self.assertEqual(len([x for x in f if x.strip()]), 1)

    def test_capped(self):
        for i in range(12):
            cla.ledger_add("DONE запись %d" % i, path=self.path, keep=5)
        recs = self._lines()
        self.assertEqual(len(recs), 5)
        self.assertIn("запись 11", recs[-1]["line"])     # кап режет СТАРЫЕ, свежие целы

    def test_failure_is_swallowed(self):
        # реестр вспомогательный: запись в мозг к этому моменту УЖЕ прошла — рушить её отчёт нельзя
        self.assertIsNone(cla.ledger_add("DONE x", path=os.path.join(self._tmp.name, "нет", "к", "ф")))

    def test_main_writes_ledger_on_success(self):
        # сквозь main(): успешная запись оставляет след и по НОВОЙ строке, и по досланным из спула
        spool = os.path.join(self._tmp.name, "pending.txt")
        saved = (cla.SPOOL_PATH, cla.LEDGER_PATH, sys.argv)
        cla.SPOOL_PATH, cla.LEDGER_PATH = spool, self.path
        self.addCleanup(lambda: setattr(cla, "SPOOL_PATH", saved[0]))
        self.addCleanup(lambda: setattr(cla, "LEDGER_PATH", saved[1]))
        self.addCleanup(lambda: setattr(sys, "argv", saved[2]))
        cla.spool_add("DONE 2026-07-30 12:00 UTC: отложенная прошлым сбоем")
        sys.argv = ["cowork_log_append.py", "DONE итог задачи 61"]
        doc = {"text": "старый журнал"}   # живой мост: write_doc меняет док, read_doc отдаёт новый

        def fake_post(u, p):
            doc["text"] = p["text"]
            return {"ok": True, "chars": 99}

        with mock.patch.object(cla, "load_env", lambda p: {"BRIDGE_URL": "https://x/exec", "BRIDGE_TOKEN": "t"}), \
             mock.patch.object(cla, "get", lambda u, p: {"ok": True, "text": doc["text"]}), \
             mock.patch.object(cla, "post", fake_post), \
             contextlib.redirect_stdout(io.StringIO()):
            cla.main()
        lines = [r["line"] for r in self._lines()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("итог задачи 61" in x for x in lines))
        self.assertTrue(any("отложенная прошлым сбоем" in x for x in lines))


class TestStampContract(unittest.TestCase):
    """КОНТРАКТ строки журнала: «<ТИП> <ГГГГ-ММ-ДД ЧЧ:ММ UTC>: <текст>» — на ВСЕХ трёх путях записи.

    До 28.07 дату получали только строки БЕЗ типа: `msg.startswith(("DONE","NOTE","ASK"))` отдавал
    строку как есть. Живой замер: серверный сплиттер видел 80 записей из 1747 — резать журнал было
    не по чему. Три пути: оркестратор (pc_orchestrator._cowork), хук SessionEnd
    (dispatch_notify._cowork), ручная строка (CLI/stdin).
    """

    STAMP = "2026-07-28 21:05 UTC"
    RE_CONTRACT = re.compile(r"^(DONE|NOTE|ASK|PLAN|BLOCKED|WAITING|SKIPPED) "
                             r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC: \S")

    def _contract(self, line):
        self.assertRegex(line, self.RE_CONTRACT, "не по контракту: %r" % line)

    def test_path_1_orchestrator(self):
        """pc_orchestrator._cowork шлёт «NOTE Orchestrator: …» — раньше уходило БЕЗ даты вообще."""
        line = cla.stamp_line("NOTE Orchestrator: авто-фетч: рабочая копия грязная", self.STAMP)
        self._contract(line)
        self.assertEqual(line, "NOTE 2026-07-28 21:05 UTC: Orchestrator: авто-фетч: рабочая копия грязная")

    def test_path_2_hook_done(self):
        """Хук SessionEnd шлёт готовую строку сессии — тип есть, даты в формате контракта нет."""
        line = cla.stamp_line("DONE Dispatch 28.07 18:35: класс 17.07 закрыт", self.STAMP)
        self._contract(line)
        self.assertTrue(line.startswith("DONE 2026-07-28 21:05 UTC: Dispatch 28.07 18:35:"), line)

    def test_path_2_hook_ask(self):
        """ASK — тот же контракт: развилка обязана быть видна сплиттеру наравне с DONE."""
        line = cla.stamp_line("ASK Dispatch: нужен выбор владельца", self.STAMP)
        self._contract(line)
        # съедается ТОЛЬКО тип: «Dispatch:» — уже тело записи, автора не теряем
        self.assertEqual(line, "ASK 2026-07-28 21:05 UTC: Dispatch: нужен выбор владельца")

    def test_path_3_manual_plain_text(self):
        """Ручная строка без типа — итог сессии, тип DONE (прежнее поведение сохранено)."""
        line = cla.stamp_line("проверил состояние userbot-репо", self.STAMP)
        self._contract(line)
        self.assertEqual(line, "DONE 2026-07-28 21:05 UTC: проверил состояние userbot-репо")

    def test_every_type_gets_the_stamp(self):
        for t in cla.LOG_TYPES:
            self._contract(cla.stamp_line(t + " тело записи", self.STAMP))

    def test_idempotent_on_already_stamped(self):
        """Спул дошлёт ту же строку вторым вызовом — второй штамп ставить нельзя."""
        once = cla.stamp_line("NOTE Orchestrator: строка", self.STAMP)
        twice = cla.stamp_line(once, "2099-01-01 00:00 UTC")
        self.assertEqual(twice, once)

    def test_type_matched_by_word_boundary(self):
        """«DONEC …» — не тип: без границы слова любая строка на DONE… ломала бы контракт."""
        line = cla.stamp_line("DONEC срочная заметка", self.STAMP)
        self.assertTrue(line.startswith("DONE 2026-07-28 21:05 UTC: DONEC"), line)

    def test_stamp_format_matches_writer(self):
        """Формат штампа — один и тот же у контракта и у main() (STAMP_FMT)."""
        import datetime
        s = datetime.datetime(2026, 7, 28, 21, 5, tzinfo=datetime.timezone.utc).strftime(cla.STAMP_FMT)
        self.assertEqual(s, self.STAMP)


class TestShrinkGuard(unittest.TestCase):
    """Класс 17.07: писатель НЕ смеет затереть журнал — и НЕ смеет потерять строку.

    Инцидент-образец: мост отвечает {"ok":true,"text":""} и на пустой док, и на сорванное
    чтение; прежний гард ловил только None, "" проходил насквозь — и 758 575 символов
    заменялись одной строкой. Сети здесь нет: транспорт и конфиг подменяются на уровне
    модуля, ответ read_doc копирует ЖИВОЙ формат Bridge (правило-класс CLAUDE.md).
    """

    LINE = "DONE 2026-07-28 20:00 UTC: строка для гарда"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = (cla.SPOOL_PATH, cla.get, cla.post, cla.load_env, cla.compose, sys.argv)
        cla.SPOOL_PATH = os.path.join(self._tmp.name, "pending.txt")
        cla.load_env = lambda p: {"BRIDGE_URL": "https://bridge.test/exec", "BRIDGE_TOKEN": "TOK"}
        self.writes = []

        def fake_post(url, payload):
            self.writes.append(dict(payload))
            self.doc = payload["text"]      # живой мост: запись меняет док
            return {"ok": True, "chars": len(payload["text"])}

        cla.post = fake_post
        sys.argv = ["cowork_log_append.py", self.LINE]

    def tearDown(self):
        (cla.SPOOL_PATH, cla.get, cla.post, cla.load_env, cla.compose, sys.argv) = self._saved

    def _bridge_returns(self, text):
        # Док ЖИВОЙ: read_doc отдаёт то, что лежит сейчас, а не снимок момента настройки —
        # иначе обратное чтение писателя никогда не увидит только что записанную строку.
        self.doc = text
        cla.get = lambda url, params: {"ok": True, "name": "cowork_log", "id": "FID1",
                                       "text": self.doc}

    def _run_main(self):
        """main() с перехватом обоих потоков → (SystemExit | None, текст stderr)."""
        err, out = io.StringIO(), io.StringIO()
        exc = None
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                cla.main()
            except SystemExit as e:
                exc = e
        return exc, err.getvalue()

    def test_empty_doc_is_read_failure_no_write_line_spooled(self):
        self._bridge_returns("")
        exc, err = self._run_main()
        self.assertEqual(exc.code, 1)                       # отказ, а не «записал»
        self.assertEqual(self.writes, [])                   # ← ЗАПИСИ НЕ БЫЛО
        self.assertEqual(cla.spool_read(), [self.LINE])     # ← строка НЕ потеряна
        self.assertIn("ГАРД УСЫХАНИЯ", err)
        self.assertIn("в доке 0 символов", err)             # обе длины в stderr

    def test_whitespace_only_doc_is_also_read_failure(self):
        self._bridge_returns("   \n\n  ")
        exc, _ = self._run_main()
        self.assertEqual(exc.code, 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(cla.spool_read(), [self.LINE])

    def test_shorter_result_blocks_write_and_spools(self):
        self._bridge_returns("A" * 5000)
        cla.compose = lambda new_line, pending, old: ("коротышка", 0)   # аномальная сборка
        exc, err = self._run_main()
        self.assertEqual(exc.code, 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(cla.spool_read(), [self.LINE])
        self.assertIn("было 5000 символов, стало бы 9", err)       # ОБЕ длины дословно

    def test_normal_case_writes_and_grows(self):
        self._bridge_returns("A" * 5000)
        exc, _ = self._run_main()
        self.assertIsNone(exc)                              # успех: sys.exit не звали
        self.assertEqual(len(self.writes), 1)
        sent = self.writes[0]["text"]
        self.assertGreater(len(sent), 5000)                 # длина выросла
        self.assertTrue(sent.startswith(self.LINE))         # новейшее сверху
        self.assertTrue(sent.endswith("A" * 5000))          # прежний текст цел
        self.assertEqual(cla.spool_read(), [])              # спул вычищен

    def test_spooled_line_already_in_doc_is_not_written_twice(self):
        """КЛАСС «ЛОЖНЫЙ 401» (живой случай 01.08.2026). Мост рапортует отказ ПОСЛЕ того, как
        строка легла в док; она уходит в спул — и досылка дописывала её ВТОРОЙ раз. Дубль в
        журнале хуже пропуска: журнал — индекс, по нему считают и ищут."""
        landed = "DONE 2026-08-01 08:04 UTC: ARTIFACT тема → docs/artifacts/x.md: суть"
        cla.spool_add(landed)
        self._bridge_returns(landed + "  \n" + "A" * 5000)   # он УЖЕ в доке
        exc, _ = self._run_main()
        self.assertIsNone(exc)
        sent = self.writes[0]["text"]
        self.assertEqual(sent.count(landed), 1)             # был один раз — один и остался
        self.assertTrue(sent.startswith(self.LINE))         # новая строка всё равно легла
        self.assertEqual(cla.spool_read(), [])

    def test_spooled_line_absent_from_doc_is_still_delivered(self):
        """Обратная половина: дедуп не смеет съесть строку, которой в доке НЕТ."""
        lost = "NOTE 2026-08-01 07:00 UTC: строка, до дока НЕ дошедшая"
        cla.spool_add(lost)
        self._bridge_returns("A" * 5000)
        exc, _ = self._run_main()
        self.assertIsNone(exc)
        sent = self.writes[0]["text"]
        self.assertIn(lost, sent)
        self.assertEqual(cla.spool_read(), [])

    def test_dedup_matches_whole_lines_only(self):
        """Совпадение — ЦЕЛЬНОЙ строкой. Короткая запись, оказавшаяся подстрокой чужой длинной,
        дублем не считается — иначе досылка молча теряла бы её."""
        short = "DONE 2026-08-01 07:30 UTC: коротко"
        cla.spool_add(short)
        self._bridge_returns(short + " и ещё хвост чужой строки\n" + "A" * 5000)
        exc, _ = self._run_main()
        self.assertIsNone(exc)
        self.assertIn(short + "  \n", self.writes[0]["text"])   # дослана отдельной строкой

    def test_trailing_spaces_do_not_hide_a_duplicate(self):
        """Разделитель склейки — два пробела перед переводом строки. Дубль обязан опознаваться
        и с ними: иначе дедуп не сработал бы ровно на том, что пишет сам этот модуль."""
        landed = "DONE 2026-08-01 08:04 UTC: строка с хвостом"
        cla.spool_add(landed)
        self._bridge_returns(landed + "  \n" + "A" * 5000)
        exc, _ = self._run_main()
        self.assertIsNone(exc)
        self.assertEqual(self.writes[0]["text"].count(landed), 1)

    def test_multiline_message_becomes_one_record(self):
        """ОДНА запись = ОДНА строка: многострочный result не смеет рвать разбор по заголовкам."""
        self._bridge_returns("A" * 5000)
        sys.argv = ["cowork_log_append.py",
                    "NOTE Orchestrator: задача #7 → done\nвторая строка\nтретья"]
        exc, _ = self._run_main()
        self.assertIsNone(exc)
        head = self.writes[0]["text"].split("  \n")[0]
        self.assertNotIn("\n", head)                       # переносы схлопнуты
        self.assertIn("вторая строка", head)               # текст не потерян
        self.assertIn("третья", head)
        self.assertTrue(head.startswith("NOTE 2"), head)   # и запись по контракту

    def test_pending_lines_survive_guard_trip(self):
        """Гард не смеет съесть и ОТЛОЖЕННЫЕ: они остаются в спуле вместе с новой."""
        cla.spool_add("DONE прошлая отложенная")
        self._bridge_returns("")
        self._run_main()
        self.assertEqual(cla.spool_read(), ["DONE прошлая отложенная", self.LINE])


# фикстура снята с ЖИВОГО журнала (снимок 31.07, запись-медиана длинного хвоста, 1616 символов):
# правило-класс CLAUDE.md — голдены пишем на дословных живых строках, а не на «как удобно тесту».
LIVE_LONG = (
    "NOTE 2026-07-29 18:25 UTC: Orchestrator: родитель #44 (pcloc-dec) → done: план 6 шагов, "
    "шаг 1 в очереди · 🧩 Декомпозиция (локальный дирижёр PC): 6 шагов — исполняю ПО ОДНОМУ "
    "(lane=pc, sequential-релиз: следующий шаг встаёт только после done предыдущего). "
    "1. В репо D:\\turbobaby-bot найти, где suggest формирует текст ответа и черновиков и где "
    "доступно окно сообщений клиента: grep по \"suggest\", \"черновик\", \"Здравствуйте\", "
    "\"greeting\". Выяснить, как в окне помечено автоприветствие Telegram Business "
    "(роль/флаг/текст). Итог записать в D:\\turbobaby-bot\\artifacts\\greeting-guard-notes.md: "
    "файлы, функции, формат окна. Проверка: файл создан, пути в нём существуют. "
    "2. По собранным заметкам добавить в suggest признак автоприветствия и накрыть его юнитом; "
    "гейт зелёный, коммит с хешем в итоге шага, откат при любом красном."
)


class TestSpillThreshold(unittest.TestCase):
    """ЖУРНАЛ — ИНДЕКС, А НЕ ХРАНИЛИЩЕ ТЕЛ (31.07.2026).

    Полные отчёты уезжали в cowork_log целиком: по замеру живого журнала 21.8% записей держат
    74.5% его объёма. Теперь строка длиннее LINE_MAX кладёт ТЕЛО в файл, а в мозг отдаёт итог
    и путь. Здесь проверяется ровно это — и то, что защита от роста не стала потерей записи."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.join(self._tmp.name, "journal")

    def _spill(self, line, body=None, **kw):
        return cla.spill(line, body, spill_dir=self.dir, **kw)

    # --- порог: обоснован ЗАМЕРОМ, а не вкусом ---

    def test_threshold_is_above_longest_compliant_line(self):
        """Замер 31.07 по 81 живой ARTIFACT-записи (уже правильный вид «итог + путь»):
        p50=177, p90=218, МАКСИМУМ 588. Порог обязан стоять ВЫШЕ максимума — иначе он резал бы
        записи, которые формат уже соблюдают, и правило спорило бы само с собой."""
        self.assertGreater(cla.LINE_MAX, 588)
        # …и не улетать в бесконечность: замер даёт p90 длинного хвоста 1362 — порог выше него
        # перестал бы что-либо ловить (при 1500 экономия падает с 68.6% до 50.3%).
        self.assertLess(cla.LINE_MAX, 1000)

    def test_short_line_untouched_and_no_file_created(self):
        line = "DONE 2026-07-31 09:00 UTC: ARTIFACT порог журнала → docs/artifacts/x.md: готово"
        got, path = self._spill(line)
        self.assertEqual(got, line)
        self.assertIsNone(path)
        self.assertFalse(os.path.exists(self.dir))     # пустых папок за собой не оставляем

    def test_boundary_exactly_at_threshold_is_untouched(self):
        line = "DONE 2026-07-31 09:00 UTC: " + "я" * (cla.LINE_MAX - 27)
        self.assertEqual(len(line), cla.LINE_MAX)
        self.assertEqual(self._spill(line), (line, None))

    # --- вынос тела ---

    def test_long_line_becomes_summary_plus_path(self):
        got, rel = self._spill(LIVE_LONG)
        self.assertLessEqual(len(got), cla.LINE_MAX)          # в журнал ушёл ИТОГ
        self.assertTrue(got.startswith("NOTE 2026-07-29 18:25 UTC:"), got)  # контракт цел
        self.assertIn(rel, got)                               # …и ССЫЛКА на тело
        self.assertIn("полный текст %d симв." % len(LIVE_LONG), got)
        self.assertTrue(rel.startswith("docs/artifacts/journal/"), rel)

    def _body(self, rel):
        with open(os.path.join(self.dir, os.path.basename(rel)), encoding="utf-8") as f:
            return f.read()

    def test_body_file_holds_the_whole_record(self):
        got, rel = self._spill(LIVE_LONG)
        body = self._body(rel)
        self.assertIn(LIVE_LONG, body)                        # тело ЦЕЛИКОМ, а не обрезок
        self.assertIn(str(len(LIVE_LONG)), body)              # и длина названа числом
        self.assertNotIn(got[:60] + "…", body.split("---")[0])

    def test_original_newlines_survive_in_the_file(self):
        """В журнал уходит одна строка, а в файл — ИСХОДНЫЙ текст: простыня в одну строку
        нечитаема, а сохранить формат стоит ноль."""
        raw = "DONE отчёт\n\n1. первый пункт\n2. второй пункт\n" + "хвост " * 200
        line = " ".join(raw.split())
        _, rel = self._spill(line, raw)
        self.assertIn("1. первый пункт\n2. второй пункт", self._body(rel))

    def test_head_is_cut_on_phrase_boundary_not_mid_word(self):
        got, _ = self._spill(LIVE_LONG)
        head = got.split(" … → ")[0]
        self.assertTrue(LIVE_LONG.startswith(head), head)      # голова — дословное начало записи
        nxt = LIVE_LONG[len(head):len(head) + 1]
        self.assertIn(nxt, (" ", ".", ",", ";", "·", "—", ""), repr(head[-40:]))
        self.assertGreater(len(head), cla.LINE_MAX // 2)       # итог остался итогом

    def test_two_spills_in_the_same_second_get_own_files(self):
        """Писателей двое (демон + SessionEnd-хук) — секунда одна. Коллизия обязана развестись
        суффиксом, а не тихо затереть чужое тело."""
        now = datetime.datetime(2026, 7, 31, 9, 0, 0, tzinfo=datetime.timezone.utc)
        a = self._spill(LIVE_LONG, now=now)[1]
        b = self._spill(LIVE_LONG.replace("#44", "#45"), now=now)[1]
        self.assertNotEqual(a, b)
        self.assertEqual(len(os.listdir(self.dir)), 2)

    def test_type_of_record_lands_in_the_file_name(self):
        for line, mark in ((LIVE_LONG, "-note"), (LIVE_LONG.replace("NOTE", "ASK ", 1), "-ask")):
            _, rel = self._spill(line)
            self.assertIn(mark, rel, rel)

    # --- FAIL-SAFE: защита от роста не смеет стать потерей ---

    def test_unwritable_target_keeps_the_full_line(self):
        """Файл не записался → строка уходит в журнал КАК БЫЛА. Длинная запись хуже короткой,
        но потерянная хуже обеих (тот же принцип, что у гарда усыхания)."""
        with mock.patch.object(cla.os, "makedirs", side_effect=OSError("нет доступа")):
            got, rel = self._spill(LIVE_LONG)
        self.assertEqual(got, LIVE_LONG)
        self.assertIsNone(rel)

    def test_open_failure_also_keeps_the_full_line(self):
        with mock.patch("builtins.open", side_effect=OSError("диск полон")):
            got, rel = self._spill(LIVE_LONG)
        self.assertEqual(got, LIVE_LONG)
        self.assertIsNone(rel)

    # --- контракт строки после выноса ---

    def test_short_line_is_still_stamped_and_idempotent(self):
        """Итог обязан оставаться записью по контракту: тип+дата на месте, второй штамп не лепится
        (иначе досылка из спула родила бы «DONE <дата> UTC: DONE <дата> UTC: …»)."""
        got, _ = self._spill(LIVE_LONG)
        self.assertTrue(cla._RE_STAMPED.match(got), got)
        self.assertEqual(cla.stamp_line(got, "2026-07-31 10:00 UTC"), got)


class TestSpillInMain(unittest.TestCase):
    """Тот же вынос, но по ЖИВОМУ пути запуска: argv → mост / спул."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = (cla.SPOOL_PATH, cla.LEDGER_PATH, cla.SPILL_DIR,
                       cla.get, cla.post, cla.load_env, sys.argv)
        cla.SPOOL_PATH = os.path.join(self._tmp.name, "pending.txt")
        cla.LEDGER_PATH = os.path.join(self._tmp.name, "ledger.jsonl")
        cla.SPILL_DIR = os.path.join(self._tmp.name, "journal")
        cla.load_env = lambda p: {"BRIDGE_URL": "https://bridge.test/exec", "BRIDGE_TOKEN": "TOK"}
        # ЖИВОЕ поведение моста: успешный write_doc МЕНЯЕТ документ, и следующий read_doc
        # (обратное чтение писателя) отдаёт УЖЕ НОВЫЙ текст. Неизменный док — мок, который не
        # умеет представить «прочитать назад после записи»: он прятал бы регресс (правило-класс
        # CLAUDE.md — формат и ПОВЕДЕНИЕ мока равны источнику).
        self.doc = "A" * 5000
        cla.get = lambda url, params: {"ok": True, "name": "cowork_log", "text": self.doc}
        self.writes = []

        def fake_post(url, payload):
            self.writes.append(dict(payload))
            self.doc = payload["text"]
            return {"ok": True, "chars": len(payload["text"])}

        cla.post = fake_post
        sys.argv = ["cowork_log_append.py", LIVE_LONG]

    def tearDown(self):
        (cla.SPOOL_PATH, cla.LEDGER_PATH, cla.SPILL_DIR,
         cla.get, cla.post, cla.load_env, sys.argv) = self._saved

    def _run(self):
        err, out = io.StringIO(), io.StringIO()
        exc = None
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                cla.main()
            except SystemExit as e:
                exc = e
        return exc, out.getvalue(), err.getvalue()

    def test_bridge_receives_summary_not_the_body(self):
        exc, out, _ = self._run()
        self.assertIsNone(exc)
        head = self.writes[0]["text"].split("  \n")[0]
        self.assertLessEqual(len(head), cla.LINE_MAX)
        self.assertIn("docs/artifacts/journal/", head)
        self.assertNotIn("greeting-guard-notes", head)         # тело в мозг НЕ уехало
        self.assertIn("тело вынесено в", out)                  # …и об этом сказано в отчёте
        files = os.listdir(cla.SPILL_DIR)
        self.assertEqual(len(files), 1)
        with open(os.path.join(cla.SPILL_DIR, files[0]), encoding="utf-8") as f:
            self.assertIn("greeting-guard-notes", f.read())

    def test_existing_records_are_untouched(self):
        """Правило владельца: старые записи НЕ трогаем. Склейка обязана только ДОБАВЛЯТЬ."""
        self._run()
        self.assertTrue(self.writes[0]["text"].endswith("A" * 5000))

    def test_failure_spools_the_short_line_not_the_sheet(self):
        """Мост упал → в спул ложится ИТОГ, а не простыня: иначе отложенная запись доехала бы
        до журнала следующим успешным вызовом и обошла порог сама."""
        cla.get = lambda url, params: (_ for _ in ()).throw(_http(500))
        exc, _, err = self._run()
        self.assertEqual(exc.code, 1)
        spooled = cla.spool_read()
        self.assertEqual(len(spooled), 1)
        self.assertLessEqual(len(spooled[0]), cla.LINE_MAX)
        self.assertIn("docs/artifacts/journal/", spooled[0])
        self.assertIn("Тело записи УЖЕ сохранено", err)        # тело не потеряно и названо

    def test_short_line_leaves_no_files_behind(self):
        sys.argv = ["cowork_log_append.py", "DONE Dispatch 31.07: короткий итог, всё зелено"]
        exc, out, _ = self._run()
        self.assertIsNone(exc)
        self.assertFalse(os.path.exists(cla.SPILL_DIR))
        self.assertNotIn("тело вынесено", out)


class TestStdinEncoding(unittest.TestCase):
    """29.07.2026: кириллица из пайпа ложилась в мозг мохибейком.

    Живой случай: `printf '…тип ТС…' | python cowork_log_append.py` на Windows дал в доке
    «С‚РёРї РўРЎ» — sys.stdin декодировал UTF-8-байты кодировкой консоли (cp1251). Пять строк
    подряд, среди них два ARTIFACT и два DONE. Ни один гард этого не видит: длина растёт, текст
    непустой, обратное чтение совпадает с тем, что записали. Ловится только глазами.
    """

    class _Stream:
        def __init__(self, raw):
            self.buffer = io.BytesIO(raw)

    def test_utf8_pipe_is_not_mangled(self):
        text = "DONE тип ТС: убраны ложные вердикты «авто» — переход 45→1"
        got = cla.read_stdin_text(self._Stream(text.encode("utf-8")))
        self.assertEqual(got, text)
        # ровно тот мохибейк, который лёг в живой журнал
        self.assertNotIn("С‚РёРї", got)
        self.assertNotIn("Р ", got)

    def test_cp1251_pipe_still_readable(self):
        """Родная консоль Windows шлёт cp1251 — её тоже понимаем, а не портим."""
        text = "DONE строка из консоли"
        self.assertEqual(cla.read_stdin_text(self._Stream(text.encode("cp1251"))), text)

    def test_broken_bytes_do_not_empty_the_line(self):
        """Битые байты не должны превратиться в пустую строку: пустая = отказ записи."""
        got = cla.read_stdin_text(self._Stream(b"DONE \xff\xfe\x00 hvost"))
        self.assertTrue(got.strip())
        self.assertIn("DONE", got)

    def test_text_stream_without_buffer_works(self):
        """Подменённый в тестах StringIO (без .buffer) читаем как есть."""
        self.assertEqual(cla.read_stdin_text(io.StringIO("DONE уже текст")), "DONE уже текст")


class TestArgvStdinSentinel(unittest.TestCase):
    """«-» в argv — СЕНТИНЕЛ stdin, а не текст записи.

    Живой брак 01.08.2026: вызов `cowork_log_append.py - <<EOF …EOF` (привычка от brain_writer,
    где «-» сентинел именно так и работает) уложил в мозг литерал «-» ДВАЖДЫ, а тело heredoc
    выбросил молча. Ни один гард этого не видит: строка непустая, длина растёт, обратное чтение
    совпадает с записанным — ровно та же слепая зона, что у мохибейка stdin.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = (cla.SPOOL_PATH, cla.get, cla.post, cla.load_env,
                       cla.read_stdin_text, sys.argv)
        self.addCleanup(self._restore)
        cla.SPOOL_PATH = os.path.join(self._tmp.name, "pending.txt")
        cla.load_env = lambda p: {"BRIDGE_URL": "https://bridge.test/exec", "BRIDGE_TOKEN": "TOK"}
        # Мост ведёт себя как живой: запись меняет док, обратное чтение видит новый текст.
        self.doc = "DONE старый журнал целиком"
        cla.get = lambda url, params: {"ok": True, "name": "cowork_log", "id": "FID1",
                                       "text": self.doc}
        self.writes = []

        def fake_post(url, payload):
            self.writes.append(dict(payload))
            self.doc = payload["text"]
            return {"ok": True, "chars": len(payload["text"])}

        cla.post = fake_post

    def _restore(self):
        (cla.SPOOL_PATH, cla.get, cla.post, cla.load_env,
         cla.read_stdin_text, sys.argv) = self._saved

    def _run(self, argv, stdin_text):
        cla.read_stdin_text = lambda stream=None: stdin_text
        sys.argv = ["cowork_log_append.py"] + argv
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            try:
                cla.main()
            except SystemExit:
                pass
        return self.writes[0]["text"].split("\n")[0] if self.writes else ""

    def test_dash_argv_reads_stdin_not_literal(self):
        first = self._run(["-"], "DONE Dispatch 05:25: итог из heredoc")
        self.assertIn("итог из heredoc", first)
        self.assertNotIn("UTC: -", first)          # ← литерал «-» в мозг не лёг

    def test_no_argv_still_reads_stdin(self):
        self.assertIn("строка из пайпа", self._run([], "NOTE строка из пайпа"))

    def test_real_argv_text_wins_over_stdin(self):
        """Обычный argv-вызов stdin не трогает: detached-спавн его попросту не имеет."""
        first = self._run(["DONE строка из argv"], "СТДИН ЧИТАТЬ НЕ ДОЛЖНЫ")
        self.assertIn("строка из argv", first)
        self.assertNotIn("ЧИТАТЬ НЕ ДОЛЖНЫ", first)


class TestReceiptIsNotTheFact(unittest.TestCase):
    """ОТВЕТ МОСТА ≠ СУДЬБА ЗАПИСИ — второй писатель того же класса.

    Живой случай 01.08.2026: `cowork_log_append.py` получил HTTP 404 и заспулил строку с
    рапортом «ОТЛОЖЕНО, уйдёт следующим вызовом», хотя судить о том, легла запись или нет, по
    коду ответа НЕЛЬЗЯ (writeDoc_ фиксирует мутацию ДО сборки {ok:true}; тело едет вторым
    плечом). Цена ошибки — ручной повтор: у него СВОЙ штамп времени, дословный дедуп досылки
    его не поймает, и в журнал ляжет дубль.

    Регресс ровно на развилку: легло → успех; не легло → ошибка; неизвестно → так и сказано.
    Сети нет, транспорт инъектируется; ответы копируют ЖИВОЙ формат Bridge (правило-класс)."""

    MSG = "DONE итог задачи про расписку"
    OLD = "DONE 2026-07-31 10:00 UTC: прежняя строка журнала"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._saved = (cla.SPOOL_PATH, cla.LEDGER_PATH, cla.get, cla.post, cla.load_env, sys.argv)
        cla.SPOOL_PATH = os.path.join(self._tmp.name, "pending.txt")
        cla.LEDGER_PATH = os.path.join(self._tmp.name, "ledger.jsonl")
        cla.load_env = lambda p: {"BRIDGE_URL": "https://bridge.test/exec", "BRIDGE_TOKEN": "TOK"}
        sys.argv = ["cowork_log_append.py", self.MSG]
        self.doc = self.OLD          # «живой» док; фейковый write_doc в него и пишет
        self.reads = 0

    def tearDown(self):
        (cla.SPOOL_PATH, cla.LEDGER_PATH, cla.get, cla.post, cla.load_env, sys.argv) = self._saved

    def _wire(self, landed, read_back_ok=True, receipt=None):
        """landed — легла ли запись НА САМОМ ДЕЛЕ; receipt — что вернёт/бросит write_doc."""
        def fake_get(url, params):
            self.reads += 1
            if self.reads > 1 and not read_back_ok:   # обратное чтение сорвалось
                raise _http(404)
            return {"ok": True, "name": "cowork_log", "id": "FID1", "text": self.doc}

        def fake_post(url, payload):
            if landed:                                 # мутация зафиксирована ДО ответа
                self.doc = payload["text"]
            if isinstance(receipt, Exception):
                raise receipt
            return receipt if receipt is not None else {"ok": True, "chars": len(payload["text"])}

        cla.get, cla.post = fake_get, fake_post

    def _run(self):
        err, out = io.StringIO(), io.StringIO()
        exc = None
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                cla.main()
            except SystemExit as e:
                exc = e
        return exc, out.getvalue(), err.getvalue()

    def test_landed_write_with_lost_receipt_is_success(self):
        """404, а строка ЛЕГЛА (живой случай): успех, спул ПУСТ — иначе досылка/повтор дадут дубль."""
        self._wire(landed=True, receipt=_http(404))
        exc, out, err = self._run()
        self.assertIsNone(exc)                       # НЕ отказ: запись состоялась
        self.assertIn("OK: записано в мозг", out)
        self.assertIn("РАСПИСКА ПОТЕРЯНА", out)      # класс назван вслух, а не замолчан
        self.assertEqual(cla.spool_read(), [])       # ← нечего досылать: строка уже в доке

    def test_write_that_never_landed_still_errors(self):
        """Обратная сторона: не легло — честный отказ и строка в спуле (иначе потеря)."""
        self._wire(landed=False, receipt=_http(404))
        exc, out, err = self._run()
        self.assertEqual(exc.code, 1)
        self.assertIn("НЕ ЛЕГЛА", err)
        self.assertIn("повтор безопасен", err)
        self.assertEqual(len(cla.spool_read()), 1)

    def test_unknown_outcome_is_named_unknown_not_failure(self):
        """Расписка — отказ, обратное чтение тоже не удалось: «не знаю», а не «не записано»."""
        self._wire(landed=True, read_back_ok=False, receipt=_http(404))
        exc, out, err = self._run()
        self.assertEqual(exc.code, 1)
        self.assertIn("ИСХОД ЗАПИСИ НЕ ПОДТВЕРЖДЁН", err)
        self.assertIn("СЛЕПОЙ ПОВТОР ВРУЧНУЮ ЗАПРЕЩЁН", err)
        self.assertNotIn("НЕ ЛЕГЛА", err)            # ← так говорить права не имеем
        self.assertEqual(len(cla.spool_read()), 1)   # спул безопасен: досылку дедуп сверит с доком

    def test_bridge_says_ok_but_line_absent_is_caught(self):
        """Зеркало класса: мост ответил ok, а строки в доке нет — молча «успехом» не считаем."""
        self._wire(landed=False, receipt={"ok": True, "chars": 999})
        exc, out, err = self._run()
        self.assertEqual(exc.code, 1)
        self.assertIn("ОБРАТНОЕ ЧТЕНИЕ", err)
        self.assertEqual(len(cla.spool_read()), 1)

    def test_lost_receipt_does_not_duplicate_on_next_call(self):
        """Идемпотентность досылки НЕ ослаблена: после потерянной расписки следующий вызов
        не дублирует строку (её нет в спуле, а дедуп сверяет с живым доком)."""
        self._wire(landed=True, receipt=_http(404))
        self._run()
        first_doc = self.doc
        sys.argv = ["cowork_log_append.py", "DONE следующая строка"]
        self._wire(landed=True)                      # мост снова здоров
        exc, out, err = self._run()
        self.assertIsNone(exc)
        # Ищем ХВОСТ записи: штамп писатель вставляет ПОСЛЕ типа («DONE <дата> UTC: итог…»),
        # поэтому исходная строка целиком подстрокой не является — сверяем по опознавательной части.
        body = "итог задачи про расписку"
        self.assertEqual(self.doc.count(body), 1)    # ← ровно одно вхождение, дубля нет
        self.assertIn(first_doc.splitlines()[0].strip(), self.doc)


if __name__ == "__main__":
    unittest.main()
