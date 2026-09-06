# -*- coding: utf-8 -*-
"""
lesson_regress.py — РЕГРЕССИЯ УРОКА: после урока корпус прогоняется и говорит исход.

ЗАЧЕМ. Правило, записанное владельцем в тренажёре, применяется со СЛЕДУЮЩЕГО черновика
(`trainer._apply_lesson` → `suggest.append_playbook_rule`), а корпус из 16 кейсов, который умеет
дать вердикт (`trainer_run.py`), после урока не гонялся НИКОГДА (замер 05.09.2026: ни одного
вызова `trainer_run` из пути урока — только ворота, тесты и докстринги). Владелец видел
«записано» и не видел «и ничего не сломалось». Этот модуль добавляет второе.

ЧЕМ ОН НЕ ЯВЛЯЕТСЯ (границы, они же безопасность):
  • НЕ пишет вердикт ворот. Прогон регрессии — не основание выкатки: имени `write_verdict`
    и файла `TRAINER_GREEN_FILE` в этом модуле нет ни одного (сверяется тестом `test_lesson_regress`
    по тексту файла). В реестр ворот регрессия не кладёт ничего;
  • НЕ правит книгу правил и НЕ откатывает урок. Она ИЗМЕРЯЕТ и ГОВОРИТ; снятие урока остаётся
    движением владельца — у него для этого есть «отмени урок N» (`trainer.cancel_lesson` →
    `lesson_store.withdraw`). С 06.09.2026 это движение прибор ЗАПУСКАЕТ так же, как запись
    (`act=снят`): корпус меряет обе стороны, потому что ответ меняют обе;
  • НЕ задерживает нажатие. `spawn()` — fire-and-forget Popen отдельным ОТСОединённым процессом
    (DETACHED_PROCESS), урок пишется и карточка «✅ Принято» уходит владельцу как раньше. Прогон
    идёт ПОСЛЕ и в чужом процессе; его падение урока не отменяет;
  • НЕ трогает клиентов и не шлёт ничего наружу, кроме ОДНОЙ строки исхода владельцу через
    штатный `dispatch_notify` (тот же канал, что у всех пушей полосы).

ТРИ ГОЛОСА ИСХОДА (третий обязателен и молчанием не заменяется):
  ✅ ничего не сломалось — кейсы, что были зелёными, зелены и сейчас;
  🔴 сломалось N кейсов — каждый назван НОМЕРОМ и САМИМ ЧЕКОМ (имя, «ждали», «факт»), не пересказом;
  ❔ прогон не состоялся (голова молчит, Bridge недоступен, корпус не прочитан, бюджет вышел) →
     НЕИЗВЕСТНО. Это НЕ «всё хорошо»: молчание источника выздоровлением не является.
Четвёртый оттенок — «эталона не было, записана база»: он тоже не говорит «ничего не сломалось».

С ЧЕМ СРАВНИВАЕМ — С ЧИСЛОМ, А НЕ С ПАМЯТЬЮ. Последний известный результат лежит в
`pc_orchestrator.lesson_regress.json` (маска `pc_orchestrator.*.json` в .gitignore — там же живут
остальные файлы состояния полосы). Новой сущности в git это не заводит. Первый эталон, если своего
файла ещё нет, БЕРЁТСЯ ЧТЕНИЕМ из реестра ворот (`client_contour.TRAINER_GREEN_FILE`, только
чтение, только при совпавшем `corpus_sha`) — чтобы первый же урок сравнивался с числом, а не с
пустотой.

ЦЕНА И ЧАСТОТА (замер 05.09.2026, артефакт `2026-09-05-корпус-тренажёра-на-замороженном-кандидате`):
832с на 32 кейсо-прогона = 26с на кейсо-прогон. Скрининг = 16 кейсов × 1 прогон ≈ 416с ≈ 7 мин;
подтверждение красных = только красные кейсы × 1 прогон. Два полных прогона (как у ворот) здесь не
нужны: ворота удостоверяют коммит, регрессия СРАВНИВАЕТ — поэтому первый прогон ищет красное, а
второй ходит ТОЛЬКО по найденному красному (мигание генератора не даёт ложного «сломалось», а цена
остаётся ≈ половиной ворот).

УРОКИ ПОДРЯД — СКЛЕЙКА, а не очередь и не пропуск. Корпус меряет КНИГУ ЦЕЛИКОМ, а не отдельное
правило: после уроков A, B, C вопрос «ломает ли нынешняя книга корпус» закрывается ОДНИМ прогоном
по итоговой книге. Очередь ответила бы на тот же вопрос трижды (21 мин) и первые два ответа
устарели бы к моменту доставки; пропуск потерял бы ответ вовсе — а третий голос запрещает молчание.
Замок честности склейки: строка исхода НАЗЫВАЕТ все склеенные уроки по номерам, и при красном
исходе прямо говорит, что прибор не знает, КОТОРЫЙ из них сломал — иначе владельцу сказали бы
«твой урок #14 в порядке» о прогоне, мерившем #12+#13+#14.

Запуск:
    venv/Scripts/python.exe lesson_regress.py --after-lesson        # то, что зовёт trainer (детач)
    venv/Scripts/python.exe lesson_regress.py --status              # что лежит в эталоне
    venv/Scripts/python.exe lesson_regress.py --dry --only 1,4      # механизм на двух кейсах, без строки
Рубильники: `LESSON_REGRESS_OFF=1` гасит ветку целиком (урок пишется как раньше);
`LESSON_REGRESS_BUDGET_SEC` — потолок времени на один заход (по умолчанию 1800с).
"""

