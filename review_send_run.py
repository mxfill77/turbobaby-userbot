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


def manus_output_text(body, roles=("assistant",)):
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
        "состояние %s, сообщений %d (чужих ролей пропущено %d), текстовых кусков %d"
        % (state or _NA_STATE, len(messages), skipped, len(parts)),
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
    profile=None,
    notice=None,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """Создать задачу в Manus и ДОЖДАТЬСЯ результата. → dict фактов (без суждений).

    Канал АСИНХРОНЕН, и это не мелочь реализации, а его контракт: `POST /v1/tasks`
    отдаёт код 200 и `task_id` — приём работы, а не ответ. Ровно на этом
    отправщик и стоял до сегодня: 200 без текста уезжал наверх как
    «принято, ответа нет» (`accepted_no_answer`) и выглядел исправной отправкой
    с вечно пустым лотком.

    Ни одна ветка НЕ превращает «не дождались» в «готово». Истёкшее ожидание
    уезжает наверх ОБРЫВОМ ПОСЛЕ ОТПРАВКИ (``transport_error`` при
    ``request_sent=True``) — а такой факт классификатор зовёт «неизвестно» и
    только им; текст обрыва начинается словом ``poll_timeout`` и несёт
    идентификатор задачи, число опросов и последнее увиденное состояние, чтобы
    заход можно было добрать руками (`--manus-task <id>`).

    `wait=0` выключает опрос целиком — тогда поведение ровно прежнее.
    """
    url = base.rstrip("/") + "/" + path.lstrip("/")
    # Тело приёма. Поля `mode` нет ни в v1, ни в v2 — оно уехало: доке v1 нужен
    # `prompt` (и `agentProfile`, который канал по факту подставляет сам). Ключ
    # `--manus-profile` оставлен пустым сознательно: слать литерал, который на
    # этой полосе не проверен живым заходом, значит менять модель канала вслепую.
    payload = {"prompt": prompt}
    if profile:
        payload["agentProfile"] = profile

    facts = manus_http(
        url, key=key, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST", timeout=timeout
    )
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

    # Приём не состоялся (обрыв, 4xx, 5xx) — тело ошибки уезжает наверх ДОСЛОВНО
    # и судится там же, где судился раньше. Опрашивать нечего.
    if facts["transport_error"] or facts["status"] is None or int(facts["status"]) >= 400:
        return facts

    facts["task_id"], facts["task_url"] = manus_task_id(facts["body"])
    # Сказать вслух, что работа ПРИНЯТА, ДО начала ожидания. Без этого
    # идентификатор задачи живёт только в памяти процесса до самого конца
    # захода — и оборванное ожидание (или убитый процесс) уносит с собой
    # единственный ключ, которым заход можно добрать: `--manus-task <id>`.
    if facts["task_id"] and notice:
        notice(facts, wait)
    if not facts["task_id"] or wait <= 0:
        return facts

    poll = max(1, int(poll))
    task_id = facts["task_id"]
    started = clock()
    while True:
        if clock() - started >= wait:
            facts["waited_sec"] = int(clock() - started)
            facts["request_sent"] = True
            facts["transport_error"] = (
                "poll_timeout: задача %s не дошла до терминального состояния за %d с "
                "(опросов %d, последнее состояние %s%s)"
                % (
                    task_id,
                    facts["waited_sec"],
                    facts["polls"],
                    facts["last_state"] or _NA_STATE,
                    "; последний обрыв опроса: %s" % facts["last_poll_error"] if facts["last_poll_error"] else "",
                )
            )
            return facts

        sleep(poll)
        got = fetch_manus_task(task_id, key=key, base=base, task_path=task_path)
        facts["polls"] += 1
        facts["waited_sec"] = int(clock() - started)

        if got["transport_error"]:
            # Обрыв ОДНОГО опроса приговором не является: работа уже принята и
            # идёт на той стороне. Повторяем до предела ожидания, но последний
            # обрыв помним — он попадёт в отчёт, если предел истечёт.
            facts["last_poll_error"] = got["transport_error"]
            continue

        facts["status"] = got["status"]
        facts["body"] = got["body"]
        facts["poll_target"] = got["target"]
        if got["status"] is None or int(got["status"]) >= 400:
            return facts  # 401/404/429 на заборе — отказ с дословным телом

        state, text, note = manus_output_text(got["body"])
        facts["last_state"] = state
        facts["poll_note"] = note

        if state == _MANUS_FAILED:
            facts["request_sent"] = True
            facts["transport_error"] = "task_failed: задача %s кончилась состоянием failed (%s)" % (
                task_id,
                manus_error_text(got["body"]) or "текста ошибки канал не дал",
            )
            return facts

        signature = manus_settled_signature(got["body"])
        if signature and signature == facts["last_signature"]:
            facts["settled_polls"] += 1
        else:
            facts["last_signature"] = signature
            facts["settled_polls"] = 0

        ready_by = None
        if state == _MANUS_DONE:
            ready_by = "status=completed"
        elif signature and facts["settled_polls"] >= settle_polls:
            ready_by = "затишье %d опросов подряд при status=%s" % (facts["settled_polls"], state or _NA_STATE)

        if ready_by:
            # Наверх уезжает СОБРАННЫЙ текст ассистента: он и есть ответ канала,
            # и он ляжет в лоток дословно. Пустой разбор телом не подменяем —
            # пусть классификатор увидит сырой конверт и назовёт это «принято,
            # ответа нет», а не «готово».
            facts["ready_by"] = ready_by
            facts["answer_from_output"] = bool(text.strip())
            if text.strip():
                facts["body"] = text
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
