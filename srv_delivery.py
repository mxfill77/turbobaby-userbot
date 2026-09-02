# -*- coding: utf-8 -*-
"""srv_delivery.py — ДОСТАВКА РАБОТЫ СЕРВЕРНОЙ ПОЛОСЫ В ОБЩИЙ РЕПОЗИТОРИЙ С ПК (03.09.2026).

ЗАЧЕМ. Сервер пишет коммиты, но отдать их в общий репозиторий не может: его push отбит
аутентификацией. Замер 03.09.2026 — общий `refs/heads/main` стои́т на `e646f1f` от 30.08.2026
13:31 UTC, живое `main` сервера ушло на `f1375cb`; между ними 2 коммита, оба от 02.09.2026 20:0x
UTC, и оба несут ОДИН файл — `docs/artifacts/2026-09-03-server-contour-code-drift-recon.md`,
артефакт серверной разведки. На ПК его не было ровно поэтому: работа сделана, а доехать ей нечем.

РЕШЕНИЕ, И ПОЧЕМУ ИМЕННО ТАКОЕ. Права записи серверу не выдаём (новых ключей и токенов не
заводим — это отдельное решение владельца, а не побочный эффект доставки). Вместо этого ПК —
у которого право записи В ОБЩИЙ репозиторий уже есть — САМ ходит на сервер ЧИТАТЬ и сам кладёт
прочитанное наружу. Направление выбрано не по вкусу: чтение сервера (`git-upload-pack`) не меняет
на нём ни одного байта, а значит доставка не может испортить полосу, которую обслуживает.

ВСЕ ВЕТКИ, А НЕ ОДНА (этап 3, 03.09.2026). Первая редакция таскала ровно `main`, и это оставляло
работу в ЕДИНСТВЕННОЙ копии молча: замер 03.09 — на сервере 6 веток, в общем репозитории 2, и
**17 уникальных коммитов** не имели копии нигде, кроме диска VPS. Четыре ветки
(`backup-24-07`, `backup-main-2507`, `wip-229-230`, `wip-works-buffer-50-56-20260822`) не
существовали в общем репозитории вовсе — их не «отставание» описывало, а отсутствие. Теперь
`ls-remote --heads` снимает КАРТУ веток обеих сторон, и каждая серверная ветка судится отдельно.

ЧЕГО ЭТОТ МОДУЛЬ НЕ УМЕЕТ ПО УСТРОЙСТВУ (проверяется тестом `test_srv_delivery`):
  • писать на сервер — ни `push`, ни `receive-pack`, ни удалённой команды; наружу к серверу уходят
    ровно `ls-remote` и `fetch`, обе read-only;
  • перезаписывать чужую работу — в общий репозиторий уходит ТОЛЬКО перемотка вперёд (и создание
    ветки, которой там нет вовсе), и это заперто ДВАЖДЫ: своей проверкой родства
    (`merge-base --is-ancestor`) и обычным, НЕ форсным push'ем, который сам GitHub отобьёт при
    расхождении. Слов `--force`/`+refs` в сторону `hub` в файле нет;
  • удалять ветки — ветка, живущая в общем репозитории и отсутствующая на сервере, не предмет
    доставки ни одной строкой: её имя не попадает даже в план отправки;
  • разруливать конфликт истории — расхождение ХОТЬ НА ОДНОЙ ветке останавливает доставку
    ЦЕЛИКОМ и зовёт человека. Частичная доставка мимо конфликта была бы «остановиться и
    промолчать», а не «остановиться и доложить»;
  • трогать рабочее дерево этого репозитория — всё живёт в голом зеркале под `tmp/`.

ГДЕ ЛОЖАТСЯ ПРИЕХАВШИЕ АРТЕФАКТЫ (поправка 03.09.2026). Первая редакция клала их в
`tmp/srv_delivery/artifacts` — временный каталог доставки. Файл, которого человек не находит там,
где он ищет артефакты, доставленным НЕ ЯВЛЯЕТСЯ: замер 03.09 — серверная разведка
`2026-09-03-server-contour-code-drift-recon.md` лежала на ПК с 03.09 и не читалась из
`docs/artifacts` ни одной дорогой (489 файлов в общей папке, серверных среди них 0). Теперь
выкладка идёт в `docs/artifacts/srv/`, а `backfill_legacy()` добирает туда всё, что прошлые
обороты успели положить только во временный каталог. Ничего при этом не удаляется и не
перезаписывается.

ТРИ ИСХОДА, А НЕ ДВА. «Сервер не ответил» — это НЕИЗВЕСТНО, а не «нечего забирать» и не «ошибка».
Молчание источника доставкой не является: `unknown` не двигает состояние вперёд, ничего не пишет
наружу и говорит об этом в журнал. И наоборот — «на сервере нового нет» это НОРМА (`nothing`),
а не сбой: такой исход не поднимает тревогу и в журнал не лезет.

ПОЧЕМУ РЕГУЛЯРНОСТЬ ЖИВЁТ В ДЕМОНЕ, А НЕ В ПЛАНИРОВЩИКЕ. Своя задача Планировщика — красная
операция (`schtasks`), её без владельца не завести. Демон `pc_orchestrator` тикает сам каждые
~3 минуты, работает под тем же пользователем (значит видит и ключ `~/.ssh/turbobaby_vps`, и
кэш учётных данных git) и уже носит ровно такой вызов для слепка очереди. Цена названа вслух:
вставший демон замораживает доставку — этот модуль НЕ СУДЬЯ жизни ПК и права сказать «сервер
молчит» на основании собственной тишины не имеет. Смерть демона по-прежнему судят О2/О4.

ЧАСТОТА РЕШАЕТСЯ ЗДЕСЬ, А НЕ ЗВОНЯЩИМ. Демон зовёт `--tick` каждый оборот, а идти ли в сеть,
решает сам модуль по своему штампу (`SRV_DELIVERY_EVERY_MIN`, по умолчанию 30 минут). Так же
устроен `queue_snapshot_pc`: частота вызова на число походов наружу не влияет.

ЖУРНАЛ — ПО СМЕНЕ ИСХОДА, а не по таймеру: иначе 48 строк в сутки на пустом месте. Затянувшееся
`unknown`/`blocked` повторяется не чаще, чем раз в `SRV_DELIVERY_REPEAT_H` часов, — чтобы долгая
недоступность не стала тишиной.

ОТКАТ: `SRV_DELIVERY_OFF=1` в окружении демона — ветка мертва целиком, ни одного обращения
наружу; полностью — снять единственный вызов `_srv_delivery()` в `pc_orchestrator.poll_once`.

ЗАПУСК:
    venv\\Scripts\\python.exe srv_delivery.py --status   # что бы решил, наружу не ходим за push
    venv\\Scripts\\python.exe srv_delivery.py --dry      # сходить и посмотреть, но не доставлять
    venv\\Scripts\\python.exe srv_delivery.py --tick     # боевой оборот (с учётом своего штампа)
    venv\\Scripts\\python.exe srv_delivery.py --now      # боевой оборот, штамп частоты игнорируем
    venv\\Scripts\\python.exe srv_delivery.py --backfill # добрать артефакты в видимую папку, без сети
"""
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VENV_PY = os.path.join(REPO, "venv", "Scripts", "python.exe")

