# -*- coding: utf-8 -*-
"""
test_photo_inbox.py — регресс папки входящих снимков (задание 60-b).

Здесь стои́т ОТРИЦАТЕЛЬНЫЙ ТЕСТ, без которого задание не принимается, и он тройной:
  • сообщение без вложения строки не создаёт ВОВСЕ;
  • неудачная загрузка создаёт строку НЕ СКАЧАН с причиной словами;
  • повторная доставка того же номера сообщения второй строки не рождает.

Сети в тестах нет ни байта: загрузку делает вызывающий, а сюда приходит уже готовый исход —
поэтому «неудачную загрузку» изображает не мок Telegram, а тот же вход, что придёт живьём.
Боевой индекс `docs/vhodyashchie-snimki/index.md` тесты НЕ трогают: каждый пишет в свой
временный каталог (`index_path`/`inbox_dir`), и это проверяется отдельным тестом.
"""

import os
import datetime
import tempfile
import shutil
import unittest

import photo_inbox as pi


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 40)
WHEN = datetime.datetime(2026, 9, 12, 11, 10, 3, tzinfo=datetime.timezone.utc)   # 18:10:03 Пхукет


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="photo_inbox_test_")
        self.index = os.path.join(self.dir, "index.md")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def body(self):
        if not os.path.exists(self.index):
            return ""
        with open(self.index, "r", encoding="utf-8") as f:
            return f.read()

    def rows(self):
        """Только строки-записи индекса (шапка и разделитель — не записи)."""
        body = self.body()
        out = []
        for ln in body.splitlines():
            if not ln.startswith("| "):
                continue
            if ln.startswith("| время") or set(ln) <= set("|- "):
                continue
            out.append(ln)
        return out

    def save(self, msg_id=101, *, has_photo=True, caption="", data=PNG,
             error="", empty_path=False, sender="владелец", chat="Тренеровка", chat_id=-100777):
        """Один полный проход «сообщение → план → загрузка → индекс»."""
        plan = pi.plan_save(has_photo=has_photo, chat_id=chat_id, msg_id=msg_id, when=WHEN,
                            kind="png", index_path=self.index, inbox_dir=self.dir)
        if plan["act"] != "save":
            return plan, None
        path = ""
        if not error and not empty_path:
            path = plan["path"]
            with open(path, "wb") as f:
                f.write(data)
        res = pi.commit_save(plan, chat=chat, chat_id=chat_id, msg_id=msg_id, when=WHEN,
                             sender=sender, caption=caption, downloaded_path=path,
                             error=error, index_path=self.index)
        return plan, res


# ----------------------------- ОТРИЦАТЕЛЬНЫЙ ТЕСТ (п.4) -----------------------

class TestNegative(Base):
    def test_message_without_attachment_makes_no_row(self):
        """Сообщение без вложения — НЕ КАРТИНКА, и строки в индексе НЕТ ВОВСЕ."""
        plan, res = self.save(msg_id=1, has_photo=False)
        self.assertEqual(plan["act"], "skip")
        self.assertEqual(plan["outcome"], pi.NOT_A_PHOTO)
        self.assertIsNone(res)
        self.assertEqual(self.rows(), [])
        self.assertFalse(os.path.exists(self.index), "текстовое сообщение не смеет даже создать индекс")

    def test_failed_download_makes_row_with_reason(self):
        """Сорванная загрузка — строка ЕСТЬ, исход НЕ СКАЧАН, причина СЛОВАМИ.
        Без этой строки Штаб не узнает, что снимок вообще присылали."""
        plan, res = self.save(msg_id=2, error="download_media: TimeoutError: истекло время")
        self.assertEqual(res["outcome"], pi.NOT_DOWNLOADED)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertIn(pi.NOT_DOWNLOADED, rows[0])
        self.assertIn("TimeoutError", rows[0])
        self.assertNotIn(pi.SAVED, rows[0])

    def test_repeat_delivery_makes_no_second_row(self):
        """Повторная доставка ТОГО ЖЕ номера сообщения второй строки не рождает."""
        self.save(msg_id=3)
        first = self.rows()
        self.assertEqual(len(first), 1)
        plan, res = self.save(msg_id=3)
        self.assertEqual(plan["act"], "skip")
        self.assertIn("уже в индексе", plan["reason"])
        self.assertIsNone(res)
        self.assertEqual(self.rows(), first, "повтор не смеет ни добавить, ни изменить строку")

    def test_repeat_after_failed_download_also_skipped(self):
        """И повтор ПОСЛЕ неудачи второй строки не даёт: ключ — номер сообщения, а не исход."""
        self.save(msg_id=4, error="сеть отвалилась")
        plan, _ = self.save(msg_id=4)
        self.assertEqual(plan["act"], "skip")
        self.assertEqual(len(self.rows()), 1)

    def test_same_msg_id_in_another_chat_is_not_a_repeat(self):
        """Ключ повтора — ПАРА (чат, номер). Одинаковый номер в другом чате — другой снимок."""
        self.save(msg_id=5, chat_id=-100777)
        self.save(msg_id=5, chat_id=-100888, chat="Другая")
        self.assertEqual(len(self.rows()), 2)


