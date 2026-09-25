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
    venv\\Scripts\\python.exe vps_token_install.py --probe-slots  # перемер: 1 вызов на КАЖДОЕ имя входа
    venv\\Scripts\\python.exe vps_token_install.py --slot C   # новый токен ТОЛЬКО в слот C (25.09)
    venv\\Scripts\\python.exe vps_token_install.py --use C    # слот C → действующее + рестарт (25.09)
Режим `--probe-slots` (22.09.2026, 71u) файл не правит и демон не трогает, но ЧИТАЕТ файл окружения
на сервере в процесс оболочки — это класс `env`, и запускает его владелец, а не headless-заход.
С 25.09 он меряет ВСЕ слоты, какие юнит держит в файле (`TB_CLAUDE_TOKEN_<буква>`), а не три.

СЛОТЫ ЛЮБОЙ БУКВЫ И ДВА НОВЫХ РЕЖИМА (25.09.2026, задание Штаба «учётки одним словом»):
  • `--slot X` — годовой токен уходит ТОЛЬКО в `TB_CLAUDE_TOKEN_X` (действующее и прочие слоты не
    тронуты), одна проба ИМЕННО этого слота; `401`/иное — откат из копии, `429` — оставляем (лимит
    не порча токена: так заводят учётку, у которой лимит ещё не сброшен). Демон НЕ перезапускается:
    слот он увидит на ближайшем рестарте (`--use` или «учётка N» с телефона);
  • `--use X` — ЗНАЧЕНИЕ слота X становится действующим. Значение копируется СЕРВЕРНОЙ программой
    из строки слота в строку действующего и наружу не выходит вовсе — ни в stdin, ни в вывод.
    Ход: разведка → идёт заход — стоп словами → копия с датой рядом → запись → ОДНА проба по файлу
    → отказ: возврат файла из копии, демон НЕ тронут → зелено: ОДИН рестарт демона. Проба стоит ДО
    рестарта сознательно: она меряет то же, с чем поднимется демон (файл), а отказ тогда не стоит
    второго рестарта «обратно». Пустой слот действующим не становится НИ ОДНОЙ веткой — проверка
    дважды: по разведке ПК и в самой серверной программе, ДО копии и записи;
  • `X` — буква A–Z либо номер учётки из реестра ПК (`accounts.py`): номер переводится в букву.
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
import shlex
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
# Слот любой буквы (25.09.2026): имя переменной = префикс + ОДНА латинская заглавная. Закрытая
# форма, а не «любое слово»: имя уходит строкой в программу оболочки на сервере.
SLOT_PREFIX = "TB_CLAUDE_TOKEN_"
_SLOT_NAME_RE = re.compile(r"TB_CLAUDE_TOKEN_[A-Z]")     # только fullmatch: `$` пропустил бы «\n»

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


def parse_envelope(text):
    u"""Вывод CLI → конверт `--output-format json` (dict) либо None. Чистая функция.

    Конверт — одна строка-объект, но чужой текст вокруг неё бывает С ОБЕИХ сторон: строки `k=v`
    пробы слотов сверху и строка хука `SessionEnd hook … failed` СНИЗУ (живой конверт 71r,
    `tmp/vps_token_switch/probe_neg.json`). Прежний `json.loads(text[первая скобка:])` на таком
    хвосте падал «Extra data», и ЧИСТЫЙ конверт уходил в «иное» (правка 22.09.2026, 71u).
    `raw_decode` берёт первый объект и хвост не читает."""
    text = text or u""
    dec = json.JSONDecoder()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("{"):
            try:
                data, _end = dec.raw_decode(s)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    brace = text.find("{")
    if brace >= 0:
        try:
            data, _end = dec.raw_decode(text[brace:])
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


def status_of(data, text):
    u"""Статус поставщика машинным полем → int | None. Чистая функция.

    ИМЁН ДВА, и путать их уже стоило верного вердикта. Конверт `--output-format json` называет
    поле `api_error_status` (живой замер 71r: `"api_error_status":401`; так же читает серверный
    `limit_slot.envelope`), а `apiErrorStatus` — имя того же поля в ТРАНСКРИПТЕ `.jsonl`. До
    22.09 судья читал только второе, машинного поля в живом конверте не видел вовсе и судил по
    тексту — то есть ровно тем способом, от которого предостерегает его же шапка."""
    if isinstance(data, dict):
        for key in ("api_error_status", "apiErrorStatus"):
            raw = data.get(key)
            if isinstance(raw, bool) or raw is None:
                continue
            if isinstance(raw, int):
                return raw
            if isinstance(raw, str) and raw.strip().isdigit():
                return int(raw.strip())
    m = re.search(r'"(?:api_error_status|apiErrorStatus)"\s*:\s*"?(\d{3})', text or u"")
    return int(m.group(1)) if m else None


