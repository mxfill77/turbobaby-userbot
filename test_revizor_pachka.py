# -*- coding: utf-8 -*-
"""Суточный список находок ревизора с отбраковкой построчно (задание Штаба 0015j-71d.2209).

Наружу из этих проверок не уходит НИЧЕГО: отправка подаётся инъекцией `sender`, боевая дверь
(`_revizor_pachka_send_live` → `_dnotify_spawn`) подменена на исключение там, где путь мог бы до неё
дойти. Все файлы (ожидание, реестр вердиктов, расписка карточек, полный текст) — во временном
каталоге; базу модерации и боевые файлы состояния тесты не открывают.
Запуск: venv\\Scripts\\python.exe -m unittest test_revizor_pachka -v
"""

import os
import json
import tempfile
import unittest
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"
for _marker in ("PRETOOL_APPROVED_KINDS", "PRETOOL_APPROVED_OBJECT", "PRETOOL_APPROVED_TASK"):
    os.environ.pop(_marker, None)

import pc_orchestrator as o          # noqa: E402
import revizor_pachka as rp           # noqa: E402
import card_terminal_log              # noqa: E402

NOW = 1_790_000_000.0                  # 2026-09-21 ~21:33 UTC — фиксированные часы
DAY = 86400.0
REJ = o._REJECT_PREFIX + " (ответ с ПК: Filipp/console): это ступени прайса"


def f_owner(cid=1126977519, ev="разные суммы депозита 5000 и 10000", cls="#92",
            check="депозит без противоречий"):
    """Находка в живом формате пост-релизного прохода (#92): action=owner, чек полем check."""
    return {"class": cls, "client_id": cid, "action": "owner", "check": check,
            "evidence": ev, "task_text": ""}


def at_local(day_offset, hour, tz=rp.DEFAULT_TZ_HOURS):
    """Метка времени: местные сутки NOW + day_offset, час `hour` по Пхукету."""
    base_day, _h = rp.local_day(NOW, tz)
    import datetime as _d
    d = _d.datetime.strptime(base_day, "%Y-%m-%d").replace(tzinfo=_d.timezone.utc)
    return (d + _d.timedelta(days=day_offset, hours=hour - tz)).timestamp()


def _no_live(*_a, **_k):
    raise AssertionError("боевая дверь наружу позвана из проверки")


class Tmp(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="pachka_")
        self.sp = os.path.join(d, "pachka.json")
        self.vp = os.path.join(d, "verdicts.json")
        self.cp = os.path.join(d, "cards.json")
        self.fp = os.path.join(d, "full.txt")
        p = mock.patch.object(o, "_dnotify_spawn", _no_live)
        p.start()
        self.addCleanup(p.stop)
        c = mock.patch.object(o, "_cowork", lambda line: None)
        c.start()
        self.addCleanup(c.stop)
        # список включён НА ТЕСТ (соседний набор держит откат флагом на уровне модуля)
        e = mock.patch.dict(os.environ)
        e.start()
        self.addCleanup(e.stop)
        os.environ.pop("REVIZOR_PACHKA_OFF", None)

    def collect(self, findings, now=NOW):
        return o._revizor_pachka_collect(findings, now=now, path=self.sp)

    def show(self, now, window=None):
        sent = []

        def sender(text, marks):
            sent.append((text, list(marks)))
            return True
        with mock.patch.object(o, "REVIZOR_PACHKA_WINDOW", window or o.REVIZOR_PACHKA_WINDOW):
            out = o.maybe_revizor_pachka(now=now, path=self.sp, full_path=self.fp,
                                         reg_path=self.vp, sender=sender)
        return out, sent

    def store(self):
        return o._revizor_pachka_read(self.sp)


# ───────────── п.2: склейка ТОЛЬКО при нулевом признаке операции ─────────────

