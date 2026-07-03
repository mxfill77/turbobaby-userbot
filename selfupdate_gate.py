# -*- coding: utf-8 -*-
"""
selfupdate_gate.py — гейт самообновления pc_agent.

КОРЕНЬ КЛАССА БАГА: команда «обновись» перезапускала агента, НЕ проверив новый код.
Битый/непроверенный pc_agent.py убивал канал управления (тема 205) без возможности
откатиться командой (её некому принять). Гейт запускает py_compile + import-smoke на
НОВОМ коде В ОТДЕЛЬНОМ интерпретаторе. Провал → НЕ перезапускаемся, остаёмся на старом
(рабочем) коде и отвечаем «⛔ обновление отклонено».

Функция чистая (subprocess), без Telegram — легко тестируется.
"""

import subprocess


def code_gate(py_exe, cwd, compile_files, import_smoke, timeout=60):
    """Проверить здоровье кода перед самоперезапуском. Возвращает (ok: bool, msg: str).
      1) py_compile перечисленных файлов (синтаксис);
      2) import-smoke модуля import_smoke в СВЕЖЕМ интерпретаторе (ловит ошибки времени
         импорта, которых py_compile не видит).
    Любой сбой → (False, краткая причина). Всё ок → (True, 'ok')."""
    try:
        r = subprocess.run(
            [str(py_exe), "-m", "py_compile", *[str(f) for f in compile_files]],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return False, "py_compile: " + ((r.stderr or r.stdout).strip()[:500] or "ошибка компиляции")
    except Exception as e:
        return False, f"py_compile не запустился: {e}"

    try:
        r = subprocess.run(
            [str(py_exe), "-c", f"import {import_smoke}"],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return False, "import-smoke: " + ((r.stderr or r.stdout).strip()[:500] or "ошибка импорта")
    except Exception as e:
        return False, f"import-smoke не запустился: {e}"

    return True, "ok"
