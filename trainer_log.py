# -*- coding: utf-8 -*-
"""
trainer_log.py — ЛОГ ГРУППЫ-ТРЕНАЖЁРА в мозг (Brain-док `KB_trainer_log`).

ЗАЧЕМ. Всё, что происходит в «Тренеровке», должно оставаться в мозге, а не только в Telegram:
старт ТЕСТ-N, каждая реплика клиента (текст/гео/фото), каждый ответ бота ЦЕЛИКОМ (с шапкой и
служебными тегами), каждое нажатие кнопок и каждый принятый/отменённый урок. По этому логу
владелец разбирает прогон, а не по памяти сессии.

ФОРМАТ СТРОКИ (одна строка = одно событие):
    TRN <ГГГГ-ММ-ДД ЧЧ:ММ> | TEST-N | client|bot|btn|lesson | <текст>
Переводы строк внутри текста заменяем на «⏎» — строка обязана остаться ОДНОЙ (иначе лог
перестаёт быть построчным и его нельзя резать/архивировать по границе события).

КАНАЛ — ТОТ ЖЕ, что у cowork_log_append.py: Bridge read_doc/write_doc. ПРОБА ПО ФАКТУ (23.07):
  • Bridge разрешает адресацию и по имени (`name=`), и по файловому id (`id=`) — и на чтение,
    и на запись (write_doc с id уходит в DriveApp.getFileById);
  • но СОЗДАВАТЬ доки канал НЕ УМЕЕТ: имена резолвятся по фиксированному манифесту
    (`list_brain`), и `write_doc name=KB_trainer_log` отвечает `unknown_name`.
Поэтому обходного пути мы НЕ строим (прямое требование ТЗ): пустой док создаёт Штаб и передаёт
file id. Пока id не задан — модуль честно НЕ ПИШЕТ и возвращает status='no_doc' (тренажёр при
этом работает как работал: лог — наблюдение, а не часть клиентского пути).

ГДЕ ЖИВЁТ ID. `.env` мы не трогаем (ограничение задачи), поэтому:
  1) переменная окружения TRAINER_LOG_DOC_ID (если Штаб положит её туда) — приоритет;
  2) сайдкар-файл `trainer_log_doc.json` рядом с кодом: {"doc_id": "...", "archive_id": "..."}.
Секретов тут нет: file id Drive регистрируется в KB_MASTER открытой строкой.

РОТАЦИЯ. Док > TRAINER_LOG_MAX_CHARS (≈1 МБ) → САМЫЕ СТАРЫЕ строки срезаются в архив-док
(archive_id). Архива нет → срез НЕ делаем и пишем предупреждение в лог процесса: молча терять
историю нельзя (лучше распухший док, чем тихо съеденный хвост).

FAIL-SAFE ВЕЗДЕ. Любая ошибка сети/Bridge/парса → запись не удалась, исключение НЕ летит наружу,
тренажёр не ломается. Лог мозга не имеет права уронить клиентский контур.
"""

import os
import json
import datetime
import urllib.request
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
SIDECAR = os.path.join(BASE_DIR, "trainer_log_doc.json")

DOC_NAME = "KB_trainer_log"            # имя дока в мозге (для KB_MASTER и когда он попадёт в манифест)
KIND_CLIENT, KIND_BOT, KIND_BTN, KIND_LESSON = "client", "bot", "btn", "lesson"
KINDS = (KIND_CLIENT, KIND_BOT, KIND_BTN, KIND_LESSON)

# 1 МБ — порог ротации из ТЗ. Считаем в СИМВОЛАХ (Bridge отдаёт/принимает текст, не байты).
MAX_CHARS = int(os.getenv("TRAINER_LOG_MAX_CHARS", "1000000") or "1000000")
# Сколько символов оставляем в живом доке после среза (свежий хвост), остальное — в архив.
KEEP_CHARS = int(os.getenv("TRAINER_LOG_KEEP_CHARS", "700000") or "700000")
HTTP_TIMEOUT = int(os.getenv("TRAINER_LOG_TIMEOUT", "30") or "30")

import logging
log = logging.getLogger("trainer_log")   # хендлеры вешает процесс-хозяин (userbot/moderbot)


# ------------------------------- конфиг канала -------------------------------

