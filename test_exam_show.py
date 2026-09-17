# -*- coding: utf-8 -*-
"""Набор витрины экзамена: показ сохранённого текста, право на тап, кандидат и откат.

БОЕВЫХ ФАЙЛОВ НЕ КАСАЕТСЯ НИ ОДИН ТЕСТ: и журнал вердиктов, и каталог черновиков подменяются
на временные, сеть не зовётся ни разу (отправитель всегда подставной), голова не зовётся вовсе.
"""

import json
import os
import shutil
import tempfile
import unittest

import exam_reshow
import exam_show
import pc_agent
import trainer_run

# Окно дат для тестов. Берётся ДОВОДОМ, а не живым `trainer_run.placeholders()`: живое окно ходит
# за таблицей периодов ради `when_cross`, а набор в сеть не ходит ни разу. Ключи — те же, что у
# живого окна; подставляет их ТА ЖЕ `trainer_run.substitute`, что и прогон, — своей копии
# подстановки у набора нет по построению (иначе он зеленел бы на собственной реализации).
PH = {"when": "с 6 по 11 октября", "when_sloppy": "с 6ого по 11ое октября",
      "when_1d": "с 6 по 7 октября", "when_month": "с 6 октября по 6 ноября",
      "when_en": "from October 6 to October 11", "when_cross": "с 28 октября по 3 ноября",
      "cross": {"text": "с 28 октября по 3 ноября"}, "year": "2026", "year_next": "2027"}


# Отпечаток ЖИВОГО корпуса — умолчание фикстуры с 12.09.2026. До этого дня снимок набора носил
# выдуманный отпечаток («aaaa…»), и это было безразлично: замок отпечатка стоял только у показа.
# Теперь его спрашивает и ЗАПИСЬ ВЕРДИКТА, поэтому снимок фикстуры обязан быть самосогласованным —
# иначе набор целиком мерил бы один и тот же отказ. Тесты, которым нужен РАСХОД, называют чужой
# отпечаток явно (их ровно два, и оба про расход).
def _live_corpus():
    return exam_show.corpus_fingerprint()


def _shot(case=1, total=17, corpus=None, commit="deadbee", rules="v1", hints=None):
    return {"case": case, "total": total, "name": "первый контакт", "lang": "ru",
            "question": "Здравствуйте! Хочу XMAX 300", "draft": "Здравствуйте! 307 ฿/день.",
            "note": "<<<QUOTE>>> NMAX — 307 ฿/день",
            "corpus": _live_corpus() if corpus is None else corpus, "commit": commit,
            "rules": rules, "built_at": "2026-09-11T00:00:00Z",
            "hints": [] if hints is None else list(hints)}


def _hint(obs, rule):
    return {"observation": obs, "rule": rule}


# Две подсказки фикстуры: с наблюдением и без него (второе — законный исход `split_pair`, когда
# модель не соблюла формат, и он обязан доезжать до кнопки живым).
HINTS = [_hint("назвал срок выдачи, которого не знает", "не называй сроков выдачи"),
         _hint("", "спрашивай даты аренды до цены")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exam_show_test_")
        self.shots = os.path.join(self.tmp, "shots")
        os.makedirs(self.shots)
        self.log = os.path.join(self.tmp, "verdicts.tsv")
        self.desk = os.path.join(self.tmp, "session.json")
        self.lessons = os.path.join(self.tmp, "lessons.tsv")
        self._shots_was, exam_show.SHOTS_DIR = exam_show.SHOTS_DIR, self.shots
        self._verd_was, exam_show.VERDICTS = exam_show.VERDICTS, self.log
        # СТОЛ И БАЗА УРОКОВ — ВО ВРЕМЕННОЕ МЕСТО. Без этих двух подмен набор писал бы в боевой
        # `exam_session.json` и в боевую таблицу уроков, по которой бот отвечает клиентам.
        self._sess_was, exam_show.SESSION = exam_show.SESSION, self.desk
        self._less_was, exam_show.LESSON_PATH = exam_show.LESSON_PATH, self.lessons
        self.addCleanup(self._restore)

    def _restore(self):
        exam_show.SHOTS_DIR = self._shots_was
        exam_show.VERDICTS = self._verd_was
        exam_show.SESSION = self._sess_was
        exam_show.LESSON_PATH = self._less_was
        shutil.rmtree(self.tmp, ignore_errors=True)

    def critic(self, hints=None):
        """ПОДСТАВНОЙ КРИТИК. Голову набор не зовёт ни разу и ни одной веткой: живой критик ходит
        в LLM, а подсказки — данные, и проверять на них надо разбор, а не удачу модели."""
        return lambda q, a, tr="": list(HINTS if hints is None else hints)

    def lesson_rows(self):
        """Строки временной базы уроков (шапка не в счёт). Файла нет — пусто."""
        try:
            with open(self.lessons, encoding="utf-8") as f:
                return [ln for ln in f.read().splitlines()[1:] if ln.strip()]
        except OSError:
            return []

    def raw(self):
        """Байты журнала. Отдельным методом ради закрытия файла: ResourceWarning в выводе гейта
        неотличим от настоящей новости, и своя же грязь читалась бы как чужая поломка."""
        with open(self.log, encoding="utf-8") as f:
            return f.read()

    def put_shot(self, shot):
        with open(exam_show.shot_path(shot["case"]), "w", encoding="utf-8") as f:
            json.dump(shot, f, ensure_ascii=False)
        return shot

    def sender(self):
        """Подставной отправщик: копит вызовы, в сеть не ходит."""
        calls = []

        def send(text, chat, markup=None):
            calls.append({"text": text, "chat": chat, "markup": markup})
            return ("chat:%s" % chat, True, "777")
        send.calls = calls
        return send

    def fresh_agent(self):
        # `**_` — не вкусовщина: боевой `show` зовёт замок С ТОКЕНАМИ карточки (`tokens=…`), и
        # подставной ловец, не принимающий их, скрыл бы разъезд подписи молча.
        return lambda **_: (True, "агент свеж (подставной)")


# ───────────────────────── ПОКАЗЫВАЕТСЯ СОХРАНЁННОЕ, А НЕ СВЕЖЕЕ ─────────────────────────

class TestFrozenTextIsWhatIsShown(Base):
    def test_show_never_reaches_the_head(self):
        """У показа НЕТ ДОРОГИ к голове: `trainer_run` не упомянут в теле `show` ни разу.

        Проверка стои́т на ИСХОДНИКЕ, а не на «мы туда не ходили»: подменённый раннер зеленел бы
        и в коде, который зовёт голову при промахе кеша."""
        import inspect
        body = inspect.getsource(exam_show.show)
        self.assertNotIn("trainer_run", body)
        self.assertNotIn("run_case", body)
        self.assertNotIn("generate", body)

    def test_only_freeze_calls_the_head(self):
        """Голову зовёт РОВНО одна функция модуля — та, что кладёт черновик на диск."""
        import inspect
        src = inspect.getsource(exam_show)
        self.assertEqual(src.count("import trainer_run"), 1)
        self.assertIn("import trainer_run", inspect.getsource(exam_show.freeze))

    def test_second_show_repeats_the_very_same_text(self):
        """Повторный показ отдаёт ТОТ ЖЕ текст побуквенно — вердикт нельзя привязать к тексту,
        которого больше нет."""
        shot = self.put_shot(_shot(corpus=exam_show.corpus_fingerprint()))
        send = self.sender()
        for _ in range(2):
            ok, _msg = exam_show.show(shot["case"], sender=send, agent_fn=self.fresh_agent())
            self.assertTrue(ok)
        self.assertEqual(send.calls[0]["text"], send.calls[1]["text"])
        self.assertIn(shot["draft"], send.calls[0]["text"])

    def test_freeze_does_not_overwrite_an_existing_shot(self):
        """Второй `--freeze` НА ТОМ ЖЕ КОММИТЕ не пересобирает: иначе «покажи ещё раз» молча
        меняло бы судимый текст. Слот опознаётся коммитом сборки (12.09.2026)."""
        self.put_shot(_shot(commit=exam_show.head_commit()))
        called = []

        def runner(case):
            called.append(case)
            return {"draft": "другой ответ", "note": ""}
        ok, why, _ = exam_show.freeze(1, runner=runner, ph=PH, critic=self.critic())
        self.assertFalse(ok)
        self.assertEqual(called, [], "голова не должна была зваться вовсе")
        self.assertIn("уже собран", why)
        self.assertEqual(exam_show.load_shot(1)["draft"], _shot()["draft"])

    def test_freeze_saves_the_three_fingerprints(self):
        ok, path, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ", "note": "записка"}, ph=PH,
            critic=self.critic())
        self.assertTrue(ok, path)
        for key in ("corpus", "commit", "rules", "built_at"):
            self.assertIn(key, shot)
        self.assertEqual(shot["corpus"], exam_show.corpus_fingerprint())
        self.assertTrue(os.path.exists(path))

    def test_new_commit_gets_its_own_slot_and_the_old_one_survives(self):
        """ВТОРОЙ СЛОТ, КЛЮЧУЕМЫЙ КОММИТОМ (12.09.2026). Снимок старого кода остаётся на диске
        БАЙТ-В-БАЙТ, новый ложится рядом, показ берёт НОВЕЙШИЙ и называет его коммит в карточке.

        Зачем правило: до 12.09 у кейса был один слот, и `freeze` отказывал по ФАКТУ файла —
        показать кейс на новом коде было нельзя вовсе, единственное место занято старым текстом."""
        old = self.put_shot(_shot(commit="deadbee"))
        old_path = exam_show.shot_path(1)
        ok, path, new = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ НОВОГО кода", "note": "новая записка"}, ph=PH,
            critic=self.critic())
        self.assertTrue(ok, path)
        self.assertNotEqual(path, old_path, "новый снимок лёг в тот же файл — старый затёрт")
        self.assertTrue(os.path.exists(old_path), "старый слот исчез с диска")
        with open(old_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["draft"], old["draft"], "старый слот изменён")
        self.assertEqual(new["commit"], exam_show.head_commit())
        # показ берёт новейший — и в карточке стои́т ЕГО коммит, а не коммит прежнего слота
        self.assertEqual(exam_show.load_shot(1)["draft"], "ответ НОВОГО кода")
        self.assertEqual(len(exam_show.shot_slots(1)), 2)
        card = exam_show.card_text(exam_show.load_shot(1), len(exam_show.shot_slots(1)))
        self.assertIn(exam_show.head_commit(), card)
        self.assertNotIn("deadbee", card)
        self.assertIn("НОВЕЙШИЙ снимок кейса (всего слотов 2)", card)
        # ссылка в журнал вердиктов ведёт на ПОКАЗАННЫЙ слот, а не на имя кейса вообще
        self.assertIn(os.path.basename(path), exam_show.shot_ref(1))

    def test_newest_is_by_built_at_not_by_file_mtime(self):
        """Новизна судится по `built_at` СНИМКА, а не по mtime файла: mtime двигает любое касание
        диска (копия, checkout, антивирус), а момент сборки принадлежит самому тексту."""
        a = _shot(commit="aaaaaaa")
        a["built_at"], a["draft"] = "2026-09-11T00:00:00Z", "старый"
        b = _shot(commit="bbbbbbb")
        b["built_at"], b["draft"] = "2026-09-12T00:00:00Z", "новый"
        for shot in (b, a):                      # b кладём ПЕРВЫМ — mtime у него старше
            with open(exam_show.shot_path(1, shot["commit"]), "w", encoding="utf-8") as f:
                json.dump(shot, f, ensure_ascii=False)
        self.assertEqual(exam_show.load_shot(1)["draft"], "новый")
        self.assertEqual([s["commit"] for _p, s in exam_show.shot_slots(1)],
                         ["bbbbbbb", "aaaaaaa"])

    def test_empty_draft_is_not_saved_as_a_shot(self):
        """Молчащая голова не смеет оставить пустой черновик — судить было бы нечего."""
        ok, why, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "   ", "note": "", "unknown": "голова молчала"}, ph=PH,
            critic=self.critic())
        self.assertFalse(ok)
        self.assertIsNone(shot)
        self.assertIn("голова молчала", why)
        self.assertFalse(os.path.exists(exam_show.shot_path(1)))


# ──────────── ВОПРОС В КАРТОЧКЕ — ТОТ ЖЕ, ЧТО ВИДЕЛА ГОЛОВА (заведено 12.09.2026) ────────────
# Живой дефект: в показанной владельцу карточке кейса 1 стояло «…XMAX 300 {when_sloppy}…», а в
# ответе бота ниже — сами даты «с 6 по 11 октября». Подстановка в прогоне была, в показе — нет:
# `freeze` клал в снимок СЫРУЮ строку корпуса. Карточка при этом не врала — она честно печатала
# то, что лежало в снимке. Поэтому чинится снимок, а не карточка.

