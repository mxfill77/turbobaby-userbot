# -*- coding: utf-8 -*-
"""
samoprogon.py — САМОПРОГОН тренажёра БЕЗ владельца: корпус гоняется сам, наружу — НОЛЬ.

Задание Штаба 0024-72c.2309. Разведка, на которой стоит модуль: 71n
(`docs/artifacts/2026-09-22-71n-SAMOPROGONRAZVEDKA-2209.md`). Три её находки, закрытые здесь:
  1. прогон корпуса шлёт владельцу ЖИВУЮ карточку: кейс 17 нарочно пересекает границу сезонов,
     путь ответа идёт `suggest` → `season_gate.note_for` → `raise_card` → `_default_sender()` →
     `dispatch_notify.deliver`, а ловушки у `trainer_run` нет (она есть только у витрины экзамена);
  2. живого вызывающего БЕЗ урока нет ни одного;
  3. судья цены замкнут на текст, который вставил САМ КОД (`ensure_price_figure`,
     `compose_quote_draft` — «ТОЛЬКО КОДОМ»): наивный самопрогон мерил бы код, а не бота.

ЧЕМ МОДУЛЬ НЕ ЯВЛЯЕТСЯ (границы, они же безопасность; на каждую — замок в `test_samoprogon`):
  • НЕ шлёт наружу НИЧЕГО: ни карточки, ни строки владельцу, ни dispatch, ни журнала, ни мозга.
    В КОДЕ модуля нет ни одного имени отправителя (замок по AST в наборе). Единственный выход —
    файлы в `tmp/samoprogon/` и stdout;
  • НЕ пишет в боевую базу уроков (`lesson_store.tsv`), в живой набор экзамена (`exam_live/`) и в
    реестр ворот выкатки. Кандидаты ложатся в СВОЮ таблицу того же формата (`CANDIDATES_FILE`),
    которую голова не читает (`suggest.active_lesson_bullets` открывает ровно `STORE_PATH`);
  • НЕ промотирует: кандидат становится правилом только словом владельца («урок включи N:
    причина» → `lesson_promote.py` → право автора и непустая причина). Вызова промоута в коде
    модуля нет ни одного (замок по AST);
  • НЕ правит клиентского бота: модуль вне клиентского контура (`client_contour.is_client` →
    False), `suggest`/`season_gate`/`lesson_store` только ЗОВЁТ, не меняя в них ни строки;
  • НЕ идёт дорогой `lesson_regress --after-lesson`: у неё при ПУСТОЙ очереди уроков замер всё
    равно состоится, а владельцу уйдёт «Урок записан» про урок, которого не было
    (`worth_measuring` на пустом отвечает True). Строки об уроке в коде модуля нет (замок по AST).

ЧЕСТНЫЙ СЧЁТ — ТРИ ИСХОДА НА КЕЙС, И ЦЕНА НИКОГДА НЕ ЗЕЛЁНАЯ:
  • КРАСНЫЙ — хотя бы один ЧЕСТНЫЙ чек красен (лексика, регрессия кода с внешним ожиданием);
  • НЕИЗВЕСТНО — прибор не смог судить (`trainer_run`: голова молчит, записка пуста) ЛИБО в
    кейсе стоит чек ЗАМКНУТОГО судьи (`CLOSED_CHECKS`): его ожидание берётся из той же ценовой
    записки, которую код сам вставил в черновик, и зелень такого чека говорит о коде, а не о боте.
    Замкнутый чек не засчитывается ни зелёным, ни красным — он виден в отчёте отдельной графой;
  • ЗЕЛЁНЫЙ — только кейс, где ВСЕ применимые чеки честные и все зелены.
Доля НЕИЗВЕСТНО печатается первой строкой отчёта, словами и числом (`tally`).

ЖИВОЙ ВЫЗЫВАЮЩИЙ — `maybe_launch()`: зовётся на витке демона, раз в местные сутки после
`SAMOPROGON_HOUR` (по Пхукету) поднимает ОТСОЕДИНЁННЫЙ `samoprogon.py --run` (образец —
`lesson_regress._launch`). ОЧЕРЕДЬ САМОПРОГОНА — список ИЗМЕНЕНИЙ предмета замера с прошлого
состоявшегося замера: коммит кода, отпечаток корпуса, отпечаток книги уроков. Очередь пуста →
замер НЕ состоится и сказано об этом будет только в файл состояния.

Места записи (временное место задания, названо явно): `tmp/samoprogon/` — состояние, лок,
отчёт, таблица кандидатов. Больше модуль не пишет никуда (изоляция `moderation_ipc` и логов —
штатная `trainer_run`: temp-БД и `TURBOBABY_TEST_LOGS=1`).

Запуск:
    venv/Scripts/python.exe samoprogon.py --plan        # разбор корпуса по судьям, БЕЗ головы
    venv/Scripts/python.exe samoprogon.py --status      # что лежит в состоянии
    venv/Scripts/python.exe samoprogon.py --run [--only 17] [--force]   # тело ребёнка (зовёт голову)
Рубильник: `SAMOPROGON_OFF=1` — живой вызывающий мёртв целиком.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(REPO, "venv", "Scripts", "python.exe")
SELF = os.path.join(REPO, "samoprogon.py")

DIR = os.path.join(REPO, "tmp", "samoprogon")                 # ЕДИНСТВЕННОЕ место записи модуля
STATE_FILE = os.path.join(DIR, "state.json")
LOCK_FILE = os.path.join(DIR, "run.lock")
REPORT_FILE = os.path.join(DIR, "last_report.md")
CANDIDATES_FILE = os.path.join(DIR, "candidates.tsv")          # формат lesson_store, НЕ боевая база

OFF_ENV = "SAMOPROGON_OFF"
HOUR_ENV = "SAMOPROGON_HOUR"
HOUR_DEFAULT = 4                    # местный час (UTC+7), после которого сутки считаются «пора»
TZ_OFFSET = 7 * 3600                # Пхукет — тот же пояс, что у суточного списка ревизора
LOCK_STALE = 3 * 3600               # лок старше — отбирается: умерший замер не держит прибор
RUNS = 1                            # скрининг: один круг на кейс (ворот не открывает — и не должен)
CRITIC_MAX = 3                      # сколько красных кейсов за замер отдаём критику
WHO = "самопрогон (бот сам; критик — та же голова)"
HISTORY_KEEP = 20

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = (getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | NO_WINDOW)

# ── ЗАМКНУТЫЙ СУДЬЯ: чеки, чьё ожидание берётся из той же записки, что КОД вставил сам ─────────
# Источник перечня — `trainer_run.expectations()` (ожидания из `pricing_note`) и
# `trainer_run.case_checks()` (что с ними сверяется). Причина у каждого — дословно в отчёт.
CLOSED_CHECKS = {
    "строка J дословно": "строку J вставляет КОД из записки (compose_quote_draft), ожидание — "
                         "та же записка",
    "сетка прайса дословно": "сетку вставляет КОД из sheet-блока записки, ожидание — тот же блок",
    "цена цифрой": "число вставляет КОД (ensure_price_figure), ожидание — числа той же записки",
    "минимальный срок доехал": "правило гарантирует КОД (ensure_min_term), ожидание — та же записка",
}
DELIVERY_CHECK = "доставка = цена зоны"
DELIVERY_WHY = ("строку доставки вставляет КОД, а цену зоны корпус не назвал — ожидание взято из "
                "той же строки")

GREEN, RED, UNKNOWN = "green", "red", "unknown"


# ─────────────────────────────────────── рубильник и часы ────────────────────────────────────

def off(env=None):
    env = os.environ if env is None else env
    if str(env.get(OFF_ENV) or "").strip().lower() in ("1", "on", "yes", "true", "да"):
        return "рубильник %s" % OFF_ENV
    if env.get("TESTING"):
        return "TESTING=1 — живой самопрогон из тестового захода не поднимаем"
    return ""


def hour(env=None):
    env = os.environ if env is None else env
    try:
        h = int(str(env.get(HOUR_ENV) or "").strip() or HOUR_DEFAULT)
    except ValueError:
        return HOUR_DEFAULT
    return h if 0 <= h <= 23 else HOUR_DEFAULT


def local(now):
    """Unix-время → местное (Пхукет) datetime без пояса."""
    return datetime.datetime.fromtimestamp(float(now) + TZ_OFFSET,
                                           datetime.timezone.utc).replace(tzinfo=None)


def due(state, now, at_hour=HOUR_DEFAULT):
    """Пора ли поднимать самопрогон. → (да/нет, почему словами).

    Раз в МЕСТНЫЕ сутки и не раньше `at_hour`. Отметка ставится при ПОДЪЁМЕ ребёнка
    (`launched_day`), а не при конце замера: упавший ребёнок не имеет права превратить сутки в
    поток перезапусков — следующий заход будет завтра, а причина провала лежит в истории."""
    t = local(now)
    day = t.strftime("%Y-%m-%d")
    if t.hour < int(at_hour):
        return False, "рано: местный час %d < %d" % (t.hour, int(at_hour))
    if str((state or {}).get("launched_day") or "") == day:
        return False, "сегодня (%s) уже поднимался" % day
    return True, day


# ─────────────────────────────────────── состояние и лок ─────────────────────────────────────

def read_state(path=None):
    try:
        with io.open(path or STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(d, path=None):
    path = path or STATE_FILE
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        return False, "состояние не записано: %s" % e
    return True, path


def acquire(path=None, now=None, stale=LOCK_STALE):
    """Одиночка замера: O_EXCL-файл. Стухший и снятый лок отбираются. → (взят?, причина)."""
    path = path or LOCK_FILE
    now = time.time() if now is None else now
    payload = json.dumps({"pid": os.getpid(), "ts": now})
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        return True, ""
    except OSError:
        pass
    try:
        with io.open(path, encoding="utf-8") as f:
            held = json.load(f)
        age = now - float(held.get("ts") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        held, age = {}, stale + 1
    if held.get("pid") and age <= stale:
        return False, "самопрогон уже идёт (pid %s, %.0fс)" % (held.get("pid"), age)
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(payload)
    except OSError as e:
        return False, "лок не отобран: %s" % e
    return True, ""


def release(path=None):
    """Лок не удаляется (уборка запрещена) — перезаписывается пустой меткой «свободно»."""
    try:
        with io.open(path or LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": None, "ts": 0}))
        return True
    except OSError:
        return False


# ─────────────────────────── ОЧЕРЕДЬ самопрогона: изменения предмета замера ──────────────────

def _sha_file(path):
    """sha256 файла (16 hex) | '?' — прочитать не смогли (незнание НЕ равно «не менялось»)."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return "?"


