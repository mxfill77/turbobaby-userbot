# -*- coding: utf-8 -*-
"""vitrina_pc_run.py — РУКИ живой витрины состояния полосы ПК.

Читает уже измеренное другими, ничего не считает заново и меняет ровно ДВЕ вещи
снаружи: одно сообщение в теме сводок (ПРАВКОЙ) и узел пульса в мозге. Решение —
в :mod:`vitrina_pc` (чистая логика).

ИСТОЧНИКИ И ПОЧЕМУ ИМЕННО ОНИ. Ни одного своего замера: всё, что показывает
витрина, уже измерил кто-то другой и положил на диск.

    queue_snapshot_pc.state.json          слепок очереди — демон пишет витком
                                          (он же — простой полосы и приход Штаба)
    pc_orchestrator.task_started.json     отметка claim — «с какого времени в работе»
                                          и час прихода последнего задания Штаба
    tmp/expect_pc/state.json              слой ожиданий О1–О4
    tmp/done_judge_pc/judged.json         вердикты судьи закрытия (ступень C)
    docs/review_inbox                     лоток внешних ответов
    git log                               следы заходов по осям за сутки
    узел мозга `shtab_vitrina`            ЧЕЛОВЕЧЕСКИЕ части — их пишет Штаб

Мост за очередью здесь НЕ ЗОВЁТСЯ, как и у сводки: `get_pending("done")` замерен в
26.8 с, а всё внутри витка исполняется синхронно. Единственный поход в мост —
чтение узла Штаба и запись пульса, оба доверенным писателем.

═══ ОБНОВЛЕНИЕ НА МЕСТЕ: ТРИ ИСХОДА, А НЕ ДВА ══════════════════════════════════

Пункт 4 задания. Идентификатор сообщения хранится в :data:`DEFAULT_STATE` рядом с
подписью чисел, и порядок веток такой:

    подпись та же            → НЕ ТРОГАЕМ НИЧЕГО (правка не нужна — числа не менялись)
    id есть, правка удалась  → правка на месте, id прежний
    id есть, правка сорвалась→ СКАЗАТЬ В ЛОГ и ждать следующего оборота.
                               НОВОГО НЕ ШЛЁМ: сорванная правка — это чаще всего
                               сеть, и слать на каждый сбой новое означало бы
                               строить ту самую ленту, от которой витрина уходит
    Telegram сказал «этого сообщения нет» → и только тогда новое
                               (:func:`vitrina_pc.edit_lost` — признак в ЧИСТОМ слое,
                               проверяется тестом без сети)

ТРЕТИЙ ИСХОД ОБЯЗАТЕЛЕН И ЖИВЁТ В КАЖДОМ ЧТЕНИИ: источник не прочитался → в
витрине стои́т «НЕИЗВЕСТНО» с названной причиной. Ноль вместо незнания — прямой
запрет задания, и он держится дверью :func:`vitrina_pc.number`, а не дисциплиной.
"""
from __future__ import annotations

import argparse
import calendar
import datetime
import io
import json
import os
import sys

import contour_digest as cd
import contour_digest_run as cdr
import shtab_box_signals            # РАЗЛИЧИТЕЛЬ ВИДА РЯДА: тот же, что у остановки ящика
import vitrina_pc as vp
import zayavki_pc

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE = "vitrina_pc_state.json"
CLAIM_FILE = "pc_orchestrator.task_started.json"
# Метка оборота ящика Штаба: в ней же с 02.09 лежит ФРАЗА ЕГО ОСТАНОВКИ. Имя
# зеркалит `pc_orchestrator.SHTAB_BOX_TICK_FILE`; равенство сторожит тест.
BOX_TICK_FILE = "pc_orchestrator.shtab_box_tick.json"

# Узел ПУЛЬСА в мозге: тот же текст витрины, чтобы Штаб видел состояние полосы, не
# заходя в Telegram. Ключ свой; в чужие узлы витрина не пишет ни одной веткой.
PULSE_KEY = "pulse_pc"
PULSE_TITLE = "KB_pulse_pc"


# ───────────────────────────── время ─────────────────────────────

def now_ts(clock=None):
    """Часы одной дверью: тест подменяет их числом, боевой путь берёт системные."""
    if clock is not None:
        return float(clock() if callable(clock) else clock)
    import time

    return time.time()


def day_utc(ts):
    """Календарный день UTC. Тот же разрез, что у ящика Штаба и у внешних ответов."""
    return cdr.day_utc(ts)


