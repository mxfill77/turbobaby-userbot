# -*- coding: utf-8 -*-
"""
chatlog_ingest.py — ЗАХВАТ в хранилище переписки ТОГО, ЧТО УЖЕ ЛЕЖИТ НА ДИСКЕ (05.09.2026).

ГЛАВНОЕ, ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ: он не ходит в Telegram ни одним запросом и не правит ни
одной строки клиентского кода. Всё, что он читает, уже написано на диск боевыми процессами —
он лишь переносит это туда, где оно не умрёт от ротации. Перепись 05.09 назвала это «что уже
есть даром» (§5), и первая очередь хранилища состоит ровно из этого.

Почему чтением СО СТОРОНЫ, а не правкой самих писателей. `userbot_listen.py` и
`moderation_bot.py` — клиентский контур: их правка стои́т на закрытых воротах, а переписка
гибнет СЕГОДНЯ. Захват со стороны даёт архив немедленно и не трогает ни одного живого процесса;
цена — мы берём то, что писатель уже записал, и не можем добавить полей, которых он не пишет
(у клиентских ЛС нет номера сообщения — см. `chatlog_store.msg_key`).

ЧЕТЫРЕ ИСТОЧНИКА, и у каждого своя причина быть первым:

  userbot   `userbot.log` + `userbot.log.1` — ПОЛНЫЙ текст каждого входящего ЛС. Умирает прямо
            сейчас: замер переписи — `.log.1` стоял на 5 242 827 Б при потолке ротации 5 МБ,
            то есть следующий перекат выбрасывал самый старый бэкап целиком. 2.2 % файла —
            переписка, 97.8 % — процессный шум, из-за объёма которого она и уходит.
  dispatch  `dispatch_notify.log` — НАША ИСХОДЯЩАЯ ПОЛОВИНА форума Штаба. До 05.09 писалась
            обрезанной в 90 символов (восемь одинаковых `text[:90]`); обрезка снята, и с этого
            дня строки полные. Старые строки захватываются тоже — с честной пометкой `cut`.
  delivery  `delivery_6m.jsonl` — 8767 сообщений рабочей группы Delivery за полгода, снятые
            26.06.2026 и с тех пор лежащие ОДНОЙ копией.
  export    `data_export/**/*.json` — 30 готовых выгрузок (партнёрские, зарплатные, агентские,
            командные чаты), снятых 27.05.2026. Тоже одной копией.

ЧЕГО В ПЕРВОЙ ОЧЕРЕДИ НЕТ НАМЕРЕННО: картинок (перепись §6 — одна фотография весит как 5200
сообщений, режим медиа решается отдельно и ДО первого скачивания) и `client_chats.jsonl`
(перепись §3 выводит клиентский класс из проекта архива рабочих групп; к тому же он лежит уже
в трёх копиях и ротацией не съедается — то есть не подходит ни под «гибнет», ни под «одиночная
копия»).

ИДЕМПОТЕНТНОСТЬ. Повторный прогон безвреден: дедуп по ключу внутри файла дня
(`chatlog_store.existing_keys`). Это не удобство, а требование — захват будет вызываться снова
после каждого переката лога, и без дедупа второй прогон удвоил бы архив.
"""

import io
import os
import re
import sys
import json
import time
import argparse

import chatlog_store as store

HERE = os.path.dirname(os.path.abspath(__file__))

# ─── форум Штаба: номера тем, известные полосе ПК ────────────────────────────────────────────
# Источник — перепись 05.09 §2.1 и код (`dispatch_notify.py`, `pc_agent.py`, `pc_orchestrator.py`).
# Имена нужны индексу: строка «тема 1160» человеку ничего не говорит, а «Инбокс решений» говорит.
HQ_CHAT_ID = -1003853365891
HQ_TOPICS = {
    1160: "Инбокс решений",
    328: "Постановка задач",
    829: "Карточка Splinter",
    205: "PC-дев (пульт)",
    161: "Аудит / сводки",
    1: "Общая (General)",
}

