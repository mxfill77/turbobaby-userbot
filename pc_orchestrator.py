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
import tempfile
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
# ПК-side ливнесс ОДИНОЧЕК lane=pc (развязка 328-pc, этап 2): одиночка, застрявшая в in_progress
# дольше этого порога (ПК был выключен / процесс умер посреди прогона / self-update-гонка) →
# честный failed вместо тихого вечного зависания. Порог СТРОГО > TASK_TIMEOUT (45 мин): демон
# исполняет одну задачу за раз СИНХРОННО и в пределах TASK_TIMEOUT завершает статус ≠ in_progress,
# поэтому живой прогон под нож не попадёт — реапится только орфан мёртвого процесса. vps-реапер НЕ
# дублируем: он про VPS/цепочки, а тут ТОЛЬКО одиночки lane=pc, которых он не видит.
PC_SINGLE_STALE = int(os.getenv("PC_SINGLE_STALE", "5400") or "5400")  # 90 мин: орфан-одиночка in_progress → failed
NEEDS_APPROVAL_TOPIC = int(os.getenv("PC_NA_TOPIC", "829") or "829")   # тема, куда Splinter постит карточку
HEARTBEAT_STALE = int(os.getenv("PC_HB_STALE", "180") or "180")        # watchdog: heartbeat протух
WATCH_VERIFY_SLEEP = int(os.getenv("PC_WATCH_VERIFY", "20") or "20")
# Контур-вотчдог клиентского контура (разбор #128, часть 3): демон — единственный надёжно
# выживающий процесс (его самого держит heartbeat+schtasks), поэтому он же следит за
# pc_agent/userbot/moderation_bot и поднимает мёртвых.
CLIENT_WATCH_SEC = int(os.getenv("PC_CLIENT_WATCH_SEC", "300") or "300")        # проверка контура каждые 5 мин
CLIENT_COOLDOWN_SEC = int(os.getenv("PC_CLIENT_COOLDOWN_SEC", "900") or "900")  # анти-флап: ≤1 подъём/процесс за 15 мин
CLIENT_MAX_DEATHS = int(os.getenv("PC_CLIENT_MAX_DEATHS", "3") or "3")          # 3 смерти подряд → стоп + громкий NOTE
# Фикс КЛАССА (вердикт #171): «не смог проверить» ≠ «мёртв». На просыпающемся/тормозящем ПК CIM-поиск
# finder'а падает по таймауту → раньше возвращал [] → вотчдог считал процесс мёртвым → лишний старт →
# дубль → Conflict (синглтон). Теперь: finder РАЗЛИЧАЕТ error/timeout от честной пустоты; рестарт
# требует ТРЁХ условий (finder успешен + процесса нет + лог протух); grace после пробуждения; алярм
# на слепоту вместо рестартов. Пороги — только СТРОЖЕ к рестарту, не слабее.
CLIENT_LOG_STALE = int(os.getenv("PC_CLIENT_LOG_STALE", "120") or "120")        # свежий лог (≤ этого) ВЕТИРУЕТ рестарт
CLIENT_BLIND_ALARM = int(os.getenv("PC_CLIENT_BLIND_ALARM", "3") or "3")        # N слепых циклов подряд → NOTE «вотчдог слеп»
WAKE_GRACE_SEC = int(os.getenv("PC_WAKE_GRACE", "120") or "120")                # после пробуждения ПК — окно без вердиктов
WAKE_JUMP_MARGIN = int(os.getenv("PC_WAKE_JUMP_MARGIN", "60") or "60")          # скачок wall-clock > POLL+это → «ПК проснулся»
RESULT_MAX = 4500
TIMEOUT_MARK = "⏱"       # маркер таймаут/сирота-диагнозов: думатель самопочинки их НЕ чинит
MANUAL_MARK = "✋"        # маркер «headless доказанно не может» (снова красное ПОСЛЕ approve) —
                         # зеркало ручной карты VPS «✋ ТРЕБУЕТСЯ РУЧНОЕ ДЕЙСТВИЕ»: думатель НЕ чинит
                         # (переформулировка родила бы петлю ре-аппрувов), цепь = halt
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")   # ФОЛБЭК: явный путь из .env (может протухнуть при автообновлении)
# Базовая папка версионных установок claude-code (AppData\Roaming\Claude\claude-code\<версия>\claude.exe).
# Резолвим НОВЕЙШУЮ установку сами → путь переживает автообновление, даже когда .env-путь протух (WinError 2).
_CLAUDE_BASE = os.path.join(os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
                            "Claude", "claude-code")

STOP_FLAG = os.path.join(REPO, "pc_orchestrator.stop")
HEARTBEAT_FILE = os.path.join(REPO, "pc_orchestrator.heartbeat")
LOCK_FILE = os.path.join(REPO, "pc_orchestrator.lock")       # OS-синглтон демона (разбор #128, часть 4)
SUPERSEDE_ENV = "PC_ORCH_SUPERSEDE_PID"                       # self-update: PID старого, которого сменяем
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


def _claude_candidates():
    """Явные кандидаты бинаря по ВСЕМ известным схемам установки claude-code (на случай, если
    автообновление сменит место): native-инсталлер (~/.local/bin), LOCALAPPDATA\\Programs, npm-шим."""
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    return [
        os.path.join(home, ".local", "bin", "claude.exe"),            # native-инсталлер (новая схема)
        os.path.join(home, ".local", "bin", "claude.cmd"),
        os.path.join(local, "Programs", "claude", "claude.exe"),      # LOCALAPPDATA\Programs
        os.path.join(local, "Programs", "claude-code", "claude.exe"),
        os.path.join(appdata, "npm", "claude.cmd"),                   # npm -g шим
    ]


def _claude_base_dirs():
    """Базы версий claude-code: обычная Roaming\\Claude\\claude-code + РЕАЛЬНАЯ MSIX-база
    AppData\\Local\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude\\claude-code. У Store/MSIX-установки
    Roaming\\Claude — виртуальный редирект, видимый ТОЛЬКО в интерактивной сессии; демон запущен
    Планировщиком и его не видит (FileNotFoundError). Реальный путь в Packages доступен всегда."""
    out = [_CLAUDE_BASE]
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(os.getenv("USERPROFILE") or home, "AppData", "Local")
    try:
        for cc in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache",
                                         "Roaming", "Claude", "claude-code")):
            if cc not in out:
                out.append(cc)
    except Exception:
        pass
    return out


def _resolve_claude_once():
    """Один проход по всем местам (без ретраев). → путь | None. Порядок:
      1) PATH-шим: shutil.which('claude') — учитывает PATHEXT (claude.cmd/.exe/.bat);
      2) CLAUDE_BIN из .env — только если файл реально существует;
      3) НОВЕЙШАЯ версия claude-code по всем базам (вкл. реальную MSIX-Packages — фикс «не найден в бою»);
      4) явные кандидаты других схем установки (_claude_candidates)."""
    w = shutil.which("claude")                       # 1) PATH-шим (.cmd/.exe через PATHEXT)
    if w and os.path.isfile(w):
        return w
    if CLAUDE_BIN and os.path.isabs(CLAUDE_BIN) and os.path.isfile(CLAUDE_BIN):  # 2) .env, если жив
        return CLAUDE_BIN
    try:                                             # 3) новейшая версионная установка (все базы)
        cands = []
        for base in _claude_base_dirs():
            for d in glob.glob(os.path.join(base, "*")):
                exe = os.path.join(d, "claude.exe")
                if os.path.isfile(exe):
                    cands.append((_ver_key(os.path.basename(d)), exe))
        if cands:
            cands.sort()
            return cands[-1][1]
    except Exception:
        pass
    for c in _claude_candidates():                   # 4) другие схемы установки
        if os.path.isfile(c):
            return c
    return None


def resolve_claude(retries=1, retry_sleep=2.0):
    """Найти исполняемый claude БЕЗ привязки к версии/месту установки. Кэшируем, но перепроверяем
    существование (переживаем автообновление). НЕ сдаёмся с первого прохода: transient-недоступность
    диска (стейджинг автообновления/антивирус — кейс задачи #35: файл был на месте, а isfile мигнул
    False) → короткий ретрай. → путь (str) | None (после ретраев — реально нигде нет)."""
    global _claude_cache
    if _claude_cache and os.path.isfile(_claude_cache):
        return _claude_cache
    _claude_cache = None
    for i in range(retries + 1):
        p = _resolve_claude_once()
        if p:
            _claude_cache = p
            return p
        if i < retries:
            log.warning("resolve_claude: не найден (проход %s: PATH/CLAUDE_BIN/%s/кандидаты) — "
                        "транзиент? повтор через %sс", i + 1, _CLAUDE_BASE, retry_sleep)
            time.sleep(retry_sleep)
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
        msg = ("claude CLI не найден (после ретрая): нет ни в PATH, ни в CLAUDE_BIN (.env), ни в "
               + os.path.join(_CLAUDE_BASE, "<версия>", "claude.exe")
               + ", ни в кандидатах (~/.local/bin, LOCALAPPDATA\\Programs, npm). "
               "Проверь установку/автообновление claude-code.")
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
            return "failed", (f"{TIMEOUT_MARK} таймаут {TASK_TIMEOUT}s — headless прерван, "
                              "задача не завершилась")
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
            # класс «самомодификация → ложный failed» (порт 48d9c64): claude погашен ПЛАНОВЫМ
            # self-update-рестартом демона (4 признака) — это НЕ сбой → done, думателя НЕ зовём.
            # Под флагом STEP_SELFHEAL (=0 → байт-в-байт прежний failed ниже).
            if _selfheal_on() and _killed_by_planned_restart(rc, text):
                log.info("id=%s claude погашен ПЛАНОВЫМ рестартом демона (rc=%s) → done (плановый)", tid, rc)
                return "done", ("🔁 Завершено плановым рестартом демона (самомодификация "
                                "pc_orchestrator): claude-процесс задачи штатно погашен в окне "
                                "управляемого self-update-рестарта — работа к этому моменту сделана "
                                "(RESULT в логе, коммит в git). Это НЕ сбой.")[:RESULT_MAX]
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


# ------------------- команды-рычаги (прямое исполнение без headless) ----------
# Одиночные lane=pc задачи-команды («рестартни userbot» / «рестартни модербот» / «статус контура»)
# демон исполняет САМ (полномочия вотчдога), мгновенно, без headless claude. Распознавание — по
# ЯКОРНЫМ паттернам (НЕ LLM, НЕ подстрока): совпадает, только когда ВЕСЬ текст задачи и есть команда,
# поэтому дев-задачи («тз: … рестартни …») и путь темы 205 не перехватываются. pc_agent командой не
# рестартим (агент себя чужими руками не трогает).
_CMD_RESTART_UB = re.compile(r"^\s*(?:рестартни|рестарт|перезапусти|restart)\s+(?:userbot|юзербот)\s*[.!]*\s*$", re.I)
_CMD_RESTART_MB = re.compile(r"^\s*(?:рестартни|рестарт|перезапусти|restart)\s+(?:модербот|moderbot|moderation[_ ]?bot|модербот)\s*[.!]*\s*$", re.I)
_CMD_STATUS = re.compile(r"^\s*статус\s+контура\s*[.!?]*\s*$", re.I)


def _match_command(text):
    """Распознать команду-рычаг по якорным паттернам (весь текст = команда). →
    'restart_userbot'|'restart_moderbot'|'status' | None (не команда → обычный headless-путь)."""
    t = str(text or "")
    if _CMD_STATUS.match(t):
        return "status"
    if _CMD_RESTART_UB.match(t):
        return "restart_userbot"
    if _CMD_RESTART_MB.match(t):
        return "restart_moderbot"
    return None


def _proc_line(label, pids, extra=""):
    base = (f"{label}: жив (PID {', '.join(map(str, pids))})" if pids else f"{label}: НЕ ЖИВ")
    return base + (f" · {extra}" if extra else "")


def _contour_status(finder=None):
    """Статус клиентского контура: живость+PID userbot/moderation_bot/pc_agent/pc_orchestrator и
    свежесть heartbeat демона. → многострочный текст (в результат задачи). finder — для тестов."""
    find = finder or _find_pids_by_script
    ub, mb = find("userbot_listen.py"), find("moderation_bot.py")
    ag, orch = find("pc_agent.py"), find("pc_orchestrator.py")
    mb_extra = "" if mb else ("нет MODERBOT_TOKEN → reply-режим (штатно)"
                              if not os.getenv("MODERBOT_TOKEN", "").strip() else "")
    try:
        hb = open(HEARTBEAT_FILE, encoding="utf-8").read().strip()
        age = _age_sec(hb)
        hb_txt = ("нет" if age is None else
                  f"свеж ({int(age)}с назад)" if age <= HEARTBEAT_STALE else f"ПРОТУХ ({int(age)}с назад)")
    except Exception:
        hb_txt = "нет"
    return "\n".join(["📊 Статус контура:",
                      _proc_line("userbot", ub),
                      _proc_line("moderation_bot", mb, mb_extra),
                      _proc_line("pc_agent", ag),
                      _proc_line("pc_orchestrator", orch, f"heartbeat {hb_txt}")])


