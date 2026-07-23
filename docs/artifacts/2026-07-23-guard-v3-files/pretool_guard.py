#!/usr/bin/env python3
"""PreToolUse hook Claude Code — закрывает СЛЕПОЕ ПЯТНО записи в Лист1/CRM/деньги.

Контекст: `venv/bin/python3 *` в allow → ЛЮБОЙ мой python-скрипт (вкл. ПИШУЩИЙ в рабочие таблицы через Bridge)
исполнялся БЫ без сигнала. Этот hook перехватывает python-команды и, если скрипт содержит WRITE-признак
(запись в Лист1/CRM, деньги, удаление), форсит permissionDecision="ask" + пушит человеческую КРАСНУЮ КАРТОЧКУ
в Telegram владельцу. Читающий python (recon/gate/tests/reports через write_doc в журналы) — не трогает (defer).

ПОЗИЦИЯ БЕЗОПАСНОСТИ: hook ТОЛЬКО ДОБАВЛЯЕТ подтверждения (никогда не выдаёт новых разрешений).
- red python → "ask" (+карточка); жёсткие классы (процессы/секреты) → "deny".
- зелёный python, ambiguous (в3, ниже) и всё прочее → exit 0 (defer к штатным allow/ask/deny
  rules; venv python уже в allow, доктринальное красное держит ask/deny-слой settings).
- FAIL-SAFE РАЗБОРА: сбой разбора цепи → скан по СЫРОЙ команде, т.е. КРАСНЕЕ (см. _scan);
  доктринальные проверки (red-токены, sqlite, процессы, секреты) идут ДО любого ambiguous-выхода.
ПЕРЕКЛАССИФИКАЦИЯ 02.07 (меньше шума, ask только где «да» реально решает):
- tests/*.py и gate.py → ранний defer БЕЗ чтения содержимого (моки по определению);
- memory.db через python-код → defer (своя БД бота; sqlite3 CLI остаётся ask в settings);
  SQL-write в ИНУЮ .db → ask;
- git push / systemctl restart splinter → авто на уровне settings (restart идёт только по «да»
  владельца в ТЗ — терминальный prompt был двойным вопросом); systemctl stop остался ask.
ТРИ КЛАССА ПОВЕРХ python-скана (23.07.2026) — python-скан при этом НЕ ослаблен:
(а) ЧЁРНЫЙ СПИСОК ПРОЦЕССОВ: kill/pkill/`systemctl kill|stop` по боевым процессам контура
    (splinter, orchestrator-daemon, userbot, moderation_bot, pc_agent) и `kill` по PID 1 →
    HARD-BLOCK: permissionDecision="deny", карточка владельцу НЕ шлётся, approve НЕВОЗМОЖЕН,
    строка `proc_hard_block` в GUARD_LOG. Отложенный `systemd-run … systemctl stop|kill` —
    тот же HARD-BLOCK (обёртки раскрываются). systemctl restart|start СВОИХ сервисов —
    ЗЕЛЁНОЕ (defer к allow-правилам settings): это штатный поток (подъём после гейта /
    self-update / упавшего сервиса), его гейтит оркестратор ДО команды; ask здесь ломал
    headless — красное не исполнялось даже после «да» владельца (тупик задач 339/340).
    Остальные глаголы по своим (mask/disable/reload…) — обычное красное с карточкой.
(б) ДАННЫЕ ≠ КОМАНДА: красное слово внутри ПОИСКОВОГО ШАБЛОНА grep/rg/sed/awk (аргумент
    -n/-e/-E либо первый позиционный) — это ДАННЫЕ, они НЕ краснят команду. Шаблон вырезается
    ТОЛЬКО из скан-представления и ТОЛЬКО в СВОЁМ сегменте цепи; ОПЕРАНДЫ (файлы!) остаются под
    сканом, а токен с признаками ИСПОЛНЕНИЯ ($(…)/`…`/system("…")) не вырезается и раскрывается
    отдельным сегментом. Итог: `grep -n ".env" f.py` — зелёное, `grep -n foo .env` — блок.
(в) HARD-BLOCK .env: обращение к файлу секретов в ЛЮБОЙ позиции цепи (после &&/;/|, внутри
    `bash -c`, $(…), `…`) → deny БЕЗ карточки, лог `env_hard_block`, approve НЕВОЗМОЖЕН.
    Сканируется ТОЛЬКО ТЕКСТ КОМАНДЫ: содержимое .py-целей НЕ проверяется (иначе любой боевой
    скрипт с load_env() встал бы намертво).
в3 (23.07.2026, доктрина «шквал подтверждений»): AMBIGUOUS САМ ПО СЕБЕ НЕ КРАСНЫЙ. Незнакомая
команда, python без внятной цели (stdin/heredoc `python3 - <<EOF`, неизвестный -m, нечитаемый
.py, кривое квотирование) → defer к слоям settings (allow/ask/deny). Красное гарда — ТОЛЬКО
доктринальный список: живые таблицы/CRM/деньги (RED_TOKENS), файл секретов, SQL-write вне
memory.db (с в3 ловится и в heredoc/stdin — проверка поднята ДО ambiguous-выходов), kill/stop
боевых процессов, PID 1. clasp/массовые удаления/git-force держит ask/deny-слой settings —
гард их не дублирует. Конверт «не распознал операцию» упразднён вместе с ambiguous-ask
(хелперы дедупа карточек оставлены: их контракт держит test_guard_inbox).
Единая точка классификации — classify(cmd, cwd) → (kind, hit, blob), kind ∈ block/red/ambiguous/
green; can_approve(kind, hit) и decision(kind, hit, reason) — чистые (их зовут тесты, ничего не
исполняя). Направление fail-safe разбора прежнее: сбой разбора → скан по СЫРОЙ команде (краснее).

НЕ трогает реальный гейт записи confirmed=true в Bridge (ReadFleet.js) — тот независим (третий слой защиты).
Зона 🟢 (конфиг агента; прод Splinter/таблицы не трогает). НИЧЕГО не печатает в stdout, кроме JSON-решения.
"""
import sys, os, json, re, shlex, time, fcntl, hashlib

