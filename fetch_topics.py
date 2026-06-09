"""
🔍 Сбор всех тем форума обслуживания (topic_id → название = байк).
Запуск (юзербот, из D:\\turbobaby-bot):
    python fetch_topics.py

Собирает названия тем из служебных сообщений о создании тем
(MessageActionTopicCreate) — работает в ЛЮБОЙ версии Telethon.
Результат → topics_map.json:  { "<topic_id>": "название = байк", ... }

Splinter (manager-bot) читает этот файл и знает байк каждой темы.
Перезапускай при добавлении новых тем. Только чтение.
"""

import os
import json
import asyncio

from telethon import TelegramClient
from telethon.tl.types import MessageActionTopicCreate, MessageActionTopicEdit

from dotenv import load_dotenv
load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("SESSION_NAME", "turbobaby_session")

SERVICING_BOT_API_ID = -1002751134848
SERVICING_TELETHON_ID = 2751134848
OUT_FILE = "topics_map.json"


async def get_entity(client):
    for cand in (SERVICING_BOT_API_ID, SERVICING_TELETHON_ID):
        try:
            e = await client.get_entity(cand)
            print(f"  группа найдена по id {cand}")
            return e
        except Exception as ex:
            print(f"  не вышло по id {cand}: {ex}")
    async for d in client.iter_dialogs():
        if getattr(d.entity, "forum", False):
            nm = (d.name or "")
            if "บำรุง" in nm or "servic" in nm.lower() or "обслуж" in nm.lower():
                print(f"  нашёл форум по названию: {d.name}")
                return d.entity
    return None


async def main():
    if not API_ID or not API_HASH:
        print("⚠️ Нужны API_ID и API_HASH в .env"); return

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    print("✅ Юзербот подключён\n")

    entity = await get_entity(client)
    if not entity:
        print("⚠️ Не нашёл группу обслуживания (юзербот в ней состоит?)")
        await client.disconnect(); return
    if not getattr(entity, "forum", False):
        print("⚠️ Группа не форум (нет тем)")
        await client.disconnect(); return

    # Проходим по всей истории, ловим события создания (и переименования) тем.
    topics = {}        # topic_id(str) -> текущее название
    renamed = {}        # topic_id -> последнее новое имя (если тему переименовывали)
    count = 0
    print("  читаю историю группы (сбор тем)…")
    async for msg in client.iter_messages(entity, limit=None):
        count += 1
        act = getattr(msg, "action", None)
        if isinstance(act, MessageActionTopicCreate):
            # id служебного сообщения о создании = topic_id
            topics[str(msg.id)] = act.title
        elif isinstance(act, MessageActionTopicEdit):
            # переименование темы: новое имя в act.title (если задано)
            new_title = getattr(act, "title", None)
            if new_title:
                # topic_id = id темы, к которой относится правка
                tid = getattr(msg, "reply_to", None)
                top_id = None
                if tid is not None:
                    top_id = getattr(tid, "reply_to_top_id", None) or getattr(tid, "reply_to_msg_id", None)
                if top_id:
                    renamed[str(top_id)] = new_title
        if count % 2000 == 0:
            print(f"    …просмотрено {count} сообщений, тем найдено {len(topics)}")

    # применяем переименования поверх исходных названий
    for tid, name in renamed.items():
        topics[tid] = name

    print(f"  готово: просмотрено {count} сообщений, тем найдено {len(topics)}")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Сохранено {len(topics)} тем в {OUT_FILE}\n")
    print("Список тема → байк:")
    for tid, title in sorted(topics.items(), key=lambda x: int(x[0])):
        print(f"  [{tid}] {title}")
    print(f"\nПоложи {OUT_FILE} в manager-bot\\ рядом со splinter.py.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
