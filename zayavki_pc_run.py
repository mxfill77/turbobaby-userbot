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

ТРЕТЬЕ ДЕЙСТВИЕ МОСТА, ЗАВЕДЕНО 03.09.2026 — ``close_by_sift``. Ряд, которому
отбор карточки не дал, закрывается САМ: ``complete_task(done)`` с меткой
:data:`zayavki_pc.SIFT_MARK`. Повод замерен в тот же день: в очереди десять рядов
`needs_approval`, шесть из них заявки-ревью, и карточки не уходило НИ ПО ОДНОЙ —
шесть рядов числились «ждёт владельца», а нажать было нечего и не будет.

ЧЕГО ЭТО ДЕЙСТВИЕ НЕ ДЕЛАЕТ: не говорит за владельца ни «да», ни «нет» (у него
свой третий исход и свой разрез, не пересекающийся с ``_REJECT_PREFIX``); не
трогает ряд, по которому карточка уходила; не трогает клиентский дефект ни одной
веткой; молчит, если отбор недостоверен, — тогда ряд остаётся ОТКРЫТЫМ. Откат
одной переменной: :data:`NO_CLOSE_FLAG`.

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
import re
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

# Рубильник ОДНОЙ ветки — закрытия рядов отбором (03.09.2026). Заведён отдельно от
# `ZAYAVKI_PC_OFF` намеренно: выключать доставку заявок ради отката закрытия
# значило бы менять одну беду на другую. Поднят — ряды снова висят открытыми, всё
# остальное работает байт-в-байт как до правки.
NO_CLOSE_FLAG = "ZAYAVKI_PC_NO_CLOSE"


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


def closing_enabled():
    """Закрытие рядов отбором включено? Дефолт — да."""
    return str(os.getenv(NO_CLOSE_FLAG) or "").strip().lower() not in ("1", "true", "yes", "on")


# ───────────────────────────── реестр отправленного ─────────────────────────────


def state_default():
    return {"schema": zayavki_pc.SCHEMA, "sent": {}, "closed": {}}


def read_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return state_default()
    out = state_default()
    if isinstance(data, dict) and isinstance(data.get("sent"), dict):
        out["sent"] = dict(data["sent"])
    # Реестр закрытых отбором заведён 03.09.2026 и в старом файле отсутствует —
    # это не поломка, а первый день: пустой реестр честно означает «за сутки
    # отбор не закрыл ничего», а не «мы не знаем».
    if isinstance(data, dict) and isinstance(data.get("closed"), dict):
        out["closed"] = dict(data["closed"])
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

    def close_by_sift(self, tid, text):
        """ОТБОР закрывает ряд сам — третьим исходом. → (ok, why).

        Статус ``done`` и НЕ ``_REJECT_PREFIX``: префикс отказа значит «отклонено
        Филиппом», а Филипп этой заявки не видел. Своя метка
        (:data:`zayavki_pc.SIFT_MARK`) даёт слепку очереди отдельный разрез, не
        пересекающийся ни с решениями владельца, ни со сбоями.
        """
        res = self._d.bc.complete_task(tid, zayavki_pc.SIFT_STATUS, text)
        ok = bool(isinstance(res, dict) and res.get("ok"))
        return ok, ("" if ok else str((res or {}).get("error") or "мост не ответил распиской"))


# ─────────────────── клиентский контур и тело ряда (руки отбора) ───────────────────


def _row_text(row):
    """Тело ряда как его отдала очередь. → str (пусто = тела нет)."""
    got = row if isinstance(row, dict) else {}
    text = got.get("task_text")
    if not str(text or "").strip():
        text = got.get("goal")
    return str(text or "")


_RE_PREMISE_ADDR = re.compile(r"найден по адресу\s+(\S+)")
# Хвост «:НОМЕР СТРОКИ» живого адреса. Ступень B пишет адрес якоря КОДА именно так
# (`review_intake_run.py:263` → ``address="%s:%d" % (rel, line)``), и это не
# украшение: без номера строки владелец не найдёт место.
_RE_ADDR_LINE = re.compile(r":\d+$")


def premise_addresses(got):
    """Адреса, по которым премиса ПОДТВЕРДИЛАСЬ. → list[str].

    Берём адрес из причины премисы, а НЕ имена, упомянутые находкой: подтвердился
    тот файл, в котором проба РЕАЛЬНО нашла якорь. Упомянуть можно что угодно;
    найдено — ровно одно место, и только оно говорит о том, что живёт в дереве.

    Возвращается адрес ДОСЛОВНО, вместе с номером строки: это то, что ступень B
    написала, и врать о нём здесь нельзя. Нормализует для графа :func:`_file_of`.
    """
    why = str((got or {}).get("premise_why") or "")
    return [m.group(1).rstrip(".,;") for m in _RE_PREMISE_ADDR.finditer(why)]


