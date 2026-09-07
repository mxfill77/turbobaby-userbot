# -*- coding: utf-8 -*-
"""
proc_bytes.py — ПРИБОР, КОТОРЫЙ ГОВОРИТ, КАКИЕ БАЙТЫ ПРОИЗВЕЛИ ЭТОТ ОТВЕТ.

ЗАЧЕМ ОН ЕСТЬ (класс, а не случай; числа замерены 07.09.2026, разбор —
`docs/artifacts/2026-09-07-какой-код-исполняет-живой-бот.md`). У живого бота НЕТ ОДНОГО КОММИТА,
которым его можно назвать: он исполняет смесь трёх возрастов —

  • **19 стартовых модулей** байтами того момента, когда процесс запустился (03.09 05:02:13);
    из них **12 уже разошлись с диском**, и весь ценовой узел живой бот держит устаревшим;
  • **59 ленивых модулей**, читаемых с диска ПРИ ПЕРВОМ ВЫЗОВЕ; момент их открытия **не записан
    нигде** — ни в логе, ни в локе, ни в одном файле состояния;
  • **6 горячих файлов данных**, читаемых свежими на каждом вызове (`price_source.json`,
    `trainer_rules.json`, `trainer_log_doc.json`, `playbook.md`, `turbobaby_faq_v1.md`,
    `park_list.md`), из них три — ВНЕ git вовсе.

Пока прибора нет, каждая заплата чинит слагаемое: отпечаток замыкания слеп к файлам, ворота их
не держат, ленивый импорт идёт мимо ворот — это не три дефекта, а три следствия одного незнания.

ЧТО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ. Он не судит жизнь процесса, не поднимает и не трогает процессов, не
пишет в мозг и никого не перезапускает. Он только РЕГИСТРИРУЕТ и ОТВЕЧАЕТ.

────────────────────────── КОНТРАКТ (четыре части, взяты дословно из §6 артефакта) ─────────────

ЧАСТЬ 1. ОЖИДАЕМЫЙ РЕЗУЛЬТАТ — опровергаемое утверждение о КОНКРЕТНОМ ответе:
    «Ответ, отправленный клиенту в момент T, произведён поимённо названным набором байт: для
     каждого модуля, РЕАЛЬНО загруженного к моменту T, известны путь и sha256 содержимого,
     прочитанного процессом; для каждого файла данных, прочитанного при подготовке этого ответа,
     известны путь и sha256 прочитанного текста.»
  Три свойства, без которых результат не тот: (а) речь про ЗАГРУЖЕННЫЕ, а не про «числящиеся в
  замыкании» — незагруженный модуль обязан ОТСУТСТВОВАТЬ, а не показываться «как в коммите»;
  (б) снимок берётся В МОМЕНТ ОТВЕТА, а не старта; (в) единицей служит СОДЕРЖИМОЕ, а не коммит.

ЧАСТЬ 2. ИСТОЧНИК ИСТИНЫ — сам процесс и больше никто: `sys.modules` знает, что процесс
  действительно загрузил; байты — те, что отдал загрузчик. Ни git, ни карта `_FILE_PROCESS_RULES`,
  ни `client_contour.closure`, ни `child_raise_commit.json` источником не являются (первые три
  описывают ДИСК, четвёртый — НАМЕРЕНИЕ: он берёт `rev-parse HEAD` рабочего дерева и пишется даже
  при неудавшемся подъёме). Наружу это выходит СТРОКОЙ В СОБСТВЕННОМ ЛОГЕ БОТА, рядом со строкой
  отправки, — она отличима грепом по `PROC_BYTES` и несёт идентификатор ответа.

ЧАСТЬ 3. СПОСОБ НАБЛЮДЕНИЯ — сверка СОДЕРЖИМОГО ПОИМЁННО, а не одно слово на процесс. Исходов
  пять, и каждый нужен: `совпало`, `разошлось`, `нет в отпечатке` (модуль не загружался — это
  ОТДЕЛЬНЫЙ исход, а не «совпало»), `вне git` (файл, которого коммит не знает), `не проверено`
  (провенанс слабый — байты взяты с диска, а не у процесса). Негодные признаки, каждый запрещён
  замером: живой PID (жив четверо суток, держит 12 стухших модулей), факт рестарта (ставит только
  19 стартовых, 59 ленивых не читает вовсе), `HEAD` дерева, `child_raise_commit.json`, `ok=True`
  моста, `live_child_base()` (угадывает коммит по времени старта).

ЧАСТЬ 4. ОТРИЦАТЕЛЬНЫЙ ТЕСТ — приёмка. Состояния, где прибор ВЫГЛЯДИТ отвечающим верно, а байты
  другие; все три живут голденами в `test_proc_bytes.py`:
  ОТ-1 «зелёный рестарт»: PID новый, коммит записан, баннер в логе — а дверь A4 за заход не
       открывалась, значит 59 имён в процесс не вошли ВОВСЕ. Ответ обязан быть «нет в отпечатке».
  ОТ-2 «отпечаток со старта»: снят при запуске и совпал байт-в-байт — а через час дверь прочитала
       редакцию суток новее. Отпечаток СТАРШЕ ответа обязан быть отвергнут ОТДЕЛЬНЫМ исходом.
  ОТ-3 «все модули совпали, а текст другой»: 78 из 78 зелены, но ответ произвели ещё и данные,
       которых коммит не знает (`playbook.md`, `lesson_store.tsv` — вне git; `price_source.json` —
       в git, но горячий). Прибор, считающий ТОЛЬКО модули, проходит ОТ-3 и потому не принят.

────────────────────────── КАК ЭТО УСТРОЕНО ────────────────────────────────────────────────────

Два регистратора, оба ставятся `install()` и оба ничего не спрашивают у вызывающего:

1. КОД — свой `sys.meta_path`-искатель. Он не ищет сам: спрашивает остальных, а найденной spec
   оборачивает загрузчик. `get_data` отдаёт ТЕ САМЫЕ байты, которые процесс сейчас скомпилирует —
   провенанс `чтение`, сильнейший. Если сработал кэш `__pycache__` (275 файлов на 07.09), исходник
   загрузчиком не читается вовсе — тогда пишем хеш исходника, снятый в ту же секунду, провенансом
   `кэш`: связь с байтами процесса здесь держится проверкой mtime+size внутри самого загрузчика, и
   это СЛАБЕЕ, поэтому названо отдельным словом, а не спрятано.
   Момент загрузки записывается ВСЕГДА — ровно этого сегодня нет нигде, и ровно это делает
   ленивую дверь наблюдаемой.

2. ДАННЫЕ — аудит-ловушка `sys.addaudithook` на событии `open`. Она видит КАЖДОЕ открытие файла в
   процессе, включая те, что делает чужой код без единой правки на месте вызова. Пишем только
   ЧТЕНИЯ файлов репозитория с расширением из ПОЛОЖИТЕЛЬНОГО перечня. Перечень положительный
   намеренно: список-исключение вынуждает назвать в коде расширение секретного файла, и гард ПК
   уже отказал разведке 07.09 ровно за это. Побочно положительный перечень и защищает: файл
   секретов расширения из перечня не имеет и в отпечаток не попадёт ни одной веткой.

ЦЕНА И ГРАНИЦЫ, названные вслух:
  • Аудит-ловушка НЕСНИМАЕМА (так устроен Python). Поэтому вся её работа стои́т за флагом `_ARMED`,
    а `disarm()` делает её пустым `return` — снять нельзя, обезвредить можно.
  • Хеш кэшируется по (путь, mtime_ns, размер) — горячий файл, читаемый каждый вызов, хешируется
    один раз на редакцию. Файл больше 8 МБ не хешируется: пишем размер и пометку `велик`.
  • Модуль, вошедший в процесс ДО установки прибора, получает провенанс `добор` — это хеш ДИСКА,
    а не байтов процесса, и сверка честно зовёт его `не проверено`. Иначе прибор врал бы ровно тем
    способом, от которого лечит.
  • Прибор ставится ВНУТРЬ процесса и работает с момента установки. Живым ботам он достанется
    только при следующем их подъёме: подъём — отдельное решение владельца, здесь его нет.
  • Разряда C (`exec`/`eval`/`importlib.reload`) в замыкании ботов ноль — и всё же событие `exec`
    ловится тоже: ноль сегодня не есть ноль завтра.

Запуск руками (только чтение, ничего не пишет):
    venv/Scripts/python.exe proc_bytes.py --last
    venv/Scripts/python.exe proc_bytes.py --verify <коммит>
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = 1

# Строка наружу отличима грепом одним словом — так же, как METRICS и ROTATE-FAIL на этой полосе.
LINE_TAG = "PROC_BYTES"
LEDGER_FILE = "proc_bytes.jsonl"
LEDGER_MAX_BYTES = 4 * 1024 * 1024
LEDGER_KEEP = 3

# ── ПРОВЕНАНС: откуда взялись байты. Порядок = сила; слабое не имеет права выглядеть сильным ────
VIA_READ = "чтение"        # байты отданы загрузчиком процессу — сильнейший
VIA_PYC = "кэш"            # процесс взял .pyc; исходник хеширован в ту же секунду
VIA_BACKFILL = "добор"     # модуль был в процессе ДО установки прибора: хеш диска, не процесса
_RANK = {VIA_BACKFILL: 1, VIA_PYC: 2, VIA_READ: 3}
WEAK_VIA = (VIA_BACKFILL,)

# ── ИСХОДЫ СВЕРКИ (часть 3 контракта). Их пять, и «нет в отпечатке» — не «совпало» ──────────────
V_MATCH = "совпало"
V_DIFF = "разошлось"
V_ABSENT = "нет в отпечатке"
V_UNTRACKED = "вне git"
V_WEAK = "не проверено"
V_UNEXPECTED = "вне ожидания"   # в отпечатке есть, а судить нечем — молчать об этом нельзя

OK = "СОВПАЛО"
BROKEN = "РАЗОШЛОСЬ"
PARTIAL = "НЕПОЛНО"
STALE = "ОТПЕЧАТОК СТАРШЕ ОТВЕТА"

# Положительный перечень — см. шапку. Расширения секретов здесь нет и быть не может.
DATA_EXT = (".json", ".jsonl", ".md", ".tsv", ".csv", ".txt", ".yaml", ".yml", ".xml", ".html",
            ".prompt", ".sql", ".tmpl")
SKIP_PARTS = ("__pycache__", ".git", "venv", "node_modules", "site-packages")
MAX_HASH_BYTES = 8 * 1024 * 1024

_local = threading.local()
_HASH_CACHE = {}
_HASH_CACHE_MAX = 4096


# ─────────────────────────── мелкая механика путей и хешей ─────────────────────────────────────

def _norm(path):
    """Абсолютный путь, если он ВНУТРИ репозитория и не в служебном каталоге; иначе None."""
    try:
        if isinstance(path, bytes):
            path = path.decode("utf-8", "replace")
        if not isinstance(path, str) or not path:
            return None
        ap = os.path.abspath(path)
    except Exception:
        return None
    a, b = (ap.lower(), HERE.lower()) if os.name == "nt" else (ap, HERE)
    if not a.startswith(b + os.sep):
        return None
    low = a.replace("\\", "/")
    for part in SKIP_PARTS:
        if ("/" + part + "/") in low:
            return None
    return ap


def rel(path):
    """Путь ОТ КОРНЯ РЕПО прямыми слэшами — единственная форма имени в отпечатке.

    Абсолютный путь в отпечатке был бы непереносим и несравним с `git show <коммит>:<путь>`."""
    try:
        r = os.path.relpath(os.path.abspath(path), HERE)
    except Exception:
        return str(path)
    return r.replace("\\", "/")


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    """(sha, размер, пометка). Кэш по (путь, mtime_ns, размер): горячий файл хешируется один раз
    на редакцию, а смена редакции видна сразу — тот же приём, что у `price_source.load`."""
    try:
        st = os.stat(path)
    except OSError:
        return None, None, "нет файла"
    key = (path, st.st_mtime_ns, st.st_size)
    hit = _HASH_CACHE.get(key)
    if hit is not None:
        return hit, st.st_size, None
    if st.st_size > MAX_HASH_BYTES:
        return None, st.st_size, "велик"
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        sha = h.hexdigest()
    except OSError as exc:
        return None, st.st_size, "не прочитан: %s" % exc.__class__.__name__
    if len(_HASH_CACHE) >= _HASH_CACHE_MAX:
        _HASH_CACHE.clear()
    _HASH_CACHE[key] = sha
    return sha, st.st_size, None


def _iso(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def pyc_ties_source(cached, origin):
    """ЧЕМ ДЕРЖИТСЯ ПРОВЕНАНС `кэш` — и держится ли вообще.

    Замер 07.09: на этой машине `__pycache__` тёплый (275 файлов), поэтому загрузчик исходник почти
    никогда не читает — сильный провенанс `чтение` в живой работе редок, и молчать об этом нельзя.
    Связь «байты, которые исполнил процесс» ↔ «файл, который мы хешируем» держит здесь ЗАГОЛОВОК
    КЭША: в нём лежат mtime и размер исходника на момент компиляции, и сам загрузчик отказывается
    от кэша при расхождении. Сверяем этот заголовок со `stat` исходника и говорим ВСЛУХ, сошлось ли.

    Возврат — пометка строкой (или None, если сверить нечем). Никогда не бросает."""
    try:
        if not isinstance(cached, str) or not os.path.isfile(cached):
            return "кэш не назван"
        with open(cached, "rb") as f:
            head = f.read(16)
        if len(head) < 16:
            return "заголовок кэша короток"
        flags = int.from_bytes(head[4:8], "little")
        if flags & 0b1:
            return "кэш на хеше исходника (не на времени)"
        mtime = int.from_bytes(head[8:12], "little")
        size = int.from_bytes(head[12:16], "little")
        st = os.stat(origin)
        if mtime == (int(st.st_mtime) & 0xFFFFFFFF) and size == (st.st_size & 0xFFFFFFFF):
            return "кэш сверен с исходником"
        return "КЭШ РАЗОШЁЛСЯ С ИСХОДНИКОМ"
    except Exception:
        return None


# ─────────────────────────── ЖУРНАЛ ПРОЦЕССА (то, что процесс о себе знает) ────────────────────

class Ledger(object):
    """Что процесс РЕАЛЬНО прочитал: модули (имя → байты) и файлы данных (путь → байты).

    Порядковый номер `seq` растёт на каждую новую запись и служит ровно одному: отделить двери,
    открывшиеся МЕЖДУ двумя ответами, от загруженных когда-то раньше. Без него «прибор видит
    ленивую дверь» осталось бы словами."""

    def __init__(self, clock=None):
        self.clock = clock or time.time
        self.modules = {}
        self.data = {}
        self.installed_at = None
        self.seq = 0
        self.emitted_seq = 0
        self.exec_events = 0
        self.exec_where = []
        self._lock = threading.Lock()

    def module_paths(self):
        """Абсолютные пути уже записанных модулей — чтобы штатный запуск модуля не считался
        динамическим исполнением."""
        return {os.path.join(HERE, r["путь"].replace("/", os.sep)) for r in self.modules.values()}

    # — код —
    def note_module(self, name, path, sha, via, size=None, note=None):
        with self._lock:
            old = self.modules.get(name)
            if old is not None and _RANK.get(old.get("как"), 0) > _RANK.get(via, 0):
                return old      # сильный провенанс не понижаем слабым: добор не смеет затереть чтение
            # Равный провенанс ЗАМЕЩАЕТ: повторная загрузка того же имени — это НОВЫЕ байты и новый
            # момент, и старый хеш здесь был бы прямой ложью (ровно случай ОТ-2).
            now = self.clock()
            self.seq += 1
            rec = {"имя": name, "путь": rel(path), "sha": sha, "как": via,
                   "размер": size, "когда": _iso(now), "ts": now, "seq": self.seq}
            if note:
                rec["пометка"] = note
            self.modules[name] = rec
            return rec

    # — данные —
    def note_data(self, path, sha, size=None, note=None):
        with self._lock:
            now = self.clock()
            key = rel(path)
            old = self.data.get(key)
            if old is not None and old.get("sha") == sha:
                old["читан"] = old.get("читан", 1) + 1
                old["последний"] = _iso(now)
                return old
            self.seq += 1
            rec = {"путь": key, "sha": sha, "размер": size, "когда": _iso(now), "ts": now,
                   "читан": 1, "последний": _iso(now), "seq": self.seq}
            if note:
                rec["пометка"] = note
            self.data[key] = rec
            return rec

    def since(self, seq):
        """Имена и пути, появившиеся ПОСЛЕ номера seq, — то есть двери, открывшиеся к этому ответу."""
        mods = sorted(r["имя"] for r in self.modules.values() if r["seq"] > seq)
        dats = sorted(r["путь"] for r in self.data.values() if r["seq"] > seq)
        return mods, dats


_LEDGER = Ledger()
_ARMED = False
_INSTALLED = False
_FINDER = None


def ledger():
    return _LEDGER


def armed():
    return _ARMED


# ─────────────────────────── РЕГИСТРАТОР КОДА: свой искатель импорта ───────────────────────────

def _wrap_loader(spec):
    """Обернуть загрузчик найденной spec так, чтобы байты и МОМЕНТ загрузки попали в журнал.

    Две точки, а не одна, и обе нужны:
      • `get_data(<исходник>)` — байты, которые процесс СЕЙЧАС скомпилирует. Провенанс `чтение`.
      • `exec_module` — страховка на случай попадания в `__pycache__`: тогда исходник загрузчиком
        не читается вовсе, и без этой точки ленивая дверь осталась бы незаписанной. Провенанс
        `кэш` — слабее, и назван слабее.
    Ошибка обёртки не имеет права стоить импорта: любое исключение глотаем и отдаём spec как есть."""
    try:
        loader = getattr(spec, "loader", None)
        origin = getattr(spec, "origin", None)
        if loader is None or not isinstance(origin, str) or not origin.endswith(".py"):
            return spec
        if _norm(origin) is None:
            return spec                      # чужое дерево (stdlib, venv) — не наш предмет
        if getattr(loader, "_proc_bytes_wrapped", False):
            return spec
        name = spec.name

        get_data = getattr(loader, "get_data", None)
        if callable(get_data):
            def _get_data(path, _orig=get_data, _name=name, _origin=origin):
                data = _orig(path)
                try:
                    if _ARMED and isinstance(path, str) and path.endswith(".py") \
                            and os.path.abspath(path) == os.path.abspath(_origin):
                        _LEDGER.note_module(_name, path, sha_bytes(data), VIA_READ, len(data))
                except Exception:
                    pass
                return data
            try:
                loader.get_data = _get_data
            except Exception:
                pass

        exec_module = getattr(loader, "exec_module", None)
        if callable(exec_module):
            def _exec_module(module, _orig=exec_module, _name=name, _origin=origin):
                try:
                    if _ARMED and _name not in _LEDGER.modules:
                        sha, size, note = sha_file(_origin)
                        tie = pyc_ties_source(getattr(module, "__cached__", None), _origin)
                        note = "; ".join(x for x in (note, tie) if x) or None
                        _LEDGER.note_module(_name, _origin, sha, VIA_PYC, size, note)
                except Exception:
                    pass
                return _orig(module)
            try:
                loader.exec_module = _exec_module
            except Exception:
                pass

        try:
            loader._proc_bytes_wrapped = True
        except Exception:
            pass
    except Exception:
        pass
    return spec


class _Finder(object):
    """Искатель, который сам ничего не ищет: спрашивает ОСТАЛЬНЫХ и оборачивает найденное.

    Так он не подменяет механику импорта (одна из немногих вещей, ломать которую нельзя молча) —
    он стои́т сбоку. Защита от рекурсии — по ИМЕНИ модуля, а не флагом на поток: флаг на поток
    терял бы вложенные импорты, а имя точно."""

    def find_spec(self, fullname, path=None, target=None):
        if not _ARMED:
            return None
        busy = getattr(_local, "busy", None)
        if busy is None:
            busy = _local.busy = set()
        if fullname in busy:
            return None
        busy.add(fullname)
        try:
            for finder in list(sys.meta_path):
                if finder is self:
                    continue
                fs = getattr(finder, "find_spec", None)
                if fs is None:
                    continue
                try:
                    spec = fs(fullname, path, target)
                except ImportError:
                    spec = None
                except Exception:
                    spec = None
                if spec is not None:
                    return _wrap_loader(spec)
            return None
        finally:
            busy.discard(fullname)


# ─────────────────────────── РЕГИСТРАТОР ДАННЫХ: аудит-ловушка ─────────────────────────────────

def _is_read_mode(mode, flags):
    """Чтение ли это. Пишущее открытие ответа не производит — `playbook.md` бот и ЧИТАЕТ, и ПИШЕТ,
    и смешивать эти два события значило бы звать запись входом ответа."""
    if isinstance(mode, str) and mode:
        return ("r" in mode) and ("+" not in mode)
    if isinstance(flags, int):
        try:
            acc = flags & getattr(os, "O_ACCMODE", 3)
        except Exception:
            return False
        return acc == getattr(os, "O_RDONLY", 0)
    return False


def _audit(event, args):
    """Ловушка событий. НИКОГДА не бросает: исключение из аудит-хука отменяет саму операцию, то
    есть сломанный прибор уронил бы боевое чтение. Цена ошибки здесь выше цены пропуска."""
    try:
        if not _ARMED:
            return
        if event == "exec":
            # Разряд C артефакта — ДИНАМИЧЕСКОЕ исполнение НАШЕГО кода, и сегодня его ноль.
            # Счётчик пришлось чинить ДВАЖДЫ, и оба промаха стоит назвать: голое событие дало 865
            # (в `exec` идёт штатный запуск каждого модуля и `namedtuple` из stdlib), а после
            # первой правки — 24, потому что `os.path.abspath("<string>")` разрешает выдуманное имя
            # ОТ РАБОЧЕГО КАТАЛОГА, и чужой `<string>` выглядел файлом репозитория. Отсюда правило:
            # считаем только СУЩЕСТВУЮЩИЙ .py нашего дерева, которого нет среди записанных модулей.
            # Названная граница: `exec` строки к дереву не привязан и в счёт не идёт вовсе.
            try:
                fn = getattr(args[0] if args else None, "co_filename", None)
                if isinstance(fn, str) and fn.endswith(".py"):
                    ap = _norm(fn)
                    if ap and os.path.isfile(ap) and ap not in _LEDGER.module_paths():
                        _LEDGER.exec_events += 1
                        _LEDGER.exec_where.append(rel(ap))
                        del _LEDGER.exec_where[20:]
            except Exception:
                pass
            return
        if event not in ("open", "io.open"):
            return
        if getattr(_local, "hashing", False):
            return                          # наше собственное чтение файла — не вход ответа
        path = args[0] if args else None
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        if not _is_read_mode(mode, flags):
            return
        ap = _norm(path)
        if ap is None:
            return
        ext = os.path.splitext(ap)[1].lower()
        if ext not in DATA_EXT:
            return
        if os.path.basename(ap) == LEDGER_FILE:
            return                          # свой же журнал входом ответа не является
        _local.hashing = True
        try:
            sha, size, note = sha_file(ap)
        finally:
            _local.hashing = False
        if sha is None and note == "нет файла":
            return                          # открытие провалится, ничего не прочитано
        _LEDGER.note_data(ap, sha, size, note)
    except Exception:
        return


# ─────────────────────────── УСТАНОВКА ──────────────────────────────────────────────────────────

def backfill(led=None):
    """Записать то, что уже в процессе, провенансом `добор`. Это НЕ байты процесса, а байты диска
    в момент установки, и сверка обязана звать их `не проверено`: если между загрузкой и установкой
    файл переписали, хеш будет от ЧУЖОЙ редакции — ровно тот обман, от которого прибор лечит.
    Возвращает число добранных имён."""
    led = led or _LEDGER
    n = 0
    for name, mod in list(sys.modules.items()):
        try:
            f = getattr(mod, "__file__", None)
            if not isinstance(f, str) or not f.endswith(".py"):
                continue
            if _norm(f) is None:
                continue
            if name in led.modules:
                continue
            sha, size, note = sha_file(f)
            led.note_module(name, f, sha, VIA_BACKFILL, size, note)
            n += 1
        except Exception:
            continue
    return n


def install(do_backfill=True):
    """Поставить оба регистратора. Идемпотентна: повторный вызов только взводит флаг.

    Ставить надо КАК МОЖНО РАНЬШЕ во входном скрипте — всё, что загрузилось до, получит слабый
    провенанс `добор`. Возвращает журнал процесса."""
    global _ARMED, _INSTALLED, _FINDER
    if not _INSTALLED:
        _FINDER = _Finder()
        try:
            sys.meta_path.insert(0, _FINDER)
        except Exception:
            pass
        try:
            sys.addaudithook(_audit)
        except Exception:
            pass
        _INSTALLED = True
    _ARMED = True
    _LEDGER.installed_at = _LEDGER.clock()
    if do_backfill:
        backfill(_LEDGER)
    return _LEDGER


def disarm():
    """Обезвредить. Снять аудит-ловушку Python не позволяет никому — поэтому вся её работа стои́т
    за флагом, и здесь он гасится. Для тестов и для отката без рестарта."""
    global _ARMED
    _ARMED = False


def reset(clock=None):
    """Чистый журнал (для тестов). Регистраторы остаются на месте."""
    global _LEDGER
    _LEDGER = Ledger(clock=clock)
    _HASH_CACHE.clear()
    return _LEDGER


# ─────────────────────────── ОТПЕЧАТОК ──────────────────────────────────────────────────────────

def digest_of(modules, data):
    """Отпечаток набора одной строкой. Считается по ИМЕНАМ И ХЕШАМ, отсортированным, — два
    процесса с одинаковым набором байт дают одинаковый digest независимо от порядка загрузки."""
    h = hashlib.sha256()
    for r in sorted(modules, key=lambda x: x["имя"]):
        h.update(("M %s %s %s\n" % (r["имя"], r["путь"], r.get("sha"))).encode("utf-8"))
    for r in sorted(data, key=lambda x: x["путь"]):
        h.update(("D %s %s\n" % (r["путь"], r.get("sha"))).encode("utf-8"))
    return h.hexdigest()


def fingerprint(answer=None, led=None, mark=False):
    """СНИМОК В МОМЕНТ ОТВЕТА (часть 1 контракта).

    `answer` — идентификатор ответа: без него строку нельзя связать с конкретным сообщением, и
    отпечаток превращается в справку о процессе вообще. `mark=True` сдвигает отметку «отсюда
    считаем новое», чтобы следующий ответ показал СВОИ двери, а не все подряд."""
    led = led or _LEDGER
    mods = sorted(led.modules.values(), key=lambda x: x["имя"])
    dats = sorted(led.data.values(), key=lambda x: x["путь"])
    new_mods, new_data = led.since(led.emitted_seq)
    weak = [r["имя"] for r in mods if r.get("как") in WEAK_VIA]
    now = led.clock()
    fp = {
        "v": VERSION,
        "ответ": answer,
        "снят": _iso(now),
        "ts": now,
        "pid": os.getpid(),
        "поставлен": _iso(led.installed_at) if led.installed_at else None,
        "модули": mods,
        "данные": dats,
        "двери": {"код": new_mods, "данные": new_data},
        "слабых": weak,
        "счёт": {"модулей": len(mods), "данных": len(dats), "слабых": len(weak),
                 "новых_модулей": len(new_mods), "новых_данных": len(new_data),
                 "exec": led.exec_events},
        "exec_где": list(led.exec_where),
        "digest": digest_of(mods, dats),
    }
    if mark:
        led.emitted_seq = led.seq
    return fp


def line(fp):
    """Строка наружу: отличима грепом по `PROC_BYTES`, несёт идентификатор ответа и digest, по
    которому в `proc_bytes.jsonl` находится полный поимённый список."""
    c = fp["счёт"]
    return ("%s v=%d ответ=%s pid=%s модулей=%d слабых=%d данных=%d новых=%d/%d digest=%s "
            "снят=%s ref=%s" % (
                LINE_TAG, fp["v"], fp.get("ответ"), fp.get("pid"), c["модулей"], c["слабых"],
                c["данных"], c["новых_модулей"], c["новых_данных"], fp["digest"][:12],
                fp["снят"], LEDGER_FILE))


def ledger_path():
    """Куда ложится полный отпечаток. Через `log_setup.state_path` — под тестом это temp, и боевой
    файл не трогается ни одним прогоном (класс 05.08: «гейт съел спул»)."""
    try:
        import log_setup
        return log_setup.state_path(os.path.join(HERE, LEDGER_FILE))
    except Exception:
        return os.path.join(HERE, LEDGER_FILE)


def emit(answer=None, logger=None, led=None, path=None):
    """Записать отпечаток: полный — строкой JSON в `proc_bytes.jsonl`, короткий — в лог бота.

    Никогда не бросает: прибор наблюдения не имеет права стоить ответа клиенту. Возвращает
    (отпечаток, строка)."""
    fp = fingerprint(answer=answer, led=led, mark=True)
    text = line(fp)
    p = path or ledger_path()
    try:
        try:
            import log_setup
            log_setup.rotate_if_needed(p, LEDGER_MAX_BYTES, LEDGER_KEEP)
        except Exception:
            pass
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(fp, ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:
        if logger is not None:
            logger.info(text)
    except Exception:
        pass
    return fp, text


def read_last(path=None, answer=None):
    """Последний отпечаток из журнала (или последний с данным идентификатором ответа)."""
    p = path or ledger_path()
    found = None
    try:
        with open(p, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if answer is not None and rec.get("ответ") != answer:
                    continue
                found = rec
    except OSError:
        return None
    return found


# ─────────────────────────── СВЕРКА (часть 3) ───────────────────────────────────────────────────

def _git(args, run=None):
    """Спросить git. `run` — инъекция для тестов: голден не имеет права зависеть от рабочего дерева."""
    if run is not None:
        return run(args)
    try:
        p = subprocess.run(["git"] + list(args), cwd=HERE, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, p.stdout
    except Exception:
        return 1, b""


def expect_from_git(commit, paths, run=None):
    """Ожидаемое содержимое каждого пути В КОММИТЕ: {путь: sha256} либо {путь: None}, если такого
    файла коммит не знает ВОВСЕ.

    `None` — это не «пропустить», а ОТДЕЛЬНЫЙ факт «вне git», и именно он валит ОТ-3: `playbook.md`
    и `lesson_store.tsv` живут вне git, ответ меняют, а сверка «код совпал с коммитом» их не
    замечает по устройству."""
    out = {}
    for p in paths:
        code, blob = _git(["show", "%s:%s" % (commit, p)], run=run)
        out[p] = sha_bytes(blob) if code == 0 else None
    return out


def verify(fp, expect, answer_at=None, strict=True):
    """Сверить отпечаток с ожидаемым содержимым ПОИМЁННО.

    `expect` — {путь: sha256 | None}. Ключи задают и то, ЧТО должно было быть загружено: путь,
    названный в `expect` и отсутствующий в отпечатке, даёт `нет в отпечатке` — исход ОТ-1.
    `answer_at` — время ответа: отпечаток, снятый РАНЬШЕ него, отвергается целиком (исход ОТ-2).
    `strict=False` перестаёт валить приговор на слабом провенансе (остаётся видно в разрезе).

    Возвращает {'итог', 'по_именам', 'разрез', 'слабых', 'digest'}."""
    if answer_at is not None and fp.get("ts") is not None and fp["ts"] < answer_at:
        return {"итог": STALE, "по_именам": {}, "разрез": {},
                "слабых": len(fp.get("слабых") or []), "digest": fp.get("digest"),
                "почему": "снят %s, ответ %s" % (fp.get("снят"), _iso(answer_at))}

    have = {}
    weak_paths = set()
    for r in fp.get("модули") or []:
        have[r["путь"]] = r.get("sha")
        if r.get("как") in WEAK_VIA:
            weak_paths.add(r["путь"])
    for r in fp.get("данные") or []:
        have[r["путь"]] = r.get("sha")

    by_name = {}
    for p in sorted(set(list(expect.keys()) + list(have.keys()))):
        want = expect.get(p, "___НЕ_ОЖИДАЛСЯ___")
        got = have.get(p)
        if p not in have:
            by_name[p] = V_ABSENT
        elif want is None:
            by_name[p] = V_UNTRACKED
        elif want == "___НЕ_ОЖИДАЛСЯ___":
            by_name[p] = V_UNEXPECTED      # загружено, а ожидания нет — судить нечем, но и молчать нельзя
        elif got is None:
            by_name[p] = V_WEAK
        elif got != want:
            by_name[p] = V_DIFF
        elif p in weak_paths:
            by_name[p] = V_WEAK if strict else V_MATCH
        else:
            by_name[p] = V_MATCH

    cut = {}
    for v in by_name.values():
        cut[v] = cut.get(v, 0) + 1

    if cut.get(V_DIFF) or cut.get(V_UNTRACKED) or cut.get(V_UNEXPECTED):
        total = BROKEN
    elif cut.get(V_ABSENT) or cut.get(V_WEAK):
        total = PARTIAL
    else:
        total = OK
    return {"итог": total, "по_именам": by_name, "разрез": cut,
            "слабых": len(weak_paths), "digest": fp.get("digest")}


def report(res, limit=40):
    """Человеческий разрез приговора — поимённо, а не одним словом на процесс."""
    lines = ["итог: %s" % res["итог"]]
    for k in (V_DIFF, V_UNTRACKED, V_UNEXPECTED, V_ABSENT, V_WEAK, V_MATCH):
        n = res["разрез"].get(k)
        if n:
            lines.append("  %s: %d" % (k, n))
    shown = 0
    for p, v in sorted(res["по_именам"].items()):
        if v == V_MATCH:
            continue
        lines.append("    %-52s %s" % (p, v))
        shown += 1
        if shown >= limit:
            lines.append("    … (ещё %d)" % (len([1 for x in res["по_именам"].values()
                                                  if x != V_MATCH]) - shown))
            break
    return "\n".join(lines)


# ─────────────────────────── РУКИ (только чтение) ───────────────────────────────────────────────

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--last" in argv or not argv:
        fp = read_last()
        if fp is None:
            print("отпечатков нет: %s" % ledger_path())
            return 2
        print(line(fp))
        print("  модулей %d (слабых %d), данных %d, exec-событий %d"
              % (fp["счёт"]["модулей"], fp["счёт"]["слабых"], fp["счёт"]["данных"],
                 fp["счёт"].get("exec", 0)))
        for name in (fp.get("двери") or {}).get("код", [])[:20]:
            print("  дверь кода открылась к этому ответу: %s" % name)
        return 0
    if "--verify" in argv:
        i = argv.index("--verify")
        commit = argv[i + 1] if len(argv) > i + 1 else "HEAD"
        fp = read_last()
        if fp is None:
            print("отпечатков нет: %s" % ledger_path())
            return 2
        paths = [r["путь"] for r in fp.get("модули") or []] + \
                [r["путь"] for r in fp.get("данные") or []]
        res = verify(fp, expect_from_git(commit, paths))
        print("отпечаток %s против %s" % (fp["digest"][:12], commit))
        print(report(res))
        return 0 if res["итог"] == OK else 1
    print(__doc__.strip().splitlines()[0])
    return 2


if __name__ == "__main__":
    sys.exit(main())
