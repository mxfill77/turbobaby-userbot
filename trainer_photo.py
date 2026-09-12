# -*- coding: utf-8 -*-
"""
trainer_photo.py — СНИМОК В ТРЕНАЖЁРЕ: загрузка вложения и чтение его головой (полоса ПК).

ЗАЧЕМ (живой провал 12.09.2026 18:10, группа-тренажёр). Владелец прислал фотографию своей
ценовой сетки, бот ответил «сообщение пришло без вложения». Разведка назвала место потери
числом, а не догадкой — три шага пути вложения:

  1. приходит ли фото в событие  — ДА. В `userbot.log` за 18:10 лежит строка тренажёрного лога
     `TRN 2026-09-12 18:10 | TEST-11 | client | [фото]`, а маркер «[фото]» рождается РОВНО в
     одном месте (`trainer.client_body`, ветка `has_photo`) и ровно при `msg.photo is not None`
     (`userbot_listen.py`). То есть Telethon вложение принёс.
  2. скачивается ли              — НЕТ. `download_media` во ВСЁМ клиентском пути отсутствует:
     userbot_listen.py / trainer.py / trainer_run.py / suggest.py / moderation_bot.py — 0
     совпадений на пять файлов. Байтов снимка на диске не появлялось никогда.
  3. доходит ли до запроса к голове — НЕТ. В транскрипт уходит СТРОКА «[фото]» (6 символов), и
     дальше `suggest.generate_draft` → `suggest._cli_llm(system, user)` принимает ДВЕ СТРОКИ и
     зовёт CLI с `--allowed-tools ""` — файл голова открыть не может физически.

Значит «картинка не загрузилась» — не сбой сети и не каприз модели: снимок доезжает до процесса
и умирает шестью символами перед головой. Этот модуль закрывает шаги 2 и 3.

ГРАНИЦЫ (они же безопасность):
  • модуль НИКОМУ не пишет: ни в Telegram, ни в CRM, ни в мозг. Он скачивает и читает;
  • место для временного названо ЯВНО — `tmp/trainer_photos/` в корне репозитория (константа
    `PHOTO_DIR`). Ничего не удаляем: файлы остаются лежать, уборки за собой здесь нет;
  • голова зовётся ТЕМ ЖЕ CLI и по ТОЙ ЖЕ подписке, что и клиентский `suggest._cli_llm`
    (ANTHROPIC_API_KEY вычищается из env), но с `--allowed-tools "Read"` — иначе снимок не
    открыть. Ни одного другого инструмента голове не дано;
  • сеть инъектируема (`llm=`), поэтому отрицательные тесты не ходят наружу.

ТРИ ИСХОДА, И ОНИ РАЗНЫЕ (требование задания 60-a, п.3). Молчание запрещено наравне с выдумкой:

  РАСПОЗНАНО     — снимок прочитан, есть непустые строки. Их число известно (`lines`).
  НЕ РАСПОЗНАНО  — байты есть, а содержимого нет: битый файл, не-изображение, голова не смогла
                   или голова НЕДОСТУПНА. Бот говорит человеку СЛОВАМИ и просит текстом.
                   Содержимое в этой ветке ВСЕГДА пустое — выдумывать запрещено.
  НЕ СКАЧАЛОСЬ   — байтов нет вовсе (Telegram не отдал, диск не принял). Отличается от второго:
                   там читать было ЧТО и не вышло, здесь читать было НЕЧЕГО.

Почему «голова недоступна» = НЕ РАСПОЗНАНО, а не пустой ответ: пустой ответ неотличим от
молчания бота, а молчание в тренажёре и есть тот дефект, ради которого модуль заводится.
"""

import os
import re
import json
import glob
import asyncio
import subprocess
import tempfile
import logging

log = logging.getLogger("trainer_photo")   # хендлеры вешает процесс-хозяин

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- ЯВНО НАЗВАННОЕ ВРЕМЕННОЕ МЕСТО -----------------------------------------
# Одно на весь модуль, внутри репозитория, под .gitignore-маской tmp/. Ничего отсюда не
# удаляем: снимок — доказательство разбора, а уборка за собой запрещена рамкой.
PHOTO_DIR = os.path.join(BASE_DIR, "tmp", "trainer_photos")

# Потолок размера: выше него снимок голове не подаём (CLI читает файл целиком).
MAX_BYTES = int(os.getenv("TRAINER_PHOTO_MAX_BYTES", str(12 * 1024 * 1024)) or str(12 * 1024 * 1024))

