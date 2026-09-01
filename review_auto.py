"""Ступень A внешнего ревью-контура — ПОВОД, СБОРКА СЛУЧАЯ, УЧЁТ ЗАХОДОВ.

Ступень 1 (:mod:`review_pack`) умеет собрать пакет второго мнения из ОБЪЯВЛЕННОЙ
человеком спецификации случая. Ступень 2 (:mod:`review_send`) умеет отправить
готовый пакет в канал и разобрать ответ. Между ними до сих пор стоял человек: он
решал, ЧТО отдать на ревью и КОГДА. Ступень A убирает ровно это звено — и ничего
больше: она отвечает на три вопроса и на них останавливается.

1. **Есть ли повод.** Поводов ровно два, и оба — события своей полосы, а не
   таймер ради таймера:

   * ``chain`` — закрытая цепочка, ИЗМЕНИВШАЯ ОПЕРАЦИОННОЕ СОСТОЯНИЕ. Слово
     «изменившая» здесь не оценочное: цепочка объявила коммит, и этот коммит
     ЖИВОЙ в git (проверяет `review_auto_run`, руками). Не объявила — повода
     нет; объявила, но коммит не нашёлся — повода тоже нет, и причина названа
     отдельным словом (``commit_unverified``), потому что это разные новости.
   * ``digest`` — суточный дайджест: раз в календарные сутки, не раньше
     назначенного часа, по цепочкам, закрытым за последние сутки.

2. **Что отдать.** Спецификация случая для :mod:`review_pack` строится ЗДЕСЬ из
   фактов очереди, а не пишется руками.

3. **Сколько раз пробовать.** Отказ канала пакет не теряет: заход считается,
   повтор разрешён РОВНО ОДИН и не раньше паузы. Второй отказ закрывает повод
   словом ``abandoned`` с названной причиной — пакет остаётся в лотке.

ИНВАРИАНТЫ (те же, что у ступеней 1–2 и слоя ожиданий, и по той же причине):

* **Чистота.** Ни сети, ни диска, ни подпроцессов, ни ``getenv``, ни часов.
  «Сейчас» — ПОЛЕ ВХОДА (``now_iso``), а не ``datetime.now()``: решение о поводе
  обязано воспроизводиться на тех же фактах байт в байт. Держит тест
  ``REVIEW_AUTO_PURE`` обходом AST.
* **Fail-closed у стражи.** Постановка Штаба — единственный кусок пакета,
  который приходит СВОБОДНЫМ ТЕКСТОМ от человека, и потому единственный, где
  абсолютный путь или контакт клиента могут приехать наружу. Санитайзер режет
  их ТЕМИ ЖЕ правилами, которыми ступень 2 задерживает исходящее
  (:data:`review_send._OUTBOUND_RULES`), и заканчивает проверкой этой же стражей:
  что она всё ещё видит — снимается целой строкой. Не сошлось за три прохода —
  постановка снимается целиком. Инвариант в одну строку:
  ``outbound_violations(sanitize(x)) == []`` для ЛЮБОГО ``x``.
* **Ничего не выдумывать.** Гипотеза Штаба едет отдельным полем пакета и с
  оговоркой «не факт». Сводка случая содержит только то, что взято из строки
  очереди и из проверенного коммита.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ НИ ОДНОЙ ВЕТКОЙ: не исполняет находки ревьюера, не
ставит задач, не правит код и не читает ответы каналов вовсе. Ответ приходит в
лоток файлом и адресован ЧЕЛОВЕКУ — это ступень B, и её здесь нет.
"""

from __future__ import annotations

import datetime
import re

import review_pack
import review_send

SCHEMA = "turbobaby.review_auto/v1"

TRIGGER_KINDS = ("chain", "digest")

