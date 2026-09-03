# -*- coding: utf-8 -*-
"""
deploy_voice.py — ГОЛОС ПОДЪЁМА РЕБЁНКА МИМО ВОРОТ (03.09.2026).

ЗАЧЕМ (класс, измеренный переписью 03.09). Ворота клиентского контура стоя́т на трёх дорогах
ДОСТАВКИ КОДА и работают: 169 отказов за 42.5 суток. Но ребёнка поднимают ещё шесть дверей, и
ворот они не спрашивают — потому что «поднять процесс» и «выбрать код» в этом контуре ОДНО
действие: python грузит модули с диска в момент старта, отдельного шага «выкатить версию» нет.
За те же 42.5 суток мимо ворот прошло 39 пусков, 26 из них сменили детям код, 19 несли
КЛИЕНТСКИЕ файлы — при нуле доставок через ворота и нуле решений владельца.

ЧЕГО НЕ ХВАТАЛО — не замка, а СЛОВА. Единственная строка, которой контур вообще замечал код
детей, — «метка отведена назад … долг детей снова виден» (`pc_orchestrator._adopt_live_child_base`):
26 строк за окно, и все 26 говорят про ОТСТАВАНИЕ. Когда дети УЕХАЛИ ВПЕРЁД мимо ворот, событие
для той строки буквально то же самое, и она пишет то же слово «долг». Строк, называющих обход,
выкатку или новый код у детей, было **ноль**. Дефект был не в том, что дверь открыта, а в том,
что открытая дверь МОЛЧАЛА.

ЧТО ЭТОТ МОДУЛЬ ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ — граница жёсткая:
  • ДАЁТ ГОЛОС: на каждом подъёме ребёнка мимо ворот кладёт в журнал строку, из которой видно,
    какой коммит лежал на диске в эту секунду, отличается ли он от коммита прежнего подъёма и
    есть ли среди отличий клиентские файлы;
  • НЕ ЗАКРЫВАЕТ НИ ОДНОЙ ДВЕРИ. Ни одна ветка не возвращает «не поднимать», не бросает
    исключение наружу и не трогает ворота, реестры одобрений и вердиктов. Подъём происходит во
    ВСЕХ исходах, включая «git не ответил» и «признак не построился»;
  • НЕ РОНЯЕТ БОТОВ. `announce()` не выпускает наружу ни одного исключения (`except BaseException`
    в самом внешнем слое), и зовётся ПОСЛЕ того, как ребёнок уже поднят: даже полный отказ голоса
    оставляет подъём состоявшимся.

ТРЕТИЙ ИСХОД ОБЯЗАТЕЛЕН И НЕ ПРЕВРАЩАЕТСЯ В ЗЕЛЁНОЕ. git молчит → в журнал уходит
«КОММИТ НЕИЗВЕСТЕН», а не тишина; дифф не сложился → «отличия НЕИЗВЕСТНЫ», а не «клиентских нет».
Молчания в этом модуле нет ровно ОДИН раз — когда коммит подъёма совпал с прежним: тогда выкатки
не было, и строка о выкатке была бы ложью (диагностическая строка в лог процесса всё равно идёт).

ПОЧЕМУ ПОРОГ БУДИЛЬНИКА — «КЛИЕНТСКИЕ ФАЙЛЫ», А НЕ «НОВЫЙ КОД». Внутренних коммитов на этой
полосе десятки в сутки, и подъём на новом внутреннем коде — норма, а не новость. Владельца будит
только то, что доедет до ЖИВОГО клиента. Признак клиентского — ТОТ ЖЕ, которым судят ворота
(`client_contour.is_client`, транзитивное import-замыкание userbot_listen/moderation_bot), чтобы
голос и ворота не спорили о том, что такое «клиентский файл».

ГДЕ ГОЛОС СОЗНАТЕЛЬНО МЯГЧЕ ВОРОТ. У ворот «не знаю» = «клиентский» (fail-closed: они ДЕРЖАТ, и
цена ошибки — задержка). Голос не держит ничего, и цена его ошибки другая: ложный будильник
тратит внимание владельца, а честная строка «отличия НЕИЗВЕСТНЫ» в журнале не теряется. Поэтому
будильник звонит ТОЛЬКО на доказанных клиентских файлах (правило 4 задания), а неопределённость
уходит строкой. Одно исключение оставлено воротам: если сам ПРИЗНАК не построился (`closure.ok`
False), `client_contour.is_client` отвечает True на всё — это его fail-closed, и мы его не гасим,
а НАЗЫВАЕМ в строке словами «признак контура не построился».

ПАМЯТЬ О ПРЕЖНЕМ ПОДЪЁМЕ. `pc_orchestrator.child_raise_commit.json` (маска `pc_orchestrator.*.json`
уже в .gitignore — реестром вердиктов не является, это наш собственный блокнот). Пишут его ОБЕ
двери: агент и демон живут на одной машине в одном дереве. Первый подъём после появления модуля
сравнивать не с чем — исход `NO_PREV`, и он назван вслух, а не выдан за «изменений нет».

ОТКАТ — ОДНА РУЧКА, `DEPLOY_VOICE_OFF=1` (читается на КАЖДОМ подъёме, рестарт не нужен). Она
выключает РОВНО голос и не может тронуть подъём по устройству: всё, что умеет этот модуль, —
говорить. Полный откат — `git revert` коммита; ворота, реестры и двери в обоих случаях нетронуты.

Интерфейс:
    verdict(kind, door, commit, prev, changed, client_hits, closure_ok) → Voice  (ЧИСТАЯ, голден)
    card_text(kind, door, commit, prev, client_hits, changed)          → текст владельцу (ЧИСТАЯ)
    announce(kind, door, ...)   → Voice — вся дорога: git → признак → журнал → будильник → память
    head_commit() / changed_between() / client_of() / read_prev() / remember()  — слой ввода-вывода
"""

