# -*- coding: utf-8 -*-
"""contour_digest_run.py — РУКИ сводки состояния контура полосы ПК.

Читает уже измеренное другими, ничего не считает заново и НИЧЕГО не меняет.
Решение — в :mod:`contour_digest` (чистая логика), адрес и дверь — те же, что у
ступени D (`review_audit_run.audit_topic` / `send_audit`): тема сводок берётся из
живой настройки демона, каскада фолбэков у двери нет.

ИСТОЧНИКИ И ПОЧЕМУ ИМЕННО ОНИ. Все четыре — ФАЙЛЫ, которые пишет не эта ветка:

    queue_snapshot_pc.state.json   слепок очереди: демон пишет каждым витком
    tmp/expect_pc/state.json       слой ожиданий: наблюдатель тикает раз в 10 мин
    review_outbox_queue_state.json реестр исходящих ступени F
    tmp/done_judge_pc/judged.json  вердикты судьи закрытия (ступень C) — чем цепочка
                                   ДОКАЗАНА; без него счёт серии зачитывал бы любое
                                   `done`, в том числе закрытое без адреса
    git log                        движение осей за окно

Очередь через мост здесь НЕ ЧИТАЕТСЯ намеренно, и это не экономия: `get_pending`
исполняется СИНХРОННО внутри витка демона, а замер полосы говорит 26.8 с на один
`done` (`queue_snapshot_pc.py` §шапка). Шесть таких заходов в сутки съели бы
порог тишины О2 ради чисел, которые демон уже положил на диск сам.

ВРЕМЯ ЗАМЕРА — mtime ФАЙЛА, а не поле внутри него. Поле пишет тот, кого мы
проверяем; mtime ставит система. Ровно та же доктрина, по которой ступень C не
верит отчёту исполнителя, а меряет продукт по адресу.

ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЖИВЁТ В КОДЕ, А НЕ В ТЕСТЕ: чтение каждого источника обёрнуто
в свой `try`, и провал даёт СТРОКУ «неизвестно» с названной причиной
(:func:`contour_digest.dead_source`), а не отсутствие строки.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import subprocess
import sys

import contour_digest as cd
import done_judge_pc
import review_audit
import review_audit_run
import review_intake_run
import shtab_box               # ЗА СМЕЩЕНИЕМ ПОЛОСЫ: второй экземпляр числа разошёлся бы молча

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE = "contour_digest_state.json"
JOURNAL_WRITER = "cowork_log_append.py"

# Оси движения — ПО ПУТЯМ ФАЙЛОВ коммита, а не по словам его сообщения. Слова
# пишет человек, пути пишет правка; рамка требует, чтобы заход называл свою ось,
# и здесь мы её ПРОВЕРЯЕМ, а не переспрашиваем.
AXIS3_PATHS = ("pc_orchestrator.py", "review_*.py", "recon_*.py", "expectations_pc*.py",
               "done_judge_pc.py", "content_product_verifier.py", "queue_snapshot_pc.py",
               "series_pc.py", "contour_digest*.py", "pretool_guard.py")
BUSINESS_PATHS = ("suggest.py", "pricing*.py", "price_*.py", "delivery*.py", "booking*.py",
                  "client_*.py", "moderation*.py", "bot.py", "trainer*.py", "fixtures/*")


# ───────────────────────────── время и диск ─────────────────────────────

def now_ts(clock=None):
    """Часы одной дверью: тест подменяет их числом, боевой путь берёт системные."""
    if clock is not None:
        return float(clock() if callable(clock) else clock)
    import time

    return time.time()


def stamp_words(ts):
    """Метка UTC словами. `None` — так и сказано, нулём не подменяется."""
    if ts is None:
        return "?"
    try:
        got = datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc)
    except (TypeError, ValueError, OSError):
        return "?"
    return got.strftime("%Y-%m-%d %H:%M UTC")


def _path(root, rel):
    return rel if os.path.isabs(rel) else os.path.join(root, rel)


def quote(text, limit=cd.GOAL_MAX):
    """ЧУЖОЙ текст в сообщение — только через стражу полосы. → str.

    Цель строки очереди, причина падения, подпись детей и заголовок коммита — всё
    это писали не мы. Задержанная стражей строка ЗАМЕНЯЕТСЯ пометкой и сообщение
    не глушит (:func:`review_audit.safe_line`): второй слой
    (:func:`review_audit.outbound_safe`) у двери остановил бы сводку ЦЕЛИКОМ, а
    молчание здесь дороже одной непоказанной цитаты.
    """
    fixed, _kind = review_audit.safe_line(text, limit, cd.one_line)     # суди полный, режь показ
    return fixed


def _cause(exc):
    """Причина отказа БЕЗ пути. → str.

    Замерено при сборке 02.09 и стоило бы всего канала: голый текст исключения
    несёт АБСОЛЮТНЫЙ путь (`[WinError 2] … 'C:\\\\Users\\\\…'`), страж исходящего
    ловит его видом «абсолютный путь Windows» и задерживает СООБЩЕНИЕ ЦЕЛИКОМ.
    То есть сводка молчала бы ровно в том случае, ради которого написана строка
    «неизвестно», — когда источник умер. Адрес источника в строке и так назван
    отдельным полем; путь во второй раз не нужен, а стоит канала.
    """
    name = exc.__class__.__name__
    words = getattr(exc, "strerror", None) or str(getattr(exc, "args", ("",))[0] if
                                                  getattr(exc, "args", None) else "")
    words = " ".join(str(words or "").split())
    if not words or ":" in words or "\\" in words or "/" in words:
        words = ""
    return "%s%s" % (name, (": %s" % words) if words else "")


def read_json(root, rel):
    """Файл-источник → (данные, время замера, причина отказа).

    Время замера — mtime, снятый ДО чтения: файл, переписанный между stat и
    чтением, отдаст числа новее своей метки, и это честнее наоборот.
    """
    path = _path(root, rel)
    try:
        read_at = os.path.getmtime(path)
    except OSError as exc:
        return None, None, "файла нет или он не читается: %s" % _cause(exc)
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:                       # noqa: BLE001 — любой отказ = «неизвестно»
        return None, read_at, "файл не разобран: %s" % _cause(exc)
    if not isinstance(data, dict):
        return None, read_at, "в файле не словарь состояния"
    return data, read_at, ""


def parse_iso(value):
    """ISO-метка → epoch. Не разобралась — `None`, и это третий исход."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        got = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=datetime.timezone.utc)
    return got.timestamp()


