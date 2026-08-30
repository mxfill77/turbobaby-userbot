# -*- coding: utf-8 -*-
"""
proc_identity.py — ОДНО ПРАВИЛО ЛИЧНОСТИ ПРОЦЕССА на всю полосу ПК: **номер + имя запуска +
момент старта**. Заведено 30.08.2026 по классу «номер процесса не является именем».

ПОВОД — ДВА ЖИВЫХ ПРОСТОЯ, ОБА С ОДНИМ УСТРОЙСТВОМ И ОБА ДОРОГИЕ.
  • 23.08.2026, агент полосы. BSOD 0x7F в 05:14; `release_agent_lock()` не позвался, в
    `pc_agent.lock` остался номер 8960. После загрузки 8960 достался `wlanext.exe`. Агент
    выходил с «pc_agent уже запущен (PID 8960)» НАВСЕГДА, полоса без агента 05:14 → 06:16.
    Класс тогда же назван словами и записан в память сессий; правки в коде не случилось.
  • 26.08.2026, модербот. Штатный ребут 11:46:47; в `moderation_bot.lock` остался номер 18200
    от 23.08 18:29. После загрузки 18200 достался `PinWin.exe` (создан 11:48:14 — на 70 секунд
    ПОЗЖЕ загрузки, то есть заведомо не автор лока). Каждый подъём модербота стартовал и
    выходил за полсекунды; контур-вотчдог считал это смертями и вставал в стоп трижды
    (26.08 12:45, 26.08 14:34, 29.08 12:30). Модербот пролежал 26.08 11:47 → 30.08 02:38 —
    **3 суток 15 часов**, и поднялся не починкой, а тем, что на следующей загрузке номер 18200
    никто не занял. Исход решала монетка ОС.

ПОЧЕМУ НОМЕР — НЕ ИМЯ. Номер процесса уникален только СРЕДИ ЖИВЫХ. Умер владелец — номер
возвращается в оборот, и Windows раздаёт его тем охотнее, чем ближе перезагрузка: после старта
системы номера идут почти подряд. Вопрос «существует ли процесс с номером N» и вопрос «жив ли
ТОТ, кто взял лок» — разные вопросы, и первый выдавал себя за второй во всех четырёх синглтон-
гардах полосы. Правильный ответ склеен из ДВУХ положительных фактов: процесс с этим номером
есть И это тот же запуск (имя образа сходится, момент рождения сходится).

ЭТО НЕ НОВАЯ МЫСЛЬ ДЛЯ ПОЛОСЫ — ОНА УЖЕ ТРИЖДЫ ЗАПИСАНА В ЭТОМ ЖЕ ДЕРЕВЕ, но до гардов не
доехала. `pc_orchestrator._PROC_TOKEN` («ЛИЧНОСТЬ ЭТОГО ЗАПУСКА демона: PID плюс момент
старта»), `pc_orchestrator._is_descendant` («родитель моложе ребёнка → PID переиспользован»),
`expectations_pc.kid_writer` («процесс с номером из лока существует И запущен тогда же, когда
написан лок») и `session_watch.identity_ok` — четыре готовых образца. Модуль не изобретает
правило, а СОБИРАЕТ его в одно место и раздаёт всем сразу.

═══ ЧЕТЫРЕ ВЕРДИКТА, И ПОЧЕМУ ИХ НЕ ДВА ══════════════════════════════════════════════════════

    OURS_ALIVE       владелец лока ЖИВ и это он — второй экземпляр не поднимаем;
    STALE            владелец ДОКАЗАННО не работает (номера нет / номер занят чужим образом /
                     занят другим запуском) — лок стухший, забираем;
    HELD_UNVERIFIED  процесс с этим номером ЕСТЬ, но личность не подтвердить (доступ закрыт,
                     возраст не добыт). Это НЕ «стухший»: занятый номер — повод не лезть;
    UNKNOWN          проба не ответила вовсе: неизвестно даже, существует ли номер.

Развилка на четыре, а не на два, сделана ровно потому, что «не смог проверить» уже однажды
выдало себя за «мёртв» на этой полосе (класс #171, ложная смерть вотчдога, f89da43). Здесь та
же асимметрия цены: лишние 15 минут простоя стоят времени, а два поллера на одном токене —
это `Conflict` в Telegram, обоюдная борьба за апдейты и молчаливая порча работы.

**КАК ЧИТАТЬ МОЛЧАНИЕ — РЕШАЕТ ВЫЗЫВАЮЩИЙ, А НЕ МОДУЛЬ.** `HELD_UNVERIFIED` держит лок у всех
без исключения (это не ослабление ни для кого: сегодня `tasklist` в этом случае тоже отвечает
«жив»). А `UNKNOWN` — параметр `on_unknown`: три бота выбирают `hold` (усиление: сегодня их
`_pid_alive` глотает свой отказ в False и УВОДИТ В КРАЖУ ЛОКА, то есть в двойной запуск), а
демон — `take`, потому что у его синглтона контракт обратный и записан словами в его же
докстринге: «не поднять демона хуже, чем поднять второго». Чужое осознанное решение мы не
переигрываем — мы только чиним ту половину, где номер выдавал себя за личность.

═══ ФОРМАТ ЛОКА: ПЕРВАЯ СТРОКА — НОМЕР, ВТОРАЯ — ЛИЧНОСТЬ ════════════════════════════════════

    9360
    {"v":1,"pid":9360,"started":1787000000.123,"image":"…\\python.exe","script":"moderation_bot.py",…}

Первая строка оставлена голым номером СОЗНАТЕЛЬНО: её читают снаружи (наблюдатель ожиданий), и
формат обязан пережить любого читателя, который смотрит только на неё. Вторая строка —
необязательная: лок без неё (старого формата, либо написанный процессом, который упал между
созданием файла и записью) судится ПО ДРУГОЙ ветке, см. ниже. Ни одна ветка не отвечает
«владелец жив», не имея на это ДВУХ фактов.

═══ ЛОК СТАРОГО ФОРМАТА: ЧЕМ СУДИМ, ЕСЛИ ЛИЧНОСТИ В НЁМ НЕ ЗАПИСАНО ══════════════════════════

Сверяем момент рождения процесса с ВРЕМЕНЕМ ПРАВКИ САМОГО ЛОКА. Владелец сначала родился, а
потом написал лок — значит `started ≤ mtime` всегда. Процесс, родившийся ПОЗЖЕ, чем написан
лок, автором быть не может НИ ПРИ КАКИХ обстоятельствах: пока лок писался, номер принадлежал
кому-то другому, а номера среди живых уникальны. Это ровно тот замок, которого не хватило
26.08: `PinWin.exe` моложе лока на трое суток.

ЗАПАС `TOL_BORN` ВЗЯТ ЩЕДРЫМ (60 с) НАМЕРЕННО, и это не небрежность. Ошибка в эту сторону
дёшева (пропустим редчайшее переиспользование номера ВНУТРИ минуты — и следующий же запуск
владельца положит лок нового формата, где сверка точная), а ошибка в обратную сторону —
объявить стухшим лок ЖИВОГО владельца — стоит двойного запуска. Оба живых случая класса имели
разрыв в часы и сутки, не в секунды.

═══ ЧЕГО ЗДЕСЬ СОЗНАТЕЛЬНО НЕТ ═══════════════════════════════════════════════════════════════

Убийств (`os.kill` на Windows зовёт TerminateProcess — наблюдатель убил бы наблюдаемого одной
строкой), сети, чтения конфигов и секретов, часов, кэша и любого суждения о том, ЧТО процессу
делать. Модуль отвечает на один вопрос — «тот ли это процесс» — и отдаёт ответ вызывающему.

ОТКАТ: вернуть в четырёх гардах прежние `_pid_alive` (они удалены одним коммитом с этим файлом,
`git revert` возвращает всё разом). Локи нового формата старым кодом читаются первой строкой,
то есть откат не требует трогать ни одного лок-файла.
"""