PROJECT = "/root/turbobaby-manager-bot"

# WRITE-признаки рабочих таблиц/денег/удаления. confirmed=true — УНИВЕРСАЛЬНЫЙ (Bridge требует его на КАЖДУЮ
# запись в Лист1 → любая боевая запись его содержит). write_doc/state_set/add_event/service_upsert/o3_task_*/
# set_info_pin — ЗЕЛЁНЫЕ (Brain-журналы / Bot Data / свои таблицы), их здесь НЕТ намеренно.
# Порядок ВАЖЕН: конкретные действия ПЕРВЫМИ, обобщённые (DOWRITE/confirmed) — ПОСЛЕДНИМИ, чтобы скрипт с
# set_fleet_oil+confirmed=true распознался как set_fleet_oil (конкретно), а не как безликое confirmed.
RED_TOKEN_HIT = {
    "set_fleet_oil": "set_fleet_oil", "set_fleet_service": "set_fleet_service",
    "add_transaction": "add_transaction", "void_last": "void_last",
    "create_booking": "create_booking", "activate_booking": "activate_booking",
    "closing_upsert": "closing_upsert", "delete_event": "delete_event",
    "DOWRITE": "DOWRITE",
    "confirmed=true": "confirmed", "confirmed=True": "confirmed", "confirmed = true": "confirmed",
    '"confirmed": true': "confirmed", '"confirmed":true': "confirmed",
}
RED_TOKENS = tuple(RED_TOKEN_HIT.keys())
_GREEN_MODULES = {"py_compile", "json.tool", "pytest", "unittest", "pip", "venv", "http.server",
                  "platform", "sysconfig", "site"}
# Инфо-флаги интерпретатора: НИЧЕГО не исполняют (печатают версию/справку) → зелёное, даже без .py-цели.
# Убирает ложный ambiguous-ask на `venv/bin/python3 --version` (нет target → раньше падало в ask, хотя
# venv python в allow). Сужение неоднозначности (06.07.2026) — red-список НЕ трогает.
_INFO_FLAGS = {"--version", "-V", "-VV", "--help", "-h"}
_ENV_ASSIGN = re.compile(r"^\w+=")   # env-префикс VAR=val перед интерпретатором (PRETOOL_NOPUSH=1 …)
_SQLITE_WRITE = re.compile(r"\b(UPDATE|DELETE\s+FROM|INSERT\s+INTO|DROP\s+TABLE)\b", re.IGNORECASE)

# Дедуп ambiguous-карточек (UX-фикс 08.07.2026, спам-инцидент задачи 163). в3 (23.07.2026):
# ambiguous → defer, путь ambiguous-карточек из main() удалён — дедуп в бою НЕ зовётся. Хелперы
# (_dedup_bump/_dedup_save_mid/_edit) оставлены как движок повторных карточек: их контракт держит
# test_guard_inbox (5), формат стора /tmp на сессию с TTL прежний.
_DEDUP_DIR = os.environ.get("PRETOOL_DEDUP_DIR") or "/tmp/cc_pretool_dedup"
_DEDUP_TTL = 4 * 3600

# Guard-маркер для headless-задач (шаг 2/6 родитель 185): когда CC_TASK_ID задан в env,
# красный блок пишет маркер-файл → демон видит → гасит claude-подпроцесс → needs_approval.
GUARD_BLOCK_DIR = "/tmp/cc_guard_block"

# ── HARD-BLOCK: журнал жёстких блоков (JSONL). Путь берётся В МОМЕНТ ЗАПИСИ (тесты подменяют
# PRETOOL_GUARD_LOG). Пишется ТОЛЬКО факт блока: событие + сама команда; значений секретов в
# команде нет (файл секретов не читается — он как раз заблокирован).
GUARD_LOG = "/tmp/cc_pretool_guard.log"
HARD_BLOCK_HITS = ("proc_hard_block", "env_hard_block")

# Боевые процессы контура: остановка = обрыв живых задач/очередей → агенту НЕЛЬЗЯ вообще.
# Сверка по НОРМАЛИЗОВАННОМУ токену (нижний регистр, «-» и «.» → «_»), т.е. splinter.service,
# orchestrator-daemon, /root/…/moderation_bot.py — одно и то же имя.
_PROTECTED_PROCS = ("splinter", "orchestrator_daemon", "userbot", "moderation_bot", "pc_agent")
_KILL_CMDS = {"kill", "pkill"}
_HARD_VERBS = {"kill", "stop"}                    # systemctl kill|stop → жёстко
_GREEN_VERBS = {"restart", "start"}               # свои сервисы: штатный поток → defer (в2, 339/340)
_SVC_VERBS = {"kill", "stop", "restart", "start", "reload", "try-restart",
              "force-reload", "enable", "disable", "mask", "unmask"}
