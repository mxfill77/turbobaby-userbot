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

ЗЕЛЁНЫЙ ВЕРДИКТ (критерий из артефакта, §5; с 07.09.2026 — ВАРИАНТ F, см. узел «КРИТЕРИЙ
НАБОРА» ниже по файлу) — все условия сразу:
  • 12 кейсов корпуса, каждый ЗАЧТЁН (12 из 12, не «11 из 12»);
  • ДВА прогона из двух (генератор недетерминирован — один прогон ничего не доказывает), а
    кейсам класса «обязательное упоминание» — ТРИ круга с зачётом большинством ≥2 из 3: у них
    ИЗМЕРЕНА лотерея головы 4.3% на круг, и «ни одного красного» ломалось бы без дефекта
    продукта в каждом шестом прогоне (замер a5f799a). Прощается только КРАСНОЕ МЕНЬШИНСТВО у
    зачтённого кейса, и прощённый круг в счёт чеков не идёт вовсе;
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

ЗАПИСЬ В РЕЕСТР — ТОЛЬКО ПО ЯВНОМУ КЛЮЧУ `--write` (05.09.2026, разведение прогонов). До этого дня
запись была УМОЛЧАНИЕМ, а безопасный замер требовал вспомнить `--no-write`. Это ровно наоборот
частоте вызовов: замер зовут постоянно (разведка, регресс урока, проверка кейса), а запись — это
акт ВЫКАТКИ на живых ботов, и в бою она случилась 1 раз за всю жизнь ворот (замер лога 05.09).
Забытый ключ у замера стоил бы выкатки, забытый ключ у выкатки — ничего, кроме второго запуска;
дешевле обязана быть та ошибка, которая случается чаще. Поэтому:
  • без ключа — ПРОГОН-ЗАМЕР: реестр не открывается на запись ни одной веткой;
  • `--write`  — прогон, МЕНЯЮЩИЙ реестр: зелёный кладётся в `green` (и ворота откроются на этот
    коммит), любой не-зелёный СНОСИТ прежнюю зелень того же коммита (fail-closed `write_verdict`);
  • `--no-write` жив и означает то же, что и раньше, — «писать не смей». Ключ оставлен намеренно:
    он стои́т в чужих командах и в подсказках уроков, и молчаливая смерть флага сделала бы их
    «неизвестной опцией» вместо честного замера. Вместе с `--write` побеждает ОН (fail-closed).

КОНТРОЛЬНАЯ ТОЧКА НАБОРА — `--state <файл>` (07.09.2026). До этого дня прогон был МОНОЛИТЕН:
результат появлялся ТОЛЬКО в конце (`build_verdict` после последнего круга), промежуточной записи
не было ни одной, и обрыв на любой минуте стоил ВЕСЬ заход целиком плюс повтор за ним. Цена этого
свойства измерена: 17 кейсов дают 38 кругов (13×2 + 4×3 классу), круг 43.7–46.6 с ⇒ 1661–1771 с
при потолке захода 2700 с — то есть один медленный круг или одна пауза границы, и полный вердикт
не помещается в заход ВООБЩЕ, а помещавшийся вчера пропадает целиком.
  • ТОЧКА СТАВИТСЯ ПОСЛЕ КЕЙСА, А НЕ ПОСЛЕ КРУГА. Исход кейса решает критерий F ПОСЛЕ всех его
    кругов (большинство 2 из 3); половина кругов исходом не является, и записанная как исход дала
    бы зелёное там, где решения ещё нет. Поэтому единица записи — ЗАКРЫТЫЙ кейс со всеми кругами.
  • ТОЧКА ПРИВЯЗАНА К ОСНОВАНИЮ ЦЕЛИКОМ (`point_basis`): коммит, корпус и его sha, число кругов,
    критерий (ид И текст правила), отпечаток ПРАВОК дерева (`tree_key` — sha `git diff HEAD`) и
    отпечаток ПОДСТАНОВОК (`ph_key` — даты и окно через границу сезонов уезжают сами). Любое
    расхождение → набор начинается ЗАНОВО, а причина НАЗЫВАЕТСЯ ВСЛУХ (stdout + поле
    `point_reset` самой записи вердикта). Молчаливое доиспользование чужой точки склеило бы
    вердикт из двух версий кода — ровно тот подлог, ради запрета которого вердикт привязан к
    коммиту целиком. «Не знаю» (`?` у отпечатка дерева/подстановок) совпадением НЕ считается.
  • ПРЕЖНИЙ НАБОР НЕ СТИРАЕТСЯ: точка держит наборы ПО КЛЮЧУ ОСНОВАНИЯ (`sets`), и смена
    основания заводит новый набор рядом, а не поверх. Вернулись на прежнее основание — прежний
    набор нашёлся и доигрывается.
  • КРАСНЫЙ И «НЕИЗВЕСТНО» ЛОЖАТСЯ В ТОЧКУ НАРАВНЕ С ЗЕЛЁНЫМ. Точка — запись СОСТОЯВШЕГОСЯ
    замера, а не механизм повтора: перегонять красный кейс на возобновлении значило бы
    перекатывать лотерею головы до нужного исхода.
  • ЗАПИСЬ АТОМАРНА (`tmp` + `os.replace`): убитый посреди записи процесс оставляет прошлую точку
    целой, а не половину файла.
  • ТОЧКА НЕ РЕЕСТР И НЕ ВЕРДИКТ: `--state` не открывает `--write` ни одной веткой, ворот не
    трогает и в клиентский контур не ходит.
Что точка НЕ ловит честно: правку дерева ВНУТРИ захода (отпечаток снимается на входе, как и
`clean`) — тот же остаток, что у привязки к коммиту, и сторожит его `head_moved`.

Запуск:
    venv/Scripts/python.exe trainer_run.py                 # 12 кейсов × 2 прогона, ЗАМЕР без записи
    venv/Scripts/python.exe trainer_run.py --runs 1 --only 3                # разведка одного кейса
    venv/Scripts/python.exe trainer_run.py --runs 2 --write   # ЗАМЕР + ЗАПИСЬ = выкатка при зелёном
    venv/Scripts/python.exe trainer_run.py --report docs/artifacts/<файл>.md
    venv/Scripts/python.exe trainer_run.py --state tmp/trainer_state/набор.json --only 1,2,3
    venv/Scripts/python.exe trainer_run.py --state tmp/trainer_state/набор.json   # добор остатка
Код выхода: 0 — вердикт зелёный (записан, если просили `--write`); 1 — не зелёный; 2 — прогон не
состоялся (инфраструктура: нет корпуса, git молчит, HEAD уехал) — вердикта нет вовсе, ворота держат.
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
import price_source                                                      # noqa: E402
import season_gate                                                       # noqa: E402
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

# ── ТРЕТИЙ ИСХОД: ОКНО СЧИТАЕТСЯ ИЗ ЖИВОЙ ТАБЛИЦЫ, А НЕ ЗАШИВАЕТСЯ ────────────────────────────
# Кейс третьего исхода («цену не считаю, зовёт человека», `season_gate`) обязан идти на сроке,
# который ДЕЙСТВИТЕЛЬНО пересекает границу сезонов. Зашитые даты этого не держат, и вот ЖИВОЕ
# доказательство, а не опасение: сама реплика клиента, с которой снят кейс 17
# (`logs/userbot_stderr.log:51830`, 03.08.2026), просит цену «с 27.12 по 18.01» — и обе эти даты
# лежат ВНУТРИ одного периода P5 ПИК (12-15…02-05), то есть границу НЕ пересекают вовсе
# (`season_gate.span` → `one`, замер 07.09.2026). Зашей мы даты живой фразы — кейс молча проверял
# бы обычный расчёт под именем третьего исхода. Поэтому окно СЧИТАЕТСЯ от ближайшей границы
# `price_source.json` и ПРОВЕРЯЕТСЯ `season_gate.span`; не сошлось — исход «неизвестно», а не
# зелёное (молчание таблицы выздоровлением не является — то же правило, что у слоя ожиданий).
NEEDS_SEASON_CROSS = "season_cross"
CROSS_BACK = 5        # суток ДО границы
CROSS_FWD = 5         # суток ПОСЛЕ границы (обе даты входят в срок — правило листа «Календарь»)
CROSS_MARGIN = 3      # запас, чтобы старт был в будущем и пайплайн не ушёл в «уточните даты»
CROSS_HORIZON = 400   # дальше года границы искать незачем — их девять на круг

