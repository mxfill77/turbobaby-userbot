# -*- coding: utf-8 -*-
"""wa_bridge.py — ВХОДЯЩИЙ МОСТ WhatsApp: ПК ← очередь VPS (WA-2b, 07.09.2026).

ЧТО ЭТО. VPS принимает WhatsApp и КЛАДЁТ В ОЧЕРЕДЬ — своего суждения о клиенте у него нет
(wa_webhook.py: «It NEVER sends any message back to WhatsApp»). Забирает, решает и готовит
ответ ПК. Этот модуль — дверь ПК в ту очередь: забор → разбор по ВИДУ записи → тот же
клиентский путь, что у Telegram → карточка модерации. Отправки клиенту здесь нет ни одной.

ПУТЬ ОДИН, А НЕ ДВА. Черновик собирает suggest.draft_from_transcript — ровно то тело, которым
живёт Telegram-инбаунд (цена из Календаря, allowlist парка, книга правил, все гварды и пост-чеки).
Здесь только СВОЙ контекст (по номеру, а не по окну Telegram) и своя адресация. Заведись у WA
второй конвейер — он разошёлся бы с первым молча, и расхождение увидел бы клиент.

ВИД ЗАПИСИ РЕШАЕТ СЕРВЕР, А НЕ МЫ. У каждой строки забора есть поле kind (правка сервера
07.09.2026, wa_kind.py): inbound · echo · history · receipt · unknown. Мы его ЧИТАЕМ и не
пересчитываем: два независимых судьи одного и того же разошлись бы. Поля kind нет вовсе или
значение незнакомое → unknown, и это ТРЕТИЙ ИСХОД, а не «наверное клиент»: молчаливое
приведение неизвестного к входящему даёт карточку там, где её быть не должно, — менеджер
отвечает на квитанцию или на собственное эхо. Это же правило страхует от СТАРОГО сервера: если
на той стороне поднимут код без различителя, записи приедут без kind и станут unknown —
ни одной ложной карточки.

ЧТО С ЧЕМ ДЕЛАЕМ:
  inbound  → контекст + ЧЕРНОВИК → карточка модератору «Черновик клиенту WA · +номер · имя»
  echo     → ТОЛЬКО контекст (ручной ответ менеджера с телефона; черновик не порождаем)
  history  → контекст пачкой, без карточек (досинхрон старой переписки)
  receipt  → счётчик в лог, больше ничего
  unknown  → отказ в лог, клиентским сообщением НЕ считаем

ACK — ТОЛЬКО ПОСЛЕ УСПЕХА, И ПОШТУЧНО. Подтверждаем ровно те id, которые обработаны; упавшая
запись не подтверждается и вернётся по истечении аренды сервера (LEASE 5 мин). Всё упало —
ack не уходит ВООБЩЕ (ни одного сетевого вызова). Против вечного круга — счётчик попыток:
после WA_MAX_TRIES заходов запись подтверждается с громкой строкой в лог, чтобы очередь не
встала колом на одной строке.

ПОВТОРНАЯ ВЫДАЧА НЕ ДАЁТ ДУБЛЯ. Сервер выдаёт строку в АРЕНДУ и честно говорит, что при потере
ack выдаст её снова («the PC dedupes by id»). Поэтому контекст пополняется идемпотентно — по id
записи, — и второй заход той же строки не удваивает ни реплику, ни карточку.

ОТПРАВКА — ЗАГЛУШКА. Двери wa_send не существует ни на VPS, ни здесь: ответ клиенту в WhatsApp
сегодня технически невозможен (KB_WA_PLAN §7). Одобренный черновик WA не уходит в Telegram-канал
по ошибке — его снимает замок в suggest.poll_and_send по префиксу client_ref.

СЕКРЕТ. WA_PULL_SECRET читает САМ этот модуль из окружения/.env; в лог, в текст ошибки и в
исключения он не попадает ни одной веткой (_safe). Нет секрета → мост не поднимается вовсе.

ЗАПУСК (мост НЕ включён ни в один автозапуск — включение отдельным решением владельца):
    --once   один заход: забор → обработка → ack
    --probe  только посмотреть: забор БЕЗ обработки и БЕЗ ack (записи не трогаем)
    --loop   цикл раз в 20с
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:                                    # dotenv не обязателен: тогда только окружение
    pass

log = logging.getLogger("wa_bridge")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── настройки забора ────────────────────────────────────────────────────────────────────
WA_QUEUE_BASE = (os.getenv("WA_QUEUE_BASE") or "https://wa.turbophuket.com").rstrip("/")
WA_PULL_SECRET = (os.getenv("WA_PULL_SECRET") or "").strip()
POLL_SEC = float(os.getenv("WA_POLL_SEC", "20") or "20")     # раз в 20с, как задано
PULL_LIMIT = int(os.getenv("WA_PULL_LIMIT", "20") or "20")   # до 20 записей; сервер режет сам
HTTP_TIMEOUT = float(os.getenv("WA_HTTP_TIMEOUT", "25") or "25")
BACKOFF_START = float(os.getenv("WA_BACKOFF_START", "20") or "20")
BACKOFF_MAX = float(os.getenv("WA_BACKOFF_MAX", "600") or "600")
MAX_TRIES = int(os.getenv("WA_MAX_TRIES", "3") or "3")       # заходов на одну запись до сдачи
CTX_KEEP = int(os.getenv("WA_CTX_KEEP", "200") or "200")     # сколько реплик диалога храним

# ─── виды записи: имена ДОСЛОВНО серверные (wa_kind.py), чтобы у одной вещи не стало двух имён ──
KIND_INBOUND = "inbound"
KIND_ECHO = "echo"
KIND_HISTORY = "history"
KIND_RECEIPT = "receipt"
KIND_UNKNOWN = "unknown"
KNOWN_KINDS = (KIND_INBOUND, KIND_ECHO, KIND_HISTORY, KIND_RECEIPT)

# Заглушка отправки: дословная строка, которую ждёт лог (реальная отправка — после онбординга).
SEND_DISABLED_MSG = "WA-отправка отключена (ключ не задан)"

_DIGITS_RE = re.compile(r"\D+")


def is_enabled() -> bool:
    """Мост поднимается ТОЛЬКО при заданном WA_PULL_SECRET. Нет секрета → ни одного вызова наружу."""
    return bool(WA_PULL_SECRET)


def _safe(text) -> str:
    """Строка наружу без секрета: адрес забора несёт его в ПУТИ, и он лезет в текст ошибок urllib."""
    s = str(text or "")
    if WA_PULL_SECRET:
        s = s.replace(WA_PULL_SECRET, "<secret>")
    return s


def _url(verb: str) -> str:
    return WA_QUEUE_BASE + "/wa-queue/" + verb + "/" + WA_PULL_SECRET


def _default_transport(method: str, url: str, payload=None, timeout=None):
    """HTTP наружу. Возвращает разобранный JSON (dict). Любой сбой — исключение вызывающему."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout or HTTP_TIMEOUT) as r:
        body = r.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


