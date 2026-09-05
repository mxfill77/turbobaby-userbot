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
import booking_draft      # noqa: E402  (O3 кусок 1 «Кнопка Бронь»: экстракция заявки, read-only)
import trainer            # noqa: E402  (ГРУППА-ТРЕНАЖЁР: панель кнопок под ответом userbot)
import trainer_log        # noqa: E402  (ЛОГ ТРЕНАЖЁРА в мозг: нажатия кнопок и уроки; fail-safe)
import proc_identity      # noqa: E402  (ЛИЧНОСТЬ ПРОЦЕССА: номер + имя запуска + момент старта)

try:
    import log_setup                       # ротация + тестовый лог в temp (см. log_setup)
    _mb_h = log_setup.rotating_handler("moderation_bot.log")
except Exception:
    _mb_h = None
if _mb_h is None:
    _mb_h = logging.FileHandler("moderation_bot.log", encoding="utf-8")
    _mb_h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[_mb_h, logging.StreamHandler()],
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
    """Отразить decision-dict в IPC и в интерфейсе. Клиенту НИЧЕГО не шлём.

    → 'decided' (решение взято ЭТИМ нажатием) | 'closed' (за него уже решили) | None (решения
    не было вовсе: отказ прав, вопрос, правка). Значение нужно вызывающему, чтобы снять кнопки
    с закрытой карточки."""
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
    if d in ("rejected", "ready", "test_held"):
        # АТОМАРНЫЙ ЗАХВАТ РЕШЕНИЯ (06.09.2026). Раньше здесь стоял `set_decision` — UPDATE БЕЗ
        # предусловия по статусу, то есть решение ставилось из ЛЮБОГО состояния строки, включая
        # уже отправленное 'sent'. Именно этой дорогой к клиенту уходило ВТОРОЕ сообщение:
        # кнопки после решения не гасли, и повторный (или чужой) тап ✅ снова ставил 'ready'.
        # Теперь предусловие проверяет САМА СУБД внутри одного UPDATE — победитель ровно один
        # при любой гонке (двух модераторов, двух процессов, повторного тапа по старой карточке).
        # Проигравший НЕ получает «Принято»: ему говорится, что уже решено и КЕМ.
        won, row = moderation_ipc.claim_decision(
            draft["id"], d, final_text=(None if d == "rejected" else dec.get("final_text")),
            decided_by=by)
        if not won:
            log.info(f"решение по #{draft['id']} не взято ({by}): карточку уже закрыл "
                     f"{(row or {}).get('decided_by') or '?'} "
                     f"[{(row or {}).get('status') or 'строки нет'}]")
            await context.bot.send_message(chat_id, moderation_core.render_closed(row),
                                           reply_to_message_id=draft.get("card_msg_id"))
            return "closed"
        card = f"❌ Отклонено ({by})" if d == "rejected" else dec["card"] + f"\n— {by}"
        await context.bot.send_message(chat_id, card,
                                       reply_to_message_id=draft.get("card_msg_id"))
        return "decided"


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


