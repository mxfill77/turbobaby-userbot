#!/usr/bin/env python3
"""РУКИ СЛОЯ ОЖИДАНИЙ ПОЛОСЫ ПК — ярус 2 (11.08.2026). Зеркало `expectations_run.py` сервера.

Решение живёт в `expectations_pc.py` (чистая функция «факты → вердикт», ни одного обращения к
миру). Здесь ровно руки: СОБРАТЬ факты, ОТНЕСТИ вердикт в канал, СОХРАНИТЬ состояние эпизодов.

ПОЧЕМУ ОТДЕЛЬНЫЙ ПРОЦЕСС, А НЕ ВЕТКА В ДЕМОНЕ (правило владельца «наблюдатель не живёт на том,
за чем следит»). Все сторожа очереди ПК — реапер одиночек `process_stuck_singles`, реапер сирот,
TTL подтверждения `process_approval_timeouts`, сторож застрявших цепей `process_stuck_chains` —
живут ВНУТРИ `poll_once`, то есть внутри того самого оборота, чьё отсутствие и есть предмет О2.
Демон, переставший крутиться, не судит себя ни одной из этих веток; наблюдатель обязан быть вне.

СЕМЬ ИСТОЧНИКОВ ФАКТОВ, КАЖДЫЙ ПЕРЕЖИВАЕТ СМЕРТЬ СВОЕГО ПРЕДМЕТА:
  очередь   — GET моста (`get_pending` по открытым статусам, полоса pc). ТОЛЬКО чтение;
  оборот    — файл `pc_orchestrator.heartbeat`, который демон пишет ПОСЛЕДНЕЙ строкой poll_once;
  занятость — `pc_orchestrator.task_started.json`: отметка CLAIM на диске (объявленный заход);
  модербот  — mtime `moderation_ipc.db` (ПРОДУКТ его пятисекундного тика) плюс проба процесса по
              `moderation_bot.lock`. База НЕ ОТКРЫВАЕТСЯ: `os.stat`, ни sqlite, ни запроса —
              наблюдатель не смеет соперничать с боевым писателем за замок файла;
  часы      — QueryUnbiasedInterruptTime: время БОДРСТВОВАНИЯ машины (сон в него не идёт);
  след      — О4: реестр `cowork_log.ledger` (отметка КАЖДОЙ строки, дошедшей до мозга) — тот
              самый поток, чей возраст меряет серверное О4. Только чтение хвоста файла;
  клиенты   — О5: ЧИСЛО ОТПРАВЛЕННЫХ клиенту из боевой `moderation_ipc.db` плюс реестр попыток
              `suggest_pairs.jsonl` (второй свидетель: reply-режим в базу не пишет вовсе). Базу
              ОТКРЫВАЕТ — но ТОЛЬКО режимом `mode=ro`, и не отсюда, а из `client_silence_pc`:
              `sqlite3` в этом файле запрещён инвариантом, и запрет цел (см. шапку глаза).

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
в боевой лог». Флаг живёт РОВНО ОДИН ИМПОРТ и снимается в `finally` (`_guard_test_logs`): тот же
флаг читает замок журнала владельца, и оставленный стоять он глушил СОБСТВЕННЫЙ пульс полосы —
12 суток немоты при живом писателе, `docs/artifacts/2026-09-01-pulse-silence-diagnosis.md`.

ГРАНИЦА: ожидание ТОЛЬКО НАБЛЮДАЕТ. Единственная реакция — заметка владельцу. Задач не ставит
(ветки нет вовсе — в отличие от серверного оригинала), процессов не перезапускает, очередь не
мутирует (ни claim, ни complete, ни heartbeat, ни enqueue), состояние демона не трогает: свои
файлы живут в ОТДЕЛЬНОМ каталоге `tmp/expect_pc/`.

ПРОТИВ read-and-ignore: одна заметка на эпизод и одна на его закрытие. Повторов-напоминаний НЕТ.

FAIL-SAFE: любой сбой сбора → факта нет → вердикта нет (молчание). Заметка не ушла → эпизод НЕ
помечен, скажем на следующем прогоне.

ПУБЛИКАЦИЯ ВЕРДИКТА О ДЕТЯХ (18.08.2026) НОВЫХ ФАКТОВ НЕ СОБИРАЕТ НИ ОДНОГО: она берёт готовый
вердикт О3 и кладёт его наружу ПЕРИОДИЧЕСКОЙ строкой журнала (`maybe_kids_pulse`), тем же каналом
и в той же форме, что пульс О4. Тревогой не является: карточки не рождает, владельцу сама по себе
не показывается — громкость про детей по-прежнему целиком за заметкой О3.

ОТКАТ: порог соответствующей ветки = 0 в окружении (EXPECT_PC_NEW_MIN / EXPECT_PC_RUN_MIN /
EXPECT_PC_TURN_MIN / EXPECT_PC_MOD_MIN / EXPECT_PC_LIFE_MIN / EXPECT_PC_CLIENT_MIN /
EXPECT_PC_KIDS_MIN) — ветка мертва
целиком (у О5 умирают ОБА исхода: и «ушло», и «ослеп»); полностью —
снять задачу Планировщика наблюдателя. У О4 откат стоит ЛИШНЕЙ строки в мозге раз в 6 часов и
возвращает серверное О4 к прежнему признаку «след работы», то есть к 4 ложным эпизодам за 11 суток.

ЗАПУСК:
    venv\\Scripts\\python.exe expectations_pc_run.py            # боевой прогон (заметки уходят)
    venv\\Scripts\\python.exe expectations_pc_run.py --dry      # решение и факты, канал не трогаем
    venv\\Scripts\\python.exe expectations_pc_run.py --status   # три состояния словами, без канала
"""
import calendar
import ctypes
import json
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import expectations_pc as ex                                          # noqa: E402
# ГЛАЗ О5 — ЕДИНСТВЕННЫЙ файл слоя, которому позволен `sqlite3`, и позволен он под своим
# инвариантом (только `mode=ro`, ни одного глагола записи в SQL). Здесь `sqlite3` по-прежнему НЕ
# импортируется — прежний замок рук цел байт в байт, см. шапку `client_silence_pc`.
import client_silence_pc as eye                                       # noqa: E402
# ГЛАЗ О6 — ТОТ ЖЕ ОБХОД, ЧТО ДЕРЖИТ ВОРОТА КЛИЕНТСКОГО КОНТУРА, и это НЕ совпадение, а требование:
# «замыкание считается тем же способом, каким оно считается сегодня для клиентского контура».
# Модуль чистый (ни git, ни subprocess, ни сети — только файлы репозитория), и наблюдателю он даёт
# ровно то, чего рукописная карта дать не может: список зависимостей, который не отстаёт.
import client_contour as contour                                      # noqa: E402

LANE_LABEL = "ПК"
HEARTBEAT_FILE = os.path.join(REPO, "pc_orchestrator.heartbeat")
TASK_START_FILE = os.path.join(REPO, "pc_orchestrator.task_started.json")
# О3: ОБЩИЙ канал (сведение) и лок. Сам ПРОДУКТ модербота живёт КЛЮЧОМ внутри базы — см.
# `moderbot_facts`: mtime этого файла двигают трое, и зелёного он не даёт никому (правка 18.08).
MOD_IPC_FILE = os.path.join(REPO, "moderation_ipc.db")
# ═══ ИМЯ ЛОКА — ОДИН ИСТОЧНИК НА ВЕСЬ СЛОЙ (05.09.2026) ══════════════════════════════════════
# До этой правки имя лока лежало литералом в ТРЁХ местах слоя (`MOD_LOCK_FILE`, `KID_FILES`,
# `CODE_LOCKS`), и `pc_agent.lock` был написан руками ДВАЖДЫ. Пока все копии совпадают, беды нет;
# расходятся они молча, и разошедшийся литерал звучит у владельца ровно как «лок не прочитан» —
# то есть как слепота прибора, а не как опечатка. Теперь имя пишется ОДИН раз здесь, а путь
# берётся `lock_path(имя)`; собирать его из кусков в другом месте больше негде.
# Локов на полосе ровно четыре, и они же — граница прибора: `rc_supervisor` лока не пишет вовсе.
LOCK_FILES = {
    "pc_orchestrator": "pc_orchestrator.lock",
    "pc_agent": "pc_agent.lock",
    "userbot": "userbot.lock",
    "moderation_bot": "moderation_bot.lock",
}


def lock_path(name):
    """Имя процесса → ПОЛНЫЙ путь его лока | None (такого процесса слой не наблюдает)."""
    fn = LOCK_FILES.get(name)
    return os.path.join(REPO, fn) if fn else None


# О3: ОБЩИЙ канал (сведение) и лок. Сам ПРОДУКТ модербота живёт КЛЮЧОМ внутри базы — см.
# `moderbot_facts`: mtime этого файла двигают трое, и зелёного он не даёт никому (правка 18.08).
MOD_LOCK_FILE = lock_path("moderation_bot")                   # О3: чей это продукт (номер процесса)
# СОБСТВЕННЫЙ ПРОДУКТ ДВУХ ОСТАЛЬНЫХ ДЕТЕЙ (18.08.2026). У каждого — СВОЙ файл и СВОЙ лок; общего
# источника на всех больше нет ни у кого. Периоды и обоснование пределов — `expectations_pc.KID_SIGNS`.
KID_FILES = {
    # Тик `_cowork_sync_job` каждые 30 с; файл личный, писать в него больше некому.
    "pc_agent": (os.path.join(REPO, "pc_agent.log"), lock_path("pc_agent")),
    # Keepalive Telethon раз в 60 с → `session.save()`. Продукт БИБЛИОТЕКИ, и это сказано вслух:
    # своих периодических строк у userbot нет вовсе.
    "userbot": (os.path.join(REPO, "turbobaby_session.session"), lock_path("userbot")),
}
# О4: реестр УСПЕШНО ушедших строк журнала (`cowork_log_append.ledger_add`) — ровно то, что
# серверное О4 видит как «след с ПК». Читаем ХВОСТ файла: кольцо на 500 строк, а нужна последняя.
LEDGER_FILE = os.path.join(REPO, "cowork_log.ledger")
LEDGER_TAIL_BYTES = 65536
# О6 (02.09.2026): ЧЕЙ ЛОК несёт номер процесса и момент его запуска. Имена не повторяются здесь
# ни разу — берутся из единственного источника `LOCK_FILES` (правка 05.09.2026): О3, дети и О6
# читают ОДИН И ТОТ ЖЕ файл по одному и тому же имени, и разойтись им больше негде.
CODE_LOCKS = {name: lock_path(name) for name in LOCK_FILES}
# КАК ЭТОТ ЖЕ ПРОЦЕСС ЗОВЁТСЯ В РУКОПИСНОЙ КАРТЕ ДЕМОНА (`_FILE_PROCESS_RULES`). Нужно ТОЛЬКО ради
# улики «карта знает N из M» — решение принимается вычислением и от этого словаря не зависит ни
# одной веткой. У демона имени в карте нет вовсе (`None`): он следит за собой сам, замыканием, и
# приписывать ему «карта знает 0» значило бы обвинять карту в том, чего она и не обязана уметь.
CODE_MAP_NAME = {"pc_orchestrator": None, "pc_agent": "pc_agent",
                 "userbot": "userbot", "moderation_bot": "moderbot"}
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


