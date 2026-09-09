# -*- coding: utf-8 -*-
"""Тесты лотка информационных заявок (`zayavki_lotok_pc` + `zayavki_lotok_run` + руки ступеней).

КАРТА КЛАССОВ:

  TestPurity            чистота чистого слоя обходом AST: ни диска, ни сети, ни часов, ни env
  TestOneDiscriminator  различитель НЕ переписан: род и работу судит `zayavki_route_pc.decide`
  TestFile              файл лотка: шапка машинная, тело ДОСЛОВНОЕ, разбор возвращает его целым
  TestNegative          ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания, ТРИ случая, все на ТЕСТОВОЙ сущности
                        и БЕЗ единого сообщения владельцу и без единого ряда очереди
  TestVisible           изъятое видно: строкой списка и ЧИСЛОМ витрины; НЕИЗВЕСТНО вместо нуля
  TestBudget            дедуп и СУТОЧНЫЙ ПОТОЛОК не протекли: лоток считается тем же счётом
  TestOff               рубильник возвращает прежний путь (ряд + карточка)

ВЛАДЕЛЬЦУ ОТСЮДА НЕ УХОДИТ НИЧЕГО. Очередь и мост заменены двойниками, лоток живёт
во ВРЕМЕННОМ каталоге теста; боевой лоток `docs/zayavki_lotok` ни одной веткой не
открывается на запись.
"""
from __future__ import annotations

import ast
import io
import os
import shutil
import tempfile
import unittest

os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")   # боевой лог демона тестом не трогаем

import recon_auto
import review_intake
import vitrina_pc as vp
import zayavki_lotok_pc as zl
import zayavki_lotok_run as zlr
import zayavki_route_pc as zr

HERE = os.path.dirname(os.path.abspath(__file__))

# Тела ТЕСТОВЫХ заявок. Формат маркера — дословно тот, что пишут ступени B и E:
# по нему же считают дедуп и суточный потолок, и разойдись он — протечёт потолок.
ASK_B = "[заявка-ревью дата=2026-09-09 ключ=aabbccdd1122]\nВИД НАХОДКИ: УПРОЩАЕМО"
ASK_E = "[разведка-заявка дата=2026-09-09 ключ=ffeeddcc3344]\nПОВОД: ожидание без разбора"
GUARD = ("🔴 Хочу удалить файл — разрешить?\nОбъект: docs/tmp/x.md\n"
         "Если «да»: файл будет удалён безвозвратно")
DAY = "2026-09-09"


class _Daemon:
    """Двойник демона: даёт РОВНО то, чем пользуются руки лотка и постановка ряда.

    `_is_owner_work` — не заглушка со своим мнением, а ПРОКСИ на настоящую функцию
    полосы: подменить её значило бы проверять собственную выдумку вместо признака,
    которым живёт демон.
    """

    def __init__(self):
        import pc_orchestrator as o
        self._o = o
        self.enqueued = []          # СЛЕД: сюда попадает всё, что стало РЯДОМ очереди
        self.approvals = []
        self.NEEDS_APPROVAL_TOPIC = o.NEEDS_APPROVAL_TOPIC
        self.bc = self

    # — интерфейс демона, которым пользуются руки ступеней —
    def _is_owner_work(self, row, pids=()):
        return self._o._is_owner_work(row, pids)

    def enqueue_pc_task(self, text, frm="Filipp"):
        self.enqueued.append({"text": text, "from": frm})
        return True, 1000 + len(self.enqueued), None

    def claim_task(self, tid):
        return {"ok": True}

    def set_needs_approval(self, tid, what, topic=None, frm=None):
        self.approvals.append({"id": tid, "topic": topic, "from": frm})
        return {"ok": True}

    def get_pending(self, status):
        return {"ok": True, "items": []}