import argparse
import io
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(REPO, "venv", "Scripts", "python.exe")
SELF = os.path.join(REPO, "lesson_regress.py")
DNOTIFY = os.path.join(REPO, "dispatch_notify.py")

# состояние: та же маска, что у остальных файлов полосы (pc_orchestrator.*.json в .gitignore)
STATE_FILE = os.path.join(REPO, "pc_orchestrator.lesson_regress.json")
LOCK_FILE = os.path.join(REPO, "pc_orchestrator.lesson_regress.lock")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = (getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | NO_WINDOW)

OFF_ENV = "LESSON_REGRESS_OFF"
BUDGET_ENV = "LESSON_REGRESS_BUDGET_SEC"
BUDGET_DEFAULT = 1800                    # потолок одного захода, секунды
MAX_GLUED = 4                            # сколько раз подряд склейка перезапускает замер
# ДВА ДВИЖЕНИЯ ВЛАДЕЛЬЦА, ОБА МЕНЯЮТ ОТВЕТ, ОБА МЕРЯЮТСЯ ОДНИМ КОРПУСОМ (06.09.2026). Прибор
# завёлся на записи и говорил «Урок #N записан» безусловно; с подключением команды отмены урока
# к отзыву в базе тем же прибором меряется и снятие. Слово движения едет от нажатия до строки
# исхода: строка «Урок #7 записан» о СНЯТОМ уроке была бы ложью о собственном поводе, а именно
# по ней владелец судит, что вообще произошло.
#
# ИМЁН ЧУЖИХ ПИСАТЕЛЕЙ ЗДЕСЬ НЕТ И НИЖЕ НЕ БУДЕТ. Тело модуля держит инвариант «прибор ИЗМЕРЯЕТ
# и ГОВОРИТ, но сам ничего не откатывает», и держит его ТЕКСТОМ: набор проверяет, что имён
# функций отката и записи книги в теле файла не встречается вовсе (`test_lesson_regress`,
# `test_regression_does_not_touch_the_rule_book`). Замок дешёвый и потому строгий — упоминание
# ради красоты комментария его не сто́ит.
ACT_ADDED = "записан"
ACT_WITHDRAWN = "снят"
HISTORY_KEEP = 10
LINE_MAX = 700                           # «одна короткая строка» — режем по границе, а не молча
_PENDING_KEEP = 20


# ─────────────────────────────────────── рубильники ──────────────────────────────────────────

def off(env=None):
    """Ветка выключена? Рубильник + замок TESTING (набор гоняют с TESTING=1 — живой прогон
    корпуса из тестового захода недопустим ровно так же, как живой ряд в очередь)."""
    env = os.environ if env is None else env
    if str(env.get(OFF_ENV) or "").strip().lower() in ("1", "on", "yes", "true"):
        return "рубильник %s" % OFF_ENV
    if env.get("TESTING"):
        return "TESTING=1 — живой прогон корпуса из тестового захода не запускаем"
    return ""


def budget_sec(env=None):
    env = os.environ if env is None else env
    try:
        v = int(str(env.get(BUDGET_ENV) or "").strip() or BUDGET_DEFAULT)
    except ValueError:
        return BUDGET_DEFAULT
    return v if v > 0 else BUDGET_DEFAULT


# ─────────────────────────────────────── состояние ───────────────────────────────────────────

def read_state(path=None):
    """Состояние с диска (эталон + очередь склейки). Битое/отсутствующее → {} (не падение)."""
    path = path or STATE_FILE
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(d, path=None):
    """Состояние на диск атомарно (tmp + os.replace). → (ok, путь|причина)."""
    path = path or STATE_FILE
    tmp = path + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        return False, "состояние не записано: %s" % e
    return True, path


