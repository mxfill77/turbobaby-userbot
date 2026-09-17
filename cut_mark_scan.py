# -*- coding: utf-8 -*-
"""
cut_mark_scan.py — СТОРОЖ ПОМЕТКИ РЕЗА (класс «решение снимается с укороченного текста»).

ПРАВИЛО КЛАССА (62-s §2, 62-t §3, 63-c, 63-d): решение судит полный текст, режется только показ,
рез объявляет себя числом. Перепись 63-d (`docs/artifacts/2026-09-17-SVIDETEL-kanala-git-1709.md`)
нашла 48 мест без доказанного права; рамка (10.09 #1) запрещает чинить такой класс поштучно.
Поэтому сторож НЕ чинит старое, а запрещает ставить НОВЫЙ рез, о котором автор не сказал,
показ это или решение.

ПОМЕТКА — одна строка-комментарий рядом с резом, нового файла не требует:

    head = text[:120]            # рез: показ
    key = sha[:12]               # рез: решение — ключ: первые 12 hex sha256, коллизии не страшны
    # рез: показ
    line = one_line(why, 50)

  • место: любая физическая строка самого выражения реза ИЛИ строка-комментарий прямо над ним;
  • `показ` — рез живёт только на пути к человеку;
  • `решение` обязано назвать ПРАВО (якорь формата, ключ, машинный знак на своём месте) после
    слова: голое `# рез: решение` — это объявленное нарушение правила, и сторож его КРАСИТ.

ЧТО СЧИТАЕТСЯ РЕЗОМ (метод — часть договора, его сужение обязано ронять test_cut_mark_scan):
  • head   — `x[:B]`, где B не отрицательное число (`x[:-3]` — снятие известного хвоста формата);
  • tail   — `x[-B:]`;
  • helper — вызов функции реза полосы по имени (`CUT_HELPERS`): one_line, _tail, goal_line, …;
  • bytes  — `f.read(N)` и `f.seek(-N, …)`: голова или хвост файла в байтах.
НЕ считается: `x[1:]`, `x[i]`, `split(sep)[0]`, `splitlines()[-1]`, `partition` — рез по
разделителю без числа сторож НЕ ВИДИТ (предел метода, назван вслух, а не спрятан).

ИЗВЕСТНЫЕ МЕСТА — `cut_mark_known.json`: резы дерева на дату переписи, ключ «подпись|выражение»
с кратностью по файлу. Рез из списка сторожа не будит; такой же рез СВЕРХ кратности — будит.
Номера строк в ключ не входят: сдвиг кода не краснеет, правка самого выражения — краснеет
(тронул рез — пометь его). Список только УБЫВАЕТ: число и дата прибиты в тесте.

ОБЛАСТЬ: отслеживаемые git `*.py` КОРНЯ репо, без `test_*.py`, `tmp*` и `_scratch*` (рекурсия
тянет архивные копии модулей — см. шапку nonparse_scan).

Запуск:
  venv\\Scripts\\python.exe cut_mark_scan.py                  — проверка дерева, rc=1 на новом резе
  venv\\Scripts\\python.exe cut_mark_scan.py --files a.py b.py — только названные файлы
  venv\\Scripts\\python.exe cut_mark_scan.py --staged         — файлы индекса в виде коммита (хук)
  venv\\Scripts\\python.exe cut_mark_scan.py --list           — все резы области построчно
  … --root КАТАЛОГ --files x.py                              — файл из копии (стенд), список — репо
  venv\\Scripts\\python.exe cut_mark_scan.py --json --measured ГГГГ-ММ-ДД — перепись для списка
"""

import ast
import io
import json
import os
import re
import subprocess
import sys
import time
import tokenize
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN = os.path.join(HERE, "cut_mark_known.json")

SIG_HEAD = "head"
SIG_TAIL = "tail"
SIG_HELPER = "helper"
SIG_BYTES = "bytes"
SIGNATURES = (SIG_HEAD, SIG_TAIL, SIG_HELPER, SIG_BYTES)

# Функции реза полосы (замер определений 17.09). `short`/`_short`/`_cut` не взяты: у полосы это
# голова sha коммита, а не текста.
CUT_HELPERS = frozenset((
    "one_line", "_tail", "_tail_shown", "_last_line", "first_line", "_cap_result",
    "_clip", "_clip_block", "_clip_err", "_clip_quote", "clip_named", "shown", "goal_line",
    "_cut_head", "cut_head", "shorten",
))

KIND_SHOW = "показ"
KIND_DECIDE = "решение"
MARK_RE = re.compile(r"#\s*рез:\s*(показ|решение)(.*)$")
RIGHT_MIN = 3                      # знаков права после слова «решение»

VERDICT_UNMARKED = "без пометки"
VERDICT_NO_RIGHT = "решение без права"

Cut = namedtuple("Cut", "file line sig expr mark")
FileScan = namedtuple("FileScan", "cuts err")      # cuts is None → файл НЕ считан (не «чисто»)


# ------------------------------- поиск резов ----------------------------------

