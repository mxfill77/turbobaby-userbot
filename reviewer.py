# -*- coding: utf-8 -*-
"""reviewer.py — периодический ревизор качества черновиков (класс 23.07.2026).

Сканирует записи moderation_ipc (статусы sent/ready/test_held) и проверяет каждое
окно (client_id) по 3 чекам:
  depcheck  — депозит: «И» требует ОБА одновременно → находка (ИЛИ-предложение = норма)
  yearcheck — год как год поколения (рядом с моделью/«г.в.»/«поколение»),
               НЕ в денежном контексте (฿/бат/в день/депозит)
  jcheck    — J-цена использована дословно: цифры побуквенно, модель через _bike_key

STAFF фильтр: окна Earth (8562625260) и Пыма (659135499) пропускаются полностью —
внутренний контур клиентскими чеками не проверяется.

Возврат run_reviewer(): (n_windows, findings)
  n_windows — сколько уникальных client_id проверено
  findings  — list[dict] с ключами: client_id, check, detail, draft_snippet
"""

import re

# ── STAFF фильтр ──────────────────────────────────────────────────────────────
STAFF_IDS = frozenset({8562625260, 659135499})   # Earth, Пым — внутренний контур

# ── Паттерны depcheck ─────────────────────────────────────────────────────────
# Любой денежный маркер — число + валюта (฿, бат, B, ฿)
_MONEY_RE = re.compile(r"\d[\d\s,\.]*(?:฿|бат\b|B(?=\b))", re.IGNORECASE | re.UNICODE)
# Паспорт в тексте
_PASSPORT_RE = re.compile(r"(?i)паспорт|passport")
# «И»-соединитель (AND): «и», «плюс», «+», «а также», «оба», «both»
# НЕ «или», НЕ «or», НЕ «либо»
_AND_RE = re.compile(
    r"\b(?:и\b|плюс\b|а\s+также\b|оба\b|both\b)",
    re.IGNORECASE | re.UNICODE,
)
# «ИЛИ»-соединитель — явная альтернатива; если есть в ТОЙ ЖЕ клаузе, это НЕ находка.
# Список расширен формулировками ЖИВОГО прода (замер 31.07, разбор находок ревизора §«(а)»):
# «депозит 3000 ฿ — ПАСПОРТОМ МОЖНО», «МОЖНО оставить паспорт ВМЕСТО денег» и разделитель «/»
# из прайс-блока, который печатает КОД («Депозит: 3000 ฿ / паспорт»). Слэш засчитываем ТОЛЬКО
# вплотную к слову «паспорт»: иначе «337 ฿/день» из соседней фразы снимал бы настоящую находку.
_OR_RE = re.compile(
    r"\b(?:или|либо|or\b)|вместо\b|instead\b|можн[оа]\b|"
    r"/\s*(?:загран)?паспорт|(?:загран)?паспорт\w*\s*/",
    re.IGNORECASE | re.UNICODE)

# ── Паттерны yearcheck ────────────────────────────────────────────────────────
# Четырёхзначное число, которое выглядит как год (2010–2030)
_YEAR_DIGITS_RE = re.compile(r"\b(20[12][0-9])\b")
# Признак ДЕНЕЖНОГО контекста: ฿/бат/в день/в месяц/итого/депозит в радиусе 10 символов
_MONEY_CTX_RE = re.compile(
    r"(?:฿|бат\b|/день\b|/мес\b|/месяц\b|итого\b|депозит\b|deposit\b|per\s+day\b)",
    re.IGNORECASE | re.UNICODE,
)
# Признак ПОКОЛЕНИЯ: слова рядом с годом (в пределах 30 символов)
_YEAR_GEN_MARKERS = re.compile(
    r"(?:поколени[ея]\b|г\.в\.?|г\.\s*выпуска\b|new\s+gen\b|год\s+вып)",
    re.IGNORECASE | re.UNICODE,
)
# Любая известная модель байка (широкий паттерн — латинские буквы + цифры)
_MODEL_LIKE_RE = re.compile(
    r"\b(?:XMAX|NMAX|AEROX|FORZA|ADV|PCX|CLICK|MT|CBR|CB|REBEL|XSR|R7|NINJA"
    r"|VULCAN|VERSYS|Z\d00|SH\d+|LEAD|WAVE|DREAM)\b",
    re.IGNORECASE,
)

