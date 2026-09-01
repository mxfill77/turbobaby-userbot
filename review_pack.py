"""Ступень 1 внешнего ревью-контура — СБОРКА пакета второго мнения (без отправки).

Пакет собирается поверх уже существующего сборщика контекста
(:mod:`hq_context_pack`): чтение файлов, вычисление хешей, отсев запрещённых имён
и правило «обязательный источник не влез → blocked» живут ТАМ и здесь не
переписываются. Этот модуль добавляет ровно одно: оболочку пакета второго
мнения — сводку, result packet, числа с хешами источников и ОБЯЗАТЕЛЬНЫЙ
хвост-вопросы ревьюеру.

Инварианты (те же, что у H1, и по той же причине):

* **Чистота.** Ни сети, ни подпроцессов, ни записи на диск, ни чтения часов.
  Единственный ввод-вывод — UTF-8 чтения файлов, которые вызывающий назвал
  ЯВНО в манифесте; их делает ``hq_context_pack``. Дата сборки — ПОЛЕ ВХОДА,
  а не ``datetime.now()``: пакет обязан пересобираться байт в байт.
* **Fail-closed.** Обязательный источник, который не влез в потолок (15000
  знаков), не читается или попал под запрет имени — это ``blocked`` без тела,
  а не молча урезанный пакет. Необязательный источник опускается ТОЛЬКО с
  записью в ``omitted[]`` с путём, ролью, полосой и причиной.
* **Ничего не выдумывать.** Сводка, статусы, числа — это то, что вызывающий
  ОБЪЯВИЛ; модуль их не выводит из тела источников и не переписывает. Число,
  чей источник опущен, теряет доказательство и уходит в ``unknowns``, а не
  печатается как подтверждённое.

Структурное/охранное нарушение спецификации случая поднимает
:class:`ReviewPackError` (весь вызов недействителен). Состояния доказательств
(не влезло, обязательный источник пропал) — ``blocked``-словарь, а не исключение.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import re

import hq_context_pack as hcp

SCHEMA = "turbobaby.review_pack/v1"

# Потолок пакета — 15000 знаков ВСЕГО РЕНДЕРА, а не одного тела выдержек:
# ревьюер получает файл целиком, и шапка с таблицами хешей в его окно тоже
# входит. Мерить только тело значило бы обещать потолок, которого нет.
REVIEW_MAX_CHARS = 15000

PACK_KINDS = ("chain", "digest")
PACK_STATUSES = ("ok", "blocked")

_KIND_TITLE = {
    "chain": "закрытая цепочка с операционным изменением",
    "digest": "суточный дайджест",
}

# Хвост обязателен и неизменяем: смысл ступени 1 — не «показать работу», а
# получить второе мнение ровно по трём вопросам. Формулировки нарочно
# отрицательные («не делать вовсе»), потому что вопрос «что улучшить» всегда
# получает ответ «добавить ещё», а нам нужен обратный ход.
TAIL_QUESTIONS = (
    "Что здесь УПРОЩАЕМО: какой кусок решает ту же задачу меньшим числом деталей?",
    "Чего НЕ ДЕЛАТЬ ВОВСЕ: какая часть работы не нужна — не «сделать иначе», а не делать?",
    "Где ПЕРЕУСЛОЖНЕНО: какая конструкция дороже своей пользы (лишний слой, ручка, статус, файл)?",
)

SUMMARY_LINE_MAX = 400
SUMMARY_LINES_MAX = 20
NUMBERS_MAX = 60

# ГИПОТЕЗА ШТАБА — текст ПОСТАНОВКИ, если она была. Живёт ОТДЕЛЬНЫМ полем и
# отдельным разделом, а не строкой сводки, ровно по одной причине: постановка —
# это то, чего от работы ХОТЕЛИ, а сводка и числа — то, что вышло. Слепив их,
# ревьюер получает гипотезу под видом факта и начинает судить работу её же
# меркой; разделив — может сказать «сделано не то, что просили» или «просили
# лишнего», а это ровно те два ответа, ради которых контур существует.
#
# Поле НЕОБЯЗАТЕЛЬНОЕ и различает три состояния, а не два:
#   ключа в случае нет  → раздела нет вовсе (пакеты ступени 1 рендерятся байт в
#                         байт как прежде — их sha256 не сдвинулся ни на бит);
#   ключ есть, пусто    → раздел ЕСТЬ и говорит «постановки не было» (молчание
#                         объявленное, а не молчание по недосмотру);
#   ключ есть, записи   → раздел с записями.
HYPOTHESIS_TEXT_MAX = 2000
HYPOTHESIS_RECORDS_MAX = 20

_RE_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_NA = "—"


class ReviewPackError(ValueError):
    """Структурно или охранно недействительная спецификация случая.

    ``reason`` — стабильный машинный слаг, ``detail`` несёт контекст.
    """

    def __init__(self, reason, detail=None):
        self.reason = reason
        self.detail = detail
        super().__init__("%s: %s" % (reason, detail) if detail else reason)


# ───────────────────────────── валидация входа ─────────────────────────────


def _norm_rel(path):
    # Ровно та же нормализация, что у сборщика контекста: иначе «docs/a.md» и
    # «./docs/a.md» разъедутся между манифестом и ссылками чисел, и связь
    # «число → хеш источника» порвётся молча.
    return hcp._norm_rel(path)


def _require_str(value, reason, detail):
    if not isinstance(value, str) or not value.strip():
        raise ReviewPackError(reason, detail)
    return value


def _validate_date(value, field):
    if not isinstance(value, str) or not _RE_DATE.match(value):
        raise ReviewPackError("invalid_date", "%s must be YYYY-MM-DD, got %r" % (field, value))
    year, month, day = (int(part) for part in value.split("-"))
    try:
        datetime.date(year, month, day)
    except ValueError as exc:
        raise ReviewPackError("invalid_date", "%s=%r is not a calendar date (%s)" % (field, value, exc))
    return value


def _validate_summary(summary):
    if not isinstance(summary, (list, tuple)) or not summary:
        raise ReviewPackError("invalid_summary", "summary must be a non-empty list of lines")
    if len(summary) > SUMMARY_LINES_MAX:
        raise ReviewPackError(
            "invalid_summary", "summary has %d lines, max %d" % (len(summary), SUMMARY_LINES_MAX)
        )
    out = []
    for i, line in enumerate(summary):
        _require_str(line, "invalid_summary", "summary[%d] must be a non-empty str" % i)
        if len(line) > SUMMARY_LINE_MAX:
            raise ReviewPackError(
                "invalid_summary", "summary[%d] length %d exceeds %d" % (i, len(line), SUMMARY_LINE_MAX)
            )
        if "\n" in line or "\r" in line:
            raise ReviewPackError("invalid_summary", "summary[%d] must be a single line" % i)
        out.append(line)
    return out


def _validate_result_packets(kind, result_packets, manifest_index):
    if not isinstance(result_packets, (list, tuple)) or not result_packets:
        raise ReviewPackError("invalid_result_packets", "result_packets must be a non-empty list")
    # Закрытая цепочка описывается РОВНО одним result packet: две расписки на
    # одну цепочку означают, что склеены две цепочки, и ревьюер не поймёт, к
    # какой относится сводка.
    if kind == "chain" and len(result_packets) != 1:
        raise ReviewPackError(
            "invalid_result_packets", "kind=chain requires exactly one result packet, got %d" % len(result_packets)
        )
    out = []
    for i, rec in enumerate(result_packets):
        if not isinstance(rec, dict):
            raise ReviewPackError("invalid_result_packets", "result_packets[%d] must be a dict" % i)
        task_id = _require_str(rec.get("task_id"), "invalid_result_packets", "result_packets[%d].task_id" % i)
        path = _require_str(rec.get("path"), "invalid_result_packets", "result_packets[%d].path" % i)
        reported_status = _require_str(
            rec.get("reported_status"), "invalid_result_packets", "result_packets[%d].reported_status" % i
        )
        norm = _norm_rel(path)
        source = manifest_index.get(norm)
        if source is None:
            raise ReviewPackError(
                "result_packet_not_in_manifest",
                "result_packets[%d] path=%r is not among the declared sources" % (i, path),
            )
        # Расписка цепочки — это и есть предмет ревью. Объявить её
        # необязательной значит разрешить пакет, из которого она вылетела по
        # потолку: ревьюер получил бы сводку без того, что она описывает.
        if not source.get("required"):
            raise ReviewPackError(
                "result_packet_not_required_source",
                "result_packets[%d] path=%r must be declared required=True" % (i, path),
            )
        out.append({"task_id": task_id, "path": norm, "reported_status": reported_status})
    return out


def _validate_numbers(numbers, manifest_index):
    if numbers is None:
        return []
    if not isinstance(numbers, (list, tuple)):
        raise ReviewPackError("invalid_numbers", "numbers must be a list")
    if len(numbers) > NUMBERS_MAX:
        raise ReviewPackError("invalid_numbers", "numbers has %d entries, max %d" % (len(numbers), NUMBERS_MAX))
    out = []
    for i, rec in enumerate(numbers):
        if not isinstance(rec, dict):
            raise ReviewPackError("invalid_numbers", "numbers[%d] must be a dict" % i)
        name = _require_str(rec.get("name"), "invalid_numbers", "numbers[%d].name" % i)
        value = rec.get("value")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ReviewPackError("invalid_numbers", "numbers[%d].value must be str/int/float" % i)
        if isinstance(value, float) and not math.isfinite(value):
            raise ReviewPackError("invalid_numbers", "numbers[%d].value must be finite" % i)
        source = _require_str(rec.get("source"), "invalid_numbers", "numbers[%d].source" % i)
        norm = _norm_rel(source)
        # Число без названного источника — это мнение, а не число. Здесь
        # проверяется только объявленная связь; факт, что источник ДОЕХАЛ,
        # проверяется после сборки (иначе число печаталось бы с чужим хешем).
        if norm not in manifest_index:
            raise ReviewPackError(
                "number_source_not_in_manifest",
                "numbers[%d] source=%r is not among the declared sources" % (i, source),
            )
        out.append({"name": name, "value": value, "source": norm})
    return out


def _validate_hypothesis(value):
    """Гипотеза Штаба → нормализованный список записей. Неявное «не было» → [].

    Записи — пары «чья постановка» + «её текст». Список, а не строка, потому что
    у дайджеста постановок столько же, сколько цепочек, и слепить их в один
    абзац значило бы потерять, какая гипотеза к какой работе.
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ReviewPackError("invalid_hypothesis", "hypothesis must be a list of records or null")
    if len(value) > HYPOTHESIS_RECORDS_MAX:
        raise ReviewPackError(
            "invalid_hypothesis", "hypothesis has %d records, max %d" % (len(value), HYPOTHESIS_RECORDS_MAX)
        )
    out = []
    for i, rec in enumerate(value):
        if not isinstance(rec, dict):
            raise ReviewPackError("invalid_hypothesis", "hypothesis[%d] must be a dict" % i)
        source = _require_str(rec.get("source"), "invalid_hypothesis", "hypothesis[%d].source must be a non-empty str" % i)
        text = _require_str(rec.get("text"), "invalid_hypothesis", "hypothesis[%d].text must be a non-empty str" % i)
        if len(text) > HYPOTHESIS_TEXT_MAX:
            raise ReviewPackError(
                "oversized_hypothesis",
                "hypothesis[%d].text length %d exceeds %d" % (i, len(text), HYPOTHESIS_TEXT_MAX),
            )
        out.append({"source": source, "text": text})
    return out


