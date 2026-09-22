# -*- coding: utf-8 -*-
"""СЧЁТ КАРТОЧЕК И ОТВЕТОВ ВЛАДЕЛЬЦА — ПОЛОСА ПК (22.09.2026, задание Штаба 0015g-71a.2209).

ПОВОД — ЗАМЕР, А НЕ ДОГАДКА (`docs/artifacts/2026-09-22-KARTOCHKISCHET-2209.md`). Факт «карточка
показана» на полосе был, но РАЗМАЗАН по трём местам разной формы (лог демона, журнал, файл
надзора), а факт «владелец ответил» жил строкой ЖУРНАЛА (`card_terminal_log`, с 16.08) и
реестром `pc_orchestrator.approvals.jsonl`, у которого единственный вызывающий — CLI, и потому
последняя запись там от 31.07. Счёт «сколько карточек какого класса, за сколькими стояла
операция, сколько ответов» собирался бы грепом по прозе — то есть не собирался вовсе.

ЧТО МОДУЛЬ ДЕЛАЕТ. Одна строка JSONL на событие, в момент события, двумя видами:
  • `показана` — карточка ДОСТАВЛЕНА (расписка моста легла), пишет её тот, кто карточку выписал;
  • `ответ`    — терминал карточки НАБЛЮДЁН (разрешено / отказано / истекло / закрыто).
И чистый счёт по классам (`tally`) — из этих строк, а не из текста карточек.

ГЛАВНЫЙ ЗАМОК — ПРИЗНАК «ЗА КАРТОЧКОЙ СТОЯЛА ОПЕРАЦИЯ» НЕЛЬЗЯ ПОДНЯТЬ СЛОВОМ.
Параметра `op` у `shown_row` НЕТ. Признак выводится из ИСТОЧНИКА (`src`), и ровно один источник
даёт `op=True` — `SRC_GUARD`: его зовёт единственная ветка демона, куда управление приходит
только через `_detect_needs_approval`, а тот признаёт карточкой ТОЛЬКО файл-маркер, который
пишет сам гард в момент перехвата настоящего вызова инструмента, со своим `run_token`.
Строка `NEEDS_APPROVAL:`, напечатанная моделью, туда не доезжает (она — `FAIL_UNBACKED_RED`).
Текст карточки на признак не влияет ни одной веткой: «Класс операции: env» в теле карточки
ревизора `op` не поднимет, потому что ревизор зовёт `shown_row` со своим источником.

ПДн И СЕКРЕТЫ НЕ ТЕКУТ. В строку идут номер, класс, объект (через
`card_terminal_log.safe_object` — имя, выдающее секрет, заменяется целиком), слово исхода и
источник. Текста карточки здесь нет: у owner-карточки ревизора в тексте живут выдержки из
переписки менеджеров, поэтому ревизор объект не передаёт вовсе.

ЧАСОВ У МОДУЛЯ НЕТ: `now_iso` приходит параметром (идиома `card_terminal_log`).
ОТКАТ: `CARD_LEDGER_OFF=1` — ни одной строки, поведение демона байт-в-байт прежнее.
"""

import json
import os
import re as _re
from datetime import datetime as _dt

import card_terminal_log

EV_SHOWN = "показана"
EV_ANSWER = "ответ"

# ИСТОЧНИКИ «показана». Операцию несёт ТОЛЬКО первый (см. шапку).
SRC_GUARD = "маркер-гарда"          # needs_approval из файла-маркера гарда с нашим run_token
SRC_REVIZOR = "ревизор"             # сводная owner-карточка ревизора (`_revizor_post_owner_card`)
_OP_SOURCES = frozenset((SRC_GUARD,))

# ИСТОЧНИКИ «ответ».
SRC_TERMINAL = "терминал-очереди"   # статус ряда ушёл из needs_approval (`process_card_terminals`)
SRC_REVIZOR_ANSWER = "ревизор-ответ"

CLS_REVIZOR = "ревизор"
CLS_UNNAMED = "без-класса"          # гард не проставил штамп класса — говорим это, а не гадаем

