# -*- coding: utf-8 -*-
"""
accounts_run.py — РУКИ РЕЕСТРА УЧЁТОК: «учётки» и «учётка N» с телефона, заведение учётки с ПК
(25.09.2026, задание Штаба «учётки одним словом»).

Решение (что такое реестр, какой профиль у строителей) живёт в `accounts_registry.py` — его импортирует
демон. Здесь — всё, что ходит наружу: живые пробы профилей ПК (`claude -p` под каталогом профиля),
пробы слотов сервера и переключение серверного демона (`vps_token_install`, ssh). Демон этот модуль
НЕ импортирует: замыкание демона не растёт ни на `subprocess`, ни на ssh.

КАК ЭТО ЗВУЧИТ (25.09.2026, просьба владельца «красиво, читабельно, человечески, кратко, правильно —
не компьютерный список»): ответы «учётки», «учётка N», «учётки заведи» — короткие строки словами. Учётка
зовётся «№3 samfold» (номер + метка), статус — «✅ работает» / «⏳ лимит до вс 27.09 16:00» (время сброса
по МЕСТНЫМ часам ПК) / «❌ вход не принят» / «❔ нет ответа»; ни кодов поставщика, ни имён файлов, ни
путей профилей, ни UTC. Форму сброса, которую не разобрали, ответ оставляет словами поставщика в «»
(выдумывать время нельзя). У учётки со входом сервера названа его буква (занятую букву видно). То, что
требует руки владельца, — строка ❗ с тем, ЧТО сделать (какую копию вернуть, какую команду дать); итог
«учётка N» — в шапке. Подсказки «как поправить» ведут на экран владельца (`--screen`), а не на голое
«--set», которое консоль отвергла бы. Упала сама дверь — владельцу одна строка, трасса — в stderr.
Консоль (`--show`, `--screen`, `--set`, `--builders`) остаётся технической.

ЧТО ВИДИТ ВЛАДЕЛЕЦ И ОТКУДА КАЖДОЕ СЛОВО:
  «учётки»    → по ОДНОЙ живой пробе на каждый профиль ПК из реестра и на действующее и КАЖДЫЙ слот
                сервера; ответ поставщика (200/429/401), время сброса ЕГО словами, что действует у
                строителей ПК (реестр), у RC (свой файл) и у демона сервера (равенство значений,
                посчитанное на сервере). Итог каждой пробы ложится в `accounts_last.json` — это и
                есть «последний ответ поставщика».
  «учётка N»  → ПК: проба профиля N; 200 → номер строителей в реестре = N (файл вне git: дерево
                не пачкается, авто-фетч не встаёт); иначе строители остаются где были. Сервер:
                `vps_token_install.use_slot` (копия с датой, проба, при зелёном ОДИН рестарт, отказ —
                возврат копии, идёт заход — стоп словами). Слово владельца = разрешение на один
                рестарт демона сервера; второго рестарта «обратно» ход не делает — проба стоит до
                рестарта. RC этим словом не трогается ни одной веткой.
АВТОПЕРЕКЛЮЧЕНИЯ УЧЁТКИ ПО ЛИМИТУ НЕТ: этот модуль зовут только слово владельца и его консоль.
Старый серверный повтор ОДНОЙ задачи под вторым слотом при 429 (`limit_slot`, 20.09) остаётся
как был — действующее он не меняет; «учётки» говорит, есть ли эта подстраховка (второй вход в лимите
или не пускает — её нет, и это сказано, а не обещано).

СЕКРЕТЫ. Токенов и `.credentials.json` модуль не читает вовсе: проба профиля — это запуск CLI с
`CLAUDE_CONFIG_DIR`, а наружу идут только слово исхода, статус, время сброса и текст поставщика
(конверт CLI значений входа не содержит, замер 71r), пропущенный через `exit_evidence.scrub`.

ЗАПУСК:
    venv\\Scripts\\python.exe accounts_run.py --report          # «учётки»
    venv\\Scripts\\python.exe accounts_run.py --init            # «учётки заведи» (первый реестр)
    venv\\Scripts\\python.exe accounts_run.py --switch 3        # «учётка 3»
    venv\\Scripts\\python.exe accounts_run.py --show            # реестр без проб, без сети
    venv\\Scripts\\python.exe accounts_run.py --set 4 --profile D:\\claude_profile_4 --slot C --label четвёртая
    venv\\Scripts\\python.exe accounts_run.py --builders 3      # только номер строителей ПК, без сервера
    venv\\Scripts\\python.exe accounts_run.py --screen          # экран владельца: как завести учётку N
"""

import argparse
import datetime as _dt
import glob
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import namedtuple

try:                                    # база поясов; нет её — запасная карта постоянных смещений ниже
    import zoneinfo as _zoneinfo
except Exception:                       # noqa: BLE001
    _zoneinfo = None

import accounts_registry as accounts
import exit_evidence
import io_utf8
import profile_choice
import vps_token_install as vti

REPO = accounts.REPO
WORK = vti.UCHETKI_WORK                     # tmp/uchetki_2509 — временное место, названо заданием
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Модель пробы профиля ПК = модель исполнителя полосы (`pc_orchestrator.EXECUTOR_MODEL`): лимит
# учётки бывает раздельным по моделям, и проба дешёвой моделью зеленела бы там, где строители
# упрутся. Равенство держит тест (по исходнику демона, без его импорта).
PROBE_MODEL = os.environ.get("ACCOUNTS_PROBE_MODEL", "claude-opus-5-5")
PROBE_TIMEOUT = int(os.environ.get("ACCOUNTS_PROBE_TIMEOUT", "240"))
PROBE_PROMPT = "Reply with exactly: OK"

# У RC свой выбор (задание 25.09): файл рядом с реестром, по умолчанию — ОСНОВНОЙ. Имя обязано
# совпадать с `rc_supervisor.RC_CHOICE_REL` — равенство держит тест (импорт rc_supervisor здесь
# недопустим: он вешает ротируемый лог на файл, который держит живой супервизор).
RC_CHOICE_REL = "rc_profile_choice.txt"

# Флаги пробы гарда — тот же список, что чистит демон (`pc_orchestrator._run_task_impl`).
_TEST_FLAGS = ("PRETOOL_TEST_RUN", "ORCH_TEST_MODE", "PRETOOL_NOPUSH", "PYTEST_CURRENT_TEST")

PC_UNKNOWN = u"неизвестно"


# ═══════════════════ проба профиля ПК ═══════════════════

def _ver_key(name):
    """'2.1.217' → (2, 1, 217); нечисловое → (0,). Зеркало `rc_supervisor._ver_key`."""
    nums = re.findall(r"\d+", name or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def claude_base_dirs():
    """Базы версий claude-code, включая РЕАЛЬНУЮ MSIX-базу. Зеркало `rc_supervisor.claude_base_dirs`:
    pc_agent поднимает Планировщик, и Roaming-редирект MSIX ему не виден."""
    home = os.path.expanduser("~")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    local = os.getenv("LOCALAPPDATA") or os.path.join(os.getenv("USERPROFILE") or home,
                                                      "AppData", "Local")
    out = [os.path.join(appdata, "Claude", "claude-code")]
    try:
        for cc in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache",
                                         "Roaming", "Claude", "claude-code")):
            if cc not in out:
                out.append(cc)
    except Exception:                                  # noqa: BLE001
        pass
    return out


def newest_versioned_claude(bases=None, globber=None, isfile=None):
    """Новейшая версионная установка по ЧИСЛОВОМУ ключу. Зеркало `rc_supervisor.newest_versioned_claude`."""
    bases = bases if bases is not None else claude_base_dirs()
    _glob = globber or glob.glob
    _isf = isfile or os.path.isfile
    cands = []
    try:
        for base in bases:
            for d in _glob(os.path.join(base, "*")):
                exe = os.path.join(d, "claude.exe")
                if _isf(exe):
                    cands.append((_ver_key(os.path.basename(d)), exe))
    except Exception:                                  # noqa: BLE001
        return None
    if not cands:
        return None
    cands.sort()
    return cands[-1][1]


def find_claude(which=None, isfile=None, newest=None):
    """CLI claude ТЕМ ЖЕ порядком, что у RC (`rc_supervisor.resolve_claude`; равенство держит тест):
    PATH-шим → вечный нативный шим ~/.local/bin → новейшая версионная установка (Desktop/MSIX) →
    прочие схемы. Импортировать rc_supervisor отсюда нельзя: он вешает лог-обработчик на старте.
    Нет нигде → None (проба «неизвестно», а не «жива»)."""
    _which = which or shutil.which
    _isf = isfile or os.path.isfile
    shim = _which("claude")
    if shim:
        return shim
    home = os.path.expanduser("~")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude.cmd")):
        if _isf(c):
            return c
    exe = (newest or newest_versioned_claude)()
    if exe:
        return exe
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    for c in (os.path.join(local, "Programs", "claude", "claude.exe"),
              os.path.join(local, "Programs", "claude-code", "claude.exe"),
              os.path.join(appdata, "npm", "claude.cmd")):
        if _isf(c):
            return c
    return None