# ─── забор и подтверждение ───────────────────────────────────────────────────────────────

def pull(transport=None, limit=None):
    """GET /wa-queue/pull/<секрет> → (ok, items, error). Ошибка сети/адреса → (False, [], причина)
    БЕЗ секрета в тексте. Тело не по форме (не dict / ok=false) → тоже честный отказ."""
    if not is_enabled():
        return False, [], "WA_PULL_SECRET не задан"
    try:
        data = (transport or _default_transport)("GET", _url("pull"))
    except Exception as e:
        return False, [], type(e).__name__ + ": " + _safe(e)
    if not isinstance(data, dict) or not data.get("ok"):
        return False, [], _safe((data or {}).get("error") if isinstance(data, dict) else "не dict")
    items = data.get("items")
    if not isinstance(items, list):
        return False, [], "items не список"
    cap = int(limit or PULL_LIMIT)
    return True, [it for it in items if isinstance(it, dict)][:cap], ""


def ack(ids, transport=None):
    """POST /wa-queue/ack/<секрет> с телом ids → (ok, acked, error). Пустой список — НЕ вызов:
    подтверждать нечего, а лишний запрос наружу это лишний путь для сбоя."""
    ids = [i for i in (ids or []) if i is not None]
    if not ids:
        return True, 0, ""
    if not is_enabled():
        return False, 0, "WA_PULL_SECRET не задан"
    try:
        data = (transport or _default_transport)("POST", _url("ack"), {"ids": ids})
    except Exception as e:
        return False, 0, type(e).__name__ + ": " + _safe(e)
    if not isinstance(data, dict) or not data.get("ok"):
        return False, 0, _safe((data or {}).get("error") if isinstance(data, dict) else "не dict")
    return True, int(data.get("acked") or 0), ""


