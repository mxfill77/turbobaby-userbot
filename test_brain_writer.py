# -*- coding: utf-8 -*-
"""
test_brain_writer.py — офлайн-тесты доверенного писателя в мозг (brain_writer). Сети и секретов
НЕТ: транспорт инжектируется FakeBridge, конфиг — словарём env.

Моки КОПИРУЮТ ЖИВОЙ формат Bridge (правило-класс CLAUDE.md, снят пробой 23.07 и повторяет
test_trainer_log): read_doc ok → {"ok":true,"name":…,"id":…,"text":…}; отказ →
{"ok":false,"error":"unknown_name",…}; write_doc ok → {"ok":true,"chars":N}; отказ записи →
{"ok":false,"error":"write_failed","message":"getFileById"}. Адресация и id=, и name=.
"""
import io
import os
import tempfile
import unittest

# Разведение тестового и боевого контекста ДО импорта модуля (идиом test_pretool_guard):
# заодно включает гард тестового контекста самого brain_writer (живой док не трогаем).
os.environ["TURBOBABY_TEST_LOGS"] = "1"

import brain_writer as bw  # noqa: E402

ENV = {"BRIDGE_URL": "https://bridge.test/exec", "BRIDGE_TOKEN": "TOK"}


class FakeBridge:
    """Живой формат ответов Bridge (см. шапку). docs: id → текст; names: имя манифеста → id."""

    def __init__(self, docs, names=None):
        self.docs = dict(docs)
        self.names = dict(names or {})
        self.reads, self.writes = [], []
        self.fail_write = False
        self.lie_on_write = None     # Bridge «ок», но в док легло другое (ловля обратным чтением)
        self.lie_on_create = None    # то же для СОЗДАНИЯ: ok+id вернулся, а в файле не тот текст

    def _resolve(self, params):
        if params.get("id"):
            return params["id"] if params["id"] in self.docs else None
        return self.names.get(params.get("name"))

    def get(self, url, params):
        self.reads.append(dict(params))
        if params.get("action") == "list_brain":       # живой формат doGet.list_brain: манифест целиком
            return {"ok": True, "manifest": dict(self.names, folder_id="FOLDER")}
        did = self._resolve(params)
        if did is None or did not in self.docs:
            return {"ok": False, "error": "unknown_name", "known": sorted(self.names)}
        return {"ok": True, "name": params.get("name"), "id": did, "text": self.docs[did]}

    def post(self, url, payload):
        self.writes.append(dict(payload))
        if payload.get("action") == "create_brain_plain":     # живой формат ReadDocs.createBrainPlain_
            nid = "NEW%d" % (len(self.docs) + 1)
            text = payload.get("text", "")
            self.docs[nid] = text if self.lie_on_create is None else self.lie_on_create
            if payload.get("key"):
                self.names[payload["key"]] = nid
            # живой createBrainPlain_ отдаёт ровно эти пять полей (key || null, chars — длина текста)
            return {"ok": True, "name": payload.get("name"), "key": payload.get("key") or None,
                    "id": nid, "chars": len(text)}
        if self.fail_write:
            return {"ok": False, "error": "write_failed", "message": "getFileById"}
        did = self._resolve(payload)
        if did is None:
            return {"ok": False, "error": "unknown_name", "known": sorted(self.names)}
        self.docs[did] = payload["text"] if self.lie_on_write is None else self.lie_on_write
        return {"ok": True, "chars": len(payload["text"])}


# Мини-док со структурой KB_MASTER: якорь «РАЗДЕЛ 4» с линией «═» и «РАЗДЕЛ 3» выше.
MINI_DOC = "\n".join([
    "РАЗДЕЛ 3 — ХРОНИКА",
    "- старая запись",
    "",
    "══════════",
    "РАЗДЕЛ 4 — ПРОЧЕЕ",
    "- хвост дока",
])


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bridge = FakeBridge(docs={"FID1": "старая строка\nещё строка",
                                       "MINI": MINI_DOC},
                                 names={"cowork_log": "FID1", "index": "MINI"})

    def _append(self, text, **kw):
        kw.setdefault("env", ENV)
        kw.setdefault("get", self.bridge.get)
        kw.setdefault("post", self.bridge.post)
        kw.setdefault("backup_dir", self._tmp.name)
        return bw.append(text, **kw)


