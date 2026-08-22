# -*- coding: utf-8 -*-
"""
trainer_pin.py — ПИН ВНЕШНЕЙ ГРАНИЦЫ прогона тренажёра (лекарство от флапа набора, 22.08.2026).

ЗАЧЕМ. Прогон набора (`trainer_run.py`) даёт РАЗНЫЙ состав красных на ОДНОМ коммите: 22.08 три
живых прогона подряд дали 11/11/11 из 12, а красный кейс переезжал (5 → 10 → 5). Число, у которого
красный переезжает, ключом к воротам быть не может: непонятно, какой прогон брать. Флап при этом
живёт НЕ в наборе — измерено пробами того же дня:

  · `probe_env.py`  — все ЧЕТЫРЕ живых входа промпта (faq, парк, playbook, pricing_note) и САМ
    системный промпт (26063 симв.) за 4 пробы подряд не сдвинулись НИ НА БАЙТ;
  · `probe_head.py` — тот же промпт побайтно, поданный НАСТОЯЩЕЙ голове 3 раза, дал 3 РАЗНЫХ
    ответа (403/205/401 симв. на кейсе 5; 188/208/229 на кейсе 10);
  · сидения 04:18 и 11:16 разошлись по `pricing_note` кейса 12 (132 симв. «точная цена из
    Календаря сейчас недоступна» против 588 симв. полного quote-блока) — это ЖИВАЯ СЕТЬ.

Значит исход прогона зависит от трёх вещей, и ДВЕ из них — не код бота: выборка головы и
доступность моста. Этот модуль убирает обе из ЗАМЕРА, оставляя в нём ровно код.

ЧТО ДЕЛАЕТ. Записывает живой снимок ГРАНИЦЫ и потом проигрывает его:

  граница                                  что за ней                    как ключуется
  ---------------------------------------  ----------------------------  -----------------------
  pricing._default_get(params)             цена/парк из Календаря        params БЕЗ токена
  delivery._default_get(params)            зоны доставки                 params БЕЗ токена
  suggest._bridge_read_doc(name)           FAQ из Brain                  имя дока
  suggest._cli_llm / _default_llm          ГОЛОВА (claude CLI)           sha(system) + sha(user)
  suggest.load_playbook()                  книга правил — ЛОКАЛЬНЫЙ файл, sha снимка
                                           который переписывает боевой
                                           suggest.append_playbook_rule
  price_gate._bridge_caller()              СТОРОЖ СВЕЖЕСТИ ЦЕНЫ — девять   action + params БЕЗ
    (шестая дверь, `GatePin`, 23.08)       GET к Календарю мимо первых     токена
                                           пяти границ
  suggest.now_phuket / price_freshness     КАЛЕНДАРЬ — живая дата, из      (не ключуется: это
    .judge (седьмая дверь, `Clock`, 23.08) которой продукт САМ строит      ИСТОЧНИК ключей, а
                                           ключи к первым шести            не потребитель)

ПОЧЕМУ ЭТО НЕ ПОДДЕЛКА ПРОВЕРКИ — четыре свойства, каждое проверяемо:
 1. Снимок СНЯТ ЖИВЬЁМ и ВСЛЕПУЮ: один проход, до того как известен хоть один вердикт, без
    переигрываний и без выбора «удачного» ответа. Что голова сказала — то и заморожено, красное
    оно или зелёное. Код это ДЕРЖИТ: `record`-режим отказывается писать поверх существующего
    снимка (`FileExistsError`), поэтому «перезаписать, пока не позеленеет» технически невозможно.
 2. Ни один чек не снят и ни один порог не сдвинут: корпус, `expect`, `skip` и `TRAINER_MIN_*`
    этот модуль не читает вовсе. Над замороженным текстом головы работает ВЕСЬ код продукта —
    postcheck_*, compose_*, ensure_*, drop_*, enforce_* — и все 15 чеков кейса.
 3. Ошибка в ответе бота по-прежнему краснит: доказывается ОТРИЦАТЕЛЬНЫМ тестом (`mutate_head`
    ниже) — заведомая порча ответа обязана дать красный во ВСЕХ прогонах.
 4. Промах кэша НЕ прощается молча: он считается и виден в отчёте. Мост на промахе кидает
    `PinMiss` (вызывающие ловят широкий Exception → штатный фолбэк), голова отдаёт пустую строку
    (тот же путь, что документированный фолбэк «LLM-тайбрейк недоступен → НЕ трогаем»).

ЧЕСТНАЯ ЦЕНА, которую нельзя замолчать: замороженная голова больше НЕ ловит дрейф самой головы.
Набор на пине меряет КОД, а не живую модель. Поэтому число с пина — это число кода, и вторым
ключом к воротам (`client_contour` ждёт «зелёный прогон через тренажёр» ЖИВОГО продукта) оно
служить НЕ МОЖЕТ.

ЧТО ИЗМЕНИЛОСЬ 23.08.2026 — три вещи, и все против этой оговорки, а не в обход неё:
  · `GatePin` (внизу файла) закрывает ШЕСТУЮ живую дверь — сторожа свежести цены. До неё «пин»
    держал пять границ, а замер всё равно стоял на живой сети: 100% времени набора и один
    сетевой вердикт на восемь кейсов;
  · `Pin(..., pin_head=False)` — ВНЕШНИЕ двери из снимка, ГОЛОВА ЖИВАЯ. Это и есть режим
    «честного живого числа»: из замера убрана сеть, но не модель. Оговорка выше снимается
    ТОЛЬКО в этом режиме и ТОЛЬКО для головы; на `pin_head=True` она в силе дословно;
  · `Clock` (внизу файла) закрывает СЕДЬМУЮ дверь — КАЛЕНДАРЬ, из-за которой снимок жил ровно
    сутки: 111 ключей моста из 125 несут ЖИВУЮ дату. Без неё «воспроизводимый замер» кончался
    в полночь.

ЗАМОК ОТ ЗЛОУПОТРЕБЛЕНИЯ. `trainer_run.py` этот модуль НЕ ИМПОРТИРУЕТ и знать о нём не должен:
пин ставится только СНАРУЖИ, в измерительном раннере. Поэтому вердикт для ворот
(`trainer_run.write_verdict`) физически не может быть снят на пине — там, где пин, там нет
`write_verdict`, а там, где `write_verdict`, нет пина.

Секреты: `token` вырезается из params ДО хеширования и ДО записи, в снимок не попадает ни разу;
`_bridge_read_doc` оборачивается целиком, поэтому токен там даже не виден.
"""