# ── адреса. Не секреты: IP сервера уже стои́т литералом в `pretool_guard._SSH_OWN_EXTRA`, а путь
# к ключу — путь, а не значение. Обе ручки переопределяются окружением, чтобы тест и вторая
# машина не правили код.
SRV_REMOTE = os.environ.get("SRV_DELIVERY_SRV", "root@5.223.94.179:/root/turbobaby-manager-bot")
HUB_REMOTE = os.environ.get("SRV_DELIVERY_HUB", "https://github.com/mxfill77/turbobaby-manager-bot.git")
BRANCH = os.environ.get("SRV_DELIVERY_BRANCH", "main")
SSH_KEY = os.environ.get("SRV_DELIVERY_KEY", os.path.join(os.path.expanduser("~"), ".ssh", "turbobaby_vps"))

# Таймауты ssh — не украшение: без них зависший сокет держал бы оборот демона, чей возраст меряют
# О2 и О4. Числа заданы владельцем в постановке и здесь не «подобраны».
SSH_OPTS = ("-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes")

WORK = os.path.join(REPO, "tmp", "srv_delivery")
MIRROR = os.path.join(WORK, "manager-bot.git")
STATE_FILE = os.path.join(WORK, "state.json")

# Куда ложатся приехавшие артефакты. ВИДИМАЯ папка, а не временная: см. шапку. Ручка окружения
# нужна тесту — иначе регресс писал бы в общую папку репозитория живыми файлами.
ART_DIR = os.environ.get("SRV_DELIVERY_ART_DIR", os.path.join(REPO, "docs", "artifacts", "srv"))
TMP_ART_DIR = os.path.join(WORK, "artifacts")   # где их держала выкладка 03.09 — оттуда добираем