class TestAppend(Base):
    def test_append_top_by_name(self):
        res = self._append("NOTE проба канала", name="cowork_log")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(self.bridge.docs["FID1"], "NOTE проба канала\nстарая строка\nещё строка")
        self.assertEqual(self.bridge.writes[0].get("name"), "cowork_log")  # адресация именем
        self.assertNotIn("id", self.bridge.writes[0])
        self.assertIn("NOTE проба канала", res["fragment"])
        # бэкап лёг ДО записи и содержит СТАРЫЙ текст целиком
        with open(res["backup"], encoding="utf-8") as f:
            self.assertEqual(f.read(), "старая строка\nещё строка")

    def test_append_bottom_by_id(self):
        res = self._append("хвостовая строка", doc_id="FID1", place="bottom")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(self.bridge.docs["FID1"], "старая строка\nещё строка\nхвостовая строка")
        self.assertEqual(self.bridge.writes[0].get("id"), "FID1")          # адресация file id

    def test_idempotent_noop_without_write_and_backup(self):
        res = self._append("ещё строка", name="cowork_log")
        self.assertEqual(res["status"], "noop")
        self.assertEqual(self.bridge.writes, [])
        self.assertIsNone(res["backup"])
        self.assertEqual(os.listdir(self._tmp.name), [])

    def test_anchor_before_with_require_above(self):
        res = self._append("- новая запись хроники", name="index",
                           anchor=r"^═+\s*\nРАЗДЕЛ 4\b", require_above="РАЗДЕЛ 3")
        self.assertEqual(res["status"], "ok")
        new = self.bridge.docs["MINI"]
        # блок лёг в конец Раздела 3: выше «старая запись», ниже пустая строка и линия «═»
        self.assertIn("- старая запись\n\n- новая запись хроники\n\n══════════\nРАЗДЕЛ 4", new)
        # хвост дока (Раздел 4 и дальше) остался байт-в-байт
        self.assertTrue(new.endswith("══════════\nРАЗДЕЛ 4 — ПРОЧЕЕ\n- хвост дока"))

    def test_anchor_must_match_exactly_once(self):
        for bad_anchor in (r"^РАЗДЕЛ 9\b",          # 0 совпадений
                           r"^- .+$"):              # 2+ совпадений
            with self.assertRaises(bw.BrainWriterError) as cm:
                self._append("текст", name="index", anchor=bad_anchor)
            self.assertEqual(cm.exception.code, 3, bad_anchor)
        self.assertEqual(self.bridge.writes, [])     # док НЕ тронут

    def test_require_above_missing_blocks_write(self):
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("текст", name="index", anchor=r"^РАЗДЕЛ 4\b", require_above="РАЗДЕЛ 0")
        self.assertEqual(cm.exception.code, 3)
        self.assertEqual(self.bridge.writes, [])

    def test_empty_text_rejected(self):
        with self.assertRaises(bw.BrainWriterError):
            self._append("   \n\n")
        self.assertEqual(self.bridge.reads, [])      # даже читать не ходили


class TestFailSafe(Base):
    def test_unknown_doc_read_fails_without_write(self):
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("строка", name="нет_такого")
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.bridge.writes, [])

    def test_write_failed_raises_but_backup_stays(self):
        self.bridge.fail_write = True
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("строка", name="cowork_log")
        self.assertEqual(cm.exception.code, 4)
        self.assertIn("бэкап", str(cm.exception))
        self.assertEqual(len(os.listdir(self._tmp.name)), 1)   # старый текст спасён для отката

    def test_readback_catches_lying_write(self):
        # Bridge ответил ok, но в док легло НЕ ТО → обратное чтение обязано поймать
        self.bridge.lie_on_write = "чужой текст"
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("строка", name="cowork_log")
        self.assertEqual(cm.exception.code, 5)
        self.assertIn("ОБРАТНОЕ ЧТЕНИЕ", str(cm.exception))

    def test_no_config_no_network(self):
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("строка", name="cowork_log", env={})
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self.bridge.reads, [])

    def test_exactly_one_address_required(self):
        for kw in ({}, {"doc_id": "FID1", "name": "cowork_log"}):
            with self.assertRaises(bw.BrainWriterError):
                self._append("строка", **kw)

    def test_test_context_guard_blocks_live_transport(self):
        # под юнитами/гейтом (TURBOBABY_TEST_LOGS=1 выше) писатель БЕЗ инжектированного
        # транспорта обязан отказаться — урок KB_trainer_log 23.07 (гейт писал в живой док)
        with self.assertRaises(bw.BrainWriterError) as cm:
            bw.apply(lambda old: old + "x", name="cowork_log", env=ENV)
        self.assertIn("тестовый контекст", str(cm.exception))


