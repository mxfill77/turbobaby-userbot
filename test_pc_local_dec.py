# -*- coding: utf-8 -*-
"""
test_pc_local_dec.py — порт эталонных СЦЕНАРИЕВ tests/test_pc_dec.py VPS-репо (manager-bot,
декомпозер ПК-театра) на ЛОКАЛЬНЫЙ дирижёр (флаг PC_LOCAL_DEC; шаг 6/7 родителя 185).
Дополняет юнит-тесты test_pc_orchestrator (TestLocalDec*): здесь — сквозные сценарии
«как на VPS-эталоне», блоками (0)/(А)-(Е), через РЕАЛЬНЫЕ process_new/process_local_chains/
process_approved/process_approval_timeouts/process_stuck_singles. Без сети/claude/Bridge —
всё замокано; разрушительного ничего не выполняется.
Отличия театров учтены: исполнитель шагов — ЭТОТ ЖЕ демон (не «ПК за очередью»), поэтому
аналог (Д) «ПК молчит» = ПК-ливнесс застрявшей in_progress (process_stuck_singles, ⏱) и
просрочки approve/needs_approval (⏱) — все ⏱/✋-провалы глушат думателя (гейт _loc_after_fail).
Регресс ТЗ шага 6: при PC_LOCAL_DEC=0 старый путь байт-в-байт цел (родитель — run_task,
надзор даже не читает очередь), цепи from=Filipp-pc-dec (VPS-театр) не задеты.
Запуск: venv\\Scripts\\python.exe -m unittest test_pc_local_dec -v
"""

import datetime
import os
import unittest

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (деплой 334);
                                       # ставим ДО импорта o: load_dotenv(override=False) не перепишет

import pc_orchestrator as o           # noqa: E402


def now_iso(ago_sec=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=ago_sec)).isoformat()


class FakeBridge:
    """Очередь в памяти (порт lane-aware мока VPS-эталона): enqueue/claim/complete/
    needs_approval + хелперы ассертов по маркерам цепи. self.calls считает get_pending —
    доказательство «флаг off → надзор очередь даже не читает»."""

    def __init__(self):
        self.rows, self.nid, self.calls = {}, 100, 0

    def enqueue_task(self, frm, txt, lane="pc"):
        self.nid += 1
        self.rows[self.nid] = {"id": self.nid, "from": frm, "task_text": txt, "status": "new",
                               "result": "", "updated": now_iso(), "lane": lane or "pc"}
        return {"ok": True, "id": self.nid}

    def get_pending(self, status, lane=None):
        self.calls += 1
        sts = [x.strip() for x in str(status).split(",")]
        items = [dict(r) for r in sorted(self.rows.values(), key=lambda x: -x["id"])
                 if r["status"] in sts]
        return {"ok": True, "items": items}

    def claim_task(self, tid):
        r = self.rows.get(int(tid))
        if not r:
            return {"ok": False, "error": "not_found"}
        if r["status"] != "new":
            return {"ok": False, "error": "already_claimed"}
        r["status"], r["updated"] = "in_progress", now_iso()
        return {"ok": True, "task": dict(r)}

    def complete_task(self, tid, status, result=""):
        r = self.rows.get(int(tid))
        if not r:
            return {"ok": False, "error": "not_found"}
        r["status"], r["result"], r["updated"] = status, result, now_iso()
        return {"ok": True}

    def set_needs_approval(self, tid, what, topic=None):
        r = self.rows.get(int(tid))
        r["status"], r["result"], r["updated"] = "needs_approval", what, now_iso()
        return {"ok": True}

    def task_heartbeat(self, tid):
        return {"ok": True}

    # --- хелперы ассертов (зеркало VPS-эталона) ---

    def chain_news(self, frm=None):
        """new-задачи с маркером шага цепи (инвариант sequential: максимум одна)."""
        frm = frm or o.PC_LOCAL_DEC_FROM
        return [r for r in sorted(self.rows.values(), key=lambda x: x["id"])
                if r["status"] == "new" and r["from"] == frm
                and o._STEP_RE.match(r["task_text"])]

    def steps(self, pid, status=None):
        out = []
        for r in sorted(self.rows.values(), key=lambda x: x["id"]):
            m = o._STEP_RE.match(str(r["task_text"]))
            if m and int(m.group(3)) == int(pid) and (status is None or r["status"] == status):
                out.append(r)
        return out

    def summaries(self, pid):
        return [r for r in sorted(self.rows.values(), key=lambda x: x["id"])
                if str(r["task_text"]).startswith(f"[сводка родитель {pid}]")]

    def cards(self, pid):
        return [r for r in sorted(self.rows.values(), key=lambda x: x["id"])
                if str(r["task_text"]).startswith(f"[карточка родитель {pid}]")]

    def adapt_cards(self, pid):
        return [r for r in sorted(self.rows.values(), key=lambda x: x["id"])
                if str(r["task_text"]).startswith(f"[коррекция плана родитель {pid}]")]