def _head():
    try:
        p = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=30, creationflags=NO_WINDOW)
        out = (p.stdout or "").strip()
        return out if p.returncode == 0 and len(out) == 40 else "?"
    except Exception:                                        # noqa: BLE001 — «не знаю» ≠ «тот же»
        return "?"


def subject(head_fn=None, corpus=None, book=None):
    """ПРЕДМЕТ замера — то, от чего зависит ответ бота на корпусе. → dict.

    Книга уроков ЧИТАЕТСЯ (sha байтов), не открывается на запись ни одной веткой."""
    if corpus is None or book is None:
        import client_contour                                # noqa: PLC0415 — лёгкий, без сети
        corpus = client_contour.TRAINER_CASES_FILE if corpus is None else corpus
        if book is None:
            import lesson_store                              # noqa: PLC0415
            book = lesson_store.STORE_PATH
    return {"commit": (head_fn or _head)(), "corpus_sha": _sha_file(corpus),
            "book_sha": _sha_file(book)}


_SUBJECT_WORDS = {"commit": "коммит кода", "corpus_sha": "корпус", "book_sha": "книга уроков"}


def queue(state, subj):
    """ОЧЕРЕДЬ самопрогона = изменения предмета с прошлого СОСТОЯВШЕГОСЯ замера. → список слов.

    Прошлого замера не было → «первый замер». Поле '?' (не прочитали) — всегда изменение: два
    «не знаю» не равны «тому же самому». Пусто → мерить нечего, и замер НЕ состоится."""
    last = (state or {}).get("measured_subject")
    if not isinstance(last, dict):
        return ["первый замер"]
    out = []
    for k, word in _SUBJECT_WORDS.items():
        a, b = str(last.get(k) or "?"), str((subj or {}).get(k) or "?")
        if a == "?" or b == "?" or a != b:
            out.append("%s: %s → %s" % (word, a[:7], b[:7]))
    return out


