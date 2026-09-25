# -*- coding: utf-8 -*-
"""
accounts_registry.py — РЕЕСТР УЧЁТОК ПОДПИСКИ ОБЕИХ ПОЛОС И ВЫБОР СТРОИТЕЛЕЙ, ВНЕ GIT (25.09.2026).

ЗАЧЕМ. До 25.09 учётку строителей ПК решал файл `claude_profile_choice.txt`, а он лежит В GIT.
Правка отслеживаемого файла делает дерево грязным, а грязное дерево останавливает авто-фетч демона
(`pc_orchestrator._git_autofetch`, ветка «рабочая копия грязная — git pull пропущен»). Значит
переключение учётки словом с телефона либо требовало коммита (заход Штаба), либо тихо замораживало
доставку кода. Серверная полоса при этом переключалась отдельной ручкой, и связи «номер учётки →
профиль ПК → слот сервера» не было нигде, кроме голов людей.

ЧТО ЗДЕСЬ. Один JSON-файл вне git (`accounts_registry.json`, строка в `.gitignore`):

    {"form": 1,
     "builders": 3,                                   # номер учётки строителей ПК
     "accounts": {
        "1": {"profile": "ОСНОВНОЙ",           "slot": "A", "label": "основная"},
        "2": {"profile": "D:\\\\claude_profile_2", "slot": "B", "label": "вторая"},
        "3": {"profile": "D:\\\\claude_profile_3", "slot": "",  "label": "третья"}}}

  • номер → каталог профиля ПК (слово ОСНОВНОЙ = ключ CLAUDE_CONFIG_DIR снят) → слот сервера
    (буква A–Z: переменная `TB_CLAUDE_TOKEN_<буква>` в файле окружения демона; пусто = слота нет)
    → метка человеческими словами, БЕЗ ПОЧТЫ (знак `@` в метке — отказ записи);
  • четвёртая и следующие учётки — новой строкой реестра, без правки кода: `accounts_run.py --set`.

ВЫБОР СТРОИТЕЛЕЙ — ТРИ ИСХОДА, И НИ ОДИН НЕ ПУСТОЙ (наследник `profile_choice.py`, его замер 70u):
    номер найден, профиль ОСНОВНОЙ        → ACT_DROP : ключ снят (основной профиль)
    номер найден, каталог существует      → ACT_SET  : ключ = каталог
    всё остальное                          → ACT_DROP + WARNING: основной профиль и причина словами
«Всё остальное» — это реестр не прочитан · не JSON · номер строителей не назван · номер не из
реестра · каталог не существует. РЕЕСТРА НЕТ ВОВСЕ — отдельная ветка, МОСТ (`builders_effective`):
решает прежний файл дерева `claude_profile_choice.txt` тем же разбором, что до 25.09, плюс WARNING
«заведи реестр»; прежний файл не решает — ОСНОВНОЙ плюс WARNING (слово задания). Мост нужен, чтобы
ДОСТАВКА кода сама не переводила учётку: 25.09 владелец перевёл строителей и RC прежним рычагом.
Направление выбрано сознательно не «наследовать окружение демона», как у
`profile_choice.ACT_KEEP`: наследство сегодня — постоянная переменная пользователя, и 18–22.09 она
держала второй аккаунт (91 старт RC, 0 регистраций). Пустое значение ключа (профиль БЕЗ входа)
не ставится НИ ОДНОЙ веткой — инвариант унаследован от `PROFILE_CHOICE_NEVER_EMPTY`.

RC ЭТОТ МОДУЛЬ НЕ ЧИТАЕТ. У канала телефона свой выбор (`rc_supervisor.RC_CHOICE_REL`; своего
файла нет — прежний файл дерева, как до 25.09), и смена строителей его не трогает: реестр RC не
читает, а прежний файл слово «учётка N» не правит — замок в `test_accounts.py`.

ЧИСТОТА (замок в тесте): `io`, `json`, `os`, `re`, `collections`, `profile_choice` — и всё. Ни
сети, ни подпроцессов, ни секретов: модуль импортирует ДЕМОН (обе ветки спавна), и замыкание
демона не должно расти ни на `subprocess`, ни на ssh. Пробы и переключение живут в
`accounts_run.py`, который демон не импортирует. Модуль НИЧЕГО НЕ УДАЛЯЕТ: запись реестра — это
временный файл рядом и атомарная подмена `os.replace` (первое заведение — `O_EXCL`).
"""

