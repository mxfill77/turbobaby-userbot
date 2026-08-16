# -*- coding: utf-8 -*-
"""
result_judge_pc.py — СУДЬЯ АДРЕСА РЕЗУЛЬТАТА полосы ПК. По адресу шага (`result_ref`) он ЧИТАЕТ
ПРОДУКТ НАЗАД и отвечает ОДНИМ ИЗ ТРЁХ: ДОКАЗАН · НЕ ДОКАЗАН · НЕИЗВЕСТНО. Пункт 2 контракта
Штаба, зеркало полосы VPS.

ПОВОД (своя полоса, проверяемо здесь). Перепись 15.08.2026, коммит a545ad2, артефакт
`docs/artifacts/2026-08-15-orchestrator-trusts-report-pc.md`: 87 мест судят исход в 6 видах,
62 из них — по ОТЧЁТУ исполнителя, 25 — по чтению назад, и НИ ОДНО из 25 не читает продукт
задачи или шага цепи. Пункт 1 контракта дал шагу МЕСТО для адреса (`result_ref.py`, коммиты
6cb2a70 и 05e54aa: маркер `[result_ref: <вид> <указатель>]`, разделитель ПРОБЕЛ, пять видов).
Место без читателя — это по-прежнему ноль чтений назад. Этот модуль и есть читатель.

СУДЬЯ НЕ ПОДКЛЮЧЁН — И ЭТО ДЕРЖИТСЯ ЧИСЛОМ, А НЕ ОБЕЩАНИЕМ. Вызовов из боевого кода НОЛЬ;
инвариант `RESULT_JUDGE_UNWIRED` (`test_result_judge_pc.py`) ast-обходит все боевые `*.py`
корня и роняет гейт на первом импорте, обращении по имени или упоминании имени модуля в коде.
Вердикт шага, движение цепи, TTL подтверждения и правило «нет адреса — нет зелёного» не тронуты
НИ ОДНОЙ ВЕТКОЙ: прибор построен и положен на полку. Подключение — отдельное решение владельца.

═══ ТРИ ИСХОДА И ПОЧЕМУ ТРЕТИЙ ОБЯЗАТЕЛЕН ═══════════════════════════════════════════════════

    ДОКАЗАН      адрес назван, прочитано ОЖИДАЕМОЕ;
    НЕ ДОКАЗАН   адрес назван, чтение УДАЛОСЬ и вернуло «нет»: пусто или не то;
    НЕИЗВЕСТНО   адрес не назван ЛИБО прочитать не удалось.

`НЕИЗВЕСТНО` — это НЕ «в порядке». Слой ожиданий этой полосы стои́т на том же законе
(`CLAUDE.md`, §О1-О4, пункт 3: «Проверить невозможно» → «неизвестно», и оно НЕ закрывает
открытый эпизод). Сосед по слоям назван прямо: `parse_outcome.py` в шапке честно говорит, что
различать «источник не прочитан» и «источник пуст» — НЕ его класс и лечится «на своём слое,
отдельным значением „не знаю“ (None)». Вот этот слой. Поэтому тристейт здесь — `True` / `False`
/ `None`, а не пустота: пустота уже́ означала бы ответ.

ЦЕНТРАЛЬНОЕ ПРАВИЛО, из которого выведены ВСЕ ветки чтения:

    НЕ ДОКАЗАН выдаётся ТОЛЬКО ПОСЛЕ УДАВШЕГОСЯ ЧТЕНИЯ, вернувшего ответ «нет».
    Любой сбой САМОГО ЧТЕНИЯ (git не запустился, базу не открыть, читателя мозга не дали,
    указатель не разобран) — НЕИЗВЕСТНО.

Правило не косметическое, у него есть направление: `НЕ ДОКАЗАН` — самый сильный исход, и если
его давать по сбою инструмента, судья превратится в «нет адреса — нет зелёного» боковой дверью,
через неразобранный указатель. Класс называется заранее, чтобы его не завели молча.

ПОРЯДОК СИЛЫ (дословно с полосы VPS, принято как ЗАЯВЛЕННОЕ — исходник той полосы отсюда не
читается): `НЕ ДОКАЗАН` > `НЕИЗВЕСТНО` > `ДОКАЗАН`. Складывать вердикты (шаги цепи, две
половины одного чтения) — `combine()`: побеждает СИЛЬНЕЙШИЙ. Пустой список — `НЕИЗВЕСТНО`, а не
`ДОКАЗАН`: «ничего не судили» доказательством не является. Тот же порядок работает на тристейте
ответа (`_combine_answers`), и совпадение двух реализаций закреплено тестом.

═══ ЧЕМ ЧИТАЕТСЯ КАЖДЫЙ ИЗ ПЯТИ ВИДОВ ═══════════════════════════════════════════════════════

    commit         хеш есть в `origin/main` (`rev-parse` → `merge-base --is-ancestor`);
    file           файл есть И НЕПУСТ (`isfile` + `getsize`; СОДЕРЖИМОЕ НЕ ЧИТАЕТСЯ ВООБЩЕ);
    row            строка есть по названному условию (sqlite, режим `mode=ro`, значение —
                   связанным параметром, никогда подстановкой в текст запроса);
    brain          узел СОДЕРЖИТ названное И ВЫРОС (две половины, складываются порядком силы);
    service_start  старт сервиса МОЛОЖЕ названного коммита (`GetProcessTimes` против `%ct`).

Две границы чтения, обе — решения, а не случайности:
  • `file` и `row` пускаются ТОЛЬКО ВНУТРЬ ДЕРЕВА полосы (`_repo_path`). Указатель наружу
    (`C:\\Windows\\...`) даёт НЕИЗВЕСТНО, а не ответ: судья исхода шага не вправе быть оракулом
    существования файлов всей машины, и `.env` мимо этого замка не прочитать даже размером.
    Виндовый путь СВОЕЙ полосы (`D:\\turbobaby-bot\\...`) — внутри, он законен и обязателен.
  • `brain` НЕ ХОДИТ В СЕТЬ НИ ОДНОЙ ВЕТКОЙ. Читателя узла обязан дать вызывающий
    (`Ctx.read_doc`); своего нет, `bridge_http`/`urllib`/`socket` не импортируются вовсе (тест).
    Не дали читателя → НЕИЗВЕСТНО. Так судья остаётся оффлайновым прибором.

ПРО «ВЫРОС» У ВИДА `brain`. Половина «содержит названное» ловит подлог «текст был там и до
шага», только если известен РАЗМЕР ДО. Базовой линии в адресе нет, и выдумывать её судья не
станет: `Ctx.brain_baseline` не задан → половина «вырос» = `None`, и по порядку силы весь
вердикт садится в НЕИЗВЕСТНО, даже когда названное в узле нашлось. Это честно: «нашлось» без
«выросло» не доказывает, что продукт произвёл ЭТОТ шаг.

═══ ОКНО: РУЧКА ЖИВАЯ, А ВЕРДИКТ ЕЁ НЕ ВИДИТ ════════════════════════════════════════════════

`Ctx.window` = пара ISO-меток. Окно доезжает ТОЛЬКО ДО ЧТЕНИЯ и садится в поле `in_window`
самого чтения (был ли прочитанный факт свеж). Функция вердикта `verdict_of()` поля `in_window`
НЕ ЧИТАЕТ — ни одной строкой, и это ast-инвариант, а не соглашение. Отсюда замок: сдвиг окна на
два часа не меняет ни одного вердикта, ХОТЯ `in_window` при том же сдвиге переворачивается.
Судья судит по АДРЕСУ, а не по времени; «свежесть» — приём, которым мы НЕ судим, потому что
свежий чужой файл доказывает продукт шага ровно так же, как чужая запись — авторство тика
модербота (класс О3 этой полосы).

ЗАМОК МЕТОДА: РАЗНОСТИ ВРЕМЕНИ — ТОЛЬКО ЧЕРЕЗ `julianday()`. В модуле НЕТ НИ ОДНОГО вычитания
на Python (инвариант считает узлы `ast.Sub` и требует ноль), нет `datetime`, `time`,
`timedelta`. Всякая разность считается SQL-выражением в `:memory:`-соединении — ФАЙЛ ПОД ЭТО НЕ
ОТКРЫВАЕТСЯ, «база» здесь нужна ради одной функции. Три вида времени приведены к julian прямо в
SQL: ISO-строка, unix-секунды (`'unixepoch'`), виндовый FILETIME (`julianday('1601-01-01') +
?/864000000000.0`) — последний СОЗНАТЕЛЬНО не переводится в unix на Python, как это делает
сосед `expectations_pc_run._pid_alive_probe` (`cre.value / 1e7 - _FILETIME_EPOCH`): там
вычитание на Python, здесь его нет вовсе.

ОТКАТ (одной строкой): судья никем не зовётся — удалять нечего, достаточно НЕ ПОДКЛЮЧАТЬ.
Целиком — `git revert` коммита этого захода: боевой путь не изменится ни на байт, потому что
он этого модуля не касается.
"""