# Бюджет одного круга головы со снимком. Замер 12.09: чтение PNG 9699 б заняло 78 с
# (одна проба), поэтому 30 с здесь было бы гарантированным ложным «не смог».
PHOTO_TIMEOUT = int(os.getenv("TRAINER_PHOTO_TIMEOUT", "300") or "300")

PHOTO_MODEL = os.getenv("TRAINER_PHOTO_MODEL", "sonnet").strip() or "sonnet"

# --- ИСХОДЫ ------------------------------------------------------------------
RECOGNIZED = "РАСПОЗНАНО"
NOT_RECOGNIZED = "НЕ РАСПОЗНАНО"
NOT_DOWNLOADED = "НЕ СКАЧАЛОСЬ"

# Слово, которым голова обязана назвать СВОЙ отказ. Держим одно и то же в промпте и в разборе:
# разъедутся — отказ головы поедет в продукт как содержимое снимка, то есть как выдумка.
REFUSAL_TOKEN = "НЕ РАСПОЗНАНО"

# Человеческие слова бота на второй и третий исход. Не «⚠️ ошибка», а просьба к живому человеку:
# тренажёром пользуется владелец, и он должен понимать, что делать дальше.
WORDS_NOT_RECOGNIZED = ("Фото пришло, но прочитать его я не смог. "
                        "Пришлите, пожалуйста, те же данные текстом.")
WORDS_NOT_DOWNLOADED = ("Фото не скачалось — до меня дошло только уведомление о вложении. "
                        "Пришлите его ещё раз или продиктуйте данные текстом.")


# ------------------------------- тип по байтам --------------------------------
# Расширению из имени файла НЕ верим: Telegram отдаёт имя, какое захочет отправитель, а «битый
# файл под видом снимка» (отрицательный тест п.4) — это ровно случай, когда имя врёт. Судим по
# сигнатуре первых байтов.

_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)