# «Истекло» — терминал БЕЗ ответа: владелец не решал ничего (тот же довод, что у ревизора).
NO_ANSWER_WORDS = frozenset((card_terminal_log.OUT_EXPIRED,))


def off(env=None):
    e = os.environ if env is None else env
    return (str(e.get("CARD_LEDGER_OFF", "")).strip() or "0") not in ("0", "", "false", "no")


def class_of(kinds):
    """Классы карточки → ОДНО имя класса для счёта. Несколько классов — через запятую, по
    алфавиту: карточка считается один раз, а не раз на класс."""
    ks = sorted({str(k).strip() for k in (kinds or ()) if str(k).strip()})
    return ", ".join(ks) if ks else CLS_UNNAMED


def shown_row(tid, cls, src, now_iso, obj=""):
    """Строка «карточка показана». `op` выводится из источника и больше ни из чего."""
    return {"ts": str(now_iso or ""), "event": EV_SHOWN, "task": str(tid),
            "class": str(cls or CLS_UNNAMED), "object": card_terminal_log.safe_object(obj),
            "op": src in _OP_SOURCES, "src": str(src or "")}


def answer_row(tid, cls, answer, src, now_iso):
    """Строка «терминал карточки наблюдён». `answered` = владелец РЕШИЛ (истечение — не решение)."""
    a = str(answer or "")
    return {"ts": str(now_iso or ""), "event": EV_ANSWER, "task": str(tid),
            "class": str(cls or CLS_UNNAMED), "answer": a,
            "answered": bool(a) and a not in NO_ANSWER_WORDS, "src": str(src or "")}


def append(path, row):
    """Дописать строку → ok. НИКОГДА не бросает: сорванный след не смеет отменить карточку."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def load(path):
    """Строки с диска → список dict. Нет файла / битая строка → пропуск, а не падение."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for ln in f:
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except Exception:
        return []
    return out


def tally(rows):
    """ЧИСТЫЙ счёт по классам → {класс: {cards, shown, op, answered, unanswered, answers}}.

    cards      — различных карточек (номеров задач) класса;
    shown      — событий показа (ревизор правит ту же карточку — это новый показ);
    op         — карточек, за которыми стояла операция (признак — из источника показа);
    answered   — карточек, по которым владелец РЕШИЛ (первый наблюдённый ответ);
    unanswered — cards − answered (висит, истекло или терминал не наблюдён)."""
    t, first_answer, op_tids = {}, {}, {}
    cls_of_task = {}
    for r in rows or ():
        if r.get("event") != EV_SHOWN:
            continue
        c, tid = str(r.get("class") or CLS_UNNAMED), str(r.get("task") or "")
        s = t.setdefault(c, {"cards": 0, "shown": 0, "op": 0, "answered": 0, "unanswered": 0,
                             "answers": {}})
        s["shown"] += 1
        key = (c, tid)
        if key not in cls_of_task:
            cls_of_task[key] = True
            s["cards"] += 1
        if r.get("op") is True and key not in op_tids:
            op_tids[key] = True
            s["op"] += 1
    for r in rows or ():
        if r.get("event") != EV_ANSWER:
            continue
        key = (str(r.get("class") or CLS_UNNAMED), str(r.get("task") or ""))
        if key in first_answer or key not in cls_of_task:
            continue                      # ответ без показа в окне не считаем: карточку не видели
        first_answer[key] = r
        s = t[key[0]]
        w = str(r.get("answer") or "")
        s["answers"][w] = s["answers"].get(w, 0) + 1
        if r.get("answered") is True:
            s["answered"] += 1
    for s in t.values():
        s["unanswered"] = s["cards"] - s["answered"]
    return t


def classes_without_op(rows):
    """Классы, у которых за окно строк есть карточки и НИ ОДНА не стояла за операцией."""
    return sorted(c for c, s in tally(rows).items() if s["cards"] > 0 and s["op"] == 0)