# ───────────────────────────── состояние сводки ─────────────────────────────

def state_path(root=HERE, named=None):
    return named or _path(root, DEFAULT_STATE)


def read_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except Exception:                              # noqa: BLE001
        return {}


def write_state(state, path):
    """Метка оборота — ЕДИНСТВЕННОЕ, что эта ветка пишет. → (ok, причина)."""
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        return True, ""
    except Exception as exc:                       # noqa: BLE001
        return False, "метка оборота не записана: %s" % exc


# ───────────────────────────── чтение источников ─────────────────────────────

def closed_since(snapshot, since):
    """Закрытые строки очереди за окно. → list[dict], отсортированы по времени."""
    rows = []
    for tid, item in ((snapshot.get("closed") or {}) if snapshot else {}).items():
        if not isinstance(item, dict):
            continue
        at = item.get("at")
        try:
            at = float(at)
        except (TypeError, ValueError):
            at = None
        if since is not None and (at is None or at < float(since)):
            continue
        rows.append({"id": item.get("id") or tid, "at": at, "outcome": item.get("outcome"),
                     "goal": item.get("goal") or "", "why": item.get("why") or "",
                     # ВИНОВНИК НЕИЗВЕСТНОСТИ ЕДЕТ С РЯДА, а не вычисляется здесь: считающий слой
                     # чист и различитель спросить не может. Нет поля → `None`, и счёт отнесёт
                     # ряд к судье, то есть к прежнему поведению.
                     "by": item.get("by")})
    return sorted(rows, key=lambda r: (r.get("at") is None, r.get("at") or 0))


def all_closed(snapshot):
    """ВЕСЬ реестр закрытых — окно серии шире окна сводки и живёт своим счётом."""
    return closed_since(snapshot, None)


def all_rows(snapshot):
    """ОБЕ половины слепка очереди — открытые и закрытые. → list[dict] | None.

    Заведено для счёта ящика Штаба, и корпус здесь ШИРЕ, чем у серии, по той же
    причине, по которой ступень E считает свой потолок и по закрытым рядам: маркер
    взятого задания уходит из открытых вместе с закрывшейся строкой, и счёт по
    одним открытым мерил бы «сколько сейчас в работе», а спрошено «сколько взято
    за сутки». Обе цифры совпадают ровно до первого закрытия.

    ``None`` — слепка нет: отличать «не прочитали» от «пусто» обязан вызывающий,
    и пустой список этой разницы не несёт.
    """
    if snapshot is None:
        return None
    rows = []
    for tid, item in ((snapshot.get("open") or {}) if snapshot else {}).items():
        if isinstance(item, dict):
            rows.append({"id": item.get("id") or tid, "goal": item.get("goal") or ""})
    return rows + all_closed(snapshot)


