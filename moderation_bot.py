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
import trainer            # noqa: E402  (ГРУППА-ТРЕНАЖЁР: панель кнопок под ответом userbot)

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
    """Куда постить карточки: MOD_GROUP_ID из env или чат, где бота уже видели.
    ЖЁСТКИЙ белый список назначения (протечка 22.07 14:25: mod_chat оказался отравлен id группы
    ТРЕНАЖЁРА → боевые карточки клиентов ушли в «Тренеровку»): группа тренажёра НИКОГДА не может
    быть целью боевых карточек — совпадение цели с trainer chat_id ⇒ None (карточки ждут в
    очереди, протечки нет), громкий лог."""
    if MOD_GROUP_ID is not None:
        target = MOD_GROUP_ID
    else:
        v = moderation_ipc.get_meta("mod_chat")
        target = int(v) if v and v.lstrip("-").isdigit() else None
    if target is not None and trainer.is_trainer_chat(target):
        log.error(f"mod_chat={target} указывает на группу ТРЕНАЖЁРА — боевые карточки туда НЕ шлю "
                  "(белый список назначения); жду перепривязки mod_chat.")
        return None
    return target


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
        # STAFF-SKIP (слой 3, инцидент 22.07 @extthiwxer=Earth): внутренний аккаунт команды →
        # карточка модерации НЕ создаётся ВОВСЕ (даже если черновик как-то попал в очередь до
        # ужесточения реестра). Черновик закрываем rejected с причиной — не висит вечно new.
        try:
            if suggest.is_internal_user_id(r.get("client_id")):
                moderation_ipc.mark(r["id"], "rejected", reason="internal staff (STAFF-SKIP)")
                log.info(f"STAFF-SKIP: черновик #{r['id']} от внутреннего id{r.get('client_id')} "
                         f"({r.get('client_ref')}) — карточку модерации НЕ пощу.")
                continue
        except Exception as e:
            log.warning(f"STAFF-SKIP чек #{r.get('id')}: {e}")
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

INTAKE_ENQUEUE_TIMEOUT = 10   # с; ожидание подтверждения записи в очередь (IPC мёртв → честная ошибка)