# ── Паттерн J-text из pricing_note ───────────────────────────────────────────
# "ЦЕНА из Календаря бронирования (использовать ДОСЛОВНО, не пересчитывать и не округлять): <phrase>."
_J_TEXT_RE = re.compile(
    r"ЦЕНА из Календаря бронирования[^:]*:\s*(.+?)\.",
    re.DOTALL,
)
# Цифровые группы (digits + разделители .,\s) — для побуквенной сверки цифр
_DIGIT_GROUPS_RE = re.compile(r"\d[\d\s,\.]*\d|\d")


def _bike_key(name: str) -> str:
    """Имя байка → alnum-ключ: снимаем CC/СС и не-alnum. Зеркало suggest._bike_key."""
    s = re.sub(r"(?i)[сc][сc]", "", str(name or ""))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _normalize_digits(s: str) -> str:
    """Убрать пробелы и незначимые разделители из числовой строки для побуквенной сверки."""
    return re.sub(r"[\s,\.]", "", s)


# ── Check 1: depcheck ─────────────────────────────────────────────────────────

def _clause_at(text: str, pos: int) -> str:
    """Клауза (предложение) вокруг позиции: до ближайших «.!?» или перевода строки."""
    s = text or ""
    left = max(s.rfind(ch, 0, pos) for ch in ".!?\n")
    rights = [r for r in (s.find(ch, pos) for ch in ".!?\n") if r != -1]
    return s[left + 1: (min(rights) if rights else len(s))]


def _depcheck(text: str) -> str | None:
    """Депозит: «И ОБА» требование → находка; «ИЛИ» альтернатива → None.

    Скоуп — ТА ЖЕ КЛАУЗА (предложение), где стоит слово «паспорт»: в ней обязаны быть и деньги,
    и «И»-соединитель, и не быть предложения выбора. Прежнее окно ±150 символов ловило союз «и»
    в ПОСТОРОННЕЙ фразе и красило живой ОТПРАВЛЕННЫЙ клиенту текст id=213 («…депозит 3000 ฿ —
    паспортом можно, всё верно… на чём вы ездили раньше И как долго») и id=214 («…можно оставить
    паспорт вместо денег, как вы И хотели») — оба дают клиенту ВЫБОР, то есть ровно то, чего
    правило и требует. Разбор с дословными текстами: docs/artifacts/2026-07-31-revizor-false-
    findings-audit.md. Ложная находка здесь дороже пропуска: она уходит владельцу карточкой и
    ревизору поводом править клиентского бота.
    """
    if not _PASSPORT_RE.search(text):
        return None
    for pm in _PASSPORT_RE.finditer(text):
        clause = _clause_at(text, pm.start())
        if not _MONEY_RE.search(clause):
            continue                  # деньги в другой фразе — про этот депозит ничего не сказано
        if _OR_RE.search(clause):
            continue                  # выбор предложен («или/либо», «вместо денег», «/паспорт»)
        if _AND_RE.search(clause):
            snippet = clause.strip()[:120]
            return f"депозит требует ОБА (деньги И паспорт одновременно): «{snippet}»"
    return None


# ── Check 2: yearcheck ────────────────────────────────────────────────────────

def _yearcheck(text: str) -> str | None:
    """Год: ловим 20xx ТОЛЬКО рядом с моделью/«г.в.»/«поколение».
    Денежный контекст (฿/бат/в день) в ±10 символах → НЕ год, не флагуем.
    """
    for m in _YEAR_DIGITS_RE.finditer(text):
        year = m.group(1)
        ys, ye = m.start(), m.end()
        # Контекст ±40 символов для проверки
        ctx = text[max(0, ys - 40): ye + 40]
        # Денежный контекст — пропуск
        if _MONEY_CTX_RE.search(text[max(0, ys - 10): ye + 10]):
            continue
        # Нужен хотя бы один «поколение»-маркер ИЛИ название модели в контексте
        if _YEAR_GEN_MARKERS.search(ctx) or _MODEL_LIKE_RE.search(ctx):
            snippet = ctx.strip()[:100]
            return f"год поколения в тексте — проверить: «{snippet}»"
    return None


# ── Check 3: jcheck ───────────────────────────────────────────────────────────