def sniff_kind(path):
    """Тип изображения по СИГНАТУРЕ файла. → 'jpeg'|'png'|'gif'|'bmp'|'webp' либо None (не
    изображение / файл не читается). None — это приговор «читать нечего», голову не зовём."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except Exception:
        return None
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    for sig, kind in _MAGIC:
        if head.startswith(sig):
            return kind
    return None


def file_facts(path):
    """Признаки файла для артефакта: (байты, тип). Самого снимка наружу не отдаём НИКОГДА —
    только числа. Файла нет → (0, None)."""
    try:
        size = os.path.getsize(path)
    except Exception:
        return (0, None)
    return (size, sniff_kind(path))


# ------------------------------- шаг 2: загрузка -------------------------------

def photo_path(chat_id, msg_id, kind="jpg"):
    """Куда кладём снимок. Имя несёт чат и id сообщения — один и тот же снимок не скачивается
    дважды и не затирает соседний."""
    return os.path.join(PHOTO_DIR, f"trn_{int(chat_id)}_{int(msg_id)}.{kind}")


async def download(msg, chat_id=None, msg_id=None, target=None):
    """Скачать вложение сообщения ТЕМ ЖЕ сеансом, которым бот и так работает: у объекта Telethon
    `Message` метод `download_media` ходит через КЛИЕНТА, которому сообщение принадлежит, — второй
    сессии здесь не заводится ни одной.

    `target` — ЯВНО названный путь файла. Не задан → временное место разбора `PHOTO_DIR`
    (поведение задания 60-a). Задан → качаем ТУДА и больше никуда: врезка в слушателя (60-d)
    кладёт снимок СРАЗУ в папку входящих `photo_inbox.INBOX_DIR`, чтобы строка индекса и чтение
    головой пришлись на ОДИН И ТОТ ЖЕ файл. Вторая копия того же снимка на диске означала бы, что
    Штаб и голова смотрят на разные байты.

    → dict(ok, path, bytes, kind, reason). ok=False означает исход НЕ СКАЧАЛОСЬ.
    Исключения не выпускает: сорванная загрузка обязана дать ТРЕТИЙ исход, а не уронить турн."""
    cid = chat_id if chat_id is not None else getattr(msg, "chat_id", 0) or 0
    mid = msg_id if msg_id is not None else getattr(msg, "id", 0) or 0
    out = {"ok": False, "path": "", "bytes": 0, "kind": None, "reason": ""}
    if getattr(msg, "photo", None) is None and getattr(msg, "media", None) is None:
        out["reason"] = "в сообщении нет вложения"
        return out
    target = target or photo_path(cid, mid)
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    except Exception as e:
        out["reason"] = f"каталог {os.path.dirname(target)} не создан: {type(e).__name__}"
        return out
    try:
        got = await msg.download_media(file=target)
    except Exception as e:
        out["reason"] = f"download_media: {type(e).__name__}: {e}"
        return out
    if not got:
        out["reason"] = "download_media вернул пусто"
        return out
    size, kind = file_facts(got)
    out.update(path=got, bytes=size, kind=kind)
    if size <= 0:
        out["reason"] = "файл нулевой длины"
        return out
    out["ok"] = True
    return out


# ------------------------------- шаг 3: чтение головой -------------------------

_SYSTEM = (
    "Ты читаешь ОДНО изображение и пересказываешь ТОЛЬКО то, что на нём действительно видно. "
    "Ничего не додумывай и ничего не достраивай по смыслу: если строка не читается — пропусти её. "
    "Ответ — голый текст без вступлений и без выводов: по одной прочитанной строке на строку ответа. "
    f"Если открыть или прочитать изображение не удалось — ответь РОВНО «{REFUSAL_TOKEN}» и больше ничего."
)

_USER_TMPL = (
    "Открой файл {path} инструментом Read и выпиши всё читаемое содержимое изображения: "
    "заголовки, строки таблицы, числа, подписи. Сохраняй порядок строк оригинала. "
    f"Не смог открыть или ничего не разобрать — ответь РОВНО «{REFUSAL_TOKEN}»."
)


def _resolve_claude():
    """Тот же бинарь, что зовёт клиентский suggest. Отдельного резолвера не заводим —
    разъехавшиеся резолверы дают «на ПК работает, у бота нет»."""
    import suggest
    return suggest._resolve_claude()


def _cli_vision(path, model=None, timeout=None):
    """Один круг головы со снимком: claude CLI, подписка Max, ОДИН инструмент Read.

    Отличий от `suggest._cli_llm` ровно два, и оба вынужденные: `--allowed-tools "Read"`
    (без него файл не открыть) и свой таймаут (замер 12.09 — 78 с на PNG 9699 б).
    Всё остальное копирует клиентский путь: нейтральный cwd, вычищенный API-ключ,
    `--output-format json`. → текст ответа; отказ/таймаут/сбой → пустая строка."""
    cbin = _resolve_claude()
    if not cbin:
        raise RuntimeError("claude CLI не найден — чтение снимка головой невозможно")
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)      # ключ НЕ утекает: идём по подписке
    env.pop("OPENAI_API_KEY", None)
    cmd = [cbin, "-p", _USER_TMPL.format(path=path),
           "--system-prompt", _SYSTEM,
           "--model", (model or PHOTO_MODEL),
           "--output-format", "json",
           "--allowed-tools", "Read"]
    try:
        p = subprocess.run(cmd, cwd=tempfile.gettempdir(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=(timeout or PHOTO_TIMEOUT))
    except subprocess.TimeoutExpired:
        log.warning("ТРЕНАЖЁР/фото: голова не ответила за %sс", timeout or PHOTO_TIMEOUT)
        return ""
    if p.returncode != 0:
        log.warning("ТРЕНАЖЁР/фото: claude CLI rc=%s — %s", p.returncode, (p.stderr or "").strip()[-300:])
        return ""
    try:
        data = json.loads(p.stdout or "")
    except Exception:
        return ""
    if not isinstance(data, dict) or data.get("is_error"):
        return ""
    return (data.get("result") or "").strip()


def _clean_lines(text):
    """Строки прочитанного: непустые, без служебных обрамлений markdown-таблицы."""
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if set(s) <= set("-|= "):          # разделитель таблицы содержимым не является
            continue
        out.append(s)
    return out


_REFUSAL_RE = re.compile(r"^\W*не\s+распознано\W*$", re.IGNORECASE)


def describe(path, llm=None, model=None, timeout=None):
    """Прочитать СКАЧАННЫЙ снимок головой. → dict(outcome, lines, text, bytes, kind, reason).

    `llm` — инъекция головы (тесты и отрицательные пробы подают свою функцию `path -> str`).
    Ни одна ветка не возвращает содержимое при исходе НЕ РАСПОЗНАНО: `text` там ВСЕГДА пусто —
    это и есть машинный замок против выдумки, а не обещание в промпте."""
    size, kind = file_facts(path)
    res = {"outcome": NOT_RECOGNIZED, "lines": 0, "text": "",
           "bytes": size, "kind": kind, "reason": ""}
    if size <= 0:
        res["outcome"] = NOT_DOWNLOADED
        res["reason"] = "байтов нет: файла нет или он пуст"
        return res
    if kind is None:
        res["reason"] = "не изображение: сигнатура файла не опознана"
        return res
    if size > MAX_BYTES:
        res["reason"] = f"снимок {size} б больше потолка {MAX_BYTES} б"
        return res
    call = llm or _cli_vision
    try:
        raw = call(path) if llm else call(path, model=model, timeout=timeout)
    except Exception as e:
        # ГОЛОВА НЕДОСТУПНА — второй отрицательный тест задания. Исход НЕ РАСПОЗНАНО со словами,
        # а НЕ пустой ответ и не молчание.
        res["reason"] = f"голова недоступна: {type(e).__name__}: {e}"
        return res
    raw = (raw or "").strip()
    if not raw:
        res["reason"] = "голова не дала текста"
        return res
    if _REFUSAL_RE.match(raw):
        res["reason"] = "голова сказала, что прочитать не смогла"
        return res
    lines = _clean_lines(raw)
    if not lines:
        res["reason"] = "в ответе головы нет ни одной содержательной строки"
        return res
    res.update(outcome=RECOGNIZED, lines=len(lines), text="\n".join(lines))
    return res


# ------------------------------- в продукт -------------------------------------

def transcript_body(desc, text=None):
    """Тело клиентской реплики для транскрипта тренажёра ПО ИСХОДУ разбора снимка.

    Так прочитанное доезжает до головы: не шестью символами «[фото]», а содержимым. Подпись
    (`text`) не вытесняет снимок и снимок не вытесняет подпись — до этой правки текст побеждал
    вложение целиком, и фото с подписью пропадало бесследно."""
    o = (desc or {}).get("outcome")
    if o == RECOGNIZED:
        mark = "[фото, распознано]\n" + (desc.get("text") or "")
    elif o == NOT_DOWNLOADED:
        mark = "[фото не скачалось]"
    else:
        mark = "[фото, прочитать не удалось]"
    t = (text or "").strip()
    return (t + "\n" + mark) if t else mark


def human_note(desc):
    """Что бот говорит ЧЕЛОВЕКУ, если снимок не прочитан. Для РАСПОЗНАНО — пусто: там говорит
    сам ответ по содержимому. Молчания нет ни в одной ветке."""
    o = (desc or {}).get("outcome")
    if o == RECOGNIZED:
        return ""
    if o == NOT_DOWNLOADED:
        return WORDS_NOT_DOWNLOADED
    return WORDS_NOT_RECOGNIZED


# ═══════════════ ОДНА ДОСТАВКА — ОДИН ПУТЬ, ОДНА СТРОКА (задание 60-d) ═══════════════════════
# Врезка в слушателя обязана делать ТРИ вещи за одну доставку: положить снимок в папку входящих
# (задание 60-b), родить РОВНО ОДНУ строку индекса и прочитать снимок головой (задание 60-a).
# Третьей ветки здесь нет СОЗНАТЕЛЬНО: стоило бы хранению и чтению разойтись по двум местам —
# и появился бы способ получить строку без файла (или файл без строки), то есть ровно тот
# молчаливый пропуск, против которого 60-b и заводилось.
#
# Почему СНАЧАЛА строка, ПОТОМ чтение. Загрузка — секунды, чтение головой — 78…88 с (замер 60-a).
# Строка пишется сразу после загрузки: сорвись чтение хоть насмерть, Штаб всё равно узнает, что
# снимок пришёл и лежит. Обратный порядок терял бы факт доставки ради факта чтения.
#
# Второй копии снимка не заводим: качаем СРАЗУ в папку входящих, и читаем ТОТ ЖЕ файл.
# `tmp/trainer_photos` остаётся местом ручного разбора 60-a и врезкой не используется.


def _inbox_twin(msg_id, inbox_dir):
    """Уже лежащий файл этого сообщения (повторная доставка). Имя снимка несёт номер сообщения
    последним полем (`…_msg106536.jpg`), поэтому находится он БЕЗ индекса. Нет файла → ""."""
    try:
        hits = sorted(glob.glob(os.path.join(inbox_dir, "*_msg%d.*" % int(msg_id))))
    except OSError as e:
        # Папка недоступна — это НОВОСТЬ, а не «файла нет»: молчаливое "" тут сделало бы
        # неисправный диск неотличимым от честного отсутствия снимка.
        log.warning("ТРЕНАЖЁР/фото: папка входящих %s не читается: %s", inbox_dir, e)
        return ""
    return hits[0] if hits else ""


async def intake(msg, *, chat_id=None, msg_id=None, chat_title="", sender=None, when=None,
                 caption=None, llm=None, index_path=None, inbox_dir=None):
    """ПУТЬ СНИМКА ЦЕЛИКОМ: скачать → записать строку индекса → прочитать головой. Один вызов,
    один файл, одна строка. Сети в тестах не требует: голова инъектируется через `llm`.

    → dict исхода ЧТЕНИЯ (как у `describe`) плюс четыре поля связки:
       saved       — исход ХРАНЕНИЯ словами `photo_inbox` (СОХРАНЁН · НЕ СКАЧАН · НЕ КАРТИНКА ·
                     пусто на повторе);
       index_line  — родившаяся строка индекса ("" — строки не было ни одной);
       path        — файл, который и записан в индекс, и прочитан (он один и тот же);
       read        — звалось ли чтение вообще.

    Исключений не выпускает ни одной веткой: сорванный снимок не имеет права уронить турн."""
    import photo_inbox
    import photo_inbox_fetch as pif        # sender_label/is_image_message/_fix_extension — одно
                                           # мнение на репозиторий, своих копий не заводим

    cid = chat_id if chat_id is not None else getattr(msg, "chat_id", None)
    mid = msg_id if msg_id is not None else getattr(msg, "id", None)
    when = when if when is not None else getattr(msg, "date", None)
    cap = caption if caption is not None else getattr(msg, "message", None)
    if cap is None:
        cap = ""                          # подписи не было — пустая колонка, а не потеря
    inbox = inbox_dir or photo_inbox.INBOX_DIR

    res = {"outcome": NOT_DOWNLOADED, "lines": 0, "text": "", "bytes": 0, "kind": None,
           "reason": "", "saved": "", "index_line": "", "path": "", "read": False}

    if cid is None or mid is None:
        # Ключ повтора — ПАРА (чат, номер сообщения). Подставить сюда ноль значило бы склеить
        # ВСЕ безымянные доставки в один ключ: первая записалась бы, остальные ушли бы как
        # «уже в индексе». Лучше честный отказ со словами.
        res["reason"] = "у сообщения нет номера чата или номера сообщения"
        return res

    plan = photo_inbox.plan_save(has_photo=pif.is_image_message(msg), chat_id=cid, msg_id=mid,
                                 when=when, index_path=index_path, inbox_dir=inbox)
    if plan["act"] != "save":
        res["saved"] = plan["outcome"]
        res["reason"] = plan["reason"]
        if plan["outcome"] == photo_inbox.NOT_A_PHOTO:
            return res                    # вложения не было: ни строки, ни чтения, ни байта
        # ПОВТОР той же доставки. Второй строки не рождаем и второй раз не качаем — но уже
        # лежащий файл читаем: ответ бота обязан быть содержательным и на повторе.
        twin = _inbox_twin(mid, inbox)
        if not twin:
            return res
        res["path"] = twin
        res["read"] = True
        res.update(await asyncio.to_thread(describe, twin, llm))
        return res

    got = await download(msg, chat_id=cid, msg_id=mid, target=plan["path"])
    path = pif._fix_extension(got["path"]) if got["path"] else ""
    saved = photo_inbox.commit_save(
        plan, chat=(chat_title or "?"), chat_id=cid, msg_id=mid, when=when,
        sender=pif.sender_label(msg, sender), caption=cap,
        downloaded_path=path, error=("" if got["ok"] else got["reason"]),
        index_path=index_path)
    res.update(saved=saved["outcome"], index_line=saved["line"], bytes=saved["bytes"],
               path=path or plan["path"], reason=saved["reason"])
    if saved["outcome"] != photo_inbox.SAVED:
        return res                        # читать НЕЧЕГО: исход остаётся НЕ СКАЧАЛОСЬ
    res["read"] = True
    res.update(await asyncio.to_thread(describe, path, llm))
    res["path"] = path
    return res