def _kb_intake(intake_id):
    """Кнопка «✅ В CRM» на карточке ЗАЯВКА (по тапу — пост во «Входящие брони» userbot-аккаунтом)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ В CRM", callback_data=f"crm:{intake_id}")]])


async def _post_booking_card(context, chat_id, draft, username):
    """Собрать карточку-ЧЕРНОВИК заявки из транскрипта диалога (тот же, что хранит IPC для
    перегенерации) и запостить менеджеру reply на карточку черновика. Ни строчки в CRM/IPC —
    только чтение цены через Bridge. Экстракция (claude CLI) — в отдельном потоке, чтобы не
    блокировать heartbeat/poll event-loop бота. Если из заявки собрался валидный пост «🆕 БРОНЬ»,
    вешаем кнопку «✅ В CRM» (кандидат сохраняем в очередь intake со статусом draft)."""
    if not suggest.is_approver(username):
        await context.bot.send_message(chat_id, "⛔ Нет прав",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    transcript = draft.get("transcript") or ""
    if not transcript.strip():
        await context.bot.send_message(chat_id, "⚠️ Нет транскрипта диалога — заявку не собрать.",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    meta = {"transcript": transcript, "client_ref": draft.get("client_ref"),
            "client_name": draft.get("client_name")}
    try:
        import asyncio
        card, intake_text = await asyncio.to_thread(
            booking_draft.make_booking_and_intake, transcript, None, None, None, None, meta)
    except Exception as e:
        log.warning(f"booking card #{draft.get('id')}: {type(e).__name__}: {e}")
        await context.bot.send_message(chat_id, "⚠️ Не удалось собрать заявку (см. moderation_bot.log).",
                                       reply_to_message_id=draft.get("card_msg_id"))
        return
    kb = None
    if intake_text:                                   # есть валидный пост → сохраняем кандидат + кнопка
        try:
            # client_id (O3-2.1): userbot после поста «🆕 БРОНЬ» перешлёт из диалога ЭТОГО
            # клиента фото паспорта (если было) сразу за карточкой — Splinter привяжет (окно 5 мин).
            intake_id = moderation_ipc.save_intake_candidate(
                intake_text, client_id=draft.get("client_id"))
            kb = _kb_intake(intake_id)
        except Exception as e:
            log.warning(f"intake candidate #{draft.get('id')}: {type(e).__name__}: {e}")
    await context.bot.send_message(chat_id, card, reply_markup=kb,
                                   reply_to_message_id=draft.get("card_msg_id"))


async def _confirm_intake(context, q, data):
    """Тап «✅ В CRM»: подтверждаем запись в очередь (draft→pending) с таймаутом; userbot-аккаунт
    заберёт и запостит во «Входящие брони». С ПК В CRM НЕ пишем — финальное «да» за авторизатором
    в группе. Сбой/таймаут записи в очередь → честная ошибка на карточке, бот НЕ падает."""
    import asyncio
    username = (q.from_user.username if q.from_user else None)
    if not suggest.is_approver(username):
        await context.bot.send_message(q.message.chat_id, "⛔ Нет прав на отправку в CRM")
        return
    try:
        intake_id = int(data.split(":", 1)[1])
    except Exception:
        return
    try:
        ok = await asyncio.wait_for(
            asyncio.to_thread(moderation_ipc.confirm_intake, intake_id),
            timeout=INTAKE_ENQUEUE_TIMEOUT)
    except Exception as e:   # IPC мёртв / таймаут записи в очередь — честная ошибка, без падения
        log.warning(f"intake confirm #{intake_id}: {type(e).__name__}: {e}")
        try:
            await q.edit_message_reply_markup(reply_markup=_kb_intake(intake_id))  # кнопку оставляем (ретрай)
        except Exception:
            pass
        await context.bot.send_message(
            q.message.chat_id,
            "⚠️ Не удалось поставить заявку в очередь отправки (IPC недоступен) — НЕ отправлено, попробуй ещё раз.")
        return
    if not ok:   # нет draft-записи или уже ушла
        await context.bot.send_message(q.message.chat_id, "⚠️ Заявка уже отправлена или устарела — пропускаю.")
        return
    base = (q.message.text or "").strip()
    note = ("\n\n⏳ Отправляю во «Входящие брони» (userbot-аккаунтом) — подтверди там своим «да» "
            "(ты авторизатор); шли по одной, дожидайся ✅/❌.")
    try:
        await q.edit_message_text(base + note)       # карточку редактируем, кнопку убираем (без дублей)
    except Exception:
        await context.bot.send_message(q.message.chat_id, note.strip())


# ---------------------- ГРУППА-ТРЕНАЖЁР: панель кнопок ------------------------
# Единственная роль модербота в группе «Тренеровка»: под КАЖДЫМ ответом userbot (шапка
# «[тренажёр …») повесить панель [🔄 Заново][📋 До CRM][🎓 Обучить] и обработать нажатия.
# Клиентские ответы шлёт ТОЛЬКО userbot; модербот в диалог ТЕСТ-клиента текстов НЕ пишет.
# Права на кнопки — approver (в группе это владелец). Дубль кнопок ТЕКСТ-командами держит
# userbot (работают, даже если модербот лёг).

def _kb_trainer_panel():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Заново", callback_data="tr:reset"),
        InlineKeyboardButton("📋 До CRM", callback_data="tr:crm"),
        InlineKeyboardButton("🎓 Обучить", callback_data="tr:teach"),
    ]])


def _kb_trainer_hyps(hyps, per_row=5):
    """Кнопки гипотез = ТОЛЬКО номера [1]..[N] + [✍️ другое]: полные формулировки печатаются
    списком в самом сообщении (trainer.hyps_messages) — подпись inline-кнопки Telegram режет по
    ширине, и владелец не мог дочитать правило. callback_data прежний (tr:hyp:<i> / tr:hyp:other) —
    маршрутизация и старые обработчики не меняются."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows, row = [], []
    for i in range(len(hyps or [])):
        row.append(InlineKeyboardButton(str(i + 1), callback_data=f"tr:hyp:{i}"))
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✍️ другое", callback_data="tr:hyp:other")])
    return InlineKeyboardMarkup(rows)


