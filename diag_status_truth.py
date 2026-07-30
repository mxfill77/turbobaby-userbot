# -*- coding: utf-8 -*-
"""
diag_status_truth.py — ЗАМЕР класса «статус врёт»: сколько задач помечены НЕУДАЧЕЙ, но по факту
дали коммит или запись в журнал. Это число и есть цена класса.

Живой повод (30.07.2026): задача 54 выполнила работу и получила failed по таймауту подтверждения,
задача 61 выполнила работу, закоммитила a3f75dd, записала журнал — и получила failed по
сердцебиению. По этим статусам планируется следующий шаг, то есть план дважды строился на неверной
строке.

Источники — только чтение:
  • журнал cowork_log (срез снимается через brain_writer: секреты берёт САМ доверенный писатель,
    здесь их нет и в помине — запрет класса 328);
  • pc_orchestrator.log — CLAIM/терминальные строки. Время лога ЛОКАЛЬНОЕ (UTC+7): сверено по
    паре «13:33:47 локально» ↔ «end=2026-07-30T06:33:47+00:00» в той же строке METRICS;
  • git log — коммиты окна.

Окно задачи = [момент CLAIM, момент строки failed]. Коммит или запись ВНУТРИ окна = работа была.
Отдельной секцией — задачи, которые ПК клеймила, но терминала своей полосы так и не поставила:
их статус пришёл с ЧУЖОЙ полосы, и на ПК его не видно вовсе (ровно случай задачи 61).

Запуск:
    venv\\Scripts\\python.exe diag_status_truth.py --fetch      # снять свежий срез журнала и мерить
    venv\\Scripts\\python.exe diag_status_truth.py              # мерить по уже снятому срезу
"""

import io
import os
import re
import sys
import json          # noqa: F401 — используется в тестах реестра/срезов
import datetime
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = datetime.timezone.utc
PC_TZ = datetime.timezone(datetime.timedelta(hours=7))     # локальное время этого ПК
SLICE_DEFAULT = os.path.join(HERE, "tmp", "journal_slice.txt")
LOG_DEFAULT = os.path.join(HERE, "pc_orchestrator.log")
WINDOW_HOURS = 48.0
ORPHAN_WINDOW_SEC = 5400        # = PC_SINGLE_STALE: дальше окна одного прогона быть не может

TYPES = ("DONE", "NOTE", "ASK", "PLAN", "BLOCKED", "WAITING", "SKIPPED")
_J_HEAD = re.compile(r"^(%s)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+UTC:\s*(.*)$" % "|".join(TYPES))
_J_CLAIM = re.compile(r"^Orchestrator:\s*взял задачу #(\d+)")
# ЯКОРНО от начала строки: «задача #N [(пометка)] → failed …». Свободный подстрочный поиск сюда
# нельзя — result провалившейся задачи часто ЦИТИРУЕТ чужие строки лога («id=5 … → failed»), и
# первый прогон замера записал в провалы задачу ИЗ ЦИТАТЫ (ложный #5). Числа — факт: инструмент,
# который тихо считает не то, врёт ровно так же, как статус, который мы чиним.
_J_FAIL = re.compile(r"^Orchestrator:\s*(?:задача|смоук-шаг)\s*#(\d+)\s*(?:\([^)]*\))?\s*→\s*failed\b(.*)$")
_J_TERM = re.compile(r"^Orchestrator:\s*(?:задача|смоук-шаг)\s*#(\d+)\s*(?:\([^)]*\))?\s*→\s*\w+")
_J_ORCH = re.compile(r"^Orchestrator:")

_L_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+\w+\s+(.*)$")
_L_CLAIM = re.compile(r"^CLAIM id=(\d+)")
_L_FAIL = re.compile(r"^(?:NEEDS_APPROVAL|APPROVED|COMPLETE|COMMAND|SMOKE|stuck-single:)\s+id=(\d+)\b.*?failed")
_L_TERM = re.compile(r"^(?:COMPLETE|NEEDS_APPROVAL|APPROVED|COMMAND|SMOKE|stuck-single:)\s+id=(\d+)\b")


