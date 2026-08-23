# -*- coding: utf-8 -*-
"""
trainer_run.py — БЕЗГОЛОВЫЙ ПРОГОН тренажёра по корпусу + МАШИННЫЙ ВЕРДИКТ для ворот клиентского
контура. Вариант А артефакта `docs/artifacts/2026-07-30-trainer-verdict-recon.md`.

ЗАЧЕМ. Ворота выкатки (client_contour.release_reason) держат КАЖДЫЙ клиентский коммит до «да»
владельца, потому что ВТОРОЕ основание — «зелёный прогон через тренажёр» — было заглушкой:
тренажёр живёт ВНУТРИ боевого userbot (Telethon-событие + singleton-лок + одно глобальное
состояние в moderation_ipc.meta) и вердикта не отдаёт вовсе. Этот раннер отдаёт.

ЧЕМ ОН НЕ ЯВЛЯЕТСЯ (границы, они же безопасность):
  • НЕ поднимает userbot и НЕ трогает его singleton-лок (`userbot.lock`) — Telethon здесь не
    импортируется вообще; живой PID продолжает работать;
  • НЕ трогает состояние тренажёра (9 ключей `trainer_*` в боевой moderation_ipc.meta): своё
    состояние держит в памяти, боевая БД уводится в temp ПЕРЕД любым обращением;
  • НЕ шлёт НИЧЕГО наружу: ни в Telegram, ни в очередь модерации, ни в KB_trainer_log. Единственный
    выход — файл вердикта на диске и текст в stdout;
  • НЕ трогает `SUGGEST_TEST_MODE` — второй слой защиты остаётся ровно там, где стоял.
Поэтому раннер не попадает в клиентский контур (его никто из ботов не импортит) и его можно
переписывать, не упираясь в ворота, которые он призван открывать.

ЧТО ГОНЯЕТ. Тот же боевой пайплайн, что тренажёр в группе, — `generate()` ниже КОПИРУЕТ вызовы
`userbot_listen._trainer_generate` (detect_lang → extract_booking_hints → build_pricing_note →
park_allowlist → load_playbook → load_faq → generate_draft → strip_thai) вызов-в-вызов. Дубль —
честный минус варианта А (артефакт, §4); чтобы он не разъехался молча, `test_trainer_run`
СВЕРЯЕТ порядок вызовов обеих функций по AST и краснеет на любом расхождении.

Bridge — ЖИВОЙ (read-only GET прайса/зон/FAQ): ожидания чеков считаются из ТОГО ЖЕ pricing_note,
который питает черновик, поэтому смена цен в листе вердикт не красит (правило-класс «проверка
повторяет живой формат»).

ЗЕЛЁНЫЙ ВЕРДИКТ (критерий из артефакта, §5) — все условия сразу:
  • 12 кейсов корпуса, каждый прошёл ВСЕ применимые чеки (12 из 12, не «11 из 12»);
  • ДВА прогона из двух (генератор недетерминирован — один прогон ничего не доказывает);
  • дерево ЧИСТОЕ и `git rev-parse HEAD` не сдвинулся за время прогона (демон коммитит сам —
    вердикт, снятый на плывущем HEAD, удостоверял бы другой код);
  • НИ ОДНОГО исхода «неизвестно» (22.08.2026): голова, не давшая текста, зелёного не даёт ни
    одному кейсу — см. узел «МОЛЧАЩАЯ ГОЛОВА = НЕИЗВЕСТНО» ниже. Третье значение `result`
    — `'unknown'`: для ворот это «не зелёный», но красным прибор называет только то, что уличил.
Любой провал → зелёная запись НЕ пишется, а прежняя зелёная запись этого коммита СНОСИТСЯ
(fail-closed, как везде в воротах). Красная запись остаётся для карточки владельцу.

ФОРМАТ ВЕРДИКТА — ровно тот, которого ждут ворота (`client_contour.TRAINER_GREEN_FILE` =
`pc_orchestrator.client_trainer_green.json`, в .gitignore по маске `pc_orchestrator.*.json`):

    {"green": {"<commit7>": {"commit": "<40 hex>", "result": "green",
                             "checks_passed": N, "checks_total": N,
                             "cases": 12, "cases_total": 12, "runs": 2, "clean": true,
                             "head_moved": "", "head_after": "<40 hex>",
                             "tree_dirty": false, "tree_known": true, "dirty_paths": [],
                             "corpus": "trainer_cases.json", "corpus_sha": "<16 hex sha256>",
                             "runner": "trainer_run.py", "ts": <unix>, "when": "<ISO>"}},
     "red":   {"<commit7>": {... "result": "red", "failed": ["3/1 доставка = цена зоны", ...]}}}

ПРИВЯЗКА ЗАМЕРА К КОММИТУ (23.08.2026). Коммит ФИКСИРУЕТСЯ ОДИН раз на ВХОДЕ (`bind_head`) и
ложится в результат вместе с числом; на выходе вершина СВЕРЯЕТСЯ (`verify_head`), и сдвиг
называется пометкой `head_moved: <старый7>→<новый7>` В САМОЙ ЗАПИСИ, а не только в stdout. Грязь
дерева на момент фиксации видна полями `tree_dirty`/`dirty_paths` (судит её по-прежнему `clean` —
поля показывают, а не решают). Зачем — см. блок «ПРИВЯЗКА ЗАМЕРА К КОММИТУ» ниже по файлу.

Запуск:
    venv/Scripts/python.exe trainer_run.py                 # 12 кейсов × 2 прогона, вердикт на диск
    venv/Scripts/python.exe trainer_run.py --runs 1 --only 3 --no-write     # разведка одного кейса
    venv/Scripts/python.exe trainer_run.py --report docs/artifacts/<файл>.md
Код выхода: 0 — зелёный вердикт записан; 1 — красный (зелёного нет); 2 — прогон не состоялся
(инфраструктура: нет корпуса, git молчит, HEAD уехал) — вердикта нет вовсе, ворота держат.
"""

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.abspath(__file__))