# ══ ПУШИ БЕЗ КНОПКИ (22.09.2026, задание Штаба 0015i-71c.2209) ═══════════════════════════════
# Замер 71a: кнопочных карточек за 16.08–22.09 — 22, а мимо счёта с 05.09 ушло 525 `🔴`, 59 `⛔` и
# 169 `🔔`. Это три РОДА сообщений без кнопки, и у них ДВА места события:
#   • `🔴`/`⛔` — `pretool_guard._emit_ask` → `_push`: гард ПЕРЕХВАТИЛ настоящий вызов инструмента
#     и выписал карточку; `⛔` — тот же путь для класса высшей цены (`is_top_tier`). Гард зовёт
#     отправителя ЯВНЫМ КЛЮЧОМ `--guard-push <род> <класс>` — это и есть признак из места события;
#   • `🔔` — хук `Notification` Claude Code (`dispatch_notify --hook notification`): сессия ждёт
#     разрешения. В payload хука есть только ТЕКСТ («Claude needs your permission to use Bash»),
#     решения гарда хук не видит, класса операции не знает. ПРИЗНАКОМ ЭТОТ РОД НЕ НАДЕЛЯЕТСЯ:
#     `op=None` («в месте события признака нет»), а не False и не True.
# Строка пишется ПОСЛЕ доставки (Telegram вернул message_id): событие — «сообщение ушло», а не
# «собирались отправить». Текста сообщения в строке нет — только род, класс, источник, канал и
# номер сообщения; по номеру строка СВЕРЯЕМА с журналом доставки (`reconcile`).
EV_PUSH = "пуш"

ROD_GUARD = "гард-карточка"        # 🔴
ROD_GUARD_TOP = "гард-высшая"      # ⛔ ВЫСШАЯ ЦЕНА
ROD_WAIT = "хук-ожидания"          # 🔔 Dispatch ждёт твоего разрешения
PUSH_RODS = (ROD_GUARD, ROD_GUARD_TOP, ROD_WAIT)

SRC_GUARD_PUSH = "гард-пуш"        # dispatch_notify --guard-push (зовёт ТОЛЬКО pretool_guard._push)
SRC_WAIT_HOOK = "хук-notification" # dispatch_notify --hook notification
_PUSH_OP_SOURCES = frozenset((SRC_GUARD_PUSH,))
_PUSH_NO_SIGN_SOURCES = frozenset((SRC_WAIT_HOOK,))

GUARD_PUSH_FLAG = "--guard-push"   # ключ отправителя; гард и dispatch_notify берут ОДНУ константу


def push_row(rod, src, cls, channel, mid, now_iso):
    """Строка «пуш без кнопки ушёл». Параметра `op` НЕТ: признак выводится из источника.
    SRC_GUARD_PUSH → True (гард перехватил вызов); SRC_WAIT_HOOK → None (признака в месте события
    нет); любой другой источник → None (не знаем — не наделяем)."""
    s = str(src or "")
    op = True if s in _PUSH_OP_SOURCES else None
    return {"ts": str(now_iso or ""), "event": EV_PUSH, "rod": str(rod or ""),
            "class": str(cls or CLS_UNNAMED), "op": op, "src": s,
            "channel": str(channel or ""), "mid": str(mid or "")}


def push_tally(rows):
    """ЧИСТЫЙ счёт пушей по родам → {род: {pushes, op_true, op_none, classes: {класс: n}}}."""
    t = {}
    for r in rows or ():
        if r.get("event") != EV_PUSH:
            continue
        s = t.setdefault(str(r.get("rod") or ""), {"pushes": 0, "op_true": 0, "op_none": 0,
                                                   "classes": {}})
        s["pushes"] += 1
        if r.get("op") is True:
            s["op_true"] += 1
        elif r.get("op") is None:
            s["op_none"] += 1
        c = str(r.get("class") or CLS_UNNAMED)
        s["classes"][c] = s["classes"].get(c, 0) + 1
    return t


# ── ЖУРНАЛ ДОСТАВКИ (`dispatch_notify.log`) → записи. Нужен ДВУМ приборам: сверке и парам. ──────
# Род строки журнала здесь определяется для АУДИТА (какие сообщения ушли), а не для признака
# операции: `op` из журнала не выводится нигде. Формат строки — `arch_text` отправителя:
# `ГГГГ-ММ-ДД ЧЧ:ММ:СС,мс | итог[(ветка)]: channel=… ok=… mid=… | <текст>` (местное время).
_RE_ITOG = _re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \| итог(\([^)]*\))?: "
                       r"channel=(\S+) ok=(\S+) mid=(\S+) \| (.*)$")