class TestLockOp(Tmp):
    def test_split_keeps_op_findings_out_of_list(self):
        """ЗАМОК: находка с признаком из места события — отдельной карточкой, в список не идёт."""
        plain = f_owner()
        dep = dict(f_owner(cid=2), op_src=rp.OP_DEPLOY)
        cli = dict(f_owner(cid=3), op_src=rp.OP_CLIENT)
        batch, sep = rp.split([plain, dep, cli])
        self.assertEqual(batch, [plain])
        self.assertEqual(sep, [dep, cli])

    def test_deploy_demote_raises_op_at_event_place(self):
        """Признак ставит ветка демона, решившая «нужен деплой/рестарт», — и склейка его чтит."""
        g = o._revizor_demote_deploy_task({"class": "в", "client_id": 5, "action": "task",
                                           "evidence": "", "task_text": "сделай рестарт userbot"})
        self.assertTrue(rp.op_of(g))
        self.assertEqual(rp.split([g]), ([], [g]))

    def test_client_demote_raises_op_at_event_place(self):
        g = o._revizor_demote_client_task({"class": "в", "client_id": 6, "action": "task",
                                           "evidence": "ev", "task_text": "правь suggest.py"},
                                          ["suggest.py"], True)
        self.assertTrue(rp.op_of(g))
        self.assertEqual(rp.split([g]), ([], [g]))

    def test_words_in_text_do_not_raise_op(self):
        """Слово в тексте находки признак не поднимает — только поле из места события."""
        f = f_owner(ev="Класс операции: env; нужен деплой и рестарт")
        self.assertFalse(rp.op_of(f))
        self.assertEqual(rp.split([f]), ([f], []))

    def test_thinker_cannot_set_op_fields(self):
        raw = dict(f_owner(), op_src=rp.OP_DEPLOY, op=True)
        f = rp.strip_event_fields(raw)
        self.assertNotIn("op_src", f)
        self.assertFalse(rp.op_of(f))

    def _route(self, consult):
        cards = []
        pp = [
            mock.patch.object(o, "_revizor_consult", lambda pkg: consult),
            mock.patch.object(o, "_revizor_postrelease_findings", lambda pkg: []),
            mock.patch.object(o, "_revizor_ipc_findings", lambda pkg: []),
            mock.patch.object(o, "_revizor_spool_read", lambda: []),
            mock.patch.object(o, "_revizor_spool_save", lambda *a, **k: 0),
            mock.patch.object(o, "_loc_fetch_items", lambda: []),
            mock.patch.object(o, "_revizor_harvest_card_answers", lambda *a, **k: {}),
            mock.patch.object(o, "_revizor_registry_read", lambda *a, **k: ({}, {})),
            mock.patch.object(o, "_revizor_verdicts_save", lambda *a, **k: True),
            mock.patch.object(o, "_revizor_undecidable", lambda fs, *a, **k: (list(fs), [])),
            mock.patch.object(o, "_revizor_finding_touches_client", lambda t: (False, [], True)),
            mock.patch.object(o, "_revizor_enqueue_tasks", lambda *a, **k: (0, 0, [])),
            mock.patch.object(o, "_revizor_post_owner_card",
                              lambda fs, items, now=None: (cards.append(list(fs)), (True, 7, ""))[1]),
            mock.patch.object(o, "REVIZOR_PACHKA_FILE", self.sp),
        ]
        for p in pp:
            p.start()
        try:
            out = o._revizor_route([{"client_id": 42}], now=NOW)
        finally:
            for p in reversed(pp):
                p.stop()
        return out, cards

    def test_route_op_finding_goes_card_plain_goes_list(self):
        """Прогон целиком: деплой-находка (признак из места события) — карточкой, как раньше;
        простая owner-находка — в список, карточки по ней нет."""
        out, cards = self._route([
            {"class": "в", "action": "task", "evidence": "", "task_text": "сделай рестарт userbot"},
            {"class": "а", "action": "owner", "evidence": "спорный тариф 700", "task_text": ""}])
        self.assertEqual(len(cards), 1)
        self.assertEqual([rp.op_of(f) for f in cards[0]], [True])
        st = self.store()
        self.assertEqual([x["class"] for x in st["lines"]], ["а"])
        self.assertEqual(out["batched"], 1)

    def test_route_thinker_op_field_is_ignored(self):
        """Думатель, вписавший признак в сырой JSON, карточку себе не выпишет — строка в список."""
        out, cards = self._route([{"class": "а", "action": "owner", "evidence": "тариф 700",
                                   "task_text": "", "op_src": rp.OP_DEPLOY, "op": True}])
        self.assertEqual(cards, [])
        self.assertEqual(len(self.store()["lines"]), 1)

    def test_list_unwritable_falls_back_to_card(self):
        """Список на диск не ложится → показ ПРЕЖНИЙ: находка едет карточкой, а не теряется."""
        os.makedirs(self.sp)                       # путь ожидания — каталог: ни прочесть, ни записать
        out, cards = self._route([{"class": "а", "action": "owner", "evidence": "тариф 700",
                                   "task_text": ""}])
        self.assertEqual(len(cards), 1)
        self.assertEqual(out.get("batched"), 0)