def note_pending(n, rule, path=None, now=None, act=None):
    """Пометить урок как ждущий замера. Пишется в НАЖАТИИ владельца, поэтому дёшево и fail-safe:
    один маленький json. Именно эта пометка делает СКЛЕЙКУ возможной — идущий замер увидит урок,
    приехавший после его старта, и перезапустится по итоговой книге.

    `act` — КАКОЕ движение владельца привело замер: `записан` (умолчание) или `снят`. Хранится
    рядом с номером потому, что склейка может собрать в один прогон и запись, и отмену, а строка
    исхода обязана назвать каждое движение своим словом."""
    now = time.time() if now is None else now
    try:
        d = read_state(path)
        pend = d.get("pending")
        pend = pend if isinstance(pend, list) else []
        pend.append({"n": n, "rule": str(rule or "")[:300], "ts": now,
                     "act": str(act or ACT_ADDED)})
        d["pending"] = pend[-_PENDING_KEEP:]
        return write_state(d, path)[0]
    except Exception:                                        # noqa: BLE001 — урок важнее пометки
        return False


def take_pending(path=None):
    """Снять очередь склейки (прочитать и очистить). → список уроков."""
    d = read_state(path)
    pend = d.get("pending")
    pend = list(pend) if isinstance(pend, list) else []
    if pend:
        d["pending"] = []
        write_state(d, path)
    return pend


# ─────────────────────────────────────── одиночка ────────────────────────────────────────────

def acquire(path=None, now=None, stale=None):
    """Одиночка замера: O_EXCL-файл с pid и временем. → (взят?, причина).

    Стухший лок (старше `stale`) ОТБИРАЕТСЯ: умерший замер не имеет права держать регрессию
    выключенной навсегда — иначе один упавший процесс превратил бы третий голос в молчание."""
    path = path or LOCK_FILE
    now = time.time() if now is None else now
    stale = budget_sec() + 600 if stale is None else stale
    payload = json.dumps({"pid": os.getpid(), "ts": now}, ensure_ascii=False)
    try:
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
    except (OSError, ValueError, TypeError):
        age = stale + 1                                     # нечитаемый лок = стухший
        held = {}
    if not held.get("pid"):
        age = stale + 1                                     # снятый лок (release) — свободен сразу
    if age <= stale:
        return False, "замер уже идёт (pid %s, %.0fс) — урок склеится с ним" % (held.get("pid"), age)
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(payload)
    except OSError as e:
        return False, "лок не отобран: %s" % e
    return True, "стухший лок отобран (возраст %.0fс)" % age


def release(path=None):
    """Снять лок. Файл НЕ удаляем содержимым-в-никуда: перезаписываем пустой меткой — уборка за
    собой запрещена, а пустая метка честно говорит «свободно» (возраст нулевой не мешает: чтение
    ищет `pid`, а его там нет)."""
    path = path or LOCK_FILE
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": None, "ts": 0}, ensure_ascii=False))
        return True
    except OSError:
        return False


def _lock_free(path=None, now=None, stale=None):
    """Свободен ли лок (для --status; отдельная функция, чтобы не брать его ради вопроса)."""
    path = path or LOCK_FILE
    now = time.time() if now is None else now
    stale = budget_sec() + 600 if stale is None else stale
    try:
        with io.open(path, encoding="utf-8") as f:
            held = json.load(f)
    except (OSError, ValueError):
        return True
    if not held.get("pid"):
        return True
    try:
        return (now - float(held.get("ts") or 0)) > stale
    except (TypeError, ValueError):
        return True


# ─────────────────────────────────────── замер ───────────────────────────────────────────────

def _case_row(res):
    """Строка кейса из результата прогона: что зелено, что красно и ЧЕМ красно (сам чек)."""
    green, red = [], []
    for c in res.get("checks") or []:
        if c.get("skipped"):
            continue                                        # снят с причиной — в счёт не идёт
        (green if c.get("ok") else red).append(c)
    return {"ok": bool(res.get("ok")) and not res.get("unknown"),
            "checks": [c.get("name") for c in green],
            "red": [{"name": c.get("name"), "expected": str(c.get("expected"))[:160],
                     "fact": str(c.get("fact"))[:160]} for c in red]}


