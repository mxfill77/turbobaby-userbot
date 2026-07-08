# -*- coding: utf-8 -*-
"""
moderation_bot.py — диалоговый бот-модератор (задача-2, вариант «в»). Bot API (PTB 21.7).

Отдельный процесс. Токен MODERBOT_TOKEN из .env — если пуст, бот НЕ поднимается
(деградация: userbot работает в reply-режиме). Токен НЕ логируется.

Что делает:
  • heartbeat в IPC (userbot по нему понимает, что бот жив → шлёт черновики в IPC);
  • постит карточки черновиков С КНОПКАМИ [✅ Да][❌ Отклонить];
  • понимает reply свободным текстом (moderation_core.interpret через Anthropic):
    готовый текст → отправка; инструкция/команда → применяет и просит подтверждение;
    вопрос → отвечает по FAQ; «-»/«нет» → reject;
  • пишет РЕШЕНИЕ в IPC (ready/test_held/rejected); ОТПРАВКУ клиенту делает userbot.
  • Бот клиенту НЕ пишет НИКОГДА. APPROVER-whitelist на кнопки и reply.

SAFETY: в TEST_MODE решение = 'test_held' (userbot не отправит) + второй замок в
userbot.send_to_client. Голос: пока не расшифровываем (см. хвост про Whisper/Premium).

Запуск: ТОЛЬКО через pc_agent (тема 205) или venv-python. НЕ на VPS.
"""

import os
import sys
import logging
import subprocess

from dotenv import load_dotenv

load_dotenv()

import suggest            # noqa: E402  (после load_dotenv — читает конфиг из окружения)
import moderation_core    # noqa: E402
import moderation_ipc     # noqa: E402
import booking_draft      # noqa: E402  (O3 кусок 1 «Кнопка Бронь»: экстракция заявки, read-only)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[logging.FileHandler("moderation_bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("moderation_bot")

# БЕЗОПАСНОСТЬ: httpx/telegram на INFO пишут URL вида .../bot<TOKEN>/getUpdates — это
# утечка токена в лог. Глушим их (и шумный apscheduler) до WARNING.
for _noisy in ("httpx", "telegram", "telegram.ext", "apscheduler"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

TOKEN = suggest.MODERBOT_TOKEN
_env_mod = os.getenv("MOD_GROUP_ID", "").strip()
MOD_GROUP_ID = int(_env_mod) if _env_mod.lstrip("-").isdigit() else None
HEARTBEAT_SEC = 5
POLL_SEC = 3


def _target_chat():
    """Куда постить карточки: MOD_GROUP_ID из env или чат, где бота уже видели."""
    if MOD_GROUP_ID is not None:
        return MOD_GROUP_ID
    v = moderation_ipc.get_meta("mod_chat")
    return int(v) if v and v.lstrip("-").isdigit() else None


def _kb_initial():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data="m:{id}:yes"),
         InlineKeyboardButton("❌ Отклонить", callback_data="m:{id}:no")],
        # O3 кусок 1: экстракция заявки из диалога в карточку-черновик (read-only, без записи в CRM).
        [InlineKeyboardButton("📋 Бронь", callback_data="m:{id}:booking")],
    ])


def _kb_confirm():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="m:{id}:send")],
        [InlineKeyboardButton("📌 Запомнить как правило", callback_data="m:{id}:remember")],
        [InlineKeyboardButton("✏️ Ещё правка", callback_data="m:{id}:more"),
         InlineKeyboardButton("❌", callback_data="m:{id}:no")],
    ])


def _kb(markup_factory, draft_id):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    base = markup_factory()
    rows = []
    for row in base.inline_keyboard:
        rows.append([InlineKeyboardButton(b.text, callback_data=b.callback_data.format(id=draft_id))
                     for b in row])
    return InlineKeyboardMarkup(rows)


# ------------------------------- джобы ---------------------------------------

async def job_heartbeat(context):
    try:
        moderation_ipc.heartbeat()
    except Exception as e:
        log.warning(f"heartbeat: {e}")


async def job_poll_new(context):
    chat = _target_chat()
    if chat is None:
        return  # не знаем группу — ждём (добавьте бота в группу и напишите там)
    try:
        rows = moderation_ipc.fetch_new()
    except Exception as e:
        log.warning(f"poll_new: {e}")
        return
    for r in rows:
        try:
            head = f"✏️ Черновик клиенту {r['client_ref']}"
            if r.get("first_contact"):
                head += " (первый контакт)"
            msg = await context.bot.send_message(
                chat, head + "\n\n" + (r["draft"] or ""), reply_markup=_kb(_kb_initial, r["id"]))
            moderation_ipc.mark_posted(r["id"], msg.message_id)
        except Exception as e:
            log.warning(f"post card #{r['id']}: {e}")


