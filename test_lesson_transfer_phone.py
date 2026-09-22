# -*- coding: utf-8 -*-
"""
test_lesson_transfer_phone.py — регресс СЛОВА ПЕРЕНОСА урока набора в базу бота С ТЕЛЕФОНА
(22.09.2026, задание 71e TELEFONUROK): тема 205 → `pc_agent.on_message` → дверь `lesson_transfer.py`.

ЧТО СТОРОЖИТСЯ, ПО ПУНКТАМ ЗАДАНИЯ:
  п.1 `TestTransferWordForms` — формы слова разбирает агент, а образцы печатает дверь: образец обязан
      разбираться тем же разбором; слова переноса не сталкиваются со словами включения и набора.
  п.2 `TestTransferAuthor` + `TestLiveRightFromTheEvent` — имя автора берётся ТОЛЬКО из опознанного
      отправителя; право проверяется ЖИВЫМ запуском: настоящий обработчик агента поднимает настоящую
      дверь субпроцессом, и `moderation_core.may_write_rule` судит в ней по списку прав окружения.
      Имя портится регистром, собакой в начале и чужим источником.
  п.3 `TestLiveReasonAndAnswer` — причина — слова владельца из его сообщения, кириллица и регистр
      доезжают дословно; пусто — кандидат, и это сказано словами.
  п.4 там же — ответ говорит, вошёл ли урок в ответ бота, под каким номером лёг и чем откатить
      ОДНИМ сообщением; откат этим сообщением возвращает базу байт в байт.
  п.5 `TestLiveRefusals` — два состояния намеренно: чужой отправитель → отказ (дверь не
      поднимается); запись прошла, а живой читатель строку не вернул → отказ, а не успех.

БОЕВОГО НЕ КАСАЕМСЯ. Живые прогоны идут в КОПИИ ДЕРЕВА модулей во временном каталоге ОС: дверь-ребёнок
грузит свои модули оттуда, и её адреса по умолчанию (набор, база, книга, запись о переносе) — файлы
копии. Список прав, адрес моста и прочее окружение ребёнка задаёт тест. НАРУЖУ НЕ УХОДИТ НИЧЕГО:
отправка агента `_send` подменена сборщиком ответов (боевая отправка и её проверка — РАЗНЫЕ функции),
а бот контекста на любую попытку отправить отвечает падением теста.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_transfer_phone -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import lesson_store as LS
import lesson_transfer as LT
import lesson_word_forms as WF
import pc_agent as a

HERE = os.path.dirname(os.path.abspath(__file__))
OWNER_NAME = "SamHold"
STRANGER_ID = a.ALLOWED_USER_ID + 1
WHY_OWNER = "Клиент уже назвал даты — второй вопрос о датах ЗЛИТ его, и заявка теряется"
CRITIC_WHY = "критик: бот переспросил даты"
NOW = 1790082000                                     # 2026-09-22, фиксированные часы
BOOK = ("# Playbook\n\n## Стиль общения\n- КНИЖНЫЙ-СТИЛЬ отвечай коротко\n\n"
        "## Выученные правила\n- (2026-07-14) КНИЖНОЕ-ПРАВИЛО не тяни время\n")
SET_RULES = ("НАБОР-1 не спрашивай даты дважды в одном сообщении",
             "НАБОР-2 депозит называй вместе с ценой",
             "НАБОР-3 доставку в отель предлагай сам",
             "НАБОР-4 снятый урок")
SPOILED = "Цены называй только при ясных датах".encode("utf-8").decode("cp1251")
BASE_RULES = ("БАЗА-1 здоровайся один раз", "БАЗА-2 модели называй только из парка",
              "БАЗА-3 не обещай того, чего нет")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class _NoOutsideBot:
    """Бот контекста: любая попытка уйти наружу — падение теста, а не тихая отправка."""

    def __getattr__(self, name):
        def _refuse(*_a, **_kw):
            raise AssertionError("тест пытался отправить наружу через бота: %s" % name)
        return _refuse


def _user(uid=None, username=OWNER_NAME):
    return types.SimpleNamespace(id=a.ALLOWED_USER_ID if uid is None else uid, username=username,
                                 is_bot=False)


# =======================================================================================
# п.1 — формы слова: разбор агента и образцы двери — одно и то же
# =======================================================================================
class TestTransferWordForms(unittest.TestCase):

    def test_door_samples_parse_by_the_agent_parser(self):
        for n in (1, 12):
            self.assertEqual(a.transfer_word(WF.transfer_phrase(n, "Почему ТАК правильно")),
                             ("transfer", n, "Почему ТАК правильно"))
        self.assertEqual(a.transfer_word(WF.transfer_back_phrase(10)), ("untransfer", 10, ""))
        self.assertEqual(a.transfer_word(WF.TRANSFER_LIST_PHRASE), ("ready", None, ""))
        self.assertEqual(a.transfer_word("урок перенос след"), ("trace", None, ""))
        # безномерный образец перечня разбирается, если вместо N назвать число
        probe = WF.transfer_phrase().replace(" N:", " 7:")
        self.assertEqual(a.transfer_word(probe)[:2], ("transfer", 7))

    def test_unparsed_hint_dictates_only_parseable_forms(self):
        for sample in ("урок перенос откати 3", "урок перенос след", WF.TRANSFER_LIST_PHRASE):
            self.assertIn("«%s»" % sample.replace(" 3", " M"), a.TRANSFER_UNPARSED)
            self.assertIsNotNone(a.transfer_word(sample), sample)

    def test_transfer_words_do_not_collide_with_promote_and_batch_words(self):
        mine = ("урок перенеси 5: x", "урок перенос откати 5", "урок перенос", "урок перенос след")
        for t in mine:
            self.assertIsNone(a.lesson_word(t), t)
            self.assertIsNone(a.LESSON_HEAD_RE.match(t.lower()), t)
        theirs = ("урок включи 5: x", "урок откати 5", "урок кандидаты", "урок след",
                  "урок набор сними k 3: x", "урок набор k", "урок наборы")
        for t in theirs:
            self.assertIsNone(a.transfer_word(t), t)
            self.assertIsNone(a.TRANSFER_HEAD_RE.match(t), t)

    def test_reason_case_is_kept_and_empty_is_empty(self):
        self.assertEqual(a.transfer_word("Урок ПЕРЕНЕСИ #12: Даты В ПРОШЛОМ — уточни"),
                         ("transfer", 12, "Даты В ПРОШЛОМ — уточни"))
        self.assertEqual(a.transfer_word("урок перенеси 12"), ("transfer", 12, ""))
        self.assertEqual(a.transfer_word("урок перенеси 12:"), ("transfer", 12, ""))

    def test_a_broken_tail_is_named_not_guessed(self):
        """«урок перенос 12» (существительное вместо глагола) — не перенос и не общая подсказка."""
        self.assertIsNone(a.transfer_word("урок перенос 12: причина"))
        self.assertIsNotNone(a.TRANSFER_HEAD_RE.match("урок перенос 12: причина"))


# =======================================================================================
# п.2 — имя из источника события: чистая часть замка
# =======================================================================================
class TestTransferAuthor(unittest.TestCase):

    def test_owner_name_is_the_event_username(self):
        self.assertEqual(a.transfer_author(_user()), (OWNER_NAME, None))
        self.assertEqual(a.transfer_author(_user(username="SAMHOLD")), ("SAMHOLD", None))
        self.assertEqual(a.transfer_author(_user(username=" @SamHold ")), (OWNER_NAME, None))

    def test_no_name_in_the_event_is_a_refusal_not_a_substitute(self):
        for name in (None, "", "   ", "@"):
            self.assertEqual(a.transfer_author(_user(username=name)), ("", a.TRANSFER_NO_NAME), name)

    def test_foreign_sender_is_refused_even_with_the_owner_name(self):
        self.assertEqual(a.transfer_author(_user(uid=STRANGER_ID)), ("", a.TRANSFER_DENIED))
        self.assertEqual(a.transfer_author(None), ("", a.TRANSFER_DENIED))


# =======================================================================================
# ЖИВОЙ ПРОГОН: копия дерева, настоящий обработчик агента, настоящая дверь субпроцессом
# =======================================================================================
class _LiveDoor(unittest.TestCase):

    APPROVERS = OWNER_NAME

    @classmethod
    def setUpClass(cls):
        cls._box = tempfile.TemporaryDirectory(prefix="telefon_urok_test_")
        cls.pristine = os.path.join(cls._box.name, "pristine")
        os.makedirs(cls.pristine)
        for name in sorted(os.listdir(HERE)):
            if name.endswith(".py") and not name.startswith("test_"):
                shutil.copy2(os.path.join(HERE, name), os.path.join(cls.pristine, name))
        cls._n = 0

    @classmethod
    def tearDownClass(cls):
        cls._box.cleanup()

    def setUp(self):
        type(self)._n += 1
        self.tree = os.path.join(self._box.name, "tree%d" % self._n)
        shutil.copytree(self.pristine, self.tree)
        self.set = os.path.join(self.tree, "exam_live", "lessons.tsv")
        self.base = os.path.join(self.tree, "lesson_store.tsv")
        book = os.path.join(self.tree, "manager-bot", "docs", "playbook.md")
        os.makedirs(os.path.dirname(self.set))
        os.makedirs(os.path.dirname(book))
        with open(book, "w", encoding="utf-8") as f:
            f.write(BOOK)
        # набор: #1, #2 действующие (причина критика), #3 кандидат, #4 снят, #5 испорчен кодировкой
        for i, rule in enumerate(SET_RULES, start=1):
            kw = dict(question="Сколько стоит на неделю? (%d)" % i, bot_answer="Уточню и вернусь.",
                      correct=rule, who="SamHold", when="2026-09-18T10:00:00Z", path=self.set,
                      source=LS.SOURCE_EXAM)
            if i == 3:
                LS.add_candidate(**kw)
            else:
                LS.add(why=CRITIC_WHY, **kw)
        LS.withdraw(number=4, path=self.set, now=NOW)
        LS.add(question="вопрос", bot_answer="ответ", correct=SPOILED, why=CRITIC_WHY, who="SamHold",
               when="2026-09-18T10:00:00Z", path=self.set, source=LS.SOURCE_EXAM)
        # база бота: три действующих урока книги — номера 1..3 заняты чужими уроками
        for rule in BASE_RULES:
            LS.add(question="перенос книги", bot_answer="перенос книги", correct=rule,
                   why="перенос книги", who="владелец", when="2026-09-03T12:00:00Z", path=self.base,
                   source=LS.SOURCE_BOOK)
        self.set_sha = _sha(self.set)

        self.addCleanup(setattr, a, "REPO_DIR", a.REPO_DIR)
        a.REPO_DIR = Path(self.tree)
        # ОТПРАВКА: боевая `_send` не зовётся ни разу — вместо неё сборщик ответов теста
        self.sent = []

        async def _collect(context, chat_id, text, **kw):
            self.sent.append(text)
        self.addCleanup(setattr, a, "_send", a._send)
        a._send = _collect
        # ОКРУЖЕНИЕ РЕБЁНКА — задано тестом: список прав, без моста, без токенов, тест-режим
        env = mock.patch.dict(os.environ, {
            "TESTING": "1", "APPROVER_USERNAMES": self.APPROVERS, "INTAKE_APPROVERS": "",
            "BRIDGE_URL": "", "BRIDGE_TOKEN": "", "MODERBOT_TOKEN": "", "AGENT_BOT_TOKEN": "",
            "LESSON_LLM_ROUTE": "0"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LESSON_BASE_READ_OFF", None)      # вернётся вместе со словарём
        # СЧЁТ ПОДЪЁМОВ ДВЕРИ: каждый вызов subprocess.run агента с lesson_transfer.py
        self.door_runs = []
        real_run = subprocess.run

        def _spy(argv, *args, **kw):
            if any(str(x).endswith("lesson_transfer.py") for x in argv):
                self.door_runs.append(list(argv))
            return real_run(argv, *args, **kw)
        spy = mock.patch.object(a.subprocess, "run", side_effect=_spy)
        spy.start()
        self.addCleanup(spy.stop)

    # --- приборы -------------------------------------------------------------------------
    def say(self, text, uid=None, username=OWNER_NAME, **msg_extra):
        before = len(self.sent)
        msg = types.SimpleNamespace(text=text, message_thread_id=a.HQ_THREAD_ID, **msg_extra)
        update = types.SimpleNamespace(effective_message=msg,
                                       effective_chat=types.SimpleNamespace(id=a.HQ_CHAT_ID),
                                       effective_user=_user(uid, username))
        asyncio.run(a.on_message(update, types.SimpleNamespace(bot=_NoOutsideBot())))
        got = self.sent[before:]
        self.assertEqual(len(got), 1, "на одно сообщение ждали ровно один ответ: %r" % got)
        return got[0]

    def row(self, number):
        return next((x for x in LS.load(self.base).lessons if x.number == number), None)

    def untouched(self, base_sha):
        self.assertEqual(_sha(self.base), base_sha, "база бота тронута")
        self.assertEqual(_sha(self.set), self.set_sha, "набор тронут")
        self.assertEqual(LT.load_moves(self.base), (), "запись о переносе появилась")


# =======================================================================================
# п.2 — право проверяется ЖИВЫМ запуском: регистр, собака, чужой источник
# =======================================================================================
class TestLiveRightFromTheEvent(_LiveDoor):

    def test_case_of_the_event_name_passes_the_live_right_and_is_kept(self):
        """В списке прав имя строчными и с собакой, в событии — заглавными: право одно и то же,
        а в строку ложится имя из события как есть."""
        with mock.patch.dict(os.environ, {"APPROVER_USERNAMES": "@samhold"}):
            out = self.say("урок перенеси 1: " + WHY_OWNER, username="SAMHOLD")
        self.assertIn("ВОШЁЛ В ОТВЕТ", out)
        self.assertEqual(self.row(4).who, "SAMHOLD")
        self.assertEqual(LT.load_moves(self.base)[0].who, "SAMHOLD")

    def test_at_sign_in_front_of_the_event_name_is_cut(self):
        out = self.say("урок перенеси 1: " + WHY_OWNER, username="@SamHold")
        self.assertIn("ВОШЁЛ В ОТВЕТ", out)
        self.assertEqual(self.row(4).who, OWNER_NAME)

    def test_owner_name_outside_the_list_is_refused_by_the_live_right(self):
        base_sha = _sha(self.base)
        with mock.patch.dict(os.environ, {"APPROVER_USERNAMES": "someone_else"}):
            out = self.say("урок перенеси 1: " + WHY_OWNER)
        self.assertIn("Нет прав", out)
        self.assertEqual(len(self.door_runs), 1, "право судила не дверь")
        self.untouched(base_sha)

    def test_empty_list_of_rights_refuses_the_owner_too(self):
        base_sha = _sha(self.base)
        with mock.patch.dict(os.environ, {"APPROVER_USERNAMES": ""}):
            out = self.say("урок перенеси 1: " + WHY_OWNER)
        self.assertIn("Нет прав", out)
        self.untouched(base_sha)

    def test_name_written_in_the_text_is_not_an_author(self):
        """Чужой источник имени — текст сообщения: у отправителя ника нет, а в тексте названо имя
        из списка прав. Отказ, и дверь не поднималась."""
        base_sha = _sha(self.base)
        out = self.say("урок перенеси 1: велел @SamHold", username=None)
        self.assertEqual(out, a.TRANSFER_NO_NAME)
        self.assertEqual(self.door_runs, [])
        self.untouched(base_sha)

    def test_text_name_does_not_replace_the_sender_name(self):
        out = self.say("урок перенеси 1: так сказал @someone_else, " + WHY_OWNER)
        self.assertIn("ВОШЁЛ В ОТВЕТ", out)
        self.assertEqual(self.row(4).who, OWNER_NAME)


# =======================================================================================
# п.3–4 — причина словами владельца; ответ: вошёл ли, какой номер, чем откатить одним сообщением
# =======================================================================================
class TestLiveReasonAndAnswer(_LiveDoor):

    def test_owner_transfers_by_phone_and_rolls_back_by_phone_byte_for_byte(self):
        sha1 = _sha(self.base)
        out = self.say("урок перенеси 1: " + WHY_OWNER)
        sha2 = _sha(self.base)
        self.assertTrue(out.startswith("✅"), out)
        self.assertIn("ВОШЁЛ В ОТВЕТ", out)
        self.assertIn("как урок #4", out)
        self.assertIn("бот отвечает по нему", out)
        back_word = WF.transfer_back_phrase(4)
        self.assertIn("одним сообщением в теме %s: «%s»" % (WF.PROMOTE_TOPIC, back_word), out,
                      "откат одним сообщением не продиктован")
        les = self.row(4)
        self.assertEqual((les.state, les.who, les.why, les.source, les.correct),
                         (LS.STATE_ACTIVE, OWNER_NAME, WHY_OWNER, LS.SOURCE_EXAM, SET_RULES[0]),
                         "строка базы не та: автор, причина словами владельца, источник")
        self.assertEqual(self.door_runs[-1][-1], "--why-stdin", "причина поехала не через stdin")
        self.assertNotIn(WHY_OWNER, " ".join(self.door_runs[-1]), "причина в командной строке")
        back = self.say(back_word)
        self.assertIn("ОТКАЧЕН", back)
        self.assertIn("совпал", back)
        self.assertNotEqual(sha1, sha2)
        self.assertEqual(_sha(self.base), sha1, "база после отката словом не равна базе до переноса")
        self.assertEqual(_sha(self.set), self.set_sha, "набор тронут")

    def test_empty_reason_lands_candidate_and_says_it_in_words(self):
        out = self.say("урок перенеси 2")
        self.assertTrue(out.startswith("📝"), out)
        self.assertIn("КАНДИДАТОМ", out)
        self.assertIn("НЕ входит", out)
        self.assertNotIn("ВОШЁЛ", out)
        self.assertIn("«%s»" % WF.promote_phrase(4), out, "как включить — не сказано словом")
        les = self.row(4)
        self.assertEqual((les.state, les.why), (LS.STATE_CANDIDATE, ""))
        with open(self.base, encoding="utf-8") as f:
            self.assertNotIn(CRITIC_WHY, f.read(), "причина критика переехала за владельца")

    def test_ready_list_names_numbers_and_topics_not_texts(self):
        out = self.say("урок перенос")
        self.assertIn("можно перенести в базу бота: 3 из 5", out)
        self.assertIn("#1 — %s" % LT.topic_of(SET_RULES[0]), out)
        self.assertIn("испорчен кодировкой: #5", out)
        self.assertIn("сняты: #4", out)
        self.assertIn("«%s»" % WF.transfer_phrase(), out)
        for rule in SET_RULES:
            self.assertNotIn(rule, out, "в перечень уехал текст правила целиком")
        self.assertNotIn("Сколько стоит", out, "в перечень уехал вопрос клиента")
        self.say("урок перенеси 1: " + WHY_OWNER)
        again = self.say("урок перенос")
        self.assertIn("#1 → урок бота #4", again)
        self.assertIn("можно перенести в базу бота: 2 из 5", again)


# =======================================================================================
# п.5 — отрицательный тест, два состояния НАМЕРЕННО
# =======================================================================================
class TestLiveRefusals(_LiveDoor):

    def test_state_1_foreign_sender_is_refused_and_the_door_is_not_raised(self):
        """Чужой отправитель — даже с именем владельца и именем из списка прав в событии."""
        base_sha = _sha(self.base)
        out = self.say("урок перенеси 1: " + WHY_OWNER, uid=STRANGER_ID)
        self.assertEqual(out, a.TRANSFER_DENIED)
        for word in ("урок перенос", "урок перенос откати 4", "урок перенос 12"):
            self.assertEqual(self.say(word, uid=STRANGER_ID), a.TRANSFER_DENIED, word)
        self.assertEqual(self.door_runs, [], "дверь поднималась на чужое слово")
        self.untouched(base_sha)

    def test_state_1_owner_message_forwarded_by_a_stranger_is_refused(self):
        base_sha = _sha(self.base)
        origin = types.SimpleNamespace(sender_user=_user())
        out = self.say("урок перенеси 1: " + WHY_OWNER, uid=STRANGER_ID, username="someone_else",
                       forward_origin=origin, forward_from=_user())
        self.assertEqual(out, a.TRANSFER_DENIED)
        self.assertEqual(self.door_runs, [])
        self.untouched(base_sha)

    def test_state_2_written_but_the_live_reader_did_not_return_it_is_a_refusal(self):
        """Запись проходит, а живой читатель правил в процессе двери выключен объявленным
        откатом `LESSON_BASE_READ_OFF` — строка лежит, в ответ она не вошла."""
        with mock.patch.dict(os.environ, {"LESSON_BASE_READ_OFF": "1"}):
            out = self.say("урок перенеси 1: " + WHY_OWNER)
        self.assertTrue(out.startswith("⛔"), out)
        self.assertIn("НЕ состоялся как правило", out)
        self.assertIn("до записи 0, после 0", out)
        self.assertIn("ВЫКЛЮЧЕНО", out)
        self.assertNotIn("бот отвечает", out)
        self.assertNotIn("ВОШЁЛ", out)
        les = self.row(4)
        self.assertEqual(les.state, LS.STATE_ACTIVE, "запись не прошла — состояние не то, что меряем")
        self.assertIn("«%s»" % WF.transfer_back_phrase(4), out, "откат записанного не продиктован")


if __name__ == "__main__":
    unittest.main()
