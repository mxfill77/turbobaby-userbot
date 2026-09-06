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
# Рубильники БОЕВОГО .env не текут в тесты (деплой 334: LESSON_LLM_ROUTE=1 в .env ронял
# «default = keyword»-тесты и гнал РЕАЛЬНЫЙ думатель прямо из гейта — 350с и краснота).
# Ставим "0" ДО импортов: load_dotenv(override=False) в модулях его не перепишет; тесты
# LLM-пути включают рубильник сами (локально) и инжектят think-фейк.
os.environ["LESSON_LLM_ROUTE"] = "0"

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
suggest.INTAKE_APPROVERS = set()     # пусто → is_intake_approver фолбэкает на approver-whitelist
# Реестр команды из team_registry.json не течёт в общие тесты (сообщение от @Pleummmm иначе бы
# молча выпадало из конвейера в тестах, написанных под чистое окружение). Тесты блока команды
# ставят свой реестр сами (и восстанавливают в tearDown).
suggest.TEAM_REGISTRY = {"usernames": set(), "user_ids": set(), "group_ids": set()}
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

# 4б) БОЕВАЯ БАЗА УРОКОВ — НЕДОСТУПНА ТЕСТАМ ПО ПОСТРОЕНИЮ (06.09.2026). С подключением чтения
# `suggest.load_playbook` берёт секцию «Выученные правила» из `lesson_store.tsv` — БОЕВОГО файла
# состояния полосы, лежащего в корне репо. Без этой строки любой тест, зовущий load_playbook,
# начал бы зависеть от того, сколько уроков сегодня лежит на ЭТОЙ машине: тест зеленел бы или
# краснел от чужой записи, а не от своего предмета. Уводим путь на несуществующий файл —
# `lesson_store.load` называет это `exists=False`, чтение отдаёт третий исход, и load_playbook
# возвращает КНИГУ ровно как до 06.09. Тесты самого чтения ставят свою временную таблицу сами.
suggest.LESSON_BASE_PATH = os.path.join(tempfile.gettempdir(), "turbobaby_TESTING_no_lesson_store.tsv")


def outbound_prod_attempts():
    """Мок-счётчик «наружу»: сколько раз за прогон пытались открыть БОЕВОЙ IPC. Норма — 0.
    Единственный канал, которым фикстура может достичь реальной группы, — боевая очередь;
    ноль обращений к ней = ноль сообщений наружу."""
    return moderation_ipc.prod_ipc_open_attempts


# ─────────────────── 5) ДВЕРЬ СТОРОЖА СВЕЖЕСТИ — ОФЛАЙН (21.08.2026) ────────────────────
# ЗАЧЕМ. Путь ответа спрашивает сторожа свежести БЕЗУСЛОВНО: `suggest._safe_quote_for_model`
# зовёт `price_gate.allow()` без инъекции, и та одалживает у демона живой клиент моста
# (`price_gate._bridge_caller`) — девять GET `quote_price` по 40–43с в норме и до 180с по
# бюджету (`price_gate.PROBE_DEFAULT`). Ни один голден цены этой двери не подменял, поэтому
# КАЖДЫЙ тест, чьи hints несут модель+даты, ходил в ЖИВОЙ мост и краснел по его скорости, а не
# по своему предмету: три прогона одной командой без единой правки дали 16, 62 и 61 красный
# (`docs/artifacts/2026-08-21-remaining-reds.md` §0). Кэш вердикта (TTL 30 мин) платил эту цену
# не раз за прогон: `price_gate.reset()` в соседних наборах взводит её заново.
#
# ЧТО ИМЕННО ПОДМЕНЕНО — РОВНО ДВЕРЬ, А НЕ ВЕРДИКТ. Сторож работает ЦЕЛИКОМ: читает настоящий
# `price_source.json`, обходит те же девять проб `price_freshness_run.PROBES`, судит
# `price_freshness.judge`. Подменён единственный шаг, которого нет без сети, — ОТВЕТ ЛИСТА.
# Это тот же приём, которым владелец мерил бюджет проб 20.08 («подменялась ровно одна вещь —
# сама дверь источника»), и та же форма, что у `test_price_gate.py:158`.
#
# ПОЧЕМУ НЕ ОБЪЯВЛЕННЫЙ ОТКАТ `PRICE_GATE_TTL_MIN=0` (образец `test_price_source.py:72`). Он
# дешевле, но отдаёт `(True, None)` НЕ СПРАШИВАЯ сторожа — то есть заглушка МОЛЧА возвращает
# успех, и ветка гейта в пути ответа перестаёт исполняться вовсе. Соседний набор вправе так
# делать: его предмет — счёт по файлу. Общая обвязка — не вправе.
#
# ЗАГЛУШКА НЕ ОБЯЗАНА ВЕРНУТЬ «ДА» И НЕ ВСЕГДА ЕГО ВОЗВРАЩАЕТ:
#   • правило не прочитано / без слепка ручек → дверь БРОСАЕТ, `live_handles` зовёт это отказом
#     двери, вердикт `НЕИЗВЕСТНО`, цена НЕ называется (проверено `test_price_gate.py`);
#   • слепок старше `PRICE_FRESH_MAX_AGE_DAYS` → `УСТАРЕЛО` и цена гаснет ровно как в бою:
#     возраст судится по дате слепка, а не по ответу листа, и офлайн его не смягчает;
#   • адрес, которого фикстура не знает → AssertionError-тривайр (образец `test_bridge_http.py`),
#     чтобы новый живой вызов не спрятался под заглушкой молча.
# ТЕСТЫ, ЧЕЙ ПРЕДМЕТ — ОТКАЗ МОСТА, сюда не заходят вовсе: они подают свой `get=`, а
# `price_gate.verdict` при инъекции идёт мимо `_bridge_caller` и мимо кэша.
#
# ЧТО ТЕРЯЕМ (забор Честертона). Полный прогон был ПОБОЧНОЙ живой канарейкой: он раз в прогон
# сверял живые H3/I3/J3 со слепком и краснел бы на повёрнутой ручке. Канарейка была случайной
# (никто её так не звал) и недетерминированной, но она была. Закрывается НАЗВАННЫМ прогоном
# `venv\Scripts\python.exe price_freshness_run.py` — те же девять проб живой дверью, вердикт
# на экран; и самим боем: сторож стои́т в пути ответа и судит живой лист раз в 30 минут.