def _file_of(address):
    """Адрес премисы → ИМЯ ФАЙЛА для вопроса графу. → str.

    ЗАКРЫТАЯ СЛЕПОТА ОТБОРА (найдена замером 03.09.2026, живой ряд #32). Живой
    адрес ступени B несёт номер строки — ``model_name.py:30``, — а
    `client_contour._norm` берёт `basename` и номер не срезает: замер прямой пробой
    дал ``is_client("model_name.py") = True`` и ``is_client("model_name.py:30") =
    False``. То есть условие 2 отбора («предмет виден клиенту») было слепо к КАЖДОМУ
    адресу, найденному в коде, — а другого формата у живой ЖИВОЙ премисы и нет.

    Почему это не заметили раньше: реплей 03.09 строил ``premise_why`` СВОИМИ
    пробами, где адрес шёл голым именем файла, а голден `DEFECT_PREMISE` был
    написан так же. Мок разошёлся с живым форматом и зеленел молча — тот самый
    класс, о котором предупреждает `CLAUDE.md` («голдены детекта — дословные фразы
    живого провала»). Голден переведён на живой формат тем же коммитом.

    ПОРОГ ОТБОРА ЭТИМ НЕ ТРОНУТ: три условия :func:`zayavki_pc.client_defect`
    остались дословно теми же. Починен РАЗБОР АДРЕСА, который скармливался
    второму условию, и направление правки — в сторону БОЛЬШЕГО числа карточек и
    МЕНЬШЕГО числа закрытий, то есть в осторожную.
    """
    return _RE_ADDR_LINE.sub("", str(address or "").strip())


def client_files_of(got, repo=HERE, closure=None):
    """Адреса премисы, лежащие в КЛИЕНТСКОМ контуре. → (list[str], определимо:bool).

    Признак берём чужой и единственный на полосе — `client_contour`: транзитивное
    замыкание импортов живых клиентских процессов, посчитанное по факту с диска.
    Второго определения «что видит клиент» здесь не заводится: разойдись они, одно
    начало бы молча врать, и узнать какое было бы неоткуда.

    ВНИМАНИЕ НА НАПРАВЛЕНИЕ FAIL-CLOSED. У `client_contour` оно своё и обратное
    нашему: не смог построить граф → «клиентское ВСЁ» (там цена ошибки — правка
    уехала к живому клиенту). У отбора карточек цена ошибки другая: лишняя
    карточка — это ровно тот шум, от которого заводился отбор, а несостоявшаяся
    карточка находку НЕ ТЕРЯЕТ (строка сводки и открытая заявка остаются).
    Поэтому недостоверный граф мы НЕ превращаем в «клиентское», а честно отдаём
    ``определимо=False`` — третьим исходом, и зовущий говорит о нём словом.
    """
    try:
        import client_contour
    except Exception:                       # модуля нет — отбор обязан сказать это словом
        return [], False
    try:
        cl = closure if closure is not None else client_contour.closure(repo)
    except Exception:
        return [], False
    if not getattr(cl, "ok", False):
        return [], False
    hits = []
    for addr in premise_addresses(got):
        name = _file_of(addr)          # номер строки графу не задаём: он о файлах
        try:
            if client_contour.is_client(name, cl=cl):
                hits.append(addr)      # наружу — адрес ДОСЛОВНО, со строкой
        except Exception:
            continue
    return hits, True


def sent_today_count(state, now):
    """Сколько карточек уже ушло за ПОСЛЕДНИЕ СУТКИ. → int.

    Считаем скользящим окном по реестру отправок, а не по календарному дню:
    полночь UTC не является границей внимания владельца, и пачка в 23:50 плюс
    пачка в 00:10 — это одна ночь, а не два дня по потолку.
    """
    return _within_day((state or {}).get("sent") or {}, now, "first_at")


def closed_today_count(state, now):
    """Сколько рядов ОТБОР закрыл сам за последние сутки. → int.

    Число задания (пункт 5) и второе из двух, которые сводка обязана нести ВРОЗЬ.
    Окно то же скользящее, что у карточек: сутки владельца — не календарный день.
    """
    return _within_day((state or {}).get("closed") or {}, now, "at")


