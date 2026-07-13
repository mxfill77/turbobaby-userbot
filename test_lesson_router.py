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


if __name__ == "__main__":
    unittest.main(verbosity=2)