# ── ЖИВОЙ ФОРМАТ ЗАПУСКА ─────────────────────────────────────────────────────────────────────
# load_dotenv() ДО импорта suggest — ровно как боевой процесс (`userbot_listen.py:39`, комментарий
# там же: «suggest импортируем ПОСЛЕ load_dotenv — модуль читает конфиг»). Без этой строки прогон
# уезжает мимо живого контура: SUGGEST_LLM_VIA_CLI читается как False → генерация идёт платным
# API-ключом (которого нет), а BRIDGE_URL пуст → парк берётся из снимка park_list.md. Проверено
# ЖИВЫМ запуском 30.07: без dotenv первый же кейс упал в anthropic-клиент. Правило-класс
# «проверка повторяет ЖИВОЙ ФОРМАТ ЗАПУСКА» (ENV_PLAYBOOK, п.8) — способ старта тоже часть формата.
from dotenv import load_dotenv                                           # noqa: E402

load_dotenv()

# Логи прогона — в temp, БОЕВЫЕ не трогаем. Рубильник штатный (log_setup: «явный переключатель
# только логов»). Живой факт 30.07: без него первый же прогон полез ротировать `pricing.log`,
# который держит открытым боевой процесс, и утонул в PermissionError WinError 32. Поведение
# пайплайна это не меняет — меняется только адресат файлов лога.
os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")

import client_contour                                                    # noqa: E402
import suggest                                                           # noqa: E402
import trainer                                                           # noqa: E402

CASES_FILE = client_contour.TRAINER_CASES_FILE
VERDICT_FILE = client_contour.TRAINER_GREEN_FILE

_MONTHS_GEN = ("", "января", "февраля", "марта", "апреля", "мая", "июня",
               "июля", "августа", "сентября", "октября", "ноября", "декабря")
_MONTHS_EN = ("", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December")

_CYR_RE = re.compile(r"[а-яёА-ЯЁ]")
_LAT_RE = re.compile(r"[a-zA-Z]")
# «• Сутки:» / «• Неделя:» — строки СЕТКИ прайса по парку. Для контрпримера (клиент спросил ТОЛЬКО
# про депозит) их появление и есть провал «вывалили прайс».
_SHEET_LINE_RE = re.compile(r"^\s*[•\-\*]\s*(?:сутки|неделя|месяц|day|week|month)\s*:", re.I | re.M)


_IPC_ISOLATED = False


def isolate_ipc():
    """Боевая moderation_ipc.db → одноразовый temp: даже если какой-то узел пайплайна однажды
    потянется к очереди/мете, он попадёт в свой файл, а не в живое состояние тренажёра (ТЕСТ-N,
    seq, транскрипт владельца). Идемпотентно.

    ПОД ТЕСТ-ОБВЯЗКОЙ (TESTING=1) НЕ ТРОГАЕМ НИЧЕГО: там БД уже уведена в temp И
    ПРОИНИЦИАЛИЗИРОВАНА `test_isolation`, а второй переезд ломает соседей в общем прогоне. Живой
    факт 30.07: пока это делалось ПРИ ИМПОРТЕ модуля, `-m unittest discover` ронял два теста
    test_suggest с «no such table: intake» — импорт раннера уводил БД у всего процесса. Отсюда же
    правило: побочек на импорте у раннера нет, изоляция включается в момент ПРОГОНА."""
    global _IPC_ISOLATED
    if _IPC_ISOLATED or str(os.getenv("TESTING") or "") == "1":
        return
    import moderation_ipc
    moderation_ipc.DB_PATH = os.path.join(tempfile.gettempdir(), "trainer_run_ipc.db")
    moderation_ipc.init_db()
    _IPC_ISOLATED = True


# ───────────────────────────── git: коммит и чистота дерева ──────────────────────────────────

def _git(args, timeout=30):
    """git из каталога репозитория → (rc, stdout). Молчащий/упавший git = (1, '')."""
    try:
        p = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "").strip()
    except Exception as e:                                   # noqa: BLE001 — «не знаю» ≠ «чисто»
        return 1, f"git не ответил: {e}"


def head_commit():
    """Текущий HEAD (40 hex) | '' — если git молчит. Вердикт без коммита не имеет смысла."""
    rc, out = _git(["rev-parse", "HEAD"])
    return out if rc == 0 and re.match(r"^[0-9a-f]{40}$", out or "") else ""


def dirty_tracked():
    """ОТСЛЕЖИВАЕМЫЕ правки против HEAD → список путей | None (git не ответил).
    Тот же источник правды, что у ворот грязного дерева (`pc_orchestrator._dirty_tracked`):
    python грузит модули С ДИСКА, и вердикт на грязном дереве удостоверял бы НЕ тот код."""
    rc, out = _git(["diff", "--name-only", "HEAD"])
    if rc != 0:
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


# ─────────────────── ПРИВЯЗКА ЗАМЕРА К КОММИТУ: фиксация на входе + сверка на выходе ─────────
# ЗАЧЕМ (живой случай 23.08.2026). Шесть живых прогонов набора легли на ТРИ РАЗНЫХ коммита
# (`_scratch_livenum_0823/live_run1[1-6].json`: 59edfdb×3, 9f14c1a, 4d5691f×2) — вершину двигала
# ЧУЖАЯ сессия, а прогон этого не заметил. Материальная причина: привязка жила ТОЛЬКО в `main()`
# (снятие HEAD в начале + сверка в конце), а НАРУЖУ модуль отдавал единственную ручку
# `head_commit()` — «какая вершина СЕЙЧАС». Каждый вызывающий импровизировал: живой раннер замера
# снимал HEAD ОДИН раз на процесс (`probe_livenum.py:114`) и штамповал его во ВСЕ свои прогоны, а
# сверки на выходе не делал вовсе — потому что её в модуле НЕ БЫЛО. Отсюда «чистый прогон», чей
# коммит назначен тем, что стояло за 12 минут до его конца.
#
# Устройство: коммит ФИКСИРУЕТСЯ ровно один раз (`bind_head`), а на выходе вершина сверяется
# (`verify_head`) — не совпало, значит замер НЕ принадлежит ни старому коммиту (код мог измениться
# под ногами), ни новому (его не мерили). Привязка НЕ переписывает `commit` новой вершиной ни
# одной веткой: сдвиг НАЗЫВАЕТСЯ пометкой `head_moved`, а не поглощается молча.

