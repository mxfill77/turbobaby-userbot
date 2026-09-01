"""Руки ступени D ревью-контура: лоток и очередь → сообщения в тему Аудит.

Разделение то же, что у ступеней 1–2, A и B, и по той же причине: решение живёт
в чистом модуле (:mod:`review_audit`) и проверяется без диска и сети, а здесь —
только ввод-вывод: чтение лотка, чтение очереди, отправка в тему, реестр,
журнал.

ТРИ ВЕЩИ, КОТОРЫЕ ЗДЕСЬ ВАЖНЕЕ ОСТАЛЬНЫХ:

1. **Адрес НЕ ЗАШИТ ЧИСЛОМ и фолбэка не имеет.** Номер темы Аудит приезжает
   ручкой демона ``AUDIT_TOPIC`` (env ``PC_AUDIT_TOPIC``) — той же формы, что
   соседние ``PC_NA_TOPIC``/``PC_INBOX_TOPIC``/``PC_RAISE_TOPIC``. Не настроена →
   контур НЕ ШЛЁТ НИЧЕГО и говорит об этом словом. Каскад ``send_topic``
   (тема → инбокс 1160 → личка) здесь ЗАПРЕЩЁН: чужое мнение, севшее в тему
   ответа владельца или в тему постановки задач, читается как задание. Дверь без
   каскада — ``dispatch_notify.send_topic_strict``.
2. **Секреты берёт САМ импортируемый модуль.** Токен бота живёт в
   ``dispatch_notify``, клиент моста — в демоне; здесь их не читают и не видят
   (запрет класса 328). Демон одалживается тем же приёмом, что у ступени B, —
   через ``queue_snapshot_pc._guard_test_logs``, чтобы не повесить хендлер на
   боевой лог и не оставить флаг стоять.
3. **Показ — единственное действие ступени.** Ни задач, ни правок, ни очереди на
   запись: очередь читается ТОЛЬКО ради числа заявок в сводке. Ступень D
   операционного состояния не меняет ни одной веткой.

ЗАПУСК РУКАМИ::

    venv/Scripts/python.exe review_audit_run.py --status                  # что видит контур
    venv/Scripts/python.exe review_audit_run.py --dry                     # что ушло бы, дословно
    venv/Scripts/python.exe review_audit_run.py --digest --day 2026-09-01 --dry
    venv/Scripts/python.exe review_audit_run.py --digest --day 2026-09-01 --send --journal
    venv/Scripts/python.exe review_audit_run.py --tick                    # оборот демона
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import subprocess
import sys

import review_audit
import review_intake
import review_intake_run

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STATE = "review_audit_state.json"
DEFAULT_INBOX = review_intake_run.DEFAULT_INBOX
JOURNAL_WRITER = "cowork_log_append.py"

# Час UTC, раньше которого суточная сводка не уходит. Тот же, что у дайджеста
# ступени A (01:00 UTC = 08:00 по Пхукету): сутки UTC кончаются в 07:00 по
# Пхукету, и сводка приходит через час после их конца — то есть про ЗАКОНЧЕННЫЙ
# день, а не про идущий.
DIGEST_HOUR = 1


def now_iso(clock=None):
    """Момент времени ISO-8601 в UTC. Часы — ЕДИНСТВЕННОЙ дверью, ради теста."""
    now = (clock or (lambda: datetime.datetime.now(datetime.timezone.utc)))()
    return now.astimezone(datetime.timezone.utc).isoformat()


def _path(root, rel):
    return os.path.join(root, rel.replace("/", os.sep))


# ───────────────────────────── реестр ─────────────────────────────


def state_default():
    return {"schema": review_audit.SCHEMA, "bootstrap_at": "", "shown": {}, "held": {},
            "digests": {}}


def read_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return state_default()
    if not isinstance(data, dict):
        return state_default()
    out = state_default()
    for key in ("shown", "held", "digests"):
        if isinstance(data.get(key), dict):
            out[key] = data[key]
    if isinstance(data.get("bootstrap_at"), str):
        out["bootstrap_at"] = data["bootstrap_at"]
    return out


def write_state(state, path):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


# ───────────────────────────── лоток: шапки файлов ─────────────────────────────


def answer_headers(root=HERE, inbox=DEFAULT_INBOX, files=None):
    """Файлы лотка → ШАПКИ заходов в каналы. → (list[dict], skipped[(rel, why)]).

    Считать сводку по находкам нельзя: отказ канала находок не несёт вовсе, а
    один ответ несёт три. Числа «пакетов ушло» и «ответов получено» — про ЗАХОДЫ,
    и отвечает на них файл целиком, а не его содержимое.

    Файл, ответом не являющийся (заметки прошлых ступеней), пропускается с
    НАЗВАННОЙ причиной: молчаливый пропуск сделал бы «заходов не было»
    неотличимым от «разбор сломался».
    """
    out, skipped = [], []
    for rel in (files if files is not None else review_intake_run.answer_files(root, inbox)):
        try:
            with io.open(_path(root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            skipped.append((rel, "файл не прочитан: %s" % exc))
            continue
        try:
            header = review_intake.parse_answer(text)
        except review_intake.ReviewIntakeError as exc:
            skipped.append((rel, "не файл ответа (%s)" % exc.reason))
            continue
        out.append({"rel": rel, "pack": header.get("pack") or "",
                    "channel": header.get("channel") or "",
                    "send_date": header.get("send_date") or "",
                    "outcome": header.get("outcome") or "",
                    "reason": header.get("reason") or "",
                    "answered": bool(header.get("ok"))})
    return out, skipped


# ───────────────────────────── очередь: только ЧТЕНИЕ ─────────────────────────────


def placed_claims(day, daemon=None):
    """Сколько заявок ступени B стоит в очереди за этот день. → (count|None, ids, why).

    ``None`` — мост не ответил, и это ТРЕТИЙ ИСХОД, а не ноль: ноль означал бы
    «заявок не заводили», то есть сводка соврала бы о работе соседней ступени.
    Та же доктрина, что в слое ожиданий.

    Очередь здесь ТОЛЬКО ЧИТАЕТСЯ. Ступень D не ставит рядов ни одной веткой.
    """
    try:
        d = daemon or review_intake_run._daemon()
    except Exception as exc:                       # noqa: BLE001 — импорт демона не роняет показ
        return None, [], "демон не одолжился: %s" % exc
    ids = []
    for status in ("needs_approval", "new"):
        try:
            res = d.bc.get_pending(status)
        except Exception as exc:                   # noqa: BLE001
            return None, [], "очередь не ответила: %s" % exc
        if not res.get("ok"):
            return None, [], str(res.get("error") or "мост не ответил")
        for row in (res.get("items") or []):
            if str(row.get("lane") or "pc") != "pc":
                continue
            text = str(row.get("task_text") or "")
            if not review_intake.is_claim(text):
                continue
            for stamp, _key in review_intake.claim_markers([row]):
                if stamp == day and row.get("id") not in ids:
                    ids.append(row.get("id"))
    return len(ids), ids, ""


def queue_ids_by_key(daemon=None):
    """Ключ заявки → номер ряда очереди. → (dict, why).

    Нужен ровно для одной строки сообщения — «заявка очереди #NN». Без неё
    владелец видит находку, но не может найти ряд, по которому решает. Мост
    молчит → пустой словарь и НАЗВАННАЯ причина: строка просто не появится, а
    показ не сорвётся (адрес полного текста в сообщении есть и без неё).
    """
    try:
        d = daemon or review_intake_run._daemon()
    except Exception as exc:                       # noqa: BLE001
        return {}, "демон не одолжился: %s" % exc
    out = {}
    for status in ("needs_approval", "new"):
        try:
            res = d.bc.get_pending(status)
        except Exception as exc:                   # noqa: BLE001
            return out, "очередь не ответила: %s" % exc
        if not res.get("ok"):
            return out, str(res.get("error") or "мост не ответил")
        for row in (res.get("items") or []):
            if str(row.get("lane") or "pc") != "pc":
                continue
            for _stamp, key in review_intake.claim_markers([row]):
                out.setdefault(key, row.get("id"))
    return out, ""


# ───────────────────────────── адрес и отправка ─────────────────────────────


def audit_topic(daemon=None):
    """Номер темы Аудит — ИЗ ЖИВОЙ НАСТРОЙКИ ДЕМОНА. → (int, why).

    Ручка живёт там же, где соседние темы полосы (``pc_orchestrator.AUDIT_TOPIC``,
    env ``PC_AUDIT_TOPIC``), и читает её САМ демон. Здесь ни ``.env``, ни
    ``getenv``: адрес приезжает готовым числом, как и клиент моста.

    Ноль — законный исход «тема не настроена», а не ошибка. Дефолтного номера у
    ручки нет СОЗНАТЕЛЬНО: подставить сюда любое соседнее число значило бы, что
    ненастроенный контур молча пишет находки внешнего канала в чужую тему.
    """
    try:
        d = daemon or review_intake_run._daemon()
    except Exception as exc:                       # noqa: BLE001
        return 0, "демон не одолжился: %s" % exc
    tid = int(getattr(d, "AUDIT_TOPIC", 0) or 0)
    if not tid:
        return 0, ("тема Аудит НЕ НАСТРОЕНА: ручка PC_AUDIT_TOPIC пуста — "
                   "показ выключен целиком, фолбэка в другие темы нет по построению")
    return tid, ""


def send_audit(text, topic, sender=None):
    """Сообщение в тему Аудит. → (ok, message_id|'', why).

    Дверь одна и без каскада (:func:`dispatch_notify.send_topic_strict`): чужой
    адрес здесь хуже молчания. Токен берёт сам ``dispatch_notify``.

    Перед отправкой — ВТОРОЙ слой стражи исходящего поверх построчного: если она
    видит что-то и после починки цитаты, не отправляем ВОВСЕ и называем вид.
    Молчание с названной причиной дешевле утечки.
    """
    hits = review_audit.outbound_safe(text)
    if hits:
        kinds = ", ".join(sorted({h["kind"] for h in hits}))
        return False, "", "страж исходящего задержал сообщение целиком (%s)" % kinds
    if sender is None:
        import dispatch_notify

        sender = dispatch_notify.send_topic_strict
    _channel, ok, why = sender(text, topic)
    return bool(ok), (why if ok else ""), ("" if ok else why)


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
    except Exception as exc:                       # журнал НИКОГДА не роняет показ
        return -1, "журнал не ответил: %s" % exc
    return done.returncode, done.stdout.decode("utf-8", "replace").strip()


# ───────────────────────────── сборка ─────────────────────────────


def build(root=HERE, inbox=DEFAULT_INBOX, files=None, tree=None, tree_why=""):
    """Лоток → заявки с премисой + шапки заходов. → dict.

    Заявки строит ступень B ТЕМ ЖЕ кодом, которым ставит их в очередь
    (:func:`review_intake_run.build`): второй разбор ответа означал бы, что тема
    Аудит и очередь могут разойтись в том, что вообще считается находкой.

    ``files``/``tree`` пробрасываются насквозь: регрессу нужен корпус из двух
    названных файлов, а не проход по всему дереву — иначе тест платит секунды за
    то, что проверяет не он.
    """
    built = review_intake_run.build(root, inbox, files=files, tree=tree, tree_why=tree_why)
    headers, skipped = answer_headers(root, inbox, files=files)
    built["headers"] = headers
    built["header_skipped"] = skipped
    return built


# ───────────────────────────── оборот ─────────────────────────────


def tick(root=HERE, state_path=None, inbox=DEFAULT_INBOX, send=False, clock=None,
         limit=None, budget=review_audit.IMMEDIATE_BUDGET, force=False, day=None,
         digest=None, write_journal=False, daemon=None, sender=None, journal_fn=None,
         digest_hour=DIGEST_HOUR, built=None):
    """Один оборот ступени D. → dict отчёта.

    ``send=False`` — сухой ход: сообщения СОБРАНЫ и показаны в отчёте дословно,
    наружу не уходит ничего и реестр не меняется. Ровно так проверяется, ЧТО
    именно уедет, до того как оно уедет.

    БУТСТРАП. Первый оборот backlog НЕ показывает — тот же приём и та же причина,
    что у ступени B и у ревизора: на момент рождения ступени в лотке лежат ответы,
    накопленные до неё (замер 01.09 — 32 заявки, из них высоких 10), и вывалить их
    пачкой в тему Аудит значило бы открыть её потоком в первую же минуту. Ключи
    при этом ложатся в реестр СЛОВОМ «не показывали» — остаток назван, а не
    потерян; ручной заход берёт их по ``--force``.
    """
    state_path = state_path or _path(root, DEFAULT_STATE)
    stamp = now_iso(clock)
    today = review_intake.today_utc(stamp)
    hour = int(stamp[11:13]) if len(stamp) >= 13 else 0
    state = read_state(state_path)
    bootstrap = not state.get("bootstrap_at")
    report = {"today": today, "at": stamp, "bootstrap": bootstrap, "sent": [], "failed": [],
              "held": [], "digest": None, "why": "", "topic": 0, "topic_why": "",
              "acted": False, "lines": []}

    built = build(root, inbox) if built is None else built
    items = built["claims"]
    report["built"] = len(items)
    report["high"] = sum(1 for i in items if review_audit.is_high(i))

    topic, topic_why = (0, "сухой ход: адрес не спрашиваем") if not send else audit_topic(daemon)
    report["topic"], report["topic_why"] = topic, topic_why
    if send and not topic:
        report["why"] = topic_why
        return report

    # ─── немедленный показ ───
    shown = review_audit.shown_keys(state.get("shown"))
    left = max(0, int(budget) - review_audit.shown_count(state.get("shown"), today))
    if bootstrap and not force:
        take, held = [], [(i, "бутстрап: лоток старше ступени D — не показывали") for i in items]
    else:
        take, held = review_audit.select_immediate(
            items, posted=set() if force else shown,
            budget=len(items) if force else left, limit=limit)
    ids_by_key, _ids_why = ({}, "") if not take else queue_ids_by_key(daemon)
    for item in take:
        claim = item["claim"]
        text = review_audit.finding_message(item, today, queue_id=ids_by_key.get(claim["key"]))
        report["lines"].append(text)
        if not send:
            continue
        ok, mid, why = send_audit(text, topic, sender)
        if not ok:
            report["failed"].append({"key": claim["key"], "why": why})
            continue
        report["sent"].append({"key": claim["key"], "kind": claim["kind"], "message_id": mid,
                               "score": review_audit.weight(claim, item["premise"])["score"]})
        state.setdefault("shown", {})[claim["key"]] = {
            "day": today, "at": stamp, "message_id": mid, "kind": claim["kind"],
            "score": review_audit.weight(claim, item["premise"])["score"],
            "keys": review_intake.claim_keys(claim)}
        if write_journal:
            (journal_fn or journal)(review_audit.index_line(item), repo=root)
    report["held"] = [((i.get("claim") or {}).get("key"), why) for i, why in held]

    # ─── суточная сводка ───
    want_digest = digest
    digest_day = day or review_audit.previous_day(today)
    if want_digest is None:
        want_digest = (hour >= digest_hour and not review_audit.digest_done(state.get("digests"),
                                                                           digest_day))
    if want_digest:
        stats = review_audit.day_stats(built["headers"], items, digest_day)
        placed, placed_ids, placed_why = ((None, [], "сухой ход: очередь не спрашиваем")
                                          if not send else placed_claims(digest_day, daemon))
        shown_today = sum(1 for row in (state.get("shown") or {}).values()
                          if isinstance(row, dict) and row.get("day") == digest_day)
        note = ""
        if stats["high"] > shown_today:
            note = ("Высоких находок больше, чем показано немедленно (%d против %d): остальные "
                    "названы этой сводкой и лежат файлами в лотке — потерянных нет."
                    % (stats["high"], shown_today))
        text = review_audit.digest_message(stats, digest_day, placed=placed,
                                           placed_ids=placed_ids, shown_today=shown_today,
                                           note=note)
        report["lines"].append(text)
        entry = {"day": digest_day, "stats": {k: v for k, v in stats.items() if k != "items"},
                 "placed": placed, "placed_why": placed_why, "sent": False, "message_id": ""}
        if send:
            ok, mid, why = send_audit(text, topic, sender)
            entry["sent"], entry["message_id"], entry["why"] = ok, mid, why
            if ok:
                state.setdefault("digests", {})[digest_day] = {"at": stamp, "message_id": mid}
                if write_journal:
                    (journal_fn or journal)(
                        review_audit.digest_index_line(stats, digest_day, placed), repo=root)
            else:
                report["failed"].append({"key": "сводка %s" % digest_day, "why": why})
        report["digest"] = entry

    if send:
        if bootstrap:
            for item in items:
                keys = review_intake.claim_keys(item["claim"])
                if set(keys) & review_audit.shown_keys(state.get("shown")):
                    continue
                state.setdefault("held", {})[item["claim"]["key"]] = {
                    "seen_at": stamp, "keys": keys,
                    "why": "бутстрап: ответ лежал в лотке до рождения ступени D — не показывали"}
            state["bootstrap_at"] = stamp
        write_state(state, state_path)
    report["acted"] = bool(report["sent"] or (report["digest"] or {}).get("sent"))
    if not report["why"]:
        if not send:
            report["why"] = ("сухой ход: собрано %d сообщений, наружу не ушло ничего"
                             % len(report["lines"]))
        else:
            report["why"] = ("показано %d, сводка %s, не ушло %d"
                             % (len(report["sent"]),
                                "ушла" if (report["digest"] or {}).get("sent") else "не слалась",
                                len(report["failed"])))
    return report


def line(report):
    """Строка исхода оборота для журнала/лога. → str (одна строка, '' — молчание)."""
    sent, dig = report.get("sent") or [], report.get("digest") or {}
    if not sent and not dig.get("sent") and not report.get("failed"):
        return ""
    parts = ["ступень D: в тему Аудит показано находок %d" % len(sent)]
    for row in sent:
        parts.append("ключ=%s (%s, вес %s, msg %s)"
                     % (row["key"], row["kind"], row["score"], row["message_id"] or "—"))
    if dig.get("sent"):
        st = dig.get("stats") or {}
        parts.append("сводка %s (пакетов %s, ответов %s, находок %s, высоких %s)"
                     % (dig.get("day"), st.get("packs"), st.get("answered"),
                        st.get("findings"), st.get("high")))
    if report.get("failed"):
        parts.append("не ушло %d" % len(report["failed"]))
    return "; ".join(parts)


# ───────────────────────────── CLI ─────────────────────────────


def _render(report):
    out = ["день: %s (замер %s)" % (report.get("today"), report.get("at")),
           "заявок в лотке: %s, из них высокой важности: %s"
           % (report.get("built"), report.get("high")),
           "тема Аудит: %s%s" % (report.get("topic") or "не названа",
                                 (" — %s" % report["topic_why"]) if report.get("topic_why") else ""),
           "исход: %s" % (report.get("why") or "—")]
    for i, text in enumerate(report.get("lines") or [], 1):
        out.append("")
        out.append("──────── сообщение %d (дословно) ────────" % i)
        out.append(text)
    if report.get("sent") or report.get("failed"):
        out.append("")
        for row in report.get("sent") or []:
            out.append("  УШЛО ключ=%s message_id=%s" % (row["key"], row["message_id"]))
        for row in report.get("failed") or []:
            out.append("  НЕ УШЛО %s: %s" % (row["key"], row["why"]))
    held = report.get("held") or []
    if held:
        out.append("")
        out.append("отложено: %d" % len(held))
        seen = {}
        for _key, why in held:
            seen[why] = seen.get(why, 0) + 1
        for why, n in sorted(seen.items(), key=lambda kv: -kv[1]):
            out.append("  — %s: %d" % (why, n))
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ступень D ревью-контура: находки внешних каналов → тема Аудит.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="что видит контур; ничего не шлём")
    mode.add_argument("--dry", action="store_true", help="собрать сообщения и показать дословно")
    mode.add_argument("--send", action="store_true", help="боевая отправка в тему Аудит")
    mode.add_argument("--tick", action="store_true", help="оборот демона")
    parser.add_argument("--digest", action="store_true", help="принудительно собрать суточную сводку")
    parser.add_argument("--day", default=None, help="день сводки YYYY-MM-DD (по умолчанию вчера)")
    parser.add_argument("--limit", type=int, default=None, help="потолок находок за заход")
    parser.add_argument("--budget", type=int, default=review_audit.IMMEDIATE_BUDGET,
                        help="потолок немедленных показов в сутки")
    parser.add_argument("--force", action="store_true", help="взять ОТЛОЖЕННОЕ (бутстрап/бюджет)")
    parser.add_argument("--journal", action="store_true", help="писать строки-индексы в журнал")
    parser.add_argument("--json", action="store_true", help="отчёт машиночитаемо")
    args = parser.parse_args(argv)

    send = bool(args.send or args.tick)
    report = tick(HERE, send=send, limit=args.limit, budget=args.budget, force=args.force,
                  day=args.day, digest=(True if args.digest else None),
                  write_journal=args.journal)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json
          else _render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
