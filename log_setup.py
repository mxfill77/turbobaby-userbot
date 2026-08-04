# -*- coding: utf-8 -*-
"""
log_setup.py — единая ротация логов ПК-контура + РАЗВЕДЕНИЕ тестовых и боевых логов.

ТРИ БОЛИ, которые лечит модуль (первые две — живой факт аудита 22:27, третья — 31.07.2026):

1) РОТАЦИИ НЕ БЫЛО НИГДЕ. Все модули вешали голый `logging.FileHandler` — файл рос вечно:
   pc_agent.log 28.7 МБ, pricing.log 22.2 МБ, pc_orchestrator.log 11.2 МБ. На диске C: при этом
   оставалось 0.4 ГБ. Здесь — `RotatingFileHandler` с общим порогом и числом бэкапов.

2) ТЕСТЫ ПИСАЛИ В БОЕВОЙ ЛОГ. В `pretool_guard.log` 21:39 лежат фикстуры прогона
   (`clasp push`, `sqlite3 bookings.db "select 1"`, пустая команда) — их невозможно отличить от
   настоящих действий владельца, и они попали в разбор «последних 20 Allow» как реальные
   события. Разведение здесь: под тестом ЛЮБОЙ лог уезжает в temp, боевой файл не трогается.

3) ОДИН ФАЙЛ — ТРИ ПРОЦЕССА, И ПЕРЕКАТ НЕ ПРОХОДИЛ НИКОГДА. `pricing.py` — модуль-библиотека:
   он живёт ВНУТРИ `userbot_listen`, `moderation_bot` и `pc_orchestrator` (все три тянут
   `suggest`), и каждый вешал СВОЙ `RotatingFileHandler` на ОДИН `pricing.log`. На Windows
   перекат = `os.rename` боевого файла; файл, открытый другим процессом, не переименовывается
   (WinError 32), исключение уходит в `Handler.handleError` — у демона без stderr в никуда, —
   а САМА ЗАПИСЬ ТЕРЯЕТСЯ. Замер 31.07.2026: `pricing.log` замер на 23 302 269 байт со штампом
   22.07 21:39 — девять суток КАЖДАЯ строка котировки молча выбрасывалась, а «лог не растёт»
   выглядело как здоровье. Проба тем же днём: `pricing.log` и `delivery.log` заняты другим
   процессом, `dispatch_notify.log`/`pretool_guard.log` свободны.

   Лечится ЗДЕСЬ, двумя независимыми механизмами:
   * `handler_path` — файл РАЗВОДИТСЯ ПО ВЛАДЕЛЬЦУ: канонический `pricing.log` принадлежит
     процессу-хозяину (см. `log_owner`), любой ДРУГОЙ процесс пишет `pricing.<владелец>.log`.
     Один файл — один держатель, спорить за rename больше некому;
   * `_copytruncate` — перекат ВСЁ-ТАКИ ПРОХОДИТ там, где `rename` невозможен;
   * `SafeRotatingFileHandler` — отказ переката больше НЕ проглатывается и не стоит записей.

   Разведение по владельцу лечит только тех, кого можно развести. Один и тот же входной скрипт,
   запущенный ДВАЖДЫ (хуки `dispatch_notify.py` бегут по нескольку разом), получает одну и ту же
   кличку и снова спорит за канон — имя тут помочь не может в принципе. Для этого случая и живёт
   вторая ступень: `_copytruncate`.

Признак теста берём ПО ФАКТУ окружения, а не по вежливой договорённости «не забудь выставить»:
TESTING=1 (общий рубильник репо, ставится `test_isolation`), TURBOBABY_TEST_LOGS=1 (явный
переключатель только логов) или PYTEST_CURRENT_TEST (взводит сам pytest). Порядок наследования
env вниз по дереву процессов делает признак верным и для subprocess-тестов (гард запускается
именно так).

Пороги настраиваются env: LOG_MAX_BYTES (по умолчанию 5 МБ), LOG_BACKUPS (по умолчанию 3),
LOG_ROLLOVER_RETRY_SEC (пауза перед повтором сорвавшегося переката, по умолчанию 300 с).
Итого потолок на один лог = MAX × (BACKUPS + 1) ≈ 20 МБ.
"""

