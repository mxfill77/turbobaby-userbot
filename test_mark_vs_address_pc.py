# -*- coding: utf-8 -*-
"""Регресс «ИСХОД ЗАХОДА СУДИТ АДРЕС РЕЗУЛЬТАТА, А НЕ МАРКЕР ГАРДА» (06.09.2026)
+ инвариант MARK_VS_ADDR_PURE.

ЧТО ЗДЕСЬ ГЛАВНОЕ И БЕЗ ЧЕГО ПРАВКА НЕ ПРИНИМАЕТСЯ — КОНТРФАКТ. Один и тот же вход (тот же
номер ряда, тот же текст задачи, та же карточка гарда, тот же замок происхождения), отличающийся
РОВНО ОДНИМ — читается ли продукт по названному адресу, — обязан давать ДВА РАЗНЫХ исхода:

    продукт по адресу ЧИТАЕТСЯ → CLOSE: ряд закрыт продуктом, владельцу ИЗВЕЩЕНИЕ без кнопки;
    продукт по адресу НЕ ЧИТАЕТСЯ → PARK: ряд паркуется и спрашивает владельца, как и до правки.

Зелёный без второй половины ничего не доказывает: прибор, закрывающий ВСЁ, проходит любой
положительный тест и стоит дороже отсутствующего.

КОРПУС СТРОИТСЯ ЖИВЫМ КОДОМ — карточка живым `pretool_guard._card`, вердикт живым
`done_judge_pc.judge_parked` поверх живого V0. Рукописный мок здесь был бы хуже отсутствующего:
формат карточки ПК за неделю менялся трижды, а вердикт судьи — внешний источник для решения.

ИЗ ПРОВЕРКИ НЕ УХОДИТ НИЧЕГО. Путь «сообщение владельцу» (`_notify_topic` → `dispatch_notify`)
здесь не зовётся ни одной веткой: тесты трогают ЧИСТЫЙ модуль, который отправлять физически не
умеет, и читают исходник демона `ast`-разбором. Боевая база, денежные пути и боевые файлы
состояния не задеты: всё, что пишется, пишется во временный каталог `tempfile.mkdtemp`.

    venv\\Scripts\\python.exe -m unittest test_mark_vs_address_pc
"""

import ast
import os
import shutil
import tempfile
import unittest

import done_judge_pc as dj
import mark_vs_address_pc as MA
import pretool_guard as PG

REPO = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(REPO, "mark_vs_address_pc.py")
ORCH_SRC = os.path.join(REPO, "pc_orchestrator.py")

# Префикс замка происхождения ПК. Дублируется здесь НАМЕРЕННО и сверяется с живым замком
# отдельным тестом (`TestOriginLock.test_prefix_matches_live_detector`).
GUARD_PREFIX = "NEEDS_APPROVAL (гард): "

FOLDER = "docs/artifacts"
WORDS = "маркер гарда исход захода 06.09"
TASK = ("ЦЕЛЬ: проверить, кто судит исход захода. ЗАПРЕТЫ: нет.\n"
        "АДРЕС РЕЗУЛЬТАТА: файл в " + FOLDER + " за 06.09 со словами " + WORDS)
NAME = "2026-09-06-marker-vs-address.md"
REL = FOLDER + "/" + NAME
# Заголовок с БОЛЬШОЙ буквы — живая форма артефактов полосы; слова адреса лежат в нём.
BODY = "# Маркер гарда исход захода 06.09 — разбор\n\nтело продукта\n"
# ЧУЖОЙ файл: стои́т по адресу и за ту же дату, но слов адреса в нём нет НИ в имени, НИ в теле.
OTHER_NAME = "2026-09-06-sovsem-drugaya-tema.md"
OTHER_BODY = "# Совсем другая тема\n\nтут про цену аренды, и ни слова про предмет задания\n"

# Карточка, на которой встал живой ряд #72 (класс sqlite, объект «таблица collections»).
KIND, OBJ, CMD = "sqlite", "таблица collections", "sqlite3 moderation_ipc.db \"insert into x\""


