# -*- coding: utf-8 -*-
"""
dispatch_notify.py — fire-and-forget Telegram-уведомление от Dispatch/Claude Code.

Зеркало VPS-паттерна: когда сессия на ПК ждёт разрешения/ввода ИЛИ завершила задачу,
Филиппу уходит сообщение (он пропускает запросы в приложении).

АДРЕС ВЫБИРАЕТ ОДИН ПРИЗНАК — ЖДЁТ ЛИ СООБЩЕНИЕ ОТВЕТА ВЛАДЕЛЬЦА (deliver/awaits_reply,
правило владельца 10.08.2026). Ждёт → тема Инбокс 1160 (личка — фолбэк). Не ждёт (итог сессии,
сводка, «задача выполнена») → тема постановки задач 328 (инбокс → личка — фолбэки). Вид
сообщения адресом больше не распоряжается: перечень видов протухает на первом новом виде.
Замок: карточка с кнопкой и высшая карточка §7 попадают в инбокс ВСЕГДА, мимо признака.
Константы каналов берём из pc_agent (единый источник).

НЕ НАВРЕДИ: любые сетевые/HTTP-ошибки проглатываются, короткий лог в dispatch_notify.log,
ВСЕГДА exit 0 — уведомление никогда не роняет вызывающий процесс/сессию. Таймаут на запрос.
Токен читаем из .env (AGENT_BOT_TOKEN), НИКОГДА не логируем и не печатаем.

Использование:
  python dispatch_notify.py "любой текст"          # прямая отправка (аргумент)
  echo '{"message":"..."}' | python dispatch_notify.py --hook notification
  python dispatch_notify.py --hook stop
  python dispatch_notify.py --hook session_end     # ЗАВЕРШЕНИЕ Code-сессии → тема 328 + cowork_log
"""

import os
import re
import sys
import time
import io
import json
import logging
import contextlib
import subprocess
import urllib.request
import urllib.error

import io_utf8   # переключатель stdout/stderr в UTF-8 (класс «charmap can't encode 📊»)

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
NOTIFY_LOG = os.path.join(HERE, "dispatch_notify.log")
COWORK_LOG = os.path.join(HERE, "cowork_hook.log")   # вывод ОТДЕЛЁННОЙ записи в cowork_log
HTTP_TIMEOUT = 8  # сек
SUMMARY_MAX = 300     # символов краткой сводки в карточке завершения сессии
# Скрытый спавн cowork_log_append (класс «мигающие чёрные окна» 22.07: каждый console-ребёнок
# без этого флага открывал НОВОЕ окно на ПК владельца). POSIX → 0.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# --- лог ТОЛЬКО в свой файл (не трогаем root / pc_agent.log) ---
_log = logging.getLogger("dispatch_notify")
_log.setLevel(logging.INFO)
_log.propagate = False
try:
    import log_setup                       # ротация + тестовый лог в temp (см. log_setup)
    _h = log_setup.rotating_handler(NOTIFY_LOG)
    if _h is not None:
        _log.addHandler(_h)
except Exception:
    try:
        _h = logging.FileHandler(NOTIFY_LOG, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        _log.addHandler(_h)
    except Exception:
        pass


def _load_env(path):
    vals = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return vals


_env = _load_env(ENV_PATH)
TOKEN = (_env.get("AGENT_BOT_TOKEN", "") or os.getenv("AGENT_BOT_TOKEN", "")).strip()

# Каналы — из конфига pc_agent (единый источник констант). Импорт под подавлением любого
# stdout/stderr (чтобы не засорять hook-протокол) и с безопасным фолбэком.
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import pc_agent  # noqa: E402
    DM_CHAT_ID = int(pc_agent.ALLOWED_USER_ID)
    HQ_CHAT_ID = int(pc_agent.HQ_CHAT_ID)
    HQ_THREAD_ID = int(pc_agent.HQ_THREAD_ID)
except Exception as e:
    _log.info(f"pc_agent-конфиг недоступен ({type(e).__name__}) — фолбэк на env/дефолт")
    DM_CHAT_ID = int(os.getenv("DISPATCH_DM_CHAT_ID", "504608015"))
    HQ_CHAT_ID = int(os.getenv("HQ_CHAT_ID", "-1003853365891"))
    HQ_THREAD_ID = int(os.getenv("HQ_THREAD_ID", "205"))

# Тема Инбокс HQ-форума: критические инциденты контура (доказанная смерть демона / 3-смерти-halt
# клиент-бота / halt-слепота вотчдога) сыплются СЮДА, а не в личку. Личка Филиппа — ФОЛБЭК, если
# форум недоступен (бота выкинуло из темы / форум лёг). Тот же чат HQ_CHAT_ID, другая тема.
INBOX_THREAD_ID = int(os.getenv("HQ_INBOX_THREAD_ID", "1160"))

# Тема ПОСТАНОВКИ ЗАДАЧ владельца (328) — туда же, куда владелец кладёт задания и куда смотрит
# по ходу дня. Сигналы про САМИ ЗАДАНИЯ (например «немая сессия: задание не начало исполняться»,
# session_watch.py) уместны рядом с заданием, а не в инбоксе аварий контура: владелец читает 328
# в том же контексте, в каком ставил задачу. Маршрут — send_topic (тема → 1160 → личка).
TASKS_THREAD_ID = int(os.getenv("HQ_TASKS_TOPIC", "328"))


def _api(method, payload):
    """POST в Bot API. Возвращает (ok, body). Токен/URL НЕ логируем."""
    url = "https://api.telegram.org/bot" + TOKEN + "/" + method
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = json.loads(r.read().decode("utf-8"))
            return bool(body.get("ok")), body
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "error_code": getattr(e, "code", None), "description": str(getattr(e, "reason", ""))}
        return False, body
    except Exception as e:
        return False, {"ok": False, "description": type(e).__name__}


