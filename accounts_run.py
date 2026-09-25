# -*- coding: utf-8 -*-
"""
accounts_run.py — РУКИ РЕЕСТРА УЧЁТОК: «учётки» и «учётка N» с телефона, заведение учётки с ПК
(25.09.2026, задание Штаба «учётки одним словом»).

Решение (что такое реестр, какой профиль у строителей) живёт в `accounts.py` — его импортирует
демон. Здесь — всё, что ходит наружу: живые пробы профилей ПК (`claude -p` под каталогом профиля),
пробы слотов сервера и переключение серверного демона (`vps_token_install`, ssh). Демон этот модуль
НЕ импортирует: замыкание демона не растёт ни на `subprocess`, ни на ssh.

ЧТО ВИДИТ ВЛАДЕЛЕЦ И ОТКУДА КАЖДОЕ СЛОВО:
  «учётки»    → по ОДНОЙ живой пробе на каждый профиль ПК из реестра и на действующее и КАЖДЫЙ слот
                сервера; ответ поставщика (200/429/401), время сброса ЕГО словами, что действует у
                строителей ПК (реестр), у RC (свой файл) и у демона сервера (равенство значений,
                посчитанное на сервере). Итог каждой пробы ложится в `accounts_last.json` — это и
                есть «последний ответ поставщика».
  «учётка N»  → ПК: проба профиля N; 200 → номер строителей в реестре = N (файл вне git: дерево
                не пачкается, авто-фетч не встаёт); иначе строители остаются где были. Сервер:
                `vps_token_install.use_slot` (копия с датой, проба, при зелёном ОДИН рестарт, отказ —
                возврат копии, идёт заход — стоп словами). Слово владельца = разрешение на один
                рестарт демона сервера; второго рестарта «обратно» ход не делает — проба стоит до
                рестарта. RC этим словом не трогается ни одной веткой.
АВТОПЕРЕКЛЮЧЕНИЯ ПО ЛИМИТУ НЕТ: этот модуль зовут только слово владельца и его консоль.

СЕКРЕТЫ. Токенов и `.credentials.json` модуль не читает вовсе: проба профиля — это запуск CLI с
`CLAUDE_CONFIG_DIR`, а наружу идут только слово исхода, статус, время сброса и текст поставщика
(конверт CLI значений входа не содержит, замер 71r), пропущенный через `exit_evidence.scrub`.

ЗАПУСК:
    venv\\Scripts\\python.exe accounts_run.py --report          # «учётки»
    venv\\Scripts\\python.exe accounts_run.py --switch 3        # «учётка 3»
    venv\\Scripts\\python.exe accounts_run.py --show            # реестр без проб, без сети
    venv\\Scripts\\python.exe accounts_run.py --set 4 --profile D:\\claude_profile_4 --slot C --label четвёртая
    venv\\Scripts\\python.exe accounts_run.py --builders 3      # только номер строителей ПК, без сервера
    venv\\Scripts\\python.exe accounts_run.py --screen          # экран владельца: как завести учётку N
"""

import argparse
import os
import shutil
import subprocess
import tempfile
import time

import accounts
import exit_evidence
import io_utf8
import profile_choice
import vps_token_install as vti

REPO = accounts.REPO
WORK = vti.UCHETKI_WORK                     # tmp/uchetki_2509 — временное место, названо заданием
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Модель пробы профиля ПК = модель исполнителя полосы (`pc_orchestrator.EXECUTOR_MODEL`): лимит
# учётки бывает раздельным по моделям, и проба дешёвой моделью зеленела бы там, где строители
# упрутся. Равенство держит тест (по исходнику демона, без его импорта).
PROBE_MODEL = os.environ.get("ACCOUNTS_PROBE_MODEL", "claude-opus-5-5")
PROBE_TIMEOUT = int(os.environ.get("ACCOUNTS_PROBE_TIMEOUT", "240"))
PROBE_PROMPT = "Reply with exactly: OK"

