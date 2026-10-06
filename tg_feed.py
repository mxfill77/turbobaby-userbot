# -*- coding: utf-8 -*-
"""
tg_feed.py — ЛЕНТА ЛИЧНЫХ СООБЩЕНИЙ КЛИЕНТОВ Telegram для «Агентов» (03.10.2026).

Основание — слово владельца 03.10 13:19 («В агентв из тг тоже должно так же идти … всё как с
ватсапом надо») и его «да» 13:59 на правку userbot; разведка TGAGENTMAP0310, путь (б). Агент WA
берёт вход из очереди, где у каждого сообщения есть id, время и вид. У Telegram такого хранилища не
было: в `userbot.log` нет id сообщения и нет исходящих, а `moderation_ipc.drafts` получает ряд только
при собранном черновике (338 из ≈439 клиентских). Здесь — ровно ряд очереди: на каждое входящее и
исходящее ЛИЧНОЕ сообщение клиента одна JSON-строка.

КОНТРАКТ РЯДА (один для ПК и сервера; ключ — (chat_id, msg_id, dir)):
    chat_id    int          id личного чата (= id клиента)
    msg_id     int          id сообщения в этом чате
    dir        "in"|"out"   входящее от клиента | исходящее нашего аккаунта
    ts         int          время СООБЩЕНИЯ (не записи), секунды UTC
    kind       str          text | photo | voice | video | sticker | file | location | other
    text       str          текст; у медиа — подпись; нет подписи — пусто
    reply_to   int|null     id сообщения, на которое ответ
    sender_id  int|null     у входящего — клиент, у исходящего — наш аккаунт

ЧЕГО ЗДЕСЬ НЕТ ПО ПОСТРОЕНИЮ: сети, Telegram, SQLite и чтения ленты. Модуль собирает ряд из уже
полученного объекта сообщения и ДОПИСЫВАЕТ строку целиком: один `os.write` до последнего байта,
затем fsync. Хвост, оборванный прошлой записью, новую строку не портит: перед ней ставится перевод
строки. Дубли (Telegram может прислать апдейт повторно после переподключения) снимает читатель по
ключу, писатель ленту не читает. Правки сообщений (`MessageEdited`) в ленту не идут.

Решение «клиент ли это» здесь НЕ принимается. Его принимает вызывающий (`userbot_listen._feed`)
функциями suggest — теми же, что отсеивают команду и партнёров до черновика.

Путь — `tg_feed/tg_feed.jsonl` в корне репозитория, ВНЕ git: маска `*.jsonl` и отдельная строка
`tg_feed/` в .gitignore (как у `chatlog/`: тексты клиентов не уедут в историю, даже если маска
исчезнет). Откат без правки кода — `TG_FEED_OFF=1` в окружении userbot: ряд не пишется, обработчики
работают как до 03.10.
"""

import json
import os
from datetime import timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FEED_DIR = os.path.join(BASE_DIR, "tg_feed")
FEED_FILE = os.path.join(FEED_DIR, "tg_feed.jsonl")
OFF_ENV = "TG_FEED_OFF"

DIR_IN, DIR_OUT = "in", "out"
KINDS = ("text", "photo", "voice", "video", "sticker", "file", "location", "other")
FIELDS = ("chat_id", "msg_id", "dir", "ts", "kind", "text", "reply_to", "sender_id")

# Вид медиа узнаём по имени TL-класса, а не по isinstance: модуль не тянет Telethon и проверяется
# без него. Имена — из живой схемы Telethon 1.43 (telethon.tl.types).
_MEDIA_NONE = ("", "MessageMediaEmpty", "MessageMediaWebPage")   # превью ссылки — это текст
_MEDIA_PHOTO = ("MessageMediaPhoto",)
_MEDIA_LOCATION = ("MessageMediaGeo", "MessageMediaGeoLive", "MessageMediaVenue")
_MEDIA_DOCUMENT = ("MessageMediaDocument",)

# Символы, на которых `str.splitlines()` рвёт строку, хотя json.dumps(ensure_ascii=False) их не
# экранирует. Читатель с splitlines получил бы из одного ряда два битых — экранируем сами.
_LINE_BREAKERS = (" ", " ", "\x85", "\x1c", "\x1d", "\x1e", "\x0b", "\x0c")