GROUP_HQ = "hq-shtab"
GROUP_DM = "owner-dm"
GROUP_CLIENT_DM = "client-dm"
GROUP_DELIVERY = "delivery"

DELIVERY_CHAT_ID = -1002445921469


# ─────────────────────────── userbot.log ───────────────────────────

# Живой формат строки задаёт `userbot_listen.py:151`: `f"{date_iso} | {who} | {name} | {text}"`
# при хендлере с `fmt="%(message)s"` — то есть НИКАКОГО префикса логгера, строка идёт как есть.
_RE_UB_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
# Второе поле — автор, и только он: `@ник` либо `id<число>`. Проверка нужна не для красоты.
# Тем же ISO-временем начинается СЛУЖЕБНАЯ строка того же журнала
# (`log.warning(f"{_now()} | SUGGEST: сбой генерации черновика: {e}")`), а текст исключения
# может содержать что угодно, включая « | ». Без замка на форму автора такая строка легла бы
# в архив сообщением от несуществующего человека.
_RE_UB_WHO = re.compile(r"^(?:@[A-Za-z0-9_]{1,32}|id\d+)$")

NO_TEXT_MARK = "[без текста / медиа]"


def _userbot_files():
    """Живой журнал и ВСЕ его повёрнутые копии. `.log.1` — тот самый файл, который уходит
    первым при следующем перекате; `.2`/`.3` берём, если ротация успела их завести."""
    out = []
    for name in ["userbot.log"] + ["userbot.log.%d" % i for i in range(1, 10)]:
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            out.append(p)
    return out


def ingest_userbot(w, verbose=False):
    seen_lines = 0
    for path in _userbot_files():
        n = 0
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.rstrip("\n").rstrip("\r")
                if not _RE_UB_TS.match(raw):
                    continue
                parts = raw.split(" | ", 3)
                if len(parts) != 4:
                    continue
                ts, who, _name, text = parts        # _name — ИМЯ ПРОФИЛЯ, в архив не идёт
                if not _RE_UB_WHO.match(who.strip()):
                    continue
                media = []
                if text.strip() == NO_TEXT_MARK:
                    media = [{"kind": "unknown", "note": "медиа/пустое; файл не сохранён"}]
                rec = store.line(ts=ts, ref=store.who_ref(who.strip()), text=text,
                                 mid=None, topic_id=None, topic_name="ЛС",
                                 media=media, extra={"src": "userbot.log", "dir": "in"})
                if w.add(GROUP_CLIENT_DM, rec):
                    n += 1
        seen_lines += n
        if verbose:
            print("  %-22s %5d строк переписки" % (os.path.basename(path), n))
    w.note_group(GROUP_CLIENT_DM, title="Клиентские ЛС (журнал userbot)", kind="dm",
                 source="userbot.log(+ротация)")
    return seen_lines


# ─────────────────────────── dispatch_notify.log ───────────────────────────

# `%(asctime)s | %(message)s`, где message = `итог(...): channel=… ok=… [mid=…] | <текст>`.
_RE_DN_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d+$")
_RE_DN_CH = re.compile(r"channel=(\S+)")
_RE_DN_OK = re.compile(r"ok=(\S+)")
_RE_DN_MID = re.compile(r"\bmid=(\S+)")


def _dispatch_files():
    out = []
    for name in ["dispatch_notify.log"] + ["dispatch_notify.log.%d" % i for i in range(1, 10)]:
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            out.append(p)
    return out


