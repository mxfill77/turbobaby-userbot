# -*- coding: utf-8 -*-
"""chain_plan_read.py — read-only читалка плана локальной цепи из очереди Bridge.

Диагностический инструмент Dispatch-сессий: показывает task_text/result родителя
цепи pcloc-dec и её шагов (get_pending по всем статусам, лента lane=pc). НИЧЕГО
не мутирует: только GET к Bridge через штатный клиент pc_orchestrator.Bridge.
Секреты не печатает.

Запуск: python chain_plan_read.py <id родителя> [<id шага> ...]
"""
import sys

import pc_orchestrator as p

STATUSES = ("done", "failed", "new", "in_progress", "needs_approval", "approved")


def main():
    ids = {int(a) for a in sys.argv[1:]} or {274}
    # родитель + соседние id шагов цепи, если явно не заданы
    if len(ids) == 1:
        base = next(iter(ids))
        ids |= set(range(base + 1, base + 8))
    for st in STATUSES:
        r = p.bc.get_pending(st)
        if not r.get("ok"):
            print(f"{st}: ERR {r.get('error')}")
            continue
        for it in r.get("items", []):
            tid = int(it.get("id") or 0)
            if tid in ids:
                print("=" * 20, f"id={tid} status={st}")
                print("TASK:", str(it.get("task_text") or "")[:1200])
                print("RESULT:", str(it.get("result") or "")[:2600])
                print()


if __name__ == "__main__":
    main()
