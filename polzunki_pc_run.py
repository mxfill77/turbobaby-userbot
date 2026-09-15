# -*- coding: utf-8 -*-
"""ПОЛЗУНКИ ГЛОБАЛЬНОЙ СКИДКИ — РУКИ прибора (заведено 15.09.2026, задание 62-g).

Решение живёт в :mod:`polzunki_pc` и мира не трогает; здесь — только мир: спросить живую дверь,
прочитать и записать своё состояние, отдать строку витрине и строку журналу. Ни одного порога
сравнения и ни одного вердикта в этом файле нет.

ТОЛЬКО ЧТЕНИЕ ЛИСТА. Ручки снимает ЧУЖОЙ, уже боевой съёмщик — :func:`price_freshness_run.live_handles`
(девять GET `quote_price`, окно в одни сутки). Второй съёмщик разошёлся бы с первым молча, и два
прибора судили бы разные числа под одними именами. Ни `set_*`, ни `toggle_*` не зовутся ни одной
веткой, `price_source.json` не переписывается, лист «Календарь бронирования» не открывается.
Секреты моста берёт САМ одолженный клиент демона — здесь их не читают и не видят (запрет 328).

НАРУЖУ НЕ УХОДИТ НИЧЕГО. У прибора нет двери в Telegram и нет вызова `dispatch_notify` ни одной
строкой: его выход — СТРОКА В ВИТРИНЕ полосы (витрина читает поле ``vitrina`` состояния) плюс
строка в журнале. Показ владельцу делает витрина, которая и так правит одно своё сообщение.

ЧАСТОТУ РЕШАЕТ ПРИБОР, А НЕ ЗВОНЯЩИЙ (форма `price_quench_pc_probe`): демон зовёт ``--tick``
каждый свой заглядыш, а в сеть прибор идёт по своему полу :data:`EVERY_ENV`. Поэтому частота
вызова отсюда на число живых котировок не влияет.

КАК ЗАПУСКАЕТСЯ (из корня репозитория):
    venv\\Scripts\\python.exe polzunki_pc_run.py --tick       # оборот с учётом своего пола
    venv\\Scripts\\python.exe polzunki_pc_run.py --force      # мерить сейчас, пол не спрашивать
    venv\\Scripts\\python.exe polzunki_pc_run.py --status     # словами, в сеть НЕ ходит
    venv\\Scripts\\python.exe polzunki_pc_run.py --dry        # решение без записи состояния
    venv\\Scripts\\python.exe polzunki_pc_run.py --negative   # отрицательный тест руками
"""

import datetime
import io
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import polzunki_pc as pp                                             # noqa: E402

STATE_FILE = "polzunki_pc_state.json"
SCHEMA = "polzunki_pc/1"
JOURNAL_WRITER = "cowork_log_append.py"
OFF_ENV = "POLZUNKI_PC_OFF"

EVERY_ENV = "POLZUNKI_EVERY_MIN"
# ПАСПОРТ ПОРОГА: терпит=окно, в котором сдвинутый ползунок ещё не замечен
#   | замер=ЕСТЬ, и он двусторонний. (1) ЦЕНА ОБОРОТА: девять GET живой дверью, замер 13.09 —
#   ≈13 с на вызов, круг ≈2 мин; при поле 6 ч это 4 круга в сутки ≈ 8 минут двери, то есть
#   0.6 % суток. (2) СКОРОСТЬ СОБЫТИЯ: единственный измеренный сдвиг ручки жил НЕЗАМЕЧЕННЫМ
#   11 суток (I3 съехал с 0.15 на 0.0 между 02.09 и 13.09) — 6 ч меньше этого в 44 раза.
#   Ниже брать нечем: замера частоты ПОВОРОТА ручек на полосе нет вовсе (журнала положений
#   никто не ведёт — `price_gate` говорит о том же про своё окно 30 мин), а выше 6 ч окно
#   незамеченности начинает мериться сменами суток
#   | снят=2026-09-15 | делится=НЕ ДЕЛИТСЯ — это пол ОДНОГО круга из девяти проб
#   | предел-единицы=НЕ НУЖЕН: единица и есть круг; предел ОДНОЙ пробы держит чужой съёмщик
#   (`PRICE_GATE_ONE_SEC` у врезки, сокет-таймаут 240 с у одолженного клиента)
#   | род=каденция | артефакт=docs/artifacts/2026-09-15-ПОЛЗУНКИ-storozh-1509.md
EVERY_DEFAULT = 360.0


