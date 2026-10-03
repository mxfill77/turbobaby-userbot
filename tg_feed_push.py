# -*- coding: utf-8 -*-
"""
tg_feed_push.py — ПУШЕР ЛЕНТЫ Telegram на сервер (03.10.2026, задание Штаба 0128p-79n.0310, TGPUSHPC0310).

Основание — слово владельца 03.10 13:19 «всё как с ватсапом надо» и его «да» 13:59: тексты клиентов
Telegram хранятся и на сервере. Ленту пишет userbot (`tg_feed.py`, TGFEEDPC0310): одна JSON-строка
на личное сообщение клиента. Здесь — её доставка на дверь сервера, откуда ряды возьмёт агент.

КОНТРАКТ ДВЕРИ (один для ПК и сервера):
    POST <адрес> · заголовки `Content-Type: application/json`, `Authorization: Bearer <токен>`
    тело  {"batch_id": "tgp-<начало>-<конец>", "rows": [ряды ленты как есть]}
    ответ 200 JSON {"accepted": [[chat_id, msg_id, dir], …]} — ключи, записанные до ответа, дубли тоже.
    Иное (не 200, таймаут, кривой JSON, нет ключа `accepted`, кривой элемент) — НЕ подтверждено НИЧЕГО.

КУРСОР ДВИГАЕТ ТОЛЬКО СЕРВЕР. Курсор — JSON с атомарной заменой (`os.replace`): смещение в байтах,
счёт пройденных строк и отпечаток байтов перед смещением. После ответа он идёт по ленте В ЕЁ ПОРЯДКЕ
до первого ряда, ключа которого нет в `accepted`, и встаёт перед ним. Потерянный ответ, таймаут,
обрыв процесса посреди пачки — курсор на месте, следующий заход шлёт те же ряды, сервер отвечает их
ключами как дублями. Повтор безвреден, потеря — нет.

ЧЕГО НЕ ШЛЁМ: неполную последнюю строку (писатель ещё пишет или оборвался) — её ждём. Строку, которая
не разбирается в ряд с ключом (обрывок прошлой записи, закрытый писателем переводом строки), — не
шлём и проходим с ГРОМКОЙ строкой: сервер не смог бы её подтвердить, и лента встала бы навсегда.

ГРОМКО И СТОП: лента короче курсора · под курсором другие байты (ленту подменили) · курсор нечитаем ·
один ряд больше пачки · нет токена. Отправки нет, пишется признак `tg_feed/tg_feed_push.alarm.json`
(`"alarm": true`). Снимает стоп человек; простой путь — отложить файл курсора: пушер начнёт с нуля,
а сервер ответит на прошлое дублями.

ПОВТОР: 5 → 10 → 20 … с, потолок паузы 5 мин; пауза живёт в курсоре и переживает перезапуск.
12 неудач подряд — громкая строка и тот же признак (`"kind": "ceiling"`); первая же продвинувшая
курсор пачка признак гасит (`"alarm": false`, файл переписывается, а не удаляется).

ПАЧКА ≤ 100 рядов и ≤ 256 КиБ тела. Заход ≤ 15 с своего бюджета; демон держит потолок 20 с снаружи.

ПЕРЕМЕННЫЕ (только имена; значения читает сам процесс):
    TG_FEED_PUSH_URL    адрес двери; нет — `WA_QUEUE_BASE` + `/tg-queue/push`
                        (`WA_QUEUE_BASE` по умолчанию https://wa.turbophuket.com, как в wa_bridge.py)
    TG_FEED_PUSH_TOKEN  токен; нет — `WA_PULL_SECRET`, секрет двери выдачи (wa_bridge.py)
Выключатель вызова (`TG_FEED_PUSH=1`) живёт в демоне, а не здесь: ручной запуск `--tick` работает.

ЧЕГО ЗДЕСЬ НЕТ ПО ПОСТРОЕНИЮ: Telegram, моста, SQLite, удаления файлов. Редиректы не идут — токен не
уедет на чужой адрес. Токен не печатается ни одной веткой (`_safe`).

Запуск: python tg_feed_push.py --tick   (код 0 — ок/нечего/пауза, 1 — неудача, 2 — стоп, 3 — занято)
"""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