class _Tmp(unittest.TestCase):
    """Общий корень: ВРЕМЕННЫЙ каталог, названный явно. Боевого лотка не касаемся."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="lotok_pc_")
        self.d = _Daemon()

    def tearDown(self):
        # Каталог теста — ЕДИНСТВЕННОЕ, что здесь исчезает, и он создан этим же тестом.
        shutil.rmtree(self.root, ignore_errors=True)

    def queue_b(self):
        import review_intake_run
        return review_intake_run.Queue(daemon=self.d, root=self.root)

    def queue_e(self):
        import recon_auto_run
        return recon_auto_run.Queue(daemon=self.d, root=self.root)


class TestPurity(unittest.TestCase):
    """ZAYAVKI_LOTOK_PC_PURE — обходом AST, а не обещанием."""

    def setUp(self):
        with io.open(os.path.join(HERE, "zayavki_lotok_pc.py"), encoding="utf-8") as fh:
            self.tree = ast.parse(fh.read())

    def test_no_io_no_clock_no_env(self):
        banned = {"open", "input", "print", "exec", "eval"}
        attrs = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    self.assertNotIn(f.id, banned, "чистый слой зовёт %s" % f.id)
                if isinstance(f, ast.Attribute):
                    attrs.add(f.attr)
        for bad in ("getenv", "now", "time", "run", "Popen", "urlopen", "request",
                    "listdir", "makedirs", "replace_file"):
            self.assertNotIn(bad, attrs, "чистый слой зовёт .%s" % bad)

    def test_imports_are_pure(self):
        """Импортируется РОВНО различитель маршрута — ни os, ни io, ни json."""
        names = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        self.assertEqual(names - {"__future__"}, {"zayavki_route_pc"},
                         "чистый слой потянул лишний импорт: %s" % sorted(names))


class TestOneDiscriminator(unittest.TestCase):
    """Различитель ОДИН на полосу: лоток его ЗОВЁТ, а не переписывает."""

    def test_informational_is_the_route_verdict(self):
        row = zl.row_of(ASK_E, "Filipp-recon-ask")
        self.assertEqual(zl.informational(row, owner_work=False),
                         zr.decide(row, owner_work=False))

    def test_lotok_module_has_no_second_kind_table(self):
        """В чистом слое лотка НЕТ своего списка маркеров: он живёт в маршруте."""
        with io.open(os.path.join(HERE, "zayavki_lotok_pc.py"), encoding="utf-8") as fh:
            src = fh.read()
        body = src.split('"""', 2)[-1]          # шапку не считаем: там маркеры цитируются в прозе
        for mark in ("[заявка-ревью", "[разведка-заявка"):
            self.assertNotIn(mark, body,
                             "лоток завёл второй экземпляр списка маркеров — он разойдётся молча")


class TestFile(unittest.TestCase):
    """Файл лотка: шапка машинная, тело ДОСЛОВНОЕ и возвращается целым."""

    def test_body_survives_verbatim(self):
        v = zr.decide(zl.row_of(ASK_E, "Filipp-recon-ask"), owner_work=False)
        text = zl.file_text(ASK_E, v, "2026-09-09T20:00:00Z", day=DAY)
        self.assertEqual(zl.claim_text_of(text), ASK_E)

    def test_head_is_parsed_back(self):
        v = zr.decide(zl.row_of(ASK_B, "Filipp-review-claim"), owner_work=False)
        got = zl.parse_file(zl.file_text(ASK_B, v, "2026-09-09T20:00:00Z", day=DAY))
        self.assertEqual(got["day"], DAY)
        self.assertEqual(got["from"], "Filipp-review-claim")
        self.assertEqual(got["kind"], zr.sbs.KIND_ASK)
        self.assertEqual(got["claim"], ASK_B)

    def test_no_separator_means_empty_body_not_whole_file(self):
        """Разделителя нет → тело ПУСТО. Отдать шапку за текст заявки нельзя:
        счёт маркеров получил бы наши собственные слова вместо чужого маркера."""
        self.assertEqual(zl.claim_text_of("просто файл без разделителя"), "")


