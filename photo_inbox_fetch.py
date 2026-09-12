# -*- coding: utf-8 -*-
"""
photo_inbox_fetch.py — РУКИ к `photo_inbox`: снять снимки владельца из названной группы на диск.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ. Вся логика индекса живёт в `photo_inbox` и сети не знает вовсе — поэтому
её регресс гоняется без единого байта наружу. Здесь, и только здесь, живёт Telegram.

ПОЧЕМУ НЕ ВРЕЗКА В userbot_listen. Врезка — правка ЖИВОГО процесса, а он сейчас работает
(userbot_listen.py, замер 12.09 19:4x). Задание 60-b врезки не просит и процессов трогать не
велит: продукт — папка, индекс и живой снимок в ней. Врезка остаётся отдельным решением
владельца и записана в артефакте строкой «чего заход не сделал».

ГЛАВНАЯ ОСТОРОЖНОСТЬ — СЕССИЯ. Файл `turbobaby_session.session` держит ЖИВОЙ userbot и пишет в
него прямо сейчас. Второй клиент поверх того же файла — это (а) спор двух процессов за один
SQLite и (б) риск испортить боевую сессию. Поэтому:

  • живой файл открывается ТОЛЬКО НА ЧТЕНИЕ (`file:…?mode=ro`, sqlite backup API) и снимается
    его КОПИЯ в явно названное место `tmp/photo_inbox_session/`;
  • клиент работает по КОПИИ: ни одной записи в боевой файл не уходит физически;
  • `client.start()` НЕ зовётся НИКОГДА — он спрашивает телефон и код и в headless повис бы.
    Зовётся `connect()` + `is_user_authorized()`; не авторизованы → честный выход, а не вход.

ЧТО БЕРЁМ И ЧЕГО НЕ БЕРЁМ (границы задания, они же в коде):
  • только НАЗВАННЫЕ группы: по умолчанию одна — группа-тренажёр (`trainer.TRAINER_GROUP_NAME`).
    Имя приходит из ТОГО ЖЕ места, откуда его берёт боевой тренажёр, — второго мнения нет;
  • только сообщения ВЛАДЕЛЬЦА: своё (`msg.out`) и ботовское пропускаем. Клиентских диалогов
    скрипт не открывает ни одного — он вообще не умеет ходить никуда, кроме списка названных групп;
  • НАРУЖУ НЕ УХОДИТ НИЧЕГО: `send_message`/`send_file`/`forward` в этом файле не встречаются,
    и это проверяется тестом;
  • ничего не удаляется, включая копию сессии: она остаётся лежать в названном месте.
"""

import os
import re
import sys
import shutil
import sqlite3
import asyncio
import argparse
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import photo_inbox  # noqa: E402

# Явно названное временное место для КОПИИ сессии. Не `tmp/trainer_photos` (там снимки 60-a) и
# не корень: своё имя — чтобы уборщик и человек видели, что это и откуда.
SESSION_COPY_DIR = os.path.join(BASE_DIR, "tmp", "photo_inbox_session")

# Живая сессия — та же, что у `fetch_*.py`/`collect_booking.py`. Имя (не значение секрета!)
# читает код, а не мы.
LIVE_SESSION = os.path.join(BASE_DIR, os.getenv("TELETHON_SESSION_NAME", "turbobaby_session") + ".session")


# ------------------------------- копия сессии ---------------------------------

def copy_session(dst_dir=None):
    """Снять КОПИЮ живой сессии, не тронув оригинал. → путь к копии (без «.session»).

    Дорога первая — sqlite backup через `mode=ro`: согласованный снимок, записи в оригинал
    невозможны физически. Дорога вторая (журнал горячий, ro-открытие отказало) — обычное
    копирование файла. Обе только ЧИТАЮТ. Не вышло ни одной — исключение, и заход честно
    сообщает, что снимка не будет, вместо того чтобы лезть в боевой файл на запись."""
    dst_dir = dst_dir or SESSION_COPY_DIR
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "photo_inbox.session")
    if not os.path.exists(LIVE_SESSION):
        raise RuntimeError("живой сессии нет на месте — копировать нечего")
    try:
        src = sqlite3.connect("file:%s?mode=ro" % LIVE_SESSION.replace("\\", "/"), uri=True)
        try:
            out = sqlite3.connect(dst)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
    except Exception:
        shutil.copy2(LIVE_SESSION, dst)          # оригинал только читается и здесь
    return dst[: -len(".session")]


