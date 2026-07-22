# -*- coding: utf-8 -*-
"""
test_trainer_log.py — ЛОГ ГРУППЫ-ТРЕНАЖЁРА в мозг (KB_trainer_log).

Покрытие (по ТЗ п.9–п.11):
  • формат строки ДОСЛОВНО: «TRN <дата время> | TEST-N | client|bot|btn|lesson | <текст>»;
  • многострочный ответ бота остаётся ОДНОЙ строкой (переводы строк → «⏎»);
  • новая строка ложится СВЕРХУ (свежее первым), старое не затирается;
  • ротация >1 МБ режет ТОЛЬКО по границе строки и уносит САМОЕ СТАРОЕ в архив;
  • архива нет → срез НЕ делаем (историю молча не теряем);
  • канал = Bridge read_doc/write_doc ПО FILE ID (проба 23.07: id поддержан обеими операциями);
  • нет file id → status='no_doc' и НИ ОДНОГО обращения к сети (тренажёр при этом жив);
  • любая ошибка канала → status='error', исключение наружу НЕ летит.

Моки копируют ЖИВОЙ формат ответов Bridge (правило-класс CLAUDE.md): read_doc отдаёт
{"ok":true,"name":…,"id":…,"text":…}, отказ — {"ok":false,"error":"unknown_name",…}.
"""

import os
import datetime
import unittest

os.environ.setdefault("TESTING", "1")

import trainer_log


ENV = {"BRIDGE_URL": "https://bridge.example/exec", "BRIDGE_TOKEN": "T0KEN"}
NOW = datetime.datetime(2026, 7, 23, 1, 5)


class FakeBridge:
    """Мини-Bridge по ЖИВОМУ контракту: доки адресуются по id, ответы — те же поля, что у прода."""

    def __init__(self, docs=None, fail_read=False, fail_write=False):
        self.docs = dict(docs or {})
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.reads, self.writes = [], []

    def get(self, url, params):
        self.reads.append(params.get("id") or params.get("name"))
        if self.fail_read:
            return {"ok": False, "error": "unknown_name",
                    "message": 'Нет имени "KB_trainer_log" в манифесте. См. list_brain.'}
        did = params.get("id")
        if did not in self.docs:
            return {"ok": False, "error": "write_failed", "message": "getFileById"}
        return {"ok": True, "name": None, "id": did, "text": self.docs[did]}

    def post(self, url, payload):
        self.writes.append((payload.get("id"), payload.get("text")))
        if self.fail_write:
            return {"ok": False, "error": "write_failed", "message": "Exception: DriveApp"}
        self.docs[payload["id"]] = payload["text"]
        return {"ok": True, "chars": len(payload["text"])}


class TestLineFormat(unittest.TestCase):
    def test_format_is_exactly_as_specified(self):
        line = trainer_log.format_line(7, "client", "Да, даты с 25ого по 30ое июля", now=NOW)
        self.assertEqual(line, "TRN 2026-07-23 01:05 | TEST-7 | client | "
                               "Да, даты с 25ого по 30ое июля")

    def test_multiline_bot_answer_stays_one_line(self):
        answer = ("[тренажёр | ТЕСТ-10 | правил: 3]\n\n"
                  "Отлично, даты учли — XMAX 300.\n\n"
                  "Бронируем?\n[собрано: модель ✅ даты ✅]")
        line = trainer_log.format_line(10, "bot", answer, now=NOW)
        self.assertEqual(line.count("\n"), 0)                       # ОДНА строка — иначе лог не режется
        self.assertIn("[тренажёр | ТЕСТ-10 | правил: 3] ⏎", line)   # шапка сохранена
        self.assertIn("[собрано: модель ✅ даты ✅]", line)          # служебные теги сохранены
        self.assertTrue(line.startswith("TRN 2026-07-23 01:05 | TEST-10 | bot | "))

    def test_all_four_kinds_and_fallbacks(self):
        for kind in trainer_log.KINDS:
            self.assertIn(f"| {kind} | ", trainer_log.format_line(1, kind, "x", now=NOW))
        self.assertIn("| btn | ", trainer_log.format_line(1, "чужое", "x", now=NOW))   # неизвестный вид не теряем
        self.assertIn("| [пусто]", trainer_log.format_line(1, "bot", "   ", now=NOW))
        self.assertIn("TEST-0", trainer_log.format_line(None, "bot", "x", now=NOW))    # битый N не роняет


