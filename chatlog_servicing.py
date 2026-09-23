# -*- coding: utf-8 -*-
"""
chatlog_servicing.py — ФОРУМ ОБСЛУЖИВАНИЯ в хранилище переписки, ВКЛЮЧАЯ СНИМКИ (23.09.2026).

ЗАЧЕМ. Задание ОБСЛУЖФОРУМЖУРНАЛФОТО2309. До этого дня форума Обслуживания (-1002751134848,
тема под каждый байк, бот там Splinter) в `chatlog/_manifest.json` не было вовсе: `chatlog_ingest`
питается только тем, что боевые процессы УЖЕ положили на диск (журнал userbot, журнал доставки
Штаба, две разовые выгрузки), а форум Обслуживания на диск не пишет никто. Фото архив не хранил
нигде — только маркер `[PHOTO]` у выгрузок. Здесь и то и другое.

ЧТО ЭТОТ МОДУЛЬ ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ:
  • СЕТИ ЗДЕСЬ НЕТ и telethon не импортируется. Сообщения приходят уже разобранными «фактами»
    (простые dict), загрузку снимка делает вызывающий (`chatlog_servicing_fetch`) переданной
    функцией. Поэтому весь регресс гоняется без единого байта наружу — образец `photo_inbox`;
  • НАРУЖУ НЕ ПИШЕТ НИЧЕГО: ни в Telegram, ни в мозг. Строка дня — в `chatlog/` (вне git, как весь
    архив), снимок — в `docs/obsluzhivanie-snimki/` (под git-путём, НЕ в tmp: задание велит так);
  • НИЧЕГО НЕ УДАЛЯЕТ И НЕ ЗАТИРАЕТ: строки только дописываются (`chatlog_store.Writer`), снимок,
    уже лежащий на диске, второй раз не качается и не перезаписывается.

ОКНО — СВЕЖЕЕ, А НЕ ВСЯ ИСТОРИЯ: последние `WINDOW_DAYS` суток ИЛИ `WINDOW_LIMIT` сообщений —
что наступит раньше. Предсмертный взгляд задания назвал цену ошибки прямо: взяться качать всю
историю и все медиа — не влезть в таймаут захода. Сверх окна загрузкам поставлен свой бюджет
времени (`BUDGET_S`): снимок, до которого очередь не дошла, не пропадает молча, а получает исход
НЕ СКАЧАН с причиной словами.

ТРИ ИСХОДА ВЛОЖЕНИЯ (слова — ОДНИ с `photo_inbox`, второго мнения нет):
  СОХРАНЁН    — байты на диске, сигнатура опознана как изображение; в `media` строки лежит
                путь к файлу от корня репозитория;
  НЕ СКАЧАН   — снимок БЫЛ, файла нет: Telegram не отдал, бюджет кончился, байты нулевые,
                сигнатура чужая. В `media` запись с исходом и ПРИЧИНОЙ СЛОВАМИ, файла нет;
  НЕ КАРТИНКА — вложение есть, но это не снимок (видео, голос, файл, стикер). Записи-снимка
                НЕТ и файла нет; остаётся маркер вида вложения — тот же, что ставят другие
                источники архива (`chatlog_ingest._MEDIA_MARK`).
Сообщение без вложения: `media` пустой, файла нет.

ПОВТОРНЫЙ ПРОГОН НЕ УДВАИВАЕТ АРХИВ: ключ дедупа — `m<номер сообщения>` (`chatlog_store.msg_key`),
и сообщение, чей ключ уже лежит в файле дня, не только не пишется второй раз, но и снимок его НЕ
КАЧАЕТСЯ повторно — лишние байты и лишнее время захода.

ЛЮДЕЙ В АРХИВЕ НЕТ — так же, как во всём `chatlog`: автор — `who_ref(str(sender_id))`. Форма сырья
выбрана не случайно: ровно её пишет `export_all.py` (`'from_id': str(msg.sender_id)`), поэтому
механик из форума Обслуживания и он же в зарплатной выгрузке — ОДИН псевдоним. Текст чистится
`chatlog_store.scrub` (ники, почта, телефоны, пароли и ключи).
"""

import os
import io
import json
import time
import datetime

import chatlog_store as store
import photo_inbox

HERE = os.path.dirname(os.path.abspath(__file__))

CHAT_ID = -1002751134848
GROUP_SLUG = "servicing"
TITLE = "Обслуживание (форум, тема под каждый байк; бот Splinter)"
KIND = "forum"
SOURCE = "telegram:userbot (свежее окно, chatlog_servicing_fetch)"
SRC_TAG = "telegram:servicing"

