# -*- coding: utf-8 -*-
# cowork_log_append.py — прямая запись строки-итога в мозг (cowork_log) через Bridge.
# Запуск: python cowork_log_append.py "DONE Dispatch <время>: что сделал"
#         python cowork_log_append.py [-]   ← текст из stdin (кириллица, многострочный блок)
#
# ГАРД УСЫХАНИЯ (класс 17.07.2026, образец — VPS cclog.py:200-203). Запись идёт циклом
# read_doc → склейка → write_doc, то есть документ ПЕРЕЗАПИСЫВАЕТСЯ целиком. Значит любая
# кривизна чтения превращается в потерю журнала: мост отвечает {"ok":true,"text":""} и когда
# док действительно пуст, и когда чтение сорвалось. Прежний гард ловил только None, а ""
# проходил насквозь — и 758 575 символов уехали бы в одну строку. Отсюда два правила:
#   1) пустой текст от моста = ОТКАЗ ЧТЕНИЯ, а не пустой документ;
#   2) новый текст короче старого = аномалия сборки, запись отменяется.
# Оба отказа падают в общий except main(), поэтому строка НЕ теряется, а уходит в спул ровно
# как при таймауте: защита не смеет превращаться в потерю записи.
import os, re, sys, json, time, socket, datetime, urllib.request, urllib.parse, urllib.error
import io_utf8   # переключатель stdout/stderr в UTF-8 (класс «charmap can't encode 📊»)
import bridge_http   # durable-транспорт: ручной обход редиректа моста (класс 02.08.2026)

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
DOC_NAME = "cowork_log"
SPOOL_PATH = os.path.join(HERE, "cowork_log.spool")   # имя по образцу trainer_log.spool

# Мост — веб-приложение Apps Script: /exec отвечает 302 на второе плечо, и цель редиректа
# ИНОГДА отдаёт 404. 28.07 строка журнала потерялась именно так, хотя живые пробы тем же адресом
# с этого же ПК отвечали 200 (docs/artifacts/2026-07-28-journal-write-fixes.md §1). Значит 404
# здесь ВРЕМЕННЫЙ и подлежит повтору; постоянные ошибки (401/403) повторять бессмысленно.
# С 02.08.2026 первым это плечо повторяет САМ транспорт (`bridge_http._fetch_receipt`) — и там
# повтор дешевле и безопаснее: он переспрашивает ТОЛЬКО расписку, не переотправляя запись.
# Здешний повтор остался внешним рубежом на сбой ПЕРВОГО плеча.
_RETRY_HTTP = (404, 429, 500, 502, 503, 504)
RETRY_TRIES = int(os.getenv("BRIDGE_RETRY_TRIES", "2") or "2")
RETRY_PAUSE_SEC = float(os.getenv("BRIDGE_RETRY_PAUSE", "1") or "1")


class UnknownOutcome(RuntimeError):
    """Исход записи НЕ УСТАНОВЛЕН: расписка моста — отказ, а обратное чтение не удалось.

    Отдельный тип, потому что это НЕ «не записано». Слепой повтор ВРУЧНУЮ здесь запрещён: у
    новой попытки будет свой штамп времени, а дедуп досылки сверяет строку ДОСЛОВНО — второй
    штамп он не поймает и в журнал ляжет дубль. Спул при этом безопасен: досылку дедуп сверяет
    с живым доком (см. compose)."""


class ShrinkGuard(RuntimeError):
    """Отказ гарда усыхания: писать НЕ будем, чтобы не укоротить журнал.

    Отдельный тип — ради честного заголовка в stderr: это не сбой моста, а наш сознательный
    отказ. Наследуется от RuntimeError, чтобы общий except в main() отработал как обычно и
    положил строку в спул."""


def load_env(path):
    vals = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return vals

HTTP_TIMEOUT = 30