class TestAppend(unittest.TestCase):
    DID = "1AbcTrainerLogDocId"

    def _append(self, bridge, text="привет", kind="client", n=5, sidecar=None):
        return trainer_log.append(n, kind, text, now=NOW, env=ENV,
                                  get=bridge.get, post=bridge.post,
                                  sidecar_path=sidecar or self._sidecar)

    def setUp(self):
        import tempfile, json
        self._dir = tempfile.TemporaryDirectory()
        self._sidecar = os.path.join(self._dir.name, "trainer_log_doc.json")
        with open(self._sidecar, "w", encoding="utf-8") as f:
            json.dump({"doc_id": self.DID}, f)
        self._old_env = os.environ.pop("TRAINER_LOG_DOC_ID", None)

    def tearDown(self):
        self._dir.cleanup()
        if self._old_env is not None:
            os.environ["TRAINER_LOG_DOC_ID"] = self._old_env

    def test_new_line_goes_on_top_and_nothing_is_lost(self):
        b = FakeBridge({self.DID: "TRN 2026-07-23 01:00 | TEST-5 | btn | старое событие"})
        res = self._append(b, "Да, даты с 25ого по 30ое июля")
        self.assertEqual(res["status"], "ok")
        text = b.docs[self.DID]
        self.assertTrue(text.startswith("TRN 2026-07-23 01:05 | TEST-5 | client | Да, даты"))
        self.assertIn("старое событие", text)                       # прошлое не затёрто
        self.assertEqual(b.writes[0][0], self.DID)                  # писали ПО ID, а не по имени
        self.assertEqual(b.reads, [self.DID])

    def test_no_doc_id_means_no_network_and_honest_status(self):
        import json
        with open(self._sidecar, "w", encoding="utf-8") as f:
            json.dump({}, f)
        b = FakeBridge({})
        res = self._append(b)
        self.assertEqual(res["status"], "no_doc")                   # Штаб ещё не завёл док
        self.assertEqual(b.reads, [])
        self.assertEqual(b.writes, [])                              # в сеть НЕ ходили вовсе
        self.assertIn("TRN ", res["line"])                          # строка всё равно собрана

    def test_channel_failure_is_error_not_exception(self):
        self.assertEqual(self._append(FakeBridge({}, fail_read=True))["status"], "error")
        self.assertEqual(self._append(FakeBridge({self.DID: ""}, fail_write=True))["status"],
                         "error")
        # safe_append не бросает даже при полностью битом окружении
        self.assertIn(trainer_log.safe_append(1, "bot", "x", env={}, sidecar_path=self._sidecar),
                      ("no_doc", "error"))

    def test_env_var_wins_over_sidecar(self):
        os.environ["TRAINER_LOG_DOC_ID"] = "ENV_ID"
        try:
            self.assertEqual(trainer_log.doc_id(self._sidecar), "ENV_ID")
        finally:
            os.environ.pop("TRAINER_LOG_DOC_ID")
        self.assertEqual(trainer_log.doc_id(self._sidecar), self.DID)
        self.assertEqual(trainer_log.doc_id(os.path.join(self._dir.name, "нет.json")), "")