class LocBase(unittest.TestCase):
    """Сцена порта: моки думателей (диспетчер по преамбуле — план/самопочинка/адаптация/
    одиночка), мок-исполнитель run_task с очередью результатов, чистые кэши дирижёра."""

    _ENV_KEYS = ("PC_LOCAL_DEC", "STEP_SELFHEAL", "PLAN_ADAPT")

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in self._ENV_KEYS}
        os.environ["PC_LOCAL_DEC"] = "1"
        os.environ["STEP_SELFHEAL"] = "1"
        os.environ["PLAN_ADAPT"] = "1"
        self._save = (o.bc, o.run_task, o._thinker_exec, o._notify, o._cowork, o._stopped,
                      o.maybe_update_bots, o._notify_chain_card, o._loc_mark_chain_final)
        self.fb = FakeBridge()
        o.bc = self.fb
        o._notify = lambda *a, **k: None
        o._cowork = lambda *a, **k: None
        o._stopped = lambda: False
        o.maybe_update_bots = lambda *a, **k: ""
        # СПАМ-ЛУП 14:25 14.07: незамоканный _notify_chain_card стрелял из гейт-тестов РЕАЛЬНЫМИ
        # subprocess-карточками фикстурной цепи 101 в личку (41 шт при каждом прогоне гейта).
        # Тесты НИКОГДА не шлют наружу: карточки копим в self.chain_cards, state-файл не трогаем.
        self.chain_cards = []
        o._notify_chain_card = lambda pid, text, **kw: self.chain_cards.append((pid, text))
        o._loc_mark_chain_final = lambda pid, path=None: None   # боевой state-файл в тестах не пишем
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()
        o._loc_adapted.clear()
        # думатели: диспетчер по преамбуле (как fake_run VPS-эталона)
        self.plan_out = "1. шаг один\n2. шаг два"
        self.planner_prompts = []
        self.adapt_calls, self.adapt_queue = 0, []
        self.adapt_out = '{"verdict":"keep","adjusted_steps":[],"reason":"план верен"}'
        self.thinker_calls = 0
        self.thinker_out = '{"verdict":"halt","fixed_step":"","reason":"дефолт мока"}'
        self.task_thinker_calls = 0

        def fake_thinker(prompt, timeout, tag):
            if prompt.startswith(o.PLANNER_PREAMBLE):
                self.planner_prompts.append(prompt)
                return self.plan_out
            if prompt.startswith(o.ADAPT_PREAMBLE):
                self.adapt_calls += 1
                if self.adapt_queue:
                    return self.adapt_queue.pop(0)
                return self.adapt_out
            if prompt.startswith(o.TASK_THINKER_PREAMBLE):
                self.task_thinker_calls += 1
                return '{"verdict":"halt","fixed_task":"","reason":"одиночный думатель"}'
            self.thinker_calls += 1
            return self.thinker_out
        o._thinker_exec = fake_thinker
        # исполнитель шагов (run_task этого же демона)
        self.exec_queue = []

        def fake_run_task(tid, text, note=""):
            if self.exec_queue:
                return self.exec_queue.pop(0)
            return ("done", "RESULT: ок")
        o.run_task = fake_run_task

    def tearDown(self):
        (o.bc, o.run_task, o._thinker_exec, o._notify, o._cowork, o._stopped,
         o.maybe_update_bots, o._notify_chain_card, o._loc_mark_chain_final) = self._save
        o._loc_summarized.clear()
        o._loc_adapt_finish.clear()
        o._loc_adapted.clear()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # --- обвязка сцены ---

    def new_parent(self, text="крупное ТЗ для ПК"):
        return self.fb.enqueue_task(o.PC_LOCAL_DEC_FROM, text)["id"]

    def plan_parent(self, plan="1. первый\n2. второй"):
        """Родитель + построение плана (process_new → планировщик) → pid; шаг 1 уже релизнут."""
        self.plan_out = plan
        pid = self.new_parent()
        o.process_new()
        return pid

    def exec_step(self, status="done", result="шаг сделан"):
        """Мок-исполнение ЕДИНСТВЕННОГО new-шага цепи РЕАЛЬНЫМ process_new (порт pc_exec эталона;
        заодно держит инвариант sequential release). → id шага."""
        news = self.fb.chain_news()
        self.assertEqual(len(news), 1,
                         f"на очереди должен ждать ровно 1 шаг цепи (sequential release): {news}")
        sid = news[0]["id"]
        self.exec_queue.append((status, result))
        o.process_new()
        return sid


# ------------------------- (0) юниты плана: restart-proof из очереди -------------------------

