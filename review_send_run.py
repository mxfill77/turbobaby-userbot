"""Руки ступени 2 ревью-контура: отправить пакет в канал и принять ответ.

Разделение то же, что у ступени 1 (`review_pack` / `review_pack_build`): вся
логика — в чистом :mod:`review_send`, здесь только ввод-вывод — подпроцесс CLI,
HTTP, чтение пакета, запись ответа в `docs/review_inbox/` и (по явному ключу
`--journal`) одна строка-индекс через штатного писателя журнала.

    venv/Scripts/python.exe review_send_run.py --pack docs/review_outbox/<файл>.md
    venv/Scripts/python.exe review_send_run.py --pack … --channel codex --journal
    venv/Scripts/python.exe review_send_run.py --pack … --dry        # без сети вовсе
    venv/Scripts/python.exe review_send_run.py --manus-task <id>     # только забрать результат

Канал Manus АСИНХРОНЕН: `POST /v1/tasks` — приём работы (код 200 + `task_id`),
ответ забирается опросом `GET /v1/tasks/{task_id}` до состояния `completed`.
Вебхук как второй документированный способ полосе НЕДОСТУПЕН: он требует НАШЕГО
публичного HTTPS-адреса, куда Manus сам шлёт POST и проверяет доступность
пробным запросом, — у ПК-полосы такого адреса нет, наружу она только ходит.

И у канала ДВА предела, а не один: жёсткие 5000 estimated tokens (код 400) и
МОЛЧАЛИВЫЙ срез сообщения около 2400 знаков — принятое кодом 200 длинное
сообщение канал прячет во вложение, а ревьюер вложений не открывает. Поэтому
длинное исходящее едет ЧАСТЯМИ по одной задаче (`taskId`), см. :func:`send_manus`.

Коды возврата: 0 — все запрошенные каналы ответили; 3 — хотя бы один отказал
(в сухом прогоне — страж задержал исходящее); 4 — отказов нет, но есть
«неизвестно»; 2 — вход недействителен (пакета нет, имя канала не то), файлов не
написано. Сухой прогон файлов в лоток не пишет вовсе.

Что этот файл НЕ делает ни одной веткой: не создаёт задач, не пишет в очередь,
не исполняет ничего из ответа канала, не читает `.env` (ключ берётся из
ОКРУЖЕНИЯ процесса по имени, названному ключом `--key-env`) и не передаёт
секреты в дочерний процесс — см. :func:`child_env`.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import review_send

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INBOX = "docs/review_inbox"
DEFAULT_WORKDIR = "tmp/review_send"
JOURNAL_WRITER = "cowork_log_append.py"

# Имя переменной окружения с ключом Manus. В коде репозитория такого имени НЕ
# БЫЛО ни одного (замер 01.09: `manus` в .py — ноль совпадений, имена вида
# *_API_KEY в трекаемом коде ровно два, и оба чужие). Значит имя здесь не
# «найдено», а НАЗНАЧЕНО — и потому вынесено в ключ `--key-env`: когда владелец
# заведёт ключ под другим именем, отправщик правки не потребует.
DEFAULT_KEY_ENV = "MANUS_API_KEY"

DEFAULT_MANUS_BASE = "https://api.manus.ai"
DEFAULT_MANUS_PATH = "/v1/tasks"

# Забор результата. Manus исполняет задачу АСИНХРОННО: `POST /v1/tasks` отдаёт
# только `task_id`/`task_url` и код 200 — ответа в этом теле нет и быть не
# может. Два документированных способа получить результат: вебхук (Manus сам
# шлёт POST на НАШ публичный HTTPS-адрес — «send a test request to verify your
# endpoint is accessible») и опрос `GET /v1/tasks/{task_id}`. Публичного адреса
# у ПК-полосы нет, наружу она только ходит, — значит доступен РОВНО опрос.
DEFAULT_MANUS_TASK_PATH = "/v1/tasks/{task_id}"

# Состояния задачи по доке v1 (`status`): pending | running | completed | failed.
# Терминальны два последних; всё прочее (включая незнакомое слово) — «ещё идёт».
_MANUS_DONE = "completed"
_MANUS_FAILED = "failed"
# `running` — это «агент РАБОТАЕТ ПРЯМО СЕЙЧАС», и короткое молчание в этом
# состоянии затишьем считать нельзя. Живая проба 01.09: ревьюер бросил
# промежуточную реплику «Проверяю пакет по трём вопросам.» и задумался на 48 с
# при `status=running` — правило затишья сочло это ответом и выдало 32 знака
# вместо разбора, а задача спокойно доработала до `completed` сама. Но и ГЛУХОЙ
# запрет тут неверен: соседняя задача просидела в нетерминальном состоянии
# полчаса с готовым ответом. Поэтому не запрет, а ЦЕНА: при объявленной работе
# затишье обязано быть вчетверо длиннее (при шаге 8 с это ~3.2 мин полной
# тишины против 48 с, на которых правило и споткнулось).
_MANUS_RUNNING = "running"
MANUS_RUNNING_QUIET_FACTOR = 4
_NA_STATE = "—"

DEFAULT_TIMEOUT = 900

# Предел ожидания готовности задачи и шаг опроса. 1800 с (30 мин) — не круглое
# число с потолка: пакет второго мнения это 7–14 тыс. знаков разбора, живой
# заход Codex на том же пакете идёт единицы минут, а Manus по своей доке ходит
# в сеть и браузер, то есть дольше. 30 минут дают запас порядка на медленный
# заход и при этом кончаются РАНЬШЕ, чем демон снимет задачу по своему
# TASK_TIMEOUT. Шаг 10 с — 6 опросов в минуту против лимита канала 100/мин на
# чтение (запас 16x), за 30 минут это 180 опросов.
DEFAULT_MANUS_WAIT = 1800
DEFAULT_MANUS_POLL = 10
# Сколько подряд опросов задача должна выглядеть НЕИЗМЕННОЙ, чтобы считаться
# сдавшей работу без терминального `status`. Число заведено живой пробой
# 01.09.2026, а не вкусом: задача aG444Jibk7cD… за 1801 с и 166 опросов ни разу
# не вышла из `pending`, хотя ассистент к тому времени ДВАЖДЫ ответил и ждал нас.
# По доке v2 это состояние зовётся `waiting` («needs user confirmation or
# input») и опрашивать дальше нечего; в v1-ответе такого поля нет вовсе, поэтому
# признак — ЗАТИШЬЕ: `updated_at`, число сообщений и последнее сообщение не
# менялись 6 опросов подряд (60 с при шаге 10 с).
MANUS_SETTLE_POLLS = 6

# Сколько подряд опросов задача обязана выглядеть ЗАКОНЧЕННОЙ И НЕМОЙ, прежде
# чем ожидание ответа на часть будет прекращено. Замер 05.09.2026 по 25 живым
# промахам: канал ставит `status=completed` за 3 с (`created_at` 1788609083 →
# `updated_at` 1788609086), кладёт в `output` РОВНО ОДНО сообщение — наше же, с
# ролью `user`, — и печатает `credit_usage: 0`. Ни в одном из 25 случаев после
# этого не появилось ничего: состояние терминально и по доке, и по факту.
#
# Почему не 1 (выйти с первого же опроса): подтверждение стои́т ОДИН шаг опроса
# (10 с) против 1800 с прежнего ожидания, а страхует от единственного случая,
# который наблюдением не закрыт — гонки, где канал переводит статус в
# терминальный РАНЬШЕ, чем дописывает текст ответа. Цена страховки 0.55% от
# снимаемого, поэтому она куплена. Почему не 6 (как у затишья): затишье судит
# по КОСВЕННОМУ признаку (ничего не менялось), а здесь признак ПРЯМОЙ —
# документированное терминальное состояние, и держать его под тем же
# подозрением значит платить за одно и то же дважды.
MANUS_EMPTY_CONFIRM_POLLS = 2

# Сколько знаков канал держит ВСТРОЕННЫМ ТЕКСТОМ в одном сообщении. Порог 400 —
# не единственный: ПОД ним канал молча срезает длинное сообщение и укладывает
# остаток ВЛОЖЕНИЕМ, оставляя ревьюеру ~1100 знаков и строку
# «...(message too long, full content saved as attachment "…txt")». Ревьюер
# вложение не открывает (правило №1 нашего же конверта), то есть судит по
# заголовку — и честно об этом говорит. Замеры 01.09.2026, маркерными пробами
# (маркеры по всей длине, ревьюер перечисляет видимые):
#
#   2169 знаков / 3938 байт — вложений 0, видны ВСЕ 4 маркера;
#   2400 знаков / 4373 байта — вложений 0, виден и последний маркер на 2389;
#   4790 знаков / 8727 байт — вложение 1, видно 1100 знаков, виден 1 маркер из 6.
#
# Значит граница лежит в (2400, 4790] знаков; берём проверенные 2400 как потолок
# СООБЩЕНИЯ и режем с запасом. Порог 5000 estimated tokens тут ни при чём — он
# срабатывает выше и кодом 400, а этот срез молчалив.
MANUS_INLINE_MAX = 2400
# Место под рамку части (кто говорит, какая это часть из скольких и что делать).
# Считается из потолка, а не сверх него: рамка едет в ТОМ ЖЕ сообщении.
MANUS_PART_OVERHEAD = 420
MANUS_CHUNK_CHARS = MANUS_INLINE_MAX
# Сколько знаков тела ответа на ДОСЫЛКУ хранит протокол: форма ответа служебная
# (идентификатор и адрес задачи), и запас нужен, чтобы увидеть её, а не копить эхо.
MANUS_PART_REPLY_KEEP = 400

# Рамка части. Метка ОБЯЗАНА стоять в начале сообщения: канал показывает голову
# сообщения, и по этой же метке отправщик потом находит, откуда в переписке
# начинается ОТВЕТ на последнюю часть.
_PART_MARK = "[ЧАСТЬ %d/%d]"
_PART_FIRST = (
    "ПАКЕТ ПРИДЁТ %(n)d ЧАСТЯМИ ОДНИМ ПОТОКОМ: канал не принимает его целиком —\n"
    "длинное сообщение он срезает и прячет остаток во вложение, а вложения тебе\n"
    "открывать нельзя. Части — это ОДИН разрезанный текст, а не разные задачи.\n"
    "На части 1..%(prev)d отвечай РОВНО одним словом: ПРИНЯТО. Ничего не разбирай:\n"
    "правила ответа и три вопроса придут в части %(n)d/%(n)d.\n\n"
    "%(mark)s\n"
)
_PART_NEXT = "%(mark)s — продолжение того же текста. Ответь одним словом: ПРИНЯТО.\n"
_PART_LAST = (
    "%(mark)s — ПОСЛЕДНЯЯ. Склей части 1..%(n)d подряд в один текст и отвечай\n"
    "по правилам и трём вопросам из него. Ответ — текстом.\n"
)
# Сокет одного опроса: короткий сознательно — опрос дешёвый и повторяемый, а
# долгий сокет съел бы весь бюджет ожидания одним висящим соединением.
MANUS_POLL_HTTP_TIMEOUT = 60

# Имена, которые НЕ уезжают в дочерний процесс. Судим по форме имени, а не по
# списку: список протухает на первом новом секрете, а канал запускается с полным
# окружением демона. Отдельно важен ключ провайдера: у Codex вход по подписке,
# и оставленный в окружении ключ увёл бы заход на платный маршрут молча.
_RE_SECRET_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|COOKIE|SESSION)")


def child_env(base=None, keep=()):
    """Окружение дочернего процесса без секретов. → dict."""
    src = os.environ if base is None else base
    keep = {name.upper() for name in keep}
    out = {}
    for name, value in src.items():
        if name.upper() in keep or not _RE_SECRET_NAME.search(name):
            out[name] = value
    return out


def read_text(path):
    with io.open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write_text(path, text):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # newline="\n" — по той же причине, по какой лоток исходящих объявлен
    # `-text` в .gitattributes: sha256 файла ответа напечатан в строке-индексе
    # журнала и служит адресом. CRLF от свежего чекаута менял бы хеш.
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def journal_argv(repo=HERE, python=None):
    """argv штатного писателя журнала. Строка идёт СТДИНОМ, а не аргументом."""
    return [python or sys.executable, os.path.join(repo, JOURNAL_WRITER), "-"]


# ───────────────────────────── канал Codex (CLI) ─────────────────────────────


def resolve_codex(explicit=None):
    """Путь к запускаемому Codex. → str | None.

    На Windows в PATH лежат три файла с одним именем (`codex`, `codex.cmd`,
    `codex.ps1`), и запускаем из них через CreateProcess ровно `.cmd`.
    ``shutil.which`` выбирает его сам по PATHEXT — поэтому резолвим им, а не
    склейкой пути руками.
    """
    if explicit:
        return explicit if os.path.exists(explicit) or shutil.which(explicit) else shutil.which(explicit)
    return shutil.which("codex")


def send_codex(prompt, *, root, workdir, binary=None, model=None, cd=None, timeout=DEFAULT_TIMEOUT, sandbox="read-only"):
    """Отправить текст в Codex CLI. → dict фактов прогона (без суждений).

    Промпт уходит СТДИНОМ (аргумент `-`), а не в argv: кириллица в argv на этой
    полосе коверкается — известный класс, из-за которого писатель журнала тоже
    принимает строку стдином. Плюс пакет длиннее любой разумной командной
    строки Windows.
    """
    facts = {
        "target": None,
        "launch_error": None,
        "timed_out": False,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "last_message": None,
        "last_message_error": None,
    }
    exe = resolve_codex(binary)
    if not exe:
        facts["launch_error"] = "исполняемый файл codex не найден (PATH и --codex-bin пусты)"
        facts["target"] = binary or "codex"
        return facts

    os.makedirs(workdir, exist_ok=True)
    out_path = os.path.join(workdir, "codex_last_message.txt")
    if os.path.exists(out_path):
        # Хвост прошлого прогона обязан быть снят ДО запуска: иначе упавший
        # заход выдал бы вчерашний ответ за сегодняшний — класс «свежий
        # результат от следа прежнего прогона».
        os.remove(out_path)

    argv = [exe, "exec", "--sandbox", sandbox, "--color", "never", "--output-last-message", out_path]
    if model:
        argv += ["-m", model]
    if cd:
        argv += ["-C", cd]
    argv.append("-")
    facts["target"] = "%s exec --sandbox %s%s" % (os.path.basename(exe), sandbox, (" -m " + model) if model else "")

    try:
        done = subprocess.run(
            argv,
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=root,
            env=child_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        facts["timed_out"] = True
        return facts
    except OSError as exc:
        facts["launch_error"] = "%s: %s" % (type(exc).__name__, exc)
        return facts

    facts["returncode"] = done.returncode
    facts["stdout"] = done.stdout.decode("utf-8", "replace")
    facts["stderr"] = done.stderr.decode("utf-8", "replace")
    # Цену захода канал печатает В STDOUT и больше нигде: с концом процесса она
    # исчезает, а бюджет массовой отправки считается именно по ней. Кладём сырой
    # вывод рядом с ответом — в рабочий каталог (tmp/, вне git), не в лоток:
    # лоток хранит ОТВЕТ, а это протокол канала.
    try:
        write_text(os.path.join(workdir, "codex_stdout.txt"), facts["stdout"])
        write_text(os.path.join(workdir, "codex_stderr.txt"), facts["stderr"])
    except OSError:
        pass  # протокол — удобство, а не доказательство: его потеря вердикта не меняет
    if os.path.exists(out_path):
        try:
            facts["last_message"] = read_text(out_path)
        except OSError as exc:
            facts["last_message_error"] = "%s: %s" % (type(exc).__name__, exc)
    return facts


# ───────────────────────────── канал Manus (HTTP) ─────────────────────────────

# Транспортные отказы, про которые ТОЧНО известно, что запрос наружу не ушёл.
# Всё остальное считается ушедшим — сознательно в эту сторону: назвать
# отправленный запрос неотправленным дороже обратной ошибки, потому что из
# «не ушло» следует «повтори», а повтор — это второй заход и вторая оплата.
_NOT_SENT = (socket.gaierror, ConnectionRefusedError)


def _manus_headers(key, *, json_body):
    """Заголовки запроса к Manus. → dict.

    Ключ живёт ТОЛЬКО здесь и в сокете: ни одна ветка модуля его не печатает, не
    кладёт в протокол и не отдаёт в дочерний процесс (см. :func:`child_env`).
    """
    headers = {
        "API_KEY": key,
        "Authorization": "Bearer %s" % key,
        "User-Agent": "turbobaby-review-send/1",
    }
    if json_body:
        headers["Content-Type"] = "application/json; charset=utf-8"
    return headers


def manus_http(url, *, key, data=None, method="GET", timeout=DEFAULT_TIMEOUT):
    """Один HTTP-заход к Manus. → dict фактов (без суждений).

    Вынесен из :func:`send_manus` ровно потому, что заходов теперь ДВА вида —
    приём работы (POST) и забор результата (GET), — и граница «расписка ≠
    судьба» у них одна и та же: обрыв ДО отправки и обрыв ПОСЛЕ различаются
    здесь, а не у вызывающего.
    """
    facts = {"target": url, "request_sent": False, "transport_error": None, "status": None, "body": None}
    req = urllib.request.Request(
        url, data=data, method=method, headers=_manus_headers(key, json_body=data is not None)
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            facts["request_sent"] = True
            facts["status"] = resp.getcode()
            facts["body"] = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # Сервер ОТВЕТИЛ — просто кодом ошибки. Это не транспортный отказ.
        facts["request_sent"] = True
        facts["status"] = exc.code
        try:
            facts["body"] = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — тело ошибки необязательно
            facts["body"] = None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        facts["transport_error"] = "%s: %s" % (type(reason).__name__, reason)
        facts["request_sent"] = not isinstance(reason, _NOT_SENT)
    except (TimeoutError, socket.timeout) as exc:  # noqa: UP041 — socket.timeout жив на этой версии
        facts["transport_error"] = "%s: %s" % (type(exc).__name__, exc)
        facts["request_sent"] = True
    except OSError as exc:
        facts["transport_error"] = "%s: %s" % (type(exc).__name__, exc)
        facts["request_sent"] = not isinstance(exc, _NOT_SENT)
    return facts


def _json_or_none(body):
    """Тело как объект, если оно им является. → object | None (без исключений)."""
    if isinstance(body, (dict, list)):
        return body
    if not isinstance(body, str) or not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def manus_task_id(body):
    """Идентификатор и адрес созданной задачи. → (task_id|None, task_url|None)."""
    obj = _json_or_none(body)
    if not isinstance(obj, dict):
        return None, None
    tid = obj.get("task_id") or obj.get("id")
    turl = obj.get("task_url") or obj.get("url")
    return (
        tid.strip() if isinstance(tid, str) and tid.strip() else None,
        turl.strip() if isinstance(turl, str) and turl.strip() else None,
    )


def _manus_messages(body):
    """Массив сообщений задачи. → list[dict] (пусто, если формы нет)."""
    obj = _json_or_none(body)
    if not isinstance(obj, dict):
        return []
    output = obj.get("output")
    return [msg for msg in output if isinstance(msg, dict)] if isinstance(output, list) else []


def _msg_text(msg):
    """Текстовые куски одного сообщения В ПОРЯДКЕ КАНАЛА. → list[str]."""
    parts = []
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return [content]
    if not isinstance(content, list):
        return parts
    for item in content:
        if isinstance(item, str) and item.strip():
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    return parts


def manus_output_text(body, roles=("assistant",), after_marker=None):
    """Состояние задачи и её текст из тела `GET /v1/tasks/{id}`. → (state|None, text, note).

    Форма снята с доки v1 (`get-task`) и подтверждена живым телом 01.09: результат
    живёт в массиве `output` сообщений, у каждого `content` — массив кусков,
    текстовый кусок помечен `type: "output_text"` и несёт `text`. Берём куски В
    ПОРЯДКЕ КАНАЛА и только текстовые: перестановка или отбор «что поинтереснее»
    превратили бы второе мнение в наш пересказ.

    РОЛЬ ОБЯЗАТЕЛЬНА к проверке. В `output` лежит ВСЯ переписка, и первым в ней
    идёт НАШ СОБСТВЕННЫЙ промпт с ролью `user` (живая проба 01.09: 4 сообщения —
    наш пакет, ответ, служебное `continue`, ответ). Без отбора по роли отправщик
    склеил бы свой же вопрос с ответом и положил бы в лоток «ответ ревьюера»,
    наполовину написанный нами — худший из возможных исходов, потому что он
    выглядит как настоящий. Если роли не проставлены НИ У ОДНОГО сообщения,
    отбор снимается: это другой формат, и лучше отдать всё, чем ничего.
    """
    obj = _json_or_none(body)
    if not isinstance(obj, dict):
        return None, "", "тело не JSON-объект"
    raw_state = obj.get("status")
    state = raw_state.strip().lower() if isinstance(raw_state, str) else None
    messages = _manus_messages(body)
    # Отсечка по метке последней части. Нужна ровно потому, что при досылке
    # частями в переписке лежат наши же «ПРИНЯТО»-ответы, а между ними канал
    # умеет вставлять СВОЁ служебное `continue` с ролью `user` (видели живьём).
    # Брать «хвост подряд идущих ассистентов» из-за этого нельзя: чужой
    # `continue` посреди ответа отрезал бы его первую половину. Берём всё, что
    # сказано ПОСЛЕ нашей последней части; метки нет — берём всё, и это честно.
    dropped_before = 0
    if after_marker:
        for i, msg in enumerate(messages):
            role = msg.get("role")
            role = role.strip().lower() if isinstance(role, str) else None
            if role == "user" and any(after_marker in part for part in _msg_text(msg)):
                dropped_before = i + 1
        messages = messages[dropped_before:]
    wanted = {role.lower() for role in (roles or ())}
    roles_present = any(isinstance(msg.get("role"), str) and msg.get("role").strip() for msg in messages)
    parts = []
    skipped = 0
    for msg in messages:
        role = msg.get("role")
        role = role.strip().lower() if isinstance(role, str) else None
        if wanted and roles_present and role not in wanted:
            skipped += 1
            continue
        parts.extend(_msg_text(msg))
    text = "\n\n".join(part.strip("\n") for part in parts)
    return (
        state,
        text,
        "состояние %s, сообщений %d (до метки отброшено %d, чужих ролей пропущено %d), текстовых кусков %d"
        % (state or _NA_STATE, len(messages), dropped_before, skipped, len(parts)),
    )


def manus_settled_signature(body):
    """Подпись «канал сказал своё и ждёт нас». → str | None.

    ``None`` означает «рано»: задача либо ещё не отвечала, либо последним
    говорил не ассистент, либо его сообщение не закрыто. Не-``None``, ПОВТОРИВШИЙСЯ
    подряд :data:`MANUS_SETTLE_POLLS` раз, и есть признак затишья.

    Почему признак вообще нужен — см. комментарий у :data:`MANUS_SETTLE_POLLS`:
    документированный терминальный `status` на живой задаче не наступил ни разу
    за полчаса. Почему признак именно такой: он меняется от ЛЮБОГО шевеления на
    той стороне (правка `updated_at`, новое сообщение, смена последнего), то есть
    ошибается в сторону «ждём дальше», а не в сторону преждевременного «готово».
    """
    obj = _json_or_none(body)
    if not isinstance(obj, dict):
        return None
    messages = _manus_messages(body)
    if not messages:
        return None
    last = messages[-1]
    role = last.get("role")
    role = role.strip().lower() if isinstance(role, str) else None
    if role != "assistant":
        return None
    status = last.get("status")
    status = status.strip().lower() if isinstance(status, str) else None
    if status and status not in ("completed", "done", "finished"):
        return None
    if not _msg_text(last):
        return None
    return "%s|%d|%s|%s" % (obj.get("updated_at"), len(messages), last.get("id"), status)


def manus_credit_usage(body):
    """КВИТАНЦИЯ канала за задачу. → int | None.

    ``None`` значит «канал квитанции не дал», и это НЕ ноль: своего суждения о
    чужой цене у отправщика нет ни одной веткой. Поле `credit_usage` канал
    печатает в теле `GET /v1/tasks/{id}` рядом со `status`; на 25 промахах
    05.09.2026 оно равнялось нулю при `status=completed` — то есть канал сам
    сказал, что работы не делал и денег не взял.

    Читается ЧИСЛО, а не истинность: ноль — полноценный ответ квитанции, и
    путать его с «поля нет» нельзя, иначе бесплатный отказ станет неотличим от
    молчания канала о цене.
    """
    obj = _json_or_none(body)
    if not isinstance(obj, dict):
        return None
    value = obj.get("credit_usage")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def manus_error_text(body):
    """Текст ошибки задачи, если канал его дал. → str."""
    obj = _json_or_none(body)
    if not isinstance(obj, dict):
        return ""
    for field in ("error", "error_message", "incomplete_details"):
        value = obj.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for inner in ("message", "reason", "detail"):
                text = value.get(inner)
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return ""


def fetch_manus_task(task_id, *, key, base=DEFAULT_MANUS_BASE, task_path=DEFAULT_MANUS_TASK_PATH, timeout=MANUS_POLL_HTTP_TIMEOUT):
    """Один забор состояния задачи по идентификатору. → dict фактов HTTP."""
    quoted = urllib.parse.quote(str(task_id), safe="")
    url = base.rstrip("/") + "/" + task_path.lstrip("/").replace("{task_id}", quoted)
    return manus_http(url, key=key, method="GET", timeout=timeout)


def manus_chunks(text, limit):
    """Разрезать текст на куски не длиннее предела. → list[str].

    Режем ПО ГРАНИЦАМ СТРОК, пока это возможно: пакет второго мнения —
    размеченный текст, и разрыв посреди строки таблицы, хеша или пути делает
    кусок нечитаемым ровно там, где ревьюер должен что-то сверить. Строку
    длиннее предела режем жёстко: одна длинная строка иначе сорвала бы заход.
    Склейка кусков подряд обязана давать ИСХОДНЫЙ текст знак в знак — на этом
    стоит вся честность досылки, поэтому переносы строк остаются внутри кусков
    (``splitlines(True)``), а не выбрасываются.
    """
    limit = max(200, int(limit))
    out, cur, cur_len = [], [], 0
    for line in text.splitlines(True):
        while len(line) > limit:
            if cur:
                out.append("".join(cur))
                cur, cur_len = [], 0
            out.append(line[:limit])
            line = line[limit:]
        if cur and cur_len + len(line) > limit:
            out.append("".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line)
    if cur:
        out.append("".join(cur))
    return out or [""]


def _part_head(index, total):
    """Рамка части ``index`` из ``total``. → str. Одна на отправку и на опознание."""
    mark = _PART_MARK % (index, total)
    if index == 1:
        return _PART_FIRST % {"n": total, "prev": total - 1, "mark": mark}
    if index == total:
        return _PART_LAST % {"n": total, "mark": mark}
    return _PART_NEXT % {"mark": mark}


def manus_parts(prompt, limit=MANUS_CHUNK_CHARS, overhead=MANUS_PART_OVERHEAD):
    """Готовые СООБЩЕНИЯ канала (кусок текста плюс рамка части). → list[str].

    Умещается целиком — одно сообщение и никакой рамки: лишняя обёртка на
    коротком пакете только мешала бы ревьюеру. Не умещается — рамка обязательна,
    иначе части выглядят как отдельные задачи и ревьюер отвечает на каждую.
    """
    if not limit or len(prompt) <= limit:
        return [prompt]
    bodies = manus_chunks(prompt, max(200, limit - overhead))
    total = len(bodies)
    return [_part_head(index, total) + body for index, body in enumerate(bodies, 1)]


def _holds_head(body, head):
    """Держит ли задача НАШЕ сообщение с этой рамкой. → bool.

    Признак — из ответа КАНАЛА: сообщение с ролью `user` в `output` ЭТОЙ задачи,
    которое НАЧИНАЕТСЯ рамкой части. Именно начало, а не вхождение метки: пакет
    может цитировать метки (разбор этой самой резки их цитирует), и вхождение
    засчитало бы часть, которой в задаче нет.
    """
    want = head.replace("\r\n", "\n")
    for msg in _manus_messages(body):
        role = msg.get("role")
        if not (isinstance(role, str) and role.strip().lower() == "user"):
            continue
        if "".join(_msg_text(msg)).replace("\r\n", "\n").lstrip().startswith(want):
            return True
    return False


def manus_parts_seen(body, total):
    """Какие части из ``total`` канал держит в задаче. → list[int] по возрастанию.

    Число доехавших частей называет КАНАЛ, а не наш счётчик отправок: код 200 на
    досылку значит «запрос принят», а не «сообщение легло в ту же задачу».
    """
    if not isinstance(total, int) or total < 2:
        return []
    return [k for k in range(1, total + 1) if _holds_head(body, _part_head(k, total))]


def _wait_settled(facts, *, key, base, task_path, poll, settle_polls, deadline, started, clock, sleep, tag=""):
    """Опрашивать задачу, пока она не замрёт. → "ready" | "timeout" | "http" | "failed".

    Обновляет ``facts`` на месте. НИ ОДНА ветка не превращает «не дождались» в
    «готово»: истёкший предел выходит исходом ``timeout`` и оставляет в фактах
    обрыв ПОСЛЕ отправки, который классификатор зовёт «неизвестно».
    """
    facts["last_signature"] = None
    facts["settled_polls"] = 0
    while True:
        facts["waited_sec"] = int(clock() - started)
        if clock() >= deadline:
            facts["request_sent"] = True
            facts["poll_timeout"] = True
            facts["transport_error"] = (
                "poll_timeout: задача %s не дошла до готовности%s за %d с "
                "(опросов %d, последнее состояние %s%s)"
                % (
                    facts.get("task_id"),
                    tag,
                    facts["waited_sec"],
                    facts["polls"],
                    facts["last_state"] or _NA_STATE,
                    "; последний обрыв опроса: %s" % facts["last_poll_error"] if facts["last_poll_error"] else "",
                )
            )
            return "timeout"

        sleep(poll)
        got = fetch_manus_task(facts["task_id"], key=key, base=base, task_path=task_path)
        facts["polls"] += 1
        facts["waited_sec"] = int(clock() - started)

        if got["transport_error"]:
            # Обрыв ОДНОГО опроса приговором не является: работа уже принята и
            # идёт на той стороне. Повторяем до предела, но последний обрыв
            # помним — он попадёт в отчёт, если предел истечёт.
            facts["last_poll_error"] = got["transport_error"]
            continue

        facts["status"] = got["status"]
        facts["body"] = got["body"]
        facts["poll_target"] = got["target"]
        if got["status"] is None or int(got["status"]) >= 400:
            return "http"  # 401/404/429 на заборе — отказ с дословным телом

        state, _text, note = manus_output_text(got["body"])
        facts["last_state"] = state
        facts["poll_note"] = note
        facts["credit_usage"] = manus_credit_usage(got["body"])

        if state == _MANUS_FAILED:
            facts["request_sent"] = True
            facts["transport_error"] = "task_failed: задача %s кончилась состоянием failed (%s)" % (
                facts.get("task_id"),
                manus_error_text(got["body"]) or "текста ошибки канал не дал",
            )
            return "failed"

        signature = manus_settled_signature(got["body"])
        if signature and signature == facts["last_signature"]:
            facts["settled_polls"] += 1
        else:
            facts["last_signature"] = signature
            facts["settled_polls"] = 0

        if state == _MANUS_DONE:
            facts["ready_by"] = "status=completed"
            return "ready"
        needed = settle_polls * (MANUS_RUNNING_QUIET_FACTOR if state == _MANUS_RUNNING else 1)
        if signature and facts["settled_polls"] >= needed:
            facts["ready_by"] = "затишье %d опросов подряд (нужно было %d) при status=%s" % (
                facts["settled_polls"],
                needed,
                state or _NA_STATE,
            )
            return "ready"


def _wait_reply(
    facts,
    *,
    marker,
    key,
    base,
    task_path,
    poll,
    deadline,
    started,
    clock,
    sleep,
    tag="",
    confirm_polls=MANUS_EMPTY_CONFIRM_POLLS,
    head=None,
):
    """Дождаться ОТВЕТА канала на конкретную часть. → "ready" | "idle" | "missing" | "timeout" | "http" | "failed".

    Между частями ждать «затишья» нельзя и не нужно. Нельзя — потому что
    `status` живой задачи неделями сидит в `running`/`pending` и о готовности
    ничего не сообщает (обе живые пробы 01.09). Не нужно — потому что здесь есть
    ПОЛОЖИТЕЛЬНЫЙ признак, а не догадка: после нашей части появилось сообщение
    ассистента с текстом, значит часть принята и можно слать следующую.

    Исходов ЧЕТЫРЕ, и они РАЗНЫЕ — свалка «ответа нет» была дефектом:

    * ``ready`` — текст ассистента после нашей метки есть. Проверяется ПЕРВЫМ, до
      всякого разбора состояния: ответ остаётся ответом, каким бы словом канал ни
      назвал состояние задачи.
    * ``idle`` — задача ЗАКОНЧЕНА (`status=completed`) и НЕМА. Терминальное
      состояние ждать больше нечего: ждать его — значит ждать, пока изменится то,
      что по доке канала уже не меняется. До 05.09.2026 этой ветки здесь не было,
      и каждый такой случай стоил полного бюджета (замер: 25 промахов, 1802–1807 с
      каждый, 12.53 ч суммарно, ответов ноль).
    * ``timeout`` — потолок ожидания исчерпан на НЕтерминальном состоянии. Это
      «канал ещё работает, а мы больше не ждём», и путать это с ``idle`` нельзя:
      там работа кончена, здесь — брошена нами, и добрать её позже ещё можно.
    * ``failed`` / ``http`` — отказ, объявленный каналом.

    Пустое НЕ становится ответом ни одной веткой: ``idle`` уезжает наверх
    отдельным фактом (``facts["idle_error"]``) и отдельным ярлыком, а не текстом.

    ``head`` — рамка части, которую мы только что послали. Задана — ни ответ, ни
    немота не судятся, пока канал не ПОКАЖЕТ эту часть в задаче (:func:`_holds_head`).
    Без замка досыл в уже закрытую задачу читал бы её ПРЕЖНЕЕ состояние: прежний
    `completed` за немоту новой части, прежнее «ПРИНЯТО» за ответ на неё. Задача
    закончена, а части в ней нет и после подтверждения — исход ``missing``
    (``facts["split_error"]``): часть до ревьюера не доехала.
    """
    facts["empty_polls"] = 0
    facts["missing_polls"] = 0
    while True:
        facts["waited_sec"] = int(clock() - started)
        if clock() >= deadline:
            facts["request_sent"] = True
            facts["poll_timeout"] = True
            facts["transport_error"] = (
                "poll_timeout: задача %s не ответила%s за %d с (опросов %d, последнее состояние %s%s)"
                % (
                    facts.get("task_id"),
                    tag,
                    facts["waited_sec"],
                    facts["polls"],
                    facts["last_state"] or _NA_STATE,
                    "; последний обрыв опроса: %s" % facts["last_poll_error"] if facts["last_poll_error"] else "",
                )
            )
            return "timeout"

        sleep(poll)
        got = fetch_manus_task(facts["task_id"], key=key, base=base, task_path=task_path)
        facts["polls"] += 1
        facts["waited_sec"] = int(clock() - started)

        if got["transport_error"]:
            facts["last_poll_error"] = got["transport_error"]
            continue

        facts["status"] = got["status"]
        facts["body"] = got["body"]
        facts["poll_target"] = got["target"]
        if got["status"] is None or int(got["status"]) >= 400:
            return "http"

        state, text, note = manus_output_text(got["body"], after_marker=marker)
        facts["last_state"] = state
        facts["poll_note"] = note
        facts["credit_usage"] = manus_credit_usage(got["body"])

        if state == _MANUS_FAILED:
            facts["request_sent"] = True
            facts["transport_error"] = "task_failed: задача %s кончилась состоянием failed (%s)" % (
                facts.get("task_id"),
                manus_error_text(got["body"]) or "текста ошибки канал не дал",
            )
            return "failed"

        if head is not None and not _holds_head(got["body"], head):
            facts["empty_polls"] = 0
            if state != _MANUS_DONE:
                facts["missing_polls"] = 0
                continue
            facts["missing_polls"] += 1
            if facts["missing_polls"] >= max(1, int(confirm_polls)):
                facts["request_sent"] = True
                facts["split_error"] = (
                    "часть %s не видна в задаче %s: задача закончена, нашего сообщения в ней нет "
                    "(подтверждено %d опросами подряд, ждали %d с; %s)"
                    % (marker, facts.get("task_id"), facts["missing_polls"], facts["waited_sec"], note)
                )
                return "missing"
            continue
        facts["missing_polls"] = 0

        # ОТВЕТ ВПЕРЁД ВСЕГО. Порядок здесь не стилистика: поменяй его местами со
        # следующей веткой — и задача, закончившаяся ВМЕСТЕ с ответом (законный и
        # частый случай), была бы объявлена немой, а живой разбор выброшен.
        if text.strip():
            return "ready"

        if state == _MANUS_DONE:
            facts["empty_polls"] += 1
            if facts["empty_polls"] >= max(1, int(confirm_polls)):
                facts["request_sent"] = True
                facts["idle_error"] = (
                    "channel_idle: задача %s закончена состоянием %s и не сказала ни слова%s "
                    "(подтверждено %d опросами подряд, ждали %d с, квитанция канала %s; %s)"
                    % (
                        facts.get("task_id"),
                        _MANUS_DONE,
                        tag,
                        facts["empty_polls"],
                        facts["waited_sec"],
                        _NA_STATE if facts.get("credit_usage") is None else facts["credit_usage"],
                        note,
                    )
                )
                return "idle"
            continue

        # Состояние нетерминальное — счётчик подтверждений обнуляется. Иначе два
        # разнесённых по времени `completed` (например, вокруг ожившей задачи)
        # сложились бы в подтверждение, которого никто не наблюдал.
        facts["empty_polls"] = 0


def send_manus(
    prompt,
    *,
    key,
    base=DEFAULT_MANUS_BASE,
    path=DEFAULT_MANUS_PATH,
    timeout=DEFAULT_TIMEOUT,
    task_path=DEFAULT_MANUS_TASK_PATH,
    wait=DEFAULT_MANUS_WAIT,
    poll=DEFAULT_MANUS_POLL,
    settle_polls=MANUS_SETTLE_POLLS,
    chunk_chars=MANUS_CHUNK_CHARS,
    profile=None,
    notice=None,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """Довести текст до ревьюера в Manus и дождаться ответа. → dict фактов (без суждений).

    Канал ставит отправщику ДВА разных предела, и путать их нельзя:

    * **5000 estimated tokens** — жёсткий: отвечает кодом 400 и телом
      ``message content must be at most 5000 estimated tokens``. Виден сразу.
    * **~2400 знаков на сообщение** — МОЛЧАЛИВЫЙ: длинное сообщение принимается
      кодом 200, но канал срезает его и прячет остаток вложением, оставляя
      ревьюеру ~1100 знаков. Вложение ревьюер не открывает (правило №1 нашего же
      конверта) и честно отвечает «пакет обрезан». Именно этот предел делал
      заход бессмысленным при честном коде 200.

    Поэтому исходящее едет ЧАСТЯМИ по ОДНОЙ задаче (`taskId` доки v1 — «for
    continuing existing tasks (multi-turn)»): часть 1 создаёт задачу, остальные
    досылаются в неё же, и между частями отправщик ДОЖИДАЕТСЯ, пока канал закроет
    ход. Склейка частей подряд равна исходному тексту знак в знак: рамка
    добавляется поверх куска, а не вместо него.

    ХОД ЗАКРЫТ — ЭТО ОТВЕТ ЛИБО НЕМОТА, НО ТОЛЬКО ПРИ ЧАСТИ, ВИДНОЙ В ЗАДАЧЕ (правка
    17.09.2026). До неё следующая часть уходила лишь после ТЕКСТА ассистента, а
    немота на промежуточной части кончала весь заход: канал с 04.09 закрывает
    задачу за 2–6 с молча (`completed`, `credit_usage: 0`), и оба живых захода
    17.09 кончились `parts 10 · parts_sent 1` — ревьюер получил первую часть с
    шапкой «придёт 10 частями», остальных девяти и трёх вопросов из хвоста (части
    9–10) не видел ни разу. Немота на промежуточной части ответа и не требует: мы сами
    просим там одно слово. Сколько частей доехало, называет КАНАЛ
    (:func:`manus_parts_seen` по `output` задачи и `task_id` в ответе на
    досылку), а не счётчик отправок: не собралось в одной задаче — ``split_error``.

    Ни одна ветка НЕ превращает «не дождались» в «готово», и ни одна не выдаёт
    оборванную досылку за целую: не ушедшая часть возвращается наверх фактом
    отказа, а не тишиной.

    ``wait=0`` выключает опрос, ``chunk_chars=0`` — деление на части.
    """
    url = base.rstrip("/") + "/" + path.lstrip("/")
    parts = manus_parts(prompt, chunk_chars)
    poll = max(1, int(poll))
    started = clock()
    deadline = started + max(0, wait)

    def post(text, task_id=None):
        # Поля `mode` нет ни в v1, ни в v2 — доке v1 нужен `prompt` (и
        # `agentProfile`, который канал по факту подставляет сам). Ключ
        # `--manus-profile` пуст сознательно: слать литерал, не проверенный
        # живым заходом, значит менять модель канала вслепую.
        payload = {"prompt": text}
        if profile:
            payload["agentProfile"] = profile
        if task_id:
            payload["taskId"] = task_id
        return manus_http(
            url,
            key=key,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            timeout=timeout,
        )

    facts = post(parts[0])
    facts["create_status"] = facts["status"]
    facts["create_body"] = facts["body"]
    facts["task_id"] = None
    facts["task_url"] = None
    facts["polls"] = 0
    facts["waited_sec"] = 0
    facts["last_state"] = None
    facts["last_poll_error"] = None
    facts["last_signature"] = None
    facts["settled_polls"] = 0
    facts["ready_by"] = None
    facts["parts"] = len(parts)
    facts["parts_sent"] = 1
    facts["part_chars"] = [len(part) for part in parts]
    facts["part_error"] = None
    # Три факта разведения исходов. Заводятся ЗДЕСЬ, а не по месту установки:
    # вызывающий читает их через `.get`, и отсутствие ключа было бы неотличимо
    # от «не случилось», а протокол захода недосчитался бы графы.
    facts["idle_error"] = None  # канал закончил и промолчал
    facts["poll_timeout"] = False  # потолок исчерпан на живой работе
    facts["empty_polls"] = 0
    facts["missing_polls"] = 0
    facts["credit_usage"] = None  # квитанция канала: None ≠ 0
    facts["split_error"] = None  # части не собрались у канала в одной задаче
    facts["parts_seen"] = None  # номера частей, которые канал показал в задаче
    facts["silent_turns"] = 0  # промежуточных частей, закрытых каналом молча
    # Код ответа на КАЖДЫЙ POST (часть 1 — создание, дальше досылки) и служебное
    # тело ответа на досылку (правка 18.09.2026). До неё протокол помнил только
    # «все досылки < 400» (`part_error` пуст), а ответ на досылку не записывался
    # вовсе: разбор 63-v не смог назвать код части k/N живым протоколом.
    facts["part_status"] = [facts["status"]]
    facts["part_replies"] = []

    # Приём не состоялся (обрыв, 4xx, 5xx) — тело ошибки уезжает наверх ДОСЛОВНО
    # и судится там же, где судилось раньше. Опрашивать нечего.
    if facts["transport_error"] or facts["status"] is None or int(facts["status"]) >= 400:
        return facts

    facts["task_id"], facts["task_url"] = manus_task_id(facts["body"])
    # Сказать вслух, что работа ПРИНЯТА, ДО начала ожидания. Без этого
    # идентификатор задачи живёт только в памяти процесса до конца захода — и
    # оборванное ожидание уносит единственный ключ, которым его можно добрать.
    if facts["task_id"] and notice:
        notice(facts, wait)
    if not facts["task_id"] or wait <= 0:
        return facts

    total = len(parts)

    def wait_part(index):
        # Ход по части закрыт, только когда канал ПОКАЗАЛ её в задаче (`head`).
        outcome = _wait_reply(
            facts,
            marker=_PART_MARK % (index, total),
            key=key,
            base=base,
            task_path=task_path,
            poll=poll,
            deadline=deadline,
            started=started,
            clock=clock,
            sleep=sleep,
            tag=" на часть %d/%d" % (index, total),
            head=_part_head(index, total),
        )
        if outcome in ("ready", "idle"):
            facts["parts_seen"] = manus_parts_seen(facts["body"], total)
        return outcome

    def split_by_channel(upto):
        # Части 1..upto обязаны стоять в задаче все: одна потерянная посередине —
        # и ревьюер склеит обрезок, ни словом об этом не узнав.
        lost = [k for k in range(1, upto + 1) if k not in (facts["parts_seen"] or [])]
        if lost:
            facts["request_sent"] = True
            facts["split_error"] = "в задаче %s канал показал %d из %d частей (нет: %s)" % (
                facts.get("task_id"),
                upto - len(lost),
                upto,
                ", ".join(str(k) for k in lost),
            )
        return bool(lost)

    for index in range(1, total):
        outcome = wait_part(index)
        if outcome == "idle":
            # Немота на ПРОМЕЖУТОЧНОЙ части — не конец захода: часть в задаче, ход
            # закрыт, а ответа на неё мы и не ждём по существу (просим одно слово).
            facts["idle_error"] = None
            facts["empty_polls"] = 0
            facts["silent_turns"] += 1
            outcome = "ready"
        if outcome != "ready" or split_by_channel(index):
            return facts

        got = post(parts[index], task_id=facts["task_id"])
        facts["parts_sent"] = index + 1
        facts["part_status"].append(got["status"])
        # Тела нет (обрыв) — так и пишется `None`: пустая строка читалась бы «канал ответил пусто».
        reply = got["body"]
        facts["part_replies"].append(reply[:MANUS_PART_REPLY_KEEP] if isinstance(reply, str) else None)
        if got["transport_error"] or got["status"] is None or int(got["status"]) >= 400:
            # Досылка сорвалась — у ревьюера ОБРЕЗОК, и звать это ответом нельзя.
            facts["status"] = got["status"]
            facts["body"] = got["body"]
            facts["transport_error"] = got["transport_error"] or facts["transport_error"]
            facts["request_sent"] = True
            facts["part_error"] = "часть %d/%d не ушла (код %s)" % (
                index + 1,
                total,
                got["status"] if got["status"] is not None else _NA_STATE,
            )
            return facts
        # Код 200 — это «запрос принят», а не «легло в ту же задачу». Канал назвал
        # в ответе другую задачу — часть ушла мимо ревьюера, дальше слать некуда.
        # Сверяется ровно поле `task_id`: форма ответа на досылку живьём не
        # записана, и `id` в ней может оказаться идентификатором СООБЩЕНИЯ.
        got_obj = _json_or_none(got["body"])
        got_id = got_obj.get("task_id") if isinstance(got_obj, dict) else None
        if isinstance(got_id, str) and got_id.strip() and got_id.strip() != facts["task_id"]:
            facts["status"] = got["status"]
            facts["body"] = got["body"]
            facts["request_sent"] = True
            facts["split_error"] = "часть %d/%d канал положил в ДРУГУЮ задачу (%s вместо %s)" % (
                index + 1,
                total,
                got_id.strip(),
                facts["task_id"],
            )
            return facts

    if total > 1:
        # Последняя часть судится тем же замком: иначе прежний `completed` задачи
        # был бы принят за готовность ответа на часть, которой канал ещё не видел.
        outcome = wait_part(total)
        if outcome in ("ready", "idle") and split_by_channel(total):
            facts["idle_error"] = None
            return facts
        if outcome != "ready":
            return facts

    outcome = _wait_settled(
        facts,
        key=key,
        base=base,
        task_path=task_path,
        poll=poll,
        settle_polls=settle_polls,
        deadline=deadline,
        started=started,
        clock=clock,
        sleep=sleep,
        tag=" после последней части" if len(parts) > 1 else "",
    )
    if outcome != "ready":
        return facts

    if total > 1:
        facts["parts_seen"] = manus_parts_seen(facts["body"], total)
        if split_by_channel(total):
            return facts

    marker = _PART_MARK % (total, total) if total > 1 else None
    state, text, note = manus_output_text(facts["body"], after_marker=marker)
    facts["last_state"] = state
    facts["poll_note"] = note
    # Наверх уезжает СОБРАННЫЙ текст ассистента: он и есть ответ канала, и он
    # ляжет в лоток дословно. Пустой разбор телом не подменяем — пусть
    # классификатор увидит сырой конверт и назовёт это «принято, ответа нет».
    facts["answer_from_output"] = bool(text.strip())
    if text.strip():
        facts["body"] = text
        return facts

    # Задача КОНЧЕНА и нема — тот же исход, что и между частями, и звать его надо
    # тем же словом. Без этой ветки хвост захода отдавал бы `accepted_no_answer`
    # («принял, ответа нет»), то есть ОПЛАЧЕННЫЙ заход, который слой повтора
    # честно попытался бы добрать ЗАБОРОМ, — а забирать из терминальной пустой
    # задачи нечего и не станет никогда.
    if state == _MANUS_DONE:
        facts["idle_error"] = (
            "channel_idle: задача %s закончена состоянием %s и не сказала ни слова "
            "(ждали %d с, опросов %d, квитанция канала %s; %s)"
            % (
                facts.get("task_id"),
                _MANUS_DONE,
                facts["waited_sec"],
                facts["polls"],
                _NA_STATE if facts.get("credit_usage") is None else facts["credit_usage"],
                note,
            )
        )
    return facts


# ───────────────────────────── заход ─────────────────────────────


def _sha256_bytes_of_text(text):
    return review_send._sha256_text(text)


def _announce_manus_task(facts, wait):
    """Объявить принятую задачу немедленно. Печать со сбросом буфера.

    Сброс не украшение: вывод захода почти всегда перенаправлен в файл, а там
    поток блочный — без flush строка «задача принята» проявилась бы только в
    конце ожидания, то есть ровно тогда, когда она уже не нужна.
    """
    sys.stdout.write(
        "ЗАДАЧА ПРИНЯТА: %s · %s · ждём готовности до %d с\n"
        % (facts.get("task_id"), facts.get("task_url") or _NA_STATE, wait)
    )
    sys.stdout.flush()


def run_channel(channel, prompt, ctx, args):
    """Один канал: отправить, принять, вынести вердикт. → (verdict, answer)."""
    common = dict(
        pack_name=ctx["pack_name"],
        pack_sha256=ctx["pack_sha256"],
        prompt_sha256=ctx["prompt_sha256"],
        send_date=ctx["send_date"],
        min_chars=args.min_chars,
    )
    if channel == "codex":
        facts = send_codex(
            prompt,
            root=ctx["root"],
            workdir=ctx["workdir"],
            binary=args.codex_bin,
            model=args.codex_model,
            cd=args.codex_cd,
            timeout=args.timeout,
        )
        return review_send.classify_codex(
            channel_target=facts["target"],
            launch_error=facts["launch_error"],
            timed_out=facts["timed_out"],
            returncode=facts["returncode"],
            stdout=facts["stdout"],
            stderr=facts["stderr"],
            last_message=facts["last_message"],
            last_message_error=facts["last_message_error"],
            **common
        )

    key = os.environ.get(args.key_env, "").strip()
    url = args.manus_base.rstrip("/") + "/" + args.manus_path.lstrip("/")
    if not key:
        return review_send.classify_manus(
            channel_target=url, credentials_present=False, key_env_name=args.key_env, **common
        )
    # getattr с дефолтом, а не `args.manus_wait`: этот же `run_channel` зовёт
    # автосборка ступени A со СВОИМ объектом настроек (`review_auto_run._ChannelArgs`),
    # который про новые ключи не знает. Прямое обращение уронило бы весь
    # автоматический контур на AttributeError в первом же обороте.
    facts = send_manus(
        prompt,
        key=key,
        base=args.manus_base,
        path=args.manus_path,
        timeout=args.timeout,
        task_path=getattr(args, "manus_task_path", DEFAULT_MANUS_TASK_PATH),
        wait=getattr(args, "manus_wait", DEFAULT_MANUS_WAIT),
        poll=getattr(args, "manus_poll", DEFAULT_MANUS_POLL),
        settle_polls=getattr(args, "manus_settle", MANUS_SETTLE_POLLS),
        chunk_chars=getattr(args, "manus_chunk", MANUS_CHUNK_CHARS),
        profile=getattr(args, "manus_profile", None),
        notice=_announce_manus_task,
    )
    # Протокол захода — рядом с ответом, в рабочий каталог (tmp/, вне git): в
    # лотке живёт ОТВЕТ, а это переписка с каналом. Ключа в нём нет ни одной
    # строкой: заголовки сюда не кладутся вовсе.
    try:
        write_text(
            os.path.join(ctx["workdir"], "manus_protocol.json"),
            json.dumps(facts, ensure_ascii=False, indent=2, sort_keys=True),
        )
    except (OSError, TypeError, ValueError):
        pass  # протокол — удобство, а не доказательство: его потеря вердикта не меняет
    return review_send.classify_manus(
        channel_target=facts["target"],
        request_sent=facts["request_sent"],
        transport_error=facts["transport_error"],
        status=facts["status"],
        body=facts["body"],
        idle_error=facts.get("idle_error"),
        poll_timeout=facts.get("poll_timeout", False),
        credit_usage=facts.get("credit_usage"),
        split_error=facts.get("split_error"),
        **common
    )


def fetch_only(args):
    """Режим `--manus-task <id>`: только забрать результат уже созданной задачи. → код возврата.

    Он же отрицательная проба забора: несуществующий идентификатор ОБЯЗАН дать
    НАЗВАННЫЙ отказ, а не тишину. Поэтому вердикт здесь выносит тот же чистый
    классификатор, что и в боевом заходе, — своей ветки суждений у диагностики
    нет и быть не должно, иначе два пути начнут расходиться в оценках.
    """
    send_date = args.date or datetime.date.today().isoformat()
    common = dict(
        pack_name="(забор задачи %s)" % args.manus_task,
        pack_sha256=None,
        prompt_sha256=None,
        send_date=send_date,
        min_chars=args.min_chars,
    )
    key = os.environ.get(args.key_env, "").strip()
    url = args.manus_base.rstrip("/") + "/" + args.manus_task_path.lstrip("/").replace(
        "{task_id}", urllib.parse.quote(str(args.manus_task), safe="")
    )
    if not key:
        verdict, _answer = review_send.classify_manus(
            channel_target=url, credentials_present=False, key_env_name=args.key_env, **common
        )
    else:
        got = fetch_manus_task(args.manus_task, key=key, base=args.manus_base, task_path=args.manus_task_path)
        state, text, note = manus_output_text(got["body"])
        sys.stdout.write("ЗАБОР ЗАДАЧИ: %s\n" % got["target"])
        sys.stdout.write(
            "КОД: %s · %s%s\n"
            % (
                got["status"] if got["status"] is not None else _NA_STATE,
                note,
                " · обрыв: %s" % got["transport_error"] if got["transport_error"] else "",
            )
        )
        sys.stdout.write("ТЕЛО ДОСЛОВНО:\n%s\n" % (got["body"] if got["body"] is not None else "— тела нет"))
        verdict, _answer = review_send.classify_manus(
            channel_target=got["target"],
            request_sent=got["request_sent"],
            transport_error=got["transport_error"],
            status=got["status"],
            body=text if (state == _MANUS_DONE and text.strip()) else got["body"],
            credit_usage=manus_credit_usage(got["body"]),
            **common
        )
    sys.stdout.write("ИСХОД: %s (%s) — %s\n" % (verdict["title"], verdict["reason"], verdict["detail"]))
    return {"answered": 0, "refused": 3, "unknown": 4}[verdict["outcome"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Отправить пакет второго мнения в каналы и принять ответы.")
    parser.add_argument("--pack", default=None, help="файл пакета из docs/review_outbox")
    parser.add_argument(
        "--channel",
        action="append",
        default=None,
        help="канал (%s); можно повторять; по умолчанию все" % "|".join(review_send.CHANNELS),
    )
    parser.add_argument("--root", default=HERE)
    parser.add_argument("--inbox", default=None, help="каталог входящего лотка (по умолчанию docs/review_inbox)")
    parser.add_argument("--workdir", default=None, help="рабочий каталог отправщика (по умолчанию tmp/review_send)")
    parser.add_argument("--date", default=None, help="день отправки ГГГГ-ММ-ДД (по умолчанию сегодня)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="бюджет времени на канал, с")
    parser.add_argument("--min-chars", type=int, default=review_send.ANSWER_MIN_CHARS)
    parser.add_argument("--codex-bin", default=None)
    parser.add_argument("--codex-model", default=None)
    parser.add_argument("--codex-cd", default=None)
    parser.add_argument("--manus-base", default=DEFAULT_MANUS_BASE)
    parser.add_argument("--manus-path", default=DEFAULT_MANUS_PATH, help="путь приёма работы (создание задачи)")
    parser.add_argument(
        "--manus-task-path", default=DEFAULT_MANUS_TASK_PATH, help="путь забора результата, с меткой {task_id}"
    )
    parser.add_argument(
        "--manus-wait", type=int, default=DEFAULT_MANUS_WAIT, help="предел ожидания готовности задачи, с (0 — не ждать)"
    )
    parser.add_argument("--manus-poll", type=int, default=DEFAULT_MANUS_POLL, help="шаг опроса задачи, с")
    parser.add_argument(
        "--manus-settle",
        type=int,
        default=MANUS_SETTLE_POLLS,
        help="сколько опросов подряд задача должна не меняться, чтобы считаться сдавшей работу",
    )
    parser.add_argument(
        "--manus-chunk",
        type=int,
        default=MANUS_CHUNK_CHARS,
        help="потолок ОДНОГО сообщения в знаках; длиннее — досылка частями (0 — не делить)",
    )
    parser.add_argument("--manus-profile", default=None, help="agentProfile канала (по умолчанию не слать вовсе)")
    parser.add_argument(
        "--manus-task",
        default=None,
        help="ТОЛЬКО забрать результат уже созданной задачи по идентификатору (пакет не нужен)",
    )
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV, help="имя переменной окружения с ключом Manus")
    parser.add_argument("--journal", action="store_true", help="дописать строку-индекс в журнал")
    parser.add_argument("--dry", action="store_true", help="собрать и проверить исходящее, наружу НЕ ходить")
    args = parser.parse_args(argv)

    # Забор результата — отдельный путь: пакета он не читает, файлов в лоток не
    # пишет и каналов не выбирает. Стоит ПЕРВЫМ, чтобы не требовать пакета там,
    # где его нет по смыслу.
    if args.manus_task:
        return fetch_only(args)

    if not args.pack:
        sys.stderr.write("НЕ НАЗВАН ПАКЕТ: нужен --pack (или --manus-task <id> для забора созданной задачи)\n")
        return 2

    channels = args.channel or list(review_send.CHANNELS)
    bad = [c for c in channels if c not in review_send.CHANNELS]
    if bad:
        sys.stderr.write("НЕИЗВЕСТНЫЙ КАНАЛ: %s (есть: %s)\n" % (", ".join(bad), ", ".join(review_send.CHANNELS)))
        return 2

    try:
        pack_text = read_text(args.pack)
    except OSError as exc:
        sys.stderr.write("ПАКЕТ НЕ ПРОЧИТАН: %s\n" % exc)
        return 2

    try:
        prompt = review_send.build_prompt(pack_text)
    except review_send.ReviewSendError as exc:
        sys.stderr.write("ПАКЕТ НЕДЕЙСТВИТЕЛЕН: %s\n" % exc)
        return 2

    if len(prompt) > review_send.PROMPT_MAX_CHARS:
        sys.stderr.write(
            "ИСХОДЯЩЕЕ ДЛИННЕЕ ПОТОЛКА: %d знаков при %d — это не пакет из лотка\n"
            % (len(prompt), review_send.PROMPT_MAX_CHARS)
        )
        return 2

    send_date = args.date or datetime.date.today().isoformat()
    inbox_dir = args.inbox or os.path.join(args.root, *DEFAULT_INBOX.split("/"))
    workdir = args.workdir or os.path.join(args.root, *DEFAULT_WORKDIR.split("/"))
    ctx = {
        "root": args.root,
        "workdir": workdir,
        "pack_name": os.path.basename(args.pack),
        "pack_sha256": _sha256_bytes_of_text(pack_text),
        "prompt_sha256": _sha256_bytes_of_text(prompt),
        "send_date": send_date,
    }
    pack_rel = os.path.relpath(os.path.abspath(args.pack), args.root).replace(os.sep, "/")

    # Стража исходящего — ДО выбора канала и до любой сети. Находка здесь
    # означает, что наружу не уйдёт ничего ни одним каналом.
    violations = review_send.outbound_violations(prompt)
    sys.stdout.write(
        "ИСХОДЯЩЕЕ: %s (%d знаков, sha %s), стража: %s\n"
        % (
            ctx["pack_name"],
            len(prompt),
            ctx["prompt_sha256"][:12],
            "чисто" if not violations else "ЗАДЕРЖАНО (%s)" % ", ".join(sorted({v["kind"] for v in violations})),
        )
    )

    # Сухой прогон кончается ЗДЕСЬ и файлов не пишет. Соблазн выдать в лоток
    # «вердикт сухого прогона» отвергнут сознательно: файл в лотке означает
    # «канал ответил вот это», а сухой прогон канала не спрашивал вовсе —
    # такой файл был бы четвёртым исходом, которого у контура нет.
    if args.dry:
        sys.stdout.write(
            "СУХОЙ ПРОГОН: наружу не отправлено ничего, файлов не написано; каналы: %s\n" % ", ".join(channels)
        )
        return 3 if violations else 0

    outcomes = []
    for channel in channels:
        if violations:
            verdict = review_send.refused_by_guard(
                channel=channel,
                pack_name=ctx["pack_name"],
                pack_sha256=ctx["pack_sha256"],
                prompt_sha256=ctx["prompt_sha256"],
                send_date=send_date,
                violations=violations,
            )
            answer = ""
        else:
            verdict, answer = run_channel(channel, prompt, ctx, args)

        path = os.path.join(inbox_dir, review_send.answer_filename(ctx["pack_name"], channel, send_date))
        write_text(path, review_send.render_answer(verdict, answer, pack_rel=pack_rel))
        rel = os.path.relpath(path, args.root).replace(os.sep, "/")
        line = review_send.index_line(verdict, rel)
        outcomes.append(verdict["outcome"])

        sys.stdout.write(
            "КАНАЛ %s: %s (%s) · ответ %d знаков · цена %s %s → %s\n"
            % (
                channel,
                verdict["title"],
                verdict["reason"],
                verdict["answer_chars"],
                "—" if verdict.get("cost_value") is None else verdict["cost_value"],
                verdict.get("cost_unit") or "",
                rel,
            )
        )
        sys.stdout.write("ИНДЕКС: %s\n" % line)

        if args.journal:
            done = subprocess.run(
                journal_argv(repo=HERE),
                input=line.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            sys.stdout.write(
                "ЖУРНАЛ: код %d %s\n" % (done.returncode, done.stdout.decode("utf-8", "replace").strip())
            )
            if done.returncode != 0:
                sys.stderr.write(done.stderr.decode("utf-8", "replace"))

    if "refused" in outcomes:
        return 3
    if "unknown" in outcomes:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