class TestRotation(unittest.TestCase):
    def test_under_threshold_is_untouched(self):
        text = "a\nb\nc"
        self.assertEqual(trainer_log.rotate(text, max_chars=100, keep_chars=50), (text, ""))

    def test_cuts_only_on_line_boundary_oldest_goes_to_archive(self):
        lines = [f"TRN строка {i}" for i in range(10)]              # свежие сверху
        text = "\n".join(lines)
        fresh, spill = trainer_log.rotate(text, max_chars=50, keep_chars=40)
        self.assertTrue(fresh.startswith("TRN строка 0"))           # свежее осталось
        self.assertTrue(spill.endswith("TRN строка 9"))             # самое старое ушло в архив
        self.assertNotIn("\n\n", fresh + spill)
        for part in (fresh, spill):                                 # ни одно событие не разорвано
            for ln in part.splitlines():
                self.assertTrue(ln.startswith("TRN строка "), ln)
        self.assertEqual(len(fresh.splitlines()) + len(spill.splitlines()), 10)

    def test_single_giant_line_is_not_torn(self):
        text = "TRN " + "я" * 500                                   # ни одной границы строки
        self.assertEqual(trainer_log.rotate(text, max_chars=10, keep_chars=5), (text, ""))

    def test_rotation_without_archive_keeps_everything(self):
        did, sid = "DOC", "SID"
        import tempfile, json
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"doc_id": did}, f)                       # archive_id НЕ задан
            old = "\n".join(f"TRN старое {i}" for i in range(50))
            b = FakeBridge({did: old})
            old_max = trainer_log.MAX_CHARS
            trainer_log.MAX_CHARS = 100
            try:
                res = trainer_log.append(1, "btn", "новое", now=NOW, env=ENV,
                                         get=b.get, post=b.post, sidecar_path=p)
            finally:
                trainer_log.MAX_CHARS = old_max
            self.assertEqual(res["status"], "ok")
            self.assertIn("TRN старое 49", b.docs[did])              # хвост НЕ съеден молча
            self.assertIn("новое", b.docs[did])
        self.assertEqual(len(b.writes), 1)                          # в архив не писали (его нет)

    def test_rotation_with_archive_moves_oldest(self):
        did, aid = "DOC", "ARCH"
        import tempfile, json
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"doc_id": did, "archive_id": aid}, f)
            old = "\n".join(f"TRN старое {i}" for i in range(50))
            b = FakeBridge({did: old, aid: "TRN архивное 0"})
            old_max, old_keep = trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS
            trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS = 200, 120
            try:
                res = trainer_log.append(1, "btn", "новое", now=NOW, env=ENV,
                                         get=b.get, post=b.post, sidecar_path=p)
            finally:
                trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS = old_max, old_keep
            self.assertEqual(res["status"], "ok")
            self.assertIn("новое", b.docs[did])
            self.assertIn("TRN старое 49", b.docs[aid])             # самое старое — в архиве
            self.assertIn("TRN архивное 0", b.docs[aid])            # прежний архив не затёрт
            self.assertNotIn("TRN старое 49", b.docs[did])


class TestWiring(unittest.TestCase):
    """Проводка боевых точек вызова (ТЗ п.9: пишем ВСЁ). userbot_listen.py импортить нельзя
    (Telethon + живая сессия), поэтому проверяем ПО ИСХОДНИКУ."""

    def _src(self, name):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), name),
                  encoding="utf-8") as f:
            return f.read()

    def test_userbot_logs_client_bot_and_commands(self):
        src = self._src("userbot_listen.py")
        self.assertIn("import trainer_log", src)
        self.assertIn("trainer_log.KIND_CLIENT", src)               # каждая реплика клиента
        self.assertIn("trainer_log.KIND_BOT", src)                  # каждый ответ бота ЦЕЛИКОМ
        self.assertIn("trainer_log.KIND_LESSON", src)               # уроки (в т.ч. «другое»)
        self.assertIn("trainer_log.KIND_BTN", src)                  # заново / до crm

    def test_moderbot_logs_every_button_and_lesson(self):
        src = self._src("moderation_bot.py")
        self.assertIn("import trainer_log", src)
        for anchor in ("🔄 Заново", "📋 До CRM", "🎓 Обучить", "✍️ другое",
                       "✖ Отмена", "✔ Применить"):
            self.assertIn(anchor, src, f"нажатие не логируется: {anchor}")
        self.assertIn("trainer_log.KIND_LESSON", src)

    def test_auto_apply_map_covers_this_module(self):
        """Правка trainer_log.py обязана рестартить ОБА бота — иначе живые процессы доживут
        на старом коде (класс delivery.py dae330a)."""
        import pc_orchestrator as o
        self.assertEqual(o._procs_for_file("trainer_log.py"), {"userbot", "moderbot"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