def _channel_to_place(channel):
    """channel= из лога → (группа, номер темы, имя темы).

    Канал — это то, КУДА сообщение легло на самом деле (с учётом фолбэков), а не то, куда его
    послали. Поэтому разбор идёт по нему, а не по виду карточки."""
    ch = (channel or "").strip()
    if ch == "DM":
        return GROUP_DM, None, "личка владельца"
    if ch == "inbox":
        return GROUP_HQ, 1160, HQ_TOPICS[1160]
    if ch.startswith("topic:"):
        try:
            tid = int(ch.split(":", 1)[1])
        except ValueError:
            return GROUP_HQ, None, "тема неизвестна"
        return GROUP_HQ, tid, HQ_TOPICS.get(tid, "тема %d" % tid)
    if ch.isdigit() or (ch.startswith("-") and ch[1:].isdigit()):
        tid = int(ch)
        return GROUP_HQ, tid, HQ_TOPICS.get(tid, "тема %d" % tid)
    return GROUP_HQ, None, "не доставлено"


def ingest_dispatch(w, verbose=False):
    total = 0
    cut = 0
    for path in _dispatch_files():
        n = 0
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.rstrip("\n").rstrip("\r")
                parts = raw.split(" | ", 2)
                if len(parts) != 3:
                    continue
                m = _RE_DN_TS.match(parts[0])
                if not m or not parts[1].startswith("итог"):
                    continue
                ts = "%sT%s" % (m.group(1), m.group(2))
                head, text = parts[1], parts[2]
                ch = (_RE_DN_CH.search(head).group(1) if _RE_DN_CH.search(head) else "")
                okm = _RE_DN_OK.search(head)
                ok = bool(okm and okm.group(1).lower().startswith("t"))
                midm = _RE_DN_MID.search(head)
                # ОТСУТСТВИЕ поля mid= — это подпись строки, написанной кодом СТАРШЕ 05.09.2026,
                # то есть заведомо обрезанной по 90 символов. Значение `-` при наличии поля
                # означает другое: текст целый, а номера нет (отправка не прошла). Разница
                # между этими двумя случаями — и есть доказательство эффекта правки.
                is_cut = midm is None
                mid = None
                if midm and midm.group(1).isdigit():
                    mid = int(midm.group(1))
                group, tid, tname = _channel_to_place(ch)
                if head.startswith("итог(stop)"):
                    group, tid, tname = GROUP_HQ, None, "не отправлялось (stop)"
                rec = store.line(ts=ts, ref=store.SELF_REF, text=text, mid=mid,
                                 topic_id=tid, topic_name=tname,
                                 extra={"src": "dispatch_notify.log", "dir": "out",
                                        "ch": ch, "ok": ok, "cut": is_cut,
                                        "kind": head.split(":", 1)[0]})
                if w.add(group, rec):
                    n += 1
                    if is_cut:
                        cut += 1
        total += n
        if verbose:
            print("  %-22s %5d строк итогов" % (os.path.basename(path), n))
    w.note_group(GROUP_HQ, title="Форум Штаба TurboBaby", group_id=HQ_CHAT_ID, kind="forum",
                 source="dispatch_notify.log")
    w.note_group(GROUP_DM, title="Личка владельца (фолбэк доставки)", kind="dm",
                 source="dispatch_notify.log")
    return total, cut


# ─────────────────────────── delivery_6m.jsonl ───────────────────────────

def ingest_delivery(w, verbose=False):
    path = os.path.join(HERE, "delivery_6m.jsonl")
    if not os.path.isfile(path):
        return 0
    n = 0
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            # Номера сообщения в этой выгрузке НЕТ — снимавший её скрипт его не сохранил.
            # Значит ключ дедупа считается по содержимому (см. chatlog_store.msg_key).
            rec = store.line(ts=o.get("date"), ref=store.who_ref(o.get("author")),
                             text=o.get("text"), mid=None,
                             topic_id=None, topic_name=None,
                             extra={"src": "delivery_6m.jsonl"})
            if w.add(GROUP_DELIVERY, rec):
                n += 1
    w.note_group(GROUP_DELIVERY, title="Delivery (доставка байков клиентам)",
                 group_id=DELIVERY_CHAT_ID, kind="group", source="delivery_6m.jsonl")
    if verbose:
        print("  %-22s %5d сообщений" % ("delivery_6m.jsonl", n))
    return n


