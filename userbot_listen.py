"""
userbot_listen.py — слушающее ядро userbot @turbophuket (ЭТАП C).

ТОЛЬКО слушает входящие ЛИЧНЫЕ сообщения и логирует их в userbot.log.
НИЧЕГО не отправляет, не пересылает, не реагирует — НОЛЬ исходящих сообщений.
Это первый постоянно живущий процесс (в отличие от разведчиков fetch_*/recon_*,
которые делают disconnect и выходят сразу).

Запускать ТОЛЬКО с ПК (D:\\turbobaby-bot), где лежат .env и
turbobaby_session.session — те же ключи и та же сессия, что у fetch_*.py.
.env и *.session НЕ в git и кодом не трогаются.

Запуск:
    cd D:\\turbobaby-bot
    venv\\Scripts\\activate
    python userbot_listen.py

Остановка: Ctrl+C (аккуратно отключится от Telegram).

Зависимости: telethon, python-dotenv (уже стоят, как у fetch_*.py).

ЭТАП C (по умолчанию) — userbot только слушает и логирует, ноль исходящих.
Ступень ① SUGGEST — ОПЦИОНАЛЬНА и по умолчанию ВЫКЛЮЧЕНА (SUGGEST_MODE=off в .env):
при выключенной SUGGEST поведение идентично Stage C. При включении черновики ответов
готовятся и уходят клиенту ТОЛЬКО после модерации reply-командой — логика в suggest.py.
"""

import os
import time
import asyncio
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import User

load_dotenv()

# suggest импортируем ПОСЛЕ load_dotenv — модуль читает конфиг (SUGGEST_MODE и пр.)
# из окружения на импорте; иначе флаги из .env не подхватятся.
import suggest  # noqa: E402
import trainer  # noqa: E402  ГРУППА-ТРЕНАЖЁР (изолированный путь; боевой поток не задет)
import trainer_log  # noqa: E402  ЛОГ ТРЕНАЖЁРА в мозг (KB_trainer_log; fail-safe, ничего не блокирует)
import trainer_photo  # noqa: E402  СНИМОК В ТРЕНАЖЁРЕ: загрузка в папку входящих + чтение головой
import booking_draft  # noqa: E402  (текст-команда «до crm» в тренажёре — мост 2.1, read-only)
import proc_identity  # noqa: E402  ЛИЧНОСТЬ ПРОЦЕССА: номер + имя запуска + момент старта

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION = os.getenv("TELETHON_SESSION_NAME", "turbobaby_session")  # та же сессия, что fetch_*.py

# Свои аккаунты — их сообщения НЕ логируем (как в fetch_client_chats.py).
OWN_USERNAMES = {"turbophuket", "turbophuket1"}

LOG_FILE = "userbot.log"

# Singleton-гард: lock-файл с PID живого экземпляра. Главная страховка от ДВУХ
# клиентов на одной session turbobaby_session (двойной клиент = риск бана).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(BASE_DIR, "userbot.lock")

# Логирование: файл (utf-8) + stdout. Каждая строка самодостаточна (своя ISO-дата).
try:
    import log_setup                       # ротация + тестовый лог в temp (см. log_setup)
    _ub_h = log_setup.rotating_handler(LOG_FILE, fmt="%(message)s")
except Exception:
    _ub_h = None
if _ub_h is None:
    _ub_h = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _ub_h.setFormatter(logging.Formatter("%(message)s"))
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[_ub_h, logging.StreamHandler()],
)
log = logging.getLogger("userbot")

_ME_ID = None  # id аккаунта userbot; заполняется в main после get_me (нужно SUGGEST)


def _now() -> str:
    """ISO-метка текущего момента (UTC) для служебных строк журнала."""
    return datetime.now(timezone.utc).isoformat()


def _read_lock_pid() -> int:
    """Номер из лока — ПЕРВОЙ строкой (в локе нового формата второй строкой идёт личность).
    Оставлен ради читающих снаружи: сам гард судит не по номеру, а по личности."""
    rec = proc_identity.read_lock(LOCK_FILE)
    return int(rec["pid"]) if rec else 0