class TestTheQuestionIsTheOneTheHeadSaw(Base):
    def corpus(self, cases):
        """Свой корпус во временном каталоге — боевого `trainer_cases.json` не касаемся ничем."""
        p = os.path.join(self.tmp, "cases.json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"cases": cases}, f, ensure_ascii=False)
        return p

    TEMPLATED = {"id": 1, "name": "с шаблоном", "lang": "ru",
                 "lines": ["Здравствуйте! Хочу XMAX 300 {when_sloppy}, залог паспортом"]}
    PLAIN = {"id": 2, "name": "без шаблона", "lang": "ru",
             "lines": ["Требуется ли депозит?"]}

    def frozen(self, case):
        path = self.corpus([case])
        ok, where, shot = exam_show.freeze(
            case["id"], cases_path=path, runner=lambda c: {"draft": "ответ", "note": "записка"},
            ph=PH, critic=self.critic())
        self.assertTrue(ok, where)
        return shot

    def test_the_shot_carries_the_dates_and_not_the_template(self):
        """ПОСЛЕ правки: в снимок ложится текст с подставленными датами, шаблона не остаётся."""
        shot = self.frozen(self.TEMPLATED)
        self.assertIn("с 6ого по 11ое октября", shot["question"])
        self.assertNotIn("{", shot["question"])
        self.assertNotIn("when_sloppy", shot["question"])

    def test_the_raw_corpus_line_is_kept_and_not_lost(self):
        """Сырая строка НЕ ТЕРЯЕТСЯ — она рядом, ключом `question_raw`: по ней видно, из какого
        кейса собран вопрос, и подстановку всегда можно пересчитать."""
        shot = self.frozen(self.TEMPLATED)
        self.assertEqual(shot["question_raw"], self.TEMPLATED["lines"][0])
        self.assertNotEqual(shot["question_raw"], shot["question"])

    def test_a_case_without_a_template_is_byte_for_byte_the_same(self):
        """ОТРИЦАТЕЛЬНАЯ сторона: кейсу без шаблона правка не меняет ни байта."""
        shot = self.frozen(self.PLAIN)
        self.assertEqual(shot["question"], self.PLAIN["lines"][0])
        self.assertEqual(shot["question"], shot["question_raw"])

    def test_the_card_repeats_the_shot_byte_for_byte(self):
        """ОДИН ИСТОЧНИК ПРАВДЫ: карточка не подрисовывает ничего — строка вопроса в ней совпадает
        со строкой снимка побайтно."""
        shot = self.frozen(self.TEMPLATED)
        card = exam_show.card_text(shot)
        self.assertIn(shot["question"], card)
        line = [ln for ln in card.splitlines() if ln == shot["question"]]
        self.assertEqual(len(line), 1, "вопрос обязан стоять в карточке ОТДЕЛЬНОЙ строкой, дословно")

    def test_one_window_goes_both_to_the_head_and_into_the_question(self):
        """Окно дат считается РОВНО ОДИН РАЗ и идёт в оба места — голове и в записанный вопрос.

        Два отдельных `placeholders()` разъехались бы на границе суток и месяца, и снимок держал бы
        вопрос, которого голова не видела. Подставной прогонщик выдаёт РАЗНОЕ окно на каждый вызов —
        расхождение стало бы видно текстом, а не рассуждением."""
        import sys as _sys
        import types
        fake = types.ModuleType("trainer_run")
        state = {"n": 0, "seen": []}

        def placeholders():
            state["n"] += 1
            return {"when_sloppy": "ОКНО-%d" % state["n"]}

        def run_case(case, ph):
            state["seen"].append(ph)
            return {"draft": "ответ", "note": "записка"}

        fake.placeholders = placeholders
        fake.run_case = run_case
        fake.substitute = trainer_run.substitute        # подстановка — НАСТОЯЩАЯ, не копия
        fake.build_transcript = trainer_run.build_transcript      # и сборка реплик тоже настоящая
        real = _sys.modules.get("trainer_run")
        _sys.modules["trainer_run"] = fake
        try:
            ok, where, shot = exam_show.freeze(1, cases_path=self.corpus([self.TEMPLATED]),
                                               critic=self.critic())
        finally:
            if real is None:
                _sys.modules.pop("trainer_run", None)
            else:
                _sys.modules["trainer_run"] = real
        self.assertTrue(ok, where)
        self.assertEqual(state["n"], 1, "окно посчитано больше одного раза — оно разъедется")
        self.assertIn("ОКНО-1", shot["question"])
        self.assertEqual(state["seen"][0]["when_sloppy"], "ОКНО-1")

    def test_the_substituter_is_the_runners_own(self):
        """Второй подстановки на полосе нет: показ зовёт ТУ ЖЕ функцию, что готовит реплики голове.

        Проверка на исходнике — своя копия `format(**ph)` зеленела бы на любом поведенческом тесте
        ровно до первого расхождения, а расходятся такие копии молча."""
        import inspect
        self.assertIn("trainer_run.substitute", inspect.getsource(exam_show.freeze))
        self.assertNotIn("format(", inspect.getsource(exam_show.question_of))
        self.assertIn("substitute(raw, ph)", inspect.getsource(trainer_run.build_transcript))

    def test_no_template_survives_in_the_card_on_any_of_the_seventeen(self):
        """ЗАМОК ЧИСЛОМ на ЖИВОМ корпусе: ни в одной из карточек не остаётся фигурных скобок.

        Мерить на одном кейсе мало — шаблонов в корпусе пять видов, и промах любого из них даёт
        владельцу ту же небрежность на другом кейсе."""
        cases = exam_show.load_cases()
        self.assertEqual(len(cases), 17, "корпус переехал — пересчитать замок")
        raw_with_template, card_with_template = 0, 0
        for case in cases:
            raw = exam_show.question_of(case)
            if "{" in raw:
                raw_with_template += 1
            shot = _shot(case=case.get("id"), total=len(cases)) | {
                "question": trainer_run.substitute(raw, PH), "note": "записка", "draft": "ответ"}
            if "{" in exam_show.card_text(shot):
                card_with_template += 1
        self.assertEqual(raw_with_template, 10, "в корпусе 10 кейсов из 17 несут шаблон в первой реплике")
        self.assertEqual(card_with_template, 0, "в карточке не смеет остаться НИ ОДНОГО шаблона")


# ────────────────────── ПЕРЕПОКАЗ: та же карточка + названное отличие ──────────────────────

class TestReshow(Base):
    def test_a_reshow_without_a_named_difference_sends_nothing(self):
        """Перепоказ без отличия — вторая одинаковая карточка: владелец ищет разницу сам."""
        self.put_shot(_shot(corpus=exam_show.corpus_fingerprint()))
        send = self.sender()
        ok, msg = exam_reshow.reshow(1, "   ", sender=send, agent_fn=self.fresh_agent())
        self.assertFalse(ok)
        self.assertEqual(send.calls, [], "наружу не должно уйти ничего")
        self.assertIn("отличия", msg)

    def test_the_header_names_the_reshow_and_the_living_buttons(self):
        head = exam_reshow.header(1, "даты вместо шаблона")
        self.assertIn("ПЕРЕПОКАЗ", head)
        self.assertIn("кнопки рабочие", head)
        self.assertIn("даты вместо шаблона", head)

    def test_the_reshow_carries_the_whole_card_and_the_same_buttons(self):
        """Карточка идёт ЦЕЛИКОМ и одним сообщением, кнопки — те же, что у обычного показа."""
        shot = self.put_shot(_shot(corpus=exam_show.corpus_fingerprint()))
        send = self.sender()
        ok, _msg = exam_reshow.reshow(1, "отличие", sender=send, agent_fn=self.fresh_agent())
        self.assertTrue(ok)
        self.assertEqual(len(send.calls), 1, "перепоказ РОВНО один")
        text = send.calls[0]["text"]
        # счёт «пройдено K» — часть карточки с 12.09.2026, и перепоказ несёт её наравне с показом:
        # сравнение с карточкой БЕЗ счёта расходилось бы на одну строку шапки
        self.assertIn(exam_show.card_text(shot, passed=0), text)
        self.assertTrue(text.startswith(exam_reshow.MARK))
        self.assertEqual(send.calls[0]["markup"], exam_show.markup(1, shot))

    def test_the_reshow_never_reaches_the_head(self):
        """У перепоказа нет дороги к голове ни одной веткой — проверка на исходнике модуля."""
        import inspect
        src = inspect.getsource(exam_reshow)
        for word in ("trainer_run", "run_case", "generate", "freeze"):
            self.assertNotIn(word, src)

    def test_the_reshow_keeps_every_lock_of_the_show(self):
        """Замки показа работают и на перепоказе: чужой адрес и несвежий ловец отказывают."""
        self.put_shot(_shot(corpus=exam_show.corpus_fingerprint()))
        send = self.sender()
        ok, msg = exam_reshow.reshow(1, "отличие", sender=send, chat=-1, agent_fn=self.fresh_agent())
        self.assertFalse(ok)
        self.assertEqual(send.calls, [])
        ok, msg = exam_reshow.reshow(1, "отличие", sender=send,
                                     agent_fn=lambda **_: (False, "агент СТАРШЕ кода кнопки"))
        self.assertFalse(ok)
        self.assertEqual(send.calls, [], "несвежий ловец обязан остановить и перепоказ")


# ───────────────────────────── ОДИН КЕЙС И ОДИН АДРЕС ────────────────────────────────────

class TestOneCaseOneAddress(Base):
    def test_card_carries_all_five_parts(self):
        shot = _shot()
        text = exam_show.card_text(shot)
        self.assertIn("кейс 1 из 17", text)
        self.assertIn(shot["question"], text)
        self.assertIn(shot["draft"], text)
        self.assertIn("ПОЧЕМУ так", text)
        self.assertIn(shot["note"][:20], text)

    def test_reason_is_never_invented(self):
        """Записки нет → причина названа ПУСТОЙ словами, а не сочинена из ответа бота."""
        line = exam_show.reason_line(_shot() | {"note": ""})
        self.assertIn("причина не записана", line)
        self.assertNotIn("307", line)

    def test_any_chat_but_the_trainer_group_is_refused(self):
        self.put_shot(_shot(corpus=exam_show.corpus_fingerprint()))
        send = self.sender()
        for alien in (-1001234567, 0, 777):
            ok, msg = exam_show.show(1, chat=alien, sender=send, agent_fn=self.fresh_agent())
            self.assertFalse(ok, alien)
            self.assertIn(str(exam_show.TRAINER_CHAT), msg)
        self.assertEqual(send.calls, [], "в чужой чат не смеет уйти ни одно сообщение")

    def test_the_pinned_address_is_the_trainer_group(self):
        self.assertEqual(exam_show.TRAINER_CHAT, -5193185299)

    def test_show_without_a_saved_shot_refuses_and_sends_nothing(self):
        send = self.sender()
        ok, msg = exam_show.show(9, sender=send, agent_fn=self.fresh_agent())
        self.assertFalse(ok)
        self.assertIn("--freeze", msg)
        self.assertEqual(send.calls, [])

    def test_corpus_drift_stops_the_show(self):
        """Черновик с чужим отпечатком корпуса не показывается: кейс мог поменяться под ним."""
        self.put_shot(_shot(corpus="0000000000000000"))
        send = self.sender()
        ok, msg = exam_show.show(1, sender=send, agent_fn=self.fresh_agent())
        self.assertFalse(ok)
        self.assertIn("0000000000000000", msg)
        self.assertEqual(send.calls, [])


# ────────── ЗАМОК: ЖИВОЙ ЛОВЕЦ ОБЯЗАН РАЗБИРАТЬ КНОПКИ ИМЕННО ЭТОЙ КАРТОЧКИ ─────────────
#
# Предмет замка поменялся 12.09.2026 (задание 52-d): было «оба файла кнопки старше процесса»,
# стало «pc_agent.py старше процесса И его разбор принимает каждый колбэк карточки». Причина
# названа числами в шапке `exam_show` и в двух артефактах за 12.09: прежний состав замыкания
# остановил показ дважды (отставание 1097 с и 1575 с) на `exam_show.py` — файле, которого живой
# агент в себе НЕ НЕСЁТ ВОВСЕ (тап спавнит дверь отдельным процессом, `pc_agent.py:1077`).

# Разбор ПРЕЖНЕГО агента: до коммита 56ca76a он знал ровно две кнопки. Фикстура историческая, а
# не выдуманная — ровно такой разбор и жил в процессе, когда карточка уже печатала шесть кнопок.
NARROW_PARSE = 'EXAM_CB_RE = re.compile(r"^exam:(ok|no):([0-9]{1,3})$")'


class TestStaleAgentLock(Base):
    def _lock(self, pid=4242, started=1000.0):
        p = os.path.join(self.tmp, "pc_agent.lock")
        with open(p, "w", encoding="utf-8") as f:
            f.write("%d\n" % pid)
            f.write(json.dumps({"pid": pid, "started": started, "script": "pc_agent.py"}))
        return p

    def _cbs(self, case_id=1, hints=3):
        """Колбэки, которые НАПЕЧАТАЕТ карточка снимка с таким числом подсказок."""
        made = [_hint("наблюдение %d" % i, "правило %d" % i) for i in range(1, hints + 1)]
        return exam_show.card_callbacks(case_id, _shot(hints=made))

    def _live_parse(self, _path=None):
        """Разбор ЖИВОГО агента, прочитанный с диска (read-only, файла не касаемся)."""
        with open("pc_agent.py", encoding="utf-8", errors="replace") as f:
            return f.read()

    # ─── (1) ВРЕМЯ: разбор правлен после старта процесса ───

    def test_agent_older_than_the_button_forbids_the_show(self):
        lock = self._lock(started=1000.0)
        carries, why = exam_show.agent_carries_button(
            lock, mtime_fn=lambda p: 2000.0, callbacks=self._cbs())
        self.assertIs(carries, False)
        self.assertIn("СТАРШЕ", why)
        self.assertIn("1000 с", why, "отставание обязано быть названо ЧИСЛОМ")
        self.assertIn("обновись", why, "цена снятия обязана быть названа словами")

    def test_time_lock_watches_the_parser_file_only(self):
        """СУТЬ ПРАВКИ 52-d: свежесть меряется по файлу, живущему В ПАМЯТИ процесса, и только.

        Наш собственный файл из сравнения по времени убран: агент его не импортирует, ребёнок
        читает его с диска на каждом тапе. Контрфакт прежнего состава стоит прямо в проверке —
        останься `exam_show.py` в замыкании, запрошенный ниже mtime 10^9 дал бы отказ."""
        self.assertEqual(exam_show.BUTTON_CLOSURE, ("pc_agent.py",))
        asked = []

        def mtime(path):
            asked.append(os.path.basename(path))
            return 1e9 if path.endswith("exam_show.py") else 2000.0

        lock = self._lock(started=9000.0)
        carries, why = exam_show.agent_carries_button(
            lock, mtime_fn=mtime, callbacks=self._cbs(), read_fn=self._live_parse)
        self.assertIs(carries, True, why)
        self.assertEqual(asked, ["pc_agent.py"],
                         "у замка нет права спрашивать возраст файла, которого агент не несёт")

    # ─── (2) СОДЕРЖИМОЕ: префикс, которого разбор не принимает ───

    def test_prefix_the_parser_rejects_forbids_the_show(self):
        """ГЛАВНЫЙ ОТРИЦАТЕЛЬНЫЙ: файлы свежие, а кнопок агент не разбирает → НЕТ, с числами."""
        lock = self._lock(started=9000.0)
        cbs = self._cbs(hints=3)
        self.assertEqual(len(cbs), 6, "карточка с 3 подсказками печатает 6 кнопок")
        carries, why = exam_show.agent_carries_button(
            lock, mtime_fn=lambda p: 2000.0, callbacks=cbs, read_fn=lambda p: NARROW_PARSE)
        self.assertIs(carries, False)
        # 5 из 6: прежний разбор принимает РОВНО «exam:ok:1», а «exam:no:…» карточка с
        # подсказками не печатает вовсе — её второй ряд занят номерами.
        self.assertIn("НЕ принимает 5 из 6", why, "отказ обязан быть назван ЧИСЛОМ")
        for dead in ("exam:h1:1", "exam:h2:1", "exam:h3:1", "exam:own:1", "exam:go:1"):
            self.assertIn(dead, why, "непонятая кнопка обязана быть названа по имени")
        self.assertNotIn("СТАРШЕ", why, "это отказ ПО СОДЕРЖИМОМУ, а не по времени")

    def test_a_case_number_wider_than_the_parser_also_refuses(self):
        """Разбор берёт 1–3 цифры номера; четырёхзначный кейс — тот же класс, другая дорога."""
        lock = self._lock(started=9000.0)
        carries, why = exam_show.agent_carries_button(
            lock, mtime_fn=lambda p: 2000.0, callbacks=self._cbs(case_id=1234),
            read_fn=self._live_parse)
        self.assertIs(carries, False)
        self.assertIn("exam:ok:1234", why)

    def test_the_live_parser_accepts_every_button_the_live_card_prints(self):
        """КОНТРАКТ ДВУХ ЖИВЫХ ФАЙЛОВ, и он здесь главный на будущее.

        Сверяется не литерал, а печать `markup` при ПОЛНОМ наборе подсказок (`HINTS_MAX`) против
        разбора из живого `pc_agent.py`. Разъедутся — покраснеет он, а не владелец, чей тап молча
        не дошёл до двери."""
        rx, words = exam_show.agent_parse()
        self.assertIsNotNone(rx, words)
        cbs = self._cbs(hints=exam_show.HINTS_MAX)
        self.assertEqual(len(cbs), exam_show.HINTS_MAX + 3)
        bad = [t for t in cbs if not rx.match(t)]
        self.assertEqual(bad, [], "живой агент не разбирает свои же кнопки: %s (разбор %s)"
                         % (bad, words))

    # ─── (3) НЕИЗВЕСТНО: третий исход, и он тоже запрещает ───

    def test_third_outcome_is_unknown_and_it_also_forbids(self):
        """Нет лока / нет момента старта / нет карточки / нет разбора → НЕИЗВЕСТНО."""
        carries, why = exam_show.agent_carries_button(os.path.join(self.tmp, "нет-такого"))
        self.assertIsNone(carries)
        self.assertIn("лока", why)
        legacy = os.path.join(self.tmp, "legacy.lock")
        with open(legacy, "w", encoding="utf-8") as f:
            f.write("4242\n")
        carries2, _ = exam_show.agent_carries_button(legacy)
        self.assertIsNone(carries2)
        lock = self._lock(started=9000.0)
        # карточка не предъявлена — «ДА» отсюда не выходит
        carries3, why3 = exam_show.agent_carries_button(lock, mtime_fn=lambda p: 2000.0)
        self.assertIsNone(carries3)
        self.assertIn("кнопок на сверку 0", why3)
        # разбор переехал из файла — сверять не с чем
        carries4, why4 = exam_show.agent_carries_button(
            lock, mtime_fn=lambda p: 2000.0, callbacks=self._cbs(),
            read_fn=lambda p: "# разбор уехал в другой модуль\n")
        self.assertIsNone(carries4)
        self.assertIn("EXAM_CB_RE", why4)
        self.put_shot(_shot(corpus=exam_show.corpus_fingerprint()))
        send = self.sender()
        for verdict in (False, None):
            ok, msg = exam_show.show(1, sender=send, agent_fn=lambda **_: (verdict, "почему-то"))
            self.assertFalse(ok)
            self.assertIn("НЕИЗВЕСТНО" if verdict is None else "НЕТ", msg)
        self.assertEqual(send.calls, [], "при недоказанном ловце наружу не уходит ничего")

    # ─── сквозняк: отказ по содержимому останавливает ЖИВОЙ показ, а не только функцию ───

    def test_show_sends_nothing_when_the_parser_is_narrow(self):
        self.put_shot(_shot(corpus=exam_show.corpus_fingerprint(), hints=HINTS))
        lock = self._lock(started=9000.0)
        send = self.sender()
        ok, msg = exam_show.show(
            1, sender=send,
            agent_fn=lambda **kw: exam_show.agent_carries_button(
                lock, mtime_fn=lambda p: 2000.0, read_fn=lambda p: NARROW_PARSE, **kw))
        self.assertFalse(ok)
        self.assertIn("НЕ принимает", msg)
        self.assertEqual(send.calls, [], "непонятая кнопка наружу не уходит")

    def test_show_hands_the_lock_the_real_card_buttons(self):
        """Замок судит колбэки ЭТОГО снимка: число подсказок задаёт предмет сверки."""
        self.put_shot(_shot(corpus=exam_show.corpus_fingerprint(), hints=HINTS))
        seen = []

        def spy(callbacks=None, **_):
            seen.append(list(callbacks or []))
            return True, "подставной"

        ok, _msg = exam_show.show(1, sender=self.sender(), agent_fn=spy)
        self.assertTrue(ok)
        self.assertEqual(seen, [["exam:ok:1", "exam:h1:1", "exam:h2:1",
                                 "exam:own:1", "exam:go:1"]])


# ──────────────────────────── ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ У ТАПА ───────────────────────────────

class TestFourNegatives(Base):
    def test_no_right_no_record(self):
        """Без права — отказ, и журнала не появляется ВОВСЕ (не пустой, а несозданный)."""
        self.put_shot(_shot())
        ok, msg = exam_show.tap(1, "ok", "@чужой", right_fn=lambda who: False)
        self.assertFalse(ok)
        self.assertIn("не вправе", msg)
        self.assertFalse(os.path.exists(self.log))

    def test_the_refusal_differs_from_the_permit(self):
        """Ответы сравниваются МЕЖДУ СОБОЙ: проверка одного слова отказа зеленела бы и на
        двери, которая права не спрашивает вовсе."""
        self.put_shot(_shot())
        _, denied = exam_show.tap(1, "ok", "@чужой", right_fn=lambda who: False)
        _, allowed = exam_show.tap(1, "ok", "@свой", right_fn=lambda who: True)
        self.assertNotEqual(denied, allowed)

    def test_unknown_verdict_word_is_refused(self):
        """Слово вердикта — ЗАКРЫТАЯ таблица из двух ключей; всё прочее отказ, а не «наверное да»."""
        self.put_shot(_shot())
        for word in ("yes", "верно", "", None, "промолчу", "ok;no"):
            ok, _ = exam_show.tap(1, word, "@свой", right_fn=lambda who: True)
            self.assertFalse(ok, word)
        self.assertFalse(os.path.exists(self.log))

    def test_whitespace_and_case_around_a_known_word_are_tolerated(self):
        """Пробел и регистр вокруг ключа прощаются НАМЕРЕННО: ключ приходит из кнопки, а руками
        его набирают с консоли, и «OK » — это тот же вердикт, а не второй.

        КЕЙСЫ РАЗНЫЕ (правка 12.09.2026). Прежняя редакция била тремя словами по ОДНОМУ кейсу, и
        сегодня это меряло бы не прощение пробела, а замок «одна запись на кейс»: второй тап
        отказал бы по причине, к регистру отношения не имеющей."""
        for case, word in ((1, " ok"), (2, "OK"), (3, "No ")):
            self.put_shot(_shot(case=case))
            ok, msg = exam_show.tap(case, word, "@свой", right_fn=lambda who: True,
                                    sender=self.sender(), agent_fn=self.fresh_agent())
            self.assertTrue(ok, "%s → %s" % (word, msg))

    def test_tap_without_a_frozen_text_is_refused(self):
        """Вердикт без текста, к которому он относится, непроверяем — потому не пишется."""
        ok, msg = exam_show.tap(4, "no", "@свой", right_fn=lambda who: True)
        self.assertFalse(ok)
        self.assertIn("черновика нет", msg)
        self.assertFalse(os.path.exists(self.log))

    def test_the_right_is_asked_before_the_verdict_word(self):
        """Порядок выбран, а не случился: чужой с кривым словом слышит про ПРАВО, а не про слово."""
        self.put_shot(_shot())
        _, msg = exam_show.tap(1, "мусор", "@чужой", right_fn=lambda who: False)
        self.assertIn("не вправе", msg)


# ───────────────────── КАНДИДАТ: АВТОР, ВРЕМЯ, НОМЕР, ОТКАТ ─────────────────────────────

class TestCandidateRecord(Base):
    def setUp(self):
        super().setUp()
        self.put_shot(_shot(commit="abc1234", rules="v7"))

    def tap(self, verdict="ok", who="@filipp", case=1):
        return exam_show.tap(case, verdict, who, right_fn=lambda w: True,
                             sender=self.sender(), agent_fn=self.fresh_agent())

    def test_record_carries_all_seven_parts(self):
        ok, msg = self.tap()
        self.assertTrue(ok, msg)
        rows = exam_show.load_verdicts(self.log)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["номер"], 1)
        self.assertEqual(r["состояние"], "кандидат")
        self.assertEqual(r["автор"], "@filipp")
        self.assertEqual(r["кейс"], "1")
        self.assertEqual(r["из"], "17", "«кейс из скольких» — одна часть, а не две: без знаменателя "
                                        "вердикт не привязан к размеру корпуса, по которому вынесен")
        self.assertEqual(r["вердикт"], "верно")
        self.assertEqual(r["коммит"], "abc1234")
        self.assertEqual(r["корпус"], exam_show.corpus_fingerprint())
        self.assertEqual(r["правила"], "v7")
        self.assertTrue(r["время"].endswith("Z"))

    def test_the_record_points_at_the_very_draft_that_was_shown(self):
        """Ссылка на черновик — девятая часть записи, и до 12.09 её не сторожил ни один тест.

        Без неё вердикт «верно» указывает на кейс, но не на ТЕКСТ: черновик кейса один, однако
        доказать это можно только ссылкой в самой строке. Сравнение идёт с `shot_ref` того же
        кейса, а не с концом имени файла: проверка «оканчивается на case-1.json» зеленела бы и на
        ссылке в чужой каталог — ровно то, что случается, когда каталог черновиков подменён (здесь
        он и подменён, на временный с ДРУГОГО тома).

        Заодно названа НЕСИММЕТРИЧНОСТЬ, найденная этой же проверкой: запись экранирует поле
        (`lesson_store.esc`), а `load_verdicts` обратно НЕ разэкранирует — то есть в строке лежит
        `C:\\\\Users\\\\…`, а не `C:\\Users\\…`. На путях Windows это видно глазом, и читатель
        журнала обязан звать `unesc` сам. Проверка сторожит ОБА конца, чтобы починка любого из них
        не прошла молча."""
        import lesson_store
        self.tap()
        r = exam_show.load_verdicts(self.log)[0]
        self.assertTrue(r["черновик"], "пустая ссылка — это вердикт без текста, к которому вынесен")
        self.assertEqual(r["черновик"], exam_show._esc(exam_show.shot_ref(1)))
        self.assertEqual(lesson_store.unesc(r["черновик"]), exam_show.shot_ref(1),
                         "разэкранированная ссылка обязана указывать на показанный черновик")

    def test_a_missing_proof_is_named_and_not_left_blank(self):
        """Нет коммита в черновике → в строке стои́т СЛОВО, а не пустая графа.

        Пустая графа среди трёх опор вердикта читается как «коммита не было», хотя значит «мы его
        не записали», и отличить одно от другого потом нечем."""
        os.remove(exam_show.shot_path(1))
        self.put_shot(_shot() | {"commit": "", "rules": None})
        self.tap()
        r = exam_show.load_verdicts(self.log)[0]
        self.assertEqual(r["коммит"], "НЕ ЗАПИСАНО(коммит)")
        self.assertEqual(r["правила"], "НЕ ЗАПИСАНО(версия правил)")
        self.assertEqual(r["корпус"], exam_show.corpus_fingerprint(),
                         "целая опора не смеет пострадать")

    def test_a_wrong_typed_proof_is_not_swallowed_as_empty(self):
        """`0`/`[]`, приехавшие по ошибке вызывающего, не выдают себя за честно пустое значение."""
        for junk in (0, [], None, "   "):
            self.assertEqual(exam_show._evidence(junk, "коммит"), "НЕ ЗАПИСАНО(коммит)")
        self.assertEqual(exam_show._evidence("  abc1234 ", "коммит"), "abc1234")

    def test_candidate_never_becomes_active_on_any_path(self):
        """Ни одна ветка модуля не пишет состояния «актив» и не зовёт перевод.

        Меряем ИМЕНА И ЛИТЕРАЛЫ КОДА через `ast`, а не текст файла. Прежняя редакция читала весь
        исходник целиком и покраснела от собственного объяснения в докстроке (там названа зрячая
        форма `lesson_store.promote`) — то есть от ПРОЗЫ, а не от поведения. Проверка, которую
        нельзя объяснить, не объяснив её в запрещённых словах, сторожит не то."""
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(exam_show))
        names, literals = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
        self.assertNotIn("promote", names, "перевода кандидата модуль не зовёт ни одной веткой")
        self.assertNotIn("актив", literals, "состояния «актив» модуль не пишет ни одним литералом")
        self.assertEqual(exam_show.STATE_CANDIDATE, "кандидат")
        self.tap()
        self.assertEqual({r["состояние"] for r in exam_show.load_verdicts(self.log)}, {"кандидат"})

    def test_the_reason_is_not_invented_for_the_owner(self):
        """У кандидата причины нет и графы для неё нет: пусто значит пусто."""
        self.assertNotIn("причина", exam_show.HEADER)
        self.tap()
        self.assertNotIn("почему", self.raw())

    def test_numbers_grow_and_are_never_reused(self):
        """Откат освобождает КЕЙС, но не НОМЕР: пересуженный кейс получает новую строку.

        Кейсы разные (правка 12.09.2026) — три вердикта по одному кейсу сегодня запрещены самим
        замком «одна запись на кейс», и меряли бы его, а не рост номеров."""
        self.put_shot(_shot(case=2))
        self.tap("ok", case=1)
        self.tap("no", case=2)
        exam_show.rollback(2, who="@filipp", path=self.log, right_fn=lambda w: True)
        self.tap("ok", case=2)
        nums = [r["номер"] for r in exam_show.load_verdicts(self.log)]
        self.assertEqual(nums, [1, 2, 3], "откат не смеет освободить номер под чужой вердикт")

    def test_rollback_keeps_the_row_and_only_changes_the_state(self):
        self.tap()
        before = self.raw().splitlines()
        ok, msg = exam_show.rollback(1, who="@filipp", path=self.log, right_fn=lambda w: True)
        self.assertTrue(ok, msg)
        after = self.raw().splitlines()
        self.assertEqual(len(before), len(after), "строка обязана остаться на месте")
        rec = exam_show.load_verdicts(self.log)[0]
        self.assertTrue(rec["состояние"].startswith("откачен("))
        self.assertEqual(rec["вердикт"], "верно", "сам вердикт откат не переписывает")
        self.assertEqual(rec["автор"], "@filipp")

    def test_second_rollback_refuses_with_its_own_words(self):
        self.tap()
        exam_show.rollback(1, who="@f", path=self.log, right_fn=lambda w: True)
        ok, msg = exam_show.rollback(1, who="@f", path=self.log, right_fn=lambda w: True)
        self.assertFalse(ok)
        self.assertIn("уже не кандидат", msg)

    def test_rollback_of_a_missing_number_touches_nothing(self):
        self.tap()
        raw = self.raw()
        ok, msg = exam_show.rollback(99, who="@f", path=self.log, right_fn=lambda w: True)
        self.assertFalse(ok)
        self.assertIn("нет", msg)
        self.assertEqual(self.raw(), raw)

    def test_rollback_asks_the_same_right_as_the_record(self):
        """ЗАМОК ОТКАТА (17.09.2026): до правки чужое имя откатывало вердикт №1 живым вызовом.

        Предикат права — СПИСОК ИМЁН с нормализацией `@`/регистра, как у `suggest.is_approver`, а
        не «да/нет» литералом: иначе проверка зеленела бы и на двери, которая имени не читает."""
        def right(who):
            return (who or "").lstrip("@").lower() == "filipp"
        self.tap()
        raw = self.raw()
        for alien in ("chuzhoy_0917", "@chuzhoy_0917", "", None):
            ok, msg = exam_show.rollback(1, who=alien, path=self.log, right_fn=right)
            self.assertFalse(ok, msg)
            self.assertIn("не вправе откатывать", msg)
            self.assertIn("Вердикт №1 не тронут", msg)
            self.assertEqual(self.raw(), raw, "чужой откат не смеет тронуть ни байта журнала")
        ok, msg = exam_show.rollback(1, who="@Filipp", path=self.log, right_fn=right)
        self.assertTrue(ok, msg)
        self.assertEqual(exam_show.passed_count(exam_show.load_verdicts(self.log)), 0)

    def test_rollback_and_record_ask_one_and_the_same_default_right(self):
        """Без подставленного права ОБЕ двери зовут ОДИН предикат — `moderation_core.may_write_rule`.
        Меряем вызовами, а не текстом исходника: общий предикат, позванный одной дверью и не
        позванный другой, в тексте выглядел бы одинаково."""
        import moderation_core
        asked = []

        def spy(who):
            asked.append(who)
            return False
        was, moderation_core.may_write_rule = moderation_core.may_write_rule, spy
        try:
            ok_tap, _ = exam_show.tap(1, "ok", "@кто", path=self.log)
            ok_rb, msg = exam_show.rollback(1, who="@кто", path=self.log)
        finally:
            moderation_core.may_write_rule = was
        self.assertEqual((ok_tap, ok_rb), (False, False), msg)
        self.assertEqual(asked, ["@кто", "@кто"], "откат обязан спросить тот же предикат, что запись")

    def test_a_tab_in_the_author_name_cannot_break_the_table(self):
        """Экранирование ЧУЖОЕ (lesson_store.esc) — своя вторая копия разъехалась бы с первой."""
        exam_show.tap(1, "ok", "@зло\tсюда\nи сюда", right_fn=lambda w: True, path=self.log,
                      sender=self.sender(), agent_fn=self.fresh_agent())
        self.assertEqual(len(self.raw().splitlines()), 2)
        self.assertEqual(len(exam_show.load_verdicts(self.log)), 1)


