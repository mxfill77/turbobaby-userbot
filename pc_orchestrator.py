#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pc_orchestrator.py — ПК-оркестратор ступень 1 (порт VPS orchestrator_daemon под Windows).

Демон поллит очередь Bridge (lane=pc, ~60с), берёт ОДНУ задачу за раз, исполняет headless
`claude -p` в D:\\turbobaby-bot с текущим .claude/settings.json + pretool_guard (трёхцветный
гард). Красное в headless → задача переходит в NEEDS_APPROVAL (карточку постит Splinter),
демон ждёт решения (approve→повтор шага / reject/таймаут 30мин→failed). Статусы + строка в
cowork_log + пуш Филиппу на done/failed/needs_approval.

БЕЗОПАСНОСТЬ / НЕ НАВРЕДИ:
  - Демон САМ красное не исполняет: headless-гард (pretool_guard) блокирует ask; claude по
    преамбуле выводит маркер NEEDS_APPROVAL и завершается. Демон детектит маркер (stdout ИЛИ
    файл-маркер PRETOOL_ASK_MARKER, который гард пишет в headless) → needs_approval.
  - lane=pc СТРОГО: обрабатываем ТОЛЬКО задачи с item.lane=='pc'. Нет поля lane → пропуск
    (fail-safe: чужие VPS-задачи на ПК не исполняем).
  - Жёсткий таймаут задачи (45 мин) → kill + failed. Одна задача за раз (не параллелим).
  - Рубильник: файл pc_orchestrator.stop → поллинг останавливается (без убийства процессов).
  - userbot/moderation_bot НЕ трогаем.

