# -*- coding: utf-8 -*-
"""vitrina_pc.py — ЖИВАЯ ВИТРИНА СОСТОЯНИЯ полосы ПК. Чистая логика.

ЗАЧЕМ ТРЕТИЙ ВИД ПОКАЗА, КОГДА ЕСТЬ СВОДКА. Премиса задания проверена по коду, а
не по памяти: :func:`contour_digest_run.tick` зовёт :func:`review_audit_run.send_audit`,
та — :func:`dispatch_notify.send_topic_strict`, а он бьёт в ``sendMessage``. То
есть сводка контура кладёт в тему НОВОЕ сообщение КАЖДЫЙ раз (живой замер: первая
ушла 01.09 19:39 UTC с ``message_id=9100``), и содержимое её — числа с адресами
источников, без человеческого пояснения. Шесть таких в сутки — лента, по которой
владелец листает назад, чтобы понять «что сейчас».

ВИТРИНА СВОДКУ НЕ ЗАМЕНЯЕТ И НЕ ОТМЕНЯЕТ. Она стои́т рядом в той же теме и живёт
ОДНИМ сообщением, которое демон ПРАВИТ на месте: у витрины нет истории, у сводки
нет «сейчас». Разные вопросы — разные виды показа:

    сводка   «что было за окно 4 ч»   новое сообщение каждый раз, лента, история
    витрина  «что происходит сейчас»  ОДНО сообщение, правится, истории нет

ЧТО ВИТРИНА НЕ ДЕЛАЕТ (границы, каждая закрыта тестом):
  • НЕ ШЛЁТ НОВОГО СООБЩЕНИЯ НА ОБОРОТЕ. Второй оборот подряд правит первое
    (`test_second_turn_makes_no_second_message`). Новое — ровно один случай:
    старого больше НЕТ, и это сказал сам Telegram (:func:`edit_lost`).
  • НЕ ВЫДУМЫВАЕТ ЧЕЛОВЕЧЕСКИХ ЧАСТЕЙ. «Сейчас делаем», «застряло» и «куда идём»
    приходят из узла мозга, который пишет Штаб (:data:`SHTAB_NODE`). Узел не
    прочитан → части говорят :data:`NO_SHTAB`, а не пустоту и не сочинение кода.
  • НЕ СТАВИТ НУЛЯ ВМЕСТО НЕЗНАНИЯ. Источник числа недоступен → в витрине стои́т
    :data:`UNKNOWN` с причиной (:func:`number`). Прямой запрет задания, и он
    сильнее удобства: «взято 0» читается как измеренный факт.
  • НЕ ПЕРЕНОСИТ ЧУЖОГО ТЕКСТА внешних каналов — только счёт и темы одним словом.
  • НЕ ИСПОЛНЯЕТ И НЕ СТАВИТ ЗАДАЧ, кнопок не несёт, клиентского контура не
    касается (инвариант ``VITRINA_PC_READS_ONLY`` обходом AST боевых файлов).

═══ ПОЧЕМУ В ШАПКЕ «ЧИСЛА МЕНЯЛИСЬ», А НЕ «СОБРАНО» ════════════════════════════

Пункт 4 задания: частота обновления — не чаще, чем меняются числа. Стои́т в шапке
время СБОРКИ — и правка уходит каждый оборот, потому что время в теле меняется
всегда; витрина превращается в мигалку и ест API ради собственного штампа.
Поэтому подпись оборота у витрины ОДНА ЕДИНСТВЕННАЯ: время, когда числа
ИЗМЕНИЛИСЬ в последний раз. Признак смены — :func:`signature` по телу, где штампа
уже нет. Числа стоя́т сутки → сутки не будет ни одной правки, и это правильный
ответ, а не молчание.

СЛЕДСТВИЕ НАЗВАНО, А НЕ СПРЯТАНО: по витрине НЕЛЬЗЯ судить, жива ли полоса.
Замерший демон замораживает витрину ровно так же, как спокойный контур, и
различить их изнутри нечем. Жизнь полосы судят О2 и О4 — они живут ВНЕ демона
(`CLAUDE.md`, слой ожиданий, пункт 4: «наблюдатель не живёт на том, за чем
следит»). Тот же замок и у слепка очереди `queue_state_pc`.

═══ ШЕСТЬ ЧАСТЕЙ И ГДЕ БЕРЁТСЯ КАЖДАЯ ══════════════════════════════════════════

Обход :data:`PARTS` строит текст целиком: часть, у которой не нашлось ни строки,
— падение теста, а не тихий пропуск (тот же закон 3, которым живёт сводка).

    СЕЙЧАС ДЕЛАЕМ   слова Штаба + живое: строки очереди `in_progress` и с какого
                    времени (отметка claim демона), возраст числом
    ЗАСТРЯЛО        слова Штаба + живое: открытые ожидания О1–О4, упавшие строки
    ЭТАП И КУДА ИДЁМ слова Штаба + живое: серия к критерию фазы, сколько осталось
    ЧИСЛА           серия из 30 с ОПОРОЙ · ящик Штаба за сутки · внешний контур за
                    сутки · карточки в ожидании
    ОСИ             счёт заходов за сутки по трём осям рамки
    ЖДЁТ ВЛАДЕЛЬЦА  открытые решения списком, коротко

═══ ОСИ: ПОЧЕМУ «ОБСЛУЖИВАНИЕ» — ОСТАТОК, А НЕ ТРЕТИЙ СПИСОК ═══════════════════

Рамка (редакция 02.09 #1, строки 418–419) требует от каждого захода назвать свою
ось: «это движет ось (этап 3), это чинит данные бизнеса, или это обслуживание нас
самих». Две первые уже опознаются ПО ПУТЯМ ФАЙЛОВ коммита
(:data:`contour_digest_run.AXIS3_PATHS` / :data:`~contour_digest_run.BUSINESS_PATHS`),
и списки эти взяты оттуда, а не набраны здесь заново.

Третья ось СВОЕГО списка путей не получает СОЗНАТЕЛЬНО: три списка, которые
обязаны в сумме покрыть дерево, расходятся молча на первом новом файле, и заход
проваливался бы мимо всех трёх, не будучи посчитан нигде. Поэтому обслуживание —
ОСТАТОК: всё, что за сутки коммитилось и не попало ни в ось, ни в бизнес
(:func:`axes_tally`). Сумма трёх при этом ≥ числа коммитов: заход, тронувший и
ось, и бизнес, честно стои́т в обеих — рамка спрашивает «что двигал», а не «к
какой одной полке отнести».

ЧЕСТНАЯ ГРАНИЦА, НАЗВАННАЯ В САМОЙ ВИТРИНЕ: считается СЛЕД захода — коммит, а не
сам заход. Заход, не оставивший коммита (разведка, чтение, упавшая задача), в
счёт осей не попадает; чтобы разрыв был виден, рядом печатается второе число —
сколько строк очереди закрылось за те же сутки.

Чистая логика: ни часов, ни диска, ни сети, ни ``getenv`` — инвариант
``VITRINA_PC_PURE`` обходом AST. Руки — :mod:`vitrina_pc_run`.
"""
from __future__ import annotations