# ─────────────────────────── ЛОВЕЦ ТАПА: РОУТЕР pc_agent ────────────────────────────────

class TestAgentRoute(unittest.TestCase):
    OWNER = pc_agent.ALLOWED_USER_ID

    def test_goldens_of_the_button_parser(self):
        self.assertEqual(pc_agent._exam_cb_parse("exam:ok:7"), ("ok", "7"))
        self.assertEqual(pc_agent._exam_cb_parse("exam:no:17"), ("no", "17"))
        for alien in ("exam:ok:", "exam:maybe:1", "exam:ok:1;rm -rf /", "exam:ok:абв",
                      "gate:no:1", "", None, "exam:ok:1234"):
            self.assertIsNone(pc_agent._exam_cb_parse(alien), alien)

    def test_owner_tap_is_routed_to_the_exam_kind(self):
        route = pc_agent._chain_cb_route("exam:ok:3", self.OWNER)
        self.assertTrue(route["ok"])
        self.assertEqual(route["kind"], "exam")
        self.assertEqual((route["action"], route["pid"]), ("ok", "3"))
        self.assertTrue(route["answer"], "тост не смеет быть пустым — иначе «часики» повиснут")

    def test_the_toast_promises_exactly_what_the_door_does(self):
        """Тост обещает РОВНО то, что произойдёт, и у пяти кнопок он разный.

        Правка 12.09.2026. Прежняя редакция требовала слова «кандидат» у обоих вердиктов, потому
        что счёта «пройдено K» не существовало вовсе и вердикт не значил ничего. Сегодня счёт есть
        (слово владельца 12.09), и обещать «кандидатом» там, где кейс засчитывается, значило бы
        соврать в другую сторону. Инвариант, который остаётся: тост не смеет обещать БОЛЬШЕ, чем
        делает дверь, и не смеет быть пустым — иначе «часики» на кнопке повиснут."""
        said = {d: pc_agent._chain_cb_route("exam:%s:3" % d, self.OWNER)["answer"]
                for d in ("ok", "no", "go", "own", "h1", "h4")}
        self.assertEqual(len(set(said.values())), len(said), "две кнопки с одним тостом")
        for key, text in said.items():
            self.assertTrue(text.strip(), key)
            self.assertNotIn("зачт", text, key)
            self.assertNotIn("экзамен пройден", text, key)
        self.assertIn("4", said["h4"], "тумблер обязан назвать номер, который отмечает")
        self.assertNotIn("урок", said["h1"], "тумблер уроков не пишет — обещать их нельзя")

    def test_a_stranger_is_refused_before_the_data_is_parsed(self):
        route = pc_agent._chain_cb_route("exam:ok:3", (self.OWNER or 0) + 99999)
        self.assertFalse(route["ok"])
        self.assertIsNone(route["kind"])

    def test_exam_does_not_shadow_the_other_four_buttons(self):
        """Новая ветка не смеет перехватить чужие кнопки — у каждой свой род."""
        self.assertEqual(pc_agent._chain_cb_route("gate:no:abc", self.OWNER)["kind"], "gate")
        self.assertEqual(pc_agent._chain_cb_route("box:free:aabbcc", self.OWNER)["kind"], "box")

    def test_only_a_number_can_travel_from_telegram_into_the_command_line(self):
        """Инвариант безопасности: в командную строку двери едут ТОЛЬКО цифры.

        Меряется теперь САМА СБОРКА аргументов (`exam_args`), а не текст регулярки: сверка с
        литералом шаблона зеленела бы ровно до дня, когда шаблон законно поменяли, — и в этот день
        сказала бы «сломалось» про правку, ничего не ослабившую. Здесь проверено поведение: что бы
        ни пришло из Telegram, в argv не появляется ни одного символа, кроме цифр и наших ключей."""
        import re
        allowed = {"--case", "--tap", "--apply", "--own", "--toggle", "ok", "no"}
        for data in ("exam:ok:3", "exam:no:17", "exam:go:1", "exam:own:1", "exam:h1:2",
                     "exam:h4:170"):
            action, case = pc_agent._exam_cb_parse(data)
            args = pc_agent.exam_args(action, case)
            self.assertIsNotNone(args, data)
            for piece in args:
                self.assertTrue(piece in allowed or piece.isdigit(),
                                "в командную строку уехало не число и не наш ключ: %r" % piece)
        for alien in ("exam:ok:1 --who @root", "exam:h5:1", "exam:ok:абв", "exam:go:1;rm -rf /"):
            self.assertIsNone(re.match(pc_agent.EXAM_CB_RE, alien), alien)


