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

    def __init__(self, rows=None, ok=True, why=""):
        self._rows = rows if rows is not None else [live_row(1), live_row(2), live_row(3)]
        self._ok, self._why = ok, why
        self.calls = []

    def awaiting(self):
        self.calls.append(("awaiting",))
        return (self._rows if self._ok else []), self._ok, self._why

    def accept(self, tid):
        self.calls.append(("accept", str(tid)))
        return True, ""

    def reject(self, tid):
        self.calls.append(("reject", str(tid)))
        return True, ""

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

    def test_three_live_zayavki_are_delivered_once(self):
        rep = run.tick(root=self.dir, state_path=self.state, send=True,
                       clock=lambda: 1788400000.0, queue=_FakeQueue(), sender=self._sender)
        self.assertEqual(rep["zayavki"], 3)
        self.assertEqual(len(rep["sent"]), 3)
        self.assertEqual(len(self.sent), 3)
        for _text, markup in self.sent:
            self.assertIn("inline_keyboard", markup)   # ответ в ОДИН тап, а не набором текста
        again = run.tick(root=self.dir, state_path=self.state, send=True,
                         clock=lambda: 1788400060.0, queue=_FakeQueue(), sender=self._sender)
        self.assertEqual(len(again["sent"]), 0)        # повторной отправки НЕТ
        self.assertEqual(len(self.sent), 3)

    def test_reminder_after_a_day_is_a_single_message(self):
        run.tick(root=self.dir, state_path=self.state, send=True, clock=lambda: 1788400000.0,
                 queue=_FakeQueue(), sender=self._sender)
        self.sent.clear()
        rep = run.tick(root=self.dir, state_path=self.state, send=True,
                       clock=lambda: 1788400000.0 + z.REMIND_MIN_SEC,
                       queue=_FakeQueue(), sender=self._sender)
        self.assertEqual(len(rep["reminded"]), 3)
        self.assertEqual(len(self.sent), 1)            # ОДНА строка на всех, а не три карточки
        self.assertIsNone(self.sent[0][1])             # у напоминания кнопок нет

    def test_claim_without_visible_consequence_is_held_with_a_reason(self):
        q = _FakeQueue(rows=[live_row(1, status="done")])
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
                       clock=lambda: 1788400000.0, queue=_FakeQueue(), sender=self._sender)
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.state))
        self.assertEqual(len(rep["sent"]), 3)          # собрано, но не отправлено


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


if __name__ == "__main__":
    unittest.main()
