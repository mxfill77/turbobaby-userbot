# -*- coding: utf-8 -*-
"""Регресс ДЕЖУРНОГО ПО КАРТОЧКАМ (ПК-полоса, зеркало 20ebbc1) + инвариант CARD_DUTY_PURE.

КОРПУС СТРОИТСЯ ЖИВЫМ КОДОМ ГАРДА (`pretool_guard._card`), а не рукописным моком: формат
карточки ПК менялся за неделю трижды (штамп класса 31.07, поле «Объект» 01.08, гейт объекта
02.08), и мок здесь был бы хуже отсутствующего — он бы застыл на позавчерашней форме и красил
зелёным то, чего в бою нет. Это то же правило-класс, что «мок внешнего ответа обязан копировать
живой формат»: карточка для дежурного — внешний источник.

ИНВАРИАНТ CARD_DUTY_PURE ЖИВЁТ ЗДЕСЬ, А НЕ В ОТДЕЛЬНОМ РЕЕСТРЕ. На полосе сервера он лежит в
`invariants_check.py`; на ПК такого файла нет вовсе, и заводить целый каркас ради одной проверки
значило бы порт чужой конструкции вместо зеркала правила. Проверка та же (ast-разбор, не греп) и
с тем же fail-closed; место исполнения — полный гейт, который гоняет все `test_*.py`.
"""
import ast
import os
import sys
import unittest

import card_duty as CD
import pretool_guard as PG

REPO = os.path.dirname(os.path.abspath(__file__))
DUTY_SRC = os.path.join(REPO, "card_duty.py")

# Префикс, которым замок происхождения ПК метит карточку из файла-маркера гарда. Дублируется
# здесь НАМЕРЕННО и сверяется с живым детектором отдельным тестом — см.
# `TestOriginLock.test_prefix_matches_live_detector`.
GUARD_PREFIX = "NEEDS_APPROVAL (гард): "


def live_card(kind, obj="", cmd="", prefix=GUARD_PREFIX, stamp=True):
    """Карточка в ТОЧНО ТОМ виде, в каком её видит демон: тело от живого `pretool_guard._card`,
    штамп класса от `_write_marker` (последней строкой) и префикс замка происхождения."""
    body = PG._card(kind, obj, cmd)
    if stamp:
        body += "\n" + PG.KIND_LINE_PREFIX + kind
    return prefix + body


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ГЛАВНЫЙ ТЕСТ: ВЕСЬ СЛОВАРЬ КРАСНЫХ ОПЕРАЦИЙ ПК-ГАРДА С ЖИВОЙ ЦЕЛЬЮ
# ══════════════════════════════════════════════════════════════════════════════════════════
# Живая цель на КАЖДЫЙ вид закрытого словаря `pretool_guard._KIND_VOCAB`. Пара — то, с чем гард
# реально зовёт `_card`: (obj, raw_cmd). ОБЪЕКТ БЕРЁТСЯ ЖИВЫМ ИЗВЛЕКАТЕЛЕМ ГАРДА там, где его
# извлекает сам гард (`_extract_kill_target`, `_extract_host`, `_RE_OUTSIDE_WRITE`) — подсунуть
# сюда красивую строку руками значило бы проверять свою фантазию вместо живого разбора; пустой
# obj оставлен там, где цель достаёт уже `_card_fields`.
_KILL_CMD = "taskkill /PID 4872 /F"
_NET_CMD = "ssh root@5.223.94.179 ls"
_OUT_CMD = "echo x > " + "C:/Program" + "Data/x.txt"   # склейка: голый литерал краснит гард в проходящей команде
_PY_CMD = "venv/Scripts/python.exe -c \"import os; os." + "remove('tmp/x.txt')\""
_LIVE_TARGET = {
    "delete":       ("", "rm suggest.py"),
    "kill":         (PG._extract_kill_target(_KILL_CMD), _KILL_CMD),
    "schtasks":     ("", "schtasks /change /tn TurboBabyRC /disable"),
    "git_force":    ("", "git reset --hard origin/main"),
    "sqlite":       ("", "sqlite3 moderation_ipc.db \"update meta set v=1\""),
    "clasp":        ("проект Bridge", "clasp push"),
    "clasp_push":   ("проект Bridge", "clasp push --force"),
    "clasp_deploy": ("проект Bridge", "clasp deploy -i AKfycbx0123456789abc"),
    "clasp_run":    ("проект Bridge", "clasp run activate_booking"),
    "live_sheet":   ("", "venv/Scripts/python.exe -c \"import gspread; gspread.open('Лист1')\""),
    "network":      (PG._extract_host(_NET_CMD) or "", _NET_CMD),
    "env":          ("", "cat .env"),
    "outside":      (PG._RE_OUTSIDE_WRITE.search(_OUT_CMD).group(2), _OUT_CMD),
    "write_outside": (PG._RE_OUTSIDE_WRITE.search(_OUT_CMD).group(2), _OUT_CMD),
    "py_write":     ("os." + "remove", _PY_CMD),
    "edit_secret":  (r"D:\turbobaby-bot\.env", ""),
    "read_secret":  (r"D:\turbobaby-bot\.env", ""),
    "edit_claude":  (r"D:\turbobaby-bot\.claude\settings.json", ""),
    "edit_git":     (r"D:\turbobaby-bot\.git\hooks\pre-commit", ""),
    # ДВА ИМЕНИ НЕЗНАНИЯ ГАРДА. Цели у них нет ЗАКОНОМЕРНО — и держит их вторая половина
    # `_highest_kind`, а не наличие объекта (см. `test_unparsed_kinds_held_without_object`).
    "unknown":      ("", "нераспознанная команда"),
    PG.KIND_UNKNOWN_TOOL: ("", ""),
}