# ═══════════ РАБОЧЕЕ МЕСТО ВЛАДЕЛЬЦА: ПОДСКАЗКИ, ТУМБЛЕРЫ, УРОКИ (12.09.2026) ═══════════════

class DeskBase(Base):
    """Общая обстановка рабочего места: кейс 1 с подсказками, кейс 2 без них (ему приходить
    следующим), право выдано, отправщик и ловец подставные."""

    def setUp(self):
        super().setUp()
        self.put_shot(_shot(case=1, hints=HINTS))
        self.put_shot(_shot(case=2, hints=HINTS))
        self.send = self.sender()

    def yes(self, _who=None):
        return True

    def toggle(self, n, case=1, who="@filipp"):
        return exam_show.toggle(case, n, who, right_fn=self.yes, session_path=self.desk)

    def apply(self, case=1, who="@filipp"):
        return exam_show.apply_marked(case, who, right_fn=self.yes, path=self.log,
                                      session_path=self.desk, sender=self.send,
                                      agent_fn=self.fresh_agent())

    def tap(self, verdict="ok", case=1, who="@filipp"):
        return exam_show.tap(case, verdict, who, right_fn=self.yes, path=self.log,
                             sender=self.send, agent_fn=self.fresh_agent())


class TestHintsAreBornAtFreezeAndLiveInTheShot(Base):
    def test_the_shot_carries_the_hints_with_both_fields(self):
        """Подсказка — ДВА поля: наблюдение (что не так здесь) и правило (как надо всегда)."""
        ok, path, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ", "note": "записка"}, ph=PH,
            critic=self.critic())
        self.assertTrue(ok, path)
        self.assertEqual(len(shot["hints"]), 2)
        self.assertEqual(shot["hints"][0]["observation"], HINTS[0]["observation"])
        self.assertEqual(shot["hints"][0]["rule"], HINTS[0]["rule"])
        with open(path, encoding="utf-8") as f:                 # и они ЛЕЖАТ НА ДИСКЕ, а не в RAM
            self.assertEqual(json.load(f)["hints"], shot["hints"])

    def test_a_silent_critic_does_not_cancel_the_paid_draft(self):
        """Круг головы за черновик уже оплачен: отказ критика не смеет его выбросить."""
        def broken(q, a, tr=""):
            raise RuntimeError("критик упал")
        ok, path, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ", "note": ""}, ph=PH, critic=broken)
        self.assertTrue(ok, path)
        self.assertEqual(shot["hints"], [])
        self.assertTrue(os.path.exists(path))

    def test_a_hint_without_a_rule_is_dropped_on_read(self):
        """Правило — то, что ложится уроком. Подсказка без него до кнопки не доезжает."""
        got = exam_show.hints_of({"hints": [_hint("наблюдение", "  "), "строка", 7,
                                            _hint("", "правило живо")]})
        self.assertEqual(got, [{"observation": "", "rule": "правило живо"}])

    def test_more_than_four_hints_are_cut_to_four(self):
        """Кнопок-номеров ровно четыре: пятая подсказка не имеет чем быть нажатой."""
        many = [_hint("н%d" % i, "п%d" % i) for i in range(9)]
        self.assertEqual(len(exam_show.hints_of({"hints": many})), 4)

    def test_the_critic_is_the_very_one_behind_the_teach_button(self):
        """Второго критика нет: живой сборщик зовёт ровно три чужие функции тренажёра."""
        import inspect
        src = inspect.getsource(exam_show._default_critic)
        for name in ("trainer.hypotheses_prompt", "trainer.parse_hypotheses", "trainer.split_pair"):
            self.assertIn(name, src, name)
        self.assertIn("paired=True", src)


class TestTheCardIsAWorkplace(DeskBase):
    def test_the_header_carries_the_score(self):
        card = exam_show.card_text(exam_show.load_shot(1), passed=3)
        self.assertIn("кейс 1 из 17 · пройдено 3", card)

    def test_the_score_is_absent_when_it_was_not_counted(self):
        """«Ноль» и «не считали» — разные новости; подставить первое вместо второго нельзя."""
        self.assertNotIn("пройдено", exam_show.card_text(exam_show.load_shot(1)))

    def test_the_card_shows_observation_and_rule_of_every_hint(self):
        card = exam_show.card_text(exam_show.load_shot(1), passed=0)
        self.assertIn("1. " + HINTS[0]["observation"], card)
        self.assertIn(HINTS[0]["rule"], card)
        self.assertIn(HINTS[1]["rule"], card)
        self.assertIn("наблюдение критик не назвал", card, "пустое поле названо словами")

    def test_a_shot_without_hints_says_so_and_keeps_the_old_buttons(self):
        """Старые снимки не ломаем: слова «подсказок нет» и РОВНО прежние две кнопки."""
        old = _shot(case=5)
        card = exam_show.card_text(old)
        self.assertIn("подсказок критика в этом снимке нет", card)
        self.assertEqual(exam_show.markup(5, old), {"inline_keyboard": [[
            {"text": "✅ Верно", "callback_data": "exam:ok:5"},
            {"text": "❌ Неверно", "callback_data": "exam:no:5"}]]})

    def test_a_shot_with_hints_gets_the_four_kinds_of_buttons(self):
        kb = exam_show.markup(1, exam_show.load_shot(1))["inline_keyboard"]
        flat = [b["callback_data"] for row in kb for b in row]
        self.assertEqual(flat, ["exam:ok:1", "exam:h1:1", "exam:h2:1", "exam:own:1", "exam:go:1"])
        labels = [b["text"] for row in kb for b in row]
        self.assertIn("✅ Верно", labels)
        self.assertIn("✍️ своё", labels)
        self.assertIn("✔ Применить", labels)

    def test_every_button_of_the_card_is_understood_by_the_catcher(self):
        """ЗАМЫКАНИЕ: каждая кнопка карточки разбирается ловцом и превращается в аргументы двери.
        Кнопка, которую ловец не знает, — это молчащий тап, а он стоил владельцу дня 05.09."""
        kb = exam_show.markup(1, exam_show.load_shot(1))["inline_keyboard"]
        for row in kb:
            for b in row:
                parsed = pc_agent._exam_cb_parse(b["callback_data"])
                self.assertIsNotNone(parsed, b["callback_data"])
                self.assertIsNotNone(pc_agent.exam_args(*parsed), b["callback_data"])

    def test_the_card_says_in_words_what_each_button_does(self):
        card = exam_show.card_text(exam_show.load_shot(1), passed=0)
        for word in ("✅ Верно", "ТУМБЛЕРЫ", "✔ Применить", "✍️ своё", "10 минут"):
            self.assertIn(word, card, word)


class TestTogglesMarkAndNothingElse(DeskBase):
    def test_a_number_marks_and_a_second_tap_unmarks(self):
        ok, msg = self.toggle(1)
        self.assertTrue(ok, msg)
        self.assertEqual(exam_show.load_session(self.desk)["selected"], [1])
        ok, msg = self.toggle(1)
        self.assertTrue(ok, msg)
        self.assertEqual(exam_show.load_session(self.desk)["selected"], [])
        self.assertIn("снял отметку", msg)

    def test_marks_accumulate_and_are_named_back(self):
        self.toggle(2)
        ok, msg = self.toggle(1)
        self.assertTrue(ok, msg)
        self.assertEqual(exam_show.load_session(self.desk)["selected"], [1, 2])
        self.assertIn("1, 2", msg)

    def test_a_toggle_writes_neither_a_verdict_nor_a_lesson(self):
        """Промах пальцем не смеет стать действующим правилом."""
        self.toggle(1)
        self.toggle(2)
        self.assertFalse(os.path.exists(self.log), "журнала вердиктов не должно появиться")
        self.assertEqual(self.lesson_rows(), [], "в базу уроков тумблер не пишет ни строки")


