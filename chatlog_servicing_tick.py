# -*- coding: utf-8 -*-
"""
chatlog_servicing_tick.py — ЧАСЫ архива форума Обслуживания (30.09.2026, задание 0065-75h, SVCCLOCK3009).

ЧЕГО НЕ ХВАТАЛО. Захват форума (`chatlog_servicing_fetch`) построен 23.09 и отработал один раз
руками; вызывающего у него не было нигде, и архив `chatlog/servicing/` отстал на 7 суток
(AIMGRTOPIC3009). Соседний `chatlog_ingest --tick` форум не обновляет: он сети не знает и лишь
переносит то, что боевые процессы уже положили на диск, а форум на диск не пишет никто.

ЧТО ДЕЛАЕТ. Демон зовёт этот файл ОТДЕЛЬНЫМ ПРОЦЕССОМ каждый оборот (`pc_orchestrator._chatlog_capture`),
а идти ли в Telegram — решает СВОЙ штамп: не чаще раза в `EVERY_MIN` (720 мин = два захода в
сутки). Заход — тот же захват по КОПИИ сессии, что у `chatlog_servicing_fetch` (его же функция
`run`): живой файл сессии только читается, `client.start()` не зовётся, отправок нет.

ИСХОД КАЖДОГО ЗАХОДА — В ШТАМП: ок и сколько строк, ошибка и какая, пропуск и почему. Строку в
журнал демона пишет сам демон, читая этот штамп (`pc_orchestrator._svc_clock_lines`): ребёнок
живёт с stdout в никуда, и его собственная печать не увидена бы никем.

ОШИБКА — БЕЗ ПОВТОРОВ ДО СЛЕДУЮЩЕГО СРОКА. Заявка ставится в штамп ДО захода, поэтому упавший
заход не открывает срок заново: следующая попытка — через `EVERY_MIN` от заявки.

ЗАМОК ОБЩИЙ С `chatlog_ingest`. Оба захвата дописывают один индекс `_index/<месяц>.tsv` и
переписывают один манифест; два процесса, пишущие одновременно, уже стоили архиву 1118 дублей и
двух битых файлов (замер 22.09). Поэтому заход держит тот же файл-замок, что и `chatlog_ingest.tick`;
занят — заход откладывается на следующий оборот без заявки (это не отказ, а очередь).

ОТКАТ. `CHATLOG_SERVICING_TICK_OFF=1` — заход не ходит в Telegram ни разу, в штамп раз в период
ложится «пропуск: выключено флагом». Флаг читается из окружения И ЗАНОВО из файла `.env` на каждом
обороте (ключ в файле главнее унаследованного), поэтому строка в `.env` действует со следующего
оборота демона, без рестарта. Общий `CHATLOG_TICK_OFF=1` гасит и эти часы.

НАРУЖУ — ТОЛЬКО ЧИСЛА И ДАТЫ. Ни текста сообщений, ни имён: в штамп идёт счёт, даты окна и ИМЯ
КЛАССА исключения (`str(e)` не берётся — сообщение исключения может цитировать разбираемое).

Запуск (зовёт демон): venv\\Scripts\\python.exe chatlog_servicing_tick.py
"""

import io
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))

STATE_ENV = "CHATLOG_SERVICING_TICK_STATE"
DEFAULT_STATE = os.path.join(HERE, "tmp", "chatlog_servicing_tick", "state.json")
EVERY_ENV = "CHATLOG_SERVICING_TICK_EVERY_MIN"
DEFAULT_EVERY_MIN = 720
# Пол периода: опечатка «7» вместо «720» не должна превращать часы в долбёжку Telegram по копии
# сессии каждые три минуты — худший случай этого захода (разлогин юзербота) дороже любой свежести.
MIN_EVERY_MIN = 60
OFF_ENV = "CHATLOG_SERVICING_TICK_OFF"
COMMON_OFF_ENV = "CHATLOG_TICK_OFF"
_ON = ("1", "true", "yes", "on", "да")

# Коды возврата `chatlog_servicing_fetch.run` → слова. Ключи — ровно те, что он возвращает.
CODE_WORDS = {
    2: "нет API_ID/API_HASH в окружении — Telegram не открыть",
    3: "не авторизованы по копии сессии — входить не стали",
    4: "форум не найден среди диалогов юзербота",
    5: "потолок захода исчерпан — архив не записан",
}


def state_path(path=None):
    return path or (os.getenv(STATE_ENV) or "").strip() or DEFAULT_STATE


