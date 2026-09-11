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


def _shot(case=1, total=17, corpus="aaaaaaaaaaaaaaaa", commit="deadbee", rules="v1"):
    return {"case": case, "total": total, "name": "первый контакт", "lang": "ru",
            "question": "Здравствуйте! Хочу XMAX 300", "draft": "Здравствуйте! 307 ฿/день.",
            "note": "<<<QUOTE>>> NMAX — 307 ฿/день", "corpus": corpus, "commit": commit,
            "rules": rules, "built_at": "2026-09-11T00:00:00Z"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exam_show_test_")
        self.shots = os.path.join(self.tmp, "shots")
        os.makedirs(self.shots)
        self.log = os.path.join(self.tmp, "verdicts.tsv")
        self._shots_was, exam_show.SHOTS_DIR = exam_show.SHOTS_DIR, self.shots
        self._verd_was, exam_show.VERDICTS = exam_show.VERDICTS, self.log
        self.addCleanup(self._restore)

    def _restore(self):
        exam_show.SHOTS_DIR = self._shots_was
        exam_show.VERDICTS = self._verd_was
        shutil.rmtree(self.tmp, ignore_errors=True)

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
        return lambda: (True, "агент свеж (подставной)")


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
        ok, why, _ = exam_show.freeze(1, runner=runner, ph=PH)
        self.assertFalse(ok)
        self.assertEqual(called, [], "голова не должна была зваться вовсе")
        self.assertIn("уже собран", why)
        self.assertEqual(exam_show.load_shot(1)["draft"], _shot()["draft"])

    def test_freeze_saves_the_three_fingerprints(self):
        ok, path, shot = exam_show.freeze(
            1, runner=lambda c: {"draft": "ответ", "note": "записка"}, ph=PH)
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
            1, runner=lambda c: {"draft": "ответ НОВОГО кода", "note": "новая записка"}, ph=PH)
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
            1, runner=lambda c: {"draft": "   ", "note": "", "unknown": "голова молчала"}, ph=PH)
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
            ph=PH)
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
        real = _sys.modules.get("trainer_run")
        _sys.modules["trainer_run"] = fake
        try:
            ok, where, shot = exam_show.freeze(1, cases_path=self.corpus([self.TEMPLATED]))
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
        self.assertIn(exam_show.card_text(shot), text)
        self.assertTrue(text.startswith(exam_reshow.MARK))
        self.assertEqual(send.calls[0]["markup"], exam_show.markup(1))

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
                                     agent_fn=lambda: (False, "агент СТАРШЕ кода кнопки"))
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


# ─────────────────── ЗАМОК: КНОПКА НЕ СМЕЕТ БЫТЬ НОВЕЕ СВОЕГО ЛОВЦА ──────────────────────

