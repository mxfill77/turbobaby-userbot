# -*- coding: utf-8 -*-
"""test_shtab_box_signals.py — набор СИГНАЛЬНОЙ ОСТАНОВКИ ящика заданий Штаба.

ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА, ПО ОДНОМУ НА СИГНАЛ, названы заданием и живут в
:class:`TestNegative`; каждый идёт В ПАРЕ с положительным контролем. Пара
обязательна: правило «никогда ничего не брать» проходит любую отрицательную
проверку идеально и стои́т ноль, поэтому рядом с «сигнал есть → не берём» всегда
стои́т «сигнала нет → БЕРЁМ».

Плюс пятый и шестой отрицательные, которых задание требует отдельно:

* **неопределимый сигнал → не берём** (`test_undeterminate_*`): корпус не
  прочитан, и «не знаю» значит «стоп»;
* **сигнал В снимается САМ** (`test_signal_c_clears_itself_*`): после ответа
  владельца карточка уходит из ``needs_approval``, и ящик идёт дальше БЕЗ
  чьего-либо слова — ни метки, ни правки, ни рестарта.

Прочие классы:

* :class:`TestPurity` — инвариант ``SHTAB_SIGNALS_PURE``: слой решения не смеет
  завести часы, диск, сеть, окружение или подпроцессы.
* :class:`TestMirrors` — ЗАИМСТВОВАННЫЕ ЛИТЕРАЛЫ. Чистый слой не импортирует ни
  демона, ни судью, ни терминал карточек — он ПОВТОРЯЕТ их контракты, и равенство
  каждого сторожит тест, а не дисциплина правки. Ровно тем же приёмом
  `contour_digest` держит имена полей реестра вердиктов.
* :class:`TestReason` — «что считать одной причиной»: код, маркеры, канон.
* :class:`TestRelease` — метка снятия: именная, одноразовая, читается из узла.
* :class:`TestNotWeakened` — прежние замки ящика НЕ ОСЛАБЛЕНЫ: пустая фраза
  остановки возвращает поведение, равное прежнему.

ФИКСТУРЫ ЗАИМСТВОВАНЫ У НАБОРА ЯЩИКА (`test_shtab_box`), а не набраны заново:
``FakeQueue``, ``_node``, ``_reader``, ``GOOD_BODY`` описывают ОДИН предмет, и
второй их экземпляр разошёлся бы с первым молча — тот же класс, которым живёт
весь этот куст.
"""
from __future__ import annotations

import ast
import io
import os
import unittest

import card_terminal_log as ctl
import contour_digest as cd
import done_judge_pc as dj
import shtab_box as sb
import shtab_box_run as run
import shtab_box_signals as sig
import zayavki_pc as zp
from test_shtab_box import (FakeQueue, GOOD_BODY, HEAD_TEXT, TODAY, _box, _doc_reader,
                            _files, _lister, _node, _reader, _ready)

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = "signal-probe-0902"