def day_start(day):
    """Начало суток UTC по 'YYYY-MM-DD' → epoch | None (третий исход)."""
    try:
        got = datetime.datetime.strptime(str(day or ""), "%Y-%m-%d")
    except ValueError:
        return None
    return float(calendar.timegm(got.timetuple()))


def stamp_words(ts):
    return cdr.stamp_words(ts)


# ───────────────────────────── состояние витрины ─────────────────────────────

def state_path(root=HERE, named=None):
    return named or cdr._path(root, DEFAULT_STATE)


def read_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except Exception:                              # noqa: BLE001
        return {}


def write_state(state, path):
    """Идентификатор сообщения и подпись чисел — ЕДИНСТВЕННОЕ, что пишется на диск."""
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
        return True, ""
    except Exception as exc:                       # noqa: BLE001
        return False, "состояние витрины не записано: %s" % exc


# ───────────────────────────── чтение источников ─────────────────────────────

def read_claims(root=HERE, named=None):
    """Отметки claim демона → {id: epoch}. Отказ чтения → пустой словарь.

    Отметка бывает двух видов (голая строка и словарь с ``at``) — обе живут в
    боевом файле прямо сейчас, и разбирать надо обе: демон дописывал поле годом
    позже, старые записи так и остались строками.
    """
    path = cdr._path(root, named or CLAIM_FILE)
    try:
        with io.open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except Exception:                              # noqa: BLE001 — отметки нет = возраст неизвестен
        return {}
    if not isinstance(got, dict):
        return {}
    out = {}
    for tid, val in got.items():
        raw = val.get("at") if isinstance(val, dict) else val
        at = cdr.parse_iso(raw)
        if at is not None:
            out[str(tid)] = at
    return out


def read_box_stop(root=HERE, named=None):
    """Остановка ящика Штаба из МЕТКИ ОБОРОТА демона → фраза словами. → str.

    ЧИТАЕМ ЧУЖОЙ ФАЙЛ, А НЕ СЧИТАЕМ ЗАНОВО, и это не лень, а цена. Сигналы А и Б
    стоя́т на ЗАКРЫТЫХ рядах очереди, а чтение ``done`` — 26.8 с (замер
    `queue_snapshot_pc`, 14.08). Витрина ходит каждые 10 минут; пересчитывай она
    сигналы сама, это стоило бы полминуты на оборот и завело бы ВТОРОЕ мнение о
    том, остановлен ли ящик, — расходящееся с первым молча.

    Файла нет / поле не строка → ПУСТО, и пустота здесь честная: витрина не
    утверждает «остановки нет», она просто не показывает строки. Судить о жизни
    ящика по молчанию его метки нельзя ровно так же, как о жизни полосы — по
    молчанию витрины (об этом её собственный подвал).
    """
    path = cdr._path(root, named or BOX_TICK_FILE)
    try:
        with io.open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except Exception:                              # noqa: BLE001 — метки нет = показывать нечего
        return ""
    stop = got.get("stop") if isinstance(got, dict) else None
    return str(stop) if isinstance(stop, str) else ""


def running_rows(snapshot, claims, now):
    """Строки очереди В РАБОТЕ и с какого времени. → list | None.

    ``None`` — слепка нет; пустой список — полоса свободна. Разница названа
    словами в :func:`vitrina_pc.part_now`, и подменять одно другим нельзя.
    """
    if snapshot is None:
        return None
    out = []
    for tid, item in ((snapshot.get("open") or {})).items():
        if not isinstance(item, dict) or item.get("status") != "in_progress":
            continue
        at = (claims or {}).get(str(tid))
        out.append({"id": tid, "goal": quote(item.get("goal")),
                    "age": (float(now) - at) if at is not None else None})
    return sorted(out, key=lambda r: (r.get("age") is None, -(r.get("age") or 0)))


def quote(text, limit=vp.GOAL_MAX):
    """ЧУЖОЙ текст в витрину — только через стражу полосы. → str.

    Цель строки очереди и причину падения писали не мы, и абсолютный путь Windows
    внутри них задержал бы стражу ВСЮ витрину целиком (замерено на сводке 02.09:
    голый текст исключения нёс `[WinError 2] … C:\\Users\\…`). Построчная починка
    (:func:`review_audit.safe_line`) меняет одну цитату; второй слой у двери
    остановил бы показ — а молчание витрины дороже одной непоказанной цели.
    """
    import review_audit

    fixed, _kind = review_audit.safe_line(vp.one_line(text, limit))
    return fixed