def probe_env(profile, base=None):
    """Окружение пробы профиля: ровно то, с чем пошёл бы ребёнок строителей под этой учёткой.
    ОСНОВНОЙ → ключ снят; путь → ключ = путь. Пустого значения не ставит ни одна ветка."""
    env = dict(os.environ if base is None else base)
    env.pop("ANTHROPIC_API_KEY", None)           # подписка, а не платный ключ — как у демона
    env.pop("OPENAI_API_KEY", None)
    for flag in _TEST_FLAGS:
        env.pop(flag, None)
    if profile == accounts.WORD_MAIN or not profile:
        env.pop(accounts.KEY, None)
    else:
        env[accounts.KEY] = profile
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _row(word, code=None, reset=u"", text=u"", called=False):
    return {"word": word, "code": code, "reset": reset, "text": text, "called": called,
            "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}


def pc_probe(profile, runner=None, claude=None, isdir=None, base_env=None):
    """ОДИН дешёвый вызов под профилем → строка {word, code, reset, text, called, at}.

    Нейтральный cwd (системный temp) + `--setting-sources project`: ни проектных, ни
    пользовательских настроек, значит ни хуков, ни карточек — из пробы наружу к человеку не уходит
    ничего. `--no-session-persistence`: транскрипта в профиле проба не оставляет."""
    isdir = isdir or os.path.isdir
    if profile != accounts.WORD_MAIN:
        try:
            if not isdir(profile):
                return _row(u"нет каталога", text=u"каталога профиля «%s» нет" % profile)
        except Exception as e:                        # noqa: BLE001
            return _row(PC_UNKNOWN, text=u"каталог не проверить (%s)" % type(e).__name__)
    exe = claude if claude is not None else find_claude()
    if not exe:
        return _row(PC_UNKNOWN, text=u"claude не найден на ПК — пробы не было")
    cmd = [exe, "-p", PROBE_PROMPT, "--model", PROBE_MODEL, "--output-format", "json",
           "--no-session-persistence", "--setting-sources", "project"]
    run = runner or subprocess.run
    try:
        p = run(cmd, cwd=tempfile.gettempdir(), env=probe_env(profile, base_env),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=PROBE_TIMEOUT, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return _row(PC_UNKNOWN, text=u"claude не ответил за %d с" % PROBE_TIMEOUT)
    except Exception as e:                            # noqa: BLE001 — не запустился = не знаем
        return _row(PC_UNKNOWN, text=u"claude не запустился (%s)" % type(e).__name__)
    rc = getattr(p, "returncode", 255)
    out = (getattr(p, "stdout", "") or "") + "\n" + (getattr(p, "stderr", "") or "")
    word, why = vti.verdict_of_probe(rc, out)
    data = vti.parse_envelope(out)
    code = 200 if word == vti.GREEN else vti.status_of(data, out)
    if code is None and word == vti.LIMIT:
        code = 429
    reset = vti.reset_of(data, out) if code == 429 else u""
    text = u"%s" % (data.get("result", u"") if isinstance(data, dict) else why)
    return _row(word, code, reset, exit_evidence.scrub(vti._squeeze(text, 200)), True)


# ═══════════════════ человеческие слова: время, статус, имена (25.09.2026, «красиво везде») ═══════════════════
# Владелец читает ответ с телефона: ни кодов поставщика, ни имён файлов, ни UTC. Время сброса — по
# МЕСТНЫМ часам ПК (владелец живёт в его поясе). Всё ниже — ЧИСТЫЕ функции: «сейчас» и пояс вывода
# приходят параметрами (тесты); по умолчанию — часы ПК и его местный пояс.

WEEKDAYS = (u"пн", u"вт", u"ср", u"чт", u"пт", u"сб", u"вс")
_MONTHS = {u"jan": 1, u"feb": 2, u"mar": 3, u"apr": 4, u"may": 5, u"jun": 6, u"jul": 7, u"aug": 8,
           u"sep": 9, u"oct": 10, u"nov": 11, u"dec": 12}
# Запас на случай, когда база поясов не загрузилась: ТОЛЬКО пояса без летнего времени — у них смещение
# постоянное, и число здесь не врёт ни в один день года. Пояса нет ни там, ни здесь — слова поставщика.
_FIXED_ZONES = {u"UTC": 0, u"GMT": 0, u"Z": 0, u"ETC/UTC": 0, u"ETC/GMT": 0, u"UNIVERSAL": 0, u"ZULU": 0,
                u"ASIA/BANGKOK": 420, u"ASIA/HO_CHI_MINH": 420, u"ASIA/SAIGON": 420, u"ASIA/JAKARTA": 420,
                u"ASIA/PHNOM_PENH": 420, u"ASIA/VIENTIANE": 420, u"ASIA/NOVOSIBIRSK": 420,
                u"ASIA/KRASNOYARSK": 420, u"ASIA/SINGAPORE": 480, u"ASIA/KUALA_LUMPUR": 480,
                u"ASIA/SHANGHAI": 480, u"ASIA/HONG_KONG": 480, u"ASIA/MANILA": 480, u"ASIA/TOKYO": 540,
                u"ASIA/SEOUL": 540, u"ASIA/KOLKATA": 330, u"ASIA/CALCUTTA": 330, u"ASIA/DUBAI": 240,
                u"EUROPE/MOSCOW": 180}
PAST_DAYS = 180                          # дата без года дальше полугода в прошлом — это следующий год
_ZONE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}$")
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.I)
# «Sep 27, 9am (UTC)» · «Sep 27, 9:30am (UTC)» · «4am (Asia/Bangkok)» · «Sep 27 at 9am UTC»
_WORDS_RE = re.compile(
    r"^(?:(?P<mon>[a-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)?\s*"
    r"(?:\(\s*(?P<zp>[^()]{1,40}?)\s*\)|(?P<zb>utc|gmt))$", re.I)
_DOTTED_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s+UTC$", re.I)   # эпоха после vti.reset_of
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$", re.I)


def _tz_of(tz):
    """Пояс вывода: None → местный пояс ПК; число → часы смещения; timedelta; tzinfo."""
    if tz is None or isinstance(tz, _dt.tzinfo):
        return tz
    if isinstance(tz, _dt.timedelta):
        return _dt.timezone(tz)
    return _dt.timezone(_dt.timedelta(minutes=int(round(float(tz) * 60))))


def _aware(now):
    """«Сейчас» → aware-время. None → часы ПК; наивное — считается UTC."""
    if now is None:
        return _dt.datetime.now(_dt.timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=_dt.timezone.utc)
    return now


def _local(moment, tz=None):
    t = _tz_of(tz)
    return moment.astimezone(t) if t is not None else moment.astimezone()


def clock_words(now=None, tz=None):
    """Часы ПК словами: «23:28»."""
    loc = _local(_aware(now), tz)
    return u"%02d:%02d" % (loc.hour, loc.minute)


def provider_zone(name):
    """Имя пояса из слов поставщика → tzinfo | None (не знаем — не выдумываем)."""
    key = (name or u"").strip()
    up = key.upper()
    if up in (u"UTC", u"GMT", u"Z", u"ETC/UTC", u"ETC/GMT", u"UNIVERSAL", u"ZULU"):
        return _dt.timezone.utc
    m = _OFFSET_RE.match(key)
    if m:
        mins = int(m.group(2)) * 60 + int(m.group(3) or 0)
        if mins > 14 * 60:
            return None
        return _dt.timezone(_dt.timedelta(minutes=-mins if m.group(1) == u"-" else mins))
    if not _ZONE_NAME_RE.match(key):
        return None
    if _zoneinfo is not None:
        try:
            return _zoneinfo.ZoneInfo(key)
        except Exception:                                  # noqa: BLE001 — нет пояса в базе → запасная карта
            pass
    mins = _FIXED_ZONES.get(up)
    return _dt.timezone(_dt.timedelta(minutes=mins)) if mins is not None else None


def _with_year(now, tz, mon, day, h, mi):
    """Дата без года → год по правилу: год «сейчас»; дальше полугода в прошлом — следующий год; дальше
    полугода в будущем — прошлый (сразу после Нового года «Dec 31» — это вчера, а не через год). Такого
    дня нет ни в одном из трёх лет («Feb 30», «Feb 29» вне високосного окна) → None."""
    year = now.astimezone(tz).year
    lo = now - _dt.timedelta(days=PAST_DAYS)
    hi = now + _dt.timedelta(days=366 - PAST_DAYS)
    for y in (year, year + 1, year - 1):
        try:
            cand = _dt.datetime(y, mon, day, h, mi, tzinfo=tz)
        except (ValueError, OverflowError):
            continue
        if lo <= cand < hi:
            return cand
    return None


def reset_moment(reset, now=None):
    """Слова сброса поставщика → момент (aware datetime) | None. Чистая функция («сейчас» — параметр).

    Формы: «Sep 27, 9am (UTC)», «Sep 27, 9:30am (UTC)», «Sep 29, 4am (Asia/Bangkok)», «4am (Asia/Bangkok)»
    (без даты — ближайшее такое время ПОСЛЕ «сейчас»), эпоха «limit reached|1758963600» и её след у
    `vti.reset_of` («27.09 09:00 UTC»), ISO с поясом. Пояс неизвестен, форма чужая или год вне 1971–2999
    (мусор, который часы ПК не переведут) → None. Не бросает."""
    try:
        m = _reset_moment(reset, now)
    except (OverflowError, OSError, ValueError):
        return None
    if m is None or not 1971 <= m.year <= 2999:
        return None
    return m


def _reset_moment(reset, now):
    s = u" ".join((u"%s" % (reset or u"")).split())
    if not s:
        return None
    now = _aware(now)
    m = re.search(r"limit reached\|(\d{9,11})\b", s, re.I) or re.fullmatch(r"(\d{9,11})", s)
    if m:
        try:
            return _dt.datetime.fromtimestamp(int(m.group(1)), _dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    m = _DOTTED_RE.match(s)
    if m:
        return _with_year(now, _dt.timezone.utc, int(m.group(2)), int(m.group(1)),
                          int(m.group(3)), int(m.group(4)))
    if _ISO_RE.match(s):
        try:
            return _dt.datetime.fromisoformat(s[:-1] + u"+00:00" if s[-1] in u"Zz" else s)
        except ValueError:
            return None
    m = _WORDS_RE.match(s)
    if not m:
        return None
    tz = provider_zone(m.group("zp") or m.group("zb"))
    if tz is None:
        return None
    h, mi = int(m.group("h")), int(m.group("mi") or 0)
    ap = (m.group("ap") or u"").lower().replace(u".", u"")
    if not ap and m.group("mi") is None:
        return None                                        # «9 (UTC)» — час без am/pm и минут не судим
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == u"pm" else 0)
    elif h > 23:
        return None
    if mi > 59:
        return None
    if m.group("mon"):
        mon = _MONTHS.get(m.group("mon")[:3].lower())
        if mon is None:
            return None
        return _with_year(now, tz, mon, int(m.group("day")), h, mi)
    base = now.astimezone(tz)
    cand = base.replace(hour=h, minute=mi, second=0, microsecond=0)
    if cand <= base:
        cand = cand + _dt.timedelta(days=1)
    return cand


def when_words(moment, now=None, tz=None):
    """Момент → местное время ПК словами: «16:00 сегодня» · «04:00 завтра» · «вс 27.09 16:00»."""
    now = _aware(now)
    loc = _local(moment, tz)
    today = _local(now, tz).date()
    hm = u"%02d:%02d" % (loc.hour, loc.minute)
    if loc.date() == today:
        return u"%s сегодня" % hm
    if loc.date() == today + _dt.timedelta(days=1):
        return u"%s завтра" % hm
    year = u"" if loc.year == today.year else u".%d" % loc.year
    return u"%s %02d.%02d%s %s" % (WEEKDAYS[loc.weekday()], loc.day, loc.month, year, hm)


def reset_words(reset, now=None, tz=None):
    """Слова сброса поставщика → местное время ПК словами; не разобрали или часы ПК его не переводят — ЕГО
    словами в «»; сброса нет — "". Не бросает: время сброса не смеет уронить ответ."""
    s = u" ".join((u"%s" % (reset or u"")).split())
    if not s:
        return u""
    try:
        m = reset_moment(s, now)
        if m is not None:
            return when_words(m, now, tz)
    except (OverflowError, OSError, ValueError):          # часы ПК не переводят момент — слова поставщика
        pass
    return u"«%s»" % s[:60]


K_OK, K_LIMIT, K_DENIED, K_NODIR, K_EMPTY, K_UNKNOWN, K_NONE = (
    "ok", "limit", "denied", "nodir", "empty", "unknown", "none")


