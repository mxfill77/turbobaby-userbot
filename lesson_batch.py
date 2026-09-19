# -*- coding: utf-8 -*-
"""
lesson_batch.py — ДВЕРЬ, которой владелец СНИМАЕТ НАБОР УРОКОВ ЦЕЛИКОМ и возвращает его обратно
(09.09.2026). Зеркало двери перевода `lesson_promote.py`, объект другой.

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ЕЩЁ ОДИН КЛЮЧ У `lesson_promote.py`. Объекты разные, и путать их
дорого: у перевода объект — ОДИН урок по номеру, у снятия набора — ВЕСЬ набор по ключу источника.
Один CLI с ключами обоих объектов сделал бы возможной команду, где названы и номер, и ключ, —
и кому-то пришлось бы решать, что она значит. Роутер при этом ОДИН (`pc_agent.lesson_word`):
второго рядом не заводим, и слово владельца по-прежнему начинается с «урок».

ЧТО ЭТО ЗАКРЫВАЕТ, ЧИСЛОМ. `lesson_store.withdraw(source=…)` живёт с 09.09.2026 (утренний заход)
и умеет пометить набор снятым — на этом всё: он не знает, КТО снимает и ЗАЧЕМ, не оставляет следа
и НЕ ВОЗВРАЩАЕТСЯ. Звал его вне тестов ноль файлов, а с телефона его не было вовсе: снять набор
владелец мог только с консоли, которой не пользуется. Пока этой двери нет, заливать экспорт
переписки НЕЛЬЗЯ — залить получится, а вынуть нечем.

ДВА ШАГА, И ОБЪЕКТ НАЗЫВАЕТСЯ ВО ВТОРОМ:
  1) `--show <ключ>` — что за набор: сколько строк, сколько уйдёт, когда залит, кем. ЧТЕНИЕ.
  2) `--off <ключ> --count N --why "…"` — снять. Подтверждение обязано нести КЛЮЧ И ЧИСЛО
     (решение Штаба, переносу не подлежит). Число сверяется с ЖИВОЙ таблицей: названо не то —
     отказ, и не тронуто ничего. Подтверждения без числа не бывает вовсе.

ТРИ ЗАМКА, каждый со своим отказом словами:
  • ПРАВО — `moderation_core.may_write_rule` (fail-closed: пустой список прав = НИКОМУ), то же
    самое, что судит запись урока, перевод кандидата и «отмени урок N». Судит его
    `trainer.withdraw_batch`, а не этот файл: второй гейт разошёлся бы с первым молча.
  • ПРИЧИНА — пустая «почему» ОТКАЗЫВАЕТ, ровно как у перевода. Ниоткуда не выводится.
  • СЛЕД И ВОЗВРАТ — снятие пишет строку следа на КАЖДЫЙ урок (автор, время, ключ, число, прежние
    БАЙТЫ состояния), и `--back <ключ>` возвращает файл к тому, что было, побайтно.

ЧЕМ ЭТО НЕ ЯВЛЯЕТСЯ: `--back` — не «включить набор». Он возвращает РОВНО ТО состояние, которое
у каждой строки было до снятия: действующая станет действующей, кандидат — кандидатом, а снятая
раньше другим разрезом останется снятой тем разрезом.

ЗАПУСК (боевая таблица — по умолчанию; `--path` уводит на свою копию):
    venv/Scripts/python.exe lesson_batch.py --census
    venv/Scripts/python.exe lesson_batch.py --show экспорт_переписки
    venv/Scripts/python.exe lesson_batch.py --who filipp --off экспорт_переписки --count 12 \
        --why "залили не тот файл"
    venv/Scripts/python.exe lesson_batch.py --who filipp --back экспорт_переписки
    venv/Scripts/python.exe lesson_batch.py --trace

Код возврата: 0 — сделано, 1 — отказано (внятными словами в stdout), 2 — разбор командной
строки. Отказ печатается в stdout, а не в stderr: эту строку читает ЧЕЛОВЕК (её пересылает в
Telegram `pc_agent`), а не разборщик логов.
"""

import argparse
import sys

import io_utf8

EXIT_OK = 0
EXIT_REFUSED = 1


def _census_card(path=None):
    """Все наборы таблицы числами → текст для человека. ТОЛЬКО ЧТЕНИЕ."""
    import lesson_store
    store = lesson_store.load(path)
    if not store.exists:
        return "База уроков ещё не заведена — наборов нет."
    census = lesson_store.sources_census(store.lessons)
    if len(census) == 0:
        return "В базе уроков нет ни одной строки — наборов нет."
    out = ["Наборы в базе уроков (сколько строк):"]
    for name, count in census:
        out.append("  %-20s — %d" % (name, count))
    # СТРОКИ СТАРШЕ ФОРМАТА НАЗВАНЫ ОТДЕЛЬНО И НЕ ОБЕЩАНЫ К СНЯТИЮ: «источник неизвестен» —
    # не набор, а возраст, и разреза снятия у него нет ни одной веткой (`by_source`).
    out.append("— снять целиком можно только НАЗВАННЫЙ набор (%s); «%s» набором не является."
               % (", ".join(lesson_store.SOURCES), lesson_store.SAY_UNKNOWN))
    return "\n".join(out)