import os
import re
import sqlite3
import subprocess

import result_ref as rr

REPO = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ═════════════════════════ ТРИ ИСХОДА И ПОРЯДОК СИЛЫ ═════════════════════════════════════════

PROVEN = "ДОКАЗАН"
DISPROVEN = "НЕ ДОКАЗАН"
UNKNOWN = "НЕИЗВЕСТНО"

# Порядок ЗАПИСАН ЧИСЛОМ, а не порядком строк: по нему складывают вердикты, и «сильнее» обязано
# быть проверяемым, а не подразумеваемым. НЕ ДОКАЗАН > НЕИЗВЕСТНО > ДОКАЗАН.
STRENGTH = {DISPROVEN: 2, UNKNOWN: 1, PROVEN: 0}
VERDICTS = (DISPROVEN, UNKNOWN, PROVEN)

# Ссылка, относительно которой судится вид `commit`. Имя вида читается дословно: «коммит в
# origin/main», а не «коммит в дереве».
MAIN_REF = "origin/main"

# Поля ЧТЕНИЯ. `answer` — тристейт: True «прочитано ожидаемое», False «прочитано и там нет»,
# None «прочитать не удалось». `in_window` — контекст свежести, вердикту НЕ ВИДЕН.
ANSWER = "answer"
DETAIL = "detail"
AT = "at"
IN_WINDOW = "in_window"