class TestFiveNegativesOfTheWorkplace(DeskBase):
    def test_a_tap_on_a_stale_hint_number_refuses_and_writes_nothing(self):
        ok, msg = self.toggle(4)
        self.assertFalse(ok)
        self.assertIn("устарела", msg)
        self.assertEqual(exam_show.load_session(self.desk).get("selected", []), [])
        self.assertFalse(os.path.exists(self.log))

    def test_a_tap_on_a_hint_of_another_shot_refuses(self):
        """Стол собран на одном снимке, показан другой: номер 1 значит разные правила."""
        self.toggle(1)
        exam_show.save_session(dict(exam_show.load_session(self.desk),
                                    shot_key="другой@снимок"), self.desk)
        ok, msg = self.toggle(2)
        self.assertFalse(ok)
        self.assertIn("на другом снимке", msg)
        self.assertEqual(self.lesson_rows(), [])

    def test_a_verdict_on_a_shot_with_an_alien_corpus_is_refused(self):
        self.put_shot(_shot(case=7, corpus="0000000000000000", hints=HINTS))
        for door in (lambda: self.tap("ok", case=7), lambda: self.apply(case=7),
                     lambda: exam_show.toggle(7, 1, "@filipp", right_fn=self.yes,
                                              session_path=self.desk)):
            ok, msg = door()
            self.assertFalse(ok, msg)
            self.assertIn("0000000000000000", msg)
        self.assertFalse(os.path.exists(self.log))

    def test_a_second_yes_on_the_same_case_makes_one_record_not_two(self):
        ok, first = self.tap("ok")
        self.assertTrue(ok, first)
        ok, second = self.tap("ok")
        self.assertFalse(ok)
        self.assertIn("уже судим", second)
        self.assertEqual(len(exam_show.load_verdicts(self.log)), 1)
        # откат освобождает кейс — иначе пересудить его было бы нечем
        exam_show.rollback(1, who="@filipp", path=self.log, right_fn=self.yes)
        ok, third = self.tap("no")
        self.assertTrue(ok, third)
        self.assertEqual(len(exam_show.load_verdicts(self.log)), 2)

    def test_apply_without_marks_refuses_in_words(self):
        ok, msg = self.apply()
        self.assertFalse(ok)
        self.assertIn("Ничего не отмечено", msg)
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.lesson_rows(), [])

    def test_after_the_last_case_comes_the_result_and_not_an_eighteenth(self):
        last = str(exam_show.load_cases()[-1]["id"])
        self.assertIsNone(exam_show.next_case_id(last))
        said = exam_show.advance(last, path=self.log, sender=self.send,
                                 agent_fn=self.fresh_agent())
        self.assertIn("ЭКЗАМЕН ПРОЙДЕН", said)
        self.assertIn("из 17", said)
        self.assertEqual(self.send.calls, [], "восемнадцатой карточки не существует")

    def test_the_right_is_fail_closed_on_every_new_door(self):
        """Право одно на все двери, и на пустом списке — НИКОМУ."""
        for door in (lambda: exam_show.toggle(1, 1, "@чужой", right_fn=lambda w: False,
                                              session_path=self.desk),
                     lambda: exam_show.apply_marked(1, "@чужой", right_fn=lambda w: False,
                                                    path=self.log, session_path=self.desk),
                     lambda: exam_show.own_start(1, "@чужой", right_fn=lambda w: False,
                                                 session_path=self.desk)):
            ok, msg = door()
            self.assertFalse(ok)
            self.assertIn("не вправе", msg)
        self.assertFalse(os.path.exists(self.log))
        self.assertEqual(self.lesson_rows(), [])


class TestApplyWritesLessonsAndAdvances(DeskBase):
    def test_marked_hints_become_lessons_with_author_source_and_reason(self):
        import lesson_store
        self.toggle(1)
        self.toggle(2)
        ok, msg = self.apply()
        self.assertTrue(ok, msg)
        store = lesson_store.load(self.lessons)
        self.assertEqual(len(store.lessons), 2, "каждая отмеченная подсказка — ОТДЕЛЬНЫЙ урок")
        first = store.lessons[0]
        self.assertEqual(first.correct, HINTS[0]["rule"], "уроком ложится ПРАВИЛО подсказки")
        self.assertEqual(first.who, "@filipp")
        self.assertEqual(first.source, lesson_store.SOURCE_EXAM)
        self.assertIn("критик, подтверждено тапом", first.why)
        self.assertIn(HINTS[0]["observation"], first.why, "причина — наблюдение критика")
        self.assertEqual(first.question, exam_show.load_shot(1)["question"])
        self.assertEqual(first.bot_answer, exam_show.load_shot(1)["draft"])

    def test_the_lesson_acts_at_once_in_training_mode(self):
        import lesson_store
        self.toggle(1)
        self.apply()
        self.assertEqual(len(lesson_store.active(lesson_store.load(self.lessons).lessons)), 1)

    def test_one_line_turns_the_rule_off_and_the_tap_lays_a_candidate(self):
        """Слово владельца отменяет «действует сразу» — переключение стоит одной строки."""
        import lesson_store
        was, exam_show.EXAM_LESSON_MODE = exam_show.EXAM_LESSON_MODE, "candidate"
        try:
            self.toggle(1)
            ok, msg = self.apply()
            self.assertTrue(ok, msg)
            lessons = lesson_store.load(self.lessons).lessons
            self.assertEqual(len(lesson_store.active(lessons)), 0)
            self.assertEqual(len(lesson_store.candidates(lessons)), 1)
            self.assertIn("КАНДИДАТАМИ", msg)
        finally:
            exam_show.EXAM_LESSON_MODE = was

    def test_the_verdict_is_wrong_and_carries_the_lesson_numbers(self):
        self.toggle(1)
        self.toggle(2)
        ok, msg = self.apply()
        self.assertTrue(ok, msg)
        rows = exam_show.load_verdicts(self.log)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["вердикт"], "неверно")
        self.assertEqual(rows[0]["уроки"], "1, 2")
        self.assertEqual(rows[0]["состояние"], "кандидат",
                         "вердикт остаётся кандидатом, даже когда урок уже действует")

    def test_the_marks_are_cleared_so_a_second_apply_cannot_repeat_them(self):
        self.toggle(1)
        self.apply()
        self.assertEqual(exam_show.load_session(self.desk)["selected"], [])

    def test_the_next_case_comes_by_itself_after_every_verdict(self):
        ok, msg = self.tap("ok")
        self.assertTrue(ok, msg)
        self.assertEqual(len(self.send.calls), 1, "следующая карточка обязана уйти сама")
        self.assertIn("кейс 2 из 17", self.send.calls[0]["text"])
        self.assertIn("пройдено 1", self.send.calls[0]["text"], "счёт двинулся на зачтённый кейс")

    def test_a_silent_telegram_does_not_undo_a_written_verdict(self):
        """Вердикт уже на диске: сорвавшийся показ следующего кейса не смеет его отменить."""
        def dead(text, chat, markup=None):
            raise RuntimeError("Telegram молчит")
        ok, msg = exam_show.tap(1, "ok", "@filipp", right_fn=self.yes, path=self.log,
                                sender=dead, agent_fn=self.fresh_agent())
        self.assertTrue(ok, msg)
        self.assertEqual(len(exam_show.load_verdicts(self.log)), 1)
        self.assertIn("вердикт при этом ЗАПИСАН", msg,
                      "сорвавшийся показ обязан сказать словами, что вердикт всё-таки лёг")


class TestOwnWordsLesson(DeskBase):
    def start(self, who="@filipp", now=None):
        return exam_show.own_start(1, who, right_fn=self.yes, session_path=self.desk, now=now)

    def take(self, text, who="@filipp", now=None):
        return exam_show.own_take(text, who, right_fn=self.yes, path=self.log,
                                  session_path=self.desk, now=now, sender=self.send,
                                  agent_fn=self.fresh_agent())

    def test_the_next_message_becomes_a_candidate_lesson(self):
        import lesson_store
        ok, msg = self.start()
        self.assertTrue(ok, msg)
        self.assertIn("10 минут", msg)
        ok, msg = self.take("не называй сроков выдачи вообще")
        self.assertTrue(ok, msg)
        lessons = lesson_store.load(self.lessons).lessons
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0].correct, "не называй сроков выдачи вообще")
        self.assertEqual(lessons[0].source, lesson_store.SOURCE_EXAM)
        self.assertEqual(lessons[0].why, "", "причину за владельца не выдумывает ни одна ветка")
        self.assertEqual(len(lesson_store.candidates(lessons)), 1)

    def test_the_card_says_in_one_line_how_to_switch_it_on(self):
        self.start()
        _ok, msg = self.take("правило своими словами")
        self.assertIn("lesson_promote.py", msg)
        self.assertIn("--why", msg)

    def test_the_verdict_and_the_next_case_follow_the_own_lesson(self):
        self.start()
        ok, msg = self.take("правило своими словами")
        self.assertTrue(ok, msg)
        rows = exam_show.load_verdicts(self.log)
        self.assertEqual([rows[0]["вердикт"], rows[0]["уроки"]], ["неверно", "1"])
        self.assertEqual(len(self.send.calls), 1)
        self.assertIn("кейс 2 из 17", self.send.calls[0]["text"])

    def test_after_ten_minutes_the_waiting_is_over_and_nothing_is_written(self):
        self.start(now=1000)
        ok, msg = self.take("поздний текст", now=1000 + exam_show.PENDING_TTL_SEC + 1)
        self.assertFalse(ok)
        self.assertIn("10 минут", msg)
        self.assertEqual(self.lesson_rows(), [])
        self.assertFalse(os.path.exists(self.log))

    def test_a_text_without_any_waiting_is_never_a_lesson(self):
        ok, msg = self.take("просто реплика в группе")
        self.assertFalse(ok)
        self.assertIn("ожидания", msg)
        self.assertEqual(self.lesson_rows(), [])

    def test_the_waiting_belongs_to_the_one_who_asked_for_it(self):
        self.start(who="@filipp")
        ok, msg = self.take("чужой текст", who="@посторонний")
        self.assertFalse(ok)
        self.assertIn("принадлежит", msg)
        self.assertEqual(self.lesson_rows(), [])

    def test_an_empty_text_is_not_a_lesson(self):
        self.start()
        ok, msg = self.take("   \n  ")
        self.assertFalse(ok)
        self.assertIn("Пустой урок", msg)
        self.assertEqual(self.lesson_rows(), [])


class TestAJudgedCaseGetsNoLessons(DeskBase):
    """ЗАМОК «УЖЕ СУДИМ» СТОИТ ДО УРОКОВ (17.09.2026). До правки обе двери сперва писали урок, а
    потом слышали «второй записи не делаем»: живой прогон 17.09 оставил урок #18 действующим без
    вердикта. Стол здесь остаётся на судимом кейсе — так живьём бывает на последнем кейсе и на
    кейсе, за которым нет снимка: показ следующего отказывает ДО перевода стола."""

    def judge_and_stay(self):
        ok, msg = exam_show.tap(1, "ok", "@filipp", right_fn=self.yes, path=self.log,
                                sender=self.send, agent_fn=lambda **_: (None, "следующего нет"))
        self.assertTrue(ok, msg)
        self.assertEqual(len(exam_show.load_verdicts(self.log)), 1)

    def test_apply_on_a_judged_case_writes_zero_lessons(self):
        self.toggle(1)
        self.judge_and_stay()
        self.assertEqual(exam_show.load_session(self.desk)["selected"], [1],
                         "стол обязан остаться на судимом кейсе — иначе меряем не тот замок")
        ok, msg = self.apply()
        self.assertFalse(ok, msg)
        self.assertIn("уже судим", msg)
        self.assertIn(exam_show.JUDGED_NO_LESSONS, msg)
        self.assertEqual(self.lesson_rows(), [], "по судимому кейсу уроков — ноль")
        self.assertEqual(len(exam_show.load_verdicts(self.log)), 1)

    def test_own_words_on_a_judged_case_write_zero_lessons_and_end_the_waiting(self):
        self.judge_and_stay()
        ok, msg = exam_show.own_start(1, "@filipp", right_fn=self.yes, session_path=self.desk)
        self.assertTrue(ok, msg)
        ok, msg = exam_show.own_take("правило своими словами", "@filipp", right_fn=self.yes,
                                     path=self.log, session_path=self.desk, sender=self.send,
                                     agent_fn=self.fresh_agent())
        self.assertFalse(ok, msg)
        self.assertIn("уже судим", msg)
        self.assertIn(exam_show.JUDGED_NO_LESSONS, msg)
        self.assertEqual(self.lesson_rows(), [], "по судимому кейсу уроков — ноль")
        self.assertEqual(len(exam_show.load_verdicts(self.log)), 1)
        self.assertEqual(exam_show.pending_own(self.desk), (None, None),
                         "ожидание снято: следующий текст не должен слышать тот же отказ")

    def test_a_free_case_still_gets_its_lessons(self):
        """Обратная сторона замка: несудимый кейс пишет уроки как прежде."""
        self.toggle(1)
        ok, msg = self.apply()
        self.assertTrue(ok, msg)
        self.assertEqual(len(self.lesson_rows()), 1)


class TestRulesVersionIsAStringFromTheLiveType(Base):
    """ВЕРСИЯ ПРАВИЛ — СТРОКОЙ (17.09.2026). Значение снимается с ЖИВОГО `lesson_store.Version`
    настоящей таблицы, а не подставляется строкой: прежний набор собирал снимок с `rules="v1"` и
    потому не видел, что живой тип — кортеж, который `json.dump` кладёт списком."""

    def live_version(self):
        import lesson_store
        lesson_store.add(question="вопрос", bot_answer="ответ", correct="правило",
                         why="потому что так", who="@filipp", source=lesson_store.SOURCE_EXAM,
                         path=self.lessons)
        v = lesson_store.version(self.lessons)
        self.assertIs(type(v), lesson_store.Version)
        self.assertTrue(v.ok, v.say)
        return v

    def test_freeze_writes_the_version_as_a_string(self):
        v = self.live_version()
        ok, path, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ", "note": ""}, ph=PH, critic=self.critic())
        self.assertTrue(ok, path)
        self.assertEqual(shot["rules"], v.said)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["rules"], v.said, "на диске — строка, а не список")

    def test_an_old_shot_with_the_list_form_reads_as_the_version(self):
        """Старый снимок НЕ переписывается: его список читается версией при записи вердикта."""
        v = self.live_version()
        old = json.loads(json.dumps(v))
        self.assertIsInstance(old, list, "так живой тип ложился в снимки 11–17.09")
        self.assertEqual(exam_show.rules_text(old), v.said)
        self.put_shot(_shot(rules=old))
        ok, msg = exam_show.tap(1, "ok", "@filipp", right_fn=lambda w: True, path=self.log,
                                sender=self.sender(), agent_fn=self.fresh_agent())
        self.assertTrue(ok, msg)
        self.assertEqual(exam_show.load_verdicts(self.log)[0]["правила"], v.said)
        self.assertIn("правила %s." % v.said, msg, "квитанция владельцу — версия, а не печать типа")
        self.assertNotIn("[", msg.split("правила", 1)[1].split("\n", 1)[0])

    def test_an_unverified_version_is_named_not_invented(self):
        """Поле разошлось с таблицей → версия НЕ подставляется ни записанной, ни пересчитанной."""
        import lesson_store
        self.live_version()
        with open(lesson_store.version_path(self.lessons), "w", encoding="utf-8") as f:
            f.write("0000000000000000")
        v = lesson_store.version(self.lessons)
        self.assertFalse(v.ok)
        self.assertEqual(exam_show.rules_text(v), "")
        self.assertEqual(exam_show.rules_text(json.loads(json.dumps(v))), "")
        self.put_shot(_shot(rules=json.loads(json.dumps(v))))
        exam_show.tap(1, "ok", "@filipp", right_fn=lambda w: True, path=self.log,
                      sender=self.sender(), agent_fn=self.fresh_agent())
        self.assertEqual(exam_show.load_verdicts(self.log)[0]["правила"],
                         "НЕ ЗАПИСАНО(версия правил)")

    def test_a_string_version_stays_as_it_was(self):
        self.assertEqual(exam_show.rules_text("v7"), "v7")
        self.assertEqual(exam_show.rules_text(None), "")
        self.assertEqual(exam_show.rules_text(0), "")


