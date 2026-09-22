# -*- coding: utf-8 -*-
u"""vps_token_install.py — ОДНА КОМАНДА ВЛАДЕЛЬЦА: новый годовой токен основной учётки уезжает в
файл окружения серверного демона, демон поднимается, живая проба называет исход словом, любой
отказ откатывается копией (заведён 23.09.2026, задание Штаба `0018s-71s.2209`).

ЗАЧЕМ. Серверная полоса стои́т с 22.09: действующий вход (слот B) уткнулся в недельный лимит до
27.09 09:00 UTC, запасной (слот A) отвечает 401. Починка известна поимённо
(`docs/artifacts/2026-09-22-71r-TOKENOSNOVNOY-2209.md`, §5), но состоит из трёх шагов на чужой
машине, два из которых — красные операции с секретным файлом. Здесь эти два шага сведены в ОДИН
запуск: владелец выпускает токен сам (`claude setup-token` требует настоящего TTY и его согласия
в браузере — обойти нечем), а всё остальное делает код.

ЧТО ЭТОТ МОДУЛЬ ЗНАЕТ ПРО СЕРВЕР, И ОТКУДА (замеры заходов 71p/71r, 22.09.2026):
  • файл окружения демона на 22.09 — `/root/.config/claude-executor.env` (600, root, 374 байта),
    и приезжает он в юнит строкой `EnvironmentFile=` из
    `/etc/systemd/system/orchestrator-daemon.service.d/10-oauth-token.conf:4`;
  • имён в нём три: действующее и два слота повтора;
  • писать НАДО В ДВА: `active_slot()` (`limit_slot.py:129-137`) ищет слот РАВЕНСТВОМ ЗНАЧЕНИЙ,
    и если действующее не совпало ни с A, ни с B, `plan()` отдаёт `ACT_UNKNOWN` — повтора не
    будет вовсе. «Вписать только в действующее» тихо убивает механизм подмены слота;
  • СЛОТ B НЕ ТРОГАЕМ НИ ОДНОЙ ВЕТКОЙ. Он исправен и принадлежит второй учётке — после 27.09
    09:00 UTC это честный запасной: упор основной уведёт повтор на вторую, а не на ту же самую.
    Имени слота B в списке записи нет, и это держит тест;
  • рестарт обязателен: systemd читает `EnvironmentFile` только при старте, а демон отдаёт
    ребёнку КОПИЮ своего окружения (`orchestrator_daemon.py:1484`, `:2098`) и файл не перечитывает.

ПУТЬ К ФАЙЛУ ОКРУЖЕНИЯ ЗАШИТ НЕ БУДЕТ, И ЭТО НЕ МЕЛОЧЬ. Абзац выше — СНИМОК чужой полосы на
22.09.2026; зашей его строкой в код, и он станет второй копией факта, которая отстаёт МОЛЧА:
переедет drop-in — инструмент уверенно поправит файл, которого демон не читает, и доложит
«вписано». Поэтому адрес спрашивается у того, кто им распоряжается: `systemctl show <юнит>
-p EnvironmentFiles`, разбор — чистой функцией `parse_env_files`. Файлов названо не ровно один
(ноль или несколько) — это НАЗВАННЫЙ ОТКАЗ, а не повод выбрать любой. Побочно это и снимает
законное красное гарда ПК: путь секретного файла строковой константой кода он считает
обращением к секрету (`pretool_guard._RE_ENV` + `_py_env_readonly`), и по существу он прав.

СЕКРЕТ НЕ ВЫХОДИТ НАРУЖУ НИ ОДНИМ КАНАЛОМ, и каждый канал закрыт отдельно:
  • ввод — `getpass` (эха нет, в историю команд не попадает, значением аргумента не бывает);
  • argv — значение НЕ передаётся ни удалённой команде, ни `ps` (ни здесь, ни на сервере):
    программа для сервера уезжает в `stdin` ssh base64-блобом, и значение живёт литералом
    ВНУТРИ неё, то есть только в памяти удалённого python;
  • диск ПК — значение не пишется никуда вовсе; временного файла с ним нет ни на одной стороне;
  • печать — наружу идут ТОЛЬКО признаки: сошлось/нет, число знаков, номер строки, имя
    переменной, размер файла. Ни одна ветка не печатает значение, и это держит тест;
  • ИСХОДНИК — в файле нет ни одной строки вида `ИМЯ=<длинное смешанное значение>`: синтетика
    отрицательного теста собирается из кусков на ходу. Форма выноса секрета красна сама по себе,
    и выдуманное значение в ней неотличимо от настоящего — ни гардом, ни человеком.

ТРИ ИСХОДА, А НЕ ДВА. «ssh не поднялся», «юнита нет» и «файл окружения не назван юнитом» — это
ДОКАЗАННЫЙ ОТКАЗ с названной причиной (законная ветвь по заданию), а не «не получилось» и не
повод продолжать вслепую: при них у владельца не просят даже вставить значение. Зелёным
считается ТОЛЬКО ответ поставщика без ошибки; `429` и `401` — оба отказ, и они разные («вход
принят, лимит кончился» против «вход не принят»).

ОТКАТ ВСТРОЕН И НЕОТКЛЮЧАЕМ. Копия боевого файла делается РЯДОМ с ним (`…bak-ГГГГММДД-ЧЧММССZ`,
права 600) ДО единственной записи, сверяется с оригиналом побайтно и НЕ УДАЛЯЕТСЯ НИКОГДА — ни
при успехе, ни при откате. Любой отказ на шагах «вписать → перезапустить → проба» возвращает файл
из копии и говорит об этом вслух; если демон к тому моменту уже поднялся с новым значением, он
поднимается ещё раз — иначе работающий процесс разошёлся бы с файлом на диске.

ЗАПУСК (боевой — только рукой владельца, у которого есть TTY):
    venv\\Scripts\\python.exe vps_token_install.py            # полный ход
    venv\\Scripts\\python.exe vps_token_install.py --check     # только разведка сервера, без ввода
    venv\\Scripts\\python.exe vps_token_install.py --dry       # ввод и проверка формы, сервер не правим
    venv\\Scripts\\python.exe vps_token_install.py --selftest  # отрицательный тест НА КОПИИ, без сети
Ключ `--force` пропускает вопрос при идущей на сервере задаче; `--keep-nongreen` оставляет новый
токен, когда проба ответила `429` (лимит — не порча токена), вместо отката по умолчанию.

ЧЕГО МОДУЛЬ НЕ УМЕЕТ ПО УСТРОЙСТВУ: выпускать токен (нужен TTY и согласие владельца в браузере);
читать значения с сервера наружу; трогать слот B; удалять хоть что-нибудь (слов `remove`/`unlink`/
`rmtree` в файле нет); ходить в сеть в режимах `--selftest` и `--check` без запроса значения.
"""
import argparse
import getpass
import hashlib
import inspect
import io
import json
import os
import re
import subprocess
import time

