import os, asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE = os.getenv('PHONE')

client = TelegramClient('turbobaby_session', API_ID, API_HASH)

# Целевые операционные/учётные группы (по ключевым словам в названии)
TARGETS = [
    'money cashflow', 'cashflow', 'расч', 'выплат',
    'самоорганизац', 'การจัดการเอง',
    'บำรุงรักษา', 'обслуживание', 'сервис', 'ремонт',
    'delivery cooperation',
    'salary',
]

def short(t, n=120):
    t = (t or '').replace('\n', ' ').strip()
    return t[:n] + ('…' if len(t) > n else '')

async def main():
    await client.start(phone=PHONE)
    print("\n✅ Подключились\n" + "="*70)
    dialogs = await client.get_dialogs()

    for d in dialogs:
        if not (d.is_group or d.is_channel):
            continue
        name_l = d.name.lower()
        if not any(k in name_l for k in TARGETS):
            continue

        print(f"\n📂 {d.name}")
        print(f"   chat_id = {d.id}")
        try:
            msgs = await client.get_messages(d.entity, limit=15)
            print(f"   последних сообщений: {len(msgs)}")
            for m in reversed(msgs):
                who = ''
                try:
                    s = await m.get_sender()
                    who = (getattr(s, 'first_name', '') or '') + (f"@{s.username}" if getattr(s, 'username', None) else '')
                except: pass
                date = m.date.strftime('%m-%d %H:%M') if m.date else '?'
                if m.text:
                    print(f"     [{date}] {who[:20]:20} | {short(m.text)}")
                elif m.photo:
                    print(f"     [{date}] {who[:20]:20} | [ФОТО] {short(m.message)}")
                elif m.document:
                    print(f"     [{date}] {who[:20]:20} | [ФАЙЛ]")
        except Exception as e:
            print(f"   ⚠️ {e}")
        print("   " + "-"*60)

    print("\n✨ Готово\n")

with client:
    client.loop.run_until_complete(main())