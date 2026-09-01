#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deps_registry_check.py — СТОРОЖ РЕЕСТРОВ ЗАВИСИМОСТЕЙ ДЕМОНА, который успевает ДО КОММИТА.

ЗАЧЕМ ОН ВООБЩЕ ЕСТЬ (класс, а не случай). У демона два РУЧНЫХ реестра зависимостей —
`pc_orchestrator._ORCH_RUNTIME` (что грузится безусловно; на это смотрят ворота грязного дерева)
и `pc_orchestrator._ORCH_LAZY_UNCOVERED` (названный ОСТАТОК: ленивое, воротами не покрытое).
Оба обязаны совпадать с ФАКТОМ импортов на диске, и это стерегут три теста в `test_pc_orchestrator`.
Тесты свою работу делают, но узнаёт о красном не автор правки, а ДЕМОН — на self-update, когда
гоняет `-m unittest test_pc_orchestrator test_pc_local_dec`. Цена этого опоздания измерена дважды
за сутки 01.09.2026:

  • 30.08 (2f015bb) новый ВЕРХНИЙ импорт `proc_identity` — красное простояло ~2 суток и всё это
    время держало доставку правок демону закрытой;
  • 01.09 16:48 (298a387) `import model_name` в suggest.py — красное поймал тик 16:53:08
    («AssertionError: Items in the first set but not the second: 'model_name.py'»), закрыто
    17:21:52 (e9dc168), то есть полчаса эстафета стояла.

Оба раза расхождение существовало УЖЕ В МОМЕНТ КОММИТА, и увидеть его было нечем. Этот сторож —
ровно тот недостающий момент: он гоняет ТЕ ЖЕ САМЫЕ тесты-сторожа (не свою копию их логики —
копия разъехалась бы) и отвечает ДО того, как коммит лёг.

ПОЧЕМУ НЕ «СЧИТАТЬ РЕЕСТР АВТОМАТИЧЕСКИ» (развилка, решённая сознательно). Соблазн: пусть
`_ORCH_RUNTIME` считается замыканием сам — тогда новый верхний импорт попадает под ворота без
единой строки от автора. Отказались: оба теста-сторожа сравнивают ФАКТ с РУЧНЫМ кортежем, и если
кортеж начать считать тем же обходом, сравнение станет тавтологией — тесты перестанут уметь падать
вовсе. Для `_ORCH_LAZY_UNCOVERED` это ещё хуже: он не «список файлов», а ЗАПИСАННЫЙ ОСТАТОК —
признание, что дыра есть и почему её оставили. Автосчёт превратил бы признание в молчание.
Поэтому реестр остаётся ручным и осознанным, а автоматическим делается НАДЗОР: расхождение теперь
видно на коммите, а не через отказ эстафеты.

ТРИ ИСХОДА, а не два (правило полосы: «не смог проверить» ≠ «проверено и хорошо»):
  0 — сверено, расхождения нет (или в коммите нет ни одного .py — сверять нечего);
  1 — РАСХОЖДЕНИЕ названо поимённо: коммит держать;
  2 — сверить НЕ СМОГ (нет интерпретатора, тесты не загрузились, таймаут). Коммит НЕ держим:
      сторож — ранний слой, а жёсткая сеть (unittest-гейт демона) под ним осталась. Но молчать
      об этом нельзя, поэтому исход 2 всегда громкий.

Запуск: `python deps_registry_check.py [--staged] [--quiet]`
  --staged  сверять только если в индексе есть .py (режим pre-commit-хука);
  --quiet   молчать при зелёном (при 1 и 2 говорит всегда).
