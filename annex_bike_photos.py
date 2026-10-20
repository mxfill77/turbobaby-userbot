# -*- coding: utf-8 -*-
"""
annex_bike_photos.py — СНИМКИ 38 БАЙКОВ ДЛЯ ПРИЛОЖЕНИЯ К ДОГОВОРУ АРЕНДЫ ПАРКА (20.10.2026).

ЗАЧЕМ. К договору аренды парка Филипп → TB Mobility от 20.10.2026 собирается Приложение 4: по
каждому из 38 байков Приложения 1 — обложка зелёной книги, страница с Филиппом и ФОТО БАЙКА, где
виден номер. Зелёные книги лежат на Диске, а фото — только в форуме Обслуживания (тема под каждый
байк, -1002751134848). Облачная сессия до Telegram не достаёт (сеть окружения закрыта), браузера
владельца у неё нет. Здесь, на ПК, форум открыт юзербот-сессией, и браузер не нужен вовсе.

ЧТО ДЕЛАЕТ. Для каждого байка из `BIKES` идёт в ЕГО тему форума и кладёт на диск снимки темы:
`--newest` самых свежих и `--oldest` самых ранних (ранние — кадры приёмки, номер на них виден чаще).
Какой кадр пойдёт в приложение, решает не скрипт: номер на снимке сверяется глазами с книгой.

ГРАНИЦЫ (они же безопасность, образец — `chatlog_servicing_fetch`):
  • ТОЛЬКО ЧТЕНИЕ: отправок, пересылок, правок и удалений в файле нет — проверяется тестом по AST;
  • сессия — по КОПИИ функцией `photo_inbox_fetch.copy_session`, `client.start()` не зовётся;
  • ничего не удаляет и не перезаписывает: снимок, уже лежащий на диске, второй раз не качается;
    качается он в `<имя>.part` и получает итоговое имя только целым (огрызок обрыва — не снимок);
  • сбой одной темы или одного снимка пишется в отчёт словами, заход идёт дальше;
  • место названо явно: `tmp/annex_bike_photos/` (под .gitignore — снимки в git не идут, решение
    владельца 23.09) плюс один архив рядом `tmp/annex_bike_photos.zip` — его и загрузить на Диск.

Запуск:
  venv\\Scripts\\python.exe annex_bike_photos.py --dry     — пройти темы, посчитать, ничего не качать
  venv\\Scripts\\python.exe annex_bike_photos.py           — скачать снимки и собрать архив
"""

import os
import re
import sys
import json
import asyncio
import zipfile
import argparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

OUT_DIR = os.path.join(BASE_DIR, "tmp", "annex_bike_photos")
ZIP_PATH = os.path.join(BASE_DIR, "tmp", "annex_bike_photos.zip")
SESSION_COPY_DIR = os.path.join(BASE_DIR, "tmp", "annex_bike_photos_session")
TOPICS_MAP = os.path.join(BASE_DIR, "topics_map.json")

NEWEST = 12
OLDEST = 4
SCAN_CAP = 600          # сообщений одной темы, дальше не читаем — потолок на случай длинной темы
PART = ".part"          # суффикс недокачанного снимка
RUN_TIMEOUT_S = 1500

# Приложение 1 договора от 20.10.2026: номер строки, имя байка ДОСЛОВНО как в договоре, тема форума.
# Тема подобрана по номеру в конце имени темы (`topics_map.json`); у №25 темы с его номером нет.
BIKES = (
    (1, "ADV 350CC BLACK PHUKET 5849", 62),
    (2, "ADV 350CC GREY BKK 798", 64),
    (3, "CB 300CC R 9011", 66),
    (4, "CBR 650R PHUKET 4505", 68),
    (5, "CLICK 125CC PHUKET 5580", 69),
    (6, "FORZA 300CC WHITE PHUKET 8950", 70),
    (7, "MT-03 300CC BLUE 5068", 71),
    (8, "NINJA 400CC PHUKET 6334", 73),
    (9, "NMAX 155CC BLACK GOLD PHUKET 4255", 75),
    (10, "NMAX 155CC BLACK GOLD-2 PHUKET 4685", 76),
    (11, "NMAX 155CC BLACK GOLD-3 PHUKET 7530", 77),
    (12, "NMAX 155CC BLACK GREEN PHUKET 4253", 78),
    (13, "NMAX 155CC BLACK PHUKET 8952", 74),
    (14, "NMAX 155CC GREEN-B PHUKET 4957", 79),
    (15, "NMAX 155CC GREY GOLD PHUKET 6908", 201),
    (16, "NMAX 155CC GREY PHUKET 5960", 80),
    (17, "NMAX 155CC RACE PHUKET 7532", 346),
    (18, "NMAX 155CC RED WHITE PHUKET 9548", 1051),
    (19, "VULCAN 650CC S PHUKET 5065", 81),
    (20, "XMAX 300CC NEW RED GLOSS BKK 5709", 721),
    (21, "XMAX 300CC BLUE PHUKET 5773", 264),
    (22, "XMAX 300CC GREEN PHUKET 4248", 222),
    (23, "XMAX 300CC GREY PHUKET 4246", 381),
    (24, "XSR 155CC BLACK PHUKET 8949", 334),
    (25, "XSR 155 GREEN", None),
    (26, "XMAX 300CC NEW BLACK PHUKET 8969", 85),
    (27, "XMAX 300CC NEW BLUE PHUKET 1813", 243),
    (28, "ADV 350CC DARK GREY BANGKOK 6895", 63),
    (29, "XMAX 300CC NEW RED GLOSS 2 PHUKET 4729", 119),
    (30, "XMAX 300CC NEW BROWN BANGKOK 9315", 1190),
    (31, "ADV 350CC RED BKK 9890", 65),
    (32, "XADV 750CC BLACK BKK 3902", 82),
    (33, "XMAX 300CC NEW BLUE-2 PHUKET 4725", 2202),
    (34, "CB 650R BLACK PHUKET 3503", 67),
    (35, "XADV 750CC GREY BKK 2478", 83),
    (36, "ADV 350CC LIGHT GREY HKT 6004", 2582),
    (37, "ADV 350CC BLACK GOLD PHUKET 372", 2583),
    (38, "XMAX 300CC NEW BLUE-3 PHUKET 4724", 308),
)


