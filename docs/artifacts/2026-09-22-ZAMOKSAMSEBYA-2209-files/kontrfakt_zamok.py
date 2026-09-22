# -*- coding: utf-8 -*-
"""kontrfakt_zamok.py — КОНТРФАКТ «самозапирания» сигнальной остановки ящика, на КОПИИ дерева.

Вопрос задания (22.09.2026, п.2): при поднятой остановке ветка чтения закрытых рядов
очереди ВЫЗЫВАЕТСЯ или ПРОПУСКАЕТСЯ? Ответ даётся строкой исполнения, а не рассуждением.

КАК УСТРОЕНО. Копия модулей ПК-полосы на коммите живого демона (e29531d7) лежит в
`tmp/stop_razbor/tree` (кладётся `git archive`, путь — первым аргументом). Отсюда
импортируются НАСТОЯЩИЕ `pc_orchestrator.maybe_shtab_box` (дверь демона: решает, будет ли
оборот) и `shtab_box_run.tick` (оборот: решает, спрашивать ли закрытые ряды). Внешнее
подменено заглушками набора ящика (`test_shtab_box`: FakeQueue, папка, шапка), а журнал и
извещения — записывающими функциями: наружу не уходит НИЧЕГО, мост не зовётся ни разу.
Состояние (метка оборота, замок, долгая память) живёт в `tmp/stop_razbor/runs/<прогон>/`.

СЦЕНАРИЙ — ЖИВОЙ ЭПИЗОД 22.09 (UTC), оборот за оборотом, с тем же видом отказа прибора:
  T0 11:04:16  всё читается                  → взят документ 70z
  T1 11:39:30  папка не перечислена          (живой лог 18:41:48 мест.)
  T2 11:54:26  закрытые ряды не прочитаны    (живой лог 18:58:43 мест.) → ОСТАНОВКА
  T3 12:05:00  очередь недоступна            (живой лог 19:08:42 мест.)
  T4 12:20:00  закрытые ряды не прочитаны    (живой лог 19:26:45 мест.) → ОСТАНОВКА
  T5 12:37:09  прибор ответил                (живой лог 19:38:11 мест.) → взят 71a

ТРИ ПРОГОНА, и два последних — не украшение, а доказательство, что проверка КУСАЕТСЯ:
  HEAD  — код как есть;
  M1    — мутант САМОЗАПИРАНИЯ: дверь демона не пускает оборот, пока в метке оборота стои́т
          фраза остановки (одна вставленная строка в исходник `maybe_shtab_box`). Проверка
          обязана увидеть разницу: закрытые ряды больше не спрашиваются, 71a не берётся никогда;
  M2    — мутант «остановку сняли»: неопределённые сигналы вычеркнуты из держащих. Мерит
          ВКЛАД самой остановки в простой: берёт ли ящик хоть что-нибудь на тех же оборотах.

Код возврата 0 — все три прогона дали ожидаемое; 1 — нет (тогда вывод выше говорит, где).
"""
from __future__ import annotations

import datetime
import inspect
import json
import os
import sys
import textwrap

UTC = datetime.timezone.utc
TREE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join("tmp", "stop_razbor", "tree"))
RUNS = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else os.path.join("tmp", "stop_razbor", "runs"))

# Тест-режим ДО импорта демона — тем же способом, что набор демона (`test_pc_orchestrator`):
# логи и файлы состояния уезжают во временные пути, писатель мозга отказывает в живой записи.
os.environ["TESTING"] = "1"
os.environ["TURBOBABY_TEST_LOGS"] = "1"
os.environ["LESSON_LLM_ROUTE"] = "0"
os.environ.pop("SHTAB_BOX", None)
sys.path.insert(0, TREE)

import pc_orchestrator as o          # noqa: E402  — КОПИЯ, не боевой модуль
import shtab_box_run as run          # noqa: E402
import shtab_box_signals as sig      # noqa: E402
import test_shtab_box as tsb         # noqa: E402  — фикстуры набора ящика, вторых не заводим

for mod in (o, run, sig):
    assert os.path.abspath(mod.__file__).startswith(TREE), "импорт не из копии: %s" % mod.__file__

KEY_A, KEY_B = "0015f-70z.2209", "0015g-71a.2209"
BODY = tsb.GOOD_BODY.replace("за 02.09", "за 22.09")
FILES, BODIES = tsb._files([(KEY_A, BODY), (KEY_B, BODY)])