def verdict_of_probe(rc, out):
    u"""Ответ дешёвого вызова → (слово исхода, пояснение). Чистая функция: судит ТЕКСТ, не сеть.

    МАШИННОЕ ПОЛЕ СТАРШЕ ТЕКСТА, и это не вкусовщина: строка «resets Sep 27, 9am (UTC)» живёт в
    транскриптах ПК 29 раз и ни разу как собственный отказ (артефакт 71r), поэтому поиск слов по
    тексту даёт ровно обратный вывод. Сначала `is_error`/`api_error_status`, слова — только когда
    машинных полей нет вовсе."""
    text = out or u""
    data = parse_envelope(text)
    if isinstance(data, dict) and data.get("is_error") is False and rc == 0:
        return GREEN, u"поставщик ответил без ошибки (is_error=false, exit=0)"

    status = status_of(data, text)
    if status == 429:
        return LIMIT, u"вход ПРИНЯТ, но лимит учётки исчерпан (api_error_status=429)"
    if status == 401:
        return DENIED, u"вход НЕ принят: поставщик отверг токен (api_error_status=401)"
    if status is not None:
        return OTHER, u"api_error_status=%d; ответ поставщика: %s" % (status, _squeeze(text))

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


# ─── перемер слотов (`--probe-slots`, заведён 22.09.2026, задание Штаба 0018t-71u.2209) ───
# Вопрос «где на самом деле отказ» до 22.09 решался по ЛОГУ демона: живой ответ поставщика был
# только у того слота, под которым демон случайно шёл, а равенство «действующее == B» — выводом
# из строки `слот=B`. Здесь — по ОДНОМУ дешёвому вызову под каждым из трёх имён, и наружу
# уходят только имя, «действующий ли», машинные поля конверта и текст поставщика.
PROBE_NAMES = (NAME_ACTIVE, NAME_SLOT_A, NAME_SLOT_B)


def slot_name(slot):
    u"""Буква слота → имя переменной (`C` → `TB_CLAUDE_TOKEN_C`). Не одна буква A–Z → ValueError.
    Чистая функция: имя уходит в программу оболочки, поэтому форма закрытая."""
    s = (u"%s" % (slot if slot is not None else u"")).strip().upper()
    name = SLOT_PREFIX + s
    if len(s) != 1 or not _SLOT_NAME_RE.fullmatch(name):
        raise ValueError(u"слот %r — не одна латинская буква A–Z" % (slot,))
    return name


def is_probe_name(name):
    u"""Имя входа, которое вправе попасть в программу пробы: действующее либо слот любой буквы."""
    return name == NAME_ACTIVE or bool(_SLOT_NAME_RE.fullmatch(name or u""))


def names_from_preflight(field):
    u"""Поле `names` разведки (`строка:имя:длина,…`) → {имя: длина правой части}. Чистая функция.
    Значений в поле нет по построению — только номер строки, имя и ДЛИНА. Повтор имени: берётся
    последний, как у `set -a; . файл` (последнее присваивание побеждает)."""
    out = {}
    for chunk in (field or u"").split(u","):
        parts = chunk.strip().rsplit(u":", 1)
        if len(parts) != 2:
            continue
        head, length = parts
        name = head.split(u":", 1)[-1].strip()
        if not length.strip().isdigit() or not name:
            continue
        out[name] = int(length.strip())
    return out


def probe_names_of(names):
    u"""Имена из разведки → что мерить: действующее ПЕРВЫМ, затем все слоты по букве. Чистая функция.
    Разведки нет (пустой словарь) → прежние три имени: перемер не должен ослепнуть от отказа разведки."""
    slots = sorted(n for n in names if _SLOT_NAME_RE.fullmatch(n))
    if not slots and NAME_ACTIVE not in names:
        return PROBE_NAMES
    return (NAME_ACTIVE,) + tuple(slots)