class TestStaleAgentLock(Base):
    def _lock(self, pid=4242, started=1000.0):
        p = os.path.join(self.tmp, "pc_agent.lock")
        with open(p, "w", encoding="utf-8") as f:
            f.write("%d\n" % pid)
            f.write(json.dumps({"pid": pid, "started": started, "script": "pc_agent.py"}))
        return p

    def test_agent_older_than_the_button_forbids_the_show(self):
        lock = self._lock(started=1000.0)
        carries, why = exam_show.agent_carries_button(lock, mtime_fn=lambda p: 2000.0)
        self.assertIs(carries, False)
        self.assertIn("СТАРШЕ", why)
        self.assertIn("обновись", why, "цена снятия обязана быть названа словами")

    def test_agent_newer_than_the_button_allows_it(self):
        lock = self._lock(started=9000.0)
        carries, _ = exam_show.agent_carries_button(lock, mtime_fn=lambda p: 2000.0)
        self.assertIs(carries, True)

    def test_third_outcome_is_unknown_and_it_also_forbids(self):
        """Нет лока / нет момента старта → НЕИЗВЕСТНО, и оно НЕ разрешает показ."""
        carries, why = exam_show.agent_carries_button(os.path.join(self.tmp, "нет-такого"))
        self.assertIsNone(carries)
        self.assertIn("лока", why)
        legacy = os.path.join(self.tmp, "legacy.lock")
        with open(legacy, "w", encoding="utf-8") as f:
            f.write("4242\n")
        carries2, _ = exam_show.agent_carries_button(legacy)
        self.assertIsNone(carries2)
        self.put_shot(_shot(corpus=exam_show.corpus_fingerprint()))
        send = self.sender()
        for verdict in (False, None):
            ok, msg = exam_show.show(1, sender=send, agent_fn=lambda: (verdict, "почему-то"))
            self.assertFalse(ok)
            self.assertIn("НЕИЗВЕСТНО" if verdict is None else "НЕТ", msg)
        self.assertEqual(send.calls, [], "при недоказанном ловце наружу не уходит ничего")


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
        его набирают с консоли, и «OK » — это тот же вердикт, а не второй."""
        self.put_shot(_shot())
        for word in (" ok", "OK", "No "):
            ok, _ = exam_show.tap(1, word, "@свой", right_fn=lambda who: True)
            self.assertTrue(ok, word)

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
        self.put_shot(_shot(commit="abc1234", corpus="ffffffffffffffff", rules="v7"))

    def tap(self, verdict="ok", who="@filipp"):
        return exam_show.tap(1, verdict, who, right_fn=lambda w: True)

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
        self.assertEqual(r["корпус"], "ffffffffffffffff")
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
        self.put_shot(_shot(corpus="ffffffffffffffff") | {"commit": "", "rules": None})
        self.tap()
        r = exam_show.load_verdicts(self.log)[0]
        self.assertEqual(r["коммит"], "НЕ ЗАПИСАНО(коммит)")
        self.assertEqual(r["правила"], "НЕ ЗАПИСАНО(версия правил)")
        self.assertEqual(r["корпус"], "ffffffffffffffff", "целая опора не смеет пострадать")

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
        self.tap("ok")
        self.tap("no")
        exam_show.rollback(2, who="@filipp", path=self.log)
        self.tap("ok")
        nums = [r["номер"] for r in exam_show.load_verdicts(self.log)]
        self.assertEqual(nums, [1, 2, 3], "откат не смеет освободить номер под чужой вердикт")

    def test_rollback_keeps_the_row_and_only_changes_the_state(self):
        self.tap()
        before = self.raw().splitlines()
        ok, msg = exam_show.rollback(1, who="@filipp", path=self.log)
        self.assertTrue(ok, msg)
        after = self.raw().splitlines()
        self.assertEqual(len(before), len(after), "строка обязана остаться на месте")
        rec = exam_show.load_verdicts(self.log)[0]
        self.assertTrue(rec["состояние"].startswith("откачен("))
        self.assertEqual(rec["вердикт"], "верно", "сам вердикт откат не переписывает")
        self.assertEqual(rec["автор"], "@filipp")

    def test_second_rollback_refuses_with_its_own_words(self):
        self.tap()
        exam_show.rollback(1, who="@f", path=self.log)
        ok, msg = exam_show.rollback(1, who="@f", path=self.log)
        self.assertFalse(ok)
        self.assertIn("уже не кандидат", msg)

    def test_rollback_of_a_missing_number_touches_nothing(self):
        self.tap()
        raw = self.raw()
        ok, msg = exam_show.rollback(99, who="@f", path=self.log)
        self.assertFalse(ok)
        self.assertIn("нет", msg)
        self.assertEqual(self.raw(), raw)

    def test_a_tab_in_the_author_name_cannot_break_the_table(self):
        """Экранирование ЧУЖОЕ (lesson_store.esc) — своя вторая копия разъехалась бы с первой."""
        exam_show.tap(1, "ok", "@зло\tсюда\nи сюда", right_fn=lambda w: True, path=self.log)
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

    def test_the_toast_promises_a_candidate_and_not_a_pass(self):
        for data, word in (("exam:ok:3", "кандидат"), ("exam:no:3", "кандидат")):
            self.assertIn(word, pc_agent._chain_cb_route(data, self.OWNER)["answer"])
        self.assertNotIn("зачт", pc_agent._chain_cb_route("exam:ok:3", self.OWNER)["answer"])

    def test_a_stranger_is_refused_before_the_data_is_parsed(self):
        route = pc_agent._chain_cb_route("exam:ok:3", (self.OWNER or 0) + 99999)
        self.assertFalse(route["ok"])
        self.assertIsNone(route["kind"])

    def test_exam_does_not_shadow_the_other_four_buttons(self):
        """Новая ветка не смеет перехватить чужие кнопки — у каждой свой род."""
        self.assertEqual(pc_agent._chain_cb_route("gate:no:abc", self.OWNER)["kind"], "gate")
        self.assertEqual(pc_agent._chain_cb_route("box:free:aabbcc", self.OWNER)["kind"], "box")

    def test_only_a_number_can_travel_from_telegram_into_the_command_line(self):
        """Инвариант безопасности: хвост callback_data просеян регуляркой до цифр."""
        import re
        self.assertEqual(pc_agent.EXAM_CB_RE.pattern, r"^exam:(ok|no):([0-9]{1,3})$")
        self.assertIsNone(re.match(pc_agent.EXAM_CB_RE, "exam:ok:1 --who @root"))


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


def dispatch_notify_module():
    import dispatch_notify
    return dispatch_notify


if __name__ == "__main__":
    unittest.main(verbosity=2)