def read_state(path=None):
    try:
        with io.open(state_path(path), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def write_state(state, path=None):
    path = state_path(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, path)


def every_min(raw=None):
    """Период из окружения → минуты. Мусор → дефолт; меньше пола → пол; 0 и ниже → 0 (выключено)."""
    raw = os.getenv(EVERY_ENV) if raw is None else raw
    try:
        m = int(str(raw).strip()) if raw not in (None, "") else DEFAULT_EVERY_MIN
    except ValueError:
        return DEFAULT_EVERY_MIN
    if m <= 0:
        return 0
    return max(m, MIN_EVERY_MIN)


def due(prev, now, every):
    """→ bool. Наступил ли срок захода. ЧИСТАЯ: только счёт (копия `chatlog_ingest.due`)."""
    last = float((prev or {}).get("ran_at") or 0)
    if not last:
        return True          # штампа нет — захода ещё не было ни разу
    # Часы уехали назад (сон, перевод времени) — отрицательная разница не запирает часы навсегда.
    return (now - last) >= every * 60 or (now - last) < 0


def off_reason(environ=None, file_env=None):
    """→ слова пропуска или None. Ключ из файла `.env` главнее унаследованного окружения."""
    env = os.environ if environ is None else environ
    fe = {} if file_env is None else file_env
    for key in (OFF_ENV, COMMON_OFF_ENV):
        raw = fe[key] if key in fe else env.get(key)
        if str(raw or "").strip().lower() in _ON:
            return "выключено флагом " + key
    return None


def _file_env():
    """Свежие значения ДВУХ флагов из `.env` (тот же парсер, что у load_dotenv). Остальное не берём."""
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(os.path.join(HERE, ".env")) or {}
    except Exception:
        return {}
    return {k: vals[k] for k in (OFF_ENV, COMMON_OFF_ENV, EVERY_ENV) if k in vals}


def outcome_of(code, res):
    """(код, итог захвата) → поля исхода. Только числа и даты окна, текста нет ни в одной ветке."""
    if code != 0 or not isinstance(res, dict):
        words = CODE_WORDS.get(code, "захват вернул код %s без итога" % code)
        return {"outcome": "error", "error": "код %s" % code, "why": words}
    win = res.get("window") or {}
    return {"outcome": "ok", "error": None, "why": None,
            "written": int(res.get("written") or 0), "dup": int(res.get("dup") or 0),
            "messages": int(res.get("messages") or 0), "topics": int(res.get("topics") or 0),
            "saved": int(res.get("saved") or 0), "not_downloaded": int(res.get("not_downloaded") or 0),
            "newest_utc": win.get("newest_utc"), "oldest_utc": win.get("oldest_utc")}


def _default_runner():
    """Боевой заход: ровно `chatlog_servicing_fetch.run` с его окном и потолком. → (код, итог)."""
    import asyncio
    import chatlog_servicing as sv
    import chatlog_servicing_fetch as fetch
    try:
        return asyncio.run(asyncio.wait_for(
            fetch.run(sv.WINDOW_DAYS, sv.WINDOW_LIMIT, False, sv.BUDGET_S), fetch.RUN_TIMEOUT_S))
    except asyncio.TimeoutError:
        return 5, None


def tick(now=None, path=None, runner=None, environ=None, file_env=None, every=None,
         lock_file=None, clock=time.time):
    """Один оборот часов. → dict с исходом (только числа). Решение о заходе — здесь, не у демона."""
    now = clock() if now is None else now
    fe = _file_env() if file_env is None else file_env
    env = os.environ if environ is None else environ
    if every is None:
        every = every_min(fe[EVERY_ENV] if EVERY_ENV in fe else env.get(EVERY_ENV))
    prev = read_state(path)
    if not due(prev, now, every or DEFAULT_EVERY_MIN):
        return {"ran": False, "why": "рано", "next_at": prev.get("next_at")}
    import chatlog_ingest as ingest          # замок ОДИН на всех писателей архива (см. шапку)
    lp = lock_file or ingest.lock_path()
    if not ingest.lock_take(lp, now):
        return {"ran": False, "why": "замок архива занят — заход на следующем обороте"}
    try:
        new = dict(prev)
        new["seq"] = int(prev.get("seq") or 0) + 1
        new["ran_at"] = now                  # ЗАЯВКА: до захода — ошибка не откроет срок заново
        new["every_min"] = every or DEFAULT_EVERY_MIN
        new["next_at"] = now + new["every_min"] * 60
        skip = off_reason(env, fe) or (None if every else "период 0 — часы выключены")
        if skip:
            new.update({"done_seq": new["seq"], "done_at": now, "outcome": "skip", "why": skip,
                        "error": None, "running": False, "skips": int(prev.get("skips") or 0) + 1})
            write_state(new, path)
            return {"ran": False, "seq": new["seq"], "outcome": "skip", "why": skip}
        new["running"] = True                # поля исхода остаются за прошлым заходом (№ done_seq)
        write_state(new, path)
        t0 = clock()
        try:
            code, res = (runner or _default_runner)()
            got = outcome_of(code, res)
        except Exception as e:
            got = {"outcome": "error", "error": type(e).__name__,   # ИМЯ КЛАССА, не текст
                   "why": "исключение " + type(e).__name__}
        for k in ("written", "dup", "messages", "topics", "saved", "not_downloaded",
                  "newest_utc", "oldest_utc"):
            new.pop(k, None)                 # счёт прошлого захода не выдаётся за счёт этого
        new.update(got)
        new["running"] = False
        new["done_seq"] = new["seq"]
        new["done_at"] = clock()
        new["duration_s"] = round(max(0.0, new["done_at"] - t0), 1)
        if got["outcome"] == "ok":
            new["ok_runs"] = int(prev.get("ok_runs") or 0) + 1
            new["total_written"] = int(prev.get("total_written") or 0) + got["written"]
        else:
            new["errors"] = int(prev.get("errors") or 0) + 1
        write_state(new, path)
        out = {"ran": True, "seq": new["seq"], "outcome": got["outcome"]}
        out.update({k: got[k] for k in ("written", "error") if k in got})
        return out
    finally:
        ingest.lock_drop(lp)


def main():
    res = tick()
    print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main())
