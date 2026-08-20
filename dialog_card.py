# -*- coding: utf-8 -*-
"""
dialog_card.py — КАРТОЧКА НА РАЗГОВОР, а не на сообщение.

ПОЧЕМУ ЕДИНИЦА СМЕНИЛАСЬ (замер 20.08.2026 по 710 живым диалогам,
`docs/artifacts/2026-08-20-dialogs-vs-card.md` §7 и §3):

    до запроса данных на бронь не доходит НИ ОДИН диалог быстрее ЧЕТЫРЁХ ходов
    (0 из 97), медиана — ДВЕНАДЦАТЬ ходов, максимум 58; даже первая названная цена
    приходит первым же ответом лишь в 11.2 % случаев;
    первый вопрос клиента МНОГОТЕМНЫЙ у 71 % входящих (2 темы — 140 диалогов,
    3 — 107, 4 — 32, 5+ — 11).

Карточка, живущая один обмен, меряет не ту величину: она показывает человеку 1/12 разговора и
молчит о том, на какие из четырёх заданных тем бот не ответил. Единица — РАЗГОВОР
(решение владельца 20.08.2026, берётся как данность).

ЧТО ЭТОТ МОДУЛЬ ДЕЛАЕТ

1. **Одна карточка на диалог**, ключ — `client_id`. Живёт, пока идёт разговор. Показывает:
   с чего клиент начал · что уже отправлено ему нами · что бот предлагает ответить СЕЙЧАС ·
   почему именно так.
2. **История сжата, а не вырезана**: последние ходы целиком, ранние — по одной строке, самые
   старые — одной считающей строкой. Ход = склейка подряд идущих реплик одной стороны, ровно
   то определение, которым считались 1269 ходов замера (иначе «медиана 12» и «ходов в карточке»
   мерили бы разными линейками).
3. **Клиент дописал, пока карточка ждала** — главный новый случай. Предложение пересобирается
   под новое сообщение, а прежнее НЕПРИНЯТОЕ становится устаревшим и отправлено быть НЕ МОЖЕТ.
   Замок живёт НЕ здесь, а в СУБД (`moderation_ipc.supersede_open`, UPDATE с предусловием по
   статусу — тот же приём, что у `claim_decision`): устаревшая строка выпадает из
   `CLAIMABLE_FROM`, и любое нажатие по ней возвращает «закрыто», сколько бы их ни было.
   Порядок в `accept_client_message` обязателен: СНАЧАЛА гасим, ПОТОМ ставим новую строку —
   иначе между двумя действиями существует окно, в котором отправляемы ОБА предложения.
4. **Захват первым ответившим остаётся как есть** — своей копии замка здесь нет, решение
   по-прежнему принимает `moderation_card.decide` (единственная дорога к `ready` во всей полосе).
5. **Многотемный вопрос виден**: по каждой теме, которую клиент поднял, карточка говорит одно из
   трёх — уже отвечено · закроет это предложение · БЕЗ ОТВЕТА.

ЧЕГО МОДУЛЬ НЕ ДЕЛАЕТ (честно, как и `moderation_card`): он НИЧЕГО не отправляет и отправлять
не умеет — канала нет ни одного. Ни таймаута, ни ретрая, ни ветки «никто не ответил — шлём
сами» здесь нет: нет входа, из которого такая ветка могла бы стартовать. Он также не постит
карточку в Telegram и не рисует кнопок — это работа `moderation_bot`, которого этот заход НЕ
трогает и на живых ботов НЕ выкатывает; точки подключения названы в
`docs/artifacts/2026-08-20-card-per-dialog.md`.
"""

import re

import card_load
import moderation_card
import suggest

# ------------------------------- ходы разговора -------------------------------

TURN_CLIENT = "client"      # реплики клиента
TURN_US = "company"         # наши реплики (бот и ручные ответы менеджера — один аккаунт)
TURN_UNKNOWN = None         # сторона не размечена: строка до первого маркера транскрипта

_MARK_CLIENT = "[клиент]:"
_MARK_US = "[менеджер]:"