# ─────────────────────────────────── ЧЕСТНЫЙ СУДЬЯ ───────────────────────────────────────────

def judge_check(case, chk):
    """Чек → (судья, причина). Судья: 'skipped' | 'closed' | 'honest'."""
    name = str(chk.get("name") or "")
    if chk.get("skipped"):
        return "skipped", str(chk.get("skipped"))
    if name in CLOSED_CHECKS:
        return "closed", CLOSED_CHECKS[name]
    if name == DELIVERY_CHECK and (case or {}).get("zone_price") is None:
        return "closed", DELIVERY_WHY
    return "honest", ""


def judge_round(case, res):
    """Один круг кейса (`trainer_run.run_case`) → честный исход. → dict.

    Порядок решает: сперва честный КРАСНЫЙ (уличённое остаётся уличённым, сколько бы замкнутых
    чеков ни стояло рядом), потом НЕИЗВЕСТНО прибора и замкнутого судьи, и только потом зелёное."""
    honest_ok, honest_red, closed, closed_red = [], [], [], []
    for c in res.get("checks") or []:
        kind, _why = judge_check(case, c)
        if kind == "skipped":
            continue
        if kind == "closed":
            closed.append(c.get("name"))
            if not c.get("ok"):
                closed_red.append(c.get("name"))
            continue
        (honest_ok if c.get("ok") else honest_red).append(c.get("name"))
    instrument = str(res.get("unknown") or "")
    if instrument:
        # Прибор не судил: его чеки помечены unknown и в честный счёт не идут ни одной графой.
        honest_ok, honest_red = [], []
    if honest_red:
        outcome = RED
    elif instrument or closed:
        outcome = UNKNOWN
    else:
        outcome = GREEN if honest_ok else UNKNOWN
    why = ""
    if outcome == UNKNOWN:
        why = ("прибор: " + instrument) if instrument else (
            ("замкнутый судья: " + ", ".join(closed)) if closed else "честных чеков нет")
    return {"id": str(case.get("id")), "name": case.get("name"), "outcome": outcome,
            "why": why, "honest_ok": honest_ok, "honest_red": honest_red,
            "closed": closed, "closed_red": closed_red}