import os
import re
import sys
import time
import shutil
import logging
import tempfile
from logging.handlers import RotatingFileHandler

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUPS = 3
DEFAULT_ROLLOVER_RETRY_SEC = 300

TEST_PREFIX = "turbobaby_TESTING_"

# Общий файл-сигнал: сюда уходит КАЖДЫЙ отказ переката, из любого процесса. Смысл — чтобы
# «ротация не работает» было видно в ОДНОМ месте, а не только внутри распухшего лога, который
# ровно поэтому никто и не открывает.
ROTATE_ERROR_LOG = "log_rotation_errors.log"

# Владелец канонического имени лога — там, где имя файла НЕ совпадает с именем входного скрипта.
# Две живых пары; всё остальное совпадает (pc_agent.py→pc_agent.log и т.д.), и голден
# TestCanonicalLogNameHasOneOwner держит этот список честным.
LOG_OWNERS = {
    "userbot.log": "userbot_listen",
    "rc_remote_control.log": "rc_supervisor",
}

_OWNER_SAFE_RE = re.compile(r"[^0-9A-Za-z_-]+")


def _int_env(name, default):
    try:
        return int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


def max_bytes():
    return _int_env("LOG_MAX_BYTES", DEFAULT_MAX_BYTES)


def backups():
    return _int_env("LOG_BACKUPS", DEFAULT_BACKUPS)


def rollover_retry_sec():
    return _int_env("LOG_ROLLOVER_RETRY_SEC", DEFAULT_ROLLOVER_RETRY_SEC)


def _started_as_test_runner():
    """True ⇔ САМ ПРОЦЕСС запущен как тест-раннер (`python -m unittest …`, `pytest …`).

    Намеренно НЕ проверяем «есть ли unittest в sys.modules»: демон импортирует
    `gate_selective`, который гоняет тесты, — по такому признаку БОЕВОЙ pc_orchestrator.log
    молча уехал бы в temp, и живая диагностика исчезла бы ровно тогда, когда она нужнее всего.
    Спрашиваем именно способ запуска процесса."""
    try:
        spec = getattr(sys.modules.get("__main__"), "__spec__", None)
        if spec is not None and (spec.name or "").split(".")[0] in ("unittest", "pytest"):
            return True
        argv0 = os.path.basename(sys.argv[0] or "").lower()
        if argv0.startswith("pytest") or argv0.startswith("py.test"):
            return True
        # 25.07.2026: `python test_pc_orchestrator.py` НАПРЯМУЮ (без -m unittest) не взводит ни
        # TESTING, ни PYTEST_CURRENT_TEST — и строки теста уходили в БОЕВОЙ лог. Запуск файла
        # test_*.py — это тоже способ запустить тест, а не «unittest где-то в модулях»: боевые
        # процессы контура так не называются, ложного срабатывания у демона быть не может.
        if argv0.startswith("test_"):
            return True
        return os.path.normpath(sys.argv[0] or "").lower().endswith(
            os.path.join("unittest", "__main__.py"))
    except Exception:
        return False


def is_test_context(env=None):
    """True ⇔ идёт тестовый прогон. env — инъекция для тестов."""
    e = os.environ if env is None else env
    if (e.get("TURBOBABY_TEST_LOGS") or "").strip() not in ("", "0"):
        return True
    if (e.get("TESTING") or "").strip() not in ("", "0"):
        return True
    if (e.get("PYTEST_CURRENT_TEST") or "").strip():
        return True
    return env is None and _started_as_test_runner()