def _rows(results):
    """Результаты прогона → {id: строка кейса}. Кейс красен, если красен хоть в одном прогоне."""
    rows = {}
    for r in results or []:
        cid = str(r.get("id"))
        row = _case_row(r)
        old = rows.get(cid)
        if old is None:
            rows[cid] = row
            continue
        old["ok"] = old["ok"] and row["ok"]
        seen = {c["name"] for c in old["red"]}
        old["red"].extend([c for c in row["red"] if c["name"] not in seen])
    return rows


def measure(runner=None, only=None, log=None, now=None, deadline=None):
    """ОДИН замер корпуса: скрининг (1 прогон) + подтверждение красных (ещё 1 прогон ТОЛЬКО по
    красным кейсам). → dict(ok, why, unknown, cases, cases_total, cases_ok, checks_ok, checks_all,
    commit, head_moved, corpus_sha, sec, flaked).

    `ok=False` означает «замера нет» (инфраструктура), а НЕ «корпус красный»: красный корпус —
    это `ok=True` с красными строками в `cases`. Разница ровно в том, за что отвечает третий голос.
    `runner` инъектируется в тестах; иначе боевой `trainer_run` (импорт ЛЕНИВЫЙ — этот модуль
    импортируется из `trainer` в пути урока и не имеет права тащить туда весь пайплайн)."""
    t0 = time.time() if now is None else now
    log = log or (lambda *_a, **_k: None)
    if runner is None:
        try:
            import trainer_run as runner                    # noqa: PLC0415 — см. докстринг
        except Exception as e:                              # noqa: BLE001
            return {"ok": False, "why": "прогонщик не импортировался (%s: %s)" % (type(e).__name__, e),
                    "unknown": [], "cases": {}, "sec": 0.0}
    try:
        bind = runner.bind_head()
        commit = str(bind.get("commit") or "")
        cases, sha = runner.load_cases()
        total = len(cases)
        if only:
            want = {str(s).strip() for s in only if str(s).strip()}
            cases = [c for c in cases if str(c.get("id")) in want]
        if not cases:
            return {"ok": False, "why": "корпус пуст — сравнивать нечего", "unknown": [],
                    "cases": {}, "sec": 0.0}
        ph = runner.placeholders()
        results, _passed, ok_c, all_c, _failed, unknown = runner.run_corpus(cases, runs=1, ph=ph,
                                                                           log=log)
    except Exception as e:                                  # noqa: BLE001 — граница, а не продукт
        return {"ok": False, "why": "прогон оборвался (%s: %s)" % (type(e).__name__, e),
                "unknown": [], "cases": {}, "sec": round((time.time() - t0), 1)}
    if unknown:
        # МОЛЧАЩАЯ ГОЛОВА / НЕДОСТУПНАЯ ГРАНИЦА = НЕИЗВЕСТНО (правило `trainer_run`, 22.08).
        # Зелёного это не даёт никому и красным никого не называет.
        return {"ok": False, "why": "судить нечего: " + "; ".join(str(u) for u in unknown[:4]),
                "unknown": list(unknown), "cases": {}, "sec": round(time.time() - t0, 1),
                "commit": commit}
    rows = _rows(results)
    red_ids = [cid for cid, row in rows.items() if not row["ok"]]
    flaked = []
    if red_ids and (deadline is None or time.time() < deadline):
        # ПОДТВЕРЖДЕНИЕ: генератор недетерминирован, один прогон красное не доказывает. Ходим
        # ТОЛЬКО по красным — цена подтверждения пропорциональна беде, а не корпусу.
        try:
            again = [c for c in cases if str(c.get("id")) in set(red_ids)]
            res2, _p, ok2, all2, _f, unk2 = runner.run_corpus(again, runs=1, ph=ph, log=log)
        except Exception as e:                              # noqa: BLE001
            return {"ok": False, "why": "подтверждение красных оборвалось (%s: %s)"
                                        % (type(e).__name__, e),
                    "unknown": [], "cases": {}, "sec": round(time.time() - t0, 1),
                    "commit": commit}
        if unk2:
            return {"ok": False, "why": "подтверждение красных: судить нечего: "
                                        + "; ".join(str(u) for u in unk2[:4]),
                    "unknown": list(unk2), "cases": {}, "sec": round(time.time() - t0, 1),
                    "commit": commit}
        rows2 = _rows(res2)
        ok_c += ok2
        all_c += all2
        for cid in red_ids:
            if rows2.get(cid, {}).get("ok"):
                flaked.append(cid)                          # мигнул: во втором прогоне зелен
                rows[cid] = rows2[cid]
    try:
        runner.verify_head(bind)
    except Exception:                                       # noqa: BLE001 — сверка вершины не судья
        pass
    return {"ok": True, "why": "", "unknown": [], "cases": rows,
            "cases_total": total, "cases_seen": len(rows),
            "cases_ok": sum(1 for r in rows.values() if r["ok"]),
            "checks_ok": ok_c, "checks_all": all_c,
            "commit": commit, "head_moved": str(bind.get("head_moved") or ""),
            "corpus_sha": sha, "sec": round(time.time() - t0, 1), "flaked": flaked}


