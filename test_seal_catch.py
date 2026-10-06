# -*- coding: utf-8 -*-
"""
test_seal_catch.py — ДОГОН ТЕРМИНАЛА И ЗАМОК ВЛАДЕЛЬЦА НА ЗАПЕЧАТАННОМ РЯДЕ (SEALCATCH0610).

ЖИВОЙ ПОВОД — ряд #41 полосы pc, 05.10.2026 (UTC; в pc_orchestrator.log местное +7):
    19:42:36  «COMPLETE id=41 status=failed» — а связи не было 19:42 → 20:13, мост расписку не принял;
    20:20:58  «stuck-single: id=41 НЕ СУЖУ — закрыта нами … (расписка моста: НЕ ПРИНЯТА)»;
    20:05 → 02:55  ящик Штаба: «в очереди задача владельца (#41) — заданий Штаба не берём ни одного»;
    06.10 03:04  ряд закрыт владельцем руками.
Печать стояла верно, сторож верно молчал — и закрытие не повторял НИКТО, а замок владельца
(`_is_owner_work`) печати не спрашивал. Файл держит обе половины лечения:
  1) ДОГОН: печать failed + расписка не принята + мост отдаёт ряд in_progress + связь есть + окно
     прошло → РОВНО ОДИН `complete_task(41, 'failed', тот же текст)`, затем ЧТЕНИЕ статуса и печать
     ok=True; НОЛЬ вызовов — мост уже закрыл ряд (в том числе когда полоса лишь ОТСТАЛА) · печати нет ·
     связи нет · второй виток в окне · ряд моложе печати · время не разобрать · расписка была принята ·
     учёт попытки не лёг;
  2) ЗАМОК: запечатанный ряд in_progress, не моложе печати, замок владельца не держит;
     незапечатанный, approved/needs_approval, моложе печати, без времени — держит.
МУТАНТЫ — на НАСТОЯЩЕМ исходнике с одной вырезанной проверкой; каждый обязан быть пойман.

Мост, очередь и связь — только подделкой; боевой реестр печатей не трогается: каждый модуль
(настоящий и мутант) получает свой временный `TASK_START_FILE`.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe test_seal_catch.py
"""

import datetime
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (как в соседних файлах)

import pc_orchestrator as o            # noqa: E402
import recon_auto_run as run           # noqa: E402

TID = 41
LIVE_RESULT = ("network_outage: связи не было 19:33–19:42 UTC; задание повторено ключом "
               "012bb-7ef.0510")                # дословный итог ряда #41 (закрытие 06.10 03:04 UTC)
LIVE_TEXT = "[от Штаба дата=2026-10-06 ключ=012b6-7ea.0510] ЗАДАНИЕ ШТАБА ИЗ ЯЩИКА"
UTC = datetime.timezone.utc
WINDOW = getattr(o, "SEAL_CATCH_SEC", 900)


class FakeBridge:
    """Мост-подделка: ряды полосы, запись complete и чтение статуса. Журнал событий — по порядку,
    чтобы «сначала чтение, потом закрытие, потом чтение» проверялось, а не подразумевалось.

    `lag=True` — полоса ОТСТАЛА (живой id=12): листинг in_progress отдаёт ряд, хотя истинный статус
    уже другой. `read_script` — ответы `_in_status` по порядку (None — чтение не удалось)."""

    def __init__(self, status="in_progress", updated=None, receipt_ok=False, lands=True,
                 readable=True, lag=False, verified=False, read_script=None):
        self.rows = {TID: {"id": TID, "lane": "pc", "from": "Filipp-shtab", "status": status,
                           "updated": updated, "task_text": LIVE_TEXT, "result": ""}}
        self.completed, self.events, self.reads = [], [], 0
        self.receipt_ok, self.lands, self.readable = receipt_ok, lands, readable
        self.lag, self.verified = lag, verified
        self.read_script = list(read_script or [])

    def _listed(self, r):
        return "in_progress" if self.lag else r["status"]

    def get_pending(self, status, lane="pc"):
        self.reads += 1
        return {"ok": True, "items": [dict(r, status=self._listed(r)) for r in self.rows.values()
                                      if self._listed(r) == status]}

    def complete_task(self, tid, status, result):
        self.completed.append((tid, status, result))
        self.events.append(("complete", tid, status))
        if self.lands and tid in self.rows:
            self.rows[tid]["status"], self.rows[tid]["result"] = status, result
        if self.verified:
            return {"ok": True, "verified_by_fact": True, "receipt_error": "BridgeReceiptLost"}
        if self.receipt_ok:
            return {"ok": True}
        return {"ok": False, "error": "BridgeReceiptLost", "error_kind": "bridge",
                "error_text": "мост ответил, но обмен не завершён"}

    def _in_status(self, tid, status):
        self.events.append(("read", tid, status))
        if self.read_script:
            v = self.read_script.pop(0)
            if v != "real":
                return v
        elif not self.readable:
            return None
        r = self.rows.get(tid)
        return bool(r and r["status"] == status)