def acquire_lock() -> bool:
    """Гарантия одного экземпляра. True — лок наш, работаем; False — уже кто-то живой.

    30.08.2026 — ГАРД УСИЛЕН, А НЕ СНЯТ. Прежде владелец опознавался ОДНИМ номером через
    `tasklist`, а номер уникален только среди живых: после ребута он достаётся чужому процессу,
    и гард держит старт НАВСЕГДА (живой случай модербота 26–30.08, простой 3 суток 15 часов;
    до него — агент полосы 23.08 после BSOD, номер 8960 = `wlanext.exe`). Теперь личность —
    номер + имя запуска + момент старта, общим правилом полосы (`proc_identity`).

    ВТОРАЯ ПРАВКА, И ОНА ВАЖНЕЕ ПЕРВОЙ ДЛЯ ЭТОГО ФАЙЛА: прежняя проба глотала свой отказ в
    False с доводом «чтобы не заклинить legit-старт» — то есть таймаут `tasklist` на
    просыпающемся ПК читался как «владелец мёртв», лок забирался и поднимался ВТОРОЙ клиент на
    одной session. Цена этой ошибки здесь названа в шапке файла: «двойной клиент = риск бана»,
    и она несопоставима с ценой лишних 15 минут ожидания следующего тика сторожа. Поэтому
    молчание пробы больше не даёт старта."""
    ok, verdict, why = proc_identity.acquire(
        LOCK_FILE, script=os.path.basename(__file__),
        log=lambda m: log.info(f"{_now()} | userbot.lock: {m}"))
    if ok:
        return True
    if verdict == proc_identity.OURS_ALIVE:
        log.info(f"{_now()} | userbot уже запущен, выхожу — второй экземпляр на session "
                 f"не поднимаю. {why}")
    else:
        log.warning(f"{_now()} | НЕ стартую [{verdict}]: {why}")
    return False


def release_lock() -> None:
    """Снять lock — только если он наш, по номеру И моменту старта (голого номера мало: после
    рестарта его может носить чужой процесс, и мы сняли бы ЧУЖОЙ живой лок)."""
    try:
        proc_identity.release(LOCK_FILE)
    except Exception as e:
        log.warning(f"{_now()} | не смог снять lock-файл: {e}")


async def on_incoming(event):
    """Обработчик входящих сообщений. ЭТАП C: только логируем, ничего не шлём."""
    # 1) Только личные диалоги (не группы, не каналы).
    if not event.is_private:
        return

    sender = await event.get_sender()
    # 2) Только живые пользователи; ботов исключаем.
    if not isinstance(sender, User) or sender.bot:
        return

    # 3) Исключаем свои же аккаунты.
    if (sender.username or "").lower() in OWN_USERNAMES:
        return

    who = f"@{sender.username}" if sender.username else f"id{sender.id}"
    name = " ".join(filter(None, [sender.first_name, sender.last_name])) or "?"
    # Текст в одну строку, чтобы журнал оставался по строке на сообщение.
    text = (event.raw_text or "").replace("\n", " ⏎ ").strip() or "[без текста / медиа]"
    date_iso = event.message.date.isoformat()

    log.info(f"{date_iso} | {who} | {name} | {text}")

    # --- ступень ① SUGGEST (opt-in; при SUGGEST_MODE=off блок не выполняется) ---
    if suggest.is_enabled():
        try:
            await suggest.on_client_message(event.client, sender, _ME_ID)
        except Exception as e:
            log.warning(f"{_now()} | SUGGEST: сбой генерации черновика: {e}")


async def on_moderation(event):
    """Reply менеджера в группе «Модерация ответов» → approve/edit/reject (ступень ①)."""
    if not suggest.is_enabled():
        return
    try:
        await suggest.on_moderation_reply(event)
    except Exception as e:
        log.warning(f"{_now()} | SUGGEST: сбой обработки модерации: {e}")


async def _suggest_ipc_poller(client):
    """Исполнитель решений бота-модератора (задача-2, bot-режим): раз в ~3с относим
    готовые (status='ready') ответы клиенту через userbot. Deprecation-safe: даже если
    бот упал, добираем уже принятые решения. SAFETY: реальная отправка — в suggest.send,
    в TEST_MODE заблокирована."""
    try:
        import moderation_ipc
        moderation_ipc.init_db()
    except Exception as e:
        log.warning(f"{_now()} | SUGGEST: IPC init: {e}")
    while True:
        # Связи нет (идёт переподключение, см. serve_forever) — сеть не дёргаем и лог не льём:
        # отправка всё равно упала бы ConnectionError'ом, а очередь дождётся подъёма. Внутренний
        # авто-реконнект Telethon сюда НЕ попадает (там _user_connected остаётся True), так что
        # короткие просадки обрабатываются как раньше — запрос уйдёт после восстановления.
        if not client.is_connected():
            await asyncio.sleep(3)
            continue
        try:
            await suggest.poll_and_send(client)
        except Exception as e:
            log.warning(f"{_now()} | SUGGEST: IPC-поллер: {e}")
        try:
            # O3-2c: пост подтверждённых заявок «🆕 БРОНЬ» во «Входящие брони» ЭТИМ userbot-аккаунтом
            # (бот-аккаунт INTAKE не видит). Отдельный try — сбой постинга не роняет client-send выше.
            await suggest.poll_and_post_intake(client)
        except Exception as e:
            log.warning(f"{_now()} | SUGGEST: intake-поллер: {e}")
        await asyncio.sleep(3)