_RE_CMD = _re.compile(r"Команда: (?:(?:Bash|PowerShell|Write|Edit|NotebookEdit|Read): )?(.*?)(?: ⏎ |$)")


def parse_itog(line):
    """Строка журнала доставки → dict | None (не строка итога / без mid)."""
    m = _RE_ITOG.match(str(line or "").rstrip("\r\n"))
    if not m:
        return None
    ts, branch, channel, ok, mid, text = m.groups()
    return {"ts": ts, "branch": (branch or "")[1:-1], "channel": channel,
            "ok": ok == "True", "mid": mid, "text": text}


def rod_of_itog(rec):
    """Род отправленного сообщения по записи журнала → ROD_* | None (не наш род)."""
    t, b = rec.get("text") or "", rec.get("branch") or ""
    if b == "notification" and t.startswith("🔔"):
        return ROD_WAIT
    if b not in ("", SRC_GUARD_PUSH):
        return None                       # ветки с кнопкой / тема / критич — чужие роды
    if t.startswith("🔴"):
        return ROD_GUARD
    if t.startswith("⛔") and "ВЫСШАЯ ЦЕНА" in t:
        return ROD_GUARD_TOP
    return None


def sent_pushes(lines, since=""):
    """Записи журнала: ушедшие (ok=True, есть номер) сообщения трёх родов, не раньше `since`."""
    out = []
    for ln in lines or ():
        rec = parse_itog(ln)
        if not rec or not rec["ok"] or not rec["mid"].isdigit():
            continue
        if since and rec["ts"] < since:
            continue
        rod = rod_of_itog(rec)
        if rod:
            rec["rod"] = rod
            out.append(rec)
    return out


def reconcile(lines, rows, since):
    """СВЕРКА «ушло ⇔ записано» по номеру сообщения → dict(verdict, sent, registered, missing).

    verdict: «СВЕРЕНО» — каждому ушедшему пушу трёх родов есть строка реестра с тем же
    (канал, номер); «ОТКАЗ» — хоть одному нет; «НЕИЗВЕСТНО» — сверять нечего (ноль ушедших:
    это не успех). `since` обязателен — окно начинается с применения, а не «когда-нибудь»."""
    if not since:
        return {"verdict": "НЕИЗВЕСТНО", "why": "окно не названо", "sent": 0, "registered": 0,
                "missing": []}
    have = {(str(r.get("channel") or ""), str(r.get("mid") or ""))
            for r in rows or () if r.get("event") == EV_PUSH}
    sent = sent_pushes(lines, since)
    missing = [(p["ts"], p["rod"], p["channel"], p["mid"]) for p in sent
               if (p["channel"], p["mid"]) not in have]
    if not sent:
        verdict = "НЕИЗВЕСТНО"
    else:
        verdict = "ОТКАЗ" if missing else "СВЕРЕНО"
    return {"verdict": verdict, "why": "", "sent": len(sent), "registered": len(sent) - len(missing),
            "missing": missing}


def _cmd_key(text, n=40):
    m = _RE_CMD.search(text or "")
    return "".join((m.group(1) if m else "").split())[:n]