# ХОДИМ ЧЕРЕЗ bridge_http, А НЕ ГОЛЫМ urlopen (класс 02.08.2026). Голый `urlopen` идёт по 302
# моста САМ и переигрывает POST как GET без тела — расписку на запись выдавал `doGet` («Invalid
# or missing token»), притом что строка ЛОЖИЛАСЬ. Разбор — docs/artifacts/
# 2026-08-02-bridge-receipt-leg-not-token.md, форма обхода — с VPS (bridge_client._exchange).
# Контракт функций прежний: разобранный JSON либо ИСКЛЮЧЕНИЕ, поэтому with_retry/transient/спул
# и обратное чтение ниже работают ровно как работали. opener — точка инъекции тестов.

def get(url, params, opener=None):
    # read_doc живёт в doGet Bridge → шлём GET с параметрами в query-строке
    # (ровно как pc_agent._bridge_read_doc). Токен идёт в query, в логи не печатаем.
    return bridge_http.request_json(url, "GET", params=params, timeout=HTTP_TIMEOUT, opener=opener)

def post(url, payload, opener=None):
    return bridge_http.request_json(url, "POST", payload=payload, timeout=HTTP_TIMEOUT,
                                    opener=opener)

def get_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and key in obj and isinstance(obj[key], str):
            return obj[key]
    return None

def transient(e):
    """Временный ли сбой (стоит повторить). HTTPError проверяем ПЕРВЫМ: он наследник URLError.

    `BridgeReceiptLost` — НИКОГДА (проверяем его ПЕРВЫМ, он наследник BridgeTransportError):
    запрос ушёл, мутация МОГЛА исполниться, и повтор даст вторую запись. Прочие отказы нашего
    транспорта временные, и это безопасно ПО ПОСТРОЕНИЮ: `bridge_http._leg_error` отдаёт голый
    `BridgeTransportError` только на GET — идемпотентном по определению. Живой случай, ради
    которого это заведено (02.08, 15:44 UTC): второе плечо бросило чтение журнала обратно на
    `/exec`, ключ расписки был мёртв — лечится не повтором мёртвого ключа, а НОВЫМ запросом
    целиком (форма VPS `retry_full=True` для read-only GET)."""
    if isinstance(e, bridge_http.BridgeReceiptLost):
        return False
    if isinstance(e, bridge_http.BridgeTransportError):
        return True
    if isinstance(e, urllib.error.HTTPError):
        return e.code in _RETRY_HTTP
    return isinstance(e, (urllib.error.URLError, TimeoutError, socket.timeout, OSError))


def with_retry(fn, tries=None, _sleep=None):
    """fn() с повтором при ВРЕМЕННОМ сбое. Постоянную ошибку пробрасывает сразу."""
    n = RETRY_TRIES if tries is None else tries
    sleeper = _sleep if _sleep is not None else time.sleep
    for i in range(max(1, n)):
        try:
            return fn()
        except Exception as e:
            if i + 1 >= n or not transient(e):
                raise
            sys.stderr.write(f"повтор после временного сбоя ({type(e).__name__}) — "
                             f"попытка {i + 2}/{n}\n")
            try:
                sleeper(RETRY_PAUSE_SEC)
            except Exception:
                pass