def prep(test, m):
    """Изолировать модуль `m` (настоящий или мутант): свой временный реестр, мост-подделка, чистая
    связь, ни журнала, ни карточек. Всё возвращается на место после теста."""
    tmp = tempfile.mkdtemp(prefix="seal_catch_")
    names = ("bc", "_cowork", "_notify_task", "_stopped", "_lesson_wait_ids", "_work_evidence",
             "TASK_START_FILE", "COWORK_LEDGER")
    saved = {k: getattr(m, k) for k in names}
    m._cowork = lambda *a, **k: None
    m._notify_task = lambda *a, **k: None
    m._stopped = lambda: False
    m._lesson_wait_ids = lambda *a, **k: set()
    m._work_evidence = lambda since, until=None: {"commits": [], "journal": []}
    m.TASK_START_FILE = os.path.join(tmp, "task_started.json")
    m.COWORK_LEDGER = os.path.join(tmp, "cowork_log.ledger")
    sets = [getattr(m, n, None) for n in ("_SEAL_SAID", "_SEAL_LAG_SAID", "_SEAL_NOTE_SAID")]
    sets = [s for s in sets if s is not None]
    m._link.reset()
    for s in sets:
        s.clear()

    def restore():
        for k, v in saved.items():
            setattr(m, k, v)
        m._link.reset()
        for s in sets:
            s.clear()
    test.addCleanup(restore)
    return tmp


def iso_ago(now, sec):
    return (now - datetime.timedelta(seconds=sec)).isoformat()


def scene(m, *, seal=True, seal_ok=False, seal_age=None, row_age=None, status="in_progress",
          head=LIVE_RESULT, updated=False, **fake):
    """Живая сцена #41: ряд взят (row_age назад), закрыт нами failed (seal_age назад, по умолчанию
    за окном) с распиской по выбору; мост отдаёт ряд в статусе `status`. → (мост, now)."""
    win = getattr(m, "SEAL_CATCH_SEC", 900)
    seal_age = win + 300 if seal_age is None else seal_age
    row_age = win + 600 if row_age is None else row_age
    now = datetime.datetime.now(UTC)
    fb = FakeBridge(status=status, updated=iso_ago(now, row_age) if updated is False else updated, **fake)
    m.bc = fb
    if seal:
        kw = {"head": head} if head is not None else {}
        m._task_closed_mark(TID, "failed", ok=seal_ok,
                            now=now - datetime.timedelta(seconds=seal_age), **kw)
    return fb, now


def link_lost(m):
    """Живая форма обрыва: молчат ДВА канала (мост + API) — вердикт «связи нет»."""
    m._link.witness(m.LINK_CH_BRIDGE, False)
    m._link.witness(m.LINK_CH_API, False)


def link_unsure(m):
    """Один канал отказал дважды — «связь не подтверждена». Сторож полосу при этом ЧИТАЕТ
    (обрыва нет), поэтому проверку связи догон обязан делать сам."""
    m._link.witness(m.LINK_CH_API, False)
    m._link.witness(m.LINK_CH_API, False)