# У RC свой выбор (задание 25.09): файл рядом с реестром, по умолчанию — ОСНОВНОЙ. Имя обязано
# совпадать с `rc_supervisor.RC_CHOICE_REL` — равенство держит тест (импорт rc_supervisor здесь
# недопустим: он вешает ротируемый лог на файл, который держит живой супервизор).
RC_CHOICE_REL = "rc_profile_choice.txt"

# Флаги пробы гарда — тот же список, что чистит демон (`pc_orchestrator._run_task_impl`).
_TEST_FLAGS = ("PRETOOL_TEST_RUN", "ORCH_TEST_MODE", "PRETOOL_NOPUSH", "PYTEST_CURRENT_TEST")

PC_UNKNOWN = u"неизвестно"


# ═══════════════════ проба профиля ПК ═══════════════════

def find_claude(which=None, isfile=None):
    """CLI claude: PATH-шим → вечный нативный шим ~/.local/bin. Нет → None (проба «неизвестно»).
    Урезанное зеркало `rc_supervisor.resolve_claude` (его импорт вешает лог-обработчик)."""
    _which = which or shutil.which
    _isf = isfile or os.path.isfile
    got = _which("claude")
    if got:
        return got
    home = os.path.expanduser("~")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude.cmd")):
        if _isf(c):
            return c
    return None


