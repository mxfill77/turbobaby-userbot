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
import vitrina_pc as vp
import vitrina_pc_run as run

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1788352800.0                      # 2026-09-02 12:40 UTC
DAY = "2026-09-02"


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
            "expects": [], "failed": [], "awaiting": {"cards": 1, "zayavki": 1},
            "series": {"streak": 5, "target": 30, "proved": 5, "blind": 13, "unproved": 0,
                       "unjudged": 0, "moved": 2},
            "shtab_taken": 1,
            "external": {"day": day, "attempts": 3, "answers": 2, "channels_answered": 1,
                         "channels_tried": 2, "findings": 4, "down": []},
            # Две РАЗНЫЕ вещи в одном списке `needs_approval` — ровно то, что до 02.09
            # витрина звала одним словом: заявка внешнего канала и карточка гарда.
            "waiting": [{"id": "3", "goal": "[заявка-ревью дата=%s ключ=47b3131c052c]" % day,
                         "zayavka": True, "key": "47b3131c052c"},
                        {"id": "2", "goal": "удаление файла — разрешить?", "zayavka": False,
                         "key": ""}],
            "axes": {vp.AXIS_MAIN: 4, vp.AXIS_BIZ: 1, vp.AXIS_SERVICE: 22},
            "axes_why": "", "closed_day": 18,
            "idle": _idle(None, busy=1, ids=["5"]), "shtab_last": _shtab_last(7200.0)}


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
        for zero in ("взято 0 из", "карточки гарда в ожидании: 0",
                     "заявки внешних каналов ждут решения: 0", "ось (этап 3) 0"):
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
        def _boom(_name):
            raise RuntimeError("мост молчит")

        got = run.read_shtab(reader=_boom)
        self.assertFalse(got["ok"])
        self.assertIn("мост молчит", got["why"])

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
    """Шесть частей, и часть без строк — падение, а не тихий пропуск."""

    def test_all_six_parts_are_rendered(self):
        text = vp.render(_live_facts(), "2026-09-02 12:40 UTC")
        for _key, title in vp.PARTS:
            self.assertIn(title, text)
        self.assertEqual(len(vp.PARTS), 6)

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
        other["awaiting"] = {"cards": 1, "zayavki": 2}
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

    def test_live_snapshot_counts_them_apart(self):
        rows = run.waiting_rows(self.SNAP)
        self.assertEqual(run.awaiting_counts(rows), {"zayavki": 3, "cards": 1})

    def test_unknown_survives_as_unknown(self):
        self.assertIsNone(run.waiting_rows(None))
        self.assertIsNone(run.awaiting_counts(None))

    def test_numbers_name_two_things_by_two_names(self):
        lines = vp.part_nums(None, None, DAY, None, run.awaiting_counts(run.waiting_rows(self.SNAP)))
        text = "\n".join(lines)
        self.assertIn("карточки гарда в ожидании: 1", text)
        self.assertIn("заявки внешних каналов ждут решения: 3", text)
        self.assertNotIn("карточки в ожидании:", text)   # прежнего общего слова больше нет

    def test_owner_list_shows_a_zayavka_by_its_own_name_not_by_marker(self):
        rows = vp.part_owner(run.waiting_rows(self.SNAP))
        text = "\n".join(rows)
        self.assertIn("ЗАЯВКА внешнего канала (ключ 47b3131c052c)", text)
        self.assertIn("задачей не станет", text)
        self.assertNotIn("[заявка-ревью", text)          # машинный маркер владельцу не показываем
        self.assertIn("#9 удаление tmp/x — разрешить?", text)   # карточка гарда — как была


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


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