# Потолки показа. Не «предел ожидания» (его у карточки нет вовсе) — предел ДЛИНЫ текста:
# сообщение Telegram ограничено 4096 символами, а разговор ходов на 58 в него не влезает.
TAIL_TURNS = 3        # последние ходы — ЦЕЛИКОМ
EARLY_LINES = 6       # ранние ходы — по ОДНОЙ строке каждый
TURN_CLIP = 600       # потолок одного «целого» хода; обрезка называется вслух
EARLY_CLIP = 90       # потолок однострочного раннего хода
HEAD_CLIP = 400       # потолок раздела «с чего клиент начал»
STALE_SHOWN = 3       # сколько устаревших предложений называем по id (остальные — числом)


def _s(value):
    """Значение → строка. None приводим к пустой строке ЯВНО, отдельной веткой: «поля нет» и
    «поле пустое» вызывающий обязан уметь различать."""
    if value is None:
        return ""
    return str(value).strip()


def _get(rec, key):
    """Поле записи → значение | None. Отсутствующая запись целиком — это «не знаю» по каждому
    полю, а не «поля пустые» (та же развилка, что у `moderation_card._get`)."""
    if rec is None:
        return None
    return rec.get(key)


def _clip(text, limit):
    """Обрезать до `limit` символов, СКАЗАВ об этом. Молчаливая обрезка врёт человеку тем, что
    он видит целый ход там, где показана половина."""
    t = _s(text)
    if len(t) <= limit:
        return t
    return t[:limit] + "… [обрезано, полностью " + str(len(t)) + " симв.]"


def _one_line(text, limit):
    """Ход в ОДНУ строку (переносы схлопнуты) — форма ранней истории."""
    return _clip(" ".join(_s(text).split()), limit)


def turns_from_transcript(transcript):
    """Транскрипт userbot → ХОДЫ разговора. → кортеж {who, text}.

    Формат источника — `suggest.transcript_from`: строки «[клиент]: …» / «[менеджер]: …» в
    хронологическом порядке, по одной на сообщение (переносы внутри реплики заменены на ' ⏎ ').
    ХОД = склейка подряд идущих реплик ОДНОЙ стороны; человек бьёт вопрос на 2–3 сообщения, и
    счёт по сообщениям завысил бы длину разговора вдвое.

    Строка без маркера (перенос, приехавший из чужого источника) НЕ теряется молча: она либо
    приклеивается к текущему ходу, либо, если хода ещё нет, открывает ход со стороной
    TURN_UNKNOWN — такой ход не считается ни клиентским, ни нашим ни в одном разрезе."""
    out = []
    who = TURN_UNKNOWN
    buf = []
    started = False
    for raw in _s(transcript).split("\n"):
        line = raw.strip()
        if len(line) == 0:
            continue
        if line.startswith(_MARK_CLIENT):
            side, body = TURN_CLIENT, line[len(_MARK_CLIENT):].strip()
        elif line.startswith(_MARK_US):
            side, body = TURN_US, line[len(_MARK_US):].strip()
        else:
            if started:
                buf.append(line)
                continue
            side, body = TURN_UNKNOWN, line
        if started and side == who:
            buf.append(body)
            continue
        if started:
            out.append({"who": who, "text": "\n".join(buf)})
        who, buf, started = side, [body], True
    if started:
        out.append({"who": who, "text": "\n".join(buf)})
    return tuple(out)


def first_client_turn(turns):
    """С чего клиент НАЧАЛ разговор. → текст | None (диалог начали мы — 285 из 710 в замере)."""
    for t in turns:
        if t["who"] == TURN_CLIENT:
            return t["text"]
    return None


# ------------------------------- темы разговора -------------------------------
# Словарь ЗАМЕРА 20.08 перенесён ДОСЛОВНО из `_scratch_dlgcard_0820/s5_turns.py` — того самого,
# которым считались 1269 ходов до вехи «запрос данных на бронь» и доли тем в них
# (наличие/модель 29.5 % · даты/срок 21.1 % · доставка/адрес 19.1 % · цена/расчёт 18.0 % ·
# деньги/оплата 16.7 % · документы/бронь 5.8 % · шлемы/комплект 4.6 % · фото/видео 3.5 %).
# Завести здесь СВОЙ словарь нельзя: тогда «71 % первых вопросов многотемные» и «темы в
# карточке» считались бы разными линейками, и расхождение было бы невидимым.