EVERY_MIN = int(os.environ.get("SRV_DELIVERY_EVERY_MIN", "30"))
REPEAT_H = int(os.environ.get("SRV_DELIVERY_REPEAT_H", "6"))
GIT_TIMEOUT = int(os.environ.get("SRV_DELIVERY_TIMEOUT", "180"))

# Исходы. `blocked` отделён от `unknown` сознательно: «сервер молчит» и «наше право записи не
# сработало» чинятся РАЗНЫМИ руками, и сливать их значило бы отправить владельца не туда.
DELIVER, NOTHING, CONFLICT, UNKNOWN, BLOCKED, OFF = (
    "deliver", "nothing", "conflict", "unknown", "blocked", "off")

# Исходы, о которых молчать нельзя даже когда они не меняются.
LOUD = (UNKNOWN, CONFLICT, BLOCKED)


# ───────────────────────────── чистая часть: факты → исход ─────────────────────────────
# Ни сети, ни диска, ни времени. Инвариант держит тест SRV_DELIVERY_PURE — ровно как
# EXPECT_PC_PURE у слоя ожиданий: судья, умеющий ходить наружу, однажды соврёт молча.

def decide(facts):
    """→ (исход, слова) ПО ОДНОЙ ветке. `facts` — то, что удалось СНЯТЬ, вместе с признанием,
    чего снять не вышло.

    Ключи: `srv_head`, `hub_head` (None = сторона не ответила), `hub_in_srv`, `srv_in_hub`
    (None = родство не сверено), `hub_absent` (сторона ОТВЕТИЛА, но этой ветки у неё нет).
    Порядок веток не косметика: неизвестность старше всего остального, иначе «сервер молчит»
    превратилось бы в «нечего забирать».

    `hub_absent` отделён от `hub_head is None` СОЗНАТЕЛЬНО и стои́т между ними: «репозиторий
    промолчал» и «репозиторий ответил, а ветки в нём нет» чинятся разными руками, и слить их
    значило бы либо не завезти четыре живые ветки, либо объявить молчание поводом писать."""
    srv = facts.get("srv_head")
    hub = facts.get("hub_head")

    if not srv:
        return UNKNOWN, u"сервер не ответил — что у него в ветке, неизвестно; ничего не трогаем"
    if facts.get("hub_absent"):
        n = facts.get("count")
        return DELIVER, u"ветки нет в общем репозитории вовсе — кладём её целиком (%s коммит(ов))" % (
            n if n is not None else u"?")
    if not hub:
        return UNKNOWN, u"общий репозиторий не ответил — сверять не с чем; ничего не трогаем"
    if srv == hub:
        return NOTHING, u"забирать нечего: main сервера и общий main стоя́т на одном коммите %s" % srv[:7]

    hub_in_srv = facts.get("hub_in_srv")
    srv_in_hub = facts.get("srv_in_hub")
    if hub_in_srv is None or srv_in_hub is None:
        return UNKNOWN, u"родство историй не сверено (объекты не выкачались) — доставку не начинаем"

    if srv_in_hub:
        # Общий репозиторий УЖЕ содержит серверную вершину и ушёл дальше — это не сбой и не
        # предмет доставки: сервер просто отстал, ЕГО нового здесь нет.
        return NOTHING, u"забирать нечего: вершина сервера %s уже в общем main, он ушёл дальше" % srv[:7]
    if hub_in_srv:
        n = facts.get("count")
        return DELIVER, u"перемотка вперёд: %s коммит(ов) сервера кладутся поверх %s" % (
            n if n is not None else u"?", hub[:7])
    return CONFLICT, (u"истории разошлись: %s не предок %s и наоборот — доставка ОСТАНОВЛЕНА, "
                      u"нужен человек" % (hub[:7], srv[:7]))


