"""Руки ревью-контура: собрать пакет второго мнения и положить его в лоток.

Разделение то же, что у `expectations_pc.py` / `expectations_pc_run.py`:
вся логика — в чистом :mod:`review_pack`, здесь только ввод-вывод. Весь
побочный эффект этого файла — ОДНА запись файла в `docs/review_outbox/`
(и, по явному ключу `--journal`, одна строка-индекс через штатного писателя
журнала). Наружу ничего не отправляется: ступень 1 — только сборка.

    venv/Scripts/python.exe review_pack_build.py --case docs/review_cases/<файл>.json
    venv/Scripts/python.exe review_pack_build.py --case … --journal

Коды возврата: 0 — пакет собран; 3 — пакет blocked (файл ВСЁ РАВНО записан:
отказ с причиной сам по себе доказательство); 2 — спецификация случая
недействительна, файла нет.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import review_pack

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTBOX = "docs/review_outbox"
JOURNAL_WRITER = "cowork_log_append.py"


def load_case(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def live_frame_version(reader=None, test_context=None):
    """Версия канона рамки, прочитанная ЖИВЬЁМ. → (версия|None, примечание|None).

    Единственное место полосы, где сборщик пакета узнаёт редакцию правил, и оно
    НАМЕРЕННО здесь, в руках: чистое ядро `review_pack` сети не касается.

    Источник — тот же узел, что читают исполнители (`KB_shtab_frame`), через
    того же доверенного писателя; секретов этот файл не видит. Копии версии тут
    нет ни одной: отказ моста даёт ``None``, а не «последнюю известную» —
    подставить вчерашний номер значило бы соврать ревьюеру о редакции правил, и
    соврать МОЛЧА, то есть ровно тем способом, против которого правило написано.

    Под гейтом и юнитами живой узел не трогаем вовсе (тот же замок, что у
    `brain_writer` на записи): тест обязан получать «неизвестно», а не ходить в
    мост за настоящим номером.
    """
    import log_setup

    if (test_context if test_context is not None else log_setup.is_test_context()) and reader is None:
        return None, "тестовый контекст: живой узел не читаем"
    try:
        if reader is None:
            import brain_writer

            reader = brain_writer.read_text
        text = reader(name=review_pack.FRAME_DOC_NAME)
    except Exception as exc:                       # noqa: BLE001 — исход один: версии нет
        return None, "%s: %s" % (type(exc).__name__, str(exc).replace("\n", " ")[:160])
    version = review_pack.parse_frame_version(text)
    if not version:
        return None, "узел прочитан (%d знаков), номер редакции в шапке не найден" % len(text or "")
    return version, None


def journal_argv(repo=HERE, python=None):
    """argv штатного писателя журнала. Строка идёт СТДИНОМ, а не аргументом.

    Кириллица в argv коверкается на этой полосе (известный класс), а у
    писателя весь argv и так считается текстом записи — сентинел «-»
    переключает его на stdin."""
    return [python or sys.executable, os.path.join(repo, JOURNAL_WRITER), "-"]


def write_pack(pack, text, outbox_dir):
    os.makedirs(outbox_dir, exist_ok=True)
    path = os.path.join(outbox_dir, review_pack.pack_filename(pack))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Собрать пакет второго мнения (без отправки).")
    parser.add_argument("--case", required=True, help="JSON со спецификацией случая")
    parser.add_argument("--root", default=HERE, help="корень, внутри которого лежат источники")
    parser.add_argument("--outbox", default=None, help="каталог лотка (по умолчанию docs/review_outbox)")
    parser.add_argument("--max-chars", type=int, default=review_pack.REVIEW_MAX_CHARS)
    parser.add_argument("--print", dest="print_text", action="store_true", help="выдать текст пакета в stdout")
    parser.add_argument("--json", dest="print_json", action="store_true", help="выдать словарь пакета в stdout")
    parser.add_argument("--journal", action="store_true", help="дописать строку-индекс в журнал")
    args = parser.parse_args(argv)

    outbox_dir = args.outbox or os.path.join(args.root, *DEFAULT_OUTBOX.split("/"))

    frame_version, frame_note = live_frame_version()

    try:
        case = load_case(args.case)
        pack = review_pack.build_review_pack(case, root=args.root, max_chars=args.max_chars,
                                             frame_version=frame_version, frame_version_note=frame_note)
    except review_pack.ReviewPackError as exc:
        sys.stderr.write("СПЕЦИФИКАЦИЯ НЕДЕЙСТВИТЕЛЬНА: %s\n" % exc)
        return 2

    text = review_pack.render_review_pack(pack)
    path = write_pack(pack, text, outbox_dir)
    rel = os.path.relpath(path, args.root).replace(os.sep, "/")
    line = review_pack.index_line(pack, rel)

    if args.print_json:
        sys.stdout.write(json.dumps(pack, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if args.print_text:
        sys.stdout.write(text)

    sys.stdout.write("ВЕРСИЯ КАНОНА: %s%s\n"
                     % (frame_version or review_pack.FRAME_VERSION_UNKNOWN,
                        " (%s)" % frame_note if frame_note else ""))
    sys.stdout.write("ПАКЕТ: %s (%s, %d знаков)\n" % (rel, pack["status"], pack["text_chars"]))
    sys.stdout.write("ИНДЕКС: %s\n" % line)

    if args.journal:
        done = subprocess.run(
            journal_argv(repo=HERE),
            input=line.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        sys.stdout.write("ЖУРНАЛ: код %d %s\n" % (done.returncode, done.stdout.decode("utf-8", "replace").strip()))
        if done.returncode != 0:
            sys.stderr.write(done.stderr.decode("utf-8", "replace"))

    return 0 if pack["status"] == "ok" else 3


if __name__ == "__main__":
    sys.exit(main())