def idle_facts(snapshot, now):
    """Простой полосы ПРЯМО СЕЙЧАС. → dict | None (слепок не прочитан).

    «Пусто» — это ``open`` без единой строки в :data:`vitrina_pc.WORK_STATUSES`;
    почему `needs_approval` работой не считается, разобрано там же числом.

    С КАКОГО ВРЕМЕНИ ПУСТО берётся у ПОСЛЕДНЕГО ЗАКРЫТИЯ, и это не приближение:
    пока в очереди нет ни одной строки, которую полоса может взять, последним
    событием очереди было именно закрытие — приди новая строка, она стояла бы в
    ``open`` и ветка сюда не дошла бы. Реестр закрытых пуст (свежий чекаут,
    обрезанный слепок) → ``sec=None`` и НЕИЗВЕСТНО с причиной, а не ноль.
    """
    if snapshot is None:
        return None
    busy = [str(tid) for tid, item in (snapshot.get("open") or {}).items()
            if isinstance(item, dict) and str(item.get("status") or "") in vp.WORK_STATUSES]
    if busy:
        return {"busy": len(busy), "ids": sorted(busy, key=lambda s: (len(s), s))}
    last_at, last_id = None, ""
    for tid, item in (snapshot.get("closed") or {}).items():
        if not isinstance(item, dict):
            continue
        try:
            at = float(item.get("at"))
        except (TypeError, ValueError):
            continue
        if last_at is None or at > last_at:
            last_at, last_id = at, str(item.get("id") or tid)
    if last_at is None:
        return {"busy": 0, "sec": None,
                "why": "в реестре закрытых нет ни одной метки времени"}
    return {"busy": 0, "since": last_at, "since_id": last_id,
            "since_words": stamp_words(last_at), "sec": max(0.0, float(now) - last_at)}


def shtab_last_facts(snapshot, claims, now):
    """Когда в очередь ПОСЛЕДНИЙ РАЗ попало задание из ящика Штаба. → dict | None.

    Ряд опознаётся ТЕМ ЖЕ маркером, которым живут дедуп ящика и суточный потолок
    (:data:`shtab_box.MARK_RE`); своей регулярки здесь нет и не будет — разойдись
    они, витрина начала бы звать приход Штаба «ни одного» ровно в тот день, когда
    форму маркера поменяют, и молча.

    ВРЕМЯ БЕРЁТСЯ ЖИВОЕ И НАЗЫВАЕТ СЕБЯ. У маркера есть только СУТКИ (``дата=``),
    а часы приходят с другого конца: отметка claim демона (ряд взят) либо время
    закрытия. Первая точнее и стои́т ближе к приходу — ящик кладёт строку, и демон
    берёт её ближайшим витком, — поэтому claim предпочитается закрытию, а какой
    именно источник дал час, едет в поле ``at_kind`` и печатается словом.
    """
    if snapshot is None:
        return None
    import shtab_box

    best = None
    seen = 0
    for half in ("open", "closed"):
        for tid, item in (snapshot.get(half) or {}).items():
            if not isinstance(item, dict):
                continue
            seen += 1
            hit = shtab_box.MARK_RE.match(str(item.get("goal") or ""))
            if not hit:
                continue
            rid = str(item.get("id") or tid)
            at, kind = (claims or {}).get(rid), "claim"
            if at is None:
                try:
                    at, kind = float(item.get("at")), "close"
                except (TypeError, ValueError):
                    at, kind = None, ""
            row = {"found": True, "id": rid, "day": hit.group(1), "key": hit.group(2),
                   "at": at, "at_kind": kind,
                   "at_words": stamp_words(at) if at is not None else "",
                   "sec": (max(0.0, float(now) - at) if at is not None else None)}
            if best is None or (row["day"], row["at"] or 0.0) > (best["day"], best["at"] or 0.0):
                best = row
    if best is not None:
        return best
    return {"found": False, "scope": "в реестре %d строк" % seen}