import io_utf8
import srv_delivery                      # ssh-таймауты и путь к ключу — оттуда, чтобы канон жил в одном месте

REPO = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ── адреса. Не секреты: IP уже стои́т литералом в `pretool_guard._SSH_OWN_EXTRA`, имя юнита —
# имя службы. Переопределяются окружением, чтобы вторая машина не правила код. Путь к файлу
# окружения здесь НЕ ЖИВЁТ СОЗНАТЕЛЬНО (см. шапку): его называет сам юнит.
SRV_HOST = os.environ.get("VPS_TOKEN_HOST", "root@5.223.94.179")
UNIT = os.environ.get("VPS_TOKEN_UNIT", "orchestrator-daemon")
CLAUDE_BIN = os.environ.get("VPS_TOKEN_CLAUDE_BIN", "claude")
PROBE_MODEL = os.environ.get("VPS_TOKEN_PROBE_MODEL", "claude-opus-5")
# Ручка на случай, когда юнит называет несколько файлов или не называет ни одного: тогда адрес
# даёт человек, а не догадка кода.
ENV_PATH_FORCED = os.environ.get("VPS_TOKEN_ENV_PATH", "")

# Имена переменных окружения (имена, не значения). Порядок не косметика: действующее ПЕРВЫМ — если
# запись оборвётся между именами, полоса останется с рабочим входом, а не со слотом, который
# никто не читает первым заходом.
NAME_ACTIVE = "CLAUDE_CODE_OAUTH_TOKEN"
NAME_SLOT_A = "TB_CLAUDE_TOKEN_A"
NAME_SLOT_B = "TB_CLAUDE_TOKEN_B"        # НЕ ПИШЕМ. Живёт здесь только как имя в запрете
WRITE_NAMES = (NAME_ACTIVE, NAME_SLOT_A)

# Форма годового токена. 108 — ЗАМЕР (артефакт 2026-07-28-vps-annual-token.md, повторён 71p §5),
# а не догадка. Пол 90 выбран так, чтобы известная порча «перенос строки в окне 80 колонок»
# (28.07: строка рвалась на 79 знаках и молча не работала) отбивалась с запасом; потолок 200 —
# на случай, если поставщик удлинит формат: чужой длинный токен лучше проверить пробой, чем
# отбить на пороге, которого никто не мерил.
TOKEN_PREFIX = "sk-ant-oat01"
LEN_REF, LEN_MIN, LEN_MAX = 108, 90, 200
# Разрешённые знаки. Всё, что не отсюда (пробел, перенос, кавычка, `$`, обратная косая, кириллица),
# ломало бы либо строку `имя=значение`, либо её чтение `set -a; . файл` — и ломало бы МОЛЧА.
_BODY_RE = re.compile(r"^[A-Za-z0-9._=-]+$")

# Исходы живой пробы. Словами, как просит задание.
GREEN, LIMIT, DENIED, OTHER = u"зелено", u"429 лимит", u"401 вход не принят", u"иное"

SSH_SHORT_TIMEOUT = int(os.environ.get("VPS_TOKEN_SSH_TIMEOUT", "90"))
SSH_PROBE_TIMEOUT = int(os.environ.get("VPS_TOKEN_PROBE_TIMEOUT", "300"))
SSH_RESTART_TIMEOUT = int(os.environ.get("VPS_TOKEN_RESTART_TIMEOUT", "180"))

# Временное место ПК названо явно: сюда и только сюда пишет `--selftest`. Боевого значения тут
# не бывает никогда — отрицательный тест работает на СИНТЕТИЧЕСКОЙ копии окружения.
WORK = os.path.join(REPO, "tmp", "vps_token_install")


# ═══════════════════ чистая часть: факты → решение ═══════════════════
# Ни сети, ни диска, ни времени. Инвариант держит тест VPS_TOKEN_PURE — ровно как EXPECT_PC_PURE
# у слоя ожиданий: судья, умеющий ходить наружу, однажды соврёт молча.

def parse_env_files(show_out):
    u"""Вывод `systemctl show <юнит> -p EnvironmentFiles` → список путей (в порядке юнита).

    Форма живого ответа — `EnvironmentFiles=/путь (ignore_errors=no)`, файлов может быть
    несколько через пробел. Скобку с флагом отрезаем, пустой ответ (`EnvironmentFiles=`) даёт
    пустой список — юнит файлов не называет, и это ОТКАЗ на стороне вызывающего, а не здесь."""
    paths = []
    for line in (show_out or u"").splitlines():
        if not line.startswith("EnvironmentFile"):
            continue
        _k, _, val = line.partition("=")
        for chunk in val.split():
            chunk = chunk.strip()
            if not chunk or chunk.startswith("("):
                continue                   # `(ignore_errors=no)` — флаг систем-д, а не путь
            paths.append(chunk.split(" (")[0])
    return paths