def _chain_markup(pid):
    """Инлайн-клавиатура управления цепью дирижёра: [⏹ Стоп цепи][📊 Статус цепи]. callback_data
    «chain:stop:<pid>» / «chain:status:<pid>» слушает pc_agent (owner-gate: только владелец)."""
    return {"inline_keyboard": [[
        {"text": "⏹ Стоп цепи", "callback_data": f"chain:stop:{pid}"},
        {"text": "📊 Статус цепи", "callback_data": f"chain:status:{pid}"},
    ]]}


def send(text, reply_markup=None):
    """DM Филиппу; при неудаче — фолбэк в тему 205. Возвращает (channel, ok).
    reply_markup (dict инлайн-клавиатуры) — необязательные кнопки; едут в обоих каналах.

    С 10.08.2026 этот каскад АДРЕСОМ ПО УМОЛЧАНИЮ не является: адрес выбирает deliver по
    признаку «ждёт ли ответа». Функция осталась примитивом лички (её зовут фолбэки и внешние
    вызовы) — но сама по себе она ведёт МИМО инбокса, и потому не годится для сообщения,
    ждущего ответа."""
    if not TOKEN:
        _log.info("нет AGENT_BOT_TOKEN — уведомление пропущено")
        return ("none", False)
    dm = {"chat_id": DM_CHAT_ID, "text": text}
    if reply_markup is not None:
        dm["reply_markup"] = reply_markup
    ok, resp = _api("sendMessage", dm)
    if ok:
        _log.info(f"DM ok → {DM_CHAT_ID}")
        return ("DM", True)
    _log.info(
        f"DM не прошёл (code={resp.get('error_code')} {str(resp.get('description',''))[:80]}) "
        f"— фолбэк в тему {HQ_THREAD_ID}"
    )
    fb = {"chat_id": HQ_CHAT_ID, "message_thread_id": HQ_THREAD_ID, "text": text}
    if reply_markup is not None:
        fb["reply_markup"] = reply_markup
    ok2, resp2 = _api("sendMessage", fb)
    if ok2:
        _log.info(f"фолбэк 205 ok → {HQ_CHAT_ID}/{HQ_THREAD_ID}")
        return ("205", True)
    _log.info(f"фолбэк 205 не прошёл (code={resp2.get('error_code')} {str(resp2.get('description',''))[:80]})")
    return ("205", False)


def send_critical(text, reply_markup=None):
    """Критический инцидент контура → тема Инбокс HQ-форума (1160) ПЕРВЫМ каналом; личка Филиппа —
    ФОЛБЭК при недоступности форума (бота выкинуло из темы / форум лёг / 403). Возвращает
    (channel, ok). Зеркало send(), но маршрут перевёрнут: форум → личка (а не личка → тема).
    reply_markup едет ОБОИМИ каналами: у карточки с кнопками смена канала не смеет отнять
    кнопку — иначе владельцу нечем ответить (см. deliver, ЗАМОК-1)."""
    if not TOKEN:
        _log.info("нет AGENT_BOT_TOKEN — критическое уведомление пропущено")
        return ("none", False)
    box = {"chat_id": HQ_CHAT_ID, "message_thread_id": INBOX_THREAD_ID, "text": text}
    if reply_markup is not None:
        box["reply_markup"] = reply_markup
    ok, resp = _api("sendMessage", box)
    if ok:
        _log.info(f"критич. в инбокс ok → {HQ_CHAT_ID}/{INBOX_THREAD_ID}")
        return ("inbox", True)
    _log.info(
        f"инбокс {INBOX_THREAD_ID} не прошёл (code={resp.get('error_code')} "
        f"{str(resp.get('description',''))[:80]}) — фолбэк в личку {DM_CHAT_ID}"
    )
    dm = {"chat_id": DM_CHAT_ID, "text": text}
    if reply_markup is not None:
        dm["reply_markup"] = reply_markup
    ok2, resp2 = _api("sendMessage", dm)
    if ok2:
        _log.info(f"фолбэк личка ok → {DM_CHAT_ID}")
        return ("DM", True)
    _log.info(f"фолбэк личка не прошёл (code={resp2.get('error_code')} {str(resp2.get('description',''))[:80]})")
    return ("DM", False)


