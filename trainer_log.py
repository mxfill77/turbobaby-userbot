# -*- coding: utf-8 -*-
"""
trainer_log.py — ЛОГ ГРУППЫ-ТРЕНАЖЁРА в мозг (Brain-док `KB_trainer_log`).

ЗАЧЕМ. Всё, что происходит в «Тренеровке», должно оставаться в мозге, а не только в Telegram:
старт ТЕСТ-N, каждая реплика клиента (текст/гео/фото), каждый ответ бота ЦЕЛИКОМ (с шапкой и
служебными тегами), каждое нажатие кнопок и каждый принятый/отменённый урок. По этому логу
владелец разбирает прогон, а не по памяти сессии.

ФОРМАТ СТРОКИ (одна строка = одно событие):
    TRN <ГГГГ-ММ-ДД ЧЧ:ММ> | TEST-N | client|bot|btn|lesson | <текст>
Переводы строк внутри текста заменяем на «⏎» — строка обязана остаться ОДНОЙ (иначе лог
перестаёт быть построчным и его нельзя резать/архивировать по границе события).

КАНАЛ — ТОТ ЖЕ, что у cowork_log_append.py: Bridge read_doc/write_doc. ПРОБА ПО ФАКТУ (23.07):
  • Bridge разрешает адресацию и по имени (`name=`), и по файловому id (`id=`) — и на чтение,
    и на запись (write_doc с id уходит в DriveApp.getFileById);
  • но СОЗДАВАТЬ доки канал НЕ УМЕЕТ: имена резолвятся по фиксированному манифесту
    (`list_brain`), и `write_doc name=KB_trainer_log` отвечает `unknown_name`.
Поэтому обходного пути мы НЕ строим (прямое требование ТЗ): пустой док создаёт Штаб и передаёт
file id. Пока id не задан — модуль честно НЕ ПИШЕТ и возвращает status='no_doc' (тренажёр при
этом работает как работал: лог — наблюдение, а не часть клиентского пути).

ГДЕ ЖИВЁТ ID. `.env` мы не трогаем (ограничение задачи), поэтому:
  1) переменная окружения TRAINER_LOG_DOC_ID (если Штаб положит её туда) — приоритет;
  2) сайдкар-файл `trainer_log_doc.json` рядом с кодом: {"doc_id": "...", "archive_id": "..."}.
Секретов тут нет: file id Drive регистрируется в KB_MASTER открытой строкой.

РОТАЦИЯ. Док > TRAINER_LOG_MAX_CHARS (≈1 МБ) → САМЫЕ СТАРЫЕ строки срезаются в архив-док
(archive_id). Архива нет → срез НЕ делаем и пишем предупреждение в лог процесса: молча терять
историю нельзя (лучше распухший док, чем тихо съеденный хвост).

ДОСТАВКА (пакет «полнота лога»): каждая операция канала ретраится с экспоненциальным бэкоффом
(TRAINER_LOG_RETRIES × TRAINER_LOG_BACKOFF·2^k). Так и не доставили → строка НЕ теряется: уходит
в ЛОКАЛЬНЫЙ СПУЛ (trainer_log.spool, по строке на событие) и дозаписывается в док при СЛЕДУЮЩЕМ
успешном append (свежее сверху, спул — под ним, хронология сохраняется). Статус доставки каждой
записи логируется в лог процесса (ok / spooled / error), чтобы «не дошло» было видно, а не молчало.

FAIL-SAFE ВЕЗДЕ. Любая ошибка сети/Bridge/парса → запись не удалась, исключение НЕ летит наружу,
тренажёр не ломается. Лог мозга не имеет права уронить клиентский контур.
"""

import os
import time
import json
import datetime
import contextlib
import urllib.request
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
SIDECAR = os.path.join(BASE_DIR, "trainer_log_doc.json")
LOCK_FILE = os.path.join(BASE_DIR, "trainer_log.lock")
SPOOL_FILE = os.path.join(BASE_DIR, "trainer_log.spool")   # недоставленные TRN-строки (gitignored)

DOC_NAME = "KB_trainer_log"            # имя дока в мозге (для KB_MASTER и когда он попадёт в манифест)
KIND_CLIENT, KIND_BOT, KIND_BTN, KIND_LESSON = "client", "bot", "btn", "lesson"
KINDS = (KIND_CLIENT, KIND_BOT, KIND_BTN, KIND_LESSON)

