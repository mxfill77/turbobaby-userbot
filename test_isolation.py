# -*- coding: utf-8 -*-
"""
test_isolation.py — ГЛОБАЛЬНАЯ тест-обвязка. Импортируется ПЕРВОЙ строкой каждого теста,
который трогает suggest/moderation (`import test_isolation` до `import suggest`).

Зачем: в среде демона окружение содержит боевые MODERBOT_TOKEN/MOD_GROUP_ID, а рядом крутится
живой модербот с горячим moderation_ipc.db. Без изоляции on_client_message видит
bot_mode_active=True и enqueue'ит ФИКСТУРНЫЕ черновики в БОЕВУЮ очередь — живой бот постит их в
реальную группу Модерации (инцидент 16:39: «@client1» улетел наружу).

Что делает — «наружу недоступно ПО ПОСТРОЕНИЮ»:
  1) TESTING=1 в окружении → suggest/moderation_ipc переключаются в тест-режим;
  2) moderation_ipc.DB_PATH → одноразовый temp; боевой moderation_ipc.db заблокирован тривайром
     в _conn() (любая попытка открыть его = RuntimeError + счётчик prod_ipc_open_attempts);
  3) боевой MODERBOT_TOKEN стёрт → bot_mode_active по умолчанию False (reply-режим, без IPC);
  4) счётчик наружу outbound_prod_attempts() = moderation_ipc.prod_ipc_open_attempts (норма: 0).

Импорт идемпотентен (повторный `import` не сбрасывает состояние).
"""

import os

# 1) Взводим TESTING ДО импорта suggest/moderation_ipc, чтобы флаги считались при импорте.
os.environ["TESTING"] = "1"

import suggest            # noqa: E402
import moderation_ipc     # noqa: E402

# 2) На случай, если модули уже были импортированы кем-то раньше (флаг прочитан как False) —
#    доводим их до тест-режима принудительно.
suggest.TESTING = True
moderation_ipc.TESTING = True

# Боевой путь недоступен: уводим дефолт в temp (тесты всё равно ставят свой tmp; это — сеть
# безопасности для тех, кто забыл переопределить DB_PATH). Боевой moderation_ipc.db + тривайр.
import tempfile          # noqa: E402
_ISO_DB = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_ipc.db")
moderation_ipc.DB_PATH = _ISO_DB
try:
    if os.path.exists(_ISO_DB):
        os.remove(_ISO_DB)            # чистим stale heartbeat от прошлых прогонов
except OSError:
    pass
moderation_ipc.init_db()             # пустая схема в изоляции → is_bot_alive() = False (чисто)

# 3) Нормализуем БОЕВОЕ окружение демона к чистым дефолтам теста. В среде демона в окружении
#    выставлены SUGGEST_MODE=1, SUGGEST_TEST_MODE=1, непустой APPROVER_USERNAMES и BRIDGE_URL/
#    TOKEN — эти значения просочились в глобали suggest при импорте и ломают тесты (написаны под
#    чистое окружение) + открывают сеть (load_faq → боевой Bridge). Возвращаем как «из коробки»:
#    тесты, которым нужно иное, ставят это сами (и восстанавливают в tearDown).
suggest.MODERBOT_TOKEN = ""          # боевой токен недоступен по построению → bot_mode_active=False
suggest.SUGGEST_MODE = False         # главный рубильник OFF (тесты включают точечно)
suggest.SUGGEST_TEST_MODE = False    # чистый дефолт (тесты TEST_MODE ставят True сами)
suggest.APPROVER_USERNAMES = set()   # пустой whitelist = любой approver (как в чистом env)
suggest.MOD_GROUP_ID = None
suggest.MOD_GROUP_NAME = ""
suggest.BRIDGE_URL = ""              # сеть недоступна: load_faq падает на локальный файл, не в Bridge
suggest.BRIDGE_TOKEN = ""
suggest.reset_disabled()

# 4) Обучающие журналы — в temp, чтобы тесты не писали в РЕПО-файлы suggest_pairs/pending.
suggest.PAIRS_FILE = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_pairs.jsonl")
suggest.PENDING_FILE = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_pending.jsonl")
try:
    for _p in (suggest.PAIRS_FILE, suggest.PENDING_FILE):
        if os.path.exists(_p):
            os.remove(_p)
except OSError:
    pass
suggest.pending = suggest.PendingStore(suggest.PENDING_FILE)


def outbound_prod_attempts():
    """Мок-счётчик «наружу»: сколько раз за прогон пытались открыть БОЕВОЙ IPC. Норма — 0.
    Единственный канал, которым фикстура может достичь реальной группы, — боевая очередь;
    ноль обращений к ней = ноль сообщений наружу."""
    return moderation_ipc.prod_ipc_open_attempts
