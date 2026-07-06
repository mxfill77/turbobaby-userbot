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

from dotenv import load_dotenv

load_dotenv()

import suggest            # noqa: E402  (после load_dotenv — читает конфиг из окружения)
import moderation_core    # noqa: E402
import moderation_ipc     # noqa: E402

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
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data="m:{id}:yes"),
        InlineKeyboardButton("❌ Отклонить", callback_data="m:{id}:no"),
    ]])


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


def main():
    if not TOKEN:
        log.warning("MODERBOT_TOKEN отсутствует — бот-модератор НЕ запускается "
                    "(деградация: userbot в reply-режиме). Впишите MODERBOT_TOKEN в .env.")
        return
    if not suggest.is_enabled():
        log.warning("SUGGEST выключен (SUGGEST_MODE=off) — боту нечего модерировать, выхожу.")
        return
    moderation_ipc.init_db()

    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, MessageHandler, filters
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, on_group_message))
    if app.job_queue is not None:
        app.job_queue.run_repeating(job_heartbeat, interval=HEARTBEAT_SEC, first=1)
        app.job_queue.run_repeating(job_poll_new, interval=POLL_SEC, first=2)
    log.info(f"moderation_bot ЗАПУСК (TEST_MODE={suggest.SUGGEST_TEST_MODE}, "
             f"mod_chat={_target_chat()}). Клиенту не пишу; решения — в IPC.")
    app.run_polling()


if __name__ == "__main__":
    main()