# ============================ ГРУППА-ТРЕНАЖЁР ================================
# Изолированный путь: активен ТОЛЬКО в привязанной группе «Тренеровка» (по chat_id).
# Боевой поток реальных клиентов (on_incoming, ЛС) НЕ задет ничем. userbot — единственный,
# кто шлёт клиентские ответы в группу; ТЕКСТ-команды (заново/до crm/урок:/отмени урок N)
# работают ВСЕГДА (даже если модербот-панель кнопок недоступна).

# Дебаунс склейки близких Telegram-событий в ОДИН турн/ответ (текст+вложение приходят
# отдельными апдейтами). Родитель ТЕСТ-4: без склейки бот отвечал ДВАЖДЫ, и из-за гонки
# (оба обработчика читали транскрипт до записи) каждый ответ видел лишь СВОЙ кусок контекста.
TRAINER_DEBOUNCE_SEC = float(os.getenv("TRAINER_DEBOUNCE_SEC", "8") or "8")
# Сериализация мутаций транскрипта и генерации ВНУТРИ процесса userbot (единственный генератор).
_TRAINER_LOCK = asyncio.Lock()


async def _trainer_send(client, chat_id, text):
    """Пост в группу тренажёра, с анти-тайским фильтром. Не роняет обработчик."""
    try:
        await client.send_message(chat_id, trainer.strip_thai(text))
    except Exception as e:
        log.warning(f"{_now()} | ТРЕНАЖЁР: не смог запостить в группу: {e}")


async def _trainer_generate(transcript, first):
    """ОДИН черновик по ПОЛНОМУ накопленному транскрипту (боевой пайплайн: прайс/зоны/generate).
    collected_facts и доставка считаются по всему окну диалога, а не по одному событию."""
    lang = suggest.detect_lang_from_client(transcript)
    hints = suggest.extract_booking_hints(transcript)
    price_note = await asyncio.to_thread(suggest.build_pricing_note, hints, lang)
    allow = suggest.park_allowlist()
    pb = suggest.load_playbook()
    faq = suggest.load_faq()
    draft = await asyncio.to_thread(
        suggest.generate_draft, transcript, lang, faq, first, price_note, None, allow, pb)
    return trainer.strip_thai(draft or "")


async def _trn_log(kind, text, n=None):
    """Событие тренажёра → KB_trainer_log ФОНОМ. Наблюдение НЕ ИМЕЕТ ПРАВА задерживать диалог:
    запись — это до 2 HTTP к Bridge (по 30с) плюс ожидание файлового лока, а вызывается она прямо
    в клиентском турне (_trainer_client_turn ждёт её ДО планирования ответа). Поэтому здесь только
    ПЛАНИРУЕМ задачу и сразу возвращаемся — сам await мгновенный, сеть живёт в фоне.
    Ошибки глотает trainer_log; исключение планирования (нет живого цикла) тоже не роняет вызов."""
    try:
        n = trainer.get_n() if n is None else n
    except Exception:
        n = 0

    async def _write():
        try:
            await asyncio.to_thread(trainer_log.safe_append, n, kind, text)
        except Exception as e:
            log.info(f"{_now()} | ТРЕНАЖЁР: лог в мозг не записан: {type(e).__name__}: {e}")

    try:
        asyncio.create_task(_write())
    except RuntimeError:
        pass                      # цикла нет — лог наблюдения не критичен


async def _trainer_client_turn(event, body):
    """Клиентская реплика владельца → ДОБАВИТЬ в накопительный транскрипт (атомарно, под локом,
    чтобы параллельные события не затирали друг друга) и запланировать ОДИН дебаунс-ответ."""
    chat_id = event.chat_id
    async with _TRAINER_LOCK:
        is_text = bool((body or "").strip()) and not body.startswith("[")
        transcript = trainer.append_turn(trainer.get_transcript(), "client", body)
        trainer.set_transcript(transcript, incoming=body if is_text else None)
        my_seq = trainer.bump_seq()
    await _trn_log(trainer_log.KIND_CLIENT, body)     # реплика клиента (текст/гео/фото) — в мозг
    asyncio.create_task(_trainer_debounced_reply(event, chat_id, my_seq))


