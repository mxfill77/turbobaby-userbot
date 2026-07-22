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
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import User

load_dotenv()

# suggest импортируем ПОСЛЕ load_dotenv — модуль читает конфиг (SUGGEST_MODE и пр.)
# из окружения на импорте; иначе флаги из .env не подхватятся.
import suggest  # noqa: E402
import trainer  # noqa: E402  ГРУППА-ТРЕНАЖЁР (изолированный путь; боевой поток не задет)
import booking_draft  # noqa: E402  (текст-команда «до crm» в тренажёре — мост 2.1, read-only)

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
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("userbot")

_ME_ID = None  # id аккаунта userbot; заполняется в main после get_me (нужно SUGGEST)


def _now() -> str:
    """ISO-метка текущего момента (UTC) для служебных строк журнала."""
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с данным PID (Windows, без psutil).

    НЕ используем os.kill(pid, 0): на Windows это ВЫЗЫВАЕТ TerminateProcess —
    то есть убило бы процесс. Спрашиваем tasklist (фиксированный запрос).
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
        )
        return f'"{pid}"' in out.stdout or f",{pid}," in out.stdout
    except Exception:
        # Не смогли проверить — считаем мёртвым, чтобы не заклинить legit-старт.
        return False


def _read_lock_pid() -> int:
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def acquire_lock() -> bool:
    """Гарантия одного экземпляра. True — лок наш, работаем; False — уже кто-то живой.

    Создаём lock атомарно (O_CREAT|O_EXCL). Если файл уже есть — проверяем, жив ли
    владелец по PID: жив → выходим (второй экземпляр не поднимаем), мёртв → снимаем
    устаревший лок и пробуем снова.
    """
    for _ in range(3):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            old = _read_lock_pid()
            if old == 0:
                # Владелец, возможно, ещё дописывает PID — подождём и перечитаем.
                time.sleep(0.3)
                old = _read_lock_pid()
            if old and _pid_alive(old):
                log.info(
                    f"{_now()} | userbot уже запущен (PID {old}), выхожу — "
                    f"второй экземпляр на session не поднимаю."
                )
                return False
            log.info(f"{_now()} | устаревший userbot.lock (PID {old or '?'} мёртв) — забираю лок.")
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass
    log.warning(f"{_now()} | не смог получить lock — на всякий случай НЕ стартую (защита от дубля).")
    return False


def release_lock() -> None:
    """Снять lock — только если он наш (наш PID внутри)."""
    try:
        if _read_lock_pid() == os.getpid():
            os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass
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


async def _trainer_client_turn(event, body):
    """Клиентская реплика владельца → ДОБАВИТЬ в накопительный транскрипт (атомарно, под локом,
    чтобы параллельные события не затирали друг друга) и запланировать ОДИН дебаунс-ответ."""
    chat_id = event.chat_id
    async with _TRAINER_LOCK:
        is_text = bool((body or "").strip()) and not body.startswith("[")
        transcript = trainer.append_turn(trainer.get_transcript(), "client", body)
        trainer.set_transcript(transcript, incoming=body if is_text else None)
        my_seq = trainer.bump_seq()
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
            return
        if trainer.get_seq() != my_seq:   # сброс/новый турн ВО ВРЕМЯ генерации → устаревший ответ не постим
            return
        n = trainer.get_n()
        k = len(suggest.list_playbook_rules())
        await _trainer_send(event.client, chat_id, trainer.render_answer(n, k, draft))
        # фиксируем обмен: транскрипт + последняя пара (для кнопок «До CRM»/«Обучить» модербота)
        transcript = trainer.append_turn(transcript, "manager", draft)
        trainer.record_turn(incoming, transcript, draft)


async def _trainer_crm(event):
    """ТЕКСТ-команда «до crm»: текущий диалог ТЕСТ-клиента → мост 2.1 → карточка «🆕 БРОНЬ [ТЕСТ]»
    ТОЛЬКО в саму группу тренажёра (боевые «Входящие брони» НЕ трогаем; в CRM не пишем)."""
    client = event.client
    chat_id = event.chat_id
    transcript = trainer.get_transcript()
    if not transcript.strip():
        await _trainer_send(client, chat_id, "⚠️ Диалог ТЕСТ-клиента пуст — нечего заводить в CRM.")
        return
    try:
        meta = {"transcript": transcript, "client_ref": "ТЕСТ", "client_name": "ТЕСТ"}
        card, intake_text = await asyncio.to_thread(
            booking_draft.make_booking_and_intake, transcript, None, None, None, None, meta)
    except Exception as e:
        log.warning(f"{_now()} | ТРЕНАЖЁР: сбой моста 2.1: {e}")
        await _trainer_send(client, chat_id, "⚠️ Не удалось собрать заявку (см. userbot.log).")
        return
    body = (intake_text or card or "⚠️ Из диалога заявку собрать не удалось.").strip()
    await _trainer_send(client, chat_id, trainer.crm_card(body))