def spool_read(path=None):
    """Отложенные прошлыми сбоями строки (старые сверху). → list[str]."""
    try:
        with open(path or SPOOL_PATH, "r", encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except Exception:
        return []


def spool_add(line, path=None):
    """Отложить строку на диск, чтобы она НЕ потерялась при сбое моста. → путь спула."""
    p = path or SPOOL_PATH
    with open(p, "a", encoding="utf-8") as f:
        f.write(line.replace("\n", " ") + "\n")
    return p


def spool_clear(path=None):
    try:
        os.remove(path or SPOOL_PATH)
    except Exception:
        pass


# ───────────── РЕЕСТР УСПЕШНЫХ ЗАПИСЕЙ (класс 30.07.2026: «статус врёт») ─────────────
# Спул выше хранит ПРОВАЛИВШИЕСЯ строки. Обратной половины не было вовсе: у ПК не оставалось
# НИКАКОГО местного следа, что запись в журнал прошла. Из-за этого демон, закрывая задачу по
# таймауту, не мог отличить «работала и записала» от «молчала» — и писал владельцу «провалена»
# там, где работа была сделана (живой случай: задача 61 — коммит a3f75dd и записи в журнал).
# Реестр закрывает именно эту дыру: одна строка JSON на каждую УСПЕШНО ушедшую запись.
try:                       # реестр — КОЛЬЦО на 500 строк, поэтому запись теста не «сорит», а
    import log_setup       # ВЫТЕСНЯЕТ настоящие записи с головы: замер полного гейта 05.08.2026 —
    LEDGER_PATH = log_setup.state_path(os.path.join(HERE, "cowork_log.ledger"))   # 13 живых строк
except Exception:          # журнала за 01.08 (задачи #173–#176) ушли из реестра за один прогон.
    LEDGER_PATH = os.path.join(HERE, "cowork_log.ledger")
LEDGER_KEEP = 500          # кап: окно следов у демона — часы, а не месяцы
LEDGER_LINE_MAX = 300      # в реестре нужен опознавательный кусок, а не весь текст записи


def ledger_add(line, ts=None, path=None, keep=LEDGER_KEEP):
    """Отметить УСПЕШНО ушедшую строку журнала в локальном реестре. → путь реестра | None.
    FAIL-SAFE: любой сбой реестра проглатывается — он вспомогательный, а сама запись в мозг
    к этому моменту УЖЕ состоялась, и рушить её отчёт из-за журнала следов нельзя."""
    p = path or LEDGER_PATH
    try:
        rec = {"ts": (ts or datetime.datetime.now(datetime.timezone.utc)).isoformat(),
               "line": " ".join(str(line or "").split())[:LEDGER_LINE_MAX]}
        old = []
        try:
            with open(p, "r", encoding="utf-8") as f:
                old = [x.rstrip("\n") for x in f if x.strip()]
        except FileNotFoundError:
            pass
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join((old + [json.dumps(rec, ensure_ascii=False)])[-keep:]) + "\n")
        return p
    except Exception:
        return None


# ─────────────────── КОНТРАКТ СТРОКИ ЖУРНАЛА (28.07.2026) ───────────────────
# «<ТИП> <ГГГГ-ММ-ДД ЧЧ:ММ UTC>: <текст>» — ОДНА запись = ОДНА строка.
#
# Зачем: серверная ротация делит журнал по ЗАГОЛОВКАМ записей (regex «тип + дата»), а дату до сих
# пор получали только строки БЕЗ типа — `msg.startswith(("DONE","NOTE","ASK"))` возвращал строку
# как есть. Живой замер 28.07: сплиттер видел 80 записей из 1747 (4.6%), резать журнал было не по
# чему. Теперь дату получает КАЖДАЯ строка; уже проштампованная (например, дошланная из спула)
# второй штамп не получает — функция идемпотентна.
LOG_TYPES = ("DONE", "NOTE", "ASK", "PLAN", "BLOCKED", "WAITING", "SKIPPED")
STAMP_FMT = "%Y-%m-%d %H:%M UTC"
_TYPE_ALT = "|".join(LOG_TYPES)
_RE_STAMPED = re.compile(r"^(?:%s)\s+\d{4}-\d{2}-\d{2}\b" % _TYPE_ALT)   # тип И дата уже есть
_RE_TYPED = re.compile(r"^(%s)\b[\s:]*" % _TYPE_ALT)                     # тип есть, даты нет


def stamp_line(msg, stamp):
    """Привести строку к контракту. Три случая, ровно по трём путям записи:
      • уже «<ТИП> <дата>…» (спул, повторная досылка)        → отдаём как есть (идемпотентность);
      • «NOTE Orchestrator: …» / «ASK Dispatch …» (тип есть) → дату вставляем СРАЗУ ПОСЛЕ типа;
      • голый текст (ручная строка)                          → это итог сессии, тип DONE.
    Переносы схлопываются вызывающим (main): многострочная запись рвёт разбор по заголовкам."""
    if _RE_STAMPED.match(msg):
        return msg
    m = _RE_TYPED.match(msg)
    if m:
        return "%s %s: %s" % (m.group(1), stamp, msg[m.end():].lstrip())
    return "DONE %s: %s" % (stamp, msg)