WINDOW_DAYS = 7
WINDOW_LIMIT = 300
# Бюджет времени ЗАГРУЗОК одного захода. 600 с — четверть потолка захода демона (2700 с): окно
# в 300 сообщений при типичной доле снимков укладывается с запасом, а зависший Telegram не съест
# весь заход — остаток получит честное НЕ СКАЧАН «бюджет исчерпан».
BUDGET_S = 600

# ЯВНО НАЗВАННОЕ МЕСТО СНИМКОВ — под git-путём (проверено `git check-ignore`: не игнорируется),
# НЕ в `tmp/` (он в .gitignore) и НЕ в `chatlog/` (он тоже вне git). Разбивка по месяцу Пхукета:
# плоская папка на год вперёд дала бы тысячи файлов в одном каталоге.
PHOTO_DIR = os.path.join(HERE, "docs", "obsluzhivanie-snimki")
TOPICS_MAP = os.path.join(HERE, "topics_map.json")

GENERAL_ID = 1
GENERAL_NAME = "General"

SAVED = photo_inbox.SAVED
NOT_DOWNLOADED = photo_inbox.NOT_DOWNLOADED
NOT_A_PHOTO = photo_inbox.NOT_A_PHOTO

PHOTO = "photo"
NOT_PHOTO_NOTE = "не картинка — файл не качался"

# Служебные действия форума, из которых берём ИМЯ темы (сами они перепиской не являются).
ACTION_TOPIC_CREATE = "MessageActionTopicCreate"
ACTION_TOPIC_EDIT = "MessageActionTopicEdit"


# ─────────────────────────── темы ───────────────────────────

def load_topics(path=None):
    """topics_map.json → (dict[int, str], None) либо (None, «почему не прочитан»).

    Третий исход обязателен: «карта не прочиталась» ≠ «тем нет». Не прочиталась — темы получат
    имена из служебных сообщений окна или честное «тема N», и захват об этом скажет числом."""
    p = path or TOPICS_MAP
    try:
        with io.open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (IOError, OSError, ValueError) as e:
        return None, type(e).__name__
    if not isinstance(raw, dict):
        return None, "не словарь"
    out = {}
    for k, v in raw.items():
        ks = str(k).strip()
        if ks.isdigit():              # чужой ключ карты — не тема; остальные темы не теряем
            out[int(ks)] = str(v)
    return out, None


def topic_id_of(fact):
    """Номер темы сообщения форума. Образец — `recon_servicing.topic_of`: у ответа внутри темы
    номер темы в `top_id`, у сообщения прямо в тему — в `reply_msg_id`. Без признака `forum_topic`
    сообщение живёт в General (номер 1)."""
    if not fact.get("forum_topic"):
        return GENERAL_ID
    tid = fact.get("top_id")
    if tid is None:
        tid = fact.get("reply_msg_id")
    if tid is None:
        return GENERAL_ID
    return int(tid)


def reply_of(fact):
    """На какое сообщение это ответ (или None). Сообщение прямо в тему ответом НЕ является:
    его `reply_msg_id` — номер темы, а не собеседника."""
    if fact.get("forum_topic") and fact.get("top_id") is None:
        return None
    return fact.get("reply_msg_id")


def topic_names_from_window(facts):
    """Имена тем из служебных сообщений окна: создание темы (номер темы = номер сообщения) и
    переименование (номер — тема, в которой оно лежит). Переименование свежее создания."""
    created, edited = {}, {}
    for f in facts:
        title = f.get("action_title")
        if not title:
            continue
        if f.get("action") == ACTION_TOPIC_CREATE:
            created[int(f["mid"])] = str(title)
        elif f.get("action") == ACTION_TOPIC_EDIT:
            edited[topic_id_of(f)] = str(title)
    return created, edited


def topic_name(tid, topics_map, created, edited):
    """Имя темы: переименование в окне → карта `topics_map.json` → создание в окне → «тема N».
    General — всегда General."""
    if tid == GENERAL_ID:
        return GENERAL_NAME
    for src in (edited, topics_map, created):
        if src and tid in src:
            return src[tid]
    return "тема %d" % tid


# ─────────────────────────── окно ───────────────────────────

def in_window(when, now, days=WINDOW_DAYS):
    """Сообщение в свежем окне? ЧИСТАЯ функция. Время без пояса считаем UTC (Telethon отдаёт UTC)."""
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return when >= now - datetime.timedelta(days=days)


# ─────────────────────────── вложения ───────────────────────────

def classify_media(has_media, has_photo=False, mime=None, is_sticker=False, kind_hint=None):
    """Вид вложения → PHOTO | другой вид словом | None (вложения нет).

    Снимок — это фото Telegram ИЛИ файл с `image/*` внутри (сетку «как файл» терять глупо —
    та же логика, что `photo_inbox_fetch.is_image_message`). Стикер — `image/webp`, но снимком
    не является: это не фотография чего-либо, а картинка из набора."""
    if not has_media:
        return None
    if is_sticker:
        return "sticker"
    if has_photo:
        return PHOTO
    if mime is not None and str(mime).startswith("image/"):
        return PHOTO
    if kind_hint:
        return str(kind_hint)
    return "other"