def send_topic(text, thread_id=None, reply_markup=None):
    """Сообщение в ЗАДАННУЮ тему HQ-форума (по умолчанию 328 — тема постановки задач владельца).
    Каскад: тема → инбокс 1160 → личка. Возвращает (channel, ok).

    ЗАЧЕМ отдельный маршрут, а не send_critical: 1160 — тема, которую владелец открывает РАДИ
    ОТВЕТА. Сообщение, ответа не ждущее (итог сессии, сводка, «задача выполнена»), место имеет
    рядом с самим заданием (328) — там владелец его и ждёт по ритуалу. Фолбэки оставлены оба,
    чтобы сигнал не утонул, если бота выкинуло из темы постановки."""
    tid = TASKS_THREAD_ID if thread_id is None else int(thread_id)
    if not TOKEN:
        _log.info("нет AGENT_BOT_TOKEN — уведомление в тему пропущено")
        return ("none", False)
    box = {"chat_id": HQ_CHAT_ID, "message_thread_id": tid, "text": text}
    if reply_markup is not None:
        box["reply_markup"] = reply_markup
    ok, resp = _api("sendMessage", box)
    if ok:
        _log.info(f"в тему {tid} ok → {HQ_CHAT_ID}/{tid}")
        return (f"topic:{tid}", True)
    _log.info(
        f"тема {tid} не прошла (code={resp.get('error_code')} "
        f"{str(resp.get('description',''))[:80]}) — фолбэк инбокс/личка"
    )
    return send_critical(text, reply_markup)


# ═══════════ ОДИН ПРИЗНАК МАРШРУТА: ЖДЁТ ЛИ СООБЩЕНИЕ ОТВЕТА ВЛАДЕЛЬЦА (10.08.2026) ═══════════
# Правило владельца: в инбокс 1160 попадает ТОЛЬКО то, что ждёт его ответа. Всё остальное —
# итоги сессий, сводки, уведомления о завершении — уезжает в тему постановки задач (328), где
# итог и так обещан ритуалом.
#
# ЧТО БЫЛО СЛОМАНО. Адрес выбирал ВИД сообщения, а не его существо: в main() стояла лесенка
# `if kind == "session_end": send_critical(...)` / `if kind == "notification": send_critical(...)`.
# Перечень видов протухает на первом новом виде, а «✅ Code-сессия завершена: …» ответа не ждёт
# вовсе — и за наблюдение 03.07–10.08 пришла в инбокс 175 раз против 9 настоящих тревог. Владелец
# открывал приложение впустую и переставал замечать настоящие карточки.
#
# ПРИЗНАК ОДИН и меряется по САМОМУ сообщению (порядок важен):
#   1) ЗАМОК — есть кнопка (куда отвечать) или это высшая карточка §7 → инбокс ВСЕГДА, мимо
#      всякого признака: замок проверяется ДО него и переживает его поломку (см. deliver);
#   2) названо ожидание от владельца (вопрос строкой, «ждёт твоего «да»», «нужен разбор») → ждёт;
#   3) назван ЗАКРЫТЫЙ исход («завершена», «выполнена», «провалена», «сторож поднял») → не ждёт;
#   4) ни того, ни другого → СЧИТАЕМ, ЧТО ЖДЁТ. Незакрытое состояние — не тишина; потерянная
#      карточка дороже лишней строки в инбоксе (тот же выбор, что у писателя журнала: длинная
#      запись лучше потерянной).
# Слова обоих признаков — не выдумка, а СНЯТЫЙ КОРПУС: 2202 живые записи dispatch_notify.log,
# разбор — docs/artifacts/2026-08-10-inbox-awaits-reply-route.md.

