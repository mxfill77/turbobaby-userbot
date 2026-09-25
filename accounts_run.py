# -*- coding: utf-8 -*-
"""
accounts_run.py — РУКИ РЕЕСТРА УЧЁТОК: «учётки» и «учётка N» с телефона, заведение учётки с ПК
(25.09.2026, задание Штаба «учётки одним словом»).

Решение (что такое реестр, какой профиль у строителей) живёт в `accounts_registry.py` — его импортирует
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
АВТОПЕРЕКЛЮЧЕНИЯ УЧЁТКИ ПО ЛИМИТУ НЕТ: этот модуль зовут только слово владельца и его консоль.
Старый серверный повтор ОДНОЙ задачи под вторым слотом при 429 (`limit_slot`, 20.09) остаётся
как был — действующее он не меняет; «учётки» называет, под чьим слотом он пойдёт.

СЕКРЕТЫ. Токенов и `.credentials.json` модуль не читает вовсе: проба профиля — это запуск CLI с
`CLAUDE_CONFIG_DIR`, а наружу идут только слово исхода, статус, время сброса и текст поставщика
(конверт CLI значений входа не содержит, замер 71r), пропущенный через `exit_evidence.scrub`.

ЗАПУСК:
    venv\\Scripts\\python.exe accounts_run.py --report          # «учётки»
    venv\\Scripts\\python.exe accounts_run.py --init            # «учётки заведи» (первый реестр)
    venv\\Scripts\\python.exe accounts_run.py --switch 3        # «учётка 3»
    venv\\Scripts\\python.exe accounts_run.py --show            # реестр без проб, без сети
    venv\\Scripts\\python.exe accounts_run.py --set 4 --profile D:\\claude_profile_4 --slot C --label четвёртая
    venv\\Scripts\\python.exe accounts_run.py --builders 3      # только номер строителей ПК, без сервера
    venv\\Scripts\\python.exe accounts_run.py --screen          # экран владельца: как завести учётку N
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import tempfile
import time

import accounts_registry as accounts
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

def _ver_key(name):
    """'2.1.217' → (2, 1, 217); нечисловое → (0,). Зеркало `rc_supervisor._ver_key`."""
    nums = re.findall(r"\d+", name or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def claude_base_dirs():
    """Базы версий claude-code, включая РЕАЛЬНУЮ MSIX-базу. Зеркало `rc_supervisor.claude_base_dirs`:
    pc_agent поднимает Планировщик, и Roaming-редирект MSIX ему не виден."""
    home = os.path.expanduser("~")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    local = os.getenv("LOCALAPPDATA") or os.path.join(os.getenv("USERPROFILE") or home,
                                                      "AppData", "Local")
    out = [os.path.join(appdata, "Claude", "claude-code")]
    try:
        for cc in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache",
                                         "Roaming", "Claude", "claude-code")):
            if cc not in out:
                out.append(cc)
    except Exception:                                  # noqa: BLE001
        pass
    return out


def newest_versioned_claude(bases=None, globber=None, isfile=None):
    """Новейшая версионная установка по ЧИСЛОВОМУ ключу. Зеркало `rc_supervisor.newest_versioned_claude`."""
    bases = bases if bases is not None else claude_base_dirs()
    _glob = globber or glob.glob
    _isf = isfile or os.path.isfile
    cands = []
    try:
        for base in bases:
            for d in _glob(os.path.join(base, "*")):
                exe = os.path.join(d, "claude.exe")
                if _isf(exe):
                    cands.append((_ver_key(os.path.basename(d)), exe))
    except Exception:                                  # noqa: BLE001
        return None
    if not cands:
        return None
    cands.sort()
    return cands[-1][1]


def find_claude(which=None, isfile=None, newest=None):
    """CLI claude ТЕМ ЖЕ порядком, что у RC (`rc_supervisor.resolve_claude`; равенство держит тест):
    PATH-шим → вечный нативный шим ~/.local/bin → новейшая версионная установка (Desktop/MSIX) →
    прочие схемы. Импортировать rc_supervisor отсюда нельзя: он вешает лог-обработчик на старте.
    Нет нигде → None (проба «неизвестно», а не «жива»)."""
    _which = which or shutil.which
    _isf = isfile or os.path.isfile
    shim = _which("claude")
    if shim:
        return shim
    home = os.path.expanduser("~")
    for c in (os.path.join(home, ".local", "bin", "claude.exe"),
              os.path.join(home, ".local", "bin", "claude.cmd")):
        if _isf(c):
            return c
    exe = (newest or newest_versioned_claude)()
    if exe:
        return exe
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    for c in (os.path.join(local, "Programs", "claude", "claude.exe"),
              os.path.join(local, "Programs", "claude-code", "claude.exe"),
              os.path.join(appdata, "npm", "claude.cmd")):
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
    """Что действует у RC — тем же порядком, что `rc_supervisor.child_env`: свой файл; его нет — МОСТ
    на прежний файл дерева (как до 25.09); не решает ни один → ОСНОВНОЙ (RC-страховка)."""
    raw, err = profile_choice.read_raw(path=os.path.join(repo or REPO, RC_CHOICE_REL))
    src = u"свой файл %s" % RC_CHOICE_REL
    if raw is None and not err:
        raw, err = profile_choice.read_raw(repo=repo or REPO)
        src = u"своего файла нет — прежний файл %s" % profile_choice.CHOICE_REL
        if raw is None and not err:
            return u"%s (ни своего файла, ни прежнего — RC-страховка)" % accounts.WORD_MAIN
    d = profile_choice.decide(raw, err, isdir)
    if d.action == profile_choice.ACT_DROP:
        return u"%s (%s)" % (accounts.WORD_MAIN, src)
    if d.action == profile_choice.ACT_SET:
        return u"%s (%s)" % (d.value, src)
    return u"%s (%s не разобран: %s — RC-страховка)" % (accounts.WORD_MAIN, src, d.reason)


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


def retry_words(srv, owner_of):
    u"""Честная строка про СЕРВЕРНЫЙ повтор задачи при лимите (`limit_slot`, 20.09). Чистая функция.

    Слово «автопереключения нет» верно для УЧЁТКИ: действующий вход сервера меняет только слово
    владельца. Но демон, упёршись в 429 на слоте A или B, повторяет ОДНУ задачу под вторым из них —
    действующее при этом не меняется. Владелец обязан видеть, под чьей учёткой пойдёт такой повтор."""
    if not srv.get("ok"):
        return []
    by_letter, active_letters, _ar = srv_map(srv.get("rows"))
    if len(active_letters) != 1 or active_letters[0] not in (u"A", u"B"):
        return [u"повтор задачи при 429 на сервере: нет (действующее не совпало ровно с A или B)"]
    act = active_letters[0]
    other = u"B" if act == u"A" else u"A"
    row = by_letter.get(other)
    if not row or not row.get("called"):
        return [u"повтор задачи при 429 на сервере: нет — слот %s пуст или его нет" % other]
    who = (u" (№%d)" % owner_of[other]) if other in owner_of else u""
    return [u"при 429 сервер повторит ЗАДАЧУ один раз под слотом %s%s — старый повтор limit_slot; "
            u"учётку он НЕ переключает" % (other, who)]


def builders_words(choice):
    """Choice строителей → слова: «№N», путь (мост на прежний файл) или ОСНОВНОЙ, плюс WARNING."""
    if choice.number is not None and not choice.warn:
        b = u"№%d" % choice.number
    elif choice.action == accounts.ACT_SET and choice.value:
        b = choice.value
    else:
        b = accounts.WORD_MAIN
    if choice.warn:
        why = choice.reason
        tail = u" — строители на %s (WARNING)" % accounts.WORD_MAIN
        if why.endswith(tail):                     # то же слово дважды в одной строке не повторяем
            why = why[:-len(tail)]
        b += u" (WARNING: %s)" % why
    return b


def registry_fix_words(reg):
    """Что делать, если реестр не прочитан: нет — завести словом; битый — «заведи» откажет, правка на ПК."""
    if reg.state == accounts.ST_ABSENT:
        return u"завести реестр одним словом — «учётки заведи»"
    return u"реестр есть, но не разобран — «учётки заведи» его не перезапишет; поправить на ПК: " \
           u"«accounts_run.py --show», затем «--set»"


def report_text(reg, pc_rows, srv, choice, rc_words, now=None):
    """Реестр + пробы → ответ «учётки». Чистая функция (всё, что нужно, приходит параметрами)."""
    now = now or time.strftime("%H:%M UTC", time.gmtime())
    out = [u"👥 Учётки — живые пробы, %s" % now]
    b = builders_words(choice)
    if reg.state != accounts.ST_OK:
        out.append(u"⚠️ %s — %s." % (reg.reason, registry_fix_words(reg)))
    for err in reg.errors:
        out.append(u"⚠️ реестр: %s" % err)
    by_letter, active_letters, active_row = srv_map(srv.get("rows"))
    owner_of = {}
    for n, row in sorted(reg.data["accounts"].items()):
        pc = pc_rows.get(n)
        if row.get("slot_error"):
            s_words = u"слот спорный — %s" % row["slot_error"]
        elif row["slot"]:
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
    if "main" in pc_rows:
        out.append(u"%s · ПК %s: %s" % (u"без реестра — строители идут так" if reg.state != accounts.ST_OK
                                          else u"откат строителей (строки в реестре нет)",
                                          pc_rows["main"].get("profile") or accounts.WORD_MAIN,
                                          _code_words(pc_rows["main"])))
    stray = sorted(set(by_letter) - set(owner_of))
    for letter in stray:
        out.append(u"слот %s вне реестра: %s%s" % (letter, _code_words(by_letter[letter]),
                                                   u" ✓действует" if letter in active_letters else u""))
    if not srv.get("ok"):
        s_act = u"не проверено — %s" % (srv.get("words") or u"сервер не ответил")
    elif active_letters:
        s_act = u", ".join(u"слот %s%s" % (x, (u" (№%d)" % owner_of[x]) if x in owner_of else u"")
                           for x in active_letters)
        s_act += u", проба действующего: %s" % _code_words(active_row)
    else:
        s_act = u"значение действующего не совпало ни с одним слотом, проба: %s" % _code_words(active_row)
    out.append(u"ДЕЙСТВУЕТ: строители ПК — %s · RC — %s · сервер — %s" % (b, rc_words, s_act))
    out.extend(retry_words(srv, owner_of))
    out.append(u"модели проб: ПК %s · сервер %s" % (PROBE_MODEL, vti.PROBE_MODEL))
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
    choice0 = accounts.builders_effective(repo, isdir=isdir)
    eff = choice0.value if (choice0.action == accounts.ACT_SET and choice0.value) else accounts.WORD_MAIN
    known = set(os.path.normcase(row["profile"]) for row in reg.data["accounts"].values())
    if (choice0.number is None or choice0.warn) and os.path.normcase(eff) not in known:
        # строители идут профилем, которого нет среди строк реестра (реестра нет — мост на прежний
        # файл; откат на ОСНОВНОЙ без его строки) — меряем ТО, чем они идут на самом деле
        pc_rows["main"] = dict(pc_probe(eff, runner=runner, isdir=isdir), profile=eff)
    try:
        srv = (srv_probe or vti.probe_all_slots)(work=WORK)
    except Exception as e:                            # noqa: BLE001 — сервер не смеет уронить отчёт ПК
        srv = {"ok": False, "words": u"проба сервера упала (%s)" % type(e).__name__, "rows": []}
    text = report_text(reg, pc_rows, srv, choice0, rc_choice_words(repo, isdir))
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
        return 2, (u"⛔ Учётку не переключаю: %s. Строители сейчас: %s. Дальше: %s. Ничего не изменено."
                   % (reg.reason, builders_words(accounts.builders_effective(repo, isdir=isdir)),
                      registry_fix_words(reg)))
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
    if row.get("slot_error"):
        out.append(u"Сервер: НЕ переключён — %s (реестр: %s). Демон не тронут." % (
            row["slot_error"], u"; ".join(reg.errors)))
    elif not row["slot"]:
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
    both = pc_ok and (srv_ok or (not row["slot"] and not row.get("slot_error")))
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
                      (u"слот %s" % row["slot"]) if row["slot"] else
                      (u"слот спорный — %s" % row["slot_error"]) if row.get("slot_error") else u"слота нет",
                      (u" (последний ответ: %s, %s)" % (_code_words(sr), sr.get("at"))) if sr else u""))
    c = accounts.builders_effective(repo, isdir=isdir)
    out.append(u"Строители: %s" % accounts.line(c))
    out.append(u"RC: %s" % rc_choice_words(repo, isdir))
    return u"\n".join(out)


# ═══════════════════ «учётки заведи»: первый реестр одним словом ═══════════════════
# Без реестра слово «учётка N» отказывает, а завести реестр можно было только с консоли ПК —
# то есть «одним словом с телефона» не работало ни разу. Заведение кладёт ровно п.1 задания
# 25.09: №1 — основной каталог, №2 — `D:\claude_profile_2` и слот B, №3 — `D:\claude_profile_3` и
# тот слот, где по пробе третья учётка; слот основной — по факту пробы. И заодно п.0: проба
# профиля №3, 200 → строители на №3, иначе на №1. Сервер заведение НЕ трогает (ни записи, ни
# рестарта) — его переключает только «учётка N».
INIT_PROFILE_2 = u"D:\\claude_profile_2"
INIT_PROFILE_3 = u"D:\\claude_profile_3"
# Правило П2 (слово Штаба 25.09): слот A = 200 → в A третья учётка; A = 429 → в A ещё основная.
# Слово Штаба держится на ПОСЫЛКЕ «основная в лимите, третья жива», и заведение её МЕРЯЕТ, а не берёт
# на веру: пробуются основная и №3 на ПК (учётка одна — лимит у неё один на обеих машинах). Слот
# назначается ТОЛЬКО когда три пробы не противоречат друг другу (находка ревью 25.09: по одной пробе
# A при третьей тоже в лимите A = 429 записал бы №1 ← A навсегда). Окно правила — до ИЗМЕРЕННОГО
# сброса недели основной, 28.09 21:00 UTC (артефакт 2209, «Sep 29, 4am (Asia/Bangkok)»): «до 29.09»
# Штаба — местная дата. После — A = 200 учёток не различает, и правило гаснет само.
P2_RULE_UNTIL = (2026, 9, 28, 21, 0)                # UTC (год, месяц, день, час, минута)
CONCLUSIVE = (200, 401, 429)                       # ответы, по которым можно судить; прочее — «не знаю»


def _code(row):
    return (row or {}).get("code")


def init_plan(slot_a, pc_main, pc3, now):
    """ЧИСТОЕ решение заведения → (строки, №строителей, слова, повторить).

    `повторить` = True — судить не по чему (проба дала 529, таймаут, сбой канала): реестр НЕ пишется,
    владелец повторяет слово позже. Иначе реестр пишется, и слот №1/№3 назначен ТОЛЬКО по правилу П2 с
    подтверждённой посылкой; противоречие проб — слоты пусты, и это сказано словами."""
    words = []
    slot1 = slot3 = u""
    a, m, t = _code(slot_a), _code(pc_main), _code(pc3)
    live = tuple(now) < P2_RULE_UNTIL
    if live and slot_a and any(c not in CONCLUSIVE for c in (a, m, t)):
        words.append(u"судить не по чему: слот A — %s · основная на ПК — %s · №3 на ПК — %s"
                     % (_code_words(slot_a), _code_words(pc_main), _code_words(pc3)))
        return None, None, words, True
    probes = u"слот A = %s · основная на ПК = %s · №3 на ПК = %s" % (a, m, t)
    if not live:
        words.append(u"правило П2 истекло (28.09 21:00 UTC — сброс недели основной; A = 200 учёток больше "
                     u"не различает) — слоты №1 и №3 НЕ назначены, назначь «accounts_run.py --set» на ПК")
    elif not slot_a:
        words.append(u"слота A на сервере нет — слоты №1 и №3 НЕ назначены")
    elif a == 200 and m == 429 and t != 429:
        slot3 = u"A"
        words.append(u"%s → основная в лимите, 200 ей не принадлежит: №3 ← A, у №1 слота нет" % probes)
    elif a == 429 and m == 429 and t == 200:
        slot1 = u"A"
        words.append(u"%s → третья жива, 429 ей не принадлежит: №1 ← A (основная), у №3 слота нет" % probes)
    else:
        words.append(u"%s — пробы не сходятся с правилом П2, чей вход в A, не доказано: слоты №1 и №3 "
                     u"НЕ назначены, назначь «accounts_run.py --set» на ПК" % probes)
    rows = {1: {"profile": accounts.WORD_MAIN, "slot": slot1, "label": u"основная"},
            2: {"profile": INIT_PROFILE_2, "slot": u"B", "label": u"вторая"},
            3: {"profile": INIT_PROFILE_3, "slot": slot3, "label": u"третья"}}
    if t == 200:
        builders = 3
        words.append(u"п.0: проба профиля №3 — 200 (is_error=false) → строители на №3")
    else:
        builders = 1
        words.append(u"п.0: проба профиля №3 — %s → строители на №1 (ОСНОВНОЙ)"
                     % (_code_words(pc3) + ((u", %s" % pc3["text"]) if (pc3 or {}).get("text") else u"")))
    return rows, builders, words, False


def init_registry(runner=None, srv_probe=None, isdir=None, repo=None, today=None, save=True):
    """«учётки заведи» → (код, текст). 0 — заведён; 2 — отказ без единой записи."""
    reg = accounts.load(repo)
    if reg.state != accounts.ST_ABSENT:
        return 2, (u"⛔ Реестр уже есть (%s) — «учётки заведи» не перезаписывает. Смотреть: «учётки»; "
                   u"править: accounts_run.py --set на ПК. Ничего не изменено." % reg.reason)
    try:
        srv = (srv_probe or vti.probe_all_slots)(work=WORK)
    except Exception as e:                            # noqa: BLE001
        srv = {"ok": False, "words": u"проба сервера упала (%s)" % type(e).__name__, "rows": []}
    if not srv.get("ok"):
        return 2, (u"⛔ Реестр НЕ заведён: сервер не ответил (%s), а слот третьей учётки решает его проба. "
                   u"Повтори «учётки заведи» позже. Ничего не изменено." % (srv.get("words") or u"причина не названа"))
    by_letter, active_letters, active_row = srv_map(srv.get("rows"))
    pc_main = pc_probe(accounts.WORD_MAIN, runner=runner, isdir=isdir)
    pc3 = pc_probe(INIT_PROFILE_3, runner=runner, isdir=isdir)
    rows, builders, words, retry = init_plan(by_letter.get("A"), pc_main, pc3,
                                             today or tuple(time.gmtime()[:5]))
    if retry:
        return 2, (u"⛔ Реестр НЕ заведён: %s. Слот, записанный по такой пробе, остался бы чужим навсегда — "
                   u"повтори «учётки заведи» позже. Ничего не изменено." % u"; ".join(words))
    try:
        accounts.create(rows, builders, repo)
    except (FileExistsError, ValueError) as e:
        return 2, u"⛔ Реестр НЕ заведён: %s. Ничего не изменено." % e
    if save:
        _remember(repo, {1: pc_main, 3: pc3}, srv)
    out = [u"🆕 Реестр учёток заведён (%s, вне git):" % accounts.REGISTRY_REL]
    for n, row in sorted(rows.items()):
        out.append(u"№%d «%s» · ПК %s · сервер %s" % (n, row["label"], row["profile"],
                                                      (u"слот %s" % row["slot"]) if row["slot"] else u"слота нет"))
    out.extend(words)
    act = u", ".join(active_letters) or u"значение действующего не совпало ни с одним слотом"
    out.append(u"ДЕЙСТВУЕТ: строители ПК — №%d · RC — %s · сервер — %s (заведение сервер НЕ трогает; "
               u"перевести его — «учётка N»)" % (builders, rc_choice_words(repo, isdir), act))
    return 0, u"\n".join(out)


SCREEN = u"""КАК ЗАВЕСТИ УЧЁТКУ N (руки владельца; код не правится)