import re

import contour_digest as cd
import shtab_box

SCHEMA = "turbobaby.vitrina_pc/v1"

# ═════════════════════════ ЧИСЛА, НАЗВАННЫЕ ВСЛУХ ════════════════════════════

TICK_SEC = 600.0          # как часто ветка ЗАГЛЯДЫВАЕТ (правка — только при смене чисел)
SHTAB_STALE_DAYS = 3      # слова Штаба старше — печатаются с возрастом вслух
LIST_MAX = 5              # строк в списке; остаток называется числом
GOAL_MAX = 64             # цель строки очереди в одну строку витрины
TEXT_MAX = 3900           # потолок сообщения Telegram 4096; запас на хвост

# ═════════════════════════ СЛОВА, КОТОРЫЕ НЕЛЬЗЯ ПОДМЕНИТЬ ═══════════════════

UNKNOWN = "НЕИЗВЕСТНО"
# «Штаб не обновил» — не украшение, а исход: человеческие части витрины без узла
# НЕ СОЧИНЯЮТСЯ кодом (пункт 5 задания).
NO_SHTAB = "Штаб не обновил"

HEAD = "ВИТРИНА ПОЛОСЫ ПК · одно сообщение, правится на месте"
FOOT = ("витрина не судья жизни полосы: замерший демон замораживает её так же, как спокойный "
        "контур — жизнь судят О2/О4 вне демона")