class TestCreatePlain(Base):
    """Создание нового Brain-дока: единственный легальный путь с ПК (секреты у писателя, не у скрипта)."""

    def _create(self, name, **kw):
        kw.setdefault("env", ENV)
        kw.setdefault("get", self.bridge.get)
        kw.setdefault("post", self.bridge.post)
        return bw.create_plain(name, **kw)

    def test_create_registers_and_is_addressable_by_name(self):
        r = self._create("KB_проба_архив", key="проба_архив", text="история целиком")
        self.assertTrue(r["ok"])
        self.assertEqual(self.bridge.docs[r["id"]], "история целиком")
        # зарегистрирован в манифесте → тем же каналом читается по ИМЕНИ
        self.assertEqual(bw.read_text(name="проба_архив", env=ENV, get=self.bridge.get),
                         "история целиком")

    def test_create_without_key_not_in_manifest(self):
        r = self._create("KB_безымянный", text="текст")
        self.assertTrue(r["ok"])
        self.assertEqual(bw.read_text(doc_id=r["id"], env=ENV, get=self.bridge.get), "текст")

    def test_create_needs_name(self):
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._create("   ", text="x")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self.bridge.writes, [])

    def test_create_blocked_in_test_context_without_mock(self):
        """Под гейтом/юнитами без инжектированного транспорта живой Brain не трогаем."""
        for kw in ({}, {"post": self.bridge.post}, {"get": self.bridge.get}):
            with self.assertRaises(bw.BrainWriterError) as cm:
                bw.create_plain("KB_живой", text="x", env=ENV, **kw)
            self.assertIn("тестовый контекст", str(cm.exception))

    def test_create_verifies_by_reading_back(self):
        """«Мост ответил ok» фактом записи не является — сверяем по ЖИВОМУ файлу."""
        r = self._create("KB_сверка", key="сверка", text="строка один\nстрока два")
        self.assertTrue(r["verified"])
        self.assertEqual(r["chars_back"], len("строка один\nстрока два"))
        self.assertEqual(r["fffd"], 0)
        self.assertEqual(self.bridge.reads[-1]["id"], r["id"])   # читали именно созданный файл

    def test_create_readback_mismatch_names_created_id(self):
        """Создание НЕидемпотентно: отказ обязан назвать id — иначе повтор даст ДУБЛЬ в папке."""
        self.bridge.lie_on_create = "совсем другой текст"
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._create("KB_ложь", key="ложь", text="настоящий текст")
        self.assertEqual(cm.exception.code, 5)
        self.assertIn("ДУБЛЬ", str(cm.exception))
        self.assertEqual(self.bridge.writes[-1]["name"], "KB_ложь")
        self.assertIn("NEW", str(cm.exception))                  # id созданного файла в тексте отказа

    def test_create_refuses_mojibake_input(self):
        """Побитый ВХОД обратное чтение не ловит (оно сверит мусор с мусором) — держим на входе."""
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._create("KB_мохибейк", key="мохибейк", text="тип Т" + chr(0xFFFD) + "С")
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("U+FFFD", str(cm.exception))
        self.assertEqual(self.bridge.writes, [])                 # в папку Brain ничего не легло

    def test_create_read_failure_still_names_created_file(self):
        """Отказ ЧТЕНИЯ после успешного создания — файл уже в папке, о нём обязаны сказать."""
        self.bridge.lie_on_create = ""            # пустой ответ чтения = отказ чтения (класс 17.07)
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._create("KB_нечитаемый", text="текст")
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("УЖЕ СОЗДАН", str(cm.exception))

    def test_create_tolerates_trailing_newline_normalization(self):
        """Хвостовой перевод строки Drive нормализует — это не «текст не совпал»."""
        self.bridge.lie_on_create = "тело файла"
        r = self._create("KB_хвост", text="тело файла\n")
        self.assertTrue(r["verified"])