async def _trainer_callback(context, q, data):
    """Нажатия панели тренажёра (tr:*). approver-гейт. Результаты (сброс/CRM-карта/гипотезы/
    принятое правило) постим В САМУ ГРУППУ тренажёра. Боевые «Входящие брони»/CRM не трогаем."""
    import asyncio
    chat_id = q.message.chat_id
    username = (q.from_user.username if q.from_user else None)
    if not suggest.is_approver(username):
        await context.bot.send_message(chat_id, "⛔ Кнопки тренажёра — только для approver.")
        return
    if data == "tr:reset":
        n = trainer.reset()
        await context.bot.send_message(chat_id, f"🔄 Новый клиент ТЕСТ-{n} — контекст (факты + память диалога) очищен.")
        return
    if data == "tr:crm":
        transcript = trainer.get_transcript()
        if not transcript.strip():
            await context.bot.send_message(chat_id, "⚠️ Диалог ТЕСТ-клиента пуст — нечего заводить в CRM.")
            return
        try:
            meta = {"transcript": transcript, "client_ref": "ТЕСТ", "client_name": "ТЕСТ"}
            card, intake_text = await asyncio.to_thread(
                booking_draft.make_booking_and_intake, transcript, None, None, None, None, meta)
        except Exception as e:
            log.warning(f"trainer crm: {type(e).__name__}: {e}")
            await context.bot.send_message(chat_id, "⚠️ Не удалось собрать заявку (см. moderation_bot.log).")
            return
        body = (intake_text or card or "⚠️ Из диалога заявку собрать не удалось.").strip()
        await context.bot.send_message(chat_id, trainer.strip_thai(trainer.crm_card(body)))
        return
    if data == "tr:teach":
        incoming, answer = trainer.get_last_pair()
        if not (incoming and answer):
            await context.bot.send_message(chat_id, "⚠️ Нет последнего ответа ТЕСТ-клиента — сначала напиши как клиент.")
            return
        try:
            system, user = trainer.hypotheses_prompt(incoming, answer)
            llm = suggest.default_llm_caller()
            raw = await asyncio.to_thread(llm, system, user)
            hyps = trainer.parse_hypotheses(raw)
        except Exception as e:
            log.warning(f"trainer teach: {type(e).__name__}: {e}")
            hyps = []
        if not hyps:
            await context.bot.send_message(chat_id, "⚠️ Не удалось предложить гипотезы — напиши урок текстом: «урок: …».")
            return
        trainer.set_hyps(hyps)
        parts = trainer.hyps_messages(hyps)      # полные формулировки — в тексте, кнопки = номера
        for p in parts[:-1]:
            await context.bot.send_message(chat_id, p)
        await context.bot.send_message(chat_id, parts[-1], reply_markup=_kb_trainer_hyps(hyps))
        return
    if data.startswith("tr:hyp:"):
        sel = data[len("tr:hyp:"):]
        if sel == "other":
            await context.bot.send_message(chat_id, "✍️ Напиши правило текстом: «урок: <твоё правило>».")
            return
        try:
            i = int(sel)
        except ValueError:
            return
        hyps = trainer.get_hyps()
        if not (0 <= i < len(hyps)):
            await context.bot.send_message(chat_id, "⚠️ Гипотеза устарела — нажми «🎓 Обучить» заново.")
            return
        dec = await asyncio.to_thread(trainer.apply_lesson, hyps[i])
        await context.bot.send_message(chat_id, dec["card"])
        return


# ------------------------------- хендлеры ------------------------------------

async def on_callback(update, context):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data.startswith("tr:"):    # ГРУППА-ТРЕНАЖЁР: панель [Заново][До CRM][Обучить] и гипотезы
        await _trainer_callback(context, q, data)
        return
    if data.startswith("crm:"):   # O3-2c: «✅ В CRM» — своя маршрутизация (не m:{id}:action)
        await _confirm_intake(context, q, data)
        return
    try:
        _, sid, action = data.split(":", 2)
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
    # ГРУППА-ТРЕНАЖЁР: если это привязанная группа тренажёра ИЛИ группа с её title (защита ДО
    # привязки — корень протечки 22.07: модербота добавили в «Тренеровку», первое сообщение пришло
    # раньше привязки userbot'ом, is_trainer_chat=False → mod_chat выучился = id тренажёра, и
    # боевые карточки уехали туда) — единственная роль модербота тут панель кнопок под ответом
    # userbot (шапка «[тренажёр …»). Никакой модерации и НИКОГДА не учим mod_chat. Выходим сразу.
    if trainer.is_trainer_chat(chat.id) or trainer.title_matches(getattr(chat, "title", "") or ""):
        if trainer.has_header(msg.text or ""):
            try:
                await context.bot.send_message(
                    chat.id, "⬆️ управление ТЕСТ-клиентом:",
                    reply_to_message_id=msg.message_id, reply_markup=_kb_trainer_panel())
            except Exception as e:
                log.warning(f"trainer panel: {e}")
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
    # перехват реплая-обучения (родитель 292): «правка:»/«урок:»/«не так:» — не обычная правка
    # черновика, а замечание в копилку обучения; права строже approve (только INTAKE_APPROVERS).
    # submit_lesson (шаг 2): распознанный урок → ШТАТНАЯ задача в очереди дирижёра (enqueue, гейт
    # НЕ обходим — исполняет демон). Прямо ничего не исполняем здесь.
    lesson = moderation_core.submit_lesson(draft, msg.text or "", username)
    if lesson["decision"] != "not_lesson":
        if lesson["decision"] == "lesson":
            log.info(f"LESSON[{lesson['kind']}] окно={lesson.get('window')} draft#{lesson.get('draft_id')} "
                     f"от @{username}: {lesson.get('remark')} → queued={lesson.get('queued')} "
                     f"task#{lesson.get('task_id')}")
        await context.bot.send_message(chat.id, lesson["card"],
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
