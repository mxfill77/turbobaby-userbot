# -*- coding: utf-8 -*-
"""orch_loop_guard.py — витку дирижёра ПК: показ фаз, причина простоя и потолок чтений моста.

ЗАЧЕМ (задание Штаба 0125-78p.0210, ORCHIDLEGUARD0210). Виток демона синхронный, и на отказе
моста Apps Script он стоял до 1143 с без работы (02.10, «залипание вызова моста»), а в логе от
этого оставалась одна строка «виток растянулся» — без фаз и без причины. Здесь три вещи:

1. ПОКАЗ (включён ВСЕГДА — он ничего не решает): длительность фаз витка, каждое плечо моста
   (действие → вызовов/секунд/отказов) и `idle_reason` из ЗАКРЫТОГО списка `IDLE_REASONS`.
2. ПОТОЛОК ЧТЕНИЙ витка — выключатель `ORCH_READ_BUDGET_SEC` (умолчание 0 = прежнее поведение
   байт-в-байт). При >0 суммарное время GET-чтений моста за виток ограничено, и ограничено
   ВНУТРИ вызова: транспорт режет таймаут сокета и паузы повторов по остатку. Исчерпан —
   чтение закрывается исходом НЕИЗВЕСТНО (`ReadSkipped`), а не провалом; виток идёт дальше.
3. НЕ ПЕРЕЧИТЫВАТЬ ВТОРИЧНОЕ (тот же выключатель): перечисление папки мозга упало в этом
   витке — зависимые чтения мозга в этом же витке не делаются вовсе.

ПОЧЕМУ ОТДЕЛЬНЫЙ МОДУЛЬ, А НЕ ПОЛЕ ДЕМОНА — ЭТО ГЛАВНОЕ. Потолок 04.09 (`BridgeLoopBudget` в
`pc_orchestrator.py`) откатан в 0 потому, что демон живёт как `__main__`, а ящик и ступени ходят
в мост ОДОЛЖЕННЫМ клиентом через `import pc_orchestrator` — вторым объектом модуля со своим
счётчиком, который `reset()` не видел никогда. Модуль без побочных импортов существует в
процессе в ОДНОМ экземпляре (`sys.modules`), и `__main__`, и одолженная копия, и транспорт видят
один и тот же `G`. Класс 04.09 закрыт устройством, а не дисциплиной.

ЧТО НЕ РЕЖЕТСЯ НИКОГДА (доктрина 04.09 сохранена дословно): мутации (POST) и чтения-
доказательства пути записи (`Bridge._in_status`: CLAIM-VERIFY/COMPLETE-VERIFY) — они не входят
в область `reading()`, а транспорт режет ТОЛЬКО внутри неё. POST-время в потолок чтений не идёт
(задание: «суммарное время ЧТЕНИЙ»), но в показе видно отдельной строкой.

Потоки: две руки `process_new` — потоки, поэтому область чтения живёт в `threading.local`, а
счётчики под замком. Модуль ничего не импортирует из полосы — его импортирует транспорт.
"""

import os
import threading
import time
from contextlib import contextmanager

BUDGET_ENV = "ORCH_READ_BUDGET_SEC"
WAKE_ENV = "ORCH_WAKE_ON_DONE"

# ЗАКРЫТЫЙ СПИСОК причин (задание): других слов строка витка не печатает ни одной веткой.
BRIDGE_STALL = "bridge_stall"
BRAIN_LIST_TIMEOUT = "brain_list_timeout"
OWNER_CARD = "owner_card"
SIGNAL_STOP = "signal_stop"
NO_TASKS = "no_tasks"
OK = "ok"
IDLE_REASONS = (BRIDGE_STALL, BRAIN_LIST_TIMEOUT, OWNER_CARD, SIGNAL_STOP, NO_TASKS, OK)