# ------------------------------- отбор сообщений ------------------------------

def is_image_message(msg):
    """Это сообщение со СНИМКОМ? Фото Telegram — да; файл с картинкой внутри — тоже да
    (владелец может прислать сетку «как файл», и терять её глупо). Всё прочее — нет."""
    if msg is None:
        return False
    if getattr(msg, "photo", None) is not None:
        return True
    doc = getattr(msg, "document", None)
    mime = (getattr(doc, "mime_type", "") or "") if doc is not None else ""
    return mime.startswith("image/")


def sender_label(msg, sender):
    """Кто прислал — словами для человека. Значений токенов и телефонов здесь нет и не будет."""
    if sender is None:
        return "id%s" % (getattr(msg, "sender_id", "?"),)
    parts = [getattr(sender, "first_name", "") or "", getattr(sender, "last_name", "") or ""]
    name = " ".join(p for p in parts if p).strip()
    uname = getattr(sender, "username", "") or ""
    if name and uname:
        return "%s (@%s)" % (name, uname)
    return name or (("@" + uname) if uname else "id%s" % (getattr(msg, "sender_id", "?"),))


def is_owner_message(msg, sender):
    """Сообщение ВЛАДЕЛЬЦА, а не наше и не ботовское.

    `msg.out` — это мы сами (userbot-аккаунт): свои же ответы складывать бессмысленно.
    `sender.bot` — модербот, который в этой группе рисует кнопки. Остаётся живой человек."""
    if getattr(msg, "out", False):
        return False
    if sender is not None and getattr(sender, "bot", False):
        return False
    return True


def _fix_extension(path):
    """Привести расширение к реальной сигнатуре. Telegram отдаёт jpeg, но верить имени нельзя —
    судим по байтам. Переименование только в СВОБОДНОЕ имя: затирать соседа нельзя."""
    kind = photo_inbox.sniff_kind(path)
    if not kind:
        return path
    ext = "jpg" if kind == "jpeg" else kind
    root, cur = os.path.splitext(path)
    if cur.lower().lstrip(".") == ext:
        return path
    new = root + "." + ext
    if os.path.exists(new):
        return path
    try:
        os.rename(path, new)
    except OSError:
        return path
    return new


# ------------------------------- заход ----------------------------------------

def _phuket_day_bounds(day):
    """Границы суток ПО ПХУКЕТУ в UTC. Считать «сутки» по UTC значило бы резать вечер владельца
    пополам: 18:10 местного — это 11:10 UTC того же дня, а 23:30 местного уже следующий день UTC."""
    start_local = datetime.datetime.combine(day, datetime.time(0, 0, 0), tzinfo=photo_inbox.PHUKET)
    return (start_local.astimezone(datetime.timezone.utc),
            (start_local + datetime.timedelta(days=1)).astimezone(datetime.timezone.utc))