def bind_head():
    """ФИКСАЦИЯ коммита — ОДИН раз, на ВХОДЕ прогона. → привязка (dict), которую дальше носят
    с собой ВСЕ прогоны замера и которая целиком ложится в результат (`build_verdict(bind=…)`).

    Поля: `commit` (40 hex | '' — git молчит), `clean`/`dirty_paths` — состояние дерева НА МОМЕНТ
    ФИКСАЦИИ, `tree_known` — третий исход («git не ответил про diff» ≠ «чисто»: `clean` при этом
    fail-closed False, но врать «дерево грязное» тоже нельзя, поэтому незнание названо отдельно),
    `head_after`/`head_moved` — пустые до `verify_head`."""
    commit = head_commit()
    dirty = dirty_tracked()
    return {"commit": commit,
            "clean": dirty == [],
            "dirty_paths": list(dirty or []),
            "tree_known": dirty is not None,
            "head_after": "",
            "head_moved": ""}


def verify_head(bind):
    """СВЕРКА вершины на ВЫХОДЕ прогона против зафиксированной. → та же привязка (мутируется).

    Не совпало → `head_moved` = '<старый7>→<новый7>'. Молчание git на выходе (`head_commit()` →
    '') СЧИТАЕТСЯ СДВИГОМ и называется '<старый7>→?': доказать, что вершина стояла, мы не смогли,
    а «не смог проверить» зелёного не даёт (правило третьего исхода). Ровно это и делала прежняя
    ветка `main()` — здесь она вынесена в модуль, чтобы её видел КАЖДЫЙ вызывающий, а не CLI."""
    after = head_commit()
    bind["head_after"] = after
    bind["head_moved"] = ("" if after and after == bind.get("commit")
                          else "%s→%s" % (str(bind.get("commit") or "?")[:7], after[:7] or "?"))
    return bind


# ───────────────────────────────── корпус и подстановки ──────────────────────────────────────

def corpus_sha(path=None):
    """Отпечаток корпуса (sha256, 16 hex) — его же сверяют ворота: зелень, снятая на другом наборе
    кейсов, не должна засчитываться. → '' при нечитаемом файле."""
    return client_contour.corpus_sha(path or CASES_FILE)


def load_cases(path=None):
    """Корпус с диска → (список кейсов, sha). Битый/пустой файл → исключение: прогона не будет."""
    path = path or CASES_FILE
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{os.path.basename(path)}: кейсов нет")
    return cases, corpus_sha(path)


def placeholders(today=None):
    """Даты для реплик корпуса. Окно берём в СЛЕДУЮЩЕМ месяце (как проба runLiveSmoke): всегда в
    будущем и целиком в одном месяце — иначе прошедший старт уводит пайплайн в «уточните даты» и
    кейс проверял бы не то, что заявлено. Формулировки клиента при этом остаются ДОСЛОВНЫМИ:
    подставляются только числа."""
    d = today or suggest.today_phuket()
    yr = d.year + (1 if d.month == 12 else 0)
    mo = d.month % 12 + 1
    nyr = yr + (1 if mo == 12 else 0)
    nmo = mo % 12 + 1
    return {
        "when": f"с 6 по 11 {_MONTHS_GEN[mo]}",                     # 5 дней
        "when_sloppy": f"с 6ого по 11ое {_MONTHS_GEN[mo]}",         # живая небрежная форма ТЕСТ-11
        "when_1d": f"с 6 по 7 {_MONTHS_GEN[mo]}",                   # ровно сутки
        "when_month": f"с 6 {_MONTHS_GEN[mo]} по 6 {_MONTHS_GEN[nmo]}",   # месяц+ (кап тарифа)
        "when_en": f"from {_MONTHS_EN[mo]} 6 to {_MONTHS_EN[mo]} 11",
        "year": str(yr), "year_next": str(nyr),
    }


def build_transcript(case, ph):
    """Реплики кейса → накопительный транскрипт ТЕМ ЖЕ `trainer.append_turn`, что живой тренажёр.
    Строка, начинающаяся с «[менеджер]:», кладётся ходом менеджера (для кейсов «уже отвечали»)."""
    tr = ""
    for raw in case.get("lines", []):
        text = str(raw).format(**ph)
        if text.startswith("[менеджер]:"):
            tr = trainer.append_turn(tr, "manager", text.split(":", 1)[1].strip())
        else:
            tr = trainer.append_turn(tr, "client", text)
    return tr


# ─────────────────────────────────── боевой пайплайн ─────────────────────────────────────────

def generate(transcript, first):
    """ОДИН черновик по полному транскрипту. ДОСЛОВНАЯ КОПИЯ вызовов `_trainer_generate`
    (userbot_listen.py:245–256), только синхронная: там asyncio.to_thread ради живого цикла
    Telethon, здесь цикла нет. Порядок и аргументы обязаны совпадать вызов-в-вызов — расхождение
    ловит голден `test_trainer_run.test_pipeline_ne_razoshelsya` (AST-сверка обеих функций).
    → (черновик, pricing_note, hints)."""
    lang = suggest.detect_lang_from_client(transcript)
    hints = suggest.extract_booking_hints(transcript)
    price_note = suggest.build_pricing_note(hints, lang)
    allow = suggest.park_allowlist()
    pb = suggest.load_playbook()
    faq = suggest.load_faq()
    draft = suggest.generate_draft(transcript, lang, faq, first, price_note, None, allow, pb)
    return trainer.strip_thai(draft or ""), price_note, hints