class TestPlanUnits(LocBase):
    def test_parse_numbered(self):
        self.assertEqual(o._parse_numbered("шапка\n1. раз\n2) два\nхвост 3 не шаг"),
                         {1: "раз", 2: "два"})

    def test_plan_restored_from_parent_result(self):
        pid = self.new_parent()
        self.fb.rows[pid]["status"] = "done"
        self.fb.rows[pid]["result"] = "🧩 план:\n1. альфа\n2. бета\n3. гамма"
        plan, k, base = o._loc_current_plan(pid)
        self.assertEqual(plan, {1: ("альфа", 0), 2: ("бета", 0), 3: ("гамма", 0)})
        self.assertEqual((k, base), (0, None))

    def test_correction_card_overlays_remaining_plan(self):
        pid = self.new_parent()
        self.fb.rows[pid]["status"] = "done"
        self.fb.rows[pid]["result"] = "🧩 план:\n1. альфа\n2. бета\n3. гамма"
        cid = self.fb.enqueue_task(o.PC_LOCAL_DEC_FROM,
                                   f"[коррекция плана родитель {pid}] после шага 2 (K=1)")["id"]
        self.fb.rows[cid]["status"] = "done"
        self.fb.rows[cid]["result"] = ("🧭 коррекция: причина\nНОВЫЙ ОСТАВШИЙСЯ ПЛАН:\n"
                                       "3. дельта\n4. эпсилон")
        plan, k, base = o._loc_current_plan(pid)
        self.assertEqual(plan, {1: ("альфа", 0), 2: ("бета", 0),
                                3: ("дельта", 1), 4: ("эпсилон", 1)})
        self.assertEqual((k, base), (1, 2))


# ------------------- (А) цепь целиком: план → шаги ПО ОДНОМУ → сводка -------------------

class TestChainToSummary(LocBase):
    def test_full_chain_sequential_to_summary_and_restart_proof(self):
        pid = self.plan_parent("1. первый\n2. второй\n3. третий")
        parent = self.fb.rows[pid]
        self.assertEqual(parent["status"], "done")
        self.assertIn("локальный дирижёр PC", parent["result"])
        self.assertIn("1. первый", parent["result"])
        # планировщик — портированная преамбула ПК-репо, не VPS
        self.assertTrue(self.planner_prompts[0].startswith(o.PLANNER_PREAMBLE))
        self.assertIn("D:\\turbobaby-bot", self.planner_prompts[0])
        self.assertNotIn("/root/turbobaby-manager-bot", self.planner_prompts[0])
        # релизнут ТОЛЬКО шаг 1 (sequential), from=Filipp-pcloc-dec, lane=pc
        s1 = self.fb.chain_news()
        self.assertEqual(len(s1), 1)
        self.assertTrue(s1[0]["task_text"].startswith(f"[шаг 1/3 родитель {pid}]"))
        self.assertEqual((s1[0]["from"], s1[0]["lane"]), (o.PC_LOCAL_DEC_FROM, "pc"))
        # шаг ещё new (ждёт FIFO-клейма) → надзор не дёргает думателей и не плодит шагов
        n_rows = len(self.fb.rows)
        o.process_local_chains()
        self.assertEqual(len(self.fb.rows), n_rows)
        self.assertEqual((self.thinker_calls, self.adapt_calls), (0, 0))
        # шаг 1 done → адаптация (keep) → релиз шага 2; и так до конца
        self.exec_step("done", "первый готов")
        o.process_local_chains()
        self.assertEqual(self.adapt_calls, 1)
        s = self.fb.chain_news()
        self.assertEqual(len(s), 1)
        self.assertTrue(s[0]["task_text"].startswith(f"[шаг 2/3 родитель {pid}]"))
        self.exec_step("done", "второй готов")
        o.process_local_chains()
        self.exec_step("done", "третий готов")
        o.process_local_chains()
        sums = self.fb.summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertEqual(sums[0]["status"], "done")
        self.assertEqual(sums[0]["from"], o.PC_LOCAL_DEC_FROM)
        self.assertIn("3/3 шагов done", sums[0]["result"])
        self.assertIn("локальный дирижёр", sums[0]["result"])
        self.assertEqual(sums[0]["result"].count("✅"), 3)
        # на последнем шаге думатель адаптации НЕ зовётся (экономия лимитов)
        self.assertEqual(self.adapt_calls, 2)
        # идемпотентность
        o.process_local_chains()
        self.assertEqual(len(self.fb.summaries(pid)), 1)
        # restart-proof: «рестарт демона» (потеря кэшей) — закрытая цепь узнана по сводке
        o._loc_summarized.clear()
        o._loc_adapted.clear()
        n_rows = len(self.fb.rows)
        o.process_local_chains()
        self.assertEqual(len(self.fb.rows), n_rows)
        self.assertEqual(self.thinker_calls, 0)


# ------------- (Б) самопочинка шага: retry-перерождение, терминальный halt -------------