# ----------------------------- ТРИ ИСХОДА (п.3) -------------------------------

class TestOutcomes(Base):
    def test_saved(self):
        plan, res = self.save(msg_id=10)
        self.assertEqual(res["outcome"], pi.SAVED)
        self.assertEqual(res["reason"], "")
        self.assertEqual(res["bytes"], len(PNG))
        self.assertTrue(os.path.exists(plan["path"]))

    def test_three_outcomes_are_distinct_words(self):
        """Исходы не сливаются: три разных слова, ни одно не подстрока другого."""
        vals = [pi.SAVED, pi.NOT_DOWNLOADED, pi.NOT_A_PHOTO]
        self.assertEqual(len(set(vals)), 3)
        for a in vals:
            for b in vals:
                if a is not b:
                    self.assertNotIn(a, b)

    def test_empty_path_is_not_downloaded(self):
        _, res = self.save(msg_id=11, empty_path=True)
        self.assertEqual(res["outcome"], pi.NOT_DOWNLOADED)
        self.assertIn("пустой путь", res["reason"])

    def test_zero_length_file_is_not_downloaded(self):
        _, res = self.save(msg_id=12, data=b"")
        self.assertEqual(res["outcome"], pi.NOT_DOWNLOADED)
        self.assertIn("нулевой длины", res["reason"])

    def test_not_an_image_body_is_not_downloaded(self):
        """Байты есть, а сигнатура чужая — это НЕ СОХРАНЁН. Имени файла не верим."""
        _, res = self.save(msg_id=13, data=b"<html>not a picture at all</html>")
        self.assertEqual(res["outcome"], pi.NOT_DOWNLOADED)
        self.assertIn("сигнатура", res["reason"])

    def test_reason_is_words_not_a_code(self):
        """Причина в строке — человеческие слова, а не голый код ошибки."""
        _, res = self.save(msg_id=14, empty_path=True)
        self.assertGreater(len(res["reason"].split()), 1)


# ----------------------------- ИНДЕКС (п.2) -----------------------------------

