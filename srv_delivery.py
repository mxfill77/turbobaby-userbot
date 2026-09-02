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

ЧЕГО ЭТОТ МОДУЛЬ НЕ УМЕЕТ ПО УСТРОЙСТВУ (проверяется тестом `test_srv_delivery`):
  • писать на сервер — ни `push`, ни `receive-pack`, ни удалённой команды; наружу к серверу уходят
    ровно `ls-remote` и `fetch`, обе read-only;
  • перезаписывать чужую работу — в общий репозиторий уходит ТОЛЬКО перемотка вперёд, и это
    заперто ДВАЖДЫ: своей проверкой родства (`merge-base --is-ancestor`) и обычным, НЕ форсным
    push'ем, который сам GitHub отобьёт при расхождении. Слов `--force`/`+refs` в сторону `hub`
    в файле нет;
  • разруливать конфликт истории — расхождение ОСТАНАВЛИВАЕТ доставку и зовёт человека;
  • трогать рабочее дерево этого репозитория — всё живёт в голом зеркале под `tmp/`.

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
ART_DIR = os.path.join(WORK, "artifacts")

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
    """→ (исход, слова). `facts` — то, что удалось СНЯТЬ, вместе с признанием, чего снять не вышло.

    Ключи: `srv_head`, `hub_head` (None = сторона не ответила), `hub_in_srv`, `srv_in_hub`
    (None = родство не сверено). Порядок веток не косметика: неизвестность старше всего
    остального, иначе «сервер молчит» превратилось бы в «нечего забирать»."""
    srv = facts.get("srv_head")
    hub = facts.get("hub_head")

    if not srv:
        return UNKNOWN, u"сервер не ответил — что у него в main, неизвестно; ничего не трогаем"
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


def signature(kind, facts):
    """Подпись состояния для журнала «по смене». Возрастов и штампов в ней нет СОЗНАТЕЛЬНО:
    иначе запись уходила бы каждый оборот и индекс превратился бы в шум."""
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


def _head_of(run, remote):
    rc, out = run(["ls-remote", remote, "refs/heads/" + BRANCH], cwd=MIRROR)
    if rc != 0:
        return None, out
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "refs/heads/" + BRANCH:
            return parts[0], out
    return None, out


def probe(run):
    """Снять факты. Дешёвый путь первым: пока вершины равны, за объектами не ходим вовсе."""
    # `asked` — не украшение отчёта: без него сторона, которую мы не успели спросить, выглядит
    # в выводе «не ответившей». Молчание и невопрос — разные вещи, и путать их нельзя даже в печати.
    facts = {"srv_head": None, "hub_head": None, "hub_in_srv": None, "srv_in_hub": None,
             "count": None, "note": "", "asked": []}
    ok, out = ensure_mirror(run)
    if not ok:
        facts["note"] = u"зеркало не поднялось: " + out.strip()[-300:]
        return facts

    facts["asked"].append("srv")
    srv, srv_out = _head_of(run, "srv")
    facts["srv_head"] = srv
    if not srv:
        facts["note"] = srv_out.strip()[-300:]
        return facts

    facts["asked"].append("hub")
    hub, hub_out = _head_of(run, "hub")
    facts["hub_head"] = hub
    if not hub:
        facts["note"] = hub_out.strip()[-300:]
        return facts
    if srv == hub:
        return facts

    # Вершины разные — теперь нужны объекты, чтобы судить о РОДСТВЕ, а не о равенстве строк.
    # Обе выкачки read-only; в сторону сервера уходит `fetch`, и ничего кроме.
    rc, out = run(["fetch", "--quiet", "srv", "+refs/heads/%s:refs/remotes/srv/%s" % (BRANCH, BRANCH)],
                  cwd=MIRROR)
    if rc != 0:
        facts["srv_head"] = None
        facts["note"] = u"fetch с сервера не удался: " + out.strip()[-300:]
        return facts
    rc, out = run(["fetch", "--quiet", "hub", "+refs/heads/%s:refs/remotes/hub/%s" % (BRANCH, BRANCH)],
                  cwd=MIRROR)
    if rc != 0:
        facts["hub_head"] = None
        facts["note"] = u"fetch из общего репозитория не удался: " + out.strip()[-300:]
        return facts

    rc_a, _ = run(["merge-base", "--is-ancestor", hub, srv], cwd=MIRROR)
    rc_b, _ = run(["merge-base", "--is-ancestor", srv, hub], cwd=MIRROR)
    # 0 = предок, 1 = не предок, прочее = не смогли ответить (третий исход, а не «нет»).
    facts["hub_in_srv"] = True if rc_a == 0 else (False if rc_a == 1 else None)
    facts["srv_in_hub"] = True if rc_b == 0 else (False if rc_b == 1 else None)
    if facts["hub_in_srv"]:
        rc, out = run(["rev-list", "--count", "%s..%s" % (hub, srv)], cwd=MIRROR)
        if rc == 0 and out.strip().isdigit():
            facts["count"] = int(out.strip())
    return facts


