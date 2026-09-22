# -*- coding: utf-8 -*-
"""
test_lesson_transfer.py — регресс ДВЕРИ ПЕРЕНОСА «урок набора экзамена → база правил бота» и замка
«слова о включённом уроке — только после живого читателя» (22.09.2026, задание 71b MOSTUROKOV).

ЧТО СТОРОЖИТСЯ, ПО ПУНКТАМ ЗАДАНИЯ:
  п.1 `TestTransferOneLesson` — перенос по ОДНОМУ номеру набора, под правом; строка несёт автора, время,
      свой номер и причину, запись о переносе — номер набора; пустая причина → КАНДИДАТ, причина
      критика из набора в строку не переезжает.
  п.2 `TestNumbersNotMixed` — номера двух баз не смешиваются в ОБЕ стороны.
  п.3 `TestRollbackByteForByte` — база до / после / после отката: первая и третья равны байт в байт;
      урок без записи о переносе не откатывается, и это сказано словами.
  п.4–5 `TestAnswerGate` — слова «бот отвечает» только при «строк урока в промпте после = до + 1»;
      запись прошла, а читатель строки не вернул → ОТКАЗ (код 3), а не успех.
  ложь показа у двери перевода — `TestPromoteCardTellsTheTruth`.

БОЕВОГО НЕ КАСАЕМСЯ: набор, база, книга и записи о переносе — временные файлы; `lesson_store.STORE_PATH`
и адреса читателя в `suggest` подменены и возвращаются; право — подменённый список или инъекция.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_transfer -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import ast
import contextlib
import hashlib
import io
import os
import tempfile
import unittest

import lesson_promote
import lesson_store as LS
import lesson_transfer as LT
import suggest
import trainer

HERE = os.path.dirname(os.path.abspath(__file__))
OWNER = "filipp"
STRANGER = "stranger_dp"
WHY = "клиент назвал даты — цена по прайсу известна, ожидание теряет заявку"
CRITIC_WHY = "критик: бот ушёл в «вернусь» при известной цене"
NOW = 1790082000                                     # 2026-09-22, фиксированные часы
BOOK = ("# Playbook\n\n## Стиль общения\n- КНИЖНЫЙ-СТИЛЬ отвечай коротко\n\n"
        "## Выученные правила\n- (2026-07-14) КНИЖНОЕ-ПРАВИЛО не тяни время\n")

SET_RULES = ("НАБОР-1 называй цену сразу, если даты названы",
             "НАБОР-2 не спрашивай даты дважды в одном сообщении",
             "НАБОР-3 депозит называй вместе с ценой",
             "НАБОР-4 доставку в отель предлагай сам")
BASE_RULES = ("БАЗА-1 здоровайся один раз", "БАЗА-2 модели называй только из парка",
              "БАЗА-3 не обещай того, чего нет")


def allow(name):
    return name == OWNER


def deny(_name):
    return False


class _Base(unittest.TestCase):
    """Свой набор, своя база бота и своя книга на каждый тест; читатель смотрит в эту базу."""

    def setUp(self):
        box = tempfile.TemporaryDirectory(prefix="lesson_transfer_test_")
        self.addCleanup(box.cleanup)
        self.box = box.name
        self.set = os.path.join(self.box, "exam_lessons.tsv")
        self.base = os.path.join(self.box, "lesson_store.tsv")
        self.book = os.path.join(self.box, "playbook.md")
        with open(self.book, "w", encoding="utf-8") as f:
            f.write(BOOK)
        self.addCleanup(setattr, LS, "STORE_PATH", LS.STORE_PATH)
        LS.STORE_PATH = self.base
        for name in ("PLAYBOOK_FILE", "LESSON_BASE_PATH", "LESSON_BASE_OFF", "APPROVER_USERNAMES"):
            self.addCleanup(setattr, suggest, name, getattr(suggest, name))
        suggest.PLAYBOOK_FILE = self.book
        suggest.LESSON_BASE_PATH = None
        suggest.LESSON_BASE_OFF = False
        suggest.APPROVER_USERNAMES = {OWNER}
        # набор: первые два действующих (с причиной критика), третий кандидат, четвёртый снят
        for i, rule in enumerate(SET_RULES, start=1):
            kw = dict(question="Сколько стоит на неделю? (%d)" % i, bot_answer="Уточню и вернусь.",
                      correct=rule, who="SamHold", when="2026-09-18T10:00:00Z", path=self.set,
                      source=LS.SOURCE_EXAM)
            if i == 3:
                LS.add_candidate(**kw)
            else:
                LS.add(why=CRITIC_WHY, **kw)
        LS.withdraw(number=4, path=self.set, now=NOW)
        # база бота: три действующих урока книги — номера 1..3 ЗАНЯТЫ чужими уроками
        for rule in BASE_RULES:
            LS.add(question="перенос книги", bot_answer="перенос книги", correct=rule,
                   why="перенос книги", who="владелец", when="2026-09-03T12:00:00Z", path=self.base,
                   source=LS.SOURCE_BOOK)

    # --- приборы -------------------------------------------------------------------------
    @staticmethod
    def sha(path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    @staticmethod
    def raw_line(path, number):
        with open(path, "rb") as f:
            for line in f:
                if line.split(b"\t")[0] == str(number).encode():
                    return line
        return None

    def row(self, path, number):
        return next((x for x in LS.load(path).lessons if x.number == number), None)

    def prompt(self):
        return LT.live_prompt()

    def move(self, n, **kw):
        kw.setdefault("who", OWNER)
        kw.setdefault("src", self.set)
        kw.setdefault("base", self.base)
        kw.setdefault("may_write", allow)
        kw.setdefault("now", NOW)
        return LT.transfer_lesson(n, **kw)

    def back(self, n, **kw):
        kw.setdefault("who", OWNER)
        kw.setdefault("base", self.base)
        kw.setdefault("may_write", allow)
        kw.setdefault("now", NOW + 60)
        return LT.rollback_transfer(n, **kw)

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = LT.main(list(argv))
        return code, out.getvalue()


# =======================================================================================
# п.1 — перенос ОДНОГО урока отдельным действием под правом
# =======================================================================================
class TestTransferOneLesson(_Base):

    def test_reason_given_lands_active_carries_all_parts_and_enters_the_answer(self):
        dec = self.move(1, why=WHY)
        self.assertEqual(dec["status"], LT.STATUS_IN_ANSWER, dec["card"])
        n = dec["n"]
        self.assertEqual(n, 4, "урок бота получил не свой номер: база занята 1..3")
        les = self.row(self.base, n)
        self.assertEqual(les.state, LS.STATE_ACTIVE)
        self.assertEqual(les.who, OWNER, "автор строки — не тот, кто переносил")
        self.assertEqual(les.when, LS.now_stamp(NOW), "время строки — не время переноса")
        self.assertEqual(les.why, WHY, "причина строки — не слова владельца")
        self.assertEqual(les.source, LS.SOURCE_EXAM)
        mv = LT.load_moves(self.base, number=n)
        self.assertEqual(len(mv), 1)
        self.assertEqual((mv[0].act, mv[0].number, mv[0].set_number, mv[0].who, mv[0].why),
                         (LT.ACT_TRANSFER, n, 1, OWNER, WHY))
        self.assertEqual(mv[0].set_key, LT.set_key(self.set))
        self.assertIn(LT.bullet_of(les), self.prompt().splitlines())
        self.assertIn("ВОШЁЛ В ОТВЕТ", dec["card"])
        self.assertIn("бот отвечает", dec["card"])

    def test_empty_reason_lands_candidate_and_critic_reason_does_not_travel(self):
        dec = self.move(1, why="   ")
        self.assertEqual(dec["status"], LT.STATUS_CANDIDATE, dec["card"])
        les = self.row(self.base, dec["n"])
        self.assertEqual(les.state, LS.STATE_CANDIDATE)
        self.assertEqual(les.why, "", "причина за владельца подставлена (из набора или ниоткуда)")
        with open(self.base, encoding="utf-8") as f:
            self.assertNotIn(CRITIC_WHY, f.read())
        self.assertNotIn(LT.bullet_of(les), self.prompt().splitlines(), "кандидат дошёл до промпта")
        self.assertNotIn("бот отвечает по нему со", dec["card"])
        self.assertNotIn("ВОШЁЛ", dec["card"])
        self.assertIn("НЕ входит", dec["card"])
        # и штатный перевод без слов владельца его НЕ включает: причины в строке нет
        res = LS.promote(dec["n"], why="", who=OWNER, path=self.base)
        self.assertFalse(res.ok)

    def test_one_lesson_per_move_and_no_second_transfer(self):
        first = self.move(2, why=WHY)
        self.assertEqual(first["status"], LT.STATUS_IN_ANSWER, first["card"])
        before = self.sha(self.base)
        again = self.move(2, why=WHY)
        self.assertEqual(again["status"], LT.STATUS_REFUSED)
        self.assertIn("уже перенесён", again["card"])
        self.assertIn("#%d" % first["n"], again["card"])
        self.assertEqual(self.sha(self.base), before, "повторный перенос тронул базу")
        self.assertEqual(len(LS.load(self.base).lessons), 4, "перенесено больше одного урока")

    def test_right_is_fail_closed_and_nothing_is_written(self):
        before = self.sha(self.base)
        self.assertEqual(self.move(1, why=WHY, may_write=deny)["status"], LT.STATUS_DENIED)
        # живой гейт: чужое имя и пустой список прав
        self.assertEqual(self.move(1, why=WHY, who=STRANGER, may_write=None)["status"],
                         LT.STATUS_DENIED)
        suggest.APPROVER_USERNAMES = set()
        self.assertEqual(self.move(1, why=WHY, may_write=None)["status"], LT.STATUS_DENIED)
        self.assertEqual(self.move(1, why=WHY, who="  ")["status"], LT.STATUS_NO_AUTHOR)
        self.assertEqual(self.sha(self.base), before)
        self.assertFalse(os.path.exists(LT.transfer_log_path(self.base)))

    def test_withdrawn_missing_and_spoiled_lessons_are_refused(self):
        spoiled = "Цены называй только при ясных датах".encode("utf-8").decode("cp1251")
        LS.add(question="вопрос", bot_answer="ответ", correct=spoiled, why=CRITIC_WHY, who="SamHold",
               when="2026-09-18T10:00:00Z", path=self.set, source=LS.SOURCE_EXAM)
        before = self.sha(self.base)
        for n, word in ((4, "снят"), (99, "нет"), (5, "кодировкой")):
            dec = self.move(n, why=WHY)
            self.assertEqual(dec["status"], LT.STATUS_REFUSED, dec["card"])
            self.assertIn(word, dec["card"])
        self.assertEqual(self.sha(self.base), before)

    def test_missing_base_is_not_created_and_same_file_is_refused(self):
        ghost = os.path.join(self.box, "no_base.tsv")
        dec = self.move(1, why=WHY, base=ghost)
        self.assertEqual(dec["status"], LT.STATUS_REFUSED)
        self.assertFalse(os.path.exists(ghost), "дверь завела базу первой строкой")
        self.assertEqual(self.move(1, why=WHY, base=self.set)["status"], LT.STATUS_REFUSED)


# =======================================================================================
# п.2 — номера двух баз не смешиваются, в обе стороны
# =======================================================================================
class TestNumbersNotMixed(_Base):

    def test_set_number_does_not_touch_the_same_number_in_the_base(self):
        combat_two = self.raw_line(self.base, 2)
        set_sha = self.sha(self.set)
        dec = self.move(2, why=WHY)
        self.assertEqual(dec["status"], LT.STATUS_IN_ANSWER, dec["card"])
        self.assertNotEqual(dec["n"], 2, "урок набора #2 сел на номер #2 базы")
        self.assertEqual(self.raw_line(self.base, 2), combat_two, "одноимённый урок базы тронут")
        self.assertEqual(self.sha(self.set), set_sha, "перенос изменил набор")
        self.assertEqual(self.row(self.base, dec["n"]).correct, SET_RULES[1])

    def test_base_number_does_not_touch_the_same_number_in_the_set(self):
        dec = self.move(1, why=WHY)
        n = dec["n"]                                            # 4 — в наборе тоже есть #4
        set_sha = self.sha(self.set)
        set_same = self.raw_line(self.set, n)
        self.assertIsNotNone(set_same)
        self.assertEqual(self.back(n)["status"], LT.STATUS_ROLLED_BACK)
        self.assertEqual(self.sha(self.set), set_sha, "откат по номеру базы тронул набор")
        self.assertEqual(self.raw_line(self.set, n), set_same)
        # номер базы, который из набора не переносился, откатом переноса не открывается
        before = self.sha(self.base)
        dec2 = self.back(2)
        self.assertEqual(dec2["status"], LT.STATUS_REFUSED)
        self.assertEqual(self.sha(self.base), before)


# =======================================================================================
# п.3 — откат по номеру, байт в байт
# =======================================================================================
class TestRollbackByteForByte(_Base):

    def test_before_after_rollback_first_equals_third(self):
        sha1 = self.sha(self.base)
        dec = self.move(1, why=WHY)
        sha2 = self.sha(self.base)
        back = self.back(dec["n"])
        sha3 = self.sha(self.base)
        self.assertEqual(back["status"], LT.STATUS_ROLLED_BACK, back["card"])
        self.assertNotEqual(sha1, sha2)
        self.assertEqual(sha1, sha3, "база после отката не равна базе до переноса")
        self.assertTrue(LS.version(self.base).ok, "версия базы после отката не отвечает содержимому")
        self.assertIn("совпал", back["card"])
        self.assertEqual(self.back(dec["n"])["status"], LT.STATUS_REFUSED, "второй откат прошёл")
        self.assertEqual([m.act for m in LT.load_moves(self.base)],
                         [LT.ACT_TRANSFER, LT.ACT_UNTRANSFER])

    def test_rollback_refuses_when_the_base_moved_after_transfer(self):
        dec = self.move(1, why=WHY)
        LS.add(question="q", bot_answer="a", correct="ЧУЖОЙ урок после переноса", why="w",
               who="владелец", path=self.base, source=LS.SOURCE_TRAINER)
        before = self.sha(self.base)
        back = self.back(dec["n"])
        self.assertEqual(back["status"], LT.STATUS_REFUSED)
        self.assertIn("изменилась", back["card"])
        self.assertEqual(self.sha(self.base), before)

    def test_promoted_candidate_rolls_back_in_two_steps(self):
        sha1 = self.sha(self.base)
        dec = self.move(1, why="")
        n = dec["n"]
        self.assertTrue(LS.promote(n, why=WHY, who=OWNER, path=self.base).ok)
        self.assertEqual(self.back(n)["status"], LT.STATUS_REFUSED, "перенос откачен поверх перевода")
        self.assertTrue(LS.rollback(n, who=OWNER, path=self.base).ok)
        self.assertEqual(self.back(n)["status"], LT.STATUS_ROLLED_BACK)
        self.assertEqual(self.sha(self.base), sha1)

    def test_lesson_without_transfer_record_is_refused_in_words(self):
        dec = self.back(1)
        self.assertEqual(dec["status"], LT.STATUS_REFUSED)
        self.assertIn("записи о переносе", dec["card"])
        self.assertIn("нет", dec["card"])


# =======================================================================================
# п.4–5 — слова «бот отвечает» только после живого читателя; запись без читателя — ОТКАЗ
# =======================================================================================
class TestAnswerGate(_Base):

    def reader_elsewhere(self):
        other = os.path.join(self.box, "other_base.tsv")
        LS.add(question="q", bot_answer="a", correct="ДРУГАЯ-БАЗА правило", why="w", who="владелец",
               when="2026-09-03T12:00:00Z", path=other, source=LS.SOURCE_TRAINER)
        suggest.LESSON_BASE_PATH = other

    def test_written_but_reader_blind_is_a_refusal_not_a_success(self):
        """НАМЕРЕННОЕ СОСТОЯНИЕ: запись в базу проходит, а живой читатель читает другой файл."""
        self.reader_elsewhere()
        dec = self.move(1, why=WHY)
        self.assertEqual(dec["status"], LT.STATUS_NOT_IN_ANSWER, dec["card"])
        les = self.row(self.base, dec["n"])
        self.assertEqual(les.state, LS.STATE_ACTIVE, "запись не прошла — состояние не то, что меряем")
        self.assertEqual((dec["answer_before"], dec["answer_after"]), (0, 0))
        self.assertNotIn("бот отвечает по нему", dec["card"])
        self.assertIn("в ответ ещё НЕ вошёл", dec["card"])

    def test_reader_switched_off_is_a_refusal(self):
        suggest.LESSON_BASE_OFF = True
        dec = self.move(1, why=WHY)
        self.assertEqual(dec["status"], LT.STATUS_NOT_IN_ANSWER, dec["card"])
        self.assertIn("ВЫКЛЮЧЕНО", dec["card"])

    def test_prompt_not_built_is_not_a_success(self):
        def broken():
            raise RuntimeError("сборщик упал")
        dec = self.move(1, why=WHY, build=broken)
        self.assertEqual(dec["status"], LT.STATUS_NOT_IN_ANSWER, dec["card"])
        self.assertIn("НЕ СВЕРЕНО", dec["card"])

    def test_same_text_already_in_the_prompt_does_not_count_as_entry(self):
        """Строка того же вида уже стоит в промпте (из книги) — вхождением её не считаем."""
        bullet = "- (%s) %s" % (LS.now_stamp(NOW)[:10], SET_RULES[0])
        with open(self.book, "a", encoding="utf-8") as f:
            f.write("\n## Так НЕ говорим\n%s\n" % bullet)
        self.reader_elsewhere()
        dec = self.move(1, why=WHY)
        self.assertEqual((dec["answer_before"], dec["answer_after"]), (1, 1))
        self.assertEqual(dec["status"], LT.STATUS_NOT_IN_ANSWER, dec["card"])

    def test_cli_exit_codes_tell_success_refusal_and_written_not_read_apart(self):
        code, out = self.cli("--who", OWNER, "--from-set", "1", "--why", WHY, "--set", self.set,
                             "--base", self.base)
        self.assertEqual(code, LT.EXIT_OK, out)
        code, out = self.cli("--who", STRANGER, "--from-set", "2", "--why", WHY, "--set", self.set,
                             "--base", self.base)
        self.assertEqual(code, LT.EXIT_REFUSED, out)
        self.reader_elsewhere()
        code, out = self.cli("--who", OWNER, "--from-set", "2", "--why", WHY, "--set", self.set,
                             "--base", self.base)
        self.assertEqual(code, LT.EXIT_NOT_IN_ANSWER, out)
        self.assertNotEqual(code, LT.EXIT_OK)
        code, out = self.cli("--who", OWNER, "--from-set", "1", "--rollback", "4", "--base", self.base)
        self.assertEqual(code, LT.EXIT_REFUSED, "два движения за вызов прошли")


class TestPromoteCardTellsTheTruth(_Base):
    """Ложь 70x: перевод в базе, которую бот не читает, печатал «бот отвечает по нему»."""

    def promote_cli(self, path, n):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = lesson_promote.main(["--who", OWNER, "--promote", str(n), "--why", WHY,
                                        "--path", path])
        return code, out.getvalue()

    def test_promote_in_an_unread_base_says_not_in_answer_and_is_not_success(self):
        code, out = self.promote_cli(self.set, 3)          # кандидат #3 набора, набор бот не читает
        self.assertEqual(self.row(self.set, 3).state, LS.STATE_ACTIVE, "перевод не записан")
        self.assertEqual(code, lesson_promote.EXIT_NOT_IN_ANSWER, out)
        self.assertNotIn("бот отвечает по нему", out)
        self.assertIn("в ответ бота ещё НЕ вошёл", out)
        self.assertIn("урок откати 3", out, "след и откат из карточки пропали")

    def test_promote_in_the_read_base_keeps_the_success_words(self):
        n = LS.add_candidate(question="q", bot_answer="a", correct="КАНДИДАТ-БАЗЫ называй депозит",
                             who=OWNER, when="2026-09-20T10:00:00Z", path=self.base,
                             source=LS.SOURCE_TRAINER)
        code, out = self.promote_cli(self.base, n)
        self.assertEqual(code, lesson_promote.EXIT_OK, out)
        self.assertIn("бот отвечает по нему", out)

    def test_the_replaced_line_is_the_first_line_of_the_trainer_card(self):
        """Замок формы: дверь заменяет ПЕРВУЮ строку карточки `trainer._promote_lesson`. Уедет
        обещание в другую строку — этот тест покраснеет раньше, чем ложь вернётся."""
        n = LS.add_candidate(question="q", bot_answer="a", correct="ЗАМОК карточки", who=OWNER,
                             when="2026-09-20T10:00:00Z", path=self.base, source=LS.SOURCE_TRAINER)
        dec = trainer.promote_lesson(n, why=WHY, who=OWNER, may_write=allow, path=self.base)
        lines = dec["card"].split("\n")
        self.assertIn("бот отвечает", lines[0])
        self.assertFalse(any("бот отвечает" in ln for ln in lines[1:]))


# =======================================================================================
# форма двери
# =======================================================================================
class TestDoorShape(unittest.TestCase):

    def test_live_set_path_is_the_exam_live_lessons(self):
        import exam_show
        self.assertEqual(os.path.normcase(LT.LIVE_SET_PATH), os.path.normcase(exam_show.LIVE_LESSONS))

    def test_source_has_no_deleting_call_and_truncates_only_the_temp_file(self):
        with open(os.path.join(HERE, "lesson_transfer.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ("remove", "unlink", "truncate",
                                                                  "rmtree", "removedirs"):
                bad.append(node.lineno)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "open" and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and any(c in str(node.args[1].value) for c in "wx")):
                if not (isinstance(node.args[0], ast.Name) and node.args[0].id == "tmp"):
                    bad.append(node.lineno)
        self.assertEqual(bad, [])

    def test_mojibake_detector(self):
        good = "Цены называй только при ясных датах, депозит — вместе с ценой"
        self.assertFalse(LT.looks_mojibake(good))
        self.assertTrue(LT.looks_mojibake(good.encode("utf-8").decode("cp1251", errors="replace")))
        self.assertFalse(LT.looks_mojibake("Ask for dates once"))
        self.assertFalse(LT.looks_mojibake(""))


class TestReadCardsPrintNoText(_Base):

    def test_list_and_trace_name_numbers_not_texts(self):
        self.move(1, why=WHY)
        code, out = self.cli("--list", "--set", self.set, "--base", self.base)
        self.assertEqual(code, 0)
        for rule in SET_RULES:
            self.assertNotIn(rule, out)
        self.assertIn("урок бота #4", out)
        self.assertIn("можно переносить сейчас: 2", out)       # #2 и #3; #1 перенесён, #4 снят
        code, out = self.cli("--trace", "--base", self.base)
        self.assertIn("набор #1", out)


if __name__ == "__main__":
    unittest.main()