import copy
import datetime
import hashlib
import io
import json
import os
import time

SNAPSHOT_VERSION = 2
GATE_SNAPSHOT_VERSION = 1
_SECRET_KEYS = ("token", "key", "secret", "password", "auth")

# Момент НАЧАЛА съёмки, aware-UTC, в мету снимка. Пишется САМИМ снимком, а не вызывающим:
# «часы замера» — свойство снимка, и забыть их снять не должно быть возможно (`Clock` без них
# не заводится, а старые снимки читает по `снят`, см. `Clock.from_meta`).
MOMENT_KEY = "момент"


def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


class PinMiss(Exception):
    """Ключа нет в снимке. Мост её кидает — вызывающие ловят широкий Exception и уходят в
    ШТАТНЫЙ фолбэк, ровно как при недоступном мосте. Детерминированно и посчитано."""


def _sha(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:16]


def _scrub(params):
    """params БЕЗ секретов — и для ключа, и для записи на диск."""
    if not isinstance(params, dict):
        return {"_raw": repr(params)}
    return {k: v for k, v in params.items() if k.lower() not in _SECRET_KEYS}


def _pkey(params):
    return json.dumps(_scrub(params), ensure_ascii=False, sort_keys=True)


def _hkey(system, user):
    return _sha(system) + ":" + _sha(user)