class TestChainSelfheal(LocBase):
    def test_fail_retry_rebirth_then_terminal_halt(self):
        pid = self.plan_parent()
        self.exec_step("done", "первый готов")
        o.process_local_chains()                      # релиз шага 2
        self.exec_step("failed", "claude -p упал (exit=1): кривой путь")
        self.thinker_out = ('{"verdict":"retry","fixed_step":"шаг два: взять верный путь",'
                            '"reason":"в шаге был неверный путь"}')
        o.process_local_chains()                      # провал → думатель retry → перерождение
        self.assertEqual(self.thinker_calls, 1)
        reborn = self.fb.chain_news()
        self.assertEqual(len(reborn), 1)
        self.assertTrue(reborn[0]["task_text"].startswith(f"[шаг 2/2 родитель {pid}]"))
        self.assertIn("[самопочинка шага 2, попытка 1]", reborn[0]["task_text"])
        self.assertEqual(reborn[0]["lane"], "pc")
        crd = self.fb.cards(pid)
        self.assertEqual(len(crd), 1)
        self.assertTrue(crd[0]["result"].startswith("🩹"))
        self.assertFalse(self.fb.summaries(pid), "цепь жива — сводки ещё нет")
        self.exec_step("failed", "снова упал")
        o.process_local_chains()                      # повторный провал → терминальный halt
        self.assertEqual(self.thinker_calls, 1, "повторный провал думатель НЕ чинит (петли нет)")
        crd = self.fb.cards(pid)
        self.assertEqual(len(crd), 2)
        self.assertTrue(crd[1]["result"].startswith("🛑"))
        sums = self.fb.summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("1/2", sums[0]["result"])
        self.assertIn("❌", sums[0]["result"])
        self.assertFalse(self.fb.chain_news(), "перерождений/шагов после halt нет")

    def test_thinker_halt_on_first_fail_stops_chain(self):
        pid = self.plan_parent()
        self.exec_step("failed", "исполнительский провал")
        self.thinker_out = '{"verdict":"halt","fixed_step":"","reason":"нужен человек"}'
        o.process_local_chains()
        self.assertEqual(self.thinker_calls, 1)
        self.assertFalse(self.fb.chain_news())
        self.assertEqual(len(self.fb.summaries(pid)), 1)
        crd = self.fb.cards(pid)
        self.assertTrue(crd and "halt" in crd[0]["result"])

    def test_selfheal_off_immediate_halt_without_thinker(self):
        pid = self.plan_parent()
        os.environ["STEP_SELFHEAL"] = "0"
        self.exec_step("failed", "провал")
        o.process_local_chains()
        self.assertEqual(self.thinker_calls, 0)
        self.assertEqual(len(self.fb.summaries(pid)), 1)

    def test_chain_step_fail_not_hijacked_by_single_selfheal(self):
        # провал шага в process_new НЕ уходит одиночному думателю (им владеет надзор цепи)
        self.plan_parent()
        sid = self.exec_step("failed", "провал шага")
        self.assertEqual(self.fb.rows[sid]["status"], "failed")
        self.assertEqual(self.fb.rows[sid]["result"], "провал шага")   # голый failed, без карт
        self.assertEqual(self.task_thinker_calls, 0)
        self.assertEqual(self.thinker_calls, 0, "шаговый думатель — только в тике надзора")


# ---------------- (В) адаптация плана: adjust / finish / лимит дрейфа / off ----------------

class TestChainPlanAdapt(LocBase):
    def test_adjust_card_corrected_release_and_restart_proof(self):
        pid = self.plan_parent("1. первый\n2. второй\n3. третий")
        self.exec_step("done", "первый готов")
        self.adapt_queue = ['{"verdict":"adjust","adjusted_steps":["новый второй","новый третий"],'
                            '"reason":"результат шага 1 изменил остаток"}']
        o.process_local_chains()
        ac = self.fb.adapt_cards(pid)
        self.assertEqual(len(ac), 1)
        self.assertIn("после шага 1", ac[0]["task_text"])
        self.assertEqual(ac[0]["status"], "done")
        self.assertIn("2. новый второй", ac[0]["result"])
        self.assertIn("3. новый третий", ac[0]["result"])
        s = self.fb.chain_news()
        self.assertEqual(len(s), 1)
        self.assertTrue(s[0]["task_text"].startswith(f"[шаг 2/3 родитель {pid}]"))
        self.assertIn("[коррекция плана 1]", s[0]["task_text"])
        self.assertIn("новый второй", s[0]["task_text"])
        # рестарт демона между шагами: план продолжается ИЗ КАРТОЧКИ, не из памяти
        o._loc_adapted.clear()
        o._loc_summarized.clear()
        self.exec_step("done", "новый второй готов")
        self.adapt_calls = 0
        o.process_local_chains()
        s = self.fb.chain_news()
        self.assertEqual(len(s), 1)
        self.assertIn("новый третий", s[0]["task_text"])
        self.assertEqual(self.adapt_calls, 1)
        self.exec_step("done", "новый третий готов")
        o.process_local_chains()
        sums = self.fb.summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("3/3", sums[0]["result"])

    def test_finish_early_summary_no_more_releases(self):
        pid = self.plan_parent("1. первый\n2. второй\n3. третий")
        self.exec_step("done", "первый готов")
        self.adapt_queue = ['{"verdict":"finish","adjusted_steps":[],"reason":"цель уже достигнута"}']
        o.process_local_chains()
        sums = self.fb.summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("завершено досрочно", sums[0]["result"])
        self.assertFalse(self.fb.chain_news(), "шаги 2-3 не релизнуты")

    def test_third_adjust_drift_terminal_halt(self):
        pid = self.plan_parent("1. а\n2. б\n3. в\n4. г")
        adj = '{"verdict":"adjust","adjusted_steps":["з1","з2","з3"],"reason":"дрейф %d"}'
        self.exec_step("done", "ок")
        self.adapt_queue = [adj % 1]
        o.process_local_chains()                     # коррекция 1
        self.exec_step("done", "ок")
        self.adapt_queue = [adj % 2]
        o.process_local_chains()                     # коррекция 2
        self.exec_step("done", "ок")
        self.adapt_queue = [adj % 3]
        o.process_local_chains()                     # коррекция 3 → лимит → halt
        self.assertEqual(len(self.fb.adapt_cards(pid)), 2, "встали только 2 карточки (лимит)")
        halt = [c for c in self.fb.cards(pid) if "план дрейфует" in c["result"]]
        self.assertEqual(len(halt), 1)
        self.assertEqual(len(self.fb.summaries(pid)), 1)
        self.assertFalse(self.fb.chain_news(), "после halt релиза нет")

    def test_adapt_off_release_by_original_plan(self):
        os.environ["PLAN_ADAPT"] = "0"
        pid = self.plan_parent()
        self.exec_step("done", "ок")
        o.process_local_chains()
        self.assertEqual(self.adapt_calls, 0)
        s = self.fb.chain_news()
        self.assertEqual(len(s), 1)
        self.assertTrue(s[0]["task_text"].startswith(f"[шаг 2/2 родитель {pid}]"))


