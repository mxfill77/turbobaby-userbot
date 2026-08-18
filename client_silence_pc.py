#!/usr/bin/env python3
"""ГЛАЗ ОЖИДАНИЯ О5 — «КЛИЕНТАМ НЕ УХОДИТ НИЧЕГО». ОДИН источник истины, ТОЛЬКО чтение (17.08.2026).

Предмет ожидания — РЕЗУЛЬТАТ, а не флаг: СКОЛЬКО сообщений ушло клиентам. Основание —
`docs/artifacts/2026-08-17-bot-output-path.md` (коммит aeea8ef): за всю жизнь контура
(03.07–17.08.2026, 470 черновиков) клиентам ушло НОЛЬ, но держится это значением в окружении
(`SUGGEST_TEST_MODE`), у которого ДЕФОЛТ В КОДЕ ОБРАТНЫЙ (`False` = «отправлять»). Пропадёт
значение — оба замка откроются МОЛЧА, без ошибки и без строки в логе. Прибор смотрит не на замки,
а на их итог.

ПОЧЕМУ ОТДЕЛЬНЫЙ ФАЙЛ, А НЕ ВЕТКА В РУКАХ СЛОЯ. Инвариант слоя
(`test_expectations_pc.TestHandsHaveNoTeeth`) ЗАПРЕЩАЕТ рукам импорт `sqlite3`, и запрет этот не
формальность: О3 судит модербота по mtime ЭТОЙ ЖЕ базы и НАМЕРЕННО её не открывает («наблюдатель
не смеет соперничать с боевым писателем за замок файла»). О5 без открытия базы невозможен —
предмет здесь ЧИСЛО, а не возраст файла. Поэтому запрет рукам оставлен БАЙТ В БАЙТ, а чтение
вынесено сюда: в единственный файл слоя, у которого есть `sqlite3`, и у которого за это отобрано
всё остальное. Разрешение получено СУЖЕНИЕМ замка, а не его ослаблением, и сужение проверяется
кодом (`test_expectations_pc.TestO5EyeIsReadOnly`), а не обещанием этой шапки:
  · КАЖДОЕ соединение открывается ТОЛЬКО через `ro_uri()` — то есть URI-режимом `mode=ro`;
  · ни в одном SQL модуля нет глагола записи (INSERT/UPDATE/DELETE/DROP/CREATE/REPLACE/ALTER);
  · руки слоя по-прежнему `sqlite3` не импортируют — прежний замок цел.

ЧТО СЧИТАЕТСЯ ОТПРАВЛЕННЫМ, И ПОЧЕМУ ИМЕННО ЭТО (сверено по `moderation_ipc`/`suggest`, а не по
названиям): `sent` ставит РОВНО ОДНА строка кода — `moderation_ipc.mark(id, "sent")` в
`suggest.poll_and_send` (`suggest.py:6893`), и ставится она ТОЛЬКО когда `send_to_client` вернул
успех. Это и есть счётчик ОТПРАВЛЕННЫХ. Рядом живут два числа, которые отправкой НЕ являются и
вердикта не рождают, но в отчёт идут, потому что означают «замки поехали»:
  · `failed`  — попытка отправки БЫЛА и не удалась (второй замок удержал, первый — нет);
  · `ready`   — строка ВЗВЕДЕНА: единственный вход в отправку, опрос забирает её раз в 3 с.

ВТОРОЙ СВИДЕТЕЛЬ — И ОН ЗАВЕДЁН НЕ ДЛЯ КРАСОТЫ, А ПО ИЗМЕРЕННОЙ ДЫРЕ. У единственного
клиентского выхода `suggest.send_to_client` ДВА вызывающих, и второй — `on_moderation_reply`
(`suggest.py:7177`, reply-режим, деградация при неподнятом модерботе) — в `moderation_ipc.db`
НЕ ПИШЕТ ВОВСЕ: черновик живёт у него в памяти процесса (`pending`), а итог отправки уходит
только в реестр обучения `suggest_pairs.jsonl` (`record_pair`, зовётся на КАЖДУЮ попытку обеими
ветками). Прибор, читающий одну базу, к этой ветке СЛЕП — поэтому читается и реестр.

ПРАВИЛО ВТОРОГО СВИДЕТЕЛЯ — ОДНОСТОРОННЕЕ (монотонность громкости): он умеет ТОЛЬКО добавить
нарушение и НИКОГДА не снять его и не превратить вердикт в «неизвестно». Непрочитанный реестр
оставляет в силе вердикт базы и НАЗЫВАЕТСЯ отдельной строкой в отчёте — «тихо» с непрочитанным
вторым свидетелем и «тихо» с прочитанным различимы всегда.

НАРУЖУ УЕЗЖАЮТ ТОЛЬКО ЧИСЛА И ВРЕМЕНА. Ни одна ветка этого модуля не возвращает тело сообщения,
`client_id`, `client_ref`, `draft` и `final_text` — у прибора для них нет ни поля, ни SQL-колонки.

ИЗОЛЯЦИЯ: под `TESTING=1` боевая база НЕ ЧИТАЕТСЯ (зеркало тривайра `moderation_ipc._conn`).
Отличие сознательное — здесь это НЕ исключение, а честный отказ `ok=False` с причиной: у слоя
ожиданий непрочитанный источник даёт исход «НЕИЗВЕСТНО», и он громче тихого падения.

ВТОРОЙ ПРЕДМЕТ ЭТОГО ГЛАЗА (18.08.2026) — СОБСТВЕННЫЙ ПРОДУКТ МОДЕРБОТА, ключ `meta['heartbeat']`.
Он живёт здесь не по родству тем, а по замку: файл слоя с `sqlite3` ровно один, и второй заводить
значило бы ослабить инвариант вместо того, чтобы им пользоваться. Соединение по-прежнему ОДНО на
весь модуль (`_ro_conn`) — это проверяется `ast`-ом, а не обещанием.

ЗАЧЕМ КЛЮЧ, ЕСЛИ ЕСТЬ mtime ФАЙЛА. Затем, что файл ОБЩИЙ: в `moderation_ipc.db` пишут трое —
модербот тиком (5 с), userbot поллером черновиков (3 с) и тренажёр сессией. «Файл шевельнулся»
означает «кто-то писал», и на этом наблюдение за модерботом 18.08 и сломалось. Ключ пишет РОВНО
ОДНА строка кода (`moderation_ipc.heartbeat` ← `moderation_bot.job_heartbeat`), поэтому его
значение — подпись автора, а не след соседа. Значение уезжает наружу СЫРОЙ СТРОКОЙ: разбор времени
живёт в решении (`expectations_pc.parse_iso`), у которого он уже есть и уже проверен.

КОНТРОЛЬНАЯ СУММА ДО И ПОСЛЕ — свидетель, а НЕ доказательство, и разница названа честно. У файла
живой писатель с тиком 5 с, поэтому расхождение хешей не различает «подвинули мы» и «подвинул он»:
совпали — доказано, что за наше окно файл не менялся вовсе; разошлись — `sha_same=False`, и
доказательством чтения-только служит НЕ хеш, а режим `mode=ro`, которым SQLite отбивает любой
глагол записи на уровне ОС (живой отказ — в тесте `TestO5EyeIsReadOnly`).
"""
import hashlib
import json
import os
import sqlite3