import io
import json
import os
import re
from collections import namedtuple

import profile_choice

KEY = profile_choice.KEY                    # CLAUDE_CONFIG_DIR — одно имя на оба модуля
WORD_MAIN = profile_choice.WORD_MAIN        # ОСНОВНОЙ
ACT_DROP = profile_choice.ACT_DROP
ACT_SET = profile_choice.ACT_SET

REPO = os.path.dirname(os.path.abspath(__file__))

# Место реестра названо ЯВНО и одно: корень рабочего дерева ПК, под `.gitignore`. Не `tmp/`
# (его чистят), не каталог профиля (он у каждой учётки свой) — рядом с прочим состоянием полосы.
REGISTRY_REL = "accounts_registry.json"
# Последние ответы поставщика по учёткам и слотам (пишет `accounts_run.py`, читает «учётки»).
LAST_REL = "accounts_last.json"

FORM = 1
NUM_MAX = 99                    # номер учётки: 1…99 — больше учёток у полосы не бывает
LABEL_MAX = 40                  # метка — два-три слова, не абзац
VALUE_MAX = profile_choice.VALUE_MAX
SLOT_RE = re.compile(r"^[A-Z]$")
SLOT_VAR_PREFIX = "TB_CLAUDE_TOKEN_"     # имя переменной слота на сервере: префикс + буква

Reg = namedtuple("Reg", "state path data errors reason")
Choice = namedtuple("Choice", "action value reason warn number")

ST_OK = "ok"
ST_ABSENT = "absent"
ST_BROKEN = "broken"


# ═══════════════════ чьё слово: владельца или захода строителей ═══════════════════
# Переключение учётки — СЛОВО ВЛАДЕЛЬЦА (задание 25.09: «слово = разрешение на один рестарт демона
# сервера»). Гейт владельца живёт в pc_agent, но сами команды (`accounts_run.py --switch/--init`,
# `vps_token_install.py --use`) лежат в дереве, и заход строителей мог бы позвать их сам — гард
# ПК судит текст команды, а рестарт внутри программы для сервера для него не красный. Поэтому
# команды спрашивают окружение: демон кладёт КАЖДОМУ ребёнку-строителю эти метки
# (`pc_orchestrator._run_task_impl`; имена сверяет тест с исходником демона). Нашли метку —
# это заход строителей, и переключение отказывает словами. Предел назван: процесс того же
# пользователя может снять метки руками — это граница против ошибки, а не против злого умысла.
BUILDER_MARKERS = ("PRETOOL_ASK_MARKER", "PRETOOL_MARKER_TOKEN", "GIT_SERIAL_PC_OWNER")


def builder_child(env=None):
    """Окружение → имя первой найденной метки ребёнка-строителя | "" (не строитель). Чистая."""
    src = os.environ if env is None else env
    for name in BUILDER_MARKERS:
        if src.get(name):
            return name
    return ""


def registry_path(repo=None, path=None):
    """Абсолютный путь реестра. `path` — прямое указание (тесты, разовые замеры)."""
    if path:
        return str(path)
    return os.path.join(str(repo or REPO), REGISTRY_REL)


def last_path(repo=None):
    return os.path.join(str(repo or REPO), LAST_REL)


def slot_var(slot):
    """Буква слота → имя переменной на сервере. Не буква — ValueError (в оболочку не идёт)."""
    s = str(slot or "").strip().upper()
    if not SLOT_RE.match(s):
        raise ValueError("слот %r — не буква A–Z" % (slot,))
    return SLOT_VAR_PREFIX + s


# ═══════════════════ чистая часть: сырой текст → реестр → решение ═══════════════════

def _bad_text(s):
    return any(ord(ch) < 32 for ch in s)