# ──────────────────────────────────────── чеки ───────────────────────────────────────────────
# Инварианты берём ТЕ ЖЕ, что у e2e-смоука (`suggest._smoke_checks`, шесть чеков транспорта #92),
# и добавляем тренажёрные. Своей копии инвариантов здесь НЕТ — разъехаться нечему.

def _chk(name, ok, expected, fact):
    return {"name": name, "ok": bool(ok), "expected": expected, "fact": fact}


def expectations(case, transcript, note, hints):
    """Ожидания кейса из ТОГО ЖЕ pricing_note, что питает черновик (точка правды — живой Bridge)."""
    return {
        "j_line": suggest._quote_block_from_note(note),
        # НАЛИЧИЕ — из ТОГО ЖЕ источника, куда его печатает КОД: фразу «свободен на эти даты»
        # `_client_price` пишет в ЗАПИСКУ ровно под `if q.get("available")`. Ключ обязан быть здесь
        # ТОЖЕ: производителей ожиданий ДВА (этот и `suggest._smoke_expectations`), а читатель один
        # (`suggest._smoke_checks`). 23.08.2026 ключ завели только у второго — и живой прогон 21 дал
        # 6 из 12: на ЗДОРОВЫХ кейсах ветки `ok` правдивая строка КОДА звалась выдумкой, потому что
        # читатель получал пустоту. Замок на эту пару — `test_trainer_run` (оба словаря обязаны
        # нести ключ и одинаковое значение на одной записке).
        "avail": suggest.availability_from_note(note),
        "delivery_line": suggest._delivery_block_from_note(note),
        "sheet_line": suggest._sheet_block_from_note(note),
        "zone": case.get("zone"),
        "zone_price": case.get("zone_price"),
        "full_data": bool(hints.get("model") and hints.get("has_dates") and hints.get("maps_link")),
    }


def _zone_expectation(exp):
    """Цена зоны для чека доставки: из живой строки доставки, если кейс её не назвал явно."""
    if exp.get("zone_price") is not None:
        return exp
    dl = exp.get("delivery_line") or ""
    m = re.search(r"(\d[\d\s]{1,6})\s*(?:฿|бат|thb|baht)", dl, re.I)
    if m:
        exp = dict(exp)
        exp["zone_price"] = int(m.group(1).replace(" ", ""))
    return exp


def _code_blocks(exp):
    """Строки, которые вставляет в черновик КОД ДОСЛОВНО (quote/доставка/сетка) — их текст берётся
    из листа как есть и авторству LLM не принадлежит."""
    out = []
    for key in ("j_line", "delivery_line", "sheet_line"):
        out += [ln.strip() for ln in (exp.get(key) or "").split("\n") if ln.strip()]
    return out


def llm_prose(client, exp):
    """Текст, который сочинила МОДЕЛЬ: клиентский текст МИНУС дословные вставки КОДА. Нужен чеку
    языка: канон строки J вставляется кодом из листа (он по-русски даже в EN-ответе — отдельный
    класс, записан в остатки), и мерить им язык МОДЕЛИ значит красить кейс за чужую вину."""
    out = client
    for ln in _code_blocks(exp):
        out = out.replace(ln, " ")
    return out