class TestWholeRedVocabulary(unittest.TestCase):
    """ГЛАВНЫЙ ТЕСТ. Ни один вид красной операции ПК-гарда, у которого НАЗВАНА ЦЕЛЬ, дежурный
    закрыть не может. Словарь берётся ИЗ ГАРДА — появится новый красный вид, он попадёт под
    проверку сам (и потребует живой цели, иначе тест падает)."""

    def test_vocabulary_is_taken_from_the_guard(self):
        """Перечень целей обязан покрывать ВЕСЬ живой словарь — ни больше, ни меньше.

        Это замок против тихого старения: новый вид в гарде без живой цели здесь = красный гейт,
        а не молчаливо непроверенный класс. Ровно тот дефект, из-за которого на ПК заводят
        `_ACTION_CHECK` и реестр сирот."""
        self.assertEqual(set(_LIVE_TARGET), set(PG._KIND_VOCAB),
                         "словарь красных видов гарда и перечень живых целей разошлись")

    def test_named_target_is_never_closed(self):
        """ГЛАВНОЕ УТВЕРЖДЕНИЕ: ни один вид словаря дежурный не закрывает. Счёт печатается."""
        held, closed, no_object = [], [], []
        for kind in PG._KIND_VOCAB:
            obj, cmd = _LIVE_TARGET[kind]
            card = live_card(kind, obj, cmd)
            f = CD.facts(card, "guard")
            action, rule, proof = CD.verdict(f)
            if not f["obj"]:
                no_object.append(kind)
            (closed.append((kind, rule)) if action == CD.CLOSE else held.append(kind))
            self.assertEqual(action, CD.HOLD,
                             "вид %s закрыт дежурным (условие «%s»): %s" % (kind, rule, proof))
        sys.stderr.write(
            "\n[ГЛАВНЫЙ ТЕСТ] словарь красных операций ПК-гарда: %d видов · УДЕРЖАНО %d · "
            "ЗАКРЫТО %d · без объекта (держит вторая половина высшего вида) %d: %s\n"
            % (len(PG._KIND_VOCAB), len(held), len(closed), len(no_object),
               ", ".join(no_object) or "—"))
        self.assertEqual(closed, [], "закрытых видов быть не может ни одного")

    def test_no_new_unknown_names(self):
        """ЕДИНСТВЕННОЕ, ЧЕМ ИМЕНА НЕЗНАНИЯ МОГУТ ПРОТУХНУТЬ, — третье такое имя в гарде.
        Ловим по живому словарю. (Сегодня оба исхода — HOLD, но текст доказательства владельцу
        разный, и он обязан называть правильную причину.)"""
        live = {k for k in PG._KIND_VOCAB if k.startswith("unknown")}
        self.assertEqual(live, set(CD._UNPARSED_KINDS),
                         "в гарде завелось имя незнания, которого дежурный не знает")

    def test_every_stamped_kind_held_with_empty_object(self):
        """ГЛАВНАЯ ПОПРАВКА ЗЕРКАЛА, пиннится отдельно от главного теста.

        Слепая копия условия 1 закрыла бы КАЖДУЮ штампованную карточку с пустым объектом —
        то есть воскресила бы слоем позже класс, который гард ПК закрыл заме́ром 02.08 (класс
        Д-3): с 31.07 пустой объект означает «действие разобрано, цель не извлеклась», а не
        «совпало слово». Проверяем ВЕСЬ живой словарь, без объекта у каждого вида."""
        for kind in PG._KIND_VOCAB:
            f = CD.facts(live_card(kind, "", ""), "guard")
            self.assertEqual(f["hit"], kind)
            a, rule, proof = CD.verdict(f)
            self.assertEqual((a, rule), (CD.HOLD, ""),
                             "штампованный вид %s с пустой целью закрыт (%s): %s" % (kind, rule, proof))
            if f["obj"]:
                # `live_sheet` — fail-closed сам: без имени листа гард пишет объектом «лист не
                # определён», и это НАЗВАННАЯ цель, а не пустая. Держится первой половиной.
                self.assertIn("названа цель", proof, kind)
            else:
                self.assertIn("не разобрал" if kind in CD._UNPARSED_KINDS else "действие разобрано",
                              proof, kind)

    def test_hard_card_kinds_are_never_closed(self):
        """Виды, которые гард спрашивает ВСЕГДА (`_HARD_CARD`), дежурный не снимает никогда —
        ни с объектом, ни без него."""
        for kind in PG._HARD_CARD:
            for obj in ("", "живая цель"):
                a, rule, _ = CD.decide(live_card(kind, obj, ""), "guard")
                self.assertEqual(a, CD.HOLD, "hard-вид %s закрыт условием «%s»" % (kind, rule))

    def test_rule_object_alive_only_for_pre_stamp_form(self):
        """Дверь условия 1 не заколочена: карточка БЕЗ штампа класса (форма старше 31.07, когда
        `_verb_acts` ещё не отсеивал подстроку слоем раньше) закрывается как прежде."""
        f = CD.facts(live_card("git_force", "", "", stamp=False), "guard")
        self.assertEqual((f["hit"], f["obj"]), ("", ""))
        a, rule, proof = CD.verdict(f)
        self.assertEqual((a, rule), (CD.CLOSE, CD.R_OBJECT))
        self.assertIn("старше 31.07", proof)

    def test_top_tier_without_object_never_becomes_a_card(self):
        """Высший вид без объекта до дежурного не доходит ВООБЩЕ: гард отвечает `deny`-текстом,
        а не карточкой. Проверяем на живом коде — иначе «дежурный закрыл высший вид» читалось бы
        как дыра там, где карточки не было."""
        for kind in PG._TOP_TIER:
            text = PG._deny_text(kind, "", "")
            self.assertTrue(text.startswith("⛔ ОТКАЗ"), kind)
            self.assertEqual(CD.facts(text, "guard")["has_card_shape"], False,
                             "отказ высшего вида опознан как карточка — вид %s" % kind)

    def test_verdict_decided_by_target_not_by_origin(self):
        """Решает ЦЕЛЬ, а не источник: та же карточка с целью удержана при любом происхождении."""
        card = live_card("sqlite", "", "sqlite3 moderation_ipc.db \"update meta set v=1\"")
        for origin in ("guard", "self", "legacy", "", None):
            self.assertEqual(CD.decide(card, origin)[0], CD.HOLD, "origin=%r" % (origin,))


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ЧЕТЫРЕ УСЛОВИЯ
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestFourRules(unittest.TestCase):

    def _empty_object_card(self):
        """Карточка гардовой формы С ПУСТЫМ объектом и БЕЗ штампа класса — единственная форма,
        которую условие 1 сегодня закрывает (см. `test_rule_object_alive_only_for_pre_stamp_form`
        и поправку зеркала в шапке card_duty). Собрана живым `_card`, а не руками."""
        card = live_card("edit_claude", "", "", stamp=False)
        self.assertIn("Объект: —", card, "живой гард перестал печатать прочерк — корпус протух")
        return card

    def test_rule_object_closes_empty_target(self):
        action, rule, proof = CD.decide(self._empty_object_card(), "guard")
        self.assertEqual((action, rule), (CD.CLOSE, CD.R_OBJECT))
        self.assertIn("Объект", proof)

    def test_rule_object_does_not_touch_free_text(self):
        """Свободный текст без подписанной строки формой не опознан — молчание формы не повод."""
        for text in ("", "просто отчёт исполнителя", "NEEDS_APPROVAL: op=other | что-то"):
            self.assertEqual(CD.decide(text, "guard")[0], CD.HOLD, repr(text))

    def test_rule_object_ignores_number(self):
        """ЧИСЛО в условие не входит: карточка с целью и БЕЗ числа удерживается.
        Регресс против воскрешения снятого 02.08 правила «нет числа → нет карточки»."""
        card = live_card("kill", "сервис nginx", "systemctl stop nginx")
        f = CD.facts(card, "guard")
        self.assertTrue(f["obj"])
        self.assertEqual(f["num"], "", "у остановки сервиса числа нет по природе")
        self.assertEqual(CD.verdict(f)[0], CD.HOLD)

    # ПОЧЕМУ У ПРАВИЛ 2–4 ФИКСТУРА — СВОБОДНЫЙ ТЕКСТ, А НЕ КАРТОЧКА ГАРДА. На карточке гардовой
    # формы правило 1 решает РАНЬШЕ (объект либо назван — тогда высший вид держит всё, либо пуст —
    # тогда закрывает условие 1). Значит условия 2–4 достижимы ТОЛЬКО на тексте без подписанной
    # строки. На ПК такой текст карточкой сегодня не становится вовсе (замок происхождения), и
    # это ровно тот факт, из-за которого вклад правил 2–4 на ПК равен нулю — см. артефакт.
    _FREE = "какой-то текст без подписанных строк карточки"

    def test_rule_source_closes_only_proven_self(self):
        self.assertEqual(CD.decide(self._FREE, "self")[1], CD.R_SOURCE)
        for origin in ("guard", "legacy", "guard_weak", ""):
            self.assertEqual(CD.decide(self._FREE, origin)[0], CD.HOLD, origin)

    def test_rule_done_needs_answered_id(self):
        self.assertEqual(CD.decide(self._FREE, "guard", answered_id="")[0], CD.HOLD)
        a, r, p = CD.decide(self._FREE, "guard", answered_id="207")
        self.assertEqual((a, r), (CD.CLOSE, CD.R_DONE))
        self.assertIn("207", p)

    def test_rule_probe_dup_and_mark(self):
        self.assertEqual(CD.decide(self._FREE, "guard", dup_id="12")[1], CD.R_PROBE)
        probed = PG._probe_intercept(self._FREE)
        self.assertIn(CD._PROBE_MARK, probed, "пометка пробы гарда изменилась — читатель протух")
        self.assertTrue(CD.facts(probed, "guard")["probe"])

    def test_rules_2_4_unreachable_on_guard_shaped_card(self):
        """СТРУКТУРНОЕ СВОЙСТВО ПК, названное тестом: на карточке ГАРДА условия 2–4 не решают
        никогда — объект решает раньше. Это и есть причина, по которой их живой вклад нулевой."""
        named = live_card("delete", "", "rm suggest.py")
        empty = self._empty_object_card()
        for origin in ("self", "guard", "legacy"):
            self.assertEqual(CD.decide(named, origin, dup_id="9", answered_id="9")[0], CD.HOLD)
            self.assertEqual(CD.decide(empty, origin, dup_id="9", answered_id="9")[1], CD.R_OBJECT)

    def test_first_matching_rule_decides(self):
        """Порядок = нумерация владельца: пустой объект решает раньше дубля."""
        self.assertEqual(CD.decide(self._empty_object_card(), "guard", dup_id="12")[1], CD.R_OBJECT)

    def test_named_target_beats_every_rule(self):
        """Высший вид неотменяем: цель названа — ни одно из четырёх условий не срабатывает."""
        card = live_card("delete", "", "rm suggest.py")
        a, rule, proof = CD.decide(card, "self", dup_id="12", answered_id="207")
        self.assertEqual((a, rule), (CD.HOLD, ""))
        self.assertIn("высший вид", proof)


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ЧТЕНИЕ ПОЛЯ С МЕСТА ГАРДА (зеркало object_from_card / _is_obj_place)
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestFieldReadFromGuardPlace(unittest.TestCase):

    def test_object_read_matches_guard_reader(self):
        """Дежурный и гард обязаны читать ОДНО значение: иначе «да» владельца сверяется с одним,
        а вопрос снимается по другому."""
        for kind, (obj, cmd) in _LIVE_TARGET.items():
            card = live_card(kind, obj, cmd)
            self.assertEqual(CD.facts(card, "guard")["obj"], PG.object_from_card(card),
                             "разошлись читатели объекта у вида %s" % kind)

    def test_forged_object_line_below_body_is_not_read(self):
        """Поддельная строка «Объект:», приехавшая НЕ на своё место, полем не считается.
        Форма без штампа — чтобы вердикт зависел именно от чтения поля, а не от щита штампа."""
        card = live_card("edit_claude", "", "", stamp=False) + "\nОбъект: подделка"
        f = CD.facts(card, "guard")
        self.assertEqual(f["obj"], "", "прочитана строка вне места гарда")
        self.assertEqual(CD.verdict(f)[1], CD.R_OBJECT)

    def test_kind_stamp_read_all_or_nothing(self):
        card = live_card("delete", "", "rm suggest.py")
        self.assertEqual(CD.facts(card, "guard")["hit"], "delete")
        two = card.replace(PG.KIND_LINE_PREFIX + "delete", PG.KIND_LINE_PREFIX + "delete, env")
        self.assertEqual(CD.facts(two, "guard")["hit"], "", "штамп на два вида принят за класс")

    def test_number_read_from_its_place(self):
        card = live_card("delete", "", "rm suggest.py")
        self.assertEqual(CD.facts(card, "guard")["num"], "1 цель")

    def test_number_matches_the_printer_on_whole_vocabulary(self):
        """ЗАМОК ОТ ТИХОГО РАЗЪЕЗДА: читающая сторона обязана отдавать РОВНО то число, которое
        напечатала печатающая, — и не на одном виде, а на всём живом словаре.

        Именно здесь ломается связка со смещением: `39fb7e4` (03.09.2026) вставил между объектом
        и числом строку последствия, и `lines[i + 1]` стал читать её. Тест сверяет с источником
        (`_card_fields`), поэтому следующая вставленная строка покраснит гейт СРАЗУ и на всех
        видах, а не потеряет число молча в карточке владельца."""
        lost = []
        for kind, (obj, cmd) in _LIVE_TARGET.items():
            printed = " ".join((PG._card_fields(kind, obj, cmd)[1] or "").split())
            got = CD.facts(live_card(kind, obj, cmd), "guard")["num"]
            if got != printed:
                lost.append("%s: напечатано %r, прочитано %r" % (kind, printed, got))
        self.assertEqual(lost, [], "читатель числа разъехался с печатающей стороной: %s"
                         % "; ".join(lost))

    def test_conseq_prefix_mirrors_the_live_guard(self):
        """Зеркало литерала, а не пересказ: строку последствия дежурный знает своей копией
        (модуль чистый, импорта гарда в нём нет) — расхождение обязано падать здесь."""
        self.assertEqual(CD._CONSEQ_PREFIX, PG.CONSEQ_LINE_PREFIX,
                         "строка последствия в гарде переименована — читатель числа ослеп")

    # ── ОТРИЦАТЕЛЬНЫЕ: число ВЫГЛЯДИТ на месте, но принадлежит ДРУГОЙ строке ──
    # Обе фикстуры зелены у сегодняшнего (смещение) и у правильного (место) читателя, и обе
    # КРАСНЫ у наивного «поискать „Число:“ подальше» — того самого фикса, которым позиционную
    # связку чинят вторично. Прибор обязан показать отказ, а не правдоподобное чужое число.

    def test_foreign_number_line_below_the_body_is_not_borrowed(self):
        """Строка «Число:», приехавшая в ТЕЛО карточки, полем не считается: своей строки числа
        у блока нет — значит «не знаю», а не число из чужой строки."""
        card = "\n".join(ln for ln in live_card("delete", "", "rm suggest.py").splitlines()
                         if not ln.startswith("Число: "))
        f = CD.facts(card + "\nЧисло: 99 целей", "guard")
        self.assertEqual(f["obj"], "suggest.py", "объект должен читаться как прежде")
        self.assertEqual(f["num"], "", "число взято из чужой строки тела")

    def test_number_is_not_borrowed_from_the_next_block(self):
        """Маркер ПК МНОГОБЛОЧНЫЙ, и объект берётся из ПЕРВОГО блока (`object_from_card`). Число
        обязано быть числом ТОГО ЖЕ блока: у второго оно напечатано и выглядит на месте."""
        first = "\n".join(ln for ln in PG._card("kill", "PID 4872", "taskkill /PID 4872 /F").splitlines()
                          if not ln.startswith("Число: "))
        second = PG._card("delete", "", "rm suggest.py")
        card = GUARD_PREFIX + first + "\n" + second + "\n" + PG.KIND_LINE_PREFIX + "kill"
        self.assertIn("Число: 1 цель", second, "фикстура собрана не из живой карточки")
        f = CD.facts(card, "guard")
        self.assertEqual(f["obj"], "PID 4872", "объект должен остаться от первого блока")
        self.assertEqual(f["num"], "", "число одолжено у соседнего блока")