import io
import json
import os
import subprocess
import time
from collections import namedtuple

import client_contour

REPO = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VENV_PY = os.path.join(REPO, "venv", "Scripts", "python.exe")
JOURNAL_WRITER = os.path.join(REPO, "cowork_log_append.py")
DNOTIFY = os.path.join(REPO, "dispatch_notify.py")


def _state(path):
    """Боевой путь СОСТОЯНИЯ под тестом уводится в одноразовый temp (тот же приём и та же ручка,
    что у `pc_orchestrator._state` → `log_setup.state_path`).

    ПРАВИЛО СТОИ́Т ЗДЕСЬ, В КОНСТАНТЕ, А НЕ В setUp ТЕСТОВ — и это не вкус, а измеренный класс
    полосы: тест зовёт живую функцию, та берёт путь по умолчанию, и боевой файл переписан
    фикстурой. Ровно так уже уничтожали спул ревизора и снимок надзора вотчдога. Здесь цена та же
    и хуже: подменённая память подъёмов заставит следующий ЖИВОЙ подъём назвать выкаткой то, чего
    не было, — либо промолчать о той, что была. Замер 03.09 при заведении: три тест-класса зовут
    `client_watchdog_tick` напрямую, боевой файл успел записаться дважды за один прогон.

    Фолбэк на боевой путь при отсутствии `log_setup` сознателен: голос без изоляции работает
    верно, голос без памяти — нет."""
    try:
        import log_setup
        return log_setup.state_path(path)
    except Exception:
        return path


STATE_FILE = _state(os.path.join(REPO, "pc_orchestrator.child_raise_commit.json"))

GIT_TIMEOUT = 15          # столько же, сколько у pc_orchestrator._git_out
SHORT = 9                 # длина хеша в строке — как у реконсиляции детей (head[:9])
NAMES_MAX = 8             # имён файлов в строке; остаток называется числом (LINE_MAX журнала 600)