def probe_env(profile, base=None):
    """Окружение пробы профиля: ровно то, с чем пошёл бы ребёнок строителей под этой учёткой.
    ОСНОВНОЙ → ключ снят; путь → ключ = путь. Пустого значения не ставит ни одна ветка."""
    env = dict(os.environ if base is None else base)
    env.pop("ANTHROPIC_API_KEY", None)           # подписка, а не платный ключ — как у демона
    env.pop("OPENAI_API_KEY", None)
    for flag in _TEST_FLAGS:
        env.pop(flag, None)
    if profile == accounts.WORD_MAIN or not profile:
        env.pop(accounts.KEY, None)
    else:
        env[accounts.KEY] = profile
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _row(word, code=None, reset=u"", text=u"", called=False):
    return {"word": word, "code": code, "reset": reset, "text": text, "called": called,
            "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}


def pc_probe(profile, runner=None, claude=None, isdir=None, base_env=None):
    """ОДИН дешёвый вызов под профилем → строка {word, code, reset, text, called, at}.

    Нейтральный cwd (системный temp) + `--setting-sources project`: ни проектных, ни
    пользовательских настроек, значит ни хуков, ни карточек — из пробы наружу к человеку не уходит
    ничего. `--no-session-persistence`: транскрипта в профиле проба не оставляет."""
    isdir = isdir or os.path.isdir
    if profile != accounts.WORD_MAIN:
        try:
            if not isdir(profile):
                return _row(u"нет каталога", text=u"каталога профиля «%s» нет" % profile)
        except Exception as e:                        # noqa: BLE001
            return _row(PC_UNKNOWN, text=u"каталог не проверить (%s)" % type(e).__name__)
    exe = claude if claude is not None else find_claude()
    if not exe:
        return _row(PC_UNKNOWN, text=u"claude не найден на ПК — пробы не было")
    cmd = [exe, "-p", PROBE_PROMPT, "--model", PROBE_MODEL, "--output-format", "json",
           "--no-session-persistence", "--setting-sources", "project"]
    run = runner or subprocess.run
    try:
        p = run(cmd, cwd=tempfile.gettempdir(), env=probe_env(profile, base_env),
                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=PROBE_TIMEOUT, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return _row(PC_UNKNOWN, text=u"claude не ответил за %d с" % PROBE_TIMEOUT)
    except Exception as e:                            # noqa: BLE001 — не запустился = не знаем
        return _row(PC_UNKNOWN, text=u"claude не запустился (%s)" % type(e).__name__)
    rc = getattr(p, "returncode", 255)
    out = (getattr(p, "stdout", "") or "") + "\n" + (getattr(p, "stderr", "") or "")
    word, why = vti.verdict_of_probe(rc, out)
    data = vti.parse_envelope(out)
    code = 200 if word == vti.GREEN else vti.status_of(data, out)
    if code is None and word == vti.LIMIT:
        code = 429
    reset = vti.reset_of(data, out) if code == 429 else u""
    text = u"%s" % (data.get("result", u"") if isinstance(data, dict) else why)
    return _row(word, code, reset, exit_evidence.scrub(vti._squeeze(text, 200)), True)


# ═══════════════════ чистые сборщики слов ═══════════════════

def rc_choice_words(repo=None, isdir=None):
    """Что действует у RC: его СОБСТВЕННЫЙ файл. Нет файла → ОСНОВНОЙ (умолчание RC)."""
    path = os.path.join(repo or REPO, RC_CHOICE_REL)
    raw, err = profile_choice.read_raw(path=path)
    if raw is None and not err:
        return u"%s (свой выбор не задан — умолчание RC)" % accounts.WORD_MAIN
    d = profile_choice.decide(raw, err, isdir)
    if d.action == profile_choice.ACT_DROP:
        return u"%s (свой файл %s)" % (accounts.WORD_MAIN, RC_CHOICE_REL)
    if d.action == profile_choice.ACT_SET:
        return u"%s (свой файл %s)" % (d.value, RC_CHOICE_REL)
    return u"%s (свой файл не разобран: %s — RC-страховка)" % (accounts.WORD_MAIN, d.reason)


def _code_words(row):
    if not row:
        return u"проб не было"
    code = row.get("code")
    head = u"%s" % code if code is not None else row.get("word") or PC_UNKNOWN
    if code == 200:
        head = u"200 жива"
    elif code == 429:
        head = u"429 лимит"
    elif code == 401:
        head = u"401 вход не принят"
    tail = (u", сброс «%s»" % row["reset"]) if row.get("reset") else u""
    return head + tail


def srv_map(rows):
    """Строки пробы сервера → ({буква: строка}, [буквы, чьё значение = действующему], строка действующего)."""
    by_letter, active_letters, active_row = {}, [], None
    for r in rows or []:
        name = r.get("name") or u""
        if name == vti.NAME_ACTIVE:
            active_row = r
            continue
        if name.startswith(vti.SLOT_PREFIX):
            letter = name[len(vti.SLOT_PREFIX):]
            by_letter[letter] = r
            if r.get("active") == "1":
                active_letters.append(letter)
    return by_letter, sorted(active_letters), active_row


def report_text(reg, pc_rows, srv, choice, rc_words, now=None):
    """Реестр + пробы → ответ «учётки». Чистая функция (всё, что нужно, приходит параметрами)."""
    now = now or time.strftime("%H:%M UTC", time.gmtime())
    out = [u"👥 Учётки — живые пробы, %s" % now]
    if reg.state != accounts.ST_OK:
        out.append(u"⚠️ %s — строители на %s (WARNING). Как завести: «accounts_run.py --screen» на ПК."
                   % (reg.reason, accounts.WORD_MAIN))
    for err in reg.errors:
        out.append(u"⚠️ реестр: %s" % err)
    by_letter, active_letters, active_row = srv_map(srv.get("rows"))
    owner_of = {}
    for n, row in sorted(reg.data["accounts"].items()):
        pc = pc_rows.get(n)
        if row["slot"]:
            owner_of[row["slot"]] = n
            sr = by_letter.get(row["slot"])
            if not srv.get("ok"):
                s_words = u"слот %s — сервер не ответил" % row["slot"]
            elif sr is None:
                s_words = u"слот %s — в файле сервера его нет" % row["slot"]
            elif sr.get("word") == u"пуст":
                s_words = u"слот %s — пуст" % row["slot"]
            else:
                s_words = u"слот %s: %s%s" % (row["slot"], _code_words(sr),
                                             u" ✓действует" if row["slot"] in active_letters else u"")
        else:
            s_words = u"слота нет"
        out.append(u"№%d «%s» · ПК %s: %s · сервер %s"
                   % (n, row["label"], row["profile"], _code_words(pc), s_words))
    if reg.state != accounts.ST_OK and "main" in pc_rows:
        out.append(u"без реестра · ПК %s: %s" % (accounts.WORD_MAIN, _code_words(pc_rows["main"])))
    stray = sorted(set(by_letter) - set(owner_of))
    for letter in stray:
        out.append(u"слот %s вне реестра: %s%s" % (letter, _code_words(by_letter[letter]),
                                                   u" ✓действует" if letter in active_letters else u""))
    b = (u"№%d" % choice.number) if (choice.number is not None and not choice.warn) else accounts.WORD_MAIN
    if choice.warn:
        b += u" (WARNING: %s)" % choice.reason
    if not srv.get("ok"):
        s_act = u"не проверено — %s" % (srv.get("words") or u"сервер не ответил")
    elif active_letters:
        s_act = u", ".join(u"слот %s%s" % (x, (u" (№%d)" % owner_of[x]) if x in owner_of else u"")
                           for x in active_letters)
        s_act += u", проба действующего: %s" % _code_words(active_row)
    else:
        s_act = u"значение действующего не совпало ни с одним слотом, проба: %s" % _code_words(active_row)
    out.append(u"ДЕЙСТВУЕТ: строители ПК — %s · RC — %s · сервер — %s" % (b, rc_words, s_act))
    if srv.get("ok") and srv.get("busy"):
        out.append(u"на сервере сейчас идёт заход (%d) — «учётка N» рестарт отложит словами" % srv["busy"])
    return u"\n".join(out)


# ═══════════════════ ходы ═══════════════════

def report(runner=None, srv_probe=None, isdir=None, repo=None, save=True):
    """«учётки»: пробы всех профилей ПК и всех слотов сервера → текст. Последние ответы — на диск."""
    reg = accounts.load(repo)
    pc_rows = {}
    for n, row in sorted(reg.data["accounts"].items()):
        pc_rows[n] = pc_probe(row["profile"], runner=runner, isdir=isdir)
    if reg.state != accounts.ST_OK:
        pc_rows["main"] = pc_probe(accounts.WORD_MAIN, runner=runner, isdir=isdir)
    try:
        srv = (srv_probe or vti.probe_all_slots)(work=WORK)
    except Exception as e:                            # noqa: BLE001 — сервер не смеет уронить отчёт ПК
        srv = {"ok": False, "words": u"проба сервера упала (%s)" % type(e).__name__, "rows": []}
    choice = accounts.builders_choice(reg, isdir)
    text = report_text(reg, pc_rows, srv, choice, rc_choice_words(repo, isdir))
    if save:
        _remember(repo, pc_rows, srv)
    return text


def _remember(repo, pc_rows, srv):
    last = accounts.load_last(repo)
    for n, row in pc_rows.items():
        last["pc"][u"%s" % n] = row
    by_letter, active_letters, _ar = srv_map(srv.get("rows"))
    for letter, r in by_letter.items():
        last["srv"][letter] = {"code": r.get("code"), "word": r.get("word"), "reset": r.get("reset"),
                               "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    if srv.get("ok"):
        last["srv_active"] = u",".join(active_letters)
    try:
        accounts.save_last(last, repo)
    except Exception:                                 # noqa: BLE001 — память не смеет уронить ответ
        pass


def switch(n, runner=None, use=None, isdir=None, repo=None, save=True):
    """«учётка N»: обе полосы на учётку N. → (код, текст). Код 0 — обе стороны на N, 1 — часть,
    2 — отказ до всякого действия (реестра нет, номера нет)."""
    reg = accounts.load(repo)
    num = accounts.parse_number(n)
    if reg.state != accounts.ST_OK:
        return 2, (u"⛔ Учётку не переключаю: %s. Строители на %s (WARNING). Как завести реестр — "
                   u"«accounts_run.py --screen» на ПК. Ничего не изменено." % (reg.reason, accounts.WORD_MAIN))
    have = u", ".join(u"№%d" % x for x in sorted(reg.data["accounts"])) or u"ни одной"
    if num is None or num not in reg.data["accounts"]:
        return 2, (u"⛔ Учётки «%s» в реестре нет. Есть: %s. Ничего не изменено." % (n, have))
    row = reg.data["accounts"][num]
    before = accounts.builders_choice(reg, isdir)
    prev = (u"№%d" % before.number) if (before.number is not None and not before.warn) else accounts.WORD_MAIN
    out = [u"🔁 Учётка №%d «%s» — итог:" % (num, row["label"])]
    # ── ПК ──
    pc = pc_probe(row["profile"], runner=runner, isdir=isdir)
    pc_ok = pc.get("code") == 200
    if pc_ok:
        try:
            accounts.set_builders(num, repo)
            out.append(u"ПК: строители %s → №%d · проба профиля %s: %s. RC не тронут."
                       % (prev, num, row["profile"], _code_words(pc)))
        except Exception as e:                        # noqa: BLE001
            pc_ok = False
            out.append(u"ПК: проба 200, но реестр НЕ записан (%s) — строители остались %s."
                       % (type(e).__name__, prev))
    else:
        out.append(u"ПК: НЕ переключён — проба профиля %s: %s%s. Строители остались %s."
                   % (row["profile"], _code_words(pc),
                      (u" (%s)" % pc["text"]) if pc.get("text") and pc.get("code") not in (200, 429) else u"",
                      prev))
    # ── сервер ──
    srv_ok, srv_row = False, None
    if not row["slot"]:
        out.append(u"Сервер: у учётки №%d слота нет — демон НЕ переключён и не перезапущен." % num)
    else:
        try:
            res = (use or vti.use_slot)(row["slot"], force=False, work=WORK)
        except Exception as e:                        # noqa: BLE001
            res = {"ok": False, "stage": "error", "lines": [u"ход упал (%s)" % type(e).__name__],
                   "row": None}
        srv_ok, srv_row = bool(res.get("ok")), res.get("row")
        out.append(u"Сервер (слот %s): %s" % (row["slot"], u" ".join(res.get("lines") or [u"ответа нет"])))
    if save:
        last = accounts.load_last(repo)
        last["pc"][u"%d" % num] = pc
        if srv_row is not None and row["slot"]:
            last["srv"][row["slot"]] = {"code": srv_row.get("code"), "word": srv_row.get("word"),
                                        "reset": srv_row.get("reset"),
                                        "at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
            if srv_ok:
                last["srv_active"] = row["slot"]
        try:
            accounts.save_last(last, repo)
        except Exception:                             # noqa: BLE001
            pass
    both = pc_ok and (srv_ok or not row["slot"])
    out.append(u"ИТОГ: %s" % (u"обе полосы на №%d" % num if (pc_ok and srv_ok)
                             else u"ПК на №%d, сервера у учётки нет" % num if both
                             else u"переключено НЕ всё — см. строки выше"))
    return (0 if both else 1), u"\n".join(out)


def show(repo=None, isdir=None):
    """Реестр без проб и без сети — что записано и что действует по записи."""
    reg = accounts.load(repo)
    out = [u"Реестр: %s — %s" % (accounts.registry_path(repo), reg.reason)]
    for err in reg.errors:
        out.append(u"  ⚠️ %s" % err)
    last = accounts.load_last(repo)
    for n, row in sorted(reg.data["accounts"].items()):
        pc = last["pc"].get(u"%d" % n)
        sr = last["srv"].get(row["slot"]) if row["slot"] else None
        out.append(u"  №%d «%s» · ПК %s (последний ответ: %s%s) · сервер %s%s"
                   % (n, row["label"], row["profile"], _code_words(pc),
                      (u", %s" % pc["at"]) if pc else u"",
                      (u"слот %s" % row["slot"]) if row["slot"] else u"слота нет",
                      (u" (последний ответ: %s, %s)" % (_code_words(sr), sr.get("at"))) if sr else u""))
    c = accounts.builders_choice(reg, isdir)
    out.append(u"Строители: %s" % accounts.line(c))
    out.append(u"RC: %s" % rc_choice_words(repo, isdir))
    return u"\n".join(out)


SCREEN = u"""КАК ЗАВЕСТИ УЧЁТКУ N (руки владельца; код не правится)

1. Профиль ПК — войти в новую учётку в СВОЁМ каталоге (PowerShell на ПК):
     $env:CLAUDE_CONFIG_DIR='D:\\claude_profile_N'; claude auth login
   Вход идёт в браузере; токенов никуда не копировать.
2. Слот сервера — выпустить годовой токен той же учётки и положить его в свободную букву:
     claude setup-token                                   (в том же окне, где шаг 1)
     venv\\Scripts\\python.exe vps_token_install.py --slot C
   Ввод скрыт. Проба одного слота: 200/429 — оставлен, 401 — откат из копии. Демон не
   перезапускается. Занятые буквы видно словом «учётки» с телефона.
3. Строка в реестр (файл accounts_registry.json вне git, метка — без почты):
     venv\\Scripts\\python.exe accounts_run.py --set N --profile D:\\claude_profile_N --slot C --label "N-я"
   Основной профиль: --profile ОСНОВНОЙ. Слота нет: --slot -.
   Номер строителей в новом реестре пуст (строители на ОСНОВНОЙ с WARNING) — назначить без
   сервера: accounts_run.py --builders N, либо словом «учётка N» (тогда и сервер).
4. Проверить: «учётки» в теме 205 — у №N должен стоять ответ 200 (или 429 со временем сброса).
5. Перевести обе полосы: «учётка N». Слово = разрешение на ОДИН рестарт демона сервера; если на
   сервере идёт заход — ответ скажет «стоп», повторить позже. RC остаётся на своём выборе
   (файл rc_profile_choice.txt, по умолчанию ОСНОВНОЙ) — телефонный канал не переезжает.

Автопереключения по лимиту НЕТ: полосы переходят на другую учётку только словом владельца."""


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description=u"Реестр учёток: пробы и переключение обеих полос.")
    ap.add_argument("--report", action="store_true", help=u"«учётки»: живые пробы ПК и сервера")
    ap.add_argument("--switch", metavar="N", help=u"«учётка N»: обе полосы на учётку N")
    ap.add_argument("--show", action="store_true", help=u"реестр без проб и без сети")
    ap.add_argument("--screen", action="store_true", help=u"экран владельца: как завести учётку N")
    ap.add_argument("--set", metavar="N", help=u"завести или поправить учётку N")
    ap.add_argument("--profile", help=u"ОСНОВНОЙ либо путь к каталогу профиля ПК")
    ap.add_argument("--slot", default="", help=u"буква слота сервера A–Z, «-» — слота нет")
    ap.add_argument("--label", default="", help=u"метка словами, без почты")
    ap.add_argument("--builders", metavar="N",
                    help=u"только номер строителей ПК (консоль владельца, без проб и без сервера)")
    args = ap.parse_args(argv)
    if args.screen:
        print(SCREEN)
        return 0
    if args.builders:
        try:
            prev = accounts.set_builders(args.builders)
        except ValueError as e:
            print(u"ОТКАЗ: %s. Реестр не тронут." % e)
            return 2
        print(u"Строители ПК: %s → №%s (сервер не тронут; RC не тронут)"
              % ((u"№%s" % prev) if prev else accounts.WORD_MAIN, args.builders))
        return 0
    if args.show:
        print(show())
        return 0
    if args.set:
        if not args.profile:
            print(u"ОТКАЗ: --set требует --profile (ОСНОВНОЙ или путь). Реестр не тронут.")
            return 2
        try:
            clean = accounts.set_entry(args.set, args.profile, args.slot, args.label)
        except ValueError as e:
            print(u"ОТКАЗ: %s. Реестр не тронут." % e)
            return 2
        print(u"Записано №%s: профиль %s · слот %s · «%s» → %s"
              % (args.set, clean["profile"], clean["slot"] or u"нет", clean["label"],
                 accounts.registry_path()))
        return 0
    if args.switch:
        code, text = switch(args.switch)
        print(text)
        return code
    if args.report:
        print(report())
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