REPO = os.path.dirname(os.path.abspath(__file__))
PROD_DB = os.path.join(REPO, "moderation_ipc.db")            # источник истины О5
PROD_PAIRS = os.path.join(REPO, "suggest_pairs.jsonl")       # второй свидетель (reply-режим)

# Статус черновика, означающий ФАКТ ОТПРАВКИ КЛИЕНТУ. Кортеж, а не строка: если контур когда-нибудь
# заведёт второе имя успеха, прибор обязан расширяться данными, а не переписыванием ветки.
SENT_STATUSES = ("sent",)
ATTEMPT_STATUSES = ("failed",)      # попытка была, не дошло — вердикта не рождает, в отчёт идёт
ARMED_STATUSES = ("ready",)         # взведено: единственный вход в отправку

# Соединение читателя не смеет ЖДАТЬ: боевой писатель тикает раз в 5 с, и наблюдателю дешевле
# честное «не прочитал» (→ НЕИЗВЕСТНО), чем секунды в очереди за замком.
READ_TIMEOUT_SEC = 2.0
# ЖИВОЙ ФОРМАТ реестра, снятый с кода писателя: `json.dumps(rec, ensure_ascii=False)` разделяет
# ключ и значение как «": "», а `True` печатает как `true`. Идеализированного «"sent":true» здесь
# нет намеренно — фикстура обязана копировать прод, а не схему.
PAIRS_SENT_MARK = '"sent": true'
PAIRS_MAX_BYTES = 8 * 1024 * 1024   # потолок чтения реестра: выше — «не опрошен», а не полчаса I/O
# Имя ключа СОБСТВЕННОГО продукта модербота. Держится копией (наблюдатель не импортирует
# наблюдаемого) и закреплён тестом, читающим `moderation_ipc.heartbeat` исходником: переименуют
# ключ — обязано ПОКРАСНЕТЬ здесь, а не молча превратиться в вечное «неизвестно».
MOD_HB_KEY = "heartbeat"
SHA_CHUNK = 1024 * 1024


