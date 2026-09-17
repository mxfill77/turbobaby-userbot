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

# ПОСТАНОВКА В ПАКЕТЕ. Было 1200 при объявленном потолке ступени 1 в 2000 — то есть
# ТЗ резалось МОЛЧА и на треть раньше, чем обещано (замер 05.09: расписка задачи 173
# несёт ровно 1202 знака постановки, обрыв пришёлся внутрь первого пункта). Потолок
# НЕ ПОДНЯТ: 2000 — это `review_pack.HYPOTHESIS_TEXT_MAX`, он был объявлен и раньше;
# снята молчаливая недодача внутри него. Константа берётся у ступени 1, а не
# дублируется числом: разъехавшись, они дали бы `oversized_hypothesis` на сборке.
HYPOTHESIS_MAX = review_pack.HYPOTHESIS_TEXT_MAX
RESULT_HEAD_MAX = 900        # голова результата задачи в расписке
SPOOL_MAX = 200              # кап спула закрытых цепочек (это индекс, не архив)

# ─────────────── ЗДОРОВЬЕ ВНЕШНЕГО КАНАЛА (заведено 05.09.2026) ───────────────
# ЗАЧЕМ. Заход в канал СИНХРОНЕН внутри витка, и лежачий канал берёт свой полный
# предел ожидания КАЖДЫЙ раз, ничего не отдавая. Замер суток 04.09 (`pc_orchestrator.log`,
# окно 00:00–24:00): 20 прогонов ступени A, медиана 1845с, сумма 37094с = 42.9% суток;
# исход `manus` во всех двадцати — `unknown/answer_lost`, ответов ноль. Для сравнения
# те же сутки при ЖИВОМ канале: 02.09 — 22 прогона, медиана 155с, max 250с; 03.09 —
# 14 прогонов, медиана 172с, max 287с. Разница между живым и лежачим каналом —
# не проценты, а порядок.
#
# ПОРОГ ВЫБРАН ЗАМЕРОМ, А НЕ НА ГЛАЗ. Корпус лотка `docs/review_inbox` — 152 захода
# (76 codex + 76 manus), лента исходов по времени: у `codex` — 76 из 76 `answered`,
# ни одного провала; у `manus` — две серии неответов подряд, длиной 14 и 22, и
# ОДИНОЧНЫХ провалов в корпусе НОЛЬ. Значит порог в два подряд не даёт на этом
# корпусе ни одного ложного «лежит» (0 из 152), а стои́т ровно один лишний полный
# заход — это цена за то, чтобы разовая осечка не хоронила канал.
CHANNEL_DOWN_STRIKES = 2
# ОЖИВАЕТ САМ, БЕЗ ВЛАДЕЛЬЦА: лежачий канал получает пробный пакет раз в это окно.
# Шесть часов — тот же порядок, что у ожидания О4 (след жизни полосы), и он даёт
# каналу четыре шанса в сутки против измеренных серий в 14 и 22 захода.
CHANNEL_PROBE_AFTER_SEC = 6 * 3600
# ЧТО КАНАЛУ НЕ В ВИНУ. `outbound_guard` — наш пакет споткнулся о стражу исходящего,
# `sender_crashed` — упали наши руки, `no_credentials` — не задан наш ключ. Наружу в
# этих трёх случаях не ушло НИЧЕГО, и судить по ним канал значит хоронить живое за
# свою же ошибку.
CHANNEL_OUR_FAULT = frozenset({"outbound_guard", "sender_crashed", "no_credentials"})

# ──────────────── ПОВОД, А НЕ ЛЕНТА (заведено 05.09.2026) ────────────────
# Правило контура: внешних зовут ПО ПОВОДУ — закрыт крупный класс, разошлись два
# наших замера, меняется архитектура. Пакет на каждую закрытую цепочку этому не
# отвечает: за 04.09 закрытых цепочек 21, поводов по правилу — 8.
# «Крупный» назван числом подтверждённых коммитов, а не размером текста: корпус
# 103 расписок даёт 38 цепочек без коммита, 46 с одним, 15 с двумя и 4 с тремя —
# то есть два и выше отделяет работу, тронувшую не один шаг, от однострочной правки.
EVENT_MIN_COMMITS = 2

