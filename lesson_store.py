# -*- coding: utf-8 -*-
"""
lesson_store.py — ХРАНИЛИЩЕ УРОКОВ ТРЕНАЖЁРА. Отдельная таблица; бот её пока НЕ зовёт.

ЗАЧЕМ. Урок тренажёра сегодня оседает одной строкой в `trainer_rules.json` — словаре
«правило → источник». У такой формы нет НИЧЕГО, кроме самого текста правила: ни причины
(«почему так правильно»), ни автора, ни времени, ни номера, ни отката. Снять урок можно
только вырезав ключ руками, а ответить «кто и когда это записал» — нечем вовсе. Здесь
заводится ОТДЕЛЬНАЯ таблица, где у каждого урока шесть обязательных полей (вопрос клиента,
что ответил бот, как правильно, ПОЧЕМУ, кто записал, когда), свой номер и состояние.

ГРАНИЦА ШАГА, названная честно и ПОДВИНУТАЯ 05.09.2026. Шаг 1 (19.08) строил хранилище, и тогда
сюда не ходил никто. Теперь подключён РОВНО ОДИН файл — `trainer.py`: кнопка «🎓 Обучить» и
команда «урок:» кладут урок КАНДИДАТОМ (`add_candidate`), а плоскую книгу правил больше не
трогают. `moderation_bot.py`, `userbot_listen.py`, `lesson_router.py` и — особо — `suggest.py`
(сборщик КЛИЕНТСКОГО ответа) сюда по-прежнему не ходят ни одной веткой: кандидат, по которому
владелец ещё не назвал причину, не имеет права влиять на то, что читает клиент. Круг заперт
ПОИМЁННО грепом по импортам, а не обещанием (`test_lesson_store.TestBotNotWired`).

ПОЧЕМУ TSV, А НЕ CSV, JSON ИЛИ БАЗА — три отказа, каждый по своей причине:

  • CSV. Кавычки разрешают ПЕРЕВОД СТРОКИ ВНУТРИ ПОЛЯ, а урок многострочен почти всегда
    (переписка клиента — это несколько реплик). Значит «одна строка файла = один урок»
    перестаёт быть правдой: `wc -l`, `grep`, `Select-String` и глаз начинают считать
    уроки неверно, причём МОЛЧА. Здесь перевод строки экранируется (`\\n`), и правило
    «строка = урок» держится БАЙТАМИ, а не соглашением.
  • JSON целым файлом. Дозапись невозможна: каждая новая запись переписывает файл
    ЦЕЛИКОМ, то есть на каждый урок трогает байты всех прежних. Оборванная запись —
    потеря всей таблицы. Требование «уроки не исчезают НИКОГДА» такой формой не
    обеспечивается ничем, кроме везения.
  • sqlite. Это красная операция контура (запись в БД), и на шаг «построить хранилище»
    она не нужна: таблица из восьми колонок читается пересчётом за миллисекунды (замер
    ёмкости ниже).

ПОЧЕМУ ФАЙЛ НЕ ПОД GIT — не лень, а замер чужого кода. `pc_orchestrator` возвращает дерево
к HEAD (`git checkout HEAD -- <пути>`, строки 2389 и 2491), а авто-фетч пропускает `git pull`
при грязной рабочей копии (строка 6161). Отслеживаемый git файл, в который дописывает
рантайм, попадает под оба: первый его МОЛЧА откатывает (ровно то «переполнение, стирающее
записи», которого быть не должно), второй из-за него навсегда встаёт. Ровно этим доводом
`.gitignore` уже держит вне git `series_pc.state.json`. Долговечность даёт не git, а
дисциплина записи (ниже) — и она проверяема тестом, в отличие от git-истории.

ЧЕМ ГАРАНТИРОВАНО «УРОКИ НЕ ИСЧЕЗАЮТ НИКОГДА» — тремя устройствами, а не обещанием:

  1. ЗАПИСЬ ТОЛЬКО ДОПИСЫВАНИЕМ. Новый урок кладётся `open(..., "a")` + `fsync`: старые
     байты не трогаются вовсе, оборванная запись портит максимум последнюю строку.
  2. РОТАЦИИ И ПОТОЛКА НЕТ НИ ОДНОЙ ВЕТКОЙ. В модуле нет ни `os.remove`, ни `os.truncate`,
     ни `os.unlink`; единственный усекающий `open(..., "w")` открывает ВРЕМЕННЫЙ файл, а не
     таблицу. Это заперто разбором собственного исходника (`TestNothingEverDeletes`).
  3. СНЯТИЕ НЕ УДАЛЯЕТ СТРОКУ. Оно меняет ПОСЛЕДНЕЕ поле строки (состояние), причём
     перезапись идёт по СЫРЫМ строкам: остальные байты переносятся дословно, а не через
     круг «разобрать → собрать заново» (круг не байт-в-байт на чужих экранированных
     последовательностях). Перед перезаписью рядом кладётся копия `.bak`.

СОСТОЯНИЕ строки — `кандидат` (записан, но НЕ действует; заведено 05.09.2026), `актив` либо
`снят(<разрез>;<штамп UTC>)`, где разрез это один из
трёх, названных владельцем: `урок` (один), `день` (все за сутки), `автор` (все одного
человека). Разрез и время живут ВНУТРИ поля состояния сознательно: колонок в таблице ровно
восемь (шесть обязательных полей + номер + состояние), а след «кем и когда снято» терять
нельзя — иначе откат перестаёт быть обратимым знанием.

ПЕРСОНАЛЬНОЕ НЕ ПОПАДАЕТ В ТАБЛИЦУ ПО УСТРОЙСТВУ, а не по дисциплине вызывающего: `add()`
прогоняет четыре ТЕКСТОВЫХ поля через детектор `anonymize_corpus._clean_text` — тот самый,
которым обезличен корпус из 710 диалогов, — и выключателя у этой чистки нет. Метки
устойчивы ВНУТРИ урока (один человек — одно `Лицо_1` во всех четырёх полях) и НЕ устойчивы
между уроками: сквозная метка склеила бы разных людей в одного и стала бы трекером.

  Поле `кто записал` НЕ чистится, и это решение, а не дыра: владелец потребовал знать
  автора урока поимённо, а чистка превратила бы менеджера в `Лицо_1` и убила бы разрез
  отката «снять всё записанное одним человеком».

  СОМНИТЕЛЬНЫЙ СЛУЧАЙ решается В ПОЛЬЗУ ПРИВАТНОСТИ: слово, опознанное основой имени со
  склонением (`_name_hit`) или стоящее в звательной/подписной позиции, маскируется, даже если
  это могло быть не имя. Отменяет маску только защищённый словарь детектора (модели, места,
  бренды, частые слова) — без него «Хонда» и «Раваи» уехали бы в `Лицо_1`, и урок потерял бы
  смысл, ради которого записан.

  ЦЕНА ЭТОГО ВЫБОРА НАЗВАНА ЧИСЛОМ, А НЕ СПРЯТАНА. Звательная позиция ловит первое слово
  после приветствия, и на живой фразе «Здравствуйте! Меня зовут Сергей…» под маску уходит
  местоимение: в таблицу ложится «Здравствуйте! Лицо_2 зовут Лицо_1». Имя вычищено верно,
  местоимение — лишнее. Это ПЕРЕмаскировка, и она сознательно не чинится здесь: `PROTECTED`
  живёт в `anonymize_corpus`, а его полноту обезличивания держит замок корпуса
  (`anonymize_corpus_stable`, «совпасть с прежней до вхождения»). Расширить словарь ради
  своего случая = молча обесценить чужой замер на 710 диалогах. Замер перемаскировки и
  разбор — в docs/artifacts/2026-08-19-lesson-store.md §6; поведение заперто тестом
  `TestPersonalDataNeverLands.test_greeting_position_overmasks_by_design`.

  ГРАНИЦА названа честно и в другую сторону: имя, которого нет ни в словаре, ни в
  звательной/подписной позиции, ОСТАНЕТСЯ — это та же граница, что у `anonymize_corpus`,
  и она не ноль.

ЁМКОСТЬ — числом, а не «сколько влезет»: см. `COMFORT_LESSONS` и `capacity()`. Отказа по
переполнению НЕТ: превышение порога комфорта делает таблицу МЕДЛЕННЕЕ и говорит об этом
вслух, но не теряет ни строки и не отказывает в записи.

ЗАПУСК:
    venv/Scripts/python.exe lesson_store.py --status          # ёмкость и целостность числами
    venv/Scripts/python.exe lesson_store.py --list            # активные уроки
    venv/Scripts/python.exe lesson_store.py --list --all      # вместе со снятыми
"""