def _src(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


def _closed(tid, status="done", result="", day=TODAY, key=None):
    """Закрытый ряд очереди С МАРКЕРОМ ЯЩИКА — ровно в той форме, в какой ящик его ставит."""
    return {"id": tid, "status": status, "result": result,
            "task_text": "%s дата=%s ключ=%s] %s\nтело задания"
                         % (sb.MARK, day, key or ("k%03d" % tid), sb.HEAD_WORDS)}


def _card(tid, frm="Filipp"):
    """КАРТОЧКА ГАРДА — ряд, ДЕРЖАЩИЙ операцию, в живой форме.

    Живая форма замерена по коду демона: карточку рождает
    ``process_new`` → ``run_task`` вернул ``needs_approval`` → ``set_needs_approval``,
    и ``task_text`` у такого ряда — ИСХОДНЫЙ ТЕКСТ ЗАДАЧИ, без маркера вопроса.
    Маркера здесь нет намеренно: он и есть различитель.
    """
    return {"id": tid, "status": "needs_approval", "from": frm,
            "task_text": "почини гейт и закоммить\n🔴 гард: удаление файла tmp/x — разрешить?"}


def _zayavka(tid, frm="Filipp-review-claim"):
    """ЗАЯВКА ВНЕШНЕГО КАНАЛА (ступень B) — ждёт мнения, не держит НИЧЕГО."""
    return {"id": tid, "status": "needs_approval", "from": frm,
            "task_text": "[заявка-ревью дата=%s ключ=abcdef012345]\nвопрос владельцу" % TODAY}


def _recon_ask(tid):
    """ЗАЯВКА РАЗВЕДКИ (ступень E) — живая форма четырёх рядов замера 03.09."""
    return {"id": tid, "status": "needs_approval", "from": "Filipp-recon-ask",
            "task_text": "[разведка-заявка дата=%s ключ=38e6d0ce6598]\nповод требует "
                         "операционного действия" % TODAY}


def _revizor_card(tid):
    """INFO-КАРТОЧКА РЕВИЗОРА — третий вид, освобождённый от APPROVAL_TTL."""
    return {"id": tid, "status": "needs_approval", "from": "Filipp-revizor",
            "task_text": "%s\n🔍 Ревизор: находки" % sig.REVIZOR_CARD_MARK}


def _faceless(tid):
    """Ряд БЕЗ ТЕЛА: вид определить нечем — отказ обязан быть консервативным."""
    return {"id": tid, "status": "needs_approval", "from": "Filipp", "task_text": ""}


def _ledger(*pairs):
    """(tid, proved, addressed) → реестр вердиктов в форме судьи."""
    rows = {}
    for i, (tid, ok, addressed) in enumerate(pairs, 1):
        rows[str(tid)] = {dj.F_PROVED: bool(ok), dj.F_ADDRESSED: bool(addressed),
                          dj.F_REASON: "проба", dj.F_SEQ: i}
    return lambda root: (rows, True, "")


def _folder(docs=None):
    """Заглушки источника: (перечислитель папки, читатель тел). Кандидат — :data:`KEY`."""
    files, bodies = _files(docs if docs is not None else _box((KEY, GOOD_BODY)))
    return _lister(files), _doc_reader(bodies)


def _tick(node_text=None, closed=(), rows=(), ledger=None, place=False, budget=50,
          queue=None, docs=None, **kw):
    """Оборот ящика на заглушках, с ЖИВЫМ кандидатом В ПАПКЕ.

    Кандидат нужен по построению: закрытые ряды (а с ними и сигналы) ящик
    спрашивает ТОЛЬКО при живом задании — иначе платил бы 27 с витка за пустой
    ящик. Набор обязан повторять живой порядок, а не удобный.

    ПЕРЕЕЗД 03.09.2026 РАЗВЁЛ ДВА ИСТОЧНИКА, и здесь это видно лучше всего:
    ЗАДАНИЯ приезжают из ПАПКИ (``docs``), а МЕТКИ СНЯТИЯ сигналов — по-прежнему из
    ШАПКИ (``node_text``). Шапка задач больше не несёт, поэтому по умолчанию она
    пуста: положи мы в неё блок «для живости», ящик честно доложил бы о закрытой
    двери, и половина проверок сигналов зеленела бы рядом с предупреждением о
    задании, которое никто не возьмёт.

    ПОТОЛОК ПОДНЯТ НАМЕРЕННО (``budget=50``): закрытые ряды набора несут маркер
    ящика и потому ТРАТЯТ суточный потолок. Оставь мы боевые три — половина
    проверок сигналов зеленела бы от исчерпанного бюджета, то есть проверяла бы
    не то. Тесты самого потолка передают боевое число явно.
    """
    import datetime
    import tempfile

    text = node_text if node_text is not None else HEAD_TEXT
    lister, doc_reader = _folder(docs)
    q = queue if queue is not None else FakeQueue(rows=list(rows), closed=list(closed))
    return run.tick(root=tempfile.mkdtemp(prefix="shtabsig_"), place=place, queue=q,
                    budget=budget, reader=_reader(text),
                    lister=kw.pop("lister", lister), doc_reader=kw.pop("doc_reader", doc_reader),
                    ledger=ledger or (lambda root: ({}, True, "")),
                    clock=lambda tz: datetime.datetime(2026, 9, 2, 12, 0, tzinfo=tz), **kw)


# ═══════════════════════════ чистота ═══════════════════════════════════


class TestPurity(unittest.TestCase):
    """SHTAB_SIGNALS_PURE — слой решения остаётся чистым."""

    FORBIDDEN_MODULES = ("os", "io", "time", "datetime", "subprocess", "socket",
                         "requests", "urllib", "pathlib", "shutil", "sqlite3",
                         "brain_writer", "pc_orchestrator")
    FORBIDDEN_CALLS = ("open", "getenv", "input", "exec", "eval", "compile", "__import__")

    def setUp(self):
        self.tree = ast.parse(_src("shtab_box_signals.py"))

    def test_no_impure_imports(self):
        got = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                got += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                got.append(node.module.split(".")[0])
        bad = sorted(set(got) & set(self.FORBIDDEN_MODULES))
        self.assertEqual([], bad, "чистый слой сигналов импортировал %s" % bad)

    def test_no_impure_calls(self):
        bad = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in self.FORBIDDEN_CALLS:
                    bad.append(node.func.id)
        self.assertEqual([], sorted(set(bad)), "чистый слой сигналов зовёт %s" % sorted(set(bad)))

    def test_module_imports_only_pure_neighbours(self):
        """Соседи названы поимённо: кольцо импортов ловится тестом, а не при запуске."""
        got = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                got |= {a.name for a in node.names}
        self.assertEqual({"re", "contour_digest", "recon_auto", "shtab_box", "zayavki_pc"}, got)


# ═══════════════════════════ заимствованные литералы ═══════════════════


class TestMirrors(unittest.TestCase):
    """Повторённые контракты обязаны быть РАВНЫ оригиналу — иначе они врут молча."""

    def test_fail_code_regex_equals_the_daemon_one(self):
        import pc_orchestrator

        self.assertEqual(pc_orchestrator.FAIL_CODE_RE.pattern, sig.FAIL_CODE_RE.pattern)

    def test_reject_mark_equals_the_card_terminal_one(self):
        self.assertEqual(ctl.REJECT_MARK, sig.REJECT_MARK)

    def test_unknown_prefix_equals_the_judge_one(self):
        self.assertEqual(dj.UNKNOWN_PREFIX, sig.UNKNOWN_PREFIX)

    def test_every_fail_code_of_the_daemon_has_a_name_here(self):
        """Новый код демона обязан ронять ЭТОТ тест, а не тихо съезжать в «прочее»."""
        import pc_orchestrator

        self.assertEqual(sorted(pc_orchestrator.FAIL_REASONS),
                         sorted(sig.FAIL_NAMES))
        for code, (words, _mark) in pc_orchestrator.FAIL_REASONS.items():
            self.assertEqual(words, sig.FAIL_NAMES[code], "имя кода %s разъехалось" % code)

    def test_judge_words_come_from_the_digest_not_from_here(self):
        """«Доказана» — слово `contour_digest`, и своего у сигналов нет."""
        self.assertNotIn("доказана", _src("shtab_box_signals.py").split("judged_of")[0][-400:])
        self.assertEqual(cd.JUDGE_PROVED, "доказана")

    def test_revizor_card_mark_equals_the_daemon_one(self):
        """Маркер info-карточки ревизора заимствован — расхождение обязано ронять ТЕСТ."""
        import pc_orchestrator

        self.assertEqual(pc_orchestrator.REVIZOR_OWNER_MARK, sig.REVIZOR_CARD_MARK)

    def test_two_claim_marks_are_not_retyped_here(self):
        """Маркеры ступеней B и E литералами здесь НЕ набраны — их спрашивают у владельца.

        Третий экземпляр строки разошёлся бы с обоими молча, и разбор вернул бы
        «карточка» на живой заявке ровно в день смены маркера. Проверяем не
        обещание, а ИСХОДНИК.
        """
        src = _src("shtab_box_signals.py")
        for mark in (zp.CLAIM_MARK, zp.RECON_MARK):
            self.assertNotIn('"%s' % mark, src, "маркер %s набран литералом" % mark)

    def test_the_daemon_frees_exactly_these_three_kinds_from_the_ttl(self):
        """РАЗЛИЧИТЕЛЬ СТОИ́Т НА ЖИВОМ ПРАВИЛЕ ДЕМОНА, а не на нашем вкусе.

        «Держит операцию» = «подлежит APPROVAL_TTL». Освобождены от TTL ровно три
        вида (`process_approval_timeouts`), и ровно их сигнал В зовёт заявками.
        Заведи демон четвёртый — этот тест обязан покраснеть, а не съесть его
        молча в кучку держащих.
        """
        import pc_orchestrator as pc

        for text in ("[заявка-ревью дата=2026-09-03 ключ=abcdef012345]\nx",
                     "[разведка-заявка дата=2026-09-03 ключ=abcdef012345]\nx",
                     "%s\n🔍 Ревизор: находки" % pc.REVIZOR_OWNER_MARK):
            free = (pc._is_review_claim(text) or pc._is_recon_ask(text)
                    or pc._is_revizor_owner_card(text))
            kind, _how = sig.awaiting_kind({"id": 1, "task_text": text})
            self.assertTrue(free, "демон больше не освобождает этот вид от TTL: %r" % text[:40])
            self.assertEqual(sig.KIND_ASK, kind, "вид разошёлся с TTL-гейтом демона: %r" % text[:40])
        card = "почини гейт\n🔴 гард: удаление файла — разрешить?"
        self.assertFalse(pc._is_review_claim(card) or pc._is_recon_ask(card)
                         or pc._is_revizor_owner_card(card))
        self.assertEqual(sig.KIND_BLOCK, sig.awaiting_kind({"id": 2, "task_text": card})[0])

    def test_body_is_picked_by_the_same_rule_as_the_owner_module(self):
        """Тело ряда выбирается ТЕМ ЖЕ правилом, что у `zayavki_pc.split_awaiting`.

        Слепок очереди кладёт первую строку тела в ``goal``; разойдись правила —
        витрина и ящик назвали бы ОДИН ряд разными видами, и оба были бы уверены.
        """
        row = {"id": 7, "status": "needs_approval",
               "goal": "[заявка-ревью дата=%s ключ=abcdef012345]" % TODAY}
        self.assertEqual([row], zp.split_awaiting([row])["zayavki"])
        self.assertEqual(sig.KIND_ASK, sig.awaiting_kind(row)[0])
        recon = {"id": 8, "status": "needs_approval",
                 "goal": "[разведка-заявка дата=%s ключ=abcdef012345]" % TODAY}
        self.assertEqual([recon], zp.split_awaiting([recon])["foreign"])
        self.assertEqual(sig.KIND_ASK, sig.awaiting_kind(recon)[0])


# ═══════════════════════════ что считать одной причиной ════════════════


class TestReason(unittest.TestCase):
    """Сравнение ПО СМЫСЛУ, а не по случайному совпадению подстроки (класс 01.09)."""

    def test_reason_class_reads_the_structural_code_first(self):
        cls, how = sig.reason_class("⏱ провал [причина=run_timeout · таймаут прогона]: 2700s")
        self.assertEqual("код причины", how)
        self.assertIn("run_timeout", cls)
        self.assertIn("таймаут прогона", cls)

    def test_same_code_different_numbers_is_one_reason(self):
        a, _ = sig.reason_class("⏱ провал [причина=run_timeout · таймаут прогона]: 2700s — head")
        b, _ = sig.reason_class("⏱ провал [причина=run_timeout · таймаут прогона]: 900s — tail")
        self.assertEqual(a, b)

    def test_different_codes_are_different_reasons(self):
        a, _ = sig.reason_class("[причина=run_timeout · таймаут прогона]")
        b, _ = sig.reason_class("[причина=exec_error · ошибка выполнения]")
        self.assertNotEqual(a, b)

    def test_owner_refusal_and_judge_verdict_are_their_own_classes(self):
        self.assertEqual(sig.CLS_REJECT, sig.reason_class(ctl.REJECT_MARK + ": не надо")[0])
        self.assertEqual(sig.CLS_UNKNOWN,
                         sig.reason_class(dj.UNKNOWN_PREFIX + " — адрес пуст")[0])

    def test_canon_glues_one_reason_that_differs_only_by_numbers_and_paths(self):
        """Канон терпим к тому, чем причина отличается от СЕБЯ: числа, пути, кавычки."""
        a, how = sig.reason_class("мост не отдал ряд 118 из docs/one.md за 900 с")
        b, _ = sig.reason_class("мост не отдал ряд 77 из tmp/other/two.md за 12 с")
        self.assertEqual(a, b)
        self.assertIn("канон", how)

    def test_canon_does_NOT_glue_a_phrase_with_its_own_longer_form(self):
        """ЧЕСТНАЯ ГРАНИЦА: сравнение полное, а не по префиксу.

        Склей мы короткую форму с длинной — это было бы сравнение по началу
        строки, то есть подстрокой: ровно класс, закрытый 01.09.
        """
        a, _ = sig.reason_class("ошибка: мост молчит")
        b, _ = sig.reason_class("ошибка: мост молчит, обмен не завершён, повторим позже")
        self.assertNotEqual(a, b)

    def test_class_name_is_cut_only_for_showing(self):
        long_reason = "отказ " + " ".join("слово%d" % i for i in range(40))
        cls, _how = sig.reason_class(long_reason)
        self.assertGreater(len(cls.split()), sig.CANON_SHOW)
        self.assertTrue(sig.show_class(cls).endswith("…"))
        self.assertLessEqual(len(sig.show_class(cls).split()), sig.CANON_SHOW + 1)

    def test_a_shared_word_does_NOT_glue_two_different_reasons(self):
        """ГЛАВНЫЙ тест класса 01.09: общее слово равенства не даёт."""
        a, _ = sig.reason_class("ошибка: диск переполнен, писать некуда")
        b, _ = sig.reason_class("ошибка: модель отказала, повторить нечем")
        self.assertNotEqual(a, b)

    def test_empty_result_is_a_class_of_its_own(self):
        self.assertEqual(sig.CLS_SILENT, sig.reason_class("")[0])
        self.assertEqual(sig.CLS_SILENT, sig.reason_class("   \n  ")[0])

    def test_box_marker_line_does_not_glue_reasons(self):
        """Маркер ряда у каждой задачи свой — склеивать по нему нельзя."""
        head = "%s дата=%s ключ=aaa] %s\n" % (sb.MARK, TODAY, sb.HEAD_WORDS)
        a, _ = sig.reason_class(head + "диск переполнен, писать некуда")
        b, _ = sig.reason_class(head + "модель отказала, повторить нечем")
        self.assertNotEqual(a, b)


# ═══════════════════════════ метка снятия ══════════════════════════════


class TestRelease(unittest.TestCase):

    def test_mark_is_read_from_the_node_text(self):
        mark = sig.mark_of(sig.SIG_A, "118|121")
        text = "шапка узла\n%s\n[[ЗАДАЧА ключ=abc]]\nтело\n[[КОНЕЦ ключ=abc]]" % (
            sig.RELEASE_FORM % mark)
        self.assertEqual({mark}, sig.release_marks(text))

    def test_mark_is_named_not_general(self):
        """Разные улики — разные метки: снять сигнал навсегда одной строкой нельзя."""
        self.assertNotEqual(sig.mark_of(sig.SIG_A, "118|121"),
                            sig.mark_of(sig.SIG_A, "121|123"))
        self.assertNotEqual(sig.mark_of(sig.SIG_A, "118|121"),
                            sig.mark_of(sig.SIG_B, "118|121"))

    def test_mark_is_latin_hex_only(self):
        """Кириллицы в машинной части нет — иначе вернулась бы ловушка похожих букв."""
        mark = sig.mark_of(sig.SIG_A, "118|121")
        self.assertRegex(mark, r"^[0-9a-f]{12}$")

    def test_release_form_is_accepted_by_its_own_reader(self):
        """Образец, который печатает фраза остановки, обязан ею же и читаться."""
        mark = sig.mark_of(sig.SIG_B, "2026-09-02|отказ владельца")
        self.assertEqual({mark}, sig.release_marks(sig.RELEASE_FORM % mark))

    def test_release_does_not_touch_signals_that_clear_themselves(self):
        """В и Г словом не снимаются: держать их словом было бы глупостью."""
        got = sig.evaluate(closed=[], open_rows=[_card(1)], judged={}, day=TODAY,
                           left=0, budget=3,
                           released={sig.mark_of(sig.SIG_C, "1"), sig.mark_of(sig.SIG_D, "x")})
        c = [s for s in got if s["sig"] == sig.SIG_C][0]
        self.assertTrue(c["on"])
        self.assertFalse(c["released"])
        self.assertEqual(sig.BY_ITSELF, c["release"])


# ═══════════════════════════ отрицательные тесты ═══════════════════════


class TestNegative(unittest.TestCase):
    """По одному на сигнал, каждый — В ПАРЕ с положительным контролем."""

    # ── сигнал А: две подряд недоказанные ────────────────────────────────
    def test_signal_a_stops_the_box_and_names_the_reason(self):
        rep = _tick(closed=[_closed(11, "failed", "[причина=exec_error · ошибка выполнения]"),
                            _closed(12, "failed", "[причина=run_timeout · таймаут прогона]")],
                    place=True)
        self.assertEqual([], rep["placed"], "остановленный ящик поставил задание")
        self.assertIn("СИГНАЛЬНАЯ ОСТАНОВКА", rep["stop"])
        self.assertIn("сигнал А", rep["stop"])
        self.assertIn("#11", rep["stop"])
        self.assertIn("#12", rep["stop"])
        self.assertIn(sig.BY_OWNER, rep["stop"])
        self.assertEqual(rep["stop"], rep["why"], "причина остановки не доехала до исхода")

    def test_signal_a_silent_when_the_last_one_is_proved(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ: одна доказанная в хвосте — и «двух подряд» нет."""
        rep = _tick(closed=[_closed(11, "failed", "[причина=exec_error · ошибка]"),
                            _closed(12, "done", "готово")],
                    ledger=_ledger((12, True, True)), place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]), rep["why"])

    def test_signal_a_needs_two_not_one(self):
        """Одна недоказанная — НЕ сигнал: остановка ни на чём хуже отсутствия остановки."""
        rep = _tick(closed=[_closed(11, "failed", "[причина=exec_error · ошибка]")], place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]))

    def test_signal_a_counts_the_owner_refusal_as_undoubted(self):
        """Отказ владельца — тоже «не получила вердикта сделано», дословно по заданию."""
        rep = _tick(closed=[_closed(11, "failed", ctl.REJECT_MARK + " (ответ с ПК)"),
                            _closed(12, "failed", ctl.REJECT_MARK + " (ответ с ПК)")],
                    place=True)
        self.assertIn("сигнал А", rep["stop"])
        self.assertIn("отклонена владельцем", rep["stop"])

    def test_signal_a_counts_the_judge_unknown_as_undoubted(self):
        """`done` без вердикта судьи — тоже не «сделано»: одного статуса мало."""
        rep = _tick(closed=[_closed(11, "done", "сдано"), _closed(12, "done", "сдано")],
                    ledger=_ledger((11, False, True), (12, False, True)), place=True)
        self.assertIn("сигнал А", rep["stop"])
        self.assertIn(cd.JUDGE_UNPROVED, rep["stop"])

    def test_signal_a_released_by_the_owner_word_lets_the_box_take(self):
        """СИГНАЛ СНЯТ → БЕРЁМ. Метка приходит из узла, кода никто не правил."""
        mark = sig.mark_of(sig.SIG_A, "11|12")
        node = HEAD_TEXT + (sig.RELEASE_FORM % mark) + "\n"
        rep = _tick(node_text=node,
                    closed=[_closed(11, "failed", "[причина=exec_error · ошибка]"),
                            _closed(12, "failed", "[причина=run_timeout · таймаут]")],
                    place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]), rep["why"])
        a = [s for s in rep["signals"] if s["sig"] == sig.SIG_A][0]
        self.assertTrue(a["on"], "снятый сигнал обязан остаться видимым, а не исчезнуть")
        self.assertTrue(a["released"])

    def test_a_foreign_mark_does_not_release_this_case(self):
        """Метка ИМЕННАЯ: чужая строка снятия остановку не трогает."""
        node = HEAD_TEXT + (
            sig.RELEASE_FORM % sig.mark_of(sig.SIG_A, "77|78")) + "\n"
        rep = _tick(node_text=node,
                    closed=[_closed(11, "failed", "[причина=exec_error · ошибка]"),
                            _closed(12, "failed", "[причина=run_timeout · таймаут]")],
                    place=True)
        self.assertIn("сигнал А", rep["stop"])
        self.assertEqual([], rep["placed"])

    # ── сигнал Б: третий раз одна причина ────────────────────────────────
    def test_signal_b_stops_on_the_third_time_of_one_reason(self):
        same = "⏱ провал [причина=run_timeout · таймаут прогона]: таймаут %ds"
        rep = _tick(closed=[_closed(21, "failed", same % 2700),
                            _closed(22, "failed", same % 2700),
                            _closed(23, "failed", same % 900)], place=True)
        self.assertEqual([], rep["placed"])
        self.assertIn("сигнал Б", rep["stop"])
        self.assertIn("run_timeout", rep["stop"])
        self.assertIn("3 раз", rep["stop"])
        self.assertIn(sig.BY_OWNER, rep["stop"])

    def test_signal_b_silent_on_two_of_one_reason(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ порога: два раза — ещё не сигнал."""
        same = "⏱ провал [причина=run_timeout · таймаут прогона]"
        rep = _tick(closed=[_closed(21, "failed", same), _closed(22, "failed", same),
                            _closed(23, "done", "сдано")],
                    ledger=_ledger((23, True, True)), place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]))

    def test_signal_b_does_not_glue_three_different_reasons(self):
        """ТРИ РАЗНЫЕ причины — не сигнал Б. Здесь бы и сработал класс подстроки."""
        rep = _tick(closed=[_closed(21, "failed", "[причина=run_timeout · таймаут прогона]"),
                            _closed(22, "failed", "[причина=exec_error · ошибка выполнения]"),
                            _closed(23, "done", "сдано")],
                    ledger=_ledger((23, True, True)), place=True)
        self.assertEqual("", rep["stop"], rep["stop"])

    def test_signal_b_counts_rows_not_readings(self):
        """Счёт по НОМЕРАМ строк: тот же корпус, прочитанный дважды, счёт не двигает."""
        same = "[причина=exec_error · ошибка выполнения]"
        rows = [_closed(21, "failed", same), _closed(22, "failed", same)]
        first = _tick(closed=rows, place=True)
        second = _tick(closed=rows + list(rows), place=True)   # тот же корпус, задвоенный
        b1 = [s for s in first["signals"] if s["sig"] == sig.SIG_B][0]
        b2 = [s for s in second["signals"] if s["sig"] == sig.SIG_B][0]
        self.assertEqual(b1["count"], b2["count"])
        self.assertLess(b1["count"], sig.REPEAT_FLOOR)

    def test_signal_b_counts_only_today(self):
        """Сутки берутся из маркера ряда: вчерашние причины сегодняшний счёт не двигают."""
        same = "[причина=exec_error · ошибка выполнения]"
        rep = _tick(closed=[_closed(21, "failed", same, day="2026-09-01"),
                            _closed(22, "failed", same, day="2026-09-01"),
                            _closed(23, "failed", same, day=TODAY)], place=True)
        b = [s for s in rep["signals"] if s["sig"] == sig.SIG_B][0]
        self.assertEqual(1, b["count"])
        self.assertFalse(b["on"])

    def test_signal_b_released_by_the_owner_word(self):
        same = "[причина=exec_error · ошибка выполнения]"
        cls, _how = sig.reason_class(same)
        mark = sig.mark_of(sig.SIG_B, "%s|%s" % (TODAY, cls))
        node = HEAD_TEXT + (sig.RELEASE_FORM % mark) + "\n"
        rep = _tick(node_text=node,
                    closed=[_closed(21, "failed", same), _closed(22, "failed", same),
                            _closed(23, "failed", same)], place=True)
        b = [s for s in rep["signals"] if s["sig"] == sig.SIG_B][0]
        self.assertTrue(b["released"])
        # А при этом ЖИВ — три failed подряд остановят и сами по себе.
        self.assertIn("сигнал А", rep["stop"])

    # ── сигнал В: карточка ГАРДА ждёт ответа ─────────────────────────────
    def test_signal_c_stops_while_a_card_awaits_the_owner(self):
        rep = _tick(rows=[_card(1), _card(2)], place=True)
        self.assertEqual([], rep["placed"])
        self.assertIn("сигнал В", rep["stop"])
        self.assertIn("#1", rep["stop"])
        self.assertIn("держат 2", rep["stop"])
        self.assertIn(sig.BY_ITSELF, rep["stop"])
        self.assertNotIn(sig.BY_OWNER, rep["stop"].split("сигнал В")[1])

    # ── ЧЕТЫРЕ ОТРИЦАТЕЛЬНЫХ ТЕСТА РАЗЛИЧИТЕЛЯ (пункт 5 задания 03.09) ───
    def test_only_claims_in_the_queue_and_the_box_takes_work(self):
        """1. ТОЛЬКО ЗАЯВКИ — ЯЩИК БЕРЁТ ЗАДАНИЕ.

        Живой корпус замера 03.09 дословно: четыре заявки разведки и одна заявка
        внешнего канала, карточек гарда ноль. До этой правки ящик стоял на всех
        пяти; ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ тут же — задание обязано УЕХАТЬ, иначе
        правило «никогда ничего не берём» прошло бы проверку идеально и стоило
        ноль.
        """
        rows = [_recon_ask(13), _recon_ask(31), _recon_ask(39), _recon_ask(40), _zayavka(79)]
        rep = _tick(rows=rows, place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]), rep["why"])
        c = [s for s in rep["signals"] if s["sig"] == sig.SIG_C][0]
        self.assertFalse(c["on"])
        self.assertEqual(0, c["count"])
        # ЖДУЩИЕ НЕ ПРОПАЛИ ИЗ ВИДА: молчание про пять открытых заявок читалось бы
        # как «у владельца пусто» — а это ровно то враньё, от которого правка.
        self.assertIn("ждут мнения 5", c["why"])

    def test_a_guard_card_among_the_claims_still_stops_the_box(self):
        """2. ЕСТЬ КАРТОЧКА ГАРДА — НЕ БЕРЁТ. Право карточки останавливать не тронуто."""
        rows = [_recon_ask(13), _zayavka(79), _card(80)]
        rep = _tick(rows=rows, place=True)
        self.assertEqual([], rep["placed"])
        self.assertIn("держат 1, ждут мнения 2", rep["stop"])
        self.assertIn("#80", rep["stop"])

    def test_an_unreadable_row_stops_the_box_and_says_why(self):
        """3. ВИД РЯДА НЕ ОПРЕДЕЛЁН — НЕ БЕРЁТ, И ПРИЧИНА НАЗВАНА ОТДЕЛЬНЫМИ СЛОВАМИ.

        Отказ консервативный: цена лишней остановки — виток, цена пропущенной
        карточки гарда — красная операция, о которой владельца не спросили. Но
        «не разобрали ряд» обязано читаться иначе, чем «владелец держит красное».
        """
        rep = _tick(rows=[_faceless(90)], place=True)
        self.assertEqual([], rep["placed"])
        self.assertIn("сигнал В", rep["stop"])
        self.assertIn("НЕОПОЗНАННЫМ видом", rep["stop"])
        self.assertIn("#90", rep["stop"])
        kind, how = sig.awaiting_kind(_faceless(90))
        self.assertEqual(sig.KIND_UNKNOWN, kind)
        self.assertIn("нет тела", how)

    def test_an_answered_guard_card_clears_the_signal_by_itself(self):
        """4. КАРТОЧКА ОТВЕЧЕНА — СНИМАЕТСЯ САМ. Ни метки, ни правки, ни рестарта."""
        before = _tick(rows=[_card(81), _zayavka(79)], place=True)
        self.assertIn("держат 1", before["stop"])
        after = _tick(rows=[dict(_card(81), status="approved"), _zayavka(79)], place=True)
        self.assertEqual("", after["stop"], after["stop"])
        self.assertEqual(1, len(after["placed"]), after["why"])

    def test_the_revizor_info_card_is_not_a_holding_card(self):
        """Третий вид, освобождённый демоном от TTL, ящик тоже не держит."""
        rep = _tick(rows=[_revizor_card(70)], place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]), rep["why"])

    def test_two_numbers_never_add_up_into_one(self):
        """Числа идут ВРОЗЬ: «держат» и «ждут мнения» в одно не складываются."""
        split = sig.split_waiting([_card(1), _faceless(2), _recon_ask(3), _zayavka(4)])
        self.assertEqual([1, 2], [r["id"] for r in sig.holding(split)])
        self.assertEqual([3, 4], [r["id"] for r in split["ask"]])
        c = sig.signal_c([_card(1), _faceless(2), _recon_ask(3), _zayavka(4)])
        self.assertEqual(2, c["count"])
        self.assertIn("держат 2, ждут мнения 2", c["why"])
        self.assertNotIn("держат 4", c["why"])

    def test_open_rows_that_are_not_awaiting_are_not_counted_at_all(self):
        """Предмет В — ожидание ответа, а не открытая работа (её судит owner_busy)."""
        split = sig.split_waiting([{"id": 1, "status": "new", "task_text": "работа"},
                                   {"id": 2, "status": "in_progress", "task_text": "работа"}])
        self.assertEqual([], sig.holding(split))
        self.assertEqual([], split["ask"])

    def test_signal_c_clears_itself_after_the_owner_answered(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗАДАНИЯ: снимается САМ, без чужого вмешательства.

        Единственная разница между двумя оборотами — ряд ушёл из `needs_approval`
        (владелец ответил). Ни метки, ни правки, ни рестарта между ними нет.
        """
        before = _tick(rows=[_card(5)], place=True)
        self.assertIn("сигнал В", before["stop"])
        after = _tick(rows=[dict(_card(5), status="approved")], place=True)
        self.assertEqual("", after["stop"], after["stop"])
        self.assertEqual(1, len(after["placed"]), after["why"])

    def test_signal_c_silent_without_cards(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ: карточек нет — берём."""
        rep = _tick(rows=[{"id": 9, "status": "new", "from": "x", "task_text": "чужая"}],
                    place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]))

    # ── сигнал Г: суточный потолок ───────────────────────────────────────
    def test_signal_d_is_shown_but_does_not_enforce_here(self):
        """Г ВИДЕН всегда и НЕ решает: у одного замка не бывает двух владельцев."""
        rep = _tick(place=True)
        d = [s for s in rep["signals"] if s["sig"] == sig.SIG_D][0]
        self.assertFalse(d["enforced"])
        self.assertIn("осталось", d["why"])
        self.assertEqual(4, len(rep["signals"]), "человек обязан видеть все четыре")

    def test_signal_d_shows_the_ceiling_reached(self):
        rows = [_closed(30 + i, "done", "сдано") for i in range(sb.DAILY_BUDGET)]
        rep = _tick(closed=rows, budget=sb.DAILY_BUDGET,
                    ledger=_ledger(*[(30 + i, True, True) for i in range(sb.DAILY_BUDGET)]),
                    place=True)
        d = [s for s in rep["signals"] if s["sig"] == sig.SIG_D][0]
        self.assertTrue(d["on"])
        self.assertIn("исчерпан", d["why"])
        # Держит его ПРЕЖНИЙ замок, а не сигнальный слой: фраза остановки молчит,
        # а задание всё равно не взято.
        self.assertEqual("", rep["stop"])
        self.assertEqual([], rep["placed"])
        self.assertTrue(any("потолок" in w for _k, w in rep["held"]), rep["held"])

    # ── неопределимый сигнал ─────────────────────────────────────────────
    def test_undeterminate_signal_stops_the_box(self):
        """«Не знаю» значит «стоп»: реестр вердиктов не прочитан → не берём."""
        rep = _tick(closed=[_closed(41, "done", "сдано"), _closed(42, "done", "сдано")],
                    ledger=lambda root: ({}, False, "файл реестра битый"), place=True)
        self.assertEqual([], rep["placed"])
        self.assertIn("ОПРЕДЕЛИТЬ НЕЛЬЗЯ", rep["stop"])
        self.assertIn("«не знаю» значит «стоп»", rep["stop"])
        a = [s for s in rep["signals"] if s["sig"] == sig.SIG_A][0]
        self.assertFalse(a["determinate"])

    def test_undeterminate_signal_is_released_by_the_owner_word_too(self):
        """Иначе молчащий прибор держал бы ящик до правки кода — а её условие запрещает."""
        mark = sig.mark_of(sig.SIG_A, "неизвестно|реестр|%s" % TODAY)
        node = HEAD_TEXT + (sig.RELEASE_FORM % mark) + "\n"
        rep = _tick(node_text=node,
                    closed=[_closed(41, "done", "сдано"), _closed(42, "done", "сдано")],
                    ledger=lambda root: ({}, False, "файл реестра битый"), place=True)
        self.assertEqual("", rep["stop"], rep["stop"])
        self.assertEqual(1, len(rep["placed"]), rep["why"])

    def test_closed_corpus_unreadable_stops_both_a_and_b(self):
        import datetime
        import tempfile

        queue = FakeQueue(rows=[], closed=[], closed_ok=False)
        rep = run.tick(root=tempfile.mkdtemp(prefix="shtabsig_"), place=True, queue=queue,
                       reader=_reader(HEAD_TEXT), lister=_folder()[0], doc_reader=_folder()[1],
                       ledger=lambda root: ({}, True, ""),
                       clock=lambda tz: datetime.datetime(2026, 9, 2, 12, 0, tzinfo=tz))
        self.assertEqual([], rep["placed"])
        for letter in (sig.SIG_A, sig.SIG_B):
            one = [s for s in rep["signals"] if s["sig"] == letter][0]
            self.assertTrue(one["on"] and not one["determinate"], letter)