def waiting_rows(snapshot):
    """Открытые решения владельца (`needs_approval`), РАЗВЕДЁННЫЕ по видам. → list | None.

    ВИД СПРАШИВАЕТСЯ У РАЗЛИЧИТЕЛЯ ЯЩИКА (:func:`shtab_box_signals.awaiting_kind`),
    а не считается здесь заново, и это правка 03.09.2026. До неё витрина судила
    одним признаком ступени B — и потому звала КАРТОЧКАМИ ГАРДА всё остальное,
    включая заявки разведки: живой замер того же дня даёт четыре таких ряда из
    пяти. Строка «карточки гарда в ожидании: 4» была прямым враньём про красные
    операции, которых не было ни одной.

    Второго определения здесь не заводится намеренно: витрина обязана называть
    ряд тем же словом, каким его называет остановка ящика, — иначе владелец
    читает два числа про один предмет и не знает, какому верить.
    """
    if snapshot is None:
        return None
    out = []
    for tid, item in sorted((snapshot.get("open") or {}).items()):
        if not isinstance(item, dict) or item.get("status") != "needs_approval":
            continue
        goal = item.get("goal")
        mark = zayavki_pc.parse_marker(str(goal or ""))
        kind, how = shtab_box_signals.awaiting_kind(item)
        out.append({"id": tid, "goal": quote(goal),
                    "zayavka": zayavki_pc.is_zayavka(goal),
                    "kind": kind, "kind_why": how,
                    "holds": kind != shtab_box_signals.KIND_ASK,
                    "key": mark[1] if mark else ""})
    return out


def awaiting_counts(waiting):
    """Ждущие решения → два ЧИСЛА разными именами. → dict | None.

    ``holding`` — ряды, ДЕРЖАЩИЕ операцию (карточки гарда плюс неопознанные:
    отказ консервативный). ``asking`` — ждущие мнения. Складывать их нельзя
    НИКОГДА: первое число значит «полоса остановлена», второе — «владельцу есть
    что почитать». Третье, ``blind``, — сколько из держащих попали туда по
    незнанию, чтобы «не разобрали ряд» не читалось как «красная операция».

    `None` держится насквозь: слепок не прочитан — все строки витрины скажут
    «НЕИЗВЕСТНО», и ни одна не подставит ноль.
    """
    if waiting is None:
        return None
    hold = [w for w in waiting if w.get("holds")]
    return {"holding": len(hold), "asking": len(waiting) - len(hold),
            "blind": sum(1 for w in hold
                         if w.get("kind") == shtab_box_signals.KIND_UNKNOWN)}


def failed_rows(snapshot, since):
    """Упавшие строки за сутки. → list | None."""
    if snapshot is None:
        return None
    return [{"id": r.get("id"), "why": quote(r.get("why"), 48)}
            for r in cdr.closed_since(snapshot, since) if r.get("outcome") == "failed"]


def open_expectations(expect):
    """Открытые ожидания О1–О4. → list | None (слой не прочитан)."""
    if expect is None:
        return None
    eps = expect.get("open")
    return sorted(eps) if isinstance(eps, dict) else []


def _hashes(rows):
    """Строки `git log --pretty=%h %s` → множество хешей. `None` держится насквозь."""
    if rows is None:
        return None
    return {ln.split(" ", 1)[0] for ln in rows if ln.strip()}


def axes_of_day(root, since, runner=None):
    """Следы заходов по трём осям за сутки. → (tally, причина).

    Списки путей ВЗЯТЫ У СВОДКИ (:mod:`contour_digest_run`), а не набраны заново:
    два экземпляра одного признака расходятся молча, и тогда сводка и витрина
    показывали бы разное движение одной и той же оси.
    """
    if since is None:
        return vp.axes_tally(None, None, None), "начало суток не сосчитать"
    total, t_why = cdr.git_moves(root, since, ["."], runner=runner)
    axis, a_why = cdr.git_moves(root, since, cdr.AXIS3_PATHS, runner=runner)
    biz, b_why = cdr.git_moves(root, since, cdr.BUSINESS_PATHS, runner=runner)
    why = t_why or a_why or b_why
    return vp.axes_tally(_hashes(total), _hashes(axis), _hashes(biz)), why


def read_shtab(node=None, reader=None):
    """Узел Штаба → разобранные человеческие части. → dict (:func:`vitrina_pc.parse_shtab`).

    Чтение — доверенным писателем (`brain_writer.read_text`), как у ящика: секреты
    моста читает ОН, а этот модуль их не видит. Любой отказ — «Штаб не обновил» с
    причиной; витрину это НЕ роняет ни одной веткой (пункт 6 задания).
    """
    name = node or vp.SHTAB_NODE
    if reader is None:
        def reader(doc):
            import brain_writer

            return brain_writer.read_text(name=doc)
    try:
        text = reader(name)
    except Exception as exc:                       # noqa: BLE001 — любой отказ = «Штаб не обновил»
        return vp.parse_shtab("", ok=False,
                              why="узел %s не прочитан: %s" % (name, str(exc)[:160]))
    if not isinstance(text, str) or not text.strip():
        return vp.parse_shtab("", ok=False, why="узел %s отдал пустой текст" % name)
    return vp.parse_shtab(text)


