# -*- coding: utf-8 -*-
"""
test_photo_intake.py — регресс ВРЕЗКИ чтения снимка в слушателя (задание 60-d, 12.09.2026).

Предмет — связка трёх заходов в ОДНУ доставку: снимок скачивается (60-a), ложится в папку
входящих и рождает РОВНО ОДНУ строку индекса (60-b), читается головой и едет в турн (60-a).

Сети здесь нет ни в одном тесте, живого процесса — тоже:
  • Telegram — двойник `FakeMsg`: его `download_media` пишет заданные БАЙТЫ в заданный файл и
    считает свои вызовы (второй вызов на повторе был бы виден числом);
  • голова инъектируется через `llm=` и тоже считает вызовы — «чтения не было» проверяется
    пустым списком, а не верой;
  • папка входящих и индекс уведены во ВРЕМЕННЫЙ каталог: боевые `docs/vhodyashchie-snimki/`
    и её `index.md` тесты не трогают ни одной строкой (это проверяется отдельным тестом);
  • сам `userbot_listen.py` НЕ импортируется: врезка судится РАЗБОРОМ AST. Импорт потянул бы
    `.env`, telethon и живую сессию — то есть ровно то, чего заданию касаться нельзя.

Отрицательные требования задания (п.4) закрыты классом `TestNegative`: сообщение без вложения
не рождает ни строки, ни чтения; повторная доставка того же номера второй строки не рождает;
неудачная загрузка оставляет исход НЕ СКАЧАН.
"""

import os
import ast
import shutil
import asyncio
import tempfile
import datetime
import unittest

import photo_inbox
import trainer_photo


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200          # настоящая сигнатура, огрызок тела
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
NOT_A_PICTURE = "<html>это вовсе не картинка</html>".encode("utf-8")

WHEN = datetime.datetime(2026, 9, 12, 11, 10, 35, tzinfo=datetime.timezone.utc)  # 18:10:35 (+07)
CHAT_ID = -5193185299
CHAT = "Тренеровка"


class FakeSender:
    def __init__(self, first="mike", username="SamHold", bot=False):
        self.first_name = first
        self.last_name = ""
        self.username = username
        self.bot = bot


class FakeMsg:
    """Двойник telethon.Message. `download_media` пишет БАЙТЫ в тот файл, который просят, —
    то есть ровно то, что делает живой Telethon, и ничего сверх."""

    def __init__(self, msg_id=106536, data=PNG, caption="", photo=True, fail=None, empty=False):
        self.id = msg_id
        self.chat_id = CHAT_ID
        self.date = WHEN
        self.message = caption
        self.out = False
        self.sender_id = 111
        self.photo = object() if photo else None
        self.media = self.photo
        self.document = None
        self._data = data
        self._fail = fail
        self._empty = empty
        self.downloads = []

    async def download_media(self, file=None):
        self.downloads.append(file)
        if self._fail is not None:
            raise self._fail
        if self._empty:
            return None
        os.makedirs(os.path.dirname(file), exist_ok=True)
        with open(file, "wb") as f:
            f.write(self._data)
        return file


class Head:
    """Двойник головы: считает вызовы и помнит, КАКОЙ файл ей дали читать."""

    def __init__(self, answer="Модель | 1 день | 7 дней\nClick 350 300\nNMAX 600 550"):
        self.calls = []
        self.answer = answer

    def __call__(self, path):
        self.calls.append(path)
        return self.answer