def status_kind(row):
    """Строка пробы → вид исхода. Код поставщика старше слова; «нет каталога»/«пуст» — слова проб."""
    if not row:
        return K_NONE
    code, word = row.get("code"), row.get("word")
    if code == 200 or (code is None and word == vti.GREEN):
        return K_OK
    if code == 429 or (code is None and word == vti.LIMIT):
        return K_LIMIT
    if code == 401 or (code is None and word == vti.DENIED):
        return K_DENIED
    if word == u"нет каталога":
        return K_NODIR
    if word == u"пуст":
        return K_EMPTY
    return K_UNKNOWN


def status_icon_text(row, now=None, tz=None):
    """Строка пробы → (значок, слова): ✅ работает · ⏳ лимит до … · ❌ вход не принят · ❔ нет ответа."""
    k = status_kind(row)
    if k == K_OK:
        return u"✅", u"работает"
    if k == K_LIMIT:
        when = reset_words(row.get("reset"), now, tz)
        return u"⏳", (u"лимит до %s" % when) if when else u"лимит"
    if k == K_DENIED:
        return u"❌", u"вход не принят"
    if k == K_NODIR:
        return u"❌", u"профиля нет на ПК"
    if k == K_EMPTY:
        return u"❌", u"вход пустой"
    if k == K_NONE:
        return u"❔", u"не проверено"
    return u"❔", u"нет ответа"


def status_words(row, now=None, tz=None):
    return u"%s %s" % status_icon_text(row, now, tz)


def _secs_words(secs):
    return u"%d с" % secs if secs < 60 else u"%d мин" % max(1, int(round(secs / 60.0)))


def silent_why(row):
    """Почему проба «не ответила» — коротко словами (или "", если сказать нечего). Кодов и имён ошибок
    наружу нет."""
    if not row:
        return u"проверки не было"
    code = row.get("code")
    if code == 529:
        return u"Claude перегружен"
    if isinstance(code, int) and code >= 500:
        return u"сбой у Claude"
    if isinstance(code, int):
        return u"Claude отказал"
    text = u"%s" % (row.get("text") or u"")
    m = re.match(u"claude не ответил за (\\d+) с", text)
    if m:
        return u"Claude не ответил за %s" % _secs_words(int(m.group(1)))
    for head, words in ((u"claude не найден", u"Claude не найден на ПК"),
                        (u"claude не запустился", u"Claude не запустился"),
                        (u"каталог ", u"профиль не проверить"),
                        (u"канал", u"нет связи с сервером")):
        if text.startswith(head):
            return words
    return u""


def brief_words(row):
    """Совсем коротко, для скобок: работает · в лимите · вход не принят · профиля нет · пустой · без ответа."""
    return {K_OK: u"работает", K_LIMIT: u"в лимите", K_DENIED: u"вход не принят", K_NODIR: u"профиля нет",
            K_EMPTY: u"пустой"}.get(status_kind(row), u"без ответа")


def probe_words(row, now=None, tz=None):
    """Исход пробы для строки хода («ПК: …», «Сервер: проверка — …»): как `status_icon_text`, но «профиля
    нет» без «на ПК» и с причиной молчания в скобках, если её можно назвать."""
    k = status_kind(row)
    icon, text = status_icon_text(row, now, tz)
    if k == K_NODIR:
        return icon, u"профиля нет"
    if k in (K_UNKNOWN, K_NONE):
        why = silent_why(row)
        return icon, u"%s%s" % (text, (u" (%s)" % why) if why else u"")
    return icon, text


def subject_phrase(who, row, now=None, tz=None):
    """Учётка + исход пробы → (значок, фраза): «samfold в лимите до …», «вход samfold не принят»."""
    k = status_kind(row)
    if k == K_OK:
        return u"✅", u"%s работает" % who
    if k == K_LIMIT:
        when = reset_words((row or {}).get("reset"), now, tz)
        return u"⏳", u"%s в лимите%s" % (who, (u" до %s" % when) if when else u"")
    if k == K_DENIED:
        return u"❌", u"вход %s не принят" % who
    if k == K_NODIR:
        return u"❌", u"профиля %s нет" % who
    if k == K_EMPTY:
        return u"❌", u"вход %s пустой" % who
    why = silent_why(row)
    return u"❔", u"%s не ответила%s" % (who, (u" (%s)" % why) if why else u"")


def her_words(row, now=None, tz=None):
    """Исход пробы учётки, уже названной шапкой ответа → (значок, «у неё лимит до …» / «её вход не принят» /
    «она не ответила (…)»)."""
    k = status_kind(row)
    if k == K_OK:
        return u"✅", u"она работает"
    if k == K_LIMIT:
        when = reset_words((row or {}).get("reset"), now, tz)
        return u"⏳", u"у неё лимит%s" % ((u" до %s" % when) if when else u"")
    if k == K_DENIED:
        return u"❌", u"её вход не принят"
    if k == K_NODIR:
        return u"❌", u"её профиля нет"
    if k == K_EMPTY:
        return u"❌", u"её вход пустой"
    why = silent_why(row)
    return u"❔", u"она не ответила%s" % ((u" (%s)" % why) if why else u"")


def on_her_words(row, now=None, tz=None):
    """Для итога «сервер на ней, но …»: «в лимите до …» · «вход не принят» · «проверка без ответа»."""
    k = status_kind(row)
    if k == K_LIMIT:
        when = reset_words((row or {}).get("reset"), now, tz)
        return u"в лимите%s" % ((u" до %s" % when) if when else u"")
    if k == K_DENIED:
        return u"вход не принят"
    if k == K_EMPTY:
        return u"вход пустой"
    return u"проверка без ответа"


# Метка, которая ничего не добавляет к номеру («учётка 3», «третья», «3-я»), на экран не идёт: «№3 третья»
# читается как заикание. «основная» и свои слова (samfold) — смысл, их показываем. Данные реестра те же.
_ORDINAL_LABELS = {1: (u"первая",), 2: (u"вторая",), 3: (u"третья",), 4: (u"четвертая",), 5: (u"пятая",),
                   6: (u"шестая",), 7: (u"седьмая",), 8: (u"восьмая",), 9: (u"девятая",), 10: (u"десятая",)}


def _default_label(n, label):
    v = (label or u"").strip().lower().replace(u"ё", u"е")
    return (not v or v == u"учетка %d" % n or v in (u"%d-я" % n, u"%dя" % n)
            or v in _ORDINAL_LABELS.get(n, ()))


def account_name(n, row=None):
    """«№3 samfold»; метка, которая ничего не добавляет к номеру («учётка 3», «третья»), — просто «№3»."""
    label = (u"%s" % ((row or {}).get("label") or u"")).strip()
    return u"№%d" % n if _default_label(n, label) else u"№%d %s" % (n, label)


def _profile_base(profile):
    base = re.split(r"[\\/]", (u"%s" % profile).rstrip(u"\\/"))[-1] or u"%s" % profile
    m = re.match(r"(?i)^claude_profile_(\S+)$", base)
    return m.group(1) if m else base


def profile_words(profile, prep=False, accs=None):
    """Профиль ПК, которого нет в реестре, словами: «основной профиль» / «профиль 3» (каталог
    claude_profile_3; prep=True — «основном профиле» / «профиле 3»). Пути целиком наружу не идут.
    Если короткое имя совпало с номером учётки реестра, у которой ДРУГОЙ каталог («профиль 3» рядом с №3,
    чей профиль не claude_profile_3), — имя каталога целиком, чтобы не спутать."""
    if not profile or profile == accounts.WORD_MAIN:
        return u"основном профиле" if prep else u"основной профиль"
    short = _profile_base(profile)
    num = accounts.parse_number(short) if accs else None
    if num is not None and num in accs and _pkey(accs[num].get("profile")) != _pkey(profile):
        short = re.split(r"[\\/]", (u"%s" % profile).rstrip(u"\\/"))[-1] or short
    return (u"профиле %s" if prep else u"профиль %s") % short


def _pkey(profile):
    """Один ключ профиля для всех сравнений: регистр, «\\» и «/», хвостовой разделитель — не различия
    (один и тот же каталог, записанный по-разному, не должен стать «не в реестре»)."""
    p = u"%s" % (profile or accounts.WORD_MAIN)
    if p == accounts.WORD_MAIN:
        return p
    return os.path.normcase(os.path.normpath(p))


def account_of_profile(accs, profile):
    """Профиль ПК → номер учётки реестра с тем же каталогом (ключ `_pkey`) | None."""
    key = _pkey(profile)
    for n in sorted(accs or {}):
        if _pkey(accs[n].get("profile")) == key:
            return n
    return None


def registry_short_reason(reg):
    """Почему реестр не читается — два-три слова, без имени файла."""
    r = reg.reason or u""
    if u"не JSON" in r:
        return u"файл испорчен"
    if u"не прочитан" in r:
        return u"файл не открылся"
    return u"не разобран"


# ═══════════════════ чистые сборщики слов ═══════════════════

RcChoice = namedtuple("RcChoice", "value note broken")


def rc_choice(repo=None, isdir=None):
    """Что действует у RC — тем же порядком, что `rc_supervisor.child_env`: свой файл; его нет — МОСТ
    на прежний файл дерева (как до 25.09); не решает ни один → ОСНОВНОЙ (RC-страховка).
    → RcChoice(профиль, откуда словами, broken — свой/прежний файл есть, но не разобран)."""
    raw, err = profile_choice.read_raw(path=os.path.join(repo or REPO, RC_CHOICE_REL))
    src = u"свой файл %s" % RC_CHOICE_REL
    if raw is None and not err:
        raw, err = profile_choice.read_raw(repo=repo or REPO)
        src = u"своего файла нет — прежний файл %s" % profile_choice.CHOICE_REL
        if raw is None and not err:
            return RcChoice(accounts.WORD_MAIN, u"ни своего файла, ни прежнего — RC-страховка", False)
    d = profile_choice.decide(raw, err, isdir)
    if d.action == profile_choice.ACT_DROP:
        return RcChoice(accounts.WORD_MAIN, src, False)
    if d.action == profile_choice.ACT_SET:
        return RcChoice(d.value, src, False)
    return RcChoice(accounts.WORD_MAIN, u"%s не разобран: %s — RC-страховка" % (src, d.reason), True)


def rc_choice_words(repo=None, isdir=None):
    """То же для консоли: «профиль (откуда)»."""
    c = rc_choice(repo, isdir)
    return u"%s (%s)" % (c.value, c.note)


def _code_words(row):
    if not row:
        return u"проб не было"
    code = row.get("code")
    head = u"%s" % code if code is not None else row.get("word") or PC_UNKNOWN
    if code == 200:
        head = u"200 жива"
    elif code == 429:
        head = u"429 лимит"
    elif code == 401:
        head = u"401 вход не принят"
    tail = (u", сброс «%s»" % row["reset"]) if row.get("reset") else u""
    return head + tail


def srv_map(rows):
    """Строки пробы сервера → ({буква: строка}, [буквы, чьё значение = действующему], строка действующего)."""
    by_letter, active_letters, active_row = {}, [], None
    for r in rows or []:
        name = r.get("name") or u""
        if name == vti.NAME_ACTIVE:
            active_row = r
            continue
        if name.startswith(vti.SLOT_PREFIX):
            letter = name[len(vti.SLOT_PREFIX):]
            by_letter[letter] = r
            if r.get("active") == "1":
                active_letters.append(letter)
    return by_letter, sorted(active_letters), active_row