async def _trainer_debounced_reply(event, chat_id, my_seq):
    """Через TRAINER_DEBOUNCE_SEC после последнего события — ОДИН ответ по ПОЛНОМУ транскрипту.
    Если за окно пришёл новый турн или был сброс (токен seq сменился, в т.ч. кнопкой модербота) —
    эта задача устаревает и молчит (ответит задача самого свежего турна)."""
    try:
        await asyncio.sleep(TRAINER_DEBOUNCE_SEC)
    except asyncio.CancelledError:
        return
    if trainer.get_seq() != my_seq:
        return
    async with _TRAINER_LOCK:
        if trainer.get_seq() != my_seq:
            return
        transcript = trainer.get_transcript()
        if not transcript.strip():
            return
        first = not trainer.has_manager_turn(transcript)   # приветствие — один раз на ТЕСТ-клиента
        incoming, _ans = trainer.get_last_pair()
        try:
            draft = await _trainer_generate(transcript, first)
        except Exception as e:
            log.warning(f"{_now()} | ТРЕНАЖЁР: сбой генерации ответа: {e}")
            await _trainer_send(event.client, chat_id, "⚠️ Не удалось сгенерировать ответ (см. userbot.log).")
            # ветка «упало» обязана быть видна в TRN-логе (иначе тишина неотличима от «не жали»)
            await _trn_log(trainer_log.KIND_BOT,
                           f"⚠️ сбой генерации ответа: {type(e).__name__}: {e}")
            return
        if trainer.get_seq() != my_seq:   # сброс/новый турн ВО ВРЕМЯ генерации → устаревший ответ не постим
            return
        n = trainer.get_n()
        k = len(suggest.list_playbook_rules())
        answer = trainer.render_answer(n, k, draft)
        await _trainer_send(event.client, chat_id, answer)
        # фиксируем обмен: транскрипт + последняя пара (для кнопок «До CRM»/«Обучить» модербота)
        transcript = trainer.append_turn(transcript, "manager", draft)
        trainer.record_turn(incoming, transcript, draft)
    # ответ бота в мозг — ЦЕЛИКОМ: с шапкой [тренажёр|ТЕСТ-N|правил:K] и служебными тегами
    await _trn_log(trainer_log.KIND_BOT, answer, n)


async def _trainer_crm(event):
    """ТЕКСТ-команда «до crm»: текущий диалог ТЕСТ-клиента → мост 2.1 → карточка «🆕 БРОНЬ [ТЕСТ]»
    ТОЛЬКО в саму группу тренажёра (боевые «Входящие брони» НЕ трогаем; в CRM не пишем)."""
    client = event.client
    chat_id = event.chat_id
    transcript = trainer.get_transcript()
    if not transcript.strip():
        await _trainer_send(client, chat_id, "⚠️ Диалог ТЕСТ-клиента пуст — нечего заводить в CRM.")
        await _trn_log(trainer_log.KIND_BTN,
                       "⚠️ До CRM (текст-команда): диалог пуст — карточка не собрана")
        return
    try:
        meta = {"transcript": transcript, "client_ref": "ТЕСТ", "client_name": "ТЕСТ"}
        card, intake_text = await asyncio.to_thread(
            booking_draft.make_booking_and_intake, transcript, None, None, None, None, meta)
    except Exception as e:
        log.warning(f"{_now()} | ТРЕНАЖЁР: сбой моста 2.1: {e}")
        await _trainer_send(client, chat_id, "⚠️ Не удалось собрать заявку (см. userbot.log).")
        await _trn_log(trainer_log.KIND_BTN,
                       f"⚠️ До CRM (текст-команда): сбой моста 2.1 — {type(e).__name__}: {e}")
        return
    body = (intake_text or card or "⚠️ Из диалога заявку собрать не удалось.").strip()
    await _trainer_send(client, chat_id, trainer.crm_card(body))
    await _trn_log(trainer_log.KIND_BTN, "До CRM (текст-команда): карточка [ТЕСТ] собрана")


async def _trainer_reset(event):
    """ТЕКСТ-команда «заново»: полный сброс контекста ТЕСТ-клиента, инкремент N."""
    n = trainer.reset()
    await _trainer_send(event.client, event.chat_id,
                        f"🔄 Сброшено. Новый клиент ТЕСТ-{n} — контекст (факты + память диалога) очищен.")
    await _trn_log(trainer_log.KIND_BTN, f"Заново (текст-команда): старт нового ТЕСТ-{n}", n)