# ══════════════════════════════════════════════════════════════════════════════════════════
#  FAIL-CLOSED
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestFailClosed(unittest.TestCase):

    def test_two_verdicts_only(self):
        self.assertEqual({CD.HOLD, CD.CLOSE}, {"hold", "close"})

    def test_broken_facts_hold(self):
        for bad in (None, "", [], 0, "строка"):
            self.assertEqual(CD.verdict(bad)[0], CD.HOLD, repr(bad))

    def test_broken_rule_holds(self):
        """Сбой ЛЮБОГО правила → HOLD, а не проглоченная карточка."""
        orig = CD._RULES

        def boom(f):
            raise RuntimeError("правило сломалось")
        try:
            CD._RULES = ((CD.R_OBJECT, boom),) + orig[1:]
            a, rule, proof = CD.decide(live_card("edit_claude", "", "", stamp=False), "guard")
            self.assertEqual((a, rule), (CD.HOLD, ""))
            self.assertIn("не отработало", proof)
        finally:
            CD._RULES = orig

    def test_close_result_keeps_card_verbatim_and_has_no_button(self):
        card = live_card("edit_claude", "", "")
        out = CD.close_result(card, CD.R_OBJECT, "доказательство")
        self.assertIn(card, out, "исходная карточка обязана сохраниться ДОСЛОВНО")
        self.assertNotIn("approved", out.lower())
        self.assertIn("операция НЕ выполнена", out)

    def test_close_result_starts_with_manual_mark(self):
        """МАШИННЫЙ КОНТРАКТ ПК: первый символ терминального текста — `MANUAL_MARK`, иначе
        снятую карточку подхватит самопочинка/надзор цепи (`NO_HEAL_PREFIXES`) и сожжёт круг
        думателя на вопросе, который уже снят."""
        import pc_orchestrator as PO
        out = CD.close_result("карточка", CD.R_OBJECT, "доказательство")
        self.assertTrue(out.startswith(PO.MANUAL_MARK), "шапка потеряла ✋ — контракт ПК нарушен")
        self.assertTrue(out.lstrip().startswith(PO.NO_HEAL_PREFIXES))

    def test_note_has_no_answer_form(self):
        n = CD.note("ПК", 208, CD.R_OBJECT, "доказательство")
        self.assertIn("дежурный по карточкам", n)
        self.assertIn("ПК", n)
        for word in ("«да»", "approve", "кнопк"):
            self.assertNotIn(word, n.lower())