import ctypes
import json
import os
import subprocess
import sys
import time

VERSION = 1

# ─── вердикты (значения — для логов владельцу, сравнение только по этим именам) ───────────────
OURS_ALIVE = "жив-наш"
STALE = "стухший"
HELD_UNVERIFIED = "занят-неподтверждён"
UNKNOWN = "неизвестно"

HOLD_VERDICTS = (OURS_ALIVE, HELD_UNVERIFIED)   # при этих лок НЕ забираем никогда

# ─── запасы сверки ────────────────────────────────────────────────────────────────────────────
TOL_SAME = 2.0        # с: два чтения ОДНОГО момента старта расходиться не должны вовсе, запас формальный
TOL_BORN = 60.0       # с: «родился раньше, чем написал лок» — щедро, довод в шапке
KILL_TOL_BORN = 5.0   # с: тот же запас перед СНЯТИЕМ процесса — туже, действие разрушительное

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_PROC_QUERY_LIMITED = 0x1000        # PROCESS_QUERY_LIMITED_INFORMATION — «спросить», не «тронуть»
_ERR_INVALID_PARAMETER = 87         # такого номера в системе нет — ЕДИНСТВЕННОЕ доказанное «нет»
_ERR_ACCESS_DENIED = 5              # процесс ЕСТЬ, но чужой: «нет» это не значит
_STILL_ACTIVE = 259                 # GetExitCodeProcess: «ещё работает». Ровно этим числом
                                    # завершившийся процесс с удержанным дескриптором отличается
                                    # от работающего — см. разбор в probe_handle