class TestTheTwoReadersOfOneDesk(DeskBase):
    """КОНТРАКТ: стол пишет `exam_show`, а ждущий кейс читает `pc_agent` — СВОИМ разбором.

    Второй читатель заведён не от хорошей жизни: `import exam_show` в агенте затаскивает прогонщик
    корпуса в замыкание живых ворот клиентского контура (замер — `test_trainer_run`). Цена второго
    разбора — риск разъезда ключей, и сторожит его этот класс, а не аккуратность."""

    def test_what_one_writes_the_other_reads(self):
        self.start_ok = self.assertTrue(exam_show.own_start(
            1, "@filipp", right_fn=self.yes, session_path=self.desk, now=1000)[0])
        self.assertEqual(str(pc_agent._exam_pending_case(path=self.desk, now=1001)), "1")

    def test_the_catcher_agrees_on_the_ten_minute_ttl(self):
        exam_show.own_start(1, "@filipp", right_fn=self.yes, session_path=self.desk, now=1000)
        late = 1000 + exam_show.PENDING_TTL_SEC + 1
        self.assertIsNone(pc_agent._exam_pending_case(path=self.desk, now=late))
        self.assertEqual(exam_show.pending_own(self.desk, now=late), (None, None))

    def test_a_desk_without_waiting_is_silence_for_the_catcher(self):
        """Показ кейса ставит стол БЕЗ ожидания — и ловец обязан молчать на каждом сообщении."""
        exam_show.save_session({"case": "1", "shot_key": "x", "selected": [1]}, self.desk)
        self.assertIsNone(pc_agent._exam_pending_case(path=self.desk))

    def test_an_unreadable_desk_is_fail_closed(self):
        """Ложное «жду» превратило бы первое сообщение владельца в урок, которого он не писал."""
        with open(self.desk, "w", encoding="utf-8") as f:
            f.write("{это не json")
        self.assertIsNone(pc_agent._exam_pending_case(path=self.desk))
        self.assertIsNone(pc_agent._exam_pending_case(path=os.path.join(self.tmp, "нет.json")))

    def test_the_catcher_watches_the_very_group_where_the_card_hangs(self):
        self.assertEqual(pc_agent.EXAM_CHAT_ID, exam_show.TRAINER_CHAT)


class TestSenderDoor(unittest.TestCase):
    def test_the_chat_must_be_named_and_has_no_default(self):
        import inspect

        import dispatch_notify
        sig = inspect.signature(dispatch_notify.send_chat_strict)
        self.assertIs(sig.parameters["chat_id"].default, inspect.Parameter.empty)
        for empty in ("", None, 0, "0"):
            channel, ok, why = dispatch_notify.send_chat_strict("текст", empty)
            self.assertFalse(ok)
            self.assertEqual(channel, "none")
            self.assertIn("не назван", why)

    def test_the_door_has_no_fallback_cascade(self):
        """Сообщение, адресованное группе, в инбоксе владельца хуже молчания.

        Меряем КОД, а не докстроку: соседние двери названы в ней по имени, и проверка по всему
        исходнику краснела бы от объяснения ровно того, что проверяет."""
        import inspect
        fn = dispatch_notify_module().send_chat_strict
        body = inspect.getsource(fn).replace(fn.__doc__ or "", "")
        for cascade in ("send_critical", "send_topic", "DM_CHAT_ID", "HQ_CHAT_ID"):
            self.assertNotIn(cascade, body, cascade)


class TestReachProbes(unittest.TestCase):
    def test_reach_probe_names_the_chat_or_refuses(self):
        ok, why = dispatch_notify_module().chat_reachable("")
        self.assertFalse(ok)
        self.assertIn("не назван", why)

    def test_both_probes_only_read_and_never_send(self):
        """Проба доступности НЕ смеет оставить следа в группе: только `getChat`.

        Проверяем ОБЕ (наш бот и модербот): вторая ходит чужим токеном в чат, где живут люди, и
        случайный `sendMessage` здесь был бы сообщением, которого никто не просил."""
        import inspect
        for fn in (dispatch_notify_module().chat_reachable, exam_show.reach_as_moderbot):
            body = inspect.getsource(fn).replace(fn.__doc__ or "", "")
            self.assertIn("getChat", body)
            self.assertNotIn("sendMessage", body)
            self.assertNotIn("getUpdates", body)

    def test_the_moderbot_probe_never_prints_the_token(self):
        import inspect
        body = inspect.getsource(exam_show.reach_as_moderbot)
        self.assertNotIn("print(token", body)
        self.assertNotIn("%s\" % token", body)
        self.assertIn("не логируем", body)


# ═══════════ ТРИ ИСХОДА ПОДСКАЗОК ВМЕСТО ОДНОГО (12.09.2026, задание 52-b) ═══════════════════

class TestThreeOutcomesOfTheHints(Base):
    """Снимок без подсказок обязан говорить, ПОЧЕМУ их нет, и у двух нулевых исходов слова РАЗНЫЕ.

    ГОЛОВА ЗДЕСЬ НЕ ЗОВЁТСЯ НИ РАЗУ: подменена ровно она (`suggest.default_llm_caller`), а запрос,
    разбор, сохранение сырого ответа и сборка отчёта — настоящие. Подменить вместо головы весь
    критик значило бы мерить собственную фикстуру."""

    # Ответ в ПРАВИЛЬНОЙ форме — той самой, что живьём отдали 5 ответов из 5 (замер 12.09.2026):
    # маркер секции, строки двумя частями через «||», пустая секция отсева.
    FORM = ("ГИПОТЕЗЫ:\n"
            "назвал 307 ฿/день, хотя в прайсе 449 || называй цены дословно из прайса\n"
            "написал «свободен», не проверив календарь || не подтверждай наличие без проверки\n"
            "ОТСЕЯНО:\n(нет)")
    # Ответ ЕСТЬ, а гипотез в нём разбор не находит: модель забраковала всё сама.
    ALL_DROPPED = ("ГИПОТЕЗЫ:\n"
                   "ОТСЕЯНО:\nвсё, что нашлось, уже написано в книге правил — добавить нечего")

    def setUp(self):
        super().setUp()
        import trainer
        self.trainer = trainer
        self.raws = os.path.join(self.tmp, "critic_raw")
        self._raw_was, exam_show.CRITIC_RAW_DIR = exam_show.CRITIC_RAW_DIR, self.raws
        # ЖИВЫЕ ИСТОЧНИКИ ЗАПРОСА — ПУСТЫМИ: канон и книга правил ходят в сеть, а набор не ходит
        # туда ни разу (шапка модуля). Разбор при пустой книге сверяет повторы ни с чем — ровно
        # то, что нужно: предмет здесь исход, а не содержание книги.
        self._canon_was, trainer.business_canon = trainer.business_canon, lambda mod=None: ""
        self._book_was, trainer.rules_book = trainer.rules_book, lambda mod=None: ""
        self.addCleanup(self._restore_critic)

    def _restore_critic(self):
        exam_show.CRITIC_RAW_DIR = self._raw_was
        self.trainer.business_canon = self._canon_was
        self.trainer.rules_book = self._book_was

    def ask(self, answer):
        """Живой критик с подменённой головой. → (подсказки, отчёт)."""
        import suggest
        was = suggest.default_llm_caller
        suggest.default_llm_caller = lambda: (lambda s, u: answer)
        try:
            return exam_show._default_critic("вопрос", "ответ бота", "транскрипт", case_id=1)
        finally:
            suggest.default_llm_caller = was

    def test_an_answer_in_the_right_form_yields_the_hints(self):
        """(а) Форма соблюдена — подсказки вынимаются, и исход назван числом."""
        hints, rep = self.ask(self.FORM)
        self.assertEqual(len(hints), 2)
        self.assertEqual(hints[0]["observation"], "назвал 307 ฿/день, хотя в прайсе 449")
        self.assertEqual(hints[0]["rule"], "называй цены дословно из прайса")
        self.assertEqual(rep["outcome"], exam_show.OUT_HINTS)
        self.assertEqual(rep["parsed"], 2)

    def test_an_empty_answer_is_the_silent_critic(self):
        """(б) Ответа нет вовсе — исход «критик не ответил», с пределом и сроком ожидания."""
        hints, rep = self.ask("")
        self.assertEqual(hints, [])
        self.assertEqual(rep["outcome"], exam_show.OUT_SILENT)
        self.assertEqual(rep["raw_len"], 0)
        self.assertEqual(rep["limit"], exam_show.CRITIC_TIMEOUT)
        self.assertIsNotNone(rep["waited"], "сколько ждали — измеренное число, а не пропуск")

    def test_an_answer_that_yields_nothing_is_the_third_outcome(self):
        """(в) Ответ ЕСТЬ, а подсказок ноль — это НЕ (а) и НЕ (б), и длина ответа названа."""
        hints, rep = self.ask(self.ALL_DROPPED)
        self.assertEqual(hints, [])
        self.assertEqual(rep["outcome"], exam_show.OUT_UNPARSED)
        self.assertNotEqual(rep["outcome"], exam_show.OUT_SILENT)
        self.assertEqual(rep["raw_len"], len(self.ALL_DROPPED))
        self.assertGreater(rep["raw_len"], 0)

    def test_the_words_of_the_two_empty_outcomes_differ_and_both_say_unknown(self):
        """Разные слова — весь смысл правки: одинаковые прятали, ЧТО именно чинить."""
        silent = exam_show.no_hints_line({"critic": self.ask("")[1]})
        unparsed = exam_show.no_hints_line({"critic": self.ask(self.ALL_DROPPED)[1]})
        self.assertNotEqual(silent, unparsed)
        self.assertIn("НЕ ОТВЕТИЛ", silent)
        self.assertIn("ОТВЕТИЛ —", unparsed)
        for line in (silent, unparsed):
            self.assertIn("НЕИЗВЕСТНО", line, "нулевой исход не смеет выдавать незнание за ответ")

    def test_an_old_shot_without_the_critic_key_is_shown_as_before(self):
        """Снимок старого образца: прежние слова и РОВНО прежние две кнопки."""
        old = _shot(case=5)
        self.assertNotIn("critic", old)
        self.assertIn("подсказок критика в этом снимке нет", exam_show.card_text(old))
        self.assertEqual(exam_show.markup(5, old), {"inline_keyboard": [[
            {"text": "✅ Верно", "callback_data": "exam:ok:5"},
            {"text": "❌ Неверно", "callback_data": "exam:no:5"}]]})

    def test_the_raw_answer_lives_in_the_temp_place_and_never_in_the_shot(self):
        """Сырой ответ сохранён ФАЙЛОМ, а в снимок едут только длина и отпечаток."""
        _hints, rep = self.ask(self.FORM)
        # Путь относителен репозиторию, а на чужом диске — абсолютен (см. `_save_critic_raw`):
        # набор гоняется из временного каталога C:, а репозиторий лежит на D:.
        path = os.path.join(exam_show.REPO, rep["raw_path"])
        self.assertTrue(os.path.exists(path), rep["raw_path"])
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), self.FORM)
        self.assertTrue(os.path.normcase(os.path.normpath(path)).startswith(
            os.path.normcase(os.path.normpath(self.raws))), "временное место набора, а не боевое")
        self.assertEqual(rep["raw_len"], len(self.FORM))
        self.assertEqual(len(rep["raw_sha256"]), 16)
        self.assertNotIn(self.FORM, json.dumps(rep, ensure_ascii=False))

    def test_the_critic_ceiling_is_its_own_and_is_given_back_after_the_call(self):
        """Потолок критика СВОЙ, и потолок черновика после круга остаётся прежним."""
        import suggest
        was = suggest.CLI_TIMEOUT
        self.assertNotEqual(exam_show.CRITIC_TIMEOUT, was, "общий потолок — это и был дефект")
        seen = []
        caller_was = suggest.default_llm_caller
        suggest.default_llm_caller = lambda: (lambda s, u: seen.append(suggest.CLI_TIMEOUT) or "")
        try:
            exam_show._default_critic("в", "о", "т", case_id=1)
        finally:
            suggest.default_llm_caller = caller_was
        self.assertEqual(seen, [exam_show.CRITIC_TIMEOUT], "круг критика идёт под СВОИМ потолком")
        self.assertEqual(suggest.CLI_TIMEOUT, was, "потолок черновика не угнан")

    def test_a_line_without_the_separator_still_becomes_a_hint(self):
        """ЗАМОК на fail-safe `split_pair`: строка без «||» — это ПРАВИЛО, а не потерянная подсказка.

        Правило — то, что ложится уроком; выбросить его из-за несоблюдённого формата значило бы
        терять работу оплаченного круга. Поэтому такой ответ даёт исход (а), а не (в), и тест
        стои́т здесь, чтобы «развести исходы» однажды не сломало этот fail-safe заодно."""
        hints, rep = self.ask("ГИПОТЕЗЫ:\nне называй сроков выдачи — их согласует менеджер")
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]["observation"], "")
        self.assertEqual(rep["outcome"], exam_show.OUT_HINTS)

    def test_the_shot_carries_the_outcome_of_the_critic_round(self):
        """Исход едет В СНИМОК: вопрос «почему подсказок нет» задаётся СУТКИ спустя, глядя в файл."""
        ok, path, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ", "note": ""}, ph=PH, critic=self.critic())
        self.assertTrue(ok, path)
        self.assertEqual(shot["critic"]["outcome"], exam_show.OUT_HINTS)
        self.assertEqual(shot["critic"]["parsed"], len(shot["hints"]))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["critic"], shot["critic"])

    def test_the_collector_is_told_the_outcome_right_away(self):
        """Собирающий узнаёт исход в ту минуту, когда ещё может позвать круг заново."""
        _h, ok_rep = self.ask(self.FORM)
        line = exam_show.critic_line({"critic": ok_rep})
        self.assertIn("подсказок 2", line)
        self.assertIn(str(exam_show.CRITIC_TIMEOUT), line)
        for answer in ("", self.ALL_DROPPED):
            self.assertIn("НЕИЗВЕСТНО", exam_show.critic_line({"critic": self.ask(answer)[1]}))
        self.assertIn("старого образца", exam_show.critic_line(_shot(case=5)))

    def test_a_fallen_critic_is_named_silent_and_does_not_cancel_the_shot(self):
        """Круг оборвался исключением — снимок жив, а исход назван словом, а не тишиной."""
        def broken(q, a, tr=""):
            raise RuntimeError("критик упал")
        ok, path, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ", "note": ""}, ph=PH, critic=broken)
        self.assertTrue(ok, path)
        self.assertEqual(shot["hints"], [])
        self.assertEqual(shot["critic"]["outcome"], exam_show.OUT_SILENT)
        self.assertEqual(shot["critic"]["error"], "RuntimeError")
        self.assertIn("НЕИЗВЕСТНО", exam_show.no_hints_line(shot))