# ══════════════════════════════════════════════════════════════════════════════════════════
#  ИНВАРИАНТ CARD_DUTY_PURE — граница держится отсутствием инструментов, а не докстрингом
# ══════════════════════════════════════════════════════════════════════════════════════════
_ALLOWED_IMPORTS = frozenset(("re",))
# ГОЛЫЕ встроенные имена, дающие руки. Именно голые: `compile` — исполнение, а `re.compile` —
# сборка регулярки, и различает их ПОЗИЦИЯ, а не слово (то же правило исполняющей позиции, что в
# гарде). Метод объекта (`f.get`, `m.group`) сюда не попадает по построению.
_FORBIDDEN_CALLS = frozenset((
    "open", "exec", "eval", "compile", "__import__", "input", "globals", "vars", "setattr",
))
# Модули, через которые руки приходят. Второй рубеж — на случай отложенного/переименованного
# импорта внутри функции.
_FORBIDDEN_ATTR_ROOTS = frozenset((
    "os", "sys", "subprocess", "shutil", "socket", "requests", "urllib", "pathlib", "tempfile",
    "sqlite3", "bridge_http", "pretool_guard", "bc", "builtins", "logging",
))


def duty_ast_findings(src):
    """→ список (адрес, чем плохо). Пустой список = дежурный чист. Разбор ast, не подстрока:
    имя в комментарии, строке и докстринге кодом не является."""
    out = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in _ALLOWED_IMPORTS:
                    out.append(("строка %d" % node.lineno,
                                "импорт «%s» вне списка %s — у решения появились руки"
                                % (a.name, sorted(_ALLOWED_IMPORTS))))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in _ALLOWED_IMPORTS:
                out.append(("строка %d" % node.lineno,
                            "импорт из «%s» вне списка — у решения появились руки" % node.module))
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _FORBIDDEN_CALLS:
                out.append(("строка %d" % node.lineno,
                            "вызов «%s» — запись/исполнение в модуле, который обязан быть "
                            "чистым решением" % fn.id))
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                    and fn.value.id in _FORBIDDEN_ATTR_ROOTS:
                out.append(("строка %d" % node.lineno,
                            "обращение к «%s.%s» — модуль решения не смеет трогать мир"
                            % (fn.value.id, fn.attr)))
        elif isinstance(node, ast.Name) and node.id == "approved":
            out.append(("строка %d" % node.lineno,
                        "имя «approved» в коде дежурного: одобрять он не вправе ни при каких "
                        "условиях — вердиктов ровно два, HOLD и CLOSE"))
    return out


