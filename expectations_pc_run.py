#!/usr/bin/env python3
"""РУКИ СЛОЯ ОЖИДАНИЙ ПОЛОСЫ ПК — ярус 2 (11.08.2026). Зеркало `expectations_run.py` сервера.

Решение живёт в `expectations_pc.py` (чистая функция «факты → вердикт», ни одного обращения к
миру). Здесь ровно руки: СОБРАТЬ факты, ОТНЕСТИ вердикт в канал, СОХРАНИТЬ состояние эпизодов.

ПОЧЕМУ ОТДЕЛЬНЫЙ ПРОЦЕСС, А НЕ ВЕТКА В ДЕМОНЕ (правило владельца «наблюдатель не живёт на том,
за чем следит»). Все сторожа очереди ПК — реапер одиночек `process_stuck_singles`, реапер сирот,
TTL подтверждения `process_approval_timeouts`, сторож застрявших цепей `process_stuck_chains` —
живут ВНУТРИ `poll_once`, то есть внутри того самого оборота, чьё отсутствие и есть предмет О2.
Демон, переставший крутиться, не судит себя ни одной из этих веток; наблюдатель обязан быть вне.

ШЕСТЬ ИСТОЧНИКОВ ФАКТОВ, КАЖДЫЙ ПЕРЕЖИВАЕТ СМЕРТЬ СВОЕГО ПРЕДМЕТА:
  очередь   — GET моста (`get_pending` по открытым статусам, полоса pc). ТОЛЬКО чтение;
  оборот    — файл `pc_orchestrator.heartbeat`, который демон пишет ПОСЛЕДНЕЙ строкой poll_once;
  занятость — `pc_orchestrator.task_started.json`: отметка CLAIM на диске (объявленный заход);
  модербот  — mtime `moderation_ipc.db` (ПРОДУКТ его пятисекундного тика) плюс проба процесса по
              `moderation_bot.lock`. База НЕ ОТКРЫВАЕТСЯ: `os.stat`, ни sqlite, ни запроса —
              наблюдатель не смеет соперничать с боевым писателем за замок файла;
  часы      — QueryUnbiasedInterruptTime: время БОДРСТВОВАНИЯ машины (сон в него не идёт);
  след      — О4: реестр `cowork_log.ledger` (отметка КАЖДОЙ строки, дошедшей до мозга) — тот
              самый поток, чей возраст меряет серверное О4. Только чтение хвоста файла.

ГРАНИЦА О4 ОТДЕЛЬНОЙ СТРОКОЙ: у этой ветки есть КАНАЛ (пульс в журнал), но нет ЗУБОВ. Она ничего
не перезапускает, очередь не трогает и владельцу не пишет вовсе — молчание пульса адресовано
наблюдателю ВНЕ машины. Пульс уходит ТОЛЬКО при доказанном обороте: «неизвестно» его не пишет
никогда, иначе пульс отнял бы у серверного О4 зубы ровно там, где нужнее всего правда.

ГДЕ СТОИТ САМ НАБЛЮДАТЕЛЬ И ПОЧЕМУ ЭТО ВАЖНО ДЛЯ О3. Задача Планировщика `TurboBabyExpectPC`,
каждые 10 минут, под SYSTEM, отдельным процессом — то есть ВНЕ всех трёх наблюдаемых. Модербот
падает — наблюдатель тикает; демон встаёт — наблюдатель тикает. Живой случай, ради которого это
не формальность: 05.08 09:51:48 → 06.08 19:44:10 модербот лежал 33.9 часа, и контур-вотчдог
демона (единственный, кто за ним следит) молчал всё это время ровно потому, что стоял сам демон.
Демон отсюда НЕ импортируется ради логики — только ради боевого клиента моста (секреты берёт сам
импортируемый модуль, здесь их нет и они не читаются). Импорт идёт с `TURBOBABY_TEST_LOGS=1`,
иначе наблюдатель повесил бы свой хендлер на БОЕВОЙ журнал демона — известный класс «тесты сорят
в боевой лог».

ГРАНИЦА: ожидание ТОЛЬКО НАБЛЮДАЕТ. Единственная реакция — заметка владельцу. Задач не ставит
(ветки нет вовсе — в отличие от серверного оригинала), процессов не перезапускает, очередь не
мутирует (ни claim, ни complete, ни heartbeat, ни enqueue), состояние демона не трогает: свои
файлы живут в ОТДЕЛЬНОМ каталоге `tmp/expect_pc/`.

ПРОТИВ read-and-ignore: одна заметка на эпизод и одна на его закрытие. Повторов-напоминаний НЕТ.

FAIL-SAFE: любой сбой сбора → факта нет → вердикта нет (молчание). Заметка не ушла → эпизод НЕ
помечен, скажем на следующем прогоне.

ОТКАТ: порог соответствующей ветки = 0 в окружении (EXPECT_PC_NEW_MIN / EXPECT_PC_RUN_MIN /
EXPECT_PC_TURN_MIN / EXPECT_PC_MOD_MIN / EXPECT_PC_LIFE_MIN) — ветка мертва целиком; полностью —
снять задачу Планировщика наблюдателя. У О4 откат стоит ЛИШНЕЙ строки в мозге раз в 6 часов и
возвращает серверное О4 к прежнему признаку «след работы», то есть к 4 ложным эпизодам за 11 суток.

ЗАПУСК:
    venv\\Scripts\\python.exe expectations_pc_run.py            # боевой прогон (заметки уходят)
    venv\\Scripts\\python.exe expectations_pc_run.py --dry      # решение и факты, канал не трогаем
    venv\\Scripts\\python.exe expectations_pc_run.py --status   # три состояния словами, без канала
"""
import ctypes
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import expectations_pc as ex                                          # noqa: E402

