# -*- coding: utf-8 -*-
"""ДОЛЯ ГАШЕНИЯ ЦЕНЫ — РУКИ прибора (заведено 12.09.2026, задание 42-a).

Решение живёт в `price_quench_pc.py` и мира не трогает; здесь — только мир: собрать ценовую
записку боевым путём продукта, записать цепочку вызовов, назвать дорогу и положить наблюдение
в состояние. Ни одного порога и ни одного вердикта в этом файле нет.

ОТКУДА ВЗЯТО. Это ПЕРЕНОС зонда `tmp/zond_pyat_sborok_0911/zond.py` (артефакт
`docs/artifacts/2026-09-11-ЗОНД-pyat-sborok-1109.md`), а не вторая его реализация: замер зонда
не переделан, его записывающие обёртки перенесены сквозными, как были. Разница ровно одна —
зонд жил в `tmp/` и прибором не являлся (проверено 12.09: `git ls-files` по его папке → 0,
`tmp/` в `.gitignore:81`), а этот файл лежит в репозитории и зовётся периодически.

КАК ЗАПУСКАЕТСЯ:
    venv\\Scripts\\python.exe price_quench_pc_probe.py --tick       # оборот с учётом своего пола
    venv\\Scripts\\python.exe price_quench_pc_probe.py --force      # мерить сейчас, пол не спрашивать
    venv\\Scripts\\python.exe price_quench_pc_probe.py --status     # словами, в сеть НЕ ходит
    venv\\Scripts\\python.exe price_quench_pc_probe.py --dry        # решение без записи состояния
    venv\\Scripts\\python.exe price_quench_pc_probe.py --negative   # отрицательный тест руками

ЧАСТОТУ РЕШАЕТ ПРИБОР, А НЕ ЗВОНЯЩИЙ (форма `srv_delivery.py`): демон зовёт `--tick` каждый
оборот, а в сеть прибор идёт по своему полу `PRICE_QUENCH_EVERY_MIN` (30 минут). Поэтому частота
вызова отсюда на число живых котировок не влияет.

ЧЕГО НЕ ДЕЛАЕТ НИ ОДНОЙ ВЕТКОЙ: головы (`generate_draft`) не зовёт; в `moderation_ipc` не ходит
(ценовой путь её не касается — проверено грепом по `pricing`/`price_gate`/`price_source`/
`season_gate`/`noprice_gate`); наружу не шлёт ничего (замок ниже); процессов не трогает; в
очередь не пишет. На диск пишет РОВНО один свой файл состояния.

ЗАМОК «НАРУЖУ НЕ УХОДИТ НИЧЕГО»: у `noprice_gate`/`season_gate` подменён ТОЛЬКО адресат карточки
(`_default_sender`) — логика записки не меняется ни байтом, возврат `raise_card` и так никем не
читается. Задержанные карточки считаются и кладутся в наблюдение ЧИСЛОМ, а не текстом.
"""
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                                     # noqa: BLE001
    pass

import price_quench_pc as pq                                          # noqa: E402

STATE_DIR = os.path.join(REPO, "tmp", "price_quench_pc")
STATE_FILE = "state.json"

# ПРОБА НАЗВАНА ЗДЕСЬ И ЕДЕТ В КАЖДОЕ НАБЛЮДЕНИЕ. Смена пробы — смена серии, и она обязана быть
# ВИДНА: два разных вопроса, сложенные в одну долю, дают число, которое не значит ничего.
PROBE_MODEL = os.environ.get("PRICE_QUENCH_MODEL", "NMAX 155")
# Фраза НАША, а не клиентская: текста живых диалогов в приборе нет ни строки (запрет задания).
# ФОРМА ТРАНСКРИПТА ОБЯЗАТЕЛЬНА, И ЭТО ИЗМЕРЕНО, А НЕ УГАДАНО: на голой строке без роли
# `detect_lang_from_client` дал `ru` на английской фразе, а `extract_booking_hints` — ни модели,
# ни дат (замер 12.09, первый прогон отрицательного теста). Ценовой путь при этом не вызывался
# ВООБЩЕ, и прибор честно сказал «неизвестно» — ровно так отрицательный тест и окупился.
# Разметка — дословно `trainer.append_turn`: `[клиент]: …`.
PROBE_ASK_EN = "[клиент]: Hi! How much is the %s %s? Is it available for those dates?"
# Окно считается ПО ТОМУ ЖЕ ПРАВИЛУ, что у `trainer_run.placeholders`: следующий месяц, 6→11.
# Копия, а не импорт, намеренно: прибор не вправе менять свой вопрос оттого, что поменялся
# корпус тренажёра. Правило скопировано, число — нет.
PROBE_DAY_FROM, PROBE_DAY_TO = 6, 11
_MONTHS_EN = ("", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December")


def _dir():
    return os.environ.get("CC_PRICE_QUENCH_DIR") or STATE_DIR


def state_path():
    return os.path.join(_dir(), STATE_FILE)


def load_state():
    """Состояние с диска. Любая беда → факт с `ok=False` и НАЗВАННОЙ причиной: решение обязано
    сказать «неизвестно», а не принять пустоту за «гашений не было»."""
    p = state_path()
    if not os.path.exists(p):
        return {"ok": True, "obs": [], "last": None, "probe": None, "series_started": None,
                "err": "", "path": p, "fresh": True}
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, TypeError) as e:
        return {"ok": False, "obs": [], "last": None, "probe": None, "series_started": None,
                "err": "%s: %s" % (type(e).__name__, str(e)[:120]), "path": p, "fresh": False}
    if not isinstance(raw, dict):
        return {"ok": False, "obs": [], "last": None, "probe": None, "series_started": None,
                "err": "состояние не словарь (%s)" % type(raw).__name__, "path": p, "fresh": False}
    raw.setdefault("obs", [])
    raw.setdefault("last", None)
    raw.setdefault("probe", None)
    raw.setdefault("series_started", None)
    raw["ok"], raw["err"], raw["path"], raw["fresh"] = True, "", p, False
    return raw