def decide_all(facts):
    """→ (исход, слова) ПО ВСЕМ веткам сразу. Старшинство исходов не косметика.

    Конфликт и неизвестность ОСТАНАВЛИВАЮТ доставку целиком, а не пропускают её мимо себя по
    соседней ветке: «доставили пять из шести, а про шестую промолчали» — ровно тот исход, ради
    запрета которого в задании стои́т слово «остановиться». Ветки, которая есть в общем
    репозитории и которой нет на сервере, здесь нет ни в одном разрезе: доставка её не судит и
    не трогает."""
    branches = facts.get("branches")
    if not branches:
        return decide(facts)        # одиночная форма — ровно как была до этапа 3

    per = {}
    for name in sorted(branches):
        per[name] = decide(branches[name])[0]

    torn = sorted([n for n in per if per[n] == CONFLICT])
    if torn:
        return CONFLICT, (u"истории разошлись на ветке(ах) %s — доставка ОСТАНОВЛЕНА ЦЕЛИКОМ, "
                          u"нужен человек" % u", ".join(torn))
    dark = sorted([n for n in per if per[n] == UNKNOWN])
    if dark:
        return UNKNOWN, u"по ветке(ам) %s факты не сняты — доставку не начинаем" % u", ".join(dark)

    give = sorted([n for n in per if per[n] == DELIVER])
    if not give:
        return NOTHING, u"забирать нечего: все %d веток сервера уже в общем репозитории" % len(per)

    total, fresh = 0, []
    for n in give:
        c = branches[n].get("count")
        if c:
            total += c
        if branches[n].get("hub_absent"):
            fresh.append(n)
    tail = u"" if not fresh else u"; новых веток %d (%s)" % (len(fresh), u", ".join(fresh))
    # «Суммой по веткам, с перекрытием» — не оговорка, а признание метода: один коммит, лежащий
    # в двух ветках, посчитан дважды. Назвать это число уникальным значило бы обещать владельцу
    # больше работы, чем едет (живой замер 03.09: сумма 26, уникальных 17).
    return DELIVER, u"вперёд по %d ветке(ам): %s; коммитов суммой по веткам (с перекрытием) %d%s" % (
        len(give), u", ".join(give), total, tail)


def plan_pushes(facts):
    """→ [(ветка, серверный хеш, хеш общего репозитория или None)], по именам.

    Чистая СОЗНАТЕЛЬНО: список того, что БУДЕТ послано наружу, обязан быть предсказуем без сети —
    иначе «мы не трогаем чужие ветки» проверялось бы рассказом, а не разбором плана."""
    branches = facts.get("branches")
    if not branches:
        return [(BRANCH, facts.get("srv_head"), facts.get("hub_head"))]
    out = []
    for name in sorted(branches):
        b = branches[name]
        if decide(b)[0] == DELIVER:
            out.append((name, b.get("srv_head"), b.get("hub_head")))
    return out


def signature(kind, facts):
    """Подпись состояния для журнала «по смене». Возрастов и штампов в ней нет СОЗНАТЕЛЬНО:
    иначе запись уходила бы каждый оборот и индекс превратился бы в шум.

    С этапа 3 подпись держит ВСЕ ветки: без этого движение любой ветки, кроме `main`, было бы
    доставлено молча — тот же класс «сделано и не видно», из-за которого завели этот модуль."""
    branches = facts.get("branches")
    if branches:
        parts = []
        for name in sorted(branches):
            b = branches[name]
            parts.append(u"%s=%s/%s" % (name, (b.get("srv_head") or "-")[:8],
                                        (b.get("hub_head") or "-")[:8]))
        return u"%s|%s" % (kind, u",".join(parts))
    return u"%s|%s|%s" % (kind, (facts.get("srv_head") or "-")[:12],
                          (facts.get("hub_head") or "-")[:12])


def should_say(prev, sig, kind, now):
    """→ bool. Говорить ли в журнал. `prev` — прошлое состояние (dict или None).

    Правила, каждое покрыто тестом:
      • подпись сменилась и исход не «нечего» → говорим (это событие);
      • «нечего» молчит ВСЕГДА, кроме выздоровления после громкого исхода: отсутствие новостей
        новостью не является, а вот «сервер снова отвечает» — является;
      • громкий исход, который не меняется, повторяется не чаще REPEAT_H часов: затянувшаяся
        недоступность обязана оставаться видимой, но не обязана быть шумом."""
    prev = prev or {}
    prev_sig = prev.get("sig")
    prev_kind = prev.get("kind")
    said_at = float(prev.get("said_at") or 0)

    if kind == OFF:
        return False
    if kind == NOTHING:
        return prev_kind in LOUD
    if sig != prev_sig:
        return True
    if kind in LOUD and (now - said_at) >= REPEAT_H * 3600:
        return True
    return False


