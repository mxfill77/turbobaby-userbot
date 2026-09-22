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


if __name__ == "__main__":        # только чтение: счёт по файлу, путь — первым аргументом
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "pc_orchestrator.cards_ledger.jsonl")
    rs = load(p)
    print("строк: %d · файл: %s" % (len(rs), p))
    for c, s in sorted(tally(rs).items()):
        print("%-24s карточек %d · показов %d · с операцией %d · ответов %d · без ответа %d · %s"
              % (c, s["cards"], s["shown"], s["op"], s["answered"], s["unanswered"], s["answers"]))
    print("классы без операций:", ", ".join(classes_without_op(rs)) or "нет")