# ─────────────────────────── СЛОВА, КОТОРЫХ В КОНТУРЕ НЕ БЫЛО ────────────────────────────────
# Правило 3 задания: новая форма обязана отличаться от штатной строки отставания РАЗНЫМИ СЛОВАМИ,
# а не пунктуацией. Замер 03.09 перед заведением: `мимо ворот` — 0 совпадений в *.py репозитория,
# 0 в `pc_orchestrator.log` за 42.5 суток, 0 в `pc_agent.log*` за 18.6 суток. Штатная строка
# отставания говорит «метка отведена назад … долг детей снова виден» — ни одного общего слова
# с `MARK`. Инвариант заморожен тестом (test_deploy_voice.TestWordsAreNew).
MARK = "подъём мимо ворот"
DEPLOY_WORDS = "ВЫКАТКА КЛИЕНТСКОГО КОДА БЕЗ ВОРОТ"
UNKNOWN_WORDS = "КОММИТ НЕИЗВЕСТЕН"

DOOR_READOPT = "авто-реадопшн pc_agent"       # B1 переписи: старт агента поднимает userbot
DOOR_WATCHDOG = "контур-вотчдог демона"       # B2 переписи: сторож поднимает мёртвого ребёнка

# Дети, о подъёме которых голос говорит. pc_agent сюда НЕ входит: он не клиентский процесс, а его
# подъём и так каскадом порождает B1, который скажет за userbot.
KINDS = ("userbot", "moderation_bot")
_KIND_ALIASES = {"moderbot": "moderation_bot", "moderation_bot": "moderation_bot",
                 "userbot": "userbot"}

# ─────────────────────────── ИСХОДЫ (их шесть, и ни один не «хорошо») ────────────────────────
SAME = "same"                  # коммит тот же → выкатки НЕ БЫЛО, строки о выкатке нет
NEW_CLIENT = "new_client"      # новый коммит, среди отличий КЛИЕНТСКИЕ файлы → будим
NEW_INTERNAL = "new_internal"  # новый коммит, клиентских нет → строка есть, не будим
NO_PREV = "no_prev"            # прежний подъём не записан — сравнивать не с чем
NO_DIFF = "no_diff"            # коммит другой, но дифф не получен — клиентские НЕ проверены
NO_COMMIT = "no_commit"        # git не ответил — коммит подъёма неизвестен
OFF = "off"                    # голос выключен ручкой отката — молчит СОЗНАТЕЛЬНО
OUTCOMES = (SAME, NEW_CLIENT, NEW_INTERNAL, NO_PREV, NO_DIFF, NO_COMMIT, OFF)

# ОТКАТ ОДНОЙ РУЧКОЙ, и он выключает РОВНО голос. Ворота, двери и подъём детей ей недоступны по
# устройству: единственное, что умеет `announce`, — говорить, поэтому «выключить» здесь значит
# «замолчать», а не «запретить». Цена отката названа честно: с выключенным голосом полоса
# возвращается ровно в то состояние, из-за которого он заведён, — 0 строк на 19 выкаток.
OFF_ENV = "DEPLOY_VOICE_OFF"
_OFF_VALUES = ("1", "true", "yes", "on")

Voice = namedtuple("Voice", "outcome line loud note")


def _kind(name):
    """Имя ребёнка к одному виду: вотчдог зовёт его 'moderation_bot', подниматель — 'moderbot'."""
    return _KIND_ALIASES.get(str(name or "").strip().lower(), str(name or "").strip().lower())


def _cut(commit):
    """Коммит для показа. Пустой/None → ''. Длину режем ТОЛЬКО для текста — сравнение отдельно."""
    s = str(commit or "").strip()
    return s[:SHORT] if s else ""


def _same_commit(a, b):
    """Один ли это коммит. Хеши приходят разной длины (7/9/40) — нормализуем тем же `short`,
    что и ворота; не-хеш (метка) сравниваем как есть, чтобы разные метки не склеились в одну."""
    if not a or not b:
        return False
    na, nb = client_contour.short(a), client_contour.short(b)
    if na and nb:
        return na == nb
    return str(a).strip() == str(b).strip()


