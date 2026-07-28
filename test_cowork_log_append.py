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
import contextlib
import tempfile
import unittest
import urllib.error

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
            return {"ok": True, "chars": len(payload["text"])}

        cla.post = fake_post
        sys.argv = ["cowork_log_append.py", self.LINE]

    def tearDown(self):
        (cla.SPOOL_PATH, cla.get, cla.post, cla.load_env, cla.compose, sys.argv) = self._saved

    def _bridge_returns(self, text):
        cla.get = lambda url, params: {"ok": True, "name": "cowork_log", "id": "FID1", "text": text}

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
        cla.compose = lambda new_line, pending, old: "коротышка"   # аномальная сборка
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


if __name__ == "__main__":
    unittest.main()
