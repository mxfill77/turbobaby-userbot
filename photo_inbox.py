# -*- coding: utf-8 -*-
"""
photo_inbox.py — ПАПКА ВХОДЯЩИХ СНИМКОВ: снимок с телефона владельца ложится на диск (полоса ПК).

ЗАЧЕМ. У Штаба нет входящего ящика: картинку, присланную владельцем с телефона в группу, он
увидеть не может НИКАК — она живёт внутри Telegram и до диска не доезжает. Задание 60-a назвало
место потери числом (`download_media` — 0 совпадений на пять файлов клиентского пути), а этот
модуль даёт Штабу способ увидеть снимок: файл в НАЗВАННОЙ папке репозитория плюс строка в
индексе рядом. Дальше Штаб открывает файл сам на ближайшем будильнике.

ГРАНИЦЫ (они же безопасность):
  • модуль НИКОМУ не пишет наружу: ни в Telegram, ни в CRM, ни в мозг. Он кладёт файл и
    дописывает строку — всё;
  • сети здесь нет ВООБЩЕ и telethon не импортируется: загрузку делает вызывающий (`photo_inbox_fetch`),
    сюда приходит уже готовый исход. Поэтому отрицательные тесты наружу не ходят ни одним байтом;
  • ничего не удаляется и не перезаписывается: индекс открывается только на ДОПИСЫВАНИЕ ("a"),
    существующий файл снимка не затирается;
  • снимки берутся ТОЛЬКО из сообщений владельца в названных группах — отбор делает вызывающий,
    и это записано в его коде, а не в обещании.

ПОЧЕМУ ПАПКА НЕ В `tmp/`. Соседний `trainer_photo.PHOTO_DIR` = `tmp/trainer_photos/` — и `tmp/`
стои́т в `.gitignore` (строка 81). Файл оттуда на облако не уезжает, а значит Штаб его не видит —
то есть цель задания не достигается вовсе. Здесь место названо ЯВНО и лежит под git:
`docs/vhodyashchie-snimki/` (проверено `git check-ignore` — не игнорируется).

ТРИ ИСХОДА, И ОНИ НЕ СЛИВАЮТСЯ:

  СОХРАНЁН    — байты на диске, сигнатура опознана как изображение. Строка в индексе есть.
  НЕ СКАЧАН   — вложение было, а файла нет: Telegram не отдал, диск не принял, байты нулевые,
                сигнатура не опознана. Строка в индексе ЕСТЬ, и в ней причина СЛОВАМИ.
  НЕ КАРТИНКА — во вложение брать было нечего. Строки в индексе НЕТ ВОВСЕ.

Третий исход отличается от второго по существу, и разница нужна именно Штабу: «НЕ СКАЧАН» — это
новость «снимок БЫЛ и потерян», «НЕ КАРТИНКА» — это обычное текстовое сообщение, о котором
новости нет. Молчаливого пропуска нет: сорванная загрузка обязана оставить след, иначе Штаб не
узнает, что снимок вообще присылали.
"""

import os
import re
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- ЯВНО НАЗВАННОЕ МЕСТО ----------------------------------------------------
# Одно на весь модуль, внутри рабочей папки репозитория и ПОД git — иначе Штаб с облака не
# прочитает. Ничего отсюда не удаляем: снимок — доказательство, а уборки за собой рамка не велит.
INBOX_DIR = os.path.join(BASE_DIR, "docs", "vhodyashchie-snimki")
INDEX_PATH = os.path.join(INBOX_DIR, "index.md")

# Пхукет — UTC+7 круглый год (перевода часов в Таиланде нет), поэтому фиксированное смещение
# здесь не упрощение, а точное описание. Telethon отдаёт время в UTC — переводим ОДИН раз, в
# одном месте: два разных перевода дали бы две «правды» об одной минуте.
PHUKET = datetime.timezone(datetime.timedelta(hours=7))

# --- ИСХОДЫ ------------------------------------------------------------------
SAVED = "СОХРАНЁН"
NOT_DOWNLOADED = "НЕ СКАЧАН"
NOT_A_PHOTO = "НЕ КАРТИНКА"

# Шапка индекса. Пишется РОВНО ОДИН раз — при рождении файла; дальше только дописывание строк.
INDEX_HEADER = """# Входящие снимки владельца — индекс

Строка на снимок. Дописывается, НЕ перезаписывается: соседняя строка потеряться не может.
Снимок с исходом НЕ СКАЧАН строку тоже даёт — с причиной словами; сообщение без вложения строки
не даёт вовсе. Повторная доставка того же номера сообщения второй строки не рождает.

Подпись записана ДОСЛОВНО, но переносы и разделители экранированы, чтобы снимок занимал ровно
одну строку: `\\` → `\\\\`, перевод строки → `\\n`, возврат каретки → `\\r`, табуляция → `\\t`,
`|` → `\\|`. Обратное преобразование — `photo_inbox.unescape(…)`, оно дословное.

| время (Пхукет) | чат | № сообщения | кто прислал | подпись | файл | байт | исход |
|---|---|---|---|---|---|---|---|"""