REPORTED = {"done": "reported_done", "failed": "reported_failed"}

# Коммит из результата: 7–40 hex рядом со словом-указателем. Голый hex-осколок
# без слова не берём — в отчётах полно sha256 файлов и выдержек, и принять их за
# коммит значило бы объявить операционное изменение там, где менялся только текст.
_RE_COMMIT = re.compile(
    r"(?i)\b(?:commit|коммит|коммита|коммите|коммитом|hash|хеш|хеша)\b[^0-9a-f\n]{0,40}([0-9a-f]{7,40})\b"
)

# ГРАНИЦА ТЗ. Задание кончается там, где кончается «ЧТО СДЕЛАТЬ»: ниже идут
# служебные разделы (арифметика, временное, предсмертный взгляд, адрес результата),
# которые ревьюеру не нужны и место съедают. Раздел ищется ЗАГОЛОВКОМ в живом
# формате ящика, а не догадкой по номеру пункта.
_RE_TASK_DO = re.compile(r"(?im)^[^\S\n]{0,8}(?:#{1,6}\s*)?(?:\d+[.)]\s*)?ЧТО\s+СДЕЛАТЬ\b")
_RE_TASK_TAIL = re.compile(
    r"(?im)^[^\S\n]{0,8}(?:#{1,6}\s*)?(?:АРИФМЕТИКА|ВРЕМЕННОЕ|ПРЕДСМЕРТНЫЙ|ОСОБО\s+ПРО|"
    r"АДРЕС\s+РЕЗУЛЬТАТА|ПРИЗНАК\s+СДЕЛАННОСТИ)\b"
)
_TASK_TAIL_NOTE = " [ТЗ приложено ДО раздела «ЧТО СДЕЛАТЬ» включительно; служебный хвост (%d знаков) не приложен]"
_CLIP_MARK = " [ОБРЕЗАНО: показано %d знаков из %d]"

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


def clip_named(text, limit):
    """Обрезка, КОТОРАЯ СЕБЯ НАЗЫВАЕТ. → str длиной не больше ``limit``.

    Прежний хвост « …» не отличался от многоточия внутри самого текста: ревьюер
    не мог понять, кончилось ТЗ или его обрубили, — и советовал сделать то, что
    уже сделано в невидимой ему части. Теперь обрыв несёт ЧИСЛА: сколько показано
    из скольких. Влезает — не приписывается ничего.

    Потолок соблюдается СТРОГО (в отличие от прежнего «+2 знака»): ступень 1
    отказывает на `oversized_hypothesis`, и молчаливый перебор превратил бы
    обрезку в отказ сборки.
    """
    text = str(text or "")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ReviewAutoError("invalid_limit", "limit must be a positive int, got %r" % (limit,))
    if len(text) <= limit:
        return text
    room = max(1, limit - len(_CLIP_MARK % (0, len(text))) - 8)   # запас на рост числа знаков
    head = text[:room].rstrip()
    return head + (_CLIP_MARK % (len(head), len(text)))