GIT_TIMEOUT = 20
SQL_TIMEOUT = 2.0

# Единственные подкоманды git, которые судья умеет. Все три ЧИТАЮЩИЕ; инвариант теста требует,
# чтобы иных в модуле не появилось.
GIT_READ_ONLY = ("rev-parse", "merge-base", "show")

_HASH_RE = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
_HEX_LETTER_RE = re.compile(r"[a-fA-F]")
_PID_RE = re.compile(r"(?:PID|pid|ПИД)\s*[:=]?\s*(\d+)")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DIGITS_RE = re.compile(r"^\d+$")
_EQ_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")

_PROC_QUERY_LIMITED = 0x1000
_ERR_INVALID_PARAMETER = 87
_ERR_ACCESS_DENIED = 5


def combine(verdicts):
    """Список вердиктов → СИЛЬНЕЙШИЙ. Пусто → НЕИЗВЕСТНО («ничего не судили» ≠ «доказано»)."""
    got = [v for v in verdicts if v in STRENGTH]
    if len(got) == 0:
        return UNKNOWN
    return max(got, key=lambda v: STRENGTH[v])


def stronger(first, second):
    """Пара вердиктов → сильнейший. Тот же порядок, отдельным именем для читаемости веток."""
    return combine((first, second))


def _combine_answers(answers):
    """Тристейты чтения → один, ТЕМ ЖЕ порядком силы: False > None > True. Пусто → None."""
    got = list(answers)
    if len(got) == 0:
        return None
    for one in got:
        if one is False:
            return False
    for one in got:
        if one is None:
            return None
    return True


# ═════════════════════════ ВРЕМЯ: ТОЛЬКО julianday() ═════════════════════════════════════════
# Значение времени — ПАРА («вид», число/строка). Вид называется явно, потому что три источника
# полосы отдают время тремя разными способами, и молчаливое приведение одного к другому — как
# раз тот класс, ради которого замок метода и поставлен.