def _exec_command(cmd, restart_fn=None, status_fn=None):
    """Исполнить команду-рычаг НАПРЯМУЮ (полномочия вотчдога), без headless. → (status, result).
    Рестарт уважает рубильник и штампует анти-флап-реестр (не воюет с авто-применением кода)."""
    if cmd == "status":
        return "done", (status_fn or _contour_status)()
    kind = "userbot" if cmd == "restart_userbot" else "moderbot"
    if _stopped():
        return "failed", "рубильник pc_orchestrator.stop активен — рестарт не выполняю"
    try:
        ok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
    except Exception as e:
        return "failed", f"{kind}: исключение рестарта: {e}"
    _stamp_apply_restart(kind)
    if ok and pids:
        return "done", f"🔁 {kind} перезапущен напрямую (рычаг вотчдога): {detail}, PID {', '.join(map(str, pids))}"
    if ok:
        return "done", f"🔁 {kind} (рычаг): {detail}"
    return "failed", f"{kind}: рестарт не удался — {detail}"


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
    cmd = _match_command(text)                 # команда-рычаг? исполняем САМИ, без headless claude
    if cmd:
        status, result = _exec_command(cmd)
        bc.complete_task(tid, status, result)
        log.info("COMMAND id=%s cmd=%s → %s", tid, cmd, status)
        _cowork(f"задача #{tid} (рычаг {cmd}) → {status} · {_clip(result)}")
        _notify(_human(status, tid, result))
        return
    # Локальный дирижёр (PC_LOCAL_DEC=1): осиротевшая synthetic (сводка/карточка — демон упал
    # между enqueue и complete) → довести done, НЕ исполняя; родитель Filipp-pcloc-dec → строим
    # план, НЕ исполняем как обычную задачу. Флаг off → False сразу (поведение байт-в-байт
    # прежнее; from=Filipp-pcloc-dec до порта не существовал — чужого не задеваем).
    frm = str(task.get("from") or "")
    if frm == PC_LOCAL_DEC_FROM and _loc_finalize_orphan_synthetic(tid, text):
        return
    if _is_local_dec_parent(frm, text):
        _local_dec_plan(tid, text)
        return
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
        # САМОПОЧИНКА (STEP_SELFHEAL=1): провал ОДИНОЧНОЙ задачи lane=pc → думатель, РОВНО 1 попытка.
        # True = финализировано внутри (перерождение / терминальный failed с диагнозом); False =
        # прежний путь (fail-safe: флаг off / сбой думателя / очередь не приняла). Байт-в-байт
        # прежнее поведение при STEP_SELFHEAL=0 (короткое замыкание в _maybe_selfheal).
        if status == "failed" and _maybe_selfheal(tid, text, result, frm=str(task.get("from") or "")):
            return
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
            # ⏱ первым символом: для шага локальной цепи просрочка approve = halt без думателя
            # (гейт _loc_after_fail; переформулировка не вернёт ушедшего Филиппа)
            msg = f"{TIMEOUT_MARK} approve истёк (>30 мин) — повтори задачу"
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} (approved) → failed · {_clip(msg)}")
            _notify(_human("failed", tid, "approve истёк"))
            continue
        status, result = run_task(tid, str(task.get("task_text") or ""),
                                  note="[ОДОБРЕНО ЧЕЛОВЕКОМ] предыдущий шаг подтверждён. ")
        if status == "needs_approval":
            # ✋ первым символом (зеркало ручной карты VPS): headless красное ДОКАЗАННО не проходит
            # даже после «да» — для шага локальной цепи это терминальный halt без думателя
            # (гейт _loc_after_fail; ре-аппрув/переформулировка = петля, рвём после ровно 1 круга)
            msg = (f"{MANUAL_MARK} одобрено, но шаг снова упирается в красное — выполни вручную: "
                   + result[:400])
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
            msg = (f"{TIMEOUT_MARK} подтверждение не получено за 30 мин — задача провалена")
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} → failed · {_clip(msg)}")
            _notify(_human("failed", tid, "подтверждение не получено за 30 мин"))


# ---------------- ПРЯМОЙ КАНАЛ ПК↔Bridge для ОДИНОЧЕК pc (развязка 328-pc, этап 1) --------------
# Поставить ОДИНОЧНУЮ lane=pc задачу прямо в очередь Bridge СУЩЕСТВУЮЩИМ экшеном enqueue_task,
# МИНУЯ девбот-в-splinter (тема 328). Демон claim'ит её обычным поллингом (process_new). Это
# ДОПОЛНИТЕЛЬНЫЙ путь, НЕ замена темы 328/девбота (тот остаётся рабочим). Инвариант: даже когда
# Splinter/девбот лежат — и enqueue, и claim идут ПК→Bridge напрямую, поэтому одиночка pc всё равно
# принимается и исполняется. lane ЖЁСТКО == LANE ('pc') — чужие полосы не трогаем. Эмпирически
# проверено с ПК: enqueue_task → id, get_pending видит, claim берёт (RECON развязки 328-pc).

def enqueue_pc_task(text, frm="Filipp", bridge=None):
    """ПРЯМОЙ enqueue одиночки lane=pc в очередь Bridge (существующий экшен, без нового Bridge-кода).
    Детерминированно, lane жёстко 'pc'. → (ok: bool, id|None, err|None). Демон подхватит её обычным
    process_new — enqueue+claim идут напрямую ПК→Bridge, независимо от Splinter/девбота (инвариант)."""
    t = str(text or "").strip()
    if not t:
        return False, None, "пустой текст задачи"
    b = bridge if bridge is not None else bc
    r = b.enqueue_task(frm or "Filipp", t, lane=LANE)   # LANE == 'pc' строго (чужие полосы не создаём)
    if not r.get("ok"):
        return False, None, str(r.get("error") or "enqueue отклонён Bridge")
    nid = r.get("id")
    log.info("direct-enqueue: одиночка lane=%s поставлена в очередь id=%s (минуя splinter)", LANE, nid)
    return True, nid, None


# ---------------- ПК-side таймаут ОДИНОЧЕК pc (развязка 328-pc, этап 2) --------------------------

def process_stuck_singles(now=None):
    """ПК-side ливнесс одиночек lane=pc: задача, застрявшая в in_progress дольше PC_SINGLE_STALE
    (ПК был выключен / процесс умер посреди прогона / гонка self-update), → честный failed с видимой
    пометкой вместо тихого вечного зависания. НЕ дублирует и НЕ ослабляет vps-реапер: тот про
    VPS/цепочки, здесь ТОЛЬКО одиночки lane=pc, которых он не видит. Порог > TASK_TIMEOUT, поэтому
    живой синхронный прогон демона (≤45 мин) под нож не попадёт — реапится лишь орфан мёртвого
    процесса. lane СТРОГО (чужие полосы не трогаем: _lane_ok). updated=None → не реапим (fail-safe,
    идиома `or 0` как в process_approval_timeouts)."""
    if _stopped():
        return
    r = bc.get_pending("in_progress")
    if not r.get("ok"):
        log.warning("get_pending(in_progress) ошибка: %s", r.get("error"))
        return
    now = now or datetime.datetime.now(datetime.timezone.utc)
    for task in [it for it in r.get("items", []) if _lane_ok(it)]:
        tid = task.get("id")
        age = _age_sec(task.get("updated"), now=now) or 0
        if age <= PC_SINGLE_STALE:
            continue
        msg = (f"{TIMEOUT_MARK} ПК-таймаут одиночки: задача провисела in_progress {int(age)}с (> {PC_SINGLE_STALE}с) — "
               "ПК был выключен, либо прогон застрял/оборвался посреди исполнения. Помечена failed "
               "ПК-ливнессом (не зависает вечно). Повтори при необходимости.")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.warning("stuck-single: id=%s in_progress %sс > %sс → failed (ПК-ливнесс одиночки)",
                    tid, int(age), PC_SINGLE_STALE)
        _cowork(f"задача #{tid} (одиночка) → failed по ПК-таймауту ({int(age)}с) · {_clip(msg)}")
        _notify(_human("failed", tid, "ПК-таймаут одиночки (застряла in_progress)"))


def poll_once():
    """Один цикл: добить орфанов-одиночек → довести одобренное → просроченные ожидания →
    надзор локальных цепей (PC_LOCAL_DEC) → новое → heartbeat."""
    process_stuck_singles()       # этап 2: ПК-side ливнесс одиночек pc, застрявших в in_progress
    process_approved()
    process_approval_timeouts()
    process_local_chains()        # локальный дирижёр: done-шаг → релиз следующего, финал → сводка
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
    """Гейт self-update ступень 2: unittest собственных тестов демона (новым кодом). → (ok, msg).
    env-очистка THINKER_MODEL/FALLBACK: гейт обязан проверять новый код против ТЕКУЩЕГО .env
    (каким его перечитает перезапущенный демон), а НЕ против значения, унаследованного в память
    ЭТОГО демона со старта. Иначе прежний короткий алиас модели в env демона ронял бы golden-тест
    и блокировал self-update, а перечитать .env демон может только рестартом → дедлок (хвост #194)."""
    try:
        env = dict(os.environ)
        for k in ("THINKER_MODEL", "THINKER_FALLBACK"):
            env.pop(k, None)                     # подпроцесс перечитает их из .env (load_dotenv)
        p = subprocess.run([VENV_PY, "-m", "unittest", "test_pc_orchestrator", "test_pc_local_dec"], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, env=env)
        return p.returncode == 0, _tail((p.stderr or "") + (p.stdout or ""), 400)
    except Exception as e:
        return False, f"unittest-гейт не запустился: {e}"


def _spawn_daemon():
    """Поднять НОВЫЙ detached-процесс демона (тот же venv+скрипт). → True/False. Мокается в тестах.
    Передаём PC_ORCH_SUPERSEDE_PID=<свой PID>: новый в acquire_singleton дождётся смерти старого
    (нас) прежде чем забрать лок — эстафета без перекрытия (разбор #128, часть 4)."""
    try:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        env = dict(os.environ)
        env[SUPERSEDE_ENV] = str(os.getpid())
        subprocess.Popen([VENV_PY, os.path.join(REPO, "pc_orchestrator.py")], cwd=REPO,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags, env=env)
        return True
    except Exception as e:
        log.error("self-update: не смог запустить новый процесс: %s", e)
        return False


def maybe_self_update(blob_fn=None, code_gate=None, tests_gate=None, spawner=None, head_fn=None,
                      children_fn=None):
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
    # Дети self-update: применить свежий код к userbot/moderbot по диффу RUNNING_COMMIT..new_commit
    # ДО передачи эстафеты (pc_agent — только пометка). Делаем это в СТАРОМ процессе (у него есть
    # ссылка на прежний коммит и общий анти-флап-реестр с maybe_update_bots — не дёрнем повторно то,
    # что дев-задача уже рестартнула). Best-effort: сбой рестарта детей НЕ блокирует хендовер демона
    # (дети — отдельные процессы; если что — их подхватит контур-вотчдог нового демона).
    try:
        (children_fn or _selfupdate_restart_children)(RUNNING_COMMIT, new_commit)
    except Exception as e:
        log.warning("self-update: реконсиляция детей упала (не блокирует хендовер): %s", e)
    if not (spawner or _spawn_daemon)():
        log.error("self-update: spawn нового демона НЕ УДАЛСЯ — продолжаю на старом коде "
                  "(упаду — watchdog поднимет через schtasks)")
        return False
    log.info("self-update: %s→%s — гейт пройден, новый процесс запущен, передаю управление", RUNNING_COMMIT, new_commit)
    _cowork(f"self-update: {RUNNING_COMMIT}→{new_commit} (гейт пройден, управляемый рестарт демона)")
    return True


# ------------------- САМОПОЧИНКА ОДИНОЧНЫХ задач (флаг STEP_SELFHEAL) -----------
# Порт VPS-образцов: самопочинка одиночных (9b0a496) + «думатель» (784e453) + класс «плановый
# рестарт ≠ падение» (48d9c64). Под флагом STEP_SELFHEAL: провал ОДИНОЧНОЙ задачи lane=pc,
# которая НЕ красный NEEDS_APPROVAL (отсечён раньше в run_task→process_new) и НЕ плановый
# self-update-рестарт (см. _killed_by_planned_restart ниже — стал done внутри run_task), →
# локальный ДУМАТЕЛЬ (тот же кондуктор-механизм --fallback-model, чистый генератор --max-turns 1),
# но со СВОЕЙ парой моделей THINKER_MODEL/THINKER_FALLBACK — НЕ завязан на SUGGEST_MODEL клиентского
# suggest (у думателя своя задача: строгий диагноз, не клиентский черновик) → строгий JSON
# {verdict:retry|halt, fixed_task, reason} →
#   retry: РОВНО одно перерождение «[самопочинка задачи N, попытка 1] …» в очередь lane=pc + карточка;
#   повторный провал уже МАРКИРОВАННОЙ задачи = терминальный failed (маркер = стоп, петля невозможна);
#   halt: сразу терминальный failed с причиной.
# FAIL-SAFE: ЛЮБОЙ сбой думателя (не-JSON / таймаут / пусто / исключение / очередь не приняла) =
# прежний ГОЛЫЙ failed (не хуже базового поведения). STEP_SELFHEAL=0/нет → ветка не зовётся вовсе
# (поведение байт-в-байт прежнее). Красное НЕ ослаблено: думатель ничего не исполняет
# (--allowed-tools '' + нейтральный cwd → без settings.json/pretool_guard), тема 829/инбокс 1160 нетронуты.
STEP_SELFHEAL_TIMEOUT = int(os.getenv("PC_SELFHEAL_TIMEOUT", "180") or "180")   # думатель — короткий ответ
THINKER_MODEL = os.getenv("THINKER_MODEL", "claude-fable-5").strip() or "claude-fable-5"  # своя голова думателя (НЕ SUGGEST_MODEL); ПОЛНЫЙ id — короткий алиас «fable-5» даёт 404 на claude -p (родитель #194)
THINKER_FALLBACK = os.getenv("THINKER_FALLBACK", "claude-opus-4-8").strip()     # свой фолбэк думателя; ПОЛНЫЙ id — «opus-4.8» даёт 404 (родитель #194)
# Маркер перерождения одиночной задачи стоит ПЕРВЫМ в тексте → якорь ^ (страховка от ложного
# срабатывания на ТЗ, где маркер лишь упомянут в теле). N = id исходной задачи.
_HEAL_TASK_RE = re.compile(r"^\s*\[самопочинка задачи (\d+), попытка (\d+)\]")
# Конверт одобренной заявки (маркер ОБЯЗАН стоять ПЕРВЫМ — якорь ^): самопочинка его НЕ трогает,
# перерождение сдвинуло бы маркер и сломало бы разрыв петли ре-конвертов (спека, часть 2/2 §1).
_CONVERT_RE = re.compile(r"^\s*\[конверт одобренной заявки\b")
# Признак самомодификации демона для класса «плановый рестарт ≠ падение».
_SELFMOD_RE = re.compile(r"pc[-_ ]?orchestrator|самомодифика", re.IGNORECASE)

# Преамбула думателя ШАГА локальной цепи (порт THINKER_PREAMBLE VPS, спека часть 2/2 §1):
# та же схема/строгость, но ключ fixed_step и контекст с родителем/планом (даёт _loc_selfheal_consult).
THINKER_PREAMBLE = (
    "Ты — думательный слой самопочинки ПК-оркестратора TurboBaby (мета-дирижёр). Шаг декомпозиции "
    "упал при исполнении. Твоя задача — ТОЛЬКО диагноз и вердикт; ты НИЧЕГО не исполняешь, "
    "инструментов у тебя нет, файлы не читаешь — решай строго по данным ниже.\n"
    "Ответь СТРОГО ОДНИМ JSON-объектом, без текста до/после, без markdown-обёртки:\n"
    '{"verdict":"retry"|"halt","fixed_step":"<новая формулировка шага>","reason":"<1 строка диагноза>"}\n'
    "verdict=retry — ТОЛЬКО если провал починим переформулировкой шага (неверный путь/имя файла, "
    "недостающий контекст, кривая команда) и правка очевидна; fixed_step тогда — САМОДОСТАТОЧНОЕ "
    "дев-ТЗ ≤400 символов (исполнитель увидит ТОЛЬКО его, впиши нужный контекст). Во всех прочих "
    "случаях (причина неясна, нужен человек, красная зона, объём не влезает в таймаут) — "
    "verdict=halt и fixed_step пустой. Система даёт РОВНО ОДНУ попытку починки — не предлагай "
    "многошаговых планов.\n\n"
)
TASK_THINKER_PREAMBLE = (
    "Ты — думательный слой самопочинки ПК-оркестратора TurboBaby (мета-дирижёр). Одиночная "
    "headless-задача упала при исполнении. Твоя задача — ТОЛЬКО диагноз и вердикт; ты НИЧЕГО "
    "не исполняешь, инструментов у тебя нет, файлы не читаешь — решай строго по данным ниже.\n"
    "Ответь СТРОГО ОДНИМ JSON-объектом, без текста до/после, без markdown-обёртки:\n"
    '{"verdict":"retry"|"halt","fixed_task":"<новая формулировка задачи>","reason":"<1 строка диагноза>"}\n'
    "verdict=retry — ТОЛЬКО если провал починим переформулировкой задачи (неверный путь/имя файла, "
    "недостающий контекст, кривая команда) и правка очевидна; fixed_task тогда — САМОДОСТАТОЧНОЕ "
    "дев-ТЗ ≤400 символов (исполнитель увидит ТОЛЬКО его, впиши нужный контекст). Во всех прочих "
    "случаях (причина неясна, нужен человек, красная зона, объём не влезает в таймаут) — "
    "verdict=halt и fixed_task пустой. Система даёт РОВНО ОДНУ попытку починки — не предлагай "
    "многошаговых планов.\n\n"
)