# ───────── ЖУРНАЛ — ИНДЕКС, А НЕ ХРАНИЛИЩЕ ТЕЛ (класс 31.07.2026) ─────────
# ЗАМЕР по ЖИВОМУ журналу (снимок 31.07: cowork_log 123 283 симв. + cowork_log_archive
# 763 217 симв., 2058 записей за 19.06–30.07). Замер ВОСПРОИЗВОДИМ: `journal_measure.py`
# (--refresh снимет свежие снимки через brain_writer, read-only); разбор и выбор порога —
# docs/artifacts/2026-07-31-journal-index-threshold.md. Единица замера —
# ЛОГИЧЕСКАЯ ЗАПИСЬ, склеенная в ОДНУ строку ровно так, как её кладёт main() ниже: только это
# и есть «длина строки журнала» (строгий разбор «тип+дата» на архиве даёт псевдозапись на
# 395 593 символа — артефакт парсера, а не живая строка; на это легко купиться).
#   • 78.2% записей ≤ 600 символов — и держат они всего 25.5% объёма журнала;
#   • оставшиеся 21.8% (448 записей) держат 74.5% объёма — это ОТЧЁТЫ, уехавшие целиком;
#   • строки УЖЕ правильного вида «итог + путь» (81 живая ARTIFACT-запись): p50=177, p90=218,
#     МАКСИМУМ 588 символов.
# Порог = 600 — ближайшее круглое число ВЫШЕ самой длинной живой строки правильного вида (588).
# То есть он по построению не трогает НИ ОДНОЙ записи, которая уже соблюдает формат
# «итог + ссылка», и режет только тела. Число получено ЗАМЕРОМ, а не вкусом: менять его —
# только пересняв замер (порог назначенный от порога измеренного отличается тем, что второй
# можно оспорить числами).
LINE_MAX = 600
# Тела живут ОТДЕЛЬНОЙ папкой, а не вперемешку с решениями владельца: артефактов-решений в
# docs/artifacts 94 штуки, а вынос по замеру даёт ~140 файлов в неделю — смешать значит утопить
# решения в теле переписки.
SPILL_REL_DIR = "docs/artifacts/journal"
SPILL_DIR = os.path.join(HERE, *SPILL_REL_DIR.split("/"))
# Границы фразы для реза головы. Порядок не важен — берём самую ПОЗДНЮЮ подходящую.
_CUT_MARKS = (". ", "! ", "? ", "; ", " — ", " · ", ", ")


def _cut_head(text, budget):
    """Голова записи не длиннее budget, обрезанная по границе ФРАЗЫ (иначе — слова).

    Рез посреди слова запрещён: строку-итог читает человек, а не греп. Слишком ранний рез тоже
    вреден (итог перестаёт быть итогом), поэтому граница принимается только со второй половины
    бюджета; не нашлась — режем по последнему пробелу."""
    if len(text) <= budget:
        return text
    head = text[:budget]
    floor = budget // 2
    cut = -1
    for mark in _CUT_MARKS:
        i = head.rfind(mark)
        if i >= floor:
            cut = max(cut, i + (1 if mark[0] in ".!?;" else 0))
    if cut < floor:
        cut = head.rfind(" ")
    if cut < floor:
        cut = budget
    return head[:cut].rstrip(" ,;:—·-")


def _spill_path(spill_dir, now, kind):
    """Свободное имя файла тела: <дата>-<ЧЧММСС>-<тип>[-N].md. Секунда одна, писателей может
    быть двое (демон + хук) — коллизию разводим суффиксом, а не молча затираем чужое тело."""
    base = now.strftime("%Y-%m-%d-%H%M%S")
    for n in range(1, 100):
        name = "%s-%s%s.md" % (base, kind, "" if n == 1 else "-%d" % n)
        path = os.path.join(spill_dir, name)
        if not os.path.exists(path):
            return path
    return os.path.join(spill_dir, "%s-%s-p%d.md" % (base, kind, os.getpid()))


