# -*- coding: utf-8 -*-
"""diag_rc_liveness_replay.py — РЕПЛЕЙ зомби-пробы супервизора RC по живому логу.

ЗАЧЕМ. Порог свежести лога для ветки канала менялся «на глаз» (общий 1200с достался ей по
умолчанию от серверной ветки). Здесь тот же вопрос решается ЧИСЛАМИ: берём боевой
`rc_remote_control.log`, восстанавливаем каждую ЖИЗНЬ каждой ветки (старт → гашение/выход) и
прогоняем предикат `rc_supervisor.channel_alive` на КАЖДОЙ жизни при старом и новом пороге.

ПОЧЕМУ РЕПЛЕЙ ТОЧЕН ДЛЯ ВЕТКИ КАНАЛА. Замер 30.07.2026 20:41–20:45 (pid 13464):
процесс стартовал 20:41:34.623, последняя запись в его --debug-file — 20:41:35.814 (всплеск
904 мс, 70 строк), дальше НИ БАЙТА: возраст лога 252.1с при возрасте процесса 253.3с.
Значит для этой ветки `log_age(t) == возраст процесса − 1.2с`, и возраст лога в момент гашения
равен ДЛИТЕЛЬНОСТИ ЖИЗНИ из журнала. Никакой реконструкции mtime не нужно.

ЧЕСТНАЯ ГРАНИЦА. Второе плечо предиката (established > 0) из журнала не восстановить. Но там,
где сторож ГАСИЛ, он уже доказал `est == 0` на 3 проверках подряд — а нас интересует ровно эта
точка. Поэтому реплей отвечает на вопрос «сработало бы гашение В ТОТ ЖЕ МОМЕНТ при новом
пороге», а не «доживёт ли канал до утра».

Запуск: venv/Scripts/python.exe diag_rc_liveness_replay.py
"""
import os
import re
import sys
import datetime
import collections

import rc_supervisor as S

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rc_remote_control.log")

# «2026-07-30 19:36:14,102 | [named-channel] старт pid=17504: C:\...»
_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) \| \[([\w-]+)\] (.+)$")
_START = re.compile(r"^старт pid=(\d+)")
_ZOMBIE = re.compile(r"^ЗОМБИ")
_EXIT = re.compile(r"^процесс вышел")

Life = collections.namedtuple("Life", "branch pid start end how")


def parse(path=LOG):
    """Журнал → список жизней ветвей. Строки до dual-mode (без тега [ветка]) игнорируем —
    у них не было ни веток, ни пробы живости, приплетать их к замеру нельзя."""
    open_ = {}
    lives, skipped = [], 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            m = _RE.match(ln.rstrip("\n"))
            if not m:
                skipped += 1
                continue
            ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") \
                + datetime.timedelta(milliseconds=int(m.group(2)))
            branch, msg = m.group(3), m.group(4)
            ms = _START.match(msg)
            if ms:
                open_[branch] = (int(ms.group(1)), ts)
                continue
            how = "зомби" if _ZOMBIE.match(msg) else ("выход" if _EXIT.match(msg) else None)
            if how and branch in open_:
                pid, st = open_.pop(branch)
                lives.append(Life(branch, pid, st, ts, how))
    return lives, skipped, open_


def replay(lives, threshold, strikes=None, interval=None):
    """Сколько жизней ветки канала были бы ГАШЕНЫ при пороге threshold.
    Гашение наступает, когда возраст лога держится выше порога strikes проверок подряд:
    age >= threshold + strikes*interval. Для ветки канала age == длительность жизни (см. шапку)."""
    strikes = S.LIVENESS_STRIKES if strikes is None else strikes
    interval = S.CHECK_INTERVAL if interval is None else interval
    need = threshold + strikes * interval
    return [l for l in lives if (l.end - l.start).total_seconds() >= need], need