# ------------- (Г) красный шаг: needs_approval ждёт, «нет»/✋ = halt, «да» = продолжение -------------

class TestChainRedStep(LocBase):
    def test_needs_approval_waits_then_reject_halts_without_thinker(self):
        pid = self.plan_parent()
        sid = self.exec_step("needs_approval", "op=other | запись в Лист1: строка байка")
        self.assertEqual(self.fb.rows[sid]["status"], "needs_approval")
        n_rows = len(self.fb.rows)
        o.process_local_chains()
        o.process_local_chains()
        self.assertEqual(self.fb.rows[sid]["status"], "needs_approval")
        self.assertEqual(len(self.fb.rows), n_rows, "демон ждёт Филиппа, ничего не плодит")
        self.assertEqual((self.thinker_calls, self.adapt_calls), (0, 0))
        # «нет N» — devbot финализирует failed с префиксом отказа
        self.fb.complete_task(sid, "failed", "отклонено Филиппом (кнопка)")
        o.process_local_chains()
        self.assertEqual(self.thinker_calls, 0, "отказ владельца думатель не чинит")
        self.assertEqual(len(self.fb.summaries(pid)), 1)
        self.assertFalse(self.fb.chain_news())

    def test_approve_reruns_step_and_chain_continues(self):
        pid = self.plan_parent()
        sid = self.exec_step("needs_approval", "op=other | красный кусок")
        self.fb.rows[sid]["status"] = "approved"
        self.fb.rows[sid]["updated"] = now_iso()
        self.exec_queue.append(("done", "после одобрения сделан"))
        o.process_approved()
        self.assertEqual(self.fb.rows[sid]["status"], "done")
        o.process_local_chains()
        s = self.fb.chain_news()
        self.assertEqual(len(s), 1)
        self.assertTrue(s[0]["task_text"].startswith(f"[шаг 2/2 родитель {pid}]"))

    def test_red_again_after_approve_manual_mark_halts(self):
        pid = self.plan_parent()
        sid = self.exec_step("needs_approval", "op=other | красный кусок")
        self.fb.rows[sid]["status"] = "approved"
        self.fb.rows[sid]["updated"] = now_iso()
        self.exec_queue.append(("needs_approval", "op=other | снова красное"))
        o.process_approved()
        row = self.fb.rows[sid]
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["result"].startswith(o.MANUAL_MARK))
        o.process_local_chains()
        self.assertEqual(self.thinker_calls, 0, "✋-карту думатель не чинит")
        self.assertEqual(len(self.fb.summaries(pid)), 1)
        self.assertFalse(self.fb.chain_news())

    def test_approved_expiry_halts_without_thinker(self):
        pid = self.plan_parent()
        sid = self.exec_step("needs_approval", "op=other | красный кусок")
        self.fb.rows[sid]["status"] = "approved"
        self.fb.rows[sid]["updated"] = now_iso(ago_sec=o.APPROVAL_TTL + 60)
        o.process_approved()
        row = self.fb.rows[sid]
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["result"].startswith(o.TIMEOUT_MARK))
        o.process_local_chains()
        self.assertEqual(self.thinker_calls, 0, "⏱-просрочку approve думатель не чинит")
        self.assertEqual(len(self.fb.summaries(pid)), 1)

    def test_needs_approval_timeout_halts_without_thinker(self):
        pid = self.plan_parent()
        sid = self.exec_step("needs_approval", "op=other | красный кусок")
        self.fb.rows[sid]["updated"] = now_iso(ago_sec=o.APPROVAL_TTL + 60)
        o.process_approval_timeouts()
        row = self.fb.rows[sid]
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["result"].startswith(o.TIMEOUT_MARK))
        o.process_local_chains()
        self.assertEqual(self.thinker_calls, 0)
        self.assertEqual(len(self.fb.summaries(pid)), 1)