import tg_feed

FEED_FILE = tg_feed.FEED_FILE
CURSOR_FILE = os.path.join(tg_feed.FEED_DIR, "tg_feed_push.cursor.json")
ALARM_FILE = os.path.join(tg_feed.FEED_DIR, "tg_feed_push.alarm.json")
LOCK_FILE = os.path.join(tg_feed.FEED_DIR, "tg_feed_push.lock")

URL_ENV, TOKEN_ENV = "TG_FEED_PUSH_URL", "TG_FEED_PUSH_TOKEN"
BASE_ENV, PULL_SECRET_ENV = "WA_QUEUE_BASE", "WA_PULL_SECRET"
DEFAULT_BASE = "https://wa.turbophuket.com"
DOOR_PATH = "/tg-queue/push"

KEY = ("chat_id", "msg_id", "dir")
DIRS = (tg_feed.DIR_IN, tg_feed.DIR_OUT)

BATCH_ROWS = 100
BATCH_BYTES = 256 * 1024
BATCH_HEAD = 128                  # запас на {"batch_id":"tgp-…-…","rows":[ ]}
BACKOFF_START, BACKOFF_MAX = 5, 300
CEILING_FAILS = 12
BUDGET_S = 15.0                   # свой бюджет захода; демон снаружи держит 20 с
HTTP_TIMEOUT_S = 10.0
MIN_LEFT_S = 2.0                  # меньше — новую пачку не начинаем
ANCHOR_LEN = 64
READ_MAX = 8 * 1024 * 1024
REMIND_S = 3600                   # громкий стоп повторяется не чаще раза в час

RC_OK, RC_FAIL, RC_STOP, RC_BUSY = 0, 1, 2, 3
LOUD = "!! TG-ПУШЕР: "
QUIET = "TG-ПУШЕР: "


def backoff(fails):
    """Пауза после `fails` неудач подряд: 5, 10, 20, 40 … с, не больше 300."""
    if fails <= 0:
        return 0
    return min(BACKOFF_START * (2 ** (fails - 1)), BACKOFF_MAX)


def door_url(env=None):
    e = os.environ if env is None else env
    url = (e.get(URL_ENV) or "").strip()
    if url:
        return url
    return ((e.get(BASE_ENV) or "").strip() or DEFAULT_BASE).rstrip("/") + DOOR_PATH


def door_token(env=None):
    e = os.environ if env is None else env
    return (e.get(TOKEN_ENV) or "").strip() or (e.get(PULL_SECRET_ENV) or "").strip()


# ─── транспорт ───────────────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Редирект → HTTPError с 3xx: заголовок с токеном не уходит на другой адрес."""

    def redirect_request(self, *a, **k):
        return None


def http_transport(url, body, headers, timeout):
    """POST → (код, тело-байты). HTTPError — это ответ, а не исключение; сеть/таймаут — исключение."""
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:                       # noqa: BLE001 — тело ошибки не обязательно
            data = b""
        return e.code, data


# ─── курсор и признак ────────────────────────────────────────────────────────────────────

class CursorBroken(Exception):
    pass


def _fresh_state():
    return {"offset": 0, "count": 0, "anchor": "", "fails": 0, "next_at": 0.0,
            "last_error": "", "skipped_bad": 0, "updated_at": 0.0}


