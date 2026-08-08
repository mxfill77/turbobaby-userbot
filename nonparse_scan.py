# -*- coding: utf-8 -*-
"""
nonparse_scan.py — СЧЁТЧИК СЛЕПЫХ ЧИТАТЕЛЕЙ ПК-полосы (метод храповика класса «нуль по неразбору»).

ЗАЧЕМ ИМЕННО ХРАПОВИК, А НЕ ЗАМОК. Класс живёт в сотне мест разом (перепись:
`docs/artifacts/2026-08-08-zero-by-nonparse-pc-recon.md`). Замок при импорте («читатель обязан
вернуть Reading») детонировал бы на всех сразу — такую правку нельзя ни закончить за заход, ни
откатить по частям. Храповик другой: он НЕ требует чинить старое, он запрещает СТАВИТЬ НОВОЕ.
Текущее число закреплено базовой линией в репозитории, гейт краснеет ТОЛЬКО при РОСТЕ.

ЧТО СЧИТАЕТСЯ (метод — часть договора; его ослабление обязано ронять `test_nonparse_ratchet`):

  • `swallow`  — обработчик `except`, чьё тело целиком состоит из МОЛЧАЛИВОГО схлопывания:
                 `pass` / `continue` / `break` / `return <пусто>`. Сказавший вслух (лог, `raise`,
                 любой вызов) НЕ в счёте: считаем немоту, а не обработку ошибок вообще;
  • `or_empty` — `x or ""` / `x or []` / `x or {}` / `x or 0`: идиома, которая УНИЧТОЖАЕТ уже
                 существующее различение. Живой пример полосы — `_git_out` честно отдаёт `None`
                 при «git не ответил» и `""` при «ответил пустым», а потребитель
                 `(out or "").splitlines()` делает эти два состояния одним;
  • `guard_empty` — та же идиома, переписанная тернарником: `x if x else []`,
                 `x if x is not None else {}`. Подпись заведена НЕ для полноты, а чтобы запрет
                 на `or_empty` не обходился переписью в одну строку. Форма узкая СОЗНАТЕЛЬНО
                 (проверяемое и подставляемое — одно имя): широкое «любой тернарник с пустотой в
                 else» дало бы на этой полосе 143 попадания, из них подавляющее большинство —
                 честные развилки, а не схлопывание «не знаю».

«ПУСТО» — это `""`, `[]`, `{}`, `()`, `0`, `False`, `list()/dict()/set()/tuple()`. `None` пустотой
НЕ считается СОЗНАТЕЛЬНО: на этой полосе `None` — честное «не знаю» и эталон третьего состояния
(`_git_out`, `_find_pids_by_script`, `_pid_alive_probe`, `_dirty_tracked`). Поэтому
`return None, offset, False` — ЗРЯЧИЙ возврат, а `return [], offset, False` — слепой: метод
обязан НАГРАЖДАТЬ честную форму, иначе он толкает чинить не туда.

СЛЕПОЙ ЧИТАТЕЛЬ = функция (или уровень модуля), где есть ХОТЯ БЫ ОДНА такая точка. Считаем и
читателей, и точки: читатель — грань, сравнимая с ручной переписью, точка — то, что реально
правится.

ОБЛАСТЬ: `*.py` В КОРНЕ репозитория, НЕ рекурсивно. Рекурсия здесь — известная мина: в дереве
лежат `manager-bot/` (июньский снимок VPS), `tmp_selfupdate_deps_*/red_probe/` (копия репо) и
`docs/artifacts/2026-07-23-guard-v3-files/` (архивная копия гарда) — они дали бы вторые
экземпляры тех же модулей и ложный рост. Тесты (`test_*.py`) не в счёте: их дело — фикстуры.
Замороженная тройка клиентского контура (`OUT_OF_SCOPE`) исключена явно и списком: её правка
запрещена контуром, и держать её под храповиком значит красить гейт чужой полосе.

САМ СЧЁТЧИК ЖИВЁТ ПО СВОЕМУ ЖЕ КОНТРАКТУ: `scan_repo` отдаёт наверх пару «осмотрено/разобрано»
файлов (`parse_outcome.Reading`). Счётчик, ослепший на всём репозитории, обязан сказать это
вслух и уронить гейт, а не отдать «нарушений ноль».

Запуск:
  venv\\Scripts\\python.exe nonparse_scan.py            — таблица по файлам и итог
  venv\\Scripts\\python.exe nonparse_scan.py --json     — базовая линия в формате nonparse_baseline.json
  venv\\Scripts\\python.exe nonparse_scan.py --file X.py — точки одного файла построчно
"""

