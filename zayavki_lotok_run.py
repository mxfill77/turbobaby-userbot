# -*- coding: utf-8 -*-
"""Руки лотка информационных заявок: диск и только диск.

Разделение то же, что у всех ступеней полосы: всё, что РЕШАЕТ, живёт чистым
:mod:`zayavki_lotok_pc`; здесь ввод-вывод — каталог, файлы, чтение.

    venv/Scripts/python.exe zayavki_lotok_run.py --status     # что лежит в лотке; ничего не пишем

ЧИТАЕТСЯ ТЕМ ЖЕ, ЧЕМ ЛОТОК РЕВЬЮ, И ЭТО НЕ КРАСИВОЕ СЛОВО, А ГРАНИЦА ЗАВИСИМОСТЕЙ:
``os.listdir`` плюс чтение файла — ни моста, ни базы, ни сети, ни демона. Отсюда
право витрины звать этот модуль напрямую: он не тянет за собой куста импортов
(`review_intake_run` тянет демона, и витрине он не нужен).

ТРЕТИЙ ИСХОД ЕСТЬ У КАЖДОГО ЧТЕНИЯ. Каталога нет или он не читается → ``ok=False``
и ПРИЧИНА СЛОВОМ, а не пустой список: «в лотке 0» и «лоток не прочитан» — разные
новости, и путать их запрещено заданием прямо.

НИЧЕГО НЕ УДАЛЯЕТ НИ ОДНОЙ ВЕТКОЙ. Файл лотка не переписывается и не стирается: уже
лежащий файл того же имени — это ИДЕМПОТЕНТНОСТЬ (та же заявка), и запись честно
отвечает «уже лежит», а не затирает чужое.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

import zayavki_lotok_pc as lotok

HERE = os.path.dirname(os.path.abspath(__file__))


def enabled(env=None):
    """Ступень включена? → bool. Рубильник спрашивают РУКИ: чистый слой в среду не смотрит."""
    got = (env if env is not None else os.environ).get(lotok.OFF_FLAG) or ""
    return str(got).strip().lower() not in ("1", "true", "yes", "on")


def lotok_dir(root=HERE):
    """Каталог лотка. → путь."""
    return os.path.join(root, *lotok.LOTOK_DIR.split("/"))


def put(text, verdict, stamp, day, root=HERE):
    """Заявка → ФАЙЛ в лотке (атомарно). → (ok, rel|None, почему).

    Ключ файла берётся из ``verdict['key']``, если зовущий его знает, иначе из
    времени: имя обязано быть устойчивым, чтобы повтор той же заявки не размножил
    файлов. Уже лежащий файл НЕ ПЕРЕПИСЫВАЕТСЯ — отвечаем ``ok=True`` и говорим,
    что он уже там.
    """
    d = verdict if isinstance(verdict, dict) else {}
    name = lotok.file_name(day, d.get("key") or d.get("id") or stamp)
    path = os.path.join(lotok_dir(root), name)
    rel = "%s/%s" % (lotok.LOTOK_DIR, name)
    try:
        os.makedirs(lotok_dir(root), exist_ok=True)
        if os.path.exists(path):
            return True, rel, "файл лотка уже лежит — повтора не делаем"
        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(lotok.file_text(text, d, stamp, day=day))
        os.replace(tmp, path)
        return True, rel, ""
    except Exception as exc:                       # noqa: BLE001 — незнание называется словом
        return False, None, "лоток не принял файл (%s: %s)" % (type(exc).__name__, exc)


def rows(root=HERE):
    """Все файлы лотка → записи. → (list[dict], ok, почему).

    ``ok=False`` — каталог не прочитан. ОТДЕЛЬНЫЙ признак, а не длина списка:
    пустой лоток и нечитаемый лоток дают один и тот же пустой список, и различать
    их обязано поле, а не догадка зовущего.

    Файл, который не разобрался, НЕ роняет чтение целиком: он пропускается со
    своей причиной. Один битый файл не смеет отнять счёт у остальных — тот же
    приём, что у шапок лотка ревью.
    """
    base = lotok_dir(root)
    try:
        names = sorted(n for n in os.listdir(base) if n.endswith(".md"))
    except FileNotFoundError:
        # ТРЕТИЙ ИСХОД РАЗДВОИЛСЯ, И РАЗНИЦА ТУТ НЕ СТИЛИСТИЧЕСКАЯ. «Каталога ЕЩЁ
        # НЕТ» — это честный пустой лоток: его создаёт первая же запись (`put`), и
        # до неё класть было нечего. Назвать это незнанием значило бы запереть обе
        # ступени намертво: ставить нельзя, потому что «не знаю», а «не знаю» не
        # кончится, потому что ставить нельзя. Причина при этом НЕ МОЛЧИТ — она
        # едет наружу словом и печатается витриной рядом с числом.
        return [], True, "лотка ещё нет по адресу %s — в него не клали ничего" % lotok.LOTOK_DIR
    except Exception as exc:                       # noqa: BLE001
        # А ВОТ ЭТО — НАСТОЯЩЕЕ НЕЗНАНИЕ: каталог есть, но не читается (права, не
        # каталог вовсе, диск). Здесь ноль был бы враньём, и `ok=False` его запрещает.
        return [], False, "лоток не прочитан (%s)" % type(exc).__name__
    out = []
    for name in names:
        try:
            with io.open(os.path.join(base, name), encoding="utf-8") as fh:
                got = lotok.parse_file(fh.read())
        except Exception:                          # noqa: BLE001 — битый файл, а не битый лоток
            continue
        got["rel"] = "%s/%s" % (lotok.LOTOK_DIR, name)
        out.append(got)
    return out, True, ""


def marker_rows(root=HERE):
    """Лоток → ряды В ВИДЕ ОЧЕРЕДИ для чужих счётчиков маркеров. → (list, ok, почему).

    Зачем вообще: дедуп и СУТОЧНЫЙ ПОТОЛОК обеих ступеней считаются по ЖИВОЙ
    ОЧЕРЕДИ. Заявка из очереди ушла — счёт обязан получить её отсюда, иначе
    потолок обнулится молча и полоса начнёт ставить заявки без конца.
    """
    got, ok, why = rows(root)
    return lotok.queue_like(got), ok, why


def main(argv=None):
    ap = argparse.ArgumentParser(description="лоток информационных заявок: что в нём лежит")
    ap.add_argument("--status", action="store_true", help="показать записи лотка (ничего не пишем)")
    ap.add_argument("--day", default="", help="показать записи ТОЛЬКО за этот день (ГГГГ-ММ-ДД)")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args(argv)
    got, ok, why = rows(HERE)
    if args.day:
        got = lotok.day_rows(got, args.day)
    if args.json:
        print(json.dumps({"ok": ok, "why": why, "rows": got}, ensure_ascii=False, indent=1))
        return 0
    for line in lotok.vitrina_lines(got, ok=ok, why=why, limit=50):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