def _selfheal_on():
    """Флаг STEP_SELFHEAL=1 в .env (демон load_dotenv'ит на старте). 0/нет → прежнее поведение."""
    return (os.environ.get("STEP_SELFHEAL") or "").strip() == "1"


def _killed_by_planned_restart(rc, task_text, blob_fn=None):
    """Класс «самомодификация демона → ложный failed» (порт 48d9c64) на ПК. True → claude задачи
    завершился НЕ из-за поломки, а потому что демон в окне управляемого self-update-рестарта
    (задача правила pc_orchestrator.py). РОВНО 4 обязательных признака, нужны ВСЕ (любое сомнение
    → False → прежний честный failed, fail-safe):
      1) rc != 0 — claude оборвался ненормально (логический no-RESULT при rc==0 сюда НЕ попадает);
      2) self-update РЕАЛЬНО назрел: HEAD-блоб pc_orchestrator.py != запущенной версии RUNNING_BLOB
         (единственная причина планового рестарта — задача закоммитила правку самого демона);
      3) текст задачи — про сам демон (pc_orchestrator / самомодификация);
      4) взведён рубильник pc_orchestrator.stop — демон в окне намеренной остановки/рестарта.
    Признаки взяты из реальных механизмов ПК (self-update-дифф + стоп-файл) — лишнего не выдумываем
    (schtasks/heartbeat — забота watchdog, не признак гибели конкретной задачи)."""
    if rc == 0:
        return False
    new_blob = (blob_fn or _blob_hash)()
    if not new_blob or RUNNING_BLOB is None or new_blob == RUNNING_BLOB:
        return False
    if not _SELFMOD_RE.search(str(task_text or "")):
        return False
    return _stopped()