def photo_filename(when, mid, topic_id, ext="jpg"):
    """Имя снимка: дата и время ПО ПХУКЕТУ, тема и номер сообщения. По имени снимок находится
    без архива: когда, в какой теме-байке и каким сообщением пришёл (образец `photo_inbox.photo_name`)."""
    t = photo_inbox.to_phuket(when)
    return "%s_%s_t%d_msg%d.%s" % (t.strftime("%Y-%m-%d"), t.strftime("%H-%M-%S"),
                                   int(topic_id), int(mid), ext)


def photo_path(when, mid, topic_id, photo_dir=None):
    t = photo_inbox.to_phuket(when)
    return os.path.join(photo_dir or PHOTO_DIR, t.strftime("%Y-%m"),
                        photo_filename(when, mid, topic_id))


def rel(path):
    """Путь от корня репозитория с прямыми слэшами — таким он и ложится в строку архива: ссылка
    обязана открываться одинаково с любой полосы. Вне репозитория (тесты) — абсолютный."""
    try:
        r = os.path.relpath(path, HERE)
    except ValueError:
        return path.replace("\\", "/")
    if r.startswith(".."):
        return path.replace("\\", "/")
    return r.replace("\\", "/")


def existing_photo(path):
    """Снимок уже лежит по этому имени (и какой он). → (путь, байты) либо (None, 0).

    Нужен на обрыв посреди захода: файл скачан, а строка не легла. Повторный прогон не качает его
    второй раз и не затирает — берёт готовый. Файл с похожим именем, но другим расширением
    (`_fix_extension` мог переименовать под реальную сигнатуру) — тоже наш."""
    base = os.path.splitext(path)[0]
    d = os.path.dirname(path)
    if not os.path.isdir(d):
        return None, 0
    stem = os.path.basename(base)
    for fn in sorted(os.listdir(d)):
        if os.path.splitext(fn)[0] == stem:
            p = os.path.join(d, fn)
            return p, os.path.getsize(p)
    return None, 0


def photo_outcome(downloaded_path, error=None):
    """Исход загрузки → запись `media`. Ни одна ветка не молчит: сорванная загрузка даёт НЕ СКАЧАН
    с причиной СЛОВАМИ — без неё потеря выглядела бы как отсутствие снимка."""
    size = 0
    if downloaded_path:
        try:
            size = os.path.getsize(downloaded_path)
        except OSError:
            size = -1                            # путь назван, а файла нет — это не «ноль байт»
    reason = None
    sniffed = None
    if error:
        reason = str(error)
    elif not downloaded_path:
        reason = "загрузка вернула пустой путь"
    elif size < 0:
        reason = "загрузка назвала путь, а файла на диске нет"
    elif size == 0:
        reason = "файл нулевой длины"
    else:
        sniffed = photo_inbox.sniff_kind(downloaded_path)
        if sniffed is None:
            reason = "сигнатура файла не опознана как изображение"
    if reason is None:
        return {"kind": PHOTO, "outcome": SAVED, "file": rel(downloaded_path),
                "bytes": size, "type": sniffed}
    return {"kind": PHOTO, "outcome": NOT_DOWNLOADED, "reason": reason, "file": None,
            "bytes": max(size, 0)}


def download_error_words(e):
    """Исключение загрузки → причина словами. Только ИМЯ класса и короткий хвост: текст ошибки
    Telegram несёт имя запроса, а не переписку, но длину держим, чтобы строка архива не пухла."""
    tail = str(e).strip().replace("\n", " ")[:160]
    if tail:
        return "Telegram не отдал файл: %s: %s" % (type(e).__name__, tail)
    return "Telegram не отдал файл: %s" % type(e).__name__


# ─────────────────────────── строка ───────────────────────────

def build_line(fact, tid, tname, media):
    """Факт сообщения → строка дня `chatlog_store.line`. Имени, ника и телефона сюда передать
    нельзя физически — `line()` их не принимает; автор — только псевдоним."""
    extra = {"src": SRC_TAG, "dir": ("out" if fact.get("out") else "in"),
             "reply_to": reply_of(fact)}
    if fact.get("bot") is not None:
        extra["bot"] = bool(fact.get("bot"))
    if fact.get("album") is not None:
        extra["album"] = str(fact.get("album"))
    when = fact.get("when")
    ts = when.isoformat() if when is not None else None
    return store.line(ts=ts, ref=store.who_ref(fact.get("sender_raw")), text=fact.get("text"),
                      mid=fact.get("mid"), topic_id=tid, topic_name=tname, media=media,
                      extra=extra)