# ─────────────────────────── data_export/**/*.json ───────────────────────────

_MEDIA_MARK = {"[PHOTO]": "photo", "[VIDEO]": "video", "[VOICE]": "voice", "[FILE]": "document"}


def _export_files():
    base = os.path.join(HERE, "data_export")
    out = []
    if not os.path.isdir(base):
        return out
    for cat in sorted(os.listdir(base)):
        cdir = os.path.join(base, cat)
        if not os.path.isdir(cdir):
            continue
        for fn in sorted(os.listdir(cdir)):
            if fn.endswith(".json"):
                out.append((cat, fn, os.path.join(cdir, fn)))
    return out


def _is_personal(data, msgs):
    """Личный чат (1:1) или группа?

    Вопрос не праздный: `export_all.py` называет файлы личных чатов ПО НИКУ собеседника
    (`data_export/team/deramor.json`), а `name` в конверте для личного чата — это ИМЯ ЧЕЛОВЕКА
    («Babushka Boi»). Взять их как имя каталога и заголовок группы значило бы положить ник и имя
    в архив — то самое, чего в нём быть не должно ни в одном поле. Поэтому у личных чатов и
    каталог, и заголовок, и путь источника пседонимизируются.

    Признак первый и точный: в чате один-на-один сообщения собеседника несут `from_id`, равный
    id самого чата. Признак второй, страховочный: авторов не больше двух. Второй признак может
    ошибиться на молчаливой группе с двумя говорящими — и ошибётся В СТОРОНУ ПРИВАТНОСТИ
    (группа получит псевдоним вместо имени), что здесь и есть правильная сторона."""
    chat_id = str(data.get("id") or "")
    authors = set()
    for m in msgs:
        if isinstance(m, dict) and m.get("from_id"):
            authors.add(str(m["from_id"]))
    return (chat_id and chat_id in authors) or (0 < len(authors) <= 2)


