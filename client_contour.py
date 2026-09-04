# -*- coding: utf-8 -*-
"""
client_contour.py — ПРИЗНАК КЛИЕНТСКОГО КОНТУРА (что доезжает до ЖИВОГО клиента) и реестр
ОСНОВАНИЙ пропуска для ворот выкатки. Модуль ЧИСТЫЙ: ни git, ни subprocess, ни сети — только
файлы репозитория. Всё, что дёргает боевое (карточка, рестарт, дифф), живёт в pc_orchestrator.

ЗАЧЕМ (живой класс 28–30.07.2026). Цепи ревизора id=4 (тип ТС) и id=44 (гард приветствий) правили
`suggest.py` и уезжали на боевых ботов АВТОМАТИЧЕСКИ: 30.07 01:48–01:53 авто-реконсайл рестартнул
userbot и moderation_bot на коммит 4528917 за 17 минут ДО того, как владелец успел цепь остановить.
Единственным, что удержало текст от клиента, был SUGGEST_TEST_MODE — везение второго слоя, а не
ворота. Ворота — здесь.

ПРИЗНАК (а не список имён). Клиентский файл — тот, что лежит в ТРАНЗИТИВНОМ import-замыкании
живых клиентских процессов: `userbot_listen.py` и `moderation_bot.py`. Замыкание считается ПО
ФАКТУ с диска (ast, все узлы Import/ImportFrom — включая импорты внутри функций: `userbot_listen`
тянет `moderation_ipc` именно так), а не берётся из хардкод-списка. Почему так:
  • список имён дырявый — НОВЫЙ модуль в него не попадёт, и первая же правка уедет в бой молча.
    Живой пример прямо в репозитории: комментарий к `_FILE_PROCESS_RULES` утверждает, что
    «userbot booking* не импортит», а `userbot_listen.py:46` его импортирует (мост тренажёра
    добавили позже, комментарий устарел). Граф это видит, список — нет;
  • граф самообновляется вместе с кодом: новый модуль становится клиентским В ТОТ ЖЕ МОМЕНТ,
    когда бот его импортирует, без правки ворот.

Замыкание дополнено ДАННЫМИ: не-.py файлы репозитория, чьи имена лежат строковыми литералами
в модулях замыкания (промпты, json-правила). Они меняют поведение клиента вообще без рестарта.

СРЕЗ НА ЧУЖИХ ПРОЦЕССАХ (единственное исключение, и оно принципиальное; ПАРАМЕТР `cut`, дефолт —
`FOREIGN_ENTRIES`). Обход НЕ идёт сквозь входные точки ДРУГИХ живых процессов —
`pc_orchestrator.py` (демон) и `pc_agent.py` (агент): сами
они внутренние, и всё, что висит ТОЛЬКО под ними, внутреннее тоже. Без среза граф схлопывается:
`moderation_core._default_lesson_enqueue` ЛЕНИВО импортирует `pc_orchestrator`, чтобы положить
строку в очередь (`lesson_router._default_lesson_thinker` — так же, ради думателя), и через это
одно ребро в «клиентский контур» въезжает весь демон вместе с гейтом и инфраструктурой. Это
межпроцессная граница-вызов, а не рантайм бота, и владелец прямо держит демона внутренним (он
обязан продолжать выкатываться сам). Список тут — по ПРОЦЕССАМ, а не по файлам: процессов три и
новый заводят осознанно, файлы же появляются каждую неделю — их считает граф.
ОСТАТОК ЧЕСТНО: код, который клиент увидит ЧЕРЕЗ демона (`_thinker_exec` классификатора уроков),
срезом не покрыт. Появится клиентский текст внутри `pc_orchestrator.py` — признак его не поймает.

Срез — ПАРАМЕТР, а не свойство обхода (07.08.2026). У обхода появился второй заказчик: self-update
демона спрашивает «от каких файлов я завишу» (`pc_orchestrator._dep_files`) и зовёт `closure` с
`entries=('pc_orchestrator.py',), cut=()`. Ему срез не нужен и ВРЕДЕН — `pc_agent.py` демон
импортирует лениво, и стухшая копия этого модуля в памяти демона так же реальна, как любая другая.
Срез отвечает на вопрос «что увидит КЛИЕНТ», а не «что импортируется»: это разные вопросы, поэтому
он переехал из тела обхода в аргумент. Дефолт сохранён байт-в-байт — ворота контура не тронуты.

FAIL-CLOSED. Не удалось построить граф (нет входной точки, файл не читается, синтаксис не
парсится, каталог недоступен) → `is_client()` отвечает True на ВСЁ. «Не знаю» — это НЕ «внутренний».

ГРАНИЦЫ ПРИЗНАКА (честно):
  • сверка идёт по basename — одноимённые файлы в разных каталогах неразличимы (в сторону
    fail-closed: лишний файл посчитаем клиентским, пропустить клиентский не можем);
  • динамический импорт по строке (`importlib.import_module(var)`) граф не видит. В замыкании
    таких нет — проверено; появится — правило-класс «мок обязан копировать живой формат» здесь
    звучит как «признак обязан считать по факту», и его придётся расширить;
  • данные ищутся по литералам: имя файла, собранное из кусков в рантайме, не найдётся.

Интерфейс:
    closure(repo, entries, cut) → Closure(files, data, ok, reason)  — замыкание с кэшем по mtime
    is_client(path)            → bool (fail-closed True при ok=False)
    split(paths)               → (клиентские, внутренние)
    mentions(text)             → (клиентский?, что названо, определимо?) — для ворот ВХОДА (ревизор)
    release_reason(commit)     → 'owner' | 'trainer' | None — основание пропуска ворот
    approve(commit)            → записать «да» владельца
    deny(commit)               → записать «нет» владельца (НИЧЕГО не открывает, закрывает повод)
    owner_denied(commit)       → запись отказа на этот коммит | {}
    remember_asked(...)        → запомнить ПОВОД, о котором спросили карточкой
    trainer_verdict(commit)    → (зачтён?, дословная причина) — ВТОРОЕ основание
    trainer_status(commit)     → строка причины для карточки владельцу
    card_text(...)             → дословный текст карточки владельцу
    frozen()                   → контур заморожен? (ЕДИНСТВЕННАЯ ручка тишины отказов)
    refusal_shape(kinds, held) → подпись отказа БЕЗ коммита (кого держим | какие файлы)
    gate_route(kinds, held)    → ('card'|'feed', причина) — АДРЕС отказа, не право на выкатку
"""