def seal_of(m=o):
    return m._task_started_rec(TID)["closed"]


# ═══════════════════════════════ 1. ДОГОН ТЕРМИНАЛА ═══════════════════════════════════════════

class TestCatchUpLive41(unittest.TestCase):
    """Положительный путь на живой сцене: чтение, один вызов, чтение, печать ok=True."""

    def setUp(self):
        prep(self, o)

    def test_one_complete_then_read_then_seal_ok(self):
        fb, now = scene(o)
        with self.assertLogs("pc_orchestrator", level="WARNING") as cm:
            o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [(TID, "failed", LIVE_RESULT)],
                         "ровно один complete_task тем же статусом и тем же текстом")
        self.assertEqual(fb.events, [("read", TID, "failed"), ("complete", TID, "failed"),
                                     ("read", TID, "failed")],
                         "до закрытия — проверка, что его ещё нет; после — что оно легло")
        seal = seal_of()
        self.assertTrue(seal["ok"], "подтверждённый факт снимает «расписка не принята»")
        self.assertIn("caught", seal)
        self.assertEqual(seal["catch_n"], 1)
        self.assertNotIn("head", seal, "после подтверждения реестр не держит текст отчёта")
        self.assertTrue(any("ТЕРМИНАЛ ДОГНАН id=41" in line for line in cm.output), cm.output)
        self.assertEqual(fb.rows[TID]["status"], "failed")

    def test_seal_without_text_sends_dated_catch_up_line(self):
        # Печати, поставленные ДО правки (ровно как у живого #41), текста не несут.
        fb, now = scene(o, head=None)
        o.process_stuck_singles(now=now)
        self.assertEqual(len(fb.completed), 1)
        text = fb.completed[0][2]
        self.assertTrue(text.startswith("догон терминала %s" % now.isoformat()), text)
        self.assertIn("failed", text)

    def test_after_catch_up_the_next_cycle_calls_nothing(self):
        # Мост закрыл ряд — дальше в полосе in_progress его нет, и догонять нечего.
        fb, now = scene(o)
        o.process_stuck_singles(now=now)
        o.process_stuck_singles(now=now + datetime.timedelta(seconds=WINDOW + 60))
        self.assertEqual(len(fb.completed), 1)

    def test_verified_by_fact_counts_as_confirmation(self):
        # Настоящий complete_task при потерянной расписке сам читает статус и отвечает
        # verified_by_fact. Такое доказательство не смеет отменить второе неудачное чтение.
        fb, now = scene(o, verified=True, read_script=[False, None])
        o.process_stuck_singles(now=now)
        self.assertEqual(len(fb.completed), 1)
        self.assertTrue(seal_of()["ok"])
        self.assertEqual(o._seal_catch_up(fb.rows[TID], now=now + datetime.timedelta(seconds=WINDOW * 3)),
                         "расписка принята — догонять нечего")