def ro_uri(path):
    """Путь → URI ЕДИНСТВЕННОГО законного режима открытия. `mode=ro` запрещает запись на уровне
    ОС (файл открывается O_RDONLY), а не договорённостью: любой глагол записи через такое
    соединение отбивается самим SQLite. Проверено живым отказом, см. тест `TestO5EyeIsReadOnly`.

    `immutable=1` здесь ЗАПРЕЩЁН и не является ускорением: он велит SQLite считать файл
    неизменным и разрешает отдать УСТАРЕВШИЕ страницы, а у нас под руками живой писатель — прибор
    прочитал бы прошлое и назвал его настоящим."""
    return "file:" + os.path.abspath(path).replace("\\", "/") + "?mode=ro"


def isolation_block(path):
    """Причина, по которой читать НЕЛЬЗЯ | None. Зеркало тривайра `moderation_ipc._conn`: под
    `TESTING=1` боевая очередь недоступна по построению, чтобы гейт не читал живую базу и не
    выдавал её числа за фикстуру. На чужие (временные) пути запрет не распространяется."""
    if os.environ.get("TESTING", "").strip() != "1":
        return None
    if os.path.abspath(path) != os.path.abspath(PROD_DB):
        return None
    return "изоляция тестов (TESTING=1): боевая moderation_ipc.db не читается"


def _ro_conn(path):
    """ЕДИНСТВЕННОЕ место всего слоя, где база вообще открывается. Отдельной функцией — не ради
    краткости: тест считает вызовы `connect` `ast`-ом и требует, чтобы он был ровно один. Второй
    предмет глаза (ключ модербота) обязан ходить ТОЙ ЖЕ дорогой, а не завести себе свою."""
    return sqlite3.connect(ro_uri(path), uri=True, timeout=READ_TIMEOUT_SEC)