class Pin(object):
    """Снимок границы + подмена функций границы. Режимы: 'record' | 'replay'."""

    TARGETS = (("pricing", "_default_get"), ("delivery", "_default_get"))

    def __init__(self, path, mode, pin_head=True):
        """`pin_head=False` — ВНЕШНИЕ двери из снимка, а ГОЛОВА ЖИВАЯ.

        Заведено 23.08.2026 ради честного живого числа: пин головы убирает из замера саму модель
        (`trainer_pin` строки 46-49 это и говорят — число с пина ключом к воротам быть не может).
        Снимок при этом НЕ ТРОГАЕТСЯ ни на байт: его секция `head` просто не читается, и промахов
        головы в таком прогоне не бывает по построению (`misses['head']` остаётся 0 — считать
        нечего, живая голова отвечает сама). Ослаблением проверки это не является: молчащую
        голову ловит `trainer_run._HeadWatch` (правило «МОЛЧАЩАЯ ГОЛОВА = НЕИЗВЕСТНО», 22.08),
        и оно не зависит от того, кто дал ответ — снимок или CLI."""
        if mode not in ("record", "replay"):
            raise ValueError("режим только 'record' или 'replay'")
        self.path, self.mode, self.pin_head = path, mode, bool(pin_head)
        self.moment = _now_utc() if mode == "record" else None
        self.data = {"version": SNAPSHOT_VERSION, "bridge": {}, "read_doc": {},
                     "head": {}, "playbook": None, "meta": {}}
        self.misses = {"bridge": 0, "read_doc": 0, "head": 0}
        self.calls = {"bridge": 0, "read_doc": 0, "head": 0}
        self.per_case = {}
        self._case = None
        self._orig = []
        self._mutate = None
        if mode == "replay":
            with io.open(path, encoding="utf-8") as f:
                self.data = json.load(f)
            if self.data.get("version") != SNAPSHOT_VERSION:
                raise ValueError("снимок другой версии: %r" % self.data.get("version"))
        elif os.path.exists(path):
            # Замок свойства №1: переснять поверх — значит получить право переигрывать голову,
            # пока не позеленеет. Такого права у замера быть не должно.
            raise FileExistsError("снимок уже существует, перезапись запрещена: %s" % path)

    # ── учёт по кейсам: сколько раз какая граница дёрнулась (доказательство п.3) ──────────────
    def case(self, cid):
        self._case = cid
        self.per_case.setdefault(cid, {"bridge": 0, "read_doc": 0, "head": 0})

    def _tick(self, kind):
        self.calls[kind] += 1
        if self._case is not None:
            self.per_case[self._case][kind] += 1

    # ── отрицательный тест: заведомая порча ОТВЕТА БОТА, в памяти и только на замер ───────────
    def mutate_head(self, fn):
        """fn(case_id, answer) -> answer' — применяется к ответу головы ПОСЛЕ проигрывания.
        Живёт в памяти: снимок на диске не трогается, откатывать нечего."""
        self._mutate = fn

    # ── сами границы ─────────────────────────────────────────────────────────────────────────
    def _wrap_get(self, real, tag):
        def wrapped(params):
            self._tick("bridge")
            k = _pkey(params)
            if self.mode == "record":
                v = real(params)
                self.data["bridge"][k] = {"tag": tag, "params": _scrub(params),
                                          "value": copy.deepcopy(v)}
                return v
            hit = self.data["bridge"].get(k)
            if hit is None:
                self.misses["bridge"] += 1
                raise PinMiss("моста нет в снимке: %s" % k[:160])
            return copy.deepcopy(hit["value"])
        return wrapped

    def _wrap_read_doc(self, real):
        def wrapped(name):
            self._tick("read_doc")
            if self.mode == "record":
                v = real(name)
                self.data["read_doc"][str(name)] = v
                return v
            if str(name) not in self.data["read_doc"]:
                self.misses["read_doc"] += 1
                return None                     # ровно то, что отдаёт живой мост при промахе
            return self.data["read_doc"][str(name)]
        return wrapped

    def _wrap_head(self, real):
        def wrapped(system, user):
            self._tick("head")
            k = _hkey(system, user)
            if self.mode == "record":
                v = real(system, user)
                self.data["head"][k] = {"case": self._case, "system_len": len(system or ""),
                                        "user_len": len(user or ""), "answer": v}
                out = v
            else:
                hit = self.data["head"].get(k)
                if hit is None:
                    self.misses["head"] += 1
                    out = ""                    # документированный фолбэк «голова недоступна»
                else:
                    out = hit["answer"]
            if self._mutate is not None:
                out = self._mutate(self._case, out)
            return out
        return wrapped

    # ── установка/снятие ─────────────────────────────────────────────────────────────────────
    def install(self):
        import importlib
        import suggest
        for modname, attr in self.TARGETS:
            try:
                mod = importlib.import_module(modname)
            except ImportError:
                continue
            real = getattr(mod, attr, None)
            if real is None:
                continue
            self._orig.append((mod, attr, real))
            setattr(mod, attr, self._wrap_get(real, "%s.%s" % (modname, attr)))
        # кэши границы гасим: иначе первый кейс наполнит их живьём, а снимок останется без ключей
        for modname, cache, empty in (("pricing", "_FLEET_CACHE", {"ts": 0.0, "data": None}),
                                      ("delivery", "_ZONES_CACHE", {"ts": 0.0, "data": None})):
            try:
                mod = importlib.import_module(modname)
            except ImportError:
                continue
            if hasattr(mod, cache):
                self._orig.append((mod, cache, getattr(mod, cache)))
                setattr(mod, cache, dict(empty))

        self._orig.append((suggest, "_bridge_read_doc", suggest._bridge_read_doc))
        suggest._bridge_read_doc = self._wrap_read_doc(suggest._bridge_read_doc)
        if self.pin_head:
            for attr in ("_cli_llm", "_default_llm"):
                real = getattr(suggest, attr)
                self._orig.append((suggest, attr, real))
                setattr(suggest, attr, self._wrap_head(real))

        # playbook — ЛОКАЛЬНЫЙ файл, в который пишет боевой suggest.append_playbook_rule.
        # Замер обязан видеть один и тот же текст, а не тот, что успел дописать живой процесс.
        real_pb = suggest.load_playbook
        self._orig.append((suggest, "load_playbook", real_pb))
        if self.mode == "record":
            self.data["playbook"] = real_pb()
        pb_text = self.data.get("playbook") or ""
        suggest.load_playbook = lambda: pb_text
        return self

    def uninstall(self):
        for mod, attr, real in reversed(self._orig):
            setattr(mod, attr, real)
        self._orig = []

    def save(self, meta=None):
        m = dict(meta or {})
        if self.moment is not None:
            m.setdefault(MOMENT_KEY, self.moment.isoformat())
        self.data["meta"] = dict(self.data.get("meta") or {}, **m)
        tmp = self.path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        return self.path

    def stats(self):
        return {"режим": self.mode, "голова": "из снимка" if self.pin_head else "ЖИВАЯ",
                "вызовов": dict(self.calls), "промахов": dict(self.misses),
                "ключей": {"мост": len(self.data["bridge"]), "доков": len(self.data["read_doc"]),
                           "головы": len(self.data["head"])},
                "playbook_sha": _sha(self.data.get("playbook") or ""),
                "по_кейсам": self.per_case}

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False