T_ISO = "iso"                 # '2026-08-16T15:38:41+07:00' — так отдаёт мост и пишет журнал
T_UNIX = "unix"               # секунды эпохи — так отдаёт git (%ct) и os.stat
T_FILETIME = "filetime"       # 100-нс тики с 1601 года — так отдаёт GetProcessTimes


def _julian(value):
    """Значение времени → (SQL-выражение julian, параметры) | (None, ()) если вид неизвестен."""
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None, ()
    kind, raw = value
    if kind == T_ISO:
        return "julianday(?)", (raw,)
    if kind == T_UNIX:
        return "julianday(?, 'unixepoch')", (raw,)
    if kind == T_FILETIME:
        return "julianday('1601-01-01') + ?/864000000000.0", (raw,)
    return None, ()


def _days_between(later, earlier):
    """Разность СУТКАМИ (later минус earlier) | None. Единственное место, где считают время."""
    expr_a, args_a = _julian(later)
    expr_b, args_b = _julian(earlier)
    if expr_a is None or expr_b is None:
        return None
    sql = "SELECT (" + expr_a + ") - (" + expr_b + ")"
    con = sqlite3.connect(":memory:")
    try:
        row = con.execute(sql, tuple(args_a) + tuple(args_b)).fetchone()
    except sqlite3.Error:
        return _say_none("разность времён не посчиталась")
    finally:
        con.close()
    if row is None:
        return None
    return row[0]


def _say_none(_reason):
    """Явное «не знаю» с названной причиной. Существует, чтобы ни один `except` не был НЕМЫМ."""
    return None


def shift_window(window, hours):
    """Окно → окно, сдвинутое на `hours` часов. Сдвиг — тоже SQL (`datetime`), не арифметика."""
    if window is None:
        return None
    modifier = "%+d hours" % int(hours)
    out = []
    con = sqlite3.connect(":memory:")
    try:
        for bound in window:
            row = con.execute("SELECT datetime(?, ?)", (bound, modifier)).fetchone()
            out.append(row[0] if row is not None else None)
    except sqlite3.Error:
        return _say_none("окно не сдвинулось")
    finally:
        con.close()
    return tuple(out)


def _in_window(at, window):
    """Факт со временем `at` попал в окно? True/False/None. ВЕРДИКТ ЭТОГО ПОЛЯ НЕ ВИДИТ."""
    if window is None or at is None or len(window) != 2:
        return None
    after_since = _days_between(at, (T_ISO, window[0]))
    before_until = _days_between((T_ISO, window[1]), at)
    if after_since is None or before_until is None:
        return None
    return bool(after_since >= 0 and before_until >= 0)


# ═════════════════════════ КОНТЕКСТ ЧТЕНИЯ ═══════════════════════════════════════════════════

class Ctx(object):
    """Чем судье читать. Всё внешнее — ПОДСТАВЛЯЕМОЕ: тест не ходит ни в git, ни на диск, ни в
    мост, а живой вызов получает те же ветки, что и тест. Своего читателя мозга у судьи нет
    СОЗНАТЕЛЬНО (см. шапку): не дали — НЕИЗВЕСТНО."""

    def __init__(self, repo=None, window=None, read_doc=None, brain_baseline=None,
                 run_git=None, proc_start=None):
        self.repo = repo if repo is not None else REPO
        self.window = window
        self.read_doc = read_doc
        self.brain_baseline = brain_baseline
        self.run_git = run_git if run_git is not None else self._git
        self.proc_start = proc_start if proc_start is not None else _proc_start

    def _git(self, args):
        """git ЧИТАЮЩЕЙ подкомандой → (код возврата, stdout) | None «не запустился»."""
        if len(args) == 0 or args[0] not in GIT_READ_ONLY:
            return _say_none("подкоманда git вне списка читающих: %r" % (args,))
        try:
            done = subprocess.run(["git"] + list(args), cwd=self.repo, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=GIT_TIMEOUT, creationflags=NO_WINDOW)
        except (OSError, ValueError, subprocess.SubprocessError):
            return _say_none("git не запустился")
        return done.returncode, done.stdout.strip()


def _ctx(ctx):
    return ctx if isinstance(ctx, Ctx) else Ctx()