async def on_trainer_group(event):
    """Входящее сообщение в ГРУППЕ. Действуем ТОЛЬКО в привязанной группе тренажёра; всё прочее —
    мимо (боевой контур не трогаем). Ленивая привязка по первому сообщению владельца в группе
    с title «Тренеровка». Ботов (в т.ч. модербот) и свои сообщения игнорируем."""
    if not event.is_group:
        return
    sender = await event.get_sender()
    if not isinstance(sender, User) or sender.bot:
        return  # только живые люди; модербот/каналы — не клиентские реплики
    if (sender.username or "").lower() in OWN_USERNAMES:
        return
    chat_id = event.chat_id
    bound = trainer.bound_chat_id()
    if bound is None:
        # ленивая привязка: строго по title «Тренеровка» (иначе НЕ наша группа — выходим)
        try:
            chat = await event.get_chat()
            title = getattr(chat, "title", "") or ""
        except Exception:
            title = ""
        if not trainer.title_matches(title):
            return
        trainer.bind_chat(chat_id)
        await _trainer_send(
            event.client, chat_id,
            f"🎓 Группа-тренажёр привязана: chat_id={chat_id}, title=«{title}». "
            f"Пиши как клиент — отвечу ТЕСТ-клиентом. Команды: заново · до crm · урок: … · отмени урок N.")
        bound = chat_id
    if int(chat_id) != int(bound):
        return  # строгая ИЗОЛЯЦИЯ: другая группа — не тренажёр

    text = event.raw_text or ""
    username = sender.username
    kind, payload = trainer.parse_command(text)
    # «✍ другое» БЕЗ ПРЕФИКСА: модербот кнопкой поставил ожидание — СЛЕДУЮЩЕЕ текстовое сообщение
    # владельца становится правилом ЦЕЛИКОМ и ДОСЛОВНО (голосовой ввод/опечатки НЕ правим). Ловит
    # именно userbot: в группе он видит ВСЕ сообщения, а модербот — не обязательно (privacy-режим).
    # Команда управления (заново / до crm / урок: … / отмени урок N) ожидание НЕ съедает — она
    # остаётся командой, ожидание живёт дальше до своего TTL.
    if kind is None and text.strip() and trainer.pending_lesson() is not None:
        if not suggest.is_approver(username):
            await _trainer_send(event.client, chat_id, "⛔ Учить бота может только approver.")
            await _trn_log(trainer_log.KIND_LESSON,
                           f"⛔ отказ: @{username or '?'} не approver — свободный текст правила не принят")
            return
        if trainer.take_pending_lesson(username):
            dec = await asyncio.to_thread(trainer.apply_lesson, text.strip())
            await _trainer_send(event.client, chat_id, dec["card"])
            await _trn_log(trainer_log.KIND_LESSON,
                           f"«другое» (свободный текст, дословно): {text.strip()} → {dec['card']}")
            return
    if kind == "reset":
        await _trainer_reset(event)
        return
    if kind == "crm":
        await _trainer_crm(event)
        return
    if kind == "lesson":
        if not suggest.is_approver(username):
            await _trainer_send(event.client, chat_id, "⛔ Учить бота может только approver.")
            await _trn_log(trainer_log.KIND_LESSON,
                           f"⛔ отказ: @{username or '?'} не approver — «урок: …» не принят")
            return
        dec = await asyncio.to_thread(trainer.apply_lesson, payload)
        await _trainer_send(event.client, chat_id, dec["card"])
        await _trn_log(trainer_log.KIND_LESSON, f"урок: {payload} → {dec['card']}")
        return
    if kind == "cancel":
        if not suggest.is_approver(username):
            await _trainer_send(event.client, chat_id, "⛔ Откатывать правила может только approver.")
            await _trn_log(trainer_log.KIND_LESSON,
                           f"⛔ отказ: @{username or '?'} не approver — «отмени урок {payload}» не выполнена")
            return
        dec = trainer.cancel_lesson(payload)
        await _trainer_send(event.client, chat_id, dec["card"])
        await _trn_log(trainer_log.KIND_LESSON, f"отмени урок {payload} → {dec['card']}")
        return
    # обычная реплика ТЕСТ-клиента → боевой пайплайн, ответ в группу.
    # Вложения (гео-ПИН/фото) собираем МАРКЕРАМИ, как transcript_from в ЛС-пути: иначе пин не
    # резолвится в зону, а фото паспорта не засчитывается трекером (event.raw_text их не несёт).
    msg = event.message
    geo = getattr(msg, "geo", None)
    gm = suggest.geo_marker(geo) if geo is not None else None
    has_photo = getattr(msg, "photo", None) is not None
    note = None
    if has_photo:
        # ЧТЕНИЕ СНИМКА (задания 60-a/60-b/60-d). Первой строкой — гашение: чтение головой длится
        # 78…88 с (замер 60-a), а дебаунс ответа 8 с. Подпись и снимок приходят РАЗНЫМИ апдейтами
        # (живой след 12.09 18:10), значит ответ на подпись уже запланирован и без bump_seq
        # бот ответил бы «пришлите фото» за 8 с — то есть опоздал бы к собственному снимку.
        trainer.bump_seq()
        try:
            desc = await trainer_photo.intake(
                msg, chat_id=chat_id, chat_title=trainer.TRAINER_GROUP_NAME, sender=sender)
        except Exception as e:
            # Сорванный снимок НЕ имеет права уронить турн: молчание бота — тот самый дефект,
            # ради которого всё это заводилось. Падаем в прежний бессодержательный «[фото]».
            log.warning(f"{_now()} | ТРЕНАЖЁР/фото: разбор снимка сорвался: {type(e).__name__}: {e}")
            desc = None
        if desc:
            note = trainer_photo.transcript_body(desc)
            log.info(f"{_now()} | ТРЕНАЖЁР/фото: msg={getattr(msg, 'id', '?')} "
                     f"хранение={desc.get('saved') or '—'} чтение={desc.get('outcome')} "
                     f"байт={desc.get('bytes')} строк={desc.get('lines')} "
                     f"{('· ' + desc['reason']) if desc.get('reason') else ''}")
            words = trainer_photo.human_note(desc)
            if words:
                await _trainer_send(event.client, chat_id, words)
    body = trainer.client_body(text, has_photo=has_photo, geo_marker=gm, photo_note=note)
    await _trainer_client_turn(event, body)