LANE_LABEL = "ПК"
HEARTBEAT_FILE = os.path.join(REPO, "pc_orchestrator.heartbeat")
TASK_START_FILE = os.path.join(REPO, "pc_orchestrator.task_started.json")
MOD_IPC_FILE = os.path.join(REPO, "moderation_ipc.db")        # О3: ПРОДУКТ тика, читаем только stat
MOD_LOCK_FILE = os.path.join(REPO, "moderation_bot.lock")     # О3: чей это продукт (номер процесса)
# О4: реестр УСПЕШНО ушедших строк журнала (`cowork_log_append.ledger_add`) — ровно то, что
# серверное О4 видит как «след с ПК». Читаем ХВОСТ файла: кольцо на 500 строк, а нужна последняя.
LEDGER_FILE = os.path.join(REPO, "cowork_log.ledger")
LEDGER_TAIL_BYTES = 65536
STATE_DIR = os.path.join(REPO, "tmp", "expect_pc")     # СВОЙ каталог: файлы демона не трогаем
STATE_FILE = "state.json"
STATE_KEEP = 32
# Сколько максимум засчитывать в тишину за ОДНО наблюдение. Наблюдатель мог не работать час, и о
# том, крутился ли демон в этот час, мы не знаем НИЧЕГО: записать его в «молчал» значило бы
# выдумать факт. Два периода наблюдателя — та же дисциплина, что у серверного WAIT_STEP_CAP.
STEP_CAP_SEC = 1200.0


def _dir():
    """Каталог состояния наблюдателя. CC_EXPECT_PC_DIR — явная подмена (тест не пишет в боевой)."""
    return (os.environ.get("CC_EXPECT_PC_DIR") or "").strip() or STATE_DIR


# ═══════════════════════════ ЧАСЫ БОДРСТВОВАНИЯ ════════════════════════════════════════════
_QUIT_FN = None


def awake_seconds():
    """Секунды БОДРСТВОВАНИЯ машины (с загрузки; проспанное в S3/гибернации не идёт) | None.

    Зеркало `pc_orchestrator.awake_monotonic`, но СВОЁ: наблюдатель не вправе зависеть от импорта
    того, за чем следит. Отличие сознательное — здесь НЕТ фолбэка на `time.monotonic`: у демона
    фолбэк правильный (часы не смеют его остановить), а у наблюдателя он дал бы ложную тишину на
    каждом сне ПК. Нет часов → None → исход «неизвестно»."""
    global _QUIT_FN
    if _QUIT_FN is None:
        try:
            fn = ctypes.windll.kernel32.QueryUnbiasedInterruptTime
            fn.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
            fn.restype = ctypes.c_int
            _QUIT_FN = fn
        except Exception:
            _QUIT_FN = False
    if not _QUIT_FN:
        return None
    try:
        v = ctypes.c_ulonglong()
        if not _QUIT_FN(ctypes.byref(v)):
            return None
        return v.value / 1e7                     # 100-нс тики → секунды
    except Exception:
        return None