import ast
import hashlib
import io
import json
import os
import re
import time
from collections import namedtuple

REPO = os.path.dirname(os.path.abspath(__file__))

# Живые клиентские процессы. ИМЕННО они (а не «всё, что похоже на бота») определяют контур:
# userbot пишет клиенту в личку, moderation_bot несёт модерацию/кнопки над теми же черновиками.
CLIENT_ENTRIES = ("userbot_listen.py", "moderation_bot.py")

# Входные точки ЧУЖИХ живых процессов: обход через них не идёт, сами они внутренние. См. «СРЕЗ НА
# ЧУЖИХ ПРОЦЕССАХ» в шапке. Держать этот кортеж коротким — это перечень ПРОЦЕССОВ, не файлов.
FOREIGN_ENTRIES = ("pc_orchestrator.py", "pc_agent.py")

# Расширения, которые НИКОГДА не приезжают коммитом (runtime-мусор, всё в .gitignore) — в данные
# контура их не берём, иначе `userbot.log` в тексте находки уводил бы разбор в шум.
_RUNTIME_EXT = (".log", ".db", ".session", ".spool", ".marker", ".tmp", ".lock", ".offset",
                ".heartbeat", ".flag")

# Строковый литерал похож на имя файла репозитория: «docs/revizor_checklist.md», «trainer_rules.json».
_RE_DATA_LITERAL = re.compile(r"^[\w][\w./\\-]{1,79}\.[A-Za-z0-9]{1,8}$")
# Имя файла внутри свободного текста (дев-ТЗ ревизора).
_RE_FILE_TOKEN = re.compile(r"[\w][\w./\\-]{0,79}\.[A-Za-z0-9]{1,8}")

# Голое имя модуля без расширения («поправь детект в suggest») ловим только для стемов ДЛИНОЙ ≥4:
# короткие («bot») дают шум на любом английском слове.
_MIN_STEM = 4

Closure = namedtuple("Closure", "files data ok reason")

_cache = {}          # (repo, entries) -> (signature, Closure)


# ------------------------------- построение графа -----------------------------

def _norm(path):
    """basename в нижнем регистре; и '/' и '\\' — путь может приехать из git-диффа или из Windows."""
    return os.path.basename(str(path or "").replace("\\", "/").strip()).lower()


def _signature(repo):
    """Отпечаток каталога для кэша: (имя, mtime_ns, размер) всех .py в корне репо. Любая правка,
    появление или удаление модуля меняет отпечаток → граф пересчитывается. → tuple | None (сбой)."""
    try:
        items = []
        with os.scandir(repo) as it:
            for e in it:
                if e.name.lower().endswith(".py") and e.is_file():
                    st = e.stat()
                    items.append((e.name.lower(), st.st_mtime_ns, st.st_size))
        return tuple(sorted(items))
    except OSError:
        return None


def _module_file(repo, name):
    """Локальный модуль репозитория по имени импорта → путь | None (внешний пакет/стдлиб)."""
    if not name or not re.match(r"^\w+$", name):
        return None
    p = os.path.join(repo, name + ".py")
    if os.path.isfile(p):
        return p
    p = os.path.join(repo, name, "__init__.py")
    if os.path.isfile(p):
        return p
    return None