def check_shape(value):
    u"""Проверка ФОРМЫ значения, НЕ ПЕЧАТАЯ значение. → {"ok", "reason", "n"}.

    `n` — число знаков, единственное, что уходит наружу от самого значения. Ни одна ветка не
    кладёт в ответ ни кусок значения, ни его хеш: отпечаток секрета — тоже секрет."""
    if value is None:
        return {"ok": False, "reason": u"значение не введено вовсе", "n": 0}
    n = len(value)
    if n == 0:
        return {"ok": False, "reason": u"пустой ввод: 0 знаков", "n": 0}
    if value.strip() != value:
        return {"ok": False, "reason": u"по краям пробелы/перенос — вставка захватила лишнее (%d знаков)" % n,
                "n": n}
    if not value.startswith(TOKEN_PREFIX):
        return {"ok": False, "reason": u"нет префикса годового токена `%s` (знаков %d): это не тот "
                                       u"вид ключа либо вставка началась не с начала" % (TOKEN_PREFIX, n),
                "n": n}
    if n < LEN_MIN:
        return {"ok": False, "reason": u"знаков %d, это меньше пола %d при образце %d — значение "
                                       u"обрезано при копировании (класс 28.07: перенос строки в "
                                       u"окне 80 колонок)" % (n, LEN_MIN, LEN_REF), "n": n}
    if n > LEN_MAX:
        return {"ok": False, "reason": u"знаков %d, это больше потолка %d при образце %d — вставка "
                                       u"захватила соседний текст" % (n, LEN_MAX, LEN_REF), "n": n}
    body = value[len(TOKEN_PREFIX):]
    if not _BODY_RE.match(body):
        bad = [i + 1 for i, ch in enumerate(value) if not _BODY_RE.match(ch)]
        return {"ok": False, "reason": u"посторонний знак в позиции %s (всего таких %d) — пробел, "
                                       u"перенос, кавычка или кириллица внутри значения ломают "
                                       u"строку окружения молча" % (bad[0] if bad else u"?", len(bad)),
                "n": n}
    if n != LEN_REF:
        return {"ok": True, "reason": u"форма годного токена, знаков %d (образец %d — расхождение "
                                      u"названо вслух, решает живая проба)" % (n, LEN_REF), "n": n}
    return {"ok": True, "reason": u"форма годного токена, знаков %d" % n, "n": n}


def env_rewrite(text, value, names):
    u"""Замена значения У НАЗВАННЫХ ИМЁН в тексте файла окружения. → (новый текст, отчёт).

    Отчёт — список `{"line", "name", "action", "old_len", "new_len"}`: номера, имена и ДЛИНЫ,
    никаких значений. Правая часть меняется ЦЕЛИКОМ (вместе с кавычками, если они были) — так
    прежняя форма записи не может подмешаться в новое значение.

    Имя, которого в файле нет, ДОПИСЫВАЕТСЯ в конец: отсутствующий слот A — ровно тот случай,
    ради которого правка и делается, и молча пропустить его значило бы оставить повтор мёртвым.
    Имена, не названные в `names` (в боевом ходу это слот B), не трогаются ни в одной ветке —
    их строки уезжают в вывод байт в байт.

    Регулярка живёт ВНУТРИ функции намеренно: исходник этой функции уезжает на сервер через
    `inspect.getsource`, и ссылка на модульную константу там не разрешилась бы."""
    import re as _re
    pat = _re.compile(r"^(\s*)(export\s+)?([A-Za-z_][A-Za-z0-9_]*)([ \t]*=)(.*)$")
    names = tuple(names)
    out, report, seen = [], [], set()
    for i, line in enumerate(text.splitlines(True), 1):
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        m = pat.match(body)
        if m and m.group(3) in names:
            name = m.group(3)
            out.append(m.group(1) + (m.group(2) or "") + name + m.group(4) + value + eol)
            report.append({"line": i, "name": name, "action": "replace",
                           "old_len": len(m.group(5)), "new_len": len(value)})
            seen.add(name)
        else:
            out.append(line)
    missing = [n for n in names if n not in seen]
    if missing and out and not out[-1].endswith(("\n", "\r")):
        out[-1] = out[-1] + "\n"           # файл без перевода строки в конце: иначе строки слиплись бы
    for name in missing:
        out.append(name + "=" + value + "\n")
        report.append({"line": len(out), "name": name, "action": "append",
                       "old_len": 0, "new_len": len(value)})
    return "".join(out), report


def plan_write(text, value, names=WRITE_NAMES):
    u"""Форма + правка ОДНОЙ дверью. → (ok, словами, новый текст, отчёт).

    Негодное значение до текста НЕ ДОХОДИТ: при отказе возвращается исходный текст тем же
    объектом, и записывать становится нечего. Это и меряет отрицательный тест."""
    shape = check_shape(value)
    if not shape["ok"]:
        return False, shape["reason"], text, []
    new_text, report = env_rewrite(text, value, names)
    if not report:
        return False, u"ни одна строка не изменена и не дописана — правка пуста, писать нечего", text, []
    if new_text == text:
        return False, u"новый текст побайтно равен прежнему — значение уже стои́т в файле", text, report
    return True, shape["reason"], new_text, report