# ═══════════════════════════ видимость ═════════════════════════════════


class TestVisible(unittest.TestCase):
    """Молчащая остановка запрещена: она неотличима от пустого ящика."""

    def test_stop_words_name_what_on_what_and_how_to_release(self):
        got = _tick(closed=[_closed(11, "failed", "[причина=exec_error · ошибка]"),
                            _closed(12, "failed", "[причина=run_timeout · таймаут]")])
        words = got["stop"]
        self.assertIn("сигнал А", words)                       # ЧТО сработало
        self.assertIn("#11", words)                            # НА ЧЁМ стои́т
        self.assertIn("[[ЯЩИК СНЯТЬ метка=", words)            # ЧЕМ снимается
        self.assertIn(sb.NODE_NAME, words)                     # и КУДА это класть

    def test_journal_line_is_ASK_for_signals_held_by_the_owner_word(self):
        got = _tick(closed=[_closed(11, "failed", "[причина=exec_error · ошибка]"),
                            _closed(12, "failed", "[причина=run_timeout · таймаут]")])
        self.assertTrue(got["signal_journal"].startswith("ASK "), got["signal_journal"])
        self.assertIn("ЯЩИК ШТАБА", got["signal_journal"])

    def test_journal_line_is_NOTE_when_the_signal_clears_itself(self):
        got = _tick(rows=[_card(1)])
        self.assertTrue(got["signal_journal"].startswith("NOTE "), got["signal_journal"])

    def test_all_four_are_listed_even_when_quiet(self):
        got = _tick()
        words = sig.all_words(got["signals"])
        self.assertEqual(4, len(words))
        for letter in sig.SIGNALS:
            self.assertTrue(any(("сигнал %s" % letter) in w for w in words), letter)

    def test_status_render_prints_all_four(self):
        import datetime
        import tempfile

        data = run.build(tempfile.mkdtemp(prefix="shtabsig_"),
                         queue=FakeQueue(rows=[], closed=[_closed(11, "failed", "x")]),
                         reader=_reader(HEAD_TEXT), lister=_folder()[0], doc_reader=_folder()[1],
                         ledger=lambda root: ({}, True, ""),
                         clock=lambda tz: datetime.datetime(2026, 9, 2, 12, 0, tzinfo=tz))
        data.pop("queue", None)
        text = run._render(None, data)
        self.assertIn("сигналы", text)
        self.assertIn("ОСТАНОВКА:", text)
        for letter in sig.SIGNALS:
            self.assertIn("сигнал %s" % letter, text)

    def test_signals_are_not_counted_when_the_corpus_was_not_asked(self):
        """ТРЕТЬЕ СОСТОЯНИЕ: «не спрашивали» — это НЕ «прибор отказал»."""
        rows = [{"id": 4, "status": "new", "from": "Filipp", "task_text": "работа владельца"}]
        rep = _tick(queue=FakeQueue(rows=rows, busy=True, busy_ids=[4]), place=True)
        self.assertFalse(rep["signals_asked"])
        self.assertEqual("", rep["stop"])
        self.assertEqual([], rep["signals"])
        self.assertIn("задача владельца", rep["why"])


