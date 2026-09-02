# -*- coding: utf-8 -*-
"""test_shtab_box.py — набор ЯЩИКА ЗАДАНИЙ ШТАБА (:mod:`shtab_box`, :mod:`shtab_box_run`).

Пять ОТРИЦАТЕЛЬНЫХ тестов, названных заданием, живут в :class:`TestNegative` и
проверяют ровно то, что ящик обязан НЕ делать:

  1. узел недоступен → не берём ничего и говорим «НЕИЗВЕСТНО», а не «пусто»;
  2. блок без адреса результата → отказ с названной причиной;
  3. тот же ключ второй раз → не берётся никогда;
  4. стоп-файл на месте → не берётся, и узла мы даже не спрашиваем;
  5. в очереди задача владельца → не берётся ни одного.

Рядом с каждым — ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ. Без него отрицательный тест проходит и
у ящика, который не берёт НИЧЕГО НИКОГДА: правило, глушащее всё, отрицательные
проверки проходит идеально и стои́т ноль.

Остальные классы:

* :class:`TestPurity` — инвариант ``SHTAB_BOX_PURE``: чистый модуль решения не
  смеет завести часы, сеть, диск или окружение.
* :class:`TestReadsOnly` — инвариант ``SHTAB_BOX_READS_ONLY``, САМЫЙ ДОРОГОЙ
  здесь. Ящик читает ровно ОДИН назначенный узел и НЕ ПИШЕТ в мозг ни одной
  веткой: граница доверия ящика проходит по тому, ЧЕЙ это узел, и запись с нашей
  стороны стирает её целиком.
* :class:`TestParse` — формат блоков и отказы разбора (незакрытый блок, разъезд
  ключей, двойники).
* :class:`TestGates` — ворота приёма: ЗАПРЕТЫ и АДРЕС РЕЗУЛЬТАТА.
* :class:`TestCeiling` — суточный потолок и лимит витка; оба restart-proof, оба
  считаются по маркерам ЖИВОЙ очереди чужим устройством (ступень E).
* :class:`TestHands` — руки: сухой ход очередь не трогает, дорогое чтение `done`
  не спрашивается впустую, боевой ход ставит ряд.
* :class:`TestWiring` — врезка в демона и в сводку контура.
"""
from __future__ import annotations

import ast
import io
import os
import tempfile
import unittest

import contour_digest as cd
import contour_digest_run as cdr
import recon_auto
import result_ref
import shtab_box as sb
import shtab_box_run as run

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = "2026-09-02"