def _reading(answer, detail, at=None, ctx=None):
    """Чтение: тристейт + СЛОВА о том, что именно прочитано, + время факта + окно (контекст)."""
    return {ANSWER: answer, DETAIL: detail, AT: at,
            IN_WINDOW: _in_window(at, None if ctx is None else ctx.window)}


# ═════════════════════════ РАЗБОР УКАЗАТЕЛЕЙ ═════════════════════════════════════════════════

def _hash_in(text, skip=None):
    """Указатель → хеш коммита | None. Предпочитаем токен С ШЕСТНАДЦАТЕРИЧНОЙ БУКВОЙ: именно
    буква отличает хеш от даты вида `20260816`, которая в текст шага попадает часто."""
    cands = [m.group(0).lower() for m in _HASH_RE.finditer(text)]
    if skip is not None:
        cands = [c for c in cands if c != str(skip)]
    lettered = [c for c in cands if _HEX_LETTER_RE.search(c) is not None]
    if len(lettered) > 0:
        return lettered[0]
    if len(cands) > 0:
        return cands[0]
    return None


def _pid_in(text):
    """Указатель → PID | None. Число обязано быть НАЗВАНО словом PID: голая цифра в тексте
    шага — это что угодно (номер задачи, порт, размер), и гадать судья не станет."""
    got = _PID_RE.search(text)
    if got is None:
        return None
    return int(got.group(1))


def _repo_path(ctx, raw):
    """Указатель-путь → абсолютный путь ВНУТРИ дерева полосы | None (наружу или не разобран)."""
    text = raw.strip().strip('"').strip("'")
    if len(text) == 0:
        return None
    text = text.replace("\\", os.sep).replace("/", os.sep)
    if not os.path.isabs(text):
        text = os.path.join(ctx.repo, text)
    full = os.path.normcase(os.path.normpath(os.path.abspath(text)))
    root = os.path.normcase(os.path.normpath(os.path.abspath(ctx.repo)))
    if not full.startswith(root + os.sep):
        return None
    return os.path.normpath(os.path.abspath(text))


def _brain_parts(pointer):
    """Указатель мозга → (док, искомое). Делит `§`, а при его отсутствии — ПЕРВЫЙ пробел."""
    text = pointer.strip()
    if len(text) == 0:
        return None, ""
    if "§" in text:
        doc, needle = text.split("§", 1)
        return doc.strip(), needle.strip()
    if " " in text:
        doc, needle = text.split(" ", 1)
        return doc.strip(), needle.strip()
    return text, ""


def _ro_uri(path):
    """Путь → URI ТОЛЬКО ДЛЯ ЧТЕНИЯ. Запись исключена режимом, а не дисциплиной вызова."""
    slashed = path.replace("\\", "/").replace("?", "%3f").replace("#", "%23")
    if os.path.isabs(path):
        return "file:///" + slashed.lstrip("/") + "?mode=ro"
    return "file:" + slashed + "?mode=ro"


# ═════════════════════════ ЧТЕНИЕ ПО ВИДАМ ═══════════════════════════════════════════════════

def _resolve_commit(ctx, spec):
    """spec → полный хеш | False «такого коммита нет» | None «git не ответил»."""
    got = ctx.run_git(["rev-parse", "--verify", "--quiet", spec + "^{commit}"])
    if got is None:
        return None
    code, out = got
    if code != 0 or len(out) == 0:
        return False
    return out.splitlines()[0].strip()


def _commit_time(ctx, full):
    """Полный хеш → («unix», секунды) | None. Время коммита — ФАКТ, а не оценка свежести."""
    got = ctx.run_git(["show", "-s", "--format=%ct", full])
    if got is None:
        return None
    code, out = got
    if code != 0 or _DIGITS_RE.match(out.strip()) is None:
        return None
    return (T_UNIX, int(out.strip()))