def retry_line(by_letter, active_letters, owner_of, accs):
    u"""Строка про СЕРВЕРНЫЙ повтор задачи при лимите (`limit_slot`, 20.09). Демон, упёршись в лимит на входе
    A или B, один раз повторяет упавшую задачу под вторым из них; действующий вход при этом не меняется
    (его меняет только слово владельца). Строка — только когда повтор правда возможен: действующий вход —
    ровно A или B, второй заполнен. И по-честному: второй вход тоже в лимите или не пускает — подстраховки
    нет, и это сказано, а не обещано. Чистая функция."""
    if len(active_letters) != 1 or active_letters[0] not in (u"A", u"B"):
        return u""
    other = u"B" if active_letters[0] == u"A" else u"A"
    row = by_letter.get(other)
    if not row or not row.get("called"):
        return u""
    n = owner_of.get(other)
    who = u"вход %s%s" % (other, (u" (%s)" % account_name(n, accs.get(n))) if n is not None else u"")
    head = u"Если у сервера кончится лимит, "
    k = status_kind(row)
    if k == K_OK:
        return head + u"упавшую задачу он один раз повторит через %s." % who
    if k == K_LIMIT:
        return head + u"подстраховки нет: %s тоже в лимите." % who
    if k in (K_DENIED, K_EMPTY, K_NODIR):
        return head + u"подстраховки нет: %s не работает." % who
    return head + u"подстраховка под вопросом: %s не ответил." % who


def slot_status_words(row, now=None, tz=None):
    """Статус ВХОДА сервера, когда подлежащее уже «вход X»: «✅ работает» · «⏳ лимит до …» · «❌ не принят»."""
    k = status_kind(row)
    if k == K_DENIED:
        return u"❌ не принят"
    if k == K_EMPTY:
        return u"❌ пустой"
    return status_words(row, now, tz)


_STUCK = {K_LIMIT: u"лимит", K_DENIED: u"вход не принят", K_NODIR: u"профиля нет", K_EMPTY: u"вход пустой"}


def builders_warn_words(reg, choice, where, row=None):
    """Строители не на своей учётке (реестр есть, а выбор ушёл с предупреждением) → строка ⚠️ | "".

    `where` — где строители на деле, тем же именем, что в строке «Строители ПК — …» (предложный падеж);
    `row` — проба того, чем они идут: там лимит или вход не принят — строители стоят, и это сказано."""
    if not choice.warn or reg.state != accounts.ST_OK:
        return u""
    accs = reg.data["accounts"]
    n = choice.number
    stuck = _STUCK.get(status_kind(row)) if row is not None else None
    tail = (u"; там %s — строители стоят" % stuck) if stuck else u""
    if n is None:
        return u"⚠️ За строителями учётка не закреплена — пока они на %s%s. Закрепить: «учётка N»." % (where, tail)
    if n not in accs:
        return u"⚠️ Строители записаны на №%d, а её в реестре нет — пока они на %s%s." % (n, where, tail)
    if u"не проверить" in (choice.reason or u""):
        return u"⚠️ Профиль %s не проверить — строители пока на %s%s." % (account_name(n, accs[n]), where, tail)
    return u"⚠️ Профиля %s на ПК нет — строители пока на %s%s." % (account_name(n, accs[n]), where, tail)


_DISPUTE_ERR_RE = re.compile(u"^слот ([A-Z]) назван у (№\\d+(?:, №\\d+)*)")


def disputes_of(reg):
    """Спорные входы реестра → {буква: [номера учёток]} (строка проверки реестра и след снятого слота)."""
    out = {}
    if reg.state != accounts.ST_OK:
        return out
    for err in reg.errors or []:
        m = _DISPUTE_ERR_RE.match(u"%s" % err)
        if m:
            out[m.group(1)] = [int(x) for x in re.findall(u"№(\\d+)", m.group(2))]
    for n, row in reg.data["accounts"].items():
        m = re.search(u"слот ([A-Z])", row.get("slot_error") or u"")
        if m and n not in out.setdefault(m.group(1), []):
            out[m.group(1)].append(n)
    return dict((k, sorted(v)) for k, v in out.items())


def _and(items):
    items = list(items)
    if len(items) < 2:
        return u"".join(items)
    return u"%s и %s" % (u", ".join(items[:-1]), items[-1])


def _reg_error_words(err):
    e = re.sub(u"^№(\\d+): ", u"№\\1 — ", u"%s" % err)
    return re.sub(u"^в реестре ", u"", e)


def _rc_of(rc_words):
    """RcChoice | прежняя строка «профиль (откуда)» → RcChoice."""
    if isinstance(rc_words, RcChoice):
        return rc_words
    s = u"%s" % (rc_words or accounts.WORD_MAIN)
    return RcChoice(s.split(u" (", 1)[0].strip() or accounts.WORD_MAIN, s, u"не разобран" in s)


def _now_and_clock(now, tz):
    """now: datetime | None | прежняя готовая строка часов → (aware «сейчас», часы словами)."""
    if isinstance(now, _dt.datetime):
        dt = _aware(now)
        return dt, clock_words(dt, tz)
    dt = _aware(None)
    return dt, (u"%s" % now if now else clock_words(dt, tz))


def report_text(reg, pc_rows, srv, choice, rc_words, now=None, tz=None):
    """Реестр + пробы → ответ «учётки». Чистая функция (всё, что нужно, приходит параметрами).

    Блоки через пустую строку: шапка с часами ПК · ⚠️ реестра нет / не читается · по строке на учётку (как
    она на ПК и, если у неё есть вход сервера, какой и как он) · кто на какой сейчас (строители ПК, телефон,
    сервер), что ещё есть на сервере и есть ли у него подстраховка · ⚠️ то, что владелец может поправить.
    Пустой блок не печатается. `rc_words` — RcChoice (или прежняя строка консоли)."""
    now, clock = _now_and_clock(now, tz)
    rc = _rc_of(rc_words)
    reg_ok = reg.state == accounts.ST_OK
    accs = reg.data["accounts"] if reg_ok else {}
    srv_ok = bool(srv.get("ok"))
    by_letter, active_letters, active_row = srv_map(srv.get("rows"))
    owner_of = dict((row["slot"], n) for n, row in accs.items() if row.get("slot"))
    disputes = disputes_of(reg)

    def sw(row):
        return status_words(row, now, tz)

    def slot_sw(row):
        return slot_status_words(row, now, tz)

    top, lines, lanes, warns = [], [], [], []
    if reg.state == accounts.ST_ABSENT:
        top.append(u"⚠️ Реестра учёток нет — скажи «учётки заведи».")
    elif not reg_ok:
        top.append(u"⚠️ Реестр учёток не читается (%s) — поправить на ПК (посмотреть: accounts_run.py --show)."
                   % registry_short_reason(reg))

    # ── по строке на учётку ──
    for n, row in sorted(accs.items()):
        name = account_name(n, row)
        pc = pc_rows.get(n)
        pi, pt = status_icon_text(pc, now, tz)
        if row.get("slot_error"):
            lines.append(u"%s — %s %s · вход спорный" % (name, pi, pt))
            continue
        if not row.get("slot"):
            lines.append(u"%s — %s %s · только ПК" % (name, pi, pt))
            continue
        letter = row["slot"]
        sr = by_letter.get(letter) if srv_ok else None
        if sr is not None and status_icon_text(sr, now, tz) == (pi, pt):
            lines.append(u"%s — %s %s · ПК и сервер (вход %s)" % (name, pi, pt, letter))
        else:
            ss = u"❔ не проверен" if not srv_ok else (u"❌ не найден" if sr is None else slot_sw(sr))
            if status_kind(pc) == K_NODIR:
                pt = u"профиля нет"
            si, st = ss.split(u" ", 1)
            lines.append(u"%s — %s ПК: %s · %s сервер (вход %s): %s" % (name, pi, pt, si, letter, st))
        if sr is not None and probes_disagree(pc, sr):
            warns.append(disagree_words(n, pc, letter, sr, name))

    # ── кто на какой сейчас ──
    known = {}                                   # профиль ПК → его проба (для профилей вне реестра)
    for n, row in accs.items():
        if pc_rows.get(n) is not None:
            known[_pkey(row["profile"])] = pc_rows[n]
    if pc_rows.get("main") is not None:
        known[_pkey(pc_rows["main"].get("profile"))] = pc_rows["main"]

    def lane(profile, num=None):
        """→ (ключ сравнения, имя, имя в предложном падеже, проба | None)."""
        n = num if num is not None else account_of_profile(accs, profile)
        if n is not None:
            nm = account_name(n, accs[n])
            return n, nm, nm, pc_rows.get(n)
        key = _pkey(profile)
        return ((u"профиль", key), profile_words(profile, accs=accs) + (u" (не в реестре)" if reg_ok else u""),
                profile_words(profile, prep=True, accs=accs), known.get(key))

    def lane_words(key, name, row):
        # у учётки реестра статус уже стоит в её строке; у профиля вне реестра — только здесь
        return name if (isinstance(key, int) or row is None) else u"%s: %s" % (name, sw(row))

    b_prof = choice.value if (choice.action == accounts.ACT_SET and choice.value) else accounts.WORD_MAIN
    b_num = choice.number if (choice.number is not None and not choice.warn and choice.number in accs) else None
    b_key, b_name, b_where, b_row = lane(b_prof, b_num)
    r_key, r_name, r_where, r_row = lane(rc.value)

    def tag(x):
        return u" (спорный)" if x in disputes else (u" (ничей)" if reg_ok else u"")

    s_key, s_line = None, u""
    if not srv_ok:
        s_line = u"❔ Сервер не ответил — его входы не проверены."
    elif len(active_letters) == 1 and active_letters[0] in owner_of:
        s_key = owner_of[active_letters[0]]
        s_line = u"Сервер — %s" % account_name(s_key, accs[s_key])
    elif len(active_letters) == 1:
        x = active_letters[0]
        s_key = (u"вход", x)
        s_line = u"Сервер — вход %s%s: %s" % (x, tag(x), slot_sw(by_letter.get(x) or active_row))
    elif active_letters:
        s_key = (u"вход", tuple(active_letters))
        parts = [(u"вход %s (%s)" % (x, account_name(owner_of[x], accs[owner_of[x]]))) if x in owner_of
                 else (u"вход %s%s" % (x, tag(x))) for x in active_letters]
        s_line = u"Сервер — %s (у них одно значение): %s" % (_and(parts), slot_sw(active_row))
    else:
        s_key = (u"вход", None)
        s_line = u"Сервер — незнакомый вход%s: %s" % (
            (u" (не совпал ни с %s)" % u", ни с ".join(sorted(by_letter))) if by_letter else u"", slot_sw(active_row))
    if isinstance(b_key, int) and b_key == r_key == s_key:
        lanes.append(u"Сейчас всё на %s: строители ПК, телефон, сервер." % b_name)
    elif b_key == r_key:
        lanes.extend([u"Строители ПК и телефон — %s" % lane_words(b_key, b_name, b_row), s_line])
    else:
        lanes.extend([u"Строители ПК — %s" % lane_words(b_key, b_name, b_row),
                      u"Телефон — %s" % lane_words(r_key, r_name, r_row), s_line])
    if srv_ok:
        for x in sorted(set(by_letter) - set(owner_of) - set(active_letters)):
            who = u" (спорный)" if x in disputes else (u", ничей" if reg_ok else u"")
            lanes.append(u"На сервере есть ещё вход %s%s: %s." % (x, who, slot_sw(by_letter[x])))
        retry = retry_line(by_letter, active_letters, owner_of, accs)
        if retry:
            lanes.append(retry)
        if srv.get("busy"):
            lanes.append(u"На сервере сейчас идёт задача — пока она не кончится, «учётка N» сервер не переключит.")

    # ── что владелец может поправить ──
    for letter, nums in sorted(disputes.items()):
        names = [account_name(x, accs[x]) if x in accs else u"№%d" % x for x in nums]
        warns.append(u"⚠️ Вход сервера %s записан сразу за %s — %s сервер не тронут."
                     % (letter, _and(names), _and(u"«учётка %d»" % x for x in nums)))
        warns.append(u"Оставь вход %s за одной из них — на ПК: accounts_run.py --screen, шаг 3." % letter)
    for err in reg.errors:
        if not _DISPUTE_ERR_RE.match(u"%s" % err):          # спорный вход уже сказан строкой выше
            warns.append(u"⚠️ В реестре: %s." % _reg_error_words(err).rstrip(u"."))
    bw = builders_warn_words(reg, choice, b_where, b_row)
    if bw:
        warns.append(bw)
    if rc.broken:
        warns.append(u"⚠️ Не читается, какую учётку выбрал телефон, — он пока на %s." % r_where)
    blocks = [[u"👥 Учётки · %s" % clock], top, lines, lanes, warns]
    return u"\n\n".join(u"\n".join(b) for b in blocks if b)