class TestStdinUtf8(unittest.TestCase):
    """CLI-вход: многострочный блок приходит по stdin, и декодировать его ЛОКАЛЬЮ нельзя."""

    class _Stdin:
        def __init__(self, data):
            self.buffer = io.BytesIO(data)

    def _patch(self, data):
        old = bw.sys.stdin
        self.addCleanup(setattr, bw.sys, "stdin", old)
        bw.sys.stdin = self._Stdin(data)

    def test_utf8_bytes_decoded(self):
        self._patch("Строка «ёлка»\n".encode("utf-8"))
        self.assertEqual(bw._stdin_text(), "Строка «ёлка»\n")

    def test_non_utf8_refused_with_clean_error(self):
        self._patch("Строка".encode("cp1251"))       # локаль Windows — в мозг такое не кладём
        with self.assertRaises(bw.BrainWriterError) as cm:
            bw._stdin_text()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("UTF-8", str(cm.exception))


class TestShrinkGuard(Base):
    """Класс 17.07: пустое чтение и укорачивание дока блокируются, обратное чтение сверяет ДЛИНУ."""

    def test_empty_read_is_failure_not_empty_doc(self):
        self.bridge.docs["FID1"] = ""
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("строка", name="cowork_log")
        self.assertEqual(cm.exception.code, 2)            # именно ОТКАЗ ЧТЕНИЯ
        self.assertIn("ПУСТОЙ текст", str(cm.exception))
        self.assertEqual(self.bridge.writes, [])          # док не тронут

    def test_whitespace_only_read_is_failure(self):
        self.bridge.docs["FID1"] = "   \n\n"
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("строка", name="cowork_log")
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(self.bridge.writes, [])

    def test_shrinking_mutate_blocked_with_both_lengths(self):
        old = self.bridge.docs["FID1"]
        with self.assertRaises(bw.BrainWriterError) as cm:
            bw.apply(lambda o: "коротко", name="cowork_log", env=ENV,
                     get=self.bridge.get, post=self.bridge.post, backup_dir=self._tmp.name)
        self.assertEqual(cm.exception.code, 6)
        self.assertIn("было %d символов" % len(old), str(cm.exception))
        self.assertIn("стало бы 7", str(cm.exception))
        self.assertEqual(self.bridge.writes, [])
        self.assertEqual(os.listdir(self._tmp.name), [])  # до бэкапа дело не дошло

    def test_declared_shrink_allowed(self):
        """Осознанное сокращение возможно — но только объявленное ЯВНО."""
        res = bw.apply(lambda o: o[:10], name="cowork_log", env=ENV, allow_shrink=True,
                       get=self.bridge.get, post=self.bridge.post, backup_dir=self._tmp.name)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(self.bridge.docs["FID1"], "старая стр")

    def test_readback_length_catches_truncation_that_line_check_misses(self):
        """Мост ответил ok, строка в доке ЕСТЬ — но док усечён. Ловит только сверка длины."""
        self.bridge.lie_on_write = "строка\nобрывок"
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._append("строка", name="cowork_log")
        self.assertIn("строка", self.bridge.docs["FID1"])   # проверка «наличие строки» прошла бы
        self.assertEqual(cm.exception.code, 5)
        self.assertIn("УКОРОТИЛСЯ", str(cm.exception))

    def test_normal_append_grows(self):
        old = self.bridge.docs["FID1"]
        res = self._append("NOTE рост", name="cowork_log")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["before_chars"], len(old))
        self.assertGreater(res["after_chars"], res["before_chars"])


class TestReadText(Base):
    def test_read_empty_is_failure(self):
        self.bridge.docs["FID1"] = ""
        with self.assertRaises(bw.BrainWriterError) as cm:
            bw.read_text(doc_id="FID1", env=ENV, get=self.bridge.get)
        self.assertEqual(cm.exception.code, 2)

    def test_read_by_name_and_id(self):
        self.assertEqual(bw.read_text(name="index", env=ENV, get=self.bridge.get), MINI_DOC)
        self.assertEqual(bw.read_text(doc_id="FID1", env=ENV, get=self.bridge.get),
                         "старая строка\nещё строка")

    def test_read_unknown_raises(self):
        with self.assertRaises(bw.BrainWriterError):
            bw.read_text(name="нет_такого", env=ENV, get=self.bridge.get)