class IntakeCase(unittest.TestCase):
    """Общая обвязка: своя папка входящих и свой индекс на каждый тест."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="intake_")
        self.index = os.path.join(self.dir, "index.md")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def take(self, msg, head=None, sender=None):
        return asyncio.run(trainer_photo.intake(
            msg, chat_id=CHAT_ID, chat_title=CHAT, sender=sender or FakeSender(),
            llm=head, index_path=self.index, inbox_dir=self.dir))

    def rows(self):
        if not os.path.exists(self.index):
            return []
        with open(self.index, "r", encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f
                    if ln.startswith("| ") and not ln.startswith("| время")
                    and set(ln.strip()) - set("-| ")]


class TestFullPath(IntakeCase):
    """Поддельное сообщение со снимком проходит путь скачивание → чтение → строка индекса."""

    def test_photo_goes_all_the_way(self):
        head = Head()
        msg = FakeMsg(caption="Цены вот так на этот сезон выглядят")
        res = self.take(msg, head)

        self.assertEqual(res["saved"], photo_inbox.SAVED)                # хранение
        self.assertEqual(res["outcome"], trainer_photo.RECOGNIZED)       # чтение
        self.assertEqual(res["lines"], 3)
        self.assertEqual(res["bytes"], len(PNG))
        self.assertEqual(len(head.calls), 1)                             # голова звана РОВНО раз
        self.assertEqual(len(msg.downloads), 1)                          # и скачано РОВНО раз
        self.assertEqual(len(self.rows()), 1)                            # одна доставка — одна строка

    def test_index_row_and_reading_share_one_file(self):
        """Главное свойство врезки: строка индекса рождается НА ТОМ ЖЕ пути, что и чтение."""
        head = Head()
        msg = FakeMsg()
        res = self.take(msg, head)
        self.assertEqual(head.calls, [res["path"]])                      # читали ровно этот файл
        self.assertTrue(os.path.exists(res["path"]))
        self.assertIn(os.path.basename(res["path"]), self.rows()[0])     # он же назван в индексе
        self.assertEqual(msg.downloads, [os.path.join(self.dir, "2026-09-12_18-10-35_msg106536.jpg")])

    def test_file_lands_in_the_inbox_not_in_tmp(self):
        """Второй копии снимка нет: качаем СРАЗУ в папку входящих (60-b), а не в tmp разбора."""
        res = self.take(FakeMsg(), Head())
        self.assertEqual(os.path.dirname(res["path"]), self.dir)
        self.assertNotIn("trainer_photos", res["path"])

    def test_content_reaches_the_turn(self):
        """Прочитанное едет в транскрипт содержимым, а не шестью символами «[фото]»."""
        res = self.take(FakeMsg(), Head())
        body = trainer_photo.transcript_body(res, text="вот моя сетка")
        self.assertIn("вот моя сетка", body)
        self.assertIn("NMAX 600", body)
        self.assertNotIn("[фото]", body)
        self.assertEqual(trainer_photo.human_note(res), "")    # распознано → отдельных слов нет

    def test_caption_goes_into_the_index_verbatim(self):
        cap = "Цены вот так на этот сезон выглядят проверь и подумай в чем ошибка"
        self.take(FakeMsg(caption=cap), Head())
        cols = self.rows()[0].split(" | ")      # 0 время · 1 чат · 2 № · 3 кто · 4 подпись · 5 файл
        self.assertEqual(photo_inbox.unescape(cols[4].strip()), cap)

    def test_two_different_messages_make_two_rows(self):
        """Одна доставка — одна строка, но РАЗНЫЕ доставки строк не теряют (альбом из двух)."""
        self.take(FakeMsg(msg_id=106536), Head())
        self.take(FakeMsg(msg_id=106537, data=JPEG), Head())
        self.assertEqual(len(self.rows()), 2)


class TestNegative(IntakeCase):
    """Три отрицательных требования задания (п.4) — и ни одно не проходит «по-хорошему»."""

    def test_message_without_attachment_makes_no_row_and_no_reading(self):
        head = Head()
        msg = FakeMsg(photo=False)
        res = self.take(msg, head)
        self.assertEqual(res["saved"], photo_inbox.NOT_A_PHOTO)
        self.assertEqual(res["index_line"], "")
        self.assertEqual(head.calls, [])                       # чтения не было
        self.assertEqual(msg.downloads, [])                    # и загрузки не было
        self.assertFalse(os.path.exists(self.index))           # индекс даже не создан

    def _raw_index(self):
        with open(self.index, "rb") as f:
            return f.read()

    def test_repeat_delivery_makes_no_second_row(self):
        msg = FakeMsg()
        self.take(msg, Head())
        before = self._raw_index()
        again = FakeMsg()                                      # тот же номер, новая доставка
        res = self.take(again, Head())
        self.assertEqual(self._raw_index(), before)            # индекс ПОБАЙТОВО тот же
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(res["index_line"], "")
        self.assertEqual(again.downloads, [])                  # второй раз не качали

    def test_repeat_still_reads_the_file_already_on_disk(self):
        """Повтор не рождает строки, но и не делает бота бессодержательным: файл читается."""
        self.take(FakeMsg(), Head())
        head = Head()
        res = self.take(FakeMsg(), head)
        self.assertEqual(res["outcome"], trainer_photo.RECOGNIZED)
        self.assertEqual(len(head.calls), 1)

    def test_failed_download_keeps_outcome_not_downloaded(self):
        head = Head()
        msg = FakeMsg(fail=OSError("Telegram не отдал файл"))
        res = self.take(msg, head)
        self.assertEqual(res["saved"], photo_inbox.NOT_DOWNLOADED)          # «НЕ СКАЧАН»
        self.assertEqual(res["outcome"], trainer_photo.NOT_DOWNLOADED)      # «НЕ СКАЧАЛОСЬ»
        self.assertEqual(head.calls, [])                                    # читать было НЕЧЕГО
        self.assertEqual(len(self.rows()), 1)                               # но строка ЕСТЬ
        self.assertIn(photo_inbox.NOT_DOWNLOADED, self.rows()[0])
        self.assertIn("Telegram", self.rows()[0])                           # причина СЛОВАМИ
        self.assertNotIn(photo_inbox.SAVED, self.rows()[0])

    def test_empty_download_is_not_downloaded_too(self):
        res = self.take(FakeMsg(empty=True), Head())
        self.assertEqual(res["saved"], photo_inbox.NOT_DOWNLOADED)
        self.assertEqual(res["outcome"], trainer_photo.NOT_DOWNLOADED)
        self.assertEqual(len(self.rows()), 1)

    def test_not_a_picture_never_reaches_the_head(self):
        """Байты пришли, а картинкой не являются: голову НЕ зовём — незваная не выдумает."""
        head = Head()
        res = self.take(FakeMsg(data=NOT_A_PICTURE), head)
        self.assertEqual(res["saved"], photo_inbox.NOT_DOWNLOADED)
        self.assertEqual(head.calls, [])
        self.assertEqual(res["text"], "")

    def test_head_refusal_keeps_the_photo_stored(self):
        """Хранение и чтение НЕ связаны судьбой: нечитаемый снимок всё равно лежит и в индексе."""
        head = Head(answer="НЕ РАСПОЗНАНО")
        res = self.take(FakeMsg(), head)
        self.assertEqual(res["saved"], photo_inbox.SAVED)
        self.assertEqual(res["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertEqual(res["text"], "")                                   # выдумки нет ни строки
        self.assertEqual(trainer_photo.human_note(res), trainer_photo.WORDS_NOT_RECOGNIZED)
        self.assertIn(photo_inbox.SAVED, self.rows()[0])

    def test_head_down_does_not_break_the_row(self):
        def dead(path):
            raise RuntimeError("claude CLI не найден")
        res = self.take(FakeMsg(), dead)
        self.assertEqual(res["saved"], photo_inbox.SAVED)
        self.assertEqual(res["outcome"], trainer_photo.NOT_RECOGNIZED)
        self.assertEqual(len(self.rows()), 1)

    def test_message_without_ids_is_refused_with_words(self):
        """Ключ повтора — ПАРА (чат, номер). Подставленный ноль склеил бы ВСЕ безымянные
        доставки в один ключ: первая легла бы, остальные ушли как «уже в индексе»."""
        head = Head()
        msg = FakeMsg()
        msg.id = None
        res = asyncio.run(trainer_photo.intake(msg, chat_title=CHAT, sender=FakeSender(),
                                               llm=head, index_path=self.index,
                                               inbox_dir=self.dir))
        self.assertEqual(res["outcome"], trainer_photo.NOT_DOWNLOADED)
        self.assertIn("номера", res["reason"])
        self.assertEqual(res["index_line"], "")
        self.assertEqual(head.calls, [])
        self.assertEqual(msg.downloads, [])
        self.assertFalse(os.path.exists(self.index))

    def test_live_inbox_is_not_touched_by_tests(self):
        """Боевая папка входящих в наборе не участвует: все пути ведут во временный каталог."""
        live = photo_inbox.INDEX_PATH
        before = os.path.getsize(live) if os.path.exists(live) else None
        self.take(FakeMsg(msg_id=999001), Head())
        after = os.path.getsize(live) if os.path.exists(live) else None
        self.assertEqual(before, after)


# ═══════════════ ВРЕЗКА СТОИТ В СЛУШАТЕЛЕ — судим РАЗБОРОМ, а не подстрокой ═══════════════════
# Подстрочная проверка позеленела бы на собственном комментарии («…врезка в userbot_listen…»
# встречается в докстроках трёх файлов). Поэтому здесь ast: ищем ВЫЗОВЫ в теле функции.

def _listener_func(name="on_trainer_group"):
    with open(os.path.join(BASE_DIR, "userbot_listen.py"), "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _calls(node):
    """Имена вызовов в теле узла: 'trainer_photo.intake', 'trainer.bump_seq', …"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            out.append(ast.unparse(n.func))
    return out