TICKS = [
    ("T0", datetime.datetime(2026, 9, 22, 11, 4, 16, tzinfo=UTC), "ok"),
    ("T1", datetime.datetime(2026, 9, 22, 11, 39, 30, tzinfo=UTC), "folder_down"),
    ("T2", datetime.datetime(2026, 9, 22, 11, 54, 26, tzinfo=UTC), "closed_down"),
    ("T3", datetime.datetime(2026, 9, 22, 12, 5, 0, tzinfo=UTC), "queue_down"),
    ("T4", datetime.datetime(2026, 9, 22, 12, 20, 0, tzinfo=UTC), "closed_down"),
    ("T5", datetime.datetime(2026, 9, 22, 12, 37, 9, tzinfo=UTC), "ok"),
]
WHAT = {"ok": "прибор отвечает", "folder_down": "папка НЕ перечислена",
        "closed_down": "закрытые ряды НЕ прочитаны", "queue_down": "очередь НЕ прочитана"}


class LiveLikeQueue(tsb.FakeQueue):
    """FakeQueue + одна правда живой полосы: взятое задание к следующему обороту уже ЗАКРЫТО
    (22.09: #45 взят 11:04, сдан 11:31) и лежит в `done` со своим маркером ящика."""

    def place_task(self, text, lane=None):
        ok, tid, err = super().place_task(text, lane=lane)
        self._closed.append({"id": tid, "status": "done", "result": "сдано", "task_text": text})
        return ok, tid, err


class Recorder(object):
    def __init__(self):
        self.lines = []

    def __call__(self, *a, **kw):
        self.lines.append(" ".join(str(x) for x in a)[:160])
        return True, "записано в заглушку", ""


def scenario(label, daemon_gate, drop_undeterminate=False):
    root = os.path.join(RUNS, "%s-%s" % (label, datetime.datetime.now().strftime("%H%M%S%f")))
    os.makedirs(root, exist_ok=True)
    tick_path = os.path.join(root, "pc_orchestrator.shtab_box_tick.json")
    q = LiveLikeQueue()
    journal, notes = Recorder(), Recorder()
    o._cowork = journal                      # строка итога демона → заглушка, а не cowork_log
    run._journal = journal                   # строка остановки/взятия → заглушка
    orig_active = sig.active
    if drop_undeterminate:
        sig.active = lambda signals: [s for s in orig_active(signals) if s.get("determinate")]
    cur = {}
    calls = []

    def runner(root=None, place=True, limit=1, budget=40, write_journal=True):
        calls.append(cur["name"])
        return run.tick(root=cur["root"], place=place, limit=limit, budget=budget,
                        write_journal=write_journal, clock=lambda tz: cur["at"], queue=q,
                        journal_fn=journal, reader=tsb._reader(tsb.HEAD_TEXT),
                        ledger=lambda r: ({}, True, ""),
                        lister=tsb._lister(FILES, ok=cur["mode"] != "folder_down",
                                           why="TimeoutError: The read operation timed out"),
                        doc_reader=tsb._doc_reader(BODIES), notify_fn=notes,
                        hold_notify_fn=tsb._HoldDoor())

    rows, placed_all = [], []
    try:
        for name, at, mode in TICKS:
            cur.update(name=name, at=at, mode=mode, root=root)
            q._ok = mode != "queue_down"
            q._closed_ok = mode != "closed_down"
            before_asked, before_placed, before_calls = len(q.asked), len(q.tasks), len(calls)
            rep = daemon_gate(now=at.timestamp(), tick_path=tick_path, runner=runner)
            asked = q.asked[before_asked:]
            st = o._shtab_box_read_tick(tick_path)
            placed = [t for t in q.tasks[before_placed:]]
            keys = [k for k in (KEY_A, KEY_B) for _tid, text in placed if ("ключ=%s" % k) in text]
            placed_all += keys
            census = st.get("census") or {}
            rows.append({
                "tick": name, "at": at.strftime("%H:%M:%S"), "instrument": WHAT[mode],
                "tick_ran": len(calls) > before_calls,
                "closed_asked": tuple(run.CLOSED_STATUSES) in [tuple(a) for a in asked],
                "stop": bool(str(st.get("stop") or "")),
                "taken": keys,
                "census_next": census.get("next", ""), "census_waiting": census.get("waiting"),
                "why": str((rep or {}).get("why") or "")[:150],
            })
    finally:
        sig.active = orig_active
    return {"label": label, "rows": rows, "taken": placed_all, "journal": journal.lines,
            "notices": notes.lines, "root": root}