# ───────────── п.3: кнопка списка = кнопка карточки, двусторонне ─────────────

class TestSameVerdict(Tmp):
    def test_list_tap_writes_same_record_as_card_reject(self):
        f = f_owner()
        # карточка: расписка + «нет» владельца → жатва (живой путь)
        o._revizor_card_keys_add(58, [f], now=NOW, path=self.cp)
        vp_card = self.vp + ".card"
        o._revizor_harvest_card_answers([{"id": 58, "status": "failed", "result": REJ}],
                                        now=NOW, path=self.cp, reg_path=vp_card)
        # список: та же находка → ожидание → тап ❌
        self.collect([f])
        mark = self.store()["lines"][0]["mark"]
        ok, _t = o.revizor_pachka_reject(mark, now=NOW, path=self.sp, reg_path=self.vp)
        self.assertTrue(ok)
        a, b = o._revizor_verdicts_read(vp_card), o._revizor_verdicts_read(self.vp)
        self.assertEqual(set(a), set(b))                                   # тот же ключ
        key = o._revizor_verdict_key(f)
        self.assertEqual(list(a), [key])
        same = ("verdict", "gist", "source", "hits", "last_hit", "at")
        self.assertEqual({k: a[key][k] for k in same}, {k: b[key][k] for k in same})
        self.assertEqual(b[key]["verdict"], "ложная")
        self.assertEqual(b[key]["source"], o.REVIZOR_SRC_OWNER)
        # единственное различие — основание называет место нажатия
        self.assertIn("карточке #58", a[key]["why"])
        self.assertIn(mark, b[key]["why"])
        # и действие одинаково: оба реестра глушат ту же находку следующего прогона
        for reg in (a, b):
            self.assertEqual(o._revizor_verdict_of(f, reg)[:2], ("ложная", True))

    def test_rejected_line_absent_from_next_list(self):
        self.collect([f_owner(cid=1), f_owner(cid=2)])
        _o, sent = self.show(at_local(1, 9))
        m1 = sent[0][1][0]
        ok, _t = o.revizor_pachka_reject(m1, now=NOW, path=self.sp, reg_path=self.vp)
        self.assertTrue(ok)
        # ревизор нашёл ту же находку снова: вердикт глушит её до склейки
        again, muted = o._revizor_apply_verdicts([f_owner(cid=1)], path=self.vp, save=False)
        self.assertEqual((again, len(muted)), ([], 1))
        # и даже если бы она дошла — метка отбракованной в ожидание не возвращается
        self.assertEqual(self.collect([f_owner(cid=1)]), 0)
        self.collect([f_owner(cid=3)])                           # повод для следующего списка
        _o, sent2 = self.show(at_local(2, 9))
        self.assertNotIn(m1, sent2[0][1])
        self.assertEqual(len(sent2[0][1]), 2)

    def test_double_tap_does_not_write_twice(self):
        self.collect([f_owner()])
        m = self.store()["lines"][0]["mark"]
        self.assertTrue(o.revizor_pachka_reject(m, now=NOW, path=self.sp, reg_path=self.vp)[0])
        ok, text = o.revizor_pachka_reject(m, now=NOW, path=self.sp, reg_path=self.vp)
        self.assertTrue(ok)
        self.assertIn("уже отбракована", text)

    def test_verdict_from_other_path_drops_line_before_show(self):
        f = f_owner()
        self.collect([f])
        o.revizor_set_verdict(f, "ложная", now=NOW, path=self.vp, source=o.REVIZOR_SRC_HAND)
        out, sent = self.show(at_local(1, 9))
        self.assertIsNone(out)                                   # ожидать нечего — списка нет
        self.assertEqual(sent, [])
        self.assertEqual(self.store()["lines"], [])