async def run(day, titles, limit, dry, index_path=None, inbox_dir=None):
    from telethon import TelegramClient      # импорт внутри: регресс `photo_inbox` его не требует
    from dotenv import load_dotenv
    load_dotenv()                            # значения читает КОД, мы их не видим и не печатаем
    api_id = int(os.getenv("API_ID", "0") or "0")
    api_hash = os.getenv("API_HASH", "") or ""
    if not api_id or not api_hash:
        print("НЕТ ДОСТУПА: API_ID/API_HASH не заданы окружением — Telegram не открыть")
        return 2

    sess = copy_session()
    print("сессия: работаем по КОПИИ %s (живой файл только читался)" % os.path.relpath(sess + ".session", BASE_DIR))

    lo, hi = _phuket_day_bounds(day)
    wanted = {t.strip().casefold() for t in titles if t.strip()}
    saved = failed = skipped = seen = 0

    client = TelegramClient(sess, api_id, api_hash)
    await client.connect()                   # НЕ start(): вход по телефону в headless недопустим
    try:
        if not await client.is_user_authorized():
            print("НЕ АВТОРИЗОВАНЫ по копии сессии — входить не будем. Снимок не снят.")
            return 3
        targets = []
        async for d in client.iter_dialogs():
            if (d.name or "").strip().casefold() in wanted:
                targets.append(d)
        if not targets:
            print("НЕ НАЙДЕНА ни одна названная группа: %s" % ", ".join(sorted(wanted)))
            return 4
        for d in targets:
            print("группа: %s (%s)" % (d.name, d.id))
            async for msg in client.iter_messages(d.entity, limit=limit, offset_date=hi):
                when = getattr(msg, "date", None)
                if when is None or when < lo:
                    break                    # идём от свежих к старым: вышли за сутки — дальше не надо
                if when >= hi:
                    continue
                sender = None
                try:
                    sender = await msg.get_sender()
                except Exception:
                    sender = None
                if not is_owner_message(msg, sender):
                    continue
                seen += 1
                plan = photo_inbox.plan_save(has_photo=is_image_message(msg), chat_id=d.id,
                                             msg_id=msg.id, when=when,
                                             index_path=index_path, inbox_dir=inbox_dir)
                if plan["act"] != "save":
                    skipped += 1
                    print("  msg %s — пропуск: %s" % (msg.id, plan["reason"]))
                    continue
                if dry:
                    print("  msg %s — СУХО, лёг бы в %s" % (msg.id, plan["filename"]))
                    continue
                path, err = "", ""
                try:
                    os.makedirs(os.path.dirname(plan["path"]), exist_ok=True)
                    got = await msg.download_media(file=plan["path"])
                    path = _fix_extension(got) if got else ""
                except Exception as e:
                    err = "%s: %s" % (type(e).__name__, e)
                res = photo_inbox.commit_save(plan, chat=d.name, chat_id=d.id, msg_id=msg.id,
                                              when=when, sender=sender_label(msg, sender),
                                              caption=(getattr(msg, "message", "") or ""),
                                              downloaded_path=path, error=err, index_path=index_path)
                if res["outcome"] == photo_inbox.SAVED:
                    saved += 1
                else:
                    failed += 1
                print("  msg %s — %s | %s | %d б" % (msg.id, res["outcome"], res["filename"], res["bytes"]))
    finally:
        await client.disconnect()
    print("ИТОГ: сообщений владельца %d · СОХРАНЁН %d · НЕ СКАЧАН %d · без строки %d" %
          (seen, saved, failed, skipped))
    return 0


def main(argv=None):
    import trainer                            # имя группы — из того же места, что у боевого тренажёра
    ap = argparse.ArgumentParser(description="Снять снимки владельца из названной группы в docs/vhodyashchie-snimki")
    ap.add_argument("--date", default=datetime.datetime.now(photo_inbox.PHUKET).strftime("%Y-%m-%d"),
                    help="сутки ПО ПХУКЕТУ, ГГГГ-ММ-ДД (по умолчанию сегодня)")
    ap.add_argument("--chat", action="append", default=[],
                    help="название группы; по умолчанию — группа-тренажёр")
    ap.add_argument("--limit", type=int, default=200, help="сколько последних сообщений просмотреть")
    ap.add_argument("--dry", action="store_true", help="ничего не качать, только сказать, что легло бы")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.date):
        print("дата не в формате ГГГГ-ММ-ДД")
        return 2
    day = datetime.date.fromisoformat(a.date)
    titles = a.chat or [trainer.TRAINER_GROUP_NAME]
    return asyncio.run(run(day, titles, a.limit, a.dry))


if __name__ == "__main__":
    sys.exit(main())