# Маркер ВЫСШЕЙ карточки §7 (живые таблицы, деньги, клиентский контур, выкатка прода, удаление
# вне tmp, снос процессов). Копия головы pretool_guard.TOP_TIER_BANNER — гарда НЕ импортируем:
# notify обязан оставаться самодостаточным fire-and-forget (та же причина, что у копии
# _RE_SECRET_VALUE ниже). Гард свою строку не переписывает, а замок ловит её голову.
TOP_TIER_MARK = "ВЫСШАЯ ЦЕНА"

# (2) НАЗВАННОЕ ОЖИДАНИЕ ОТ ВЛАДЕЛЬЦА. Дословные формы из корпуса: «— разрешить?» (705 карточек
# гарда), «нужно твоё «да»», «задача #N ждёт твоего «да»», «Dispatch ждёт твоего разрешения/
# ввода», «нужен разбор», «нужна расшифровка», «ЖДЁТ РУЧНОГО рестарта». Вопрос считаем только
# КОНЦОМ СТРОКИ: так «разрешить?» карточки ловится, а вопросительный знак внутри пересказа
# сводки — нет.
_RE_ASKS = re.compile(
    r"(?imu)(\?\s*$"
    r"|жд[её]т\s+тво|жд[её]т\s+ручного|жду\s+тво"
    r"|нужн[оаы]\s+тво|нужен\s+тво"
    r"|нужен\s+разбор|нужна\s+расшифровка"
    r"|требуется\s+подтверждени|подтверждение\s+только\s+с\s+объектом"
    r"|разрешить\b|разреши\b)")

# (3) ЗАКРЫТЫЙ ИСХОД: работа кончилась, от владельца ничего не ждут. Дословные формы из корпуса:
# «задача завершена», «Code-сессия завершена», «задача #N выполнена», «задача #N провалена»,
# «СТОРОЖ ПОДНЯЛ userbot», «ПК СПАЛ … — весь контур стоял».
_RE_CLOSED = re.compile(
    r"(?imu)(заверш[её]н|выполнен|провален|сторож\s+подн[яё]л|пк\s+спал"
    r"|устран[её]н|починен|перезапущен)")


def locked_to_inbox(text, reply_markup=None):
    """ЗАМОК (1): сообщение обязано попасть в инбокс 1160, ЧТО БЫ НИ СЛУЧИЛОСЬ С ПРИЗНАКОМ.
    Два повода, оба структурные, а не словесные:
      • есть инлайн-кнопки — владельцу физически некуда нажать вне инбокса;
      • высшая карточка §7 — операционное состояние без «да» владельца не меняется.
    → bool. Ошибка внутри = True: сомнение решается в пользу инбокса."""
    try:
        if reply_markup:
            return True
        return TOP_TIER_MARK in str(text)
    except Exception:
        return True


def awaits_reply(text, reply_markup=None, declared=None):
    """ОДИН признак маршрута: ЖДЁТ ЛИ сообщение ОТВЕТА владельца. → bool.

    declared — ответ на ТОТ ЖЕ вопрос от отправителя, который про своё сообщение знает точно
    (`--critical`: инцидент контура сам не рассосётся). Это не отдельный маршрут и не вид
    сообщения: адрес по-прежнему выбирает deliver, признак остаётся один.

    text приводим к строке БЕЗ `or ""`: пустоты у сообщения нет, а нестроковый вход не
    притворяется тишиной — он просто не совпадёт ни с одним признаком и уедет в инбокс."""
    t = str(text)
    if locked_to_inbox(t, reply_markup):
        return True
    if declared is not None:
        return bool(declared)
    if _RE_ASKS.search(t):
        return True
    if _RE_CLOSED.search(t):
        return False
    return True


def deliver(text, reply_markup=None, declared=None):
    """ЕДИНСТВЕННОЕ МЕСТО ВЫБОРА АДРЕСА. Ждёт ответа → инбокс 1160 (личка — фолбэк).
    Не ждёт → тема постановки задач 328 (инбокс → личка — фолбэки). → (channel, ok).

    Замок стоит ПЕРЕД признаком и не зависит от него: даже если awaits_reply сломается или
    его подменят, карточка с кнопкой и высшая карточка §7 уедут в инбокс. Исключение в самом
    признаке тоже читается как «ждёт» — молчание дороже лишней строки."""
    if locked_to_inbox(text, reply_markup):
        return send_critical(text, reply_markup)
    try:
        waits = bool(awaits_reply(text, reply_markup, declared))
    except Exception as e:                       # noqa: BLE001 — признак не смеет терять карточку
        _log.info(f"признак маршрута упал ({type(e).__name__}) — считаем, что ждёт ответа")
        waits = True
    if waits:
        return send_critical(text, reply_markup)
    return send_topic(text, None, reply_markup)