def log_path(filename, env=None):
    """Куда РЕАЛЬНО писать лог `filename`. Боевой прогон → файл в репо; тест → одноимённый файл
    в temp с префиксом. Абсолютный путь на входе уважаем (берём только имя для тест-ветки)."""
    filename = os.fspath(filename)
    name = os.path.basename(filename)
    if is_test_context(env):
        return os.path.join(tempfile.gettempdir(), TEST_PREFIX + name)
    return filename if os.path.isabs(filename) else os.path.join(HERE, name)


# ─────────── ФАЙЛ СОСТОЯНИЯ ПОД ТЕСТОМ (класс 05.08.2026: «гейт съел спул») ───────────────────
# Полный гейт УНИЧТОЖИЛ боевой спул ревизора — 12 недоставленных находок владельца. Изоляция в том
# тест-файле БЫЛА, но держалась на договорённости («каждый тест-класс подменяет константу сам») и
# до класса-нарушителя не доехала. Найти это грепом нельзя ПО УСТРОЙСТВУ дефекта: тест не упоминал
# ни одной константы состояния — боевой файл читала и переписывала позванная им живая функция
# демона. Значит правило обязано стоять не в тесте, а в самом пути.
#
# Дискриминатор тот же, что уже боевой у log_path выше: решает СПОСОБ ЗАПУСКА ПРОЦЕССА
# (`python -m unittest …`, `pytest`, `python test_x.py`), а не добрая воля теста. Он проверен
# продом — на нём же держится увод БОЕВОГО pc_orchestrator.log из-под тестов, — поэтому расширение
# его с логов на состояние нового режима отказа не вносит.
_TEST_STATE_DIR = None


def state_path(filename, env=None):
    """Куда РЕАЛЬНО лежит файл СОСТОЯНИЯ (спул, снимок, реестр). Бой → файл в репо; тест-прогон →
    одноимённый файл в ОДНОРАЗОВОМ каталоге temp, общем на процесс.

    Отдельно от `log_path` намеренно: лог под тестом достаточно увести в temp по имени, а состояние
    двух разных прогонов не должно встречаться в одном файле — поэтому каталог свой на процесс и
    создаётся лениво (в бою не создаётся вовсе)."""
    filename = os.fspath(filename)
    name = os.path.basename(filename)
    if not is_test_context(env):
        return filename if os.path.isabs(filename) else os.path.join(HERE, name)
    global _TEST_STATE_DIR
    if _TEST_STATE_DIR is None:
        _TEST_STATE_DIR = tempfile.mkdtemp(prefix=TEST_PREFIX + "state_")
    return os.path.join(_TEST_STATE_DIR, name)


# ─────────────── РАЗВЕДЕНИЕ ФАЙЛА ПО ПРОЦЕССАМ (боль 3) ──────────────────────────────────────

def owner_tag(argv0=None, env=None):
    """Кличка ПРОЦЕССА — по имени входного скрипта (`userbot_listen`, `moderation_bot`,
    `pc_orchestrator`). Именно она, а не PID: PID даёт новый файл на каждый рестарт и через
    неделю каталог не читается, а имя входа стабильно и сразу отвечает «чей это лог».
    Перекрывается env `TURBOBABY_LOG_OWNER` — для запусков через обёртку/`-c`, где argv[0]
    ничего не говорит."""
    e = os.environ if env is None else env
    try:
        forced = (e.get("TURBOBABY_LOG_OWNER") or "").strip()
        raw = forced or os.path.basename(os.fspath(
            sys.argv[0] if argv0 is None else argv0) or "")
        if not raw or raw.startswith("-"):          # `python -c …`, интерактив — владельца нет
            return "misc"
        stem = os.path.splitext(raw)[0]
        if stem == "__main__":                      # `python -m unittest` и подобные
            return "misc"
        return _OWNER_SAFE_RE.sub("-", stem).strip("-_").lower() or "misc"
    except Exception:
        return "misc"


