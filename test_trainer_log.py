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

    def _append(self, bridge, text="привет", kind="client", n=5, sidecar=None, retries=1):
        # retries=1 и sleep-заглушка: тесты канала не должны спать реальный бэкофф
        return trainer_log.append(n, kind, text, now=NOW, env=ENV,
                                  get=bridge.get, post=bridge.post,
                                  sidecar_path=sidecar or self._sidecar,
                                  lock_path=self._lock, spool_path=self._spool,
                                  retries=retries, sleep=lambda s: None)

    def setUp(self):
        import tempfile, json
        self._dir = tempfile.TemporaryDirectory()
        self._sidecar = os.path.join(self._dir.name, "trainer_log_doc.json")
        self._lock = os.path.join(self._dir.name, "trainer_log.lock")
        self._spool = os.path.join(self._dir.name, "trainer_log.spool")
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
        self.assertFalse(os.path.exists(self._lock))                # лок снят, не висит

    def test_header_stays_first_line(self):
        """Легенда дока, положенная Штабом при создании, обязана остаться ПЕРВОЙ строкой:
        события ложатся под неё, иначе описание формата уезжает вниз и первым же уходит в архив."""
        head = ("TRN LOG v1 | Полный лог тренажёра «Тренеровка» (клиентский контур) | "
                "формат: TRN <дата время> | TEST-N | client|bot|btn|lesson | <текст>")
        b = FakeBridge({self.DID: head})
        self.assertEqual(self._append(b, "первое событие")["status"], "ok")
        self.assertEqual(self._append(b, "второе событие")["status"], "ok")
        lines = b.docs[self.DID].splitlines()
        self.assertEqual(lines[0], head)                            # легенда — всё ещё первая
        self.assertIn("второе событие", lines[1])                   # свежее — сразу под ней
        self.assertIn("первое событие", lines[2])                   # старое — ниже
        self.assertEqual(len(lines), 3)
        # split_header — чистая и обратимая
        self.assertEqual(trainer_log.split_header(head + "\nx\ny"), (head, "x\ny"))
        self.assertEqual(trainer_log.split_header("x\ny"), ("", "x\ny"))
        self.assertEqual(trainer_log.split_header(""), ("", ""))
        self.assertEqual(trainer_log.split_header("TRN LOG v1\r\nx"), ("TRN LOG v1", "x"))

    def test_header_survives_rotation(self):
        import tempfile, json
        head = "TRN LOG v1 | легенда"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"doc_id": "DOC", "archive_id": "ARCH"}, f)
            old = head + "\n" + "\n".join(f"TRN старое {i}" for i in range(60))
            b = FakeBridge({"DOC": old, "ARCH": ""})
            om, ok_ = trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS
            trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS = 200, 120
            try:
                res = trainer_log.append(1, "btn", "новое", now=NOW, env=ENV, get=b.get,
                                         post=b.post, sidecar_path=p,
                                         lock_path=os.path.join(d, "l.lock"),
                                         spool_path=os.path.join(d, "s.spool"))
            finally:
                trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS = om, ok_
            self.assertEqual(res["status"], "ok")
            self.assertTrue(b.docs["DOC"].startswith(head))         # легенда НЕ уехала в архив
            self.assertNotIn(head, b.docs["ARCH"])
            self.assertIn("TRN старое 59", b.docs["ARCH"])           # в архив ушло самое старое

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

    def test_channel_failure_spools_not_raises(self):
        """Канал упал → строка НЕ теряется: уходит в локальный спул (status='spooled'),
        исключение наружу не летит. Спул растёт по мере провалов (хронологически)."""
        self.assertEqual(self._append(FakeBridge({}, fail_read=True))["status"], "spooled")
        self.assertEqual(self._append(FakeBridge({self.DID: ""}, fail_write=True),
                                      "вторая")["status"], "spooled")
        spooled = trainer_log._spool_read(self._spool)
        self.assertEqual(len(spooled), 2)                           # обе недоставленные — в спуле
        self.assertIn("привет", spooled[0])
        self.assertIn("вторая", spooled[1])
        # safe_append не бросает даже при полностью битом окружении
        b = FakeBridge({}, fail_read=True)
        self.assertIn(trainer_log.safe_append(1, "bot", "x", env={}, sidecar_path=self._sidecar,
                                              get=b.get, post=b.post, lock_path=self._lock,
                                              spool_path=self._spool, retries=1,
                                              sleep=lambda s: None),
                      ("no_doc", "spooled", "error"))
        # спул недоступен (каталог не существует) → честный 'error', но НЕ исключение
        bad_spool = os.path.join(self._dir.name, "нет-каталога", "s.spool")
        res = trainer_log.append(1, "bot", "x", now=NOW, env=ENV, sidecar_path=self._sidecar,
                                 get=FakeBridge({}, fail_read=True).get, post=b.post,
                                 lock_path=self._lock, spool_path=bad_spool,
                                 retries=1, sleep=lambda s: None)
        self.assertEqual(res["status"], "error")

    def test_test_run_never_writes_to_the_live_doc(self):
        """ЖИВОЙ ИНЦИДЕНТ 23.07: как только в сайдкар лёг file id, ДВА прогона гейта залили в
        KB_trainer_log 30 строк из фикстур (боевые обработчики дёргаются тестами по-настоящему) и
        растянули гейт с 18с до 106с на сетевых round-trip'ах. Тестовый прогон обязан молчать."""
        self.assertTrue(trainer_log.in_test_context(), "юнит-тест обязан опознаваться как тест")
        calls = []

        def boom_get(*a, **kw):
            calls.append("get"); raise AssertionError("сеть в тестах трогать нельзя")

        def boom_post(*a, **kw):
            calls.append("post"); raise AssertionError("сеть в тестах трогать нельзя")

        old = trainer_log._get, trainer_log._post
        trainer_log._get, trainer_log._post = boom_get, boom_post
        try:
            res = trainer_log.append(7, "btn", "🎓 Обучить", sidecar_path=self._sidecar,
                                     lock_path=self._lock)
            self.assertEqual(res["status"], "test")
            self.assertEqual(trainer_log.safe_append(7, "client", "хочу скутер",
                                                     sidecar_path=self._sidecar,
                                                     lock_path=self._lock), "test")
        finally:
            trainer_log._get, trainer_log._post = old
        self.assertEqual(calls, [], "в тестовом контексте не должно быть НИ ОДНОГО обращения к сети")
        # …но мок-тесты самого модуля (инъекция get/post) продолжают проверять запись как обычно
        b = FakeBridge({self.DID: "TRN LOG v1 | легенда"})
        self.assertEqual(self._append(b, "строка мок-теста")["status"], "ok")
        self.assertIn("строка мок-теста", b.docs[self.DID])

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
                                         get=b.get, post=b.post, sidecar_path=p,
                                         lock_path=os.path.join(d, "l.lock"),
                                         spool_path=os.path.join(d, "s.spool"))
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
                                         get=b.get, post=b.post, sidecar_path=p,
                                         lock_path=os.path.join(d, "l.lock"),
                                         spool_path=os.path.join(d, "s.spool"))
            finally:
                trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS = old_max, old_keep
            self.assertEqual(res["status"], "ok")
            self.assertIn("новое", b.docs[did])
            self.assertIn("TRN старое 49", b.docs[aid])             # самое старое — в архиве
            self.assertIn("TRN архивное 0", b.docs[aid])            # прежний архив не затёрт
            self.assertNotIn("TRN старое 49", b.docs[did])