import argparse
import os
import re
import shutil
import sys
import time
from collections import Counter, namedtuple

import anonymize_corpus as A
import io_utf8
import parse_outcome

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_NAME = "lesson_store.tsv"
STORE_PATH = os.path.join(HERE, STORE_NAME)
BACKUP_SUFFIX = ".bak"
TMP_SUFFIX = ".tmp"

# ---------------------------------------------------------------------------------------
# Колонки. Порядок — часть формата файла: менять нельзя, не переписав уже лежащие строки.
# ---------------------------------------------------------------------------------------
COL_NUM = "номер"
COL_QUESTION = "вопрос_клиента"
COL_BOT = "ответ_бота"
COL_RIGHT = "как_правильно"
COL_WHY = "почему"
COL_WHO = "кто_записал"
COL_WHEN = "когда"
COL_STATE = "состояние"

COLUMNS = (COL_NUM, COL_QUESTION, COL_BOT, COL_RIGHT, COL_WHY, COL_WHO, COL_WHEN, COL_STATE)
HEADER_LINE = "\t".join(COLUMNS)

# Индексы колонок, которые правятся ПО МЕСТУ (см. `_replace_fields`). Считаются из COLUMNS, а не
# пишутся числами: порядок колонок — часть формата, и разъехаться эти два места не должны.
IDX_WHY = COLUMNS.index(COL_WHY)
IDX_STATE = COLUMNS.index(COL_STATE)

# Шесть обязательных полей урока (решение владельца). Номер и состояние ставит хранилище.
REQUIRED_FIELDS = (COL_QUESTION, COL_BOT, COL_RIGHT, COL_WHY, COL_WHO, COL_WHEN)

