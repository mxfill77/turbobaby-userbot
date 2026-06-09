import os
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')

client = TelegramClient('turbobaby_session', API_ID, API_HASH)


def is_thai_text(text):
    """Проверяет есть ли тайские символы в строке"""
    if not text:
        return False
    return any('\u0E00' <= c <= '\u0E7F' for c in text)


async def main():
    await client.start(phone=PHONE)
    print("\n✅ Подключились к Telegram\n")
    print("=" * 75)

    dialogs = await client.get_dialogs()

    # ===== ЧАСТЬ 1: ПАПКИ =====
    print("\n📁 ТВОИ ПАПКИ В TELEGRAM:\n")

    result = await client(GetDialogFiltersRequest())
    salary_folder_chats = []

    for folder in result.filters:
        if hasattr(folder, 'title'):
            title = folder.title.text if hasattr(folder.title, 'text') else folder.title
            folder_id = folder.id
            included = folder.include_peers if hasattr(folder, 'include_peers') else []
            print(f"   📂 [{folder_id}] {title}  →  чатов: {len(included)}")

            # Если папка похожа на Salary — сохраняем её содержимое
            if 'salary' in title.lower() or 'зарплат' in title.lower() or 'salar' in title.lower():
                print(f"      ⭐ ВАЖНАЯ ПАПКА — посмотрим что внутри:")
                for peer in included:
                    peer_id = None
                    if hasattr(peer, 'user_id'):
                        peer_id = peer.user_id
                    elif hasattr(peer, 'channel_id'):
                        peer_id = peer.channel_id
                    elif hasattr(peer, 'chat_id'):
                        peer_id = peer.chat_id
                    salary_folder_chats.append(peer_id)

                # Найдём имена этих чатов
                for d in dialogs:
                    if d.id in salary_folder_chats or (hasattr(d.entity, 'id') and d.entity.id in salary_folder_chats):
                        print(f"         └─ {d.name}")

    print()
    print("=" * 75)

    # ===== ЧАСТЬ 2: @PLEUMMMM — главный менеджер =====
    print("\n⭐ ГЛАВНЫЙ ОФИС-МЕНЕДЖЕР @Pleummmm:\n")

    try:
        pleum = await client.get_entity('Pleummmm')
        last_msg = await client.get_messages(pleum, limit=1)
        approx_count = last_msg[0].id if last_msg else 0
        # Последнее сообщение когда
        last_date = last_msg[0].date.strftime('%Y-%m-%d') if last_msg else '?'
        print(f"   ✅ {pleum.first_name} {pleum.last_name or ''}")
        print(f"   📊 ~{approx_count} сообщений")
        print(f"   📅 Последнее: {last_date}")
        print(f"   🆔 ID: {pleum.id}")
    except Exception as e:
        print(f"   ⚠️  Не нашёл @Pleummmm: {e}")

    print()
    print("=" * 75)

    # ===== ЧАСТЬ 3: РАБОЧИЕ ГРУППЫ =====
    print("\n👥 ТВОИ РАБОЧИЕ ГРУППЫ (топ-7):\n")

    work_groups_keywords = [
        'turbostm', 'turtbostm', 'самоорганизация', 'การจัดการเอง',
        'партнерка stm', 'delivery cooperation', 'การบำรุงรักษา',
        'digital shop', 'михаил'
    ]

    work_groups_found = []
    for dialog in dialogs:
        if not (dialog.is_group or dialog.is_channel):
            continue
        name_lower = dialog.name.lower()
        if any(kw in name_lower for kw in work_groups_keywords):
            work_groups_found.append(dialog)

    for dialog in work_groups_found:
        try:
            last_msg = await client.get_messages(dialog.entity, limit=1)
            approx_count = last_msg[0].id if last_msg else 0
            last_date = last_msg[0].date.strftime('%Y-%m-%d') if last_msg else '?'
            print(f"   👥 {dialog.name}")
            print(f"      └─ ~{approx_count} сообщений, последнее: {last_date}")
        except Exception as e:
            print(f"   ⚠️  {dialog.name}: {e}")
        print()

    print("=" * 75)

    # ===== ЧАСТЬ 4: АКТИВНЫЕ УЧАСТНИКИ DELIVERY COOPERATION =====
    print("\n🛵 АКТИВНЫЕ УЧАСТНИКИ Delivery cooperation (последние 45 дней):\n")

    delivery_chat = None
    for d in dialogs:
        if 'delivery cooperation' in d.name.lower():
            delivery_chat = d
            break

    if delivery_chat:
        cutoff = datetime.now(timezone.utc) - timedelta(days=45)
        active_users = {}  # user_id -> {'name': ..., 'count': ..., 'has_thai': ...}

        print("   ⏳ Считаю сообщения за последние 45 дней...")
        async for msg in client.iter_messages(delivery_chat.entity, offset_date=None):
            if msg.date < cutoff:
                break
            if not msg.sender_id:
                continue

            uid = msg.sender_id
            if uid not in active_users:
                sender = await msg.get_sender()
                if sender:
                    name = f"{sender.first_name or ''} {sender.last_name or ''}".strip()
                    username = sender.username or ''
                    active_users[uid] = {
                        'name': name,
                        'username': username,
                        'count': 0,
                        'has_thai_name': is_thai_text(name)
                    }
                else:
                    continue

            active_users[uid]['count'] += 1

        # Сортируем по активности
        sorted_users = sorted(active_users.items(), key=lambda x: x[1]['count'], reverse=True)

        print(f"\n   Всего активных за 45 дней: {len(sorted_users)}\n")
        for uid, info in sorted_users[:20]:
            tag = "🇹🇭" if info['has_thai_name'] else "🇷🇺"
            uname = f"@{info['username']}" if info['username'] else "(нет username)"
            print(f"   {tag}  {info['name'][:35]:35} {uname:25} {info['count']:4} сообщ.")
    else:
        print("   ⚠️  Delivery cooperation не найдена")

    print()
    print("=" * 75)

    # ===== ЧАСТЬ 5: АКТИВНЫЕ ЛИЧНЫЕ ЧАТЫ за последние 3 месяца =====
    print("\n👤 АКТИВНЫЕ ЛИЧНЫЕ ЧАТЫ (последние 3 мес, топ-40):\n")

    cutoff_personal = datetime.now(timezone.utc) - timedelta(days=90)
    active_personal = []

    print("   ⏳ Фильтрую активные диалоги...")
    for dialog in dialogs:
        if not dialog.is_user:
            continue
        try:
            last_msg = await client.get_messages(dialog.entity, limit=1)
            if not last_msg:
                continue
            if last_msg[0].date < cutoff_personal:
                continue
            # Сохраняем
            entity = dialog.entity
            name = f"{entity.first_name or ''} {entity.last_name or ''}".strip() or dialog.name
            username = entity.username or ''
            active_personal.append({
                'name': name,
                'username': username,
                'msg_count': last_msg[0].id,
                'last_date': last_msg[0].date,
                'has_thai': is_thai_text(name) or is_thai_text(username)
            })
        except:
            continue

    # Сортируем по количеству сообщений
    active_personal.sort(key=lambda x: x['msg_count'], reverse=True)

    print(f"\n   Активных за 3 месяца: {len(active_personal)}")
    print(f"   Топ-40:\n")
    for i, chat in enumerate(active_personal[:40], 1):
        tag = "🇹🇭" if chat['has_thai'] else "🇷🇺"
        uname = f"@{chat['username']}" if chat['username'] else "(нет)"
        date = chat['last_date'].strftime('%m-%d')
        print(f"   {i:2}. {tag}  {chat['name'][:32]:32} {uname:22} ~{chat['msg_count']:5} сообщ. (посл.{date})")

    print()
    print("=" * 75)
    print("\n✨ Разведка завершена!\n")
    print("📋 Что мне сейчас покажи:")
    print("   1. Папка Salary — нашлась? Какие чаты в ней?")
    print("   2. @Pleummmm — сколько сообщений?")
    print("   3. Размер рабочих групп")
    print("   4. Тайцы из Delivery — кто сейчас активен")
    print("   5. Личные чаты — узнаёшь сотрудников/клиентов в топе?\n")


with client:
    client.loop.run_until_complete(main())