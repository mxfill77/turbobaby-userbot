# -*- coding: utf-8 -*-
"""
session_watch.py — ДЕТЕКТОР НЕМОТЫ сессий Claude Code на ПК.

ЗАЧЕМ. 29.07.2026 RC-сессия «опись мозга» (PID 21216, `cse_01Xa5KTXp3RbcmFFXqeoAcJx`) не
начала работать ВООБЩЕ: процесс жив, heartbeat каждые 20 с без пропусков, 529 heartbeat за
2 ч 53 мин — и НОЛЬ запросов к модели, ноль tool_use, транскрипт `.jsonl` не создан. Владелец
узнал через два часа и только потому, что спросил. Разбор — `docs/artifacts/
2026-07-29-mute-session-21216-evidence.md`: сессия исправно подключилась к SSE за 0.3 с, а
первое событие получила через 16 минут, и то служебное (`control_request`) — задание к ней
не доехало. Отказ на стороне ДОСТАВКИ, а не исполнения.

Почему его не видит НИ ОДИН существующий надзор:
  • `rc_supervisor.channel_alive` — лог свеж (heartbeat пишется!), соединения есть → ЖИВ;
  • контур-вотчдог `pc_orchestrator` — следит за userbot/moderbot/pc_agent, не за сессиями;
  • ливнесс одиночек lane=pc — судит по статусу задачи в Bridge, а эта сессия из RC-полосы.
Все они спрашивают «процесс жив?». Немота — это «процесс жив, а работы нет».

ПРИЗНАК ОТКАЗА (оба условия сразу, И):
  1) за MUTE_AFTER секунд после старта НЕ появился транскрипт `<sessionId>.jsonl`;
  2) в логе сессии нет НИ ОДНОГО следа работы (запрос к модели / tool_use).
Второе условие — только ОПРАВДАТЕЛЬНОЕ: оно умеет заткнуть сигнал, но никогда его не поднимает
(см. «немота против молчаливого thinking» ниже).

ПОРОГ MUTE_AFTER = 300 с (5 минут). Обоснование числами, замер по корпусу 13 живых сессий
за сутки 29.07: транскрипт появляется через **1.7–3.1 с** после старта процесса у 12 из 12
работавших (медиана 1.8 с; у RC/sdk-cli — 2.9 и 3.1 с). Мёртвая — 10 300+ с и ничего. Разрыв
в три порядка, порог кладём с многократным запасом в середину: 300 с это **97× выше**
худшего живого старта (3.1 с) и **24× ниже** сегодняшнего срока обнаружения (2 ч). Медленный
старт (холодный диск, MCP, пробуждение ПК) стоит секунды, не минуты, — под нож не попадёт.

ОБЛАСТЬ (MUTE_ENTRYPOINTS, по умолчанию `sdk-cli`). Следим за сессиями, которых владелец НЕ
ВИДИТ: RC/headless (`entrypoint=sdk-cli`) — ровно тот класс, что отказал. Сессии Claude
Desktop исключены НАМЕРЕННО: там владелец сидит перед окном и немоту видит сам, зато открытая
и не тронутая сессия («открыл и не написал») дала бы гарантированный ложняк. Расширяется одной
переменной окружения.

НЕМОТА против МОЛЧАЛИВОГО THINKING — честно. Отличить надёжно МОЖНО, и вот почему: транскрипт
создаётся на ПЕРВОМ обработанном событии, ДО первого запроса к модели, — то есть задолго до
любого размышления. Долгий thinking идёт УЖЕ ПРИ СУЩЕСТВУЮЩЕМ файле транскрипта. Поэтому
«транскрипта нет вовсе» не может означать «думает» — только «работы не было». Ненадёжна лишь
ВТОРАЯ половина признака: сессия может честно думать минутами без единого tool_use. Ровно
поэтому tool_use здесь НИКОГДА не поднимает сигнал, а только снимает его. Безопасная сторона
соблюдена в обе стороны: у по-настоящему немой сессии нет НИ транскрипта, НИ следов работы,
так что пропустить её нельзя; а думающая сессия имеет транскрипт и не будет тронута никогда.

СЛЕПОТА ≠ СМЕРТЬ (класс #171). Не смогли получить список процессов (CIM/tasklist упал по
таймауту на просыпающемся ПК) → тик МОЛЧИТ целиком и повторит позже. Проверка личности
процесса (анти-PID-reuse), наоборот, FAIL-OPEN: не смогли подтвердить — сигналим (лучше
лишняя карточка, чем пропущенная немота).

Сигнал: РОВНО ОДИН на сессию. Состояние «уже сигналили» — на диске (STATE_PATH), ключ —
`sessionId`; пометка ставится ТОЛЬКО при подтверждённой доставке, иначе сигнал потерялся бы
на первой же сетевой ошибке. Канал — тема 328 (постановка задач) через `dispatch_notify
--topic`, фолбэк инбокс 1160 → личка.

Хост: вызывается тиком `pc_orchestrator` (он «единственный надёжно выживающий процесс» и уже
держит контур-вотчдог). Модуль самодостаточен: `python session_watch.py --once` — один проход,
`--dry-run` — без отправки.
"""

