# -*- coding: utf-8 -*-
"""Руки очереди исходящих: реестр на диске, повтор, показ молчания канала.

Разделение то же, что у соседних ступеней: суждения живут в чистом
:mod:`review_outbox_queue` (часов не знает, диска не касается), а здесь — диск,
подпроцессы, тема Аудит и журнал.

ОТКУДА БЕРЁТСЯ ОЧЕРЕДЬ. Не из нового канала данных, а из УЖЕ СУЩЕСТВУЮЩЕГО
лотка ``docs/review_inbox``: отправщик кладёт туда файл на КАЖДЫЙ заход, включая
отказ, и в шапке этого файла стоят пакет, канал, исход и причина. Разбирает
шапки тот же код, которым их читает ступень D (:func:`review_audit_run.answer_headers`) —
второй разборщик означал бы, что очередь и сводка могут разойтись в том, что
считается отказом. Отсюда важное следствие: запись очереди появляется ТОЛЬКО у
захода, который реально был; выдумать несостоявшийся заход этому слою нечем.

БУТСТРАП И ЕГО ИЗВЕСТНАЯ МИНА. Первый же оборот видит в лотке весь накопленный
хвост (замер 01.09: 14 неудачных заходов в Manus за сутки). Показать их разом
значит завалить тему Аудит историей. Поэтому первый оборот ставит
``bootstrap_at`` и помечает всё, что старше него, как ``held`` — в очереди
такие записи ВИДНЫ (``--status``), но в тему не уезжают без ``--force``.
Мина здесь названа ступенью D дословно: её бутстрап оказался ДЕКОРАТИВНЫМ —
реестр писался и никем не читался, а тесты этого не поймали, потому что смотрели
ОДИН оборот, а дефект живёт со второго. Поэтому здесь ``held`` читается в
:func:`announce_due` (ветка ``held``), и регресс гоняет ДВА оборота подряд.

ЧТО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ. Не удаляет ни пакетов, ни файлов лотка. Не ставит
задач и очередь ПК не трогает вовсе — ни чтением, ни записью. Не читает ``.env``:
ключ канала берёт сам отправщик в своём процессе. Не превращает исчерпание в
«готово» ни одной веткой.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import subprocess
import sys

import review_audit_run
import review_intake_run
import review_outbox_queue as Q

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STATE = "review_outbox_queue_state.json"
DEFAULT_INBOX = review_intake_run.DEFAULT_INBOX
SENDER = "review_send_run.py"
JOURNAL_WRITER = "cowork_log_append.py"

# Показов молчания в сутки. Тот же потолок, что у немедленных показов ступени D
# (``review_audit.IMMEDIATE_BUDGET`` = 3), и по той же причине: тема, в которую
# сыплется всё подряд, перестаёт читаться на второй день. Остаток не теряется —
# он назван числом в отчёте и уедет следующими сутками.
ANNOUNCE_BUDGET = 3

# Повторов в сутки на всю полосу. Замер 01.09: заходов в канал 14 за сутки. Если
# бы канал лёг целиком, беспотолочная очередь докупила бы к ним 28 повторов;
# потолок 6 — это две попытки для трёх пакетов, то есть отказ канала переживает
# самое свежее, а не всё подряд.
RETRY_BUDGET = 6

# Бюджет времени на ОДИН повтор, секунды. Шире сокет-таймаута отправщика
# (``review_send_run.DEFAULT_TIMEOUT`` = 900) с запасом на его собственное
# ожидание готовности: снять повтор по нашему таймауту значило бы породить ровно
# тот исход «оплачено и потеряно», ради которого ступень и заведена.
RETRY_TIMEOUT = 2100


# ───────────────────────────── время и пути ─────────────────────────────


def now_utc(clock=None):
    """Момент — ЕДИНСТВЕННОЙ дверью, ради теста. → datetime UTC."""
    return (clock or (lambda: datetime.datetime.now(datetime.timezone.utc)))().astimezone(
        datetime.timezone.utc
    )


def _path(root, rel):
    return os.path.join(root, rel.replace("/", os.sep))


# ───────────────────────────── реестр ─────────────────────────────


def state_default():
    return {"schema": Q.SCHEMA, "bootstrap_at": "", "packs": {}, "announced": {}, "day": {}}


def read_state_why(path):
    """Реестр с диска и НАЗВАННАЯ причина, если прочитать его не удалось.
    → (state, why). Пустой ``why`` = прочитано штатно.

    Битый файл по-прежнему даёт пустой реестр, а не падение: потеря реестра НЕ
    теряет пакетов — они лежат в лотке, и следующий оборот соберёт очередь
    заново, потеряв ровно счётчик попыток и отметку показа.

    ПОЧЕМУ ПРИЧИНА ВОЗВРАЩАЕТСЯ ОТДЕЛЬНЫМ ЗНАЧЕНИЕМ, А НЕ КЛЮЧОМ РЕЕСТРА. Реестр
    целиком уезжает на диск :func:`write_state`; «почему прошлый файл не читался»
    — новость ОБ ОБОРОТЕ, а не состояние очереди, и в файле она превратилась бы в
    вечную запись о давно починенном.

    ОТСУТСТВИЕ ФАЙЛА ПОВРЕЖДЕНИЕМ НЕ СЧИТАЕТСЯ, и это не поблажка. Первый оборот
    на любой полосе видит ровно эту картину, и назвать её бедой значило бы
    объявлять беду каждому новому развёртыванию — шум, в котором утонет
    настоящая. Отличить «файла ещё не было» от «файл унесли» этому слою нечем;
    видимый признак у обоих один и уже есть — ``bootstrap`` в отчёте.
    """
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return state_default(), ""
    except OSError as exc:
        return state_default(), "реестр очереди НЕ ПРОЧИТАН (%s) — счётчик попыток начат заново" % exc
    except ValueError as exc:
        return state_default(), "реестр очереди ПОВРЕЖДЁН (JSON не разобран: %s) — счётчик попыток начат заново" % exc
    if not isinstance(data, dict):
        return state_default(), (
            "реестр очереди ПОВРЕЖДЁН (в файле %s, а не запись очереди) — счётчик попыток начат заново"
            % type(data).__name__
        )
    out = state_default()
    lost = []
    for k in ("packs", "announced", "day"):
        if isinstance(data.get(k), dict):
            out[k] = data[k]
        elif k in data:
            lost.append(k)
    if isinstance(data.get("bootstrap_at"), str):
        out["bootstrap_at"] = data["bootstrap_at"]
    elif "bootstrap_at" in data:
        lost.append("bootstrap_at")
    if lost:
        return out, "реестр очереди ПОВРЕЖДЁН ЧАСТИЧНО (не разобраны разделы: %s)" % ", ".join(sorted(lost))
    return out, ""


def read_state(path):
    """Реестр с диска. → dict. Дверь для тех, кому причина не нужна."""
    return read_state_why(path)[0]


def write_state(state, path):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


# ───────────────────────────── лоток → очередь ─────────────────────────────


def lotok(root=HERE, inbox=DEFAULT_INBOX, files=None):
    """Шапки заходов из лотка. → (list[dict], skipped).

    Тем же разборщиком, что у ступени D: один взгляд на то, что считается
    отказом, на обе ступени.
    """
    return review_audit_run.answer_headers(root=root, inbox=inbox, files=files)


def _file_at(root, rel):
    """Момент захода: mtime файла лотка. → datetime UTC | None.

    День отправки из шапки для очереди грубоват — он не различает два захода
    одних суток, а паузы здесь минутные. Поэтому берём mtime.

    ``None`` (файла на диске уже нет) — ТРЕТИЙ ИСХОД, и подменять его текущим
    временем нельзя. Поймано регрессом ``test_one_retry_per_turn_by_default``:
    с подстановкой «сейчас» КАЖДЫЙ оборот видел заход новее прошлого и тратил
    попытку на пустом месте — за два тика запись доходила до предела, ни разу не
    сходив в канал. Не знаем времени — считаем, что нового захода не было.
    """
    try:
        ts = os.path.getmtime(_path(root, rel))
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


def sync(state, *, root=HERE, inbox=DEFAULT_INBOX, files=None, now=None, headers=None):
    """Лоток → записи очереди. → отчёт dict.

    Ответ ЗАКРЫВАЕТ запись, даже если пришёл позже отказа: канал, ответивший со
    второго захода, не должен остаться в очереди навсегда. Обратное неверно —
    отказ ПОСЛЕ ответа записи не открывает: ответ уже лежит файлом, и второй
    заход за ним не нужен.
    """
    now = now or now_utc()
    heads, skipped = (headers, []) if headers is not None else lotok(root, inbox, files)
    added, closed, advanced = [], [], []
    boot = Q.parse_iso(state.get("bootstrap_at"))
    for h in sorted(heads, key=lambda x: (x.get("send_date") or "", x.get("rel") or "")):
        # ПУТЬ, а не имя: пакет придётся ОТКРЫТЬ ради повтора и НАЗВАТЬ владельцу
        # исполнимой командой. Имя — запасной вариант на старую шапку без пути.
        pack = h.get("pack_path") or h.get("pack") or ""
        channel = h.get("channel") or ""
        if not pack or not channel:
            skipped.append((h.get("rel") or "?", "в шапке нет пакета или канала"))
            continue
        try:
            k = Q.key(pack, channel)
        except Q.ReviewOutboxError as exc:
            skipped.append((h.get("rel") or "?", "ключ не собрался: %s" % exc.reason))
            continue
        rec = state["packs"].get(k)
        seen = _file_at(root, h.get("rel") or "")
        at = seen or now
        if h.get("answered"):
            if rec and rec.get("state") != "closed":
                state["packs"][k] = Q.close(rec, at=at, why="канал ответил файлом %s" % h.get("rel"))
                closed.append(state["packs"][k])
            continue
        if rec is None:
            try:
                rec = Q.new_record(
                    pack=pack, channel=channel, send_date=h.get("send_date") or "",
                    reason=h.get("reason") or "", at=at, answer_rel=h.get("rel"),
                )
            except Q.ReviewOutboxError as exc:
                skipped.append((h.get("rel") or "?", "запись не собралась: %s" % exc.reason))
                continue
            # Историю бутстрапа в тему не показываем — но в очереди её видно.
            if boot is None or at < boot:
                state["announced"][k] = "bootstrap"
            state["packs"][k] = rec
            added.append(rec)
            continue
        if rec.get("state") == "closed":
            continue
        # Тот же файл, что уже учтён, — не новый заход. Признак — имя файла
        # ответа: отправщик пишет его по (пакет, канал, день), и повтор в тот же
        # день перезаписывает его же, поэтому решает не имя, а ВРЕМЯ. Времени
        # нет вовсе — новым заходом не считаем (см. :func:`_file_at`).
        last = Q.parse_iso(rec.get("last_at"))
        if seen is None or (last is not None and seen <= last):
            continue
        state["packs"][k] = Q.advance(rec, reason=h.get("reason") or "", at=at)
        advanced.append(state["packs"][k])
    return {"added": added, "closed": closed, "advanced": advanced, "skipped": skipped}


# ───────────────────────────── повтор ─────────────────────────────


def retry_argv(rec, root=HERE, python=None):
    """Живой формат запуска повтора. → list argv.

    Повтор идёт ТЕМ ЖЕ отправщиком и ТЕМИ ЖЕ ключами, которыми пакет уезжал в
    первый раз, — правило 8 свода среды: проверка (и повтор) повторяют живой
    формат запуска. Свой второй путь отправки означал бы два кода, расходящихся
    в вердиктах.
    """
    py = python or sys.executable
    sender = os.path.join(root, SENDER)
    if rec.get("kind") == "refetch":
        return [py, sender, "--root", root, "--manus-task", str(rec.get("task_id"))]
    return [py, sender, "--root", root, "--pack", _path(root, rec.get("pack") or ""),
            "--channel", str(rec.get("channel"))]


def retry_one(rec, *, root=HERE, runner=None, timeout=RETRY_TIMEOUT):
    """Один повтор. → (ok, reason, detail).

    ``ok`` — канал ответил (код 0). Иначе ``reason`` — причина НОВОГО захода,
    снятая из свежей шапки лотка; её не хватило — берём код возврата отправщика,
    и это тоже названная причина, а не тишина.

    Пакета нет на диске — это НЕ внешний отказ и не повод повторять: причина
    ``pack_missing`` уводит запись в «наше», то есть в исчерпание с именем.
    """
    if rec.get("kind") == "resend":
        p = _path(root, rec.get("pack") or "")
        if not os.path.exists(p):
            return False, "pack_missing", "пакета нет на диске: %s" % rec.get("pack")
    run = runner or subprocess.run
    argv = retry_argv(rec, root)
    try:
        done = run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, cwd=root)
    except Exception as exc:                       # noqa: BLE001 — повтор НИКОГДА не роняет оборот
        return False, "retry_launch_failed", "повтор не запустился: %s" % exc
    out = (done.stdout or b"").decode("utf-8", "replace")
    if done.returncode == 0:
        return True, "ok", out.strip()[-300:]
    return False, "", "код отправщика %d; хвост: %s" % (done.returncode, out.strip()[-300:])


def retry_due(state, *, root=HERE, inbox=DEFAULT_INBOX, now=None, budget=RETRY_BUDGET,
              limit=1, runner=None, do=True):
    """Повторить дозревшие записи. → отчёт dict.

    ``limit`` — сколько повторов за ОДИН оборот; по умолчанию ОДИН. Причина та
    же, что у ступени A: демон исполняет всё синхронно внутри витка, и три
    подряд упёршихся в потолок захода съели бы бюджет тишины О2 (20 минут).

    Результат повтора в реестр кладёт НЕ эта функция, а следующий :func:`sync`,
    читающий свежую шапку лотка: судить свой заход по собственному коду возврата
    значило бы завести второй классификатор рядом с ``review_send``.
    """
    now = now or now_utc()
    day = now.date().isoformat()
    used = int((state.get("day") or {}).get(day, {}).get("retries", 0))
    out = {"retried": [], "skipped": [], "budget_left": max(0, budget - used)}
    if not do:
        for k, rec in sorted(state["packs"].items()):
            ok, why = Q.due(rec, now)
            if ok:
                out["skipped"].append((k, "сухой ход: повтор не делался (%s)" % why))
        return out
    for k, rec in sorted(state["packs"].items()):
        if len(out["retried"]) >= max(0, int(limit)):
            break
        if out["budget_left"] <= 0:
            out["skipped"].append((k, "суточный потолок повторов %d исчерпан" % budget))
            break
        ok_due, why = Q.due(rec, now)
        if not ok_due:
            continue
        ok, reason, detail = retry_one(rec, root=root, runner=runner)
        out["budget_left"] -= 1
        state.setdefault("day", {}).setdefault(day, {})["retries"] = used + len(out["retried"]) + 1
        if ok:
            state["packs"][k] = Q.close(rec, at=now, why="повтор %d удался" % (int(rec.get("attempts") or 0) + 1))
        elif reason:
            state["packs"][k] = Q.advance(rec, reason=reason, at=now)
        else:
            # Причины отправщик не назвал — запись двигаем СВОИМ временем, чтобы
            # попытка не осталась бесплатной, а причину подставит следующий sync
            # из свежей шапки. Инвариант ступени A: обрыв, который не стоит
            # попытки, — это бесконечный повтор.
            state["packs"][k] = Q.advance(rec, reason=rec.get("reason") or "", at=now)
        out["retried"].append({"key": k, "ok": ok, "reason": reason, "detail": detail,
                               "attempt": int(rec.get("attempts") or 0) + 1})
    return out


# ───────────────────────────── показ молчания ─────────────────────────────


def announce_due(state, *, now=None, topic=0, budget=ANNOUNCE_BUDGET, send=False, sender=None,
                 force=False):
    """Исчерпанные записи → сообщения в тему Аудит. → отчёт dict.

    ``held``-ветка ЧИТАЕТ отметку бутстрапа, а не только пишет её: ровно на этом
    ступень D поймала свой декоративный бутстрап. Отметка снимается только
    ``--force``.
    """
    now = now or now_utc()
    day = now.date().isoformat()
    used = int((state.get("day") or {}).get(day, {}).get("announced", 0))
    rep = {"announced": [], "held": [], "failed": [], "texts": [], "budget_left": max(0, budget - used)}
    left = len([r for r in state["packs"].values() if r.get("state") == "queued"])
    for k, rec in sorted(state["packs"].items()):
        if rec.get("state") != "exhausted":
            continue
        mark = (state.get("announced") or {}).get(k)
        if mark == "bootstrap" and not force:
            rep["held"].append((k, "отложена бутстрапом: заход старше первого оборота очереди"))
            continue
        if mark and mark != "bootstrap":
            continue
        if rep["budget_left"] <= 0:
            rep["held"].append((k, "суточный потолок показов %d исчерпан — уедет следующими сутками" % budget))
            continue
        text = Q.audit_text(rec, queued_left=left)
        rep["texts"].append({"key": k, "text": text})
        # Потолок тратится и в СУХОМ ходу. Иначе сухой прогон показывал бы 14
        # сообщений там, где боевой отправит 3, — то есть врал бы ровно о том,
        # ради чего его и зовут («что именно уедет, до того как оно уедет»).
        rep["budget_left"] -= 1
        if not send:
            continue
        ok, mid, why = review_audit_run.send_audit(text, topic, sender=sender)
        state.setdefault("day", {}).setdefault(day, {})["announced"] = (
            used + len(rep["announced"]) + len(rep["failed"]) + 1
        )
        if ok:
            state["announced"][k] = Q.iso(now)
            rep["announced"].append({"key": k, "message_id": mid})
        else:
            rep["failed"].append({"key": k, "why": why})
    return rep


def journal(line, repo=HERE, runner=None):
    """Строка-индекс в журнал штатным писателем. → (код, вывод).

    Кириллица идёт СТДИНОМ (известный класс коверканья argv на этой полосе).
    """
    run = runner or subprocess.run
    try:
        done = run([sys.executable, os.path.join(repo, JOURNAL_WRITER), "-"],
                   input=line.encode("utf-8"), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=240)
    except Exception as exc:                       # noqa: BLE001 — журнал НИКОГДА не роняет оборот
        return -1, "журнал не ответил: %s" % exc
    return done.returncode, done.stdout.decode("utf-8", "replace").strip()


# ───────────────────────────── оборот ─────────────────────────────


def tick(root=HERE, state_path=None, inbox=DEFAULT_INBOX, send=False, clock=None, files=None,
         retry=True, retry_limit=1, budget=ANNOUNCE_BUDGET, retry_budget=RETRY_BUDGET,
         force=False, write_journal=False, daemon=None, sender=None, journal_fn=None,
         runner=None, headers=None):
    """Один оборот очереди исходящих. → dict отчёта.

    Порядок ветвей не переставляется: сначала СВЕРКА с лотком (там лежит правда о
    том, что случилось на самом деле), потом повтор, потом показ. Показ последним
    затем, чтобы удавшийся в этом же обороте повтор не породил сообщения «канал
    лежал» о пакете, который только что доехал.

    ``send=False`` — сухой ход: сообщения СОБРАНЫ и лежат в отчёте дословно,
    наружу не уходит ничего. ``retry=False`` — повторов не делаем вовсе (дозревшие
    названы в отчёте). Реестр в сухом ходу всё равно пишется: счётчик попыток —
    это память о РЕАЛЬНО случившихся заходах, а не о наших намерениях.
    """
    now = now_utc(clock)
    state_path = state_path or os.path.join(root, DEFAULT_STATE)
    state, state_why = read_state_why(state_path)
    first = not state.get("bootstrap_at")

    synced = sync(state, root=root, inbox=inbox, files=files, now=now, headers=headers)
    if first:
        state["bootstrap_at"] = Q.iso(now)

    retried = retry_due(state, root=root, inbox=inbox, now=now, budget=retry_budget,
                        limit=retry_limit, runner=runner, do=bool(retry))
    if retried["retried"]:
        # Повтор мог изменить лоток — пересверяемся, чтобы показ судил свежее.
        synced2 = sync(state, root=root, inbox=inbox, files=files, now=now, headers=headers)
        for k in ("added", "closed", "advanced", "skipped"):
            synced[k] = list(synced[k]) + list(synced2[k])

    topic, topic_why = review_audit_run.audit_topic(daemon=daemon) if send else (0, "сухой ход: адрес не спрашивали")
    announced = announce_due(state, now=now, topic=topic, budget=budget, send=bool(send and topic),
                             sender=sender, force=force)

    exhausted = [r for r in state["packs"].values() if r.get("state") == "exhausted"]
    queued = [r for r in state["packs"].values() if r.get("state") == "queued"]
    write_state(state, state_path)

    report = {
        "schema": Q.SCHEMA,
        "at": Q.iso(now),
        "bootstrap": first,
        "state_path": state_path,
        # Порча реестра обязана быть НАЗВАНА, а не выглядеть первым оборотом:
        # замер 02.09 показал, что битый файл до этой ветки давал ровно ту же
        # картину, что честный бутстрап, и на пустом лотке очередь уходила в ноль
        # без единого слова (`docs/artifacts/2026-09-02-...`).
        "state_why": state_why,
        "topic": topic,
        "topic_why": topic_why,
        "added": synced["added"],
        "advanced": synced["advanced"],
        "closed": synced["closed"],
        "skipped": synced["skipped"],
        "retried": retried["retried"],
        "retry_skipped": retried["skipped"],
        "exhausted": exhausted,
        "queued": len(queued),
        "queued_recs": queued,
        "announced": announced["announced"],
        "announce_texts": announced["texts"],
        "held": announced["held"],
        "announce_failed": announced["failed"],
        "announce_why": topic_why if not topic else "",
    }
    report["line"] = Q.journal_line(report)
    if write_journal and report["line"]:
        report["journal"] = (journal_fn or journal)(report["line"], repo=root)
    return report


def line(report):
    return (report or {}).get("line") or ""


def _render(report):
    out = []
    out.append("ОЧЕРЕДЬ ИСХОДЯЩИХ (ступень F) · %s%s" % (report["at"], " · БУТСТРАП" if report["bootstrap"] else ""))
    out.append("реестр: %s" % report["state_path"])
    if report.get("state_why"):
        out.append("ВНИМАНИЕ: %s" % report["state_why"])
    out.append("тема Аудит: %s%s" % (report["topic"] or "не настроена", (" (%s)" % report["topic_why"]) if report.get("topic_why") else ""))
    out.append("взято в очередь: %d · продвинуто: %d · закрыто ответом: %d · пропущено файлов: %d"
               % (len(report["added"]), len(report["advanced"]), len(report["closed"]), len(report["skipped"])))
    out.append("повторов: %d · в очереди ждут: %d · исчерпано: %d"
               % (len(report["retried"]), report["queued"], len(report["exhausted"])))
    for rec in sorted(report["queued_recs"], key=lambda r: r.get("next_at") or ""):
        out.append("  ЖДЁТ %s · попытка %d/%d · вид %s · следующая %s · причина `%s` (%s)"
                   % (rec["key"], rec["attempts"], Q.MAX_ATTEMPTS, rec["kind"], rec["next_at"],
                      rec["reason"], rec["origin"]))
    for rec in sorted(report["exhausted"], key=lambda r: r.get("key") or ""):
        outcome, title, why = Q.outcome(rec)
        out.append("  ИСЧЕРПАНО %s · %s · %s" % (rec["key"], title, why))
    for item in report["retried"]:
        out.append("  ПОВТОР %s · попытка %d · %s · %s"
                   % (item["key"], item["attempt"], "ответ получен" if item["ok"] else "снова мимо", item["detail"]))
    for key_, why in report["held"]:
        out.append("  ОТЛОЖЕНО %s · %s" % (key_, why))
    for item in report["announced"]:
        out.append("  ПОКАЗАНО %s · message_id=%s" % (item["key"], item["message_id"]))
    for item in report["announce_failed"]:
        out.append("  НЕ ПОКАЗАНО %s · %s" % (item["key"], item["why"]))
    if report["announce_texts"] and not report["announced"]:
        for item in report["announce_texts"]:
            out.append("--- СООБЩЕНИЕ %s (наружу НЕ ушло) ---" % item["key"])
            out.append(item["text"])
    out.append("ИНДЕКС: %s" % (report["line"] or "— (оборот без новостей)"))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="Очередь исходящих ревью-контура: повтор и показ молчания канала.")
    p.add_argument("--root", default=HERE)
    p.add_argument("--inbox", default=DEFAULT_INBOX)
    p.add_argument("--state", default=None)
    p.add_argument("--send", action="store_true", help="отправлять сообщения в тему Аудит")
    p.add_argument("--dry", action="store_true", help="сухой ход (то же, что без --send)")
    p.add_argument("--no-retry", action="store_true", help="повторов НЕ делать, дозревшие только назвать")
    p.add_argument("--retry-limit", type=int, default=1, help="повторов за один оборот")
    p.add_argument("--budget", type=int, default=ANNOUNCE_BUDGET, help="показов в тему Аудит за сутки")
    p.add_argument("--retry-budget", type=int, default=RETRY_BUDGET, help="повторов за сутки")
    p.add_argument("--force", action="store_true", help="показать и то, что отложено бутстрапом")
    p.add_argument("--journal", action="store_true", help="дописать строку-индекс в журнал")
    p.add_argument("--status", action="store_true", help="только показать очередь, ничего не делать")
    a = p.parse_args(argv)

    report = tick(
        root=a.root, state_path=a.state, inbox=a.inbox,
        send=bool(a.send and not a.dry and not a.status),
        retry=not (a.no_retry or a.status),
        retry_limit=a.retry_limit, budget=a.budget, retry_budget=a.retry_budget,
        force=a.force, write_journal=bool(a.journal and not a.status),
    )
    sys.stdout.write(_render(report) + "\n")
    if report["announce_failed"]:
        return 3
    if report["exhausted"]:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
