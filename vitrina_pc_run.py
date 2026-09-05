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
    tmp/expect_pc/state.json              слой ожиданий О1–О6, и он же — СЛОВА О КАЖДОМ
                                          УЗЛЕ (слот `health`, пишется каждым тиком)
    pc_orchestrator.client_watch.json     снимок надзора контур-вотчдога: сдался ли он
                                          на ком-нибудь из троих детей
    tmp/done_judge_pc/judged.json         вердикты судьи закрытия (ступень C)
    docs/review_inbox                     лоток внешних ответов
    git log                               следы заходов по осям за сутки
    документ папки мозга `shtab_vitrina`  ЧЕЛОВЕЧЕСКИЕ части — их пишет Штаб;
                                          берётся ДОРОГОЙ ЯЩИКА (перечисление папки
                                          + чтение по file id), см. `read_shtab`

Мост за очередью здесь НЕ ЗОВЁТСЯ, как и у сводки: `get_pending("done")` замерен в
26.8 с, а всё внутри витка исполняется синхронно. Единственный поход в мост —
слова Штаба и запись пульса, оба доверенным писателем.

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
import expectations_pc as ex     # СЛОВА ПРИБОРОВ И ПЕРЕЧЕНЬ ДЕТЕЙ — оттуда, где ими судят
import shtab_box_run as sbr         # ДОРОГА К ПАПКЕ МОЗГА: та же, которой ящик берёт задания
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


# ───────────────────── УЗЛЫ КОНТУРА И ИХ ПРИГОВОРЫ ─────────────────────
# СПИСОК СОБИРАЕТСЯ ИЗ ЖИВОГО КОНТУРА, а не набирается здесь руками, и правило отбора одно:
# УЗЛОМ становится то, у чего на полосе УЖЕ есть свой продукт на диске и прибор, который этот
# продукт УЖЕ судит. Всё, чему пришлось бы завести пробу, в список не идёт — прямой запрет
# задания («новых приборов не изобретать»), и отказ этот назван в артефакте числом, а не молча.
#
# Отсюда шесть узлов, и ни один из них здесь не выдуман:
#   pc_orchestrator  — имя берётся у `expectations_pc.HEALTH_DAEMON`, приговор снимает О2
#   pc_agent · userbot · moderation_bot — перечень берётся у `expectations_pc.KIDS` (это
#                      единственное определение слова «дети» на полосе: копия списка
#                      контур-вотчдога, закреплённая тестом), приговор снимает О3/`kids_state`
#   контур-вотчдог   — судится СВОИМ продуктом, снимком надзора `client_watch.json`
#   наблюдатель ожиданий — судится СВОИМ продуктом, файлом состояния `tmp/expect_pc/state.json`
WATCH_FILE = "pc_orchestrator.client_watch.json"
# Предел свежести снимка надзора. Контур-вотчдог тикает раз в 300 с (`pc_orchestrator
# .CLIENT_WATCH_SEC`) ВНУТРИ витка демона; берём три промаха подряд — тем же счётом, каким сводка
# меряет свои источники (`contour_digest.SOURCES`, «три промаха подряд»).
WATCH_LIMIT_SEC = 900.0
WATCHDOG_NODE = "контур-вотчдог"
WATCHER_NODE = "наблюдатель ожиданий (TurboBabyExpectPC)"
# ЧЕГО ПРИБОР НЕ ВИДИТ НА ЭТОМ УЗЛЕ. Оговорки детей НЕ ПЕРЕПИСАНЫ, а взяты у самого прибора
# (`expectations_pc.KID_SIGNS[...]["caveat"]`): две копии одной оговорки разошлись бы молча на
# первой же правке признака. Своих здесь ровно три — на узлы, у которых оговорки в слое нет.
HEALTH_BLIND = {
    ex.HEALTH_DAEMON: "виток доказывает оборот, а не пользу; объявленный заход оправдывает тишину",
    # Дословно у прибора: он же и объяснил, почему этот снимок не годится в факт о детях.
    WATCHDOG_NODE: ex.KID_REJECTED["pc_orchestrator.client_watch.json"],
    WATCHER_NODE: "судит себя своим же файлом; выключенный ПК не наблюдает никто — это предмет "
                  "СЕРВЕРНОГО О4, а не этой строки",
}


def read_watch(root=HERE, named=None):
    """Снимок надзора контур-вотчдога → (данные, время замера, причина). Тем же чтением, что
    у сводки: третий исход держится дверью :func:`contour_digest_run.read_json`."""
    return cdr.read_json(root, named or WATCH_FILE)


