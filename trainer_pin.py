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

ЗАМОК ОТ ЗЛОУПОТРЕБЛЕНИЯ. `trainer_run.py` этот модуль НЕ ИМПОРТИРУЕТ и знать о нём не должен:
пин ставится только СНАРУЖИ, в измерительном раннере. Поэтому вердикт для ворот
(`trainer_run.write_verdict`) физически не может быть снят на пине — там, где пин, там нет
`write_verdict`, а там, где `write_verdict`, нет пина.

Секреты: `token` вырезается из params ДО хеширования и ДО записи, в снимок не попадает ни разу;
`_bridge_read_doc` оборачивается целиком, поэтому токен там даже не виден.
"""

import copy
import hashlib
import io
import json
import os

SNAPSHOT_VERSION = 2
_SECRET_KEYS = ("token", "key", "secret", "password", "auth")


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

    def __init__(self, path, mode):
        if mode not in ("record", "replay"):
            raise ValueError("режим только 'record' или 'replay'")
        self.path, self.mode = path, mode
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
        self.data["meta"] = dict(self.data.get("meta") or {}, **(meta or {}))
        tmp = self.path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        return self.path

    def stats(self):
        return {"режим": self.mode, "вызовов": dict(self.calls), "промахов": dict(self.misses),
                "ключей": {"мост": len(self.data["bridge"]), "доков": len(self.data["read_doc"]),
                           "головы": len(self.data["head"])},
                "playbook_sha": _sha(self.data.get("playbook") or ""),
                "по_кейсам": self.per_case}

    def __enter__(self):
        return self.install()

    def __exit__(self, *exc):
        self.uninstall()
        return False
