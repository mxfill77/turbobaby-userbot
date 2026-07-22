# -*- coding: utf-8 -*-
"""
test_lesson_router.py — юниты обработчика задачи-урока дирижёра (родитель 292, шаг 3/7).
БЕЗ Telegram / Bridge / Anthropic и БЕЗ записи в боевые доки: все побочки инъектируем
фейками, файловые правки — во временные файлы. Golden-правило CLAUDE.md: в позитивах —
ЖИВЫЕ формулировки менеджера + парафразы RU/EN, в негативах — размытое «переделай».

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_router -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import inspect
import json
import os
import tempfile
import unittest

import lesson_router as lr
import moderation_core as mc
import suggest


class TestClassify(unittest.TestCase):
    """Классификация замечания → style|fact|supervision|unclear (ядро шага 3)."""

    def _route(self, remark):
        return lr.classify_lesson_remark(remark)[0]

    def test_style_positives(self):
        # живые + парафразы RU/EN про ТОН/длину/формулировку/приветствие/эмодзи
        for phrase in (
            "Звучит слишком сухо и по-канцелярски, надо теплее",
            "Не здоровайся дважды в одном диалоге",
            "Слишком длинно — пиши короче и живее",
            "Убери канцелярит, звучит как робот",
            "Многовато эмодзи, оставь один",
            "Make it shorter and friendlier, less formal",
            "The tone is too cold — warmer wording please",
        ):
            self.assertEqual(lr.STYLE, self._route(phrase), phrase)

    def test_fact_positives(self):
        # живые + парафразы про НЕВЕРНЫЕ данные/расчёт/факт/FAQ
        for phrase in (
            "Цена на нмакс неверная — тариф другой",
            "Депозит указал неправильно, он 3000, а не 5000",
            "Перепутал даты брони в ответе",
            "Скидку посчитал с ошибкой",
            "Клиенту назвал модель, которой у нас нет в парке",
            "Wrong price for the weekly rental",
            "The deposit amount is incorrect",
        ):
            self.assertEqual(lr.FACT, self._route(phrase), phrase)

    def test_supervision_positives(self):
        # живые + парафразы про сам ДЕТЕКТ/ревизора/чек-лист
        for phrase in (
            "Ревизор должен ловить повтор приветствия",
            "Добавь в чек-лист проверку выдуманной цены",
            "Детектор пропустил утечку служебного маркера",
            "Новый класс дефекта: бот обещает наличие байка",
            "Should catch fake prices that aren't in the quote",
            "Add to checklist: greeting after Telegram auto-hello",
        ):
            self.assertEqual(lr.SUPERVISION, self._route(phrase), phrase)

    def test_unclear_negatives(self):
        # размытое/пустое → НЕЯСНОЕ (не угадываем)
        for phrase in ("", "   ", "Плохо, переделай", "Не так", "Ерунда получилась",
                       "Так не пойдёт", "Что-то не то"):
            self.assertEqual(lr.UNCLEAR, self._route(phrase), repr(phrase))

    def test_priority_supervision_over_style(self):
        # надзорный маркер бьёт попутный стилевой (приветствие) — это про ДЕТЕКТ, не про тон
        self.assertEqual(lr.SUPERVISION,
                         self._route("Ревизор не поймал повторное приветствие"))

    def test_priority_fact_over_style(self):
        # конкретный фактический дефект важнее общего «звучит»
        self.assertEqual(lr.FACT, self._route("Звучит нормально, но цена неверная"))


class TestClassifyType(unittest.TestCase):
    """Родитель 112, шаг 1/8: ВТОРАЯ ось урока — type = behavior | code | unsure + уверенность.
    Ортогональна classify_lesson_remark (та про адресата, эта про природу урока). Golden-правило
    CLAUDE.md: позитивы — ЖИВЫЕ формулировки + парафразы RU/EN; негативы — размытое «переделай»."""

    def _type(self, remark):
        return lr.classify_lesson_type(remark)["type"]

    def test_spec_two_examples(self):
        # ДВА примера прямо из спеки шага 1/8 — дословно
        self.assertEqual(lr.BEHAVIOR, self._type('не пиши "данные получил"'))
        self.assertEqual(lr.CODE, self._type("цена из столбца J"))

    def test_behavior_positives(self):
        # ПОВЕДЕНИЕ: тон/формулировки/что говорить-не говорить/дефолты/порядок вопросов
        for phrase in (
            "Не пиши «данные получил», это звучит по-роботски",
            "Звучит слишком сухо — пиши теплее и по-человечески",
            "Не обещай наличие, просто предложи посмотреть",
            "Сначала спроси даты, потом модель — порядок вопросов другой",
            "По умолчанию предлагай Nmax, если клиент не назвал модель",
            "Don't say we got the data, just answer the question",
            "The tone is too cold, warmer wording please",
        ):
            self.assertEqual(lr.BEHAVIOR, self._type(phrase), phrase)

    def test_code_positives(self):
        # МАШИНА: цифры/поля-столбцы/гарды/инварианты/резолверы/транспорт/интеграции
        for phrase in (
            "Цена берётся из столбца J, а не из K",
            "Депозит считается неправильно — поправь формулу",
            "Резолвер зон доставки роняет позиционный список",
            "Гард не пропускает выдуманную цену, которой нет в quote",
            "Инвариант суммы нарушен при округлении",
            "The price must come from column J of the sheet",
            "Deposit number is wrong in the integration",
        ):
            self.assertEqual(lr.CODE, self._type(phrase), phrase)

    def test_unsure_negatives(self):
        # размытое/пустое → unsure (не угадываем тип)
        for phrase in ("", "   ", "Плохо, переделай", "Не так", "Так не пойдёт", "Что-то не то"):
            self.assertEqual(lr.UNSURE, self._type(phrase), repr(phrase))

    def test_confidence_high_for_clean_signal(self):
        # чистый сигнал одной оси → high; behavior|code всегда приходят с high (инвариант)
        for phrase, want_type in (('не пиши "данные получил"', lr.BEHAVIOR),
                                  ("цена из столбца J", lr.CODE)):
            d = lr.classify_lesson_type(phrase)
            self.assertEqual(want_type, d["type"], phrase)
            self.assertEqual("high", d["confidence"], phrase)

    def test_confidence_low_for_unsure(self):
        # unsure всегда несёт confidence=low (правило спеки «при низкой — type=unsure»)
        d = lr.classify_lesson_type("плохо, переделай")
        self.assertEqual(lr.UNSURE, d["type"])
        self.assertEqual("low", d["confidence"])

    def test_mixed_signal_without_dominance_is_unsure(self):
        # обе оси зацепились без резкого перевеса → тип неясен → unsure (не угадываем)
        d = lr.classify_lesson_type("короче про цену")     # behavior «короче» ≈ code «цена»
        self.assertEqual(lr.UNSURE, d["type"])
        self.assertEqual("low", d["confidence"])

    def test_axis_is_orthogonal_to_route(self):
        # НАДЗОР-урок (первая ось = supervision) по ВТОРОЙ оси = code (правка машины-детектора):
        # оси независимы, второй тип не дублирует маршрут
        remark = "ревизор должен ловить выдуманную цену, которой нет в quote"
        self.assertEqual(lr.SUPERVISION, lr.classify_lesson_remark(remark)[0])
        self.assertEqual(lr.CODE, self._type(remark))

    # ===== КЛАСС-ФИКС #164: условие на состоянии данных/трекера → code (речь-акт не перевешивает) =====
    def test_golden_292_code_on_both_axes(self):
        # УРОК 292 (разбор #164): раньше ось-2 давала behavior на «не пиши», хотя это ФАКТ на СОСТОЯНИИ
        # ДАННЫХ. Фикс-класс: условие «если клиент прислал только локацию» → code ОБЕИМИ осями.
        remark = 'не пиши "данные получил", если клиент прислал только локацию'
        self.assertEqual(lr.CODE, self._type(remark))                    # ось-2: природа = code
        self.assertEqual("high", lr.classify_lesson_type(remark)["confidence"])
        self.assertEqual(lr.FACT, lr.classify_lesson_remark(remark)[0])  # ось-1: маршрут = факт/логика

    def test_golden_data_condition_positives_are_code(self):
        # ЖИВАЯ 292 + парафразы RU/EN: правило с условием на состоянии данных/трекера → code НЕЗАВИСИМО
        # от глагола речи («не пиши»/«не подтверждай»/«нельзя писать»/«don't confirm»).
        for phrase in (
            'не пиши "данные получил", если клиент прислал только локацию — данных ещё нет',
            'не подтверждай "данные получил", если пришла только геолокация',
            "когда данные ещё не собраны, не пиши что всё получил",
            'нельзя писать "данные получены", пока не прислал паспорт',
            "don't confirm data received when the tracker is still empty",
        ):
            self.assertEqual(lr.CODE, self._type(phrase), phrase)

    def test_golden_behavior_without_data_condition(self):
        # Чистая манера/тон/дефолт-предложение БЕЗ условия на данных → behavior (речь-акт остаётся собой):
        # спека-голдены задачи + условие-фолбэк дефолта (условие лишь «когда действует», суть — что предлагать).
        for phrase in (
            "пиши теплее, без канцелярита",
            "не предлагай старое поколение по умолчанию",
            "По умолчанию предлагай Nmax, если клиент не назвал модель",   # условие-фолбэк дефолта ≠ данные
            "если клиент злится, отвечай мягче",                           # условие на настроении ≠ данные
        ):
            self.assertEqual(lr.BEHAVIOR, self._type(phrase), phrase)

    def test_golden_bridge_silent_is_code(self):
        # «не называй цену, если Bridge молчит» → code (цена/Bridge — машина; условие лишь усиливает).
        self.assertEqual(lr.CODE, self._type("не называй цену, если Bridge молчит"))

    def test_data_condition_bare_manner_stays_behavior(self):
        # ЗАМОК регресса: БЕЗ условия «данные получил» — это МАНЕРА (робо-фраза) → behavior (step6/спека).
        # Именно УСЛОВИЕ, а не слово «данные», переводит урок в code.
        self.assertEqual(lr.BEHAVIOR, self._type('не пиши "данные получил"'))

    # ===== КЛАСС-ФИКС живого кейса 22.07 16:49 (ОБРАТНЫЙ #164): упоминание денег ≠ правка расчёта =====
    def test_golden_live_1649_wording_is_behavior(self):
        """ЖИВОЙ ДЕФЕКТ 22.07 16:49 ДОСЛОВНО: урок ушёл CODE-карточкой владельцу из-за ОДНОГО слова
        «тариф» в _CODE_KW (beh=0, cod=1 → «чистый code-сигнал»). Это чистое ПОВЕДЕНИЕ: правило о
        ФОРМУЛИРОВКЕ уже вычисленного (не обещать уточнить то, что уже посчитано) — LLM исполняет его
        из playbook. Обе оси разводим одинаково: ось-2 behavior, ось-1 стиль (книга правил)."""
        remark = ("не жди с ответом уточню-и-вернусь — сразу указывай стоимость, "
                  "если тариф известен")
        d = lr.classify_lesson_type(remark)
        self.assertEqual(lr.BEHAVIOR, d["type"], d)                       # ось-2: природа = поведение
        self.assertEqual("high", d["confidence"], d)
        self.assertEqual(lr.STYLE, lr.classify_lesson_remark(remark)[0])  # ось-1: маршрут = книга правил

    def test_golden_wording_positives_are_behavior(self):
        # Формулировка УЖЕ ВЫЧИСЛЕННОГО результата (не обещать/не дублировать/не переспрашивать/сразу
        # называть) → behavior ДАЖЕ со словами цена/тариф/стоимость/доставка. Парафразы RU/EN.
        for phrase in (
            "не жди с ответом уточню-и-вернусь — сразу указывай стоимость, если тариф известен",
            "не обещай перезвонить с ценой, если тариф уже назван — просто повтори цифру",
            "не дублируй стоимость доставки, если уже написал её выше",
            "не переспрашивай даты, если клиент их назвал — сразу давай цену",
            "don't promise to check the price later, state it right away",
        ):
            self.assertEqual(lr.BEHAVIOR, self._type(phrase), phrase)

    def test_golden_source_and_condition_stay_code(self):
        """КОНТРАСТ (граница с обеих сторон): урок, меняющий ИСТОЧНИК или УСЛОВИЕ расчёта, остаётся
        code — речь-акт «сразу называй» его НЕ перетягивает. Включает регресс #164-стороны: «цену
        бери из столбца J» и «не называй цену, если Bridge молчит»."""
        for phrase in (
            "цену бери из столбца J",                          # ИСТОЧНИК → code (регресс #164 цел)
            "не называй цену, если Bridge молчит",             # УСЛОВИЕ/недоступность → code
            "сразу указывай стоимость из столбца J",           # тот же речь-акт + ИСТОЧНИК → code
            "сразу называй цену, если Bridge молчит",          # тот же речь-акт + недоступность → code
            "не обещай скидку — пересчитай её по формуле",     # способ расчёта → code
        ):
            self.assertEqual(lr.CODE, self._type(phrase), phrase)

    def test_golden_live_1649_routes_to_playbook_not_owner_card(self):
        """ЖИВОЙ КОНТУР целиком (route_lesson_urok, канал «урок:»): тот же урок ДОСЛОВНО обязан уйти
        ПРАВИЛОМ В PLAYBOOK с реплаем «✅ Принято…», а НЕ карточкой «нужен код-фикс» владельцу — как
        было в живом дефекте 16:49. Все побочки инъектированы (без Telegram/1160/playbook)."""
        task = ('[урок:правка от @Filipp] родитель 292 — обучение на карточку черновика\n'
                'окно диалога: клиент 12345\n'
                'карточка модер-группы: msg=777\n'
                'Замечание: не жди с ответом уточню-и-вернусь — сразу указывай стоимость, '
                'если тариф известен\n'
                'Исходный черновик: Уточню стоимость и вернусь к вам.')
        seen = {}

        def _style(rule):
            seen["style"] = rule
            return "added"

        def _owner(card):
            seen["owner"] = card
            return ("инбокс 1160", True)

        def _reply(card_msg_id, text, buttons=None):
            seen["reply"] = text
            return 1

        dec = lr.route_lesson_urok(task, append_style=_style, find_conflict=lambda r: "",
                                   notify_owner=_owner, reply_moderation=_reply)
        self.assertEqual(lr.BEHAVIOR, dec["type"], dec)
        self.assertEqual("done", dec["status"], dec)
        self.assertFalse(dec.get("delegate"), dec)
        self.assertIn("style", seen)                     # правило записано в книгу правил
        self.assertNotIn("owner", seen)                  # карточка «нужен код-фикс» НЕ ушла владельцу
        self.assertIn("Принято", seen.get("reply", ""))  # учителю ушёл ack о принятом правиле


class TestParse(unittest.TestCase):
    """Разбор задачи-урока (контракт с build_lesson_task шага 2)."""

    def test_is_lesson_task(self):
        self.assertTrue(lr.is_lesson_task("[урок:правка от @danya] родитель 292 — …"))
        self.assertFalse(lr.is_lesson_task("[шаг 3/7 родитель 292] сделай X"))
        self.assertFalse(lr.is_lesson_task("тз: обычная задача"))
        self.assertFalse(lr.is_lesson_task(""))

    def test_parse_real_builder_output(self):
        # берём НАСТОЯЩИЙ билдер шага 2 — гуард контракта между шагами (без approver-гейта)
        import moderation_core
        draft = {"id": 7, "client_id": 555, "client_ref": "Иван (555)",
                 "draft": "Аренда от 1200฿/сутки"}
        les = {"kind": "не так", "remark": "цена неверная, тариф другой",
               "window": 555, "draft_id": 7}
        text = moderation_core.build_lesson_task(les, draft, "danya")
        self.assertTrue(lr.is_lesson_task(text))
        p = lr.parse_lesson_task(text)
        self.assertEqual("цена неверная, тариф другой", p["remark"])
        self.assertEqual("не так", p["kind"])
        self.assertEqual("@danya", p["who"])
        self.assertIn("555", p["window"])

    def test_parse_missing_fields_graceful(self):
        p = lr.parse_lesson_task("[урок:правка] без тела")
        self.assertEqual("", p["remark"])           # нет «Замечание:» → пусто, не падаем


class TestSupervisionAppend(unittest.TestCase):
    """Надзорный append: строка-класс в чек-лист (во временный файл)."""

    def _tmp(self, body):
        fd, path = tempfile.mkstemp(suffix=".md", text=True)
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_appends_next_letter(self):
        path = self._tmp("Чек-лист:\n- [класс а] числа не из quote;\n- [класс б] чужая модель;\n")
        self.assertEqual("added", lr.append_checklist_class("ревизор должен ловить обещание наличия", path))
        with open(path, encoding="utf-8") as f:
            out = f.read()
        self.assertIn("[класс в]", out)             # следующая свободная буква после б
        self.assertIn("обещание наличия", out)
        self.assertIn("[класс а]", out)             # старое не тронуто

    def test_dedup(self):
        path = self._tmp("- [класс а] бот обещает наличие байка;\n")
        self.assertEqual("duplicate", lr.append_checklist_class("бот обещает наличие байка", path))

    def test_empty_and_missing_file(self):
        self.assertEqual("error", lr.append_checklist_class("   ", self._tmp("x")))
        self.assertEqual("error", lr.append_checklist_class("правило", os.path.join(
            tempfile.gettempdir(), "no_such_lesson_checklist_zzz.md")))

    def test_next_letter_from_current_checklist(self):
        # реальный чек-лист (а..ж) → следующая буква з
        with open(lr.REVIZOR_CHECKLIST, encoding="utf-8") as f:
            body = f.read()
        self.assertEqual("з", lr._next_class_letter(body))


class TestHandle(unittest.TestCase):
    """handle_lesson_task: маршрутизация с инъекцией всех побочек (без боевых доков/1160)."""

    def _task(self, remark):
        return (f"[урок:правка от @danya] родитель 292 — замечание менеджера в копилку обучения\n"
                f"окно диалога: Иван (555) (client_id=555) · черновик #7\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Аренда от 1200฿/сутки")

    def test_style_route_calls_style_sink(self):
        seen = {}
        def style_sink(rule):
            seen["rule"] = rule
            return "added"
        dec = lr.handle_lesson_task(self._task("звучит сухо, пиши теплее"),
                                    append_style=style_sink,
                                    append_checklist=lambda r: self.fail("checklist не должен звать"),
                                    notify_owner=lambda c: self.fail("owner не должен звать"))
        self.assertEqual(lr.STYLE, dec["route"])
        self.assertFalse(dec["delegate"])
        self.assertEqual("done", dec["status"])
        self.assertIn("сухо", seen["rule"])         # именно замечание ушло в книгу правил

    def test_supervision_route_calls_checklist_sink(self):
        seen = {}
        def check_sink(rule):
            seen["rule"] = rule
            return "added"
        dec = lr.handle_lesson_task(self._task("ревизор должен ловить выдуманную цену"),
                                    append_checklist=check_sink,
                                    append_style=lambda r: self.fail("style не должен звать"))
        self.assertEqual(lr.SUPERVISION, dec["route"])
        self.assertEqual("done", dec["status"])
        self.assertIn("выдуманную цену", seen["rule"])

    def test_fact_route_delegates_with_test_note(self):
        dec = lr.handle_lesson_task(self._task("депозит указал неправильно, он 3000"))
        self.assertEqual(lr.FACT, dec["route"])
        self.assertTrue(dec["delegate"])
        self.assertIn("юнит-тест", dec["delegate_text"])    # требование теста передано планировщику
        self.assertIn("депозит указал неправильно", dec["delegate_text"])
        self.assertIn("Bridge", dec["delegate_text"])       # деньги/Bridge запрещены явно

    def test_unclear_route_cards_owner_to_1160(self):
        # НЕЯСНОЕ → карточка владельцу, НЕ угадываем; ни style, ни checklist не зовём
        sent = {}
        def owner_sink(card):
            sent["card"] = card
            return True
        dec = lr.handle_lesson_task(self._task("плохо, переделай"),
                                    append_style=lambda r: self.fail("style не должен звать"),
                                    append_checklist=lambda r: self.fail("checklist не должен звать"),
                                    notify_owner=owner_sink)
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertFalse(dec["delegate"])
        self.assertEqual("done", dec["status"])
        self.assertTrue(dec["delivered"])
        self.assertIn("плохо, переделай", sent["card"])     # исходное замечание в карточке
        self.assertIn("1160", dec["result"])

    def test_unclear_owner_channel_down_is_failsafe(self):
        # канал 1160 упал → не роняем, замечание не теряем (видно в результате/логе)
        dec = lr.handle_lesson_task(self._task("не то"),
                                    notify_owner=lambda c: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertEqual("done", dec["status"])
        self.assertFalse(dec["delivered"])
        self.assertIn("не доставлена", dec["result"])

    def test_unclear_delivered_reports_channel_honestly(self):
        # ГОЛДЕН живого провала: карточка ДОШЛА (sink подтвердил канал (channel, ok)) — рапорт обязан
        # сказать «доставлено через <канал>» и НЕ содержать ложного «недоступен»/«не доставлена».
        # Так закрыт разрыв: исполнитель рапортовал «1160 недоступно», хотя карточка реально дошла.
        dec = lr.handle_lesson_task(self._task("плохо, переделай"),
                                    notify_owner=lambda c: ("инбокс 1160", True))
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertTrue(dec["delivered"])
        self.assertEqual("инбокс 1160", dec["channel"])
        self.assertIn("доставлено через инбокс 1160", dec["result"])
        self.assertNotIn("недоступен", dec["result"])       # НЕТ ложной жалобы на недоступность
        self.assertNotIn("не доставлена", dec["result"])

    def test_unclear_delivered_via_fallback_channel_is_honest(self):
        # доставка ушла ФОЛБЭКОМ (личка вместо инбокса) — рапорт всё равно честный «доставлено через …»
        dec = lr.handle_lesson_task(self._task("что-то не то"),
                                    notify_owner=lambda c: ("личку (фолбэк)", True))
        self.assertTrue(dec["delivered"])
        self.assertIn("доставлено через личку (фолбэк)", dec["result"])
        self.assertNotIn("недоступен", dec["result"])

    def test_unclear_sink_reports_not_delivered(self):
        # sink ЧЕСТНО отрапортовал провал доставки (channel, ok=False) → «не доставлена», канал пуст
        dec = lr.handle_lesson_task(self._task("не то"),
                                    notify_owner=lambda c: ("", False))
        self.assertFalse(dec["delivered"])
        self.assertEqual("", dec["channel"])
        self.assertIn("не доставлена", dec["result"])

    def test_unclear_fire_and_forget_sink_cannot_confirm(self):
        # РЕГРЕССИЯ, из-за которой был ложный рапорт: fire-and-forget-sink (Popen) возвращал None —
        # доставку ПОДТВЕРДИТЬ невозможно ⇒ delivered=False. Отсюда требование: боевой sink обязан
        # вернуть НАСТОЯЩИЙ статус (channel, ok), а не None (см. _deliver_owner_card).
        dec = lr.handle_lesson_task(self._task("переделай"), notify_owner=lambda c: None)
        self.assertFalse(dec["delivered"])
        self.assertIn("не доставлена", dec["result"])

    def test_style_sink_error_is_failed_not_crash(self):
        dec = lr.handle_lesson_task(self._task("пиши короче"),
                                    append_style=lambda r: (_ for _ in ()).throw(OSError("disk")))
        self.assertEqual(lr.STYLE, dec["route"])
        self.assertEqual("failed", dec["status"])   # sink упал → failed-карта, замечание не потеряно
        self.assertIn("короче", dec["result"])

    def test_supervision_sink_error_is_failed(self):
        dec = lr.handle_lesson_task(self._task("ревизор пропустил утечку служебного"),
                                    append_checklist=lambda r: "error")
        self.assertEqual(lr.SUPERVISION, dec["route"])
        self.assertEqual("failed", dec["status"])


class TestAckAfterCommit(unittest.TestCase):
    """Родитель 292, шаг 4: подтверждение «урок принят…» уходит ТОЛЬКО после РЕАЛЬНОГО коммита.
    Ключевой инвариант — подтверждение НЕ уходит до коммита (gate ack_after_commit)."""

    def test_ack_text_format(self):
        # формат родителя 292: суть + куда записан + «применится со следующего ответа»
        ack = lr.build_lesson_ack("депозит 3000, не 5000", "код/критфакты/FAQ + golden-тест")
        self.assertIn("Урок принят", ack)
        self.assertIn("депозит 3000, не 5000", ack)          # суть
        self.assertIn("код/критфакты/FAQ + golden-тест", ack)  # куда записан
        self.assertIn("применится со следующего ответа", ack)

    def test_ack_withheld_without_commit(self):
        # НЕТ коммита → подтверждение НЕ формируется (None). Это и есть «не уходит до коммита».
        self.assertIsNone(lr.ack_after_commit("", "суть", "чек-лист", verify=lambda r: self.fail(
            "verify не должен зваться на пустом ref")))
        self.assertIsNone(lr.ack_after_commit(None, "суть", "чек-лист"))
        # ref есть, но НЕ реальный коммит (verify=False) → всё равно None
        self.assertIsNone(lr.ack_after_commit("deadbeef", "суть", "чек-лист", verify=lambda r: False))

    def test_ack_sent_only_after_real_commit(self):
        # verify подтвердил реальный коммит → подтверждение формируется с сутью и «куда»
        seen = {}
        def verify(ref):
            seen["ref"] = ref
            return True
        ack = lr.ack_after_commit("abc1234", "цена нмакс исправлена", "код + golden-тест", verify=verify)
        self.assertEqual("abc1234", seen["ref"])             # гейт реально проверял ИМЕННО этот ref
        self.assertIsNotNone(ack)
        self.assertIn("цена нмакс исправлена", ack)
        self.assertIn("применится со следующего ответа", ack)

    def test_verify_commit_failsafe(self):
        # пустой ref → False без обращения к git; сбой раннера → False (нет доказательства коммита)
        self.assertFalse(lr.verify_commit("", run=lambda r: self.fail("run на пустом ref")))
        self.assertFalse(lr.verify_commit("   "))
        self.assertFalse(lr.verify_commit("x", run=lambda r: (_ for _ in ()).throw(OSError("no git"))))
        self.assertTrue(lr.verify_commit("realhash", run=lambda r: True))


class TestLessonCardAndAckMaterial(unittest.TestCase):
    """Шаг 4: задача-урок несёт координату карточки модер-группы (точка реплая), а handle_lesson_task
    отдаёт материал подтверждения (суть/куда/пути-к-коммиту) для гейта после коммита."""

    def test_parse_card_msg_id_from_real_builder(self):
        import moderation_core
        draft = {"id": 7, "client_id": 555, "client_ref": "Иван (555)",
                 "draft": "Аренда от 1200฿/сутки", "card_msg_id": 90210}
        les = {"kind": "не так", "remark": "цена неверная", "window": 555, "draft_id": 7}
        text = moderation_core.build_lesson_task(les, draft, "danya")
        self.assertIn("msg=90210", text)                     # координата карточки в тексте задачи
        self.assertEqual("90210", lr.parse_lesson_task(text)["card_msg_id"])

    def test_card_msg_id_absent_is_graceful(self):
        # черновик без card_msg_id → «msg=?», парс отдаёт пусто (не падаем)
        import moderation_core
        text = moderation_core.build_lesson_task(
            {"kind": "урок", "remark": "r", "window": 1, "draft_id": 1}, {"id": 1, "client_id": 1}, "danya")
        self.assertIn("msg=?", text)
        self.assertEqual("", lr.parse_lesson_task(text)["card_msg_id"])

    def _task_card(self, remark, card="90210"):
        return (f"[урок:правка от @danya] родитель 292 — замечание менеджера в копилку обучения\n"
                f"окно диалога: Иван (555) (client_id=555) · черновик #7\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Аренда от 1200฿/сутки")

    def test_supervision_yields_ack_material_and_tracked_commit_path(self):
        # НАДЗОР добавлен → есть суть, «куда», координата карточки И tracked-путь чек-листа к коммиту
        dec = lr.handle_lesson_task(self._task_card("ревизор должен ловить выдуманную цену"),
                                    append_checklist=lambda r: "added")
        self.assertEqual("ревизор должен ловить выдуманную цену", dec["ack_subject"])
        self.assertIn("чек-лист", dec["ack_where"])
        self.assertEqual("90210", dec["card_msg_id"])
        self.assertIn(lr._REVIZOR_REL, dec["commit_paths"])  # чек-лист отслеживается → коммитим его

    def test_supervision_duplicate_has_nothing_to_commit(self):
        # правило уже в чек-листе → коммитить нечего (пустые commit_paths) ⇒ подтверждение не задвоится
        dec = lr.handle_lesson_task(self._task_card("ревизор должен ловить выдуманную цену"),
                                    append_checklist=lambda r: "duplicate")
        self.assertEqual("done", dec["status"])
        self.assertEqual([], dec["commit_paths"])

    def test_style_route_has_no_repo_commit_path(self):
        # СТИЛЬ пишет в gitignored playbook manager-bot (свой контур) → в этом репо коммитить нечего
        dec = lr.handle_lesson_task(self._task_card("звучит сухо, пиши теплее"),
                                    append_style=lambda r: "added")
        self.assertEqual(lr.STYLE, dec["route"])
        self.assertEqual([], dec["commit_paths"])
        self.assertIn("книг", dec["ack_where"].lower())      # «книгу правил»

    def test_fact_route_carries_ack_material_for_planner_commit(self):
        # ФАКТ делегируется; подтверждение уйдёт после коммита планировщика — материал уже собран
        dec = lr.handle_lesson_task(self._task_card("депозит указал неправильно, он 3000"))
        self.assertTrue(dec["delegate"])
        self.assertEqual("депозит указал неправильно, он 3000", dec["ack_subject"])
        self.assertIn("golden", dec["ack_where"].lower())
        self.assertEqual("90210", dec["card_msg_id"])


class TestDefaultRouteDestinations(unittest.TestCase):
    """Родитель 306, шаг 3/6: связать 4 класса классификатора с ИМЕНОВАННЫМИ В СПЕКЕ адресатами
    через РЕАЛЬНЫЕ дефолты модуля — не только инъектированные фейки. Разрыв «тест ≠ реальность»
    (как в #306 шаг 2/6 про message_id): юниты классификатора проверяли КОНСТАНТУ маршрута, а
    handle-тесты подставляли фейк-sink'и, но НИКТО не проверял, что ДЕФОЛТНЫЙ sink каждого класса
    бьёт в свой адресат спеки: СТИЛЬ→playbook поверх STYLE_GUIDE, НАДЗОР→docs/revizor_checklist.md,
    ФАКТ→код/критфакты/FAQ+тест, НЕЯСНОЕ→владельцу 1160. Тихий репойнт дефолта прошёл бы мимо тестов.
    Прод-доки/1160 не трогаем: STYLE-sink подменяем спаем, остальное — через константы/материал."""

    def test_style_default_sink_is_playbook_over_style_guide(self):
        # СТИЛЬ-дефолт РЕАЛЬНО пишет в playbook «Выученные правила» (слой ПОВЕРХ STYLE_GUIDE
        # клиентского бота), а не куда попало. Прод-playbook не трогаем — спай вместо боевого append.
        import suggest
        seen = {}
        orig = suggest.append_playbook_rule
        suggest.append_playbook_rule = lambda rule, now=None: (seen.__setitem__("rule", rule), "added")[1]
        self.addCleanup(lambda: setattr(suggest, "append_playbook_rule", orig))
        self.assertTrue(str(suggest.STYLE_GUIDE).strip())          # адресат-база стиля существует
        self.assertEqual("added", lr._default_append_style("звучит слишком сухо, пиши теплее"))
        self.assertEqual("звучит слишком сухо, пиши теплее", seen["rule"])  # правило ушло в playbook

    def test_supervision_default_path_is_revizor_checklist(self):
        # НАДЗОР-дефолт целится в docs/revizor_checklist.md (живой чек-лист ревизора) — путь из спеки.
        self.assertEqual(os.path.join("docs", "revizor_checklist.md"), lr._REVIZOR_REL)
        self.assertTrue(lr.REVIZOR_CHECKLIST.replace("\\", "/").endswith("docs/revizor_checklist.md"))
        # и handle БЕЗ инъекции чек-листа сообщает ИМЕННО этот tracked-путь к коммиту (адресат надзора)
        dec = lr.handle_lesson_task(self._sup_task(), append_checklist=lambda r: "duplicate")
        self.assertEqual(lr.SUPERVISION, dec["route"])
        # duplicate → коммитить нечего, но «куда записан» всё равно указывает на чек-лист ревизора
        self.assertIn("чек-лист", dec["ack_where"])

    def _sup_task(self):
        return ("[урок:урок от @filipp] родитель 306 — замечание менеджера\n"
                "окно диалога: Маша (777) (client_id=777) · черновик #12\n"
                "карточка модер-группы: msg=90310\n"
                "Замечание: ревизор должен ловить выдуманную цену, которой нет в quote\n"
                "Исходный черновик: Есть Aerox за 900฿")

    def test_unclear_default_owner_channel_undelivered_in_standalone(self):
        # Дефолт-канал владельца (1160) в standalone ОТСУТСТВУЕТ → недоставлено (демон инъектит боевой
        # _notify_critical → инбокс 1160). Проверяем, что дефолт честно возвращает False, а не «ок».
        self.assertFalse(lr._default_notify_owner("любая карточка-уточнение"))

    def test_four_class_where_map_matches_spec_destinations(self):
        # Единая карта «применённый класс → куда записан» отражает адресатов спеки шага 3 дословно:
        self.assertIn("STYLE_GUIDE", lr._ROUTE_WHERE[lr.STYLE])          # СТИЛЬ → поверх STYLE_GUIDE
        self.assertIn("чек-лист", lr._ROUTE_WHERE[lr.SUPERVISION])        # НАДЗОР → чек-лист ревизора
        self.assertIn("FAQ", lr._ROUTE_WHERE[lr.FACT])                    # ФАКТ → код/критфакты/FAQ+тест
        self.assertNotIn(lr.UNCLEAR, lr._ROUTE_WHERE)                     # НЕЯСНОЕ → не в правило/код, а владельцу


class TestClassifyLiveRemarks(unittest.TestCase):
    """Golden-правило CLAUDE.md: классификатор обязан верно разводить ДОСЛОВНЫЕ реплики учителя из
    живого цикла (fixtures/lesson_cycle_292.json), а не только идеализированные парафразы. Берём
    remark ровно как в фикстуре (после снятия триггера правка:/урок:/не так:) → тот же класс, что
    ждёт цикл. Это анти-разрыв «тест ≠ реальность» на входе классификатора (а не только в handle)."""

    def _route(self, remark):
        return lr.classify_lesson_remark(remark)[0]

    def test_live_fixture_remarks_route_as_expected(self):
        # (замечание из живого окна, ожидаемый класс) — дословно из lesson_cycle_292.json
        for remark, want in (
            ("суточный тариф на Nmax 1500, а не 1200 — цена неверная", lr.FACT),
            ("ревизор должен ловить выдуманную цену, которой нет в quote", lr.SUPERVISION),
            ("звучит слишком сухо и по-канцелярски, пиши теплее и по-человечески", lr.STYLE),
            ("плохо, переделай", lr.UNCLEAR),
        ):
            self.assertEqual(want, self._route(remark), remark)


class TestLessonClassifyLLM(unittest.TestCase):
    """Родитель 334, шаг 1/6: думательный классификатор урока (THINKER_MODEL, _thinker_exec-паттерн).
    Реальный claude НЕ дёргаем — думатель инъектируем фейком. Юнит-вызов на 2 примерах (ФАКТ/СТИЛЬ)
    + ретрай + fail-safe. Golden-правило CLAUDE.md: замечания — дословные реплики учителя."""

    def test_two_examples_classify(self):
        # ПРИМЕР 1 (ФАКТ): дословная реплика учителя про неверную цену → class ФАКТ, route fact
        fact_json = ('{"reading":"суточный тариф Nmax назван неверно",'
                     '"class":"ФАКТ","confidence":"high","plan":"поправить тариф Nmax в критфактах + тест"}')
        dec = lr.classify_lesson_llm("суточный тариф на Nmax 1500, а не 1200 — цена неверная",
                                     draft="Аренда Nmax от 1200฿/сутки", window="Иван (555)",
                                     think=lambda prompt: fact_json)
        self.assertEqual("ФАКТ", dec["class"])
        self.assertEqual(lr.FACT, dec["route"])
        self.assertEqual("high", dec["confidence"])
        self.assertIn("тариф", dec["reading"])
        self.assertTrue(dec["plan"])

        # ПРИМЕР 2 (СТИЛЬ): дословная реплика про тон → class СТИЛЬ, route style
        style_json = ('{"reading":"ответ звучит сухо/канцелярски","class":"СТИЛЬ",'
                      '"confidence":"high","plan":"переписать теплее, по-человечески"}')
        dec2 = lr.classify_lesson_llm("звучит слишком сухо и по-канцелярски, пиши теплее",
                                      draft="Аренда доступна.", window="Маша (777)",
                                      think=lambda prompt: style_json)
        self.assertEqual("СТИЛЬ", dec2["class"])
        self.assertEqual(lr.STYLE, dec2["route"])

    def test_prompt_carries_verbatim_remark_draft_window(self):
        seen = {}
        def think(prompt):
            seen["prompt"] = prompt
            return '{"reading":"r","class":"НАДЗОР","confidence":"low","plan":"p"}'
        dec = lr.classify_lesson_llm("ревизор должен ловить выдуманную цену",
                                     draft="Есть Aerox за 900฿", window="Петя (888)", think=think)
        self.assertEqual(lr.SUPERVISION, dec["route"])
        self.assertIn("ревизор должен ловить выдуманную цену", seen["prompt"])  # замечание ДОСЛОВНО
        self.assertIn("Есть Aerox за 900฿", seen["prompt"])                     # черновик в промпте
        self.assertIn("Петя (888)", seen["prompt"])                            # окно в промпте

    def test_one_retry_on_bad_json_then_ok(self):
        # первый ответ — мусор (без JSON), второй — валиден: РОВНО один ретрай спасает
        calls = {"n": 0}
        def think(prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                return "извини, не смог"                # брак → ретрай
            return '{"reading":"r","class":"ФАКТ","confidence":"low","plan":"p"}'
        dec = lr.classify_lesson_llm("депозит неправильный, он 3000", think=think)
        self.assertEqual(2, calls["n"])                # был ровно один ретрай
        self.assertEqual(lr.FACT, dec["route"])

    def test_no_more_than_one_retry_failsafe_none(self):
        # брак дважды подряд → None (fail-safe: вызывающий откатится на keyword-классификатор)
        calls = {"n": 0}
        def think(prompt):
            calls["n"] += 1
            return "мусор без json"
        self.assertIsNone(lr.classify_lesson_llm("плохо, переделай", think=think))
        self.assertEqual(2, calls["n"])                # база + ровно один ретрай, не больше

    def test_invalid_class_is_rejected(self):
        # class вне СТИЛЬ|ФАКТ|НАДЗОР → брак (None после ретрая), не угадываем маршрут
        self.assertIsNone(lr.classify_lesson_llm(
            "x", think=lambda p: '{"reading":"r","class":"НЕЯСНО","confidence":"high","plan":"p"}'))

    def test_bad_confidence_normalized_to_low(self):
        # неведомая уверенность → консервативно low (урок не теряем, но и не доверяем как явному)
        dec = lr.classify_lesson_llm(
            "x", think=lambda p: '{"reading":"r","class":"СТИЛЬ","confidence":"maybe","plan":"p"}')
        self.assertEqual("low", dec["confidence"])

    def test_thinker_exception_is_failsafe_none(self):
        # сбой думателя (исключение) не роняет — оба вызова падают → None
        self.assertIsNone(lr.classify_lesson_llm(
            "x", think=lambda p: (_ for _ in ()).throw(RuntimeError("claude down"))))

    def test_json_with_wrapper_garbage_parsed(self):
        # думатель обернул JSON в текст/markdown — берём от первой { до последней }
        dec = lr.classify_lesson_llm("x", think=lambda p: (
            'вот ответ:\n```json\n{"reading":"r","class":"НАДЗОР","confidence":"high","plan":"p"}\n```'))
        self.assertEqual(lr.SUPERVISION, dec["route"])


class TestLessonHighConfidence(unittest.TestCase):
    """Родитель 334, шаг 2/6: confidence=high — урок берётся В РАБОТУ СРАЗУ по LLM-маршруту, учителю
    уходит РЕПЛАЕМ в модер-группу «Понял так: <reading>. Делаю: <plan>», дальше ШТАТНЫЙ пайплайн без
    изменений и БЕЗ карточки «переформулируй конкретнее». Классификатор мокаем (реальный claude не
    дёргаем). Golden-правило CLAUDE.md: замечания — дословные реплики учителя."""

    def _task(self, remark, card="90210"):
        return (f"[урок:правка от @danya] родитель 334 — замечание менеджера в копилку обучения\n"
                f"окно диалога: Иван (555) (client_id=555) · черновик #7\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Аренда Nmax от 1200฿/сутки")

    def _high(self, cls, route, reading="суть ясна", plan="правлю причину"):
        def classify(remark, draft="", window=""):
            return {"reading": reading, "class": cls, "confidence": "high",
                    "plan": plan, "route": route}
        return classify

    def test_understanding_format_exact(self):
        u = lr.build_lesson_understanding("суточный тариф Nmax назван неверно",
                                          "поправить тариф Nmax в критфактах + тест")
        self.assertIn("Понял так: суточный тариф Nmax назван неверно", u)
        self.assertIn("Делаю: поправить тариф Nmax в критфактах + тест", u)

    def test_high_fact_takes_immediately_and_replies_to_mod_group(self):
        # дословная реплика про неверную цену, confidence=high ФАКТ → route fact, делегируем,
        # а В МОДЕР-ГРУППУ на сообщение с уроком (card_msg_id) ушёл реплай «Понял так… Делаю…».
        sent = {}
        def reply_mod(card_msg_id, text):
            sent["card"], sent["text"] = card_msg_id, text
            return True
        dec = lr.handle_lesson_task(
            self._task("суточный тариф на Nmax 1500, а не 1200 — цена неверная"),
            classify=self._high("ФАКТ", lr.FACT, reading="суточный тариф Nmax назван неверно",
                                plan="поправить тариф Nmax в критфактах + тест"),
            reply_moderation=reply_mod)
        self.assertEqual(lr.FACT, dec["route"])
        self.assertTrue(dec["delegate"])                       # штатный пайплайн: планировщик диф→коммит
        self.assertEqual("high", dec["confidence"])
        self.assertEqual("90210", sent["card"])                # реплай на сообщение с уроком
        self.assertIn("Понял так: суточный тариф Nmax назван неверно", sent["text"])
        self.assertIn("Делаю: поправить тариф Nmax в критфактах + тест", sent["text"])
        self.assertEqual(sent["text"], dec["understanding"])   # то же в результате задачи (рапорт)

    def test_high_style_runs_normal_pipeline_no_clarification(self):
        # confidence=high СТИЛЬ → штатный стиль-пайплайн (playbook), НИКАКОЙ карточки-уточнения
        seen = {}
        dec = lr.handle_lesson_task(
            self._task("звучит слишком сухо и по-канцелярски, пиши теплее"),
            classify=self._high("СТИЛЬ", lr.STYLE),
            append_style=lambda r: (seen.__setitem__("rule", r), "added")[1],
            notify_owner=lambda c: self.fail("владельца НЕ зовём для high (не «неясно»)"),
            reply_moderation=lambda cid, t: True)
        self.assertEqual(lr.STYLE, dec["route"])
        self.assertEqual("done", dec["status"])
        self.assertIn("сухо", seen["rule"])
        self.assertNotIn("переформулируй", dec.get("result", "").lower())

    def test_high_overrides_keyword_unclear(self):
        # ключевой смысл шага: РАЗМЫТАЯ по ключевым словам реплика («плохо, переделай» → keyword UNCLEAR),
        # но LLM уверен (high СТИЛЬ) → урок В РАБОТУ, а НЕ карточка «переформулируй конкретнее».
        dec = lr.handle_lesson_task(
            self._task("плохо, переделай"),
            classify=self._high("СТИЛЬ", lr.STYLE, reading="тон слишком резкий", plan="смягчить тон"),
            append_style=lambda r: "added",
            notify_owner=lambda c: self.fail("для high владельца-уточнение НЕ шлём"),
            reply_moderation=lambda cid, t: True)
        self.assertEqual(lr.STYLE, dec["route"])               # не UNCLEAR — high перекрыл keyword
        self.assertIn("LLM-high", dec["reason"])

    def test_low_confidence_asks_teacher_not_high_pipeline(self):
        # confidence=low (шаг 3) → НЕ берём урок в работу вслепую и НЕ шлём understanding «Делаю…»:
        # спрашиваем учителя «верно?» реплаем в модер-группу и ждём (status=waiting, pending_low).
        sent = {}
        dec = lr.handle_lesson_task(
            self._task("депозит указал неправильно, он 3000"),
            classify=lambda r, d="", w="": {"reading": "депозит назван неверно", "class": "ФАКТ",
                                             "confidence": "low", "plan": "p", "route": lr.FACT},
            reply_moderation=lambda cid, t: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual("waiting", dec["status"])             # урок не закрыт — ждём подтверждения
        self.assertFalse(dec["delegate"])                      # в работу пока НЕ отдали
        self.assertEqual("low", dec["confidence"])
        self.assertIn("pending_low", dec)                      # состояние ожидания сохранено
        self.assertIn("верно?", sent["text"])                  # спросили «верно?» в модер-группе
        self.assertNotIn("Делаю", sent["text"])                # это НЕ high-реплай «Понял так… Делаю…»

    def test_llm_none_falls_back_to_keyword(self):
        # думатель вернул None (брак/сбой) → keyword-классификатор, реплай-понимание не шлём
        dec = lr.handle_lesson_task(
            self._task("звучит сухо, пиши теплее"),
            classify=lambda r, d="", w="": None,
            append_style=lambda r: "added",
            reply_moderation=lambda cid, t: self.fail("None → реплай НЕ шлём"))
        self.assertEqual(lr.STYLE, dec["route"])
        self.assertNotIn("understanding", dec)

    def test_classify_exception_is_failsafe_keyword(self):
        # сбой думателя (исключение) не роняет — откат на keyword
        dec = lr.handle_lesson_task(
            self._task("ревизор должен ловить выдуманную цену"),
            classify=lambda r, d="", w="": (_ for _ in ()).throw(RuntimeError("claude down")),
            append_checklist=lambda r: "added",
            reply_moderation=lambda cid, t: self.fail("сбой classify → high-путь не идёт"))
        self.assertEqual(lr.SUPERVISION, dec["route"])

    def test_reply_sink_failure_does_not_lose_lesson(self):
        # реплай в модер-группу упал → урок ВСЁ РАВНО берётся в работу (пайплайн идёт), understanding в dec
        dec = lr.handle_lesson_task(
            self._task("депозит указал неправильно, он 3000"),
            classify=self._high("ФАКТ", lr.FACT),
            reply_moderation=lambda cid, t: (_ for _ in ()).throw(OSError("mod group down")))
        self.assertEqual(lr.FACT, dec["route"])
        self.assertTrue(dec["delegate"])
        self.assertIsNotNone(dec["understanding"])

    def test_classify_receives_remark_draft_window(self):
        # думатель питается ДОСЛОВНЫМ замечанием + черновиком + окном (парс из тела задачи)
        seen = {}
        def classify(remark, draft="", window=""):
            seen.update(remark=remark, draft=draft, window=window)
            return {"reading": "r", "class": "ФАКТ", "confidence": "high", "plan": "p", "route": lr.FACT}
        lr.handle_lesson_task(self._task("цена неверная — тариф другой"),
                              classify=classify, reply_moderation=lambda cid, t: True)
        self.assertEqual("цена неверная — тариф другой", seen["remark"])
        self.assertIn("Nmax", seen["draft"])                   # исходный черновик распарсен и передан
        self.assertIn("555", seen["window"])

    def test_default_no_injection_stays_keyword(self):
        # БЕЗ инъекции classify и с выключенным рубильником — чистый keyword-путь (никакого claude)
        self.assertFalse(lr._lesson_llm_enabled())             # рубильник по умолчанию OFF
        dec = lr.handle_lesson_task(self._task("звучит сухо, пиши теплее"),
                                    append_style=lambda r: "added")
        self.assertEqual(lr.STYLE, dec["route"])
        self.assertNotIn("confidence", dec)                    # LLM-путь не активировался


class TestLessonLowConfidence(unittest.TestCase):
    """Родитель 334, шаг 3/6: confidence=low — урок НЕ берётся вслепую. Реплаем в ТУ ЖЕ модер-группу
    спрашиваем учителя «Понял так: <reading> — верно? Ответь "да" или поправь одним сообщением» и
    сохраняем состояние ожидания. По ответу: «да» от INTAKE_APPROVERS → урок в работу как high;
    текстовая поправка → РОВНО одна пере-классификация; снова low → «отложил, разберёт владелец» +
    карточка в 1160. Классификатор мокаем (реальный claude не дёргаем)."""

    def _task(self, remark, card="90210"):
        return (f"[урок:правка от @danya] родитель 334 — замечание менеджера в копилку обучения\n"
                f"окно диалога: Иван (555) (client_id=555) · черновик #7\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Аренда Nmax от 1200฿/сутки")

    def _low(self, cls, route, reading="суть не до конца ясна", plan="уточню и поправлю"):
        def classify(remark, draft="", window=""):
            return {"reading": reading, "class": cls, "confidence": "low", "plan": plan, "route": route}
        return classify

    # --- текстовые билдеры ветки low --------------------------------------------------------
    def test_confirm_text_format_exact(self):
        c = lr.build_lesson_low_confirm("депозит назван неверно")
        self.assertIn("Понял так: депозит назван неверно", c)
        self.assertIn("верно?", c)
        self.assertIn('Ответь "да"', c)
        self.assertIn("поправь одним сообщением", c)

    def test_confirm_empty_reading_is_graceful(self):
        self.assertIn("верно?", lr.build_lesson_low_confirm(""))    # формат не роняем

    def test_affirmative_detects_short_agreement(self):
        for yes in ("да", "Да!", "да, верно", "всё так", "верно", "точно", "ага", "yes",
                    "ок", "да все верно", "правильно 👍"):
            self.assertTrue(lr.is_lesson_affirmative(yes), yes)

    def test_affirmative_rejects_correction_text(self):
        # текстовая поправка (в т.ч. начинается с «нет») — НЕ согласие
        for no in ("нет, дело в цене", "плохо, переделай", "депозит 3000, а не 5000",
                   "поправь тон, слишком сухо", "", "   ", "это про надзор — ревизор должен ловить"):
            self.assertFalse(lr.is_lesson_affirmative(no), repr(no))

    # --- вход в ожидание --------------------------------------------------------------------
    def test_low_enters_wait_and_replies_to_mod_group(self):
        sent = {}
        dec = lr.handle_lesson_task(
            self._task("депозит вроде не так"),
            classify=self._low("ФАКТ", lr.FACT, reading="депозит назван неверно"),
            reply_moderation=lambda cid, t: (sent.update(card=cid, text=t), True)[1],
            notify_owner=lambda c: self.fail("владельца НЕ зовём в момент ожидания (не «неясно»)"))
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("low", dec["confidence"])
        self.assertEqual("90210", sent["card"])                # реплай на сообщение с уроком
        self.assertIn("Понял так: депозит назван неверно", sent["text"])
        self.assertIn("верно?", sent["text"])
        # состояние ожидания несёт всё для возобновления
        p = dec["pending_low"]
        self.assertEqual(lr.FACT, p["route"])
        self.assertEqual("90210", p["card_msg_id"])
        self.assertIn("депозит вроде не так", p["remark"])
        self.assertIn("Nmax", p["draft"])                      # черновик сохранён для пере-классификации

    def test_low_reply_api_error_fails_with_diagnosis_not_silent_wait(self):
        # КЛАСС-ФИКС #102: реплай-переспрос упал (API-ошибка) → урок НЕ висит молча в ожидании, а падает
        # failed с ДИАГНОЗОМ + карточка-уточнение владельцу в 1160 (переспрос не дошёл — решай вручную).
        owner = {}
        dec = lr.handle_lesson_task(
            self._task("что-то с ценой"),
            classify=self._low("ФАКТ", lr.FACT),
            reply_moderation=lambda cid, t: (_ for _ in ()).throw(OSError("mod group down")),
            notify_owner=lambda c: (owner.update(card=c), ("инбокс 1160", True))[1])
        self.assertEqual("failed", dec["status"])                 # НЕ waiting — тихого ожидания нет
        self.assertNotIn("pending_low", dec)                      # в ожидание не уходим
        self.assertIn("НЕ доставлен", dec["reason"])              # диагноз: переспрос не дошёл
        self.assertIn("mod group down", dec["reason"])            # конкретная причина в диагнозе
        self.assertIn("card", owner)                              # карточка ушла владельцу
        self.assertTrue(dec["delivered"])                         # доставку владельцу подтвердили
        self.assertIn("НЕ доставлен", dec["result"])

    def test_low_reply_no_message_id_fails_not_waits(self):
        # реплай «доставлен» без message_id (Bot API ok, но факт не подтверждён) → falsy → тоже failed
        dec = lr.handle_lesson_task(
            self._task("что-то с ценой"),
            classify=self._low("СТИЛЬ", lr.STYLE),
            reply_moderation=lambda cid, t: False,                # не доставлено (нет message_id)
            notify_owner=lambda c: ("инбокс 1160", True))
        self.assertEqual("failed", dec["status"])
        self.assertIn("не подтвердил доставку", dec["reason"].lower() + dec["result"].lower())

    # --- resume: «да» от аппрувера ----------------------------------------------------------
    def test_resume_yes_from_approver_takes_lesson_as_high(self):
        # «да» от INTAKE_APPROVERS → урок в работу как high по маршруту из pending (учитель согласился)
        sent = {}
        dec0 = lr.handle_lesson_task(self._task("депозит вроде не так"),
                                     classify=self._low("ФАКТ", lr.FACT, reading="депозит назван неверно",
                                                        plan="поправить депозит в критфактах + тест"),
                                     reply_moderation=lambda cid, t: True)
        dec = lr.resume_low_lesson(dec0["pending_low"], "да", is_approver=True,
                                   reply_moderation=lambda cid, t: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual(lr.FACT, dec["route"])
        self.assertEqual("high", dec["confidence"])
        self.assertEqual("approved", dec["resumed"])
        self.assertTrue(dec["delegate"])                       # ФАКТ → штатно планировщику
        self.assertEqual("90210", sent["card"])                # реплай «Понял так… Делаю…» в модер-группу
        self.assertIn("Понял так: депозит назван неверно", sent["text"])
        self.assertIn("Делаю: поправить депозит в критфактах + тест", sent["text"])

    def test_resume_yes_style_runs_style_sink(self):
        seen = {}
        pending = {"text": self._task("тон вроде сухой"), "route": lr.STYLE, "class": "СТИЛЬ",
                   "reading": "тон сух", "plan": "смягчить тон", "remark": "тон вроде сухой",
                   "draft": "", "window": "555", "card_msg_id": "90210"}
        dec = lr.resume_low_lesson(pending, "да, верно", is_approver=True,
                                   append_style=lambda r: (seen.__setitem__("rule", r), "added")[1],
                                   reply_moderation=lambda cid, t: True)
        self.assertEqual(lr.STYLE, dec["route"])
        self.assertEqual("done", dec["status"])
        self.assertIn("тон вроде сухой", seen["rule"])         # исходное замечание ушло в книгу правил

    def test_resume_yes_from_non_approver_is_ignored(self):
        # «да» НЕ от INTAKE_APPROVERS → подтверждение не принимаем, продолжаем ждать уполномоченного
        pending = {"text": self._task("депозит вроде не так"), "route": lr.FACT, "class": "ФАКТ",
                   "reading": "r", "plan": "p", "remark": "депозит вроде не так", "card_msg_id": "90210"}
        dec = lr.resume_low_lesson(pending, "да", is_approver=False,
                                   append_style=lambda r: self.fail("не применяем без аппрувера"),
                                   append_checklist=lambda r: self.fail("не применяем без аппрувера"),
                                   reply_moderation=lambda cid, t: self.fail("не отвечаем на чужое «да»"))
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("ignored_non_approver", dec["resumed"])

    # --- resume: текстовая поправка ---------------------------------------------------------
    def test_resume_correction_reclassifies_once_to_high(self):
        # текстовая поправка → РОВНО одна пере-классификация; вернулся high → урок в работу как high
        calls = {"n": 0}
        def classify(remark, draft="", window=""):
            calls["n"] += 1
            self.assertIn("депозит 3000", remark)              # пере-классифицируем по ТЕКСТУ поправки
            return {"reading": "депозит 3000, не 5000", "class": "ФАКТ", "confidence": "high",
                    "plan": "поправить депозит + тест", "route": lr.FACT}
        sent = {}
        pending = {"text": self._task("вроде не так с депозитом"), "route": lr.STYLE, "class": "СТИЛЬ",
                   "reading": "r", "plan": "p", "remark": "вроде не так с депозитом",
                   "draft": "Аренда Nmax от 1200฿/сутки", "window": "555", "card_msg_id": "90210"}
        dec = lr.resume_low_lesson(pending, "нет, депозит 3000, не 5000", is_approver=True,
                                   classify=classify,
                                   reply_moderation=lambda cid, t: (sent.update(text=t), True)[1])
        self.assertEqual(1, calls["n"])                        # РОВНО одна пере-классификация
        self.assertEqual(lr.FACT, dec["route"])                # новый класс из поправки
        self.assertEqual("high", dec["confidence"])
        self.assertEqual("reclassified_high", dec["resumed"])
        self.assertTrue(dec["delegate"])
        self.assertIn("Понял так: депозит 3000, не 5000", sent["text"])

    def test_resume_correction_still_low_defers_to_owner(self):
        # поправка снова low → «отложил, разберёт владелец» реплаем + карточка-уточнение владельцу в 1160
        replies = []
        sent_owner = {}
        pending = {"text": self._task("вроде не то"), "route": lr.STYLE, "class": "СТИЛЬ",
                   "reading": "r", "plan": "p", "remark": "вроде не то",
                   "draft": "черновик", "window": "555", "card_msg_id": "90210"}
        dec = lr.resume_low_lesson(
            pending, "ну переделай как-нибудь", is_approver=True,
            classify=lambda r, d="", w="": {"reading": "r2", "class": "СТИЛЬ", "confidence": "low",
                                            "plan": "p2", "route": lr.STYLE},
            notify_owner=lambda c: (sent_owner.__setitem__("card", c), ("инбокс 1160", True))[1],
            reply_moderation=lambda cid, t: (replies.append(t), True)[1])
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertEqual("deferred_to_owner", dec["resumed"])
        self.assertEqual("done", dec["status"])
        self.assertTrue(dec["delivered"])
        self.assertEqual("инбокс 1160", dec["channel"])
        self.assertTrue(any("тлож" in r.lower() for r in replies))   # реплай «отложил…» в модер-группу
        self.assertIn("ну переделай как-нибудь", sent_owner["card"])  # поправка — в карточке владельцу
        self.assertIn("доставлено через инбокс 1160", dec["result"])

    def test_resume_correction_classify_none_defers_to_owner(self):
        # пере-классификация вернула None (брак/сбой) → так же откладываем владельцу (не угадываем)
        dec = lr.resume_low_lesson(
            {"text": self._task("не то"), "route": lr.FACT, "class": "ФАКТ", "reading": "r",
             "plan": "p", "remark": "не то", "draft": "", "window": "", "card_msg_id": "90210"},
            "ещё какая-то невнятица", is_approver=True,
            classify=lambda r, d="", w="": None,
            notify_owner=lambda c: ("инбокс 1160", True),
            reply_moderation=lambda cid, t: True)
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertEqual("deferred_to_owner", dec["resumed"])

    def test_resume_defer_owner_channel_down_is_failsafe(self):
        # канал 1160 упал при откладывании → не роняем, замечание видно в результате/логе
        dec = lr.resume_low_lesson(
            {"text": self._task("не то"), "route": lr.FACT, "class": "ФАКТ", "reading": "r",
             "plan": "p", "remark": "не то", "draft": "", "window": "", "card_msg_id": "90210"},
            "снова невнятно", is_approver=True,
            classify=lambda r, d="", w="": {"reading": "r", "class": "ФАКТ", "confidence": "low",
                                            "plan": "p", "route": lr.FACT},
            notify_owner=lambda c: (_ for _ in ()).throw(RuntimeError("1160 down")),
            reply_moderation=lambda cid, t: True)
        self.assertFalse(dec["delivered"])
        self.assertIn("не доставлена", dec["result"])

    def test_high_confidence_still_immediate_no_wait(self):
        # регрессия шага 2: confidence=high по-прежнему берётся СРАЗУ (не в ожидание)
        dec = lr.handle_lesson_task(self._task("депозит указал неправильно, он 3000"),
                                    classify=lambda r, d="", w="": {"reading": "r", "class": "ФАКТ",
                                                                    "confidence": "high", "plan": "p",
                                                                    "route": lr.FACT},
                                    reply_moderation=lambda cid, t: True)
        self.assertEqual("high", dec["confidence"])
        self.assertNotIn("pending_low", dec)                   # не ожидание — сразу в работу


class TestLessonLowWaitTimeout(unittest.TestCase):
    """Родитель 334, шаг 4/6: таймаут ожидания ответа на low-уточнение. Учитель молчит на «верно?»
    дольше 24ч → урок НЕ теряем: карточка-уточнение владельцу в 1160 + СНЯТИЕ ожидания. Повторный
    тик карточку НЕ задваивает. Время ПОДМЕНЯЕМ (now инъектируется), Telegram/1160 — фейками."""

    _DAY = 24 * 3600

    def _pending(self, created_at=1_000_000.0):
        # состояние ожидания как из _enter_low_wait (с меткой времени created_at)
        return {"text": "[урок:правка от @danya] родитель 334 …", "route": lr.FACT, "class": "ФАКТ",
                "reading": "депозит назван неверно", "plan": "p", "remark": "депозит вроде не так",
                "draft": "Аренда Nmax от 1200฿/сутки", "window": "Иван (555)", "who": "@danya",
                "card_msg_id": "90210", "created_at": created_at}

    def test_enter_low_wait_stamps_created_at(self):
        # _enter_low_wait штампует created_at (epoch) → таймаут-тику есть от чего считать возраст
        dec = lr.handle_lesson_task(
            self._task_task("депозит вроде не так"),
            classify=lambda r, d="", w="": {"reading": "r", "class": "ФАКТ", "confidence": "low",
                                            "plan": "p", "route": lr.FACT},
            reply_moderation=lambda cid, t: True)
        self.assertEqual("waiting", dec["status"])
        self.assertIn("created_at", dec["pending_low"])
        self.assertGreater(dec["pending_low"]["created_at"], 0)

    def _task_task(self, remark, card="90210"):
        return (f"[урок:правка от @danya] родитель 334 — замечание менеджера в копилку обучения\n"
                f"окно диалога: Иван (555) (client_id=555) · черновик #7\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Аренда Nmax от 1200฿/сутки")

    def test_not_expired_before_24h_no_card(self):
        # молчание < 24ч → тик НЕ трогает: ждём ответа учителя (None, карточки нет)
        pending = self._pending(created_at=1_000_000.0)
        now = 1_000_000.0 + self._DAY - 60          # 23ч59м — ещё рано
        self.assertIsNone(lr.check_low_wait_timeout(
            pending, now=now, notify_owner=lambda c: self.fail("рано — владельца НЕ зовём")))
        self.assertNotIn("timed_out", pending)      # ожидание НЕ снято

    def test_expired_after_24h_cards_owner_and_clears_wait(self):
        # молчание ≥ 24ч → карточка-уточнение владельцу в 1160 + ожидание снято (clear_wait)
        sent = {}
        pending = self._pending(created_at=1_000_000.0)
        now = 1_000_000.0 + self._DAY + 1           # 24ч+ — таймаут
        dec = lr.check_low_wait_timeout(
            pending, now=now, notify_owner=lambda c: (sent.__setitem__("card", c), ("инбокс 1160", True))[1])
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertEqual("timeout_to_owner", dec["resumed"])
        self.assertTrue(dec["clear_wait"])          # ожидание снять
        self.assertEqual("done", dec["status"])
        self.assertTrue(dec["delivered"])
        self.assertEqual("инбокс 1160", dec["channel"])
        self.assertIn("депозит вроде не так", sent["card"])   # замечание в карточке владельцу
        self.assertIn("доставлено через инбокс 1160", dec["result"])
        self.assertTrue(pending["timed_out"])       # состояние помечено снятым

    def test_second_tick_does_not_duplicate_card(self):
        # ключ шага 4: ПОВТОРНЫЙ тик по уже сработавшему таймауту карточку НЕ задваивает (идемпотентно)
        calls = {"n": 0}
        pending = self._pending(created_at=1_000_000.0)
        now = 1_000_000.0 + self._DAY + 1
        def notify(card):
            calls["n"] += 1
            return ("инбокс 1160", True)
        dec1 = lr.check_low_wait_timeout(pending, now=now, notify_owner=notify)
        self.assertIsNotNone(dec1)
        self.assertEqual(1, calls["n"])
        # тик ещё раз (демон не успел удалить pending) → None, владельца НЕ зовём повторно
        dec2 = lr.check_low_wait_timeout(pending, now=now + self._DAY, notify_owner=notify)
        self.assertIsNone(dec2)
        self.assertEqual(1, calls["n"])             # карточка отправлена РОВНО один раз

    def test_missing_created_at_is_not_timed_out_blindly(self):
        # нет метки времени → возраст 0 → НЕ таймаутим вслепую (свежее/неизвестное состояние)
        pending = {"remark": "r", "route": lr.FACT}
        self.assertEqual(0.0, lr.low_wait_age_sec(pending, now=9_999_999_999.0))
        self.assertIsNone(lr.check_low_wait_timeout(
            pending, now=9_999_999_999.0, notify_owner=lambda c: self.fail("нет метки → не таймаутим")))

    def test_expired_owner_channel_down_is_failsafe(self):
        # канал 1160 упал при таймауте → не роняем, урок виден в результате/логе, ожидание всё равно снято
        pending = self._pending(created_at=1_000_000.0)
        now = 1_000_000.0 + self._DAY + 1
        dec = lr.check_low_wait_timeout(
            pending, now=now, notify_owner=lambda c: (_ for _ in ()).throw(RuntimeError("1160 down")))
        self.assertFalse(dec["delivered"])
        self.assertIn("не доставлена", dec["result"])
        self.assertTrue(dec["clear_wait"])          # даже при провале доставки ожидание снимаем (урок в логе)
        self.assertTrue(pending["timed_out"])

    def test_custom_timeout_respected(self):
        # timeout инъектируем (демон может задать иной порог) — тик считает по нему
        pending = self._pending(created_at=1_000_000.0)
        self.assertIsNone(lr.check_low_wait_timeout(
            pending, now=1_000_000.0 + 100, timeout=200, notify_owner=lambda c: self.fail("рано")))
        dec = lr.check_low_wait_timeout(
            pending, now=1_000_000.0 + 250, timeout=200, notify_owner=lambda c: ("инбокс 1160", True))
        self.assertEqual("timeout_to_owner", dec["resumed"])


FIXTURE_292 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "lesson_cycle_292.json")
TEACHERS = ("filipp", "filipp_alt", "danya", "dasha")


class TestLesson334Golden332AndInvariants(unittest.TestCase):
    """Родитель 334, шаг 5/6 — ПРИЁМОЧНЫЙ голден LLM-классификатора урока на РЕАЛЬНОМ провале #332 +
    замок инвариантов. Голден-фраза (правило-класс CLAUDE.md: дословный урок из живого окна): менеджер
    поправил черновик-подтверждение бота и оставил урок «не говори "локацию и данные получил", если
    клиент прислал их в прошлой брони» — это про ЛОГИКУ ТРЕКЕРА / СВЕЖЕСТЬ СЕССИИ, класс ФАКТ.

    Разрыв «тест ≠ реальность»: раньше keyword-классификатор такую фразу НЕ ловил (ни цена/модель/дата,
    ни тон) → UNCLEAR → карточка владельцу «переформулируй». КЛАСС-ФИКС #164 закрыл разрыв на keyword-
    слое: правило с УСЛОВИЕМ на состоянии данных/трекера («…если/когда данные собраны/присланы/получены…»)
    ловится как ФАКТ независимо от речь-акта «не говори/пиши» (см. test_keyword_now_catches_...). Думатель
    сверху даёт ПРОЧТЕНИЕ/ПЛАН (трекер/свежесть сессии) и уверенность → урок берётся В РАБОТУ СРАЗУ,
    БЕЗ «переформулируй» и с осмысленным «Понял так… Делаю…». Прогоняем ОДНУ
    живую фразу через все ветки #334 (high / low / «да» / поправка / таймаут) + замок инвариантов:
    триггер только от INTAKE_APPROVERS, «урок принят» ТОЛЬКО после коммита, клиенту НИЧЕГО не шлётся,
    регресс цикла 292 цел. Реальный claude НЕ дёргаем — классификатор инъектируем фейком; текст задачи
    собираем РЕАЛЬНЫМ moderation_core.build_lesson_task, чтобы конвейер сборки был живой."""

    # Дословный урок #332 + черновик, который он правит (бот подтвердил получение уже присланного).
    REMARK = 'не говори "локацию и данные получил", если клиент прислал их в прошлой брони'
    DRAFT_TEXT = "Локацию и данные получил, спасибо! Оформляю бронь."
    CARD = {"id": 42, "client_id": 606, "client_ref": "@nikita",
            "draft": DRAFT_TEXT, "card_msg_id": 90332}
    # Прочтение думателя — ПРО ТРЕКЕР/СВЕЖЕСТЬ СЕССИИ (ожидаемый контракт классификатора для этой фразы).
    READING = ("не подтверждать «локацию и данные получил», когда они уже пришли в прошлой брони — "
               "вопрос свежести сессии и логики трекера окна")
    PLAN = "поправить логику трекера сессии: не слать «получил», если данные уже есть в прошлой брони + тест"

    def setUp(self):
        # process_lesson/триггер-гейт судят по suggest.INTAKE_APPROVERS — ставим учителей фикстуры.
        self._save = (suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES)
        suggest.INTAKE_APPROVERS = set(TEACHERS)
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES = self._save

    def _task_text(self, who="danya"):
        # РЕАЛЬНЫЙ конвейер сборки задачи: reply-урок → process_lesson → build_lesson_task (не идеал-текст).
        lesson = mc.process_lesson(self.CARD, "не так: " + self.REMARK, who)
        self.assertEqual("lesson", lesson["decision"])
        return mc.build_lesson_task(lesson, self.CARD, who)

    def _classify(self, confidence="high", cls="ФАКТ", route=None):
        route = lr.FACT if route is None else route
        def classify(remark, draft="", window=""):
            return {"reading": self.READING, "class": cls, "confidence": confidence,
                    "plan": self.PLAN, "route": route}
        return classify

    # --- КЛАСС-ФИКС #164: keyword ТЕПЕРЬ берёт трекер-условие как ФАКТ (речь-акт не перевешивает) ---
    def test_keyword_now_catches_data_condition_as_fact(self):
        # До фикса: keyword не находил ни факт/модель/дату/тон → UNCLEAR. Класс-фикс #164: правило с
        # УСЛОВИЕМ на состоянии данных/трекера («…если клиент прислал…получил») → ФАКТ независимо от
        # «не говори». Думатель сверху всё равно даёт прочтение/план (трекер/свежесть) — но keyword
        # больше не роняет такую фразу в UNCLEAR/«переформулируй».
        route, _ = lr.classify_lesson_remark(self.REMARK)
        self.assertEqual(lr.FACT, route)

    def test_thinker_prompt_carries_verbatim_332_remark(self):
        # промпт думателя несёт урок #332 ДОСЛОВНО + черновик, который он правит (реальный claude не зовём)
        prompt = lr.build_lesson_classify_prompt(self.REMARK, self.DRAFT_TEXT, "Никита (606)")
        self.assertIn(self.REMARK, prompt)
        self.assertIn(self.DRAFT_TEXT, prompt)

    # --- ветка high: урок В РАБОТУ, ФАКТ, прочтение про трекер/свежесть, БЕЗ «переформулируй» ----
    def test_high_golden_fact_taken_no_reformulate(self):
        sent = {}
        dec = lr.handle_lesson_task(
            self._task_text(),
            classify=self._classify("high"),
            notify_owner=lambda c: self.fail("для high карточку-«переформулируй» владельцу НЕ шлём"),
            reply_moderation=lambda cid, t: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual(lr.FACT, dec["route"])                 # класс ФАКТ
        self.assertEqual("ФАКТ", dec["llm_class"])
        self.assertEqual("high", dec["confidence"])
        self.assertTrue(dec["delegate"])                        # урок В РАБОТУ (планировщику), не карточка
        # прочтение — про ЛОГИКУ ТРЕКЕРА / СВЕЖЕСТЬ СЕССИИ
        low = dec["reading"].lower()
        self.assertTrue("трекер" in low and ("свежест" in low or "сесси" in low), dec["reading"])
        # реплай учителю «Понял так… Делаю…» в модер-группу на карточку урока, БЕЗ «переформулируй»
        self.assertEqual("90332", sent["card"])
        self.assertIn("Понял так", sent["text"])
        self.assertIn("Делаю", sent["text"])
        self.assertNotIn("переформулируй", sent["text"].lower())
        self.assertNotIn("переформулируй", dec["result"].lower())
        # ФАКТ-делегат требует golden-тест ДОСЛОВНОЙ фразой клиента (правило-класс CLAUDE.md)
        self.assertIn("ДОСЛОВНОЙ фразой клиента", dec["delegate_text"])

    # --- ветка low: та же фраза размыта → спросили учителя «верно?», ждём, БЕЗ «переформулируй» --
    def test_low_asks_teacher_and_waits(self):
        sent = {}
        dec = lr.handle_lesson_task(
            self._task_text(),
            classify=self._classify("low"),
            notify_owner=lambda c: self.fail("low: владельца в момент вопроса НЕ зовём"),
            reply_moderation=lambda cid, t: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual("waiting", dec["status"])              # урок не закрыт — ждём подтверждения
        self.assertFalse(dec["delegate"])                       # в работу пока НЕ отдали
        self.assertIn("pending_low", dec)
        self.assertEqual("90332", sent["card"])
        self.assertIn("верно?", sent["text"])
        self.assertNotIn("Делаю", sent["text"])                # это НЕ high-реплай
        self.assertNotIn("переформулируй", sent["text"].lower())

    # --- ветка «да»: подтверждение аппрувера → урок В РАБОТУ как high ---------------------------
    def test_yes_from_approver_takes_as_high(self):
        dec0 = lr.handle_lesson_task(self._task_text(), classify=self._classify("low"),
                                     reply_moderation=lambda cid, t: True)
        sent = {}
        dec = lr.resume_low_lesson(dec0["pending_low"], "да", is_approver=True,
                                   reply_moderation=lambda cid, t: (sent.update(text=t), True)[1])
        self.assertEqual(lr.FACT, dec["route"])
        self.assertEqual("high", dec["confidence"])
        self.assertEqual("approved", dec["resumed"])
        self.assertTrue(dec["delegate"])
        self.assertIn("Делаю", sent["text"])                   # «Понял так… Делаю…» после согласия

    # --- ИНВАРИАНТ (в ветке «да»): подтверждает ТОЛЬКО INTAKE_APPROVERS -------------------------
    def test_yes_from_non_approver_is_ignored(self):
        dec0 = lr.handle_lesson_task(self._task_text(), classify=self._classify("low"),
                                     reply_moderation=lambda cid, t: True)
        dec = lr.resume_low_lesson(dec0["pending_low"], "да", is_approver=False,
                                   append_checklist=lambda r: self.fail("без аппрувера урок не применяем"),
                                   reply_moderation=lambda cid, t: self.fail("на чужое «да» не отвечаем"))
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("ignored_non_approver", dec["resumed"])

    # --- ветка поправка: текстовая правка → РОВНО одна пере-классификация → high ----------------
    def test_correction_reclassifies_once_to_high(self):
        dec0 = lr.handle_lesson_task(self._task_text(), classify=self._classify("low"),
                                     reply_moderation=lambda cid, t: True)
        calls = {"n": 0}
        correction = "нет, дело в трекере: не подтверждай данные, если они из прошлой брони"
        def classify2(remark, draft="", window=""):
            calls["n"] += 1
            self.assertIn("трекер", remark)                    # пере-классификация по ТЕКСТУ поправки
            return {"reading": "логика трекера сессии: не слать «получил» на старые данные",
                    "class": "ФАКТ", "confidence": "high", "plan": "поправить трекер + тест", "route": lr.FACT}
        sent = {}
        dec = lr.resume_low_lesson(dec0["pending_low"], correction, is_approver=True,
                                   classify=classify2,
                                   reply_moderation=lambda cid, t: (sent.update(text=t), True)[1])
        self.assertEqual(1, calls["n"])                        # РОВНО одна пере-классификация
        self.assertEqual("reclassified_high", dec["resumed"])
        self.assertEqual(lr.FACT, dec["route"])
        self.assertTrue(dec["delegate"])
        self.assertIn("трекер", sent["text"].lower())

    # --- ветка таймаут: учитель молчит >24ч → карточка владельцу в 1160, ожидание снято ---------
    def test_timeout_cards_owner_and_clears_wait(self):
        dec0 = lr.handle_lesson_task(self._task_text(), classify=self._classify("low"),
                                     reply_moderation=lambda cid, t: True)
        pending = dec0["pending_low"]
        sent = {}
        dec = lr.check_low_wait_timeout(
            pending, now=pending["created_at"] + 24 * 3600 + 1,
            notify_owner=lambda c: (sent.__setitem__("card", c), ("инбокс 1160", True))[1])
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertEqual("timeout_to_owner", dec["resumed"])
        self.assertTrue(dec["clear_wait"])                     # ожидание снято
        self.assertIn(self.REMARK, sent["card"])               # урок #332 НЕ потерян — в карточке владельцу

    # --- ИНВАРИАНТ: триггер урока принимаем ТОЛЬКО от INTAKE_APPROVERS --------------------------
    def test_trigger_only_from_intake_approvers(self):
        for who in TEACHERS:
            d = mc.process_lesson(self.CARD, "не так: " + self.REMARK, who)
            self.assertEqual("lesson", d["decision"], who)
        d = mc.process_lesson(self.CARD, "не так: " + self.REMARK, "stranger")
        self.assertEqual("denied", d["decision"])              # чужой не может учить
        self.assertNotIn("remark", d)                          # и урок/окно ему не отдаём

    # --- ИНВАРИАНТ: «урок принят» формируется ТОЛЬКО после доказанного коммита ------------------
    def test_ack_only_after_commit(self):
        subj, where = self.REMARK, lr._ROUTE_WHERE[lr.FACT]
        self.assertIsNone(lr.ack_after_commit("", subj, where))                       # нет ref → молчим
        self.assertIsNone(lr.ack_after_commit("abc", subj, where, verify=lambda r: False))  # не коммит
        ack = lr.ack_after_commit("abc1234", subj, where, verify=lambda r: True)
        self.assertIn("Урок принят", ack)
        self.assertIn("применится со следующего ответа", ack)

    # --- ИНВАРИАНТ: клиенту НИЧЕГО не шлётся — у дирижёра урока нет клиентского sink'а ----------
    def test_no_client_facing_sink_in_api(self):
        # замок API: единственные побочки — книга правил / чек-лист / владелец-1160 / реплай в МОДЕР-группу.
        # Ни одного клиентского канала: добавление DM-клиенту как параметра провалит этот замок.
        params = set(inspect.signature(lr.handle_lesson_task).parameters) - {"text"}
        self.assertEqual({"append_style", "append_checklist", "notify_owner", "classify",
                          "reply_moderation"}, params)

    def test_high_reply_goes_to_mod_group_not_client(self):
        # реплай уходит на КАРТОЧКУ урока в модер-группе (card_msg_id), а НЕ в client_id окна
        sent = {}
        lr.handle_lesson_task(self._task_text(), classify=self._classify("high"),
                              reply_moderation=lambda cid, t: (sent.update(card=cid), True)[1])
        self.assertEqual(str(self.CARD["card_msg_id"]), sent["card"])
        self.assertNotEqual(str(self.CARD["client_id"]), sent["card"])   # не в личку клиента

    # --- ИНВАРИАНТ: остальные циклы 292 стабильны; сам 292 keyword ТЕПЕРЬ берёт как ФАКТ (фикс #164) --
    def test_cycle_292_keyword_routes(self):
        with open(FIXTURE_292, encoding="utf-8") as f:
            fx = json.load(f)
        want = {"fact_price_nmax": lr.FACT, "supervision_fake_price": lr.SUPERVISION,
                "style_dry_tone": lr.STYLE, "unclear_redo": lr.UNCLEAR,
                # урок №292/152: КЛАСС-ФИКС #164 — условие на состоянии данных/трекера («если клиент
                # прислал только локацию — данных ещё нет») ловится keyword-осью как ФАКТ (речь-акт «не
                # пиши» больше не перевешивает). LLM-слой сверху даёт прочтение/план — но keyword уже не роняет в UNCLEAR.
                "fact_data_received_geo_only": lr.FACT}
        for c in fx["cycles"]:
            remark = mc.parse_lesson(c["reply_text"])["remark"]
            self.assertEqual(want[c["name"]], lr.classify_lesson_remark(remark)[0], c["name"])

    def test_default_path_keyword_catches_data_condition_as_fact(self):
        # Рубильник LLM по умолчанию OFF + без инъекции classify → чистый keyword-путь. КЛАСС-ФИКС #164:
        # трекер-условие #332 keyword берёт как ФАКТ → делегирует планировщику (правка+golden-тест), а
        # НЕ роняет в UNCLEAR/«переформулируй». LLM-поля (confidence/reading) при этом НЕ появляются.
        self.assertFalse(lr._lesson_llm_enabled())
        dec = lr.handle_lesson_task(self._task_text(), notify_owner=lambda c: ("инбокс 1160", True),
                                    reply_moderation=lambda cid, t: self.fail("keyword-путь реплай не шлёт"))
        self.assertEqual(lr.FACT, dec["route"])
        self.assertTrue(dec["delegate"])                       # → планировщику (код+тест), не карточка владельцу
        self.assertIn("ДОСЛОВНОЙ фразой клиента", dec["delegate_text"])
        self.assertNotIn("confidence", dec)                    # LLM-путь не активировался


class TestRouteByType(unittest.TestCase):
    """Родитель 112, шаг 3/8: маршрут урока по ВТОРОЙ оси type = behavior|code|unsure.
      • behavior → немедленная запись в playbook + ack Филиппу «действует со следующего черновика»;
                   конфликт с записанным правилом → показать оба, спросить кнопками какое оставить;
      • code     → в дев-очередь ПК (delegate + golden-тест);
      • unsure   → переспрос кнопками «правило / нужен код», ответ дороутит урок.
    Все побочки инъектируем фейками (без Telegram/1160/playbook/claude). Golden-правило CLAUDE.md:
    позитивы — ЖИВЫЕ формулировки + парафразы; спека-примеры дословно."""

    def _task(self, remark, card="90210"):
        return (f"[урок:правка от @filipp] родитель 112 — замечание менеджера в копилку обучения\n"
                f"окно диалога: Иван (555) (client_id=555) · черновик #7\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Аренда Nmax от 1200฿/сутки")

    def _type(self, kind, conf="high"):
        def classify_type(remark):
            return {"type": kind, "confidence": conf, "reason": f"фейк-{kind}",
                    "behavior_hits": 0, "code_hits": 0}
        return classify_type

    # --- текстовые билдеры ---------------------------------------------------------------------
    def test_behavior_ack_format_exact(self):
        # формат спеки 112/3 ДОСЛОВНО: «Понял так: <правило> — действует со следующего черновика»
        ack = lr.build_behavior_ack('не пиши "данные получил"')
        self.assertIn('Понял так: не пиши "данные получил"', ack)
        self.assertIn("действует со следующего черновика", ack)

    def test_conflict_card_shows_both_rules(self):
        card = lr.build_behavior_conflict_card("не здоровайся дважды", "здоровайся дважды")
        self.assertIn("НОВОЕ: не здоровайся дважды", card)
        self.assertIn("СТАРОЕ: здоровайся дважды", card)
        self.assertIn("какое оставить", card.lower())

    def test_type_unsure_ask_offers_both_buttons(self):
        ask = lr.build_type_unsure_ask("что-то с ценой")
        self.assertIn("правило", ask.lower())
        self.assertIn("код", ask.lower())

    # --- ветка behavior ------------------------------------------------------------------------
    def test_behavior_writes_playbook_and_acks_filipp(self):
        seen, sent = {}, {}
        dec = lr.route_lesson_by_type(
            self._task('не пиши "данные получил", это по-роботски'),
            classify_type=self._type(lr.BEHAVIOR),
            find_conflict=lambda r: "",                       # конфликта нет
            append_style=lambda r: (seen.__setitem__("rule", r), "added")[1],
            reply_moderation=lambda cid, t, b=None: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual(lr.BEHAVIOR, dec["type"])
        self.assertEqual("done", dec["status"])
        self.assertFalse(dec["delegate"])
        self.assertIn("данные получил", seen["rule"])          # правило ушло в playbook НЕМЕДЛЕННО
        self.assertEqual("90210", sent["card"])                # ack реплаем на карточку урока
        self.assertIn("действует со следующего черновика", sent["text"])

    def test_behavior_duplicate_is_done_not_failed(self):
        dec = lr.route_lesson_by_type(
            self._task("звучит сухо, пиши теплее"),
            classify_type=self._type(lr.BEHAVIOR), find_conflict=lambda r: "",
            append_style=lambda r: "duplicate", reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual("done", dec["status"])
        self.assertIn("уже есть", dec["result"])

    def test_behavior_sink_error_is_failed(self):
        dec = lr.route_lesson_by_type(
            self._task("пиши короче и живее"),
            classify_type=self._type(lr.BEHAVIOR), find_conflict=lambda r: "",
            append_style=lambda r: (_ for _ in ()).throw(OSError("disk")),
            reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual(lr.BEHAVIOR, dec["type"])
        self.assertEqual("failed", dec["status"])
        self.assertIn("короче", dec["result"])

    def test_behavior_conflict_shows_both_and_asks_which_to_keep(self):
        # новое правило противоречит записанному → НЕ затираем: показываем оба + кнопки, ждём выбора
        asked = {}
        dec = lr.route_lesson_by_type(
            self._task("не здоровайся дважды в одном диалоге"),
            classify_type=self._type(lr.BEHAVIOR),
            find_conflict=lambda r: "здоровайся дважды в одном диалоге",
            append_style=lambda r: self.fail("при конфликте вслепую НЕ пишем"),
            reply_moderation=lambda cid, t, b=None: (asked.update(text=t, buttons=b), True)[1])
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("behavior_conflict", dec["resolve"])
        self.assertIn("pending_conflict", dec)
        self.assertEqual([lr.BEHAVIOR_KEEP_NEW, lr.BEHAVIOR_KEEP_OLD], dec["buttons"])
        self.assertEqual([lr.BEHAVIOR_KEEP_NEW, lr.BEHAVIOR_KEEP_OLD], asked["buttons"])  # кнопки в реплае
        self.assertIn("НОВОЕ: не здоровайся дважды", asked["text"])                        # показали оба
        self.assertIn("СТАРОЕ: здоровайся дважды", asked["text"])
        p = dec["pending_conflict"]
        self.assertEqual("не здоровайся дважды в одном диалоге", p["new_rule"])
        self.assertEqual("здоровайся дважды в одном диалоге", p["old_rule"])

    def test_behavior_conflict_undelivered_goes_to_owner_not_silent(self):
        # переспрос-конфликт не дошёл → НЕ затираем и НЕ молчим: failed + карточка владельцу (фикс #102)
        owner = {}
        dec = lr.route_lesson_by_type(
            self._task("не здоровайся дважды"),
            classify_type=self._type(lr.BEHAVIOR),
            find_conflict=lambda r: "здоровайся дважды",
            reply_moderation=lambda cid, t, b=None: False,     # переспрос не доставлен
            notify_owner=lambda c: (owner.update(card=c), ("инбокс 1160", True))[1])
        self.assertEqual("failed", dec["status"])
        self.assertNotIn("pending_conflict", dec)
        self.assertTrue(dec["delivered"])
        self.assertIn("card", owner)                           # ушла владельцу
        self.assertIn("не затёр", dec["result"].lower())

    # --- ветка code ----------------------------------------------------------------------------
    def test_code_delegates_to_dev_queue_with_golden_test(self):
        dec = lr.route_lesson_by_type(
            self._task("цена берётся из столбца J, а не из K"),
            classify_type=self._type(lr.CODE),
            append_style=lambda r: self.fail("code в playbook НЕ пишем"),
            reply_moderation=lambda cid, t, b=None: self.fail("code в модер-группу ack НЕ шлём"))
        self.assertEqual(lr.CODE, dec["type"])
        self.assertTrue(dec["delegate"])                       # в дев-очередь ПК, как ФАКТ
        self.assertIn("столбца J", dec["delegate_text"])
        self.assertIn("юнит-тест", dec["delegate_text"])       # golden-тест обязателен
        self.assertIn("ДОСЛОВНОЙ фразой клиента", dec["delegate_text"])
        self.assertIn("Bridge", dec["delegate_text"])          # деньги/Bridge запрещены

    # --- ветка unsure --------------------------------------------------------------------------
    def test_unsure_asks_with_buttons_and_waits(self):
        asked = {}
        dec = lr.route_lesson_by_type(
            self._task("плохо, переделай"),
            classify_type=self._type(lr.UNSURE, conf="low"),
            reply_moderation=lambda cid, t, b=None: (asked.update(text=t, buttons=b), True)[1],
            notify_owner=lambda c: self.fail("в момент переспроса владельца НЕ зовём"))
        self.assertEqual(lr.UNSURE, dec["type"])
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("type_choice", dec["resolve"])
        self.assertIn("pending_type", dec)
        self.assertEqual([lr.TYPE_CHOICE_RULE, lr.TYPE_CHOICE_CODE], dec["buttons"])
        self.assertEqual([lr.TYPE_CHOICE_RULE, lr.TYPE_CHOICE_CODE], asked["buttons"])
        self.assertEqual("90210", dec["pending_type"]["card_msg_id"])

    def test_unsure_undelivered_goes_to_owner(self):
        dec = lr.route_lesson_by_type(
            self._task("что-то не то"),
            classify_type=self._type(lr.UNSURE, conf="low"),
            reply_moderation=lambda cid, t, b=None: False,
            notify_owner=lambda c: ("инбокс 1160", True))
        self.assertEqual("failed", dec["status"])
        self.assertTrue(dec["delivered"])
        self.assertIn("не висит молча", dec["result"])

    # --- resume: выбор кнопки unsure → дороутинг ------------------------------------------------
    def test_resume_choice_rule_routes_to_behavior(self):
        seen, sent = {}, {}
        pending = {"text": self._task('не пиши "данные получил"'), "remark": 'не пиши "данные получил"',
                   "card_msg_id": "90210", "who": "@filipp", "window": "555", "draft": ""}
        dec = lr.resume_type_unsure(pending, "правило", find_conflict=lambda r: "",
                                    append_style=lambda r: (seen.__setitem__("rule", r), "added")[1],
                                    reply_moderation=lambda cid, t, b=None: (sent.update(text=t), True)[1])
        self.assertEqual(lr.BEHAVIOR, dec["type"])
        self.assertEqual("chose_rule", dec["resumed"])
        self.assertEqual("done", dec["status"])
        self.assertIn("данные получил", seen["rule"])          # дороутили в playbook
        self.assertIn("действует со следующего черновика", sent["text"])

    def test_resume_choice_code_routes_to_dev_queue(self):
        pending = {"text": self._task("цена из столбца J"), "remark": "цена из столбца J",
                   "card_msg_id": "90210", "draft": ""}
        dec = lr.resume_type_unsure(pending, "нужен код")
        self.assertEqual(lr.CODE, dec["type"])
        self.assertEqual("chose_code", dec["resumed"])
        self.assertTrue(dec["delegate"])
        self.assertIn("столбца J", dec["delegate_text"])

    def test_resume_choice_unrecognized_keeps_waiting(self):
        pending = {"text": self._task("не то"), "remark": "не то", "card_msg_id": "90210"}
        dec = lr.resume_type_unsure(pending, "ну не знаю",
                                    reply_moderation=lambda cid, t, b=None: self.fail("не дороутим вслепую"))
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("choice_unrecognized", dec["resumed"])
        self.assertEqual([lr.TYPE_CHOICE_RULE, lr.TYPE_CHOICE_CODE], dec["buttons"])

    def test_resume_choice_synonyms_accepted(self):
        # живые синонимы кнопок: «поведение/behavior» → rule; «code/дев» → code
        pend_rule = {"text": self._task("тон сух"), "remark": "тон сух", "card_msg_id": "1"}
        d1 = lr.resume_type_unsure(pend_rule, "поведение", find_conflict=lambda r: "",
                                   append_style=lambda r: "added", reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual("chose_rule", d1["resumed"])
        d2 = lr.resume_type_unsure({"text": self._task("цифра"), "remark": "цифра", "card_msg_id": "1"},
                                   "в дев-очередь")
        self.assertEqual("chose_code", d2["resumed"])

    # --- resume: разрешение конфликта behavior -------------------------------------------------
    def test_resume_conflict_keep_new_replaces_and_acks(self):
        seen, sent = {}, {}
        pending = {"new_rule": "не здоровайся дважды", "old_rule": "здоровайся дважды",
                   "remark": "не здоровайся дважды", "card_msg_id": "90210"}
        dec = lr.resume_behavior_conflict(
            pending, "оставить новое",
            replace_style=lambda old, new: (seen.update(old=old, new=new), "replaced")[1],
            reply_moderation=lambda cid, t, b=None: (sent.update(text=t), True)[1])
        self.assertEqual("done", dec["status"])
        self.assertEqual("kept_new", dec["resolved"])
        self.assertEqual("здоровайся дважды", seen["old"])     # старое передано на удаление
        self.assertEqual("не здоровайся дважды", seen["new"])  # новое записано
        self.assertIn("действует со следующего черновика", sent["text"])

    def test_resume_conflict_keep_old_writes_nothing(self):
        sent = {}
        pending = {"new_rule": "не здоровайся дважды", "old_rule": "здоровайся дважды", "card_msg_id": "90210"}
        dec = lr.resume_behavior_conflict(
            pending, "оставить старое",
            replace_style=lambda old, new: self.fail("оставили старое → ничего не заменяем"),
            reply_moderation=lambda cid, t, b=None: (sent.update(text=t), True)[1])
        self.assertEqual("done", dec["status"])
        self.assertEqual("kept_old", dec["resolved"])
        self.assertIn("здоровайся дважды", sent["text"])       # ack про прежнее правило
        self.assertIn("оставили СТАРОЕ", dec["result"])

    def test_resume_conflict_replace_error_is_failed(self):
        dec = lr.resume_behavior_conflict(
            {"new_rule": "не X", "old_rule": "X", "card_msg_id": "1"}, "новое",
            replace_style=lambda old, new: "error", reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual("failed", dec["status"])
        self.assertEqual("kept_new", dec["resolved"])

    def test_resume_conflict_choice_unrecognized_waits(self):
        dec = lr.resume_behavior_conflict(
            {"new_rule": "не X", "old_rule": "X", "card_msg_id": "1"}, "хм",
            replace_style=lambda old, new: self.fail("не решаем вслепую"),
            reply_moderation=lambda cid, t, b=None: self.fail("не отвечаем на невнятицу"))
        self.assertEqual("waiting", dec["status"])
        self.assertEqual("choice_unrecognized", dec["resolved"])

    # --- РЕАЛЬНЫЙ классификатор второй оси (не только фейк) — анти-разрыв «тест ≠ реальность» ----
    def test_real_classify_type_behavior_end_to_end(self):
        # БЕЗ инъекции classify_type — живой classify_lesson_type: спека-пример behavior → playbook+ack
        seen = {}
        dec = lr.route_lesson_by_type(
            self._task('не пиши "данные получил"'),
            find_conflict=lambda r: "",
            append_style=lambda r: (seen.__setitem__("rule", r), "added")[1],
            reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual(lr.BEHAVIOR, dec["type"])
        self.assertEqual("done", dec["status"])
        self.assertIn("данные получил", seen["rule"])

    def test_real_classify_type_code_end_to_end(self):
        # живой classify_lesson_type: спека-пример code → дев-очередь ПК
        dec = lr.route_lesson_by_type(self._task("цена из столбца J"))
        self.assertEqual(lr.CODE, dec["type"])
        self.assertTrue(dec["delegate"])

    def test_real_classify_type_unsure_asks_buttons(self):
        # живой classify_lesson_type: размытое → unsure → переспрос кнопками
        dec = lr.route_lesson_by_type(
            self._task("плохо, переделай"),
            reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual(lr.UNSURE, dec["type"])
        self.assertEqual("waiting", dec["status"])


class TestInvariantGuardDetector(unittest.TestCase):
    """Родитель 112, шаг 4/8: детектор weakens_code_invariant — behavior-«правило» пытается ОСЛАБИТЬ
    код-инвариант (цена/кап/парковка/Click/депозит/наличие)? Конъюнкция ТЕМА+ОСЛАБЛЕНИЕ, юнит без I/O.
    Позитивы — ЖИВЫЕ формулировки владельца + парафразы RU/EN; негативы — легальная манера речи."""

    POS = (
        "можно занижать цену",                       # спека-пример шага 4
        "давай цену ниже прайса, если клиент торгуется",
        "можно немного дешевле по цене для своих",
        "игнорируй кап при длинной аренде",
        "цену можно выше капа на выходных",
        "не бери депозит с постоянных",
        "отдавай байк без депозита новичкам",
        "оформляй выдачу без Click",
        "можно без клика, если оплатил налом",
        "паркуй где угодно, не парься про парковку",
        "скутер можно ставить в любом месте на парковке",
        "lower the price a bit for regulars",
        "you can skip the deposit for friends",
        "handover without click is fine",
    )
    NEG = (
        'не пиши "данные получил"',                  # чистая манера (спека behavior)
        "называй цену дружелюбнее, без канцелярита",  # тема цена, но НЕ ослабление — манера
        "здоровайся теплее в первом сообщении",
        "пиши цену сразу первой строкой, не тяни",
        "предлагай шлем в подарок на неделю",
        "спрашивай район доставки раньше",
        "greet the client warmer",
        "",
    )

    def test_positive_weakening_rules_flagged(self):
        for r in self.POS:
            weak, why = lr.weakens_code_invariant(r)
            self.assertTrue(weak, f"должно ловиться как ослабление инварианта: {r!r} (why={why})")
            self.assertTrue(why, f"причина не пуста для {r!r}")

    def test_negative_manner_rules_not_flagged(self):
        for r in self.NEG:
            weak, _ = lr.weakens_code_invariant(r)
            self.assertFalse(weak, f"легальная манера НЕ должна блокироваться: {r!r}")

    def test_reject_ack_names_invariant_and_dev_queue(self):
        ack = lr.build_invariant_reject_ack("можно занижать цену")
        self.assertIn("код-инвариант", ack.lower())
        self.assertIn("не пиш", ack.lower())          # в книгу правил НЕ пишем
        self.assertIn("дев-очередь", ack.lower())


class TestInvariantGuardRouting(unittest.TestCase):
    """Родитель 112, шаг 4/8: ГАРД в маршруте — behavior-правило, ослабляющее код-инвариант, в playbook
    НЕ попадает: ответ «это код-инвариант» + роут в CODE-путь (дев-очередь). Единый чокпоинт записи, так
    что даже ЯВНЫЙ выбор учителя «правило» не протаскивает ослабление. Инварианты целы (playbook не тронут)."""

    def _task(self, remark, card="90210"):
        return (f"[урок:правка от @filipp] родитель 112 — замечание менеджера в копилку обучения\n"
                f"окно диалога: Иван (555) (client_id=555) · черновик #7\n"
                f"карточка модер-группы: msg={card}\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Аренда Nmax от 1200฿/сутки")

    def _type(self, kind, conf="high"):
        return lambda remark: {"type": kind, "confidence": conf, "reason": f"фейк-{kind}",
                               "behavior_hits": 0, "code_hits": 0}

    def test_price_lowering_behavior_rule_goes_to_code_not_playbook(self):
        # классификатор ошибочно счёл «можно занижать цену» поведением → ГАРД перехватывает: в playbook
        # НЕ пишем (append_style рухнул бы), уходит в дев-очередь как код-инвариант.
        sent = {}
        dec = lr.route_lesson_by_type(
            self._task("можно занижать цену, если клиент торгуется"),
            classify_type=self._type(lr.BEHAVIOR),
            append_style=lambda r: self.fail("правило-инвариант в playbook НЕ пишем"),
            find_conflict=lambda r: self.fail("до конфликт-пробы не доходим — гард раньше"),
            reply_moderation=lambda cid, t, b=None: (sent.update(card=cid, text=t), True)[1])
        self.assertEqual(lr.CODE, dec["type"])                 # ушло в код-путь
        self.assertTrue(dec["delegate"])                       # дев-очередь ПК
        self.assertIn("invariant_guard", dec)
        self.assertIn("юнит-тест", dec["delegate_text"])       # golden-тест обязателен
        self.assertIn("занижать цену", dec["delegate_text"])   # исходный урок в тексте задачи
        self.assertIn("код-инвариант", dec["result"].lower() + dec["reason"].lower())
        self.assertEqual("90210", sent["card"])                # ack реплаем на карточку урока
        self.assertIn("код-инвариант", sent["text"].lower())

    def test_explicit_teacher_choice_rule_cannot_weaken_invariant(self):
        # учитель ЯВНО жмёт «правило» на «игнорируй кап» → гард всё равно роутит в код (не в playbook)
        pending = {"text": self._task("игнорируй кап при длинной аренде"),
                   "remark": "игнорируй кап при длинной аренде", "card_msg_id": "90210", "draft": ""}
        dec = lr.resume_type_unsure(
            pending, "правило",
            append_style=lambda r: self.fail("даже по выбору «правило» инвариант в playbook НЕ пишем"),
            reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual(lr.CODE, dec["type"])
        self.assertTrue(dec["delegate"])
        self.assertEqual("chose_rule", dec["resumed"])         # пришли из ветки «правило»…
        self.assertIn("invariant_guard", dec)                  # …но гард развернул в код

    def test_legit_behavior_rule_still_reaches_playbook(self):
        # контроль: НЕ ослабляющее правило (тема цена, но манера) проходит гард и пишется в playbook
        seen = {}
        dec = lr.route_lesson_by_type(
            self._task("называй цену дружелюбнее, без канцелярита"),
            classify_type=self._type(lr.BEHAVIOR), find_conflict=lambda r: "",
            append_style=lambda r: (seen.__setitem__("rule", r), "added")[1],
            reply_moderation=lambda cid, t, b=None: True)
        self.assertEqual(lr.BEHAVIOR, dec["type"])
        self.assertEqual("done", dec["status"])
        self.assertIn("цену дружелюбнее", seen["rule"])         # легальная манера — в playbook

    def test_invariant_rule_never_lands_in_real_playbook_file(self):
        # ИНВАРИАНТЫ ЦЕЛЫ на живом sink: гоняем реальный suggest.append_playbook_rule на ВРЕМЕННЫЙ
        # playbook. Правило-ослабление не должно осесть в файле; контрольная манера — осядет.
        saved = suggest.PLAYBOOK_FILE
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "playbook.md")
            with open(pf, "w", encoding="utf-8") as f:
                f.write("# Playbook\n\n## Стиль общения\n- коротко\n\n## Выученные правила\n")
            suggest.PLAYBOOK_FILE = pf

            def _learned():
                with open(pf, encoding="utf-8") as f:
                    return " || ".join(suggest._playbook_learned_rules(f.read())).lower()

            try:
                lr.route_lesson_by_type(
                    self._task("можно занижать цену для своих"),
                    classify_type=self._type(lr.BEHAVIOR),
                    reply_moderation=lambda cid, t, b=None: True)      # append_style = боевой (temp-файл)
                self.assertNotIn("занижать цену", _learned())         # инвариант не ослаблен через playbook
                # контроль: легальная манера тем же боевым путём — осядет
                lr.route_lesson_by_type(
                    self._task("здоровайся теплее в первом сообщении"),
                    classify_type=self._type(lr.BEHAVIOR), find_conflict=lambda r: "",
                    reply_moderation=lambda cid, t, b=None: True)
                self.assertIn("теплее", _learned())
            finally:
                suggest.PLAYBOOK_FILE = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
