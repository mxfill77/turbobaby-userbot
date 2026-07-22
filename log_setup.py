# -*- coding: utf-8 -*-
"""
log_setup.py — единая ротация логов ПК-контура + РАЗВЕДЕНИЕ тестовых и боевых логов.

ДВЕ БОЛИ, которые лечит модуль (обе — живой факт аудита 22:27):

1) РОТАЦИИ НЕ БЫЛО НИГДЕ. Все модули вешали голый `logging.FileHandler` — файл рос вечно:
   pc_agent.log 28.7 МБ, pricing.log 22.2 МБ, pc_orchestrator.log 11.2 МБ. На диске C: при этом
   оставалось 0.4 ГБ. Здесь — `RotatingFileHandler` с общим порогом и числом бэкапов.

2) ТЕСТЫ ПИСАЛИ В БОЕВОЙ ЛОГ. В `pretool_guard.log` 21:39 лежат фикстуры прогона
   (`clasp push`, `sqlite3 bookings.db "select 1"`, пустая команда) — их невозможно отличить от
   настоящих действий владельца, и они попали в разбор «последних 20 Allow» как реальные
   события. Разведение здесь: под тестом ЛЮБОЙ лог уезжает в temp, боевой файл не трогается.

Признак теста берём ПО ФАКТУ окружения, а не по вежливой договорённости «не забудь выставить»:
TESTING=1 (общий рубильник репо, ставится `test_isolation`), TURBOBABY_TEST_LOGS=1 (явный
переключатель только логов) или PYTEST_CURRENT_TEST (взводит сам pytest). Порядок наследования
env вниз по дереву процессов делает признак верным и для subprocess-тестов (гард запускается
именно так).

Пороги настраиваются env: LOG_MAX_BYTES (по умолчанию 5 МБ), LOG_BACKUPS (по умолчанию 3).
Итого потолок на один лог = MAX × (BACKUPS + 1) ≈ 20 МБ.
"""

import os
import sys
import logging
import tempfile
from logging.handlers import RotatingFileHandler

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 3

TEST_PREFIX = "turbobaby_TESTING_"


def _int_env(name, default):
    try:
        return int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


def max_bytes():
    return _int_env("LOG_MAX_BYTES", DEFAULT_MAX_BYTES)


def backups():
    return _int_env("LOG_BACKUPS", DEFAULT_BACKUPS)


def _started_as_test_runner():
    """True ⇔ САМ ПРОЦЕСС запущен как тест-раннер (`python -m unittest …`, `pytest …`).

    Намеренно НЕ проверяем «есть ли unittest в sys.modules»: демон импортирует
    `gate_selective`, который гоняет тесты, — по такому признаку БОЕВОЙ pc_orchestrator.log
    молча уехал бы в temp, и живая диагностика исчезла бы ровно тогда, когда она нужнее всего.
    Спрашиваем именно способ запуска процесса."""
    try:
        spec = getattr(sys.modules.get("__main__"), "__spec__", None)
        if spec is not None and (spec.name or "").split(".")[0] in ("unittest", "pytest"):
            return True
        argv0 = os.path.basename(sys.argv[0] or "").lower()
        if argv0.startswith("pytest") or argv0.startswith("py.test"):
            return True
        return os.path.normpath(sys.argv[0] or "").lower().endswith(
            os.path.join("unittest", "__main__.py"))
    except Exception:
        return False


def is_test_context(env=None):
    """True ⇔ идёт тестовый прогон. env — инъекция для тестов."""
    e = os.environ if env is None else env
    if (e.get("TURBOBABY_TEST_LOGS") or "").strip() not in ("", "0"):
        return True
    if (e.get("TESTING") or "").strip() not in ("", "0"):
        return True
    if (e.get("PYTEST_CURRENT_TEST") or "").strip():
        return True
    return env is None and _started_as_test_runner()


def log_path(filename, env=None):
    """Куда РЕАЛЬНО писать лог `filename`. Боевой прогон → файл в репо; тест → одноимённый файл
    в temp с префиксом. Абсолютный путь на входе уважаем (берём только имя для тест-ветки)."""
    name = os.path.basename(filename)
    if is_test_context(env):
        return os.path.join(tempfile.gettempdir(), TEST_PREFIX + name)
    return filename if os.path.isabs(filename) else os.path.join(HERE, name)


def rotating_handler(filename, fmt="%(asctime)s | %(message)s", level=logging.INFO, env=None):
    """RotatingFileHandler по разрешённому пути. Падение на открытии файла НЕ роняет вызывающего
    (лог вторичен): вернём None, и модуль просто останется без файлового хендлера."""
    path = log_path(filename, env)
    try:
        h = RotatingFileHandler(path, maxBytes=max_bytes(), backupCount=backups(),
                                encoding="utf-8")
    except Exception:
        return None
    h.setFormatter(logging.Formatter(fmt))
    h.setLevel(level)
    return h


def rotate_if_needed(path, limit=None, keep=None):
    """Ротация для писателей БЕЗ модуля logging (гард пишет строку через open(..., 'a')).
    Сдвигает path.N → path.N+1 и path → path.1, оставляя `keep` бэкапов. Никогда не бросает."""
    lim = max_bytes() if limit is None else limit
    n = backups() if keep is None else keep
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= lim:
            return False
        oldest = "%s.%d" % (path, n)
        if os.path.isfile(oldest):
            os.remove(oldest)
        for i in range(n - 1, 0, -1):
            src, dst = "%s.%d" % (path, i), "%s.%d" % (path, i + 1)
            if os.path.isfile(src):
                os.replace(src, dst)
        os.replace(path, path + ".1")
        return True
    except Exception:
        return False