def _env_file(path=None):
    """BRIDGE_URL/BRIDGE_TOKEN ПРЯМО из .env репо (как cowork_log_append): сервис-контекст может
    не иметь их в окружении процесса. Секреты не логируем и наружу не отдаём."""
    vals = {}
    try:
        with open(path or ENV_PATH, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return vals


def _sidecar(path=None):
    try:
        with open(path or SIDECAR, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def doc_id(sidecar_path=None):
    """File id живого дока: env TRAINER_LOG_DOC_ID → сайдкар → '' (док ещё не заведён Штабом)."""
    return (os.getenv("TRAINER_LOG_DOC_ID", "").strip()
            or str(_sidecar(sidecar_path).get("doc_id") or "").strip())


def archive_id(sidecar_path=None):
    """File id архив-дока (для ротации) или '' — тогда срез НЕ делаем."""
    return (os.getenv("TRAINER_LOG_ARCHIVE_DOC_ID", "").strip()
            or str(_sidecar(sidecar_path).get("archive_id") or "").strip())


# ------------------------------- формат строки -------------------------------

def _stamp(now=None):
    return (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M")


def format_line(n, kind, text, now=None):
    """Строка лога ровно по ТЗ: «TRN <дата время> | TEST-N | <kind> | <текст>».
    Переводы строк → «⏎» (одно событие = одна строка). Неизвестный kind → 'btn' (не теряем событие)."""
    kind = kind if kind in KINDS else KIND_BTN
    body = " ⏎ ".join(s.strip() for s in str(text or "").splitlines() if s.strip()) or "[пусто]"
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return f"TRN {_stamp(now)} | TEST-{n} | {kind} | {body}"


# ------------------------------- транспорт Bridge ----------------------------

def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(full, method="GET"),
                                timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _doc_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and isinstance(obj.get(key), str):
            return obj[key]
    return None


def read_doc(did, env=None, get=None):
    """Текст дока по file id → str | None (ошибка/нет доступа). Читает ТОЛЬКО по id."""
    env = env if env is not None else _env_file()
    url, token = env.get("BRIDGE_URL"), env.get("BRIDGE_TOKEN")
    if not (url and token and did):
        return None
    try:
        r = (get or _get)(url, {"action": "read_doc", "token": token, "id": did})
    except Exception as e:
        log.warning("trainer_log: read_doc упал: %s: %s", type(e).__name__, e)
        return None
    if not (isinstance(r, dict) and r.get("ok")):
        log.warning("trainer_log: read_doc не ok: %s", json.dumps(r, ensure_ascii=False)[:200])
        return None
    return _doc_text(r)


def write_doc(did, text, env=None, post=None):
    """Перезаписать док по file id. → True|False. Пустой id/текст None → False (ничего не пишем)."""
    env = env if env is not None else _env_file()
    url, token = env.get("BRIDGE_URL"), env.get("BRIDGE_TOKEN")
    if not (url and token and did) or text is None:
        return False
    try:
        w = (post or _post)(url, {"action": "write_doc", "token": token, "id": did, "text": text})
    except Exception as e:
        log.warning("trainer_log: write_doc упал: %s: %s", type(e).__name__, e)
        return False
    if not (isinstance(w, dict) and w.get("ok")):
        log.warning("trainer_log: write_doc не ok: %s", json.dumps(w, ensure_ascii=False)[:200])
        return False
    return True


# ------------------------------- ротация -------------------------------------

def rotate(text, max_chars=None, keep_chars=None):
    """Разрезать лог по границе СТРОКИ: → (свежий_хвост, срез_в_архив).
    Порог не превышен → (text, '') — резать нечего. Режем ТОЛЬКО по «\\n», чтобы событие никогда
    не разрывалось пополам. Новые строки лежат СВЕРХУ (как в cowork_log), значит в архив уходит
    НИЗ — самые старые события."""
    max_chars = MAX_CHARS if max_chars is None else max_chars
    keep_chars = KEEP_CHARS if keep_chars is None else keep_chars
    t = text or ""
    if len(t) <= max_chars:
        return t, ""
    cut = t.rfind("\n", 0, max(0, keep_chars))
    if cut <= 0:                       # ни одной границы строки — не рвём событие, оставляем как есть
        return t, ""
    return t[:cut], t[cut + 1:]


# ------------------------------- публичная запись ----------------------------

def append(n, kind, text, now=None, env=None, get=None, post=None, sidecar_path=None):
    """Дописать ОДНО событие тренажёра в KB_trainer_log. → dict(status, line):
      status='ok'      — строка легла в док;
      status='no_doc'  — file id ещё не задан (Штаб не завёл док) — НЕ ошибка, просто не пишем;
      status='error'   — канал недоступен/Bridge отказал (тренажёр при этом жив).
    Новая строка идёт ПЕРВОЙ (свежее сверху) — тем же порядком, что cowork_log."""
    line = format_line(n, kind, text, now)
    did = doc_id(sidecar_path)
    if not did:
        log.info("trainer_log: doc_id не задан — событие не записано: %s", line[:160])
        return {"status": "no_doc", "line": line}
    env = env if env is not None else _env_file()
    old = read_doc(did, env, get)
    if old is None:
        return {"status": "error", "line": line}
    fresh, spill = rotate(line + "\n" + old)
    if spill:
        aid = archive_id(sidecar_path)
        if aid:
            prev = read_doc(aid, env, get) or ""
            if write_doc(aid, (spill + "\n" + prev).strip(), env, post):
                log.warning("trainer_log: ротация — %s символов срезано в архив", len(spill))
            else:
                fresh, spill = line + "\n" + old, ""    # архив не принял → НИЧЕГО не теряем
        else:
            log.warning("trainer_log: лог перерос %s символов, но archive_id не задан — "
                        "срез НЕ делаю (историю молча не теряем)", MAX_CHARS)
            fresh = line + "\n" + old
    return {"status": "ok" if write_doc(did, fresh, env, post) else "error", "line": line}


def safe_append(n, kind, text, **kw):
    """Обёртка для боевых точек вызова: НИКОГДА не бросает и не блокирует. → status-строка."""
    try:
        return append(n, kind, text, **kw).get("status", "error")
    except Exception as e:                                  # pragma: no cover — тотальный fail-safe
        log.warning("trainer_log: запись упала целиком: %s: %s", type(e).__name__, e)
        return "error"