# Поисковые утилиты: их ШАБЛОН — данные (класс «данные ≠ команда»).
_SEARCH_CMDS = {"grep", "egrep", "fgrep", "zgrep", "rg", "ag", "ack", "sed", "awk", "gawk", "mawk"}
_PATTERN_FLAGS = {"-e", "-E", "-n", "--regexp", "--expression"}
# Обёртки: команда-цель идёт ПОСЛЕ них (иначе `sudo systemctl stop splinter` проскочил бы).
_WRAPPERS = {"sudo", "doas", "env", "nohup", "nice", "ionice", "time", "timeout",
             "stdbuf", "xargs", "systemd-run"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_ENV_FILE = re.compile(r"(?:^|[^\w.\-])\.env[\w.\-]*")          # .env, .env.local, /root/app/.env
# Признаки ИСПОЛНЕНИЯ внутри «шаблона»: такой токен шаблоном не считаем (дыра
# `awk 'BEGIN{system("…")}'` / `grep -e "$(…)"` закрыта) — он и остаётся под сканом, и раскрывается.
_EXEC_IN_PATTERN = re.compile(r"\$\(|`|\bsystem\s*\(|\bpopen\s*\(|\|\s*['\"]?\s*(?:sh|bash|zsh|xargs)\b")
_SUBST = re.compile(r"\$\(([^()]{1,400})\)|`([^`]{1,400})`"
                    r"|(?:system|popen)\s*\(\s*['\"]([^'\"]{1,400})['\"]")

_BLOCK_TEXT = {
    "proc_hard_block": "⛔ ЖЁСТКИЙ БЛОК (proc_hard_block): остановка боевого процесса контура%s. "
                       "Карточка владельцу НЕ шлётся, approve НЕВОЗМОЖЕН — агент боевые процессы "
                       "не гасит НИКОГДА (обрыв живых задач и очередей). Нужна остановка — "
                       "требуется решение владельца: переставь задачу в 328 после его ответа.",
    "env_hard_block": "⛔ ЖЁСТКИЙ БЛОК (env_hard_block): обращение к файлу секретов%s. "
                      "Карточка владельцу НЕ шлётся, approve НЕВОЗМОЖЕН — секреты агенту не "
                      "показываются ни в каком виде. Нужно значение ключа — владелец передаёт его сам.",
}


def _guard_log(event, cmd, why=""):
    """Строка JSONL о жёстком блоке. Best-effort: сбой журнала НЕ отменяет блок (решение важнее)."""
    try:
        line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event,
                           "cmd": " ".join((cmd or "").split())[:400], "why": why},
                          ensure_ascii=False)
        with open(os.environ.get("PRETOOL_GUARD_LOG") or GUARD_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _guard_write_marker(task_id, hit, card):
    """Записать маркер красного блока для демона-наблюдателя."""
    if not task_id:
        return
    try:
        os.makedirs(GUARD_BLOCK_DIR, exist_ok=True)
        path = os.path.join(GUARD_BLOCK_DIR, f"{task_id}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"task_id": task_id, "hit": hit, "card": card}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _defer():
    sys.exit(0)   # ничего не печатаем → штатный permission-flow (allow/ask rules)


def can_approve(kind, hit=""):
    """False = ЖЁСТКИЙ БЛОК: ни карточки владельцу, ни «да» — обойти нельзя (deny хука).
    Единственный источник правды: по нему же main() решает, слать ли карточку/конверт."""
    return not (kind == "block" or hit in HARD_BLOCK_HITS)


def decision(kind, hit, reason):
    """Чистая функция «классификация → решение хука» (её зовут тесты, ничего не исполняя):
    block → deny (approve НЕВОЗМОЖЕН), red → ask, ambiguous/green → None (defer). в3 23.07.2026:
    ambiguous сам по себе не красный — решают слои settings (доктринальное красное там ask/deny)."""
    if kind in ("green", "ambiguous"):
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask" if can_approve(kind, hit) else "deny",
        "permissionDecisionReason": reason}}


def _emit(d):
    if d:
        print(json.dumps(d, ensure_ascii=False))
    sys.exit(0)


def _ask(reason):
    _emit(decision("red", "", reason))


# per-действие: (Что — таблица/операция, Последствия, Проверь). Объект (байк/клиент/сумма/док/таблица)
# доклеивается к «Что» из _detail(), если извлёкся. Тексты короткие, человеческие — НЕ дамп кода.
_ACTIONS = {
    "confirmed": ("БОЕВАЯ запись в Лист1/CRM (confirmed=true)",
                  "уйдёт в реальный учёт парка/аренд; люди пишут туда руками параллельно",
                  "та ли строка/байк и значение — откат не вернёт затёртый чужой ввод"),
    "set_fleet_oil": ("запись «ТО масло» в Лист1 Байки (колонка I)",
                      "изменит учёт ТО байка в живой таблице парка",
                      "тот ли байк и пробег — правит боевую строку парка"),
    "set_fleet_service": ("запись планового ТО в Лист1 (редуктор/ABS/возд.фильтр)",
                          "изменит график ТО байка в живой таблице парка",
                          "тот ли байк и вид ТО — правит боевую строку парка"),
    "add_transaction": ("проводка ДЕНЕГ в кассу (Money/Cashflow)",
                        "изменит денежный учёт — баланс кошелька сдвинется",
                        "та ли сумма, знак (+/−) и кошелёк — откат = ручной сторно"),
    "void_last": ("отмена последней денежной проводки (Money)",
                  "откатит последнюю проводку в кассе",
                  "точно ли ПОСЛЕДНЯЯ проводка — та, что нужно снять"),
    "create_booking": ("создание брони в CRM «клиенты»",
                       "заведёт новую строку аренды в живой CRM",
                       "тот ли клиент/байк/даты — строка попадёт в биллинг"),
    "activate_booking": ("активация брони → «В аренде» (CRM)",
                         "переведёт байк в статус аренды в живой CRM",
                         "тот ли байк/клиент — сменит боевой статус аренды"),
    "closing_upsert": ("запись закрытия аренды (CRM)",
                       "закроет аренду и зафиксирует расчёт в живой CRM",
                       "тот ли клиент/байк и суммы закрытия — правит биллинг"),
    "delete_event": ("УДАЛЕНИЕ события из истории",
                     "сотрёт запись безвозвратно",
                     "то ли событие — удаление необратимо"),
    "DOWRITE": ("скрипт помечен DOWRITE=1 — реальная запись (не dry-run)",
                "выполнит боевую запись в рабочие данные",
                "прочитай, ЧТО именно пишет скрипт — это не пробный прогон"),
    "proc_ctl": ("остановка/перезапуск процесса или systemd-сервиса",
                 "процесс прервётся: живые задачи, очереди и открытые сессии не досчитаются",
                 "тот ли процесс/юнит и переживёт ли контур его паузу — restart|start своих "
                 "сервисов идёт зелёным (штатный поток), сюда попадает только НЕштатное"),
    "sqlite": ("SQL-запись в БД вне memory.db (UPDATE/DELETE/INSERT/DROP)",
               "изменит НЕизвестную базу данных (не свою memory.db)",
               "какая это БД и почему пишем не в memory.db — memory.db через код шёл бы без вопроса"),
}
# в3 23.07.2026: штатно НЕдостижимо (ambiguous → defer, main() до карточки не доходит);
# оставлено фолбэком _card на случай red-hit вне _ACTIONS (карточка не падает, а страшнеет).
_AMBIGUOUS = ("не распознал операцию — скрипт может писать в рабочие данные, но точную операцию не разобрал",
              "неизвестно — не могу гарантировать, что скрипт только читает",
              "команда в карточке — подтверждай, только если понимаешь, что она делает")