def case_checks(case, draft, exp):
    """Все ПРИМЕНИМЫЕ чеки кейса по готовому черновику → список dict(name, ok, expected, fact).
    Применимость объявляет сам кейс (`expect`): цену/сетку/доставку требуем ТОЛЬКО там, где клиент
    их спросил, иначе «нет quote-блока» краснило бы приветственный кейс на ровном месте.
    `skip` кейса снимает ИМЕНОВАННЫЙ чек С ПРИЧИНОЙ (причина лежит в корпусе и видна в отчёте) —
    молча чек не исчезает никогда."""
    want = case.get("expect") or {}
    skip = case.get("skip") or {}
    client = suggest.client_facing_text(draft)
    exp = _zone_expectation(exp)
    smoke = {c["name"]: c for c in suggest._smoke_checks(draft, exp)}
    out = []

    # 1–2. точечная цена (quote) и доставка — по требованию кейса
    if want.get("quote"):
        out.append(smoke["строка J дословно"])
    if want.get("delivery"):
        out.append(smoke["доставка = цена зоны"])
    # 3–6. универсальные инварианты транспорта: годы, отписка, наличие, депозит
    for n in ("нет годов", "нет «вернусь/уточним» при полных данных",
              "нет утверждений о наличии", "депозит без противоречий"):
        out.append(smoke[n])

    # 7. сетка прайса по парку — ДОСЛОВНО (кейс «все модели и цены»)
    if want.get("sheet"):
        sheet = exp.get("sheet_line")
        lines = [ln.strip() for ln in (sheet or "").split("\n") if ln.strip()]
        missing = [ln for ln in lines if ln not in client]
        out.append(_chk("сетка прайса дословно", bool(lines) and not missing,
                        "строки сетки парка из sheet-блока — посимвольно в тексте клиента",
                        ("сетка не собрана — прайс-интент не сработал" if not lines
                         else ("не найдено дословно: " + " | ".join(missing)[:200] if missing
                               else "сетка в черновике посимвольно"))))
    # 8. контрпример прайса: клиент про сетку не спрашивал — вываливать её нельзя
    if want.get("no_sheet"):
        hit = _SHEET_LINE_RE.findall(client)
        out.append(_chk("прайс не вывален", not exp.get("sheet_line") and not hit,
                        "клиент про модели/цены не спрашивал — сетки парка в ответе нет",
                        ("sheet-блок собран прайс-интентом" if exp.get("sheet_line")
                         else (f"строки сетки в тексте: {len(hit)}" if hit else "сетки нет"))))
    # 9. цена цифрой: посчитали — обязаны назвать (живые ТЕСТ-7/ТЕСТ-10). Ожидание берём ИЗ ТОГО ЖЕ
    # quote/sheet-блока, а не «число с валютой»: живая строка J звучит как «стоимость: 3520 (…704 в
    # день)» — БЕЗ знака валюты, и требование «฿ рядом» краснило бы верный ответ (поймано 30.07).
    if want.get("price_figure"):
        nums = re.findall(r"\d{3,}", (exp.get("j_line") or "") + " " + (exp.get("sheet_line") or ""))
        # ЧИСЛО ЦЕЛИКОМ, а не кусок: «590» внутри «5900» — другая цена (правило suggest.word_hit)
        hit = [n for n in nums if suggest.word_hit(n, client)]
        has = bool(hit) if nums else bool(re.search(r"\d[\d\s]{2,}\s*(?:฿|бат|thb|baht)", client, re.I))
        out.append(_chk("цена цифрой", has,
                        "посчитанная цена (числа из quote/сетки) звучит клиенту",
                        ("числа в тексте: " + ", ".join(hit[:4])) if hit else
                        ("посчитанных чисел в тексте нет" if nums else "цифры цены в тексте нет")))

    # 10. тайские буквы: ฿ живёт, буквы — нет (strip_thai отработал)
    thai = [c for c in client if trainer._is_thai_letter(c)]
    out.append(_chk("тайских букв нет", not thai, "в тексте клиента нет тайских букв (฿ легитимен)",
                    ("остались: " + "".join(sorted(set(thai))[:10]) if thai else "тайских букв нет")))
    # 11. язык ответа = язык клиента (по тексту МОДЕЛИ; дословные вставки КОДА — см. llm_prose)
    lang = case.get("lang", "ru")
    prose = llm_prose(client, exp)
    if lang == "en":
        bad = _CYR_RE.findall(prose)
        ok, fact = not bad, ("кириллица в EN-ответе: " + "".join(sorted(set(bad))[:10])
                             if bad else "ответ на английском")
    else:
        ok = bool(_CYR_RE.search(prose))
        fact = "ответ по-русски" if ok else "кириллицы в ответе нет"
    out.append(_chk("язык ответа", ok, f"язык ответа совпал с языком клиента ({lang})", fact))
    # 12. приветствие ровно один раз (задвоенный зачин — живой класс #44/#311)
    greets = suggest._WINDOW_GREETING_RE.findall(client)
    out.append(_chk("приветствие не задвоено", len(greets) <= 1,
                    "зачин-приветствие в ответе не больше одного раза",
                    f"приветствий: {len(greets)}"))

    # 13. трекер собранного (§243/6): что клиент прислал — помечено, переспроса не будет
    for key in (case.get("expect_facts") or []):
        facts = suggest.collected_facts(case["_transcript"])
        out.append(_chk(f"трекер: {key}", bool(facts.get(key)),
                        f"собранное «{key}» засчитано трекером",
                        "засчитано" if facts.get(key) else "НЕ засчитано (будет переспрос)"))
    # 14–15. лексика кейса: запрещённое / обязательное (голдены на дословных фразах).
    # СРАВНЕНИЕ ПО СЛОВУ, а не по куску строки (`suggest.word_hit`, 23.08.2026): токен корпуса —
    # это КОРЕНЬ, и он обязан начинаться с начала слова. Иначе «опасн» краснило внутри
    # «безопасным» (ложный КРАСНЫЙ, 3 живых прогона из 7), а «водил»/«5 дней»/«от 3» зеленели
    # внутри «проводил»/«15 дней»/«от 300 ฿» (ложный ЗЕЛЁНЫЙ, ключ к воротам).
    low = client.lower()
    for word in (case.get("forbid") or []):
        found = suggest.word_hit(word.lower(), low)
        out.append(_chk(f"без «{word}»", not found,
                        f"формулировки «{word}» в ответе нет",
                        f"есть: «{word}»" if found else "нет"))
    req = case.get("require_any") or []
    if req:
        hit = [w for w in req if suggest.word_hit(w.lower(), low)]
        out.append(_chk("обязательное упоминание", bool(hit),
                        "в ответе есть одно из: " + ", ".join(req),
                        ("есть: " + ", ".join(hit)) if hit else "нет ни одного"))
    for c in out:
        if c["name"] in skip:
            c["skipped"] = str(skip[c["name"]])       # снят С ПРИЧИНОЙ, а не молча
    return out


# ────────────────────── МОЛЧАЩАЯ ГОЛОВА = НЕИЗВЕСТНО, а не зелёное ───────────────────────────
# ЗАМЕР 22.08.2026 (docs/artifacts/2026-08-22-honest-trainer-checks.md), из-за которого этот узел
# заведён: при МОЛЧАЩЕЙ голове набор оставался зелёным у 7 кейсов из 12 — 66 живых чеков из 112
# (58.9%). Зелень давал не бот, а КОД: `compose_quote_draft`, `ensure_price_figure`,
# `ensure_closing_question`, `_append_collected_note` дописывают в черновик текст, по которому и
# проходят «строка J дословно», «цена цифрой», «сетка прайса дословно», «язык ответа». Чек,
# проходящий на пустом ответе, проверкой не является — молчащий бот набирал почти шестьдесят
# процентов набора.
#
# ПРАВИЛО. Голова не дала текста → исход кейса НЕИЗВЕСТНО. Не зелёный (доказывать нечем) и не
# красный (кода мы не уличили — молчала ГРАНИЦА, а не продукт). «Неизвестно» сильнее «доказано»:
# оно не закрывается ни одним зелёным чеком, в зачёт чеков не идёт и зелёного вердикта не даёт
# ни одной веткой. Тем же правилом закрыт промах ЗАМЕРА: измерительная обвязка, проигрывающая
# снимок границы, на промахе отдаёт продукту пустую строку — и до 22.08 это давало ЗЕЛЁНОЕ МОЛЧА
# (правка промпта EN-ветки → те же «12 из 12»). Теперь промах снимка = «неизвестно».
#
# Почему «любой пустой заход», а не «все»: замерено на живом наборе — в здоровом прогоне ПУСТЫХ
# ответов нет ни одного (13 заходов, минимальный 48 симв., у кейса 2 их два). Значит пустой заход
# не бывает штатным, и мягкое условие «хоть один ответил» лишь пропускало бы промах пост-чека.
#
# Про пин этот узел НИЧЕГО НЕ ЗНАЕТ (замок `trainer_pin`: раннер его не импортирует): признак
# общий — «ответа головы не существует», и он одинаково ловит и живой таймаут CLI, и промах снимка.

