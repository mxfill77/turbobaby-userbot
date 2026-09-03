# -*- coding: utf-8 -*-
"""shtab_box_run.py — РУКИ ящика заданий Штаба: папка мозга → ряд очереди полосы ПК.

Разделение то же, что у ступеней A/B/D/E и слоя ожиданий: всё, что РЕШАЕТ, живёт
чистым :mod:`shtab_box`; здесь только ввод-вывод — мозг, очередь, диск, журнал.

    venv/Scripts/python.exe shtab_box_run.py --status   # что лежит в ящике и что мешает
    venv/Scripts/python.exe shtab_box_run.py --dry      # что УШЛО БЫ в очередь, дословно
    venv/Scripts/python.exe shtab_box_run.py --place    # боевая постановка
    venv/Scripts/python.exe shtab_box_run.py --show KEY # дословный текст ряда по ключу задания

ИСТОЧНИК ЗАДАНИЙ С 03.09.2026 — ОТДЕЛЬНЫЕ ДОКУМЕНТЫ ПАПКИ МОЗГА с префиксом
:data:`shtab_box.TASK_PREFIX` в имени. Раньше задание было блоком внутри узла
:data:`shtab_box.NODE_NAME`; узел остаётся ЧЕЛОВЕЧЕСКОЙ ШАПКОЙ и держит метки
снятия сигналов, а источником задач быть перестал — и об этом говорится вслух
(:func:`shtab_box.old_door_line`), потому что молча закрытая дверь выглядит
поломкой.

ЧТЕНИЕ — ТОЛЬКО ЧЕРЕЗ ДОВЕРЕННОГО ПИСАТЕЛЯ (:mod:`brain_writer`), и только его
функциями ЧТЕНИЯ: :func:`brain_writer.list_folder` (перечисление папки) и
:func:`brain_writer.read_text` (тело документа по file id). Причина не в
вежливости: секреты моста берёт САМ процесс писателя, а скрипт, читающий
конфигурацию ради похода в мозг, — красный канал по гарду (класс 328). Здесь нет
ни одной ветки, которая смотрит в окружение за ключами: их не видит и не может
увидеть этот файл.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ НИ ОДНОЙ ВЕТКОЙ — и это держит тест
``SHTAB_BOX_READS_ONLY`` обходом AST, а не обещание здесь:

  • НЕ ПИШЕТ В МОЗГ. Ни ``append``, ни ``apply``, ни ``write_doc``, ни
    ``create_plain``. Ящик, в который полоса умеет писать, — это машина, ставящая
    себе задачи собственными словами; вся граница доверия ящика проходит по тому,
    ЧЬЯ это папка, и запись с нашей стороны стирает её целиком. Переезд 03.09
    границу не двинул: папка та же, писатель тот же, инвариант тот же.
  • НЕ ХОДИТ ЗА ПРЕДЕЛЫ ПАПКИ. Признак задания ровно один
    (:data:`shtab_box.TASK_PREFIX`), имя шапки ровно одно
    (:data:`shtab_box.NODE_NAME`); оба взяты из чистого модуля, литералом здесь не
    набраны.
  • НЕ ПРАВИТ ГАРДА, КАРТОЧЕК И ВОРОТ. Ящик добавляет ИСТОЧНИК задач, а не право
    их исполнять: взятый блок встаёт обычным зелёным рядом ``new`` и идёт тем же
    ``process_new``, что и задача, присланная владельцем руками.
  • НЕ УДАЛЯЕТ И НЕ ТРОГАЕТ ПРОЦЕССОВ.

СИГНАЛЬНАЯ ОСТАНОВКА (02.09.2026) СЧИТАЕТСЯ ЗДЕСЬ, А РЕШАЕТСЯ В :mod:`shtab_box_signals`.
Руки приносят четыре корпуса фактов и ни одного вердикта: закрытые ряды очереди с
маркером ящика (сигналы А и Б), открытые ряды в ``needs_approval`` (сигнал В),
реестр вердиктов судьи закрытия (:func:`read_ledger`) и МЕТКИ СНЯТИЯ, вычитанные
из того же узла-ящика. Ни одного нового чтения ветка не завела: закрытые ряды
ящик уже читал ради суточного потолка, открытые — ради замка владельца, узел —
ради самих заданий. Своё здесь ровно одно — реестр вердиктов, и это локальный
JSON, а не поход в мост.

СТОП-СЛОВО — ФАЙЛ, А НЕ ПЕРЕМЕННАЯ, и живёт оно здесь, потому что это диск:

    pc_orchestrator.shtab_box.off        (в корне репозитория)

Гасит ящик ЦЕЛИКОМ со следующего витка — без рестарта демона и без правки кода.
Устройство одолжено у рубильников демона (``_flag_off_file``) намеренно: у полосы
уже есть выключатель, который проверяется НА КАЖДОМ вызове и не болеет тихим
отказом наследуемого окружения (новый процесс демона рождается с ``env`` родителя,
и выключение правкой конфигурации по эстафете self-update просто не доезжает).
Направление отказа КОНСЕРВАТИВНОЕ: не смогли ответить, есть ли файл, — считаем
ВЫКЛЮЧЕНО. У аварийного рубильника «не знаю» обязано значить «стоп».
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import recon_auto_run
import review_intake
import shtab_box
import shtab_box_signals as sig

HERE = os.path.dirname(os.path.abspath(__file__))

# Имя стоп-файла собрано ТЕМ ЖЕ правилом, что у рубильников демона
# (`pc_orchestrator._flag_off_file`): один вид у всех выключателей полосы — чтобы
# владелец не вспоминал, какой из них называется иначе.
STOP_FILE = "pc_orchestrator.shtab_box.off"

# Корпуса рядов очереди берутся У СТУПЕНИ E, а не набираются здесь заново: это
# один и тот же вопрос «где живут маркеры суток», и два его экземпляра разъехались
# бы молча. `failed` и `done` читаются ОБА — маркер взятого задания уходит из
# открытых рядов вместе с закрывшейся строкой, и счёт по одним открытым мерил бы
# ОДНОВРЕМЕННОСТЬ вместо суток (урок ступени E, 01.09.2026: пять постановок за
# одни сутки при потолке 2).
OPEN_STATUSES = recon_auto_run.OPEN_STATUSES
CLOSED_STATUSES = ("failed", "done")

FROM = "Filipp-shtab"          # from ряда: видно в очереди, откуда взялась задача


def now_iso(clock=None):
    """Текущее время UTC в ISO. Единственная точка, где ветка смотрит на часы."""
    stamp = (clock or datetime.datetime.now)(datetime.timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def _path(root, rel):
    return rel if os.path.isabs(rel) else os.path.join(root, *rel.split("/"))


# ───────────────────────────── стоп-слово ─────────────────────────────


def stop_file(root=HERE):
    """Путь стоп-файла ящика. → str."""
    return _path(root, STOP_FILE)


def stopped(root=HERE):
    """Ящик выключен стоп-файлом? → (bool, слова).

    Проверяется НА КАЖДОМ обороте — поэтому файл действует со следующего витка и
    рестарта не требует. Не смогли ответить (диск не отдал ответа) → ВЫКЛЮЧЕНО:
    у аварийного рубильника «не знаю» значит «стоп», а не «работай».
    """
    path = stop_file(root)
    try:
        if os.path.exists(path):
            return True, "ящик выключен стоп-файлом %s (снять: удалить файл)" % STOP_FILE
        return False, ""
    except Exception as exc:                            # noqa: BLE001
        return True, ("проверить стоп-файл %s не удалось (%s) — считаем ВЫКЛЮЧЕНО"
                      % (STOP_FILE, exc))


# ───────────────────────────── узел-ящик ─────────────────────────────


def read_ledger(root=HERE):
    """Реестр вердиктов судьи закрытия → (записи, ok, причина).

    ТРЕТИЙ ИСХОД НАЗВАН ОТДЕЛЬНЫМ ПРИЗНАКОМ, а не пустотой словаря, и это здесь
    обязательно. :func:`done_judge_pc.read_ledger` на любой отказ отдаёт ``{}`` —
    и сам об этом честно предупреждает: «пустой ответ НЕ означает „всё хорошо“,
    он означает отсутствие доказательств». Пустой реестр у полосы, которая ещё
    ничего не судила, и нечитаемый реестр — разные новости, а по длине словаря
    они одинаковы. Различаем их ПОВТОРНЫМ вопросом к диску: файла нет вовсе —
    честный ноль; файл есть, а записей нет — прибор отказал.

    Своего разбора JSON здесь нет ни строки: реестр читает его владелец.
    """
    try:
        import done_judge_pc

        path = done_judge_pc.ledger_path(root)
        rows = done_judge_pc.read_ledger(root)
    except Exception as exc:                            # noqa: BLE001 — любой отказ = «неизвестно»
        return {}, False, "реестр вердиктов не прочитан: %s" % str(exc)[:160]
    if rows:
        return rows, True, ""
    try:
        if not os.path.exists(path):
            return {}, True, "реестра вердиктов ещё нет — судья не судил ни одной"
    except Exception as exc:                            # noqa: BLE001
        return {}, False, "реестр вердиктов не прочитан: %s" % str(exc)[:160]
    return {}, False, ("файл реестра вердиктов есть, а записей в нём ноль — считаем это "
                       "отказом чтения, а не честным нулём")


def read_node(name=None, reader=None):
    """Назначенный узел мозга → (текст, ok, причина).

    ТРЕТИЙ ИСХОД ОБЯЗАТЕЛЕН И ЖИВЁТ ЗДЕСЬ: узла нет, мост молчит, имя не
    разрешилось — всё это ``ok=False``, то есть «что лежит в ящике, НЕИЗВЕСТНО».
    Пустой текст доверенный писатель сам считает отказом чтения, а не пустым
    доком (`brain_writer._read`), и это ровно то поведение, которое ящику нужно:
    «ящик пуст» и «ящик не прочитан» — разные новости, и вторая не смеет
    прикидываться первой.

    ``reader`` — точка подмены для тестов. Живой путь берёт ЧТЕНИЕ доверенного
    писателя; секреты моста читает ОН, а не этот модуль.
    """
    node = name or shtab_box.NODE_NAME
    if reader is None:
        def reader(doc):
            import brain_writer

            return brain_writer.read_text(name=doc)
    try:
        text = reader(node)
    except Exception as exc:                            # noqa: BLE001 — любой отказ = «неизвестно»
        return "", False, "узел %s не прочитан: %s" % (node, str(exc)[:200])
    if not isinstance(text, str) or not text.strip():
        return "", False, "узел %s отдал пустой текст — считаем это отказом чтения" % node
    return text, True, ""


def read_folder(prefix=None, lister=None):
    """Дети папки мозга с префиксом заданий → (файлы, ok, причина).

    ИСТОЧНИК ЗАДАНИЙ С 03.09.2026. Отбор передаётся мосту (он умеет отбирать сам и
    экономит нам список из полусотни имён), но РЕШАЕТ, что считать заданием,
    :func:`shtab_box.parse_folder` — здесь только доставка.

    ═══ УСЕЧЕНИЕ = ОТКАЗ, А НЕ КОРОТКИЙ СПИСОК ═══════════════════════════════

    Это главная ветка функции, и она не осторожничает, а лечит ИЗМЕРЕННЫЙ обман.
    Мост перебирает детей папки в порядке Drive (произвольном), обрывается по
    лимиту и сортирует по имени УЖЕ ОБРЕЗАННЫЙ кусок. Замер 03.09 на живой папке
    из 49 файлов, ``limit=3``: вернулись ``KB_QUEUE_STATE``, ``KB_cowork_log``,
    ``KB_queue_state_pc`` — отсортированные, выглядящие началом и началом НЕ
    ЯВЛЯЮЩИЕСЯ (первые три по алфавиту начинаются с ``BACKUP…``).

    Порядок разбора у нас назначен ПО ИМЕНИ, то есть «первым берём первый по
    имени». На усечённом списке это правило дало бы уверенный и НЕВЕРНЫЙ ответ:
    взяли бы «первый» из произвольного куска. Поэтому усечение — это ``ok=False``,
    то есть НЕИЗВЕСТНО и ноль постановок, а не «взяли что видно».

    Пустой список при ``ok=True`` — честный ноль (замер 03.09:
    ``prefix='shtab_task_'`` → ``ok=True, count=0``), и это НЕ отказ.
    """
    pref = shtab_box.TASK_PREFIX if prefix is None else str(prefix)
    if lister is None:
        def lister(p):
            import brain_writer

            return brain_writer.list_folder(prefix=p)
    try:
        r = lister(pref)
    except Exception as exc:                            # noqa: BLE001 — любой отказ = «неизвестно»
        return [], False, ("перечисление папки мозга не удалось: %s: %s"
                           % (type(exc).__name__, str(exc)[:180]))
    if not isinstance(r, dict) or not r.get("ok"):
        return [], False, ("перечисление папки мозга ответило НЕ ok: %s"
                           % json.dumps(r, ensure_ascii=False, default=str)[:200])
    files = r.get("files")
    if not isinstance(files, list):
        return [], False, "в ответе перечисления нет списка файлов — читать нечего"
    if r.get("truncated"):
        return [], False, ("перечисление УСЕЧЕНО (отдано %s из папки, упёрлись в потолок) — "
                           "вернувшийся список не начало папки, а произвольный её кусок, "
                           "отсортированный так, что выглядит началом; порядок разбора по нему "
                           "дал бы уверенно неверный «первый»" % (r.get("count"),))
    return files, True, ""


def read_doc_text(doc_id, reader=None):
    """Тело задания ПО file id → (текст, ok, причина).

    ПО ID, А НЕ ПО ИМЕНИ, и выбора здесь нет: документ, созданный прямо в папке, в
    реестре моста ключа не имеет, и на имя мост отвечает дословно «unknown_name …
    в живом реестре такого ключа нет» (живая проба 03.09 на ``shtab_box_probe``).
    Вторая дорога к такому документу не закрыта нами — её не существует.

    Пустой текст доверенный писатель сам считает отказом чтения, а не пустым доком,
    и это ровно то поведение, которое ящику нужно.
    """
    ident = str(doc_id or "").strip()
    if not ident:
        return "", False, "у документа нет file id — читать его нечем"
    if reader is None:
        def reader(fid):
            import brain_writer

            return brain_writer.read_text(doc_id=fid)
    try:
        text = reader(ident)
    except Exception as exc:                            # noqa: BLE001 — любой отказ = «неизвестно»
        return "", False, ("тело документа не прочитано (id %s): %s: %s"
                           % (ident[:12], type(exc).__name__, str(exc)[:160]))
    if not isinstance(text, str) or not text.strip():
        return "", False, "документ (id %s) отдал пустой текст — считаем это отказом чтения" % ident[:12]
    return text, True, ""


# ───────────────────────────── очередь ─────────────────────────────


class Queue(recon_auto_run.Queue):
    """Очередь ящика: тот же клиент моста и тот же механизм, что у ступеней B и E.

    Наследуемся, а не переписываем: постановка ряда, чтение статусов и признак
    «в очереди есть работа владельца» — один механизм на полосу. Отличие ровно
    одно — метка ``from``, по которой в очереди видно происхождение задачи.

    ЗАЯВОК ВЛАДЕЛЬЦУ ЯЩИК НЕ СТАВИТ НИ ОДНОЙ, и ``place_ask`` здесь не зовётся
    нигде: у ящика нет собственного мнения, о котором стоило бы спрашивать. Он
    либо берёт готовый блок, либо не берёт и говорит почему.
    """

    def place_task(self, text):
        """Задание Штаба — зелёный ряд ``new``. → (ok, id|None, причина).

        Ни ``claim``, ни ``set_needs_approval``: ряд подберёт штатный
        ``process_new`` и исполнит headless-ребёнком под обычным гардом. Это и
        есть «демон сам берёт задачу из ящика» — не имитация ряда, а обычная
        работа полосы.
        """
        ok, tid, err = self._d.enqueue_pc_task(text, frm=FROM)
        return (bool(ok), tid, "" if ok else str(err or "enqueue отклонён"))


# ───────────────────────────── сборка ─────────────────────────────


def build(root=HERE, queue=None, clock=None, reader=None, node=None,
          budget=shtab_box.DAILY_BUDGET, ledger=None, lister=None, doc_reader=None,
          prefix=None, read_max=shtab_box.READ_MAX):
    """Всё, что нужно для решения: ящик + очередь + маркеры. → dict.

    Ничего не ставит и никуда не пишет — этой же функцией живут ``--status`` и
    ``--dry``.

    ПОРЯДОК ЧТЕНИЙ — ЭТО ПОРЯДОК ИХ ЦЕНЫ, и он выбран, а не случаен:

    1. **стоп-файл** — стои́т диска;
    2. **ПЕРЕЧИСЛЕНИЕ ПАПКИ** — один вызов моста, отдаёт имена и file id всех
       заданий разом. Пустой ящик закрывается ЗДЕСЬ, не тронув ни очереди, ни
       единого тела;
    3. **ШАПКА** :data:`shtab_box.NODE_NAME` — второе чтение моста, и оно
       БЕЗУСЛОВНОЕ. Соблазн прочитать её только при живом кандидате велик и
       неверен: шапка несёт метки снятия сигналов И блоки СТАРОЙ ФОРМЫ, а старый
       блок опаснее всего ровно тогда, когда папка пуста — Штаб положил задание по
       прежней памяти, и «ящик пуст» было бы про пустую папку правдой, а про
       положенное задание враньём;
    4. **открытые ряды** — замок владельца;
    5. **закрытые ряды** — только при живом кандидате. Замер полосы:
       ``get_pending("done")`` — 120 строк за **26.8 с** против 2.9 с у ``failed``
       (`queue_snapshot_pc`, 14.08.2026), а виток демона исполняется синхронно;
    6. **ТЕЛА ДОКУМЕНТОВ** — последними и не все. Тело стои́т отдельного похода в
       мост, поэтому не читается у того, кого всё равно не возьмут: у снятого
       Штабом и у уже взятого (ключ в маркерах). Оставшиеся читаются по порядку
       имён, не больше :data:`shtab_box.READ_MAX` за виток, и КАЖДЫЙ непрочитанный
       получает свою строку — тихого обрезания здесь нет.

    Третье состояние названо честно везде: «не спрашивали, потому что незачем» —
    это НЕ отказ прибора и НЕ прочитанный ноль.
    """
    stamp = now_iso(clock)
    today = review_intake.today_utc(stamp)
    out = {"schema": shtab_box.SCHEMA, "stamp": stamp, "today": today,
           "node": node or shtab_box.NODE_NAME, "node_ok": False, "node_why": "",
           "prefix": shtab_box.TASK_PREFIX if prefix is None else str(prefix),
           "folder_ok": False, "folder_why": "", "files": 0,
           "docs": [], "bad": [], "gates": {}, "read": 0, "old_door": "",
           "off": False, "off_why": "",
           "queue_ok": False, "queue_asked": False, "queue_why": "",
           "owner_busy": None, "owner_rows": [],
           "marks_ok": False, "marks_asked": False, "marks_why": "", "task_marks": [],
           "rows": 0, "taken_today": None,
           # СИГНАЛЬНАЯ ОСТАНОВКА: три состояния, как у маркеров суток, — посчитана ·
           # не посчитана, потому что до неё не дошло · посчитана и говорит «не знаю».
           "signals": [], "signals_asked": False, "signals_why": "", "released": [],
           "judged_ok": False, "judged_why": "", "stop": ""}

    out["off"], out["off_why"] = stopped(root)
    if out["off"]:
        return out

    files, folder_ok, folder_why = read_folder(out["prefix"], lister=lister)
    out["folder_ok"], out["folder_why"], out["files"] = folder_ok, folder_why, len(files)
    if not folder_ok:
        return out

    docs, bad = shtab_box.parse_folder(files, prefix=out["prefix"])
    out["docs"], out["bad"] = docs, bad

    # ШАПКА: метки снятия сигналов + ДОКЛАД О ЗАКРЫТОЙ ДВЕРИ. Её отказ НЕ
    # останавливает ящик — источником задач она быть перестала, а нечитаемые метки
    # снятия дают ровно один эффект: снятый владельцем сигнал остаётся стоять. Это
    # строже, а не слабее, и потому законно.
    text, node_ok, node_why = read_node(out["node"], reader=reader)
    out["node_ok"], out["node_why"] = node_ok, node_why
    if node_ok:
        stale, _stale_bad = shtab_box.parse_node(text)
        out["old_door"] = shtab_box.old_door_line(len(stale))

    q = None if queue is False else (queue or Queue())
    live_rows, ok, qwhy = ([], False, "очередь не спрашивали")
    if q is not None:
        live_rows, ok, qwhy = q.rows(OPEN_STATUSES)
        out["queue_asked"] = True
    out["queue_ok"], out["queue_why"] = ok, qwhy
    if ok:
        busy, ids = q.owner_busy(live_rows)
        out["owner_busy"], out["owner_rows"] = busy, ids

    closed_rows, closed_ok, closed_why = ([], False, "закрытые ряды не спрашивали")
    # ЖИВОЙ КАНДИДАТ СЧИТАЕТСЯ ДО ЧТЕНИЯ ТЕЛ, и иначе быть не может: ворота судят
    # тело, тело читается только у невзятых, а невзятые известны лишь из маркеров —
    # то есть из того самого дорогого чтения, ради которого вопрос и задан. Круг
    # разрывается ДЕШЁВЫМ признаком: кандидат — это документ, которого Штаб НЕ
    # СНИМАЛ. Папка из одних снятых документов дорогого чтения не стои́т.
    fresh = [d for d in docs if not d.get("revoked")]
    if q is None or not ok:
        closed_why = "очередь не прочитана — маркеров суток нет ни одного"
    elif out["owner_busy"]:
        closed_why = "в очереди работа владельца — дорогое чтение done не понадобилось"
    elif not fresh:
        closed_why = ("заданий, не снятых Штабом, в папке нет — дорогое чтение done "
                      "не понадобилось")
    else:
        closed_rows, closed_ok, closed_why = q.rows(CLOSED_STATUSES)
        out["marks_asked"] = True
    out["marks_ok"] = bool(ok and closed_ok)
    out["marks_why"] = closed_why
    if ok:
        all_rows = list(live_rows) + list(closed_rows)
        out["rows"] = len(all_rows)
        out["task_marks"] = shtab_box.markers(all_rows)
        if out["marks_ok"]:
            out["taken_today"] = shtab_box.taken_today(all_rows, today)

    # ── ТЕЛА ДОКУМЕНТОВ ───────────────────────────────────────────────────────
    # ПОСЛЕДНЕЕ ЧТЕНИЕ И САМОЕ ИЗБИРАТЕЛЬНОЕ: каждое тело — свой поход в мост.
    # Не читаем у снятого (его не возьмут) и у уже взятого (маркер ключа стои́т в
    # очереди). Порядок — тот, что назначен `parse_folder`, то есть по имени:
    # потолок витка обязан отрезать ХВОСТ предсказуемого списка, а не случайных.
    taken_keys = {k for _d, k in out["task_marks"]}
    queue_ue = []
    for doc in docs:
        if doc.get("revoked"):
            continue
        if doc["key"] in taken_keys:
            doc["taken"] = True
            continue
        queue_ue.append(doc)
    cap = int(read_max) if read_max is not None else len(queue_ue)
    for i, doc in enumerate(queue_ue):
        if i >= cap:
            doc["unread"] = ("тело в этот виток не читали: за виток читаем не больше %d "
                             "документов, а ждут %d — дойдём следующим витком"
                             % (cap, len(queue_ue)))
            continue
        body, body_ok, body_why = read_doc_text(doc["id"], reader=doc_reader)
        if not body_ok:
            doc["unread"] = body_why
            continue
        doc["body"] = body.strip()
        out["read"] += 1
        # Ворота считаются ДЛЯ ВСЕХ прочитанных, а не только для взятого: владелец
        # должен видеть в `--status`, почему лежащее в ящике задание не берётся, —
        # иначе ящик выглядит сломанным ровно тогда, когда он честно отказывает.
        gate_ok, reason, why = shtab_box.check(doc)
        out["gates"][doc["key"]] = {"ok": gate_ok, "reason": reason, "why": why}

    # ── СИГНАЛЬНАЯ ОСТАНОВКА ──────────────────────────────────────────────────
    # СЧИТАЕТСЯ РОВНО ТОГДА, КОГДА СПРАШИВАЛИ ЗАКРЫТЫЕ РЯДЫ, и это не экономия
    # ради экономии, а тот же третий исход, что у маркеров суток. Не дошли до
    # дорогого чтения `done` — значит либо очередь не прочитана, либо в ней
    # работа владельца, либо принятых блоков нет вовсе; во всех трёх случаях
    # ящик и так не возьмёт ничего, а рапорт «сигналы говорят НЕ ЗНАЮ» повесил
    # бы на них чужую вину и приучил владельца не верить остановке.
    #
    # ДЫРЫ ЗДЕСЬ НЕТ, и это проверяемо: единственная ветка, которая СТАВИТ ряд,
    # требует `queue_ok` и живого кандидата — то есть ровно тех условий, при
    # которых `marks_asked` истинно. Сигналы не могут промолчать над задачей,
    # которую взяли.
    if not out["marks_asked"]:
        out["signals_why"] = ("сигналы не считались — до них не дошло (%s)"
                              % (closed_why or "дорогое чтение done не понадобилось"))
    else:
        out["signals_asked"] = True
        judged, judged_ok, judged_why = (ledger or read_ledger)(root)
        out["judged_ok"], out["judged_why"] = judged_ok, judged_why
        # МЕТКИ СНЯТИЯ ЖИВУТ В ШАПКЕ, а шапка с 03.09 читается отдельно и может не
        # прочитаться. Нечитаемая шапка → меток НЕТ → снятый владельцем сигнал
        # остаётся СТОЯТЬ. Направление отказа названо вслух: оно строже, а не
        # слабее, и потому не требует третьего исхода — «не знаю, снят ли сигнал»
        # и «сигнал не снят» ведут ящик к одному и тому же поступку.
        out["released"] = sorted(sig.release_marks(text)) if node_ok else []
        left = shtab_box.budget_left(out["task_marks"], today, budget, out["marks_ok"])
        out["signals"] = sig.evaluate(
            closed=sig.box_rows(closed_rows), open_rows=live_rows, judged=judged,
            day=today, left=left, budget=budget, released=out["released"],
            rows_ok=bool(closed_ok), open_ok=bool(ok), judged_ok=bool(judged_ok),
            marks_ok=bool(out["marks_ok"]))
        out["stop"] = sig.stop_words(out["signals"], node=out["node"])
        out["signals_why"] = out["stop"] or "все сигналы молчат"
    out["queue"] = q
    return out


# ───────────────────────────── оборот ─────────────────────────────


def tick(root=HERE, place=False, limit=shtab_box.TICK_LIMIT,
         budget=shtab_box.DAILY_BUDGET, write_journal=False, clock=None, queue=None,
         journal_fn=None, reader=None, node=None, ledger=None, lister=None,
         doc_reader=None, prefix=None, read_max=shtab_box.READ_MAX):
    """Один оборот ящика. → dict отчёта.

    ``place=False`` — сухой ход: папка перечислена, документы разобраны, тела
    прочитаны, ворота посчитаны, текст ряда собран, ОЧЕРЕДЬ НЕ ТРОНУТА. Боевой ход
    отличается ровно одним действием — постановкой ряда. Реестра на диске у ящика
    НЕТ И НЕ БУДЕТ: память процесса и файл рядом с ним переживают ровно до первого
    self-update (их на этой полосе десятки в день), а дедуп обязан пережить всё.
    Источник истины один — ЖИВАЯ ОЧЕРЕДЬ.
    """
    data = build(root, queue=queue, clock=clock, reader=reader, node=node, budget=budget,
                 ledger=ledger, lister=lister, doc_reader=doc_reader, prefix=prefix,
                 read_max=read_max)
    today = data["today"]
    report = {"acted": False, "why": "", "today": today, "stamp": data["stamp"],
              "node": data["node"], "docs": len(data["docs"]), "bad": data["bad"],
              "placed": [], "failed": [], "held": [], "texts": {},
              "off": data["off"], "node_ok": data["node_ok"],
              "folder_ok": data["folder_ok"], "old_door": data["old_door"],
              "queue_ok": data["queue_ok"], "marks_ok": data["marks_ok"],
              "owner_busy": data["owner_busy"], "owner_rows": data["owner_rows"],
              "taken_today": data["taken_today"], "line": "",
              "stop": data["stop"], "signals": data["signals"],
              "signals_asked": data["signals_asked"],
              "stop_marks": sig.marks(data["signals"]),
              "signal_journal": sig.journal_line(data["signals"], today)}

    if data["off"]:
        report["why"] = data["off_why"]
        report["build"] = {k: v for k, v in data.items() if k != "queue"}
        return report

    take, held = shtab_box.select(
        data["docs"], task_marks=data["task_marks"], today=today, budget=budget,
        owner_busy=bool(data["owner_busy"]), limit=limit,
        marks_ok=bool(data["marks_ok"]), source_ok=bool(data["folder_ok"]),
        stop_words=data["stop"])
    report["held"] = [(k, why) for k, why in held]
    # Отказы РАЗБОРА докладываются наравне с отказами ворот: документ, о котором не
    # сказали, — это задание, молча пропавшее по дороге. «Где именно» называется
    # адресом того источника, из которого запись пришла: у документа папки это ИМЯ
    # (строк у него нет), у блока старой формы — строка узла.
    for row in data["bad"]:
        where = ("документ %s" % row["name"]) if row.get("name") else ("строка %s" % row.get("line"))
        report["held"].append((str(row.get("key") or "?"),
                               "не разобран: %s (%s)" % (row.get("why"), where)))
    # ЗАКРЫТАЯ ДВЕРЬ — ОТДЕЛЬНАЯ СТРОКА ОТЧЁТА, а не молчание: блок старой формы в
    # шапке иначе выглядел бы для Штаба положенным заданием, которое «не берут».
    if data["old_door"]:
        report["held"].append(("", data["old_door"]))

    for blk, text in take:
        report["texts"][blk["key"]] = text
        if not place:
            report["held"].append((blk["key"], "сухой ход: текст собран, очередь не тронута"))
            continue
        if not (data["queue_ok"] and data.get("queue") is not None):
            report["held"].append((blk["key"], "очередь недоступна — не ставим вслепую"))
            continue
        ok, tid, err = data["queue"].place_task(text)
        if not ok:
            report["failed"].append({"key": blk["key"], "why": err, "id": tid})
            continue
        report["placed"].append({"key": blk["key"], "id": tid})
        if write_journal:
            (journal_fn or _journal)(shtab_box.index_line(blk, tid, today), repo=root)

    report["acted"] = bool(report["placed"] or report["failed"])
    report["line"] = _line(report)
    report["why"] = _why(report, data)
    report["build"] = {k: v for k, v in data.items() if k != "queue"}
    return report


def _journal(line, repo=HERE):
    """Строка-индекс в журнал — ЧУЖИМ писателем (ступень B), своего не заводим."""
    import review_intake_run

    return review_intake_run.journal(line, repo=repo)


def _why(report, data):
    """Одна строка «почему исход такой». Причина названа ВСЕГДА, в том числе когда
    ничего не взято: молчание ящика и его отказ — разные новости, и различать их
    владелец обязан без чтения кода."""
    if data["off"]:
        return data["off_why"]
    # ИСТОЧНИК — ПАПКА, И ЕГО МОЛЧАНИЕ ГОВОРИТ ПЕРВЫМ. Слово «пусто» на этой ветке
    # прозвучать не может ни одной дорогой: перечисление не ответило (или ответило
    # усечённым списком) — значит НЕИЗВЕСТНО, что лежит в ящике.
    if not data["folder_ok"]:
        return ("папка заданий не перечислена (%s) — что в ящике лежит, НЕИЗВЕСТНО; "
                "не берём ничего" % (data["folder_why"] or "причина не названа"))
    if data["queue_asked"] and not data["queue_ok"]:
        return "очередь недоступна (%s) — не берём ничего" % (data["queue_why"] or "?")
    if data["owner_busy"]:
        return ("в очереди задача владельца (%s) — заданий Штаба не берём ни одного"
                % ", ".join("#%s" % i for i in data["owner_rows"]))
    # СИГНАЛЬНАЯ ОСТАНОВКА ГОВОРИТ РАНЬШЕ «ящик пуст» и раньше отказов ворот: она
    # объясняет, почему ящик не возьмёт НИЧЕГО, даже когда брать есть что, — а
    # причина «блоков 0» на остановленном ящике была бы правдой и обманом сразу.
    #
    # НО ПРЕЖНЮЮ НОВОСТЬ ОНА НЕ ВЫТЕСНЯЕТ, и это поймал набор ящика, а не глаз:
    # непрочитанный корпус закрытых рядов даёт СРАЗУ ДВЕ новости — «день считаем
    # исчерпанным» (прежний замок) и «определить сигнал нельзя» (новый). Скажи мы
    # одну, владелец пошёл бы чинить не то. Порядок внутри строки — прежняя
    # первой: она старше и её ищут глазами.
    if data["stop"]:
        if data["marks_asked"] and not data["marks_ok"]:
            return "%s · %s" % (_marks_unread(data), data["stop"])
        return data["stop"]
    if not data["docs"]:
        # ПУСТАЯ ПАПКА И ЗАКРЫТАЯ ДВЕРЬ — РАЗНЫЕ НОВОСТИ. Скажи мы здесь «ящик
        # пуст», Штаб, положивший блок в шапку по прежней памяти, прочитал бы
        # честное сообщение как поломку — задание лежит, а полоса говорит «пусто».
        empty = ("ящик пуст: документов %s* в папке мозга нет%s"
                 % (data["prefix"],
                    (", не разобрано %d" % len(data["bad"])) if data["bad"] else ""))
        return "%s · %s" % (empty, data["old_door"]) if data["old_door"] else empty
    if report["placed"]:
        return "взято %d, отложено %d" % (len(report["placed"]), len(report["held"]))
    refused = [w for _k, w in report["held"] if w.startswith("НЕ ПРИНЯТ")]
    if refused and len(refused) == len(report["held"]):
        return "документов %d, принят 0 — %s" % (len(data["docs"]), "; ".join(refused[:2]))
    # ОТКАЗ ПРИБОРА — ТОЛЬКО ЕСЛИ ПРИБОР СПРАШИВАЛИ. Дорогое чтение `done` мы
    # пропускаем сознательно, когда ставить нечего, и это НЕ поломка моста: назови
    # мы её так, каждый оборот с непринятым блоком кричал бы «сверить нечем», и
    # владелец перестал бы отличать настоящий отказ моста от нашей же экономии.
    # Ровно тот класс, который ступень E назвала третьим состоянием: «не
    # спрашивали, потому что незачем».
    if data["marks_asked"] and not data["marks_ok"]:
        return _marks_unread(data)
    return "документов %d, взято 0, отложено %d" % (len(data["docs"]), len(report["held"]))


def _marks_unread(data):
    """Прежняя новость о непрочитанном корпусе — ОДНОЙ строкой в одном месте.

    Вынесена из :func:`_why` не для красоты: она нужна в ДВУХ ветках (одна сама по
    себе, вторая рядом с фразой остановки), а два её экземпляра разъехались бы
    молча — тот же класс, которым живёт весь этот куст.
    """
    return ("закрытые ряды очереди не прочитаны (%s) — сколько заданий Штаба взято сегодня, "
            "НЕИЗВЕСТНО; день считаем исчерпанным и не берём ничего" % data["marks_why"])


def _line(report):
    """Строка исхода оборота для журнала/ленты. → str (пусто = писать нечего)."""
    placed = report.get("placed") or []
    if not placed and not report.get("failed"):
        return ""
    parts = ["ящик Штаба: взято %d" % len(placed)]
    for row in placed:
        parts.append("#%s (ключ %s)" % (row["id"], row["key"]))
    if report.get("failed"):
        parts.append("не встало %d" % len(report["failed"]))
    return "; ".join(parts)


# ───────────────────────────── CLI ─────────────────────────────


def _render(report=None, data=None):
    lines = []
    if data is not None:
        lines.append("день: %s (замер %s)" % (data["today"], data["stamp"]))
        lines.append("источник: документы папки мозга %s* — %s"
                     % (data["prefix"],
                        "перечислено, детей с префиксом %d" % data["files"] if data["folder_ok"]
                        else "НЕ ПЕРЕЧИСЛЕНА: %s" % data["folder_why"]))
        lines.append("шапка: %s — %s" % (data["node"],
                                         "прочитана" if data["node_ok"]
                                         else "НЕ ПРОЧИТАНА: %s" % data["node_why"]))
        if data["old_door"]:
            lines.append("  ⚠ %s" % data["old_door"])
        if data["off"]:
            lines.append("СТОП-ФАЙЛ: %s" % data["off_why"])
        lines.append("заданий в ящике: %d, не разобрано: %d, тел прочитано: %d"
                     % (len(data["docs"]), len(data["bad"]), data["read"]))
        for row in data["bad"]:
            lines.append("  ✗ %s: %s"
                         % (row.get("name") or ("ключ=%s" % row.get("key")), row.get("why")))
        for doc in data["docs"]:
            gate = data["gates"].get(doc["key"], {})
            if doc.get("revoked"):
                state = "ОТОЗВАНО ШТАБОМ (метка %s в имени)" % shtab_box.REVOKE_MARK
            elif doc.get("taken"):
                state = "уже брали — маркер ключа стои́т в очереди, тело не читали"
            elif doc.get("unread"):
                state = doc["unread"]
            elif gate.get("ok"):
                state = "ворота пройдены (тело %d симв.)" % len(doc.get("body") or "")
            elif gate:
                state = "НЕ ПРИНЯТО (%s): %s" % (gate.get("reason"), gate.get("why"))
            else:
                state = "состояние не определялось"
            lines.append("  • %s (ключ=%s) — %s" % (doc["name"], doc["key"], state))
        # ТРИ СОСТОЯНИЯ ОЧЕРЕДИ, А НЕ ДВА. «Не спрашивали» — это не «недоступна»:
        # ящик, не прочитавший узел, до очереди не доходит вовсе, и рапорт
        # «НЕДОСТУПНА» повесил бы на мост чужую вину. Та же поправка, что у
        # маркеров суток строкой ниже.
        lines.append("очередь: %s%s"
                     % ("прочитана, рядов %d" % data["rows"] if data["queue_ok"]
                        else ("НЕДОСТУПНА (%s)" % data["queue_why"] if data["queue_asked"]
                              else "не спрашивали — до неё не дошло"),
                        (", работа владельца: %s"
                         % (", ".join("#%s" % i for i in data["owner_rows"]) or "нет"))
                        if data["queue_ok"] else ""))
        lines.append("маркеры суток: %s (%s)"
                     % ("корпус полон" if data["marks_ok"]
                        else ("КОРПУС НЕ ПРОЧИТАН — день исчерпан" if data["marks_asked"]
                              else "не спрашивали, потому что незачем"),
                        data["marks_why"] or "прочитаны"))
        lines.append(shtab_box.digest_line(data["taken_today"] or 0, data["today"],
                                           ok=data["marks_ok"], why=data["marks_why"]))
        # ВСЕ ЧЕТЫРЕ СИГНАЛА В ОДНОМ МЕСТЕ, включая молчащие: перечень, из которого
        # молчащие вычеркнуты, читается как «других сторожей нет».
        if not data["signals_asked"]:
            lines.append(data["signals_why"] or "сигналы: не считались")
        else:
            lines.append("сигналы (реестр вердиктов: %s%s):"
                         % ("прочитан" if data["judged_ok"] else "НЕ ПРОЧИТАН",
                            (", %s" % data["judged_why"]) if data["judged_why"] else ""))
            for row in sig.all_words(data["signals"], data["today"]):
                lines.append("  · %s" % row)
            if data["released"]:
                lines.append("  снято словом владельца: %s" % ", ".join(data["released"]))
            lines.append("ОСТАНОВКА: %s" % (data["stop"] or "нет — ящик берёт как обычно"))
    if report is not None:
        lines.append("исход: %s" % (report.get("why") or "—"))
        for row in report.get("placed") or []:
            lines.append("  ВЗЯТО #%s ключ=%s" % (row["id"], row["key"]))
        for row in report.get("failed") or []:
            lines.append("  НЕ ВСТАЛО ключ=%s: %s" % (row["key"], row["why"]))
        for key, why in report.get("held") or []:
            lines.append("  отложено ключ=%s: %s" % (key or "—", why))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ящик заданий Штаба: документы папки мозга → задача полосы ПК "
                    "(не более одной за виток).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="что лежит в ящике и что мешает")
    mode.add_argument("--dry", action="store_true", help="собрать всё, очередь НЕ трогать")
    mode.add_argument("--place", action="store_true", help="боевая постановка")
    parser.add_argument("--show", default=None, help="дословный текст ряда по ключу задания")
    parser.add_argument("--limit", type=int, default=shtab_box.TICK_LIMIT,
                        help="потолок заданий за виток")
    parser.add_argument("--budget", type=int, default=shtab_box.DAILY_BUDGET,
                        help="потолок заданий на сутки")
    parser.add_argument("--journal", action="store_true", help="писать строки-индексы в журнал")
    parser.add_argument("--json", action="store_true", help="отчёт машиночитаемо")
    args = parser.parse_args(argv)

    if args.show:
        # ТЕКСТ ПО КЛЮЧУ — отдельная дорога, не зависящая от отбора: замок владельца
        # и потолок суток режут блок ДО сборки текста, и «покажи, что ты собирался
        # поставить» упиралось бы в «ничего не выбрано» ровно тогда, когда ответ
        # важнее всего.
        data = build(HERE)
        for doc in data["docs"]:
            if doc["key"] != args.show:
                continue
            text = shtab_box.task_text(doc, data["today"])
            if text:
                print(text)
            elif doc.get("revoked"):
                print("ЗАДАНИЕ ОТОЗВАНО ШТАБОМ: документ %s" % doc["name"])
            elif doc.get("unread"):
                print("ТЕЛО НЕ ПРОЧИТАНО: %s" % doc["unread"])
            else:
                gate = data["gates"].get(doc["key"], {})
                print("ЗАДАНИЕ НЕ ПРИНЯТО (%s): %s" % (gate.get("reason"), gate.get("why")))
            return 0
        print("задания с ключом %s в ящике сейчас нет (искали документ %s)"
              % (args.show, shtab_box.doc_name(args.show)))
        return 0
    if args.status or not (args.dry or args.place):
        data = build(HERE, budget=args.budget)
        data.pop("queue", None)
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str) if args.json
              else _render(None, data))
        return 0
    report = tick(HERE, place=bool(args.place), limit=args.limit, budget=args.budget,
                  write_journal=bool(args.journal))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(_render(report, report.get("build")))
    return 0


if __name__ == "__main__":            # pragma: no cover
    sys.exit(main())