def _spill_text(line, body, now, cap):
    """Содержимое файла-тела. Тело кладём в ИСХОДНОМ виде (с переносами): схлопнутая в одну
    строку простыня нечитаема, а сохранить формат стоит ноль — в журнал-то уходит итог."""
    src = (body if (body or "").strip() else line).rstrip()
    return ("# Тело записи журнала — %s UTC\n\n"
            "Вынесено АВТОМАТИЧЕСКИ писателем `cowork_log_append.py`: строка журнала была "
            "**%d символов** при пороге **%d** (журнал — индекс, а не хранилище тел).\n"
            "В `cowork_log` ушёл итог и ссылка на этот файл.\n\n"
            "| поле | значение |\n|---|---|\n"
            "| записано | %s |\n| символов в строке | %d |\n| порог | %d |\n\n"
            "---\n\n%s\n"
            % (now.strftime("%Y-%m-%d %H:%M"), len(line), cap,
               now.strftime("%Y-%m-%d %H:%M:%S"), len(line), cap, src))


def spill(line, body=None, now=None, spill_dir=None, rel_dir=SPILL_REL_DIR, line_max=None):
    """Строку журнала длиннее порога → («итог + путь», путь тела). Иначе — как была.

    body — ИСХОДНЫЙ текст записи (до схлопывания переносов): он и уходит в файл.
    FAIL-SAFE: файл не записался → возвращаем строку БЕЗ ИЗМЕНЕНИЙ. Длинная строка в журнале
    хуже короткой, но ПОТЕРЯННАЯ хуже обеих — защита от роста не смеет становиться потерей
    (тот же принцип, что у гарда усыхания выше).
    → (строка для журнала, путь тела относительно репо | None)"""
    cap = LINE_MAX if line_max is None else line_max
    if len(line) <= cap:
        return line, None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    m = _RE_TYPED.match(line)
    kind = (m.group(1) if m else "log").lower()
    try:
        d = spill_dir or SPILL_DIR
        os.makedirs(d, exist_ok=True)
        path = _spill_path(d, now, kind)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_spill_text(line, body, now, cap))
    except Exception:
        return line, None
    rel = rel_dir.rstrip("/") + "/" + os.path.basename(path)
    tail = " … → %s (полный текст %d симв.)" % (rel, len(line))
    return _cut_head(line, max(1, cap - len(tail))) + tail, rel


def line_already_in(old, line):
    """Строка УЖЕ стоит в доке ЦЕЛЬНОЙ строкой? Границы — край текста или перевод строки;
    хвостовые пробелы не в счёт (склейка ниже разделяет строки двумя пробелами перед «\\n»).
    Подстрока внутри чужой строки вхождением НЕ считается — иначе короткая запись «уже есть»
    в чужой длинной, и досылка молча потеряла бы её (образец brain_writer._whole_line_count)."""
    body = (line or "").strip()
    if not body:
        return False
    return any(ln.strip() == body for ln in str(old or "").split("\n"))


def compose(new_line, pending, old):
    """Текст дока после дозаписи: новейшая строка сверху, отложенные следом (от новых к старым),
    ниже — прежний текст целиком. Вынесено отдельной функцией, чтобы гард монотонности можно было
    проверить тестом, подменив сборку на «усыхающую»: по построению склейка только ДОБАВЛЯЕТ.
    → (текст, сколько отложенных отброшено дублями).

    ДЕДУП ДОСЫЛКИ (класс «ложный 401», живой случай 01.08.2026). Мост регулярно рапортует отказ
    записи ПОСЛЕ того, как строка уже легла в док (401/таймаут на ответе, не на записи). Строка
    при этом уходит в спул — и следующая успешная запись дописывала её ВТОРОЙ раз. Дубль в
    журнале хуже пропуска: журнал — индекс, по нему считают и ищут. Досылаем только те
    отложенные, которых в ЖИВОМ доке ещё нет; сверка идёт по свежепрочитанному `old`, то есть
    по факту, а не по местному реестру следов."""
    keep = [ln for ln in pending if not line_already_in(old, ln)]
    dropped = len(list(pending)) - len(keep)
    return "  \n".join([new_line] + list(reversed(keep))) + "  \n" + old, dropped