class _HeadWatch(object):
    """Наблюдатель ответов головы на время ОДНОГО черновика.

    Оборачивает ОБЕ точки входа (`suggest._cli_llm`, `suggest._default_llm`) поверх того, что
    стои́т в модуле СЕЙЧАС: измерительная обвязка снаружи остаётся ПОД наблюдением, а не мимо
    него. Оригиналы возвращаются на место в `__exit__` при любом исходе."""

    ATTRS = ("_cli_llm", "_default_llm")

    def __init__(self):
        self.answers = []            # длины ответов головы, по заходам
        self.points = 0              # сколько точек входа удалось накрыть (считаем НА ВХОДЕ:
        self._orig = []              # `_orig` пустеет в `__exit__`, а судим мы уже после него)

    def _wrap(self, real):
        def wrapped(system, user):
            out = real(system, user)
            self.answers.append(len(out or ""))
            return out
        return wrapped

    def __enter__(self):
        for attr in self.ATTRS:
            real = getattr(suggest, attr, None)
            if real is None:
                continue
            self._orig.append((attr, real))
            self.points += 1
            setattr(suggest, attr, self._wrap(real))
        return self

    def __exit__(self, *exc):
        for attr, real in reversed(self._orig):
            setattr(suggest, attr, real)
        self._orig = []
        return False

    def silence(self):
        """Причина «судить нечего» ДОСЛОВНО | None — голова дала текст во всех заходах."""
        if not self.points:
            return "точек входа головы в suggest не нашлось — наблюдать нечего"
        if not self.answers:
            return "голову не спросили ни разу — ответа продукта не существует"
        empty = [i + 1 for i, n in enumerate(self.answers) if n <= 0]
        if empty:
            return ("голова промолчала: пустых ответов %d из %d (заходы %s, длины %s)"
                    % (len(empty), len(self.answers), empty, self.answers))
        return None


# ─────────────────────────────────────── прогон ──────────────────────────────────────────────

def run_case(case, ph, log=print):
    """Один прогон одного кейса → dict(id, name, ok, unknown, checks, draft, note).

    `unknown` — дословная причина «судить нечего» либо None. При непустом `unknown` кейс НЕ ЗАЧТЁН
    (`ok=False`) и его чеки помечены `unknown=True`: они посчитаны и видны в отчёте, но зачёту не
    подлежат — зелёный чек над текстом, который дописал код, ничего не доказывает."""
    isolate_ipc()                      # боевая мета/очередь недоступны по построению
    tr = build_transcript(case, ph)
    case = dict(case, _transcript=tr)
    first = not trainer.has_manager_turn(tr)
    with _HeadWatch() as hw:
        draft, note, hints = generate(tr, first)
    silence = hw.silence()             # СНАЧАЛА «неизвестно», и только потом красное: пустой
    if silence:                        # черновик при молчащей голове — вина границы, не кода
        checks = (case_checks(case, draft, expectations(case, tr, note, hints))
                  if (draft or "").strip() else [])
        for c in checks:
            c["unknown"] = True
        return {"id": case.get("id"), "name": case.get("name"), "ok": False, "unknown": silence,
                "checks": checks, "draft": draft, "note": note}
    if not (draft or "").strip():
        return {"id": case.get("id"), "name": case.get("name"), "ok": False, "unknown": None,
                "draft": "", "note": note,
                "checks": [_chk("черновик получен", False,
                                "непустой черновик от боевого пайплайна",
                                "голова ответила, но черновик пуст — оборвался пайплайн")]}
    checks = case_checks(case, draft, expectations(case, tr, note, hints))
    return {"id": case.get("id"), "name": case.get("name"), "unknown": None,
            "ok": all(c["ok"] for c in checks if not c.get("skipped")),
            "checks": checks, "draft": draft, "note": note}


def run_corpus(cases, runs=2, ph=None, log=print):
    """Корпус × runs прогонов → (результаты, passed_cases, checks_passed, checks_total, failed,
    unknown). Кейс зачтён, только если ВСЕ его чеки зелёные в КАЖДОМ прогоне (flake-контроль
    артефакта) и НИ В ОДНОМ прогоне исход не был «неизвестно».

    Чеки кейса с исходом НЕИЗВЕСТНО не идут НИ в `checks_passed`, НИ в `checks_total`, НИ в
    `failed`: они не зелёные (доказывать нечем) и не красные (продукт не уличён). Иначе вышло бы
    одно из двух вранья — либо зелень над текстом кода, либо обвинение кода в молчании границы."""
    ph = ph or placeholders()
    results, failed, unknown = [], [], []
    passed = checks_ok = checks_all = 0
    for case in cases:
        cid = case.get("id")
        case_ok = True
        for r in range(1, runs + 1):
            t0 = time.time()
            res = run_case(case, ph, log=log)
            res["run"] = r
            res["sec"] = round(time.time() - t0, 1)
            results.append(res)
            unk = res.get("unknown")
            if unk:
                unknown.append(f"{cid}/{r} {unk}")
            else:
                for c in res["checks"]:
                    if c.get("skipped"):
                        continue                   # снят с причиной — в счёт не идёт (виден в отчёте)
                    checks_all += 1
                    checks_ok += 1 if c["ok"] else 0
                    if not c["ok"]:
                        failed.append(f"{cid}/{r} {c['name']}")
            case_ok = case_ok and res["ok"] and not unk
            skipped = [c["name"] for c in res["checks"] if c.get("skipped")]
            log("  [%s] кейс %s «%s» прогон %d/%d — %s (%.0fс)%s"
                % ("UNK" if unk else ("OK " if res["ok"] else "RED"), cid, case.get("name"), r, runs,
                   ("НЕИЗВЕСТНО: " + str(unk)) if unk else
                   ("все чеки зелёные" if res["ok"] else
                    "провалено: " + ", ".join(c["name"] for c in res["checks"]
                                              if not c["ok"] and not c.get("skipped"))),
                   res["sec"], (" [снято: " + ", ".join(skipped) + "]") if skipped else ""))
        passed += 1 if case_ok else 0
    return results, passed, checks_ok, checks_all, failed, unknown