# Четыре поля, которые несут ЖИВОЙ ТЕКСТ и потому чистятся от персонального. `кто_записал`
# и `когда` сюда не входят — см. шапку.
SCRUBBED_FIELDS = (COL_QUESTION, COL_BOT, COL_RIGHT, COL_WHY)

STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"
DAY_LEN = 10                       # «2026-08-19» — префикс штампа, по нему идёт разрез «день»

# ---------------------------------------------------------------------------------------
# Состояние строки
# ---------------------------------------------------------------------------------------
STATE_ACTIVE = "актив"
# КАНДИДАТ (05.09.2026). Урок, записанный кнопкой/командой владельца, но ещё НЕ действующий.
# Заведён потому, что у записи и у применения РАЗНАЯ цена ошибки: записать замечание должно быть
# дёшево (иначе урок не запишут вовсе), а начать отвечать по нему всем клиентам — дорого. Поэтому
# кандидат имеет право лежать БЕЗ причины («кандидат, причина не названа»), а действующим
# становится только отдельным действием и только с непустой причиной (`promote`).
# ИНВАРИАНТ, ради которого всё: действующий урок ВСЕГДА с причиной. Кандидат — не действующий:
# `is_active` сравнивает с `актив` и на кандидате отвечает False, поэтому ни одна ветка чтения
# «действующих» кандидата не подхватит.
STATE_CANDIDATE = "кандидат"
CUT_ONE = "урок"
CUT_DAY = "день"
CUT_WHO = "автор"
CUTS = (CUT_ONE, CUT_DAY, CUT_WHO)

_RE_WITHDRAWN = re.compile(r"^снят\((урок|день|автор);([^)]+)\)$")


def withdrawn_state(cut, stamp):
    """Значение поля состояния для снятого урока. Разрез и время — внутри поля (см. шапку)."""
    if cut not in CUTS:
        raise ValueError("неизвестный разрез снятия: %r (знаю %s)" % (cut, ", ".join(CUTS)))
    return "снят(%s;%s)" % (cut, stamp)


def parse_state(value):
    """Поле состояния → (снят ли, разрез, штамп). Неопознанное состояние — ГРОМКОЕ `None`
    в разрезе, а не тихое «активен»: чужая пометка не смеет притворяться активной строкой.

    Кандидат разобран ЯВНОЙ веткой, и это не украшение: без неё `кандидат` не совпал бы ни с
    `актив`, ни с `_RE_WITHDRAWN`, и функция объявила бы живого кандидата СНЯТЫМ с неизвестным
    разрезом. Снятым он от этого не становится — но всякий, кто судит по этому ответу, счёл бы
    его снятым, а откат перестал бы отличать снятое от несозревшего."""
    if value in (STATE_ACTIVE, STATE_CANDIDATE):
        return False, None, None
    m = _RE_WITHDRAWN.match(value)
    if m is None:
        return True, None, None
    return True, m.group(1), m.group(2)


# ---------------------------------------------------------------------------------------
# Экранирование. Одна строка файла = один урок, ВСЕГДА: табуляция и перевод строки внутри
# поля не выживают. Обратное преобразование одним проходом — иначе «\\n» (экранированная
# обратная косая + буква n) при двухпроходной замене превратилась бы в перевод строки.
# ---------------------------------------------------------------------------------------
_BS = chr(92)
_ESCAPES = ((_BS, _BS + _BS), ("\t", _BS + "t"), ("\n", _BS + "n"), ("\r", _BS + "r"))
_UNESCAPE = {"t": "\t", "n": "\n", "r": "\r", _BS: _BS}
_RE_ESCAPED = re.compile(re.escape(_BS) + "(.)", re.S)


def esc(value):
    """Текст поля → безопасный для TSV кусок. Порядок замен значим: косая идёт ПЕРВОЙ."""
    out = value
    for src, dst in _ESCAPES:
        out = out.replace(src, dst)
    return out


def unesc(value):
    """Обратно. Чужая последовательность (`\\q`) переносится ДОСЛОВНО: файл правят руками,
    и молча съесть непонятый символ — это потеря текста урока."""
    def back(m):
        known = _UNESCAPE.get(m.group(1))
        if known is None:
            return m.group(0)
        return known
    return _RE_ESCAPED.sub(back, value)


# ---------------------------------------------------------------------------------------
# Отказ в записи. Причина называется СЛОВАМИ владельцу, а не кодом.
# ---------------------------------------------------------------------------------------
REASON_WHY_EMPTY = ("урок НЕ записан: пустое «почему». Причина обязательна — без неё урок "
                    "нельзя ни проверить, ни отменить осмысленно (единственное жёсткое "
                    "требование владельца)")
REASON_EMPTY = "урок НЕ записан: пустое обязательное поле «%s»"
REASON_TYPE = "урок НЕ записан: поле «%s» должно быть текстом, пришло %s"
# СТРАХОВКА, а не рабочая ветка: сегодняшний детектор ПОДСТАВЛЯЕТ МЕТКУ («<телефон_1>»), а не
# вырезает текст, поэтому непустое поле пустым после чистки не становится ни на одном входе —
# замерено (`test_only_personal_field_becomes_a_label`). Ветка оставлена на случай, если
# детектор когда-нибудь начнёт вырезать: тогда пустое обязательное поле обязано ОТКАЗАТЬСЯ,
# а не проехать пустым. Честно: это непройденная ветка, и она названа непройденной.
REASON_SCRUBBED_OUT = ("урок НЕ записан: поле «%s» после вычистки персонального стало пустым — "
                       "в нём не было ничего, кроме персональных данных")


