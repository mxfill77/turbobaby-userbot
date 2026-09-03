"""Регресс ступени G — заявка доходит до владельца и отвечается одним тапом.

* ``TestPurity`` — инвариант ``ZAYAVKI_PC_PURE``: чистый слой решения не смеет
  завести часы, сеть, диск или ``getenv``. Обход AST.
* ``TestMarkersMatchStageB`` — литералы разбора СВЕРЯЮТСЯ С ОРИГИНАЛОМ ступени B.
  Разойдись они молча — ступень вернула бы «не заявка» на живой заявке и
  перестала бы доносить, ничего не сломав видимо.
* ``TestDigestOnLiveShape`` — голдены сняты с текста, собранного НАСТОЯЩИМ
  ``review_intake.claim_text``, а не с идеализированной схемы (правило-класс
  полосы: формат мока = формат источника).
* ``TestNoForeignText`` — прямой запрет задания: чужой текст канала не уезжает
  владельцу ни строкой.
* ``TestConsequences`` / ``TestPlan`` — «последствие не видно → не шлём», сутки
  между напоминаниями, отсутствие повторной полной отправки.
* ``TestNotATask`` — ОТРИЦАТЕЛЬНЫЕ ПРОБЫ: ни «да», ни «нет» не рождают задачи;
  `enqueue` не зовётся ни одной веткой.
* ``TestButtonContract`` — сквозной голден: `callback_data`, который печатает
  ступень, разбирается РЕАЛЬНЫМ регекспом `pc_agent`. Кнопка, которую некому
  разобрать, — это тишина в ответ на тап, и класс этот на полосе уже ловили.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import tempfile
import unittest

import review_intake as ri
import zayavki_pc as z
import zayavki_pc_run as run

HERE = os.path.dirname(os.path.abspath(__file__))

# Живая заявка полосы: ключи и вид сняты со слепка очереди 02.09.2026 (ряды #1–#3).
LIVE_KEY = "47b3131c052c"
LIVE_CLAIM = {
    "schema": ri.SCHEMA,
    "key": LIVE_KEY,
    "kind": "ПЕРЕУСЛОЖНЕНО",
    "quote": "конвейер внешнего ревью переусложнён: два канала, упаковка, лоток",
    "sources": [{
        "channel": "codex", "send_date": "2026-09-01", "pack": "chain-pc-2026-09-01-72",
        # `answer` несёт УЖЕ относительный путь — так его пишет живая ступень B
        # (замер 02.09). Мок с голым именем файла спрятал бы склейку каталога дважды.
        "pack_sha256": "0123456789abcdef" * 4,
        "answer": "docs/review_inbox/2026-09-01-2026-09-01-chain-pc-2026-09-01-72-codex.md",
        "quote": "конвейер внешнего ревью переусложнён: два канала, упаковка, лоток",
        "kind": "ПЕРЕУСЛОЖНЕНО",
    }],
    "channels": ["codex"],
    "packs": ["chain-pc-2026-09-01-72"],
}
LIVE_PREMISE = {"outcome": ri.PREMISE_ALIVE,
                "why": "якорь найден в review_intake_run.py по названному адресу"}


def live_row(tid=3, status="needs_approval", claim=None, premise=None):
    """Ряд очереди с телом, собранным НАСТОЯЩЕЙ ступенью B."""
    text = ri.claim_text(claim or LIVE_CLAIM, premise or LIVE_PREMISE, "2026-09-02")
    return {"id": tid, "lane": "pc", "status": status, "task_text": text}


# ───────────────────────── голдены отбора (03.09.2026) ─────────────────────────
# ПРАВИЛО ПОЛОСЫ: голдены детекта — ДОСЛОВНЫЕ фразы живого провала, а не
# придуманные под детектор. Эта снята с разбора языкового провала EN-набора
# (цепь #17, 02.09): англоязычный клиент получает русское приветствие. Ровно та
# тема, которую Штаб назвал единственным клиентским дефектом ночи.
DEFECT_KEY = "d1efec70c11e"
DEFECT_QUOTE = (
    "2. НЕ ДЕЛАТЬ ВОВСЕ: ветка первого контакта в `suggest.py` не проверяет язык "
    "клиента — англоязычный клиент получает русское приветствие, потому что правило "
    "первого контакта конфликтует с английской инструкцией."
)
DEFECT_CLAIM = {
    "schema": ri.SCHEMA,
    "key": DEFECT_KEY,
    "kind": "НЕ ДЕЛАТЬ ВОВСЕ",
    "quote": DEFECT_QUOTE,
    "sources": [{
        "channel": "codex", "send_date": "2026-09-02", "pack": "chain-pc-2026-09-02-17",
        "pack_sha256": "fedcba9876543210" * 4,
        "answer": "docs/review_inbox/2026-09-02-2026-09-02-chain-pc-2026-09-02-17-codex.md",
        "quote": DEFECT_QUOTE, "kind": "НЕ ДЕЛАТЬ ВОВСЕ",
    }],
    "channels": ["codex"],
    "packs": ["chain-pc-2026-09-02-17"],
}
# Посылка ЖИВА и адрес НАЗВАН — оба условия отбора, и оба берутся из настоящего
# рендера ступени B, а не подставляются в digest руками.
#
# АДРЕС НЕСЁТ НОМЕР СТРОКИ, И ЭТО ПОПРАВКА 03.09.2026 ПО ЖИВОМУ ПРОВАЛУ. Прежний
# голден писал голое `suggest.py`, а живая ступень B пишет `%s:%d`
# (`review_intake_run.py:263`) — и на этом расхождении отбор был СЛЕП: замер прямой
# пробой дал `is_client("model_name.py") = True` при `is_client("model_name.py:30")
# = False`, то есть условие 2 не срабатывало НИ НА ОДНОМ адресе, найденном в коде.
# Живой ряд #32 (`227d09eb0b52`, тот самый, который артефакт 03.09 назвал
# клиентским дефектом) из-за этого уезжал в сводку. Голден переведён на живой
# формат — тот самый урок `CLAUDE.md` про мок, разошедшийся с продом.
DEFECT_PREMISE = {"outcome": ri.PREMISE_ALIVE,
                  "why": "`RULE-1` найден по адресу suggest.py:30"}


def defect_row(tid=42, status="needs_approval", premise=None):
    """Ряд-заявка, несущая КЛИЕНТСКИЙ ДЕФЕКТ (единственный вид, который карточится)."""
    text = ri.claim_text(DEFECT_CLAIM, premise or DEFECT_PREMISE, "2026-09-02")
    return {"id": tid, "lane": "pc", "status": status, "task_text": text}


class TestPurity(unittest.TestCase):
    """ZAYAVKI_PC_PURE — чистая логика остаётся чистой."""

    BANNED_CALLS = {"now", "utcnow", "time", "monotonic", "getenv", "open", "run", "Popen", "urlopen"}
    BANNED_IMPORTS = {"os", "subprocess", "socket", "urllib", "time", "shutil", "requests"}

    def test_no_clock_no_disk_no_network_no_env(self):
        with io.open(os.path.join(HERE, "zayavki_pc.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="zayavki_pc.py")
        bad_imports, bad_calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad_imports |= {a.name.split(".")[0] for a in node.names} & self.BANNED_IMPORTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in self.BANNED_IMPORTS:
                    bad_imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name in self.BANNED_CALLS:
                    bad_calls.add(name)
        self.assertFalse(bad_imports, "чистый слой завёл запрещённый импорт: %s" % bad_imports)
        self.assertFalse(bad_calls, "чистый слой завёл запрещённый вызов: %s" % bad_calls)


class TestMarkersMatchStageB(unittest.TestCase):
    """Литералы разбора обязаны совпадать с оригиналом ступени B — иначе тихая слепота."""

    def test_claim_mark_is_the_same_literal(self):
        self.assertEqual(z.CLAIM_MARK, ri.CLAIM_MARK)

    def test_quote_head_is_the_same_literal(self):
        # Граница чужого текста списана с `review_intake.claim_text`; проверяем не по
        # памяти, а по строке, которую он РЕАЛЬНО печатает.
        text = ri.claim_text(LIVE_CLAIM, LIVE_PREMISE, "2026-09-02")
        self.assertIn(z.QUOTE_HEAD, text.splitlines())

    def test_real_claim_text_is_recognized(self):
        self.assertTrue(z.is_zayavka(live_row()["task_text"]))

    def test_guard_card_is_not_a_zayavka(self):
        self.assertFalse(z.is_zayavka("🔴 гард: удаление файла tmp/x — разрешить?"))
        self.assertFalse(z.is_zayavka(""))
        self.assertFalse(z.is_zayavka(None))


class TestSplit(unittest.TestCase):
    """Пункт 5: карточки гарда и заявки считаются ВРОЗЬ."""

    def test_split_names_two_things_apart(self):
        rows = [live_row(1), live_row(2),
                {"id": 9, "task_text": "🔴 гард: red-операция ждёт «да»"}]
        got = z.split_awaiting(rows)
        self.assertEqual([r["id"] for r in got["zayavki"]], [1, 2])
        self.assertEqual([r["id"] for r in got["cards"]], [9])

    def test_snapshot_goal_is_enough(self):
        # Слепок очереди хранит только ПЕРВУЮ строку тела под именем `goal`; маркер
        # стои́т первым и обрезку переживает — иначе витрина слепла бы на слепке.
        got = z.split_awaiting([{"id": 3, "goal": "[заявка-ревью дата=2026-09-02 ключ=%s]" % LIVE_KEY}])
        self.assertEqual(len(got["zayavki"]), 1)
        self.assertEqual(len(got["cards"]), 0)


class TestDigestOnLiveShape(unittest.TestCase):
    """Разбор стои́т на тексте, собранном настоящей ступенью B."""

    def setUp(self):
        self.got = z.digest(live_row())

    def test_fields_are_read_not_guessed(self):
        self.assertEqual(self.got["key"], LIVE_KEY)
        self.assertEqual(self.got["date"], "2026-09-02")
        self.assertEqual(self.got["kind"], "ПЕРЕУСЛОЖНЕНО")
        self.assertEqual(self.got["premise"], ri.PREMISE_TITLE[ri.PREMISE_ALIVE])
        self.assertIn("названному адресу", self.got["premise_why"])
        self.assertEqual(z.channels(self.got), ["codex"])
        self.assertEqual(z.packs(self.got), ["chain-pc-2026-09-01-72"])

    def test_broken_body_says_unknown_and_does_not_raise(self):
        got = z.digest({"id": 5, "task_text": "[заявка-ревью дата=2026-09-02 ключ=%s]\nмусор" % LIVE_KEY})
        self.assertEqual(got["kind"], z.UNKNOWN)
        self.assertEqual(got["premise"], z.UNKNOWN)
        self.assertEqual(got["sources"], [])


class TestNoForeignText(unittest.TestCase):
    """ПРЯМОЙ ЗАПРЕТ ЗАДАНИЯ: чужой текст канала не едет владельцу ни строкой."""

    QUOTE = "конвейер внешнего ревью переусложнён: два канала, упаковка, лоток"

    def test_digest_stops_at_the_quote_border(self):
        got = z.digest(live_row())
        blob = json.dumps(got, ensure_ascii=False)
        self.assertNotIn(self.QUOTE, blob)
        self.assertNotIn(z.QUOTE_HEAD, blob)

    def test_message_carries_pointer_not_body(self):
        text = z.message(z.digest(live_row()))
        self.assertNotIn(self.QUOTE, text)
        self.assertNotIn(z.QUOTE_HEAD, text)
        # ФАЙЛ ЛОТКА, а не пакет: пакета в `docs/review_inbox` нет, и указывать на
        # его имя значило послать владельца туда, где файла не окажется.
        self.assertIn("docs/review_inbox/2026-09-01-2026-09-01-chain-pc-2026-09-01-72-codex.md",
                      text)
        self.assertNotIn("review_inbox/docs/", text)   # каталог не приклеен дважды

    def test_message_says_both_consequences_and_that_it_is_not_a_task(self):
        text = z.message(z.digest(live_row()))
        self.assertIn("НЕ задача", text)
        self.assertIn("✅", text)
        self.assertIn("❌", text)
        self.assertIn("Задача НЕ ставится", text)
        self.assertIn("НИКОГДА", text)               # молчание — не согласие
        self.assertLessEqual(len(text), z.MSG_MAX)

    def test_live_three_source_claim_fits_whole(self):
        """Живая заявка #2 — три источника, длинные имена файлов лотка — влезает ЦЕЛИКОМ.

        Класс пойман живой пробой 02.09: при потолке 900 её текст обрывался на
        «Не ответишь — за», то есть первой под нож уходила ЕДИНСТВЕННАЯ строка про
        то, что молчание не согласие, и половина второй кнопки вместе с ней.
        Ножницы на конце всегда режут хвост, а хвост здесь — смысл.
        """
        srcs = []
        for chan, num in (("codex", 101), ("manus", 99), ("codex", 105)):
            srcs.append(dict(LIVE_CLAIM["sources"][0], channel=chan,
                             pack="2026-09-01-chain-pc-2026-09-01-%d.md" % num,
                             answer="docs/review_inbox/2026-09-01-2026-09-01-chain-pc-"
                                    "2026-09-01-%d-%s.md" % (num, chan)))
        claim = dict(LIVE_CLAIM, key="98c2c8bf1be6", kind="НЕ ДЕЛАТЬ ВОВСЕ", sources=srcs)
        text = z.message(z.digest(live_row(2, claim=claim)))
        self.assertLess(len(text), z.MSG_MAX, "текст упёрся в потолок — хвост срежется")
        self.assertTrue(text.rstrip().endswith("НИКОГДА."), text[-60:])
        self.assertIn("❌ Отклонить", text)
        self.assertIn(z.INBOX_DIR, text)

    def test_outbound_door_refuses_text_with_the_quote_border(self):
        # Второй замок — уже в руках: строка-граница в исходящем означает, что разбор
        # пропустил чужое, и дверь обязана закрыться, а не «почистить и отправить».
        sent = []
        channel, ok, why = run.outbound("шапка\n%s\nчужое" % z.QUOTE_HEAD, None,
                                        sender=lambda t, m: sent.append(t) or ("inbox", True))
        self.assertFalse(ok)
        self.assertEqual(sent, [])
        self.assertIn("чуж", why)


class TestConsequences(unittest.TestCase):
    """«Из чего не видно последствия ответа — не отправлять вовсе, а сказать почему»."""

    def test_visible_for_a_live_claim(self):
        ok, why = z.consequences(z.digest(live_row()))
        self.assertTrue(ok, why)

    def test_no_row_number_is_refused(self):
        ok, why = z.consequences(z.digest(live_row(tid=None)))
        self.assertFalse(ok)
        self.assertIn("номера", why)

    def test_already_closed_row_is_refused(self):
        ok, why = z.consequences(z.digest(live_row(status="done")))
        self.assertFalse(ok)
        self.assertIn("не ждёт решения", why)

    def test_nothing_to_decide_is_refused(self):
        ok, why = z.consequences(z.digest(
            {"id": 5, "status": "needs_approval",
             "task_text": "[заявка-ревью дата=2026-09-02 ключ=%s]\nмусор" % LIVE_KEY}))
        self.assertFalse(ok)
        self.assertIn("ни вид", why)

    def test_one_field_is_enough(self):
        # Вид есть, премисы нет — решать всё ещё можно, просто с меньшей опорой.
        ok, _ = z.consequences({"id": 5, "status": "needs_approval",
                                "kind": "УПРОЩАЕМО", "premise": z.UNKNOWN})
        self.assertTrue(ok)


class TestPlan(unittest.TestCase):
    """Молчание не согласие; напоминание не чаще раза в сутки; повторной отправки нет."""

    NOW = 1788400000.0

    def test_first_time_is_sent(self):
        steps = z.plan([z.digest(live_row())], {"sent": {}}, self.NOW)
        self.assertEqual([s["action"] for s in steps], ["send"])

    def test_same_zayavka_is_never_sent_twice(self):
        state = {"sent": {LIVE_KEY: {"last_at": self.NOW - 60}}}
        steps = z.plan([z.digest(live_row())], state, self.NOW)
        self.assertEqual(steps[0]["action"], "skip")
        self.assertNotIn("send", [s["action"] for s in steps])

    def test_reminder_only_after_a_full_day(self):
        just_under = {"sent": {LIVE_KEY: {"last_at": self.NOW - z.REMIND_MIN_SEC + 1}}}
        just_over = {"sent": {LIVE_KEY: {"last_at": self.NOW - z.REMIND_MIN_SEC}}}
        self.assertEqual(z.plan([z.digest(live_row())], just_under, self.NOW)[0]["action"], "skip")
        self.assertEqual(z.plan([z.digest(live_row())], just_over, self.NOW)[0]["action"], "remind")

    def test_broken_stamp_is_silence_not_a_second_card(self):
        state = {"sent": {LIVE_KEY: {"last_at": None}}}
        step = z.plan([z.digest(live_row())], state, self.NOW)[0]
        self.assertEqual(step["action"], "skip")
        self.assertIn("не доказать", step["why"])

    def test_hold_beats_send(self):
        step = z.plan([z.digest(live_row(status="done"))], {"sent": {}}, self.NOW)[0]
        self.assertEqual(step["action"], "hold")

    def test_reminder_is_one_line(self):
        line = z.reminder([z.digest(live_row(1)), z.digest(live_row(3))])
        self.assertEqual(len(line.splitlines()), 1)
        self.assertIn("#1", line)
        self.assertIn("#3", line)


class _FakeQueue:
    """Очередь-протокол: считает ВСЕ обращения, чтобы отсутствие `enqueue` было доказуемо."""

    def __init__(self, rows=None, ok=True, why="", close_ok=True):
        self._rows = rows if rows is not None else [live_row(1), live_row(2), live_row(3)]
        self._ok, self._why = ok, why
        self._close_ok = close_ok
        self.calls = []
        self.closed_texts = []

    def awaiting(self):
        self.calls.append(("awaiting",))
        return (self._rows if self._ok else []), self._ok, self._why

    def accept(self, tid):
        self.calls.append(("accept", str(tid)))
        return True, ""

    def reject(self, tid):
        self.calls.append(("reject", str(tid)))
        return True, ""

    def close_by_sift(self, tid, text):
        self.calls.append(("close_by_sift", str(tid)))
        self.closed_texts.append(text)
        return self._close_ok, ("" if self._close_ok else "мост отказал")

    def enqueue(self, *a, **k):                     # ловушка: не должна звонить НИКОГДА
        self.calls.append(("enqueue",))
        raise AssertionError("ступень G поставила задачу — запрет задания нарушен")


class TestNotATask(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЕ ПРОБЫ: ни один ответ не рождает задачи."""

    def test_yes_only_accepts(self):
        q = _FakeQueue()
        ok, words = run.answer(3, True, queue=q)
        self.assertTrue(ok)
        self.assertEqual(q.calls, [("accept", "3")])
        self.assertIn("принята к сведению", words)
        self.assertIn("НЕ стала", words)

    def test_no_only_closes(self):
        q = _FakeQueue()
        ok, words = run.answer(3, False, queue=q)
        self.assertTrue(ok)
        self.assertEqual(q.calls, [("reject", "3")])
        self.assertIn("отклонена", words)
        self.assertIn("не породила", words)

    def test_module_never_enqueues(self):
        # Замок устройством, а не обещанием: слова `enqueue`/`enqueue_pc_task` в коде
        # ступени не встречаются вовсе.
        for name in ("zayavki_pc.py", "zayavki_pc_run.py"):
            with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
                src = fh.read()
            self.assertNotIn("enqueue_pc_task", src, name)
            self.assertNotIn(".enqueue_task(", src, name)