def log_owner(name):
    """Чей КАНОНИЧЕСКОЕ (без суффикса) имя файла. По умолчанию — одноимённый скрипт
    (`pc_agent.log` → `pc_agent`), исключения объявлены в LOG_OWNERS. Для файла, который никакому
    входному скрипту не принадлежит (`pricing.log` — модуль-библиотека, процесса `pricing` нет),
    каноническое имя не занимает НИКТО: все писатели уходят под суффикс, и спорить за rename
    становится некому."""
    base = os.path.basename(os.fspath(name))
    declared = LOG_OWNERS.get(base.lower())
    if declared:
        return declared
    return os.path.splitext(base)[0].lower()


def handler_name(name, owner=None):
    """Имя файла для ДЕРЖАЩЕГО хендлера этого процесса: хозяину — каноническое, всем прочим —
    `<имя>.<владелец>.log`."""
    base = os.path.basename(os.fspath(name))
    who = owner or owner_tag()
    if who == log_owner(base):
        return base
    stem, ext = os.path.splitext(base)
    return "%s.%s%s" % (stem, who, ext)


def handler_path(filename, env=None, owner=None):
    """Путь для файлового хендлера — `log_path` + разведение по владельцу.

    Разводим ТОЛЬКО здесь, а не в `log_path`, и это не мелочь. Держит файл (и мешает rename)
    лишь тот, кто ОТКРЫЛ ЕГО НАДОЛГО, — то есть хендлер. Писатели в режиме open→write→close
    (гард: `_log` → `open(p,'a')`; `dispatch_notify._write_session_metrics` → дозапись в
    `pc_orchestrator.log`) файл не держат и ротации не мешают, поэтому им НЕЛЬЗЯ менять адрес:
    иначе единая лента аудита гарда развалилась бы на файл-на-процесс без всякой пользы."""
    p = os.fspath(filename)
    head, base = os.path.split(p)
    split = handler_name(base, owner)
    return log_path(os.path.join(head, split) if head else split, env)


# ─────────────── ОТКАЗ ПЕРЕКАТА НЕ ПРОГЛАТЫВАЕТСЯ ────────────────────────────────────────────

_ROTATE_FAILURES = []            # последние отказы переката этого процесса (для диагностики)
_ROTATE_FAILURES_MAX = 50


def rotation_failures():
    """Копия списка отказов переката в ЭТОМ процессе. Пусто ⇔ перекат ни разу не срывался."""
    return list(_ROTATE_FAILURES)


def _copytruncate(path, dest):
    """ПЕРЕКАТ ТАМ, ГДЕ `rename` НЕВОЗМОЖЕН: архив — копией, боевой файл — усечением на месте.

    Почему это вообще работает, когда `os.replace` падает WinError 32. Python открывает лог
    через `_wopen`/`_SH_DENYNO`, то есть отдаёт соседям `FILE_SHARE_READ|FILE_SHARE_WRITE`,
    но НЕ `FILE_SHARE_DELETE`. Переименование требует у файла права DELETE — его нет, отсюда
    отказ. А чтение и усечение просят ровно то, что соседи разрешили, — и проходят.
    Замер 31.07.2026 на боевом `pricing.log` (держали три процесса): `os.replace` → WinError 32,
    `copy2`+`truncate(0)` → успех, 23 302 269 байт уехали в архив.

    Почему это ЛЕЧИТ, а не просто «уменьшает файл». Чужой хендлер держит СВОЙ поток в режиме
    `"a"`: после усечения его `shouldRollover` меряет `seek(0,2)` → 0 < порога → перекат больше
    НЕ ЗАПРАШИВАЕТСЯ, и запись ложится в файл. Стенд 31.07: до усечения строка терялась
    (`handleError`, `Message: 'quote before truncate'`), после — легла. То есть соседей,
    застрявших в вечном отказе, это поднимает БЕЗ их рестарта.

    РАЗМЕН НАЗВАН ВСЛУХ: между `copy2` и `truncate` есть окно в миллисекунды, и строки, попавшие
    в него, не попадут ни в архив, ни в новый файл. Это осознанно: терять миллисекунду записей
    раз в 5 МБ несопоставимо дешевле, чем терять ВСЕ записи вечно, как было девять суток.
    Возвращает (переложили?, исключение|None). Не бросает."""
    try:
        shutil.copy2(path, dest)
        with open(path, "r+b") as f:
            f.truncate(0)
        return True, None
    except Exception as exc:
        return False, exc


