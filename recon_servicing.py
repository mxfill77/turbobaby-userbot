"""
🔍 Разведка структуры операционных групп (форум-темы определяются БЕЗ спец-API —
работает на любой версии Telethon).

Запуск (юзербот, из D:\turbobaby-bot):
    python recon_servicing.py

Только чтение — ничего не отправляет и не меняет.
По каждой группе: темы (по reply_to), сэмплы сообщений (📷 = фото),
+ сохраняет несколько фото-образцов в ./recon_photos.
"""

import os
import asyncio
from collections import defaultdict

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageActionTopicCreate

from dotenv import load_dotenv
load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("SESSION_NAME", "turbobaby_session")

TARGETS = {
    -1002751134848: "Обслуживание",
    -1002445921469: "Delivery",
    -1003966216195: "Отметка",
    -4969266437:     "MoneyCashflow",
    -4827606746:     "Самоорганизация",
}

SCAN_LIMIT = 250
SAMPLES_PER_TOPIC = 6
MAX_PHOTOS = 8
PHOTO_DIR = "recon_photos"


def short(text, n=110):
    if not text:
        return ""
    return text.replace("\n", " ⏎ ")[:n] + ("…" if len(text) > n else "")


def topic_of(msg):
    rt = getattr(msg, "reply_to", None)
    if rt and getattr(rt, "forum_topic", False):
        return getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)
    return None


async def dump_chat(client, chat_id, name, photo_budget):
    print("\n" + "=" * 70)
    print(f"📂 {name}  ({chat_id})")
    print("=" * 70)

    try:
        entity = await client.get_entity(chat_id)
    except Exception as e:
        print(f"  ⚠️ entity error: {e}")
        return photo_budget

    print(f"  forum (темы): {getattr(entity, 'forum', False)}")

    topic_titles = {}
    buckets = defaultdict(list)
    msgs = []

    async for msg in client.iter_messages(entity, limit=SCAN_LIMIT):
        if isinstance(getattr(msg, "action", None), MessageActionTopicCreate):
            topic_titles[msg.id] = msg.action.title
            continue
        msgs.append(msg)

    for msg in msgs:
        try:
            s = await msg.get_sender()
            sender = getattr(s, "username", None) or getattr(s, "first_name", "") or "?"
        except Exception:
            sender = "?"
        has_photo = isinstance(msg.media, MessageMediaPhoto)
        tid = topic_of(msg) or 0
        buckets[tid].append((sender, msg.message or "", has_photo, msg))

    print(f"  тем обнаружено: {len([t for t in buckets if t])} (+General)")
    print(f"  всего сообщений в выборке: {len(msgs)}")

    for tid in sorted(buckets.keys()):
        title = topic_titles.get(tid, "General" if tid == 0 else f"тема {tid}")
        items = buckets[tid]
        photos = sum(1 for _, _, p, _ in items if p)
        print(f"\n  --- [{tid}] {title}  ({len(items)} сообщ., {photos} с фото) ---")
        for sender, text, has_photo, msg in items[:SAMPLES_PER_TOPIC]:
            flag = "📷" if has_photo else "  "
            print(f"    {flag} @{sender}: {short(text)}")
            if has_photo and photo_budget > 0:
                os.makedirs(PHOTO_DIR, exist_ok=True)
                path = os.path.join(PHOTO_DIR, f"{name}_{tid}_{msg.id}.jpg")
                try:
                    await msg.download_media(file=path)
                    print(f"        💾 {path}")
                    photo_budget -= 1
                except Exception as e:
                    print(f"        ⚠️ фото: {e}")

    return photo_budget


async def main():
    if not API_ID or not API_HASH:
        print("⚠️ Нужны API_ID и API_HASH в .env")
        return

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    print("✅ Юзербот подключён, разведка (только чтение)\n")

    budget = MAX_PHOTOS
    for chat_id, name in TARGETS.items():
        try:
            budget = await dump_chat(client, chat_id, name, budget)
        except Exception as e:
            print(f"  ⚠️ ошибка по {name}: {e}")

    print("\n" + "=" * 70)
    print(f"Готово. Фото-образцы в ./{PHOTO_DIR}. Скинь вывод — настрою Splinter под темы.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
