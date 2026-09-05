"""Руки ступени A ревью-контура: повод → пакет → канал → лоток → журнал.

Разделение то же, что у ступеней 1–2 и у слоя ожиданий: всё, что РЕШАЕТ, живёт
чистым :mod:`review_auto`; здесь только ввод-вывод — диск, git, каналы, журнал.

    venv/Scripts/python.exe review_auto_run.py --status     # что решил бы контур, ничего не делая
    venv/Scripts/python.exe review_auto_run.py --dry        # собрать пакет, наружу НЕ ходить
    venv/Scripts/python.exe review_auto_run.py --tick       # боевой оборот (тот же вызов, что у демона)
    venv/Scripts/python.exe review_auto_run.py --from-queue # добрать закрытые цепочки из живой очереди

ГДЕ ЭТОТ КОД ЖИВЁТ В БОЮ. Демон зовёт :func:`tick` ИЗ ВИТКА — тем же устройством,
каким зовёт ревизора: флаг окружения + метка на диске + один оборот на тик.
Отсюда цена, названная вслух: заход в канал СИНХРОНЕН, и виток демона стои́т
ровно столько, сколько отвечает канал (потолок — ``timeout``, по умолчанию 300с,
как у ревизора окна). Асинхронная отправка здесь была бы честнее по времени, но
нечестна по учёту: исход захода надо записать в то же состояние, из которого он
начат, иначе «повтор не чаще одного раза» перестаёт держаться при рестарте.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ: не читает ответы каналов как команды, не ставит
задач, не правит код, не удаляет пакетов. Отказ канала пакет не выбрасывает —
пакет остаётся в ``docs/review_outbox`` и назван в строке отказа адресом.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

import review_auto
import review_pack
import review_pack_build
import review_send
import review_send_run

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STATE = "review_auto_state.json"
DEFAULT_RECEIPTS = "docs/review_receipts"
DEFAULT_OUTBOX = "docs/review_outbox"
DEFAULT_INBOX = "docs/review_inbox"
DEFAULT_WORKDIR = "tmp/review_auto"
JOURNAL_WRITER = "cowork_log_append.py"

DEFAULT_DIGEST_HOUR = 1        # 01:00 UTC = 08:00 по Пхукету: сутки полосы уже кончились
DEFAULT_TIMEOUT = 300          # столько же, сколько думателю-ревизору на одно окно
GIT_TIMEOUT = 20

_RE_SHA = re.compile(r"^[0-9a-f]{7,40}$")
# Артефакт задачи ищем по ФОРМЕ пути, а не по слову «артефакт»: отчёты пишут о
# нём по-разному, а путь в дереве один.
_RE_ARTIFACT = re.compile(r"docs/artifacts/[0-9A-Za-zА-Яа-яЁё._\-]+\.(?:md|json)")


def now_iso(clock=None):
    """Текущее время UTC в ISO. Единственная точка, где контур смотрит на часы."""
    stamp = (clock or datetime.datetime.now)(datetime.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


# ───────────────────────────── диск ─────────────────────────────


def _path(root, rel):
    return os.path.join(root, *rel.split("/"))


def read_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return review_auto.state_read(json.load(fh))
    except (OSError, ValueError):
        return review_auto.state_default()


def write_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def journal(line, repo=HERE, runner=None):
    """Строка-индекс в журнал штатным писателем. → (код, вывод).

    Кириллица идёт СТДИНОМ: в argv она на этой полосе коверкается (известный
    класс), а у писателя весь argv и так считается текстом записи.
    """
    run = runner or subprocess.run
    try:
        done = run(
            [sys.executable, os.path.join(repo, JOURNAL_WRITER), "-"],
            input=line.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=240,
        )
    except Exception as exc:                      # журнал НИКОГДА не роняет контур
        return -1, "журнал не ответил: %s" % exc
    return done.returncode, done.stdout.decode("utf-8", "replace").strip()


# ───────────────────────────── git ─────────────────────────────


def verify_commits(claimed, root=HERE, runner=None):
    """Какие из объявленных коммитов ЖИВЫ в дереве. → list[str].

    В git уходит только то, что прошло ``_RE_SHA``: хеш приезжает из свободного
    текста отчёта, и подавать такое аргументом команде без проверки формы нельзя.
    """
    run = runner or subprocess.run
    out = []
    for sha in claimed or []:
        if not _RE_SHA.match(str(sha or "")):
            continue
        try:
            done = run(
                ["git", "cat-file", "-e", "%s^{commit}" % sha],
                cwd=root, capture_output=True, timeout=GIT_TIMEOUT,
            )
        except Exception:
            continue                              # git недоступен → коммит НЕ подтверждён (не «подтверждён»)
        if done.returncode == 0:
            out.append(sha)
    return out


# ───────────────────────────── расписка закрытой цепочки ─────────────────────


def note_closed(queue_id, task_text, status, result, *, root=HERE, state_path=None,
                clock=None, runner=None, stamp=None):
    """Закрытая задача очереди → расписка на диске + запись в спул. → dict | None.

    Зовётся ДЕМОНОМ в момент закрытия задачи: он один знает исход в тот же миг,
    и читать ради этого `get_pending("done")` (26.8с на 120 строк — замер 14.08)
    каждый оборот было бы платой без новости. ``None`` — задача расписки не
    заслуживает (нераспознанный статус); повод из неё не родится.
    """
    if status not in review_auto.REPORTED:
        return None
    claimed = review_auto.claimed_commits(result)
    verified = verify_commits(claimed, root=root, runner=runner)
    artifacts = sorted(set(_RE_ARTIFACT.findall(str(result or ""))))
    artifacts = [a for a in artifacts if os.path.isfile(_path(root, a))]
    rec = review_auto.receipt(
        queue_id=queue_id,
        task_text=task_text,
        status=status,
        result=result,
        closed_at=stamp or now_iso(clock),
        claimed=claimed,
        verified=verified,
        artifacts=artifacts,
    )
    write_text(_path(root, review_auto.receipt_rel(rec)),
               json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")
    path = state_path or _path(root, DEFAULT_STATE)
    state = read_state(path)
    state["spool"] = review_auto.spool_add(state.get("spool"), rec)
    write_state(path, state)
    return rec


# ───────────────────────────── один оборот ─────────────────────────────


DEFAULT_PROBE_WAIT = 600       # предел ожидания у ПРОБЫ лежачего канала, с


class _ChannelArgs(object):
    """Ровно те поля, которые читает :func:`review_send_run.run_channel`.

    ПОЛЕ ``manus_wait`` ЗАВЕДЕНО 05.09.2026, и это не украшение. До него
    ``run_channel`` брал предел ожидания через ``getattr(args, "manus_wait",
    DEFAULT_MANUS_WAIT)`` — атрибута тут не было, поэтому боевой автоконтур ЖДАЛ
    1800с и ручки к этому числу не имел ни одной (ни `.env`, ни аргумента). Теперь
    число приезжает сюда явно: боевой заход — прежние 1800с (поведение живого
    канала байт-в-байт), проба лежачего — короткое окно.
    """

    def __init__(self, timeout, key_env, codex_bin=None, codex_model=None, codex_cd=None,
                 manus_base=None, manus_wait=None):
        self.min_chars = review_send.ANSWER_MIN_CHARS
        self.timeout = timeout
        self.key_env = key_env
        self.codex_bin = codex_bin
        self.codex_model = codex_model
        self.codex_cd = codex_cd
        self.manus_base = manus_base or review_send_run.DEFAULT_MANUS_BASE
        self.manus_path = review_send_run.DEFAULT_MANUS_PATH
        self.manus_wait = review_send_run.DEFAULT_MANUS_WAIT if manus_wait is None else int(manus_wait)


def line_counts(root, paths):
    """Сколько строк в каждом файле. → {путь: n}. Нет файла → его нет и в словаре.

    Границы строк в манифесте — ФАКТ с диска, а не догадка: сборщик контекста
    сверяет их с файлом и падает на выходе за конец. Считать их обязаны руки —
    у чистого модуля диска нет.
    """
    out = {}
    for rel in paths:
        full = _path(root, rel)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                out[rel] = sum(1 for _ in fh) or 1
        except OSError:
            continue
    return out


def plan_artifacts(root, paths, max_lines):
    """Артефакты → (планы выборки, задержанные стражей). Руки: читают диск, решает чистый модуль.

    Что именно ЕДЕТ, знает только план: с 05.09 артефакт приходит выборкой разделов
    по весу, а не первыми N строками. Поэтому и страже показывается ИМЕННО ВЫБРАННЫЙ
    ТЕКСТ — экран головы после смены порядка проверял бы не то, что уезжает наружу.
    """
    plans, held = {}, []
    for rel in paths:
        try:
            with open(_path(root, rel), "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue                                   # нет файла — сборщик назовёт причину сам
        plan = review_auto.plan_artifact_excerpt(text, max_lines=max_lines)
        lines = text.splitlines(keepends=True)
        excerpt = "".join("".join(lines[start - 1 : end]) for start, end in plan["ranges"])
        found = review_send.outbound_violations(excerpt)
        if found:
            held.append({"path": rel, "kinds": sorted({v["kind"] for v in found})})
        else:
            plans[rel] = plan
    return plans, held


def screen_artifacts(root, paths, head_lines=review_auto.ARTIFACT_HEAD_LINES):
    """Отсеять артефакты, чья ГОЛОВА спотыкается о стражу исходящего. → (принятые, задержанные).

    ЗАЧЕМ, и это найдено ЖИВЬЁМ на первом же боевом обороте 01.09. Постановку контур
    санитайзит сам, расписку тоже — а артефакт репозитория читается ДОСЛОВНО, и в нём
    абсолютных путей полно (первый же повод приложил артефакт ПРО абсолютные корни в
    тестах). Итог: страж честно задержал пакет целиком, оба канала получили
    ``outbound_guard``, и так было бы КАЖДЫЙ раз — автоматический контур, который всегда
    задержан, бесполезен.

    Санитайзить артефакт нельзя: пакет печатает sha256 файла-источника, а санитайзенный
    текст этому хешу больше не соответствует — доказательство превратилось бы в подделку.
    Поэтому спотыкающийся артефакт НЕ ЕДЕТ, и его имя названо в сводке пакета.
    """
    kept, held = [], []
    for rel in paths:
        try:
            with open(_path(root, rel), "r", encoding="utf-8") as fh:
                head = "".join([line for _, line in zip(range(head_lines), fh)])
        except OSError:
            continue                                   # нет файла — сборщик назовёт причину сам
        found = review_send.outbound_violations(head)
        if found:
            held.append({"path": rel, "kinds": sorted({v["kind"] for v in found})})
        else:
            kept.append(rel)
    return kept, held


def _fit_digest(trigger, build_date, counts, root, max_chars, frame):
    """Дайджест с ИЗМЕРЕННЫМ числом приложенных расписок. → (case, pack).

    Фиксированное число здесь не работает, и это ЗАМЕР, а не опасение: первый живой
    дайджест 01.09 с четырьмя расписками занял 16046 знаков при потолке 15000 и ушёл
    ``required_pack_exceeds_budget``. Размер расписки не постоянен (голова результата плюс
    постановка — от 200 до 2100 знаков), поэтому любое назначенное число блокировало бы
    пакет ровно в те дни, когда отчёты подробнее обычного.

    Считаем сверху вниз и останавливаемся на первом влезшем; не влезло даже с одной —
    отдаём ``blocked`` как есть: отказ с причиной честнее молча урезанного пакета.
    """
    top = min(len(trigger["receipts"]), review_auto.DIGEST_RECEIPTS_MAX)
    case = pack = None
    for n in range(top, 0, -1):
        case = review_auto.case_for_digest(trigger["receipts"], trigger["day"], build_date,
                                           line_counts=counts, receipts_in_pack=n)
        pack = review_pack.build_review_pack(case, root=root, max_chars=max_chars,
                                             frame_version=frame[0], frame_version_note=frame[1])
        if pack["status"] == "ok":
            return case, pack
    if case is None:                       # расписок нет вовсе — сюда доходит только пустой день
        case = review_auto.case_for_digest([], trigger["day"], build_date, line_counts=counts)
        pack = review_pack.build_review_pack(case, root=root, max_chars=max_chars,
                                             frame_version=frame[0], frame_version_note=frame[1])
    return case, pack


def _fit_chain(rec, build_date, root, max_chars, frame):
    """Цепочка с ИЗМЕРЕННЫМ строчным бюджетом артефакта. → (case, pack).

    Бюджет не назначен, а подобран сборкой — тем же приёмом, что у `_fit_digest`, и
    по той же причине: размер артефакта не постоянен, а любое фиксированное число
    либо блокирует пакет в подробные дни, либо оставляет место пустым в короткие
    (замер лотка 05.09: медианный неиспользованный резерв 3517 знаков при потолке
    15000, максимум 8754 — то есть больше половины пакета уходило впустую).

    ПОТОЛОК ПАКЕТА ЗДЕСЬ НЕ ТРОГАЕТСЯ НИ ОДНОЙ ВЕТКОЙ: `max_chars` приходит сверху
    и только СУДИТ попытку. Меняется распределение уже имеющегося места, а не его
    количество — иначе пакет стал бы длиннее, но не умнее.
    """
    artifacts = list(rec.get("artifacts") or [])

    def assemble(plans, held, oversized=()):
        counts = line_counts(root, [review_auto.receipt_rel(rec)] + list(plans))
        case = review_auto.case_for_chain(rec, build_date, line_counts=counts,
                                          artifact_sources=list(plans), held_artifacts=held,
                                          artifact_plans=plans, oversized_artifacts=oversized)
        return case, review_pack.build_review_pack(case, root=root, max_chars=max_chars,
                                                   frame_version=frame[0], frame_version_note=frame[1])

    case = pack = None
    plans = held = None
    for budget in review_auto.ARTIFACT_LINE_BUDGETS:
        plans, held = plan_artifacts(root, artifacts, budget)
        case, pack = assemble(plans, held)
        # Влез И артефакт доехал: источник, выпавший по потолку, — это тот же
        # обрубок, только названный другим словом, и уменьшать бюджет ещё есть куда.
        if pack["status"] == "ok" and not [o for o in pack["omitted"] if o.get("reason") == "context_limit"]:
            return case, pack

    # Ни один бюджет не вместил артефакт. Пересобираем БЕЗ него: иначе сводка
    # обещала бы «показано N строк» у источника, которого в пакете нет вовсе, —
    # то есть врала бы ровно в том месте, ради честности которого правка и делалась.
    # Причину назовёт раздел «ОПУЩЕНО» словом `context_limit`, как и раньше.
    lost = {o["path"] for o in (pack["omitted"] if pack else []) if o.get("reason") == "context_limit"}
    if lost:
        oversized = [{"path": path, "lines": (plans or {}).get(path, {}).get("total_lines")} for path in sorted(lost)]
        case, pack = assemble({k: v for k, v in (plans or {}).items() if k not in lost}, held, oversized)
    return case, pack


def _build_pack(trigger, root, build_date, max_chars):
    """Повод → (case, pack, text, rel-путь пакета). Пишет индекс дня для дайджеста.

    Версия канона рамки читается ЗДЕСЬ и ровно ОДИН раз на пакет: подгонка
    дайджеста под потолок пересобирает пакет до DIGEST_RECEIPTS_MAX раз, и чтение
    внутри цикла било бы по мосту столько же раз ради одного и того же числа.
    """
    frame = review_pack_build.live_frame_version()
    if trigger["kind"] == "chain":
        case, pack = _fit_chain(trigger["receipt"], build_date, root, max_chars, frame)
    else:
        index_rel = review_auto.digest_index_rel(trigger["day"])
        write_text(_path(root, index_rel),
                   review_auto.digest_index_text(trigger["receipts"], trigger["day"], build_date))
        counts = line_counts(root, [index_rel] + [review_auto.receipt_rel(r) for r in trigger["receipts"]])
        case, pack = _fit_digest(trigger, build_date, counts, root, max_chars, frame)
    text = review_pack.render_review_pack(pack)
    rel = "%s/%s" % (DEFAULT_OUTBOX, review_pack.pack_filename(pack))
    write_text(_path(root, rel), text)
    return case, pack, text, rel


def tick(*, root=HERE, state_path=None, now=None, digest_hour=DEFAULT_DIGEST_HOUR,
         channels=None, timeout=DEFAULT_TIMEOUT, key_env=review_send_run.DEFAULT_KEY_ENV,
         dry=False, write_journal=True, max_chars=review_pack.REVIEW_MAX_CHARS,
         codex_bin=None, codex_model=None, codex_cd=None, manus_base=None,
         inbox=None, workdir=None, journal_fn=None, clock=None, only=None,
         probe_wait=DEFAULT_PROBE_WAIT):
    """Один оборот ступени A. → dict-отчёт (никогда не бросает наружу исключений канала).

    Отчёт всегда несёт ``acted`` и ``why``: «повода не было» — это ИСХОД, а не
    молчание, и вызывающий (демон) обязан уметь его записать.
    """
    stamp = now or now_iso(clock)
    path = state_path or _path(root, DEFAULT_STATE)
    state = read_state(path)

    # `only` — не боевая ручка, а СПОСОБ ПРОВЕРИТЬ дайджест руками. Без неё он недостижим:
    # цепочка всегда идёт первой, и пока спул не пуст, дайджест не собрать даже сухим прогоном.
    if only == "chain":
        trigger = review_auto.chain_trigger(state, stamp)
    elif only == "digest":
        trigger = review_auto.digest_trigger(state, stamp, digest_hour)
    elif only is None:
        trigger = review_auto.next_trigger(state, stamp, digest_hour)
    else:
        raise review_auto.ReviewAutoError("invalid_only", "only=%r not in ('chain','digest')" % (only,))
    if trigger is None:
        return {"acted": False, "why": "повода нет", "trigger": None}

    build_date = review_auto.day_of(stamp)

    # Дайджест наступил, а отдавать нечего — законный исход. День закрываем
    # словом `empty`, иначе контур весь день перебирал бы пустое окно.
    if trigger["kind"] == "digest" and not trigger["receipts"]:
        state = review_auto.note_digest_day(state, trigger["day"], "empty")
        write_state(path, state)
        return {"acted": False, "why": "дайджест наступил, закрытых цепочек за сутки нет",
                "trigger": trigger["key"]}

    case, pack, text, pack_rel = _build_pack(trigger, root, build_date, max_chars)
    report = {
        "acted": True,
        "trigger": trigger["key"],
        "kind": trigger["kind"],
        "pack": pack_rel,
        "pack_status": pack["status"],
        "pack_chars": pack["text_chars"],
        "pack_sha256": pack["text_sha256"],
        "hypothesis": len(case.get("hypothesis") or []),
        "occasion": trigger.get("occasion") or ("суточный дайджест" if trigger["kind"] == "digest" else "—"),
    }

    prompt = review_send.build_prompt(text)
    violations = review_send.outbound_violations(prompt)
    report["guard"] = sorted({v["kind"] for v in violations}) or ["чисто"]

    if dry:
        report["acted"] = False
        report["why"] = "сухой прогон: пакет собран, наружу не отправлено ничего"
        return report

    # ПЛАН КАНАЛОВ СЧИТАЕТСЯ ДО `note_attempt` СОЗНАТЕЛЬНО. Заход, в котором ехать
    # некому, попыткой не является: списать её значило бы похоронить повод за чужой
    # простой. Пакет при этом уже собран и лежит в лотке — он не потерян.
    wanted = list(channels or list(review_send.CHANNELS))
    plan = review_auto.channel_plan(state, wanted, stamp)
    report["channels"] = {"send": list(plan["send"]), "probe": list(plan["probe"]),
                          "skip": list(plan["skip"])}
    report["channels_line"] = review_auto.channel_plan_line(plan)
    going = list(plan["send"]) + list(plan["probe"])
    if not going:
        report["acted"] = False
        report["why"] = "все каналы лежат, пакет собран и ждёт: %s" % report["channels_line"]
        return report

    state = review_auto.note_attempt(state, trigger["key"], trigger["kind"], stamp, pack_rel=pack_rel)
    write_state(path, state)          # заход отмечен ДО сети: обрыв здесь обязан стоить попытку

    ctx = {
        "root": root,
        "workdir": workdir or _path(root, DEFAULT_WORKDIR),
        "pack_name": os.path.basename(pack_rel),
        "pack_sha256": review_send._sha256_text(text),
        "prompt_sha256": review_send._sha256_text(prompt),
        "send_date": build_date,
    }
    def _args_for(is_probe):
        return _ChannelArgs(timeout, key_env, codex_bin=codex_bin, codex_model=codex_model,
                            codex_cd=codex_cd, manus_base=manus_base,
                            manus_wait=probe_wait if is_probe else None)

    inbox_dir = inbox or _path(root, DEFAULT_INBOX)

    outcomes, reasons, answers = [], [], []
    for channel in going:
        is_probe = channel in plan["probe"]
        args = _args_for(is_probe)
        if violations:
            verdict = review_send.refused_by_guard(
                channel=channel, pack_name=ctx["pack_name"], pack_sha256=ctx["pack_sha256"],
                prompt_sha256=ctx["prompt_sha256"], send_date=build_date, violations=violations,
            )
            answer = ""
        else:
            try:
                verdict, answer = review_send_run.run_channel(channel, prompt, ctx, args)
            except Exception as exc:
                # Падение РУК канала исходом канала не является — но и тишиной
                # быть не может: называем его отказом с причиной и идём дальше.
                verdict = review_send._verdict(
                    channel=channel, pack_name=ctx["pack_name"], pack_sha256=ctx["pack_sha256"],
                    send_date=build_date, outcome="refused", reason="sender_crashed",
                    detail="руки канала упали: %s" % exc, prompt_sha256=ctx["prompt_sha256"],
                )
                answer = ""
        rel = "%s/%s" % (DEFAULT_INBOX, review_send.answer_filename(ctx["pack_name"], channel, build_date))
        write_text(os.path.join(inbox_dir, os.path.basename(rel)),
                   review_send.render_answer(verdict, answer, pack_rel=pack_rel))
        outcomes.append(verdict["outcome"])
        reasons.append(verdict["reason"])
        answers.append({"channel": channel, "outcome": verdict["outcome"], "reason": verdict["reason"],
                        "answer_chars": verdict["answer_chars"], "cost_value": verdict.get("cost_value"),
                        "cost_unit": verdict.get("cost_unit"), "file": rel, "probe": is_probe})
        state = review_auto.note_channel(state, channel, verdict["outcome"], verdict["reason"],
                                         stamp, probed=is_probe)

    state, verdict_slug = review_auto.note_outcome(state, trigger["key"], outcomes, reasons, stamp)
    if trigger["kind"] == "digest":
        state = review_auto.note_digest_day(state, trigger["day"], verdict_slug)
    write_state(path, state)

    report.update({"outcomes": outcomes, "reasons": reasons, "answers": answers, "verdict": verdict_slug})
    report["line"] = "%s; повод: %s; каналы: %s" % (
        review_auto.refusal_line(trigger["key"], verdict_slug, outcomes, reasons, pack_rel),
        report["occasion"], report["channels_line"],
    )

    if write_journal:
        line = "ARTIFACT ревью-контур A %s → %s: %s" % (trigger["key"], pack_rel, report["line"])
        code, out = (journal_fn or journal)(line)
        report["journal"] = {"code": code, "out": out}
    return report


# ───────────────────────────── добор из живой очереди ─────────────────────────


def from_queue(*, root=HERE, state_path=None, statuses=("done",), limit=20, clock=None):
    """Добрать закрытые цепочки из ЖИВОЙ очереди в спул. → list расписок.

    Это НЕ боевой путь (боевой — :func:`note_closed` из витка демона), а разовый
    добор: контур, заведённый сегодня, иначе не увидел бы ни одной цепочки,
    закрытой до его рождения. Читает очередь ТОЛЬКО на чтение.
    """
    from queue_snapshot_pc import bridge_getter           # тот же одолженный клиент, что у слепка

    get_pending = bridge_getter()
    path = state_path or _path(root, DEFAULT_STATE)
    out = []
    for status in statuses:
        res = get_pending(status)
        if not res.get("ok"):
            continue
        rows = [it for it in (res.get("items") or []) if str(it.get("lane") or "pc") == "pc"]
        rows.sort(key=lambda it: str(it.get("updated") or it.get("created") or ""), reverse=True)
        for row in rows[:limit]:
            closed = row.get("updated") or row.get("created")
            if not review_auto.parse_iso(closed):
                continue
            rec = note_closed(
                int(row.get("id")), row.get("task_text"), status, row.get("result"),
                root=root, state_path=path, clock=clock, stamp=closed,
            )
            if rec is not None:
                out.append(rec)
    return out


# ───────────────────────────── CLI ─────────────────────────────


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ступень A ревью-контура: повод → пакет → канал.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tick", action="store_true", help="боевой оборот (тот же вызов, что у демона)")
    mode.add_argument("--dry", action="store_true", help="собрать пакет, наружу НЕ ходить")
    mode.add_argument("--status", action="store_true", help="что решил бы контур; ничего не пишем")
    mode.add_argument("--from-queue", dest="from_queue", action="store_true",
                      help="добрать закрытые цепочки из живой очереди в спул")
    parser.add_argument("--root", default=HERE)
    parser.add_argument("--state", default=None)
    parser.add_argument("--now", default=None, help="отметка времени ISO (по умолчанию сейчас)")
    parser.add_argument("--digest-hour", type=int, default=DEFAULT_DIGEST_HOUR)
    parser.add_argument("--channel", action="append", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--key-env", default=review_send_run.DEFAULT_KEY_ENV)
    parser.add_argument("--codex-bin", default=None)
    parser.add_argument("--codex-model", default=None)
    parser.add_argument("--codex-cd", default=None)
    parser.add_argument("--manus-base", default=None, help="адрес канала Manus (для отрицательной пробы)")
    parser.add_argument("--probe-wait", type=int, default=DEFAULT_PROBE_WAIT,
                        help="предел ожидания у ПРОБЫ лежачего канала, с")
    parser.add_argument("--only", choices=("chain", "digest"), default=None,
                        help="рассматривать только этот повод (проверка руками, не боевая ручка)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--journal", action="store_true", help="дописать строку-индекс в журнал")
    args = parser.parse_args(argv)

    state_path = args.state or _path(args.root, DEFAULT_STATE)

    if args.from_queue:
        got = from_queue(root=args.root, state_path=state_path, limit=args.limit)
        sys.stdout.write("ДОБРАНО ЦЕПОЧЕК: %d\n" % len(got))
        for rec in got:
            sys.stdout.write("  %s · %s · операционное изменение: %s (%s)\n"
                             % (rec["task_id"], rec["reported_status"],
                                "да" if rec["operational_change"] else "нет", rec["change_reason"]))
        return 0

    stamp = args.now or now_iso()
    if args.status:
        state = read_state(state_path)
        trigger = review_auto.next_trigger(state, stamp, args.digest_hour)
        spool = state.get("spool") or []
        sys.stdout.write("СПУЛ: %d цепочек, из них с операционным изменением %d\n"
                         % (len(spool), sum(1 for r in spool if r.get("operational_change"))))
        sys.stdout.write("ДАЙДЖЕСТ: последний день %s (%s)\n"
                         % (state["digest"].get("last_day") or "—", state["digest"].get("last_reason") or "—"))
        if trigger is None:
            sys.stdout.write("ПОВОД: нет\n")
        else:
            sys.stdout.write("ПОВОД: %s (%s)\n" % (trigger["key"], trigger["kind"]))
        for key, rec in sorted((state.get("triggers") or {}).items()):
            sys.stdout.write("  повод %s: заходов %s, закрыт=%s, вердикт=%s\n"
                             % (key, rec.get("attempts"), rec.get("closed"), rec.get("verdict")))
        return 0

    report = tick(
        root=args.root, state_path=state_path, now=stamp, digest_hour=args.digest_hour,
        channels=args.channel, timeout=args.timeout, key_env=args.key_env, dry=args.dry,
        write_journal=bool(args.journal), codex_bin=args.codex_bin, codex_model=args.codex_model,
        codex_cd=args.codex_cd, manus_base=args.manus_base, only=args.only,
        probe_wait=args.probe_wait,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if not report.get("acted"):
        return 0
    if "answered" in (report.get("outcomes") or []):
        return 0
    return 3


if __name__ == "__main__":
    sys.exit(main())