def _index_manifest(sources):
    if not isinstance(sources, (list, tuple)) or not sources:
        raise ReviewPackError("invalid_sources", "case.sources must be a non-empty list")
    index = {}
    for i, rec in enumerate(sources):
        if not isinstance(rec, dict):
            raise ReviewPackError("invalid_sources", "sources[%d] must be a dict" % i)
        path = rec.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ReviewPackError("invalid_sources", "sources[%d].path must be a non-empty str" % i)
        index[_norm_rel(path)] = rec
    return index


def _validate_case(case):
    if not isinstance(case, dict):
        raise ReviewPackError("invalid_case", "case must be a dict")

    kind = case.get("kind")
    if kind not in PACK_KINDS:
        raise ReviewPackError("invalid_kind", "kind=%r not in %r" % (kind, list(PACK_KINDS)))

    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not _RE_CASE_ID.match(case_id):
        # case_id уходит в ИМЯ ФАЙЛА пакета — поэтому это слаг, а не свободный
        # текст: иначе спецификация случая правит путь записи.
        raise ReviewPackError("invalid_case_id", "case_id=%r must match %s" % (case_id, _RE_CASE_ID.pattern))

    task_class = case.get("task_class")
    if task_class not in hcp.TASK_CLASSES:
        raise ReviewPackError("invalid_task_class", "%r not in %r" % (task_class, list(hcp.TASK_CLASSES)))

    subject_date = _validate_date(case.get("subject_date"), "subject_date")
    build_date = _validate_date(case.get("build_date"), "build_date")

    active_objective = case.get("active_objective")
    if not isinstance(active_objective, str) or not active_objective.strip():
        raise ReviewPackError("invalid_objective", "active_objective must be a non-empty str")
    if len(active_objective) > hcp.OBJECTIVE_MAX_CHARS:
        raise ReviewPackError(
            "oversized_objective",
            "active_objective length %d exceeds %d" % (len(active_objective), hcp.OBJECTIVE_MAX_CHARS),
        )

    summary = _validate_summary(case.get("summary"))
    manifest_index = _index_manifest(case.get("sources"))
    result_packets = _validate_result_packets(kind, case.get("result_packets"), manifest_index)
    numbers = _validate_numbers(case.get("numbers"), manifest_index)

    spec = {
        "kind": kind,
        "case_id": case_id,
        "task_class": task_class,
        "subject_date": subject_date,
        "build_date": build_date,
        "active_objective": active_objective,
        "summary": summary,
        "result_packets": result_packets,
        "numbers": numbers,
        "sources": list(case["sources"]),
        "coverage_plan": case.get("coverage_plan"),
        "route": case.get("route"),
        "hypothesis_declared": "hypothesis" in case,
    }
    if spec["hypothesis_declared"]:
        spec["hypothesis"] = _validate_hypothesis(case.get("hypothesis"))
    return spec


