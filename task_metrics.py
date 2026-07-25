# -*- coding: utf-8 -*-
"""task_metrics — ЕДИНАЯ структурная строка метрик на одну задачу (наблюдаемость обеих полос: pc/vps).

Одна задача = одна строка «METRICS key=value …» в лог демона (orchestrator_daemon.log / лог
pc_orchestrator). Формат ОДИН для ПК и VPS (файл байт-в-байт копируется в оба репо), поля —
только `key=value` без пробелов внутри значений → строка тривиально грепается и парсится:

    METRICS task=<id> lane=<pc|vps> type=<read|code|build|other> mode=<prod|test> src=<входной файл>
            model=<модель> effort=<уровень> start=<iso> end=<iso> dur_s=<секунды, 2 знака>
            outcome=<исход> attempts=<n> selfheals=<n> tokens_in=<n|na> tokens_out=<n|na>

Три поля добавлены 25.07.2026:
  type — что просят СДЕЛАТЬ (признаки текста задания, см. task_type / CLI --type), а не тема.
  mode/src — ЧЕЙ это прогон. Боевой демон даёт «mode=prod src=orchestrator_daemon.py», любой
    тест-прогон — «mode=test» и своё имя входного файла. Раньше строка теста была неотличима от
    живой задачи и врала владельцу прямо в журнале.
  dur_s — секунды с сотыми: раньше формат резал до int, и всё короче полутора секунд
    схлопывалось в «dur_s=0».

Все функции ЧИСТЫЕ (без времени/сети/claude) → детерминированный юнит (test_metrics_line /
TestMetricsLine). Тайминги/исход подставляет вызывающая обёртка run_task в каждом демоне.
"""
import re

# claude -p принимает --effort ровно из этого набора (claude --help: low|medium|high|xhigh|max).
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Доктрина репо «каждая задача ultrathink» → дефолт и фолбэк на невалидное значение = xhigh.
DEFAULT_EFFORT = "xhigh"

# Маркер самопочинки (порт конвертов оркестратора): «[самопочинка задачи N, попытка K]» /
# «[самопочинка шага N, попытка K]». Берём номер ПОПЫТКИ (K) как число самопочинок этой задачи.
_SELFHEAL_RE = re.compile(r"\[самопочинк[аи][^\]]*попытк[аи]\s*(\d+)\]")

# ------------------------------------------------------------------------------------------------
# ТЕСТ-ПРОГОН ПРОТИВ БОЕВОГО (25.07.2026). Демон гасит файловый лог под тестом, но признак «имя
# входного файла начинается с test_» дырявый: КОПИЯ теста под другим именем (реальный случай —
# `git show <хеш>:tests/test_x.py > /tmp/old.py`) снова лила строки в боевой журнал. Добавлен
# ORCH_TEST_MODE=1 — его ставит gate.py ВСЕМ тест-процессам, а в бою его нет ни в .env, ни в юните
# (и демон явно вычищает его из дочерних окружений). Функция ЧИСТАЯ: окружение и модули приходят
# аргументами, поэтому дыру можно проверить юнитом, а не «на живом журнале».
_UNDER_TEST_ENV = ("PYTEST_CURRENT_TEST", "ORCH_DAEMON_TEST", "ORCH_TEST_MODE")


