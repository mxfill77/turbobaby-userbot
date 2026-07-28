# -*- coding: utf-8 -*-
"""
test_cowork_log_append.py — писатель строки-итога в мозг: РАЗОВЫЙ сбой моста не должен
терять строку. Инцидент 28.07: `read_doc` вернул 404 (флакость Apps Script: /exec → 302 →
googleusercontent, цель редиректа иногда 404), писатель счёл это фатальным и напечатал
«НЕ ЗАПИСАНО (сохрани вручную)» — строка держалась только в голове человека.

Сети здесь нет: транспорт инъектируется через фейковый fn (как _get у pricing.fleet).
"""

import os
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


if __name__ == "__main__":
    unittest.main()