class LessonRejected(ValueError):
    """Урок не принят хранилищем. Несёт ИМЯ поля и причину словами."""

    def __init__(self, reason, field=None):
        ValueError.__init__(self, reason)
        self.reason = reason
        self.field = field


# ---------------------------------------------------------------------------------------
# Чистка персонального
# ---------------------------------------------------------------------------------------
# Словарь имён — ВСТРОЕННЫЙ список детектора, без корпуса: `build_gazetteer(())` отдаёт
# основы только из `BUILTIN_NAMES` за вычетом защищённых, бизнес- и общих латинских слов.
# Корпус живых диалогов здесь не читается ни одной веткой.
NAME_STEMS = A.build_gazetteer(())

Cleaned = namedtuple("Cleaned", "question bot_answer correct why hits")


def scrub_lesson(question, bot_answer, correct, why):
    """Четыре текстовых поля урока → вычищенные + счётчик сработавших видов персонального.

    Метки общие на ОДИН урок: один и тот же человек называется `Лицо_1` во всех четырёх
    полях. Между уроками общих меток нет — словарь меток умирает вместе с вызовом."""
    labels = A._Labels()
    stats = Counter()
    values = (question, bot_answer, correct, why)
    for name, value in zip(SCRUBBED_FIELDS, values):
        if not isinstance(value, str):
            raise TypeError(REASON_TYPE % (name, type(value).__name__))
    clean = [A._clean_text(v, labels, NAME_STEMS, stats) for v in values]
    return Cleaned(clean[0], clean[1], clean[2], clean[3], dict(stats))


# ---------------------------------------------------------------------------------------
# Чтение таблицы
# ---------------------------------------------------------------------------------------
Lesson = namedtuple("Lesson", "number question bot_answer correct why who when state line")
Store = namedtuple("Store", "lessons reading broken exists path")


def _path(path):
    if path is None:
        return STORE_PATH
    return path


def now_stamp(now=None):
    """Штамп UTC ISO-8601. Часы подменяемы: тест не зависит от настоящего времени."""
    moment = time.gmtime() if now is None else time.gmtime(now)
    return time.strftime(STAMP_FMT, moment)


def day_of(stamp):
    """Штамп → сутки «ГГГГ-ММ-ДД» (ключ разреза «день»)."""
    return stamp[:DAY_LEN]


def _leading_number(raw):
    """Первое поле битой строки → номер, если он там всё-таки читается. Иначе `None`
    («не знаю»), а не 0: подставленный ноль занял бы чужой номер."""
    head = raw.split("\t")[0].strip()
    if head.isdigit():
        return int(head)
    return None


def load(path=None):
    """Таблица целиком → Store. Сбой чтения ГРОМКИЙ (исключение наверх), отсутствие файла —
    названное состояние `exists=False`, а не притворная пустота.

    `reading` — контракт `parse_outcome`: осмотрено строк данных / опознано уроков. Файл из
    непустых, но неразобранных строк даёт исход НЕРАЗБОР, а не «уроков нет»."""
    target = _path(path)
    if not os.path.isfile(target):
        return Store((), parse_outcome.reading(0, 0), (), False, target)

    lessons, broken, seen = [], [], 0
    with open(target, encoding="utf-8", newline="") as f:
        for idx, line in enumerate(f, start=1):     # ПОТОКОМ: файл целиком в память не берём
            row = line.rstrip("\n").rstrip("\r")
            if len(row.strip()) == 0:
                continue
            if idx == 1 and row == HEADER_LINE:
                continue
            seen += 1
            parts = row.split("\t")
            if len(parts) != len(COLUMNS) or not parts[0].strip().isdigit():
                broken.append((idx, row))
                continue
            lessons.append(Lesson(int(parts[0].strip()), unesc(parts[1]), unesc(parts[2]),
                                  unesc(parts[3]), unesc(parts[4]), unesc(parts[5]),
                                  unesc(parts[6]), unesc(parts[7]), idx))
    return Store(tuple(lessons), parse_outcome.reading(seen, len(lessons)),
                 tuple(broken), True, target)


def norm_who(who):
    """Ключ автора: без пробелов по краям, без ведущей собачки, регистр не значим."""
    return who.strip().lstrip("@").casefold()


def is_active(lesson):
    return lesson.state == STATE_ACTIVE


def is_candidate(lesson):
    return lesson.state == STATE_CANDIDATE


def active(lessons):
    """Только ДЕЙСТВУЮЩИЕ. Кандидаты сюда не попадают ни одной веткой — это и есть замок
    «действующий урок всегда с причиной»: без `promote` (а он без причины отказывает) строка
    состояние `актив` не получает."""
    return tuple(les for les in lessons if is_active(les))


def candidates(lessons):
    """Только кандидаты — записанные, но ещё не действующие."""
    return tuple(les for les in lessons if is_candidate(les))


def by_author(lessons, who):
    """Уроки одного человека (снятые тоже — фильтруй `active()` сверху, если не нужны)."""
    key = norm_who(who)
    return tuple(les for les in lessons if norm_who(les.who) == key)


