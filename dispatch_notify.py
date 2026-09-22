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


# ═══════════ ЗАМОК 1: ПРОБА НЕ ПИШЕТ ВЛАДЕЛЬЦУ ВЖИВУЮ (05.09.2026) ═══════════
# ПОВОД, ЗАМЕРЕННЫЙ, А НЕ ПРИДУМАННЫЙ. В ночь на 05.09 владельцу за 19 секунд (05:14:37–05:14:56)
# упало 16 карточек ворот подряд — на коммиты `4528917`, `452891745`, `d860a9ad1` (июльские и
# августовские, к той ночи отношения не имевшие) и `new777`, которого в репозитории нет вовсе.
# Живые ворота в это время не отказывали НИ РАЗУ: в `pc_orchestrator.log` за 05:14 нет ни одной
# строки «ОСТАНОВЛЕНО», только вотчдог. То есть весь поток дала ПРОБА, ходившая боевым путём
# отправки. Настоящих поводов за сутки было три.
#
# ПОЧЕМУ ЗАМОК СТОИТ ИМЕННО ЗДЕСЬ, В `_api`. Это ЕДИНСТВЕННОЕ место, где модуль трогает сеть:
# через него идут send, send_critical, send_topic, send_topic_strict, edit_topic_strict — и любая
# дверь, которую здесь заведут завтра. Замок, поставленный в каждую дверь по отдельности, protect
# только те двери, которые кто-то вспомнил; поставленный в горлышко — все.
#
# ПРИЗНАК СТРУКТУРНЫЙ, А НЕ ФЛАГ. Флаг «это тест» проваливается ровно тем, что следующий заход
# забудет его выставить, и поток повторится. Поэтому мы не спрашиваем зовущего, кто он, а СМОТРИМ
# НА ТОЧКУ ВХОДА ПРОЦЕССА: боевые входы полосы — это файлы, лежащие В КОРНЕ репозитория
# (pc_orchestrator.py, pc_agent.py, dispatch_notify.py, pretool_guard.py, expectations_pc_run.py,
# deploy_voice.py, lesson_regress.py, rc_supervisor.py, review_audit_run.py — все до одного).
# Пробы и тесты живут ГДЕ УГОДНО ЕЩЁ: `_scratch_*/x.py`, `tmp/x.py`, `test_*.py`, `python -c`,
# REPL, чужое дерево. Автору пробы не надо помнить НИ О ЧЁМ — молчание получается само.
#
# FAIL-CLOSED: всё, чего не удалось разобрать (точки входа нет, путь не разобрался), считается
# ПРОБОЙ. Не смогли доказать, что вызов боевой — значит он не боевой.
REPO_ROOT = HERE

# Явно и намеренно разрешённая живая отправка ИЗ ПРОБЫ. Процесс-локальная переменная, а НЕ
# переменная окружения — и это выбор, а не мелочь: env наследуется детьми и переживает скрипт,
# поэтому один раз выставленный `LIVE=1` в оболочке разрешил бы отправку всем следующим пробам
# смены, то есть вернул бы ровно тот класс, от которого замок и заводится. Здесь разрешение
# умирает вместе с процессом, который его выдал.
_LIVE_FROM_PROBE = ""


def allow_live_from_probe(reason):
    """ЯВНОЕ НАМЕРЕННОЕ ДЕЙСТВИЕ, которым проба берёт на себя живую отправку владельцу.

    Причина обязана быть названа СЛОВАМИ и уходит в лог: разрешение, которого потом не найти в
    журнале, неотличимо от дефекта. Пустая причина — ValueError, а не «разрешено молча»."""
    global _LIVE_FROM_PROBE
    r = str(reason or "").strip()
    if not r:
        raise ValueError("allow_live_from_probe: причина обязана быть названа словами — "
                         "живая отправка владельцу без названной причины не разрешается")
    _LIVE_FROM_PROBE = r
    _log.info(f"ЖИВАЯ ОТПРАВКА ИЗ ПРОБЫ РАЗРЕШЕНА ЯВНО: {r}")
    return r


def _entry_point():
    """Абсолютный путь файла, С КОТОРОГО ЗАПУЩЕН процесс. Пустая строка = входа нет."""
    p = getattr(sys.modules.get("__main__"), "__file__", None) or (sys.argv[0] if sys.argv else "")
    try:
        return os.path.abspath(p) if p else ""
    except Exception:
        return ""


def probe_verdict(entry=None, env=None):
    """ЧИСТАЯ функция «это проба?» → (is_probe: bool, why: str).

    Всё, о чём судит, принимает доводами — поэтому проверяется тестом без сети и без подстроенного
    процесса. `entry` — путь точки входа, `env` — окружение."""
    env = os.environ if env is None else env
    entry = _entry_point() if entry is None else entry
    if env.get("PYTEST_CURRENT_TEST"):
        return True, "идёт тест (PYTEST_CURRENT_TEST)"
    if not entry:
        return True, "точка входа не названа (python -c / REPL / встроенный интерпретатор)"
    base = os.path.basename(entry)
    if base.startswith("test_") or base.endswith("_test.py"):
        return True, f"точка входа — тест «{base}»"
    try:
        d = os.path.dirname(os.path.abspath(entry))
        root = os.path.normcase(os.path.abspath(REPO_ROOT))
        same = os.path.normcase(d) == root
    except Exception:
        return True, f"путь точки входа «{entry}» не разобрался"
    if not same:
        return True, f"точка входа «{base}» лежит НЕ в корне репозитория ({d})"
    return False, f"боевая точка входа «{base}» в корне репозитория"