def _rx(pattern):
    return re.compile(pattern, re.I | re.U)


TOPICS = (
    ("цена/расчёт", _rx(r"стоимост|цена|ц[еэ]н[ыу]|бат\b|฿|скидк|тариф|прайс|дней\s*:")),
    ("наличие/модель", _rx(r"есть\s+в\s+налич|свободн|занят|доступн|nmax|xmax|forza|adv|xadv|pcx|"
                           r"cbr|cb\s*\d|xsr|ninja|mt-?03|скутер|мотоцикл|байк")),
    ("даты/срок", _rx(r"с\s+\d{1,2}\W|по\s+\d{1,2}\W|\d{1,2}[./]\d{1,2}|на\s+\d+\s*(дн|сут|недел|мес)|"
                      r"числ[аое]|дат[ауые]")),
    ("доставка/адрес", _rx(r"доставк|привез|привоз|подвез|адрес|апартамент|отел|локац|район|самовывоз")),
    ("шлемы/комплект", _rx(r"шлем|держател|кофр|замок")),
    ("документы/бронь", _rx(r"паспорт|документ|договор|контракт|брон|оформ")),
    ("деньги/оплата", _rx(r"оплат|перевод|предоплат|депозит|залог|наличн|крипт|рубл|usdt")),
    ("фото/видео", _rx(r"фото|видео|photo|скинь\w*\s+фото|пришл\w+\s+фото")),
)

TOPIC_NAMES = tuple(name for name, _pattern in TOPICS)

TOPIC_DONE = "done"      # тема закрыта нашим ходом ПОСЛЕ вопроса — отвечено
TOPIC_NOW = "now"        # тему закроет ТЕКУЩЕЕ предложение (ещё НЕ отправленное!)
TOPIC_OPEN = "open"      # тема поднята и НЕ закрыта ничем — вот она, дырка

TOPIC_MARK = {
    TOPIC_DONE: "✅ уже отвечено",
    TOPIC_NOW: "➡️ закроет это предложение",
    TOPIC_OPEN: "❗ БЕЗ ОТВЕТА",
}

# Признак СЛОВАРНЫЙ, и врать он умеет в ОБЕ стороны — карточка обязана сказать это человеку, а
# не выдавать галочку за доказательство. Обе стороны наблюдаются на живой фикстуре и закреплены
# тестом `test_known_skew_of_the_word_based_check_is_documented`:
#   • лишнее «❗» — наша строка инструмента «| дней: 7 — 4928 ฿» темы «даты/срок» словарём НЕ
#     задевает (у замера этот текст относится к «цена/расчёт»), и срок покажется незакрытым;
#   • ложное «✅» — реплика «про доставку напишу позже» слово «доставк» СОДЕРЖИТ, и тема
#     засчитается закрытой, хотя ответа в ней нет.
# Расширять словарь ради этого нельзя: он один на замер и на карточку, иначе «71 % многотемных»
# и «темы карточки» разъедутся молча. Перекос назван вслух — это дешевле и честнее.
TOPIC_CAVEAT = ("   (темы опознаны словарём замера: «❗» бывает лишним, «✅» — не доказательство "
                "полноты ответа; спорное решает человек)")


def topics_of(text):
    """Темы, которые задевает текст. → кортеж имён в порядке словаря (карточка не должна
    «прыгать» списком от хода к ходу)."""
    t = _s(text)
    if len(t) == 0:
        return ()
    return tuple(name for name, pattern in TOPICS if pattern.search(t) is not None)