# ─── вид записи и адресация ──────────────────────────────────────────────────────────────

def kind_of(rec) -> str:
    """Вид записи ПО ПОЛЮ СЕРВЕРА. Поля нет / значение незнакомое → unknown (НИКОГДА не inbound)."""
    k = str((rec or {}).get("kind") or "").strip().lower()
    return k if k in KNOWN_KINDS else KIND_UNKNOWN


def number_of(rec) -> str:
    """Номер клиента в каноничном виде «+66…». Пусто → '' (запись без адреса — не клиентская)."""
    digits = _DIGITS_RE.sub("", str((rec or {}).get("from") or ""))
    return ("+" + digits) if digits else ""


def wa_ref_prefix() -> str:
    """Префикс WA-адреса берём из suggest — там же его читает замок отправки (одно определение)."""
    import suggest
    return suggest.WA_CLIENT_REF_PREFIX


def client_ref(number: str, name: str = "") -> str:
    """Адрес карточки: «WA · +номер · имя». Модербот печатает заголовок как
    «Черновик клиенту {client_ref}», то есть «Черновик клиенту WA · +66… · Иван»."""
    parts = [wa_ref_prefix().strip().rstrip("·").strip(), number or "?"]
    if (name or "").strip():
        parts.append(name.strip())
    return " · ".join(p for p in parts if p)


def client_id_of(number: str) -> int:
    """Числовой id клиента WA для очереди модерации: ОТРИЦАТЕЛЬНЫЙ, из цифр номера. Telegram-id
    положительны, поэтому столкнуться им негде; отправка по нему всё равно закрыта замком."""
    digits = _DIGITS_RE.sub("", number or "")
    return -int(digits) if digits else 0


# ─── контекст диалога по номеру (как по @username в Telegram) ────────────────────────────

def ctx_key(number: str) -> str:
    return "wa:ctx:" + (number or "?")


def load_context(number, path=None):
    """Реплики диалога по номеру: список словарей id/role/text/ts/name. Нет записи / битое → []."""
    import moderation_ipc
    try:
        raw = moderation_ipc.get_meta(ctx_key(number), path=path)
        data = json.loads(raw) if raw else []
    except Exception:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def append_context(number, role, text, rec_id=None, ts=None, name=None, path=None):
    """Дописать реплику в контекст номера. ИДЕМПОТЕНТНО по id записи: повторная выдача той же
    строки сервером (потерянный ack) диалог не удваивает. → True, если реплика легла."""
    import moderation_ipc
    items = load_context(number, path=path)
    if rec_id is not None and any(str(d.get("id")) == str(rec_id) for d in items):
        return False
    items.append({"id": rec_id, "role": role, "text": (text or ""),
                  "ts": ts or 0, "name": (name or "")})
    items = items[-CTX_KEEP:]
    moderation_ipc.set_meta(ctx_key(number), json.dumps(items, ensure_ascii=False), path=path)
    return True


def context_transcript(number, path=None) -> str:
    """Контекст в ТОМ ЖЕ формате, что transcript_from у Telegram: «[клиент]: …» / «[менеджер]: …»,
    старое сверху. Другого формата suggest не понимает, и второго формата заводить нельзя."""
    lines = []
    for d in load_context(number, path=path):
        body = (d.get("text") or "").strip() or "[без текста / медиа]"
        lines.append("[" + (d.get("role") or "клиент") + "]: " + body)
    return "\n".join(lines)


def _tries_key(rec_id) -> str:
    return "wa:try:" + str(rec_id)


def _bump_try(rec_id, path=None) -> int:
    """Счётчик заходов на одну запись (против вечного круга на неубиваемой строке)."""
    import moderation_ipc
    try:
        n = int(moderation_ipc.get_meta(_tries_key(rec_id), path=path) or 0)
    except Exception:
        n = 0
    n += 1
    try:
        moderation_ipc.set_meta(_tries_key(rec_id), str(n), path=path)
    except Exception:
        pass
    return n


# ─── черновик по входящему: ТОТ ЖЕ путь, что у Telegram ──────────────────────────────────