def _kb_trainer_hyps(hyps, selected=None, per_row=5):
    """Кнопки гипотез = ТОЛЬКО номера [1]..[N] + [✍️ другое]: полные формулировки печатаются
    списком в самом сообщении (trainer.hyps_messages) — подпись inline-кнопки Telegram режет по
    ширине, и владелец не мог дочитать правило. callback_data прежний (tr:hyp:<i> / tr:hyp:other) —
    маршрутизация и старые обработчики не меняются.
    МУЛЬТИВЫБОР: номер — ТУМБЛЕР, отмеченный показываем как «✅N»; запись происходит по
    «✔ Применить», отмена выбора — «✖ Отмена» (промах пальцем больше не становится правилом)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    sel = set(int(x) for x in (selected or []))
    rows, row = [], []
    for i in range(len(hyps or [])):
        title = ("✅" + str(i + 1)) if i in sel else str(i + 1)
        row.append(InlineKeyboardButton(title, callback_data=f"tr:hyp:{i}"))
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✔ Применить", callback_data="tr:hyp:apply"),
                 InlineKeyboardButton("✖ Отмена", callback_data="tr:hyp:cancel")])
    rows.append([InlineKeyboardButton("✍️ другое", callback_data="tr:hyp:other")])
    return InlineKeyboardMarkup(rows)


async def _trn_log(kind, text, n=None):
    """Событие тренажёра → KB_trainer_log (Brain) ФОНОМ. КРИТИЧНО именно здесь: приложение
    собрано ApplicationBuilder().token(...).build() без concurrent_updates, то есть PTB обрабатывает
    апдейты ПОСЛЕДОВАТЕЛЬНО — зависшая на Bridge запись лога тренажёра тормозила бы модерацию
    РЕАЛЬНЫХ клиентов. Поэтому только ПЛАНИРУЕМ задачу и сразу возвращаемся."""
    import asyncio
    try:
        n = trainer.get_n() if n is None else n
    except Exception:
        n = 0

    async def _write():
        try:
            await asyncio.to_thread(trainer_log.safe_append, n, kind, text)
        except Exception as e:
            log.info(f"trainer_log: {type(e).__name__}: {e}")

    try:
        asyncio.create_task(_write())
    except RuntimeError:
        pass                      # цикла нет — лог наблюдения не критичен


async def _trainer_callback(context, q, data):
    """Нажатия панели тренажёра (tr:*). approver-гейт. Результаты (сброс/CRM-карта/гипотезы/
    принятое правило) постим В САМУ ГРУППУ тренажёра. Боевые «Входящие брони»/CRM не трогаем.
    Каждое нажатие и каждый урок пишем в KB_trainer_log (ТЗ п.9)."""
    import asyncio
    chat_id = q.message.chat_id
    username = (q.from_user.username if q.from_user else None)
    if not suggest.is_approver(username):
        await context.bot.send_message(chat_id, "⛔ Кнопки тренажёра — только для approver.")
        # отказ — тоже событие обучения: в TRN, чтобы «не жали» и «жали, но отказано» различались
        await _trn_log(trainer_log.KIND_BTN,
                       f"⛔ отказ: @{username or '?'} не approver — кнопка {data} не выполнена")
        return
    if data == "tr:reset":
        n = trainer.reset()
        await context.bot.send_message(chat_id, f"🔄 Новый клиент ТЕСТ-{n} — контекст (факты + память диалога) очищен.")
        await _trn_log(trainer_log.KIND_BTN, f"🔄 Заново → старт нового ТЕСТ-{n}", n)
        return
    if data == "tr:crm":
        transcript = trainer.get_transcript()
        if not transcript.strip():
            await context.bot.send_message(chat_id, "⚠️ Диалог ТЕСТ-клиента пуст — нечего заводить в CRM.")
            await _trn_log(trainer_log.KIND_BTN, "⚠️ 📋 До CRM: диалог пуст — карточка не собрана")
            return
        try:
            meta = {"transcript": transcript, "client_ref": "ТЕСТ", "client_name": "ТЕСТ"}
            card, intake_text = await asyncio.to_thread(
                booking_draft.make_booking_and_intake, transcript, None, None, None, None, meta)
        except Exception as e:
            log.warning(f"trainer crm: {type(e).__name__}: {e}")
            await context.bot.send_message(chat_id, "⚠️ Не удалось собрать заявку (см. moderation_bot.log).")
            await _trn_log(trainer_log.KIND_BTN,
                           f"⚠️ 📋 До CRM: сбой моста 2.1 — {type(e).__name__}: {e}")
            return
        body = (intake_text or card or "⚠️ Из диалога заявку собрать не удалось.").strip()
        await context.bot.send_message(chat_id, trainer.strip_thai(trainer.crm_card(body)))
        await _trn_log(trainer_log.KIND_BTN, "📋 До CRM → карточка [ТЕСТ] собрана")
        return
    if data == "tr:teach":
        incoming, answer = trainer.get_last_pair()
        if not (incoming and answer):
            await context.bot.send_message(chat_id, "⚠️ Нет последнего ответа ТЕСТ-клиента — сначала напиши как клиент.")
            await _trn_log(trainer_log.KIND_BTN,
                           "⚠️ 🎓 Обучить: нет последней пары клиент/бот — гипотезы не запрошены")
            return
        llm_err = None
        try:
            system, user = trainer.hypotheses_prompt(incoming, answer)
            llm = suggest.default_llm_caller()
            raw = await asyncio.to_thread(llm, system, user)
            hyps = trainer.parse_hypotheses(raw)
        except Exception as e:
            log.warning(f"trainer teach: {type(e).__name__}: {e}")
            hyps, llm_err = [], f"{type(e).__name__}: {e}"
        if not hyps:
            await context.bot.send_message(chat_id, "⚠️ Не удалось предложить гипотезы — напиши урок текстом: «урок: …».")
            # «LLM упал» и «LLM ответил пусто» — РАЗНЫЕ ветки, обе видны в TRN раздельно
            await _trn_log(trainer_log.KIND_BTN, "⚠️ 🎓 Обучить: " +
                           (f"сбой LLM гипотез — {llm_err}" if llm_err
                            else "LLM вернул 0 гипотез (пустой разбор)"))
            return
        trainer.set_hyps(hyps)                   # новый список → отметки тумблеров обнуляются
        parts = trainer.hyps_messages(hyps)      # полные формулировки — в тексте, кнопки = номера
        for p in parts[:-1]:
            await context.bot.send_message(chat_id, p)
        await context.bot.send_message(chat_id, parts[-1], reply_markup=_kb_trainer_hyps(hyps, []))
        await _trn_log(trainer_log.KIND_BTN,
                       "🎓 Обучить → гипотезы: " + " | ".join(f"{i + 1}. {h}"
                                                              for i, h in enumerate(hyps)))
        return
    if data.startswith("tr:hyp:"):
        sel = data[len("tr:hyp:"):]
        hyps = trainer.get_hyps()
        if sel == "other":
            # БЕЗ ПРЕФИКСА: ставим ожидание, следующее текстовое сообщение владельца станет
            # правилом ЦЕЛИКОМ и ДОСЛОВНО (голосовой ввод/опечатки не правим). Ловит userbot —
            # он видит в группе ВСЕ сообщения; TTL ожидания 10 минут.
            trainer.start_pending_lesson(username)
            await context.bot.send_message(
                chat_id, "✍️ Слушаю: напиши правило СЛЕДУЮЩИМ сообщением — запишу его целиком, "
                         "как сказано (префикс «урок:» не нужен). Жду 10 минут.")
            await _trn_log(trainer_log.KIND_BTN, "✍️ другое → жду свободный текст правила (10 мин)")
            return
        if sel == "cancel":
            trainer.set_selection([])
            try:
                await q.edit_message_reply_markup(reply_markup=_kb_trainer_hyps(hyps, []))
            except Exception:
                pass
            await context.bot.send_message(chat_id, "✖ Отменено — ничего не записано.")
            await _trn_log(trainer_log.KIND_BTN, "✖ Отмена → отметки сняты, ничего не записано")
            return
        if sel == "apply":
            picked = trainer.selected_hypotheses()
            dec = await asyncio.to_thread(trainer.apply_lessons, picked)
            trainer.set_selection([])
            try:
                await q.edit_message_reply_markup(reply_markup=_kb_trainer_hyps(hyps, []))
            except Exception:
                pass
            await context.bot.send_message(chat_id, dec["card"])   # ОДНО сообщение со списком
            await _trn_log(trainer_log.KIND_LESSON, "✔ Применить → " + dec["card"])
            return
        try:
            i = int(sel)
        except ValueError:
            return
        if not (0 <= i < len(hyps)):
            await context.bot.send_message(chat_id, "⚠️ Гипотеза устарела — нажми «🎓 Обучить» заново.")
            await _trn_log(trainer_log.KIND_BTN,
                           f"⚠️ тап по устаревшей гипотезе №{i + 1} (актуальных: {len(hyps)}) — не применена")
            return
        # ТУМБЛЕР: тап помечает/снимает ✅ прямо на кнопке; в книгу правил пока НИЧЕГО не пишем.
        picked = trainer.toggle_selection(i)
        try:
            await q.edit_message_reply_markup(reply_markup=_kb_trainer_hyps(hyps, picked))
        except Exception as e:
            log.warning(f"trainer hyp toggle: {e}")
        mark = "отмечена" if i in picked else "снята"
        await _trn_log(trainer_log.KIND_BTN,
                       f"номер {i + 1} {mark} (отмечено сейчас: "
                       f"{', '.join(str(x + 1) for x in picked) or '—'})")
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
    outcome = await _apply(context, q.message.chat_id, draft, dec)
    if outcome in ("decided", "closed"):
        # Кнопки с ЗАКРЫТОЙ карточки снимаем (закрыли мы или тот, кто успел раньше — неважно):
        # иначе карточка недельной давности остаётся на вид рабочей и приглашает на второй тап.
        # Это УДОБСТВО, а не замок: замок — предусловие в СУБД, и он держит даже если снять
        # кнопки не удалось (Telegram недоступен, сообщение слишком старое для правки).
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            log.info(f"кнопки карточки #{draft['id']} не сняты ({type(e).__name__}: {e}) — "
                     "замок решения от этого не слабеет, он в СУБД")


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
# оркестратора могли на миг поднять второй экземпляр. Атомарный lock-файл (как в pc_agent).
#
# 30.08.2026 — ГАРД НЕ СНЯТ И НЕ ОСЛАБЛЕН, А УСИЛЕН ВТОРОЙ ПРИМЕТОЙ. Прежде живость владельца
# решалась ОДНИМ номером (`tasklist /FI "PID eq N"`), а номер уникален только СРЕДИ ЖИВЫХ:
# после ребута 26.08 номер 18200 из лока достался `PinWin.exe` (создан на 70 с позже загрузки),
# и гард честно докладывал «уже запущен» ТРОЕ СУТОК, пока модербот лежал. Теперь личность
# владельца — номер + имя запуска + момент старта, одним общим правилом полосы
# (`proc_identity`). Заодно закрыт второй, тихий вход в ту же беду: прежний `_pid_alive`
# глотал СВОЙ отказ в False, то есть таймаут `tasklist` на просыпающемся ПК читался как
# «владелец мёртв» и уводил в КРАЖУ лока — ровно в двойной запуск, от которого гард и стои́т.
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moderation_bot.lock")


def acquire_lock():
    """True — лок наш; False — другой живой moderation_bot уже держит его (выходим).

    Стухший лок опознаёт и снимает САМА программа при старте — руками делать ничего не надо;
    снятый лок уезжает уликой в `tmp/stale_locks/`, потому что каждый такой файл — живой случай
    переиспользования номера. «Занят» и «не смог проверить» разведены и ОБА не дают старта."""
    ok, verdict, why = proc_identity.acquire(
        LOCK_FILE, script=os.path.basename(__file__),
        log=lambda m: log.info(f"moderation_bot.lock: {m}"))
    if ok:
        return True
    if verdict == proc_identity.OURS_ALIVE:
        log.warning(f"moderation_bot уже запущен — второй не поднимаю, выхожу. {why}")
    else:
        log.warning(f"moderation_bot НЕ стартую [{verdict}]: {why}")
    return False


def release_lock():
    """Снять лок, только если он наш — по номеру И моменту старта. Голого номера тут мало по
    той же причине, что и при заборе: после рестарта чужой процесс может носить наш прежний
    номер, и снятие его лока открыло бы дорогу второму экземпляру."""
    try:
        proc_identity.release(LOCK_FILE)
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
