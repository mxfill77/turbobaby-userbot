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
* :class:`TestGates` — ворота приёма: ЗАПРЕТЫ и АДРЕС РЕЗУЛЬТАТА. С 02.09.2026
  форма адреса ОДНА на ящик и на судью закрытия, и проверяются ОБЕ половины
  сведения: чужая форма (канон ``result_ref``) воротами не берётся, а принятая —
  не только берётся, но и СУДИТСЯ живым ``done_judge_pc`` до вердикта «сделано».
* :class:`TestCeiling` — суточный потолок и лимит витка; оба restart-proof, оба
  считаются по маркерам ЖИВОЙ очереди чужим устройством (ступень E). С 04.09.2026
  предмет проверки здесь ещё и ОБОСНОВАНИЕ числа: потолок 8 — ограничитель ущерба
  от разгона, и комментарий, выдающий его за долю мощности, тест не пропускает.
* :class:`TestLaneDay` — сутки ящика по МЕСТНОМУ времени полосы (UTC+7): обе
  стороны границы, стык с маркерами прежнего отсчёта (день не исчерпывается дважды
  и обнуляется один раз) и неприкосновенность общей ``review_intake.today_utc``.
* :class:`TestHands` — руки: сухой ход очередь не трогает, дорогое чтение `done`
  не спрашивается впустую, боевой ход ставит ряд.