# ------- (Д) локальный аналог «ПК молчит»: застрявший шаг реапится ⏱ и глушит думателя -------

class TestChainStuckTimeout(LocBase):
    def test_stuck_in_progress_step_reaped_and_chain_halts(self):
        pid = self.plan_parent()
        step = self.fb.chain_news()[0]
        step_id = step["id"]
        self.fb.rows[step_id]["status"] = "in_progress"        # демон умер посреди прогона
        self.fb.rows[step_id]["updated"] = now_iso(ago_sec=o.PC_SINGLE_STALE + 60)
        o.process_stuck_singles()
        row = self.fb.rows[step_id]
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["result"].startswith(o.TIMEOUT_MARK))
        o.process_local_chains()
        self.assertEqual(self.thinker_calls, 0, "⏱-диагноз ливнесса думатель не чинит")
        sums = self.fb.summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("0/2", sums[0]["result"])
        o.process_local_chains()                               # цепь закрыта, повторов нет
        self.assertEqual(len(self.fb.summaries(pid)), 1)
        self.assertEqual(self.thinker_calls, 0)


# ---------- (Е) регресс: PC_LOCAL_DEC=0 байт-в-байт + изоляция чужого на полосе pc ----------

class TestFlagOffAndIsolation(LocBase):
    def test_flag_off_parent_runs_old_path_byte_identical(self):
        os.environ["PC_LOCAL_DEC"] = "0"
        pid = self.new_parent()
        self.exec_queue.append(("done", "RESULT: ок"))
        o.process_new()
        self.assertEqual(self.fb.rows[pid]["status"], "done")
        self.assertEqual(self.fb.rows[pid]["result"], "RESULT: ок")
        self.assertEqual(self.planner_prompts, [], "планировщик не зовётся вовсе")

    def test_flag_off_parent_fail_stays_bare_failed(self):
        os.environ["PC_LOCAL_DEC"] = "0"
        pid = self.new_parent()
        self.exec_queue.append(("failed", "упал как обычная задача"))
        o.process_new()
        self.assertEqual(self.fb.rows[pid]["status"], "failed")
        self.assertEqual(self.fb.rows[pid]["result"], "упал как обычная задача")
        self.assertEqual((self.task_thinker_calls, self.thinker_calls), (0, 0))

    def test_flag_off_supervision_does_not_even_read_queue(self):
        os.environ["PC_LOCAL_DEC"] = "0"
        tid = self.fb.enqueue_task(o.PC_LOCAL_DEC_FROM, "[шаг 1/2 родитель 9] кусок")["id"]
        self.fb.rows[tid]["status"] = "failed"
        self.fb.calls = 0
        n_rows = len(self.fb.rows)
        o.process_local_chains()
        self.assertEqual(self.fb.calls, 0, "флаг off → надзор очередь даже не читает")
        self.assertEqual(len(self.fb.rows), n_rows)
        self.assertEqual(self.thinker_calls, 0)

    def test_vps_theatre_chain_untouched_by_supervision(self):
        # цепь VPS-театра (from=Filipp-pc-dec) на той же полосе: надзор её НЕ группирует
        sid = self.fb.enqueue_task("Filipp-pc-dec", "[шаг 1/2 родитель 55] сделай на ПК")["id"]
        fid = self.fb.enqueue_task("Filipp-pc-dec", "[шаг 2/2 родитель 55] хвост")["id"]
        self.fb.rows[fid]["status"] = "failed"
        self.fb.rows[fid]["result"] = "упал"
        n_rows = len(self.fb.rows)
        o.process_local_chains()
        self.assertEqual(len(self.fb.rows), n_rows, "ни сводок, ни карт, ни перерождений")
        self.assertFalse(self.fb.summaries(55))
        self.assertFalse(self.fb.cards(55))
        self.assertEqual(self.thinker_calls, 0)
        self.assertEqual(self.fb.rows[fid]["status"], "failed")
        self.assertEqual(self.fb.rows[fid]["result"], "упал")
        # а new-шаг чужого театра process_new исполняет ОБЫЧНОЙ задачей (не планирует, не доводит)
        self.exec_queue.append(("done", "RESULT: сделан ПК-исполнителем"))
        o.process_new()
        self.assertEqual(self.fb.rows[sid]["status"], "done")
        self.assertEqual(self.fb.rows[sid]["result"], "RESULT: сделан ПК-исполнителем")
        self.assertEqual(self.planner_prompts, [])

    def test_lone_pc_single_untouched_by_supervision(self):
        tid = self.fb.enqueue_task("Filipp-pc", "одиночная задача ПК")["id"]
        o.process_local_chains()
        self.assertEqual(self.fb.rows[tid]["status"], "new")
        self.assertEqual((self.thinker_calls, self.adapt_calls), (0, 0))

    def test_orphan_synthetic_finalized_not_executed(self):
        # осиротевшая synthetic (демон упал между enqueue и complete) → довести done, не исполнять
        pid = self.plan_parent()
        self.exec_step("done", "первый готов")        # шаг 1 исполнен штатно
        rid = self.fb.enqueue_task(o.PC_LOCAL_DEC_FROM,
                                   f"[сводка родитель {pid}] сводный отчёт")["id"]

        def boom(tid, text, note=""):
            raise AssertionError("run_task не должен исполнять synthetic")
        o.run_task = boom
        o.process_new()                               # FIFO дошёл до осиротевшей сводки
        row = self.fb.rows[rid]
        self.assertEqual(row["status"], "done")
        self.assertIn("Сводка декомпозиции", row["result"])