# ═══════════════ УСТОЙЧИВОСТЬ СОЕДИНЕНИЯ (класс 05.08.2026) ═══════════════════════════════
# Разрыв внешней сети ПК НЕ ИМЕЕТ ПРАВА ронять процесс. Замер суток 04–05.08: ПЯТЬ смертей
# userbot, все — один и тот же НЕОБРАБОТАННЫЙ ConnectionError Telethon (`mtprotosender.py:266`
# после connection_retries), вход — `client.start()` ниже. Ошибки прикладного кода среди них нет
# ни одной: WinError 121/1236/1231 — коды ЛОКАЛЬНОГО сетевого стека Windows, причина внешняя
# (переезд ПК между Wi-Fi «Samgold 7» ↔ «Bless_house_2.4GHz», DNS ложился целиком). Один простой
# длился 370 минут. Разбор: docs/artifacts/2026-08-05-userbot-crashes-and-mute-session-5348.md
#
# Форма — ШТАТНАЯ для Telethon, без самодеятельности: ОДИН клиент на процесс и повторный
# `client.start()` НА ТОМ ЖЕ ОБЪЕКТЕ. Так можно по устройству библиотеки: session-файл после
# disconnect переоткрывается лениво (`SQLiteSession._cursor`), `connect()` заново поднимает
# send/recv/update/keepalive-циклы и свежий `_disconnected`-future, а `run_until_disconnected()`
# заново шлёт GetState (то есть снова просит апдейты). Внутренний авто-реконнект Telethon
# (`connection_retries=5`, ~30с) остаётся ПЕРВОЙ ступенью и здесь не трогается — этот цикл
# включается ровно там, где сдался он.
RECONNECT_DELAY_MIN = 5          # первая пауза после разрыва, с
RECONNECT_DELAY_MAX = 300        # потолок паузы, с — сеть лежит часами, долбить её незачем
RECONNECT_ALARM_SEC = 900        # раз в столько секунд простоя — ERROR в лог: тихого цикла нет

# Сетевой класс отказа. ConnectionError, ConnectionAbortedError, ConnectionResetError,
# TimeoutError и socket.gaierror — ВСЕ потомки OSError, живые коды инцидента приходят ими же.
# Всё ОСТАЛЬНОЕ (баг кода, отозванная сессия, RPCError) сюда НЕ попадает и роняет процесс как
# раньше: вечный цикл, глотающий настоящий дефект, хуже падения — контур-вотчдог остаётся
# вторым слоем ровно для этого. asyncio.CancelledError/KeyboardInterrupt — BaseException,
# в этот кортеж не входят и остановку не глушат.
_NET_ERRORS = (OSError, asyncio.TimeoutError)

_startup_done = False       # разовый пост-стартовый блок отработал
_moderation_hooked = False  # хендлер модерации повешен: второй = ДВОЙНАЯ обработка модерации
_ipc_poller = None          # задача-исполнитель решений модербота: вторая = ДВОЙНАЯ отправка