# Заходов на один повод: первый + РОВНО ОДИН повтор. Число не круглое ради
# красоты: канал платный (замер ступени 2 — 16 тыс. токенов на холодный пакет),
# и слепой повтор при лежащем канале умножает цену, ничего не узнавая нового.
MAX_ATTEMPTS = 2
# …и повтор не раньше паузы: «не чаще одного раза» читается двояко (один повтор
# ВСЕГО / не чаще раза в период), поэтому выполнены оба прочтения сразу.
RETRY_AFTER_SEC = 3600

# Сутки дайджеста — окно НАЗАД от часа сборки, а не календарный день: сутки
# полосы кончаются не в полночь UTC, и «вчерашний» дайджест на живом дне терял
# бы всю работу, закрытую после порога.
DIGEST_WINDOW_SEC = 24 * 3600

HYPOTHESIS_MAX = 1200        # постановка в пакете (потолок самого пакета — 2000)
RESULT_HEAD_MAX = 900        # голова результата задачи в расписке
SPOOL_MAX = 200              # кап спула закрытых цепочек (это индекс, не архив)

REPORTED = {"done": "reported_done", "failed": "reported_failed"}

# Коммит из результата: 7–40 hex рядом со словом-указателем. Голый hex-осколок
# без слова не берём — в отчётах полно sha256 файлов и выдержек, и принять их за
# коммит значило бы объявить операционное изменение там, где менялся только текст.
_RE_COMMIT = re.compile(
    r"(?i)\b(?:commit|коммит|коммита|коммите|коммитом|hash|хеш|хеша)\b[^0-9a-f\n]{0,40}([0-9a-f]{7,40})\b"
)

_REDACT_MARK = "[снято стражей: %s]"
_REDACT_LINE = "[строка снята стражей: %s]"
_REDACT_ALL = "[текст постановки снят стражей целиком]"