def probe_slot_script(target, name, model=PROBE_MODEL, claude_bin=CLAUDE_BIN, unit=UNIT):
    u"""Программа оболочки для ОДНОГО имени входа. Уезжает в stdin `bash -s`. Чистая функция.

    Каждая строка закрывает конкретный канал утечки или расхождения с демоном:
      • `exec 2>/dev/null` ДО чтения файла: строка-обломок токена (класс 28.07, перенос при
        вставке) исполнилась бы командой, и `bash` напечатал бы её ХВОСТ в сообщении
        «command not found». Поток ошибок у чтения файла закрыт насовсем;
      • сравнение `[ "$X" = "$Y" ]` — встроенная команда: значений нет ни в argv, ни в выводе,
        наружу уходит только 1/0 (ровно как `limit_slot.active_slot` на сервере);
      • значение попадает в `claude` ПРЕФИКСОМ-присваиванием, а не аргументом (`ps` его не видит);
      • `env -u ANTHROPIC_API_KEY` — как у демона (`child_env.pop`), иначе платный ключ перекрыл бы
        вход и проба мерила бы не тот канал;
      • `cd /` + `--setting-sources project`: из корня проектных настроек нет, а пользовательские
        не грузятся — хуки сервера (карточки, журнал) пробой не зовутся. Живой довод: проба 71r
        на ПК дёрнула `SessionEnd`-хук (хвост `probe_neg.json`);
      • `--no-session-persistence` — на сервере не остаётся транскрипта (временного не заводим);
      • `</dev/null` — `claude -p` читает stdin в подсказку, а stdin здесь — сама программа;
      • `file_mtime` и `daemon_start` — чтобы «действующий по файлу» можно было назвать входом
        ДЕМОНА: демон держит копию окружения со старта и файл не перечитывает."""
    if not is_probe_name(name):
        raise ValueError(u"имя %r не имя входа (действующее или слот по букве) — в оболочку не "
                         u"подставляется" % (name,))
    q = shlex.quote(target)
    lines = [
        "exec 2>/dev/null",
        "cd / || exit 3",
        "set -a; . %s || { echo loaded=0; exit 0; }; set +a" % q,
        "echo loaded=1",
        'if [ -z "${%s}" ]; then echo slot_filled=0; exit 0; fi' % name,
        "echo slot_filled=1",
        'if [ "${%s}" = "${%s}" ]; then echo is_active=1; else echo is_active=0; fi' % (name, NAME_ACTIVE),
        "echo file_mtime=$(stat -c %%Y %s)" % q,
        'echo daemon_start=$(date -d "$(systemctl show %s -p ExecMainStartTimestamp --value)" +%%s)' % unit,
        ('CLAUDE_CODE_OAUTH_TOKEN="${%s}" env -u ANTHROPIC_API_KEY timeout 240 %s '
         '-p "Reply with exactly: OK" --model %s --output-format json '
         '--no-session-persistence --setting-sources project </dev/null 2>&1') % (name, claude_bin, model),
        "echo probe_rc=$?",
    ]
    return "\n".join(lines) + "\n"


def slot_row(name, out, channel_rc=0):
    u"""Сырой вывод пробы одного имени → строка отчёта. Чистая функция.

    `code` — статус поставщика: 200, если конверт без ошибки, иначе `api_error_status`; `None`,
    если вызова не было (слот пуст, файл не загрузился, канал упал). Код выхода `claude` берётся
    из `probe_rc` программы, а не у ssh: у ssh он всегда от последнего `echo`."""
    kv = parse_kv(out)
    row = {"name": name, "active": kv.get("is_active"), "filled": kv.get("slot_filled"),
           "is_error": None, "code": None, "word": OTHER, "text": u"", "called": False,
           "file_after_start": None, "reset": u""}
    try:
        row["file_after_start"] = int(kv.get("file_mtime", "")) > int(kv.get("daemon_start", ""))
    except ValueError:
        row["file_after_start"] = None
    if channel_rc == 255:
        row["text"] = u"канал: %s" % _squeeze(out, 300)
        return row
    if kv.get("loaded") != "1":
        row["text"] = u"файл окружения не загрузился — вызова не было"
        return row
    if kv.get("slot_filled") == "0":
        row["word"], row["text"] = u"пуст", u"значения под этим именем нет — вызова не было"
        return row
    rc_raw = kv.get("probe_rc", "")
    rc = int(rc_raw) if rc_raw.isdigit() else 255
    row["called"] = True
    data = parse_envelope(out)
    word, why = verdict_of_probe(rc, out)
    row["word"] = word
    if isinstance(data, dict):
        row["is_error"] = data.get("is_error")
        row["text"] = _squeeze(u"%s" % data.get("result", u""), 300)
    else:
        row["text"] = u"конверта нет (exit=%s): %s" % (rc, _squeeze(out, 300))
    row["code"] = 200 if word == GREEN else status_of(data, out)
    if row["code"] is None and word == LIMIT:
        row["code"] = 429                  # лимит назван текстом поставщика, а не полем
    if row["code"] == 429:
        row["reset"] = reset_of(data, out)
    if word == OTHER and not row["text"]:
        row["text"] = why
    return row