def _day_file(day):
    """Путь файла дня БЕЗ заведения каталогов: сухой прогон не смеет создать ничего."""
    return os.path.join(store.root(), GROUP_SLUG, day[:4], day[5:7], day + ".jsonl")


# ─────────────────────────── захват ───────────────────────────

async def capture(facts, download, topics_map=None, photo_dir=None, budget_s=BUDGET_S,
                  clock=time.monotonic, dry=False):
    """Факты окна → строки архива + снимки на диске. → dict ТОЛЬКО С ЧИСЛАМИ.

    `download(fact, path)` — async, кладёт снимок сообщения по пути и возвращает фактический путь
    (или пустоту). В регрессе — подделка без сети; в бою — `download_media` по копии сессии.

    Ни одной строки переписки в возврате нет ни в одной ветке — только счётчики: возврат уходит
    в stdout захода, а stdout — в отчёт очереди, то есть наружу."""
    facts = sorted(facts, key=lambda f: (f.get("when") is None, f.get("when"), f.get("mid")))
    created, edited = topic_names_from_window(facts)
    w = store.Writer()
    w.note_group(GROUP_SLUG, title=TITLE, group_id=CHAT_ID, kind=KIND, source=SOURCE)
    n = {"facts": len(facts), "service": 0, "messages": 0, "no_media": 0,
         "photo": 0, "saved": 0, "saved_bytes": 0, "not_downloaded": 0, "not_a_photo": 0,
         "photo_reused": 0, "photo_already_archived": 0, "photo_would_download": 0,
         "topics_from_map": 0, "topics_fallback": 0}
    topics_seen = set()
    keys_by_day = {}
    reasons = {}                         # причина НЕ СКАЧАН (голова до двоеточия) → сколько раз
    t0 = clock()
    for f in facts:
        if f.get("action"):
            n["service"] += 1            # создание/переименование темы, закреп — не переписка
            continue
        n["messages"] += 1
        tid = topic_id_of(f)
        tname = topic_name(tid, topics_map, created, edited)
        if tid not in topics_seen:
            topics_seen.add(tid)
            if topics_map is not None and tid in topics_map:
                n["topics_from_map"] += 1
            elif tname.startswith("тема "):
                n["topics_fallback"] += 1
        kind = f.get("media")
        media = []
        if kind == PHOTO:
            n["photo"] += 1
            when = f.get("when")
            day = store.day_of(when.isoformat() if when is not None else None)
            if day and day not in keys_by_day:
                keys_by_day[day] = store.existing_keys(_day_file(day))
            archived = bool(day) and store.msg_key(f.get("mid"), None, None, None) in keys_by_day[day]
            if archived:
                # Сообщение уже в архиве: строка всё равно уйдёт в Writer и будет отсечена
                # дедупом, а снимок второй раз НЕ качается.
                n["photo_already_archived"] += 1
            elif dry:
                n["photo_would_download"] += 1   # сухо: ни байта на диск, ни запроса в сеть
            else:
                path = photo_path(when, f.get("mid"), tid, photo_dir)
                have, _size = existing_photo(path)
                if have:
                    n["photo_reused"] += 1
                    entry = photo_outcome(have)
                elif budget_s is not None and clock() - t0 > budget_s:
                    entry = photo_outcome(None, "бюджет времени захода (%d с) исчерпан — "
                                                "до снимка очередь не дошла" % budget_s)
                else:
                    got, err = None, None
                    try:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        got = await download(f, path)
                    except Exception as e:        # сеть/диск: исход НЕ СКАЧАН с причиной, не молчание
                        err = download_error_words(e)
                    entry = photo_outcome(got, err)
                media = [entry]
                if entry["outcome"] == SAVED:
                    n["saved"] += 1
                    n["saved_bytes"] += entry["bytes"]
                else:
                    n["not_downloaded"] += 1
                    why = ":".join(entry["reason"].split(":")[:2])[:80]   # до имени класса
                    reasons[why] = reasons.get(why, 0) + 1
        elif kind:
            n["not_a_photo"] += 1
            media = [{"kind": kind, "note": NOT_PHOTO_NOTE}]
        else:
            n["no_media"] += 1
        w.add(GROUP_SLUG, build_line(f, tid, tname, media))
    n["topics"] = len(topics_seen)
    n["not_downloaded_reasons"] = reasons
    n["skipped_no_day"] = w.skipped_no_day
    if dry:
        n["dry"] = True
        n["would_write"] = sum(len(v) for v in w.buf.values())
        return n
    st = w.commit()
    n["written"] = st["written"]
    n["dup"] = st["dup"]
    n["days"] = st["days"]
    n["index_rows"] = st["index_rows"]
    return n