class TestCatchUpText(unittest.TestCase):
    """Текст повтора — ровно тот, что получил бы мост: хвостовые маркеры итога и план цепи живут в
    result, и голова их теряла бы (находка проверки 06.10)."""

    def setUp(self):
        prep(self, o)

    def test_first_close_stores_text_only_when_receipt_refused(self):
        fb = FakeBridge(receipt_ok=False)
        o._complete(7, "failed", "итог семь", bridge=fb)
        self.assertEqual(o._task_started_rec(7)["closed"]["head"], "итог семь")
        o._complete(8, "done", "итог восемь", bridge=FakeBridge(receipt_ok=True))
        self.assertNotIn("head", o._task_started_rec(8)["closed"])

    def test_text_up_to_queue_cap_is_resent_whole(self):
        tail = "Следов работы в окне задачи нет (коммитов 0, записей журнала 0)."
        full = "📡 СВЯЗИ НЕ БЫЛО " + "х" * 3000 + " [причина=network_outage] " + tail
        self.assertLessEqual(len(full), o.RESULT_MAX)
        fb, now = scene(o, head=full)
        o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed[0][2], full, "хвост итога доехал целиком")
        self.assertTrue(fb.completed[0][2].endswith(tail))

    def test_text_over_queue_cap_is_cut_by_the_queue_rule(self):
        long = "строка отчёта. " * 700
        self.assertGreater(len(long), o.RESULT_MAX)
        with mock.patch.object(o, "_cap_result", lambda s, tid=None: "РЕЗ ОЧЕРЕДИ:" + str(tid)):
            o._complete(9, "failed", long, bridge=FakeBridge(receipt_ok=False))
        self.assertEqual(o._task_started_rec(9)["closed"]["head"], "РЕЗ ОЧЕРЕДИ:9",
                         "второго правила обрезки нет: длинный текст режет тот же _cap_result")

    def test_second_attempt_sends_the_same_text_and_keeps_first_close_time(self):
        fb, now = scene(o, lands=False)
        first = seal_of()["at"]
        o.process_stuck_singles(now=now)
        o.process_stuck_singles(now=now + datetime.timedelta(seconds=WINDOW + 5))
        self.assertEqual([c[2] for c in fb.completed], [LIVE_RESULT, LIVE_RESULT])
        self.assertEqual(seal_of()["first_at"], first)