def journal_ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def fetch_slice(days=WINDOW_HOURS / 24.0, out=SLICE_DEFAULT):
    """Снять срез журнала за N суток в файл. Читает мозг ТОЛЬКО через brain_writer — секретов
    этот модуль не видит и не читает (класс 328). → число строк среза."""
    import brain_writer                      # локальный импорт: замер по срезу сети не требует
    text = brain_writer.read_text(name="cowork_log")
    since = datetime.datetime.now(UTC) - datetime.timedelta(days=days)
    kept = []
    for raw in text.splitlines():
        m = _J_HEAD.match(raw.strip())
        if m and journal_ts(m.group(2)) >= since:
            kept.append(raw.strip())
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    return len(kept)


def read_journal(path):
    """Срез журнала → [(тип, момент UTC, текст)] по возрастанию времени."""
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for raw in f:
            m = _J_HEAD.match(raw.strip())
            if m:
                rows.append((m.group(1), journal_ts(m.group(2)), m.group(3)))
    return sorted(rows, key=lambda r: r[1])


def read_daemon_log(path, since):
    """Лог демона → (claims, fails, closed). CLAIM собираем по ВСЕМУ логу (у задачи, взятой до
    окна замера, окно иначе не построить), провалы — только внутри окна. Время лога локальное."""
    claims, fails, closed = {}, {}, set()
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            m = _L_TS.match(raw.rstrip("\n"))
            if not m:
                continue
            ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=PC_TZ).astimezone(UTC)
            body = m.group(2)
            c = _L_CLAIM.match(body)
            if c:
                claims.setdefault(int(c.group(1)), ts)      # ПЕРВЫЙ claim задачи
                continue
            t = _L_TERM.match(body)
            if t:
                closed.add(int(t.group(1)))
            if ts >= since:
                fl = _L_FAIL.match(body)
                if fl:
                    fails[int(fl.group(1))] = ts
    return claims, fails, closed