def task_text_for_pack(text, *, limit=HYPOTHESIS_MAX):
    """ТЗ для пакета: ЦЕЛИКОМ или ДО «ЧТО СДЕЛАТЬ» включительно, но НЕ МОЛЧА. → str.

    Лестница ровно из трёх ступеней, и на каждой пакет говорит, что он сделал:

    1. влезает целиком — едет целиком, без единой пометки;
    2. не влезает — едет голова ДО раздела «ЧТО СДЕЛАТЬ» включительно, и хвост
       НАЗВАН числом знаков (``_TASK_TAIL_NOTE``);
    3. не влезает даже голова — обрезка называет себя числами (``clip_named``).

    Молчаливого исхода нет ни одного: «текст кончился» и «текст обрубили» для
    ревьюера обязаны выглядеть по-разному.
    """
    full = str(text or "")
    if len(full) <= limit:
        return full
    match_do = _RE_TASK_DO.search(full)
    if match_do is None:
        return full
    tail = _RE_TASK_TAIL.search(full, match_do.end())
    if tail is None:
        return full
    head = full[: tail.start()].rstrip()
    return head + (_TASK_TAIL_NOTE % (len(full) - len(head)))


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
    # СУДИТ ПОЛНАЯ СТРОКА, ДО РЕЗА (17.09.2026, задание 62-t): склейка переносов
    # способна родить форму, которой не видели правила снятия на сыром тексте
    # (телефон, разнесённый пробелами шире своего шаблона), и рез по потолку разрезал
    # бы её мимо стражи ниже. Нашла — строка снимается целиком, как и в цикле.
    early = review_send.outbound_violations(out)
    if early:
        out = _REDACT_LINE % ", ".join(sorted({v["kind"] for v in early}))
    out = clip_named(out, limit)

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
        "hypothesis": sanitize(task_text_for_pack(task_text)),
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
    return {"schema": SCHEMA, "triggers": {}, "digest": {"last_day": None, "last_reason": None},
            "spool": [], "channels": {}}


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
    channels = raw.get("channels")
    if isinstance(channels, dict):
        for name, rec in channels.items():
            if isinstance(name, str) and isinstance(rec, dict):
                st["channels"][name] = dict(rec)
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


# ───────────────────────── здоровье внешних каналов ─────────────────────────


def channel_failed(outcome, reason):
    """Заход провален ПО ВИНЕ КАНАЛА? → bool.

    `answered` — не провал. Всё остальное провал, КРОМЕ трёх наших собственных
    причин (`CHANNEL_OUR_FAULT`): в них наружу не ушло ничего, и канал ни при чём.
    """
    if str(outcome or "") == "answered":
        return False
    return str(reason or "") not in CHANNEL_OUR_FAULT


def note_channel(state, channel, outcome, reason, now_iso, *, probed=False):
    """Исход захода → здоровье канала. → новое состояние (вход не мутируется).

    Ответил — счётчик обнуляется ЦЕЛИКОМ, и лежачий встаёт тем же движением: это
    и есть самооживление, никакого участия владельца ветка не требует.
    """
    st = state_read(state)
    now = _require_now(now_iso)
    stamp = now.isoformat().replace("+00:00", "Z")
    rec = dict(st["channels"].get(channel) or {})
    rec["last_at"] = stamp
    rec["last_outcome"] = str(outcome or "")
    rec["last_reason"] = str(reason or "")
    if not channel_failed(outcome, reason):
        rec["strikes"] = 0
        rec["down_since"] = None
        rec["last_probe_at"] = None
    else:
        rec["strikes"] = int(rec.get("strikes") or 0) + 1
        if rec["strikes"] >= CHANNEL_DOWN_STRIKES and not rec.get("down_since"):
            rec["down_since"] = stamp
        if probed:
            # Проба стои́т ожидания, и её надо отсчитать ОТ ПРОБЫ, а не от начала
            # лежания: иначе следующий виток пробовал бы снова и снова.
            rec["last_probe_at"] = stamp
    st["channels"][channel] = rec
    return st


def channel_down(state, channel):
    """Канал признан лежачим? → bool."""
    rec = (state.get("channels") or {}).get(channel) or {}
    return bool(rec.get("down_since"))