# ЦЕНОВАЯ ЦИФРА ТРЕТЬЕГО ИСХОДА. Запрет шире, чем «число с ฿»: записка `season_gate` отдельной
# строкой запрещает и «от … ฿», и диапазон, и СРЕДНЕЕ по периоду — значит ловить надо ЛЮБОЕ
# число, звучащее ценой, а не только помеченное валютой.
_MONEY_RE = re.compile(
    r"(?:от|около|примерно|from|about|around)?\s*\d[\d\s]{0,8}\d*\s*"
    r"(?:฿|бат\w*|бахт\w*|thb|baht|usd|\$|€)"
    r"|\d[\d\s]{0,8}\d*\s*(?:/|за|в|per)\s*(?:день|сутки|сут\b|неделю|месяц|day|week|month)",
    re.I)
_NUM_RE = re.compile(r"\d[\d\s]{0,8}\d|\d+")
_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
# Разрешённая цифра прощается ТОЛЬКО в имени модели — то есть сразу за латинским словом
# («XMAX 300», «CB 650R»). Голый список номеров прощал бы и «примерно 650», а это уже ориентир
# цены: разрешать надо КОНТЕКСТ, а не число.
_MODEL_CTX_RE = re.compile(r"[A-Za-z][A-Za-z\-]{0,12}[\s\-]*$")


def price_hits(text, allow=()):
    """Всё, что звучит ЦЕНОЙ, в тексте клиента. → список найденного (пусто = цены нет ни в каком виде).

    Два сита, и второе — главное:
      1. число С ВАЛЮТОЙ или СО СТАВКОЙ («307 ฿», «от 1535 бат», «300 в день») — цена всегда,
         даже если само число разрешено кейсом: «300» в «XMAX 300» и «300 ฿» — разные факты;
      2. ЛЮБОЕ число от трёх знаков, кроме годов и кроме цифр ИМЕНИ модели (число из `allow`,
         стоящее сразу за латинским словом). Диапазон «1200–1500», среднее «около 1350» и
         ориентир «от 1200» попадают сюда все три — им не нужен ни знак валюты, ни слово «цена»,
         а клиенту они звучат ценой одинаково.
    Годы отданы СОСЕДНЕМУ чеку («нет годов»): одна вина — один красный, иначе отчёт врёт числом."""
    allow = {str(a).strip() for a in allow if str(a).strip()}
    text = text or ""
    out = []
    for m in _MONEY_RE.finditer(text):
        hit = m.group(0).strip()
        if hit and hit not in out:
            out.append(hit)
    for m in _NUM_RE.finditer(text):
        raw = m.group(0).strip()
        num = re.sub(r"\s+", "", raw)
        if len(num) < 3 or _YEAR_RE.match(num):
            continue
        if num in allow and _MODEL_CTX_RE.search(text[:m.start()]):
            continue                       # цифра ИМЕНИ модели, а не цены
        if raw not in out:
            out.append(raw)
    return out


def cross_window(today=None, doc=None):
    """Окно аренды, ПЕРЕСЕКАЮЩЕЕ ближайшую границу сезонов, — из ЖИВОЙ таблицы периодов.
    → dict(ok, text, iso_start, iso_end, seam, why). `ok=False` — окна нет, и `why` называет причину.

    Границу ищем ПО ФАЙЛУ (`price_source.period_of`), своей копии календаря здесь нет: перепишут
    сезоны — поедет и окно. Найденное окно СВЕРЯЕТСЯ `season_gate.span`, и несошедшаяся сверка
    даёт `ok=False`, а не «наверное пересекает»."""
    out = {"ok": False, "text": "", "iso_start": "", "iso_end": "", "seam": "", "why": ""}
    d = today or suggest.today_phuket()
    doc = price_source.load() if doc is None else doc
    if doc is None:
        out["why"] = "таблица сезонов не прочиталась (price_source.json) — окно считать нечем"
        return out
    day = d + datetime.timedelta(days=CROSS_MARGIN + CROSS_BACK)
    for _ in range(CROSS_HORIZON):
        try:
            prev = price_source.period_of(doc, day - datetime.timedelta(days=1))
            cur = price_source.period_of(doc, day)
        except Exception as exc:                  # noqa: BLE001 — отказ файла ≠ «границы нет»
            out["why"] = "периоды не разобрались (%s)" % type(exc).__name__
            return out
        if prev is not None and cur is not None and prev.get("key") != cur.get("key"):
            ds = day - datetime.timedelta(days=CROSS_BACK)
            de = day + datetime.timedelta(days=CROSS_FWD - 1)
            verdict, _names, detail = season_gate.span(ds.isoformat(), de.isoformat(), doc=doc)
            if verdict != season_gate.SEASON_CROSSES:
                out["why"] = ("окно %s → %s границу НЕ пересекает (%s: %s)"
                              % (ds.isoformat(), de.isoformat(), verdict, detail))
                return out
            out.update(ok=True, iso_start=ds.isoformat(), iso_end=de.isoformat(), seam=detail,
                       text="с %02d.%02d по %02d.%02d" % (ds.day, ds.month, de.day, de.month))
            return out
        day += datetime.timedelta(days=1)
    out["why"] = "границы сезонов в ближайшие %d суток не нашлось" % CROSS_HORIZON
    return out