def _parse_thinker_json(text, fix_key="fixed_task"):
    """Строгий парс ответа думателя → {"verdict",<fix_key>,"reason"} или None (fail-safe).
    Терпим обёртку-мусор вокруг JSON (берём от первой { до последней }), но verdict обязан быть
    retry|halt — иначе None."""
    t = (text or "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    v = str(d.get("verdict") or "").strip().lower()
    if v not in ("retry", "halt"):
        return None
    return {"verdict": v,
            fix_key: str(d.get(fix_key) or "").strip(),
            "reason": str(d.get("reason") or "").strip()}


def _thinker_exec(prompt, timeout, tag):
    """Думатель = ПЕРЕИСПОЛЬЗОВАННЫЙ кондуктор Fable5→fallback (--fallback-model одним вызовом CLI),
    но ЧИСТЫЙ генератор: --max-turns 1 (один ответ, без инструментального цикла) + --allowed-tools ''
    + нейтральный cwd (tempdir, НЕ репо) → CLI не читает .claude/settings.json+pretool_guard и НИЧЕГО
    не исполняет (думатель красное не трогает). ANTHROPIC_API_KEY вычищен → подписка Max, не платный
    API. Возврат: текст ответа (распакован из --output-format json) или None при ЛЮБОМ сбое
    (запуск/таймаут/exit!=0/claude не найден) — fail-safe (upstream → прежний голый failed).
    Инъектируется в тестах (реальный claude не дёргаем)."""
    cbin = resolve_claude()
    if not cbin:
        log.warning("%s: claude не найден — думатель недоступен (fail-safe)", tag)
        return None
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # идём по ~/.claude (подписка), не по платному ключу
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [cbin, "-p", prompt, "--model", THINKER_MODEL,
           "--output-format", "json", "--max-turns", "1", "--allowed-tools", ""]
    if THINKER_FALLBACK:                         # кондуктор: фолбэк исполняет сам CLI в этом же вызове
        cmd += ["--fallback-model", THINKER_FALLBACK]
    try:
        p = subprocess.run(cmd, cwd=tempfile.gettempdir(), capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except Exception as e:
        log.warning("%s: думатель не отработал (%s) — fail-safe", tag, e)
        return None
    if p.returncode != 0:
        log.warning("%s: думатель exit=%s — fail-safe", tag, p.returncode)
        return None
    raw = (p.stdout or "").strip()
    out = raw
    try:
        env_j = json.loads(raw)
        if isinstance(env_j, dict) and "result" in env_j:   # CLI-конверт --output-format json
            out = (env_j.get("result") or "").strip()
    except Exception:
        pass
    return out


def _task_selfheal_consult(task_text, fail_text):
    """Думатель самопочинки ОДИНОЧНОЙ задачи: контекст — текст задачи ДОСЛОВНО + суть провала
    (краткая). Возврат: dict {"verdict","fixed_task","reason"} или None (любой сбой думателя =
    None = fail-safe прежний голый failed)."""
    prompt = (TASK_THINKER_PREAMBLE +
              f"УПАВШАЯ ЗАДАЧА (текст дословно):\n{str(task_text or '')[:2000]}\n\n"
              f"СУТЬ ПРОВАЛА:\n{str(fail_text or '')[:1200]}\n")
    out = _thinker_exec(prompt, STEP_SELFHEAL_TIMEOUT, "task-selfheal")
    if out is None:
        return None
    verdict = _parse_thinker_json(out, fix_key="fixed_task")
    if verdict is None:
        log.warning("task-selfheal: ответ думателя не распарсился (fail-safe failed): %.200s", out)
    return verdict


def _maybe_task_selfheal(tid, text, fail_text, frm):
    """Провал ОДИНОЧНОЙ задачи lane=pc → тот же думательный слой, РОВНО 1 попытка. Возврат True =
    финализация сделана здесь (перерождение / терминальный failed с диагнозом); False = прежний
    голый failed в вызывающем коде (fail-safe). Красное НЕ ослаблено: сюда доходит только
    исполнительский failed — needs_approval отсечён раньше в run_task/process_new, а плановый рестарт
    самомод-задачи (фикс 48d9c64) уже стал done внутри run_task и думателя не видит."""
    hm = _HEAL_TASK_RE.match(str(text or ""))
    if hm:
        # перерождённая задача упала ПОВТОРНО → терминальный failed (без retry) — петля невозможна
        oid = hm.group(1)
        msg = (f"🛑 самопочинка не помогла (попытка 1 исчерпана): перерождение задачи {oid} упало "
               f"повторно — нужен человек.\n{str(fail_text or '')}")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.info("task-selfheal: id=%s (перерождение задачи %s) упал ПОВТОРНО → терминальный failed", tid, oid)
        _cowork(f"задача #{tid} (перерождение {oid}) → failed повторно · {_clip(msg)}")
        _notify(_human("failed", tid, "самопочинка не помогла — нужен человек"))
        return True
    verdict = _task_selfheal_consult(text, fail_text)
    if verdict is None:
        return False                              # fail-safe: сбой думателя = прежний голый failed
    reason = verdict["reason"] or "(без причины)"
    fixed = verdict["fixed_task"]
    if verdict["verdict"] != "retry" or not fixed:
        msg = (f"задача упала → думатель: halt, причина: {reason}\n"
               f"Перерождение не поможет (диагноз думателя выше), нужен человек.\n"
               f"{str(fail_text or '')}")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.info("task-selfheal: id=%s → думатель halt (%s)", tid, reason[:120])
        _cowork(f"задача #{tid} → failed (думатель: halt) · {_clip(reason)}")
        _notify(_human("failed", tid, "думатель: halt — нужен человек"))
        return True
    reborn = f"[самопочинка задачи {tid}, попытка 1] {fixed}"[:RESULT_MAX]
    r = bc.enqueue_task(frm or "Filipp", reborn)
    if not r.get("ok"):
        log.warning("task-selfheal: перерождение id=%s не встало в очередь (%s) — fail-safe failed",
                    tid, r.get("error"))
        return False                              # fail-safe: очередь не приняла → прежний голый failed
    nid = r.get("id")
    card = (f"🩹 задача упала → думатель: retry, правка: {fixed[:200]}, причина: {reason[:200]}\n"
            f"Перерождена задачей id {nid} (попытка 1 из 1; повторный провал = терминальный failed).\n"
            f"Исходный провал: {str(fail_text or '')[:400]}")[:RESULT_MAX]
    bc.complete_task(tid, "done", card)           # карточка решения → cowork_log/пуш (тема 829-красное нетронуто)
    log.info("task-selfheal: id=%s перерождён задачей %s (retry)", tid, nid)
    _cowork(f"задача #{tid} → самопочинка retry, перерождена #{nid} · {_clip(card)}")
    _notify(_human("done", tid, f"самопочинка: перерождена задачей #{nid}"))
    return True


def _maybe_selfheal(tid, text, fail_text, frm=""):
    """Провал одиночной задачи (исполнительский failed) → думательный слой, РОВНО 1 попытка.
    Возврат True = финализация сделана здесь; False = ничего не делал → прежний путь в вызывающем
    коде (fail-safe). STEP_SELFHEAL=0/нет → False сразу (поведение байт-в-байт прежнее)."""
    if str(frm or "") == PC_LOCAL_DEC_FROM:
        # артефакт ЛОКАЛЬНОЙ цепи (шаг [шаг i/N]/родитель/synthetic) — НЕ одиночка: им владеет
        # надзор цепи (process_local_chains → _loc_after_fail: свой думатель самопочинки шага);
        # одиночное перерождение сорвало бы маркеры/порядок цепи. False = голый failed → тик цепи.
        return False
    if not _selfheal_on():
        return False
    if str(fail_text or "").lstrip().startswith(TIMEOUT_MARK):
        # ⏱-диагнозы (таймаут задачи / ПК-ливнесс застрявшей in_progress) думатель НЕ чинит:
        # переформулировка не ускорит зависший claude и не оживит Bridge — честный failed
        return False
    if _CONVERT_RE.match(str(text or "")):
        # конверт одобренной заявки НЕ перерождаем: маркер конверта обязан стоять ПЕРВЫМ,
        # перерождение сдвинуло бы его → сломался бы разрыв петли ре-конвертов
        return False
    return _maybe_task_selfheal(tid, text, fail_text, frm)


# ------------------- ЛОКАЛЬНЫЙ ДИРИЖЁР-ДЕКОМПОЗЕР (флаг PC_LOCAL_DEC) -----------
# Порт мозга декомпозера VPS (docs/dec_port_spec.md в manager-bot, шаг 2/7 родителя 185):
# при PC_LOCAL_DEC=1 демон берёт РОДИТЕЛЯ from=Filipp-pcloc-dec (lane=pc, БЕЗ маркера
# «[шаг i/N родитель id]») и строит план ЛОКАЛЬНО — headless claude -p ТЕМ ЖЕ кондуктором
# THINKER_MODEL/THINKER_FALLBACK (_thinker_exec: --max-turns 1, --allowed-tools '',
# нейтральный cwd → планировщик НИЧЕГО не исполняет, красное не ослаблено). Парс плана —
# _PLAN_LINE_RE («N. шаг» / «N) шаг», прочие строки молча игнор), потолок MAX_STEPS=8.
# УРОК 166: красное в ТЗ — НЕ повод валить план (планировщик только планирует); NEEDS_APPROVAL
# в выводе учитывается ТОЛЬКО при пустом плане (fail-safe чисто-красного родителя → честный
# failed с картой, НЕ кнопка). 🔴-пометка красных шагов — только дисплей в result родителя
# (строка с 🔴 не матчит _PLAN_LINE_RE → restart-proof парс плана из result цел); текст шагов
# в очереди НЕ помечается. PC_LOCAL_DEC=0/нет → ветка не зовётся вовсе (поведение байт-в-байт
# прежнее: такой родитель ушёл бы обычным headless-путём run_task).
# РЕЛИЗ ШАГОВ (шаг 3/7 родителя 185) — SEQUENTIAL: в очереди живёт максимум ОДИН шаг цепи,
# следующий встаёт ТОЛЬКО после done предыдущего (веер дал бы гонку halt-on-fail и порядка
# перерождений — у process_new нет guard'а последовательности). Шаги/synthetic идут
# from=Filipp-pcloc-dec: VPS-надзор (process_pc_chains) группирует ТОЛЬКО from==Filipp-pc-dec —
# к локальным цепям он СЛЕП, дирижёр локальный целиком. Состояние цепи — ТОЛЬКО из очереди
# (restart-proof: план = нумерованный список в result родителя + карточки коррекций поверх);
# в памяти процесса лишь дедуп-кэши (_loc_summarized/_loc_adapt_finish). Сводки/карточки =
# synthetic-задачи lane=pc прямым каналом 86d8b03 (enqueue_pc_task → claim → complete done).
MAX_STEPS = 8                                     # потолок шагов плана (как на VPS)
PC_LOCAL_DEC_FROM = "Filipp-pcloc-dec"            # метка родителя локального дирижёра
PC_DEC_PLAN_TIMEOUT = int(os.getenv("PC_DEC_PLAN_TIMEOUT", "600") or "600")  # план думается дольше починки

# Маркеры цепи — БАЙТ-В-БАЙТ из порт-спеки (docs/dec_port_spec.md manager-bot, раздел 2):
_STEP_RE = re.compile(r"^\[шаг (\d+)/(\d+) родитель (\d+)\]")        # маркер шага цепи (match с начала)
_PLAN_LINE_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(\S.*)")            # строка плана «N. шаг» / «N) шаг»
_SUM_RE = re.compile(r"^\[сводка родитель (\d+)\]")                  # synthetic-сводка цепи
_HEAL_RE = re.compile(r"\[самопочинка шага (\d+), попытка (\d+)\]")  # search: маркер идёт ПОСЛЕ [шаг i/N]
_ADAPT_MARK_RE = re.compile(r"\[коррекция плана (\d+)\]")            # K-происхождение шага (restart-proof счётчик)
_ADAPT_CARD_RE = re.compile(r"^\[коррекция плана родитель (\d+)\]")  # карточка адаптации (остаток плана в result)
_CARD_RE = re.compile(r"^\[карточка родитель (\d+)\]")               # событийная карточка цепи (🩹/🛑/🧭/⚠️/🏁)
_ADAPT_BASE_RE = re.compile(r"после шага (\d+)")                     # база коррекции из task_text карточки
ADAPT_REPLACED_MARK = "♻️ заменён коррекцией плана"                  # done-карта шага, заменённого адаптацией
ADAPT_FINISH_MARK = "⏭ закрыт досрочно"                              # done-карта шага, закрытого finish'ем
_REJECT_PREFIX = "отклонено Филиппом"     # префикс result devbot-отказа (halt цепи без думателя)
PLAN_ADAPT_MAX = 2                        # потолок коррекций на цепь; третий adjust = дрейф плана → halt
PLAN_ADAPT_TIMEOUT = int(os.getenv("PC_PLAN_ADAPT_TIMEOUT", "180") or "180")  # думатель адаптации — короткий ответ
# Преамбула думателя адаптации плана (порт ADAPT_PREAMBLE VPS, спека часть 2/2 §2).
ADAPT_PREAMBLE = (
    "Ты — думательный слой адаптации плана ПК-оркестратора TurboBaby (мета-дирижёр). Очередной шаг "
    "декомпозиции успешно завершён. Твоя задача — сверить результаты сделанного с целью родителя "
    "и решить, верен ли ЕЩЁ оставшийся план; ты НИЧЕГО не исполняешь, инструментов у тебя нет, "
    "файлы не читаешь — решай строго по данным ниже.\n"
    "Ответь СТРОГО ОДНИМ JSON-объектом, без текста до/после, без markdown-обёртки:\n"
    '{"verdict":"keep"|"adjust"|"finish","adjusted_steps":["<шаг>",...],"reason":"<1 строка>"}\n'
    "verdict=keep — оставшийся план верен, исполнять как есть (adjusted_steps пустой). Это "
    "ДЕФОЛТ: при малейшем сомнении — keep.\n"
    "verdict=adjust — ТОЛЬКО если результаты сделанных шагов сделали оставшиеся лишними/"
    "неверными и правка очевидна; adjusted_steps = НОВЫЙ полный список ОСТАВШИХСЯ шагов "
    "(сделанные не трогай), каждый — САМОДОСТАТОЧНОЕ дев-ТЗ ≤400 символов (исполнитель увидит "
    "ТОЛЬКО его текст, впиши нужный контекст).\n"
    "verdict=finish — цель родителя УЖЕ достигнута, оставшиеся шаги не нужны вовсе "
    "(adjusted_steps пустой).\n\n"
)


def _plan_adapt_on():
    """Флаг PLAN_ADAPT=1 в .env (отдельно от STEP_SELFHEAL, независимый откат). 0/нет → прежнее
    поведение (после done-шага всегда keep: релиз следующего шага прежнего плана)."""
    return (os.environ.get("PLAN_ADAPT") or "").strip() == "1"


def _parse_adapt_json(text):
    """Строгий парс ответа думателя адаптации → {"verdict","adjusted_steps","reason"} или None
    (None = fail-safe keep у вызывающего). Терпим обёртку-мусор вокруг JSON; verdict обязан быть
    keep|adjust|finish; adjust без непустых adjusted_steps → None (пустой adjust = keep)."""
    t = (text or "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    v = str(d.get("verdict") or "").strip().lower()
    if v not in ("keep", "adjust", "finish"):
        return None
    raw_steps = d.get("adjusted_steps")
    steps = ([str(s).strip() for s in raw_steps if str(s).strip()]
             if isinstance(raw_steps, list) else [])
    if v == "adjust" and not steps:
        return None
    return {"verdict": v, "adjusted_steps": steps, "reason": str(d.get("reason") or "").strip()}
# Красность шага (дословно с VPS): красное действие исполнитель шага спросит кнопкой сам —
# пометка нужна владельцу заранее увидеть, где цепь встанет на «да».
_HEADLESS_IMPOSSIBLE_RE = re.compile(
    r"clasp|redeploy|\bsqlite3\b|set_fleet_(?:oil|service)|delete_event|confirmed\s*=\s*true|"
    r"лист\s*1|\bcrm\b|зарплат|байки|транзакц|проводк|деньг|касс|удал(?:и|ени|яе|ён)|календар",
    re.IGNORECASE)

# Порт PLANNER_PREAMBLE под репо D:\turbobaby-bot (PC_NOTE не нужен: исполнитель шагов — ЭТОТ ПК).
# Отличие от VPS-оригинала: планировщик здесь — чистый генератор без инструментов (_thinker_exec),
# поэтому строка про read-only разведку заменена честной «решай по тексту ТЗ».
PLANNER_PREAMBLE = (
    "Ты — планировщик декомпозиции ПК-театра TurboBaby (репо D:\\turbobaby-bot). Твоя задача — "
    "РАЗБИТЬ крупное ТЗ на шаги, НЕ выполняя его: ты чистый генератор без инструментов — файлы "
    "не читаешь и не правишь, не коммитишь, не деплоишь, в таблицы не пишешь; решай по тексту ТЗ.\n"
    "ФОРМАТ ОТВЕТА — СТРОГО и ТОЛЬКО нумерованный список шагов, каждый с новой строки "
    "«N. <шаг>», без заголовков, без кода, без текста до/после списка. Шагов 2–7. Каждый шаг — "
    "САМОДОСТАТОЧНОЕ дев-ТЗ (до 45 мин, ≤400 символов): исполнитель — headless-агент на ЭТОМ ПК "
    "в репо D:\\turbobaby-bot — увидит ТОЛЬКО текст шага, поэтому впиши в каждый нужный контекст "
    "(файлы, функции, что сделать, как проверить). Шаги строго в порядке исполнения; правки кода "
    "раньше, деплой/рестарт/проверка — последними.\n"
    "КРАСНАЯ ЗОНА В ТЗ — НЕ ПОВОД ОТКАЗЫВАТЬСЯ ОТ ПЛАНА (урок задачи 166): ты ТОЛЬКО планируешь "
    "и сам ничего не исполняешь, поэтому упоминание clasp/деплоя/рабочих таблиц/денег/удаления в "
    "ТЗ НЕ требует подтверждения на этапе плана — НЕ выводи NEEDS_APPROVAL из-за содержимого ТЗ. "
    "Красное действие оформи ОТДЕЛЬНЫМ шагом (обычно последним): исполнитель ЭТОГО шага сам "
    "спросит «да» Филиппа кнопкой по штатной механике. Если ТЗ явно говорит, что прод-применение "
    "(деплой/рестарт) делается отдельно/хвостом — тем более просто строй план. ЕДИНСТВЕННОЕ "
    "исключение: ВСЁ ТЗ целиком = одно красное действие и разбивать не на что (например «задеплой "
    "прод») — тогда вместо списка выведи РОВНО одну строку "
    "«NEEDS_APPROVAL: op=other | <карточка: что · куда · последствия>».\n\n"
    "КРУПНОЕ ТЗ:\n"
)


def _local_dec_on():
    """Флаг PC_LOCAL_DEC=1 в .env (демон load_dotenv'ит на старте). 0/нет → прежнее поведение."""
    return (os.environ.get("PC_LOCAL_DEC") or "").strip() == "1"


def _is_local_dec_parent(frm, text):
    """Родитель локальной декомпозиции: флаг взведён + from=Filipp-pcloc-dec + текст БЕЗ маркера
    «[шаг i/N родитель id]» (маркированный = шаг чужой/своей цепи, не родитель)."""
    if not _local_dec_on():
        return False
    if str(frm or "") != PC_LOCAL_DEC_FROM:
        return False
    return not _STEP_RE.match(str(text or "").strip())


def _plan_steps(out):
    """Строки плана из вывода планировщика по _PLAN_LINE_RE (прочие строки молча игнор;
    порядок — как в выводе). → список текстов шагов."""
    steps = []
    for ln in str(out or "").splitlines():
        m = _PLAN_LINE_RE.match(ln)
        if m:
            steps.append(m.group(2).strip())
    return steps


def _dec_red_note(steps):
    """🔴-пометка красных шагов плана (только дисплей в result родителя, тексты шагов не трогаем).
    → строка с \\n на конце или '' (красных нет)."""
    red = [str(i) for i, s in enumerate(steps, 1) if _HEADLESS_IMPOSSIBLE_RE.search(s)]
    if not red:
        return ""
    return ("🔴 красные шаги: " + ", ".join(red)
            + " — исполнитель шага спросит «да» кнопкой, сам не исполнит.\n")


def _local_dec_plan(tid, text):
    """Построить план декомпозиции для родителя Filipp-pcloc-dec ЛОКАЛЬНО (кондуктор
    THINKER_MODEL/фолбэк, чистый генератор) и закрыть родителя: done с планом в result
    (restart-proof источник плана) или честный failed с диагнозом. Красное НЕ ослаблено:
    планировщик ничего не исполняет; урок 166 — NEEDS_APPROVAL в выводе валит родителя
    ТОЛЬКО при пустом плане (чисто-красное ТЗ), и это failed-карта, НЕ кнопка."""
    out = _thinker_exec(PLANNER_PREAMBLE + str(text or ""), PC_DEC_PLAN_TIMEOUT, "pcloc-dec-plan")
    if out is None:
        msg = ("планировщик локальной декомпозиции не отработал (кондуктор "
               f"{THINKER_MODEL}/фолбэк {THINKER_FALLBACK or 'нет'}): сбой/таймаут claude -p — "
               "план не построен, повтори задачу")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.warning("pcloc-dec: id=%s планировщик не отработал → failed", tid)
        _cowork(f"родитель #{tid} (pcloc-dec) → failed · {_clip(msg)}")
        _notify(_human("failed", tid, "планировщик декомпозиции не отработал"))
        return
    steps = _plan_steps(out)
    if not steps:
        card = _detect_needs_approval(out)
        if card:   # чисто-красный родитель (единственное исключение урока 166) → failed, НЕ кнопка
            msg = ("планировщик needs_approval: " + card)[:RESULT_MAX]
        else:
            msg = ("план пуст: планировщик не вернул нумерованный список «N. <шаг>». "
                   "Вывод (хвост): " + _tail(out, 700))[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.warning("pcloc-dec: id=%s пустой план → failed (%s)", tid, _clip(msg, 160))
        _cowork(f"родитель #{tid} (pcloc-dec) → failed · {_clip(msg)}")
        _notify(_human("failed", tid, msg))
        return
    if len(steps) > MAX_STEPS:
        msg = (f"план из {len(steps)} шагов превышает потолок {MAX_STEPS} — "
               "упрости ТЗ или разбей на два «декомпозируй:»")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.warning("pcloc-dec: id=%s план %s шагов > %s → failed", tid, len(steps), MAX_STEPS)
        _cowork(f"родитель #{tid} (pcloc-dec) → failed · {_clip(msg)}")
        _notify(_human("failed", tid, msg))
        return
    plan_txt = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    # РЕЛИЗ ШАГА 1 — ДО закрытия родителя (crash-окно спеки: шаг не встал → родитель остаётся
    # in_progress, зависание честно добьёт ПК-ливнесс process_stuck_singles; done-родитель
    # без шага 1 осиротил бы цепь молча). Только при ok шага 1 родитель закрывается done.
    if not _loc_release(tid, 1, len(steps), steps[0]):
        log.warning("pcloc-dec: id=%s шаг 1 не встал в очередь — родитель остаётся in_progress", tid)
        return
    result = (f"🧩 Декомпозиция (локальный дирижёр PC): {len(steps)} шагов — исполняю ПО ОДНОМУ "
              "(lane=pc, sequential-релиз: следующий шаг встаёт только после done предыдущего).\n"
              + plan_txt + "\n" + _dec_red_note(steps)
              + "Шаг 1 уже в очереди. Красный шаг спросит «да» кнопкой. "
                "После последнего шага пришлю сводку.")[:RESULT_MAX]
    bc.complete_task(tid, "done", result)
    log.info("pcloc-dec: id=%s план из %s шагов построен (done), шаг 1 релизнут", tid, len(steps))
    _cowork(f"родитель #{tid} (pcloc-dec) → done: план {len(steps)} шагов, шаг 1 в очереди · {_clip(result)}")
    _notify(_human("done", tid, f"декомпозиция: план из {len(steps)} шагов, шаг 1 в очереди"))


# ---- sequential-релиз и надзор локальной цепи (шаг 3/7 родителя 185) ----
# Порт process_pc_chains/_pc_chain_tick VPS-демона (docs/dec_port_spec.md, раздел 3) на локальную
# почву: тот же снимок очереди, те же маркеры, но исполнитель шагов — ЭТОТ ЖЕ демон (process_new),
# поэтому детект «ПК молчит» (PC_STEP_TIMEOUT) не нужен: зависший in_progress добьёт ПК-ливнесс
# process_stuck_singles → failed → halt цепи следующим тиком; new ждёт своего FIFO-клейма;
# needs_approval ждёт Филиппа (просрочку закроет process_approval_timeouts → failed → halt).
#
# КРАСНАЯ МЕХАНИКА ЦЕПИ (шаг 5/7 родителя 185, спека §4 «КРАСНОЕ В ЦЕПИ») — ШТАТНЫМ путём
# одиночек, без спец-веток: шаг цепи = обычная задача очереди, NEEDS_APPROVAL в его выводе →
# process_new → set_needs_approval (карточку с кнопками несёт devbot VPS: инбокс INBOX_TOPIC_ID,
# прод 1160; from=Filipp-pcloc-dec включён в его QUEUE_FROMS_PC); тик цепи на needs_approval →
# return (цепь ЖДЁТ «да», следующий шаг не релизится — sequential-модель). Approve → devbot ставит
# approved → process_approved ре-ран с нотой [ОДОБРЕНО ЧЕЛОВЕКОМ] → done → тик релизит следующий
# шаг (продолжение цепи). Reject («нет»/❌ — devbot финализирует failed «отклонено Филиппом»),
# просрочка needs_approval/approved (⏱-диагнозы) и повторное красное после approve (✋-ручная
# карта: headless доказанно не может) → halt цепи БЕЗ думателя (гейт _loc_after_fail) + сводка.
# Красное НЕ ослаблено: перерождённые/скорректированные шаги идут тем же путём — красное снова
# даст кнопку; approve обхода гейтов не создаёт.

_LOC_STATUSES = ("new", "in_progress", "needs_approval", "approved", "done", "failed")
_loc_summarized = set()    # родители, по которым сводка уже отправлена (дедуп-кэш памяти процесса;
                           # после рестарта от дублей защищает скан очереди _loc_summary_exists)
_loc_adapt_finish = {}     # pid → причина досрочного finish (кэш для 🏁-шапки сводки; сводка идёт
                           # ТЕМ ЖЕ вызовом _loc_after_done — рестарт между ними не страшен)
_loc_adapted = set()       # (pid, step_i), по которым думатель адаптации уже спрошен (память
                           # процесса; после рестарта — максимум один лишний keep-вопрос)


def _loc_enqueue(text):
    """Задача/synthetic локальной цепи в очередь lane=pc от имени дирижёра — ПРЯМОЙ канал
    ПК↔Bridge (86d8b03: enqueue_pc_task, минуя splinter/devbot). → dict в форме Bridge-ответа."""
    ok, nid, err = enqueue_pc_task(text, frm=PC_LOCAL_DEC_FROM)
    return {"ok": ok, "id": nid, "error": err}


def _loc_fetch_items():
    """Все задачи полосы pc по статусам _LOC_STATUSES → items (у каждого есть status) | None
    (ошибка чтения ЛЮБОГО статуса → пропустить цикл надзора ЦЕЛИКОМ: частичная картина цепи
    опаснее ожидания)."""
    items = []
    for st in _LOC_STATUSES:
        r = bc.get_pending(st)
        if not r.get("ok"):
            return None
        for it in r.get("items", []):
            if isinstance(it, dict):
                it.setdefault("status", st)
                items.append(it)
    return items


def _loc_group_chains(items):
    """items полосы pc → {pid: [(step_i, step_n, item), …]} ТОЛЬКО локальных цепей
    (from=Filipp-pcloc-dec + паттерн шага). Одиночки pc, цепи VPS-театра (from=Filipp-pc-dec)
    и synthetic без [шаг i/N] (сводки/карточки/родитель) не попадают."""
    chains = {}
    for it in items:
        if str(it.get("from") or "") != PC_LOCAL_DEC_FROM:
            continue
        m = _STEP_RE.match(str(it.get("task_text") or ""))
        if m:
            chains.setdefault(int(m.group(3)), []).append(
                (int(m.group(1)), int(m.group(2)), it))
    return chains


def _loc_chain_steps(pid):
    """Шаги цепи родителя pid (для осиротевшей сводки). Ошибка чтения → []."""
    items = _loc_fetch_items()
    return (_loc_group_chains(items).get(int(pid)) or []) if items is not None else []


def _loc_summary_exists(pid):
    """Сводка по родителю pid уже есть в очереди (в любом живом статусе)? Защита от дубля
    после рестарта демона (кэш _loc_summarized живёт только в памяти процесса)."""
    mark = f"[сводка родитель {pid}]"
    for st in ("done", "new", "in_progress"):
        try:
            r = bc.get_pending(st)
        except Exception:
            continue
        if r.get("ok") and any(str(it.get("task_text") or "").startswith(mark)
                               for it in r.get("items", [])):
            return True
    return False


def _loc_post_card(pid, text):
    """Событийная карточка цепи (🩹 retry / 🛑 terminal / 🏁 finish / ⚠️ план не восстановился) —
    synthetic-задачей прямым каналом (enqueue → claim → complete done): очередь — единственный
    канал дирижёра наружу, devbot принесёт done-рапортом. (🧭-карточка коррекции идёт отдельным
    маркером _ADAPT_CARD_RE — она же restart-proof носитель остатка плана.)"""
    r = _loc_enqueue(f"[карточка родитель {pid}] событие локальной цепи")
    if not r.get("ok"):
        log.warning("pcloc-dec: карточка родителя %s не встала в очередь (%s)", pid, r.get("error"))
        return
    sid = r.get("id")
    bc.claim_task(sid)                    # даже если claim не прошёл — complete финализирует
    bc.complete_task(sid, "done", str(text)[:RESULT_MAX])


def _loc_summary_text(pid, steps):
    """Сводка локальной цепи из переданных шагов (зеркало _pc_summary_text VPS). Дубли номера
    (провал + перерождение самопочинки) — последняя запись по id; total = n-маркер последнего
    релизнутого шага (несёт актуальный итог после коррекций плана)."""
    rows = sorted([s for s in steps if str(s[2].get("status")) in ("done", "failed")],
                  key=lambda x: (x[0], int(x[2].get("id") or 0)))
    last = {}
    for i, n, it in rows:
        last[i] = (i, n, it)
    rows = [last[k] for k in sorted(last)]
    if not rows:
        return f"🧩 Сводка декомпозиции (родитель {pid}, локальный дирижёр): шагов не найдено (очередь пуста?)"
    n_done = sum(1 for _i, _n, it in rows if str(it.get("status")) == "done")
    total = rows[-1][1]
    head = f"🧩 Сводка декомпозиции (родитель {pid}, локальный дирижёр): {n_done}/{total} шагов done"
    fin = _loc_adapt_finish.get(pid)
    if fin:
        head += f", 🏁 завершено досрочно: {fin}"
    elif n_done < len(rows):
        head += ", есть упавшие/пропущенные"
    lines = [head]
    for i, n, it in rows:
        emoji = "✅" if str(it.get("status")) == "done" else "❌"
        first = (str(it.get("result") or "").strip().splitlines() or ["(пусто)"])[0]
        lines.append(f"{emoji} шаг {i}/{n}: {first[:400]}")
    return "\n".join(lines)[:RESULT_MAX]


def _loc_post_summary(pid, steps):
    """Финал цепи → сводка synthetic-задачей прямым каналом (enqueue → claim → complete done).
    Идемпотентно: _loc_summarized (память) + _loc_summary_exists (скан очереди — restart-proof)."""
    if pid in _loc_summarized:
        return
    if _loc_summary_exists(pid):
        _loc_summarized.add(pid)
        return
    text = _loc_summary_text(pid, steps)
    r = _loc_enqueue(f"[сводка родитель {pid}] сводный отчёт по шагам")
    if not r.get("ok"):
        log.warning("pcloc-dec: сводка родителя %s не встала в очередь (%s)", pid, r.get("error"))
        return
    sid = r.get("id")
    bc.claim_task(sid)
    cm = bc.complete_task(sid, "done", text)
    _loc_summarized.add(pid)
    log.info("pcloc-dec: сводка родителя %s → задача %s (bridge_ok=%s)", pid, sid, cm.get("ok"))


def _parse_numbered(text):
    """Нумерованные строки «N. <текст>» → {N: <текст>} (восстановление плана из result родителя /
    карточки коррекции). Прочие строки игнорируются."""
    out = {}
    for line in (text or "").splitlines():
        m = _PLAN_LINE_RE.match(line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _loc_current_plan(pid):
    """ТЕКУЩИЙ план цепи, restart-proof ИЗ ОЧЕРЕДИ (никакой памяти процесса): план родителя
    (нумерованный список в result, done lane=pc) + карточки «[коррекция плана родитель pid]
    после шага B…» (нумерованный остаток в result) поверх, в порядке id. → (plan: {номер:
    (текст, K-происхождение; 0=исходный)}, K всего коррекций, база последней коррекции | None).
    Ошибка чтения / родитель не найден → ({}, 0, None) — вызывающий даст честный halt-диагноз."""
    try:
        r = bc.get_pending("done")
    except Exception as e:
        log.warning("pcloc-dec: план родителя %s не прочитан (%s)", pid, e)
        return {}, 0, None
    if not r.get("ok"):
        return {}, 0, None
    parent_result, cards = "", []
    for it in r.get("items", []):
        if int(it.get("id") or 0) == int(pid):
            parent_result = str(it.get("result") or "")
        m = _ADAPT_CARD_RE.match(str(it.get("task_text") or ""))
        if m and int(m.group(1)) == int(pid):
            cards.append(it)
    plan = {num: (txt, 0) for num, txt in _parse_numbered(parent_result).items()}
    last_base = None
    cards.sort(key=lambda x: int(x.get("id") or 0))
    for k, card in enumerate(cards, 1):
        bm = _ADAPT_BASE_RE.search(str(card.get("task_text") or ""))
        nums = _parse_numbered(str(card.get("result") or ""))
        if not bm or not nums:
            continue          # осиротевшая/пустая коррекция — план не меняла (fail-safe keep)
        base = int(bm.group(1))
        plan = {num: v for num, v in plan.items() if num <= base}
        plan.update({num: (txt, k) for num, txt in nums.items()})
        last_base = base
    return plan, len(cards), last_base


def _loc_release(pid, j, total, text, k=0):
    """Релиз шага j/total цепи pid (sequential: следующий шаг встаёт ТОЛЬКО после done
    предыдущего — максимум один шаг цепи в очереди). k>0 → шаг из коррекции плана (маркер
    для restart-proof счётчика/глаз). Enqueue-fail → warn, повтор следующим тиком (done-шаг
    остаётся последним в снимке). → ok-флаг."""
    mark = f"[коррекция плана {k}] " if k else ""
    r = _loc_enqueue(f"[шаг {j}/{total} родитель {pid}] {mark}{text}"[:RESULT_MAX])
    if not r.get("ok"):
        log.warning("pcloc-dec: релиз шага %s/%s родителя %s не встал (%s) — повтор следующим циклом",
                    j, total, pid, r.get("error"))
        return False
    log.info("pcloc-dec: шаг %s/%s родителя %s релизнут (id %s, lane=pc)", j, total, pid, r.get("id"))
    return True


def _loc_parent_context(pid):
    """Контекст родителя pid для думателей: (исходная цель дословно, план шагов). Родитель после
    декомпозиции лежит в done (task_text = цель, result = план). Не нашли → заглушки (не валимся)."""
    try:
        r = bc.get_pending("done")
        if r.get("ok"):
            for it in r.get("items", []):
                if int(it.get("id") or 0) == int(pid):
                    return (str(it.get("task_text") or "").strip()[:1500],
                            str(it.get("result") or "").strip()[:2000])
    except Exception as e:
        log.warning("pcloc-selfheal: контекст родителя %s не прочитан (%s)", pid, e)
    return (f"(родитель {pid} не найден в очереди)", "(план недоступен)")


def _loc_selfheal_consult(pid, step_i, step_n, step_text, fail_text):
    """Думатель самопочинки шага локальной цепи: промпт = цель родителя ДОСЛОВНО + план шагов +
    упавший шаг + суть провала. Возврат: dict {"verdict","fixed_step","reason"} или None
    (любой сбой думателя = None = fail-safe прежний halt-on-fail)."""
    goal, plan = _loc_parent_context(pid)
    prompt = (THINKER_PREAMBLE +
              f"ИСХОДНАЯ ЦЕЛЬ РОДИТЕЛЯ (дословно):\n{goal}\n\n"
              f"ПЛАН ШАГОВ РОДИТЕЛЯ:\n{plan}\n\n"
              f"УПАВШИЙ ШАГ {step_i}/{step_n} (текст дословно):\n{step_text}\n\n"
              f"СУТЬ ПРОВАЛА:\n{str(fail_text or '')[:1200]}\n")
    out = _thinker_exec(prompt, STEP_SELFHEAL_TIMEOUT, "pcloc-selfheal")
    if out is None:
        return None
    verdict = _parse_thinker_json(out, fix_key="fixed_step")
    if verdict is None:
        log.warning("pcloc-selfheal: ответ думателя не распарсился (fail-safe halt): %.200s", out)
    return verdict


def _loc_adapt_consult(pid, step_i, steps, remaining):
    """Думатель адаптации локальной цепи — ТА ЖЕ схема (ADAPT_PREAMBLE, кондуктор, --max-turns 1,
    строгий JSON): сделанное берём из done-шагов снапшота (дубли номера — последний по id:
    перерождение самопочинки затирает провал), оставшееся — из восстановленного плана (в очереди
    его нет — шаги релизятся по одному). None = fail-safe keep."""
    goal, plan_txt = _loc_parent_context(pid)
    done_last = {}
    for i, _n, it in sorted([s for s in steps if str(s[2].get("status")) == "done"],
                            key=lambda x: (x[0], int(x[2].get("id") or 0))):
        done_last[i] = str(it.get("result") or "").strip()
    done_lines = [f"шаг {i}: {(done_last[i].splitlines() or ['(пусто)'])[0][:300]}"
                  for i in sorted(done_last)] or ["(результатов пока нет)"]
    rem_lines = [f"шаг {j}: {t[:400]}" for j, t in remaining]
    prompt = (ADAPT_PREAMBLE +
              f"ИСХОДНАЯ ЦЕЛЬ РОДИТЕЛЯ (дословно):\n{goal}\n\n"
              f"ИСХОДНЫЙ ПЛАН ШАГОВ:\n{plan_txt}\n\n"
              f"РЕЗУЛЬТАТЫ СДЕЛАННЫХ ШАГОВ (сжато; только что завершён шаг {step_i}):\n"
              + "\n".join(done_lines) + "\n\n"
              "ОСТАВШИЕСЯ ШАГИ ПЛАНА:\n" + "\n".join(rem_lines) + "\n")
    out = _thinker_exec(prompt, PLAN_ADAPT_TIMEOUT, "pcloc-plan-adapt")
    if out is None:
        return None
    v = _parse_adapt_json(out)
    if v is None:
        log.warning("pcloc-plan-adapt: ответ думателя не распарсился/пуст (fail-safe keep): %.200s", out)
    return v


def _loc_after_fail(pid, i, n, it, steps):
    """Провал шага локальной цепи (уже failed в очереди). Отказ Филиппа / ⏱-диагноз
    (таймаут headless, ПК-ливнесс застрявшей in_progress, просрочка approve/needs_approval) /
    ✋-ручная карта (одобрено, но headless снова упёрся в красное) → halt без
    думателя; перерождение упало ПОВТОРНО → терминальный halt (🛑-карта); STEP_SELFHEAL=0 →
    прежний halt-on-fail; иначе думатель самопочинки → РОВНО одно перерождение с маркером
    «[самопочинка шага i, попытка 1]» + 🩹-карта. Halt в sequential-модели = просто НЕ релизить
    дальше + сводка (пропускать нечего — остальных шагов в очереди нет). FAIL-SAFE: любой сбой
    думателя/enqueue = halt (не хуже прежнего). Конверты сюда не попадают структурно: маркер
    конверта обязан стоять ПЕРВЫМ (^), а текст шага начинается с «[шаг i/N…»."""
    text = str(it.get("task_text") or "")
    fail_text = str(it.get("result") or "")
    if fail_text.lstrip().startswith((_REJECT_PREFIX, TIMEOUT_MARK, MANUAL_MARK)):
        _loc_post_summary(pid, steps)         # человек сказал «нет» / ⏱ / ✋ — чинить нечего
        return
    if _HEAL_RE.search(text):
        _loc_post_card(pid, f"🛑 самопочинка не помогла (попытка 1 исчерпана): шаг {i}/{n} "
                            f"родителя {pid} упал повторно — цепочка остановлена, нужен человек.\n"
                            f"{fail_text[:400]}")
        _loc_post_summary(pid, steps)
        return
    if not _selfheal_on():
        _loc_post_summary(pid, steps)         # прежний halt-on-fail (провал уже отрапортован ❌)
        return
    verdict = _loc_selfheal_consult(pid, i, n, text, fail_text)
    if verdict is None or verdict["verdict"] != "retry" or not verdict["fixed_step"]:
        reason = (verdict or {}).get("reason") or "(сбой думателя — fail-safe halt)"
        _loc_post_card(pid, f"шаг {i}/{n} упал → думатель: halt, причина: {reason}\n"
                            f"Цепочка остановлена (диагноз думателя выше).")
        _loc_post_summary(pid, steps)
        return
    fixed, reason = verdict["fixed_step"], verdict["reason"] or "(без причины)"
    r = _loc_enqueue((f"[шаг {i}/{n} родитель {pid}] "
                      f"[самопочинка шага {i}, попытка 1] {fixed}")[:RESULT_MAX])
    if not r.get("ok"):
        log.warning("pcloc-dec: перерождение шага %s родителя %s не встало (%s) — fail-safe halt",
                    i, pid, r.get("error"))
        _loc_post_summary(pid, steps)         # очередь не приняла → halt (не хуже прежнего)
        return
    _loc_post_card(pid, f"🩹 шаг {i}/{n} упал → думатель: retry, правка: {fixed[:200]}, "
                        f"причина: {reason[:200]}\n"
                        f"Перерождён задачей id {r.get('id')} (lane=pc; попытка 1 из 1; повторный "
                        f"провал = терминальный halt).\nИсходный провал: {fail_text[:400]}")
    log.info("pcloc-dec: шаг %s/%s родителя %s перерождён задачей %s (retry)", i, n, pid, r.get("id"))


def _loc_after_done(pid, i, n, it, steps):
    """Done шага локальной цепи: план restart-proof из очереди → последний по плану → сводка
    (думатель НЕ зовётся — экономия лимитов); план не восстановился → ⚠️-карточка + halt; иначе
    при PLAN_ADAPT=1 адаптация (keep/adjust/finish): keep → релиз следующего шага прежнего плана;
    adjust → карточка коррекции (restart-proof остаток плана в result) + релиз первого
    скорректированного (счётчик K = карточки коррекций в очереди, лимит PLAN_ADAPT_MAX, дальше
    halt «план дрейфует»); finish → 🏁-карта + сводка «завершено досрочно». Дедуп консультаций:
    (pid, i) в _loc_adapted; шаг сам из свежей коррекции (last_base == i) → не переспрашиваем.
    ЛЮБОЙ сбой думателя/карточки/потолок MAX_STEPS = fail-safe keep."""
    plan, k_cnt, last_base = _loc_current_plan(pid)
    total = max(plan) if plan else n
    if i >= total:
        _loc_post_summary(pid, steps)
        return
    if plan.get(i + 1) is None:
        _loc_post_card(pid, f"⚠️ план родителя {pid} не восстановился из очереди (шаг {i + 1} "
                            f"не найден в result родителя/коррекций) — цепочка остановлена, "
                            f"поставь «декомпозируй:» заново.")
        _loc_post_summary(pid, steps)
        return
    consult = (_plan_adapt_on() and last_base != i and (pid, i) not in _loc_adapted)
    if consult:
        _loc_adapted.add((pid, i))
        remaining = [(j, plan[j][0]) for j in sorted(plan) if j > i]
        verdict = _loc_adapt_consult(pid, i, steps, remaining)
        if verdict is not None and verdict["verdict"] == "finish":
            reason = (verdict["reason"] or "(без причины)")[:300]
            _loc_adapt_finish[pid] = reason
            _loc_post_card(pid, f"🏁 после шага {i} думатель решил: цель родителя {pid} достигнута "
                                f"досрочно ({reason}) — оставшиеся шаги {i + 1}–{total} не релизятся.")
            _loc_post_summary(pid, steps)
            log.info("pcloc-plan-adapt: родитель %s finish после шага %s (%s)", pid, i, reason[:120])
            return
        if verdict is not None and verdict["verdict"] == "adjust":
            new_steps = verdict["adjusted_steps"]
            reason = (verdict["reason"] or "(без причины)")[:300]
            k = k_cnt + 1
            if k > PLAN_ADAPT_MAX:
                _loc_post_card(pid, f"🛑 план дрейфует: думатель запросил коррекцию №{k} (лимит "
                                    f"{PLAN_ADAPT_MAX} на цепь) — цепочка остановлена, нужен "
                                    f"владелец. Диагноз думателя: {reason}")
                _loc_post_summary(pid, steps)
                log.info("pcloc-plan-adapt: родитель %s — adjust №%s (> лимита %s) → halt (дрейф)",
                         pid, k, PLAN_ADAPT_MAX)
                return
            if i + len(new_steps) > MAX_STEPS:
                log.warning("pcloc-plan-adapt: родитель %s adjust дал %s шагов (итог > потолка %s) "
                            "— fail-safe keep", pid, len(new_steps), MAX_STEPS)
            else:
                new_total = i + len(new_steps)
                numbered = "\n".join(f"{j}. {s}" for j, s in enumerate(new_steps, start=i + 1))
                card = (f"🧭 после шага {i} думатель скорректировал план (коррекция "
                        f"{k}/{PLAN_ADAPT_MAX}): {reason}\n"
                        f"НОВЫЙ ОСТАВШИЙСЯ ПЛАН (шаги {i + 1}–{new_total}, релизятся по одному):\n"
                        f"{numbered}\n"
                        f"Итог плана {new_total} шагов; сделанные шаги 1–{i} не тронуты. "
                        f"Третья коррекция = halt «план дрейфует».")
                cr = _loc_enqueue(f"[коррекция плана родитель {pid}] после шага {i} (K={k})")
                if cr.get("ok"):
                    bc.claim_task(cr.get("id"))
                    bc.complete_task(cr.get("id"), "done", card[:RESULT_MAX])
                    _loc_release(pid, i + 1, new_total, new_steps[0], k=k)
                    log.info("pcloc-plan-adapt: родитель %s adjust K=%s после шага %s → релиз "
                             "скорректированного шага %s/%s", pid, k, i, i + 1, new_total)
                    return
                log.warning("pcloc-plan-adapt: карточка коррекции родителя %s не встала (%s) — "
                            "fail-safe keep", pid, cr.get("error"))
    # keep / fail-safe / адаптация выключена / уже спрошено → следующий шаг прежнего плана
    txt, k_origin = plan[i + 1]
    _loc_release(pid, i + 1, total, txt, k=k_origin)


def _loc_chain_tick(pid, steps):
    """Один тик надзора локальной цепи: смотрим ПОСЛЕДНИЙ шаг (максимальный номер, при дублях —
    старший id: перерождение самопочинки). Ожидание → return (исполнитель — этот же демон:
    new ждёт FIFO-клейма process_new, in_progress-зомби добьёт process_stuck_singles,
    needs_approval ждёт Филиппа / process_approval_timeouts); done/failed → хуки цепи."""
    i, n, it = max(steps, key=lambda s: (s[0], int(s[2].get("id") or 0)))
    st = str(it.get("status") or "")
    if st in ("new", "in_progress", "needs_approval", "approved"):
        return
    # терминальный статус: закрытая ранее цепь (рестарт демона) → в кэш и не трогать
    if _loc_summary_exists(pid):
        _loc_summarized.add(pid)
        return
    if st == "failed":
        _loc_after_fail(pid, i, n, it, steps)
    elif st == "done":
        _loc_after_done(pid, i, n, it, steps)


def process_local_chains():
    """Надзор локальных цепей (каждый цикл демона): read-only снимок полосы pc → тик по каждой
    СВОЕЙ цепи (from=Filipp-pcloc-dec). Чужое на полосе (одиночки, цепи VPS-театра Filipp-pc-dec)
    не трогаем. Сбой тика одной цепи не валит остальные (доберём следующим циклом).
    PC_LOCAL_DEC=0/нет → return сразу (поведение демона байт-в-байт прежнее)."""
    if not _local_dec_on() or _stopped():
        return
    items = _loc_fetch_items()
    if items is None:
        return
    chains = _loc_group_chains(items)
    for pid in sorted(set(chains) - _loc_summarized):
        try:
            _loc_chain_tick(pid, chains[pid])
        except Exception as e:
            log.warning("pcloc-dec: тик цепи родителя %s упал (%s) — следующим циклом", pid, e)


def _loc_finalize_orphan_synthetic(tid, text):
    """Осиротевшая synthetic-задача локальной цепи (демон упал между enqueue и complete) →
    довести done, НЕ исполняя headless'ом и НЕ отдавая планировщику как «родителя»
    (порт одноимённой ветки process_new VPS-демона). True = финализирована здесь."""
    sm = _SUM_RE.match(text)
    if sm:
        pid = int(sm.group(1))
        bc.complete_task(tid, "done", _loc_summary_text(pid, _loc_chain_steps(pid)))
        log.info("pcloc-dec: осиротевшая сводка id=%s доведена", tid)
        return True
    if _ADAPT_CARD_RE.match(text):
        bc.complete_task(tid, "done", "🧭 карточка коррекции плана (осиротела при рестарте "
                                      "демона; шаги коррекции уже в цепочке родителя)")
        log.info("pcloc-dec: осиротевшая карточка адаптации id=%s доведена", tid)
        return True
    if _CARD_RE.match(text):
        bc.complete_task(tid, "done", "🃏 карточка события цепи (осиротела при рестарте демона; "
                                      "цепь родителя идёт своим ходом)")
        log.info("pcloc-dec: осиротевшая карточка id=%s доведена", tid)
        return True
    return False


# ------------------- авто-обновление userbot/moderbot после дев-задач ----------
# Убираем ручное «обнови userbot» из темы 205: после done дев-задачи («тз:…»), если она
# создала НОВЫЕ коммиты, затронувшие рантайм-файлы бота → гейт (unittest затронутых тестов)
# → зелено → рестарт бота ТОЙ ЖЕ механикой pc_agent (CIM-поиск PID + taskkill + venv-spawn,
# PID-контроль) → верификация (PID поднялся + лог свежий) → строка в карточку/cowork.
# Гейт красный → НЕ рестартим (код запушен, применится позже). Стоп-флаг уважаем.
# suggest.py/pricing.py импортят ОБА бота → их правка рестартит и userbot, и moderbot.

# ЯВНАЯ карта «файл → какие процессы рестартить» (НЕ эвристика префиксов): единственный источник
# правды и для авто-обновления после дев-задач (maybe_update_bots), и для реконсиляции детей после
# self-update демона (_selfupdate_restart_children). Значения — подмножество
# {"userbot","moderbot","pc_agent"}. pc_agent НИКОГДА не рестартим чужими руками (только пометка
# «ждёт ручного рестарта»). Правила проверяются по имени файла (basename, lower); один файл может
# задеть НЕСКОЛЬКО процессов (suggest/pricing импортят и userbot, и moderation_bot — общий рантайм).
# moderation_ipc.py специально → userbot (IPC модерации исполняет userbot), а НЕ moderbot —
# поэтому карта явная, без общего префикса «moderation».
_FILE_PROCESS_RULES = (
    (lambda n: n.startswith("userbot"),   ("userbot",)),              # userbot_listen.py и т.п.
    (lambda n: n.startswith("suggest"),   ("userbot", "moderbot")),   # suggest*.py — общий рантайм
    (lambda n: n.startswith("pricing"),   ("userbot", "moderbot")),   # pricing*.py — общий рантайм
    (lambda n: n.startswith("booking"),   ("userbot",)),              # booking_draft.py и др. booking-модули
    (lambda n: n == "moderation_ipc.py",  ("userbot",)),              # IPC модерации → рестарт userbot
    (lambda n: n == "moderation_bot.py",  ("moderbot",)),             # сам модербот
    (lambda n: n == "moderation_core.py", ("moderbot",)),             # ядро модерации
    (lambda n: n == "pc_agent.py",        ("pc_agent",)),             # агент — ТОЛЬКО пометка (ручной рестарт)
)
_RE_DEV_TASK = re.compile(r"^\s*тз\b", re.I)   # дев-задача: текст начинается с «тз:/тз …»


def _procs_for_file(path):
    """Множество процессов, которые надо рестартить из-за правки данного файла (по ЯВНОЙ карте).
    Пусто → файл рантайм ботов не задевает (README/*.md/тесты и т.п.)."""
    name = os.path.basename(str(path or "")).lower()
    procs = set()
    for pred, targets in _FILE_PROCESS_RULES:
        if pred(name):
            procs.update(targets)
    return procs


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
    """Разложить изменённые файлы по цели рестарта бота по ЯВНОЙ карте _FILE_PROCESS_RULES. →
    (userbot_files, moderbot_files). Файл может попасть в обе группы (suggest/pricing — общий
    рантайм). pc_agent сюда НЕ попадает: maybe_update_bots рестартит только боты (агент — пометка)."""
    ub, mb = [], []
    for p in paths:
        procs = _procs_for_file(p)
        if "userbot" in procs:
            ub.append(p)
        if "moderbot" in procs:
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
            _stamp_apply_restart(kind)          # реестр анти-флапа: реконсиляция self-update не дёрнет повторно
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


# ------------------- анти-флап авто-рестартов + реконсиляция детей self-update ----
# Лёгкий реестр «когда последний раз авто-рестартили процесс» (в памяти демона). Его СТАМПИТ и
# maybe_update_bots (рестарт после дев-задачи), и командой-рычагом _exec_command, и реконсиляция
# детей после self-update; реконсиляция ЧИТАЕТ его и ПОДАВЛЯЕТ рестарт, если процесс дёргали недавно
# (< APPLY_COOLDOWN_SEC). Так авто-применение кода не воюет с только что случившимся рестартом
# (уважение анти-флапа — требование задачи). Контур-вотчдог держит СВОЙ отдельный анти-флап
# (_client_watch_state) — его забор не трогаем.
APPLY_COOLDOWN_SEC = int(os.getenv("PC_APPLY_COOLDOWN_SEC", "120") or "120")
_apply_restart_at = {}     # name -> time.time() последнего авто-рестарта (userbot/moderbot)


def _stamp_apply_restart(name, now=None, state=None):
    (state if state is not None else _apply_restart_at)[name] = time.time() if now is None else now


def _apply_antiflap(name, now, cooldown, state):
    """True → рестарт подавить (этот процесс уже авто-рестартили меньше cooldown назад)."""
    last = state.get(name)
    return last is not None and (now - last) < cooldown


def _diff_names(old_commit, new_commit):
    """Файлы, изменённые между двумя коммитами (old..new). git молчит/ошибка/нет диффа → []."""
    if not old_commit or not new_commit or old_commit == new_commit:
        return []
    out = _git_out(["diff", "--name-only", f"{old_commit}..{new_commit}"])
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]


def _selfupdate_restart_children(old_commit, new_commit, diff_fn=None, restart_fn=None,
                                 now=None, cooldown=None, state=None):
    """После УСПЕШНОГО self-update демона: рестарт затронутых детей по ЯВНОЙ карте на основе диффа
    old..new. → строка-итог для лога/cowork ('' если никого не трогали). Правила:
      • userbot/moderbot → штатный рестарт механикой вотчдога (_restart_via_pc_agent), с уважением
        анти-флапа (недавно рестартили → пропуск);
      • pc_agent → НЕ трогаем чужими руками, только пометка «ждёт ручного рестарта»;
      • дифф пуст / тронуты только не-код-файлы (README/*.md/тесты) → никого не рестартим.
    На каждое применение — NOTE в cowork «авто-применил <коммит>: рестарт <кто>». Всё внешнее
    (дифф/рестарт/время/реестр) инъектируется — в тестах боевое не дёргаем."""
    if _stopped():
        return ""
    now = time.time() if now is None else now
    cooldown = APPLY_COOLDOWN_SEC if cooldown is None else cooldown
    state = _apply_restart_at if state is None else state
    changed = (diff_fn or _diff_names)(old_commit, new_commit)
    if not changed:
        return ""
    procs = {}          # name -> [files]
    for p in changed:
        for name in _procs_for_file(p):
            procs.setdefault(name, []).append(p)
    if not procs:
        return ""                                  # тронуты только не-код-файлы — никого не рестартим
    notes = []
    if "pc_agent" in procs:                        # агент себя чужими руками не рестартует — только пометка
        procs.pop("pc_agent")
        msg = ("pc_agent изменён — ЖДЁТ РУЧНОГО рестарта (Планировщик/сам подхватит), "
               "чужими руками не трогаю")
        log.info("self-update дети: %s", msg)
        _cowork(f"авто-применил {new_commit}: {msg}")
        _notify(f"ℹ️ Оркестратор: {msg} (self-update {new_commit})")
        notes.append(msg)
    for kind, label in (("userbot", "userbot"), ("moderbot", "модербот")):
        if kind not in procs:
            continue
        if _apply_antiflap(kind, now, cooldown, state):
            log.info("self-update дети: %s недавно рестартили — анти-флап, рестарт пропущен", kind)
            notes.append(f"{label}: анти-флап (недавно рестартили) — рестарт пропущен")
            continue
        try:
            ok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
        except Exception as e:
            ok, pids, detail = False, [], f"исключение рестарта: {e}"
        _stamp_apply_restart(kind, now, state)
        if ok and pids:
            log.info("self-update дети: %s рестартнут до %s, PID %s", kind, new_commit, pids)
            _cowork(f"авто-применил {new_commit}: рестарт {kind} (PID {', '.join(map(str, pids))})")
            notes.append(f"{label} рестартнут (PID {', '.join(map(str, pids))})")
        elif ok:                                    # рестарт не требовался (напр. модербот без токена)
            log.info("self-update дети: %s — %s", kind, detail)
            _cowork(f"авто-применил {new_commit}: рестарт {kind} — {_tail(detail, 160)}")
            notes.append(f"{label}: {detail}")
        else:
            log.error("self-update дети: рестарт %s НЕ УДАЛСЯ — %s", kind, detail)
            _cowork(f"авто-применил {new_commit}: рестарт {kind} НЕ удался — {_tail(detail, 160)}")
            _notify(f"⚠️ Оркестратор: {label} не рестартнут после self-update {new_commit}: {_tail(detail, 160)}")
            notes.append(f"{label}: рестарт НЕ удался — {_tail(detail, 160)}")
    return " ; ".join(notes)


# ------------------- реконсиляция детей на ЛЮБОЙ новый коммит (класс-фикс) ----
# РАЗБОР c6d8a30: свежий код ДЕТЕЙ (suggest/pricing/booking/moderation_*) применялся к живым
# процессам ТОЛЬКО когда (а) правку принёс дев-таск самого демона (maybe_update_bots по
# head_before..HEAD), ЛИБО (б) вместе с ней сменился блоб pc_orchestrator.py (self-update →
# _selfupdate_restart_children). Коммит, тронувший ТОЛЬКО файлы детей и пришедший ВНЕ дев-таска
# (cowork/ручной коммит/git pull) — оставлял детей на СТАРОМ коде до следующей смены
# pc_orchestrator.py. Так c6d8a30 (только suggest.py+тест) не подхватился, а f89da43 (менял
# pc_orchestrator.py) — подхватился self-update'ом. ФИКС: каждый цикл демон сверяет HEAD с
# последним ПРИМЕНЁННЫМ к детям коммитом; HEAD ушёл вперёд и в диффе есть файлы детей → рестарт
# затронутых (ТА ЖЕ явная карта _FILE_PROCESS_RULES + гейт затронутых тестов + анти-флап +
# рубильник — гейты НЕ ослабляем). Реагирует на изменённые файлы детей в диффе ДАЖЕ когда сам
# pc_orchestrator.py не менялся. Красный гейт → метку НЕ двигаем (диапазон со стале-кодом доживёт
# до фикс-коммита), но HEAD запоминаем, чтобы не гонять гейт каждый цикл (как _SU_REJECTED_BLOB).
CHILD_RECONCILE_SEC = int(os.getenv("PC_CHILD_RECONCILE_SEC", "60") or "60")
_last_child_commit = None         # коммит, чьи изменения детей уже применены (init = старт демона)
_child_reconcile_rejected = None  # HEAD с КРАСНЫМ гейтом детей — не гоняем гейт каждый цикл
_child_reconcile_last_run = 0.0   # троттлинг тела реконсиляции


def _full_head():
    """Полный хеш HEAD (для метки/диффа реконсиляции). git молчит → None."""
    return _git_out(["rev-parse", "HEAD"])


def reconcile_children_tick(head_fn=None, diff_fn=None, gate_fn=None, restart_fn=None,
                            now=None, cooldown=None, state=None):
    """Тело реконсиляции детей на новый коммит (без троттлинга — троттлит maybe_reconcile_children).
    → строка-итог для лога ('' если нечего/рубильник). Всё внешнее инъектируется для тестов.
    Метку/rejected хранит в модульных глобалах (переживают тики; рестарт демона сбрасывает —
    первый прогон просто примет текущий HEAD как применённый, т.к. дети стартовали с ним)."""
    global _last_child_commit, _child_reconcile_rejected
    if _stopped():
        return ""
    head = (head_fn or _full_head)()
    if not head:
        return ""
    if _last_child_commit is None:        # первый прогон: дети стартовали с текущим HEAD → он уже «применён»
        _last_child_commit = head
        return ""
    if head == _last_child_commit or head == _child_reconcile_rejected:
        return ""                         # нет нового коммита ИЛИ этот HEAD уже провалил гейт — ждём новый
    changed = (diff_fn or _diff_names)(_last_child_commit, head)
    ub_files, mb_files = _classify_changed(changed)
    if not (ub_files or mb_files):
        _last_child_commit = head         # тронуты только не-код-файлы детей (pc_orchestrator/доки/тесты) — двигаем метку
        return ""
    now = time.time() if now is None else now
    cooldown = APPLY_COOLDOWN_SEC if cooldown is None else cooldown
    state = _apply_restart_at if state is None else state
    short = head[:9]
    notes, gate_red = [], False
    for kind, label, files in (("userbot", "userbot", ub_files), ("moderbot", "модербот", mb_files)):
        if not files:
            continue
        mods = _affected_test_modules(files)
        ok, gmsg = (gate_fn or _gate_test_modules)(mods)
        if not ok:
            gate_red = True
            log.error("реконсиляция детей %s: гейт КРАСНЫЙ (%s) — рестарт отложен", kind, _tail(gmsg, 200))
            _notify(f"⚠️ Оркестратор: {label} НЕ перезапущен — тесты красные (код запушен, применится после фикса)")
            notes.append(f"{label}: тесты красные — рестарт отложен")
            continue
        if _apply_antiflap(kind, now, cooldown, state):   # уже рестартнули (дев-таск/self-update/прошлый тик) → код применён
            log.info("реконсиляция детей %s: недавно рестартили — анти-флап, пропуск", kind)
            notes.append(f"{label}: анти-флап (недавно рестартили) — пропуск")
            continue
        try:
            rok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
        except Exception as e:
            rok, pids, detail = False, [], f"исключение рестарта: {e}"
        _stamp_apply_restart(kind, now, state)
        if rok and pids:
            log.info("реконсиляция детей: %s рестартнут до %s, PID %s", kind, short, pids)
            _cowork(f"авто-применил {short}: рестарт {kind} (PID {', '.join(map(str, pids))})")
            notes.append(f"{label} рестартнут (PID {', '.join(map(str, pids))})")
        elif rok:                                          # рестарт не требовался (напр. модербот без токена)
            log.info("реконсиляция детей: %s — %s", kind, detail)
            _cowork(f"авто-применил {short}: рестарт {kind} — {_tail(detail, 160)}")
            notes.append(f"{label}: {detail}")
        else:
            log.error("реконсиляция детей: рестарт %s НЕ УДАЛСЯ — %s", kind, detail)
            _cowork(f"авто-применил {short}: рестарт {kind} НЕ удался — {_tail(detail, 160)}")
            _notify(f"⚠️ Оркестратор: {label} не рестартнут (реконсиляция {short}): {_tail(detail, 160)}")
            notes.append(f"{label}: рестарт НЕ удался")
    if gate_red:
        _child_reconcile_rejected = head   # метку НЕ двигаем: стале-код детей доживёт до фикс-коммита
    else:
        _last_child_commit = head
        _child_reconcile_rejected = None
    return " ; ".join(notes)


def maybe_reconcile_children(now=None):
    """Троттлинг реконсиляции детей: тело не чаще CHILD_RECONCILE_SEC. → строка|None (None = рано)."""
    global _child_reconcile_last_run
    now = time.time() if now is None else now
    if now - _child_reconcile_last_run < CHILD_RECONCILE_SEC:
        return None
    _child_reconcile_last_run = now
    return reconcile_children_tick()


# ------------------- контур-вотчдог клиентского контура (часть 3) -------------
# Демон каждые CLIENT_WATCH_SEC (5 мин) проверяет живость pc_agent/userbot/moderation_bot по
# PID (CIM-поиск процесса = источник правды) + свежести их логов (диагностика). Мёртвого
# поднимает ШТАТНО: pc_agent — через Планировщик (schtasks /Run), userbot/moderation_bot — той
# же механикой pc_agent (venv-spawn, CIM-guard от дубля). На каждый подъём — NOTE в cowork_log.
# Анти-флаппинг: не чаще 1 подъёма на процесс за CLIENT_COOLDOWN_SEC (15 мин); CLIENT_MAX_DEATHS
# (3) смертей подряд → СТОП попыток по этому процессу + громкий NOTE «нужен разбор». Живой снова
# → счётчик смертей сброшен. moderation_bot без MODERBOT_TOKEN — не «мёртв», а штатно не поднят
# (reply-режим) → пропускаем. Состояние в памяти демона (переживает тики; рестарт демона его
# сбрасывает — не страшно). Всё внешнее (finder/raiser/now/state) инъектируется для тестов.

_client_watch_state = {}      # name -> {"last_raise": float, "deaths": int, "halted": bool}
                              # спец-ключ "__blind__" -> счётчик подряд СЛЕПЫХ циклов (finder не смог)
_client_watch_last_run = 0.0  # монотонная метка последнего прогона контура (троттлинг 5 мин)
_client_grace_until = 0.0     # wall-clock: до этого момента вердикты «мёртв»/рестарты подавлены (ПК проснулся)
_loop_prev_wall = None        # wall-clock старта прошлой итерации главного цикла (детект скачка = сна)


def _find_pids_by_script(script_name):
    """PID python-процессов, исполняющих <script_name> (фиксированный CIM-запрос, read-only).
    ТРИ исхода — ключ фикса #171 («не смог проверить» ≠ «мёртв»):
      • список PID — CIM отработал, процесс(ы) найдены;
      • []         — CIM отработал, процессов НЕТ (честная пустота → повод к проверке смерти);
      • None       — CIM упал/таймаут/скрытый сбой (просыпающийся/тормозящий ПК) — исход НЕИЗВЕСТЕН.
    Раньше ошибка глушилась в [] → вотчдог принимал таймаут за смерть → лишний рестарт → дубль.
    script_name — литерал из спецификации контура (не пользовательский ввод)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
          "| Where-Object { $_.CommandLine -like '*" + script_name + "*' } "
          "| Select-Object -ExpandProperty ProcessId")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=20)
    except Exception as e:
        log.warning("контур-вотчдог: CIM-поиск %s не удался (%s) — исход НЕИЗВЕСТЕН, НЕ считаем мёртвым", script_name, e)
        return None
    pids = [int(x) for x in (p.stdout or "").split() if x.strip().isdigit()]
    if pids:
        return pids
    # Пусто: отличаем ЧЕСТНУЮ пустоту (rc=0, тихий stderr) от СКРЫТОГО сбоя CIM (rc!=0 / ошибка в stderr).
    if p.returncode != 0 or (p.stderr or "").strip():
        log.warning("контур-вотчдог: CIM-поиск %s пуст, но rc=%s / stderr=%r — исход НЕИЗВЕСТЕН, НЕ считаем мёртвым",
                    script_name, p.returncode, _tail((p.stderr or "").strip(), 120))
        return None
    return []


def _raise_pc_agent():
    """Поднять pc_agent через Планировщик (у него отдельная задача schtasks). → (ok, detail)."""
    rc, out = _schtasks_run("pc_agent")
    return rc == 0, f"schtasks /Run /TN pc_agent rc={rc}: {_tail(out, 160)}"


def _raise_client_bot(kind):
    """Поднять userbot/moderation_bot ТОЙ ЖЕ механикой pc_agent (venv-spawn + CIM-guard). → (ok, detail).
    kind ∈ 'userbot'|'moderbot'. start() идемпотентен: если процесс уже есть — второй не создаст."""
    try:
        import pc_agent   # lazy: не тянем telegram в общий импорт демона
    except Exception as e:
        return False, f"импорт pc_agent не удался: {e}"
    try:
        proc = pc_agent.UserbotProcess() if kind == "userbot" else pc_agent.ModerbotProcess()
        return True, proc.start()
    except Exception as e:
        return False, f"ошибка старта {kind}: {e}"


def _client_watch_specs():
    """Спецификации процессов клиентского контура. skip() → «не применимо» (не считаем мёртвым)."""
    return [
        {"name": "pc_agent",
         "finder": lambda: _find_pids_by_script("pc_agent.py"),
         "raiser": _raise_pc_agent,
         "logfile": os.path.join(REPO, "pc_agent.log"),
         "skip": lambda: False},
        {"name": "userbot",
         "finder": lambda: _find_pids_by_script("userbot_listen.py"),
         "raiser": lambda: _raise_client_bot("userbot"),
         "logfile": os.path.join(REPO, "userbot.log"),
         "skip": lambda: False},
        {"name": "moderation_bot",
         "finder": lambda: _find_pids_by_script("moderation_bot.py"),
         "raiser": lambda: _raise_client_bot("moderbot"),
         "logfile": os.path.join(REPO, "moderation_bot.log"),
         # без токена модербот НЕ должен работать (reply-режим) — это не смерть, а штатный простой
         "skip": lambda: not os.getenv("MODERBOT_TOKEN", "").strip()},
    ]


def _log_age_sec(path, now):
    """Возраст (сек) последней записи в лог. now — time.time(). None — файла нет/ошибка."""
    try:
        return max(0.0, now - os.path.getmtime(path))
    except Exception:
        return None


def _client_watch_step(name, alive, now, state, cooldown, max_deaths):
    """Чистое решение по ОДНОМУ процессу. → (action, new_state_entry). Побочек нет — подъём делает
    вызывающий. action ∈ 'alive'|'raise'|'cooldown'|'halted'|'halt_now'."""
    st = dict(state.get(name) or {"last_raise": 0.0, "deaths": 0, "halted": False})
    if alive:
        return "alive", {"last_raise": st["last_raise"], "deaths": 0, "halted": False}
    if st["halted"]:
        return "halted", st                        # уже сдались (громкий NOTE был при переходе)
    if now - st["last_raise"] < cooldown:
        return "cooldown", st                       # анти-флап: рано поднимать снова
    st["deaths"] += 1
    st["last_raise"] = now
    if st["deaths"] >= max_deaths:
        st["halted"] = True
        return "halt_now", st                       # смерть №max_deaths подряд → стоп, не поднимаем
    return "raise", st


def client_watchdog_tick(now=None, specs=None, state=None, cooldown=None, max_deaths=None,
                         grace_until=None, log_stale=None, blind_alarm=None):
    """Один прогон контур-вотчдога. → dict name->action (для тестов/лога). Побочки: raiser()+NOTE.
    Уважает рубильник pc_orchestrator.stop (клиентский контур при намеренной остановке не трогаем).
    Фикс #171: finder РАЗЛИЧАЕТ три исхода (см. _find_pids_by_script); «мёртв» требует ТРЁХ условий
    (finder успешен + процесса нет + лог протух); grace после пробуждения ПК; слепой-счётчик → алярм."""
    now = time.time() if now is None else now
    specs = _client_watch_specs() if specs is None else specs
    state = _client_watch_state if state is None else state
    cooldown = CLIENT_COOLDOWN_SEC if cooldown is None else cooldown
    max_deaths = CLIENT_MAX_DEATHS if max_deaths is None else max_deaths
    grace_until = _client_grace_until if grace_until is None else grace_until
    log_stale = CLIENT_LOG_STALE if log_stale is None else log_stale
    blind_alarm = CLIENT_BLIND_ALARM if blind_alarm is None else blind_alarm
    if _stopped():
        return {"_": "stopped"}
    # (2) GRACE после пробуждения ПК: в окне WAKE_GRACE_SEC никаких вердиктов «мёртв»/рестартов —
    # CIM на только что проснувшемся ПК медленный, «пусто» здесь недостоверно. Только логируем.
    if grace_until and now < grace_until:
        log.info("контур-вотчдог: grace после пробуждения ПК (%sс осталось) — вердикты отложены",
                 int(grace_until - now))
        return {"_": "grace"}
    out = {}
    considered = 0        # сколько процессов реально проверяли (не skip)
    any_success = False   # хоть один finder дал достоверный ответ (OK+список или OK+пусто)
    for sp in specs:
        name = sp["name"]
        # skip (штатный простой, напр. модербот без токена) — это НЕ смерть, вне слепого-счётчика
        try:
            if sp.get("skip") and sp["skip"]():
                out[name] = "skip"
                state[name] = {"last_raise": 0.0, "deaths": 0, "halted": False}
                continue
        except Exception as e:
            log.warning("контур-вотчдог: skip-проверка %s упала: %s", name, e)
        considered += 1
        # (1) finder РАЗЛИЧАЕТ три исхода. None (или исключение) = «не смог проверить» → SKIP цикла
        # проверки ЭТОГО процесса, БЕЗ рестарта. Это НЕ смерть.
        try:
            pids = sp["finder"]()
        except Exception as e:
            log.warning("контур-вотчдог: finder %s упал (%s) — SKIP, без рестарта", name, e)
            pids = None
        if pids is None:
            out[name] = "check_failed"
            log.warning("контур-вотчдог: не смог проверить %s (finder слеп) — SKIP цикла, БЕЗ рестарта", name)
            continue
        any_success = True
        alive = bool(pids)
        if not alive:
            # (1) «мёртв» (повод к рестарту) = finder УСПЕШЕН И процесса нет И лог протух > порога.
            # Свежий лог = недавняя активность/возможная гонка CIM → рестарт ВЕТИРУЕМ (строже к рестарту).
            log_age = _log_age_sec(sp.get("logfile"), now)
            if log_age is not None and log_age <= log_stale:
                out[name] = "fresh_log"
                log.warning("контур-вотчдог: %s без PID, но лог свеж (%sс ≤ %sс) — смерть НЕ доказана, рестарт отложен",
                            name, int(log_age), log_stale)
                continue
        action, st = _client_watch_step(name, alive, now, state, cooldown, max_deaths)
        state[name] = st
        out[name] = action
        if action == "raise":
            try:
                ok, detail = sp["raiser"]()
            except Exception as e:
                ok, detail = False, f"raiser упал: {e}"
            log_age = _log_age_sec(sp.get("logfile"), now)
            log.warning("контур-вотчдог: %s МЁРТВ (смерть %s/%s, лог %s) → подъём ok=%s: %s",
                        name, st["deaths"], max_deaths,
                        (f"{int(log_age)}с назад" if log_age is not None else "нет"), ok, _tail(str(detail), 200))
            _cowork(f"вотчдог поднял {name} (смерть {st['deaths']}/{max_deaths}): {_tail(str(detail), 160)}")
            if not ok:
                _notify(f"⚠️ Оркестратор: контур-вотчдог не смог поднять {name}: {_tail(str(detail), 160)}")
        elif action == "halt_now":
            log.error("контур-вотчдог: %s умер %s раз подряд — СТОП попыток, нужен разбор", name, st["deaths"])
            _cowork(f"вотчдог: {name} умер {st['deaths']} раза подряд — СТОП, нужен разбор")
            _notify(f"⚠️ Оркестратор: {name} умер {max_deaths} раза подряд — контур-вотчдог остановлен, нужен разбор")
    # (1) Слепой-счётчик: цикл СЛЕП, если процессы проверяли, но НИ ОДИН finder не смог ответить.
    # blind_alarm подряд слепых → NOTE «вотчдог слеп — глянь ПК» (алярм, НЕ рестарты). Любой успешный
    # finder обнуляет счётчик. (На спящем/тормозящем ПК все CIM-запросы таймаутят вместе.)
    if considered and not any_success:
        blind = int(state.get("__blind__", 0)) + 1
        state["__blind__"] = blind
        out["__blind__"] = blind
        if blind >= blind_alarm:
            log.error("контур-вотчдог: %s циклов подряд СЛЕП (CIM не отвечает) — нужен глаз на ПК", blind)
            _cowork(f"вотчдог слеп {blind} цикла подряд (CIM не отвечает) — глянь ПК (спит/тормозит?)")
            _notify(f"⚠️ Оркестратор: контур-вотчдог слеп {blind} цикла подряд — CIM не отвечает, глянь ПК")
            state["__blind__"] = 0   # сброс после алярма (не спамим каждый тик; ре-алярм ещё через blind_alarm)
    elif any_success:
        state["__blind__"] = 0
    return out


def maybe_client_watchdog(now=None):
    """Троттлинг контур-вотчдога: тело прогоняем не чаще CLIENT_WATCH_SEC. → dict|None (None = ещё рано)."""
    global _client_watch_last_run
    now = time.time() if now is None else now
    if now - _client_watch_last_run < CLIENT_WATCH_SEC:
        return None
    _client_watch_last_run = now
    return client_watchdog_tick(now=now)


def _woke_from_sleep(now, prev, poll=None, margin=None):
    """(2) Детект пробуждения ПК: между соседними итерациями главного цикла wall-clock скакнул
    много больше интервала поллинга (ПК спал в S3/гибернации — состояния доступны, см. powercfg /a).
    Чистая/тестируемая. prev=None (первый виток) → пробуждением НЕ считаем."""
    poll = POLL_SEC if poll is None else poll
    margin = WAKE_JUMP_MARGIN if margin is None else margin
    return prev is not None and (now - prev) > (poll + margin)


# ------------------- OS-синглтон демона (разбор #128, часть 4) ----------------
# Инцидент: ДВА pc_orchestrator одновременно (оба стартовали в одну секунду от Планировщика
# поверх уже живого) → оба поллят очередь и наперегонки claim'ят задачи (двойное исполнение).
# Класс-фикс: атомарный lock-файл с PID (O_CREAT|O_EXCL, как в pc_agent). Второй живой демон
# при старте видит лок живого и выходит. Дубль, поднявшийся БЕЗ лока (старый код), схлопнется
# сам: при первом же self-update оба старых спавнят новых, но лок эксклюзивен — выживает ОДИН.
# Self-update-эстафета: старый спавнит нового с env PC_ORCH_SUPERSEDE_PID=<свой PID>; новый,
# если лок держит именно этот PID, ЖДЁТ его смерти (старый снимает лок в finally main()) —
# гарантированно «гасим старого перед стартом нового», перекрытия нет.

def _lock_pid_alive(pid):
    """Жив ли процесс по PID (Windows, без psutil). Инъектируется в тестах."""
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=10)
        return f'"{pid}"' in r.stdout or f",{pid}," in r.stdout
    except Exception:
        return False


def _read_lock_pid(path=None):
    try:
        with open(path or LOCK_FILE, encoding="utf-8") as f:   # закрываем сразу: на Windows
            return int(f.read().strip() or "0")                # висящий хэндл блокирует os.remove
    except Exception:
        return 0


def acquire_singleton(lock_path=None, pid_alive=None, supersede_wait=30.0, sleep=0.5):
    """True — лок наш, стартуем; False — другой ЖИВОЙ демон уже держит лок, выходим (второй не поднимаем).
    Мёртвый холдер → забираем лок. Холдер == наш supersede-PID (self-update) → ждём его смерти до
    supersede_wait, затем забираем (эстафета). pid_alive/lock_path инъектируются в тестах."""
    lock_path = lock_path or LOCK_FILE
    pid_alive = pid_alive or _lock_pid_alive
    sup = os.getenv(SUPERSEDE_ENV, "").strip()
    sup = int(sup) if sup.lstrip("-").isdigit() else 0
    deadline = time.time() + supersede_wait
    for _ in range(200):                       # верхняя граница итераций (страховка от вечного цикла)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            holder = _read_lock_pid(lock_path)
            if holder == os.getpid():          # уже наш (перезабор) — считаем успехом
                return True
            if holder == 0 or not pid_alive(holder):
                log.warning("singleton: устаревший лок (PID %s мёртв) — забираю", holder or "?")
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue                        # заберём на следующем витке
            if holder == sup and time.time() < deadline:
                time.sleep(sleep)               # self-update: ждём смерти сменяемого старого демона
                continue
            if holder == sup:                   # старый завис дольше окна — successor всё равно забирает
                log.warning("singleton: сменяемый PID %s не умер за %sс — забираю лок (successor)", holder, supersede_wait)
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue
            log.warning("singleton: pc_orchestrator уже запущен (живой PID %s) — второй НЕ стартую", holder)
            return False
    log.error("singleton: не смог получить лок за 200 итераций — НЕ стартую (страховка)")
    return False


def release_singleton(lock_path=None):
    """Снять лок ТОЛЬКО если он наш (чужой/successor'ский лок не трогаем)."""
    lock_path = lock_path or LOCK_FILE
    try:
        if _read_lock_pid(lock_path) == os.getpid():
            os.remove(lock_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("singleton: не смог снять лок: %s", e)


def main():
    # OS-синглтон ПЕРВЫМ действием: два демона одновременно недопустимы (двойной claim задач).
    if not acquire_singleton():
        log.warning("=== ВТОРОЙ ЭКЗЕМПЛЯР ДЕМОНА — выхожу (singleton-лок держит живой процесс) ===")
        return
    try:
        _main_loop()
    finally:
        release_singleton()


def _main_loop():
    _init_running_version()
    log.info("=== ДЕМОН СТАРТ (lane=%s, poll=%ss, task_timeout=%ss, approval_ttl=%ss, claude=%s, commit=%s) ===",
             LANE, POLL_SEC, TASK_TIMEOUT, APPROVAL_TTL, CLAUDE_BIN, RUNNING_COMMIT)
    if _stopped():
        log.info("рубильник pc_orchestrator.stop активен — не стартую поллинг")
        return
    global _loop_prev_wall, _client_grace_until
    while not _stopped():
        try:
            # (2) Детект сна/пробуждения ДО вотчдога: если ПК спал, wall-clock скакнёт — взводим grace,
            # чтобы первый пост-пробуждение прогон вотчдога (троттлинг уже истёк) не принял медленный
            # CIM за смерть и не рестартнул зря. Взводим ТОЛЬКО на реальном скачке; норм. виток не трогаем.
            now = time.time()
            if _woke_from_sleep(now, _loop_prev_wall):
                _client_grace_until = now + WAKE_GRACE_SEC
                gap = int(now - _loop_prev_wall)
                log.warning("детект пробуждения ПК: скачок wall-clock %sс (>%s+%s) — grace вотчдога %sс",
                            gap, POLL_SEC, WAKE_JUMP_MARGIN, WAKE_GRACE_SEC)
            _loop_prev_wall = now
            poll_once()
            maybe_client_watchdog()   # часть 3: следим за pc_agent/userbot/moderation_bot (троттлинг 5 мин)
            maybe_reconcile_children()  # класс-фикс c6d8a30: применить свежий код детей на ЛЮБОЙ новый коммит
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
    elif arg == "--enqueue":
        # ПРЯМОЙ КАНАЛ (этап 1): инъекция одиночки lane=pc прямо в очередь Bridge, минуя splinter.
        # Демон подхватит обычным поллингом. Работает даже при лежащем Splinter/девботе.
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        ok, nid, err = enqueue_pc_task(text)
        if ok:
            print(f"OK: одиночка lane={LANE} поставлена в очередь id={nid} (прямой канал, минуя splinter)")
        else:
            print(f"FAIL enqueue: {err}")
            sys.exit(1)
    else:
        main()