async def _startup_once(client):
    """Пост-стартовый блок — РОВНО ОДИН РАЗ за жизнь процесса (вход, реестр, SUGGEST, поллер).

    Зовётся после КАЖДОГО удачного входа, поэтому обязан быть идемпотентным. Два его шага
    опасны повтором и закрыты СВОИМИ замками: второй `on_moderation` — двойная обработка
    модерации, второй IPC-поллер — ДВОЙНАЯ ОТПРАВКА клиенту. Общий флаг снимается в самом
    конце: сорвавшийся на сети блок обязан повториться целиком."""
    global _ME_ID, _startup_done, _moderation_hooked, _ipc_poller
    if _startup_done:
        return
    me = await client.get_me()
    _ME_ID = me.id
    log.info(
        f"{_now()} | вошёл как @{me.username} (id={me.id}). "
        f"Слушаю входящие ЛИЧНЫЕ сообщения."
    )
    # Premium статус (для голосовой расшифровки в задаче-2) — читаем БЕЗ второго
    # клиента, из уже поднятого me. Появится в логе после рестарта.
    log.info(f"{_now()} | Premium аккаунта: {getattr(me, 'premium', None)} "
             f"(нужно для транскрипции голосовых reply).")
    # STAFF-РЕЗОЛВ (инцидент 22.07 @extthiwxer=Earth: стейл-username в реестре → фильтр мимо):
    # карточки ФАКТИЧЕСКИХ профилей внутренних аккаунтов (реестр по id) в лог — владелец
    # сверяет глазами «Earth = @… id …». Read-only (get_entity), fail-safe.
    for uid in sorted(suggest.TEAM_REGISTRY.get("user_ids") or []):
        try:
            ent = await client.get_entity(uid)
            uname = f"@{ent.username}" if getattr(ent, "username", None) else "(без username)"
            name = " ".join(filter(None, [getattr(ent, "first_name", None),
                                          getattr(ent, "last_name", None)])) or "?"
            log.info(f"{_now()} | STAFF-РЕЗОЛВ: id={uid} {uname} «{name}» — внутренний контур, "
                     f"сообщения НЕ обрабатываются (ноль реакций).")
        except Exception as e:
            log.warning(f"{_now()} | STAFF-РЕЗОЛВ: id={uid} не разрезолвился: {e}")
    if suggest.is_enabled():
        # Резолвим группу модерации (по ID из env или по имени) и вешаем 2-й хендлер.
        gid = await suggest.resolve_mod_group(client)
        if gid is not None and not _moderation_hooked:
            client.add_event_handler(on_moderation, events.NewMessage(chats=gid))
            _moderation_hooked = True
            # САМОЛЕЧЕНИЕ протечки 22.07: mod_chat в IPC-meta (куда модербот постит карточки)
            # оказался отравлен id группы ТРЕНАЖЁРА → боевые карточки уехали в «Тренеровку».
            # userbot знает НАСТОЯЩИЙ id «Модерации ответов» (резолв по имени) — если meta
            # пуста или указывает на тренажёр, чиним на резолвленный gid.
            try:
                import moderation_ipc
                v = moderation_ipc.get_meta("mod_chat")
                cur = int(v) if v and v.lstrip("-").isdigit() else None
                if cur is None or trainer.is_trainer_chat(cur):
                    moderation_ipc.set_meta("mod_chat", str(int(gid)))
                    log.warning(f"{_now()} | mod_chat вылечен: {cur} → {gid} "
                                f"(боевые карточки снова в «Модерацию ответов»).")
            except Exception as e:
                log.warning(f"{_now()} | самолечение mod_chat: {e}")
        # Исполнитель решений бота-модератора (bot-режим) — только при наличии токена.
        if suggest.MODERBOT_TOKEN and _ipc_poller is None:
            _ipc_poller = asyncio.create_task(_suggest_ipc_poller(client))
        log.info(
            f"{_now()} | SUGGEST ВКЛЮЧЁН "
            f"(TEST_MODE={suggest.SUGGEST_TEST_MODE}, mod_group={gid}, "
            f"bot-режим={'да' if suggest.MODERBOT_TOKEN else 'нет (reply-режим)'}, "
            f"лимиты {suggest.RATE_PER_HOUR}/ч {suggest.RATE_PER_DAY}/д)."
        )
    else:
        log.info(f"{_now()} | SUGGEST выключен — чистый Stage C, исходящих ноль.")
    _startup_done = True


