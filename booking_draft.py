# -*- coding: utf-8 -*-
"""
booking_draft.py — O3 кусок 1 «Кнопка Бронь».

По нажатию кнопки «📋 Бронь» на карточке модербота: берём ТРАНСКРИПТ клиентского диалога
(тот же, что уже лежит в записи черновика IPC для СТРАТЕГИЯ-перегенерации), гоним экстрактор
на голове SUGGEST_MODEL (--max-turns 1, СТРОГИЙ JSON), валидируем факты и собираем
карточку-ЧЕРНОВИК заявки МЕНЕДЖЕРУ в формате колонок листа «клиенты».

ГРАНИЦЫ (жёстко):
  • НИКАКОЙ записи в CRM/таблицы — менеджер копирует руками (авто-запись будет в куске 2).
  • Единственное обращение наружу — ЧТЕНИЕ цены через Bridge pricing.quote_for_model (read-only).
  • Карточка идёт МЕНЕДЖЕРУ (в группу модерации), НЕ клиенту.
  • Экстрактор — отдельный вызов, голова SUGGEST_MODEL (+кондуктор-фолбэк), НЕ THINKER_*.

Вся логика инъектируема (call_llm / allowlist / quote_fn / today) → покрыта мок-тестами без
живого Telegram/Anthropic/Bridge.
"""

import os
import re
import json
import difflib
import logging
import datetime
import tempfile
import subprocess

import suggest   # переиспуем SUGGEST_MODEL/резолв claude/park_allowlist/KNOWN_MODELS
import pricing   # переиспуем quote_for_model (read-only котировка из Календаря)

log = logging.getLogger("booking_draft")

# Поля СТРОГОГО JSON экстрактора (порядок = порядок в промпте).
EXTRACT_FIELDS = ("model", "name", "date_from", "date_to_datetime",
                  "price_day", "deposit", "helmets", "contact", "note")

MISSING = "—"
CLICK_KEY = "click125"   # HONDA CLICK 125 — жёсткий блок «не сдаём»


# ------------------------------- экстрактор (LLM) ----------------------------

_EXTRACT_SYSTEM = (
    "Ты — экстрактор заявки на аренду мототехники TurboBaby (Пхукет). На вход — ТРАНСКРИПТ "
    "диалога построчно ([менеджер]/[клиент]). Верни СТРОГО ОДИН JSON-объект и НИЧЕГО больше "
    "(без markdown, без пояснений, без ```). Поля ровно такие:\n"
    '{"model": null, "name": null, "date_from": null, "date_to_datetime": null, '
    '"price_day": null, "deposit": null, "helmets": null, "contact": null, "note": null}\n'
    "ПРАВИЛА:\n"
    "- Бери ТОЛЬКО факты, которые ЯВНО есть в диалоге. Чего нет — оставляй null. НИЧЕГО не "
    "выдумывай, не додумывай, не подставляй значения по умолчанию.\n"
    "- model — модель байка, как названа в диалоге (напр. «NMAX 155», «Honda Click 125»).\n"
    "- name — имя клиента, если он представился.\n"
    "- date_from — дата начала аренды в формате YYYY-MM-DD.\n"
    "- date_to_datetime — дата (и время, если названо) конца аренды: «YYYY-MM-DD HH:MM» или "
    "«YYYY-MM-DD», если время не названо.\n"
    "- price_day — озвученная в диалоге цена за день (число бат). НЕ считай и НЕ придумывай сам; "
    "нет явной цены — null.\n"
    "- deposit — залог как в диалоге: число бат ЛИБО слово «паспорт» (не оба).\n"
    "- helmets — число шлемов (целое).\n"
    "- contact — телеграм-ник (@...) или телефон клиента.\n"
    "- note — доставка/адрес/особые пожелания одной строкой.\n"
    "Верни только JSON-объект."
)