# ═══════════════════════════ прежние замки не ослаблены ════════════════


class TestNotWeakened(unittest.TestCase):
    """Сигналы добавлены СВЕРХ замков, а не вместо них."""

    def test_empty_stop_words_keep_select_exactly_as_before(self):
        blocks = _ready((KEY, GOOD_BODY))
        base = sb.select(blocks, today=TODAY, marks_ok=True, source_ok=True)
        same = sb.select(blocks, today=TODAY, marks_ok=True, source_ok=True, stop_words="")
        self.assertEqual(base, same)
        self.assertEqual(1, len(base[0]))

    def test_stop_words_can_only_refuse_never_permit(self):
        """Фраза остановки не открывает НИ ОДНОГО прежнего замка."""
        blocks = _ready((KEY, GOOD_BODY))
        for kw in ({"source_ok": False}, {"owner_busy": True}, {"marks_ok": False},
                   {"budget": 0}, {"limit": 0}):
            args = dict(today=TODAY, marks_ok=True, source_ok=True)
            args.update(kw)
            take, _held = sb.select(blocks, stop_words="", **args)
            self.assertEqual([], take, kw)
            take2, _held2 = sb.select(blocks, stop_words="СТОП", **args)
            self.assertEqual([], take2, kw)

    def test_stop_reports_the_reason_for_every_block(self):
        blocks = _ready((KEY, GOOD_BODY), ("second-key-0902", GOOD_BODY))
        take, held = sb.select(blocks, today=TODAY, stop_words="СТОП: проба")
        self.assertEqual([], take)
        self.assertEqual(2, len(held))
        self.assertTrue(all(why == "СТОП: проба" for _k, why in held))

    def test_signals_do_not_touch_the_gates(self):
        """Ворота приёма ветка не трогала: блок без адреса как не брался, так и не берётся."""
        body = GOOD_BODY.replace("АДРЕС РЕЗУЛЬТАТА", "адрес где-то там")
        rep = _tick(docs=_box((KEY, body)), place=True)
        self.assertEqual([], rep["placed"])
        self.assertTrue(any("no_address" in why for _k, why in rep["held"]), rep["held"])

    def test_stop_file_still_wins_over_everything(self):
        """Стоп-файл гасит ящик ЦЕЛИКОМ — сигналы этого не меняют."""
        import datetime
        import tempfile

        root = tempfile.mkdtemp(prefix="shtabsig_")
        with io.open(os.path.join(root, run.STOP_FILE), "w", encoding="utf-8") as fh:
            fh.write("off")
        rep = run.tick(root=root, place=True, queue=FakeQueue(),
                       reader=_reader(HEAD_TEXT), lister=_folder()[0], doc_reader=_folder()[1],
                       clock=lambda tz: datetime.datetime(2026, 9, 2, 12, 0, tzinfo=tz))
        self.assertTrue(rep["off"])
        self.assertEqual([], rep["placed"])
        self.assertEqual("", rep["stop"])