_FILETIME_EPOCH = 11644473600.0     # 1601-01-01 → 1970-01-01, секунды
_PY_IMAGES = ("python.exe", "pythonw.exe", "python", "python3")

def reserve_dir(lock_path):
    """Куда уезжает снятый стухший лок. Каталог выводится ИЗ ПУТИ САМОГО ЛОКА, а не берётся
    глобальной константой: боевые локи лежат в корне репо → улики уедут в `tmp/stale_locks`
    (под .gitignore, дерево не пачкается и авто-фетч не встаёт), а лок из временного каталога
    теста — во временный же каталог, и ни один прогон не сорит в боевое дерево."""
    return os.path.join(os.path.dirname(os.path.abspath(lock_path)), "tmp", "stale_locks")


class Probe(object):
    """Ответ пробы процесса. `exists`: True | False | None (не смогли спросить).
    `started`: момент рождения, с эпохи | None. `image`: полный путь образа | None.
    `how`: чем добыто — едет в лог, чтобы «неизвестно» никогда не было безымянным."""

    __slots__ = ("exists", "started", "image", "how")

    def __init__(self, exists=None, started=None, image=None, how=""):
        self.exists, self.started, self.image, self.how = exists, started, image, how

    def __repr__(self):                                                       # pragma: no cover
        return "Probe(exists=%r, started=%r, image=%r, how=%r)" % (
            self.exists, self.started, self.image, self.how)

    def __eq__(self, other):
        return (isinstance(other, Probe) and self.exists == other.exists
                and self.started == other.started and self.image == other.image)


# ════════════════════════ ПРОБА ПРОЦЕССА: ПРАВО СПРОСИТЬ, А НЕ ТРОНУТЬ ════════════════════════

_k32_cache = []


def _k32():
    """ПРИВАТНЫЙ хэндл kernel32 с расставленными типами. Приватный намеренно: `ctypes.windll`
    кэшируется на процесс, и правка `restype` на нём молча меняла бы поведение соседних модулей
    (`expectations_pc_run` зовёт те же функции). `use_last_error` — чтобы код ошибки читался
    своим каналом, а не через `GetLastError`, который успевает затереться чужим вызовом."""
    if _k32_cache:
        return _k32_cache[0]
    if os.name != "nt":
        _k32_cache.append(None)
        return None
    try:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                 ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    except Exception:                                                     # pragma: no cover
        k = None
    _k32_cache.append(k)
    return k