def _names(paths, cap=NAMES_MAX):
    """Список имён в строку: не больше `cap`, остаток — числом (строка журнала имеет потолок)."""
    items = [str(p) for p in (paths or [])]
    if len(items) <= cap:
        return ", ".join(items)
    return ", ".join(items[:cap]) + " и ещё %d" % (len(items) - cap)


# ───────────────────────────────── ЧИСТОЕ ЯДРО (голден) ──────────────────────────────────────

def verdict(kind, door, commit, prev, changed=None, client_hits=None, closure_ok=True):
    """Что сказать о подъёме. ЧИСТАЯ функция: ни git, ни диска, ни сети. → Voice.

    commit      — коммит НА ДИСКЕ в секунду подъёма (None = git не ответил);
    prev        — коммит прежнего подъёма этого ребёнка (None = не записан);
    changed     — файлы диффа prev..commit (None = дифф не получен, [] = отличий нет);
    client_hits — клиентские среди `changed` (None = не считали);
    closure_ok  — построился ли признак контура (False → его fail-closed назван словами).

    `line` пуста РОВНО в одном исходе — SAME: выкатки не было, и строка о выкатке была бы ложью.
    `loud` True ровно в одном — NEW_CLIENT: правило 4 задания (будим только на клиентских файлах).
    """
    k = _kind(kind)
    door = str(door or "неизвестная дверь")
    head = "%s: %s поднят дверью «%s»" % (MARK, k, door)

    if not commit:
        return Voice(NO_COMMIT,
                     "%s — %s (git не ответил): выкачен ли новый код, сказать нечем. "
                     "Это третий исход, а не «всё хорошо»." % (head, UNKNOWN_WORDS),
                     False, "")
    cm = _cut(commit)

    if not prev:
        return Voice(NO_PREV,
                     "%s на коммите %s; прежний подъём НЕ ЗАПИСАН — сравнить не с чем, отличия "
                     "НЕИЗВЕСТНЫ. Следующий подъём этого ребёнка сравнит." % (head, cm),
                     False, "")
    pm = _cut(prev)

    if _same_commit(commit, prev):
        return Voice(SAME, "", False,
                     "%s: %s на коммите %s — тот же, что в прошлый подъём; нового кода не "
                     "выкачено" % (MARK, k, cm))

    if changed is None:
        return Voice(NO_DIFF,
                     "%s на коммите %s, прежний подъём был на %s — коммит ДРУГОЙ, но отличия "
                     "НЕИЗВЕСТНЫ (git дифф не ответил): клиентские файлы НЕ ПРОВЕРЕНЫ, владельца "
                     "не бужу — доказательства нет." % (head, cm, pm),
                     False, "")

    total = len(changed)
    hits = list(client_hits or [])
    if hits:
        why = "" if closure_ok else \
            "; признак контура не построился — считаю клиентским (fail-closed, как у ворот)"
        return Voice(NEW_CLIENT,
                     "%s на коммите %s, прежний подъём был на %s — %s: клиентских файлов %d из "
                     "%d — %s%s" % (head, cm, pm, DEPLOY_WORDS, len(hits), total, _names(hits), why),
                     True, "")
    return Voice(NEW_INTERNAL,
                 "%s на коммите %s, прежний подъём был на %s — код у ребёнка сменился, файлов в "
                 "отличиях %d, клиентских среди них НЕТ; владельца не бужу, строка живёт в "
                 "журнале." % (head, cm, pm, total),
                 False, "")