# ═════════════════════════ УЗЕЛ ШТАБА ════════════════════════════════════════
# Имя ровно одно и живёт здесь; полоса в этот узел НЕ ПИШЕТ ни одной веткой —
# витрина его только ЧИТАЕТ. Пишет Штаб.

SHTAB_NODE = "shtab_vitrina"

# Разделы узла — те же три человеческие части. Маркер набран в стиле ящика
# (`shtab_box.OPEN_RE`), чтобы Штабу не пришлось помнить два синтаксиса.
SECT_RE = re.compile(r"^\s*\[\[\s*(СЕЙЧАС|ЗАСТРЯЛО|КУДА ИДЁМ)\s*\]\]\s*$")
STAMP_RE = re.compile(r"^\s*дата\s*=\s*(\d{4}-\d{2}-\d{2})\s*$")
SECT_KEY = {"СЕЙЧАС": "now", "ЗАСТРЯЛО": "stuck", "КУДА ИДЁМ": "goal"}
SHTAB_PART_MAX = 400      # слова Штаба на часть; остаток режется с названием

# ═════════════════════════ ЧАСТИ ВИТРИНЫ ═════════════════════════════════════
# Обход ИМЕННО этого кортежа строит текст. Часть без строк — падение теста.

PARTS = (
    ("now", "СЕЙЧАС ДЕЛАЕМ"),
    ("stuck", "ЗАСТРЯЛО"),
    ("goal", "ЭТАП И КУДА ИДЁМ"),
    ("nums", "ЧИСЛА"),
    ("axes", "ОСИ"),
    ("owner", "ЖДЁТ ВЛАДЕЛЬЦА"),
)
PART_KEYS = tuple(k for k, _ in PARTS)

# Три оси рамки. Порядок — порядок строки 418 канона.
AXIS_MAIN = "ось (этап 3)"
AXIS_BIZ = "бизнес"
AXIS_SERVICE = "обслуживание"
AXES = (AXIS_MAIN, AXIS_BIZ, AXIS_SERVICE)

# ═════════════════════════ TELEGRAM СКАЗАЛ «СООБЩЕНИЯ НЕТ» ═══════════════════
# Единственный случай, когда витрине разрешено слать НОВОЕ сообщение. Признак
# живёт в ЧИСТОЙ логике намеренно: он решает, можно ли завести второе сообщение в
# теме, а такое решение обязано проверяться тестом без сети.

LOST_MARKS = (
    "message to edit not found",
    "message to be edited not found",
    "message_id_invalid",
    "message can't be edited",
    "message identifier is not specified",
)
# «не изменилось» — НЕ потеря и НЕ отказ: Telegram так отвечает на правку тем же
# текстом. Для витрины это успех (в теме стои́т ровно то, что мы хотели).
SAME_MARK = "message is not modified"


def edit_lost(detail):
    """Telegram сказал «этого сообщения больше нет»? → bool.

    Осторожность НАМЕРЕННО односторонняя: всё, что не опознано списком, считается
    ВРЕМЕННЫМ отказом, и витрина ждёт следующего оборота. Ошибка в эту сторону
    стои́т задержки; ошибка в другую — второго сообщения в теме на каждый сбой
    сети, то есть ровно той ленты, от которой витрина уходит.
    """
    words = str(detail or "").lower()
    return any(mark in words for mark in LOST_MARKS)


def edit_same(detail):
    """Правка тем же текстом — успех, а не отказ."""
    return SAME_MARK in str(detail or "").lower()