def _find(patterns, blob):
    for p in patterns:
        m = re.search(p, blob, re.IGNORECASE)
        if m:
            g = (m.group(1) or "").strip().strip('\'"').strip()
            if g:
                return g
    return ""


def _detail(hit, blob):
    """Человеческий ОБЪЕКТ операции из argv/тела скрипта. '' если не извлеклось (тогда карточка — по действию+таблице)."""
    bits = []
    if hit in ("confirmed", "set_fleet_oil", "set_fleet_service",
               "create_booking", "activate_booking", "closing_upsert", "delete_event"):
        bike = _find([r"\bplate\s*[=:]\s*['\"]?([A-Za-z0-9][A-Za-z0-9\- ]{1,11})",
                      r"\bbike(?:_id|_no|_num|_number)?\s*[=:]\s*['\"]?([A-Za-z0-9][A-Za-z0-9\- ]{1,11})",
                      r"['\"]plate['\"]\s*:\s*['\"]([A-Za-z0-9][A-Za-z0-9\- ]{1,11})"], blob)
        if bike:
            bits.append("байк " + bike)
        client = _find([r"\bclient\s*[=:]\s*['\"]?([^\"',)]{2,30})",
                        r"['\"]client['\"]\s*:\s*['\"]([^\"']{2,30})"], blob)
        if client:
            bits.append("клиент " + client)
        val = _find([r"\b(?:mileage|km|odo|пробег|value|val)\s*[=:]\s*['\"]?(\d{2,7})"], blob)
        if val and hit in ("set_fleet_oil", "set_fleet_service"):
            bits.append("пробег " + val)
    elif hit in ("add_transaction", "void_last"):
        amount = _find([r"\bamount\s*[=:]\s*['\"]?(-?\d[\d ]{0,9})",
                        r"['\"]amount['\"]\s*:\s*['\"]?(-?\d+)",
                        r"\bsum\s*[=:]\s*['\"]?(-?\d[\d ]{0,9})"], blob)
        if amount:
            bits.append("сумма " + amount.strip())
        wallet = _find([r"\bwallet\s*[=:]\s*['\"]?([\w\- ]{2,20})",
                        r"\baccount\s*[=:]\s*['\"]?([\w\- ]{2,20})"], blob)
        if wallet:
            bits.append("кошелёк " + wallet.strip())
    elif hit == "sqlite":
        table = _find([r"UPDATE\s+['\"`]?(\w+)", r"INSERT\s+INTO\s+['\"`]?(\w+)",
                       r"DELETE\s+FROM\s+['\"`]?(\w+)"], blob)
        if table:
            bits.append("таблица " + table)
    elif hit == "DOWRITE":
        doc = _find([r"write_doc\s*\(\s*(?:name|id)\s*=\s*['\"]?([\w\-]+)"], blob)
        if doc:
            bits.append("док " + doc)
    elif hit == "proc_ctl":
        tgt = _find([r"proc_target=([^\n]{1,60})"], blob)   # маркер кладёт classify()
        if tgt:
            bits.append("цель " + tgt)
    return " — " + ", ".join(bits) if bits else ""


def _is_test_script(cmd):
    """Тестовый прогон: запускаемый .py лежит в scratchpad (/tmp/claude-*/…/scratchpad/) или несёт
    _dryrun/_test в имени. Влияние: 🧪-пометка первой строкой карточки И пуш в Telegram НЕ шлётся
    (фикс утечек 01–05.07: тестовые красные карточки летели Филиппу в личку; терминальная карточка
    и решение ask ОСТАЮТСЯ — классификацию red/ambiguous/green это НЕ меняет). Боевые скрипты из
    репо — без пометки, с пушем."""
    try:
        toks = shlex.split(cmd)
    except Exception:
        toks = cmd.split()
    for t in toks:
        if not t.endswith(".py"):
            continue
        if "/tmp/claude-" in t and "/scratchpad/" in t:
            return True
        base = os.path.basename(t)
        if "_dryrun" in base or "_test" in base:
            return True
    return False


def _card(hit, blob="", test=False, cmd="", count=1):
    ambiguous = hit == "ambiguous" or hit not in _ACTIONS
    if ambiguous:
        what, cons, check = _AMBIGUOUS
    else:
        what, cons, check = _ACTIONS[hit]
        what += _detail(hit, blob)
    head = "🧪 ТЕСТ (dry-run, не реальная операция)\n" if test else ""
    # UX-фикс 08.07 (инцидент 163): ambiguous-карточка НЕСЁТ саму команду — владелец решает прямо из
    # уведомления («глянь выше вручную» в headless некуда). Повтор той же команды в задаче → счётчик ×N.
    cmd_line = ""
    if ambiguous:
        c = " ".join((cmd or "").split())
        if c:
            cmd_line = "Команда: " + (c[:200] + "…" if len(c) > 200 else c) + "\n"
    rep = ("Повтор: ×%d — та же команда в этой задаче (карточка обновлена, новых не шлю)\n" % count) \
        if count > 1 else ""
    return (head +
            "🔴 КРАСНОЕ\n" + rep +
            "Что: " + what + "\n" + cmd_line +
            "Последствия: " + cons + "\n"
            "Проверь: " + check + " — жду твоё «да».")