def probe_handle(pid):
    """Проба ДЕСКРИПТОРОМ: существование + момент рождения + образ, одним системным вызовом и
    без единого подпроцесса (значит, без консольной вспышки и без таймаута, который уже однажды
    выдал себя за смерть). Доступ просим самый узкий из существующих."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return Probe(None, None, None, "номер не разобран")
    if pid <= 0:
        return Probe(None, None, None, "номер не положителен")
    k = _k32()
    if k is None:
        return Probe(None, None, None, "kernel32 недоступен (не Windows?)")
    try:
        h = k.OpenProcess(_PROC_QUERY_LIMITED, False, pid)
    except Exception as e:                                                # pragma: no cover
        return Probe(None, None, None, "OpenProcess не состоялся: %s" % type(e).__name__)
    if not h:
        err = ctypes.get_last_error()
        if err == _ERR_INVALID_PARAMETER:
            return Probe(False, None, None, "OpenProcess: номера %d в системе нет" % pid)
        if err == _ERR_ACCESS_DENIED:
            return Probe(True, None, None, "OpenProcess: доступ закрыт — процесс ЕСТЬ, личность не добыть")
        return Probe(None, None, None, "OpenProcess: ошибка %d" % err)
    started, image, exited = None, None, False
    try:
        # ОТКРЫЛСЯ — ЕЩЁ НЕ ЗНАЧИТ РАБОТАЕТ. Ловушка Windows, пойманная отрицательным тестом №2:
        # пока хоть один дескриптор процесса удерживается (а `subprocess.Popen` держит его до
        # сборки объекта), `OpenProcess` на ЗАВЕРШИВШИЙСЯ процесс успешно открывается и честно
        # отдаёт его прежний момент старта. Прибор без этой проверки объявлял бы мёртвого ребёнка
        # живым — и гард запретил бы поднять его заново, то есть воспроизвёл бы ровно ту беду,
        # ради которой заход и затеян. `tasklist` завершённых не показывает, поэтому старый
        # прибор здесь был прав, а новый — обязан догнать. STILL_ACTIVE отличаем ЯВНО.
        code = ctypes.c_ulong(_STILL_ACTIVE)
        if k.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != _STILL_ACTIVE:
            exited = True
    except Exception:                                                     # pragma: no cover
        exited = False
    try:
        cre, ext, ker, usr = (ctypes.c_ulonglong() for _ in range(4))
        if k.GetProcessTimes(h, ctypes.byref(cre), ctypes.byref(ext),
                             ctypes.byref(ker), ctypes.byref(usr)) and cre.value:
            started = cre.value / 1e7 - _FILETIME_EPOCH
    except Exception:                                                     # pragma: no cover
        started = None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_ulong(len(buf))
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            image = buf.value or None
    except Exception:                                                     # pragma: no cover
        image = None
    finally:
        try:
            k.CloseHandle(h)
        except Exception:                                                 # pragma: no cover
            pass
    if exited:
        return Probe(False, None, None,
                     "OpenProcess: номер %d ещё открывается, но процесс УЖЕ ЗАВЕРШИЛСЯ "
                     "(дескриптор кто-то держит)" % pid)
    return Probe(True, started, image, "OpenProcess+GetProcessTimes+QueryFullProcessImageName")


def probe_tasklist(pid, runner=None):
    """ЗАПАСНАЯ проба списком задач. Нужна ровно там, где дескриптор не дают: чужой владелец
    процесса (доступ закрыт) — а имя образа `tasklist` показывает и тогда. Момента рождения он
    не знает, поэтому один он личность не подтверждает, только ОПРОВЕРГАЕТ (чужой образ).

    ЗАМЕР полосы (05.08.2026): на несуществующий номер `tasklist` отвечает rc=0 и строкой
    «задачи не найдены» — значит rc≠0 это НЕ «мёртв», а сбой самой пробы. Разделяем по
    содержимому, а не по коду возврата."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return Probe(None, None, None, "номер не разобран")
    run = runner or (lambda: subprocess.run(
        ["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=10, creationflags=_NO_WINDOW))
    try:
        r = run()
    except Exception as e:
        return Probe(None, None, None, "tasklist не ответил (%s) — это НЕ смерть, а незнание" % type(e).__name__)
    if getattr(r, "returncode", 0) != 0:
        return Probe(None, None, None, "tasklist дал rc=%s — это НЕ смерть, а сбой пробы" % r.returncode)
    out = getattr(r, "stdout", "") or ""
    for line in out.splitlines():
        fields = _csv_fields(line)
        if len(fields) >= 2 and fields[1].strip() == str(pid):
            return Probe(True, None, fields[0].strip() or None, "tasklist (имя есть, возраст — нет)")
    return Probe(False, None, None, "tasklist: номера %d в списке нет" % pid)


def _csv_fields(line):
    """Разбор строки CSV-вывода `tasklist` без модуля csv: поля в кавычках, разделитель запятая.
    Своими руками — потому что кодировка вывода на русской Windows не utf-8, и csv-модуль на
    подменённых символах ведёт себя хуже, чем простой проход по кавычкам."""
    out, buf, inside = [], [], False
    for ch in line:
        if ch == '"':
            inside = not inside
        elif ch == "," and not inside:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def process_probe(pid, handle=None, tasklist=None):
    """ОБЩАЯ проба полосы: дескриптор, а где он не дан — список задач. Три исхода наружу.

    Каскад, а не одна проба, потому что у двух источников РАЗНЫЕ слепые пятна: дескриптор не
    открывается на чужого владельца, а `tasklist` не знает возраста. Вместе они закрывают
    единственный случай, который иначе уходил бы в «неизвестно» на ровном месте — чужой процесс
    под другим пользователем, занявший наш номер (именно так выглядел `wlanext.exe` 23.08)."""
    try:
        n = int(pid)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        # НОМЕРА НЕТ ВОВСЕ (пустой лок, битая запись). Спрашивать о нём систему бессмысленно, а
        # запасная проба здесь стоила бы подпроцесса на каждый пустой лок — в тестах это сотни
        # запусков `tasklist` на ровном месте. Отвечаем «не спрашивали», решает `judge`.
        return Probe(None, None, None, "номер не положителен — систему не спрашивали")
    h = (handle or probe_handle)(pid)
    if h.exists is False:
        return h                                       # ДОКАЗАННОЕ отсутствие (ERROR_INVALID_PARAMETER)
    if h.exists is True and (h.started is not None or h.image is not None):
        return h                                       # личность добыта — второй источник не нужен
    # сюда доходим только в двух случаях: дескриптор не дали (чужой владелец) либо проба молчит.
    # ТОЛЬКО ЗДЕСЬ платим за подпроцесс: в горячем пути (свой процесс, чужой процесс, номера нет)
    # его нет вовсе — иначе каждый тик сторожа и каждый прогон тестов стоил бы запуска tasklist.
    t = (tasklist or probe_tasklist)(pid)
    if t.exists is False:
        return Probe(False, None, None, t.how)
    if h.exists is True or t.exists is True:
        merged = Probe(True, h.started, h.image or t.image,
                       "%s; %s" % (h.how, t.how))
        return merged
    return Probe(None, None, None, "%s; %s" % (h.how, t.how))


def whoami(pid=None, probe=None):
    """Личность ТЕКУЩЕГО процесса → dict. Момент старта берём тем же прибором, каким потом будем
    сверять: два разных источника времени дали бы расхождение на ровном месте."""
    pid = os.getpid() if pid is None else int(pid)
    p = (probe or process_probe)(pid)
    return {"pid": pid, "started": p.started,
            "image": p.image or (sys.executable or None)}


# ═══════════════════════════════ ЛОК: ЗАПИСЬ, ЧТЕНИЕ, ПРИГОВОР ════════════════════════════════

def lock_text(script=None, pid=None, probe=None, clock=None):
    """Текст лок-файла: первая строка — голый номер (совместимость наружу), вторая — личность."""
    me = whoami(pid=pid, probe=probe)
    blob = {"v": VERSION, "pid": me["pid"], "started": me["started"], "image": me["image"],
            "script": script or os.path.basename(sys.argv[0] or ""),
            "written": (clock or time.time)()}
    return "%d\n%s\n" % (me["pid"], json.dumps(blob, ensure_ascii=False))


def read_lock(path):
    """Лок → запись о владельце | None (файла нет / не прочитан).

    `legacy=True` означает «личности в файле нет», а НЕ «файл плохой»: такие локи писал прежний
    код и пишет любой процесс, упавший между созданием файла и записью тела. Судить их можно —
    другой веткой, по времени правки файла."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        mtime = os.stat(path).st_mtime
    except OSError:
        return None
    rec = {"pid": 0, "started": None, "image": None, "script": None, "written": None,
           "mtime": mtime, "legacy": True, "raw": raw}
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return rec
    try:
        rec["pid"] = int(lines[0])
    except ValueError:
        rec["pid"] = 0
    if len(lines) > 1 and rec["pid"]:
        try:
            blob = json.loads("\n".join(lines[1:]))
        except Exception:
            blob = None
        # тело засчитываем ТОЛЬКО если оно про тот же номер: иначе это чужой хвост, и верить ему
        # опаснее, чем не иметь его вовсе (ложное «личность известна» ведёт к ложному приговору)
        if isinstance(blob, dict) and _int_or_zero(blob.get("pid")) == rec["pid"]:
            rec["started"] = _float_or_none(blob.get("started"))
            rec["image"] = blob.get("image") or None
            rec["script"] = blob.get("script") or None
            rec["written"] = _float_or_none(blob.get("written"))
            rec["legacy"] = rec["started"] is None
    return rec


def judge(rec, probe, tol_same=None, tol_born=None):
    """ЧИСТЫЙ приговор о владельце лока → (вердикт, причина словами). Ни одного побочного
    действия: снимает лок и стартует ВЫЗЫВАЮЩИЙ, а голдены лежат на этой функции.

    Порядок проверок — от доказательного к косвенному, и каждая следующая нужна только тогда,
    когда предыдущая не решила: есть ли номер → тот ли образ → тот ли запуск."""
    tol_same = TOL_SAME if tol_same is None else tol_same
    tol_born = TOL_BORN if tol_born is None else tol_born
    if rec is None:
        return STALE, "лок-файла нет"
    if not rec.get("pid"):
        return STALE, "в локе нет номера (пусто или не разобрано)"
    pid = rec["pid"]
    if probe is None:
        return UNKNOWN, "пробы процесса не делали"
    if probe.exists is False:
        return STALE, "процесса с номером %d в системе НЕТ (%s)" % (pid, probe.how)
    if probe.exists is None:
        return UNKNOWN, "проба не ответила: существует ли номер %d — неизвестно (%s)" % (pid, probe.how)

    # ── процесс с таким номером ЕСТЬ. Первый признак личности — имя запуска ────────────────────
    want = rec.get("image")
    if probe.image:
        if want and not _same_image(probe.image, want):
            return STALE, ("номер %d занят ЧУЖИМ образом: %s вместо %s — это не наш запуск"
                           % (pid, _base(probe.image), _base(want)))
        if not want and not _is_python_image(probe.image):
            return STALE, ("номер %d занят ЧУЖИМ образом %s (лок старого формата: владельцем "
                           "мог быть только python) — это не наш запуск"
                           % (pid, _base(probe.image)))

    # ── второй признак — момент старта ────────────────────────────────────────────────────────
    if probe.started is None:
        return HELD_UNVERIFIED, ("процесс %d есть, но момент старта не добыт (%s) — личность не "
                                 "подтверждена, лок не трогаю" % (pid, probe.how))
    if rec.get("started") is not None:
        delta = abs(float(probe.started) - float(rec["started"]))
        if delta <= tol_same:
            return OURS_ALIVE, "процесс %d жив и это тот же запуск (расхождение старта %.3fс)" % (pid, delta)
        return STALE, ("номер %d занят ДРУГИМ запуском: старт %s, а лок писал запуск от %s "
                       "(разница %.0fс)" % (pid, _stamp(probe.started), _stamp(rec["started"]), delta))

    # ── лок старого формата: личности в нём нет, сверяем с временем правки самого файла ────────
    mtime = _float_or_none(rec.get("mtime"))
    if mtime is None:
        return HELD_UNVERIFIED, ("процесс %d есть, но у лока нет ни личности, ни времени правки — "
                                 "сверить нечем, лок не трогаю" % pid)
    born_after = float(probe.started) - mtime
    if born_after > tol_born:
        return STALE, ("процесс %d рождён %s — ПОЗЖЕ, чем написан лок (%s), на %.0fс: автором "
                       "лока он быть не может" % (pid, _stamp(probe.started), _stamp(mtime), born_after))
    return OURS_ALIVE, ("процесс %d жив и рождён до записи лока (%s ≤ %s) — лок старого формата, "
                        "точнее не сверить" % (pid, _stamp(probe.started), _stamp(mtime)))


# ═══════════════════════════════ ЗАБОР И СНЯТИЕ ЛОКА ══════════════════════════════════════════

def acquire(path, script=None, probe=None, log=None, tries=3, sleep=None,
            on_unknown="hold", clock=None, retire_dir=None):
    """Взять лок → (ok, вердикт, причина). Единственная дверь забора для всей полосы.

    `on_unknown`: "hold" — проба промолчала, лок НЕ трогаем и не стартуем (усиление, выбор трёх
    ботов); "take" — считаем держателя отсутствующим (прежний осознанный контракт синглтона
    демона: «не поднять демона хуже, чем поднять второго»).

    СТУХШИЙ ЛОК СНИМАЕТ САМА ПРОГРАММА — руками ничего делать не надо. Но снимает НЕ УДАЛЕНИЕМ:
    файл уезжает в резерв `tmp/stale_locks/`, потому что каждый такой файл — улика живого случая
    переиспользования номера, а `tmp/` под .gitignore и дерево от него не пачкается."""
    _sleep = sleep or time.sleep
    _log = log or (lambda *a, **k: None)
    _probe = probe or process_probe
    last = (UNKNOWN, "лок не разобран")
    for _ in range(max(1, int(tries))):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, lock_text(script=script, probe=_probe, clock=clock).encode("utf-8"))
            finally:
                os.close(fd)
            return True, STALE, "лок взят (был свободен)"
        except FileExistsError:
            rec = read_lock(path)
            if rec is not None and not rec.get("pid"):
                # гонка: файл создан, тело ещё не дописано — даём владельцу договорить
                _sleep(0.3)
                rec = read_lock(path)
            if rec is None:
                continue                                  # исчез между except и чтением — пробуем снова
            if is_me(rec, _probe):
                return True, OURS_ALIVE, "лок уже наш (перезабор тем же запуском)"
            verdict, why = judge(rec, _probe(rec["pid"]))
            last = (verdict, why)
            if verdict in HOLD_VERDICTS:
                return False, verdict, why
            if verdict == UNKNOWN and on_unknown != "take":
                return False, verdict, why
            moved = retire(path, rec, retire_dir=retire_dir, clock=clock)
            _log("СТУХШИЙ, забираю — %s%s" % (why, (" (улика → %s)" % moved) if moved else ""))
            continue
        except OSError as e:
            last = (UNKNOWN, "лок не создан: %s" % e)
            _sleep(0.3)
    return False, last[0], last[1]


def kill_ok(pid, seen_at, probe=None, tol_born=None):
    """ТОТ ЛИ ЭТО ПРОЦЕСС, КОГО МЫ ВИДЕЛИ, — единственный вопрос перед снятием → (можно, почему).

    Снятие процесса по голому номеру — тот же класс, что и лок по голому номеру, только цена
    выше: лок держит НАШ старт, а `taskkill` бьёт ЧУЖОЙ живой процесс. Разрыв здесь не
    теоретический: номер приходит из CIM-поиска по имени скрипта, а бьют его секундами позже,
    иногда в цикле добивания до 20 с. Умри владелец в этом окне — номер уходит в оборот, и удар
    достаётся тому, кто его подобрал.

    Судим ТЕМ ЖЕ приговором, что и локи: `seen_at` играет роль «времени правки лока» — процесс,
    рождённый ПОЗЖЕ момента, когда мы его видели, тем же процессом быть не может. Запас здесь
    туже общего (`KILL_TOL_BORN`): действие разрушительное, и цена ошибки несимметрична.

    Любой исход кроме доказанного «тот самый» — ОТКАЗ БИТЬ. Не опознали — не трогаем."""
    rec = {"pid": _int_or_zero(pid), "started": None, "image": None,
           "mtime": _float_or_none(seen_at), "legacy": True}
    verdict, why = judge(rec, (probe or process_probe)(pid),
                         tol_born=KILL_TOL_BORN if tol_born is None else tol_born)
    return (verdict == OURS_ALIVE), why


def release(path, probe=None):
    """Снять лок ТОЛЬКО если он наш — и «наш» здесь тоже по двум приметам, а не по номеру:
    процесс, чей номер совпал после рестарта, иначе снял бы чужой живой лок."""
    rec = read_lock(path)
    if rec is None:
        return False
    if not is_me(rec, probe or process_probe):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def retire(path, rec, retire_dir=None, clock=None):
    """Стухший лок → в резерв (не удаляем: файл — улика). → путь резерва | None.

    ПЕРЕД ПЕРЕЕЗДОМ ПЕРЕЧИТЫВАЕМ ФАЙЛ и сверяем, что это ТОТ ЖЕ лок, который судили. Без этой
    сверки открыта гонка: пока мы судили, владелец мог умереть, а другой экземпляр — взять лок
    заново, и мы снесли бы ЖИВОЙ лок по приговору, вынесенному мёртвому."""
    now = read_lock(path)
    if now is None:
        return None
    if now.get("pid") != rec.get("pid") or abs(_float(now.get("mtime")) - _float(rec.get("mtime"))) > 0.001:
        return None                                    # лок сменился под руками — приговор протух
    d = retire_dir or reserve_dir(path)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime((clock or time.time)()))
    dest = os.path.join(d, "%s.%s.txt" % (os.path.basename(path), stamp))
    try:
        os.makedirs(d, exist_ok=True)
        os.replace(path, dest)
        return dest
    except OSError:
        try:
            os.remove(path)                            # резерв не вышел — забор важнее улики
        except OSError:
            return None
        return None


# ═══════════════════════════════════ мелочи ═══════════════════════════════════════════════════

def is_me(rec, probe):
    """Владелец лока — ЭТОТ САМЫЙ запуск? Номер обязателен, но его одного мало: сверяем момент
    старта, если он в локе записан. Лок старого формата с нашим номером считаем нашим — другого
    признака у него нет, а ошибиться здесь можно только внутри одного живого процесса."""
    if not rec or rec.get("pid") != os.getpid():
        return False
    if rec.get("started") is None:
        return True
    me = whoami(probe=probe)
    if me.get("started") is None:
        return False
    return abs(float(me["started"]) - float(rec["started"])) <= TOL_SAME


def _same_image(a, b):
    """Сверяем ИМЯ образа, а не полный путь. Полный путь сравнивать соблазнительно и опасно:
    одна и та же программа приходит то DOS-путём, то путём устройства, и ложное «чужой» здесь
    стоит двойного запуска. Имя `python.exe` против `PinWin.exe` решает наш класс целиком."""
    return _base(a) == _base(b)


def _is_python_image(image):
    return _base(image) in _PY_IMAGES


def _base(p):
    return os.path.basename(str(p or "")).lower().replace("/", "\\").split("\\")[-1]


def _int_or_zero(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float_or_none(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    f = _float_or_none(v)
    return 0.0 if f is None else f


def _stamp(ts):
    try:
        return time.strftime("%d.%m %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return "?"