def _rotate(path, lim, n):
    """Сам сдвиг path.N → path.N+1 и path → path.1. Возвращает (сдвинули?, исключение|None) —
    вызывающий решает, докладывать об отказе или молчать. Не бросает.

    Если `rename` не проходит (файл держит другой процесс), пробуем `_copytruncate`: перекат
    важнее способа переката. Наружу в этом случае уходит успех, а не отказ."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= lim:
            return False, None
        oldest = "%s.%d" % (path, n)
        if os.path.isfile(oldest):
            os.remove(oldest)
        for i in range(n - 1, 0, -1):
            src, dst = "%s.%d" % (path, i), "%s.%d" % (path, i + 1)
            if os.path.isfile(src):
                os.replace(src, dst)
        try:
            os.replace(path, path + ".1")
        except OSError as exc:
            ok, exc2 = _copytruncate(path, path + ".1")
            if not ok:
                # докладываем ИСХОДНУЮ причину (отказ rename), а не вторичную: она диагностична
                return False, exc
            _report_rotate_note(path, "rename отказал (%s), перекат прошёл copytruncate"
                                      % exc.__class__.__name__)
        return True, None
    except Exception as exc:
        return False, exc


def _signal_append(msg):
    """Дописать строку в общий файл-сигнал. САМ НЕ РОТИРУЕТ — иначе `_rotate` → `_report_*` →
    `_rotate` замкнулись бы в петлю. Никогда не бросает."""
    try:
        with open(log_path(ROTATE_ERROR_LOG), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _report_rotate_note(path, what):
    """Перекат ПРОШЁЛ, но обходным путём. Не отказ — в `rotation_failures()` не кладём и stderr
    не шумим; но в файл-сигнал строка идёт: у copytruncate есть окно потери в миллисекунды, и
    знать, что перекат идёт через него, надо. Объём — одна строка на 5 МБ лога."""
    msg = "ROTATE-NOTE %s | %s | %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), path, what)
    _signal_append(msg)
    return msg


def _report_rotate_failure(path, exc, extra=""):
    """Отказ переката ВИДЕН: строка в общий `log_rotation_errors.log`, строка в stderr и запись
    в памяти процесса (`rotation_failures()`). Прежнее поведение — голый `except: pass` — и
    сделало девятисуточную немоту `pricing.log` незаметной. Сам никогда не бросает: лечение не
    имеет права стоить дороже болезни."""
    msg = "ROTATE-FAIL %s | %s | %s: %s%s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), path, exc.__class__.__name__, exc, extra)
    try:
        _ROTATE_FAILURES.append(msg)
        del _ROTATE_FAILURES[:-_ROTATE_FAILURES_MAX]
    except Exception:
        pass
    try:
        _rotate(log_path(ROTATE_ERROR_LOG), 512 * 1024, 1)   # молча: петлю рвёт _signal_append
    except Exception:
        pass
    _signal_append(msg)
    try:
        if sys.stderr is not None:
            sys.stderr.write(msg + "\n")
    except Exception:
        pass
    return msg


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler, у которого сорвавшийся перекат ВИДЕН и НЕ СТОИТ ЗАПИСЕЙ.

    Штатный ведёт себя так: `emit` → `doRollover` кидает WinError 32 → `except` → `handleError`
    → строка потеряна. И так на КАЖДОЙ записи, пока файл держит чужой процесс, то есть вечно.

    Здесь у переката ДВЕ ступени, и первая почти всегда достаточна:
    1) `rename` — штатный, дешёвый;
    2) `_copytruncate` — когда `rename` невозможен (файл держит сосед). Перекат ВСЁ-ТАКИ
       ПРОХОДИТ: порог соблюдён, бэкап на месте, и застрявшие соседи оживают без рестарта.
       В файл-сигнал уходит ROTATE-NOTE — обходной путь не прячем.

    Если сорвались ОБЕ: (а) запись всё равно ложится в файл — растущий лог лучше немого;
    (б) ROTATE-FAIL в файл-сигнал, в stderr и в сам лог (первый раз за эпизод); (в) следующая
    попытка откладывается на `LOG_ROLLOVER_RETRY_SEC` — иначе перекат дёргался бы на каждой
    строке. Размен назван вслух: в этом (теперь редком) случае файл РАСТЁТ сверх порога —
    тихая потеря диагностики хуже видимого роста, а видимый рост чинится по сигналу."""

    def __init__(self, *args, **kwargs):
        RotatingFileHandler.__init__(self, *args, **kwargs)
        self._rollover_retry_after = 0.0
        self._rollover_failed = False

    def _reopen(self):
        """Базовый `doRollover` закрывает поток ДО падения `rename` — вернуть его на место."""
        if self.stream is None:
            try:
                self.stream = self._open()
            except Exception:
                self.stream = None

    def doRollover(self):
        now = time.time()
        if now < self._rollover_retry_after:
            return                        # пауза после отказа: про него уже сказано, не шумим
        try:
            RotatingFileHandler.doRollover(self)
        except Exception as exc:
            # Ступень 2. Базовый уже сдвинул бэкапы и освободил слот `.1` — упал он на самом
            # переименовании боевого файла, ровно туда и кладём копию.
            ok = False
            if self.backupCount > 0:
                ok, _ = _copytruncate(self.baseFilename, self.baseFilename + ".1")
            if ok:
                self._reopen()
                self._rollover_failed = False
                self._rollover_retry_after = 0.0
                _report_rotate_note(self.baseFilename,
                                    "rename отказал (%s), перекат прошёл copytruncate"
                                    % exc.__class__.__name__)
                return
            self._rollover_retry_after = now + rollover_retry_sec()
            first = not self._rollover_failed          # маркер В САМ ЛОГ — раз за эпизод
            self._rollover_failed = True
            msg = _report_rotate_failure(
                self.baseFilename, exc,
                " | не прошли ни rename, ни copytruncate; лог продолжит расти, повтор через %d с"
                % rollover_retry_sec())
            self._reopen()
            if first and self.stream is not None:
                try:
                    self.stream.write("!! %s\n" % msg)
                    self.stream.flush()
                except Exception:
                    pass
        else:
            self._rollover_failed = False
            self._rollover_retry_after = 0.0