def save_state(st):
    """Атомарно и best-effort: диск недоступен → худшее, что случится, — потерянное наблюдение,
    а не упавший оборот демона."""
    d = _dir()
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
        p = state_path()
        tmp = p + ".tmp"
        body = {k: st.get(k) for k in ("obs", "last", "probe", "series_started")}
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except (OSError, ValueError, TypeError) as e:                     # noqa: BLE001
        print("состояние не записано (%s: %s)" % (type(e).__name__, str(e)[:120]),
              file=sys.stderr)
        return False


def window(today=None):
    """Окно пробы → (текст для фразы, iso-начало, iso-конец). Всегда в БУДУЩЕМ и целиком в одном
    месяце: прошедший старт увёл бы путь в «уточните даты», и прибор мерил бы не цену, а разбор
    дат. Правило — то же, что у `trainer_run.placeholders`."""
    import suggest
    d = today or suggest.today_phuket()
    yr = d.year + (1 if d.month == 12 else 0)
    mo = d.month % 12 + 1
    return ("from %s %d to %s %d" % (_MONTHS_EN[mo], PROBE_DAY_FROM, _MONTHS_EN[mo], PROBE_DAY_TO),
            "%04d-%02d-%02d" % (yr, mo, PROBE_DAY_FROM),
            "%04d-%02d-%02d" % (yr, mo, PROBE_DAY_TO))


def probe_signature(model, ds, de):
    """Подпись пробы. По ней серия отличает «то же самое наблюдение» от другого вопроса."""
    return "%s|%s|%s" % (model, ds, de)


