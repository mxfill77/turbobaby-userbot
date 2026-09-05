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


def _main(argv):
    ap = argparse.ArgumentParser(
        description="Захват в chatlog/ того, что уже лежит на диске. В Telegram не ходит.")
    ap.add_argument("--source", action="append", choices=list(SOURCES) + ["all"],
                    help="источник; можно повторять; по умолчанию all")
    ap.add_argument("--dry", action="store_true", help="прочитать и посчитать, НЕ записывая")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    src = a.source or ["all"]
    sources = list(SOURCES) if "all" in src else [s for s in SOURCES if s in src]
    res = run(sources, verbose=not a.quiet, dry=a.dry)
    print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
