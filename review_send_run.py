"""Руки ступени 2 ревью-контура: отправить пакет в канал и принять ответ.

Разделение то же, что у ступени 1 (`review_pack` / `review_pack_build`): вся
логика — в чистом :mod:`review_send`, здесь только ввод-вывод — подпроцесс CLI,
HTTP, чтение пакета, запись ответа в `docs/review_inbox/` и (по явному ключу
`--journal`) одна строка-индекс через штатного писателя журнала.

    venv/Scripts/python.exe review_send_run.py --pack docs/review_outbox/<файл>.md
    venv/Scripts/python.exe review_send_run.py --pack … --channel codex --journal
    venv/Scripts/python.exe review_send_run.py --pack … --dry        # без сети вовсе

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
import urllib.error
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

DEFAULT_TIMEOUT = 900

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


def send_manus(
    prompt,
    *,
    key,
    base=DEFAULT_MANUS_BASE,
    path=DEFAULT_MANUS_PATH,
    timeout=DEFAULT_TIMEOUT,
):
    """Отправить текст в Manus по HTTP. → dict фактов захода (без суждений).

    ЧЕСТНАЯ ГРАНИЦА: живой формат ответа этого канала полосой НЕ СНЯТ — ключа на
    ПК нет, и ни одного настоящего ответа Manus здесь никто не видел. Поэтому
    тело разбирается по нескольким общеупотребимым именам полей (см.
    `review_send.extract_manus_answer`), а адрес и путь вынесены в ключи: первый
    живой заход с ключом почти наверняка потребует ОДНОЙ правки контракта, и она
    должна быть правкой ключа запуска, а не кода.
    """
    url = base.rstrip("/") + "/" + path.lstrip("/")
    facts = {"target": url, "request_sent": False, "transport_error": None, "status": None, "body": None}

    payload = json.dumps({"prompt": prompt, "mode": "quality"}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "API_KEY": key,
            "Authorization": "Bearer %s" % key,
            "User-Agent": "turbobaby-review-send/1",
        },
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


# ───────────────────────────── заход ─────────────────────────────


def _sha256_bytes_of_text(text):
    return review_send._sha256_text(text)


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
    facts = send_manus(prompt, key=key, base=args.manus_base, path=args.manus_path, timeout=args.timeout)
    return review_send.classify_manus(
        channel_target=facts["target"],
        request_sent=facts["request_sent"],
        transport_error=facts["transport_error"],
        status=facts["status"],
        body=facts["body"],
        **common
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Отправить пакет второго мнения в каналы и принять ответы.")
    parser.add_argument("--pack", required=True, help="файл пакета из docs/review_outbox")
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
    parser.add_argument("--manus-path", default=DEFAULT_MANUS_PATH)
    parser.add_argument("--key-env", default=DEFAULT_KEY_ENV, help="имя переменной окружения с ключом Manus")
    parser.add_argument("--journal", action="store_true", help="дописать строку-индекс в журнал")
    parser.add_argument("--dry", action="store_true", help="собрать и проверить исходящее, наружу НЕ ходить")
    args = parser.parse_args(argv)

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
