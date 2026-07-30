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

СРЕЗ НА ЧУЖИХ ПРОЦЕССАХ (единственное исключение, и оно принципиальное). Обход НЕ идёт сквозь
входные точки ДРУГИХ живых процессов — `pc_orchestrator.py` (демон) и `pc_agent.py` (агент): сами
они внутренние, и всё, что висит ТОЛЬКО под ними, внутреннее тоже. Без среза граф схлопывается:
`moderation_core._default_lesson_enqueue` ЛЕНИВО импортирует `pc_orchestrator`, чтобы положить
строку в очередь (`lesson_router._default_lesson_thinker` — так же, ради думателя), и через это
одно ребро в «клиентский контур» въезжает весь демон вместе с гейтом и инфраструктурой. Это
межпроцессная граница-вызов, а не рантайм бота, и владелец прямо держит демона внутренним (он
обязан продолжать выкатываться сам). Список тут — по ПРОЦЕССАМ, а не по файлам: процессов три и
новый заводят осознанно, файлы же появляются каждую неделю — их считает граф.
ОСТАТОК ЧЕСТНО: код, который клиент увидит ЧЕРЕЗ демона (`_thinker_exec` классификатора уроков),
срезом не покрыт. Появится клиентский текст внутри `pc_orchestrator.py` — признак его не поймает.

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
    closure(repo)              → Closure(files, data, ok, reason)  — замыкание с кэшем по mtime
    is_client(path)            → bool (fail-closed True при ok=False)
    split(paths)               → (клиентские, внутренние)
    mentions(text)             → (клиентский?, что названо, определимо?) — для ворот ВХОДА (ревизор)
    release_reason(commit)     → 'owner' | 'trainer' | None — основание пропуска ворот
    approve(commit)            → записать «да» владельца
    trainer_verdict(commit)    → (зачтён?, дословная причина) — ВТОРОЕ основание
    trainer_status(commit)     → строка причины для карточки владельцу
    card_text(...)             → дословный текст карточки владельцу
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


def _build(repo, entries):
    """Обход в ширину от входных точек. → Closure. Любой сбой чтения/парса ФАЙЛА ЗАМЫКАНИЯ делает
    результат недостоверным (ok=False) — тогда ворота считают клиентским всё."""
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
            if nxt and os.path.basename(nxt).lower() not in FOREIGN_ENTRIES:
                queue.append(nxt)          # срез: в чужой процесс (демон/агент) не заходим
    return Closure(frozenset(files), frozenset(data), True, "ok")


def closure(repo=None, entries=None):
    """Транзитивное import-замыкание клиентских процессов (с кэшем по отпечатку каталога). →
    Closure. Кэш нужен потому, что реконсиляция детей спрашивает признак каждые 60 с."""
    repo = repo or REPO
    entries = tuple(entries or CLIENT_ENTRIES)
    sig = _signature(repo)
    if sig is None:
        return Closure(frozenset(), frozenset(), False, f"каталог {repo} не читается")
    ck = (os.path.normcase(os.path.abspath(repo)), entries)
    hit = _cache.get(ck)
    if hit and hit[0] == sig:
        return hit[1]
    val = _build(repo, entries)
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


def corpus_sha(path=None):
    """Отпечаток корпуса кейсов (sha256, 16 hex) → '' при нечитаемом файле. Вердикт обязан назвать
    ТОТ корпус, что лежит на диске: зелень, снятая на урезанном наборе кейсов, не основание."""
    try:
        with io.open(path or TRAINER_CASES_FILE, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return ""


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
            return False, ("вердикт КРАСНЫЙ: чеков %s/%s, кейсов %s/%s (прогон %s)"
                           % (r.get("checks_passed"), r.get("checks_total"), r.get("cases"),
                              r.get("cases_total"), r.get("when") or "?"))
        if g:
            return False, ("вердикта на этот коммит нет (последний зелёный — на %s)"
                           % ", ".join(sorted(g)[:3]))
        return False, "прогона не было"
    if str(rec.get("result") or "") != "green":
        return False, f"запись не зелёная: result={rec.get('result')!r}"
    rc = str(rec.get("commit") or "").strip().lower()
    cc = str(commit or "").strip().lower()
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


# ------------------------------- карточка владельцу ---------------------------

def card_text(kinds, commit, client_files, subject="", where="", trainer_available=False,
              trainer_note=""):
    """Дословный текст карточки-ворот. Минимум владельца: ЧТО меняется, КАКИЕ файлы, КАКОЙ коммит,
    КАК откатить — плюс чем ворота открываются. По ВТОРОМУ основанию карточка говорит ПРИЧИНУ
    (вердикта нет / КРАСНЫЙ / снят на другом коммите) и КОМАНДУ, которой вердикт снимают: иначе
    владелец видит «тренажёр не открыл» и не знает, что с этим делать."""
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
        f"Откатить: git revert --no-edit {commit}\n"
        "SUGGEST_TEST_MODE не трогали — второй слой на месте."
    )