class TestIndex(Base):
    def test_line_has_all_eight_fields_in_order(self):
        _, res = self.save(msg_id=20, caption="Цены вот так", sender="Филипп")
        row = self.rows()[0]
        cols = [c.strip() for c in row.strip("|").split(" | ")]
        self.assertEqual(len(cols), 8, row)
        self.assertEqual(cols[0], "2026-09-12 18:10:03")        # время по Пхукету
        self.assertIn("Тренеровка", cols[1])                    # чат
        self.assertEqual(cols[2], "20")                         # номер сообщения
        self.assertEqual(cols[3], "Филипп")                     # кто прислал
        self.assertEqual(cols[4], "Цены вот так")               # подпись дословно
        self.assertTrue(cols[5].endswith(".png"))               # имя файла
        self.assertEqual(cols[6], str(len(PNG)))                # размер в байтах
        self.assertEqual(cols[7], pi.SAVED)                     # исход

    def test_index_is_appended_not_rewritten(self):
        """Дописывание, а не перезапись: соседняя строка пережить обязана."""
        self.save(msg_id=21)
        self.save(msg_id=22)
        self.save(msg_id=23)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertIn("| 21 |", rows[0])
        self.assertIn("| 23 |", rows[2])

    def test_foreign_line_survives(self):
        """Чужая строка, дописанная руками, не теряется и разбор не роняет."""
        self.save(msg_id=24)
        with open(self.index, "a", encoding="utf-8") as f:
            f.write("| строка руками владельца |\n")
        self.save(msg_id=25)
        body = self.body()
        self.assertIn("строка руками владельца", body)
        self.assertIn("| 25 |", body)

    def test_header_written_once(self):
        self.save(msg_id=26)
        self.save(msg_id=27)
        self.assertEqual(self.body().count("# Входящие снимки владельца"), 1)

    def test_time_is_phuket_not_utc(self):
        """Время в индексе — Пхукет (+7), и это видно числом: 11:10 UTC = 18:10 местного."""
        _, _ = self.save(msg_id=28)
        self.assertIn("2026-09-12 18:10:03", self.rows()[0])
        self.assertNotIn("11:10:03", self.rows()[0])

    def test_naive_time_is_read_as_utc(self):
        t = pi.to_phuket(datetime.datetime(2026, 9, 12, 11, 10, 3))
        self.assertEqual(t.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-12 18:10:03")


# ----------------------------- ПОДПИСЬ ДОСЛОВНО --------------------------------

class TestCaption(Base):
    def test_multiline_caption_stays_one_row(self):
        """Подпись с переносом не разрывает индекс на две строки."""
        _, _ = self.save(msg_id=30, caption="первая\nвторая\tтретья")
        self.assertEqual(len(self.rows()), 1)

    def test_caption_is_verbatim_after_unescape(self):
        for src in ["первая\nвторая", "цена | 500", "слэш \\ и |", "", "таб\tтут", "обычная подпись"]:
            self.assertEqual(pi.unescape(pi.escape(src)), src)

    def test_pipe_in_caption_does_not_add_column(self):
        self.save(msg_id=31, caption="700 | 3500 | 12000")
        row = self.rows()[0]
        cols = [c for c in row.strip("|").split(" | ")]
        self.assertEqual(len(cols), 8)
        self.assertEqual(pi.unescape(cols[4].strip()), "700 | 3500 | 12000")

    def test_empty_caption_is_allowed(self):
        _, res = self.save(msg_id=32, caption="")
        self.assertEqual(res["outcome"], pi.SAVED)
        self.assertEqual(len(self.rows()), 1)


# ----------------------------- ИМЯ ФАЙЛА (п.1) ---------------------------------

class TestName(unittest.TestCase):
    def test_name_carries_date_time_and_msg_id(self):
        """По имени снимок находится БЕЗ индекса — в нём дата, время и номер сообщения."""
        n = pi.photo_name(WHEN, 12345, "jpg")
        self.assertEqual(n, "2026-09-12_18-10-03_msg12345.jpg")

    def test_jpeg_becomes_jpg(self):
        self.assertTrue(pi.photo_name(WHEN, 1, "jpeg").endswith(".jpg"))

    def test_name_is_not_a_path(self):
        """Тип приходит снаружи — имя обязано остаться ИМЕНЕМ, а не путём."""
        n = pi.photo_name(WHEN, 1, "../../evil.sh")
        self.assertNotIn("/", n)
        self.assertNotIn("\\", n)
        self.assertTrue(n.endswith(".sh"))

    def test_empty_kind_falls_back_to_jpg(self):
        self.assertTrue(pi.photo_name(WHEN, 1, "").endswith(".jpg"))


# ----------------------------- МЕСТО (п.1) -------------------------------------

class TestPlace(unittest.TestCase):
    def test_inbox_dir_is_inside_repo(self):
        root = os.path.dirname(os.path.abspath(pi.__file__))
        self.assertTrue(os.path.abspath(pi.INBOX_DIR).startswith(root + os.sep))

    def test_inbox_dir_is_not_under_tmp(self):
        """Место НЕ в tmp/: tmp/ под .gitignore, и Штаб такой файл с облака не прочитает —
        то есть цель задания не достигается вовсе."""
        rel = os.path.relpath(pi.INBOX_DIR, os.path.dirname(os.path.abspath(pi.__file__)))
        self.assertFalse(rel.replace("\\", "/").startswith("tmp/"), rel)

    def test_index_lives_next_to_photos(self):
        self.assertEqual(os.path.dirname(pi.INDEX_PATH), pi.INBOX_DIR)

    def _src(self):
        with open(pi.__file__, "r", encoding="utf-8") as f:
            return f.read()

    def test_module_does_not_import_telethon(self):
        """Сети в модуле нет: telethon здесь не импортируется ни строкой."""
        src = self._src()
        self.assertNotIn("import telethon", src)
        self.assertNotIn("from telethon", src)

    def test_index_opened_only_for_append(self):
        """Перезаписи нет ни одной: индекс открывается только в режиме дописывания."""
        self.assertNotIn('open(path, "w"', self._src())


# ----------------------------- РУКИ: наружу ничего --------------------------------

class FakeSender(object):
    def __init__(self, bot=False, first_name="Филипп", username="f"):
        self.bot, self.first_name, self.username, self.last_name = bot, first_name, username, ""


class FakeDoc(object):
    def __init__(self, mime):
        self.mime_type = mime


class FakeMsg(object):
    def __init__(self, photo=None, document=None, out=False, sender_id=1):
        self.photo, self.document, self.out, self.sender_id = photo, document, out, sender_id


class TestFetcher(unittest.TestCase):
    """Руки проверяем БЕЗ сети: telethon здесь не поднимается ни разу."""

    def setUp(self):
        import photo_inbox_fetch
        self.f = photo_inbox_fetch

    def _src(self):
        with open(self.f.__file__, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_nothing_goes_out(self):
        """Наружу не уходит НИ ОДНОГО сообщения: отправки в коде нет ни одной формой."""
        src = self._src()
        for forbidden in ("send_message", "send_file", "forward_messages", "edit_message"):
            self.assertNotIn(forbidden + "(", src, forbidden)

    def test_never_calls_start(self):
        """client.start() спросил бы телефон и код — в headless это вечный висяк.

        Судим по РАЗБОРУ, а не по подстроке: слово `client.start()` стои́т в докстроке модуля,
        и текстовая проверка ловила бы собственное объяснение вместо вызова."""
        import ast
        tree = ast.parse(self._src())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "start"]
        self.assertEqual(calls, [], "в коде найден вызов .start()")

    def test_live_session_is_opened_read_only(self):
        """Живая сессия занята работающим userbot: открываем её только на чтение."""
        self.assertIn("mode=ro", self._src())

    def test_own_and_bot_messages_are_not_taken(self):
        self.assertFalse(self.f.is_owner_message(FakeMsg(out=True), FakeSender()))
        self.assertFalse(self.f.is_owner_message(FakeMsg(), FakeSender(bot=True)))
        self.assertTrue(self.f.is_owner_message(FakeMsg(), FakeSender()))

    def test_image_detection(self):
        self.assertTrue(self.f.is_image_message(FakeMsg(photo=object())))
        self.assertTrue(self.f.is_image_message(FakeMsg(document=FakeDoc("image/png"))))
        self.assertFalse(self.f.is_image_message(FakeMsg(document=FakeDoc("application/pdf"))))
        self.assertFalse(self.f.is_image_message(FakeMsg()))
        self.assertFalse(self.f.is_image_message(None))

    def test_phuket_day_bounds_are_shifted_seven_hours(self):
        """Сутки считаются по Пхукету: 00:00 местного = 17:00 UTC предыдущего дня."""
        lo, hi = self.f._phuket_day_bounds(datetime.date(2026, 9, 12))
        self.assertEqual(lo.strftime("%Y-%m-%d %H:%M"), "2026-09-11 17:00")
        self.assertEqual(hi.strftime("%Y-%m-%d %H:%M"), "2026-09-12 17:00")
        self.assertLess(lo, WHEN)
        self.assertLess(WHEN, hi)

    def test_extension_follows_signature_not_name(self):
        d = tempfile.mkdtemp(prefix="photo_inbox_ext_")
        try:
            p = os.path.join(d, "a.jpg")
            with open(p, "wb") as fh:
                fh.write(PNG)
            self.assertTrue(self.f._fix_extension(p).endswith(".png"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_session_copy_dir_is_named_and_inside_repo(self):
        root = os.path.dirname(os.path.abspath(pi.__file__))
        self.assertTrue(os.path.abspath(self.f.SESSION_COPY_DIR).startswith(root + os.sep))
        self.assertIn("photo_inbox_session", self.f.SESSION_COPY_DIR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