# ВИТОК БЕЗ ОТКАЗОВ, НО С ДОЛГИМИ ЧТЕНИЯМИ — ТОЖЕ ЗАЛИПАНИЕ. Повторы транспорта, кончившиеся
# успехом, отказом наружу не выходят: виток 02.10 15:57→16:36 стоял 620 с, не записав в лог ни
# одного отказа. Порог 240 с — ровно та граница, после которой виток с POLL_SEC=60 сам
# называется растянувшимся (`LOOP_GAP_NOTE_SEC` = 300 с): здоровое чтение витка короче её.
STALL_READ_SEC = float(os.getenv("ORCH_STALL_READ_SEC", "240") or "240")

BRAIN_LEGS = ("list_brain_folder", "read_doc", "list_brain")
_TIMEOUT_NAMES = ("TimeoutError", "timeout", "ReadTimeout")


def read_budget_sec(env=None):
    """Значение выключателя потолка. Мусор/минус/пусто → 0 (выключено, прежнее поведение)."""
    raw = (env if env is not None else os.environ).get(BUDGET_ENV, "")
    try:
        v = float(str(raw).strip() or "0")
    except ValueError:
        return 0.0
    return v if v > 0 else 0.0


def wake_on_done(env=None):
    """Быстрый подхват — строго «1» (как остальные рубильники полосы); всё прочее — выключено."""
    return str((env if env is not None else os.environ).get(WAKE_ENV, "") or "").strip() == "1"


class ReadSkipped(RuntimeError):
    """Чтение НЕ СДЕЛАНО (или прервано) потолком витка — третий исход, а не отказ моста.

    `no_retry` читают слои повторов (`brain_writer._retry_read`): повтор такого чтения ничего
    не лечит, а жжёт паузы после того, как время витка уже кончилось."""

    no_retry = True

    def __init__(self, why, leg=""):
        self.why = str(why or "budget")
        self.leg = str(leg or "?")
        super().__init__("НЕ СПРАШИВАЛИ (%s): %s — исход НЕИЗВЕСТНО, отложено до следующего "
                         "витка" % (self.leg, _WHY_WORDS.get(self.why, self.why)))


_WHY_WORDS = {
    "budget": "потолок времени на чтения моста в этом витке исчерпан",
    "brain_known_fail": "перечисление папки мозга в этом витке уже упало — зависимое чтение не "
                        "повторяем",
}


def _fail_kind(exc):
    """Имя отказа для показа: `timeout` отдельно (по нему узнаётся brain_list_timeout)."""
    if exc is None:
        return ""
    if isinstance(exc, ReadSkipped):
        return "cut"
    name = type(exc).__name__
    text = str(exc)
    if name in _TIMEOUT_NAMES or "timed out" in text.lower() or "TimeoutError" in text:
        return "timeout"
    return name