def rotating_handler(filename, fmt="%(asctime)s | %(message)s", level=logging.INFO, env=None,
                     owner=None):
    """Ротируемый хендлер по разрешённому пути, РАЗВЕДЁННОМУ по процессу-владельцу. Падение на
    открытии файла НЕ роняет вызывающего (лог вторичен): вернём None, и модуль просто останется
    без файлового хендлера."""
    path = handler_path(filename, env, owner)
    try:
        h = SafeRotatingFileHandler(path, maxBytes=max_bytes(), backupCount=backups(),
                                    encoding="utf-8")
    except Exception:
        return None
    h.setFormatter(logging.Formatter(fmt))
    h.setLevel(level)
    return h


def rotate_if_needed(path, limit=None, keep=None):
    """Ротация для писателей БЕЗ модуля logging (гард пишет строку через open(..., 'a')).
    Сдвигает path.N → path.N+1 и path → path.1, оставляя `keep` бэкапов. Никогда не бросает,
    но и НЕ МОЛЧИТ: отказ уходит в `_report_rotate_failure`. Возврат — «сдвинули ли файл»,
    контракт прежний (False и «не пора», и «не смогли»), поэтому смотреть надо сигнал."""
    lim = max_bytes() if limit is None else limit
    n = backups() if keep is None else keep
    ok, exc = _rotate(path, lim, n)
    if exc is not None:
        _report_rotate_failure(path, exc, " | писатель без logging (append-режим)")
    return ok