def load_state(path):
    """Курсор с диска. Нет файла — с нуля. Есть, но нечитаем/не по форме — CursorBroken (СТОП,
    а не «с нуля»: молча начать сначала значит скрыть, что кто-то испортил файл)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return _fresh_state()
    try:
        st = json.loads(raw.decode("utf-8"))
    except Exception as e:                      # noqa: BLE001
        raise CursorBroken("курсор не JSON: %s" % type(e).__name__)
    if not isinstance(st, dict):
        raise CursorBroken("курсор не объект")
    out = _fresh_state()
    out.update(st)
    for k in ("offset", "count", "fails", "skipped_bad"):
        if not isinstance(out[k], int) or isinstance(out[k], bool) or out[k] < 0:
            raise CursorBroken("поле %s не целое ≥ 0" % k)
    if not isinstance(out["anchor"], str):
        raise CursorBroken("поле anchor не строка")
    try:
        out["next_at"] = float(out["next_at"] or 0)
    except (TypeError, ValueError):
        raise CursorBroken("поле next_at не число")
    return out


def _atomic_write_json(path, obj):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    data = json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_state(path, st, now):
    st = dict(st)
    st["updated_at"] = now
    _atomic_write_json(path, st)


def _read_json(path):
    try:
        with open(path, "rb") as f:
            v = json.loads(f.read().decode("utf-8"))
        return v if isinstance(v, dict) else {}
    except Exception:                           # noqa: BLE001 — признак не читается: считаем «нет»
        return {}


def raise_alarm(path, kind, reason, now, out, **extra):
    """Признак для ожидания + громкая строка. Тот же стоп громко не чаще раза в REMIND_S."""
    prev = _read_json(path)
    same = prev.get("alarm") is True and prev.get("kind") == kind and prev.get("reason") == reason
    said = float(prev.get("said_at") or 0) if same else 0.0
    loud = not same or now - said >= REMIND_S
    rec = {"alarm": True, "kind": kind, "reason": reason,
           "since": prev.get("since") if same else now,
           "said_at": now if loud else said, "feed": FEED_FILE}
    rec.update(extra)
    _atomic_write_json(path, rec)
    if loud:
        out(LOUD + ("ПОТОЛОК" if kind == "ceiling" else "СТОП") + " — " + reason +
            " · признак → " + path)


def clear_alarm(path, now, out):
    prev = _read_json(path)
    if prev.get("alarm") is True:
        _atomic_write_json(path, {"alarm": False, "cleared_at": now, "was": prev.get("reason", "")})
        out(QUIET + "признак снят — пачка снова подтверждена (было: %s)" % prev.get("reason", ""))


# ─── замок захода ────────────────────────────────────────────────────────────────────────

def take_lock(path):
    """Замок ОС на байт файла: отпускается сам со смертью процесса (снятый по таймауту заход
    следующего не запирает), файл не создаётся заново и не удаляется. → fd или None (занято)."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def drop_lock(fd):
    if fd is None:
        return
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    os.close(fd)


# ─── лента ───────────────────────────────────────────────────────────────────────────────

def row_key(rec):
    """Ключ ряда (chat_id, msg_id, dir) или None, если ряд не по контракту."""
    if not isinstance(rec, dict):
        return None
    cid, mid, d = rec.get("chat_id"), rec.get("msg_id"), rec.get("dir")
    if not isinstance(cid, int) or isinstance(cid, bool):
        return None
    if not isinstance(mid, int) or isinstance(mid, bool):
        return None
    if d not in DIRS:
        return None
    return (cid, mid, d)


def scan(buf, base, offset):
    """Полные строки буфера начиная с `offset` (абсолютное; `buf` начинается с `base`).
    → список (начало, конец, ряд|None, ключ|None). Хвост без \\n не входит."""
    entries = []
    pos = offset
    end_abs = base + len(buf)
    while pos < end_abs:
        nl = buf.find(b"\n", pos - base)
        if nl < 0:
            break
        stop = base + nl + 1
        raw = buf[pos - base:nl]
        rec = key = None
        if raw.strip():
            try:
                rec = json.loads(raw.decode("utf-8"))
                key = row_key(rec)
            except Exception:                   # noqa: BLE001 — обрывок записи: не ряд
                rec = key = None
            if key is None:
                rec = None
        entries.append((pos, stop, rec, key))
        pos = stop
    return entries


def parse_accepted(status, body):
    """Ответ двери → множество ключей или (None, причина). Строго: любой кривой элемент —
    не подтверждено НИЧЕГО (повтор безвреден, ложное подтверждение — потеря ряда)."""
    if status != 200:
        return None, "ответ %s" % status
    try:
        data = json.loads((body or b"").decode("utf-8"))
    except Exception as e:                      # noqa: BLE001
        return None, "ответ не JSON (%s)" % type(e).__name__
    if not isinstance(data, dict) or "accepted" not in data:
        return None, "в ответе нет ключа accepted"
    acc = data["accepted"]
    if not isinstance(acc, list):
        return None, "accepted не список"
    keys = set()
    for it in acc:
        if not isinstance(it, (list, tuple)) or len(it) != 3:
            return None, "кривой элемент accepted"
        k = row_key({"chat_id": it[0], "msg_id": it[1], "dir": it[2]})
        if k is None:
            return None, "кривой элемент accepted"
        keys.add(k)
    return keys, ""