def _norm_profile(raw):
    """Значение профиля → (нормализованное, причина-брак)."""
    if not isinstance(raw, str):
        return "", "профиль не строка"
    v = raw.strip()
    if not v:
        return "", "профиль пуст"
    if len(v) > VALUE_MAX:
        return "", "профиль длиной %d символов (потолок %d)" % (len(v), VALUE_MAX)
    if _bad_text(v):
        return "", "в профиле управляющий символ"
    if v.upper() == WORD_MAIN:
        return WORD_MAIN, ""
    if not ("\\" in v or "/" in v):
        return "", "профиль «%s» — ни слово %s, ни путь к каталогу" % (v, WORD_MAIN)
    return v, ""


def _norm_slot(raw):
    if raw is None:
        return "", ""
    if not isinstance(raw, str):
        return "", "слот не строка"
    v = raw.strip().upper()
    if v in ("", "-", "НЕТ"):
        return "", ""
    if not SLOT_RE.match(v):
        return "", "слот «%s» — не одна буква A–Z" % raw
    return v, ""


def _norm_label(raw, number):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return "учётка %d" % number, ""
    if not isinstance(raw, str):
        return "", "метка не строка"
    v = raw.strip()
    if "@" in v:
        return "", "в метке знак @ — метка без почты"
    if len(v) > LABEL_MAX:
        return "", "метка длиной %d символов (потолок %d)" % (len(v), LABEL_MAX)
    if _bad_text(v):
        return "", "в метке управляющий символ"
    return v, ""


