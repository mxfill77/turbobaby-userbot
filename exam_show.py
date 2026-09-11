#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ВИТРИНА ОДНОГО КЕЙСА экзаменационного набора владельцу и ЗАПИСЬ ТАПА кандидатом.

Показывает в группу-тренажёр РОВНО ОДИН кейс: вопрос клиента, ЗАФИКСИРОВАННЫЙ ответ бота,
причину ответа одной строкой и две кнопки. Тап по кнопке — вердикт владельца; он ложится
КАНДИДАТОМ в свой журнал с автором, временем, номером и откатом по номеру.

ГЛАВНОЕ ПРАВИЛО МОДУЛЯ: ПОКАЗЫВАЕТСЯ СОХРАНЁННЫЙ ТЕКСТ, А НЕ СВЕЖАЯ ГЕНЕРАЦИЯ.
Предполёт 11.09.2026 (`docs/artifacts/2026-09-11-ЛИСТ-predpolet-17-1109.md`, §5) доказал числом:
один и тот же кейс 3 на одном и том же коммите дал круг 1 красным, а круг 2 — зелёным. Вердикт,
снятый с плавающего ответа, привязан к тексту, которого больше нет, и отменить его нечем.
Поэтому черновик собирается РОВНО ОДИН раз (`--freeze`, единственная ветка, зовущая голову),
ложится на диск вместе с отпечатком корпуса, коммитом и временем сборки, а `--show` головы не
зовёт вовсе — ни первый раз, ни повторный. Разделение проведено ПО ФАЙЛУ, а не по аккуратности:
у `show` нет ни одной дороги к `trainer_run.run_case`.

ВТОРОЕ ПРАВИЛО: ОДИН КЕЙС НА ЭКРАН. Стена из семнадцати судимых ответов тратит первое
впечатление, а оно одноразовое. Кейс называется явным ключом `--case N`; умолчания нет.

НОВОГО БОТА НЕ ЗАВОДИТ НИ ОДНОЙ ВЕТКОЙ. Отправитель — существующий `dispatch_notify`
(AGENT_BOT_TOKEN, единственное горлышко `_api` с замком пробы); ловец тапа — существующий
`pc_agent` на ТОМ ЖЕ токене. Пара уже работает на кнопках ворот (`gate:yes|no`), здесь она
переиспользована, а не скопирована.

ЧЕГО МОДУЛЬ НЕ ДЕЛАЕТ НАМЕРЕННО: не считает «пройдено N из 17», не пишет в реестр ворот
(`pc_orchestrator.client_trainer_green.json`), не переводит кандидата в действующие и не трогает
базу уроков `lesson_store.tsv` ни одним байтом. Вердикт кандидата — сырое показание владельца;
что с ним делать, решает следующий заход.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time

import io_utf8  # noqa: F401  — stdout/stderr в UTF-8 до первой печати кириллицы

REPO = os.path.dirname(os.path.abspath(__file__))

# ЕДИНСТВЕННЫЙ разрешённый адрес показа. Литерал, а не чтение живой меты, и это выбор:
# `trainer.group_id()` ходит в боевую `moderation_ipc` (её пишет живой модербот каждые 5 секунд),
# а заданию велено боевой базы не касаться. Второе следствие важнее первого: адрес, взятый из
# изменяемого места, может однажды указать в клиентский чат — здесь это невозможно по построению.
TRAINER_CHAT = -5193185299

SHOTS_DIR = os.path.join(REPO, "exam_shots")            # сохранённые черновики (в git)
VERDICTS = os.path.join(REPO, "exam_verdicts.tsv")      # журнал тапов (рантайм, вне git)
CASES = os.path.join(REPO, "trainer_cases.json")

HEADER = "номер\tсостояние\tвремя\tавтор\tкейс\tиз\tвердикт\tкоммит\tкорпус\tправила\tчерновик"
STATE_CANDIDATE = "кандидат"

# Вердикт — ЗАКРЫТАЯ таблица из двух слов. Из Telegram в журнал не уезжает ничего, кроме ключа,
# который владелец мог бы написать и сам (тот же приём, что у `pc_agent.GATE_WORDS`).
VERDICTS_WORDS = {"ok": "верно", "no": "неверно"}


# ---------------------------------------------------------------------------------------
# отпечатки: чем именно подписан показанный текст
# ---------------------------------------------------------------------------------------