# ───────────────────────────── время ─────────────────────────────

def now_ts(clock=None):
    return float(clock() if callable(clock) else (clock if clock is not None else time.time()))


def when_of(ts):
    """Метка времени → datetime UTC. Сезон судится по UTC ровно потому, что по UTC штампует
    журнал полосы: две шкалы в одном приборе разъехались бы на границе месяца."""
    try:
        return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def stamp(ts):
    w = when_of(ts)
    return "" if w is None else w.strftime("%Y-%m-%d %H:%M UTC")


# ───────────────────────────── состояние ─────────────────────────────
# ЖИВЁТ ВНЕ ОЧЕРЕДИ И ВНЕ ВРЕМЕННОЙ ПАПКИ (пункт 1 задания): очередь чистится и не знает
# приборов, `tmp/` лежит под `.gitignore` и переживает не всякий заход. Файл — в корне
# репозитория, рядом с состояниями соседних приборов полосы, и в git он не заводится (его
# содержимое — замер, а не код).

def state_path(root=HERE, named=None):
    return named or os.path.join(root, STATE_FILE)


def read_state(path):
    """Состояние с диска → dict. Нечитаемое состояние — это ПУСТОЕ состояние, а не авария:
    прибор в этом случае просто считает картину новой и скажет о ней один раз."""
    try:
        with io.open(path, encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def write_state(data, path):
    """Состояние на диск через .tmp + replace. → (ok, причина)."""
    tmp = path + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
        os.replace(tmp, path)
    except OSError as exc:
        return False, "состояние не записано (%s)" % type(exc).__name__
    return True, ""


def enabled(env=None):
    got = env if env is not None else os.environ
    return (str(got.get(OFF_ENV) or "").strip() != "1")


def every_sec(env=None):
    """Пол каденции в секундах. Ноль и мусор → дефолт; отрицательное → пола нет."""
    got = env if env is not None else os.environ
    raw = str(got.get(EVERY_ENV) or "").strip()
    if not raw:
        return EVERY_DEFAULT * 60.0
    try:
        return float(raw) * 60.0
    except ValueError:
        return EVERY_DEFAULT * 60.0


# ───────────────────────────── дверь ─────────────────────────────

def live(get=None):
    """Живой лист → факты для прибора. Никогда не бросает: отказ двери — это ИСХОД.

    Съёмщик ЧУЖОЙ и боевой; своего разбора ответа двери у прибора нет ни строки.
    """
    import price_freshness_run

    return price_freshness_run.live_handles(get=get)


def journal(line, repo=HERE, runner=None):
    """Строка-индекс в журнал штатным писателем. → (код, вывод).

    Кириллица идёт СТДИНОМ: в argv она на этой полосе коверкается (известный класс), а у
    писателя весь argv и так считается текстом записи.
    """
    run = runner or subprocess.run
    try:
        done = run([sys.executable, os.path.join(repo, JOURNAL_WRITER), "-"],
                   input=line.encode("utf-8"), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=240)
    except Exception as exc:                       # noqa: BLE001 — журнал НИКОГДА не роняет прибор
        return -1, "журнал не ответил: %s" % exc
    return done.returncode, done.stdout.decode("utf-8", "replace").strip()


# ───────────────────────────── оборот ─────────────────────────────

def tick(root=HERE, named=None, now=None, facts=None, get=None, force=False,
         write=True, write_journal=False, journal_fn=None, env=None):
    """Один оборот прибора. → dict отчёта.

    ``facts`` подаются тестом и отрицательным прогоном; боевой путь идёт в дверь сам.
    ``write=False`` — сухой ход: вердикт посчитан, состояние НЕ тронуто, наружу ничего.
    """
    path = state_path(root, named)
    ts = now_ts(now)
    report = {"acted": False, "asked": False, "state": "", "kind": "", "why": "",
              "line": "", "vitrina": None, "text": "", "verdict": None}
    if not enabled(env):
        report["why"] = "прибор выключен (%s=1)" % OFF_ENV
        return report

    old = read_state(path)
    # ПОЛ КАДЕНЦИИ СПРАШИВАЕТСЯ ДО ДВЕРИ, иначе он не экономил бы ни одного GET. Подан
    # готовый набор фактов — пол не при чём: мерить уже нечего, дверь не спрашивается.
    if facts is None and not force:
        last = old.get("seen_at")
        floor = every_sec(env)
        if isinstance(last, (int, float)) and floor > 0 and (ts - float(last)) < floor:
            report["why"] = ("рано: с прошлого замера %.1f мин при поле %.0f мин"
                             % ((ts - float(last)) / 60.0, floor / 60.0))
            report["state"] = str(old.get("state") or "")
            report["vitrina"] = old.get("vitrina")
            return report

    if facts is None:
        facts = live(get=get)
        report["asked"] = True

    verdict = pp.judge(facts, when=when_of(ts))
    report["verdict"] = verdict
    report["state"] = verdict["state"]

    # «С КАКОГО ЗАМЕРА ДЕРЖИТСЯ» СЧИТАЕТСЯ ПО КАРТИНЕ, А НЕ ПО ИСХОДУ: смена месяца меняет
    # предписание и исход, но неподвижную ручку моложе не делает.
    pic = pp.picture(verdict)
    held_at = ts
    rounds = 1
    if old.get("picture") == pic:
        held_at = old.get("held_at") if isinstance(old.get("held_at"), (int, float)) else ts
        rounds = int(old.get("held_rounds") or 0) + 1
    since_words = stamp(held_at)

    need, kind = pp.signal(verdict, old)
    report["kind"] = kind if need else ""
    row = pp.vitrina_row(verdict, kind if need else _open_kind(old, verdict),
                         since_words=since_words, rounds=rounds)
    report["vitrina"] = row

    new = dict(old)
    new.update({"schema": SCHEMA, "picture": pic, "state": verdict["state"],
                "why": verdict.get("why") or "", "seen_at": ts, "seen": verdict.get("live"),
                "must": verdict.get("must"), "held_at": held_at, "held_rounds": rounds,
                "vitrina": row})
    if need:
        text = pp.text(verdict, kind, since_words=since_words, rounds=rounds)
        report["acted"] = True
        report["text"] = text
        report["line"] = text
        new.update({"signal_key": pp.key(verdict), "signal_state": verdict["state"],
                    "signal_kind": kind, "signal_at": ts, "signal_text": text})
        report["why"] = "сигнал: %s" % kind
    elif verdict["state"] == pp.MATCH and not old.get("signal_key"):
        # ДВА РАЗНЫХ ПОВОДА МОЛЧАТЬ НАЗЫВАЮТСЯ РАЗНЫМИ СЛОВАМИ: «всё сошлось» — это исход, а
        # «картина не менялась» — отсутствие новости. Одна формулировка на оба случая читалась
        # бы в логе как «прибор ничего не проверял».
        report["why"] = "совпадает — прибор молчит"
    else:
        report["why"] = "картина не менялась — сигнала нет"

    if write:
        ok, why = write_state(new, path)
        if not ok:
            # НЕЗАПИСАННОЕ СОСТОЯНИЕ НАЗЫВАЕТСЯ ВСЛУХ: молча оно означало бы, что тот же сигнал
            # родится заново следующим оборотом, и владелец получит его дважды.
            report["why"] += " · %s" % why
    if write and write_journal and report["line"]:
        (journal_fn or journal)("NOTE " + report["line"], repo=root)
    return report


def _open_kind(old, verdict):
    """Род ОТКРЫТОГО сигнала, когда нового не родилось. → str.

    Нужен ровно затем, чтобы строка витрины не пропадала между оборотами: сигнал рождается
    ОДИН раз, а показывать ОТКРЫТОЕ расхождение витрина обязана всё время, пока оно открыто.

    ВОЗВРАТ ОТКРЫТЫМ НЕ БЫВАЕТ, и это разница по существу, а не по вкусу. Расхождение живёт,
    пока ручка стои́т не там; возврат — СОБЫТИЕ, оно случилось и кончилось. Поэтому при исходе
    СОВПАДАЕТ род пуст: строка о возврате доживает в состоянии до следующего оборота прибора
    (окно в целый пол каденции — витрина успевает её показать), а дальше прибор молчит, как и
    велено. Верни мы здесь прежний род, «ВЕРНУЛИ» висело бы в витрине вечно.
    """
    state = (verdict or {}).get("state")
    if state == pp.DIVERGED:
        return pp.MOVED
    if state == pp.UNKNOWN:
        return pp.BLIND
    return ""


# ───────────────────────────── руки ─────────────────────────────

def _negative(root=HERE):
    """Отрицательный тест руками: четыре конца на подложенных фактах, БОЕВЫХ файлов не касаясь.

    Состояние берётся ОТДЕЛЬНОЕ (`*.negative`), боевое не открывается ни на чтение, ни на запись.
    """
    # ЧИСТЫЙ ЛИСТ ДЕЛАЕТСЯ ПЕРЕЗАПИСЬЮ, А НЕ УДАЛЕНИЕМ: на этой полосе удаление файла — красная
    # операция, и прибору она не нужна вовсе (пустое состояние читается тем же кодом, что и
    # отсутствующее — см. :func:`read_state`).
    path = os.path.join(root, STATE_FILE + ".negative")
    write_state({"schema": SCHEMA, "note": "состояние отрицательного прогона, боевым не является"},
                path)
    base = 1789000000.0                        # 2026-09-10 UTC — месяц внутри «июнь-октябрь»
    out = []

    def _facts(h3, i3, j3):
        return {"ok": True, "handles": {"H3": h3, "I3": i3, "J3": j3}}

    r1 = tick(root=root, named=path, now=base, facts=_facts(0.15, 0.0, 0.25))
    out.append(("значение вне правил", r1["state"], r1["kind"], pp.DIVERGED, pp.MOVED))
    r2 = tick(root=root, named=path, now=base + 60, facts=_facts(0.15, 0.0, 0.25))
    out.append(("та же картина дважды", r2["state"], r2["kind"] or "сигнала нет",
                pp.DIVERGED, "сигнала нет"))
    r3 = tick(root=root, named=path, now=base + 120, facts={"ok": False, "handles": None,
                                                           "error": "дверь не ответила"})
    out.append(("пустой ответ двери", r3["state"], r3["kind"], pp.UNKNOWN, pp.BLIND))
    r4 = tick(root=root, named=path, now=base + 180, facts=_facts(0.15, 0.15, 0.25))
    out.append(("возврат к предписанному", r4["state"], r4["kind"], pp.MATCH, pp.RETURNED))
    r5 = tick(root=root, named=path, now=base + 240, facts=_facts(0.15, 0.15, 0.25))
    out.append(("возврат сказан ОДИН раз", r5["state"], r5["kind"] or "сигнала нет",
                pp.MATCH, "сигнала нет"))
    return out, [r1, r2, r3, r4, r5]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                                # noqa: BLE001
        pass

    if "--negative" in args:
        rows, _reports = _negative()
        bad = 0
        for name, state, kind, want_state, want_kind in rows:
            ok = (state == want_state and kind == want_kind)
            bad += 0 if ok else 1
            print("%-26s → %-12s %-14s (ждали %s / %s) %s"
                  % (name, state, kind, want_state, want_kind, "OK" if ok else "ПРОВАЛ"))
        print("\nконцов проверено %d, провалов %d" % (len(rows), bad))
        return 0 if not bad else 2

    if "--status" in args:
        got = read_state(state_path())
        if not got:
            print("СОСТОЯНИЕ: не прочитано — замера ещё не было (%s)" % STATE_FILE)
            return 2
        print("СОСТОЯНИЕ (в сеть не ходили, %s):" % STATE_FILE)
        print("  исход: %s — %s" % (got.get("state"), got.get("why")))
        print("  снято: %s" % json.dumps(got.get("seen"), ensure_ascii=False))
        print("  предписано: %s" % json.dumps(got.get("must"), ensure_ascii=False))
        print("  замер: %s · держится с %s (замеров подряд %s)"
              % (stamp(got.get("seen_at")), stamp(got.get("held_at")), got.get("held_rounds")))
        print("  последний показанный сигнал: %s (%s) от %s"
              % (got.get("signal_kind") or "не было", got.get("signal_state") or "—",
                 stamp(got.get("signal_at")) or "—"))
        print("  строка витрины: %s" % (got.get("vitrina") or "прибор молчит"))
        return 0

    dry = "--dry" in args
    report = tick(force=("--force" in args), write=not dry,
                  write_journal=("--tick" in args or "--journal" in args))
    print("ИСХОД: %s — %s" % (report["state"] or "не считан", report["why"]))
    if report["verdict"]:
        print("РУЧКИ: %s" % pp.pairs(report["verdict"]))
    print("СИГНАЛ: %s" % (report["kind"] or "не родился (картина не менялась)"))
    print("ВИТРИНА: %s" % (report["vitrina"] or "строки нет — прибор молчит"))
    if dry:
        print("(сухой ход: состояние НЕ записано)")
    if "--json" in args:
        print(json.dumps({k: v for k, v in report.items() if k != "verdict"},
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
