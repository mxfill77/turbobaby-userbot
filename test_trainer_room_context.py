# -*- coding: utf-8 -*-
"""
test_trainer_room_context.py — ОТРИЦАТЕЛЬНЫЙ ТЕСТ пути «текст, написанный владельцем в комнате
тренажёра, → ВХОД ГОЛОВЫ» (задание 67c, 19.09.2026).

ЧТО ЗДЕСЬ ДОКАЗЫВАЕТСЯ И ЧТО НЕТ. Карточка живого набора ПЕЧАТАЛА владельцу «урок ложится в базу
уроков ЖИВОГО набора и на ответы бота НЕ действует» — 19.09.2026 (задание 67d) эта фраза выпрямлена
во всех трёх местах показа, и замком на неё стои́т `test_exam_show`
(`TestLiveWordsEndWhereObservationEnds`, проверен мутантом). Первая половина фразы — про базу и верна;
вторая — про СУДЬБУ СОДЕРЖАНИЯ, а содержание доезжает до головы ВТОРОЙ дорогой, мимо всякой базы:
тот же текст владелец печатает ОБЫЧНЫМ СООБЩЕНИЕМ в комнату (`pc_agent.on_message` ловит его из
общего потока чата `EXAM_CHAT_ID`), а `userbot_listen.on_trainer_group` видит в этой комнате ВСЁ и,
не опознав в тексте команду тренажёра, кладёт его репликой ТЕСТ-клиента в накопительный транскрипт.
Транскрипт целиком уходит голове (`_trainer_generate` → `suggest.generate_draft`).

ПРИБОР КОНЧАЕТСЯ ТАМ, ГДЕ КОНЧАЕТСЯ НАБЛЮДЕНИЕ. Живую голову тест НЕ зовёт ни разу (сеть —
красная операция на этой полосе), поэтому он меряет не «что ответила голова», а ВХОД головы:
`generate_draft(call_llm=…)` — штатный шов инъекции, через него ответ головы становится ФУНКЦИЕЙ
ЕЁ ВХОДА. Отсюда и сила отрицательного теста: спорный кусок истории — ЕДИНСТВЕННЫЙ носитель ника и
содержания во всём входе, и без него их во входе НОЛЬ. Ответ, содержащий ник, при нулевом входе
пришлось бы ВЫДУМАТЬ.

Сущности только тестовые: выдуманный ник, выдуманные FAQ и книга правил, синтетический транскрипт.
Ни боевой базы, ни денежных путей, ни боевых файлов состояния тест не касается; в комнату ничего
не отправляется (голова заменена записывающей заглушкой).
"""

import hashlib
import unittest

import suggest
import trainer


# Тестовый ник и тестовое содержание урока: в боевых базах их нет по построению (они выдуманы
# здесь), поэтому любое их появление во входе головы имеет ровно один источник — транскрипт.
NICK = "@test_partner_zzq"
LESSON = ("Касаемо вопросов по обмену можно отвечать, что можно поинтересоваться "
          "здесь у наших близких партнёров " + NICK)
LESSON_MARK = "вопросов по обмену"

# Заглушки баз: ни ника, ни содержания про обмен. Живые базы измерены отдельно (артефакт 67c) и
# дают те же нули, но тест обязан быть детерминированным и не зависеть от растущих боевых файлов.
FAQ = ("ТЕСТ-FAQ. Аренда мотобайков на Пхукете: залог, доставка, минимальный срок. "
       "Валют не меняем, партнёров по этой теме не называем.")
PLAYBOOK = ("## Выученные правила\n"
            "- (2026-01-01) не дублируй название модели в одной строке.\n")
PARK = ("Yamaha NMAX 155", "Honda PCX 160")


class _Head:
    """Записывающая заглушка головы. Ответ — функция ВСЕГО входа: одинаковый вход даёт одинаковый
    ответ, любое изменение входа меняет ответ. Именно это свойство и проверяется ниже."""

    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        digest = hashlib.sha256(("%s\x00%s" % (system, user)).encode("utf-8")).hexdigest()
        return "Здравствуйте! Ответ головы " + digest[:16]

    @property
    def fed(self):
        """ВЕСЬ вход головы за прогон, одной строкой (система + реплики, все круги)."""
        return "\n".join("%s\n%s" % (s, u) for s, u in self.calls)