def verdict_of_probe(rc, out):
    u"""Ответ дешёвого вызова → (слово исхода, пояснение). Чистая функция: судит ТЕКСТ, не сеть.

    МАШИННОЕ ПОЛЕ СТАРШЕ ТЕКСТА, и это не вкусовщина: строка «resets Sep 27, 9am (UTC)» живёт в
    транскриптах ПК 29 раз и ни разу как собственный отказ (артефакт 71r), поэтому поиск слов по
    тексту даёт ровно обратный вывод. Сначала `is_error`/`apiErrorStatus`, слова — только когда
    машинных полей нет вовсе."""
    text = out or u""
    data = None
    brace = text.find("{")
    if brace >= 0:
        try:
            data = json.loads(text[brace:])
        except Exception:
            data = None
    if isinstance(data, dict) and data.get("is_error") is False and rc == 0:
        return GREEN, u"поставщик ответил без ошибки (is_error=false, exit=0)"

    status = None
    if isinstance(data, dict):
        raw = data.get("apiErrorStatus")
        if isinstance(raw, (int, str)) and str(raw).isdigit():
            status = int(raw)
    if status is None:
        m = re.search(r'"apiErrorStatus"\s*:\s*"?(\d{3})', text)
        if m:
            status = int(m.group(1))
    if status == 429:
        return LIMIT, u"вход ПРИНЯТ, но недельный лимит учётки исчерпан (apiErrorStatus=429)"
    if status == 401:
        return DENIED, u"вход НЕ принят: поставщик отверг токен (apiErrorStatus=401)"
    if status is not None:
        return OTHER, u"apiErrorStatus=%d; ответ поставщика: %s" % (status, _squeeze(text))

    low = text.lower()
    if "429" in low or "rate_limit" in low or "weekly limit" in low or "usage limit" in low:
        return LIMIT, u"машинного поля нет, но текст поставщика говорит о лимите: %s" % _squeeze(text)
    if ("401" in low or "authenticate" in low or "invalid bearer token" in low
            or "access token is invalid" in low):
        return DENIED, u"машинного поля нет, но текст поставщика говорит об отказе входа: %s" % _squeeze(text)
    if rc == 0 and isinstance(data, dict) and data.get("is_error") is False:
        return GREEN, u"поставщик ответил без ошибки (is_error=false)"
    return OTHER, u"exit=%s; ответ поставщика: %s" % (rc, _squeeze(text))


def _squeeze(text, limit=400):
    u"""Текст поставщика в одну строку и под потолок. Секретов в нём нет: конверт CLI значения
    входа не содержит (замер 71r, три конверта в `tmp/vps_token_switch/`)."""
    one = u" ".join((text or u"").split())
    return one[:limit] + (u"…" if len(one) > limit else u"")


def backup_name(path, stamp):
    u"""Имя копии РЯДОМ с боевым файлом. Копия не удаляется никогда, поэтому имя обязано быть
    уникальным само по себе — метка времени в UTC до секунды."""
    return "%s.bak-%s" % (path, stamp)


# ═══════════════════ программы для сервера ═══════════════════
# Уезжают в stdin ssh base64-блобом: так значение не попадает ни в argv (видно в `ps` на ОБЕИХ
# сторонах), ни на диск, ни в лог оболочки. Удалённая команда при этом состоит из двух слов без
# единой кавычки — ей нечего поломать ни в разборе Windows, ни в разборе sh.

_REMOTE_BOOT = "base64 -d | python3"

_RS_PREFLIGHT = r'''
import os, sys, time
def out(k, v):
    sys.stdout.write("%s=%s\n" % (k, v))
out("host_ok", 1)
if not os.path.isfile(TARGET):
    out("file_exists", 0); sys.exit(0)    # НЕ ошибка канала: это законный отказ, судит ПК
st = os.stat(TARGET)
out("file_exists", 1)
out("file_bytes", st.st_size)
out("file_mode", oct(st.st_mode & 0o777))
out("file_mtime", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(st.st_mtime)))
# имена в файле — БЕЗ значений: номер строки, имя и ДЛИНА правой части, ничего больше
seen = []
try:
    fh = open(TARGET, "r")
    try:
        for i, line in enumerate(fh.read().splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            name, _, val = s.partition("=")
            seen.append("%d:%s:%d" % (i, name.replace("export ", "").strip(), len(val)))
    finally:
        fh.close()
except Exception as e:
    out("read_err", type(e).__name__)
out("names", ",".join(seen) if seen else "-")
# идёт ли прямо сейчас заход исполнителя: рестарт убил бы его вместе с cgroup демона
busy = 0
for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        fh = open("/proc/%s/cmdline" % pid, "rb")
        try:
            cmd = fh.read()
        finally:
            fh.close()
    except Exception:
        continue
    if b"claude" in cmd and b"base64" not in cmd:
        busy += 1
out("busy_claude", busy)
'''

_RS_APPLY = r'''
import hashlib, io, os, shutil, sys
def out(k, v):
    sys.stdout.write("%s=%s\n" % (k, v))
def sha(p):
    h = hashlib.sha256()
    fh = open(p, "rb")
    try:
        h.update(fh.read())
    finally:
        fh.close()
    return h.hexdigest()

if not os.path.isfile(TARGET):
    out("err", "file_missing"); sys.exit(3)

fh = io.open(TARGET, "r", encoding="utf-8", errors="strict", newline="")
try:
    before = fh.read()
finally:
    fh.close()
out("bytes_before", len(before.encode("utf-8")))

# ── копия РЯДОМ, до единственной записи. Занятое имя не перезаписываем никогда: старая копия
# дороже красивого имени, а удалять нам нельзя ни при какой ветке.
bak = BAK
n = 1
while os.path.exists(bak):
    n += 1
    bak = "%s-%d" % (BAK, n)
shutil.copy2(TARGET, bak)
os.chmod(bak, 0o600)
if sha(bak) != sha(TARGET):
    out("backup", bak); out("err", "backup_mismatch"); sys.exit(4)
out("backup", bak)
out("backup_ok", 1)

after, report = env_rewrite(before, VALUE, NAMES)
if not report:
    out("err", "no_change"); sys.exit(5)
for r in report:
    out("line", "%s:%s:%s:%s->%s" % (r["line"], r["name"], r["action"], r["old_len"], r["new_len"]))

# ── запись: временный файл с правами 600 и атомарная подмена. Мусора не остаётся, а оборванная
# запись не может оставить полуфайл на месте боевого.
tmp = TARGET + ".new"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
try:
    os.write(fd, after.encode("utf-8"))
finally:
    os.close(fd)
os.replace(tmp, TARGET)
os.chmod(TARGET, 0o600)

fh = io.open(TARGET, "r", encoding="utf-8", errors="strict", newline="")
try:
    back = fh.read()
finally:
    fh.close()
out("bytes_after", len(back.encode("utf-8")))
out("readback_ok", 1 if back == after else 0)

# ── обратная сверка ПО СМЫСЛУ, а не по факту записи: у каждого названного имени правая часть
# обязана стать ровно значением, а строки ПРОЧИХ имён — остаться байт в байт прежними.
ok_named, ok_others = 1, 1
b_lines, a_lines = before.splitlines(), back.splitlines()
for i, line in enumerate(a_lines):
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        continue
    name, _, val = s.partition("=")
    name = name.replace("export ", "").strip()
    if name in NAMES:
        if val != VALUE:
            ok_named = 0
    elif i < len(b_lines) and line != b_lines[i]:
        ok_others = 0
out("named_ok", ok_named)
out("others_untouched", ok_others)
out("done", 1 if (ok_named and ok_others and back == after) else 0)
'''

