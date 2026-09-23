# -*- coding: utf-8 -*-
"""
chatlog_servicing_fetch.py — РУКИ к `chatlog_servicing`: прочитать свежее окно форума Обслуживания
живым юзерботом и положить его в архив переписки вместе со снимками (23.09.2026).

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Вся логика — окно, темы, три исхода снимка, дедуп — живёт в
`chatlog_servicing` и сети не знает. Здесь, и только здесь, живёт Telegram (образец пары
`photo_inbox` / `photo_inbox_fetch`).

ТОЛЬКО ЧТЕНИЕ. В этом файле нет ни одной отправки, пересылки, правки и удаления — проверяется
тестом разбором AST (`test_chatlog_servicing.TestNothingGoesOut`). Splinter и клиентского бота
заход не трогает: он вообще не умеет ходить никуда, кроме одного названного форума.

СЕССИЯ — ПО КОПИИ, как у `photo_inbox_fetch`, и ЕГО ЖЕ функцией (`copy_session`): живой файл
держит работающий userbot, второй клиент поверх него — спор двух процессов за один SQLite.
Живой файл только читается, копия ложится в явно названное `tmp/chatlog_servicing_session/`
и там остаётся (уборки за собой нет). `client.start()` не зовётся никогда: в headless он спросил
бы телефон и повис. Не авторизованы по копии — честный выход кодом 3, сессия не пересоздаётся.

НАРУЖУ — ТОЛЬКО ЧИСЛА. stdout захода уходит в отчёт очереди; ни текста сообщений, ни подписей,
ни имён в нём нет ни одной строкой.

Запуск:
  venv\\Scripts\\python.exe chatlog_servicing_fetch.py --dry    — прочитать окно, ничего не писать
  venv\\Scripts\\python.exe chatlog_servicing_fetch.py          — захват + снимки
"""

import os
import sys
import json
import asyncio
import argparse
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import chatlog_servicing as servicing  # noqa: E402
import photo_inbox_fetch               # noqa: E402  (копия сессии и выправка расширения — ОДНИ на репо)

# Явно названное временное место для КОПИИ сессии. Своё, а не `tmp/photo_inbox_session/`: два
# захода разных заданий не должны снимать копию поверх копии друг друга.
SESSION_COPY_DIR = os.path.join(BASE_DIR, "tmp", "chatlog_servicing_session")
# Объект операции — КОПИЯ, и она названа полным именем (имя файла внутри задаёт
# `photo_inbox_fetch.copy_session`): карточка гарда обязана называть копию, а не оригинал.
SESSION_COPY_FILE = os.path.join(SESSION_COPY_DIR, "photo_inbox.session")

# Потолок всего захода. Выше него — честный выход без записи архива; снимки, успевшие лечь,
# следующий прогон подберёт с диска, а не скачает второй раз (`servicing.existing_photo`).
RUN_TIMEOUT_S = 1500


# ─────────────────────────── сообщение → факт ───────────────────────────

_HINTS = (("gif", "gif"), ("video_note", "video_note"), ("video", "video"), ("voice", "voice"),
          ("audio", "audio"), ("poll", "poll"), ("venue", "geo"), ("geo", "geo"),
          ("contact", "contact"), ("dice", "dice"), ("game", "game"), ("invoice", "invoice"))


def media_kind(m):
    """Вид вложения сообщения Telethon → вид по `servicing.classify_media`.

    Предпросмотр ссылки (`MessageMediaWebPage`) вложением НЕ считается, хотя `m.photo` у него
    бывает НЕ пустым (Telethon отдаёт картинку превью как фото): иначе каждая ссылка в теме стала
    бы «снимком». `MessageMediaPhoto` без самого фото (снимок с таймером, истёк) — снимок, и его
    честный исход НЕ СКАЧАН, а не «вложения не было»."""
    media = getattr(m, "media", None)
    if media is None:
        return servicing.classify_media(False)
    cls = type(media).__name__
    if cls == "MessageMediaWebPage":
        return servicing.classify_media(False)
    has_photo = cls == "MessageMediaPhoto" or getattr(m, "photo", None) is not None
    doc = getattr(m, "document", None)
    mime = getattr(doc, "mime_type", None) if doc is not None else None
    hint = None
    for attr, word in _HINTS:
        if getattr(m, attr, None) is not None:
            hint = word
            break
    if hint is None and doc is not None:
        hint = "document"
    if hint is None:
        hint = cls
    return servicing.classify_media(True, has_photo=has_photo, mime=mime,
                                    is_sticker=getattr(m, "sticker", None) is not None,
                                    kind_hint=hint)