def _base_transcript():
    """Диалог ТЕСТ-клиента ДО спорного куска — той же сборкой, что живой путь тренажёра."""
    t = ""
    t = trainer.append_turn(t, "client", trainer.client_body("Привет, хочу байк на неделю"))
    t = trainer.append_turn(t, "manager", "Здравствуйте! Подскажите даты, подберу вариант.")
    t = trainer.append_turn(t, "client", trainer.client_body("С 20 по 27 сентября"))
    return t


def _run(transcript):
    head = _Head()
    draft = suggest.generate_draft(transcript, "ru", FAQ, is_first_contact=False,
                                   pricing_note="", call_llm=head, park_models=PARK,
                                   playbook=PLAYBOOK)
    return draft, head


class TestLessonReachesHeadThroughRoom(unittest.TestCase):
    """Спорный кусок истории — единственный носитель содержания во входе головы."""

    def setUp(self):
        base = _base_transcript()
        # С куском: ровно так его кладёт `_trainer_client_turn`, не опознав команду тренажёра.
        self.with_lesson = trainer.append_turn(base, "client", trainer.client_body(LESSON))
        self.without = base
        self.draft_with, self.head_with = _run(self.with_lesson)
        self.draft_without, self.head_without = _run(self.without)

    def test_lesson_lands_as_client_turn_not_as_command(self):
        """Урок владельца тренажёр командой НЕ считает — значит он станет репликой клиента."""
        kind, _payload = trainer.parse_command(LESSON)
        self.assertIsNone(kind, "текст урока опознан командой тренажёра — путь был бы другим")
        self.assertIn("[клиент]: " + LESSON, self.with_lesson,
                      "урок не лёг репликой ТЕСТ-клиента — сборка транскрипта разошлась с живой")

    def test_head_input_carries_nick_and_content_with_the_chunk(self):
        """ЕСТЬ кусок — ник и содержание во входе головы ЕСТЬ."""
        self.assertGreaterEqual(self.head_with.fed.count(NICK), 1,
                                "ник не доехал до входа головы — путь назван неверно")
        self.assertGreaterEqual(self.head_with.fed.count(LESSON_MARK), 1,
                                "содержание урока не доехало до входа головы")
        self.assertGreaterEqual(len(self.head_with.calls), 1, "голову не звали ни разу")

    def test_head_input_is_clean_without_the_chunk(self):
        """НЕТ куска — ни ника, ни содержания во ВСЁМ входе головы НОЛЬ. Это и есть отрицание."""
        self.assertEqual(self.head_without.fed.count(NICK), 0,
                         "ник нашёлся во входе БЕЗ спорного куска — носитель другой")
        self.assertEqual(self.head_without.fed.count(LESSON_MARK), 0,
                         "содержание нашлось во входе БЕЗ спорного куска — носитель другой")

    def test_answer_changes(self):
        """Ответ головы — функция входа; входы разошлись, значит разошёлся и ответ."""
        self.assertNotEqual(self.head_with.fed, self.head_without.fed,
                            "входы головы совпали — куска в них нет, и путь не тот")
        self.assertNotEqual(self.draft_with, self.draft_without,
                            "ответ НЕ изменился — путь не тот, и это надо сказать прямо")

    def test_bases_are_not_the_carrier(self):
        """Контроль: заглушки баз чисты — иначе нули выше зеленели бы на пустом месте."""
        for name, text in (("FAQ", FAQ), ("книга правил", PLAYBOOK)):
            self.assertEqual(text.count(NICK), 0, name)
            self.assertEqual(text.count(LESSON_MARK), 0, name)


class TestRoomIsShared(unittest.TestCase):
    """Комната у экзамена и у тренажёра ОДНА — ровно поэтому второй дороге есть где пройти."""

    def test_exam_chat_equals_trainer_chat(self):
        import exam_show
        import pc_agent
        self.assertEqual(int(exam_show.TRAINER_CHAT), int(pc_agent.EXAM_CHAT_ID),
                         "чат карточек экзамена и чат тренажёра разошлись — премиса П1 протухла")


if __name__ == "__main__":
    unittest.main(verbosity=2)