_RS_RESTORE = r'''
import hashlib, os, shutil, sys
def out(k, v):
    sys.stdout.write("%s=%s\n" % (k, v))
def sha(p):
    h = hashlib.sha256()
    fh = open(p, "rb")
    try:
        h.update(fh.read())
    finally:
        fh.close()
    return h.hexdigest()

if not os.path.isfile(BAK):
    out("err", "backup_missing"); sys.exit(6)
shutil.copy2(BAK, TARGET)        # копия остаётся на месте: возврат — это КОПИРОВАНИЕ, не перенос
os.chmod(TARGET, 0o600)
out("restored_ok", 1 if sha(BAK) == sha(TARGET) else 0)
out("bytes", os.stat(TARGET).st_size)
'''


def _remote_program(body, **consts):
    u"""Сборка программы для сервера: константы литералами + ИСХОДНИК чистой функции + тело.

    `env_rewrite` уезжает на сервер `inspect.getsource`'ом, а не второй копией: на ПК её меряет
    отрицательный тест, и меряет он ровно тот код, который правит боевой файл. Копия-близнец
    разошлась бы молча — ровно тот класс, которым живут архивные гарды этой полосы."""
    head = ["# -*- coding: utf-8 -*-"]
    for key in sorted(consts):
        head.append("%s = %r" % (key, consts[key]))
    head.append(inspect.getsource(env_rewrite))
    head.append(body)
    return "\n".join(head)


# ═══════════════════ руки: ssh, шаги, откат ═══════════════════