def cross_guard(case, transcript, ph=None):
    """Кейс третьего исхода судим ТОЛЬКО на окне, реально пересекающем границу. → причина или ''.

    Непустая причина означает исход НЕИЗВЕСТНО (не зелёный и не красный): судить третий исход не
    на его условии — это зелень по неверной причине, и она была бы МОЛЧАЛИВОЙ. Сверяем не свою
    веру в плейсхолдер, а ДАТЫ, которые из реплики разобрал сам пайплайн."""
    if str(case.get("needs") or "") != NEEDS_SEASON_CROSS:
        return ""
    cross = (ph or {}).get("cross") or {}
    if cross and not cross.get("ok"):
        return "окно через границу не собрано: " + (str(cross.get("why")) or "причина не названа")
    h = suggest.extract_booking_hints(transcript)
    verdict, _names, detail = season_gate.span(h.get("iso_start"), h.get("iso_end"))
    if verdict == season_gate.SEASON_CROSSES:
        return ""
    return ("даты кейса границу сезонов НЕ пересекают (%s: %s) — кейс проверял бы обычный расчёт "
            "под именем третьего исхода" % (verdict, detail))


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
    # ОКНО ЧЕРЕЗ ГРАНИЦУ СЕЗОНОВ — единственный плейсхолдер, который НЕ выводится из календаря
    # месяцев: он считается из ЖИВОЙ таблицы периодов (узел «ТРЕТИЙ ИСХОД» выше). Не собралось —
    # подставляем обычное окно, а кейс третьего исхода ГАСИТ `cross_guard` исходом «неизвестно»:
    # молча позеленеть на непересекающих датах нельзя ни одной веткой.
    cross = cross_window(today=d)
    return {
        "when": f"с 6 по 11 {_MONTHS_GEN[mo]}",                     # 5 дней
        "when_sloppy": f"с 6ого по 11ое {_MONTHS_GEN[mo]}",         # живая небрежная форма ТЕСТ-11
        "when_1d": f"с 6 по 7 {_MONTHS_GEN[mo]}",                   # ровно сутки
        "when_month": f"с 6 {_MONTHS_GEN[mo]} по 6 {_MONTHS_GEN[nmo]}",   # месяц+ (кап тарифа)
        "when_en": f"from {_MONTHS_EN[mo]} 6 to {_MONTHS_EN[mo]} 11",
        "when_cross": cross["text"] or f"с 6 по 11 {_MONTHS_GEN[mo]}",    # через границу сезонов
        "cross": cross,
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
        # МИНИМАЛЬНЫЙ СРОК — из ТОЙ ЖЕ записки, что питает черновик. Пусто → ветка «короче
        # минимума» не срабатывала, гарантии в этом кейсе нет вовсе и чека тоже.
        #   `pairs` — предмет чека КОДА: ПОСТУСЛОВИЕ гарантии (правило звучит клиенту). Мерить
        #     ДОСЛОВНОЙ строкой кода нельзя: сказала голова сама — код молчит по построению,
        #     и дословного совпадения не будет НИКОГДА (живой замер 06.09: 2 круга из 3).
        #   `line` — та самая дословная строка; нужна `llm_prose`, чтобы текст КОДА не шёл голове
        #     в зачёт, и развилке require_any.
        "min_term_pairs": suggest.min_term_pairs_from_note(note),
        "min_term_line": suggest.min_term_line_from_note(note, case.get("lang", "ru")),
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
    """Строки, которые вставляет в черновик КОД ДОСЛОВНО (quote/доставка/сетка/минимальный срок) —
    их текст берётся из листа или из записки как есть и авторству LLM не принадлежит."""
    out = []
    for key in ("j_line", "delivery_line", "sheet_line", "min_term_line"):
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
    # 16. РАЗДЕЛЕНИЕ ЧЕКА (06.09.2026): минимальный срок с этого дня ГАРАНТИРУЕТ КОД
    # (`suggest.ensure_min_term`), а не голова. Значит и мерить его обязан чек КОДА — но мерить
    # ПОСТУСЛОВИЕ гарантии («правило звучит клиенту»), а НЕ дословную строку кода: гарантия
    # намеренно молчит, когда голова сказала правило сама, и чек на дословность краснил бы
    # ЗДОРОВЫЙ ответ. Это не рассуждение: живой прогон 06.09 дал ровно такой красный на 2 кругах
    # из 3, где ответ клиенту был безупречен.
    min_pairs = [tuple(p) for p in (exp.get("min_term_pairs") or [])]
    min_line = (exp.get("min_term_line") or "").strip()
    if min_pairs:
        reached = suggest.min_term_said(client, min_pairs, lang)
        out.append(_chk("минимальный срок доехал", reached,
                        "правило «сдаём от N дней» звучит клиенту — минимум назван числом и"
                        " сказано, что короче не сдаём (гарантия suggest.ensure_min_term)",
                        ("правило в тексте (%s)" % ("строку дописал КОД" if min_line in client
                                                    else "своими словами головы")) if reached
                        else "правила в тексте клиента НЕТ ни в каком виде"))
    # 17. ТРЕТИЙ ИСХОД: цены нет НИ В КАКОМ ВИДЕ. Чек существует отдельно от `forbid` потому, что
    # `forbid` меряет СЛОВА (`suggest.word_hit`), а запрещено здесь ЧИСЛО — и не одно конкретное,
    # а любое: точное, «от … ฿», диапазон, среднее по периоду. Разрешённые цифры кейс называет
    # ПОИМЁННО и с причиной (`forbid_price.allow`) — молча тут не разрешается ничего.
    fp = case.get("forbid_price")
    if fp:
        allow = (fp.get("allow") or []) if isinstance(fp, dict) else []
        money = price_hits(client, allow)
        out.append(_chk("цены нет ни в каком виде", not money,
                        "в ответе нет ни одной ценовой цифры: ни точной, ни «от … ฿», ни "
                        "диапазона, ни среднего по периоду"
                        + (" (разрешено кейсом: " + ", ".join(str(a) for a in allow) + ")"
                           if allow else ""),
                        ("цена прозвучала: " + ", ".join(money[:6])) if money
                        else "ценовых цифр в тексте нет"))
    req = case.get("require_any") or []
    if req:
        # Токены, которые ТЕПЕРЬ закрывает строка КОДА, голове в зачёт не идут: зелёный чек над
        # текстом, который дописал код, ничего не доказывает о голове (тот же класс, из-за
        # которого 22.08 молчащая голова набирала 58.9% набора). Правило РЕЖЕТ ПО ФАКТУ, а не по
        # номеру кейса: сработала ли гарантия на ЭТОЙ записке — видно из min_term_line, и у
        # кейсов 5/6 (опыт/менеджер) она пуста, поэтому их require_any остаётся мерой ГОЛОВЫ.
        by_code = [w for w in req if suggest.word_hit(w.lower(), min_line.lower())] if min_line else []
        hit = [w for w in req if suggest.word_hit(w.lower(), low)]
        chk = _chk("обязательное упоминание", bool(hit),
                   "в ответе есть одно из: " + ", ".join(req),
                   ("есть: " + ", ".join(hit)) if hit else "нет ни одного")
        if by_code:
            # ЧТО ГОЛОВА СКАЗАЛА САМА — считаем по её прозе (client МИНУС вставки КОДА) и печатаем
            # в отчёт. Числу это место даёт, вердикту — нет: снятый чек в зачёт не идёт.
            own = [w for w in req if suggest.word_hit(w.lower(), prose.lower())]
            chk["fact"] = ("голова сама: " + (", ".join(own) if own else "не сказала ничего")
                           + "; закрыто гарантией кода: " + ", ".join(by_code))
            chk["skipped"] = ("гарантирует КОД (suggest.ensure_min_term) — голове не "
                              "засчитывается; мерит чек «минимальный срок доехал»")
        out.append(chk)
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


# ────────────── КРИТЕРИЙ НАБОРА: ВАРИАНТ F ПО ИЗМЕРЕННОЙ ЛОТЕРЕЕ (07.09.2026) ────────────────
# ЗАЧЕМ. Прежний критерий — «два круга, ни одного красного, у всех кейсов» — по арифметике не
# выдерживает ИЗМЕРЕННОЙ лотереи головы. ЗАМЕР 06–07.09 (docs/artifacts/2026-09-06-частота-
# лотереи-три-кейса-06.09.md, коммит a5f799a): частота красного круга БЕЗ дефекта продукта —
# 4.3% у кейса 5 и 4.3% у кейса 6 (23 круга на кейс, объединённая выборка). При двух таких
# кейсах «два круга без единого красного» даёт зелёный НАБОР лишь в 83.7% прогонов: каждый
# шестой красный набор рождается лотереей, а не дефектом. Чтобы держать 90%, частота обязана
# быть ≤2.6% на круг — она вдвое больше, и уменьшить её критерием нельзя, она свойство головы.
#
# ЧТО СДЕЛАНО (решение Штаба 07.09 — вариант F таблицы §5.3 того же артефакта): кейсам КЛАССА
# «обязательное упоминание» — ТРИ круга с зачётом БОЛЬШИНСТВОМ (≥2 из 3), остальным — два круга
# и все зелёные, как было. Цена +3 кейс-круга (35 вместо 32, ≈1632 с вместо ≈1492 — плюс 9%
# времени экзамена), набор поднимается до 98.9%, а сила детекции ровно та же, что у втрое более
# дорогого «три круга ВСЕМ» (вариант B, 48 кругов).
#
# ЧТО СОЗНАТЕЛЬНО НЕ СДЕЛАНО И НЕ ДОЛЖНО ПРИЕХАТЬ ПОЗЖЕ ПОД ВИДОМ УПРОЩЕНИЯ. Вариант D
# («классу хватает ОДНОГО зелёного круга из двух») дешевле F на все три круга и поднимает набор
# до 99.6% — и ОТКЛОНЁН: он роняет поимку ползучей регрессии (кейс, который сломан и краснеет в
# трети прогонов) с 51% до 9%, то есть превращает чек в украшение. «≥1 из N» — это не упрощение
# F, а другой вариант с другой ценой, и заводить его надо решением, а не рефакторингом.
#
# КЛАСС БЕРЁТСЯ ИЗ КОРПУСА, А НЕ СПИСКОМ НОМЕРОВ В КОДЕ. Признак — непустой `require_any` у
# самого кейса (`trainer_cases.json`), а не «кейсы 5, 6, 10»: список номеров протухнет на первом
# же новом кейсе с чеком прозы головы, и протухнет МОЛЧА — новый кейс поедет на двух кругах и
# вернёт сегодняшнюю лотерею, ничем себя не выдав. Номеров кейсов в этом файле нет ни одного.
#
# ГРАНИЦЫ ПОСЛАБЛЕНИЯ — их четыре, и ни одна не расширена:
#   • «неизвестно» большинством НЕ ЛЕЧИТСЯ: молчащая голова гасит и кейс, и весь набор, ровно
#     как гасила (класс 22.08). Прощается только КРАСНЫЙ круг — то есть ответ, который прибор
#     ИЗМЕРИЛ и признал негодным, а не тот, которого не было;
#   • прощается только МЕНЬШИНСТВО и только у кейса, зачтённого в целом: 2 красных круга из 3 —
#     красный кейс и красный набор, и все его красные круги идут в `failed` пофамильно;
#   • чеки прощённого круга не идут НИ в `checks_passed`, НИ в `checks_total`, НИ в `failed` —
#     ровно как чеки круга «неизвестно». Считать прощённый круг зелёным значило бы врать числом
#     вместо честного «этот круг в зачёт не идёт»;
#   • при `runs` меньше `TRAINER_MIN_RUNS` лишних кругов НЕ ТРАТИМ ВОВСЕ: такой прогон зелёным
#     не бывает ни одной веткой (`build_verdict` требует `runs >= TRAINER_MIN_RUNS`), а значит
#     большинство ему нечего защищать. Разведка `--runs 1 --only 5` и регресс урока
#     (`lesson_regress`, тоже `runs=1`) стоят ровно столько же, сколько стоили до 07.09.
CRITERION = "F"
CLASS_NAME = "обязательное упоминание"
CLASS_RUNS = 3


def case_class(case):
    """Класс кейса — ИЗ КОРПУСА. → имя класса либо '' (кейс класса не несёт).

    Единственный признак класса «обязательное упоминание» — непустой `require_any` в самом
    кейсе. Ни одного номера кейса здесь нет и быть не должно (см. узел выше)."""
    return CLASS_NAME if (case.get("require_any") or []) else ""


def rounds_for(case, runs):
    """Сколько кругов гонять ЭТОМУ кейсу при базовом числе кругов `runs`. → int.

    Кейс класса получает не «+1», а ПОЛ в `CLASS_RUNS`: при `--runs 5` он идёт те же 5, а не 6.
    При `runs` ниже порога ворот критерий не работает вовсе — зелёного вердикта у такого прогона
    не бывает, и платить за большинство незачем."""
    runs = int(runs)
    if runs < client_contour.TRAINER_MIN_RUNS:
        return runs
    return max(runs, CLASS_RUNS) if case_class(case) else runs


def need_green(rounds, klass):
    """Сколько ЗЕЛЁНЫХ кругов из `rounds` нужно кейсу для зачёта. → int.

    Классу — большинство (для трёх кругов это 2), остальным — ВСЕ круги, как было до 07.09."""
    rounds = int(rounds)
    return (rounds // 2 + 1) if klass else rounds


def criterion_text(runs):
    """Применённый критерий ОДНОЙ строкой — в вердикт, в отчёт и в карточку владельцу.

    Строка описывает ПРАВИЛО, действующее в этом коде; сколько кругов вышло у каждого кейса,
    говорит отдельное поле `runs_by_case` (правило и его применение — разные факты)."""
    return ("%s: кейсам класса «%s» (признак — require_any в корпусе) %d круга, зачёт "
            "большинством ≥%d из %d; остальным %d круга, зелёными обязаны быть все; «неизвестно» "
            "большинством не лечится"
            % (CRITERION, CLASS_NAME, CLASS_RUNS, need_green(CLASS_RUNS, CLASS_NAME), CLASS_RUNS,
               int(runs)))


def _num_key(k):
    """Порядок кейсов в строке «кругов по кейсам»: числа числами, прочее словами."""
    s = str(k)
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


def rounds_line(by_case):
    """«Кругов у каждого кейса» человекочитаемой строкой: `1:2 2:2 … 5:3 6:3 …`. → str."""
    if not by_case:
        return "—"
    return " ".join("%s:%s" % (k, by_case[k]) for k in sorted(by_case, key=_num_key))


# ══════════════════ КОНТРОЛЬНАЯ ТОЧКА НАБОРА: единица — ЗАКРЫТЫЙ КЕЙС ════════════════════════
# ЗАЧЕМ (арифметика, а не вкус). Прогон вердикта монолитен: 38 кругов × 43.7–46.6 с = 1661–1771 с
# при потолке захода 2700 с. Контрольной точки у него не было ни одной, поэтому таймаут стоил не
# единицу работы, а ВЕСЬ заход и повтор за ним. Точка делает цену обрыва равной ОДНОМУ кейсу.
#
# ПОЧЕМУ ПОСЛЕ КЕЙСА, А НЕ ПОСЛЕ КРУГА. Исход кейса решает критерий F ПОСЛЕ ВСЕХ его кругов
# (класс «обязательное упоминание» — большинство ≥2 из 3). Круг исходом не является: записанный
# как исход первый зелёный круг кейса класса дал бы зелёное там, где решения ещё нет, а первый
# красный — красное у кейса, который критерий зачтёт. Поэтому единица записи — закрытый кейс
# целиком, со всеми своими кругами, планом и прощёнными кругами.
#
# ПОЧЕМУ ОСНОВАНИЕ ЦЕЛИКОМ. Точка обещает «эти кейсы уже померены», и обещание держится ровно
# настолько, насколько совпадает ВСЁ, от чего зависел замер. Совпал один коммит — мало: другой
# корпус, другое число кругов, другой критерий зачёта или другие подставленные даты дают ДРУГОЙ
# замер под тем же именем. Расхождение любого поля → набор ЗАНОВО и вслух.

POINT_KEEP = 8                    # столько наборов разных оснований точка держит рядом

_BASIS_WORDS = {"commit": "коммит", "corpus": "корпус", "corpus_sha": "отпечаток корпуса",
                "runs": "кругов базово", "criterion": "критерий (правило)",
                "criterion_id": "критерий (ид)", "tree": "правки дерева",
                "ph": "подстановки (даты)"}


def tree_key():
    """Отпечаток ПРАВОК рабочего дерева против HEAD (sha256, 16 hex) → '?' — git не ответил.

    Коммита мало: python грузит модули С ДИСКА, и на грязном дереве код опознаётся НЕ коммитом.
    Берём весь патч `git diff HEAD` (не список имён): дважды правленный один файл даёт тот же
    список путей и РАЗНЫЙ код. Чистое дерево даёт стабильный отпечаток пустого патча.
    '?' — это ТРЕТИЙ ИСХОД, и он не равен ни одному другому значению (см. `basis_diff`)."""
    rc, out = _git(["diff", "HEAD"])
    if rc != 0:
        return "?"
    return hashlib.sha256((out or "").encode("utf-8", "replace")).hexdigest()[:16]


def ph_key(ph):
    """Отпечаток ПОДСТАНОВОК кейсов (sha256, 16 hex) → '?' — посчитать нечем.

    Даты уезжают САМИ (месяц в `placeholders`, окно через границу сезонов — из живой таблицы
    периодов), а кейс, померенный на других датах, — это другой замер, а не тот же."""
    try:
        raw = json.dumps(ph or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return "?"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def point_basis(commit, sha, runs, ph, corpus=None, tree=None):
    """ОСНОВАНИЕ набора — всё, от чего зависит замер кейса. → dict.

    `tree`/`ph` можно подать готовыми (тесты и повторные вызовы), иначе считаются здесь."""
    return {"commit": str(commit or ""),
            "corpus": str(corpus or os.path.basename(CASES_FILE)),
            "corpus_sha": str(sha or ""),
            "runs": int(runs),
            "criterion": criterion_text(runs),
            "criterion_id": CRITERION,
            "tree": tree_key() if tree is None else str(tree),
            "ph": ph_key(ph) if not isinstance(ph, str) else ph}


def basis_key(basis):
    """Ключ основания (sha256, 16 hex) — под ним набор лежит в файле точки."""
    raw = json.dumps(basis or {}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _val(v):
    """Значение поля основания для человека: длинное режем, чтобы причина читалась строкой."""
    s = str(v)
    return s if len(s) <= 46 else s[:43] + "…"


def basis_diff(old, new):
    """ЧЕМ основания разошлись. → список дословных причин ('' пустой список = сошлись целиком).

    Незнание (`?` у отпечатка дерева или подстановок) совпадением НЕ считается НИ РАЗУ, даже когда
    '?' стои́т с обеих сторон: два «не знаю» — это не «то же самое», а два неизмеренных факта."""
    out = []
    for k in sorted(set(old or {}) | set(new or {})):
        a, b = (old or {}).get(k), (new or {}).get(k)
        word = _BASIS_WORDS.get(k, k)
        if k in ("tree", "ph") and (str(a) == "?" or str(b) == "?"):
            out.append("%s: назвать нечем (%s против %s) — «не знаю» совпадением не считается"
                       % (word, _val(a), _val(b)))
            continue
        if a != b:
            out.append("%s: %s против %s" % (word, _val(a), _val(b)))
    return out


def case_rec_bad(rec, case, runs):
    """Годна ли запись кейса из точки. → '' (годна) либо дословная причина отказа.

    Fail-closed: битую, чужую или разошедшуюся по числу кругов запись НЕ чиним и НЕ доверяем ей —
    кейс гоняется заново, а причина называется."""
    if not isinstance(rec, dict):
        return "запись кейса битая (не объект)"
    if str(rec.get("id")) != str(case.get("id")):
        return "запись лежит под чужим ключом (в ней id=%s)" % _val(rec.get("id"))
    plan = rec.get("plan")
    if not isinstance(plan, dict) or not isinstance(rec.get("results"), list):
        return "в записи нет плана либо кругов"
    want = rounds_for(case, runs)
    if int(plan.get("rounds") or 0) != want or len(rec["results"]) != want:
        return ("кругов в записи %s (план) / %d (фактом), а критерий требует %d"
                % (_val(plan.get("rounds")), len(rec["results"]), want))
    for fld in ("checks_ok", "checks_all"):
        if not isinstance(rec.get(fld), int):
            return "в записи нет счёта чеков (%s)" % fld
    if not isinstance(rec.get("failed"), list) or not isinstance(rec.get("unknown"), list):
        return "в записи нет списков провалов/неизвестного"
    return ""


class Point(object):
    """Контрольная точка набора: ЗАКРЫТЫЕ кейсы ОДНОГО основания, переживающие обрыв прогона.

    Держит весь документ точки в памяти (`doc`) и переписывает его целиком атомарно после каждого
    закрытого кейса. Наборы ЧУЖИХ оснований в документе не трогаются — они лежат рядом."""

    def __init__(self, path, basis, doc=None, done=None, reset=""):
        self.path = str(path)
        self.basis = dict(basis or {})
        self.key = basis_key(self.basis)
        self.doc = doc if isinstance(doc, dict) else {}
        self.done = dict(done or {})
        self.reset = str(reset or "")
        self.taken, self.taken_rounds = [], 0        # взято из точки: кейсы и их круги
        self.live, self.live_rounds = [], 0          # прогнано живьём в ЭТОМ заходе

    def get(self, case, runs):
        """Годная запись ЭТОГО кейса → (запись | None, причина отказа | '')."""
        rec = self.done.get(str(case.get("id")))
        if rec is None:
            return None, ""
        why = case_rec_bad(rec, case, runs)
        return (None, why) if why else (rec, "")

    def put(self, rec):
        """Кейс ЗАКРЫТ → в точку, на диск, атомарно. → (записано?, причина отказа | путь)."""
        self.done[str(rec.get("id"))] = rec
        sets = self.doc.get("sets") if isinstance(self.doc.get("sets"), dict) else {}
        now = time.time()
        sets[self.key] = {"basis": self.basis, "ts": now,
                          "when": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
                          "cases": self.done}
        if len(sets) > POINT_KEEP:
            old = sorted(sets.items(), key=lambda kv: (kv[1] or {}).get("ts", 0))
            for k, _v in old[:len(sets) - POINT_KEEP]:
                if k != self.key:
                    sets.pop(k, None)
        self.doc["sets"] = sets
        tmp = self.path + ".tmp"
        try:
            d = os.path.dirname(os.path.abspath(self.path))
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.doc, f, ensure_ascii=False)
            os.replace(tmp, self.path)              # атомарно: убитая запись не рвёт прошлую точку
        except OSError as e:
            return False, "точка не записана: %s" % e
        return True, self.path


def open_point(path, basis):
    """Открыть контрольную точку по её ОСНОВАНИЮ. → Point (её `reset` — причина сброса либо '').

    Расхождение основания НЕ чинится и НЕ доиспользуется частями: набор начинается ЗАНОВО, причину
    несёт `reset`, и её обязан сказать вслух вызывающий. Прежний набор при этом НЕ стирается — он
    остаётся в файле под своим ключом основания и найдётся, если вернуться на то основание.
    Отсутствия файла сбросом НЕ зовём: точки просто ещё не было."""
    doc = {}
    try:
        with io.open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except OSError:
        return Point(path, basis)                        # точки ещё не было — это не сброс
    except ValueError as e:
        return Point(path, basis, reset="файл точки не разобран (%s) — набор начат ЗАНОВО" % e)
    if not isinstance(doc, dict):
        return Point(path, basis, reset="файл точки не объект — набор начат ЗАНОВО")
    sets = doc.get("sets") if isinstance(doc.get("sets"), dict) else {}
    mine = sets.get(basis_key(basis))
    if isinstance(mine, dict):
        diff = basis_diff(mine.get("basis") or {}, basis)
        if diff:
            # Ключ сошёлся, а поля нет — подлог либо битый файл. Верим ПОЛЯМ, а не ключу.
            return Point(path, basis, doc,
                         reset="набор под тем же ключом, но основание НЕ то: " + "; ".join(diff))
        done = mine.get("cases") if isinstance(mine.get("cases"), dict) else {}
        return Point(path, basis, doc, done)
    if not sets:
        return Point(path, basis, doc)                   # файл есть, наборов нет — не сброс
    last = max(sets.values(), key=lambda v: (v or {}).get("ts", 0) if isinstance(v, dict) else 0)
    cases = (last or {}).get("cases") if isinstance(last, dict) else {}
    diff = basis_diff((last or {}).get("basis") or {}, basis)
    return Point(path, basis, doc,
                 reset=("в точке лежит набор ДРУГОГО ОСНОВАНИЯ (закрытых кейсов %d, записан %s) — "
                        "он НЕ доиспользуется, набор начат ЗАНОВО; расхождение: %s"
                        % (len(cases if isinstance(cases, dict) else {}),
                           _val((last or {}).get("when") or "?"),
                           "; ".join(diff) or "поля основания не названы (запись без основания)")))


def point_info(point):
    """Что сказать вердикту про контрольную точку. → dict (пустой — точки не было вовсе)."""
    if point is None:
        return {}
    return {"path": point.path, "basis": point.key, "reset": point.reset,
            "taken": list(point.taken), "taken_rounds": int(point.taken_rounds),
            "live": list(point.live), "live_rounds": int(point.live_rounds)}


# ─────────────────────────────────────── прогон ──────────────────────────────────────────────

def run_case(case, ph, log=print):
    """Один прогон одного кейса → dict(id, name, ok, unknown, checks, draft, note).

    `unknown` — дословная причина «судить нечего» либо None. При непустом `unknown` кейс НЕ ЗАЧТЁН
    (`ok=False`) и его чеки помечены `unknown=True`: они посчитаны и видны в отчёте, но зачёту не
    подлежат — зелёный чек над текстом, который дописал код, ничего не доказывает."""
    isolate_ipc()                      # боевая мета/очередь недоступны по построению
    tr = build_transcript(case, ph)
    # УСЛОВИЕ КЕЙСА ПРОВЕРЯЕТСЯ ДО ГОЛОВЫ. Кейс, объявивший `needs`, судится только на своём
    # условии; не сошлось — «неизвестно» БЕЗ круга головы (и без его 47 секунд): зелёное по
    # неверной причине хуже честного «проверить было нечем».
    blocked = cross_guard(case, tr, ph)
    if blocked:
        return {"id": case.get("id"), "name": case.get("name"), "ok": False, "unknown": blocked,
                "checks": [], "draft": "", "note": ""}
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


def run_one_case(case, runs, ph, log=print):
    """ВСЕ круги ОДНОГО кейса + его исход по критерию → САМОДОСТАТОЧНАЯ запись кейса (dict).

    Это ЕДИНИЦА НАБОРА и единица контрольной точки: раньше этого места исхода не существует
    (критерий F решает после всех кругов), позже — уже не нужно ничего пересчитывать, потому что
    всё, чем кейс входит в вердикт, лежит в этой записи: `results` (круги с черновиками и чеками),
    `plan` (класс, круги, нужное большинство, зелёные/красные/неизвестные, прощённые),
    `checks_ok`/`checks_all` (счёт чеков ЭТОГО кейса), `failed`/`unknown` (его строки для вердикта).

    Запись обязана быть JSON-сериализуемой целиком — на ней стои́т возобновление.

    Чеки круга с исходом НЕИЗВЕСТНО и чеки ПРОЩЁННОГО критерием круга не идут НИ в `checks_ok`,
    НИ в `checks_all`, НИ в `failed`: они не зелёные (доказывать нечем либо доказано обратное) и не
    красные для вердикта (кейс зачтён большинством). Иначе вышло бы одно из двух вранья — либо
    зелень над текстом, который признан негодным, либо красный вердикт у кейса, который критерий
    зачёл."""
    cid = case.get("id")
    klass = case_class(case)
    total = rounds_for(case, runs)
    need = need_green(total, klass)
    mine, failed, unknown = [], [], []
    checks_ok = checks_all = 0
    for r in range(1, total + 1):
        t0 = time.time()
        res = run_case(case, ph, log=log)
        res["run"] = r
        res["rounds"] = total
        res["sec"] = round(time.time() - t0, 1)
        mine.append(res)
        skipped = [c["name"] for c in res["checks"] if c.get("skipped")]
        log("  [%s] кейс %s «%s» прогон %d/%d — %s (%.0fс)%s"
            % ("UNK" if res.get("unknown") else ("OK " if res["ok"] else "RED"), cid,
               case.get("name"), r, total,
               ("НЕИЗВЕСТНО: " + str(res["unknown"])) if res.get("unknown") else
               ("все чеки зелёные" if res["ok"] else
                "провалено: " + ", ".join(c["name"] for c in res["checks"]
                                          if not c["ok"] and not c.get("skipped"))),
               res["sec"], (" [снято: " + ", ".join(skipped) + "]") if skipped else ""))
    greens = [x for x in mine if x["ok"] and not x.get("unknown")]
    unks = [x for x in mine if x.get("unknown")]
    # «Неизвестно» гасит кейс ЦЕЛИКОМ и мимо большинства: судить нечем — значит не зачтено.
    case_ok = len(greens) >= need and not unks
    forgiven = []
    for res in mine:
        r = res["run"]
        if res.get("unknown"):
            unknown.append(f"{cid}/{r} {res['unknown']}")
            continue
        bad = [c["name"] for c in res["checks"] if not c["ok"] and not c.get("skipped")]
        if case_ok and bad:
            # МЕНЬШИНСТВО у зачтённого кейса: круг остаётся в отчёте красным и с черновиком,
            # но вердикту не идёт ни зелёным, ни красным — как круг «неизвестно».
            res["tolerated"] = ("прощён критерием %s: класс «%s», зелёных кругов %d из %d "
                                "(нужно %d)" % (CRITERION, klass, len(greens), total, need))
            forgiven.append("%s/%s %s" % (cid, r, ", ".join(bad)))
            log("  [ПРОЩЁН] кейс %s прогон %d/%d — %s" % (cid, r, total, res["tolerated"]))
            continue
        for c in res["checks"]:
            if c.get("skipped"):
                continue                           # снят с причиной — в счёт не идёт (виден в отчёте)
            checks_all += 1
            checks_ok += 1 if c["ok"] else 0
            if not c["ok"]:
                failed.append(f"{cid}/{r} {c['name']}")
    return {"id": str(cid), "results": mine,
            "plan": {"class": klass, "rounds": total, "need": need, "green": len(greens),
                     "red": len(mine) - len(greens) - len(unks), "unknown": len(unks),
                     "ok": bool(case_ok), "tolerated": forgiven},
            "checks_ok": checks_ok, "checks_all": checks_all,
            "failed": failed, "unknown": unknown}


def run_corpus(cases, runs=2, ph=None, log=print, point=None):
    """Корпус × круги → (результаты, passed_cases, checks_passed, checks_total, failed, unknown,
    plan).

    СКОЛЬКО КРУГОВ у кейса и СКОЛЬКО ЗЕЛЁНЫХ ему нужно, решает КРИТЕРИЙ F (узел «КРИТЕРИЙ
    НАБОРА» выше) — и то и другое живёт в `run_one_case`, здесь идёт только СБОРКА набора.

    `point` — контрольная точка (`open_point`) либо None. С точкой каждый ЗАКРЫТЫЙ кейс тут же
    ложится на диск, а кейс, уже лежащий в точке ГОДНОЙ записью, НЕ ГОНЯЕТСЯ ВОВСЕ: его круги
    берутся как есть. Отказ точки в записи прогон НЕ РОНЯЕТ (замер дороже точки), но называется
    вслух — иначе следующий заход молча начал бы набор сначала.

    `plan` — dict id → {class, rounds, need, green, red, unknown, ok, tolerated}: из него вердикт
    берёт число кругов КАЖДОГО кейса и список прощённых кругов, чтобы читатель вердикта видел
    правило, не открывая код."""
    ph = ph or placeholders()
    results, failed, unknown, plan = [], [], [], {}
    passed = checks_ok = checks_all = 0
    for case in cases:
        cid = case.get("id")
        rec, why = point.get(case, runs) if point is not None else (None, "")
        if why:
            log("  [ТОЧКА ОТКАЗ] кейс %s: %s — кейс гоняется ЗАНОВО" % (cid, why))
        if rec is None:
            rec = run_one_case(case, runs, ph, log=log)
            if point is not None:
                point.live.append(str(cid))
                point.live_rounds += int(rec["plan"]["rounds"])
                wok, where = point.put(rec)
                log("  [ТОЧКА] кейс %s закрыт и записан → %s" % (cid, where) if wok else
                    "  [ТОЧКА НЕ ЗАПИСАНА] кейс %s: %s — при обрыве он будет гоняться заново"
                    % (cid, where))
        else:
            p = rec["plan"]
            point.taken.append(str(cid))
            point.taken_rounds += int(p["rounds"])
            log("  [ИЗ ТОЧКИ] кейс %s «%s» — %d кругов НЕ гонялись: %s (зелёных %d из %d, нужно "
                "%d, неизвестно %d)"
                % (cid, case.get("name"), p["rounds"], "ЗАЧТЁН" if p["ok"] else "НЕ зачтён",
                   p["green"], p["rounds"], p["need"], p["unknown"]))
        results.extend(rec["results"])
        plan[str(cid)] = rec["plan"]
        checks_ok += int(rec["checks_ok"])
        checks_all += int(rec["checks_all"])
        failed.extend(rec["failed"])
        unknown.extend(rec["unknown"])
        passed += 1 if rec["plan"]["ok"] else 0
    return results, passed, checks_ok, checks_all, failed, unknown, plan


# ─────────────────────────────────────── вердикт ─────────────────────────────────────────────

def build_verdict(commit, cases_total, passed, checks_ok, checks_all, runs, clean, failed,
                  sha, now=None, unknown=None, bind=None, plan=None, point=None):
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
    вершиной физически не собирается — ни из CLI, ни из чужого раннера замера.

    `plan` — разбор кругов по кейсам от `run_corpus`. Из него в запись ложатся ЧЕТЫРЕ поля,
    которых до 07.09.2026 не было ни одного: `criterion` (каким ПРАВИЛОМ получен исход),
    `runs_by_case`/`runs_line` (сколько кругов вышло у КАЖДОГО кейса) и `tolerated`/
    `tolerated_why` (какие красные круги прощены большинством и за что). Без них читатель
    вердикта не отличает «зелёный, потому что всё зелено» от «зелёный, потому что красный круг
    оказался меньшинством», и обязан лезть в код за правилом — ровно то, что чинится.
    Поле `runs` остаётся БАЗОВЫМ числом кругов (его и сверяют ворота с `TRAINER_MIN_RUNS`);
    сколько кругов вышло на самом деле, говорят `runs_by_case` и `runs_max` — два разных факта,
    и сводить их в одно число значило бы соврать одному из читателей.

    `point` — сводка контрольной точки (`point_info`). Из неё в запись ложатся ЧЕТЫРЕ поля,
    которых до 07.09.2026 не было ни одного: `resumed`/`resumed_why` — сколько кейсов ВЗЯТО ИЗ
    ТОЧКИ (в этом заходе их не гоняли) и какие именно, `resumed_rounds` — сколько кругов за ними
    стои́т, `point_reset` — почему набор пришлось начать заново. ЗЕЛЁНОГО ЭТИ ПОЛЯ НЕ МЕНЯЮТ и
    менять не должны: точка привязана к основанию ЦЕЛИКОМ (коммит, корпус, круги, критерий,
    правки дерева, подстановки), и набор, собранный за два захода на одном основании, — это тот
    же набор, а не склейка двух. Но читатель вердикта обязан ВИДЕТЬ, что он собран возобновлением,
    а не восстанавливать это чтением логов."""
    now = time.time() if now is None else now
    unknown = list(unknown or [])
    bind = bind or {}
    plan = plan or {}
    point = point or {}
    taken = [str(x) for x in (point.get("taken") or [])]
    by_case = {str(k): int((v or {}).get("rounds") or 0) for k, v in plan.items()}
    tolerated = [t for _k, v in sorted(plan.items(), key=lambda kv: _num_key(kv[0]))
                 for t in ((v or {}).get("tolerated") or [])]
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
        "criterion": criterion_text(runs), "criterion_id": CRITERION,
        "runs_by_case": by_case, "runs_line": rounds_line(by_case),
        "runs_max": max(by_case.values()) if by_case else int(runs),
        "tolerated": len(tolerated), "tolerated_why": tolerated[:40],
        "resumed": len(taken), "resumed_why": taken[:40],
        "resumed_rounds": int(point.get("taken_rounds") or 0),
        "point_reset": str(point.get("reset") or ""),
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
                if rec.get("unknown") else "")
             + (f" · **прощено большинством:** {rec.get('tolerated')} кругов"
                if rec.get("tolerated") else ""), "",
             f"**Критерий:** {rec.get('criterion') or '—'}", "",
             f"**Кругов по кейсам:** `{rec.get('runs_line') or '—'}`", "",
             ("**Контрольная точка:** взято из неё кейсов %d (кругов %d — в этом заходе они НЕ "
              "гонялись): %s" % (rec.get("resumed") or 0, rec.get("resumed_rounds") or 0,
                                 ", ".join(rec.get("resumed_why") or []) or "—")
              if rec.get("resumed") else "**Контрольная точка:** не использована — весь набор "
              "прогнан в один заход"), "",
             *([f"**ТОЧКА СБРОШЕНА (набор начат заново):** {rec['point_reset']}", ""]
               if rec.get("point_reset") else []),
             "| # | кейс | прогон | итог | чеки | провалено |", "|---|---|---|---|---|---|"]
    for r in results:
        live = [c for c in r["checks"] if not c.get("skipped")]
        bad = [c["name"] for c in live if not c["ok"]]
        if r.get("tolerated"):
            bad = bad + ["**" + r["tolerated"] + "**"]
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

def build_parser():
    """Разбор argv ОТДЕЛЬНОЙ функцией — чтобы решение «пишем ли реестр» проверялось голденом, а не
    чтением глазами: до 05.09.2026 оно жило внутри `main`, куда тесту не дойти без живого прогона."""
    ap = argparse.ArgumentParser(description="безголовый прогон тренажёра → вердикт для ворот")
    ap.add_argument("--cases", default=CASES_FILE)
    ap.add_argument("--runs", type=int, default=client_contour.TRAINER_MIN_RUNS)
    ap.add_argument("--only", default="", help="через запятую: id кейсов (разведка)")
    ap.add_argument("--out", default=VERDICT_FILE)
    ap.add_argument("--report", default="")
    # РАЗВЕДЕНИЕ ПРОГОНОВ (05.09.2026): безопасен ТОТ, который зовут чаще. Подробности и цифры —
    # в шапке модуля, раздел «ЗАПИСЬ В РЕЕСТР — ТОЛЬКО ПО ЯВНОМУ КЛЮЧУ».
    ap.add_argument("--write", action="store_true",
                    help="ЗАПИСАТЬ вердикт в реестр ворот = ВЫКАТКА на живых ботов при зелёном "
                         "(и снос прежней зелени коммита при любом другом исходе)")
    ap.add_argument("--no-write", action="store_true",
                    help="явный замер без записи; сегодня это и есть умолчание, а вместе с "
                         "--write побеждает этот ключ (fail-closed)")
    ap.add_argument("--drafts", action="store_true", help="печатать черновики целиком")
    # КОНТРОЛЬНАЯ ТОЧКА (07.09.2026). Ключ НЕ трогает реестр и НЕ подразумевает `--write`:
    # точка — это память о ЗАКРЫТЫХ кейсах, а не вердикт. Подробности — шапка модуля.
    ap.add_argument("--state", default="",
                    help="файл КОНТРОЛЬНОЙ ТОЧКИ: закрытый кейс ложится в него сразу, и на "
                         "следующем заходе с тем же ОСНОВАНИЕМ (коммит, корпус, круги, критерий, "
                         "правки дерева, подстановки) он не гоняется заново; расхождение "
                         "основания начинает набор ЗАНОВО и говорит об этом вслух")
    return ap


def writes_registry(a):
    """Тронет ли ЭТОТ прогон реестр вердиктов. → bool.

    ОДНО место, где живёт правило, — и `main`, и голден спрашивают его, а не повторяют условие
    каждый своими словами (два экземпляра одного условия расходятся молча). Умолчание — НЕТ:
    прогон-замер зовут постоянно, а запись есть акт выкатки. `--no-write` сильнее `--write`:
    два ключа разом — противоречие в команде, и разрешается оно в сторону «не трогать»."""
    return bool(getattr(a, "write", False)) and not bool(getattr(a, "no_write", False))


def main(argv=None):
    a = build_parser().parse_args(argv)

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

    plan_rounds = sum(rounds_for(c, a.runs) for c in cases)
    in_class = [str(c.get("id")) for c in cases if case_class(c)]
    print(f"ПРОГОН ТРЕНАЖЁРА: коммит {commit[:7]} ЗАФИКСИРОВАН на входе, кейсов {len(cases)} из "
          f"{total}, прогонов {a.runs}, дерево "
          f"{'чистое' if clean else 'ГРЯЗНОЕ: ' + ', '.join(dirty or ['? git не ответил'])}")
    print("КРИТЕРИЙ %s: кейсов класса «%s» — %d (%s), им по %d круга с зачётом ≥%d из %d; "
          "остальным по %d. Кейс-кругов всего: %d"
          % (CRITERION, CLASS_NAME, len(in_class), ", ".join(in_class) or "нет", CLASS_RUNS,
             need_green(CLASS_RUNS, CLASS_NAME), CLASS_RUNS, a.runs, plan_rounds))
    ph = placeholders()
    # КОНТРОЛЬНАЯ ТОЧКА. Основание собирается ЗДЕСЬ и целиком — до первого круга, чтобы сброс был
    # объявлен ДО того, как заход потратит хоть одну минуту головы.
    point = None
    if a.state:
        basis = point_basis(commit, sha, a.runs, ph, corpus=os.path.basename(a.cases))
        point = open_point(a.state, basis)
        if point.reset:
            print("ТОЧКА СБРОШЕНА — НАБОР НАЧАТ ЗАНОВО: " + point.reset)
        print("КОНТРОЛЬНАЯ ТОЧКА: %s · основание %s (коммит %s, корпус %s, кругов %d, критерий %s, "
              "правки дерева %s, подстановки %s) · закрытых кейсов в ней %d: %s"
              % (a.state, point.key, commit[:7], basis["corpus_sha"], basis["runs"],
                 basis["criterion_id"], basis["tree"], basis["ph"], len(point.done),
                 ", ".join(sorted(point.done, key=_num_key)) or "нет"))
    results, passed, ok, allc, failed, unknown, plan = run_corpus(cases, runs=a.runs, ph=ph,
                                                                  point=point)

    verify_head(bind)                        # вершина на выходе: сдвиг НАЗЫВАЕТСЯ, а не глотается
    pinfo = point_info(point)
    rec = build_verdict(commit, total, passed, ok, allc, a.runs, clean, failed, sha,
                        unknown=unknown, bind=bind, plan=plan, point=pinfo)
    print("\nИТОГ: %s — кейсов %d/%d, чеков %d/%d, прогонов %d, дерево %s%s"
          % (rec["result"].upper(), rec["cases"], rec["cases_total"], rec["checks_passed"],
             rec["checks_total"], rec["runs"],
             "чистое" if clean else "ГРЯЗНОЕ (tree_dirty: true)",
             (", НЕИЗВЕСТНО %d кейсо-прогонов" % len(unknown)) if unknown else ""))
    # ПРАВИЛО И ЕГО ПРИМЕНЕНИЕ — двумя строками, чтобы «каким правилом получен этот исход»
    # читалось из вывода прогона, а не восстанавливалось чтением кода.
    print("КРИТЕРИЙ: " + rec["criterion"])
    print("КРУГОВ ПО КЕЙСАМ: " + rec["runs_line"])
    if point is not None:
        # ЧИСЛА ВОЗОБНОВЛЕНИЯ — двумя счётчиками, а не одним: «сколько сэкономлено» и «сколько
        # стоил этот заход» — разные факты, и сводить их в одно число значило бы соврать обоим.
        print("ТОЧКА: взято из неё кейсов %d (кругов %d — голову не звали ни разу) · прогнано "
              "живьём кейсов %d (кругов %d) · точка: %s"
              % (len(point.taken), point.taken_rounds, len(point.live), point.live_rounds,
                 point.path))
    if rec["tolerated"]:
        print("ПРОЩЕНО БОЛЬШИНСТВОМ (в зачёт чеков не идёт): " + "; ".join(rec["tolerated_why"][:12]))
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
    # ПИШЕМ ТОЛЬКО ПО ЯВНОМУ СЛОВУ. `--no-write` сильнее `--write`: два ключа разом — это
    # противоречие в команде, а не голосование, и разрешается оно в сторону «не трогать реестр».
    if not writes_registry(a):
        print("вердикт на диск НЕ записан: это ПРОГОН-ЗАМЕР%s. Записать (и тем ВЫКАТИТЬ на живых "
              "ботов при зелёном) — тот же вызов с --write"
              % (" (--no-write сильнее --write)" if (a.no_write and a.write) else " — умолчание"))
        return 0 if rec["result"] == "green" else 1
    wok, where = write_verdict(rec, a.out)
    print(f"вердикт: {where}" if wok else f"ВЕРДИКТ НЕ ЗАПИСАН: {where}")
    if rec["result"] == "green":
        # ЧТО ИМЕННО ОТКРОЕТСЯ — говорим правду, а не заученную фразу: под заморозкой контура
        # зелёный вердикт ворот НЕ открывает (client_contour.freeze_holds_release, 05.09.2026),
        # и обещание «откроются» звало бы владельца ждать выкатки, которой не будет.
        held, why = client_contour.freeze_holds_release()
        print("ворота клиентского контура НЕ открываются: %s" % why if held else
              "ворота клиентского контура откроются на этот коммит по основанию «trainer»")
        return 0
    print("ворота ДЕРЖАТ коммит: зелёного вердикта нет (нужно «да» владельца)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