# ═════════════════════════ ЧИСЛО ИЛИ НЕЗНАНИЕ ════════════════════════════════

def number(value, why=""):
    """Число для витрины. ``None`` → «НЕИЗВЕСТНО (причина)», НИКОГДА не ноль.

    Прямой запрет задания (пункт 3), и он держится здесь одной дверью: любое
    число витрины проходит через эту функцию, поэтому «поставить ноль вместо
    незнания» пришлось бы делать нарочно, в обход.
    """
    if value is None:
        return "%s (%s)" % (UNKNOWN, one_line(why, 90) or "источник причины не назвал")
    return str(value)


def one_line(value, limit=GOAL_MAX):
    """Текст в одну строку. Переносы — в пробел, хвост — в многоточие."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def age_words(sec):
    """Возраст числом и словами. Нет чем мерить — так и сказано."""
    if sec is None:
        return "возраст неизвестен"
    try:
        val = max(0.0, float(sec))
    except (TypeError, ValueError):
        return "возраст неизвестен"
    if val < 90:
        return "%d с" % int(val)
    if val < 5400:
        return "%d мин" % int(round(val / 60.0))
    if val < 86400:
        return "%.1f ч" % (val / 3600.0)
    return "%.1f сут" % (val / 86400.0)


def listed(items, limit=LIST_MAX):
    """Список с НАЗВАННЫМ остатком: молча обрезанный читается как полный."""
    rows = list(items or [])
    if len(rows) <= limit:
        return rows
    return rows[:limit] + ["…и ещё %d" % (len(rows) - limit)]


# ═════════════════════════ УЗЕЛ ШТАБА: РАЗБОР ════════════════════════════════

def parse_shtab(text, ok=True, why=""):
    """Текст узла Штаба → три человеческие части. → dict.

    Разбор ПРОЩАЮЩИЙ по форме и СТРОГИЙ по исходу: неизвестные разделы
    пропускаются молча (Штаб вправе писать себе что угодно), но раздел, которого
    нет, даёт :data:`NO_SHTAB`, а не пустую строку. Пустая строка в витрине
    читается как «всё спокойно», и это была бы ложь от лица Штаба.

    ``ok=False`` — узел не прочитан вовсе: все три части говорят одно и то же и
    несут ПРИЧИНУ. Отличать «Штаб молчит» от «мост молчит» обязан читающий, и для
    этого причина едет в текст.
    """
    out = {"ok": bool(ok), "why": str(why or ""), "day": "", "parts": {}}
    if not ok or not isinstance(text, str) or not text.strip():
        out["ok"] = False
        out["why"] = out["why"] or "узел %s не прочитан" % SHTAB_NODE
        return out
    cur, buf = None, []
    for raw in text.splitlines():
        hit = SECT_RE.match(raw)
        if hit:
            if cur:
                out["parts"][cur] = "\n".join(buf).strip()
            cur, buf = SECT_KEY[hit.group(1)], []
            continue
        if cur is None:
            stamp = STAMP_RE.match(raw)
            if stamp and not out["day"]:
                out["day"] = stamp.group(1)
            continue
        buf.append(raw)
    if cur:
        out["parts"][cur] = "\n".join(buf).strip()
    out["parts"] = {k: v for k, v in out["parts"].items() if v}
    if not out["parts"]:
        out["ok"] = False
        out["why"] = out["why"] or "в узле %s нет ни одного раздела витрины" % SHTAB_NODE
    return out


def days_between(a, b):
    """Разница календарных дат 'YYYY-MM-DD' в сутках. → int | None (третий исход)."""
    def _num(day):
        parts = str(day or "").split("-")
        if len(parts) != 3:
            return None
        try:
            y, m, d = (int(p) for p in parts)
        except ValueError:
            return None
        # Достаточно МОНОТОННОЙ шкалы: витрина спрашивает «давно ли», а не «сколько
        # ровно». Календаря здесь нет и не будет — он привёл бы часы в чистый слой.
        return y * 372 + m * 31 + d
    left, right = _num(a), _num(b)
    if left is None or right is None:
        return None
    return right - left


def shtab_words(parsed, key, today=""):
    """Человеческая часть словами Штаба. → str, НИКОГДА не пустая.

    Слова старше :data:`SHTAB_STALE_DAYS` едут ВМЕСТЕ С ВОЗРАСТОМ: витрина не
    вправе выдавать позавчерашнее «сейчас делаем» за сегодняшнее, но и молчать о
    нём не вправе — вычеркнутый раздел читается как «работы нет».
    """
    got = parsed or {}
    if not got.get("ok"):
        return "%s (%s)" % (NO_SHTAB, one_line(got.get("why"), 110) or "причина не названа")
    body = (got.get("parts") or {}).get(key)
    if not body:
        return "%s: раздела нет в узле %s" % (NO_SHTAB, SHTAB_NODE)
    words = one_line(body, SHTAB_PART_MAX)
    old = days_between(got.get("day"), today) if (got.get("day") and today) else None
    if old is not None and old > SHTAB_STALE_DAYS:
        return "%s [Штаб обновлял %d сут назад — %s]" % (words, old, got.get("day"))
    return words


# ═════════════════════════ ОСИ ═══════════════════════════════════════════════

def axes_tally(total, axis_hits, biz_hits):
    """Хеши коммитов за сутки → счёт по трём осям. → dict {ось: int|None}.

    ``total`` — ВСЕ коммиты суток, ``axis_hits``/``biz_hits`` — попавшие в оси.
    Обслуживание = остаток, и это разобрано в шапке. ``None`` в любом входе →
    ``None`` во всех трёх: посчитать часть на неполном корпусе значит выдать
    неполное число за полное.
    """
    if total is None or axis_hits is None or biz_hits is None:
        return {AXIS_MAIN: None, AXIS_BIZ: None, AXIS_SERVICE: None}
    all_h, ax, bz = set(total), set(axis_hits), set(biz_hits)
    return {AXIS_MAIN: len(ax), AXIS_BIZ: len(bz), AXIS_SERVICE: len(all_h - ax - bz)}


def axes_words(tally, closed_day, why=""):
    """Строка осей: счёт по трём + названная граница признака."""
    got = tally or {}
    parts = ["%s %s" % (name, number(got.get(name), why or "git за сутки не прочитан"))
             for name in AXES]
    return ("заходов за сутки (след — коммит): %s · строк очереди закрылось за сутки: %s"
            % (" · ".join(parts), number(closed_day, "слепок очереди не прочитан")))


# ═════════════════════════ ЧАСТИ ════════════════════════════════════════════

def part_now(shtab, running, today=""):
    """СЕЙЧАС ДЕЛАЕМ: слова Штаба + что в работе и с какого времени.

    ``running`` — ``[{"id":…, "goal":…, "age": сек|None}]`` либо ``None``
    («слепок не прочитан»). Пустой список и ``None`` — РАЗНЫЕ новости, и обе
    названы словами: «в работе ничего» против «неизвестно».
    """
    out = ["Штаб: %s" % shtab_words(shtab, "now", today)]
    if running is None:
        out.append("в работе: %s" % number(None, "слепок очереди не прочитан"))
        return out
    if not running:
        out.append("в работе строк нет — полоса свободна")
        return out
    rows = []
    for one in running:
        age = one.get("age")
        rows.append("#%s «%s» — %s" % (one.get("id"), one_line(one.get("goal"), GOAL_MAX),
                                       ("идёт %s" % age_words(age)) if age is not None
                                       else "с какого времени НЕИЗВЕСТНО (отметки claim нет)"))
    out += ["в работе %d: %s" % (len(running), rows[0])] + rows[1:LIST_MAX]
    if len(rows) > LIST_MAX:
        out.append("…и ещё %d" % (len(rows) - LIST_MAX))
    return out


def part_stuck(shtab, expects, failed, today=""):
    """ЗАСТРЯЛО: слова Штаба + открытые ожидания + упавшее за сутки."""
    out = ["Штаб: %s" % shtab_words(shtab, "stuck", today)]
    if expects is None:
        out.append("открытых ожиданий О1–О4: %s" % number(None, "слой ожиданий не прочитан"))
    elif not expects:
        out.append("открытых ожиданий О1–О4 нет")
    else:
        out.append("открытых ожиданий О1–О4: %d — %s"
                   % (len(expects), " · ".join(listed(sorted(expects)))))
    if failed is None:
        out.append("упало за сутки: %s" % number(None, "слепок очереди не прочитан"))
    elif not failed:
        out.append("упавших строк за сутки нет")
    else:
        out.append("упало за сутки %d: %s"
                   % (len(failed), " · ".join(listed(["#%s %s" % (f.get("id"),
                                                                 one_line(f.get("why"), 48))
                                                      for f in failed]))))
    return out


def part_goal(shtab, counted, today=""):
    """ЭТАП И КУДА ИДЁМ: слова Штаба + чем меряется рост и сколько осталось.

    Критерий фазы берётся у сводки (:data:`contour_digest.SERIES_TARGET`) — второе
    определение «тридцати подряд» развело бы два показа при одном источнике.
    """
    out = ["Штаб: %s" % shtab_words(shtab, "goal", today)]
    if not counted:
        out.append("критерий фазы: %d чистых цепочек подряд · сейчас %s"
                   % (cd.SERIES_TARGET, number(None, "слепок очереди не прочитан")))
        return out
    streak = counted.get("streak", 0)
    target = counted.get("target", cd.SERIES_TARGET)
    out.append("критерий фазы: %d чистых цепочек подряд · сейчас %d · осталось %d "
               "(чистая = сдана И доказана судьёй по названному адресу)"
               % (target, streak, max(0, int(target) - int(streak))))
    return out


def part_nums(counted, taken, day, external, awaiting, box_stop=""):
    """ЧИСЛА: серия с ОПОРОЙ · ящик Штаба · внешний контур · карточки в ожидании.

    Серия едет ВМЕСТЕ С ОПОРОЙ намеренно: «серия 18» без «доказал судья 5» — ровно
    то число, которое до 02.09 врало молча (`contour_digest`, §«DONE ЧИСТОЙ НЕ
    ДЕЛАЕТ»).
    """
    out = []
    if not counted:
        out.append("серия: %s" % number(None, "слепок очереди не прочитан или старше предела"))
    else:
        out.append("серия: %d из %d подряд · судья доказал %d · без адреса %d · не доказано %d "
                   "· судья не судил %d · из них меняли состояние %d"
                   % (counted.get("streak", 0), counted.get("target", cd.SERIES_TARGET),
                      counted.get("proved", 0), counted.get("blind", 0),
                      counted.get("unproved", 0), counted.get("unjudged", 0),
                      counted.get("moved", 0)))
    out.append("ящик Штаба за %s: взято %s из %d за сутки"
               % (day or "?", number(taken, "слепок очереди не прочитан"),
                  shtab_box.DAILY_BUDGET))
    # СИГНАЛЬНАЯ ОСТАНОВКА ЯЩИКА — СТРОКОЙ, А НЕ ЧИСЛОМ. Она приходит готовой
    # фразой из метки оборота демона (`vitrina_pc_run.read_box_stop`); витрина её
    # НЕ СЧИТАЕТ и НЕ СОКРАЩАЕТ до слова «остановлен»: вся ценность здесь в том,
    # ЧТО сработало, НА ЧЁМ стои́т числом и КАКУЮ строку владельцу вставить в узел,
    # чтобы снять. Урезанная до статуса, она стала бы неотличима от «ящик пуст».
    #
    # МОЛЧИТ — ЗНАЧИТ МОЛЧИТ, и «остановки нет» витрина не утверждает: метки
    # может не быть вовсе (демон не делал ни одного оборота ящика), а пустоту
    # выдавать за здоровье — ровно тот класс, от которого весь этот модуль.
    if str(box_stop or "").strip():
        out.append(one_line(box_stop, TEXT_MAX))
    if external is None:
        out.append("внешний контур за сутки: %s" % number(None, "лоток не прочитан"))
    else:
        down = external.get("down") or []
        out.append("внешний контур за %s: заходов %d · ответов %d · каналов ответило %d из %d "
                   "· находок %d%s"
                   % (external.get("day") or day or "?", external.get("attempts", 0),
                      external.get("answers", 0), external.get("channels_answered", 0),
                      external.get("channels_tried", 0), external.get("findings", 0),
                      (" · КАНАЛ ЛЕЖАЛ: %s" % ", ".join(down)) if down else ""))
    out.append("карточки в ожидании: %s"
               % number(awaiting, "слепок очереди не прочитан"))
    return out


def part_axes(tally, closed_day, why=""):
    return [axes_words(tally, closed_day, why)]


def part_owner(waiting):
    """ЖДЁТ ВЛАДЕЛЬЦА: открытые решения списком, коротко."""
    if waiting is None:
        return ["%s: %s" % ("открытые решения", number(None, "слепок очереди не прочитан"))]
    if not waiting:
        return ["решения владельца не ждёт ничего"]
    return listed(["#%s %s" % (w.get("id"), one_line(w.get("goal"), GOAL_MAX))
                   for w in waiting])


# ═════════════════════════ СБОРКА ════════════════════════════════════════════

def body(facts):
    """Тело витрины БЕЗ штампа. → str.

    Штампа здесь нет намеренно: по этому телу считается подпись смены
    (:func:`signature`), и время сборки в нём заставляло бы витрину править себя
    каждый оборот (разбор — в шапке модуля).
    """
    got = facts or {}
    shtab = got.get("shtab") or {}
    today = got.get("day") or ""
    made = {
        "now": part_now(shtab, got.get("running"), today),
        "stuck": part_stuck(shtab, got.get("expects"), got.get("failed"), today),
        "goal": part_goal(shtab, got.get("series"), today),
        "nums": part_nums(got.get("series"), got.get("shtab_taken"), today,
                          got.get("external"), got.get("awaiting"),
                          box_stop=got.get("box_stop") or ""),
        "axes": part_axes(got.get("axes"), got.get("closed_day"), got.get("axes_why") or ""),
        "owner": part_owner(got.get("waiting")),
    }
    out = []
    for key, title in PARTS:
        rows = [r for r in (made.get(key) or []) if str(r or "").strip()]
        if not rows:
            raise AssertionError("часть %r осталась без строки — витрина обязана говорить" % key)
        out += ["%s" % title] + ["• %s" % r for r in rows] + [""]
    return "\n".join(out).rstrip()


def signature(facts):
    """Подпись СМЕНЫ ЧИСЕЛ: правим сообщение, только когда она поменялась."""
    return body(facts)


def render(facts, changed_words=""):
    """Готовое сообщение витрины. → str.

    Единственная подпись оборота — время ПОСЛЕДНЕЙ СМЕНЫ чисел, и оно приходит
    готовой строкой от рук: часов в чистом слое нет.
    """
    text = "\n".join([HEAD,
                      "числа менялись последний раз: %s" % (changed_words or "?"),
                      "", body(facts), "", FOOT])
    if len(text) <= TEXT_MAX:
        return text
    # Обрезаем ЯВНО и с названным числом: молча укороченная витрина читается как
    # полная, а Telegram отказал бы целиком — и владелец не увидел бы ничего.
    cut = text[:TEXT_MAX].rstrip()
    return "%s\n…витрина обрезана до %d символов (потолок сообщения)" % (cut, TEXT_MAX)


def journal_line(report):
    """Строка-индекс в журнал: что стало с витриной за оборот."""
    got = report or {}
    if got.get("skipped"):
        return "NOTE ВИТРИНА ПК: %s" % one_line(got.get("skipped"), 200)
    how = got.get("how") or "не тронута"
    return ("NOTE ВИТРИНА ПК: %s · сообщение %s · тема %s%s"
            % (how, got.get("message_id") or "нет", got.get("topic") or "не настроена",
               ("" if got.get("ok") else " · причина: %s" % one_line(got.get("why"), 120))))