def _src(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


# ─────────────────────────── образцы блоков ───────────────────────────
# ТЕЛО ЗАДАНИЯ — правдоподобное и ЦЕЛОЕ: и запреты, и адрес. Именно из него
# вычитаются части в отрицательных тестах, чтобы отказ был вызван ОДНОЙ
# нехваткой, а не тем, что образец был мусором с самого начала.

GOOD_BODY = (
    "ЦЕЛЬ: пересчитать, сколько строк очереди полосы закрылось вердиктом «неизвестно» за август,\n"
    "и назвать долю числом.\n\n"
    + sb.PROHIBITIONS + "\n\n"
    "ПРИЗНАК СДЕЛАННОСТИ: в артефакте стои́т число и команда, которой оно получено.\n\n"
    "[result_ref: file docs/artifacts/2026-09-02-dolya-neizvestno.md]"
)

# Тело БЕЗ адреса результата — вырезана ровно последняя строка.
NO_ADDR_BODY = GOOD_BODY.replace(
    "[result_ref: file docs/artifacts/2026-09-02-dolya-neizvestno.md]", "").strip()

# Тело БЕЗ блока запретов — вырезан ровно он.
NO_PROHIB_BODY = GOOD_BODY.replace(sb.PROHIBITIONS, "").strip()


def _node(*pairs):
    """Пары (ключ, тело) → текст узла-ящика в живом формате, с человеческой шапкой."""
    out = ["═══ ЯЩИК ЗАДАНИЙ ШТАБА — полоса ПК ═══",
           "Блоки идут подряд, каждый со своим ключом. Между блоками можно писать что угодно.",
           ""]
    for key, body in pairs:
        out += ["[[ЗАДАЧА ключ=%s]]" % key, body, "[[КОНЕЦ ключ=%s]]" % key, ""]
    return "\n".join(out)


def _reader(text):
    """Чтение узла-заглушка: помнит, какое имя у него спросили."""
    seen = []

    def read(name):
        seen.append(name)
        return text

    read.seen = seen
    return read


def _dead_reader(why="узла нет"):
    def read(name):
        raise RuntimeError(why)

    return read


def _row(key, day=TODAY, tail=""):
    """Ряд очереди с маркером взятого задания — ровно в той форме, в какой его ставим."""
    return {"id": 900, "task_text": "%s дата=%s ключ=%s] %s%s"
                                    % (sb.MARK, day, key, sb.HEAD_WORDS, tail)}


class FakeQueue(object):
    """Очередь-заглушка: помнит, какие корпуса у неё спрашивали и что ставили."""

    def __init__(self, rows=(), closed=(), ok=True, closed_ok=True, busy=False, busy_ids=()):
        self._rows, self._closed = list(rows), list(closed)
        self._ok, self._closed_ok = ok, closed_ok
        self._busy, self._busy_ids = busy, list(busy_ids)
        self.tasks = []
        self.asked = []
        self.next_id = 700

    def rows(self, statuses=run.OPEN_STATUSES):
        self.asked.append(tuple(statuses))
        if not self._ok:
            return [], False, "мост не ответил"
        if tuple(statuses) == run.CLOSED_STATUSES:
            if not self._closed_ok:
                return [], False, "мост не ответил на закрытые"
            return list(self._closed), True, ""
        return list(self._rows), True, ""

    def owner_busy(self, rows):
        return self._busy, list(self._busy_ids)

    def place_task(self, text):
        self.next_id += 1
        self.tasks.append((self.next_id, text))
        return True, self.next_id, ""


def _tick(node_text=None, reader=None, queue=None, place=False, root=None, **kw):
    """Оборот ящика на заглушках. Корень — ВСЕГДА временный каталог, если не назван."""
    root = root or tempfile.mkdtemp(prefix="shtabbox_")
    return run.tick(root=root, place=place, queue=queue if queue is not None else FakeQueue(),
                    reader=reader or _reader(node_text if node_text is not None else _node()),
                    clock=lambda tz: __import__("datetime").datetime(2026, 9, 2, 12, 0, tzinfo=tz),
                    **kw)


# ═══════════════════════════ чистота ═══════════════════════════════════


class TestPurity(unittest.TestCase):
    """SHTAB_BOX_PURE — чистая логика остаётся чистой."""

    FORBIDDEN_MODULES = ("os", "io", "time", "subprocess", "socket", "requests",
                         "urllib", "pathlib", "shutil", "sqlite3", "brain_writer")
    FORBIDDEN_CALLS = ("open", "getenv", "system", "popen", "now", "utcnow", "time")

    def test_no_world_in_the_pure_module(self):
        tree = ast.parse(_src("shtab_box.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], self.FORBIDDEN_MODULES,
                                     "чистый модуль завёл мир: %s" % alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], self.FORBIDDEN_MODULES,
                                 "чистый модуль завёл мир: %s" % node.module)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                self.assertNotIn(name, self.FORBIDDEN_CALLS,
                                 "чистый модуль позвал мир: %s" % name)

    def test_the_module_knows_no_queue_and_no_paths(self):
        body = _src("shtab_box.py")
        for forbidden in ("get_pending", "enqueue_pc_task", "D:\\", "__file__"):
            self.assertNotIn(forbidden, body, "в чистом модуле не место %s" % forbidden)


# ═══════════════════════════ только чтение ═════════════════════════════


class TestReadsOnly(unittest.TestCase):
    """SHTAB_BOX_READS_ONLY — ящик читает один узел и НЕ ПИШЕТ в мозг ни одной веткой.

    Самый дорогой инвариант набора. Граница доверия ящика проходит по тому, ЧЕЙ
    это узел: пишет в него Штаб, читает полоса. Ветка записи с нашей стороны
    превратила бы ящик в машину, ставящую себе задачи собственными словами, и
    заметить это было бы нечем — текст в очереди выглядел бы точно так же.
    """

    WRITERS = ("append", "apply", "write_doc", "create_plain", "unregister", "register")

    def test_no_brain_write_call_anywhere_in_the_box(self):
        for name in ("shtab_box.py", "shtab_box_run.py"):
            tree = ast.parse(_src(name))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                owner = getattr(func.value, "id", "")
                if owner in ("brain_writer", "bw"):
                    self.assertNotIn(func.attr, self.WRITERS,
                                     "%s пишет в мозг: %s.%s" % (name, owner, func.attr))
                    self.assertEqual(func.attr, "read_text",
                                     "%s зовёт у писателя не чтение: %s" % (name, func.attr))

    def test_only_one_node_name_and_it_lives_in_the_pure_module(self):
        """Имя узла названо ОДИН раз, берётся из чистого модуля и стои́т В КАНОНЕ.

        Второй экземпляр имени — это второй ящик, который разъедется с первым
        молча: читать станем один узел, а рассказывать владельцу про другой.

        Приставки ``KB_`` в имени нет по ЗАМЕРУ (живая проба 02.09: мост ответил
        «ИМЯ НЕ РАЗРЕШЕНО: 'KB_shtab_box' → канон 'shtab_box'»). Пин здесь именно
        затем, чтобы приставка не вернулась «для красоты».
        """
        self.assertEqual(sb.NODE_NAME, "shtab_box")
        self.assertFalse(sb.NODE_NAME.startswith("KB_"),
                         "переводчик имён моста приставку снимает — канон без неё")
        hands = _src("shtab_box_run.py")
        self.assertNotIn('"%s"' % sb.NODE_NAME, hands, "имя узла набрано в руках литералом")
        self.assertIn("shtab_box.NODE_NAME", hands, "руки обязаны брать имя у чистого модуля")

    def test_the_hands_never_reach_for_secrets(self):
        """Руки не читают конфигурации ради похода в мозг (класс 328, красный канал)."""
        hands = _src("shtab_box_run.py")
        for forbidden in ("BRIDGE_TOKEN", "BRIDGE_URL", "load_dotenv", "load_env"):
            self.assertNotIn(forbidden, hands, "руки полезли за секретами: %s" % forbidden)


# ═══════════════════════════ разбор узла ═══════════════════════════════


class TestParse(unittest.TestCase):

    def test_blocks_in_a_row_each_with_its_own_key(self):
        blocks, bad = sb.parse_node(_node(("alpha-1", "тело раз"), ("beta.2", "тело два")))
        self.assertEqual([b["key"] for b in blocks], ["alpha-1", "beta.2"])
        self.assertEqual([b["body"] for b in blocks], ["тело раз", "тело два"])
        self.assertEqual(bad, [])

    def test_text_between_blocks_is_ignored_and_does_not_leak_into_a_body(self):
        """Узел остаётся человеческим документом: заметки Штаба между блоками — не задание."""
        text = _node(("kk1", "тело")) + "\nсовершенно посторонняя заметка Штаба\n"
        blocks, bad = sb.parse_node(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["body"], "тело")
        self.assertEqual(bad, [])

    def test_unclosed_block_is_refused_not_taken(self):
        """Незакрытый блок — возможно, ОБРЕЗАННЫЙ. Взять его значило бы взять полузадание."""
        blocks, bad = sb.parse_node("[[ЗАДАЧА ключ=k1]]\n" + GOOD_BODY)
        self.assertEqual(blocks, [])
        self.assertEqual(len(bad), 1)
        self.assertIn("не закрыт", bad[0]["why"])

    def test_mismatched_close_key_is_refused(self):
        blocks, bad = sb.parse_node("[[ЗАДАЧА ключ=k1]]\nтело\n[[КОНЕЦ ключ=k2]]\n")
        self.assertEqual(blocks, [])
        self.assertIn("не совпал", bad[0]["why"])

    def test_two_blocks_with_the_same_key_refuse_BOTH(self):
        """Двойники не берутся НИ ОДИН: ключ — имя, которым дедуп их различает."""
        blocks, bad = sb.parse_node(_node(("dup", "первое"), ("dup", "второе")))
        self.assertEqual(blocks, [])
        self.assertEqual(len(bad), 2)
        self.assertTrue(all("двойники" in b["why"] for b in bad))

    def test_a_malformed_key_is_REPORTED_not_silently_swallowed(self):
        """Кривой ключ обязан дать ОТКАЗ СО СЛОВАМИ, а не исчезнуть.

        Кириллица в ключе запрещена (визуально одинаковые «с» дали бы два разных
        ключа и дедуп, промахивающийся молча), но узнать об этом Штаб должен из
        причины, а не по тому, что задание «почему-то не берут». Поэтому границу
        блока разбор ловит ШИРЕ формы ключа, а форму судят ворота.
        """
        blocks, bad = sb.parse_node("[[ЗАДАЧА ключ=алфа]]\nтело\n[[КОНЕЦ ключ=алфа]]\n")
        self.assertEqual(len(blocks), 1, "блок с кривым ключом пропал молча")
        self.assertEqual(bad, [])
        ok, reason, why = sb.check(blocks[0])
        self.assertFalse(ok)
        self.assertEqual(reason, "bad_key")
        self.assertIn("не по форме", why)

    def test_a_too_short_key_is_refused_by_the_gate_with_a_reason(self):
        blocks, _bad = sb.parse_node("[[ЗАДАЧА ключ=k1]]\nтело\n[[КОНЕЦ ключ=k1]]\n")
        self.assertEqual(len(blocks), 1)
        ok, reason, _why = sb.check(blocks[0])
        self.assertFalse(ok)
        self.assertEqual(reason, "bad_key")


# ═══════════════════════════ ворота приёма ═════════════════════════════


class TestGates(unittest.TestCase):

    def test_the_shipped_sample_of_prohibitions_passes_its_own_gate(self):
        """Образец, который мы предлагаем вставлять, ОБЯЗАН проходить наши же ворота.

        Иначе документация звала бы Штаб писать блоки, которые ящик не берёт, — и
        расхождение вскрылось бы только на живом задании.
        """
        ok, missing = sb.prohibitions_named(sb.PROHIBITIONS)
        self.assertTrue(ok, "образец не проходит собственные ворота: %s" % missing)

    def test_every_required_topic_is_actually_required(self):
        """Каждая тема запретов — НАСТОЯЩЕЕ условие, а не украшение.

        Вычёркиваем темы по одной из образца и требуем отказа. Без этого теста
        список ``REQUIRED`` мог бы содержать тему, которую ни один блок не
        обязан называть, и никто бы не заметил.
        """
        for name, words in sb.REQUIRED:
            body = GOOD_BODY
            for word in words:
                body = body.replace(word, "…").replace(word.upper(), "…")
            ok, missing = sb.prohibitions_named(body)
            self.assertFalse(ok, "тема %r ничего не требует" % name)
            self.assertIn(name, missing)

    def test_the_address_gate_uses_the_lane_canon_not_its_own_regex(self):
        """Адрес судит ``result_ref`` — канон полосы, а не наша регулярка.

        Разойдись два экземпляра канона, и ящик принимал бы блоки, которые судья
        ступени C адресом не считает: задача уехала бы в работу и не закрылась бы
        никогда.
        """
        self.assertIn("result_ref", _src("shtab_box.py"))
        for kind in result_ref.KINDS:
            body = NO_ADDR_BODY + "\n[result_ref: %s указатель]" % kind
            ok, reason, _why = sb.check({"key": "kk1", "body": body})
            self.assertTrue(ok, "вид %s каноном принят, а воротами нет (%s)" % (kind, reason))

    def test_the_dead_form_of_the_address_is_not_an_address(self):
        """Снятая форма ``[result_ref file:путь]`` адресом НЕ является — по канону."""
        body = NO_ADDR_BODY + "\n[result_ref file:docs/artifacts/x.md]"
        ok, reason, _why = sb.check({"key": "kk1", "body": body})
        self.assertFalse(ok)
        self.assertEqual(reason, "no_address")

    def test_empty_and_oversized_bodies_are_refused_with_their_own_reasons(self):
        ok, reason, _ = sb.check({"key": "kk1", "body": "   "})
        self.assertFalse(ok)
        self.assertEqual(reason, "empty")
        ok, reason, why = sb.check({"key": "kk1", "body": GOOD_BODY + "x" * sb.BODY_MAX})
        self.assertFalse(ok)
        self.assertEqual(reason, "too_long")
        self.assertIn(str(sb.BODY_MAX), why, "потолок обязан быть назван ЧИСЛОМ")

    def test_task_text_carries_the_block_verbatim(self):
        """Чужое задание едет ДОСЛОВНО: правка чужой постановки незаметна и незаконна."""
        text = sb.task_text({"key": "kk1", "body": GOOD_BODY}, TODAY)
        self.assertIn(GOOD_BODY, text)

    def test_the_marker_of_the_longest_key_still_fits_the_snapshot_goal(self):
        """Маркер — ПЕРВОЙ строкой, и при САМОМ ДЛИННОМ ключе он ещё умещается в слепок.

        По этой строке ряд узнаю́т трое: дедуп, суточный счёт и сводка контура.
        Слепок режет цель по 90 символов, ставя многоточие на 90-м
        (``queue_snapshot_pc.goal_line``), — значит закрывающая скобка маркера
        обязана попасть в первые 89. Не попади она туда, ``MARK_RE`` перестал бы
        совпадать, и счёт «задач от Штаба взято N» поехал бы ВНИЗ МОЛЧА: сводка
        показывала бы меньше, чем полоса взяла, и никто бы не узнал.
        """
        import queue_snapshot_pc

        text = sb.task_text({"key": "k" * sb.KEY_MAX, "body": GOOD_BODY}, TODAY)
        first = text.splitlines()[0]
        self.assertTrue(sb.MARK_RE.match(first))
        self.assertLessEqual(sb.MARK_FIXED + sb.KEY_MAX, queue_snapshot_pc.GOAL_MAX - 1,
                             "маркер с самым длинным ключом не влезает в goal слепка")
        # …и это не арифметика на бумаге: гоняем ЖИВУЮ обрезку слепка.
        self.assertTrue(sb.MARK_RE.match(queue_snapshot_pc.goal_line(text)),
                        "обрезанная слепком цель перестала узнаваться дедупом")

    def test_a_refused_block_gets_no_text_at_all(self):
        """Непринятому блоку текста не собирается: иначе отказ жил бы только в логе."""
        self.assertIsNone(sb.task_text({"key": "kk1", "body": NO_ADDR_BODY}, TODAY))
        self.assertIsNone(sb.task_text({"key": "kk1", "body": GOOD_BODY}, "вчера"))


# ═══════════════════════════ ОТРИЦАТЕЛЬНЫЕ ═════════════════════════════


class TestNegative(unittest.TestCase):
    """Пять отрицательных проверок задания — и положительный контроль к каждой."""

    # ── положительный контроль, общий для всех пяти ────────────────────
    def test_POSITIVE_CONTROL_a_healthy_box_actually_places_a_task(self):
        """Без этого теста все пять отрицательных проходят у ящика, не берущего НИЧЕГО."""
        q = FakeQueue()
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])
        self.assertEqual(rep["placed"][0]["key"], "kk1")
        self.assertEqual(len(q.tasks), 1)
        self.assertIn(GOOD_BODY, q.tasks[0][1])

    # ── 1. узел недоступен ─────────────────────────────────────────────
    def test_1_unreachable_node_says_UNKNOWN_and_takes_nothing(self):
        q = FakeQueue()
        rep = _tick(reader=_dead_reader("мост не ответил"), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertFalse(rep["node_ok"])
        self.assertIn("НЕИЗВЕСТНО", rep["why"])
        # И слово «пусто» в причине НЕ звучит: пустой ящик и недоступный — разные новости.
        self.assertNotIn("ящик пуст", rep["why"])

    def test_1b_an_empty_node_reads_differently_from_an_unreachable_one(self):
        """Контраст к предыдущему: пустой ящик говорит «пуст», а не «неизвестно»."""
        rep = _tick(_node(), place=True)
        self.assertIn("пуст", rep["why"])
        self.assertNotIn("НЕИЗВЕСТНО", rep["why"])

    def test_1c_an_empty_text_from_the_node_counts_as_a_read_failure(self):
        """Мост, отдавший пустую строку, — это отказ чтения, а не пустой док."""
        _text, ok, why = run.read_node("KB_x", reader=lambda name: "")
        self.assertFalse(ok)
        self.assertIn("отказом чтения", why)

    # ── 2. блок без адреса результата ──────────────────────────────────
    def test_2_block_without_a_result_address_is_refused_with_a_named_reason(self):
        q = FakeQueue()
        rep = _tick(_node(("kk1", NO_ADDR_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        reasons = " ".join(w for _k, w in rep["held"])
        self.assertIn("НЕ ПРИНЯТ (no_address)", reasons)
        self.assertIn("адрес результата", reasons)
        # Причина обязана доехать до строки лога, а не остаться внутри отчёта.
        self.assertIn("no_address", rep["why"])
        # …и НЕ ПРИКИДЫВАТЬСЯ ПОЛОМКОЙ МОСТА. Дорогое чтение `done` пропущено
        # потому, что ставить нечего, — это наша экономия, а не отказ прибора.
        # Назови мы её отказом, каждый оборот с кривым блоком кричал бы «сверить
        # нечем», и настоящий отказ моста утонул бы в этом крике.
        self.assertNotIn("не прочитаны", rep["why"])
        self.assertFalse(rep["build"]["marks_asked"])

    def test_2b_block_without_the_prohibitions_is_refused_too(self):
        q = FakeQueue()
        rep = _tick(_node(("kk1", NO_PROHIB_BODY)), queue=q, place=True)
        self.assertEqual(q.tasks, [])
        reasons = " ".join(w for _k, w in rep["held"])
        self.assertIn("no_prohibitions", reasons)

    def test_2c_a_refused_block_does_not_block_a_good_neighbour(self):
        """Отказ одному блоку не глушит ящик целиком — иначе один кривой блок Штаба
        останавливал бы канал навсегда, и выглядело бы это как «ящик сломался»."""
        q = FakeQueue()
        rep = _tick(_node(("bad", NO_ADDR_BODY), ("good", GOOD_BODY)), queue=q, place=True)
        self.assertEqual([r["key"] for r in rep["placed"]], ["good"])

    # ── 3. тот же ключ второй раз ──────────────────────────────────────
    def test_3_the_same_key_is_never_taken_twice(self):
        """Дедуп по ЖИВОЙ ОЧЕРЕДИ, и закрытый ряд считается наравне с открытым."""
        q = FakeQueue(closed=[_row("kk1")])
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertIn("уже брали", " ".join(w for _k, w in rep["held"]))

    def test_3b_a_key_taken_on_a_previous_DAY_is_still_never_taken_again(self):
        """«Не берётся НИКОГДА» — это не «не берётся сегодня»: дата в маркере другая,
        а ключ тот же, и повтор всё равно запрещён."""
        q = FakeQueue(closed=[_row("kk1", day="2026-08-01")])
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertIn("уже брали", " ".join(w for _k, w in rep["held"]))

    def test_3c_POSITIVE_a_different_key_next_to_a_taken_one_still_goes(self):
        q = FakeQueue(closed=[_row("kk1")])
        rep = _tick(_node(("kk1", GOOD_BODY), ("kk2", GOOD_BODY)), queue=q, place=True)
        self.assertEqual([r["key"] for r in rep["placed"]], ["kk2"])

    def test_3d_the_marker_of_a_placed_row_is_readable_back_by_the_dedup(self):
        """Замок круга: то, что мы СТАВИМ, обязано узнаваться тем, чем мы ДЕДУПИМ.
        Разойдись форма маркера и его регулярка — дедуп молча перестал бы работать."""
        text = sb.task_text({"key": "kk1", "body": GOOD_BODY}, TODAY)
        self.assertEqual(sb.markers([{"task_text": text}]), [(TODAY, "kk1")])

    # ── 4. стоп-файл ───────────────────────────────────────────────────
    def test_4_the_stop_file_kills_the_box_entirely(self):
        root = tempfile.mkdtemp(prefix="shtabbox_off_")
        with io.open(os.path.join(root, run.STOP_FILE), "w", encoding="utf-8") as fh:
            fh.write("стоп\n")
        q = FakeQueue()
        reader = _reader(_node(("kk1", GOOD_BODY)))
        rep = _tick(reader=reader, queue=q, place=True, root=root)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertTrue(rep["off"])
        self.assertIn(run.STOP_FILE, rep["why"])
        # И узла мы даже не спрашивали: выключенный ящик не ходит в мозг.
        self.assertEqual(reader.seen, [])
        # Ни очереди: выключено значит выключено, а не «прочитаем и не поставим».
        self.assertEqual(q.asked, [])

    def test_4b_POSITIVE_the_same_root_without_the_file_places_normally(self):
        """Тот же корень БЕЗ файла — задача встаёт. Значит гасит именно файл."""
        root = tempfile.mkdtemp(prefix="shtabbox_on_")
        q = FakeQueue()
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True, root=root)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])

    def test_4c_an_unanswerable_stop_check_means_OFF(self):
        """«Не знаю, есть ли файл» у аварийного рубильника значит СТОП, а не «работай»."""
        real = os.path.exists
        try:
            os.path.exists = lambda p: (_ for _ in ()).throw(OSError("диск не отвечает"))
            off, why = run.stopped(tempfile.mkdtemp(prefix="shtabbox_err_"))
        finally:
            os.path.exists = real
        self.assertTrue(off)
        self.assertIn("ВЫКЛЮЧЕНО", why)

    # ── 5. в очереди задача владельца ──────────────────────────────────
    def test_5_an_owner_task_in_the_queue_blocks_every_block(self):
        q = FakeQueue(busy=True, busy_ids=[321])
        rep = _tick(_node(("kk1", GOOD_BODY), ("kk2", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertIn("#321", rep["why"])
        self.assertIn("задача владельца", rep["why"])

    def test_5b_the_owner_lock_saves_the_expensive_read_of_closed_rows(self):
        """Занятая полоса не платит 27 секунд витка за ответ, который не понадобится."""
        q = FakeQueue(busy=True, busy_ids=[321])
        _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertNotIn(run.CLOSED_STATUSES, q.asked)

    def test_5c_POSITIVE_a_free_lane_places_the_very_same_block(self):
        q = FakeQueue(busy=False)
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])


# ═══════════════════════════ потолки ═══════════════════════════════════


class TestCeiling(unittest.TestCase):

    def test_not_more_than_one_per_tick(self):
        q = FakeQueue()
        rep = _tick(_node(("kk1", GOOD_BODY), ("kk2", GOOD_BODY), ("kk3", GOOD_BODY)),
                    queue=q, place=True)
        self.assertEqual(len(rep["placed"]), 1)
        self.assertIn("не больше 1 за виток", " ".join(w for _k, w in rep["held"]))

    def test_the_daily_ceiling_counts_CLOSED_rows_too(self):
        """Урок ступени E дословно: счёт по одним открытым рядам мерит одновременность."""
        closed = [_row("aa1"), _row("bb1"), _row("cc1")]
        q = FakeQueue(closed=closed)
        rep = _tick(_node(("kk9", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertIn("суточный потолок исчерпан", " ".join(w for _k, w in rep["held"]))

    def test_yesterdays_rows_do_not_eat_todays_budget(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ потолка: правило, глушащее всё, прошло бы тест выше."""
        closed = [_row("aa1", day="2026-09-01"), _row("bb1", day="2026-09-01"),
                  _row("cc1", day="2026-09-01")]
        rep = _tick(_node(("kk9", GOOD_BODY)), queue=FakeQueue(closed=closed), place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])

    def test_unread_closed_rows_mean_EXHAUSTED_not_EMPTY(self):
        """Третий исход: корпус не прочитан → день исчерпан, и сказано это ДРУГИМИ словами."""
        q = FakeQueue(closed_ok=False)
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertIn("НЕИЗВЕСТНО", rep["why"])
        self.assertIn("исчерпанным", rep["why"])

    def test_the_ceiling_number_is_named_and_reused_not_reinvented(self):
        """Потолок и дедуп берутся у ступени E, а не пишутся вторым экземпляром."""
        self.assertEqual(sb.DAILY_BUDGET, 3)
        self.assertIs(sb.budget_left.__wrapped__ if hasattr(sb.budget_left, "__wrapped__")
                      else recon_auto.budget_left, recon_auto.budget_left)
        self.assertEqual(sb.budget_left([], TODAY, 3, marks_ok=False), 0)
        self.assertEqual(sb.budget_left([(TODAY, "a")], TODAY, 3), 2)
        body = _src("shtab_box.py")
        self.assertIn("recon_auto.markers", body)
        self.assertIn("recon_auto.budget_left", body)

    def test_recon_auto_markers_still_works_exactly_as_before(self):
        """Правка чужого модуля обязана быть обратно совместимой БАЙТ-В-БАЙТ по поведению."""
        row = {"task_text": "[разведка дата=2026-09-02 ключ=abc123abc123] что-то"}
        self.assertEqual(recon_auto.markers([row]), [("2026-09-02", "abc123abc123")])
        self.assertEqual(recon_auto.markers([row], "ask"), [])
        # …и наш маркер прежними глазами НЕ виден: два счёта не смешиваются.
        self.assertEqual(recon_auto.markers([_row("kk1")]), [])
        self.assertEqual(sb.markers([row]), [])


# ═══════════════════════════ руки ══════════════════════════════════════


class TestHands(unittest.TestCase):

    def test_dry_run_touches_no_queue_row(self):
        q = FakeQueue()
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=False)
        self.assertEqual(q.tasks, [])
        self.assertEqual(rep["placed"], [])
        self.assertIn("kk1", rep["texts"])
        self.assertIn("сухой ход", " ".join(w for _k, w in rep["held"]))

    def test_the_expensive_closed_corpus_is_not_read_on_an_empty_box(self):
        """Пустой ящик — обычное состояние, и платить за него полминуты витка нельзя."""
        q = FakeQueue()
        _tick(_node(), queue=q, place=True)
        self.assertNotIn(run.CLOSED_STATUSES, q.asked)

    def test_the_closed_corpus_IS_read_when_there_is_a_candidate(self):
        """Положительный контроль экономии: когда есть что ставить — читаем всё."""
        q = FakeQueue()
        _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertIn(run.CLOSED_STATUSES, q.asked)

    def test_the_box_places_a_GREEN_row_and_never_an_approval_card(self):
        """Ящик добавляет источник задач, а не право их исполнять: ряд обычный, зелёный.

        Ни ``claim``, ни ``set_needs_approval`` — карточки и ворота этой веткой не
        трогаются, задачу подбирает штатный ``process_new`` под обычным гардом.

        Судим по ДЕЙСТВИЮ (обход AST на ВЫЗОВЫ), а не по подстроке: имена этих
        методов законно стоя́т в докстрингах, которые объясняют, почему их здесь
        нет, — и грепающая проверка ловила бы собственное объяснение.
        """
        forbidden = ("set_needs_approval", "place_ask", "approve_task", "claim_task")
        tree = ast.parse(_src("shtab_box_run.py"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name:
                    called.add(name)
        for name in forbidden:
            self.assertNotIn(name, called, "ящик тронул карточки/ворота: %s()" % name)

    def test_a_dead_queue_places_nothing_blindly(self):
        q = FakeQueue(ok=False)
        rep = _tick(_node(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(q.tasks, [])
        self.assertIn("очередь недоступна", rep["why"])

    def test_the_journal_line_names_the_key_and_the_row(self):
        line = sb.index_line({"key": "kk1"}, 707, TODAY)
        self.assertIn("kk1", line)
        self.assertIn("#707", line)
        self.assertIn(sb.NODE_NAME, line)

    def test_no_state_file_is_written_anywhere(self):
        """Реестра на диске у ящика нет: его съел бы первый self-update, а дедуп обязан
        пережить всё. Источник истины один — живая очередь."""
        root = tempfile.mkdtemp(prefix="shtabbox_state_")
        before = sorted(os.listdir(root))
        _tick(_node(("kk1", GOOD_BODY)), queue=FakeQueue(), place=True, root=root)
        self.assertEqual(sorted(os.listdir(root)), before, "ящик оставил файл в корне")


# ═══════════════════════════ врезка ════════════════════════════════════


class TestWiring(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")
        import pc_orchestrator

        cls.o = pc_orchestrator

    def test_the_branch_is_called_from_poll_loop_and_throttled(self):
        body = _src("pc_orchestrator.py")
        self.assertIn("maybe_shtab_box()", body)
        self.assertTrue(hasattr(self.o, "maybe_shtab_box"))
        self.assertEqual(self.o.SHTAB_BOX_LIMIT, sb.TICK_LIMIT)
        self.assertEqual(self.o.SHTAB_BOX_BUDGET, sb.DAILY_BUDGET)

    def test_the_stop_file_name_matches_the_daemon_switch(self):
        """Стоп-файл ящика и рубильник демона — ОДИН файл, а не два похожих имени."""
        self.assertEqual(os.path.basename(self.o._flag_off_file("SHTAB_BOX")), run.STOP_FILE)

    def test_the_daemon_branch_is_off_by_the_same_file(self):
        self.assertTrue(callable(self.o._shtab_box_on))

    def test_selfupdate_registry_knows_the_new_modules(self):
        """Реестр self-update молчал бы о новых файлах — и правка не доехала бы до демона."""
        for mod in ("shtab_box.py", "shtab_box_run.py"):
            self.assertIn(mod, self.o._ORCH_LAZY_UNCOVERED)

    def test_the_placed_row_counts_as_owner_work_for_the_next_turn(self):
        """СЛЕДСТВИЕ, названное вслух: взятое задание Штаба само становится работой
        владельца, поэтому второе не берётся, пока первое открыто. Это ЛИШНИЙ замок
        сверх суточного, и знать о нём надо — иначе «почему взято только одно» будет
        выглядеть поломкой."""
        row = {"from": run.FROM, "task_text": sb.task_text({"key": "kk1", "body": GOOD_BODY},
                                                           TODAY), "status": "new"}
        self.assertTrue(self.o._is_owner_work(row, set()))

    # ── видимость: сводка контура ─────────────────────────────────────
    def test_the_digest_counts_taken_tasks_separately(self):
        rows = [{"goal": _row("aa1")["task_text"][:90]},
                {"goal": _row("bb1")["task_text"][:90]},
                {"goal": "[разведка дата=%s ключ=abc123abc123] чужое" % TODAY}]
        self.assertEqual(cd.shtab_taken(rows, TODAY), 2)

    def test_the_digest_says_UNKNOWN_when_the_snapshot_is_dead(self):
        """Ноль вместо незнания читается как «Штаб ничего не клал» — а это утверждение."""
        self.assertIsNone(cd.shtab_taken(None, TODAY))
        self.assertIn("НЕИЗВЕСТНО", cd.shtab_line(None, TODAY))

    def test_the_count_line_is_printed_always_even_on_a_calm_digest(self):
        """Спокойная сводка схлопывается в одну фразу — счёт ящика обязан выжить в ней."""
        report = {"interval": cd.INTERVAL_SEC, "since_words": "a", "now_words": "b",
                  "day": TODAY, "shtab_taken": 2, "closed_count": 0,
                  "series": {"streak": 0, "target": 30, "moved": 0, "window": 0, "service": 0},
                  "readings": {k: [cd.reading(k, "ок", src="queue", read_at=1, now=1)]
                               for k in cd.TOPIC_KEYS}}
        text = cd.render(report)
        self.assertIn("ЯЩИК ШТАБА:", text)
        self.assertIn("взято 2", text)
        self.assertIn("задач от Штаба взято 2", cd.journal_line(report))

    def test_the_digest_takes_rows_from_BOTH_halves_of_the_snapshot(self):
        """Счёт по одним открытым рядам мерил бы «сколько сейчас в работе»."""
        snap = {"open": {"1": {"id": 1, "goal": _row("aa1")["task_text"][:90]}},
                "closed": {"2": {"id": 2, "at": 1.0, "goal": _row("bb1")["task_text"][:90],
                                 "outcome": "done"}}}
        self.assertEqual(cd.shtab_taken(cdr.all_rows(snap), TODAY), 2)
        self.assertIsNone(cdr.all_rows(None))


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