class TestTick(unittest.TestCase):
    """Оборот: что уходит, что придерживается, что пишется в реестр."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="zayavki_")
        self.state = os.path.join(self.dir, "state.json")
        self.sent = []

    def _sender(self, text, markup=None):
        self.sent.append((text, markup))
        return ("inbox", True)

    # ВНИМАНИЕ НА ФИКСТУРУ (правка 03.09.2026). До отбора эти три пробы гоняли
    # `live_row` — обычную находку про наш конвейер. С 03.09 карточку получает
    # ТОЛЬКО клиентский дефект, и на прежней фикстуре пробы мерили бы уже не
    # «доставка работает», а «отбор не пропустил», то есть молча сменили бы
    # предмет. Механику доставки меряем на `defect_row` — единственном ряде,
    # который до доставки вообще доезжает; что прочее уходит в сводку, проверяет
    # `TestOtborCardOrDigest`.
    def test_client_defect_is_delivered_once(self):
        q = _FakeQueue(rows=[defect_row(42)])
        rep = run.tick(root=self.dir, state_path=self.state, send=True,
                       clock=lambda: 1788400000.0, queue=q, sender=self._sender)
        self.assertEqual(rep["zayavki"], 1)
        self.assertEqual(len(rep["sent"]), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("inline_keyboard", self.sent[0][1])  # ответ в ОДИН тап
        again = run.tick(root=self.dir, state_path=self.state, send=True,
                         clock=lambda: 1788400060.0, queue=_FakeQueue(rows=[defect_row(42)]),
                         sender=self._sender)
        self.assertEqual(len(again["sent"]), 0)        # повторной отправки НЕТ
        self.assertEqual(len(self.sent), 1)

    def test_reminder_after_a_day_is_a_single_message(self):
        rows = [defect_row(42), defect_row(43)]
        run.tick(root=self.dir, state_path=self.state, send=True, clock=lambda: 1788400000.0,
                 queue=_FakeQueue(rows=rows), sender=self._sender)
        self.sent.clear()
        rep = run.tick(root=self.dir, state_path=self.state, send=True,
                       clock=lambda: 1788400000.0 + z.REMIND_MIN_SEC,
                       queue=_FakeQueue(rows=rows), sender=self._sender)
        self.assertTrue(rep["reminded"])
        self.assertEqual(len(self.sent), 1)            # ОДНА строка на всех, а не карточки
        self.assertIsNone(self.sent[0][1])             # у напоминания кнопок нет

    def test_claim_without_visible_consequence_is_held_with_a_reason(self):
        # Ряд уже НЕ ждёт решения — и это придержка ПОСЛЕ отбора: заявка обязана
        # быть клиентским дефектом, иначе до `consequences` дело не дойдёт вовсе.
        q = _FakeQueue(rows=[defect_row(42, status="done")])
        rep = run.tick(root=self.dir, state_path=self.state, send=True,
                       clock=lambda: 1788400000.0, queue=q, sender=self._sender)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(rep["held"]), 1)
        self.assertIn("не ждёт решения", rep["held"][0]["why"])
        self.assertIn("НЕ отправлена", rep["line"])

    def test_bridge_silence_sends_nothing(self):
        q = _FakeQueue(ok=False, why="мост не ответил")
        rep = run.tick(root=self.dir, state_path=self.state, send=True,
                       clock=lambda: 1788400000.0, queue=q, sender=self._sender)
        self.assertEqual(self.sent, [])
        self.assertIn("очередь недоступна", rep["why"])
        self.assertFalse(os.path.exists(self.state))   # несделанного в реестр не пишем

    def test_dry_run_touches_nothing(self):
        rep = run.tick(root=self.dir, state_path=self.state, send=False,
                       clock=lambda: 1788400000.0, queue=_FakeQueue(rows=[defect_row(42)]),
                       sender=self._sender)
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(len(rep["sent"]), 1)          # собрано, но не отправлено


class TestOtborCardOrDigest(unittest.TestCase):
    """ОТБОР 03.09.2026: карточкой — только клиентский дефект, прочее — строкой сводки.

    Четыре отрицательные пробы задания стоят здесь вместе с голденом непорочности:
    без последнего «ноль карточек» ничего не доказывает — ровно так первая версия
    фильтра и была вырожденной (вето по цене срабатывало на ярлыке вида, который
    есть в КАЖДОЙ находке, и 0 из 122 читался как «дефектов нет»).
    """

    def _digest(self, row):
        return z.digest(row)

    def _sig(self, row):
        return z.defect_signals(row.get("task_text"))

    # ── голден непорочности: фильтр УМЕЕТ сказать «да» ────────────────────
    def test_filter_not_degenerate(self):
        row = defect_row(42)
        got, sig = self._digest(row), self._sig(row)
        self.assertTrue(sig["client"], "живая фраза дефекта не дала слов поверхности")
        self.assertTrue(sig["defect"], "живая фраза дефекта не дала слов дефекта")
        self.assertFalse(sig["cost"], "ярлык вида «НЕ ДЕЛАТЬ ВОВСЕ» снова считается ценой — "
                                      "фильтр выродился, как 03.09 до правки")
        verdict = z.client_defect(got, sig, ["suggest.py"])
        self.assertTrue(verdict["is"], verdict["why"])
        steps = z.route([got], {DEFECT_KEY: sig}, {DEFECT_KEY: ["suggest.py"]}, sent_today=0)
        self.assertEqual(steps[0]["action"], "card")

    def test_kind_label_alone_is_not_a_cost_claim(self):
        # Прямая защита от вырождения: голая шапка вида НЕ обязана давать слов цены.
        for label in ("УПРОЩАЕМО", "НЕ ДЕЛАТЬ ВОВСЕ", "ПЕРЕУСЛОЖНЕНО"):
            self.assertFalse(z.defect_signals("1. %s: " % label)["cost"], label)

    # ── 1. находка БЕЗ АДРЕСА карточкой не идёт ──────────────────────────
    def test_finding_without_an_address_gets_no_card(self):
        # Адреса нет → ступень B честно ставит премису НЕИЗВЕСТНО (её собственная
        # ветка «адрес не назван»), и отбор обязан отказать НА ПЕРВОМ условии.
        nameless = ri.premise([])
        self.assertEqual(nameless["outcome"], ri.PREMISE_UNKNOWN)
        row = defect_row(42, premise=nameless)
        got, sig = self._digest(row), self._sig(row)
        verdict = z.client_defect(got, sig, ["suggest.py"])
        self.assertFalse(verdict["is"])
        self.assertIn("посылка не жива", verdict["why"])
        steps = z.route([got], {DEFECT_KEY: sig}, {DEFECT_KEY: ["suggest.py"]}, sent_today=0)
        self.assertEqual(steps[0]["action"], "digest")

    # ── 2. находка с ПРОТУХШЕЙ посылкой карточкой не идёт ────────────────
    def test_stale_premise_gets_no_card(self):
        stale = ri.premise([{"anchor": "RULE-1", "address": "", "found": False}])
        self.assertEqual(stale["outcome"], ri.PREMISE_STALE)
        row = defect_row(42, premise=stale)
        got, sig = self._digest(row), self._sig(row)
        verdict = z.client_defect(got, sig, ["suggest.py"])
        self.assertFalse(verdict["is"])
        self.assertIn("посылка не жива", verdict["why"])
        self.assertEqual(z.route([got], {DEFECT_KEY: sig},
                                 {DEFECT_KEY: ["suggest.py"]})[0]["action"], "digest")

    # ── 3. клиентский дефект едет СВЕРХ ПОТОЛКА ──────────────────────────
    def test_client_defect_passes_even_over_the_cap(self):
        row = defect_row(42)
        got, sig = self._digest(row), self._sig(row)
        steps = z.route([got], {DEFECT_KEY: sig}, {DEFECT_KEY: ["suggest.py"]},
                        sent_today=z.CARD_CAP)          # потолок исчерпан ПОЛНОСТЬЮ
        self.assertEqual(steps[0]["action"], "card_over")
        self.assertIn("потолок", steps[0]["why"])
        text = z.message(got, defect_why=steps[0]["why"], over_cap=True)
        self.assertIn(z.OVER_CAP_MARK, text)
        self.assertTrue(text.startswith(z.OVER_CAP_MARK), "пометка обязана идти ПЕРВОЙ строкой")
        # и в сводке это ОТЛОЖЕНО ПОТОЛКОМ, а не отброшено
        self.assertIn(z.OVER_CAP_MARK, z.summary(steps))

    def test_cap_never_turns_a_finding_into_silence(self):
        # Сколько бы ни было сверх потолка — на каждую находку остаётся след.
        rows = [defect_row(40 + i) for i in range(5)]
        items = [self._digest(r) for r in rows]
        sig = {DEFECT_KEY: self._sig(rows[0])}
        steps = z.route(items, sig, {DEFECT_KEY: ["suggest.py"]}, sent_today=0)
        self.assertEqual(len(steps), 5)
        self.assertTrue(all(s["action"] in ("card", "card_over") for s in steps))
        self.assertEqual(sum(1 for s in steps if s["action"] == "card"), z.CARD_CAP)

    # ── 4. лоток недоступен → НЕИЗВЕСТНО, а не ноль ──────────────────────
    def test_unreadable_inbox_says_unknown_not_zero(self):
        text = z.summary([], inbox_ok=False, inbox_why="мост не ответил")
        self.assertIn("НЕИЗВЕСТНО", text)
        self.assertIn("мост не ответил", text)
        self.assertNotIn("пришло 0", text)
        self.assertIn("отсутствием находок не является", text)

    def test_tick_reports_unknown_when_the_queue_is_silent(self):
        d = tempfile.mkdtemp(prefix="zayavki_unk_")
        rep = run.tick(root=d, state_path=os.path.join(d, "s.json"), send=True,
                       clock=lambda: 1788400000.0,
                       queue=_FakeQueue(ok=False, why="мост не ответил"), sender=lambda *a: None)
        self.assertIn("НЕИЗВЕСТНО", rep["summary"])

    # ── обычная находка (не про клиента) уходит строкой в сводку ─────────
    def test_ordinary_finding_goes_to_the_digest_line(self):
        row = live_row(3)
        got, sig = self._digest(row), self._sig(row)
        steps = z.route([got], {LIVE_KEY: sig}, {LIVE_KEY: []}, sent_today=0)
        self.assertEqual(steps[0]["action"], "digest")
        text = z.summary(steps, found=1)
        self.assertIn("пришло 1", text)
        self.assertIn("карточкой отобрано 0", text)
        self.assertIn(z.INBOX_DIR, text)               # указатель на лоток обязателен

    def test_digest_carries_no_foreign_text(self):
        # Прямой запрет задания: чужой текст канала в сводку телом не переносится.
        row = live_row(3)
        got = self._digest(row)
        steps = z.route([got], {LIVE_KEY: self._sig(row)}, {LIVE_KEY: []})
        text = z.summary(steps, found=1)
        self.assertNotIn(LIVE_CLAIM["quote"], text)
        self.assertNotIn(z.QUOTE_HEAD, text)

    def test_signals_return_only_our_own_vocabulary(self):
        # Устройством, а не аккуратностью: наружу уходят слова ИЗ НАШИХ СПИСКОВ.
        sig = z.defect_signals(defect_row(42)["task_text"])
        for bucket, words in (("client", z.CLIENT_WORDS), ("defect", z.DEFECT_WORDS),
                              ("cost", z.COST_WORDS)):
            for word in sig[bucket]:
                self.assertIn(word, words, "в возврате слово не из нашего списка: %r" % word)

    def test_cap_is_below_stage_b_daily_budget(self):
        # Поставить заявку и ПЕРЕБИТЬ ею работу владельца — разные по цене действия.
        import review_intake_run
        self.assertLess(z.CARD_CAP, review_intake_run.DEFAULT_BUDGET)


class TestButtonContract(unittest.TestCase):
    """Кнопку обязан разобрать ЖИВОЙ pc_agent, а не «такой же» регексп из головы.

    Класс пойман на этой полосе живьём (инциденты 14.07 23:37 и 15.07 10:59): тап по
    кнопке молчал, потому что разбирать её было некому. Голден сквозной: печатает
    ступень — разбирает `pc_agent`.
    """

    def _agent_re(self):
        with io.open(os.path.join(HERE, "pc_agent.py"), encoding="utf-8") as fh:
            src = fh.read()
        hit = re.search(r'ZAYAVKA_CB_RE\s*=\s*re\.compile\(r"([^"]+)"\)', src)
        self.assertIsNotNone(hit, "в pc_agent.py нет ZAYAVKA_CB_RE — кнопку некому разобрать")
        return re.compile(hit.group(1))

    def test_both_buttons_parse_in_pc_agent(self):
        rx = self._agent_re()
        row = z.buttons(3)["inline_keyboard"][0]
        self.assertEqual(len(row), 2)
        for cell in row:
            m = rx.match(cell["callback_data"])
            self.assertIsNotNone(m, cell["callback_data"])
            self.assertEqual(m.group(2), "3")
        self.assertEqual([c["callback_data"] for c in row],
                         ["zayavka:yes:3", "zayavka:no:3"])

    def test_chain_buttons_are_not_zayavka(self):
        rx = self._agent_re()
        self.assertIsNone(rx.match("chain:stop:3"))
        self.assertIsNone(rx.match("zayavka:maybe:3"))
        self.assertIsNone(rx.match("zayavka:yes:abc"))


# ═══════════ СУДЬБА РЯДА БЕЗ КАРТОЧКИ — ОТРИЦАТЕЛЬНЫЕ ТЕСТЫ (03.09.2026) ═══════════


class TestPremiseAddressWithLineNumber(unittest.TestCase):
    """РЕГРЕСС НАЙДЕННОЙ СЛЕПОТЫ: адрес живой премисы несёт номер строки.

    Без нормализации `client_contour` не узнаёт НИ ОДИН адрес, найденный в коде, —
    условие 2 отбора становится недостижимым, и клиентский дефект уезжает в сводку.
    Замер 03.09 живым рядом #32. Тест меряет ОБЕ формы одним корпусом: разойдись
    они снова — красное здесь, а не молчаливая слепота в проде.
    """

    def test_line_suffix_is_stripped_only_for_the_graph_question(self):
        self.assertEqual(run._file_of("model_name.py:30"), "model_name.py")
        self.assertEqual(run._file_of("suggest.py"), "suggest.py")
        # каталоги и точки не трогаем, режется ТОЛЬКО хвост «:цифры»
        self.assertEqual(run._file_of("docs/review_inbox/a.md:12"), "docs/review_inbox/a.md")
        self.assertEqual(run._file_of("model_name.py:30:7"), "model_name.py:30")

    def test_address_is_returned_verbatim_with_its_line(self):
        # Наружу адрес идёт ДОСЛОВНО: без номера строки владелец не найдёт место.
        got = z.digest(defect_row(42))
        self.assertEqual(run.premise_addresses(got), ["suggest.py:30"])

    def test_the_graph_sees_a_live_address_that_carries_a_line(self):
        try:
            import client_contour
            cl = client_contour.closure(HERE)
        except Exception as exc:                       # pragma: no cover
            self.skipTest("client_contour недоступен: %r" % exc)
        if not getattr(cl, "ok", False):
            self.skipTest("граф контура не построился")
        got = z.digest(defect_row(42))
        hits, determined = run.client_files_of(got, repo=HERE, closure=cl)
        self.assertTrue(determined)
        self.assertEqual(hits, ["suggest.py:30"],
                         "адрес с номером строки снова невидим графу — отбор ослеп")


class TestSiftClosesRowsWithoutACard(unittest.TestCase):
    """Ряд, которому отбор не дал карточку, НЕ ВИСИТ «ждёт владельца»."""

    def _digest(self, row):
        return z.digest(row)

    def _sig(self, row):
        return z.defect_signals(row["task_text"])

    def _routed(self, row, key, files):
        return z.route([self._digest(row)], {key: self._sig(row)}, {key: files})[0]

    # ── ряд БЕЗ карточки закрывается, и автор решения ВИДЕН ───────────────
    def test_row_without_a_card_is_closed_and_names_its_author(self):
        row = self._routed(live_row(37), LIVE_KEY, [])
        self.assertEqual(row["action"], "digest")
        close, why = z.closable(row, determined=True, carded=False)
        self.assertTrue(close)
        self.assertIn("отбор не дал карточку", why)

        verdicts = z.closures([row], determined={LIVE_KEY: True})
        self.assertEqual([v["close"] for v in verdicts], [True])
        self.assertEqual(verdicts[0]["outcome"], z.SIFT_OUTCOME)

        text = z.sift_result(verdicts[0], pointer_text="лоток docs/review_inbox и ряд #37")
        self.assertIn(z.SIFT_MARK, text)
        self.assertIn("ОТБОР", text)                     # кто решил
        self.assertIn("не человек", text)
        self.assertIn("НЕ ВИДЕЛ", text)
        self.assertIn("согласием", text)                 # и что это НЕ согласие

    def test_the_outcome_is_neither_accepted_nor_rejected(self):
        # Прямое условие задания: третий исход, не равный ни одному из двух прежних.
        text = z.sift_result({"id": 37, "why": "предмет клиенту не виден"})
        self.assertNotEqual(z.SIFT_OUTCOME, "принято к сведению")
        self.assertNotEqual(z.SIFT_OUTCOME, "отклонено")
        self.assertIn("НЕ «принято к сведению»", text)
        self.assertIn("НЕ «отклонено»", text)
        # и разрез в очереди НЕ пересекается с отказом владельца
        import pc_orchestrator
        self.assertNotIn(pc_orchestrator._REJECT_PREFIX, text)
        self.assertEqual(z.SIFT_STATUS, "done")          # закономерное закрытие, не сбой

    # ── 1. ряд с КЛИЕНТСКИМ ДЕФЕКТОМ отбором НЕ закрывается ──────────────
    def test_a_client_defect_row_is_never_closed(self):
        row = self._routed(defect_row(42), DEFECT_KEY, ["suggest.py:30"])
        self.assertEqual(row["action"], "card")
        close, why = z.closable(row, determined=True, carded=False)
        self.assertFalse(close)
        self.assertIn("ждёт живого решения", why)
        self.assertEqual([v["close"] for v in z.closures([row], determined={DEFECT_KEY: True})],
                         [False])

    def test_a_client_defect_over_the_cap_is_not_closed_either(self):
        # Сверх потолка исход другой (`card_over`), а запрет тот же.
        got = z.digest(defect_row(42))
        row = z.route([got], {DEFECT_KEY: self._sig(defect_row(42))},
                      {DEFECT_KEY: ["suggest.py:30"]}, sent_today=z.CARD_CAP)[0]
        self.assertEqual(row["action"], "card_over")
        self.assertFalse(z.closable(row, determined=True)[0])

    # ── 2. карточка отправлена, ответа нет → ряд ПРОДОЛЖАЕТ ждать ────────
    def test_a_carded_row_keeps_waiting_for_the_answer(self):
        row = self._routed(live_row(37), LIVE_KEY, [])
        close, why = z.closable(row, determined=True, carded=True)
        self.assertFalse(close)
        self.assertIn("ждём ОТВЕТА", why)
        self.assertIn("молчание", why)
        verdicts = z.closures([row], determined={LIVE_KEY: True}, carded=[LIVE_KEY])
        self.assertEqual([v["close"] for v in verdicts], [False])

    # ── 3. отбор НЕДОСТУПЕН → ряд остаётся ОТКРЫТЫМ ─────────────────────
    def test_an_undecidable_sift_keeps_the_row_open(self):
        row = self._routed(live_row(37), LIVE_KEY, [])
        for determined, word in ((False, "не построился"), (None, "не спрошен")):
            close, why = z.closable(row, determined=determined)
            self.assertFalse(close, determined)
            self.assertIn("остаётся", why)
            self.assertIn(word, why)
        # и через `closures`: ключа в карте нет вовсе — тот же исход
        self.assertEqual([v["close"] for v in z.closures([row], determined={})], [False])
        self.assertEqual([v["close"] for v in z.closures([row])], [False])

    def test_a_row_without_a_number_is_not_closed(self):
        row = dict(self._routed(live_row(None), LIVE_KEY, []), id=None)
        close, why = z.closable(row, determined=True)
        self.assertFalse(close)
        self.assertIn("нет номера", why)

    # ── журнал: автор решения назван СЛОВОМ ─────────────────────────────
    def test_journal_line_says_the_sift_decided_not_the_owner(self):
        line = z.index_line({"closed": [{"id": 37, "why": "отбор не дал карточку: предмет "
                                                          "клиенту не виден"}]})
        self.assertIn(z.SIFT_OUTCOME.upper(), line)
        self.assertIn("решил ОТБОР, не владелец", line)
        self.assertIn("согласием это не является", line)
        self.assertIn("#37", line)

    # ── сводка несёт ОБА числа врозь ────────────────────────────────────
    def test_summary_carries_both_numbers_apart(self):
        row = self._routed(live_row(37), LIVE_KEY, [])
        text = z.summary([row], found=1, awaiting_owner=4, closed_today=6)
        self.assertIn("ЖДЁТ ТВОЕГО РЕШЕНИЯ: 4", text)
        self.assertIn("ЗАКРЫТО ОТБОРОМ за сутки: 6", text)
        self.assertIn("складывать их нельзя", text)
        self.assertNotIn("10", text.split("ЖДЁТ ТВОЕГО РЕШЕНИЯ")[1])   # суммы нет нигде
        # не названы — «неизвестно», а не ноль
        blind = z.summary([row], found=1)
        self.assertIn("ЖДЁТ ТВОЕГО РЕШЕНИЯ: %s" % z.UNKNOWN, blind)
        self.assertIn("ЗАКРЫТО ОТБОРОМ за сутки: %s" % z.UNKNOWN, blind)

    def test_summary_no_longer_claims_every_row_stayed_open(self):
        # Строка «каждая заявка осталась ОТКРЫТОЙ» с 03.09 была бы враньём.
        text = z.summary([self._routed(live_row(37), LIVE_KEY, [])], found=1,
                         awaiting_owner=0, closed_today=1)
        self.assertNotIn("осталась ОТКРЫТОЙ", text)


class TestReconAsksAreJudgedByAnotherSift(unittest.TestCase):
    """Заявки ступени E под НАШ отбор не попадают — и это устройство."""

    def test_marker_is_the_same_literal_as_stage_e(self):
        import recon_auto
        self.assertEqual(z.RECON_MARK, recon_auto.ASK_MARK)

    def test_a_recon_ask_is_not_our_zayavka(self):
        import recon_auto
        text = recon_auto.ask_text(
            {"key": "d5bb46caa17f", "src": "expect", "title": "модербот молчит",
             "evidence": ["tmp/expect_pc/state.json"]},
            "2026-09-03", "предмет требует операционного действия")
        self.assertTrue(z.is_recon_ask(text))
        self.assertFalse(z.is_zayavka(text))

    def test_recon_asks_land_in_their_own_bucket_not_among_guard_cards(self):
        import recon_auto
        ask = recon_auto.ask_text({"key": "d5bb46caa17f", "src": "expect", "title": "t"},
                                  "2026-09-03", "почему")
        rows = [live_row(37), {"id": 40, "task_text": ask},
                {"id": 9, "task_text": "🔴 гард: red-операция ждёт «да»"}]
        got = z.split_awaiting(rows)
        self.assertEqual([r["id"] for r in got["zayavki"]], [37])
        self.assertEqual([r["id"] for r in got["foreign"]], [40])
        self.assertEqual([r["id"] for r in got["cards"]], [9])

    def test_a_recon_ask_never_reaches_the_closer(self):
        # Сквозной замок: ряд ступени E не доезжает даже до `route`, а значит и до
        # закрытия. Проверяем ЖИВЫМ оборотом, а не рассуждением.
        import recon_auto
        ask = recon_auto.ask_text({"key": "d5bb46caa17f", "src": "expect", "title": "t"},
                                  "2026-09-03", "почему")
        d = tempfile.mkdtemp(prefix="zayavki_recon_")
        q = _FakeQueue(rows=[{"id": 40, "lane": "pc", "status": "needs_approval",
                              "task_text": ask}])
        rep = run.tick(root=HERE, state_path=os.path.join(d, "s.json"), send=True,
                       clock=lambda: 1788400000.0, queue=q, sender=lambda *a: None)
        self.assertEqual(rep["foreign"], 1)
        self.assertEqual(rep["zayavki"], 0)
        self.assertEqual(rep["closed"], [])
        self.assertNotIn("close_by_sift", [c[0] for c in q.calls])
        self.assertIn("ступени E", rep["summary"])


class TestTickClosesAndCounts(unittest.TestCase):
    """Оборот целиком: закрытие идёт в очередь, в реестр и в оба числа."""

    NOW = 1788400000.0

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="zayavki_close_")
        self.state = os.path.join(self.dir, "s.json")

    def _tick(self, q, send=True, **kw):
        return run.tick(root=HERE, state_path=self.state, send=send,
                        clock=lambda: self.NOW, queue=q, sender=lambda *a: ("тест", True), **kw)

    def test_row_without_a_card_is_closed_in_the_queue(self):
        q = _FakeQueue(rows=[live_row(37)])
        rep = self._tick(q)
        self.assertEqual([r["id"] for r in rep["closed"]], [37])
        self.assertIn(("close_by_sift", "37"), q.calls)
        self.assertNotIn("accept", [c[0] for c in q.calls])
        self.assertNotIn("reject", [c[0] for c in q.calls])
        text = q.closed_texts[0]
        self.assertIn(z.SIFT_MARK, text)
        self.assertIn("ОТБОР", text)
        self.assertIn(z.INBOX_DIR, text)                  # находка не потеряна
        self.assertNotIn(z.QUOTE_HEAD, text)              # чужого текста нет
        self.assertNotIn(LIVE_CLAIM["quote"], text)

    def test_closure_is_recorded_and_counted_for_a_sliding_day(self):
        self._tick(_FakeQueue(rows=[live_row(37)]))
        state = run.read_state(self.state)
        self.assertEqual(len(state["closed"]), 1)
        rec = list(state["closed"].values())[0]
        self.assertEqual(rec["outcome"], z.SIFT_OUTCOME)
        self.assertEqual(rec["queue_id"], 37)
        self.assertEqual(run.closed_today_count(state, self.NOW), 1)
        # сутки прошли — из счёта уходит
        self.assertEqual(run.closed_today_count(state, self.NOW + 86401), 0)

    def test_both_numbers_are_reported_apart(self):
        rep = self._tick(_FakeQueue(rows=[live_row(37), defect_row(42)]))
        self.assertEqual(rep["awaiting_owner"], 1)        # только клиентский дефект
        self.assertEqual(rep["closed_today"], 1)
        self.assertIn("ЖДЁТ ТВОЕГО РЕШЕНИЯ: 1", rep["summary"])
        self.assertIn("ЗАКРЫТО ОТБОРОМ за сутки: 1", rep["summary"])

    def test_a_carded_row_is_kept_open_on_the_next_turn(self):
        # Отправили карточку по дефекту; на следующем обороте отбор его НЕ закрывает,
        # даже если бы счёл строкой сводки.
        q = _FakeQueue(rows=[defect_row(42)])
        self._tick(q)
        state = run.read_state(self.state)
        self.assertIn(DEFECT_KEY, state["sent"])
        q2 = _FakeQueue(rows=[defect_row(42)])
        rep = self._tick(q2)
        self.assertEqual(rep["closed"], [])
        self.assertNotIn("close_by_sift", [c[0] for c in q2.calls])

    def test_dry_run_closes_nothing_in_the_queue(self):
        q = _FakeQueue(rows=[live_row(37)])
        rep = self._tick(q, send=False)
        self.assertEqual([r["id"] for r in rep["closed"]], [37])
        self.assertNotIn("close_by_sift", [c[0] for c in q.calls])
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(rep["closed_today"], 1)          # сухой ход всё равно считает

    def test_bridge_refusal_leaves_the_row_open(self):
        # Мост отказал — ряд НЕ считается закрытым ни в одном числе.
        q = _FakeQueue(rows=[live_row(37)], close_ok=False)
        rep = self._tick(q)
        self.assertEqual(rep["closed"], [])
        self.assertEqual([r["id"] for r in rep["failed"]], [37])
        self.assertIn("закрытие отбором не прошло", rep["failed"][0]["why"])
        self.assertEqual(rep["awaiting_owner"], 1)
        self.assertEqual(rep["closed_today"], 0)
        self.assertEqual(run.read_state(self.state)["closed"], {})

    def test_the_off_switch_keeps_rows_open(self):
        os.environ[run.NO_CLOSE_FLAG] = "1"
        try:
            q = _FakeQueue(rows=[live_row(37)])
            rep = self._tick(q)
            self.assertEqual(rep["closed"], [])
            self.assertNotIn("close_by_sift", [c[0] for c in q.calls])
            self.assertIn("закрытие выключено", rep["kept"][0]["why"])
            self.assertEqual(rep["awaiting_owner"], 1)
        finally:
            os.environ.pop(run.NO_CLOSE_FLAG, None)

    def test_the_journal_line_carries_the_author(self):
        rep = self._tick(_FakeQueue(rows=[live_row(37)]))
        self.assertIn("решил ОТБОР, не владелец", rep["line"])


if __name__ == "__main__":
    unittest.main()
