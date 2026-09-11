# -*- coding: utf-8 -*-
"""test_vitrina_pc.py — регресс живой витрины состояния полосы ПК.

* ``TestPurity`` — ``VITRINA_PC_PURE``: чистая логика без часов, диска, сети и
  ``getenv``; номера темы не знает ВООБЩЕ.
* ``TestReadsOnly`` — ``VITRINA_PC_READS_ONLY``: витрина ничего не исполняет и
  задач не ставит — ни одной ветки записи в очередь, обходом AST боевых файлов.
* ``TestNoZeroForUnknown`` — ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1 задания: недоступный источник
  числа даёт НЕИЗВЕСТНО с причиной, а не ноль. Проверяется и точечно, и на целом
  сообщении, собранном при ВСЕХ мёртвых источниках.
* ``TestInPlace`` — ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2 задания: второй оборот подряд НЕ создаёт
  второго сообщения. Сюда же — сорванная правка (нового не шлём, ждём оборота) и
  единственный законный случай нового сообщения: Telegram сказал, что старого нет.
* ``TestShtabNode`` — ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3 задания: отсутствующий узел Штаба не
  роняет витрину и ВИДЕН строкой во всех трёх человеческих частях.
* ``TestParts`` — шесть частей, и часть без строк — падение, а не тихий пропуск.
* ``TestAxes`` — три оси: обслуживание — ОСТАТОК; неполный корпус даёт None во
  всех трёх, а не частичное число, выданное за полное.
* ``TestSignature`` — правка идёт ТОЛЬКО при смене чисел (пункт 4 задания).
* ``TestPulse`` — узел пульса витрину не роняет: мозг молчит → строка есть.
"""
from __future__ import annotations

import ast
import io
import json
import os
import tempfile
import unittest

import contour_digest as cd
import contour_digest_run as cdr
import expectations_pc as ex
import shtab_box_signals as sbs
import vitrina_pc as vp
import vitrina_pc_run as run

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1788352800.0                      # 2026-09-02 12:40 UTC
DAY = "2026-09-02"


# Документ Штаба в ПАПКЕ мозга — та форма, в какой его отдаёт перечисление моста
# (замер 04.09: у файла ровно три поля — id, mime, name). Идентификатор здесь
# ВЫДУМАННЫЙ намеренно: живой file id в тестах был бы тем самым зашитым числовым
# адресом, который задание запрещает.
_FILE = {"name": vp.SHTAB_NODE, "id": "ID-документа-витрины", "mime": "text/plain"}


def _folder(files, truncated=False):
    """Перечислитель папки в форме ответа `brain_writer.list_folder`."""
    def _lister(_prefix):
        return {"ok": True, "files": list(files), "count": len(files), "truncated": truncated}
    return _lister