# ───────────────────────────── сборка ─────────────────────────────

def collect(root=HERE, now=None, runner=None, inbox=None, day=None, shtab=None):
    """Все источники → факты витрины. Ни одной ветки записи. → dict."""
    now = now_ts(now)
    the_day = day or day_utc(now)
    since = day_start(the_day)
    snapshot, _q_at, q_why = cdr.read_json(root, cd.source("queue")["addr"])
    expect, _e_at, _e_why = cdr.read_json(root, cd.source("expect")["addr"])
    heads, records, _i_why = cdr.read_inbox(root, inbox)
    claims = read_claims(root)
    counted = None
    if snapshot is not None:
        counted = cd.series(cdr.all_closed(snapshot), judged=cdr.read_judged(root))
    tally, axes_why = axes_of_day(root, since, runner=runner)
    waiting = waiting_rows(snapshot)
    closed_day = None if snapshot is None else len(cdr.closed_since(snapshot, since))
    external = None
    if heads is not None and records is not None:
        external = cd.external_stats(heads, records, the_day)
    return {
        "schema": vp.SCHEMA,
        "now": now,
        "day": the_day,
        "shtab": shtab if shtab is not None else read_shtab(),
        "running": running_rows(snapshot, claims, now),
        "idle": idle_facts(snapshot, now),
        "shtab_last": shtab_last_facts(snapshot, claims, now),
        "expects": open_expectations(expect),
        "failed": failed_rows(snapshot, since),
        "series": counted,
        "shtab_taken": cd.shtab_taken(cdr.all_rows(snapshot), the_day),
        "box_stop": read_box_stop(root),
        "external": external,
        "awaiting": awaiting_counts(waiting),
        "waiting": waiting,
        "axes": tally,
        "axes_why": axes_why,
        "closed_day": closed_day,
        "queue_why": q_why,
    }


# ───────────────────────────── дверь ─────────────────────────────

def put(text, topic, message_id=None, sender=None, editor=None):
    """Положить витрину в тему. → (ok, message_id, how, why).

    ``how`` — что именно случилось: ``правка`` · ``новое сообщение`` · ``не тронута``.
    Порядок веток разобран в шапке модуля; НОВОЕ шлётся ровно в двух случаях —
    идентификатора нет вовсе, либо Telegram сказал, что сообщения больше нет.
    """
    import review_audit

    hits = review_audit.outbound_safe(text)
    if hits:
        kinds = ", ".join(sorted({h["kind"] for h in hits}))
        return False, message_id, "не тронута", "страж исходящего задержал витрину (%s)" % kinds
    if sender is None or editor is None:
        import dispatch_notify

        sender = sender or dispatch_notify.send_topic_strict
        editor = editor or dispatch_notify.edit_topic_strict
    if message_id:
        _ch, ok, detail = editor(text, topic, message_id)
        if ok or vp.edit_same(detail):
            return True, message_id, "правка", ""
        if not vp.edit_lost(detail):
            # ВРЕМЕННЫЙ отказ: говорим в лог и ждём следующего оборота. Новое
            # сообщение здесь было бы вторым в теме на каждый сбой сети.
            return False, message_id, "не тронута", "правка не удалась: %s" % detail
        why_lost = "старого сообщения больше нет (%s)" % detail
    else:
        why_lost = "идентификатора сообщения не было"
    _ch, ok, detail = sender(text, topic)
    if not ok:
        return False, None, "не тронута", "новое сообщение не ушло: %s" % detail
    return True, detail, "новое сообщение (%s)" % why_lost, ""


def write_pulse(text, key=PULSE_KEY, writer=None):
    """Тот же текст — в узел пульса мозга. → (ok, причина).

    Замена дока целиком доверенным писателем, как у слепка очереди: у витрины нет
    истории по построению, и дописывание превратило бы узел в ленту. Страж усушки
    снят по той же причине — спокойный контур короче тревожного.

    Пульс витрину НЕ РОНЯЕТ: мозг молчит → сообщение в теме всё равно уже стои́т, и
    это сказано строкой, а не проглочено.
    """
    if writer is None:
        def writer(body):
            import brain_writer

            return brain_writer.apply(lambda _old: body, name=key, expect=body,
                                      backup_tag="vitrina_pc", allow_shrink=True)
    try:
        res = writer(text)
    except Exception as exc:                       # noqa: BLE001
        return False, "узел пульса %s не обновлён: %s" % (key, str(exc)[:160])
    ok = bool(res) if not isinstance(res, dict) else bool(res.get("ok", True))
    return ok, "" if ok else "узел пульса %s отказал: %s" % (key, str(res)[:160])