def topic_ledger(turns, proposal=None):
    """Что клиент спросил и что из этого закрыто. → кортеж {topic, state, asked_turn}.

    Тема СПРОШЕНА в ходе i, если её задевает клиентский ход i. Она ЗАКРЫТА, если её задевает
    хоть один НАШ ход ПОСЛЕ i (позже — значит в ответ; наш ход ДО вопроса ответом не является).
    Открытую тему, которую задевает текущее предложение, помечаем `now` — «закроет», а не
    «закрыто»: предложение ещё не отправлено, и нажатия может не быть.

    Словарь тем — из замера, и он словарный: тему, которой в нём нет, карточка не увидит. Это
    названо вслух в самой карточке, когда у клиентских ходов не опозналось НИ ОДНОЙ темы."""
    asked = {}
    ours = {}
    for i, t in enumerate(turns):
        names = topics_of(t["text"])
        if t["who"] == TURN_CLIENT:
            for name in names:
                if name not in asked:
                    asked[name] = i
            continue
        if t["who"] == TURN_US:
            for name in names:
                ours.setdefault(name, []).append(i)
    if proposal is None:
        planned = ()
    else:
        planned = topics_of(proposal)
    out = []
    for name in TOPIC_NAMES:
        if name not in asked:
            continue
        i = asked[name]
        later = [j for j in ours.get(name, []) if j > i]
        if len(later) > 0:
            state = TOPIC_DONE
        elif name in planned:
            state = TOPIC_NOW
        else:
            state = TOPIC_OPEN
        out.append({"topic": name, "state": state, "asked_turn": i + 1})
    return tuple(out)


def unanswered(ledger):
    """Темы, которые останутся без ответа даже если предложение отправить. → кортеж имён."""
    return tuple(r["topic"] for r in ledger if r["state"] == TOPIC_OPEN)


# ------------------------------- сжатая история -------------------------------

_SIDE_RU = {TURN_CLIENT: "КЛИЕНТ", TURN_US: "МЫ", TURN_UNKNOWN: "?"}
_SIDE_SHORT = {TURN_CLIENT: "клиент", TURN_US: "мы", TURN_UNKNOWN: "?"}


def compress_history(turns, tail=TAIL_TURNS, early=EARLY_LINES):
    """Сжатая история разговора. → список строк.

    Ход №1 здесь НЕ повторяется: он целиком живёт в разделе «с чего клиент начал» и является
    якорем карточки. Дальше три яруса — самые старые ходы одной СЧИТАЮЩЕЙ строкой, ранние по
    одной строке каждый, последние `tail` ходов целиком. Человек обязан видеть, КУДА идёт
    разговор, а не читать простыню на 58 ходов."""
    rest = list(turns[1:])
    if len(rest) == 0:
        return ["(разговор только начался — дальше первого хода он не ушёл)"]
    if tail < 0:
        tail = 0
    if len(rest) > tail:
        head_part, tail_part = rest[:len(rest) - tail], rest[len(rest) - tail:]
    else:
        head_part, tail_part = [], rest
    lines = []
    if len(head_part) > early:
        folded = head_part[:len(head_part) - early]
        head_part = head_part[len(head_part) - early:]
        n_client = len([t for t in folded if t["who"] == TURN_CLIENT])
        n_us = len([t for t in folded if t["who"] == TURN_US])
        lines.append("… ещё " + str(len(folded)) + " ходов до этого (клиент " + str(n_client) +
                     " · мы " + str(n_us) + ")")
    base = len(turns) - len(head_part) - len(tail_part) + 1
    for k, t in enumerate(head_part):
        lines.append("· №" + str(base + k) + " " + _SIDE_SHORT[t["who"]] + ": " +
                     _one_line(t["text"], EARLY_CLIP))
    if len(tail_part) > 0:
        lines.append("── последние " + str(len(tail_part)) + " ход(ов) целиком ──")
        start = len(turns) - len(tail_part) + 1
        for k, t in enumerate(tail_part):
            lines.append("▸ №" + str(start + k) + " " + _SIDE_RU[t["who"]] + ":")
            lines.append(_clip(t["text"], TURN_CLIP))
    return lines


# ------------------------------- строки разговора -----------------------------
# Разговор в очереди — это НЕ одна строка, а все строки одного client_id. Открытая (непринятое
# предложение) среди них может быть только ОДНА: гашение в `accept_client_message` держит этот
# инвариант, а тест его проверяет на трёх дописках подряд.

OPEN_STATUSES = ("new", "posted", "pending_confirm")     # зеркалит moderation_ipc.CLAIMABLE_FROM
UNDELIVERED_STATUSES = ("ready", "test_held")            # решено человеком, но ещё не доставлено
SUPERSEDE_REASON = "клиент написал снова — предложение пересобрано"