def live_send_verdict():
    """Пускать ли этот вызов в сеть → (ok: bool, why: str). Замок 1 целиком."""
    is_probe, why = probe_verdict()
    if not is_probe:
        return True, why
    if _LIVE_FROM_PROBE:
        return True, f"проба ({why}), но живая отправка разрешена явно: {_LIVE_FROM_PROBE}"
    return False, why


# ─── ИДЕНТИФИКАТОР ПОСЛЕДНЕЙ ОТПРАВКИ — для АРХИВА, а не для маршрута (05.09.2026) ───────────
# Каскады send/send_critical/send_topic возвращают (channel, ok) и НОМЕРА сообщения не отдают:
# зовущему он был не нужен, пока лог был обрезком в 90 символов. Архиву он нужен ровно затем,
# чтобы наша исходящая строка была СВЕРЯЕМА с самим Telegram: без message_id архивная строка —
# это наше слово о том, что мы отправили, а с ним — адрес, по которому это проверяется.
#
# Почему снимок, а не возврат из каждой функции: `_api` — ЕДИНСТВЕННОЕ горлышко всех отправок
# (пять дверей выше зовут его и никто не ходит в сеть мимо), поэтому один снимок здесь покрывает
# все каскады и все фолбэки разом, не трогая ни одной сигнатуры и ни одного вызывающего.
# Процесс живёт одну доставку (хук/CLI — one-shot), поэтому «последний» здесь означает «наш».
_LAST_SEND = {"mid": None, "chat": None, "thread": None}


def last_send_id():
    """message_id последнего УСПЕШНОГО sendMessage/editMessageText → str.

    '-' означает «номера нет»: отправка не состоялась, ушла из пробы (замок 1) или Telegram
    ответил без `result.message_id`. Это НЕ то же самое, что отсутствие поля `mid=` в строке
    лога: отсутствие поля означает строку, написанную кодом СТАРШЕ 05.09.2026, то есть заведомо
    обрезанную по 90 символов. Разница нужна разбору архива (`chatlog_ingest.py --source
    dispatch`), который иначе не отличил бы «текст целый, номера нет» от «текст обрезан»."""
    mid = _LAST_SEND.get("mid")
    return str(mid) if mid else "-"


def _remember_send(method, body):
    """Запомнить номер только что отправленного сообщения. Никогда не бросает и ничего не меняет
    в судьбе отправки: снимок вторичен, и сбой разбора ответа не смеет отменить доставку."""
    try:
        if method not in ("sendMessage", "editMessageText"):
            return
        if not (isinstance(body, dict) and body.get("ok")):
            return
        res = body.get("result")
        if not isinstance(res, dict) or not res.get("message_id"):
            return
        _LAST_SEND["mid"] = res.get("message_id")
        _LAST_SEND["chat"] = (res.get("chat") or {}).get("id")
        _LAST_SEND["thread"] = res.get("message_thread_id")
    except Exception:
        pass


def _api(method, payload):
    """POST в Bot API. Возвращает (ok, body). Токен/URL НЕ логируем.

    ЗАМОК 1 (см. выше) стои́т ПЕРВОЙ строкой: из пробы наружу по умолчанию не уходит ничего."""
    live, why = live_send_verdict()
    if not live:
        _log.info(f"ЖИВАЯ ОТПРАВКА НЕ СДЕЛАНА — это проба: {method} — {why}")
        return False, {"ok": False, "description": "проба: живая отправка владельцу запрещена — " + why}
    url = "https://api.telegram.org/bot" + TOKEN + "/" + method
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            body = json.loads(r.read().decode("utf-8"))
            _remember_send(method, body)
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


