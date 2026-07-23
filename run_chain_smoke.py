# -*- coding: utf-8 -*-
"""run_chain_smoke.py — прогнать обязательный смоук-шаг цепи РОВНО как демон.

Вызывает pc_orchestrator._exec_smoke_step() — ту же функцию, которой демон
исполняет финальный «🔬 обязательный смоук-шаг» локальной цепи (suggest.runLiveSmoke
под SUGGEST_TEST_MODE; без TEST_MODE смоук сам вернёт skipped, живого клиента не
касаясь). Печатает вердикт (status, result) и выходит кодом 0 при done, 1 при failed.

Запуск (venv): python run_chain_smoke.py
"""
import sys

import pc_orchestrator as p


def main():
    status, result = p._exec_smoke_step()
    print(f"STATUS: {status}")
    print(result)
    return 0 if status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