# ---- (Ж) вотчдог застрявшей МЕЖДУ ШАГАМИ цепи: потерянный релиз → reconcile-досдвиг ----
# Инцидент 15.07 (cowork_log 00:35): цепь 365 стоит на 3/7 с 23:06 — последний шаг done, следующий
# НЕ релизнут, 0 in_progress; штатный тик релизит СОБЫТИЙНО, а событие потеряно → цепь висит навсегда,
# stuck-single (in_progress) слеп. Вотчдог process_stuck_chains досдвигает reconcile'ом ИЗ ОЧЕРЕДИ.

class TestChainWatchdogStuck(LocBase):
    def _wd_cards(self):
        return [t for (_pid, t) in self.chain_cards if str(t).startswith("🩺")]

    def test_lost_release_watchdog_continues_chain_summary_not_duplicated(self):
        # ГОЛДЕН: шаг done + ПОТЕРЯННЫЙ релиз следующего → вотчдог продолжает цепь; финал-сводка ×1.
        pid = self.plan_parent("1. первый\n2. второй\n3. третий")
        self.exec_step("done", "первый готов")
        o.process_local_chains()                                   # штатно релизит шаг 2
        sid2 = self.exec_step("done", "второй готов")              # шаг 2 done
        # РЕЛИЗ ШАГА 3 ПОТЕРЯН: штатный тик НЕ отработал (событие пропало / демон уснул в consult).
        self.assertFalse(self.fb.chain_news(), "шаг 3 не релизнут — цепь застряла между шагами")
        # свежий done → вотчдог ещё молчит (не гонка со штатным тиком)
        o.process_stuck_chains()
        self.assertFalse(self.fb.chain_news(), "done свежий → вотчдог ждёт PC_CHAIN_STALE")
        self.assertEqual(self._wd_cards(), [])
        # шаг 2 висит done дольше порога → вотчдог досдвигает шаг 3 из плана очереди
        self.fb.rows[sid2]["updated"] = now_iso(ago_sec=o.PC_CHAIN_STALE + 60)
        o.process_stuck_chains()
        news = self.fb.chain_news()
        self.assertEqual(len(news), 1)
        self.assertTrue(news[0]["task_text"].startswith(f"[шаг 3/3 родитель {pid}]"))
        self.assertIn("третий", news[0]["task_text"])
        self.assertEqual(news[0]["lane"], "pc")
        self.assertEqual(self.thinker_calls, 0, "вотчдог думателя-самопочинки НЕ зовёт")
        self.assertTrue(self._wd_cards() and f"#{pid}" in self._wd_cards()[0], "🩺-карточка владельцу")
        # цепь снова движется → вотчдог её больше не трогает (шаг 3 open)
        n_rows = len(self.fb.rows)
        o.process_stuck_chains()
        self.assertEqual(len(self.fb.rows), n_rows, "открытый шаг → вотчдог молчит (нет дублей)")
        # шаг 3 исполнен → штатный финал → сводка ×1
        self.exec_step("done", "третий готов")
        o.process_local_chains()
        sums = self.fb.summaries(pid)
        self.assertEqual(len(sums), 1)
        self.assertIn("3/3 шагов done", sums[0]["result"])
        # ФИНАЛ-СВОДКА НЕ ДУБЛИРУЕТСЯ: лишние проходы вотчдога/тика/рестарт кэшей — сводка остаётся ×1
        o.process_stuck_chains()
        o.process_local_chains()
        o._loc_summarized.clear()
        o.process_stuck_chains()
        self.assertEqual(len(self.fb.summaries(pid)), 1)
        self.assertFalse(self.fb.chain_news(), "цепь закрыта — новых шагов вотчдог не плодит")

    def test_watchdog_ignores_failed_last_step(self):
        # последний шаг failed — епархия _loc_after_fail (halt/самопочинка), НЕ вотчдога
        pid = self.plan_parent()
        sid = self.exec_step("failed", "провал шага")
        self.fb.rows[sid]["updated"] = now_iso(ago_sec=o.PC_CHAIN_STALE + 60)
        n_rows = len(self.fb.rows)
        o.process_stuck_chains()
        self.assertEqual(len(self.fb.rows), n_rows, "failed вотчдог не досдвигает и не плодит")
        self.assertFalse(self.fb.summaries(pid))
        self.assertEqual((self.thinker_calls, self.adapt_calls), (0, 0))
        self.assertEqual(self._wd_cards(), [])

    def test_watchdog_ignores_open_step(self):
        # есть открытый шаг (цепь движется) → вотчдог молчит даже при протухшем updated
        pid = self.plan_parent()
        sid = self.fb.chain_news()[0]["id"]
        self.fb.rows[sid]["updated"] = now_iso(ago_sec=o.PC_CHAIN_STALE + 60)
        n_rows = len(self.fb.rows)
        o.process_stuck_chains()
        self.assertEqual(len(self.fb.rows), n_rows)
        self.assertFalse(self.fb.summaries(pid))
        self.assertEqual(self._wd_cards(), [])

    def test_watchdog_no_release_when_plan_exhausted(self):
        # последний шаг плана done+протух, сводки нет → вотчдог НЕ релизит и НЕ делает сводку
        # (финал — работа штатного тика; вотчдог только досдвигает ОСТАВШИЕСЯ шаги)
        pid = self.plan_parent("1. первый\n2. второй")
        self.exec_step("done", "первый готов")
        o.process_local_chains()                                   # релиз шага 2
        sid2 = self.exec_step("done", "второй готов")              # последний шаг done
        self.fb.rows[sid2]["updated"] = now_iso(ago_sec=o.PC_CHAIN_STALE + 60)
        n_rows = len(self.fb.rows)
        o.process_stuck_chains()
        self.assertEqual(len(self.fb.rows), n_rows, "план исчерпан → вотчдог не трогает (сводка — тик)")
        self.assertFalse(self.fb.summaries(pid))
        self.assertEqual(self._wd_cards(), [])
        # а штатный тик закрывает цепь сводкой как обычно
        o.process_local_chains()
        self.assertEqual(len(self.fb.summaries(pid)), 1)

    def test_watchdog_flag_off_does_not_read_queue(self):
        os.environ["PC_LOCAL_DEC"] = "0"
        tid = self.fb.enqueue_task(o.PC_LOCAL_DEC_FROM, "[шаг 1/2 родитель 9] кусок")["id"]
        self.fb.rows[tid]["status"] = "done"
        self.fb.rows[tid]["updated"] = now_iso(ago_sec=o.PC_CHAIN_STALE + 60)
        self.fb.calls = 0
        n_rows = len(self.fb.rows)
        o.process_stuck_chains()
        self.assertEqual(self.fb.calls, 0, "флаг off → вотчдог очередь даже не читает")
        self.assertEqual(len(self.fb.rows), n_rows)

    def test_watchdog_ignores_vps_theatre_chain(self):
        # цепь VPS-театра (from=Filipp-pc-dec) на той же полосе: вотчдог её НЕ группирует
        tid = self.fb.enqueue_task("Filipp-pc-dec", "[шаг 1/3 родитель 55] кусок")["id"]
        self.fb.rows[tid]["status"] = "done"
        self.fb.rows[tid]["updated"] = now_iso(ago_sec=o.PC_CHAIN_STALE + 60)
        n_rows = len(self.fb.rows)
        o.process_stuck_chains()
        self.assertEqual(len(self.fb.rows), n_rows, "чужой театр вотчдог не досдвигает")
        self.assertEqual(self._wd_cards(), [])

    def test_watchdog_restart_proof_no_double_release(self):
        # рестарт демона (кэши пусты) между досдвигом и персистом: повторный reconcile не плодит дубль
        pid = self.plan_parent("1. первый\n2. второй\n3. третий")
        self.exec_step("done", "первый готов")
        o.process_local_chains()
        sid2 = self.exec_step("done", "второй готов")
        self.fb.rows[sid2]["updated"] = now_iso(ago_sec=o.PC_CHAIN_STALE + 60)
        o.process_stuck_chains()                                   # досдвиг шага 3
        self.assertEqual(len(self.fb.chain_news()), 1)
        o._loc_summarized.clear()
        o._loc_adapted.clear()
        n_rows = len(self.fb.rows)
        o.process_stuck_chains()                                   # «после рестарта» — шаг 3 уже open
        self.assertEqual(len(self.fb.rows), n_rows, "открытый шаг 3 → повторного релиза нет")
        self.assertEqual(len(self.fb.chain_news()), 1)


if __name__ == "__main__":
    unittest.main()