Автозапуск: schtasks (ONSTART) + watchdog (--watchdog): проверяет свежесть heartbeat, при
падении зовёт schtasks /Run, ЛОГИРУЕТ его вывод и ВЕРИФИЦИРУЕТ подъём (урок инцидента 205 —
тихого сбоя быть не должно).
"""
import os
import re
import sys
import glob
import time
import json
import shutil
import logging
import datetime
import subprocess
import urllib.request
import urllib.parse

REPO = r"D:\turbobaby-bot"

# .env грузим ДО чтения любых config-констант ниже (CLAUDE_BIN/LANE/POLL_SEC/…), иначе
# os.getenv вернёт дефолты вместо значений из .env — прод-баг: демон не увидел бы CLAUDE_BIN.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
except Exception:
    pass

VENV_PY = os.path.join(REPO, "venv", "Scripts", "python.exe")
DNOTIFY = os.path.join(REPO, "dispatch_notify.py")
TASK_NAME = "pc_orchestrator"          # имя задачи Планировщика (schtasks /Run /TN pc_orchestrator)

LANE = os.getenv("PC_LANE", "pc")
POLL_SEC = int(os.getenv("PC_POLL_SEC", "60") or "60")
TASK_TIMEOUT = int(os.getenv("PC_TASK_TIMEOUT", "2700") or "2700")     # 45 мин жёсткий таймаут
APPROVAL_TTL = int(os.getenv("PC_APPROVAL_TTL", "1800") or "1800")     # 30 мин ожидание approve
NEEDS_APPROVAL_TOPIC = int(os.getenv("PC_NA_TOPIC", "829") or "829")   # тема, куда Splinter постит карточку
HEARTBEAT_STALE = int(os.getenv("PC_HB_STALE", "180") or "180")        # watchdog: heartbeat протух
WATCH_VERIFY_SLEEP = int(os.getenv("PC_WATCH_VERIFY", "20") or "20")
RESULT_MAX = 4500
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")   # ФОЛБЭК: явный путь из .env (может протухнуть при автообновлении)
# Базовая папка версионных установок claude-code (AppData\Roaming\Claude\claude-code\<версия>\claude.exe).
# Резолвим НОВЕЙШУЮ установку сами → путь переживает автообновление, даже когда .env-путь протух (WinError 2).
_CLAUDE_BASE = os.path.join(os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
                            "Claude", "claude-code")

STOP_FLAG = os.path.join(REPO, "pc_orchestrator.stop")
HEARTBEAT_FILE = os.path.join(REPO, "pc_orchestrator.heartbeat")
LOG_PATH = os.path.join(REPO, "pc_orchestrator.log")
NA_MARKER = "NEEDS_APPROVAL:"
ASK_MARKER_ENV = "PRETOOL_ASK_MARKER"   # env для pretool_guard: писать красную карточку в этот файл
MARKER_TOKEN_ENV = "PRETOOL_MARKER_TOKEN"   # env: токен нашего запуска — гард штампует им карточки;
MARKER_SEP = "\x1f"                          # демон принимает ТОЛЬКО карточки со своим токеном (fix ghost:
#   subprocess-тесты гарда наследовали боевой маркер и писали фикстурные карточки — «призрак PID 1»).

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pc_orchestrator")

BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()

_NA_FALLBACK = ("требует подтверждения", "нужно подтверждение", "нужно «да»", "needs approval",
                "permission to use", "не разрешено гейтом", "нет прав на approve")

PREAMBLE = (
    "Ты выполняешь задачу автономно в headless-режиме (без интерактивного подтверждения) в репо "
    "D:\\turbobaby-bot — .claude/settings.json и pretool_guard действуют. Зелёное/оранжевое "
    "(чтение, правки кода в проекте, тесты, git commit/push) делай САМ. НАСТОЯЩЕЕ КРАСНОЕ "
    "(удаление файлов, правка/чтение .env и секретов, taskkill/schtasks, sqlite/запись в БД, "
    "выход за пределы проекта, сеть кроме git/Bridge/Telegram) — НЕ выполняй и НЕ ищи обход: "
    "выведи РОВНО одну строку «NEEDS_APPROVAL: <карточка: что · зачем · последствия>» и заверши "
    "работу (исполнит человек после «да»). userbot и moderation_bot НЕ трогай. Первая строка "
    "ответа — краткая сводка (≤400 символов). ЗАДАЧА:\n"
)


# ------------------------------- Bridge (очередь) ----------------------------

class Bridge:
    """Минимальный клиент очереди Bridge (совместим с протоколом VPS). read=GET, мутации=POST."""
    def __init__(self, url=None, token=None, timeout=90):
        self.url = url or BRIDGE_URL
        self.token = token or BRIDGE_TOKEN
        self.timeout = timeout

    def _get(self, action, **params):
        q = {"action": action, "token": self.token, **{k: v for k, v in params.items() if v is not None}}
        full = self.url + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(urllib.request.Request(full, method="GET"), timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}

    def _post(self, action, **fields):
        body = json.dumps({"action": action, "token": self.token, **fields}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}

    def get_pending(self, status, lane=LANE):
        return self._get("get_pending", status=status, lane=lane)

    def claim_task(self, tid):
        return self._post("claim_task", id=tid)

    def complete_task(self, tid, status, result):
        return self._post("complete_task", id=tid, status=status, result=(result or "")[:RESULT_MAX])

    def set_needs_approval(self, tid, what, topic=NEEDS_APPROVAL_TOPIC):
        # topic → Splinter постит красную карточку в эту тему (по уточнению Филиппа — 829).
        return self._post("set_needs_approval", id=tid, what=(what or "")[:RESULT_MAX], topic=topic)

    def task_heartbeat(self, tid):
        return self._post("task_heartbeat", id=tid)

    def enqueue_task(self, frm, text, lane=LANE):
        return self._post("enqueue_task", **{"from": frm, "task_text": text, "lane": lane})


bc = Bridge()


# ------------------------------- утилиты -------------------------------------

def _cowork(line):
    """Строка-итог в cowork_log через скрипт (fire-and-forget)."""
    try:
        subprocess.Popen([VENV_PY, os.path.join(REPO, "cowork_log_append.py"), "NOTE Orchestrator: " + line],
                         cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except Exception as e:
        log.warning("cowork_log не записан: %s", e)


def _notify(text):
    """Пуш Филиппу через dispatch_notify (fire-and-forget, человекочитаемо)."""
    try:
        subprocess.Popen([VENV_PY, DNOTIFY, text], cwd=REPO,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except Exception as e:
        log.warning("пуш не отправлен: %s", e)


def _stopped():
    return os.path.exists(STOP_FLAG)


def _write_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
    except Exception:
        pass


def _lane_ok(item):
    """СТРОГО: обрабатываем только задачи своей полосы. Нет поля lane → пропуск (fail-safe)."""
    return str(item.get("lane") or "") == LANE


def _age_sec(updated_iso, now=None):
    try:
        s = str(updated_iso).replace("Z", "+00:00")
        t = datetime.datetime.fromisoformat(s)
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        now = now or datetime.datetime.now(datetime.timezone.utc)
        return (now - t).total_seconds()
    except Exception:
        return None


def _detect_needs_approval(out, marker_content="", run_token=None):
    """Красная зона: файл-маркер гарда (headless-сигнал) ИЛИ маркер в stdout ИЛИ фолбэк-фразы.
    run_token задан → принимаем ТОЛЬКО карточки нашего запуска (строки '<token>\\x1f<текст>');
    чужие/старые (иной или пустой токен) — логируем и игнорируем (страховка от «призрака»)."""
    # 1) файл-маркер гарда (надёжный сигнал: гард форснул ask в headless).
    #    Дедуп строк: одно красное действие могло ретраиться → карточка набегала ×N (была ×5).
    if marker_content:
        seen, uniq, foreign = set(), [], 0
        for ln in marker_content.splitlines():
            if not ln.strip():
                continue
            tok, text = ln.split(MARKER_SEP, 1) if MARKER_SEP in ln else ("", ln)
            if run_token is not None and tok != run_token:
                foreign += 1            # чужая/старая карточка (не наш run_token) — не наш ask
                continue
            key = text.strip()
            if key and key not in seen:
                seen.add(key)
                uniq.append(text)
        if foreign:
            log.warning("маркер: игнорирую %d чужих/старых карточек (не наш run_token=%s)", foreign, run_token)
        if uniq:
            return ("NEEDS_APPROVAL (гард): " + "\n".join(uniq))[:RESULT_MAX]
        # только чужие карточки → это НЕ наш красный сигнал; идём к stdout-маркеру/фолбэку ниже
    t = out or ""
    for line in t.splitlines():
        i = line.find(NA_MARKER)
        if i >= 0:
            what = line[i + len(NA_MARKER):].strip()
            return (what or "(claude не уточнил красное действие)")[:RESULT_MAX]
    low = t.lower()
    if any(p in low for p in _NA_FALLBACK):
        return ("(гейт заблокировал красное; маркера нет — вывод:)\n" + t[:1400])
    return None


# ------------------------------- запуск claude -------------------------------

def _ver_key(name):
    """Ключ сортировки версии '2.1.197' → (2,1,197); нечисловое → (0,)."""
    nums = re.findall(r"\d+", name or "")
    return tuple(int(n) for n in nums) if nums else (0,)


_claude_cache = None


def resolve_claude():
    """Найти исполняемый claude БЕЗ привязки к версии (иначе .env-путь протухает при
    автообновлении → [WinError 2]). Порядок:
      1) PATH-шим: shutil.which('claude') — учитывает PATHEXT (claude.cmd/.exe/.bat);
      2) CLAUDE_BIN из .env — только если файл реально существует;
      3) НОВЕЙШАЯ версия в AppData\\...\\claude-code\\<версия>\\claude.exe.
    → путь (str) или None. Кэшируем, но перепроверяем существование (переживаем автообновление)."""
    global _claude_cache
    if _claude_cache and os.path.isfile(_claude_cache):
        return _claude_cache
    _claude_cache = None
    w = shutil.which("claude")                       # 1) PATH-шим (.cmd/.exe через PATHEXT)
    if w and os.path.isfile(w):
        _claude_cache = w
        return w
    if CLAUDE_BIN and os.path.isabs(CLAUDE_BIN) and os.path.isfile(CLAUDE_BIN):  # 2) .env, если жив
        _claude_cache = CLAUDE_BIN
        return CLAUDE_BIN
    try:                                             # 3) новейшая версионная установка
        cands = []
        for d in glob.glob(os.path.join(_CLAUDE_BASE, "*")):
            exe = os.path.join(d, "claude.exe")
            if os.path.isfile(exe):
                cands.append((_ver_key(os.path.basename(d)), exe))
        if cands:
            cands.sort()
            _claude_cache = cands[-1][1]
            return _claude_cache
    except Exception:
        pass
    return None


def run_claude(prompt, timeout, cwd, env):
    """Запуск headless claude -p. Возврат (returncode, stdout, stderr). Таймаут → TimeoutError.
    Инъектируется в тестах (реальный claude не дёргаем). Путь резолвится версионно-независимо."""
    cbin = resolve_claude()
    if not cbin:
        raise FileNotFoundError("claude CLI не найден (PATH/.env/AppData)")
    try:
        p = subprocess.run([cbin, "-p", prompt], cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=env)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        raise TimeoutError("claude -p timeout")


def run_task(tid, text, note=""):
    """Исполнить задачу через headless claude -p. → (status, result). status ∈ done|failed|needs_approval."""
    marker_path = os.path.join(REPO, f"pc_ask_{tid}.marker")
    try:  # ЧИСТИМ маркер ПЕРЕД каждым запуском headless (в т.ч. перед повтором approved-шага):
        if os.path.exists(marker_path):   # иначе красная карточка прошлого прогона протекла бы
            os.remove(marker_path)        # в результат нового (ложное needs_approval).
    except Exception:
        pass
    cbin = resolve_claude()                       # версионно-независимый резолв (класс-фикс WinError 2)
    if not cbin:
        msg = ("claude CLI не найден: нет ни в PATH, ни в CLAUDE_BIN (.env), ни в "
               + os.path.join(_CLAUDE_BASE, "<версия>", "claude.exe")
               + ". Проверь установку/автообновление claude-code.")
        log.error("id=%s НЕ НАЙДЕН claude: %s", tid, msg)
        return "failed", msg
    run_token = f"{os.getpid()}-{int(time.time() * 1000)}-{tid}"   # контекст запуска: pid+ts+tid
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # headless идёт по ~/.claude (подписка), не платный API
    env.pop("OPENAI_API_KEY", None)
    env[ASK_MARKER_ENV] = marker_path            # pretool_guard в headless пишет сюда красную карточку
    env[MARKER_TOKEN_ENV] = run_token            # …штампуя её нашим токеном — чужие карточки отсеем
    prompt = (note + PREAMBLE) if note else PREAMBLE
    prompt += text
    log.info("RUN id=%s (timeout=%ss)", tid, TASK_TIMEOUT)
    try:
        rc, out, err = run_claude(prompt, TASK_TIMEOUT, REPO, env)
    except TimeoutError:
        log.warning("id=%s ТАЙМАУТ %ss → failed", tid, TASK_TIMEOUT)
        return "failed", f"таймаут {TASK_TIMEOUT}s — headless прерван, задача не завершилась"
    except Exception as e:
        log.error("id=%s ошибка запуска: %s", tid, e)
        return "failed", f"ошибка запуска claude: {e}"
    # прочитать маркер ДО удаления (гард пишет туда красную карточку в headless)
    marker_content = ""
    try:
        if os.path.isfile(marker_path):
            with open(marker_path, encoding="utf-8", errors="ignore") as mf:
                marker_content = mf.read().strip()
    except Exception:
        pass
    try:
        os.remove(marker_path)
    except Exception:
        pass
    card = _detect_needs_approval(out, marker_content, run_token)
    if card is not None:
        log.info("id=%s NEEDS_APPROVAL", tid)
        return "needs_approval", card
    out = (out or "").strip()
    if rc != 0:
        return "failed", (f"claude exit={rc}: " + (out or err or "нет вывода"))[:RESULT_MAX]
    return "done", (out or "(claude вернул пустой вывод)")[:RESULT_MAX]


# ------------------------------- обработчики цикла ---------------------------

def _human(kind, tid, text):
    first = (str(text or "").strip().splitlines() or ["(пусто)"])[0][:300]
    if kind == "done":
        return f"✅ Оркестратор: задача #{tid} выполнена — {first}"
    if kind == "failed":
        return f"❌ Оркестратор: задача #{tid} провалена — {first}"
    if kind == "needs_approval":
        return f"🔔 Оркестратор: задача #{tid} ждёт твоего «да» — {first}"
    return f"Оркестратор: задача #{tid}"


def process_new():
    """Взять СТАРЕЙШУЮ new-задачу своей полосы, исполнить, записать результат/needs_approval."""
    if _stopped():
        return
    r = bc.get_pending("new")
    if not r.get("ok"):
        log.warning("get_pending(new) ошибка: %s", r.get("error"))
        return
    items = [it for it in r.get("items", []) if _lane_ok(it)]
    if not items:
        return
    task = sorted(items, key=lambda x: int(x.get("id") or 0))[0]   # FIFO
    tid = task.get("id")
    text = str(task.get("task_text") or "")
    cl = bc.claim_task(tid)
    if not cl.get("ok"):
        log.info("claim id=%s не удался (%s) — пропуск", tid, cl.get("error"))
        return
    log.info("CLAIM id=%s in_progress", tid)
    _cowork(f"взял задачу #{tid} (in_progress)")
    status, result = run_task(tid, text)
    if status == "needs_approval":
        bc.set_needs_approval(tid, result)
        log.info("NEEDS_APPROVAL id=%s", tid)
        _cowork(f"задача #{tid} → needs_approval (красное, жду «да»)")
        _notify(_human("needs_approval", tid, result))
    else:
        bc.complete_task(tid, status, result)
        log.info("COMPLETE id=%s status=%s", tid, status)
        _cowork(f"задача #{tid} → {status}")
        _notify(_human(status, tid, result))


def process_approved():
    """Одобренные красные задачи (status=approved) → повторить шаг headless. Снова красное →
    failed (не зацикливаемся; демон красное сам не исполняет). Истёк TTL → failed."""
    r = bc.get_pending("approved")
    if not r.get("ok"):
        return
    for task in sorted([it for it in r.get("items", []) if _lane_ok(it)], key=lambda x: int(x.get("id") or 0)):
        tid = task.get("id")
        if _stopped():
            return
        if (_age_sec(task.get("updated")) or 0) > APPROVAL_TTL:
            log.info("APPROVED id=%s истёк (>%ss) → failed", tid, APPROVAL_TTL)
            bc.complete_task(tid, "failed", "approve истёк (>30 мин) — повтори задачу")
            _notify(_human("failed", tid, "approve истёк"))
            continue
        status, result = run_task(tid, str(task.get("task_text") or ""),
                                  note="[ОДОБРЕНО ЧЕЛОВЕКОМ] предыдущий шаг подтверждён. ")
        if status == "needs_approval":
            bc.complete_task(tid, "failed",
                             "одобрено, но шаг снова упирается в красное — выполни вручную: " + result[:400])
            _notify(_human("failed", tid, "снова красное после approve — вручную"))
        else:
            bc.complete_task(tid, status, result)
            _notify(_human(status, tid, result))
        log.info("APPROVED id=%s → %s", tid, status)


def process_approval_timeouts():
    """needs_approval-задачи, провисевшие дольше APPROVAL_TTL без решения → failed (не ждём вечно).
    (reject Филиппа Splinter ставит сам — такие сюда не попадают.)"""
    r = bc.get_pending("needs_approval")
    if not r.get("ok"):
        return
    for task in [it for it in r.get("items", []) if _lane_ok(it)]:
        tid = task.get("id")
        if (_age_sec(task.get("updated")) or 0) > APPROVAL_TTL:
            log.info("NEEDS_APPROVAL id=%s таймаут (>%ss) → failed", tid, APPROVAL_TTL)
            bc.complete_task(tid, "failed", "подтверждение не получено за 30 мин — задача провалена")
            _notify(_human("failed", tid, "подтверждение не получено за 30 мин"))


def poll_once():
    """Один цикл: довести одобренное → добить просроченные ожидания → взять новое → heartbeat."""
    process_approved()
    process_approval_timeouts()
    process_new()
    _write_heartbeat()


def main():
    log.info("=== ДЕМОН СТАРТ (lane=%s, poll=%ss, task_timeout=%ss, approval_ttl=%ss, claude=%s) ===",
             LANE, POLL_SEC, TASK_TIMEOUT, APPROVAL_TTL, CLAUDE_BIN)
    if _stopped():
        log.info("рубильник pc_orchestrator.stop активен — не стартую поллинг")
        return
    while not _stopped():
        try:
            poll_once()
        except Exception as e:
            log.exception("ошибка цикла: %s", e)
        for _ in range(POLL_SEC):
            if _stopped():
                break
            time.sleep(1)
    log.info("=== ДЕМОН ОСТАНОВЛЕН (рубильник/стоп-флаг) ===")


# ------------------------------- watchdog / автозапуск -----------------------

def _heartbeat_fresh(now=None, threshold=HEARTBEAT_STALE):
    try:
        hb = open(HEARTBEAT_FILE, encoding="utf-8").read().strip()
        age = _age_sec(hb, now=now)
        return age is not None and age <= threshold
    except Exception:
        return False


def _schtasks_run(task_name=TASK_NAME):
    """Запустить задачу Планировщика. Возврат (rc, output). Инъектируется в тестах."""
    try:
        p = subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True, text=True, timeout=30)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return -1, f"schtasks не запустился: {e}"


def watchdog(now=None, runner=None, verify_sleep=None):
    """НАДЁЖНЫЙ вотчдог (урок инцидента 205 — тихого сбоя быть не должно):
    heartbeat свежий → alive; протух и рубильник неактивен → schtasks /Run + ЛОГИРУЕМ его вывод +
    ВЕРИФИЦИРУЕМ подъём; не поднялся → ГРОМКИЙ лог (не молча). → 'stopped'|'alive'|'restarted'|'failed_to_start'."""
    if _stopped():
        log.info("watchdog: рубильник активен — демон намеренно не поднимается")
        return "stopped"
    if _heartbeat_fresh(now=now):
        return "alive"
    log.warning("watchdog: heartbeat протух (>%ss) — демон не жив, поднимаю через schtasks", HEARTBEAT_STALE)
    rc, out = (runner or _schtasks_run)()
    log.warning("watchdog: schtasks /Run /TN %s → rc=%s | %s", TASK_NAME, rc, out)   # ЛОГ вывода (205)
    if verify_sleep is None:
        verify_sleep = WATCH_VERIFY_SLEEP
    if verify_sleep:
        time.sleep(verify_sleep)
    if _heartbeat_fresh(now=now):
        log.info("watchdog: демон поднялся — heartbeat свежий")
        return "restarted"
    log.error("watchdog: ТИХИЙ СБОЙ ПРЕДОТВРАЩЁН — демон НЕ поднялся после schtasks /Run (rc=%s). "
              "Нужно вмешательство.", rc)
    _notify("⚠️ Оркестратор: watchdog не смог поднять демон через schtasks — проверь pc_orchestrator.log")
    return "failed_to_start"


def self_update_ok():
    """Гейт самообновления (если демон обновляется): py_compile+import-smoke нового кода. Битый → False."""
    try:
        import selfupdate_gate
        return selfupdate_gate.code_gate(VENV_PY, REPO, ["pc_orchestrator.py"], "pc_orchestrator")
    except Exception as e:
        return False, f"гейт не запустился: {e}"


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--watchdog":
        print(watchdog())
    elif arg == "--stop":
        open(STOP_FLAG, "w").close()
        print("рубильник поставлен: pc_orchestrator.stop")
    elif arg == "--start":
        try:
            os.remove(STOP_FLAG)
        except Exception:
            pass
        print("рубильник снят")
    else:
        main()