def _dedup_path(session):
    sid = re.sub(r"[^\w\-]", "_", str(session or "nosession"))[:64]
    return os.path.join(_DEDUP_DIR, sid + ".json")


def _dedup_bump(session, cmd):
    """Счётчик одинаковой ambiguous-команды в рамках сессии (= headless-задачи). → (count, mid|None):
    count — какой это раз (1 = первая карточка), mid — message_id уже висящей Telegram-карточки.
    FAIL-SAFE: любой сбой стора → (1, None) = прежнее поведение (новая карточка), не хуже."""
    try:
        os.makedirs(_DEDUP_DIR, exist_ok=True)
        path = _dedup_path(session)
        key = hashlib.sha1(cmd.encode("utf-8", "ignore")).hexdigest()[:16]
        with open(path + ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            ent = data.get(key) if isinstance(data, dict) else None
            now = time.time()
            if not isinstance(ent, dict) or now - float(ent.get("ts", 0)) > _DEDUP_TTL:
                ent = {"count": 0, "mid": None}
            ent["count"] = int(ent.get("count", 0)) + 1
            ent["ts"] = now
            data[key] = ent
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        return ent["count"], ent.get("mid")
    except Exception:
        return 1, None


def _dedup_save_mid(session, cmd, mid):
    """Запомнить (message_id, chat_id) первой карточки (под тем же lock; tuple → JSON-список) —
    повтор будет править ЕЁ в том же чате (инбокс/личка). Best-effort."""
    try:
        path = _dedup_path(session)
        key = hashlib.sha1(cmd.encode("utf-8", "ignore")).hexdigest()[:16]
        with open(path + ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get(key), dict):
                data[key]["mid"] = mid
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, path)
    except Exception:
        pass


def _push(card):
    """Отправить карточку. → (message_id, chat_id)|None (нужно дедупу: повтор правит ЭТУ карточку
    В ТОМ ЖЕ чате). Маршрут 13.07.2026 — внутри notify.send_card: тема-инбокс HQ 1160, личка-фолбэк."""
    if os.environ.get("PRETOOL_NOPUSH") == "1":
        return None   # тест-режим валидации хука: не спамить Telegram красными карточками
    try:
        sys.path.insert(0, PROJECT)
        from notify import send_card
        return send_card(card)
    except Exception:
        return None   # пуш — вторичный канал; не роняем решение из-за сети/ошибки


def _edit(mid, card):
    """Повтор той же команды → правка УЖЕ висящей карточки (счётчик ×N). mid из стора: [message_id,
    chat_id] (карточки 13.07+ несут чат: инбокс 1160 или личка-фолбэк) либо голый id (легаси-записи
    до маршрута инбокса → личка). Сбой → молча (спама нет)."""
    if os.environ.get("PRETOOL_NOPUSH") == "1" or not mid:
        return
    chat = None
    if isinstance(mid, (list, tuple)):
        chat = mid[1] if len(mid) > 1 else None
        mid = mid[0] if mid else None
    if not mid:
        return
    try:
        sys.path.insert(0, PROJECT)
        from notify import edit_card
        edit_card(mid, card, chat_id=chat)
    except Exception:
        pass


def _is_python(cmd):
    return re.search(r"(^|\s|/)(python3?|venv/bin/python3?)(\s|$)", cmd) is not None


def _strip_git_msg(cmd):
    """Нюанс bd5d516 (фикс 12.07.2026): слово-интерпретатор ВНУТРИ текста git commit -m
    («фикс python скрипта») попадало в скан _is_python → git-команда шла в _analyze → ветка -m
    видела «не-зелёный модуль» → ложная ambiguous-карточка. Для СКАНА интерпретатора из git-команды
    вырезаются payload'ы -m/-am/--message (формы: -m <txt>, -am <txt>, --message <txt>,
    --message=<txt>, приклеенное -m<txt>); классификация самого git НЕ меняется — git без
    интерпретатора вне -m остаётся не-python → defer к штатным allow/ask rules, как и был.
    Не git (учитывая env-префикс) / сбой разбора → команда КАК ЕСТЬ (fail-safe: скан полный,
    интерпретатор в компаунде `git … && python3 evil.py` по-прежнему ловится)."""
    try:
        toks = shlex.split(cmd)
    except Exception:
        return cmd
    i0 = 0
    while i0 < len(toks) and _ENV_ASSIGN.match(toks[i0]):
        i0 += 1
    if i0 >= len(toks) or toks[i0] != "git":
        return cmd
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t in ("-m", "-am", "--message"):
            out.append(t)
            i += 2
            continue
        if t.startswith("--message="):
            out.append("--message")
            i += 1
            continue
        if re.match(r"^-a?m.", t):        # приклеенный payload: -mтекст / -amтекст
            out.append("-m")
            i += 1
            continue
        out.append(t)
        i += 1
    return " ".join(out)


def _args_after_interp(toks):
    """Аргументы ПОСЛЕ интерпретатора: срезает ведущие VAR=val (env-префикс) и сам python-токен.
    Фикс инцидента 163 (08.07.2026): `PRETOOL_NOPUSH=1 venv/bin/python3 --version` считал интерпретатор
    обычным аргументом → инфо-флаг не распознавался → ложный ambiguous → конверт-спам. Probe-паттерны
    без python (node --check / node tests/*harness* / cat / grep / diff) сюда НЕ доходят вовсе —
    main() дефёрит не-python до анализа (они «может писать» не считаются by construction)."""
    i = 0
    while i < len(toks) and _ENV_ASSIGN.match(toks[i]):
        i += 1
    if i < len(toks) and re.search(r"(^|/)python3?$", toks[i]):
        return toks[i + 1:]
    return toks[1:]   # интерпретатор не опознан токеном → прежнее поведение (fail-safe)