def due(prev, now, every_min=None):
    """→ bool. Пора ли идти наружу. Частоту решает модуль, а не звонящий (см. шапку)."""
    every = (EVERY_MIN if every_min is None else every_min) * 60
    if every <= 0:
        return True
    last = float((prev or {}).get("probed_at") or 0)
    if not last:
        return True         # штампа нет вовсе — наружу ещё не ходили ни разу, ждать нечего
    # Часы могли уехать назад (перевод времени, сон) — отрицательная разница не имеет права
    # запереть доставку навсегда.
    return (now - last) >= every or (now - last) < 0


# ───────────────────────────── руки: git, состояние, журнал ─────────────────────────────

def _ssh_command():
    return " ".join(["ssh", "-i", '"%s"' % SSH_KEY] + list(SSH_OPTS))


def git_runner(argv, cwd=None, timeout=None):
    """Единственная дверь наружу. Все походы к серверу и в общий репозиторий идут через неё —
    поэтому тест может подменить её целиком и увидеть КАЖДУЮ команду, которую модуль послал бы."""
    env = dict(os.environ)
    env["GIT_SSH_COMMAND"] = _ssh_command()
    env["GIT_TERMINAL_PROMPT"] = "0"      # без учётных данных падаем, а не виснем на приглашении
    try:
        p = subprocess.run(["git"] + list(argv), cwd=cwd or WORK, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout or GIT_TIMEOUT, creationflags=NO_WINDOW)
        out = (p.stdout or b"").decode("utf-8", "replace")
        return p.returncode, out
    except Exception as e:
        return 255, u"git не запустился: %s" % e