def readback_landed(url, token, line):
    """ЛЕГЛА ЛИ строка НА САМОМ ДЕЛЕ — по живому доку, а не по коду ответа.

    ОТВЕТ МОСТА НЕ ЕСТЬ ФАКТ ЗАПИСИ (класс; живые случаи: ложный 401 01.08.2026 и HTTP 404 на
    задаче 166 — оба раза строка ЛЕГЛА). Механизм, а не гипотеза: Apps Script отдаёт тело POST-а
    ВТОРЫМ плечом (`/exec` → 302 → echo), падение этого плеча приходит
    тем же исключением, что и падение первого, а мутация к тому моменту уже зафиксирована —
    `writeDoc_` зовёт `brainTextWrite_(id, text)` и лишь ПОТОМ собирает `{ok:true}`
    (копия прода: tmp/bridge_v75/ReadDocs.js:352-354). Значит код ответа описывает доставку
    РАСПИСКИ, а не судьбу записи. Разбор — docs/artifacts/2026-08-01-bridge-receipt-not-fact.md

    → True  — строка в доке (запись легла; отказ моста был потерей расписки);
      False — док ПРОЧИТАН и строки в нём нет (единственный предъявимый факт «не легло»);
      None  — прочитать не удалось: исход НЕИЗВЕСТЕН, «не записано» говорить нельзя."""
    try:
        r = with_retry(lambda: get(url, {"action": "read_doc", "token": token, "name": DOC_NAME}))
    except Exception:
        return None
    if not (isinstance(r, dict) and r.get("ok")):
        return None
    back = get_text(r)
    # Пустой текст мост отдаёт и при СОРВАННОМ чтении (тот же класс, что ГАРД 1 в main): это
    # «не знаю», а не «строки нет». Иначе отказ чтения выдал бы себя за факт «не легло».
    if back is None or not back.strip():
        return None
    return line_already_in(back, line)