def by_day(lessons, day):
    """Уроки за сутки «ГГГГ-ММ-ДД»."""
    key = day.strip()
    return tuple(les for les in lessons if day_of(les.when) == key)


# ---------------------------------------------------------------------------------------
# Запись урока
# ---------------------------------------------------------------------------------------
def validate(question, bot_answer, correct, why, who, when, why_required=True):
    """Шесть обязательных полей → (принят ли, причина словами, имя поля).

    Чистая функция: файла не трогает, вызывается и отдельно — до записи.

    `why_required=False` — режим КАНДИДАТА: пустое «почему» пропускается, потому что кандидат
    действующим уроком не является и по нему бот не отвечает никому. Все прочие пять полей
    обязательны и там: без вопроса, ответа бота, правильного ответа, автора и времени кандидат
    нечем ни проверить, ни отозвать. Для ДЕЙСТВУЮЩЕГО урока флаг не трогаем никогда."""
    fields = ((COL_QUESTION, question), (COL_BOT, bot_answer), (COL_RIGHT, correct),
              (COL_WHY, why), (COL_WHO, who), (COL_WHEN, when))
    for name, value in fields:
        if not isinstance(value, str):
            return False, REASON_TYPE % (name, type(value).__name__), name
    for name, value in fields:
        if len(value.strip()) == 0:
            if name == COL_WHY:
                if not why_required:
                    continue
                return False, REASON_WHY_EMPTY, name
            return False, REASON_EMPTY % name, name
    return True, "", None


def _next_number(store):
    """Следующий номер: максимум занятого + 1. Битые строки в счёт ВХОДЯТ (их номер может
    читаться) — иначе новый урок сел бы на чужой номер."""
    top = 0
    for les in store.lessons:
        if les.number > top:
            top = les.number
    for _lineno, raw in store.broken:
        got = _leading_number(raw)
        if got is not None and got > top:
            top = got
    return top + 1


def _append_row(target, row, need_header):
    """Дозапись ОДНОЙ строки. `"a"` не усекает файл ни при каких обстоятельствах."""
    with open(target, "a", encoding="utf-8", newline="") as f:
        if need_header:
            f.write(HEADER_LINE + "\n")
        f.write(row + "\n")
        f.flush()
        os.fsync(f.fileno())


def _add_row(question, bot_answer, correct, why, who, when, path, now, state, why_required):
    """Общее тело `add`/`add_candidate` → НОМЕР записанного.

    Порядок сознателен: сначала проверка обязательных полей на СЫРОМ входе (пустое «почему»
    отказывается до всякой работы), потом вычистка персонального, потом повторная проверка
    непустоты (поле, состоявшее из одних персональных данных, не должно проехать пустым)."""
    stamp = now_stamp(now) if when is None else when
    ok, reason, field = validate(question, bot_answer, correct, why, who, stamp,
                                 why_required=why_required)
    if not ok:
        raise LessonRejected(reason, field=field)

    clean = scrub_lesson(question, bot_answer, correct, why)
    values = ((COL_QUESTION, clean.question), (COL_BOT, clean.bot_answer),
              (COL_RIGHT, clean.correct), (COL_WHY, clean.why))
    for name, value in values:
        # У кандидата «почему» законно пусто ВХОДОМ — проверять его на «вычистилось в пустоту»
        # нечего: пустым оно и пришло. Прочие три поля проверяются всегда.
        if name == COL_WHY and not why_required and len(why.strip()) == 0:
            continue
        if len(value.strip()) == 0:
            raise LessonRejected(REASON_SCRUBBED_OUT % name, field=name)

    target = _path(path)
    store = load(target)
    number = _next_number(store)
    need_header = (not store.exists) or os.path.getsize(target) == 0
    row = "\t".join((str(number), esc(clean.question), esc(clean.bot_answer),
                     esc(clean.correct), esc(clean.why), esc(who.strip()),
                     esc(stamp), state))
    _append_row(target, row, need_header)
    return number


def add(question, bot_answer, correct, why, who, when=None, path=None, now=None):
    """Записать ДЕЙСТВУЮЩИЙ урок (состояние `актив`). → НОМЕР записанного.

    «Почему» обязательно и здесь остаётся обязательным: это единственная дорога, кладущая строку
    сразу действующей, и инвариант «действующий урок всегда с причиной» держится ею."""
    return _add_row(question, bot_answer, correct, why, who, when, path, now,
                    STATE_ACTIVE, True)


def add_candidate(question, bot_answer, correct, who, why="", when=None, path=None, now=None):
    """Записать КАНДИДАТА (состояние `кандидат`). → НОМЕР записанного.

    Отличие от `add` ровно одно: «почему» разрешено пустым. Ни одна ветка НЕ подставляет причину
    из текста урока — пусто значит пусто, и таким кандидат и ложится. Подстановка была бы хуже
    пустоты: у каждого урока появилось бы «почему», которого владелец не говорил, и отличить
    названную причину от придуманной стало бы нечем.

    Порядок аргументов иной, чем у `add` (`who` перед `why`), СОЗНАТЕЛЬНО: у кандидата причины
    обычно нет, и позиционный вызов не должен уметь молча сдвинуть автора в графу причины."""
    return _add_row(question, bot_answer, correct, why, who, when, path, now,
                    STATE_CANDIDATE, False)