# Ключи — как в ЖИВОМ реестре 01.08.2026 (29 доков + folder_id; `north_star` зарегистрирован
# этим же заходом): только [a-z0-9_], ни одной заглавной, ни одного префикса KB_. Именно поэтому
# рамка, зовущая доки титулами (KB_MASTER, KB_INFRA), получала unknown_name — правило-класс
# «мок копирует живой формат».
LIVE_KEYS = ["booking_flow", "business_rules", "cc_log", "cc_log_archive", "cc_userbot_log",
             "collect_booking_spec", "cowork_log", "cowork_log_archive", "cowork_log_test",
             "cowork_log_test_archive", "executors_map", "faq", "index", "infra",
             "knowledge_base", "north_star", "orchestrator_plan", "orchestrator_safety",
             "park_list", "payments_plan", "project_state", "pulse", "review", "review_archive",
             "rules", "sessions_log", "sessions_log_archive", "state_model", "turbobaby_faq"]


class ResolveBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bridge = FakeBridge(docs={k.upper(): "тело " + k for k in LIVE_KEYS},
                                 names={k: k.upper() for k in LIVE_KEYS})

    def _res(self, name):
        return bw.resolve_name(name, env=ENV, get=self.bridge.get)


class TestResolveNames(ResolveBase):
    """Разрешение имён: ОДНО правило канона поверх живого реестра, и ничего кроме него."""

    def test_canon_rule_covers_case_prefix_and_separators(self):
        """Одно правило: регистр, префикс KB_ и разделители — всё это одно и то же имя."""
        for name in ("KB_INFRA", "kb_infra", "KB_Infra", "kb-infra", "INFRA", " infra "):
            addr, ref, _ = self._res(name)
            self.assertEqual(addr, {"name": "infra"}, name)
            self.assertEqual(ref, "name:infra")

    def test_exact_registry_key_needs_no_resolution(self):
        """Ключ реестра уходит на мост как раньше: ни лишнего запроса, ни смены поведения."""
        self.assertEqual(bw.read_text(name="infra", env=ENV, get=self.bridge.get), "тело infra")
        self.assertEqual([r.get("action") for r in self.bridge.reads], ["read_doc"])

    def test_token_tail_match_both_directions(self):
        """`KB_userbot_log` ↔ `cc_userbot_log` и `KB_claude_review_archive` ↔ `review_archive`."""
        self.assertEqual(self._res("KB_userbot_log")[0], {"name": "cc_userbot_log"})
        self.assertEqual(self._res("KB_claude_review_archive")[0], {"name": "review_archive"})

    def test_substring_alone_is_not_a_match(self):
        """Совпадение — только по ГРАНИЦЕ ТОКЕНА и только с ХВОСТА. `park` внутри `park_list` не
        считается; иначе `cc_log` «совпал» бы с `cc_log_archive` и правка ушла бы в архив."""
        for name in ("KB_park", "KB_nowledge_base", "cc_log_arch"):
            with self.assertRaises(bw.BrainWriterError) as cm:
                self._res(name)
            self.assertIn("НЕ РАЗРЕШЕНО", str(cm.exception), name)

    def test_ambiguity_refuses_instead_of_guessing(self):
        """Несколько кандидатов — отказ со списком: угадать чужой док хуже, чем встать."""
        for name, cands in (("KB_log_archive", ("cc_log_archive", "cowork_log_archive")),
                            ("KB_log", ("cc_log", "cowork_log", "sessions_log"))):
            with self.assertRaises(bw.BrainWriterError) as cm:
                self._res(name)
            for c in cands:
                self.assertIn(c, str(cm.exception), name)

    def test_registered_orphan_is_caught_by_the_rule_itself(self):
        """KB_NORTH_STAR жил вне реестра и потому «не существовал» во всех описях. Лечение —
        РЕГИСТРАЦИЯ (сделана 01.08 на живом мосту), а не строка данных в коде: получив ключ,
        имя ловится тем же каноном, что KB_INFRA."""
        addr, ref, note = self._res("KB_NORTH_STAR")
        self.assertEqual(addr, {"name": "north_star"})
        self.assertEqual(self._res("north_star")[0], addr)   # тот же канон — тот же адрес

    def test_addresses_that_need_data_refuse_honestly(self):
        """ГРАНИЦА ПРАВИЛА, держится намеренно. Титул ≠ ключ (`KB_MASTER` при ключе `index`),
        орфан без ключа (`roadmap_v2`), id из сайдкара (`KB_trainer_log`), мёртвое имя
        (`roadmap_master`) — лексикой не выводятся. Пока данных нет, канал ОТКАЗЫВАЕТ вслух
        и называет канон, а не угадывает док: словарь в коде разошёлся бы с реестром молча."""
        for name in ("KB_MASTER", "roadmap_v2", "KB_trainer_log", "roadmap_master"):
            with self.assertRaises(bw.BrainWriterError) as cm:
                self._res(name)
            self.assertIn("НЕ РАЗРЕШЕНО", str(cm.exception), name)
            self.assertTrue(getattr(cm.exception, "verdict", False), name)

    def test_refusal_names_the_cure_that_exists(self):
        """Отказ обязан назвать выход, который РАБОТАЕТ: регистрация ключа (проверена живьём)."""
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._res("KB_MASTER")
        self.assertIn("--register", str(cm.exception))

    def test_unresolvable_name_says_what_it_tried(self):
        with self.assertRaises(bw.BrainWriterError) as cm:
            self._res("KB_INFRA_UNIFY_FUTURE")
        self.assertIn("infra_unify_future", str(cm.exception))       # назван канон, а не «нет»

    def test_folder_id_is_never_a_doc(self):
        with self.assertRaises(bw.BrainWriterError):
            self._res("folder_id")

    def test_registry_is_the_only_source_of_addresses(self):
        """Снимут ключ с прода — имя перестаёт резолвиться ТУТ ЖЕ. Второго словаря нет, значит
        нечему разойтись с реестром: канал знает ровно то, что знает мост."""
        self.bridge.names.pop("north_star")
        with self.assertRaises(bw.BrainWriterError):
            self._res("KB_NORTH_STAR")

    def test_flaky_registry_read_is_retried_not_a_verdict(self):
        """Флап моста на ДОБАВОЧНОМ запросе реестра не имеет права стать «имя не резолвится»."""
        bw._READ_PAUSE = 0
        self.addCleanup(lambda: setattr(bw, "_READ_PAUSE", 1.5))
        real, calls = self.bridge.get, []

        def flaky(url, params):
            if params.get("action") == "list_brain":
                calls.append(1)
                if len(calls) < 3:
                    raise OSError("The read operation timed out")
            return real(url, params)

        self.assertEqual(bw.resolve_name("KB_INFRA", env=ENV, get=flaky)[0], {"name": "infra"})
        self.assertEqual(len(calls), 3)

    def test_registry_failure_keeps_the_honest_unknown_name(self):
        """Реестр не прочитался — не выдумываем вердикт про имя, отдаём ответ моста как есть."""
        self.bridge.get = lambda url, params: {"ok": False, "error": "unknown_name"}
        with self.assertRaises(bw.BrainWriterError) as cm:
            bw.read_text(name="KB_INFRA", env=ENV, get=self.bridge.get)
        self.assertIn("unknown_name", str(cm.exception))