class TestCardDutyPure(unittest.TestCase):
    """CARD_DUTY_PURE. FAIL-CLOSED: файла нет / не парсится → провал, а не тишина: нечитаемый
    дежурный доверия не имеет."""

    def test_live_module_is_clean(self):
        with open(DUTY_SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(duty_ast_findings(src), [], "живой card_duty.py обзавёлся руками")

    def test_single_import(self):
        with open(DUTY_SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual(len(imports), 1, "импорт в модуле решения обязан быть ровно один (re)")

    def test_goldens_position_beats_word(self):
        """`re.compile` и `f.get(...)` законны, голый `compile`/`os.remove` — нет."""
        cases = [
            ("re.compile и .get законны (позиция, не слово)", 0,
             "import re\n_R = re.compile('x')\ndef f(d):\n    return d.get('a') or _R.match('b')\n"),
            ("импорт вне списка → флаг", 1, "import re\nimport os\n"),
            ("from-импорт вне списка → флаг", 1, "from pretool_guard import _card\n"),
            ("голый open() → флаг", 1, "import re\nf = open('x')\n"),
            ("os.remove → флаг (импорт + вызов)", 2, "import os\nos.remove('x')\n"),
            ("имя approved в коде → флаг", 1, "import re\napproved = 1\n"),
            ("слово approved в СТРОКЕ и комментарии — не флаг", 0,
             "import re\n# approved тут только словом\nS = 'approved'\n"),
        ]
        for title, expect, src in cases:
            self.assertEqual(len(duty_ast_findings(src)), expect, title)

    def test_unparsable_is_a_failure(self):
        with self.assertRaises(SyntaxError):
            duty_ast_findings("def broken(:\n")

    def test_word_approved_absent_from_module(self):
        """Слова «approved» нет в модуле ВООБЩЕ (ни кодом, ни текстом): его нельзя одобрить даже
        случайной копипастой из демона."""
        with open(DUTY_SRC, encoding="utf-8") as f:
            self.assertNotIn("approved", f.read())


# ══════════════════════════════════════════════════════════════════════════════════════════
#  РУКИ ДЕМОНА
# ══════════════════════════════════════════════════════════════════════════════════════════
class TestDaemonHands(unittest.TestCase):

    def setUp(self):
        import pc_orchestrator as PO
        self.PO = PO

    def test_default_off(self):
        """Дефолт — ВЫКЛЮЧЕНО: без флага ветка мертва и карточка идёт владельцу байт-в-байт."""
        old = os.environ.pop("CARD_DUTY", None)
        try:
            self.assertFalse(self.PO._card_duty_on())
            self.assertFalse(self.PO._maybe_card_duty(1, live_card("edit_claude", "", "")))
        finally:
            if old is not None:
                os.environ["CARD_DUTY"] = old

    def test_flag_parsing(self):
        old = os.environ.get("CARD_DUTY")
        try:
            for val, want in (("1", True), ("0", False), ("", False), ("да", False), ("11", False)):
                os.environ["CARD_DUTY"] = val
                self.assertEqual(self.PO._card_duty_on(), want, repr(val))
        finally:
            os.environ.pop("CARD_DUTY", None)
            if old is not None:
                os.environ["CARD_DUTY"] = old

    def test_banner_names_the_switch(self):
        """СОСТОЯНИЕ РУБИЛЬНИКА ЧИТАЕТСЯ ИЗ ЛОГА СТАРТА, А НЕ ТОЛЬКО ИЗ `.env`.

        Замер третьего захода (05.08.2026) назвал это остатком с ценой: срабатываний дежурного в
        боевом логе ноль, но ноль срабатываний доказывает, что ВЕТКА НЕ РАБОТАЛА, и НЕ доказывает,
        что рубильник стоит в нуле. Единственным способом проверить «дежурный выключен» оставалось
        чтение `.env` — то есть КРАСНАЯ операция ради проверки выключенности. Цена молчания тут
        выше, чем у флагов думателя: этот рубильник решает, КАКИЕ ВОПРОСЫ ВЛАДЕЛЕЦ УВИДИТ.

        Проверяем по ИСХОДНИКУ и до цикла — там же, где стоит зеркальный тест флагов думателя
        (`test_pc_orchestrator.test_banner_names_thinker_flags_from_live_env`), и той же меркой:
        печатается ЖИВОЙ `os.environ` процесса, а не строка файла (их расходит `override=False`
        у load_dotenv — унаследованное от предка значение файл не перезаписывает)."""
        with open(os.path.join(REPO, "pc_orchestrator.py"), encoding="utf-8") as f:
            body = f.read().split("def _main_loop", 1)[1]
        head = body[:body.index("while not _stopped()")]
        self.assertIn("CARD_DUTY=%s", head, "баннер старта не называет рубильник дежурного")
        self.assertIn("int(_card_duty_on())", head, "в баннер идёт не живое значение рубильника")
        self.assertIn('os.environ.get("CARD_DUTY")', head,
                      "рядом с флагом обязано стоять СЫРОЕ значение живого окружения")
        self.assertIn('int(_flag_forced_off("CARD_DUTY"))', head,
                      "аварийный стоп-файл поверх флага в баннере не назван")

    def test_origin_reads_the_lock_stamp(self):
        self.assertEqual(self.PO._duty_origin(live_card("delete", "", "rm x.py")), "guard")
        self.assertEqual(self.PO._duty_origin("op=kill | снять процесс"), "legacy")
        self.assertEqual(self.PO._duty_origin(""), "legacy")

    def test_origin_never_returns_self(self):
        """`self` на ПК недостижим: замок терминализует заявку исполнителя раньше карточки."""
        for text in ("NEEDS_APPROVAL: op=other | я хочу", "🗣 слова исполнителя", "",
                     live_card("delete", "", "rm x.py")):
            self.assertNotEqual(self.PO._duty_origin(text), "self")

    def test_fingerprint_collapses_same_card_and_splits_different(self):
        a = live_card("delete", "", "rm suggest.py")
        self.assertEqual(self.PO._duty_fingerprint(a),
                         self.PO._duty_fingerprint(a.replace(GUARD_PREFIX, "")))
        self.assertNotEqual(self.PO._duty_fingerprint(a),
                            self.PO._duty_fingerprint(live_card("delete", "", "rm bot.py")))

    def test_hands_are_failsafe_on_bridge_error(self):
        """Мост не ответил → близнецы пусты, дежурный СТРОЖЕ, а не мягче."""
        old = self.PO.bc.get_pending
        try:
            self.PO.bc.get_pending = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("нет сети"))
            self.assertEqual(self.PO._duty_queue_twins(1, live_card("delete", "", "rm x.py")),
                             ("", ""))
        finally:
            self.PO.bc.get_pending = old


class TestOriginLock(unittest.TestCase):

    def test_prefix_matches_live_detector(self):
        """Константа-читатель дежурного и литерал ЖИВОГО замка обязаны совпадать. Замок не
        трогаем — связь держит этот тест, иначе две копии разъехались бы молча."""
        import pc_orchestrator as PO
        token = "tok"
        marker = token + PO.MARKER_SEP + "🔴 Хочу удалить файл x — разрешить?"
        produced = PO._detect_needs_approval("", marker, token)
        self.assertIsNotNone(produced, "живой детектор перестал рождать карточку из маркера")
        self.assertTrue(produced.startswith(PO.DUTY_GUARD_PREFIX),
                        "префикс замка изменился: дежурный примет боевую карточку за legacy")
        self.assertEqual(PO.DUTY_GUARD_PREFIX, GUARD_PREFIX)

    def test_executor_claim_is_not_a_card(self):
        """Заявка исполнителя карточкой не становится — она и до дежурного не доезжает."""
        import pc_orchestrator as PO
        self.assertIsNone(PO._detect_needs_approval("NEEDS_APPROVAL: op=kill | снять процесс", "", "tok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