class Guard:
    """Состояние ОДНОГО витка. Один объект на процесс (`G`); часы инъектируются."""

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._tl = threading.local()
        self.limit = 0.0
        self.open = False
        self._reset()

    def _reset(self):
        self.t0 = self.clock()
        self.spent = 0.0          # секунды GET-чтений витка (включая повторы и неразрезаемые)
        self.legs = {}            # действие → {"n", "sec", "fail", "cut", "kinds"}
        self.phases = {}          # фаза → секунды
        self.cut = 0              # чтений, не сделанных/прерванных потолком
        self.tripped = False      # потолок перейдён в этом витке
        self.brain_fail = ""      # причина упавшего перечисления папки мозга ("" — не падало)
        self.brain_fail_kind = ""
        self.facts = {}

    # ── виток ─────────────────────────────────────────────────────────────────────────
    def begin(self, limit=None, clock=None):
        """Открыть виток: обнулить ВСЁ. limit=None → значение выключателя из среды."""
        with self._lock:
            if clock is not None:
                self.clock = clock
            self.limit = read_budget_sec() if limit is None else max(0.0, float(limit))
            self.open = True
            self._reset()

    @property
    def on(self):
        """Режем ли что-нибудь в этом витке. Выключатель 0 → никогда."""
        return self.open and self.limit > 0

    def now(self):
        return self.clock()

    def remaining(self):
        return max(0.0, self.limit - self.spent) if self.on else None

    def exhausted(self):
        return self.on and self.spent >= self.limit

    # ── область разрезаемого чтения ─────────────────────────────────────────────────
    def deadline(self):
        """Срок текущего разрезаемого чтения ЭТОГО потока или None (прежнее поведение)."""
        stack = getattr(self._tl, "stack", None)
        return stack[-1] if stack else None

    def gate(self, leg):
        """Можно ли начать разрезаемое чтение `leg`. → None (да) | ReadSkipped (нет)."""
        if not self.on:
            return None
        if leg in BRAIN_LEGS and self.brain_fail:
            return ReadSkipped("brain_known_fail", leg)
        if self.exhausted():
            return ReadSkipped("budget", leg)
        return None

    @contextmanager
    def reading(self, leg):
        """Разрезаемое чтение. Выключено → ничего не делает вовсе (транспорт видит None).

        Включено: пропуск поднимается `ReadSkipped` ДО сети; иначе транспорту отдаётся срок
        `сейчас + остаток`, по которому он режет таймаут сокета и паузы своих повторов."""
        if not self.on:
            yield None
            return
        skip = self.gate(leg)
        if skip is not None:
            self.note_cut(leg)
            raise skip
        stack = getattr(self._tl, "stack", None)
        if stack is None:
            stack = self._tl.stack = []
        stack.append(self.now() + self.remaining())
        try:
            yield stack[-1]
        finally:
            stack.pop()

    def note_cut(self, leg):
        with self._lock:
            self.cut += 1
            self.tripped = True
            st = self.legs.setdefault(str(leg or "?"), _leg())
            st["cut"] += 1

    # ── учёт (транспорт зовёт на КАЖДЫЙ вызов, в любом режиме) ───────────────────────
    def account(self, action, method, sec, exc=None):
        if not self.open:
            return
        sec = max(0.0, float(sec))
        kind = _fail_kind(exc)
        with self._lock:
            st = self.legs.setdefault(str(action or method or "?"), _leg())
            st["n"] += 1
            st["sec"] += sec
            if str(method).upper() == "GET":
                st["get"] = True
                self.spent += sec
                if self.limit > 0 and self.spent >= self.limit:
                    self.tripped = True
            if kind == "cut":
                st["cut"] += 1
                self.cut += 1
                self.tripped = True
            elif kind:
                st["fail"] += 1
                st["kinds"][kind] = st["kinds"].get(kind, 0) + 1

    def note_brain_fail(self, why, kind=""):
        """Перечисление папки мозга УПАЛО (а не «пусто» и не «усечено»). Факт пишется всегда,
        пропуск зависимых чтений по нему — только при включённом выключателе (`gate`)."""
        with self._lock:
            if not self.brain_fail:
                self.brain_fail = str(why or "перечисление папки мозга упало")[:200]
                self.brain_fail_kind = str(kind or "")

    def note(self, **facts):
        with self._lock:
            self.facts.update(facts)

    @contextmanager
    def phase(self, name):
        t0 = self.now()
        try:
            yield
        finally:
            dt = max(0.0, self.now() - t0)
            with self._lock:
                self.phases[name] = self.phases.get(name, 0.0) + dt

    # ── итог витка ───────────────────────────────────────────────────────────────────
    def snapshot(self):
        with self._lock:
            legs = {k: dict(v, kinds=dict(v["kinds"])) for k, v in self.legs.items()}
            return {"total": max(0.0, self.now() - self.t0), "limit": self.limit,
                    "spent": self.spent, "cut": self.cut, "tripped": self.tripped,
                    "brain_fail": self.brain_fail, "brain_fail_kind": self.brain_fail_kind,
                    "phases": dict(self.phases), "legs": legs, "facts": dict(self.facts)}


def _leg():
    return {"n": 0, "sec": 0.0, "fail": 0, "cut": 0, "kinds": {}, "get": False}


G = Guard()


