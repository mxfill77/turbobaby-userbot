# Класс кодировки ВЫВОДА на ПК-полосе — закрытие (2026-07-30)

## Симптом (третий укус за сутки)
Кнопка **«Статус цепи»** отвечала не статусом, а трейсбеком:
`'charmap' codec can't encode character '\U0001f4ca'` (📊). Тот же класс за 29–30.07 кусал
дважды до этого: писатель журнала (093119e — stdin), `dispatch_notify` (stdin хука). Каждый
раз чинили ОДНО место из трёх — класс возвращался.

## Корень (одним предложением)
Текст с не-ASCII (эмодзи 📊 ⏹ 🔔, кириллица) уходит наружу без явного UTF-8, и Python на
Windows берёт кодировку КОНСОЛИ (cp1251): эмодзи вне cp1251 роняют процесс на `print`, а
кириллица в UTF-8-приёмник ложится мохибейком. Две полосы одного класса:
- **ЗАПИСЬ (encode):** дочерний `pc_orchestrator.py --chain-status` печатал 📊 в ПАЙП → cp1251
  → падение; в кнопку уезжал traceback. Воспроизведено «до фикса»: `print('\U0001F4CA')` в
  пайп без явного UTF-8 → `UnicodeEncodeError: 'charmap' codec can't encode '\U0001f4ca'`.
- **ЧТЕНИЕ (decode):** родитель `pc_agent._chain_cli` читал вывод ребёнка `text=True` БЕЗ
  `encoding=` → декод локалью Windows (мохибейк даже если ребёнок уже пишет UTF-8).

## Фикс — общий шов, а не третья заплата
Новый модуль **`io_utf8.py`** → `force_utf8()` переключает stdout/stderr процесса в UTF-8
(reconfigure, идемпотентно, безопасно под тестом). Зовётся ПЕРВОЙ командой в каждом
вход-процессе. Почему reconfigure, а не PYTHONIOENCODING в env: env помогает только детям, а
процесс из Планировщика/терминала про наш env не знает — reconfigure чинит сам процесс.

### Полоса ЗАПИСИ — `force_utf8()` в вход-процессах
| Файл | Точка | Что печатало не-ASCII |
|---|---|---|
| `pc_orchestrator.py` | `__main__` (первая строка) | `--chain-status/-stop` печатают 📊/⏹ |
| `pc_agent.py` | `main()` (первая строка) | logging→stderr, traceback (кириллица) |
| `dispatch_notify.py` | `main()` (первая строка) | `--topic` печатает итог; тексты 🔔/📊/⏹ |
| `cowork_log_append.py` | `main()` (первая строка) | «OK: записано…» в utf-8-приёмник detached-ребёнком |

### Полоса ЧТЕНИЯ — `encoding="utf-8", errors="replace"` в каждом `subprocess.run(text=True)`
| Файл | Строки (функции) |
|---|---|
| `pc_agent.py` | `_find_userbot_pids`, `_taskkill`, `UB.update`(git pull), `_find_moderbot_pids`, `_git_pull`(--ff-only), **`_chain_cli`** (кнопка), `_agent_pid_alive` — 7 вызовов |
| `pc_orchestrator.py` | `_count_claude_procs`, `_find_pids_by_script`, `_lock_pid_alive`, `_find_daemon_pids` — 4 вызова (ещё 6 уже имели encoding) |
| `selfupdate_gate.py` | py_compile + import-smoke — 2 вызова (вывод едет в Telegram «гейт кода») |
| `lesson_router.py` | `git rev-parse` (вывод отбрасывался, но класс тот же) — 1 вызов |

### Уже были чисты (проверено, держим под стражем)
- `rc_supervisor.py:260/315` (`claude doctor`, `netstat`) — `encoding=` на месте.
- `pc_orchestrator.py` run_claude/думатель/git/schtasks — `encoding=` на месте.

## Страж — `test_utf8_output_guard.py`
AST-скан по контуру (7 файлов): (A) каждый вход-процесс зовёт `force_utf8()`; (B) каждый
`subprocess.run/Popen/check_output` в text-режиме задаёт `encoding=`. Плюс ЖИВОЙ round-trip:
ребёнок печатает 📊/⏹ в пайп (реальный `io_utf8`), родитель читает kwargs'ами `_chain_cli` —
эмодзи обязан дойти целым; `PYTHONUTF8/PYTHONIOENCODING` из env ребёнка убраны, значит работает
именно `force_utf8()`. Новое место (print-скрипт без force_utf8 / text=True без encoding) валит
гейт — четвёртого укуса молча не будет. Страж СРАЗУ поймал пропущенный `pc_agent._git_pull:490`.

## Границы (НЕ закрыто этой задачей — правка запрещена контуром)
`userbot_listen.py`, `moderation_bot.py`, `suggest.py` — тот же класс на них остаётся отдельным
остатком (перечислены в страже списком `OUT_OF_SCOPE`, тест проверяет, что они реально есть).
Точки касания: `userbot_listen.py:93` (tasklist, text=True), `moderation_bot.py:585` (tasklist,
text=True), `suggest.py:4917` (уже с encoding). Развилка владельцу: расширять ли страж на
боты/клиент отдельной задачей (их правка требует явного разрешения).

## Проверка живьём (verbatim — см. FACT-отчёт сессии)
- БЕЗ фикса: `rc=1`, `'charmap' codec can't encode character '\U0001f4ca'`.
- Реальный путь: `_chain_cli('status'|'stop', 424242)` → чистая кириллица, без падения (read-only).
- Тосты кнопок: `📊 читаю статус…` / `⏹ останавливаю цепь…` — целы.
- Round-trip: `📊 цепь #7: в работе, done 3`, rc=0.
- Гейт полный: **2191 тестов, OK (skipped=10)**.