# «resets Sep 27, 9am (UTC)» / «resets 4am (Asia/Bangkok)» — дословные формы живых отказов
# (`exit_evidence.py`, голдены #90/#92/#153/#154). Число «…limit reached|1758963600» — эпоха UTC.
_RESET_RE = re.compile(r"resets?\s+(?:at\s+)?([A-Za-z0-9 ,:/_()+\-]{2,60})", re.I)
_RESET_EPOCH_RE = re.compile(r"limit reached\|(\d{9,11})\b", re.I)


def reset_of(data, text):
    u"""Когда поставщик обещает снять лимит — ЕГО словами. → строка | "" (не назвал — не выдумываем).

    Зовётся ТОЛЬКО на доказанном 429 (`slot_row`): слово «resets» живёт и в чужих цитатах
    (мина 71r), поэтому на 401 или «ином» время сброса не ищется вовсе. Чистая функция."""
    if isinstance(data, dict):
        for key in ("resets_at", "reset_at", "retry_after", "rate_limit_reset"):
            val = data.get(key)
            if val not in (None, u"", {}, []):
                return (u"%s" % val)[:60]
        src = u"%s" % data.get("result", u"")
    else:
        src = text or u""
    m = _RESET_EPOCH_RE.search(src)
    if m:
        return time.strftime("%d.%m %H:%M UTC", time.gmtime(int(m.group(1))))
    m = _RESET_RE.search(src)
    if not m:
        return u""
    said = m.group(1).strip()
    if u")" in said:
        said = said[:said.index(u")") + 1]
    return said.rstrip(u" .,")


def summarize_slots(rows):
    u"""Строки отчёта → счёт и приговор словами. Чистая функция.

    Отвечает ровно на два вопроса владельца: «действующий без лимита?» и «лимит только на
    одном?». Имена, чьи значения побайтно равны действующему, считаются ПО ИМЕНАМ (задание
    просит вызов на имя), но разных входов может быть меньше трёх — это называется отдельно."""
    counts = {200: 0, 429: 0, 401: 0, "other": 0, "none": 0}
    for r in rows:
        if not r["called"]:
            counts["none"] += 1
        elif r["code"] in (200, 429, 401):
            counts[r["code"]] += 1
        else:
            counts["other"] += 1
    act = [r for r in rows if r["name"] == NAME_ACTIVE]
    act_code = act[0]["code"] if act else None
    same = [r["name"] for r in rows if r["name"] != NAME_ACTIVE and r["active"] == "1"]
    lines = [u"ИТОГО по именам: 200 — %d · 429 — %d · 401 — %d · иное — %d · без вызова — %d"
             % (counts[200], counts[429], counts[401], counts["other"], counts["none"])]
    if act_code == 200:
        lines.append(u"ДЕЙСТВУЮЩИЙ отвечает 200 — вход демона рабочий, лимита на нём нет.")
    elif act_code == 429:
        lines.append(u"ДЕЙСТВУЮЩИЙ отвечает 429 — вход принят, лимит исчерпан.")
    elif act_code == 401:
        lines.append(u"ДЕЙСТВУЮЩИЙ отвечает 401 — вход не принят.")
    else:
        lines.append(u"ДЕЙСТВУЮЩИЙ: живого статуса нет — исход НЕИЗВЕСТНО.")
    lines.append(u"Слоты с тем же значением, что у действующего: %s"
                 % (u", ".join(same) if same else u"нет"))
    green = [r["name"] for r in rows if r["code"] == 200]
    lines.append(u"Отвечают 200: %s" % (u", ".join(green) if green else u"ни одно имя"))
    fas = [r["file_after_start"] for r in rows if r["file_after_start"] is not None]
    if fas and not any(fas):
        lines.append(u"Файл окружения не менялся после старта демона — «действующий» здесь = вход демона.")
    elif any(fas):
        lines.append(u"ВНИМАНИЕ: файл окружения изменён ПОСЛЕ старта демона — демон держит прежнее "
                     u"значение, «действующий по файлу» ≠ вход демона до его перезапуска.")
    return counts, lines


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