# ═══════════════════ ШЕСТАЯ ДВЕРЬ: СТОРОЖ СВЕЖЕСТИ ЦЕНЫ (23.08.2026) ═══════════════════════════

class GatePin(object):
    """Пин ШЕСТОЙ живой двери — `price_gate` → девять GET к Календарю.

    ЗАЧЕМ ОТДЕЛЬНЫМ КЛАССОМ И ОТДЕЛЬНЫМ ФАЙЛОМ. Замер 22.08
    (`docs/artifacts/2026-08-22-pin-degeneracy-check.md`, `_scratch_pindegen_0822/res_gate.json`):
    прогон «на пине» всё равно ходил в живую сеть — 17 спросов сторожа, **42.80с из 42.89с
    времени набора (100%)**, 8 кейсов из 12. Сторож берёт клиент моста, одолженный у демона
    (`price_gate._bridge_caller` → `queue_snapshot_pc._daemon.bc._get`), и идёт мимо ВСЕХ пяти
    границ `Pin` — промахи `Pin` при этом нули, потому что `Pin` про эту дверь не знает.
    Отдельный файл снимка — не прихоть: секции `Pin` версионированы (`SNAPSHOT_VERSION`), и
    подмешивание шестой двери туда обесценило бы снимок 22.08 (версия 2), то есть заставило бы
    ПЕРЕСНЯТЬ уже снятое. Пересъёмка запрещена, поэтому дверь живёт своим снимком.

    ПОЧЕМУ ЭТО НЕ ПОДДЕЛКА — те же четыре свойства, что у `Pin`:
     1. снимок снят ЖИВЬЁМ и ВСЛЕПУЮ, одним проходом; `record` отказывается писать поверх
        (`FileExistsError`), поэтому «переигрывать, пока не позеленеет» технически невозможно;
     2. ни один чек не снят и ни один порог не сдвинут — корпус и `expect` класс не читает;
     3. промах двери НЕ ПРОЩАЕТСЯ и НЕ КРАСИТ, а даёт **НЕИЗВЕСТНО** — см. ниже;
     4. над замороженным вердиктом работает ВЕСЬ код продукта: `price_gate.verdict`, кэш,
        `price_freshness.judge`, `bot_action` и обе ветки `suggest`, которые сторожа спрашивают.

    ПРОМАХ = НЕИЗВЕСТНО, А НЕ КРАСНОЕ — и это главное отличие от наивного пина. Отказ двери
    продукт обязан толковать как «цену не называем» (правило владельца 17.08), поэтому пустой
    снимок дал бы КРАСНЫЙ набор — то есть прибор обвинил бы КОД в том, что промолчала ГРАНИЦА
    ЗАМЕРА. Это ровно та ложь, от которой 22.08 заведено правило «МОЛЧАЩАЯ ГОЛОВА = НЕИЗВЕСТНО»
    (`trainer_run`, узел того же имени); здесь оно зеркалится на шестую дверь. Кейс, спросивший
    сторожа при промахнувшемся снимке, получает исход НЕИЗВЕСТНО: не зелёный (доказывать нечем)
    и не красный (кода не уличили).

    ПЯТНО ЛИПКОЕ, И ЭТО НЕ НЕБРЕЖНОСТЬ. Вердикт сторожа КЭШИРУЕТСЯ на `PRICE_GATE_TTL_MIN`
    (дефолт 30 мин) — за набор дверь дёргается ОДИН раз, а обслуживает восемь кейсов. Считай мы
    «неизвестно» только по свежим промахам, семь кейсов из восьми получили бы вердикт, рождённый
    промахом, и назывались бы измеренными. Поэтому `tainted` держится до `new_run()`, который
    сбрасывает и кэш продукта (`price_gate.reset()`), и само пятно — так каждый прогон честно
    переигрывает дверь заново.

    Секреты: клиент моста поднимается ТОЛЬКО в режиме `record` и ТОЛЬКО самим `price_gate`
    (запрет класса 328 цел — здесь их не читают и не видят); `params` чистятся `_scrub` до
    хеширования и до записи.
    """

    def __init__(self, path, mode):
        if mode not in ("record", "replay"):
            raise ValueError("режим только 'record' или 'replay'")
        self.path, self.mode = path, mode
        self.moment = _now_utc() if mode == "record" else None
        self.data = {"version": GATE_SNAPSHOT_VERSION, "gate": {}, "meta": {}}
        self.calls = self.misses = self.asks = 0
        self.seconds = 0.0
        self.tainted = False              # вердикт в игре рождён промахом снимка
        self.unknown = set()              # кейсы, спросившие сторожа при таком вердикте
        self.asks_by_case = {}
        self._case = None
        self._orig = []
        if mode == "replay":
            with io.open(path, encoding="utf-8") as f:
                self.data = json.load(f)
            if self.data.get("version") != GATE_SNAPSHOT_VERSION:
                raise ValueError("снимок двери другой версии: %r" % self.data.get("version"))
        elif os.path.exists(path):
            raise FileExistsError("снимок двери уже существует, перезапись запрещена: %s" % path)

    # ── учёт ─────────────────────────────────────────────────────────────────────────────────
    def case(self, cid):
        self._case = cid
        self.asks_by_case.setdefault(cid, 0)

    def new_run(self):
        """Начать прогон с ЧИСТОЙ двери: кэш вердикта продукта сброшен, пятно снято.

        Без этого прогоны 2 и 3 переиспользовали бы вердикт, снятый в прогоне 1 (окно 30 мин), —
        именно так 22.08 совпадение трёх прогонов частично объяснялось КЭШЕМ, а не пином."""
        import price_gate
        price_gate.reset()
        self.tainted = False
        self.unknown = set()
        self.asks_by_case = {}
        self._case = None

    # ── сама дверь ───────────────────────────────────────────────────────────────────────────
    def _key(self, action, kw):
        return json.dumps({"action": str(action), "params": _scrub(kw)},
                          ensure_ascii=False, sort_keys=True)

    def _wrap_factory(self, real):
        """`price_gate._bridge_caller()` отдаёт функцию `caller(action, **kw)`. В replay живой
        клиент моста не поднимается ВОВСЕ: `real` не зовётся ни разу."""
        def factory():
            inner = real() if self.mode == "record" else None

            def caller(action, **kw):
                t0 = time.time()
                self.calls += 1
                k = self._key(action, kw)
                try:
                    if self.mode == "record":
                        v = inner(action, **kw)
                        self.data["gate"][k] = {"action": str(action), "params": _scrub(kw),
                                                "value": copy.deepcopy(v)}
                        return v
                    hit = self.data["gate"].get(k)
                    if hit is None:
                        self.misses += 1
                        self.tainted = True
                        raise PinMiss("двери сторожа нет в снимке: %s" % k[:160])
                    return copy.deepcopy(hit["value"])
                finally:
                    self.seconds += time.time() - t0
            return caller
        return factory

    def _wrap_allow(self, real):
        """Наблюдатель СПРОСА. Считает, кто спрашивал сторожа, и метит кейс «неизвестно», если
        вердикт, которым его обслужили, рождён промахом снимка — свежим или лежащим в кэше."""
        def wrapped(*a, **kw):
            self.asks += 1
            if self._case is not None:
                self.asks_by_case[self._case] = self.asks_by_case.get(self._case, 0) + 1
            out = real(*a, **kw)
            if self.tainted and self._case is not None:
                self.unknown.add(self._case)
            return out
        return wrapped

    # ── установка/снятие ─────────────────────────────────────────────────────────────────────
    def install(self):
        import price_gate
        self._orig.append((price_gate, "_bridge_caller", price_gate._bridge_caller))
        price_gate._bridge_caller = self._wrap_factory(price_gate._bridge_caller)
        self._orig.append((price_gate, "allow", price_gate.allow))
        price_gate.allow = self._wrap_allow(price_gate.allow)
        price_gate.reset()                # кэш в памяти процесса: иначе дверь не дёрнется ни разу
        return self

    def uninstall(self):
        import price_gate
        for mod, attr, real in reversed(self._orig):
            setattr(mod, attr, real)
        self._orig = []
        price_gate.reset()                # замороженный вердикт не смеет пережить замер

    def save(self, meta=None):
        m = dict(meta or {})
        if self.moment is not None:
            m.setdefault(MOMENT_KEY, self.moment.isoformat())
        self.data["meta"] = dict(self.data.get("meta") or {}, **m)
        tmp = self.path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        return self.path

    def stats(self):
        return {"режим": self.mode, "ключей": len(self.data.get("gate") or {}),
                "вызовов_двери": self.calls, "промахов": self.misses,
                "спросов_сторожа": self.asks, "секунд_у_двери": round(self.seconds, 2),
                "пятно": self.tainted, "неизвестно": sorted(self.unknown),
                "спросов_по_кейсам": dict(self.asks_by_case)}

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False


