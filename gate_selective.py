# -*- coding: utf-8 -*-
"""
gate_selective.py — селективный тест-гейт авто-применения (порт VPS-образцов
GATE_STEP_SELECTIVE d288947 / GATE_SINGLE_SELECTIVE 9c3c3e7).

ЗАЧЕМ. Полный прогон всех тест-модулей на КАЖДОМ авто-применении (после done дев-задачи,
рестарт userbot/moderbot) дорог и растёт линейно с числом тестов. VPS решает это селективно:
на ПРОМЕЖУТОЧНЫХ шагах цепи и на ОДИНОЧНЫХ задачах гоняем ТОЛЬКО тесты, затронутые изменёнными
файлами; на ФИНАЛЬНОМ шаге цепи (и на pre-push/self-update — вне этого модуля) остаётся ПОЛНЫЙ
гейт — неубираем. Любой сбой селектора (исключение / не смог сопоставить ни один тест) → откат к
ПОЛНОМУ гейту (fail-safe: узкий гейт не смеет пропустить регрессию тише полного).

Функция чистая (только git-независимый разбор строк + os-глоб по каталогу репо, без сети и
Telegram) — легко тестируется по образцу test_selfupdate_gate.py.

Включение — раздельными флагами .env (дефолт 0, поведение прежнее):
  GATE_STEP_SELECTIVE=1   — селектив на промежуточных шагах цепи (финал всё равно полный);
  GATE_SINGLE_SELECTIVE=1 — селектив на одиночных (не-цепь) дев-задачах.
"""

import os
import re

# Маркер шага локальной цепи «[шаг i/N родитель id] …» (match с начала) — тот же формат, что
# _STEP_RE в pc_orchestrator; держим копию здесь, чтобы модуль был самодостаточным (как selfupdate_gate).
_STEP_RE = re.compile(r"^\[шаг (\d+)/(\d+) родитель (\d+)\]")

# Режимы решения гейта (возврат decide):
MODE_OFF = "off"                 # фича выключена флагом → вызывающий гонит ПРЕЖНИЙ гейт (байт-в-байт)
MODE_SELECTIVE = "selective"     # гоним только затронутые тест-модули
MODE_FULL_FINAL = "full:final"   # финальный шаг цепи → полный гейт (неубираем)
MODE_FULL_FAILSAFE = "full:failsafe"  # селектор сорвался/ничего не сопоставил → полный гейт (fail-safe)


def env_on(name, env=None):
    """Флаг name=='1' в окружении (env или os.environ). Всё прочее (0/нет/пусто) → False."""
    src = env if env is not None else os.environ
    return (src.get(name) or "").strip() == "1"


def parse_step(text):
    """Разобрать маркер шага цепи из текста задачи. → (is_step: bool, i: int, n: int).
    Не шаг (одиночка/родитель/без маркера) → (False, 0, 0)."""
    m = _STEP_RE.match(str(text or ""))
    if not m:
        return False, 0, 0
    return True, int(m.group(1)), int(m.group(2))


def is_final_step(is_step, i, n):
    """Финальный шаг цепи (последний номер) → полный гейт. Одиночка (не шаг) финалом НЕ считаем
    (её судьбу решает GATE_SINGLE_SELECTIVE)."""
    return bool(is_step) and i >= n >= 1


def affected_test_modules(changed_paths, repo_dir):
    """Тест-модули, затронутые изменёнными .py: сам test_*.py и test_<stem> при наличии на диске.
    → отсортированный список. Порт _affected_test_modules (там REPO зашит; здесь — параметром)."""
    mods = set()
    for p in changed_paths or []:
        base = os.path.basename(str(p or ""))
        if not base.endswith(".py"):
            continue
        stem = base[:-3]
        if stem.startswith("test_"):
            mods.add(stem)
            continue
        cand = "test_" + stem
        if os.path.isfile(os.path.join(repo_dir, cand + ".py")):
            mods.add(cand)
    return sorted(mods)


def all_test_modules(repo_dir):
    """ПОЛНЫЙ набор тест-модулей репо (глоб test_*.py). → отсортированный список стемов.
    Это «полный гейт»: финальный шаг + fail-safe гонят его целиком."""
    mods = []
    for name in os.listdir(repo_dir):
        if name.startswith("test_") and name.endswith(".py"):
            mods.append(name[:-3])
    return sorted(mods)


def decide(changed_paths, repo_dir, *, is_step, step_i, step_n,
           step_selective, single_selective, affected_fn=None, all_fn=None):
    """Решить, какие тест-модули гнать и в каком режиме. → (mods, mode).

      • флаг для этого типа задачи выключен → (None, MODE_OFF): вызывающий гонит ПРЕЖНИЙ гейт;
      • финальный шаг цепи → (полный список, MODE_FULL_FINAL) — неубираемый полный гейт;
      • селектор сопоставил тесты → (затронутые, MODE_SELECTIVE);
      • селектор ничего не сопоставил ЛИБО упал исключением → (полный список, MODE_FULL_FAILSAFE).

    fail-safe тотальный: любое исключение внутри (в т.ч. в affected_fn) → полный гейт; и лишь если
    даже полный список не собрать → (None, MODE_OFF) как крайняя деградация (не хуже прежнего)."""
    affected_fn = affected_fn or affected_test_modules
    all_fn = all_fn or all_test_modules
    try:
        selective_on = step_selective if is_step else single_selective
        if not selective_on:
            return None, MODE_OFF
        if is_final_step(is_step, step_i, step_n):
            return all_fn(repo_dir), MODE_FULL_FINAL
        affected = affected_fn(changed_paths, repo_dir)
        if not affected:
            return all_fn(repo_dir), MODE_FULL_FAILSAFE   # не смог сопоставить ни один тест → полный
        return sorted(affected), MODE_SELECTIVE
    except Exception:
        try:
            return all_fn(repo_dir), MODE_FULL_FAILSAFE
        except Exception:
            return None, MODE_OFF