async def _trainer_reset(event):
    """ТЕКСТ-команда «заново»: полный сброс контекста ТЕСТ-клиента, инкремент N."""
    n = trainer.reset()
    await _trainer_send(event.client, event.chat_id,
                        f"🔄 Сброшено. Новый клиент ТЕСТ-{n} — контекст (факты + память диалога) очищен.")


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
    if kind == "reset":
        await _trainer_reset(event)
        return
    if kind == "crm":
        await _trainer_crm(event)
        return
    if kind == "lesson":
        if not suggest.is_approver(username):
            await _trainer_send(event.client, chat_id, "⛔ Учить бота может только approver.")
            return
        dec = await asyncio.to_thread(trainer.apply_lesson, payload)
        await _trainer_send(event.client, chat_id, dec["card"])
        return
    if kind == "cancel":
        if not suggest.is_approver(username):
            await _trainer_send(event.client, chat_id, "⛔ Откатывать правила может только approver.")
            return
        dec = trainer.cancel_lesson(payload)
        await _trainer_send(event.client, chat_id, dec["card"])
        return
    # обычная реплика ТЕСТ-клиента → боевой пайплайн, ответ в группу.
    # Вложения (гео-ПИН/фото) собираем МАРКЕРАМИ, как transcript_from в ЛС-пути: иначе пин не
    # резолвится в зону, а фото паспорта не засчитывается трекером (event.raw_text их не несёт).
    msg = event.message
    geo = getattr(msg, "geo", None)
    gm = suggest.geo_marker(geo) if geo is not None else None
    has_photo = getattr(msg, "photo", None) is not None
    body = trainer.client_body(text, has_photo=has_photo, geo_marker=gm)
    await _trainer_client_turn(event, body)


async def main():
    # Singleton-гард ДО подключения: если живой экземпляр уже есть — выходим,
    # чтобы не было двух клиентов на одной session.
    if not acquire_lock():
        return

    # Клиент создаём внутри main (под asyncio.run) — как в fetch_client_chats.py,
    # чтобы Telethon корректно привязался к event loop.
    client = TelegramClient(SESSION, API_ID, API_HASH)
    client.add_event_handler(on_incoming, events.NewMessage(incoming=True))
    # ГРУППА-ТРЕНАЖЁР: отдельный хендлер только на ГРУППОВЫЕ входящие (func=is_group), чтобы
    # ЛС-путь (on_incoming) не задевать. Внутри — строгая изоляция по привязанному chat_id.
    client.add_event_handler(
        on_trainer_group, events.NewMessage(incoming=True, func=lambda e: e.is_group))
    # Второй хендлер (модерация) вешаем ПОСЛЕ старта — когда известен id группы
    # (может резолвиться по имени через iter_dialogs). См. блок после get_me ниже.

    # ГРУППА-ТРЕНАЖЁР хранит сессионное состояние в moderation_ipc.meta (общий канал с модерботом).
    # init_db идемпотентна — гарантируем таблицу meta даже без bot-режима. Не критично для ЛС-пути.
    try:
        import moderation_ipc
        moderation_ipc.init_db()
    except Exception as e:
        log.warning(f"{_now()} | ТРЕНАЖЁР: init IPC meta: {e}")

    log.info(f"{_now()} | --- userbot_listen ЗАПУСК (ЭТАП C: слушаю, НЕ отвечаю) ---")
    try:
        # start() поднимет существующую сессию turbobaby_session — код подтверждения не спросит.
        await client.start()
        me = await client.get_me()
        global _ME_ID
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
            if gid is not None:
                client.add_event_handler(on_moderation, events.NewMessage(chats=gid))
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
            if suggest.MODERBOT_TOKEN:
                asyncio.create_task(_suggest_ipc_poller(client))
            log.info(
                f"{_now()} | SUGGEST ВКЛЮЧЁН "
                f"(TEST_MODE={suggest.SUGGEST_TEST_MODE}, mod_group={gid}, "
                f"bot-режим={'да' if suggest.MODERBOT_TOKEN else 'нет (reply-режим)'}, "
                f"лимиты {suggest.RATE_PER_HOUR}/ч {suggest.RATE_PER_DAY}/д)."
            )
        else:
            log.info(f"{_now()} | SUGGEST выключен — чистый Stage C, исходящих ноль.")
        # Процесс ЖИВЁТ постоянно — это и есть отличие от разведчиков.
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
        release_lock()  # снимаем lock при любом выходе
        log.info(f"{_now()} | --- userbot_listen ОСТАНОВЛЕН ---")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info(f"{_now()} | Ctrl+C — останавливаюсь.")