def measure(getter=None, today=None):
    """ОДНА ЖИВАЯ СБОРКА ЦЕНОВОЙ ЗАПИСКИ → наблюдение. Единственное место прибора, ходящее в сеть.

    `getter` пробрасывается в `suggest.build_pricing_note` насквозь — им же отрицательный тест
    подкладывает мост, который не отвечает. Продуктовый путь при этом ТОТ ЖЕ: подменяется дверь
    в сеть, а не логика записки."""
    import logging
    import suggest
    import pricing
    import price_gate
    import price_source
    import season_gate
    import noprice_gate

    calls, cards = [], []

    def _no_send(text, *_a, **_kw):
        cards.append(1)
        return True

    orig = {"send_np": noprice_gate._default_sender, "send_se": season_gate._default_sender,
            "quote": pricing.quote_for_model, "allow": price_gate.allow,
            "reprice": price_source.reprice, "safe": suggest._safe_quote_for_model}

    def _w_quote(model, ds, de, _get=None, name_filter=None):
        t0 = time.perf_counter()
        try:
            r = orig["quote"](model, ds, de, _get=_get, name_filter=name_filter)
        except Exception as e:                                        # noqa: BLE001
            calls.append({"fn": "pricing.quote_for_model", "exc": type(e).__name__,
                          "sec": round(time.perf_counter() - t0, 2)})
            raise
        d = r if isinstance(r, dict) else {}
        calls.append({"fn": "pricing.quote_for_model", "status": d.get("status"),
                      "has_quote": bool(d.get("quote")), "noprice": d.get("noprice"),
                      "sec": round(time.perf_counter() - t0, 2)})
        return r

    def _w_allow(*a, **kw):
        may, card = orig["allow"](*a, **kw)
        calls.append({"fn": "price_gate.allow", "may": bool(may),
                      "card": (" ".join(str(card).split())[:200] if card else None)})
        return may, card

    def _w_reprice(*a, **kw):
        try:
            r = orig["reprice"](*a, **kw)
        except Exception as e:                                        # noqa: BLE001
            calls.append({"fn": "price_source.reprice", "exc": type(e).__name__})
            raise
        d = r if isinstance(r, dict) else {}
        calls.append({"fn": "price_source.reprice", "status": d.get("status"),
                      "has_quote": bool(d.get("quote")), "noprice": d.get("noprice")})
        return r

    def _w_safe(model, ds, de, getter=None, name_filter=None):
        t0 = time.perf_counter()
        r = orig["safe"](model, ds, de, getter=getter, name_filter=name_filter)
        d = r if isinstance(r, dict) else {}
        calls.append({"fn": "_safe_quote_for_model", "status": d.get("status"),
                      "has_quote": bool(d.get("quote")), "noprice": d.get("noprice"),
                      "sec": round(time.perf_counter() - t0, 2)})
        return r

    text, ds, de = window(today)
    ask = PROBE_ASK_EN % (PROBE_MODEL, text)
    out = {"at": time.time(), "sec": None, "road": pq.ROAD_UNKNOWN, "why": "", "quoted": False,
           "probe": probe_signature(PROBE_MODEL, ds, de), "cards_blocked": 0,
           "timeout": getattr(pricing, "HTTP_TIMEOUT", None),
           "tries": getattr(pricing, "RETRY_TRIES", None), "err": ""}
    noprice_gate._default_sender = lambda: _no_send
    season_gate._default_sender = lambda: _no_send
    pricing.quote_for_model = _w_quote
    price_gate.allow = _w_allow
    price_source.reprice = _w_reprice
    suggest._safe_quote_for_model = _w_safe
    t0 = time.perf_counter()
    try:
        lang = suggest.detect_lang_from_client(ask)
        hints = suggest.extract_booking_hints(ask)
        note = suggest.build_pricing_note(hints, lang, getter=getter)
        out["quoted"] = bool(suggest._quote_block_from_note(note))
        out["head"] = " ".join((note or "").strip().split("\n")[0].split())[:180]
        out["note_len"] = len(note or "")
        out["model"] = hints.get("model")
    except Exception as e:                                            # noqa: BLE001
        # ИСКЛЮЧЕНИЕ РЕЗУЛЬТАТОМ НЕ ЯВЛЯЕТСЯ И БЛАГОПОЛУЧИЕМ ТОЖЕ. Записка не собралась, но
        # сказать, ГДЕ умерло число, мы не можем — это ровно третий исход, и он назван причиной.
        out["err"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        out["sec"] = round(time.perf_counter() - t0, 2)
        out["calls"] = calls
        out["road"], out["why"] = pq.ROAD_UNKNOWN, ("сборка записки упала (%s) — дороги отказа "
                                                    "не назвал никто" % out["err"])
        return out
    finally:
        noprice_gate._default_sender = orig["send_np"]
        season_gate._default_sender = orig["send_se"]
        pricing.quote_for_model = orig["quote"]
        price_gate.allow = orig["allow"]
        price_source.reprice = orig["reprice"]
        suggest._safe_quote_for_model = orig["safe"]
    out["sec"] = round(time.perf_counter() - t0, 2)
    out["calls"] = calls
    out["cards_blocked"] = len(cards)
    out["road"], out["why"] = pq.road_of(calls, out["quoted"], out.get("timeout"),
                                         out.get("tries"), out.get("head", ""))
    return out


def remember(st, obs, cfg):
    """Наблюдение → состояние. Кольцо серии и СМЕНА СЕРИИ, объявленная вслух.

    ПОЧЕМУ СЕРИЯ ВООБЩЕ ЕСТЬ. Доля имеет смысл только над ОДНИМ вопросом: сложить наблюдения по
    разным моделям и разным окнам в одно число — значит получить величину, не отвечающую ни на
    какой вопрос. Сменилась подпись пробы (модель или окно) — прежние наблюдения уходят из счёта,
    и это ВИДНО полем `series_started`, а не случается молча."""
    keep = max(1, int(cfg.get("keep") or pq.KEEP_DEFAULT))
    sig = obs.get("probe")
    if st.get("probe") != sig:
        st["obs"] = []
        st["probe"] = sig
        st["series_started"] = obs.get("at")
    obs_trim = {k: v for k, v in obs.items() if k != "calls"}
    st["obs"] = (list(st.get("obs") or []) + [obs_trim])[-keep:]
    st["last"] = obs_trim
    return st


def run(mode="--tick", now=None, getter=None, today=None):
    """Один оборот прибора. → словарь итога (для теста, лога и человека)."""
    now = time.time() if now is None else float(now)
    cfg = pq.config(os.environ)
    st = load_state()
    facts = {"now": now, "quench": st}
    word, info = pq.verdict(facts, cfg, now)
    out = {"mode": mode, "now": now, "state": word, "why": info.get("why"),
           "line": pq.render_line(word, info), "need": info.get("need"),
           "judged": info.get("judged"), "quenched": info.get("quenched"),
           "share_pp": info.get("share_pp"), "roads": info.get("roads"),
           "probe": st.get("probe"), "measured": False, "road": None}
    if mode in ("--status", "--dry"):
        due, why = pq.probe_due((st.get("last") or {}).get("at"), cfg, now)
        out["due"], out["due_why"] = due, why
        return out
    if mode != "--force":
        due, why = pq.probe_due((st.get("last") or {}).get("at"), cfg, now)
        out["due"], out["due_why"] = due, why
        if not due:
            return out
    obs = measure(getter=getter, today=today)
    out.update({"measured": True, "road": obs.get("road"), "road_why": obs.get("why"),
                "quoted": obs.get("quoted"), "sec": obs.get("sec")})
    if st.get("ok") is not True:
        # Состояние не прочиталось — наблюдение есть, а копить его НЕ ВО ЧТО. Затирать
        # нечитаемый файл новым нельзя: это молча уничтожило бы серию. Говорим и уходим.
        out["why"] = "наблюдение сделано, но состояние не прочитано (%s) — в счёт не легло" % st.get("err")
        return out
    remember(st, obs, cfg)
    save_state(st)
    word, info = pq.verdict({"now": now, "quench": st}, cfg, now)
    out.update({"state": word, "why": info.get("why"), "line": pq.render_line(word, info),
                "judged": info.get("judged"), "quenched": info.get("quenched"),
                "share_pp": info.get("share_pp"), "roads": info.get("roads"),
                # Подпись серии обновляется ЗДЕСЬ, а не наверху: наверху её ещё нет у первой
                # пробы, и `null` в итоге читался бы как «серии нет» при уже сделанном замере.
                "probe": st.get("probe"), "series_started": st.get("series_started")})
    return out


def negative():
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ РУКАМИ: намеренно создаём «цена погашена», прибор ОБЯЗАН показать отказ.

    Мост подменяется дверью, которая не отвечает НИКОГДА (та же дверь, что у живого таймаута, —
    исключение из `getter`). Прибор, сказавший здесь «цена собралась» или промолчавший, — сломан.
    Состояние при этом НЕ ПИШЕТСЯ: подлог в живую серию не попадает ни одной веткой."""
    def _dead(_params):
        raise TimeoutError("проба отрицательного теста: мост не отвечает")

    obs = measure(getter=_dead)
    ok = (not obs.get("quoted")) and pq.is_quench(obs.get("road"))
    return {"ok": ok, "road": obs.get("road"), "why": obs.get("why"),
            "quoted": obs.get("quoted"), "sec": obs.get("sec")}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "--tick"
    if mode == "--negative":
        r = negative()
        print("ОТРИЦАТЕЛЬНЫЙ ТЕСТ: %s" % ("ПРИБОР ПОКАЗАЛ ОТКАЗ" if r["ok"] else "ПРИБОР СМОЛЧАЛ — ДЕФЕКТ"))
        print("  дорога: %s — %s" % (r["road"], r["why"]))
        print("  quote-блок: %s · %s с" % ("ЕСТЬ" if r["quoted"] else "НЕТ", r["sec"]))
        return 0 if r["ok"] else 1
    try:
        out = run(mode)
    except Exception as e:                                            # noqa: BLE001
        # Молчание не считается сбоем наблюдателя, но и падением демонского оборота быть не смеет.
        print("прибор доли не отработал (%s: %s)" % (type(e).__name__, str(e)[:160]),
              file=sys.stderr)
        return 0
    if mode == "--status":
        print("ДОЛЯ ГАШЕНИЯ ЦЕНЫ · полоса ПК")
        print("  исход: %s" % out["state"])
        print("  %s" % out["line"])
        print("  почему: %s" % out["why"])
        print("  проба: %s · пора мерить: %s (%s)"
              % (out["probe"] or "серии ещё нет", out.get("due"), out.get("due_why")))
        return 0
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