# ---------------------------------------------------------------------------------------
# Снятие: три разреза владельца. Строка ОСТАЁТСЯ, меняется только последнее поле.
# ---------------------------------------------------------------------------------------
# `lines_*` считает ФИЗИЧЕСКИЕ строки файла (шапка входит) — это и есть доказательство, что
# снятие ничего не удалило: числа обязаны совпасть.
WithdrawResult = namedtuple("WithdrawResult", "cut key marked already lines_before lines_after")


def _matches(lesson, cut, key):
    if cut == CUT_ONE:
        return lesson.number == key
    if cut == CUT_DAY:
        return day_of(lesson.when) == key
    return norm_who(lesson.who) == key


def _replace_state(raw, state):
    """Последнее поле СЫРОЙ строки → новое состояние. Остальные байты не трогаются вовсе:
    круг «разобрать → собрать» не байт-в-байт на чужих экранированных последовательностях."""
    head, _tail = raw.rsplit("\t", 1)
    return head + "\t" + state


def _replace_fields(raw, by_index):
    """Названные колонки СЫРОЙ строки → новые УЖЕ ЭКРАНИРОВАННЫЕ значения; остальные байты
    переносятся дословно. Разбор здесь идёт только по табуляции и БЕЗ `unesc`/`esc` — то есть
    нетронутые поля не проходят круг «разобрать → собрать», на котором чужая последовательность
    могла бы поменяться. Строка не той ширины не правится вовсе (лучше не тронуть, чем испортить)."""
    parts = raw.split("\t")
    if len(parts) != len(COLUMNS):
        return raw
    for idx, value in by_index.items():
        parts[idx] = value
    return "\t".join(parts)


def _count_lines(target):
    """Физических строк в файле (шапка входит). Потоком: размер файла не уходит в память."""
    total = 0
    with open(target, encoding="utf-8", newline="") as f:
        for _line in f:
            total += 1
    return total


def _rewrite(target, edits_by_line):
    """Единственное место, трогающее уже лежащие байты. Копия `.bak` кладётся ДО правки,
    новое тело пишется во ВРЕМЕННЫЙ файл (усекается ОН, не таблица) и въезжает атомарным
    `os.replace`. → (строк прочитано, строк записано) — числа обязаны совпасть.

    `edits_by_line` — {номер физической строки: {индекс колонки: экранированное значение}}.
    Каждая строка источника переписывается в приёмник; меняются только названные поля названных
    строк. Ни одна ветка не пропускает строку — в этом и состоит «не удаляем»."""
    shutil.copyfile(target, target + BACKUP_SUFFIX)
    tmp = target + TMP_SUFFIX
    lines_in, lines_out = 0, 0
    with open(target, encoding="utf-8", newline="") as src:
        with open(tmp, "w", encoding="utf-8", newline="") as dst:
            for idx, line in enumerate(src, start=1):
                lines_in += 1
                body = line.rstrip("\n").rstrip("\r")
                edit = edits_by_line.get(idx)
                if edit:
                    body = _replace_fields(body, edit)
                dst.write(body + "\n")
                lines_out += 1
            dst.flush()
            os.fsync(dst.fileno())
    os.replace(tmp, target)
    return lines_in, lines_out


def withdraw(number=None, day=None, who=None, path=None, now=None):
    """Снять уроки одним из ТРЁХ разрезов владельца: `number=` (один урок), `day=` (все за
    сутки), `who=` (всё записанное одним человеком). → WithdrawResult.

    Строки НЕ УДАЛЯЮТСЯ: меняется только поле состояния, след остаётся. `lines_before` и
    `lines_after` в ответе — это доказательство числом, что ни одна строка не пропала."""
    given = [(CUT_ONE, number), (CUT_DAY, day), (CUT_WHO, who)]
    named = [(cut, val) for cut, val in given if val is not None]
    if len(named) != 1:
        raise ValueError("снятие требует РОВНО одного разреза из трёх (number=/day=/who=), "
                         "передано: %d" % len(named))
    cut, key = named[0]
    if cut == CUT_ONE:
        key = int(key)
    elif cut == CUT_DAY:
        key = key.strip()
    else:
        key = norm_who(key)

    target = _path(path)
    store = load(target)
    if not store.exists:
        # Таблицы ещё нет — снимать нечего. `lines_before=0` называет это однозначно: ноль
        # ФИЗИЧЕСКИХ строк бывает только у несуществующего файла, у пустого есть шапка.
        return WithdrawResult(cut, key, (), (), 0, 0)
    stamp = now_stamp(now)
    state = withdrawn_state(cut, stamp)

    hit_lines, marked, already = {}, [], []
    for les in store.lessons:
        if not _matches(les, cut, key):
            continue
        # КАНДИДАТ СНИМАЕТСЯ НАРАВНЕ С ДЕЙСТВУЮЩИМ. Иначе ошибочно записанный кандидат остался бы
        # в таблице навсегда: `active()` его не показывает, а `withdraw()` считал бы «уже снят» —
        # и владелец, отзывая свой урок, получал бы «нечего снимать» при живой строке.
        if is_active(les) or is_candidate(les):
            hit_lines[les.line] = {IDX_STATE: state}
            marked.append(les.number)
        else:
            already.append(les.number)

    if len(hit_lines) > 0:
        lines_before, lines_after = _rewrite(target, hit_lines)
    else:
        lines_before = _count_lines(target)        # ничего не снято — файла не касаемся вовсе
        lines_after = lines_before

    return WithdrawResult(cut, key, tuple(marked), tuple(already), lines_before, lines_after)