def trailing_number(name):
    """Номер в конце имени («… PHUKET 5849» → «5849»); имени без номера — None."""
    m = re.search(r"(\d+)\s*$", name or "")
    return m.group(1) if m else None


def bike_dir(no, name, out_dir=None):
    slug = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_")
    return os.path.join(out_dir or OUT_DIR, "%02d_%s" % (no, slug))


def photo_name(when, tid, mid):
    """Имя снимка: дата по Пхукету, тема и номер сообщения. Тема в имени — чтобы снимок из темы,
    названной по ошибке (`--topic`), был виден в папке байка как чужой, а не смешался с верными."""
    import photo_inbox
    return "%s_t%d_m%d.jpg" % (photo_inbox.to_phuket(when).strftime("%Y-%m-%d"), int(tid), int(mid))


def parse_topic_overrides(items):
    """«25=1234» → {25: 1234}: тему для байка без своей темы называет человек, а не догадка скрипта."""
    out = {}
    for it in items or ():
        no, _, tid = it.partition("=")
        out[int(no)] = int(tid)
    return out


def plan(overrides=None):
    """Список (номер, имя, тема) с учётом подсказок; тема None — байк без темы."""
    overrides = overrides or {}
    return [(no, name, overrides.get(no, tid)) for no, name, tid in BIKES]


async def topic_photos(client, entity, tid, want, reverse, is_image, scan_cap=SCAN_CAP):
    """Снимки ОДНОЙ темы форума: первые `want` штук от свежего края (или от раннего при `reverse`).

    Фильтр по снимкам здесь ручной, и это не небрежность: в Telethon 1.43.2 `iter_messages` с
    `reply_to=` идёт ветвью `GetRepliesRequest`, а `filter=` уходит только в ветвь `SearchRequest` —
    с темой он молча игнорируется, и `limit` считал бы любые сообщения, а не снимки. Поэтому
    читаем сообщения темы подряд (не больше `scan_cap`) и отбираем снимки сами."""
    got = []
    if want <= 0:
        return got
    async for m in client.iter_messages(entity, reply_to=tid, limit=scan_cap, reverse=reverse):
        if is_image(m):
            got.append(m)
            if len(got) >= want:
                break
    return got


async def download_one(m, path, fix_ext):
    """Скачать снимок атомарно: сначала в `<имя>.part`, и только целый файл переименовать в итоговое
    имя. Telethon пишет файл кусками и при обрыве (таймаут захода, сеть, Ctrl+C) недокачанное не
    убирает — без `.part` огрызок с итоговым именем следующий прогон принял бы за готовый снимок.
    → итоговый путь или None."""
    part = path + PART
    got = await m.download_media(file=part)
    if not got:
        return None
    os.replace(got, path)
    return fix_ext(path)


async def fetch_bike(client, entity, no, name, tid, newest, oldest, dry, is_image, fix_ext,
                     out_dir=None, existing=None):
    """Один байк: снимки его темы → папка байка. Любой сбой темы или снимка записывается в строку
    отчёта словами, а заход идёт к следующему байку: одна битая тема не должна оставить остальных
    37 без снимков, а архив — несобранным."""
    import chatlog_servicing as servicing
    existing = existing or servicing.existing_photo
    row = {"no": no, "name": name, "topic": tid, "photos": 0, "saved": 0, "files": [], "failed": []}
    if tid is None:
        row["why"] = "у байка нет своей темы в topics_map.json — подскажите: --topic %d=<номер темы>" % no
        return row
    seen = {}
    try:
        for m in await topic_photos(client, entity, tid, newest, reverse=False, is_image=is_image):
            seen[m.id] = m
        for m in await topic_photos(client, entity, tid, oldest, reverse=True, is_image=is_image):
            seen[m.id] = m
    except Exception as e:                                 # тема не читается — байк без снимков
        row["why"] = "тема %s не прочитана: %s" % (tid, type(e).__name__)
    row["photos"] = len(seen)
    d = bike_dir(no, name, out_dir)
    for mid in sorted(seen):
        m = seen[mid]
        path = os.path.join(d, photo_name(m.date, tid, mid))
        have, _size = existing(path)
        if have and _size > 0:
            row["files"].append(have)
            continue
        if dry:
            continue
        os.makedirs(d, exist_ok=True)
        try:
            got = await download_one(m, path, fix_ext)
        except Exception as e:
            row["failed"].append({"mid": mid, "why": type(e).__name__})
            continue
        if got:
            row["saved"] += 1
            row["files"].append(got)
        else:
            row["failed"].append({"mid": mid, "why": "Telegram не отдал файл"})
    return row