def _client_source():
    """Пути ОБОИХ свидетелей О5 → (база, реестр). Подмена только явная и только через окружение
    (`CC_EXPECT_PC_CLIENT_DB` / `CC_EXPECT_PC_CLIENT_PAIRS`) — тем же приёмом, что `CC_EXPECT_PC_DIR`:
    отрицательный тест обязан уметь показать прибору ТЕСТОВУЮ сущность, не касаясь боевой базы."""
    db = os.environ.get("CC_EXPECT_PC_CLIENT_DB", "").strip()
    pairs = os.environ.get("CC_EXPECT_PC_CLIENT_PAIRS", "").strip()
    return (eye.PROD_DB if db == "" else db), (eye.PROD_PAIRS if pairs == "" else pairs)


def client_facts():
    """ФАКТ О5: числа обоих свидетелей. Чтение и ничего кроме — база открывается режимом `mode=ro`
    внутри глаза, и ни одна ветка отсюда не умеет писать в неё даже теоретически."""
    db, pairs = _client_source()
    return eye.client_facts(db, pairs)


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


ERR_TAIL = 80          # сколько символов ПРИЧИНЫ берём в сообщение; имени это не касается


def _why_short(exc, path=None):
    """Отказ ФС → ПРИЧИНА словами, БЕЗ пути внутри. Режется только она.

    ПОЧЕМУ ПУТЬ ВЫРЕЗАЕТСЯ, А НЕ ОСТАВЛЯЕТСЯ ПОДЛИННЕЕ (живой дефект 05.09.2026). `str(OSError)`
    несёт имя файла ПОСЛЕДНИМ и через `repr`, а `repr` на Windows УДВАИВАЕТ каждый разделитель:
    «[Errno 2] No such file or directory: 'D:\\\\turbobaby-bot\\\\» — это уже 57 символов из
    прежнего среза в 60. От имени владельцу доставались ТРИ буквы, и сообщение звучало так:
    «лок pc_agent не прочитан: [Errno 2] No such file or directory: 'D:\\\\turbobaby-bot\\\\pc_».
    Обрезаны были ВСЕ ЧЕТЫРЕ имени полосы (mod… / pc_… / pc_… / use…), причём `pc_agent.lock` и
    `pc_orchestrator.lock` давали ОДИН И ТОТ ЖЕ огрызок «pc_» — по сообщению их не различить.

    Лечится не длиной среза, а порядком: длинный кусок (путь) выкидывается, имя файла зовущий
    называет САМ и ЦЕЛИКОМ до среза. Тогда имя любой длины переживает любой предел — тот самый
    следующий процесс с длинным именем, на котором «просто вписать правильное имя» сломалось бы."""
    why = str(exc)
    for form in ([repr(path), str(path)] if path else []):
        why = why.replace(form, "")
    why = why.strip().rstrip(":").strip()
    return why[:ERR_TAIL] or type(exc).__name__


