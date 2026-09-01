# -*- coding: utf-8 -*-
"""test_review_auto.py — регресс ступени A ревью-контура (повод → пакет → канал).

Три вещи здесь важнее остальных и потому стоят отдельными классами:

* ``TestPurity`` — инвариант ``REVIEW_AUTO_PURE``: чистый модуль решения не смеет
  завести часы, сеть, диск или ``getenv``. Проверяется обходом AST, а не верой.
* ``TestOutboundSanitizer`` — инвариант стражи: ``outbound_violations(sanitize(x))``
  пусто для ЛЮБОГО ``x``, включая ДОСЛОВНЫЙ текст живой постановки этой полосы
  (в нём есть абсолютный путь — то самое, что задержало бы пакет, и то самое,
  чего в пакете быть не должно).
* ``TestNegativeChannel`` — ОТРИЦАТЕЛЬНАЯ ПРОБА полного пути: оба канала мертвы,
  и контур обязан записать отказ НАЗВАННОЙ причиной, НЕ потерять пакет и
  повторить РОВНО ОДИН раз.

Имена боевых секретов в фикстурах НЕ ЦИТИРУЮТСЯ (правило среды №6): страж судит
по ФОРМЕ присвоения, поэтому нейтральное имя проверяет ту же ветку, а живое имя
в тексте теста стоило бы карточки владельцу на каждой правке файла.
"""
from __future__ import annotations

import ast
import io
import json
import os
import shutil
import socket
import tempfile
import unittest

import review_auto
import review_auto_run
import review_pack
import review_send

HERE = os.path.dirname(os.path.abspath(__file__))

# Живой текст постановки этой полосы (заголовок задачи 82, дословно): в нём есть
# абсолютный путь. Голден намеренно не «идеализированная формулировка» — класс
# «тест ≠ реальность» на этой полосе уже стоил зелёного юнита при живом провале.
LIVE_TASK_TEXT = (
    "Ты выполняешь задачу автономно в headless-режиме (без интерактивного подтверждения) "
    "в репо D:\\turbobaby-bot — .claude/settings.json и pretool_guard действуют. "
    "ЦЕЛЬ: ступень A ревью-контура — демон сам собирает пакет и шлёт в каналы."
)

_NOW = "2026-09-01T12:00:00Z"