def make_zip(src_dir=None, zip_path=None):
    """Один архив со всеми снимками — его удобно загрузить на Диск одним файлом."""
    src_dir = src_dir or OUT_DIR
    zip_path = zip_path or ZIP_PATH
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for root, _dirs, files in os.walk(src_dir):
            for f in sorted(files):
                if f.endswith(PART):
                    continue                               # недокачанный снимок в архив не идёт
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, os.path.dirname(src_dir)))
                n += 1
    return n


async def run(newest, oldest, dry, overrides):
    from telethon import TelegramClient                   # импорт внутри: тестам он не нужен
    from dotenv import load_dotenv
    import photo_inbox_fetch
    import chatlog_servicing as servicing
    import chatlog_servicing_fetch as sfetch

    def is_image(m):
        # Снимок — тем же судьёй, что у архива форума: превью ссылки и стикер снимком НЕ считаются
        # (у `photo_inbox_fetch.is_image_message` оба прошли бы и заняли место кадра приёмки).
        return sfetch.media_kind(m) == servicing.PHOTO

    load_dotenv()                                          # значения читает КОД, мы их не печатаем
    api_id = int(os.getenv("API_ID", "0") or "0")
    api_hash = os.getenv("API_HASH", "")
    if not api_id or not api_hash:
        print("НЕТ ДОСТУПА: API_ID/API_HASH не заданы окружением — Telegram не открыть")
        return 2, None

    sess = photo_inbox_fetch.copy_session(SESSION_COPY_DIR)
    client = TelegramClient(sess, api_id, api_hash)
    await client.connect()                                 # НЕ start(): вход по телефону недопустим
    try:
        if not await client.is_user_authorized():
            print("НЕ АВТОРИЗОВАНЫ по копии сессии — входить не будем.")
            return 3, None
        entity = await sfetch._find_forum(client)
        if entity is None:
            print("ФОРУМ НЕ НАЙДЕН: %d среди диалогов юзербота нет" % servicing.CHAT_ID)
            return 4, None
        report = []
        for no, name, tid in plan(overrides):
            row = await fetch_bike(client, entity, no, name, tid, newest, oldest, dry, is_image,
                                   photo_inbox_fetch._fix_extension)
            row["files"] = [os.path.relpath(f, BASE_DIR) for f in row["files"]]
            report.append(row)
        if not dry:
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
        return 0, report
    finally:
        await client.disconnect()


def main(argv=None):
    import io_utf8
    io_utf8.force_utf8()                                   # печать по-русски в cp1251-консоли ПК
    ap = argparse.ArgumentParser(description="Снимки 38 байков из форума Обслуживания → tmp/annex_bike_photos")
    ap.add_argument("--newest", type=int, default=NEWEST, help="сколько самых свежих снимков темы")
    ap.add_argument("--oldest", type=int, default=OLDEST, help="сколько самых ранних снимков темы")
    ap.add_argument("--topic", action="append", default=[], metavar="N=TOPIC",
                    help="тема для байка N, если своей нет: N=<номер темы из topics_map.json>")
    ap.add_argument("--dry", action="store_true", help="пройти темы и посчитать, ничего не качать")
    a = ap.parse_args(argv)
    try:
        code, report = asyncio.run(asyncio.wait_for(
            run(a.newest, a.oldest, a.dry, parse_topic_overrides(a.topic)), RUN_TIMEOUT_S))
    except asyncio.TimeoutError:
        print("ПОТОЛОК ЗАХОДА %d с — снимки, успевшие лечь, остаются на диске" % RUN_TIMEOUT_S)
        return 5
    if report is None:
        return code
    for r in report:
        print("%2d  %-40s тема %-5s снимков %-3d скачано %-3d сбоев %-2d %s"
              % (r["no"], r["name"], r["topic"], r["photos"], r["saved"], len(r["failed"]),
                 r.get("why", "")))
    if not a.dry:
        n = make_zip()
        print("АРХИВ: %s — %d файлов. Его загрузить на Диск (или приложить в чат)."
              % (os.path.relpath(ZIP_PATH, BASE_DIR), n))
    return code


if __name__ == "__main__":
    sys.exit(main())
