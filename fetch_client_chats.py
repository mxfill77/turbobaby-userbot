"""
fetch_client_chats.py — выгрузка личных переписок с клиентами из Telegram
для построения FAQ/шаблонов (read-only, НИЧЕГО не отправляет).

Запускать с ПК из D:\\turbobaby-bot (там лежит .env с API_ID/API_HASH и
Telethon-сессия userbot @turbophuket — та же, что использует fetch_topics.py).

Что делает:
  - проходит ЛИЧНЫЕ диалоги (не группы, не каналы) за последний год
  - сохраняет: собеседник (имя + @username + id), дата, кто писал, текст
  - пишет в client_chats.jsonl (по строке на сообщение) — остаётся НА ПК

Приватность: имена/контакты сохраняются локально (для связки с таблицей
клиентов). Файл никуда не загружается. Медиа НЕ скачиваются — только текст.

Зависимости: pip install telethon python-dotenv
"""

import os
import json
import asyncio
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import User

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("TELETHON_SESSION_NAME", "turbobaby_session")  # как в fetch_topics.py

# Период: последний год
SINCE = datetime.now(timezone.utc) - timedelta(days=365)

OUT_FILE = "client_chats.jsonl"

# Сколько максимум сообщений на диалог тянуть (защита от гигантских чатов)
MAX_MSGS_PER_DIALOG = 4000

# Имена СВОИХ аккаунтов/менеджеров — чтобы помечать, кто писал (me=компания).
# Клиентские реплики ценнее всего (это их вопросы).
OWN_USERNAMES = {"turbophuket", "turbophuket1"}


async def main():
    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    my_id = me.id
    print(f"Вошёл как @{me.username} (id={my_id}). Тяну личные диалоги за год...")

    total_dialogs = 0
    total_msgs = 0

    with open(OUT_FILE, "w", encoding="utf-8") as out:
        async for dialog in client.iter_dialogs():
            ent = dialog.entity
            # ТОЛЬКО личные диалоги с пользователями (не группы/каналы/боты)
            if not isinstance(ent, User):
                continue
            if ent.bot:
                continue
            # Пропустим свои же аккаунты
            if (ent.username or "").lower() in OWN_USERNAMES:
                continue

            peer_name = " ".join(filter(None, [ent.first_name, ent.last_name])) or "?"
            peer_username = ent.username or ""
            peer_phone = ent.phone or ""

            msgs = []
            try:
                async for m in client.iter_messages(ent, limit=MAX_MSGS_PER_DIALOG):
                    if m.date < SINCE:
                        break  # дальше только старше года
                    if not m.message:  # пропускаем медиа без текста
                        continue
                    who = "company" if (m.sender_id == my_id) else "client"
                    msgs.append({
                        "date": m.date.isoformat(),
                        "who": who,
                        "text": m.message,
                    })
            except Exception as e:
                print(f"  ! пропускаю {peer_name}: {e}")
                continue

            if not msgs:
                continue

            # клиентские сообщения — самое ценное; пропускаем диалоги без них
            if not any(x["who"] == "client" for x in msgs):
                continue

            msgs.reverse()  # хронологический порядок
            record = {
                "peer_name": peer_name,
                "peer_username": peer_username,
                "peer_phone": peer_phone,
                "peer_id": ent.id,
                "messages": msgs,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            total_dialogs += 1
            total_msgs += len(msgs)
            if total_dialogs % 20 == 0:
                print(f"  ...{total_dialogs} диалогов, {total_msgs} сообщений")

    await client.disconnect()
    print(f"\nГотово. {total_dialogs} диалогов, {total_msgs} сообщений → {OUT_FILE}")
    print("Файл лежит на твоём ПК. Дальше: пришли его (или кусок) — соберу FAQ/шаблоны.")


if __name__ == "__main__":
    asyncio.run(main())