class TestResolvedAddressIsUsedForWriteToo(ResolveBase):
    """САМОЕ ОПАСНОЕ место правки: прочитать один док, а записать по старому адресу."""

    def test_append_by_title_reads_and_writes_the_same_doc(self):
        res = bw.append("НОВАЯ СТРОКА", name="KB_INFRA", env=ENV, get=self.bridge.get,
                        post=self.bridge.post, backup_dir=self._tmp.name)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(self.bridge.writes), 1)
        self.assertEqual(self.bridge.writes[0].get("name"), "infra")   # НЕ «KB_INFRA»
        self.assertTrue(self.bridge.docs["INFRA"].startswith("НОВАЯ СТРОКА\n"))

    def test_unresolvable_name_never_reaches_write(self):
        """Имя без адреса обязано умереть ДО write_doc: «непонятно куда» пишут в чужой док."""
        for name in ("KB_MASTER", "roadmap_master", "KB_log"):
            with self.assertRaises(bw.BrainWriterError):
                bw.append("НОВАЯ СТРОКА", name=name, env=ENV, get=self.bridge.get,
                          post=self.bridge.post, backup_dir=self._tmp.name)
        self.assertEqual(self.bridge.writes, [])

    def test_raw_lane_keeps_the_pre_translator_behaviour(self):
        """resolve=False — «как было»: имя уходит на мост как написано, промах остаётся
        промахом. На этой полосе меряется числитель регресса, поэтому она обязана быть честной."""
        with self.assertRaises(bw.BrainWriterError) as cm:
            bw.read_text(name="KB_INFRA", env=ENV, get=self.bridge.get, resolve=False)
        self.assertIn("unknown_name", str(cm.exception))
        self.assertEqual(bw.read_text(name="infra", env=ENV, get=self.bridge.get, resolve=False),
                         "тело infra")


if __name__ == "__main__":
    unittest.main()