* :class:`TestWiring` — врезка в демона и в сводку контура.
"""
from __future__ import annotations

import ast
import datetime
import io
import os
import re
import tempfile
import unittest

import contour_digest as cd
import contour_digest_run as cdr
import done_judge_pc as dj
import recon_auto
import result_ref
import review_intake            # ПРЕЖНЯЯ дверь суток: держим доказательство, что она НЕ тронута
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

#
# АДРЕС РЕЗУЛЬТАТА стои́т здесь В ФОРМЕ СУДЬИ ЗАКРЫТИЯ (правка 02.09.2026): ворота
# ящика и судья читают ОДНУ форму, и образец набора обязан быть той же формы —
# иначе набор зеленел бы на блоке, который живой судья закрыть не сможет.
ADDR_LINE = ("АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 02.09 "
             "со словами доля неизвестного за август")

# Прежняя, СНЯТАЯ форма ворот — канон Штаба `result_ref`. Живёт в наборе ровно как
# ОТРИЦАТЕЛЬНЫЙ образец: судья её не читает, значит ворота её не берут.
FOREIGN_ADDR = result_ref.marker(result_ref.make("file",
                                                 "docs/artifacts/2026-09-02-dolya.md"))

GOOD_BODY = (
    "ЦЕЛЬ: пересчитать, сколько строк очереди полосы закрылось вердиктом «неизвестно» за август,\n"
    "и назвать долю числом.\n\n"
    + sb.PROHIBITIONS + "\n\n"
    "ПРИЗНАК СДЕЛАННОСТИ: в артефакте стои́т число и команда, которой оно получено.\n\n"
    + ADDR_LINE
)

# Тело БЕЗ адреса результата — вырезана ровно последняя строка.
NO_ADDR_BODY = GOOD_BODY.replace(ADDR_LINE, "").strip()

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


# ═══════════════════ ПАПКА-ЗАГЛУШКА (источник с 03.09.2026) ═══════════════════
# Задание — ОТДЕЛЬНЫЙ ДОКУМЕНТ папки мозга, значит и заглушек теперь две: одна
# перечисляет папку (имена + file id), вторая отдаёт тело по id. Разделены они не
# для красоты — ровно так устроен живой канал, и слить их в одну значило бы
# спрятать от набора обе развилки переезда: «перечисление молчит» и «тело не
# читается» — РАЗНЫЕ отказы с разными исходами.

# Шапка узла БЕЗ единого блока: с 03.09 источником задач она быть перестала.
HEAD_TEXT = "═══ ЯЩИК ЗАДАНИЙ ШТАБА — полоса ПК ═══\nЗадания лежат отдельными документами папки.\n"


def _box(*pairs):
    """Задания → список пар для :func:`_tick`.

    Пара — ``(ключ, тело)``; имя документа собирается каноном (:func:`sb.doc_name`).
    Тройка ``(имя, тело, "raw")`` задаёт имя ДОСЛОВНО — ею живут проверки префикса,
    отзыва и двойников, где имя и есть предмет.
    """
    return list(pairs)


def _files(pairs):
    """Пары → (дети папки, тела по file id). Форма ответа — живая (замер 03.09:
    у файла ровно три поля — name, id, mime)."""
    files, bodies = [], {}
    for i, pair in enumerate(pairs or ()):
        name = pair[0] if len(pair) > 2 else sb.doc_name(pair[0])
        fid = "fid-%02d" % i
        files.append({"name": name, "id": fid, "mime": "text/plain"})
        bodies[fid] = pair[1]
    return files, bodies


def _ready(*pairs):
    """Пары → документы, ГОТОВЫЕ К ОТБОРУ: ровно такими их отдают руки.

    Чистый :func:`sb.parse_folder` тел не знает — тело стои́т отдельного похода в
    мост, и приделывает его :func:`run.build`. Проверки чистого слоя обязаны
    повторять ЖИВУЮ форму документа, а не удобную: собери мы их иначе, `select`
    зеленел бы на структуре, которой в бою не бывает.
    """
    files, bodies = _files(pairs)
    docs, _bad = sb.parse_folder(files)
    for doc in docs:
        doc["body"] = bodies[doc["id"]]
    return docs


def _lister(files, ok=True, truncated=False, why="мост не ответил", extra=()):
    """Перечислитель папки-заглушка. Помнит, с каким префиксом его звали.

    ``extra`` — ЧУЖИЕ дети папки (без нашего префикса): они обязаны приезжать в
    ответе и обязаны отсеиваться нами, а не только мостом.
    """
    seen = []

    def lst(prefix):
        seen.append(prefix)
        if not ok:
            raise RuntimeError(why)
        all_files = list(files) + [dict(f) for f in extra]
        got = [f for f in all_files if str(f.get("name") or "").startswith(prefix or "")]
        return {"ok": True, "folder_id": "F1", "count": len(got), "files": got,
                "folders": [], "folders_count": 0, "prefix": prefix or None,
                "scanned": len(all_files), "truncated": bool(truncated)}

    lst.seen = seen
    return lst


def _doc_reader(bodies, dead=()):
    """Читатель тела по file id. ``dead`` — id, на которых чтение падает."""
    seen = []

    def read(fid):
        seen.append(fid)
        if fid in dead:
            raise RuntimeError("тело документа не отдалось")
        return bodies.get(fid, "")

    read.seen = seen
    return read


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
        self.lanes = []
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

    def place_task(self, text, lane=None):
        """Постановка-заглушка. ПОЛОСА ЗАПОМИНАЕТСЯ, а не проглатывается: с
        03.09.2026 «куда поставили» — предмет проверки наравне с «поставили ли»."""
        self.next_id += 1
        self.tasks.append((self.next_id, text))
        self.lanes.append(str(lane or ""))
        return True, self.next_id, ""


class _HoldDoor(object):
    """Дверь извещения об ОСТАНОВКЕ-заглушка в форме :func:`run.notify_hold`.

    ФОРМА ПОВТОРЕНА ДОСЛОВНО — ``(текст, кнопки) → (ok, канал, причина)``: мок,
    разошедшийся с живой дверью, зеленел бы молча, и это ровно тот класс, на котором
    полоса ПК уже обжигалась (`CLAUDE.md`, «Форматы»).

    Живёт ЗДЕСЬ, а не в наборе сигналов, по той же причине, по которой там же живут
    `FakeQueue` и `_reader`: предмет один, и второй экземпляр заглушки разошёлся бы с
    первым молча.
    """

    def __init__(self, ok=True, why="инбокс закрыт", boom=None):
        self._ok, self._why, self._boom = ok, why, boom
        self.sent = []

    def __call__(self, text, markup=None):
        self.sent.append((text, markup))
        if self._boom:
            raise RuntimeError(self._boom)
        return (self._ok, "inbox" if self._ok else "", "" if self._ok else self._why)


def _tick(docs=None, reader=None, queue=None, place=False, root=None, node_text=None,
          lister=None, doc_reader=None, extra=(), **kw):
    """Оборот ящика на заглушках. Корень — ВСЕГДА временный каталог, если не назван.

    ``docs`` — задания папки парами (см. :func:`_box`). ``lister``/``doc_reader``
    подменяются целиком там, где предмет проверки — сам отказ чтения.
    """
    root = root or tempfile.mkdtemp(prefix="shtabbox_")
    files, bodies = _files(docs or ())
    # ДВЕРЬ ИЗВЕЩЕНИЯ ОБ ОСТАНОВКЕ ЗАГЛУШЕНА ПО УМОЛЧАНИЮ ВО ВСЁМ НАБОРЕ (06.09.2026).
    # Условие задания дословно: «пробных извещений владельцу не слать ни одного — у
    # боевого пути и проверки разные вызовы, из проверки не уходит НИЧЕГО». Чужой
    # замок `dispatch_notify` (замок 1) это и так ловит — ЗАМЕРЕНО: до заглушки набор
    # доходил до боевой двери и получал «проба: живая отправка владельцу запрещена», —
    # но замок своего предмета обязан стоять здесь, а не в соседнем модуле.
    kw.setdefault("hold_notify_fn", _HoldDoor())
    return run.tick(root=root, place=place, queue=queue if queue is not None else FakeQueue(),
                    lister=lister if lister is not None else _lister(files, extra=extra),
                    doc_reader=doc_reader if doc_reader is not None else _doc_reader(bodies),
                    reader=reader or _reader(node_text if node_text is not None else HEAD_TEXT),
                    clock=lambda tz: __import__("datetime").datetime(2026, 9, 2, 12, 0, tzinfo=tz),
                    **kw)


# ═══════════════════════════ чистота ═══════════════════════════════════


class TestPurity(unittest.TestCase):
    """SHTAB_BOX_PURE — чистая логика остаётся чистой."""

    FORBIDDEN_MODULES = ("os", "io", "time", "subprocess", "socket", "requests",
                         "urllib", "pathlib", "shutil", "sqlite3", "brain_writer")
    FORBIDDEN_CALLS = ("open", "getenv", "system", "popen", "now", "utcnow", "time")

    # ЧИСТЫХ МОДУЛЕЙ У ЯЩИКА ДВА (04.09.2026): к отбору добавилась ПРИЁМКА
    # (`shtab_box_accept`). Она судит текст текстом — тело задания против текста
    # артефакта — и мира ей не нужно ровно так же: диск живёт в руках
    # (`shtab_box_run.artifact_of`), а сюда приезжает уже прочитанная строка.
    # Инвариант распространён на неё СРАЗУ, а не «когда-нибудь»: чистота, не
    # закреплённая с первого дня, теряется первой же удобной правкой.
    PURE = ("shtab_box.py", "shtab_box_accept.py")

    def test_no_world_in_the_pure_module(self):
        for name in self.PURE:
            tree = ast.parse(_src(name))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], self.FORBIDDEN_MODULES,
                                         "%s завёл мир: %s" % (name, alias.name))
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], self.FORBIDDEN_MODULES,
                                     "%s завёл мир: %s" % (name, node.module))
                if isinstance(node, ast.Call):
                    call = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    self.assertNotIn(call, self.FORBIDDEN_CALLS,
                                     "%s позвал мир: %s" % (name, call))

    def test_the_module_knows_no_queue_and_no_paths(self):
        body = _src("shtab_box.py")
        for forbidden in ("get_pending", "enqueue_pc_task", "D:\\", "__file__"):
            self.assertNotIn(forbidden, body, "в чистом модуле не место %s" % forbidden)

    def test_the_box_borrows_only_the_address_reader_from_the_judge(self):
        """У судьи закрытия ящик берёт РОВНО читатель адреса — и ничего с руками.

        `done_judge_pc` умеет писать на диск (пакеты V0 и реестр вердиктов), и с
        02.09 чистый модуль его импортирует. Список разрешённого держит эти руки
        снаружи: без него одна опечатка (`note`, `_packets`) завела бы ящику диск,
        а инвариант чистоты смотрит на ИМЕНА МОДУЛЕЙ и такую правку пропустил бы.
        """
        allowed = {"read_address"}
        seen = set()
        for node in ast.walk(ast.parse(_src("shtab_box.py"))):
            if isinstance(node, ast.Attribute) and getattr(node.value, "id", "") == "done_judge_pc":
                seen.add(node.attr)
        self.assertTrue(seen, "ящик перестал спрашивать судью — формы разъедутся снова")
        self.assertEqual(seen - allowed, set(), "ящик берёт у судьи лишнее: %s" % (seen - allowed))


# ═══════════════════════════ только чтение ═════════════════════════════


class TestReadsOnly(unittest.TestCase):
    """SHTAB_BOX_READS_ONLY — ящик читает один узел и НЕ ПИШЕТ в мозг ни одной веткой.

    Самый дорогой инвариант набора. Граница доверия ящика проходит по тому, ЧЕЙ
    это узел: пишет в него Штаб, читает полоса. Ветка записи с нашей стороны
    превратила бы ящик в машину, ставящую себе задачи собственными словами, и
    заметить это было бы нечем — текст в очереди выглядел бы точно так же.
    """

    WRITERS = ("append", "apply", "write_doc", "create_plain", "unregister", "register")

    # ДВЕРЕЙ ЧТЕНИЯ СТАЛО ДВЕ (переезд 03.09.2026), и обе названы ПОИМЁННО, а не
    # «всё, что не в WRITERS». Белый список против чёрного здесь принципиален:
    # чёрный пропустил бы любую новую функцию писателя, включая пишущую, — а
    # именно эта ошибка стоила бы всего инварианта.
    READERS = ("read_text", "list_folder")

    def test_no_brain_write_call_anywhere_in_the_box(self):
        # ПРИЁМКА И ДОЖИМ ПОПАДАЮТ ПОД ТОТ ЖЕ ИНВАРИАНТ (04.09.2026), и это не
        # формальность: дожим СТАВИТ задачи, то есть у него был бы самый понятный
        # повод «заодно отметить попытку в документе Штаба». Ни одной такой ветки
        # нет — и обхода AST теперь не миновать ни одному из трёх файлов ящика.
        for name in ("shtab_box.py", "shtab_box_run.py", "shtab_box_accept.py"):
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
                    self.assertIn(func.attr, self.READERS,
                                  "%s зовёт у писателя не чтение: %s" % (name, func.attr))

    def test_both_reading_doors_are_really_read_only_in_the_writer_itself(self):
        """Белый список выше стои́т на утверждении «обе двери — чтение». Проверяем ЕГО.

        `list_folder` заведена этим заходом, и написать её можно было по-разному.
        Инвариант ящика опирается на то, что она НИЧЕГО НЕ МЕНЯЕТ: экшен моста у неё
        один, GET-овый, и никакого POST-экшена в её теле нет.
        """
        tree = ast.parse(_src("brain_writer.py"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "list_folder")
        body = ast.dump(fn)
        self.assertIn("list_brain_folder", body, "дверь перестала звать перечисление")
        for forbidden in ("_post", "write_doc", "register_brain_doc", "create_brain_plain",
                          "move_into_brain"):
            self.assertNotIn(forbidden, body, "перечисление папки завело запись: %s" % forbidden)

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


# ═══════════════════════════ источник: папка ═══════════════════════════


class TestFolderSource(unittest.TestCase):
    """ПЕРЕЕЗД 03.09.2026: задание — документ папки, а не блок в узле.

    Всё, что проверяется здесь, до 03.09 не существовало ни одной строкой — поэтому
    ни один прежний зелёный тест об этом не говорит ничего.
    """

    # ── префикс ────────────────────────────────────────────────────────
    def test_the_prefix_lives_in_exactly_one_place(self):
        """Второй экземпляр префикса — второй ящик, который разъедется молча."""
        self.assertEqual("shtab_task_", sb.TASK_PREFIX)
        hands = _src("shtab_box_run.py")
        self.assertNotIn('"%s"' % sb.TASK_PREFIX, hands, "префикс набран в руках литералом")
        self.assertIn("shtab_box.TASK_PREFIX", hands, "руки обязаны брать префикс у чистого модуля")

    def test_the_prefix_does_not_swallow_the_header_or_the_probe(self):
        """ЗАМЕР 03.09, а не вкус: `shtab_box`→2 документа, `shtab_box_`→1 (проба),
        `shtab_task_`→0. Возьми мы первые два — источником задач стали бы САМА ШАПКА
        и документ-проба, который дословно говорит «Заданий не несёт»."""
        for foreign in ("shtab_box", "shtab_box_probe", "KB_MASTER", "cowork_esign_2026-09-03"):
            self.assertFalse(foreign.startswith(sb.TASK_PREFIX),
                             "префикс затягивает чужого ребёнка папки: %s" % foreign)

    def test_foreign_children_are_filtered_by_US_and_not_only_by_the_bridge(self):
        """Отбор моста — параметр запроса; решает, что считать заданием, наш код.

        Проверяем на перечислении, которое ЧУЖИХ ДЕТЕЙ ВЕРНУЛО (отбор на той стороне
        не сработал): ящик обязан отсеять их сам, а не взять `KB_MASTER` за задание.
        """
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True,
                    extra=[{"name": "KB_MASTER", "id": "x1", "mime": "d"},
                           {"name": "shtab_box", "id": "x2", "mime": "d"},
                           {"name": "shtab_box_probe", "id": "x3", "mime": "d"}])
        self.assertEqual(["kk1"], [r["key"] for r in rep["placed"]], rep["why"])
        self.assertEqual(1, rep["docs"], "чужой ребёнок папки заехал в задания")

    def test_the_key_is_the_document_name_without_the_prefix(self):
        docs, bad = sb.parse_folder([{"name": sb.doc_name("addr-canon.0903"), "id": "f1"}])
        self.assertEqual([], bad)
        self.assertEqual("addr-canon.0903", docs[0]["key"])
        self.assertEqual(sb.doc_name("addr-canon.0903"), docs[0]["name"])

    def test_a_name_of_bare_prefix_has_no_key_and_says_so(self):
        docs, bad = sb.parse_folder([{"name": sb.TASK_PREFIX, "id": "f1"}])
        self.assertEqual([], docs)
        self.assertIn("ключа в нём нет", bad[0]["why"])

    def test_a_document_without_a_file_id_is_refused_with_a_named_reason(self):
        """Читать такой документ нечем: по имени мост его не отдаёт (замер 03.09)."""
        docs, bad = sb.parse_folder([{"name": sb.doc_name("kk1"), "id": ""}])
        self.assertEqual([], docs)
        self.assertIn("нет file id", bad[0]["why"])

    # ── порядок разбора ────────────────────────────────────────────────
    def test_the_order_is_by_NAME_and_it_is_OURS_not_the_bridges(self):
        """Порядок назначен явно и НЕ берётся у моста.

        Мост уже сортирует, но при усечении сортирует произвольный кусок; кроме
        того «первый» обязан быть вопросом, на который отвечает НАШ код. Подаём
        нарочно перемешанный список — выйти он обязан по имени.
        """
        names = ["03-vtoroe", "01-pervoe", "02-srednee"]
        files = [{"name": sb.doc_name(n), "id": "f%d" % i} for i, n in enumerate(names)]
        docs, _bad = sb.parse_folder(files)
        self.assertEqual(["01-pervoe", "02-srednee", "03-vtoroe"], [d["key"] for d in docs])

    def test_the_first_by_name_is_the_one_actually_taken(self):
        """Порядок не украшение отчёта: при потолке 1 за виток берётся ПЕРВЫЙ по имени."""
        q = FakeQueue()
        rep = _tick(_box(("bbb-vtoroe", GOOD_BODY), ("aaa-pervoe", GOOD_BODY)),
                    queue=q, place=True)
        self.assertEqual(["aaa-pervoe"], [r["key"] for r in rep["placed"]], rep["why"])

    def test_the_order_is_the_SAME_on_two_ticks_in_a_row(self):
        """Предсказуемость — это одинаковость на двух витках, а не красота списка.

        Папка отдаёт детей в порядке Drive (произвольном), и он может меняться от
        вызова к вызову. Подаём тот же набор в ОБРАТНОМ порядке — ответ обязан
        совпасть.
        """
        pairs = [("bbb", GOOD_BODY), ("aaa", GOOD_BODY), ("ccc", GOOD_BODY)]
        first, _b1 = sb.parse_folder(_files(pairs)[0])
        second, _b2 = sb.parse_folder(_files(list(reversed(pairs)))[0])
        self.assertEqual([d["key"] for d in first], [d["key"] for d in second])
        self.assertEqual(["aaa", "bbb", "ccc"], [d["key"] for d in first])

    # ── двойники ───────────────────────────────────────────────────────
    def test_two_documents_with_the_same_key_refuse_BOTH(self):
        """Drive разрешает двум файлам одно имя, а ключ — имя задачи для дедупа."""
        files = [{"name": sb.doc_name("dup"), "id": "f1"},
                 {"name": sb.doc_name("dup"), "id": "f2"}]
        docs, bad = sb.parse_folder(files)
        self.assertEqual([], docs)
        self.assertEqual(2, len(bad))
        self.assertIn("двойники не берём ни один", bad[0]["why"])

    def test_a_revoked_twin_still_collides_with_a_fresh_one_of_the_same_key(self):
        """«Снял и положил заново под тем же ключом» — это НЕ новая задача: маркер
        очереди у неё был бы старый, и дедуп молча счёл бы её выполненной."""
        files = [{"name": sb.doc_name("dup"), "id": "f1"},
                 {"name": sb.doc_name("dup") + ".СНЯТО", "id": "f2"}]
        docs, bad = sb.parse_folder(files)
        self.assertEqual([], docs)
        self.assertEqual(2, len(bad))

    # ── отзыв переименованием ──────────────────────────────────────────
    def test_a_revoked_document_is_not_taken_and_the_reason_is_in_WORDS(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗАДАНИЯ: отозванный документ не берётся."""
        q = FakeQueue()
        rep = _tick(_box((sb.doc_name("kk1") + ".СНЯТО", GOOD_BODY, "raw")),
                    queue=q, place=True)
        self.assertEqual([], rep["placed"])
        self.assertEqual([], q.tasks)
        reasons = " ".join(w for _k, w in rep["held"])
        self.assertIn("ОТОЗВАНО ШТАБОМ", reasons)
        self.assertIn("kk1", reasons)

    def test_POSITIVE_the_very_same_document_without_the_mark_IS_taken(self):
        """Контроль к предыдущему: гасит именно метка, а не что-то ещё в имени."""
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(["kk1"], [r["key"] for r in rep["placed"]], rep["why"])

    def test_the_revoked_document_costs_no_body_read(self):
        """Снятого не читаем вовсе: иначе отзыв стоил бы похода в мост каждый виток."""
        files, bodies = _files(_box((sb.doc_name("kk1") + ".СНЯТО", GOOD_BODY, "raw")))
        reader = _doc_reader(bodies)
        _tick(lister=_lister(files), doc_reader=reader, queue=FakeQueue(), place=True)
        self.assertEqual([], reader.seen)

    def test_the_mark_is_recognised_with_four_separators_and_any_case(self):
        """Человек, переименовывающий файл в Drive, поставит и дефис, и пробел.

        Ворота, требующие дословности, ответили бы на такое переименование
        МОЛЧАНИЕМ — то есть ящик взял бы задание, которое Штаб считает снятым.
        Это худший из возможных исходов, и цена терпимости здесь — ноль.
        """
        for tail in (".СНЯТО", "-СНЯТО", "_СНЯТО", " СНЯТО", ".снято", ".Снято"):
            gone, clean = sb.revoked_name(sb.doc_name("kk1") + tail)
            self.assertTrue(gone, tail)
            self.assertEqual(sb.doc_name("kk1"), clean, tail)

    def test_the_mark_must_be_a_WHOLE_word_at_the_very_end(self):
        """Слово целиком и в хвосте — иначе снятыми оказались бы посторонние имена."""
        for name in (sb.doc_name("snyatokrytiya"), sb.doc_name("kk1") + ".СНЯТО.ещё",
                     sb.doc_name("СНЯТОЕ")):
            gone, _clean = sb.revoked_name(name)
            self.assertFalse(gone, name)

    def test_a_name_that_is_prefix_plus_mark_alone_ends_up_KEYLESS_not_taken(self):
        """Вырожденное имя `shtab_task_СНЯТО`: метку видно, а ключа под ней нет.

        Проверяем ИСХОД, а не внутренний признак: как ни назови такое имя, задачей
        оно стать не должно, и причина обязана прозвучать словами.
        """
        docs, bad = sb.parse_folder([{"name": sb.TASK_PREFIX + "СНЯТО", "id": "f1"}])
        self.assertEqual([], docs)
        self.assertIn("ключа в нём нет", bad[0]["why"])

    def test_revocation_is_reported_but_does_NOT_recall_a_placed_row(self):
        """ГРАНИЦА ОТЗЫВА, названная вслух: метка останавливает ВЗЯТИЕ, а не работу.

        Уже поставленный ряд ящик не снимает ни одной веткой — это решение
        владельца. Второй раз задание всё равно не возьмётся, но причиной будет
        ДЕДУП, а не отзыв: «работа уже идёт» — новость важнее, чем «снято», и
        сказать вторую значило бы намекнуть, что снятие что-то остановило.

        РЯДОМ ЛЕЖИТ ЖИВОЕ ЗАДАНИЕ, и это не украшение сцены: маркеры суток ящик
        читает только при живом кандидате, а из одних снятых документов кандидата
        не выходит (см. соседний тест про дешёвую папку). Без соседа мы проверяли
        бы не порядок причин, а экономию.
        """
        q = FakeQueue(closed=[_row("kk1")])
        rep = _tick(_box((sb.doc_name("kk1") + ".СНЯТО", GOOD_BODY, "raw"),
                         ("zzz-sosed", GOOD_BODY)), queue=q, place=True)
        by_key = dict(rep["held"])
        self.assertIn("уже брали", by_key.get("kk1", ""))
        self.assertNotIn("ОТОЗВАНО", by_key.get("kk1", ""))

    def test_a_folder_of_ONLY_revoked_documents_costs_no_expensive_read(self):
        """Экономия, из-за которой у соседнего теста есть живой сосед.

        Снятое Штабом взять нельзя ни при каких маркерах, поэтому папка из одних
        снятых документов не стои́т 27 секунд чтения `done`. Причина при этом
        названа словами — молчания здесь нет.
        """
        q = FakeQueue()
        rep = _tick(_box((sb.doc_name("kk1") + ".СНЯТО", GOOD_BODY, "raw")),
                    queue=q, place=True)
        self.assertNotIn(run.CLOSED_STATUSES, q.asked)
        self.assertIn("ОТОЗВАНО ШТАБОМ", " ".join(w for _k, w in rep["held"]))

    def test_dropping_the_prefix_also_removes_the_task_but_SILENTLY(self):
        """Тихая дорога отзыва названа честно, а не спрятана.

        Убрать префикс тоже снимает задание — но ящик не скажет об этом НИЧЕГО,
        потому что документа для него больше нет. Поэтому канон отзыва — метка.
        """
        q = FakeQueue()
        rep = _tick(_box(("OFF_" + sb.doc_name("kk1"), GOOD_BODY, "raw")), queue=q, place=True)
        self.assertEqual([], rep["placed"])
        self.assertIn("пуст", rep["why"])
        self.assertNotIn("ОТОЗВАНО", " ".join(w for _k, w in rep["held"]))

    # ── потолок чтений ─────────────────────────────────────────────────
    def test_the_read_ceiling_holds_and_the_dropped_ones_are_NAMED(self):
        """ТИХОГО ОБРЕЗАНИЯ НЕТ: непрочитанные получают свою строку с числом."""
        pairs = [("k%02d" % i, GOOD_BODY) for i in range(sb.READ_MAX + 3)]
        files, bodies = _files(_box(*pairs))
        reader = _doc_reader(bodies)
        rep = _tick(lister=_lister(files), doc_reader=reader, queue=FakeQueue(), place=True)
        self.assertEqual(sb.READ_MAX, len(reader.seen), "потолок чтений не сработал")
        held = " ".join(w for _k, w in rep["held"])
        self.assertIn("не больше %d" % sb.READ_MAX, held)
        self.assertIn("дойдём следующим витком", held)
        # …и взято при этом ровно одно: потолок чтений замков не подменяет.
        self.assertEqual(1, len(rep["placed"]))

    def test_a_document_whose_body_will_not_read_is_HELD_not_refused(self):
        """Отказ чтения тела — это «не знаю», а не «задание плохое»."""
        files, bodies = _files(_box(("kk1", GOOD_BODY)))
        rep = _tick(lister=_lister(files), doc_reader=_doc_reader(bodies, dead=["fid-00"]),
                    queue=FakeQueue(), place=True)
        self.assertEqual([], rep["placed"])
        held = " ".join(w for _k, w in rep["held"])
        self.assertIn("тело документа не прочитано", held)
        self.assertNotIn("НЕ ПРИНЯТ", held)

    # ── закрытая дверь ─────────────────────────────────────────────────
    def test_the_old_door_in_the_header_is_reported_not_silently_ignored(self):
        """Молча закрытая дверь выглядит поломкой: у Штаба на экране лежит задание,
        которое «почему-то не берут»."""
        head = HEAD_TEXT + "\n" + _node(("oldkey", GOOD_BODY))
        rep = _tick(_box(), node_text=head, queue=FakeQueue(), place=True)
        self.assertEqual([], rep["placed"])
        self.assertIn("старой формы", rep["why"])
        self.assertIn(sb.TASK_PREFIX, rep["why"])
        self.assertIn("пуст", rep["why"], "новость про пустую папку пропасть не должна")

    def test_a_block_in_the_header_is_NEVER_taken_as_a_task(self):
        """Двух дверей нет: блок в шапке источником задач не является ни одной веткой."""
        head = HEAD_TEXT + "\n" + _node(("oldkey", GOOD_BODY))
        q = FakeQueue()
        rep = _tick(_box(), node_text=head, queue=q, place=True)
        self.assertEqual([], q.tasks)
        self.assertEqual(0, rep["docs"])


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

    def test_the_address_gate_asks_the_closing_judge_itself(self):
        """ФОРМА АДРЕСА ОДНА, и живёт она у СУДЬИ: ворота зовут его читатель.

        До 02.09 форм было две — ворота судили каноном ``result_ref``, судья читал
        свою прозу, и пересечение их было ПУСТО: принятая ящиком задача не могла
        закрыться доказанной ни при каком исполнении. Своя копия чужого правила
        разошлась бы снова и разошлась бы МОЛЧА, поэтому пин стои́т на чужую
        функцию, а не на форму строки.
        """
        self.assertIn("done_judge_pc.read_address", _src("shtab_box.py"))
        for form in (ADDR_LINE,
                     "АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 02.09.2026 со словами доля",
                     "АДРЕС РЕЗУЛЬТАТА: файл docs/artifacts/x.md со словами доля"):
            body = NO_ADDR_BODY + "\n" + form
            self.assertIsNotNone(dj.read_address(body), "предпосылка теста протухла: %s" % form)
            ok, reason, _why = sb.check({"key": "kk1", "body": body})
            self.assertTrue(ok, "судья читает, а ворота не берут (%s): %s" % (reason, form))

    def test_the_shipped_sample_of_the_address_passes_its_own_gate(self):
        """Образец адреса, как и образец запретов, обязан проходить НАШИ ЖЕ ворота.

        Иначе документация звала бы Штаб писать адрес, на котором ящик отказывает,
        — а увидели бы мы это только на живом задании.
        """
        body = "ЦЕЛЬ: проверить образец.\n\n" + sb.PROHIBITIONS + "\n\n" + sb.ADDRESS
        ok, reason, why = sb.check({"key": "kk1", "body": body})
        self.assertTrue(ok, "образец адреса не проходит собственные ворота: %s %s" % (reason, why))
        self.assertIsNotNone(dj.read_address(sb.ADDRESS), "образец не читается судьёй")

    def test_NEGATIVE_the_foreign_form_of_the_address_is_not_taken(self):
        """ОТРИЦАТЕЛЬНАЯ ПОЛОВИНА СВЕДЕНИЯ: чужая форма воротами НЕ берётся.

        Чужая здесь — прежняя своя: КАНОН ШТАБА ``result_ref``. Он собран не
        литералом, а самим каноном (``result_ref.make``), чтобы тест доказывал
        отказ НАСТОЯЩЕЙ форме, а не выдуманной строке.
        """
        body = NO_ADDR_BODY + "\n" + FOREIGN_ADDR
        self.assertTrue(result_ref.is_named(result_ref.read(body)),
                        "образец обязан быть настоящим каноном result_ref")
        self.assertIsNone(dj.read_address(body),
                          "предпосылка протухла: судья научился читать канон — сведи формы заново")
        ok, reason, why = sb.check({"key": "kk1", "body": body})
        self.assertFalse(ok, "ворота берут форму, на которой судья слеп")
        self.assertEqual(reason, "no_address")
        self.assertIn("судья", why)
        self.assertIn(sb.ADDRESS_FORM, why, "отказ обязан НАЗЫВАТЬ единственную форму")

    def test_an_address_without_the_proving_words_is_not_taken_either(self):
        """Адрес без слов судья отвергает дословно — ворота обязаны отвергнуть его тут.

        Иначе блок брался бы ради закрытия, которое заведомо не состоится: слова и
        есть то, чем судья доказывает продукт.
        """
        body = NO_ADDR_BODY + "\nАДРЕС РЕЗУЛЬТАТА: файл docs/artifacts/x.md"
        addr = dj.read_address(body)
        self.assertIsNotNone(addr)
        self.assertEqual(addr["words"], "")
        ok, reason, why = sb.check({"key": "kk1", "body": body})
        self.assertFalse(ok)
        self.assertEqual(reason, "no_address")
        self.assertIn("без слов", why)

    def test_POSITIVE_a_taken_block_is_actually_judged_by_the_closing_judge(self):
        """ПОЛОЖИТЕЛЬНАЯ ПОЛОВИНА: принятый блок судья не только ЧИТАЕТ, но и СУДИТ.

        Проверять одну половину мало: расхождение форм убивало задачу не на
        воротах (их она проходила), а в СУДЕ — «адрес результата не назван». Здесь
        прогоняется весь путь ступени C на поддельном корне: опорный снимок ДО,
        артефакт по адресу, вердикт ПОСЛЕ. Имя артефакта — КИРИЛЛИЧЕСКОЕ, как у
        71% наших сентябрьских файлов.
        """
        root = tempfile.mkdtemp(prefix="shtabjudge_")
        os.makedirs(os.path.join(root, "docs", "artifacts"))
        text = sb.task_text({"key": "kk1", "body": GOOD_BODY}, TODAY)
        self.assertIsNotNone(text)
        base = dj.baseline(text, root=root)
        self.assertTrue(base["ok"], "судья не снял опорный снимок по адресу из ящика")
        with io.open(os.path.join(root, "docs", "artifacts", "2026-09-02-доля-неизвестного.md"),
                     "w", encoding="utf-8") as fh:
            fh.write("# доля неизвестного за август\n\n13 из 18 закрытий — без адреса.\n")
        verdict = dj.judge("119", text, "done", base, root=root)
        self.assertEqual(verdict["verdict"], dj.DONE, verdict["reason"])

    def test_a_cyrillic_artifact_name_survives_the_chosen_form(self):
        """Кириллица в имени артефакта — не редкость, а НОРМА полосы (37 из 52 за сентябрь).

        Форма с ПРЯМЫМ ПУТЁМ на ней слепа: набор символов пути у судьи латинский.
        Значит ворота такой блок не берут — расхождение обязано стоить отказа на
        входе, а не недоказуемого закрытия на выходе. Форма «папка + дата + слова»
        имени файла не разбирает вовсе и потому переживает кириллицу.
        """
        body = (NO_ADDR_BODY
                + "\nАДРЕС РЕЗУЛЬТАТА: файл docs/artifacts/2026-09-02-доля.md со словами доля")
        self.assertIsNone(dj.read_address(body))
        ok, reason, _why = sb.check({"key": "kk1", "body": body})
        self.assertFalse(ok)
        self.assertEqual(reason, "no_address")
        ok, reason, _why = sb.check({"key": "kk1", "body": GOOD_BODY})
        self.assertTrue(ok, reason)

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
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])
        self.assertEqual(rep["placed"][0]["key"], "kk1")
        self.assertEqual(len(q.tasks), 1)
        self.assertIn(GOOD_BODY, q.tasks[0][1])

    # ── 1. источник недоступен ─────────────────────────────────────────
    def test_1_unreachable_folder_says_UNKNOWN_and_takes_nothing(self):
        """ПЕРЕЧИСЛЕНИЕ НЕ ОТВЕТИЛО → НЕИЗВЕСТНО и ноль постановок.

        Проверено ЗАНОВО на новом источнике: до 03.09 роль «источник молчит» играл
        отказ чтения узла, и зелёный старого теста не сказал бы об этой ветке
        ничего — она другая функция и другой отказ.
        """
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), lister=_lister([], ok=False, why="мост не ответил"),
                    queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertFalse(rep["folder_ok"])
        self.assertIn("НЕИЗВЕСТНО", rep["why"])
        # И слово «пусто» в причине НЕ звучит: пустой ящик и недоступный — разные новости.
        self.assertNotIn("пуст", rep["why"])
        # Очереди мы даже не спрашивали: не зная источника, ящик не идёт дальше.
        self.assertEqual(q.asked, [])

    def test_1b_an_empty_folder_reads_differently_from_an_unreachable_one(self):
        """Контраст к предыдущему: пустой ящик говорит «пуст», а не «неизвестно»."""
        rep = _tick(_box(), place=True)
        self.assertIn("пуст", rep["why"])
        self.assertNotIn("НЕИЗВЕСТНО", rep["why"])

    def test_1c_an_empty_text_from_a_document_counts_as_a_read_failure(self):
        """Мост, отдавший пустую строку, — это отказ чтения, а не пустой док."""
        _text, ok, why = run.read_doc_text("fid-1", reader=lambda fid: "")
        self.assertFalse(ok)
        self.assertIn("отказом чтения", why)

    def test_1d_a_TRUNCATED_listing_is_a_refusal_and_not_a_short_list(self):
        """УСЕЧЁННЫЙ СПИСОК — НЕИЗВЕСТНО, а не «взяли что видно».

        Замер 03.09 на живой папке (49 файлов, limit=3) вернул НЕ три первых по
        алфавиту: мост сортирует уже обрезанный произвольный кусок. Наш порядок
        разбора — «первый по имени», и на таком списке он дал бы уверенный и
        неверный ответ.
        """
        q = FakeQueue()
        files, _b = _files(_box(("kk1", GOOD_BODY)))
        rep = _tick(_box(("kk1", GOOD_BODY)), lister=_lister(files, truncated=True),
                    queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertFalse(rep["folder_ok"])
        self.assertIn("НЕИЗВЕСТНО", rep["why"])
        self.assertIn("УСЕЧЕНО", rep["why"])
        self.assertNotIn("пуст", rep["why"])

    def test_1e_a_dead_HEADER_does_not_stop_the_box(self):
        """Шапка перестала быть источником — значит её отказ ящик не останавливает.

        Ослаблением это не является: единственное, что теряется вместе с шапкой, —
        МЕТКИ СНЯТИЯ сигналов, то есть снятый владельцем сигнал остаётся стоять.
        Строже, а не слабее.
        """
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), reader=_dead_reader("шапки нет"),
                    queue=q, place=True)
        self.assertFalse(rep["node_ok"])
        self.assertEqual(len(rep["placed"]), 1, rep["why"])
        self.assertEqual(rep["build"]["released"], [])

    # ── 2. блок без адреса результата ──────────────────────────────────
    def test_2_block_without_a_result_address_is_refused_with_a_named_reason(self):
        q = FakeQueue()
        rep = _tick(_box(("kk1", NO_ADDR_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        reasons = " ".join(w for _k, w in rep["held"])
        self.assertIn("НЕ ПРИНЯТ (no_address)", reasons)
        self.assertIn("адрес результата", reasons)
        # Причина обязана доехать до строки лога, а не остаться внутри отчёта.
        self.assertIn("no_address", rep["why"])
        # …и НЕ ПРИКИДЫВАТЬСЯ ПОЛОМКОЙ МОСТА: отказ ворот остаётся отказом ворот, а
        # не превращается в «сверить нечем». Назови мы его так, настоящий отказ
        # моста утонул бы в этом крике.
        self.assertNotIn("не прочитаны", rep["why"])
        self.assertTrue(rep["build"]["marks_ok"])

    def test_2d_the_gate_refusal_now_COSTS_the_expensive_read_and_that_is_deliberate(self):
        """ЧЕСТНАЯ ЦЕНА ПЕРЕЕЗДА, записанная тестом, а не спрятанная.

        До 03.09 отказ ворот дорогого чтения `done` не стоил: тело блока приезжало
        вместе с узлом, ворота считались ДО очереди, и «принятых блоков нет» служило
        дешёвым признаком «дальше не идём». Теперь тело — отдельный поход в мост, и
        порядок обратный: сначала маркеры (кого не читать), потом тела.

        ПОЧЕМУ ИМЕННО ТАК, а не наоборот. Взятые документы из папки НЕ ИСЧЕЗАЮТ —
        они копятся в ней навсегда. Читай мы тела раньше маркеров, каждый виток
        перечитывал бы тела всех когда-либо взятых заданий: цена росла бы БЕЗ
        ПОТОЛКА. Цена `done` фиксированная (~27 с) и от числа заданий не зависит.
        Меняем растущую на постоянную — сознательно.
        """
        q = FakeQueue()
        rep = _tick(_box(("kk1", NO_ADDR_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertIn(run.CLOSED_STATUSES, q.asked, "маркеры суток читаются ДО тел")

    def test_2e_an_ALREADY_TAKEN_document_costs_no_body_read_at_all(self):
        """Оборотная сторона того же порядка — та, ради которой он и выбран.

        Документ, ключ которого уже стои́т маркером в очереди, тела не стои́т ни
        разу: его не читают вовсе. Иначе папка, копящая взятые задания, дорожала бы
        с каждым выполненным заданием.
        """
        q = FakeQueue(closed=[_row("kk1")])
        files, bodies = _files(_box(("kk1", GOOD_BODY)))
        reader = _doc_reader(bodies)
        rep = _tick(_box(("kk1", GOOD_BODY)), lister=_lister(files), doc_reader=reader,
                    queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(reader.seen, [], "тело уже взятого задания читали зря")
        self.assertEqual(rep["build"]["read"], 0)

    def test_2b_block_without_the_prohibitions_is_refused_too(self):
        q = FakeQueue()
        rep = _tick(_box(("kk1", NO_PROHIB_BODY)), queue=q, place=True)
        self.assertEqual(q.tasks, [])
        reasons = " ".join(w for _k, w in rep["held"])
        self.assertIn("no_prohibitions", reasons)

    def test_2c_a_refused_block_does_not_block_a_good_neighbour(self):
        """Отказ одному блоку не глушит ящик целиком — иначе один кривой блок Штаба
        останавливал бы канал навсегда, и выглядело бы это как «ящик сломался»."""
        q = FakeQueue()
        rep = _tick(_box(("bad", NO_ADDR_BODY), ("good", GOOD_BODY)), queue=q, place=True)
        self.assertEqual([r["key"] for r in rep["placed"]], ["good"])

    # ── 3. тот же ключ второй раз ──────────────────────────────────────
    def test_3_the_same_key_is_never_taken_twice(self):
        """Дедуп по ЖИВОЙ ОЧЕРЕДИ, и закрытый ряд считается наравне с открытым."""
        q = FakeQueue(closed=[_row("kk1")])
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertIn("уже брали", " ".join(w for _k, w in rep["held"]))

    def test_3b_a_key_taken_on_a_previous_DAY_is_still_never_taken_again(self):
        """«Не берётся НИКОГДА» — это не «не берётся сегодня»: дата в маркере другая,
        а ключ тот же, и повтор всё равно запрещён."""
        q = FakeQueue(closed=[_row("kk1", day="2026-08-01")])
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertIn("уже брали", " ".join(w for _k, w in rep["held"]))

    def test_3c_POSITIVE_a_different_key_next_to_a_taken_one_still_goes(self):
        q = FakeQueue(closed=[_row("kk1")])
        rep = _tick(_box(("kk1", GOOD_BODY), ("kk2", GOOD_BODY)), queue=q, place=True)
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
        files, bodies = _files(_box(("kk1", GOOD_BODY)))
        lister, doc_reader, reader = _lister(files), _doc_reader(bodies), _reader(HEAD_TEXT)
        rep = _tick(lister=lister, doc_reader=doc_reader, reader=reader,
                    queue=q, place=True, root=root)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertTrue(rep["off"])
        self.assertIn(run.STOP_FILE, rep["why"])
        # И в мозг мы не ходили НИ ОДНОЙ из трёх дверей: выключенный ящик не читает
        # ни папки, ни тел, ни шапки. Проверено ЗАНОВО: дверей стало три, и старый
        # тест сторожил только одну из них.
        self.assertEqual(lister.seen, [])
        self.assertEqual(doc_reader.seen, [])
        self.assertEqual(reader.seen, [])
        # Ни очереди: выключено значит выключено, а не «прочитаем и не поставим».
        self.assertEqual(q.asked, [])

    def test_4b_POSITIVE_the_same_root_without_the_file_places_normally(self):
        """Тот же корень БЕЗ файла — задача встаёт. Значит гасит именно файл."""
        root = tempfile.mkdtemp(prefix="shtabbox_on_")
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True, root=root)
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
        rep = _tick(_box(("kk1", GOOD_BODY), ("kk2", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertIn("#321", rep["why"])
        self.assertIn("задача владельца", rep["why"])

    def test_5b_the_owner_lock_saves_the_expensive_read_of_closed_rows(self):
        """Занятая полоса не платит 27 секунд витка за ответ, который не понадобится."""
        q = FakeQueue(busy=True, busy_ids=[321])
        _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertNotIn(run.CLOSED_STATUSES, q.asked)

    def test_5c_POSITIVE_a_free_lane_places_the_very_same_block(self):
        q = FakeQueue(busy=False)
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])


# ═══════════════════════════ потолки ═══════════════════════════════════


class TestCeiling(unittest.TestCase):

    def test_not_more_than_one_per_tick(self):
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY), ("kk2", GOOD_BODY), ("kk3", GOOD_BODY)),
                    queue=q, place=True)
        self.assertEqual(len(rep["placed"]), 1)
        self.assertIn("не больше 1 за виток", " ".join(w for _k, w in rep["held"]))

    def test_the_daily_ceiling_counts_CLOSED_rows_too(self):
        """Урок ступени E дословно: счёт по одним открытым рядам мерит одновременность.

        Корпус набирается ОТ КОНСТАНТЫ, а не тремя строками: числом здесь был
        прежний потолок 3, и после подъёма до 8 тест зеленел бы на пустом месте.
        """
        closed = [_row("q%02d" % i) for i in range(sb.DAILY_BUDGET)]
        q = FakeQueue(closed=closed)
        rep = _tick(_box(("kk9", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertIn("суточный потолок исчерпан", " ".join(w for _k, w in rep["held"]))

    def test_yesterdays_rows_do_not_eat_todays_budget(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ потолка: правило, глушащее всё, прошло бы тест выше."""
        closed = [_row("q%02d" % i, day="2026-09-01") for i in range(sb.DAILY_BUDGET)]
        rep = _tick(_box(("kk9", GOOD_BODY)), queue=FakeQueue(closed=closed), place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])

    def test_unread_closed_rows_mean_EXHAUSTED_not_EMPTY(self):
        """Третий исход: корпус не прочитан → день исчерпан, и сказано это ДРУГИМИ словами."""
        q = FakeQueue(closed_ok=False)
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(rep["placed"], [])
        self.assertEqual(q.tasks, [])
        self.assertIn("НЕИЗВЕСТНО", rep["why"])
        self.assertIn("исчерпанным", rep["why"])

    def test_a_box_of_FIFTY_does_not_send_the_lane_into_itself(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПОДЪЁМА 8 → 40 (04.09.2026, вечер).

        Подъём потолка обязан отвечать на вопрос «а не уйдёт ли полоса в себя»
        ЧИСЛОМ, а не обещанием. Здесь смоделирован ровно тот сценарий, ради
        которого потолок и заведён: Штаб залил в папку ПОЛСОТНИ документов, все
        ворота проходят, ни один сигнал по исходу не сработал (закрытые ряды —
        голые маркеры без вердиктов), владелец полосу не занимает. То есть у
        полосы отняты ВСЕ остановки, кроме двух счётных, — и ответ обязан быть
        конечным.

        ЧТО ИМЕННО ДЕРЖИТ, ПОИМЁННО И НА КАКОМ БЛОКЕ:

        * **внутри витка — «не больше одной за виток»** (:data:`sb.TICK_LIMIT`),
          и держит он со ВТОРОГО блока, а не с сорок первого. Это главный ответ
          на «уйдёт ли полоса в себя»: сколько бы ни лежало в папке, за оборот
          уезжает РОВНО ОДНА задача;
        * **между витками — суточный потолок**, и он захлопывается на блоке
          №41: сорок взято, десять остались лежать со словами «суточный потолок
          исчерпан». Полсотни в сорок не помещаются — свойство «разгон упирается
          в потолок» подъёмом не потеряно.

        ЦЕНА ВРЕМЕНЕМ СЧИТАЕТСЯ ЗДЕСЬ ЖЕ И ОНА ГЛАВНАЯ: сорок взятых — это сорок
        ОТДЕЛЬНЫХ витков, а виток смотрит в папку не чаще ``SHTAB_BOX_MIN_SEC``
        (600 с). Пачка из полусотни физически не может уехать быстрее, чем за
        40 × 600 с ≈ 6.7 часа ОДНИХ ПАУЗ, — и это до того, как полоса начнёт
        исполнять хоть одну задачу. Человек видит разгон задолго до потолка.
        """
        BOX = 50
        docs = _box(*[("kk%02d" % i, GOOD_BODY) for i in range(BOX)])

        # ── 1. ОДИН ВИТОК: держит НЕ потолок, а «одна за виток», и со второго блока
        rep = _tick(docs, queue=FakeQueue(), place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])
        self.assertEqual(rep["placed"][0]["key"], "kk00")
        self.assertIn("не больше 1 за виток", rep["held"][0][1])

        # ── 2. ВИТОК ЗА ВИТКОМ: взятое возвращается в очередь МАРКЕРОМ, как в бою
        closed, taken, stopped_by = [], [], ""
        for _ in range(BOX + 10):                 # запас заведомо больше потолка
            rep = _tick(docs, queue=FakeQueue(closed=list(closed)), place=True)
            if not rep["placed"]:
                stopped_by = " ".join(w for _k, w in rep["held"])
                break
            for row in rep["placed"]:
                taken.append(row["key"])
                closed.append(_row(row["key"]))   # день по умолчанию — сегодняшний
        # СХОДИМОСТЬ: цикл кончился отказом брать, а не исчерпанием запаса витков.
        self.assertTrue(stopped_by, "полоса не остановилась за %d витков" % (BOX + 10))
        self.assertEqual(len(taken), sb.DAILY_BUDGET)
        self.assertEqual(len(taken), 40)
        self.assertEqual(len(set(taken)), 40)     # дедуп не тронут: сорок РАЗНЫХ
        self.assertEqual(taken[-1], "kk39")       # взят сороковой по счёту блок…
        self.assertIn("суточный потолок исчерпан", stopped_by)   # …а сорок первый — нет
        self.assertIn("40 задачи от Штаба", stopped_by)
        self.assertEqual(BOX - len(taken), 10)    # десять остались лежать

        # ── 3. ЗАВТРА ПОЛОСА СНОВА БЕРЁТ: потолок — ловушка на разгон, а не стоп-кран
        rep = _tick(docs, queue=FakeQueue(closed=[_row(k, day="2026-09-01") for k in taken]),
                    place=True)
        self.assertEqual(len(rep["placed"]), 1, rep["why"])

    def test_the_ceiling_number_is_named_and_reused_not_reinvented(self):
        """Потолок и дедуп берутся у ступени E, а не пишутся вторым экземпляром."""
        self.assertEqual(sb.DAILY_BUDGET, 40)
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

    def test_the_ceiling_is_honest_about_what_it_is_and_what_really_stops(self):
        """ОБОСНОВАНИЕ — предмет проверки наравне с числом (правка 04.09.2026).

        Прежний комментарий выдавал ограничитель за справедливый делёж («30%
        медианной мощности»), и число, взятое как доля, поднимается только вместе
        с ложью о доле. Держим тем же способом, каким полоса держит остальные
        числа: обоснование обязано называть, ЧТО останавливает на самом деле.

        Правка 04.09.2026 (вечер) добавила сюда второе требование: обоснование
        обязано называть и ФИЗИЧЕСКИЙ ПРЕДЕЛ полосы. Число, поднятое без него,
        неотличимо от взятого с потолка — а «40 выше наблюдаемого максимума 30»
        и есть весь довод, почему ловушка не связывает здоровую работу.
        """
        body = _src("shtab_box.py")
        head = body.split("DAILY_BUDGET = ")[0]
        self.assertNotIn("30% медианной суточной мощности", head)
        self.assertIn("ЛОВУШКА НА РАЗГОН, А НЕ ДЕЛЁЖ", head)
        tail = head.split("СОРОК ЗАДАЧ В СУТКИ")[-1]
        self.assertIn("shtab_box_signals", tail)
        self.assertIn("МАКС 30", tail)

    def test_the_daemon_default_moved_with_the_constant(self):
        """Подъём в чистом модуле без ручки демона был бы КОСМЕТИКОЙ.

        Бой берёт число не отсюда, а из `os.getenv("SHTAB_BOX_BUDGET", …)`: оставь
        мы там прежний дефолт — константа говорила бы 40, а ящик брал бы 8, и
        расхождение было бы молчаливым (ровно им живёт весь класс «прибор с двумя
        экземплярами врёт обоими»).

        ЧИСЛО ЗДЕСЬ НЕ НАБИРАЕТСЯ ЛИТЕРАЛОМ, а СЧИТЫВАЕТСЯ У КОНСТАНТЫ (правка
        04.09.2026): прежняя форма `'"SHTAB_BOX_BUDGET", "8"'` держала два
        экземпляра вместе ровно до следующей правки — поправь кто-нибудь оба
        литерала порознь на разные числа, и тест остался бы зелёным. Теперь
        подъём ОДНОГО экземпляра из двух красит набор немедленно.
        """
        # СРАВНИВАЕМ СО СТРОКОЙ, А НЕ СО ВСЕМ ИСХОДНИКОМ: `assertIn` по файлу
        # вываливает в отчёт весь `pc_orchestrator.py` (под мегабайт), и красный
        # замок становится нечитаемым ровно тогда, когда его надо прочесть.
        line = [ln for ln in _src("pc_orchestrator.py").splitlines()
                if ln.startswith("SHTAB_BOX_BUDGET = ")]
        self.assertEqual(len(line), 1, line)
        self.assertIn('"SHTAB_BOX_BUDGET", "%d"' % sb.DAILY_BUDGET, line[0])
        # …и обратная сторона того же замка: другого числа в этой строке нет —
        # ни в имени ручки, ни во втором плече `or`.
        self.assertEqual(re.findall(r"\d+", line[0]), [str(sb.DAILY_BUDGET)] * 2, line[0])


class TestBillable(unittest.TestCase):
    """ОГРАНИЧИТЕЛЬ ПО ИСХОДУ: слот суток тратит не всякий взятый ряд (06.09.2026).

    Различитель внешнего отказа живёт НЕ ЗДЕСЬ (`shtab_box_signals.external_refusal`)
    и здесь не проверяется. Проверяется арифметика маркеров: она обязана только
    СУЖАТЬ счёт и не смеет иметь собственного мнения о чужом API.
    """

    def test_billable_without_externals_changes_nothing(self):
        marks = [("2026-09-06", "a"), ("2026-09-06", "b")]
        self.assertEqual(marks, sb.billable(marks, ()))
        self.assertEqual(marks, sb.billable(marks, None))

    def test_billable_drops_exactly_the_named_keys(self):
        marks = [("2026-09-06", "a"), ("2026-09-05", "b"), ("2026-09-06", "c")]
        self.assertEqual([("2026-09-05", "b"), ("2026-09-06", "c")],
                         sb.billable(marks, ["a"]))

    def test_billable_never_invents_a_mark(self):
        """Ошибка различителя может дать лишний слот, но не порвать дедуп."""
        marks = [("2026-09-06", "a")]
        self.assertEqual([], sb.billable(marks, ["a"]))
        self.assertEqual(marks, sb.billable(marks, ["другой ключ"]))

    def test_the_daily_count_and_the_ceiling_read_the_same_narrowed_list(self):
        marks = [("2026-09-06", "a"), ("2026-09-06", "b"), ("2026-09-06", "c")]
        billed = sb.billable(marks, ["b"])
        self.assertEqual(2, sb.marks_today(billed, "2026-09-06"))
        self.assertEqual(sb.DAILY_BUDGET - 2, sb.budget_left(billed, "2026-09-06"))

    def test_digest_line_says_BOTH_numbers_when_there_were_externals(self):
        line = sb.digest_line(8, "2026-09-06", external=3, billed=5)
        self.assertIn("взято 8", line)
        self.assertIn("внешних отказов 3", line)
        self.assertIn("съедено 5", line)

    def test_digest_line_for_a_foreign_caller_is_untouched(self):
        """Сводка контура видит слепок без статусов и итогов — её число прежнее.

        Это НЕ два разошедшихся счёта, а честная граница прибора: отличить
        внешний отказ по одному полю ``goal`` нельзя ничем, и молча приписывать
        сводке льготу было бы враньём в её сторону.
        """
        self.assertNotIn("внешних отказов", sb.digest_line(8, "2026-09-06"))


class TestLaneDay(unittest.TestCase):
    """СУТКИ ЯЩИКА — МЕСТНЫЕ (правка 04.09.2026, :func:`shtab_box.lane_day`)."""

    def test_the_lane_stands_at_plus_seven_and_the_offset_is_not_asked_of_the_OS(self):
        self.assertEqual(sb.LANE_TZ.utcoffset(None), datetime.timedelta(hours=7))

    def test_the_night_belongs_to_the_new_day_not_to_the_old_one(self):
        """ЖИВОЙ СЛУЧАЙ ЗАМЕРА 04.09 ДОСЛОВНО: 02:51 местного = 19:51 UTC вчерашних.

        Это и есть цена прежнего правила: по UTC ящик считал бы ночь вчерашним
        днём и стоял бы на вчерашнем потолке до 07:00 местного.
        """
        self.assertEqual(sb.lane_day("2026-09-03T19:51:13Z"), "2026-09-04")
        self.assertEqual(review_intake.today_utc("2026-09-03T19:51:13Z"), "2026-09-03")

    def test_the_day_turns_at_local_midnight_and_not_at_seven(self):
        """ГРАНИЦА НАЗВАНА С ОБЕИХ СТОРОН: минута до и минута после 17:00 UTC."""
        self.assertEqual(sb.lane_day("2026-09-03T16:59:59Z"), "2026-09-03")
        self.assertEqual(sb.lane_day("2026-09-03T17:00:00Z"), "2026-09-04")
        # …а прежний рубеж 00:00 UTC днём полосы больше не является
        self.assertEqual(sb.lane_day("2026-09-03T23:59:59Z"), "2026-09-04")

    def test_an_offset_in_the_stamp_is_obeyed_not_ignored(self):
        """Момент один, написаний много: день обязан зависеть от МОМЕНТА."""
        self.assertEqual(sb.lane_day("2026-09-04T00:51:13+07:00"), "2026-09-04")
        self.assertEqual(sb.lane_day("2026-09-03T17:51:13+00:00"), "2026-09-04")

    def test_a_naked_stamp_is_read_as_UTC_and_not_as_this_machine(self):
        """Чистая функция не смеет спрашивать часовой пояс ОС — иначе она нечистая."""
        self.assertEqual(sb.lane_day("2026-09-03T17:00:00"), "2026-09-04")

    def test_the_module_stays_pure_though_it_now_knows_about_time(self):
        """Знание о СМЕЩЕНИИ — не часы. Часов не завелось: «сейчас» приезжает полем."""
        self.assertNotIn("datetime.datetime.now", _src("shtab_box.py"))
        with self.assertRaises(TypeError):
            sb.lane_day()                       # без момента ответа нет вовсе

    def test_the_hands_ask_the_lane_day_and_no_longer_the_utc_one(self):
        """Правка бесполезна, если руки продолжают звать прежнюю дверь."""
        body = _src("shtab_box_run.py")
        self.assertIn("shtab_box.lane_day(stamp)", body)
        self.assertNotIn("review_intake.today_utc(stamp)", body)

    def test_the_shared_utc_day_is_left_untouched_for_its_four_owners(self):
        """СОСЕДИ НЕ ТРОНУТЫ: у ступеней B и E день остаётся UTC, и это проверяется."""
        self.assertEqual(review_intake.today_utc("2026-09-03T23:59:59Z"), "2026-09-03")
        for mod in ("recon_auto_run.py", "review_audit_run.py", "review_intake_run.py"):
            self.assertIn("review_intake.today_utc(stamp)", _src(mod))

    def test_the_digest_counts_the_box_by_the_SAME_day_as_the_ceiling(self):
        """Сводка и замок обязаны считать ОДНИ сутки, иначе владелец видит два числа.

        Сводка показывает «взято N при потолке M», а держит потолок ящик. Останься
        разрез сводки на UTC — ночью она называла бы вчерашний день при живом
        сегодняшнем счёте. Окно внешних ответов при этом на UTC и остаётся: у него
        разрез свой, и тест это ЗАКРЕПЛЯЕТ, а не молчит об этом.
        """
        ts = datetime.datetime(2026, 9, 3, 19, 51, 13,
                               tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(cdr.day_lane(ts), "2026-09-04")
        self.assertEqual(cdr.day_utc(ts), "2026-09-03")
        self.assertIn("day_lane(now)", _src("contour_digest_run.py"))
        self.assertEqual(cdr.day_lane(None), "")      # третий исход, а не сегодняшний день

    def test_the_seam_never_exhausts_a_day_twice(self):
        """ЗАМОК СТЫКА, названный числом.

        Старый маркер с датой X написан внутри UTC-суток X = местного отрезка
        [X 07:00, X+1 07:00). В корзину БОЛЕЕ ПОЗДНИХ местных суток он не попадает
        никогда — строка сравнивается на равенство. Значит переезд не может
        «съесть» завтрашний бюджет: единственный возможный исход стыка —
        одноразовое обнуление, и оно проверено соседним тестом.
        """
        old = [("2026-09-03", "k%02d" % i) for i in range(3)]      # взяты по UTC-суткам 03.09
        self.assertEqual(sb.budget_left(old, "2026-09-04", sb.DAILY_BUDGET), sb.DAILY_BUDGET)
        self.assertEqual(sb.budget_left(old, "2026-09-05", sb.DAILY_BUDGET), sb.DAILY_BUDGET)
        # …и в СВОИ местные сутки они по-прежнему считаются, а не пропадают вовсе
        self.assertEqual(sb.budget_left(old, "2026-09-03", sb.DAILY_BUDGET), sb.DAILY_BUDGET - 3)

    def test_the_seam_resets_the_day_at_most_once_and_the_price_is_named(self):
        """Обратная половина стыка: ночные маркеры сегодня не видны — ОДИН раз.

        Задача, взятая 04.09 в 02:51 местного, помечена вчерашним числом (UTC
        03.09) и в счёт местного 04.09 не идёт. Худшая цена — прежний потолок 3
        сверх нового 8 за одни сутки перехода, и только в день выкатки.
        """
        night = sb.lane_day("2026-09-03T19:51:13Z")                # местное 04.09 02:51
        self.assertEqual(night, "2026-09-04")
        stamped = review_intake.today_utc("2026-09-03T19:51:13Z")  # а маркер несёт вот это
        self.assertEqual(stamped, "2026-09-03")
        self.assertNotEqual(night, stamped)
        self.assertEqual(sb.budget_left([(stamped, "kk1")], night, sb.DAILY_BUDGET),
                         sb.DAILY_BUDGET)
        # СО ВТОРЫХ СУТОК стык кончается: новые маркеры набраны местным днём
        fresh = [(night, "n%02d" % i) for i in range(sb.DAILY_BUDGET)]
        self.assertEqual(sb.budget_left(fresh, night, sb.DAILY_BUDGET), 0)


# ═══════════════════════════ руки ══════════════════════════════════════


class TestHands(unittest.TestCase):

    def test_dry_run_touches_no_queue_row(self):
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=False)
        self.assertEqual(q.tasks, [])
        self.assertEqual(rep["placed"], [])
        self.assertIn("kk1", rep["texts"])
        self.assertIn("сухой ход", " ".join(w for _k, w in rep["held"]))

    def test_the_expensive_closed_corpus_is_not_read_on_an_empty_box(self):
        """Пустой ящик — обычное состояние, и платить за него полминуты витка нельзя."""
        q = FakeQueue()
        _tick(_box(), queue=q, place=True)
        self.assertNotIn(run.CLOSED_STATUSES, q.asked)

    def test_the_closed_corpus_IS_read_when_there_is_a_candidate(self):
        """Положительный контроль экономии: когда есть что ставить — читаем всё."""
        q = FakeQueue()
        _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
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
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual(q.tasks, [])
        self.assertIn("очередь недоступна", rep["why"])

    def test_the_journal_line_names_the_key_the_document_and_the_row(self):
        """Строка журнала обязана закрывать вопрос «откуда взялась задача #N» БЕЗ
        похода в папку: с 03.09 ответом на него служит ИМЯ ДОКУМЕНТА."""
        line = sb.index_line({"key": "kk1", "name": sb.doc_name("kk1")}, 707, TODAY)
        self.assertIn("kk1", line)
        self.assertIn("#707", line)
        self.assertIn(sb.doc_name("kk1"), line)

    def test_the_only_file_the_box_leaves_is_its_long_memory(self):
        """ЗДЕСЬ СТОЯЛО «реестра на диске у ящика нет» (снято 05.09.2026).

        Прежний замок держал премису «файл съест первый self-update, а живая
        очередь вечна». Обе половины замерены и обе неверны (разбор —
        `shtab_box.merge_known`), и цена ошибки — три повторно взятых закрытых
        задания за одни сутки. Теперь на диск ложится РОВНО ОДИН файл — долгая
        память взятых ключей, и никакого второго: реестр, растущий сам по себе,
        остаётся запрещённым."""
        root = tempfile.mkdtemp(prefix="shtabbox_state_")
        _tick(_box(("kk1", GOOD_BODY)), queue=FakeQueue(), place=True, root=root)
        self.assertEqual(sorted(os.listdir(root)), [run.TAKEN_FILE],
                         "на диске обязан появиться ровно один файл — долгая память")

    def test_a_dry_run_writes_no_memory_at_all(self):
        """Сухой ход очередь не трогает — значит и памяти о взятии писать нечего.

        Запиши он ключ, `--dry` ОДИН РАЗ навсегда закрыл бы задание, которое
        никто не ставил, и заметить это было бы нечем: документ просто перестал
        бы браться."""
        root = tempfile.mkdtemp(prefix="shtabbox_dry_")
        _tick(_box(("kk1", GOOD_BODY)), queue=FakeQueue(), place=False, root=root)
        self.assertEqual(sorted(os.listdir(root)), [], "сухой ход оставил файл на диске")
        rows, ok, why = run.read_taken(root)
        self.assertEqual((rows, ok), ([], True), why)


# ═══════════════════ долгая память взятых ключей ═══════════════════════


class TestLongMemory(unittest.TestCase):
    """Ящик помнит взятые ключи ДОЛЬШЕ, чем живёт ряд очереди (05.09.2026).

    ЖИВОЙ УЩЕРБ, ОТ КОТОРОГО ЭТОТ КЛАСС: 05.09 номера очереди пошли по кругу
    (#241 в 11:19 → «номер переиспользован очередью», ряды 1..7 в 11:44), вместе
    с рядами исчезли маркеры, и ящик взял ЗАНОВО три уже закрытых задания. Все
    проверки ниже — отрицательные: они падают на коде, у которого память живёт
    только в очереди.
    """

    def _root(self):
        return tempfile.mkdtemp(prefix="shtabbox_mem_")

    # ── три обязательные отрицательные ──────────────────────────────────

    def test_a_closed_key_is_not_taken_again_after_the_daemon_restarts(self):
        """ОТРИЦАТЕЛЬНАЯ №1: закрытый ключ не берётся ВТОРОЙ РАЗ и после рестарта.

        Рестарт демона моделируется честно: второй оборот идёт ДРУГИМ вызовом,
        с ПУСТОЙ очередью (ряд закрылся и уехал) и без единого общего объекта в
        памяти — общий у двух оборотов только корень на диске.
        """
        root = self._root()
        first = _tick(_box(("kk1", GOOD_BODY)), queue=FakeQueue(), place=True, root=root)
        self.assertEqual([r["key"] for r in first["placed"]], ["kk1"])

        second = _tick(_box(("kk1", GOOD_BODY)), queue=FakeQueue(), place=True, root=root)
        self.assertEqual(second["placed"], [], "закрытый ключ взят ВТОРОЙ раз")
        self.assertIn("уже брали", dict(second["held"])["kk1"])

    def test_a_fresh_key_is_taken_as_usual_while_memory_is_full(self):
        """ОТРИЦАТЕЛЬНАЯ №2: память НЕ глушит ящик — новый ключ берётся как обычно.

        Замок против пере-затягивания: правка, останавливающая всё подряд, прошла
        бы проверку №1 и была бы негодной.
        """
        root = self._root()
        for key in ("kk1", "kk3", "kk4"):
            self.assertEqual(run.remember(root, key=key, day=TODAY, lane="pc")[0], True)
        rep = _tick(_box(("kk2", GOOD_BODY)), queue=FakeQueue(), place=True, root=root)
        self.assertEqual([r["key"] for r in rep["placed"]], ["kk2"], rep["why"])

    def test_the_queue_numbering_wrapping_around_does_not_touch_key_dedup(self):
        """ОТРИЦАТЕЛЬНАЯ №3: круг номеров очереди на дедуп по ключу не влияет.

        Дословный слепок живого случая: ряд ключа стоял под номером 241, номера
        пошли по кругу, и теперь под номерами 1..7 лежат ЧУЖИЕ ряды. Дедуп судит
        ИМЯ ЗАДАЧИ, а не номер, — значит ключ обязан остаться взятым.
        """
        root = self._root()
        big = FakeQueue()
        big.next_id = 240
        first = _tick(_box(("kk1", GOOD_BODY)), queue=big, place=True, root=root)
        self.assertEqual(first["placed"][0]["id"], 241)

        wrapped = FakeQueue(rows=[{"id": 1, "task_text": "чужой ряд после круга"},
                                  {"id": 2, "task_text": "и ещё один"}],
                            closed=[{"id": 3, "task_text": "закрытый чужой"}])
        wrapped.next_id = 3
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=wrapped, place=True, root=root)
        self.assertEqual(rep["placed"], [], "после круга номеров ключ взят заново")
        self.assertIn("уже брали", dict(rep["held"])["kk1"])
        self.assertEqual(wrapped.tasks, [], "в очередь после круга уехал ряд")

    # ── устройство памяти ───────────────────────────────────────────────

    def test_memory_is_written_only_after_the_row_actually_stood(self):
        """Порядок обязателен: ряд → память. Не встал ряд — ключ НЕ запоминается,
        иначе задание не возьмут больше никогда, и заметить это будет нечем."""
        root = self._root()

        class Refusing(FakeQueue):
            def place_task(self, text, lane=None):
                return False, None, "мост отказал"

        rep = _tick(_box(("kk1", GOOD_BODY)), queue=Refusing(), place=True, root=root)
        self.assertEqual(rep["placed"], [])
        rows, ok, why = run.read_taken(root)
        self.assertEqual((rows, ok), ([], True), why)

    def test_the_same_key_never_grows_the_file_twice(self):
        """Дозапись идемпотентна по ключу: дожим ходит витками, а файл — не лента."""
        root = self._root()
        self.assertEqual(run.remember(root, key="kk1", day=TODAY, lane="pc")[0], True)
        self.assertEqual(run.remember(root, key="kk1", day=TODAY, lane="pc")[0], True)
        rows, ok, _why = run.read_taken(root)
        self.assertTrue(ok)
        self.assertEqual([r["key"] for r in rows], ["kk1"])

    def test_an_unreadable_memory_stops_the_box_instead_of_reading_as_empty(self):
        """ТРЕТИЙ ИСХОД: битую память нельзя прочитать как пустую.

        Пустая память у ящика, который ещё ничего не брал, и нечитаемая память —
        разные новости; по длине списка они одинаковы. Прочитай мы вторую как
        первую, ящик заново взял бы ВСЁ, что лежит в папке."""
        root = self._root()
        with io.open(run.taken_path(root), "w", encoding="utf-8") as fh:
            fh.write('{"key": "kk1"}\n{это не json}\n')
        rows, ok, why = run.read_taken(root)
        self.assertEqual((rows, ok), ([], False))
        self.assertIn("НЕИЗВЕСТНО", why)

        rep = _tick(_box(("kk2", GOOD_BODY)), queue=FakeQueue(), place=True, root=root)
        self.assertEqual(rep["placed"], [], "на нечитаемой памяти ящик взял задание")
        self.assertIn("долгая память", rep["why"])

    def test_a_missing_file_is_an_honest_zero_and_not_a_failure(self):
        """Ящик, который ещё ничего не брал, обязан работать: файла нет — это ноль."""
        root = self._root()
        rows, ok, why = run.read_taken(root)
        self.assertEqual((rows, ok), ([], True))
        self.assertIn("не заводился", why)

    def test_memory_only_adds_keys_and_never_removes_one(self):
        """Слияние ослабить дедуп не может ни одной веткой: множество только растёт."""
        marks, by_lane = sb.merge_known(
            [(TODAY, "from-queue")], {"pc": [(TODAY, "from-queue")], "vps": []},
            [{"key": "from-memory", "day": TODAY, "lane": "pc"}])
        self.assertEqual({k for _d, k in marks}, {"from-queue", "from-memory"})
        self.assertEqual({k for _d, k in by_lane["pc"]}, {"from-queue", "from-memory"})

    def test_a_key_known_to_both_sources_is_counted_once(self):
        """Дважды посчитанный ключ съел бы суточный потолок дважды."""
        marks, by_lane = sb.merge_known(
            [(TODAY, "kk1")], {"pc": [(TODAY, "kk1")], "vps": []},
            [{"key": "kk1", "day": TODAY, "lane": "vps"}])
        self.assertEqual(marks, [(TODAY, "kk1")])
        self.assertEqual(sb.marks_today(marks, TODAY), 1)
        self.assertEqual(by_lane["vps"], [])

    def test_a_memory_record_without_a_day_blocks_the_key_but_no_budget(self):
        """ТРЕТИЙ ИСХОД у поля дня: «когда взяли» неизвестно — ключ всё равно взят,
        а вот чужой суточный бюджет он не тратит."""
        marks, _by_lane = sb.merge_known([], {"pc": [], "vps": []},
                                         [{"key": "kk1", "lane": "pc"}])
        self.assertEqual([k for _d, k in marks], ["kk1"])
        self.assertEqual(sb.marks_today(marks, TODAY), 0)

    def test_a_memory_record_without_a_lane_reads_as_pc(self):
        """Пустая полоса читается как ПК — ТЕМ ЖЕ правилом, что у ряда очереди
        (`row_lane`), а не отдельным мнением слияния."""
        _marks, by_lane = sb.merge_known([], {"pc": [], "vps": []},
                                         [{"key": "kk1", "day": TODAY}])
        self.assertEqual([k for _d, k in by_lane[sb.LANE_DEFAULT]], ["kk1"])
        self.assertEqual(by_lane["vps"], [])

    def test_the_memory_file_is_ignored_by_git(self):
        """Отслеживаемый файл откатывался бы к HEAD вместе с деревом — то есть
        память стиралась бы молча ровно тем же способом, что и в очереди."""
        with io.open(os.path.join(HERE, ".gitignore"), encoding="utf-8") as fh:
            self.assertIn(run.TAKEN_FILE, fh.read())


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


# ═══════════════════════ ПОЛОСА ИСПОЛНЕНИЯ (03.09.2026) ═══════════════════════


def _lane_body(word, body=None):
    """Тело задания с назначенной полосой. Строку собирает ОДНО место — тест не
    смеет знать форму лучше кода: набери мы её здесь литералом, правка формы в
    :mod:`shtab_box` осталась бы зелёной у себя и красной в бою."""
    base = GOOD_BODY if body is None else body
    return base + "\n\nПОЛОСА: %s" % word


class FakeBridge(object):
    """Клиент моста-заглушка: помнит ПОЛОСУ каждого вопроса и каждой постановки."""

    def __init__(self, items=None, ok=True):
        self._items, self._ok = dict(items or {}), ok
        self.asked, self.placed = [], []
        self.next_id = 500

    def get_pending(self, status, lane="pc"):
        self.asked.append((status, lane))
        if not self._ok:
            return {"ok": False, "error": "мост не ответил"}
        return {"ok": True, "items": list(self._items.get(status) or [])}

    def enqueue_task(self, frm, text, lane="pc"):
        self.next_id += 1
        self.placed.append({"from": frm, "lane": lane, "text": text})
        return {"ok": True, "id": self.next_id}


class FakeDaemon(object):
    """Демон-заглушка для рук: только то, что руки у него действительно берут."""

    def __init__(self, bridge=None):
        self.bc = bridge or FakeBridge()
        self.pc_calls = []

    def enqueue_pc_task(self, text, frm="Filipp"):
        self.pc_calls.append((frm, text))
        r = self.bc.enqueue_task(frm, text, lane=sb.LANE_PC)
        return True, r.get("id"), None

    def _revizor_chain_pids(self, rows):
        return set()

    def _is_owner_work(self, item, pids):
        return str(item.get("from") or "").startswith("Filipp")


class TestLaneForm(unittest.TestCase):
    """ФОРМА УКАЗАНИЯ ПОЛОСЫ: одна, в теле, с тремя исходами."""

    def test_the_form_lives_in_exactly_one_place(self):
        """Второй экземпляр формы — второе мнение о том, куда ехать заданию."""
        self.assertEqual(("pc", "vps"), sb.LANES)
        self.assertEqual("pc", sb.LANE_DEFAULT)
        hands = _src("shtab_box_run.py")
        self.assertNotIn("ПОЛОСА:", hands, "форма набрана в руках литералом")
        self.assertNotIn('"vps"', hands, "имя чужой полосы набрано в руках литералом")
        self.assertIn("shtab_box.LANE", hands, "руки обязаны брать полосу у чистого модуля")

    def test_the_lane_is_read_from_the_BODY_and_never_from_the_name(self):
        """ИМЯ НЕСЁТ КЛЮЧ, и смешивать роли нельзя.

        Живой документ 03.09 назван `shtab_task_srv-delivery-check.0903` — «srv», про
        сервер, — а исполняется ПОЛОСОЙ ПК (тянет с сервера по ssh и правит общий
        репозиторий отсюда). Читай мы полосу из имени, он уехал бы не туда МОЛЧА.
        """
        doc = {"key": "srv-delivery-check.0903", "name": "shtab_task_srv-delivery-check.0903",
               "body": GOOD_BODY}
        self.assertEqual(sb.LANE_PC, sb.lane_of(doc)["lane"])
        self.assertFalse(sb.lane_of(doc)["named"])

    def test_an_unnamed_lane_is_PC_and_the_default_is_SAID_not_implied(self):
        got = sb.read_lane(GOOD_BODY)
        self.assertTrue(got["ok"])
        self.assertFalse(got["named"])
        self.assertEqual(sb.LANE_PC, got["lane"])
        self.assertIn("не названа", got["why"])
        self.assertIn("по умолчанию", sb.lane_words(got))

    def test_a_named_lane_is_taken_in_all_its_spellings(self):
        for word, lane in (("пк", sb.LANE_PC), ("PC", sb.LANE_PC), ("ПК", sb.LANE_PC),
                           ("сервер", sb.LANE_VPS), ("VPS", sb.LANE_VPS),
                           ("vps", sb.LANE_VPS), ("Server", sb.LANE_VPS)):
            got = sb.read_lane(_lane_body(word))
            self.assertTrue(got["ok"], word)
            self.assertTrue(got["named"], word)
            self.assertEqual(lane, got["lane"], word)

    def test_the_tolerances_are_the_measured_ones_and_not_wishful(self):
        """Маркер списка, знак «=», хвостовая точка и регистр слова ПОЛОСА.

        Каждая мелочь куплена НЕСИММЕТРИЧНОЙ ценой ошибки: непонятая строка = МОЛЧА
        на ПК, лишняя терпимость = громкий отказ. Штаб пишет списками (живой блок
        запретов — четыре пункта с «•»), поэтому «• ПОЛОСА: сервер» обязано читаться.
        """
        for line in ("• ПОЛОСА: сервер", "- полоса: сервер", "  ПОЛОСА = сервер",
                     "ПОЛОСА: сервер.", "Полоса: СЕРВЕР"):
            got = sb.read_lane(GOOD_BODY + "\n" + line)
            self.assertEqual(sb.LANE_VPS, got["lane"], line)

    def test_an_UNKNOWN_lane_name_refuses_the_task_ENTIRELY(self):
        """Требование задания дословно: неизвестная полоса — задание НЕ БЕРЁТСЯ вовсе."""
        got = sb.read_lane(_lane_body("марс"))
        self.assertFalse(got["ok"])
        self.assertIn("НЕИЗВЕСТНО", got["why"])
        ok, reason, why = sb.check({"key": "kk1", "body": _lane_body("марс")})
        self.assertFalse(ok)
        self.assertEqual("bad_lane", reason)
        self.assertIn("марс", why)
        self.assertIn(sb.LANE_FORM, why)

    def test_a_LONG_lane_line_is_refused_LOUDLY_and_not_defaulted_SILENTLY(self):
        """Мина захвата: короткий захват сделал бы длинную строку НЕСОВПАВШЕЙ,
        то есть явное указание Штаба ушло бы в дефолт молча."""
        got = sb.read_lane(_lane_body("сервер, потому что доставка живёт именно там"))
        self.assertFalse(got["ok"])
        self.assertTrue(got["named"])

    def test_two_DIFFERENT_lane_lines_refuse_both_the_citation_trap(self):
        """Канон запрещает цитировать форму маркера в прозе — здесь тот же класс.

        «Первое побеждает» дало бы ТИХИЙ промах на задании ПРО полосы (вроде этого):
        поехали бы по цитате. Несогласие двух имён даёт громкий отказ.
        """
        body = _lane_body("сервер") + "\nПОЛОСА: пк"
        got = sb.read_lane(body)
        self.assertFalse(got["ok"])
        self.assertIn("цитировать", got["why"])
        # А ДВА ОДИНАКОВЫХ указания — не противоречие: цитата, совпавшая с делом,
        # ничего не ломает, и отказывать на ней значило бы штрафовать за повтор.
        self.assertTrue(sb.read_lane(_lane_body("сервер") + "\nПОЛОСА: vps")["ok"])

    def test_the_row_text_names_the_lane_and_WHO_chose_it(self):
        named = sb.task_text({"key": "kk1", "name": "shtab_task_kk1", "id": "f1",
                              "body": _lane_body("сервер")}, TODAY)
        self.assertIn("VPS", named)
        self.assertIn("названа заданием", named)
        default = sb.task_text({"key": "kk1", "name": "shtab_task_kk1", "id": "f1",
                                "body": GOOD_BODY}, TODAY)
        self.assertIn("по умолчанию", default)
        # МАРКЕР ПЕРВОЙ СТРОКИ НЕ ТРОНУТ НИ СИМВОЛОМ: его читают дедуп, суточный счёт
        # и сводка контура. Смени мы его — вчерашние задания взялись бы заново, молча.
        for text in (named, default):
            self.assertTrue(sb.MARK_RE.match(text.splitlines()[0]))


class TestLaneRoute(unittest.TestCase):
    """МАРШРУТ: куда именно уезжает ряд и что при этом видно в логе."""

    def test_an_unnamed_lane_goes_to_PC_and_it_is_VISIBLE_IN_THE_LOG(self):
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True)
        self.assertEqual([sb.LANE_PC], q.lanes)
        self.assertEqual(1, len(rep["placed"]))
        self.assertEqual(sb.LANE_PC, rep["placed"][0]["lane"])
        # ТРИ МЕСТА, ГДЕ ДЕФОЛТ ОБЯЗАН ПРОЗВУЧАТЬ СЛОВАМИ, а не подразумеваться:
        # строка-индекс журнала, строка исхода оборота и отчёт.
        line = sb.index_line(rep["build"]["docs"][0], rep["placed"][0]["id"], TODAY)
        self.assertIn("по умолчанию", line)
        self.assertIn("по умолчанию", run._line(rep))
        self.assertIn("ПК", rep["why"])

    def test_a_NAMED_vps_lane_really_goes_to_the_other_lane(self):
        """Положительный контроль к дефолту: без него правило «всё на ПК» проходит
        отрицательные проверки идеально и стои́т ноль."""
        q = FakeQueue()
        rep = _tick(_box(("kk1", _lane_body("сервер"))), queue=q, place=True)
        self.assertEqual([sb.LANE_VPS], q.lanes)
        self.assertEqual(sb.LANE_VPS, rep["placed"][0]["lane"])
        self.assertIn("названа заданием", run._line(rep))

    def test_an_unknown_lane_is_NOT_PLACED_ANYWHERE_and_says_why(self):
        q = FakeQueue()
        rep = _tick(_box(("kk1", _lane_body("марс"))), queue=q, place=True)
        self.assertEqual([], rep["placed"])
        self.assertEqual([], q.tasks)
        self.assertEqual([], q.lanes)
        why = " ".join(w for _k, w in rep["held"])
        self.assertIn("bad_lane", why)
        self.assertIn("марс", why)

    def test_the_PC_road_still_goes_through_the_daemons_own_placer(self):
        """Дорога полосы ПК не тронута: она идёт прежним постановщиком демона (у
        которого lane зашит) и его строкой в логе, а не сырым экшеном моста."""
        d = FakeDaemon()
        ok, tid, err = run.Queue(daemon=d).place_task("текст", lane=sb.LANE_PC)
        self.assertTrue(ok, err)
        self.assertEqual(1, len(d.pc_calls))
        self.assertEqual(run.FROM, d.pc_calls[0][0])
        self.assertEqual(sb.LANE_PC, d.bc.placed[0]["lane"])

    def test_the_foreign_lane_goes_by_the_bridge_action_with_the_lane_named(self):
        d = FakeDaemon()
        ok, tid, err = run.Queue(daemon=d).place_task("текст", lane=sb.LANE_VPS)
        self.assertTrue(ok, err)
        self.assertEqual([], d.pc_calls, "чужая полоса не смеет ехать дорогой полосы ПК")
        self.assertEqual(sb.LANE_VPS, d.bc.placed[0]["lane"])
        self.assertEqual(run.FROM, d.bc.placed[0]["from"])

    def test_a_lane_OUTSIDE_the_set_is_refused_by_the_hands_themselves(self):
        """Замок на случай опечатки вызывающего: полосы, которой нет, ряд не достаётся."""
        d = FakeDaemon()
        ok, tid, why = run.Queue(daemon=d).place_task("текст", lane="марс")
        self.assertFalse(ok)
        self.assertIsNone(tid)
        self.assertEqual([], d.bc.placed)
        self.assertIn("не из набора", why)


class TestLaneCeilings(unittest.TestCase):
    """ПОТОЛКИ РАЗДЕЛЬНЫЕ — и это проверяется, а не обещается."""

    def _vps_row(self, key, day=TODAY):
        row = _row(key, day)
        row["lane"] = sb.LANE_VPS
        return row

    def test_a_VPS_task_does_NOT_eat_the_PC_ceiling(self):
        """Прямое требование задания. Три ЧУЖИХ постановки за сегодня не смеют
        закрыть день полосе ПК: она на них не потратила ни одного оборота."""
        closed = [self._vps_row("v%02d" % i) for i in range(sb.DAILY_BUDGET)]
        q = FakeQueue(closed=closed)
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True, budget=sb.DAILY_BUDGET)
        self.assertEqual(1, len(rep["placed"]), rep["held"])
        self.assertEqual(sb.LANE_PC, rep["placed"][0]["lane"])

    def test_and_the_PC_ceiling_still_stops_the_PC_lane_positive_control(self):
        """Контроль к предыдущему: те же три ряда СВОЕЙ полосы день закрывают."""
        closed = [_row("p%02d" % i) for i in range(sb.DAILY_BUDGET)]
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=FakeQueue(closed=closed), place=True,
                    budget=sb.DAILY_BUDGET)
        self.assertEqual([], rep["placed"])
        self.assertIn("суточный потолок исчерпан",
                      " ".join(w for _k, w in rep["held"]))

    def test_the_vps_ceiling_stops_the_vps_lane_by_its_OWN_count(self):
        closed = [self._vps_row("v%02d" % i) for i in range(sb.DAILY_BUDGET)]
        rep = _tick(_box(("kk1", _lane_body("сервер"))), queue=FakeQueue(closed=closed),
                    place=True, budget=sb.DAILY_BUDGET)
        self.assertEqual([], rep["placed"])
        self.assertIn("на полосу VPS", " ".join(w for _k, w in rep["held"]))

    def test_the_TICK_limit_stays_COMMON_to_both_lanes(self):
        """Замок «не больше одной за виток» маршрутом НЕ ослаблен: две полосы не
        дают права поставить две задачи за оборот."""
        rep = _tick(_box(("aa1", GOOD_BODY), ("bb1", _lane_body("сервер"))),
                    queue=FakeQueue(), place=True)
        self.assertEqual(1, len(rep["placed"]))
        self.assertIn("не больше 1 за виток", " ".join(w for _k, w in rep["held"]))


class TestLaneDedup(unittest.TestCase):
    """ГЛАВНАЯ МИНА МАРШРУТА: дедуп обязан видеть ОБЕ полосы."""

    def test_a_task_taken_on_the_VPS_lane_is_NEVER_taken_again(self):
        """Без этого ящик ставил бы одно и то же задание КАЖДЫЙ ВИТОК — молча.

        Наследованное чтение очереди спрашивало только свою полосу. Ряд уехал на
        чужую → маркер невидим → и дедуп, и суточный потолок (они считают ОДНИ И ТЕ
        ЖЕ маркеры) промахнулись бы разом. Ровно класс 539, давший на полосе VPS
        четыре дубля за трое суток.
        """
        row = _row("kk1")
        row["lane"] = sb.LANE_VPS
        q = FakeQueue(closed=[row])
        rep = _tick(_box(("kk1", _lane_body("сервер"))), queue=q, place=True)
        self.assertEqual([], rep["placed"])
        self.assertEqual([], q.tasks)
        self.assertIn("уже брали", " ".join(w for _k, w in rep["held"]))

    def _end_to_end(self, bridge, docs):
        """Оборот НА НАСТОЯЩЕЙ очереди, с заглушкой на границе МОСТА, а не выше её.

        Это не педантизм: мина маршрута жила ИМЕННО в чтении очереди
        (наследованный `rows` спрашивал одну полосу), и проверка на очереди-заглушке
        зеленела бы у обоих кодов — и у чинёного, и у дырявого. Заглушку опускаем до
        клиента моста, чтобы в проверку попал сам вопрос «а какую полосу спросили».
        """
        files, bodies = _files(docs)
        return run.tick(root=tempfile.mkdtemp(prefix="shtablane_"), place=True,
                        queue=run.Queue(daemon=FakeDaemon(bridge)),
                        lister=_lister(files), doc_reader=_doc_reader(bodies),
                        reader=_reader(HEAD_TEXT),
                        ledger=lambda root: ({}, True, ""),
                        clock=lambda tz: __import__("datetime").datetime(2026, 9, 2, 12, 0,
                                                                         tzinfo=tz))

    def test_END_TO_END_the_vps_marker_is_seen_through_the_real_queue(self):
        """Тот же дедуп, но через НАСТОЯЩЕЕ чтение очереди. Спрашивай ящик одну
        полосу — маркер был бы невидим, и задание уехало бы ВТОРОЙ раз."""
        row = dict(_row("kk1"), lane=sb.LANE_VPS, status="done")
        bridge = FakeBridge({"done": [row]})
        rep = self._end_to_end(bridge, _box(("kk1", _lane_body("сервер"))))
        self.assertEqual([], rep["placed"], rep["why"])
        self.assertEqual([], bridge.placed)
        self.assertIn("уже брали", " ".join(w for _k, w in rep["held"]))

    def test_END_TO_END_positive_control_the_same_task_without_the_marker_goes(self):
        """Без положительного контроля предыдущий тест проходит и у ящика, который
        не ставит НИЧЕГО НИКОГДА."""
        bridge = FakeBridge({})
        rep = self._end_to_end(bridge, _box(("kk1", _lane_body("сервер"))))
        self.assertEqual(1, len(rep["placed"]), rep["why"])
        self.assertEqual(sb.LANE_VPS, bridge.placed[0]["lane"])
        self.assertIn(sb.HEAD_WORDS, bridge.placed[0]["text"])

    def test_the_queue_is_asked_for_BOTH_lanes_in_ONE_call_per_status(self):
        """Один вызов на статус, а не два: цена витка не смеет удвоиться там, где
        задание просит его учащать. `lane='all'` проверен живой пробой 03.09."""
        d = FakeDaemon()
        rows, ok, why = run.Queue(daemon=d).rows(("new", "done"))
        self.assertTrue(ok, why)
        self.assertEqual([("new", run.LANE_ALL), ("done", run.LANE_ALL)], d.bc.asked)

    def test_rows_carry_their_OWN_lane_and_the_empty_field_reads_as_PC(self):
        """Пустое поле = ПК — то же правило, что у ступеней B и E. Третий исход
        здесь завёл бы второе мнение о том, чей это ряд."""
        d = FakeDaemon(FakeBridge({"new": [{"id": 1}, {"id": 2, "lane": "vps"}]}))
        rows, ok, _why = run.Queue(daemon=d).rows(("new",))
        self.assertEqual([sb.LANE_PC, sb.LANE_VPS], [r["lane"] for r in rows])
        split = sb.rows_by_lane(rows)
        self.assertEqual([1], [r["id"] for r in split[sb.LANE_PC]])
        self.assertEqual([2], [r["id"] for r in split[sb.LANE_VPS]])

    def test_the_owner_lock_still_judges_ONLY_our_own_lane(self):
        """Правило зеркала: посылку меряют ЗДЕСЬ. Карточка владельца на сервере не
        смеет глушить ящик полосы ПК — этого никто не мерил и никто не заказывал."""
        foreign = {"id": 77, "from": "Filipp-328-dev", "status": "new", "lane": sb.LANE_VPS,
                   "task_text": "чужая работа владельца"}
        d = FakeDaemon(FakeBridge({"new": [foreign]}))
        q = run.Queue(daemon=d)
        rows, _ok, _why = q.rows(("new",))
        busy, ids = q.owner_busy(sb.rows_by_lane(rows)[sb.LANE_PC])
        self.assertFalse(busy)
        self.assertEqual([], ids)
        # Положительный контроль: тот же ряд СВОЕЙ полосы замок видит.
        own = dict(foreign, lane=sb.LANE_PC)
        busy2, ids2 = q.owner_busy([own])
        self.assertTrue(busy2)
        self.assertEqual([77], ids2)


class TestLaneCost(unittest.TestCase):
    """ЦЕНА ВЗГЛЯДА: перечисление за виток РОВНО ОДНО, и пауза названа числом."""

    def test_exactly_ONE_folder_listing_per_tick(self):
        """Прямой запрет задания. Перечисление — самый дешёвый из вызовов витка
        (2.42–2.66 с замером 03.09), но второе означало бы, что источник читается
        дважды и может разойтись сам с собой внутри одного оборота."""
        files, bodies = _files(_box(("kk1", GOOD_BODY)))
        lister = _lister(files)
        _tick(_box(("kk1", GOOD_BODY)), lister=lister, doc_reader=_doc_reader(bodies),
              queue=FakeQueue(), place=True)
        self.assertEqual(1, len(lister.seen), "перечислений за виток больше одного")

    def test_the_pause_is_measured_and_not_inherited(self):
        """1800 с были СПИСАНЫ со ступени E, а не измерены для ящика. Новое число
        обосновано ценой взгляда (19.6 и 33.7 с живым замером) и худшим
        застрявшим вызовом моста (84.3 с из 18 вызовов двух проб 03.09)."""
        import pc_orchestrator as o

        self.assertEqual(600.0, o.SHTAB_BOX_MIN_SEC)
        # Запас к худшему ИЗМЕРЕННОМУ взгляду — не меньше семикратного: виток ящика
        # синхронен внутри poll_once, и его пауза судится О2.
        self.assertGreaterEqual(o.SHTAB_BOX_MIN_SEC / 84.3, 7.0)

    def test_an_unreachable_listing_places_nothing_on_EITHER_lane(self):
        """Отрицательный тест задания, проверенный ЗАНОВО на двух полосах: не зная
        источника, ящик не ставит ничего и никуда — и очередь даже не спрашивает."""
        q = FakeQueue()
        rep = _tick(_box(("kk1", _lane_body("сервер"))),
                    lister=_lister([], ok=False, why="мост не ответил"), queue=q, place=True)
        self.assertEqual([], rep["placed"])
        self.assertEqual([], q.tasks)
        self.assertEqual([], q.lanes)
        self.assertEqual([], q.asked)
        self.assertIn("НЕИЗВЕСТНО", rep["why"])
        self.assertNotIn("пуст", rep["why"])


class TestBridgeBudgetThirdOutcome(unittest.TestCase):
    """ТРЕТИЙ ИСХОД ПОТОЛОКА МОСТА доезжает до ящика СЛОВАМИ (04.09.2026).

    «Не спрашивали, потому что мост был занят» — это НЕ ноль и НЕ отказ прибора. Ящик, увидевший
    такое, не берёт ничего и говорит причину; молчаливое «взято 0» было бы неотличимо от честно
    пустой очереди, а голое имя класса — от поломки моста."""

    class _Daemon:
        """Демон, у которого очередь уже упёрлась в потолок витка."""

        def __init__(self):
            import pc_orchestrator as o

            self.o = o
            self.budget = o.BridgeLoopBudget(limit=1)
            self.budget.spend(50.0, "get_pending")          # потолок исчерпан ДО нашего чтения
            self.bc = self._Bc(self)

        class _Bc:
            def __init__(self, d):
                self.d = d
                self.went_to_net = 0

            def get_pending(self, status, lane=None):
                if self.d.budget.exhausted():
                    return self.d.budget.skip("get_pending")
                self.went_to_net += 1
                return {"ok": True, "items": []}

    def test_the_box_takes_nothing_and_names_the_reason_in_words(self):
        d = self._Daemon()
        rows, ok, why = run.Queue(d).rows(run.OPEN_STATUSES)
        self.assertEqual((rows, ok), ([], False), "на срезанном чтении ящик не берёт НИЧЕГО")
        self.assertEqual(d.bc.went_to_net, 0, "срезанное чтение в сеть не уходит вовсе")
        self.assertIn("не спрашивали", why)
        self.assertIn("мост был занят", why)
        self.assertNotIn("BridgeBudgetExhausted", why,
                         "имя класса — не причина: тот же класс ложных диагнозов закрыт в explain")

    def test_the_same_holds_for_the_parent_lane_reader(self):
        """Ступень E (`recon_auto_run.Queue`) — та же дверь и то же правило."""
        import recon_auto_run

        d = self._Daemon()
        rows, ok, why = recon_auto_run.Queue(d).rows(recon_auto_run.OPEN_STATUSES)
        self.assertEqual((rows, ok), ([], False))
        self.assertIn("не спрашивали", why)

    def test_a_healthy_read_is_untouched(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ у потребителя: непочатый потолок ящик не задевает ничем."""
        d = self._Daemon()
        d.budget.reset(limit=900)
        rows, ok, why = run.Queue(d).rows(run.OPEN_STATUSES)
        self.assertEqual((rows, ok, why), ([], True, ""))
        self.assertEqual(d.bc.went_to_net, len(run.OPEN_STATUSES))


class _Sender(object):
    """Дверь наружу-заглушка в форме ``dispatch_notify.send_topic_strict``.

    Форма повторена ДОСЛОВНО — ``(текст, тема) → (канал, ok, detail)``: мок,
    разошедшийся с живой дверью, зеленел бы молча, и это ровно тот класс, на
    котором полоса ПК уже обжигалась (`CLAUDE.md`, «Форматы»).
    """

    def __init__(self, ok=True, why="тема закрыта"):
        self._ok, self._why = ok, why
        self.sent = []

    def __call__(self, text, topic):
        self.sent.append((text, int(topic)))
        return ("topic:%s" % topic, self._ok, "9001" if self._ok else self._why)


class _Notifier(object):
    """Заглушка на месте :func:`run.notify_taken` — считает ИЗВЕЩЕНИЯ, а не отправки."""

    def __init__(self, ok=True, why="не отправлено", boom=None):
        self._ok, self._why, self._boom = ok, why, boom
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        if self._boom:
            raise RuntimeError(self._boom)
        return (self._ok, "9001" if self._ok else "", "" if self._ok else self._why)


class TestTakeNotice(unittest.TestCase):
    """ВЗЯТИЕ ВИДНО ВЛАДЕЛЬЦУ (задание 05-box-take-visible.0905).

    ПРЕМИСА, ПЕРЕМЕРЕННАЯ ПО КОДУ 06.09.2026, а не принятая на слово: до этой
    правки о взятии задания Штаба знали только лог демона и журнал среды
    (``_journal`` → ``cowork_log``), а отправки наружу у ящика не было ни одной —
    имя ``dispatch_notify`` не встречалось ни в одном из четырёх его модулей.
    Владелец видел собственные задания потому, что сам их отправлял; положенное
    Штабом было для него невидимо вплоть до закрытия. Премиса подтвердилась.

    ПРЕДСМЕРТНЫЙ ВЗГЛЯД ЗАДАНИЯ ЗАКРЫТ ДВУМЯ ТЕСТАМИ, а не обещанием: извещение
    не смеет стать карточкой с кнопками (:meth:`test_the_notice_carries_no_buttons_
    and_never_goes_to_the_inbox`) и не смеет стать пересказом задания
    (:meth:`test_the_notice_is_two_short_lines_and_quotes_the_goal_verbatim`).
    """

    # ── текст ────────────────────────────────────────────────────────────────

    def test_the_notice_names_the_row_the_key_the_lane_and_the_author(self):
        """Пункт 3 задания: номер очереди, ключ, полоса, суть цели, пометка Штаба."""
        blk = {"key": "kk1", "body": GOOD_BODY, "lane": sb.LANE_PC, "lane_named": True}
        text = sb.take_notice(blk, 707, TODAY)
        self.assertIn("#707", text)
        self.assertIn("kk1", text)
        self.assertIn("ПК", text)
        self.assertIn("пересчитать", text)          # ДОСЛОВНЫЙ кусок цели, не пересказ
        self.assertIn(sb.NOTICE_BY_SHTAB, text)

    def test_the_notice_is_two_short_lines_and_quotes_the_goal_verbatim(self):
        """Пункт 4: одна-две строки, без пересказа. Длинное режется по дороге."""
        blk = {"key": "kk1", "body": GOOD_BODY, "lane": sb.LANE_PC, "lane_named": True}
        text = sb.take_notice(blk, 707, TODAY)
        self.assertEqual(len(text.splitlines()), 2, "извещение расползлось за две строки")
        self.assertLessEqual(len(text), sb.NOTICE_MAX)
        # ЗАПРЕТЫ, ПРИЗНАК СДЕЛАННОСТИ И АДРЕС — ЭТО И ЕСТЬ ПЕРЕСКАЗ ЗАДАНИЯ.
        # Ни один из них в извещении стоять не смеет: владельцу показывают, что
        # взяли, а не переписывают ему постановку.
        for chunk in ("ПРИЗНАК СДЕЛАННОСТИ", "АДРЕС РЕЗУЛЬТАТА", "ничего не удалять"):
            self.assertNotIn(chunk, text, "извещение пересказывает задание: %s" % chunk)

    def test_a_long_goal_loses_its_tail_and_never_the_head(self):
        """Режем ХВОСТ цели: номер, ключ и пометка происхождения — то, ради чего
        извещение существует, и обрезаться они не смеют ни при какой длине."""
        blk = {"key": "kk1", "lane": sb.LANE_PC, "lane_named": True,
               "body": "ЦЕЛЬ: " + ("длинная цель " * 200)}
        text = sb.take_notice(blk, 707, TODAY)
        self.assertLessEqual(len(text), sb.NOTICE_MAX)
        self.assertIn("#707", text)
        self.assertIn("kk1", text)
        self.assertIn(sb.NOTICE_BY_SHTAB, text)
        self.assertTrue(text.rstrip().endswith("…"), "обрезали не хвост")

    def test_the_goal_is_the_GOAL_and_not_the_first_line_of_the_body(self):
        """Первой строкой тела стои́т «ПОЛОСА: пк». Взяв её за цель, извещение
        рассказывало бы про полосу дважды, а про цель — ни разу."""
        body = "ПОЛОСА: пк\n\nЦЕЛЬ. Владелец видит взятие своими глазами.\n\nЧТО СДЕЛАТЬ\n1. …"
        self.assertEqual(sb.goal_of(body), "Владелец видит взятие своими глазами.")

    def test_a_goal_on_the_next_line_is_still_found(self):
        self.assertEqual(sb.goal_of("ЦЕЛЬ\nстрока под заголовком"), "строка под заголовком")

    def test_no_goal_is_SAID_and_never_guessed(self):
        """Третий исход и здесь: цели нет → говорим словами, а не подставляем
        первую попавшуюся строку. «ПОЛОСА: пк» в графе «цель» врёт молча."""
        self.assertEqual(sb.goal_of("ПОЛОСА: пк\n\nЧТО СДЕЛАТЬ\n1. …"), sb.NOTICE_NO_GOAL)
        self.assertEqual(sb.goal_of(""), sb.NOTICE_NO_GOAL)
        self.assertEqual(sb.goal_of(None), sb.NOTICE_NO_GOAL)

    def test_a_retry_is_named_so_it_is_not_read_as_a_second_notice(self):
        """Дожим — четвёртый заход по СТАРОМУ ключу. Без пометки он приходит
        владельцу тем же словом «взято» и выглядит задвоением, которого нет."""
        blk = {"key": "kk1.d4", "body": GOOD_BODY, "lane": sb.LANE_PC, "lane_named": True,
               "retry": True, "of": "kk1", "attempt": 4}
        self.assertIn("дожим, попытка 4", sb.take_notice(blk, 707, TODAY))
        plain = {"key": "kk1", "body": GOOD_BODY, "lane": sb.LANE_PC, "lane_named": True}
        self.assertNotIn("дожим", sb.take_notice(plain, 707, TODAY))

    # ── дверь ────────────────────────────────────────────────────────────────

    # Двери с каскадом и/или клавиатурой. Список тот же, что стоял здесь с 05.09.
    CASCADE_DOORS = ("send_critical", "send_topic", "deliver", "awaits_reply", "send")
    # ЕДИНСТВЕННОЕ ИСКЛЮЧЕНИЕ, НАЗВАННОЕ ПОИМЁННО. Белый список против чёрного здесь
    # принципиален ровно так же, как у дверей чтения мозга: «всё, кроме этой функции»
    # ловит и ту ветку, которую завтра напишет забывший про инвариант.
    HOLD_DOOR = "notify_hold"

    def _door_names(self, inside=None, outside=None):
        """Имена вызовов и доводов модуля ящика, отобранные ПО ФУНКЦИИ. → (имена, доводы).

        ИМЯ ДВЕРИ СЧИТАЕТСЯ УПОМЯНУТЫМ И ТОГДА, КОГДА ЕЁ НЕ ЗОВУТ НА МЕСТЕ, а кладут
        в переменную и зовут через неё: одного обхода ВЫЗОВОВ мало —
        ``door = dispatch_notify.send_critical`` проехало бы мимо него молча.
        """
        tree = ast.parse(_src("shtab_box_run.py"))
        picked = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == (inside or outside):
                picked.update(id(n) for n in ast.walk(fn))
        named, kwargs = set(), set()
        for node in ast.walk(tree):
            # «Внутри названной функции» и «во всём модуле, КРОМЕ неё» — один обход и
            # один признак: два похожих обхода разъехались бы молча.
            if (inside and id(node) not in picked) or (outside and id(node) in picked):
                continue
            if isinstance(node, ast.Attribute):
                named.add(node.attr)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name:
                    named.add(name)
                for kw in (node.keywords or []):
                    if kw.arg:
                        kwargs.add(kw.arg)
        return named, kwargs

    def test_the_TAKE_notice_carries_no_buttons_and_never_goes_to_the_inbox(self):
        """ПРЯМОЙ ЗАПРЕТ ЗАДАНИЯ 05.09: «новых карточек не заводить… кнопок у него нет
        и ответа оно не ждёт». Судим по ДЕЙСТВИЮ — обходом AST на вызовы и на доводы,
        а не грепом: имена запрещённых дверей законно стоя́т в докстрингах, которые
        объясняют, почему их здесь нет, и грепающая проверка ловила бы собственное
        объяснение.

        ═══ ЗДЕСЬ СТОЯЛ ЗАПРЕТ НА ВЕСЬ МОДУЛЬ (сужен 06.09.2026) ═════════════════

        Прежняя проверка обходила ВЕСЬ ``shtab_box_run.py`` и запрещала каскадные
        двери где угодно в нём. Предмет у неё был ОДИН — извещение о ВЗЯТИИ, — а
        область на весь файл; 06.09 владелец потребовал для ДРУГОГО сообщения ровно
        обратного: сигнальная остановка обязана уходить КРИТИЧЕСКИМ каналом (1160
        первым, личка откатом) и С КНОПКОЙ, потому что она ЖДЁТ ОТВЕТА — им она и
        снимается. Признак адреса один и тот же («ждёт ли ответа», правило владельца
        10.08.2026), и по нему два сообщения обязаны ехать в РАЗНЫЕ двери.

        СУЖЕНИЕ — НЕ ОСЛАБЛЕНИЕ, и вот чем это доказано: запрет остался полным для
        ВСЕГО модуля, кроме одной поимённо названной функции :data:`HOLD_DOOR`;
        область покрытия включает и код вне функций, где «дверь в переменной» была бы
        так же опасна. Что происходит ВНУТРИ исключения, проверяет соседний тест —
        то есть незапрещённого места в модуле не осталось ни одного.
        """
        named, kwargs = self._door_names(outside=self.HOLD_DOOR)
        for door in self.CASCADE_DOORS:
            self.assertNotIn(door, named, "ящик открыл дверь с каскадом/кнопкой: %s" % door)
        self.assertIn("send_topic_strict", named, "дверь без каскада не названа вовсе")
        self.assertNotIn("reply_markup", kwargs, "извещению о взятии приделали клавиатуру")

    def test_the_STOP_notice_uses_the_critical_channel_and_only_that_one(self):
        """Обратный инвариант той же пары: у извещения об ОСТАНОВКЕ дверь ОДНА и
        каскадная. Проверяем не «что-то каскадное вызвано», а ИМЕННО ``send_critical``:
        1160 первым каналом, личка откатом — тот адрес, где владелец отвечает.

        ВТОРОЙ ДВЕРИ ВНУТРИ ИСКЛЮЧЕНИЯ БЫТЬ НЕ ДОЛЖНО: `deliver` сам выбирает адрес
        по признаку и в ЭТОМ месте был бы вторым мнением о том же (адрес здесь решён
        видом сообщения), а `send_topic_strict` увёз бы карточку с кнопкой в тему
        постановки, где владелец ответа не ждёт.
        """
        named, _kwargs = self._door_names(inside=self.HOLD_DOOR)
        self.assertIn("send_critical", named, "остановка поехала не критическим каналом")
        for door in ("send_topic", "deliver", "awaits_reply", "send_topic_strict"):
            self.assertNotIn(door, named, "у извещения об остановке завелась вторая дверь: %s"
                             % door)

    def test_the_topic_is_the_working_one_and_taken_from_dispatch_notify(self):
        """Тема постановки задач (328) живёт ОДНИМ значением в ``dispatch_notify``;
        второй её экземпляр здесь разошёлся бы с первым молча."""
        import dispatch_notify

        s = _Sender()
        ok, detail, why = run.notify_taken("извещение", sender=s)
        self.assertEqual((ok, why), (True, ""))
        self.assertEqual(detail, "9001")
        self.assertEqual([t for _t, t in s.sent], [dispatch_notify.TASKS_THREAD_ID])
        self.assertEqual(dispatch_notify.TASKS_THREAD_ID, 328)

    def test_an_empty_notice_is_not_sent_at_all(self):
        s = _Sender()
        ok, _detail, why = run.notify_taken("   ", sender=s)
        self.assertFalse(ok)
        self.assertEqual(s.sent, [])
        self.assertIn("пустой текст", why)

    def test_a_refused_door_returns_its_reason(self):
        """Отказ у двери без фолбэков обязан ВОЗВРАЩАТЬСЯ причиной: иначе «взяли и
        сказали» неотличимо от «взяли, а сказать не смогли»."""
        ok, _detail, why = run.notify_taken("текст", sender=_Sender(ok=False, why="бота нет в теме"))
        self.assertFalse(ok)
        self.assertIn("бота нет в теме", why)

    # ── виток ────────────────────────────────────────────────────────────────

    def test_taking_a_task_sends_exactly_one_notice(self):
        q, n = FakeQueue(), _Notifier()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True, notify_fn=n)
        self.assertEqual(len(rep["placed"]), 1)
        self.assertEqual(len(n.texts), 1, "одно взятие — одно извещение")
        self.assertEqual(len(rep["noticed"]), 1)
        self.assertEqual(rep["notice_failed"], [])
        self.assertIn("#%s" % rep["placed"][0]["id"], n.texts[0])
        self.assertIn(sb.NOTICE_BY_SHTAB, n.texts[0])

    def test_a_second_tick_over_the_same_key_says_nothing_twice(self):
        """Пункт 5: перезапуск демона не задваивает извещение — потому что не
        задваивает ВЗЯТИЕ. Корень тот же, долгая память та же, маркер ряда на
        месте: до строки извещения второй виток не доходит вовсе."""
        root = tempfile.mkdtemp(prefix="shtabnotice_")
        n = _Notifier()
        first = _tick(_box(("kk1", GOOD_BODY)), queue=FakeQueue(), place=True,
                      root=root, notify_fn=n)
        self.assertEqual(len(first["placed"]), 1)
        second = _tick(_box(("kk1", GOOD_BODY)), queue=FakeQueue(rows=[_row("kk1")]),
                       place=True, root=root, notify_fn=n)
        self.assertEqual(second["placed"], [])
        self.assertEqual(len(n.texts), 1, "второй виток сказал о том же взятии ещё раз")

    def test_a_task_sent_by_the_OWNER_makes_no_notice_at_all(self):
        """ПУНКТ 6, ОТРИЦАТЕЛЬНЫЙ ТЕСТ. Иначе владелец получит эхо собственного
        сообщения. Ящик крутится на живой очереди, полной работы владельца, — и
        молчит: цикл извещения идёт по документам ПАПКИ, а ряд владельца встаёт в
        очередь мимо ящика.
        """
        owner_rows = [{"id": 11, "task_text": "почини цену на месяц", "status": "new"},
                      {"id": 12, "task_text": "посмотри лог демона", "status": "in_progress"}]
        n = _Notifier()
        rep = _tick(_box(), queue=FakeQueue(rows=owner_rows, busy=True, busy_ids=[11, 12]),
                    place=True, notify_fn=n)
        self.assertEqual(n.texts, [], "извещение ушло на задание, которое ящик не брал")
        self.assertEqual(rep["noticed"], [])
        self.assertEqual(rep["notices"], [])

    def test_the_notice_is_born_in_exactly_ONE_place_in_the_whole_lane(self):
        """Отрицательный тест закрыт МЕСТОМ, а не условием: условие можно ошибочно
        вычислить, места ошибиться нельзя. Дорога владельца
        (``pc_orchestrator.enqueue_pc_task``) об извещении не знает ни строкой —
        значит породить его не может даже при сломанном признаке.
        """
        born = []
        for name in sorted(os.listdir(HERE)):
            if not name.endswith(".py") or name.startswith("test_") or name == "shtab_box.py":
                continue
            tree = ast.parse(_src(name))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "take_notice":
                    born.append(name)
        self.assertEqual(sorted(set(born)), ["shtab_box_run.py"],
                         "извещение рождается больше чем в одном месте: %s" % sorted(set(born)))

    def test_a_dry_run_shows_the_text_and_sends_nothing(self):
        """Запрет задания дословно: «проверка идёт на отключённой отправке либо
        разбором готового текста, а не живой посылкой»."""
        q, n = FakeQueue(), _Notifier()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=False, notify_fn=n)
        self.assertEqual(q.tasks, [])
        self.assertEqual(n.texts, [], "сухой ход послал живое сообщение")
        self.assertEqual(len(rep["notices"]), 1)
        self.assertTrue(rep["notices"][0]["dry"])
        self.assertIn(sb.NOTICE_BY_SHTAB, rep["notices"][0]["text"])
        self.assertIn("НЕ ОТПРАВЛЕНО", run._render(report=rep))

    def test_a_failed_delivery_neither_drops_the_row_nor_goes_silent(self):
        """Ряд уже стои́т, и молчание Telegram — не повод бросать очередь. Но «взяли,
        а сказать не смогли» обязано быть СЛЫШНО строкой исхода."""
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True,
                    notify_fn=_Notifier(ok=False, why="бота нет в теме"))
        self.assertEqual(len(q.tasks), 1, "неушедшее извещение уронило постановку ряда")
        self.assertEqual(len(rep["placed"]), 1)
        self.assertEqual(rep["noticed"], [])
        self.assertEqual(len(rep["notice_failed"]), 1)
        self.assertIn("ИЗВЕЩЕНИЕ НЕ УШЛО", rep["line"])
        self.assertIn("бота нет в теме", rep["line"])

    def test_an_exploding_door_does_not_take_the_tick_down(self):
        """Дверь наружу роняет виток ящика ни одной веткой: очередь дороже новости."""
        q = FakeQueue()
        rep = _tick(_box(("kk1", GOOD_BODY)), queue=q, place=True,
                    notify_fn=_Notifier(boom="сокет закрыт"))
        self.assertEqual(len(rep["placed"]), 1)
        self.assertEqual(len(rep["notice_failed"]), 1)
        self.assertIn("сокет закрыт", rep["notice_failed"][0]["why"])


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