# ─────────────────────────────────────── эталон и сравнение ──────────────────────────────────

def baseline(state=None, corpus_sha=None, gate=None, path=None):
    """Последний известный результат — ЧИСЛОМ. → (эталон|None, откуда).

    Порядок: свой файл состояния → (если своего нет) ЧТЕНИЕ реестра ворот, только при совпавшем
    `corpus_sha`. Реестр ворот здесь читается и НИКОГДА не пишется."""
    state = read_state(path) if state is None else state
    last = state.get("last")
    if isinstance(last, dict) and last.get("cases"):
        return last, "свой эталон от %s" % (last.get("when") or "?")
    if gate is None:
        try:
            import client_contour                           # noqa: PLC0415 — ленивый, см. шапку
            with io.open(client_contour.TRAINER_GREEN_FILE, encoding="utf-8") as f:
                gate = json.load(f)
        except Exception:                                   # noqa: BLE001
            gate = None
    rec = (gate or {}).get("last") if isinstance(gate, dict) else None
    if not isinstance(rec, dict) or not rec.get("cases_total"):
        return None, "эталона нет"
    if corpus_sha and str(rec.get("corpus_sha") or "") != str(corpus_sha):
        return None, "запись ворот снята на другом корпусе (%s) — эталоном не берём" % rec.get("corpus_sha")
    red = {}
    for item in rec.get("failed") or []:
        head, _sp, name = str(item).partition(" ")
        cid = head.split("/")[0]
        red.setdefault(cid, []).append({"name": name, "expected": "", "fact": ""})
    cases = {}
    for cid, checks in red.items():
        cases[cid] = {"ok": False, "checks": [], "red": checks}
    # зелёные кейсы записи ворот поимённо не перечислены — берём их из корпуса-на-диске:
    # всё, что не названо красным в записи, было зелёным (иначе оно попало бы в `failed`).
    return ({"cases": cases, "cases_total": rec.get("cases_total"), "cases_ok": rec.get("cases"),
             "checks_ok": rec.get("checks_passed"), "checks_all": rec.get("checks_total"),
             "commit": rec.get("commit"), "corpus_sha": rec.get("corpus_sha"),
             "when": rec.get("when"), "outcome": rec.get("result"), "green_implicit": True},
            "эталон прочитан из реестра ворот (%s, %s)" % (rec.get("when"), rec.get("result")))


def _was_green(base, cid):
    """Был ли кейс зелёным в эталоне. У записи ворот зелёные поимённо не перечислены — там
    «не назван красным» И значит зелёным (иначе он был бы в `failed`)."""
    row = (base.get("cases") or {}).get(cid)
    if isinstance(row, dict):
        return bool(row.get("ok"))
    return bool(base.get("green_implicit"))


def compare(now_rec, base):
    """Сравнение замера с эталоном. → dict(verdict, broken, fixed, why).

    verdict: 'unknown' — замера нет; 'base' — сравнивать не с чем (эталон записан впервые);
    'broken' — есть кейс, который БЫЛ зелёным и стал красным; 'ok' — таких нет."""
    if not now_rec.get("ok"):
        return {"verdict": "unknown", "broken": [], "fixed": [],
                "why": now_rec.get("why") or "замер не состоялся"}
    if not base:
        return {"verdict": "base", "broken": [], "fixed": [], "why": "эталона не было"}
    broken, fixed = [], []
    for cid, row in sorted((now_rec.get("cases") or {}).items(), key=lambda kv: _num(kv[0])):
        was = _was_green(base, cid)
        if row["ok"]:
            if not was:
                fixed.append(cid)
            continue
        if not was:
            continue                                        # был красным и остался — не регрессия
        # ПЕРЕСТАЛ ПРОХОДИТЬ — называем САМ ЧЕК. Если эталон перечислил зелёные чеки поимённо,
        # вперёд ставим те, что в нём были зелёными (именно они перестали); прочие красные идут
        # следом и не теряются — «новый чек упал» тоже новость.
        prev = set(((base.get("cases") or {}).get(cid) or {}).get("checks") or [])
        stopped = [c for c in row["red"] if c["name"] in prev]
        rest = [c for c in row["red"] if c["name"] not in prev]
        broken.append({"id": cid, "checks": (stopped + rest) or row["red"]})
    return {"verdict": "broken" if broken else "ok", "broken": broken, "fixed": fixed, "why": ""}