# ═══════════════════ СЕДЬМАЯ ДВЕРЬ: КАЛЕНДАРЬ (23.08.2026) ════════════════════════════════════

class Clock(object):
    """Замер идёт по ЧАСАМ СНИМКА, а не по сегодняшним. Лекарство от «снимок живёт сутки».

    ═══ ОТКУДА БЕРЁТСЯ СУТОЧНЫЙ СРОК — ДОСЛОВНО, И ЧТО ОН ЗАЩИЩАЕТ (вопрос забора) ═══

    Срока как НАСТРОЙКИ нет: ни `expires`, ни `ttl`, ни `max_age` в снимке не лежит, и ни одна
    строка `Pin`/`GatePin` возраст не проверяет. Сутки берутся из ПРОДУКТА — из одной строки
    (`suggest.py`, `build_price_sheet_note`):

        base = today or today_phuket()
        ...
        ds = (base + datetime.timedelta(days=1)).isoformat()      # ← якорь «ЗАВТРА»

    Эта дата уходит в `params` вызова `quote_price`, а `params` — и есть КЛЮЧ снимка
    (`_pkey`). Значит ключ несёт живую дату, и назавтра продукт спрашивает ДРУГОЙ вопрос.
    Замер 23.08 на снимке 22.08, кейс 2: **111 промахов из 112 вызовов моста** (3 окна × 37
    моделей) плюс промах головы 1 из 1 — ключ головы `sha(system)+sha(user)` тоже несёт даты,
    потому что в промпт вшит рассчитанный прайс-блок.

    ЧТО ЭТОТ ЗАБОР ЗАЩИЩАЕТ — и почему его нельзя убрать. Дата в ключе отличает ВОПРОСЫ друг от
    друга: «цена XSR с 24 по 25» и «цена XSR с 24 по 23 сентября» — это сутки против месяца, и
    ответы у них разные в разы. Выкинь дату из ключа (или огрубляй её до «какая-нибудь») — и
    снимок начнёт отвечать замороженной ценой на ВОПРОС, КОТОРОГО НЕ СЛЫШАЛ. Вот это и была бы
    подделка: не «снимок протух», а «снимок врёт». Поэтому забор стои́т, и `Clock` его НЕ ТРОГАЕТ:
    ключ остаётся дословным, с датой, во всю точность.

    ═══ РЕШЕНИЕ: не ослабить ключ, а вернуть продукту тот день, в который снимок снят ═══

    Ключ = f(вопрос, ДЕНЬ). Ломается не ключ, ломается ДЕНЬ. Значит чинить надо день:
    в `replay` продукт получает НЕ сегодняшнюю дату, а момент съёмки снимка — и САМ, своим
    же кодом, строит ровно те даты, что записаны. Совпадение ключей становится свойством
    ПОСТРОЕНИЯ, а не удачи.

    Замер того же кейса 2 на том же снимке 22.08, тем же кодом, в тот же час:

        без Clock:  мост 112 вызовов / 111 промахов, голова 1/1, черновик 0 симв. (кейс мёртв)
        с Clock:    мост 112 вызовов /   0 промахов, голова 2/0, черновик 1701 симв. (кейс ok)

    Снимок, объявленный вчера протухшим, ожил ЦЕЛИКОМ — и при этом не был ни переснят, ни
    открыт на запись, ни изменён на байт.

    ПОЧЕМУ ЭТО НЕ «УВЕЛИЧИТЬ СРОК ЖИЗНИ». Срок не тронут вовсе — его и не было. Снимок не стал
    жить дольше: он стал воспроизводимым НЕЗАВИСИМО от календаря, потому что календарь убран из
    ЗАМЕРА тем же приёмом, что сеть и голова. «Неделя вместо суток» лечила бы симптом до
    следующего понедельника; здесь суток нет ни одной ни в одну сторону.

    ПОЧЕМУ ЭТО НЕ ПОДДЕЛКА — четыре свойства `Pin` целы, и добавляются два своих:
     1. снимок снят живьём и вслепую, перезапись по-прежнему запрещена (`FileExistsError`):
        `Clock` НЕ пишет в снимок ни одной веткой и физически не может стать способом
        «переснять, пока не позеленеет» — он умеет только читать мету;
     2. ни один чек не снят и ни один порог не сдвинут: `Clock` не знает ни про `expect`, ни про
        `TRAINER_MIN_*`, ни про корпус;
     3. промах по-прежнему НЕ прощается: если день угадан неверно (или съёмка шла через полночь)
        — ключи не совпадут и промахи вырастут. Ошибка заморозки ГРОМКАЯ, а не молчаливая, и
        видна тем же счётчиком, что и все прочие промахи;
     4. правило «промах снимка = НЕИЗВЕСТНО» и правило «молчащая голова = НЕИЗВЕСТНО» не
        затронуты ни одной строкой;
     5. **день выбирает СНИМОК, а не оператор.** Штатная дорога одна — `Clock.from_snapshot(путь)`:
        момент берётся из меты того самого файла, который снят вслепую. «Подобрать удачный день»
        нельзя, не переснимая снимок, а переснимать запрещено;
     6. над замороженным днём работает ВЕСЬ код дат продукта: `today_phuket`, гейт прошедшего
        старта, якорь прайса, возрастная ветка сторожа свежести. Ни одна из них не выключена —
        им лишь сказано, КОТОРЫЙ ЧАС.

    ЧЕСТНАЯ ЦЕНА, которую нельзя замолчать (ровно того же вида, что цена пина головы): замер по
    замороженным часам больше НЕ ловит поломки, зависящие от НАСТОЯЩЕГО календаря — переход
    месяца и года, пересечение слепком цен порога возраста `PRICE_FRESH_MAX_AGE_DAYS`, «старт
    клиента уже прошёл». Этих классов на пине не видно ни в какой день. Лекарство от цены —
    СВЕЖИЙ снимок: снять его в новый файл слепым проходом можно всегда, и тогда замороженный
    день — новый.

    ═══ ЧТО ИМЕННО ЗАМОРАЖИВАЕТСЯ (две живые точки, обе найдены грепом, а не догадкой) ═══

        suggest.now_phuket(now=None)      ЕДИНАЯ точка правды дат клиентского контура; через неё
                                          идёт `today_phuket` и все 9 его вызовов в suggest
        price_freshness.judge(now=None)   вторые часы: при `now is None` берёт
                                          `datetime.date.today()` (price_freshness.py:174) —
                                          ЛОКАЛЬНУЮ дату машины, мимо Пхукета

    Каждой точке момент подаётся в ЕЁ семантике: `now_phuket` получает сам момент (aware-UTC,
    зону он переведёт сам), `judge` — ЛОКАЛЬНУЮ дату этого момента, то есть дословно то, что
    вернул бы `date.today()` в ту секунду. Так заморозка воспроизводит прошлое, а не подменяет
    одни часы другими.
    """

    def __init__(self, moment, why=""):
        if not isinstance(moment, datetime.datetime):
            raise ValueError("момент обязан быть datetime, получено %r" % type(moment).__name__)
        if moment.tzinfo is None:
            raise ValueError("момент обязан быть с зоной: наивный трактовался бы по локали ПК")
        self.moment = moment
        self.why = why
        # ЛОКАЛЬНАЯ дата момента — ровно то, что вернул бы `datetime.date.today()` в ту секунду.
        self.local_day = moment.astimezone().date()
        self.reads = {"now_phuket": 0, "judge": 0}
        self._orig = []

    # ── штатная дорога: день берётся ИЗ СНИМКА ───────────────────────────────────────────────
    @classmethod
    def from_meta(cls, meta, where=""):
        """Мета снимка → `Clock`. Новые снимки несут `момент` (aware ISO, пишет сам `save`);
        снятые до 23.08 — только `снят` (локальное «ГГГГ-ММ-ДД ЧЧ:ММ:СС» от `time.strftime`),
        и он читается как ЛОКАЛЬНОЕ время машины, потому что им и записан. Нет ни того ни
        другого — отказ, а не «возьмём сегодня»: молча замерить не тот день хуже, чем не
        замерить вовсе."""
        if not isinstance(meta, dict):
            # Слепого `meta or {}` здесь быть не должно: он превратил бы «меты нет вовсе» в
            # «меты нет часов» — разные болезни с одинаковым текстом отказа.
            raise ValueError("мета снимка не словарь (%s): часы замера взять неоткуда — %s"
                             % (type(meta).__name__, where or "?"))
        raw = meta.get(MOMENT_KEY)
        if raw:
            m = datetime.datetime.fromisoformat(str(raw))
            if m.tzinfo is None:
                m = m.replace(tzinfo=datetime.timezone.utc)
            return cls(m, why="%s: мета «%s»" % (where or "снимок", MOMENT_KEY))
        raw = meta.get("снят")
        if not raw:
            raise ValueError("в мете снимка нет ни «%s», ни «снят» — часы замера неизвестны: %s"
                             % (MOMENT_KEY, where or "?"))
        m = datetime.datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").astimezone()
        return cls(m, why="%s: мета «снят» (локальное время)" % (where or "снимок"))

    @classmethod
    def from_snapshot(cls, path):
        with io.open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_meta(data.get("meta"), where=os.path.basename(path))

    # ── установка/снятие ─────────────────────────────────────────────────────────────────────
    def install(self):
        import price_freshness
        import suggest
        moment, day, reads = self.moment, self.local_day, self.reads

        real_now = suggest.now_phuket
        self._orig.append((suggest, "now_phuket", real_now))

        def now_phuket(now=None):
            # Инъекция вызывающего сильнее заморозки: тест, назвавший свой момент, обязан его и
            # получить — иначе `Clock` втихую переписывал бы чужие голдены.
            reads["now_phuket"] += 1
            return real_now(moment if now is None else now)
        suggest.now_phuket = now_phuket

        real_judge = price_freshness.judge
        self._orig.append((price_freshness, "judge", real_judge))

        def judge(snapshot, live, now=None, max_age=None):
            reads["judge"] += 1
            return real_judge(snapshot, live, now=(day if now is None else now), max_age=max_age)
        price_freshness.judge = judge
        return self

    def uninstall(self):
        for mod, attr, real in reversed(self._orig):
            setattr(mod, attr, real)
        self._orig = []

    def stats(self):
        return {"момент": self.moment.isoformat(), "день_Пхукет": self.day().isoformat(),
                "день_локальный": self.local_day.isoformat(), "откуда": self.why,
                "чтений_часов": dict(self.reads)}

    def day(self):
        """Тот «сегодня», которым живёт клиентский контур на замороженных часах. Считается
        переводом зоны НАПРЯМУЮ, мимо `now_phuket`: иначе справка о часах сама накручивала бы
        счётчик чтений, который служит уликой в отчёте."""
        import suggest
        return self.moment.astimezone(suggest.PHUKET_TZ).date()

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False