# `--use X` (25.09.2026): значение слота → действующее. ЗНАЧЕНИЕ ЧИТАЕТСЯ И ПИШЕТСЯ ЗДЕСЬ, на
# сервере, и наружу не выходит ни одним каналом: в программе его нет (она несёт только ИМЕНА), в
# выводе — только признаки. Порядок строк — порядок защит: слот пуст → отказ ДО копии и записи.
_RS_USE = r'''
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
def value_of(text, name):
    found = ""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.replace("export ", "").strip() != name:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        found = v                     # последнее присваивание побеждает, как у `set -a; . файл`
    return found

if not os.path.isfile(TARGET):
    out("err", "file_missing"); sys.exit(3)
fh = io.open(TARGET, "r", encoding="utf-8", errors="strict", newline="")
try:
    before = fh.read()
finally:
    fh.close()
src = value_of(before, SLOT_NAME)
if not src:
    out("slot_filled", 0); out("err", "slot_empty"); sys.exit(0)   # пустой слот НЕ становится действующим
out("slot_filled", 1)
out("slot_len", len(src))
if value_of(before, ACTIVE_NAME) == src:
    out("already_active", 1); out("done", 1); sys.exit(0)          # писать нечего, копии не делаем
out("already_active", 0)

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

after, report = env_rewrite(before, src, (ACTIVE_NAME,))
for r in report:
    out("line", "%s:%s:%s:%s->%s" % (r["line"], r["name"], r["action"], r["old_len"], r["new_len"]))
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
ok_active = 1 if value_of(back, ACTIVE_NAME) == src else 0
ok_slot = 1 if value_of(back, SLOT_NAME) == src else 0
ok_others = 1
b_lines, a_lines = before.splitlines(), back.splitlines()
for i, line in enumerate(a_lines):
    s = line.strip()
    name = s.partition("=")[0].replace("export ", "").strip() if "=" in s else ""
    if name == ACTIVE_NAME:
        continue
    if i >= len(b_lines) or line != b_lines[i]:
        ok_others = 0
out("active_ok", ok_active)
out("slot_untouched", ok_slot)
out("others_untouched", ok_others)
out("done", 1 if (ok_active and ok_slot and ok_others and back == after) else 0)
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


def apply_value(target, value, stamp, names=WRITE_NAMES):
    u"""Шаги в–г: копия рядом + запись в названные имена (по умолчанию — ДВА: действующее и A).
    `--slot X` зовёт с одним именем слота X: действующее и прочие слоты тогда не трогаются."""
    prog = _remote_program(_RS_APPLY, TARGET=target, NAMES=tuple(names), VALUE=value,
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


PROBE_WORK = os.path.join(REPO, "tmp", "vps_slots_probe")   # временное место перемера, названо заданием 71u


# Временное место заданий 25.09 («учётки одним словом»), названо заданием: сюда и только сюда
# кладётся сырой вывод проб `--use`/`--slot` и проб, заказанных словом владельца.
UCHETKI_WORK = os.path.join(REPO, "tmp", "uchetki_2509")


def _save_raw(work, tag, text):
    u"""Сырой вывод захода (только `k=v`-признаки и конверт CLI, значений в нём нет) → файл."""
    if not work:
        return
    try:
        os.makedirs(work, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
        with io.open(os.path.join(work, "%s-%s.txt" % (stamp, tag)), "w",
                     encoding="utf-8", newline="\n") as f:
            f.write(text or u"")
    except Exception:                                  # noqa: BLE001 — след не смеет уронить ход
        pass


def probe_one(target, name, work=None):
    u"""Одна проба одного имени входа по файлу окружения → строка отчёта (`slot_row`)."""
    rc, out = ssh_run("bash -s", stdin_text=probe_slot_script(target, name), timeout=SSH_PROBE_TIMEOUT)
    _save_raw(work, "probe-" + name, u"ssh_rc=%s\n%s" % (rc, out))
    return slot_row(name, out, channel_rc=rc)


def probe_slots(target, save=True, names=None, work=None):
    u"""По ОДНОМУ дешёвому вызову под каждым именем входа. → список строк отчёта (`slot_row`).

    Один ssh-заход и не больше одного вызова поставщика на имя: пустой слот вызова не рождает.
    Имена по умолчанию — прежние три; `names` приходит из разведки (все слоты по букве).
    Сырой вывод каждого захода кладётся в `tmp/vps_slots_probe/` (или `work`) — в нём только
    `k=v`-признаки и конверт CLI, значений входа в нём не бывает (конверт их не содержит, замер
    71r), и следующий заход ПК читает исход отсюда, а не со слов."""
    where = work if work else (PROBE_WORK if save else None)
    rows = []
    for name in (names or PROBE_NAMES):
        rows.append(probe_one(target, name, where))
    return rows


def probe_all_slots(work=None):
    u"""Адрес у юнита → разведка имён (без значений) → по одной пробе на действующее и КАЖДЫЙ слот.
    → {"ok", "target", "words", "rows", "busy"}. Разведка не удалась (канал, файла нет) → проб НЕТ
    и причина названа: три пробы в мёртвый канал стоили бы трёх таймаутов и не сказали бы больше."""
    target, words = find_env_path()
    res = {"ok": False, "target": target, "words": words, "rows": [], "busy": None}
    if not target:
        return res
    ok, facts, pwords = preflight(target)
    if not ok:
        res["words"] = pwords
        return res
    names = names_from_preflight(facts.get("names"))
    res["busy"] = int(facts.get("busy_claude") or 0)
    res["rows"] = probe_slots(target, save=bool(work), names=probe_names_of(names), work=work)
    res["ok"] = True
    return res


def run_probe_slots():
    u"""Режим `--probe-slots`: адрес у юнита → все имена входа → отчёт по именам и приговор."""
    print(u"Перемер слотов: сервер %s · юнит %s · модель пробы %s" % (SRV_HOST, UNIT, PROBE_MODEL))
    res = probe_all_slots(work=PROBE_WORK)
    print(u"Адрес файла окружения: %s — %s"
          % (res["target"] if res["target"] else u"НЕ ОПРЕДЕЛЁН", res["words"]))
    if not res["ok"]:
        print(u"ОТКАЗ ДОКАЗАН, причина названа. Вызовов не было.")
        return 2
    rows = res["rows"]
    for r in rows:
        print(u"СЛОТ %-24s действующий=%-3s is_error=%-5s статус=%-4s %s%s · «%s»"
              % (r["name"], {"1": u"да", "0": u"нет"}.get(r["active"], u"?"),
                 {True: "true", False: "false"}.get(r["is_error"], "-"),
                 r["code"] if r["code"] is not None else "-", r["word"],
                 (u" · сброс «%s»" % r["reset"]) if r.get("reset") else u"", r["text"]))
    _counts, lines = summarize_slots(rows)
    for line in lines:
        print(line)
    print(u"Сырой вывод (без значений): %s" % PROBE_WORK)
    return 0 if all(r["called"] or r["word"] == u"пуст" for r in rows) else 2


def _row_words(row):
    u"""Строка пробы → короткие слова для человека: статус, слово, сброс."""
    if row is None:
        return u"пробы не было"
    code = row.get("code")
    tail = (u", сброс «%s»" % row["reset"]) if row.get("reset") else u""
    return u"%s (%s)%s" % (code if code is not None else u"без статуса", row.get("word"), tail)


def use_slot(slot, force=False, work=None, stamp=None):
    u"""`--use X`: значение слота X → действующее, ОДНА проба, при зелёном ОДИН рестарт демона.

    → {"ok", "stage", "slot", "name", "lines", "row", "backup", "restarted", "rolled_back"}.
    `stage` называет, где ход остановился: slot_name · address · preflight · slot_empty · busy ·
    write · already · probe · busy_late · restart · done. Любой отказ после записи — возврат файла
    из копии; копия не удаляется НИКОГДА."""
    res = {"ok": False, "stage": u"", "slot": u"%s" % slot, "name": u"", "lines": [], "row": None,
           "backup": u"", "restarted": False, "rolled_back": False}
    say = res["lines"].append
    try:
        name = slot_name(slot)
    except ValueError as e:
        res["stage"] = "slot_name"
        say(u"ОТКАЗ: %s" % e)
        return res
    res["name"] = name
    letter = name[len(SLOT_PREFIX):]
    target, words = find_env_path()
    if not target:
        res["stage"] = "address"
        say(u"ОТКАЗ: адрес файла окружения не определён — %s. Ничего не менялось." % words)
        return res
    ok, facts, pwords = preflight(target)
    if not ok:
        res["stage"] = "preflight"
        say(u"ОТКАЗ: %s. Ничего не менялось." % pwords)
        return res
    names = names_from_preflight(facts.get("names"))
    if not names.get(name):
        res["stage"] = "slot_empty"
        say(u"ОТКАЗ: слот %s (%s) %s — пустой слот действующим НЕ становится. Файл и демон не "
            u"тронуты." % (letter, name, u"пуст" if name in names else u"в файле не назван"))
        return res
    busy = int(facts.get("busy_claude") or 0)
    if busy and not force:
        res["stage"] = "busy"
        say(u"СТОП: на сервере идёт заход (процессов claude: %d). Рестарт убил бы его вместе с "
            u"cgroup демона — НЕ перезапускаю. Файл и демон не тронуты; повтори слово, когда заход "
            u"кончится." % busy)
        return res
    stamp = stamp or time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    prog = _remote_program(_RS_USE, TARGET=target, SLOT_NAME=name, ACTIVE_NAME=NAME_ACTIVE,
                           BAK=backup_name(target, stamp))
    rc, fields, raw = run_remote_py(prog)
    _save_raw(work, "use-write-" + letter, u"ssh_rc=%s\n%s" % (rc, raw))
    bak = fields.get("backup") or u""
    res["backup"] = bak
    if fields.get("err") == "slot_empty":
        res["stage"] = "slot_empty"
        say(u"ОТКАЗ: слот %s на сервере пуст — действующим НЕ становится. Файл не тронут, копии "
            u"нет." % letter)
        return res
    if fields.get("already_active") == "1":
        row = probe_one(target, NAME_ACTIVE, work)
        res["row"], res["ok"], res["stage"] = row, row.get("code") == 200, "already"
        say(u"Слот %s уже действующий: файл не менялся, рестарта не было. Проба действующего: %s."
            % (letter, _row_words(row)))
        return res
    if rc == 255 or fields.get("err") or fields.get("done") != "1":
        res["stage"] = "write"
        why = fields.get("err") or (u"канал: %s" % _squeeze(raw, 200) if rc == 255
                                    else u"запись не подтвердилась обратным чтением")
        say(u"ОТКАЗ на записи: %s." % why)
        if bak:
            rok, _rf, rwords = restore(target, bak)
            res["rolled_back"] = rok
            say(u"ОТКАТ: %s" % rwords)
        else:
            say(u"Копии нет — файл не менялся.")
        return res
    say(u"Записано: действующее = значение слота %s (копия %s, не удаляется)." % (letter, bak))
    row = probe_one(target, NAME_ACTIVE, work)
    res["row"] = row
    if row.get("code") != 200:
        res["stage"] = "probe"
        rok, _rf, rwords = restore(target, bak)
        res["rolled_back"] = rok
        say(u"Проба по новому файлу: %s — зелёным считается только 200." % _row_words(row))
        say(u"ОТКАТ: %s. Демон НЕ перезапускался — он и не уходил с прежнего значения." % rwords
            if rok else u"ОТКАТ НЕ СОСТОЯЛСЯ: %s — нужна рука владельца (демон не перезапускался, "
                        u"но файл на диске уже другой)." % rwords)
        return res
    ok2, facts2, _w2 = preflight(target)
    busy2 = int(facts2.get("busy_claude") or 0) if ok2 else 0
    if busy2 and not force:
        # Пока шла проба, демон взял задачу. Рестарт убил бы её — возвращаем файл, демон не трогаем.
        res["stage"] = "busy_late"
        rok, _rf, rwords = restore(target, bak)
        res["rolled_back"] = rok
        say(u"СТОП: пока шла проба, на сервере начался заход (%d). Рестарт НЕ делаю; %s." % (busy2, rwords))
        return res
    rok_u, rfacts, rwords_u = restart_unit()
    res["restarted"] = True
    if not rok_u:
        res["stage"] = "restart"
        rok, _rf, rwords = restore(target, bak)
        res["rolled_back"] = rok
        ok3, _f3, w3 = restart_unit()
        say(u"Рестарт: %s. ОТКАТ: %s; демон поднят обратно: %s." % (rwords_u, rwords, w3))
        return res
    res["ok"], res["stage"] = True, "done"
    say(u"Проба действующего: %s. Рестарт: %s." % (_row_words(row), rwords_u))
    return res


def install_slot(slot, value, work=None, stamp=None):
    u"""`--slot X`: годовой токен ТОЛЬКО в `TB_CLAUDE_TOKEN_X`, одна проба ЭТОГО слота, без рестарта.
    401/иное → возврат файла из копии; 429 → оставляем (вход принят, лимит — не порча токена)."""
    res = {"ok": False, "stage": u"", "slot": u"%s" % slot, "name": u"", "lines": [], "row": None,
           "backup": u"", "rolled_back": False}
    say = res["lines"].append
    try:
        name = slot_name(slot)
    except ValueError as e:
        res["stage"] = "slot_name"
        say(u"ОТКАЗ: %s" % e)
        return res
    res["name"] = name
    shape = check_shape(value)
    if not shape["ok"]:
        res["stage"] = "shape"
        say(u"ОТКАЗ формы: %s. Сервер не трогали." % shape["reason"])
        return res
    target, words = find_env_path()
    if not target:
        res["stage"] = "address"
        say(u"ОТКАЗ: адрес файла окружения не определён — %s." % words)
        return res
    ok, _facts, pwords = preflight(target)
    if not ok:
        res["stage"] = "preflight"
        say(u"ОТКАЗ: %s." % pwords)
        return res
    stamp = stamp or time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    ok, fields, words = apply_value(target, value, stamp, names=(name,))
    bak = fields.get("backup") or u""
    res["backup"] = bak
    if not ok:
        res["stage"] = "write"
        say(u"ОТКАЗ на записи: %s." % words)
        if bak:
            rok, _rf, rwords = restore(target, bak)
            res["rolled_back"] = rok
            say(u"ОТКАТ: %s" % rwords)
        return res
    for ln in fields.get("lines", []):
        say(u"строка %s" % ln)
    row = probe_one(target, name, work)
    res["row"] = row
    if row.get("code") in (200, 429):
        res["ok"], res["stage"] = True, "done"
        say(u"Слот %s записан, проба: %s. Действующее НЕ менялось, демон не перезапускался — "
            u"слот он увидит на ближайшем рестарте («учётка N» или --use %s)."
            % (name[len(SLOT_PREFIX):], _row_words(row), name[len(SLOT_PREFIX):]))
        return res
    res["stage"] = "probe"
    rok, _rf, rwords = restore(target, bak)
    res["rolled_back"] = rok
    say(u"Проба слота: %s — вход не принят. ОТКАТ: %s" % (_row_words(row), rwords))
    return res


def resolve_slot_arg(arg):
    u"""Аргумент `--slot`/`--use` → (буква | "", словами). Буква A–Z — как есть; номер — через
    реестр учёток ПК (`accounts.py`). Номера нет в реестре или у учётки нет слота — отказ словами."""
    raw = (u"%s" % (arg or u"")).strip()
    if raw.isdigit():
        try:
            import accounts
        except Exception as e:                        # noqa: BLE001
            return u"", u"реестр учёток не читается (%s)" % type(e).__name__
        reg = accounts.load()
        row = reg.data["accounts"].get(accounts.parse_number(raw))
        if reg.state != accounts.ST_OK:
            return u"", reg.reason
        if row is None:
            return u"", u"учётки №%s в реестре нет" % raw
        if not row["slot"]:
            return u"", u"у учётки №%s «%s» слота сервера нет" % (raw, row["label"])
        return row["slot"], u"учётка №%s «%s» → слот %s" % (raw, row["label"], row["slot"])
    try:
        return slot_name(raw)[len(SLOT_PREFIX):], u"слот %s" % raw.upper()
    except ValueError as e:
        return u"", u"%s" % e


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
    ap.add_argument("--probe-slots", action="store_true",
                    help=u"перемер: по одному дешёвому вызову под действующим и КАЖДЫМ слотом, "
                         u"файл не правим, демон не трогаем, значения не печатаем")
    ap.add_argument("--slot", metavar="X",
                    help=u"новый токен ТОЛЬКО в слот X (буква A–Z или номер учётки из реестра); "
                         u"одна проба слота, демон не перезапускаем")
    ap.add_argument("--use", metavar="X",
                    help=u"значение слота X → действующее: копия, одна проба, при зелёном ОДИН "
                         u"рестарт демона; идёт заход — стоп словами")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.probe_slots:
        return run_probe_slots()
    if args.slot or args.use:
        letter, words = resolve_slot_arg(args.slot or args.use)
        print(u"Сервер: %s · юнит: %s · %s" % (SRV_HOST, UNIT, words))
        if not letter:
            print(u"ОТКАЗ: %s. Ничего не менялось." % words)
            return 2
        if args.use:
            res = use_slot(letter, force=args.force, work=UCHETKI_WORK)
        else:
            value = _ask_value()
            res = install_slot(letter, value, work=UCHETKI_WORK)
        for line in res["lines"]:
            print(line)
        print(u"ИТОГ: %s (этап %s)" % (u"ГОТОВО" if res["ok"] else u"НЕ СДЕЛАНО", res["stage"]))
        return 0 if res["ok"] else 7

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