def _py_targets(cmd, cwd):
    """Все .py-цели команды → list существующих абсолютных путей (realpath). Непрочитавшиеся пути пропускаются."""
    try:
        toks = shlex.split(cmd)
    except Exception:
        return []
    out = []
    for t in toks:
        if not t.endswith(".py"):
            continue
        for cand in (t, os.path.join(cwd or PROJECT, t), os.path.join(PROJECT, t)):
            if os.path.isfile(cand):
                out.append(os.path.realpath(cand))
                break
    return out


def _is_trusted_test(cmd, cwd):
    """Ранний defer БЕЗ чтения содержимого (переклассификация 02.07): запуск тестов/гейта = зелёная рутина
    по определению (tests/* — моки, gate.py их прогоняет). True ТОЛЬКО если есть ≥1 .py-цель и ВСЕ цели
    лежат в PROJECT/tests/ или равны PROJECT/gate.py. Скрипт вне tests/ рядом в команде → НЕ доверяем."""
    targets = _py_targets(cmd, cwd)
    if not targets:
        return False
    tests_dir = os.path.join(PROJECT, "tests") + os.sep
    gate = os.path.join(PROJECT, "gate.py")
    return all(p.startswith(tests_dir) or p == gate for p in targets)


def _read_file(path, cwd):
    for cand in (path, os.path.join(cwd or PROJECT, path), os.path.join(PROJECT, path)):
        try:
            if os.path.isfile(cand):
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read(400000)
        except Exception:
            continue
    return None


# ══ РАЗБОР ЦЕПОЧКИ: сегменты, слово-команда, поисковый шаблон = ДАННЫЕ ══════════════════════
def _base(t):
    """Имя команды без пути и кавычек, нижним регистром (/usr/bin/systemctl → systemctl)."""
    return os.path.basename((t or "").strip("'\"")).lower()


def _tokens(seg):
    try:
        return shlex.split(seg)
    except Exception:
        return seg.split()          # кривое квотирование → грубые токены (скан всё равно полный)


def _split_segments(cmd):
    """Цепочку → сегменты по шелл-разделителям (&&, ||, ;, |, &, перевод строки) ВНЕ КАВЫЧЕК.
    Кавычки уважаются намеренно: в `grep -n "a|b" f` труба — часть ШАБЛОНА, а не разделитель;
    зато `grep -e x&&pkill …` разъедется на два сегмента, и второй под сканом останется."""
    segs, buf, q, i, n = [], [], None, 0, len(cmd or "")
    while i < n:
        ch = cmd[i]
        if q:
            buf.append(ch)
            if ch == "\\" and q == '"' and i + 1 < n:
                buf.append(cmd[i + 1]); i += 2; continue
            if ch == q:
                q = None
            i += 1; continue
        if ch in "'\"":
            q = ch; buf.append(ch); i += 1; continue
        if ch == "\\" and i + 1 < n:
            buf.append(ch); buf.append(cmd[i + 1]); i += 2; continue
        if cmd[i:i + 2] in ("&&", "||"):
            segs.append("".join(buf)); buf = []; i += 2; continue
        if ch in ";|&\n":
            segs.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    segs.append("".join(buf))
    return [s for s in segs if s.strip()]


def _cmd_index(toks):
    """Индекс слова-КОМАНДЫ сегмента: пропускает env-префикс (VAR=val) и обёртки
    (sudo/env/nohup/timeout N/xargs/systemd-run…). None — команды в сегменте нет.
    Разбор СТРУКТУРНЫЙ, а не по подстроке: `cat splinter.log` командой-убийцей не станет."""
    i, hops = 0, 0
    while i < len(toks) and _ENV_ASSIGN.match(toks[i]):
        i += 1
    while i < len(toks) and hops < 4:
        name = _base(toks[i])
        if name not in _WRAPPERS:
            return i
        i += 1
        while i < len(toks) and toks[i].startswith("-"):
            i += 1
        if name in ("timeout", "nice", "ionice") and i < len(toks) and re.match(r"^[\d.]+[smhd]?$", toks[i]):
            i += 1
        while i < len(toks) and _ENV_ASSIGN.match(toks[i]):
            i += 1
        hops += 1
    return i if i < len(toks) else None


def _strip_search_pattern(toks, idx):
    """→ (токены БЕЗ поискового шаблона, список вырезанных). Шаблон = аргумент -n/-e/-E либо
    ПЕРВЫЙ позиционный у grep/rg/sed/awk. Операнды-ФАЙЛЫ и прочие токены не трогаем — иначе
    `grep -n foo .env` перестал бы блокироваться. Токен с признаком исполнения не вырезается."""
    if idx is None or _base(toks[idx]) not in _SEARCH_CMDS:
        return toks, []
    drop, pat_seen, i = set(), False, idx + 1
    while i < len(toks):
        t = toks[i]
        if t in _PATTERN_FLAGS and i + 1 < len(toks):
            drop.add(i + 1); pat_seen = True; i += 2; continue
        if t.startswith("-") and t != "-":
            i += 1; continue
        if not pat_seen:
            drop.add(i); pat_seen = True
        i += 1
    keep, dropped = [], []
    for k, t in enumerate(toks):
        if k in drop and not _EXEC_IN_PATTERN.search(t):
            dropped.append(t)
        else:
            keep.append(t)
    return keep, dropped


def _mask(seg, dropped):
    """Убрать шаблоны из ТЕКСТА сегмента (в кавычках или без), сохранив всё прочее ДОСЛОВНО —
    red-скан не должен слабеть от переклейки токенов. Не нашли форму → текст как есть (краснее)."""
    out = seg
    for d in dropped:
        for form in ('"' + d + '"', "'" + d + "'", d):
            if d and form in out:
                out = out.replace(form, " ", 1)
                break
    return out