# ------------------------------- экранирование --------------------------------
# Подпись обязана быть ДОСЛОВНОЙ и при этом не разорвать строку индекса. Оба требования
# выполнимы только обратимым экранированием: «выкинуть переносы» дословность ломает молча.

_ESCAPES = (("\\", "\\\\"), ("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t"), ("|", "\\|"))
_UNESCAPE_RE = re.compile(r"\\(\\|n|r|t|\|)")
_UNESCAPE_MAP = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t", "|": "|"}


def escape(text):
    """Текст → одна строка индекса, обратимо. Порядок замен важен: обратный слэш ПЕРВЫМ,
    иначе экранирующий слэш сам попадёт под замену и обратное чтение соврёт."""
    s = "" if text is None else str(text)
    for src, dst in _ESCAPES:
        s = s.replace(src, dst)
    return s


def unescape(text):
    """Строка индекса → исходный текст ДОСЛОВНО. Обратна `escape` на любом входе (тест)."""
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPE_MAP[m.group(1)], "" if text is None else str(text))


# ------------------------------- имя файла ------------------------------------

def _ext_of(kind_or_name, default="jpg"):
    """Расширение по типу/имени, приведённое к короткому виду. Точек и путей не пропускает:
    имя файла собирает МАШИНА, и чужая строка не смеет стать путём."""
    s = (kind_or_name or "").strip().lower()
    s = os.path.basename(s)
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    s = re.sub(r"[^a-z0-9]", "", s)
    if s == "jpeg":
        s = "jpg"
    return s or default


def photo_name(when, msg_id, kind="jpg"):
    """Имя файла снимка: ДАТА, ВРЕМЯ и НОМЕР СООБЩЕНИЯ — всё трое в имени.

    Зачем троица: по имени снимок находится БЕЗ индекса. Индекс — удобство, а не единственный
    носитель смысла; потеряется он — файл всё равно скажет, когда и каким сообщением пришёл."""
    t = to_phuket(when)
    return "%s_%s_msg%d.%s" % (t.strftime("%Y-%m-%d"), t.strftime("%H-%M-%S"),
                               int(msg_id), _ext_of(kind))


def to_phuket(when):
    """Любое время → время Пхукета. Наивное время считаем UTC (Telethon отдаёт UTC), а не
    местным: «наивное = местное» на машине в другом поясе молча сдвинуло бы весь индекс."""
    if when is None:
        return datetime.datetime.now(datetime.timezone.utc).astimezone(PHUKET)
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(PHUKET)


# ------------------------------- индекс ---------------------------------------

def _key(chat_id, msg_id):
    """Ключ повтора — ПАРА (чат, номер сообщения). Номер сообщения уникален внутри чата, но не
    между чатами: ключ из одного номера склеил бы два разных снимка в один."""
    return "%s/%s" % (int(chat_id), int(msg_id))


def indexed_keys(index_path=None):
    """Ключи, уже стоящие в индексе. Индекса нет → пустое множество (а не ошибка): первая
    доставка обязана работать на голом месте."""
    path = index_path or INDEX_PATH
    keys = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                if not ln.startswith("| "):
                    continue
                cols = ln.split(" | ")
                if len(cols) < 4:
                    continue
                chat_id, msg_id = _parse_chat_msg(cols)
                if chat_id is not None:
                    keys.add(_key(chat_id, msg_id))
    except FileNotFoundError:
        return keys
    except OSError:
        return keys
    return keys


_CHAT_ID_RE = re.compile(r"\((-?\d+)\)\s*$")


def _parse_chat_msg(cols):
    """Из колонок строки индекса — (chat_id, msg_id). Не распарсилось → (None, None): чужую
    строку молча считать своей нельзя, но и падать на ней тоже нельзя."""
    m = _CHAT_ID_RE.search(cols[1].strip())
    if not m:
        return (None, None)
    raw = cols[2].strip()
    if not raw.lstrip("-").isdigit():
        return (None, None)
    return (int(m.group(1)), int(raw))


def already_indexed(chat_id, msg_id, index_path=None):
    """Этот снимок уже в индексе? Замок против ВТОРОЙ строки на повторную доставку одного и
    того же сообщения (Telegram переприсылает апдейты, а заход фетча можно запустить дважды)."""
    return _key(chat_id, msg_id) in indexed_keys(index_path)


def chat_cell(title, chat_id):
    """Колонка чата: имя плюс id в скобках. Id нужен машине (ключ повтора), имя — человеку."""
    return "%s (%s)" % ((title or "?").strip().replace("|", "¦"), int(chat_id))