def under_test(argv0="", env=None, modules=()):
    """True → это ТЕСТ-ПРОГОН, а не боевой демон. Признаки: pytest в модулях/окружении, имя входного
    файла test_*, ORCH_DAEMON_TEST=1, ORCH_TEST_MODE=1. Ошибаться безопаснее В СТОРОНУ «тест»:
    лишняя тишина в журнале дешевле, чем выдуманные строки в боевой статистике."""
    e = env if env is not None else {}
    if "pytest" in (modules or ()):
        return True
    if e.get("PYTEST_CURRENT_TEST"):
        return True
    if e.get("ORCH_DAEMON_TEST") == "1" or e.get("ORCH_TEST_MODE") == "1":
        return True
    base = str(argv0 or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base.startswith("test_")


# ------------------------------------------------------------------------------------------------
# ТИП ЗАДАЧИ ПО ПРИЗНАКАМ ТЕКСТА (25.07.2026). Классифицируем ДЕЙСТВИЕ, которое просят сделать, а
# не ТЕМУ задания — ровно тот урок, что и сужение слоя 2 в оркестраторе: тема ловит не то. Поэтому
# в списках стоят глаголы и явные запреты, а слов вроде «гейт», «тесты», «коммит» тут НЕТ: они
# встречаются в любом задании («вступили новые пороги гейтов», «коммит 0bbca3d») и метили бы всё
# подряд правкой кода.
# ПОРЯДОК РЕШАЕТ: явный запрет менять бьёт всё остальное → стройка → правка → слабое чтение.
_T_READ_HARD = (      # владелец ЯВНО запретил менять — это чтение, что бы ни стояло рядом
    "read-only", "readonly", "только чтение", "ничего не менять", "ничего не меняем",
    "не менять", "не трогать", "не коммитить", "не сливать", "не применять", "не выполнять",
    "без правок", "без коммита", "не пушить", "push не делать",
)
_T_BUILD = (          # стройка: новое, архитектура, выкатка в прод
    "внедрить", "спроектир", "архитектур", "класс-фикс", "выкатить", "в прод", "отгрузить",
    "достроить", "построить", "с нуля", "новый модуль", "рефактор", "переписать", "этап ",
    "рубеж", "лестниц",
)
_T_CODE = (           # правка кода по готовой спеке
    "починить", "почини", "фикс", "поправ", "правк", "перенести", "порт ", "приземлить",
    "влить", "merge", "слить", "закоммитить", "сузить", "убрать", "добавить", "заменить",
    "догнать", "[шаг ",
)
_T_READ_SOFT = (      # наблюдение без запрета — чтение, если стройки и правки не нашлось
    "замер", "диагностик", "разобрать", "разбор", "проверить", "проверка", "показать",
    "покажи", "сводк", "дословный вывод", "fact:", "сколько", "выяснить", "сверить",
)
_T_GROUPS = (
    (_T_READ_HARD, "read", "явный запрет менять"),
    (_T_BUILD, "build", "стройка"),
    (_T_CODE, "code", "правка кода"),
    (_T_READ_SOFT, "read", "наблюдение"),
)


def task_type_explain(text):
    """(тип, сработавший признак). Не опознали → ('other', '') — ЧЕСТНО, а не догадкой: метрика
    нужна владельцу как факт, натянутая метка испортит ровно ту статистику, ради которой считаем."""
    t = str(text or "").lower()
    if not t.strip():
        return "other", ""
    for group, label, _human in _T_GROUPS:
        for marker in group:
            if marker in t:
                return label, marker
    return "other", ""


def task_type(text):
    """Тип задачи: read (чтение и диагностика) / code (правка кода) / build (стройка) / other."""
    return task_type_explain(text)[0]


def norm_effort(value):
    """Нормализовать уровень усилий: регистр не важен, неизвестное/пустое → DEFAULT_EFFORT (xhigh)."""
    v = (str(value or "").strip() or DEFAULT_EFFORT).lower()
    return v if v in EFFORT_LEVELS else DEFAULT_EFFORT


def _cell(value):
    """Значение одной ячейки key=value: None → 'na'; переносы/пробелы схлопываем в '_' (нет пробелов
    внутри значения → строка остаётся однотокенной для grep/split)."""
    if value is None:
        return "na"
    s = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not s:
        return "na"
    return "_".join(s.split())


def metrics_line(task, lane, model, effort, start_iso, end_iso, dur_s, outcome,
                 attempts, selfheals, tokens_in=None, tokens_out=None,
                 task_text=None, mode=None, src=None):
    """Собрать каноническую однострочную запись METRICS. dur_s → секунды с СОТЫМИ (короткие задачи
    больше не схлопываются в 0); счётчики → int; None-токены → 'na'. type считается из task_text
    признаками (task_type), mode/src приходят от вызывающего демона. Формат ЗАМОРОЖЕН — правка
    ломает голдены (test_metrics_line/TestMetricsLine)."""
    return (
        "METRICS "
        f"task={_cell(task)} lane={_cell(lane)} type={task_type(task_text)} "
        f"mode={_cell(mode)} src={_cell(src)} "
        f"model={_cell(model)} effort={_cell(effort)} "
        f"start={_cell(start_iso)} end={_cell(end_iso)} dur_s={float(dur_s):.2f} "
        f"outcome={_cell(outcome)} attempts={int(attempts)} selfheals={int(selfheals)} "
        f"tokens_in={_cell(tokens_in)} tokens_out={_cell(tokens_out)}"
    )


# ПОЛНЫЙ вход = свежие + прочитанные из кэша + записанные в кэш. С включённым кэшем промпта «голое»
# input почти пусто, а весь system-промпт лежит в cacheRead/cacheCreation (живой замер 23.07.2026,
# claude 2.1.217: sonnet input=2, cacheRead=29339, cacheCreation=16329). Считать по ОДНОМУ input —
# соврать владельцу о расходе (класс «лог месяцами врал»; ср. suggest._mu_input_total). Поддержаны
# ОБА нейминга: usage.* (snake_case, агрегат ответа) и modelUsage[*].* (camelCase, по головам CLI).
_USAGE_IN_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_MU_IN_FIELDS = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")


def _sum_fields(d, fields):
    """(сумма присутствующих полей, был ли хоть один ключ) — отличаем «0 токенов» от «нет данных»."""
    total, seen = 0, False
    for f in fields:
        if f in d:
            seen = True
            try:
                total += int(d.get(f) or 0)
            except (TypeError, ValueError):
                pass
    return total, seen


def _as_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def extract_tokens(result_json):
    """(tokens_in, tokens_out) из json-ответа claude -p (--output-format json), иначе (None, None).
    tokens_in — ПОЛНЫЙ вход (input + cacheRead + cacheCreation), НЕ голое input: с кэшем промпта
    поле input почти пусто, и метрика соврала бы о расходе (класс suggest._mu_input_total). Источники
    по порядку: usage (агрегат, snake_case) → сумма modelUsage[*] (по головам, camelCase). Устойчиво к
    отсутствию ключей и не-dict входу (текст-режим ПК без --output-format json → (None, None))."""
    if not isinstance(result_json, dict):
        return None, None
    usage = result_json.get("usage")
    if isinstance(usage, dict):
        ti, ti_seen = _sum_fields(usage, _USAGE_IN_FIELDS)
        to = _as_int(usage.get("output_tokens"))
        if ti_seen or to is not None:
            return (ti if ti_seen else None), to
    mu = result_json.get("modelUsage")
    if isinstance(mu, dict):
        si = so = 0
        seen = False
        for v in mu.values():
            if not isinstance(v, dict):
                continue
            _in, in_seen = _sum_fields(v, _MU_IN_FIELDS)
            out = v.get("outputTokens")
            if out is None:
                out = v.get("output_tokens")
            out = _as_int(out)
            if in_seen:
                si += _in
                seen = True
            if out is not None:
                so += out
                seen = True
        if seen:
            return si, so
    return None, None


def selfheal_count(*texts):
    """Число самопочинок по маркерам «[самопочинка … попытка N]» в тексте(ах) задачи (0, если нет)."""
    best = 0
    for t in texts:
        for m in _SELFHEAL_RE.finditer(str(t or "")):
            try:
                best = max(best, int(m.group(1)))
            except ValueError:
                pass
    return best


if __name__ == "__main__":                                   # способ ПОКАЗАТЬ признаки владельцу
    import sys as _sys
    _a = _sys.argv[1:]
    if _a and _a[0] == "--type":
        for _t in _a[1:]:
            _lbl, _mk = task_type_explain(_t)
            print("type=%-6s признак=%-22s текст=%s"
                  % (_lbl, _mk or "(нет → честное other)", str(_t)[:96]))
    else:
        print("ПРИЗНАКИ ТИПА ЗАДАЧИ (порядок решает, первый сработавший побеждает):")
        for _grp, _lbl, _human in _T_GROUPS:
            print("  %-6s %-22s %s" % (_lbl, "(" + _human + ")", ", ".join(_grp)))
        print("  other  (ничего не совпало)   — не гадаем, помечаем честно")
        print("")
        print("Проверить фразу:  python3 task_metrics.py --type \"<текст задания>\"")