def _free_port():
    """Порт, взятый у ядра и сразу отпущенный. Константа вроде 9 могла бы оказаться
    занятой чужой службой — и отрицательная проба тихо стала бы положительной."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _receipt(queue_id=82, *, changed=True, closed=_NOW, text=LIVE_TASK_TEXT, result=None):
    result = result if result is not None else "FACT: commit abc1234 в git log\nRESULT: сделано"
    claimed = review_auto.claimed_commits(result)
    return review_auto.receipt(
        queue_id=queue_id, task_text=text, status="done", result=result,
        closed_at=closed, claimed=claimed, verified=claimed if changed else [],
    )


class TestPurity(unittest.TestCase):
    """REVIEW_AUTO_PURE — чистая логика остаётся чистой."""

    BANNED_CALLS = {"now", "utcnow", "time", "monotonic", "getenv", "open", "run", "Popen", "urlopen"}
    BANNED_IMPORTS = {"os", "subprocess", "socket", "urllib", "time", "shutil", "requests"}

    def test_no_clock_no_disk_no_network_no_env(self):
        with io.open(os.path.join(HERE, "review_auto.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="review_auto.py")
        bad_imports, bad_calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad_imports |= {a.name.split(".")[0] for a in node.names} & self.BANNED_IMPORTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in self.BANNED_IMPORTS:
                    bad_imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name in self.BANNED_CALLS:
                    bad_calls.add(name)
        self.assertEqual(bad_imports, set(), "чистый модуль импортирует запрещённое")
        self.assertEqual(bad_calls, set(), "чистый модуль зовёт запрещённое")

    def test_hands_do_not_reimplement_the_decision(self):
        """Руки не решают: учёт заходов живёт ТОЛЬКО в чистом модуле."""
        with io.open(os.path.join(HERE, "review_auto_run.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("MAX_ATTEMPTS =", body)
        self.assertNotIn("RETRY_AFTER_SEC =", body)


class TestOutboundSanitizer(unittest.TestCase):
    """Постановка Штаба — единственный свободный текст пакета; страж её не увидит."""

    def test_live_task_text_loses_absolute_path(self):
        out = review_auto.sanitize(LIVE_TASK_TEXT)
        self.assertNotIn("turbobaby-bot", out)
        self.assertIn("[снято стражей: абсолютный путь Windows]", out)
        self.assertIn("ступень A ревью-контура", out)          # смысл постановки уцелел

    def test_invariant_guard_finds_nothing_after_sanitize(self):
        long_value = "abcdefghijklmnopqrstuvwxyz012345"
        corpus = [
            LIVE_TASK_TEXT,
            "ключ sk-abcdefghijklmnopqrstuvwx лежит прямо тут",
            "напиши на info@turbophuket.com или @turbobabymanager, тел +66 812345678",
            "путь /root/turbobaby/bot.py и \\\\SERVER\\share\\file.txt",
            "api_key=" + long_value,                      # форма присвоения, имя нейтральное
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THs",
            "обычный текст без единой находки",
            "",
        ]
        for text in corpus:
            with self.subTest(text=text[:40]):
                self.assertEqual(review_send.outbound_violations(review_auto.sanitize(text)), [])

    def test_fail_closed_when_wide_rules_cannot_reach_it(self):
        """Что широкие правила не сняли, снимается ЦЕЛОЙ строкой — молчание дороже утечки."""
        out = review_auto.sanitize("хвост D:")           # не путь по правилу стражи
        self.assertEqual(review_send.outbound_violations(out), [])

    def test_cap_and_multiline_collapse(self):
        out = review_auto.sanitize("а\nб\nв" + "я" * 5000)
        self.assertLessEqual(len(out), review_auto.HYPOTHESIS_MAX + 2)
        self.assertNotIn("\n", out)

    def test_none_is_empty_not_a_crash(self):
        self.assertEqual(review_auto.sanitize(None), "")


class TestCommits(unittest.TestCase):
    def test_commit_taken_only_next_to_a_pointing_word(self):
        got = review_auto.claimed_commits("FACT: commit 298a387 в git log; sha256 ea11af69d67a пакета")
        self.assertEqual(got, ["298a387"])

    def test_bare_hex_is_not_a_commit(self):
        self.assertEqual(review_auto.claimed_commits("просто 298a387 без слова"), [])

    def test_dedup_and_lowercase(self):
        self.assertEqual(review_auto.claimed_commits("коммит ABC1234, commit abc1234"), ["abc1234"])

    def test_three_outcomes_of_operational_change(self):
        self.assertEqual(review_auto.operational_change(["a1b2c3d"], ["a1b2c3d"]), (True, "commit_verified"))
        self.assertEqual(review_auto.operational_change(["a1b2c3d"], []), (False, "commit_unverified"))
        self.assertEqual(review_auto.operational_change([], []), (False, "no_commit_claimed"))

    def test_verified_outside_claimed_is_not_counted(self):
        """Подтверждение чужого коммита операционным изменением ЭТОЙ цепочки не является."""
        self.assertEqual(review_auto.operational_change(["a1b2c3d"], ["deadbee"]), (False, "commit_unverified"))


class TestReceipt(unittest.TestCase):
    def test_task_id_carries_the_day_because_queue_numbers_are_reused(self):
        one = _receipt(82, closed="2026-09-01T10:00:00Z")
        two = _receipt(82, closed="2026-08-22T10:00:00Z")
        self.assertNotEqual(one["task_id"], two["task_id"])
        self.assertEqual(one["task_id"], "pc-2026-09-01-82")

    def test_hypothesis_is_sanitized_inside_the_receipt(self):
        rec = _receipt()
        self.assertNotIn("turbobaby-bot", rec["hypothesis"])
        self.assertEqual(review_send.outbound_violations(rec["hypothesis"]), [])

    def test_unknown_status_is_refused(self):
        with self.assertRaises(review_auto.ReviewAutoError):
            review_auto.receipt(queue_id=1, task_text="x", status="needs_approval", result="",
                                closed_at=_NOW, claimed=[], verified=[])

    def test_spool_is_idempotent_by_task_id(self):
        rec = _receipt()
        spool = review_auto.spool_add(review_auto.spool_add([], rec), rec)
        self.assertEqual(len(spool), 1)

    def test_spool_is_capped(self):
        spool = []
        for i in range(review_auto.SPOOL_MAX + 10):
            spool = review_auto.spool_add(spool, _receipt(i, closed="2026-09-01T%02d:%02d:00Z" % (i // 60, i % 60)))
        self.assertEqual(len(spool), review_auto.SPOOL_MAX)


class TestAttempts(unittest.TestCase):
    """«Повтор не чаще одного раза» — оба прочтения сразу: один повтор ВСЕГО и не раньше паузы."""

    def setUp(self):
        self.state = review_auto.state_default()

    def test_first_attempt_is_allowed(self):
        self.assertEqual(review_auto.attempt_allowed(self.state, "chain:x", _NOW), (True, "first_attempt"))

    def test_retry_is_refused_while_the_pause_holds(self):
        st = review_auto.note_attempt(self.state, "chain:x", "chain", _NOW)
        st, verdict = review_auto.note_outcome(st, "chain:x", ["refused"], ["channel_unreachable"], _NOW)
        self.assertEqual(verdict, "retry")
        self.assertEqual(review_auto.attempt_allowed(st, "chain:x", "2026-09-01T12:10:00Z"),
                         (False, "retry_too_soon"))

    def test_exactly_one_retry_and_then_abandoned(self):
        st = review_auto.note_attempt(self.state, "chain:x", "chain", _NOW)
        st, _ = review_auto.note_outcome(st, "chain:x", ["refused"], ["channel_unreachable"], _NOW)
        later = "2026-09-01T13:30:00Z"
        self.assertEqual(review_auto.attempt_allowed(st, "chain:x", later), (True, "retry_allowed"))
        st = review_auto.note_attempt(st, "chain:x", "chain", later)
        st, verdict = review_auto.note_outcome(st, "chain:x", ["refused"], ["channel_unreachable"], later)
        self.assertEqual(verdict, "abandoned")
        self.assertEqual(review_auto.attempt_allowed(st, "chain:x", "2026-09-02T00:00:00Z"),
                         (False, "already_closed"))

    def test_one_answered_channel_closes_the_trigger(self):
        st = review_auto.note_attempt(self.state, "chain:x", "chain", _NOW)
        st, verdict = review_auto.note_outcome(st, "chain:x", ["answered", "refused"],
                                               ["ok", "no_credentials"], _NOW)
        self.assertEqual(verdict, "answered")
        self.assertTrue(st["triggers"]["chain:x"]["closed"])

    def test_refusal_line_names_channel_reason_and_the_pack_address(self):
        line = review_auto.refusal_line("chain:x", "retry", ["refused"], ["channel_unreachable"],
                                        "docs/review_outbox/p.md")
        self.assertIn("channel_unreachable", line)
        self.assertIn("docs/review_outbox/p.md", line)
        self.assertIn("НЕ потерян", line)

    def test_broken_state_starts_the_count_over_instead_of_half_trusting_it(self):
        self.assertEqual(review_auto.state_read("мусор"), review_auto.state_default())
        self.assertEqual(review_auto.state_read({"triggers": "не словарь"})["triggers"], {})


class TestTriggers(unittest.TestCase):
    def _state_with(self, *recs):
        st = review_auto.state_default()
        for rec in recs:
            st["spool"] = review_auto.spool_add(st["spool"], rec)
        return st

    def test_chain_without_operational_change_is_not_a_trigger(self):
        self.assertIsNone(review_auto.chain_trigger(self._state_with(_receipt(1, changed=False)), _NOW))

    def test_oldest_chain_goes_first(self):
        st = self._state_with(_receipt(2, closed="2026-09-01T11:00:00Z"),
                              _receipt(1, closed="2026-09-01T09:00:00Z"))
        self.assertEqual(review_auto.chain_trigger(st, _NOW)["receipt"]["queue_id"], 1)

    def test_digest_waits_for_its_hour(self):
        st = self._state_with(_receipt(1))
        self.assertIsNone(review_auto.digest_trigger(st, "2026-09-01T00:30:00Z", 1))
        self.assertIsNotNone(review_auto.digest_trigger(st, "2026-09-01T01:30:00Z", 1))

    def test_digest_fires_once_a_day(self):
        st = review_auto.note_digest_day(self._state_with(_receipt(1)), "2026-09-01", "answered")
        self.assertIsNone(review_auto.digest_trigger(st, _NOW, 1))
        self.assertIsNotNone(review_auto.digest_trigger(st, "2026-09-02T02:00:00Z", 1))

    def test_digest_window_is_the_last_24h_not_the_calendar_day(self):
        st = self._state_with(_receipt(1, closed="2026-08-31T23:00:00Z"),
                              _receipt(2, closed="2026-08-30T23:00:00Z"))
        window = review_auto.digest_trigger(st, _NOW, 1)["receipts"]
        self.assertEqual([r["queue_id"] for r in window], [1])

    def test_empty_digest_is_a_trigger_with_an_empty_window_not_silence(self):
        trigger = review_auto.digest_trigger(review_auto.state_default(), _NOW, 1)
        self.assertIsNotNone(trigger)
        self.assertEqual(trigger["receipts"], [])

    def test_event_outruns_the_schedule(self):
        self.assertEqual(review_auto.next_trigger(self._state_with(_receipt(1)), _NOW, 1)["kind"], "chain")

    def test_bad_digest_hour_is_refused_not_guessed(self):
        with self.assertRaises(review_auto.ReviewAutoError):
            review_auto.digest_trigger(review_auto.state_default(), _NOW, 99)


class TestCaseBuilding(unittest.TestCase):
    """Спецификация случая строится из фактов и собирается ступенью 1 без правок руками."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="reviewauto_case_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _write_receipt(self, rec):
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(rec).split("/")),
            json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    def test_chain_case_builds_an_ok_pack_with_the_hypothesis_section(self):
        rec = _receipt()
        self._write_receipt(rec)
        pack = review_pack.build_review_pack(review_auto.case_for_chain(rec, "2026-09-01"), root=self.root)
        self.assertEqual(pack["status"], "ok")
        text = review_pack.render_review_pack(pack)
        self.assertIn("## ГИПОТЕЗА ШТАБА (постановка задачи — НЕ факт и НЕ доказательство)", text)
        self.assertIn("ступень A ревью-контура", text)
        self.assertIn("## ВОПРОСЫ РЕВЬЮЕРУ", text)
        self.assertEqual(review_send.outbound_violations(review_send.build_prompt(text)), [])

    def test_chain_without_operational_change_cannot_become_a_case(self):
        with self.assertRaises(review_auto.ReviewAutoError):
            review_auto.case_for_chain(_receipt(changed=False), "2026-09-01")

    def test_digest_case_keeps_the_full_list_in_the_required_index(self):
        recs = [_receipt(i, closed="2026-09-01T0%d:00:00Z" % i) for i in range(1, 4)]
        for rec in recs:
            self._write_receipt(rec)
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.digest_index_rel("2026-09-01").split("/")),
            review_auto.digest_index_text(recs, "2026-09-01", "2026-09-01"))
        case = review_auto.case_for_digest(recs, "2026-09-01", "2026-09-01")
        required = [s["path"] for s in case["sources"] if s["required"]]
        # Индекс — ПЕРВЫЙ обязательный: он один отвечает за полноту списка.
        self.assertEqual(required[0], review_auto.digest_index_rel("2026-09-01"))
        # Расписки тоже обязательны — правило ступени 1 не разрешает иначе.
        self.assertTrue(all(s["required"] for s in case["sources"]))
        pack = review_pack.build_review_pack(case, root=self.root)
        self.assertEqual(pack["status"], "ok")
        text = review_pack.render_review_pack(pack)
        for rec in recs:
            self.assertIn(rec["task_id"], text)

    def test_digest_index_names_every_chain_including_the_read_only_ones(self):
        text = review_auto.digest_index_text([_receipt(1), _receipt(2, changed=False)],
                                             "2026-09-01", "2026-09-01")
        self.assertIn("pc-2026-09-01-1", text)
        self.assertIn("pc-2026-09-01-2", text)
        self.assertIn("commit_unverified", text)

    def test_empty_hypothesis_is_declared_silence_not_a_missing_section(self):
        rec = _receipt(text="")
        self._write_receipt(rec)
        case = review_auto.case_for_chain(rec, "2026-09-01")
        self.assertEqual(case["hypothesis"], [])
        pack = review_pack.build_review_pack(case, root=self.root)
        self.assertIn("постановки не было", review_pack.render_review_pack(pack))