def index_line(*, when, chat, chat_id, msg_id, sender, caption, filename, size, outcome, reason=""):
    """Одна строка индекса. Порядок колонок задан заданием и не переставляется: Штаб читает
    её глазами, а разбор — по позиции."""
    out = outcome if not reason else "%s — %s" % (outcome, reason)
    return "| %s | %s | %d | %s | %s | %s | %d | %s |" % (
        to_phuket(when).strftime("%Y-%m-%d %H:%M:%S"),
        chat_cell(chat, chat_id),
        int(msg_id),
        escape(sender or "?"),
        escape(caption or ""),
        escape(filename or "—"),
        int(size or 0),
        escape(out),
    )


def append_line(line, index_path=None):
    """Дописать строку в индекс. ТОЛЬКО режим "a": перезапись убила бы соседние строки, а
    потеря соседа заданием названа недопустимой прямо. Шапка рождается один раз."""
    path = index_path or INDEX_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fresh = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if fresh:
            f.write(INDEX_HEADER + "\n")
        f.write(line + "\n")
    return path


# ------------------------------- сигнатура ------------------------------------
# Расширению из имени НЕ верим — имя отдаёт отправитель. Судим по первым байтам, как и
# `trainer_photo.sniff_kind`; свою копию не заводим, чтобы два разных мнения о типе не
# разъехались («на ПК картинка, у бота нет»).

def sniff_kind(path):
    """Тип изображения по сигнатуре, ЗАИМСТВОВАННЫЙ у `trainer_photo` (одно мнение на репозиторий).
    Модуль недоступен → None, и это честное «не опознано», а не «сойдёт»."""
    try:
        import trainer_photo
    except Exception:
        return None
    return trainer_photo.sniff_kind(path)


# ------------------------------- два шага ------------------------------------
# Сеть здесь не живёт, поэтому шага два: СНАЧАЛА план (куда класть и надо ли вообще), ПОТОМ
# запись исхода. Между ними вызывающий делает `await download_media` — асинхронность остаётся
# снаружи, а вся логика индекса тестируется без единого байта сети.

def plan_save(*, has_photo, chat_id, msg_id, when, kind="jpg", index_path=None, inbox_dir=None):
    """Что делать с сообщением. → dict(act, outcome, path, filename, reason).

    act='skip'  — строки не будет: либо вложения нет (НЕ КАРТИНКА), либо номер уже в индексе;
    act='save'  — качать в `path`.

    Повтор отсекается ЗДЕСЬ, до загрузки: качать второй раз то, что уже лежит, — лишние байты
    и лишний риск второй строки."""
    if not has_photo:
        return {"act": "skip", "outcome": NOT_A_PHOTO, "path": "", "filename": "",
                "reason": "в сообщении нет вложения-картинки"}
    if already_indexed(chat_id, msg_id, index_path):
        return {"act": "skip", "outcome": "", "path": "", "filename": "",
                "reason": "номер сообщения уже в индексе"}
    name = photo_name(when, msg_id, kind)
    return {"act": "save", "outcome": "", "reason": "",
            "filename": name, "path": os.path.join(inbox_dir or INBOX_DIR, name)}


def commit_save(plan, *, chat, chat_id, msg_id, when, sender, caption,
                downloaded_path="", error="", index_path=None):
    """Записать ИСХОД загрузки в индекс. → dict(outcome, line, bytes, filename, reason).

    Ни одна ветка не молчит: сорванная загрузка (`error`, пустой путь, нулевые байты, чужая
    сигнатура) даёт строку НЕ СКАЧАН с причиной СЛОВАМИ. Именно эта строка сообщает Штабу, что
    снимок БЫЛ, — без неё потеря выглядит как отсутствие снимка."""
    res = {"outcome": NOT_DOWNLOADED, "line": "", "bytes": 0,
           "filename": plan.get("filename", ""), "reason": ""}
    path = downloaded_path or ""
    size = 0
    if path:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
    if error:
        res["reason"] = str(error)
    elif not path:
        res["reason"] = "загрузка вернула пустой путь"
    elif size <= 0:
        res["reason"] = "файл нулевой длины"
    elif sniff_kind(path) is None:
        res["reason"] = "сигнатура файла не опознана как изображение"
    else:
        res["outcome"] = SAVED
    if path:
        res["filename"] = os.path.basename(path)
    res["bytes"] = size
    res["line"] = index_line(when=when, chat=chat, chat_id=chat_id, msg_id=msg_id,
                             sender=sender, caption=caption,
                             filename=res["filename"] if res["outcome"] == SAVED else (res["filename"] or "—"),
                             size=size, outcome=res["outcome"], reason=res["reason"])
    append_line(res["line"], index_path)
    return res