def _gate_markup(commit):
    """Инлайн-клавиатура ворот клиентского контура: [✅ Выкатить][⛔ Не выкатывай]. callback_data
    «gate:yes:<коммит>» / «gate:no:<коммит>» слушает pc_agent (owner-gate: только владелец).

    ЗАЧЕМ ОНА ВООБЩЕ (05.09.2026). Карточка ворот уезжает в тему-инбокс (deliver → send_critical),
    а СЛОВО ответа читается на полосе ПК только из ТЕКСТА ЗАДАЧИ ОЧЕРЕДИ — то есть НЕ там, где
    карточка показана. Кнопка закрывает ровно этот разрыв: она есть в том же сообщении, ловится
    тем же токеном (AGENT_BOT_TOKEN), и её нажатие ПОДСТАВЛЯЕТ то же самое слово в тот же самый
    разбор рычага. Новых слов не заводит, ворот мимо оснований не открывает.

    Коммит в callback_data — КОРОТКИЙ (Telegram даёт 64 байта на всё поле): «gate:no:» + 40 hex
    ещё влезает, но короткий оставляет запас и совпадает с тем, что владелец видит в карточке."""
    c = str(commit or "")[:40] or "head"
    return {"inline_keyboard": [[
        {"text": "✅ Выкатить", "callback_data": f"gate:yes:{c}"},
        {"text": "⛔ Не выкатывай", "callback_data": f"gate:no:{c}"},
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


def send_topic_strict(text, thread_id):
    """Сообщение РОВНО в названную тему, БЕЗ каскада фолбэков. → (channel, ok, detail).

    ``detail`` — message_id при успехе и ПРИЧИНА отказа при неудаче: у отправки, которой некуда
    отступать, обе новости одинаково нужны зовущему — первой он доказывает, что ушло, второй
    отчитывается, почему нет.

    Зачем отдельная дверь рядом с send_topic. У того каскад «тема → инбокс 1160 → личка», и он
    там прав: сигнал о жизни контура не должен утонуть из-за одной недоступной темы. Но есть
    сообщения, которым чужой адрес ХУЖЕ молчания, и первое такое — показ находок внешнего
    ревьюера в тему Аудит (ступень D ревью-контура): текст пришёл ИЗВНЕ, и уехать он смеет
    только туда, куда владелец его позвал. Инбокс 1160 — тема ОТВЕТА владельца, тема 328 —
    постановка задач; чужое мнение, севшее в любую из них, читается как задание. Поэтому здесь
    фолбэков нет ни одного, а отказ ВОЗВРАЩАЕТСЯ ПРИЧИНОЙ, чтобы зовущий мог о нём отчитаться.

    Тему НАЗЫВАЕТ зовущий и обязан назвать: подстановки «по умолчанию» здесь нет специально —
    дефолтный номер темы означал бы, что ненастроенный контур молча пишет куда-то ещё.
    """
    tid = int(thread_id or 0)
    if not tid:
        _log.info("send_topic_strict: тема не названа — не отправляем ничего")
        return ("none", False, "тема не названа (0)")
    if not TOKEN:
        _log.info("нет AGENT_BOT_TOKEN — сообщение в тему пропущено")
        return ("none", False, "нет AGENT_BOT_TOKEN")
    ok, resp = _api("sendMessage", {"chat_id": HQ_CHAT_ID, "message_thread_id": tid, "text": text})
    if ok:
        mid = ((resp or {}).get("result") or {}).get("message_id")
        _log.info(f"строго в тему {tid} ok → {HQ_CHAT_ID}/{tid} (message_id={mid})")
        return (f"topic:{tid}", True, str(mid or ""))
    why = f"code={resp.get('error_code')} {str(resp.get('description',''))[:120]}"
    _log.info(f"строго в тему {tid} НЕ прошло ({why}) — фолбэка нет по построению")
    return (f"topic:{tid}", False, why)


def send_chat_strict(text, chat_id, reply_markup=None):
    """Сообщение В НАЗВАННЫЙ ЧАТ (не в HQ-форум), БЕЗ каскада фолбэков. → (channel, ok, detail).

    Зачем дверь рядом с send_topic_strict (11.09.2026, показ кейса экзамена в тренажёрной группе).
    Все пять прежних дверей ходят в ОДИН чат — HQ_CHAT_ID либо личку владельца; чата как параметра
    у них нет вовсе. Показ экзаменационного кейса живёт в ДРУГОМ чате (группа-тренажёр), и до
    сегодня отправить туда кнопку было нечем ничем, кроме `moderation_bot`, — а он живой процесс
    и трогать его нельзя. Дверь закрывает ровно этот разрыв и НЕ заводит второго бота: токен тот
    же (AGENT_BOT_TOKEN), горлышко то же (`_api`, замок пробы на нём), кнопки те же `reply_markup`.

    ЧАТ НАЗЫВАЕТ ЗОВУЩИЙ И ОБЯЗАН НАЗВАТЬ. Умолчания здесь нет специально: подставленный адрес
    означал бы, что дверь с произвольным чатом молча пишет владельцу в личку, — то есть ровно тот
    класс, от которого доктрина адреса (`deliver` → `awaits_reply`) и защищает. По той же причине
    фолбэков нет ни одного: сообщение, адресованное конкретной группе, в инбоксе владельца хуже
    молчания — он прочитает его как обращённое к нему.

    ЭТА ДВЕРЬ НЕ ОТМЕНЯЕТ ДОКТРИНУ АДРЕСА, а стои́т вне её: доктрина решает, в какую ТЕМУ
    ВЛАДЕЛЬЦА класть сообщение, ждущее ответа. Здесь адресат — не владелец, а чат, названный
    вызывающим; `deliver` этой дверью не пользуется и пользоваться не должен.
    """
    cid = str(chat_id or "").strip()
    if not cid or cid in ("0", "-0"):
        _log.info("send_chat_strict: чат не назван — не отправляем ничего")
        return ("none", False, "чат не назван")
    if not TOKEN:
        _log.info("нет AGENT_BOT_TOKEN — сообщение в чат пропущено")
        return ("none", False, "нет AGENT_BOT_TOKEN")
    payload = {"chat_id": cid, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    ok, resp = _api("sendMessage", payload)
    if ok:
        mid = ((resp or {}).get("result") or {}).get("message_id")
        _log.info(f"строго в чат {cid} ok (message_id={mid})")
        return (f"chat:{cid}", True, str(mid or ""))
    why = f"code={resp.get('error_code')} {str(resp.get('description',''))[:120]}"
    _log.info(f"строго в чат {cid} НЕ прошло ({why}) — фолбэка нет по построению")
    return (f"chat:{cid}", False, why)


def chat_reachable(chat_id):
    """Виден ли НАШЕМУ боту названный чат → (ok, строка словами). НИЧЕГО НЕ ОТПРАВЛЯЕТ.

    `getChat` — чтение: в чат не уходит ни одно сообщение, участники ничего не видят. Дверь нужна
    затем, что «мы умеем слать» и «нас туда пустили» — РАЗНЫЕ новости, и вторую чтением кода не
    узнать: бот, не добавленный в группу, получает 403 на первой же боевой отправке, то есть в
    самый неудобный момент. Здесь это выясняется заранее и без следа.

    Возвращает ПРИЧИНУ дословно (обрезанную), а не пересказ: «чат не найден», «бот выкинут» и
    «нет прав» чинятся по-разному, и склеивать их в одно «недоступен» значило бы спрятать разницу.
    """
    cid = str(chat_id or "").strip()
    if not cid:
        return False, "чат не назван"
    if not TOKEN:
        return False, "нет AGENT_BOT_TOKEN"
    ok, resp = _api("getChat", {"chat_id": cid})
    if ok:
        res = (resp or {}).get("result") or {}
        return True, "чат виден: «%s» (%s)" % (res.get("title") or res.get("id") or cid,
                                               res.get("type") or "?")
    return False, "code=%s %s" % (resp.get("error_code"), str(resp.get("description", ""))[:120])


# ── ЧТЕНИЕ НАЗАД СВОЕГО СООБЩЕНИЯ (заведено 19.09.2026, задание 97) ─────────────────────────
# ЗАЧЕМ ВООБЩЕ. Отправив карточку, мы знаем ровно одно: Telegram ответил номером. Это СЛОВО
# ОТПРАВИТЕЛЯ о том, что он отправил, а не доказательство, что сообщение лежит в чате минуту
# спустя. Замер 18.09.2026 (задание 67a) кончился честным «доставка НЕИЗВЕСТНА»: `getUpdates` своих
# сообщений боту не отдаёт, чужой бот сообщений бота не видит вовсе, а `getMessage` у Bot API НЕТ.
#
# ЧЕМ ЧИТАЕМ. Единственное чтение, доступное боту про СВОЁ сообщение, — ХОЛОСТАЯ правка разметки
# ТОЙ ЖЕ разметкой: Telegram сверяет новое содержимое со старым и на совпадении ОТКАЗЫВАЕТ словами
# «message is not modified». Этот отказ и есть чтение назад: он говорит разом, что сообщение с этим
# номером в этом чате ЕСТЬ, что оно НАШЕ (чужое бот править не может) и что кнопки на нём ТЕ ЖЕ.
# Наружу при этом не уходит ни одного нового сообщения и не меняется ни один байт видимого.
#
# МИНА, КОТОРАЯ ЗДЕСЬ ЖИВЁТ И ЗАКРЫТА ОТКАЗОМ: `editMessageReplyMarkup` БЕЗ поля `reply_markup`
# СНИМАЕТ кнопки с сообщения. То есть «прочитать» вызовом без разметки значило бы оставить владельца
# с карточкой без единой кнопки — тихо и необратимо. Поэтому разметка здесь ОБЯЗАТЕЛЬНА, а её
# отсутствие — отказ до сети, а не умолчание.
SEEN_NOT_MODIFIED = "not modified"
SEEN_GONE = ("message to edit not found", "message_id_invalid", "message to delete not found")


def present_verdict(ok, body):
    """Ответ Telegram на холостую правку разметки → (исход, словами). ЧИСТАЯ функция, сети нет.

    исход: True — сообщение на месте; False — его там нет; None — судить нечем (НЕИЗВЕСТНО).

    ТРЕТИЙ ИСХОД ОБЯЗАТЕЛЕН и не сводится к False: «чат не найден», «бота выкинули» и замок пробы
    говорят о НАШЕМ доступе, а не о судьбе сообщения, и назвать их «сообщения нет» значило бы
    выдать незнание за факт ровно там, где владелец ждёт доказательства."""
    body = body if isinstance(body, dict) else {}
    desc = str(body.get("description") or "")
    low = desc.lower()
    if ok:
        return True, ("сообщение НА МЕСТЕ: Telegram принял холостую правку и вернул его "
                      "(правка холостая — разметка та же, что уехала с карточкой)")
    if SEEN_NOT_MODIFIED in low:
        return True, ("сообщение НА МЕСТЕ и кнопки на нём ТЕ ЖЕ: Telegram отказал холостой правке "
                      "своими словами — «%s»" % desc[:160])
    if any(mark in low for mark in SEEN_GONE):
        return False, "сообщения с таким номером в этом чате НЕТ: «%s»" % desc[:160]
    return None, "судить нечем: code=%s «%s»" % (body.get("error_code"), desc[:160])


def message_present(chat_id, message_id, reply_markup=None, api=None):
    """ЛЕЖИТ ЛИ НАШЕ СООБЩЕНИЕ В ЧАТЕ → (исход, словами). НИ ОДНОГО НОВОГО СООБЩЕНИЯ НЕ ШЛЁТ.

    Разметку называет ЗОВУЩИЙ и обязан назвать ту самую, что уехала с сообщением: правка другой
    разметкой была бы уже не чтением, а подменой кнопок у владельца на глазах (узел выше)."""
    cid = str(chat_id or "").strip()
    try:
        mid = int(str(message_id).strip())
    except (TypeError, ValueError):
        return None, "номер сообщения не назван числом (%r) — читать нечего" % (message_id,)
    if not cid or cid in ("0", "-0"):
        return None, "чат не назван — читать нечего"
    if reply_markup is None:
        return None, ("разметка не названа: правка БЕЗ неё СНЯЛА БЫ кнопки с карточки владельца — "
                      "читать назад так нельзя, ничего не сделано")
    if not TOKEN:
        return None, "нет AGENT_BOT_TOKEN — читать нечем"
    ok, body = (api or _api)("editMessageReplyMarkup",
                             {"chat_id": cid, "message_id": mid, "reply_markup": reply_markup})
    seen, why = present_verdict(ok, body)
    _log.info("чтение назад %s/%s: %s — %s" % (
        cid, mid, {True: "ЛЕЖИТ", False: "НЕТ", None: "НЕИЗВЕСТНО"}[seen], _scrub_token(why)))
    return seen, why


def _scrub_token(text):
    """Вычистить ЗНАЧЕНИЕ токена из любой строки, уходящей наружу (stdout, лог, отчёт).

    Telegram своего токена в описании ошибки сегодня не повторяет — но «сегодня не повторяет»
    гарантией не является, а цена промаха односторонняя: секрет, один раз попавший в лог или в
    отчёт владельца, оттуда уже не вынимается. Поэтому чистка стои́т НА ВЫХОДЕ двери, а не на
    доверии к чужому формату ответа."""
    s = text if isinstance(text, str) else str(text)
    tok = TOKEN if isinstance(TOKEN, str) else ""
    return s.replace(tok, "<токен скрыт>") if tok else s


def _identity_line(result):
    """Результат `getMe` → одна строка словами: «@имя «видимое имя» id=<число>». ЧИСТАЯ функция.

    Отделена от двери затем, что сверяется БЕЗ СЕТИ: форма ответа Telegram — это голден, а не
    догадка. Поля в нём необязательные (у бота может не быть `username`), и пустое поле называется
    СЛОВАМИ, а не исчезает из строки: «имени нет» и «имя не прочитано» чинятся по-разному, а
    исчезнувшее поле читается как первое при любом из двух."""
    res = result if isinstance(result, dict) else {}
    uname = res.get("username")
    title = res.get("first_name")
    bid = res.get("id")
    return " ".join((
        ("@" + uname.strip()) if isinstance(uname, str) and uname.strip() else "@-имени НЕТ",
        ("«%s»" % title.strip()) if isinstance(title, str) and title.strip() else "«видимого имени НЕТ»",
        ("id=%s" % bid) if isinstance(bid, int) and not isinstance(bid, bool) else "id НЕ ПРОЧИТАН",
    ))


def bot_identity():
    """КТО МЫ ДЛЯ TELEGRAM → (ok, строка словами). НИЧЕГО НЕ ОТПРАВЛЯЕТ.

    `getMe` — вызов САМОМУ СЕБЕ: наружу не уходит ни одного сообщения, ни один чат и ни один
    участник следа не видит. Дверь заведена 12.09.2026 затем, что публичное имя бота ЧТЕНИЕМ КОДА
    не узнаётся вовсе: в репозитории лежит только имя ПЕРЕМЕННОЙ с токеном (`AGENT_BOT_TOKEN`), а
    @-имя и числовой идентификатор живут на стороне Telegram и меняются владельцем бота без единой
    правки здесь. Догадка по имени переменной именем НЕ является, а владельцу имя нужно ровно
    затем, чтобы добавить бота в группу, — и промах здесь стоит добавления ЧУЖОГО бота.

    Токен не печатается ни в успехе, ни в отказе: всё, что уходит наружу, проходит `_scrub_token`.
    Токена нет → это ТРЕТИЙ ИСХОД («отсюда не видно»), а не «бота нет»."""
    if not TOKEN:
        return False, "нет AGENT_BOT_TOKEN — кто мы, отсюда не видно"
    ok, resp = _api("getMe", {})
    if ok:
        body = resp if isinstance(resp, dict) else {}
        return True, _scrub_token(_identity_line(body.get("result")))
    r = resp if isinstance(resp, dict) else {}
    code, desc = r.get("error_code"), r.get("description")
    return False, _scrub_token("code=%s %s" % (
        code if isinstance(code, int) else "НЕ ПРОЧИТАН",
        desc[:120] if isinstance(desc, str) else "описания нет"))


def edit_topic_strict(text, thread_id, message_id):
    """ПРАВКА уже стоящего сообщения темы, БЕЗ каскада и БЕЗ отправки нового. → (channel, ok, detail).

    Зачем дверь рядом с send_topic_strict (02.09.2026, живая витрина состояния). Весь показ полосы
    до сих пор УМЕЛ ТОЛЬКО СЛАТЬ: и находки ревьюера, и сводка контура кладут в тему НОВОЕ
    сообщение каждый раз, и это правильно для события — у события есть история. Но у СОСТОЯНИЯ
    истории нет: шесть сводок в сутки превращают «что сейчас» в ленту, по которой владелец
    листает назад. Витрина живёт ОДНИМ сообщением, и правит его вот этим вызовом.

    ЧЕГО ЭТА ДВЕРЬ НЕ ДЕЛАЕТ НАМЕРЕННО: она НЕ отправляет нового сообщения ни одной веткой —
    даже когда правка не удалась. Решение «старого больше нет, можно завести новое» принимает
    ЗОВУЩИЙ по причине отказа (`vitrina_pc.edit_lost`), и проверяется оно тестом без сети. Слей
    обе роли в одну функцию — и первый же промах сети родил бы в теме второе сообщение, то есть
    ровно ту ленту, ради ухода от которой правка и заведена.

    ``detail`` при отказе несёт ОПИСАНИЕ Telegram дословно (обрезанное), а не пересказ: по нему
    зовущий отличает «сообщения нет» от «сеть молчит», и пересказ здесь стоил бы этой разницы.
    Ответ «message is not modified» — тоже отказ по коду, но для витрины это успех (в теме стои́т
    ровно то, что мы хотели), и судит об этом опять же зовущий.
    """
    tid = int(thread_id or 0)
    mid = int(message_id or 0)
    if not tid:
        _log.info("edit_topic_strict: тема не названа — не правим ничего")
        return ("none", False, "тема не названа (0)")
    if not mid:
        _log.info("edit_topic_strict: message_id не назван — не правим ничего")
        return ("none", False, "message_id не назван (0)")
    if not TOKEN:
        _log.info("нет AGENT_BOT_TOKEN — правка сообщения пропущена")
        return ("none", False, "нет AGENT_BOT_TOKEN")
    # message_thread_id в editMessageText не передаётся: сообщение уже лежит в своей теме, и
    # адресуется оно парой (chat_id, message_id). Лишнее поле здесь — не «надёжнее», а лишний
    # способ разойтись с тем, куда сообщение легло на самом деле.
    ok, resp = _api("editMessageText", {"chat_id": HQ_CHAT_ID, "message_id": mid, "text": text})
    if ok:
        _log.info(f"правка в теме {tid} ok → {HQ_CHAT_ID}/{tid} (message_id={mid})")
        return (f"topic:{tid}", True, str(mid))
    why = f"code={resp.get('error_code')} {str(resp.get('description',''))[:160]}"
    _log.info(f"правка в теме {tid} НЕ прошла ({why}) — нового сообщения отсюда не шлём")
    return (f"topic:{tid}", False, why)


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


def _cowork(line, spawner=None, env=None):
    """ВТОРОЙ канал ритуала: строка-итог в cowork_log (мозг) через cowork_log_append.py.

    ОТДЕЛЁННЫЙ (detached) спавн, НЕ ждём: живой замер — карточка в Telegram уходит за ~1 с, а
    запись в cowork_log это ДВА round-trip к Bridge на VPS (~8 с). Синхронное ожидание держало
    SessionEnd-хук ~9.4 с, и Claude Code гасил его на выходе сессии («SessionEnd hook … failed:
    Hook cancelled») — оба канала терялись. Теперь хук отдаёт управление сразу, а запись в мозг
    доживает сама (DETACHED_PROCESS переживает смерть сессии-родителя).
    Вывод ребёнка льём в ОТДЕЛЬНЫЙ cowork_hook.log: два процесса, дописывающие один файл, рвут
    друг другу строки (живой прогон: строка лога осталась обрезком «…ена (pid=2416)»).
    → bool: True = процесс записи запущен (не «запись подтверждена»)."""
    # ТЕСТ-ПРОГОН В ЖУРНАЛ ВЛАДЕЛЬЦА НЕ ПИШЕТ (класс 19.08.2026). Замер живого журнала за сутки:
    # из 26 строк «дети контура» 21 порождена ПРОГОНОМ ТЕСТОВ, 10 из них слепые («модербот —
    # неизвестно (изоляция тестов)»). Строка уходит владельцу ОТ ИМЕНИ прибора, а прибор в тот же
    # час зряч — отставание 3.4 с. Автор не наблюдатель: `expectations_pc_run.run()` без `pulser=`
    # берёт боевой `send_pulse` и приходит сюда; тест-класс, писавшийся до появления пульсовой
    # ветки, о ней не знает и знать не обязан.
    # ПОЧЕМУ ПРАВИЛО ЗДЕСЬ, А НЕ В ТЕСТЕ: изоляция «каждый тест-класс глушит канал сам» уже
    # однажды не доехала до класса-нарушителя (05.08.2026 — полный гейт уничтожил боевой спул
    # ревизора, 12 недоставленных находок). Дискриминатор берём тот, что уже боевой у
    # log_path/state_path: он спрашивает СПОСОБ ЗАПУСКА ПРОЦЕССА, поэтому ловит и полный гейт
    # (`TESTING` от соседа по прогону), и одиночный `python test_x.py`, где `TESTING` нет вовсе —
    # а это 11 строк из 21, и они опаснее слепых: сказаны фикстурными часами, но звучат как правда.
    # `env={}` — ЯВНОЕ заявление вызывающего «полоса живая»; им и только им тест доходит до спавна.
    # ОТКАЗ ГЛУХОЙ, БЕЗ СПУЛА: отложенная строка доехала бы до журнала следующим живым вызовом —
    # то есть протекла бы позже и уже без опознавательного отпечатка происхождения.
    # СБОЙ САМОГО ДИСКРИМИНАТОРА СУДИМ В ПОЛЬЗУ ЗАПИСИ: молчащий пульс — это ложная смерть полосы
    # для серверного О4 (тишина адресована НАРУЖУ), а лишняя строка видна глазами и стоит строку.
    try:
        import log_setup
        muted = log_setup.is_test_context(env)
    except Exception:                                    # fail-open, см. абзац выше
        muted = False
    if muted:
        _log.info(f"cowork_log: ТЕСТ-ПРОГОН — строка в журнал НЕ пойдёт | {line[:90]}")
        return False
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

# ─── АРХИВНАЯ СТРОКА ИТОГА (05.09.2026, первая очередь хранилища переписки) ───────────────────
# До этого дня итог доставки писался в лог как `text[:90]` — в ВОСЬМИ одинаковых местах ниже.
# Замер переписи 05.09: наша исходящая доля форума Штаба ≈50 сообщений в сутки при типовом
# размере ~700 Б, то есть обрезка выбрасывала СЕМЬ ВОСЬМЫХ каждой собственной карточки —
# единственной половины архива, которая у полосы уже есть и не стоит ни одного запроса в
# Telegram. Снятие обрезки отдаёт эту половину даром: 35 КБ/сут, 12 МБ в год.
#
# ЧТО ЗДЕСЬ ДЕЛАЕТСЯ ПОМИМО СНЯТИЯ ОБРЕЗКИ, и почему это не украшение:
#   1) перевод строки заменяется на « ⏎ » — тем же знаком, каким его давно заменяет
#      `userbot_listen`. Инвариант «одна строка лога = одно сообщение» до сих пор держался
#      СЛУЧАЙНО (в 90 символов перенос попадал редко); на полном тексте карточка с переносами
#      развалила бы разбор архива на первой же многострочной сводке;
#   2) значения токенов/паролей маскируются тем же `_RE_SECRET_VALUE`, что и команда в пинге.
#      Обрезка в 90 символов была СЛУЧАЙНЫМ замком от утечки секрета в лог — снимая её, замок
#      надо поставить осознанный, иначе первая же карточка с `TOKEN=…` в теле легла бы в файл
#      целиком.
# Потолка длины здесь НЕТ намеренно: потолок — это та же обрезка под другим именем.
_ARCH_NL = " ⏎ "


def arch_text(text):
    """Текст карточки для архивной строки лога: ЦЕЛИКОМ, в одну строку, с маской секретов."""
    s = _RE_SECRET_VALUE.sub(lambda m: m.group(1) + m.group(2) + "***", str(text or ""))
    return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", _ARCH_NL)


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


# ─── СЧЁТ ПУШЕЙ БЕЗ КНОПКИ (22.09.2026, задание Штаба 0015i-71c.2209) ──────────────────────────
# Три рода сообщений без кнопки уходили владельцу мимо реестра карточек (замер 71a: с 05.09 —
# 525 `🔴`, 59 `⛔`, 169 `🔔`). Строку в реестр пишет ЭТОТ процесс и ТОЛЬКО после доставки: номер
# сообщения есть лишь тогда, когда Telegram его вернул, и по нему строка сверяется с журналом
# доставки (`card_ledger_pc.reconcile`). Признак операции сюда приходит НЕ из текста: для
# `🔴`/`⛔` — из ключа `--guard-push`, которым зовёт ТОЛЬКО гард в момент перехвата
# (`pretool_guard._push`); для `🔔` признака в месте события нет, и строка его не несёт.
# Боевой путь и проверка различаются в коде: без `path` строка ложится в боевой реестр ТОЛЬКО
# при боевой точке входа (замок 1, `live_send_verdict`); проверка пишет в названный ей путь.
# Импорт под подавлением: сломанный счёт не смеет уронить доставку.
GUARD_PUSH_FLAG = "--guard-push"   # та же строка, что card_ledger_pc.GUARD_PUSH_FLAG (сверяет тест)
LEDGER_FILE = os.path.join(HERE, "pc_orchestrator.cards_ledger.jsonl")
try:
    import card_ledger_pc
except Exception:                     # noqa: BLE001
    card_ledger_pc = None


def _ledger_push(branch, rod_key, cls, channel, ok, path=None):
    """Строка «пуш ушёл» в реестр → True (легла) | False (не пишем / не легла). Никогда не бросает.

    branch — КТО зовёт (ветка main): "guard" = `--guard-push`, "wait" = `--hook notification`;
    rod_key — от гарда: "red" (🔴) | "top" (⛔ высшая цена). Источник и признак выводит
    `card_ledger_pc.push_row` из branch, а не из текста сообщения."""
    try:
        cl = card_ledger_pc
        if cl is None or cl.off() or not ok:
            return False
        mid = last_send_id()
        if mid == "-":
            return False                  # номера нет — сообщения нет, строки тоже
        if path is None:
            if not live_send_verdict()[0]:
                return False              # проба/тест: в боевой реестр не пишем НИКОГДА
            path = LEDGER_FILE
        if branch == "guard":
            src = cl.SRC_GUARD_PUSH
            rod = {"red": cl.ROD_GUARD, "top": cl.ROD_GUARD_TOP}.get(rod_key, "неизвестный")
        elif branch == "wait":
            src, rod = cl.SRC_WAIT_HOOK, cl.ROD_WAIT
        else:
            return False
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        c = "" if cls in (None, "", "-") else cls
        return cl.append(path, cl.push_row(rod, src, c, channel, mid, now))
    except Exception:                     # noqa: BLE001
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
            _log.info(f"итог(критич): channel={channel} ok={ok} mid={last_send_id()} "
                      f"| {arch_text(text)}")
            sys.exit(0)
        if args and args[0] == "--whoami":
            # КТО МЫ ДЛЯ TELEGRAM. Вызов самому себе (getMe): наружу не уходит ни одного
            # сообщения. Ветка стои́т ЗДЕСЬ, а не в отдельном скрипте, по двум причинам: замок 1
            # пускает в сеть только боевые точки входа в корне репозитория, а токен живёт ровно в
            # этом модуле — второй читатель `.env` был бы вторым местом, где секрет может утечь.
            ok, why = bot_identity()
            print(("✅ бот отправки агента: " if ok else "⛔ имя бота не прочитано: ") + why)
            _log.info(f"итог(--whoami): ok={int(bool(ok))} | {why}")
            sys.exit(0 if ok else 1)
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
            _log.info(f"итог(тема): channel={channel} ok={ok} mid={last_send_id()} "
                      f"| {arch_text(text)}")
            print(f"channel={channel} ok={int(bool(ok))}")
            sys.exit(0)
        if args and args[0] == GUARD_PUSH_FLAG:
            # ПУШ ГАРДА (роды 🔴/⛔): `--guard-push <red|top> <класс> <текст>`. Зовёт ТОЛЬКО
            # `pretool_guard._push` в момент перехвата настоящего вызова — поэтому строка реестра
            # несёт признак операции. Адрес по-прежнему выбирает deliver (ничего не меняется).
            rod_key = args[1] if len(args) > 1 else ""
            cls = args[2] if len(args) > 2 else ""
            text = " ".join(args[3:]).strip() or "🔴 Гард: карточка"
            channel, ok = deliver(text)
            _log.info(f"итог(гард-пуш): channel={channel} ok={ok} mid={last_send_id()} "
                      f"| {arch_text(text)}")
            _ledger_push("guard", rod_key, cls, channel, ok)
            sys.exit(0)
        if args and args[0] == "--card":
            # карточка управления цепью дирижёра: текст + кнопки [⏹ Стоп цепи][📊 Статус цепи].
            # Кнопка = место для ответа, значит инбокс (ЗАМОК-1 в deliver): раньше такая карточка
            # уходила в личку — то есть МИМО инбокса, хотя ответа ждала.
            pid = args[1] if len(args) > 1 else ""
            text = " ".join(args[2:]).strip() or f"🧩 Цепь #{pid}"
            channel, ok = deliver(text, _chain_markup(pid))
            _log.info(f"итог(карточка цепи {pid}): channel={channel} ok={ok} "
                      f"mid={last_send_id()} | {arch_text(text)}")
            sys.exit(0)
        if args and args[0] == "--gate-card":
            # КАРТОЧКА ВОРОТ клиентского контура: текст + кнопки [✅ Выкатить][⛔ Не выкатывай].
            # Адрес не выбираем и не называем руками — его по-прежнему решает deliver; кнопка лишь
            # включает ЗАМОК-1 (карточка с кнопкой обязана лечь в инбокс), то есть карточка уезжает
            # ТУДА ЖЕ, куда уезжала словом, и становится отвечаемой ОТТУДА, где показана.
            commit = args[1] if len(args) > 1 else ""
            text = " ".join(args[2:]).strip() or f"⛔ Ворота клиентского контура: {commit}"
            channel, ok = deliver(text, _gate_markup(commit))
            _log.info(f"итог(карточка ворот {commit}): channel={channel} ok={ok} "
                      f"mid={last_send_id()} | {arch_text(text)}")
            print(f"channel={channel} ok={int(bool(ok))}")
            sys.exit(0)
        if args and args[0] == "--pachka-card":
            # СУТОЧНЫЙ СПИСОК НАХОДОК РЕВИЗОРА (22.09.2026): текст и готовые кнопки — из файла, который
            # положил демон (`_revizor_pachka_send_live`); под каждой строкой кнопка ❌N, её ловит
            # pc_agent. Кнопка включает ЗАМОК-1 — список ложится в инбокс, адрес решает deliver.
            # Клавиатуру собирает демон, а не этот модуль: импорт `revizor_pachka` отсюда втянул бы
            # его в клиентское замыкание (dispatch_notify в нём живёт), а ему там делать нечего.
            with open(args[1] if len(args) > 1 else "", encoding="utf-8") as f:
                pl = json.load(f)
            text = str(pl.get("text") or "").strip() or "🔍 Ревизор: суточный список"
            kb = pl.get("markup")
            channel, ok = deliver(text, kb if isinstance(kb, dict) and kb.get("inline_keyboard") else None)
            _log.info(f"итог(суточный список ревизора): channel={channel} ok={ok} "
                      f"mid={last_send_id()} | {arch_text(text)}")
            print(f"channel={channel} ok={int(bool(ok))}")
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
                _log.info(f"итог(session_end): channel={channel} ok={ok} "
                          f"mid={last_send_id()} | {arch_text(text)}")
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
                          f"{TASKS_THREAD_ID} хуком session_end mid={last_send_id()} "
                          f"| {arch_text(text)}")
                sys.exit(0)
            if kind == "notification":
                # Сессия ЖДЁТ разрешения — признак читает это в самом тексте («ждёт твоего
                # разрешения/ввода») и ведёт в инбокс 1160, личка — фолбэк. Отдельной ветки
                # «этот вид всегда критический» больше нет: вид не адрес.
                channel, ok = deliver(text)
                _log.info(f"итог(notification): channel={channel} ok={ok} "
                          f"mid={last_send_id()} | {arch_text(text)}")
                _ledger_push("wait", "", "", channel, ok)   # род 🔔: признака в месте события нет
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
        _log.info(f"итог: channel={channel} ok={ok} mid={last_send_id()} | {arch_text(text)}")
    except Exception as e:
        # НИКОГДА не роняем вызывающий процесс
        try:
            _log.info(f"проглочена ошибка: {type(e).__name__}")
        except Exception:
            pass
    sys.exit(0)


if __name__ == "__main__":
    main()