class TestStageOneUntouched(unittest.TestCase):
    """Поле гипотезы НЕ сдвинуло уже собранные пакеты: у случая без ключа раздела нет вовсе."""

    def test_case_without_the_key_renders_byte_for_byte_as_before(self):
        case_path = os.path.join(HERE, "docs", "review_cases", "2026-09-01-chains-2026-08-31.json")
        if not os.path.exists(case_path):
            self.skipTest("случая ступени 1 нет в дереве")
        with io.open(case_path, encoding="utf-8") as fh:
            case = json.load(fh)
        self.assertNotIn("hypothesis", case)
        pack = review_pack.build_review_pack(case, root=HERE)
        text = review_pack.render_review_pack(pack)
        self.assertNotIn("ГИПОТЕЗА ШТАБА", text)
        stored = os.path.join(HERE, "docs", "review_outbox", review_pack.pack_filename(pack))
        if os.path.exists(stored):
            with io.open(stored, encoding="utf-8", newline="") as fh:
                self.assertEqual(fh.read(), text, "пакет ступени 1 пересобрался ИНАЧЕ")

    def test_oversized_hypothesis_is_refused_not_silently_clipped(self):
        with self.assertRaises(review_pack.ReviewPackError):
            review_pack._validate_hypothesis([{"source": "s", "text": "я" * 9000}])

    def test_hypothesis_records_are_capped(self):
        with self.assertRaises(review_pack.ReviewPackError):
            review_pack._validate_hypothesis([{"source": "s", "text": "t"}] * 99)