def watchdog_said(watch):
    """Снимок надзора → СЛОВО прибора о самом стороже. → (слово, чем снято).

    Сторож говорит о себе ровно тем, что в снимке: ребёнок, на котором он СДАЛСЯ (`halted`),
    и есть его собственный отказ — дальше он этого ребёнка не поднимает ни разу. Возраст снимка
    здесь НЕ судится: он едет отдельной осью (`stale`) и решает уже :func:`vitrina_pc.health_verdict`.
    """
    if not isinstance(watch, dict):
        return None, "снимок надзора не прочитан"
    kids = watch.get("children")
    if not isinstance(kids, dict) or not kids:
        return None, "в снимке надзора нет ни одного ребёнка"
    halted = sorted(str(n) for n, v in kids.items()
                    if isinstance(v, dict) and v.get("halted"))
    if halted:
        return ex.MOD_IDLE, "сдался на %s (подъёмов больше не будет)" % ", ".join(halted)
    return ex.MOD_OK, "надзор за %d детьми, сдавшихся нет" % len(kids)


def health_nodes(expect, expect_at, watch, watch_at, now):
    """Узлы контура с приговорами. → list | None (ни один источник не прочитан).

    ДВЕ ОСИ У КАЖДОГО УЗЛА, и путать их нельзя: СЛОВО прибора и ВОЗРАСТ ИСТОЧНИКА этого слова.
    Свежее слово из замершего источника — это не свежее слово, и решает такую пару чистый слой
    (:func:`vitrina_pc.health_verdict`); здесь только читается и складывается.

    Возраст четырёх первых узлов — возраст ФАЙЛА НАБЛЮДАТЕЛЯ, а не возраст их собственных
    продуктов, и это сказано намеренно: продукт ребёнка щупает слой ожиданий, а мы читаем то, что
    он записал. Наблюдатель замер → все четыре строки становятся НЕИЗВЕСТНО разом, и это верно:
    свежесть чужих продуктов нам в тот момент никто не мерил.
    """
    stale_ex = cd.stale(expect_at, now, cd.limit_of("expect"))
    age_ex = cd.age_words(expect_at, now)
    stale_wd = cd.stale(watch_at, now, WATCH_LIMIT_SEC)
    age_wd = cd.age_words(watch_at, now)
    said = {}
    slot = (expect or {}).get(ex.HEALTH_SLOT) if isinstance(expect, dict) else None
    for row in ((slot or {}).get("rows") or []) if isinstance(slot, dict) else []:
        if isinstance(row, dict) and row.get("name"):
            said[str(row["name"])] = row

    def _from_layer(name, blind):
        row = said.get(name) or {}
        src = row.get("src") or ("вердикта о «%s» в состоянии наблюдателя нет" % name)
        return {"name": name, "said": row.get("said"), "stale": stale_ex,
                "src": quote(src, 72), "age": age_ex, "blind": quote(blind, 120)}

    out = [_from_layer(ex.HEALTH_DAEMON, HEALTH_BLIND[ex.HEALTH_DAEMON])]
    out += [_from_layer(kid, (ex.KID_SIGNS.get(kid) or {}).get("caveat") or "")
            for kid in ex.KIDS]
    word, why = watchdog_said(watch)
    out.append({"name": WATCHDOG_NODE, "said": word, "stale": stale_wd,
                "src": quote("%s: %s" % (WATCH_FILE, why), 72), "age": age_wd,
                "blind": quote(HEALTH_BLIND[WATCHDOG_NODE], 120)})
    # НАБЛЮДАТЕЛЬ СУДИТСЯ СВОИМ ПРОДУКТОМ, и слово у него одно: файл он пишет каждым тиком, значит
    # «написал» и есть «работает». Молчание файла приговором «мёртв» НЕ становится — на этой полосе
    # его нечем отличить от сна машины (стенные часы сон считают, а монотонных здесь нет), и третий
    # исход тут не вежливость, а единственный честный ответ.
    out.append({"name": WATCHER_NODE, "said": ex.MOD_OK if expect is not None else None,
                "stale": stale_ex, "age": age_ex,
                "src": quote("%s (предел %d мин)"
                             % (cd.source("expect")["addr"],
                                int(round(float(cd.limit_of("expect")) / 60.0))), 72),
                "blind": quote(HEALTH_BLIND[WATCHER_NODE], 120)})
    return out


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