def _ipc(ipc=None):
    """Очередь: инъекция в тестах, ленивый импорт в бою (тяжёлый модуль не тянем на импорте)."""
    if ipc is not None:
        return ipc
    import moderation_ipc
    return moderation_ipc


def current_row(rows):
    """Строка с ЖИВЫМ предложением разговора (последняя открытая). → строка | None.

    None — это не сбой: значит по разговору решение уже принято (или всё погашено), и
    предлагать сейчас нечего. Карточка обязана сказать это словами, а не показать пустоту."""
    live = [r for r in rows if _s(_get(r, "status")) in OPEN_STATUSES]
    if len(live) == 0:
        return None
    return live[-1]


def stale_rows(rows, ipc=None):
    """Устаревшие предложения разговора (отправке не подлежат). → кортеж строк."""
    mark = _ipc(ipc).STATUS_SUPERSEDED
    return tuple(r for r in rows if _s(_get(r, "status")) == mark)


def proposal_text(row):
    """Что УЙДЁТ клиенту, если нажать «отправить как есть». → строка (может быть пустой).

    Зеркалит выбор `moderation_card.decide` (кандидат после правки модератора важнее исходного
    черновика) НАМЕРЕННО и проверяется тестом: карточка, показывающая не тот текст, который
    уйдёт, опаснее отсутствующей."""
    candidate = _s(_get(row, "final_text"))
    if len(candidate) > 0:
        return candidate
    return _s(_get(row, "draft"))


def card_key(client_id):
    """Ключ карточки разговора. Единица — РАЗГОВОР, поэтому ключ — клиент, а не строка очереди."""
    return "dlg:" + _s(client_id)


# ------------------------------- сборка карточки ------------------------------

FOOT = ("Отправка — ТОЛЬКО по нажатию человека. Карточка одна на разговор и закрывается за "
        "первым ответившим; предела ожидания нет — висит, пока не ответят.")

NO_LIVE = ("🔒 Открытого предложения по этому разговору сейчас нет — отправлять нечего. "
           "Ждём следующего сообщения клиента.")


def _head(row, turns, rows):
    parts = ["🗂 КАРТОЧКА РАЗГОВОРА"]
    ref = _s(_get(row, "client_ref"))
    if len(ref) > 0:
        parts.append(ref)
    parts.append("ходов " + str(len(turns)))
    parts.append("строк очереди " + str(len(rows)))
    if _get(row, "first_contact"):
        parts.append("первый контакт")
    return " · ".join(parts)


def _sent_lines(turns, rows):
    """«Что уже отправлено ему нами» — по ТРАНСКРИПТУ (он и есть правда о диалоге: ручные
    ответы менеджера идут с того же аккаунта и в него попадают). Решённое, но ещё не
    доставленное считается ОТДЕЛЬНО: 'ready' — это «человек нажал», а не «клиент получил»."""
    ours = [t for t in turns if t["who"] == TURN_US]
    out = ["📤 УЖЕ ОТПРАВЛЕНО КЛИЕНТУ НАМИ: " + str(len(ours)) + " ход(ов) (по транскрипту диалога)"]
    if len(ours) > 0:
        out.append("   последнее: " + _one_line(ours[-1]["text"], EARLY_CLIP))
    pending = [r for r in rows if _s(_get(r, "status")) in UNDELIVERED_STATUSES]
    if len(pending) > 0:
        out.append("   решено человеком, но ещё НЕ доставлено: " + str(len(pending)))
    return out


def _topic_lines(ledger, turns):
    out = ["🎯 ТЕМЫ КЛИЕНТА (в замере 20.08 первый вопрос многотемный у 71 % входящих):"]
    if len(ledger) == 0:
        client_turns = [t for t in turns if t["who"] == TURN_CLIENT]
        if len(client_turns) == 0:
            out.append("• реплик клиента в разговоре нет — спрашивать было нечего")
        else:
            out.append("• тем не опознал: словарь замера не поймал ни одной. Это «не знаю», "
                       "а НЕ «клиент ничего не спрашивал» — прочтите ходы выше сами")
        return out
    for r in ledger:
        out.append("• " + r["topic"] + " (спрошено в ходе №" + str(r["asked_turn"]) + ") — " +
                   TOPIC_MARK[r["state"]])
    left = unanswered(ledger)
    if len(left) > 0:
        out.append("⚠️ останется БЕЗ ОТВЕТА даже после отправки: " + " · ".join(left))
    out.append(TOPIC_CAVEAT)
    return out