def card_text(kind, door, commit, prev, client_hits, changed=None, closure_ok=True):
    """Текст владельцу о СОСТОЯВШЕЙСЯ выкатке клиентского кода мимо ворот. ЧИСТАЯ (голден).

    Кнопок не несёт СОЗНАТЕЛЬНО: кнопка означает «жду ответа», а это НОВОСТЬ о свершившемся —
    решение владельца тут ничего не отменяет, ребёнок уже работает на этом коде. Адрес карточки
    выбирает не этот модуль, а признак `dispatch_notify.awaits_reply` (правило владельца 10.08)."""
    k = _kind(kind)
    hits = list(client_hits or [])
    total = len(changed) if changed is not None else len(hits)
    lines = [
        "🚨 %s" % DEPLOY_WORDS,
        "%s подняли дверью «%s», и он УЖЕ РАБОТАЕТ на коммите %s." % (k, door, _cut(commit)),
        "Прежний подъём был на %s. Ворота этот коммит не смотрели: дверь их не спрашивает — "
        "она поднимает процесс, а код python берёт с диска сам." % _cut(prev),
        "Клиентские файлы (%d из %d): %s" % (len(hits), total, _names(hits)),
        "Посмотреть, что уехало: git show --stat %s" % _cut(commit),
        "Это НЕ вопрос и не требует ответа: дверь не закрыта, подъём не отменяется. "
        "Сообщение существует потому, что раньше такой выкатки не было видно нигде.",
    ]
    if not closure_ok:
        lines.insert(4, "Признак контура не построился — файлы посчитаны КЛИЕНТСКИМИ по "
                        "fail-closed, как это делают ворота.")
    return "\n".join(lines)


# ─────────────────────────────────── СЛОЙ ВВОДА-ВЫВОДА ───────────────────────────────────────

def _git(args, timeout=GIT_TIMEOUT, repo=None):
    """git в репозитории → stdout.strip() | None. Тихо: недоступный git — исход, а не падение."""
    try:
        p = subprocess.run(["git"] + list(args), cwd=repo or REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=NO_WINDOW)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def head_commit(repo=None, git=None):
    """Коммит НА ДИСКЕ в эту секунду. → полный хеш | None (git молчит → исход НЕИЗВЕСТНО)."""
    return (git or _git)(["rev-parse", "HEAD"], repo=repo) or None


def changed_between(prev, cur, repo=None, git=None):
    """Файлы диффа prev..cur. → список | None (git не ответил). Пустой список — законный ответ
    «отличий нет», и он НЕ то же самое, что None: путать их значит терять третий исход."""
    if not prev or not cur:
        return None
    out = (git or _git)(["diff", "--name-only", "%s..%s" % (prev, cur)], repo=repo)
    if out is None:
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def client_of(paths, repo=None, closure_fn=None):
    """Клиентские среди путей → (список, closure_ok). Признак — ТОТ ЖЕ, что у ворот; его
    fail-closed (граф не построился → клиентским считается всё) сохранён и назван вторым членом."""
    try:
        cl = (closure_fn or client_contour.closure)(repo or REPO)
    except Exception:
        return list(paths or []), False           # признак упал — «не знаю» это НЕ «внутренний»
    hits = []
    for p in (paths or []):
        try:
            if client_contour.is_client(p, cl=cl):
                hits.append(p)
        except Exception:
            hits.append(p)
    return hits, bool(getattr(cl, "ok", False))