# ─────────────────────────────────────── вердикт ─────────────────────────────────────────────

def build_verdict(commit, cases_total, passed, checks_ok, checks_all, runs, clean, failed,
                  sha, now=None, unknown=None, bind=None):
    """Запись вердикта РОВНО в том виде, который читают ворота (client_contour.trainer_verdict).

    `unknown` — список исходов «судить нечего» (молчащая голова). Он ГАСИТ зелёное, но красным
    прогон не называет: `result` получает третье значение `'unknown'`. Для ворот это то же самое
    «не зелёный» (они сверяют `result == 'green'`, а `write_verdict` кладёт всё не-зелёное в
    fail-closed ящик), но владельцу в карточке больше не врут словом «КРАСНЫЙ» про то, чего
    прибор не измерил.

    `bind` — ПРИВЯЗКА замера (`bind_head` → `verify_head`). Из неё в результат ложатся четыре
    поля, которых до 23.08.2026 не было ни одного (23.08): `head_moved` — сдвинулась ли вершина ЗА
    ВРЕМЯ прогона и куда, `head_after` — что стояло на выходе, `tree_dirty`/`dirty_paths` — было
    ли дерево грязным В МОМЕНТ ФИКСАЦИИ коммита и чем именно. `tree_dirty` — ЗЕРКАЛО `clean`
    (одно поле, один факт: два независимых источника разъехались бы), поэтому вердикта оно не
    меняет — оно его ПОКАЗЫВАЕТ, а судить грязь остаётся прежнему условию `clean`.
    Условие зелёного НЕ РАСШИРЕНО: «HEAD не сдвинулся» стои́т в критерии с самого начала (шапка
    модуля, §ЗЕЛЁНЫЙ ВЕРДИКТ) — оно жило веткой `main()`, которая на сдвиге просто не доходила до
    записи. Теперь то же самое условие живёт в самой записи, и зелёная запись со сдвинутой
    вершиной физически не собирается — ни из CLI, ни из чужого раннера замера."""
    now = time.time() if now is None else now
    unknown = list(unknown or [])
    bind = bind or {}
    head_moved = str(bind.get("head_moved") or "")
    green = (not unknown
             and passed == cases_total >= client_contour.TRAINER_MIN_CASES
             and checks_all > 0 and checks_ok == checks_all
             and runs >= client_contour.TRAINER_MIN_RUNS and clean and not failed
             and not head_moved)
    return {
        "commit": commit,
        "result": "green" if green else ("red" if failed or not unknown else "unknown"),
        "unknown": len(unknown), "unknown_why": unknown[:40],
        "checks_passed": checks_ok, "checks_total": checks_all,
        "cases": passed, "cases_total": cases_total, "runs": runs, "clean": bool(clean),
        "head_moved": head_moved, "head_after": str(bind.get("head_after") or ""),
        "tree_dirty": not bool(clean), "tree_known": bool(bind.get("tree_known", True)),
        "dirty_paths": list(bind.get("dirty_paths") or [])[:40],
        "corpus": os.path.basename(CASES_FILE), "corpus_sha": sha,
        "runner": os.path.basename(__file__), "ts": now,
        "when": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "failed": failed[:40],
    }


def write_verdict(rec, path=None):
    """Вердикт на диск. FAIL-CLOSED: КРАСНЫЙ прогон СНОСИТ прежнюю зелёную запись этого коммита —
    иначе вчерашняя зелень открывала бы ворота коммиту, который сегодня красный. → (ok, путь)."""
    path = path or VERDICT_FILE
    c = client_contour.short(rec.get("commit"))
    if not c:
        return False, "вердикт без коммита не пишем"
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
        d = d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        d = {}
    green = d.get("green") if isinstance(d.get("green"), dict) else {}
    red = d.get("red") if isinstance(d.get("red"), dict) else {}
    if rec.get("result") == "green":
        green[c] = rec
        red.pop(c, None)
    else:
        green.pop(c, None)                      # fail-closed: красный снимает прежнюю зелень
        red[c] = rec
    for box in (green, red):
        if len(box) > client_contour._KEEP:
            for k, _v in sorted(box.items(), key=lambda kv: kv[1].get("ts", 0))[:len(box) - client_contour._KEEP]:
                box.pop(k, None)
    d["green"], d["red"], d["last"] = green, red, rec
    tmp = path + ".tmp"
    try:
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError as e:
        return False, f"вердикт не записан: {e}"
    return True, path


# ─────────────────────────────────────── отчёт ───────────────────────────────────────────────