def corpus_fingerprint(path=None):
    """Отпечаток корпуса кейсов — первые 16 hex sha256 ФАЙЛА, как его считает предполёт.

    Именно файла, а не разобранного json: пересборка через `json.dumps` дала бы свой отпечаток,
    который не сошёлся бы ни с одним числом в чужих артефактах."""
    with open(path or CASES, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def head_commit():
    """Короткий хеш HEAD → str. Не смог — пустая строка, а не выдуманное значение."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                           capture_output=True, text=True, timeout=20)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:                                                      # noqa: BLE001
        return ""


def rules_version():
    """Версия базы правил одним полем (заведена 11.09.2026). Нет базы — так и говорим."""
    try:
        import lesson_store
        return lesson_store.version() or ""
    except Exception:                                                      # noqa: BLE001
        return ""


def stamp(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))


# ---------------------------------------------------------------------------------------
# корпус и сохранённый черновик
# ---------------------------------------------------------------------------------------

def load_cases(path=None):
    with open(path or CASES, encoding="utf-8") as f:
        d = json.load(f)
    return d["cases"] if isinstance(d, dict) else d


def find_case(case_id, cases=None):
    cases = cases if cases is not None else load_cases()
    for c in cases:
        if str(c.get("id")) == str(case_id):
            return c, len(cases)
    return None, len(cases)


# ── ДВА СЛОТА ОДНОГО КЕЙСА: снимок ключуется КОММИТОМ СБОРКИ (заведено 12.09.2026) ──────────
# ЗАЧЕМ. До этого дня у кейса был РОВНО ОДИН файл `case-N.json`, и `freeze` отказывался его
# трогать («перезаписи нет по построению»). Правило верное — вердикт владельца привязан к тексту,
# и молча подменять текст нельзя. Но у него был второй, незамеченный эффект: после ПРАВКИ КОДА
# показать кейс на новом коде стало невозможно вовсе — единственный слот занят снимком старого.
# Развязка: слот ключуется коммитом. Снимок старого кода остаётся на диске НЕТРОНУТЫМ, снимок
# нового ложится рядом, показ берёт НОВЕЙШИЙ и печатает его коммит в карточке (он там и так был).
# Отказ от перезаписи при этом цел: слот одного и того же коммита второй раз не собирается.

LEGACY_SHOT = "case-%s.json"                    # слот до 12.09.2026 — читается, но не пишется
SLOT_SHOT = "case-%s@%s.json"                   # слот, ключуемый коммитом сборки


def shot_path(case_id, commit=None):
    """Путь слота. Без коммита — ПРЕЖНЕЕ имя (`case-N.json`): по нему читаются снимки,
    собранные до 12.09.2026, и на него по-прежнему смотрят вызывающие, которым нужен «тот самый
    файл». С коммитом — слот этого коммита."""
    if commit:
        return os.path.join(SHOTS_DIR, SLOT_SHOT % (case_id, commit))
    return os.path.join(SHOTS_DIR, LEGACY_SHOT % case_id)


def shot_slots(case_id):
    """ВСЕ слоты кейса на диске, НОВЕЙШИЙ ПЕРВЫМ. → list[(путь, shot)].

    Порядок — по `built_at` снимка, а не по mtime файла: mtime меняет любое касание диска (копия,
    git checkout, антивирус), а момент сборки записан внутри и принадлежит самому тексту. Снимок
    без `built_at` уходит в конец: неизвестный возраст не смеет выиграть у известного."""
    out = []
    try:
        names = sorted(os.listdir(SHOTS_DIR))
    except OSError:
        return out
    head, tail = LEGACY_SHOT % case_id, "case-%s@" % case_id
    for name in names:
        if name != head and not (name.startswith(tail) and name.endswith(".json")):
            continue
        path = os.path.join(SHOTS_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                shot = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(shot, dict):
            out.append((path, shot))
    out.sort(key=lambda p: str(p[1].get("built_at") or ""), reverse=True)
    return out


def shot_ref(case_id):
    """Ссылка на черновик ДЛЯ ЖУРНАЛА: путь от корня репо, а с чужого диска — как есть.

    Ссылается на ТОТ слот, который показан (новейший), а не на имя кейса вообще: вердикт владельца
    относится к конкретному тексту, и строка журнала обязана вести к нему.

    `os.path.relpath` на Windows БРОСАЕТ `ValueError`, когда цель и корень на разных томах
    (`path is on mount 'C:', start on mount 'D:'`) — поймано своим же набором, когда каталог
    черновиков подменили на временный в `%TEMP%`. Падение здесь стоило бы вердикта владельца:
    исключение прилетело бы ПОСЛЕ проверки права и слова, то есть на самом последнем шаге."""
    slots = shot_slots(case_id)
    p = slots[0][0] if slots else shot_path(case_id)
    try:
        return os.path.relpath(p, REPO)
    except ValueError:
        return p


def load_shot(case_id):
    """НОВЕЙШИЙ снимок кейса из всех слотов | None. Слотов нет — None (это не ошибка)."""
    slots = shot_slots(case_id)
    return slots[0][1] if slots else None


def question_of(case):
    """СЫРАЯ первая реплика кейса — так, как она лежит в корпусе, с шаблоном даты и всем прочим.

    Дат ЗДЕСЬ НЕ ПОДСТАВЛЯЕТ НИ ОДНОЙ, и это не недоделка: подстановка на полосе ровно одна —
    `trainer_run.substitute`, и зовёт её `freeze` (единственное место, где известно окно `ph`,
    с которым кейс уходил голове). Вторая подстановка здесь разошлась бы с прогоном молча.

    Сырая строка нужна самому снимку: она ложится в него ключом `question_raw` рядом с показанным
    вопросом. Дословно — потому что владелец судит ответ на тот вопрос, который бот видел; пересказ
    («клиент спросил про цену») сделал бы вердикт непроверяемым."""
    lines = case.get("lines") or []
    return str(lines[0]) if lines else ""


def reason_line(shot, limit=220):
    """ПРИЧИНА ответа одной строкой (пункт 5 задания) — из записки, которую получила голова.

    НЕ ВЫДУМЫВАЕТСЯ. Записки нет — так и сказано словами; сочинённая причина выглядела бы как
    объяснение бота, которого бот не давал, и владелец судил бы по ней."""
    note = " ".join(str(shot.get("note") or "").split())
    if not note:
        return "причина не записана — записки к этому кругу нет"
    return note[:limit] + ("…" if len(note) > limit else "")


def freeze(case_id, cases_path=None, runner=None, now=None, ph=None):
    """Собрать черновик кейса ОДИН раз и положить на диск. → (ok, путь|причина, shot|None).

    ЕДИНСТВЕННАЯ ветка модуля, зовущая голову. Уже лежащий черновик НЕ перезаписывается НИ ОДНОЙ
    веткой: иначе «покажи ещё раз» молча меняло бы судимый текст — ровно то, от чего модуль и
    заведён. Отказ считается ПО КОММИТУ (12.09.2026): слот этого коммита уже собран — второй раз
    голову не зовём; коммит другой — это ДРУГОЙ ПРОДУКТ, и ему положен свой слот рядом со старым.
    Старые слоты не трогаются и не удаляются никогда.

    ОКНО ДАТ (`ph`) БЕРЁТСЯ ОДИН РАЗ И ИДЁТ В ОБА МЕСТА — голове (через `run_case`) и в записанный
    вопрос (через `trainer_run.substitute`). Два отдельных вызова `placeholders()` разъехались бы
    на границе суток и месяца, и снимок хранил бы вопрос, которого голова не видела. Замер
    12.09.2026, кейс 1: в снимке лежала СЫРАЯ строка корпуса `…XMAX 300 {when_sloppy}…`, а в ответе
    головы стояли сами даты «с 6 по 11 октября» — владельцу показали шаблон вместо вопроса.

    `ph` доводом, а не только изнутри: с подставленным `runner` (набор) живое окно считать нечем и
    незачем — `placeholders()` ходит за живой таблицей периодов ради `when_cross`."""
    case, total = find_case(case_id, load_cases(cases_path))
    if case is None:
        return False, "кейса %s в корпусе нет (всего %d)" % (case_id, total), None
    commit = head_commit()
    for path, shot in shot_slots(case_id):
        if str(shot.get("commit") or "") == commit or not commit:
            return False, ("черновик кейса %s на коммите %s уже собран: %s (перезаписи нет по "
                           "построению)" % (case_id, shot.get("commit") or "?", path)), shot
    target = shot_path(case_id, commit)
    import trainer_run                                   # ленивый импорт: тянет suggest и сеть

    if ph is None:
        ph = trainer_run.placeholders()
    if runner is None:
        def runner(c):
            return trainer_run.run_case(c, ph)
    rec = runner(case)
    draft = (rec.get("draft") or "").strip()
    if not draft:
        return False, "голова не дала черновика (%s) — показывать нечего" % (
            rec.get("unknown") or "причина не названа"), None
    raw = question_of(case)
    shot = {
        "case": case.get("id"), "total": total, "name": case.get("name") or "",
        "lang": case.get("lang") or "", "question": trainer_run.substitute(raw, ph),
        "question_raw": raw,
        "draft": draft, "note": rec.get("note") or "",
        "corpus": corpus_fingerprint(cases_path), "commit": commit,
        "rules": rules_version(), "built_at": stamp(now),
    }
    os.makedirs(SHOTS_DIR, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        json.dump(shot, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return True, target, shot


# ---------------------------------------------------------------------------------------
# карточка и кнопки
# ---------------------------------------------------------------------------------------

def card_text(shot, slots_total=None):
    """Ровно пять частей пункта 5: номер из скольки · вопрос · ответ · причина · зачем тап.

    `slots_total` — сколько слотов этого кейса лежит на диске. Больше одного → карточка говорит
    прямо, что показан САМЫЙ НОВЫЙ, а прежний жив: иначе владелец, увидев второй раз тот же кейс,
    не отличит «показали заново то же» от «показали ответ нового кода»."""
    older = ""
    if slots_total and int(slots_total) > 1:
        older = ("\nЭто НОВЕЙШИЙ снимок кейса (всего слотов %d); прежние сохранены и не тронуты — "
                 "их коммиты другие." % int(slots_total))
    head = (
        "🎓 ЭКЗАМЕН · кейс %s из %s — %s\n"
        "Показан ЗАФИКСИРОВАННЫЙ ответ от %s (коммит %s, корпус %s). Голова сейчас НЕ звалась." % (
            shot.get("case"), shot.get("total"), shot.get("name") or "без имени",
            shot.get("built_at") or "?", shot.get("commit") or "?", shot.get("corpus") or "?"))
    body = (
        "\n\n❓ КЛИЕНТ:\n%s\n\n"
        "🤖 БОТ:\n%s\n\n"
        "📌 ПОЧЕМУ так: %s\n\n"
        "Ниже — твой вердикт. Он ляжет КАНДИДАТОМ (счёта «пройдено N из 17» не будет, ворота не "
        "двинутся), причина не обязательна, откат по номеру." % (
            shot.get("question") or "(вопрос не записан)",
            shot.get("draft") or "(ответа нет)", reason_line(shot)))
    return head + older + body


def markup(case_id):
    """Две кнопки. callback_data ловит `pc_agent` тем же токеном, которым карточка отправлена."""
    return {"inline_keyboard": [[
        {"text": "✅ Верно", "callback_data": "exam:ok:%s" % case_id},
        {"text": "❌ Неверно", "callback_data": "exam:no:%s" % case_id},
    ]]}


# ---------------------------------------------------------------------------------------
# ЗАМОК ПОКАЗА: кнопка не смеет быть новее процесса, который её ловит
# ---------------------------------------------------------------------------------------
# Класс измерен в этом же репозитории и стоил владельцу тапа. Живой замер 05.09.2026
# (`pc_orchestrator._agent_code_stale`, шапка на :3019): владелец нажал кнопку ворот в 10:29,
# разбор вернул «action=None ok=False» — ветку `gate:` внесли в 05:20 того же дня, а процесс
# агента стартовал 03.09 в 05:02 и нёс код коммита 2f015bb. Сводка всё это время писала «жив».
#
# Для показа экзамена цена этого класса выше, чем для ворот: первое впечатление одноразовое
# (пункт 5 задания), и карточка с кнопкой, которая молча отвечает «карточка устарела», тратит
# его целиком. Поэтому показ FAIL-CLOSED: не доказано, что живой агент несёт нашу кнопку, —
# не показываем и называем цену словами.
#
# ТРИ ИСХОДА, а не два: «лока нет / момент старта не читается» — это НЕИЗВЕСТНО, и оно тоже
# запрещает показ. Молчание источника согласием не является.

AGENT_LOCK = os.path.join(REPO, "pc_agent.lock")
# Замыкание кнопки: файл, где живёт разбор `exam:` (pc_agent), и файл, который он зовёт (мы).
BUTTON_CLOSURE = ("pc_agent.py", "exam_show.py")


def agent_carries_button(lock_path=None, mtime_fn=None, files=None):
    """Несёт ли ЖИВОЙ pc_agent код нашей кнопки? → (исход, строка словами).

    исход: True — несёт; False — точно не несёт; None — проверить нечем (НЕИЗВЕСТНО).
    Предмет — момент рождения процесса против mtime файлов замыкания кнопки, ровно тот же, что
    у `_agent_code_stale`: Python читает исходник один раз, на импорте, поэтому файл новее
    рождения процесса в память этого процесса не попадал."""
    import proc_identity
    rec = proc_identity.read_lock(lock_path or AGENT_LOCK)
    if rec is None:
        return None, "лока pc_agent нет — жив ли агент и какой код несёт, отсюда не видно"
    if not rec.get("pid"):
        return None, "лок pc_agent без номера процесса — судить нечем"
    started = rec.get("started")
    if started is None:
        return None, "в локе pc_agent нет момента старта (старый формат) — судить нечем"
    getm = mtime_fn or (lambda p: os.path.getmtime(p))
    newest, name = None, ""
    for f in (files or BUTTON_CLOSURE):
        try:
            m = getm(os.path.join(REPO, f))
        except OSError:
            continue
        if newest is None or m > newest:
            newest, name = m, f
    if newest is None:
        return None, "не прочитал mtime ни одного файла кнопки — судить нечем"
    if newest <= started:
        return True, "живой агент (pid %s) стартовал позже правки «%s» — кнопку он знает" % (
            rec.get("pid"), name)
    lag = int(newest - started)
    return False, (
        "живой агент (pid %s) СТАРШЕ кода кнопки на %d с: файл «%s» правлен после его старта. "
        "Тап по такой кнопке вернёт «карточка устарела» и пропадёт — так было 05.09 в 10:29. "
        "Цена снятия: владелец шлёт агенту в тему 205 одно слово «обновись» (он сам делает "
        "git pull и перезапускает себя), после чего показ пойдёт" % (rec.get("pid"), lag, name))


def reach_as_moderbot(chat_id=None):
    """Виден ли чат МОДЕРБОТУ → (ok, строка словами). ЧТЕНИЕ (`getChat`), не отправка.

    Зачем вторая проба рядом с `dispatch_notify.chat_reachable`. Отказ «chat not found» у нашего
    бота имеет ДВЕ причины, и лечатся они по-разному: (1) бот не добавлен в группу — владелец
    добавляет его одним движением; (2) номер группы протух — группа могла стать супергруппой, и
    тогда добавлять некого, надо брать новый номер. Одним ботом их не различить, двумя — можно:
    модербот в этой группе ЖИВЁТ (он ставит там панель кнопок), и если номер видит он, а мы нет,
    то причина ровно первая.

    Токен берёт `suggest`, который и так читает конфиг, — мы его не читаем, не логируем и не
    печатаем (тот же приём, что у `pc_orchestrator._moderation_reply_send`). Двух `getUpdates` не
    создаём: `getChat` — чтение, с живым поллером модербота оно не конфликтует."""
    import urllib.error
    import urllib.request

    import suggest
    token = (getattr(suggest, "MODERBOT_TOKEN", "") or "").strip()
    if not token:
        return None, "MODERBOT_TOKEN пуст — этой пробой судить нечем"
    data = json.dumps({"chat_id": str(chat_id if chat_id is not None else TRAINER_CHAT)})
    req = urllib.request.Request("https://api.telegram.org/bot" + token + "/getChat",
                                 data=data.encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:                                                  # noqa: BLE001
            body = {"ok": False, "description": "HTTP %s" % getattr(e, "code", "?")}
    except Exception as e:                                                 # noqa: BLE001
        return None, "проба не состоялась (%s)" % type(e).__name__
    if body.get("ok"):
        res = body.get("result") or {}
        return True, "чат виден модерботу: «%s» (%s)" % (
            res.get("title") or res.get("id"), res.get("type") or "?")
    return False, "модерботу тоже нет: code=%s %s" % (
        body.get("error_code"), str(body.get("description", ""))[:120])


# ---------------------------------------------------------------------------------------
# ПОКАЗ
# ---------------------------------------------------------------------------------------

def show(case_id, chat=None, sender=None, agent_fn=None, cases_path=None):
    """Показать СОХРАНЁННЫЙ черновик кейса в группу-тренажёр. → (ok, строка словами).

    Головы не зовёт. Проверок четыре, и каждая отказывает ДО сети, потому что показанная
    карточка неотзываема: первое впечатление тратится один раз."""
    dest = TRAINER_CHAT if chat is None else int(chat)
    if dest != TRAINER_CHAT:
        return False, ("⛔ показ разрешён РОВНО в группу-тренажёр %d; названо %d — не отправлено "
                       "ничего" % (TRAINER_CHAT, dest))
    shot = load_shot(case_id)
    if shot is None:
        return False, ("⛔ сохранённого черновика кейса %s нет (%s). Сперва «--freeze --case %s» — "
                       "показывать свежую генерацию запрещено" % (case_id, shot_path(case_id), case_id))
    live = corpus_fingerprint(cases_path)
    if (shot.get("corpus") or "") != live:
        return False, ("⛔ черновик собран на корпусе %s, а на диске сейчас %s — кейс мог поменяться. "
                       "Не показано ничего" % (shot.get("corpus") or "?", live))
    carries, why = (agent_fn or agent_carries_button)()
    if carries is not True:
        return False, ("⛔ показ не сделан: %s\n(исход «%s» — показывать кнопку, тап по которой "
                       "потеряется, дороже, чем подождать)" % (why, "НЕИЗВЕСТНО" if carries is None else "НЕТ"))
    if sender is None:
        import dispatch_notify
        sender = dispatch_notify.send_chat_strict
    channel, ok, detail = sender(card_text(shot, len(shot_slots(case_id))), dest, markup(case_id))
    if not ok:
        return False, "⛔ показ НЕ прошёл (%s): %s" % (channel, detail)
    return True, "✅ кейс %s из %s показан в %s, message_id=%s. Ждём тапа." % (
        shot.get("case"), shot.get("total"), channel, detail)


# ---------------------------------------------------------------------------------------
# ЖУРНАЛ ТАПОВ: кандидат с автором, временем, номером и откатом
# ---------------------------------------------------------------------------------------

def _esc(value):
    """Экранирование полей — ЧУЖОЕ, из `lesson_store`. Второй copy разъехался бы с первой."""
    import lesson_store
    return lesson_store.esc(value)


def _evidence(value, what):
    """Поле ДОКАЗАТЕЛЬСТВА для строки журнала: пустота здесь — новость, а не пустая графа.

    Вердикт держится на трёх опорах — коммит, отпечаток корпуса, версия правил. Пустая графа
    среди них читается как «коммита не было», хотя значит «мы его не записали», и отличить одно
    от другого потом нечем: строка журнала — это всё, что останется. Поэтому отсутствие
    называется СЛОВАМИ и видно прямо в строке.

    `isinstance`, а не `or ""`: приехавшие по ошибке вызывающего `0`, `[]` или `None` перестают
    быть неотличимы от честно пустой строки (та же зрячая форма, что у `lesson_store.promote`)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "НЕ ЗАПИСАНО(%s)" % what


def load_verdicts(path=None):
    """Журнал → список словарей. Файла нет — пустой список (это не ошибка)."""
    target = path or VERDICTS
    try:
        with open(target, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    cols = HEADER.split("\t")
    out = []
    for raw in lines[1:]:
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) < len(cols):
            parts += [""] * (len(cols) - len(parts))
        rec = dict(zip(cols, parts[:len(cols)]))
        rec["_raw"] = raw
        try:
            rec["номер"] = int(rec["номер"])
        except ValueError:
            continue
        out.append(rec)
    return out


def _next_number(rows):
    return (max((r["номер"] for r in rows), default=0)) + 1


def tap(case_id, verdict, who, path=None, right_fn=None, now=None, cases_path=None):
    """ТАП = ВЕРДИКТ, и он записывается КАНДИДАТОМ. → (ok, строка словами).

    Порядок отказов выбран, а не случился: право → слово вердикта → сохранённый черновик.
    Право первым потому, что оно единственное несимметричное: лишний отказ стоит одной ненажатой
    кнопки, лишнее «да» кладёт в журнал чужое показание под именем владельца."""
    may = right_fn
    if may is None:
        import moderation_core
        may = moderation_core.may_write_rule
    if not may(who):
        return False, ("⛔ «%s» не вправе судить экзамен: имя не в списке правящих книгу правил. "
                       "Ничего не записано. Право одно на все двери (запись урока, перевод "
                       "кандидата, снятие набора) и на пустом списке НИКОМУ — это не сбой, а "
                       "замок." % (who or "(имя не названо)"))
    word = VERDICTS_WORDS.get(str(verdict or "").strip().lower())
    if not word:
        return False, "⛔ не понял вердикт «%s»: знаю только ok и no. Ничего не записано." % verdict
    shot = load_shot(case_id)
    if shot is None:
        return False, ("⛔ тап по кейсу %s не записан: сохранённого черновика нет, а вердикт без "
                       "текста, к которому он относится, непроверяем." % case_id)
    target = path or VERDICTS
    rows = load_verdicts(target)
    number = _next_number(rows)
    row = "\t".join([
        str(number), STATE_CANDIDATE, stamp(now), _esc(who or ""),
        str(shot.get("case")), str(shot.get("total")), word,
        _esc(_evidence(shot.get("commit"), "коммит")),
        _esc(_evidence(shot.get("corpus"), "корпус")),
        _esc(_evidence(shot.get("rules"), "версия правил")),
        _esc(shot_ref(case_id)),
    ])
    need_header = not os.path.exists(target) or os.path.getsize(target) == 0
    with open(target, "a", encoding="utf-8", newline="\n") as f:
        if need_header:
            f.write(HEADER + "\n")
        f.write(row + "\n")
    return True, ("📝 Записано КАНДИДАТОМ №%d: кейс %s из %s — «%s», автор %s, время %s.\n"
                  "Коммит %s · корпус %s · правила %s.\n"
                  "Действующим не стало, счёт не двинулся, ворота не тронуты. "
                  "Откат: «exam_show.py --rollback %d»." % (
                      number, shot.get("case"), shot.get("total"), word, who or "?", stamp(now),
                      shot.get("commit") or "?", shot.get("corpus") or "?",
                      shot.get("rules") or "?", number))


def rollback(number, who="", path=None, now=None):
    """Откат вердикта ПО НОМЕРУ. Строка ОСТАЁТСЯ, меняется только состояние. → (ok, строка).

    Строку не удаляем ни одной веткой: удалённый вердикт не отличить от невыставленного, и
    перепись задним числом соврала бы про то, что владелец смотрел. Повторный откат отказывает —
    «уже откачен» это другая новость, чем «откатил»."""
    target = path or VERDICTS
    rows = load_verdicts(target)
    hit = [r for r in rows if r["номер"] == int(number)]
    if not hit:
        return False, "⛔ вердикта №%s в журнале нет — ничего не тронуто." % number
    rec = hit[0]
    if rec["состояние"] != STATE_CANDIDATE:
        return False, "⛔ вердикт №%s уже не кандидат (%s) — ничего не тронуто." % (
            number, rec["состояние"])
    with open(target, encoding="utf-8") as f:
        lines = f.read().splitlines()
    mark = "откачен(%s;%s)" % (_esc(who or "?"), stamp(now))
    done = False
    for i, raw in enumerate(lines):
        if raw == rec["_raw"]:
            parts = raw.split("\t")
            parts[1] = mark
            lines[i] = "\t".join(parts)
            done = True
            break
    if not done:
        return False, "⛔ строку вердикта №%s не нашёл на месте — ничего не тронуто." % number
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return True, "↩️ вердикт №%s откачен (%s). Строка на месте, из счёта он вышел." % (number, mark)


def trace(path=None):
    rows = load_verdicts(path)
    if not rows:
        return "Журнал вердиктов пуст — тапов не было."
    out = ["Вердиктов в журнале: %d" % len(rows)]
    for r in rows:
        out.append("№%d %s · кейс %s из %s — «%s» · %s · %s" % (
            r["номер"], r["состояние"], r["кейс"], r["из"], r["вердикт"], r["автор"], r["время"]))
    return "\n".join(out)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        description="Показ ОДНОГО кейса экзамена владельцу и запись тапа кандидатом.")
    p.add_argument("--case", help="номер кейса; умолчания нет намеренно")
    p.add_argument("--freeze", action="store_true",
                   help="собрать черновик ОДИН раз и положить на диск (единственная ветка с головой)")
    p.add_argument("--show", action="store_true",
                   help="показать СОХРАНЁННЫЙ черновик в группу-тренажёр (голову не зовёт)")
    p.add_argument("--card", action="store_true", help="напечатать карточку, ничего не отправляя")
    p.add_argument("--slots", action="store_true",
                   help="перечислить слоты кейса (новейший первым) — ничего не меняет")
    p.add_argument("--tap", choices=sorted(VERDICTS_WORDS), help="записать вердикт кандидатом")
    p.add_argument("--who", default="", help="автор вердикта (имя владельца)")
    p.add_argument("--rollback", help="откат вердикта по номеру")
    p.add_argument("--trace", action="store_true", help="перепись журнала вердиктов")
    p.add_argument("--agent", action="store_true", help="несёт ли живой pc_agent код кнопки")
    p.add_argument("--reach", action="store_true",
                   help="видна ли группа-тренажёр нашему боту (getChat, ничего не отправляет)")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.trace:
        print(trace())
        return 0
    if a.reach:
        import dispatch_notify
        ok, why = dispatch_notify.chat_reachable(TRAINER_CHAT)
        print("НАШ БОТ (AGENT_BOT_TOKEN): %s (%d) — %s" % (
            "ДОСТУПНА" if ok else "НЕДОСТУПНА", TRAINER_CHAT, why))
        mok, mwhy = reach_as_moderbot()
        print("МОДЕРБОТ (контроль):       %s — %s" % (
            {True: "ДОСТУПНА", False: "НЕДОСТУПНА", None: "НЕИЗВЕСТНО"}[mok], mwhy))
        if not ok and mok is True:
            print("⇒ номер группы ЖИВОЙ, а нашего бота в ней НЕТ: лечится добавлением бота.")
        elif not ok and mok is False:
            print("⇒ группу не видит НИ ОДИН бот: номер протух (супергруппа?) — добавлять некого.")
        return 0 if ok else 1
    if a.agent:
        carries, why = agent_carries_button()
        print("%s — %s" % ({True: "ДА", False: "НЕТ", None: "НЕИЗВЕСТНО"}[carries], why))
        return 0 if carries else 1
    if a.rollback:
        ok, msg = rollback(a.rollback, who=a.who)
        print(msg)
        return 0 if ok else 1
    if a.tap:
        if not a.case:
            print("⛔ кейс не назван (--case N) — ничего не записано.")
            return 2
        ok, msg = tap(a.case, a.tap, a.who)
        print(msg)
        return 0 if ok else 1
    if not a.case:
        print("⛔ кейс не назван (--case N). Умолчания нет: один кейс на экран — это правило.")
        return 2
    if a.freeze:
        ok, where, _shot = freeze(a.case)
        print(("✅ черновик кейса %s собран и сохранён: %s" % (a.case, where)) if ok
              else "⛔ черновик не собран: %s" % where)
        return 0 if ok else 1
    if a.slots:
        slots = shot_slots(a.case)
        if not slots:
            print("слотов кейса %s нет ни одного — сперва --freeze." % a.case)
            return 1
        print("слотов кейса %s: %d (новейший первым)" % (a.case, len(slots)))
        for i, (path, shot) in enumerate(slots):
            print("%s %s · коммит %s · корпус %s · собран %s" % (
                "→" if i == 0 else " ", os.path.basename(path), shot.get("commit") or "?",
                shot.get("corpus") or "?", shot.get("built_at") or "?"))
        return 0
    if a.card:
        shot = load_shot(a.case)
        if shot is None:
            print("⛔ сохранённого черновика кейса %s нет — сперва --freeze." % a.case)
            return 1
        print(card_text(shot, len(shot_slots(a.case))))
        return 0
    if a.show:
        ok, msg = show(a.case)
        print(msg)
        return 0 if ok else 1
    print("⛔ не назван ни один ключ действия (--freeze / --show / --card / --tap / --rollback).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