def git_moves(root, since_ts, paths, runner=None):
    """Коммиты за окно, задевшие названные пути. → (list[str], причина).

    Отдельным вызовом на каждую ось: pathspec отвечает на вопрос «что тронуто»
    точнее, чем разбор `--name-only` в одном потоке, и не путает два ответа.
    """
    run = runner or subprocess.run
    # Пояс НАЗВАН явно. Наивная метка читается git'ом как МЕСТНОЕ время, а полоса
    # живёт на UTC+7 — окно молча уехало бы на семь часов, и «за четыре часа»
    # показывало бы одиннадцать (замер 02.09 при сборке).
    when = datetime.datetime.fromtimestamp(float(since_ts), datetime.timezone.utc).isoformat()
    argv = ["git", "log", "--since=%s" % when, "--pretty=format:%h %s", "--"] + list(paths)
    try:
        done = run(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except Exception as exc:                       # noqa: BLE001
        return None, "git не ответил: %s" % _cause(exc)
    if done.returncode != 0:
        return None, "git вернул %d" % done.returncode
    out = done.stdout.decode("utf-8", "replace").strip()
    return [ln for ln in out.splitlines() if ln.strip()], ""


# ───────────────────────────── сборка разделов ─────────────────────────────

def _closed_words(rows):
    named = []
    for row in rows[-cd.LIST_MAX:]:
        word = {"done": "сдана", "failed": "упало", "rejected": "отклонено владельцем",
                cd.OUT_UNKNOWN: "НЕИЗВЕСТНО (судья не прочитал продукт)"}.get(
            row.get("outcome"), "исход не сверен")
        named.append("#%s %s" % (row.get("id"), word))
    # Остаток НАЗЫВАЕТСЯ числом: список, обрезанный молча, читается как полный.
    if len(rows) > cd.LIST_MAX:
        named.append("…и ещё %d раньше" % (len(rows) - cd.LIST_MAX))
    return " · ".join(named)


def section_closed(snapshot, read_at, why, since, now):
    """«Что закрылось с прошлой сводки». Источник мёртв → одна строка «неизвестно»."""
    if snapshot is None:
        return [cd.dead_source("closed", "queue", why)], None
    rows = closed_since(snapshot, since)
    if not rows:
        return [cd.reading("closed", "закрытых строк за окно нет", src="queue",
                           read_at=read_at, now=now, kind=cd.OK)], 0
    return [cd.reading("closed", "закрылось %d: %s" % (len(rows), _closed_words(rows)),
                       src="queue", read_at=read_at, now=now, kind=cd.OK)], len(rows)


def section_red(snapshot, q_at, q_why, expect, e_at, e_why, outbox, o_at, o_why, since, now):
    """«Красное или неизвестное» — три источника, и смерть каждого видна строкой."""
    out = []
    if snapshot is None:
        out.append(cd.dead_source("red", "queue", q_why))
    else:
        rows = closed_since(snapshot, since)
        bad = [r for r in rows if r.get("outcome") == "failed"]
        # РАЗБОР «НЕ СВЕРЕНО» ОДИН НА ПОЛОСУ (:func:`contour_digest.is_unsure`): с 19.09
        # на том же признаке стои́т льгота счёта серии, и второй экземпляр вопроса
        # разошёлся бы с первым МОЛЧА — читатель увидел бы строку про несверенный ряд
        # там, где счёт его льготой не считает, или наоборот.
        unsure = [r for r in rows if cd.is_unsure(r)]
        # НЕИЗВЕСТНЫЕ ЗАКРЫТИЯ — СВОЕЙ СТРОКОЙ И ТАК ЖЕ ГРОМКО, как упавшие (решение Штаба
        # 11.09.2026, п. 3). До этого они лежали среди «упало», и владелец читал провал работы
        # там, где полоса работу сделала, а прибор не смог её прочитать (живой случай 245).
        unread = [r for r in rows if cd.is_unknown(r)]
        if bad:
            out.append(cd.reading("red", "упало строк %d: %s" % (
                len(bad), " · ".join("#%s %s" % (r["id"], quote(r.get("why"), 50))
                                     for r in bad[:cd.LIST_MAX])),
                src="queue", read_at=q_at, now=now, kind=cd.RED))
        if unread:
            out.append(cd.reading("red", "НЕИЗВЕСТНО — судья не прочитал продукт у %d строк: %s "
                                         "(это про СУДЬЮ, а не про работу)" % (
                len(unread), " · ".join("#%s %s" % (r["id"], quote(r.get("why"), 50))
                                        for r in unread[:cd.LIST_MAX])),
                src="queue", read_at=q_at, now=now, kind=cd.RED))
        if unsure:
            out.append(cd.reading("red", "исход не сверен у %d строк: %s" % (
                len(unsure), " · ".join("#%s" % r["id"] for r in unsure[:cd.LIST_MAX])),
                src="queue", read_at=q_at, now=now, kind=cd.UNKNOWN,
                why="слепок ушедшую строку видел, а исхода её не читал"))
    if expect is None:
        out.append(cd.dead_source("red", "expect", e_why))
    else:
        eps = expect.get("open") or {}
        if isinstance(eps, dict) and eps:
            out.append(cd.reading("red", "открытых ожиданий %d: %s" % (
                len(eps), " · ".join(sorted(eps)[:cd.LIST_MAX])),
                src="expect", read_at=e_at, now=now, kind=cd.RED))
        else:
            out.append(cd.reading("red", "открытых ожиданий нет (О1–О4)", src="expect",
                                  read_at=e_at, now=now, kind=cd.OK))
        kids = expect.get("kids") if isinstance(expect.get("kids"), dict) else {}
        sig = kids.get("sig")
        if sig:
            out.append(cd.reading("red", "дети контура: %s" % quote(sig, 110),
                                  src="expect", read_at=e_at, now=now, kind=cd.OK))
        else:
            out.append(cd.reading("red", "вердикта о детях в слое нет", src="expect",
                                  read_at=e_at, now=now, kind=cd.UNKNOWN,
                                  why="ключ kids пуст — судить нечем"))
    if outbox is None:
        out.append(cd.dead_source("red", "outbox", o_why))
    else:
        packs = outbox.get("packs") or {}
        fresh = []
        for key, rec in (packs.items() if isinstance(packs, dict) else []):
            if not isinstance(rec, dict) or rec.get("state") != "exhausted":
                continue
            at = parse_iso(rec.get("last_at"))
            if since is None or (at is not None and at >= float(since)):
                fresh.append(key)
        left = sum(1 for r in (packs.values() if isinstance(packs, dict) else [])
                   if isinstance(r, dict) and r.get("state") == "queued")
        if fresh:
            out.append(cd.reading("red", "канал не довёз за окно %d пакетов: %s" % (
                len(fresh), " · ".join(quote(k, 46) for k in sorted(fresh)[:cd.LIST_MAX])),
                src="outbox", read_at=o_at, now=now, kind=cd.RED))
        else:
            out.append(cd.reading("red", "новых исчерпаний исходящих за окно нет (в очереди %d)"
                                  % left, src="outbox", read_at=o_at, now=now, kind=cd.OK))
    return out


def section_await(snapshot, read_at, why, now):
    """«Что ждёт решения владельца» — открытые строки в `needs_approval`."""
    if snapshot is None:
        return [cd.dead_source("await", "queue", why)]
    rows = [(tid, item) for tid, item in (snapshot.get("open") or {}).items()
            if isinstance(item, dict) and item.get("status") == "needs_approval"]
    if not rows:
        return [cd.reading("await", "решения владельца не ждёт ничего", src="queue",
                           read_at=read_at, now=now, kind=cd.OK)]
    named = " · ".join("#%s %s" % (tid, quote(item.get("goal"), 46))
                       for tid, item in sorted(rows)[:cd.LIST_MAX])
    return [cd.reading("await", "ждут решения %d: %s" % (len(rows), named), src="queue",
                       read_at=read_at, now=now, kind=cd.WAIT)]


def day_utc(ts):
    """Календарный день UTC для окна внешних ответов. → 'YYYY-MM-DD'.

    Именно UTC, а не местное: ``отправлено:`` в файле ответа пишет отправщик по
    UTC, и местный день сдвинул бы разрез на семь часов — та же мина, на которой
    ``git log --since=`` уже показывал 11 коммитов вместо 3.
    """
    try:
        return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def day_lane(ts):
    """Календарный день ПОЛОСЫ для счёта ящика. → 'YYYY-MM-DD'.

    ЗАВЕДЕНА 04.09.2026 ВМЕСТЕ С ПЕРЕЕЗДОМ ЯЩИКА НА МЕСТНЫЕ СУТКИ, и не для
    красоты: сводка показывает «взято N при потолке M», а замок потолка живёт в
    ящике. Останься сводка на UTC — с 00:00 до 07:00 местного она считала бы
    ВЧЕРАШНИЕ сутки и показывала бы владельцу одно число, пока полоса держит
    другое. Это ровно тот молчаливый разъезд, против которого счёт ящика вообще
    берётся у ящика (:func:`contour_digest.shtab_taken`).

    Смещение НЕ набирается здесь вторым экземпляром — оно берётся у
    :data:`shtab_box.LANE_TZ`. Соседний :func:`day_utc` остаётся UTC и не тронут:
    у окна внешних ответов разрез свой и по своей причине.
    """
    try:
        return datetime.datetime.fromtimestamp(float(ts), shtab_box.LANE_TZ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def read_inbox(root, inbox=None):
    """Лоток → (шапки заходов, записи находок, причина отказа).

    Оба разбора — ЧУЖИМ кодом: шапки читает ступень D
    (:func:`review_audit_run.answer_headers`), находки — ступень B
    (:func:`review_intake_run.read_records`). Свой третий разборщик означал бы,
    что сводка и суточный дайджест могут разойтись в том, что вообще считается
    ответом канала.

    Мост здесь по-прежнему НЕ ЗОВЁТСЯ, и заявки не строятся: премиса находок
    требует ``git ls-files`` и проб по дереву, а сводке нужны только вид находки
    и счёт. Читаются ФАЙЛЫ ЛОТКА и ничего больше.
    """
    inbox = inbox or review_intake_run.DEFAULT_INBOX
    # ПУСТОЙ ЛОТОК И ОТСУТСТВУЮЩИЙ — РАЗНЫЕ НОВОСТИ, и различить их обязаны мы:
    # обход каталога, которого нет, отдаёт пустой список молча, и строка сказала
    # бы «заходов не было» там, где правда «спросить не у кого». Третий исход
    # ставится ЗДЕСЬ, потому что ниже его уже нечем отличить.
    if not os.path.isdir(_path(root, inbox)):
        return None, None, "лоток не найден по адресу %s" % inbox
    try:
        headers, _hskip = review_audit_run.answer_headers(root=root, inbox=inbox)
        records, _rskip = review_intake_run.read_records(root, inbox)
    except Exception as exc:                       # noqa: BLE001 — источник не роняет сводку
        return None, None, _cause(exc)
    return headers, records, ""


def fresh_or_none(data, read_at, now, src):
    """Слепок годен для СЧЁТА? Протух или возраст не сверить → `None`. → данные | None.

    Отдельная дверь, а не ветка на месте: :func:`contour_digest.reading` пометит
    строку «неизвестно» ПО ВОЗРАСТУ, но слова строки соберутся раньше — и числа,
    посчитанные по протухшему слепку, поехали бы наружу под шапкой «неизвестно»,
    то есть выглядели бы фактом. Тот же приём, каким :func:`build` гасит числа
    серии и счёт ящика.
    """
    if data is None:
        return None
    old = cd.stale(read_at, now, cd.limit_of(src))
    return None if (old is None or old) else data


def fresh_why(data, read_at, now, src, why=""):
    """ПОЧЕМУ :func:`fresh_or_none` сказала «не годен» — словами. Годен → `""`. → str.

    ТРИ ИСХОДА, И НИ ОДИН НЕ ЗОВЁТСЯ ЧУЖИМ ИМЕНЕМ (правка 11.09.2026, хвост задания
    31-a). До неё показ печатал на все один текст — «слепок очереди не прочитан», — и
    при живом файле на диске это была неправда: слепок ПРОЧИТАН и просто стар. Цена
    не косметическая, потому что чинят по этим словам РАЗНОЕ:

      • «не прочитан» — файла нет, не разобран, не тот формат: чинят диск и писателя;
      • «прочитан, но старше предела» — файл на месте, а обновлять его перестали:
        чинят ВСТАВШИЙ ДЕМОН, и это совсем другая рука;
      • «возраст не сверить» — третий исход :func:`contour_digest.stale`, ведущий в
        «неизвестно», а не в «свежо»: отсутствие метки доказывает только её отсутствие.

    ВЕЛИЧИНУ ЭТА ДВЕРЬ НЕ ТРОГАЕТ. Гашение числа стои́т в :func:`fresh_or_none` и
    поставлено коммитом f5dc9ab; здесь только СЛОВА, и порядок веток тот же, чтобы
    два показа одной величины не разошлись молча.

    ``why`` — причина, названная РУКАМИ у места отказа (:func:`read_json`): она ближе
    к нему, чем дежурная, и потому сильнее.
    """
    if data is None:
        return "слепок %s не прочитан%s" % (_src_words(src),
                                            (": %s" % why) if str(why or "").strip() else "")
    limit = cd.limit_of(src)
    old = cd.stale(read_at, now, limit)
    if old is None:
        return "слепок %s прочитан, но возраст не сверить" % _src_words(src)
    if old:
        return ("слепок %s прочитан, но старше предела %d с"
                % (_src_words(src), int(limit)))
    return ""


def _src_words(src):
    """Имя источника словами витрины. Незнакомое зовётся собой, а не «источником»."""
    return {"queue": "очереди", "expect": "ожиданий"}.get(str(src or ""), str(src or ""))


def _oldest(*stamps):
    """Возраст пары источников — по БОЛЕЕ СТАРОМУ. Нет метки хоть у одного → `None`."""
    got = list(stamps)
    return None if any(s is None for s in got) else min(got)


def section_external(headers, records, why, day, now, intake=None, i_at=None,
                     recon=None, r_at=None, queue=None, q_at=None):
    """«Внешние ответы за сутки»: счёт, СУДЬБА находок и ПОЛЬЗА от них.

    Прирост 04.09.2026 — четыре строки, и ни одной больше: сводку читают с
    телефона, а стена хуже отсутствия. Каждая строка несёт СВОЙ источник и СВОЙ
    возраст, потому что реестры стареют по-разному и общего возраста у них нет.

    ЧЕТЫРЕ СТРОКИ СЧИТАЮТСЯ ЗНАКАМИ, А НЕ ПУНКТАМИ (поправка того же дня): четыре
    пункта кода разворачивались на телефоне в 26 физических строк, и потолок
    переехал на меру читателя — :data:`contour_digest.FATE_CHARS`, взятую из
    медианы собственных пунктов этой же сводки.

    Лоток мёртв → строка одна и говорит «неизвестно»: без записей находок судьбу
    считать не от чего, и печатать при этом четыре строки нулей значило бы
    утверждать, что находок не было.
    """
    if headers is None or records is None:
        return [cd.dead_source("external", "inbox", why)]
    stats = cd.external_stats(headers, records, day)
    fate = cd.external_fate(records, day,
                            intake=fresh_or_none(intake, i_at, now, "intake"),
                            recon=fresh_or_none(recon, r_at, now, "recon"),
                            queue=fresh_or_none(queue, q_at, now, "queue"))
    return [
        cd.reading("external", cd.external_words(stats), src="inbox",
                   read_at=now, now=now, kind=cd.external_verdict(stats)),
        cd.reading("external", cd.fate_words_intake(fate), src="intake",
                   read_at=i_at, now=now, kind=cd.OK),
        cd.reading("external", cd.fate_words_recon(fate), src="recon",
                   read_at=r_at, now=now, kind=cd.OK),
        cd.reading("external", cd.fate_words_queue(fate), src="queue",
                   read_at=q_at, now=now, kind=cd.OK),
        cd.reading("external", cd.benefit_words(fate), src="benefit",
                   read_at=_oldest(r_at, q_at), now=now, kind=cd.OK),
    ]


def section_axis(root, since, now, runner=None):
    """«Двигался ли этап 3 и двигался ли бизнес» — по путям коммитов за окно."""
    out = []
    for topic, paths, name in (("ось этап 3", AXIS3_PATHS, "этап 3"),
                               ("бизнес", BUSINESS_PATHS, "бизнес")):
        rows, why = git_moves(root, since, paths, runner=runner)
        if rows is None:
            out.append(cd.dead_source("axis", "git", "%s: %s" % (name, why)))
            continue
        if rows:
            words = "%s ДВИГАЛСЯ: коммитов %d — %s" % (
                name, len(rows), " · ".join(quote(r, 46) for r in rows[:3]))
        else:
            words = "%s за окно не двигался" % name
        out.append(cd.reading("axis", words, src="git", read_at=now, now=now, kind=cd.OK))
    return out


def read_judged(root=HERE):
    """Реестр вердиктов судьи закрытия → dict. Отказ чтения → `{}`.

    ЧИТАЕМ ЧУЖИМ ЖЕ КОДОМ (:func:`done_judge_pc.read_ledger`), а не своим разбором
    JSON: путь и форма записи принадлежат судье, и второй их экземпляр разошёлся бы
    молча — тот же довод, по которому счёт ящика Штаба ведёт сам ящик.

    Пустой ответ ВТОРОЙ СТРОКОЙ НЕ ОБЪЯСНЯЕТСЯ и «неизвестным» источником не
    становится СОЗНАТЕЛЬНО: у отсутствия доказательства и у отсутствия реестра
    последствие ОДНО — цепочка не доказана. Заводить сюда третий исход значило бы
    дать пустому реестру право сохранять серию, а это ровно тот класс, который
    поправка 02.09 закрывает.
    """
    try:
        return done_judge_pc.read_ledger(root)
    except Exception:                              # noqa: BLE001 — судья молчит = не доказано
        return {}


def section_series(snapshot, read_at, why, now, judged=None):
    """Серия цепочек: паспорт источника отдельно от чисел — числа считает чистая логика.

    ТРЕВОГА ПРО СУДЬЮ ЛОМАЕТ СПОКОЙСТВИЕ СВОДКИ, а не остаётся числом в хвосте строки
    (решение Штаба 11.09.2026, п. 3). Спокойная сводка схлопывается в одну фразу и
    разделов не печатает вовсе — то есть тревога, оставленная строкой серии, при доле
    неизвестного выше пятой части читалась бы как «всё тихо». Красный вид `RED` тут
    выбран не для громкости ради громкости: `is_calm` смотрит именно на вид.
    """
    if snapshot is None:
        return [cd.dead_source("series", "queue", why)], None
    rows = all_closed(snapshot)
    counted = cd.series(rows, judged=judged)
    kind = cd.RED if counted.get("alarm") else cd.OK
    return ([cd.reading("series", "", src="queue", read_at=read_at, now=now, kind=kind)],
            counted)


# ───────────────────────────── оборот ─────────────────────────────

def build(root=HERE, now=None, since=None, interval=cd.INTERVAL_SEC, runner=None, inbox=None,
          day=None):
    """Все источники → отчёт. Ни одной ветки записи, кроме метки оборота у tick."""
    now = now_ts(now)
    if since is None:
        since = now - float(interval)
    snapshot, q_at, q_why = read_json(root, cd.source("queue")["addr"])
    expect, e_at, e_why = read_json(root, cd.source("expect")["addr"])
    outbox, o_at, o_why = read_json(root, cd.source("outbox")["addr"])
    # РЕЕСТРЫ СУДЬБЫ — ступени B и E. Читаются здесь, а не в разделе: причина
    # отказа у них та же, что у остальных источников, и ветка «неизвестно» обязана
    # быть ОДНА на все файлы, а не своя у каждого раздела.
    intake, in_at, _in_why = read_json(root, cd.source("intake")["addr"])
    recon, rc_at, _rc_why = read_json(root, cd.source("recon")["addr"])
    heads, records, i_why = read_inbox(root, inbox)
    closed_rows, closed_count = section_closed(snapshot, q_at, q_why, since, now)
    series_rows, counted = section_series(snapshot, q_at, q_why, now, judged=read_judged(root))
    # Слепок старше предела → чистая логика уже сказала «неизвестно». Числа серии в
    # таком случае наружу НЕ ЕДУТ: сосчитанное по протухшему слепку выглядит фактом.
    if series_rows and series_rows[0].get("kind") in (cd.UNKNOWN, cd.HYPO):
        counted = None
    # СЧЁТ ЯЩИКА — ЗА КАЛЕНДАРНЫЕ СУТКИ, а не за окно сводки, и это не описка.
    # Потолок ящика назван В СУТКАХ и считается по маркеру с датой; покажи сводка
    # число за своё четырёхчасовое окно — владелец сверял бы с потолком две разные
    # величины и всякий раз получал бы «недобор». СУТКИ ЗДЕСЬ МЕСТНЫЕ С 04.09.2026:
    # ящик переехал на них (`shtab_box.lane_day`), и разрез сводки обязан поехать
    # вместе с ним — иначе ночью сводка показывала бы вчерашний день. Окно внешних
    # ответов при этом остаётся на UTC (`day_utc`): у него разрез свой.
    the_day = day or day_lane(now)
    rows = all_rows(snapshot)
    taken = cd.shtab_taken(rows, the_day)
    # Протухший слепок → числа наружу НЕ ЕДУТ, ровно как у серии: сосчитанное по
    # старому слепку выглядит фактом и им не является.
    if series_rows and series_rows[0].get("kind") in (cd.UNKNOWN, cd.HYPO):
        taken = None
    return {
        "schema": cd.SCHEMA,
        "interval": float(interval),
        "since": since,
        "since_words": stamp_words(since),
        "now": now,
        "now_words": stamp_words(now),
        "day": the_day,
        "shtab_taken": taken,
        "closed_count": closed_count,
        "series": counted,
        "readings": {
            "closed": closed_rows,
            "red": section_red(snapshot, q_at, q_why, expect, e_at, e_why,
                               outbox, o_at, o_why, since, now),
            "await": section_await(snapshot, q_at, q_why, now),
            "external": section_external(heads, records, i_why, day or day_utc(now), now,
                                         intake=intake, i_at=in_at, recon=recon, r_at=rc_at,
                                         queue=snapshot, q_at=q_at),
            "axis": section_axis(root, since, now, runner=runner),
            "series": series_rows,
        },
        "sent": False,
        "send_why": "",
    }


def tick(root=HERE, state=None, now=None, interval=cd.INTERVAL_SEC, send=False,
         write_journal=False, daemon=None, sender=None, journal_fn=None, runner=None,
         force=False):
    """Один оборот сводки. → отчёт.

    Порядок намеренный: собрать → отправить → и только при УДАЧНОЙ отправке
    сдвинуть метку окна. Сорванная отправка оставляет окно прежним, поэтому
    следующая сводка расскажет о том же периоде, а не проглотит его молча.
    """
    now = now_ts(now)
    spath = state_path(root, state)
    st = read_state(spath)
    prev = st.get("last")
    try:
        prev = float(prev) if prev is not None else None
    except (TypeError, ValueError):
        prev = None
    since = prev if prev is not None else (now - float(interval))
    if not force and prev is not None and (now - prev) < float(interval):
        report = {"skipped": "интервал не вышел: с прошлой сводки %d мин из %d"
                             % (int((now - prev) / 60), int(float(interval) / 60)),
                  "interval": float(interval), "sent": False}
        return report
    report = build(root=root, now=now, since=since, interval=interval, runner=runner)
    report["text"] = cd.render(report)
    if send:
        topic, why = review_audit_run.audit_topic(daemon)
        if not topic:
            report["send_why"] = why or "тема сводок не настроена"
        else:
            ok, mid, swhy = review_audit_run.send_audit(report["text"], topic, sender=sender)
            report["sent"] = bool(ok)
            report["topic"] = topic
            report["message_id"] = mid
            report["send_why"] = "" if ok else (swhy or "дверь молчит")
        if report["sent"]:
            st["last"] = now
            st["last_iso"] = stamp_words(now)
            st["message_id"] = report.get("message_id")
            ok, swhy = write_state(st, spath)
            if not ok:
                report["state_why"] = swhy
    # ЖУРНАЛ — ИНДЕКС, А НЕ ЛЕНТА. Шесть строк в сутки на спокойный контур были бы
    # чистым шумом: сводка и так уехала в тему, а журнал ведёт учёт НОВОСТЕЙ.
    # Пишем, когда есть что заметить: контур неспокоен ЛИБО отправка сорвалась.
    if write_journal and (not cd.is_calm(report) or not report.get("sent")):
        (journal_fn or review_audit_run.journal)(cd.journal_line(report))
        report["journaled"] = True
    return report


def line(report):
    """Строка лога демона — коротко и числами."""
    if report.get("skipped"):
        return "сводка контура: %s" % report["skipped"]
    return cd.journal_line(report)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сводка состояния контура полосы ПК (только чтение)")
    ap.add_argument("--status", action="store_true", help="что видит сводка, без сборки текста")
    ap.add_argument("--dry", action="store_true", help="показать сообщение ДОСЛОВНО, не отправляя")
    ap.add_argument("--send", action="store_true", help="собрать и отправить в тему сводок")
    ap.add_argument("--journal", action="store_true", help="дописать строку-индекс в журнал")
    ap.add_argument("--interval", type=float, default=cd.INTERVAL_SEC,
                    help="интервал сводки в секундах (по умолчанию %d = 4 ч)" % cd.INTERVAL_SEC)
    ap.add_argument("--force", action="store_true", help="не ждать интервала")
    args = ap.parse_args(argv)
    if args.status:
        rep = build(interval=args.interval)
        print("окно %s → %s · интервал %s" % (rep["since_words"], rep["now_words"],
                                              cd.interval_words(rep["interval"])))
        for key, title in cd.TOPICS:
            rows = [r for r in (rep["readings"].get(key) or []) if r.get("words")]
            if not rows:
                continue
            print("\n%s:" % title)
            for rd in rows:
                print(cd.line_text(rd))
        print("\nСЕРИЯ: %s" % cd.series_line(rep.get("series"),
                                             (rep["readings"].get("series") or [None])[0]))
        print("ЯЩИК ШТАБА: %s" % cd.shtab_line(rep.get("shtab_taken"), rep.get("day"),
                                               (rep["readings"].get("series") or [None])[0]))
        return 0
    rep = tick(send=bool(args.send), write_journal=bool(args.journal), interval=args.interval,
               force=bool(args.force) or bool(args.dry))
    if rep.get("skipped"):
        print(rep["skipped"])
        return 0
    print(rep.get("text") or "")
    if args.send:
        print("\n--- отправка: %s%s" % ("ушла" if rep.get("sent") else "НЕ ушла",
                                        "" if rep.get("sent") else " · %s" % rep.get("send_why")))
    return 0


if __name__ == "__main__":            # pragma: no cover
    sys.exit(main())