class TestCatchUpZeroCalls(unittest.TestCase):
    """Случаи, где вызова быть НЕ должно."""

    def setUp(self):
        prep(self, o)

    def test_bridge_already_closed_the_row(self):
        fb, now = scene(o, status="failed")
        o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [])

    def test_lagging_lane_row_already_closed_no_second_complete(self):
        # Живой id=12: закрытие легло, а листинг in_progress ещё отдаёт ряд.
        fb, now = scene(o, status="failed", lag=True)
        with self.assertLogs("pc_orchestrator", level="WARNING") as cm:
            o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [], "второй complete затёр бы уже лёгший result")
        self.assertTrue(seal_of()["ok"])
        self.assertTrue(any("повтор не понадобился" in line for line in cm.output), cm.output)

    def test_no_seal(self):
        fb, now = scene(o, seal=False, row_age=60)      # моложе порога сироты: сторож молчит сам
        o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [])
        self.assertEqual(o._seal_catch_up(fb.rows[TID], now=now), "печать: none")
        self.assertEqual(fb.completed, [])

    def test_no_link_lost(self):
        fb, now = scene(o)
        link_lost(o)
        o.process_stuck_singles(now=now)
        self.assertEqual((fb.reads, fb.completed), (0, []))
        self.assertTrue(o._seal_catch_up(fb.rows[TID], now=now).startswith("связь:"))
        self.assertEqual(fb.completed, [])

    def test_no_link_unsure_while_the_lane_is_read(self):
        fb, now = scene(o)
        link_unsure(o)
        o.process_stuck_singles(now=now)
        self.assertGreater(fb.reads, 0, "сторож полосу прочитал — значит проверку связи делает догон")
        self.assertEqual(fb.completed, [])

    def test_fresh_seal_waits_for_the_window(self):
        # Первая попытка тоже ждёт окна: отсчёт идёт и от самого закрытия.
        fb, now = scene(o, seal_age=300, row_age=600)
        o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [])
        self.assertTrue(o._seal_catch_up(fb.rows[TID], now=now).startswith("окно:"))

    def test_second_cycle_inside_the_window(self):
        # Не подтверждено: запись не легла, чтение говорит «не в этом статусе».
        fb, now = scene(o, lands=False)
        o.process_stuck_singles(now=now)
        self.assertEqual(len(fb.completed), 1)
        self.assertFalse(seal_of()["ok"], "не подтверждено — печать «не принята» остаётся")
        o.process_stuck_singles(now=now + datetime.timedelta(seconds=60))
        o.process_stuck_singles(now=now + datetime.timedelta(seconds=WINDOW - 5))
        self.assertEqual(len(fb.completed), 1, "внутри окна повтора нет")
        # Контроль на пустоту: за окном повтор ЕСТЬ — иначе тест выше зеленел бы и на мёртвой ветке.
        o.process_stuck_singles(now=now + datetime.timedelta(seconds=WINDOW + 5))
        self.assertEqual(len(fb.completed), 2)
        self.assertEqual(seal_of()["catch_n"], 2)

    def test_unreadable_status_defers_without_mutation(self):
        fb, now = scene(o, readable=False)
        o.process_stuck_singles(now=now)
        o.process_stuck_singles(now=now + datetime.timedelta(seconds=120))
        self.assertEqual(fb.completed, [], "статус не прочитан — вслепую не шлём")
        self.assertEqual(seal_of()["catch_n"], 1, "попытка учтена: следующее чтение — через окно")

    def test_unknown_after_complete_waits_for_the_window(self):
        fb, now = scene(o, lands=False, read_script=[False, None])
        o.process_stuck_singles(now=now)
        o.process_stuck_singles(now=now + datetime.timedelta(seconds=120))
        self.assertEqual(len(fb.completed), 1)
        self.assertFalse(seal_of()["ok"])

    def test_receipt_accepted_but_read_not_confirming_keeps_seal_open(self):
        fb, now = scene(o, lands=False, receipt_ok=True)
        o.process_stuck_singles(now=now)
        self.assertEqual(len(fb.completed), 1)
        self.assertFalse(seal_of()["ok"])

    def test_row_younger_than_seal(self):
        fb, now = scene(o, seal_age=WINDOW + 600, row_age=60)    # номер переиспользован
        o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [])
        self.assertEqual(o._seal_catch_up(fb.rows[TID], now=now), "печать: younger")

    def test_row_time_unknown(self):
        for bad in (None, "не-дата"):
            fb, now = scene(o, updated=bad)
            o.process_stuck_singles(now=now)
            self.assertEqual(fb.completed, [], repr(bad))
            self.assertEqual(o._seal_catch_up(fb.rows[TID], now=now), "печать: unknown")

    def test_receipt_was_accepted(self):
        fb, now = scene(o, seal_ok=True)
        o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [])

    def test_attempt_not_recorded_means_no_mutation(self):
        fb, now = scene(o)
        with mock.patch.object(o, "_task_started_write", lambda st, tid, path=None: False):
            o.process_stuck_singles(now=now)
            o.process_stuck_singles(now=now + datetime.timedelta(seconds=60))
            o.process_stuck_singles(now=now + datetime.timedelta(seconds=WINDOW * 2))
        self.assertEqual(fb.completed, [], "без учёта попытки окно не отсчитать — повтор был бы слепым")

    def test_switch_off(self):
        fb, now = scene(o)
        with mock.patch.object(o, "SEAL_CATCH_SEC", 0):
            o.process_stuck_singles(now=now)
        self.assertEqual(fb.completed, [])

    def test_catch_up_goes_through_the_single_road(self):
        # Догон не обходит `_complete`: замок «каждое закрытие печатается» остаётся в силе.
        import inspect
        src = inspect.getsource(o._seal_catch_up)
        self.assertIn("_complete(", src)
        self.assertNotIn(".complete_task(", src)


# ═══════════════════════════════ 2. ЗАМОК ВЛАДЕЛЬЦА ═══════════════════════════════════════════

def owner_row(now, tid=TID, row_age=600, status="in_progress", updated=False):
    return {"id": tid, "lane": "pc", "from": "Filipp-shtab", "status": status,
            "updated": iso_ago(now, row_age) if updated is False else updated, "task_text": LIVE_TEXT}