def _stale_lines(stale):
    """Список устаревших РАСТЁТ с каждой допиской (в разговоре на 12 ходов их будет 11), поэтому
    он ограничен: человеку нужен факт «прежние отправке не подлежат», а не перепись id."""
    ids = ["#" + str(_get(r, "id")) for r in stale[-STALE_SHOWN:]]
    tail = ""
    if len(stale) > STALE_SHOWN:
        tail = " и ещё " + str(len(stale) - STALE_SHOWN)
    return ["♻️ ПРЕДЛОЖЕНИЕ ПЕРЕСОБРАНО под последнее сообщение клиента.",
            "   Прежние предложения разговора (" + str(len(stale)) + ") УСТАРЕЛИ и ОТПРАВКЕ НЕ "
            "ПОДЛЕЖАТ: " + ", ".join(ids) + tail +
            " — нажатие по ним вернёт «закрыто», сообщение не уйдёт."]


def build(rows, hints=None, doc=None, today=None, ipc=None):
    """Собрать карточку РАЗГОВОРА по всем строкам очереди одного client_id. → dict
    {text, row, turns, ledger, stale, facts, cannot, hints}.

    Чистая функция: ни Telegram, ни сети, ни записи в очередь (чтение файла периодов цен —
    через `moderation_card`, как и раньше). Счётчик нагрузки здесь НЕ читается ни одной строкой:
    его числа владельцу не показываются."""
    ordered = list(rows)
    live = current_row(ordered)
    stale = stale_rows(ordered, ipc=ipc)
    if live is not None:
        src = live
    elif len(ordered) > 0:
        src = ordered[-1]
    else:
        src = None
    turns = turns_from_transcript(_get(src, "transcript"))
    if live is None:
        proposal = ""
    else:
        proposal = proposal_text(live)
    ledger = topic_ledger(turns, proposal=proposal)
    facts = moderation_card.price_facts(_get(src, "pricing_note"))
    if hints is None:
        h = suggest.extract_booking_hints(_s(_get(src, "transcript")), today=today)
    else:
        h = hints
    cannot = moderation_card.cannot_compute(facts, h, doc=doc)

    lines = [_head(src, turns, ordered)]
    if len(stale) > 0 and live is not None:
        lines.append("")
        lines.extend(_stale_lines(stale))
    lines.append("")
    head_turn = first_client_turn(turns)
    lines.append("🎬 С ЧЕГО КЛИЕНТ НАЧАЛ:")
    if len(turns) > 0 and turns[0]["who"] == TURN_US:
        # 285 разговоров из 710 в замере начала КОМПАНИЯ. Молчать об этом нельзя: «первый
        # вопрос клиента» в исходящем контакте — уже ОТВЕТ на наше сообщение, и читать его
        # как самостоятельное обращение значит понять разговор наоборот.
        lines.append("(разговор начали мы — исходящий контакт, клиент писал не первым)")
    if head_turn is None:
        lines.append("(реплик клиента в разговоре ещё нет — отвечать пока не на что)")
    else:
        lines.append(_clip(head_turn, HEAD_CLIP))
    lines.append("")
    lines.append("🧵 РАЗГОВОР ДАЛЬШЕ (сжато: ранние — строкой, последние — целиком):")
    lines.extend(compress_history(turns))
    lines.append("")
    lines.extend(_sent_lines(turns, ordered))
    lines.append("")
    lines.append("🤖 БОТ ПРЕДЛАГАЕТ ОТПРАВИТЬ СЕЙЧАС:")
    if live is None:
        lines.append(NO_LIVE)
    elif len(proposal) == 0:
        lines.append("(бот ответа не собрал — отправлять нечего, поправьте текст или отклоните)")
    else:
        lines.append(proposal)
    lines.append("")
    lines.extend(_topic_lines(ledger, turns))
    lines.append("")
    lines.append("🧾 ПОЧЕМУ ИМЕННО ТАК:")
    lines.extend(moderation_card._why_lines(facts, cannot, h))
    if len(cannot) > 0:
        lines.append("")
        for c in cannot:
            lines.append("⛔ НЕ СЧИТАЮ: " + c["line"])
    lines.append("")
    lines.append("ЧТО МОЖНО СДЕЛАТЬ: " +
                 " · ".join(label for _code, label in moderation_card.actions()))
    lines.append(FOOT)
    return {"text": "\n".join(lines), "row": live, "turns": turns, "ledger": ledger,
            "stale": stale, "facts": facts, "cannot": cannot, "hints": h}