import ast
import io
import json
import os
import subprocess
import sys
from collections import namedtuple

import parse_outcome

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "nonparse_baseline.json")

# Имена подписей — часть договора: тест счётчика требует РОВНО этот набор, ни больше ни меньше.
SIG_SWALLOW = "swallow"
SIG_OR_EMPTY = "or_empty"
SIG_GUARD_EMPTY = "guard_empty"
SIGNATURES = (SIG_SWALLOW, SIG_OR_EMPTY, SIG_GUARD_EMPTY)

# Замороженный клиентский контур: правка запрещена, под храповик не берём (границу держим
# СПИСКОМ явно — ровно как OUT_OF_SCOPE в test_utf8_output_guard, чтобы страж не притворялся,
# будто закрыл и их).
OUT_OF_SCOPE = ("userbot_listen.py", "moderation_bot.py", "suggest.py")

MODULE_LEVEL = "<module>"

Point = namedtuple("Point", "file line func sig")
FileScan = namedtuple("FileScan", "points readers ok err")  # points is None → файл НЕ считан (не «чисто»)
Result = namedtuple("Result", "points files readers reading unreadable")


# ------------------------------- словарь «пустоты» ----------------------------

def _is_empty_const(node):
    """Литерал ПУСТОТЫ: '', 0, False, [], {}, (), set(), list(), dict(), tuple(), frozenset().
    `None` СЮДА НЕ ВХОДИТ — это честное «не знаю» (см. шапку)."""
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None:
            return False
        if isinstance(v, (str, bytes)):
            return len(v) == 0
        if isinstance(v, bool):
            return v is False
        if isinstance(v, (int, float)):
            return v == 0
        return False
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return len(node.elts) == 0
    if isinstance(node, ast.Dict):
        return len(node.keys) == 0
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        empty_call = node.func.id in ("list", "dict", "set", "tuple", "frozenset")
        return empty_call and len(node.args) == 0 and len(node.keywords) == 0
    return False


def _is_blind_return(node):
    """`return` из обработчика, уничтожающий различение: несёт пустоту и НЕ несёт `None`.

    `return []`                  → слепой;
    `return [], offset, False`   → слепой (полезная нагрузка схлопнута в пустоту);
    `return None, offset, False` → ЗРЯЧИЙ: «не знаю» доехало наверх;
    `return` / `return None`     → зрячий."""
    value = node.value
    if value is None:
        return False
    parts = list(value.elts) if isinstance(value, (ast.Tuple, ast.List)) and value.elts else [value]
    for p in parts:
        if isinstance(p, ast.Constant) and p.value is None:
            return False
    for p in parts:
        if _is_empty_const(p):
            return True
    return False


def _name_of(node):
    return node.id if isinstance(node, ast.Name) else None


def _is_guard_empty(node):
    """`x if x else <пусто>` / `x if x is [not] None else <пусто>` — переписанная `or`-идиома.

    Узко по устройству: подставляемое значение и проверяемое — ОДНО имя. Иначе под подпись
    попали бы честные развилки вида `a if режим else []`."""
    if not _is_empty_const(node.orelse):
        return False
    body = _name_of(node.body)
    if body is None:
        return False
    test = node.test
    if _name_of(test) == body:
        return True
    if isinstance(test, ast.Compare) and _name_of(test.left) == body and len(test.comparators) == 1:
        cmp_to = test.comparators[0]
        is_none_op = isinstance(test.ops[0], (ast.Is, ast.IsNot))
        return is_none_op and isinstance(cmp_to, ast.Constant) and cmp_to.value is None
    return False


