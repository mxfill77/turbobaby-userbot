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
    "ответа — краткая сводка (≤400 символов). ПОСЛЕДНЯЯ строка вывода — ОБЯЗАТЕЛЬНО "
    "«RESULT: <краткий итог сделанного>»; без этой строки задача считается НЕ выполненной. ЗАДАЧА:\n"
)

_RE_RESULT = re.compile(r"(?m)^\s*RESULT:\s*\S")   # признак результата в stdout headless


def _tail(s, n=500):
    """Хвост строки для диагноза (последние ~n символов, без пустышек)."""
    s = (s or "").strip()
    return s[-n:] if s else ""


COWORK_RESULT_MAX = 1500   # полный текст RESULT/причины failed в cowork_log штаб читает без скринов


def _clip(s, n=COWORK_RESULT_MAX):
    """Однострочный итог для cowork_log: схлопываем пробелы/переносы (лог — одна строка),
    режем до n символов с явной пометкой «…обрезано», чтобы штаб видел усечение."""
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else (s[:n] + " …обрезано")


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
        # encoding=utf-8 + errors=replace: без явной кодировки text=True берёт локаль Windows
        # (cp1251) → кириллица в карточках 829 превращалась в кракозябры. replace → не падаем на
        # неведомом байте, а подставляем �. PYTHONIOENCODING=utf-8 ребёнку выставлен в run_task().
        p = subprocess.run([cbin, "-p", prompt], cwd=cwd, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        raise TimeoutError("claude -p timeout")


def run_task(tid, text, note=""):
    """Исполнить задачу через headless claude -p. → (status, result). status ∈ done|failed|needs_approval.
    Контракт результата (фикс ложного done задачи #24): done ТОЛЬКО при непустом stdout со строкой
    «RESULT: <итог>»; пустой stdout → один авто-повтор (транзиент), снова пустой → failed с хвостом
    stderr; непустой без RESULT → failed insufficient_output. Диагноз (stderr) всегда в карточке+логе."""
    marker_path = os.path.join(REPO, f"pc_ask_{tid}.marker")
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
    env["PYTHONIOENCODING"] = "utf-8"            # ребёнок пишет stdout/stderr в utf-8 → нет кракозябр (пара к encoding в run_claude)
    env[ASK_MARKER_ENV] = marker_path            # pretool_guard в headless пишет сюда красную карточку
    env[MARKER_TOKEN_ENV] = run_token            # …штампуя её нашим токеном — чужие карточки отсеем
    prompt = (note + PREAMBLE) if note else PREAMBLE
    prompt += text
    for attempt in (1, 2):
        try:  # ЧИСТИМ маркер ПЕРЕД КАЖДОЙ попыткой headless (в т.ч. перед авто-повтором):
            if os.path.exists(marker_path):   # иначе красная карточка прошлого прогона протекла бы
                os.remove(marker_path)        # в результат нового (ложное needs_approval).
        except Exception:
            pass
        log.info("RUN id=%s (timeout=%ss, попытка %s/2)", tid, TASK_TIMEOUT, attempt)
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
        out_s = (out or "").strip()
        err_tail = _tail(err)
        if rc != 0:
            return "failed", (f"claude exit={rc}: " + (out_s or err_tail or "нет вывода"))[:RESULT_MAX]
        if not out_s:
            if attempt == 1:   # один авто-повтор: пустой stdout бывает транзиентом
                log.warning("id=%s пустой stdout (rc=0) — авто-повтор; stderr: %s",
                            tid, err_tail or "(пуст)")
                continue
            log.error("id=%s ПУСТОЙ ВЫВОД ×2 → failed; stderr: %s", tid, err_tail or "(пуст)")
            return "failed", ("пустой вывод claude (2 попытки — работа не выполнялась) | stderr: "
                              + (err_tail or "(пуст)"))[:RESULT_MAX]
        if not _RE_RESULT.search(out_s):   # вывод есть, но итог не подтверждён строкой RESULT:
            log.warning("id=%s insufficient_output (нет «RESULT:») → failed", tid)
            return "failed", ("insufficient_output: нет строки «RESULT: <итог>» — выполнение не "
                              "подтверждено. stdout(хвост): " + _tail(out_s)
                              + (" | stderr(хвост): " + err_tail if err_tail else ""))[:RESULT_MAX]
        return "done", out_s[:RESULT_MAX]


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
    # HEAD до задачи (только для дев-задач): диффом head_before..HEAD увидим новые коммиты задачи,
    # чтобы понять, надо ли перезапускать userbot/moderbot (авто-обновление вместо ручной команды).
    head_before = _git_out(["rev-parse", "HEAD"]) if _is_dev_task(text) else None
    status, result = run_task(tid, text)
    if status == "needs_approval":
        bc.set_needs_approval(tid, result)
        log.info("NEEDS_APPROVAL id=%s", tid)
        _cowork(f"задача #{tid} → needs_approval (красное, жду «да»)")
        _notify(_human("needs_approval", tid, result))
    else:
        if status == "done":   # задача успешна → применить свежий код к боту(ам), если рантайм менялся
            result = (result + maybe_update_bots(tid, text, head_before))[:RESULT_MAX]
        bc.complete_task(tid, status, result)
        log.info("COMPLETE id=%s status=%s", tid, status)
        # полный текст RESULT (done) / причины failed → штаб читает итог из cowork_log без скринов
        _cowork(f"задача #{tid} → {status} · {_clip(result)}")
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
            msg = "approve истёк (>30 мин) — повтори задачу"
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} (approved) → failed · {_clip(msg)}")
            _notify(_human("failed", tid, "approve истёк"))
            continue
        status, result = run_task(tid, str(task.get("task_text") or ""),
                                  note="[ОДОБРЕНО ЧЕЛОВЕКОМ] предыдущий шаг подтверждён. ")
        if status == "needs_approval":
            msg = "одобрено, но шаг снова упирается в красное — выполни вручную: " + result[:400]
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} (approved) → failed · {_clip(msg)}")
            _notify(_human("failed", tid, "снова красное после approve — вручную"))
        else:
            bc.complete_task(tid, status, result)
            _cowork(f"задача #{tid} (approved) → {status} · {_clip(result)}")
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
            msg = "подтверждение не получено за 30 мин — задача провалена"
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} → failed · {_clip(msg)}")
            _notify(_human("failed", tid, "подтверждение не получено за 30 мин"))