import os
import re
import sys
import json
import glob
import time
import logging
import calendar
import subprocess

REPO = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
# Реестр ЖИВЫХ сессий: Claude Code кладёт сюда <pid>.json на старте и убирает на выходе
# (живой факт 29.07: все 13 файлов = 13 живых процессов). Поля — pid, sessionId, cwd,
# startedAt (мс), entrypoint, kind, name.
SESSIONS_DIR = os.path.join(HOME, ".claude", "sessions")
# Транскрипты: <projects>/<slug(cwd)>/<sessionId>.jsonl
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
STATE_PATH = os.path.join(REPO, "pc_session_watch.state.json")
LOG_PATH = os.path.join(REPO, "session_watch.log")

MUTE_AFTER = int(os.getenv("PC_MUTE_AFTER", "300") or "300")          # см. шапку: 300 с
TICK_SEC = int(os.getenv("PC_MUTE_TICK", "60") or "60")               # шаг автономного цикла
MUTE_TOPIC = int(os.getenv("PC_MUTE_TOPIC", "328") or "328")          # тема постановки задач
STATE_TTL = int(os.getenv("PC_MUTE_STATE_TTL", "604800") or "604800")  # 7 сут: чистка пометок
# Допуск сверки «время старта процесса ≈ startedAt сессии» (анти-PID-reuse). Щедрый: сравниваем
# разные источники времени (файл сессии пишется чуть позже создания процесса).
IDENTITY_TOLERANCE = int(os.getenv("PC_MUTE_IDENTITY_TOL", "120") or "120")
MUTE_ENTRYPOINTS = tuple(x.strip() for x in
                         (os.getenv("PC_MUTE_ENTRYPOINTS", "sdk-cli") or "").split(",")
                         if x.strip())

# Скрытый спавн детей (класс «мигающие чёрные окна» 22.07). POSIX → 0.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Следы РАБОТЫ в логе сессии (--debug-file). Все сняты с ЖИВЫХ логов 29.07 и проверены на паре
# «мёртвая / здоровая»: в здоровой cse_0141ZBzVF2i38mwDeuCTsdP6 — 108 / 73 / 71 совпадений,
# в немой cse_01Xa5KTXp3RbcmFFXqeoAcJx — РОВНО НОЛЬ у каждого. Подстроки дословные: голое `401`
# из соседнего разбора уже показало, что число-в-JSON даёт 78 % ложных.
ACTIVITY_MARKERS = (
    "[API REQUEST]",                        # запрос к модели ушёл
    "Stream started - received first chunk",  # ответ модели пошёл
    "new action being classified",          # tool_use: действие поехало в классификатор
    "Hooks: Found",                         # хуки крутятся вокруг инструментов
)

log = logging.getLogger("session_watch")
log.setLevel(logging.INFO)
log.propagate = False
try:
    import log_setup
    _h = log_setup.rotating_handler(LOG_PATH)
    if _h is not None:
        log.addHandler(_h)