# ═══════════════════ ходы ═══════════════════

def report(runner=None, srv_probe=None, isdir=None, repo=None, save=True, now=None, tz=None):
    """«учётки»: пробы всех профилей ПК и всех слотов сервера → текст. Последние ответы — на диск."""
    reg = accounts.load(repo)
    pc_rows = {}
    for n, row in sorted(reg.data["accounts"].items()):
        pc_rows[n] = pc_probe(row["profile"], runner=runner, isdir=isdir)
    choice0 = accounts.builders_effective(repo, isdir=isdir)
    eff = choice0.value if (choice0.action == accounts.ACT_SET and choice0.value) else accounts.WORD_MAIN
    known = set(_pkey(row["profile"]) for row in reg.data["accounts"].values())
    if (choice0.number is None or choice0.warn) and _pkey(eff) not in known:
        # строители идут профилем, которого нет среди строк реестра (реестра нет — мост на прежний
        # файл; откат на ОСНОВНОЙ без его строки) — меряем ТО, чем они идут на самом деле
        pc_rows["main"] = dict(pc_probe(eff, runner=runner, isdir=isdir), profile=eff)
    try:
        srv = (srv_probe or vti.probe_all_slots)(work=WORK)
    except Exception as e:                            # noqa: BLE001 — сервер не смеет уронить отчёт ПК
        srv = {"ok": False, "words": u"проба сервера упала (%s)" % type(e).__name__, "rows": []}
    text = report_text(reg, pc_rows, srv, choice0, rc_choice(repo, isdir), now=now, tz=tz)
    if save:
        _remember(repo, pc_rows, srv)
    return text


def _remember(repo, pc_rows, srv):
    last = accounts.load_last(repo)
    for n, row in pc_rows.items():
        last["pc"][u"%s" % n] = row
    by_letter, active_letters, _ar = srv_map(srv.get("rows"))
    for letter, r in by_letter.items():
        last["srv"][letter] = {"code": r.get("code"), "word": r.get("word"), "reset": r.get("reset"),
                               "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    if srv.get("ok"):
        last["srv_active"] = u",".join(active_letters)
    try:
        accounts.save_last(last, repo)
    except Exception:                                 # noqa: BLE001 — память не смеет уронить ответ
        pass


def _prev_words(reg, before):
    """Где строители сейчас → слова для «остались на …»/«были на …»: «№1 mxfill» / «основном профиле»."""
    accs = reg.data["accounts"]
    if before.number is not None and not before.warn and before.number in accs:
        return account_name(before.number, accs[before.number])
    prof = before.value if (before.action == accounts.ACT_SET and before.value) else accounts.WORD_MAIN
    n = account_of_profile(accs, prof)
    return account_name(n, accs[n]) if n is not None else profile_words(prof, prep=True, accs=accs)


def _trim(text, limit=120):
    one = u" ".join((u"%s" % (text or u"")).split())
    return one if len(one) <= limit else one[:limit - 1] + u"…"


_BAK_RE = re.compile(r"\.bak-(\d{8})-(\d{6})Z$")


def copy_words(bak, now=None, tz=None):
    """Копия файла входов на сервере (её делает `use_slot`, не удаляет никогда — их там может быть много) →
    «копии от 23:28 (.bak-20260925-162800Z)»: время по часам ПК и хвост имени, по которому её не спутать с
    другими. Имени нет — «самой свежей копии (около 23:28)» (копию делает этот же ход, минуты назад)."""
    b = u"%s" % (bak or u"")
    m = _BAK_RE.search(b)
    if not m:
        if b:
            return u"копии «%s»" % re.split(r"[\\/]", b)[-1]
        return u"самой свежей копии (около %s)" % clock_words(now, tz)
    try:
        made = _dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=_dt.timezone.utc)
        loc = _local(made, tz)
        today = _local(_aware(now), tz).date()
    except (ValueError, OverflowError, OSError):
        return u"копии %s" % m.group(0)
    when = (u"%02d:%02d" % (loc.hour, loc.minute) if loc.date() == today
            else u"%02d.%02d %02d:%02d" % (loc.day, loc.month, loc.hour, loc.minute))
    return u"копии от %s (%s)" % (when, m.group(0))


def use_words(res, num, letter, name=None, now=None, tz=None):
    u"""Итог `vti.use_slot` → (строки владельцу, исход словом). Чистая функция.

    Каждому этапу `use_slot` — свои слова; то, что требует руки владельца, идёт отдельной строкой ❗ с тем,
    ЧТО сделать (и из какой копии вернуть файл — копий на сервере много, не удаляется ни одна). Учётка названа
    шапкой ответа, поэтому здесь она — «она»/«на ней»; где «она» спуталась бы с «прежней», стоит имя.
    Исходы: on · on_same · busy · busy_unknown · rolled · empty · missing · unreached · already_bad ·
    kept_new · stuck · unknown. Незнакомый этап — короткая строка и первая строка двери, обрезанная."""
    stage = u"%s" % (res.get("stage") or u"")
    raw = [u"%s" % x for x in (res.get("lines") or [])]
    said = u" ".join(raw)
    row = res.get("row")
    who = name or u"№%d" % num
    again = u"повтори «учётка %d» позже" % num
    restart_cmd = u"systemctl restart %s" % vti.UNIT
    copy = copy_words(res.get("backup"), now, tz)
    if stage in (u"slot_name", u"address", u"preflight"):
        return [u"❔ Сервер: не достучался — ничего не менял."], u"unreached"
    if stage == u"error":
        return [u"❔ Сервер: переключение оборвалось ошибкой — что с сервером, неизвестно; проверь «учётки»."], \
            u"unknown"
    if stage == u"slot_empty":
        if u"в файле не назван" in said:
            return [u"❌ Сервер: входа %s на сервере нет — не переключал." % letter], u"missing"
        return [u"❌ Сервер: вход %s пустой — не переключал." % letter], u"empty"
    if stage == u"busy":
        if u"не проверено" in said:
            return [u"⏸ Сервер: не вышло проверить, свободен ли он, — не переключал; %s." % again], u"busy_unknown"
        return [u"⏸ Сервер: сейчас выполняет задачу — не переключал; %s." % again], u"busy"
    if stage == u"busy_late":
        if u"начался заход" in said:
            head, state = u"⏸ Сервер: пока проверял, он взял задачу — не перезапускал", u"busy"
        else:
            head, state = u"⏸ Сервер: после проверки не вышло убедиться, что он свободен, — не перезапускал", \
                u"busy_unknown"
        if res.get("rolled_back"):
            return [u"%s, вернул как было; %s." % (head, again)], state
        return [u"%s; %s." % (head, again),
                u"ℹ️ Новая учётка на сервере уже записана (проверка — работает) и включится при следующем "
                u"перезапуске."], state
    if stage == u"already":
        if res.get("restarted"):
            if res.get("ok"):
                return [u"✅ Сервер: уже был записан на неё, перезапущен — работает."], u"on_same"
            return [u"❌ Сервер: уже был записан на неё, но после перезапуска не поднялся.",
                    u"❗ Нужна рука на сервере: %s." % restart_cmd], u"stuck"
        if res.get("ok"):
            return [u"✅ Сервер: и так на ней, проверка — работает."], u"on_same"
        if status_kind(row) == K_OK:
            if u"идёт заход" in said:
                return [u"⏸ Сервер занят задачей и пока на прежней учётке (новая уже записана). "
                        u"Повтори «учётка %d» позже." % num], u"busy"
            return [u"⏸ Сервер пока на прежней учётке (новая уже записана), а свободен ли он — не проверить. "
                    u"Повтори «учётка %d» позже." % num], u"busy_unknown"
        icon, text = her_words(row, now, tz)
        if (row or {}).get("file_after_start") is True:
            # Файл входов сменили ПОСЛЕ старта демона: сервер ещё работает на прежней учётке, а в файле уже
            # она — с отказом. Следующий перезапуск (свой, по сбою или чужой) поднимет его на негодном входе.
            return [u"⚠️ Сервер пока на прежней учётке, но в файле входов уже она, а %s — перезапуск сервера "
                    u"поднимет его на ней." % text,
                    u"❗ До перезапуска сервера переведи его словом «учётка N» на рабочую учётку."], u"stuck"
        return [u"%s Сервер: и так на ней, но %s." % (icon, text)], u"already_bad"
    if stage == u"write":
        if u"Копия не сошлась" in said:
            return [u"❌ Сервер: записать не вышло — ничего не поменялось."], u"rolled"
        if u"ОТКАЗ на записи: канал:" in said or u"(канал оборвался)" in said:
            if res.get("rolled_back"):
                return [u"❔ Сервер: связь оборвалась при записи — вернул как было."], u"rolled"
            if u"запись до файла не дошла" in said:
                return [u"❔ Сервер: связь оборвалась до записи — ничего не поменялось."], u"rolled"
            return [u"❔ Сервер: связь оборвалась при записи — поменялся ли файл входов, неизвестно, а вернуть "
                    u"не вышло.",
                    u"❗ Проверь «учётки»: если сервер не на прежней учётке — вручную на сервере вернуть файл "
                    u"входов из %s." % copy], u"unknown"
        if res.get("backup"):
            if res.get("rolled_back"):
                return [u"❌ Сервер: записать не вышло — вернул как было."], u"rolled"
            return [u"❌ Сервер: записать не вышло, и вернуть как было — тоже.",
                    u"❗ Вручную на сервере: вернуть файл входов из %s." % copy], u"stuck"
        return [u"❌ Сервер: записать не вышло — ничего не поменялось."], u"rolled"
    if stage == u"probe":
        icon, text = probe_words(row, now, tz)
        _hi, her = her_words(row, now, tz)
        if not res.get("rolled_back"):
            return [u"❌ Сервер: %s, а вернуть прежнюю учётку не вышло — в файле сервера остался её вход." % her,
                    u"❗ Вручную на сервере: вернуть файл входов из %s." % copy], u"stuck"
        if u"НО демон мог" in said:
            if u"заходов демона:" in said:
                when = u"Когда закончится текущая задача, перезапусти сервер"
            else:
                when = u"Перезапусти сервер, когда он освободится (сейчас это не проверить)"
            return [u"⚠️ Сервер: %s — вернул как было, но сервер мог успеть её подхватить." % her,
                    u"❗ %s: %s." % (when, restart_cmd)], u"stuck"
        if res.get("restarted"):
            if u"демон не поднялся" in said:
                return [u"❌ Сервер лежит: %s, прежнюю учётку вернул, но после перезапуска сервер не поднялся."
                        % her, u"❗ Нужна рука на сервере: %s." % restart_cmd], u"stuck"
            return [u"%s Сервер: проверка — %s, вернул как было (сервер перезапущен на прежней учётке)."
                    % (icon, text)], u"rolled"
        return [u"%s Сервер: проверка — %s, вернул как было." % (icon, text)], u"rolled"
    if stage == u"restart":
        back = u"поднят обратно: демон активен" in said
        if res.get("rolled_back") and back:
            return [u"❌ Сервер: после перезапуска не поднялся — вернул прежнюю учётку и поднял обратно."], u"rolled"
        if res.get("rolled_back"):
            return [u"❌ Сервер: после перезапуска не поднялся — вернул прежнюю учётку, но поднять не вышло.",
                    u"❗ Нужна рука на сервере: %s." % restart_cmd], u"stuck"
        if back:
            # restore() зовёт отказом и обрыв канала ПОСЛЕ копирования файла: вернулась ли прежняя учётка, не
            # доказано ни в одну сторону — называем то, что знаем (сервер поднят), и куда смотреть.
            return [u"⚠️ Сервер не поднялся с первого раза, со второго поднялся; вернулась ли прежняя учётка — "
                    u"не подтвердилось.",
                    u"❗ Проверь «учётки»: на какой учётке сервер."], u"kept_new"
        return [u"❌ Сервер: после перезапуска не поднялся, и вернуть прежнюю учётку не вышло.",
                u"❗ Вручную на сервере: вернуть файл входов из %s и перезапустить: %s." % (copy, restart_cmd)], \
            u"stuck"
    if stage == u"done" and res.get("ok"):
        return [u"✅ Сервер: переключён и перезапущен, проверка — работает."], u"on"
    return [u"❔ Сервер: исход непонятен — проверь «учётки».",
            u"Подробности: %s" % _trim(raw[0] if raw else u"ответа нет")], u"unknown"