def _int_env(name, default):
    """Числовая настройка из окружения с ПОЛНОЙ защитой (идиом log_setup._int_env). Мусор/пусто →
    default. Без try мусор в env ронял бы ИМПОРТ модуля, а его импортит userbot_listen на уровне
    модуля ⇒ не поднимался бы весь userbot. Заявленный «FAIL-SAFE ВЕЗДЕ» обязан начинаться здесь."""
    try:
        return int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


# 1 МБ — порог ротации из ТЗ. Считаем в СИМВОЛАХ (Bridge отдаёт/принимает текст, не байты).
MAX_CHARS = _int_env("TRAINER_LOG_MAX_CHARS", 1000000)
# Сколько символов оставляем в живом доке после среза (свежий хвост), остальное — в архив.
KEEP_CHARS = _int_env("TRAINER_LOG_KEEP_CHARS", 700000)
HTTP_TIMEOUT = _int_env("TRAINER_LOG_TIMEOUT", 30)
# ДОСТАВКА: попыток на КАЖДУЮ операцию канала (1 = без ретраев) и база экспоненциального бэкоффа
# (пауза перед k-м повтором = BACKOFF_SEC · 2^(k-1)). Вызовы живут в фоновом потоке
# (asyncio.to_thread из _trn_log), поэтому паузы диалог не задерживают.
RETRIES = _int_env("TRAINER_LOG_RETRIES", 3)
BACKOFF_SEC = _int_env("TRAINER_LOG_BACKOFF", 2)

# ЗАГОЛОВОК-ЛЕГЕНДА дока (первая строка, положена Штабом при создании: «TRN LOG v1 | …»).
# Новые события ложатся СВЕРХУ, поэтому без пиннинга легенда уехала бы вниз, а при ротации —
# первой же в архив: живой док остался бы без описания собственного формата.
HEADER_MARK = "TRN LOG"

# СЕРИАЛИЗАЦИЯ ЗАПИСИ. Bridge умеет только read+write ЦЕЛОГО дока — значит два потребителя
# (userbot и moderbot) в один момент делают read-modify-write, и запись, пришедшая второй,
# ЗАТИРАЕТ строку первой. Событие теряется молча, а ТЗ требует «писать ВСЁ». Оба процесса живут
# на ОДНОМ ПК, поэтому критическую секцию закрываем файловым локом (тот же идиом O_CREAT|O_EXCL,
# что userbot.lock/moderation_bot.lock). Лок протух (владелец умер посреди HTTP) → забираем.
# Не дождались за LOCK_WAIT_SEC → пишем ВСЁ РАВНО с предупреждением: потерять событие хуже,
# чем рискнуть редкой гонкой. Пороги масштабируются на RETRIES: держатель лока теперь законно
# может пережидать ретраи с бэкоффом (иначе живого владельца посчитали бы протухшим).
LOCK_STALE_SEC = _int_env("TRAINER_LOG_LOCK_STALE", 4 * HTTP_TIMEOUT * RETRIES + 30)
LOCK_WAIT_SEC = _int_env("TRAINER_LOG_LOCK_WAIT", 2 * HTTP_TIMEOUT * RETRIES + 10)

import logging
log = logging.getLogger("trainer_log")   # хендлеры вешает процесс-хозяин (userbot/moderbot)


# --- ГАРД ТЕСТОВОГО КОНТЕКСТА (иначе гейт пишет мусор в ЖИВОЙ док мозга) ----------------------
# ЖИВОЙ ИНЦИДЕНТ 23.07.2026: точки записи повесили на боевые обработчики (moderation_bot.
# _trainer_callback и т.п.), а юнит-тесты эти обработчики ДЁРГАЮТ по-настоящему. Как только в
# сайдкар лёг file id, ДВА прогона гейта залили в KB_trainer_log 30 строк из фикстур («хочу
# скутер», «Ответ бота ТЕСТ-клиенту», TEST-1/4/6) и растянули гейт с 18с до 106с на сетевых
# round-trip'ах. Точка правды «идёт тестовый прогон» в репозитории одна — log_setup.is_test_context
# (TESTING / TURBOBABY_TEST_LOGS / PYTEST_CURRENT_TEST / запуск через unittest|pytest); тем же
# сигналом логи уводятся в temp, а moderation_ipc — на тестовую БД.
# Гард срабатывает ТОЛЬКО на боевом транспорте: если вызывающий инжектировал get/post (мок-тесты
# самого trainer_log), запись идёт как обычно — там сети нет и проверять нечего.