def _num(cid):
    try:
        return (0, int(str(cid)))
    except (TypeError, ValueError):
        return (1, 0)


# ─────────────────────────────────────── строка исхода ───────────────────────────────────────

def _act(les):
    """Движение одного урока: `записан` (умолчание — так писали до 06.09) или `снят`."""
    a = str((les or {}).get("act") or "").strip()
    return a if a in (ACT_ADDED, ACT_WITHDRAWN) else ACT_ADDED


def _lesson_tag(lessons):
    """«Урок #12 записан» / «Урок #12 снят» / склейка — по числу и ПО ДВИЖЕНИЮ склеенных.

    Смешанная склейка (одно записали, другое сняли) называет движение У КАЖДОГО номера: одно
    общее слово на такую пару соврало бы про половину прогона, а прибор и так не знает, которое
    из движений сломало корпус."""
    items = [l for l in (lessons or []) if l.get("n") is not None]
    if not items:
        return "Урок записан"
    if len(items) == 1:
        return "Урок #%s %s" % (items[0]["n"], _act(items[0]))
    acts = {_act(l) for l in items}
    nums = [str(l["n"]) for l in items]
    if len(acts) == 1:
        word = "записаны" if acts == {ACT_ADDED} else "сняты"
        return "Уроки #%s %s (склейка %d в один прогон)" % (",#".join(nums), word, len(items))
    pairs = ", ".join("#%s (%s)" % (l["n"], _act(l)) for l in items)
    return "Уроки %s — склейка %d в один прогон" % (pairs, len(items))


def outcome_line(cmp_res, now_rec, lessons=None, base_from=""):
    """ОДНА короткая строка владельцу — тремя голосами. Третий не заменяется молчанием."""
    tag = _lesson_tag(lessons)
    glued = len([l for l in (lessons or []) if l.get("n") is not None]) > 1
    v = cmp_res.get("verdict")
    if v == "unknown":
        line = ("❔ %s · регрессия корпуса НЕ СОСТОЯЛАСЬ: %s → НЕИЗВЕСТНО, сломал ли урок корпус "
                "(это не «всё хорошо»). Повторю на следующем уроке; проверить руками: "
                "venv/Scripts/python.exe trainer_run.py --runs 1 --no-write"
                % (tag, cmp_res.get("why") or "причина не названа"))
    elif v == "base":
        line = ("❔ %s · регрессия корпуса: %d/%d кейсов, %d/%d чеков — но сравнивать НЕ С ЧЕМ "
                "(эталона не было, база записана). Сравнение — со следующего урока."
                % (tag, now_rec.get("cases_ok", 0), now_rec.get("cases_seen", 0),
                   now_rec.get("checks_ok", 0), now_rec.get("checks_all", 0)))
    elif v == "broken":
        parts = []
        for b in cmp_res["broken"][:3]:
            chk = (b["checks"] or [{}])[0]
            parts.append("#%s чек «%s» (ждали: %s · факт: %s)"
                         % (b["id"], chk.get("name") or "?",
                            (chk.get("expected") or "?")[:70], (chk.get("fact") or "?")[:70]))
        more = len(cmp_res["broken"]) - len(parts)
        items = [l for l in (lessons or []) if l.get("n") is not None]
        last = items[-1] if items else {}
        # ЧТО ДЕЛАТЬ С КРАСНЫМ — ЗАВИСИТ ОТ ДВИЖЕНИЯ. Совет «снять — отмени урок N» после ОТМЕНЫ
        # звал бы снимать уже снятое; обратной команды («вернуть урок N») у полосы нет вовсе, и
        # прибор об этом ГОВОРИТ, а не советует несуществующее.
        if _act(last) == ACT_WITHDRAWN:
            tail = ("Отмена НЕ откатана: строка #%s в базе помечена снятой, и вернуть её "
                    "командой сегодня нечем — это отдельное движение." % last.get("n"))
        else:
            tail = "Урок НЕ снят: снять — «отмени урок %s»." % (last.get("n") or "N")
        line = ("🔴 %s · регрессия корпуса: СЛОМАЛОСЬ %d кейсов из %d — %s%s. %s"
                % (tag, len(cmp_res["broken"]), now_rec.get("cases_seen", 0), "; ".join(parts),
                   (" и ещё %d" % more) if more > 0 else "", tail))
        if glued:
            line += " Который из склеенных сломал — прибор не знает: мерилась книга целиком."
        if now_rec.get("head_moved"):
            line += " NB: HEAD уехал за прогон (%s) — красное могло прийти и от кода." % now_rec["head_moved"]
    else:
        line = ("✅ %s · регрессия корпуса: %d/%d кейсов, %d/%d чеков — ничего не сломалось (%.0f мин)."
                % (tag, now_rec.get("cases_ok", 0), now_rec.get("cases_seen", 0),
                   now_rec.get("checks_ok", 0), now_rec.get("checks_all", 0),
                   float(now_rec.get("sec") or 0) / 60.0))
        if now_rec.get("flaked"):
            line += " Мигнули и подтвердились со второго прогона: %s." % ", ".join(now_rec["flaked"])
        if cmp_res.get("fixed"):
            line += " Позеленело: %s." % ", ".join(cmp_res["fixed"])
    if now_rec.get("glue_exhausted"):
        line += (" NB: уроки шли быстрее корпуса — замер сделан по книге НА МОМЕНТ ПРОГОНА, "
                 "последние правила в нём не мерились.")
    if base_from and v in ("ok", "broken"):
        line += " [эталон: %s]" % base_from
    line = " ".join(line.split())
    return line if len(line) <= LINE_MAX else line[:LINE_MAX - 1] + "…"