# ------------------------------- применение решения --------------------------

async def _apply(context, chat_id, draft, dec, edit_msg_id=None):
    """Отразить decision-dict в IPC и в интерфейсе. Клиенту НИЧЕГО не шлём."""
    d = dec["decision"]
    by = dec.get("_by")
    if d == "denied":
        await context.bot.send_message(chat_id, "⛔ Нет прав на approve",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    if d == "await_more":
        await context.bot.send_message(chat_id, dec["card"], reply_to_message_id=draft.get("card_msg_id"))
        return
    if d == "answer":
        await context.bot.send_message(chat_id, dec["answer"] or "(пустой ответ)",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    if d == "confirm":
        m = await context.bot.send_message(chat_id, dec["card"], reply_markup=_kb(_kb_confirm, draft["id"]))
        # directive сохраняем в IPC — по нему кнопка «Запомнить как правило» соберёт правило
        moderation_ipc.set_candidate(draft["id"], dec["final_text"], directive=dec.get("directive"))
        moderation_ipc.mark_posted(draft["id"], m.message_id)  # новая карточка = точка reply
        return
    if d in ("remembered", "duplicate", "no_directive", "not_saved"):
        await context.bot.send_message(chat_id, dec["card"], reply_to_message_id=draft.get("card_msg_id"))
        return
    if d == "rejected":
        moderation_ipc.set_decision(draft["id"], "rejected", decided_by=by)
        await context.bot.send_message(chat_id, f"❌ Отклонено ({by})",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    if d in ("ready", "test_held"):
        moderation_ipc.set_decision(draft["id"], d, final_text=dec["final_text"], decided_by=by)
        await context.bot.send_message(chat_id, dec["card"] + f"\n— {by}",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return


# ---------------------- O3 кусок 1: карточка «Бронь» --------------------------

async def _post_booking_card(context, chat_id, draft, username):
    """Собрать карточку-ЧЕРНОВИК заявки из транскрипта диалога (тот же, что хранит IPC для
    перегенерации) и запостить менеджеру reply на карточку черновика. Ни строчки в CRM/IPC —
    только чтение цены через Bridge. Экстракция (claude CLI) — в отдельном потоке, чтобы не
    блокировать heartbeat/poll event-loop бота."""
    if not suggest.is_approver(username):
        await context.bot.send_message(chat_id, "⛔ Нет прав",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    transcript = draft.get("transcript") or ""
    if not transcript.strip():
        await context.bot.send_message(chat_id, "⚠️ Нет транскрипта диалога — заявку не собрать.",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    try:
        import asyncio
        card = await asyncio.to_thread(booking_draft.make_booking_card, transcript)
    except Exception as e:
        log.warning(f"booking card #{draft.get('id')}: {type(e).__name__}: {e}")
        await context.bot.send_message(chat_id, "⚠️ Не удалось собрать заявку (см. moderation_bot.log).",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    await context.bot.send_message(chat_id, card, reply_to_message_id=draft.get("card_msg_id"))


# ------------------------------- хендлеры ------------------------------------

async def on_callback(update, context):
    q = update.callback_query
    await q.answer()
    try:
        _, sid, action = (q.data or "").split(":", 2)
        draft = moderation_ipc.get(int(sid))
    except Exception:
        return
    if draft is None:
        return
    username = (q.from_user.username if q.from_user else None)
    if action == "booking":      # 📋 Бронь: экстракция заявки → карточка-черновик менеджеру (read-only)
        await _post_booking_card(context, q.message.chat_id, draft, username)
        return
    if action == "remember":     # 📌 захват правки в playbook (Фаза 2) — approver-гейт внутри
        dec = moderation_core.remember_rule(draft, username)
    else:
        dec = moderation_core.process_callback(draft, action, username, suggest.SUGGEST_TEST_MODE,
                                               candidate=draft.get("final_text"))
    dec["_by"] = f"@{username}" if username else "?"
    await _apply(context, q.message.chat_id, draft, dec)


async def on_group_message(update, context):
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None:
        return
    # запоминаем группу (для постинга, если MOD_GROUP_ID не задан)
    if chat.type in ("group", "supergroup"):
        moderation_ipc.set_meta("mod_chat", str(chat.id))
    if not msg.reply_to_message:
        return
    draft = moderation_ipc.draft_by_card(msg.reply_to_message.message_id)
    if draft is None:
        return  # reply не на нашу карточку
    username = (update.effective_user.username if update.effective_user else None)
    if msg.voice is not None:
        await context.bot.send_message(chat.id, "🎤 Голос пока не расшифровываю — ответьте текстом. "
                                       "Голосовую расшифровку добавим позже.",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    dec = moderation_core.process_reply(draft, msg.text or "", username, suggest.load_faq(),
                                        suggest.SUGGEST_TEST_MODE)
    dec["_by"] = f"@{username}" if username else "?"
    await _apply(context, chat.id, draft, dec)


# ---------------------------- обработчик ошибок (разбор #128) ----------------------------
# Причина «мгновенной смерти» класса: у moderation_bot НЕ было error-handler'а. Любая
# необработанная ошибка в polling-цикле PTB (сетевой сбой httpx.ConnectError/ReadError, а
# главное — Conflict «terminated by other getUpdates», когда рядом на миг оказывалась ВТОРАЯ
# копия бота) всплывала как unhandled → PTB писал «No error handlers are registered» и ронял
# процесс. В логе бота при этом пусто (падение вне его логгера) — оттого смерть выглядела
# «мгновенной без улик». Ставим handler по образцу pc_agent: сетевые/Conflict — пережить и
# продолжить (PTB переподключит long-polling), прочее — залогировать с трейсом (в файл, не в /dev/null).
async def on_error(update, context):
    from telegram.error import NetworkError, TimedOut, Conflict
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning(f"сетевая ошибка (не падаю, PTB переподключится): {type(err).__name__}: {err}")
    elif isinstance(err, Conflict):
        # вторая копия дёргает getUpdates → НЕ падаем: singleton-гард (ниже) на старте отсекает
        # второй экземпляр; если конфликт всё же мигнул — просто ждём, Telegram сам разведёт.
        log.warning(f"Conflict getUpdates (вероятно вторая копия) — не падаю, жду: {err}")
    else:
        log.error("необработанная ошибка", exc_info=err)


# ------------------------- singleton-гард (разбор #128) ----------------------
# Двух moderation_bot на одном MODERBOT_TOKEN быть не должно: два поллера → Telegram отдаёт
# Conflict, один из процессов умирает. Раньше защиты не было — pc_agent-старт и контур-вотчдог
# оркестратора могли на миг поднять второй экземпляр. Атомарный lock-файл с PID (как в pc_agent).
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moderation_bot.lock")


def _pid_alive(pid):
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=10)
        return f'"{pid}"' in r.stdout or f",{pid}," in r.stdout
    except Exception:
        return False


def acquire_lock():
    """True — лок наш; False — другой живой moderation_bot уже держит его (выходим)."""
    for _ in range(3):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(LOCK_FILE, encoding="utf-8") as f:
                    old = int(f.read().strip() or "0")
            except Exception:
                old = 0
            if old and _pid_alive(old):
                log.warning(f"moderation_bot уже запущен (PID {old}) — второй не поднимаю, выхожу.")
                return False
            log.info(f"устаревший moderation_bot.lock (PID {old or '?'} мёртв) — забираю.")
            try:
                os.remove(LOCK_FILE)
            except FileNotFoundError:
                pass
    return False


def release_lock():
    try:
        with open(LOCK_FILE, encoding="utf-8") as f:
            mine = int(f.read().strip() or "0") == os.getpid()
        if mine:
            os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def main():
    if not TOKEN:
        log.warning("MODERBOT_TOKEN отсутствует — бот-модератор НЕ запускается "
                    "(деградация: userbot в reply-режиме). Впишите MODERBOT_TOKEN в .env.")
        return
    if not suggest.is_enabled():
        log.warning("SUGGEST выключен (SUGGEST_MODE=off) — боту нечего модерировать, выхожу.")
        return
    # singleton-гард ДО init_db/polling: второй экземпляр не поднимаем (иначе Conflict getUpdates).
    if not acquire_lock():
        return
    try:
        moderation_ipc.init_db()

        from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CallbackQueryHandler(on_callback))
        app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, on_group_message))
        app.add_error_handler(on_error)   # разбор #128: сетевые/Conflict не роняют процесс
        if app.job_queue is not None:
            app.job_queue.run_repeating(job_heartbeat, interval=HEARTBEAT_SEC, first=1)
            app.job_queue.run_repeating(job_poll_new, interval=POLL_SEC, first=2)
        log.info(f"moderation_bot ЗАПУСК (TEST_MODE={suggest.SUGGEST_TEST_MODE}, "
                 f"mod_chat={_target_chat()}). Клиенту не пишу; решения — в IPC.")
        app.run_polling()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