class TestNoteClosed(unittest.TestCase):
    """Расписка пишется файлом и ложится в спул; git спрашивают, а не верят отчёту."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="reviewauto_note_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = os.path.join(self.root, "state.json")
        self.asked = None

    def _runner(self, ok=True):
        class _Done(object):
            returncode = 0 if ok else 1

        def run(argv, **kw):
            self.asked = argv
            return _Done()

        return run

    def test_receipt_lands_on_disk_and_in_spool(self):
        rec = review_auto_run.note_closed(
            82, LIVE_TASK_TEXT, "done", "FACT: commit abc1234 в git log",
            root=self.root, state_path=self.state, runner=self._runner(True), stamp=_NOW)
        self.assertTrue(rec["operational_change"])
        self.assertTrue(os.path.exists(os.path.join(self.root, *review_auto.receipt_rel(rec).split("/"))))
        st = review_auto_run.read_state(self.state)
        self.assertEqual([r["task_id"] for r in st["spool"]], [rec["task_id"]])

    def test_unverified_commit_is_not_an_operational_change(self):
        rec = review_auto_run.note_closed(
            82, "тз", "done", "FACT: commit abc1234", root=self.root, state_path=self.state,
            runner=self._runner(False), stamp=_NOW)
        self.assertFalse(rec["operational_change"])
        self.assertEqual(rec["change_reason"], "commit_unverified")

    def test_only_hex_reaches_git(self):
        review_auto_run.verify_commits(["не-хеш; и что-то ещё"], root=self.root, runner=self._runner(True))
        self.assertIsNone(self.asked)

    def test_needs_approval_makes_no_receipt(self):
        self.assertIsNone(review_auto_run.note_closed(
            1, "тз", "needs_approval", "", root=self.root, state_path=self.state, stamp=_NOW))


class TestNegativeChannel(unittest.TestCase):
    """ОТРИЦАТЕЛЬНАЯ ПРОБА: оба канала недоступны.

    Гоняем НАСТОЯЩИЕ руки (`review_auto_run.tick`), а не заглушку: молчит контур
    или нет, видно только на полном пути «повод → пакет → канал → лоток → учёт».
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="reviewauto_neg_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = os.path.join(self.root, "state.json")
        self.rec = _receipt()
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(self.rec).split("/")),
            json.dumps(self.rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        # Дайджест дня закрыт заранее СОЗНАТЕЛЬНО: иначе следующий тик поднял бы
        # ЕГО (это законный ДРУГОЙ повод) и проба «повтора нет» проверяла бы не то.
        review_auto_run.write_state(self.state, review_auto.note_digest_day(
            dict(review_auto.state_default(), spool=[self.rec]), "2026-09-01", "answered"))
        self.dead_manus = "http://127.0.0.1:%d" % _free_port()

    def _tick(self, now):
        return review_auto_run.tick(
            root=self.root, state_path=self.state, now=now, digest_hour=1,
            codex_bin=os.path.join(self.root, "нет-такого-codex.exe"),
            manus_base=self.dead_manus, key_env="REVIEW_AUTO_TEST_KEY_ABSENT",
            timeout=20, write_journal=False)

    def test_dead_channels_are_refused_by_name_pack_survives_and_one_retry_only(self):
        first = self._tick(_NOW)

        # 1. Оба канала ОТКАЗАЛИ, и у каждого отказа НАЗВАНА причина.
        self.assertEqual(first["outcomes"], ["refused", "refused"])
        self.assertTrue(all(first["reasons"]), "отказ без причины — это тишина, а не отказ")
        self.assertEqual(first["verdict"], "retry")

        # 2. ПАКЕТ НЕ ПОТЕРЯН: он лежит в лотке, и строка отказа несёт его адрес.
        pack_path = os.path.join(self.root, *first["pack"].split("/"))
        self.assertTrue(os.path.exists(pack_path))
        with io.open(pack_path, encoding="utf-8") as fh:
            self.assertIn("ГИПОТЕЗА ШТАБА", fh.read())
        self.assertIn(first["pack"], first["line"])

        # 3. Отказ ЗАПИСАН файлом в лоток ответов — по одному на канал, со словом отказа.
        inbox = os.path.join(self.root, "docs", "review_inbox")
        files = sorted(os.listdir(inbox))
        self.assertEqual(len(files), 2)
        for name in files:
            with io.open(os.path.join(inbox, name), encoding="utf-8") as fh:
                self.assertIn("КАНАЛ ОТКАЗАЛ", fh.read())

        # 4. Немедленный повтор ЗАПРЕЩЁН (пауза не прошла) — повода нет вовсе.
        again = self._tick("2026-09-01T12:05:00Z")
        self.assertFalse(again["acted"])
        self.assertEqual(len(os.listdir(inbox)), 2, "запрещённый повтор всё-таки сходил в канал")

        # 5. Через паузу — РОВНО ОДИН повтор, и он закрывает повод словом abandoned.
        second = self._tick("2026-09-01T13:30:00Z")
        self.assertEqual(second["verdict"], "abandoned")
        st = review_auto_run.read_state(self.state)
        self.assertEqual(st["triggers"]["chain:%s" % self.rec["task_id"]]["attempts"], 2)

        # 6. Третьего захода по ЭТОМУ поводу нет никогда, а пакет по-прежнему на месте.
        self.assertEqual(review_auto.attempt_allowed(st, "chain:%s" % self.rec["task_id"],
                                                     "2026-09-03T13:30:00Z"), (False, "already_closed"))
        self.assertTrue(os.path.exists(pack_path))

    def test_digest_is_a_different_trigger_and_is_not_blocked_by_a_refused_chain(self):
        """Отказ по цепочке НЕ хоронит дайджест: поводы считаются каждый своим ключом."""
        review_auto_run.write_state(self.state, dict(review_auto.state_default(), spool=[self.rec]))
        first = self._tick(_NOW)
        self.assertEqual(first["kind"], "chain")
        second = self._tick("2026-09-01T12:05:00Z")
        self.assertEqual(second["kind"], "digest")
        self.assertTrue(second["pack"].endswith("digest-2026-09-01.md"))

    def test_attempt_is_counted_before_the_network_so_a_crash_costs_a_try(self):
        def boom(*a, **kw):
            raise RuntimeError("канал взорвался на середине")

        original = review_auto_run.review_send_run.run_channel
        review_auto_run.review_send_run.run_channel = boom
        self.addCleanup(setattr, review_auto_run.review_send_run, "run_channel", original)
        report = self._tick(_NOW)
        self.assertEqual(report["outcomes"], ["refused", "refused"])
        self.assertEqual(report["reasons"], ["sender_crashed", "sender_crashed"])
        st = review_auto_run.read_state(self.state)
        self.assertEqual(st["triggers"]["chain:%s" % self.rec["task_id"]]["attempts"], 1)

    def test_dry_run_writes_the_pack_but_touches_no_channel_and_no_attempt(self):
        report = review_auto_run.tick(root=self.root, state_path=self.state, now=_NOW,
                                      digest_hour=1, dry=True, write_journal=False)
        self.assertFalse(report["acted"])
        self.assertEqual(report["guard"], ["чисто"])
        self.assertFalse(os.path.isdir(os.path.join(self.root, "docs", "review_inbox")))
        self.assertEqual(review_auto_run.read_state(self.state)["triggers"], {})

    def test_no_trigger_is_an_outcome_not_silence(self):
        review_auto_run.write_state(self.state, review_auto.note_digest_day(
            dict(review_auto.state_default(), spool=[]), "2026-09-01", "empty"))
        report = review_auto_run.tick(root=self.root, state_path=self.state, now=_NOW,
                                      digest_hour=1, write_journal=False)
        self.assertFalse(report["acted"])
        self.assertIn("повода нет", report["why"])

    def test_empty_digest_closes_the_day_instead_of_grinding_it_all_day(self):
        review_auto_run.write_state(self.state, dict(review_auto.state_default(), spool=[]))
        report = review_auto_run.tick(root=self.root, state_path=self.state, now=_NOW,
                                      digest_hour=1, write_journal=False)
        self.assertFalse(report["acted"])
        self.assertEqual(review_auto_run.read_state(self.state)["digest"],
                         {"last_day": "2026-09-01", "last_reason": "empty"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