def say(line, popen=None):
    """ОДНА строка владельцу штатным каналом полосы (dispatch_notify → deliver выбирает адрес).
    Fire-and-forget: недоставленная строка не имеет права ронять замер. → bool «отправили»."""
    try:
        (popen or subprocess.Popen)([VENV_PY, DNOTIFY, str(line)], cwd=REPO,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return True
    except Exception:                                       # noqa: BLE001 — см. докстринг
        return False


# ─────────────────────────────────────── запуск из урока ─────────────────────────────────────

def spawn(rule, n=None, popen=None, env=None, path=None, now=None, act=None):
    """ЗАПУСК ИЗ НАЖАТИЯ ВЛАДЕЛЬЦА — и единственное, что здесь важно, это НЕ ЖДАТЬ.

    Отсоединённый процесс (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW),
    без ожидания, без чтения потоков: урок пишется и карточка уходит владельцу, как раньше.
    `act` — движение владельца (`записан` по умолчанию, `снят` у отмены урока): едет в пометку
    склейки и оттуда в строку исхода.
    НИКОГДА не бросает — предсмертный взгляд задания («регрессию повесят внутрь нажатия») закрыт
    именно здесь. → dict(spawned, why)."""
    why = off(env)
    if why:
        return {"spawned": False, "why": why}
    try:
        note_pending(n, rule, path=path, now=now, act=act)   # склейка возможна и до старта ребёнка
        (popen or subprocess.Popen)([VENV_PY, SELF, "--after-lesson"], cwd=REPO,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, creationflags=DETACHED)
        return {"spawned": True, "why": ""}
    except Exception as e:                                   # noqa: BLE001 — см. докстринг
        return {"spawned": False, "why": "%s: %s" % (type(e).__name__, e)}


def after_lesson(runner=None, only=None, log=None, path=None, say_fn=None, deadline=None,
                 max_glued=MAX_GLUED, lock=None):
    """Тело отсоединённого процесса: одиночка → замер → склейка → сравнение → ОДНА строка.

    → dict(ran, verdict, line, lessons, glued, why). `ran=False` при незанятом локе (наш урок
    склеится с идущим замером — это не потеря ответа, а его объединение)."""
    log = log or (lambda *_a, **_k: None)
    took, why = acquire(lock)
    if not took:
        return {"ran": False, "verdict": "", "line": "", "lessons": [], "glued": 0, "why": why}
    deadline = (time.time() + budget_sec()) if deadline is None else deadline
    lessons, glued, rec, cmp_res, base_from = [], 0, None, None, ""
    try:
        lessons = take_pending(path)
        while True:
            rec = measure(runner=runner, only=only, log=log, deadline=deadline)
            fresh = take_pending(path)
            if not fresh:
                break
            lessons.extend(fresh)
            if glued >= max_glued or time.time() >= deadline:
                # СКЛЕЙКА НЕ БЕСКОНЕЧНА. Уроки идут быстрее, чем корпус успевает, — считаем замер
                # по книге, какая была на прогоне, и ГОВОРИМ об этом: молча выдать вчерашнее число
                # за сегодняшнее — то же враньё, что молчание третьего голоса.
                log("склейка исчерпана (%d): уроки продолжают приходить" % glued)
                rec["glue_exhausted"] = True
                break
            glued += 1
        base, base_from = baseline(corpus_sha=(rec or {}).get("corpus_sha"), path=path)
        cmp_res = compare(rec, base)
        _save(rec, cmp_res, lessons, path=path)
        line = outcome_line(cmp_res, rec, lessons, base_from=base_from)
        (say_fn or say)(line)
        return {"ran": True, "verdict": cmp_res["verdict"], "line": line, "lessons": lessons,
                "glued": glued, "why": ""}
    except Exception as e:                                   # noqa: BLE001 — третий голос вместо тишины
        line = outcome_line({"verdict": "unknown",
                             "why": "внутренняя ошибка %s: %s" % (type(e).__name__, e)},
                            rec or {}, lessons)
        (say_fn or say)(line)
        return {"ran": True, "verdict": "unknown", "line": line, "lessons": lessons,
                "glued": glued, "why": "%s: %s" % (type(e).__name__, e)}
    finally:
        release(lock)


def _save(rec, cmp_res, lessons, path=None):
    """Эталон обновляем ТОЛЬКО состоявшимся замером: несостоявшийся не имеет права стереть
    последнее известное число — иначе следующий урок сравнивался бы с пустотой."""
    d = read_state(path)
    hist = d.get("history")
    hist = hist if isinstance(hist, list) else []
    hist.append({"ts": time.time(), "verdict": cmp_res.get("verdict"),
                 "cases_ok": rec.get("cases_ok"), "cases_seen": rec.get("cases_seen"),
                 "checks_ok": rec.get("checks_ok"), "checks_all": rec.get("checks_all"),
                 "sec": rec.get("sec"), "why": (cmp_res.get("why") or "")[:200],
                 "lessons": [l.get("n") for l in (lessons or [])],
                 "broken": [b["id"] for b in cmp_res.get("broken") or []]})
    d["history"] = hist[-HISTORY_KEEP:]
    if rec.get("ok"):
        d["last"] = {"ts": time.time(),
                     "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "outcome": cmp_res.get("verdict"),
                     "commit": rec.get("commit"), "corpus_sha": rec.get("corpus_sha"),
                     "cases": rec.get("cases"), "cases_total": rec.get("cases_total"),
                     "cases_seen": rec.get("cases_seen"), "cases_ok": rec.get("cases_ok"),
                     "checks_ok": rec.get("checks_ok"), "checks_all": rec.get("checks_all"),
                     "sec": rec.get("sec"),
                     "lessons": [{"n": l.get("n"), "rule": l.get("rule")} for l in (lessons or [])]}
    return write_state(d, path)


# ─────────────────────────────────────── CLI ─────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="регрессия урока: после урока корпус прогоняется "
                                             "и говорит исход")
    ap.add_argument("--after-lesson", action="store_true", help="тело отсоединённого процесса")
    ap.add_argument("--status", action="store_true", help="что лежит в эталоне (ничего не гоняет)")
    ap.add_argument("--dry", action="store_true", help="прогнать и НЕ отправлять строку владельцу")
    ap.add_argument("--only", default="", help="через запятую: id кейсов (разведка механизма)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001 — старый поток
        pass
    if a.status:
        st = read_state()
        base, frm = baseline(state=st)
        print("эталон: %s" % frm)
        if base:
            print("  кейсов %s/%s, чеков %s/%s, снят %s, коммит %s"
                  % (base.get("cases_ok"), base.get("cases_seen") or base.get("cases_total"),
                     base.get("checks_ok"), base.get("checks_all"), base.get("when"),
                     str(base.get("commit"))[:7]))
        print("очередь склейки: %d" % len(st.get("pending") or []))
        print("замер: %s" % ("свободно" if _lock_free() else "ИДЁТ"))
        for h in (st.get("history") or [])[-5:]:
            print("  · %s кейсов %s/%s чеков %s/%s %.0fс уроки %s"
                  % (h.get("verdict"), h.get("cases_ok"), h.get("cases_seen"), h.get("checks_ok"),
                     h.get("checks_all"), float(h.get("sec") or 0), h.get("lessons")))
        return 0
    only = [s for s in a.only.split(",") if s.strip()] if a.only else None
    if a.dry:
        rec = measure(only=only, log=print)
        base, frm = baseline(corpus_sha=rec.get("corpus_sha"))
        cmp_res = compare(rec, base)
        print("\nИСХОД: " + outcome_line(cmp_res, rec, [], base_from=frm))
        print("(--dry: строка владельцу НЕ отправлена, эталон НЕ переписан)")
        return 0 if cmp_res["verdict"] in ("ok", "base") else 1
    got = after_lesson(log=print)
    print(got.get("line") or ("замер не запускался: " + str(got.get("why"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