# ───────────────────────────── сборка ─────────────────────────────


def _sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _core_keys(pack):
    """Поля, по которым строится текст. text_* и sha256 исключены нарочно.

    Иначе получилась бы петля: длина текста печатается в тексте и меняет
    длину текста. Итог сборки — по-прежнему воспроизводимый хеш, просто он
    живёт в словаре, а не в отрендеренном файле.
    """
    return {k: pack[k] for k in pack if k not in ("text_chars", "text_sha256", "sha256")}


def _finalize(pack, text):
    pack["text_chars"] = len(text)
    pack["text_sha256"] = _sha256_text(text)
    pack["sha256"] = _sha256_text(_canonical({k: pack[k] for k in pack if k != "sha256"}))
    return pack


def _blocked_core(spec, reason, detail, extra=None):
    core = {
        "schema": SCHEMA,
        "status": "blocked",
        "reason": reason,
        "detail": detail,
        "kind": spec["kind"],
        "case_id": spec["case_id"],
        "task_class": spec["task_class"],
        "subject_date": spec["subject_date"],
        "build_date": spec["build_date"],
        "active_objective": spec["active_objective"],
        "summary": spec["summary"],
        "result_packets": spec["result_packets"],
        "numbers": [],
        "sources": [],
        "omitted": [],
        "excluded": [],
        "unknowns": [],
        "body": "",
        "body_chars": 0,
        "max_chars": spec["max_chars"],
    }
    if spec.get("hypothesis_declared"):
        core["hypothesis"] = spec["hypothesis"]
    if extra:
        core.update(extra)
    return core