def sha256_file(path):
    """Контрольная сумма файла | None. Кусками: база боевая и полтора мегабайта весит."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(SHA_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def db_counts(db=None):
    """ИСТОЧНИК ИСТИНЫ → числа. {"ok","err","db","total","sent","attempted","armed","statuses",
    "last_sent"}.

    Один запрос и ни одной колонки с телом: `status`, счётчик и МАКСИМУМ времени правки. `ok=False`
    на каждую дырку (изоляция · файла нет · sqlite отказал · таблицы `drafts` нет) — и это
    «НЕИЗВЕСТНО» решения, а не «отправок нет»: не прочитать и прочитать ноль здесь разные новости.
    """
    path = PROD_DB if db is None else db
    out = {"ok": False, "err": "", "db": path, "total": 0, "sent": 0, "attempted": 0,
           "armed": 0, "statuses": {}, "last_sent": None}
    blocked = isolation_block(path)
    if blocked is not None:
        out["err"] = blocked
        return out
    if not os.path.isfile(path):
        out["err"] = "файла источника нет: %s" % os.path.basename(path)
        return out
    con = None
    try:
        con = _ro_conn(path)
        rows = con.execute("SELECT status, COUNT(*), MAX(updated_ts) "
                           "FROM drafts GROUP BY status").fetchall()
    except sqlite3.Error as e:
        out["err"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        return out
    finally:
        if con is not None:
            con.close()
    for status, count, last in rows:
        name = "" if status is None else str(status)
        num = int(count)
        out["statuses"][name] = num
        out["total"] += num
        if name in SENT_STATUSES:
            out["sent"] += num
            out["last_sent"] = _later(out["last_sent"], last)
        if name in ATTEMPT_STATUSES:
            out["attempted"] += num
        if name in ARMED_STATUSES:
            out["armed"] += num
    out["ok"] = True
    return out


def _later(current, candidate):
    """Позднейшее из двух времён очереди. Формат один и тот же ISO, поэтому сравнение строк
    законно; `None` — честное «времени нет», и оно не притворяется нулём."""
    if candidate is None:
        return current
    text = str(candidate)
    if current is None:
        return text
    return max(current, text)


def _pair_ts(line, current):
    """Время попытки из строки реестра. НАРУЖУ УЕЗЖАЕТ ТОЛЬКО `ts`: тело сообщения, `client`,
    `draft` и `final_sent` эта функция не возвращает ни одной ветвью. Нечитаемая строка оставляет
    прошлое значение — счётчик уже увеличен вызывающим, и потеря времени не отменяет факта."""
    try:
        rec = json.loads(line)
    except (ValueError, TypeError):
        return current
    if not isinstance(rec, dict):
        return current
    return _later(current, rec.get("ts"))


def pairs_counts(pairs=None):
    """ВТОРОЙ СВИДЕТЕЛЬ → {"ok","err","path","sent","last","lines"}. Реестр попыток отправки,
    в который пишут ОБА вызывающих единственного клиентского выхода.

    ФАЙЛА НЕТ — это положительный факт, а не дырка: `record_pair` зовётся на КАЖДУЮ попытку
    (и удачную, и провальную), значит отсутствие файла означает «ни одной попытки не записано».
    Именно так он и выглядел на замере 17.08. Дырки называются иначе: размер не снят, файл выше
    потолка чтения, чтение сорвалось — там `ok=False`, и второй свидетель просто молчит."""
    path = PROD_PAIRS if pairs is None else pairs
    out = {"ok": False, "err": "", "path": path, "sent": 0, "last": None, "lines": 0}
    if not os.path.exists(path):
        out["ok"] = True
        out["err"] = "файла нет — ни одной попытки отправки не записано"
        return out
    try:
        size = os.path.getsize(path)
    except OSError as e:
        out["err"] = "размер реестра не снят: %s" % str(e)[:80]
        return out
    if size > PAIRS_MAX_BYTES:
        out["err"] = "реестр больше потолка чтения (%d Б) — второй свидетель не опрошен" % size
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                out["lines"] += 1
                if PAIRS_SENT_MARK not in line:
                    continue
                out["sent"] += 1
                out["last"] = _pair_ts(line, out["last"])
    except OSError as e:
        out["err"] = "реестр не прочитан: %s" % str(e)[:80]
        return out
    out["ok"] = True
    return out


# ═══════ СОБСТВЕННЫЙ ПРОДУКТ МОДЕРБОТА: КЛЮЧ В ОБЩЕЙ БАЗЕ, ЧИТАЕМЫЙ ТОЛЬКО НА ЧТЕНИЕ ═══════
def moderbot_heartbeat(db=None):
    """Ключ `meta['heartbeat']` → {"ok","err","db","raw","channel","sha_before","sha_after",
    "sha_same"}.

    ЧТО ЗДЕСЬ ПРЕДМЕТ, А ЧТО СВЕДЕНИЕ, И ЭТО ГЛАВНОЕ РАЗЛИЧЕНИЕ ФУНКЦИИ:
      · `raw` — ПРЕДМЕТ: подпись модербота собственным временем. Пишет её ровно одна строка кода,
        поэтому свежесть значения — его продукт, а не чей-то;
      · `channel` — mtime ОБЩЕГО файла. Это СВЕДЕНИЕ и только: писателей у файла трое, и ни одна
        ветка решения не имеет права построить на нём зелёное. Отдаём его, чтобы владелец видел
        обе величины разом (18.08: канал свеж 400 с, а модербота в системе уже нет).

    ВРЕМЯ ЗДЕСЬ НЕ РАЗБИРАЕТСЯ НАМЕРЕННО: разбор ISO живёт в решении (`expectations_pc.parse_iso`),
    у которого он уже есть, уже знает оба живых формата и уже проверен. Глаз отдаёт строку.

    ЧТЕНИЕ И НИЧЕГО КРОМЕ: `mode=ro` (запись отбивает SQLite), контрольная сумма снимается ДО и
    ПОСЛЕ. `sha_same=True` — доказано, что файл за наше окно не менялся вовсе; `False` — за окно
    его подвинули, и это не различает нас и боевого писателя (тик 5 с), поэтому доказательством
    служит режим, а не хеш; `None` — сумму снять не удалось.

    Каждая дырка (изоляция · файла нет · sqlite отказал · таблицы `meta` нет · ключа нет) даёт
    `ok=False` с НАЗВАННОЙ причиной, то есть исход «неизвестно» у решения, а не «работы нет».
    """
    path = PROD_DB if db is None else db
    out = {"ok": False, "err": "", "db": path, "raw": None, "channel": None,
           "sha_before": None, "sha_after": None, "sha_same": None}
    blocked = isolation_block(path)
    if blocked is not None:
        out["err"] = blocked
        return out
    if not os.path.isfile(path):
        out["err"] = "файла источника нет: %s" % os.path.basename(path)
        return out
    try:
        out["channel"] = os.stat(path).st_mtime
    except OSError as e:
        out["err"] = "mtime общего канала не снят: %s" % str(e)[:60]      # сведение, не предмет
    out["sha_before"] = sha256_file(path)
    con, row, err = None, None, ""
    try:
        con = _ro_conn(path)
        row = con.execute("SELECT v FROM meta WHERE k = ?", (MOD_HB_KEY,)).fetchone()
    except sqlite3.Error as e:
        err = "%s: %s" % (type(e).__name__, str(e)[:120])
    finally:
        if con is not None:
            con.close()
    out["sha_after"] = sha256_file(path)
    if out["sha_before"] is not None and out["sha_after"] is not None:
        out["sha_same"] = out["sha_before"] == out["sha_after"]
    if err:
        out["err"] = err
        return out
    if row is None:
        out["err"] = "ключа «%s» в meta нет" % MOD_HB_KEY
        return out
    out["raw"] = "" if row[0] is None else str(row[0])
    if not out["raw"]:
        out["err"] = "ключ «%s» пуст" % MOD_HB_KEY
        return out
    out["ok"] = True
    return out


def client_facts(db=None, pairs=None):
    """ФАКТ О5 для слоя ожиданий: числа обоих свидетелей в одном словаре. Решения здесь нет —
    его выносит `expectations_pc.client_state` (чистая функция), как и у всех прочих ожиданий."""
    primary = db_counts(db)
    return {"ok": primary["ok"], "err": primary["err"], "db": primary["db"],
            "sent": primary["sent"], "attempted": primary["attempted"],
            "armed": primary["armed"], "total": primary["total"],
            "statuses": primary["statuses"], "last_sent": primary["last_sent"],
            "pairs": pairs_counts(pairs)}


def main():
    """Ручная проба источника истины. Печатает ТОЛЬКО числа и времена — ни одного тела."""
    f = client_facts()
    print("источник: %s" % f["db"])
    print("прочитан: %s%s" % ("да" if f["ok"] else "НЕТ", "" if f["err"] == "" else " (%s)" % f["err"]))
    print("ОТПРАВЛЕНО КЛИЕНТУ: %d (последнее: %s)" % (f["sent"], f["last_sent"]))
    print("попыток без доставки: %d · взведено к отправке: %d · всего черновиков: %d"
          % (f["attempted"], f["armed"], f["total"]))
    print("разрез по статусам: %s" % json.dumps(f["statuses"], ensure_ascii=False, sort_keys=True))
    p = f["pairs"]
    print("второй свидетель (%s): прочитан=%s строк=%d, отправок=%d, последняя=%s%s"
          % (os.path.basename(p["path"]), "да" if p["ok"] else "НЕТ", p["lines"], p["sent"],
             p["last"], "" if p["err"] == "" else " (%s)" % p["err"]))
    hb = moderbot_heartbeat()
    print("СВОЙ продукт модербота (meta['%s']): %s%s"
          % (MOD_HB_KEY, hb["raw"] if hb["ok"] else "НЕ ПРОЧИТАН",
             "" if hb["err"] == "" else " (%s)" % hb["err"]))
    print("контрольная сумма файла до и после чтения: %s (общий канал — сведение, mtime %s)"
          % ({True: "СОВПАЛА — файл не тронут", False: "разошлась — файл подвинул боевой писатель "
              "(тик 5 с); чтение-только доказывает mode=ro, а не хеш"}.get(hb["sha_same"],
                                                                           "снять не удалось"),
             hb["channel"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