def judge_case(case, rec):
    """Все круги кейса → исход кейса: красный, если красен хоть один круг; иначе неизвестно,
    если неизвестен хоть один; иначе зелёный."""
    rounds = [judge_round(case, r) for r in (rec or {}).get("results") or []]
    if not rounds:
        return {"id": str(case.get("id")), "name": case.get("name"), "outcome": UNKNOWN,
                "why": "кругов нет", "rounds": [], "honest_ok": [], "honest_red": [],
                "closed": [], "closed_red": []}
    outs = [r["outcome"] for r in rounds]
    outcome = RED if RED in outs else (UNKNOWN if UNKNOWN in outs else GREEN)
    head = next(r for r in rounds if r["outcome"] == outcome)
    return dict(head, rounds=rounds)


def tally(judged):
    """ГРОМКАЯ ГРАФА. → dict(total, green, red, unknown, unknown_closed, unknown_instrument,
    honest_measurable, share, line)."""
    total = len(judged)
    g = sum(1 for j in judged if j["outcome"] == GREEN)
    r = sum(1 for j in judged if j["outcome"] == RED)
    u = [j for j in judged if j["outcome"] == UNKNOWN]
    u_closed = sum(1 for j in u if str(j.get("why") or "").startswith("замкнутый"))
    share = (float(len(u)) / total) if total else 0.0
    line = ("НЕИЗВЕСТНО %d из %d (%.0f%%): замкнутый судья цены %d, прибор %d · честно измерено "
            "%d (зелёных %d, красных %d)"
            % (len(u), total, share * 100, u_closed, len(u) - u_closed, g + r, g, r))
    return {"total": total, "green": g, "red": r, "unknown": len(u),
            "unknown_closed": u_closed, "unknown_instrument": len(u) - u_closed,
            "honest_measurable": g + r, "share": share, "line": line}