class TestDeliveryRetryAndSpool(unittest.TestCase):
    """Пакет «полнота лога»: доставка ретраится с бэкоффом, недоставленное копится в локальном
    спуле и дозаписывается при СЛЕДУЮЩЕМ успешном append — событие не теряется НИКОГДА."""

    DID = "1AbcTrainerLogDocId"

    def setUp(self):
        import tempfile, json
        self._dir = tempfile.TemporaryDirectory()
        self.sidecar = os.path.join(self._dir.name, "trainer_log_doc.json")
        self.lock = os.path.join(self._dir.name, "trainer_log.lock")
        self.spool = os.path.join(self._dir.name, "trainer_log.spool")
        with open(self.sidecar, "w", encoding="utf-8") as f:
            json.dump({"doc_id": self.DID}, f)
        self._old_env = os.environ.pop("TRAINER_LOG_DOC_ID", None)

    def tearDown(self):
        self._dir.cleanup()
        if self._old_env is not None:
            os.environ["TRAINER_LOG_DOC_ID"] = self._old_env

    def _append(self, bridge, text, retries=1, sleep=None, get=None):
        return trainer_log.append(3, "client", text, now=NOW, env=ENV,
                                  get=get or bridge.get, post=bridge.post,
                                  sidecar_path=self.sidecar, lock_path=self.lock,
                                  spool_path=self.spool, retries=retries,
                                  sleep=sleep or (lambda s: None))

    def test_retry_with_exponential_backoff_then_success(self):
        """Два срыва чтения подряд → третья попытка доходит; паузы растут ×2 (бэкофф)."""
        b = FakeBridge({self.DID: "TRN старое"})
        fails = {"n": 0}
        delays = []

        def flaky_get(url, params):
            if fails["n"] < 2:
                fails["n"] += 1
                raise OSError("timed out")                          # живой класс: сеть мигнула
            return b.get(url, params)

        res = self._append(b, "дошло с третьей", retries=3, sleep=delays.append, get=flaky_get)
        self.assertEqual(res["status"], "ok")
        self.assertIn("дошло с третьей", b.docs[self.DID])
        self.assertEqual(delays, [trainer_log.BACKOFF_SEC, trainer_log.BACKOFF_SEC * 2])
        self.assertFalse(os.path.exists(self.spool))                 # успех → спул не заводился

    def test_retry_exhausted_goes_to_spool(self):
        """Все попытки сорвались → status='spooled', строка в спуле, попыток ровно retries."""
        b = FakeBridge({}, fail_read=True)
        delays = []
        res = self._append(b, "не дошло", retries=3, sleep=delays.append)
        self.assertEqual(res["status"], "spooled")
        self.assertEqual(len(b.reads), 3)                            # ретраили, не сдались сразу
        self.assertEqual(delays, [trainer_log.BACKOFF_SEC, trainer_log.BACKOFF_SEC * 2])
        self.assertIn("не дошло", "\n".join(trainer_log._spool_read(self.spool)))

    def test_spool_drains_on_next_success_in_order(self):
        """Дозапись: накопленный спул уезжает в док при следующем успехе — свежее сверху,
        спул под новой строкой (хронология сохранена), спул очищен."""
        dead = FakeBridge({}, fail_read=True)
        self.assertEqual(self._append(dead, "первая недоставленная")["status"], "spooled")
        self.assertEqual(self._append(dead, "вторая недоставленная")["status"], "spooled")
        live = FakeBridge({self.DID: "TRN LOG v1 | легенда\nTRN древняя строка"})
        res = self._append(live, "свежая при живом канале")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["spool_delivered"], 2)
        lines = live.docs[self.DID].splitlines()
        self.assertEqual(lines[0], "TRN LOG v1 | легенда")           # легенда осталась первой
        self.assertIn("свежая при живом канале", lines[1])           # новое — сверху
        self.assertIn("вторая недоставленная", lines[2])             # спул — под ним, свежее выше
        self.assertIn("первая недоставленная", lines[3])
        self.assertIn("древняя строка", lines[4])                    # старое тело не затёрто
        self.assertFalse(os.path.exists(self.spool))                 # спул очищен после доставки
        # повторный успех спул заново не дозаписывает (доставлено ровно один раз)
        self.assertEqual(self._append(live, "ещё одна")["spool_delivered"], 0)

    def test_spool_survives_failed_drain(self):
        """Дренаж сорвался на ЗАПИСИ: старый спул цел, новая строка дописана в его хвост."""
        dead = FakeBridge({}, fail_read=True)
        self._append(dead, "старая недоставленная")
        half = FakeBridge({self.DID: "TRN тело"}, fail_write=True)   # читается, но не пишется
        self.assertEqual(self._append(half, "новая при полуживом")["status"], "spooled")
        spooled = trainer_log._spool_read(self.spool)
        self.assertEqual(len(spooled), 2)
        self.assertIn("старая недоставленная", spooled[0])           # хронология не сломана
        self.assertIn("новая при полуживом", spooled[1])