def read_prev(kind, path=None):
    """Коммит прежнего подъёма этого ребёнка → str | None (записи нет / файл не читается)."""
    try:
        with io.open(path or STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        ent = (d or {}).get(_kind(kind)) or {}
        return (ent.get("commit") or "").strip() or None
    except (OSError, ValueError, AttributeError):
        return None


def remember(kind, commit, door, path=None, now=None):
    """Запомнить коммит ЭТОГО подъёма. → True/False. Пишем ТОЛЬКО известный коммит: затирать
    прежний известный хеш словом «неизвестно» значит терять единственную точку сравнения.
    Запись атомарная (tmp + os.replace): обе двери пишут один файл с одной машины."""
    if not commit:
        return False
    p = path or STATE_FILE
    try:
        try:
            with io.open(p, encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                d = {}
        except (OSError, ValueError):
            d = {}
        ts = time.time() if now is None else now
        d[_kind(kind)] = {"commit": str(commit).strip(), "door": str(door or ""), "ts": float(ts),
                          "at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))}
        tmp = p + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def _journal_spawn(line):
    """Строка в cowork_log ОТДЕЛЬНЫМ процессом (копия pc_orchestrator._cowork): секреты моста
    берёт сам писатель, наш код их не видит. Fire-and-forget — сбой доставки не касается подъёма.
    Переносы схлопываем в источнике: одна запись журнала = одна строка."""
    one = " ".join(str(line or "").split())
    if not one:
        return
    subprocess.Popen([VENV_PY, JOURNAL_WRITER, "NOTE Контур: " + one], cwd=REPO,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def _wake_spawn(text):
    """Пуш владельцу через dispatch_notify (тот же канал, что у `pc_orchestrator._notify`).
    Адрес выбирает признак `awaits_reply` внутри dispatch_notify, а не этот модуль."""
    subprocess.Popen([VENV_PY, DNOTIFY, str(text)], cwd=REPO,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


# ──────────────────────────────────── ВСЯ ДОРОГА ─────────────────────────────────────────────

def is_off(env=None):
    """Выключен ли голос ручкой отката. → bool. Читается НА КАЖДОМ подъёме, а не на импорте:
    откат обязан действовать сразу, без рестарта демона и агента."""
    e = os.environ if env is None else env
    try:
        return (e.get(OFF_ENV) or "").strip().lower() in _OFF_VALUES
    except Exception:
        return False


def announce(kind, door, repo=None, journal=None, wake=None, log_fn=None, state_path=None,
             head_fn=None, diff_fn=None, client_fn=None, prev_fn=None, remember_fn=None,
             now=None, env=None):
    """Сказать о состоявшемся подъёме ребёнка мимо ворот. → Voice.

    ЗОВЁТСЯ ПОСЛЕ ПОДЪЁМА и НИКОГДА не решает, поднимать ли: возвращаемое значение нужно тестам и
    логу, ни одна дверь его не спрашивает. Исключений наружу не выпускает ВООБЩЕ — иначе голос мог
    бы уронить дверь, а это ровно то, чего задача запрещает («ботов не ронять ни в одном исходе»).
    Всё внешнее инъектируется: в тестах ни git, ни мозга, ни пушей."""
    try:
        k = _kind(kind)
        if is_off(env):
            v = Voice(OFF, "", False,
                      "%s: голос выключен ручкой %s — %s поднят молча" % (MARK, OFF_ENV, k))
            if log_fn:                       # в журнал молчим, в лог процесса — нет: выключенный
                try:                         # голос обязан быть виден тому, кто читает лог
                    log_fn(v.note)
                except Exception:
                    pass
            return v
        prev = (prev_fn or read_prev)(k, state_path)
        commit = (head_fn or head_commit)(repo)
        changed = None
        hits, closure_ok = None, True
        if commit and prev and not _same_commit(commit, prev):
            changed = (diff_fn or changed_between)(prev, commit, repo)
            if changed is not None:
                hits, closure_ok = (client_fn or client_of)(changed, repo)
        v = verdict(k, door, commit, prev, changed, hits, closure_ok)

        if log_fn:
            try:
                log_fn(v.line or v.note)
            except Exception:
                pass
        if v.line:
            try:
                (journal or _journal_spawn)(v.line)
            except Exception as e:                     # noqa: BLE001 — журнал не роняет подъём
                if log_fn:
                    try:
                        log_fn("%s: строка в журнал НЕ УШЛА (%s) — подъём состоялся" % (MARK, e))
                    except Exception:
                        pass
        if v.loud:
            try:
                (wake or _wake_spawn)(card_text(k, door, commit, prev, hits, changed, closure_ok))
            except Exception as e:                     # noqa: BLE001 — пуш не роняет подъём
                if log_fn:
                    try:
                        log_fn("%s: пуш владельцу НЕ УШЁЛ (%s) — строка в журнале осталась"
                               % (MARK, e))
                    except Exception:
                        pass
        if commit:
            (remember_fn or remember)(k, commit, door, state_path, now)
        return v
    except BaseException:                              # noqa: BLE001 — голос НЕ имеет права падать
        try:
            if log_fn:
                log_fn("%s: голос отказал целиком — подъём состоялся, факт кода НЕ НАЗВАН" % MARK)
        except Exception:
            pass
        return Voice(NO_COMMIT, "", False, "")