def read_stdin_text(stream=None):
    """stdin как UTF-8 ЯВНО. Живой дефект 29.07.2026: на Windows sys.stdin декодирует ТЕКСТ
    кодировкой консоли (здесь cp1251), а пайп шлёт UTF-8 — и кириллица легла в мозг мохибейком
    («С‚РёРї РўРЎ» вместо «тип ТС»), пять строк подряд, включая два ARTIFACT и два DONE. Мохибейк
    не ломает ни один гард: длина растёт, текст непустой, обратное чтение совпадает с записанным —
    поэтому ловится он только глазами владельца. Читаем байты сами: UTF-8, иначе cp1251 (так шлёт
    родная консоль Windows), иначе UTF-8 с заменой — но пустую строку из-за кодировки не отдаём."""
    stream = stream if stream is not None else sys.stdin
    buf = getattr(stream, "buffer", None)
    if buf is None:                      # подменённый в тесте StringIO — там уже текст
        return stream.read()
    raw = buf.read()
    for enc in ("utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def main():
    # stdout/stderr → UTF-8: строку «OK: записано…» и отчёты об отказе (кириллица) нас часто
    # спавнят detached-ребёнком в utf-8-приёмник (dispatch_notify._cowork) — без явного UTF-8
    # cp1251-байты легли бы мохибейком. Пара к read_stdin_text() (тот же класс, входная полоса).
    io_utf8.force_utf8()
    # Текст: argv, а «-» — СЕНТИНЕЛ stdin (как у brain_writer). Без сентинела вызов
    # `cowork_log_append.py - <<EOF …` укладывал в мозг литерал «-», а тело heredoc молча
    # выбрасывал: запись есть, смысла нет — та самая тихая потеря, только наизнанку (живой
    # брак 01.08.2026, две пустые строки DONE подряд).
    raw = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else "-"
    if raw == "-":
        raw = read_stdin_text().strip()
    if not raw:
        sys.stderr.write("ОШИБКА: пустая строка-итог\n"); sys.exit(1)
    # ОДНА запись = ОДНА строка: result задачи бывает многострочным, а перенос внутри записи
    # рвёт разбор журнала по заголовкам (и может подсунуть сплиттеру ложный заголовок).
    # ИСХОДНЫЙ текст (raw) держим отдельно — он уйдёт в файл-тело, если строка переросла порог.
    msg = " ".join(raw.split())
    env = load_env(ENV_PATH)
    url = env.get("BRIDGE_URL"); token = env.get("BRIDGE_TOKEN")
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime(STAMP_FMT)
    new_line = stamp_line(msg, stamp)
    # ЖУРНАЛ — ИНДЕКС: тело длиннее LINE_MAX уезжает файлом, в мозг идёт итог и путь. Стоит
    # ДО развилки «нет конфига»: в спул тоже должна лечь короткая строка, иначе отложенная
    # простыня доедет до журнала следующим успешным вызовом и порог обойдёт себя сам.
    new_line, spilled = spill(new_line, raw, now=now)
    if not url or not token:
        spool_add(new_line)
        sys.stderr.write("ОШИБКА: нет BRIDGE_URL/BRIDGE_TOKEN в .env\nОТЛОЖЕНО в "
                         + SPOOL_PATH + " (строка не потеряна): " + new_line
                         + (("\nТело записи УЖЕ сохранено: " + spilled) if spilled else "") + "\n")
        sys.exit(1)
    pending = spool_read()      # строки прошлых сбоев — дошлём вместе с новой
    try:
        r = with_retry(lambda: get(url, {"action": "read_doc", "token": token, "name": DOC_NAME}))
        if not (isinstance(r, dict) and r.get("ok")):
            raise RuntimeError("read_doc не ok: " + json.dumps(r, ensure_ascii=False)[:300])
        old = get_text(r)
        if old is None:
            raise RuntimeError("read_doc ok, но текст не найден — НЕ пишу, чтобы не затереть. Ответ: " + json.dumps(r, ensure_ascii=False)[:300])
        # Новейшая строка сверху; отложенные — следом, от новых к старым (порядок журнала цел).
        # Отложенные, уже стоящие в живом доке, отбрасываются — см. ДЕДУП ДОСЫЛКИ в compose().
        new_text, dup_dropped = compose(new_line, pending, old)
        # ГАРД 1 (класс 17.07): пустой текст — ОТКАЗ ЧТЕНИЯ, а не пустой журнал. Тот же ответ
        # {"ok":true,"text":""} мост отдаёт и при сорванном чтении; писать поверх = затереть док.
        if not old.strip():
            raise ShrinkGuard("read_doc вернул ПУСТОЙ текст — считаю это отказом чтения, а не "
                              "пустым журналом (в доке %d символов, записали бы %d)"
                              % (len(old), len(new_text)))
        # ГАРД 2: монотонность длины (образец cclog.py:200-203). Склейка выше только ДОБАВЛЯЕТ,
        # поэтому срабатывание — аномалия сборки: лучше не записать, чем укоротить журнал.
        if len(new_text) < len(old):
            raise ShrinkGuard("новый текст КОРОЧЕ старого (было %d символов, стало бы %d) — "
                              "запись отменена" % (len(old), len(new_text)))
        # ОТВЕТ МОСТА — НЕ ФАКТ ЗАПИСИ (см. readback_landed). Отказ больше не короткое замыкание:
        # он ЗАПОМИНАЕТСЯ, а вердикт выносит ОБРАТНОЕ ЧТЕНИЕ — оно одно отличает «не легло»
        # (повтор безопасен) от «легло, потерялась расписка» (ручной повтор придёт с новым штампом,
        # дедуп досылки его не поймает — и в журнал ляжет дубль). Сам POST не повторяем.
        w, receipt_err = None, None
        try:
            w = with_retry(lambda: post(url, {"action": "write_doc", "token": token,
                                              "name": DOC_NAME, "text": new_text}))
        except Exception as e:
            receipt_err = "%s: %s" % (type(e).__name__, e)
        else:
            if not (isinstance(w, dict) and w.get("ok")):
                receipt_err = "write_doc не ok: " + json.dumps(w, ensure_ascii=False)[:300]
        landed = readback_landed(url, token, new_line)
        if landed is False:
            # Док прочитан, строки в нём НЕТ — единственный случай, где «не записано» есть факт.
            raise RuntimeError(("write_doc не прошёл (%s); " % receipt_err if receipt_err
                                else "мост ответил ok, но ")
                               + "ОБРАТНОЕ ЧТЕНИЕ: строки в доке НЕТ — запись НЕ ЛЕГЛА, "
                                 "повтор безопасен")
        if landed is None and receipt_err is not None:
            raise UnknownOutcome("расписка моста — отказ (%s), обратное чтение тоже не удалось"
                                 % receipt_err)
        spool_clear()
        for done_line in [new_line] + list(pending):   # реестр следов: и новая, и досланные
            ledger_add(done_line)
        extra = f" | досланы отложенные: {len(pending) - dup_dropped}" if pending else ""
        # Отброшенные дубли НАЗЫВАЕМ вслух: молчаливый отброс неотличим от потерянной записи.
        extra += (f" (отброшено дублями, уже в доке: {dup_dropped})") if dup_dropped else ""
        # Вынос тела называем В ОТЧЁТЕ: молчаливое усечение неотличимо от «столько и написали».
        extra += f" | тело вынесено в {spilled}" if spilled else ""
        # w["chars"] — это text.length НА СТОРОНЕ МОСТА, то есть UTF-16 code units: каждый эмодзи
        # вне BMP (📦 🔴 …) считается ЗА ДВА. Python len() того же текста будет МЕНЬШЕ. Живой замер
        # 28.07: мост отрапортовал 759635, кодовых точек в доке 759548, не-BMP символов ровно 87 —
        # сходится до единицы. Это НЕ усыхание дока: два числа просто в разных единицах, сравнивать
        # их между собой нельзя (я на этом уже споткнулся — гард тут ни при чём).
        # Судьбу РАСПИСКИ называем словом: «потеряна» = мост ответил отказом, а запись ЛЕГЛА и
        # подтверждена чтением. Промолчать нельзя — иначе отказ моста неотличим от штатной записи
        # и класс не виден ни в логах, ни владельцу.
        if receipt_err:
            extra += f" | РАСПИСКА ПОТЕРЯНА ({receipt_err}) — запись ПОДТВЕРЖДЕНА обратным чтением"
        elif landed is None:
            extra += " | обратное чтение не удалось — исход по расписке моста"
        print("OK: записано в мозг, символов:", (w or {}).get("chars", "?"), extra)
    except Exception as e:
        spool_add(new_line)
        head = ("ГАРД УСЫХАНИЯ (запись отменена)" if isinstance(e, ShrinkGuard)
                else "ИСХОД ЗАПИСИ НЕ ПОДТВЕРЖДЁН" if isinstance(e, UnknownOutcome)
                else "ОШИБКА Bridge")
        # При неизвестном исходе спул безопасен (досылку дедуп сверит с живым доком), а вот
        # РУЧНОЙ повтор — нет: у него будет свой штамп, дословный дедуп его не поймает.
        warn = ("\nСЛЕПОЙ ПОВТОР ВРУЧНУЮ ЗАПРЕЩЁН: строка могла ЛЕЧЬ — посмотри док глазами."
                if isinstance(e, UnknownOutcome) else "")
        sys.stderr.write(head + ": " + str(e) + warn + "\nОТЛОЖЕНО (строка НЕ потеряна, уйдёт "
                         "следующим успешным вызовом): " + new_line + "\nСпул: " + SPOOL_PATH
                         + (("\nТело записи УЖЕ сохранено: " + spilled) if spilled else "") + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
