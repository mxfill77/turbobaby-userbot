"""Руки ступени E: поводы полосы → РАЗВЕДОЧНЫЕ автозадачи (и заявки владельцу).

Разделение то же, что у ступеней A, B, D и слоя ожиданий: всё, что РЕШАЕТ, живёт
чистым :mod:`recon_auto`; здесь только ввод-вывод — диск, очередь, журнал.

    venv/Scripts/python.exe recon_auto_run.py --status   # какие поводы видит контур
    venv/Scripts/python.exe recon_auto_run.py --dry      # что и куда УШЛО БЫ, дословно
    venv/Scripts/python.exe recon_auto_run.py --place    # боевая постановка
    venv/Scripts/python.exe recon_auto_run.py --tick     # оборот демона

ТРИ ИСТОЧНИКА ПОВОДА, И У КАЖДОГО СВОЙ ЗАМЕРЯЕМЫЙ КОРПУС — заданием названы все
три, и ни один не выдуман здесь:

1. **сработавшее ожидание без разбора** — открытые эпизоды наблюдателя
   (``tmp/expect_pc/state.json`` → ``open``). Наблюдатель живёт ВНЕ демона, и это
   не деталь: вставший демон не ставит задач ни одной веткой, поэтому повод «нет
   оборота» доходит сюда только ПОСЛЕ возвращения оборота — как разбор
   прошедшего, а не как тревога о текущем;
2. **заявка из findings с живой премисой** — пересечение ЖИВОЙ очереди (ряд
   ступени B существует) и ЖИВОЙ пересборки лотка (премиса жива СЕЙЧАС);
3. **красное/«неизвестно», повторившееся дважды** — исходы файлов лотка
   (``review_intake.parse_answer`` → ``outcome``/``reason``, поля структурные) и
   маркер вердикта V0 в закрытых рядах очереди (``done_judge_pc.UNKNOWN_PREFIX``).

СУТОЧНЫЙ ПОТОЛОК СЧИТАЕТСЯ ПО СУТКАМ, А НЕ ПО ОДНОВРЕМЕННОСТИ (поправка 02.09).
Маркер поставленной разведки живёт в её ряду, а ряд после закрытия уходит в
``done`` — и до 02.09 счёт шёл по одним ОТКРЫТЫМ рядам, то есть мерил «сколько
сейчас в работе». Обе цифры совпадают ровно до первого закрытия: в ночь 01.09
полоса поставила себе ПЯТЬ разведок за одни сутки при потолке 2, потому что
каждая закрывалась за 8–20 минут и освобождала место следующей. Теперь маркеры
считаются по открытым рядам, ``failed`` и ``done`` вместе; непрочитанная половина
корпуса делает день ИСЧЕРПАННЫМ, а не пустым (третий исход, ``budget_left``).

БУТСТРАПА ЗДЕСЬ НЕТ, И ЭТО РЕШЕНИЕ, А НЕ ПРОПУСК. У ступеней B и D первый оборот
не разгребает backlog, потому что там backlog измеряется десятками сообщений и
карточек. Здесь потолок — ДВЕ автозадачи в сутки, а замок владельца пропускает не
больше одной в работе; «вывала» не бывает по построению, и бутстрап заморозил бы
ровно те поводы, которые у полосы есть СЕГОДНЯ, то есть контур родился бы с
пустыми руками. Ступень D показала цену декоративного бутстрапа (§11.6) — здесь
его лучше не заводить вовсе, чем завести неработающим.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ НИ ОДНОЙ ВЕТКОЙ: не исполняет находок, не правит кода,
не удаляет файлов, не читает ``.env``, не трогает клиентского контура, не ходит в
внешнюю сеть и не перезапускает ничего. Единственные мутации наружу — ряд очереди
и строка журнала.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import sys

import recon_auto
import review_intake
import review_intake_run
import zayavki_lotok_run

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STATE = "recon_auto_state.json"
ARTIFACTS_DIR = "docs/artifacts"
EXPECT_STATE = "tmp/expect_pc/state.json"

# ОДИН ПОВОД ЗА ТИК — тот же приём и та же причина, что у ступени A: разведка
# стои́т полного захода `claude -p`, и разгребать очередь поводов одним оборотом
# значило бы занять полосу собой. Суточный потолок при этом свой и меньше
# (recon_auto.DAILY_BUDGET), а замок владельца делает больше одной в работе
# невозможным и без этого числа.
TICK_LIMIT = 1

TASK_FROM = "Filipp-recon"        # from автозадачи (НЕ дирижёрская цепь, НЕ ревизор, НЕ заявка B)
ASK_FROM = "Filipp-recon-ask"     # from заявки владельцу: у неё СВОЙ гард в демоне

# Статусы, в которых ряд ещё ЖИВ. Тот же кортеж, что у демона (`_LOC_OPEN`) и у
# слоя ожиданий: держится копией намеренно — руки ступени не импортируют демона
# ради константы, иначе ветка тянула бы за собой весь его запуск.
OPEN_STATUSES = ("new", "in_progress", "needs_approval", "approved")
# Закрытые статусы, в которых живут вердикты «неизвестно» ступени C: своего
# СТАТУСА у «неизвестно» в очереди нет (их ровно шесть), и недоказанное закрытие
# ложится `failed` с маркером. Искать такие исходы по статусу бесполезно — ровно
# как отказы владельца (класс `queue_snapshot_pc`, 15.08).
CLOSED_STATUSES = ("failed",)
# СЧЁТНЫЙ КОРПУС СУТОЧНОГО ПОТОЛКА — третий, и заведён он отдельно НЕ ради
# красоты. Маркер поставленной разведки живёт в её ряду, а ряд после закрытия
# уходит в `done`: считая маркеры по одним открытым рядам, полоса меряла не
# сутки, а одновременность, и в ночь 01.09.2026 поставила пять разведок при
# потолке 2 (#93 15:53, #94 16:25, #97 17:42, #98 18:13, #99 18:45 UTC — каждая
# закрывалась за 8–20 минут и освобождала место следующей).
#
# ПОЧЕМУ ОТДЕЛЬНОЙ КОНСТАНТОЙ, А НЕ ДОПИСАН В `CLOSED_STATUSES`: у этих чтений
# РАЗНАЯ ЦЕНА и разная нужность. `get_pending("failed")` — 54 строки за 2.9с и
# нужен СИГНАЛАМ повтора (то есть самим поводам), поэтому читается всегда;
# `get_pending("done")` — 120 строк за **26.8с** (замер `queue_snapshot_pc`,
# 14.08) и нужен ТОЛЬКО потолку, поэтому читается лишь когда есть что ставить.
# На обороте «поводов нет» (5 из 5 последних тиков живого лога) он не читается
# вовсе, и цена оборота остаётся прежней. Оба корпуса, слитые в один список,
# заставили бы платить 27 секунд витка демона за ответ, который никому не нужен.
BUDGET_STATUSES = ("done",)


def now_iso(clock=None):
    """Текущее время UTC в ISO. Единственная точка, где контур смотрит на часы."""
    stamp = (clock or datetime.datetime.now)(datetime.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _path(root, rel):
    return os.path.join(root, *rel.split("/"))


# ───────────────────────────── реестр ─────────────────────────────


def state_default():
    return {"schema": recon_auto.SCHEMA, "placed": {}, "held": {}, "born_at": None}


def read_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return state_default()
    out = state_default()
    if isinstance(data, dict):
        out["born_at"] = data.get("born_at")
        for key in ("placed", "held"):
            if isinstance(data.get(key), dict):
                out[key] = dict(data[key])
    return out


def write_state(path, state):
    """Реестр на диск атомарно: оборванная запись не смеет оставить контур без
    памяти о том, что уже поставлено, — иначе потолок суток держится ровно до
    первого сбоя записи."""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


# ───────────────────────────── источники ─────────────────────────────


def expect_open(root=HERE, path=None):
    """Открытые эпизоды наблюдателя ожиданий. → (dict, причина отказа).

    Третий исход обязателен и здесь: состояния нет / оно не прочиталось → это НЕ
    «эпизодов нет». Наблюдатель — отдельный процесс под Планировщиком, и его
    молчание означает «не знаю», а не «на полосе всё хорошо».
    """
    try:
        with io.open(path or _path(root, EXPECT_STATE), encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        return {}, "состояние наблюдателя не прочитано: %s" % exc
    except ValueError as exc:
        return {}, "состояние наблюдателя не разобрано: %s" % exc
    if not isinstance(data, dict):
        return {}, "состояние наблюдателя не словарь"
    eps = data.get("open")
    if not isinstance(eps, dict):
        return {}, "в состоянии наблюдателя нет раздела open"
    return eps, ""


def artifact_names(root=HERE, folder=ARTIFACTS_DIR):
    """Имена файлов ``docs/artifacts``. → list (пусто = каталога нет)."""
    try:
        return sorted(os.listdir(_path(root, folder)))
    except OSError:
        return []


def repeat_signals(root=HERE, inbox=None, rows=None):
    """Измеримые «красные»/«неизвестно» полосы со счётчиком. → list[dict].

    ДВА КОРПУСА, оба со СТРУКТУРНЫМ полем исхода, а не с чтением прозы:

    * лоток ответов — ``outcome != answered`` даёт пару «канал, причина». Это
      именно исход, записанный самим отправщиком, а не слово, вычитанное из
      текста: отказ канала — событие, а не мнение;
    * закрытые ряды очереди с префиксом ``done_judge_pc.UNKNOWN_PREFIX``:
      вердикт «неизвестно» ступени C. Своего статуса у него нет, поэтому ищем по
      маркеру результата — тем же способом, каким полоса ищет отказы владельца.

    Импорт ``done_judge_pc`` ленивый и обёрнут: без него ветка честно молчит про
    V0, но лоток считает как считала — один недоступный корпус не смеет отнять
    второй.
    """
    counts = {}
    for rel in review_intake_run.answer_files(root, inbox or review_intake_run.DEFAULT_INBOX):
        try:
            with io.open(_path(root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        try:
            head = review_intake.parse_answer(text)
        except review_intake.ReviewIntakeError:
            continue
        if str(head.get("outcome") or "") == "answered":
            continue
        cls = "отказ канала %s" % (head.get("channel") or "?")
        reason = str(head.get("reason") or head.get("outcome") or "")
        if not reason:
            continue
        slot = counts.setdefault((cls, reason), {"class": cls, "reason": reason,
                                                 "count": 0, "where": []})
        slot["count"] += 1
        slot["where"].append(rel)
    try:
        import done_judge_pc
        prefix = done_judge_pc.UNKNOWN_PREFIX
    except Exception:                                   # noqa: BLE001
        prefix = ""
    if prefix:
        for it in (rows or []):
            if prefix not in str((it or {}).get("result") or ""):
                continue
            cls = "вердикт V0 при закрытии"
            reason = "неизвестно: продукт по адресу не прочитан"
            slot = counts.setdefault((cls, reason), {"class": cls, "reason": reason,
                                                     "count": 0, "where": []})
            slot["count"] += 1
            slot["where"].append("очередь #%s" % it.get("id"))
    return list(counts.values())


def client_lens(root=HERE):
    """Граф импортов + список модулей репозитория — ОДИН РАЗ на оборот. → dict | None.

    Без этого ``mentions`` пересобирал бы граф на КАЖДЫЙ повод: замер живого
    оборота 01.09 — пять поводов, и заход не уложился в 120 с. Цена признака
    линейна по числу поводов только если линзу не переснимать; переснятая, она
    делает цену квадратичной по дереву.
    """
    try:
        import client_contour

        return {"cl": client_contour.closure(root), "modules": client_contour.repo_modules(root)}
    except Exception:                                   # noqa: BLE001 — сбой линзы разбирает route
        return None


def client_probe(cause, root=HERE, prober=None, lens=None):
    """Признак клиентского контура для предмета повода. → dict.

    Вход признака — АДРЕСА повода (имена файлов), а не его проза: ``mentions``
    судит по названным файлам репозитория, и кормить его нашими же
    формулировками значило бы спрашивать у прибора про наш стиль.

    ДВА РАЗНЫХ «НЕ ЗНАЮ», и их нельзя мешать. ``ok=False`` — прибор УПАЛ
    (исключение), и тогда маршрут отдаёт повод владельцу по fail-closed.
    ``determinate=False`` — прибор ОТРАБОТАЛ и сказал «имён репозитория в
    предмете нет вовсе»; это факт о предмете, а не отказ прибора, и разбирается
    он в :func:`recon_auto.route` вместе с посылкой правила (там же объяснено,
    почему у разведки эта ветка не ведёт к владельцу, а у ревизора ведёт).
    """
    hay = " ".join([str(cause.get("address") or "")]
                   + [str(e) for e in (cause.get("evidence") or [])])
    try:
        probe = prober or _mentions
        hit, files, determinate = probe(hay, root, lens)
        return {"hit": bool(hit), "files": list(files or []),
                "determinate": bool(determinate), "ok": True}
    except Exception as exc:                            # noqa: BLE001
        return {"hit": True, "files": [], "determinate": False, "ok": False,
                "why": str(exc)[:120]}


def _mentions(text, root, lens=None):
    import client_contour

    lens = lens if isinstance(lens, dict) else {}
    return client_contour.mentions(text, root, cl=lens.get("cl"), modules=lens.get("modules"))


# ───────────────────────────── очередь ─────────────────────────────


class Queue(review_intake_run.Queue):
    """Очередь ступени E: тот же клиент моста, что у ступени B, плюс два вопроса.

    Наследуемся сознательно: механизм постановки ряда (``enqueue`` → ``claim`` →
    ``set_needs_approval``) один на полосу, и переписывать его здесь значило бы
    завести вторую версию одного урока. Отличие ровно одно — АВТОЗАДАЧА встаёт
    зелёной (``new``) и ждёт ``process_new``, а заявка владельцу проходит тем же
    путём в ``needs_approval``, что и заявка ступени B.
    """

    def rows(self, statuses=OPEN_STATUSES):
        """Ряды полосы ПК в названных статусах. → (list, ok, why).

        Мост не ответил → ``ok=False``, и зовущий не ставит НИЧЕГО: замок
        владельца и потолок суток оба стоя́т на очереди, и ставить, не сверив их,
        значило бы обойти собственные правила по недосмотру.

        ПРИЧИНА НАЗЫВАЕТСЯ ПО СУЩЕСТВУ, А НЕ ИМЕНЕМ КЛАССА (04.09.2026): берём
        ``error_text``, а ``error`` оставляем запасным. С потолком времени на мост
        у отказа появился третий вид — «не спрашивали, мост был занят», — и голое
        ``BridgeBudgetExhausted`` в причине читалось бы как поломка прибора, тогда
        как это отложенное чтение. Тот же класс ложных диагнозов, что закрыли в
        ``bridge_http.explain``: имя класса не новость, новость — что произошло.
        """
        out = []
        for status in statuses:
            res = self._d.bc.get_pending(status)
            if not res.get("ok"):
                return [], False, str(res.get("error_text") or res.get("error") or "мост не ответил")
            for it in (res.get("items") or []):
                if str(it.get("lane") or "pc") != "pc":
                    continue
                item = dict(it)
                item.setdefault("status", status)
                out.append(item)
        return out, True, ""

    def owner_busy(self, rows):
        """В очереди есть НЕЗАКРЫТАЯ работа владельца? → (bool, [номера]).

        Судит ФУНКЦИЯ ДЕМОНА (``_is_owner_work``), а не наш список исключений:
        она уже знает и про info-карточку ревизора, и про заявку ступени B, и про
        шаги чужих цепей. Свой список разошёлся бы с ней на первом же новом виде
        ряда — и разошёлся бы молча.
        """
        pids = self._d._revizor_chain_pids(rows)
        busy = [it.get("id") for it in rows
                if str(it.get("status") or "") in OPEN_STATUSES
                and self._d._is_owner_work(it, pids)]
        return bool(busy), busy

    def place_task(self, text):
        """АВТОЗАДАЧА — зелёный ряд ``new``. → (ok, id|None, причина).

        Ни ``claim``, ни ``set_needs_approval``: задачу подберёт штатный
        ``process_new`` и исполнит headless-ребёнком. Это и есть «демон ставит
        себе задачу» — не имитация ряда, а обычная работа полосы.
        """
        ok, tid, err = self._d.enqueue_pc_task(text, frm=TASK_FROM)
        return (bool(ok), tid, "" if ok else str(err or "enqueue отклонён"))

    def claim_marks(self, rows):
        """Ряды → пары «день, ключ» ступени E. Регулярка СВОЯ, лоток ОБЩИЙ. → list."""
        return recon_auto.markers(rows, "ask")

    def place_ask(self, text):
        """ЗАЯВКА ВЛАДЕЛЬЦУ — ЛОТОК, а ряда ожидания нет. → (ok, адрес|id|None, причина).

        Три действия ступени (`enqueue` → `claim` → `set_needs_approval`) остаются
        на месте и исполняются, только если лоток заявку НЕ ВЗЯЛ: разошлись
        признаки, рубильник поднят, файл не лёг — прежний путь, ряд и карточка.
        """
        took, rel, why_lot = self.to_lotok(text, ASK_FROM)
        if took:
            return True, rel, ""
        d = self._d
        ok, tid, err = d.enqueue_pc_task(text, frm=ASK_FROM)
        if not ok:
            return False, None, str(err or "enqueue отклонён")
        d.bc.claim_task(tid)
        # `frm` называем ЯВНО: второй признак маршрута (`_is_owner_work`) судит по `from`
        # ряда, а не по тексту, и выведенное из маркера значение слабее настоящего.
        res = d.bc.set_needs_approval(tid, text, topic=d.NEEDS_APPROVAL_TOPIC, frm=ASK_FROM)
        if not (isinstance(res, dict) and res.get("ok")):
            why = str((res or {}).get("error_text") or (res or {}).get("error")
                      or "мост не ответил распиской")
            return False, tid, why[:200]
        return True, tid, ""


# ───────────────────────────── сборка ─────────────────────────────


def build(root=HERE, queue=None, clock=None, prober=None):
    """Все поводы полосы + всё, что нужно для решения. → dict.

    Ничего не пишет и никуда не ставит: этой же функцией живут ``--status`` и
    ``--dry``. Очередь ЧИТАЕТСЯ (мост), потому что два из трёх источников повода
    и оба замка правила 4 стоя́т именно на ней.
    """
    stamp = now_iso(clock)
    today = review_intake.today_utc(stamp)
    out = {"stamp": stamp, "today": today, "causes": [], "why": [], "queue_ok": False,
           "owner_busy": None, "owner_rows": [], "task_marks": [], "ask_marks": [],
           "routes": {}, "signals": [], "expect_why": "", "rows": 0,
           "lotok_ok": False, "lotok_why": "лоток не спрашивали",
           "closed_ok": False, "budget_ok": False, "budget_why": "", "marks_ok": False}

    eps, why = expect_open(root)
    out["expect_why"] = why
    if why:
        out["why"].append(why)

    # Клиент моста поднимается РОВНО ОДИН РАЗ за сборку: каждый `Queue()` тянет за
    # собой импорт демона, и три вызова подряд стоили бы трёх его запусков.
    q = None if queue is False else (queue or Queue(root=root))
    live_rows, ok, qwhy = ([], False, "очередь не спрашивали")
    if q is not None:
        live_rows, ok, qwhy = q.rows(OPEN_STATUSES)
    out["queue_ok"] = ok
    if not ok:
        out["why"].append("очередь недоступна (%s)" % qwhy)

    # ЗАКРЫТЫЕ РЯДЫ ЧИТАЮТСЯ ОТДЕЛЬНО И НЕ ФАТАЛЬНО. Корзина `failed` — самая
    # большая на полосе, и её промах не смеет отнять два других источника повода:
    # без неё пропадает ровно один сигнал (вердикт V0 «неизвестно»), а не весь
    # оборот. Живой случай 01.09 15:22: чтение всех статусов одним заходом
    # сорвалось транзиентом моста и обнулило ВСЕ пять поводов разом.
    closed_rows, closed_ok, closed_why = ([], False, "закрытые ряды не спрашивали")
    if q is not None:
        closed_rows, closed_ok, closed_why = q.rows(CLOSED_STATUSES)
    out["closed_ok"] = closed_ok
    if not closed_ok and q is not None:
        out["why"].append("закрытые ряды не прочитаны (%s) — сигнал вердикта V0 не считаем"
                          % closed_why)
    out["rows"] = len(live_rows) + len(closed_rows)
    if ok:
        busy, ids = q.owner_busy(live_rows)
        out["owner_busy"], out["owner_rows"] = busy, ids

    causes = list(recon_auto.expect_causes(eps))

    claim_rows = {}
    for it in live_rows:
        for day, ckey in review_intake.claim_markers([it]):
            claim_rows[ckey] = {"id": it.get("id"), "day": day}
    if claim_rows:
        built = review_intake_run.build(root)["claims"]
        causes += recon_auto.claim_causes(claim_rows, built)

    signals = repeat_signals(root, rows=closed_rows)
    out["signals"] = signals
    causes += recon_auto.repeat_causes(signals)

    done = recon_auto.analysed(causes, artifact_names(root))
    causes = [c for c in causes if str(c.get("key")) not in done]
    out["analysed"] = sorted(done)
    out["causes"] = recon_auto.order(causes)

    # МАРКЕРЫ СУТОК — ПО ОБЕИМ ПОЛОВИНАМ ОЧЕРЕДИ, и дорогая половина читается
    # ЗДЕСЬ, после отбора поводов: нет поводов — нечего ставить, и 27 секунд за
    # ответ, который никто не спросит, полоса не платит. Когда поводы есть,
    # читается всё: открытые ряды + `failed` + `done`.
    budget_rows, budget_ok, budget_why = ([], False, "закрытые done не спрашивали")
    if q is None or not ok:
        budget_why = "очередь не прочитана — маркеров суток нет ни одного"
    elif not out["causes"]:
        # ЧЕСТНОЕ ИМЯ ТРЕТЬЕГО СОСТОЯНИЯ: это НЕ отказ прибора и НЕ прочитанный
        # ноль, а «не спрашивали, потому что незачем». Путать его с отказом
        # нельзя — иначе каждый пустой оборот кричал бы о поломке моста.
        budget_why = "поводов нет — дорогое чтение done не понадобилось"
    else:
        budget_rows, budget_ok, budget_why = q.rows(BUDGET_STATUSES)
        if not budget_ok:
            out["why"].append("закрытые ряды (done) не прочитаны (%s) — суточный потолок "
                              "сверить нечем, день считаем ИСЧЕРПАННЫМ" % budget_why)
    out["budget_ok"], out["budget_why"] = budget_ok, budget_why
    # Полнота корпуса — И открытые, И `failed`, И `done`. Любая непрочитанная
    # половина делает счёт неполным, а неполный счёт по правилу третьего исхода
    # значит «день исчерпан», а не «день пуст» (`recon_auto.budget_left`).
    # ЛОТОК — ЧАСТЬ КОРПУСА, А НЕ ДОБАВКА К НЕМУ (09.09.2026). Заявка владельцу
    # ряда больше не создаёт, и её маркер в очереди не появится НИКОГДА. Не
    # прочитав лоток, потолок `ask_budget` считал бы пустой день каждый оборот —
    # ровно тот класс, что дал пять разведок за одни сутки при потолке 2. Лоток не
    # прочитан → корпус НЕПОЛНЫЙ, и день считается исчерпанным, а не пустым.
    lot_rows, lot_ok, lot_why = zayavki_lotok_run.marker_rows(root)
    if not lot_ok:
        out["why"].append("лоток заявок не прочитан (%s) — сколько поставлено сегодня, "
                          "НЕИЗВЕСТНО; день считаем ИСЧЕРПАННЫМ" % lot_why)
    out["lotok_ok"], out["lotok_why"] = lot_ok, lot_why
    out["marks_ok"] = bool(ok and closed_ok and budget_ok and lot_ok)
    if ok:
        all_rows = list(live_rows) + list(closed_rows) + list(budget_rows) + list(lot_rows)
        out["rows"] = len(all_rows)
        out["task_marks"] = recon_auto.markers(all_rows, "task")
        out["ask_marks"] = recon_auto.markers(all_rows, "ask")

    lens = client_lens(root) if (out["causes"] and prober is None) else None
    for cause in out["causes"]:
        out["routes"][str(cause["key"])] = recon_auto.route(
            cause, client_probe(cause, root, prober=prober, lens=lens))
    out["queue"] = q
    return out


# ───────────────────────────── оборот ─────────────────────────────


def tick(root=HERE, state_path=None, place=False, limit=TICK_LIMIT,
         budget=recon_auto.DAILY_BUDGET, ask_budget=recon_auto.ASK_BUDGET,
         write_journal=False, clock=None, queue=None, journal_fn=None, prober=None):
    """Один оборот ступени E. → dict отчёта.

    ``place=False`` — сухой ход: поводы найдены, маршруты посчитаны, тексты
    собраны, очередь НЕ тронута. Боевой ход отличается ровно двумя действиями:
    постановкой ряда и записью реестра.
    """
    state_path = state_path or _path(root, DEFAULT_STATE)
    data = build(root, queue=queue, clock=clock, prober=prober)
    today, stamp = data["today"], data["stamp"]
    state = read_state(state_path)
    report = {"acted": False, "why": "", "today": today, "stamp": stamp,
              "causes": len(data["causes"]), "placed": [], "failed": [], "held": [],
              "owner_busy": data["owner_busy"], "owner_rows": data["owner_rows"],
              "queue_ok": data["queue_ok"], "signals": data["signals"],
              "marks_ok": data.get("marks_ok"), "budget_why": data.get("budget_why", ""),
              "routes": data["routes"], "texts": {}, "line": ""}

    take, held = recon_auto.select(
        data["causes"], placed=set(state.get("placed") or {}),
        task_marks=data["task_marks"], ask_marks=data["ask_marks"], today=today,
        budget=budget, ask_budget=ask_budget, owner_busy=bool(data["owner_busy"]),
        routes=data["routes"], limit=limit, marks_ok=bool(data.get("marks_ok")))
    report["held"] = [(c["key"], why) for c, why in held]

    for cause, way, why in take:
        address, _words = recon_auto.result_address(cause, today)
        if way == recon_auto.ROUTE_OWNER:
            text = recon_auto.ask_text(cause, today, why)
        else:
            text = recon_auto.task_text(cause, today)
        if not text:
            # ЧЕТЫРЁХ ЧАСТЕЙ НЕ СОБРАЛОСЬ → НЕ СТАВИМ ВОВСЕ. Отдельная ветка, а не
            # «поставим попроще»: задача без адреса результата закрывается словом
            # исполнителя, и ступень C это право у полосы отняла.
            report["held"].append((cause["key"], "нет одной из четырёх частей — не ставим вовсе"))
            continue
        report["texts"][cause["key"]] = text
        if not place:
            report["held"].append((cause["key"], "сухой ход: %s собрана, очередь не тронута" % way))
            continue
        if not (data["queue_ok"] and data.get("queue") is not None):
            report["held"].append((cause["key"], "очередь недоступна — не ставим вслепую"))
            continue
        q = data["queue"]
        ok, tid, err = (q.place_ask(text) if way == recon_auto.ROUTE_OWNER
                        else q.place_task(text))
        if not ok:
            report["failed"].append({"key": cause["key"], "way": way, "why": err, "id": tid})
            continue
        report["placed"].append({"key": cause["key"], "id": tid, "way": way,
                                 "src": cause["src"], "kind": cause["kind"],
                                 "address": address if way == recon_auto.ROUTE_RECON else "",
                                 "why": why})
        state.setdefault("placed", {})[cause["key"]] = {
            "placed_at": stamp, "queue_id": tid, "way": way, "src": cause["src"],
            "kind": cause["kind"], "address": address if way == recon_auto.ROUTE_RECON else "",
            "why": why}
        if write_journal:
            (journal_fn or review_intake_run.journal)(
                recon_auto.index_line(cause, way, tid,
                                      address if way == recon_auto.ROUTE_RECON else ""),
                repo=root)
    if place:
        state["born_at"] = state.get("born_at") or stamp
        for key, why in report["held"]:
            state.setdefault("held", {})[key] = {"seen_at": stamp, "why": why}
        write_state(state_path, state)
    report["acted"] = bool(report["placed"] or report["failed"])
    report["line"] = _line(report)
    if not report["why"]:
        report["why"] = _why(report, data)
    # Снимок сборки едет в отчёте, чтобы печать не гоняла мост ВТОРОЙ раз: за
    # один заход очередь читается ровно однажды. Клиента моста из снимка убираем —
    # он объект, а не факт, и в json ему делать нечего.
    report["build"] = {k: v for k, v in data.items() if k != "queue"}
    return report


def _why(report, data):
    """Одна строка «почему исход такой». Причина названа ВСЕГДА, в том числе когда
    ничего не сделано: молчание контура и его отказ — разные новости."""
    if not data["queue_ok"]:
        return "очередь недоступна — ни задач, ни заявок не ставим"
    if not data["causes"]:
        return "поводов нет (проверено: ожидания, заявки, повторы)"
    if report["placed"]:
        return "поставлено %d, отложено %d" % (len(report["placed"]), len(report["held"]))
    if not data.get("marks_ok"):
        # ОТКАЗ ПРИБОРА, А НЕ ИСЧЕРПАННЫЙ ДЕНЬ, и сказано это должно быть РАЗНЫМИ
        # словами: «бюджет кончился» владелец читает как норму и не идёт смотреть,
        # а «сверить нечем» — как поломку моста, которая сама не пройдёт.
        return ("закрытые ряды очереди не прочитаны (%s) — сколько разведок уже поставлено "
                "сегодня, НЕИЗВЕСТНО; день считаем исчерпанным и не ставим ничего"
                % (data.get("budget_why") or "причина не названа"))
    if data["owner_busy"]:
        return ("в очереди задача владельца (%s) — автозадач не ставим ни одной"
                % ", ".join("#%s" % i for i in data["owner_rows"]))
    return "поводов %d, поставлено 0, отложено %d" % (len(data["causes"]), len(report["held"]))


def _line(report):
    """Строка исхода оборота для журнала/ленты. → str (пусто = писать нечего)."""
    placed = report.get("placed") or []
    if not placed and not report.get("failed"):
        return ""
    parts = ["ступень E: поставлено %d" % len(placed)]
    for row in placed:
        parts.append("#%s (%s, повод %s/%s)%s"
                     % (row["id"], row["way"], row["src"], row["kind"],
                        (" → %s" % row["address"]) if row["address"] else ""))
    if report.get("failed"):
        parts.append("не встало %d" % len(report["failed"]))
    return "; ".join(parts)


# ───────────────────────────── CLI ─────────────────────────────


def _render(report=None, data=None):
    lines = []
    if data is not None:
        lines.append("день: %s (замер %s)" % (data["today"], data["stamp"]))
        lines.append("очередь: %s, рядов %d%s"
                     % ("прочитана" if data["queue_ok"] else "НЕДОСТУПНА", data["rows"],
                        (", работа владельца: %s" % (", ".join("#%s" % i for i in data["owner_rows"])
                                                     or "нет")) if data["queue_ok"] else ""))
        lines.append("маркеры суток: %s (закрытые failed: %s; закрытые done: %s)"
                     % ("корпус полон" if data.get("marks_ok") else "КОРПУС НЕПОЛОН — день исчерпан",
                        "прочитаны" if data.get("closed_ok") else "НЕТ",
                        data.get("budget_why") or "прочитаны"))
        lines.append("маркеров задач сегодня: %d, заявок: %d"
                     % (sum(1 for d, _k in data["task_marks"] if d == data["today"]),
                        sum(1 for d, _k in data["ask_marks"] if d == data["today"])))
        if data["expect_why"]:
            lines.append("наблюдатель ожиданий: %s" % data["expect_why"])
        lines.append("сигналы повтора: %d" % len(data["signals"]))
        for sig in sorted(data["signals"], key=lambda s: -s["count"]):
            lines.append("  • %s: %s ×%d" % (sig["class"], sig["reason"], sig["count"]))
        lines.append("поводов: %d" % len(data["causes"]))
        for cause in data["causes"]:
            way, why = data["routes"].get(str(cause["key"]), ("?", ""))
            lines.append("  • [%s] ключ=%s %s" % (cause["src"], cause["key"], cause["title"]))
            lines.append("    маршрут: %s — %s" % (way, why))
            addr, _w = recon_auto.result_address(cause, data["today"])
            lines.append("    адрес результата: %s" % (addr or "НЕ СОБРАЛСЯ"))
    if report is not None:
        lines.append("исход: %s" % (report.get("why") or "—"))
        for row in report.get("placed") or []:
            lines.append("  ПОСТАВЛЕНА #%s (%s) ключ=%s" % (row["id"], row["way"], row["key"]))
        for row in report.get("failed") or []:
            lines.append("  НЕ ВСТАЛА ключ=%s: %s" % (row["key"], row["why"]))
        for key, why in report.get("held") or []:
            lines.append("  отложено ключ=%s: %s" % (key, why))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ступень E: поводы полосы ПК → разведочные автозадачи и заявки владельцу.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="какие поводы видит контур")
    mode.add_argument("--dry", action="store_true", help="собрать всё, очередь НЕ трогать")
    mode.add_argument("--place", action="store_true", help="боевая постановка")
    mode.add_argument("--tick", action="store_true", help="оборот демона")
    parser.add_argument("--limit", type=int, default=TICK_LIMIT, help="потолок автозадач за заход")
    parser.add_argument("--budget", type=int, default=recon_auto.DAILY_BUDGET,
                        help="потолок автозадач на сутки")
    parser.add_argument("--show", default=None, help="напечатать ТЕКСТ задачи по ключу повода")
    parser.add_argument("--journal", action="store_true", help="писать строки-индексы в журнал")
    parser.add_argument("--json", action="store_true", help="отчёт машиночитаемо")
    args = parser.parse_args(argv)

    if args.show:
        # ТЕКСТ ПОВОДА ПО ЕГО КЛЮЧУ — отдельная дорога, не зависящая от отбора.
        # Иначе увидеть дословный текст было бы нельзя ровно тогда, когда он
        # важнее всего: замок владельца режет повод ДО сборки текста, и «покажи,
        # что ты собирался поставить» упиралось бы в «ничего не выбрано».
        data = build(HERE)
        for cause in data["causes"]:
            if str(cause["key"]) != args.show:
                continue
            way, why = data["routes"].get(str(cause["key"]), (recon_auto.ROUTE_RECON, ""))
            text = (recon_auto.ask_text(cause, data["today"], why)
                    if way == recon_auto.ROUTE_OWNER
                    else recon_auto.task_text(cause, data["today"]))
            print(text if text else "ЧЕТЫРЁХ ЧАСТЕЙ НЕ СОБРАЛОСЬ — задача не ставится вовсе")
            return 0
        print("повода с ключом %s сейчас нет" % args.show)
        return 0
    if args.status or not (args.dry or args.place or args.tick):
        data = build(HERE)
        data.pop("queue", None)
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str) if args.json
              else _render(None, data))
        return 0
    report = tick(HERE, place=bool(args.place or args.tick), limit=args.limit,
                  budget=args.budget, write_journal=args.journal)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(_render(report, report.get("build")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