def _cowork(line, spawner=None):
    """ВТОРОЙ канал ритуала: строка-итог в cowork_log (мозг) через cowork_log_append.py.

    ОТДЕЛЁННЫЙ (detached) спавн, НЕ ждём: живой замер — карточка в Telegram уходит за ~1 с, а
    запись в cowork_log это ДВА round-trip к Bridge на VPS (~8 с). Синхронное ожидание держало
    SessionEnd-хук ~9.4 с, и Claude Code гасил его на выходе сессии («SessionEnd hook … failed:
    Hook cancelled») — оба канала терялись. Теперь хук отдаёт управление сразу, а запись в мозг
    доживает сама (DETACHED_PROCESS переживает смерть сессии-родителя).
    Вывод ребёнка льём в ОТДЕЛЬНЫЙ cowork_hook.log: два процесса, дописывающие один файл, рвут
    друг другу строки (живой прогон: строка лога осталась обрезком «…ена (pid=2416)»).
    → bool: True = процесс записи запущен (не «запись подтверждена»)."""
    py = os.path.join(HERE, "venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    cmd = [py, os.path.join(HERE, "cowork_log_append.py"), line]
    flags = NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        sink = open(COWORK_LOG, "a", encoding="utf-8")
    except Exception:
        sink = subprocess.DEVNULL
    try:
        p = (spawner or subprocess.Popen)(cmd, stdout=sink, stderr=sink,
                                          stdin=subprocess.DEVNULL, creationflags=flags,
                                          close_fds=True, cwd=HERE)
        _log.info(f"cowork_log: запись отделена (pid={getattr(p, 'pid', '?')}) | {line[:90]}")
        return True
    except Exception as e:
        _log.info(f"cowork_log: спавн не удался ({type(e).__name__}) | {line[:90]}")
        return False
    finally:
        # дескриптор УЖЕ унаследован ребёнком — свою копию закрываем сразу, иначе процесс-хук
        # держит лог открытым до выхода (в тестах это ResourceWarning, в бою — лишний хэндл)
        if sink is not subprocess.DEVNULL:
            try:
                sink.close()
            except Exception:
                pass


def _summary_from_transcript(path, limit=SUMMARY_MAX):
    """Краткая сводка сессии = ПОСЛЕДНИЙ текстовый ответ ассистента из живого transcript.

    Формат снят с ЖИВОГО файла ~/.claude/projects/<repo>/<session>.jsonl (фикстура
    fixtures/session_end_transcript.live.jsonl): построчный JSON, у ассистентских строк
    message.role='assistant' и message.content — СПИСОК блоков типов text | thinking |
    tool_use; работа субагентов помечена isSidechain=true. Сводка — только text главной
    ветки: thinking и tool_use сводкой не являются, сайдчейн — не итог сессии.
    → str ('' если текстового ответа нет / файл недоступен)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return ""
    for raw in reversed(lines):
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if not isinstance(obj, dict) or obj.get("isSidechain"):
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = " ".join(str(block.get("text") or "").split())
                if text:
                    return text[:limit] + ("…" if len(text) > limit else "")
    return ""


# Маска значений токенов/паролей в тексте, уходящем в Telegram — копия pretool_guard.
# _RE_SECRET_VALUE (гарда НЕ импортируем: notify обязан оставаться самодостаточным
# fire-and-forget без лишних зависимостей).
_RE_SECRET_VALUE = re.compile(
    r"(?i)([A-Za-z_]*(?:token|api_?key|secret|password|passwd|pwd)[A-Za-z_]*)(\s*[=:]\s*)(\S+)")
CMD_MAX = 220     # символов команды в пинге ожидания: карточка должна остаться читаемой


def _last_tool_command(path, limit=CMD_MAX):
    """Команда, ждущая разрешения = ПОСЛЕДНИЙ tool_use в transcript-е сессии (тот же живой
    формат, что у _summary_from_transcript). Сам Notification-хук команду НЕ передаёт (в его
    payload только «Claude needs your permission to use Bash») — достаём из transcript_path.
    Сайдчейн не отсеиваем: разрешения может ждать и инструмент субагента. Значения токенов/
    паролей маскируем, длину режем. → 'Bash: git status' | '' (нет tool_use / файл недоступен)."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return ""
    for raw in reversed(lines):
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                continue
            ti = block.get("input") or {}
            detail = " ".join(str(ti.get("command") or ti.get("file_path")
                                  or ti.get("notebook_path") or "").split())
            text = str(block.get("name") or "?") + ((": " + detail) if detail else "")
            text = _RE_SECRET_VALUE.sub(lambda m: m.group(1) + m.group(2) + "***", text)
            return text[:limit] + ("…" if len(text) > limit else "")
    return ""


# ------------------------- МЕТРИКИ СЕССИИ (26.07.2026) ---------------------------------------
# Обе полосы демонов пишут строку METRICS на задачу, а СЕССИЯ была единственным каналом без
# замеров: ни модели, ни уровня усилий, ни длительности. Оказалось, сессия знает о себе всё —
# надо лишь взять из двух разных мест:
#   уровень усилий — ПРЯМО В ОКРУЖЕНИИ: CLAUDE_EFFORT (хук — ребёнок сессии и видит её env);
#   модель         — в ТРАНСКРИПТЕ: message.model ассистентских строк (в окружении её НЕТ);
#   длительность   — по timestamp первой и последней строки транскрипта, с дробной частью;
#   токены         — usage ассистентских строк, ПОЛНЫЙ вход (input+cacheRead+cacheCreation),
#                    как того требует доктрина task_metrics: по одному input метрика соврала бы.
# Формат — общий task_metrics.metrics_line, чтобы `grep METRICS` работал по всем трём полосам.
# Пишем в лог ПК-полосы: у сессии своего журнала нет, а поля lane/mode/src делают строку
# однозначной — перепутать её с задачей демона нельзя.
SESSION_LANE = "session"          # третье значение полосы: не pc и не vps


def _iso_ts(v):
    """'2026-07-24T21:34:27.310Z' → datetime | None. Чужие форматы не угадываем."""
    import datetime
    t = str(v or "").strip()
    if not t:
        return None
    try:
        return datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except Exception:
        return None


def _session_facts(path):
    """Один проход по транскрипту → факты сессии. Ничего не печатает и не бросает.
    → dict(model, effort, entrypoint, first, last, tokens_in, tokens_out, prompt, turns)."""
    f = {"model": None, "effort": None, "entrypoint": None, "first": None, "last": None,
         "tokens_in": 0, "tokens_out": 0, "prompt": "", "turns": 0, "seen_usage": False}
    import json as _json
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except Exception:
        return f
    with fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = _json.loads(raw)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            ts = _iso_ts(d.get("timestamp"))
            if ts is not None:
                if f["first"] is None:
                    f["first"] = ts
                f["last"] = ts
            for k, key in (("effort", "effort"), ("entrypoint", "entrypoint")):
                if d.get(k):
                    f[key] = d[k]
            if d.get("type") == "last-prompt" and isinstance(d.get("lastPrompt"), str):
                f["prompt"] = d["lastPrompt"]
            msg = d.get("message")
            if isinstance(msg, dict) and msg.get("model"):
                f["model"] = msg["model"]
                f["turns"] += 1
                u = msg.get("usage")
                if isinstance(u, dict):
                    for k in ("input_tokens", "cache_read_input_tokens",
                              "cache_creation_input_tokens"):
                        if k in u:
                            f["seen_usage"] = True
                        try:
                            f["tokens_in"] += int(u.get(k) or 0)
                        except (TypeError, ValueError):
                            pass
                    try:
                        f["tokens_out"] += int(u.get("output_tokens") or 0)
                    except (TypeError, ValueError):
                        pass
    return f


def _session_metrics_line(path, outcome="done"):
    """Строка METRICS завершившейся сессии → str ('' если замерить нечего).
    Fail-safe целиком: сбой замера НЕ отменяет ни карточку владельцу, ни запись в журнал."""
    try:
        import task_metrics
        f = _session_facts(path)
        if f["first"] is None and not f["model"]:
            return ""                                  # транскрипта нет/пуст — замерять нечего
        dur = 0.0
        if f["first"] is not None and f["last"] is not None:
            dur = max(0.0, (f["last"] - f["first"]).total_seconds())
        sid = (os.getenv("CLAUDE_CODE_SESSION_ID") or "").strip()
        if not sid:
            sid = os.path.basename(str(path or "")).replace(".jsonl", "")
        effort = task_metrics.norm_effort(os.getenv("CLAUDE_EFFORT") or f["effort"])
        src = (os.getenv("CLAUDE_CODE_ENTRYPOINT") or f["entrypoint"] or "").strip() or None
        return task_metrics.metrics_line(
            task=(sid[:8] or "na"), lane=SESSION_LANE, model=f["model"],
            effort=effort,
            start_iso=(f["first"].isoformat(timespec="seconds") if f["first"] else None),
            end_iso=(f["last"].isoformat(timespec="seconds") if f["last"] else None),
            dur_s=dur, outcome=outcome, attempts=max(1, f["turns"]), selfheals=0,
            tokens_in=(f["tokens_in"] if f["seen_usage"] else None),
            tokens_out=(f["tokens_out"] if f["seen_usage"] else None),
            task_text=f["prompt"],
            mode=("test" if task_metrics.under_test(
                (sys.argv[0] if sys.argv else ""), os.environ, sys.modules) else "prod"),
            src=src)
    except Exception:
        return ""


def _write_session_metrics(line):
    """Дописать строку в журнал ПК-полосы (через log_setup — под тестом уедет в temp)."""
    if not line:
        return False
    try:
        try:
            import log_setup
            path = log_setup.log_path("pc_orchestrator.log")
        except Exception:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "pc_orchestrator.log")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s,000 INFO %s\n" % (stamp, line))
        return True
    except Exception:
        return False