def to_fact(m):
    """Сообщение Telethon → простой факт для `servicing.capture`. Имени, ника и телефона автора в
    факте нет: только `str(sender_id)` — сырьё псевдонима, та же форма, что у `export_all.py`."""
    rt = getattr(m, "reply_to", None)
    action = getattr(m, "action", None)
    sender = getattr(m, "sender", None)
    sid = getattr(m, "sender_id", None)
    return {
        "mid": m.id,
        "when": getattr(m, "date", None),
        "sender_raw": (str(sid) if sid is not None else None),
        "bot": (getattr(sender, "bot", None) if sender is not None else None),
        "out": bool(getattr(m, "out", False)),
        "text": getattr(m, "message", None),
        "forum_topic": bool(getattr(rt, "forum_topic", False)) if rt is not None else False,
        "top_id": getattr(rt, "reply_to_top_id", None) if rt is not None else None,
        "reply_msg_id": getattr(rt, "reply_to_msg_id", None) if rt is not None else None,
        "action": (type(action).__name__ if action is not None else None),
        "action_title": getattr(action, "title", None) if action is not None else None,
        "media": media_kind(m) if action is None else None,
        "album": getattr(m, "grouped_id", None),
    }


# ─────────────────────────── заход ───────────────────────────

async def _find_forum(client):
    """Форум по номеру; сущности нет в кеше копии — ищем среди своих диалогов (только чтение)."""
    try:
        return await client.get_entity(servicing.CHAT_ID)
    except (ValueError, TypeError) as e:
        print("сущность по номеру не найдена (%s) — ищу среди диалогов" % type(e).__name__)
    async for d in client.iter_dialogs():
        if d.id == servicing.CHAT_ID:
            return d.entity
    return None


async def run(days, limit, dry, budget_s):
    from telethon import TelegramClient      # импорт внутри: регресс логики его не требует
    from dotenv import load_dotenv
    load_dotenv()                            # значения читает КОД, мы их не видим и не печатаем
    api_id = int(os.getenv("API_ID", "0") or "0")
    api_hash = os.getenv("API_HASH", "")
    if not api_id or not api_hash:
        print("НЕТ ДОСТУПА: API_ID/API_HASH не заданы окружением — Telegram не открыть")
        return 2, None

    sess = photo_inbox_fetch.copy_session(SESSION_COPY_DIR)
    print("сессия: работаем по КОПИИ %s (живой файл только читался)"
          % os.path.relpath(SESSION_COPY_FILE, BASE_DIR))

    topics_map, topics_err = servicing.load_topics()
    client = TelegramClient(sess, api_id, api_hash)
    await client.connect()                   # НЕ start(): вход по телефону в headless недопустим
    try:
        if not await client.is_user_authorized():
            print("НЕ АВТОРИЗОВАНЫ по копии сессии — входить не будем. Захвата нет.")
            return 3, None
        entity = await _find_forum(client)
        if entity is None:
            print("ФОРУМ НЕ НАЙДЕН: %d среди диалогов юзербота нет" % servicing.CHAT_ID)
            return 4, None
        now = datetime.datetime.now(datetime.timezone.utc)
        msgs, read, stop = [], 0, None
        async for m in client.iter_messages(entity, limit=limit):
            read += 1
            if not servicing.in_window(getattr(m, "date", None), now, days):
                stop = "days"                # вышли за свежие сутки — дальше не читаем
                break
            msgs.append(m)
        if stop is None:
            stop = "limit" if read >= limit else "history_end"
        by_id = {m.id: m for m in msgs}

        async def download(fact, path):
            got = await by_id[fact["mid"]].download_media(file=path)
            if not got:
                return None
            return photo_inbox_fetch._fix_extension(got)

        facts = [to_fact(m) for m in msgs]
        res = await servicing.capture(facts, download, topics_map=topics_map, budget_s=budget_s,
                                      dry=dry)
        dates = [f["when"] for f in facts if f.get("when") is not None]
        res["window"] = {"days": days, "limit": limit, "read": read, "taken": len(msgs),
                         "stopped_by": stop,
                         "oldest_utc": (min(dates).isoformat() if dates else None),
                         "newest_utc": (max(dates).isoformat() if dates else None)}
        res["forum"] = bool(getattr(entity, "forum", False))
        res["topics_map"] = ({"ok": True, "topics": len(topics_map)} if topics_map is not None
                             else {"ok": False, "why": topics_err})
        return 0, res
    finally:
        await client.disconnect()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Свежее окно форума Обслуживания → chatlog/ + снимки в docs/obsluzhivanie-snimki")
    ap.add_argument("--days", type=int, default=servicing.WINDOW_DAYS)
    ap.add_argument("--limit", type=int, default=servicing.WINDOW_LIMIT)
    ap.add_argument("--budget", type=int, default=servicing.BUDGET_S,
                    help="бюджет времени загрузок снимков, с")
    ap.add_argument("--dry", action="store_true", help="прочитать окно, ничего не писать и не качать")
    a = ap.parse_args(argv)
    try:
        code, res = asyncio.run(asyncio.wait_for(run(a.days, a.limit, a.dry, a.budget),
                                                 RUN_TIMEOUT_S))
    except asyncio.TimeoutError:
        print("ПОТОЛОК ЗАХОДА %d с — архив этим заходом не записан" % RUN_TIMEOUT_S)
        return 5
    if res is not None:
        print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