class TestOwnerBusy(unittest.TestCase):
    """`owner_busy` — общий замок ящика Штаба и ступени E (`shtab_box_run.Queue` его наследует)."""

    def setUp(self):
        prep(self, o)
        self.q = run.Queue(daemon=o)
        self.now = datetime.datetime.now(UTC)

    def _seal(self, age=300, status="failed", ok=False, path=None):
        o._task_closed_mark(TID, status, ok=ok, now=self.now - datetime.timedelta(seconds=age),
                            path=path)

    def test_sealed_row_does_not_hold_the_lock(self):
        self._seal()
        with self.assertLogs("pc_orchestrator", level="WARNING") as cm:
            self.assertEqual(self.q.owner_busy([owner_row(self.now)]), (False, []))
        self.assertTrue(any("ряд #41 — закрыт нами, мост отстал" in line for line in cm.output),
                        cm.output)

    def test_unsealed_row_holds_the_lock(self):
        self.assertEqual(self.q.owner_busy([owner_row(self.now, tid=42)]), (True, [42]))

    def test_mixed_rows_only_the_unsealed_one_counts(self):
        self._seal()
        busy = self.q.owner_busy([owner_row(self.now), owner_row(self.now, tid=42)])
        self.assertEqual(busy, (True, [42]))

    def test_row_younger_than_seal_still_holds(self):
        self._seal(age=600)
        self.assertEqual(self.q.owner_busy([owner_row(self.now, row_age=60)]), (True, [TID]))

    def test_row_time_unknown_still_holds(self):
        self._seal()
        for bad in (None, "не-дата"):
            self.assertEqual(self.q.owner_busy([owner_row(self.now, updated=bad)]), (True, [TID]))

    def test_approved_and_needs_approval_still_hold(self):
        # approved демон исполнит заново, needs_approval ждёт «да»: печать там не терминал.
        self._seal()
        for st in ("approved", "needs_approval", "new"):
            self.assertEqual(self.q.owner_busy([owner_row(self.now, status=st)]), (True, [TID]), st)

    def test_accepted_receipt_with_open_row_also_releases(self):
        # Живой id=12 (05.09): расписка принята, а мост всё равно отдаёт ряд открытым.
        self._seal(status="done", ok=True)
        self.assertEqual(self.q.owner_busy([owner_row(self.now)]), (False, []))

    def test_lag_line_is_said_once_per_seal(self):
        self._seal()
        with self.assertLogs("pc_orchestrator", level="WARNING") as cm:
            self.q.owner_busy([owner_row(self.now)])
            self.q.owner_busy([owner_row(self.now)])
            o.log.warning("контроль")             # assertLogs требует хоть одну строку
        self.assertEqual(sum("мост отстал" in line for line in cm.output), 1)

    def test_old_daemon_without_the_function_keeps_the_old_lock(self):
        class OldDaemon:
            def _revizor_chain_pids(self, rows):
                return set()

            def _is_owner_work(self, it, pids):
                return True
        self.assertEqual(run.Queue(daemon=OldDaemon()).owner_busy([owner_row(self.now)]), (True, [TID]))

    def test_the_box_inherits_this_lock(self):
        import shtab_box_run
        self.assertTrue(issubclass(shtab_box_run.Queue, run.Queue))
        self.assertNotIn("owner_busy", shtab_box_run.Queue.__dict__,
                         "ящик переопределил замок — правка до него не доехала бы")

    def test_registry_path_is_resolved_at_call_time(self):
        with mock.patch.object(o, "_TASK_START_FROZEN", o.TASK_START_FILE), \
                mock.patch.object(o, "_state", lambda p: "<в момент зова>" + os.path.basename(p)):
            self.assertEqual(o._seal_registry_path(), "<в момент зова>pc_orchestrator.task_started.json")
        with mock.patch.object(o, "_TASK_START_FROZEN", "X:/frozen.json"):
            self.assertEqual(o._seal_registry_path(), o.TASK_START_FILE)


def borrowed_copy_scene(test, m_daemon, queue_cls):
    """ЛОВУШКА ОДОЛЖЕННОЙ КОПИИ, проводкой целиком: `TASK_START_FILE` копии == замороженному при
    импорте пути (временный, печати там НЕТ), а печать лежит там, куда указывает `_state` в момент
    зова. → ответ `owner_busy` по запечатанному ряду."""
    prep(test, m_daemon)
    now = datetime.datetime.now(UTC)
    live = os.path.join(tempfile.mkdtemp(prefix="seal_catch_live_"), "pc_orchestrator.task_started.json")
    m_daemon._task_closed_mark(TID, "failed", ok=False, now=now - datetime.timedelta(seconds=300),
                               path=live)
    with mock.patch.object(m_daemon, "_TASK_START_FROZEN", m_daemon.TASK_START_FILE), \
            mock.patch.object(m_daemon, "_state", lambda p: live):
        return queue_cls(daemon=m_daemon).owner_busy([owner_row(now)])