def _read_stdin_json():
    # BOM/пробелы срезаем ЯВНО: живой stdin хука приходит чистым, но любой перенаправляющий
    # слой (PowerShell-пайп) ставит ﻿ впереди — strip() его НЕ убирает, и полезная
    # нагрузка молча превращалась в {} (сводка вырождалась в «без текстового итога»).
    # Кодировку задаём ЯВНО: по умолчанию Python берёт системную кодовую страницу (на этом ПК
    # cp1251), и payload хука с кириллицей приезжал мохибейком либо ронял чтение. Живой след
    # 29.07 10:47:31 — карточка не доставлена: Telegram вернул 400 «strings must be encoded in
    # UTF-8», и DM, и фолбэк в тему. Тот же класс, что 093119e и правка stdin в pretool_guard.
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        raw = (sys.stdin.read() or "").lstrip("﻿ \t\r\n")
        return json.loads(raw) if raw.startswith("{") else {}
    except Exception:
        return {}


def _build(kind, hook):
    if kind == "notification":
        # Пинг «жду разрешения»: текст события + КОМАНДА, которая ждёт (payload хука команду
        # не содержит — берём последний tool_use из transcript-а). Маршрут — тема 1160 (main).
        ctx = str(hook.get("message") or hook.get("notification") or "").strip()
        text = "🔔 Dispatch ждёт твоего разрешения/ввода" + (f": {ctx}" if ctx else ".")
        cmd = _last_tool_command(str(hook.get("transcript_path") or ""))
        if cmd:
            text += "\nКоманда: " + cmd
        return text
    if kind == "stop":
        # В ЛИЧКУ НЕ УХОДИТ (см. ветку `kind == "stop"` в main): текст оставлен для лога и тестов.
        return "✅ Dispatch: задача завершена."
    if kind == "session_end":
        # ЗАВЕРШЕНИЕ Code-сессии (в т.ч. Remote Control с телефона): карточка-итог владельцу.
        summary = _summary_from_transcript(hook.get("transcript_path") or "")
        if not summary:
            reason = str(hook.get("reason") or "").strip()
            summary = (f"без текстового итога (причина: {reason})" if reason
                       else "без текстового итога")
        return "✅ Code-сессия завершена: " + summary
    return None