def _inner_pack(spec, manifest):
    return hcp.build_context_pack(
        spec["case_id"],
        spec["task_class"],
        spec["active_objective"],
        manifest,
        root=spec["root"],
        coverage_plan=spec["coverage_plan"],
        route=spec["route"],
        max_chars=spec["max_chars"],
    )


def _core_from_inner(spec, inner, omitted):
    accepted = {s["path"]: s for s in inner["sources"]}
    numbers = []
    unknowns = list(inner.get("unknowns") or [])
    for num in spec["numbers"]:
        src = accepted.get(num["source"])
        if src is None:
            # Источник числа до пакета не доехал. Печатать число с пустым
            # хешем нельзя — оно выглядело бы проверенным; это явное
            # «неизвестно», как того требует контракт H1 §11.
            numbers.append(
                {
                    "name": num["name"],
                    "value": num["value"],
                    "source": num["source"],
                    "source_sha256": None,
                    "excerpt_sha256": None,
                    "evidence": "unknown",
                }
            )
            unknowns.append(
                {
                    "source": num["source"],
                    "check": "number_evidence",
                    "snapshot_ref": inner["manifest_sha256"],
                    "blind_spot": "число %r осталось без хеша источника: источник в пакет не вошёл" % num["name"],
                }
            )
            continue
        numbers.append(
            {
                "name": num["name"],
                "value": num["value"],
                "source": num["source"],
                "source_sha256": src["source_sha256"],
                "excerpt_sha256": src["excerpt_sha256"],
                "evidence": "hashed",
            }
        )

    result_packets = []
    for rec in spec["result_packets"]:
        src = accepted.get(rec["path"])
        result_packets.append(
            {
                "task_id": rec["task_id"],
                "path": rec["path"],
                "reported_status": rec["reported_status"],
                "source_sha256": src["source_sha256"] if src else None,
            }
        )

    core = {
        "schema": SCHEMA,
        "status": "ok",
        "kind": spec["kind"],
        "case_id": spec["case_id"],
        "task_class": spec["task_class"],
        "subject_date": spec["subject_date"],
        "build_date": spec["build_date"],
        "active_objective": spec["active_objective"],
        "summary": spec["summary"],
        "result_packets": result_packets,
        "numbers": numbers,
        "manifest_sha256": inner["manifest_sha256"],
        "context_pack_sha256": inner["sha256"],
        "max_chars": spec["max_chars"],
        "body": inner["body"],
        "body_chars": inner["body_chars"],
        "sources": inner["sources"],
        "omitted": omitted,
        "excluded": inner["excluded"],
        "unknowns": unknowns,
    }
    if spec.get("hypothesis_declared"):
        core["hypothesis"] = spec["hypothesis"]
    if "coverage" in inner:
        core["coverage"] = inner["coverage"]
    return core