def read_shtab(node=None, reader=None, lister=None):
    """Слова Штаба → разобранные человеческие части. → dict (:func:`vitrina_pc.parse_shtab`).

    ═══ ИСТОЧНИК — ДОКУМЕНТ ПАПКИ, А НЕ УЗЕЛ ПО ИМЕНИ (правка 04.09.2026) ══════

    До неё витрина звала ``brain_writer.read_text(name=SHTAB_NODE)``, и это НЕ
    работало ни разу: имя ``shtab_vitrina`` в живом РЕЕСТРЕ моста не
    зарегистрировано, а на незарегистрированное имя мост отвечает дословно (живая
    проба 04.09) — «ИМЯ НЕ РАЗРЕШЕНО: 'shtab_vitrina' … в живом реестре такого
    ключа нет (ключей 37)». Отказ витрина честно печатала словами, поэтому сломан
    был РОТ, а не панель: числа собирались исправно, а три человеческие части
    сутками говорили «Штаб не обновил». Документ при этом лежал на месте — Штаб
    положил его 03.09 ПРЯМО В ПАПКУ, а такой документ реестру не виден вовсе
    (`brain_writer.list_folder`, §«детей 49, ключей 37, БЕЗ КЛЮЧА 15»).

    ДОРОГА ВЗЯТА У ЯЩИКА ЦЕЛИКОМ, И ЭТО УСЛОВИЕ, А НЕ ЭКОНОМИЯ. Перечисление —
    :func:`shtab_box_run.read_folder` (там же живёт правило «усечение = отказ, а не
    короткий список»), тело — :func:`shtab_box_run.read_doc_text` (по file id, ибо
    по имени такой документ мост не отдаёт). Заведи витрина свой поход в ту же
    папку — вторая дорога разошлась бы с первой МОЛЧА: усечение, лечёное в одной,
    в другой давало бы уверенный неверный ответ. Числовой адрес документа здесь не
    зашит ни одной строкой: имя берётся из :data:`vitrina_pc.SHTAB_NODE`, id
    приходит из перечисления.

    ЧЕТЫРЕ ОТКАЗА НАЗЫВАЮТСЯ ПРИЧИНОЙ, А НЕ ПУСТОТОЙ (пункт 3 задания): папка не
    перечислена · документа с таким именем в ней нет · имя несут ДВА документа
    (Drive это разрешает, и который из них Штаба — неизвестно; тот же
    консервативный отказ, что у :func:`shtab_box.parse_folder` на двойниках) ·
    тело не прочитано либо пусто. Каждый едет в ``why`` и оттуда — в текст всех
    трёх частей: отличать «Штаб молчит» от «мост молчит» обязан читающий.

    Витрину не роняет ни одна ветка (пункт 6 прежнего задания): ``read_folder`` и
    ``read_doc_text`` сами ловят любое исключение и отдают причину строкой.
    """
    name = node or vp.SHTAB_NODE
    files, ok, why = sbr.read_folder(prefix=name, lister=lister)
    if not ok:
        return vp.parse_shtab("", ok=False,
                              why="папка мозга не прочитана: %s" % (why or "причина не названа"))
    hits = [f for f in files
            if isinstance(f, dict) and str(f.get("name") or "") == name]
    if not hits:
        return vp.parse_shtab("", ok=False,
                              why="документа %s в папке мозга нет (детей с этим началом имени: %d)"
                                  % (name, len(files)))
    if len(hits) > 1:
        return vp.parse_shtab("", ok=False,
                              why="имя %s несут %d документа папки — который из них Штаба, "
                                  "неизвестно, не берём ни один" % (name, len(hits)))
    text, got, why = sbr.read_doc_text(hits[0].get("id"), reader=reader)
    if not got:
        return vp.parse_shtab("", ok=False,
                              why="документ %s не прочитан: %s" % (name, why or "причина не названа"))
    return vp.parse_shtab(text)


# ───────────────────────────── сборка ─────────────────────────────

def collect(root=HERE, now=None, runner=None, inbox=None, day=None, shtab=None):
    """Все источники → факты витрины. Ни одной ветки записи. → dict."""
    now = now_ts(now)
    the_day = day or day_utc(now)
    since = day_start(the_day)
    snapshot, _q_at, q_why = cdr.read_json(root, cd.source("queue")["addr"])
    expect, expect_at, _e_why = cdr.read_json(root, cd.source("expect")["addr"])
    watch, watch_at, _w_why = read_watch(root)
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
        "health": health_nodes(expect, expect_at, watch, watch_at, now),
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