except Exception:
    try:
        _h = logging.FileHandler(LOG_PATH, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        log.addHandler(_h)
    except Exception:
        pass


# --- чтение реестра сессий ------------------------------------------------------------------

def project_slug(cwd):
    """Каталог транскриптов для рабочего каталога: всё, кроме букв и цифр, → дефис, в нижний
    регистр. Живой факт: `D:\\turbobaby-bot` → `d--turbobaby-bot`."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(cwd or "")).lower()


def read_sessions(sessions_dir=None, globber=None, opener=None):
    """Реестр живых сессий → список нормализованных словарей. Битый/недописанный json молча
    пропускаем (файл пишется чужим процессом, поймать его на полузаписи — норма)."""
    d = sessions_dir or SESSIONS_DIR
    _glob = globber or glob.glob
    _open = opener or (lambda p: open(p, encoding="utf-8"))
    out = []
    try:
        files = _glob(os.path.join(d, "*.json"))
    except Exception:
        return out
    for path in files:
        try:
            with _open(path) as f:
                raw = json.load(f)
        except Exception:
            continue
        pid = raw.get("pid")
        sid = raw.get("sessionId")
        if not pid or not sid:
            continue
        out.append({
            "pid": int(pid),
            "session_id": str(sid),
            "cwd": str(raw.get("cwd") or ""),
            "started_at": float(raw.get("startedAt") or 0) / 1000.0,
            "entrypoint": str(raw.get("entrypoint") or ""),
            "kind": str(raw.get("kind") or ""),
            "name": str(raw.get("name") or ""),
            "version": str(raw.get("version") or ""),
            "path": path,
        })
    out.sort(key=lambda s: s["started_at"])
    return out


def transcript_path(sess, projects_dir=None, isfile=None, globber=None):
    """Путь к транскрипту сессии или None. Сначала точный путь по slug(cwd), затем — глоб по
    ВСЕМ проектам: сессия могла быть заведена в другом каталоге (`--add-dir`, нейтральный cwd
    у headless-прогонов), и «нет по точному пути» ещё не значит «транскрипта нет»."""
    root = projects_dir or PROJECTS_DIR
    _isf = isfile or os.path.isfile
    _glob = globber or glob.glob
    exact = os.path.join(root, project_slug(sess.get("cwd")), "%s.jsonl" % sess["session_id"])
    try:
        if _isf(exact):
            return exact
    except Exception:
        pass
    try:
        hits = _glob(os.path.join(root, "*", "%s.jsonl" % sess["session_id"]))
    except Exception:
        hits = []
    return hits[0] if hits else None


# --- следы работы ---------------------------------------------------------------------------

def parse_debug_file(cmdline):
    """`--debug-file <путь>` из командной строки процесса → путь или None. Это ТОЧНАЯ связка
    сессия↔её лог (живой факт: у PID 21216 в cmdline стоит
    `--debug-file D:\\turbobaby-bot\\rc_server_debug-cse_01Xa5KTXp3RbcmFFXqeoAcJx.log`),
    поэтому лог НЕ угадывается ни по имени, ни по времени."""
    s = str(cmdline or "")
    m = re.search(r"--debug-file[=\s]+(\"[^\"]+\"|\S+)", s)
    if not m:
        return None
    return m.group(1).strip('"')


def log_has_activity(path, opener=None, markers=None):
    """Есть ли в логе сессии хоть один след работы (ACTIVITY_MARKERS). Файла нет / не прочли →
    False («следов не нашли»), а не исключение: отсутствие лога само по себе не оправдание."""
    if not path:
        return False
    _open = opener or (lambda p: open(p, encoding="utf-8", errors="replace"))
    marks = markers or ACTIVITY_MARKERS
    try:
        with _open(path) as f:
            for line in f:
                for m in marks:
                    if m in line:
                        return True
    except Exception:
        return False
    return False


# --- процессы -------------------------------------------------------------------------------

def live_pids(runner=None):
    """PID'ы живых claude.exe → (ok, множество). ok=False — СПРОСИТЬ НЕ УДАЛОСЬ (класс #171:
    «не смог проверить» ≠ «мёртв»), вызывающий обязан промолчать весь тик."""
    try:
        p = (runner or subprocess.run)(
            ["tasklist", "/FI", "IMAGENAME eq claude.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=NO_WINDOW)
    except Exception:
        return False, set()
    out = getattr(p, "stdout", "") or ""
    if getattr(p, "returncode", 0) not in (0, None):
        return False, set()
    pids = set()
    for line in out.splitlines():
        parts = [x.strip().strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    # Пустой ответ tasklist по фильтру — это ЧЕСТНАЯ пустота («ни одного claude.exe»), и она
    # законна: значит все сессии из реестра уже вышли. ok=True, список пуст.
    return True, pids


def process_facts(pid, runner=None):
    """Командная строка и время старта процесса → {"cmdline": str, "created": epoch|None}.
    Дорого (CIM), поэтому спрашиваем ТОЛЬКО про кандидатов — а их в норме ноль. Ошибка/таймаут
    → пустой словарь (вызывающий трактует как «подтвердить не смогли»)."""
    ps = ("$p=Get-CimInstance Win32_Process -Filter \"ProcessId=%d\";"
          "if($p){[Console]::Out.Write(($p.CreationDate.ToUniversalTime()"
          ".ToString('yyyy-MM-ddTHH:mm:ssZ'))+\"`n\"+$p.CommandLine)}" % int(pid))
    try:
        p = (runner or subprocess.run)(["powershell", "-NoProfile", "-Command", ps],
                                       capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=30, creationflags=NO_WINDOW)
    except Exception:
        return {}
    out = (getattr(p, "stdout", "") or "").strip()
    if not out:
        return {}
    head, _, cmd = out.partition("\n")
    created = None
    try:
        # Время из CIM пришло в UTC → timegm (mktime считал бы его местным и врал бы на
        # смещение, а с летним временем — ещё и непредсказуемо).
        created = calendar.timegm(time.strptime(head.strip(), "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        created = None
    return {"cmdline": cmd.strip(), "created": created}


def identity_ok(sess, created, tolerance=None):
    """Тот ли это процесс (анти-PID-reuse): время старта процесса сходится со `startedAt` файла
    сессии. FAIL-OPEN — подтвердить не смогли (created=None) → True: лучше лишняя карточка,
    чем пропущенная немота. False только когда время ЯВНО противоречит."""
    if created is None:
        return True
    tol = IDENTITY_TOLERANCE if tolerance is None else tolerance
    return abs(float(created) - float(sess.get("started_at") or 0)) <= tol


# --- сам детектор ---------------------------------------------------------------------------

def scan(now=None, sessions=None, threshold=None, entrypoints=None,
         live=None, transcript=None, facts=None, activity=None):
    """Найти НЕМЫЕ сессии → список находок. Все пробы инъектируются (тесты без ФС и процессов).

    Порядок проверок — от дешёвых к дорогим, и каждая следующая только СНИМАЕТ подозрение:
    область → возраст → процесс жив → транскрипта нет → личность процесса → следов работы нет."""
    _now = time.time() if now is None else now
    thr = MUTE_AFTER if threshold is None else threshold
    scope = MUTE_ENTRYPOINTS if entrypoints is None else entrypoints
    _live = live or live_pids
    _tr = transcript or transcript_path
    _facts = facts or process_facts
    _act = activity or log_has_activity

    ok, pids = _live()
    if not ok:
        log.info("список процессов недоступен — тик молчит (слепота ≠ смерть)")
        return []

    found = []
    for s in (read_sessions() if sessions is None else sessions):
        if scope and s.get("entrypoint") not in scope:
            continue
        age = _now - (s.get("started_at") or 0)
        if age < thr:
            continue                                   # молодая — транскрипт ещё в пути
        if s["pid"] not in pids:
            continue                                   # процесс вышел: это конец, а не немота
        if _tr(s):
            continue                                   # транскрипт есть → работала
        f = _facts(s["pid"]) or {}
        if not identity_ok(s, f.get("created")):
            continue                                   # PID переиспользован чужим процессом
        dbg = parse_debug_file(f.get("cmdline"))
        if _act(dbg):
            continue                                   # следы работы есть → не немая
        found.append({
            "session_id": s["session_id"],
            "pid": s["pid"],
            "name": s.get("name") or "?",
            "entrypoint": s.get("entrypoint") or "?",
            "started_at": s.get("started_at") or 0,
            "age": age,
            "cwd": s.get("cwd") or "",
            "debug_log": dbg or "",
        })
    return found


def human_age(sec):
    """Длительность по-человечески: «2 ч 53 мин» / «7 мин» / «45 с»."""
    sec = int(max(0, sec))
    if sec < 60:
        return "%d с" % sec
    if sec < 3600:
        return "%d мин" % (sec // 60)
    return "%d ч %d мин" % (sec // 3600, (sec % 3600) // 60)


def format_signal(f):
    """Карточка владельцу: имя, PID, время старта, сколько прожила, ЧТО ИМЕННО не появилось."""
    started = time.strftime("%d.%m %H:%M:%S", time.localtime(f["started_at"]))
    lines = [
        "🔇 НЕМАЯ сессия: запущена, но не начала работать",
        "сессия: %s (%s), PID %s" % (f["name"], f["entrypoint"], f["pid"]),
        "старт: %s — прожила %s" % (started, human_age(f["age"])),
        "НЕ появилось: транскрипт %s.jsonl; запросов к модели и tool_use — ноль" % f["session_id"],
        "процесс жив, heartbeat идёт — сторож и вотчдог такую не видят",
    ]
    if f.get("debug_log"):
        lines.append("лог сессии: %s" % os.path.basename(f["debug_log"]))
    return "\n".join(lines)


def notify(text, runner=None):
    """Карточка в тему 328 через dispatch_notify (фолбэк инбокс 1160 → личка). → (channel, ok).
    ok берём из stdout ребёнка (`ok=1`), а НЕ из кода возврата: dispatch_notify по контракту
    всегда выходит нулём, и «процесс отработал» ещё не значит «сообщение дошло»."""
    py = os.path.join(REPO, "venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    try:
        p = (runner or subprocess.run)(
            [py, os.path.join(REPO, "dispatch_notify.py"), "--topic", str(MUTE_TOPIC), text],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=REPO, creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("сигнал не ушёл (%s)", type(e).__name__)
        return ("none", False)
    out = (getattr(p, "stdout", "") or "").strip()
    m = re.search(r"channel=(\S+)\s+ok=(\d)", out)
    if not m:
        return ("unknown", False)
    return (m.group(1), m.group(2) == "1")


# --- состояние «уже сигналили» --------------------------------------------------------------

def load_state(path=None):
    try:
        with open(path or STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("signalled"), dict):
            return d
    except Exception:
        pass
    return {"signalled": {}}


def save_state(state, path=None):
    p = path or STATE_PATH
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def prune_state(state, now=None, ttl=None):
    """Выбросить пометки старше TTL: сессия с таким sessionId уже не воскреснет, а файл иначе
    растёт вечно. → тот же словарь."""
    _now = time.time() if now is None else now
    _ttl = STATE_TTL if ttl is None else ttl
    sig = state.get("signalled") or {}
    for k in [k for k, v in sig.items() if _now - float((v or {}).get("at") or 0) > _ttl]:
        sig.pop(k, None)
    return state


def tick(now=None, state_path=None, notifier=None, dry_run=False, **scan_kw):
    """Один проход: найти немых → отсигналить НОВЫХ → запомнить. → список отсигналенных находок.

    Пометка ставится ТОЛЬКО при подтверждённой доставке (ok=True). Иначе сеть моргнула — и
    сигнал был бы потерян навсегда: следующий тик его уже не повторит."""
    _now = time.time() if now is None else now
    found = scan(now=_now, **scan_kw)
    if not found:
        return []
    state = prune_state(load_state(state_path), now=_now)
    sig = state.setdefault("signalled", {})
    sent = []
    for f in found:
        if f["session_id"] in sig:
            continue                                   # ровно один сигнал на сессию
        text = format_signal(f)
        if dry_run:
            log.info("DRY-RUN немая сессия pid=%s: %s", f["pid"], text.replace("\n", " | "))
            sent.append(f)
            continue
        channel, ok = (notifier or notify)(text)
        log.info("немая сессия pid=%s sid=%s возраст=%s → channel=%s ok=%s",
                 f["pid"], f["session_id"][:8], human_age(f["age"]), channel, ok)
        if ok:
            sig[f["session_id"]] = {"at": _now, "pid": f["pid"], "channel": channel}
            sent.append(f)
    if sent and not dry_run:
        save_state(state, state_path)
    return sent


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in args
    dry = "--dry-run" in args
    log.info("session_watch старт (порог=%sс, область=%s, тема=%s, once=%s, dry=%s)",
             MUTE_AFTER, ",".join(MUTE_ENTRYPOINTS) or "все", MUTE_TOPIC, once, dry)
    while True:
        try:
            sent = tick(dry_run=dry)
            if once:
                for f in sent:
                    print(format_signal(f))
                    print("-" * 40)
                print("немых сессий отсигналено: %d" % len(sent))
                return 0
        except Exception as e:               # НЕ НАВРЕДИ: детектор не смеет уронить хозяина
            log.warning("тик сорвался (%s)", type(e).__name__)
            if once:
                return 1
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    sys.exit(main())