def _trace_card(path=None):
    """След снятий и возвратов НАБОРОВ → текст для человека. ТОЛЬКО ЧТЕНИЕ."""
    import lesson_store
    seen = lesson_store.load_batch_trace(path)
    if len(seen) == 0:
        return "След наборов пуст: ни одного набора ещё не снимали."
    out = ["След наборов (кто · когда · ключ · номер · что стало):"]
    for t in seen:
        out.append("  %s  движение %-4d %-14s %-18s #%-4d @%s  «%s» → «%s»"
                   % (t.stamp, t.move, t.act, t.source, t.number, t.who, t.prev_state,
                      t.new_state))
    out.append("— всего строк следа: %d (движение = все строки с одним НОМЕРОМ движения; "
               "штамп секундный и уникальным не бывает)" % len(seen))
    return "\n".join(out)


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(
        description="дверь снятия НАБОРА уроков целиком (объект + право + причина + возврат)")
    ap.add_argument("--who", default="", help="кто делает движение (имя в Telegram, без @)")
    ap.add_argument("--show", metavar="КЛЮЧ", help="шаг 1: показать набор (чтение)")
    ap.add_argument("--off", metavar="КЛЮЧ", help="шаг 2: снять набор целиком")
    ap.add_argument("--count", metavar="N",
                    help="сколько строк уйдёт — ЧАСТЬ ПОДТВЕРЖДЕНИЯ, обязательна при --off")
    ap.add_argument("--why", default="", help="причина снятия СЛОВАМИ (обязательна при --off)")
    ap.add_argument("--back", metavar="КЛЮЧ", help="вернуть набор, снятый последним движением")
    # ПАРТИЯ — СУЖЕНИЕ КЛЮЧА, а не второй ключ: без неё всё работает ровно как вчера (весь
    # набор), с ней уезжает одна поставка. Значение «-» — строки набора БЕЗ номера заливки,
    # то есть легшие до 20.09.
    ap.add_argument("--batch", metavar="N",
                    help="номер ЗАЛИВКИ внутри набора: снять/показать/вернуть РОВНО её "
                         "(«-» — строки без номера). Не названа — весь набор, как прежде")
    ap.add_argument("--census", action="store_true", help="перечислить наборы числами (чтение)")
    ap.add_argument("--trace", action="store_true", help="показать след наборов (чтение)")
    ap.add_argument("--path", help="другой файл таблицы (по умолчанию боевой lesson_store.tsv)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.census:
        print(_census_card(args.path))
        return EXIT_OK
    if args.trace:
        print(_trace_card(args.path))
        return EXIT_OK

    import trainer
    if args.show is not None:
        dec = trainer.batch_card(args.show, path=args.path, batch=args.batch)
        print(dec["card"])
        return EXIT_OK if dec["status"] in ("shown", "empty") else EXIT_REFUSED

    named = [x for x in (args.off, args.back) if x is not None]
    if len(named) != 1:
        # РОВНО ОДНО ДВИЖЕНИЕ ЗА ВЫЗОВ, тем же доводом, что у двери перевода: «снять и тут же
        # вернуть» одной командой — это описка, а не решение человека, и исполнять её молча
        # дороже, чем отказать.
        print("Назови РОВНО одно движение: --off КЛЮЧ (с --count N и --why) либо --back КЛЮЧ. "
              "Посмотреть, что есть: --census / --show КЛЮЧ / --trace.")
        return EXIT_REFUSED

    if args.off is not None:
        # `--count` НЕ приводится к числу ЗДЕСЬ. Разбор «12 строк» или «двенадцать» — это уже
        # решение о том, что владелец имел в виду, и принимает его ОДНО место (`batch_withdraw`,
        # `_leading_number`): второе разошлось бы с первым молча. Дверь передаёт названное как
        # есть, включая «не названо» (`None`).
        dec = trainer.withdraw_batch(args.off, count=args.count, why=args.why, who=args.who,
                                     path=args.path, batch=args.batch)
    else:
        dec = trainer.restore_batch(args.back, who=args.who, path=args.path, batch=args.batch)
    print(dec["card"])
    return EXIT_OK if dec["status"] in ("batch_withdrawn", "batch_restored") else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