def _required_gap(spec, inner):
    """Обязательный источник, который в пакет не попал. → (reason, detail) | None.

    Сборщик контекста сам блокирует пропавший обязательный источник, но не
    запрещённое ИМЯ: запрещённый путь уезжает в ``excluded`` — и без этой
    проверки пакет ушёл бы ревьюеру с дырой на месте главного доказательства.
    """
    accepted = {s["path"] for s in inner["sources"]}
    excluded = {e["path"]: e for e in inner["excluded"]}
    for rec in spec["sources"]:
        if not rec.get("required"):
            continue
        path = _norm_rel(rec["path"])
        if path in accepted:
            continue
        if path in excluded:
            return (
                "required_source_denylisted",
                "обязательный источник %r отсеян запретом имени (%s)"
                % (path, excluded[path].get("matched_token")),
            )
        return ("required_source_omitted", "обязательный источник %r не вошёл в пакет" % path)
    return None


def build_review_pack(case, *, root, max_chars=REVIEW_MAX_CHARS):
    """Собрать детерминированный пакет второго мнения. → dict.

    ``case`` — объявленная спецификация случая (см. модульную docstring).
    Возвращает ``status='ok'`` либо ``status='blocked'``; в обоих случаях
    словарь сериализуем и рендерится :func:`render_review_pack`.
    """
    if not isinstance(root, str) or not root:
        raise ReviewPackError("invalid_root", "root must be a non-empty str path")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise ReviewPackError("invalid_max_chars", "max_chars must be a positive int, got %r" % (max_chars,))

    spec = _validate_case(case)
    spec["root"] = root
    spec["max_chars"] = max_chars

    required = [rec for rec in spec["sources"] if rec.get("required")]
    optional = [rec for rec in spec["sources"] if not rec.get("required")]
    if not required:
        raise ReviewPackError("no_required_source", "at least one required source must be declared")

    # ── Шаг 1. Обязательное. Не влезло — пакет blocked, а не урезан ──
    inner = _inner_pack(spec, required)
    if inner["status"] == "blocked":
        return _render_and_finalize(
            _blocked_core(
                spec,
                inner["reason"],
                inner.get("detail"),
                extra={"manifest_sha256": inner.get("manifest_sha256"), "context_pack_sha256": inner["sha256"]},
            )
        )

    gap = _required_gap(spec, inner)
    if gap is not None:
        return _render_and_finalize(
            _blocked_core(
                spec,
                gap[0],
                gap[1],
                extra={"manifest_sha256": inner["manifest_sha256"], "context_pack_sha256": inner["sha256"]},
            )
        )

    core = _core_from_inner(spec, inner, omitted=list(inner["omitted"]))
    text = render_review_pack(core)
    if len(text) > max_chars:
        return _render_and_finalize(
            _blocked_core(
                spec,
                "required_pack_exceeds_budget",
                "пакет с одними обязательными источниками занимает %d знаков при потолке %d"
                % (len(text), max_chars),
                extra={
                    "manifest_sha256": inner["manifest_sha256"],
                    "context_pack_sha256": inner["sha256"],
                    "required_chars": len(text),
                },
            )
        )

    # ── Шаг 2. Необязательное — по одному, и только если пакет ЦЕЛИКОМ влез ──
    accepted_manifest = list(required)
    for rec in optional:
        candidate_manifest = accepted_manifest + [rec]
        candidate_inner = _inner_pack(spec, candidate_manifest)
        path = _norm_rel(rec["path"])
        if candidate_inner["status"] == "blocked":
            # Необязательный источник заблокировать пакет не может: сборщик
            # блокирует только по обязательным. Значит беда общая — честнее
            # оставить прежний состав, чем принять непонятное.
            continue
        landed = {s["path"] for s in candidate_inner["sources"]}
        if path not in landed:
            # Сборщик уже записал причину (missing/unreadable/context_limit) —
            # переписывать её своей было бы враньём о причине.
            inner = candidate_inner
            accepted_manifest = candidate_manifest
            continue
        candidate_core = _core_from_inner(spec, candidate_inner, omitted=list(candidate_inner["omitted"]))
        if len(render_review_pack(candidate_core)) > max_chars:
            continue
        inner = candidate_inner
        accepted_manifest = candidate_manifest

    omitted = list(inner["omitted"])
    landed = {s["path"] for s in inner["sources"]}
    # Отсеянное по ЗАПРЕТУ ИМЕНИ в «опущено» не попадает: у него своя причина и
    # свой раздел. Иначе запрещённое имя пришло бы к ревьюеру под вывеской «не
    # влезло по потолку» — и выглядело бы поправимым поднятием потолка.
    excluded = {e["path"] for e in inner["excluded"]}
    for rec in optional:
        path = _norm_rel(rec["path"])
        if path in landed or path in excluded or any(o["path"] == path for o in omitted):
            continue
        omitted.append(
            {
                "path": path,
                "role": rec.get("role"),
                "lane": rec.get("lane"),
                "reason": "context_limit",
            }
        )

    core = _core_from_inner(spec, inner, omitted=omitted)
    return _render_and_finalize(core)