# ═════════════════════════════════ СБОР ФАКТОВ (только чтение) ═════════════════════════════
def heartbeat_facts(path=None):
    """Продукт оборота демона → {"ok","raw","err"}. Файла нет / не читается → ok=False, и это
    ЧЕСТНОЕ НЕЗНАНИЕ: «демон не писал» и «мы не прочитали» отсюда неотличимы."""
    p = path or HEARTBEAT_FILE
    try:
        with open(p, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError as e:
        return {"ok": False, "raw": "", "err": "%s: %s" % (type(e).__name__, str(e)[:80])}
    if not raw:
        return {"ok": False, "raw": "", "err": "файл heartbeat пуст"}
    return {"ok": True, "raw": raw, "err": ""}


def trace_facts(path=None, attempt=None):
    """ПОСЛЕДНИЙ след полосы наружу → {"ok","ts","line","attempt","err"} (факт О4).

    ИСТОЧНИК ВЫБРАН НЕ ПО УДОБСТВУ: реестр `cowork_log.ledger` — это отметка каждой строки,
    которая ДОШЛА до мозга (пишет её сам `cowork_log_append` после успеха), то есть ровно тот
    поток, чей возраст меряет серверное О4. Спул провалившихся сюда не входит намеренно: строка,
    не дошедшая до мозга, следом снаружи не является.

    Пульс, ушедший отсюда, попадает в тот же реестр (его пишет процесс писателя), поэтому
    отдельного счётчика «когда пульсовали» не нужно — успех виден тем же фактом, что и работа.

    НУЛЬ ПО НЕРАЗБОРУ ЗДЕСЬ ЗАПРЕЩЁН: «файла нет», «ни одна строка не разобрана» и «последний
    след стар» — три разные новости, и первые две отдают ok=False с названной причиной, а не
    молчаливое «следов нет» (оно означало бы «пора пульсовать» и врало бы наружу каждый тик)."""
    p = path or LEDGER_FILE
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - LEDGER_TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
    except OSError as e:
        return {"ok": False, "ts": None, "line": "", "attempt": attempt,
                "err": "%s: %s" % (type(e).__name__, str(e)[:80])}
    lines = [x for x in tail.splitlines() if x.strip()]
    if size > LEDGER_TAIL_BYTES and lines:
        lines = lines[1:]                       # первая строка хвоста обрезана серединой — не наша
    bad = 0
    for raw in reversed(lines):
        try:
            rec = json.loads(raw)
        except ValueError:
            bad += 1                            # битая строка реестра — считаем и идём дальше
            continue
        if not isinstance(rec, dict):
            bad += 1
            continue
        ts = ex.parse_iso(rec.get("ts"))
        if ts:
            note = rec.get("line")
            return {"ok": True, "ts": ts, "attempt": attempt, "err": "",
                    "line": note[:120] if isinstance(note, str) else ""}
        bad += 1
    return {"ok": False, "ts": None, "line": "", "attempt": attempt,
            "err": "реестр следов есть (%d строк в хвосте), но ни одной разобранной отметки "
                   "времени в нём нет (не разобрано %d)" % (len(lines), bad)}


# ═══════════════════ ПРОБА ПРОЦЕССА: ПРАВО СПРОСИТЬ, А НЕ ТРОНУТЬ ══════════════════════════
_PROC_QUERY_LIMITED = 0x1000            # PROCESS_QUERY_LIMITED_INFORMATION
_ERR_INVALID_PARAMETER = 87             # такого номера в системе нет
_ERR_ACCESS_DENIED = 5                  # процесс ЕСТЬ, но чужой — «нет» это не значит
_FILETIME_EPOCH = 11644473600.0         # 1601-01-01 → 1970-01-01, секунды


def process_probe(pid):
    """→ (есть ли процесс с этим номером | None, когда запущен | None). Ни одного права ЧТО-ТО
    ему сделать: открываем PROCESS_QUERY_LIMITED_INFORMATION — «спросить», не «тронуть».

    `os.kill(pid, 0)` здесь ЗАПРЕЩЁН НАМЕРЕННО, и это не стилистика: на Windows он не пингует, а
    зовёт TerminateProcess — наблюдатель убил бы наблюдаемого одной строкой, ровно нарушив главный
    запрет этого захода. Отказ доступа честно отличается от отсутствия: чужой процесс существует.
    Возраст запуска нужен решению против переиспользования номеров (`ex.moderbot_writer`)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None, None
    if pid <= 0:
        return None, None
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(_PROC_QUERY_LIMITED, False, pid)
    except Exception:                                                 # noqa: BLE001
        return None, None                          # не Windows / вызов не состоялся → «неизвестно»
    if not h:
        try:
            err = k32.GetLastError()
        except Exception:                                             # noqa: BLE001
            return None, None
        if err == _ERR_INVALID_PARAMETER:
            return False, None                     # ДОКАЗАННОЕ отсутствие — единственный False
        if err == _ERR_ACCESS_DENIED:
            return True, None                      # есть, но возраст не добыть → авторство «не знаю»
        return None, None
    started = None
    try:
        cre, ext, ker, usr = (ctypes.c_ulonglong() for _ in range(4))
        if k32.GetProcessTimes(h, ctypes.byref(cre), ctypes.byref(ext),
                               ctypes.byref(ker), ctypes.byref(usr)) and cre.value:
            started = cre.value / 1e7 - _FILETIME_EPOCH
    except Exception:                                                 # noqa: BLE001
        started = None
    finally:
        try:
            k32.CloseHandle(h)
        except Exception:                                             # noqa: BLE001
            pass
    return True, started


def moderbot_facts(ipc=None, lock=None):
    """ПРОДУКТ модербота и авторство → {"ok","mtime","pid","opened","started","lock_mtime","err"}.

    ПРОДУКТ, А НЕ ЖИЗНЬ: `job_heartbeat` каждые 5с коммитит запись в `moderation_ipc`, соединение
    закрывается на каждом вызове (WAL-чекпойнт), поэтому mtime основного файла двигается на каждый
    тик — измерено живой пробой 11.08 (73 шага, max 5.02с). Базу НЕ ОТКРЫВАЕМ: `os.stat` не
    трогает ни одной страницы и не соперничает с боевым писателем за замок.

    Лока нет / номер не разобран → авторство остаётся неподтверждённым (None), а не выдумывается:
    в решении это даёт «неизвестно», и ни одна дорога отсюда не ведёт к «работает»."""
    out = {"ok": False, "mtime": None, "pid": None, "opened": None, "started": None,
           "lock_mtime": None, "err": ""}
    try:
        out["mtime"] = os.stat(ipc or MOD_IPC_FILE).st_mtime
        out["ok"] = True
    except OSError as e:
        out["err"] = "%s: %s" % (type(e).__name__, str(e)[:80])
        return out
    lp = lock or MOD_LOCK_FILE
    try:
        with open(lp, encoding="utf-8") as f:
            raw = f.read().strip()
        out["lock_mtime"] = os.stat(lp).st_mtime
        out["pid"] = int(raw)
    except (OSError, ValueError) as e:
        out["err"] = "лок модербота не прочитан: %s" % str(e)[:60]
        return out
    out["opened"], out["started"] = process_probe(out["pid"])
    return out


def busy_facts(path=None):
    """Объявленный заход демона → {"task","since","limit"} | None.

    Читаем реестр отметок CLAIM (`pc_orchestrator.task_started.json`) и берём САМУЮ СВЕЖУЮ.
    Реестр отметок не чистится по завершении задачи — поэтому доверие к штампу ограничено сверху
    самим решением (`expectations_pc.busy_state`): дольше «объявленный срок + хвост» он не
    оправдывает ничего, и штамп умершего экземпляра слепит наблюдателя не дольше 55 минут."""
    try:
        with open(path or TASK_START_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    best, best_id = None, None
    for tid, val in d.items():
        raw = val.get("at") if isinstance(val, dict) else val
        ts = ex.parse_iso(raw)
        if ts is None:
            continue
        if best is None or ts > best:
            best, best_id = ts, tid
    if best is None:
        return None
    return {"task": best_id, "since": best, "limit": ex.TASK_TIMEOUT_SEC}


def _bridge():
    """Боевой клиент моста. Импорт демона ТОЛЬКО ради него и ТОЛЬКО с уводом логов в temp:
    иначе наблюдатель пишет в боевой журнал того, за кем следит."""
    os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")
    import pc_orchestrator as o                                       # noqa: E402
    return o.bc


def queue_facts(getter=None):
    """Снимок открытых строк полосы ПК → {"ok","rows","dt","err"}. ТОЛЬКО GET: ни claim, ни
    complete, ни heartbeat, ни enqueue отсюда не зовутся вовсе.

    Мост молчит хотя бы по одному статусу → ok=False целиком: половина снимка хуже отсутствующего,
    по ней «очередь стои́т» неотличимо от «мы не всё прочитали»."""
    t0 = time.time()
    try:
        get = getter or _bridge().get_pending
    except Exception as e:                                            # noqa: BLE001
        return {"ok": False, "rows": [], "dt": None,
                "err": "клиент моста не собрался: %s" % str(e)[:120]}
    rows = []
    for st in ex.OPEN_STATUSES:
        try:
            r = get(st)
        except Exception as e:                                        # noqa: BLE001
            return {"ok": False, "rows": [], "dt": time.time() - t0, "err": str(e)[:120]}
        if not (isinstance(r, dict) and r.get("ok")):
            return {"ok": False, "rows": [], "dt": time.time() - t0,
                    "err": "мост ответил без ok на %s (%s)"
                           % (st, (r or {}).get("error") if isinstance(r, dict) else "?")}
        for it in (r.get("items") or []):
            if not isinstance(it, dict):
                continue
            # «когда строка вошла в НЫНЕШНЕЕ состояние»: updated, а при его отсутствии created.
            since = ex.parse_iso(it.get("updated")) or ex.parse_iso(it.get("created"))
            rows.append({"id": it.get("id"), "status": it.get("status") or st,
                         "lane": it.get("lane"), "from": it.get("from"), "since": since,
                         "text": str(it.get("task_text") or "")[:200]})
    return {"ok": True, "rows": rows, "dt": time.time() - t0, "err": ""}


# ═══════════════ СОСТОЯНИЕ: НАКОПЛЕННАЯ ТИШИНА И ЧИСТОЕ ОЖИДАНИЕ ═══════════════════════════
def load_state():
    try:
        with open(os.path.join(_dir(), STATE_FILE), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(st):
    """Best-effort: диск недоступен → худшее, что случится, — повтор заметки на следующем прогоне."""
    d = _dir()
    try:
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, STATE_FILE + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(d, STATE_FILE))
        return True
    except Exception as e:                                            # noqa: BLE001
        print("состояние не сохранено (%s) — возможен повтор заметки" % e, file=sys.stderr)
        return False


def update_silence(state, hb, awake, now):
    """Накопить ТИШИНУ ОБОРОТА в часах бодрствования → факт {"measured","awake","why"}.

    ПОЧЕМУ СЧЁТЧИК, А НЕ ВОЗРАСТ ФАЙЛА. Возраст heartbeat по стенным часам после сна ПК равен
    длительности сна, а сон — не молчание демона (во сне не крутится никто, и об этом демон сам
    пишет карточку). Разница часов бодрствования между двумя наблюдениями сон исключает ПО
    УСТРОЙСТВУ, поэтому тишина копится наблюдение за наблюдением, а не берётся из mtime.

    ТРИ ЧЕСТНЫХ «НЕ ИЗМЕРЕНО», каждое ведёт к исходу «неизвестно», а не к «жив»:
      · часов бодрствования нет (не Windows / вызов не удался);
      · прошлого наблюдения нет — первый прогон наблюдателя или потерянное состояние;
      · часы пошли НАЗАД — машина перезагрузилась, и об интервале мы не знаем ничего.
    За одно наблюдение засчитывается не больше STEP_CAP_SEC: наблюдатель мог не работать час."""
    prev = state.get("turn") if isinstance(state.get("turn"), dict) else {}
    raw = str((hb or {}).get("raw") or "")
    ok = bool((hb or {}).get("ok"))
    cur = {"raw": raw, "awake": awake, "wall": now}
    if awake is None:
        state["turn"] = dict(cur, silent=None)
        return {"measured": False, "awake": None,
                "why": "часов бодрствования на этой машине нет — сон от молчания не отличить"}
    if not ok:
        # Факта нет: копить нечего и обнулять нечего. Прошлое состояние оставляем как было.
        state["turn"] = dict(prev, awake=awake, wall=now)
        return {"measured": False, "awake": None, "why": "heartbeat не прочитан"}
    prev_awake = prev.get("awake")
    prev_raw = prev.get("raw")
    try:
        prev_awake = None if prev_awake is None else float(prev_awake)
    except (TypeError, ValueError):
        prev_awake = None
    if prev_raw != raw or prev_awake is None or awake < prev_awake:
        why = ("оборот сменился — тишина обнулена" if prev_raw != raw and prev_raw is not None else
               "прошлого наблюдения нет" if prev_awake is None else
               "часы бодрствования пошли назад — машина перезагрузилась")
        state["turn"] = dict(cur, silent=0.0)
        return {"measured": prev_raw == raw and prev_awake is not None and awake >= prev_awake,
                "awake": 0.0, "why": why}
    try:
        silent = float(prev.get("silent") or 0.0)
    except (TypeError, ValueError):
        silent = 0.0
    silent += max(0.0, min(awake - prev_awake, STEP_CAP_SEC))
    state["turn"] = dict(cur, silent=silent)
    return {"measured": True, "awake": silent, "why": ""}


def update_mod_silence(state, mod, awake, now):
    """Накопить тишину СОБСТВЕННОГО тика модербота в часах бодрствования →
    {"measured","awake","since","why"}. Устройство то же, что у `update_silence`; отличие ровно
    одно, и оно — вторая половина замка против ложного зелёного.

    СЧЁТЧИК ОБНУЛЯЕТ ТОЛЬКО СВОЯ ЗАПИСЬ: mtime сменился И автор не опровергнут. Файл общий (в него
    пишут userbot черновиком и тренажёр сессией), поэтому «файл шевельнулся» обнулением быть не
    может: мёртвый модербот при живом клиентском трафике обнулялся бы вечно и выглядел здоровым
    ровно столько, сколько идёт трафик. Опровергнут — значит проба процесса ДОКАЗАЛА отсутствие
    (`who is False`); «не смогли проверить» обнуляет, потому что fail-safe этого слоя — молчание,
    а не заметка на пустом месте.

    ТРИ ЧЕСТНЫХ «НЕ ИЗМЕРЕНО», каждое ведёт к «неизвестно»: часов бодрствования нет · продукт не
    прочитан · прошлого наблюдения нет либо часы пошли назад (машина перезагрузилась)."""
    prev = state.get("mod") if isinstance(state.get("mod"), dict) else {}
    mtime = (mod or {}).get("mtime")
    who = ex.moderbot_writer({"moderbot": mod})
    cur = {"mtime": mtime, "awake": awake, "wall": now}
    since = prev.get("since") or now
    if awake is None:
        state["mod"] = dict(cur, silent=None, since=since)
        return {"measured": False, "awake": None, "since": since,
                "why": "часов бодрствования на этой машине нет — сон от молчания не отличить"}
    if not (mod or {}).get("ok"):
        state["mod"] = dict(prev, awake=awake, wall=now)
        return {"measured": False, "awake": None, "since": since,
                "why": "продукт модербота не прочитан"}
    prev_awake = prev.get("awake")
    try:
        prev_awake = None if prev_awake is None else float(prev_awake)
    except (TypeError, ValueError):
        prev_awake = None
    own_write = prev.get("mtime") != mtime and who is not False
    if own_write or prev_awake is None or awake < prev_awake:
        why = ("своя запись в IPC — тишина обнулена" if own_write else
               "прошлого наблюдения нет" if prev_awake is None else
               "часы бодрствования пошли назад — машина перезагрузилась")
        state["mod"] = dict(cur, silent=0.0, since=now)
        return {"measured": False, "awake": 0.0, "since": now, "why": why}
    try:
        silent = float(prev.get("silent") or 0.0)
    except (TypeError, ValueError):
        silent = 0.0
    silent += max(0.0, min(awake - prev_awake, STEP_CAP_SEC))
    state["mod"] = dict(cur, silent=silent, since=since)
    return {"measured": True, "awake": silent, "since": since, "why": ""}


def update_waits(state, facts, now):
    """Накопить ЧИСТОЕ ОЖИДАНИЕ каждой ждущей строки полосы ПК — время, простоянное ИМЕННО ПРИ
    СВОБОДНОЙ полосе. Именно по нему О1 берёт порог (обоснование — шапка expectations_pc).

    Полоса одноворкерная, и между двумя задачами всегда есть щель, в которую мгновенный снимок
    видит «свободно»: замер своей полосы даёт по возрасту строки 16 флагов за 20 суток при пороге
    30 мин и 35 при 15 мин — все ложные. Счётчик эти щели складывает и до порога не доводит.

    Снимка очереди нет → счётчики не трогаем ВООБЩЕ: молчание моста не есть простой полосы."""
    q = (facts or {}).get("queue") or {}
    if not q.get("ok"):
        return
    rows = [r for r in (q.get("rows") or []) if str(r.get("lane") or "").lower() == ex.LANE]
    busy = any(str(r.get("status") or "").lower() == "in_progress" for r in rows)
    prev = state.get("waits") or {}
    cur = {}
    for r in rows:
        if str(r.get("status") or "").lower() not in ex.WAIT_STATUSES:
            continue
        try:
            since = float(r.get("since") or 0)
        except (TypeError, ValueError):
            continue
        if since <= 0:
            continue
        key = "%s|%d" % (r.get("id"), int(since))          # тот же ключ, что у эпизода О1
        old = prev.get(key) or {}
        free = float(old.get("free") or 0.0)
        last = float(old.get("seen") or 0.0)
        if not busy and last > 0:
            free += max(0.0, min(now - last, STEP_CAP_SEC))
        cur[key] = {"free": free, "seen": now}
        r["free_wait"] = free
    state["waits"] = cur           # строки, ушедшие из ожидания, выпадают сами: состояние не растёт


def snapshot(state, now=None, getter=None):
    """ФАКТЫ и ни одного решения. Порогов здесь нет — их применяет expectations_pc.verdict()."""
    now = time.time() if now is None else float(now)
    hb = heartbeat_facts()
    mod = moderbot_facts()
    awake = awake_seconds()
    life = state.get("life") if isinstance(state.get("life"), dict) else {}
    return {
        "now": now,
        "queue": queue_facts(getter),
        "heartbeat": hb,
        "silence": update_silence(state, hb, awake, now),
        "busy": busy_facts(),
        "moderbot": mod,
        "mod_silence": update_mod_silence(state, mod, awake, now),
        # О4: когда полоса в последний раз оставила след наружу и когда МЫ в последний раз пытались
        # его оставить. Вторая половина — пол повтора: спавн писателя докладывает о старте, а не
        # об успехе, и без неё провал моста стучался бы в него каждые десять минут.
        "trace": trace_facts(attempt=life.get("attempt")),
    }


# ═══════════════════════════════════ МАРШРУТ РЕАКЦИИ ═══════════════════════════════════════
def send_note(text):
    """Заметка владельцу. Адрес выбирает ОДИН признак — ждёт ли сообщение ответа
    (`dispatch_notify.deliver`): заметка ответа не ждёт, значит идёт в тему постановки 328."""
    try:
        import dispatch_notify
        return bool(dispatch_notify.deliver(text))
    except Exception as e:                                            # noqa: BLE001
        print("заметка не ушла (%s)" % e, file=sys.stderr)
        return False


def send_pulse(line):
    """След жизни — строкой в журнал, ЕДИНСТВЕННОЙ легальной дорогой записи с ПК.

    Канал взят готовый (`dispatch_notify._cowork`) и по трём причинам, каждая — закрытый класс:
    секреты берёт САМ процесс писателя (наблюдатель их не видит и не читает); спавн отделённый и
    без окна (класс «мигающие чёрные окна» 22.07); строка проходит через `cowork_log_append`, то
    есть получает штамп, разбор типа и вынос длинного тела по `LINE_MAX`.

    → True = процесс записи ЗАПУЩЕН, а не «запись подтверждена». Подтверждает её следующий прогон
    тем же фактом, которым меряет тишину: отметкой в реестре следов. Поэтому ответ False здесь не
    трагедия — попытка записана, пол повтора соблюдён, а до серверного порога ещё 10 часов."""
    try:
        import dispatch_notify
        return bool(dispatch_notify._cowork(line))
    except Exception as e:                                            # noqa: BLE001
        print("пульс не ушёл (%s)" % e, file=sys.stderr)
        return False


def maybe_pulse(state, facts, cfg, now, pulser=None):
    """О4, руки: оставить наружу след жизни, если он ДОЛЖЕН быть оставлен. → (что вышло, почему).

    ОТЛИЧИЕ ОТ ЗАМЕТКИ, И ОНО СОЗНАТЕЛЬНОЕ. Незашедшая заметка НЕ помечается — эпизод остаётся
    открытым и на следующем прогоне повторяется. Здесь наоборот: попытка помечается ВСЕГДА, даже
    провальная. Причина — цена ошибки в другую сторону: заметка повторится через 10 минут и это
    правильно, а пульс, повторяемый каждые 10 минут при лежащем мосте, — долбёжка канала и мусор
    в журнале. Пол повтора в 30 минут оставляет до серверного порога ещё два десятка попыток."""
    due, info = ex.pulse_due(facts, cfg, now)
    if not due:
        return None, info.get("why", "")
    line = ex.render_pulse(info, LANE_LABEL)
    ok = bool((pulser or send_pulse)(line))
    state["life"] = {"attempt": now, "ok": ok, "silence": info.get("silence"),
                     "line": line[:160]}
    return ("ушёл" if ok else "не ушёл"), info.get("why", "")


def run(dry=False, now=None, getter=None, notifier=None, pulser=None):
    """Один прогон яруса 2. → словарь итога (для теста, лога и ручной проверки)."""
    now = time.time() if now is None else float(now)
    cfg = ex.config(os.environ)
    st = load_state()
    facts = snapshot(st, now, getter)
    update_waits(st, facts, now)
    verdicts = ex.verdict(facts, cfg)
    send = notifier or send_note
    open_eps = dict(st.get("open") or {})
    qstate = ex.queue_state(facts, cfg, now)[0]
    tstate, tinfo = ex.turn_state(facts, cfg, now)
    mstate, minfo = ex.moderbot_state(facts, cfg, now)
    out = {"verdicts": len(verdicts), "notes": [], "closed": [], "dry": bool(dry),
           "queue": qstate, "turn": tstate, "why": tinfo.get("why", ""),
           # У модербота состояние ходит ПАРОЙ со своей причиной: «неизвестно» без причины
           # читается как «плохо», а это разные новости.
           "moderbot": mstate, "mod_why": minfo.get("why", ""),
           # Чем именно объяснено молчание оборота, если объяснено: заход id=N. Владелец, читающий
           # статус, обязан видеть ПРИЧИНУ зелёного, а не только его цвет.
           "busy": (tinfo.get("busy") or {}).get("task")}

    # 1. ЗАКРЫТИЕ ЭПИЗОДОВ — первым: владелец обязан узнать, что кончилось, даже если сейчас
    #    открылось что-то новое.
    for key in ex.closures(facts, cfg, list(open_eps.keys())):
        if dry or send(ex.render_close(key, LANE_LABEL)):
            open_eps.pop(key, None)
            out["closed"].append(key)

    # 2. НАРУШЕНИЯ. Одна заметка на эпизод; повторов нет НАМЕРЕННО (это шум, а не настойчивость).
    for v in verdicts:
        key = str(v.get("key"))
        if key in open_eps:
            continue
        if dry:
            out["notes"].append(key)
            open_eps[key] = {"first": now, "kind": v.get("kind")}
            continue
        if not send(ex.render(v, LANE_LABEL)):
            continue                                   # не помечаем — скажем на следующем прогоне
        open_eps[key] = {"first": now, "kind": v.get("kind")}
        out["notes"].append(key)

    # 3. О4 — СЛЕД ЖИЗНИ НАРУЖУ. Не заметка и не вердикт: строка журнала для наблюдателя ВНЕ
    #    машины. Сухой прогон её не пишет — как и всё прочее, что уходит в канал.
    #    Сбой этой ветки гасится в НЕЁ САМУ и НАЗЫВАЕТСЯ: О4 заведена позже трёх остальных и не
    #    вправе стоить владельцу ни заметки О1–О3 (они уже ушли выше), ни сохранения счётчиков
    #    тишины (оно ниже). Молчаливого `pass` здесь нет — причина едет в итог прогона.
    out["life"], out["pulse"], out["pulse_why"] = ex.LIFE_UNKNOWN, None, ""
    try:
        lstate, linfo = ex.life_state(facts, cfg, now)
        out["life"] = lstate
        out["life_why"] = linfo.get("why", "")
        if dry:
            due, pinfo = ex.pulse_due(facts, cfg, now)
            out["pulse"] = "нужен (сухой прогон — не пишем)" if due else None
            out["pulse_why"] = pinfo.get("why", "")
        else:
            out["pulse"], out["pulse_why"] = maybe_pulse(st, facts, cfg, now, pulser)
    except Exception as e:                                            # noqa: BLE001
        out["pulse_why"] = "ветка О4 сорвалась (%s: %.60s)" % (type(e).__name__, e)

    if not dry:
        st["open"] = dict(list(open_eps.items())[-STATE_KEEP:])
        save_state(st)
    return out


def main():
    argv = sys.argv[1:]
    dry = "--dry" in argv or "--status" in argv
    try:
        out = run(dry=dry)
    except Exception as e:                                            # noqa: BLE001
        print("прогон не удался (%s) — вердикта нет" % e, file=sys.stderr)
        return 0                                       # молчание не считается сбоем наблюдателя
    if "--status" in argv:
        why = out["why"] or (("молчание оправдано объявленным заходом id=%s" % out["busy"])
                             if out.get("busy") else "")
        print("очередь ПК: %s · оборот демона: %s%s"
              % (out["queue"], out["turn"], (" (%s)" % why) if why else ""))
        print("модербот (работа, не жизнь): %s%s"
              % (out["moderbot"], (" (%s)" % out["mod_why"]) if out["mod_why"] else ""))
        # След жизни печатается ПАРОЙ «оборот доказан / что с пульсом»: без второй половины
        # «доказан» читалось бы как «наружу сказано», а это разные вещи.
        print("след жизни наружу (О4): %s · пульс: %s%s"
              % (out.get("life"), out.get("pulse") or "не нужен",
                 (" (%s)" % out.get("pulse_why")) if out.get("pulse_why") else ""))
        print("нарушений: %d %s" % (out["verdicts"], out["notes"]))
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