def probe_commit(pointer, ctx=None):
    """commit: ХЕШ ЕСТЬ В `origin/main`. Локальный, но не отгруженный коммит — НЕ ДОКАЗАН, и
    это не придирка: вид назван «коммит в origin/main», а продукт, лежащий только на машине
    исполнителя, до Штаба не доехал. МИНА НАЗВАНА: ссылка `origin/main` обновляется лишь
    fetch/push, судья её НЕ ОБНОВЛЯЕТ (в сеть не ходит) — на отставшей ссылке отгруженный
    коммит даст ЛОЖНЫЙ «НЕ ДОКАЗАН». Подключающий обязан либо освежить ссылку, либо принять."""
    box = _ctx(ctx)
    short = _hash_in(pointer)
    if short is None:
        return _reading(None, "указатель не несёт хеша коммита: %r" % pointer)
    full = _resolve_commit(box, short)
    if full is None:
        return _reading(None, "git не ответил про %s" % short)
    if full is False:
        return _reading(False, "коммита %s в дереве нет вовсе" % short)
    base = _resolve_commit(box, MAIN_REF)
    if base is None or base is False:
        return _reading(None, "ссылки %s нет — сравнивать не с чем" % MAIN_REF)
    got = box.run_git(["merge-base", "--is-ancestor", full, MAIN_REF])
    if got is None:
        return _reading(None, "git не ответил про предка %s" % short)
    at = _commit_time(box, full)
    code = got[0]
    if code == 0:
        return _reading(True, "коммит %s есть в %s" % (short, MAIN_REF), at=at, ctx=box)
    if code == 1:
        return _reading(False, "коммит %s есть локально, в %s его нет" % (short, MAIN_REF),
                        at=at, ctx=box)
    return _reading(None, "merge-base ответил кодом %d" % code, at=at, ctx=box)


def probe_file(pointer, ctx=None):
    """file: ФАЙЛ ЕСТЬ И НЕПУСТ. Содержимое НЕ ЧИТАЕТСЯ — только `isfile` и `getsize`: судье
    хватает факта существования, а лишнее чтение — это лишний способ прочитать чужое."""
    box = _ctx(ctx)
    path = _repo_path(box, pointer)
    if path is None:
        return _reading(None, "путь не разобран или ведёт за пределы полосы: %r" % pointer)
    try:
        here = os.path.isfile(path)
    except OSError as exc:
        return _reading(None, "проверка пути сорвалась: %s" % exc)
    if not here:
        return _reading(False, "файла нет: %s" % path)
    try:
        size = os.path.getsize(path)
        at = (T_UNIX, os.path.getmtime(path))
    except OSError as exc:
        return _reading(None, "размер не снят: %s" % exc)
    if size == 0:
        return _reading(False, "файл есть, но ПУСТ (0 байт): %s" % path, at=at, ctx=box)
    return _reading(True, "файл есть, %d байт: %s" % (size, path), at=at, ctx=box)


def probe_row(pointer, ctx=None):
    """row: СТРОКА ЕСТЬ ПО НАЗВАННОМУ УСЛОВИЮ. Указатель — `<база>:<таблица>:<условие>`, и
    делится он СПРАВА (`rsplit`): двоеточие диска принадлежит пути, а не разметке — ровно тот
    класс, ради которого канон адреса 05e54aa сменил разделитель на пробел.

    База открывается РЕЖИМОМ ЧТЕНИЯ (`mode=ro`), значение условия едет СВЯЗАННЫМ ПАРАМЕТРОМ.
    Имя таблицы и колонки сверяются с `[A-Za-z_]\\w*` — иначе чтение не состоится вовсе."""
    box = _ctx(ctx)
    parts = pointer.rsplit(":", 2)
    if len(parts) != 3:
        return _reading(None, "указатель не вида «<база>:<таблица>:<условие>»: %r" % pointer)
    raw_db, table, cond = parts
    table = table.strip()
    if _IDENT_RE.match(table) is None:
        return _reading(None, "имя таблицы не опознано: %r" % table)
    where, params = _row_condition(cond)
    if where is None:
        return _reading(None, "условие не разобрано: %r" % cond)
    path = _repo_path(box, raw_db)
    if path is None:
        return _reading(None, "база не разобрана или вне полосы: %r" % raw_db)
    try:
        con = sqlite3.connect(_ro_uri(path), uri=True, timeout=SQL_TIMEOUT)
    except sqlite3.Error as exc:
        return _reading(None, "базу не открыть на чтение: %s" % exc)
    try:
        row = con.execute('SELECT COUNT(*) FROM "' + table + '" WHERE ' + where, params).fetchone()
    except sqlite3.Error as exc:
        return _reading(None, "запрос не прошёл (%s): %s" % (table, exc))
    finally:
        con.close()
    if row is None:
        return _reading(None, "счётчик строк не вернул ответа")
    if row[0] > 0:
        return _reading(True, "строк по условию: %d (%s: %s)" % (row[0], table, cond.strip()))
    return _reading(False, "строк по условию НЕТ (%s: %s)" % (table, cond.strip()))