def create_pulse_node():                           # pragma: no cover — зовётся РУКАМИ, один раз
    """Завести узел пульса в мозге. Только из CLI: молчаливое создание из демона
    дало бы дубль на каждый сбой реестра (тот же довод, что у слепка очереди)."""
    import brain_writer

    return brain_writer.create_plain(PULSE_TITLE, key=PULSE_KEY,
                                     text="%s\n(узел заведён, витрины ещё нет)" % vp.HEAD)


# ───────────────────────────── оборот ─────────────────────────────

def tick(root=HERE, state=None, now=None, send=False, pulse=False, runner=None,
         sender=None, editor=None, pulser=None, daemon=None, shtab=None, force=False):
    """Один оборот витрины. → отчёт.

    Порядок намеренный: собрать факты → сверить ПОДПИСЬ ЧИСЕЛ → и только при смене
    трогать тему. Подпись та же — не тратим ни одного вызова API: пункт 4 задания
    «не чаще, чем меняются числа» держится здесь, а не расписанием.
    """
    now = now_ts(now)
    spath = state_path(root, state)
    st = read_state(spath)
    facts = collect(root=root, now=now, runner=runner, shtab=shtab)
    sig = vp.signature(facts)
    changed = force or sig != st.get("sig")
    if not changed:
        return {"skipped": "числа не менялись с %s — витрину не трогаем"
                           % (st.get("changed_iso") or "?"),
                "sig_same": True, "message_id": st.get("message_id"), "ok": True,
                "how": "не тронута", "facts": facts}
    changed_at = now
    text = vp.render(facts, stamp_words(changed_at))
    report = {"text": text, "facts": facts, "ok": False, "how": "не тронута", "why": "",
              "message_id": st.get("message_id"), "sig_same": False}
    if not send:
        return report
    topic, why = cdr.review_audit_run.audit_topic(daemon)
    if not topic:
        report["why"] = why or "тема витрины не настроена"
        return report
    report["topic"] = topic
    ok, mid, how, put_why = put(text, topic, st.get("message_id"), sender=sender, editor=editor)
    report.update({"ok": ok, "how": how, "why": put_why, "message_id": mid})
    if ok:
        st.update({"message_id": mid, "topic": topic, "sig": sig, "changed": changed_at,
                   "changed_iso": stamp_words(changed_at)})
        wrote, swhy = write_state(st, spath)
        if not wrote:
            report["state_why"] = swhy
    if pulse and ok:
        p_ok, p_why = write_pulse(text, writer=pulser)
        report["pulse"], report["pulse_why"] = p_ok, p_why
    return report


def line(report):
    """Строка лога демона — коротко и по делу."""
    return vp.journal_line(report)


def main(argv=None):                               # pragma: no cover — руки
    ap = argparse.ArgumentParser(description="Живая витрина состояния полосы ПК")
    ap.add_argument("--status", action="store_true", help="что видит витрина, числами")
    ap.add_argument("--dry", action="store_true", help="показать витрину ДОСЛОВНО, не отправляя")
    ap.add_argument("--send", action="store_true", help="положить/поправить витрину в теме")
    ap.add_argument("--pulse", action="store_true", help="тот же текст — в узел пульса мозга")
    ap.add_argument("--create-node", action="store_true", help="завести узел пульса (один раз)")
    ap.add_argument("--force", action="store_true", help="не ждать смены чисел")
    args = ap.parse_args(argv)
    if args.create_node:
        print(create_pulse_node())
        return 0
    if args.status:
        facts = collect()
        print(vp.body(facts))
        return 0
    if args.dry:
        facts = collect()
        print(vp.render(facts, stamp_words(now_ts())))
        return 0
    rep = tick(send=bool(args.send), pulse=bool(args.pulse), force=bool(args.force))
    if rep.get("skipped"):
        print(rep["skipped"])
        return 0
    print(rep.get("text") or "")
    print("\n--- витрина: %s%s" % (rep.get("how"),
                                   "" if rep.get("ok") else " · %s" % rep.get("why")))
    if args.pulse:
        print("--- пульс: %s" % ("лёг" if rep.get("pulse") else rep.get("pulse_why") or "не звался"))
    return 0


if __name__ == "__main__":            # pragma: no cover
    sys.exit(main())