def channel_plan(state, channels, now_iso):
    """Кому из каналов пакет ЕДЕТ в этот заход. → dict.

    ``{"send": [...], "probe": [...], "skip": [{"channel","why","down_since","next_probe_at"}]}``

    Лежачий канал пакетов не получает, но НЕ отключён: раз в ``CHANNEL_PROBE_AFTER_SEC``
    он попадает в ``probe`` и получает пакет снова. Ответил — здоровье возвращается
    само (`note_channel`), и следующий заход он снова в ``send``.
    """
    st = state_read(state)
    now = _require_now(now_iso)
    plan = {"send": [], "probe": [], "skip": []}
    for channel in list(channels or []):
        rec = st["channels"].get(channel) or {}
        down_since = parse_iso(rec.get("down_since"))
        if down_since is None:
            plan["send"].append(channel)
            continue
        last = parse_iso(rec.get("last_probe_at")) or down_since
        due = last + datetime.timedelta(seconds=CHANNEL_PROBE_AFTER_SEC)
        if now >= due:
            plan["probe"].append(channel)
            continue
        plan["skip"].append({
            "channel": channel,
            "why": "канал лежит с %s (подряд неответов %d, последняя причина %s)" % (
                rec.get("down_since"), int(rec.get("strikes") or 0), rec.get("last_reason") or "—"),
            "down_since": rec.get("down_since"),
            "next_probe_at": due.isoformat().replace("+00:00", "Z"),
        })
    return plan


def channel_plan_line(plan):
    """План каналов одной строкой для журнала. → str.

    Пропуск обязан быть СЛЫШЕН и обязан назвать, КОГДА канал попробуют снова:
    «пропущен» без времени возврата читается как «выключен навсегда».
    """
    parts = []
    if plan.get("send"):
        parts.append("шлём %s" % ", ".join(plan["send"]))
    if plan.get("probe"):
        parts.append("ПРОБА лежачего %s" % ", ".join(plan["probe"]))
    for skip in plan.get("skip") or []:
        parts.append("ПРОПУЩЕН %s (%s; следующая проба не раньше %s)"
                     % (skip["channel"], skip["why"], skip["next_probe_at"]))
    return "; ".join(parts) or "каналов нет"


def nearest_probe_at(plan):
    """Когда наступит БЛИЖАЙШАЯ проба среди пропущенных каналов. → ISO | None.

    Сравнение по времени, а не по строке: `isoformat` опускает микросекунды, когда
    они нулевые, и строки двух каналов лексикографически не сравнимы. ``None`` —
    пропущенных нет или ни одно время не разобрано; вызывающий называет это словом.
    """
    best, best_at = None, None
    for skip in plan["skip"]:                     # ключ ставит `channel_plan` всегда
        at = parse_iso(skip.get("next_probe_at"))
        if at is not None and (best_at is None or at < best_at):
            best, best_at = skip.get("next_probe_at"), at
    return best


# ───────────────────────────── повод, а не лента ─────────────────────────────


