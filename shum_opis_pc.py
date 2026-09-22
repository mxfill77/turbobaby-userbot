# -*- coding: utf-8 -*-
"""ОПИСЬ ШУМА ВЛАДЕЛЬЦУ — ПОЛОСА ПК (22.09.2026, задание Штаба 0015k-71f.2209). ТОЛЬКО ЧТЕНИЕ.

Что делает: проходит журнал доставки (`dispatch_notify.log`, строки `итог … ok=True`) и каждому
ушедшему сообщению ставит РОД и КЛАСС из МЕСТА СОБЫТИЯ, а не из слов сообщения:
  • пуш гарда (`🔴`/`⛔ ВЫСШАЯ ЦЕНА`) — класс берётся из строки решения гарда `ask | <класс>` в
    `pretool_guard.log*`, записанной В ТУ ЖЕ МИНУТУ до доставки (пара один к одному, ближайшая
    раньше). С 22.09 у пуша есть и ключ `--guard-push`, тогда класс берётся из строки реестра по
    номеру сообщения. Лога гарда нет (окно ротации) → класс «НЕИЗВЕСТНО», а не догадка по тексту;
  • `⛔` демона «авто-рестарт отменён» — только при паре со строкой демона `дерево ГРЯЗНОЕ —
    авто-рестарт` в `pc_orchestrator.log*` той же минуты;
  • `🔔` — ветка `notification` журнала (хук Claude Code).
Прочее идёт родом «прочее» со своей веткой журнала.

Сеть не трогает, в Telegram не ходит, боевых файлов не пишет. Единственная запись — файл описи
по пути, который назвал вызывающий (`--opis <путь>`).
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

import card_ledger_pc

HERE = os.path.dirname(os.path.abspath(__file__))

_RE_ITOG = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \| итог(\([^)]*\))?: "
                      r"channel=(\S+) ok=(\S+)(?: mid=(\S+))? \| (.*)$")
_RE_GUARD = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (\w+) \| ([^|]*?) \| "
                       r"(\w+) \| ([^|]*?) \| ?(.*)$")
_RE_DEMON_DIRTY = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ ERROR .*дерево ГРЯЗНОЕ — "
                             r"авто-рестарт")

ROD_GUARD = card_ledger_pc.ROD_GUARD
ROD_GUARD_TOP = card_ledger_pc.ROD_GUARD_TOP
ROD_WAIT = card_ledger_pc.ROD_WAIT
ROD_DEMON_RESTART = card_ledger_pc.ROD_DEMON_RESTART
ROD_OTHER = "прочее"
CLS_UNKNOWN_PLACE = "НЕИЗВЕСТНО-нет-лога-гарда"

_WIN_TEMP = re.compile(r"(?i)(\\|/)AppData(\\|/)Local(\\|/)Temp(\\|/)|%TEMP%|\$env:TEMP|/tmp/claude")

JOIN_S = 30          # доставка идёт после решения гарда: окно пары — назад от строки итога


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def parse_itog_any(line):
    """Строка итога журнала доставки → dict | None. Номер сообщения может отсутствовать (до 05.09)."""
    m = _RE_ITOG.match(str(line or "").rstrip("\r\n"))
    if not m:
        return None
    ts, br, ch, ok, mid, text = m.groups()
    return {"ts": ts, "branch": (br or "")[1:-1], "channel": ch, "ok": ok == "True",
            "mid": mid if (mid or "").isdigit() else "", "text": text}


def parse_guard_asks(lines):
    """Строки решения гарда `ask` → [(ts, role, tool, kind, detail)] по времени."""
    out = []
    for ln in lines or ():
        m = _RE_GUARD.match(str(ln or "").rstrip("\r\n"))
        if m and m.group(4) == "ask":
            out.append((m.group(1), m.group(2), m.group(3).strip(), m.group(5).strip(), m.group(6)))
    out.sort(key=lambda r: r[0])
    return out


def parse_demon_dirty(lines):
    return sorted(m.group(1) for m in (_RE_DEMON_DIRTY.match(str(x or "")) for x in lines or ()) if m)


def _pick(cands, used, t, window_s=JOIN_S):
    """Ближайший НЕзанятый кандидат не позже t и не раньше t−window → индекс | None."""
    best = None
    for i, c in enumerate(cands):
        ct = _ts(c if isinstance(c, str) else c[0])
        if ct > t:
            break
        if i in used or (t - ct).total_seconds() > window_s:
            continue
        best = i
    return best


def classify(itog_lines, guard_lines, demon_lines, ledger_rows, since, until):
    """→ список ушедших сообщений окна [since, until) с полями rod, cls, place, where."""
    asks = parse_guard_asks(guard_lines)
    guard_from = asks[0][0] if asks else "9999"
    dirty = parse_demon_dirty(demon_lines)
    by_mid = {(str(r.get("channel") or ""), str(r.get("mid") or "")): r
              for r in ledger_rows or () if r.get("event") == card_ledger_pc.EV_PUSH}
    used_a, used_d, out = set(), set(), []
    for ln in itog_lines or ():
        rec = parse_itog_any(ln)
        if not rec or not rec["ok"] or not (since <= rec["ts"] < until):
            continue
        t, text, br = _ts(rec["ts"]), rec["text"], rec["branch"]
        rec.update(rod=ROD_OTHER, cls=br or "-", place="журнал доставки", where="")
        guardish = br in ("", card_ledger_pc.SRC_GUARD_PUSH) and (
            text.startswith("🔴") or (text.startswith("⛔") and "ВЫСШАЯ ЦЕНА" in text))
        if br == "notification":
            rec.update(rod=ROD_WAIT, cls="-", place="хук Notification")
        elif guardish:
            rec["rod"] = ROD_GUARD_TOP if text.startswith("⛔") else ROD_GUARD
            row = by_mid.get((rec["channel"], rec["mid"])) if rec["mid"] else None
            i = _pick(asks, used_a, t)
            if row is not None:
                rec.update(cls=str(row.get("class") or "-"), place="реестр по номеру (--guard-push)")
                if i is not None:
                    used_a.add(i)
            elif i is not None:
                used_a.add(i)
                rec.update(cls=asks[i][3], place="лог гарда", where=asks[i][4],
                           role=asks[i][1])
            elif rec["ts"] < guard_from:
                rec.update(cls=CLS_UNKNOWN_PLACE, place="нет лога гарда (ротация)")
            else:
                rec.update(cls="без-пары", place="в логе гарда пары нет")
        elif text.startswith("⛔ Оркестратор: авто-рестарт"):
            j = _pick(dirty, used_d, t)
            if j is not None:
                used_d.add(j)
                rec.update(rod=ROD_DEMON_RESTART, cls="авто-рестарт-отменён",
                           place="лог демона (дерево ГРЯЗНОЕ)")
        out.append(rec)
    return out


def premise_bucket(rec):
    """Четыре корзины премисы Штаба — по КЛАССУ из места события (и пути из строки гарда)."""
    c = rec.get("cls") or ""
    if rec["rod"] == ROD_DEMON_RESTART:
        return "отмена авторестарта"
    if rec["rod"] not in (ROD_GUARD, ROD_GUARD_TOP):
        return None
    if c in ("env", "read_secret", "edit_secret"):
        return "чтение/правка секретов"
    if c == "network":
        return "выход в сеть"
    if c in ("write_outside", "outside") and _WIN_TEMP.search(rec.get("where") or ""):
        return "запись во временную папку Windows"
    if c in ("write_outside", "outside"):
        return "запись/чтение вне проекта (не temp)"
    return None


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except Exception:
        return []


def _read_many(base, n=6):
    lines = []
    for k in range(n, 0, -1):
        lines += _read("%s.%d" % (base, k))
    return lines + _read(base)


def load_live(since, until):
    return classify(_read(os.path.join(HERE, "dispatch_notify.log")),
                    _read_many(os.path.join(HERE, "pretool_guard.log")),
                    _read_many(os.path.join(HERE, "pc_orchestrator.log")),
                    card_ledger_pc.load(card_ledger_pc.default_path()), since, until)


def summary(recs):
    s = {"total": len(recs), "by_rod": {}, "by_rod_cls": {}, "premise": {}, "by_day": {},
         "by_place": {}, "by_channel": {}, "day_0912": {}, "suppressible": 0, "no_mid": 0}
    for r in recs:
        s["by_rod"][r["rod"]] = s["by_rod"].get(r["rod"], 0) + 1
        s["by_place"][r["place"]] = s["by_place"].get(r["place"], 0) + 1
        ch = r["channel"].split("→")[0]
        s["by_channel"][ch] = s["by_channel"].get(ch, 0) + 1
        if r["ts"].startswith("2026-09-12"):
            k12 = "%s · %s" % (r["rod"], r["cls"])
            s["day_0912"][k12] = s["day_0912"].get(k12, 0) + 1
        if not r["mid"]:
            s["no_mid"] += 1
        k = "%s · %s" % (r["rod"], r["cls"])
        s["by_rod_cls"][k] = s["by_rod_cls"].get(k, 0) + 1
        b = premise_bucket(r)
        if b:
            s["premise"][b] = s["premise"].get(b, 0) + 1
        d = r["ts"][:10]
        s["by_day"][d] = s["by_day"].get(d, 0) + 1
        if card_ledger_pc.push_shown(r["rod"], r["cls"]) is False:
            s["suppressible"] += 1
    return s


def write_opis(recs, path, since, until):
    """Файл описи: по каждому роду — число; для гасимых родов/классов — номера сообщений по
    чатам пачками по 100 (Telegram `deleteMessages` принимает до 100 номеров одного чата)."""
    groups = {}
    for r in recs:
        shown = card_ledger_pc.push_shown(r["rod"], r["cls"])
        key = (r["rod"], r["cls"], "гасится" if shown is False else "остаётся")
        groups.setdefault(key, []).append(r)
    L = ["# Опись отправленного владельцу %s … %s (местное время ПК)" % (since, until), "",
         "Составлено `shum_opis_pc.py` (только чтение) из `dispatch_notify.log`; род и класс — из "
         "места события (лог гарда / реестр по номеру / лог демона / ветка журнала).", "",
         "| род | класс | после правки | сообщений | с номером |", "|---|---|---|---|---|"]
    for (rod, cls, fate), rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        L.append("| %s | %s | %s | %d | %d |" % (rod, cls, fate, len(rs), sum(1 for x in rs if x["mid"])))
    L += ["", "## Номера сообщений гасимых родов — для удаления владельцем", "",
          "Чат `inbox` и `topic:*` — одна группа (разные темы), `DM` — личка. Строки без номера "
          "(до 05.09 журнал номер не писал) удалению по номеру не подлежат и здесь не перечислены.", ""]
    for (rod, cls, fate), rs in sorted(groups.items()):
        if fate != "гасится":
            continue
        per_ch = {}
        for x in rs:
            if x["mid"]:
                ch = "группа" if not x["channel"].startswith("DM") else "личка"
                per_ch.setdefault(ch, []).append(int(x["mid"]))
        L.append("### %s · %s — %d сообщений" % (rod, cls, len(rs)))
        for ch, mids in sorted(per_ch.items()):
            mids = sorted(set(mids))
            L.append("* %s, номеров %d:" % (ch, len(mids)))
            for i in range(0, len(mids), 100):
                L.append("  * `%s`" % ",".join(str(m) for m in mids[i:i + 100]))
        L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path


def main(argv):
    since = argv[0] if argv else "2026-08-22 00:00:00"
    until = argv[1] if len(argv) > 1 else "2026-09-23 00:00:00"
    recs = load_live(since, until)
    s = summary(recs)
    days = max((_ts(until) - _ts(since)).total_seconds() / 86400.0, 1e-9)
    print(json.dumps({k: v for k, v in s.items() if k != "by_day"}, ensure_ascii=False, indent=1))
    print("по дням:", json.dumps(s["by_day"], ensure_ascii=False))
    print("сутки окна: %.3f · в сутки ушло %.2f · гасимых в сутки %.2f"
          % (days, s["total"] / days, s["suppressible"] / days))
    if "--opis" in argv:
        print("опись:", write_opis(recs, argv[argv.index("--opis") + 1], since, until))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