def _default_drafter(transcript, client_id=None, client_ref=None, client_name=None, first=False):
    """Мост в клиентский путь suggest. Отдельная функция — чтобы самотесты подменяли её целиком
    и ни один тест не звал живую модель."""
    import suggest
    return asyncio.run(suggest.draft_from_transcript(
        transcript, client_id=client_id, client_ref=client_ref, client_name=client_name,
        first=first, strip_repeat_greeting=not first))


def _handle_inbound(rec, number, name, drafter, path):
    """Входящее клиента: контекст → черновик → карточка модерации. → 'inbound' | 'no_draft'."""
    import moderation_ipc
    append_context(number, "клиент", rec.get("text"), rec_id=rec.get("id"),
                   ts=rec.get("ts"), name=name, path=path)
    ctx = load_context(number, path=path)
    first = not any((d.get("role") or "") == "менеджер" for d in ctx)
    transcript = context_transcript(number, path=path)
    ref = client_ref(number, name)
    made = (drafter or _default_drafter)(
        transcript, client_id=client_id_of(number), client_ref=ref,
        client_name=(name or None), first=first)
    if not made:
        log.warning("WA: черновик по %s НЕ собран (отказ клиентского пути) — карточки нет", ref)
        return "no_draft"
    did = moderation_ipc.enqueue_draft(made, path=path)
    log.info("WA: черновик #%s → карточка модерации для %s", did, ref)
    return "inbound"


def process_record(rec, drafter=None, path=None):
    """Одна запись очереди → исход-строка. Исключение = обработка УПАЛА (запись не подтверждаем).

    Исходы: inbound (карточка) · no_draft (клиентский путь отказал) · echo · history · receipt ·
    unknown · no_number (запись без адреса — клиентской не считаем)."""
    kind = kind_of(rec)
    number = number_of(rec)
    name = str((rec or {}).get("name") or "").strip()
    if kind == KIND_RECEIPT:
        return KIND_RECEIPT                       # квитанция: только счётчик, ни контекста, ни карточки
    if kind == KIND_UNKNOWN:
        log.warning("WA: ОТКАЗ — вид записи не опознан (id=%s, kind=%r, type=%r); клиентским "
                    "сообщением НЕ считаю", (rec or {}).get("id"), (rec or {}).get("kind"),
                    (rec or {}).get("msg_type"))
        return KIND_UNKNOWN
    if not number:
        log.warning("WA: запись id=%s вида %s без номера — пропускаю", (rec or {}).get("id"), kind)
        return "no_number"
    if kind == KIND_ECHO:
        append_context(number, "менеджер", rec.get("text"), rec_id=rec.get("id"),
                       ts=rec.get("ts"), name=name, path=path)
        return KIND_ECHO                          # ручной ответ менеджера: контекст, но НЕ черновик
    if kind == KIND_HISTORY:
        # досинхрон: чья реплика — говорит эхо-признак строки; карточек история не даёт ни одной
        role = "менеджер" if rec.get("echo") else "клиент"
        append_context(number, role, rec.get("text"), rec_id=rec.get("id"),
                       ts=rec.get("ts"), name=name, path=path)
        return KIND_HISTORY
    return _handle_inbound(rec, number, name, drafter, path)


def process_batch(items, drafter=None, path=None, max_tries=None):
    """Пачка записей → (счётчики, ids_для_ack). Порядок — как отдал сервер (id по возрастанию:
    история обязана лечь в контекст в своём порядке). Упавшая запись в ack НЕ попадает."""
    counters = {k: 0 for k in ("inbound", "echo", "history", "receipt", "unknown",
                               "no_draft", "no_number", "failed", "gave_up")}
    ack_ids = []
    for rec in (items or []):
        rid = (rec or {}).get("id")
        try:
            outcome = process_record(rec, drafter=drafter, path=path)
        except Exception as e:
            counters["failed"] += 1
            tries = _bump_try(rid, path=path)
            limit = int(max_tries or MAX_TRIES)
            if tries >= limit:
                counters["gave_up"] += 1
                ack_ids.append(rid)               # круг не бесконечен: сдаёмся ГРОМКО, а не молча
                log.error("WA: запись id=%s падает %s-й раз (%s: %s) — подтверждаю и снимаю с круга",
                          rid, tries, type(e).__name__, _safe(e))
            else:
                log.warning("WA: обработка id=%s упала (%s: %s), попытка %s/%s — ack НЕ шлю",
                            rid, type(e).__name__, _safe(e), tries, limit)
            continue
        counters[outcome] = counters.get(outcome, 0) + 1
        ack_ids.append(rid)
    return counters, ack_ids