# ───────────── п.4: строка без нажатия не пропадает ─────────────

class TestCarry(Tmp):
    def test_untapped_lines_carry_to_next_list(self):
        self.collect([f_owner(cid=c) for c in (1, 2, 3)])
        out1, sent1 = self.show(at_local(1, 9))
        self.assertEqual((out1["new"], out1["carried"], out1["shown"]), (3, 0, 3))
        o.revizor_pachka_reject(sent1[0][1][0], now=NOW, path=self.sp, reg_path=self.vp)
        self.collect([f_owner(cid=4)])
        out2, sent2 = self.show(at_local(2, 9))
        self.assertEqual((out2["new"], out2["carried"], out2["shown"]), (1, 2, 3))
        self.assertIn("перенесено 2", sent2[0][0])
        self.assertTrue(set(sent1[0][1][1:]) <= set(sent2[0][1]))

    def test_show_does_not_remove_lines(self):
        self.collect([f_owner()])
        self.show(at_local(1, 9))
        self.assertEqual(len(self.store()["lines"]), 1)

    def test_send_failure_keeps_lines_new(self):
        self.collect([f_owner()])
        out = o.maybe_revizor_pachka(now=at_local(1, 9), path=self.sp, full_path=self.fp,
                                     reg_path=self.vp, sender=lambda t, m: False)
        self.assertFalse(out["sent"])
        st = self.store()
        self.assertEqual([x["shown"] for x in st["lines"]], [0])
        self.assertTrue(rp.due(st, at_local(2, 9))[0])           # завтра придёт как новая

    def test_schedule_once_per_local_day_after_hour(self):
        self.collect([f_owner()])
        self.assertIsNone(self.show(at_local(1, 8))[0])          # 08:xx по Пхукету — рано
        self.assertTrue(self.show(at_local(1, 9))[0]["sent"])    # 09:00 — пора
        self.assertIsNone(self.show(at_local(1, 20))[0])         # те же сутки — второго нет
        self.assertIsNone(self.show(at_local(2, 9))[0])          # новых нет, висящая — ждёт напоминания
        self.assertTrue(self.show(at_local(8, 9))[0]["sent"])    # через 7 суток напомним

    def test_default_hour_is_nine_phuket(self):
        self.assertEqual((rp.DEFAULT_HOUR, rp.DEFAULT_TZ_HOURS), (9, 7))


# ───────────── п.5: окно сообщения — прибор ─────────────