def render(rows, hints=None, doc=None, today=None, ipc=None):
    """Текст карточки разговора (см. build)."""
    return build(rows, hints=hints, doc=doc, today=today, ipc=ipc)["text"]


def build_for_client(client_id, hints=None, doc=None, today=None, ipc=None):
    """То же, но строки разговора берём из очереди сами."""
    return build(_ipc(ipc).dialog_rows(client_id), hints=hints, doc=doc, today=today, ipc=ipc)


# --------------------- новое сообщение клиента в идущем разговоре -------------

def accept_client_message(rec, ipc=None):
    """Клиент написал (снова) — принять сообщение в разговор. → dict {draft_id, superseded}.

    ПОРЯДОК ОБЯЗАТЕЛЕН и является самим замком: сначала гасим непринятые предложения этого
    разговора, и только ПОТОМ ставим новую строку. Обратный порядок оставляет окно, в котором
    отправляемы ОБА предложения — старое и новое; здесь такого окна нет по построению.

    Что происходит с предложением, которое человек УЖЕ принял в этот самый миг: ничего.
    Гашение идёт UPDATE'ом с предусловием по статусу, принятая строка из `OPEN_STATUSES` уже
    вышла, и её решение остаётся в силе — «устаревает» только НЕПРИНЯТОЕ, ровно как сказано."""
    queue = _ipc(ipc)
    killed = queue.supersede_open(_get(rec, "client_id"), reason=SUPERSEDE_REASON)
    draft_id = queue.enqueue_draft(rec)
    return {"draft_id": draft_id, "superseded": killed}


# ------------------------------- замер нагрузки -------------------------------
# Числа КОПЯТСЯ и владельцу НЕ показываются: ни `build`, ни `render` счётчика не читают.
# Нужны к ноябрю — падение времени на карточку до секунд означает, что надзор выродился
# в рефлекс, и это обязано быть видно числом.

def card_shown(client_id, ts=None, meter=None, path=None):
    """Карточка разговора показана человеку (первый показ ИЛИ пересборка под новое сообщение).
    → запись события | None, если замер не сумел записать (он скажет это в `card_load.errors`)."""
    m = meter
    if m is None:
        m = card_load
    return m.record(card_load.EV_SHOWN, card_key(client_id), ts=ts, path=path)


def decide(row, action, username, text=None, test_mode=False, ipc=None, meter=None,
           ts=None, path=None):
    """Действие человека над карточкой разговора. → decision-dict `moderation_card.decide`.

    Своего захвата здесь НЕТ и быть не может: решение принимает та же единственная функция, что
    и до смены единицы, — иначе замок «первый ответивший» существовал бы в двух экземплярах и
    разъехался бы. Устаревшее предложение отсекается ТАМ ЖЕ, в СУБД: его статус выпал из
    `CLAIMABLE_FROM`, захват не состоится, и вызывающий получит `decision='closed'`.

    Нажатие записывается в счётчик нагрузки ПОСЛЕ решения и на исход не влияет ни одной веткой:
    даже полностью сломанный замер не может ни отправить сообщение, ни отменить отправку."""
    dec = moderation_card.decide(row, action, username, text=text, test_mode=test_mode, ipc=ipc)
    m = meter
    if m is None:
        m = card_load
    m.record(card_load.EV_PRESSED, card_key(_get(row, "client_id")), ts=ts, path=path,
             action=_s(action), outcome=_s(dec.get("decision")))
    return dec