class TestBorrowedCopyWiring(unittest.TestCase):
    """Проводка ловушки, а не помощник отдельно: замок обязан прочитать ЖИВОЙ реестр, хотя у копии
    демона `TASK_START_FILE` смотрит в пустой временный."""

    def test_lock_reads_the_live_registry_not_the_frozen_one(self):
        self.assertEqual(borrowed_copy_scene(self, o, run.Queue), (False, []))
        # Контроль на пустоту: по замороженному пути печати нет — чтение оттуда дало бы замок.
        self.assertEqual(o._seal_row_state(TID, None)[0], "none")


# ═══════════════════════════════ 3. МУТАНТЫ ═══════════════════════════════════════════════════

def _source(mod):
    """Исходник модуля: прогон «до правки» подкладывает свой текст полем `__sealcatch_src__`."""
    s = getattr(mod, "__sealcatch_src__", None)
    if s is not None:
        return s
    with open(mod.__file__, encoding="utf-8") as f:
        return f.read()


def _mutant(mod, name, anchor, repl):
    """Настоящий исходник с ОДНОЙ правкой → новый модуль. Якорь обязан найтись ровно один раз:
    иначе мутант устарел и молча проверял бы не то."""
    src = _source(mod)
    n = src.count(anchor)
    if n != 1:
        raise AssertionError("якорь мутанта %s найден %d раз — мутант устарел" % (name, n))
    m = types.ModuleType(name)
    m.__file__ = mod.__file__
    sys.modules[name] = m
    exec(compile(src.replace(anchor, repl), mod.__file__, "exec"), m.__dict__)
    return m


MUTANTS = {
    # «без проверки расписки»
    "receipt": ('    if seal.get("ok"):\n        return "расписка принята — догонять нечего"\n', ""),
    # «без проверки связи»
    "link": ('    if name != LINK_OK:\n        return "связь: %s" % name\n', ""),
    # окно меряется только от прошлой попытки (как в первой редакции)
    "window": ('    ref = seal.get("catch_at") or seal.get("at")\n',
               '    ref = seal.get("catch_at")\n    if not ref:\n        ref = "2000-01-01T00:00:00+00:00"\n'),
    # нет чтения статуса до мутации
    "preread": ("    pre = _read()\n", "    pre = False\n"),
    # учёт попытки не проверяется
    "note": ("    if not _seal_note(tid, path=path, catch_at=stamp, catch_n=n):\n",
             "    if not (_seal_note(tid, path=path, catch_at=stamp, catch_n=n) or True):\n"),
    # печать забывает текст при повторе
    "keep": ('_SEAL_KEEP = ("head", "first_at", "catch_at", "catch_n")',
             '_SEAL_KEEP = ("first_at", "catch_at", "catch_n")'),
    # «не знаю» = «запечатан»
    "unknown": ('        return "unknown", seal\n', '        return "sealed", seal\n'),
    # замок забывает решать путь реестра в момент зова
    "regpath": ("path=path or _seal_registry_path()", "path=path"),
    # замок снимается и на approved
    "status": ('    if not isinstance(it, dict) or str(it.get("status") or "") != "in_progress":\n'
               '        return False\n', ""),
}
MUT_OWNER = "\n                and not self._sealed(it)]"