def enabled():
    """Лента включена? Выключатель отката — `TG_FEED_OFF` (1/true/yes/on/да). Читается на каждом
    ряде: смена значения не требует ничего, кроме нового окружения процесса."""
    return str(os.getenv(OFF_ENV, "") or "").strip().lower() not in ("1", "true", "yes", "on", "да")


def _text_of(msg):
    """Текст сообщения или подпись медиа (TL-поле `message`); не строка → пусто."""
    v = getattr(msg, "message", None)
    return v if isinstance(v, str) else ""


def _has(msg, prop):
    try:
        return getattr(msg, prop, None) is not None
    except Exception:                       # noqa: BLE001 — кривое медиа не роняет ряд, вид = file
        return False


def kind_of(msg):
    """Вид сообщения по контракту. Порядок важен: стикер раньше видео (у видеостикера есть атрибут
    видео), голос раньше файла; кружок и gif — видео; аудио и любой иной документ — файл."""
    media = getattr(msg, "media", None)
    name = type(media).__name__ if media is not None else ""
    if name in _MEDIA_NONE:
        return "text" if _text_of(msg) else "other"
    if name in _MEDIA_PHOTO:
        return "photo"
    if name in _MEDIA_LOCATION:
        return "location"
    if name in _MEDIA_DOCUMENT:
        if _has(msg, "sticker"):
            return "sticker"
        if _has(msg, "voice"):
            return "voice"
        if _has(msg, "video") or _has(msg, "video_note") or _has(msg, "gif"):
            return "video"
        return "file" if _has(msg, "document") else "other"
    return "other"


def _int_or_none(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _reply_to(msg):
    try:
        return _int_or_none(getattr(msg, "reply_to_msg_id", None))
    except Exception:                       # noqa: BLE001 — ответ на историю и т.п.: связи нет
        return None


def _ts(date):
    """Время сообщения в секундах UTC. Telethon отдаёт aware-UTC; наивное считаем UTC."""
    if date is None:
        raise ValueError("у сообщения нет даты")
    if isinstance(date, (int, float)) and not isinstance(date, bool):
        return int(date)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return int(date.timestamp())


def row(msg, chat_id, direction, sender_id):
    """Ряд по контракту из объекта сообщения Telethon. Сети не зовёт. Без id чата/сообщения или
    даты ряда нет — ValueError (вызывающий пишет это в журнал, обработчик не падает)."""
    if direction not in (DIR_IN, DIR_OUT):
        raise ValueError(f"направление {direction!r} вне контракта")
    cid = _int_or_none(chat_id)
    mid = _int_or_none(getattr(msg, "id", None))
    if cid is None or mid is None:
        raise ValueError(f"нет ключа ряда: chat_id={chat_id!r} msg_id={getattr(msg, 'id', None)!r}")
    return {
        "chat_id": cid,
        "msg_id": mid,
        "dir": direction,
        "ts": _ts(getattr(msg, "date", None)),
        "kind": kind_of(msg),
        "text": _text_of(msg),
        "reply_to": _reply_to(msg),
        "sender_id": _int_or_none(sender_id),
    }


def line(rec):
    """Ряд → одна строка JSON с завершающим \\n. Переводы строки внутри текста экранированы
    json.dumps, прочие разрывы строки — здесь (см. _LINE_BREAKERS)."""
    s = json.dumps({k: rec.get(k) for k in FIELDS}, ensure_ascii=False, separators=(",", ":"))
    for ch in _LINE_BREAKERS:
        if ch in s:
            s = s.replace(ch, "\\u%04x" % ord(ch))
    return s + "\n"


def append(rec, path=None):
    """Дописать ряд в ленту ЦЕЛИКОМ и с fsync. → число записанных байт. Сбой — исключение (его
    ловит вызывающий). Хвост файла без \\n (оборванная прошлая запись) закрывается переводом строки
    ДО нашей строки, чтобы два ряда не склеились в один нечитаемый."""
    path = path or FEED_FILE
    data = line(rec).encode("utf-8")
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        size = os.fstat(fd).st_size
        if size:
            os.lseek(fd, size - 1, os.SEEK_SET)
            if os.read(fd, 1) != b"\n":
                data = b"\n" + data
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            if not n:
                raise OSError("лента: запись не продвинулась (0 байт)")
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return len(data)