def _imports_of(tree):
    """Имена импортов ВСЕГО дерева (ast.walk, не только верхний уровень): ленивые импорты внутри
    функций доезжают до клиента ровно так же — `userbot_listen.py:205 import moderation_ipc`."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
            if node.level:                       # from . import X — плоское репо: имя в alias
                for a in node.names:
                    names.add(a.name.split(".")[0])
    return names


def _data_of(tree, repo):
    """Не-.py файлы репозитория, названные строковыми литералами модуля (промпты/правила/фикстуры).
    Такие правки меняют поведение клиента БЕЗ рестарта — контур обязан их видеть."""
    out = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        s = node.value.strip()
        low = s.lower()
        if len(s) > 80 or low.endswith(".py") or not _RE_DATA_LITERAL.match(s):
            continue
        if low.endswith(_RUNTIME_EXT):
            continue
        rel = s.replace("\\", "/")
        try:
            if os.path.isfile(os.path.join(repo, rel)):
                out.add(os.path.basename(rel).lower())
        except OSError:
            continue
    return out


def _build(repo, entries, cut=FOREIGN_ENTRIES):
    """Обход в ширину от входных точек. → Closure. Любой сбой чтения/парса ФАЙЛА ЗАМЫКАНИЯ делает
    результат недостоверным (ok=False) — тогда ворота считают клиентским всё.
    `cut` — входные точки чужих процессов, сквозь которые обход НЕ идёт (см. шапку); `cut=()` даёт
    ЧИСТОЕ import-замыкание, без вопроса «увидит ли это клиент»."""
    files, data, queue, seen = set(), set(), [], set()
    for e in entries:
        p = os.path.join(repo, e)
        if not os.path.isfile(p):
            return Closure(frozenset(), frozenset(), False, f"нет входной точки {e}")
        queue.append(p)
    while queue:
        path = queue.pop()
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        files.add(os.path.basename(path).lower())
        try:
            with io.open(path, encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src, filename=path)
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError) as e:
            return Closure(frozenset(), frozenset(), False,
                           f"{os.path.basename(path)}: {type(e).__name__}: {e}")
        data |= _data_of(tree, repo)
        for name in _imports_of(tree):
            nxt = _module_file(repo, name)
            if nxt and os.path.basename(nxt).lower() not in cut:
                queue.append(nxt)          # срез: в чужой процесс (демон/агент) не заходим
    return Closure(frozenset(files), frozenset(data), True, "ok")


def closure(repo=None, entries=None, cut=None):
    """Транзитивное import-замыкание входных точек (с кэшем по отпечатку каталога). → Closure.
    Кэш нужен потому, что спрашивают часто: реконсиляция детей — каждые 60 с, self-update демона —
    каждый тик поллинга. Ключ кэша включает и `entries`, и `cut`: у двух заказчиков РАЗНЫЕ
    замыкания одного каталога, и путать их нельзя.
    `cut=None` → дефолт `FOREIGN_ENTRIES` (ворота клиентского контура, поведение не менялось)."""
    repo = repo or REPO
    entries = tuple(entries or CLIENT_ENTRIES)
    cut = FOREIGN_ENTRIES if cut is None else tuple(cut)
    sig = _signature(repo)
    if sig is None:
        return Closure(frozenset(), frozenset(), False, f"каталог {repo} не читается")
    ck = (os.path.normcase(os.path.abspath(repo)), entries, cut)
    hit = _cache.get(ck)
    if hit and hit[0] == sig:
        return hit[1]
    val = _build(repo, entries, cut)
    _cache[ck] = (sig, val)
    return val


# ------------------------------- признак -------------------------------------

def is_client(path, repo=None, cl=None):
    """Правка этого файла доедет до ЖИВОГО клиента? FAIL-CLOSED: граф не построился или путь пуст
    → True («не знаю» ≠ «внутренний»)."""
    cl = cl if cl is not None else closure(repo)
    if not cl.ok:
        return True
    base = _norm(path)
    if not base:
        return True
    return base in cl.files or base in cl.data


def split(paths, repo=None, cl=None):
    """Список путей → (клиентские, внутренние) с сохранением порядка. Дубли по basename схлопываем."""
    cl = cl if cl is not None else closure(repo)
    client, internal, seen = [], [], set()
    for p in (paths or []):
        base = _norm(p)
        if base in seen:
            continue
        seen.add(base)
        (client if is_client(p, cl=cl) else internal).append(p)
    return client, internal


def repo_modules(repo=None):
    """Все .py корня репозитория (basename lower) — чтобы отличить «названо ВНУТРЕННЕЕ имя» от
    «имён вообще нет». → frozenset (пусто при сбое чтения каталога → неопределимость)."""
    repo = repo or REPO
    try:
        with os.scandir(repo) as it:
            return frozenset(e.name.lower() for e in it
                             if e.name.lower().endswith(".py") and e.is_file())
    except OSError:
        return frozenset()


def mentions(text, repo=None, cl=None, modules=None):
    """ВОРОТА ВХОДА: дев-ТЗ находки ревизора трогает клиентский контур?
    → (клиентский:bool, названные клиентские файлы:list, определимо:bool)

    Ревизор ставит ТЕКСТ задачи, а не список файлов, — поэтому определяем по именам, названным
    в тексте: и «suggest.py», и голое «suggest». Имён репозитория в тексте нет вообще →
    НЕОПРЕДЕЛИМО → fail-closed «клиентский» (правило 5 задания). Названы только внутренние —
    внутренний, зелёная задача дирижёру как раньше."""
    cl = cl if cl is not None else closure(repo)
    mods = modules if modules is not None else repo_modules(repo)
    t = str(text or "")
    named = set()
    for tok in _RE_FILE_TOKEN.findall(t):
        base = _norm(tok)
        if base in mods or (cl.ok and base in cl.data):
            named.add(base)
    for m in mods:
        stem = m[:-3]
        if len(stem) >= _MIN_STEM and re.search(r"(?<!\w)" + re.escape(stem) + r"(?!\w)", t, re.I):
            named.add(m)
    if not cl.ok:
        return True, sorted(named), False              # граф недостоверен → fail-closed
    if not named:
        return True, [], False                         # ни одного имени → неопределимо → fail-closed
    hits = sorted(n for n in named if is_client(n, cl=cl))
    return bool(hits), hits, True


# ------------------------- основания пропуска ворот ---------------------------
# Ровно ДВА: «да» владельца ИЛИ зелёный прогон через тренажёр. Реестр — на диске (переживает
# рестарт демона), в .gitignore по маске pc_orchestrator.*.json.

RELEASE_FILE = os.path.join(REPO, "pc_orchestrator.client_release.json")
TRAINER_GREEN_FILE = os.path.join(REPO, "pc_orchestrator.client_trainer_green.json")
TRAINER_CASES_FILE = os.path.join(REPO, "trainer_cases.json")
TRAINER_GREEN_ENV = "PC_TRAINER_GREEN"
TRAINER_RUNNER = "trainer_run.py"
# Порог зелёного (артефакт 2026-07-30-trainer-verdict-recon.md, §5): весь корпус и ДВА прогона.
# Меньше — не «почти зелено», а «вердикта нет»: ворота двоичные.
TRAINER_MIN_CASES = 12
TRAINER_MIN_RUNS = 2
_KEEP = 50          # сколько последних решений храним


def short(commit):
    """Нормализованный ключ коммита: первые 7 hex-символов. Разные места дают 7/9/40 символов —
    сверять их «как есть» значило бы терять одобрение владельца на ровном месте. → str ('' если не хеш)."""
    s = str(commit or "").strip().lower()
    m = re.match(r"^([0-9a-f]{7,40})$", s)
    return m.group(1)[:7] if m else ""


def _load(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def approve(commit, who="owner", path=None, now=None):
    """Записать «да» владельца на коммит. → (ok, сообщение). Реестр обрезаем до _KEEP последних."""
    c = short(commit)
    if not c:
        return False, f"не похоже на коммит: {commit!r}"
    path = path or RELEASE_FILE
    d = _load(path)
    ap = d.get("approved") if isinstance(d.get("approved"), dict) else {}
    ap[c] = {"who": who, "ts": time.time() if now is None else now}
    if len(ap) > _KEEP:
        for k, _v in sorted(ap.items(), key=lambda kv: kv[1].get("ts", 0))[:len(ap) - _KEEP]:
            ap.pop(k, None)
    d["approved"] = ap
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=0)
    except OSError as e:
        return False, f"реестр решений не записан: {e}"
    return True, c


def owner_approved(commit, path=None):
    c = short(commit)
    if not c:
        return False
    ap = _load(path or RELEASE_FILE).get("approved")
    return isinstance(ap, dict) and c in ap


# ------------------------- «НЕТ» ВЛАДЕЛЬЦА (класс 05.09.2026) -----------------
# ЗАЧЕМ. У карточки ворот была ОДНА дверь: принимался только ответ «выкати». Владелец, ответивший
# «нет», не получал ничего — ни расписки, ни следа: его решение существовало ровно до конца чата.
# Молчание при этом работает как отказ (ворота fail-closed), но молчание НЕОТЛИЧИМО от «не увидел
# карточку», и через месяц по журналу нельзя сказать, решение это было или недосмотр.
#
# ЧЕГО ОТКАЗ НЕ ДЕЛАЕТ, и это главное: он НИЧЕГО НЕ ОТКРЫВАЕТ и НИЧЕГО НЕ ПРИМЕНЯЕТ. `release_reason`
# сюда не заглядывает ни одной веткой — ворота как держали, так и держат; отказ лишь ЗАКРЫВАЕТ ПОВОД
# (перестаёт спрашивать о том, о чём уже спросили) и оставляет СЛЕД.
#
# ПОЧЕМУ ЗАПИСЬ ЖИВЁТ В ТОМ ЖЕ РЕЕСТРЕ, что и «да» (RELEASE_FILE, ключ `denied` рядом с `approved`):
# это ОДНА развилка и одно решение владельца о ОДНОМ коммите, и разносить два исхода по разным
# файлам значило бы дать им разную судьбу при ротации и разное время жизни. Файл вердикта тренажёра
# (TRAINER_GREEN_FILE) здесь не участвует ВООБЩЕ: состояние ворот отказом не правится.
#
# ПОЧЕМУ ОТКАЗ НЕ ВЕЧЕН — и почему это не мелочь. Заглушить пару «дети + файлы» навсегда значит
# сделать так, что СЛЕДУЮЩИЙ, уже нужный, вопрос о выкатке владелец не увидит никогда. Поэтому
# тишина двухслойная:
#   • ТОТ ЖЕ ПОВОД (тот же коммит + та же форма) — не спрашиваем больше никогда: это дословно тот
#     вопрос, на который ответ уже дан;
#   • ТА ЖЕ ФОРМА на ДРУГОМ коммите — в ленту, но лишь DENY_MUTE_SEC. Сутки выбраны замером полосы:
#     класс 21.08 дал 14 карточек ОДНОЙ формы за сутки, а темп полосы — 4.3 закрытых задачи в сутки,
#     то есть «нет», сказанное вчера, относится к вчерашнему состоянию дерева. Одна карточка на
#     форму в сутки — это не залп, а молчание длиннее суток уже было бы забвением.
DENY_MUTE_SEC = 24 * 3600


def remember_asked(commit, kinds, held, path=None, now=None):
    """Запомнить ПОВОД, о котором владельца СПРОСИЛИ карточкой: коммит + форма отказа.

    Нужен потому, что ответ «нет» приходит ОТДЕЛЬНОЙ задачей и формы в себе не несёт, а HEAD к тому
    времени мог уехать. Память процесса тут не годится: демон перезапускается self-update'ом по
    нескольку раз в сутки. → (ok, причина сбоя). Не записали — не страшно: отказ тогда закроет
    повод по HEAD, то есть у́же, а не шире (fail-closed в сторону «спросим снова»)."""
    path = path or RELEASE_FILE
    d = _load(path)
    d["asked"] = {"commit": short(commit) or str(commit or ""),
                  "shape": refusal_shape(kinds, held),
                  "ts": time.time() if now is None else now}
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=0)
    except OSError as e:
        return False, f"повод не запомнен: {e}"
    return True, d["asked"]["commit"]


def asked(path=None):
    """Открытый повод (последняя отправленная карточка ворот) → dict | {}."""
    a = _load(path or RELEASE_FILE).get("asked")
    return a if isinstance(a, dict) else {}


def deny(commit, who="owner", path=None, now=None, kinds=None, held=None):
    """Записать «НЕТ» владельца на коммит. → (ok, запись | причина).

    Ворота не трогает НИ ОДНОЙ веткой: это расписка о решении, а не основание. Коммит и форму
    берём из открытого повода (о нём и спрашивали), а `commit` — фолбэк на случай, когда повода в
    реестре нет."""
    path = path or RELEASE_FILE
    d = _load(path)
    a = d.get("asked") if isinstance(d.get("asked"), dict) else {}
    c = short(a.get("commit")) or short(commit)
    if not c:
        return False, f"не похоже на коммит: {commit!r}"
    shape = str(a.get("shape") or "") if short(a.get("commit")) == c else ""
    if not shape and (kinds or held):
        shape = refusal_shape(kinds, held)
    rec = {"who": who, "ts": time.time() if now is None else now, "shape": shape}
    dn = d.get("denied") if isinstance(d.get("denied"), dict) else {}
    dn[c] = rec
    if len(dn) > _KEEP:
        for k, _v in sorted(dn.items(), key=lambda kv: kv[1].get("ts", 0))[:len(dn) - _KEEP]:
            dn.pop(k, None)
    d["denied"] = dn
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=0)
    except OSError as e:
        return False, f"реестр решений не записан: {e}"
    out = dict(rec)
    out["commit"] = c
    return True, out


def owner_denied(commit, path=None):
    """Запись отказа владельца на этот коммит → dict | {}. Права выкатить НЕ даёт и не отнимает."""
    c = short(commit)
    if not c:
        return {}
    dn = _load(path or RELEASE_FILE).get("denied")
    rec = dn.get(c) if isinstance(dn, dict) else None
    return rec if isinstance(rec, dict) else {}


def deny_route(commit, kinds, held, path=None, now=None):
    """Отказ владельца уже закрыл этот повод? → ('feed', причина) | None (спрашивать можно).

    None — это «отказ ничего не говорит про этот случай», а не «выкатывай». Нечитаемый реестр даёт
    ровно None: молчать по недосмотру хуже лишней карточки (тот же fail-loud, что у `gate_route`)."""
    c = short(commit)
    if not c:
        return None
    dn = _load(path or RELEASE_FILE).get("denied")
    if not isinstance(dn, dict) or not dn:
        return None
    shape = refusal_shape(kinds, held)
    ts = time.time() if now is None else now
    rec = dn.get(c)
    if isinstance(rec, dict) and str(rec.get("shape") or shape) == shape:
        return ROUTE_FEED, ("владелец сказал «нет» на этот повод (коммит %s, форма «%s») — "
                            "повторно не спрашиваю" % (c, shape))
    same = [(k, r) for k, r in dn.items()
            if isinstance(r, dict) and str(r.get("shape") or "") == shape and shape]
    if same:
        k, r = max(same, key=lambda kr: kr[1].get("ts", 0))
        age = ts - float(r.get("ts") or 0)
        if 0 <= age < DENY_MUTE_SEC:
            return ROUTE_FEED, ("владелец сказал «нет» на ту же форму «%s» (коммит %s, %d мин "
                                "назад) — в ленту; спрошу снова через %d ч или на другой форме"
                                % (shape, k, int(age // 60), int((DENY_MUTE_SEC - age) // 3600) + 1))
    return None


def corpus_sha(path=None):
    """Отпечаток корпуса кейсов (sha256, 16 hex) → '' при нечитаемом файле. Вердикт обязан назвать
    ТОТ корпус, что лежит на диске: зелень, снятая на урезанном наборе кейсов, не основание."""
    try:
        with io.open(path or TRAINER_CASES_FILE, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


def _verdict_holds(rec, c, commit_raw, cases_path=None):
    """ДЕРЖИТСЯ ЛИ ОДНА ЗАПИСЬ вердикта целиком. → (держится?, дословная причина).

    Правила ровно те же, что стояли в теле `trainer_verdict`, — ни одно не ослаблено; вынесены
    сюда, чтобы тем же судом можно было спросить запись про ЕЁ СОБСТВЕННЫЙ коммит. Без этого
    карточка не отличает «вердикт есть, но на другом коммите» от «вердикта нет ни на одном»:
    живой класс 05.09.2026 — единственная зелёная запись (74be777, корпус 12 кейсов) не открывает
    даже сам 74be777, потому что на диске лежит корпус на 16, а карточка звала её «последним
    зелёным»."""
    if not isinstance(rec, dict):
        return False, "запись битая (не объект)"
    if str(rec.get("result") or "") != "green":
        return False, f"запись не зелёная: result={rec.get('result')!r}"
    rc = str(rec.get("commit") or "").strip().lower()
    cc = str(commit_raw or "").strip().lower()
    if short(rc) != c or (len(rc) == 40 and len(cc) == 40 and rc != cc):
        return False, f"вердикт снят на ДРУГОМ коммите ({short(rc) or '?'}), а выкатывается {c}"
    total, ok = rec.get("checks_total"), rec.get("checks_passed")
    if not isinstance(total, int) or not isinstance(ok, int) or total <= 0 or ok != total:
        return False, f"чеки не все зелёные: {ok}/{total}"
    cases, cases_total = rec.get("cases"), rec.get("cases_total")
    if not isinstance(cases, int) or cases < TRAINER_MIN_CASES or cases != cases_total:
        return False, f"кейсов {cases}/{cases_total}, нужно {TRAINER_MIN_CASES} из {TRAINER_MIN_CASES}"
    runs = rec.get("runs")
    if not isinstance(runs, int) or runs < TRAINER_MIN_RUNS:
        return False, f"прогонов {runs}, нужно {TRAINER_MIN_RUNS}"
    if rec.get("clean") is not True:
        return False, "прогон шёл по ГРЯЗНОМУ дереву — вердикт не о коммите"
    sha = corpus_sha(cases_path)
    if not sha or str(rec.get("corpus_sha") or "") != sha:
        return False, "корпус кейсов не тот, на котором снят вердикт"
    return True, ("зелёный: кейсов %s/%s, чеков %s/%s, прогонов %s, коммит совпал (%s)"
                  % (cases, cases_total, ok, total, runs, rec.get("when") or "?"))


def _green_elsewhere(g, cases_path=None):
    """Записи ящика `green`, разложенные СУДОМ, а не наличием: (держатся, не держатся).

    Каждый элемент — (ключ, дословная причина). «Держится» значит: эта запись открыла бы ворота
    СВОЕМУ коммиту, спроси мы её про него. Наличие ключа в ящике не значит ничего: ящик пишет
    раннер, а судит `_verdict_holds`."""
    live, dead = [], []
    for k in sorted(g or {}):
        rec = g.get(k)
        held, why = _verdict_holds(rec, short(k) or str(k),
                                   rec.get("commit") if isinstance(rec, dict) else None,
                                   cases_path)
        (live if held else dead).append((str(k), why))
    return live, dead


def trainer_verdict(commit, path=None, env=None, cases_path=None):
    """ВТОРОЕ основание — ЗЕЛЁНЫЙ ПРОГОН ЧЕРЕЗ ТРЕНАЖЁР. → (зачтён?, дословная причина).

    Канал ровно тот, что был заложен интерфейсом (a8f8822): безголовый раннер `trainer_run.py`
    кладёт в TRAINER_GREEN_FILE {"green": {"<commit7>": {…}}}. Ворота файлу НЕ ВЕРЯТ НА СЛОВО —
    кто может писать этот файл, тот открывает клиентский контур в обход «да» владельца, поэтому
    запись обязана доказать себя ЦЕЛИКОМ (артефакт 2026-07-30-trainer-verdict-recon.md, §5):
      • result == 'green' и ВСЕ чеки зелёные (checks_passed == checks_total > 0);
      • кейсов не меньше TRAINER_MIN_CASES, прогонов не меньше TRAINER_MIN_RUNS;
      • дерево на прогоне было ЧИСТОЕ (иначе вердикт удостоверяет не коммит, а чей-то WIP);
      • корпус тот же, что на диске (corpus_sha);
      • коммит ТОТ ЖЕ: не только ключ-семёрка, но и поле commit — вердикт, снятый на другом HEAD,
        не засчитывается (полные хеши сверяем целиком, а не по префиксу).
    Рубильник PC_TRAINER_GREEN=0/off ГАСИТ основание целиком (аварийный возврат к «только да»).
    Любое «не знаю» (нет файла, битая запись, нечитаемый корпус) → False: fail-closed."""
    if not trainer_enabled(env):
        return False, f"выключен рубильником {TRAINER_GREEN_ENV}=0"
    c = short(commit)
    if not c:
        return False, f"не похоже на коммит: {commit!r}"
    d = _load(path or TRAINER_GREEN_FILE)
    g = d.get("green") if isinstance(d.get("green"), dict) else {}
    red = d.get("red") if isinstance(d.get("red"), dict) else {}
    rec = g.get(c)
    if not isinstance(rec, dict):
        r = red.get(c)
        if isinstance(r, dict):
            # Ящик один (fail-closed), но СЛОВО берём из самой записи: с 22.08.2026 прогон умеет
            # третий исход — «неизвестно» (молчащая голова, судить нечего). Назвать его КРАСНЫМ
            # значило бы обвинить код в том, чего прибор не измерил.
            return False, ("вердикт %s: чеков %s/%s, кейсов %s/%s (прогон %s)"
                           % ({"unknown": "НЕИЗВЕСТНО"}.get(str(r.get("result") or ""), "КРАСНЫЙ"),
                              r.get("checks_passed"), r.get("checks_total"), r.get("cases"),
                              r.get("cases_total"), r.get("when") or "?"))
        # ТРИ СОСТОЯНИЯ, а не два (05.09.2026). Прежняя строка звала «последним зелёным» ЛЮБОЙ
        # ключ ящика — то есть верила файлу на слово ровно там, где сам вердикт файлу не верит.
        # Живой замер: единственная запись 74be777 (12 кейсов, corpus_sha 98ad5e3e…) не открывает
        # и свой коммит — на диске корпус на 16 (6d5d78f0…). Владелец читал «зелень есть, но не на
        # этом коммите», а правды «зелени нет нигде» не видел ни строкой.
        live, dead = _green_elsewhere(g, cases_path)
        if live:
            return False, ("вердикта на этот коммит нет; ДЕЙСТВУЮЩИЙ зелёный — на %s"
                           % ", ".join(k for k, _w in live[:3]))
        if dead:
            k, why = dead[0]
            return False, ("зелёного вердикта нет НИ НА ОДНОМ коммите: запись на %s есть, но она "
                           "не открывает и его (%s)" % (k, why))
        return False, "прогона не было"
    return _verdict_holds(rec, c, commit, cases_path)


def trainer_enabled(env=None):
    """Рубильник ВТОРОГО основания. По умолчанию ВКЛЮЧЕНО: вердикт себя доказывает сам (см.
    trainer_verdict), и держать его выключенным значило бы оставить владельца единственным ключом.
    PC_TRAINER_GREEN=0/false/no/off — аварийно погасить основание целиком, не трогая раннер."""
    env = os.environ if env is None else env
    return str(env.get(TRAINER_GREEN_ENV, "") or "").strip().lower() not in ("0", "false", "no", "off")


def trainer_status(commit, path=None, env=None, cases_path=None):
    """Дословная причина «почему тренажёр (не) открыл ворота» — для карточки владельцу."""
    return trainer_verdict(commit, path, env, cases_path)[1]


def trainer_green(commit, path=None, env=None):
    """Зелёный прогон тренажёра на ЭТОТ коммит? (двоичный ответ для release_reason)."""
    return trainer_verdict(commit, path, env)[0]


def release_reason(commit, kind=None, path=None, trainer_path=None, env=None):
    """Основание пропуска ворот для коммита. → 'owner' | 'trainer' | None (оснований нет — держим).
    kind принимается для симметрии вызовов; решение принимается ПО КОММИТУ: рестарт применяет
    состояние диска целиком, «частично выкатить» нельзя, значит и одобрять надо коммит."""
    if owner_approved(commit, path):
        return "owner"
    if trainer_green(commit, trainer_path, env):
        return "trainer"
    return None


# ------------- ЗАМОРОЗКА КОНТУРА: повтор отказа — В ЛЕНТУ, а не карточкой -----
# ЗАЧЕМ (живой класс 21.08.2026, замер по pc_orchestrator.log). Клиентский контур заморожен: ни
# один коммит на живых ботов не едет, ворота держат каждый. Но ДЕРЖАТЬ и СПРАШИВАТЬ — разные
# действия, и второе за 21.08 отработало вхолостую 14 раз. Все 14 отказов — про ОДНО И ТО ЖЕ:
# «userbot,moderbot» × «price_gate.py, price_source.py, suggest.py», состав не дрогнул ни разу.
# Почему их столько: подпись дедупа ворот включает КОММИТ, а метка `_last_child_commit` при отказе
# СОЗНАТЕЛЬНО не двигается (иначе «да» владельца применило бы уже другой коммит) — значит каждый
# новый коммит даёт новый ключ на НЕИЗМЕННОМ диффе. И все 14 коммитов трогали ТОЛЬКО `docs/`
# (артефакты и вынесенные тела журнальных записей) — тексты, которые поведение бота изменить не
# могут в принципе: клиентские файлы в карточке пришли из накопленного хвоста, а не из коммита.
#
# ЗАБОР ЧЕСТЕРТОНА (зачем каждая остановка спрашивала владельца). Ворота открывают РОВНО ДВА
# основания, и оба — живое решение: «да» владельца и зелёный тренажёр. Остановка, которой никто не
# увидел, — это выкатка, которая молча не состоится; карточка была единственным местом, где
# задержанный коммит становился ВИДИМЫМ немедленно. Уводя повтор в ленту, мы теряем именно
# НЕМЕДЛЕННОСТЬ: владелец узнаёт о задержанном коммите не пушем, а чтением ленты, и «да» на него
# приходит позже. Под заморозкой эта цена оплачена решением — выкатки всё равно нет; после
# разморозки цена вернётся, поэтому ручка ОДНА и снимается одним движением.
#
# ЧТО ИМЕННО ГЛУШИТСЯ: ПОВТОР отказа ТОЙ ЖЕ ФОРМЫ. Форма = кого держим + какие клиентские файлы;
# КОММИТ в неё не входит СОЗНАТЕЛЬНО — именно он и размножал карточки. Первый отказ КАЖДОЙ формы
# остаётся карточкой даже под заморозкой: «отказ по причине, которой раньше не было» — новость, а
# не шум. Право на выкатку эта функция не трогает НИ ОДНОЙ веткой: ворота держат в обоих исходах,
# здесь решается только АДРЕС сообщения.
#
# ПОЧЕМУ РЕЕСТР НА ДИСКЕ, А НЕ В ПАМЯТИ: демон перезапускается self-update'ом по нескольку раз в
# сутки, и память процесса вернула бы ровно тот же залп. ПОЧЕМУ У ЗАПИСИ ЕСТЬ ЭПИЗОД (mtime флага):
# заморозка — состояние временное, и «мы про эту форму уже говорили» верно ВНУТРИ одного эпизода;
# следующая заморозка обязана начать разговор заново, иначе тишина становится вечной по недосмотру.
# Не смогли запомнить → следующий отказ снова ГРОМКИЙ (fail-loud: молчание по недосмотру хуже
# лишней карточки).

FREEZE_FLAG = os.path.join(REPO, "pc_orchestrator.contour_frozen")
GATE_SEEN_FILE = os.path.join(REPO, "pc_orchestrator.gate_seen.json")
SEEN_KEEP = 50
ROUTE_CARD, ROUTE_FEED = "card", "feed"


def frozen(flag=None):
    """ЕДИНСТВЕННАЯ ручка тишины отказов: ФАЙЛ `pc_orchestrator.contour_frozen` в корне репо.

    Почему файл, а не переменная окружения: заморозка — СОСТОЯНИЕ МИРА, а не конфигурация сборки.
    Файл видно в `ls`, он читается каждым тиком (демон перезапускать не нужно), а разморозка — одно
    движение: удалить файл, и карточки возвращаются тем же тиком. Ручек ровно одна: нет файла →
    прежнее поведение байт-в-байт."""
    return os.path.exists(flag or FREEZE_FLAG)


EPISODE_UNKNOWN = -1     # «эпизод назвать не смог» — НЕ «нулевой»: сравнение с ним всегда даёт «новый»


def freeze_episode(flag=None):
    """Отпечаток ТЕКУЩЕЙ заморозки (mtime флага, целые секунды). → int | EPISODE_UNKNOWN.

    Возврат при сбое — НЕ ноль: ноль неотличим от записанного нуля, и одна нечитаемая mtime сделала
    бы тишину вечной. `-1` не совпадёт ни с одним записанным эпизодом → отказ снова громкий."""
    try:
        return int(os.path.getmtime(flag or FREEZE_FLAG))
    except OSError:
        return EPISODE_UNKNOWN


def _names(items):
    """Перечень имён → множество непустых строк. Не перечень → пусто СОЗНАТЕЛЬНО: подпись отказа
    обязана получиться ВСЕГДА, исключение здесь погасило бы саму запись об отказе."""
    if not isinstance(items, (list, tuple, set, frozenset)):
        return set()
    return {str(x).strip() for x in items if str(x).strip()}


def refusal_shape(kinds, held):
    """Подпись ОТКАЗА: «кого держим|какие клиентские файлы». Ни коммита, ни даты, ни `where` —
    все три меняются на НЕИЗМЕННОМ отказе и ровно этим размножали карточки (14 из 14 за 21.08)."""
    k = ",".join(sorted(_names(kinds))) or "боты"
    return k + "|" + ",".join(sorted(_names(held)))


def _seen_load(path=None):
    d = _load(path or GATE_SEEN_FILE)
    s = d.get("shapes")
    return s if isinstance(s, dict) else {}


def _seen_save(shapes, path=None):
    """Реестр форм на диск, хвост обрезаем до SEEN_KEEP. → (True | None, дословная причина сбоя).

    Причину возвращаем, а не глотаем: она уезжает В САМ отказ («реестр НЕ записан»), и владелец
    видит, ПОЧЕМУ карточка пришла на форму, о которой ему уже говорили. Первый член на сбое —
    `None`, а не `False`: «записать не смог» это НЕ знание, и наверх должно доехать «не знаю»."""
    try:
        items = sorted(shapes.items(), key=lambda kv: kv[1].get("last", 0))[-SEEN_KEEP:]
        with io.open(path or GATE_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"shapes": dict(items)}, f, ensure_ascii=False, indent=0)
        return True, ""
    except (OSError, TypeError, ValueError) as e:
        return None, "%s: %s" % (type(e).__name__, e)


def gate_route(kinds, held, flag=None, path=None, now=None, episode=None, commit=None,
               release_path=None):
    """АДРЕС отказа ворот: ('card'|'feed', дословная причина).

    'card' — карточка владельцу, как было. 'feed' — только строка в ленту (журнал). На САМ отказ
    не влияет ничем: ворота fail-closed и держат в обоих исходах. Карточкой остаются: любой отказ
    при снятой заморозке, ПЕРВЫЙ отказ каждой формы в эпизоде заморозки и любой отказ, который мы
    не смогли записать в реестр.

    ОТКАЗ ВЛАДЕЛЬЦА спрашивается ПЕРВЫМ и НЕ зависит от заморозки: «нет» — это ответ на конкретный
    вопрос, и он закрывает повод независимо от того, лежит ли флаг заморозки. Право выкатить он не
    меняет: обе ветки возвращают адрес, а не разрешение."""
    d = deny_route(commit, kinds, held, release_path, now)
    if d:
        return d
    if not frozen(flag):
        return ROUTE_CARD, "заморозки нет (%s отсутствует) — прежнее поведение" % os.path.basename(
            flag or FREEZE_FLAG)
    shape = refusal_shape(kinds, held)
    ep = freeze_episode(flag) if episode is None else episode
    ts = time.time() if now is None else now
    shapes = _seen_load(path)
    rec = shapes.get(shape)
    prev_ep = rec.get("ep") if isinstance(rec, dict) else None
    if not isinstance(prev_ep, int) or prev_ep != int(ep):     # форма новая ИЛИ заморозка другая
        shapes[shape] = {"ep": int(ep), "first": ts, "last": ts, "n": 1}
        saved, err = _seen_save(shapes, path)
        why = "отказ ФОРМЫ, которой в эту заморозку ещё не было: %s" % shape
        return ROUTE_CARD, why if saved else (why + " (реестр форм НЕ записан [%s] — следующий "
                                              "тоже будет громким)" % err)
    n = rec.get("n")
    rec["n"] = (n + 1) if isinstance(n, int) else 1
    rec["last"] = ts
    shapes[shape] = rec
    saved, err = _seen_save(shapes, path)
    if not saved:
        return ROUTE_CARD, ("повтор формы «%s», но реестр форм НЕ записан [%s] — молчать не имею "
                            "права" % (shape, err))
    return ROUTE_FEED, ("контур заморожен, повтор отказа №%d той же формы «%s» — в ленту, "
                        "владельца не спрашиваю" % (rec["n"], shape))


# ------------------------------- карточка владельцу ---------------------------

DENY_WORD = "не выкатывай"


def card_text(kinds, commit, client_files, subject="", where="", trainer_available=False,
              trainer_note=""):
    """Дословный текст карточки-ворот. Минимум владельца: ЧТО меняется, КАКИЕ файлы, КАКОЙ коммит,
    КАК откатить — плюс чем ворота открываются. По ВТОРОМУ основанию карточка говорит ПРИЧИНУ
    (вердикта нет / КРАСНЫЙ / снят на другом коммите) и КОМАНДУ, которой вердикт снимают: иначе
    владелец видит «тренажёр не открыл» и не знает, что с этим делать.

    ДВЕ ДВЕРИ, а не одна (05.09.2026). До этого дня карточка называла ровно один принимаемый ответ
    («выкати»), и «нет» владельца не попадало никуда: молчание работает как отказ, но молчание
    неотличимо от «не увидел карточку». Строка про отказ стои́т РЯДОМ со строкой про «да» и честно
    говорит, что она не применяет ничего, — иначе новая дверь читалась бы как вторая дорога к
    выкатке."""
    who = ", ".join(kinds) if kinds else "боты"
    subj = f" — {subject}" if str(subject or "").strip() else ""
    if trainer_available:
        tr = ("зелёный прогон через тренажёр — %s\n   снять вердикт: venv/Scripts/python.exe %s"
              % (str(trainer_note or "").strip() or f"снять: {TRAINER_RUNNER}", TRAINER_RUNNER))
    else:
        tr = f"зелёный прогон через тренажёр — ОСНОВАНИЕ ВЫКЛЮЧЕНО ({TRAINER_GREEN_ENV}=0)"
    return (
        "⛔ Оркестратор: авто-выкатка на КЛИЕНТСКИЙ контур ОСТАНОВЛЕНА — жду твоего «да».\n"
        f"Коммит: {commit}{subj}\n"
        f"Кого касается: {who} — НЕ перезапущены, живые боты остались на прежнем коде.\n"
        f"Клиентские файлы ({len(client_files)}): {', '.join(client_files)}\n"
        "Почему ворота: файл в транзитивном import-замыкании userbot_listen.py / "
        "moderation_bot.py — правка доехала бы до ЖИВОГО клиента.\n"
        f"Где сработало: {where}\n"
        "Пропуск — одно из двух:\n"
        " • твоё «да»: ответь «выкати» — применю этим же коммитом в ближайшую минуту;\n"
        f" • {tr}.\n"
        f"Не надо — ответь «{DENY_WORD}» (или «нет», «отбой»): запишу твой отказ и по этому поводу\n"
        "   спрашивать перестану. Отказ ничего не применяет и ничего не откатывает; на другом\n"
        "   коммите с другим составом файлов спрошу снова.\n"
        f"Откатить: git revert --no-edit {commit}\n"
        "SUGGEST_TEST_MODE не трогали — второй слой на месте."
    )