# Правила ЗАМЕНЫ шире правил ОБНАРУЖЕНИЯ, и это не небрежность. Страж ступени 2
# ловит ПРИЗНАК («D:\» с одним знаком после) — ему достаточно опознать форму и
# остановить пакет. Санитайзеру же надо снять ЗНАЧЕНИЕ целиком, иначе от пути
# останется хвост, который стража увидит снова. Поэтому здесь у каждого правила
# то же начало и жадный хвост до разделителя.
_REDACT_RULES = (
    ("абсолютный путь Windows", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]{1,2}[^\s\"'`,;)\]}]*")),
    ("абсолютный путь POSIX", re.compile(r"(?<![\w.-])/(?:root|home|etc|var|opt|usr/local)/[^\s\"'`,;)\]}]*")),
    ("сетевой путь UNC", re.compile(r"(?:(?<=^)|(?<=[\s\"'`(\[]))\\{2,4}[A-Za-z0-9_.-]{2,}\\[^\s\"'`,;)\]}]*", re.MULTILINE)),
    ("ключ провайдера", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("токен GitHub", re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("токен Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("токен бота Telegram", re.compile(r"(?<!\d)\d{8,12}:[A-Za-z0-9_-]{30,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*")),
    (
        "присвоенное значение секрету",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|secret|token|password|passwd|pwd|credential)s?\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{16,}"
        ),
    ),
    ("адрес почты", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("телефон", re.compile(r"(?<![\w.])\+\d[\d\s()-]{8,17}\d")),
    ("ник Telegram", re.compile(r"(?<![\w@/.])@[A-Za-z][A-Za-z0-9_]{4,31}\b")),
)


class ReviewAutoError(ValueError):
    """Структурно недействительный вызов. ``reason`` — машинный слаг."""

    def __init__(self, reason, detail=None):
        self.reason = reason
        self.detail = detail
        super().__init__("%s: %s" % (reason, detail) if detail else reason)


# ───────────────────────────── время (без часов) ─────────────────────────────


def parse_iso(value):
    """ISO-8601 (с ``Z`` или смещением) → aware datetime UTC. Мусор → None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone(datetime.timezone.utc)


def _require_now(now_iso):
    now = parse_iso(now_iso)
    if now is None:
        raise ReviewAutoError("invalid_now", "now_iso=%r is not an ISO timestamp" % (now_iso,))
    return now


def day_of(now_iso):
    """Календарный день UTC отметки. → 'ГГГГ-ММ-ДД'."""
    return _require_now(now_iso).date().isoformat()


# ───────────────────────────── стража постановки ─────────────────────────────


def sanitize(text, *, limit=HYPOTHESIS_MAX):
    """Свободный текст → безопасный для отправки наружу. → str.

    Порядок сознательный: сначала снимаем ЗНАЧЕНИЯ широкими правилами, потом
    режем по потолку, и только потом спрашиваем стражу ступени 2. Резать раньше
    снятия нельзя — обрезанный посередине ключ стража уже не опознает, а он от
    этого секретом быть не перестанет.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ReviewAutoError("invalid_text", "text must be a str or None")

    out = text
    for kind, rx in _REDACT_RULES:
        out = rx.sub(_REDACT_MARK % kind, out)
    out = " ".join(out.split())          # многострочная постановка → одна строка пакета
    if len(out) > limit:
        out = out[:limit].rstrip() + " …"

    # Fail-closed: что стража всё ещё видит — снимаем ЦЕЛОЙ СТРОКОЙ. Три прохода,
    # потому что снятие одной находки может открыть следующую; не сошлось — текста
    # постановки не будет вовсе. Молчание дороже утечки.
    for _ in range(3):
        found = review_send.outbound_violations(out)
        if not found:
            return out
        kinds = sorted({v["kind"] for v in found})
        out = _REDACT_LINE % ", ".join(kinds)
    return _REDACT_ALL if review_send.outbound_violations(out) else out


# ───────────────────────────── расписка цепочки ─────────────────────────────


def claimed_commits(result_text):
    """Коммиты, ОБЪЯВЛЕННЫЕ в результате задачи. → list[str] (нижний регистр, без дублей)."""
    if not isinstance(result_text, str):
        return []
    out, seen = [], set()
    for match in _RE_COMMIT.finditer(result_text):
        sha = match.group(1).lower()
        if sha not in seen:
            seen.add(sha)
            out.append(sha)
    return out


def operational_change(claimed, verified):
    """Изменила ли цепочка операционное состояние. → (bool, причина-слаг).

    Третий исход обязателен и здесь: «объявила коммит, а его нет» — это НЕ то же
    самое, что «коммита не объявляла». Первое — расхождение отчёта с деревом
    (новость сама по себе), второе — штатная задача только на чтение.
    """
    claimed = list(claimed or [])
    verified = [c for c in (verified or []) if c in claimed]
    if verified:
        return True, "commit_verified"
    if claimed:
        return False, "commit_unverified"
    return False, "no_commit_claimed"


def receipt(*, queue_id, task_text, status, result, closed_at, claimed, verified, artifacts=()):
    """Строка очереди → РАСПИСКА цепочки (то, что ляжет файлом и попадёт в пакет).

    ``task_id`` несёт ДЕНЬ, а не только номер очереди: номера переиспользуются
    (живой факт из лога демона — «номер переиспользован очередью»), и ключ дедупа
    без дня склеил бы разные работы разных дней в один повод.
    """
    if not isinstance(queue_id, int) or isinstance(queue_id, bool):
        raise ReviewAutoError("invalid_queue_id", "queue_id must be an int, got %r" % (queue_id,))
    if status not in REPORTED:
        raise ReviewAutoError("invalid_status", "status=%r not in %r" % (status, sorted(REPORTED)))
    closed = _require_now(closed_at)
    changed, reason = operational_change(claimed, verified)
    return {
        "schema": SCHEMA,
        "kind": "receipt",
        "task_id": "pc-%s-%d" % (closed.date().isoformat(), queue_id),
        "queue_id": queue_id,
        "lane": "pc",
        "closed_at": closed.isoformat().replace("+00:00", "Z"),
        "closed_day": closed.date().isoformat(),
        "reported_status": REPORTED[status],
        "hypothesis": sanitize(task_text),
        "result_head": sanitize(result, limit=RESULT_HEAD_MAX),
        "claimed_commits": list(claimed or []),
        "verified_commits": [c for c in (verified or []) if c in (claimed or [])],
        "operational_change": changed,
        "change_reason": reason,
        "artifacts": [str(a) for a in (artifacts or [])],
    }


def receipt_rel(rec):
    """Путь расписки в дереве. → str (относительный, с прямыми слэшами)."""
    return "docs/review_receipts/%s.json" % rec["task_id"]


def digest_index_rel(day):
    return "docs/review_receipts/%s-digest-index.md" % day


def spool_add(spool, rec):
    """Дописать расписку в спул закрытых цепочек. → новый список (идемпотентно по task_id)."""
    out = [r for r in (spool or []) if r.get("task_id") != rec["task_id"]]
    out.append(rec)
    out.sort(key=lambda r: (str(r.get("closed_at") or ""), str(r.get("task_id") or "")))
    return out[-SPOOL_MAX:]


# ───────────────────────────── состояние и учёт ─────────────────────────────


def state_default():
    return {"schema": SCHEMA, "triggers": {}, "digest": {"last_day": None, "last_reason": None}, "spool": []}


def state_read(raw):
    """Прочитанное состояние → нормализованное. Мусор → чистое состояние.

    Неразбор состояния НЕ равен «поводов не было»: он равен «истории нет», и
    контур начинает считать заходы заново. Молча принять половину словаря было бы
    хуже — тогда «повтор не чаще одного раза» перестал бы держаться.
    """
    if not isinstance(raw, dict):
        return state_default()
    st = state_default()
    triggers = raw.get("triggers")
    if isinstance(triggers, dict):
        for key, rec in triggers.items():
            if isinstance(key, str) and isinstance(rec, dict):
                st["triggers"][key] = dict(rec)
    digest = raw.get("digest")
    if isinstance(digest, dict):
        st["digest"] = {"last_day": digest.get("last_day"), "last_reason": digest.get("last_reason")}
    spool = raw.get("spool")
    if isinstance(spool, list):
        st["spool"] = [r for r in spool if isinstance(r, dict) and r.get("task_id")][-SPOOL_MAX:]
    return st


def attempt_allowed(state, key, now_iso):
    """Можно ли делать заход по поводу ``key`` прямо сейчас. → (bool, причина-слаг)."""
    rec = (state.get("triggers") or {}).get(key)
    if rec is None:
        return True, "first_attempt"
    if rec.get("closed"):
        return False, "already_closed"
    attempts = int(rec.get("attempts") or 0)
    if attempts >= MAX_ATTEMPTS:
        return False, "attempts_exhausted"
    last = parse_iso(rec.get("last_attempt_at"))
    now = _require_now(now_iso)
    if last is not None and (now - last).total_seconds() < RETRY_AFTER_SEC:
        return False, "retry_too_soon"
    return True, "retry_allowed"


def note_attempt(state, key, kind, now_iso, pack_rel=None):
    """Отметить НАЧАТЫЙ заход. → новое состояние (вход не мутируется)."""
    st = state_read(state)
    rec = dict(st["triggers"].get(key) or {})
    rec.update(
        {
            "kind": kind,
            "attempts": int(rec.get("attempts") or 0) + 1,
            "last_attempt_at": _require_now(now_iso).isoformat().replace("+00:00", "Z"),
            "closed": bool(rec.get("closed")),
        }
    )
    if pack_rel:
        rec["pack"] = pack_rel
    st["triggers"][key] = rec
    return st


def note_outcome(state, key, outcomes, reasons, now_iso):
    """Отметить ИСХОД захода. → (новое состояние, вердикт-слаг).

    Вердикты: ``answered`` — хотя бы один канал ответил, повод закрыт;
    ``retry`` — не ответил никто, но заход остался; ``abandoned`` — заходы
    исчерпаны, повод закрыт БЕЗ ответа и с названной причиной. Пакет ни в одной
    ветке не трогается: он лежит в лотке и остаётся доказательством.
    """
    st = state_read(state)
    rec = dict(st["triggers"].get(key) or {})
    rec["last_outcomes"] = list(outcomes or [])
    rec["last_reasons"] = list(reasons or [])
    rec["last_outcome_at"] = _require_now(now_iso).isoformat().replace("+00:00", "Z")
    if "answered" in (outcomes or []):
        rec["closed"] = True
        rec["verdict"] = "answered"
    elif int(rec.get("attempts") or 0) >= MAX_ATTEMPTS:
        rec["closed"] = True
        rec["verdict"] = "abandoned"
    else:
        rec["closed"] = False
        rec["verdict"] = "retry"
    st["triggers"][key] = rec
    return st, rec["verdict"]


def note_digest_day(state, day, reason):
    st = state_read(state)
    st["digest"] = {"last_day": day, "last_reason": reason}
    return st


def refusal_line(key, verdict, outcomes, reasons, pack_rel):
    """Одна строка отказа для журнала и лога. → str.

    Отказ обязан НАЗЫВАТЬ канал и причину и НАЗЫВАТЬ адрес пакета: «не
    отправилось» без адреса означало бы, что пакет потерян, а он не потерян.
    """
    pairs = ", ".join(
        "%s=%s" % (o, r) for o, r in zip(list(outcomes or []), list(reasons or []))
    ) or "каналов не было"
    tail = {
        "retry": "повтор разрешён РОВНО ОДИН, не раньше %d мин" % (RETRY_AFTER_SEC // 60),
        "abandoned": "заходы исчерпаны, повтора не будет",
        "answered": "ответ получен",
    }.get(verdict, verdict)
    return "ревью-контур: повод %s → %s (%s); пакет НЕ потерян — %s; %s" % (
        key, verdict, pairs, pack_rel or "пакет не собран", tail
    )


# ───────────────────────────── поводы ─────────────────────────────


def chain_trigger(state, now_iso):
    """Ближайший неотработанный повод «закрытая цепочка». → dict | None.

    Берём САМУЮ СТАРУЮ подходящую: очередь поводов — очередь, а не стек;
    свежая новость, обгоняющая вчерашнюю, оставила бы вчерашнюю навсегда.
    """
    st = state_read(state)
    for rec in st["spool"]:
        if not rec.get("operational_change"):
            continue
        key = "chain:%s" % rec["task_id"]
        ok, why = attempt_allowed(st, key, now_iso)
        if not ok:
            continue
        return {"kind": "chain", "key": key, "receipt": rec, "why": why}
    return None


def digest_trigger(state, now_iso, digest_hour):
    """Повод «суточный дайджест». → dict | None.

    ``receipts`` может оказаться ПУСТЫМ — это законный исход, а не отсутствие
    повода: дайджест наступил, отдавать нечего. Вызывающий закрывает день
    причиной ``empty``, и день не дёргается снова до завтра.
    """
    st = state_read(state)
    now = _require_now(now_iso)
    day = now.date().isoformat()
    last = st["digest"].get("last_day")
    if isinstance(last, str) and last >= day:
        return None
    if not isinstance(digest_hour, int) or isinstance(digest_hour, bool) or not (0 <= digest_hour <= 23):
        raise ReviewAutoError("invalid_digest_hour", "digest_hour=%r must be an int 0..23" % (digest_hour,))
    if now.hour < digest_hour:
        return None
    edge = now - datetime.timedelta(seconds=DIGEST_WINDOW_SEC)
    window = []
    for rec in st["spool"]:
        closed = parse_iso(rec.get("closed_at"))
        if closed is not None and closed > edge:
            window.append(rec)
    key = "digest:%s" % day
    ok, why = attempt_allowed(st, key, now_iso)
    if not ok:
        return None
    return {"kind": "digest", "key": key, "day": day, "receipts": window, "why": why}


def next_trigger(state, now_iso, digest_hour):
    """Один повод на виток, цепочка вперёд дайджеста. → dict | None.

    Порядок не вкусовой: цепочка — СОБЫТИЕ (её ценность падает с возрастом),
    дайджест — расписание (он никуда не денется до конца суток). Обратный порядок
    задерживал бы событие ровно на цену дайджеста.
    """
    return chain_trigger(state, now_iso) or digest_trigger(state, now_iso, digest_hour)


# ───────────────────────────── спецификация случая ─────────────────────────────


ARTIFACT_HEAD_LINES = 80     # сколько строк артефакта берём в пакет (голова = заголовок и итог)


def _source(path, *, role, required, line_counts, cap=None, evidence_status="reported"):
    """Запись манифеста источника. ``line_counts`` — ФАКТ с диска, принесённый руками.

    Границы строк выдуманными быть не могут: сборщик контекста сверяет их с
    файлом и падает на выходе за конец (`line_range_out_of_bounds`). Поэтому
    длина приходит сюда числом, а не догадкой «ну пусть будет 400».
    """
    total = int((line_counts or {}).get(path) or 1)
    end = max(1, min(total, cap) if cap else total)
    return {
        "path": path,
        "role": role,
        "lane": "pc",
        "evidence_status": evidence_status,
        "required": required,
        "start_line": 1,
        "end_line": end,
    }


def _clip(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _commit_words(rec):
    if rec["verified_commits"]:
        return "коммит подтверждён в git: %s" % ", ".join(c[:12] for c in rec["verified_commits"])
    if rec["claimed_commits"]:
        return "коммит объявлен, но в дереве НЕ найден (%s)" % ", ".join(c[:12] for c in rec["claimed_commits"])
    return "коммита не объявлено — работа только на чтение"


def case_for_chain(rec, build_date, *, line_counts=None, artifact_sources=(), held_artifacts=()):
    """Расписка закрытой цепочки → спецификация случая для :mod:`review_pack`.

    ``task_class`` = ``code_green`` не по умолчанию, а по определению повода:
    сюда доходят ТОЛЬКО цепочки с подтверждённым коммитом, то есть работа с
    зелёным кодом. Красных классов контур не собирает вовсе — их решает человек.
    """
    if not rec.get("operational_change"):
        raise ReviewAutoError("not_operational", "цепочка %r не меняла операционного состояния" % rec.get("task_id"))
    rel = receipt_rel(rec)
    sources = [_source(rel, role="evidence", required=True, line_counts=line_counts)]
    for path in artifact_sources:
        # Артефакт едет ГОЛОВОЙ, а не целиком: их пишут на тысячи строк, и
        # честный потолок пакета съедался бы одним из них. Обмана нет — границы
        # строк печатаются в таблице источников, ревьюер видит «1-80» сам.
        sources.append(_source(path, role="context", required=False,
                               line_counts=line_counts, cap=ARTIFACT_HEAD_LINES))
    summary = [
        "Повод: ЗАКРЫТАЯ ЦЕПОЧКА полосы ПК %s, изменившая операционное состояние." % rec["task_id"],
        "Заявленный исполнителем статус: %s; %s." % (rec["reported_status"], _commit_words(rec)),
        "Приёмки человеком у этой цепочки НЕТ: статус заявлен исполнителем, коммит подтверждён "
        "только фактом существования в дереве — не содержанием.",
        "Пакет собран и отправлен АВТОМАТИЧЕСКИ витком демона; ответ ревьюера не исполняется "
        "ни одной веткой контура и адресован человеку.",
    ]
    # Задержанный артефакт называется в САМОМ пакете, а не только в логе: ревьюеру важно
    # знать, что контекст неполон, и ПОЧЕМУ он неполон. «Просто не приложили» и
    # «приложить нельзя, там абсолютные пути» — разные новости.
    for held in held_artifacts or ():
        summary.append(
            "НЕ ПРИЛОЖЕНО стражей исходящего: %s (%s) — файл остаётся в дереве, наружу не уехал."
            % (held.get("path"), ", ".join(held.get("kinds") or []))
        )
    numbers = [
        {"name": "цепочка · подтверждённых коммитов", "value": len(rec["verified_commits"]), "source": rel},
        {"name": "цепочка · объявленных коммитов", "value": len(rec["claimed_commits"]), "source": rel},
    ]
    hypothesis = []
    if rec.get("hypothesis"):
        hypothesis.append({"source": "задача очереди #%d (%s)" % (rec["queue_id"], rec["task_id"]), "text": rec["hypothesis"]})
    return {
        "kind": "chain",
        "case_id": "chain-%s" % rec["task_id"],
        "task_class": "code_green",
        "subject_date": rec["closed_day"],
        "build_date": build_date,
        "active_objective": (
            "Получить второе мнение по закрытой цепочке %s полосы ПК: что из сделанного упрощаемо, "
            "чего не стоило делать вовсе, где переусложнено." % rec["task_id"]
        ),
        "summary": summary,
        "hypothesis": hypothesis,
        "result_packets": [
            {"task_id": rec["task_id"], "path": rel, "reported_status": rec["reported_status"]}
        ],
        "numbers": numbers,
        "sources": sources,
    }


DIGEST_HYPOTHESIS_MAX = 300     # постановка в дайджесте — головой: их там столько же, сколько цепочек
# ПОТОЛОК приложенных расписок, а не их число: сколько влезет на самом деле, меряет
# `review_auto_run._fit_digest` сборкой. Четыре — это верхняя граница попытки, и она уже
# однажды не влезла (16046 при 15000, живой дайджест 01.09).
DIGEST_RECEIPTS_MAX = 4


def case_for_digest(receipts, day, build_date, *, line_counts=None, receipts_in_pack=DIGEST_RECEIPTS_MAX):
    """Цепочки суток → спецификация случая дайджеста.

    ПОЛНОТУ СПИСКА держит ИНДЕКС дня, а не расписки, и это развилка, решённая
    правилом ступени 1, а не вкусом. Ступень 1 запрещает объявлять расписку
    необязательным источником (``result_packet_not_required_source``): пакет, из
    которого расписка вылетела по потолку, — это сводка без того, что она
    описывает. Значит расписки обязаны быть обязательными, а обязательных за
    сутки бывает больше, чем влезает. Выход не в ослаблении правила, а в
    разделении ролей: ИНДЕКС дня (обязательный, одна строка на цепочку)
    перечисляет ВСЕ цепочки — его полнота от потолка не зависит; расписок едет
    первые ``receipts_in_pack``, и их число НАЗВАНО в сводке пакета числом, а не
    умолчано. Ревьюер видит и полный список, и границу приложенных доказательств.
    """
    if not isinstance(receipts, (list, tuple)):
        raise ReviewAutoError("invalid_receipts", "receipts must be a list")
    index_rel = digest_index_rel(day)
    sources = [_source(index_rel, role="evidence", required=True, line_counts=line_counts)]
    ordered = sorted(receipts, key=lambda r: str(r.get("closed_at") or ""))
    for rec in ordered[:receipts_in_pack]:
        sources.append(_source(receipt_rel(rec), role="evidence", required=True, line_counts=line_counts))
    changed = [r for r in ordered if r.get("operational_change")]
    summary = [
        "Повод: СУТОЧНЫЙ ДАЙДЖЕСТ полосы ПК за сутки до %s." % build_date,
        "Цепочек в окне суток: %d, из них с подтверждённым коммитом: %d." % (len(ordered), len(changed)),
        "Все статусы ЗАЯВЛЕНЫ исполнителями; независимой приёмки ни у одной цепочки в пакете нет.",
        "Полнота списка держится ИНДЕКСОМ дня; расписок приложено %d из %d — остальные цепочки "
        "названы в индексе строкой, но своей расписки в этом пакете не имеют."
        % (min(len(ordered), receipts_in_pack), len(ordered)),
        "Пакет собран и отправлен АВТОМАТИЧЕСКИ витком демона; ответ ревьюера не исполняется.",
    ]
    numbers = [
        {"name": "дайджест · цепочек за сутки", "value": len(ordered), "source": index_rel},
        {"name": "дайджест · с подтверждённым коммитом", "value": len(changed), "source": index_rel},
        {"name": "дайджест · расписок приложено", "value": min(len(ordered), receipts_in_pack), "source": index_rel},
    ]
    # Постановки едут ГОЛОВОЙ и только у приложенных расписок: полный текст
    # двадцати заданий съел бы потолок целиком, а гипотеза — не доказательство,
    # ей отведено место по остаточному принципу СОЗНАТЕЛЬНО.
    hypothesis = [
        {
            "source": "задача очереди #%d (%s)" % (r["queue_id"], r["task_id"]),
            "text": _clip(r["hypothesis"], DIGEST_HYPOTHESIS_MAX),
        }
        # Потолок записей берётся У СТУПЕНИ 1, а не дублируется числом здесь:
        # разъехавшиеся константы дали бы `oversized_hypothesis` на сборке.
        for r in ordered[: min(receipts_in_pack, review_pack.HYPOTHESIS_RECORDS_MAX)]
        if r.get("hypothesis")
    ]
    return {
        "kind": "digest",
        "case_id": "digest-%s" % day,
        "task_class": "code_green",
        "subject_date": day,
        "build_date": build_date,
        "active_objective": (
            "Получить второе мнение по суточному дайджесту закрытых цепочек полосы ПК за %s: "
            "что из сделанного упрощаемо, чего не стоило делать вовсе, где переусложнено." % day
        ),
        "summary": summary,
        "hypothesis": hypothesis,
        "result_packets": [
            {"task_id": r["task_id"], "path": receipt_rel(r), "reported_status": r["reported_status"]}
            for r in ordered[:receipts_in_pack]
        ],
        "numbers": numbers,
        "sources": sources,
    }


def digest_index_text(receipts, day, build_date):
    """Индекс дня — обязательный источник дайджеста. → str (детерминированный текст)."""
    ordered = sorted(receipts, key=lambda r: str(r.get("closed_at") or ""))
    lines = [
        "# ИНДЕКС ЗАКРЫТЫХ ЦЕПОЧЕК ПОЛОСЫ ПК — сутки до %s" % build_date,
        "",
        "Список ПОЛНЫЙ: одна строка на цепочку, закрытую в окне суток. Строится из очереди",
        "автоматически; статусы — заявленные исполнителем, коммиты — подтверждённые в дереве.",
        "",
        "день дайджеста: %s · цепочек: %d" % (day, len(ordered)),
        "",
    ]
    if not ordered:
        lines.append("— за сутки не закрылось ни одной цепочки")
        return "\n".join(lines) + "\n"
    lines.append("| цепочка | закрыта | статус | операционное изменение | коммиты |")
    lines.append("|---|---|---|---|---|")
    for rec in ordered:
        lines.append(
            "| `%s` | %s | `%s` | %s | %s |"
            % (
                rec["task_id"],
                rec["closed_at"],
                rec["reported_status"],
                "да" if rec.get("operational_change") else "нет (%s)" % rec.get("change_reason"),
                ", ".join(c[:12] for c in rec.get("verified_commits") or []) or "—",
            )
        )
    return "\n".join(lines) + "\n"