def hand_over(run, facts):
    """Положить серверные коммиты в общий репозиторий. Только перемотка вперёд, только НЕ форсно.

    → (ok, слова, список хешей). Вызывается ТОЛЬКО при исходе `deliver`."""
    hub, srv = facts["hub_head"], facts["srv_head"]
    rc, out = run(["rev-list", "--reverse", "%s..%s" % (hub, srv)], cwd=MIRROR)
    shas = out.split() if rc == 0 else []
    # `<sha>:refs/heads/main` без `+` и без `--force`: даже если наша проверка родства ошибётся,
    # приёмная сторона отобьёт не-перемотку сама. Два замка на одну дверь — сознательно.
    rc, out = run(["push", "hub", "%s:refs/heads/%s" % (srv, BRANCH)], cwd=MIRROR)
    if rc != 0:
        return False, out.strip()[-400:], shas
    return True, out.strip()[-200:], shas


def extract_artifacts(run, facts, dest=None):
    """Выложить на диск ПК файлы `docs/artifacts/`, приехавшие с сервера, — чтобы серверная работа
    была ЧИТАЕМА здесь, а не только лежала объектами в зеркале. → список путей."""
    dest = dest or ART_DIR
    hub, srv = facts["hub_head"], facts["srv_head"]
    rc, out = run(["diff", "--name-only", "--diff-filter=ACMR", hub, srv, "--", "docs/artifacts"],
                  cwd=MIRROR)
    if rc != 0:
        return []
    written = []
    os.makedirs(dest, exist_ok=True)
    for path in [p.strip() for p in out.splitlines() if p.strip()]:
        rc, blob = run(["show", "%s:%s" % (srv, path)], cwd=MIRROR)
        if rc != 0:
            continue
        target = os.path.join(dest, os.path.basename(path))
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(blob)
        written.append(target)
    return written


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
    kind, why = decide(facts)
    shas, arts = [], []

    if kind == DELIVER and not dry:
        ok, out, shas = hand_over(run, facts)
        if ok:
            arts = extract_artifacts(run, facts)
            why = why + u"; легло, файлов артефактов на ПК: %d" % len(arts)
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
                          "srv_head": facts.get("srv_head"), "hub_head": facts.get("hub_head")})
        if said:
            new_state["said_at"] = now
        if kind == DELIVER:
            new_state["last_deliver_at"] = now
            new_state["last_deliver"] = shas
        if state is None:
            try:
                write_state(new_state)
            except Exception:
                pass

    return {"kind": kind, "why": why, "facts": facts, "said": said, "line": line,
            "state": new_state, "shas": shas, "artifacts": arts}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0] if argv else "--tick"
    # Замок теста: демон спавнит нас из `poll_once`, а `poll_once` зовут и тесты. Под тестовым
    # флагом наружу не ходим ни на шаг — иначе прогон гейта стучался бы в живой сервер.
    if os.environ.get("TURBOBABY_TEST_LOGS"):
        print(u"исход: off — TURBOBABY_TEST_LOGS: наружу не ходим")
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
        print(u"родство: hub_in_srv=%s srv_in_hub=%s count=%s" % (
            f.get("hub_in_srv"), f.get("srv_in_hub"), f.get("count")))
        if f.get("note"):
            print(u"замечание: %s" % f["note"])
    print(u"исход: %s — %s" % (res["kind"], res["why"]))
    if res.get("shas"):
        print(u"легли хеши: %s" % " ".join(s[:7] for s in res["shas"]))
    for a in res.get("artifacts") or []:
        print(u"артефакт на ПК: %s" % a)
    # Код возврата: 0 — сходили и ничего плохого; 1 — нужен человек (конфликт/право записи);
    # 2 — НЕИЗВЕСТНО. Молчание источника нулём не притворяется.
    return {CONFLICT: 1, BLOCKED: 1, UNKNOWN: 2}.get(res["kind"], 0)


if __name__ == "__main__":
    raise SystemExit(main())