def read_commits(since, repo=HERE):
    """Коммиты репо от since → [(момент UTC, хеш, заголовок)]."""
    p = subprocess.run(["git", "log", "--since=" + since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000"),
                        "--pretty=%h\x1f%cI\x1f%s"], cwd=repo, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rows = []
    for line in (p.stdout or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            rows.append((datetime.datetime.fromisoformat(parts[1]).astimezone(UTC), parts[0], parts[2]))
    return sorted(rows)


def classify(rows, claims, fails, closed, commits, now=None):
    """Разложить провалы на «за ними была работа» / «статус честен» / «окно неизвестно» и отдельно
    выделить клеймлённые задачи БЕЗ терминала своей полосы. Чистая функция — вся арифметика замера
    здесь, печать снаружи."""
    now = now or datetime.datetime.now(UTC)
    lying, honest, unknown = [], [], []
    for tid in sorted(fails):
        ft, why = fails[tid]
        ct = claims.get(tid)
        if ct is None:
            unknown.append((tid, ft, why))
            continue
        wc = [c for c in commits if ct <= c[0] <= ft]
        wj = [r for r in rows if ct <= r[1] <= ft and not _J_ORCH.match(r[2])]
        (lying if (wc or wj) else honest).append((tid, ct, ft, wc, wj, why))
    lying.sort(key=lambda r: (not r[3], r[0]))      # сначала с коммитом (твёрдая улика)
    orphan = []
    for tid, ct in sorted(claims.items()):
        if tid in closed or tid in fails:
            continue
        end = min(now, ct + datetime.timedelta(seconds=ORPHAN_WINDOW_SEC))
        orphan.append((tid, ct,
                       [c for c in commits if ct <= c[0] <= end],
                       [r for r in rows if ct <= r[1] <= end and not _J_ORCH.match(r[2])]))
    return lying, honest, unknown, orphan


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--fetch" in argv:
        argv.remove("--fetch")
        print("срез журнала снят: строк %d → %s" % (fetch_slice(), SLICE_DEFAULT))
    slice_path = argv[0] if len(argv) > 0 else SLICE_DEFAULT
    log_path = argv[1] if len(argv) > 1 else LOG_DEFAULT
    hours = float(argv[2]) if len(argv) > 2 else WINDOW_HOURS
    now = datetime.datetime.now(UTC)
    since = now - datetime.timedelta(hours=hours)

    rows = [r for r in read_journal(slice_path) if r[1] >= since]
    claims, log_fails, closed = read_daemon_log(log_path, since)
    commits = read_commits(since)

    fails = {}
    for _t, ts, text in rows:
        c = _J_CLAIM.match(text)
        if c:
            claims.setdefault(int(c.group(1)), ts)
        f = _J_FAIL.match(text)
        if f:
            fails[int(f.group(1))] = (ts, (f.group(2) or "").strip(" ·"))
        t = _J_TERM.match(text)
        if t:
            closed.add(int(t.group(1)))
    for tid, ts in log_fails.items():          # провалы, до журнала не доехавшие
        fails.setdefault(tid, (ts, "(строки в журнале нет — только лог демона)"))
    # клеймлённые ДО окна замера отбрасываем: их окно неполно, судить по нему нельзя
    claims = {k: v for k, v in claims.items() if v >= since or k in fails}

    lying, honest, unknown, orphan = classify(rows, claims, fails, closed, commits, now=now)

    print("ОКНО ЗАМЕРА: %s → %s UTC (%.0f ч)" % (since.strftime("%Y-%m-%d %H:%M"),
                                                 now.strftime("%Y-%m-%d %H:%M"), hours))
    print("строк журнала: %d | CLAIM: %d | провалов: %d | коммитов: %d\n"
          % (len(rows), len(claims), len(fails), len(commits)))
    print("=" * 100)
    print("ПРОВАЛЫ, ЗА КОТОРЫМИ ЕСТЬ РАБОТА (коммит и/или запись в журнал внутри окна задачи)")
    print("=" * 100)
    for tid, ct, ft, wc, wj, why in lying:
        print("#%-4s окно %s→%s UTC (%d мин)" % (tid, ct.strftime("%m-%d %H:%M"), ft.strftime("%H:%M"),
                                                 (ft - ct).total_seconds() // 60))
        print("      причина в статусе: %s" % (why[:150] or "(текста нет)"))
        for c in wc:
            print("      КОММИТ  %s %s  %s" % (c[1], c[0].strftime("%H:%M"), c[2][:80]))
        for r in wj:
            print("      ЖУРНАЛ  %s %s  %s" % (r[0], r[1].strftime("%H:%M"), r[2][:80]))
        print()
    if orphan:
        print("=" * 100)
        print("КЛЕЙМИЛИ, НО ТЕРМИНАЛА СВОЕЙ ПОЛОСЫ НЕТ (статус ставит чужая полоса — ПК его не знает)")
        print("=" * 100)
        for tid, ct, wc, wj in orphan:
            print("#%-4s claim %s UTC → терминальной строки на ПК НЕТ" % (tid, ct.strftime("%m-%d %H:%M")))
            for c in wc:
                print("      КОММИТ  %s %s  %s" % (c[1], c[0].strftime("%H:%M"), c[2][:80]))
            for r in wj[:4]:
                print("      ЖУРНАЛ  %s %s  %s" % (r[0], r[1].strftime("%H:%M"), r[2][:80]))
            print()
    print("=" * 100)
    print("ПРОВАЛЫ БЕЗ СЛЕДОВ РАБОТЫ (статус честен): %s"
          % (", ".join("#%s" % r[0] for r in honest) or "нет"))
    print("ПРОВАЛЫ БЕЗ ИЗВЕСТНОГО ОКНА: %s" % (", ".join("#%s" % r[0] for r in unknown) or "нет"))
    print("=" * 100)
    total = len(lying) + len(honest)
    hard = [r for r in lying if r[3]]
    print("ЦЕНА КЛАССА: %d из %d разобранных провалов (%.0f%%) дали коммит или запись — статус соврал."
          % (len(lying), total, (100.0 * len(lying) / total) if total else 0.0))
    print("  с КОММИТОМ в окне (твёрдая улика): %d — %s"
          % (len(hard), ", ".join("#%s" % r[0] for r in hard) or "нет"))
    print("  только с записью в журнал: %d — %s"
          % (len(lying) - len(hard), ", ".join("#%s" % r[0] for r in lying if not r[3]) or "нет"))
    print("  плюс задач без терминала своей полосы: %d — %s"
          % (len(orphan), ", ".join("#%s" % r[0] for r in orphan) or "нет"))


if __name__ == "__main__":
    main()