def main():
    # stdout/stderr → UTF-8: ветки --topic печатают итог в stdout, а текст карточек/пингов несёт
    # эмодзи (🔔 📊 ⏹) и кириллицу. Пара к reconfigure(stdin) выше — тот же класс кодировки.
    io_utf8.force_utf8()
    args = list(sys.argv[1:])
    try:
        if args and args[0] == "--critical":
            # Инцидент контура. Флаг — ЗАЯВЛЕНИЕ отправителя по тому же признаку («сам не
            # рассосётся, ждёт владельца»), а не отдельный маршрут: адрес выбирает deliver.
            text = " ".join(args[1:]).strip() or "🔔 Оркестратор: критический инцидент"
            channel, ok = deliver(text, declared=True)
            _log.info(f"итог(критич): channel={channel} ok={ok} | {text[:90]}")
            sys.exit(0)
        if args and args[0] == "--topic":
            # ЯВНО НАЗВАННЫЙ АДРЕС — не маршрут: вызывающий сам знает тему и признак не
            # спрашивается (`--topic [id] <текст>`, по умолчанию 328 — тема постановки задач).
            # Печатаем итог в stdout: вызывающий (session_watch) обязан знать, ДОШЛО ли, иначе
            # он пометит сессию «уже сигналили» по несостоявшейся отправке и сигнал пропадёт.
            rest = args[1:]
            tid = None
            if rest and rest[0].lstrip("-").isdigit():
                tid, rest = int(rest[0]), rest[1:]
            text = " ".join(rest).strip() or "🔔 Dispatch"
            channel, ok = send_topic(text, tid)
            _log.info(f"итог(тема): channel={channel} ok={ok} | {text[:90]}")
            print(f"channel={channel} ok={int(bool(ok))}")
            sys.exit(0)
        if args and args[0] == "--card":
            # карточка управления цепью дирижёра: текст + кнопки [⏹ Стоп цепи][📊 Статус цепи].
            # Кнопка = место для ответа, значит инбокс (ЗАМОК-1 в deliver): раньше такая карточка
            # уходила в личку — то есть МИМО инбокса, хотя ответа ждала.
            pid = args[1] if len(args) > 1 else ""
            text = " ".join(args[2:]).strip() or f"🧩 Цепь #{pid}"
            channel, ok = deliver(text, _chain_markup(pid))
            _log.info(f"итог(карточка цепи {pid}): channel={channel} ok={ok} | {text[:90]}")
            sys.exit(0)
        if args and args[0] == "--hook":
            kind = args[1] if len(args) > 1 else ""
            # Диагностика бюджета размышления: хук — РЕБЁНОК сессии, значит видит её env. Строка
            # доказывает по ФАКТУ, что блок env из .claude/settings.json доехал до живой сессии
            # (иначе «настроено» проверялось бы только по файлу). Не секрет — печатать можно.
            _log.info(f"хук={kind} | MAX_THINKING_TOKENS={os.getenv('MAX_THINKING_TOKENS') or '-'}")
            payload = _read_stdin_json()
            text = _build(kind, payload) or f"🔔 Dispatch: {kind or 'событие'}"
            if kind == "session_end":
                # ДВА КАНАЛА финала (как у Dispatch): карточка владельцу И строка-итог в
                # cowork_log. Адрес карточки выбирает признак: «✅ Code-сессия завершена: …»
                # ответа не ждёт и едет в тему постановки задач, а не в инбокс (10.08.2026).
                channel, ok = deliver(text)
                _cowork(text)
                # ТРЕТЬЯ ПОЛОСА получает свой замер: до этого сессии не писали ни модели, ни
                # усилий, ни длительности. Строка идёт в лог ПК-полосы тем же форматом, что у
                # обоих демонов, и помечена lane=session — перепутать с задачей нельзя.
                _m = _session_metrics_line(str(payload.get("transcript_path") or ""),
                                           outcome=(str(payload.get("reason") or "").strip() or "done"))
                if _write_session_metrics(_m):
                    _log.info("METRICS сессии записан: %.160s", _m)
                else:
                    _log.info("METRICS сессии не записан (замерить нечего)")
                _log.info(f"итог(session_end): channel={channel} ok={ok} | {text[:90]}")
                sys.exit(0)
            if kind == "stop":
                # ── ЛИЧКА — ТОЛЬКО ТО, ЧЕГО НЕТ В ИНБОКСЕ И ЧТО ТРЕБУЕТ ОТВЕТА ──────────────
                # (правило владельца 05.08.2026). «✅ Dispatch: задача завершена.» ответа не
                # требует и информации не несёт: ни задачи, ни итога, ни хеша — один факт
                # «сессия кончилась». Тот же факт через СЕКУНДУ уходит хуком session_end, и там
                # он с настоящей сводкой. Замер за 7 суток (29.07–05.08, dispatch_notify.log):
                # 392 сообщения в личку, из них 178 — эта строка, то есть 45 % пульта тратилось
                # на дубль без содержания.
                # Событие не теряется: строка лога ниже остаётся, карточка session_end уходит
                # в тему постановки задач (10.08.2026: ответа она не ждёт, признак ведёт её
                # в 328), а `--hook stop` по-прежнему возвращает 0 (хук не считает это сбоем).
                _log.info("итог(stop): личка пропущена — итог сессии уходит в тему "
                          f"{TASKS_THREAD_ID} хуком session_end | {text[:90]}")
                sys.exit(0)
            if kind == "notification":
                # Сессия ЖДЁТ разрешения — признак читает это в самом тексте («ждёт твоего
                # разрешения/ввода») и ведёт в инбокс 1160, личка — фолбэк. Отдельной ветки
                # «этот вид всегда критический» больше нет: вид не адрес.
                channel, ok = deliver(text)
                _log.info(f"итог(notification): channel={channel} ok={ok} | {text[:90]}")
                sys.exit(0)
        elif args:
            text = " ".join(args).strip()
        elif not sys.stdin.isatty():
            try:                      # см. _read_stdin_json: кодировку stdin задаём явно
                sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            text = (sys.stdin.read() or "").strip() or "🔔 Dispatch"
        else:
            text = "🔔 Dispatch"
        channel, ok = deliver(text)
        _log.info(f"итог: channel={channel} ok={ok} | {text[:90]}")
    except Exception as e:
        # НИКОГДА не роняем вызывающий процесс
        try:
            _log.info(f"проглочена ошибка: {type(e).__name__}")
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