class TestLiveSidecar(unittest.TestCase):
    """Сайдкар РЕПОЗИТОРИЯ (trainer_log_doc.json): оба file id заведены Штабом, ротация с этими
    id работает сквозняком (доказательство «архив подключён», а не только выглядит подключённым)."""

    ARCHIVE_ID = "1lOco1SI58UNT0a4-TBuU3yYeZzbPkWUP"

    def setUp(self):
        self._env = {k: os.environ.pop(k, None)
                     for k in ("TRAINER_LOG_DOC_ID", "TRAINER_LOG_ARCHIVE_DOC_ID")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_repo_sidecar_has_both_ids(self):
        self.assertTrue(trainer_log.doc_id(), "doc_id пропал из сайдкара репо")
        self.assertEqual(trainer_log.archive_id(), self.ARCHIVE_ID,
                         "archive_id архива KB_trainer_log (док Штаба) обязан лежать в сайдкаре")

    def test_rotation_goes_into_the_real_archive_id(self):
        """Сквозная ротация ровно с БОЕВЫМ сайдкаром: срез уходит в док с ЭТИМ archive_id."""
        import tempfile
        did = trainer_log.doc_id()
        b = FakeBridge({did: "\n".join(f"TRN старое {i}" for i in range(50)),
                        self.ARCHIVE_ID: "TRN архивное 0"})
        om, ok_ = trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS
        trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS = 200, 120
        try:
            with tempfile.TemporaryDirectory() as d:
                res = trainer_log.append(1, "btn", "новое", now=NOW, env=ENV,
                                         get=b.get, post=b.post,
                                         lock_path=os.path.join(d, "l.lock"),
                                         spool_path=os.path.join(d, "s.spool"))
        finally:
            trainer_log.MAX_CHARS, trainer_log.KEEP_CHARS = om, ok_
        self.assertEqual(res["status"], "ok")
        self.assertIn("TRN старое 49", b.docs[self.ARCHIVE_ID])      # самое старое — в архиве Штаба
        self.assertIn("TRN архивное 0", b.docs[self.ARCHIVE_ID])     # прежний архив не затёрт
        self.assertIn("новое", b.docs[did])


class TestWriteLock(unittest.TestCase):
    """Bridge умеет только read+write ЦЕЛОГО дока, а потребителей два (userbot + moderbot) на одном
    ПК. Без сериализации вторая запись затирает строку первой — событие теряется молча, а ТЗ
    требует «писать ВСЁ». Критическая секция закрыта файловым локом."""

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.lock = os.path.join(self._dir.name, "trainer_log.lock")

    def tearDown(self):
        self._dir.cleanup()

    def test_lock_is_exclusive_and_released(self):
        with trainer_log.doc_lock(self.lock, wait=0) as mine:
            self.assertTrue(mine)
            self.assertTrue(os.path.exists(self.lock))
            with trainer_log.doc_lock(self.lock, wait=0) as second:
                self.assertFalse(second)                            # занят — второй не получил
        self.assertFalse(os.path.exists(self.lock))                 # снят в finally

    def test_busy_lock_does_not_swallow_the_event(self):
        """Не дождались лока → пишем ВСЁ РАВНО (с предупреждением): потерять событие хуже,
        чем рискнуть редкой гонкой."""
        did = "DOC"
        import tempfile, json
        p = os.path.join(self._dir.name, "s.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"doc_id": did}, f)
        b = FakeBridge({did: "TRN старое"})
        old_wait = trainer_log.LOCK_WAIT_SEC
        trainer_log.LOCK_WAIT_SEC = 0            # в бою ждём ~70с (4 HTTP по 30с) — в тесте не ждём
        try:
            with trainer_log.doc_lock(self.lock, wait=0):           # лок держит «другой процесс»
                res = trainer_log.append(1, "btn", "событие", now=NOW, env=ENV, get=b.get,
                                         post=b.post, sidecar_path=p, lock_path=self.lock,
                                         spool_path=os.path.join(self._dir.name, "s.spool"))
        finally:
            trainer_log.LOCK_WAIT_SEC = old_wait
        self.assertEqual(res["status"], "ok")
        self.assertIn("событие", b.docs[did])

    def test_stale_lock_is_taken_over(self):
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write("99999")                                        # владелец умер посреди HTTP
        old_mtime = os.path.getmtime(self.lock) - 10_000
        os.utime(self.lock, (old_mtime, old_mtime))
        with trainer_log.doc_lock(self.lock, wait=0, stale=60) as mine:
            self.assertTrue(mine, "протухший лок обязан забираться, иначе лог встанет навсегда")
        self.assertFalse(os.path.exists(self.lock))

    def test_lock_never_raises_on_broken_path(self):
        bad = os.path.join(self._dir.name, "нет-такого-каталога", "l.lock")
        with trainer_log.doc_lock(bad, wait=0) as mine:
            self.assertFalse(mine)                                  # лок не взяли, но и не упали

    def test_two_sequential_writers_keep_both_lines(self):
        """Модель гонки: два потребителя пишут подряд — обе строки обязаны уцелеть."""
        import tempfile, json
        p = os.path.join(self._dir.name, "s.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"doc_id": "DOC"}, f)
        b = FakeBridge({"DOC": "TRN LOG v1 | легенда"})
        for who, txt in (("btn", "модербот: тап номера 2"), ("bot", "userbot: ответ клиенту")):
            trainer_log.append(9, who, txt, now=NOW, env=ENV, get=b.get, post=b.post,
                               sidecar_path=p, lock_path=self.lock,
                               spool_path=os.path.join(self._dir.name, "s.spool"))
        doc = b.docs["DOC"]
        self.assertIn("модербот: тап номера 2", doc)
        self.assertIn("userbot: ответ клиенту", doc)
        self.assertTrue(doc.startswith("TRN LOG v1 | легенда"))


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

    def test_all_failure_branches_are_logged(self):
        """Пакет «полнота лога» п.2: ветки «отказано/упало/пусто/устарело» пишутся в TRN — иначе
        тишина в логе неотличима от «кнопки не жали» и разбор прогона по мозгу врёт."""
        ub = self._src("userbot_listen.py")
        for anchor in ("⛔ отказ: @",                # approver-гейт (урок/отмена/свободный текст)
                       "сбой генерации ответа",     # генерация черновика упала
                       "сбой моста 2.1",            # «до crm» упал
                       "диалог пуст"):              # «до crm» по пустому диалогу
            self.assertIn(anchor, ub, f"userbot: ветка не видна в TRN: {anchor}")
        mb = self._src("moderation_bot.py")
        for anchor in ("⛔ отказ: @",                # approver-гейт кнопок
                       "сбой моста 2.1",            # 📋 До CRM упал
                       "диалог пуст",               # 📋 До CRM по пустому диалогу
                       "нет последней пары",        # 🎓 Обучить без обмена
                       "сбой LLM гипотез",          # 🎓 Обучить: LLM упал
                       "LLM вернул 0 гипотез",      # 🎓 Обучить: LLM ответил пусто (другая ветка!)
                       "устаревшей гипотезе"):      # тап по номеру из старого списка
            self.assertIn(anchor, mb, f"moderbot: ветка не видна в TRN: {anchor}")

    def test_env_garbage_never_breaks_the_import(self):
        """Настройки читаются с полной защитой: мусор в env → дефолт. Без этого ValueError падал бы
        на ИМПОРТЕ модуля, а его импортит userbot_listen на уровне модуля ⇒ не поднялся бы весь
        userbot (заявленный «FAIL-SAFE ВЕЗДЕ» обязан начинаться с разбора конфига)."""
        self.assertEqual(trainer_log._int_env("НЕТ_ТАКОЙ_ПЕРЕМЕННОЙ", 42), 42)
        for junk in ("", "  ", "много", "1e6", "10.5", None):
            os.environ["TRAINER_LOG_PROBE"] = "" if junk is None else junk
            try:
                self.assertEqual(trainer_log._int_env("TRAINER_LOG_PROBE", 7), 7, repr(junk))
            finally:
                os.environ.pop("TRAINER_LOG_PROBE", None)
        os.environ["TRAINER_LOG_PROBE"] = "123"
        try:
            self.assertEqual(trainer_log._int_env("TRAINER_LOG_PROBE", 7), 123)
        finally:
            os.environ.pop("TRAINER_LOG_PROBE", None)
        # …и сам модуль переимпортируется с мусором в окружении
        os.environ["TRAINER_LOG_MAX_CHARS"] = "не число"
        try:
            import importlib
            importlib.reload(trainer_log)
            self.assertEqual(trainer_log.MAX_CHARS, 1000000)
        finally:
            os.environ.pop("TRAINER_LOG_MAX_CHARS", None)
            import importlib
            importlib.reload(trainer_log)

    def test_log_write_is_fire_and_forget_in_both_consumers(self):
        """Лог наблюдения НЕ ИМЕЕТ ПРАВА держать диалог. В userbot запись вызывается прямо в
        клиентском турне (до планирования ответа), а модербот собран без concurrent_updates —
        PTB обрабатывает апдейты ПОСЛЕДОВАТЕЛЬНО, и зависшая на Bridge запись тормозила бы
        модерацию РЕАЛЬНЫХ клиентов. Значит в обоих _trn_log обязан планировать задачу, а не ждать."""
        for name in ("userbot_listen.py", "moderation_bot.py"):
            src = self._src(name)
            body = src.split("async def _trn_log", 1)[1].split("\nasync def ", 1)[0]
            self.assertIn("asyncio.create_task(_write())", body, name)
            self.assertNotIn("await asyncio.to_thread(trainer_log.safe_append", body.split(
                "async def _write", 1)[0], name)   # до вложенной корутины ожидания сети нет

    def test_auto_apply_map_covers_this_module(self):
        """Правка trainer_log.py обязана рестартить ОБА бота — иначе живые процессы доживут
        на старом коде (класс delivery.py dae330a)."""
        import pc_orchestrator as o
        self.assertEqual(o._procs_for_file("trainer_log.py"), {"userbot", "moderbot"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
