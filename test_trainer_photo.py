# -*- coding: utf-8 -*-
"""
test_trainer_photo.py — регресс на загрузку и распознавание снимка в тренажёре (задание 60-a).

Что здесь проверяется и почему именно так:
  • ТРИ исхода — разные значения, а не два с оттенком. «Битый файл» и «нечего читать» обязаны
    различаться, иначе разбор живого провала снова упрётся в «картинка не загрузилась»;
  • ОТРИЦАТЕЛЬНЫЙ №1 (без него задание не принимается): битый/непригодный файл под видом снимка
    → НЕ РАСПОЗНАНО, человеческие слова есть, выдуманного содержимого НЕТ ни строки;
  • ОТРИЦАТЕЛЬНЫЙ №2: голова недоступна → НЕ РАСПОЗНАНО, а не пустой ответ и не исключение;
  • сети здесь нет ни в одном тесте: голова инъектируется параметром `llm`.

Живой круг головы (реальный claude CLI со снимком) сюда НЕ входит сознательно — он меряется
отдельной пробой и записан числами в артефакт: набор обязан идти без внешних зависимостей.
"""

import os
import unittest

import trainer
import trainer_photo


TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp", "trainer_photos")

# Минимальный НАСТОЯЩИЙ png (сигнатура + огрызок). Содержимым не является — важна сигнатура.
PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _write(name, data):
    os.makedirs(TMP, exist_ok=True)
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


class SniffTests(unittest.TestCase):
    """Тип судим по СИГНАТУРЕ, а не по имени: «битый файл под видом снимка» — это ровно тот
    случай, когда расширение врёт."""

    def test_png_and_jpeg(self):
        self.assertEqual(trainer_photo.sniff_kind(_write("t_sniff.png", PNG_HEAD)), "png")
        self.assertEqual(trainer_photo.sniff_kind(_write("t_sniff.jpg", JPEG_HEAD)), "jpeg")

    def test_text_named_as_photo_is_not_image(self):
        p = _write("t_fake.jpg", "это просто текст, а не снимок".encode("utf-8"))
        self.assertIsNone(trainer_photo.sniff_kind(p))

    def test_missing_file(self):
        self.assertIsNone(trainer_photo.sniff_kind(os.path.join(TMP, "нет-такого.png")))

    def test_file_facts_numbers(self):
        p = _write("t_facts.png", PNG_HEAD)
        size, kind = trainer_photo.file_facts(p)
        self.assertEqual(size, len(PNG_HEAD))
        self.assertEqual(kind, "png")