def report_md(rec, results, commit, runs):
    """Отчёт прогона таблицей (для docs/artifacts) — по кейсам и провалившимся чекам."""
    lines = [f"# Прогон тренажёра — вердикт {rec['result'].upper()}", "",
             f"**Коммит:** `{commit}` (зафиксирован на входе) · **вершина за время прогона:** "
             + (f"**УЕХАЛА — head_moved: {rec['head_moved']}**" if rec.get("head_moved")
                else "неподвижна")
             + f" · **прогонов:** {runs} · **дерево:** "
             + ("чистое (tree_dirty: false)" if rec["clean"] else
                "ГРЯЗНОЕ (tree_dirty: true): " + ", ".join(rec.get("dirty_paths") or ["?"]))
             + f" · **корпус:** {rec['corpus']} (`{rec['corpus_sha']}`)", "",
             f"**Кейсы:** {rec['cases']}/{rec['cases_total']} · "
             f"**чеки:** {rec['checks_passed']}/{rec['checks_total']}"
             + (f" · **неизвестно:** {rec.get('unknown')} кейсо-прогонов"
                if rec.get("unknown") else ""), "",
             "| # | кейс | прогон | итог | чеки | провалено |", "|---|---|---|---|---|---|"]
    for r in results:
        live = [c for c in r["checks"] if not c.get("skipped")]
        bad = [c["name"] for c in live if not c["ok"]]
        skipped = ["%s (%s)" % (c["name"], c["skipped"]) for c in r["checks"] if c.get("skipped")]
        lines.append("| %s | %s | %s | %s | %s | %s |"
                     % (r["id"], r["name"], r["run"],
                        "🟡" if r.get("unknown") else ("🟢" if r["ok"] else "🔴"),
                        "—" if r.get("unknown") else "%d/%d" % (sum(1 for c in live if c["ok"]),
                                                                len(live)),
                        str(r["unknown"]) if r.get("unknown") else
                        (", ".join(bad) or ("снято: " + "; ".join(skipped) if skipped else "—"))))
    bad_rows = [r for r in results if not r["ok"] and not r.get("unknown")]
    if bad_rows:
        lines += ["", "## Провалы — ОЖИДАНИЕ / ФАКТ", ""]
        for r in bad_rows:
            lines.append(f"### кейс {r['id']} «{r['name']}», прогон {r['run']}")
            for c in r["checks"]:
                if c["ok"] or c.get("skipped"):
                    continue
                lines += [f"- **[{c['name']}]**", f"  - ожидание: {c['expected']}",
                          f"  - факт: {c['fact']}"]
            lines += ["", "```", (r.get("draft") or "")[:1200], "```", ""]
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────── CLI ────────────────────────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="безголовый прогон тренажёра → вердикт для ворот")
    ap.add_argument("--cases", default=CASES_FILE)
    ap.add_argument("--runs", type=int, default=client_contour.TRAINER_MIN_RUNS)
    ap.add_argument("--only", default="", help="через запятую: id кейсов (разведка)")
    ap.add_argument("--out", default=VERDICT_FILE)
    ap.add_argument("--report", default="")
    ap.add_argument("--no-write", action="store_true", help="вердикт на диск НЕ писать")
    ap.add_argument("--drafts", action="store_true", help="печатать черновики целиком")
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001 — старый поток
        pass

    bind = bind_head()                       # коммит фиксируется ОДИН раз и ЗДЕСЬ, до всякой работы
    commit = bind["commit"]
    if not commit:
        print("ПРОГОН НЕ СОСТОЯЛСЯ: git не назвал HEAD — вердикт не имеет смысла")
        return 2
    dirty = bind["dirty_paths"]
    clean = bind["clean"]
    try:
        cases, sha = load_cases(a.cases)
    except Exception as e:                                   # noqa: BLE001
        print(f"ПРОГОН НЕ СОСТОЯЛСЯ: корпус не прочитан ({type(e).__name__}: {e})")
        return 2
    total = len(cases)
    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        cases = [c for c in cases if str(c.get("id")) in want]

    print(f"ПРОГОН ТРЕНАЖЁРА: коммит {commit[:7]} ЗАФИКСИРОВАН на входе, кейсов {len(cases)} из "
          f"{total}, прогонов {a.runs}, дерево "
          f"{'чистое' if clean else 'ГРЯЗНОЕ: ' + ', '.join(dirty or ['? git не ответил'])}")
    ph = placeholders()
    results, passed, ok, allc, failed, unknown = run_corpus(cases, runs=a.runs, ph=ph)

    verify_head(bind)                        # вершина на выходе: сдвиг НАЗЫВАЕТСЯ, а не глотается
    rec = build_verdict(commit, total, passed, ok, allc, a.runs, clean, failed, sha,
                        unknown=unknown, bind=bind)
    print("\nИТОГ: %s — кейсов %d/%d, чеков %d/%d, прогонов %d, дерево %s%s"
          % (rec["result"].upper(), rec["cases"], rec["cases_total"], rec["checks_passed"],
             rec["checks_total"], rec["runs"],
             "чистое" if clean else "ГРЯЗНОЕ (tree_dirty: true)",
             (", НЕИЗВЕСТНО %d кейсо-прогонов" % len(unknown)) if unknown else ""))
    if rec["head_moved"]:
        # Замер принадлежит ЗАФИКСИРОВАННОМУ коммиту и НИКОМУ больше: новой вершине его не
        # приписываем (её не мерили), старой не удостоверяем (код мог уехать под ногами).
        print("ПРОГОН НЕ ЗАСЧИТАН: HEAD уехал за время прогона (head_moved: %s) — вердикт "
              "удостоверял бы другой код; число выше принадлежит коммиту %s, вердикт НЕ пишется"
              % (rec["head_moved"], commit[:7]))
    if unknown:
        print("неизвестно (судить нечего): " + "; ".join(unknown[:12]))
    if failed:
        print("провалено: " + "; ".join(failed[:20]))
    if a.drafts:
        for r in results:
            print(f"\n───── кейс {r['id']} «{r['name']}» прогон {r['run']} ─────\n{r['draft']}")
    if rec["head_moved"]:
        return 2                             # состав красных показан, но вердикта у этого прогона нет
    if a.report:
        with io.open(a.report, "w", encoding="utf-8") as f:
            f.write(report_md(rec, results, commit, a.runs))
        print(f"отчёт: {a.report}")
    if a.no_write:
        print("вердикт на диск НЕ записан (--no-write)")
        return 0 if rec["result"] == "green" else 1
    wok, where = write_verdict(rec, a.out)
    print(f"вердикт: {where}" if wok else f"ВЕРДИКТ НЕ ЗАПИСАН: {where}")
    if rec["result"] == "green":
        print("ворота клиентского контура откроются на этот коммит по основанию «trainer»")
        return 0
    print("ворота ДЕРЖАТ коммит: зелёного вердикта нет (нужно «да» владельца)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
