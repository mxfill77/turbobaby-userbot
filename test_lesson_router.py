# -*- coding: utf-8 -*-
"""
test_lesson_router.py — юниты обработчика задачи-урока дирижёра (родитель 292, шаг 3/7).
БЕЗ Telegram / Bridge / Anthropic и БЕЗ записи в боевые доки: все побочки инъектируем
фейками, файловые правки — во временные файлы. Golden-правило CLAUDE.md: в позитивах —
ЖИВЫЕ формулировки менеджера + парафразы RU/EN, в негативах — размытое «переделай».

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_router -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import os
import tempfile
import unittest

import lesson_router as lr


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