class TestNegative(_Tmp):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗАДАНИЯ. Три случая, ТЕСТОВЫЕ сущности.

    Меряем то, ЧТО ЗАДАНИЕ И ТРЕБУЕТ УБРАТЬ, — САМ РЯД ОЖИДАНИЯ. Прошлый заход мерил
    поле `topic`, и живой ряд #232 показал цену этого рычага: тему сняли, а карточка
    пришла (её несёт devbot VPS по метке `from`). Поэтому здесь свидетель другой:
    список `_Daemon.enqueued`. Ряд создан — там запись; ряда нет — там пусто.
    """

    def test_1_guard_card_goes_the_old_way_unchanged(self):
        """СЛУЧАЙ 1: операционная карточка гарда — прежний путь БЕЗ ИЗМЕНЕНИЙ.

        Ряд создаётся, `claim` зовётся, тема называется. Ни одна ветка лотка её
        не касается: род у неё не `KIND_ASK`, и первый же признак это говорит.
        """
        took, rel, why = self.queue_b().to_lotok(GUARD, "Filipp")
        self.assertFalse(took, "лоток взял карточку гарда — это прямой запрет задания")
        ok, tid, _why = self.queue_b().place(GUARD)
        self.assertTrue(ok)
        self.assertEqual(len(self.d.enqueued), 1, "карточка гарда обязана СТАТЬ рядом")
        self.assertEqual(self.d.approvals[-1]["topic"], self.d.NEEDS_APPROVAL_TOPIC)

    def test_2_informational_claim_creates_no_queue_row_at_all(self):
        """СЛУЧАЙ 2: информационная заявка НЕ СОЗДАЁТ РЯДА ВОВСЕ.

        Ни `enqueue`, ни `claim`, ни `set_needs_approval` — ни одного из трёх
        действий ступени. Ряда, ждущего ответа владельца, не появляется.
        """
        ok, rel, _why = self.queue_e().place_ask(ASK_E)
        self.assertTrue(ok, "заявка обязана быть принята — лотком, а не отказом")
        self.assertEqual(self.d.enqueued, [], "ряд ожидания всё-таки создан")
        self.assertEqual(self.d.approvals, [], "карточку всё-таки просили")
        self.assertTrue(str(rel).startswith(zl.LOTOK_DIR), "адрес заявки не назван лотком")

    def test_2b_but_it_is_visible_in_the_list(self):
        """…и при этом ВИДНА: файл лежит, строка списка несёт день, род и адрес."""
        self.queue_e().place_ask(ASK_E)
        rows, ok, why = zlr.rows(self.root)
        self.assertTrue(ok, why)
        self.assertEqual(len(rows), 1, "заявка исчезла без следа — это запрещено")
        line = zl.list_line(rows[0])
        self.assertIn(DAY, line)
        self.assertIn("Filipp-recon-ask", line)
        self.assertIn("НЕ СОЗДАВАЛОСЬ", line)
        self.assertEqual(rows[0]["claim"], ASK_E, "текст заявки в лотке не дословный")

    def test_3_claim_that_holds_work_still_becomes_a_waiting_row(self):
        """СЛУЧАЙ 3: заявка, которая ДЕРЖИТ РАБОТУ, по-прежнему становится рядом.

        Тело ряда здесь ДОСЛОВНО то же, что во втором случае: различает их только
        признак из кода (`_is_owner_work` по полю `from`), а не слова текста.
        """
        self.assertTrue(self.d._is_owner_work({"from": "Filipp-shtab", "task_text": ASK_E}, ()))
        took, _rel, why = self.queue_e().to_lotok(ASK_E, "Filipp-shtab")
        self.assertFalse(took, "лоток взял заявку, ДЕРЖАЩУЮ работу")
        self.assertIn("ДЕРЖИТ РАБОТУ", why)

    def test_4_unreadable_lotok_is_unknown_not_zero(self):
        """НЕЧИТАЕМЫЙ ЛОТОК = НЕИЗВЕСТНО С ПРИЧИНОЙ, А НЕ НОЛЬ.

        Каталога нет — `rows` говорит `ok=False` и называет причину, а витрина
        печатает НЕИЗВЕСТНО. «В лотке 0» было бы неотличимо от честного тихого дня.
        """
        # НЕЧИТАЕМЫЙ, А НЕ «ЕЩЁ НЕ ЗАВЕДЁННЫЙ»: на месте каталога лежит ФАЙЛ. Это
        # разные новости, и путать их нельзя в обе стороны. «Каталога ещё нет» —
        # честный пустой лоток (его создаёт первая запись), и объявить его
        # незнанием значило бы запереть обе ступени намертво: ставить нельзя,
        # потому что «не знаю», а «не знаю» не кончится, потому что ставить нельзя.
        fake = os.path.join(self.root, "занято")
        os.makedirs(os.path.join(fake, "docs"), exist_ok=True)
        with io.open(os.path.join(fake, *zl.LOTOK_DIR.split("/")), "w", encoding="utf-8") as fh:
            fh.write("это файл, а не каталог")
        rows, ok, why = zlr.rows(fake)
        self.assertFalse(ok)
        self.assertTrue(why, "причина незнания не названа")
        line = zl.vitrina_lines(None, ok=False, why=why)[0]
        self.assertIn(zl.UNKNOWN, line)
        self.assertNotIn(": 0", line)

    def test_5_unreadable_lotok_stops_placement_instead_of_doubling(self):
        """Нечитаемый лоток НЕ РАЗРЕШАЕТ ставить вслепую: дедуп не сверить.

        Та же доктрина, что у молчащего моста: непрочитанный корпус значит «не
        знаю, сколько уже поставлено», а постановка на таком «не знаю» и есть
        постановка дубля.
        """
        import review_intake_run
        fake = os.path.join(self.root, "занято2")
        os.makedirs(os.path.join(fake, "docs"), exist_ok=True)
        with io.open(os.path.join(fake, *zl.LOTOK_DIR.split("/")), "w", encoding="utf-8") as fh:
            fh.write("это файл, а не каталог")
        q = review_intake_run.Queue(daemon=self.d, root=fake)
        marks, ok, why = q.markers()
        self.assertFalse(ok)
        self.assertIn("лоток", why)


class TestVisible(_Tmp):
    """Изъятое видно ЧИСЛОМ витрины — отдельной строкой, и ноль по незнанию невозможен."""

    def test_showcase_has_its_own_line_with_the_number(self):
        self.queue_e().place_ask(ASK_E)
        rows, ok, _why = zlr.rows(self.root)
        lines = vp.part_nums(None, None, DAY, None, None,
                             lotok={"ok": ok, "rows": zl.day_rows(rows, DAY), "why": ""})
        text = "\n".join(lines)
        self.assertIn("ЗАЯВКИ В ЛОТКЕ за день: 1", text)
        self.assertIn(zl.LOTOK_DIR, text)

    def test_showcase_says_unknown_when_the_tray_is_unread(self):
        lines = vp.part_nums(None, None, DAY, None, None,
                             lotok={"ok": False, "rows": None, "why": "каталог не открылся"})
        text = "\n".join(lines)
        self.assertIn("ЗАЯВКИ В ЛОТКЕ за день: %s" % zl.UNKNOWN, text)
        self.assertIn("каталог не открылся", text)

    def test_showcase_never_loses_the_line_when_lotok_not_named(self):
        """Зовущий лоток не назвал → НЕИЗВЕСТНО, а не пропавшая строка."""
        text = "\n".join(vp.part_nums(None, None, DAY, None, None))
        self.assertIn("ЗАЯВКИ В ЛОТКЕ за день: %s" % zl.UNKNOWN, text)
        self.assertIn("лоток не спрашивали", text)

    def test_young_tray_reports_zero_with_its_reason_aloud(self):
        """Лотка ещё нет → ноль ЗАКОННЫЙ, но едет С ОГОВОРКОЙ, а не голым.

        Голый «0» читался бы как измеренный тихий день. Это ровно тот класс, ради
        которого серия витрины ездит вместе с опорой."""
        rows, ok, why = zlr.rows(os.path.join(self.root, "ещё-нет"))
        self.assertTrue(ok, "«каталога ещё нет» — это пустой лоток, а не незнание")
        text = "\n".join(vp.part_nums(None, None, DAY, None, None,
                                      lotok={"ok": ok, "rows": rows, "why": why}))
        self.assertIn("ЗАЯВКИ В ЛОТКЕ за день: 0", text)
        self.assertIn("не клали ничего", text)


class TestBudget(_Tmp):
    """ГЛАВНАЯ МИНА ПРАВКИ: дедуп и суточный потолок считаются ПО ЖИВОЙ ОЧЕРЕДИ.

    Убрав заявку из очереди и не дав взамен ничего, полоса обнулила бы счёт и
    ставила бы заявки без конца — тот же класс, что дал пять разведок за одни
    сутки при потолке 2. Проверяем, что лоток кормит ТЕ ЖЕ функции маркеров.
    """

    def test_tray_feeds_the_same_marker_counter_stage_e(self):
        self.queue_e().place_ask(ASK_E)
        rows, ok, _why = zlr.marker_rows(self.root)
        self.assertTrue(ok)
        self.assertEqual(recon_auto.markers(rows, "ask"), [(DAY, "ffeeddcc3344")])
        self.assertEqual(recon_auto.budget_left(recon_auto.markers(rows, "ask"), DAY, 1), 0,
                         "суточный потолок ступени E протёк: лоток не считается")

    def test_tray_feeds_the_same_marker_counter_stage_b(self):
        self.queue_b().place(ASK_B)
        rows, ok, _why = zlr.marker_rows(self.root)
        self.assertTrue(ok)
        self.assertEqual(review_intake.claim_markers(rows), [(DAY, "aabbccdd1122")])
        self.assertEqual(review_intake.budget_left(review_intake.claim_markers(rows), DAY, 1), 0,
                         "суточный потолок ступени B протёк: лоток не считается")

    def test_markers_of_stage_b_include_the_tray(self):
        self.queue_b().place(ASK_B)
        marks, ok, why = self.queue_b().markers()
        self.assertTrue(ok, why)
        self.assertIn((DAY, "aabbccdd1122"), marks)

    def test_second_placement_of_the_same_claim_does_not_double_the_file(self):
        """Повтор той же заявки НЕ множит файлов: имя устойчиво, чужое не затирается."""
        self.queue_e().place_ask(ASK_E)
        self.queue_e().place_ask(ASK_E)
        rows, ok, _why = zlr.rows(self.root)
        self.assertTrue(ok)
        self.assertEqual(len(rows), 1)


class TestOff(_Tmp):
    """Рубильник возвращает ПРЕЖНИЙ путь: ряд создаётся и тема называется."""

    def test_switch_returns_the_old_road(self):
        os.environ[zl.OFF_FLAG] = "1"
        try:
            ok, tid, _why = self.queue_e().place_ask(ASK_E)
            self.assertTrue(ok)
            self.assertEqual(len(self.d.enqueued), 1, "рубильник не вернул прежний путь")
            self.assertEqual(self.d.approvals[-1]["topic"], self.d.NEEDS_APPROVAL_TOPIC)
        finally:
            os.environ.pop(zl.OFF_FLAG, None)

    def test_switch_name_is_the_same_in_both_layers(self):
        with io.open(os.path.join(HERE, "zayavki_lotok_run.py"), encoding="utf-8") as fh:
            self.assertIn("lotok.OFF_FLAG", fh.read())
        self.assertEqual(zl.OFF_FLAG, "ZAYAVKI_LOTOK_PC_OFF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