def pair_waits(lines, since="", until="", window_s=60):
    """ДУБЛЬ ЧИСЛОМ: каждому ушедшему `🔔` ищется ушедший `🔴`/`⛔` ±window_s (пара один к одному).

    → dict(total, proven, time_only, none). proven — пара по времени И тот же повод (ключ
    команды: первые 40 непробельных знаков команды совпали); time_only — пара по времени, повод
    НЕ сверен (команда другая или её нет) — это НЕИЗВЕСТНО, а не дубль; none — пары нет, тоже
    НЕИЗВЕСТНО, а не «не дубль». Знаменатель — total."""
    recs = [p for p in sent_pushes(lines, since) if not until or p["ts"] < until]
    t = lambda p: _dt.strptime(p["ts"], "%Y-%m-%d %H:%M:%S")
    guards = [p for p in recs if p["rod"] in (ROD_GUARD, ROD_GUARD_TOP)]
    used = set()
    out = {"total": 0, "proven": 0, "time_only": 0, "none": 0}
    for w in (p for p in recs if p["rod"] == ROD_WAIT):
        out["total"] += 1
        tw, kw = t(w), _cmd_key(w["text"])
        best = None
        for i, g in enumerate(guards):
            if i in used:
                continue
            d = abs((tw - t(g)).total_seconds())
            if d <= window_s and (best is None or d < best[0]):
                best = (d, i, g)
        if best is None:
            out["none"] += 1
            continue
        used.add(best[1])
        kg = _cmd_key(best[2]["text"])
        if kw and kg and (kw.startswith(kg) or kg.startswith(kw)):
            out["proven"] += 1
        else:
            out["time_only"] += 1
    return out


# ══ ПОКАЗ ТОЛЬКО ПО СПИСКУ ВЛАДЕЛЬЦА (22.09.2026, задание Штаба 0015k-71f.2209) ════════════════
# Замер 71f (`docs/artifacts/2026-09-22-71f-VOSEMDESYATODIN-2209.md`): пуши гарда классов, которых
# нет в закрытом списке владельца, — основная масса того, что он получает. ПОКАЗАТЬ и РЕШИТЬ —
# разные действия, и здесь меняется ТОЛЬКО первое: решение (ask / deny / маркер → кнопочная
# карточка демона) остаётся за гардом, его код этой правкой не тронут ни строкой. Гашение
# меняет адресат пуша: вместо чата — строка `погашено` в этом реестре с родом, классом и причиной.
#
# СПИСОК ВЛАДЕЛЬЦА = `pretool_guard._TOP_TIER` ДОСЛОВНО (сверяет тест): гард и показ читают один
# список, а не два. Показ ОСТАЁТСЯ и вне списка, где он несёт защиту:
#   • класс не определён (`unknown`, пусто, без штампа) — доказать «вне списка» нечем, FAIL-SAFE;
#   • секреты (`env`/`read_secret`/`edit_secret`) — по заданию показ здесь гасится ТОЛЬКО вместе
#     с отказом по умолчанию, а отказ меняет РЕШЕНИЕ, не адресат (разбор — в артефакте 71f, п.3).
# НЕМОГО ГАШЕНИЯ НЕТ: отправитель гасит пуш только если строка `погашено` ЛЕГЛА; не легла (реестр
# выключен, диск) — пуш уходит как раньше. Откат целиком: `OWNER_MUTE_OFF=1`.
OWNER_LIST_KINDS = ("live_sheet", "clasp", "clasp_push", "clasp_deploy", "clasp_run",
                    "py_write", "delete", "kill")
VAULT_KINDS = ("env", "read_secret", "edit_secret")   # классы файла ключей (п.3 задания 71f)
FAILSAFE_SHOW = ("unknown", "unknown_tool")
# ГАСИТСЯ ТОЛЬКО НАЗВАННОЕ: перечень, а не «всё, чего нет в списке». Новый класс гарда, опечатка,
# пустое имя, «без-класса» — показываются: доказать «вне списка» можно только поимённо. Четыре
# перечня вместе дают ровно `pretool_guard._KIND_VOCAB` и не пересекаются (сверяет тест).
MUTED_KINDS = ("network", "outside", "write_outside", "edit_claude", "git_force", "sqlite",
               "schtasks")
EV_MUTED = "погашено"
ROD_DEMON_RESTART = "демон-авторестарт"   # ⛔ «авто-рестарт отменён» (опись; гашением не управляется)
MUTE_REASON = "класс вне списка владельца — показ снят, решение осталось за гардом"


def mute_off(env=None):
    e = os.environ if env is None else env
    return (str(e.get("OWNER_MUTE_OFF", "")).strip() or "0") not in ("0", "", "false", "no")


