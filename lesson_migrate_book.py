# -*- coding: utf-8 -*-
"""
lesson_migrate_book.py — ПЕРЕНОС ПЛОСКОЙ КНИГИ ПРАВИЛ В БАЗУ УРОКОВ (06.09.2026).

ЗАЧЕМ. Правила, которыми бот пользуется СЕГОДНЯ, живут одной плоской строкой в
`manager-bot/docs/playbook.md` и одним ключом в сайдкаре `trainer_rules.json`. У такой формы
есть текст — и больше ничего: ни автора, ни причины, ни номера, ни отката. Новые уроки уже
ложатся в `lesson_store` кандидатами (`trainer.lesson_candidate`, 05.09), а СТАРЫЕ так и
остались снаружи. Здесь они получают номер, автора, время и причину — то есть становятся
откатываемыми той же тройкой разрезов (`урок` / `день` / `автор`), что и все прочие.

ЧТО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ, И ЭТО ГЛАВНОЕ:

  • ПЛОСКУЮ КНИГУ НЕ ТРОГАЕТ НИ БАЙТОМ. Открывает её РОВНО одним способом — на чтение; ни
    `"w"`, ни `"a"`, ни `os.remove`, ни `suggest.append_playbook_rule`/`remove_playbook_rule`
    в файле нет ни одной ветки (заперто разбором собственного исходника,
    `test_lesson_migrate_book.TestBookNeverWritten`). Перенос здесь — это КОПИЯ, а не переезд:
    книга остаётся действующим источником, из которого бот читает правила, и снимком на своём
    месте. Сайдкар `trainer_rules.json` не трогается тем же порядком.
  • ТЕКСТ ПРАВИЛА НЕ ПРАВИТ. В поле «как правильно» ложится тело буллета ДОСЛОВНО, как его
    написал владелец. Ни сокращений, ни склейки похожих, ни правки орфографии: «здароваемся в
    приветаенном автосообщении» переезжает ровно так, с опечатками автора.
  • ПРИЧИНУ НЕ ВЫДУМЫВАЕТ. У всех перенесённых записей причина ОДНА и служебная — `перенос
    05.09` (`MIGRATION_WHY`). Она честно говорит одно: владелец причину этого правила не
    называл, а строка перенесена оптом. Причину, взятую из текста правила, отличить от
    названной владельцем было бы нечем — поэтому её здесь нет.
  • В КРУГ БОТА НЕ ВХОДИТ. Модуль зовётся РУКАМИ (CLI ниже) и не импортируется ни одним файлом
    боевого пути; `test_lesson_store.TestBotNotWired` сторожит СВОЙ список семи файлов, и этого
    среди них нет и быть не должно.

ПОЧЕМУ ЗАПИСИ ЛОЖАТСЯ ДЕЙСТВУЮЩИМИ (`актив`), А НЕ КАНДИДАТАМИ. Кандидат — это урок, по
которому бот ещё НЕ отвечает. Переносимые правила бот соблюдает уже сегодня: книга подмешивается
в промпт на каждый черновик (`suggest.load_playbook` → `make_system_prompt`, зовётся из
`userbot_listen` и `suggest.on_new_message`). Положить их кандидатами значило бы записать в базу,
что они не действуют, — и база соврала бы о живом поведении бота. Причина у них непустая
(служебная), поэтому инвариант «действующий урок всегда с причиной» держится.

ИСТОЧНИК НАБОРА (09.09.2026): всё, что вливает этот модуль, помечается `перенос_книги`
(`MIGRATION_SOURCE`, значение из списка `lesson_store.SOURCES`). До 09.09 набор опознавался
СОГЛАШЕНИЕМ внутри свободного текста причины (`перенос 05.09`) и именем автора (`владелец`) —
и ни то, ни другое источником не является: причину владелец вправе переписать словами, а тем же
именем будет подписано всё, что он запишет завтра руками. Теперь у набора есть ключ, и снимается
он целиком: `lesson_store.withdraw(source=lesson_store.SOURCE_BOOK)`.

ОСТОРОЖНО СО СЛОВОМ «ИСТОЧНИК» В ЭТОМ ФАЙЛЕ: оно тут в ДВУХ разных смыслах. `MIGRATION_SOURCE` —
источник НАБОРА (колонка таблицы). `NO_SOURCE_Q`/`NO_SOURCE_A` ниже — про то, что у переносимого
правила нет ИСХОДНИКА двух полей (вопроса клиента и ответа бота). Третий смысл — у сайдкара
`trainer_rules.json`, где «источник» значит «кто автор правила книги».

ДВА ПОЛЯ ИЗ ШЕСТИ У ПЕРЕНОСИМОГО ПРАВИЛА ИСХОДНИКА НЕ ИМЕЮТ, и это названо, а не залатано.
Хранилище требует непустыми все шесть; плоская книга хранит ОДНО — текст правила. Вопроса
клиента и ответа бота за буллетом не стои́т нигде: их не сохранял никто. Поэтому в эти две
графы ложится ЯВНАЯ служебная отметка (`NO_SOURCE_Q` / `NO_SOURCE_A`), которая говорит «поля не
было», а не выдуманная переписка. Отличить её от настоящего вопроса клиента можно грепом.

ВРЕМЯ — ДАТА СНИМКА, И ОНА ПРОВЕРЯЕТСЯ, А НЕ ОБЪЯВЛЯЕТСЯ. Штамп берётся из ИМЕНИ файла снимка
`docs/lesson_base/playbook-ГГГГ-ММ-ДД.md`, и перед переносом снимок сверяется с живой книгой
ПОБАЙТНО (sha256). Разошлись — перенос ОТКАЗЫВАЕТ целиком: дата снимка перестала описывать тот
текст, который переносится, и молча подставить «сегодня» значило бы приписать правилам время,
которого у них нет. Честная цена этого выбора названа: СОБСТВЕННАЯ дата правила в книге (у 6 из
9 буллетов префикс `(2026-07-14)` / `(2026-07-22)`) в поле «когда» НЕ едет — там у всех дата
снимка. Потерей это не становится ровно потому, что книга остаётся на месте байт в байт.

БОКОВОЙ СПИСОК И «ЗАПИСЬ БЕЗ СОДЕРЖАНИЯ» — КРИТЕРИЙ ИЗ КОДА, А НЕ ИЗ ГЛАЗА. Сайдкар
`trainer_rules.json` — это словарь «ПРАВИЛО КНИГИ → источник» (`trainer.mark_source`), а не
вторая книга. Значит запись, чей ключ не совпал НИ С ОДНИМ правилом книги, за собой правила не
имеет: она осталась от того, что `mark_source` зовётся и на исходе `duplicate`, когда нового
буллета в книге не появляется (`trainer.py`, комментарий к сайдкару). Такие записи НЕ
переносятся и НАЗЫВАЮТСЯ ПОИМЁННО в отчёте — решать их судьбу полоса не смеет, это вопрос
владельцу. Граница названа честно и в другую сторону: если правило когда-нибудь удалят из
книги, а пометку оставят, эта ветка сочтёт живое правило «записью без содержания» — потому
отчёт и печатает такие ключи целиком, а не считает их числом.

ДУБЛИ СЧИТАЮТСЯ И ПЕРЕНОСЯТСЯ ОДИН РАЗ. Совпадение — по ключу `trainer._norm_rule` (тот самый,
которым сайдкар ищет свою пометку: схлопнутые пробелы, снятый регистр, снятые дефис буллета и
дата-префикс). Совпавшая запись сайдкара НЕ добавляет строки в базу: правило уже поехало из
книги, а два номера у одного правила сделали бы откат неполным.

ПОВТОРНЫЙ ЗАПУСК НЕ ЗАДВАИВАЕТ. Перед записью читается вся таблица, и правило пропускается,
если в ней уже лежит строка с ТЕМ ЖЕ ключом `_norm_rule` и той же служебной причиной. Ключ
сознательно не «номер» и не «точный текст»: номер у переноса свой, а точный текст сравнивать
опасно — экранирование TSV и чужая правка руками разошлись бы на пробеле.

ЗАПУСК:
    venv/Scripts/python.exe lesson_migrate_book.py --dry      # план числами, НИЧЕГО не пишет
    venv/Scripts/python.exe lesson_migrate_book.py --apply    # перенос
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import namedtuple

import io_utf8
import lesson_store
import suggest
import trainer

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "docs", "lesson_base")
_RE_SNAP = re.compile(r"^playbook-(\d{4}-\d{2}-\d{2})\.md$")

# Служебные значения переноса. Все три — литералы задания владельца, а не сочинение полосы.
MIGRATION_WHY = "перенос 05.09"
MIGRATION_WHO = "владелец"
# ИСТОЧНИК НАБОРА (09.09.2026) — берётся из СПИСКА хранилища, а не пишется здесь литералом:
# значения источника живут одним местом (`lesson_store.SOURCES`), иначе опечатка в этом файле
# завела бы набор, который снятием по источнику не достать.
MIGRATION_SOURCE = lesson_store.SOURCE_BOOK
NO_SOURCE_Q = ("(перенос 05.09) вопрос клиента не сохранён — плоская книга правил такого поля "
               "не имела")
NO_SOURCE_A = ("(перенос 05.09) ответ бота не сохранён — плоская книга правил такого поля "
               "не имела")
STAMP_TIME = "T00:00:00Z"          # дата снимка + полночь UTC: разрез «день» ловит её целиком


# ---------------------------------------------------------------------------------------
# Снимок книги: дата переноса и замок побайтности
# ---------------------------------------------------------------------------------------
Snapshot = namedtuple("Snapshot", "ok path day stamp book_sha snap_sha say")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshots(snap_dir=None):
    """Снимки книги в `docs/lesson_base` → [(дата, путь)] по возрастанию даты."""
    target = snap_dir or SNAP_DIR
    out = []
    try:
        names = os.listdir(target)
    except OSError:
        return out
    for name in names:
        m = _RE_SNAP.match(name)
        if m is not None:
            out.append((m.group(1), os.path.join(target, name)))
    out.sort()
    return out


def latest_snapshot(book_path=None, snap_dir=None):
    """Свежайший снимок + СВЕРКА с живой книгой побайтно. → Snapshot(ok, …).

    `ok=False` — это отказ переносить, а не предупреждение: дата снимка описывает ТОТ текст,
    который снимали, и на разошедшейся книге она была бы подписью под чужим содержимым."""
    book = book_path or suggest.PLAYBOOK_FILE
    found = snapshots(snap_dir)
    if not found:
        return Snapshot(False, None, None, None, None, None,
                        "снимка книги нет в %s — дату переноса взять неоткуда"
                        % (snap_dir or SNAP_DIR))
    day, path = found[-1]
    if not os.path.isfile(book):
        return Snapshot(False, path, day, None, None, None,
                        "живой книги нет на месте: %s" % book)
    book_sha, snap_sha = _sha256(book), _sha256(path)
    if book_sha != snap_sha:
        return Snapshot(False, path, day, None, book_sha, snap_sha,
                        "снимок %s разошёлся с живой книгой (книга %s…, снимок %s…): дата снимка "
                        "перестала описывать переносимый текст — перенос ОТКАЗАН, нужен свежий "
                        "снимок" % (os.path.basename(path), book_sha[:8], snap_sha[:8]))
    return Snapshot(True, path, day, day + STAMP_TIME, book_sha, snap_sha,
                    "снимок %s совпал с живой книгой побайтно (sha256 %s…)"
                    % (os.path.basename(path), book_sha[:8]))


# ---------------------------------------------------------------------------------------
# Что переносим: правила книги + разбор бокового списка
# ---------------------------------------------------------------------------------------
Plan = namedtuple("Plan", "rules dups orphans")


def read_book(book_path=None):
    """Текст живой книги. ТОЛЬКО чтение."""
    with open(book_path or suggest.PLAYBOOK_FILE, encoding="utf-8") as f:
        return f.read()


def read_side(side_path=None):
    """Боковой список «правило → источник». Нет файла/битьё → {} (перенос книги от этого
    не зависит: сайдкар не добавляет строк, он только объясняет дубли)."""
    target = side_path or trainer.TRAINER_RULES_FILE
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_plan(book_text, side):
    """(текст книги, сайдкар) → Plan(правила книги, дубли сайдкара, записи без содержания).

    `dups` — записи сайдкара, за которыми СТОИТ правило книги: они уже едут вместе с книгой и
    отдельной строки не получают. `orphans` — записи, за которыми правила книги нет; они не
    переносятся и называются поимённо."""
    rules = suggest.list_playbook_rules(book_text)
    keys = {}
    for r in rules:
        keys.setdefault(trainer._norm_rule(r["rule"]), r["n"])
    dups, orphans = [], []
    for key in side:
        n = keys.get(trainer._norm_rule(key))
        (dups if n is not None else orphans).append((key, n))
    return Plan(rules, dups, [k for k, _ in orphans])


# ---------------------------------------------------------------------------------------
# Перенос
# ---------------------------------------------------------------------------------------
Result = namedtuple("Result", "ok added skipped numbers dups orphans stamp say")


def migrated_keys(path=None):
    """Ключи `_norm_rule` правил, УЖЕ перенесённых этой дорогой (по служебной причине).

    Строки чужого происхождения (кандидаты тренажёра) в счёт не идут: у них своя причина и своя
    судьба, и молча счесть их «уже перенесёнными» значило бы потерять правило книги.

    ДВА ПРИЗНАКА, А НЕ ОДИН (09.09.2026), и старый оставлен НАРОЧНО. Девять строк, перенесённых
    05–06.09, лежат БЕЗ хвоста источника: формата тогда не было. Перейди эта ветка на один только
    источник — она перестала бы их узнавать, и первый же `--apply` влил бы книгу ВТОРОЙ раз, дав
    каждому правилу два номера и неполный откат. Поэтому «уже перенесено» — это старая служебная
    причина ИЛИ новый источник набора."""
    out = set()
    for les in lesson_store.load(path).lessons:
        if les.why.strip() == MIGRATION_WHY or les.source == MIGRATION_SOURCE:
            out.add(trainer._norm_rule(les.correct))
    return out


def migrate(book_path=None, side_path=None, snap_dir=None, store_path=None, apply=False):
    """Перенос книги и бокового списка в базу уроков. → Result.

    `apply=False` (по умолчанию) — план числами, в таблицу НЕ пишется ни строки."""
    snap = latest_snapshot(book_path, snap_dir)
    if not snap.ok:
        return Result(False, 0, 0, (), (), (), None, snap.say)

    plan = build_plan(read_book(book_path), read_side(side_path))
    have = migrated_keys(store_path)
    numbers, skipped = [], 0
    for r in plan.rules:
        if trainer._norm_rule(r["rule"]) in have:
            skipped += 1
            continue
        if not apply:
            numbers.append(None)
            continue
        numbers.append(lesson_store.add(
            question=NO_SOURCE_Q, bot_answer=NO_SOURCE_A, correct=r["rule"],
            why=MIGRATION_WHY, who=MIGRATION_WHO, source=MIGRATION_SOURCE,
            when=snap.stamp, path=store_path))
    say = ("правил книги %d; перенесено %d, пропущено как уже перенесённое %d; записей бокового "
           "списка %d — из них дублей книги %d (переносятся ОДИН раз, вместе с книгой) и без "
           "содержания %d (НЕ переносятся); время у всех — %s"
           % (len(plan.rules), len(numbers), skipped, len(plan.dups) + len(plan.orphans),
              len(plan.dups), len(plan.orphans), snap.stamp))
    return Result(True, len(numbers), skipped, tuple(numbers), tuple(plan.dups),
                  tuple(plan.orphans), snap.stamp, say)


# ---------------------------------------------------------------------------------------
# Чтение назад: числа, которыми доказывается перенос
# ---------------------------------------------------------------------------------------
ReadBack = namedtuple("ReadBack", "total active_ with_who with_why migrated say")


def read_back(path=None):
    """Таблица глазами читателя: сколько строк, сколько действующих, у скольких есть автор и
    причина. Числа обязаны сойтись с числом перенесённого — иначе перенос не доказан."""
    lessons = lesson_store.load(path).lessons
    total = len(lessons)
    act = len(lesson_store.active(lessons))
    who = sum(1 for l in lessons if l.who.strip())
    why = sum(1 for l in lessons if l.why.strip())
    mig = sum(1 for l in lessons if l.why.strip() == MIGRATION_WHY)
    return ReadBack(total, act, who, why, mig,
                    "в базе строк %d; действующих %d; с автором %d; с причиной %d; из них по "
                    "служебной причине «%s» — %d" % (total, act, who, why, MIGRATION_WHY, mig))


# ---------------------------------------------------------------------------------------
# ПРОСТАВИТЬ ИСТОЧНИК УЖЕ ЛЕЖАЩИМ СТРОКАМ ПЕРЕНОСА (11.09.2026)
# ---------------------------------------------------------------------------------------
# ЗАЧЕМ ЭТО ЗДЕСЬ, А НЕ В ХРАНИЛИЩЕ. Девять строк легли 06.09, когда графы источника ещё не
# существовало, и потому читаются как «источник неизвестен» — то есть разрез `источник` и откат
# набора снимают на них НОЛЬ строк. Проставить источник может только тот, кто ДОКАЗЫВАЕТ
# происхождение, а доказательство живёт ровно здесь: эти строки положил ЭТОТ модуль и оставил на
# них СВОИ литералы. Хранилище номера принимает, но ничьего происхождения не угадывает.
#
# ПРИЗНАК — ДВА ЛИТЕРАЛА СРАЗУ, а не один. `MIGRATION_WHY` — служебная причина, `MIGRATION_WHO` —
# автор переноса; вместе их не ставит ни одна другая дорога записи (тренажёр кладёт причину
# владельца и автором самого владельца-оператора). Одного признака мало: причину «перенос 05.09»
# теоретически может вписать человек руками, и тогда источник уехал бы к строке, которой этот
# модуль не клал. Совпали ОБА — это СОБЫТИЕ переноса, а не сходство.
#
# ТРЕТИЙ ИСХОД НАЗВАН И НЕ СХЛОПНУТ: строка, не несущая обоих литералов, попадает в `unknown` и
# источника НЕ получает. Догадка здесь была бы хуже пустоты — она положила бы чужую строку в
# набор, который однажды снимут целиком.
StampPlan = namedtuple("StampPlan", "numbers unknown total say")


def book_rows(path=None):
    """Строки таблицы, про которые ДОКАЗАНО, что их положил перенос книги. → StampPlan.

    Чистое чтение: файла не трогает. Уже помеченные источником сюда тоже входят — отсеет их
    `lesson_store.set_source`, и отсеет СЧИТАЯ, а не молча."""
    numbers, unknown, total = [], [], 0
    for les in lesson_store.load(path).lessons:
        total += 1
        if (les.why.strip() == MIGRATION_WHY
                and lesson_store.norm_who(les.who) == lesson_store.norm_who(MIGRATION_WHO)):
            numbers.append(les.number)
        else:
            unknown.append(les.number)
    return StampPlan(tuple(numbers), tuple(unknown), total,
                     "строк в таблице %d; происхождение ДОКАЗАНО у %d (оба литерала переноса: "
                     "причина «%s» и автор «%s»); у %d происхождение НЕИЗВЕСТНО — источник им не "
                     "ставится" % (total, len(numbers), MIGRATION_WHY, MIGRATION_WHO,
                                   len(unknown)))


def stamp_source(path=None, apply=False):
    """Проставить доказанным строкам источник `перенос_книги`. → (StampPlan, SetSourceResult|None).

    `apply=False` (по умолчанию) — план числами, в таблицу НЕ пишется ни байта."""
    plan = book_rows(path)
    if not apply or not plan.numbers:
        return plan, None
    return plan, lesson_store.set_source(plan.numbers, MIGRATION_SOURCE, path=path)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------
def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description="перенос книги правил в базу уроков")
    ap.add_argument("--dry", action="store_true", help="план числами, НИЧЕГО не пишет (умолчание)")
    ap.add_argument("--apply", action="store_true", help="выполнить перенос")
    ap.add_argument("--stamp-source", action="store_true",
                    help="проставить источник УЖЕ ЛЕЖАЩИМ строкам переноса (с --apply — записать)")
    ap.add_argument("--path", help="другой файл таблицы уроков")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.stamp_source:
        plan, res = stamp_source(args.path, apply=bool(args.apply))
        print(plan.say)
        if res is None:
            print("это СУХОЙ прогон: в таблицу не записано ни байта (нужен --apply)")
        else:
            print("источник «%s» проставлен: строк %d; уже несли источник %d; названных, но не "
                  "найденных %d; физических строк до %d, после %d"
                  % (res.source, res.stamped, res.already, len(res.missing),
                     res.lines_before, res.lines_after))
        got = lesson_store.version(args.path)
        print("версия базы: %s" % got.say)
        return 0

    snap = latest_snapshot()
    print("снимок: %s" % snap.say)
    res = migrate(store_path=args.path, apply=bool(args.apply))
    print(res.say)
    for key, n in res.dups:
        print("  дубль сайдкара ↔ правило книги #%d: %s" % (n, key))
    for key in res.orphans:
        print("  БЕЗ СОДЕРЖАНИЯ, не перенесено: %r — что с этим делать, решает владелец" % key)
    if not res.ok:
        return 1
    if args.apply:
        print("номера перенесённых: %s" % (", ".join(str(n) for n in res.numbers) or "—"))
    else:
        print("это СУХОЙ прогон: в таблицу не записано ни строки (нужен --apply)")
    print(read_back(args.path).say)
    return 0


if __name__ == "__main__":
    sys.exit(main())