def mutant_gate():
    """Мутант М1: ОДНА строка в исходник двери демона — «стои́т остановка → оборота нет».
    Именно так выглядело бы самозапирание: снимается результатом оборота, оборот не даёт."""
    src = textwrap.dedent(inspect.getsource(o.maybe_shtab_box))
    anchor = "    said_before = _shtab_box_said(st)\n"
    assert src.count(anchor) == 1, "якорь мутанта не найден ровно один раз"
    src = src.replace(anchor, anchor + "    if str(st.get(\"stop\") or \"\"):\n        return None\n")
    ns = dict(o.__dict__)
    exec(compile(src, "<мутант М1: maybe_shtab_box>", "exec"), ns)
    return ns["maybe_shtab_box"]


def show(res):
    print("── прогон %s (состояние: %s)" % (res["label"], os.path.relpath(res["root"])))
    for r in res["rows"]:
        print("  %s %s UTC | %-28s | оборот вызван=%-3s | закрытые спрошены=%-3s | остановка=%-3s "
              "| взято=%-16s | слепок: следующий=%s ждёт=%s"
              % (r["tick"], r["at"], r["instrument"], "да" if r["tick_ran"] else "НЕТ",
                 "да" if r["closed_asked"] else "нет", "ДА" if r["stop"] else "нет",
                 ",".join(r["taken"]) or "—", r["census_next"] or "—", r["census_waiting"]))
    for r in res["rows"]:
        if r["why"]:
            print("    %s why: %s" % (r["tick"], r["why"]))


def main():
    head = scenario("HEAD", o.maybe_shtab_box)
    m1 = scenario("M1-samozapiranie", mutant_gate())
    m2 = scenario("M2-bez-ostanovki", o.maybe_shtab_box, drop_undeterminate=True)
    for res in (head, m1, m2):
        show(res)

    def row(res, name):
        return next(r for r in res["rows"] if r["tick"] == name)

    checks = []
    # HEAD: после каждой остановки СЛЕДУЮЩИЙ оборот вызван и закрытые ряды спрошены.
    after_stop = [n for n in ("T3", "T4", "T5")]
    checks.append(("HEAD: оборот вызывается на КАЖДОМ шаге после остановки T2",
                   all(row(head, n)["tick_ran"] for n in after_stop)))
    checks.append(("HEAD: закрытые ряды спрошены на T4 и T5 (остановка T2/T4 чтение не глушит)",
                   row(head, "T4")["closed_asked"] and row(head, "T5")["closed_asked"]))
    checks.append(("HEAD: остановка стоит ровно на T2 и T4 — там, где прибор отказал",
                   [r["tick"] for r in head["rows"] if r["stop"]] == ["T2", "T4"]))
    checks.append(("HEAD: T5 взял 71a, а 70z не взят второй раз",
                   head["taken"] == [KEY_A, KEY_B]))
    checks.append(("HEAD: слепок на T5 зовёт «следующим» 71a — документ, взятый ЭТИМ ЖЕ оборотом",
                   row(head, "T5")["census_next"] == KEY_B))
    checks.append(("HEAD: слепок T1–T4 держит «следующим» 70z — взятый на T0",
                   all(row(head, n)["census_next"] == KEY_A for n in ("T1", "T2", "T3", "T4"))))
    checks.append(("M1: мутант самозапирания — после T2 оборот НЕ вызывается ни разу",
                   not any(row(m1, n)["tick_ran"] for n in after_stop)))
    checks.append(("M1: остановка висит до конца, 71a не взят",
                   row(m1, "T5")["stop"] and KEY_B not in m1["taken"]))
    checks.append(("M2: без остановки на T2/T4 не взято НИЧЕГО (держит старый замок marks_ok)",
                   not row(m2, "T2")["taken"] and not row(m2, "T4")["taken"]
                   and m2["taken"] == [KEY_A, KEY_B]))
    print("── вердикты")
    bad = 0
    for words, ok in checks:
        print("  [%s] %s" % ("OK" if ok else "FAIL", words))
        bad += 0 if ok else 1
    summary = {"head": head, "m1": m1, "m2": m2, "checks": [[w, bool(k)] for w, k in checks]}
    with open(os.path.join(RUNS, "summary-%s.json" % datetime.datetime.now().strftime("%H%M%S")),
              "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=str)
    print("ИТОГ: %d из %d проверок OK" % (len(checks) - bad, len(checks)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