# ---------------------------------------------------------------------------------------
# Переход кандидата в действующие — ОТДЕЛЬНОЕ действие и ТОЛЬКО с непустой причиной
# ---------------------------------------------------------------------------------------
# Здесь живёт инвариант всей затеи: строка получает состояние `актив` РОВНО в двух местах —
# в `add()` (где «почему» обязательно проверкой) и здесь. Третьей дороги в действующие нет, и
# обе требуют непустой причины. Поэтому «действующий урок без причины» невозможен не по
# дисциплине вызывающего, а по устройству.
REASON_PROMOTE_NO_WHY = ("кандидат НЕ переведён в действующие: причина не названа. Причину не "
                         "подставляем из текста урока — пусто значит пусто; назовите «почему» "
                         "словами и повторите")
REASON_PROMOTE_MISSING = "кандидата #%s в таблице нет"
REASON_PROMOTE_NOT_CANDIDATE = "урок #%s не кандидат, а «%s» — переводить нечего"

PromoteResult = namedtuple("PromoteResult", "ok number why reason lines_before lines_after")


def promote(number, why="", path=None, now=None):
    """Кандидат #number → ДЕЙСТВУЮЩИЙ урок. → PromoteResult (не бросает: отказ — это ответ).

    Причина берётся из аргумента `why`, а при пустом аргументе — из самой строки (кандидата могли
    записать сразу с причиной). Обе пусты → ОТКАЗ: ни текст урока, ни вопрос клиента, ни что-либо
    ещё в причину НЕ превращается. Это не осторожность, а смысл поля: «почему», собранное машиной
    из текста урока, отвечает на вопрос «что написано», а не «почему так правильно», и владелец
    не сможет отличить свою причину от подставленной.

    Право на этот переход судит ВЫЗЫВАЮЩИЙ (у полосы ПК — `moderation_core.may_write_rule`,
    fail-closed): хранилище имён и списков не знает и знать не должно.

    `lines_before`/`lines_after` в ответе — доказательство числом, что перевод не потерял строк."""
    target = _path(path)
    store = load(target)
    if not store.exists:
        return PromoteResult(False, number, "", REASON_PROMOTE_MISSING % number, 0, 0)

    found = None
    for les in store.lessons:
        if les.number == int(number):
            found = les
            break
    lines = _count_lines(target)
    if found is None:
        return PromoteResult(False, number, "", REASON_PROMOTE_MISSING % number, lines, lines)
    if not is_candidate(found):
        return PromoteResult(False, found.number, found.why,
                             REASON_PROMOTE_NOT_CANDIDATE % (found.number, found.state),
                             lines, lines)

    given = why if isinstance(why, str) else ""
    final_why = given.strip() or found.why.strip()
    if len(final_why) == 0:
        return PromoteResult(False, found.number, "", REASON_PROMOTE_NO_WHY, lines, lines)

    # Причина — живой текст владельца, и чистится тем же детектором, что остальные поля. Метки
    # считаются заново, поэтому `Лицо_1` в новой причине может обозначать НЕ того человека, что
    # `Лицо_1` в уже лежащем вопросе: исходников тех полей у нас больше нет (они уже вычищены).
    # Названо здесь, а не спрятано; цена — путаница меток внутри одного урока, а не утечка.
    cleaned = scrub_lesson(found.question, found.bot_answer, found.correct, final_why).why
    ok, reason, field = validate(found.question, found.bot_answer, found.correct, cleaned,
                                 found.who, found.when)
    if not ok:
        return PromoteResult(False, found.number, cleaned, reason, lines, lines)

    before, after = _rewrite(target, {found.line: {IDX_WHY: esc(cleaned),
                                                  IDX_STATE: STATE_ACTIVE}})
    del field                                   # имя поля отказа здесь не нужно — ветка успешная
    return PromoteResult(True, found.number, cleaned, "", before, after)