def _extract_llm(transcript: str) -> str:
    """Экстракция через claude CLI (подписка Max), голова SUGGEST_MODEL + кондуктор-фолбэк,
    --max-turns 1 (не агент, один проход), --output-format json. Тот же безопасный контур, что
    _cli_llm: ANTHROPIC_API_KEY вычищен, --allowed-tools '' + нейтральный cwd (без settings.json/
    pretool_guard репо). Таймаут/ошибка → '' (upstream трактует как пустую экстракцию)."""
    cbin = suggest._resolve_claude()
    if not cbin:
        raise RuntimeError("claude CLI не найден (PATH/CLAUDE_BIN/AppData) — экстракция брони невозможна")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # идём по подписке Max, не по платному ключу
    env.pop("OPENAI_API_KEY", None)
    cmd = [cbin, "-p", transcript, "--system-prompt", _EXTRACT_SYSTEM,
           "--model", suggest.SUGGEST_MODEL, "--max-turns", "1",
           "--output-format", "json", "--allowed-tools", ""]
    if suggest.SUGGEST_MODEL_FALLBACK:   # кондуктор: фолбэк добивает сам CLI в этом же вызове
        cmd += ["--fallback-model", suggest.SUGGEST_MODEL_FALLBACK]
    try:
        p = subprocess.run(
            cmd, cwd=tempfile.gettempdir(),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=suggest.CLI_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.warning("booking: claude CLI таймаут %sс — пустая экстракция", suggest.CLI_TIMEOUT)
        return ""
    if p.returncode != 0:
        log.warning("booking: claude CLI rc=%s — %s", p.returncode, (p.stderr or "").strip()[-300:])
        return ""
    text, real = suggest._parse_cli_json(p.stdout or "")
    log.info("booking: голова=%s (просили %s, фолбэк %s)",
             real or "?", suggest.SUGGEST_MODEL, suggest.SUGGEST_MODEL_FALLBACK or "—")
    return text


def _parse_json(raw: str) -> dict:
    """Разобрать JSON экстрактора (снимаем возможные ```json-обёртки, добираем {..})."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        s, e = raw.find("{"), raw.rfind("}")
        if 0 <= s < e:
            try:
                d = json.loads(raw[s:e + 1])
                return d if isinstance(d, dict) else {}
            except Exception:
                return {}
        return {}


def _norm_field(v):
    """Пустое/пробельное/строки-заглушки → None; иначе str(strip)."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "-", "—", "n/a", "na", "нет данных"):
        return None
    return s


def extract_booking(transcript: str, call_llm=None) -> dict:
    """Транскрипт → dict полей EXTRACT_FIELDS (отсутствующее = None). call_llm(transcript)->str
    инъектируется в тестах; по умолчанию — _extract_llm (claude CLI, SUGGEST_MODEL)."""
    call_llm = call_llm or _extract_llm
    data = _parse_json(call_llm(transcript) or "")
    return {k: _norm_field(data.get(k)) for k in EXTRACT_FIELDS}


# ------------------------------- валидации -----------------------------------

def _norm_alnum(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def classify_model(model_text, allowlist):
    """Модель заявки против АКТУАЛЬНОГО списка парка (тот же источник-гейт, что у suggest).
    allowlist — suggest.park_allowlist() (отображаемые имена парка) или None (источник недоступен).
    → dict {status, display, similar}:
      click  — HONDA CLICK 125 (жёсткий блок «не сдаём»);
      ok     — точное имя из парка (display — каноничное);
      not_in_park — распознали модель, но её нет в парке (similar — ближайшие имена парка);
      unknown — модель не распознали (similar — ближайшие из парка);
      no_source — allowlist недоступен (не можем сверить — мягкая пометка, без блока);
      empty  — модель в заявке не указана."""
    norm = _norm_alnum(model_text)
    if not norm:
        return {"status": "empty", "display": None, "similar": []}
    # HONDA CLICK 125 — жёсткий блок В ПЕРВУЮ очередь (даже если бы оказался в списке).
    if CLICK_KEY in norm:
        return {"status": "click", "display": "HONDA CLICK 125", "similar": []}
    matched = None
    for disp, key in suggest.KNOWN_MODELS:
        if key == CLICK_KEY:
            continue
        if key in norm:
            matched = disp
            break
    if allowlist is None:            # источник парка недоступен → не блокируем (fail-safe)
        return {"status": "no_source", "display": matched or model_text, "similar": []}
    if matched and matched in allowlist:
        return {"status": "ok", "display": matched, "similar": []}
    # не из парка: ближайшие похожие имена ИЗ ПАРКА (difflib по нормализованному вводу)
    pool = list(allowlist)
    key_in = matched or model_text
    similar = difflib.get_close_matches(str(key_in), pool, n=3, cutoff=0.3)
    if not similar:                  # добор по общему префиксу (напр. «PCX 160» → «PCX …»)
        pref = _norm_alnum(key_in)[:3]
        similar = [d for d in pool if pref and _norm_alnum(d).startswith(pref)][:3]
    status = "not_in_park" if matched else "unknown"
    return {"status": status, "display": matched or model_text, "similar": similar}


def thread_model(transcript, allowlist):
    """Нормализованное имя модели ИЗ ПАРКА, выведенное из СОХРАНЁННОГО треда (тот же путь, что
    у suggest: extract_booking_hints по транскрипту → токен модели → маппинг на отображаемое имя
    парка). → display-имя из парка ИЛИ None. Используется как подхват, когда экстракция дала
    неточное/не-парковое имя, а suggest этого треда уже нормализовал модель."""
    if not (transcript or "").strip():
        return None
    try:
        hints = suggest.extract_booking_hints(transcript)
    except Exception:
        return None
    tokens = hints.get("models") or ([hints.get("model")] if hints.get("model") else [])
    for tok in tokens:
        n = _norm_alnum(tok)
        if not n or n == _norm_alnum(CLICK_KEY):
            continue
        for disp, key in suggest.KNOWN_MODELS:
            if key == CLICK_KEY:
                continue
            if key == n or n in key or key in n:      # «nmax»⊂«nmax155», «nmax155»==key и т.п.
                if allowlist is None or disp in allowlist:
                    return disp
    return None


def classify_deposit(deposit_text):
    """Правило владельца: залог — ЛИБО число бат, ЛИБО «паспорт», НЕ оба.
    → (kind, shown): money|passport|conflict|none."""
    t = str(deposit_text or "").lower()
    if not t.strip():
        return ("none", None)
    has_passport = bool(re.search(r"паспорт|passport|документ|id\b", t))
    has_money = bool(re.search(r"\d{3,}", t.replace(" ", "")))
    if has_passport and has_money:
        return ("conflict", deposit_text)
    if has_passport:
        return ("passport", "паспорт")
    if has_money:
        return ("money", deposit_text)
    return ("none", deposit_text)


# --- разбор дат заявки (структурные поля экстрактора) ------------------------

def _parse_date(s):
    """YYYY-MM-DD | дд.мм.гггг | дд.мм.гг | дд.мм → datetime.date | None (год по умолчанию — текущий)."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = int(y) + (2000 if y and int(y) < 100 else 0) if y else datetime.date.today().year
        try:
            return datetime.date(year, mo, d)
        except ValueError:
            return None
    return None


def _parse_datetime(s):
    """date_to_datetime → (date|None, 'HH:MM'|None)."""
    s = (s or "").strip()
    d = _parse_date(s)
    tm = re.search(r"(\d{1,2}):(\d{2})", s)
    hhmm = None
    if tm:
        try:
            hhmm = f"{int(tm.group(1)):02d}:{int(tm.group(2)):02d}"
        except ValueError:
            hhmm = None
    return d, hhmm


def _fmt_date(d):
    return d.strftime("%d.%m.%Y") if d else None


# --- цена: НЕ доверяем экстракции, читаем Bridge ------------------------------

def _num(s):
    """Первое число из строки (баты) → int | None."""
    m = re.search(r"\d[\d\s]*", str(s or "").replace(" ", " "))
    if not m:
        return None
    try:
        return int(m.group(0).replace(" ", ""))
    except ValueError:
        return None


def resolve_price(model_display, date_from, date_to, voiced_price, quote_fn=None):
    """Цена ТОЛЬКО из Bridge (read-only quote_for_model). Экстракции price_day НЕ доверяем.
    quote зовём ЛИШЬ когда есть model + обе даты. → dict {status, bridge_day, voiced, text}:
      bridge_ok       — цена от Bridge (пометка «цена Bridge»);
      bridge_conflict — Bridge есть и расходится с озвученной → показываем ОБЕ;
      unavailable     — model+даты есть, но Bridge не дал цены;
      skipped         — нет model/дат → quote НЕ зван (озвученную показываем как «со слов»)."""
    voiced = _num(voiced_price)
    if not (model_display and date_from and date_to):
        return {"status": "skipped", "bridge_day": None, "voiced": voiced, "text": None}
    quote_fn = quote_fn or pricing.quote_for_model
    try:
        res = quote_fn(model_display, date_from.isoformat(), date_to.isoformat())
    except Exception as e:
        log.info("booking: quote упал (%s) — unavailable", type(e).__name__)
        res = None
    if isinstance(res, dict) and res.get("status") == "ok" and isinstance(res.get("quote"), dict):
        bridge_day = res["quote"].get("day_price")
        if bridge_day is not None and voiced is not None and int(bridge_day) != int(voiced):
            return {"status": "bridge_conflict", "bridge_day": bridge_day, "voiced": voiced, "text": None}
        return {"status": "bridge_ok", "bridge_day": bridge_day, "voiced": voiced, "text": None}
    return {"status": "unavailable", "bridge_day": None, "voiced": voiced, "text": None}


# --- доставка вне Пхукета -----------------------------------------------------

_PHUKET = ("пхукет", "phuket", "патонг", "patong", "банг тао", "бангтао", "bangtao", "bang tao",
           "сурин", "surin", "камала", "kamala", "карон", "karon", "паклок", "paklok",
           "ката", "kata", "кату", "kathu", "katu", "раваи", "rawai", "найхарн", "nai harn",
           "найхар", "чалонг", "chalong", "аэропорт", "airport", "тхаланг", "thalang")
_NON_PHUKET = ("краби", "krabi", "бангкок", "bangkok", "самуи", "samui", "пхи-пхи", "пхипхи",
               "phi phi", "phiphi", "паттайя", "pattaya", "као лак", "khao lak", "пханган",
               "phangan", "чиангмай", "chiang mai")
_DELIVERY_KW = ("доставк", "привез", "привоз", "delivery", "deliver", "адрес", "address")


def delivery_outside_phuket(note):
    """Доставка вне Пхукета? True, если в примечании есть признак доставки И упомянут не-Пхукет."""
    t = str(note or "").lower().replace("ё", "е")
    if not any(k in t for k in _DELIVERY_KW):
        return False
    return any(k in t for k in _NON_PHUKET) and not any(k in t for k in _PHUKET)


# --- O3-2.1 (хвост §7): гео-ссылка Google Maps из КЛИЕНТСКИХ строк диалога ------
# INTAKE требует гео точки доставки; если клиент УЖЕ кинул ссылку Maps в переписке — мост
# дособирает её в поле «Доставка:» поста сам, чтобы страж не переспрашивал очевидное.
_MAPS_URL_RE = re.compile(
    r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|maps\.google\.[^\s/]+|"
    r"(?:www\.)?google\.[^\s/]+/maps)\S*", re.I)


def client_maps_link(transcript):
    """Последняя (самая свежая) ссылка Google Maps из строк «[клиент]: …» диалога → str | None.
    Строки менеджера игнорируем ЖЁСТКО: менеджер сам шлёт в приветствии ссылки НАШИХ точек
    (БангТао/Камала) — они НЕ адрес доставки клиента (иначе гео проката стало бы адресом)."""
    link = None
    for ln in str(transcript or "").splitlines():
        if not ln.lstrip().startswith("[клиент]:"):
            continue
        for m in _MAPS_URL_RE.finditer(ln):
            link = m.group(0).rstrip(".,;)»›\"'")   # хвостовая пунктуация чата — не часть URL
    return link


# O3-2.1 ЭТАП 2 (б): Telegram ГЕО-ПИН (вложение-локация) как точка доставки — БЕЗ текстовой ссылки.
# transcript_from кодирует гео-вложение маркером «[локация]» (медиа без подписи) / reply-целью;
# также ловим сырые координаты «geo:lat,lon» и «@lat,lon». Тот же словарь детекта, что _COLL_GEO
# в suggest (attachment-подмножество). Строки менеджера игнорируем — как в client_maps_link.
_GEO_PIN_RE = re.compile(r"\[локаци\w*\]|geo:\s*-?\d|@-?\d{1,2}\.\d{3,},-?\d{1,3}\.\d{3,}", re.I)


def client_geo_pin(transcript):
    """True, если в КЛИЕНТСКИХ строках есть гео-ПИН/координаты (Telegram гео-вложение → маркер
    «[локация]») БЕЗ текстовой Maps-ссылки. Даёт точку доставки, когда клиент кинул пин, а не URL."""
    for ln in str(transcript or "").splitlines():
        if not ln.lstrip().startswith("[клиент]:"):
            continue
        if _GEO_PIN_RE.search(ln):
            return True
    return False


# ------------------------------- сборка карточки -----------------------------

def build_card(ex, allowlist=None, quote_fn=None, today=None, meta=None):
    """Собрать текст карточки-ЧЕРНОВИКА заявки менеджеру из полей экстракции ex + валидаций.
    allowlist/quote_fn/today инъектируемы. meta — метаданные диалога из записи IPC
    {client_ref, client_name, transcript} для U/D/подхвата модели. НЕ пишет никуда."""
    warn = []
    meta = meta or {}
    client_ref = _norm_field(meta.get("client_ref"))
    client_name = _norm_field(meta.get("client_name"))
    transcript = meta.get("transcript") or ""

    mv = classify_model(ex.get("model"), allowlist)

    # HONDA CLICK 125 — жёсткий блок: НЕ показываем как валидную бронь (и quote НЕ зовём).
    if mv["status"] == "click":
        raw = ex.get("model") or "HONDA CLICK 125"
        return (
            "📋 ЗАЯВКА — ОТКЛОНЕНА\n"
            f"⛔ {raw}: HONDA CLICK 125 — НЕ сдаём (жёсткий блок владельца).\n"
            "Предложи клиенту PCX 150 / ADV 150 / NMAX 155.\n"
            "Заявку во вкладку «клиенты» НЕ вносить."
        )

    # C — модель. Валидная (ok/no_source) → как есть. Иначе (не-парк/неизвестна/пусто) —
    # подхват нормализованного имени из СОХРАНЁННОГО черновика треда, если оно есть.
    model_display = None       # каноничное имя для Bridge-котировки (None → quote не зовём)
    if mv["status"] == "ok":
        c = model_display = mv["display"]
    elif mv["status"] == "no_source":
        c = model_display = mv["display"]
        warn.append("список парка недоступен — сверь модель вручную (C)")
    else:  # empty | not_in_park | unknown → пробуем модель из черновика треда
        tm = thread_model(transcript, allowlist)
        if tm:
            model_display = tm
            c = f"{tm} (из черновика)"
        elif mv["status"] == "empty":
            c = MISSING
            warn.append("нет модели (C)")
        else:  # not_in_park | unknown, черновик не помог
            c = mv["display"]
            sim = (" — ближайшие: " + ", ".join(mv["similar"])) if mv["similar"] else ""
            warn.append(f"модель не из парка (C): {mv['display']}{sim}")

    # D — имя: из текста → из профиля (first_name) с пометкой → «—»+⚠️
    if ex.get("name"):
        d = ex.get("name")
    elif client_name:
        d = f"{client_name} (из профиля — уточни)"
    else:
        d = MISSING
        warn.append("нет имени (D)")

    # E/F — даты
    df = _parse_date(ex.get("date_from"))
    dt_end, hhmm = _parse_datetime(ex.get("date_to_datetime"))
    e = _fmt_date(df) or MISSING
    if not df:
        warn.append("нет даты начала (E)")
    if dt_end:
        f = _fmt_date(dt_end) + (f" , {hhmm}" if hhmm else "")
    else:
        f = MISSING
        warn.append("нет даты/времени конца (F)")

    # H — цена: НЕ из экстракции, а из Bridge (когда есть ВАЛИДНАЯ модель — вкл. подхваченную
    # из черновика — и обе даты). model_display выставлен выше только для валидных случаев.
    pr = resolve_price(model_display, df, dt_end, ex.get("price_day"), quote_fn=quote_fn)
    if pr["status"] == "bridge_ok":
        h = f"{pr['bridge_day']} ฿/день (цена Bridge)"
    elif pr["status"] == "bridge_conflict":
        h = f"Bridge {pr['bridge_day']} ฿/день / в диалоге {pr['voiced']} ฿/день"
        warn.append(f"цена расходится: Bridge {pr['bridge_day']} vs в диалоге {pr['voiced']} ฿/день (H)")
    elif pr["status"] == "unavailable":
        if pr["voiced"] is not None:
            h = f"{pr['voiced']} ฿/день (со слов; Bridge цену не дал)"
            warn.append("Bridge не подтвердил цену — проверь (H)")
        else:
            h = MISSING
            warn.append("цены нет и Bridge не дал (H)")
    else:  # skipped (нет model/дат)
        if pr["voiced"] is not None:
            h = f"{pr['voiced']} ฿/день (со слов; без дат Bridge не сверял)"
            warn.append("цена со слов, не сверена с Bridge (H)")
        else:
            h = MISSING
            warn.append("нет цены (H)")

    # S — залог: число ฿ ЛИБО «паспорт», НЕ оба
    dep_kind, dep_shown = classify_deposit(ex.get("deposit"))
    if dep_kind == "conflict":
        s = f"{dep_shown}"
        warn.append("залог: и число, и паспорт — по правилу ИЛИ-ИЛИ, уточни (S)")
    elif dep_kind == "none":
        s = MISSING
        warn.append("нет залога (S)")
    else:
        s = dep_shown

    # T — шлемы
    t_h = ex.get("helmets") or MISSING
    if not ex.get("helmets"):
        warn.append("нет числа шлемов (T)")

    # U — контакт: из текста → из метаданных диалога (@username / idNNN системе всегда известен)
    if ex.get("contact"):
        u = ex.get("contact")
    elif client_ref:
        u = client_ref
    else:
        u = MISSING
        warn.append("нет контакта (U)")

    # V — примечание + доставка
    v = ex.get("note") or MISSING
    if not ex.get("note"):
        warn.append("нет примечания (V)")
    if delivery_outside_phuket(ex.get("note")):
        warn.append("доставка ВНЕ Пхукета — уточни стоимость/возможность (V)")

    cols = (f"A=Бронь · B=OFF · C={c} · D={d} · E={e} · F={f} · "
            f"H={h} · S={s} · T={t_h} · U={u} · V={v}")

    lines = ["📋 ЗАЯВКА (черновик)", cols]
    for w in warn:
        lines.append(f"⚠️ {w}")
    lines.append("Проверь и внеси во вкладку клиенты. Авто-запись будет в куске 2.")
    return "\n".join(lines)


# ------------------------- O3-2c: текст поста «🆕 БРОНЬ» ----------------------
# Формат, который принимает живой INTAKE (Splinter): карточка collect_booking_prompt.txt.
# Поля с ⚠️/«—» (невалидные/отсутствующие) НЕ включаем — пусть INTAKE честно спросит «не хватает».
# Цену НЕ шлём (INTAKE берёт её сам). Даты — человеческие (как парсит Splinter) + строка ISO.

def build_intake(ex, allowlist=None, meta=None):
    """Собрать ТЕКСТ поста «🆕 БРОНЬ» из ВАЛИДНЫХ полей заявки (для «Входящие брони»).
    → строка поста ИЛИ None (модель под жёстким блоком Click / валидных полей нет — постить нечего).
    НЕ пишет никуда, цену не зовёт."""
    meta = meta or {}
    client_ref = _norm_field(meta.get("client_ref"))
    transcript = meta.get("transcript") or ""

    mv = classify_model(ex.get("model"), allowlist)
    if mv["status"] == "click":
        return None                                  # HONDA CLICK 125 — в CRM не отправляем

    if mv["status"] in ("ok", "no_source"):
        model_display = mv["display"]
    else:                                            # не-парк/неизвестна → подхват из черновика треда
        model_display = thread_model(transcript, allowlist)

    df = _parse_date(ex.get("date_from"))
    dt_end, _hhmm = _parse_datetime(ex.get("date_to_datetime"))
    dep_kind, _ = classify_deposit(ex.get("deposit"))
    note = _norm_field(ex.get("note"))
    delivery_bad = delivery_outside_phuket(note)
    handle_at = client_ref if (client_ref and client_ref.startswith("@")) else None

    lines = ["🆕 БРОНЬ (черновик из диалога)"]

    name = _norm_field(ex.get("name"))               # только имя ИЗ ТЕКСТА (профильное «уточни» не шлём)
    if name and handle_at:
        lines.append(f"Клиент: {name} ({handle_at})")
    elif name:
        lines.append(f"Клиент: {name}")
    elif handle_at:
        lines.append(f"Клиент: {handle_at}")

    if model_display:
        lines.append(f"Модель: {model_display}")

    if df and dt_end:                                # только полный диапазон
        term = (dt_end - df).days
        human = f"{_fmt_date(df)} – {_fmt_date(dt_end)}"
        if term > 0:
            human += f" ({term} дн.)"
        lines.append(f"Даты: {human}")
        lines.append(f"Даты ISO: {df.isoformat()} – {dt_end.isoformat()}")

    contact = _norm_field(ex.get("contact"))
    if contact:
        lines.append(f"Контакт: {contact}")
    elif client_ref and not handle_at:               # idNNN как контакт (username уже в «Клиент»)
        lines.append(f"Контакт: {client_ref}")

    # O3-2.1: поле «Доставка» дособираем гео-ссылкой Maps ИЗ КЛИЕНТСКИХ строк диалога (ссылки
    # менеджера отфильтрованы в client_maps_link). Есть ссылка — она попадает в пост даже без
    # note (страж перестаёт просить гео, оно уже есть); нет ссылки — прежнее поведение.
    # ЭТАП 2 (б): нет URL, но клиент кинул гео-ПИН/координаты ([локация]) → отражаем точку в поле.
    maps_link = client_maps_link(transcript)
    delivery_parts = []
    if note and not delivery_bad:                    # доставка вне Пхукета (⚠️) — не включаем
        delivery_parts.append(note)
    if maps_link and not any(maps_link in p for p in delivery_parts):
        delivery_parts.append(maps_link)
    elif not maps_link and client_geo_pin(transcript):
        delivery_parts.append("точка на карте (пин в диалоге)")
    if delivery_parts:
        lines.append("Доставка: " + " · ".join(delivery_parts))

    helmets = _norm_field(ex.get("helmets"))
    if helmets:
        lines.append(f"Шлемы: {helmets}")

    if dep_kind == "money":                          # ⚠️-конфликт/пусто — не включаем
        lines.append("Депозит: деньги")
    elif dep_kind == "passport":
        lines.append("Депозит: паспорт")

    if len(lines) <= 1:                              # только заголовок, валидных полей нет
        return None

    # ЭТАП 2 (а): ЧЕСТНЫЙ хвост «не хватает» для карточки «Входящие брони» — чтобы менеджер доносил
    # ТОЛЬКО недостающее (точку доставки / фото паспорта), а не всё подряд. Источник — collected_facts
    # (то же детерминированное окно, что и фильтр вопросов): geo = ссылка/пин/координаты/названо жильё;
    # passport = ФАКТ фото/файла в окне ([фото]/«вероятно паспорт»). Депозит-правило и окно НЕ трогаем —
    # это лишь подсказка менеджеру. Транскрипта нет (старый вызов) → хвоста нет (нечего оценивать).
    if transcript:
        try:
            facts = suggest.collected_facts(transcript)
        except Exception:
            facts = {}
        gaps = []
        if not facts.get("geo"):
            gaps.append("точка (гео/Maps)")
        if not facts.get("passport"):
            # фото: build_intake видит только транскрипт, а фактическую пересылку решает отдельный
            # скан живого диалога (poll_and_post_intake) → честно помечаем неопределённость, не «нет».
            gaps.append("фото паспорта (проверьте — в окне не найдено, возможно приложено пересылкой)")
        if gaps:
            lines.append("❗ Не хватает: " + "; ".join(gaps))
    return "\n".join(lines)


def make_booking_and_intake(transcript, call_llm=None, allowlist=None, quote_fn=None,
                            today=None, meta=None):
    """Одна экстракция → (карточка менеджеру, текст поста «🆕 БРОНЬ»|None). Карточка — для показа,
    intake-текст — для кнопки «✅ В CRM» (кладётся кандидатом в очередь). НЕ пишет никуда."""
    if allowlist is None and call_llm is None:   # бой: тянем актуальный список парка
        try:
            allowlist = suggest.park_allowlist()
        except Exception:
            allowlist = None
    meta = dict(meta or {})
    meta.setdefault("transcript", transcript)
    ex = extract_booking(transcript, call_llm=call_llm)
    card = build_card(ex, allowlist=allowlist, quote_fn=quote_fn, today=today, meta=meta)
    intake = build_intake(ex, allowlist=allowlist, meta=meta)
    return card, intake


def make_booking_card(transcript, call_llm=None, allowlist=None, quote_fn=None, today=None, meta=None):
    """ВЕРХНИЙ вход для кнопки «📋 Бронь»: транскрипт → экстракция → валидации → карточка.
    meta — метаданные диалога из записи IPC {client_ref, client_name, transcript} (U/D/подхват
    модели). В бою deps берутся сами (park_allowlist / quote_for_model / _extract_llm); в тестах —
    инъекция. НЕ пишет в CRM/IPC — только читает цену Bridge и возвращает строку карточки."""
    return make_booking_and_intake(transcript, call_llm=call_llm, allowlist=allowlist,
                                   quote_fn=quote_fn, today=today, meta=meta)[0]