def _jcheck(draft: str, pricing_note: str) -> str | None:
    """J-цена: цифры из pricing_note должны присутствовать в draft дословно (побуквенно).
    Название модели сверяется через _bike_key (XMAX 300 New Gen == YAMAHA XMAX 300 NEW).
    Возвращает None если pricing_note не содержит J-цену или все цифры совпадают.
    """
    if not pricing_note:
        return None
    m = _J_TEXT_RE.search(pricing_note)
    if not m:
        return None   # нет J-цены в pricing_note — чек не применяется
    j_phrase = m.group(1).strip()
    if not j_phrase:
        return None

    # Сверка цифр побуквенно
    j_groups = _DIGIT_GROUPS_RE.findall(j_phrase)
    for jg in j_groups:
        norm = _normalize_digits(jg)
        if norm and norm not in _normalize_digits(draft).replace(" ", ""):
            # Попытка найти нормализованную группу в нормализованном тексте черновика
            # (убираем пробелы/запятые из ОБОИХ для честного сравнения)
            draft_nums = re.sub(r"[\s,\.]", "", draft)
            if norm not in draft_nums:
                snippet = j_phrase[:80]
                return (f"J-цена не использована дословно (пропущена числовая группа «{norm}»"
                        f" из «{snippet}»)")

    # Сверка модели через _bike_key (подстроковая: "xmax300" ⊂ "yamahaxmax300new")
    # Бренды (YAMAHA/HONDA/SUZUKI/KAWASAKI) пропускаем — важна модель-код (XMAX, NMAX, ADV…)
    _BRANDS = frozenset({"yamaha", "honda", "suzuki", "kawasaki", "bmw", "ducati", "royal"})
    model_word_re = re.compile(r"\b([A-Z][A-Z0-9\-]{1,}(?:\s+\d{3,4})?)\b")
    draft_key_blob = _bike_key(draft)
    for mw in model_word_re.finditer(j_phrase):
        raw = mw.group(1)
        if _bike_key(raw) in _BRANDS:
            continue   # пропустить бренд
        j_key = _bike_key(raw)
        if len(j_key) < 3:
            continue
        # Подстроковый матч: j_key ⊂ draft_key_blob или draft содержит слово с общим ядром
        if j_key not in draft_key_blob:
            # Попробовать ядро модели без «new/gen» суффиксов
            core = re.sub(r"(?:new|gen|cc|\d{4})", "", j_key)
            if len(core) >= 3 and core not in draft_key_blob:
                return (f"модель из J-цены «{raw}» не найдена в черновике"
                        f" (ключ «{j_key}» отсутствует)")
    return None


# ── review_record: проверить одну запись IPC ─────────────────────────────────

def review_record(record: dict) -> list[dict]:
    """Проверить одну запись drafts из moderation_ipc.
    Возвращает список находок (пустой = всё ок).
    record: dict с ключами client_id, final_text (или draft), pricing_note.
    """
    client_id = record.get("client_id")
    if client_id in STAFF_IDS:
        return []   # STAFF фильтр

    text = str(record.get("final_text") or record.get("draft") or "")
    pricing = str(record.get("pricing_note") or "")
    if not text.strip():
        return []

    findings = []

    dep = _depcheck(text)
    if dep:
        findings.append({
            "client_id": client_id,
            "check": "depcheck",
            "detail": dep,
            "draft_snippet": text[:200],
        })

    yr = _yearcheck(text)
    if yr:
        findings.append({
            "client_id": client_id,
            "check": "yearcheck",
            "detail": yr,
            "draft_snippet": text[:200],
        })

    jc = _jcheck(text, pricing)
    if jc:
        findings.append({
            "client_id": client_id,
            "check": "jcheck",
            "detail": jc,
            "draft_snippet": text[:200],
        })

    return findings


# ── run_reviewer: основная точка входа ────────────────────────────────────────

def run_reviewer(path=None) -> tuple[int, list[dict]]:
    """Сканировать moderation_ipc (sent/ready/test_held) → (n_windows, findings).
    path — опциональный путь к БД (для тестов).
    FAIL-SAFE: любое исключение → (0, []) без краша демона.
    """
    try:
        import moderation_ipc
        import sqlite3

        db = path or moderation_ipc.DB_PATH
        conn = sqlite3.connect(db, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            # Берём только финальные записи (final_text отправлен или подтверждён)
            rows = list(conn.execute(
                "SELECT client_id, draft, final_text, pricing_note FROM drafts "
                "WHERE status IN ('sent', 'ready', 'test_held') ORDER BY id DESC LIMIT 200"
            ))
        finally:
            conn.close()
    except Exception:
        return 0, []

    seen_clients: set = set()
    all_findings: list[dict] = []

    for row in rows:
        client_id = row["client_id"]
        if client_id in STAFF_IDS:
            continue
        seen_clients.add(client_id)
        rec = {
            "client_id": client_id,
            "final_text": row["final_text"],
            "draft": row["draft"],
            "pricing_note": row["pricing_note"],
        }
        all_findings.extend(review_record(rec))

    return len(seen_clients), all_findings