def push_shown(rod, cls, env=None):
    """Показывать ли пуш владельцу → True (показ) | False (гасится) | None (род не наш — не решаем).

    Решает ТОЛЬКО для пуша гарда. `гард-высшая` показывается всегда: гард ставит этот род из
    `is_top_tier(kind)`, то есть ровно из списка владельца."""
    if rod not in (ROD_GUARD, ROD_GUARD_TOP):
        return None
    if mute_off(env) or rod == ROD_GUARD_TOP:
        return True
    c = str(cls or "").strip()
    if c in OWNER_LIST_KINDS:
        return True                       # список владельца — показ всегда
    return c not in MUTED_KINDS           # секреты, unknown, неназванное — показ (см. шапку)


def muted_row(rod, src, cls, now_iso, reason=MUTE_REASON):
    """Строка «пуш погашен». Признак операции — из источника, как у `push_row`."""
    s = str(src or "")
    return {"ts": str(now_iso or ""), "event": EV_MUTED, "rod": str(rod or ""),
            "class": str(cls or CLS_UNNAMED), "op": True if s in _PUSH_OP_SOURCES else None,
            "src": s, "reason": str(reason or "")}


def muted_tally(rows):
    """{(род, класс): n} по строкам `погашено`."""
    t = {}
    for r in rows or ():
        if r.get("event") == EV_MUTED:
            k = (str(r.get("rod") or ""), str(r.get("class") or CLS_UNNAMED))
            t[k] = t.get(k, 0) + 1
    return t


def default_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "pc_orchestrator.cards_ledger.jsonl")


def _read_lines(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except Exception:
        return []


def main(argv):
    """Только чтение. Без ключей — счёт карточек и пушей по реестру.
    `--reconcile <с какого местного времени> [журнал] [реестр]` → код 0 СВЕРЕНО / 1 ОТКАЗ / 2 НЕИЗВЕСТНО.
    `--pairs <с> <до> [журнал]` — пары `🔔`↔`🔴`/`⛔` за окно."""
    here = os.path.dirname(os.path.abspath(__file__))
    log_p = os.path.join(here, "dispatch_notify.log")
    if argv and argv[0] == "--reconcile":
        since = argv[1] if len(argv) > 1 else ""
        lp = argv[2] if len(argv) > 2 else log_p
        rp = argv[3] if len(argv) > 3 else default_path()
        r = reconcile(_read_lines(lp), load(rp), since)
        print("сверка с %s: %s · ушло %d · записано %d · без строки %d"
              % (since or "—", r["verdict"], r["sent"], r["registered"], len(r["missing"])))
        for ts, rod, ch, mid in r["missing"][:20]:
            print("  нет строки: %s %s %s mid=%s" % (ts, rod, ch, mid))
        return {"СВЕРЕНО": 0, "ОТКАЗ": 1}.get(r["verdict"], 2)
    if argv and argv[0] == "--pairs":
        since = argv[1] if len(argv) > 1 else ""
        until = argv[2] if len(argv) > 2 else ""
        lp = argv[3] if len(argv) > 3 else log_p
        lines = _read_lines(lp)
        sp = [p for p in sent_pushes(lines, since) if not until or p["ts"] < until]
        per = {}
        for p in sp:
            per[p["rod"]] = per.get(p["rod"], 0) + 1
        print("окно %s … %s (местное) · ушло по родам: %s" % (since, until or "конец", per))
        print("пары 🔔: %s" % pair_waits(lines, since, until))
        return 0
    p = argv[0] if argv else default_path()
    rs = load(p)
    print("строк: %d · файл: %s" % (len(rs), p))
    for c, s in sorted(tally(rs).items()):
        print("%-24s карточек %d · показов %d · с операцией %d · ответов %d · без ответа %d · %s"
              % (c, s["cards"], s["shown"], s["op"], s["answered"], s["unanswered"], s["answers"]))
    print("классы без операций:", ", ".join(classes_without_op(rs)) or "нет")
    for rod, s in sorted(push_tally(rs).items()):
        print("пуш %-16s %d · признак да %d · признака нет %d · %s"
              % (rod, s["pushes"], s["op_true"], s["op_none"], s["classes"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
