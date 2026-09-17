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
import sys
import unittest

import card_terminal_log as ctl
import contour_digest as cd
import done_judge_pc as dj
import shtab_box as sb
import shtab_box_run as run
import shtab_box_signals as sig
import zayavki_pc as zp
from test_shtab_box import (FakeQueue, GOOD_BODY, HEAD_TEXT, TODAY, _box, _dead_reader,
                            _doc_reader, _files, _HoldDoor, _lister, _node, _reader, _ready)

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
    """(tid, proved, addressed) → реестр вердиктов в форме судьи.

    КЛЮЧ — ИМЯ РЯДА, А НЕ НОМЕР (правка 11.09.2026 вслед за коммитом ff8b149). До неё
    фикстура ключевала номером, и это было ВЕРНО ровно до того дня, когда ключом
    реестра стало имя: с той минуты набор описывал реестр, которого на полосе больше
    нет, и зеленел бы на читателе, спрашивающем голым номером, — то есть сторожил бы
    прежний дефект вместо продукта.

    Имя поднимает ЕДИНСТВЕННАЯ ДВЕРЬ :func:`contour_digest.row_name`, а текст ряда
    берётся у :func:`_closed` — той же функции, что кладёт ряд в очередь. Собери мы
    имя здесь руками («ключ плюс решётка плюс номер»), у набора завелось бы второе
    правило имени, и разошлось бы оно с первым молча.
    """
    rows = {}
    for i, (tid, ok, addressed) in enumerate(pairs, 1):
        name = cd.row_name(tid, _closed(tid)["task_text"])
        rows[name] = {dj.F_PROVED: bool(ok), dj.F_ADDRESSED: bool(addressed),
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
    kw.setdefault("hold_notify_fn", _HoldDoor())
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
        self.assertEqual(len(sig.SIGNALS), len(rep["signals"]),
                         "человек обязан видеть ВСЕ сигналы, а не только сработавшие")

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


# ═══════════════════════ внешний отказ и сигнал Д ══════════════════════
# ФИКСТУРЫ ЗДЕСЬ — ДОСЛОВНЫЕ СТРОКИ ЖИВОГО ПРОВАЛА, а не идеализированные. Взяты
# из `pc_orchestrator.log` за 03.09.2026, из двух слотов ящика, которые премиса
# задания назвала сгоревшими на чужом API. Правило полосы прямое: голдены детекта —
# дословные фразы из живого провала, потому что придуманная «похожая» строка
# зеленеет молча, а живая ловит различитель на настоящем формате.

EXT_529 = ("провал [причина=exec_error · ошибка выполнения]: claude exit=1: API Error: 529 "
           "Overloaded. This is a server-side issue, usually temporary — try again in a "
           "moment. If it persists, check https://status.claude.com.. Следов работы в окне "
           "03.09 13:31–13:36 UTC нет (коммитов 0, записей журнала 0).")
# ТОТ ЖЕ ОТКАЗ ЧУЖОГО API, НО С РАБОТОЙ В ОКНЕ (живой #100). Внешним он НЕ
# считается, и это не придирка: заход успел сделать коммит, слот полоса потратила.
EXT_MID_WITH_WORK = ("НЕ ЗАКРЫТА, но В ОКНЕ ЗАДАЧИ ЕСТЬ РАБОТА [причина=exec_error · ошибка "
                     "выполнения]: claude exit=1: API Error: Server error mid-response. The "
                     "response above may be incomplete.. СЛЕДЫ в окне 03.09 13:15–13:23 UTC: "
                     "коммитов 1 (14cb105 «Замок единственной копии: снимок книги правил»). "
                     "Формальное закрытие не состоялось — НЕ переделывай вслепую.")
NO_TRACE_TAIL = "Следов работы в окне 04.09 01:10–01:14 UTC нет (коммитов 0, записей журнала 0)."


def _ext(tid, day=TODAY, key=None):
    """Ряд, сгоревший на чужом API без следов работы — живая форма #101."""
    return _closed(tid, "failed", EXT_529, day=day, key=key)


class TestExternal(unittest.TestCase):
    """ВНЕШНИЙ ОТКАЗ — ОДИН РАЗЛИЧИТЕЛЬ, ДВА ПОТРЕБИТЕЛЯ, СВОЯ ОСТАНОВКА.

    Предсмертный взгляд задания назвал главный способ провалиться: различитель
    напишут ДВАЖДЫ — свой у суточного счёта и свой у сигнала А, — и две копии
    разойдутся молча. Поэтому здесь проверяется не только поведение, но и то,
    что правило ОДНО (:func:`test_the_discriminator_lives_in_exactly_one_place`).
    """

    # ── положительная сторона: признак ловит живой отказ ──────────────────
    def test_the_live_529_row_is_recognised_as_external(self):
        ok, why = sig.external_refusal(_ext(11))
        self.assertTrue(ok, why)
        self.assertIn("exec_error", why)

    def test_network_outage_needs_no_words_of_the_child(self):
        """`network_outage` — это уже ВЕРДИКТ демона о внешнем; спорить с ним нечем."""
        row = _closed(12, "failed", "📡 провал [причина=network_outage · связи нет]: "
                                    "исполнитель молчал. " + NO_TRACE_TAIL)
        ok, why = sig.external_refusal(row)
        self.assertTrue(ok, why)

    # ── ОТРИЦАТЕЛЬНЫЕ: «наш провал выглядит как внешний» ─────────────────
    def test_our_own_report_quoting_the_api_error_buys_nothing(self):
        """ГЛАВНЫЙ отрицательный: наш черновик цитирует чужой отказ ДОСЛОВНО.

        Задача про ЭТОТ САМЫЙ класс обязана процитировать в докладе и «API Error:
        529», и «Следов работы в окне … нет» — иначе она не сможет о нём
        рассказать. Черновик едет в итог ПЕРЕД частью демона (`fail_result`:
        ``lead`` = маркер + черновик), поэтому поиск по строке целиком выдал бы
        нашему провалу бесплатный слот ЗА РАССКАЗ О БЕСПЛАТНЫХ СЛОТАХ.

        ПЕРЕСМОТРЕН 17.09 (задание 62-p). До снятия словаря тест требовал «наше» на
        ряде «claude exit=1: SyntaxError в нашем скрипте» без следов — и держался
        этот ответ ТОЛЬКО на отсутствии слов «API Error» в части демона. Но ошибка
        скрипта внутри захода — вывод инструмента, код возврата CLI она не ставит; в
        переписи 62-p (22 ряда `exec_error` за 05.08–16.09) такой формы нет ни одной.
        Суть теста — «цитата в черновике ничего не покупает» — проверяется теперь
        прямо: ответ с цитатой РАВЕН ответу без неё, а на ветке без процессного
        падения (rc = 0) цитата не даёт льготы вовсе.
        """
        ours = ("[черновик] СДЕЛАНО: разобран класс внешнего отказа. Живой пример — "
                "«claude exit=1: API Error: 529 Overloaded», у него в итоге стои́т "
                "«Следов работы в окне 03.09 13:31–13:36 UTC нет (коммитов 0, записей "
                "журнала 0)». НЕ СДЕЛАНО: тест. ")
        daemon = ("провал [причина=exec_error · ошибка выполнения]: claude exit=1: нет вывода. "
                  + NO_TRACE_TAIL)
        with_quote = sig.external_refusal(_closed(21, "failed", ours + daemon))
        without = sig.external_refusal(_closed(21, "failed", daemon))
        self.assertEqual(without[0], with_quote[0], "цитата в черновике поменяла ответ")
        self.assertNotIn("API Error", with_quote[1], "слова черновика протекли в причину")
        rc_zero = sig.external_refusal(_closed(
            21, "failed", ours + "провал [причина=exec_error · ошибка выполнения]: "
                                 "insufficient_output: нет строки RESULT. " + NO_TRACE_TAIL))
        self.assertFalse(rc_zero[0], "различитель клюнул на цитату в НАШЕМ черновике: %s"
                         % rc_zero[1])
        self.assertIn("процессного падения", rc_zero[1])

    def test_the_same_words_in_the_daemon_part_DO_count(self):
        """Парный контроль к якорю: правило не «никогда», а «не в нашей части»."""
        ok, _why = sig.external_refusal(_closed(22, "failed", EXT_529))
        self.assertTrue(ok)

    def test_a_row_with_work_in_the_window_is_ours_even_on_a_foreign_error(self):
        """Живой #100: та же API Error, но коммит 14cb105 в окне — слот потрачен."""
        ok, why = sig.external_refusal(_closed(23, "failed", EXT_MID_WITH_WORK))
        self.assertFalse(ok, why)
        self.assertIn("следы работы", why)

    def test_run_timeout_without_traces_is_never_external(self):
        """Наша задача не влезла в потолок — это наш исход, чей бы хвост ни печатался."""
        row = _closed(24, "failed", "⏱ провал [причина=run_timeout · таймаут прогона]: "
                                    "claude exit=1: API Error: 529 Overloaded. " + NO_TRACE_TAIL)
        ok, why = sig.external_refusal(row)
        self.assertFalse(ok, why)
        self.assertIn("НАШ контур", why)

    def test_unknown_window_gives_no_relief(self):
        """Третий исход: «следы НЕ проверялись» ≠ «следов не было»."""
        row = _closed(25, "failed", "провал [причина=exec_error · ошибка выполнения]: claude "
                                    "exit=1: API Error: 529 Overloaded. Окно работы неизвестно "
                                    "(отметки claim не сохранилось) — следы работы НЕ проверялись.")
        ok, why = sig.external_refusal(row)
        self.assertFalse(ok, why)
        self.assertIn("НЕ ЗНАЕМ", why)

    def test_owner_rejection_is_never_external(self):
        row = _closed(26, "failed", "%s: не надо. API Error: 529 Overloaded. %s"
                      % (sig.REJECT_MARK, NO_TRACE_TAIL))
        ok, why = sig.external_refusal(row)
        self.assertFalse(ok, why)
        self.assertIn(sig.REJECT_MARK, why)

    def test_a_delivered_task_is_never_external(self):
        ok, why = sig.external_refusal(_closed(27, "done", EXT_529))
        self.assertFalse(ok, why)

    # ── потребитель 1: суточный счёт ─────────────────────────────────────
    def test_an_external_refusal_does_not_eat_a_daily_slot(self):
        got = _tick(closed=[_closed(31, "done", "сдано"), _ext(32)],
                    ledger=_ledger((31, True, True)), budget=50)
        self.assertEqual(2, got["taken_today"], "взяты обе — дедуп не ослаблен")
        self.assertEqual(1, got["billed_today"], "съеден один слот, а не два")
        self.assertEqual(["k032"], got["external"])

    def test_the_dedup_still_remembers_the_externally_burned_key(self):
        """Ключ ВЗЯТ (второй раз не берём), но дня он не стоил — два разных вопроса."""
        got = _tick(closed=[_ext(33)], budget=50)
        self.assertEqual(1, got["taken_today"])
        self.assertEqual(0, got["billed_today"])

    def test_billable_only_narrows_and_never_grows(self):
        marks = [(TODAY, "a"), (TODAY, "b")]
        self.assertEqual(marks, sb.billable(marks, ()))
        self.assertEqual([(TODAY, "b")], sb.billable(marks, ["a"]))
        self.assertEqual([], sb.billable(marks, ["a", "b", "нет такого"]))

    # ── потребитель 2: сигнал А ──────────────────────────────────────────
    def test_signal_a_does_not_count_an_external_refusal(self):
        """Один наш провал плюс чужой отказ — «двух подряд» ещё нет."""
        rep = _tick(closed=[_closed(41, "failed", "[причина=model_refusal · отказ модели]"),
                            _ext(42)], budget=50)
        a = [s for s in rep["signals"] if s["sig"] == sig.SIG_A][0]
        self.assertFalse(a["on"], a["why"])
        self.assertIn("вычеркнуто внешних отказов: 1", a["why"])

    def test_two_of_ours_still_stop_the_box_through_a_foreign_one(self):
        """Соседство считается по НАШИМ рядам: льгота сигнал не ослабила."""
        rep = _tick(closed=[_closed(43, "failed", "[причина=model_refusal · отказ модели]"),
                            _ext(44), _closed(45, "failed", "[причина=unbacked_red · заявка]")],
                    budget=50)
        a = [s for s in rep["signals"] if s["sig"] == sig.SIG_A][0]
        self.assertTrue(a["on"], a["why"])
        self.assertIn("#43", a["why"])
        self.assertIn("#45", a["why"])

    # ── своя остановка: сигнал Д ─────────────────────────────────────────
    def test_three_external_in_a_row_stop_the_box(self):
        rep = _tick(closed=[_ext(51), _ext(52), _ext(53)], budget=50, place=True)
        e = [s for s in rep["signals"] if s["sig"] == sig.SIG_E][0]
        self.assertTrue(e["on"], e["why"])
        self.assertIn("ВНЕШНЕЕ", e["why"])
        self.assertEqual([], rep["placed"], "ящик обязан встать")
        self.assertIn("сигнал Д", rep["stop"])

    def test_two_external_are_not_enough(self):
        rep = _tick(closed=[_ext(54), _ext(55)], budget=50, place=True)
        e = [s for s in rep["signals"] if s["sig"] == sig.SIG_E][0]
        self.assertFalse(e["on"], e["why"])
        self.assertEqual(1, len(rep["placed"]), "полоса свободна — берём")

    def test_signal_e_is_named_differently_from_a_and_clears_by_itself(self):
        """Задание требует обоих различий: другое имя и свой порядок снятия."""
        self.assertNotEqual(sig.TITLE[sig.SIG_A], sig.TITLE[sig.SIG_E])
        rep = _tick(closed=[_ext(56), _ext(57), _ext(58)], budget=50)
        e = [s for s in rep["signals"] if s["sig"] == sig.SIG_E][0]
        self.assertEqual(sig.BY_EXT, e["release"])
        self.assertNotEqual(sig.BY_OWNER, e["release"])
        self.assertIn("держать словом не нужно", rep["stop"])
        self.assertFalse(sig.holdable(e), "остановка за погоду в замок не идёт")

    def test_signal_e_clears_with_the_day(self):
        """Корпус Д — сутки маркера: вчерашняя буря сегодня не держит."""
        rep = _tick(closed=[_ext(61, day="2026-09-01"), _ext(62, day="2026-09-01"),
                            _ext(63, day="2026-09-01")], budget=50, place=True)
        e = [s for s in rep["signals"] if s["sig"] == sig.SIG_E][0]
        self.assertFalse(e["on"], e["why"])
        self.assertEqual(1, len(rep["placed"]))

    def test_signal_e_stays_quiet_when_the_corpus_is_unreadable(self):
        """Третьего «не знаю» о том же не заводим: этот факт уже держат А и Б."""
        rep = _tick(queue=FakeQueue(rows=[], closed=[], closed_ok=False), budget=50)
        e = [s for s in rep["signals"] if s["sig"] == sig.SIG_E][0]
        self.assertFalse(e["on"], e["why"])
        self.assertFalse(e["determinate"])
        self.assertIn("уже держат А и Б", e["why"])

    def test_the_journal_line_for_a_lone_e_is_a_NOTE_not_an_ASK(self):
        """Д — новость о чужой стороне, а не развилка с выбором человека.

        ЭТОТ ТЕСТ НАШЁЛ ПРАВКУ СИГНАЛА Б, а не подтвердил задуманное: три чужих
        отказа — это один код ``exec_error``, то есть для Б «одна причина третий
        раз», и он вставал рядом с Д, требуя слова владельца ЗА ПОГОДУ. Обещание
        «снимается само» тонуло в просьбе положить метку.
        """
        got = _tick(closed=[_ext(64), _ext(65), _ext(66)], budget=50)
        self.assertTrue(got["signal_journal"].startswith("NOTE "), got["signal_journal"])
        b = [s for s in got["signals"] if s["sig"] == sig.SIG_B][0]
        self.assertFalse(b["on"], b["why"])

    def test_signal_b_still_counts_OUR_repeated_cause(self):
        """Парный контроль: вычерк сузил предмет Б, а не выключил его."""
        got = _tick(closed=[_closed(67, "failed", "[причина=run_timeout · таймаут прогона]"),
                            _closed(68, "failed", "[причина=run_timeout · таймаут прогона]"),
                            _closed(69, "failed", "[причина=run_timeout · таймаут прогона]")],
                    budget=50)
        b = [s for s in got["signals"] if s["sig"] == sig.SIG_B][0]
        self.assertTrue(b["on"], b["why"])
        self.assertIn("третий раз", b["why"])

    # ── ОДНО ПРАВИЛО В ОДНОМ МЕСТЕ ───────────────────────────────────────
    def test_the_discriminator_lives_in_exactly_one_place(self):
        """Предсмертный взгляд задания: две копии правила разойдутся молча.

        Проверяем ИСХОДНИКИ, а не обещание: признак внешнего отказа собран ровно
        в одной функции, а второй потребитель (суточный счёт) её ЗОВЁТ, своих
        литералов не набирая.
        """
        run_src = _src("shtab_box_run.py")
        box_src = _src("shtab_box.py")
        self.assertEqual(1, _src("shtab_box_signals.py").count("def external_refusal"))
        for literal in (sig.EXT_NO_TRACE, sig.EXT_UNKNOWN_WINDOW, "api error"):
            for name, src in (("shtab_box_run.py", run_src), ("shtab_box.py", box_src)):
                self.assertNotIn(literal, src,
                                 "литерал внешнего отказа «%s» продублирован в %s"
                                 % (literal, name))
        self.assertIn("sig.external_keys", run_src)


class TestExternalMirrors(unittest.TestCase):
    """Половинки признака заимствованы у демона — расхождение обязано ронять ТЕСТ."""

    def test_every_external_code_is_a_live_code_of_the_daemon(self):
        import pc_orchestrator

        for code in sig.EXT_CODES:
            self.assertIn(code, pc_orchestrator.FAIL_REASONS, code)

    def test_the_no_trace_phrase_is_the_daemons_own(self):
        """Фразу пишет `fail_result`; сменится она — различитель ослепнет молча."""
        src = _src("pc_orchestrator.py")
        self.assertIn(sig.EXT_NO_TRACE, src)
        self.assertIn(sig.EXT_UNKNOWN_WINDOW, src)
        self.assertIn("[причина=%s · %s]", src)
        self.assertTrue(sig.FAIL_HEAD.startswith("[причина="))

    def test_our_own_codes_are_deliberately_out(self):
        """Пять кодов НАШЕГО контура внешними не бывают — это часть признака."""
        import pc_orchestrator

        ours = set(pc_orchestrator.FAIL_REASONS) - set(sig.EXT_CODES)
        self.assertEqual({"approval_timeout", "heartbeat_timeout", "run_timeout",
                          "model_refusal", "unbacked_red"}, ours)


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

    # ── ОСТАНОВКА «НЕ ЗНАЮ» ЗОВЁТ ПРИБОР, А НЕ ВЛАДЕЛЬЦА (09.09.2026) ────────
    # ЖИВОЙ СЛУЧАЙ, РАДИ КОТОРОГО ЭТИ ТРИ ПРОВЕРКИ ЗАВЕДЕНЫ: 09.09 в 09:53:34 UTC
    # ящик встал двумя сигналами «закрытые ряды очереди не прочитаны» и попросил у
    # владельца слово с двумя метками снятия; в 10:04:02 UTC он взял задание #231
    # САМ — владелец не отвечал ничем. Просьба была ложной по трём разным причинам
    # сразу, и каждую держит своя строка ниже.

    def test_an_undeterminate_stop_asks_the_owner_for_NOTHING(self):
        """Ждём прибор: ни метки, ни кнопки, ни слова владельца — и ящик всё равно стои́т."""
        got = _tick(queue=FakeQueue(rows=[], closed=[], closed_ok=False), place=True)
        words = got["stop"]
        self.assertIn("ОПРЕДЕЛИТЬ НЕЛЬЗЯ", words)              # ЧТО сработало — на месте
        self.assertIn("ЖДЁМ ПРИБОР", words)                    # ЧЕМ снимается — прибором
        self.assertNotIn("[[ЯЩИК СНЯТЬ метка=", words)         # метку не просим
        self.assertNotIn("кнопка", words)                      # кнопки не обещаем
        self.assertNotIn(sig.BY_OWNER, words)
        # ТОРМОЗ НА МЕСТЕ: фраза непустая, значит `select` не берёт ничего.
        self.assertEqual([], got["placed"], "тормоз ослаблен вместе с текстом")
        self.assertTrue(got["signal_journal"].startswith("NOTE "), got["signal_journal"])
        # И кнопки действительно нет ни в замке, ни в двери наружу.
        self.assertEqual([], got["armed"])
        self.assertEqual([], got["hold_notices"])

    def test_a_DETERMINATE_stop_still_calls_the_owner_word_for_word(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ к правке 09.09: определённая остановка зовёт как вчера."""
        got = _tick(closed=[_closed(11, "failed", "[причина=exec_error · ошибка]"),
                            _closed(12, "failed", "[причина=run_timeout · таймаут]")],
                    place=True)
        words = got["stop"]
        self.assertIn(sig.BY_OWNER, words)                     # «ТОЛЬКО словом владельца»
        self.assertIn("[[ЯЩИК СНЯТЬ метка=", words)            # метка снятия
        self.assertIn("кнопка под извещением", words)          # кнопка обещана
        self.assertIn(sb.NODE_NAME, words)
        self.assertNotIn("ЖДЁМ ПРИБОР", words)                 # чужой ветки здесь нет
        self.assertTrue(got["signal_journal"].startswith("ASK "), got["signal_journal"])
        self.assertEqual([], got["placed"])
        self.assertTrue(got["armed"], "определённую остановку перестали запирать")
        self.assertTrue(got["hold_notices"], "кнопку обещали, а извещения нет")

    def test_a_DETERMINATE_stop_beside_a_blind_one_keeps_BOTH_words_and_the_ASK(self):
        """Одной настоящей развилки достаточно; слепая рядом своих слов не занимает."""
        blind = sig.signal_b([], TODAY, rows_ok=False)
        real = sig.held_signal({"mark": "aa11bb22cc33", "sig": sig.SIG_A, "count": 2,
                                "why": "две подряд недоказанные", "at": "2026-09-09T09:00:00Z"})
        line = sig.journal_line([blind, real], TODAY)
        self.assertTrue(line.startswith("ASK "), line)
        self.assertIn("ЖДЁМ ПРИБОР", line)                             # слепая — своими
        self.assertIn(sig.RELEASE_FORM % "aa11bb22cc33", line)         # определённая — прежними
        self.assertIn(sig.BY_OWNER, line)

    def test_all_four_are_listed_even_when_quiet(self):
        got = _tick()
        words = sig.all_words(got["signals"])
        self.assertEqual(len(sig.SIGNALS), len(words))
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


# ═══════════════ ЗАМОК ОСТАНОВКИ (05.09.2026, живой ущерб) ════════════════════


def _same_cause(*ids):
    """Закрытые ряды ящика с ОДНОЙ причиной провала — корпус сигнала Б.

    Форму причины набираем не от руки: код демона (`причина=<код>`) — первая
    ступень опознания :func:`shtab_box_signals.reason_class`, и живой корпус
    ходит именно по ней (70 упоминаний в логе демона).
    """
    return [_closed(i, "failed", "ошибка выполнения [причина=run_timeout · задача #%d]" % i)
            for i in ids]


def _proved_tail(*ids):
    """Два доказанных `done` в хвосте — чтобы говорил РОВНО сигнал Б, а не А заодно.

    Без них «две подряд недоказанные» сработали бы на тех же провалах, метки
    стало бы две, и проверка замка мерила бы сумму двух случаев вместо одного.
    """
    return [_closed(i, "done") for i in ids]


def _hold_tick(root, closed=(), node_text=None, place=True, reader=None, docs=None,
               ledger=None, budget=50, hold_notify_fn=None):
    """Оборот ящика на ЗАДАННОМ корне: замок живёт на диске, и два оборота обязаны
    смотреть в один каталог. Общий :func:`_tick` каждый раз заводит новый.
    """
    import datetime

    lister, doc_reader = _folder(docs)
    text = node_text if node_text is not None else HEAD_TEXT
    return run.tick(root=root, place=place, queue=FakeQueue(rows=[], closed=list(closed)),
                    budget=budget, reader=reader or _reader(text),
                    lister=lister, doc_reader=doc_reader,
                    ledger=ledger or _ledger((4, True, True), (5, True, True)),
                    hold_notify_fn=hold_notify_fn or _HoldDoor(),
                    clock=lambda tz: datetime.datetime(2026, 9, 2, 12, 0, tzinfo=tz))


class TestHold(unittest.TestCase):
    """ЗАМОК: объявление и запрет брать — одно решение, переживающее корпус.

    ПРЕДМЕТ ЗАМЕРЕН НА СВОЕЙ ПОЛОСЕ 05.09.2026, а не выдуман. Метка
    ``e0140bfc78dc`` (сигнал Б, ряды #1, #4, #6) объявлена в 12:58:21, ПОВТОРНО
    объявлена в 16:35:17 — той же меткой, — а между объявлениями ящик взял пять
    заданий (14:35 · 14:50 · 15:02 · 15:22 · 15:55) и одно после (16:45). Второе
    объявление той же метки и доказывает, что снятия не было: снятый сигнал в
    :func:`shtab_box_signals.active` не входит и объявиться второй раз не может.
    Причина исчезновения — корпус: номера очереди идут по кругу, ряды пропадают
    (:func:`shtab_box.merge_known`), а запрет пересчитывался по ним каждый виток.
    """

    def setUp(self):
        import tempfile

        self.root = tempfile.mkdtemp(prefix="shtabhold_")
        self.closed = _same_cause(1, 2, 3) + _proved_tail(4, 5)

    def _arm(self):
        """Первый оборот: сигнал Б сработал, ящик не взял ничего, замок заперт."""
        rep = _hold_tick(self.root, closed=self.closed)
        self.assertEqual([], rep["placed"], "остановка объявлена, а задание всё же взято")
        self.assertIn("третий раз одна причина", rep["stop"])
        self.assertEqual(1, len(rep["stop_marks"]), rep["stop_marks"])
        self.assertEqual(rep["stop_marks"], rep["armed"], "остановка объявлена, но не заперта")
        return rep["stop_marks"][0]

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ И ЕГО ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ ──────────────────────

    def test_NEGATIVE_the_stop_holds_when_the_corpus_that_raised_it_vanished(self):
        """ГЛАВНЫЙ ТЕСТ ПРАВКИ: ряды пропали — остановка осталась, взято НОЛЬ.

        Это дословный повтор живого случая: корпус, на котором стои́т сигнал Б,
        исчез (в бою — круг номеров очереди, здесь — пустые закрытые ряды), и
        прежний код в этот момент брал следующее задание МОЛЧА.
        """
        mark = self._arm()
        rep = _hold_tick(self.root, closed=[])
        self.assertEqual([], rep["placed"], "корпус пропал — и ящик снова берёт задания")
        self.assertIn(mark, rep["stop_marks"])
        self.assertIn("ДЕРЖИТСЯ ЗАМКОМ", rep["stop"])
        self.assertIn("снятия словом владельца не было", rep["stop"])
        self.assertEqual([], rep["armed"], "запертое запирается второй раз")

    def test_POSITIVE_the_very_same_second_tick_TAKES_when_nothing_was_latched(self):
        """Контроль: без замка тот же оборот БЕРЁТ — значит меряем замок, а не «ничего».

        Правило «никогда ничего не брать» прошло бы отрицательный тест идеально и
        стои́т ноль; пара обязательна.
        """
        import tempfile

        rep = _hold_tick(tempfile.mkdtemp(prefix="shtabhold_clean_"), closed=[])
        self.assertEqual(1, len(rep["placed"]), rep["why"])
        self.assertEqual("", rep["stop"])

    def test_the_owners_word_frees_it_and_the_box_goes_on(self):
        """Замок не вечен: слово владельца снимает случай, и ящик идёт дальше."""
        mark = self._arm()
        rep = _hold_tick(self.root, closed=[],
                         node_text=HEAD_TEXT + "\n" + (sig.RELEASE_FORM % mark))
        self.assertEqual([mark], rep["freed"])
        self.assertEqual(1, len(rep["placed"]), rep["why"])
        self.assertEqual("", rep["stop"])

    # ── ТРЕТИЙ ИСХОД: МОСТ МОЛЧИТ ────────────────────────────────────────────

    def test_a_remembered_release_survives_an_unreadable_node(self):
        """Владелец снял, шапка не читается — решение человека остаётся в силе.

        Прежде метки снятия жили ТОЛЬКО в шапке: не прочитали шапку → «меток
        нет» → снятая остановка возвращалась. Слово владельца из узла не
        исчезает, значит однажды увиденное снятие остаётся увиденным.
        """
        mark = self._arm()
        _hold_tick(self.root, closed=[], node_text=HEAD_TEXT + "\n" + (sig.RELEASE_FORM % mark))
        rep = _hold_tick(self.root, closed=[], reader=_dead_reader("мост не отдал узел"))
        self.assertEqual("", rep["stop"], "нечитаемая шапка вернула снятую остановку")
        self.assertNotIn(mark, rep["stop_marks"])

    def test_an_unreadable_node_says_UNKNOWN_and_asks_for_NO_mark(self):
        """Прямая проверка гипотезы задания: «мост молчит» ≠ «владелец не ответил».

        Ящик держится по-прежнему («не знаю» значит «стоп»), но просьбы положить
        метку — ту, которую владелец мог положить час назад, — больше нет.
        """
        self._arm()
        rep = _hold_tick(self.root, closed=self.closed,
                         reader=_dead_reader("мост не отдал узел"))
        self.assertEqual([], rep["placed"])
        self.assertIn("ПРОВЕРИТЬ НЕ УДАЛОСЬ", rep["stop"])
        self.assertIn("НЕИЗВЕСТНО", rep["stop"])
        self.assertNotIn("положите в узел", rep["stop"])
        self.assertIn("класть\nзаново НЕ НУЖНО".replace("\n", " "), rep["stop"])
        self.assertIn("ПРОВЕРИТЬ НЕ УДАЛОСЬ", rep["signal_journal"],
                      "журнал и владелец получили РАЗНЫЕ слова об одном случае")

    def test_an_undeterminate_stop_is_NOT_latched(self):
        """Остановка «не знаю» в замок не идёт — иначе моргание моста звало бы человека.

        Мост на этой полосе моргает измеримо: 05.09 — 08:18:32 «закрытые ряды не
        прочитаны» и 16:24:53 «очередь недоступна, предел ожидания 90с». Виток
        такая остановка держит целиком, но пережить его не смеет.
        """
        rep = run.tick(root=self.root, place=True,
                       queue=FakeQueue(rows=[], closed=[], closed_ok=False), budget=50,
                       reader=_reader(HEAD_TEXT), lister=_folder()[0],
                       doc_reader=_folder()[1], ledger=lambda root: ({}, True, ""),
                       clock=lambda tz: __import__("datetime").datetime(2026, 9, 2, 12, 0,
                                                                        tzinfo=tz))
        self.assertEqual([], rep["placed"])
        self.assertIn("ОПРЕДЕЛИТЬ НЕЛЬЗЯ", rep["stop"])
        self.assertEqual([], rep["armed"], "неопределимая остановка заперта навсегда")
        self.assertFalse(os.path.exists(run.hold_path(self.root)))

    # ── ЧТЕНИЕ ЗАМКА: ТРИ ИСХОДА, КАК У ВСЕХ ПРИБОРОВ ПОЛОСЫ ─────────────────

    def test_an_unreadable_latch_is_not_an_empty_latch(self):
        """Битая строка → «стои́т ли остановка, НЕИЗВЕСТНО» → не берём ничего."""
        with io.open(run.hold_path(self.root), "w", encoding="utf-8") as fh:
            fh.write("{это не json\n")
        rows, ok, why = run.read_hold(self.root)
        self.assertFalse(ok)
        self.assertEqual([], rows)
        self.assertIn("НЕИЗВЕСТНО", why)
        rep = _hold_tick(self.root, closed=[])
        self.assertEqual([], rep["placed"])
        self.assertIn("замок сигнальной остановки не прочитан", rep["why"])

    def test_a_missing_latch_file_is_an_honest_zero(self):
        rows, ok, why = run.read_hold(self.root)
        self.assertTrue(ok)
        self.assertEqual([], rows)
        self.assertIn("не заводился", why)

    def test_a_failed_latch_write_is_AUDIBLE_and_names_the_consequence(self):
        """Молчащий отказ записи неотличим от работающего замка — до взятия."""
        rep = _hold_tick(self.root, closed=self.closed)
        self.assertTrue(rep["armed"])
        broken = _hold_tick(self.root, closed=self.closed,
                            node_text=HEAD_TEXT)   # тот же случай, замок уже стои́т
        self.assertEqual([], broken["armed"])
        ok, why = run.write_hold(self.root, {"mark": ""})
        self.assertFalse(ok)
        self.assertIn("пустая метка", why)

    # ── ЧИСТЫЙ СЛОЙ ─────────────────────────────────────────────────────────

    def test_only_the_owners_word_and_only_a_determinate_signal_is_latchable(self):
        """Г показывает, В снимается сам, «не знаю» уходит с прибором."""
        four = sig.evaluate(closed=[], open_rows=[_card(9)], judged={}, day=TODAY,
                            left=0, budget=3, rows_ok=True, open_ok=True,
                            judged_ok=True, marks_ok=True)
        for one in four:
            if one["sig"] in (sig.SIG_C, sig.SIG_D):
                self.assertFalse(sig.holdable(one), one["sig"])
        blind = sig.signal_b([], TODAY, rows_ok=False)
        self.assertTrue(blind["on"])
        self.assertFalse(sig.holdable(blind), "«не знаю» просится в замок")

    def test_the_latch_index_takes_the_LATER_line(self):
        """Снятие ложится отдельной строкой поверх постановки; слова случая остаются."""
        idx = sig.hold_index([{"mark": "aa11", "sig": "Б", "why": "случай", "at": "T1"},
                              {"mark": "aa11", "released": True, "at": "T2"}])
        self.assertTrue(idx["aa11"]["released"])
        self.assertEqual("случай", idx["aa11"]["why"])
        self.assertEqual({"aa11"}, sig.hold_release([{"mark": "aa11", "released": True}], ()))

    def test_the_release_door_is_ONE_and_takes_both_sources(self):
        """Метки шапки и метки памяти сливаются в одном месте — два разошлись бы молча."""
        got = sig.hold_release([{"mark": "old1", "released": True},
                                {"mark": "old2", "released": False}], ("new1",))
        self.assertEqual({"old1", "new1"}, got)


class TestStopIsHeardAndAnswered(unittest.TestCase):
    """ОСТАНОВКА ВИДНА ВЛАДЕЛЬЦУ И СНИМАЕТСЯ ОТВЕТОМ В TELEGRAM (задание 06.09.2026).

    ПРЕМИСА, ПЕРЕМЕРЕННАЯ ПО КОДУ, А НЕ ПРИНЯТАЯ НА СЛОВО. До этой правки поднятая
    остановка не выходила за полосу ни одной веткой: `stop_words` уезжала в лог демона,
    в витрину и строкой `ASK` в журнал, а единственная дверь наружу
    (`shtab_box_run.notify_taken`) зовётся ТОЛЬКО внутри ветки `placed` — то есть на
    ВЗЯТИИ. Остановленный ящик не берёт ничего по построению, значит на остановке эта
    дверь не открывалась НИКОГДА. Снять её можно было ровно одним способом — строкой
    `[[ЯЩИК СНЯТЬ метка=…]]` в узле мозга, то есть не с телефона. Премиса подтвердилась.

    ЖИВОЙ ПУТЬ ЗДЕСЬ НЕ ТРОГАЕТСЯ НИ ОДНОЙ ВЕТКОЙ: дверь всегда заглушка `_HoldDoor`,
    и из набора наружу не уходит ничего (замок задания «пробных извещений не слать»).
    """

    def setUp(self):
        import tempfile

        self.root = tempfile.mkdtemp(prefix="shtabtell_")
        # Один случай: сигнал Б («третий раз одна причина»), хвост доказан — иначе
        # заодно говорил бы А, и меряли бы сумму двух остановок вместо одной.
        self.closed = _same_cause(1, 2, 3) + _proved_tail(4, 5)

    def _arm(self, door=None, closed=None):
        door = door or _HoldDoor()
        rep = _hold_tick(self.root, closed=self.closed if closed is None else closed,
                         hold_notify_fn=door)
        return rep, door

    # ── ИЗВЕЩЕНИЕ ────────────────────────────────────────────────────────────

    def test_a_raised_stop_reaches_the_owner_at_all(self):
        """Главный факт задания: остановка перестала быть невидимой."""
        rep, door = self._arm()
        self.assertEqual([], rep["placed"], "остановка объявлена, а задание всё же взято")
        self.assertEqual(1, len(door.sent), "остановка снова никому не сказана")
        self.assertEqual(1, len(rep["hold_told"]))
        self.assertEqual([], rep["hold_notice_failed"])

    def test_the_notice_names_the_signal_the_rows_and_how_to_release(self):
        """ТРИ ЧАСТИ ПО ЗАДАНИЮ: какой сигнал · какие ряды подняли · чем снимается."""
        _rep, door = self._arm()
        text, markup = door.sent[0]
        self.assertIn("сигнал", text.lower())
        self.assertIn("Б", text)                                  # ЧТО сработало
        self.assertIn("третий раз одна причина", text)
        for row in ("#1", "#2", "#3"):                            # КАКИЕ РЯДЫ подняли
            self.assertIn(row, text, "ряды случая в извещении не названы")
        self.assertIn("ящик снять", text)                         # ЧЕМ снимается — словом
        self.assertIn("PC-дев", text)                             # и ГДЕ это слово говорить
        self.assertTrue(markup and markup.get("inline_keyboard"), "карточка приехала без кнопки")

    def test_the_button_carries_the_mark_of_THIS_case_and_fits_telegram(self):
        _rep, door = self._arm()
        _text, markup = door.sent[0]
        data = markup["inline_keyboard"][0][0]["callback_data"]
        self.assertTrue(data.startswith("box:free:"), data)
        self.assertLessEqual(len(data.encode("utf-8")), 64, "callback_data не влезает в Telegram")
        self.assertIn(data.split(":")[-1], _rep["stop_marks"], "кнопка зовёт снять чужой случай")

    def test_the_notice_words_are_the_SAME_as_the_journal_words(self):
        """Владелец читает про один случай в двух местах, и разъехаться они не смеют:
        и фраза остановки, и карточка печатают «чем снимается» ОДНОЙ функцией."""
        rep, door = self._arm()
        way = sig.telegram_way(rep["stop_marks"][0])
        self.assertIn(way, door.sent[0][0])
        self.assertIn(way, rep["stop"])

    # ── ДЕДУП (пункт 5 задания) ──────────────────────────────────────────────

    def test_ONE_stop_is_told_ONCE_no_matter_how_many_ticks_pass(self):
        """Виток ящика — 30 минут, остановка живёт до слова владельца. Доклад ПО ФАКТУ
        дал бы под полсотни карточек за ночь; докладываем СЛУЧАЙ, и ровно один раз."""
        _rep, door = self._arm()
        for _ in range(3):
            again = _hold_tick(self.root, closed=self.closed, hold_notify_fn=door)
            self.assertEqual([], again["placed"])
            self.assertTrue(again["stop"], "остановка исчезла между витками")
        self.assertEqual(1, len(door.sent), "об одной остановке сказали %d раз" % len(door.sent))

    def test_the_dedup_survives_a_vanished_corpus_because_it_lives_in_the_latch(self):
        """Второго реестра «кому сказали» нет: признак лежит в файле замка, рядом с
        самой остановкой. Поэтому пропавший корпус (живой класс 05.09 — номера очереди
        идут по кругу) не рождает второй карточки о том же случае."""
        _rep, door = self._arm()
        gone = _hold_tick(self.root, closed=[], hold_notify_fn=door)
        self.assertIn("ДЕРЖИТСЯ ЗАМКОМ", gone["stop"])
        self.assertEqual(1, len(door.sent))

    def test_a_DIFFERENT_case_is_told_on_its_own(self):
        """Дедуп по случаю, а не по факту остановки: новая улика — новая новость.
        Молчание про вторую поломку было бы той же молчащей остановкой."""
        _rep, door = self._arm()
        other = _hold_tick(self.root, closed=_same_cause(7, 8, 9) + _proved_tail(4, 5),
                           hold_notify_fn=door)
        self.assertTrue(other["stop_marks"])
        self.assertEqual(2, len(door.sent), "о втором случае не сказали")

    # ── СУХОЙ ХОД И ОТКАЗ ДОСТАВКИ ───────────────────────────────────────────

    def test_a_dry_run_shows_the_card_and_sends_NOTHING(self):
        """Запрет задания дословно: «из проверки не уходит НИЧЕГО». Дорога увидеть
        будущую карточку целиком, ничего не послав, обязана быть."""
        door = _HoldDoor()
        rep = _hold_tick(self.root, closed=self.closed, place=False, hold_notify_fn=door)
        self.assertEqual([], door.sent, "сухой ход послал живое сообщение")
        self.assertEqual(1, len(rep["hold_notices"]))
        self.assertTrue(rep["hold_notices"][0]["dry"])
        self.assertIn("ОСТАНОВКЕ", run._render(report=rep))
        self.assertFalse(os.path.exists(run.hold_path(self.root)),
                         "сухой ход написал на диск")

    def test_a_failed_delivery_is_AUDIBLE_and_the_next_tick_says_it_AGAIN(self):
        """«Сказали» ложится на диск ПОСЛЕ доставки: не ушло — скажем следующим витком.
        Записывай мы вперёд — сорвавшаяся отправка стала бы молчанием навсегда, а мы
        чиним ровно молчание."""
        dead = _HoldDoor(ok=False, why="бота нет в инбоксе")
        rep = _hold_tick(self.root, closed=self.closed, hold_notify_fn=dead)
        self.assertEqual(1, len(rep["hold_notice_failed"]))
        self.assertEqual([], rep["hold_told"])
        self.assertIn("ИЗВЕЩЕНИЕ ОБ ОСТАНОВКЕ НЕ УШЛО", rep["why"])
        self.assertIn("бота нет в инбоксе", rep["why"])
        self.assertTrue(rep["armed"], "неушедшее извещение отменило запирание остановки")
        alive = _HoldDoor()
        again = _hold_tick(self.root, closed=self.closed, hold_notify_fn=alive)
        self.assertEqual(1, len(alive.sent), "о молчащей остановке так и не сказали")
        self.assertEqual(1, len(again["hold_told"]))

    def test_an_exploding_door_does_not_take_the_tick_down(self):
        """Дверь наружу роняет виток ящика ни одной веткой: замок дороже новости."""
        rep = _hold_tick(self.root, closed=self.closed,
                         hold_notify_fn=_HoldDoor(boom="сокет закрыт"))
        self.assertTrue(rep["armed"])
        self.assertEqual(1, len(rep["hold_notice_failed"]))
        self.assertIn("сокет закрыт", rep["hold_notice_failed"][0]["why"])

    # ── СНЯТИЕ ОТВЕТОМ (пункты 2–4 задания) ──────────────────────────────────

    def test_the_right_answer_frees_THIS_case_and_the_box_goes_on(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ контрфакта: верное снятие проходит и ящик едет."""
        rep, _door = self._arm()
        mark = rep["stop_marks"][0]
        ok, words = run.free_mark(mark, root=self.root, by="Telegram")
        self.assertTrue(ok, words)
        self.assertIn("Остановка снята", words)
        self.assertIn(mark, words)
        after = _hold_tick(self.root, closed=[])
        self.assertEqual("", after["stop"], "снятая остановка держит ящик")
        self.assertEqual(1, len(after["placed"]), after["why"])

    def test_a_WRONG_mark_frees_NOTHING(self):
        """КОНТРФАКТ ЗАДАНИЯ: снятие по неверной метке не проходит. Закрыто МЕСТОМ —
        снимаются только случаи, которые ящик сам записал в замок."""
        rep, _door = self._arm()
        ok, words = run.free_mark("deadbeef", root=self.root, by="Telegram")
        self.assertFalse(ok)
        self.assertIn("НЕТ", words)
        self.assertIn(rep["stop_marks"][0], words, "не показали, какие метки открыты")
        after = _hold_tick(self.root, closed=self.closed)
        self.assertEqual([], after["placed"], "неверная метка всё же отпустила ящик")
        self.assertTrue(after["stop"])

    def test_the_release_frees_EXACTLY_that_case_and_not_the_other(self):
        """«Снимает ИМЕННО этот случай» — обещание фразы остановки. Две поднятые
        остановки, снята одна: вторая обязана держать ящик дальше."""
        rep = _hold_tick(self.root, closed=_same_cause(1, 2, 3), hold_notify_fn=_HoldDoor())
        marks = sorted(rep["stop_marks"])
        self.assertEqual(2, len(marks), "нужен корпус, поднимающий ДВА сигнала: %s" % marks)
        ok, _words = run.free_mark(marks[0], root=self.root)
        self.assertTrue(ok)
        after = _hold_tick(self.root, closed=[])
        self.assertEqual([], after["placed"], "снятие одного случая отпустило оба")
        self.assertIn(marks[1], after["stop_marks"])
        self.assertNotIn(marks[0], after["stop_marks"])

    def test_a_second_tap_on_the_same_card_is_not_an_error(self):
        """Повторный тап по той же карточке — обычное дело у человека, и пугать его
        отказом за это нельзя. Но и «снято» второй раз говорить не о чем."""
        rep, _door = self._arm()
        mark = rep["stop_marks"][0]
        run.free_mark(mark, root=self.root)
        ok, words = run.free_mark(mark, root=self.root)
        self.assertTrue(ok)
        self.assertIn("уже снята", words)

    def test_an_OLD_latch_record_says_where_the_rows_are_instead_of_denying_them(self):
        """ЗАПИСИ СТАРШЕ 06.09 ПОЛЯ УЛИК НЕ ИМЕЮТ ВОВСЕ, и живая запись полосы
        `ddac4cc3b98a` (сигнал А, заперта 06.09 11:45Z, ряды #101 и #102) именно такая.
        Сказать о ней «рядов нет» значило бы соврать: ряды есть, их нет В ЗАПИСИ.
        """
        old = {"mark": "ddac4cc3b98a", "sig": "А", "at": "2026-09-06T11:45:39Z",
               "why": "2 последних НАШИХ закрытых задания ящика подряд без вердикта "
                      "«сделано»: #101 — закрыта как failed, #102 — закрыта как failed"}
        text = sig.hold_notice(old)
        self.assertNotIn("Рядов у случая нет", text)
        self.assertIn("названы в причине", text)
        self.assertIn("#101", text)                       # и они там действительно есть
        self.assertIn("ящик снять ddac4cc3b98a", text)    # снять её всё равно есть чем

    def test_a_released_case_is_never_told_about(self):
        """Снятая остановка новостью не является: карточка о ней звала бы снимать
        снятое. Проверяем чистый слой — он же и решает."""
        self.assertEqual([], sig.untold([{"mark": "aa11", "sig": "Б", "why": "случай"},
                                         {"mark": "aa11", "released": True}]))
        self.assertEqual(["aa11"], [r["mark"] for r in
                                    sig.untold([{"mark": "aa11", "sig": "Б", "why": "случай"}])])

    def test_an_unreadable_latch_refuses_the_release_instead_of_guessing(self):
        """Дописать снятие в файл, который не прочитан, значит снять НЕИЗВЕСТНО ЧТО."""
        with io.open(run.hold_path(self.root), "w", encoding="utf-8") as fh:
            fh.write("{это не json\n")
        ok, words = run.free_mark("aa11bb22", root=self.root)
        self.assertFalse(ok)
        self.assertIn("не прочитан", words)

    def test_an_empty_mark_frees_nothing(self):
        ok, words = run.free_mark("", root=self.root)
        self.assertFalse(ok)
        self.assertIn("не названа", words)

    def test_the_release_leaves_a_trace_of_WHO_answered(self):
        """След нужен человеку, читающему замок глазами: дорог снятия стало две."""
        rep, _door = self._arm()
        mark = rep["stop_marks"][0]
        run.free_mark(mark, root=self.root, by="Telegram")
        rows, ok, _why = run.read_hold(self.root)
        self.assertTrue(ok)
        freed = [r for r in rows if r.get("mark") == mark and r.get("released")]
        self.assertEqual(1, len(freed))
        self.assertEqual("Telegram", freed[0].get("by"))

    # ── ЧИСТЫЙ СЛОЙ: ЗАПИСЬ ДОКЛАДА НЕ ТРОГАЕТ САМ СЛУЧАЙ ────────────────────

    def test_the_told_line_does_not_overwrite_the_moment_of_the_case(self):
        """Чтение схлопывает строки одной метки поздней поверх ранней: положи мы в
        строку доклада поле `at` — оно затёрло бы МОМЕНТ СЛУЧАЯ, то самое число,
        которым запомненный сигнал объясняет человеку, когда его видели живьём."""
        idx = sig.hold_index([{"mark": "aa11", "sig": "Б", "why": "случай", "at": "T1"},
                              sig.hold_told("aa11", now="T2")])
        self.assertEqual("T1", idx["aa11"]["at"])
        self.assertEqual("T2", idx["aa11"]["told_at"])
        self.assertTrue(idx["aa11"]["told"])


def _cp1251_console():
    """Поток, ПРИТВОРЯЮЩИЙСЯ трубой на Windows. → TextIOWrapper поверх байтов.

    Ровно то, что получает дочерний процесс, когда вместо консоли ему дали трубу:
    кодовая страница системы (cp1251) и политика ошибки ``strict``. Настоящей
    консоли в наборе нет и быть не должно — фикстура повторяет её КОДИРОВКОЙ.
    """
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1251", errors="strict",
                            newline="", write_through=True)


class _LockedCp1251(io.TextIOWrapper):
    """Поток, который УЙТИ с cp1251 не может. → фикстура ВТОРОГО слоя канала.

    Не выдумка: так ведёт себя всякий приёмник, чью кодировку задал не мы. Слой
    UTF-8 на нём не берётся, и вся тяжесть ложится на политику ошибки — ровно ту
    ветку, где решение Штаба №2 (подстановка объявляет себя) и работает.
    """

    def reconfigure(self, **kw):
        if "encoding" in kw:
            raise ValueError("кодировка потока прибита снаружи")
        return super().reconfigure(**kw)


def _locked_cp1251():
    return _LockedCp1251(io.BytesIO(), encoding="cp1251", errors="strict",
                         newline="", write_through=True)


def _seen(stream, enc="cp1251"):
    """Что легло в поток → str. Кодировку называет вызывающий: в том, КАКОЙ она
    оказалась после починки канала, и состои́т половина замера."""
    stream.flush()
    return stream.buffer.getvalue().decode(enc, "replace")


class TestConsoleChannelCp1251(unittest.TestCase):
    """КАНАЛ ВЫВОДА КОМАНДНОЙ СТРОКИ ЯЩИКА НА cp1251-КОНСОЛИ (задание 11.09.2026).

    ЖИВОЙ ПОВОД С ЧИСЛАМИ. 10.09 в 17:37:47Z снятие остановки ``db2bbe4aa216``
    ПРОШЛО и легло строкой замка (``by="Telegram"``), а печать исхода упала
    ``UnicodeEncodeError`` на ``✅`` (U+2705): труба дочернего процесса кодируется
    страницей системы (cp1251), знака в ней нет. Владелец получил ТРЕЙСБЕК ВМЕСТО
    СЛОВА «СНЯТО» — последняя дверь снятия сообщила обратное истине.

    ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЗДЕСЬ КОНТРФАКТОМ, а не наблюдением: на одном и том же
    потоке-притворщике ПРЕЖНИЙ канал обязан упасть, а нынешний — напечатать и
    отдать ТОТ ЖЕ код возврата. Без падающей половины зелёный тест не значил бы
    ничего: он был бы зелёным и до правки.
    """

    def setUp(self):
        import tempfile

        self.root = tempfile.mkdtemp(prefix="shtabcon_")
        self.closed = _same_cause(1, 2, 3) + _proved_tail(4, 5)
        del run._CONSOLE_LOST[:]          # счётчик канала — общий на процесс
        self._save = (sys.stdout, sys.stderr, run.HERE)

    def tearDown(self):
        sys.stdout, sys.stderr, run.HERE = self._save
        del run._CONSOLE_LOST[:]

    def _armed(self):
        """Поднять остановку во ВРЕМЕННОМ корне → метка. Боевого замка не касаемся."""
        rep = _hold_tick(self.root, closed=self.closed, hold_notify_fn=_HoldDoor())
        self.assertTrue(rep["stop_marks"], rep["why"])
        return rep["stop_marks"][0]

    def _cli(self, argv, locked=False):
        """Прогон командной строки на притворщике → (код, что напечатано).

        ``locked`` выбирает СЛОЙ канала: обычная труба уходит в UTF-8 (слой 1,
        живая дорога кнопки), прибитая остаётся в cp1251 и проверяет объявляющую
        себя подстановку (слой 2).
        """
        out, err = (_locked_cp1251(), _locked_cp1251()) if locked \
            else (_cp1251_console(), _cp1251_console())
        sys.stdout, sys.stderr = out, err
        run.HERE = self.root
        try:
            code = run.main(argv)
        finally:
            sys.stdout, sys.stderr = self._save[0], self._save[1]
        enc = "cp1251" if locked else "utf-8"
        return code, _seen(out, enc) + _seen(err, enc)

    # ── КОНТРФАКТ: ПРЕЖНИЙ КАНАЛ ПРОТИВ НЫНЕШНЕГО ────────────────────────────

    def test_the_OLD_channel_DIES_on_a_cp1251_pipe_where_the_NEW_one_SPEAKS(self):
        """ДВЕ ПОЛОВИНЫ ОДНОГО ЗАМЕРА, обе на одном тексте и одной кодировке.

        Прогон А — дословно прежний код: ``print(words)`` в непочиненный поток. Он
        ОБЯЗАН упасть; зелень здесь означала бы, что фикстура не повторяет трубу
        владельца, и весь тест не стои́т ничего.

        Прогон Б — нынешняя командная строка целиком, от разбора аргументов до
        кода возврата.
        """
        import tempfile

        # Слова прогона А снимаются на ОТДЕЛЬНОМ корне: снимая метку своего корня,
        # тест сам сделал бы её «уже снятой» и мерил бы дальше не тот текст.
        other = tempfile.mkdtemp(prefix="shtabcon_a_")
        rep = _hold_tick(other, closed=self.closed, hold_notify_fn=_HoldDoor())
        _ok, words = run.free_mark(rep["stop_marks"][0], root=other, by="Telegram")
        self.assertIn("✅", words, "решение №1 нарушено: значок выкинули из текста")
        mark = self._armed()

        old = _cp1251_console()                      # ПРОГОН А — как было до правки
        with self.assertRaises(UnicodeEncodeError) as boom:
            print(words, file=old)
        self.assertEqual("✅", boom.exception.object[boom.exception.start])
        self.assertEqual("", _seen(old), "упавшая печать всё же что-то сказала")

        code, said = self._cli(["--free", mark])     # ПРОГОН Б — как стало
        self.assertEqual(0, code)
        self.assertIn("Остановка снята", said, "новый канал потерял само сообщение")
        self.assertIn("✅", said, "значок доехал не целым — а он в utf-8 обязан быть целым")
        self.assertIn(mark, said)

    def test_the_channel_speaks_UTF8_because_that_is_what_the_LIVE_reader_decodes(self):
        """ЖИВАЯ ДОРОГА ЭТОЙ КОМАНДЫ — НЕ КОНСОЛЬ, А ТРУБА К `pc_agent._box_cli`,
        и читает он её ``encoding="utf-8"``. Оставь мы вывод в cp1251 — трейсбек
        сменился бы мохибейком на всей кириллице разом, то есть владелец опять не
        прочитал бы слова «снято». Проверяем ровно теми kwargs, какими читает агент.
        """
        mark = self._armed()
        out = _cp1251_console()
        sys.stdout, sys.stderr = out, _cp1251_console()
        run.HERE = self.root
        try:
            self.assertEqual(0, run.main(["--free", mark, "--by", "Telegram"]))
        finally:
            sys.stdout, sys.stderr = self._save[0], self._save[1]
        self.assertEqual("utf-8", out.encoding, "канал не ушёл в utf-8 — агент прочтёт мусор")
        out.flush()
        as_agent_reads = out.buffer.getvalue().decode("utf-8", "replace")
        self.assertIn("✅ Остановка снята", as_agent_reads)
        self.assertNotIn("�", as_agent_reads, "в чтении агента появился мохибейк")

    def test_the_module_uses_the_LANE_device_and_does_not_grow_a_second_one(self):
        """Шов `io_utf8.force_utf8` заведён 30.07.2026 на ЭТОТ же класс. Свой
        переключатель кодировки здесь разошёлся бы с ним молча (класс 539), поэтому
        зовётся ЛАНЕВОЕ устройство, а своего в модуле нет ни одной строкой."""
        src = _src("shtab_box_run.py")
        self.assertIn("io_utf8", src, "ящик снова чинит вывод сам по себе")
        self.assertIn("force_utf8()", src)
        self.assertNotIn('reconfigure(encoding=', src,
                         "заведён второй переключатель кодировки мимо io_utf8")

    def test_the_substitution_ANNOUNCES_itself_and_never_silently_edits_the_text(self):
        """Решение Штаба №2, ВТОРОЙ СЛОЙ КАНАЛА: поток, который уйти с cp1251 не смог.
        Штатный ``replace`` поставил бы ``?`` и промолчал — тот же класс, что
        молчаливая обрезка. Знак назван кодовой точкой ДВАЖДЫ: вставкой на месте (её
        видит читающий строку) и строкой-замечанием (её видит смотрящий на хвост)."""
        new = _locked_cp1251()
        self.assertEqual(1, run.console_ready(new))
        self.assertEqual("cp1251", new.encoding, "фикстура обязана остаться прибитой")
        run.say("итог: ✅ снято", stream=new)
        said = _seen(new)
        self.assertIn("[U+2705]", said, "подстановка не назвала знак")
        self.assertNotIn("итог: ? снято", said, "канал молча подменил текст на «?»")
        self.assertIn("ЗАМЕЧАНИЕ КАНАЛА", said, "о подстановке не сказано отдельной строкой")
        self.assertIn("U+2705", run.console_note())
        self.assertIn("итог: ", said)
        self.assertIn(" снято", said, "текст сообщения пострадал сверх одного знака")

    def test_a_LOCKED_stream_still_gets_the_release_words_and_the_same_code(self):
        """Худшая труба полосы: с cp1251 не уходит. Сообщение обязано доехать всё
        равно — со знаком, названным кодовой точкой, и с тем же кодом возврата."""
        mark = self._armed()
        code, said = self._cli(["--free", mark], locked=True)
        self.assertEqual(0, code)
        self.assertIn("Остановка снята", said)
        self.assertIn("[U+2705]", said)

    def test_a_plain_print_of_the_module_is_fixed_TOO_without_touching_that_print(self):
        """«Один раз на весь модуль, а не каждую печать по отдельности»: чинится САМ
        ПОТОК, поэтому обычный ``print`` — в том числе чужой, из ``argparse`` или из
        :mod:`shtab_box_signals` — едет по тому же каналу, и править его не нужно ни
        одной строкой."""
        new = _locked_cp1251()
        run.console_ready(new)
        print(sig.RELEASE_BTN, file=new)             # 🔓 U+1F513, чужой модуль
        print(sig.STOP_NOTICE_HEAD, file=new)        # ⛔ U+26D4, чужой модуль
        said = _seen(new)
        self.assertIn("[U+1F513]", said)
        self.assertIn("[U+26D4]", said)
        self.assertIn("Снять остановку", said)

    def test_every_printable_sign_of_the_box_survives_the_channel(self):
        """ОБЩИЙ ЗАМОК, А НЕ ПЕРЕЧЕНЬ ЗНАКОВ. Перечень протух бы на первом новом
        значке — ровно тот дефект, которым живёт весь этот разбор. Берём ВСЕ
        строковые литералы трёх модулей ящика и прогоняем через ХУДШИЙ поток."""
        new = _locked_cp1251()
        run.console_ready(new)
        seen = 0
        for name in ("shtab_box_run.py", "shtab_box.py", "shtab_box_signals.py"):
            for node in ast.walk(ast.parse(_src(name))):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                try:
                    node.value.encode("cp1251")
                except UnicodeEncodeError:
                    seen += 1
                    print(node.value, file=new)      # упадёт — тест красный
        self.assertGreaterEqual(seen, 20, "фикстура перестала находить незаписываемые знаки")
        self.assertIn("[U+", _seen(new))

    # ── КОД ВОЗВРАТА СНЯТИЯ (пункт 4 задания) ────────────────────────────────

    def test_the_release_return_code_did_NOT_move_success_is_still_zero(self):
        """Успех по-прежнему НУЛЬ — и теперь этот нуль доезжает до владельца вместе
        со словом «снято», а не вместо него."""
        mark = self._armed()
        code, said = self._cli(["--free", mark])
        self.assertEqual(0, code)
        self.assertIn("Остановка снята", said)
        rows, ok, _why = run.read_hold(self.root)
        self.assertTrue(ok)
        self.assertTrue([r for r in rows if r.get("mark") == mark and r.get("released")],
                        "снятие не легло на диск")

    def test_the_refusal_return_code_did_NOT_move_either_it_is_still_one(self):
        """Отказ по-прежнему ЕДИНИЦА, и говорит о снятии, а не о печати."""
        self._armed()
        code, said = self._cli(["--free", "deadbeef"])
        self.assertEqual(1, code)
        self.assertIn("НЕТ", said)
        self.assertIn("⚠", said, "значок отказа пропал из вывода")
        locked_code, locked_said = self._cli(["--free", "deadbeef"], locked=True)
        self.assertEqual(1, locked_code, "на худшей трубе код отказа поехал")
        self.assertIn("[U+26A0]", locked_said, "на худшей трубе знак не назвал себя")

    def test_a_DEAD_stream_no_longer_turns_a_DONE_release_into_a_failure(self):
        """Решение Штаба №3, худший случай: канал починить не удалось вовсе.
        Снятие УЖЕ на диске — значит код возврата обязан говорить о нём. Молчание
        честнее трейсбека поверх удавшегося действия."""
        mark = self._armed()

        class _Dead(object):
            encoding = "cp1251"

            def write(self, _s):
                raise OSError("труба закрыта")

            def flush(self):
                raise OSError("труба закрыта")

        sys.stdout, sys.stderr = _Dead(), _Dead()
        run.HERE = self.root
        try:
            self.assertEqual(0, run.main(["--free", mark]),
                             "мёртвый вывод снова объявил удавшееся снятие провалом")
        finally:
            sys.stdout, sys.stderr = self._save[0], self._save[1]
        rows, ok, _why = run.read_hold(self.root)
        self.assertTrue(ok)
        self.assertTrue([r for r in rows if r.get("mark") == mark and r.get("released")])

    def test_console_ready_never_takes_the_run_down_by_itself(self):
        """Починка канала не смеет уронить ход: поток без ``reconfigure`` и поток,
        который на ней взрывается, обязаны просто не считаться."""
        class _NoRec(object):
            pass

        class _Boom(object):
            def reconfigure(self, **_kw):
                raise ValueError("нельзя")

        self.assertEqual(0, run.console_ready(_NoRec(), _Boom()))


class TestLedgerAskedByName(unittest.TestCase):
    """ЧИТАТЕЛЬ РЕЕСТРА СПРАШИВАЕТ ИМЕНЕМ (хвост задания 32-a, закрыт 11.09.2026).

    Ключом реестра судьи стало ИМЯ закрытого ряда («ключ маркера плюс номер»,
    коммит ff8b149), а сигнальный слой продолжал спрашивать ГОЛЫМ НОМЕРОМ. Отказ
    выходил безопасным по направлению — «судья не судил» не сильнее незнания, — но
    НЕВЕРНЫМ: семь перенесённых миграцией записей слой не находил, и доказанная
    задача читалась как недоказанная. Сигнал А на такой паре останавливает ящик,
    то есть цена ошибки — вставший ящик при исправной полосе.

    Имя поднимает ЕДИНСТВЕННАЯ ДВЕРЬ :func:`contour_digest.row_name` — здесь она же
    и в наборе, чтобы фикстура не развела с продуктом второй экземпляр правила.
    """

    def _row(self, tid, key=None, status="done"):
        """Ряд ящика в той форме, в какой его отдаёт :func:`shtab_box_signals.box_rows`."""
        return sig.box_rows([_closed(tid, status, "сдано", key=key)])[0]

    def test_a_verdict_lying_under_the_name_is_found(self):
        """ОТРИЦАТЕЛЬНЫЙ: запись под ИМЕНЕМ обязана находиться.

        Падает на коде до правки: `judged_of` звался без цели, имя не собиралось,
        и вердикт «доказана» читался как «судья не судил».
        """
        row = self._row(11)
        name = cd.row_name(11, _closed(11)["task_text"])
        self.assertEqual("k011#11", name, "имя собралось не тем правилом")
        ledger = {name: {dj.F_PROVED: True, dj.F_ADDRESSED: True, dj.F_REASON: "проба"}}
        ok, words = sig.proved(row, ledger)
        self.assertTrue(ok, "запись под именем %r не найдена: %s" % (name, words))
        self.assertEqual(cd.JUDGE_PROVED, words)

    def test_the_signal_reader_carries_the_row_text_for_the_name(self):
        """Имя собирается ИЗ САМОГО РЯДА: `box_rows` обязан донести его первую строку."""
        row = self._row(11)
        self.assertIn("goal", row, "ряд ящика не несёт своей первой строки — имя собирать не из чего")
        self.assertEqual("k011#11", cd.row_name(row["id"], row.get("goal")))

    # ── КОНТРОЛЬ ОБРАТНОЙ СТОРОНЫ ────────────────────────────────────────
    def test_a_row_without_a_marker_is_still_found_by_the_number(self):
        """Маркера нет — имени нет, и ключом остаётся НОМЕР: прежнее поведение цело.

        Контроль против «починили одно, сломали другое»: правка, заменившая номер
        именем БЕЗУСЛОВНО, потеряла бы записи безмаркерных рядов молча.
        """
        bare = {"id": 77, "status": "done", "result": "сдано"}
        self.assertEqual("77", cd.row_name(77, bare.get("goal")))
        ledger = {"77": {dj.F_PROVED: True, dj.F_ADDRESSED: True, dj.F_REASON: "проба"}}
        ok, words = sig.proved(bare, ledger)
        self.assertTrue(ok, "безмаркерный ряд потерял свой вердикт: %s" % words)

    def test_a_row_with_no_record_at_all_still_says_the_judge_was_silent(self):
        """Записи нет вовсе → «судья не судил». Правка не смеет родить вердикт из пустоты."""
        row = self._row(11)
        for empty in ({}, None, {"посторонний#1": {dj.F_PROVED: True}}):
            ok, words = sig.proved(row, empty)
            self.assertFalse(ok, "вердикт выдуман на реестре %r" % (empty,))
            self.assertIn(cd.JUDGE_SILENT, words)

    def test_the_old_number_key_no_longer_answers_for_a_marked_row(self):
        """Окно перехода названо вслух: у ряда С МАРКЕРОМ номерной ключ больше не отвечает.

        Это не потеря, а следствие ff8b149 (номер переиспользуется, именем не является)
        и ровно то, что закрыла миграция реестра. Тест держит границу названной.
        """
        ok, words = sig.proved(self._row(11), {"11": {dj.F_PROVED: True, dj.F_ADDRESSED: True}})
        self.assertFalse(ok, "номерной ключ ответил за именованный ряд")
        self.assertIn(cd.JUDGE_SILENT, words)


# ═══════════════ 63-c: причина сигнала Б — знаки на своих местах ═══════════════

# Формы набраны по живым сборщикам: шапка закрытия — строки с постоянными метками
# (`close_msg_pc.prepend`, отделена пустой строкой), маркер судьи — первой строкой своей
# части (`done_judge_pc.line`), итог провала — `pc_orchestrator.fail_result` (черновик
# одной строкой ПЕРЕД головой демона, `[причина=` в нём обезврежен в `(причина=`).
_HEADER_63C = "СПРАШИВАЛИ: разбор класса\nВЫШЛО: вердикт не назван\nДАЛЬШЕ: стоп\n\n"
_Q_CODE = "[причина=exec_error · ошибка выполнения]: claude exit=1: цитата"
_Q_SCRUBBED = "(причина=exec_error · ошибка выполнения]: claude exit=1: цитата"


def _corpus_63c():
    """Сплошной корпус: шапка × место маркера × цитаты в докладе × часть демона."""
    heads = ("", _HEADER_63C, "🔁 самопочинка: причина: повтор\n")
    judge = ("", dj.UNKNOWN_PREFIX + " — адрес пуст\n", dj.UNPROVEN_PREFIX + " — 2 файла\n",
             "⏱ " + dj.UNKNOWN_PREFIX + " — цитата не в начале строки\n")
    reports = ("", "доклад без цитат\nRESULT: ок",
               "доклад цитирует «%s»\nRESULT: ок" % dj.UNKNOWN_PREFIX,
               "доклад цитирует\n%s — строка доклада\nRESULT: ок" % dj.UNPROVEN_PREFIX,
               "доклад цитирует %s\nRESULT: ок" % _Q_CODE)
    daemon = ("", "⏱ 🧾 ЧЕРНОВИК: %s провал [причина=run_timeout · таймаут прогона]: 2700s. "
                  "Окно работы неизвестно." % _Q_SCRUBBED,
              "провал [причина=exec_error · ошибка выполнения]: claude exit=1: вывод\n%s\n"
              "Следов работы в окне 17.09 01:00–01:05 UTC нет (коммитов 0, записей журнала 0)."
              % dj.UNKNOWN_PREFIX)
    for h in heads:
        for j in judge:
            for r in reports:
                for d in daemon:
                    yield h + j + r + ("\n" + d if d else "")


class TestReasonClassReadsMarksInPlace63c(unittest.TestCase):
    """Вторая копия разбора маркера (`reason_class`) читает знак там, куда его ставит машина."""

    def test_the_anchor_is_a_mirror_of_the_judge_not_a_third_rule(self):
        self.assertEqual(dj._HEAD_RE.pattern, sig.JUDGE_HEAD_RE.pattern)
        self.assertEqual(dj._HEAD_RE.flags, sig.JUDGE_HEAD_RE.flags)
        self.assertEqual(dj._DAEMON_HEAD, sig.FAIL_HEAD)

    def test_sweep_the_mirror_answers_as_the_judge_on_every_form(self):
        words = {None: None, sig.UNKNOWN_PREFIX: dj.UNKNOWN, sig.UNPROVEN_PREFIX: dj.UNPROVEN}
        n = 0
        for text in _corpus_63c():
            n += 1
            self.assertEqual(dj.outcome_of(text), words[sig.judge_mark(text)], text)
        self.assertEqual(180, n)

    def test_contrafact_unproven_over_a_quoted_unknown_stays_unproven(self):
        text = _HEADER_63C + dj.UNPROVEN_PREFIX + " — 2 файла\nдоклад цитирует «%s»" % dj.UNKNOWN_PREFIX
        self.assertEqual(sig.CLS_UNPROVEN, sig.reason_class(text)[0])

    def test_contrafact_judge_verdict_over_a_quoted_daemon_code_is_the_judges(self):
        text = _HEADER_63C + dj.UNKNOWN_PREFIX + " — адрес пуст\nдоклад цитирует " + _Q_CODE
        self.assertEqual(sig.CLS_UNKNOWN, sig.reason_class(text)[0])

    def test_contrafact_a_scrubbed_quote_in_the_draft_does_not_name_the_code(self):
        text = ("⏱ 🧾 ЧЕРНОВИК: %s провал [причина=run_timeout · таймаут прогона]: 2700s. "
                "Окно работы неизвестно." % _Q_SCRUBBED)
        cls, how = sig.reason_class(text)
        self.assertIn("run_timeout", cls)
        self.assertEqual("код причины", how)

    def test_contrafact_signal_b_does_not_glue_three_different_rows_by_quotes(self):
        rows = [
            {"id": 1, "day": TODAY, "status": "failed", "result": _HEADER_63C + dj.UNKNOWN_PREFIX
             + " — адрес пуст\nдоклад цитирует " + _Q_CODE},
            {"id": 2, "day": TODAY, "status": "failed", "result": "⏱ 🧾 ЧЕРНОВИК: %s провал "
             "[причина=run_timeout · таймаут прогона]: 2700s. Окно работы неизвестно." % _Q_SCRUBBED},
            {"id": 3, "day": TODAY, "status": "failed",
             "result": "закрыто руками: разбор строки «причина=exec_error» не сошёлся"},
        ]
        self.assertFalse(sig.signal_b(rows, TODAY)["on"])

    def test_sweep_the_lock_what_was_named_in_place_is_named_the_same(self):
        """Замок не ослаблен: знак, стоявший на своём месте, опознаётся как прежде."""
        for head in ("", _HEADER_63C):
            self.assertEqual(sig.CLS_UNKNOWN,
                             sig.reason_class(head + dj.UNKNOWN_PREFIX + " — адрес пуст")[0])
            self.assertEqual(sig.CLS_UNPROVEN,
                             sig.reason_class(head + dj.UNPROVEN_PREFIX + " — 2 файла")[0])
            self.assertEqual(sig.CLS_REJECT, sig.reason_class(ctl.REJECT_MARK + ": не надо")[0])
            for code in sig.FAIL_NAMES:
                cls, how = sig.reason_class(head + "провал [причина=%s · имя]: подробности" % code)
                self.assertEqual(("%s (%s)" % (sig.FAIL_NAMES[code], code), "код причины"),
                                 (cls, how))


if __name__ == "__main__":            # pragma: no cover
    unittest.main(verbosity=2)