# ---------------------------------------------------------------------------------------
# Ёмкость и целостность
# ---------------------------------------------------------------------------------------
# ПОРОГ КОМФОРТА — ИЗМЕРЕН, а не назначен (замер 19.08.2026 на строках живого размера,
# 630 символов ≈ 1 КБ; docs/artifacts/2026-08-19-lesson-store.md §5):
#
#   уроков   файл      load()    add()     withdraw(день)
#     1 000    1.0 МБ   0.014 с   0.011 с   0.025 с
#    10 000   10.0 МБ   0.078 с   0.092 с   0.172 с
#    50 000   50.2 МБ   0.370 с   0.422 с   0.831 с
#   100 000  100.5 МБ   0.717 с   0.869 с   1.639 с
#   200 000  201.1 МБ   1.555 с   1.722 с   5.845 с
#
# ЧТЕНИЕ и ЗАПИСЬ растут ЛИНЕЙНО на всём диапазоне (≈130 000 строк/с), обрыва-ступеньки нет.
# У СНЯТИЯ линейность кончается за 100 000: 200 000 стоят 5.845 с вместо ожидаемых ~3.3 — оно
# единственное копирует файл целиком (`.bak` перед перезаписью), и на 200 МБ это упирается уже
# в диск, а не в разбор. Названо здесь, а не спрятано: это ещё один довод за порог. Порог взят
# по САМОЙ ЧАСТОЙ операции — записи урока — из чужого бюджета: тик модербота 5.000 с (CLAUDE.md,
# ожидание О3), и add() на 50 000 уроков занимает 0.422 с, то есть 8.4 % тика. На 100 000 это
# уже 17.4 %, и порог назван там, где запас ещё двенадцатикратный.
#
# ЧТО ПРОИСХОДИТ НА ПРЕВЫШЕНИИ: ничего, кроме замедления, и оно НАЗЫВАЕТСЯ ВСЛУХ (`capacity()`
# отдаёт `тесно` и фразу). Отказа в записи нет, ротации нет, обрезания нет — строк не убывает
# ни на одной ветке. Единственный физический потолок — место на диске: ≈1 КБ на урок, миллион
# уроков ≈ 1 ГБ файла; в память файл не берётся ни при чтении, ни при снятии (оба идут потоком),
# поэтому упереться в ОЗУ таблица не может.
#
# Для порядка величин: тренажёр за 28 суток (22.07→19.08.2026) накопил 6 правил ≈ 0.21 в сутки —
# при таком темпе порог недостижим в принципе; даже при 10 уроках В ДЕНЬ до него 13.7 года.
COMFORT_LESSONS = 50000
CAPACITY_FREE = "свободно"
CAPACITY_TIGHT = "тесно"

Capacity = namedtuple("Capacity", "count comfort state bytes say")


def capacity(path=None):
    """Сколько уроков лежит и не тесно ли. ОТКАЗА ПО ПЕРЕПОЛНЕНИЮ НЕТ: за порогом таблица
    работает медленнее и говорит об этом вслух, но не теряет ни строки и не отказывает."""
    target = _path(path)
    store = load(target)
    count = len(store.lessons) + len(store.broken)
    size = os.path.getsize(target) if store.exists else 0
    if count > COMFORT_LESSONS:
        state = CAPACITY_TIGHT
        say = ("уроков %d — БОЛЬШЕ порога комфорта %d: чтение таблицы линейно замедляется. "
               "Ничего не потеряно и не будет: записи не стираются ни одной веткой"
               % (count, COMFORT_LESSONS))
    else:
        state = CAPACITY_FREE
        say = "уроков %d из %d порога комфорта" % (count, COMFORT_LESSONS)
    return Capacity(count, COMFORT_LESSONS, state, size, say)


Integrity = namedtuple("Integrity", "ok duplicates broken reading say")


def integrity(path=None):
    """Целостность таблицы числами: дублирующиеся номера и неразобранные строки."""
    store = load(path)
    seen = Counter(les.number for les in store.lessons)
    dups = tuple(sorted(num for num, cnt in seen.items() if cnt > 1))
    ok = len(dups) == 0 and len(store.broken) == 0
    if ok:
        say = "целостность в порядке: %s" % store.reading.say("строк")
    else:
        say = ("ПРОБЛЕМЫ: дублей номеров %d, неразобранных строк %d; %s"
               % (len(dups), len(store.broken), store.reading.say("строк")))
    return Integrity(ok, dups, store.broken, store.reading, say)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def _render(lesson):
    return ("#%d [%s] %s | %s\n    вопрос: %s\n    ответил бот: %s\n    как правильно: %s\n"
            "    почему: %s" % (lesson.number, lesson.state, lesson.who, lesson.when,
                                lesson.question, lesson.bot_answer, lesson.correct, lesson.why))


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description="хранилище уроков тренажёра (чтение)")
    ap.add_argument("--status", action="store_true", help="ёмкость и целостность числами")
    ap.add_argument("--list", action="store_true", help="перечислить уроки")
    ap.add_argument("--all", action="store_true", help="вместе со снятыми")
    ap.add_argument("--who", help="только уроки этого человека")
    ap.add_argument("--day", help="только уроки за сутки ГГГГ-ММ-ДД")
    ap.add_argument("--path", help="другой файл таблицы (по умолчанию %s)" % STORE_NAME)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    store = load(args.path)
    if not args.list:
        cap = capacity(args.path)
        integ = integrity(args.path)
        print("файл: %s (%s)" % (store.path, "есть" if store.exists else "ещё не заведён"))
        print("ёмкость: %s; байт %d" % (cap.say, cap.bytes))
        cands = candidates(store.lessons)
        print("действующих: %d; кандидатов: %d; снятых: %d"
              % (len(active(store.lessons)), len(cands),
                 len(store.lessons) - len(active(store.lessons)) - len(cands)))
        print(integ.say)
        return 0

    rows = store.lessons
    if args.who is not None:
        rows = by_author(rows, args.who)
    if args.day is not None:
        rows = by_day(rows, args.day)
    if not args.all:
        rows = active(rows)
    for les in rows:
        print(_render(les))
    print("— всего строк показано: %d" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