def plan(cases):
    """Разбор корпуса по судьям ДО головы (статически, по флагам кейса). → список dict.

    Чек «минимальный срок доехал» статически не виден — он появляется, только если записка
    несёт правило минимума; поэтому живой счёт НЕИЗВЕСТНО может быть больше плана, но не меньше."""
    out = []
    for c in cases:
        want = c.get("expect") or {}
        closed = []
        if want.get("quote"):
            closed.append("строка J дословно")
        if want.get("sheet"):
            closed.append("сетка прайса дословно")
        if want.get("price_figure"):
            closed.append("цена цифрой")
        if want.get("delivery") and c.get("zone_price") is None:
            closed.append(DELIVERY_CHECK)
        skip = c.get("skip") or {}
        closed = [n for n in closed if n not in skip]
        out.append({"id": str(c.get("id")), "name": c.get("name"), "closed": closed,
                    "judge": "замкнутый (цена)" if closed else "честный"})
    return out


# ─────────────────────────────────── ЛОВУШКА НАРУЖУ ──────────────────────────────────────────

def default_trap():
    """Ловушка карточек владельцу — ТА ЖЕ, что у витрины экзамена (`exam_show.OwnerCardTrap`):
    подменяет `_default_sender` у ОБОИХ сторожей третьего исхода и ставит заглушку на место
    модуля отправителя в `sys.modules`. Копии здесь нет — копия разъехалась бы молча."""
    import exam_show                                         # noqa: PLC0415 — только в ребёнке
    return exam_show.OwnerCardTrap()


def default_critic(question, answer, transcript):
    """Предложения бота — ТЕМИ ЖЕ тремя вызовами `trainer`, что у кнопки «🎓 Обучить» и критика
    экзамена. Критик — та же голова, поэтому вес предложения — ГИПОТЕЗА, а не находка."""
    import suggest                                           # noqa: PLC0415
    import trainer                                           # noqa: PLC0415
    system, user = trainer.hypotheses_prompt(question, answer, transcript=transcript, paired=True)
    raw = suggest.default_llm_caller()(system, user)
    return [trainer.split_pair(h) for h in trainer.parse_hypotheses(raw, limit=3)]


