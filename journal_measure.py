# -*- coding: utf-8 -*-
"""journal_measure.py — ЗАМЕР длины записей журнала: чем обоснован порог `cowork_log_append.LINE_MAX`.

Запуск (только чтение, ничего не пишет в мозг):
    venv/Scripts/python.exe journal_measure.py            # по снимкам в tmp/ (быстро)
    venv/Scripts/python.exe journal_measure.py --refresh  # снять свежие снимки через Bridge

Секретов НЕ касается: чтение идёт через доверенного писателя (`brain_writer.read_text`), он сам
берёт конфиг моста — запрет класса 328 соблюдён, скрипт токенов не видит.

ЕДИНИЦА ЗАМЕРА — логическая ЗАПИСЬ, склеенная в ОДНУ строку ровно так, как её кладёт
`cowork_log_append.main()` (`" ".join(msg.split())`). Только это и есть «длина строки журнала».

ПОЧЕМУ РАЗБОР МЯГКИЙ (по типу в начале строки), а не строгий (по контракту «тип + дата»):
контракт введён 28.07.2026, до него записи были МНОГОСТРОЧНЫМИ и без даты. Строгий сплиттер
склеивает всё до-контрактное в одну псевдозапись на 395 593 символа и даёт по архиву p90=13 585,
p95=33 576 — все эти числа НЕВЕРНЫ. На них легко купиться и «обосновать» порог мусором.
"""
import os
import re
import sys
import datetime

import io_utf8

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP_DIR = os.path.join(HERE, "tmp")            # tmp/ под .gitignore: 886 тыс. символов журнала
DOCS = ("cowork_log", "cowork_log_archive")     # живой журнал + ротационный архив = полная неделя
LOG_TYPES = ("DONE", "NOTE", "ASK", "PLAN", "BLOCKED", "WAITING", "SKIPPED")
_ALT = "|".join(LOG_TYPES)
_LOOSE = re.compile(r"^(%s)\b" % _ALT)
_STAMP = re.compile(r"^(?:%s)\s+(\d{4})-(\d{2})-(\d{2})\s+\d{2}:\d{2}\s+UTC\b" % _ALT)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DDMM = re.compile(r"\b(\d{2})\.(\d{2})(?:\.(\d{4}))?\b")
# Окно жизни проекта. Без этого забора «ДД.ММ» тащит в выборку номера версий и времена,
# случайно похожие на дату («2.1.197», «16.49»), и «неделя» считается по мусору — проверено.
_MIN_D, _MAX_D = datetime.date(2026, 5, 1), datetime.date(2026, 8, 31)
# Сколько символов остаётся в журнале от переполненной записи («итог + путь»). Число не из головы:
# это длина реальной строки-указателя того же вида, что уже пишут ARTIFACT-записи (их p90 = 218).
TAIL = 220


def snapshot(doc, refresh=False):
    """Текст дока: из снимка в tmp/ либо свежим чтением через brain_writer (read-only)."""
    path = os.path.join(SNAP_DIR, "%s.snapshot.txt" % doc)
    if not refresh and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    import brain_writer
    text = brain_writer.read_text(name=doc)
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    sys.stderr.write("снимок %s: %d символов\n" % (doc, len(text)))
    return text


def _ok(y, m, d):
    try:
        dt = datetime.date(y, m, d)
    except ValueError:
        return None
    return dt if _MIN_D <= dt <= _MAX_D else None


def entry_date(text):
    """Дата записи: штамп контракта → ISO-дата в голове → «ДД.ММ». Не нашлась → None."""
    head = text[:160]
    m = _STAMP.match(head)
    if m:
        return _ok(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _ISO.search(head)
    if m:
        got = _ok(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if got:
            return got
    for m in _DDMM.finditer(head):
        got = _ok(int(m.group(3) or _MIN_D.year), int(m.group(2)), int(m.group(1)))
        if got:
            return got
    return None


def entries(docs=DOCS, refresh=False):
    """→ [(док, дата|None, запись одной строкой)]."""
    out = []
    for doc in docs:
        cur = None
        for raw in snapshot(doc, refresh).split("\n"):
            s = raw.strip()
            if not s:
                continue
            if _LOOSE.match(s):
                if cur is not None:
                    out.append((doc, entry_date(cur), " ".join(cur.split())))
                cur = s
            elif cur is not None:
                cur += " " + s
        if cur is not None:
            out.append((doc, entry_date(cur), " ".join(cur.split())))
    return out


def quant(vals, p):
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))] if v else 0


def table(title, rows):
    lens = [len(t) for _, _, t in rows]
    if not lens:
        print("\n%s: записей 0" % title)
        return
    tot = sum(lens)
    print("\n%s: записей %d, символов %d" % (title, len(rows), tot))
    print("  p25=%d p50=%d p75=%d p90=%d p95=%d max=%d среднее=%d"
          % (quant(lens, 25), quant(lens, 50), quant(lens, 75), quant(lens, 90),
             quant(lens, 95), max(lens), tot // len(lens)))
    print("  порог | выше порога (шт / %) | символов было → стало | экономия")
    for thr in (300, 400, 500, 600, 700, 800, 1000, 1200, 1500, 2000, 3000):
        over = [x for x in lens if x > thr]
        now = sum(min(x, TAIL) if x > thr else x for x in lens)
        print("  %5d | %4d / %5.1f%% | %8d → %-8d | −%-7d (−%4.1f%%)"
              % (thr, len(over), 100.0 * len(over) / len(lens), tot, now, tot - now,
                 100.0 * (tot - now) / max(1, tot)))


def main(argv=None):
    io_utf8.force_utf8()
    argv = sys.argv[1:] if argv is None else argv
    all_e = entries(refresh="--refresh" in argv)
    dated = [e for e in all_e if e[1]]
    if not dated:
        print("ни одной записи с распознанной датой — нечего мерить")
        return 1
    last = max(e[1] for e in dated)
    week = [e for e in dated if e[1] > last - datetime.timedelta(days=7)]
    print("ЗАПИСЕЙ ВСЕГО: %d (с датой %d, диапазон %s..%s)"
          % (len(all_e), len(dated), min(e[1] for e in dated), last))
    table("A. ЖИВОЙ cowork_log (после контракта 28.07)", [e for e in all_e if e[0] == DOCS[0]])
    table("B. НЕДЕЛЯ %s..%s (живой + архив)" % (last - datetime.timedelta(days=7), last), week)
    table("C. ВЕСЬ ЖУРНАЛ (живой + архив)", all_e)
    # ОПОРА ПОРОГА: строки, которые формат «итог + путь» УЖЕ соблюдают. Порог обязан стоять ВЫШЕ
    # их максимума — иначе он режет образцовые записи и правило спорит само с собой.
    art = sorted(len(t) for _, _, t in all_e if re.match(r"^\S+.{0,60}?ARTIFACT", t))
    if art:
        print("\nОПОРА ПОРОГА — живые строки вида «итог + путь» (ARTIFACT): n=%d min=%d p50=%d "
              "p90=%d МАКСИМУМ=%d" % (len(art), art[0], quant(art, 50), quant(art, 90), art[-1]))
        try:
            import cowork_log_append as cla
            print("  LINE_MAX = %d → %s" % (cla.LINE_MAX, "выше максимума, образцовые записи не режет"
                                            if cla.LINE_MAX > art[-1] else "НИЖЕ максимума — порог режет образцовые записи!"))
        except Exception as e:
            print("  порог не прочитан: %s" % type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