class TestWindow(Tmp):
    def test_overflow_names_hidden_count_and_full_text_path(self):
        self.collect([f_owner(cid=c) for c in range(1, 26)])
        out, sent = self.show(at_local(1, 9), window=20)
        self.assertEqual((out["shown"], out["hidden"]), (20, 5))
        self.assertIn("НЕ ПОКАЗАНО строк: 5 из 25", sent[0][0])
        self.assertIn(self.fp, sent[0][0])
        with open(self.fp, encoding="utf-8") as f:
            full = f.read()
        self.assertEqual(full.count("[метка "), 25)            # полный текст — все 25
        self.assertLessEqual(len(sent[0][0]), 4096)

    def test_char_budget_is_named_too(self):
        long_ev = "очень длинная улика " * 20
        self.collect([f_owner(cid=c, ev=long_ev + str(c)) for c in range(1, 40)])
        out, sent = self.show(at_local(1, 9), window=100)
        self.assertGreater(out["hidden"], 0)
        self.assertIn("НЕ ПОКАЗАНО строк: %d" % out["hidden"], sent[0][0])
        self.assertLessEqual(len(sent[0][0]), rp.DEFAULT_WINDOW_CHARS)

    def test_no_silent_cut_when_fits(self):
        self.collect([f_owner()])
        out, sent = self.show(at_local(1, 9))
        self.assertEqual(out["hidden"], 0)
        self.assertNotIn("НЕ ПОКАЗАНО", sent[0][0])

    def test_markup_one_button_per_shown_line_and_parsable(self):
        self.collect([f_owner(cid=c) for c in range(1, 8)])
        _o, sent = self.show(at_local(1, 9))
        kb = rp.markup(sent[0][1])
        btns = [b for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(len(btns), 7)
        import pc_agent
        for b, m in zip(btns, sent[0][1]):
            self.assertLessEqual(len(b["callback_data"].encode("utf-8")), 64)
            self.assertEqual(pc_agent._pachka_cb_parse(b["callback_data"]), ("no", m))


# ───────────── п.6: нажатие прошло, вердикт не лёг — прибор говорит ОТКАЗ ─────────────

class TestRefusal(Tmp):
    def test_registry_unwritable_shows_refusal_and_keeps_line(self):
        self.collect([f_owner()])
        m = self.store()["lines"][0]["mark"]
        os.makedirs(self.vp)                      # реестр — каталог: запись вердикта невозможна
        ok, text = o.revizor_pachka_reject(m, now=NOW, path=self.sp, reg_path=self.vp)
        self.assertFalse(ok)
        self.assertIn("ОТКАЗ", text)
        self.assertIn("легло 0 из 1", text)
        self.assertEqual(len(self.store()["lines"]), 1)          # строка осталась в списке

    def test_write_says_ok_but_readback_disagrees_is_refusal(self):
        self.collect([f_owner()])
        m = self.store()["lines"][0]["mark"]
        with mock.patch.object(o, "_revizor_verdicts_save", lambda *a, **k: True):   # «записал», а не лёг
            ok, text = o.revizor_pachka_reject(m, now=NOW, path=self.sp, reg_path=self.vp)
        self.assertFalse(ok)
        self.assertIn("обратное чтение", text)
        self.assertEqual(len(self.store()["lines"]), 1)

    def test_unknown_mark_is_refusal(self):
        self.collect([f_owner()])
        ok, text = o.revizor_pachka_reject("0123456789ab", now=NOW, path=self.sp, reg_path=self.vp)
        self.assertFalse(ok)
        self.assertIn("легло 0 из 1", text)


# ───────────── путь до человека: боевая дверь и проверка различаются ─────────────

class TestNoOutside(Tmp):
    def test_live_sender_is_the_only_caller_of_pachka_card(self):
        import inspect
        src = inspect.getsource(o)
        self.assertEqual(src.count('"--pachka-card"'), 1)
        self.assertIn('"--pachka-card"', inspect.getsource(o._revizor_pachka_send_live))
        self.assertNotIn("_dnotify_spawn", inspect.getsource(o.maybe_revizor_pachka))

    def test_default_sender_is_live_door_and_probe_blocks_it(self):
        """Без `sender` путь идёт в боевую дверь — и в проверке её ловит подмена (исключение)."""
        self.collect([f_owner()])
        with mock.patch.object(o, "REVIZOR_PACHKA_PAYLOAD", self.fp + ".send.json"):
            out = o.maybe_revizor_pachka(now=at_local(1, 9), path=self.sp, full_path=self.fp,
                                         reg_path=self.vp)
        self.assertFalse(out["sent"])                             # _no_live бросил → отказ, не успех

    def test_agent_route_owner_only(self):
        import pc_agent
        good = "pachka:no:0123456789ab"
        r = pc_agent._chain_cb_route(good, pc_agent.ALLOWED_USER_ID)
        self.assertEqual((r["ok"], r["kind"], r["pid"]), (True, "pachka", "0123456789ab"))
        self.assertFalse(pc_agent._chain_cb_route(good, -1)["ok"])
        self.assertIsNone(pc_agent._pachka_cb_parse("pachka:no:0123;rm"))


if __name__ == "__main__":
    unittest.main()