def _row_bytes(rec):
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_batch(entries, i):
    """Пачка с позиции i: ≤ BATCH_ROWS рядов и ≤ BATCH_BYTES тела. → (j, rows, oversize):
    entries[i:j] покрыты пачкой (битые строки внутри — без отправки)."""
    rows, size, j = [], BATCH_HEAD, i
    while j < len(entries):
        rec = entries[j][2]
        if rec is not None:
            n = len(_row_bytes(rec)) + (1 if rows else 0)
            if len(rows) >= BATCH_ROWS or size + n > BATCH_BYTES:
                if not rows:
                    return j, rows, True
                break
            rows.append(rec)
            size += n
        j += 1
    return j, rows, False


def _anchor(buf, base, offset):
    lo = max(0, offset - ANCHOR_LEN)
    return hashlib.sha256(buf[lo - base:offset - base]).hexdigest() if offset else ""


# ─── заход ───────────────────────────────────────────────────────────────────────────────

def tick(feed=None, cursor=None, alarm=None, lock=None, url=None, token=None, transport=None,
         now=None, clock=None, out=None, budget=None, env=None):
    """Один заход пушера. Всё внешнее подменяемо (тесты без сокетов). → код возврата."""
    feed = feed or FEED_FILE
    cursor = cursor or CURSOR_FILE
    alarm = alarm or ALARM_FILE
    lock = lock or LOCK_FILE
    transport = transport or http_transport
    clock = clock or time.monotonic
    wall = (lambda: now) if now is not None else time.time
    out = out or (lambda s: print(s, flush=True))
    budget = BUDGET_S if budget is None else budget
    url = url if url is not None else door_url(env)
    token = token if token is not None else door_token(env)

    def safe(s):
        s = str(s)
        return s.replace(token, "<токен>") if token else s

    fd = take_lock(lock)
    if fd is None:
        out(QUIET + "занято другим заходом — пропуск")
        return RC_BUSY
    try:
        return _tick_locked(feed, cursor, alarm, url, token, transport, clock, wall, out,
                            budget, safe)
    finally:
        drop_lock(fd)