_SRV_PART = {u"on": u"сервер перешёл", u"on_same": u"сервер и так на ней",
             u"busy": u"сервер не переключён (занят задачей)",
             u"busy_unknown": u"сервер не переключён (не проверил, свободен ли)",
             u"rolled": u"сервер остался как был",
             u"empty": u"сервер не переключён (вход пустой)", u"missing": u"сервер не переключён (входа нет)",
             u"unreached": u"сервер не тронут (не достучался)", u"disputed": u"сервер не тронут (вход спорный)",
             u"noslot": u"входа на сервере у неё нет", u"stuck": u"сервер ждёт руки (см. ❗)",
             u"unknown": u"что с сервером — неизвестно"}


def switch(n, runner=None, use=None, isdir=None, repo=None, save=True, now=None, tz=None):
    """«учётка N»: обе полосы на учётку N. → (код, текст). Код 0 — обе стороны на N, 1 — часть,
    2 — отказ до всякого действия (реестра нет, номера нет)."""
    reg = accounts.load(repo)
    num = accounts.parse_number(n)
    if reg.state == accounts.ST_ABSENT:
        return 2, u"⛔ Реестра учёток ещё нет — сначала «учётки заведи». Ничего не изменено."
    if reg.state != accounts.ST_OK:
        return 2, (u"⛔ Реестр учёток не читается (%s) — переключить не могу.\nПоправь его на ПК (посмотреть: "
                   u"accounts_run.py --show). Ничего не изменено." % registry_short_reason(reg))
    accs = reg.data["accounts"]
    if num is None or num not in accs:
        asked = (u"№%d" % num) if num is not None else u"«%s»" % _trim(n, 20)
        have = u", ".join(account_name(x, accs[x]) for x in sorted(accs))
        return 2, u"⛔ Учётки %s нет. %s Ничего не изменено." % (
            asked, (u"Есть: %s." % have) if have else u"В реестре пока ни одной учётки.")
    row = accs[num]
    name = account_name(num, row)
    before = accounts.builders_choice(reg, isdir)
    prev = _prev_words(reg, before)
    already = before.number == num and not before.warn
    body = []
    # ── ПК ──
    pc = pc_probe(row["profile"], runner=runner, isdir=isdir)
    pc_ok = pc.get("code") == 200
    if pc_ok:
        try:
            accounts.set_builders(num, repo)
            if already:
                body.append(u"✅ ПК: строители и так на ней.")
            elif prev == name:
                body.append(u"✅ ПК: строители записаны на неё.")
            else:
                body.append(u"✅ ПК: строители перешли на неё (были на %s)." % prev)
        except Exception:                             # noqa: BLE001
            pc_ok = False
            body.append(u"❌ ПК: проверка прошла, но записать не вышло — строители остались на %s." % prev)
    else:
        icon, text = probe_words(pc, now, tz)
        body.append(u"%s ПК: %s — строители остались на %s." % (icon, text, prev))
    # ── сервер ──
    srv_ok, srv_row, srv_state = False, None, u"noslot"
    if row.get("slot_error"):
        m = re.search(u"слот ([A-Z])", row["slot_error"])
        body.append(u"⚠️ Сервер: вход%s записан и за другой учёткой — не трогал; поправь на ПК "
                    u"(accounts_run.py --screen, шаг 3)." % ((u" %s" % m.group(1)) if m else u""))
        srv_state = u"disputed"
    elif not row["slot"]:
        body.append(u"▫️ Сервер: у неё входа нет — не трогал.")
    else:
        try:
            res = (use or vti.use_slot)(row["slot"], force=False, work=WORK)
        except Exception as e:                        # noqa: BLE001
            res = {"ok": False, "stage": "error", "lines": [u"ход упал (%s)" % type(e).__name__],
                   "row": None}
        srv_ok, srv_row = bool(res.get("ok")), res.get("row")
        words, srv_state = use_words(res, num, row["slot"], name=name, now=now, tz=tz)
        body.extend(words)
    # ── телефон: этим словом не трогается ни одной веткой ──
    rc = rc_choice(repo, isdir)
    rn = account_of_profile(accs, rc.value)
    body.append(u"Телефон не трогал — он на %s." % (account_name(rn, accs[rn]) if rn is not None
                                                    else profile_words(rc.value, prep=True, accs=accs)))
    if save:
        last = accounts.load_last(repo)
        last["pc"][u"%d" % num] = pc
        if srv_row is not None and row["slot"]:
            last["srv"][row["slot"]] = {"code": srv_row.get("code"), "word": srv_row.get("word"),
                                        "reset": srv_row.get("reset"),
                                        "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
            if srv_ok:
                last["srv_active"] = row["slot"]
        try:
            accounts.save_last(last, repo)
        except Exception:                             # noqa: BLE001
            pass
    both = pc_ok and (srv_ok or (not row["slot"] and not row.get("slot_error")))
    if pc_ok and srv_ok:
        verdict = u"Готово: ПК и сервер на %s." % name
    elif both:
        verdict = u"Готово: ПК на %s (входа на сервере у неё нет)." % name
    else:
        pc_part = (u"ПК и так на ней" if already else u"ПК перешёл") if pc_ok else u"ПК остался на %s" % prev
        if srv_state == u"already_bad":
            srv_part = u"сервер на ней, но %s" % on_her_words(srv_row, now, tz)
        elif srv_state == u"kept_new":
            srv_part = u"сервер поднялся, а на какой учётке — покажет «учётки»"
        else:
            srv_part = _SRV_PART.get(srv_state, _SRV_PART[u"unknown"])
        verdict = u"Не всё: %s, %s." % (pc_part, srv_part)
    head = u"🔁 Учётка %s — %s" % (name, u"готово ✅" if both else u"не всё ⚠️")
    return (0 if both else 1), u"\n".join([head, u""] + body + [u"", verdict])


def show(repo=None, isdir=None):
    """Реестр без проб и без сети — что записано и что действует по записи."""
    reg = accounts.load(repo)
    out = [u"Реестр: %s — %s" % (accounts.registry_path(repo), reg.reason)]
    for err in reg.errors:
        out.append(u"  ⚠️ %s" % err)
    last = accounts.load_last(repo)
    for n, row in sorted(reg.data["accounts"].items()):
        pc = last["pc"].get(u"%d" % n)
        sr = last["srv"].get(row["slot"]) if row["slot"] else None
        out.append(u"  №%d «%s» · ПК %s (последний ответ: %s%s) · сервер %s%s"
                   % (n, row["label"], row["profile"], _code_words(pc),
                      (u", %s" % pc["at"]) if pc else u"",
                      (u"слот %s" % row["slot"]) if row["slot"] else
                      (u"слот спорный — %s" % row["slot_error"]) if row.get("slot_error") else u"слота нет",
                      (u" (последний ответ: %s, %s)" % (_code_words(sr), sr.get("at"))) if sr else u""))
    c = accounts.builders_effective(repo, isdir=isdir)
    out.append(u"Строители: %s" % accounts.line(c))
    out.append(u"RC: %s" % rc_choice_words(repo, isdir))
    return u"\n".join(out)


# ═══════════════════ «учётки заведи»: первый реестр одним словом ═══════════════════
# Без реестра слово «учётка N» отказывает, а завести реестр можно было только с консоли ПК —
# то есть «одним словом с телефона» не работало ни разу. Заведение кладёт ровно п.1 задания
# 25.09: №1 — основной каталог, №2 — `D:\claude_profile_2` и слот B, №3 — `D:\claude_profile_3` и
# тот слот, где по пробе третья учётка; слот основной — по факту пробы. И заодно п.0: проба
# профиля №3, 200 → строители на №3, иначе на №1. Сервер заведение НЕ трогает (ни записи, ни
# рестарта) — его переключает только «учётка N».
INIT_PROFILE_2 = u"D:\\claude_profile_2"
INIT_PROFILE_3 = u"D:\\claude_profile_3"
# Правило П2 (слово Штаба 25.09): слот A = 200 → в A третья учётка; A = 429 → в A ещё основная.
# Слово Штаба держится на ПОСЫЛКЕ «основная в лимите, третья жива», и заведение её МЕРЯЕТ, а не берёт
# на веру: пробуются основная и №3 на ПК (учётка одна — лимит у неё один на обеих машинах). Слот
# назначается ТОЛЬКО когда три пробы не противоречат друг другу (находка ревью 25.09: по одной пробе
# A при третьей тоже в лимите A = 429 записал бы №1 ← A навсегда). Окно правила — до ИЗМЕРЕННОГО
# сброса недели основной, 28.09 21:00 UTC (артефакт 2209, «Sep 29, 4am (Asia/Bangkok)»): «до 29.09»
# Штаба — местная дата. После — A = 200 учёток не различает, и правило гаснет само.
P2_RULE_UNTIL = (2026, 9, 28, 21, 0)                # UTC (год, месяц, день, час, минута)
CONCLUSIVE = (200, 401, 429)                       # ответы, по которым можно судить; прочее — «не знаю»


def _code(row):
    return (row or {}).get("code")


def probes_disagree(pc_row, srv_row):
    """Спорят ли пробы ПК и слота сервера ОДНОЙ строки реестра. Чистая функция.

    Посылка та же, что у правила П2: лимит у учётки один на обе машины, значит одна учётка не бывает
    на одной стороне живой (200), а на другой — в лимите (429). Такая пара — «похоже, РАЗНЫЕ учётки»
    (не доказательство: модели проб у ПК и сервера разные, и лимит по модели дал бы то же). Всё прочее —
    оба 200, оба 429, 401, 529, проб нет — спором НЕ считается: коды учётку не называют, и «не
    спорят» ≠ «одна учётка» (находка проверки 25.09: профиль 2 и слот B сегодня могут быть разными)."""
    a, b = _code(pc_row), _code(srv_row)
    return a in (200, 429) and b in (200, 429) and a != b


def disagree_words(n, pc_row, letter, srv_row, name=None):
    u"""Спор проб строки реестра → строка ⚠️ владельцу: какая сторона работает, какая в лимите, что поправить.
    Подсказка — на экран владельца (`--screen`, шаг 3): голое «--set N» без профиля консоль отвергла бы."""
    def side(row):
        return u"работает" if _code(row) == 200 else u"в лимите"
    return (u"⚠️ %s: на ПК %s, на сервере %s — похоже, это разные учётки. Поправь №%d на ПК "
            u"(accounts_run.py --screen, шаг 3)." % (name or u"№%d" % n, side(pc_row), side(srv_row), n))


def init_plan(slot_a, pc_main, pc3, now, slot_b=None, pc2=None):
    """ЧИСТОЕ решение заведения → (строки, №строителей, слова, повторить).

    `повторить` = True — судить не по чему (проба дала 529, таймаут, сбой канала): реестр НЕ пишется,
    владелец повторяет слово позже. Иначе реестр пишется, и слот №1/№3 назначен ТОЛЬКО по правилу П2 с
    подтверждённой посылкой; противоречие проб — слоты пусты, и это сказано словами."""
    words = []
    slot1 = slot3 = u""
    a, m, t = _code(slot_a), _code(pc_main), _code(pc3)
    live = tuple(now) < P2_RULE_UNTIL
    if live and slot_a and any(c not in CONCLUSIVE for c in (a, m, t)):
        words.append(u"судить не по чему: слот A — %s · основная на ПК — %s · №3 на ПК — %s"
                     % (_code_words(slot_a), _code_words(pc_main), _code_words(pc3)))
        return None, None, words, True
    probes = u"слот A = %s · основная на ПК = %s · №3 на ПК = %s" % (a, m, t)
    if not live:
        words.append(u"правило П2 истекло (28.09 21:00 UTC — сброс недели основной; A = 200 учёток больше "
                     u"не различает) — слоты №1 и №3 НЕ назначены, назначь «accounts_run.py --set» на ПК")
    elif not slot_a:
        words.append(u"слота A на сервере нет — слоты №1 и №3 НЕ назначены")
    elif a == 200 and m == 429 and t != 429:
        slot3 = u"A"
        words.append(u"%s → основная в лимите, 200 ей не принадлежит: №3 ← A, у №1 слота нет" % probes)
    elif a == 429 and m == 429 and t == 200:
        slot1 = u"A"
        words.append(u"%s → третья жива, 429 ей не принадлежит: №1 ← A (основная), у №3 слота нет" % probes)
    else:
        words.append(u"%s — пробы не сходятся с правилом П2, чей вход в A, не доказано: слоты №1 и №3 "
                     u"НЕ назначены, назначь «accounts_run.py --set» на ПК" % probes)
    slot2 = u"B"
    if probes_disagree(pc2, slot_b):
        # п.1 задания кладёт №2 = профиль 2 + слот B, но пробы спорят: профиль 2 и слот B сегодня —
        # похоже, разные учётки, и «учётка 2» развела бы полосы. Слот не назначаем — назначит владелец.
        slot2 = u""
        words.append(u"№2: ПК профиль 2 — %s, слот B — %s — одна учётка не бывает живой и в лимите "
                     u"сразу: похоже, РАЗНЫЕ учётки; слот B за №2 НЕ назначен, назначь «accounts_run.py "
                     u"--set 2» на ПК" % (_code_words(pc2), _code_words(slot_b)))
    elif pc2 is not None and slot_b is not None:
        words.append(u"№2: ПК профиль 2 — %s · слот B — %s — пробы не спорят (одну учётку коды не "
                     u"доказывают)" % (_code_words(pc2), _code_words(slot_b)))
    rows = {1: {"profile": accounts.WORD_MAIN, "slot": slot1, "label": u"основная"},
            2: {"profile": INIT_PROFILE_2, "slot": slot2, "label": u"вторая"},
            3: {"profile": INIT_PROFILE_3, "slot": slot3, "label": u"третья"}}
    if t == 200:
        builders = 3
        words.append(u"п.0: проба профиля №3 — 200 (is_error=false) → строители на №3")
    else:
        builders = 1
        words.append(u"п.0: проба профиля №3 — %s → строители на №1 (ОСНОВНОЙ)"
                     % (_code_words(pc3) + ((u", %s" % pc3["text"]) if (pc3 or {}).get("text") else u"")))
    return rows, builders, words, False


SCREEN_HINT = u"Назначить вход на ПК: accounts_run.py --screen, шаг 3."
_GROUP_PRED = {K_OK: (u"работает", u"работают"), K_LIMIT: (u"в лимите", u"в лимите"),
               K_DENIED: (u"не пускает", u"не пускают"), K_NODIR: (u"без профиля", u"без профиля"),
               K_EMPTY: (u"пустой", u"пустые")}


def _group_words(items):
    """[(кто, проба)] → «вход A работает, а основная и №3 в лимите»: одинаковые исходы — вместе, без кодов."""
    groups = []
    for who, row in items:
        k = status_kind(row)
        k = k if k in _GROUP_PRED else K_UNKNOWN
        for g in groups:
            if g[0] == k:
                g[1].append(who)
                break
        else:
            groups.append((k, [who]))

    def pred(k, many):
        return _GROUP_PRED.get(k, (u"без ответа", u"без ответа"))[1 if many else 0]
    if len(groups) == 1:
        k, whos = groups[0]
        return u"%s — все %s" % (_and(whos), pred(k, True))
    parts = [u"%s %s" % (_and(whos), pred(k, len(whos) > 1)) for k, whos in groups]
    if len(parts) == 2:
        return u"%s, а %s" % tuple(parts)
    return u", ".join(parts)


def init_words(rows, builders, slot_a, pc_main, pc3, today, slot_b=None, pc2=None, now=None, tz=None):
    u"""Решение заведения (уже принятое `init_plan`) → строки владельцу. Решения здесь НЕТ: чьи входы и номер
    строителей читаются из `rows`/`builders`; пробы и «сегодня» — только чтобы назвать причину словами (и
    назвать ровно то, что пробы показали: «№3 работает» — только когда её проба и правда 200)."""
    out, hint = [], False
    if rows[3]["slot"] == u"A":
        out.append(u"Вход A на сервере — №3: он работает, а основная в лимите.")
    elif rows[1]["slot"] == u"A":
        out.append(u"Вход A на сервере — №1: он в лимите, как основная, а №3 работает.")
    elif not tuple(today) < P2_RULE_UNTIL:
        loc = _local(_dt.datetime(*P2_RULE_UNTIL, tzinfo=_dt.timezone.utc), tz)
        out.append(u"Чей вход A — теперь не понять: лимит основной кончился %02d.%02d %02d:%02d, сравнить не с чем. "
                   u"Не записал." % (loc.day, loc.month, loc.hour, loc.minute))
        hint = True
    elif not slot_a:
        out.append(u"Входа A на сервере нет — №1 и №3 пока без входа на сервере.")
    else:
        out.append(u"Чей вход A — не понять: %s. Не записал."
                   % _group_words(((u"вход A", slot_a), (u"основная", pc_main), (u"№3", pc3))))
        hint = True
    if rows[2]["slot"] == u"B" and slot_b is None and pc2 is not None:
        out.append(u"Вход B записал за №2, но на сервере его пока нет.")
    elif slot_b is not None and pc2 is not None:
        if not rows[2]["slot"]:
            out.append(u"Вход B за №2 не записал: на ПК №2 %s, а вход B %s — похоже, это разные учётки."
                       % (brief_words(pc2), brief_words(slot_b)))
            hint = True
        elif rows[2]["slot"] == u"B":
            out.append(u"Вход B записал за №2, как задумано; что на ПК и на сервере это одна учётка, пробы не доказывают.")
            kb, k2 = status_kind(slot_b), status_kind(pc2)
            b_bad = {K_DENIED: u"не пускает", K_EMPTY: u"пустой", K_UNKNOWN: u"не ответил"}.get(kb)
            if b_bad:
                out.append(u"⚠️ Но вход B сейчас %s — «учётка 2» сервер на него не переведёт, и подстраховки у "
                           u"сервера нет." % b_bad)
            p_bad = {K_NODIR: u"профиля нет", K_DENIED: u"вход не принят", K_UNKNOWN: u"проверка без ответа"}.get(k2)
            if p_bad:
                out.append(u"⚠️ А на ПК у №2 %s — «учётка 2» строителей не переведёт." % p_bad)
    if hint:
        out.append(SCREEN_HINT)
    if builders == 3:
        out.append(u"Строители ПК — на №3.")
    else:
        out.append(u"Строители ПК — на №1: %s." % subject_phrase(u"№3", pc3, now, tz)[1])
    return out


def init_silent_words(slot_a, pc_main, pc3):
    u"""Кто из трёх проб заведения не дал ответа, по которому можно судить → слова (без кодов)."""
    parts = []
    for row, who, verb in ((slot_a, u"вход A на сервере", u"не ответил"),
                           (pc_main, u"основная на ПК", u"не ответила"),
                           (pc3, u"№3 на ПК", u"не ответила")):
        if _code(row) in CONCLUSIVE:
            continue
        k = status_kind(row)
        if k == K_NODIR:
            parts.append(u"%s — профиля нет" % who)
        elif k == K_EMPTY:
            parts.append(u"%s пустой" % who)
        else:
            why = silent_why(row)
            parts.append(u"%s %s%s" % (who, verb, (u" (%s)" % why) if why else u""))
    return u", ".join(parts) or u"пробы не дали ответа"


def init_registry(runner=None, srv_probe=None, isdir=None, repo=None, today=None, save=True, now=None, tz=None):
    """«учётки заведи» → (код, текст). 0 — заведён; 2 — отказ без единой записи."""
    reg = accounts.load(repo)
    if reg.state == accounts.ST_OK:
        return 2, u"⛔ Реестр уже есть — «учётки» покажет, что в нём. Ничего не изменено."
    if reg.state != accounts.ST_ABSENT:
        return 2, (u"⛔ Реестр уже есть, но не читается (%s) — заново заводить не буду.\nПоправь его на ПК "
                   u"(посмотреть: accounts_run.py --show). Ничего не изменено." % registry_short_reason(reg))
    try:
        srv = (srv_probe or vti.probe_all_slots)(work=WORK)
    except Exception as e:                            # noqa: BLE001
        srv = {"ok": False, "words": u"проба сервера упала (%s)" % type(e).__name__, "rows": []}
    if not srv.get("ok"):
        # чей вход A, решает проба сервера — без неё записать нечего
        return 2, u"⏳ Реестр не завёл: сервер не ответил. Повтори «учётки заведи» позже. Ничего не изменено."
    by_letter, active_letters, active_row = srv_map(srv.get("rows"))
    pc_main = pc_probe(accounts.WORD_MAIN, runner=runner, isdir=isdir)
    pc3 = pc_probe(INIT_PROFILE_3, runner=runner, isdir=isdir)
    pc2 = pc_probe(INIT_PROFILE_2, runner=runner, isdir=isdir)
    today = today or tuple(time.gmtime()[:5])
    rows, builders, words, retry = init_plan(by_letter.get("A"), pc_main, pc3, today,
                                             slot_b=by_letter.get("B"), pc2=pc2)
    if retry:
        # вход, записанный по такой пробе, остался бы чужим навсегда — не пишем ничего
        return 2, (u"⏳ Реестр не завёл: %s. Повтори «учётки заведи» позже. Ничего не изменено."
                   % init_silent_words(by_letter.get("A"), pc_main, pc3))
    try:
        made = accounts.create(rows, builders, repo)
    except FileExistsError:
        return 2, u"⛔ Пока шли пробы, реестр уже завели — «учётки» покажет, что в нём. Ничего не изменено."
    except ValueError as e:
        return 2, u"⛔ Реестр не завёл: строка не прошла проверку (%s). Ничего не изменено." % e
    if save:
        _remember(repo, {1: pc_main, 2: pc2, 3: pc3}, srv)
    accs = made["accounts"]
    table = [u"%s — %s" % (account_name(n, row), (u"ПК и сервер (вход %s)" % row["slot"]) if row["slot"]
                           else u"только ПК") for n, row in sorted(accs.items())]
    tail = init_words(accs, builders, by_letter.get("A"), pc_main, pc3, today,
                      slot_b=by_letter.get("B"), pc2=pc2, now=now, tz=tz)
    owner_of = dict((row["slot"], n) for n, row in accs.items() if row["slot"])
    if len(active_letters) == 1 and active_letters[0] in owner_of:
        where = account_name(owner_of[active_letters[0]], accs[owner_of[active_letters[0]]])
    elif len(active_letters) == 1:
        where = u"ничьём входе %s" % active_letters[0]
    else:
        where = u""
    tail.append((u"Сервер не трогал — он на %s. Перевести — «учётка N»." % where) if where
                else u"Сервер не трогал. Перевести — «учётка N».")
    rc = rc_choice(repo, isdir)
    rn = account_of_profile(accs, rc.value)
    tail.append(u"Телефон не трогал — он на %s." % (account_name(rn, accs[rn]) if rn is not None
                                                    else profile_words(rc.value, prep=True, accs=accs)))
    return 0, u"\n\n".join([u"🆕 Реестр учёток заведён", u"\n".join(table), u"\n".join(tail)])


SCREEN = u"""КАК ЗАВЕСТИ УЧЁТКУ N (руки владельца; код не правится)

0. Первый раз и только для трёх известных учёток — одним словом с телефона: «учётки заведи».
   Реестр ляжет по п.1 задания 25.09, слоты №1/№3 — по пробам слота A, основной и №3 (правило
   П2 до 28.09 21:00 UTC; пробы не сходятся — слоты пусты, назначить --set), строители — по №3.
   №2 ← слот B, если профиль 2 и слот B не спорят; 200 на одной стороне и 429 на другой —
   похоже, разные учётки: слот за №2 не назначается, «учётки» говорит это строкой ⚠️.
   Проба дала 529/таймаут — реестр не пишется, слово повторить позже.
   Дальше — шаги ниже для четвёртой и следующих.

1. Профиль ПК — войти в новую учётку в СВОЁМ каталоге (PowerShell на ПК):
     $env:CLAUDE_CONFIG_DIR='D:\\claude_profile_N'; claude auth login
   Вход идёт в браузере; токенов никуда не копировать.
2. Слот сервера — выпустить годовой токен той же учётки и положить его в свободную букву:
     claude setup-token                                   (в том же окне, где шаг 1)
     venv\\Scripts\\python.exe vps_token_install.py --slot C
   Ввод скрыт. Проба одного слота: 200/429 — оставлен, 401 — откат из копии. Демон не
   перезапускается. Занятые буквы видно словом «учётки»: «вход X» в строке учётки — её,
   «ещё вход X» — ничей. Занятую букву не брать: --slot перепишет чужой вход без вопроса.
3. Строка в реестр (файл accounts_registry.json вне git, метка — без почты):
     venv\\Scripts\\python.exe accounts_run.py --set N --profile D:\\claude_profile_N --slot C --label "N-я"
   Основной профиль: --profile ОСНОВНОЙ. Слота нет: --slot -.
   Номер строителей в новом реестре пуст (строители на ОСНОВНОЙ с WARNING; пока реестра нет
   вовсе — по прежнему claude_profile_choice.txt) — назначить без
   сервера: accounts_run.py --builders N, либо словом «учётка N» (тогда и сервер).
4. Проверить: «учётки» в теме 205 — у №N должно стоять «✅ работает» (или «⏳ лимит до …»).
5. Перевести обе полосы: «учётка N». Слово = разрешение на ОДИН рестарт демона сервера; если на
   сервере идёт заход — ответ скажет «стоп», повторить позже. RC остаётся на своём выборе
   (файл rc_profile_choice.txt; нет его — прежний claude_profile_choice.txt) — телефонный канал
   не переезжает.

Автопереключения учётки по лимиту НЕТ: полосы переходят на другую учётку только словом владельца.
Сервер при 429 на слоте A/B повторяет ОДНУ задачу под вторым слотом (старый повтор 20.09);
действующий вход он не меняет — «учётки» говорит, есть ли у сервера такая подстраховка."""


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description=u"Реестр учёток: пробы и переключение обеих полос.")
    ap.add_argument("--report", action="store_true", help=u"«учётки»: живые пробы ПК и сервера")
    ap.add_argument("--init", action="store_true",
                    help=u"«учётки заведи»: первый реестр по п.1 задания 25.09 (если его ещё нет)")
    ap.add_argument("--switch", metavar="N", help=u"«учётка N»: обе полосы на учётку N")
    ap.add_argument("--show", action="store_true", help=u"реестр без проб и без сети")
    ap.add_argument("--screen", action="store_true", help=u"экран владельца: как завести учётку N")
    ap.add_argument("--set", metavar="N", help=u"завести или поправить учётку N")
    ap.add_argument("--profile", help=u"ОСНОВНОЙ либо путь к каталогу профиля ПК")
    ap.add_argument("--slot", default="", help=u"буква слота сервера A–Z, «-» — слота нет")
    ap.add_argument("--label", default="", help=u"метка словами, без почты")
    ap.add_argument("--builders", metavar="N",
                    help=u"только номер строителей ПК (консоль владельца, без проб и без сервера)")
    args = ap.parse_args(argv)
    if args.screen:
        print(SCREEN)
        return 0
    if args.show:
        print(show())
        return 0
    marker = accounts.builder_child()
    if marker:
        print(u"⛔ Это слово владельца: пробы и переключение учёток из захода строителей не "
              u"запускаются (в окружении метка %s). Скажи владельцу: «учётки» / «учётка N» в теме 205. "
              u"Ничего не изменено, проб не было." % marker)
        return 3
    if args.init:
        return _said(u"учётки заведи", init_registry, changes=True)
    if args.builders:
        try:
            prev = accounts.set_builders(args.builders)
        except ValueError as e:
            print(u"ОТКАЗ: %s. Реестр не тронут." % e)
            return 2
        print(u"Строители ПК: %s → №%s (сервер не тронут; RC не тронут)"
              % ((u"№%s" % prev) if prev else accounts.WORD_MAIN, args.builders))
        return 0
    if args.set:
        if not args.profile:
            print(u"ОТКАЗ: --set требует --profile (ОСНОВНОЙ или путь). Реестр не тронут.")
            return 2
        try:
            clean = accounts.set_entry(args.set, args.profile, args.slot, args.label)
        except ValueError as e:
            print(u"ОТКАЗ: %s. Реестр не тронут." % e)
            return 2
        print(u"Записано №%s: профиль %s · слот %s · «%s» → %s"
              % (args.set, clean["profile"], clean["slot"] or u"нет", clean["label"],
                 accounts.registry_path()))
        return 0
    if args.switch:
        return _said(u"учётка %s" % _trim(args.switch, 20), lambda: switch(args.switch), changes=True)
    if args.report:
        return _said(u"учётки", lambda: (0, report()), changes=False)
    ap.print_help()
    return 2


def _said(word, run, changes):
    u"""Слово владельца → ответ на stdout (его агент шлёт в тему как есть). Сломалось посреди хода — владельцу
    одна строка словами, трасса — в stderr (агент кладёт её в свой журнал, в тему она не идёт)."""
    try:
        code, text = run()
    except Exception:                                 # noqa: BLE001 — трасса в тему не идёт никогда
        import traceback
        traceback.print_exc()
        print(u"❔ Слово «%s» сорвалось на ПК — %s Подробности — в журнале агента."
              % (word, u"что успело измениться, покажет «учётки»." if changes else u"ничего не менял."))
        return 1
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
