# -*- coding: utf-8 -*-
"""ТОЧКА СЕРИАЛИЗАЦИИ GIT ПОЛОСЫ ПК — один индекс, одна рука (04.09.2026, задача 238).

ЗАЧЕМ ВООБЩЕ. С этого дня полоса исполняет ДВА захода одновременно, и оба живут в ОДНОМ рабочем
дереве ``D:\\turbobaby-bot``. Индекс git в дереве ровно один, и он не рассчитан на двух писателей:
``git add``/``git commit`` берут ``.git/index.lock`` файлом, а проигравший не ждёт — он ПАДАЕТ
строкой ``fatal: Unable to create '.git/index.lock': File exists``. При одном заходе этого класса
не было по построению; при двух он становится штатной погодой.

ПОЧЕМУ ЗАМОК ОС, А НЕ СВОЙ ФАЙЛ-ФЛАЖОК. Своё «создам файл — удалю файл» ровно этот класс уже и
стоило нам: ночь 03.09 полоса стояла восемь часов на СТУХШЕМ замке индекса — держатель умер, а
файл остался, и снять его было некому. Поэтому здесь замок не изображается файлом, а берётся у
ядра: байтовый диапазон файла (``msvcrt.locking`` на Windows, ``fcntl.flock`` на POSIX). Такой
замок ПРИВЯЗАН К ОТКРЫТОМУ ДЕСКРИПТОРУ, и когда держатель умирает ЛЮБОЙ смертью — исключение,
kill, синий экран, обрыв питания — ядро закрывает его дескрипторы и снимает замок само. Стухнуть
ему нечем: файл ``git.lock`` живёт вечно и пустым, его никто никогда не удаляет, а «занят» — это
состояние ядра, а не содержимое файла.

ЧТО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ И ПОЧЕМУ.
  • Он НЕ удаляет ``.git/index.lock`` — ни своего, ни чужого. Удаление живого чужого замка портит
    индекс ровно тем способом, от которого мы защищаемся. Про стухший чужой замок модуль умеет
    только ГОВОРИТЬ (:func:`index_lock_report`), и это честный третий исход: «замок есть, держателя
    не вижу» — повод разбудить человека, а не повод самому взяться за нож.
  • Он НЕ обещает, что ребёнок-исполнитель им воспользуется. Дети — это ``claude -p``, они гоняют
    git своими руками через Bash. Демон даёт им адрес замка в окружении (``GIT_SERIAL_PC``) и
    строку в преамбуле; это ИНСТРУКЦИЯ, а не решётка, и так и назван предел в артефакте. Решётка
    стоит там, где её можно поставить: на СВОИХ git-вызовах демона.

ЧИСТОТА. Решает здесь одна функция — :func:`needs_lock` (по argv сказать, писатель ли это индекса).
Она не трогает ни диск, ни процессы, поэтому проверяема без git вовсе; инвариант ``GIT_SERIAL_PURE``
в ``test_git_serial_pc.py`` сторожит это обходом AST.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time

__all__ = ["hold", "run", "needs_lock", "index_lock_report", "lock_path",
           "LOCK_ENV", "DEFAULT_TIMEOUT", "GitSerialTimeout"]

REPO = os.path.dirname(os.path.abspath(__file__))

# ВРЕМЕННОЕ У ПОЛОСЫ НАЗЫВАЕТСЯ ЯВНО: замок живёт в tmp/git_serial_pc/ и больше нигде.
LOCK_DIR = os.path.join(REPO, "tmp", "git_serial_pc")
LOCK_NAME = "git.lock"

LOCK_ENV = "GIT_SERIAL_PC"            # адрес замка, который демон кладёт в окружение ребёнка
OWNER_ENV = "GIT_SERIAL_PC_OWNER"     # чьим именем подписывается держатель (диагностика)

# ПОЧЕМУ 900 с. Держатель занимает замок на ОДНУ git-команду, а не на заход: самая долгая из них —
# ``git push`` в сеть (щедрый потолок демона на git — 90 с) и ``git gc``. 900 с = десятикратный
# запас от самого долгого законного держателя, и при этом заметно меньше TASK_TIMEOUT (2700 с):
# заход, упёршийся в замок, обязан отдать честный отказ ВНУТРИ своего бюджета, а не быть убитым
# таймаутом задачи с непонятной причиной.
DEFAULT_TIMEOUT = float(os.getenv("GIT_SERIAL_PC_TIMEOUT", "") or 900.0)
POLL_SEC = 0.15                       # шаг опроса замка: дешевле секунды и не жжёт процессор

# Подкоманды git, которые ПИШУТ индекс или ссылки рабочего дерева. Список намеренно широкий:
# ложное «нужен замок» стоит миллисекунд ожидания, ложное «не нужен» стоит порченого индекса.
WRITE_SUBCOMMANDS = frozenset({
    "add", "am", "apply", "branch", "checkout", "cherry-pick", "clean", "commit", "gc",
    "merge", "mv", "pull", "push", "rebase", "reset", "restore", "revert", "rm",
    "stash", "switch", "tag", "update-index", "update-ref", "write-tree",
})

# Глобальные ключи git, стоящие ПЕРЕД подкомандой; часть из них несёт значение отдельным словом.
_GLOBAL_FLAGS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                      "--exec-path", "--config-env"})


class GitSerialTimeout(RuntimeError):
    """Замок не отдали за отведённое время. Отдельный тип — чтобы вызывающий отличил «не дождался»
    от «git отработал и вернул ошибку»: это разные новости и разные решения."""


def lock_path(path=None):
    """Полный путь файла-замка. Каталог создаём здесь: замок обязан существовать до первой нужды."""
    p = path or os.path.join(LOCK_DIR, LOCK_NAME)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except Exception:
        pass
    return p


def _git_subcommand(argv):
    """Первое слово-подкоманда в argv git-вызова | "" . Пропускает глобальные ключи.

    argv принимаем в ДВУХ видах, и оба живые: с ведущим ``git`` (``["git", "commit", …]`` —
    так зовёт CLI) и без него (``["commit", …]`` — так зовёт демон, у которого "git" подставляет
    ``_git_call``). Отличаем по первому слову, а не по длине."""
    args = [str(a) for a in (argv or [])]
    if args and os.path.basename(args[0]).lower() in ("git", "git.exe"):
        args = args[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-"):
            return a
        if a in _GLOBAL_FLAGS_WITH_VALUE:
            i += 2
            continue
        i += 1
    return ""


def needs_lock(argv):
    """Этот git-вызов ПИШЕТ индекс/ссылки и потому обязан идти под замком? → bool. ЧИСТАЯ функция.

    Читающие вызовы (``rev-parse``, ``log``, ``status``, ``diff``, ``merge-base``) замок НЕ берут
    СОЗНАТЕЛЬНО: их в обороте демона десятки на виток, и очередь на них удлинила бы виток, чей
    возраст меряют О2/О4, не купив ничего — читатели индекс не портят."""
    return _git_subcommand(argv) in WRITE_SUBCOMMANDS


# ── сам замок ────────────────────────────────────────────────────────────────────────────────
# ДВА СЛОЯ, И ОБА НУЖНЫ. Байтовый замок ядра принадлежит ДЕСКРИПТОРУ, поэтому два потока одного
# процесса, открывшие файл дважды, честно поспорят за него и на Windows, и на POSIX. Но опираться
# на это одно нельзя: `fcntl.flock` на Linux замок ПРОЦЕССА, и два потока получили бы его оба.
# Поэтому внутри процесса очередь держит `_PROC_LOCK`, а между процессами — ядро.
_PROC_LOCK = threading.Lock()
_TL = threading.local()               # глубина реентранса ЭТОГО потока


def _lock_fd(fd):
    """Взять байтовый замок НЕБЛОКИРУЮЩЕ. → True (взяли) | False (занят). Исключение = «занят»."""
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_fd(fd):
    """Снять байтовый замок. Отказ ГЛОТАЕМ: дескриптор всё равно закрывается следом, а ядро
    снимает замок при закрытии — то есть освобождение здесь ускоряет, но не решает."""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass


def _write_owner(path, owner):
    """Подпись держателя рядом с замком — ТОЛЬКО для человека и логов. Ни одна ветка решений её не
    читает: решает ядро. Поэтому её протухание безвредно, а отказ записи ничего не ломает."""
    try:
        with open(path + ".owner", "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "owner": str(owner or ""),
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    except Exception:
        pass


def _owner_note(path):
    """Кто, судя по подписи, держит замок — строкой для диагностики. «неизвестно» — законный ответ."""
    try:
        with open(path + ".owner", encoding="utf-8") as f:
            d = json.load(f)
        return f"pid={d.get('pid')} owner={d.get('owner') or '—'} с {d.get('at')}"
    except Exception:
        return "держатель не подписан (это не ошибка: решает ядро, а не подпись)"


@contextlib.contextmanager
def hold(owner="", timeout=None, path=None, sleeper=None, clock=None):
    """Контекст «я держу git полосы». Второй ЖДЁТ, а не падает; не дождался — :class:`GitSerialTimeout`.

    Реентранс разрешён и бесплатен: вложенный ``hold`` того же потока только считает глубину.
    Без этого первая же составная операция демона (``add`` под замком, внутри — ``commit``)
    встала бы на себя саму намертво."""
    depth = getattr(_TL, "depth", 0)
    if depth:
        _TL.depth = depth + 1
        try:
            yield "реентранс (замок уже наш)"
        finally:
            _TL.depth = depth
        return

    tmo = DEFAULT_TIMEOUT if timeout is None else float(timeout)
    p = lock_path(path)
    _sleep = sleeper or time.sleep
    _now = clock or time.monotonic
    t0 = _now()

    if not _PROC_LOCK.acquire(timeout=max(0.0, tmo)):
        raise GitSerialTimeout(
            f"git полосы занят СВОИМ ЖЕ процессом дольше {tmo:.0f}с (владелец «{owner}»): "
            "второй заход ждал очереди и не дождался")
    fd = None
    try:
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
        if os.path.getsize(p) < 1:      # замку нужен хотя бы один байт: диапазон блокируем на нём
            os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
        waited = 0.0
        while True:
            if _lock_fd(fd):
                break
            spent = _now() - t0
            if spent >= tmo:
                raise GitSerialTimeout(
                    f"git полосы занят другим процессом дольше {tmo:.0f}с ({_owner_note(p)}); "
                    f"наш заход «{owner}» ждал и НЕ дождался — индекс не тронут")
            _sleep(POLL_SEC)
            waited = spent
        _write_owner(p, owner)
        _TL.depth = 1
        try:
            yield (f"взят сразу" if waited < POLL_SEC else f"взят после ожидания {waited:.1f}с")
        finally:
            _TL.depth = 0
            _unlock_fd(fd)
    finally:
        if fd is not None:
            try:
                os.close(fd)            # закрытие — вторая, безусловная дорога к снятию замка
            except Exception:
                pass
        _PROC_LOCK.release()


def run(argv, cwd=None, timeout=None, lock_timeout=None, owner="", runner=None, env=None):
    """Выполнить git-команду ПОД замком, если она пишет индекс. → CompletedProcess.

    Читающая команда идёт мимо замка — см. :func:`needs_lock`."""
    args = list(argv or [])
    if args and os.path.basename(str(args[0])).lower() not in ("git", "git.exe"):
        args = ["git"] + args
    _run = runner or subprocess.run
    kw = dict(cwd=cwd or REPO, capture_output=True, text=True,
              encoding="utf-8", errors="replace", timeout=timeout)
    if env is not None:
        kw["env"] = env
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not needs_lock(args):
        return _run(args, **kw)
    with hold(owner=owner or " ".join(args[:2]), timeout=lock_timeout):
        return _run(args, **kw)


def index_lock_report(repo=None, now=None):
    """Что сейчас с ``.git/index.lock`` — ФАКТАМИ, без приговора и без ножа.

    → dict: ``exists`` (bool), ``path``, ``age_sec`` (float|None — возраст неизвестен, и это
    отдельный ответ), ``note`` (строка человеку). Функция НИЧЕГО НЕ УДАЛЯЕТ и удалять не умеет:
    живой чужой замок — это работающий git, а не мусор, и разница между ними не выводится из
    возраста файла."""
    root = repo or REPO
    p = os.path.join(root, ".git", "index.lock")
    if not os.path.exists(p):
        return {"exists": False, "path": p, "age_sec": None,
                "note": "замка индекса нет — индекс свободен"}
    try:
        age = max(0.0, (now if now is not None else time.time()) - os.path.getmtime(p))
    except Exception:
        age = None
    if age is None:
        note = ("замок индекса ЕСТЬ, возраст неизвестен (файл не опрошен) — судить о нём нечем, "
                "это «не знаю», а не «стух»")
    elif age < 60:
        note = f"замок индекса ЕСТЬ, возраст {age:.0f}с — похоже на живой git, ждать"
    else:
        note = (f"замок индекса ЕСТЬ и ему {age:.0f}с — законной операции столько не нужно. "
                "Похоже на СТУХШИЙ замок (класс ночи 03.09). Снимать его — решение человека: "
                "проверь, нет ли живого git по этому дереву, и только тогда убирай файл вручную")
    return {"exists": True, "path": p, "age_sec": age, "note": note}


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────

def main(argv=None):
    """``python git_serial_pc.py -- git commit -m "…"`` — выполнить команду под замком полосы.

    Отдельные режимы: ``--status`` (что с индексом и с замком) и ``--hold N`` (держать замок N
    секунд — этим живёт отрицательный тест: он моделирует ЗАНЯТЫЙ индекс без настоящего git)."""
    ap = argparse.ArgumentParser(add_help=True, description="сериализация git полосы ПК")
    ap.add_argument("--owner", default=os.getenv(OWNER_ENV, "") or "cli")
    ap.add_argument("--timeout", type=float, default=None, help="сколько ждать замок, с")
    ap.add_argument("--status", action="store_true", help="показать состояние индекса и замка")
    ap.add_argument("--hold", type=float, default=None, help="держать замок N секунд и выйти")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- <команда git>")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if a.status:
        rep = index_lock_report()
        print(rep["note"])
        print("замок полосы: " + _owner_note(lock_path()))
        return 0
    if a.hold is not None:
        with hold(owner=a.owner, timeout=a.timeout) as how:
            print(f"замок взят ({how}), держу {a.hold}с", flush=True)
            time.sleep(a.hold)
        return 0
    cmd = [c for c in a.cmd if c != "--"]
    if not cmd:
        ap.error("нечего выполнять: команду передают после --")
    try:
        p = run(cmd, owner=a.owner, lock_timeout=a.timeout)
    except GitSerialTimeout as e:
        print(str(e), file=sys.stderr)
        return 75          # EX_TEMPFAIL: «не дождался», а не «git отказал»
    if p.stdout:
        sys.stdout.write(p.stdout)
    if p.stderr:
        sys.stderr.write(p.stderr)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