class TestGrafted(unittest.TestCase):
    """Применения в живом процессе НЕТ (он не поднимается), поэтому единственное доказательство
    врезки — сам код. Здесь оно и снимается."""

    def test_listener_calls_intake(self):
        fn = _listener_func()
        self.assertIsNotNone(fn, "в userbot_listen.py нет _trainer_group_message")
        self.assertIn("trainer_photo.intake", _calls(fn))

    def test_bump_seq_stands_before_the_reading(self):
        """Гашение ответа, запланированного на подпись: чтение ~90 с против дебаунса 8 с."""
        fn = _listener_func()
        seq = _calls(fn)
        self.assertIn("trainer.bump_seq", seq)
        self.assertLess(seq.index("trainer.bump_seq"), seq.index("trainer_photo.intake"))

    def test_photo_note_reaches_client_body(self):
        """Прочитанное едет в турн: без photo_note снимок снова стал бы маркером «[фото]»."""
        fn = _listener_func()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and ast.unparse(n.func) == "trainer.client_body":
                self.assertIn("photo_note", [k.arg for k in n.keywords])
                return
        self.fail("в _trainer_group_message нет вызова trainer.client_body")

    def test_module_imports_trainer_photo(self):
        with open(os.path.join(BASE_DIR, "userbot_listen.py"), "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        self.assertIn("trainer_photo", names)

    def test_intake_is_awaited_not_left_hanging(self):
        """Корутина без await молча ничего не делает — снимок исчез бы, а тесты зеленели."""
        fn = _listener_func()
        awaited = [ast.unparse(n.value.func) for n in ast.walk(fn)
                   if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)]
        self.assertIn("trainer_photo.intake", awaited)


if __name__ == "__main__":
    unittest.main(verbosity=2)
