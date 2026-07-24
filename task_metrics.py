# -*- coding: utf-8 -*-
"""task_metrics — ЕДИНАЯ структурная строка метрик на одну задачу (наблюдаемость обеих полос: pc/vps).

Одна задача = одна строка «METRICS key=value …» в лог демона (orchestrator_daemon.log / лог
pc_orchestrator). Формат ОДИН для ПК и VPS (файл байт-в-байт копируется в оба репо), поля —
только `key=value` без пробелов внутри значений → строка тривиально грепается и парсится:

    METRICS task=<id> lane=<pc|vps> model=<модель> effort=<уровень> start=<iso> end=<iso>
            dur_s=<int> outcome=<исход> attempts=<n> selfheals=<n> tokens_in=<n|na> tokens_out=<n|na>

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
                 attempts, selfheals, tokens_in=None, tokens_out=None):
    """Собрать каноническую однострочную запись METRICS. dur_s → целые секунды; счётчики → int;
    None-токены → 'na'. Формат ЗАМОРОЖЕН — правка ломает голдены (test_metrics_line/TestMetricsLine)."""
    return (
        "METRICS "
        f"task={_cell(task)} lane={_cell(lane)} model={_cell(model)} effort={_cell(effort)} "
        f"start={_cell(start_iso)} end={_cell(end_iso)} dur_s={int(round(float(dur_s)))} "
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