# ═══════════════════════════ врезка: журнал и витрина ═════════════════


class TestWiring(unittest.TestCase):
    """Строка остановки идёт И В ЖУРНАЛ, И ТУДА, ГДЕ ЕЁ УВИДИТ ВЛАДЕЛЕЦ."""

    def setUp(self):
        import pc_orchestrator

        self.o = pc_orchestrator

    def _report(self, stop="СТОП: проба", marks=("aaa111bbb222",), line="ASK ЯЩИК ШТАБА: проба"):
        return {"stop": stop, "stop_marks": list(marks), "signal_journal": line}

    def test_new_stop_is_reported_to_the_journal_once(self):
        said = []
        out = self.o._shtab_box_announce(self._report(), [],
                                         journal=lambda line, repo=None: said.append(line))
        self.assertEqual(["ASK ЯЩИК ШТАБА: проба"], said)
        self.assertEqual(["aaa111bbb222"], out)

    def test_the_same_stop_is_NOT_reported_again(self):
        """Иначе одна карточка дала бы под полсотни строк за ночь — журнал стал бы лентой."""
        said = []
        out = self.o._shtab_box_announce(self._report(), ["aaa111bbb222"],
                                         journal=lambda line, repo=None: said.append(line))
        self.assertEqual([], said)
        self.assertEqual(["aaa111bbb222"], out)

    def test_a_different_case_is_reported_even_after_the_first(self):
        said = []
        self.o._shtab_box_announce(self._report(marks=("ccc333ddd444",)), ["aaa111bbb222"],
                                   journal=lambda line, repo=None: said.append(line))
        self.assertEqual(1, len(said))

    def test_lifted_stop_clears_the_memory_so_a_repeat_speaks_again(self):
        """Молчание про повторившуюся поломку — та самая молчащая остановка."""
        self.assertEqual([], self.o._shtab_box_announce(self._report(stop=""),
                                                        ["aaa111bbb222"],
                                                        journal=lambda *a, **k: None))

    def test_journal_failure_never_breaks_the_tick(self):
        def boom(line, repo=None):
            raise RuntimeError("журнал молчит")

        out = self.o._shtab_box_announce(self._report(), [], journal=boom)
        self.assertEqual(["aaa111bbb222"], out)

    def test_tick_file_carries_the_stop_for_the_showcase(self):
        import tempfile

        path = os.path.join(tempfile.mkdtemp(prefix="shtabtick_"), "tick.json")
        self.o._shtab_box_write_tick(1.0, path, stop="СТОП: проба",
                                     marks=["aaa111bbb222"], said=["aaa111bbb222"])
        got = self.o._shtab_box_read_tick(path)
        self.assertEqual("СТОП: проба", got["stop"])
        self.assertEqual(["aaa111bbb222"], self.o._shtab_box_said(got))

    def test_the_showcase_reads_the_same_file_by_the_same_name(self):
        import vitrina_pc_run as vr

        self.assertEqual(os.path.basename(self.o.SHTAB_BOX_TICK_FILE), vr.BOX_TICK_FILE)

    def test_the_showcase_shows_the_stop_phrase_verbatim(self):
        import tempfile

        import vitrina_pc as vp
        import vitrina_pc_run as vr

        root = tempfile.mkdtemp(prefix="shtabvit_")
        words = "СИГНАЛЬНАЯ ОСТАНОВКА ЯЩИКА · сигнал А: #11 и #12 · [[ЯЩИК СНЯТЬ метка=abc123]]"
        with io.open(os.path.join(root, vr.BOX_TICK_FILE), "w", encoding="utf-8") as fh:
            fh.write('{"ts": 1.0, "stop": %s, "marks": [], "said": []}'
                     % __import__("json").dumps(words, ensure_ascii=False))
        self.assertEqual(words, vr.read_box_stop(root))
        rows = vp.part_nums(None, 1, TODAY, None, 0, box_stop=words)
        self.assertTrue(any("ЯЩИК СНЯТЬ" in r for r in rows), rows)

    def test_the_showcase_says_nothing_when_there_is_no_mark(self):
        import tempfile

        import vitrina_pc as vp
        import vitrina_pc_run as vr

        self.assertEqual("", vr.read_box_stop(tempfile.mkdtemp(prefix="shtabvit_")))
        rows = vp.part_nums(None, 1, TODAY, None, 0, box_stop="")
        self.assertFalse(any("ОСТАНОВКА" in r for r in rows), rows)

    def test_the_signal_module_is_registered_as_a_lazy_leaf(self):
        """Реестр ленивых листьев сверяется с ЖИВЫМ замыканием МНОЖЕСТВАМИ.

        Новый лист, не внесённый в него, роняет сторожа замыкания в наборе демона;
        внесённый дважды — тоже. Здесь мы держим свою половину факта явно, чтобы
        поломка называлась «ящик», а не «список демона разъехался».
        """
        self.assertIn("shtab_box_signals.py", self.o._ORCH_LAZY_UNCOVERED)
        self.assertEqual(1, list(self.o._ORCH_LAZY_UNCOVERED).count("shtab_box_signals.py"))
        self.assertNotIn("shtab_box_signals.py", self.o._ORCH_RUNTIME,
                         "ленивый лист в безусловном замыкании — значит демон грузит его на старте")

    def test_both_news_are_said_when_the_closed_corpus_is_unreadable(self):
        """Прежняя новость («день исчерпан») и новая («сигнал не определить») — обе."""
        rep = _tick(queue=FakeQueue(rows=[], closed=[], closed_ok=False), place=True)
        self.assertIn("НЕИЗВЕСТНО", rep["why"])
        self.assertIn("день считаем исчерпанным", rep["why"])
        self.assertIn("ОПРЕДЕЛИТЬ НЕЛЬЗЯ", rep["why"])


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