def live_card(kind=KIND, obj=OBJ, cmd=CMD, prefix=GUARD_PREFIX, stamp=True):
    """Карточка в ТОЧНО ТОМ виде, в каком её видит демон: тело от живого `pretool_guard._card`,
    штамп класса от `_write_marker` (последней строкой) и префикс замка происхождения."""
    body = PG._card(kind, obj, cmd)
    if stamp:
        body += "\n" + PG.KIND_LINE_PREFIX + kind
    return prefix + body


def _write(root, rel, text):
    full = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


class Case(unittest.TestCase):
    """Каждый заход живёт в СВОЁМ временном дереве. Боевого не касаемся ничем."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mark_vs_addr_")
        os.makedirs(os.path.join(self.root, FOLDER.replace("/", os.sep)))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.card = live_card()

    def run_lane(self, product=None, tid=88):
        """ОДИН И ТОТ ЖЕ ВХОД, разница ровно в `product`.

        Порядок дословно повторяет живой (`pc_orchestrator._process_one`): опорный снимок руками
        демона ДО захода → заход (здесь его изображает запись продукта, если он есть) → суд по
        адресу → решение. `product` — список пар (путь, текст) либо None, если заход не оставил
        по адресу ничего."""
        base = dj.baseline(TASK, root=self.root)
        for rel, text in (product or ()):
            _write(self.root, rel, text)
        v = dj.judge_parked(tid, TASK, base, run_id="pc-test-%s" % tid, root=self.root)
        action, rule, proof = MA.decide(
            self.card, "guard",
            proven=bool(v) and v.get("verdict") == dj.DONE,
            addressed=bool(v) and bool(v.get("address")),
            reason=(v or {}).get("reason", ""), chosen=(v or {}).get("chosen", ""),
            obj=PG.object_from_card(self.card),
            kind=", ".join(sorted(PG.kinds_from_card(self.card))))
        return v, action, rule, proof


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ПУНКТ 3 ЗАДАНИЯ — КОНТРФАКТ. Оба исхода на одном входе
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestCounterfact(Case):

    def test_green_address_closes_the_row_by_the_product(self):
        """Продукт по названному адресу ЧИТАЕТСЯ → маркер гарда ряд не паркует."""
        v, action, rule, proof = self.run_lane(product=[(REL, BODY)])
        self.assertEqual(v["verdict"], dj.DONE, v["reason"])
        self.assertEqual(action, MA.CLOSE)
        self.assertEqual(rule, MA.R_PROVEN)
        self.assertIn(REL, proof, "доказательство обязано НАЗЫВАТЬ прочитанный файл")

    def test_red_address_still_parks_the_row_and_asks_the_owner(self):
        """ТОТ ЖЕ вход, продукта по адресу НЕТ → ряд паркуется, как и до правки."""
        v, action, rule, proof = self.run_lane(product=None)
        self.assertEqual(v["verdict"], dj.UNKNOWN)
        self.assertEqual(action, MA.PARK)
        self.assertEqual(rule, MA.R_RED_ADDRESS)
        self.assertIn("адрес", proof.lower() + rule)

    def test_the_two_outcomes_differ_only_by_readability(self):
        """ЗАМОК КОНТРФАКТА: вход у обеих половин совпадает ДОСЛОВНО — тот же номер, тот же
        текст задачи, та же карточка, тот же замок происхождения. Различие ровно одно."""
        green_v, green, _, _ = self.run_lane(product=[(REL, BODY)])
        # Второе дерево — чистое, всё остальное то же самое.
        shutil.rmtree(self.root, True)
        self.root = tempfile.mkdtemp(prefix="mark_vs_addr_")
        os.makedirs(os.path.join(self.root, FOLDER.replace("/", os.sep)))
        self.addCleanup(shutil.rmtree, self.root, True)
        red_v, red, _, _ = self.run_lane(product=None)
        self.assertEqual((green, red), (MA.CLOSE, MA.PARK))
        self.assertNotEqual(green_v["verdict"], red_v["verdict"])

    def test_the_previous_run_left_at_the_address_is_not_this_run_product(self):
        """Копия прежнего прогона по адресу зелёной не делает: продукт мерится РАЗНИЦЕЙ двух
        снимков, а не наличием файла (правило судьи, здесь — его следствие)."""
        _write(self.root, REL, BODY)                 # лежало ДО захода
        _, action, rule, _ = self.run_lane(product=None)
        self.assertEqual(action, MA.PARK)
        self.assertEqual(rule, MA.R_RED_ADDRESS)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ПУНКТ 4 ЗАДАНИЯ — ОТРИЦАТЕЛЬНЫЙ СВЕРХ КОНТРФАКТА
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestFileWithoutTheAddressWords(Case):

    def test_a_file_at_the_address_without_its_words_is_not_green(self):
        """Адрес назван, файл по нему за ту же дату ПОЯВИЛСЯ, слов адреса в нём нет — ряд
        ПАРКУЕТСЯ. Иначе «адрес» превратился бы в «папку», и продуктом задачи считался бы
        любой файл, положенный туда в то же окно (в параллельном витке — продукт второй руки)."""
        v, action, rule, proof = self.run_lane(product=[(FOLDER + "/" + OTHER_NAME, OTHER_BODY)])
        self.assertEqual(v["verdict"], dj.UNKNOWN)
        self.assertEqual(action, MA.PARK)
        self.assertEqual(rule, MA.R_RED_ADDRESS)
        self.assertIn("не отвечает словам", proof)

    def test_the_words_alone_without_a_file_are_not_green_either(self):
        """Симметричная половина: по адресу пусто вовсе."""
        _, action, rule, _ = self.run_lane(product=None)
        self.assertEqual((action, rule), (MA.PARK, MA.R_RED_ADDRESS))

    def test_a_foreign_file_does_not_become_the_product_by_standing_next_to_the_right_one(self):
        """Чужой файл рядом с настоящим продуктом исхода не меняет: судья выбирает по СЛОВАМ."""
        _, action, rule, proof = self.run_lane(
            product=[(FOLDER + "/" + OTHER_NAME, OTHER_BODY), (REL, BODY)])
        self.assertEqual((action, rule), (MA.CLOSE, MA.R_PROVEN))
        self.assertIn(REL, proof)
        self.assertNotIn(OTHER_NAME, proof)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ТРИ УДЕРЖАНИЯ: закрытие требует ПОЛОЖИТЕЛЬНОГО доказательства, всё прочее — к владельцу
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestHolds(unittest.TestCase):

    def test_a_card_not_born_of_the_guard_marker_is_never_closed(self):
        """Замок происхождения стои́т ПЕРВЫМ: не маркер гарда — не наш класс вовсе."""
        for origin in ("legacy", "", "self", None):
            action, rule, _ = MA.decide(live_card(prefix=""), origin, proven=True,
                                        addressed=True, chosen=REL)
            self.assertEqual((action, rule), (MA.PARK, MA.R_NOT_GUARD), repr(origin))

    def test_a_task_without_an_address_is_parked_not_closed(self):
        """Адреса нет — предмета суда нет; ряд идёт владельцу ровно как до правки."""
        action, rule, proof = MA.decide(live_card(), "guard", proven=False, addressed=False)
        self.assertEqual((action, rule), (MA.PARK, MA.R_NO_ADDRESS))
        self.assertIn("не назван", proof)

    def test_only_a_proven_product_closes_the_row(self):
        """ЕДИНСТВЕННАЯ дверь к CLOSE: гардова карточка + названный адрес + доказанный продукт."""
        self.assertEqual(MA.decide(live_card(), "guard", proven=True, addressed=True,
                                   chosen=REL)[0], MA.CLOSE)
        for proven, addressed in ((False, True), (True, False), (False, False)):
            self.assertEqual(MA.decide(live_card(), "guard", proven=proven,
                                       addressed=addressed)[0], MA.PARK,
                             "proven=%s addressed=%s" % (proven, addressed))

    def test_garbage_facts_are_parked_not_closed(self):
        """FAIL-SAFE: мусор вместо фактов — это вопрос владельцу, а не тихое закрытие."""
        for f in (None, {}, {"origin": "guard"}, [], "guard"):
            self.assertEqual(MA.verdict(f)[0], MA.PARK, repr(f))


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ПУНКТ 2 ЗАДАНИЯ — ЗАЩИТА ОСТАЁТСЯ СЛЫШНОЙ
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestNoticeIsLoud(unittest.TestCase):

    def test_the_notice_names_the_object_and_the_class(self):
        line = MA.notice("ПК", 88, OBJ, KIND, REL)
        self.assertIn(OBJ, line)
        self.assertIn(KIND, line)
        self.assertIn("ОТКЛОНЕНА", line)
        self.assertIn("НЕ выполнена", line)

    def test_an_empty_field_says_so_instead_of_disappearing(self):
        """Молча гасить отказ запрещено: пустой класс и пустой объект становятся «не назван»,
        а не пропадают. Тишина здесь неотличима от «отказа не было»."""
        line = MA.notice("ПК", 88, "", "", "")
        self.assertEqual(line.count(MA.UNNAMED), 3, line)
        self.assertIn("отказ гарда", line)

    def test_a_dash_is_an_empty_field_too(self):
        """Прочерк — честная пометка гарда «поля нет», а не имя объекта."""
        self.assertIn(MA.UNNAMED, MA.notice("ПК", 88, "—", "-", REL))

    def test_the_notice_asks_nothing(self):
        """ИЗВЕЩЕНИЕ, А НЕ ВОПРОС: кнопок нет, слова «да» нет, отвечать не на что."""
        line = MA.notice("ПК", 88, OBJ, KIND, REL)
        self.assertIn("ответа не требует", line)
        self.assertNotIn("«да»", line)
        self.assertEqual(len(line.splitlines()), 1, "извещение — ОДНА строка")


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ТЕКСТ ЗАКРЫТИЯ: отказ в силе, карточка дословно, самопочинку не зовём
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestCloseResult(unittest.TestCase):

    def out(self, draft=""):
        return MA.close_result(live_card(), MA.R_PROVEN, "продукт прочитан: " + REL,
                               obj=OBJ, kind=KIND, draft=draft)

    def test_the_card_survives_verbatim(self):
        """Молча из истории ничего не исчезает: текст карточки сохраняется ДОСЛОВНО."""
        self.assertIn(live_card(), self.out())

    def test_the_refusal_is_restated_as_still_in_force(self):
        out = self.out()
        self.assertIn("ОТКЛОНЕНА и НЕ выполнена", out)
        self.assertIn(OBJ, out)
        self.assertIn(KIND, out)
        self.assertNotIn("approved", out.lower())

    def test_the_first_char_is_the_success_mark_not_a_failure_mark(self):
        """МАШИННЫЙ КОНТРАКТ ПК: первый символ поля `result` читают ОБА гейта самопочинки
        (`NO_HEAL_PREFIXES`). Знак успеха обязан отличаться от знаков причины провала."""
        import pc_orchestrator as PO
        out = self.out()
        self.assertTrue(out.startswith("✅"), out[:40])
        for mark in PO.NO_HEAL_PREFIXES:
            self.assertFalse(out.startswith(mark), "знак провала «%s» в голове успеха" % mark)

    def test_the_draft_block_lands_above_the_card_and_below_the_verdict(self):
        """stdout исполнителя на этой дороге ВЫБРОШЕН — без блока черновика владелец не
        прочитал бы ни слова самого захода."""
        out = self.out(draft="БЛОК-ЧЕРНОВИКА")
        self.assertIn("БЛОК-ЧЕРНОВИКА", out)
        self.assertLess(out.index("Чем доказано"), out.index("БЛОК-ЧЕРНОВИКА"))
        self.assertLess(out.index("БЛОК-ЧЕРНОВИКА"), out.index("ДОСЛОВНО карточка"))

    def test_an_empty_card_is_named_not_swallowed(self):
        self.assertIn("(текст карточки пуст)", MA.close_result("", MA.R_PROVEN, "п", kind=KIND))


# ══════════════════════════════════════════════════════════════════════════════════════════
#  СМЫЧКА С ЖИВЫМ ЗАМКОМ ПРОИСХОЖДЕНИЯ И С ДЕМОНОМ
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestOriginLock(unittest.TestCase):

    def test_prefix_matches_live_detector(self):
        """Константа этого модуля и вердикт ЖИВОГО замка демона обязаны совпадать — иначе две
        копии разъехались бы молча и ветка стала бы недостижимой."""
        import pc_orchestrator as PO
        self.assertEqual(PO.DUTY_GUARD_PREFIX, GUARD_PREFIX)
        self.assertEqual(PO._duty_origin(live_card()), MA.ORIGIN_GUARD)
        self.assertNotEqual(PO._duty_origin(live_card(prefix="")), MA.ORIGIN_GUARD)


class TestWiring(unittest.TestCase):
    """Врезка проверяется ast-разбором ЖИВОГО демона: подстрока в комментарии кодом не является,
    а из проверки не уходит ни одного сообщения владельцу."""

    # ЗАХОД ЖИВЁТ В `_process_one`, А НЕ В `process_new`. Ловушка названа прямо: с 04.09.2026
    # `process_new` — ДИСПЕТЧЕР (читает очередь, считает приоритет, разводит одну руку и две), а
    # тело захода — `run_task`, парковка, закрытие — лежит в `_process_one`. Проверка, искавшая
    # врезку в `process_new`, честно нашла бы ноль и объявила бы живую правку мёртвой.
    LANE_FN = "_process_one"

    def setUp(self):
        with open(ORCH_SRC, encoding="utf-8") as f:
            self.tree = ast.parse(f.read())
        self.fn = next((n for n in ast.walk(self.tree)
                        if isinstance(n, ast.FunctionDef) and n.name == self.LANE_FN), None)
        self.assertIsNotNone(self.fn, "%s в демоне не найден" % self.LANE_FN)

    def _call_lines(self, name):
        return [n.lineno for n in ast.walk(self.fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name]

    def test_the_branch_is_wired_into_the_lane(self):
        self.assertTrue(self._call_lines("_maybe_mark_vs_address"),
                        "ветка не врезана — правка мертва")

    def test_the_branch_stands_before_the_card_duty(self):
        """Порядок не косметический: на пересечении верны оба закрытия, но `failed` дежурного
        поверх прочитанного продукта — то же переписывание доказанного."""
        self.assertLess(min(self._call_lines("_maybe_mark_vs_address")),
                        min(self._call_lines("_maybe_card_duty")))

    def test_the_baseline_is_taken_before_the_run(self):
        """Опорный снимок обязан сниматься ДО `run_task`: снятый после, он приходил бы со слов
        проверяемого."""
        base = [n.lineno for n in ast.walk(self.fn) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "baseline"]
        self.assertLess(min(base), min(self._call_lines("run_task")))

    def test_the_daemon_asks_the_parked_judge_not_the_done_one(self):
        """Ряд, припаркованный маркером, судится `judge_parked`: подать прибору заявку
        «исполнитель сказал done» там, где исполнитель не сказал ничего, — подлог даром."""
        hand = next((n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)
                     and n.name == "_maybe_mark_vs_address"), None)
        self.assertIsNotNone(hand)
        attrs = {n.func.attr for n in ast.walk(hand)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertIn("judge_parked", attrs)
        self.assertNotIn("judge", attrs)

    def test_the_notice_goes_out_before_the_row_is_closed(self):
        """След РАНЬШЕ закрытия: упадёт мост на закрытии — владелец уже знает об отказе."""
        hand = next(n for n in ast.walk(self.tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_maybe_mark_vs_address")
        notice = [n.lineno for n in ast.walk(hand) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name) and n.func.id == "_mark_addr_notice"]
        close = [n.lineno for n in ast.walk(hand) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "_complete"]
        self.assertTrue(notice and close)
        self.assertLess(min(notice), min(close))

    def test_the_switch_defaults_to_on_and_the_stop_file_beats_the_environment(self):
        """Дефолт ВКЛЮЧЕНО (выключенная по умолчанию починка не чинит ничего), стоп-файл БЬЁТ
        значение окружения."""
        import pc_orchestrator as PO
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(PO.MARK_ADDR_ENV, None)
            with mock.patch.object(PO, "_flag_forced_off", return_value=False):
                self.assertTrue(PO._mark_vs_addr_on())
                os.environ[PO.MARK_ADDR_ENV] = "0"
                self.assertFalse(PO._mark_vs_addr_on())
                os.environ[PO.MARK_ADDR_ENV] = "1"
                self.assertTrue(PO._mark_vs_addr_on())
            with mock.patch.object(PO, "_flag_forced_off", return_value=True):
                self.assertFalse(PO._mark_vs_addr_on())


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ИНВАРИАНТ MARK_VS_ADDR_PURE — граница держится отсутствием инструментов, а не докстрингом
# ══════════════════════════════════════════════════════════════════════════════════════════
_FORBIDDEN_CALLS = frozenset((
    "open", "exec", "eval", "compile", "__import__", "input", "globals", "vars", "setattr",
))
_FORBIDDEN_ATTR_ROOTS = frozenset((
    "os", "sys", "subprocess", "shutil", "socket", "requests", "urllib", "pathlib", "tempfile",
    "sqlite3", "bridge_http", "pretool_guard", "done_judge_pc", "bc", "builtins", "logging",
))


def ast_findings(src):
    """→ список (адрес, чем плохо). Пустой список = решение чисто. Разбор ast, не подстрока:
    имя в комментарии, строке и докстринге кодом не является."""
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append(("строка %d" % node.lineno,
                        "импорт в модуле решения: у него не должно быть рук вовсе"))
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _FORBIDDEN_CALLS:
                out.append(("строка %d" % node.lineno,
                            "вызов «%s» — запись/исполнение в чистом решении" % fn.id))
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                    and fn.value.id in _FORBIDDEN_ATTR_ROOTS:
                out.append(("строка %d" % node.lineno,
                            "обращение к «%s.%s» — модуль решения не смеет трогать мир"
                            % (fn.value.id, fn.attr)))
    return out


class TestMarkVsAddrPure(unittest.TestCase):
    """MARK_VS_ADDR_PURE. FAIL-CLOSED: файла нет / не парсится → провал, а не тишина."""

    def test_live_module_is_clean(self):
        with open(SRC, encoding="utf-8") as f:
            self.assertEqual(ast_findings(f.read()), [],
                             "живой mark_vs_address_pc.py обзавёлся руками")

    def test_no_imports_at_all(self):
        with open(SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        self.assertEqual([n for n in ast.walk(tree)
                          if isinstance(n, (ast.Import, ast.ImportFrom))], [])

    def test_goldens_position_beats_word(self):
        cases = [
            (".get законен — это метод, а не руки", 0,
             "def f(d):\n    return d.get('a')\n"),
            ("любой импорт → флаг", 1, "import re\n"),
            ("from-импорт → флаг", 1, "from os import remove\n"),
            ("голый open() → флаг", 1, "f = open('x')\n"),
            ("os.remove → флаг (импорт + вызов)", 2, "import os\nos.remove('x')\n"),
            ("слово в СТРОКЕ и комментарии — не флаг", 0,
             "# os.remove тут только словом\nS = 'open(x)'\n"),
        ]
        for title, expect, src in cases:
            self.assertEqual(len(ast_findings(src)), expect, title)

    def test_unparsable_is_a_failure(self):
        with self.assertRaises(SyntaxError):
            ast_findings("def broken(:\n")

    def test_the_english_word_of_approval_is_absent_from_the_module(self):
        """Одобрять этот модуль не вправе ни при каких условиях — вердиктов ровно два. Слова нет
        в файле ВООБЩЕ (ни кодом, ни текстом): его нельзя внести даже случайной копипастой."""
        with open(SRC, encoding="utf-8") as f:
            self.assertNotIn("approved", f.read().lower())

    def test_two_verdicts_and_yes_is_not_among_them(self):
        self.assertEqual({MA.PARK, MA.CLOSE}, {"park", "close"})
        self.assertNotEqual(MA.PARK, MA.CLOSE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
