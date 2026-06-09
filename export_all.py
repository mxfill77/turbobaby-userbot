"""
TurboBaby — Массовая выгрузка недостающих Telegram чатов
=========================================================

Что делает:
- Заходит в твой Telegram через готовую сессию (turbobaby_session)
- Перебирает все чаты из списка ниже
- Сохраняет каждый в data_export/<категория>/<имя>.json
- В конце пакует всё в zip-архив

Что НЕ выгружает:
- Медиа (фото, видео, голосовые) — только текст и метаданные
  Если в сообщении было фото — пишет [PHOTO]

Запуск:
    venv\\Scripts\\activate
    python export_all.py
"""

import os
import json
import asyncio
import zipfile
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogFiltersRequest

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')

client = TelegramClient('turbobaby_session', API_ID, API_HASH)

# ============================================
# СПИСОК ЧАТОВ К ВЫГРУЗКЕ
# ============================================

# Личные чаты с командой (по username)
PERSONAL_TO_EXPORT = [
    ('deramor',         'team',     'deramor'),
    ('extthiwxer',      'delivery', 'extthiwxer'),
    ('bur002ger',       'delivery', 'bur002ger_l2fa'),
    ('meta_chats_bot',  'system',   'meta_chats_bot'),
]

# Папки Telegram (по фрагменту названия → выгружаем все чаты этой папки)
FOLDERS_TO_EXPORT = [
    ('SALARY',  'salary'),
    ('Личные',  'agents'),
]

# Большие группы (по фрагменту имени)
GROUPS_TO_EXPORT = [
    ('партнерка stm',        'partners'),
    ('партнерка с михаилом', 'partners'),
    ('turbostm',             'partners'),
]

# ============================================

OUT_DIR = Path('data_export')
OUT_DIR.mkdir(exist_ok=True)


def get_text(msg):
    """Достаёт текст из сообщения"""
    t = msg.text
    if not t:
        if msg.photo: return "[PHOTO]"
        if msg.video: return "[VIDEO]"
        if msg.voice: return "[VOICE]"
        if msg.document: return "[FILE]"
        return ""
    return t


def msg_to_dict(msg):
    """Сериализуем сообщение в простой dict"""
    sender = msg.sender
    return {
        'id': msg.id,
        'date': msg.date.isoformat() if msg.date else None,
        'from_id': str(msg.sender_id) if msg.sender_id else None,
        'from': getattr(sender, 'first_name', None) if sender else None,
        'username': getattr(sender, 'username', None) if sender else None,
        'text': get_text(msg),
        'reply_to': msg.reply_to_msg_id if msg.reply_to_msg_id else None,
    }


async def export_chat(entity, category, slug):
    """Выгружаем один чат целиком"""
    folder = OUT_DIR / category
    folder.mkdir(exist_ok=True)
    out_file = folder / f"{slug}.json"

    if out_file.exists():
        print(f"   ⏭️  {slug} уже есть, пропускаем")
        return

    print(f"   📥 {slug}...", end=' ', flush=True)

    messages = []
    count = 0
    try:
        async for msg in client.iter_messages(entity, limit=None):
            messages.append(msg_to_dict(msg))
            count += 1
            if count % 1000 == 0:
                print(f"{count}…", end=' ', flush=True)
    except Exception as e:
        print(f"⚠️ ошибка: {e}")
        return

    # Имя чата
    try:
        name = getattr(entity, 'title', None) or getattr(entity, 'first_name', None) or slug
    except:
        name = slug

    data = {
        'name': name,
        'id': entity.id if hasattr(entity, 'id') else None,
        'category': category,
        'exported_at': datetime.now().isoformat(),
        'message_count': len(messages),
        'messages': list(reversed(messages)),  # от старых к новым
    }

    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ {count} сообщ.")


async def main():
    await client.start(phone=PHONE)
    print("\n✅ Подключились к Telegram\n")
    print("=" * 70)

    # === 1. ЛИЧНЫЕ ЧАТЫ С КОМАНДОЙ ===
    print("\n👥 ЛИЧНЫЕ ЧАТЫ С КОМАНДОЙ\n")
    for username, category, slug in PERSONAL_TO_EXPORT:
        try:
            entity = await client.get_entity(username)
            await export_chat(entity, category, slug)
        except Exception as e:
            print(f"   ❌ {username}: {e}")

    # === 2. ПАПКИ TELEGRAM ===
    print("\n📁 ПАПКИ TELEGRAM\n")
    folder_result = await client(GetDialogFiltersRequest())
    all_dialogs = await client.get_dialogs()

    for folder in folder_result.filters:
        if not hasattr(folder, 'title'):
            continue
        folder_title = folder.title.text if hasattr(folder.title, 'text') else folder.title

        target_category = None
        for keyword, cat in FOLDERS_TO_EXPORT:
            if keyword.lower() in folder_title.lower():
                target_category = cat
                break
        if not target_category:
            continue

        print(f"\n   📂 Папка '{folder_title}' → '{target_category}'")

        peer_ids = set()
        for peer in folder.include_peers:
            if hasattr(peer, 'user_id'):     peer_ids.add(peer.user_id)
            elif hasattr(peer, 'channel_id'): peer_ids.add(peer.channel_id)
            elif hasattr(peer, 'chat_id'):    peer_ids.add(peer.chat_id)

        for dialog in all_dialogs:
            try:
                eid = dialog.entity.id
                if eid in peer_ids:
                    name = dialog.name or f"chat_{eid}"
                    slug = ''.join(c if c.isalnum() else '_' for c in name)[:40]
                    await export_chat(dialog.entity, target_category, slug)
            except Exception as e:
                print(f"      ⚠️ {dialog.name}: {e}")

    # === 3. БОЛЬШИЕ ГРУППЫ ===
    print("\n👥 ПАРТНЁРСКИЕ ГРУППЫ\n")
    for keyword, category in GROUPS_TO_EXPORT:
        for dialog in all_dialogs:
            if not (dialog.is_group or dialog.is_channel):
                continue
            if keyword.lower() in dialog.name.lower():
                slug = ''.join(c if c.isalnum() else '_' for c in dialog.name)[:40]
                await export_chat(dialog.entity, category, slug)

    # === 4. ZIP ===
    print("\n📦 Упаковываю в zip...")
    zip_path = Path(f'turbobaby_export_{datetime.now().strftime("%Y%m%d_%H%M")}.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file in OUT_DIR.rglob('*.json'):
            zf.write(file, file.relative_to(OUT_DIR.parent))

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"\n✅ ГОТОВО! Файл: {zip_path}  ({size_mb:.1f} МБ)")
    print(f"\n📤 Загрузи этот файл в чат с Claude\n")


with client:
    client.loop.run_until_complete(main())