def poll_once():
    """Один цикл: довести одобренное → добить просроченные ожидания → взять новое → heartbeat."""
    process_approved()
    process_approval_timeouts()
    process_new()
    _write_heartbeat()


# ------------------------------- self-update ---------------------------------
# Порт VPS-паттерна: задача изменила pc_orchestrator.py (блоб HEAD != запущенной версии) →
# гейт (code_gate + unittest своих тестов) → управляемый рестарт: spawn нового процесса →
# лог/cowork «self-update: <старый коммит>→<новый>» → старый выходит. Watchdog — страховка:
# если новый упадёт на ходу, heartbeat протухнет и schtasks /Run поднимет демон.

RUNNING_BLOB = None       # git-блоб pc_orchestrator.py на момент старта («запущенная версия»)
RUNNING_COMMIT = "?"      # короткий коммит на момент старта (для строки self-update в логе)
_SU_REJECTED_BLOB = None  # блоб, уже проваливший гейт — не гоняем гейт каждый цикл, ждём нового коммита


def _git_out(args):
    """git в REPO → stdout.strip() | None (тихо: git недоступен/ошибка — self-update просто молчит)."""
    try:
        p = subprocess.run(["git"] + args, cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def _blob_hash():
    """Хеш содержимого pc_orchestrator.py в HEAD — «версия» кода демона (дифф против запущенной)."""
    return _git_out(["rev-parse", "HEAD:pc_orchestrator.py"])


def _head_commit():
    return _git_out(["rev-parse", "--short", "HEAD"]) or "?"


def _init_running_version():
    global RUNNING_BLOB, RUNNING_COMMIT
    RUNNING_BLOB = _blob_hash()
    RUNNING_COMMIT = _head_commit()


def _gate_unittests():
    """Гейт self-update ступень 2: unittest собственных тестов демона (новым кодом). → (ok, msg)."""
    try:
        p = subprocess.run([VENV_PY, "-m", "unittest", "test_pc_orchestrator"], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600)
        return p.returncode == 0, _tail((p.stderr or "") + (p.stdout or ""), 400)
    except Exception as e:
        return False, f"unittest-гейт не запустился: {e}"


def _spawn_daemon():
    """Поднять НОВЫЙ detached-процесс демона (тот же venv+скрипт). → True/False. Мокается в тестах."""
    try:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([VENV_PY, os.path.join(REPO, "pc_orchestrator.py")], cwd=REPO,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags)
        return True
    except Exception as e:
        log.error("self-update: не смог запустить новый процесс: %s", e)
        return False


def maybe_self_update(blob_fn=None, code_gate=None, tests_gate=None, spawner=None, head_fn=None):
    """→ True = гейт пройден, новый процесс запущен, ТЕКУЩИЙ должен выйти (эстафета передана).
    False = обновляться нечему/нельзя (нет диффа, рубильник, гейт провален, spawn не удался) —
    продолжаем на старом коде. Провал гейта запоминается по блобу (без перегона каждый цикл)."""
    global _SU_REJECTED_BLOB
    if _stopped():
        return False
    new_blob = (blob_fn or _blob_hash)()
    if not new_blob or RUNNING_BLOB is None or new_blob == RUNNING_BLOB:
        return False                        # диффа нет (или git недоступен — не рискуем)
    if new_blob == _SU_REJECTED_BLOB:
        return False                        # этот код уже провалил гейт — ждём следующего коммита
    new_commit = (head_fn or _head_commit)()
    log.info("self-update: pc_orchestrator.py изменился (%s→%s) — гоняю гейт", RUNNING_COMMIT, new_commit)
    ok, msg = (code_gate or self_update_ok)()
    if not ok:
        _SU_REJECTED_BLOB = new_blob
        log.error("self-update: code_gate ПРОВАЛЕН (%s→%s): %s — остаюсь на старом коде",
                  RUNNING_COMMIT, new_commit, msg)
        _notify(f"⚠️ Оркестратор: self-update {RUNNING_COMMIT}→{new_commit} провалил гейт кода — работаю на старом")
        return False
    ok, msg = (tests_gate or _gate_unittests)()
    if not ok:
        _SU_REJECTED_BLOB = new_blob
        log.error("self-update: unittest-гейт ПРОВАЛЕН (%s→%s): %s — остаюсь на старом коде",
                  RUNNING_COMMIT, new_commit, msg)
        _notify(f"⚠️ Оркестратор: self-update {RUNNING_COMMIT}→{new_commit} провалил unittest-гейт — работаю на старом")
        return False
    if not (spawner or _spawn_daemon)():
        log.error("self-update: spawn нового демона НЕ УДАЛСЯ — продолжаю на старом коде "
                  "(упаду — watchdog поднимет через schtasks)")
        return False
    log.info("self-update: %s→%s — гейт пройден, новый процесс запущен, передаю управление", RUNNING_COMMIT, new_commit)
    _cowork(f"self-update: {RUNNING_COMMIT}→{new_commit} (гейт пройден, управляемый рестарт демона)")
    return True


# ------------------- авто-обновление userbot/moderbot после дев-задач ----------
# Убираем ручное «обнови userbot» из темы 205: после done дев-задачи («тз:…»), если она
# создала НОВЫЕ коммиты, затронувшие рантайм-файлы бота → гейт (unittest затронутых тестов)
# → зелено → рестарт бота ТОЙ ЖЕ механикой pc_agent (CIM-поиск PID + taskkill + venv-spawn,
# PID-контроль) → верификация (PID поднялся + лог свежий) → строка в карточку/cowork.
# Гейт красный → НЕ рестартим (код запушен, применится позже). Стоп-флаг уважаем.
# suggest.py/pricing.py импортят ОБА бота → их правка рестартит и userbot, и moderbot.

_USERBOT_PREFIXES = ("userbot", "suggest", "pricing")            # userbot_listen → suggest → pricing
_MODERBOT_PREFIXES = ("moderation", "moderbot", "suggest", "pricing")  # moderation_bot → suggest/pricing
_RE_DEV_TASK = re.compile(r"^\s*тз\b", re.I)   # дев-задача: текст начинается с «тз:/тз …»


def _is_dev_task(text):
    """Дев-задача («тз:…») — только после таких обновляем боты (обычные задачи не трогают рантайм)."""
    return bool(_RE_DEV_TASK.match(str(text or "")))


def _changed_files_since(head_before):
    """Файлы, изменённые НОВЫМИ коммитами задачи (head_before..HEAD). → список путей.
    Нет head_before / git молчит / нет новых коммитов → [] (обновлять нечего)."""
    if not head_before:
        return []
    head_now = _git_out(["rev-parse", "HEAD"])
    if not head_now or head_now == head_before:
        return []
    out = _git_out(["diff", "--name-only", f"{head_before}..{head_now}"])
    if not out:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _classify_changed(paths):
    """Разложить изменённые файлы по цели рестарта. → (userbot_files, moderbot_files).
    Файл может попасть в обе группы (suggest/pricing — общий рантайм)."""
    ub, mb = [], []
    for p in paths:
        name = os.path.basename(p).lower()
        if name.startswith(_USERBOT_PREFIXES):
            ub.append(p)
        if name.startswith(_MODERBOT_PREFIXES):
            mb.append(p)
    return ub, mb


def _affected_test_modules(paths):
    """Тест-модули для изменённых .py: сам test_*.py и test_<stem> при наличии на диске. → отсортированный список."""
    mods = set()
    for p in paths:
        base = os.path.basename(p)
        if not base.endswith(".py"):
            continue
        stem = base[:-3]
        if stem.startswith("test_"):
            mods.add(stem)
            continue
        cand = "test_" + stem
        if os.path.isfile(os.path.join(REPO, cand + ".py")):
            mods.add(cand)
    return sorted(mods)


def _gate_test_modules(mods):
    """Гейт обновления бота: unittest затронутых тест-модулей новым кодом. → (ok, msg).
    Нет затронутых тестов → (True, '…') — рестарт без гейта (config-правка; код уже прошёл тесты в задаче)."""
    if not mods:
        return True, "нет затронутых тестов — рестарт без гейта"
    try:
        p = subprocess.run([VENV_PY, "-m", "unittest", *mods], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600)
        return p.returncode == 0, _tail((p.stderr or "") + (p.stdout or ""), 400)
    except Exception as e:
        return False, f"unittest-гейт не запустился: {e}"


def _log_size(path):
    try:
        return os.path.getsize(str(path))
    except Exception:
        return -1


def _restart_via_pc_agent(kind, settle=0.5, wait_cycles=20):
    """Рестарт бота ТОЙ ЖЕ механикой pc_agent: стоп (CIM-поиск PID + taskkill) → дождаться
    чистоты → старт через venv-python → PID-контроль. kind ∈ 'userbot'|'moderbot'.
    → (ok, pids, detail). Реальные процессы — в тестах функция мокается целиком."""
    try:
        import pc_agent   # lazy: не тянем telegram в импорт оркестратора
    except Exception as e:
        return False, [], f"не смог импортировать механику pc_agent: {e}"
    if kind == "userbot":
        proc, finder, logfile = pc_agent.UserbotProcess(), pc_agent._find_userbot_pids, pc_agent.USERBOT_LOG
    else:
        if not os.getenv("MODERBOT_TOKEN", "").strip():
            # модербот без токена не поднимается (reply-режим userbot) — рестарт не требуется,
            # новый код применится сам, когда/если модербот запустят с токеном.
            return True, [], "модербот в reply-режиме (нет MODERBOT_TOKEN) — рестарт не требуется"
        proc, finder, logfile = pc_agent.ModerbotProcess(), pc_agent._find_moderbot_pids, os.path.join(REPO, "moderation_bot.log")
    try:
        proc.stop()
        for _ in range(wait_cycles):        # ждём смерти старых экземпляров (не поднимаем поверх живого)
            if not finder():
                break
            time.sleep(settle)
        size_before = _log_size(logfile)
        start_msg = proc.start()
        pids = finder()
        if not pids:
            return False, [], f"процесс не поднялся: {start_msg}"
        fresh = _log_size(logfile) > size_before   # свежая строка лога = файл вырос после старта
        return True, pids, ("PID поднят, лог свежий" if fresh else f"PID поднят, лог без новых строк ({start_msg})")
    except Exception as e:
        return False, [], f"ошибка рестарта: {e}"


def maybe_update_bots(tid, text, head_before, changed_fn=None, gate_fn=None,
                      restart_fn=None, is_dev_fn=None, head_fn=None):
    """После done дев-задачи применить свежий код к боту(ам). → строка-суффикс для карточки/cowork
    ('' если обновлять нечего). Уважает стоп-флаг. Всё внешнее инъектируется для тестов."""
    if _stopped():
        return ""
    if not (is_dev_fn or _is_dev_task)(text):
        return ""
    changed = (changed_fn or _changed_files_since)(head_before)
    if not changed:
        return ""
    ub_files, mb_files = _classify_changed(changed)
    if not (ub_files or mb_files):
        return ""
    commit = (head_fn or _head_commit)()
    notes = []
    for kind, label, files in (("userbot", "userbot", ub_files), ("moderbot", "модербот", mb_files)):
        if not files:
            continue
        mods = _affected_test_modules(files)
        ok, gmsg = (gate_fn or _gate_test_modules)(mods)
        if not ok:
            log.error("авто-обновление %s: гейт КРАСНЫЙ (%s) — рестарт отложен", kind, gmsg)
            notes.append(f"{label}: код запушен, рестарт отложен: тесты красные ({_tail(gmsg, 200)})")
            _notify(f"⚠️ Оркестратор: {label} НЕ перезапущен — тесты красные (код запушен, применится после фикса)")
            continue
        rok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
        if rok and pids:
            log.info("авто-обновление %s: обновлён до %s, PID %s", kind, commit, pids)
            notes.append(f"{label} обновлён до {commit}, PID {', '.join(map(str, pids))}")
        elif rok:                               # рестарт не требовался (напр. модербот без токена)
            log.info("авто-обновление %s: %s", kind, detail)
            notes.append(f"{label}: {detail}")
        else:
            log.error("авто-обновление %s: рестарт НЕ УДАЛСЯ — %s", kind, detail)
            notes.append(f"{label}: код запушен, рестарт НЕ удался — {_tail(detail, 200)}")
            _notify(f"⚠️ Оркестратор: {label} — рестарт не удался после обновления: {_tail(detail, 200)}")
    return (" | авто-обновление: " + " ; ".join(notes)) if notes else ""


def main():
    _init_running_version()
    log.info("=== ДЕМОН СТАРТ (lane=%s, poll=%ss, task_timeout=%ss, approval_ttl=%ss, claude=%s, commit=%s) ===",
             LANE, POLL_SEC, TASK_TIMEOUT, APPROVAL_TTL, CLAUDE_BIN, RUNNING_COMMIT)
    if _stopped():
        log.info("рубильник pc_orchestrator.stop активен — не стартую поллинг")
        return
    while not _stopped():
        try:
            poll_once()
            if maybe_self_update():   # задача цикла обновила pc_orchestrator.py → эстафета новому
                log.info("=== ДЕМОН ВЫШЕЛ ПО SELF-UPDATE (эстафета новому процессу) ===")
                return
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
        p = subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=30)   # utf-8: русский вывод schtasks читаем в логе
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