def _row_condition(cond):
    """Условие → (кусок WHERE, параметры) | (None, ()). Голое число — это `rowid`."""
    text = cond.strip()
    if _DIGITS_RE.match(text) is not None:
        return "rowid = ?", (int(text),)
    got = _EQ_RE.match(text)
    if got is None:
        return None, ()
    value = got.group(2).strip().strip('"').strip("'")
    if _DIGITS_RE.match(value) is not None:
        return '"' + got.group(1) + '" = ?', (int(value),)
    return '"' + got.group(1) + '" = ?', (value,)


def probe_brain(pointer, ctx=None):
    """brain: УЗЕЛ СОДЕРЖИТ НАЗВАННОЕ И ВЫРОС. Две половины складываются ПОРЯДКОМ СИЛЫ, поэтому
    «нашлось, но роста не с чем сравнить» честно садится в НЕИЗВЕСТНО, а не в ДОКАЗАН."""
    box = _ctx(ctx)
    doc, needle = _brain_parts(pointer)
    if doc is None:
        return _reading(None, "указатель мозга пуст")
    if box.read_doc is None:
        return _reading(None, "читателя мозга не дали: судья в сеть не ходит ни одной веткой")
    try:
        text = box.read_doc(doc)
    except Exception as exc:                                          # noqa: BLE001
        return _reading(None, "узел «%s» не прочитан: %s" % (doc, exc))
    if not isinstance(text, str):
        return _reading(None, "читатель мозга вернул не текст (%s)" % type(text).__name__)
    has = None if len(needle) == 0 else bool(needle in text)
    base = box.brain_baseline
    grew = None if base is None else bool(len(text) > int(base))
    answer = _combine_answers([has, grew])
    return _reading(answer, "узел «%s»: содержит названное — %s, вырос — %s (%d симв., линия %s)"
                    % (doc, _word(has), _word(grew), len(text), base))


def _word(triple):
    """Тристейт → слово. Три состояния обязаны ЗВУЧАТЬ по-разному в тексте чтения."""
    if triple is True:
        return "да"
    if triple is False:
        return "нет"
    return "неизвестно"


def _proc_start(pid):
    """PID → (жив ли, время старта) в форме тристейта. Зеркало `expectations_pc_run`:
    ERROR_INVALID_PARAMETER — ЕДИНСТВЕННОЕ доказанное отсутствие, отказ доступа — «есть, но
    возраст не добыть». `os.kill(pid, 0)` на Windows зовёт TerminateProcess и запрещён здесь
    навсегда. FILETIME отдаётся СЫРЫМ: переводить его на Python нечем — замок метода."""
    try:
        import ctypes                                                 # noqa: PLC0415
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(_PROC_QUERY_LIMITED, False, int(pid))
    except Exception:                                                 # noqa: BLE001
        return _say_none("проба процесса не состоялась"), None
    if not handle:
        try:
            err = k32.GetLastError()
        except Exception:                                             # noqa: BLE001
            return _say_none("код ошибки не снят"), None
        if err == _ERR_INVALID_PARAMETER:
            return False, None
        if err == _ERR_ACCESS_DENIED:
            return True, None
        return _say_none("OpenProcess отказал кодом %d" % err), None
    started = None
    try:
        cre, ext, ker, usr = (ctypes.c_ulonglong() for _ in range(4))
        if k32.GetProcessTimes(handle, ctypes.byref(cre), ctypes.byref(ext),
                               ctypes.byref(ker), ctypes.byref(usr)) and cre.value:
            started = (T_FILETIME, cre.value)
    except Exception:                                                 # noqa: BLE001
        started = _say_none("GetProcessTimes не ответил")
    finally:
        k32.CloseHandle(handle)
    return True, started