def _read_lock(out, path, whose):
    """Дописать в факт номер из лока, время его правки и пробу процесса. Общий кусок всех троих:
    лок у каждого свой, а устройство одно — синглтон пишет туда СВОЙ PID и отбирает устаревший.

    ИМЯ ФАЙЛА В ОТКАЗЕ НАЗЫВАЕТСЯ ЦЕЛИКОМ И ДО СРЕЗА (правка 05.09.2026): оно берётся у пути
    одним куском (`os.path.basename`) — не собирается, не угадывается и не может быть обрезано
    пределом длины. Режется только ПРИЧИНА (`_why_short`), и её потеря диагноза не отнимает."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
        out["lock_mtime"] = os.stat(path).st_mtime
        # НОМЕР — ПЕРВОЙ СТРОКОЙ. С 30.08.2026 синглтоны полосы кладут в лок ещё и личность
        # владельца (имя запуска и момент старта) — второй строкой, JSON'ом. Наблюдателю она не
        # нужна: авторство он и так сверяет ДВУМЯ приметами (`kid_writer`) и был здесь образцом.
        # Но `int()` по ВСЕМУ файлу на таком локе бросил бы ValueError, и О3 ослеп бы молча —
        # поэтому берём ровно первую строку, ради чего первая строка голым номером и оставлена.
        out["pid"] = int(raw.splitlines()[0].strip())
    except (OSError, ValueError, IndexError) as e:   # IndexError — пустой лок: строк ноль
        out["err"] = ("%s; " % out["err"] if out["err"] else "") + \
                     "лок %s (файл %s) не прочитан: %s" % (whose, os.path.basename(path) or path,
                                                           _why_short(e, path))
        return out
    out["opened"], out["started"] = process_probe(out["pid"])
    return out


def own_facts(product, lock, whose):
    """СОБСТВЕННЫЙ ПРОДУКТ ребёнка-файла → {"ok","own","pid","opened","started","lock_mtime",
    "err","channel"}.

    `own` — время последней правки ЕГО СОБСТВЕННОГО файла (`os.stat`, файл не открываем). Имя поля
    не `mtime` НАМЕРЕННО: у модербота в этом же поле лежит время из КЛЮЧА, а не из файловой
    системы, и одно имя для двух разных величин рано или поздно родит ложный диагноз.

    `channel` здесь всегда None: общего канала у этих двоих нет вовсе — их признак личный, и
    `sha_same` тоже None — контрольную сумму снимает только тот, кто ОТКРЫВАЕТ базу."""
    out = {"ok": False, "own": None, "pid": None, "opened": None, "started": None,
           "lock_mtime": None, "err": "", "channel": None, "raw": None, "sha_same": None}
    try:
        out["own"] = os.stat(product).st_mtime
        out["ok"] = True
    except OSError as e:
        # ТОТ ЖЕ ПОРЯДОК, ЧТО У ЛОКА: имя файла целиком и до среза, режется только причина. У
        # `turbobaby_session.session` прежний срез в 80 символов съедал хвост имени ровно так же
        # (репр пути 83 символа) — один класс, одно лечение, одно место.
        out["err"] = "%s: продукт %s не прочитан: %s" % (type(e).__name__,
                                                         os.path.basename(product) or product,
                                                         _why_short(e, product))
        return _read_lock(out, lock, whose)      # продукта нет — но проба процесса всё ещё нужна
    return _read_lock(out, lock, whose)


def moderbot_facts(ipc=None, lock=None):
    """ПРОДУКТ модербота и проба процесса → {"ok","own","pid","opened","started","lock_mtime",
    "err","channel","raw"}.

    ЧТО ЗДЕСЬ ИЗМЕНИЛОСЬ 18.08.2026 И ПОЧЕМУ. Прежде продуктом считался mtime ОБЩЕГО файла
    `moderation_ipc.db`, и это было главной слепотой полосы: писателей у файла трое (модербот
    тиком 5 с, userbot поллером черновиков 3 с, тренажёр сессией), поэтому «файл шевельнулся»
    означает «кто-то писал». В живом случае 18.08 свежую запись оставил САМ ПОКОЙНИК перед
    смертью — прибор увидел свежесть и сказал «неизвестно» о заведомо мёртвом ребёнке.

    Теперь продукт — КЛЮЧ `meta['heartbeat']`, который пишет ровно одна строка кода
    (`moderation_ipc.heartbeat` ← `moderation_bot.job_heartbeat`), то есть подпись автора. Читает
    его ГЛАЗ (`client_silence_pc.moderbot_heartbeat`): единственный файл слоя с `sqlite3`, режим
    `mode=ro`, контрольная сумма до и после. Прежний запрет рукам импортировать `sqlite3` цел
    байт в байт — разрешение получено сужением замка, а не его ослаблением.

    mtime общего файла едет рядом полем `channel` — СВЕДЕНИЕМ. Ни одна ветка решения его не
    читает: зелёного он не даёт никому.

    Ключ не прочитан (базы нет, занята, ключа нет, время не разобрано) → `ok=False` с названной
    причиной, то есть «НЕИЗВЕСТНО» у решения, а не «работы нет»."""
    out = {"ok": False, "own": None, "pid": None, "opened": None, "started": None,
           "lock_mtime": None, "err": "", "channel": None, "raw": None, "sha_same": None}
    hb = eye.moderbot_heartbeat(ipc or MOD_IPC_FILE)
    out["channel"] = hb.get("channel")
    out["raw"] = hb.get("raw")
    out["sha_same"] = hb.get("sha_same")
    if hb.get("ok"):
        # Разбор ISO живёт в РЕШЕНИИ (`ex.parse_iso`) — он там уже есть, знает оба живых формата
        # («…Z» и «…+00:00») и уже проверен регрессом. Глаз отдаёт строку, руки её переводят.
        out["own"] = ex.parse_iso(hb.get("raw"))
        if out["own"] is None:
            out["err"] = "время ключа heartbeat не разобрано: %.40s" % (hb.get("raw") or "")
        else:
            out["ok"] = True
    else:
        out["err"] = hb.get("err") or "ключ heartbeat не прочитан"
    return _read_lock(out, lock or MOD_LOCK_FILE, "модербота")


def kid_facts(name):
    """Факт о СОБСТВЕННОМ продукте любого ребёнка по имени. Модербот ходит своей дорогой (ключ в
    базе), двое остальных — общей (mtime личного файла); имени в таблице нет → факта нет вовсе."""
    if name == ex.KIDS_JUDGED:
        return moderbot_facts()
    pair = KID_FILES.get(name)
    if pair is None:
        return None
    return own_facts(pair[0], pair[1], name)


# ════ О6: ЧТО ПРОЦЕСС ГРУЗИТ С ДИСКА — СЧИТАЕТСЯ ГРАФОМ, А НЕ БЕРЁТСЯ ИЗ СПИСКА ══════════════
def _map_targets():
    """Рукописная карта демона `_procs_for_file` → функция | None (взять не удалось).

    Берётся ТОЛЬКО РАДИ УЛИКИ: число «карта знает N файлов из M» едет в заметку, чтобы отставание
    карты было ВИДНО владельцу тем же сообщением. Ни одна ветка решения её не читает — не смогли
    получить, и вердикт не меняется ни на букву.

    Импорт демона тут не новый и не лишний: он уже происходит КАЖДЫЙ прогон ради боевого клиента
    моста (`_bridge`), модуль после первого раза лежит в кэше, и флаг `TURBOBABY_TEST_LOGS` живёт
    ровно один вызов — тот же замок, что у `_bridge`."""
    try:
        return _guard_test_logs(_daemon)._procs_for_file
    except Exception:                                                 # noqa: BLE001
        return None


def code_facts(closure_fn=None, stat_fn=None, lock_fn=None, map_fn=None, entries=None):
    """ФАКТ О6 по КАЖДОМУ наблюдаемому процессу → {имя: {...}}. Ни одного решения: пороги
    применяет `expectations_pc.code_state`.

    ДВА ЧИСЛА НА ПРОЦЕСС, и оба сняты ПО ФАКТУ, а не по списку имён:
      `newest`  — самая свежая mtime среди файлов ТРАНЗИТИВНОГО import-замыкания входной точки.
                  Замыкание считает `client_contour.closure(..., cut=())` — тот же обход ast, что
                  держит ворота клиентского контура, и тот же, которым демон следит за собой
                  (`pc_orchestrator._dep_files`). Срез на чужих процессах СНЯТ намеренно: он
                  отвечает на вопрос «увидит ли это клиент», а нам нужен другой — «что грузит в
                  память ЭТОТ процесс»;
      `started` — момент запуска процесса, снятый пробой ОС по номеру из лока (`_read_lock` →
                  `process_probe`, право СПРОСИТЬ, а не тронуть).

    FAIL-CLOSED ВЕЗДЕ И В СТОРОНУ ПОМЕТКИ, а не молчания (в этом ветка отличается от О1–О5, и это
    названо в шапке решения): граф не построился · файл замыкания не стат`уется · лока нет · проба
    не удалась → `ok=False` с ПРИЧИНОЙ, то есть «неизвестно» у решения, то есть пометка стои́т.
    Данные замыкания (`cl.data` — промпты и json-правила) не берём НАМЕРЕННО: их процесс читает с
    диска в рантайме, рестарта они не требуют, и считать их протуханием значило бы звать старым
    код, который на самом деле свежий."""
    out = {}
    getmap = map_fn if map_fn is not None else _map_targets()
    for name, entry in (entries if entries is not None else ex.CODE_ENTRIES):
        rec = {"ok": False, "entry": entry, "files": None, "newest": None, "newest_file": None,
               "reason": "", "gap": None, "mapped": None, "pid": None, "opened": None,
               "started": None, "lock_mtime": None, "err": "", "since": None}
        try:
            cl = (closure_fn or contour.closure)(REPO, entries=(entry,), cut=())
        except Exception as e:                                        # noqa: BLE001
            cl = None
            rec["reason"] = "обход сорвался: %s: %s" % (type(e).__name__, str(e)[:80])
        if cl is not None and not cl.ok:
            rec["reason"] = str(cl.reason or "причина не названа")
        elif cl is not None:
            files = sorted(cl.files)
            rec["files"] = len(files)
            newest, newest_file, missed = None, None, None
            mtimes = []
            for base in files:
                try:
                    m = float((stat_fn or os.stat)(os.path.join(REPO, base)).st_mtime)
                except (OSError, ValueError, TypeError) as e:
                    missed = "%s: %s" % (base, _why_short(e, os.path.join(REPO, base)))
                    break                     # один нестатуемый файл делает ответ недостоверным
                mtimes.append(m)
                if newest is None or m > newest:
                    newest, newest_file = m, base
            if missed:
                rec["reason"] = "файл замыкания не прочитан (%s)" % missed
            elif newest is None:
                rec["reason"] = "замыкание пустое — считать нечего"
            else:
                rec["ok"] = True
                rec["newest"], rec["newest_file"] = newest, newest_file
            # УЛИКА (не довод): сколько файлов замыкания рукописная карта относит к ЭТОМУ процессу.
            mapname = CODE_MAP_NAME.get(name)
            if getmap is not None and mapname:
                try:
                    known = sorted(f for f in files if mapname in getmap(f))
                    rec["mapped"] = len(known)
                    rec["gap"] = sorted(set(files) - set(known))
                except Exception:                                     # noqa: BLE001
                    pass
        lock = CODE_LOCKS.get(name)
        if not lock:
            rec["err"] = "лока у «%s» нет — момент запуска брать неоткуда" % name
        else:
            (lock_fn or _read_lock)(rec, lock, name)
        # С КАКОГО МОМЕНТА ПРОЦЕСС ОТСТАЁТ — ТРЕТЬЕ ЧИСЛО, И БЕЗ НЕГО ТРЕВОГА ГЛОХНЕТ НАВСЕГДА.
        # `newest` для срока ожидания НЕ ГОДИТСЯ: он едет вперёд с КАЖДЫМ коммитом в замыкание, а
        # в замыкании userbot 67 файлов и правки идут по нескольку раз в час. Ожидание, считанное
        # от него, обнулялось бы чужой правкой быстрее, чем истекал бы срок, и процесс, не
        # перезапускавшийся ДВОЕ СУТОК, вечно звучал бы спокойной новостью «подхватит ближайшим
        # витком» (замер 05.09: userbot жив с 03.09 06:02, отстал на 2 сут, `now - newest` = 6 мин).
        # Ровно это и есть «смягчение съело настоящий отказ», от которого предостерегает задание.
        # Предмет — САМАЯ РАННЯЯ правка, которую процесс ЕЩЁ НЕ ВЗЯЛ: она неподвижна, пока живёт
        # воплощение, и растёт ровно так, как растёт непрочитанное ожидание.
        started = rec.get("started")
        if mtimes and isinstance(started, (int, float)) and started > 0:
            behind_files = [m for m in mtimes if m > started]
            rec["since"] = min(behind_files) if behind_files else None
        out[name] = rec
    return out


SU_LOG_FILE = os.path.join(REPO, "pc_orchestrator.log")
SU_TAIL_BYTES = 262144        # хвост боевого лога, который читаем: ~сутки строк self-update
# ЧТО ИМЕННО ИЩЕМ В ХВОСТЕ. Три формы, все — ДОСЛОВНЫЕ строки самого демона (`maybe_self_update`
# и `_dirty_block`), а не наши пересказы. Порядок в кортеже — порядок разбора, но решает не он, а
# ВРЕМЯ строки: берётся ПОСЛЕДНЕЕ высказывание механизма, каким бы оно ни было.
SU_MARKS = (
    ("gate", "self-update: ", "-гейт ПРОВАЛЕН"),          # ...: unittest-гейт ПРОВАЛЕН (A→B): ...
    ("gate", "self-update: ", "code_gate ПРОВАЛЕН"),
    ("dirty", "self-update демона: ", "дерево ГРЯЗНОЕ"),  # ворота авто-рестарта отказали
    ("ok", "self-update: ", "гейт пройден"),              # эстафета передана — отказа НЕТ
)


def su_facts(path=None, tail=None):
    """ЧТО САМООБНОВЛЕНИЕ СКАЗАЛО О СЕБЕ ПОСЛЕДНИМ → факт для `ex.su_state`. Только чтение.

    Источник — БОЕВОЙ ЛОГ ДЕМОНА, и это осознанный выбор из двух возможных. Второй путь (демон
    кладёт штамп отказа на диск) требует правки наблюдаемого, а наблюдатель не вправе заводить себе
    удобства в чужом процессе; лог же демон пишет и так, строки эти живут в нём с заведения
    самообновления, и читаем мы их тем же правом, каким читаем heartbeat.

    ЧЕСТНАЯ ЦЕНА НАЗВАНА: разбор стои́т на ДОСЛОВНЫХ строках демона, и переписанная формулировка
    его лога сделает причину неназванной. Направление отказа при этом БЕЗОПАСНОЕ — не «в порядке»,
    а `kind=None`, то есть «отказа не было» → в решении это САМЫЙ ГРОМКИЙ из исходов тревоги
    («причина неизвестна, и это хуже названного отказа»). Сломавшийся разбор поэтому усиливает
    сигнал, а не глушит его; тест `test_the_parse_leans_to_the_loud_side` держит это свойство.

    `ok=False` — только когда файла нет или он не читается: слепота, и она тоже громкая."""
    out = {"ok": False, "kind": None, "at": None, "why": "", "what": "", "reason": ""}
    try:
        if tail is None:
            size = os.path.getsize(path or SU_LOG_FILE)
            with open(path or SU_LOG_FILE, "rb") as fh:
                if size > SU_TAIL_BYTES:
                    fh.seek(size - SU_TAIL_BYTES)
                tail = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError, TypeError) as e:
        out["reason"] = "%s: %s" % (type(e).__name__, str(e)[:80])
        return out
    out["ok"] = True
    last = None
    for raw in str(tail).splitlines():
        for kind, lead, mark in SU_MARKS:
            if lead in raw and mark in raw:
                last = (kind, raw.strip())
                break
    if last is None:
        # Хвост прочитан, а высказываний механизма в нём нет вовсе. Это не «в порядке»: молчащее
        # самообновление при живом расхождении и есть опасный случай, поэтому `kind` остаётся None.
        out["why"] = "в хвосте лога демона высказываний самообновления нет"
        return out
    kind, line = last
    if kind == "ok":
        # ПОСЛЕДНЕЕ, ЧТО СКАЗАЛ МЕХАНИЗМ, — «эстафета передана». Значит на СЕГОДНЯШНЕЕ расхождение
        # он не жаловался ни разу, и причина по-прежнему НЕ НАЗВАНА. Зелёным это не делает ничего.
        out["why"] = "последнее высказывание самообновления — успешная эстафета, на нынешнее " \
                     "расхождение оно не жаловалось"
        return out
    out["kind"] = kind
    out["at"] = _su_stamp(line)
    out["what"] = line[:300]
    out["why"] = ("гейт самообновления провален — новый код проверку не прошёл и в бой не поехал"
                  if kind == "gate" else
                  "рабочее дерево грязное: ворота авто-рестарта запретили обновление, потому что "
                  "коммит уехал бы в бой НЕ целиком")
    return out


def _su_stamp(line):
    """«2026-09-04 06:36:58,147 ...» → секунды | None. Времени нет → None, а не «сейчас».
    Через `time` (он уже импортирован), а не `datetime`: демон пишет лог МЕСТНЫМ временем, и
    `mktime` разбирает его тем же поясом, в котором оно записано."""
    try:
        return time.mktime(time.strptime(str(line)[:19], "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return None


# ═══ О7: ЧТО ЖУРНАЛ ПРОДУКТА СКАЗАЛ О ЦЕНЕ (только чтение, тем же правом, что и su_facts) ═══
#
# ГДЕ ЖИВЁТ ПРИБОР И КТО ЕГО ОБНУЛЯЕТ — сказано здесь, чтобы он не оказался в двух экземплярах:
#   ЧИТАЕТ ЖУРНАЛ     ровно одно место — эта функция, и только она (`grep price_facts`).
#   СУДИТ             ровно одно место — `expectations_pc.price_state` (чистая функция фактов).
#   ПАМЯТЬ ЭПИЗОДА    ровно один файл — `tmp/expect_pc/state.json`, ключ `open["o7p|price"]`.
#   КРУТИТ            ровно одна задача Планировщика — `TurboBabyExpectPC`, раз в 10 минут.
#   ОБНУЛЯЕТ          сам слой, и только доказанным пересчётом (`ex.closures` → `o7p`). Руками —
#                     владелец, сняв ключ из `state.json`; больше никто и ничем.
# ВТОРОГО ЭКЗЕМПЛЯРА НЕТ И БЫТЬ НЕ ДОЛЖНО. Сторож `price_gate` судит КАЖДЫЙ ответ и пишет свой
# вердикт в журнал — но владельцу он не говорит ничего и говорить не обязан. Наш прибор ЧИТАЕТ
# его след и не дублирует его решения ни одной строкой: порогов сторожа здесь нет вовсе.
PRICE_LOG_FILE = os.path.join(REPO, "logs", "userbot_stderr.log")
# Хвост журнала продукта. Замер 05.09: 5.07 МБ = 94.7 тыс. строк за 3.5 суток, то есть 4 МБ
# накрывают заведомо больше суток — а суточный пол повтора заметки и есть самое длинное окно,
# которое ветке нужно видеть целиком. Меньше брать нельзя: живая гасилка 05.09 лежала в 0.7 МБ
# от конца, но между ней и последним пересчётом — 2.0 тыс. строк.
PRICE_TAIL_BYTES = 4194304
# ДОСЛОВНЫЕ ФОРМЫ ОБЕИХ СТРОК, снятые с живого журнала, а не пересказанные:
#   гасилка  `suggest.py:3395` / `suggest.py:3886` — «price_gate: <карточка сторожа>»;
#   пересчёт `price_source.py:413` — «price_source: MODEL → … кепка: …».
# Слово «кепка:» и есть разделитель успеха от неудачи, и оно не украшение: у `price_source:` есть
# ПЯТЬ форм-неудач («источника нет», «цену гасим», «не той схемы» …), и считать их пересчётом
# значило бы закрывать эпизод тем самым следом, который его открывает. Замер по живому журналу:
# строк `price_source:` 519, из них с «кепка:» — 519, без — 0, то есть на сегодняшнем корпусе
# разделитель ничего не отсекает и введён ЗАРАНЕЕ, против пяти известных форм.
PRICE_GATE_MARK = "price_gate: "
PRICE_QUOTE_MARK = "price_source: "
PRICE_QUOTE_OK = "кепка:"
# Отметка времени в журнале продукта: «2026-09-05T09:23:25+00:00 | @кто | …». Своей отметки у
# ценовых строк НЕТ (их пишет logging без asctime), поэтому час берётся у ближайшей соседней
# строки — и эта неточность названа вслух и в факте (`exact`), и в заметке владельцу.
PRICE_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-]\d{2}:\d{2})")


def _price_stamp(line):
    """«2026-09-05T09:23:25+00:00 | …» → секунды эпохи | None. Зона читается ИЗ СТРОКИ, а не
    подразумевается: журнал пишет UTC, машина живёт в UTC+7, и местное чтение дало бы ошибку в
    семь часов — тот же класс, из-за которого `parse_iso` разбирает зону сам."""
    m = PRICE_STAMP_RE.match(str(line))
    if not m:
        return None
    try:
        base = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return None
    off = m.group(2)
    try:
        sign = -1 if off[0] == "-" else 1
        return float(base - sign * (int(off[1:3]) * 3600 + int(off[4:6]) * 60))
    except (TypeError, ValueError, IndexError):
        return None


def price_facts(path=None, tail=None):
    """ЧТО ЖУРНАЛ ПРОДУКТА СКАЗАЛ О ЦЕНЕ ПОСЛЕДНИМ → факт для `ex.price_state`. Только чтение.

    ПОРЯДОК СТРОК, А НЕ ИХ ВРЕМЯ, отвечает на вопрос «был ли пересчёт ПОСЛЕ гасилки»: журнал
    пишется последовательно одним процессом, и «ниже по файлу» здесь строго значит «позже».
    Время нужно только для ОДНОГО — назвать владельцу час начала молчания.

    Счёт ведётся ОТ ПОСЛЕДНЕГО ПЕРЕСЧЁТА: каждый успешный пересчёт обнуляет и `since`, и
    `gates`. Поэтому `since` — это всегда ПЕРВАЯ гасилка текущего молчания, а не последняя:
    владельцу нужен час, когда цена замолчала, а не час, когда её погасили в очередной раз.

    ТРИ ИСХОДА, как и у всех: файла нет / ошибка чтения → `ok=False` и решение обязано сказать
    «неизвестно». Пустой хвост без ценовых строк — `ok=True` с нулями, и «неизвестно» из него
    делает уже решение: это ПРОЧИТАННАЯ пустота, а не провал чтения."""
    p = path or PRICE_LOG_FILE
    out = {"ok": False, "since": None, "gate_why": None, "gates": 0, "quotes": 0,
           "last_ok": None, "exact": True, "path": p, "err": ""}
    try:
        size = os.path.getsize(p)
        with open(p, "rb") as fh:
            if size > (PRICE_TAIL_BYTES if tail is None else tail):
                fh.seek(-(PRICE_TAIL_BYTES if tail is None else tail), os.SEEK_END)
                fh.readline()                # огрызок первой строки выбрасываем целиком
            raw = fh.read()
    except (OSError, ValueError, TypeError) as e:
        out["err"] = "%s: %s" % (type(e).__name__, str(e)[:80])
        return out
    stamp = None                             # последняя ВИДЕННАЯ отметка времени
    first = None                             # самая ранняя отметка хвоста — запас для строк выше неё
    for chunk in raw.split(b"\n"):
        # Кодировка журнала не объявлена нигде и на живом файле смешанная: заменяем негодные
        # байты, а не отказываемся от строки. Потерять гасилку из-за одного кривого символа —
        # ровно тот молчаливый провал, ради которого ветка заведена.
        try:
            line = chunk.decode("utf-8")
        except UnicodeDecodeError:
            line = chunk.decode("cp1251", "replace")
        got = _price_stamp(line)
        if got is not None:
            stamp = got
            if first is None:
                first = got
            continue
        if line.startswith(PRICE_QUOTE_MARK):
            if PRICE_QUOTE_OK not in line:
                continue                     # одна из пяти форм-неудач: пересчётом она не является
            out["quotes"] += 1
            out["last_ok"] = stamp
            out["since"], out["gate_why"], out["gates"] = None, None, 0   # молчание оборвано
            out["exact"] = True
        elif line.startswith(PRICE_GATE_MARK):
            out["gates"] += 1
            if out["since"] is None:
                out["since"] = stamp
                out["exact"] = stamp is not None
                out["gate_why"] = line[len(PRICE_GATE_MARK):].strip()[:400]
    if out["gates"] and out["since"] is None:
        # Гасилка нашлась ВЫШЕ первой отметки времени хвоста: своего часа у неё нет и соседа
        # сверху тоже. Берём самую раннюю отметку хвоста как ВЕРХНЮЮ границу — она не позже
        # начала молчания, значит возраст будет ЗАНИЖЕН, а не завышен: прибор скорее промолчит
        # лишний тик, чем соврёт владельцу более ранним часом.
        out["since"], out["exact"] = first, False
    out["ok"] = True
    return out


def busy_facts(path=None):
    """Когда полоса в последний раз ОБЪЯВИЛА работу → {"ok","since","limit","err"}.

    ПРЕДМЕТ — ВРЕМЯ ПРАВКИ реестра отметок (`os.stat().st_mtime`), а НЕ поле внутри него. Тем же
    одним `os.stat` и на том же файле меряет занятость сторож самого демона
    (`pc_orchestrator._wd_busy_age`): два читателя ОДНОГО файла обязаны давать ОДИН ответ.

    ПОЧЕМУ НЕ ПОЛЕ `at` (живой провал 17.08.2026, четыре ложные тревоги подряд). Id строк очереди
    на этой полосе ПЕРЕИСПОЛЬЗУЮТСЯ, а `_task_started_mark` на уже знакомом ключе уходит в ранний
    возврат и `at` не обновляет вовсе. У идущей задачи в реестре поэтому лежит `at` её ПРОШЛОГО
    воплощения (замер: старше на 20–51 час), самой свежей отметкой остаётся давно закрытая задача,
    и прибор звал её «идущим заходом». Файл при этом ТРОНУТ: `_task_started_child` пишет PID
    ребёнка сразу после спавна headless. Разрыв двух прочтений одного файла — 3.70 ч на момент
    разбора и 14.83 ч сутки спустя.

    ЧТО ТЕРЯЕМ, СКАЗАНО ПРЯМО: имени задачи в этом факте нет и быть не может — mtime несёт время,
    а не номер строки. Прежний `task` брался из ключа реестра и на повторно выданном id называл
    ЧУЖУЮ задачу; лучше не называть никого, чем называть закрытую три часа назад.

    ТРИ ИСХОДА, не два. Реестра нет вовсе → `ok=True, since=None`: «объявлять нечего» это
    ПРОЧИТАННЫЙ факт, а не провал. Ошибка чтения → `ok=False`, и решение обязано сказать
    «неизвестно». Развилка ровно та же, что у `pc_orchestrator._task_started_read`:
    FileNotFoundError → пусто, любая другая ошибка → «реестр БЫЛ, но не прочитан»."""
    out = {"ok": False, "since": None, "limit": ex.TASK_TIMEOUT_SEC, "err": ""}
    try:
        out["since"] = float(os.stat(path or TASK_START_FILE).st_mtime)
        out["ok"] = True
    except FileNotFoundError:
        out["ok"] = True            # реестра нет — объявлять нечего, и это прочитано, а не провал
    except (OSError, ValueError, TypeError) as e:
        out["err"] = "%s: %s" % (type(e).__name__, str(e)[:80])
    return out


def _guard_test_logs(fn):
    """Выполнить `fn` под `TURBOBABY_TEST_LOGS=1` и вернуть окружение КАК БЫЛО. Зеркало уже
    написанного в этом же репо `queue_snapshot_pc._guard_test_logs`, и посылка у него ЗДЕСЬ ТА ЖЕ.

    Флаг нужен РОВНО НА ИМПОРТ демона, иначе наблюдатель вешает свой хендлер на БОЕВОЙ журнал
    того, за кем следит (закрытый класс «тесты сорят в боевой лог»). Но ТОТ ЖЕ флаг читает
    `log_setup.is_test_context`, а по нему `dispatch_notify._cowork` ОТКАЗЫВАЕТ строке в журнале
    владельца (замок 19.08.2026). Прежний `setdefault` без возврата метил наблюдателя тест-флагом
    НАВСЕГДА, и пульс глох на признаке, который сам же на себя и повесил: 12 суток (19.08→31.08)
    полоса не имела голоса наружу при живом писателе и живом журнале — диагноз и числа в
    `docs/artifacts/2026-09-01-pulse-silence-diagnosis.md`.

    ЗАМОК 19.08 ЭТИМ НЕ ОТКРЫТ: тест-прогон опознают ещё три НЕЗАВИСИМЫХ признака — `TESTING=1`,
    `PYTEST_CURRENT_TEST` и способ запуска процесса (`argv[0]` вида `test_*`, `unittest/__main__`,
    `pytest`), `log_setup.py:108-136`. Снятие УТЁКШЕГО флага у боевого наблюдателя не делает
    прогон тестов боевым ни одной веткой.

    Возврат стои́т в `finally` СОЗНАТЕЛЬНО: падение импорта не смеет оставить флаг стоять."""
    key = "TURBOBABY_TEST_LOGS"
    had, prev = key in os.environ, os.environ.get(key)
    os.environ[key] = "1"
    try:
        return fn()
    finally:
        if had:
            os.environ[key] = prev
        else:
            del os.environ[key]


def _daemon():
    import pc_orchestrator as o                                       # noqa: E402
    return o


def _bridge():
    """Боевой клиент моста. Импорт демона ТОЛЬКО ради него и ТОЛЬКО с уводом логов в temp:
    иначе наблюдатель пишет в боевой журнал того, за кем следит. Флаг живёт ровно один импорт —
    см. `_guard_test_logs`, там же цена прежнего «навсегда»."""
    return _guard_test_logs(_daemon).bc


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


def kid_slot(name):
    """Имя ячейки состояния, в которой копится тишина ребёнка. У модербота она называется `mod`
    с 11.08 и переименованию не подлежит: переименовать значит потерять накопленное на первом же
    прогоне и обнулить открытый эпизод О3."""
    return "mod" if name == ex.KIDS_JUDGED else "kid_%s" % name


def update_kid_silence(state, name, fact, awake, now):
    """Накопить тишину СОБСТВЕННОГО продукта ребёнка в часах бодрствования →
    {"measured","awake","since","why"}. Устройство то же, что у `update_silence`; отличий два.

    ПЕРВОЕ: СЧЁТЧИК ОБНУЛЯЕТ ТОЛЬКО СВОЯ ЗАПИСЬ — штамп сменился И процесс из лока не опровергнут.
    Опровергнут значит проба ДОКАЗАЛА отсутствие (`who is False`); «не смогли проверить» обнуляет,
    потому что fail-safe этого слоя — молчание, а не заметка на пустом месте.

    ВТОРОЕ (18.08.2026): ШТАМП ТЕПЕРЬ У КАЖДОГО СВОЙ. У модербота это время из ключа
    `meta['heartbeat']`, у pc_agent и userbot — mtime их ЛИЧНЫХ файлов. Прежде здесь стоял mtime
    ОБЩЕЙ базы, и «файл шевельнулся» обнуляло тишину модербота при живом клиентском трафике —
    мёртвый бот выглядел здоровым ровно столько, сколько шёл трафик соседа.

    ТРИ ЧЕСТНЫХ «НЕ ИЗМЕРЕНО», каждое ведёт к «неизвестно»: часов бодрствования нет · продукт не
    прочитан · прошлого наблюдения нет либо часы пошли назад (машина перезагрузилась)."""
    slot = kid_slot(name)
    prev = state.get(slot) if isinstance(state.get(slot), dict) else {}
    stamp = (fact or {}).get("own")
    who = ex.kid_writer(fact)
    cur = {"own": stamp, "awake": awake, "wall": now}
    since = prev.get("since") or now
    if awake is None:
        state[slot] = dict(cur, silent=None, since=since)
        return {"measured": False, "awake": None, "since": since,
                "why": "часов бодрствования на этой машине нет — сон от молчания не отличить"}
    if not (fact or {}).get("ok"):
        state[slot] = dict(prev, awake=awake, wall=now)
        return {"measured": False, "awake": None, "since": since,
                "why": "продукт %s не прочитан" % name}
    prev_awake = prev.get("awake")
    try:
        prev_awake = None if prev_awake is None else float(prev_awake)
    except (TypeError, ValueError):
        prev_awake = None
    own_write = prev.get("own") != stamp and who is not False
    if own_write or prev_awake is None or awake < prev_awake:
        why = ("своя запись — тишина обнулена" if own_write else
               "прошлого наблюдения нет" if prev_awake is None else
               "часы бодрствования пошли назад — машина перезагрузилась")
        state[slot] = dict(cur, silent=0.0, since=now)
        return {"measured": False, "awake": 0.0, "since": now, "why": why}
    try:
        silent = float(prev.get("silent") or 0.0)
    except (TypeError, ValueError):
        silent = 0.0
    silent += max(0.0, min(awake - prev_awake, STEP_CAP_SEC))
    state[slot] = dict(cur, silent=silent, since=since)
    return {"measured": True, "awake": silent, "since": since, "why": ""}


def update_mod_silence(state, mod, awake, now):
    """Тишина модербота — тот же накопитель по имени. Имя оставлено прежним: под ним ветка О3
    живёт с 11.08 и на него смотрит регресс."""
    return update_kid_silence(state, ex.KIDS_JUDGED, mod, awake, now)


def update_client_unknown(state, client, awake, now):
    """Накопить НЕЗНАНИЕ О5 — время, в течение которого источник истины НЕ ЧИТАЕТСЯ →
    {"measured","awake","since","why"}. Устройство то же, что у `update_silence`/`update_mod_silence`,
    но копится ОБРАТНОЕ: там тишина ПРИ читаемом факте, здесь — отсутствие самого факта.

    ЗАЧЕМ СЧЁТЧИК, А НЕ ОДНО НАБЛЮДЕНИЕ. Единственный промах (база занята, машина просыпается,
    диск занят) заметки не стои́т, а шесть подряд — стоят. И почему часы БОДРСТВОВАНИЯ: ПК спит, и
    стенной возраст первого неудачного чтения после сна равен сну — заметка «прибор ослеп на 9
    часов» была бы ложной ровно на длину сна.

    ЧИТАЕМЫЙ ИСТОЧНИК ОБНУЛЯЕТ СЧЁТЧИК БЕЗУСЛОВНО и безразлично к тому, ЧТО он показал: предмет
    этой функции — слепота прибора, а не поведение бота. Прочитанная отправка слепотой не является
    и судится другой ветвью."""
    prev = state.get("client") if isinstance(state.get("client"), dict) else {}
    readable = client.get("ok") is True if isinstance(client, dict) else False
    since = prev.get("since")
    if since is None:
        since = now
    cur = {"awake": awake, "wall": now}
    if readable:
        state["client"] = dict(cur, silent=0.0, since=None)
        return {"measured": True, "awake": 0.0, "since": None,
                "why": "источник истины прочитан — незнания нет"}
    if awake is None:
        state["client"] = dict(cur, silent=None, since=since)
        return {"measured": False, "awake": None, "since": since,
                "why": "часов бодрствования на этой машине нет — сон от слепоты не отличить"}
    prev_awake = prev.get("awake")
    try:
        prev_awake = None if prev_awake is None else float(prev_awake)
    except (TypeError, ValueError):
        prev_awake = None
    if prev_awake is None or awake < prev_awake:
        why = ("прошлого наблюдения нет" if prev_awake is None else
               "часы бодрствования пошли назад — машина перезагрузилась")
        state["client"] = dict(cur, silent=0.0, since=now)
        return {"measured": False, "awake": 0.0, "since": now, "why": why}
    prev_silent = prev.get("silent")
    try:
        silent = 0.0 if prev_silent is None else float(prev_silent)
    except (TypeError, ValueError):
        silent = 0.0
    silent += max(0.0, min(awake - prev_awake, STEP_CAP_SEC))
    state["client"] = dict(cur, silent=silent, since=since)
    return {"measured": True, "awake": silent, "since": since, "why": ""}


def update_queue_blind(state, q, awake, now):
    """Накопить СЛЕПОТУ О1 и посчитать ЭПИЗОДЫ → {"measured","awake","since","why","episodes",
    "misses"}. Устройство — то же, что у `update_client_unknown`; предмет другой: там не читается
    источник истины о клиентах, здесь не получается СНИМОК ОЧЕРЕДИ.

    СЧЁТ ИДЁТ ВСЕГДА И С ПЕРВОГО ПРОМАХА, а голос — только после отсрочки (`ex._o1_blind`). Это
    и есть требуемое разделение громкости и вердикта: эпизод, погасший быстрее отсрочки, в счёт
    попадает, а владельцу не показывается. Обратный порядок (показывать всё, что считаем) дал бы
    по замеру 15.09 шестнадцать сообщений в сутки — отказ приёмки, а не бдительность.

    ЧАСЫ БОДРСТВОВАНИЯ, а не стенные, по той же причине, что у О2/О3/О5: ПК спит, и первый же
    промах после сна имеет стенной возраст, равный сну, — заметка «не вижу очереди 9 часов» была
    бы ложной ровно на длину сна.

    ПОЛУЧЕННЫЙ СНИМОК ОБНУЛЯЕТ СЧЁТЧИК БЕЗУСЛОВНО и безразлично к тому, ЧТО он показал: предмет
    здесь — способность смотреть, а не увиденное. Счётчик ЭПИЗОДОВ при этом не обнуляется
    никогда: он память полосы о том, сколько раз она слепла, и ноль в нём значит «не слепла»."""
    prev = state.get("qblind") if isinstance(state.get("qblind"), dict) else {}
    readable = bool(q.get("ok")) if isinstance(q, dict) else False
    episodes = int(prev.get("episodes") or 0)
    misses = int(prev.get("misses") or 0)
    since = prev.get("since")
    why = str((q or {}).get("err") or "") if isinstance(q, dict) else ""
    if readable:
        state["qblind"] = {"wall": now, "awake": awake, "blind": 0.0, "since": None,
                           "why": "", "episodes": episodes, "misses": 0}
        return {"measured": True, "awake": 0.0, "since": None, "episodes": episodes,
                "misses": 0, "why": "снимок очереди получен — слепоты нет"}
    # ЭПИЗОД ОТКРЫВАЕТСЯ ЗДЕСЬ, ДО ЛЮБОГО ПОРОГА И ДО ЛЮБЫХ ЧАСОВ. Даже когда часов бодрствования
    # нет вовсе и длину мы посчитать не сможем, ФАКТ слепоты посчитан — «не смог измерить» не
    # смеет превратиться в «не было».
    misses += 1
    if since is None:
        since, episodes = now, episodes + 1
    cur = {"wall": now, "awake": awake, "episodes": episodes, "misses": misses,
           "why": why[:160]}
    if awake is None:
        state["qblind"] = dict(cur, blind=None, since=since)
        return {"measured": False, "awake": None, "since": since, "episodes": episodes,
                "misses": misses,
                "why": why or "часов бодрствования нет — сон от слепоты не отличить"}
    prev_awake = prev.get("awake")
    try:
        prev_awake = None if prev_awake is None else float(prev_awake)
    except (TypeError, ValueError):
        prev_awake = None
    if prev_awake is None or awake < prev_awake:
        # Первое наблюдение или перезагрузка машины: длину эпизода мерить не от чего. Эпизод
        # ПОСЧИТАН (выше), но НЕ ИЗМЕРЕН — и это разные слова.
        state["qblind"] = dict(cur, blind=0.0, since=now)
        return {"measured": False, "awake": 0.0, "since": now, "episodes": episodes,
                "misses": misses,
                "why": why or ("прошлого наблюдения нет" if prev_awake is None
                               else "часы бодрствования пошли назад — машина перезагрузилась")}
    prev_blind = prev.get("blind")
    try:
        blind = 0.0 if prev_blind is None else float(prev_blind)
    except (TypeError, ValueError):
        blind = 0.0
    blind += max(0.0, min(awake - prev_awake, STEP_CAP_SEC))
    state["qblind"] = dict(cur, blind=blind, since=since)
    return {"measured": True, "awake": blind, "since": since, "episodes": episodes,
            "misses": misses, "why": why or "причина не названа"}


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


# ═══════════ ГЛАЗ О8: ШТАМП ЧАСОВ ЗАХВАТА ПЕРЕПИСКИ (22.09.2026) ═══════════════════════════
# ПУТЬ ДЕРЖИТСЯ КОПИЕЙ, а не импортом `chatlog_ingest`: наблюдатель не импортирует наблюдаемого —
# иначе поломка захвата (синтаксис, битый импорт, отсутствующий модуль) роняла бы прибор О8 ровно
# в ту минуту, ради которой он заведён, и молчание выглядело бы благополучием. Копия закреплена
# тестом, который читает исходник `chatlog_ingest.py` ТЕКСТОМ: съехавший путь обязан покраснеть.
CHATLOG_TICK_STATE = os.path.join(REPO, "tmp", "chatlog_tick", "state.json")


def chatlog_facts(path=None):
    """ШТАМП ЧАСОВ ЗАХВАТА → факт для `ex.chatlog_state`. ТОЛЬКО ЧТЕНИЕ одного json.

    ПЕРЕПИСКИ ЭТОТ ГЛАЗ НЕ ВИДИТ НИ ОДНОЙ СТРОКОЙ, и это не осторожность, а устройство: он
    открывает штамп часов (пять чисел), а не архив. Ни `chatlog/`, ни `userbot.log`, ни
    `dispatch_notify.log` здесь не открываются ни одной веткой.

    `own` — ПОДПИСЬ ЗАХОДА для накопителя тишины: пара `(runs, ran_at)`. Пара, а не одно число:
    `runs` отвечает «сколько заходов было», `ran_at` — «когда объявлен последний», и порознь обе
    врут. Счётчик, сброшенный руками в ноль, оставил бы `runs` неподвижным при живых заходах;
    `ran_at`, уехавший назад переводом часов, выглядел бы новым заходом.

    ТРИ ИСХОДА: файла нет / json битый / не словарь → `ok=False` с ИМЕНЕМ КЛАССА ошибки, и
    решение обязано сказать «неизвестно», а не «захода нет»."""
    p = path or CHATLOG_TICK_STATE
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
    except Exception as e:                                            # noqa: BLE001
        return {"ok": False, "err": type(e).__name__, "src": p, "own": None}
    if not isinstance(st, dict):
        return {"ok": False, "err": "штамп не словарь", "src": p, "own": None}
    return {"ok": True, "err": "", "src": p,
            "ran_at": st.get("ran_at"), "runs": st.get("runs"),
            "written": st.get("written"), "total_written": st.get("total_written"),
            "last_error": st.get("last_error"), "errors": st.get("errors"),
            "own": "%s|%s" % (st.get("runs"), st.get("ran_at"))}


def update_chatlog_silence(state, fact, awake, now):
    """Накопить ТИШИНУ ЧАСОВ ЗАХВАТА в часах бодрствования → {"measured","awake","since","why"}.

    Устройство то же, что у `update_silence`/`update_kid_silence`, и по той же причине: `due`
    внутри захвата считает по СТЕННЫМ часам, поэтому после сна машины заход законно происходит
    первым же витком — а стенной разрыв при этом равен порогу плюс весь сон. Мерить такой разрыв
    стенными часами значит звать сон пропущенным тиком.

    ОБНУЛЯЕТ ТОЛЬКО СМЕНА ПОДПИСИ ЗАХОДА (`own`), то есть ЗАПИСЬ, сделанная самим тиком. Ни
    чтение файла, ни его mtime, ни живой процесс счётчик не трогают: именно этим прибор отличается
    от «файл на месте — значит работает».

    ТРИ ЧЕСТНЫХ «НЕ ИЗМЕРЕНО»: часов бодрствования нет · штамп не прочитан · прошлого наблюдения
    нет либо часы пошли назад (машина перезагрузилась)."""
    prev = state.get("chatlog") if isinstance(state.get("chatlog"), dict) else {}
    stamp = (fact or {}).get("own")
    cur = {"own": stamp, "awake": awake, "wall": now}
    since = prev.get("since") or now
    if awake is None:
        state["chatlog"] = dict(cur, silent=None, since=since)
        return {"measured": False, "awake": None, "since": since,
                "why": "часов бодрствования на этой машине нет — сон от молчания не отличить"}
    if not (fact or {}).get("ok"):
        # ШТАМП НЕ ПРОЧИТАН — ПРОШЛОЕ НАБЛЮДЕНИЕ НЕ ТРОГАЕМ: накопленное не теряется, но и не
        # растёт. Промах чтения не является ни заходом, ни его отсутствием.
        state["chatlog"] = dict(prev, awake=awake, wall=now)
        return {"measured": False, "awake": None, "since": since,
                "why": "штамп часов захвата не прочитан"}
    prev_awake = prev.get("awake")
    try:
        prev_awake = None if prev_awake is None else float(prev_awake)
    except (TypeError, ValueError):
        prev_awake = None
    moved = prev.get("own") != stamp
    if moved or prev_awake is None or awake < prev_awake:
        why = ("заход состоялся — тишина обнулена" if moved else
               "прошлого наблюдения нет" if prev_awake is None else
               "часы бодрствования пошли назад — машина перезагрузилась")
        state["chatlog"] = dict(cur, silent=0.0, since=now)
        return {"measured": False, "awake": 0.0, "since": now, "why": why}
    try:
        silent = float(prev.get("silent") or 0.0)
    except (TypeError, ValueError):
        silent = 0.0
    silent += max(0.0, min(awake - prev_awake, STEP_CAP_SEC))
    state["chatlog"] = dict(cur, silent=silent, since=since)
    return {"measured": True, "awake": silent, "since": since, "why": ""}


def snapshot(state, now=None, getter=None):
    """ФАКТЫ и ни одного решения. Порогов здесь нет — их применяет expectations_pc.verdict()."""
    now = time.time() if now is None else float(now)
    hb = heartbeat_facts()
    mod = moderbot_facts()
    awake = awake_seconds()
    life = state.get("life") if isinstance(state.get("life"), dict) else {}
    life_kids = state.get("kids") if isinstance(state.get("kids"), dict) else {}
    client = client_facts()
    # СНИМОК ОЧЕРЕДИ СНИМАЕТСЯ ЗДЕСЬ, А НЕ В ЛИТЕРАЛЕ НИЖЕ, потому что его исход нужен ДВАЖДЫ:
    # сам снимок и счётчик слепоты О1, который обязан обновиться ТЕМ ЖЕ наблюдением, а не задним
    # числом. Второго вызова моста это не добавляет ни одного — вызов ровно один, как и был.
    queue = queue_facts(getter)
    chat = chatlog_facts()
    mod_silence = update_mod_silence(state, mod, awake, now)
    # КАЖДЫЙ РЕБЁНОК — СО СВОИМ ИСТОЧНИКОМ И СВОИМ СЧЁТЧИКОМ ТИШИНЫ (18.08.2026). Модербот кладётся
    # сюда ТЕМИ ЖЕ объектами, что и в свои прежние ключи, — не копией: один ребёнок обязан иметь
    # один вердикт, и заметка О3 со строкой журнала не смеют разойтись в словах ни на одном тике.
    kids = {ex.KIDS_JUDGED: {"fact": mod, "silence": mod_silence}}
    for name in ex.KIDS:
        if name in kids:
            continue
        fact = kid_facts(name)
        kids[name] = {"fact": fact, "silence": update_kid_silence(state, name, fact, awake, now)}
    return {
        "now": now,
        "queue": queue,
        # О1, ТРЕТИЙ ИСХОД: накопленная СЛЕПОТА и счёт её эпизодов. Порядок тот же, что у О5:
        # счётчик обновляется ПОСЛЕ снятия снимка, тем же наблюдением.
        "queue_blind": update_queue_blind(state, queue, awake, now),
        "heartbeat": hb,
        "silence": update_silence(state, hb, awake, now),
        "busy": busy_facts(),
        "moderbot": mod,
        "mod_silence": mod_silence,
        "kids": kids,
        # О4: когда полоса в последний раз оставила след наружу и когда МЫ в последний раз пытались
        # его оставить. Вторая половина — пол повтора: спавн писателя докладывает о старте, а не
        # об успехе, и без неё провал моста стучался бы в него каждые десять минут.
        "trace": trace_facts(attempt=life.get("attempt")),
        # О5: числа обоих свидетелей и накопленное НЕЗНАНИЕ. Порядок важен — счётчик слепоты
        # обновляется ПОСЛЕ чтения, тем же наблюдением, а не задним числом.
        "client": client,
        "client_unknown": update_client_unknown(state, client, awake, now),
        # ПУБЛИКАЦИЯ О ДЕТЯХ: когда мы в последний раз говорили о них наружу и ЧТО именно сказали.
        # Подпись нужна не для красоты — она отличает «ждём периода» от «состояние сменилось».
        # В отличие от О4 своего следа в реестре у этой строки не опознать: там она неотличима от
        # любой другой, поэтому счётчик свой.
        "kids_last": {"attempt": life_kids.get("attempt"), "sig": life_kids.get("sig")},
        # О6: ЧТО КАЖДЫЙ ПРОЦЕСС ГРУЗИТ С ДИСКА и когда он запущен. Счётчика тишины здесь НЕТ и
        # быть не должно: расхождение «диск новее памяти» — это МГНОВЕННОЕ состояние двух чисел, а
        # не накопленное молчание, и сон машины его не искажает (обе величины — стенные метки).
        "code": code_facts(),
        # О6, вторая ось: ПОЧЕМУ обновления нет. Факт читается КАЖДЫЙ прогон, но спрашивают его
        # только у просроченного расхождения (`ex._o6`): у процесса, чей срок ещё не вышел, вопрос
        # «почему не обновился» сам по себе звучит обвинением — тем самым голосом, от которого
        # ветку и лечим 05.09.2026.
        "su": su_facts(),
        # О7: ЧТО ЖУРНАЛ ПРОДУКТА СКАЗАЛ О ЦЕНЕ. Счётчика тишины здесь НЕТ и быть не должно —
        # предмет ветки не «сколько мы молчали», а «что записано в журнале»: начало молчания
        # лежит В САМОМ ЖУРНАЛЕ стенной отметкой и переживает и сон машины, и перезапуск
        # наблюдателя. Накопленный счётчик, как у О2/О3, здесь только терял бы эпизод при каждом
        # рестарте задачи Планировщика.
        "price": price_facts(),
        # О8: ШТАМП ЧАСОВ ЗАХВАТА ПЕРЕПИСКИ и накопленная тишина между заходами. Счётчик нужен
        # ровно по той же причине, что у О2: `chatlog_ingest.due` считает стенными часами, и
        # после сна машины законный заход даёт стенной разрыв «порог + весь сон». Порядок тот же,
        # что у О5: счётчик обновляется ПОСЛЕ чтения, ТЕМ ЖЕ наблюдением, а не задним числом.
        "chatlog": chat,
        "chatlog_silence": update_chatlog_silence(state, chat, awake, now),
    }


# ═══════════════════════════════════ МАРШРУТ РЕАКЦИИ ═══════════════════════════════════════
def send_note(text):
    """Заметка владельцу. Адрес выбирает ОДИН признак — ждёт ли сообщение ответа
    (`dispatch_notify.deliver`): заметка ответа не ждёт, значит идёт в тему постановки 328.

    → True ТОЛЬКО при подтверждённой отправке. `deliver` возвращает ПАРУ `(channel, ok)`, и
    разбирать её обязательно: `bool(("none", False))` — это `True`, потому что непустой кортеж
    истинен всегда. С 06.09.2026 пара разбирается так же, как в шести остальных вызовах
    (`dispatch_notify.py:921,946,957,974,1007,1021`); до правки лежащий канал возвращал сюда
    ложный успех, и `run()` помечал эпизод сказанным при ненаписанной заметке — заметка О1–О6
    после этого не повторялась НИКОГДА, О7 молчала до пола повтора (сутки).

    Форма ответа канала, отличная от пары, — тоже НЕ успех: распаковка сорвётся, и ветка ниже
    вернёт False. Это верная сторона ошибки: непомеченный эпизод скажется на следующем прогоне,
    а ложное «сказано» не чинится ничем."""
    try:
        import dispatch_notify
        channel, ok = dispatch_notify.deliver(text)
        if not ok:
            print("заметка не ушла (канал %s)" % channel, file=sys.stderr)
        return bool(ok)
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


def maybe_kids_pulse(state, facts, cfg, now, pulser=None):
    """ПУБЛИКАЦИЯ ВЕРДИКТА О ДЕТЯХ, руки: положить наружу строку о каждом ребёнке ПОИМЁННО, если
    ей пора. → (что вышло, почему).

    ГРОМКОСТЬ ОТДЕЛЕНА ОТ ВЕРДИКТА, И ЭТО ГЛАВНОЕ В ВЫБОРЕ КАНАЛА. Строка идёт тем же путём, что
    пульс О4 (`send_pulse` → `dispatch_notify._cowork`), то есть в ЖУРНАЛ и только в журнал:
    карточки она не рождает, владельцу сама по себе не показывается и адресована наблюдателю ВНЕ
    машины. Громкость про детей живёт отдельно и давно: нарушение О3 уходит ЗАМЕТКОЙ
    (`send_note`, тема 328) через 15 минут — эта ветка её не заменяет, не дублирует и не глушит.

    Попытка помечается ВСЕГДА, даже провальная, — по той же причине, что у О4: строка,
    повторяемая каждые десять минут при лежащем мосте, есть долбёжка канала, а не настойчивость.
    Подпись состояния запоминается вместе с попыткой: по ней следующий прогон отличит «ничего не
    изменилось, ждём периода» от «ребёнок сменил состояние, говорим сейчас»."""
    due, info = ex.kids_pulse_due(facts, cfg, now)
    if not due:
        return None, info.get("why", "")
    line = ex.render_kids(info, LANE_LABEL)
    ok = bool((pulser or send_pulse)(line))
    state["kids"] = {"attempt": now, "ok": ok, "sig": info.get("sig"), "line": line[:160]}
    return ("ушла" if ok else "не ушла"), info.get("why", "")


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
    qstate, qinfo = ex.queue_state(facts, cfg, now)
    tstate, tinfo = ex.turn_state(facts, cfg, now)
    mstate, minfo = ex.moderbot_state(facts, cfg, now)
    cstate, cinfo = ex.client_state(facts, cfg, now)
    pstate, pinfo = ex.price_state(facts, cfg, now)
    chstate, chinfo = ex.chatlog_state(facts, cfg, now)
    out = {"verdicts": len(verdicts), "notes": [], "closed": [], "dry": bool(dry),
           "queue": qstate, "turn": tstate, "why": tinfo.get("why", ""),
           # ОЧЕРЕДЬ ХОДИТ ТРОЙКОЙ «исход + причина + счёт эпизодов», и счёт едет в витрину
           # ВСЕГДА, а не только когда прибор заговорил. Это и есть отделение громкости от
           # вердикта с другой стороны: молчащая ветка обязана быть ВИДНА в `--status`, иначе
           # «сообщений не было» неотличимо от «слепоты не было».
           "queue_why": qinfo.get("why", ""),
           "queue_blind": (facts.get("queue_blind") or {}).get("awake"),
           "queue_episodes": (facts.get("queue_blind") or {}).get("episodes"),
           "queue_misses": (facts.get("queue_blind") or {}).get("misses"),
           # У модербота состояние ходит ПАРОЙ со своей причиной: «неизвестно» без причины
           # читается как «плохо», а это разные новости.
           "moderbot": mstate, "mod_why": minfo.get("why", ""),
           # Чем именно объяснено молчание оборота, если объяснено: КОГДА полоса объявила работу.
           # Владелец, читающий статус, обязан видеть ПРИЧИНУ зелёного, а не только его цвет.
           # Номера задачи здесь нет: предмет штампа — время правки реестра, а не имя строки.
           "busy": (tinfo.get("busy") or {}).get("age"),
           # О5 ходит ТРОЙКОЙ «исход + сколько ушло + чем объяснено»: у ветки, чей ожидаемый
           # результат ноль, слово без числа неотличимо от слепоты.
           "client": cstate, "client_sent": ex.client_spoken(cinfo),
           "client_why": cinfo.get("why", ""),
           # О7 ВСЕГДА ЕДЕТ В ИТОГ, а не только при нарушении, — и это и есть ВИТРИНА. Строка
           # пересчитывается ИЗ ФАКТОВ каждым прогоном (как О6), поэтому она не умеет ни
           # замолчать, пока молчание живо, ни задержаться, когда пересчёт вернулся: снимает её
           # тот же замер, что и ставит. Час начала и причина едут рядом со словом.
           "price": pstate, "price_since": pinfo.get("since"), "price_age": pinfo.get("age"),
           "price_why": pinfo.get("why", ""), "price_gate_why": pinfo.get("gate_why"),
           # О8 ЕДЕТ В ВИТРИНУ ВСЕГДА, а не только при нарушении, — по той же причине, что О7:
           # ветка, чей ожидаемый результат «тихо и хорошо», без строки в `--status` неотличима
           # от ветки выключенной. Слово, накопленная тишина и причина ходят тройкой.
           "chatlog": chstate, "chatlog_awake": chinfo.get("awake"),
           "chatlog_runs": chinfo.get("runs"), "chatlog_why": chinfo.get("why", "")}

    # 1. ЗАКРЫТИЕ ЭПИЗОДОВ — первым: владелец обязан узнать, что кончилось, даже если сейчас
    #    открылось что-то новое.
    for key in ex.closures(facts, cfg, list(open_eps.keys())):
        # ПОДТВЕРЖДЕНИЕ ОБНОВЛЕНИЯ ДОГОВАРИВАЕТ «С КАКОЙ НА КАКУЮ» (05.09.2026). Деталь считается
        # ЗДЕСЬ, а не в модуле решения, по той же причине, по которой там нет ни одного обращения к
        # миру: «стало» берётся из СВЕЖИХ фактов, «было» — из ключа эпизода. Сорвётся расчёт —
        # закрытие уйдёт БЕЗ детали, но уйдёт: подтверждение дороже украшения.
        detail = ""
        if key.split("|", 1)[0] in ("o6c", "o6l"):
            try:
                detail = ex.code_close_detail(key, facts, cfg, now)
            except Exception:                                         # noqa: BLE001
                detail = ""
        if dry or send(ex.render_close(key, LANE_LABEL, detail)):
            open_eps.pop(key, None)
            out["closed"].append(key)

    # 2. НАРУШЕНИЯ. Одна заметка на эпизод; повторов нет НАМЕРЕННО (это шум, а не настойчивость).
    #
    #    ЕДИНСТВЕННОЕ ИСКЛЮЧЕНИЕ — О7, и оно обосновано устройством её эпизода, а не важностью
    #    темы. У всех прочих ожиданий эпизод МОЖЕТ кончиться сам: демон провернётся, модербот
    #    затикает, процесс перезапустится. Молчание цены не кончается само ни одной дорогой —
    #    обе ветки выхода лежат в руках владельца (`ex.PRICE_BRANCHES`), и заметка, увиденная
    #    ночью и забытая к утру, оставляет полосу без цены на неделю. Пол повтора — сутки
    #    (`price_repeat`); ноль в этой ручке глушит повтор, а не первую заметку.
    repeat = float(cfg.get("price_repeat", 0.0))
    out["repeats"] = []
    for v in verdicts:
        key = str(v.get("key"))
        was = open_eps.get(key)
        if was is not None:
            if str(v.get("kind")) != "o7_pc_price_mute" or repeat <= 0:
                continue
            try:
                said = float(was.get("said") or was.get("first") or 0.0)
            except (TypeError, ValueError):
                said = 0.0
            if said <= 0 or (now - said) < repeat:
                continue                               # пол повтора не выстоян — молчим
            if dry:
                out["repeats"].append(key)
                continue
            if not send(ex.render(v, LANE_LABEL)):
                continue                               # не помечаем — скажем на следующем прогоне
            was["said"] = now
            open_eps[key] = was
            out["repeats"].append(key)
            continue
        if dry:
            out["notes"].append(key)
            open_eps[key] = {"first": now, "said": now, "kind": v.get("kind")}
            continue
        if not send(ex.render(v, LANE_LABEL)):
            continue                                   # не помечаем — скажем на следующем прогоне
        open_eps[key] = {"first": now, "said": now, "kind": v.get("kind")}
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

    # 4. ПУБЛИКАЦИЯ ВЕРДИКТА О ДЕТЯХ — периодическая строка журнала, а не тревога и не вердикт.
    #    Стои́т ПОСЛЕДНЕЙ и гасится в СЕБЯ по той же причине, что О4: заведена позже всех и не
    #    вправе стоить владельцу ни одной заметки О1–О5, уже ушедшей выше. Причина срыва едет в
    #    итог прогона словами — молчаливого `pass` здесь нет.
    out["kids"], out["kids_pulse"], out["kids_why"], out["kids_line"] = [], None, "", ""
    try:
        out["kids"] = [{"name": k["name"], "state": k["state"], "why": k.get("why", ""),
                        # ИСТОЧНИК ЕДЕТ РЯДОМ С ВЕРДИКТОМ: «работы нет» без имени признака
                        # проверить нечем, а с ним владелец видит, ЧТО именно замерло.
                        "src": k.get("src"), "channel_age": k.get("channel_age")}
                       for k in ex.kids_state(facts, cfg, now)]
        if dry:
            kdue, kinfo = ex.kids_pulse_due(facts, cfg, now)
            out["kids_pulse"] = "нужна (сухой прогон — не пишем)" if kdue else None
            out["kids_why"] = kinfo.get("why", "")
            # Строку показываем в сухом прогоне ТОЛЬКО когда она и правда пора: её форму
            # проверяют глазами до первой боевой записи, но нарисованная «на всякий случай» она
            # соврала бы сегментом «контур жив» ровно тогда, когда оборот НЕ доказан.
            out["kids_line"] = ex.render_kids(kinfo, LANE_LABEL) if kdue else ""
        else:
            out["kids_pulse"], out["kids_why"] = maybe_kids_pulse(st, facts, cfg, now, pulser)
    except Exception as e:                                            # noqa: BLE001
        out["kids_why"] = "публикация о детях сорвалась (%s: %.60s)" % (type(e).__name__, e)

    # 5. О6 — ГОВОРИТСЯ КАЖДЫЙ ВИТОК, А НЕ ОДИН РАЗ В МОМЕНТ ПРАВКИ. Это не украшение отчёта, а
    #    сам предмет ветки: прежняя пометка «pc_agent изменён» рождалась СОБЫТИЕМ внутри
    #    self-update и после хендовера не повторялась НИКОГДА (живой случай 02.09 — владелец
    #    трижды тапал кнопку, которой процесс не понимал). Здесь вердикт пересчитывается из фактов
    #    каждым прогоном, едет в итог, печатается `--status` и ЛОЖИТСЯ В СОСТОЯНИЕ на диск —
    #    то есть не умеет замолчать, пока расхождение живо. Заметка при этом одна на воплощение
    #    процесса: повтор каждые десять минут был бы шумом, а молчание — тем самым дефектом.
    #    Гасится в СЕБЯ (ветка заведена последней и не вправе стоить владельцу заметок О1–О5).
    out["code"] = []
    try:
        out["code"] = ex.code_states(facts, cfg, now)
        st["code"] = {"at": now,
                      "rows": [{"name": r.get("name"), "state": r.get("state"),
                                "behind": r.get("behind"), "files": r.get("files"),
                                "mapped": r.get("mapped"), "why": (r.get("why") or "")[:120]}
                               for r in out["code"]]}
    except Exception as e:                                            # noqa: BLE001
        out["code_why"] = "ветка О6 сорвалась (%s: %.60s)" % (type(e).__name__, e)

    # 7. СЛОВА О КАЖДОМ УЗЛЕ — НА ДИСК КАЖДЫМ ТИКОМ, а не при публикации пульса. Разбор — в
    #    `expectations_pc`, §«СПИСОК ЗДОРОВЬЯ». Коротко: `state["kids"]` пишется раз в период (6 ч),
    #    и читателю с диска доставался последний ОПУБЛИКОВАННЫЙ вердикт вместо сегодняшнего —
    #    замершее зелёное слово, неотличимое от живого. Здесь новых замеров нет ни одного: слова
    #    берутся из уже посчитанных `tstate` и `out["kids"]`. Ветка заведена последней и гасится в
    #    СЕБЯ: она не вправе стоить владельцу ни заметки О1–О6, ни счётчиков тишины ниже.
    try:
        st[ex.HEALTH_SLOT] = {"at": now, "rows": (
            [{"name": ex.HEALTH_DAEMON, "said": tstate, "src": ex.HEALTH_DAEMON_SRC,
              "why": (tinfo.get("why") or "")[:120]}]
            + [{"name": k.get("name"), "said": k.get("state"), "src": k.get("src"),
                "why": (k.get("why") or "")[:120]}
               for k in (out.get("kids") or []) if k.get("name")])}
    except Exception as e:                                            # noqa: BLE001
        out["health_why"] = "слот здоровья не собран (%s: %.60s)" % (type(e).__name__, e)

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
        why = out["why"] or (("молчание оправдано работой, объявленной %s назад"
                              % ex.human_age(out["busy"])) if out.get("busy") is not None else "")
        print("очередь ПК: %s · оборот демона: %s%s"
              % (out["queue"], out["turn"], (" (%s)" % why) if why else ""))
        print("модербот (работа, не жизнь): %s%s"
              % (out["moderbot"], (" (%s)" % out["mod_why"]) if out["mod_why"] else ""))
        # След жизни печатается ПАРОЙ «оборот доказан / что с пульсом»: без второй половины
        # «доказан» читалось бы как «наружу сказано», а это разные вещи.
        print("след жизни наружу (О4): %s · пульс: %s%s"
              % (out.get("life"), out.get("pulse") or "не нужен",
                 (" (%s)" % out.get("pulse_why")) if out.get("pulse_why") else ""))
        # О5 печатается ЧИСЛОМ, а не одним словом: «тихо» без счётчика — это вера, а не замер.
        print("клиентам ушло (О5): %s — отправлено %s%s"
              % (out["client"],
                 "не прочитано" if out["client_sent"] is None else out["client_sent"],
                 (" (%s)" % out["client_why"]) if out["client_why"] else ""))
        # Дети печатаются ПОИМЁННО, КАЖДЫЙ СО СВОИМ ИСТОЧНИКОМ и своей причиной: с 18.08.2026
        # общего признака на всех нет вовсе, и отчёт обязан показывать, ЧЕМ судится каждый.
        # ВИТРИНА О7. Строка живёт РОВНО пока живёт эпизод: она собрана из свежего замера этого
        # же прогона, а не из памяти о заметке. Молчит цена — витрина называет час и причину;
        # вернулся пересчёт — тем же замером строка становится «называется», и снимать её руками
        # не нужно и нечем.
        if out.get("price") == ex.PRICE_MUTE:
            print("цена клиентам (О7): МОЛЧИТ с %s, причина: %s"
                  % (ex._clock(out.get("price_since")),
                     out.get("price_gate_why") or "сторож причины не назвал"))
        else:
            print("цена клиентам (О7): %s%s"
                  % (out.get("price"),
                     (" (%s)" % out.get("price_why")) if out.get("price_why") else ""))
        # ВИТРИНА О8. Печатается ВСЕГДА, а не только при нарушении: ветка, чей ожидаемый
        # результат «тихо и хорошо», без строки в витрине неотличима от ветки выключенной — а
        # именно так архив переписки и простоял шестнадцать суток, никого не потревожив.
        print("часы захвата переписки (О8): %s — заходов %s%s"
              % (out.get("chatlog"),
                 "не прочитано" if out.get("chatlog_runs") is None else out.get("chatlog_runs"),
                 (" (%s)" % out.get("chatlog_why")) if out.get("chatlog_why") else ""))
        print("дети контура (публикация; у каждого СВОЙ признак и СВОЙ предел):")
        for k in out.get("kids") or []:
            print("  %-15s %-14s %-32s %s"
                  % (k["name"], k["state"], k.get("src") or "источника нет", k.get("why") or ""))
        if not out.get("kids"):
            print("  не собрано")
        print("строка о детях: %s%s"
              % (out.get("kids_pulse") or "не нужна",
                 (" (%s)" % out.get("kids_why")) if out.get("kids_why") else ""))
        # О6 печатается КАЖДЫЙ раз и ПОИМЁННО, с двумя числами на строку: на сколько процесс
        # отстал от своего замыкания и сколько файлов в этом замыкании против того, что знает
        # рукописная карта. Второе число и есть улика — «карта знает 2 из 5» видно глазами.
        print("код процессов (О6; замыкание импортов, не список имён):")
        for c in out.get("code") or []:
            print("  %-16s %-14s отстал %-12s замыкание %s, карта знает %s%s"
                  % (c.get("name"), c.get("state"),
                     ex._age_short(c.get("behind")) if c.get("behind") is not None else "—",
                     "?" if c.get("files") is None else c.get("files"),
                     "—" if c.get("mapped") is None else c.get("mapped"),
                     (" · %s" % c.get("why")) if c.get("why") else ""))
        if not out.get("code"):
            print("  не собрано%s" % ((" (%s)" % out.get("code_why")) if out.get("code_why") else ""))
        print("нарушений: %d %s%s"
              % (out["verdicts"], out["notes"],
                 (" · повтор: %s" % out["repeats"]) if out.get("repeats") else ""))
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