class TestMutants(unittest.TestCase):
    """Каждая проверка обязана быть НЕОБХОДИМОЙ: без неё набор краснеет. Сцена одна и та же для
    настоящего модуля и мутанта."""

    _cache = {}

    @classmethod
    def _orch(cls, key):
        name = "pc_orchestrator_mut_" + key
        if name not in cls._cache:
            anchor, repl = MUTANTS[key]
            cls._cache[name] = _mutant(o, name, anchor, repl)
        return cls._cache[name]

    def _calls(self, m, how, **kw):
        prep(self, m)
        fb, now = scene(m, **kw)
        if how == "unsure":
            link_unsure(m)
        if how == "lost":
            link_lost(m)
            m._seal_catch_up(fb.rows[TID], now=now)
        elif how == "nowrite":
            with mock.patch.object(m, "_task_started_write", lambda st, tid, path=None: False):
                m.process_stuck_singles(now=now)
                m.process_stuck_singles(now=now + datetime.timedelta(seconds=60))
        elif how == "twice":
            m.process_stuck_singles(now=now)
            m.process_stuck_singles(now=now + datetime.timedelta(seconds=getattr(m, "SEAL_CATCH_SEC", 900) + 5))
            return fb.completed
        else:
            m.process_stuck_singles(now=now)
        return len(fb.completed)

    def _caught(self, key, how, **kw):
        mut = self._orch(key)
        self.assertEqual(self._calls(o, how, **kw), 0, "настоящий модуль: %s" % key)
        self.assertGreater(self._calls(mut, how, **kw), 0, "мутант «%s» НЕ пойман" % key)

    def test_mutant_without_receipt_check_is_caught(self):
        self._caught("receipt", "plain", seal_ok=True)

    def test_mutant_without_link_check_is_caught(self):
        self._caught("link", "unsure")
        self._caught("link", "lost")

    def test_mutant_window_only_from_last_attempt_is_caught(self):
        self._caught("window", "plain", seal_age=300, row_age=600)

    def test_mutant_without_read_before_complete_is_caught(self):
        self._caught("preread", "plain", status="failed", lag=True)

    def test_mutant_ignoring_unrecorded_attempt_is_caught(self):
        self._caught("note", "nowrite")

    def test_mutant_unknown_as_sealed_is_caught(self):
        self._caught("unknown", "plain", updated=None)

    def test_mutant_forgetting_text_on_retry_is_caught(self):
        mut = self._orch("keep")
        self.assertEqual([c[2] for c in self._calls(o, "twice", lands=False)], [LIVE_RESULT, LIVE_RESULT])
        sent = [c[2] for c in self._calls(mut, "twice", lands=False)]
        self.assertEqual(len(sent), 2)
        self.assertNotEqual(sent[1], LIVE_RESULT, "мутант «печать забывает текст» НЕ пойман")

    def test_mutant_owner_busy_without_seal_is_caught(self):
        mut = _mutant(run, "recon_auto_run_mut_owner", MUT_OWNER, "]")
        prep(self, o)
        now = datetime.datetime.now(UTC)
        o._task_closed_mark(TID, "failed", ok=False, now=now - datetime.timedelta(seconds=300))
        self.assertEqual(run.Queue(daemon=o).owner_busy([owner_row(now)]), (False, []))
        self.assertEqual(mut.Queue(daemon=o).owner_busy([owner_row(now)]), (True, [TID]),
                         "мутант «owner_busy без печати» НЕ пойман")

    def test_mutant_lock_without_call_time_path_is_caught(self):
        mut = self._orch("regpath")
        self.assertEqual(borrowed_copy_scene(self, o, run.Queue), (False, []))
        self.assertEqual(borrowed_copy_scene(self, mut, run.Queue), (True, [TID]),
                         "мутант «замок читает замороженный путь» НЕ пойман")

    def test_mutant_lock_on_approved_is_caught(self):
        mut = self._orch("status")
        for m, want in ((o, (True, [TID])), (mut, (False, []))):
            prep(self, m)
            now = datetime.datetime.now(UTC)
            m._task_closed_mark(TID, "failed", ok=False, now=now - datetime.timedelta(seconds=300))
            self.assertEqual(run.Queue(daemon=m).owner_busy([owner_row(now, status="approved")]), want,
                             "мутант «замок снят на approved» НЕ пойман" if m is mut else "настоящий")


if __name__ == "__main__":
    unittest.main(verbosity=2)