def chain_occasion(rec):
    """Закрытая цепочка — ПОВОД звать внешних? → (bool, причина словами).

    Признаки правила контура читаются из расписки ФАКТОМ, а не оценкой. Признака
    «меняется архитектура» в расписке нет ни одним полем — он назван вслух
    остатком, а не подменён похожим числом.

    ОБЪЯВЛЕННЫЙ, НО НЕ НАЙДЕННЫЙ В ДЕРЕВЕ КОММИТ ПОВОДОМ НЕ ЯВЛЯЕТСЯ (правка
    05.09.2026, вечер). С утра 05.09 он был ПЕРВОЙ веткой этой функции — и ровно
    она заклинила ступень A. Причина не в редком стечении, а в прямом
    противоречии двух мест: `case_for_chain` отказывает без ``operational_change``,
    а у ``commit_unverified`` оно ложно ПО ОПРЕДЕЛЕНИЮ (:func:`operational_change`
    возвращает False ровно в этом исходе). Повод звал, сборка отказывала — всегда,
    на каждой такой расписке.

    ЧИСЛА ЖИВОГО ЗАКЛИНИВАНИЯ (`pc_orchestrator.log`, сутки 05.09): **55 витков
    подряд** с 03:09:02 по 17:09:06, каждый — `not_operational: цепочка
    'pc-2026-09-02-13'`; наружу за сутки не ушло НИ ОДНОГО пакета при 11 открытых
    поводах в спуле, из которых 8 собрались бы без единой ошибки.

    ПОЧЕМУ ИМЕННО «НЕ ПОВОД», А НЕ «ПОЧИНИТЬ СБОРКУ». Внешнему критику у такой
    расписки разбирать нечего: живого доступа к дереву у него нет, кода за
    объявленным коммитом там тоже нет — остаётся текст отчёта о работе, которой в
    дереве не видно. Это новость ДЛЯ НАС (расхождение двух наших замеров), а не
    предмет второго мнения.

    И она НЕ ТЕРЯЕТСЯ: расхождение по-прежнему едет в суточный дайджест — его
    окно берёт спул целиком, а `digest_index_text` называет ``change_reason``
    каждой цепочки поимённо.
    """
    verified = list(rec.get("verified_commits") or [])
    claimed = list(rec.get("claimed_commits") or [])
    if str(rec.get("reported_status") or "") == "reported_failed" and rec.get("operational_change"):
        return True, "замеры разошлись: полоса объявила отказ, а коммит в дереве живой"
    if len(verified) >= EVENT_MIN_COMMITS:
        return True, "крупный класс: подтверждённых коммитов %d" % len(verified)
    # Отказ НАЗЫВАЕТСЯ своим словом, а не сваливается в «ленту»: у этой расписки
    # замеры как раз РАЗОШЛИСЬ, и общая фраза «замеры сошлись» была бы неправдой
    # ровно в том поле, ради честности которого расписка и заводится.
    if str(rec.get("change_reason") or "") == "commit_unverified":
        return False, ("не повод: объявлено коммитов %d, в дереве нет ни одного — "
                       "внешнему критику разбирать нечего, расхождение уходит в дайджест"
                       % len(claimed))
    return False, "лента: подтверждённых коммитов %d, замеры сошлись" % len(verified)


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

    ЧТО ЗДЕСЬ ПОВОД (правка 05.09.2026). Раньше сюда проходила ЛЮБАЯ цепочка с
    операционным изменением — то есть лента: 20 пакетов за 04.09 на 21 закрытую
    цепочку. Теперь проходит только цепочка, отвечающая правилу контура
    (:func:`chain_occasion`): крупный класс или расхождение двух наших замеров.
    Остальные НЕ ТЕРЯЮТСЯ — они по-прежнему целиком идут в суточный дайджест
    (:func:`digest_trigger` берёт окно спула, а не список поводов), то есть
    второе мнение по ним приходит раз в сутки пачкой, а не пакетом на каждую.
    """
    st = state_read(state)
    for rec in st["spool"]:
        is_occasion, occasion = chain_occasion(rec)
        if not is_occasion:
            continue
        key = "chain:%s" % rec["task_id"]
        ok, why = attempt_allowed(st, key, now_iso)
        if not ok:
            continue
        return {"kind": "chain", "key": key, "receipt": rec, "why": why, "occasion": occasion}
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


# ────────────── АРТЕФАКТ ЕДЕТ ГЛАВНЫМ, А НЕ ПЕРВЫМ (правка 05.09.2026) ──────────────
# ЧТО БЫЛО. `ARTIFACT_HEAD_LINES = 80` брал ПЕРВЫЕ 80 строк артефакта — фиксированный
# срез, ничего не знающий о содержании. Пересчёт лотка своими руками 05.09
# (76 пакетов `docs/review_outbox`): срез ровно на строке 80 у 56 пакетов, медианная
# доля переданного артефакта 0.362 (min 0.215), медианный НЕИСПОЛЬЗОВАННЫЙ резерв
# пакета 3517 знаков при потолке 15000 (max 8754). То есть ревьюер судил работу по
# трети её текста при наполовину пустом пакете — и советовал сделанное в невидимой
# ему части. Виноват был вход, а не ревьюер.
#
# ЧТО СТАЛО. Место распределяется ПО ВАЖНОСТИ РАЗДЕЛА, а не по порядку строк:
# сначала цель, критерии приёмки, результат, ограничения и спорное доказательство,
# остальное — по остатку. Не влезшее НАЗЫВАЕТСЯ (раздел, строки, сколько их).
#
# ПОТОЛОК ПАКЕТА НЕ ПОДНЯТ: он как был 15000 (`review_pack.REVIEW_MAX_CHARS`), так и
# остался. Растёт только СТРОЧНЫЙ бюджет артефакта, и он не назначен, а ИЗМЕРЕН
# сборкой: `review_auto_run._fit_chain` пробует бюджеты сверху вниз и берёт первый
# влезший — ровно тем же приёмом, которым дайджест подбирает число расписок.
ARTIFACT_HEAD_LINES = 80          # прежний фиксированный срез — остался запасной веткой
# Лестница идёт и ВНИЗ от прежних 80, и это не мелочь: артефакт, не влезавший
# восемьюдесятью строками, раньше вылетал ЦЕЛИКОМ (живой случай — повод
# pc-2026-09-04-196, `context_limit`, ревьюер не увидел ни строки при свободных
# 7761 знаках у прочих пакетов). Тридцать строк цели и результата — это не «мало»,
# это разница между «судит по главному» и «не видит ничего».
ARTIFACT_LINE_BUDGETS = (200, 150, 110, 80, 50, 30)
ARTIFACT_MIN_SECTION_LINES = 6    # обрубок раздела короче этого бесполезен — лучше назвать его пропуском

_RE_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")

# Вес раздела — порядок из задания: цель · критерии приёмки · результат ·
# ограничения · спорное доказательство. Слова взяты из ЖИВЫХ заголовков артефактов
# полосы, а не придуманы: «ЦЕЛЬ», «ЧТО СЧИТАЕМ СДЕЛАННЫМ», «ИТОГ», «FACT»,
# «ЗАПРЕТЫ», «КОНТРФАКТ», «ЗАМЕР». Заголовок без единого слова из списка получает
# вес 0 и едет по остатку — молча выброшенным он не бывает ни в одной ветке.
_SECTION_WEIGHTS = (
    (5, re.compile(r"(?i)\bцел[ьи]\b|\bповод\b|\bвопрос|\bзачем\b|\bзадани|\bпремис")),
    (4, re.compile(r"(?i)критери|приёмк|приемк|признак сделанн|что счита|как проверит|сделанност")),
    (3, re.compile(r"(?i)результат|\bитог|вывод|что сделан|\bfact\b|\bответ|что измен")),
    (2, re.compile(r"(?i)ограничен|запрет|границ|чего не|не сделан|остат|риск")),
    (1, re.compile(r"(?i)спорн|доказательств|контрфакт|замер|числ|мина|провер|опроверж")),
)
_PREAMBLE_WEIGHT = 6              # шапка до первого заголовка: имя, дата, FACT — всегда первой


def section_weight(title):
    """Вес заголовка по словам задания. → int (0 — «остальное»). Чистая функция."""
    for weight, rx in _SECTION_WEIGHTS:
        if rx.search(title or ""):
            return weight
    return 0


def _sections(lines):
    """Разбор текста на разделы по markdown-заголовкам. → [{start,end,title,weight}].

    Подраздел без своего ключевого слова НАСЛЕДУЕТ вес родителя: «### 3.1 Замер»
    внутри «## РЕЗУЛЬТАТ» — это по-прежнему результат, и терять его из-за того, что
    в его собственном заголовке нужного слова нет, значило бы резать по форме.
    """
    heads = []
    for i, line in enumerate(lines, 1):
        m = _RE_MD_HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    out = []
    if not heads or heads[0][0] > 1:
        end = (heads[0][0] - 1) if heads else len(lines)
        if end >= 1:
            out.append({"start": 1, "end": end, "title": "шапка", "weight": _PREAMBLE_WEIGHT})
    stack = []                     # [(уровень, вес)] — родители текущего заголовка
    for idx, (start, level, title) in enumerate(heads):
        end = (heads[idx + 1][0] - 1) if idx + 1 < len(heads) else len(lines)
        while stack and stack[-1][0] >= level:
            stack.pop()
        weight = section_weight(title) or (stack[-1][1] if stack else 0)
        stack.append((level, weight))
        out.append({"start": start, "end": end, "title": title, "weight": weight})
    return out


def _merge(ranges):
    out = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def plan_artifact_excerpt(text, *, max_lines):
    """Текст артефакта → ВЫБОРКА разделов под бюджет строк. Чистая функция.

    ``{"total_lines", "kept_lines", "ranges": [[s,e],…], "dropped": [{…}], "full": bool}``

    Влезает целиком — едет целиком, без выборки и без пометок о пропусках: пакет не
    смеет объявлять пропуск там, где его нет. Не влезает — берутся разделы по весу
    (цель → критерии → результат → ограничения → спорное → остальное), и КАЖДЫЙ
    невзятый кусок называется в ``dropped`` с заголовками и числом строк.
    """
    if not isinstance(max_lines, int) or isinstance(max_lines, bool) or max_lines < 1:
        raise ReviewAutoError("invalid_max_lines", "max_lines must be a positive int, got %r" % (max_lines,))
    lines = str(text or "").splitlines()
    total = max(1, len(lines))
    if len(lines) <= max_lines:
        return {"total_lines": total, "kept_lines": total, "ranges": [[1, total]], "dropped": [], "full": True}

    budget = max_lines
    taken = []
    for sec in sorted(_sections(lines), key=lambda s: (-s["weight"], s["start"])):
        if budget <= 0:
            break
        size = sec["end"] - sec["start"] + 1
        if size <= budget:
            taken.append([sec["start"], sec["end"]])
            budget -= size
        elif budget >= ARTIFACT_MIN_SECTION_LINES:
            # Раздел целиком не влез — берём его ГОЛОВУ, хвост назовём пропуском.
            taken.append([sec["start"], sec["start"] + budget - 1])
            budget = 0
    ranges = _merge(taken) or [[1, min(total, max_lines)]]

    dropped, cursor = [], 1
    titles = _sections(lines)
    for start, end in ranges + [[total + 1, total + 1]]:
        if start > cursor:
            names = [s["title"] for s in titles if s["start"] >= cursor and s["start"] <= start - 1]
            dropped.append({"start": cursor, "end": start - 1, "lines": start - cursor, "titles": names})
        cursor = max(cursor, end + 1)
    kept = sum(end - start + 1 for start, end in ranges)
    return {"total_lines": total, "kept_lines": kept, "ranges": ranges, "dropped": dropped, "full": False}


def gap_summary_line(path, plan):
    """Пропуски артефакта одной строкой сводки пакета. → str | None.

    Ревьюер обязан знать, ЧЕГО он не видит: «не вошло» без имён разделов читается
    как «там было то же самое», а именно это и произвело находки-пересказы.
    """
    if plan.get("full"):
        return None
    names = []
    for rec in plan.get("dropped") or ():
        for title in rec.get("titles") or ():
            if title not in names:
                names.append(title)
    shown = "; ".join("«%s»" % t for t in names[:4])
    if len(names) > 4:
        shown += " и ещё %d раздел(ов)" % (len(names) - 4)
    line = "НЕ ВОШЛО В ПАКЕТ из %s: %d строк(и) из %d (показано %d) — %s." % (
        path, plan["total_lines"] - plan["kept_lines"], plan["total_lines"], plan["kept_lines"],
        shown or "разделов без заголовков",
    )
    # Потолки берутся У СТУПЕНИ 1, а не дублируются числами здесь: разъехавшиеся
    # константы дали бы `invalid_summary` на сборке уже готового пакета.
    return line[: review_pack.SUMMARY_LINE_MAX]


def _source(path, *, role, required, line_counts, cap=None, plan=None, evidence_status="reported"):
    """Запись манифеста источника. ``line_counts`` — ФАКТ с диска, принесённый руками.

    Границы строк выдуманными быть не могут: сборщик контекста сверяет их с
    файлом и падает на выходе за конец (`line_range_out_of_bounds`). Поэтому
    длина приходит сюда числом, а не догадкой «ну пусть будет 400».
    """
    total = int((line_counts or {}).get(path) or 1)
    end = max(1, min(total, cap) if cap else total)
    rec = {
        "path": path,
        "role": role,
        "lane": "pc",
        "evidence_status": evidence_status,
        "required": required,
        "start_line": 1,
        "end_line": end,
    }
    if plan is not None:
        # ОКНО — весь файл, а выборка внутри него: так таблица источников
        # печатает настоящий размер артефакта, и «показано 150 из 301» видно без
        # похода в репозиторий, которого у ревьюера нет.
        rec["end_line"] = max(1, min(total, plan["total_lines"]))
        if not plan.get("full"):
            rec["line_ranges"] = [list(pair) for pair in plan["ranges"]]
    return rec


def _clip(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _commit_words(rec):
    if rec["verified_commits"]:
        return "коммит подтверждён в git: %s" % ", ".join(c[:12] for c in rec["verified_commits"])
    if rec["claimed_commits"]:
        return "коммит объявлен, но в дереве НЕ найден (%s)" % ", ".join(c[:12] for c in rec["claimed_commits"])
    return "коммита не объявлено — работа только на чтение"


def case_for_chain(rec, build_date, *, line_counts=None, artifact_sources=(), held_artifacts=(),
                   artifact_plans=None, oversized_artifacts=()):
    """Расписка закрытой цепочки → спецификация случая для :mod:`review_pack`.

    ``task_class`` = ``code_green`` не по умолчанию, а по определению повода:
    сюда доходят ТОЛЬКО цепочки с подтверждённым коммитом, то есть работа с
    зелёным кодом. Красных классов контур не собирает вовсе — их решает человек.
    """
    if not rec.get("operational_change"):
        raise ReviewAutoError("not_operational", "цепочка %r не меняла операционного состояния" % rec.get("task_id"))
    rel = receipt_rel(rec)
    plans = dict(artifact_plans or {})
    sources = [_source(rel, role="evidence", required=True, line_counts=line_counts)]
    gap_lines = []
    for path in artifact_sources:
        # Артефакт едет ГЛАВНЫМ, а не первым: план выборки принесли руки (у чистого
        # модуля диска нет), веса разделов посчитала `plan_artifact_excerpt`. Плана
        # нет — падаем на прежний срез головы, но МОЛЧА он больше не режет: пропуск
        # называется строкой сводки в обеих ветках.
        plan = plans.get(path)
        if plan is None:
            sources.append(_source(path, role="context", required=False,
                                   line_counts=line_counts, cap=ARTIFACT_HEAD_LINES))
            total = int((line_counts or {}).get(path) or 1)
            if total > ARTIFACT_HEAD_LINES:
                gap_lines.append(gap_summary_line(path, {
                    "total_lines": total, "kept_lines": ARTIFACT_HEAD_LINES, "full": False,
                    "dropped": [{"start": ARTIFACT_HEAD_LINES + 1, "end": total,
                                 "lines": total - ARTIFACT_HEAD_LINES, "titles": []}],
                }))
            continue
        sources.append(_source(path, role="context", required=False, line_counts=line_counts, plan=plan))
        line = gap_summary_line(path, plan)
        if line:
            gap_lines.append(line)
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
    # Артефакт, не влезший даже нижним бюджетом, ОБЯЗАН быть назван здесь. Убрав его
    # из манифеста молча, пакет перестал бы даже упоминать о нём: раздел «опущено»
    # говорит только о том, что в манифесте БЫЛО, — и молчание читалось бы как
    # «артефакта у работы нет», а это другая новость.
    for big in oversized_artifacts or ():
        summary.append(
            "НЕ ПРИЛОЖЕН по потолку пакета: %s (%s строк) — не влезает даже минимальным бюджетом "
            "выборки; файл остаётся в дереве." % (big.get("path"), big.get("lines"))
        )
    summary.extend(gap_lines[: max(0, review_pack.SUMMARY_LINES_MAX - len(summary))])
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