def ingest_export(w, verbose=False):
    total = 0
    for cat, fn, path in _export_files():
        try:
            with io.open(path, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except (IOError, OSError, ValueError):
            if verbose:
                print("  %-22s ПРОПУЩЕН (не разобрался)" % fn[:22])
            continue
        msgs = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(msgs, list):
            continue
        # Slug строим из КАТЕГОРИИ и ИМЕНИ ФАЙЛА, а не из названия чата: название приходит из
        # Telegram и меняется, а каталог с историей переезжать не должен. Человеческое имя
        # ГРУППЫ уезжает в манифест (`title`) — это наша сущность, мы её сами так назвали.
        # А вот у ЛИЧНОГО чата имя файла и есть ник человека (см. `_is_personal`) — там в
        # архив не идёт ни имя файла, ни заголовок, ни путь источника.
        personal = _is_personal(data, msgs)
        if personal:
            slug = "dm-" + store.who_ref("data_export/%s/%s" % (cat, fn))[:9]
            title = None
            src = "data_export/%s/%s.json" % (cat, slug)
            group_id = None
        else:
            slug = store.slugify("%s-%s" % (cat, fn[:-5]))
            title = data.get("name")
            src = "data_export/%s/%s" % (cat, fn)
            group_id = data.get("id")
        n = 0
        for m in msgs:
            if not isinstance(m, dict):
                continue
            text = m.get("text") or ""
            media = []
            kind = _MEDIA_MARK.get(str(text).strip())
            if kind:
                # Сам файл НЕ качаем и не переносим: картинки в первую очередь не входят
                # (перепись §6). Остаётся отметка, что в этом месте было медиа.
                media = [{"kind": kind, "note": "маркер выгрузки; файл не сохранён"}]
            # Автор: сперва числовой id, потом ник. ИМЯ (`from`) не берётся никогда, ник
            # берётся ТОЛЬКО как сырьё для хеша и в строку не попадает.
            who_raw = m.get("from_id") or m.get("username") or ""
            rec = store.line(ts=m.get("date"), ref=store.who_ref(who_raw), text=text,
                             mid=m.get("id"), topic_id=None, topic_name=None, media=media,
                             extra={"src": src, "reply_to": m.get("reply_to")})
            if w.add(slug, rec):
                n += 1
        w.note_group(slug, title=title, group_id=group_id,
                     kind=("dm" if personal else cat), source=src)
        total += n
        if verbose:
            print("  %-40s %6d сообщ. → %s" % (fn[:40], n, slug))
    return total


# ─────────────────────────── руки ───────────────────────────

SOURCES = ("userbot", "dispatch", "delivery", "export")


def run(sources, verbose=True, dry=False):
    w = store.Writer()
    seen = {}
    for s in sources:
        if verbose:
            print("[%s]" % s)
        if s == "userbot":
            seen[s] = ingest_userbot(w, verbose)
        elif s == "dispatch":
            n, cut = ingest_dispatch(w, verbose)
            seen[s] = n
            seen["dispatch_cut"] = cut
        elif s == "delivery":
            seen[s] = ingest_delivery(w, verbose)
        elif s == "export":
            seen[s] = ingest_export(w, verbose)
    if dry:
        # Сухой прогон НИЧЕГО не пишет — ни строки дня, ни индекса, ни манифеста.
        total = sum(len(v) for v in w.buf.values())
        return {"dry": True, "read": seen, "would_write": total,
                "days": len(w.buf), "skipped_no_day": w.skipped_no_day}
    st = w.commit()
    st["read"] = seen
    return st


# ─────────────────────────── часы захвата: --tick (22.09.2026) ───────────────────────────
# ЧЕГО НЕ ХВАТАЛО С 05.09: не хранилища, а ЖИВОГО ВЫЗЫВАЮЩЕГО. Модули родились 05.09 одним
# коммитом (f991db9f), захват отработал один раз руками в 18:42:57 — и больше его не звал никто.
# Замер 22.09: последний байт архива 05.09 18:43:00, прирост за 16 суток — 0.
#
# ПОЧЕМУ НЕ НА ПУТИ ПРИЁМА СООБЩЕНИЯ, как просилось бы по смыслу слова «архив переписки».
# Две причины, обе с числом:
#   1) `userbot_listen.py` — КЛИЕНТСКОЕ замыкание (`client_contour.closure()`, замер 22.09:
#      50 файлов, `userbot_listen.py` внутри), то есть закрытые ворота: правка там — слово
#      владельца, а не наша;
#   2) даже будь ворота открыты, место всё равно не там. Захват — ПАКЕТНЫЙ: он перечитывает
#      `userbot.log(+ротация)`, `dispatch_notify.log`, `delivery_6m.jsonl` и 30 выгрузок целиком
#      (замер 22.09: 2.17с, 43 104 сообщения за проход). Повешенный на каждое входящее, он стоил
#      бы 2.17с и полного перечитывания архива НА КАЖДОЕ СООБЩЕНИЕ — квадрат по объёму и
#      задержка в клиентском ответе ради строки, которая и так уже лежит в журнале на диске.
# Настоящее место — оборот демона ПК (`pc_orchestrator.poll_once`), который НЕ клиентский
# (тот же замер: `pc_orchestrator.py` вне замыкания) и уже носит ровно такие вызовы
# (`_queue_snapshot`, `_srv_delivery`).
#
# ЧАСТОТУ РЕШАЕТ ЭТОТ МОДУЛЬ, А НЕ ЗВОНЯЩИЙ. Демон зовёт `--tick` каждый оборот (медиана 172с),
# а идти ли на диск — решает штамп: `CHATLOG_TICK_EVERY_MIN`, по умолчанию 360 мин = 4 захвата в
# сутки. Запас против потери огромен: `userbot.log` набирает ≈106 КБ/сут при потолке ротации
# 5 МБ, то есть перекат раз в ≈47 суток, а бэкап выбрасывается только на четвёртом.
# ОТКАТ: `CHATLOG_TICK_OFF=1` в окружении демона — ветка мертва целиком, диск не читается вовсе.
TICK_STATE_ENV = "CHATLOG_TICK_STATE"
DEFAULT_TICK_STATE = os.path.join(HERE, "tmp", "chatlog_tick", "state.json")
EVERY_MIN = int(os.environ.get("CHATLOG_TICK_EVERY_MIN", "360"))
OFF_ENV = "CHATLOG_TICK_OFF"


def due(prev, now, every_min=None):
    """→ bool. Пора ли идти на диск. ЧИСТАЯ: ни диска, ни часов, ни окружения — только счёт.

    Инвариант держит тест `CHATLOG_TICK_PURE`: решение о частоте обязано быть проверяемым без
    подмены времени и файлов, иначе регресс на нём не напишешь."""
    every = (EVERY_MIN if every_min is None else every_min) * 60
    if every <= 0:
        return True
    last = float((prev or {}).get("ran_at") or 0)
    if not last:
        return True          # штампа нет вовсе — захват ещё не звали ни разу, ждать нечего
    # Часы могли уехать назад (перевод времени, выход из сна) — отрицательная разница не имеет
    # права запереть захват навсегда. Копия замка `srv_delivery.due`, класс тот же.
    return (now - last) >= every or (now - last) < 0


def tick_state_path(path=None):
    return path or (os.getenv(TICK_STATE_ENV) or "").strip() or DEFAULT_TICK_STATE


def read_tick_state(path=None):
    try:
        with io.open(tick_state_path(path), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:
        return {}


def write_tick_state(state, path=None):
    path = tick_state_path(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
    os.replace(tmp, path)


LOCK_STALE_S = int(os.environ.get("CHATLOG_TICK_LOCK_STALE_S", "900"))


def lock_path(state_path=None):
    return tick_state_path(state_path) + ".lock"


def lock_take(path, now):
    """→ True, если замок взят ЭТИМ процессом. Единственная атомарная операция, одинаково
    работающая на Windows и POSIX, — `O_CREAT|O_EXCL`.

    ЗАЧЕМ ВЗАИМНОЕ ИСКЛЮЧЕНИЕ, ХОТЯ ЗАХВАТ ИДЕМПОТЕНТЕН. Дедуп (`store.existing_keys`) читает
    уже лежащее ДО записи — значит два захвата, читающие ОДНОВРЕМЕННО, видят одно и то же
    пустое место и оба дописывают. Это не гипотеза: замер 22.09 — два одновременных захвата
    положили в архив 1118 дублей в 16 файлах дня и побайтно испортили 2 файла (append двух
    процессов в один файл перемешал строки). Идемпотентность защищает от ПОВТОРА, а не от
    ОДНОВРЕМЕННОСТИ, и перепутать их стоило архиву целостности.

    Брошенный замок (процесс упал, не дойдя до `finally`) старше `LOCK_STALE_S` перехватывается.
    Без этой ветки одно падение остановило бы архив НАВСЕГДА и молча — ровно тем способом, от
    которого мы его сейчас и чиним."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, ("%d %d" % (os.getpid(), int(now))).encode("ascii"))
        finally:
            os.close(fd)
        return True
    except FileExistsError:
        # Возраст судим по времени ЗАЯВКИ, записанной В САМ замок, а не по mtime файла: у mtime
        # своя шкала (часы файловой системы), и сверка двух разных шкал даёт ложную «брошенность»
        # на ровном месте — то есть кражу ЖИВОГО замка, ради предотвращения которой он и заведён.
        age = None
        try:
            with io.open(path, encoding="ascii", errors="replace") as f:
                age = now - float(f.read().split()[-1])
        except (OSError, ValueError, IndexError):
            age = None
        if age is None:
            return False                  # замок нечитаем — считаем ЧУЖИМ ЖИВЫМ, это безопасная сторона
        if abs(age) < LOCK_STALE_S:
            return False                  # чужой ЖИВОЙ заход — уходим молча, это не ошибка
        lock_drop(path)                   # брошенный (или часы уехали на четверть часа) — берём
        return lock_take(path, now)
    except OSError:
        return False


def lock_drop(path):
    try:
        os.remove(path)
    except OSError:
        pass


def tick(now=None, every_min=None, state=None, state_path=None, runner=None, sources=None):
    """Один оборот часов захвата. → dict ТОЛЬКО С ЧИСЛАМИ.

    НИ ОДНОЙ СТРОКИ ПЕРЕПИСКИ НАРУЖУ НИ В ОДНОЙ ВЕТКЕ — включая ветку ошибки. Исключение
    разбора несёт в себе кусок разбираемой строки (`json.JSONDecodeError` цитирует документ,
    `UnicodeDecodeError` — байты), поэтому в штамп и в возврат идёт ТОЛЬКО ИМЯ КЛАССА
    исключения, а `str(e)` не берётся нигде. Держит тест `test_oshibka_ne_vynosit_tekst`.

    ШТАМП СТАВИТСЯ ДО ЗАХОДА, А НЕ ПОСЛЕ. Заход длится ~2.2с (замер 22.09: 43 104 сообщения за
    проход), и штамп после него оставлял бы эти 2.2с открытыми для второго захода. Замок закрыт
    дважды: заявкой на штамп и файлом-замком (`lock_take`)."""
    now = time.time() if now is None else now
    if os.environ.get(OFF_ENV):
        return {"ran": False, "why": u"выключено флагом " + OFF_ENV}
    prev = read_tick_state(state_path) if state is None else dict(state)
    if not due(prev, now, every_min):
        return {"ran": False, "why": u"рано", "ran_at": prev.get("ran_at")}
    lp = lock_path(state_path)
    if not lock_take(lp, now):
        return {"ran": False, "why": u"заход уже идёт"}
    try:
        fn = runner or (lambda: run(list(sources or SOURCES), verbose=False))
        new = dict(prev)
        new["ran_at"] = now                       # ЗАЯВКА: до захода, не после (см. шапку)
        new["runs"] = int(prev.get("runs") or 0) + 1
        write_tick_state(new, state_path)
        try:
            st = fn()
        except Exception as e:
            new["last_error_at"] = now
            new["last_error"] = type(e).__name__  # ИМЯ КЛАССА, не текст: см. шапку функции
            new["errors"] = int(prev.get("errors") or 0) + 1
            write_tick_state(new, state_path)
            return {"ran": True, "ok": False, "error": type(e).__name__}
        written = int((st or {}).get("written") or 0)
        new["written"] = written
        new["total_written"] = int(prev.get("total_written") or 0) + written
        new["last_error"] = None
        write_tick_state(new, state_path)
        return {"ran": True, "ok": True, "written": written,
                "dup": int((st or {}).get("dup") or 0),
                "days": int((st or {}).get("days") or 0)}
    finally:
        lock_drop(lp)


def _main(argv):
    ap = argparse.ArgumentParser(
        description="Захват в chatlog/ того, что уже лежит на диске. В Telegram не ходит.")
    ap.add_argument("--source", action="append", choices=list(SOURCES) + ["all"],
                    help="источник; можно повторять; по умолчанию all")
    ap.add_argument("--dry", action="store_true", help="прочитать и посчитать, НЕ записывая")
    ap.add_argument("--tick", action="store_true",
                    help="оборот часов захвата: идти на диск решает штамп частоты")
    ap.add_argument("--now", action="store_true",
                    help="захват немедленно, штамп частоты игнорируем")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    src = a.source or ["all"]
    sources = list(SOURCES) if "all" in src else [s for s in SOURCES if s in src]
    if a.tick or a.now:
        res = tick(every_min=(0 if a.now else None), sources=sources)
    else:
        res = run(sources, verbose=not a.quiet, dry=a.dry)
    print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