def read_state(path=None):
    try:
        with open(path or STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state, path=None):
    path = path or STATE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def journal(line):
    """Строка в `cowork_log` — отдельным процессом, fire-and-forget (копия `_cowork` демона).

    Под тестовым флагом молчит: тесты не имеют права писать в боевой журнал мозга."""
    if os.environ.get("TURBOBABY_TEST_LOGS"):
        return False
    try:
        subprocess.Popen([VENV_PY if os.path.exists(VENV_PY) else sys.executable,
                          os.path.join(REPO, "cowork_log_append.py"), line],
                         cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return True
    except Exception:
        return False


def ensure_mirror(run):
    """Голое зеркало под `tmp/` — рабочего дерева у него нет вовсе, значит доставка физически не
    может задеть ни один файл этого репозитория. Идемпотентно."""
    os.makedirs(WORK, exist_ok=True)
    if not os.path.isdir(os.path.join(MIRROR, "objects")):
        rc, out = run(["init", "--bare", "--initial-branch=main", MIRROR], cwd=WORK)
        if rc != 0:
            return False, out
    for name, url in (("srv", SRV_REMOTE), ("hub", HUB_REMOTE)):
        rc, _ = run(["remote", "set-url", name, url], cwd=MIRROR)
        if rc != 0:
            rc, out = run(["remote", "add", name, url], cwd=MIRROR)
            if rc != 0:
                return False, out
    return True, ""


def _heads_of(run, remote):
    """→ (карта {ветка: хеш} или None, вывод). None — сторона НЕ ОТВЕТИЛА; пустая карта — ответила,
    но веток у неё нет. Разные вещи: первое неизвестность, второе факт."""
    rc, out = run(["ls-remote", "--heads", remote], cwd=MIRROR)
    if rc != 0:
        return None, out
    heads = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("refs/heads/"):
            heads[parts[1][len("refs/heads/"):]] = parts[0]
    return heads, out


def _lead_head(heads):
    """Вершина «главной» ветки — ею подписан верхний уровень фактов, на котором стоя́т одиночная
    форма `decide` и старые состояния. Нет `main` — берём первую по имени: у верхнего уровня не
    должно быть права соврать «сервер не ответил» только потому, что ветка названа иначе."""
    if BRANCH in heads:
        return heads[BRANCH]
    names = sorted(heads)
    if names:
        return heads[names[0]]
    return None


def probe(run):
    """Снять факты по ВСЕМ веткам сервера. Дешёвый путь первым: пока каждая серверная вершина уже
    стои́т в общем репозитории, за объектами не ходим вовсе."""
    # `asked` — не украшение отчёта: без него сторона, которую мы не успели спросить, выглядит
    # в выводе «не ответившей». Молчание и невопрос — разные вещи, и путать их нельзя даже в печати.
    facts = {"srv_head": None, "hub_head": None, "hub_in_srv": None, "srv_in_hub": None,
             "count": None, "note": "", "asked": [], "branches": {},
             "srv_heads": {}, "hub_heads": {}}
    ok, out = ensure_mirror(run)
    if not ok:
        facts["note"] = u"зеркало не поднялось: " + out.strip()[-300:]
        return facts

    facts["asked"].append("srv")
    srv_heads, srv_out = _heads_of(run, "srv")
    if srv_heads is None:
        facts["note"] = srv_out.strip()[-300:]
        return facts
    if not srv_heads:
        facts["note"] = u"сервер ответил, но веток у него нет — сверять нечего"
        return facts
    facts["srv_heads"] = srv_heads
    facts["srv_head"] = _lead_head(srv_heads)

    facts["asked"].append("hub")
    hub_heads, hub_out = _heads_of(run, "hub")
    if hub_heads is None:
        facts["note"] = hub_out.strip()[-300:]
        return facts
    facts["hub_heads"] = hub_heads
    facts["hub_head"] = _lead_head(hub_heads)

    # Каждая серверная ветка уже стои́т в общем репозитории той же вершиной — за объектами не идём.
    # Лишние ветки НА СТОРОНЕ ХАБА равенству не мешают: они не наш предмет.
    behind = [n for n in srv_heads if hub_heads.get(n) != srv_heads[n]]
    if not behind:
        return facts

    # Вершины разошлись — теперь нужны объекты, чтобы судить о РОДСТВЕ, а не о равенстве строк.
    # Обе выкачки read-only; в сторону сервера уходит `fetch`, и ничего кроме.
    rc, out = run(["fetch", "--quiet", "srv", "+refs/heads/*:refs/remotes/srv/*"], cwd=MIRROR)
    if rc != 0:
        facts["srv_head"] = None
        facts["note"] = u"fetch с сервера не удался: " + out.strip()[-300:]
        return facts
    rc, out = run(["fetch", "--quiet", "hub", "+refs/heads/*:refs/remotes/hub/*"], cwd=MIRROR)
    if rc != 0:
        facts["hub_head"] = None
        facts["note"] = u"fetch из общего репозитория не удался: " + out.strip()[-300:]
        return facts

    branches = {}
    for name in sorted(srv_heads):
        srv_sha = srv_heads[name]
        hub_sha = hub_heads.get(name)
        b = {"srv_head": srv_sha, "hub_head": hub_sha, "hub_in_srv": None, "srv_in_hub": None,
             "count": None, "hub_absent": hub_sha is None}
        if hub_sha is None:
            # Ветки в общем репозитории нет вовсе. «Сколько нового» считаем ОТ ВСЕГО, что там уже
            # лежит: у ветки-новичка нет своей точки отсчёта, а объекты могут быть общими с main.
            rc, out = run(["rev-list", "--count", srv_sha, "--not", "--remotes=hub"], cwd=MIRROR)
            if rc == 0 and out.strip().isdigit():
                b["count"] = int(out.strip())
        elif hub_sha != srv_sha:
            rc_a, _ = run(["merge-base", "--is-ancestor", hub_sha, srv_sha], cwd=MIRROR)
            rc_b, _ = run(["merge-base", "--is-ancestor", srv_sha, hub_sha], cwd=MIRROR)
            # 0 = предок, 1 = не предок, прочее = не смогли ответить (третий исход, а не «нет»).
            b["hub_in_srv"] = True if rc_a == 0 else (False if rc_a == 1 else None)
            b["srv_in_hub"] = True if rc_b == 0 else (False if rc_b == 1 else None)
            if b["hub_in_srv"]:
                rc, out = run(["rev-list", "--count", "%s..%s" % (hub_sha, srv_sha)], cwd=MIRROR)
                if rc == 0 and out.strip().isdigit():
                    b["count"] = int(out.strip())
        branches[name] = b
    facts["branches"] = branches

    # Совместимость: верхний уровень описывает главную ветку — на нём стоя́т подпись прежних
    # состояний и одиночная форма решения.
    lead = branches.get(BRANCH)
    if lead:
        facts["hub_in_srv"] = lead["hub_in_srv"]
        facts["srv_in_hub"] = lead["srv_in_hub"]
        facts["count"] = lead["count"]
    return facts


def hand_over(run, facts):
    """Положить серверные коммиты в общий репозиторий. Только перемотка вперёд и только создание
    веток, которых там нет; только НЕ форсно.

    → (ok, слова, список хешей, список веток). Вызывается ТОЛЬКО при исходе `deliver`.
    Отправляем ПО ВЕТКЕ ЗА РАЗ, а не одной командой с пачкой refspec'ов, СОЗНАТЕЛЬНО: одна
    отбитая ветка не имеет права утащить в отказ остальные, а отчёт обязан назвать, какая именно."""
    shas, done, fail = [], [], []
    for name, srv_sha, hub_sha in plan_pushes(facts):
        if hub_sha:
            rc, out = run(["rev-list", "--reverse", "%s..%s" % (hub_sha, srv_sha)], cwd=MIRROR)
        else:
            rc, out = run(["rev-list", "--reverse", srv_sha, "--not", "--remotes=hub"], cwd=MIRROR)
        mine = out.split() if rc == 0 else []
        # `<sha>:refs/heads/<ветка>` без `+` и без `--force`: даже если наша проверка родства
        # ошибётся, приёмная сторона отобьёт не-перемотку сама. Два замка на одну дверь.
        rc, out = run(["push", "hub", "%s:refs/heads/%s" % (srv_sha, name)], cwd=MIRROR)
        if rc != 0:
            fail.append(u"%s: %s" % (name, out.strip()[-200:]))
            continue
        done.append(name)
        for sha in mine:
            if sha not in shas:
                shas.append(sha)
    if fail:
        return False, u"; ".join(fail), shas, done
    return True, u"ветки: %s" % u", ".join(done), shas, done


def extract_artifacts(run, facts, dest=None):
    """Выложить на диск ПК файлы `docs/artifacts/`, приехавшие с сервера, — чтобы серверная работа
    была ЧИТАЕМА человеком, а не только лежала объектами в зеркале. → список путей.

    Новых веток это не касается СОЗНАТЕЛЬНО: у ветки, которой в общем репозитории нет вовсе, нет
    и точки отсчёта — «что в ней нового» неопределено, а вываливать её дерево целиком значит
    засыпать общую папку июльскими копиями. Сохранность их содержимого держит push, а не выкладка."""
    dest = dest or ART_DIR
    written = []
    for name, srv_sha, hub_sha in plan_pushes(facts):
        if not hub_sha:
            continue
        rc, out = run(["diff", "--name-only", "--diff-filter=ACMR", hub_sha, srv_sha,
                       "--", "docs/artifacts"], cwd=MIRROR)
        if rc != 0:
            continue
        for path in [p.strip() for p in out.splitlines() if p.strip()]:
            rc, blob = run(["show", "%s:%s" % (srv_sha, path)], cwd=MIRROR)
            if rc != 0:
                continue
            os.makedirs(dest, exist_ok=True)
            target = os.path.join(dest, os.path.basename(path))
            with open(target, "w", encoding="utf-8", newline="") as f:
                f.write(blob)
            written.append(target)
    return written


def backfill_legacy(src_dir=None, dest=None):
    """Добор: файлы, которые ПРОШЛЫЕ обороты положили только во временный каталог доставки,
    копируются в видимую папку. → список путей.

    Копия, а не перенос, и только то, чего в видимой папке ещё нет: удалять и перезаписывать
    доставке нечем и незачем. Идемпотентно, поэтому живёт в обороте, а не в разовом скрипте, —
    разовый скрипт починил бы сегодняшний случай и промолчал бы о завтрашнем."""
    src_dir = src_dir if src_dir else TMP_ART_DIR
    dest = dest if dest else ART_DIR
    filled = []
    if not os.path.isdir(src_dir):
        return filled
    for name in sorted(os.listdir(src_dir)):
        src = os.path.join(src_dir, name)
        dst = os.path.join(dest, name)
        if not os.path.isfile(src) or os.path.exists(dst):
            continue
        with open(src, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
        os.makedirs(dest, exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        filled.append(dst)
    return filled


# ───────────────────────────── оборот ─────────────────────────────

def tick(run=None, now=None, say=None, state=None, force=False, dry=False):
    """Один оборот доставки. → dict с исходом и словами; ничего не печатает.

    `run`/`say`/`now`/`state` инъектируются — на этом стоя́т отрицательные тесты: «сервер не
    отвечает» проверяется НЕ отключением сети, а подменённой дверью, которая записывает КАЖДУЮ
    команду, — и тест видит, что `push` среди них не было."""
    run = run or git_runner
    now = time.time() if now is None else now
    prev = read_state() if state is None else state

    if os.environ.get("SRV_DELIVERY_OFF"):
        return {"kind": OFF, "why": u"выключено флагом SRV_DELIVERY_OFF", "facts": {},
                "said": False, "state": prev, "shas": []}

    if not force and not due(prev, now):
        return {"kind": "skip", "why": u"рано: своего срока (%s мин) ещё нет" % EVERY_MIN,
                "facts": {}, "said": False, "state": prev, "shas": []}

    facts = probe(run)
    kind, why = decide_all(facts)
    shas, arts, moved = [], [], []

    if kind == DELIVER and not dry:
        ok, out, shas, moved = hand_over(run, facts)
        if ok:
            arts = extract_artifacts(run, facts)
            why = why + u"; легло (%s), файлов артефактов на ПК: %d" % (out, len(arts))
        else:
            kind = BLOCKED
            why = u"коммиты сервера есть, а положить их в общий репозиторий не вышло: " + out
            shas = []

    sig = signature(kind, facts)
    said = False
    line = u"%s Доставка серверной полосы: %s" % ("DONE" if kind == DELIVER else "NOTE", why)
    if not dry and should_say(prev, sig, kind, now):
        said = bool((say or journal)(line))

    new_state = dict(prev)
    if not dry:
        new_state.update({"probed_at": now, "kind": kind, "sig": sig,
                          "srv_head": facts.get("srv_head"), "hub_head": facts.get("hub_head"),
                          "srv_heads": facts.get("srv_heads"),
                          "hub_heads": facts.get("hub_heads")})
        if said:
            new_state["said_at"] = now
        if kind == DELIVER:
            new_state["last_deliver_at"] = now
            new_state["last_deliver"] = shas
            new_state["last_branches"] = moved
        if state is None:
            try:
                write_state(new_state)
            except Exception:
                pass

    return {"kind": kind, "why": why, "facts": facts, "said": said, "line": line,
            "state": new_state, "shas": shas, "artifacts": arts, "branches": moved}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "--tick"
    # Замок теста: демон спавнит нас из `poll_once`, а `poll_once` зовут и тесты. Под тестовым
    # флагом наружу не ходим ни на шаг — иначе прогон гейта стучался бы в живой сервер.
    if os.environ.get("TURBOBABY_TEST_LOGS"):
        print(u"исход: off — TURBOBABY_TEST_LOGS: наружу не ходим")
        return 0
    if mode == "--backfill":
        # Отдельный вход: добрать в видимую папку то, что прошлые обороты положили только во
        # временную. Наружу не ходит ни одной командой.
        for path in backfill_legacy():
            print(u"добрано в общую папку: %s" % path)
        return 0

    res = tick(force=mode in ("--now", "--dry", "--status"),
               dry=mode in ("--dry", "--status"))
    if mode in ("--status", "--dry"):
        f = res["facts"]
        asked = f.get("asked") or []

        def _side(key, who):
            return f.get(key) or (u"НЕ ОТВЕТИЛ" if who in asked else u"НЕ СПРАШИВАЛИ")
        print(u"сервер:  %s" % _side("srv_head", "srv"))
        print(u"общий:   %s" % _side("hub_head", "hub"))
        branches = f.get("branches")
        if branches:
            for name in sorted(branches):
                b = branches[name]
                print(u"  ветка %-34s srv=%s hub=%s → %s" % (
                    name, (b.get("srv_head") or "-")[:7],
                    (b.get("hub_head") or u"НЕТ")[:7], decide(b)[0]))
        else:
            print(u"родство: hub_in_srv=%s srv_in_hub=%s count=%s" % (
                f.get("hub_in_srv"), f.get("srv_in_hub"), f.get("count")))
        if f.get("note"):
            print(u"замечание: %s" % f["note"])
    print(u"исход: %s — %s" % (res["kind"], res["why"]))
    if res.get("shas"):
        print(u"легли хеши: %s" % " ".join(s[:7] for s in res["shas"]))
    if not (mode in ("--status", "--dry")):
        for path in backfill_legacy():
            print(u"добрано в общую папку: %s" % path)
    for a in res.get("artifacts") or []:
        print(u"артефакт на ПК: %s" % a)
    # Код возврата: 0 — сходили и ничего плохого; 1 — нужен человек (конфликт/право записи);
    # 2 — НЕИЗВЕСТНО. Молчание источника нулём не притворяется.
    return {CONFLICT: 1, BLOCKED: 1, UNKNOWN: 2}.get(res["kind"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