def _within_day(records, now, field):
    n = 0
    for rec in (records or {}).values():
        try:
            if float(now) - float((rec or {}).get(field)) < 86400.0:
                n += 1
        except (TypeError, ValueError):
            continue
    return n


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
        # ОТКАЗ НЕ НЕСЁТ НАЙДЕННОГО (17.09.2026, задание 62-t): `str(h)` печатал поле
        # `sample` — до 24 знаков самой формы — в причину, а причина едет в отчёт захода.
        # Наружу только вид, число находок и длина текста.
        return "none", False, "страж исходящего: %s (находок %d, текст %d знаков)" % (
            ", ".join(sorted({h["kind"] for h in hits})), len(hits), len(str(text or "")))
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
    report = {"acted": False, "why": "", "awaiting": 0, "zayavki": 0, "cards": 0, "foreign": 0,
              "sent": [], "held": [], "reminded": [], "skipped": [], "failed": [], "line": "",
              "routed": [], "digested": [], "summary": "", "graph_ok": True,
              "closed": [], "kept": [], "awaiting_owner": 0, "closed_today": 0}
    if not enabled():
        report["why"] = "ступень выключена (%s)" % OFF_FLAG
        return report
    # Клиент очереди строится ОДИН раз: второй `Queue()` — второй импорт демона.
    q = None if queue is False else (queue or Queue())
    rows, ok, why = q.awaiting() if q is not None else ([], True, "")
    if not ok:
        # ТРЕТИЙ ИСХОД, А НЕ НОЛЬ. Источник молчит — значит сколько находок пришло
        # за сутки, НЕИЗВЕСТНО. Сводка «0 находок» в этот день была бы неотличима
        # от честного пустого дня, то есть прямым враньём.
        report["why"] = "очередь недоступна (%s) — не шлём ничего" % why
        report["summary"] = zayavki_pc.summary([], inbox_ok=False, inbox_why=why)
        return report
    split = zayavki_pc.split_awaiting(rows)
    report["awaiting"] = len(rows)
    report["zayavki"] = len(split["zayavki"])
    report["cards"] = len(split["cards"])
    report["foreign"] = len(split.get("foreign") or [])
    items = [zayavki_pc.digest(row) for row in split["zayavki"]]
    state = read_state(state_path)

    # ── ОТБОР: карточкой уходит только клиентский дефект (03.09.2026) ──
    # Признаки собираются ЗДЕСЬ, потому что оба требуют диска: тело ряда для слов
    # и граф импортов для клиентского контура. Решение по ним принимает чистый
    # слой (`zayavki_pc.route`) — как и у всех прочих ступеней полосы.
    signals, client_files, determined_by = {}, {}, {}
    graph_ok = True
    for row, got in zip(split["zayavki"], items):
        key = got.get("key") or ""
        signals[key] = zayavki_pc.defect_signals(_row_text(row))
        hits, determined = client_files_of(got, repo=root)
        client_files[key] = hits
        determined_by[key] = determined
        if not determined:
            graph_ok = False
    routed = zayavki_pc.route(items, signals, client_files,
                              sent_today=sent_today_count(state, now))
    report["routed"] = routed
    report["graph_ok"] = graph_ok
    by_action = {r.get("key"): r for r in routed}
    to_card = {r.get("key") for r in routed if r.get("action") in ("card", "card_over")}
    report["digested"] = [r for r in routed if r.get("action") == "digest"]

    # ── СУДЬБА РЯДА БЕЗ КАРТОЧКИ: закрывается сам, третьим исходом (03.09.2026) ──
    # Решает чистый слой; здесь только ввод-вывод. Ключи, по которым карточка уже
    # уходила, приезжают ИЗ РЕЕСТРА: такой ряд ждёт ОТВЕТА, и закрывать его нельзя.
    by_key_all = {d.get("key"): d for d in items}
    verdicts = zayavki_pc.closures(routed, determined=determined_by,
                                   carded=set((state.get("sent") or {}).keys()))
    for v in verdicts:
        if not v.get("close"):
            report["kept"].append(v)
            continue
        if not closing_enabled():
            v = dict(v, why="%s; но закрытие выключено (%s) — ряд оставлен ОТКРЫТЫМ"
                            % (v.get("why"), NO_CLOSE_FLAG))
            report["kept"].append(v)
            continue
        text = zayavki_pc.sift_result(v, pointer_text=zayavki_pc.pointer(
            by_key_all.get(v.get("key")) or {"id": v.get("id")}))
        if not send or q is None:
            report["closed"].append(dict(v, channel="сухой ход"))
            continue
        ok_close, why_close = q.close_by_sift(v.get("id"), text)
        if not ok_close:
            # Мост отказал — ряд ОСТАЁТСЯ ОТКРЫТЫМ. Считать его закрытым по нашей
            # записи значило бы потерять его из обоих чисел разом.
            report["failed"].append({"id": v.get("id"), "key": v.get("key"),
                                     "why": "закрытие отбором не прошло: %s" % why_close})
            report["kept"].append(dict(v, why="закрытие не прошло — ряд открыт"))
            continue
        report["closed"].append(v)
        state.setdefault("closed", {})[v.get("key") or str(v.get("id"))] = {
            "at": now, "at_iso": now_iso(), "queue_id": v.get("id"),
            "kind": v.get("kind"), "outcome": zayavki_pc.SIFT_OUTCOME, "why": v.get("why"),
        }

    # ДВА ЧИСЛА ВРОЗЬ (пункт 5). «Ждёт владельца» — ряды, по которым решение
    # ДЕЙСТВИТЕЛЬНО за человеком. Считаем ВЫЧИТАНИЕМ, а не сложением кучек:
    # `to_card` и `kept` ПЕРЕСЕКАЮТСЯ (ряд с дефектом лежит в обеих), и сумма
    # давала бы 2 там, где ряд один — замерено красным тестом при сборке.
    # Открыто = всё наше минус то, что отбор закрыл.
    report["awaiting_owner"] = len(routed) - len(report["closed"])
    # В боевом ходе закрытия этого захода УЖЕ лежат в реестре; в сухом их там нет
    # и никогда не будет — считаем их отдельно, иначе сухой ход печатал бы «0».
    report["closed_today"] = (closed_today_count(state, now)
                              + (0 if send else len(report["closed"])))
    report["summary"] = zayavki_pc.summary(
        routed, found=len(items), awaiting_owner=report["awaiting_owner"],
        closed_today=report["closed_today"], foreign_waiting=report["foreign"])

    # В `plan` едут ТОЛЬКО отобранные карточкой. Отложенные остаются открытыми
    # заявками в очереди и живут строкой сводки — реестр отправок их не помнит,
    # потому что отправки не было, и завтрашний отбор рассудит их заново.
    items = [d for d in items if (d.get("key") or "") in to_card]
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
        decision = by_action.get(step.get("key")) or {}
        text = zayavki_pc.message(got, defect_why=decision.get("why"),
                                  over_cap=decision.get("action") == "card_over")
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
    report["acted"] = bool(report["sent"] or report["reminded"] or report["failed"]
                           or report["closed"])
    report["line"] = zayavki_pc.index_line(report)
    if not report["why"]:
        report["why"] = ("заявок %d: карточкой %d, строкой в сводке %d, доставлено %d, "
                         "напомнено %d, придержано %d, молчим о %d; ЖДЁТ ВЛАДЕЛЬЦА %d, "
                         "ЗАКРЫТО ОТБОРОМ за сутки %d (числа разные, не складывать)"
                         % (report["zayavki"], len(to_card), len(report["digested"]),
                            len(report["sent"]), len(report["reminded"]),
                            len(report["held"]), len(report["skipped"]),
                            report["awaiting_owner"], report["closed_today"]))
        if not graph_ok:
            report["why"] += ("; граф клиентского контура НЕДОСТОВЕРЕН — предмет судился "
                              "только по словам находки")
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
    lines = ["рядов в needs_approval: %d (заявок-ревью %d, карточек гарда %d, "
             "заявок ступени E %d — чужой отбор)"
             % (report.get("awaiting", 0), report.get("zayavki", 0), report.get("cards", 0),
                report.get("foreign", 0)),
             "ЖДЁТ ВЛАДЕЛЬЦА: %d · ЗАКРЫТО ОТБОРОМ за сутки: %d (числа разные)"
             % (report.get("awaiting_owner", 0), report.get("closed_today", 0)),
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
    for row in report.get("digested") or []:
        lines.append("  📊 в сводку #%s (%s): %s"
                     % (row.get("id"), row.get("kind"), row.get("why")))
    for row in report.get("closed") or []:
        lines.append("  🔒 %s #%s (решил ОТБОР, не владелец): %s"
                     % (zayavki_pc.SIFT_OUTCOME, row.get("id"), row.get("why")))
    for row in report.get("kept") or []:
        lines.append("  ⏳ ждёт владельца #%s: %s" % (row.get("id"), row.get("why")))
    if report.get("summary"):
        lines.append("")
        lines.append("СВОДКА: " + report["summary"])
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