def probe_service_start(pointer, ctx=None):
    """service_start: СТАРТ СЕРВИСА МОЛОЖЕ НАЗВАННОГО КОММИТА. Отвечает на вопрос «правка,
    которую шаг закоммитил, доехала до ЖИВОГО процесса», а не «процесс жив»."""
    box = _ctx(ctx)
    pid = _pid_in(pointer)
    if pid is None:
        return _reading(None, "указатель не несёт PID: %r" % pointer)
    short = _hash_in(pointer, skip=pid)
    if short is None:
        return _reading(None, "указатель не несёт хеша коммита: %r" % pointer)
    full = _resolve_commit(box, short)
    if full is None:
        return _reading(None, "git не ответил про %s" % short)
    if full is False:
        return _reading(False, "названного коммита %s в дереве нет" % short)
    when = _commit_time(box, full)
    if when is None:
        return _reading(None, "время коммита %s не снято" % short)
    alive, started = box.proc_start(pid)
    if alive is False:
        return _reading(False, "процесса PID %d нет — сервис не запущен" % pid)
    if alive is None:
        return _reading(None, "про PID %d ответа нет" % pid)
    if started is None:
        return _reading(None, "PID %d есть, время старта не добыть" % pid)
    days = _days_between(started, when)
    if days is None:
        return _reading(None, "разность «старт минус коммит» не посчиталась")
    if days > 0:
        return _reading(True, "PID %d стартовал ПОЗЖЕ коммита %s (на %.4f сут)"
                        % (pid, short, days), at=started, ctx=box)
    return _reading(False, "PID %d стартовал НЕ ПОЗЖЕ коммита %s (%.4f сут)"
                    % (pid, short, days), at=started, ctx=box)


PROBES = {
    rr.KIND_COMMIT: probe_commit,
    rr.KIND_FILE: probe_file,
    rr.KIND_ROW: probe_row,
    rr.KIND_BRAIN: probe_brain,
    rr.KIND_SERVICE_START: probe_service_start,
}


# ═════════════════════════ ВЕРДИКТ ═══════════════════════════════════════════════════════════

def verdict_of(ref_value, reading):
    """(значение адреса, чтение) → (вердикт, слова). ЧИСТАЯ функция: ни диска, ни git, ни окна.

    Поля `in_window` она не читает СОЗНАТЕЛЬНО и это ast-инвариант: судить по свежести значит
    судить по времени, а не по адресу."""
    named = rr.parse(ref_value)
    if named is None:
        return UNKNOWN, "адрес не назван"
    if not isinstance(reading, dict):
        return UNKNOWN, "чтения не было"
    answer = reading.get(ANSWER)
    words = reading.get(DETAIL, "")
    if answer is None:
        return UNKNOWN, words
    if answer is True:
        return PROVEN, words
    return DISPROVEN, words


def read_back(ref_value, ctx=None):
    """Значение адреса → ЧТЕНИЕ (без вердикта). Отдельным именем, чтобы чтение можно было
    показать глазами и сохранить, не смешивая с судом."""
    named = rr.parse(ref_value)
    if named is None:
        return _reading(None, "адрес не назван")
    kind, pointer = named
    probe = PROBES.get(kind)
    if probe is None:
        return _reading(None, "вид «%s» читать нечем" % kind)
    return probe(pointer, _ctx(ctx))


def judge(ref_value, ctx=None):
    """АДРЕС ШАГА → вердикт словарём: `verdict` (один из трёх), `detail`, `kind`, `pointer`,
    `reading`. Адрес не назван → НЕИЗВЕСТНО, и это законный, а не аварийный исход."""
    named = rr.parse(ref_value)
    reading = read_back(ref_value, ctx)
    answer, words = verdict_of(ref_value, reading)
    return {
        "verdict": answer,
        "detail": words,
        "kind": named[0] if named is not None else "",
        "pointer": named[1] if named is not None else "",
        "reading": reading,
    }


def judge_item(item, ctx=None):
    """РЯД ОЧЕРЕДИ → тот же вердикт. Адрес берётся каноном полосы (`result_ref.read_item`):
    настоящая колонка сильнее маркера в тексте шага."""
    return judge(rr.read_item(item), ctx)