def _is_negative(node):
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and node.value < 0


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _sig_of(node):
    """Подпись реза узла или None."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        sl = node.slice
        if sl.step is not None:
            return None
        if sl.lower is None and sl.upper is not None and not _is_negative(sl.upper):
            return SIG_HEAD
        if sl.lower is not None and _is_negative(sl.lower) and sl.upper is None:
            return SIG_TAIL
        return None
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in CUT_HELPERS:
            return SIG_HELPER
        is_method = isinstance(node.func, ast.Attribute)
        if is_method and name == "read" and len(node.args) == 1:
            arg = node.args[0]
            whole = isinstance(arg, ast.Constant) and arg.value in (-1, None)
            return None if whole else SIG_BYTES
        if is_method and name == "seek" and len(node.args) >= 1 and _is_negative(node.args[0]):
            return SIG_BYTES
    return None


def _marks(src):
    """{строка: (вид, право, только_комментарий)} по комментариям-пометкам исходника."""
    marks = {}
    code_lines = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            m = MARK_RE.search(tok.string)
            if m is not None:
                right = m.group(2).strip().lstrip("—–-:").strip()
                marks[tok.start[0]] = [m.group(1), right, False]
        elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                              tokenize.ENDMARKER):
            code_lines.add(tok.start[0])
    for line, slot in marks.items():
        slot[2] = line not in code_lines
    return marks


def _mark_for(node, marks):
    end = getattr(node, "end_lineno", None)
    last = end if end is not None else node.lineno
    for line in range(node.lineno, last + 1):
        if line in marks:
            return marks[line]
    above = marks.get(node.lineno - 1)
    if above is not None and above[2]:
        return above
    return None


def scan_source(src, fname="<src>"):
    """Все резы исходника с пометками. SyntaxError/TokenError — ГРОМКО (исключение)."""
    tree = ast.parse(src, filename=fname)
    marks = _marks(src)
    cuts = []
    for node in ast.walk(tree):
        sig = _sig_of(node)
        if sig is None:
            continue
        mark = _mark_for(node, marks)
        cuts.append(Cut(fname, node.lineno, sig, ast.unparse(node), None if mark is None
                        else (mark[0], mark[1])))
    cuts.sort(key=lambda c: (c.line, c.sig, c.expr))
    return tuple(cuts)


def key_of(cut):
    return "%s|%s" % (cut.sig, cut.expr)


def mark_verdict(cut):
    """None — пометка годна; иначе вердикт нарушения, независимый от списка известных."""
    if cut.mark is None:
        return VERDICT_UNMARKED
    kind, right = cut.mark
    if kind == KIND_DECIDE and len(right) < RIGHT_MIN:
        return VERDICT_NO_RIGHT
    return None


def violations(cuts, known_files):
    """Новые резы без годной пометки. → список (Cut, вердикт, известно_кратно)."""
    bad = []
    groups = {}
    for c in cuts:
        v = mark_verdict(c)
        if v == VERDICT_NO_RIGHT:
            bad.append((c, v, 0))
        elif v == VERDICT_UNMARKED:
            groups.setdefault((c.file, key_of(c)), []).append(c)
    for (fname, key), group in sorted(groups.items()):
        allowed = known_files.get(fname, {}).get(key, 0)
        if len(group) > allowed:
            for c in group:
                bad.append((c, VERDICT_UNMARKED, allowed))
    bad.sort(key=lambda b: (b[0].file, b[0].line))
    return bad


# ------------------------------- область --------------------------------------

def tracked_names(repo):
    p = subprocess.run(["git", "ls-files", "--"], cwd=repo, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("git ls-files не ответил (rc=%s): %s" % (p.returncode, p.stderr.strip()))
    names = set()
    for line in p.stdout.splitlines():
        rel = line.strip()
        if len(rel) > 0 and "/" not in rel:
            names.add(rel)
    if len(names) == 0:
        raise RuntimeError("git ls-files вернул пусто — область не построена")
    return names


def in_scope(name):
    if not name.endswith(".py") or "/" in name or "\\" in name:
        return False
    return not (name.startswith("test_") or name.startswith("tmp") or name.startswith("_scratch"))


def repo_files(repo=None):
    root = repo if repo is not None else HERE
    return sorted(n for n in tracked_names(root)
                  if in_scope(n) and os.path.isfile(os.path.join(root, n)))


def scan_file(path, fname=None):
    name = fname if fname is not None else os.path.basename(path)
    try:
        with io.open(path, encoding="utf-8") as f:
            src = f.read()
        return FileScan(scan_source(src, name), "")
    except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError) as e:
        return FileScan(None, "%s: %s" % (type(e).__name__, e))


def scan_files(names, repo=None):
    """→ (резы, нечитаемые [(имя, ошибка)])."""
    root = repo if repo is not None else HERE
    cuts, unreadable = [], []
    for name in names:
        fs = scan_file(os.path.join(root, name), name)
        if fs.cuts is None:
            unreadable.append((name, fs.err))
        else:
            cuts.extend(fs.cuts)
    return cuts, unreadable


def staged_names(repo=None):
    """Файлы области, добавленные/изменённые в ИНДЕКСЕ. Сбой git — ГРОМКО (RuntimeError)."""
    root = repo if repo is not None else HERE
    p = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "--"],
                       cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("git diff --cached не ответил (rc=%s): %s" % (p.returncode,
                                                                          p.stderr.strip()))
    return sorted(n.strip() for n in p.stdout.splitlines() if in_scope(n.strip()))


def _git_show(name, repo):
    """Содержимое файла В ИНДЕКСЕ → (текст, ошибка). Текст None — не прочитан."""
    p = subprocess.run(["git", "show", ":" + name], cwd=repo, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if p.returncode != 0:
        return None, "git show :%s rc=%s" % (name, p.returncode)
    try:
        return p.stdout.decode("utf-8"), ""
    except UnicodeDecodeError as e:
        return None, "UnicodeDecodeError: %s" % e


def scan_staged(names, repo=None):
    """Резы файлов в том виде, в каком они уйдут в коммит. → (резы, нечитаемые)."""
    root = repo if repo is not None else HERE
    cuts, unreadable = [], []
    for name in names:
        src, err = _git_show(name, root)
        if src is None:
            unreadable.append((name, err))
            continue
        try:
            cuts.extend(scan_source(src, name))
        except (SyntaxError, tokenize.TokenError, ValueError) as e:
            unreadable.append((name, "%s: %s" % (type(e).__name__, e)))
    return cuts, unreadable


def stale_count(cuts, known_files, names):
    """Сколько записей списка по осмотренным файлам уже нет в коде (рез убран или правлен).
    Не краснеет: убывание — цель. Но такой запас молча впустит тот же рез обратно."""
    now = {}
    for c in cuts:
        if c.mark is None:
            k = (c.file, key_of(c))
            now[k] = now.get(k, 0) + 1
    stale = 0
    for fname in names:
        for key, count in known_files.get(fname, {}).items():
            stale += max(0, count - now.get((fname, key), 0))
    return stale


def census(cuts, measured):
    """Перепись для cut_mark_known.json: только НЕпомеченные резы (помеченному место не нужно)."""
    files = {}
    total = 0
    for c in cuts:
        if c.mark is not None:
            continue
        slot = files.setdefault(c.file, {})
        k = key_of(c)
        slot[k] = slot.get(k, 0) + 1
        total += 1
    return {"method": "cut_mark_scan/" + ",".join(SIGNATURES), "measured": measured,
            "total": total, "files": files}


def load_known(path=None):
    """Список известных. Нет файла / битый json → ГРОМКО, а не «известных нет»."""
    with io.open(path if path is not None else KNOWN, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------- CLI ------------------------------------------

def _arg(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return ""


def main(argv):
    t0 = time.monotonic()
    root = _arg(argv, "--root")
    root = HERE if len(root) == 0 else os.path.abspath(root)
    staged = "--staged" in argv
    if staged:
        names = staged_names(root)
    elif "--files" in argv:
        names = []
        for a in argv[argv.index("--files") + 1:]:
            if a.startswith("--"):
                break
            names.append(a.replace("\\", "/").split("/")[-1])
        names = [n for n in names if in_scope(n)]
    else:
        names = repo_files(root)
    cuts, unreadable = scan_staged(names, root) if staged else scan_files(names, root)
    if "--json" in argv:
        print(json.dumps(census(cuts, _arg(argv, "--measured")), ensure_ascii=False, indent=1,
                         sort_keys=True))
        return 0
    if "--list" in argv:
        for c in cuts:
            print("%s:%d  %-6s %-40s %s" % (c.file, c.line, c.sig,
                                            "-" if c.mark is None else "%s %s" % c.mark, c.expr))
    known_path = _arg(argv, "--known")
    known = load_known(known_path if len(known_path) > 0 else None)
    bad = violations(cuts, known["files"])
    for c, verdict, allowed in bad:
        print("НОВЫЙ РЕЗ %s: %s:%d  %s  %s  (известно таких: %d)"
              % (verdict, c.file, c.line, c.sig, c.expr, allowed))
    marked = sum(1 for c in cuts if c.mark is not None)
    print("сторож реза%s: файлов %d, резов %d, помечено %d, известных в списке %d (перепись %s), "
          "устарело в списке %d, нарушений %d, %.2f с"
          % (" (индекс)" if staged else "", len(names), len(cuts), marked, known["total"],
             known["measured"], stale_count(cuts, known["files"], names), len(bad),
             time.monotonic() - t0))
    if len(unreadable) > 0:
        print("НЕ СЧИТАНЫ (это НЕ «чисто»): %s" % json.dumps(unreadable, ensure_ascii=False))
        return 2
    return 1 if len(bad) > 0 else 0


if __name__ == "__main__":
    import io_utf8
    io_utf8.force_utf8()
    sys.exit(main(sys.argv[1:]))