def _subst_inners(seg):
    """Тела подстановок $(…) / `…` / system("…") / popen("…") — это ИСПОЛНЯЕМОЕ, а не данные."""
    out = []
    for m in _SUBST.finditer(seg or ""):
        for g in m.groups():
            if g and g.strip():
                out.append(g)
    return out[:8]


def _units(cmd, depth=0):
    """Цепочка → список пар (токены сегмента, текст сегмента) с ВЫРЕЗАННЫМИ поисковыми шаблонами.
    Тела подстановок и `sh -c "…"` добавляются ОТДЕЛЬНЫМИ парами (иначе прятались бы от скана)."""
    out = []
    if depth > 2 or not (cmd or "").strip():
        return out
    for seg in _split_segments(cmd):
        for inner in _subst_inners(seg):
            out.extend(_units(inner, depth + 1))
        clean = _strip_git_msg(seg)          # текст git -m — ДАННЫЕ (нюанс bd5d516)
        toks = _tokens(clean)
        i = _cmd_index(toks)
        if i is not None and _base(toks[i]) in _SHELLS:
            for j in range(i + 1, len(toks) - 1):
                if toks[j] == "-c":
                    out.extend(_units(toks[j + 1], depth + 1))
                    break
        keep, dropped = _strip_search_pattern(toks, i)
        out.append((keep, _mask(clean, dropped)))
        if len(out) > 60:
            break
    return out


def _scan(cmd):
    """→ (units, скан-текст). Сбой разбора → СЫРАЯ команда (fail-safe: скан полнее, краснит охотнее)."""
    try:
        units = _units(cmd)
        text = " ".join(t for _, t in units)
        return units, (text if text.strip() else _strip_git_msg(cmd))
    except Exception:
        return [(_tokens(cmd), cmd)], _strip_git_msg(cmd)


# ══ (а) ЧЁРНЫЙ СПИСОК ПРОЦЕССОВ ════════════════════════════════════════════════════════════
def _norm(t):
    return re.sub(r"[^a-z0-9_]", "", (t or "").lower().replace("-", "_").replace(".", "_"))


def _protected_in(args):
    """Первый аргумент, называющий боевой процесс контура ('' — таких нет)."""
    for a in args:
        v = a
        if v.startswith("-"):
            if "=" not in v:              # --signal=SIGKILL: цель может прятаться в значении флага
                continue
            v = v.split("=", 1)[1]
        n = _norm(v)
        for p in _PROTECTED_PROCS:
            if p in n:
                return a
    return ""


def _pid1_in(args):
    """PID 1 (init/systemd) среди целей `kill`. Аргументы сигналов (-s TERM, -n 9) пропускаются."""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-s", "--signal", "-n", "-q", "--queue"):
            i += 2; continue
        if a.startswith("-"):
            i += 1; continue
        if a.strip() == "1":
            return True
        i += 1
    return False


def _systemctl_parts(args):
    """→ (глагол, [юниты]). Флаги и их аргументы (-H/-M) отбрасываются."""
    verb, names, i = "", [], 0
    while i < len(args):
        a = args[i]
        if a in ("-H", "--host", "-M", "--machine"):
            i += 2; continue
        if a.startswith("-"):
            i += 1; continue
        if not verb:
            verb = a.lower()
        else:
            names.append(a)
        i += 1
    return verb, names


def _proc_class(units):
    """→ ('block', цель) | ('red', цель) | None. Блок ищем во ВСЕЙ цепи (красное первого сегмента
    не должно заслонять жёсткое во втором: `systemctl restart nginx && pkill -9 splinter`)."""
    red = None
    for toks, _text in units:
        i = _cmd_index(toks)
        if i is None:
            continue
        name, args = _base(toks[i]), toks[i + 1:]
        if name in _KILL_CMDS:
            tgt = _protected_in(args)
            if tgt:
                return "block", name + " " + tgt
            if name == "kill" and _pid1_in(args):
                return "block", "kill PID 1 (init/systemd)"
            red = red or ("red", " ".join([name] + args[:3]))
        elif name == "systemctl":
            verb, names = _systemctl_parts(args)
            tgt = _protected_in(names)
            if verb in _HARD_VERBS and tgt:
                return "block", "systemctl " + verb + " " + tgt
            # restart|start своих → ЗЕЛЁНОЕ (в2): штатный поток, гейт живёт выше (оркестратор+settings)
            if verb in _HARD_VERBS or (verb in _SVC_VERBS and tgt and verb not in _GREEN_VERBS):
                red = red or ("red", ("systemctl " + verb + " " + (tgt or " ".join(names[:2]))).strip())
    return red


def classify(cmd, cwd=None):
    """ЕДИНАЯ точка классификации (её зовут main() и тесты — исполнять ничего не требуется).
    → (kind, hit, blob): kind ∈ {'block','red','ambiguous','green'}; hit — ключ действия и,
    для жёстких блоков, имя события в журнале; blob — текст для _detail()/причины.
    Порядок: жёсткое (процессы → секреты) ПЕРЕД всем остальным — иначе `cat .env && python gate.py`
    ушёл бы в зелёное на раннем defer доверенных тестов/гейта."""
    cmd = cmd or ""
    units, scan = _scan(cmd)
    proc = _proc_class(units)
    if proc and proc[0] == "block":
        return "block", "proc_hard_block", cmd + "\nproc_target=" + proc[1]
    m = _ENV_FILE.search(scan)
    if m:
        return "block", "env_hard_block", cmd + "\nenv_target=" + m.group(0).strip()
    if proc:
        return "red", "proc_ctl", cmd + "\nproc_target=" + proc[1]
    if not _is_python(scan):
        return "green", "", cmd          # не-python и не процесс/секрет → штатные allow/ask rules
    return _analyze(cmd, cwd or PROJECT, scan)


def _block_reason(hit, blob=""):
    tgt = _find([r"proc_target=([^\n]{1,60})", r"env_target=([^\n]{1,60})"], blob)
    return _BLOCK_TEXT[hit] % ((" — " + tgt) if tgt else "")