import price_freshness_run as _pfr      # noqa: E402
import price_gate as _pgate             # noqa: E402

_CELL_OF_BIKE = {bike: cell for cell, bikes in _pfr.PROBES for bike in bikes}


def _recorded_handles():
    """Ручки ЗАПИСАННОГО правила тем же читателем, которым их читает сторож. None — «не
    прочитано»: подменять отказ пустым словарём нельзя, пустой слепок сторож принял бы за
    «сверять нечего» с ЛОЖНОЙ причиной в карточке владельцу."""
    snap, _why = _pfr.read_rule()
    if not isinstance(snap, dict):
        return None
    handles = snap.get("handles")
    return handles if isinstance(handles, dict) and handles else None


def offline_bridge_door(action, **kw):
    """Ответ листа БЕЗ СЕТИ, в живом формате двери `quote_price`.

    Живой формат снят с потребителя: `price_freshness_run.live_handles` читает у ответа
    `answer["season"]["global_discount"]` и требует `ok is True` (см. его тело). Отдаём
    положение ручки, ЗАПИСАННОЕ в правиле, — то есть проигрываем ровно один сценарий: «лист
    с момента снимка не двигали». Повёрнутую ручку, мёртвую дверь, голодный бюджет и старый
    слепок судит `test_price_gate.py` своими фикстурами; здесь их подделывать нечем.
    """
    if action != "quote_price":
        raise AssertionError("офлайн-дверь сторожа не знает адреса: %r" % (action,))
    bike = kw.get("bike")
    cell = _CELL_OF_BIKE.get(bike)
    if cell is None:
        raise AssertionError("офлайн-дверь сторожа не знает юнита: %r" % (bike,))
    handles = _recorded_handles()
    if handles is None:
        raise RuntimeError("записанное правило не прочитано — дверь отказала")
    value = handles.get(cell)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RuntimeError("в слепке нет ручки %s — дверь отказала" % cell)
    return {"ok": True, "bike": bike, "days": 1,
            "date_start": kw.get("date_start"), "date_end": kw.get("date_end"),
            "season": {"global_discount": float(value)}}


_pgate._bridge_caller = lambda: offline_bridge_door

_real_live_handles = _pfr.live_handles


def _live_handles_offline(get=None):
    """Второй вход к той же двери. `live_handles(get=None)` одалживает клиент демона САМ,
    минуя `_bridge_caller`, — без этой обёртки заглушка была бы дырявой ровно на один вызов.
    Инъекция вызывающего проходит НАСКВОЗЬ: подача `get=` ничего здесь не встречает."""
    return _real_live_handles(get=offline_bridge_door if get is None else get)


_pfr.live_handles = _live_handles_offline
_pgate.reset()                          # вердикт живого моста не переживает установку двери