# ── чистые функции: причина и строка ──────────────────────────────────────────────────
def facts_of(snap):
    """Снимок витка → плоские факты для `idle_reason` (чистая, голден)."""
    f = dict(snap.get("facts") or {})
    legs = snap.get("legs") or {}
    fails = sum(int(v.get("fail") or 0) for v in legs.values())
    brain_kind = str(snap.get("brain_fail_kind") or "")
    if not brain_kind and snap.get("brain_fail"):
        brain_kind = "fail"
    return {"worked": bool(f.get("worked")), "placed": bool(f.get("placed")),
            "stopped": bool(f.get("stopped")), "box_stop": bool(f.get("box_stop")),
            "owner_busy": bool(f.get("owner_busy")), "tripped": bool(snap.get("tripped")),
            "bridge_fails": fails, "brain_fail": brain_kind,
            "read_sec": float(snap.get("spent") or 0.0)}


def idle_reason(f, stall_sec=None):
    """Факты витка → ОДНО слово из `IDLE_REASONS`. Чистая функция.

    Порядок ветвей — это и есть правило:
      1) работа была (задача закрыта или ящик поставил ряд) → ok — виток не простаивал;
      2) перечисление папки мозга упало по таймауту → brain_list_timeout (частное раньше общего);
      3) потолок сработал · отказы плеч моста · перечисление упало иначе · чтения дольше
         порога → bridge_stall;
      4) стоп-флаг демона или сигнальная остановка ящика → signal_stop;
      5) в очереди работа владельца (карточка ждёт его ответа) → owner_card;
      6) иначе → no_tasks."""
    stall = STALL_READ_SEC if stall_sec is None else float(stall_sec)
    if f.get("worked") or f.get("placed"):
        return OK
    if f.get("brain_fail") == "timeout":
        return BRAIN_LIST_TIMEOUT
    if (f.get("tripped") or int(f.get("bridge_fails") or 0) > 0 or f.get("brain_fail")
            or float(f.get("read_sec") or 0.0) >= stall):
        return BRIDGE_STALL
    if f.get("stopped") or f.get("box_stop"):
        return SIGNAL_STOP
    if f.get("owner_busy"):
        return OWNER_CARD
    return NO_TASKS


_PHASE_ORDER = ("poll", "process_new", "box", "journal")
_PHASE_WORDS = {"poll": "очередь", "process_new": "process_new", "box": "ящик",
                "journal": "журнал"}


def line(snap, reason=None, wake=""):
    """Одна строка в конце витка. Чистая (голден): числа, причина, плечи моста."""
    reason = reason or idle_reason(facts_of(snap))
    ph = snap.get("phases") or {}
    parts = ["%s %.1f" % (_PHASE_WORDS[k], ph[k]) for k in _PHASE_ORDER if k in ph]
    known = sum(ph.get(k, 0.0) for k in ("poll", "box"))
    parts.append("прочее %.1f" % max(0.0, float(snap.get("total") or 0.0) - known))
    legs = snap.get("legs") or {}
    lp = []
    for name in sorted(legs, key=lambda k: -float(legs[k].get("sec") or 0.0)):
        st = legs[name]
        bit = "%s %d×%.1fс" % (name, int(st.get("n") or 0), float(st.get("sec") or 0.0))
        if st.get("fail"):
            bit += " отказ %d(%s)" % (st["fail"], ",".join(
                "%s:%d" % kv for kv in sorted((st.get("kinds") or {}).items())))
        if st.get("cut"):
            bit += " срезано %d" % st["cut"]
        lp.append(bit)
    lim = float(snap.get("limit") or 0.0)
    budget = ("потолок чтений %.0fс: потрачено %.1fс%s" % (
        lim, float(snap.get("spent") or 0.0), ", СРАБОТАЛ" if snap.get("tripped") else "")
              if lim > 0 else "потолок чтений выкл (чтения %.1fс)" % float(snap.get("spent") or 0.0))
    s = "ВИТОК %.1fс idle_reason=%s · фазы: %s · мост: %s · %s" % (
        float(snap.get("total") or 0.0), reason, ", ".join(parts), "; ".join(lp) or "вызовов 0",
        budget)
    if snap.get("brain_fail"):
        s += " · перечисление мозга упало"
    if wake:
        s += " · " + wake
    return s
