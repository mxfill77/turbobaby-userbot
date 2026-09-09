"""Руки ступени B ревью-контура: лоток ответов → находки → ЗАЯВКИ в очереди.

Разделение то же, что у ступеней 1–2, A и слоя ожиданий: всё, что РЕШАЕТ, живёт
чистым :mod:`review_intake`; здесь только ввод-вывод — диск, git, очередь, журнал.

    venv/Scripts/python.exe review_intake_run.py --status          # что видит контур; ничего не пишем
    venv/Scripts/python.exe review_intake_run.py --dry             # заявки построены, в очередь НЕ ставим
    venv/Scripts/python.exe review_intake_run.py --place --pack digest --limit 3   # боевая постановка
    venv/Scripts/python.exe review_intake_run.py --tick            # оборот демона (бутстрап + постановка)

ГДЕ ЭТОТ КОД ЖИВЁТ В БОЮ. Демон зовёт :func:`tick` из витка — тем же
устройством, каким зовёт ревизора и ступень A: флаг окружения + метка на диске +
один оборот на тик. Цена оборота названа числом: заход не ходит в внешние каналы
вовсе (ответ уже лежит файлом), самая дорогая его часть — ОДИН проход по
отслеживаемому дереву ради проверки премис (687 файлов, 7.9 МБ на замере 01.09).

БУТСТРАП — ПЕРВЫЙ ОБОРОТ BACKLOG НЕ РАЗГРЕБАЕТ. Тот же приём, что у
``maybe_revizor``, и по той же причине: на момент рождения контура в лотке уже
лежат ответы прошлых суток, и разложить их пачкой заявок значило бы завалить
владельца карточками за работу, которую он давно закрыл. Ключи корпуса при
бутстрапе записываются в реестр СЛОВОМ «не ставили» — то есть остаток назван, а
не потерян.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ НИ ОДНОЙ ВЕТКОЙ: не исполняет находок, не ставит
задач (заявка — ряд, ждущий человека, и три гарда демона держат её от исполнения),
не правит кода, не удаляет файлов, не трогает клиентского контура и не ходит в
внешнюю сеть. Единственная мутация наружу — постановка ряда очереди и карточка
владельцу.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import subprocess
import sys

import review_intake
import zayavki_lotok_pc
import zayavki_lotok_run

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_STATE = "review_intake_state.json"
DEFAULT_INBOX = "docs/review_inbox"
JOURNAL_WRITER = "cowork_log_append.py"

# Потолок суток. Три — не круглое число: столько находок несёт ОДИН ответ канала
# (хвост пакета задаёт ровно три вопроса), то есть за сутки контур доносит
# владельцу не больше одного полного мнения. Больше — это уже не «второе мнение»,
# а лента, которую перестают читать.
DEFAULT_BUDGET = 3

# Корпус проверки премис — отслеживаемое git дерево, текстовые расширения.
TREE_EXTS = (".py", ".md", ".json", ".jsonl", ".txt", ".gs")
TREE_MAX_BYTES = 2_000_000
GIT_TIMEOUT = 30

# СОБСТВЕННЫЕ ФАЙЛЫ КОНТУРА — вне корпуса проверки. Не гигиена, а замок от
# самоответа: голдены регресса цитируют находки ДОСЛОВНО (правило-класс полосы:
# тест обязан стоять на реальной фразе), и найдись якорь только в них — «премиса
# жива» означало бы «мы сами её и переписали к себе в фикстуру».
SELF_FILES = ("review_intake.py", "review_intake_run.py", "test_review_intake.py")

CLAIM_FROM = "Filipp-review-claim"     # метка ряда-заявки (НЕ дирижёрская цепь, НЕ ревизор)


def now_iso(clock=None):
    """Текущее время UTC в ISO. Единственная точка, где контур смотрит на часы."""
    stamp = (clock or datetime.datetime.now)(datetime.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _path(root, rel):
    return os.path.join(root, *rel.split("/"))


# ───────────────────────────── реестр заявок ─────────────────────────────


def state_default():
    return {"schema": review_intake.SCHEMA, "bootstrap_at": None, "claims": {}, "held": {}}


def read_state(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return state_default()
    out = state_default()
    if isinstance(data, dict):
        out["bootstrap_at"] = data.get("bootstrap_at")
        for key in ("claims", "held"):
            if isinstance(data.get(key), dict):
                out[key] = dict(data[key])
    return out


def known_keys(state):
    """Все ключи, о которых реестр уже знает (поставленные + отложенные). → set.

    Читаем и главный ключ записи, и её набор ``keys``: у заявки их несколько (см.
    :func:`review_intake.claim_keys`), и узнавать её по одному значило бы
    промахиваться каждый раз, когда состав источников поменялся.
    """
    out = set()
    for bucket in ("claims", "held"):
        for key, rec in (state.get(bucket) or {}).items():
            out.add(key)
            if isinstance(rec, dict):
                out.update(rec.get("keys") or [])
    return out


def write_state(path, state):
    """Реестр на диск атомарно (tmp + replace): оборванная запись не смеет
    оставить контур без памяти о том, что уже поставлено."""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


# ───────────────────────────── лоток ответов ─────────────────────────────


def answer_files(root=HERE, inbox=DEFAULT_INBOX):
    """Файлы лотка ответов. → list[rel] (детерминированный порядок)."""
    folder = _path(root, inbox)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return ["%s/%s" % (inbox, n) for n in names if n.endswith(".md")]


def read_records(root=HERE, inbox=DEFAULT_INBOX, files=None):
    """Лоток → записи источников. → (records, skipped[(rel, причина)]).

    Пропуск НАЗЫВАЕТСЯ причиной, а не молчит: в лотке лежат и файлы, ответами не
    являющиеся (заметки прошлых ступеней), и отказы каналов — и это РАЗНЫЕ
    новости. Молчаливый пропуск обоих сделал бы «находок нет» неотличимым от
    «разбор сломался».
    """
    out, skipped = [], []
    for rel in (files if files is not None else answer_files(root, inbox)):
        try:
            with io.open(_path(root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            skipped.append((rel, "файл не прочитан: %s" % exc))
            continue
        try:
            header = review_intake.parse_answer(text)
        except review_intake.ReviewIntakeError as exc:
            skipped.append((rel, "не файл ответа (%s)" % exc.reason))
            continue
        if not header.get("ok"):
            skipped.append((rel, header.get("why") or "находок нет"))
            continue
        for finding in review_intake.split_findings(header["body"]):
            out.append(review_intake.record(header, finding, answer_rel=rel))
    return out, skipped


# ───────────────────────────── проверка премисы ─────────────────────────────


def _git(args, root, runner=None):
    run = runner or subprocess.run
    try:
        done = run(["git", "-C", root] + list(args), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=GIT_TIMEOUT)
    except Exception as exc:                       # noqa: BLE001 — «git не ответил» ≠ «пусто»
        return None, str(exc)
    if done.returncode != 0:
        return None, (done.stderr or b"").decode("utf-8", "replace").strip()[:200]
    return done.stdout.decode("utf-8", "replace"), ""


def tracked_files(root=HERE, runner=None):
    """Отслеживаемые текстовые файлы дерева. → (list[rel], причина отказа).

    Корпус — именно git-дерево, а не обход каталога: в рабочем дереве этой полосы
    лежат снимок чужого репозитория и десятки временных каталогов (мины, описанные
    в CLAUDE.md), и находка, «нашедшаяся» там, доказывала бы жизнь ЧУЖОГО кода.
    `-z` берём сознательно: имена файлов здесь бывают кириллицей, и обычный вывод
    git их экранирует.
    """
    out, why = _git(["ls-files", "-z"], root, runner)
    if out is None:
        return [], why or "git ls-files не ответил"
    files = []
    for rel in out.split("\0"):
        rel = rel.strip()
        if not rel or not rel.endswith(TREE_EXTS):
            continue
        if os.path.basename(rel) in SELF_FILES:
            continue                               # замок от самоответа (см. SELF_FILES)
        files.append(rel)
    return files, ""


def probe_anchors(plan, root=HERE, files=None, why=None, reader=None):
    """План проб → пробы по НАЗВАННЫМ адресам. → list[dict].

    Один проход по корпусу на все якоря сразу: файл читается один раз, и в нём
    ищутся все ещё не найденные литералы. Живое совпадение (файл вне замороженных
    каталогов) ПЕРЕБИВАЕТ замороженное — иначе премиса, которую подтверждает
    собственный пакет из лотка, вечно числилась бы живой.

    Отказ корпуса (git не ответил) отдаёт found=None КАЖДОМУ якорю: «не смог
    проверить» — это третий исход, а не «не нашлось».
    """
    plan = list(plan or [])
    if not plan:
        return []
    if files is None:
        files, why = tracked_files(root)
    if not files:
        return [{"anchor": p["anchor"], "form": p["form"], "found": None, "frozen": False,
                 "address": "", "detail": why or "корпус дерева пуст"} for p in plan]
    read = reader or _read_text
    probes = {}
    literals = []
    for item in plan:
        anchor, form = item["anchor"], item["form"]
        if form == "path":
            rel = anchor.replace("\\", "/").lstrip("./")
            hit = rel in set(files) or os.path.isfile(_path(root, rel))
            probes[anchor] = {"anchor": anchor, "form": form, "found": bool(hit),
                              "frozen": review_intake.frozen_address(rel),
                              "address": rel if hit else "",
                              "detail": "" if hit else "файла нет в дереве"}
        else:
            probes[anchor] = {"anchor": anchor, "form": form, "found": False, "frozen": False,
                              "address": "", "detail": "в дереве не найдено"}
            literals.append(anchor)
    if literals:
        # Живые файлы первыми: как только якорь найден в живом, замороженные для
        # него уже не важны.
        order = sorted(files, key=lambda rel: (review_intake.frozen_address(rel), rel))
        pending = set(literals)
        for rel in order:
            if not pending:
                break
            text = read(_path(root, rel))
            if not text:
                continue
            frozen = review_intake.frozen_address(rel)
            for anchor in sorted(pending):
                pos = text.find(anchor)
                if pos < 0:
                    continue
                probe = probes[anchor]
                if probe["found"] and probe["frozen"] is False:
                    continue
                line = text.count("\n", 0, pos) + 1
                probe.update(found=True, frozen=frozen, address="%s:%d" % (rel, line), detail="")
                if not frozen:
                    pending.discard(anchor)
    return [probes[item["anchor"]] for item in plan]


def _read_text(path):
    try:
        if os.path.getsize(path) > TREE_MAX_BYTES:
            return ""
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# ───────────────────────────── сборка заявок ─────────────────────────────


def build(root=HERE, inbox=DEFAULT_INBOX, files=None, tree=None, tree_why=""):
    """Лоток → заявки с исходом премисы. → dict отчёта сборки.

    Ничего не пишет и никуда не ходит, кроме чтения дерева: этой же функцией
    живут ``--status`` и ``--dry``.
    """
    records, skipped = read_records(root, inbox, files)
    claims = review_intake.merge(records)
    if tree is None:
        tree, tree_why = tracked_files(root)
    out = []
    for claim in claims:
        plan = review_intake.probe_plan(claim)
        probes = probe_anchors(plan, root=root, files=tree, why=tree_why)
        out.append({"claim": claim, "premise": review_intake.premise(probes), "probes": probes})
    return {"records": records, "claims": out, "skipped": skipped,
            "tree_files": len(tree or []), "tree_why": tree_why}


# ───────────────────────────── очередь ─────────────────────────────


def _daemon():
    """Демон, одолженный ради БОЕВОГО клиента моста.

    Секреты берёт САМ импортируемый модуль — здесь их не читают и не видят
    (запрет класса 328). Импорт идёт под замком флага из :mod:`queue_snapshot_pc`:
    тот же замок, тот же разбор (флаг нужен, чтобы не повесить хендлер на боевой
    лог демона, и обязан быть снят сразу — иначе `brain_writer` откажет в живой
    записи). Зовём его функцию, а не переписываем её здесь: урок должен жить в
    одном месте.
    """
    import queue_snapshot_pc

    return queue_snapshot_pc._guard_test_logs(queue_snapshot_pc._daemon)


class Queue:
    """Тонкая обёртка очереди: постановка заявки ТЕМ ЖЕ механизмом, что у ревизора.

    Ревизор ставит и задачу-находку, и свою owner-карточку одним и тем же
    ``enqueue_pc_task`` — разница только в метке ``from`` и в том, что карточка
    сразу переводится в ``needs_approval``. Заявка идёт ВТОРЫМ путём: ряд встаёт
    и ТУТ ЖЕ становится ожиданием решения человека, минуя ``new`` (в ``new`` его
    иначе подобрал бы ``process_new`` — и заявка стала бы задачей ровно тем, чего
    ей запрещено).
    """

    def __init__(self, daemon=None, root=HERE):
        self._d = daemon or _daemon()
        # Корень нужен ЛОТКУ, и он поле, а не константа: под тестом лоток обязан
        # уехать во временный каталог вместе со всем прочим состоянием ступени.
        self._root = root

    def markers(self):
        """Ключи уже стоящих заявок из ЖИВОЙ очереди. → (list[(дата,ключ)], ok, why).

        Читаем ``needs_approval`` и ``new``: первое — штатное место заявки, второе
        — где она окажется при обрыве между постановкой и переводом. Мост не
        ответил → ok=False, и зовущий НЕ ставит ничего: дедуп не сверить, а слепая
        постановка дублей дороже отложенной.
        """
        out = []
        for status in ("needs_approval", "new"):
            res = self._d.bc.get_pending(status)
            if not res.get("ok"):
                return [], False, str(res.get("error") or "мост не ответил")
            rows = [it for it in (res.get("items") or []) if str(it.get("lane") or "pc") == "pc"]
            out.extend(review_intake.claim_markers(rows))
        # ЛОТОК — ВТОРАЯ ПОЛОВИНА ТОГО ЖЕ КОРПУСА (09.09.2026). Информационная заявка
        # ряда больше не создаёт, а дедуп и суточный потолок считаются ПО ЖИВОЙ ОЧЕРЕДИ.
        # Не спросив лоток, счёт увидел бы пустой день и разрешил ставить заново то,
        # что уже лежит: тот же класс, что дал пять разведок за сутки при потолке 2.
        # Лоток не прочитан → ok=False, и зовущий не ставит НИЧЕГО — так же, как при
        # молчащем мосте: непрочитанный корпус значит «не знаю, сколько поставлено».
        lot, lot_ok, lot_why = zayavki_lotok_run.marker_rows(self._root)
        if not lot_ok:
            return [], False, lot_why
        out.extend(self.claim_marks(lot))
        return out, True, ""

    def claim_marks(self, rows):
        """Ряды (очереди или лотка) → пары «день, ключ» СВОЕЙ ступени. → list.

        Отдельным методом ровно затем, чтобы ступень E переопределила его СВОЕЙ
        регуляркой, не заводя второго чтения лотка: маркеры у ступеней разные, а
        лоток один.
        """
        return review_intake.claim_markers(rows)

    def to_lotok(self, text, frm, day=""):
        """Заявка → ЛОТОК вместо ряда ожидания. → (взяли?, адрес|None, почему).

        ЕДИНСТВЕННОЕ МЕСТО, ГДЕ РЕШЕНИЕ СТАНОВИТСЯ ДЕЙСТВИЕМ. Своего различителя
        здесь нет: род и «держит ли работу» судит `zayavki_route_pc.decide`, тот
        же, которым полоса уже живёт. Оба признака обязаны совпасть; разошлись,
        не спрошены, рубильник поднят, модуль не поднялся, файл не лёг — ПРЕЖНИЙ
        ПУТЬ, ряд и карточка. Направление отказа выбрано в сторону владельца:
        заглушенная заявка дороже лишнего ряда.
        """
        if not zayavki_lotok_run.enabled():
            return False, None, "лоток выключен рубильником %s" % zayavki_lotok_pc.OFF_FLAG
        try:
            row = zayavki_lotok_pc.row_of(text, frm)
            verdict = zayavki_lotok_pc.informational(
                row, owner_work=self._d._is_owner_work(row, ()))
            if not verdict.get("informational"):
                return False, None, verdict.get("why") or "не информационная заявка"
            marks = self.claim_marks([{"task_text": text}])
            got_day, key = (marks[0] if marks else (day, ""))
            verdict = dict(verdict, key=key)
            stamp = now_iso()
            ok, rel, why = zayavki_lotok_run.put(text, verdict, stamp,
                                                 got_day or day, root=self._root)
            if not ok:
                return False, None, why
            return True, rel, verdict.get("why") or ""
        except Exception as exc:                   # noqa: BLE001 — сбой = прежний путь
            return False, None, "лоток не сработал (%s: %s) — ставим ряд как прежде" % (
                type(exc).__name__, exc)

    def place(self, text, topic=None):
        """Ряд-заявка в очередь + карточка владельцу. → (ok, id|None, причина).

        Порядок ровно как у owner-карточки ревизора: enqueue → claim →
        set_needs_approval, синхронно. Обрыв на середине оставляет сироту в
        ``new``/``in_progress``, и её добьёт гард ``process_new`` — заявка не
        исполняется НИ ОДНОЙ веткой, даже осиротевшая.
        """
        # ЛОТОК СТОИ́Т ПЕРВЫМ, ДО enqueue (09.09.2026). Прежний замок снимал у ряда
        # ТЕМУ карточки — рычаг, которого принимающая сторона не читает вовсе
        # (devbot VPS отбирает ряды по метке `from`, живой ряд #232). Значит
        # перекрывать надо ПОВОД: пока ряд `needs_approval` существует, он висит у
        # владельца. Взял лоток — ряда не создаётся ни одного.
        took, rel, why_lot = self.to_lotok(text, CLAIM_FROM)
        if took:
            return True, rel, ""
        d = self._d
        ok, tid, err = d.enqueue_pc_task(text, frm=CLAIM_FROM)
        if not ok:
            return False, None, str(err or "enqueue отклонён")
        d.bc.claim_task(tid)
        # `frm` называем ЯВНО: второй признак маршрута (`_is_owner_work`) судит по `from`
        # ряда, а не по тексту, и выведенное из маркера значение слабее настоящего.
        res = d.bc.set_needs_approval(tid, text, topic=topic or d.NEEDS_APPROVAL_TOPIC,
                                      frm=CLAIM_FROM)
        if not (isinstance(res, dict) and res.get("ok")):
            why = str((res or {}).get("error_text") or (res or {}).get("error")
                      or "мост не ответил распиской")
            return False, tid, why[:200]
        return True, tid, ""


# ───────────────────────────── журнал ─────────────────────────────


def journal(line, repo=HERE, runner=None):
    """Строка-индекс в журнал штатным писателем. → (код, вывод).

    Кириллица идёт СТДИНОМ: в argv она на этой полосе коверкается (известный
    класс), а у писателя весь argv и так считается текстом записи.
    """
    run = runner or subprocess.run
    try:
        done = run([sys.executable, os.path.join(repo, JOURNAL_WRITER), "-"],
                   input=line.encode("utf-8"), stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE, timeout=240)
    except Exception as exc:                       # журнал НИКОГДА не роняет контур
        return -1, "журнал не ответил: %s" % exc
    return done.returncode, done.stdout.decode("utf-8", "replace").strip()


# ───────────────────────────── оборот ─────────────────────────────


def tick(root=HERE, state_path=None, inbox=DEFAULT_INBOX, budget=DEFAULT_BUDGET,
         limit=None, pack=None, place=False, write_journal=False, clock=None,
         queue=None, journal_fn=None, force=False):
    """Один оборот ступени B. → dict отчёта.

    ``place=False`` — сухой ход: заявки построены, очередь не тронута. Боевой ход
    (``place=True``) отличается ровно тремя действиями: сверка дедупа по живой
    очереди, постановка рядов и запись реестра.

    ``force=True`` — ручной заход берёт ОТЛОЖЕННОЕ (бутстрап, бюджет прошлых
    суток): человек назвал пакет вслух, и «мы решили не разгребать backlog» ему не
    возражение. Уже ПОСТАВЛЕННОЕ не берётся и здесь — дубль остаётся невозможным
    ни на одной дороге.
    """
    state_path = state_path or _path(root, DEFAULT_STATE)
    stamp = now_iso(clock)
    today = review_intake.today_utc(stamp)
    built = build(root, inbox)
    claims = built["claims"]
    if pack:
        claims = [c for c in claims
                  if any(pack in (s.get("pack") or "") for s in c["claim"].get("sources") or [])]
    state = read_state(state_path)
    bootstrap = not state.get("bootstrap_at")
    # На БУТСТРАПЕ ставим только НАЗВАННОЕ вслух (`--pack`/`--limit`). Оборот демона ничего не
    # называет — значит первый его заход не разгребает backlog вовсе, и это ровно тот приём,
    # которым живёт `maybe_revizor`: контур родился сегодня, а лоток лежит со вчера.
    explicit = bool(pack) or limit is not None
    report = {"acted": False, "why": "", "bootstrap": bootstrap, "today": today,
              "built": len(built["claims"]), "selected": 0, "placed": [], "failed": [],
              "held": [], "skipped": built["skipped"], "tree_files": built["tree_files"],
              "line": ""}
    if not place:
        report["why"] = "сухой ход: заявки построены, очередь не тронута"
        report["held"] = [(c["claim"]["key"], "сухой ход") for c in claims]
        return report
    markers, ok, why = (queue or Queue(root=root)).markers() if queue is not False else ([], True, "")
    if not ok:
        # ТА ЖЕ доктрина, что у ревизора: очередь недоступна → дедуп не сверить →
        # НЕ ставим ничего. Слепая постановка дублей дороже отложенной заявки.
        report["why"] = "очередь недоступна (%s) — дедуп не сверить, не ставим ничего" % why
        return report
    if bootstrap and not explicit:
        take, held = [], [(c["claim"], "бутстрап: backlog не разгребаем") for c in claims]
    else:
        known = known_keys({"claims": state.get("claims")}) if force else known_keys(state)
        take, held = review_intake.select(
            [c["claim"] for c in claims], placed=known, markers=markers,
            today=today, budget=budget if not force else max(budget, len(claims)), limit=limit)
    by_key = {c["claim"]["key"]: c for c in claims}
    report["selected"] = len(take)
    q = queue or Queue(root=root)
    attempted = set()
    for claim in take:
        attempted.update(review_intake.claim_keys(claim))
        item = by_key[claim["key"]]
        text = review_intake.claim_text(claim, item["premise"], today)
        ok, tid, why = q.place(text)
        if not ok:
            report["failed"].append({"key": claim["key"], "why": why, "id": tid})
            continue
        report["placed"].append({"key": claim["key"], "id": tid, "kind": claim["kind"],
                                 "premise": item["premise"]["outcome"],
                                 "sources": len(claim["sources"])})
        state.setdefault("claims", {})[claim["key"]] = {
            "placed_at": stamp, "queue_id": tid, "kind": claim["kind"],
            "premise": item["premise"]["outcome"], "sources": len(claim["sources"]),
            "channels": claim.get("channels") or [],
            "keys": review_intake.claim_keys(claim),
        }
        if write_journal:
            (journal_fn or journal)(review_intake.index_line(claim, item["premise"], tid), repo=root)
    for claim, reason in held:
        report["held"].append((claim["key"], reason))
    if bootstrap:
        # БУТСТРАП: остальное НЕ ставим, но и не теряем — ключи ложатся в реестр
        # словом. Backlog лотка старше контура, и раскладывать его пачкой карточек
        # владельцу значило бы будить его за работу, давно им закрытую.
        known = known_keys(state)
        for item in built["claims"]:
            keys = review_intake.claim_keys(item["claim"])
            if known.intersection(keys) or attempted.intersection(keys):
                continue           # ПОПЫТКА, СОРВАВШАЯСЯ НА МОСТУ, бутстрапом не хоронится:
                                   # она вернётся следующим заходом, а не исчезнет словом
            state.setdefault("held", {})[item["claim"]["key"]] = {
                "seen_at": stamp, "kind": item["claim"]["kind"], "keys": keys,
                "why": "бутстрап: ответ лежал в лотке до рождения ступени B — не ставили"}
        state["bootstrap_at"] = stamp
    write_state(state_path, state)
    report["acted"] = bool(report["placed"] or report["failed"])
    report["line"] = _line(report)
    if not report["why"]:
        report["why"] = "поставлено %d, отложено %d" % (len(report["placed"]), len(report["held"]))
    return report


def _line(report):
    """Строка исхода оборота для журнала/ленты. → str (одна строка)."""
    placed = report.get("placed") or []
    if not placed and not report.get("failed"):
        return ""
    parts = ["ступень B: заявок поставлено %d" % len(placed)]
    for row in placed:
        parts.append("#%s (%s, премиса %s, источников %d)"
                     % (row["id"], row["kind"], row["premise"], row["sources"]))
    if report.get("failed"):
        parts.append("не встало %d" % len(report["failed"]))
    if report.get("bootstrap"):
        parts.append("бутстрап: остальное отложено словом")
    return "; ".join(parts)


# ───────────────────────────── CLI ─────────────────────────────


def _render(report, built=None):
    lines = []
    if built is not None:
        lines.append("файлов лотка пропущено: %d" % len(built["skipped"]))
        for rel, why in built["skipped"]:
            lines.append("  — %s: %s" % (rel, why))
        lines.append("корпус дерева: %d файлов%s"
                     % (built["tree_files"], (" (%s)" % built["tree_why"]) if built["tree_why"] else ""))
        lines.append("находок: %d → заявок: %d" % (len(built["records"]), len(built["claims"])))
        for item in built["claims"]:
            claim, premise = item["claim"], item["premise"]
            lines.append("  • ключ=%s вид=%s премиса=%s источников=%d [%s]"
                         % (claim["key"], claim["kind"],
                            review_intake.PREMISE_TITLE.get(premise["outcome"]),
                            len(claim["sources"]), "+".join(claim["channels"])))
            lines.append("    премиса: %s" % premise["why"])
            for src in claim["sources"]:
                lines.append("    источник: %s · %s · %s · sha %s"
                             % (src["channel"], src["send_date"], src["pack"],
                                (src["pack_sha256"] or "—")[:12]))
    if report is not None:
        lines.append("исход: %s" % (report.get("why") or "—"))
        for row in report.get("placed") or []:
            lines.append("  ПОСТАВЛЕНА заявка #%s ключ=%s" % (row["id"], row["key"]))
        for row in report.get("failed") or []:
            lines.append("  НЕ ВСТАЛА ключ=%s: %s" % (row["key"], row["why"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ступень B ревью-контура: находки внешних каналов → заявки очереди.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="что видит контур; ничего не пишем")
    mode.add_argument("--dry", action="store_true", help="построить заявки, очередь НЕ трогать")
    mode.add_argument("--place", action="store_true", help="боевая постановка заявок")
    mode.add_argument("--tick", action="store_true", help="оборот демона (бутстрап + постановка)")
    parser.add_argument("--pack", default=None, help="ставить только заявки этого пакета")
    parser.add_argument("--limit", type=int, default=None, help="потолок заявок за заход")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="потолок заявок на сутки")
    parser.add_argument("--force", action="store_true",
                        help="взять ОТЛОЖЕННОЕ (бутстрап/бюджет); поставленное не дублируется")
    parser.add_argument("--journal", action="store_true", help="писать строки-индексы в журнал")
    parser.add_argument("--json", action="store_true", help="отчёт машиночитаемо")
    args = parser.parse_args(argv)

    if args.status or args.dry or not (args.place or args.tick):
        built = build(HERE)
        report = tick(HERE, place=False, pack=args.pack) if args.dry else None
        if args.json:
            print(json.dumps({"built": len(built["claims"]),
                              "claims": [{"key": c["claim"]["key"], "kind": c["claim"]["kind"],
                                          "premise": c["premise"], "sources": c["claim"]["sources"]}
                                         for c in built["claims"]]},
                             ensure_ascii=False, indent=2))
        else:
            print(_render(report, built))
        return 0
    report = tick(HERE, place=True, pack=args.pack, limit=args.limit, budget=args.budget,
                  write_journal=args.journal, force=args.force)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json
          else _render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