def poll_once(transport=None, drafter=None, path=None, do_ack=True):
    """Один заход: забор → обработка → ack обработанных. → сводка (dict). Сеть упала → ok=False,
    и вызывающий уходит в бэкофф; ни одна запись при этом не подтверждена."""
    ok, items, err = pull(transport=transport)
    if not ok:
        return {"ok": False, "error": err, "pulled": 0, "acked": 0, "counters": {}, "ack_ids": []}
    counters, ack_ids = process_batch(items, drafter=drafter, path=path)
    acked, ack_err = 0, ""
    if do_ack and ack_ids:
        ok_ack, acked, ack_err = ack(ack_ids, transport=transport)
        if not ok_ack:
            log.warning("WA: ack не прошёл (%s) — записи вернутся по истечении аренды", ack_err)
    if items:
        log.info("WA: забор %s зап. | %s | ack %s", len(items),
                 ", ".join(k + "=" + str(v) for k, v in counters.items() if v), acked)
    return {"ok": True, "error": ack_err, "pulled": len(items), "acked": acked,
            "counters": counters, "ack_ids": ack_ids}


def run_forever(transport=None, drafter=None, path=None, sleep=None, rounds=None):
    """Цикл забора раз в POLL_SEC. Сбой сети — ТИХИЙ ретрай с бэкоффом: первая ошибка подряд в лог,
    последующие того же класса — нет (лог не должен превращаться в ленту недоступности).
    rounds — предел кругов (самотесты); None = вечно."""
    if not is_enabled():
        log.warning("WA: мост не поднят — WA_PULL_SECRET не задан")
        return 0
    _sleep = sleep or time.sleep
    delay, quiet, done = POLL_SEC, False, 0
    while rounds is None or done < rounds:
        res = poll_once(transport=transport, drafter=drafter, path=path)
        if res["ok"]:
            if quiet:
                log.info("WA: связь с очередью восстановлена")
            delay, quiet = POLL_SEC, False
        else:
            if not quiet:                         # первая ошибка подряд — говорим; дальше молчим
                quiet = True
                log.warning("WA: забор недоступен (%s) — тихий ретрай с бэкоффом", res["error"])
            else:
                log.debug("WA: забор всё ещё недоступен (%s)", res["error"])
            delay = min(max(delay, BACKOFF_START) * 2, BACKOFF_MAX)
        done += 1
        if rounds is None or done < rounds:
            _sleep(delay)
    return done


# ─── отправка: ЗАГЛУШКА до онбординга 360dialog ──────────────────────────────────────────

def send_to_wa(number, text):
    """ОТПРАВКА КЛИЕНТУ В WHATSAPP — ЗАГЛУШКА. Двери wa_send нет ни на VPS, ни здесь
    (KB_WA_PLAN §7), поэтому одобренный черновик НИКУДА не уходит и это ВИДНО в логе.
    → (False, причина) — та же форма, что у send_to_client, чтобы вызывающему не гадать."""
    log.warning("%s | номер %s, символов %d", SEND_DISABLED_MSG, number or "?", len(text or ""))
    return False, SEND_DISABLED_MSG


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="WA-мост: ПК из очереди VPS (забор/ack)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="один заход: забор, обработка, ack")
    g.add_argument("--loop", action="store_true", help="цикл раз в WA_POLL_SEC (по умолчанию 20с)")
    g.add_argument("--probe", action="store_true",
                   help="только посмотреть: забор БЕЗ обработки и БЕЗ ack (записи не трогаем)")
    a = ap.parse_args(argv)
    if not is_enabled():
        print("WA_PULL_SECRET не задан — мост не поднят (значение не печатаем)")
        return 2
    if a.probe:
        ok, items, err = pull()
        print("забор:", "ok" if ok else "ОТКАЗ", "| записей:", len(items), "|", err)
        for it in items:
            print("  id=%s kind=%s type=%s echo=%s history=%s номер=%s" %
                  (it.get("id"), kind_of(it), it.get("msg_type"), it.get("echo"),
                   it.get("history"), number_of(it)))
        return 0 if ok else 1
    if a.once:
        res = poll_once()
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1
    run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
