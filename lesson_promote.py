# -*- coding: utf-8 -*-
"""
lesson_promote.py — ДВЕРЬ, которой урок переводится из кандидата в действующий (09.09.2026).

ЗАЧЕМ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ЕЩЁ ОДИН КЛЮЧ У `lesson_store.py`. Хранилище объявлено ЧИТАЮЩИМ
снаружи («хранилище уроков тренажёра (чтение)» — его собственный CLI), и права оно не судит
принципиально: имён и списков не знает и знать не должно. Дверь обязана делать ровно обратное —
спросить ПРАВО, спросить ПРИЧИНУ и назвать человеку исход словами. Смешать это в одном CLI
значило бы поставить рядом ключ, который пишет, и ключ, который не судит прав.

ЧТО ЭТО ЗАКРЫВАЕТ, ЧИСЛОМ. `lesson_store.promote` живёт с 05.09.2026, и до 09.09 его не звал ни
один файл дерева верхнего уровня, кроме тестов (совпадений вне `test_lesson_*` — ноль). Кнопка
«🎓 Обучить» писала КАНДИДАТА, кандидата не читает `active()`, а перевести его в действующие было
НЕЧЕМ: вердикт владельца не становился правилом ни при каком его старании. Дверь — здесь.

ТРИ ЗАМКА, каждый со своим отказом словами:
  • ПРАВО — `moderation_core.may_write_rule` (fail-closed: пустой список прав = НИКОМУ), тот же
    механизм, что судит запись урока и «отмени урок N». Судит его `trainer.promote_lesson`, а
    не этот файл: второй гейт разошёлся бы с первым молча.
  • ПРИЧИНА — пустая «почему» ОТКАЗЫВАЕТ. Ни из текста урока, ни из вопроса клиента причина не
    выводится: подставленная причина отвечает на «что написано», а не на «почему так правильно».
  • СЛЕД И ОТКАТ — перевод дописывает строку следа (автор, время, номер, прежние байты полей),
    и `--rollback N` возвращает РОВНО то состояние, которое было до перевода.

ЧЕМ ЭТО НЕ ЯВЛЯЕТСЯ: `--rollback` — не «снятие». Снятие («отмени урок N», `withdraw`) кладёт
ТРЕТЬЕ, новое состояние `снят(...)`, которого до перевода не было; откат возвращает прежнее.

ЗАПУСК (боевая таблица — по умолчанию; `--path` уводит на свою копию):
    venv/Scripts/python.exe lesson_promote.py --candidates
    venv/Scripts/python.exe lesson_promote.py --who filipp --promote 7 --why "причина словами"
    venv/Scripts/python.exe lesson_promote.py --who filipp --rollback 7
    venv/Scripts/python.exe lesson_promote.py --trace

Код возврата: 0 — сделано, 1 — отказано (внятными словами в stdout), 2 — разбор командной
строки. Отказ печатается в stdout, а не в stderr: эту строку читает ЧЕЛОВЕК (её пересылает в
Telegram `pc_agent`), а не разборщик логов.
"""

import argparse
import sys

import io_utf8

EXIT_OK = 0
EXIT_REFUSED = 1


def _candidates_card(path=None):
    """Кандидаты, ждущие решения → текст для человека. ТОЛЬКО ЧТЕНИЕ."""
    import lesson_store
    store = lesson_store.load(path)
    if not store.exists:
        return "База уроков ещё не заведена — кандидатов нет."
    rows = lesson_store.candidates(store.lessons)
    if len(rows) == 0:
        return "Кандидатов нет: все уроки базы либо действуют, либо сняты."
    out = ["Кандидаты (ждут причину и решение):"]
    for les in rows:
        text = " ".join(str(les.correct or "").split())
        if len(text) > 100:
            text = text[:99] + "…"
        why = " ".join(str(les.why or "").split()) or "— причина не названа"
        out.append("  #%d от @%s (%s): %s\n      почему: %s"
                   % (les.number, les.who, les.when, text, why))
    # ОБРАЗЕЦ НЕ ПИШЕТСЯ ЗДЕСЬ РУКАМИ (22.09.2026, 70i): его собирает и сверяет собственным
    # разбором `lesson_word_forms` — то же место, где живёт форма слова. Прежде строка была
    # написана по памяти, и правка глагола в разборе оставила бы карточку диктовать мёртвую форму.
    import lesson_word_forms                                # noqa: PLC0415 — дверь-CLI, импорт по месту
    out.append("— всего кандидатов: %d. Включить: «%s»."
               % (len(rows), lesson_word_forms.promote_phrase()))
    return "\n".join(out)


def _trace_card(path=None):
    """След переводов и откатов → текст для человека. ТОЛЬКО ЧТЕНИЕ."""
    import lesson_store
    seen = lesson_store.load_trace(path)
    if len(seen) == 0:
        return "След переводов пуст: ни одного урока в действующие ещё не переводили."
    out = ["След переводов (кто · когда · номер · что стало):"]
    for t in seen:
        out.append("  %s  %-7s #%-4d @%s" % (t.stamp, t.act, t.number, t.who))
    out.append("— всего движений: %d" % len(seen))
    return "\n".join(out)


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(
        description="дверь перевода кандидата в действующий урок (право + причина + откат)")
    ap.add_argument("--who", default="", help="кто делает движение (имя в Telegram, без @)")
    ap.add_argument("--promote", type=int, metavar="N", help="перевести кандидата #N в действующие")
    ap.add_argument("--why", default="", help="причина перевода СЛОВАМИ (обязательна)")
    ap.add_argument("--rollback", type=int, metavar="N", help="откатить перевод урока #N")
    ap.add_argument("--candidates", action="store_true", help="перечислить кандидатов (чтение)")
    ap.add_argument("--trace", action="store_true", help="показать след переводов (чтение)")
    ap.add_argument("--path", help="другой файл таблицы (по умолчанию боевой lesson_store.tsv)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.candidates:
        print(_candidates_card(args.path))
        return EXIT_OK
    if args.trace:
        print(_trace_card(args.path))
        return EXIT_OK

    named = [x for x in (args.promote, args.rollback) if x is not None]
    if len(named) != 1:
        # РОВНО ОДНО ДВИЖЕНИЕ ЗА ВЫЗОВ. «Перевести и тут же откатить» одной командой — это не
        # решение человека, а описка; исполнять её молча дороже, чем отказать.
        print("Назови РОВНО одно движение: --promote N (с --why) либо --rollback N. "
              "Посмотреть, что есть: --candidates / --trace.")
        return EXIT_REFUSED

    import trainer
    if args.promote is not None:
        dec = trainer.promote_lesson(args.promote, why=args.why, who=args.who, path=args.path)
    else:
        dec = trainer.rollback_promotion(args.rollback, who=args.who, path=args.path)
    print(dec["card"])
    return EXIT_OK if dec["status"] in ("promoted", "rolled_back") else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