# ═══════════ ЛОВУШКА КАРТОЧЕК ВЛАДЕЛЬЦУ НА КРУГ СБОРКИ (17.09.2026, задание 63-h) ═════════════

# Периоды — ФИКСТУРА ДОВОДОМ (`doc=`), а не боевой `price_source.json`: набор ценового файла не
# касается ни чтением. Границы те же, что у боевого файла 07.09 (P1 06-01…09-30, P2 10-01…10-31).
SEASON_DOC = {"season": {"periods": [
    {"key": "P1", "name": "ИЮНЬ-СЕНТЯБРЬ", "from": "06-01", "to": "09-30"},
    {"key": "P2", "name": "ОКТЯБРЬ", "from": "10-01", "to": "10-31"}]}}
HINTS_CROSS = {"models": ["XMAX 300"], "iso_start": "2026-09-26", "iso_end": "2026-10-05",
               "hint_days": 9}
HINTS_NOPRICE = {"models": ["REBEL 300"], "iso_start": "2026-10-06", "iso_end": "2026-10-11",
                 "hint_days": 5}


class _NoTrap(object):
    """КОНТРФАКТ: ловушки нет. Та же сборка обязана дать попытку, иначе ноль ниже доказывал бы
    лишь то, что отправки на этом пути не бывает вообще."""

    def __init__(self):
        self.cards = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Boom(Exception):
    """Голова упала посреди круга — ловушка обязана сняться и тогда."""


class TestOwnerCardTrap(Base):
    """Сборка черновика не шлёт владельцу НИЧЕГО, а текст карточки ложится в снимок.

    «Боевой отправитель» здесь — счётчик на месте `_default_sender` обоих сторожей: ровно та
    ручка, которую боевой геттер отдаёт (`dispatch_notify.deliver`). Настоящий `dispatch_notify`
    набор не зовёт ни одной веткой. Раннер зовёт сторожей В ФОРМЕ ПУТИ ОТВЕТА — без `sender`,
    как `suggest.py` (эта форма сама заперта `test_live_call_sites_pass_no_sender`)."""

    def setUp(self):
        super(TestOwnerCardTrap, self).setUp()
        import noprice_gate
        import season_gate
        self.gates = {"season_gate": season_gate, "noprice_gate": noprice_gate}
        self.live = {}
        for name, mod in self.gates.items():
            self.addCleanup(setattr, mod, "_default_sender", mod._default_sender)
            self.addCleanup(mod.reset)
            mod.reset()
            calls = []
            self.live[name] = calls
            mod._default_sender = self._live_getter(calls)
        for key in (season_gate.OFF_ENV, noprice_gate.OFF_ENV):
            if key in os.environ:
                self.addCleanup(os.environ.__setitem__, key, os.environ[key])
                del os.environ[key]
        self.addCleanup(setattr, exam_show, "owner_card_trap", exam_show.owner_card_trap)

    @staticmethod
    def _live_getter(calls):
        def getter():
            def deliver(text, reply_markup=None, declared=None):
                calls.append(text)
                return ("боевой (счётчик набора)", True)
            return deliver
        return getter

    def attempts(self):
        return {name: len(calls) for name, calls in self.live.items()}

    def corpus(self):
        p = os.path.join(self.tmp, "cases.json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"cases": [
                {"id": 1, "name": "через границу сезонов", "lang": "ru",
                 "lines": ["Нужен XMAX 300 с 26 сентября по 5 октября"]},
                {"id": 2, "name": "модель без цены", "lang": "ru",
                 "lines": ["Сколько Rebel 300 с 6 по 11 октября?"]}]}, f, ensure_ascii=False)
        return p

    @staticmethod
    def season_runner(c):
        import season_gate
        note = season_gate.note_for(HINTS_CROSS, lang="ru", doc=SEASON_DOC)
        return {"draft": "Даты на стыке сезонов, цену посчитает коллега.", "note": note or ""}

    @staticmethod
    def noprice_runner(c):
        import noprice_gate
        note = noprice_gate.note_for(noprice_gate.KIND_NO_ROW, HINTS_NOPRICE, model="REBEL 300",
                                     lang="ru")
        return {"draft": "Цену на эту модель назовёт коллега.", "note": note or ""}

    def build(self, case_id, runner):
        return exam_show.freeze(case_id, cases_path=self.corpus(), runner=runner, ph=PH,
                                critic=self.critic([]))

    # ── пункт 3: двусторонний отрицательный ──────────────────────────────────────────────
    def test_season_case_under_trap_sends_nothing_and_keeps_the_card(self):
        import season_gate
        ok, path, shot = self.build(1, self.season_runner)
        self.assertTrue(ok, path)
        self.assertEqual(self.attempts(), {"season_gate": 0, "noprice_gate": 0})
        cards = shot[exam_show.OWNER_CARDS_KEY]
        self.assertEqual([c["gate"] for c in cards], ["season_gate"])
        self.assertTrue(cards[0]["text"].startswith(season_gate.CARD_HEAD), cards[0]["text"])
        for part in ("XMAX 300", "2026-09-26", "2026-10-05", "P1 ИЮНЬ-СЕНТЯБРЬ → P2 ОКТЯБРЬ"):
            self.assertIn(part, cards[0]["text"])
        with open(path, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk[exam_show.OWNER_CARDS_KEY], cards, "карточка не легла в файл")
        self.assertEqual(on_disk["draft"], "Даты на стыке сезонов, цену посчитает коллега.")

    def test_season_case_without_trap_does_attempt(self):
        exam_show.owner_card_trap = _NoTrap
        ok, path, shot = self.build(1, self.season_runner)
        self.assertTrue(ok, path)
        self.assertEqual(self.attempts(), {"season_gate": 1, "noprice_gate": 0})
        self.assertEqual(shot[exam_show.OWNER_CARDS_KEY], [])

    def test_noprice_case_under_trap_sends_nothing_and_keeps_the_card(self):
        import noprice_gate
        ok, path, shot = self.build(2, self.noprice_runner)
        self.assertTrue(ok, path)
        self.assertEqual(self.attempts(), {"season_gate": 0, "noprice_gate": 0})
        cards = shot[exam_show.OWNER_CARDS_KEY]
        self.assertEqual([c["gate"] for c in cards], ["noprice_gate"])
        self.assertTrue(cards[0]["text"].startswith(noprice_gate.CARD_HEAD), cards[0]["text"])
        for part in ("REBEL 300", "2026-10-06", "2026-10-11", "модели нет в записанном правиле"):
            self.assertIn(part, cards[0]["text"])

    def test_noprice_case_without_trap_does_attempt(self):
        exam_show.owner_card_trap = _NoTrap
        ok, path, _shot = self.build(2, self.noprice_runner)
        self.assertTrue(ok, path)
        self.assertEqual(self.attempts(), {"season_gate": 0, "noprice_gate": 1})

    def test_unknown_branch_importing_the_sender_is_caught_too(self):
        """ВТОРОЙ СЛОЙ: ветка, которой нет в переписи, лениво импортирует отправителя сама."""
        import sys
        was = sys.modules.get("dispatch_notify")
        self.addCleanup(lambda: sys.modules.__setitem__("dispatch_notify", was) if was is not None
                        else sys.modules.pop("dispatch_notify", None))
        calls = []

        class LiveStandIn(object):
            @staticmethod
            def deliver(text, reply_markup=None, declared=None):
                calls.append(text)
                return ("боевой (счётчик набора)", True)
        stand_in = LiveStandIn()
        sys.modules["dispatch_notify"] = stand_in

        def runner(c):
            import dispatch_notify
            try:
                dispatch_notify.deliver("⛔ карточка из ветки, которой нет в переписи")
            except RuntimeError:
                pass
            return {"draft": "ответ", "note": ""}
        ok, path, shot = self.build(1, runner)
        self.assertTrue(ok, path)
        self.assertEqual(calls, [])
        self.assertEqual(shot[exam_show.OWNER_CARDS_KEY],
                         [{"gate": "dispatch_notify.deliver",
                           "text": "⛔ карточка из ветки, которой нет в переписи"}])
        self.assertIs(sys.modules.get("dispatch_notify"), stand_in, "модуль не вернулся на место")
        exam_show.owner_card_trap = _NoTrap
        ok, path, _shot = self.build(2, runner)
        self.assertTrue(ok, path)
        self.assertEqual(len(calls), 1, "без ловушки та же ветка обязана дойти до отправителя")

    # ── пункт 2: снимается после круга — и при падении тоже ───────────────────────────────
    def test_trap_is_removed_after_the_round(self):
        import sys
        before = {n: m._default_sender for n, m in self.gates.items()}
        had = "dispatch_notify" in sys.modules
        was = sys.modules.get("dispatch_notify")
        ok, path, _shot = self.build(1, self.season_runner)
        self.assertTrue(ok, path)
        for name, mod in self.gates.items():
            self.assertIs(mod._default_sender, before[name], name)
            self.assertEqual(mod._seen, {}, "пойманная карточка оставила отметку «сказано»")
        self.assertEqual("dispatch_notify" in sys.modules, had)
        self.assertIs(sys.modules.get("dispatch_notify"), was)

    def test_trap_is_removed_when_the_head_falls(self):
        import sys
        before = {n: m._default_sender for n, m in self.gates.items()}
        had = "dispatch_notify" in sys.modules
        was = sys.modules.get("dispatch_notify")

        def falling(c):
            self.season_runner(c)
            raise Boom("голова упала после карточки")
        with self.assertRaises(Boom):
            self.build(1, falling)
        for name, mod in self.gates.items():
            self.assertIs(mod._default_sender, before[name], name)
            self.assertEqual(mod._seen, {})
        self.assertEqual("dispatch_notify" in sys.modules, had)
        self.assertIs(sys.modules.get("dispatch_notify"), was)
        self.assertEqual(self.attempts(), {"season_gate": 0, "noprice_gate": 0})
        self.assertEqual(exam_show.shot_slots(1), [], "упавший круг не кладёт снимка")
        # и следующая, уже живая, отправка в том же процессе снова идёт боевым путём
        import season_gate
        season_gate.note_for(HINTS_CROSS, doc=SEASON_DOC)
        self.assertEqual(self.attempts()["season_gate"], 1)

    def test_trap_that_cannot_be_set_stops_the_head(self):
        """Не встала — голову не зовём, частичная подмена откатана."""
        import season_gate
        before = season_gate._default_sender
        called = []
        exam_show.owner_card_trap = lambda: exam_show.OwnerCardTrap(gates=("season_gate", "json"))

        def runner(c):
            called.append(c)
            return {"draft": "ответ", "note": ""}
        ok, why, shot = self.build(1, runner)
        self.assertFalse(ok)
        self.assertIsNone(shot)
        self.assertEqual(called, [])
        self.assertIn("ловушка карточек владельцу не встала", why)
        self.assertIn("_default_sender", why)
        self.assertIs(season_gate._default_sender, before)

    # ── перепись заперта: форма живых вызовов и список сторожей ───────────────────────────
    def test_live_call_sites_pass_no_sender(self):
        """Путь ответа зовёт сторожей БЕЗ `sender` — значит через `_default_sender`, который
        ловушка и подменяет. Вызовов ровно два: появится третий — перепись устарела."""
        import ast
        import suggest
        with open(suggest.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        sites = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "note_for" and isinstance(node.func.value, ast.Name)):
                sites.append((node.func.value.id, [k.arg for k in node.keywords]))
        self.assertEqual(sorted(s[0] for s in sites), ["noprice_gate", "season_gate"])
        for gate, kws in sites:
            self.assertNotIn("sender", kws, gate)

    def test_every_default_sender_in_the_repo_is_trapped(self):
        """Каждый модуль корня с ленивым `_default_sender` стоит в списке ловушки."""
        root = os.path.dirname(os.path.abspath(exam_show.__file__))
        found = set()
        for name in os.listdir(root):
            # файлы гарда не открываются набором вовсе: по ним идёт отдельное решение владельца
            if not name.endswith(".py") or name.startswith(("test_", "pretool_guard")):
                continue
            with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                if "\ndef _default_sender(" in f.read():
                    found.add(name[:-3])
        self.assertEqual(found, set(exam_show.OWNER_CARD_GATES))

    def test_collector_line_names_the_caught_cards(self):
        self.assertIn("поля нет", exam_show.owner_cards_line(_shot()))
        self.assertIn(": 0 —", exam_show.owner_cards_line(dict(_shot(), owner_cards=[])))
        line = exam_show.owner_cards_line(dict(_shot(), owner_cards=[
            {"gate": "season_gate", "text": "⛔ НЕ СЧИТАЮ"}]))
        self.assertIn(": 1 — НЕ отправлены", line)
        self.assertIn("season_gate", line)


def dispatch_notify_module():
    import dispatch_notify
    return dispatch_notify


# ───────── ЭТАЛОН ЧЕЛОВЕКА РЯДОМ С ОТВЕТОМ БОТА (заведено 17.09.2026, задание 63-n) ─────────
# Текст эталона фикстуры выдуманный и нарочно не похож на ответ бота: смешение было бы видно
# подстрокой, а не рассуждением.

REF_TEXT = "Добрый день! Уточните, пожалуйста, даты и район — подберём."