async def serve_forever(client, sleep=None, clock=None):
    """Жизнь процесса: вход → слушаем → ОБРЫВ НЕ РОНЯЕТ, ждём и входим снова. Возврат из функции
    означает ШТАТНУЮ остановку (`run_until_disconnected` вернулся сам: disconnect/Ctrl+C).

    Экземпляр остаётся ОДИН по построению: клиент сюда передан готовым и не пересоздаётся, лок
    взят вызывающим на всю жизнь процесса, разовая инициализация закрыта своими замками.
    `sleep`/`clock` — инъекция для тестов (по умолчанию `asyncio.sleep`/`time.monotonic`)."""
    sleep = asyncio.sleep if sleep is None else sleep
    clock = time.monotonic if clock is None else clock
    delay = RECONNECT_DELAY_MIN
    down_since = None            # None ⇔ связь есть; иначе момент, когда её потеряли
    alarm_at = None              # когда последний раз кричали в лог о затяжном простое
    tries = 0
    while True:
        try:
            # start() поднимет существующую сессию turbobaby_session — код подтверждения не спросит.
            await client.start()
            await _startup_once(client)
            if down_since is not None:
                log.info(f"{_now()} | СВЯЗЬ С TELEGRAM ВОССТАНОВЛЕНА: простой "
                         f"{int(clock() - down_since)}с, попыток {tries}. Экземпляр тот же "
                         f"(PID {os.getpid()}), сессия одна, вход не задваивался.")
                down_since, alarm_at, tries, delay = None, None, 0, RECONNECT_DELAY_MIN
            # Процесс ЖИВЁТ постоянно — это и есть отличие от разведчиков.
            await client.run_until_disconnected()
            return
        except _NET_ERRORS as e:
            now = clock()
            tries += 1
            if down_since is None:
                down_since, alarm_at = now, now
                log.warning(f"{_now()} | СВЯЗЬ С TELEGRAM ПОТЕРЯНА ({type(e).__name__}: {e}) — "
                            f"процесс ЖИВ, переподключаюсь через {delay}с.")
            elif now - alarm_at >= RECONNECT_ALARM_SEC:
                alarm_at = now
                log.error(f"{_now()} | СВЯЗИ С TELEGRAM НЕТ УЖЕ {int(now - down_since)}с, "
                          f"попыток {tries}, последняя ошибка {type(e).__name__}: {e}. Продолжаю "
                          f"раз в {delay}с — смотри сеть ПК (Wi-Fi/DNS), бот сейчас НЕ СЛЫШИТ.")
            else:
                log.warning(f"{_now()} | переподключение не удалось ({type(e).__name__}: {e}); "
                            f"простой {int(now - down_since)}с, попытка {tries + 1} через {delay}с.")
            await sleep(delay)
            delay = min(delay * 2, RECONNECT_DELAY_MAX)


async def main():
    # Singleton-гард ДО подключения: если живой экземпляр уже есть — выходим,
    # чтобы не было двух клиентов на одной session. Лок держим ВСЮ жизнь процесса,
    # включая простои сети: переподключение не имеет права поднять второй экземпляр.
    if not acquire_lock():
        return

    # Клиент создаём внутри main (под asyncio.run) — как в fetch_client_chats.py,
    # чтобы Telethon корректно привязался к event loop. РОВНО ОДИН на процесс: переподключение
    # идёт на этом же объекте (см. serve_forever), иначе была бы вторая сессия.
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.add_event_handler(on_incoming, events.NewMessage(incoming=True))
    # ГРУППА-ТРЕНАЖЁР: отдельный хендлер только на ГРУППОВЫЕ входящие (func=is_group), чтобы
    # ЛС-путь (on_incoming) не задевать. Внутри — строгая изоляция по привязанному chat_id.
    client.add_event_handler(
        on_trainer_group, events.NewMessage(incoming=True, func=lambda e: e.is_group))
    # Второй хендлер (модерация) вешаем ПОСЛЕ старта — когда известен id группы
    # (может резолвиться по имени через iter_dialogs). См. _startup_once.

    # ГРУППА-ТРЕНАЖЁР хранит сессионное состояние в moderation_ipc.meta (общий канал с модерботом).
    # init_db идемпотентна — гарантируем таблицу meta даже без bot-режима. Не критично для ЛС-пути.
    try:
        import moderation_ipc
        moderation_ipc.init_db()
    except Exception as e:
        log.warning(f"{_now()} | ТРЕНАЖЁР: init IPC meta: {e}")

    log.info(f"{_now()} | --- userbot_listen ЗАПУСК (ЭТАП C: слушаю, НЕ отвечаю) ---")
    try:
        await serve_forever(client)
    finally:
        await client.disconnect()
        release_lock()  # снимаем lock при любом выходе
        log.info(f"{_now()} | --- userbot_listen ОСТАНОВЛЕН ---")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info(f"{_now()} | Ctrl+C — останавливаюсь.")