def _is_swallow_stmt(st):
    if isinstance(st, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(st, ast.Return):
        return _is_blind_return(st)
    return False


def _is_swallow_body(body):
    """Тело обработчика МОЛЧИТ целиком (ни лога, ни raise, ни единого вызова)."""
    if len(body) == 0:
        return False
    for st in body:
        if not _is_swallow_stmt(st):
            return False
    return True


# ------------------------------- обход дерева ---------------------------------

class _Walk(ast.NodeVisitor):
    """Собирает точки класса с привязкой к ЧИТАТЕЛЮ (имя функции; уровень модуля → <module>)."""

    def __init__(self, fname):
        self.fname = fname
        self.stack = []
        self.points = []
        self.readers = [MODULE_LEVEL]      # уровень модуля — тоже читатель (знаменатель доли)

    def _where(self):
        if len(self.stack) == 0:
            return MODULE_LEVEL
        return ".".join(self.stack)

    def _scoped(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.readers.append(".".join(self.stack))
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        self._scoped(node)

    def visit_ExceptHandler(self, node):
        if _is_swallow_body(node.body):
            self.points.append(Point(self.fname, node.lineno, self._where(), SIG_SWALLOW))
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        if isinstance(node.op, ast.Or) and _is_empty_const(node.values[-1]):
            self.points.append(Point(self.fname, node.lineno, self._where(), SIG_OR_EMPTY))
        self.generic_visit(node)

    def visit_IfExp(self, node):
        if _is_guard_empty(node):
            self.points.append(Point(self.fname, node.lineno, self._where(), SIG_GUARD_EMPTY))
        self.generic_visit(node)


# ------------------------------- сканирование ---------------------------------

def _walk_source(src, fname):
    walk = _Walk(fname)
    walk.visit(ast.parse(src, filename=fname))
    return walk


def scan_source(src, fname="<src>"):
    """Точки класса в исходнике. → кортеж Point. Синтаксическая ошибка — ГРОМКАЯ (SyntaxError)."""
    return tuple(_walk_source(src, fname).points)


def scan_file(path):
    """Один файл → FileScan. Нечитаемый/неразбираемый файл даёт points=None («не считал»),
    а НЕ пустой кортеж: счётчик не смеет выдавать собственную слепоту за чистоту."""
    fname = os.path.basename(path)
    try:
        with io.open(path, encoding="utf-8") as f:
            src = f.read()
    except Exception as e:
        return FileScan(None, None, False, "%s: %s" % (type(e).__name__, e))
    try:
        walk = _walk_source(src, fname)
    except SyntaxError as e:
        return FileScan(None, None, False, "SyntaxError: %s" % e)
    return FileScan(tuple(walk.points), tuple(walk.readers), True, "")


def tracked_names(repo):
    """Имена файлов, ОТСЛЕЖИВАЕМЫХ git в корне репо. Сбой git — ГРОМКИЙ (RuntimeError).

    Почему git, а не `os.listdir`: в корне живут неотслеживаемые одноразовые скрипты
    (`tmp_*.py` и прочие), которых нет ни в одном другом чекауте. Считай их — и базовая линия
    станет невоспроизводимой: на свежем клоне те же файлы «пропадут», и храповик закричит о
    сужении метода там, где ничего не менялось."""
    p = subprocess.run(["git", "ls-files", "--"], cwd=repo, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("git ls-files не ответил (rc=%s): %s" % (p.returncode, p.stderr.strip()))
    names = set()
    for line in p.stdout.splitlines():
        rel = line.strip()
        if len(rel) == 0 or "/" in rel:
            continue                      # только КОРЕНЬ: рекурсия тянет мины дерева (см. шапку)
        names.add(rel)
    if len(names) == 0:
        raise RuntimeError("git ls-files вернул пусто — область счёта не построена")
    return names


def repo_files(repo=None):
    """Файлы области: отслеживаемые git *.py КОРНЯ репо, без test_*.py и без замороженной
    тройки. → отсортировано."""
    root = repo if repo is not None else HERE
    tracked = tracked_names(root)
    names = []
    for name in sorted(tracked):
        if not name.endswith(".py"):
            continue
        if name.startswith("test_"):
            continue
        if name in OUT_OF_SCOPE:
            continue
        if os.path.isfile(os.path.join(root, name)):
            names.append(name)
    return sorted(names)


def scan_repo(repo=None):
    """Область целиком. → Result(points, files, reading, unreadable).

    `reading` — контракт САМОГО счётчика: осмотрено файлов / разобрано файлов."""
    root = repo if repo is not None else HERE
    files = repo_files(root)
    points, unreadable, readers, parsed = [], [], 0, 0
    for name in files:
        fs = scan_file(os.path.join(root, name))
        if fs.points is None:
            unreadable.append((name, fs.err))
            continue
        parsed += 1
        readers += len(fs.readers)
        points.extend(fs.points)
    return Result(tuple(points), tuple(files), readers,
                  parse_outcome.reading(len(files), parsed), tuple(unreadable))


def by_file(result):
    """{файл: {"readers": слепых читателей, "points": точек}} — только файлы с точками."""
    acc = {}
    for p in result.points:
        slot = acc.setdefault(p.file, {"readers": set(), "points": 0})
        slot["readers"].add(p.func)
        slot["points"] += 1
    return {f: {"readers": len(v["readers"]), "points": v["points"]} for f, v in acc.items()}


def totals(result):
    """Итог по области: файлов, слепых читателей, ВСЕГО читателей (знаменатель), точек."""
    readers = set()
    for p in result.points:
        readers.add((p.file, p.func))
    return {"files": len(result.files), "readers": len(readers),
            "readers_all": result.readers, "points": len(result.points)}


def by_signature(result):
    """Сколько точек дала каждая подпись (нулевая подпись — повод посмотреть, жив ли метод)."""
    acc = dict((s, 0) for s in SIGNATURES)
    for p in result.points:
        acc[p.sig] = acc.get(p.sig, 0) + 1
    return acc


def baseline_payload(result, measured=""):
    """Снимок для nonparse_baseline.json."""
    return {"method": "nonparse_scan/%s" % ",".join(SIGNATURES),
            "scope": "*.py корня репо, без test_*.py и без " + ",".join(OUT_OF_SCOPE),
            "measured": measured,
            "totals": totals(result),
            "signatures": by_signature(result),
            "files": by_file(result)}


def load_baseline(path=None):
    """Базовая линия из репозитория. Нет файла / битый json → ГРОМКО (исключение), а не «0»."""
    p = path if path is not None else BASELINE
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def growth(result, base=None):
    """Что ВЫРОСЛО против базовой линии. → список строк-нарушений (пусто = храповик держит).

    Три вида роста, каждый — красный гейт:
      • файл превысил свою линию (читателей или точек);
      • НОВЫЙ файл принёс точки (в линии его нет ⇒ его предел 0);
      • файл линии ПРОПАЛ из области (сузили метод ⇒ отчёт «стало лучше» стал бы подлогом)."""
    base = load_baseline() if base is None else base
    base_files = base["files"]
    now = by_file(result)
    bad = []
    for name in sorted(now):
        was = base_files.get(name, {"readers": 0, "points": 0})
        if now[name]["readers"] > was["readers"] or now[name]["points"] > was["points"]:
            bad.append("%s: было читателей %d/точек %d, стало %d/%d"
                       % (name, was["readers"], was["points"],
                          now[name]["readers"], now[name]["points"]))
    in_scope = set(result.files)
    for name in sorted(base_files):
        if name not in in_scope:
            bad.append("%s: файл базовой линии ВЫПАЛ из области счёта — метод сузили" % name)
    return bad


# ------------------------------- CLI ------------------------------------------

def main(argv):
    res = scan_repo()
    if "--json" in argv:
        print(json.dumps(baseline_payload(res, measured=_arg(argv, "--measured")),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    one = _arg(argv, "--file")
    if len(one) > 0:
        for p in res.points:
            if p.file == one:
                print("%s:%d  %-28s %s" % (p.file, p.line, p.func, p.sig))
        return 0
    per = by_file(res)
    for name in sorted(per, key=lambda n: (-per[n]["points"], n)):
        print("%-28s читателей %3d  точек %3d" % (name, per[name]["readers"], per[name]["points"]))
    t = totals(res)
    print("--")
    print("файлы области: %s" % res.reading.say("файлов"))
    print("подписи: %s" % json.dumps(by_signature(res), ensure_ascii=False))
    share = 100.0 * t["readers"] / t["readers_all"] if t["readers_all"] > 0 else 0.0
    print("ИТОГО слепых читателей %d из %d (%.1f %%), точек %d, файлов %d"
          % (t["readers"], t["readers_all"], share, t["points"], t["files"]))
    if len(res.unreadable) > 0:
        print("НЕ СЧИТАНЫ (это НЕ «чисто»): %s" % json.dumps(res.unreadable, ensure_ascii=False))
    return 0


def _arg(argv, name):
    """Значение --ключ значение. Нет ключа → пустая строка (у CLI третьего состояния нет)."""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return ""


if __name__ == "__main__":
    import io_utf8
    io_utf8.force_utf8()
    sys.exit(main(sys.argv[1:]))