class TestTheReferenceStandsApart(Base):
    def corpus(self, cases):
        p = os.path.join(self.tmp, "cases.json")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"cases": cases}, f, ensure_ascii=False)
        return p

    LIVE = {"id": 1, "name": "живое первое сообщение", "lang": "ru",
            "lines": ["Здравствуйте, хочу взять байк в аренду"], "expect": {},
            "reference": {"who": "менеджер", "text": REF_TEXT, "mark": "д7·р1", "parts": 1,
                          "gap_sec": 146, "base": "0123456789abcdef"}}

    def with_ref(self, text=REF_TEXT):
        return dict(_shot(), reference={"who": "менеджер", "text": text, "mark": "д7·р1",
                                        "base": "0123456789abcdef"})

    def test_a_shot_without_the_field_gets_no_block_and_the_same_card(self):
        """Снимок основного набора (поля нет) — блока нет, карточка та же, что без правки: вырезка
        блока из карточки со снимком-двойником С ПОЛЕМ даёт ровно карточку без поля."""
        plain = exam_show.card_text(_shot())
        self.assertEqual(exam_show.reference_block(_shot()), "")
        self.assertNotIn("ЭТАЛОН", plain)
        withref = exam_show.card_text(self.with_ref())
        self.assertEqual(withref.replace(exam_show.reference_block(self.with_ref()) + "\n\n", ""),
                         plain)

    def test_the_reference_is_its_own_block_after_the_bot_and_marked_human(self):
        shot = self.with_ref()
        card = exam_show.card_text(shot)
        self.assertEqual(card.count(REF_TEXT), 1)
        bot = card.split("🤖 БОТ:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(bot, shot["draft"], "в блок бота подмешано чужое")
        self.assertNotIn(REF_TEXT, bot)
        self.assertLess(card.index("🤖 БОТ:"), card.index("👤 ЭТАЛОН — ОТВЕТ ЧЕЛОВЕКА"))
        self.assertLess(card.index("👤 ЭТАЛОН — ОТВЕТ ЧЕЛОВЕКА"), card.index("📌 ПОЧЕМУ"))
        self.assertIn("Это НЕ ответ бота", card)
        self.assertIn("д7·р1", card)

    def test_an_empty_reference_says_not_linked_and_invents_nothing(self):
        block = exam_show.reference_block(self.with_ref(text="  "))
        self.assertIn("НЕ сцеплен", block)
        self.assertNotIn(":\n", block)

    def test_freeze_puts_the_reference_in_the_shot_and_hides_it_from_head_and_critic(self):
        seen_head, seen_critic = [], []

        def runner(case):
            seen_head.append(case)
            return {"draft": "ответ бота", "note": "записка"}

        def critic(q, a, tr=""):
            seen_critic.append((q, a, tr))
            return list(HINTS)
        ok, where, shot = exam_show.freeze(1, cases_path=self.corpus([self.LIVE]), runner=runner,
                                           ph=PH, critic=critic)
        self.assertTrue(ok, where)
        self.assertNotIn("reference", seen_head[0], "голова увидела эталон")
        self.assertNotIn(REF_TEXT, json.dumps(seen_head, ensure_ascii=False))
        self.assertNotIn(REF_TEXT, json.dumps(seen_critic, ensure_ascii=False))
        self.assertEqual(shot["reference"]["text"], REF_TEXT)
        self.assertEqual(shot["reference"]["mark"], "д7·р1")
        self.assertEqual(shot["draft"], "ответ бота")
        with open(where, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["reference"]["text"], REF_TEXT)

    def test_freeze_of_a_case_without_the_field_adds_no_key(self):
        case = dict(self.LIVE)
        case.pop("reference")
        ok, where, shot = exam_show.freeze(
            1, cases_path=self.corpus([case]), runner=lambda c: {"draft": "ответ", "note": ""},
            ph=PH, critic=self.critic())
        self.assertTrue(ok, where)
        self.assertNotIn("reference", shot)


# ─────────────── РАЗВОД ХРАНИЛИЩ: живой набор и тренажёр (18.09.2026, задание 63-t) ───────────────
# До развода отказ `LIVE_REFUSED` стоял у показа и тапа живого набора потому, что журнал, стол и база
# уроков у наборов были общие: тап по живому кейсу 13 лёг бы вердиктом кейсу 13 тренажёра. Этот
# класс держит обратное ОБЕИМИ сторонами: у каждого набора свои файлы, и запись одного не видна
# другому ни байтом. ВСЕ пути обоих наборов — во временном месте, включая `LIVE_*`: `use_live_set`
# переключает модуль на них, и без подмены набор писал бы в боевой `exam_live/`.

class TestTwoSetsKeepApart(unittest.TestCase):
    LIVE_NAMES = ("LIVE_CASES", "LIVE_SHOTS", "LIVE_VERDICTS", "LIVE_DESK", "LIVE_LESSONS")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exam_two_sets_")
        self._state = exam_show.set_state()
        self._live_was = dict((k, getattr(exam_show, k)) for k in self.LIVE_NAMES)
        self.addCleanup(self._restore)
        self.paths = {}
        for side in ("trainer", "live"):
            root = os.path.join(self.tmp, side)
            os.makedirs(os.path.join(root, "shots"))
            self.paths[side] = {"cases": os.path.join(root, "cases.json"),
                                "shots": os.path.join(root, "shots"),
                                "verdicts": os.path.join(root, "verdicts.tsv"),
                                "desk": os.path.join(root, "desk.json"),
                                "lessons": os.path.join(root, "lessons.tsv")}
            with open(self.paths[side]["cases"], "w", encoding="utf-8", newline="\n") as f:
                json.dump({"cases": [{"id": 13, "name": "%s 13" % side, "lines": ["вопрос"]},
                                     {"id": 14, "name": "%s 14" % side, "lines": ["вопрос"]}]},
                          f, ensure_ascii=False)
        t, lv = self.paths["trainer"], self.paths["live"]
        exam_show.restore_set(dict(self._state, CASES=t["cases"], SHOTS_DIR=t["shots"],
                                   VERDICTS=t["verdicts"], SESSION=t["desk"],
                                   LESSON_PATH=t["lessons"], HEAD_LESSON_PATH=None,
                                   SET_NAME=exam_show.SET_TRAINER, CB_HEAD="exam",
                                   AGENT_CB_NAME="EXAM_CB_RE"))
        (exam_show.LIVE_CASES, exam_show.LIVE_SHOTS, exam_show.LIVE_VERDICTS, exam_show.LIVE_DESK,
         exam_show.LIVE_LESSONS) = (lv["cases"], lv["shots"], lv["verdicts"], lv["desk"],
                                    lv["lessons"])
        self.trainer = exam_show.set_state()
        exam_show.use_live_set()
        self.live = exam_show.set_state()
        for side, state in (("trainer", self.trainer), ("live", self.live)):
            exam_show.restore_set(state)
            shot = _shot(case=13, total=2, hints=HINTS,
                         corpus=exam_show.corpus_fingerprint(self.paths[side]["cases"]))
            with open(exam_show.shot_path(13, "deadbee"), "w", encoding="utf-8") as f:
                json.dump(shot, f, ensure_ascii=False)
        exam_show.restore_set(self.trainer)
        self.sent = []

    def _restore(self):
        exam_show.restore_set(self._state)
        for k, v in self._live_was.items():
            setattr(exam_show, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def files(self, side):
        """Байты журнала, стола и базы уроков набора (None — файла нет)."""
        out = {}
        for key in ("verdicts", "desk", "lessons"):
            try:
                with open(self.paths[side][key], "rb") as f:
                    out[key] = f.read()
            except OSError:
                out[key] = None
        return out

    def door_kwargs(self):
        def send(text, chat, markup=None):
            self.sent.append(markup)
            return ("ловушка", True, "0")
        return {"right_fn": lambda _w=None: True, "sender": send,
                "agent_fn": lambda callbacks=None: (True, "стенд")}

    def test_use_live_set_moves_every_storage_and_keeps_the_head_base(self):
        self.assertEqual(exam_show.VERDICTS, self.paths["trainer"]["verdicts"])
        exam_show.use_live_set()
        exam_show.use_live_set()                      # повтор не затирает базу головы
        lv = self.paths["live"]
        self.assertEqual((exam_show.CASES, exam_show.SHOTS_DIR, exam_show.VERDICTS,
                          exam_show.SESSION, exam_show.LESSON_PATH),
                         (lv["cases"], lv["shots"], lv["verdicts"], lv["desk"], lv["lessons"]))
        self.assertEqual(exam_show.HEAD_LESSON_PATH, self.paths["trainer"]["lessons"])
        self.assertEqual((exam_show.SET_NAME, exam_show.CB_HEAD, exam_show.AGENT_CB_NAME),
                         ("живой", "examlive", "EXAM_LIVE_CB_RE"))

    def test_a_live_tap_leaves_no_trace_at_the_trainer_case_and_back(self):
        """ДВУСТОРОННИЙ ОТРИЦАТЕЛЬНЫЙ: кейс 13 есть в обоих наборах."""
        before_t, before_l = self.files("trainer"), self.files("live")
        exam_show.restore_set(self.live)
        ok, said = exam_show.tap(13, "ok", "@filipp", **self.door_kwargs())
        self.assertTrue(ok, said)
        self.assertIn("журнал набора «живой»", said)
        self.assertEqual(self.files("trainer"), before_t, "тап живого набора наследил у тренажёра")
        after_l = self.files("live")
        self.assertNotEqual(after_l["verdicts"], before_l["verdicts"])
        row = exam_show.load_verdicts(self.paths["live"]["verdicts"])[0]
        self.assertEqual((row["набор"], row["кейс"], row["вердикт"]), ("живой", "13", "верно"))
        self.assertEqual(row["корпус"], exam_show.corpus_fingerprint(self.paths["live"]["cases"]))

        exam_show.restore_set(self.trainer)
        ok, said = exam_show.tap(13, "ok", "@filipp", **self.door_kwargs())
        self.assertTrue(ok, said)
        self.assertNotIn("уже судим", said, "вердикт живого набора закрыл кейс тренажёра")
        self.assertEqual(self.files("live"), after_l, "тап тренажёра наследил у живого набора")
        row = exam_show.load_verdicts(self.paths["trainer"]["verdicts"])[0]
        self.assertEqual((row["набор"], row["кейс"]), ("тренажёр", "13"))

    def test_toggle_apply_and_own_of_the_live_set_write_only_its_own_files(self):
        before_t = self.files("trainer")
        exam_show.restore_set(self.live)
        kw = self.door_kwargs()
        self.assertTrue(exam_show.toggle(13, 1, "@filipp", right_fn=kw["right_fn"])[0])
        ok, said = exam_show.apply_marked(13, "@filipp", **kw)
        self.assertTrue(ok, said)
        self.assertIn("ЖИВОГО набора", said)
        self.assertNotIn("действуют СРАЗУ", said, "урок живого набора на бота не действует")
        with open(self.paths["live"]["lessons"], encoding="utf-8") as f:
            self.assertEqual(len([x for x in f.read().splitlines()[1:] if x.strip()]), 1)
        self.assertEqual(self.files("trainer"), before_t)
        exam_show.restore_set(self.trainer)
        live_desk = self.files("live")["desk"]
        self.assertTrue(exam_show.own_start(13, "@filipp", right_fn=kw["right_fn"])[0])
        self.assertEqual(self.files("live")["desk"], live_desk, "«своё» тренажёра тронуло стол живого")
        exam_show.restore_set(self.live)
        self.assertEqual(exam_show.pending_own(), (None, None),
                         "ожидание тренажёра видно со стола живого набора")

    def test_the_live_card_has_its_own_head_and_the_live_agent_parses_it(self):
        exam_show.restore_set(self.live)
        shot = _shot(case=13, hints=[_hint("н%d" % i, "п%d" % i)
                                     for i in range(exam_show.HINTS_MAX)])
        cbs = exam_show.card_callbacks(13, shot)
        self.assertTrue(all(c.startswith("examlive:") for c in cbs), cbs)
        rx, words = exam_show.agent_parse()
        self.assertIsNotNone(rx, words)
        self.assertEqual([c for c in cbs if not rx.match(c)], [])
        trx, _w = exam_show.agent_parse(name="EXAM_CB_RE")
        self.assertEqual([c for c in cbs if trx.match(c)], [], "разбор тренажёра взял кнопку живого")
        self.assertIn("ЖИВОЙ НАБОР", exam_show.card_text(shot))
        exam_show.restore_set(self.trainer)
        self.assertEqual([c for c in exam_show.card_callbacks(13, shot) if rx.match(c)], [])
        self.assertNotIn("ЖИВОЙ НАБОР", exam_show.card_text(shot))

    def test_the_agent_routes_the_live_head_to_the_live_door(self):
        owner = pc_agent.ALLOWED_USER_ID
        route = pc_agent._chain_cb_route("examlive:h2:13", owner)
        self.assertEqual((route["ok"], route["kind"], route["action"], route["pid"]),
                         (True, "exam_live", "h2", "13"))
        self.assertIn("живой набор", route["answer"])
        self.assertEqual(pc_agent._chain_cb_route("exam:h2:13", owner)["kind"], "exam")
        for alien in ("examlive:ok:", "examlive:ok:1234", "examlive:ok:1;x", "exam:live:ok:1"):
            self.assertIsNone(pc_agent._exam_live_cb_parse(alien), alien)

    def test_the_catcher_gives_the_text_to_the_later_waiting_desk(self):
        kw = self.door_kwargs()
        exam_show.own_start(13, "@filipp", right_fn=kw["right_fn"], now=1000)
        exam_show.restore_set(self.live)
        exam_show.own_start(13, "@filipp", right_fn=kw["right_fn"], now=1100)
        desks = (("trainer", self.paths["trainer"]["desk"]), ("live", self.paths["live"]["desk"]))
        self.assertEqual(pc_agent._exam_pending_set(desks=desks, now=1200), ("live", "13"))
        late = 1000 + exam_show.PENDING_TTL_SEC + 50
        self.assertEqual(pc_agent._exam_pending_set(desks=desks, now=late), ("live", "13"))
        self.assertIsNone(pc_agent._exam_pending_set(desks=desks, now=late + 100))

    def test_the_live_desk_literal_of_the_catcher_is_the_desk_of_the_door(self):
        self.assertEqual(
            os.path.normcase(os.path.relpath(str(pc_agent.EXAM_LIVE_DESK), str(pc_agent.REPO_DIR))),
            os.path.normcase(os.path.relpath(self._live_was["LIVE_DESK"], exam_show.REPO)))

    def test_the_trace_and_the_rollback_hint_name_the_set(self):
        exam_show.restore_set(self.live)
        self.assertIn("набора «живой»", exam_show.trace())
        exam_show.tap(13, "no", "@filipp", **self.door_kwargs())
        self.assertIn("набор живой", exam_show.trace())
        self.assertIn("exam_show.py --live --rollback",
                      exam_show.tap(13, "ok", "@filipp", **self.door_kwargs())[1])

    def test_only_reach_is_closed_to_the_live_set_and_it_switches_nothing(self):
        was = exam_show.set_state()
        self.assertEqual(exam_show.main(["--live", "--reach"]), 2)
        self.assertEqual(exam_show.set_state(), was)
        self.assertEqual(exam_show.LIVE_CLOSED, ("reach",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