def _tick_locked(feed, cursor, alarm, url, token, transport, clock, wall, out, budget, safe):
    t0 = clock()
    try:
        st = load_state(cursor)
    except CursorBroken as e:
        raise_alarm(alarm, "stop", "курсор нечитаем (%s): %s" % (e, cursor), wall(), out)
        return RC_STOP
    if wall() < st["next_at"]:
        return RC_OK                            # пауза повтора ещё идёт — молча
    try:
        f = open(feed, "rb")
    except FileNotFoundError:
        if st["offset"] == 0:
            return RC_OK                        # ленты ещё нет — нечего слать
        raise_alarm(alarm, "stop", "ленты нет, а курсор на %d байт" % st["offset"], wall(), out,
                    offset=st["offset"])
        return RC_STOP
    with f:
        size = os.fstat(f.fileno()).st_size
        offset = st["offset"]
        if size < offset:
            raise_alarm(alarm, "stop", "лента короче курсора (%d < %d байт)" % (size, offset),
                        wall(), out, offset=offset, size=size)
            return RC_STOP
        base = max(0, offset - ANCHOR_LEN)
        f.seek(base)
        buf = f.read(min(size - base, READ_MAX))
    if offset and _anchor(buf, base, offset) != st["anchor"]:
        raise_alarm(alarm, "stop", "под курсором другие байты (ленту подменили?) на %d" % offset,
                    wall(), out, offset=offset)
        return RC_STOP
    entries = scan(buf, base, offset)
    if not entries:
        if size - offset >= READ_MAX:
            raise_alarm(alarm, "stop", "строка длиннее %d байт на %d" % (READ_MAX, offset),
                        wall(), out, offset=offset)
            return RC_STOP
        return RC_OK                            # нового полного ряда нет (хвост ждём)
    if not token:
        raise_alarm(alarm, "stop", "нет токена (%s / %s)" % (TOKEN_ENV, PULL_SECRET_ENV),
                    wall(), out)
        return RC_STOP

    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
    i, sent = 0, 0
    while i < len(entries):
        left = budget - (clock() - t0)
        if left < MIN_LEFT_S:
            break
        j, rows, oversize = build_batch(entries, i)
        if oversize:
            raise_alarm(alarm, "stop", "ряд на %d больше пачки %d байт" % (entries[j][0], BATCH_BYTES),
                        wall(), out, offset=entries[j][0])
            return RC_STOP
        if not rows:                            # только битые строки — проходим их
            st, i = _advance(st, entries, i, j, set(), buf, base, out)
            save_state(cursor, st, wall())
            continue
        bid = "tgp-%d-%d" % (entries[i][0], entries[j - 1][1])
        body = json.dumps({"batch_id": bid, "rows": rows}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        try:
            status, resp = transport(url, body, dict(headers), min(HTTP_TIMEOUT_S, left))
            keys, why = parse_accepted(status, resp)
        except Exception as e:                  # noqa: BLE001 — сеть/таймаут: не подтверждено ничего
            keys, why = None, "%s: %s" % (type(e).__name__, safe(e))
        new_st, ni = (st, i) if keys is None else _advance(st, entries, i, j, keys, buf, base, out)
        moved = sum(1 for e in entries[i:ni] if e[2] is not None)
        if not moved:                           # битые строки впереди пройдены, ряд — нет
            return _fail(new_st, cursor, alarm, why or "сервер не подтвердил первый ряд пачки",
                         wall(), out, safe)
        sent += moved
        new_st["fails"], new_st["next_at"], new_st["last_error"] = 0, 0.0, ""
        st, i = new_st, ni
        save_state(cursor, st, wall())
        clear_alarm(alarm, wall(), out)
        if ni < j:
            out(QUIET + "частично: подтверждено %d из %d рядов пачки %s — остаток следующим заходом"
                % (moved, len(rows), bid))
            break
    if sent:
        out(QUIET + "отправлено %d рядов · курсор %d байт · счёт %d" % (sent, st["offset"], st["count"]))
    return RC_OK


def _advance(st, entries, i, j, keys, buf, base, out):
    """Курсор по ленте В ЕЁ ПОРЯДКЕ до первого неподтверждённого ряда в entries[i:j]."""
    st = dict(st)
    k = i
    while k < j:
        start, stop, rec, key = entries[k]
        if rec is None:
            if buf[start - base:stop - base].strip():
                st["skipped_bad"] += 1
                out(LOUD + "строка %d–%d не ряд (обрывок записи?) — не отправлена, пройдена"
                    % (start, stop))
        elif key not in keys:
            break
        st["offset"], st["count"] = stop, st["count"] + 1
        k += 1
    if k > i:
        st["anchor"] = _anchor(buf, base, st["offset"])
    return st, k


def _fail(st, cursor, alarm, why, now, out, safe):
    st = dict(st)
    st["fails"] += 1
    pause = backoff(st["fails"])
    st["next_at"] = now + pause
    st["last_error"] = safe(why)[:300]
    save_state(cursor, st, now)
    out(QUIET + "не подтверждено (%s) · неудача %d подряд · повтор через %d с"
        % (st["last_error"], st["fails"], pause))
    if st["fails"] >= CEILING_FAILS:
        # причина постоянна (иначе громко на каждой неудаче), подробности — в полях признака
        raise_alarm(alarm, "ceiling", "%d+ неудач подряд, повтор раз в %d с" % (CEILING_FAILS, BACKOFF_MAX),
                    now, out, fails=st["fails"], last_error=st["last_error"])
    return RC_FAIL


def _load_env_file():
    """Окружение демона и так несёт .env; ручной запуск подхватит его сам (как wa_bridge)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(tg_feed.BASE_DIR, ".env"))
    except Exception:                           # noqa: BLE001 — dotenv не обязателен
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv != ["--tick"]:
        print("usage: tg_feed_push.py --tick", file=sys.stderr)
        return 64
    _load_env_file()
    return tick()


if __name__ == "__main__":
    sys.exit(main())
