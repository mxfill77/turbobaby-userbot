import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient

# Загружаем настройки из .env
load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')

# Создаём клиента (session-файл сохранит вход, чтобы каждый раз не вводить код)
client = TelegramClient('turbobaby_session', API_ID, API_HASH)


async def main():
    # Подключаемся к Telegram
    await client.start(phone=PHONE)
    print("\n✅ Успешно подключились к Telegram!\n")

    # Получаем информацию о себе
    me = await client.get_me()
    print(f"👤 Аккаунт: {me.first_name} {me.last_name or ''}")
    print(f"📱 Username: @{me.username or 'нет'}")
    print(f"🆔 ID: {me.id}\n")

    # Считаем все диалоги
    print("📊 Считаем чаты...")
    dialogs = await client.get_dialogs()

    total = len(dialogs)
    personal = sum(1 for d in dialogs if d.is_user)
    groups = sum(1 for d in dialogs if d.is_group)
    channels = sum(1 for d in dialogs if d.is_channel and not d.is_group)

    print(f"\n📈 Статистика твоего аккаунта:")
    print(f"   Всего диалогов: {total}")
    print(f"   👤 Личных чатов: {personal}")
    print(f"   👥 Групп: {groups}")
    print(f"   📢 Каналов: {channels}\n")

    # Показываем первые 20 чатов для проверки
    print("📋 Первые 20 чатов (по последней активности):\n")
    for i, dialog in enumerate(dialogs[:20], 1):
        chat_type = "👤" if dialog.is_user else ("👥" if dialog.is_group else "📢")
        unread = f" [{dialog.unread_count} непрочитано]" if dialog.unread_count else ""
        print(f"   {i:2}. {chat_type} {dialog.name}{unread}")

    print("\n✨ Подключение работает! Можно идти к следующему шагу.\n")


# Запускаем
with client:
    client.loop.run_until_complete(main())