def _analyze(cmd, cwd, scan=None):
    """→ (kind, hit, blob): kind ∈ {'red','ambiguous','green'}; hit — ключ действия; blob — текст для _detail()."""
    scan = cmd if scan is None else scan
    # 0) тесты/гейт → зелёное СРАЗУ, до сканирования содержимого (моки по определению; шум ask убран 02.07)
    if _is_trusted_test(cmd, cwd):
        return "green", "", cmd
    # 1) быстрый греп по САМОЙ команде (инлайн -c, env DOWRITE, argv) — по СКАН-представлению:
    #    красное слово в поисковом шаблоне это данные, а не операция (класс «данные ≠ команда»)
    for tok in RED_TOKENS:
        if tok in scan:
            return "red", RED_TOKEN_HIT[tok], cmd
    # 1б) SQL-write в тексте команды (heredoc/stdin/инлайн) — в3: раньше такие формы прятались за
    #     ambiguous-ask, теперь ambiguous defer'ится → доктринальный sqlite проверяется ДО любого
    #     ambiguous-выхода. memory.db — своя БД (зелёная доктрина 02.07), скан-представление
    #     чтит «данные ≠ команда» (UPDATE в шаблоне grep не краснит).
    if _SQLITE_WRITE.search(scan) and ".db" in scan and "memory.db" not in scan:
        return "red", "sqlite", cmd
    # 2) разобрать команду на токены
    try:
        toks = shlex.split(cmd)
    except Exception:
        return "ambiguous", "ambiguous", cmd   # кривое квотирование → ambiguous (в3: defer)
    content = ""
    i = 0
    saw_target = False
    amb = False        # в3: ambiguous КОПИТСЯ, а не выходит сразу — красное в ЧИТАЕМОЙ части команды
    while i < len(toks):                     # важнее (иначе `python3 red.py && python3 нет_такого.py`
        t = toks[i]                          # ушёл бы в defer, не отсканировав red.py)
        if t == "-c":                                  # инлайн-код в следующем токене
            content += (toks[i + 1] if i + 1 < len(toks) else "")
            saw_target = True
            i += 2; continue
        if t == "-":                                   # stdin: тело heredoc уже отсканировано по scan (шаг 1)
            amb = True
            i += 1; continue
        if t == "-m":                                  # модуль (py_compile/pytest/json.tool — ОБРАБАТЫВАЮТ файлы-
            mod = toks[i + 1] if i + 1 < len(toks) else ""   # аргументы, НЕ исполняют их write-логику → не читаем
            if mod in _GREEN_MODULES:                  # арг-файлы: иначе `-m py_compile splinter.py` ложно ловит
                return "green", "", cmd                # токены из ИСХОДНИКА splinter.py (он их определяет)
            amb = True
            i += 2; continue
        if t.endswith(".py"):
            body = _read_file(t, cwd)
            if body is None:
                amb = True                             # путь есть, файл не прочли → ambiguous (в3: defer)
            else:
                content += body
                saw_target = True
        i += 1
    blob = cmd + "\n" + content
    for tok in RED_TOKENS:
        if tok in content:
            return "red", RED_TOKEN_HIT[tok], blob
    # memory.db через python-код = 🟢 (своя БД бота, доктрина «своя таблица через код = зелёное»,
    # переклассификация 02.07; прямой sqlite3 CLI остаётся ask в settings). SQL-write в ИНУЮ БД → ask.
    if _SQLITE_WRITE.search(blob) and ".db" in blob and "memory.db" not in blob:
        return "red", "sqlite", blob
    if amb:
        return "ambiguous", "ambiguous", blob          # доктринального красного нет → в3: defer
    if not saw_target:
        # Инфо-флаги (--version/-V/--help) НИЧЕГО не исполняют → зелёное (сужение ambiguous 06.07):
        # все не-интерпретаторные, не-`VAR=val` токены ∈ _INFO_FLAGS и хотя бы один есть.
        # Интерпретатор ищется С УЧЁТОМ env-префикса (фикс 163, 08.07) — см. _args_after_interp.
        rest = [t for t in _args_after_interp(toks) if not _ENV_ASSIGN.match(t)]
        if rest and all(t in _INFO_FLAGS for t in rest):
            return "green", "", blob
        return "ambiguous", "ambiguous", blob          # python без внятной цели (REPL/heredoc) → в3: defer
    return "green", "", blob


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        _defer()                                       # не распарсили вход → отдать штатным rules
    if (data.get("tool_name") or "") != "Bash":
        _defer()
    cmd = ((data.get("tool_input") or {}).get("command") or "")
    cwd = data.get("cwd") or PROJECT
    if not cmd:
        _defer()
    try:
        kind, hit, blob = classify(cmd, cwd)
    except Exception:
        kind, hit, blob = "ambiguous", "ambiguous", cmd  # сбой анализа → в3: defer (красное держат settings)
    if kind in ("green", "ambiguous"):
        _defer()                  # читающий python / не-python / ambiguous (в3) → штатные allow/ask/deny
    if not can_approve(kind, hit):
        why = _block_reason(hit, blob)                 # ЖЁСТКИЙ БЛОК: deny + журнал. Ниже по коду —
        _guard_log(hit, cmd, why)                      # карточка, пуш и маркер-конверт: сюда НЕ доходим,
        _emit(decision(kind, hit, why))                # т.е. approve по этой команде невозможен физически
    test = _is_test_script(cmd)                        # дальше ТОЛЬКО red: конкретная операция, каждая пушится
    card = _card(hit, blob, test, cmd=cmd)
    _guard_write_marker(os.environ.get("CC_TASK_ID", "").strip(), hit, card)
    if not test:                  # 🧪-тестовые карточки в личку НЕ пушим (утечки 01–05.07); ask остаётся
        _push(card)
    _ask(card)


if __name__ == "__main__":
    main()