def _render_and_finalize(core):
    text = render_review_pack(core)
    return _finalize(core, text)


# ───────────────────────────── рендер ─────────────────────────────


def _fmt_value(value):
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _short(sha):
    return sha[:12] if isinstance(sha, str) and sha else _NA


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return out


def render_review_pack(pack):
    """Детерминированный текст пакета. → str.

    Чистая функция полей словаря: одинаковый вход даёт байт в байт одинаковый
    выход. Поля ``text_chars``/``text_sha256``/``sha256`` в текст не входят —
    они считаются ПО нему.
    """
    p = _core_keys(pack) if "text_sha256" in pack or "sha256" in pack else pack
    status = p.get("status")
    if status not in PACK_STATUSES:
        raise ReviewPackError("invalid_status", "status=%r not in %r" % (status, list(PACK_STATUSES)))

    lines = []
    lines.append("# ПАКЕТ ВТОРОГО МНЕНИЯ — %s" % p["case_id"])
    lines.append("")
    lines.append("schema: `%s`" % p["schema"])
    lines.append("вид: **%s** (%s)" % (p["kind"], _KIND_TITLE[p["kind"]]))
    lines.append("предмет за: **%s** · пакет собран: **%s**" % (p["subject_date"], p["build_date"]))
    lines.append("класс задачи: `%s`" % p["task_class"])
    lines.append("статус пакета: **%s**" % status)
    lines.append("потолок: %d знаков · выдержки: %d знаков" % (p["max_chars"], p.get("body_chars", 0)))
    lines.append("manifest_sha256: `%s`" % (p.get("manifest_sha256") or _NA))
    lines.append("context_pack_sha256: `%s`" % (p.get("context_pack_sha256") or _NA))
    lines.append("")
    lines.append("Это ЗАПРОС ВТОРОГО МНЕНИЯ, а не отчёт о приёмке. Ничего наружу не отправлено,")
    lines.append("ничего не запущено; статусы ниже — ЗАЯВЛЕННЫЕ исполнителем, не проверенные.")
    lines.append("")

    if status == "blocked":
        lines.append("## ПАКЕТ ЗАБЛОКИРОВАН")
        lines.append("")
        lines.append("причина: `%s`" % p.get("reason"))
        lines.append("")
        lines.append(p.get("detail") or _NA)
        lines.append("")
        lines.append("Обязательный источник не влез в потолок либо не доехал. Пакет НЕ урезан")
        lines.append("молча: ревьюер получает отказ, а не половину доказательства.")
        lines.append("")

    lines.append("## ЦЕЛЬ")
    lines.append("")
    lines.append(p["active_objective"])
    lines.append("")

    # ГИПОТЕЗА идёт СРАЗУ за целью и ДО фактов — и с оговоркой прямо в заголовке.
    # Ниже неё всё проверяемо хешами, она — нет; ревьюер обязан видеть границу
    # раньше, чем начнёт читать числа.
    if "hypothesis" in p:
        lines.append("## ГИПОТЕЗА ШТАБА (постановка задачи — НЕ факт и НЕ доказательство)")
        lines.append("")
        if p["hypothesis"]:
            lines.append("Ниже — то, что от работы ХОТЕЛИ. Совпадение постановки с итогом ничем")
            lines.append("не гарантировано: расхождение — законный ответ ревьюера, а не ошибка пакета.")
            lines.append("")
            for rec in p["hypothesis"]:
                lines.append("- **%s**: %s" % (rec["source"], rec["text"]))
        else:
            lines.append("%s постановки не было: работа заведена без текста задания." % _NA)
        lines.append("")

    lines.append("## СВОДКА")
    lines.append("")
    for item in p["summary"]:
        lines.append("- %s" % item)
    lines.append("")

    lines.append("## RESULT PACKET")
    lines.append("")
    rows = [
        (
            "`%s`" % rec["task_id"],
            "`%s`" % rec["path"],
            "`%s`" % rec["reported_status"],
            "`%s`" % _short(rec.get("source_sha256")),
        )
        for rec in p["result_packets"]
    ]
    lines.extend(_table(("задача", "расписка", "заявленный статус", "sha256 источника"), rows))
    lines.append("")

    lines.append("## ЧИСЛА И ХЕШИ ИСТОЧНИКОВ")
    lines.append("")
    if p["numbers"]:
        rows = [
            (
                num["name"],
                _fmt_value(num["value"]),
                "`%s`" % num["source"],
                "`%s`" % _short(num.get("source_sha256")),
                "`%s`" % _short(num.get("excerpt_sha256")),
                num.get("evidence", "unknown"),
            )
            for num in p["numbers"]
        ]
        lines.extend(_table(("число", "значение", "источник", "sha256 источника", "sha256 выдержки", "доказательство"), rows))
    else:
        lines.append("%s чисел не объявлено" % _NA)
    lines.append("")

    lines.append("## ИСТОЧНИКИ В ПАКЕТЕ")
    lines.append("")
    if p["sources"]:
        rows = [
            (
                "`%s`" % src["path"],
                src["role"],
                src["lane"],
                src["evidence_status"],
                "да" if src["required"] else "нет",
                "%d-%d" % (src["start_line"], src["end_line"]),
                str(src["excerpt_chars"]),
                "`%s`" % _short(src["source_sha256"]),
                "`%s`" % _short(src["excerpt_sha256"]),
            )
            for src in p["sources"]
        ]
        lines.extend(
            _table(
                ("путь", "роль", "полоса", "статус", "обяз.", "строки", "знаков", "sha256 источника", "sha256 выдержки"),
                rows,
            )
        )
    else:
        lines.append("%s источников в пакете нет" % _NA)
    lines.append("")

    lines.append("## ОПУЩЕНО (omitted)")
    lines.append("")
    if p["omitted"]:
        for rec in p["omitted"]:
            lines.append(
                "- `%s` · роль %s · полоса %s · причина `%s`"
                % (rec["path"], rec.get("role"), rec.get("lane"), rec.get("reason"))
            )
    else:
        lines.append("%s ничего не опущено" % _NA)
    lines.append("")

    lines.append("## ИСКЛЮЧЕНО ПО ЗАПРЕТУ ИМЕНИ (excluded)")
    lines.append("")
    if p["excluded"]:
        for rec in p["excluded"]:
            lines.append("- `%s` · причина `%s` · совпало `%s`" % (rec["path"], rec.get("reason"), rec.get("matched_token")))
    else:
        lines.append("%s запрещённых имён в манифесте не было" % _NA)
    lines.append("")

    if p.get("coverage"):
        lines.append("## ПОКРЫТИЕ ПОЛОС")
        lines.append("")
        for rec in p["coverage"]:
            lines.append("- `%s` — %s: %s" % (rec["lane"], rec["status"], rec["reason"]))
        lines.append("")

    lines.append("## НЕИЗВЕСТНО (unknowns)")
    lines.append("")
    if p["unknowns"]:
        for rec in p["unknowns"]:
            lines.append("- `%s` · проверка `%s` · %s" % (rec["source"], rec["check"], rec["blind_spot"]))
    else:
        lines.append("%s слепых пятен не заявлено" % _NA)
    lines.append("")

    if p.get("body"):
        lines.append("## ВЫДЕРЖКИ ИСТОЧНИКОВ")
        lines.append("")
        lines.append("```text")
        lines.append(p["body"].rstrip("\n"))
        lines.append("```")
        lines.append("")

    # ── ХВОСТ. Он идёт ПОСЛЕДНИМ и БЕЗ ветвлений: пакет без вопросов —
    # это отчёт о работе, а ступень 1 существует ради ответов на них.
    lines.append("## ВОПРОСЫ РЕВЬЮЕРУ (обязательный хвост)")
    lines.append("")
    for i, question in enumerate(TAIL_QUESTIONS, 1):
        lines.append("%d. %s" % (i, question))
    lines.append("")
    lines.append("Ответ ожидается по этим трём пунктам. «Всё хорошо» ответом не является:")
    lines.append("если упрощать нечего — назови, что именно проверено и почему оно неснимаемо.")

    return "\n".join(lines) + "\n"


# ───────────────────────────── адрес и индекс ─────────────────────────────


def pack_filename(pack):
    """Имя файла пакета в исходящем лотке. → str.

    Дата в имени — ДЕНЬ СБОРКИ, а не день предмета: лоток отвечает на вопрос
    «что отсюда пора отдать», и сортировка по дню сборки на него отвечает.
    День предмета живёт внутри пакета отдельным полем.
    """
    return "%s-%s.md" % (pack["build_date"], pack["case_id"])


def index_line(pack, rel_path):
    """Строка-индекс для журнала. → str (одна строка, без переносов).

    Журнал — индекс, а не хранилище тел: сюда идут адрес и числа, по которым
    пакет находится и опознаётся, а сам пакет живёт файлом.
    """
    return (
        "ARTIFACT ревью-пакет %s → %s: вид=%s, предмет=%s, статус=%s, "
        "источников=%d, опущено=%d, знаков=%d/%d, sha=%s"
        % (
            pack["case_id"],
            rel_path,
            pack["kind"],
            pack["subject_date"],
            pack["status"],
            len(pack["sources"]),
            len(pack["omitted"]),
            pack["text_chars"],
            pack["max_chars"],
            _short(pack["text_sha256"]),
        )
    )