def main():
    lives, skipped, still_open = parse()
    named = [l for l in lives if l.branch == "named-channel"]
    server = [l for l in lives if l.branch == "rc-server"]
    z_named = [l for l in named if l.how == "зомби"]
    z_server = [l for l in server if l.how == "зомби"]
    first, last = lives[0].start, lives[-1].end

    print("=== КОРПУС ===")
    print(f"журнал: {LOG}")
    print(f"окно: {first:%Y-%m-%d %H:%M:%S} .. {last:%Y-%m-%d %H:%M:%S} "
          f"({(last - first).total_seconds() / 86400:.2f} сут)")
    print(f"строк без тега ветки (эра до dual-mode) — не в замере: {skipped}")
    print(f"незакрытых жизней (живут сейчас): {dict((k, v[0]) for k, v in still_open.items())}")
    print()
    print("=== ГАШЕНИЯ ПО ВЕТКАМ ЗА ВСЁ ОКНО ===")
    for label, all_, z in (("named-channel", named, z_named), ("rc-server", server, z_server)):
        print(f"{label:14} жизней {len(all_):4}  из них ЗОМБИ {len(z):4}  "
              f"самовыход {len(all_) - len(z):3}")
    print()

    day = last - datetime.timedelta(days=1)
    z_named_day = [l for l in z_named if l.start >= day]
    z_server_day = [l for l in z_server if l.start >= day]
    print(f"=== ПОСЛЕДНИЕ СУТКИ ({day:%m-%d %H:%M} .. {last:%m-%d %H:%M}) ===")
    print(f"named-channel ЗОМБИ: {len(z_named_day)}      rc-server ЗОМБИ: {len(z_server_day)}")
    print()

    durs = sorted((l.end - l.start).total_seconds() for l in z_named)
    print("=== ДЛИТЕЛЬНОСТЬ ЖИЗНЕЙ КАНАЛА, ЗАВЕРШЁННЫХ ГАШЕНИЕМ (с) ===")
    print(f"n={len(durs)}  min={durs[0]:.0f}  медиана={durs[len(durs) // 2]:.0f}  max={durs[-1]:.0f}")
    hist = collections.Counter(int(d) for d in durs)
    print("топ-5 повторяющихся длительностей:",
          ", ".join(f"{v}с×{c}" for v, c in hist.most_common(5)))
    print()

    print("=== РЕПЛЕЙ ПРЕДИКАТА НА ВЕТКЕ КАНАЛА ===")
    print(f"страйки={S.LIVENESS_STRIKES}, интервал={S.CHECK_INTERVAL}с "
          f"⇒ гашение при возрасте лога >= порог + {S.LIVENESS_STRIKES * S.CHECK_INTERVAL}с")
    for label, thr in (("БЫЛО  (общий 1200с)", 1200), ("СТАЛО (ветка канала)", S.NAMED_LIVENESS_MAX_AGE)):
        fired, need = replay(named, thr)
        fired_day = [l for l in fired if l.start >= day]
        print(f"{label:22} порог={thr:6}с  рубеж={need:6.0f}с  "
              f"гашений за окно: {len(fired):4}/{len(named)}   за сутки: {len(fired_day)}")
    print()

    print("=== НАСТОЯЩИЙ МЁРТВЫЙ КАНАЛ ПО-ПРЕЖНЕМУ ГАСИТСЯ ===")
    spec = S.NAMED_SPEC
    thr = spec["max_age"]
    cases = (
        ("труп: молчит порог+90с, соединений 0", thr + 90, 0, False),
        ("труп: молчит сутки, соединений 0", 86400, 0, False),
        ("труп: лога нет вообще, соединений 0", None, 0, False),
        ("здоровый: молчит 21,5 мин (старый ложняк), 0 conns", 1290, 0, True),
        ("здоровый: молчит 9ч14м (сон ПК), 0 conns", 33240, 0, True),
        ("здоровый: молчит сутки, НО есть соединение", 86400, 1, True),
    )
    ok = True
    for name, age, est, want_alive in cases:
        got = S.channel_alive(age, est, max_age=thr)
        mark = "OK " if got == want_alive else "ПРОВАЛ"
        ok = ok and got == want_alive
        print(f"{mark} {name:52} age={str(age):>6} est={est} → "
              f"{'ЖИВ' if got else 'МЁРТВ'} (ожидание: {'ЖИВ' if want_alive else 'МЁРТВ'})")
    print()
    print("ИТОГ:", "все ожидания совпали" if ok else "ЕСТЬ РАСХОЖДЕНИЯ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