class OutcomeTests(unittest.TestCase):
    """Три исхода различимы, и каждый несёт своё."""

    def test_recognized_counts_lines(self):
        p = _write("t_ok.png", PNG_HEAD)
        d = trainer_photo.describe(p, llm=lambda path: "Модель 1 день\nClick 350\nNMAX 600")
        self.assertEqual(d["outcome"], trainer_photo.RECOGNIZED)
        self.assertEqual(d["lines"], 3)
        self.assertIn("NMAX 600", d["text"])
        self.assertEqual(trainer_photo.human_note(d), "")   # распознано → бот говорит по существу

    def test_not_downloaded_is_a_third_outcome(self):
        d = trainer_photo.describe(os.path.join(TMP, "нет-файла.jpg"), llm=lambda path: "что угодно")
        self.assertEqual(d["outcome"], trainer_photo.NOT_DOWNLOADED)
        self.assertNotEqual(d["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertEqual(d["text"], "")
        self.assertIn("скачал", trainer_photo.human_note(d).lower())

    def test_empty_file_is_not_downloaded(self):
        d = trainer_photo.describe(_write("t_zero.jpg", b""), llm=lambda path: "строка")
        self.assertEqual(d["outcome"], trainer_photo.NOT_DOWNLOADED)

    def test_outcomes_are_distinct_strings(self):
        vals = {trainer_photo.RECOGNIZED, trainer_photo.NOT_RECOGNIZED, trainer_photo.NOT_DOWNLOADED}
        self.assertEqual(len(vals), 3)


class NegativeBrokenFileTests(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ №1 задания: непригодный файл под видом снимка."""

    def test_not_an_image_never_reaches_the_head(self):
        """Голову на не-изображении не зовём ВООБЩЕ: незваная голова не может ничего выдумать."""
        called = []

        def head(path):
            called.append(path)
            return "Click 350\nNMAX 600"

        p = _write("t_broken.jpg", b"PK\x03\x04not an image at all")
        d = trainer_photo.describe(p, llm=head)
        self.assertEqual(d["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertEqual(called, [])                 # голова не звана ни разу
        self.assertEqual(d["text"], "")              # ни одной выдуманной строки
        self.assertEqual(d["lines"], 0)
        self.assertIn("не изображение", d["reason"])

    def test_truncated_image_refused_by_head_gives_words_not_content(self):
        """Сигнатура верная, тело битое: голова доходит до файла и говорит, что не смогла.
        Её отказ обязан стать ИСХОДОМ, а не содержимым снимка."""
        p = _write("t_trunc.png", PNG_HEAD)
        d = trainer_photo.describe(p, llm=lambda path: trainer_photo.REFUSAL_TOKEN)
        self.assertEqual(d["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertEqual(d["text"], "")
        note = trainer_photo.human_note(d)
        self.assertTrue(note.strip())                       # молчания нет
        self.assertIn("текстом", note)                      # и это ПРОСЬБА к человеку

    def test_refusal_with_punctuation_still_refusal(self):
        p = _write("t_trunc2.png", PNG_HEAD)
        for said in ("НЕ РАСПОЗНАНО", "«НЕ РАСПОЗНАНО»", "не распознано.", "  НЕ РАСПОЗНАНО  "):
            d = trainer_photo.describe(p, llm=lambda path, s=said: s)
            self.assertEqual(d["outcome"], trainer_photo.NOT_RECOGNIZED, said)
            self.assertEqual(d["text"], "", said)

    def test_head_silence_is_not_recognized_not_silence(self):
        p = _write("t_silent.png", PNG_HEAD)
        d = trainer_photo.describe(p, llm=lambda path: "   ")
        self.assertEqual(d["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertTrue(trainer_photo.human_note(d).strip())


class NegativeHeadDownTests(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ №2 задания: голова недоступна."""

    def test_head_exception_gives_not_recognized_with_words(self):
        def dead(path):
            raise RuntimeError("claude CLI не найден")

        p = _write("t_headdown.png", PNG_HEAD)
        d = trainer_photo.describe(p, llm=dead)          # исключение НЕ выпущено наружу
        self.assertEqual(d["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertEqual(d["text"], "")
        self.assertIn("голова недоступна", d["reason"])
        self.assertTrue(trainer_photo.human_note(d).strip())

    def test_head_timeout_gives_not_recognized(self):
        """Таймаут CLI приходит в describe пустой строкой (так устроен _cli_vision) — и он
        обязан дать слова, а не пустой ответ клиенту."""
        p = _write("t_timeout.png", PNG_HEAD)
        d = trainer_photo.describe(p, llm=lambda path: "")
        self.assertEqual(d["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertTrue(trainer_photo.human_note(d).strip())


class TranscriptTests(unittest.TestCase):
    """Прочитанное обязано ДОЕХАТЬ до головы, а подпись — не вытеснить снимок."""

    def test_recognized_content_reaches_transcript(self):
        d = {"outcome": trainer_photo.RECOGNIZED, "text": "Click 350\nNMAX 600", "lines": 2}
        body = trainer_photo.transcript_body(d, text="Цены вот так на этот сезон выглядят")
        self.assertIn("Цены вот так", body)
        self.assertIn("Click 350", body)
        self.assertIn("NMAX 600", body)

    def test_not_recognized_puts_marker_not_content(self):
        d = {"outcome": trainer_photo.NOT_RECOGNIZED, "text": "", "lines": 0}
        body = trainer_photo.transcript_body(d, text="вот сетка")
        self.assertIn("вот сетка", body)
        self.assertIn("прочитать не удалось", body)

    def test_not_downloaded_marker_is_its_own(self):
        d = {"outcome": trainer_photo.NOT_DOWNLOADED, "text": "", "lines": 0}
        self.assertIn("не скачалось", trainer_photo.transcript_body(d))

    def test_client_body_no_longer_drops_photo_under_caption(self):
        """ЖИВОЙ ДЕФЕКТ 12.09: подпись побеждала вложение целиком."""
        body = trainer.client_body("Цены вот так выглядят", has_photo=True)
        self.assertIn("Цены вот так выглядят", body)
        self.assertIn("[фото]", body)                 # снимок больше не исчезает

    def test_client_body_carries_recognized_note(self):
        note = trainer_photo.transcript_body(
            {"outcome": trainer_photo.RECOGNIZED, "text": "Click 350", "lines": 1})
        body = trainer.client_body("проверь", has_photo=True, photo_note=note)
        self.assertIn("проверь", body)
        self.assertIn("Click 350", body)

    def test_client_body_old_signature_unchanged(self):
        """Обратная совместимость: живой вызов userbot_listen (три аргумента) работает как прежде."""
        self.assertEqual(trainer.client_body("хочу скутер"), "хочу скутер")
        self.assertEqual(trainer.client_body("", has_photo=True), "[фото]")
        self.assertEqual(trainer.client_body("", geo_marker="[локация 7.77,98.33]"),
                         "[локация 7.77,98.33]")
        self.assertEqual(trainer.client_body(""), "[медиа/без текста]")


class TempPlaceTests(unittest.TestCase):
    """Место для временного названо ЯВНО и лежит внутри репозитория."""

    def test_photo_dir_is_named_and_inside_repo(self):
        self.assertTrue(trainer_photo.PHOTO_DIR.endswith(os.path.join("tmp", "trainer_photos")))
        self.assertTrue(trainer_photo.PHOTO_DIR.startswith(trainer_photo.BASE_DIR))

    def test_photo_path_is_keyed_by_chat_and_message(self):
        a = trainer_photo.photo_path(-100123, 777)
        b = trainer_photo.photo_path(-100123, 778)
        self.assertNotEqual(a, b)
        self.assertIn("777", a)


class NoSideEffectTests(unittest.TestCase):
    """Модуль НИКОМУ не пишет: ни в Telegram, ни в мозг, ни в CRM."""

    def test_module_has_no_send_paths(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "trainer_photo.py"), encoding="utf-8") as f:
            src = f.read()
        for forbidden in ("send_message", "brain_writer", "trainer_log", "moderation_ipc",
                          "requests.post", "urlopen"):
            self.assertNotIn(forbidden + "(", src, forbidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