def in_test_context() -> bool:
    """Идёт тестовый прогон? Ошибка импорта log_setup → False (боевой путь важнее, fail-safe)."""
    try:
        from log_setup import is_test_context
        return bool(is_test_context())
    except Exception:
        return False


# ------------------------------- конфиг канала -------------------------------

def _env_file(path=None):
    """BRIDGE_URL/BRIDGE_TOKEN ПРЯМО из .env репо (как cowork_log_append): сервис-контекст может
    не иметь их в окружении процесса. Секреты не логируем и наружу не отдаём."""
    vals = {}
    try:
        with open(path or ENV_PATH, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return vals


def _sidecar(path=None):
    try:
        with open(path or SIDECAR, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def doc_id(sidecar_path=None):
    """File id живого дока: env TRAINER_LOG_DOC_ID → сайдкар → '' (док ещё не заведён Штабом)."""
    return (os.getenv("TRAINER_LOG_DOC_ID", "").strip()
            or str(_sidecar(sidecar_path).get("doc_id") or "").strip())


def archive_id(sidecar_path=None):
    """File id архив-дока (для ротации) или '' — тогда срез НЕ делаем."""
    return (os.getenv("TRAINER_LOG_ARCHIVE_DOC_ID", "").strip()
            or str(_sidecar(sidecar_path).get("archive_id") or "").strip())


# ------------------------------- формат строки -------------------------------

def _stamp(now=None):
    return (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M")


def format_line(n, kind, text, now=None):
    """Строка лога ровно по ТЗ: «TRN <дата время> | TEST-N | <kind> | <текст>».
    Переводы строк → «⏎» (одно событие = одна строка). Неизвестный kind → 'btn' (не теряем событие)."""
    kind = kind if kind in KINDS else KIND_BTN
    body = " ⏎ ".join(s.strip() for s in str(text or "").splitlines() if s.strip()) or "[пусто]"
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return f"TRN {_stamp(now)} | TEST-{n} | {kind} | {body}"


# ------------------------------- транспорт Bridge ----------------------------

def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(full, method="GET"),
                                timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _doc_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and isinstance(obj.get(key), str):
            return obj[key]
    return None


def read_doc(did, env=None, get=None):
    """Текст дока по file id → str | None (ошибка/нет доступа). Читает ТОЛЬКО по id."""
    env = env if env is not None else _env_file()
    url, token = env.get("BRIDGE_URL"), env.get("BRIDGE_TOKEN")
    if not (url and token and did):
        return None
    try:
        r = (get or _get)(url, {"action": "read_doc", "token": token, "id": did})
    except Exception as e:
        log.warning("trainer_log: read_doc упал: %s: %s", type(e).__name__, e)
        return None
    if not (isinstance(r, dict) and r.get("ok")):
        log.warning("trainer_log: read_doc не ok: %s", json.dumps(r, ensure_ascii=False)[:200])
        return None
    return _doc_text(r)


def write_doc(did, text, env=None, post=None):
    """Перезаписать док по file id. → True|False. Пустой id/текст None → False (ничего не пишем)."""
    env = env if env is not None else _env_file()
    url, token = env.get("BRIDGE_URL"), env.get("BRIDGE_TOKEN")
    if not (url and token and did) or text is None:
        return False
    try:
        w = (post or _post)(url, {"action": "write_doc", "token": token, "id": did, "text": text})
    except Exception as e:
        log.warning("trainer_log: write_doc упал: %s: %s", type(e).__name__, e)
        return False
    if not (isinstance(w, dict) and w.get("ok")):
        log.warning("trainer_log: write_doc не ok: %s", json.dumps(w, ensure_ascii=False)[:200])
        return False
    return True


# ------------------------------- ретрай с бэкоффом ---------------------------

def _retry(op, failed, what, retries=None, sleep=None):
    """Повторить операцию канала до retries раз с экспоненциальным бэкоффом. op() — операция,
    failed(res) — предикат «не удалось». → последний результат (удачный или нет). Никогда не
    бросает сверх того, что глотают сами read_doc/write_doc; sleep инъектируется в тестах."""
    retries = max(1, RETRIES if retries is None else retries)
    sleep = time.sleep if sleep is None else sleep
    res = op()
    for attempt in range(1, retries):
        if not failed(res):
            return res
        delay = max(0, BACKOFF_SEC) * (2 ** (attempt - 1))
        log.info("trainer_log: %s не удался (попытка %s/%s) — повтор через %sс",
                 what, attempt, retries, delay)
        try:
            sleep(delay)
        except Exception:
            pass
        res = op()
    return res


# ------------------------------- локальный спул ------------------------------
# Недоставленные TRN-строки НЕ теряем: складываем в локальный файл (по строке на событие,
# хронологически — старые сверху) и дозаписываем в док при СЛЕДУЮЩЕМ успешном append.
# Спул трогаем только ПОД doc_lock (как и сам док) — второй процесс не съест чужие строки.

def _spool_read(path=None):
    """Недоставленные строки из спула (хронологический порядок) → list[str]. Нет/битый → []."""
    try:
        with open(path or SPOOL_FILE, encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except OSError:
        return []


def _spool_add(line, path=None):
    """Дописать недоставленную строку в спул. → True (сохранили) | False (и спул не удался)."""
    try:
        with open(path or SPOOL_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except OSError as e:
        log.warning("trainer_log: спул недоступен (%s) — строка ПОТЕРЯНА: %s",
                    type(e).__name__, line[:160])
        return False


def _spool_clear(path=None):
    try:
        os.remove(path or SPOOL_FILE)
    except OSError:
        pass


# ------------------------------- лок записи ----------------------------------

def _lock_age(path):
    """Возраст лок-файла в секундах или None (лока нет/не прочитать)."""
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


@contextlib.contextmanager
def doc_lock(path=None, wait=None, stale=None, sleep=0.25):
    """Критическая секция read-modify-write дока. Отдаёт True (лок наш) либо False (не дождались —
    вызывающий пишет всё равно, но с предупреждением). Никогда не бросает и никогда не оставляет
    лок висеть: снимаем в finally, а протухший (старше stale) забираем у мёртвого владельца."""
    path = path or LOCK_FILE
    wait = LOCK_WAIT_SEC if wait is None else wait
    stale = LOCK_STALE_SEC if stale is None else stale
    deadline = time.time() + max(0, wait)
    mine = False
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            mine = True
            break
        except FileExistsError:
            age = _lock_age(path)
            if age is not None and age > stale:
                log.warning("trainer_log: лок протух (%.0fс) — забираю", age)
                try:
                    os.remove(path)
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                log.warning("trainer_log: лок занят дольше %sс — пишу без него (риск гонки)", wait)
                break
            time.sleep(sleep)
        except OSError as e:                      # каталог недоступен и т.п. — лок не обязателен
            log.warning("trainer_log: лок недоступен (%s) — пишу без него", type(e).__name__)
            break
    try:
        yield mine
    finally:
        if mine:
            try:
                os.remove(path)
            except OSError:
                pass


# ------------------------------- заголовок дока ------------------------------

def split_header(text):
    """Отделить заголовок-легенду («TRN LOG v1 | …», первая строка) от тела лога → (header, body).
    Заголовка нет → ('', text). Нужен, чтобы новые события ложились ПОД легенду, а не над ней,
    и чтобы ротация не унесла легенду в архив первой же."""
    t = (text or "").replace("\r\n", "\n").lstrip("\n")
    if not t.startswith(HEADER_MARK):
        return "", (text or "").replace("\r\n", "\n")
    head, _, rest = t.partition("\n")
    return head.rstrip(), rest.lstrip("\n")


# ------------------------------- ротация -------------------------------------

def rotate(text, max_chars=None, keep_chars=None):
    """Разрезать лог по границе СТРОКИ: → (свежий_хвост, срез_в_архив).
    Порог не превышен → (text, '') — резать нечего. Режем ТОЛЬКО по «\\n», чтобы событие никогда
    не разрывалось пополам. Новые строки лежат СВЕРХУ (как в cowork_log), значит в архив уходит
    НИЗ — самые старые события."""
    max_chars = MAX_CHARS if max_chars is None else max_chars
    keep_chars = KEEP_CHARS if keep_chars is None else keep_chars
    t = text or ""
    if len(t) <= max_chars:
        return t, ""
    cut = t.rfind("\n", 0, max(0, keep_chars))
    if cut <= 0:                       # ни одной границы строки — не рвём событие, оставляем как есть
        return t, ""
    return t[:cut], t[cut + 1:]


# ------------------------------- публичная запись ----------------------------

def _to_spool(line, spool_path, why):
    """Недоставленную строку — в спул (статус доставки логируем ЧЕСТНО). → dict как у append."""
    if _spool_add(line, spool_path):
        log.warning("trainer_log: доставка не удалась (%s) — строка в спуле, дозапишу при "
                    "следующем успехе: %s", why, line[:160])
        return {"status": "spooled", "line": line}
    return {"status": "error", "line": line}


def append(n, kind, text, now=None, env=None, get=None, post=None, sidecar_path=None,
           lock_path=None, spool_path=None, retries=None, sleep=None):
    """Дописать ОДНО событие тренажёра в KB_trainer_log. → dict(status, line):
      status='ok'      — строка легла в док (вместе с ней дозаписан накопленный спул, если был);
      status='no_doc'  — file id ещё не задан (Штаб не завёл док) — НЕ ошибка, просто не пишем;
      status='spooled' — канал не ответил после ретраев; строка СОХРАНЕНА в локальный спул и
                         дозапишется при следующем успешном append (событие НЕ потеряно);
      status='error'   — не удалась даже запись в спул (тренажёр при этом жив).
    Новое событие идёт ПЕРВЫМ (свежее сверху) — тем же порядком, что cowork_log, но ПОД
    заголовком-легендой дока, если он есть. read-modify-write закрыт файловым локом (два
    процесса пишут в один док); спул трогаем под тем же локом. retries/sleep — тестовые инъекции."""
    line = format_line(n, kind, text, now)
    # Тестовый прогон на БОЕВОМ транспорте — в живой док мозга не пишем НИКОГДА (см. in_test_context).
    if get is None and post is None and in_test_context():
        log.info("trainer_log: тестовый контекст — в живой док не пишу: %s", line[:160])
        return {"status": "test", "line": line}
    did = doc_id(sidecar_path)
    if not did:
        log.info("trainer_log: doc_id не задан — событие не записано: %s", line[:160])
        return {"status": "no_doc", "line": line}
    env = env if env is not None else _env_file()
    # Читаем и пишем ПОД ЛОКОМ: между read и write не должен влезть второй процесс (иначе его
    # строка исчезнет вместе с нашей перезаписью целого дока).
    with doc_lock(lock_path):
        old = _retry(lambda: read_doc(did, env, get), lambda r: r is None, "read_doc",
                     retries, sleep)
        if old is None:
            return _to_spool(line, spool_path, "канал недоступен на чтении")
        pending = _spool_read(spool_path)          # недоставленное ранее — дозаписываем СЕЙЧАС
        head, body = split_header(old)             # легенда дока остаётся ПЕРВОЙ строкой
        stack = [line] + list(reversed(pending))   # свежее сверху; спул под новой строкой
        merged = "\n".join(stack + ([body] if body.strip() else []))
        fresh, spill = rotate(merged)
        if spill:
            aid = archive_id(sidecar_path)
            if aid:
                prev = _retry(lambda: read_doc(aid, env, get), lambda r: r is None,
                              "read_doc(архив)", retries, sleep) or ""
                if _retry(lambda: write_doc(aid, (spill + "\n" + prev).strip(), env, post),
                          lambda r: not r, "write_doc(архив)", retries, sleep):
                    log.warning("trainer_log: ротация — %s символов срезано в архив", len(spill))
                else:
                    fresh, spill = merged, ""      # архив не принял → НИЧЕГО не теряем
            else:
                log.warning("trainer_log: лог перерос %s символов, но archive_id не задан — "
                            "срез НЕ делаю (историю молча не теряем)", MAX_CHARS)
                fresh = merged
        out = (head + "\n" + fresh) if head else fresh
        if not _retry(lambda: write_doc(did, out, env, post), lambda r: not r, "write_doc",
                      retries, sleep):
            return _to_spool(line, spool_path, "канал недоступен на записи")
        if pending:
            _spool_clear(spool_path)
            log.info("trainer_log: доставлено ok — строка + %s из спула (спул очищен)", len(pending))
        else:
            log.info("trainer_log: доставлено ok: %s", line[:120])
        return {"status": "ok", "line": line, "spool_delivered": len(pending)}


def safe_append(n, kind, text, **kw):
    """Обёртка для боевых точек вызова: НИКОГДА не бросает и не блокирует.
    → status-строка: 'ok' | 'no_doc' | 'spooled' | 'error' | 'test' (статус доставки —
    он же уже залогирован внутри append)."""
    try:
        return append(n, kind, text, **kw).get("status", "error")
    except Exception as e:                                  # pragma: no cover — тотальный fail-safe
        log.warning("trainer_log: запись упала целиком: %s: %s", type(e).__name__, e)
        return "error"
