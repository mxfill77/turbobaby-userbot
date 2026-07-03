# -*- coding: utf-8 -*-
"""
dispatch_notify.py — fire-and-forget Telegram-уведомление от Dispatch/Claude Code.

Зеркало VPS-паттерна: когда сессия на ПК ждёт разрешения/ввода ИЛИ завершила задачу,
Филиппу уходит сообщение (он пропускает запросы в приложении).

Канал: ЛИЧКА Филиппу (chat_id из pc_agent.ALLOWED_USER_ID) — основной; если DM недоступна
(бот не может инициировать диалог / 403 / getUpdates-ошибка) → ФОЛБЭК в тему 205
(pc_agent.HQ_CHAT_ID / HQ_THREAD_ID). Константы берём из pc_agent (единый источник).

НЕ НАВРЕДИ: любые сетевые/HTTP-ошибки проглатываются, короткий лог в dispatch_notify.log,
ВСЕГДА exit 0 — уведомление никогда не роняет вызывающий процесс/сессию. Таймаут на запрос.
Токен читаем из .env (AGENT_BOT_TOKEN), НИКОГДА не логируем и не печатаем.

Использование:
  python dispatch_notify.py "любой текст"          # прямая отправка (аргумент)
  echo '{"message":"..."}' | python dispatch_notify.py --hook notification
  python dispatch_notify.py --hook stop
"""

import os
import sys
import io
import json
import logging
import contextlib
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
NOTIFY_LOG = os.path.join(HERE, "dispatch_notify.log")
HTTP_TIMEOUT = 8  # сек

# --- лог ТОЛЬКО в свой файл (не трогаем root / pc_agent.log) ---
_log = logging.getLogger("dispatch_notify")
_log.setLevel(logging.INFO)
_log.propagate = False
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


def send(text):
    """DM Филиппу; при неудаче — фолбэк в тему 205. Возвращает (channel, ok)."""
    if not TOKEN:
        _log.info("нет AGENT_BOT_TOKEN — уведомление пропущено")
        return ("none", False)
    ok, resp = _api("sendMessage", {"chat_id": DM_CHAT_ID, "text": text})
    if ok:
        _log.info(f"DM ok → {DM_CHAT_ID}")
        return ("DM", True)
    _log.info(
        f"DM не прошёл (code={resp.get('error_code')} {str(resp.get('description',''))[:80]}) "
        f"— фолбэк в тему {HQ_THREAD_ID}"
    )
    ok2, resp2 = _api("sendMessage",
                      {"chat_id": HQ_CHAT_ID, "message_thread_id": HQ_THREAD_ID, "text": text})
    if ok2:
        _log.info(f"фолбэк 205 ok → {HQ_CHAT_ID}/{HQ_THREAD_ID}")
        return ("205", True)
    _log.info(f"фолбэк 205 не прошёл (code={resp2.get('error_code')} {str(resp2.get('description',''))[:80]})")
    return ("205", False)


def _read_stdin_json():
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw and raw.strip().startswith("{") else {}
    except Exception:
        return {}


def _build(kind, hook):
    if kind == "notification":
        ctx = str(hook.get("message") or hook.get("notification") or "").strip()
        return "🔔 Dispatch ждёт твоего разрешения/ввода" + (f": {ctx}" if ctx else ".")
    if kind == "stop":
        return "✅ Dispatch: задача завершена."
    return None


def main():
    args = list(sys.argv[1:])
    try:
        if args and args[0] == "--hook":
            kind = args[1] if len(args) > 1 else ""
            text = _build(kind, _read_stdin_json()) or f"🔔 Dispatch: {kind or 'событие'}"
        elif args:
            text = " ".join(args).strip()
        elif not sys.stdin.isatty():
            text = (sys.stdin.read() or "").strip() or "🔔 Dispatch"
        else:
            text = "🔔 Dispatch"
        channel, ok = send(text)
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