def _read_source(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _idle(sec, busy=0, ids=()):
    """Факт простоя в той форме, в какой его собирает `vitrina_pc_run.idle_facts`."""
    if busy:
        return {"busy": int(busy), "ids": list(ids)}
    if sec is None:
        return {"busy": 0, "sec": None, "why": "в реестре закрытых нет ни одной метки времени"}
    return {"busy": 0, "sec": float(sec), "since": NOW - float(sec), "since_id": "84",
            "since_words": "2026-09-02 11:00 UTC"}


def _shtab_last(sec, day=DAY, found=True):
    if not found:
        return {"found": False, "scope": "в реестре 77 строк"}
    return {"found": True, "id": "84", "day": day, "key": "abc123", "at": NOW - float(sec),
            "at_kind": "claim", "at_words": "2026-09-02 10:00 UTC", "sec": float(sec)}


def _dead_facts(day=DAY):
    """Все источники мертвы. Ровно тот случай, ради которого писан пункт 3."""
    return {"day": day, "shtab": vp.parse_shtab("", ok=False, why="мост молчит"),
            "running": None, "expects": None, "failed": None, "series": None,
            "shtab_taken": None, "external": None, "awaiting": None, "waiting": None,
            "axes": vp.axes_tally(None, None, None), "axes_why": "git не ответил",
            "closed_day": None}


def _live_facts(day=DAY):
    return {"day": day, "shtab": vp.parse_shtab("дата=%s\n[[СЕЙЧАС]]\nстроим витрину\n"
                                                "[[ЗАСТРЯЛО]]\nничего\n[[КУДА ИДЁМ]]\nк 30" % day),
            "running": [{"id": "5", "goal": "витрина", "age": 600.0}],
            "expects": [], "failed": [],
            "awaiting": {"holding": 1, "asking": 1, "blind": 0},
            "series": {"streak": 5, "target": 30, "proved": 5, "blind": 13, "unproved": 0,
                       "unjudged": 0, "moved": 2},
            "shtab_taken": 1,
            "external": {"day": day, "attempts": 3, "answers": 2, "channels_answered": 1,
                         "channels_tried": 2, "findings": 4, "down": []},
            # Две РАЗНЫЕ вещи в одном списке `needs_approval` — ровно то, что до 02.09
            # витрина звала одним словом: заявка внешнего канала и карточка гарда.
            "waiting": [{"id": "3", "goal": "[заявка-ревью дата=%s ключ=47b3131c052c]" % day,
                         "zayavka": True, "holds": False, "key": "47b3131c052c"},
                        {"id": "2", "goal": "удаление файла — разрешить?", "zayavka": False,
                         "holds": True, "key": ""}],
            "axes": {vp.AXIS_MAIN: 4, vp.AXIS_BIZ: 1, vp.AXIS_SERVICE: 22},
            "axes_why": "", "closed_day": 18,
            "health": _health_nodes(),
            "idle": _idle(None, busy=1, ids=["5"]), "shtab_last": _shtab_last(7200.0)}


def _health_nodes(stale=False):
    """Узлы здоровья спокойного контура: все приборы свежи и говорят зелёное."""
    return [{"name": ex.HEALTH_DAEMON, "said": ex.TURN_OK, "stale": stale,
             "src": ex.HEALTH_DAEMON_SRC, "age": "3 мин", "blind": "виток ≠ польза"}] + [
        {"name": kid, "said": ex.MOD_OK, "stale": stale,
         "src": (ex.KID_SIGNS[kid] or {}).get("src"), "age": "3 мин",
         "blind": (ex.KID_SIGNS[kid] or {}).get("caveat")} for kid in ex.KIDS]


class _Door(object):
    """Дверь-протокол: считает ОТДЕЛЬНО отправки и правки. Сеть не трогается."""

    def __init__(self, edit_ok=True, edit_why="", send_ok=True):
        self.sent, self.edited = [], []
        self.edit_ok, self.edit_why, self.send_ok = edit_ok, edit_why, send_ok

    def send(self, text, topic):
        self.sent.append((text, topic))
        return ("topic:%s" % topic, self.send_ok,
                str(9000 + len(self.sent)) if self.send_ok else "code=400 chat not found")

    def edit(self, text, topic, mid):
        self.edited.append((text, topic, mid))
        return ("topic:%s" % topic, self.edit_ok, str(mid) if self.edit_ok else self.edit_why)


class _Daemon(object):
    AUDIT_TOPIC = 829


def _git_none(argv, **kw):
    raise OSError("git отсутствует в разведочном дереве")


class TestPurity(unittest.TestCase):
    """VITRINA_PC_PURE — решение остаётся решением, а не руками."""

    BANNED_CALLS = {"now", "utcnow", "time", "monotonic", "getenv", "open", "run", "Popen",
                    "urlopen", "getmtime"}
    BANNED_IMPORTS = {"os", "subprocess", "socket", "urllib", "time", "shutil", "requests",
                      "datetime", "io", "calendar"}

    def test_no_clock_no_disk_no_network_no_env(self):
        with io.open(os.path.join(HERE, "vitrina_pc.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="vitrina_pc.py")
        bad_imports, bad_calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad_imports |= {a.name.split(".")[0] for a in node.names} & self.BANNED_IMPORTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                bad_imports |= {node.module.split(".")[0]} & self.BANNED_IMPORTS
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name in self.BANNED_CALLS:
                    bad_calls.add(name)
        self.assertEqual(bad_imports, set(), "чистый модуль завёл запрещённый импорт")
        self.assertEqual(bad_calls, set(), "чистый модуль завёл часы/диск/сеть/env")

    def test_module_knows_no_topic_number(self):
        """Номера темы в чистом модуле нет ни дефолтом, ни литералом."""
        with io.open(os.path.join(HERE, "vitrina_pc.py"), encoding="utf-8") as fh:
            body = fh.read()
        for known in ("328", "1160", "829", "205"):
            self.assertNotIn(known, body, "номер темы просочился в чистый модуль: %s" % known)

    def test_lists_of_paths_are_borrowed_not_retyped(self):
        """Списки путей осей ВЗЯТЫ у сводки: второй экземпляр разошёлся бы молча."""
        with io.open(os.path.join(HERE, "vitrina_pc_run.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("cdr.AXIS3_PATHS", body)
        self.assertIn("cdr.BUSINESS_PATHS", body)
        self.assertNotIn("AXIS3_PATHS = ", body, "список путей набран заново — он один на полосу")


class TestReadsOnly(unittest.TestCase):
    """VITRINA_PC_READS_ONLY — витрина ничего не исполняет и задач не ставит."""

    BANNED = {"enqueue_pc_task", "place_task", "place_ask", "set_needs_approval",
              "claim_task", "complete_task"}

    def test_no_queue_writes_anywhere(self):
        for name in ("vitrina_pc.py", "vitrina_pc_run.py"):
            with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=name)
            hits = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    got = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                    if got in self.BANNED:
                        hits.add(got)
            self.assertEqual(hits, set(), "%s пишет в очередь: %s" % (name, hits))

    def test_writes_only_own_state_and_own_pulse_node(self):
        """Единственные записи ветки — свой файл состояния и СВОЙ узел пульса."""
        with io.open(os.path.join(HERE, "vitrina_pc_run.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("PULSE_KEY = \"pulse_pc\"", body)
        for foreign in ("queue_state_pc", "cowork_log", "knowledge_base", "shtab_box"):
            self.assertNotIn("name=%r" % foreign, body, "витрина пишет в чужой узел %s" % foreign)


class TestNoZeroForUnknown(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1: недоступный источник → НЕИЗВЕСТНО, а НЕ ноль."""

    def test_number_never_turns_unknown_into_zero(self):
        got = vp.number(None, "слепок очереди не прочитан")
        self.assertIn(vp.UNKNOWN, got)
        self.assertIn("слепок очереди не прочитан", got)
        self.assertNotEqual(got.strip(), "0")
        self.assertNotIn("0", got.replace("НЕИЗВЕСТНО", ""), "незнание показано числом")

    def test_number_keeps_a_real_zero(self):
        """Настоящий ноль остаётся нулём: измеренное «ничего» — тоже ответ."""
        self.assertEqual(vp.number(0, "неважно"), "0")

    def test_whole_vitrina_on_dead_sources_says_unknown_everywhere(self):
        text = vp.render(_dead_facts(), "2026-09-02 12:40 UTC")
        self.assertGreaterEqual(text.count(vp.UNKNOWN), 6,
                                "мёртвые источники не назвались неизвестными")
        for title in (t for _k, t in vp.PARTS):
            self.assertIn(title, text, "часть %s пропала при мёртвых источниках" % title)
        for zero in ("взято 0 из", "ДЕРЖАТ операцию (карточки гарда): 0",
                     "ЖДУТ МНЕНИЯ (заявки, ящик не держат): 0", "ось (этап 3) 0"):
            self.assertNotIn(zero, text, "незнание подменено нулём: %s" % zero)

    def test_dead_source_lines_carry_the_reason(self):
        text = vp.render(_dead_facts(), "?")
        self.assertIn("слепок очереди не прочитан", text)
        self.assertIn("лоток не прочитан", text)
        # Причина, НАЗВАННАЯ руками, сильнее дежурной: она ближе к месту отказа.
        self.assertIn("git не ответил", text)
        self.assertIn("git за сутки не прочитан",
                      vp.axes_words(vp.axes_tally(None, None, None), None))

    def test_empty_is_not_unknown(self):
        """«В работе ничего» и «неизвестно» — РАЗНЫЕ новости, и обе названы."""
        empty = vp.part_now({"ok": False, "why": "нет"}, [], idle=_idle(600.0),
                            shtab_last=_shtab_last(3600.0))
        dead = vp.part_now({"ok": False, "why": "нет"}, None)
        self.assertIn("полоса свободна", " ".join(empty))
        self.assertNotIn(vp.UNKNOWN, " ".join(empty).replace("Штаб не обновил", ""))
        self.assertIn(vp.UNKNOWN, " ".join(dead))


class TestInPlace(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2: второй оборот подряд НЕ создаёт второго сообщения."""

    def _tick(self, root, door, shtab, now=NOW, force=False):
        return run.tick(root=root, state=os.path.join(root, "st.json"), now=now, send=True,
                        runner=_git_none, sender=door.send, editor=door.edit,
                        daemon=_Daemon(), shtab=shtab, force=force)

    def test_second_turn_makes_no_second_message(self):
        door = _Door()
        shtab = vp.parse_shtab("", ok=False, why="узла нет")
        with tempfile.TemporaryDirectory() as root:
            first = self._tick(root, door, shtab)
            self.assertTrue(first.get("ok"), first.get("why"))
            self.assertEqual(len(door.sent), 1, "первый оборот обязан ПОЛОЖИТЬ сообщение")
            mid = first["message_id"]
            # ВТОРОЙ ОБОРОТ, числа те же → не трогаем НИЧЕГО.
            second = self._tick(root, door, shtab, now=NOW + 900)
            self.assertTrue(second.get("sig_same"), "подпись чисел не сохранилась")
            self.assertEqual(len(door.sent), 1, "второй оборот отправил ВТОРОЕ сообщение")
            self.assertEqual(len(door.edited), 0, "правка без смены чисел")
            # ТРЕТИЙ ОБОРОТ, числа изменились → ПРАВКА того же сообщения.
            other = vp.parse_shtab("[[СЕЙЧАС]]\nчисла поехали")
            third = self._tick(root, door, shtab=other, now=NOW + 1800)
            self.assertEqual(len(door.sent), 1, "смена чисел родила второе сообщение")
            self.assertEqual(len(door.edited), 1, "смена чисел не поправила сообщение")
            self.assertEqual(third["message_id"], mid, "идентификатор сообщения не сохранён")
            self.assertEqual(third["how"], "правка")

    def test_failed_edit_waits_for_next_turn_and_sends_nothing(self):
        """Правка сорвалась → сказать и ждать. Нового сообщения НЕ шлём."""
        door = _Door()
        shtab = vp.parse_shtab("", ok=False, why="узла нет")
        with tempfile.TemporaryDirectory() as root:
            self._tick(root, door, shtab)
            door.edit_ok, door.edit_why = False, "code=400 Bad Gateway"
            rep = self._tick(root, door, shtab=vp.parse_shtab("[[СЕЙЧАС]]\nдругое"),
                             now=NOW + 900)
            self.assertFalse(rep["ok"])
            self.assertEqual(len(door.sent), 1, "сорванная правка родила второе сообщение")
            self.assertEqual(rep["how"], "не тронута")
            self.assertIn("правка не удалась", rep["why"])

    def test_new_message_only_when_telegram_says_it_is_gone(self):
        door = _Door()
        shtab = vp.parse_shtab("", ok=False, why="узла нет")
        with tempfile.TemporaryDirectory() as root:
            self._tick(root, door, shtab)
            door.edit_ok = False
            door.edit_why = "code=400 Bad Request: message to edit not found"
            rep = self._tick(root, door, shtab=vp.parse_shtab("[[СЕЙЧАС]]\nдругое"),
                             now=NOW + 900)
            self.assertTrue(rep["ok"])
            self.assertEqual(len(door.sent), 2, "потерянное сообщение не заведено заново")
            self.assertIn("новое сообщение", rep["how"])

    def test_edit_lost_is_narrow_on_purpose(self):
        self.assertTrue(vp.edit_lost("Bad Request: message to edit not found"))
        self.assertTrue(vp.edit_lost("code=400 MESSAGE_ID_INVALID"))
        self.assertTrue(vp.edit_lost("Bad Request: message can't be edited"))
        for temporary in ("Bad Gateway", "code=429 Too Many Requests", "URLError",
                          "Bad Request: message thread not found", ""):
            self.assertFalse(vp.edit_lost(temporary),
                             "временный отказ принят за потерю: %r" % temporary)

    def test_not_modified_counts_as_success(self):
        """Правка тем же текстом — успех: в теме стои́т ровно то, что мы хотели."""
        self.assertTrue(vp.edit_same("Bad Request: message is not modified"))
        door = _Door(edit_ok=False, edit_why="Bad Request: message is not modified")
        ok, mid, how, why = run.put("текст", 829, "9100", sender=door.send, editor=door.edit)
        self.assertTrue(ok)
        self.assertEqual(mid, "9100")
        self.assertEqual(how, "правка")
        self.assertEqual(len(door.sent), 0, "«не изменилось» родило новое сообщение")

    def test_unset_topic_sends_nothing(self):
        """Тема не настроена → наружу НЕ уходит ничего, и это сказано причиной."""
        door = _Door()

        class _NoTopic(object):
            AUDIT_TOPIC = 0

        with tempfile.TemporaryDirectory() as root:
            rep = run.tick(root=root, state=os.path.join(root, "st.json"), now=NOW, send=True,
                           runner=_git_none, sender=door.send, editor=door.edit,
                           daemon=_NoTopic(), shtab=vp.parse_shtab("", ok=False, why="нет"))
        self.assertFalse(rep["ok"])
        self.assertEqual(len(door.sent) + len(door.edited), 0)
        self.assertIn("НЕ НАСТРОЕНА", rep["why"].upper())


class TestShtabNode(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3: узла Штаба нет → витрина жива и говорит об этом."""

    def test_missing_node_does_not_break_render_and_is_visible(self):
        facts = _live_facts()
        facts["shtab"] = vp.parse_shtab("", ok=False, why="узел shtab_vitrina не прочитан: 404")
        text = vp.render(facts, "2026-09-02 12:40 UTC")
        self.assertEqual(text.count(vp.NO_SHTAB), 3, "не все три части сказали про Штаб")
        self.assertIn("404", text, "причина отказа узла не доехала до читающего")
        for title in (t for _k, t in vp.PARTS):
            self.assertIn(title, text)

    def test_reader_that_raises_is_not_an_exception_for_the_caller(self):
        def _boom(_ident):
            raise RuntimeError("мост молчит")

        got = run.read_shtab(reader=_boom, lister=_folder([_FILE]))
        self.assertFalse(got["ok"])
        self.assertIn("мост молчит", got["why"])

    # ── ИСТОЧНИК — ДОКУМЕНТ ПАПКИ, дорогой ящика (правка 04.09.2026) ──────────

    def test_words_come_from_the_folder_document_by_id(self):
        seen = {}

        def _reader(ident):
            seen["id"] = ident
            return "дата=2026-09-03\n[[СЕЙЧАС]]\nчиним рот витрины\n[[ЗАСТРЯЛО]]\nб\n[[КУДА ИДЁМ]]\nв"

        got = run.read_shtab(reader=_reader, lister=_folder([_FILE]))
        self.assertTrue(got["ok"], got["why"])
        self.assertEqual(seen["id"], _FILE["id"], "тело взято НЕ по file id из перечисления")
        self.assertEqual(got["day"], "2026-09-03")
        self.assertIn("чиним рот витрины", vp.shtab_words(got, "now"))

    def test_folder_is_asked_by_the_constant_name_and_id_is_never_hardcoded(self):
        asked = []

        got = run.read_shtab(reader=lambda _i: "[[СЕЙЧАС]]\nа",
                             lister=lambda p: asked.append(p) or _folder([_FILE])(p))
        self.assertTrue(got["ok"])
        self.assertEqual(asked, [vp.SHTAB_NODE], "папку спросили не именем из константы")
        src = _read_source("vitrina_pc_run.py") + _read_source("vitrina_pc.py")
        self.assertNotIn(_FILE["id"], src, "числовой адрес документа зашит в код")

    def test_unreadable_folder_says_why_instead_of_empty(self):
        def _dead(_p):
            raise RuntimeError("мост не ответил")

        got = run.read_shtab(lister=_dead)
        self.assertFalse(got["ok"])
        self.assertIn("мост не ответил", got["why"])
        self.assertIn(vp.NO_SHTAB, vp.shtab_words(got, "now"))

    def test_missing_document_says_why_instead_of_empty(self):
        got = run.read_shtab(lister=_folder([]))
        self.assertFalse(got["ok"])
        self.assertIn(vp.SHTAB_NODE, got["why"])
        self.assertIn("нет", got["why"])
        self.assertIn(vp.NO_SHTAB, vp.shtab_words(got, "stuck"))

    def test_two_documents_with_one_name_take_neither(self):
        twin = dict(_FILE, id="ID-второй")
        got = run.read_shtab(reader=lambda _i: "[[СЕЙЧАС]]\nа", lister=_folder([_FILE, twin]))
        self.assertFalse(got["ok"], "двойники по имени взяты — а который Штаба, неизвестно")
        self.assertIn("2 документа", got["why"])

    def test_prefix_neighbour_is_not_mistaken_for_the_document(self):
        near = {"name": vp.SHTAB_NODE + "_old", "id": "ID-соседа", "mime": "text/plain"}
        got = run.read_shtab(reader=lambda _i: "[[СЕЙЧАС]]\nа", lister=_folder([near]))
        self.assertFalse(got["ok"], "документом сочли соседа по началу имени")

    def test_empty_document_says_why_instead_of_empty(self):
        got = run.read_shtab(reader=lambda _i: "   ", lister=_folder([_FILE]))
        self.assertFalse(got["ok"])
        self.assertIn("пуст", got["why"])

    def test_truncated_listing_is_a_refusal_not_a_short_list(self):
        def _cut(_p):
            return {"ok": True, "files": [_FILE], "count": 1, "truncated": True}

        got = run.read_shtab(reader=lambda _i: "[[СЕЙЧАС]]\nа", lister=_cut)
        self.assertFalse(got["ok"], "усечённое перечисление принято за полное")
        self.assertIn("УСЕЧЕНО", got["why"])

    def test_missing_section_says_so_instead_of_empty(self):
        got = vp.parse_shtab("[[СЕЙЧАС]]\nработаем")
        self.assertTrue(got["ok"])
        self.assertIn("работаем", vp.shtab_words(got, "now"))
        self.assertIn(vp.NO_SHTAB, vp.shtab_words(got, "stuck"))

    def test_sections_are_parsed_with_the_stamp(self):
        got = vp.parse_shtab("дата=2026-09-02\n[[СЕЙЧАС]]\nа\n[[ЗАСТРЯЛО]]\nб\n"
                             "[[КУДА ИДЁМ]]\nв")
        self.assertEqual(got["day"], "2026-09-02")
        self.assertEqual(sorted(got["parts"]), ["goal", "now", "stuck"])

    def test_stale_words_carry_their_age(self):
        got = vp.parse_shtab("дата=2026-08-20\n[[СЕЙЧАС]]\nстарое")
        words = vp.shtab_words(got, "now", today="2026-09-02")
        self.assertIn("старое", words)
        self.assertIn("Штаб обновлял", words)

    def test_days_between_has_a_third_outcome(self):
        self.assertIsNone(vp.days_between("", "2026-09-02"))
        self.assertIsNone(vp.days_between("не дата", "2026-09-02"))
        self.assertEqual(vp.days_between("2026-09-01", "2026-09-02"), 1)


class TestParts(unittest.TestCase):
    """Семь частей, и часть без строк — падение, а не тихий пропуск."""

    def test_all_seven_parts_are_rendered(self):
        text = vp.render(_live_facts(), "2026-09-02 12:40 UTC")
        for _key, title in vp.PARTS:
            self.assertIn(title, text)
        self.assertEqual(len(vp.PARTS), 7)

    def test_order_is_owner_first_and_instruments_last(self):
        """РЕШЕНИЕ 2 ШТАБА 09.09: порядок задан пользой для владельца, а не привычкой.

        Сперва то, что ждёт его ОТВЕТА, и то, что застряло; длинные описания приборов — ниже
        всех. Прежний порядок был этому обратен, и цена замерена: при живом теле 4837 симв.
        раздел ЖДЁТ ВЛАДЕЛЬЦА не доезжал ни разу, держа при этом пять рядов до четырёх суток.
        """
        self.assertEqual(vp.PART_KEYS[0], "owner")
        self.assertEqual(vp.PART_KEYS[1], "stuck")
        self.assertEqual(vp.PART_KEYS[-1], "health")
        text = vp.render(_live_facts(), "?")
        self.assertLess(text.index("ЖДЁТ ВЛАДЕЛЬЦА"), text.index("ЗАСТРЯЛО"))
        self.assertLess(text.index("ЗАСТРЯЛО"), text.index("ЧТО РАБОТАЕТ И ЧТО НЕТ"))

    def test_empty_part_is_an_error_not_a_silent_skip(self):
        facts = _live_facts()
        facts["waiting"] = []
        self.assertIn("не ждёт ничего", vp.body(facts))
        saved = vp.part_owner
        try:
            vp.part_owner = lambda _w: []
            with self.assertRaises(AssertionError):
                vp.body(facts)
        finally:
            vp.part_owner = saved

    def test_numbers_part_carries_the_series_support(self):
        """Серия едет ВМЕСТЕ С ОПОРОЙ: «серия 18» без «доказал 5» врало бы молча."""
        text = vp.render(_live_facts(), "?")
        self.assertIn("серия: 5 из 30 подряд", text)
        self.assertIn("судья доказал 5", text)
        self.assertIn("без адреса 13", text)

    def test_series_target_is_borrowed_from_the_digest(self):
        self.assertIn("%d чистых цепочек подряд" % cd.SERIES_TARGET,
                      " ".join(vp.part_goal({"ok": False, "why": "нет"}, None)))

    def test_no_foreign_text_from_external_channels(self):
        """Чужой текст внешних каналов в витрину не переносится — только счёт и темы."""
        facts = _live_facts()
        facts["external"]["kinds"] = [("КРИТИЧЕСКИЙ ТЕКСТ РЕВЬЮЕРА", 2)]
        self.assertNotIn("КРИТИЧЕСКИЙ ТЕКСТ РЕВЬЮЕРА", vp.body(facts))

    def test_message_fits_telegram(self):
        facts = _live_facts()
        facts["waiting"] = [{"id": str(i), "goal": "ц" * 200} for i in range(60)]
        text = vp.render(facts, "?")
        self.assertLessEqual(len(text), vp.TEXT_MAX + 80)

    def test_running_without_claim_stamp_says_so(self):
        rows = vp.part_now({"ok": False, "why": "нет"}, [{"id": "5", "goal": "ц", "age": None}])
        self.assertIn("НЕИЗВЕСТНО", " ".join(rows))
        self.assertIn("отметки claim нет", " ".join(rows))


class TestWindow(unittest.TestCase):
    """ОКНО СООБЩЕНИЯ: режем ЦЕЛЫМИ разделами и объявляем себя (решение 3 Штаба 09.09).

    Сюда же ОТРИЦАТЕЛЬНЫЙ ТЕСТ 6 задания, и он здесь главный: искусственно раздутая
    машинная часть обязана дать сообщение, которое САМО говорит, что обрезано и чего не
    хватает, — а тот же вход при СНЯТОЙ гарантии обязан дать другой ответ.
    """

    @staticmethod
    def _fat(chars=3800):
        """Факты с искусственно РАЗДУТОЙ машинной частью. → dict.

        Раздувается остановка ящика: это единственная строка витрины без своего потолка
        (она приходит готовой фразой из метки оборота демона), и потому единственная,
        которой переполнение делается честно — не подгонкой потолка под желаемое число.
        """
        facts = _live_facts()
        facts["box_stop"] = "ящик остановлен: " + "с" * int(chars)
        return facts

    @staticmethod
    def _before_note(text):
        """Всё, что стои́т ДО объявления обрезки: сами разделы, без их имён в объявлении."""
        return text.split(vp.CUT_MARK)[0]

    def test_calm_turn_fits_and_claims_no_cutting(self):
        text, cut, lost = vp.message(_live_facts(), "?")
        self.assertLessEqual(len(text), vp.TEXT_MAX)
        self.assertEqual((cut, lost), ([], 0))
        self.assertNotIn(vp.CUT_MARK, text, "спокойный оборот объявил несуществующую обрезку")
        for _key, title in vp.PARTS:
            self.assertIn(title, text)

    def test_cut_takes_whole_sections_never_half(self):
        """Раздел либо показан ЦЕЛИКОМ, либо снят. Оборванный читается как полный."""
        facts = self._fat()
        text, cut, _lost = vp.message(facts, "?")
        self.assertLessEqual(len(text), vp.TEXT_MAX)
        self.assertTrue(cut, "раздутая машинная часть не вызвала обрезки — вход слаб")
        for title, block in vp.sections(facts, blind=False):
            if title in cut:
                self.assertNotIn(title, self._before_note(text),
                                 "снятый раздел %s всё же попал в текст кусками" % title)
            else:
                self.assertIn(block, text, "раздел %s доехал НЕ ЦЕЛИКОМ" % title)

    def test_cut_names_the_number_and_every_missing_section(self):
        """РЕШЕНИЕ 3: сказать ЧИСЛОМ, сколько не поместилось, и НАЗВАТЬ снятые разделы."""
        text, cut, lost = vp.message(self._fat(), "?")
        self.assertIn(vp.CUT_MARK, text)
        self.assertGreater(lost, 0)
        self.assertIn("не поместилось %d симв." % lost, text, "число потери не названо")
        for title in cut:
            self.assertIn(title, text, "снятый раздел %s не назван — обрезка молчит" % title)
        self.assertIn(vp.FULL_PLACE, text, "не сказано, где читать снятое")

    def test_no_section_disappears_without_a_word_about_itself(self):
        """ПУНКТ 5 задания на ПЯТИ раздутиях: третьего исхода нет ни на одном."""
        for fat in (0, 900, 2400, 3800, 9000):
            facts = self._fat(fat) if fat else _live_facts()
            text, cut, _lost = vp.message(facts, "?")
            self.assertLessEqual(len(text), vp.TEXT_MAX, "окно пробито на раздутии %d" % fat)
            for title, block in vp.sections(facts, blind=False):
                whole, named = block in text, (title in cut and title in text)
                self.assertTrue(whole or named,
                                "раздел %s пропал МОЛЧА при раздутии %d" % (title, fat))
                self.assertNotEqual(whole, named,
                                    "раздел %s и показан, и объявлен снятым (%d)" % (title, fat))

    def test_negative_inflated_machine_part_must_announce_what_is_missing(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ 6 задания, главный.

        «Снятая гарантия» — в точности прежний нож: склеить всё и отрезать по символу на
        потолке. Он ТОЖЕ печатал строку об обрезке, поэтому сверяется не наличие слова, а
        ровно то, ради чего заведено решение 3: названо ли ЧИСЛО и названы ли ИМЕНА.
        """
        facts = self._fat()
        text, cut, lost = vp.message(facts, "?")

        # ГАРАНТИЯ НА МЕСТЕ: обрезка названа числом и именами, разделы целы.
        self.assertTrue(cut)
        self.assertIn("не поместилось %d симв." % lost, text)
        for title in cut:
            self.assertIn(title, text)
        self.assertEqual([t for t, b in vp.sections(facts, blind=False)
                          if t in self._before_note(text) and b not in text], [],
                         "новый нож порвал раздел посередине")

        # ГАРАНТИЯ СНЯТА: тот же вход, прежний нож по символу — и ответ ДРУГОЙ по трём
        # признакам сразу, каждый из которых и есть предмет решения 3.
        naked = "\n".join([vp.HEAD, "числа менялись последний раз: ?", "",
                           vp.body(facts, blind=True), "", vp.FOOT])[:vp.TEXT_MAX]
        self.assertNotEqual(naked, text, "гарантия ничего не поменяла — тест не сторожит ничего")
        torn = [t for t, b in vp.sections(facts, blind=True) if t in naked and b not in naked]
        self.assertTrue(torn, "прежний нож не порвал раздела посередине — вход слаб")
        silent = [t for t in cut if t not in naked]
        self.assertTrue(silent, "прежний нож не потерял МОЛЧА ни одного раздела")
        self.assertNotIn("не поместилось", naked, "прежний нож всё-таки называл потерю числом")

    def test_owner_section_survives_the_worst_inflation(self):
        """ПРОДУКТ задания: раздел, ждущий ОТВЕТА владельца, доезжает, пока влезает хоть один."""
        text, cut, _lost = vp.message(self._fat(3800), "?")
        self.assertNotIn("ЖДЁТ ВЛАДЕЛЬЦА", cut)
        self.assertIn("#3 ЗАЯВКА внешнего канала", text)

    def test_announcement_survives_even_when_nothing_else_does(self):
        """Вырожденная ветка: окно меньше шапки с подвалом — режется ГОЛОВА, не объявление."""
        text, cut, lost = vp.message(_live_facts(), "?", limit=400)
        self.assertLessEqual(len(text), 400)
        self.assertIn(vp.CUT_MARK, text, "молчаливая обрезка на вырожденном окне")
        self.assertEqual(len(cut), len(vp.PARTS))
        self.assertGreater(lost, 0)

    def test_lost_counts_the_silence_not_the_announcement(self):
        """Число потери — цена МОЛЧАНИЯ: объявление из неё не вычитается."""
        blocks = [("А", "А\n" + "а" * 400), ("Б", "Б\n" + "б" * 400)]
        text, cut, lost = vp.fit_message("ш", blocks, "п", limit=600)
        self.assertEqual(cut, ["Б"])
        self.assertEqual(lost, len("\n\n") + len(blocks[1][1]))
        self.assertLessEqual(len(text), 600)
        self.assertGreater(len(text), 600 - len(vp.cut_words(lost, cut)),
                           "объявление вычли из потери — потеря занижена")

    def test_signature_watches_the_full_body_not_the_window(self):
        """Подпись по окну не заметила бы смены в снятом разделе — витрина стояла бы молча."""
        one, two = self._fat(), self._fat()
        two["health"] = [dict(n, blind="слепота стала другой") for n in one["health"]]
        self.assertNotEqual(vp.signature(one), vp.signature(two))
        self.assertEqual(vp.message(one, "?")[0], vp.message(two, "?")[0])


class TestBlindTailsMoved(unittest.TestCase):
    """РЕШЕНИЕ 4 ШТАБА 09.09: хвосты «не покрывает» ПЕРЕНЕСЕНЫ, а не удалены.

    Перенос считается состоявшимся только при трёх вещах разом: в окне их нет, место
    названо словами, по которым их найдут, и в этом месте они ЛЕЖАТ ДОСЛОВНО.
    """

    def test_window_has_no_tails_but_names_where_they_went(self):
        text = vp.render(_live_facts(), "?")
        self.assertNotIn(" %s: " % vp.BLIND_WORDS, text, "хвосты остались в окне")
        self.assertIn(vp.BLIND_WORDS, text, "слов, по которым их найдут, в окне нет")
        self.assertIn(vp.FULL_PLACE, text, "место переноса не названо")

    def test_full_text_carries_every_tail_verbatim(self):
        facts = _live_facts()
        full = vp.full_text(facts, "?")
        tails = [n.get("blind") for n in facts["health"] if n.get("blind")]
        self.assertGreaterEqual(len(tails), 4, "фикстура без хвостов ничего не сторожит")
        for tail in tails:
            self.assertIn(tail, full, "хвост потерян при переносе: %s" % tail)
        self.assertNotIn(vp.CUT_MARK, full, "полный текст сам обрезан — переносить некуда")
        for _key, title in vp.PARTS:
            self.assertIn(title, full)

    def test_tail_is_on_by_default_so_forgetting_the_key_says_more(self):
        node = {"name": "узел", "said": ex.TURN_OK, "stale": False, "src": "источник",
                "age": "1 мин", "blind": "слепота прибора"}
        self.assertIn("слепота прибора", vp.health_row(node))
        self.assertNotIn("слепота прибора", vp.health_row(node, blind=False))

    def test_pointer_line_only_where_there_are_nodes_to_point_at(self):
        """Указывать на слепоту приборов, которых не собрали, — обещать несуществующий текст."""
        self.assertIn(vp.BLIND_MOVED, vp.part_health(_health_nodes(), blind=False))
        self.assertNotIn(vp.BLIND_MOVED, vp.part_health(None, blind=False))
        self.assertNotIn(vp.BLIND_MOVED, vp.part_health([], blind=False))

    def test_moving_the_tails_buys_room_and_the_pointer_costs_less(self):
        """Перенос обязан быть ВЫГОДНЫМ: указатель дешевле того, что он заменил.

        Фикстура здесь МЕНЬШЕ боевой (4 узла с короткими хвостами против шести живых), и
        числа честно разные: на ней перенос освобождает 128 симв., на живом теле оборота
        09.09 — 597 симв. хвостов против одной строки указателя. Порог взят по фикстуре, а
        не по боевому замеру: тест обязан падать на СВОЁМ корпусе, а не на чужом.
        """
        facts = _live_facts()
        gross = sum(len(" · %s: %s" % (vp.BLIND_WORDS, n["blind"]))
                    for n in facts["health"] if n.get("blind"))
        net = len(vp.body(facts, blind=True)) - len(vp.body(facts, blind=False))
        self.assertGreater(gross, len(vp.BLIND_MOVED), "указатель дороже того, что он заменил")
        self.assertGreater(net, 100, "перенос не освободил окна — незачем было переносить")

    def test_pulse_gets_the_full_text_and_the_topic_gets_the_window(self):
        """Перенос состоялся только если полный текст ДЕЙСТВИТЕЛЬНО уехал в пульс."""
        door, seen = _Door(), []
        with tempfile.TemporaryDirectory() as root:
            rep = run.tick(root=root, state=os.path.join(root, "st.json"), now=NOW, send=True,
                           pulse=True, runner=_git_none, sender=door.send, editor=door.edit,
                           daemon=_Daemon(), shtab=vp.parse_shtab("", ok=False, why="нет"),
                           pulser=lambda t: seen.append(t) or {"ok": True})
        self.assertTrue(rep["ok"], rep.get("why"))
        self.assertEqual(len(seen), 1)
        self.assertIn(vp.FULL_HEAD, seen[0], "в пульс уехал не полный текст")
        self.assertNotEqual(seen[0], rep["text"], "в пульс уехало окно, а не оригинал")
        self.assertNotIn(vp.FULL_HEAD, rep["text"])


class TestAxes(unittest.TestCase):
    """Три оси рамки: обслуживание — ОСТАТОК, а не третий список путей."""

    def test_service_is_the_remainder(self):
        got = vp.axes_tally(["a", "b", "c", "d"], ["a", "b"], ["c"])
        self.assertEqual(got[vp.AXIS_MAIN], 2)
        self.assertEqual(got[vp.AXIS_BIZ], 1)
        self.assertEqual(got[vp.AXIS_SERVICE], 1)

    def test_commit_on_two_axes_counts_in_both(self):
        got = vp.axes_tally(["a"], ["a"], ["a"])
        self.assertEqual(got[vp.AXIS_MAIN], 1)
        self.assertEqual(got[vp.AXIS_BIZ], 1)
        self.assertEqual(got[vp.AXIS_SERVICE], 0, "заход обеих осей попал ещё и в остаток")

    def test_partial_corpus_gives_unknown_in_all_three(self):
        for bad in (vp.axes_tally(None, ["a"], []), vp.axes_tally(["a"], None, []),
                    vp.axes_tally(["a"], [], None)):
            self.assertEqual(set(bad.values()), {None},
                             "неполный корпус выдан за полное число")

    def test_axes_words_names_the_border_with_a_second_number(self):
        words = vp.axes_words({vp.AXIS_MAIN: 4, vp.AXIS_BIZ: 0, vp.AXIS_SERVICE: 2}, 18)
        self.assertIn("след — коммит", words)
        self.assertIn("строк очереди закрылось за сутки: 18", words)

    def test_dead_git_gives_unknown_not_zero(self):
        words = vp.axes_words(vp.axes_tally(None, None, None), None, "git не ответил")
        self.assertEqual(words.count(vp.UNKNOWN), 4)
        self.assertIn("git не ответил", words)

    def test_hashes_keep_the_third_outcome(self):
        self.assertIsNone(run._hashes(None))
        self.assertEqual(run._hashes(["abc тема", "def другая", ""]), {"abc", "def"})


class TestSignature(unittest.TestCase):
    """Пункт 4: правка идёт не чаще, чем меняются числа."""

    def test_same_facts_same_signature(self):
        self.assertEqual(vp.signature(_live_facts()), vp.signature(_live_facts()))

    def test_changed_number_changes_signature(self):
        other = _live_facts()
        other["awaiting"] = {"holding": 1, "asking": 2, "blind": 0}
        self.assertNotEqual(vp.signature(_live_facts()), vp.signature(other))

    def test_signature_has_no_timestamp_in_it(self):
        """Штампа СБОРКИ в подписи нет — иначе витрина правила бы себя каждый оборот.

        До 03.09 тест ловил это запретом слова «UTC» во всём теле. Запрет был
        ШИРЕ предмета и промахивался в обе стороны: он отвергал ВРЕМЯ СОБЫТИЯ
        (когда закрылась строка, когда пришло задание Штаба) — число как всякое
        другое, меняющееся только вместе с событием, — и при этом ничего не
        говорил о настоящем источнике мигания, НЕПРЕРЫВНОМ ВОЗРАСТЕ. Теперь
        спрошено ровно то, что защищаем: подпись не зависит от часов сборки, а
        возраст не двигает её каждый виток (`test_idle_does_not_repaint…`).
        """
        stamp = "2026-09-02 12:40 UTC"
        sig = vp.signature(_live_facts())
        self.assertNotIn(stamp, sig, "штамп сборки просочился в подпись")
        self.assertNotIn("числа менялись", sig)
        self.assertIn(stamp, vp.render(_live_facts(), stamp))
        self.assertEqual(sig, vp.signature(_live_facts()))

    def test_day_start_has_a_third_outcome(self):
        self.assertIsNone(run.day_start("не дата"))
        self.assertEqual(run.day_start("1970-01-02"), 86400.0)


class TestPulse(unittest.TestCase):
    """Узел пульса витрину не роняет и в чужие узлы не пишет."""

    def test_pulse_failure_is_a_line_not_a_crash(self):
        def _boom(_text):
            raise RuntimeError("мозг молчит")

        ok, why = run.write_pulse("текст", writer=_boom)
        self.assertFalse(ok)
        self.assertIn("мозг молчит", why)
        self.assertIn(run.PULSE_KEY, why)

    def test_pulse_writes_the_same_text(self):
        seen = []
        ok, why = run.write_pulse("витрина", writer=lambda t: seen.append(t) or {"ok": True})
        self.assertTrue(ok, why)
        self.assertEqual(seen, ["витрина"])

    def test_pulse_runs_only_after_the_topic_took_the_text(self):
        """Пульс — ВТОРОЙ адрес, а не первый: сорванный показ его не зовёт."""
        door = _Door(send_ok=False)
        seen = []
        with tempfile.TemporaryDirectory() as root:
            rep = run.tick(root=root, state=os.path.join(root, "st.json"), now=NOW, send=True,
                           pulse=True, runner=_git_none, sender=door.send, editor=door.edit,
                           daemon=_Daemon(), shtab=vp.parse_shtab("", ok=False, why="нет"),
                           pulser=lambda t: seen.append(t))
        self.assertFalse(rep["ok"])
        self.assertEqual(seen, [], "пульс лёг при несостоявшемся показе")


class TestLiveTree(unittest.TestCase):
    """Живое дерево: сборка не падает и на пустом корне, и на боевом."""

    def test_empty_root_gives_a_full_message_of_unknowns(self):
        with tempfile.TemporaryDirectory() as root:
            facts = run.collect(root=root, now=NOW, runner=_git_none,
                                shtab=vp.parse_shtab("", ok=False, why="узла нет"))
        text = vp.render(facts, "?")
        for _key, title in vp.PARTS:
            self.assertIn(title, text)
        self.assertIn(vp.UNKNOWN, text)

    def test_state_file_is_the_only_thing_written(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "st.json")
            ok, why = run.write_state({"message_id": "9100", "sig": "x"}, path)
            self.assertTrue(ok, why)
            with io.open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["message_id"], "9100")

    def test_claims_read_both_shapes(self):
        with tempfile.TemporaryDirectory() as root:
            with io.open(os.path.join(root, run.CLAIM_FILE), "w", encoding="utf-8") as fh:
                json.dump({"1": "2026-09-02T12:00:00+00:00",
                           "2": {"at": "2026-09-02T12:30:00+00:00", "pid": 1},
                           "3": {"pid": 2}}, fh)
            got = run.read_claims(root)
        self.assertEqual(sorted(got), ["1", "2"], "старая форма отметки claim потеряна")

    def test_foreign_goal_goes_through_the_outbound_guard(self):
        """Абсолютный путь в цели строки чинится ПОСТРОЧНО, а не глушит витрину."""
        snap = {"open": {"7": {"goal": "правка C:\\Users\\mxfill1\\secret.txt",
                               "status": "in_progress"}}, "closed": {}}
        rows = run.running_rows(snap, {}, NOW)
        self.assertNotIn("C:\\Users", rows[0]["goal"])


class TestZayavkiAreNotCards(unittest.TestCase):
    """Пункт 5 задания: слово «карточки» остаётся ТОЛЬКО за карточками гарда.

    Повод замерен 02.09.2026: в очереди три ряда `needs_approval`, все три —
    заявки внешних каналов, а витрина печатала «карточки в ожидании: 3». Одно
    слово на две новости с разной срочностью — неверное число, а не стиль.
    """

    SNAP = {"open": {
        "1": {"goal": "[заявка-ревью дата=2026-09-02 ключ=fb36e990a3b5]", "status": "needs_approval"},
        "2": {"goal": "[заявка-ревью дата=2026-09-02 ключ=98c2c8bf1be6]", "status": "needs_approval"},
        "3": {"goal": "[заявка-ревью дата=2026-09-02 ключ=47b3131c052c]", "status": "needs_approval"},
        "9": {"goal": "удаление tmp/x — разрешить?", "status": "needs_approval"},
        "10": {"goal": "работа", "status": "in_progress"},
    }, "closed": {}}

    # ЖИВОЙ КОРПУС 03.09: четыре заявки разведки и одна внешнего канала, карточек
    # гарда НОЛЬ. До правки того же дня витрина звала четыре разведзаявки
    # «карточками гарда» — то есть сообщала о четырёх красных операциях, которых
    # не было ни одной.
    LIVE_SNAP = {"open": {
        "13": {"goal": "[разведка-заявка дата=2026-09-02 ключ=38e6d0ce6598]", "status": "needs_approval"},
        "31": {"goal": "[разведка-заявка дата=2026-09-02 ключ=8741233058f0]", "status": "needs_approval"},
        "39": {"goal": "[разведка-заявка дата=2026-09-03 ключ=85cdcb57eb06]", "status": "needs_approval"},
        "40": {"goal": "[разведка-заявка дата=2026-09-03 ключ=d5bb46caa17f]", "status": "needs_approval"},
        "79": {"goal": "[заявка-ревью дата=2026-09-03 ключ=c30d8b2ab7e8]", "status": "needs_approval"},
    }, "closed": {}}

    def test_live_snapshot_counts_them_apart(self):
        rows = run.waiting_rows(self.SNAP)
        self.assertEqual(run.awaiting_counts(rows),
                         {"holding": 1, "asking": 3, "blind": 0})

    def test_recon_claims_are_no_longer_called_guard_cards(self):
        """ЖИВОЙ КОРПУС 03.09: держащих НОЛЬ, ждущих мнения ПЯТЬ.

        Прежний признак (только маркер ступени B) дал бы здесь «карточки гарда: 4».
        """
        counts = run.awaiting_counts(run.waiting_rows(self.LIVE_SNAP))
        self.assertEqual({"holding": 0, "asking": 5, "blind": 0}, counts)
        text = "\n".join(vp.part_nums(None, None, DAY, None, counts))
        self.assertIn("ДЕРЖАТ операцию (карточки гарда): 0", text)
        self.assertIn("ЖДУТ МНЕНИЯ (заявки, ящик не держат): 5", text)

    def test_an_unreadable_row_is_counted_as_holding_and_named_so(self):
        """Вид не определён — считаем держащим, но говорим об этом ОТДЕЛЬНЫМ словом."""
        snap = {"open": {"77": {"goal": "", "status": "needs_approval"}}, "closed": {}}
        counts = run.awaiting_counts(run.waiting_rows(snap))
        self.assertEqual({"holding": 1, "asking": 0, "blind": 1}, counts)
        text = "\n".join(vp.part_nums(None, None, DAY, None, counts))
        self.assertIn("неопознанным видом ряда", text)

    def test_unknown_survives_as_unknown(self):
        self.assertIsNone(run.waiting_rows(None))
        self.assertIsNone(run.awaiting_counts(None))

    def test_numbers_name_two_things_by_two_names(self):
        lines = vp.part_nums(None, None, DAY, None, run.awaiting_counts(run.waiting_rows(self.SNAP)))
        text = "\n".join(lines)
        self.assertIn("ДЕРЖАТ операцию (карточки гарда): 1", text)
        self.assertIn("ЖДУТ МНЕНИЯ (заявки, ящик не держат): 3", text)
        self.assertNotIn("карточки в ожидании:", text)   # прежнего общего слова больше нет

    def test_the_unknown_count_stands_next_to_the_series_even_at_zero(self):
        """ТРЕТИЙ ИСХОД В ВИТРИНЕ (11.09.2026, п. 3 решения Штаба).

        Число печатается ВСЕГДА, в том числе нулём: исход, спрятанный при нуле,
        читается как «такого не бывает» — ровно так «неизвестно» и жило до правки.
        """
        counted = {"streak": 5, "target": 30, "proved": 5, "blind": 0, "unproved": 0,
                   "unjudged": 0, "moved": 2, "window": 10, "unknown": 0, "denom": 10,
                   "alarm": False}
        text = "\n".join(vp.part_nums(counted, 1, DAY, None, None))
        self.assertIn("серия: 5 из 30 подряд", text)
        self.assertIn("НЕИЗВЕСТНО 0 из 10", text, "нулевое число спрятано")
        self.assertNotIn("ТРЕВОГА", text, "тревога при нуле неизвестных")

    def test_the_share_above_one_fifth_is_an_alarm_about_the_judge(self):
        """Доля выше пятой части звучит ТРЕВОГОЙ и называет, кого чинить — СУДЬЮ.

        Слова берутся у сводки: разойдись два показа одного числа — владелец
        получил бы две новости об одном событии и не узнал бы, какая верна."""
        counted = {"streak": 3, "target": 30, "proved": 3, "blind": 0, "unproved": 0,
                   "unjudged": 0, "moved": 0, "window": 4, "unknown": 1, "denom": 3,
                   "alarm": True}
        text = "\n".join(vp.part_nums(counted, 1, DAY, None, None))
        self.assertIn(cd.unknown_words(counted), text, "витрина сочинила свои слова")
        self.assertIn("ТРЕВОГА ПРО СУДЬЮ", text)
        self.assertIn("не про работу", text)

    def test_an_unreadable_snapshot_gives_unknown_not_zero(self):
        """Слепка нет → «НЕИЗВЕСТНО (причина)», и НИКОГДА не ноль (закон витрины)."""
        text = "\n".join(vp.part_nums(None, None, DAY, None, None))
        self.assertIn("неизвестных: %s" % vp.UNKNOWN, text)
        self.assertNotIn("неизвестных: 0", text)

    def test_a_real_failure_is_still_shown_as_a_failure(self):
        """ОТРИЦАТЕЛЬНЫЙ. Настоящее падение остаётся падением и в витрине тоже.

        И вторая половина того же замера: неизвестная строка из «упало за сутки»
        УХОДИТ (`failed_rows` берёт только `outcome == "failed"`) — то есть без
        строки неизвестных рядом с серией она не была бы видна владельцу нигде.
        Ровно эту дыру закрывает проверка выше."""
        snap = {"open": {}, "closed": {
            "244": {"id": "244", "at": 100.0, "goal": "цель", "outcome": "failed",
                    "why": "⏱ таймаут 45 мин"},
            "245": {"id": "245", "at": 101.0, "goal": "цель", "outcome": "unknown",
                    "why": "V0: UNKNOWN / sensitive_content по адресу «…-1009.md»"}}}
        rows = run.failed_rows(snap, 0.0)
        self.assertEqual([r["id"] for r in rows], ["244"], "исходы слиплись в один")
        text = "\n".join(vp.part_stuck({}, [], rows, DAY))
        self.assertIn("упало за сутки 1", text)
        self.assertNotIn("245", text, "неизвестность показана падением")

    def test_the_showcase_and_the_box_call_one_row_by_one_word(self):
        """Различитель ОДИН: витрина и остановка ящика не вправе разойтись."""
        rows = run.waiting_rows(self.LIVE_SNAP)
        live = [{"id": w["id"], "status": "needs_approval", "goal": w["goal"]} for w in rows]
        self.assertEqual(run.awaiting_counts(rows)["holding"],
                         len(sbs.holding(sbs.split_waiting(live))))

    def test_owner_list_shows_a_zayavka_by_its_own_name_not_by_marker(self):
        rows = vp.part_owner(run.waiting_rows(self.SNAP))
        text = "\n".join(rows)
        self.assertIn("ЗАЯВКА внешнего канала (ключ 47b3131c052c)", text)
        self.assertIn("задачей не станет", text)
        self.assertNotIn("[заявка-ревью", text)          # машинный маркер владельцу не показываем
        self.assertIn("#9 ДЕРЖИТ операцию: удаление tmp/x — разрешить?", text)


class TestIdleAndShtabArrival(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЕ ТЕСТЫ задания 03.09: молчание петли видно ЧИСЛОМ.

    Повод: петлю замыкает внешний агент, который будит Штаб по пустой очереди.
    Пока прибора нет, его молчание и настоящая тишина полосы выглядят одинаково —
    пустой очередью, читающейся как спокойствие. Здесь закрыты все четыре случая
    задания плюс пятый, без которого порог был бы украшением: пусто, но КОРОЧЕ
    порога — тревоги нет.
    """

    # Слепок живой формы: пять `needs_approval` (владелец не ответил) и НИ ОДНОЙ
    # строки, которую полоса может взять. Ровно это состояние стояло в очереди
    # 03.09 замером — и до правки витрина звала его «полоса свободна».
    IDLE_SNAP = {"open": {
        "13": {"goal": "[разведка-заявка дата=2026-09-02 ключ=38e6d0ce6598]",
               "status": "needs_approval"},
        "31": {"goal": "[заявка-ревью дата=2026-09-02 ключ=8741233058f0]",
               "status": "needs_approval"},
    }, "closed": {
        "84": {"id": "84", "at": NOW - 5 * 3600.0, "outcome": "done", "goal": "прошлая работа"},
        "80": {"id": "80", "at": NOW - 9 * 3600.0, "outcome": "done", "goal": "ещё прошлее"},
    }}

    BUSY_SNAP = {"open": {
        "85": {"goal": "витрина видит простой", "status": "in_progress"},
        "13": {"goal": "заявка", "status": "needs_approval"},
    }, "closed": dict(IDLE_SNAP["closed"])}

    # ── 1. очередь занята — строка про простой НЕ тревожит ──────────────────
    def test_busy_queue_never_alarms(self):
        idle = run.idle_facts(self.BUSY_SNAP, NOW)
        self.assertEqual(idle["busy"], 1, "занятость посчитана не по рабочим статусам")
        self.assertEqual(idle["ids"], ["85"], "в занятость попал ряд, ждущий владельца")
        self.assertEqual(vp.idle_alarm(idle, _shtab_last(99 * 3600.0)), "",
                         "тревога сработала при занятой очереди")
        words = vp.idle_words(idle)
        self.assertIn("очередь НЕ пуста", words)
        self.assertIn("#85", words)

    def test_needs_approval_is_not_work(self):
        """Ряд, ждущий владельца, занятостью НЕ считается — иначе простоя не будет НИКОГДА.

        Замер 03.09: в очереди 5 таких рядов, старшему 793 минуты. Считай мы их
        работой, строка простоя молчала бы вечно, оставаясь при этом «зелёной».
        """
        self.assertNotIn("needs_approval", vp.WORK_STATUSES)
        idle = run.idle_facts(self.IDLE_SNAP, NOW)
        self.assertEqual(idle["busy"], 0, "пять заявок владельца выданы за работу полосы")

    # ── 2. пусто дольше порога — ТРЕВОЖИТ ──────────────────────────────────
    def test_idle_over_threshold_alarms_with_numbers(self):
        idle = run.idle_facts(self.IDLE_SNAP, NOW)                 # пусто 5 ч при пороге 4
        self.assertAlmostEqual(idle["sec"], 5 * 3600.0, places=3)
        self.assertEqual(idle["since_id"], "84")
        alarm = vp.idle_alarm(idle, _shtab_last(None, found=False), DAY)
        self.assertTrue(alarm, "простой дольше порога прошёл молча")
        self.assertIn("ТРЕВОГА", alarm)
        self.assertIn("5.0 ч", alarm)                              # ЧИСЛОМ, а не «давно»
        self.assertIn("4.0 ч", alarm)                              # порог назван в самой строке
        self.assertIn("ни одного задания", alarm)                  # и приход Штаба тоже
        self.assertNotIn("давно", alarm)

    def test_alarm_is_a_separate_line_in_the_showcase(self):
        """Тревога — ОТДЕЛЬНАЯ строка витрины, а не приписка к строке простоя."""
        facts = _live_facts()
        facts["idle"] = _idle(6 * 3600.0)
        facts["shtab_last"] = _shtab_last(None, found=False)
        rows = vp.part_now(facts["shtab"], [], DAY, idle=facts["idle"],
                           shtab_last=facts["shtab_last"])
        alarms = [r for r in rows if r.startswith("ТРЕВОГА")]
        self.assertEqual(len(alarms), 1, "тревога не отдельной строкой: %s" % rows)
        self.assertIn("ТРЕВОГА", vp.body(facts))

    def test_idle_under_threshold_is_silent(self):
        """Пусто, но КОРОЧЕ порога — тревоги нет, а число всё равно показано."""
        idle = _idle(vp.IDLE_ALARM_SEC - 60.0)
        self.assertEqual(vp.idle_alarm(idle, _shtab_last(99 * 3600.0)), "")
        self.assertIn("очередь пуста", vp.idle_words(idle))
        self.assertIn("порог тревоги 4.0 ч", vp.idle_words(idle))

    # ── 3. слепок не прочитан — НЕИЗВЕСТНО, и оно НЕ тревога ───────────────
    def test_unread_snapshot_says_unknown_and_never_alarms(self):
        self.assertIsNone(run.idle_facts(None, NOW))
        self.assertIsNone(run.shtab_last_facts(None, {}, NOW))
        self.assertIn(vp.UNKNOWN, vp.idle_words(None))
        self.assertIn("слепок очереди не прочитан", vp.idle_words(None))
        self.assertIn(vp.UNKNOWN, vp.shtab_last_words(None))
        self.assertEqual(vp.idle_alarm(None, None), "", "незнание превращено в тревогу")

    def test_empty_closed_registry_is_unknown_not_zero(self):
        """Пусто, но с какого времени — не из чего взять: НЕИЗВЕСТНО, а не «0 мин»."""
        idle = run.idle_facts({"open": {}, "closed": {}}, NOW)
        self.assertEqual(idle["busy"], 0)
        self.assertIsNone(idle["sec"])
        words = vp.idle_words(idle)
        self.assertIn(vp.UNKNOWN, words)
        self.assertNotIn("пуста 0", words)
        self.assertEqual(vp.idle_alarm(idle, None), "")

    # ── 4. задание из ящика только что взято — счётчик обнуляется ──────────
    def test_fresh_shtab_task_resets_both_counters(self):
        """Ящик положил задание, демон его взял → простоя нет, приход свежий."""
        goal = "[от Штаба дата=2026-09-02 ключ=a1b2c3d4] задание"
        snap = {"open": {"90": {"goal": goal, "status": "in_progress"}},
                "closed": dict(self.IDLE_SNAP["closed"])}
        claims = {"90": NOW - 120.0}
        idle = run.idle_facts(snap, NOW)
        last = run.shtab_last_facts(snap, claims, NOW)
        self.assertEqual(idle["busy"], 1, "взятое задание не обнулило простой")
        self.assertEqual(vp.idle_alarm(idle, last), "", "тревога при только что взятом задании")
        self.assertTrue(last["found"])
        self.assertEqual((last["id"], last["day"], last["key"], last["at_kind"]),
                         ("90", "2026-09-02", "a1b2c3d4", "claim"))
        self.assertAlmostEqual(last["sec"], 120.0, places=3)
        self.assertIn("2 мин назад", vp.shtab_last_words(last, DAY))

    def test_newest_marker_wins_over_older_ones(self):
        snap = {"open": {}, "closed": {
            "70": {"id": "70", "at": NOW - 40 * 3600.0, "outcome": "done",
                   "goal": "[от Штаба дата=2026-09-01 ключ=oldoldold] старое"},
            "77": {"id": "77", "at": NOW - 20 * 3600.0, "outcome": "done",
                   "goal": "[от Штаба дата=2026-09-02 ключ=newnewnew] новое"}}}
        last = run.shtab_last_facts(snap, {}, NOW)
        self.assertEqual(last["key"], "newnewnew")
        self.assertEqual(last["at_kind"], "close", "часа нет — источник времени не назван")
        self.assertIn("закрылось", vp.shtab_last_words(last, DAY))

    def test_no_marker_anywhere_names_the_border_not_a_zero(self):
        """Ни одного задания в реестре — это ОКНО, а не история, и так и сказано.

        Живой замер 03.09: в реестре 6 открытых + 71 закрытая строка и РОВНО НОЛЬ
        рядов с маркером ящика. Первая же боевая печать строки — тот самый случай.
        """
        last = run.shtab_last_facts(self.IDLE_SNAP, {}, NOW)
        self.assertFalse(last["found"])
        self.assertEqual(last["scope"], "в реестре 4 строк")
        words = vp.shtab_last_words(last, DAY)
        self.assertIn("нет ни одного", words)
        self.assertIn("окно, а не история", words)

    def test_marker_regex_is_borrowed_from_the_box(self):
        """Своей регулярки маркера здесь нет: два экземпляра разошлись бы молча."""
        with io.open(os.path.join(HERE, "vitrina_pc_run.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("shtab_box.MARK_RE", body)
        self.assertNotIn("от Штаба дата=(", body, "маркер ящика набран заново")

    # ── порог: число, а не вкус ────────────────────────────────────────────
    def test_threshold_is_the_measured_number(self):
        """Порог 4 ч замерен, и замер назван в самом коде рядом с числом."""
        self.assertEqual(vp.IDLE_ALARM_SEC, 14400.0)
        with io.open(os.path.join(HERE, "vitrina_pc.py"), encoding="utf-8") as fh:
            body = fh.read()
        for proof in ("219.6", "3.66 ч", "медиана 34.2", "task_started"):
            self.assertIn(proof, body, "порог стои́т без замера: нет %s" % proof)

    def test_both_lines_are_in_the_live_message(self):
        """Обе строки задания доезжают до готового сообщения витрины."""
        facts = _live_facts()
        facts["idle"] = _idle(2 * 3600.0)
        facts["shtab_last"] = _shtab_last(26 * 3600.0)
        text = vp.render(facts, "2026-09-02 12:40 UTC")
        self.assertIn("простой полосы: очередь пуста ≥ 2.0 ч", text)
        self.assertIn("с 2026-09-02 11:00 UTC — закрылась строка #84", text)
        self.assertIn("приход Штаба: последнее задание #84", text)
        self.assertIn("маркер дата=%s" % DAY, text)
        self.assertIn("≥ 1.1 сут назад", text)
        self.assertNotIn("ТРЕВОГА", text, "2 ч простоя подняли тревогу при пороге 4 ч")

    def test_idle_changes_the_signature(self):
        """Простой — ЧИСЛО витрины: его смена обязана двигать правку сообщения."""
        one, two = _live_facts(), _live_facts()
        two["idle"] = _idle(5 * 3600.0)
        self.assertNotEqual(vp.signature(one), vp.signature(two))

    def test_idle_does_not_repaint_the_showcase_every_turn(self):
        """ЗАМОК ОТ МИГАЛКИ: возраст растёт непрерывно, а витрина — не мигалка.

        Два наблюдения через виток (600 с) внутри одной получасовой ступени
        обязаны дать ОДНУ подпись. Без ступени пустая очередь одна давала бы
        правку каждый оборот — ~144 в сутки против максимум 48 со ступенью.
        """
        base = 4 * 3600.0 + 60.0
        one, two = _live_facts(), _live_facts()
        one["idle"], two["idle"] = _idle(base), _idle(base + vp.TICK_SEC)
        self.assertEqual(vp.signature(one), vp.signature(two),
                         "простой перекрашивает витрину каждый оборот")
        far = _live_facts()
        far["idle"] = _idle(base + vp.IDLE_STEP_SEC)
        self.assertNotEqual(vp.signature(one), vp.signature(far),
                            "ступень съела и настоящую смену числа")

    def test_coarse_age_never_hides_that_it_is_a_step(self):
        """Округлённое молча читается как измеренное — поэтому знак «≥» обязателен."""
        self.assertEqual(vp.coarse_words(2 * 3600.0), "≥ 2.0 ч")
        self.assertEqual(vp.coarse_words(120.0), "2 мин", "первая ступень испорчена «≥ 0»")
        self.assertEqual(vp.coarse_words(None), "возраст неизвестен")

    def test_live_snapshot_is_read_without_crashing(self):
        """Боевой слепок разбирается обеими ветками — форма не выдумана."""
        path = os.path.join(HERE, "queue_snapshot_pc.state.json")
        if not os.path.exists(path):                   # pragma: no cover — чужое дерево
            self.skipTest("боевого слепка в этом дереве нет")
        with io.open(path, encoding="utf-8") as fh:
            snap = json.load(fh)
        now = float(snap.get("at") or NOW)
        idle = run.idle_facts(snap, now)
        last = run.shtab_last_facts(snap, run.read_claims(HERE), now)
        self.assertIsNotNone(idle)
        self.assertIn("busy", idle)
        self.assertIn(vp.UNKNOWN, vp.idle_words(None))
        self.assertTrue(vp.idle_words(idle).startswith("простой полосы:"))
        self.assertTrue(vp.shtab_last_words(last, DAY).startswith("приход Штаба:"))


class TestReaderHealsItself(unittest.TestCase):
    """ЧИТАТЕЛЬ ВЫПРАВЛЯЕТСЯ САМ — ЭТО ПРОВЕРЯЕТСЯ, А НЕ ПРЕДПОЛАГАЕТСЯ (12.09.2026).

    Витрина врала не своей ошибкой: «с какого времени пусто» она берёт как `max(at)`
    по реестру закрытых, а реестр ключевался переиспользуемым НОМЕРОМ и терял новые
    закрытия. Живой случай 11.09: реестр замёрз на 13:47Z, после чего полоса закрыла
    шесть рядов, и витрина 4 часа звала работающую очередь пустой — 9 завышений
    простоя и одна ложная ТРЕВОГА за сутки.

    Реестру вернули личность (`queue_snapshot_pc.closed_id`), витрину не тронули НИ
    ОДНОЙ строкой. Здесь на фикстуре доказывается, что этого хватило первой строке —
    и ЧЕСТНО показывается, чего не хватило второй.
    """

    GOAL_A = "[от Штаба дата=2026-09-10 ключ=23-a-kopiya-ramki.1009] вчерашний ряд"
    GOAL_B = "[от Штаба дата=2026-09-11 ключ=44-a-vitok-slepota.1209] сегодняшний ряд"

    def broken(self):
        """Реестр, каким его оставлял ключ-номер: вчерашняя запись #4 съела сегодняшнюю."""
        return {"open": {}, "closed": {
            "4": {"id": "4", "at": NOW - 20 * 3600.0, "outcome": "done", "goal": self.GOAL_A}}}

    def fixed(self):
        """Тот же час, но реестр ключеван личностью: обе записи под номером 4 живы."""
        snap = self.broken()
        snap["closed"] = dict(snap["closed"])
        snap["closed"]["4@" + self.GOAL_B] = {"id": "4", "at": NOW - 600.0, "outcome": "done",
                                              "goal": self.GOAL_B}
        return snap

    def test_idle_line_heals_itself_on_the_fixed_registry(self):
        """СТРОКА «С КАКОГО ВРЕМЕНИ ПУСТО» — выправляется сама, числом.

        20 часов простоя против 10 минут: одна и та же функция, один и тот же час,
        разница только в реестре. Правки читателя не потребовалось ни одной."""
        was = run.idle_facts(self.broken(), NOW)
        self.assertAlmostEqual(was["sec"], 20 * 3600.0, places=3)
        self.assertTrue(vp.idle_alarm(was, _shtab_last(None, found=False), DAY),
                        "ложной тревоги не случилось — фикстура не воспроизводит класс")
        now_ok = run.idle_facts(self.fixed(), NOW)
        self.assertAlmostEqual(now_ok["sec"], 600.0, places=3)
        self.assertEqual(now_ok["since_id"], "4")
        self.assertEqual(vp.idle_alarm(now_ok, _shtab_last(None, found=False), DAY), "",
                         "тревога пережила починку реестра")
        self.assertIn("10 мин", vp.idle_words(now_ok))

    def test_shtab_line_gets_the_right_key_but_the_hour_is_still_by_number(self):
        """СТРОКА «ПОСЛЕДНЕЕ ЗАДАНИЕ» — выправляется ЧАСТЬЮ, и вторая часть названа.

        Ключ задания чинится реестром: свежая запись появилась, и по сравнению
        `(день, час)` побеждает она, а не вчерашняя. А ЧАС по-прежнему берётся из
        отметок claim ПО НОМЕРУ (`vitrina_pc_run:328`), то есть у того, кто владеет
        номером СЕЙЧАС, — и реестром это не лечится ничем. Мешает ровно одно место,
        и оно в читателе; правка витрины этим заходом запрещена, поэтому здесь
        зафиксировано состояние, а не сделан вид, что класс закрыт целиком."""
        claims = {"4": NOW - 300.0}                 # claim нынешнего владельца номера 4
        was = run.shtab_last_facts(self.broken(), claims, NOW)
        self.assertEqual(was["key"], "23-a-kopiya-ramki.1009", "класс не воспроизведён")
        now_ok = run.shtab_last_facts(self.fixed(), claims, NOW)
        self.assertEqual(now_ok["key"], "44-a-vitok-slepota.1209", "ключ не выправился")
        self.assertEqual(now_ok["at_kind"], "claim")
        self.assertAlmostEqual(now_ok["at"], claims["4"], places=3)
        # ОТРИЦАТЕЛЬНАЯ ПОЛОВИНА: час пришёл от НОМЕРА, а не от найденной записи.
        self.assertNotAlmostEqual(now_ok["at"], NOW - 600.0, places=3,
                                  msg="час внезапно взят у своей записи — правило показа "
                                      "изменилось, и этот тест обязан быть переписан")

    def test_reader_file_was_not_touched_by_this_fix(self):
        """Замок на само утверждение «читатель не правился»: строки склейки на месте."""
        with io.open(os.path.join(HERE, "vitrina_pc_run.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('at, kind = (claims or {}).get(rid), "claim"', src,
                      "читатель всё-таки поправлен — утверждение п.7 стало ложным")


class TestHealthList(unittest.TestCase):
    """ЧТО РАБОТАЕТ И ЧТО НЕТ: приговор одним словом, чем снят, чего не покрывает.

    Сюда же — ОТРИЦАТЕЛЬНЫЙ ТЕСТ 4 задания (`test_fresh_trace_of_a_dead_instrument…`): состояние
    «признак выглядит правильным, а результата нет» обязано давать отказ, а не зелёное.
    """

    EX = "tmp/expect_pc/state.json"
    WD = run.WATCH_FILE

    def _tree(self, expect=None, watch=None, ex_age=60.0, wd_age=60.0):
        """Дерево с двумя источниками нужного возраста. → (корень, now). Боевого не касается."""
        root = tempfile.mkdtemp(prefix="vitrina_health_")
        os.makedirs(os.path.join(root, "tmp", "expect_pc"))
        now = NOW
        for rel, data, age in ((self.EX, expect, ex_age), (self.WD, watch, wd_age)):
            if data is None:
                continue
            path = os.path.join(root, rel.replace("/", os.sep))
            with io.open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.utime(path, (now - age, now - age))
        return root, now

    def _nodes(self, root, now):
        expect, e_at, _w = cdr.read_json(root, cd.source("expect")["addr"])
        watch, w_at, _w2 = run.read_watch(root)
        return run.health_nodes(expect, e_at, watch, w_at, now)

    @staticmethod
    def _state(said=None, at=NOW):
        rows = [{"name": ex.HEALTH_DAEMON, "said": ex.TURN_OK, "src": ex.HEALTH_DAEMON_SRC}]
        rows += [{"name": k, "said": (said or {}).get(k, ex.MOD_OK),
                  "src": ex.KID_SIGNS[k]["src"]} for k in ex.KIDS]
        return {"open": {}, ex.HEALTH_SLOT: {"at": at, "rows": rows}}

    @staticmethod
    def _watch(halted=()):
        return {"ts": NOW, "children": {k: {"deaths": 0, "halted": k in halted, "last_raise": 0.0}
                                        for k in ex.KIDS}}

    # ── список узлов ───────────────────────────────────────────────────────
    def test_six_nodes_and_every_name_comes_from_the_live_contour(self):
        """Шесть узлов, и ни одно имя не набрано в витрине руками."""
        root, now = self._tree(self._state(), self._watch())
        nodes = self._nodes(root, now)
        names = [n["name"] for n in nodes]
        self.assertEqual(len(nodes), 6)
        self.assertEqual(names[:4], [ex.HEALTH_DAEMON] + list(ex.KIDS))
        self.assertIn(run.WATCHDOG_NODE, names)
        self.assertIn(run.WATCHER_NODE, names)

    def test_every_row_says_three_things(self):
        """Приговор одним словом · чем снят и возраст · чего прибор не видит — у КАЖДОЙ строки."""
        root, now = self._tree(self._state(), self._watch())
        for row in vp.part_health(self._nodes(root, now)):
            self.assertTrue(any(w in row for w in (vp.HEALTH_OK, vp.HEALTH_BAD, vp.UNKNOWN)), row)
            self.assertIn(" · чем: ", row)
            self.assertIn(" · не покрывает: ", row)
            self.assertLess(len(row), 320, "строка на узел, не абзац — читают с телефона")

    def test_calm_contour_is_green_and_the_dead_child_is_red(self):
        root, now = self._tree(self._state(), self._watch())
        rows = vp.part_health(self._nodes(root, now))
        self.assertIn("%s — %s" % (ex.HEALTH_DAEMON, vp.HEALTH_OK), rows[0])
        self.assertIn("userbot — %s" % vp.HEALTH_OK, rows[2])
        root, now = self._tree(self._state({"userbot": ex.MOD_IDLE}), self._watch())
        rows = vp.part_health(self._nodes(root, now))
        self.assertIn("userbot — %s" % vp.HEALTH_BAD, rows[2])

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ: свежий след покойника ──────────────────────────
    def test_fresh_trace_of_a_dead_instrument_is_refused_not_greenlit(self):
        """ПРИЗНАК ВЫГЛЯДИТ ПРАВИЛЬНЫМ, А РЕЗУЛЬТАТА НЕТ.

        В файле наблюдателя лежит совершенно правильное зелёное слово о каждом узле — но сам файл
        не переписывался три часа при пределе в тридцать минут. Слово это доказывает ровно одно:
        что три часа назад кто-то его написал. Строка обязана выдать отказ и НАЗВАТЬ след.
        """
        root, now = self._tree(self._state(), self._watch(), ex_age=3 * 3600.0)
        nodes = self._nodes(root, now)
        rows = vp.part_health(nodes)
        for node, row in zip(nodes[:4], rows[:4]):
            said, _note = vp.health_verdict(node["said"], stale=False)
            self.assertEqual(said, vp.HEALTH_OK, "слово в файле было зелёным — иначе тест пуст")
            self.assertEqual(vp.health_verdict(node["said"], node["stale"])[0], vp.UNKNOWN)
            self.assertIn(vp.HEALTH_TRACE, row)
            self.assertNotIn("— %s ·" % vp.HEALTH_OK, row)
        # …а сторож, чей снимок СВЕЖ, остаётся зелёным: протухание одного источника не красит
        # соседний узел, у которого прибор свой.
        self.assertIn("%s — %s" % (run.WATCHDOG_NODE, vp.HEALTH_OK), rows[4])

    def test_stale_red_word_is_not_dressed_as_a_corpse(self):
        """Замершее «работы нет» ложным зелёным не бывает — пугать следом покойника незачем."""
        verdict, note = vp.health_verdict(ex.MOD_IDLE, stale=True)
        self.assertEqual(verdict, vp.UNKNOWN)
        self.assertNotIn(vp.HEALTH_TRACE, note)
        self.assertIn(ex.MOD_IDLE, note)

    # ── третий исход полноправен ───────────────────────────────────────────
    def test_blind_probe_is_unknown_and_never_dead(self):
        """Узел, о котором прибор не смог спросить, получает НЕИЗВЕСТНО, а не «не работает»."""
        for said, stale in ((ex.MOD_UNKNOWN, False), (None, False), ("чужое слово", False),
                            (ex.MOD_OK, None), (ex.TURN_OK, True)):
            self.assertEqual(vp.health_verdict(said, stale)[0], vp.UNKNOWN,
                             "слепая проба назвалась приговором: %r/%r" % (said, stale))

    def test_missing_health_slot_is_unknown_with_a_named_reason(self):
        """Наблюдатель ещё не тикал после правки → слова нет, и это сказано словами."""
        root, now = self._tree({"open": {}}, self._watch())
        rows = vp.part_health(self._nodes(root, now))
        self.assertIn(vp.HEALTH_NO_WORD, rows[0])
        self.assertIn("вердикта о «%s»" % ex.HEALTH_DAEMON, rows[0])

    def test_no_sources_at_all_still_names_every_node(self):
        """Оба источника мертвы → шесть строк НЕИЗВЕСТНО, а не пустой раздел и не ноль узлов."""
        root, now = self._tree()
        rows = vp.part_health(self._nodes(root, now))
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertIn(vp.UNKNOWN, row)
            self.assertNotIn(vp.HEALTH_BAD, row)

    def test_part_says_the_difference_between_no_list_and_empty_list(self):
        self.assertIn(vp.UNKNOWN, vp.part_health(None)[0])
        self.assertIn("НЕ «всё работает»", vp.part_health([])[0])

    # ── сторож судится своим продуктом ─────────────────────────────────────
    def test_watchdog_that_gave_up_on_a_child_is_red(self):
        root, now = self._tree(self._state(), self._watch(halted=("moderation_bot",)))
        row = vp.part_health(self._nodes(root, now))[4]
        self.assertIn("%s — %s" % (run.WATCHDOG_NODE, vp.HEALTH_BAD), row)
        self.assertIn("сдался на moderation_bot", row)

    def test_frozen_watchdog_snapshot_is_a_trace_not_a_verdict(self):
        """Снимок надзора трёхчасовой давности со словом «сдавшихся нет» — след, а не здоровье."""
        root, now = self._tree(self._state(), self._watch(), wd_age=3 * 3600.0)
        row = vp.part_health(self._nodes(root, now))[4]
        self.assertIn("%s — %s" % (run.WATCHDOG_NODE, vp.UNKNOWN), row)
        self.assertIn(vp.HEALTH_TRACE, row)

    # ── ничего не изобретено: слова и списки ВЗЯТЫ, а не набраны ───────────
    def test_words_and_lists_are_borrowed_from_the_instruments(self):
        """Второй словарь дал бы одному состоянию два имени — сверяем с прибором дословно."""
        self.assertEqual(vp.HEALTH_SAID[ex.MOD_OK], vp.HEALTH_OK)
        self.assertEqual(vp.HEALTH_SAID[ex.MOD_IDLE], vp.HEALTH_BAD)
        self.assertEqual(vp.HEALTH_SAID[ex.TURN_OK], vp.HEALTH_OK)
        self.assertEqual(vp.HEALTH_SAID[ex.TURN_SILENT], vp.HEALTH_BAD)
        self.assertEqual(vp.HEALTH_SAID[ex.MOD_UNKNOWN], vp.UNKNOWN)
        self.assertEqual(run.HEALTH_BLIND[run.WATCHDOG_NODE],
                         ex.KID_REJECTED["pc_orchestrator.client_watch.json"])
        for kid in ex.KIDS:
            self.assertIn(ex.KID_SIGNS[kid]["caveat"][:40],
                          run.health_nodes({}, NOW, None, None, NOW)[1 + list(ex.KIDS).index(kid)]
                          ["blind"])

    def test_section_reads_and_probes_nothing_of_its_own(self):
        """«Новых приборов не изобретать»: сборка узлов не щупает процессы ни одной веткой."""
        with io.open(os.path.join(HERE, "vitrina_pc_run.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="vitrina_pc_run.py")
        banned = {"OpenProcess", "kill", "Popen", "check_output", "tasklist", "WinDLL",
                  "psutil", "connect", "urlopen"}
        seen = {getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertEqual(seen & banned, set(), "витрина завела свою пробу живости")

    def test_live_state_of_the_observer_is_parsed_without_crashing(self):
        """Боевой файл наблюдателя разбирается сборкой узлов — форма не выдумана."""
        path = os.path.join(HERE, "tmp", "expect_pc", "state.json")
        if not os.path.exists(path):                   # pragma: no cover — чужое дерево
            self.skipTest("боевого состояния наблюдателя в этом дереве нет")
        expect, at, _why = cdr.read_json(HERE, cd.source("expect")["addr"])
        watch, w_at, _w2 = run.read_watch(HERE)
        nodes = run.health_nodes(expect, at, watch, w_at, (at or NOW) + 60.0)
        self.assertEqual(len(nodes), 6)
        for row in vp.part_health(nodes):
            self.assertIn(" · чем: ", row)


class TestStaleIsNotUnread(unittest.TestCase):
    """ПРИЧИНА ГОВОРИТ ПРАВДУ (хвост задания 31-a, закрыт 11.09.2026).

    «Слепок НЕ ПРОЧИТАН» и «слепок ПРОЧИТАН, но старше предела» — РАЗНЫЕ исходы с
    разной починкой: первый лечат мостом и диском, второй — вставшим демоном,
    который слепок больше не обновляет. Строка цели витрины печатала на оба один
    текст — «слепок очереди не прочитан», — то есть при живом файле на диске
    утверждала неправду и посылала чинить не то место.

    ВЕЛИЧИНА НЕ ТРОГАЕТСЯ: слово `НЕИЗВЕСТНО` на месте в обоих исходах и было верно
    до правки (коммит f5dc9ab). Правится РОВНО причина в скобках.

    Третий исход назван отдельно и не слит с протуханием: возраст не сверить — это
    не «стар», а «сказать нечем» (тот же закон, что у :func:`contour_digest.stale`).
    """

    LIMIT = 3600.0

    def _why(self, data, read_at, now=NOW):
        return cdr.fresh_why(data, read_at, now, "queue")

    def _goal_line(self, why):
        rows = vp.part_goal({"ok": False, "why": "нет"}, None, DAY, why=why)
        got = [r for r in rows if "критерий фазы" in r]
        self.assertEqual(1, len(got), "строка цели не одна: %r" % (rows,))
        return got[0]

    # ── ОТРИЦАТЕЛЬНЫЙ: два входа — два разных слова ──────────────────────
    def test_stale_and_unread_are_not_the_same_words(self):
        """Падает до правки: оба входа давали дословно одну строку."""
        unread = self._goal_line(self._why(None, None))
        stale = self._goal_line(self._why({"open": {}, "closed": {}}, NOW - self.LIMIT - 60.0))
        self.assertNotEqual(unread, stale,
                            "два разных исхода напечатаны одной причиной: %r" % unread)
        self.assertIn("не прочитан", unread)
        self.assertNotIn("не прочитан", stale,
                         "прочитанный слепок назван непрочитанным: %r" % stale)
        self.assertIn("старше предела", stale)

    def test_the_stale_line_names_the_limit_it_was_judged_by(self):
        """Предел берётся ТАМ, ГДЕ НАЗВАН, — второго экземпляра числа не заводится."""
        stale = self._goal_line(self._why({"closed": {}}, NOW - self.LIMIT - 60.0))
        self.assertIn(str(int(cd.limit_of("queue"))), stale)

    def test_the_third_outcome_is_its_own_words(self):
        """Возраст не сверить → своё слово, а не «стар» и не «не прочитан»."""
        blind = self._goal_line(self._why({"closed": {}}, None))
        self.assertNotIn("старше предела", blind)
        self.assertNotIn("не прочитан", blind)
        self.assertIn("возраст", blind)

    # ── КОНТРОЛЬ: показ величины не тронут ───────────────────────────────
    def test_the_shown_value_is_still_the_word_not_a_zero(self):
        """Во всех трёх исходах стои́т НЕИЗВЕСТНО — ровно как поставил f5dc9ab."""
        for why in (self._why(None, None),
                    self._why({"closed": {}}, NOW - self.LIMIT - 60.0),
                    self._why({"closed": {}}, None)):
            line = self._goal_line(why)
            self.assertIn(vp.UNKNOWN, line)
            self.assertNotIn("сейчас 0", line, "незнание подменено нулём: %r" % line)

    def test_a_fresh_snapshot_says_nothing_and_the_count_is_printed(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ: свежий слепок — причины нет, печатается ЧИСЛО."""
        self.assertEqual("", self._why({"closed": {}}, NOW - 10.0))
        line = [r for r in vp.part_goal({"ok": False, "why": "нет"}, {"streak": 4, "target": 30}, DAY)
                if "критерий фазы" in r][0]
        self.assertIn("сейчас 4", line)
        self.assertNotIn(vp.UNKNOWN, line)

    def test_the_default_keeps_the_old_words_for_callers_that_say_nothing(self):
        """Причину не подали — прежний текст: правка не роняет чужие вызовы."""
        self.assertIn("слепок очереди не прочитан", self._goal_line(""))

    def test_the_reason_reaches_the_goal_line_through_the_whole_vitrina(self):
        """Дверь доезжает ДО ТЕКСТА: причина, положенная в факты, видна в витрине."""
        facts = _dead_facts()
        facts["series_why"] = self._why({"closed": {}}, NOW - self.LIMIT - 60.0)
        text = vp.render(facts, "2026-09-11 12:40 UTC")
        self.assertIn("старше предела", text)


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