0. Первый раз и только для трёх известных учёток — одним словом с телефона: «учётки заведи».
   Реестр ляжет по п.1 задания 25.09, слоты №1/№3 — по пробам слота A, основной и №3 (правило
   П2 до 28.09 21:00 UTC; пробы не сходятся — слоты пусты, назначить --set), строители — по №3.
   Проба дала 529/таймаут — реестр не пишется, слово повторить позже.
   Дальше — шаги ниже для четвёртой и следующих.

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
   Номер строителей в новом реестре пуст (строители на ОСНОВНОЙ с WARNING; пока реестра нет
   вовсе — по прежнему claude_profile_choice.txt) — назначить без
   сервера: accounts_run.py --builders N, либо словом «учётка N» (тогда и сервер).
4. Проверить: «учётки» в теме 205 — у №N должен стоять ответ 200 (или 429 со временем сброса).
5. Перевести обе полосы: «учётка N». Слово = разрешение на ОДИН рестарт демона сервера; если на
   сервере идёт заход — ответ скажет «стоп», повторить позже. RC остаётся на своём выборе
   (файл rc_profile_choice.txt; нет его — прежний claude_profile_choice.txt) — телефонный канал
   не переезжает.

Автопереключения учётки по лимиту НЕТ: полосы переходят на другую учётку только словом владельца.
Сервер при 429 на слоте A/B повторяет ОДНУ задачу под вторым слотом (старый повтор 20.09);
действующий вход он не меняет — «учётки» называет, под чьим слотом пойдёт такой повтор."""


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description=u"Реестр учёток: пробы и переключение обеих полос.")
    ap.add_argument("--report", action="store_true", help=u"«учётки»: живые пробы ПК и сервера")
    ap.add_argument("--init", action="store_true",
                    help=u"«учётки заведи»: первый реестр по п.1 задания 25.09 (если его ещё нет)")
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
    if args.show:
        print(show())
        return 0
    marker = accounts.builder_child()
    if marker:
        print(u"⛔ Это слово владельца: пробы и переключение учёток из захода строителей не "
              u"запускаются (в окружении метка %s). Скажи владельцу: «учётки» / «учётка N» в теме 205. "
              u"Ничего не изменено, проб не было." % marker)
        return 3
    if args.init:
        code, text = init_registry()
        print(text)
        return code
    if args.builders:
        try:
            prev = accounts.set_builders(args.builders)
        except ValueError as e:
            print(u"ОТКАЗ: %s. Реестр не тронут." % e)
            return 2
        print(u"Строители ПК: %s → №%s (сервер не тронут; RC не тронут)"
              % ((u"№%s" % prev) if prev else accounts.WORD_MAIN, args.builders))
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