def parse_number(raw):
    """«3», 3, « 3 » → 3; всё прочее → None. Номер учётки — 1…NUM_MAX."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, str) and re.fullmatch(r"[0-9]{1,3}", raw.strip()):
        # ТОЛЬКО ASCII-цифры: `str.isdigit()` пропускает «²»/«①», а `int()` на них падает — и
        # падение уронило бы спавн КАЖДОГО захода демона (находка ревью 25.09).
        n = int(raw.strip())
    else:
        return None
    return n if 1 <= n <= NUM_MAX else None


def validate(data):
    """Сырой объект JSON → (реестр, ошибки). Чистая функция.

    Реестр: {"builders": int|None, "accounts": {номер: {"profile", "slot", "label"}}}.
    Негодная строка учётки ВЫБРАСЫВАЕТСЯ с названной причиной — соседние годные живут. Слот,
    названный у двух учёток сразу, снимается у ОБЕИХ: один вход сервера не может принадлежать
    двум учёткам, и угадывать, чей он, код не вправе."""
    errors = []
    if not isinstance(data, dict):
        return {"builders": None, "accounts": {}}, ["верхний уровень реестра — не объект"]
    raw_acc = data.get("accounts")
    if not isinstance(raw_acc, dict):
        return {"builders": None, "accounts": {}}, ["в реестре нет объекта accounts"]
    accounts = {}
    for key in sorted(raw_acc, key=lambda k: str(k)):
        n = parse_number(key)
        if n is None:
            errors.append("номер «%s» — не число 1…%d" % (key, NUM_MAX))
            continue
        row = raw_acc[key]
        if not isinstance(row, dict):
            errors.append("№%d: запись не объект" % n)
            continue
        profile, bad = _norm_profile(row.get("profile"))
        if bad:
            errors.append("№%d: %s" % (n, bad))
            continue
        slot, bad = _norm_slot(row.get("slot"))
        if bad:
            errors.append("№%d: %s" % (n, bad))
            continue
        label, bad = _norm_label(row.get("label"), n)
        if bad:
            errors.append("№%d: %s" % (n, bad))
            continue
        accounts[n] = {"profile": profile, "slot": slot, "label": label}
    owners = {}
    for n, row in accounts.items():
        if row["slot"]:
            owners.setdefault(row["slot"], []).append(n)
    for slot, nums in sorted(owners.items()):
        if len(nums) > 1:
            errors.append("слот %s назван у №%s — снят у всех, чей он, код не угадывает"
                          % (slot, ", №".join(str(x) for x in sorted(nums))))
            for x in nums:
                accounts[x]["slot"] = ""
                # след снятого слота: «слота нет» и «слот спорный» — разные ответы владельцу
                accounts[x]["slot_error"] = "слот %s назван и у другой учётки — спорный" % slot
    builders = None
    if data.get("builders") is not None:              # null — «ещё не назначен», не ошибка
        builders = parse_number(data.get("builders"))
        if builders is None:
            errors.append("номер строителей «%s» — не число 1…%d" % (data.get("builders"), NUM_MAX))
    return {"builders": builders, "accounts": accounts}, errors


def decode(raw, err="", path=""):
    """(сырой текст | None, причина отказа чтения) → Reg. Чистая функция."""
    if err:
        return Reg(ST_BROKEN, path, {"builders": None, "accounts": {}}, [],
                   "реестр учёток %s не прочитан (%s)" % (REGISTRY_REL, err))
    if raw is None:
        return Reg(ST_ABSENT, path, {"builders": None, "accounts": {}}, [],
                   "реестра учёток %s нет" % REGISTRY_REL)
    try:
        data = json.loads(raw)
    except ValueError as e:
        return Reg(ST_BROKEN, path, {"builders": None, "accounts": {}}, [],
                   "реестр учёток %s — не JSON (%s)" % (REGISTRY_REL, str(e)[:80]))
    reg, errors = validate(data)
    return Reg(ST_OK, path, reg, errors, "реестр прочитан: учёток %d" % len(reg["accounts"]))


def read_raw(repo=None, path=None):
    """→ (текст | None, причина отказа). Файла нет → (None, "") — штатный исход «реестра нет»."""
    p = registry_path(repo, path)
    try:
        if not os.path.isfile(p):
            return None, ""
        with io.open(p, encoding="utf-8-sig") as f:        # BOM PowerShell 5.1 — не порча
            return f.read(), ""
    except Exception as e:                                 # noqa: BLE001 — отказ чтения = не знаем
        return None, "%s: %s" % (type(e).__name__, str(e)[:120])


def load(repo=None, path=None, reader=None):
    p = registry_path(repo, path)
    raw, err = (reader or read_raw)(repo, path)
    return decode(raw, err, p)


def builders_choice(reg, isdir=None):
    """Реестр → Choice для окружения ребёнка строителей. Чистая функция (isdir — инъекция).

    Пустого значения не возвращает НИ ОДНА ветка: либо ACT_DROP, либо ACT_SET с каталогом,
    который существует на момент решения."""
    isdir = isdir or os.path.isdir
    warn_tail = " — строители на %s (WARNING)" % WORD_MAIN
    if reg.state != ST_OK:
        return Choice(ACT_DROP, "", reg.reason + warn_tail, True, None)
    n = reg.data.get("builders")
    if n is None:
        return Choice(ACT_DROP, "", "в реестре не назван номер строителей" + warn_tail, True, None)
    row = reg.data["accounts"].get(n)
    if row is None:
        return Choice(ACT_DROP, "", "номера строителей №%d в реестре нет" % n + warn_tail, True, n)
    if row["profile"] == WORD_MAIN:
        return Choice(ACT_DROP, "", "учётка №%d «%s» — профиль %s, ключ %s снят"
                      % (n, row["label"], WORD_MAIN, KEY), False, n)
    try:
        exists = bool(isdir(row["profile"]))
    except Exception as e:                                 # noqa: BLE001 — «не проверить» ≠ «есть»
        return Choice(ACT_DROP, "", "каталог учётки №%d «%s» не проверить (%s)"
                      % (n, row["profile"], type(e).__name__) + warn_tail, True, n)
    if not exists:
        return Choice(ACT_DROP, "", "каталога учётки №%d «%s» нет" % (n, row["profile"]) + warn_tail,
                      True, n)
    return Choice(ACT_SET, row["profile"], "учётка №%d «%s» — ключ %s = «%s»"
                  % (n, row["label"], KEY, row["profile"]), False, n)


# ═══════════════════ мост: реестра ещё нет — решает прежний файл дерева ═══════════════════
# Доставка кода не смеет сама переводить учётку. 25.09 в 15:16 владелец перевёл строителей и RC на
# профиль 3 ПРЕЖНИМ рычагом — строкой `claude_profile_choice.txt` (коммит 0d34f16 на ПК), а код без
# моста при отсутствии реестра увёл бы строителей на ОСНОВНОЙ молча, на первом же заходе после
# доставки. Поэтому: реестр ЕСТЬ (даже битый) — решает он; реестра НЕТ — решает прежний файл, ровно
# тем разбором, что до 25.09 (`profile_choice.decide`), с WARNING «заведи реестр». Прежний файл не
# решает (нет его, испорчен, каталога нет) — ОСНОВНОЙ плюс WARNING, как требует задание: наследовать
# окружение демона нельзя (класс 70w, профиль 2 из переменной пользователя). Реестр мост НЕ пишет и
# прежний файл НЕ правит: читать — можно, пачкать дерево — нет.
LEGACY_REL = profile_choice.CHOICE_REL


def builders_effective(repo=None, path=None, isdir=None, reader=None, legacy_reader=None):
    """Чем строители пойдут НА САМОМ ДЕЛЕ → Choice (реестр; реестра нет — мост на прежний файл).
    Та же функция, что применяет демон, отвечает и слову «учётки» — двух правд нет."""
    reg = load(repo, path, reader)
    if reg.state != ST_ABSENT:
        return builders_choice(reg, isdir)
    raw, err = (legacy_reader or profile_choice.read_raw)(repo or REPO, None)   # не cwd: консоль зовут откуда угодно
    d = profile_choice.decide(raw, err, isdir)
    if d.action in (ACT_SET, ACT_DROP) and (d.action == ACT_DROP or d.value):
        return Choice(d.action, d.value if d.action == ACT_SET else "",
                      "реестра нет — решает прежний файл %s (%s); заведи реестр словом «учётки заведи»"
                      % (LEGACY_REL, d.reason), True, None)
    return Choice(ACT_DROP, "", "%s, и прежний файл не решает (%s) — строители на %s (WARNING)"
                  % (reg.reason, d.reason, WORD_MAIN), True, None)


def apply_builders(env, repo=None, path=None, isdir=None, reader=None, legacy_reader=None):
    """ПРИМЕНИТЬ выбор строителей к УЖЕ СОБРАННОМУ окружению ребёнка (правка на месте) → Choice.
    Зовут ОБЕ ветки спавна демона (исполнитель и думатель) — замок в тесте.

    НЕ БРОСАЕТ НИКОГДА: вызов стоит без `try` в обеих ветках спавна, и исключение здесь уронило бы
    каждый заход полосы. Любой сбой разбора → ОСНОВНОЙ плюс WARNING с названной причиной."""
    try:
        c = builders_effective(repo, path, isdir, reader, legacy_reader)
    except Exception as e:                             # noqa: BLE001
        c = Choice(ACT_DROP, "", "выбор учётки сорвался (%s: %s) — строители на %s (WARNING)"
                   % (type(e).__name__, str(e)[:80], WORD_MAIN), True, None)
    if c.action == ACT_SET and c.value:
        env[KEY] = c.value
    else:
        env.pop(KEY, None)
    return c


def line(c, tid=None):
    """Строка для журнала захода. WARNING звучит словом, а не только уровнем лога."""
    who = ("id=%s " % tid) if tid is not None else ""
    head = "WARNING: " if c.warn else ""
    return "%s%sучётка строителей: %s" % (who, head, c.reason)


# ═══════════════════ запись реестра (владелец и «учётка N») ═══════════════════

def _read_json_obj(p):
    """Текущий реестр как ОБЪЕКТ для правки: чужие ключи владельца (комментарии) сохраняются."""
    if not os.path.isfile(p):
        return {"form": FORM, "builders": None, "accounts": {}}
    with io.open(p, encoding="utf-8-sig") as f:
        data = json.loads(f.read())
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        raise ValueError("реестр %s не объект с accounts — править вслепую не стану" % p)
    return data


def write_json_atomic(p, data):
    """Временный файл рядом + `os.replace`: оборванная запись не оставит полуфайла."""
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
    os.replace(tmp, p)


def set_builders(n, repo=None, path=None):
    """Номер строителей → реестр. → (прежний номер | None). Номер обязан быть в реестре."""
    p = registry_path(repo, path)
    data = _read_json_obj(p)
    reg, _errors = validate(data)
    n = parse_number(n)
    if n is None or n not in reg["accounts"]:
        raise ValueError("учётки №%s в реестре нет" % n)
    prev = parse_number(data.get("builders"))
    data["builders"] = n
    data.setdefault("form", FORM)
    write_json_atomic(p, data)
    return prev


def create(rows, builders, repo=None, path=None):
    """Завести НОВЫЙ реестр целиком (слово «учётки заведи»). → разобранные данные (как `validate`).

    Файл уже есть — любой, даже битый, — FileExistsError: заведение НЕ перезаписывает руку
    владельца. Негодная строка — ValueError, и ничего не пишется (проверка та же, что при чтении)."""
    p = registry_path(repo, path)
    if os.path.exists(p):
        raise FileExistsError("реестр %s уже есть — заведение не перезаписывает" % p)
    data = {"form": FORM, "builders": builders,
            "accounts": dict((u"%d" % n, dict(row)) for n, row in sorted(rows.items()))}
    reg, errors = validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    # O_EXCL, а не `os.replace`: файл, появившийся между проверкой выше и записью (рука владельца с
    # консоли, `--set`), НЕ затирается — создание падает FileExistsError (находка ревью 25.09, гонка
    # проверки и записи). Блок меньше килобайта пишется одним вызовом; оборванную запись читатель
    # увидит «не JSON» → ОСНОВНОЙ плюс WARNING, а не чужой выбор.
    blob = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n").encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
    try:
        done = 0
        while done < len(blob):
            done += os.write(fd, blob[done:])
    finally:
        os.close(fd)
    return reg


def set_entry(n, profile, slot, label, repo=None, path=None):
    """Завести или поправить учётку №n. Та же проверка, что при чтении: негодное не пишется."""
    p = registry_path(repo, path)
    data = _read_json_obj(p)
    num = parse_number(n)
    if num is None:
        raise ValueError("номер «%s» — не число 1…%d" % (n, NUM_MAX))
    row = {"profile": profile, "slot": slot or "", "label": label or ""}
    probe_data = {"accounts": {str(num): row}}
    reg, errors = validate(probe_data)
    if errors or num not in reg["accounts"]:
        raise ValueError("; ".join(errors) or "запись не прошла проверку")
    clean = reg["accounts"][num]
    for other_key, other in data["accounts"].items():
        if (parse_number(other_key) != num and isinstance(other, dict) and clean["slot"]
                and str(other.get("slot") or "").strip().upper() == clean["slot"]):
            raise ValueError("слот %s уже назван у №%s — один вход сервера не делится"
                             % (clean["slot"], other_key))
    data["accounts"][str(num)] = clean
    data.setdefault("form", FORM)
    data.setdefault("builders", None)
    write_json_atomic(p, data)
    return clean


# ═══════════════════ последние ответы поставщика ═══════════════════

def load_last(repo=None):
    """→ {"pc": {"№": {...}}, "srv": {"буква": {...}}, "srv_active": буква|""}. Нет/битый → пусто."""
    p = last_path(repo)
    try:
        with io.open(p, encoding="utf-8") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            # кривые ветви (null, список) заменяются пустыми: иначе запись итога падала бы УЖЕ ПОСЛЕ
            # переключения сервера, и владелец получил бы трассу вместо итога (находка ревью 25.09)
            for key in ("pc", "srv"):
                if not isinstance(data.get(key), dict):
                    data[key] = {}
            return data
    except Exception:                                      # noqa: BLE001 — нет истории ≠ ошибка
        pass
    return {"pc": {}, "srv": {}}


def save_last(data, repo=None):
    write_json_atomic(last_path(repo), data)
