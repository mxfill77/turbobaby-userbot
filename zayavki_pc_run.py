"""Руки ступени G: очередь → заявка у владельца в Telegram → ответ одним тапом.

Разделение то же, что у ступеней A, B, D и слоя ожиданий: всё, что РЕШАЕТ, живёт
чистым :mod:`zayavki_pc`; здесь только ввод-вывод — очередь, Telegram, реестр,
журнал.

    venv/Scripts/python.exe zayavki_pc_run.py --status              # что видит ступень; не шлём ничего
    venv/Scripts/python.exe zayavki_pc_run.py --dry                 # сообщения собраны, НЕ отправлены
    venv/Scripts/python.exe zayavki_pc_run.py --send                # боевая доставка владельцу
    venv/Scripts/python.exe zayavki_pc_run.py --answer yes --id 3   # ответ (зовёт кнопка pc_agent)
    venv/Scripts/python.exe zayavki_pc_run.py --tick                # оборот демона

ОТВЕТ В ОДНО ДЕЙСТВИЕ — ГДЕ ОН НА САМОМ ДЕЛЕ ИСПОЛНЯЕТСЯ. Кнопка живёт в
сообщении, которое шлёт `dispatch_notify` бот-токеном агента; тап по ней ловит
`pc_agent` (тот же токен — тот же приёмник callback'ов, третьего процесса
заводить не пришлось) и зовёт СЮДА, ``--answer``. Дальше ровно два действия
Моста, и оба УЖЕ существовали:

* ``да``  → ``approve_task`` → ряд становится ``approved`` → штатный гард демона
  ``process_approved`` видит маркер заявки и закрывает её ``done`` со словами
  «принято к сведению», НЕ ЗАПУСКАЯ headless. Задача не ставится, и это не наше
  обещание, а ветка, которая была написана раньше нас.
* ``нет`` → ``complete_task(failed)`` с префиксом отказа владельца
  (:data:`pc_orchestrator._REJECT_PREFIX`) — тем же, которым живут все прочие
  отказы полосы, чтобы слепок очереди узнавал их одним разрезом.

ЗАДАЧУ НЕ СТАВИТ НИ ОДНА ВЕТКА ЭТОГО МОДУЛЯ: ``enqueue`` здесь не зовётся вовсе.

ЧЕГО ЕЩЁ НЕТ НИ ОДНОЙ ВЕТКОЙ: не правит код, не удаляет файлов, не трогает
клиентского контура, не ходит во внешнюю сеть (кроме Telegram и Моста), не
пересылает владельцу чужой текст — разбор обрывается на границе цитаты в чистом
слое, сюда чужие строки не доезжают.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import subprocess
import sys

import zayavki_pc

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STATE = "zayavki_pc_state.json"
JOURNAL_WRITER = "cowork_log_append.py"
ANSWERED_BY = "Filipp"

# Рубильник ступени. Тот же приём, что у ступеней A/B/D: одно имя, дефолт —
# ВКЛЮЧЕНО, откат в одну переменную без правки кода.
OFF_FLAG = "ZAYAVKI_PC_OFF"


def now_ts(clock=None):
    """Эпоха секундами. Единственная точка, где ступень смотрит на часы."""
    return (clock or (lambda: datetime.datetime.now(datetime.timezone.utc).timestamp()))()


def now_iso(clock=None):
    stamp = (clock or datetime.datetime.now)(datetime.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _path(root, rel):
    return os.path.join(root, *rel.split("/"))


def enabled():
    """Ступень включена? Дефолт — да."""
    return str(os.getenv(OFF_FLAG) or "").strip().lower() not in ("1", "true", "yes", "on")


# ───────────────────────────── реестр отправленного ─────────────────────────────


def state_default():
    return {"schema": zayavki_pc.SCHEMA, "sent": {}}


def read_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return state_default()
    out = state_default()
    if isinstance(data, dict) and isinstance(data.get("sent"), dict):
        out["sent"] = dict(data["sent"])
    return out


def write_state(path, state):
    """Реестр на диск атомарно (tmp + replace): оборванная запись не смеет оставить
    ступень без памяти о том, что уже отправлено, — иначе следующий оборот пришлёт
    владельцу второе сообщение на то же решение."""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ───────────────────────────── очередь ─────────────────────────────


def _daemon():
    """Клиент Моста одалживаем у демона ТЕМ ЖЕ приёмом, что ступень B.

    ``TURBOBABY_TEST_LOGS=1`` нужен ровно на время импорта (иначе демон сорит в
    боевой лог) и обязан быть снят сразу — тот же флаг запрещает `brain_writer`
    живую запись. Зовём чужую функцию, а не переписываем её здесь: урок живёт в
    одном месте.
    """
    import queue_snapshot_pc

    return queue_snapshot_pc._guard_test_logs(queue_snapshot_pc._daemon)


class Queue:
    """Тонкая обёртка очереди: ЧТЕНИЕ ждущих решения + два ответа. Постановки нет."""

    def __init__(self, daemon=None):
        self._d = daemon or _daemon()

    def awaiting(self):
        """Ряды `needs_approval` полосы ПК. → (rows, ok, why).

        Мост не ответил → ``ok=False`` и зовущий НЕ шлёт НИЧЕГО: слепая рассылка
        по прошлому знанию — это карточка на заявку, которую владелец, может
        быть, уже закрыл.
        """
        res = self._d.bc.get_pending("needs_approval")
        if not res.get("ok"):
            return [], False, str(res.get("error") or "мост не ответил")
        rows = [it for it in (res.get("items") or []) if str(it.get("lane") or "pc") == "pc"]
        for row in rows:
            row.setdefault("status", "needs_approval")
        return rows, True, ""

    def accept(self, tid):
        """«Да» → `approve_task`. Задачу это НЕ ставит: гард демона закроет ряд `done`."""
        res = self._d.bc.approve_task(tid, ANSWERED_BY)
        ok = bool(isinstance(res, dict) and res.get("ok"))
        return ok, ("" if ok else str((res or {}).get("error") or "мост не ответил распиской"))

    def reject(self, tid):
        """«Нет» → `complete_task(failed)` с префиксом отказа владельца.

        Именно `failed` с маркером, а не выдуманный статус ``rejected``: замер
        15.08 — `get_pending("rejected")` отдаёт ноль всегда, отказы полосы живут
        в `failed` с этим префиксом, и слепок очереди ищет их именно так.
        """
        prefix = self._d._REJECT_PREFIX
        res = self._d.bc.complete_task(
            tid, "failed",
            "%s: заявка внешнего канала отклонена одним тапом. Задачей она не стала и не станет; "
            "переотправке не подлежит." % prefix)
        ok = bool(isinstance(res, dict) and res.get("ok"))
        return ok, ("" if ok else str((res or {}).get("error") or "мост не ответил распиской"))


# ───────────────────────────── дверь наружу ─────────────────────────────


def outbound(text, markup=None, sender=None):
    """Сообщение владельцу. → (channel, ok, why).

    ДВА ЗАМКА ПЕРЕД ОТПРАВКОЙ, и оба стоя́т ЗДЕСЬ, а не в чистом слое:

    1. страж исходящего (`review_audit.outbound_safe` — тот же, которым живёт
       витрина): увидел что-то — НЕ ШЛЁМ и говорим что именно;
    2. граница чужой цитаты: в тексте не смеет быть строки :data:`zayavki_pc.QUOTE_HEAD`
       — её появление означало бы, что разбор пропустил чужой текст наружу.

    Адрес не называем руками: `dispatch_notify.deliver` сам ставит карточку С
    КНОПКОЙ в инбокс 1160 — тему, которую владелец открывает РАДИ ОТВЕТА (замок
    доктрины адреса, `CLAUDE.md`).
    """
    import review_audit
    import dispatch_notify

    if zayavki_pc.QUOTE_HEAD in str(text or ""):
        return "none", False, "в тексте граница чужой цитаты — разбор пропустил чужие строки, не шлём"
    hits = review_audit.outbound_safe(text)
    if hits:
        return "none", False, "страж исходящего: %s" % "; ".join(str(h) for h in hits[:3])
    send = sender or dispatch_notify.deliver
    channel, ok = send(text, markup)
    return channel, bool(ok), ("" if ok else "канал %s отказал" % channel)


def journal(line, repo=HERE, runner=None):
    """Строка-индекс в журнал штатным писателем. → (код, вывод).

    Кириллица идёт СТДИНОМ: в argv она на этой полосе коверкается (известный
    класс), а у писателя весь argv и так считается текстом записи.
    """
    run = runner or subprocess.run
    try:
        done = run([sys.executable, os.path.join(repo, JOURNAL_WRITER), "-"],
                   input=line.encode("utf-8"), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=240)
    except Exception as exc:                       # журнал НИКОГДА не роняет ступень
        return -1, "журнал не ответил: %s" % exc
    return done.returncode, done.stdout.decode("utf-8", "replace").strip()


# ───────────────────────────── оборот ─────────────────────────────


def tick(root=HERE, state_path=None, send=False, clock=None, queue=None,
         sender=None, journal_fn=None, write_journal=False):
    """Один оборот ступени G. → dict отчёта.

    ``send=False`` — сухой ход: сообщения собраны, наружу не ушло ничего и реестр
    не тронут. Боевой ход отличается ровно двумя действиями: отправка и запись
    реестра.
    """
    state_path = state_path or _path(root, DEFAULT_STATE)
    now = now_ts(clock)
    report = {"acted": False, "why": "", "awaiting": 0, "zayavki": 0, "cards": 0,
              "sent": [], "held": [], "reminded": [], "skipped": [], "failed": [], "line": ""}
    if not enabled():
        report["why"] = "ступень выключена (%s)" % OFF_FLAG
        return report
    rows, ok, why = (queue or Queue()).awaiting() if queue is not False else ([], True, "")
    if not ok:
        report["why"] = "очередь недоступна (%s) — не шлём ничего" % why
        return report
    split = zayavki_pc.split_awaiting(rows)
    report["awaiting"] = len(rows)
    report["zayavki"] = len(split["zayavki"])
    report["cards"] = len(split["cards"])
    items = [zayavki_pc.digest(row) for row in split["zayavki"]]
    state = read_state(state_path)
    steps = zayavki_pc.plan(items, state, now)
    by_key = {d.get("key"): d for d in items}
    remind_now = []
    for step in steps:
        got = by_key.get(step.get("key")) or {}
        act = step.get("action")
        if act == "hold":
            # ПРЯМОЕ УСЛОВИЕ ЗАДАНИЯ: последствие ответа не видно → не отправляем
            # ВОВСЕ, а причину говорим. Молчаливый пропуск здесь читался бы как
            # «заявок не было».
            report["held"].append({"id": step.get("id"), "key": step.get("key"),
                                   "why": step.get("why")})
            continue
        if act == "skip":
            report["skipped"].append({"id": step.get("id"), "why": step.get("why")})
            continue
        if act == "remind":
            remind_now.append(got)
            continue
        text = zayavki_pc.message(got)
        markup = zayavki_pc.buttons(got.get("id"))
        if not send:
            report["sent"].append({"id": got.get("id"), "key": got.get("key"),
                                   "kind": got.get("kind"), "channel": "сухой ход",
                                   "text": text})
            continue
        channel, sent_ok, sent_why = outbound(text, markup, sender=sender)
        if not sent_ok:
            report["failed"].append({"id": got.get("id"), "key": got.get("key"), "why": sent_why})
            continue
        report["sent"].append({"id": got.get("id"), "key": got.get("key"),
                               "kind": got.get("kind"), "channel": channel})
        state.setdefault("sent", {})[got.get("key")] = {
            "first_at": now, "last_at": now, "at_iso": now_iso(), "queue_id": got.get("id"),
            "kind": got.get("kind"), "channel": channel, "reminders": 0,
        }
    if remind_now:
        line = zayavki_pc.reminder(remind_now)
        if not send:
            report["reminded"] = [{"id": d.get("id"), "channel": "сухой ход"} for d in remind_now]
        else:
            channel, sent_ok, sent_why = outbound(line, None, sender=sender)
            if sent_ok:
                for d in remind_now:
                    report["reminded"].append({"id": d.get("id"), "channel": channel})
                    rec = state.setdefault("sent", {}).setdefault(d.get("key"), {})
                    rec["last_at"] = now
                    rec["at_iso"] = now_iso()
                    rec["reminders"] = int(rec.get("reminders") or 0) + 1
            else:
                report["failed"].append({"id": None, "key": "напоминание", "why": sent_why})
    if send:
        write_state(state_path, state)
    report["acted"] = bool(report["sent"] or report["reminded"] or report["failed"])
    report["line"] = zayavki_pc.index_line(report)
    if not report["why"]:
        report["why"] = ("заявок %d: доставлено %d, напомнено %d, придержано %d, молчим о %d"
                         % (report["zayavki"], len(report["sent"]), len(report["reminded"]),
                            len(report["held"]), len(report["skipped"])))
    if send and write_journal and report["line"]:
        (journal_fn or journal)("NOTE " + report["line"], repo=root)
    return report


def answer(tid, yes, queue=None):
    """Ответ владельца на заявку. → (ok, слова для чата).

    Слова возвращаются ГОТОВЫМИ: их печатает `pc_agent` в чат карточки, и второй
    формулировки того же исхода заводить нельзя — иначе «принято» на кнопке и
    «принято» в журнале разойдутся словами при одном действии.
    """
    q = queue or Queue()
    if yes:
        ok, why = q.accept(tid)
        if ok:
            return True, ("✅ Заявка #%s принята к сведению. Задачей она НЕ стала: надо "
                          "исполнять — поставь задачу отдельно, своими словами." % tid)
        return False, "⚠️ Заявка #%s: «да» не прошло — %s" % (tid, why)
    ok, why = q.reject(tid)
    if ok:
        return True, "❌ Заявка #%s отклонена и закрыта. Задач она не породила ни одной." % tid
    return False, "⚠️ Заявка #%s: «нет» не прошло — %s" % (tid, why)


# ───────────────────────────── CLI ─────────────────────────────


def _render(report):
    lines = ["ждут решения всего: %d (заявок %d, карточек гарда %d)"
             % (report.get("awaiting", 0), report.get("zayavki", 0), report.get("cards", 0)),
             "исход: %s" % report.get("why")]
    for row in report.get("sent") or []:
        lines.append("  → доставлена #%s (%s) каналом %s"
                     % (row.get("id"), row.get("kind"), row.get("channel")))
        if row.get("text"):
            lines.append("     " + "\n     ".join(row["text"].splitlines()))
    for row in report.get("reminded") or []:
        lines.append("  ↻ напомнено #%s каналом %s" % (row.get("id"), row.get("channel")))
    for row in report.get("held") or []:
        lines.append("  ✋ НЕ отправлена #%s: %s" % (row.get("id"), row.get("why")))
    for row in report.get("skipped") or []:
        lines.append("  · молчим #%s: %s" % (row.get("id"), row.get("why")))
    for row in report.get("failed") or []:
        lines.append("  ⚠️ не ушло #%s: %s" % (row.get("id"), row.get("why")))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ступень G: заявки доходят до владельца")
    ap.add_argument("--status", action="store_true", help="что видит ступень; не шлём ничего")
    ap.add_argument("--dry", action="store_true", help="сообщения собраны, наружу не идут")
    ap.add_argument("--send", action="store_true", help="боевая доставка владельцу")
    ap.add_argument("--journal", action="store_true", help="строку исхода — в журнал")
    ap.add_argument("--answer", choices=("yes", "no"), help="ответ владельца на заявку")
    ap.add_argument("--id", help="номер ряда для --answer")
    ap.add_argument("--tick", action="store_true", help="оборот демона (= --send --journal)")
    args = ap.parse_args(argv)

    if args.answer:
        if not args.id:
            print("--answer требует --id")
            return 2
        ok, words = answer(args.id, args.answer == "yes")
        print(words)
        return 0 if ok else 1

    send = bool(args.send or args.tick)
    report = tick(send=send, write_journal=bool(args.journal or args.tick))
    print(_render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