def write_candidates(items, path=None, store=None):
    """Предложения → строки `кандидат` в СВОЮ таблицу. Причина ПУСТАЯ (не выдумываем её за
    владельца). → (легло, не легло, причины отказа)."""
    if store is None:
        import lesson_store as store                         # noqa: PLC0415
    path = path or CANDIDATES_FILE
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(store.STORE_PATH)):
        return 0, len(items), ["путь кандидатов совпал с боевой базой — не пишу ни строки"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    ok, bad, why = 0, 0, []
    for it in items:
        try:
            store.add_candidate(it["question"], it["answer"], it["rule"], WHO,
                                source=store.SOURCE_TRAINER, why="", path=path)
            ok += 1
        except Exception as e:                               # noqa: BLE001 — строка, не прогон
            bad += 1
            why.append("%s: %s" % (type(e).__name__, str(e)[:120]))
    return ok, bad, why


# ─────────────────────────────────────── ЗАМЕР ───────────────────────────────────────────────

def run(cases=None, runner=None, trap_factory=None, critic=None, only=None, log=None,
        cand_path=None, store=None):
    """ОДИН самопрогон корпуса под ловушкой. → dict(ok, why, judged, tally, cards, sent, …).

    Ловушка ставится на КАЖДЫЙ кейс отдельно (память «карточка уже поднята» у сторожей
    возвращается после круга — пойманная карточка владельцу не сказана). Не встала — голову НЕ
    зовём вовсе: карточка третьего исхода ушла бы владельцу вживую."""
    log = log or (lambda *_a, **_k: None)
    if runner is None:
        import trainer_run as runner                         # noqa: PLC0415 — тянет suggest и сеть
    trap_factory = trap_factory or default_trap
    if cases is None:
        cases, _sha = runner.load_cases()
    if only:
        want = {str(s).strip() for s in only if str(s).strip()}
        cases = [c for c in cases if str(c.get("id")) in want]
    ph = runner.placeholders()
    judged, cards, proposals, critic_calls = [], [], [], 0
    for case in cases:
        try:
            trap = trap_factory()
            trap.__enter__()
        except Exception as e:                               # noqa: BLE001
            return {"ok": False, "why": "ловушка наружу не встала (%s: %s) — голову не зову"
                                        % (type(e).__name__, e),
                    "judged": judged, "tally": tally(judged), "cards": cards, "proposals": []}
        try:
            rec = runner.run_one_case(case, RUNS, ph, log=log)
            j = judge_case(case, rec)
            if critic and j["outcome"] == RED and critic_calls < CRITIC_MAX:
                res = next((r for r in rec.get("results") or [] if (r.get("draft") or "").strip()),
                           None)
                if res is not None:
                    critic_calls += 1
                    tr = runner.build_transcript(case, ph)
                    q = runner.substitute((case.get("lines") or [""])[-1], ph)
                    try:
                        for h in critic(q, res["draft"], tr) or []:
                            rule = str((h or {}).get("rule") or "").strip()
                            if rule:
                                proposals.append({"case": j["id"], "question": q,
                                                  "answer": res["draft"][:1500], "rule": rule})
                    except Exception as e:                   # noqa: BLE001 — критик не судья замера
                        log("критик кейса %s не ответил (%s)" % (j["id"], type(e).__name__))
        finally:
            trap.__exit__(None, None, None)
        for c in trap.cards:
            cards.append({"case": str(case.get("id")), "gate": c.get("gate")})
        judged.append(j)
        log("  [%s] кейс %s «%s»%s" % (j["outcome"].upper(), j["id"], j["name"],
                                       (" — " + j["why"]) if j["why"] else ""))
    written = (0, 0, [])
    if proposals:
        written = write_candidates(proposals, path=cand_path, store=store)
    return {"ok": True, "why": "", "judged": judged, "tally": tally(judged), "cards": cards,
            "sent": 0, "proposals": proposals, "cand_ok": written[0], "cand_bad": written[1],
            "cand_why": written[2]}


def report_md(got, subj, changes, when):
    t = got.get("tally") or tally([])
    lines = ["# Самопрогон тренажёра без владельца — %s" % when, "",
             "**%s**" % t["line"], "",
             "Наружу: отправлено **0**; поймано ловушкой карточек владельцу: **%d** (%s)."
             % (len(got.get("cards") or []),
                ", ".join("кейс %s → %s" % (c["case"], c["gate"]) for c in got.get("cards") or [])
                or "ни одна ветка карточку не подняла"), "",
             "Предмет: коммит `%s`, корпус `%s`, книга уроков `%s` · очередь: %s"
             % (str(subj.get("commit"))[:7], subj.get("corpus_sha"), subj.get("book_sha"),
                "; ".join(changes) or "пусто"), "",
             "Кандидатов предложено %d, легло в `%s` %d (состояние «кандидат», причина пустая; "
             "промоут — только словом владельца)."
             % (len(got.get("proposals") or []), os.path.relpath(CANDIDATES_FILE, REPO),
                got.get("cand_ok") or 0), "",
             "| кейс | исход | честно зелено | честно красно | замкнутый судья (не засчитан) |",
             "|---|---|---|---|---|"]
    for j in got.get("judged") or []:
        lines.append("| %s «%s» | %s | %d | %s | %s |" % (
            j["id"], j["name"], {GREEN: "🟢", RED: "🔴", UNKNOWN: "🟡 НЕИЗВЕСТНО"}[j["outcome"]],
            len(j["honest_ok"]), ", ".join(j["honest_red"]) or "—",
            (", ".join(j["closed"]) + (" (красен: %s)" % ", ".join(j["closed_red"])
                                        if j["closed_red"] else "")) or "—"))
    return "\n".join(lines) + "\n"


def child(force=False, only=None, now=None, path=None, lock=None, subj=None, runner=None,
          trap_factory=None, critic=default_critic, log=None, report=None, cand_path=None):
    """Тело отсоединённого процесса: лок → очередь → замер → состояние и отчёт. Наружу — ничего."""
    log = log or (lambda *_a, **_k: None)
    path = path or STATE_FILE
    lock = lock or LOCK_FILE
    took, why = acquire(lock, now=now)
    if not took:
        return {"ran": False, "why": why}
    try:
        st = read_state(path)
        subj = subject() if subj is None else subj
        changes = queue(st, subj)
        when = local(time.time() if now is None else now).strftime("%Y-%m-%d %H:%M")
        hist = st.get("history") if isinstance(st.get("history"), list) else []
        if not changes and not force:
            hist.append({"when": when, "measured": False,
                         "why": "очередь пуста: ни код, ни корпус, ни книга уроков не менялись"})
            st["history"] = hist[-HISTORY_KEEP:]
            write_state(st, path)
            return {"ran": False, "why": "очередь пуста — мерить нечего"}
        try:
            got = run(runner=runner, trap_factory=trap_factory, critic=critic, only=only, log=log,
                      cand_path=cand_path)
        except Exception as e:                               # noqa: BLE001 — третий исход, не тишина
            got = {"ok": False, "why": "замер оборвался (%s: %s)" % (type(e).__name__, e)}
        t = got.get("tally") or tally([])
        hist.append({"when": when, "measured": bool(got.get("ok")), "why": got.get("why") or "",
                     "queue": changes, "only": list(only or []), "tally": {k: t[k] for k in (
                         "total", "green", "red", "unknown", "unknown_closed",
                         "unknown_instrument", "honest_measurable")},
                     "cards_caught": len(got.get("cards") or []), "sent": 0,
                     "candidates": got.get("cand_ok") or 0})
        st["history"] = hist[-HISTORY_KEEP:]
        if got.get("ok"):
            # ЧАСТИЧНЫЙ замер (`--only`) предмет замеренным НЕ объявляет: иначе прогон двух кейсов
            # опустошил бы очередь, и полный замер этого предмета не состоялся бы никогда.
            if not only:
                st["measured_subject"] = subj
            st["last"] = hist[-1]
            try:
                report = report or REPORT_FILE
                os.makedirs(os.path.dirname(report), exist_ok=True)
                with io.open(report, "w", encoding="utf-8") as f:
                    f.write(report_md(got, subj, changes, when))
            except OSError as e:
                log("отчёт не записан: %s" % e)
        write_state(st, path)
        return {"ran": True, "why": got.get("why") or "", "got": got}
    finally:
        release(lock)


# ─────────────────────────────────── ЖИВОЙ ВЫЗЫВАЮЩИЙ ────────────────────────────────────────

def launch_argv():
    """argv ребёнка. Ключа урока (`--after-lesson`) здесь нет и быть не должно (см. шапку)."""
    return [VENV_PY, SELF, "--run"]


def maybe_launch(now=None, path=None, popen=None, env=None, subj=None, lock=None):
    """Зовётся на КАЖДОМ витке демона; дорогое (git, sha) — только когда сутки «пора».
    НИКОГДА не бросает: самопрогон не смеет ронять виток. → dict(launched, why)."""
    try:
        why = off(env)
        if why:
            return {"launched": False, "why": why}
        now = time.time() if now is None else now
        path = path or STATE_FILE
        st = read_state(path)
        ok, day = due(st, now, hour(env))
        if not ok:
            return {"launched": False, "why": day}
        # ОЧЕРЕДЬ ПУСТА — ребёнка не поднимаем вовсе: замера не будет, и сутки отмечаются, чтобы
        # не спрашивать git на каждом витке до полуночи.
        changes = queue(st, subject() if subj is None else subj)
        st["launched_day"] = day
        if not changes:
            st["skipped_day"] = day
            write_state(st, path)
            return {"launched": False, "why": "очередь пуста — мерить нечего"}
        free, lwhy = _lock_free(lock or LOCK_FILE, now)
        if not free:
            return {"launched": False, "why": lwhy}
        wok, wwhy = write_state(st, path)
        if not wok:
            return {"launched": False, "why": wwhy}
        (popen or subprocess.Popen)(launch_argv(), cwd=REPO, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                    creationflags=DETACHED)
        return {"launched": True, "why": "; ".join(changes)}
    except Exception as e:                                   # noqa: BLE001 — см. докстринг
        return {"launched": False, "why": "%s: %s" % (type(e).__name__, e)}


def _lock_free(path, now):
    try:
        with io.open(path, encoding="utf-8") as f:
            held = json.load(f)
    except (OSError, ValueError):
        return True, ""
    if not isinstance(held, dict) or not held.get("pid"):
        return True, ""
    age = now - float(held.get("ts") or 0)
    return (age > LOCK_STALE), ("самопрогон уже идёт (pid %s, %.0fс)" % (held.get("pid"), age))


# ─────────────────────────────────────── CLI ─────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="самопрогон тренажёра без владельца (наружу ноль)")
    ap.add_argument("--run", action="store_true", help="тело ребёнка: замер под ловушкой")
    ap.add_argument("--plan", action="store_true", help="разбор корпуса по судьям, без головы")
    ap.add_argument("--status", action="store_true", help="что лежит в состоянии")
    ap.add_argument("--only", default="", help="через запятую: id кейсов")
    ap.add_argument("--force", action="store_true", help="мерить и при пустой очереди")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001
        pass
    if a.plan:
        import client_contour                                # noqa: PLC0415
        with io.open(client_contour.TRAINER_CASES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cases = data.get("cases") if isinstance(data, dict) else data
        p = plan(cases)
        closed = [x for x in p if x["closed"]]
        print("корпус: кейсов %d · судья замкнут на цену у %d (%s) · честный судья у %d (%s)"
              % (len(p), len(closed), ", ".join(x["id"] for x in closed), len(p) - len(closed),
                 ", ".join(x["id"] for x in p if not x["closed"])))
        for x in p:
            print("  %s «%s»: %s%s" % (x["id"], x["name"], x["judge"],
                                       (" — " + ", ".join(x["closed"])) if x["closed"] else ""))
        return 0
    if a.status:
        st = read_state()
        print(json.dumps({k: st.get(k) for k in ("launched_day", "skipped_day", "last",
                                                  "measured_subject")},
                         ensure_ascii=False, indent=1))
        return 0
    if a.run:
        only = [s for s in a.only.split(",") if s.strip()] if a.only else None
        got = child(force=a.force, only=only, log=print)
        g = got.get("got") or {}
        print("САМОПРОГОН: %s" % ((g.get("tally") or {}).get("line") or got.get("why")))
        print("наружу отправлено: 0 · поймано ловушкой: %d" % len(g.get("cards") or []))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