def ssh_run(remote_cmd, stdin_text=None, timeout=None):
    u"""ЕДИНСТВЕННАЯ дверь наружу. Всё, что уходит на сервер, идёт через неё — поэтому тест может
    подменить её целиком и увидеть КАЖДУЮ команду, которую модуль послал бы.

    Таймауты не украшение и не «подобраны»: `SSH_OPTS` взяты у `srv_delivery` (канон рамки,
    ConnectTimeout=10 + ServerAlive), а внешний `timeout` закрывает случай, когда сокет жив, а
    ответа нет. → (rc, out). `rc=255` — канал, а не сервер."""
    argv = ["ssh", "-i", srv_delivery.SSH_KEY] + list(srv_delivery.SSH_OPTS) + [SRV_HOST, remote_cmd]
    try:
        p = subprocess.run(argv,
                           input=(stdin_text.encode("utf-8") if stdin_text is not None else None),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout or SSH_SHORT_TIMEOUT, creationflags=NO_WINDOW)
        return p.returncode, (p.stdout or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 255, u"ssh не ответил за %d с" % (timeout or SSH_SHORT_TIMEOUT)
    except Exception as e:
        return 255, u"ssh не запустился: %s" % e


def run_remote_py(program, timeout=None):
    u"""Программа на сервер: base64 в stdin, два слова в команде. → (rc, поля k=v, сырой вывод)."""
    import base64
    blob = base64.b64encode(program.encode("utf-8")).decode("ascii")
    rc, out = ssh_run(_REMOTE_BOOT, stdin_text=blob, timeout=timeout)
    return rc, parse_kv(out), out


def parse_kv(out):
    u"""`k=v` строками → словарь. Повторяющийся ключ `line` копится списком `lines`."""
    fields = {}
    for line in (out or u"").splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k == "line":
            fields.setdefault("lines", []).append(v)
        fields[k] = v
    return fields


def find_env_path():
    u"""Адрес файла окружения — У ТОГО, КТО ИМ РАСПОРЯЖАЕТСЯ. → (путь|"", словами).

    Ровно один файл — берём его. Ноль или несколько — НАЗВАННЫЙ ОТКАЗ: угадывать, какой из
    нескольких читает демон, значило бы уверенно поправить не тот. Ручка `VPS_TOKEN_ENV_PATH`
    старше вывода: если человек назвал адрес сам, спорить не с чем."""
    if ENV_PATH_FORCED:
        return ENV_PATH_FORCED, u"адрес задан ручкой окружения VPS_TOKEN_ENV_PATH"
    rc, out = ssh_run("systemctl show %s -p EnvironmentFiles -p ActiveState -p MainPID" % UNIT)
    if rc == 255:
        return "", u"ssh до сервера не поднялся: %s" % _squeeze(out, 300)
    if rc != 0:
        return "", u"systemctl не ответил (юнита %s нет?): %s" % (UNIT, _squeeze(out, 300))
    paths = parse_env_files(out)
    if not paths:
        return "", (u"юнит %s не называет ни одного файла окружения (EnvironmentFile пуст) — "
                    u"вписывать некуда; адрес можно назвать руками через VPS_TOKEN_ENV_PATH" % UNIT)
    if len(paths) > 1:
        return "", (u"юнит называет %d файлов окружения (%s) — который читает демон, код не "
                    u"угадывает; назовите адрес через VPS_TOKEN_ENV_PATH" % (len(paths), u", ".join(paths)))
    return paths[0], u"адрес назван юнитом %s строкой EnvironmentFile" % UNIT


def preflight(target):
    u"""Разведка БЕЗ значения: есть ли файл, какие в нём имена, идёт ли заход. → (ok, факты, словами)."""
    rc, fields, raw = run_remote_py(_remote_program(_RS_PREFLIGHT, TARGET=target))
    if rc == 255 or not fields.get("host_ok"):
        return False, fields, u"ssh до сервера не поднялся: %s" % _squeeze(raw, 300)
    if fields.get("file_exists") != "1":
        return False, fields, u"файл окружения демона не найден по названному юнитом пути %s" % target
    return True, fields, u"канал жив, файл окружения на месте"


def apply_value(target, value, stamp):
    u"""Шаги в–г: копия рядом + запись в ДВА имени. → (ok, факты, словами)."""
    prog = _remote_program(_RS_APPLY, TARGET=target, NAMES=tuple(WRITE_NAMES), VALUE=value,
                           BAK=backup_name(target, stamp))
    rc, fields, raw = run_remote_py(prog)
    if rc == 255:
        return False, fields, u"канал оборвался на записи: %s" % _squeeze(raw, 300)
    if fields.get("err"):
        return False, fields, u"сервер отказал на записи: %s" % fields["err"]
    if fields.get("done") != "1":
        return False, fields, u"запись не подтвердилась обратным чтением: %s" % _squeeze(raw, 300)
    return True, fields, u"вписано и сверено обратным чтением"


def restart_unit():
    u"""Шаг д: перезапуск демона. → (ok, факты, словами). MainPID до/после судит вызывающий."""
    cmd = ("systemctl restart %s; systemctl show %s -p MainPID -p ActiveState -p SubState -p NRestarts"
           % (UNIT, UNIT))
    rc, out = ssh_run(cmd, timeout=SSH_RESTART_TIMEOUT)
    fields = parse_kv(out)
    if rc != 0 or fields.get("ActiveState") != "active":
        return False, fields, u"демон не поднялся: %s" % _squeeze(out, 300)
    return True, fields, u"демон активен, MainPID=%s, NRestarts=%s" % (
        fields.get("MainPID", "?"), fields.get("NRestarts", "?"))


def probe(target):
    u"""Шаг е: один дешёвый вызов ПОД НОВЫМ окружением. → (слово исхода, пояснение, сырой ответ).

    Окружение берётся тем же файлом, который читает systemd (`set -a; . файл`), а не из
    `/proc/<pid>/environ`: так проба меряет ровно то, с чем поднимется демон. Значение при этом
    не печатается ни здесь, ни на сервере — наружу идёт только конверт CLI."""
    cmd = ("set -a; . %s; set +a; %s -p 'Reply with exactly: OK' --model %s --output-format json"
           % (target, CLAUDE_BIN, PROBE_MODEL))
    rc, out = ssh_run(cmd, timeout=SSH_PROBE_TIMEOUT)
    word, why = verdict_of_probe(rc, out)
    return word, why, out


def restore(target, bak):
    u"""Шаг ж: вернуть файл из копии. Копия остаётся на диске. → (ok, факты, словами)."""
    rc, fields, raw = run_remote_py(_remote_program(_RS_RESTORE, TARGET=target, BAK=bak))
    if rc == 255 or fields.get("err") or fields.get("restored_ok") != "1":
        return False, fields, u"ОТКАТ НЕ СОСТОЯЛСЯ: %s" % _squeeze(raw, 300)
    return True, fields, u"файл возвращён из копии %s (копия на месте, не удалена)" % bak


# ═══════════════════ отрицательный тест на КОПИИ окружения ═══════════════════
# Ни одного обращения к серверу и ни одного касания боевого файла. Синтетика собирается ИЗ КУСКОВ
# на ходу: строка вида `имя=<длинное смешанное значение>` в исходнике — это форма выноса секрета,
# и выдуманное значение в ней неотличимо от настоящего.

def synth_value(tag):
    u"""Синтетическое значение ГОДНОЙ формы длиной ровно `LEN_REF`. Секретом не является."""
    room = LEN_REF - len(TOKEN_PREFIX) - 1
    return TOKEN_PREFIX + "-" + (tag * (room // len(tag) + 1))[:room]


def synth_env(names=(NAME_ACTIVE, NAME_SLOT_A, NAME_SLOT_B)):
    u"""Синтетическая КОПИЯ файла окружения: имена, комментарий, перевод строки в конце."""
    lines = [u"# синтетика для отрицательного теста, боевых значений здесь нет"]
    for i, name in enumerate(names):
        lines.append(name + "=" + synth_value("Zz%d" % (i + 1)))
    return u"\n".join(lines) + u"\n"


def bad_cases():
    u"""Негодные значения. Каждое — НАЗВАННЫЙ живой класс порчи, а не фантазия."""
    p = TOKEN_PREFIX
    return (
        (u"пусто", ""),
        (u"пробелы", "   "),
        (u"без префикса (мусор 71r)", "fake-" + "x" * 100),
        (u"платный ключ вместо годового", "sk-ant-api03-" + "y" * 95),
        (u"обрезка окном 80 колонок (класс 28.07)", p + "-" + "z" * 66),
        (u"длиннее потолка", p + "-" + "q" * 250),
        (u"перенос строки внутри", p + "-aaa\nbbb" + "c" * 90),
        (u"пробел внутри", p + "-aaa bbb" + "c" * 90),
        (u"кавычка внутри", p + '-aaa"bbb' + "c" * 90),
        (u"кириллица (мохибейк вставки)", p + u"-ааабббв" + "c" * 90),
        (u"краевой пробел вставки", " " + synth_value("Nn9")),
    )


def bad_envelopes():
    u"""Конверты пробы, на которых зеленеть НЕЛЬЗЯ. Значений входа в конвертах CLI не бывает."""
    return (
        (u"401 текстом", 1, '{"is_error":true,"result":"API Error: 401 OAuth access token is invalid."}'),
        (u"401 машинным полем", 1, '{"is_error":true,"apiErrorStatus":401,"result":"nope"}'),
        (u"429 недельный лимит", 1, '{"is_error":true,"apiErrorStatus":429,"result":"weekly limit"}'),
        (u"мусор вместо конверта", 255, "ssh: connect to host port 22: Connection refused"),
    )


def selftest():
    u"""Отрицательный тест числом. Меряется ровно тот код, который правит боевой файл
    (`plan_write` → `env_rewrite`, она же уезжает на сервер исходником)."""
    os.makedirs(WORK, exist_ok=True)
    copy_path = os.path.join(WORK, "synthetic_env_copy.txt")
    with io.open(copy_path, "w", encoding="utf-8", newline="") as f:
        f.write(synth_env())
    print(u"КОПИЯ окружения (синтетика, боевого файла не касаемся): %s" % copy_path)
    print(u"боевой путь в этом режиме не спрашивается у сервера и не открывается ни разу")
    print(u"-" * 104)

    with io.open(copy_path, "r", encoding="utf-8", newline="") as f:
        text = f.read()

    bad_n = refused = leaked = untouched = 0
    for title, value in bad_cases():
        bad_n += 1
        ok, why, new_text, report = plan_write(text, value, WRITE_NAMES)
        refused += 0 if ok else 1
        leaked += 1 if ok else 0
        untouched += 1 if (new_text == text and not report) else 0
        print(u"НЕГОДНОЕ %-40s знаков %-4d → %-17s %s"
              % (title, len(value), u"ОТКАЗ" if not ok else u"ПРОПУЩЕНО(ДЕФЕКТ)", why))
    print(u"-" * 104)

    ok, why, new_text, report = plan_write(text, synth_value("Nn9"), WRITE_NAMES)
    print(u"ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ (синтетика годной формы) знаков %d → %s | %s"
          % (LEN_REF, u"ПРИНЯТО" if ok else u"ОТКАЗ (ДЕФЕКТ)", why))
    for r in report:
        print(u"    строка %s: %s %s, правая часть %d → %d знаков"
              % (r["line"], r["name"], r["action"], r["old_len"], r["new_len"]))

    b_old = [l for l in text.splitlines() if l.startswith(NAME_SLOT_B)]
    b_new = [l for l in new_text.splitlines() if l.startswith(NAME_SLOT_B)]
    b_same = (b_old == b_new)
    sha_old = hashlib.sha256(u"\n".join(b_old).encode("utf-8")).hexdigest()[:12]
    sha_new = hashlib.sha256(u"\n".join(b_new).encode("utf-8")).hexdigest()[:12]

    # второй случай: слота A в файле нет вовсе — его обязано ДОПИСАТЬ, а не промолчать
    no_a = synth_env((NAME_ACTIVE, NAME_SLOT_B))
    ok2, _why2, _new2, rep2 = plan_write(no_a, synth_value("Nn9"), WRITE_NAMES)
    appended = [r for r in rep2 if r["action"] == "append"]

    verdicts = [(t, verdict_of_probe(rc, txt)[0]) for t, rc, txt in bad_envelopes()]
    green_on_bad = len([1 for _t, v in verdicts if v == GREEN])
    good_env = verdict_of_probe(0, '{"is_error":false,"subtype":"success","result":"OK"}')[0]

    print(u"-" * 104)
    print(u"ЧИСЛАМИ:")
    print(u"  негодных подано %d · отказано %d · ошибочно принято %d · файл после отказа байт в байт "
          u"прежний %d из %d" % (bad_n, refused, leaked, untouched, bad_n))
    print(u"  годных подано 1 · принято %d · строк изменено %d (ожидалось 2: %s)"
          % (1 if ok else 0, len([r for r in report if r["action"] == "replace"]),
             u", ".join(WRITE_NAMES)))
    print(u"  слот B не тронут: строк до %d, после %d, sha12 %s → %s, совпало=%s"
          % (len(b_old), len(b_new), sha_old, sha_new, u"ДА" if b_same else u"НЕТ"))
    print(u"  файла без слота A: дописано строк %d (ожидалось 1), всего правок %d, принято=%s"
          % (len(appended), len(rep2), u"ДА" if ok2 else u"НЕТ"))
    print(u"  конвертов пробы негодных %d · позеленело %d (ожидалось 0) · годный конверт → %s"
          % (len(verdicts), green_on_bad, good_env))
    for t, v in verdicts:
        print(u"      %-32s → %s" % (t, v))
    bad = ((refused != bad_n) or leaked or (untouched != bad_n) or (not ok) or (not b_same)
           or (len(appended) != 1) or green_on_bad or (good_env != GREEN))
    print(u"-" * 104)
    print(u"ИТОГО: %s" % (u"проверка ОТКАЗЫВАЕТ там, где обязана, и зеленеет только на годном"
                          if not bad else u"РАСХОЖДЕНИЕ — инструмент нельзя пускать в дело"))
    return 1 if bad else 0


# ═══════════════════ ход ═══════════════════

def _ask_value():
    u"""Скрытый ввод. `getpass` не эхает, значение не становится аргументом команды и потому не
    попадает ни в историю оболочки, ни в `ps`."""
    print(u"")
    print(u"Вставьте ЗНАЧЕНИЕ годового токена (ввод скрыт, эха не будет — это нормально).")
    print(u"Ожидается одна строка вида `%s…`, около %d знаков, БЕЗ переносов." % (TOKEN_PREFIX, LEN_REF))
    return getpass.getpass(u"токен: ")


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description=u"Вписать годовой токен основной учётки в окружение "
                                             u"серверного демона, перезапустить и доказать пробой.")
    ap.add_argument("--selftest", action="store_true", help=u"отрицательный тест на КОПИИ, без сети")
    ap.add_argument("--check", action="store_true", help=u"только разведка сервера, значение не спрашиваем")
    ap.add_argument("--dry", action="store_true", help=u"спросить и проверить форму, сервер не править")
    ap.add_argument("--force", action="store_true", help=u"не спрашивать при идущем заходе на сервере")
    ap.add_argument("--keep-nongreen", action="store_true",
                    help=u"оставить новый токен, если проба ответила 429 (лимит — не порча токена)")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    print(u"Сервер: %s · юнит: %s" % (SRV_HOST, UNIT))
    print(u"Пишем в имена: %s. Слот B (%s) НЕ трогаем — исправен, вторая учётка, после 27.09 запасной."
          % (u", ".join(WRITE_NAMES), NAME_SLOT_B))
    target, words = find_env_path()
    print(u"Адрес файла окружения: %s" % (target if target else u"НЕ ОПРЕДЕЛЁН"))
    print(u"  %s" % words)
    if not target:
        print(u"ОТКАЗ ДОКАЗАН, причина названа. Ничего не менялось, значение не спрашивали.")
        return 2

    ok, facts, words = preflight(target)
    print(u"Разведка: %s" % words)
    if not ok:
        print(u"ОТКАЗ ДОКАЗАН, причина названа. Ничего не менялось, значение не спрашивали.")
        return 2
    print(u"  файл: %s байт, права %s, изменён %s UTC"
          % (facts.get("file_bytes"), facts.get("file_mode"), facts.get("file_mtime")))
    print(u"  имена в файле (строка:имя:длина правой части): %s" % facts.get("names"))
    busy = int(facts.get("busy_claude") or 0)
    if args.check:
        print(u"  идущих заходов на сервере: %d" % busy)
        print(u"Режим --check: значение не спрашивали, ничего не меняли.")
        return 0

    value = _ask_value()
    shape = check_shape(value)
    print(u"Форма: %s — %s" % (u"СОШЛАСЬ" if shape["ok"] else u"НЕ СОШЛАСЬ", shape["reason"]))
    if not shape["ok"]:
        print(u"Значение отвергнуто ДО всякой записи. Боевой файл не открывался. Повторите запуск.")
        return 3
    if args.dry:
        print(u"Режим --dry: сервер не правили, демон не трогали.")
        return 0

    if busy and not args.force:
        print(u"ВНИМАНИЕ: на сервере идёт заход (%d процесс(ов) `claude`). Перезапуск демона убьёт "
              u"его вместе с cgroup." % busy)
        if (input(u"продолжать? [y/N]: ").strip().lower()) not in ("y", "yes", u"д", u"да"):
            print(u"Остановлено по вашему ответу. Ничего не менялось.")
            return 4

    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    ok, facts, words = apply_value(target, value, stamp)
    bak = facts.get("backup")
    print(u"Запись: %s" % words)
    if bak:
        print(u"  копия боевого файла: %s (не удаляется никогда)" % bak)
    for line in facts.get("lines", []):
        print(u"  строка %s" % line)
    if not ok:
        if bak:
            rok, _rf, rwords = restore(target, bak)
            print(u"ОТКАТ: %s" % rwords)
            print(u"  откат %s" % (u"состоялся" if rok else u"НЕ состоялся — нужна рука владельца"))
        else:
            print(u"Копия не создавалась — боевой файл не менялся.")
        return 5
    print(u"  байт до %s → после %s, обратное чтение=%s, прочие имена нетронуты=%s"
          % (facts.get("bytes_before"), facts.get("bytes_after"),
             facts.get("readback_ok"), facts.get("others_untouched")))

    ok, _rfacts, words = restart_unit()
    print(u"Перезапуск: %s" % words)
    if not ok:
        rok, _rf, rwords = restore(target, bak)
        print(u"ОТКАТ: %s" % rwords)
        restart_unit()
        print(u"  демон поднят обратно на прежнем значении" if rok
              else u"  откат файла НЕ состоялся — нужна рука владельца")
        return 6

    word, why, _raw = probe(target)
    print(u"")
    print(u"ВЕРДИКТ: %s" % word)
    print(u"  %s" % why)

    if word == GREEN:
        print(u"")
        print(u"Готово: сервер работает на токене основной учётки. Копия прежнего файла — %s" % bak)
        return 0
    if word == LIMIT and args.keep_nongreen:
        print(u"Ключ --keep-nongreen: новый токен ОСТАВЛЕН, отката не делаем. Лимит — не порча токена.")
        return 7

    print(u"Зелёным считается только ответ без ошибки, поэтому откатываемся — вслух и до конца.")
    rok, _rf, rwords = restore(target, bak)
    print(u"ОТКАТ: %s" % rwords)
    rok2, _rf2, rwords2 = restart_unit()
    print(u"  демон поднят заново на прежнем значении: %s" % rwords2)
    if not (rok and rok2):
        print(u"  ОТКАТ НЕ ЗАВЕРШЁН — нужна рука владельца: вернуть %s поверх %s и "
              u"`systemctl restart %s`" % (bak, target, UNIT))
    if word == LIMIT:
        print(u"  Что это значит: токен ПРИНЯТ, но у учётки кончился недельный лимит. Повторите с "
              u"ключом --keep-nongreen, если хотите оставить его до сброса.")
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