"""
import os
import re
import sys
import subprocess

REPO = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ТЕ ЖЕ тесты, что гоняет self-update демона, — а не их пересказ. Пересказ разъехался бы с
# оригиналом молча, и сторож начал бы стеречь позапрошлое правило.
GUARD_TESTS = (
    "test_pc_orchestrator.TestSelfUpdateDepClosure."
    "test_dirty_gate_watches_exactly_what_start_loads_unconditionally",
    "test_pc_orchestrator.TestSelfUpdateDepClosure."
    "test_lazy_dependencies_are_a_named_remainder_not_a_silent_gap",
    "test_pc_orchestrator.TestApprovalReachesExecutor."
    "test_orch_runtime_covers_every_top_import_of_daemon",
)

OK, DIVERGED, UNKNOWN = 0, 1, 2

REMEDY = (
    "ЧТО ДЕЛАТЬ: внести названный файл в реестр `pc_orchestrator.py` тем же способом, каким там\n"
    "  значатся соседи — строкой в кортеж плюс комментарий «чем опасна его грязь»:\n"
    "    • верхний импорт демона        → _ORCH_RUNTIME (ворота грязного дерева);\n"
    "    • ленивый (через suggest и т.п.) → _ORCH_LAZY_UNCOVERED (названный остаток).\n"
    "  Обойти сторож: git commit --no-verify (тогда красное поймает self-update демона — позже)."
)


def _venv_python():
    """Интерпретатор ЖИВОГО формата: тем же venv демон гоняет свой unittest-гейт.
    Нет его — берём тот, которым запустили сторожа (хук мог позвать системный python)."""
    cand = os.path.join(REPO, "venv", "Scripts", "python.exe")
    return cand if os.path.isfile(cand) else sys.executable


def staged_py(runner=None):
    """В индексе есть .py? → True/False/None (git не ответил — «не знаю», сверяем на всякий случай).
    Смотрим ИНДЕКС, а не рабочее дерево: хук зовут ровно на том, что человек собрался закоммитить."""
    try:
        r = (runner or subprocess.run)(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=NO_WINDOW)
    except Exception:
        return None
    if getattr(r, "returncode", 1) != 0:
        return None
    return any(ln.strip().lower().endswith(".py") for ln in (r.stdout or "").splitlines())


def classify(returncode, output, expected=len(GUARD_TESTS)):
    """Вывод unittest → (исход, слова). ЧИСТАЯ функция: ни git, ни подпроцессов — её и проверяют.

    Различать «тест упал» и «тест не загрузился» обязательно: первое — расхождение реестра
    (держим коммит), второе — переименовали/потеряли сам тест-сторож, и тогда сторож стережёт
    пустоту. Второе НЕ имеет права выглядеть зелёным ни одной веткой."""
    out = output or ""
    if "_FailedTest" in out or "ModuleNotFoundError" in out:
        return UNKNOWN, "тесты-сторожа НЕ ЗАГРУЗИЛИСЬ (переименованы? удалены?) — сверка не состоялась"
    m = re.search(r"Ran (\d+) tests?", out)
    if not m:
        return UNKNOWN, "unittest не сказал, сколько тестов прогнал — сверка не состоялась"
    ran = int(m.group(1))
    if ran != expected:
        return UNKNOWN, f"прогнано {ran} тестов из {expected} ожидаемых — сверка неполная"
    if returncode == 0:
        return OK, f"реестры зависимостей демона совпали с фактом импортов ({ran} теста-сторожа)"
    return DIVERGED, "РЕЕСТР ЗАВИСИМОСТЕЙ РАЗОШЁЛСЯ С ФАКТОМ ИМПОРТОВ"


def run_guards(py=None, runner=None, timeout=300):
    """Прогнать тесты-сторожа → (исход, слова, сырой хвост вывода)."""
    py = py or _venv_python()
    try:
        r = (runner or subprocess.run)(
            [py, "-m", "unittest", *GUARD_TESTS],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=NO_WINDOW)
    except Exception as e:
        return UNKNOWN, f"тесты-сторожа не запустились: {type(e).__name__}: {e}", ""
    out = (r.stderr or "") + (r.stdout or "")
    verdict, why = classify(r.returncode, out)
    return verdict, why, out


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    quiet = "--quiet" in argv
    if "--staged" in argv and staged_py() is False:
        if not quiet:
            print("сторож реестров: в индексе нет .py — сверять нечего")
        return OK
    verdict, why, out = run_guards()
    if verdict == OK:
        if not quiet:
            print("сторож реестров: " + why)
        return OK
    head = "❌ СТОРОЖ РЕЕСТРОВ ЗАВИСИМОСТЕЙ" if verdict == DIVERGED else "⚠️ СТОРОЖ РЕЕСТРОВ: НЕИЗВЕСТНО"
    print(f"{head}: {why}", file=sys.stderr)
    tail = "\n".join(ln for ln in out.splitlines() if ln.strip())[-1500:]
    if tail:
        print(tail, file=sys.stderr)
    if verdict == DIVERGED:
        print(REMEDY, file=sys.stderr)
    else:
        print("Коммит НЕ держим: под сторожем осталась жёсткая сеть — unittest-гейт демона.",
              file=sys.stderr)
    return verdict


if __name__ == "__main__":
    sys.exit(main())
