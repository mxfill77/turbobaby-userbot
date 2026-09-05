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

# Версия канона рамки — ОБЯЗАТЕЛЬНЫЙ параметр сборки пакета (задача 223): её
# читают руки живьём из KB_shtab_frame. Здесь она ФИКСТУРА и живым номером быть
# не должна — иначе в дереве завелась бы вторая, молча стареющая копия версии.
_FRAME = "01.01.2026 #7"

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


def _receipt(queue_id=82, *, changed=True, closed=_NOW, text=LIVE_TASK_TEXT, result=None, commits=1):
    """Расписка-фикстура. ``commits=2`` — ПОВОД по правилу «крупный класс».

    Отдельная ручка, а не новый дефолт: с 05.09.2026 поводом считается не всякая
    закрытая цепочка, и тесты, которым нужен именно повод, обязаны просить его
    ЯВНО — иначе смена правила прошла бы мимо них молча.
    """
    if result is None:
        shas = ["abc1234", "def5678", "9012abc"][:max(0, commits)]
        result = "FACT: %s\nRESULT: сделано" % " ".join("commit %s в git log" % s for s in shas)
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

    def test_chain_without_any_commit_is_not_a_trigger(self):
        rec = _receipt(1, result="RESULT: посмотрел и ничего не менял")
        self.assertEqual(rec["change_reason"], "no_commit_claimed")
        self.assertIsNone(review_auto.chain_trigger(self._state_with(rec), _NOW))

    def test_one_verified_commit_is_a_ribbon_not_an_occasion(self):
        """ЛЕНТА: один подтверждённый коммит, замеры сошлись — внешних не зовём.

        Это и есть правка 05.09: раньше такая цепочка давала пакет, и таких за
        04.09 было 12 из 21. Второе мнение по ней приходит суточным дайджестом.
        """
        rec = _receipt(1, commits=1)
        self.assertTrue(rec["operational_change"], "фикстура должна быть операционным изменением")
        self.assertEqual(review_auto.chain_occasion(rec)[0], False)
        self.assertIsNone(review_auto.chain_trigger(self._state_with(rec), _NOW))
        # …и она НЕ потеряна: дайджест берёт её тем же окном.
        self.assertEqual([r["queue_id"] for r in review_auto.digest_trigger(
            self._state_with(rec), "2026-09-01T13:00:00Z", 1)["receipts"]], [1])

    def test_two_verified_commits_are_a_big_class_occasion(self):
        rec = _receipt(1, commits=2)
        ok, why = review_auto.chain_occasion(rec)
        self.assertTrue(ok)
        self.assertIn("крупный класс", why)
        self.assertIn("крупный класс", review_auto.chain_trigger(self._state_with(rec), _NOW)["occasion"])

    def test_claimed_commit_absent_from_the_tree_is_not_an_occasion(self):
        """Заявлен коммит, а в дереве его нет — ВНЕШНИМ разбирать нечего.

        Смена смысла названа вслух дважды. Утром 05.09 эта расписка стала первой
        веткой повода — и ровно она заклинила ступень A: `case_for_chain` требует
        `operational_change`, а у `commit_unverified` оно ложно ПО ОПРЕДЕЛЕНИЮ,
        то есть сборка отказывала на каждом таком поводе всегда. Живой счёт:
        55 витков подряд на цепочке `pc-2026-09-02-13`, 03:09:02 → 17:09:06,
        ноль пакетов наружу за сутки при 11 открытых поводах в спуле.
        """
        rec = _receipt(1, changed=False)
        self.assertFalse(rec["operational_change"])
        self.assertEqual(rec["change_reason"], "commit_unverified")
        ok, why = review_auto.chain_occasion(rec)
        self.assertFalse(ok)
        self.assertIn("не повод", why)
        # Отказ назван СВОИМ словом: у этой расписки замеры как раз разошлись, и
        # общая фраза «замеры сошлись» соврала бы ровно в том поле, ради которого
        # расписка и заводится.
        self.assertNotIn("замеры сошлись", why)
        self.assertIsNone(review_auto.chain_trigger(self._state_with(rec), _NOW))
        # …и расхождение НЕ ПОТЕРЯНО: суточный дайджест берёт её тем же окном.
        self.assertEqual([r["queue_id"] for r in review_auto.digest_trigger(
            self._state_with(rec), "2026-09-01T13:00:00Z", 1)["receipts"]], [1])

    def test_every_occasion_can_actually_be_built(self):
        """ИНВАРИАНТ: ПОВОД ⇒ СБОРКА ВОЗМОЖНА. Его нарушение и есть класс 05.09.

        Проверяем перебором, а не словами: любая расписка, признанная поводом,
        обязана пройти `case_for_chain` без исключения. Утром 05.09 инвариант не
        держался — и держать его было некому, теста на связь двух мест не
        существовало вовсе.
        """
        matrix = []
        for commits in (0, 1, 2, 3):
            for changed in (True, False):
                for status in ("done", "failed"):
                    shas = ["abc1234", "def5678", "9012abc"][:commits]
                    result = "FACT: %s\nRESULT: сделано" % " ".join(
                        "commit %s в git log" % s for s in shas)
                    claimed = review_auto.claimed_commits(result)
                    matrix.append(review_auto.receipt(
                        queue_id=1, task_text=LIVE_TASK_TEXT, status=status, result=result,
                        closed_at=_NOW, claimed=claimed, verified=claimed if changed else []))
        occasions = [r for r in matrix if review_auto.chain_occasion(r)[0]]
        self.assertGreaterEqual(len(occasions), 3, "перебор не дал поводов — проверять нечего")
        for rec in occasions:
            self.assertTrue(rec["operational_change"],
                            "повод без операционного изменения: %s" % rec["change_reason"])
            review_auto.case_for_chain(rec, "2026-09-01")     # исключение здесь = провал теста

    def test_reported_failure_with_a_live_commit_is_a_divergence_occasion(self):
        rec = review_auto.receipt(
            queue_id=9, task_text=LIVE_TASK_TEXT, status="failed",
            result="FACT: commit abc1234 в git log", closed_at=_NOW,
            claimed=["abc1234"], verified=["abc1234"], artifacts=[])
        ok, why = review_auto.chain_occasion(rec)
        self.assertTrue(ok)
        self.assertIn("объявила отказ", why)

    def test_oldest_chain_goes_first(self):
        st = self._state_with(_receipt(2, closed="2026-09-01T11:00:00Z", commits=2),
                              _receipt(1, closed="2026-09-01T09:00:00Z", commits=2))
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
        self.assertEqual(review_auto.next_trigger(self._state_with(_receipt(1, commits=2)),
                                                  _NOW, 1)["kind"], "chain")

    def test_bad_digest_hour_is_refused_not_guessed(self):
        with self.assertRaises(review_auto.ReviewAutoError):
            review_auto.digest_trigger(review_auto.state_default(), _NOW, 99)


class TestChannelHealth(unittest.TestCase):
    """Лежачий канал перестаёт получать пакеты — и оживает САМ, без владельца.

    Числа порогов не выдуманы: корпус лотка `docs/review_inbox` на 05.09 — 152
    захода (76 codex + 76 manus). У codex 76 из 76 `answered`. У manus две серии
    неответов подряд, длиной 14 и 22, и ОДИНОЧНЫХ провалов в корпусе ноль —
    поэтому порог в два подряд не даёт ни одного ложного «лежит» на всём корпусе.
    """

    def _down(self, now="2026-09-01T12:00:00Z"):
        st = review_auto.state_default()
        for _ in range(review_auto.CHANNEL_DOWN_STRIKES):
            st = review_auto.note_channel(st, "manus", "unknown", "answer_lost", now)
        return st

    def test_one_miss_does_not_bury_a_channel(self):
        st = review_auto.note_channel(review_auto.state_default(), "manus",
                                      "unknown", "answer_lost", _NOW)
        self.assertFalse(review_auto.channel_down(st, "manus"))
        self.assertEqual(review_auto.channel_plan(st, ["codex", "manus"], _NOW)["send"],
                         ["codex", "manus"])

    def test_two_misses_in_a_row_stop_the_packs(self):
        plan = review_auto.channel_plan(self._down(), ["codex", "manus"], "2026-09-01T12:10:00Z")
        self.assertEqual(plan["send"], ["codex"])
        self.assertEqual(plan["probe"], [])
        self.assertEqual([s["channel"] for s in plan["skip"]], ["manus"])
        # Пропуск обязан назвать, КОГДА канал попробуют снова: без этого он читается
        # как «выключен навсегда», а выключать внешнего критика запрещено.
        self.assertEqual(plan["skip"][0]["next_probe_at"], "2026-09-01T18:00:00Z")
        self.assertIn("следующая проба не раньше", review_auto.channel_plan_line(plan))

    def test_the_channel_revives_by_itself_after_the_probe_window(self):
        st = self._down()
        late = "2026-09-01T18:00:01Z"
        plan = review_auto.channel_plan(st, ["manus"], late)
        self.assertEqual(plan["probe"], ["manus"], "проба не наступила — канал не оживёт никогда")

        # Проба не ответила → окно отсчитывается ЗАНОВО от пробы, а не от начала лежания.
        st_failed = review_auto.note_channel(st, "manus", "unknown", "answer_lost", late, probed=True)
        self.assertEqual(review_auto.channel_plan(st_failed, ["manus"], "2026-09-01T20:00:00Z")["probe"], [])

        # Проба ответила → здоровье вернулось ЦЕЛИКОМ, и следующий пакет едет обычным путём.
        st_ok = review_auto.note_channel(st, "manus", "answered", "ok", late, probed=True)
        self.assertFalse(review_auto.channel_down(st_ok, "manus"))
        self.assertEqual(review_auto.channel_plan(st_ok, ["manus"], late)["send"], ["manus"])

    def test_our_own_fault_is_never_charged_to_the_channel(self):
        """Страж исходящего, упавшие руки и незаданный ключ — наши промахи, не канала.

        Наружу в них не ушло НИЧЕГО, и хоронить за них живой канал значило бы
        остаться без критика по собственной ошибке.
        """
        for reason in sorted(review_auto.CHANNEL_OUR_FAULT):
            with self.subTest(reason=reason):
                st = review_auto.state_default()
                for _ in range(review_auto.CHANNEL_DOWN_STRIKES + 3):
                    st = review_auto.note_channel(st, "manus", "refused", reason, _NOW)
                self.assertFalse(review_auto.channel_down(st, "manus"))

    def test_state_survives_a_round_trip_through_disk(self):
        st = review_auto.state_read(json.loads(json.dumps(self._down())))
        self.assertTrue(review_auto.channel_down(st, "manus"))


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
        pack = review_pack.build_review_pack(review_auto.case_for_chain(rec, "2026-09-01"), root=self.root,
                                             frame_version=_FRAME)
        self.assertEqual(pack["status"], "ok")
        text = review_pack.render_review_pack(pack)
        self.assertIn("## ГИПОТЕЗА ШТАБА (постановка задачи — НЕ факт и НЕ доказательство)", text)
        self.assertIn("ступень A ревью-контура", text)
        self.assertIn("## ВОПРОСЫ РЕВЬЮЕРУ", text)
        self.assertEqual(review_send.outbound_violations(review_send.build_prompt(text)), [])

    def test_artifact_with_absolute_paths_is_held_and_named_in_the_pack(self):
        """Найдено ЖИВЬЁМ 01.09: первый же повод приложил артефакт ПРО абсолютные корни, и
        страж задержал ВЕСЬ пакет. Артефакт не едет, но его имя и причина едут."""
        rec = _receipt()
        self._write_receipt(rec)
        dirty = "docs/artifacts/2026-09-01-грязный.md"
        review_auto_run.write_text(os.path.join(self.root, *dirty.split("/")),
                                   "тут литерал корня r\"D:\\turbobaby-bot\" в тексте\n")
        clean = "docs/artifacts/2026-09-01-чистый.md"
        review_auto_run.write_text(os.path.join(self.root, *clean.split("/")),
                                   "тут только относительные пути: docs/artifacts/x.md\n")
        kept, held = review_auto_run.screen_artifacts(self.root, [dirty, clean])
        self.assertEqual(kept, [clean])
        self.assertEqual(held[0]["path"], dirty)
        case = review_auto.case_for_chain(rec, "2026-09-01",
                                          line_counts=review_auto_run.line_counts(
                                              self.root, [review_auto.receipt_rel(rec), clean]),
                                          artifact_sources=kept, held_artifacts=held)
        pack = review_pack.build_review_pack(case, root=self.root, frame_version=_FRAME)
        text = review_pack.render_review_pack(pack)
        self.assertIn("НЕ ПРИЛОЖЕНО стражей исходящего", text)
        self.assertIn(dirty, text)
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
        pack = review_pack.build_review_pack(case, root=self.root, frame_version=_FRAME)
        self.assertEqual(pack["status"], "ok")
        text = review_pack.render_review_pack(pack)
        for rec in recs:
            self.assertIn(rec["task_id"], text)

    def test_digest_drops_receipts_until_it_fits_instead_of_blocking(self):
        """ЗАМЕР 01.09: четыре живые расписки дали 16046 знаков при потолке 15000 и `blocked`.
        Число приложенных обязано МЕРИТЬСЯ сборкой, иначе дайджест ломается ровно в те дни,
        когда отчёты подробнее обычного."""
        fat = "я" * 850
        recs = [_receipt(i, closed="2026-09-01T0%d:00:00Z" % i, text="ц" * 2000,
                         result="commit abc123%d\n%s" % (i, fat))
                for i in range(1, 5)]
        for rec in recs:
            self._write_receipt(rec)
        day = "2026-09-01"
        review_auto_run.write_text(os.path.join(self.root, *review_auto.digest_index_rel(day).split("/")),
                                   review_auto.digest_index_text(recs, day, day))
        counts = review_auto_run.line_counts(
            self.root, [review_auto.digest_index_rel(day)] + [review_auto.receipt_rel(r) for r in recs])
        case, pack = review_auto_run._fit_digest(
            {"receipts": recs, "day": day}, day, counts, self.root, review_pack.REVIEW_MAX_CHARS,
            (_FRAME, None))
        self.assertEqual(pack["status"], "ok")
        self.assertLess(len(case["result_packets"]), len(recs), "ни одной расписки не отброшено")
        self.assertIn("расписок приложено %d из %d" % (len(case["result_packets"]), len(recs)),
                      "\n".join(case["summary"]))

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
        pack = review_pack.build_review_pack(case, root=self.root, frame_version=_FRAME)
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
        pack = review_pack.build_review_pack(case, root=HERE, frame_version=_FRAME)
        text = review_pack.render_review_pack(pack)
        self.assertNotIn("ГИПОТЕЗА ШТАБА", text)
        stored = os.path.join(HERE, "docs", "review_outbox", review_pack.pack_filename(pack))
        if os.path.exists(stored):
            with io.open(stored, encoding="utf-8", newline="") as fh:
                was = fh.read()
            # Сверяем ТЕЛО, а не файл целиком: 05.09 в шапку встали версия канона
            # рамки и явная граница ревьюера, и байтового равенства с пакетами до
            # 223 больше нет ПО ЗАМЫСЛУ. Предмет юнита — что поле гипотезы не
            # сдвинуло разделы; шапку стережёт test_review_pack.FrameVersionHeader.
            cut = lambda t: t[t.index("## ЦЕЛЬ"):]
            self.assertEqual(cut(was), cut(text), "тело пакета ступени 1 пересобралось ИНАЧЕ")

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
        self.rec = _receipt(commits=2)          # повод по правилу «крупный класс»
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


class TestBuildFailureIsAnAttempt(unittest.TestCase):
    """ОТРИЦАТЕЛЬНАЯ ПРОБА к правке 05.09 (вечер): сборка упала — очередь ИДЁТ ДАЛЬШЕ.

    Ломаем сборку ПРИЧИНОЙ, НЕ РАВНОЙ той, что заклинила полосу утром. Это не
    придирка к формулировке: ключ повода не появлялся в учёте НИ ПРИ КАКОМ
    исключении, потому что оно улетало из `tick` до `note_attempt`. Починив только
    `not_operational`, мы оставили бы дыру ровно того же размера — следующее
    исключение любого вида снова дало бы вечный круг на самой старой записи
    (`chain_trigger` берёт САМУЮ СТАРУЮ подходящую).

    Причина здесь — `oversized_hypothesis`: расписка с постановкой длиннее потолка
    ступени 1. Класс живой, а не выдуманный: потолок постановки правили на этой же
    полосе 05.09, и расписка, записанная одной редакцией, читается другой.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="reviewauto_build_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = os.path.join(self.root, "state.json")
        # Битая — СТАРШЕ здоровой: иначе проба ничего не проверяет, очередь дошла бы
        # до здоровой и не заметив поломки.
        self.broken = dict(_receipt(41, commits=2, closed="2026-09-01T09:00:00Z"),
                           hypothesis="я" * (review_pack.HYPOTHESIS_TEXT_MAX + 1))
        self.good = _receipt(42, commits=2, closed="2026-09-01T10:00:00Z")
        spool = []
        for rec in (self.broken, self.good):
            spool = review_auto.spool_add(spool, rec)
            review_auto_run.write_text(
                os.path.join(self.root, *review_auto.receipt_rel(rec).split("/")),
                json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        # День дайджеста закрыт заранее: иначе он поднялся бы как ДРУГОЙ законный
        # повод и проба мерила бы не то.
        review_auto_run.write_state(self.state, review_auto.note_digest_day(
            dict(review_auto.state_default(), spool=spool), "2026-09-01", "answered"))
        original = review_auto_run.review_send_run.run_channel
        self.addCleanup(setattr, review_auto_run.review_send_run, "run_channel", original)
        review_auto_run.review_send_run.run_channel = self._answer

    def _answer(self, channel, prompt, ctx, args):
        verdict = review_send._verdict(
            channel=channel, pack_name=ctx["pack_name"], pack_sha256=ctx["pack_sha256"],
            send_date=ctx["send_date"], outcome="answered", reason="ok",
            detail="ответ канала", answer="находка: " + "я" * 400,
            prompt_sha256=ctx["prompt_sha256"])
        return verdict, "находка: " + "я" * 400

    def _tick(self, now):
        return review_auto_run.tick(root=self.root, state_path=self.state, now=now,
                                    digest_hour=1, write_journal=False)

    def _key(self, rec):
        return "chain:%s" % rec["task_id"]

    def test_after_max_attempts_the_queue_reaches_the_next_record(self):
        # 1. Сборка падает, и падает ДРУГИМ классом: `not_operational` здесь ни при чём.
        with self.assertRaises(review_pack.ReviewPackError) as ctx:
            self._tick(_NOW)
        self.assertEqual(ctx.exception.reason, "oversized_hypothesis")

        # 2. Попытка ЗАСЧИТАНА. До правки ключа в учёте не появлялось вовсе — и
        #    именно поэтому `MAX_ATTEMPTS` не срабатывал никогда.
        st = review_auto_run.read_state(self.state)
        rec = st["triggers"][self._key(self.broken)]
        self.assertEqual(rec["attempts"], 1)
        self.assertEqual(rec["last_outcomes"], ["build_failed"])
        self.assertEqual(rec["last_reasons"], ["oversized_hypothesis"])
        self.assertEqual(rec["verdict"], "retry")

        # 3. ГЛАВНОЕ: очередь НЕ ВСТАЛА — следующий виток берёт СЛЕДУЮЩУЮ запись.
        second = self._tick("2026-09-01T12:05:00Z")
        self.assertTrue(second["acted"], "очередь встала на битой записи")
        self.assertEqual(second["trigger"], self._key(self.good))
        self.assertEqual(second["outcomes"], ["answered"] * len(review_send.CHANNELS))
        self.assertTrue(os.path.exists(os.path.join(self.root, *second["pack"].split("/"))))
        # Битая запись не оставила в лотке ни обрубка, ни пустого пакета.
        self.assertEqual(os.listdir(os.path.join(self.root, "docs", "review_outbox")),
                         [os.path.basename(second["pack"])])

        # 4. Через паузу — РОВНО ОДИН повтор по битой записи, и он последний.
        with self.assertRaises(review_pack.ReviewPackError):
            self._tick("2026-09-01T13:30:00Z")
        st = review_auto_run.read_state(self.state)
        broken = st["triggers"][self._key(self.broken)]
        self.assertEqual(broken["attempts"], review_auto.MAX_ATTEMPTS)
        self.assertTrue(broken["closed"])
        self.assertEqual(broken["verdict"], "abandoned")

        # 5. Третьего захода нет никогда, и виток больше не падает вовсе.
        self.assertEqual(review_auto.attempt_allowed(st, self._key(self.broken),
                                                     "2026-09-02T13:30:00Z"),
                         (False, "already_closed"))
        last = self._tick("2026-09-01T14:30:00Z")
        self.assertFalse(last["acted"])
        self.assertIn("повода нет", last["why"])

    def test_the_exception_is_not_swallowed_by_the_new_accounting(self):
        """Учёт попытки не смеет ПРИГЛУШИТЬ новость о поломке.

        Исключение по-прежнему летит наружу тем же путём — демон пишет его
        WARNING'ом (`pc_orchestrator.maybe_review_auto`, ветка fail-safe). Молча
        съеденная поломка была бы хуже вечного круга: круг хотя бы виден в логе.
        """
        with self.assertRaises(review_pack.ReviewPackError):
            self._tick(_NOW)

    def test_the_live_stuck_receipt_never_reaches_the_builder_anymore(self):
        """Живая форма заклинившей записи: коммит объявлен, в дереве не найден.

        Ровно такой была `pc-2026-09-02-13` (`claimed_commits` — один, `verified` —
        ни одного). Теперь она поводом не становится вовсе, и ключа в учёте не
        заводит: очередь проходит мимо неё к следующей записи с ПЕРВОГО витка.
        """
        stuck = _receipt(13, changed=False, closed="2026-09-01T08:00:00Z")
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(stuck).split("/")),
            json.dumps(stuck, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        spool = review_auto.spool_add([], stuck)
        spool = review_auto.spool_add(spool, self.good)
        review_auto_run.write_state(self.state, review_auto.note_digest_day(
            dict(review_auto.state_default(), spool=spool), "2026-09-01", "answered"))
        report = self._tick(_NOW)
        self.assertTrue(report["acted"])
        self.assertEqual(report["trigger"], self._key(self.good))
        self.assertNotIn(self._key(stuck), review_auto_run.read_state(self.state)["triggers"])


class TestLiveChannelStillGoesOut(unittest.TestCase):
    """ПОЛОЖИТЕЛЬНАЯ ПРОБА к правке 05.09: экономия не смеет стать немотой.

    Прибор, переставший ходить наружу вовсе, — это отказ, а не экономия. Поэтому
    здесь проверяется ровно обратное пропуску: живой канал и НАСТОЯЩИЙ повод
    обязаны и после правки дать пакет наружу.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="reviewauto_live_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.state = os.path.join(self.root, "state.json")
        self.rec = _receipt(commits=2)
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(self.rec).split("/")),
            json.dumps(self.rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        self.seen = []
        original = review_auto_run.review_send_run.run_channel
        self.addCleanup(setattr, review_auto_run.review_send_run, "run_channel", original)
        review_auto_run.review_send_run.run_channel = self._answer

    def _answer(self, channel, prompt, ctx, args):
        self.seen.append((channel, getattr(args, "manus_wait", None)))
        verdict = review_send._verdict(
            channel=channel, pack_name=ctx["pack_name"], pack_sha256=ctx["pack_sha256"],
            send_date=ctx["send_date"], outcome="answered", reason="ok",
            detail="ответ канала", answer="находка: " + "я" * 400,
            prompt_sha256=ctx["prompt_sha256"])
        return verdict, "находка: " + "я" * 400

    def _state(self, st):
        review_auto_run.write_state(self.state, review_auto.note_digest_day(
            dict(st, spool=[self.rec]), "2026-09-01", "answered"))

    def _tick(self, now=_NOW):
        return review_auto_run.tick(root=self.root, state_path=self.state, now=now,
                                    digest_hour=1, write_journal=False)

    def test_healthy_channels_and_a_real_occasion_still_produce_a_pack(self):
        self._state(review_auto.state_default())
        report = self._tick()
        self.assertTrue(report["acted"], "живой канал и настоящий повод не дали пакета — это отказ")
        self.assertEqual([c for c, _ in self.seen], list(review_send.CHANNELS))
        self.assertEqual(report["outcomes"], ["answered", "answered"])
        self.assertIn("крупный класс", report["occasion"])
        self.assertTrue(os.path.exists(os.path.join(self.root, *report["pack"].split("/"))))
        # Боевое ожидание живого канала правкой НЕ тронуто: те же 1800с, что и до неё.
        self.assertEqual(dict(self.seen)["manus"], review_auto_run.review_send_run.DEFAULT_MANUS_WAIT)

    def test_a_down_channel_is_skipped_while_the_live_one_keeps_working(self):
        st = review_auto.state_default()
        for _ in range(review_auto.CHANNEL_DOWN_STRIKES):
            st = review_auto.note_channel(st, "manus", "unknown", "answer_lost", _NOW)
        self._state(st)
        report = self._tick()
        self.assertTrue(report["acted"])
        self.assertEqual([c for c, _ in self.seen], ["codex"])
        self.assertEqual([s["channel"] for s in report["channels"]["skip"]], ["manus"])
        self.assertIn("ПРОПУЩЕН manus", report["line"])
        # В лотке ровно ОДИН файл, и он от codex: пропуск ответом не притворяется
        # и фальшивой расписки в лоток не кладёт.
        files = sorted(os.listdir(os.path.join(self.root, "docs", "review_inbox")))
        self.assertEqual(len(files), 1, "пропущенный канал оставил файл в лотке")
        self.assertTrue(files[0].endswith("-codex.md"), files[0])

    def test_the_probe_goes_out_on_a_short_wait_not_the_full_one(self):
        st = review_auto.state_default()
        for _ in range(review_auto.CHANNEL_DOWN_STRIKES):
            st = review_auto.note_channel(st, "manus", "unknown", "answer_lost", _NOW)
        self._state(st)
        report = self._tick("2026-09-01T18:00:01Z")
        self.assertEqual(report["channels"]["probe"], ["manus"])
        self.assertEqual(dict(self.seen)["manus"], review_auto_run.DEFAULT_PROBE_WAIT)
        # Проба ответила → канал встал сам, без единого действия владельца.
        self.assertFalse(review_auto.channel_down(review_auto_run.read_state(self.state), "manus"))

    def test_all_channels_down_costs_no_attempt(self):
        """Чужой простой не смеет хоронить наш повод: попытка не списывается."""
        st = review_auto.state_default()
        for channel in review_send.CHANNELS:
            for _ in range(review_auto.CHANNEL_DOWN_STRIKES):
                st = review_auto.note_channel(st, channel, "unknown", "answer_lost", _NOW)
        self._state(st)
        report = self._tick()
        self.assertFalse(report["acted"])
        self.assertIn("все каналы лежат", report["why"])
        self.assertEqual(self.seen, [])
        self.assertEqual(review_auto_run.read_state(self.state)["triggers"], {})
        # …а пакет собран и лежит: он не потерян ни в одной ветке.
        self.assertTrue(os.path.exists(os.path.join(self.root, *report["pack"].split("/"))))


def _artifact_text(filler_lines):
    """Синтетический артефакт полосы: шапка, главные разделы и середина-наполнитель.

    Заголовки взяты в живом написании артефактов ПК («ЦЕЛЬ», «КРИТЕРИИ ПРИЁМКИ»,
    «РЕЗУЛЬТАТ», «ЗАПРЕТЫ И ОГРАНИЧЕНИЯ», «СПОРНОЕ ДОКАЗАТЕЛЬСТВО»), а не в
    придуманном: срез судят по НИМ, и голден на выдуманных словах зеленел бы, пока
    живой артефакт резался бы по-прежнему.
    """
    lines = ["# Артефакт полосы ПК — проба среза", "", "FACT: строка жизни в шапке.", ""]
    lines += ["## ЦЕЛЬ", "", "Проверить, что место достаётся главному, а не первым строкам.", ""]
    lines += ["## СЕРЕДИНА %d" % i for i in range(0, 0)]
    for i in range(filler_lines // 4):
        lines += ["## ПОДРОБНОСТЬ %d" % i, "", "строка наполнителя номер %d" % i, ""]
    lines += ["## КРИТЕРИИ ПРИЁМКИ", "", "Приёмка: цель и результат видны ревьюеру целиком.", ""]
    lines += ["## РЕЗУЛЬТАТ", "", "Итог: доля переданного выросла, пропуски названы.", ""]
    lines += ["## ЗАПРЕТЫ И ОГРАНИЧЕНИЯ", "", "Потолок пакета не поднимался ни на знак.", ""]
    lines += ["## СПОРНОЕ ДОКАЗАТЕЛЬСТВО", "", "Контрфакт: без выборки ревьюер видел треть текста.", ""]
    return "\n".join(lines) + "\n"


class TestArtifactOrder(unittest.TestCase):
    """Артефакт едет ГЛАВНЫМ, а не первым (правка 05.09.2026, находка внешнего аудита F3).

    Два ОТРИЦАТЕЛЬНЫХ теста здесь обязательны и стоят первыми: длинный артефакт
    обязан сохранить цель/критерии/результат и порезать СЕРЕДИНУ, а короткий —
    доехать ЦЕЛИКОМ и БЕЗ единой пометки о пропусках. Второй ловит ровно ту
    ошибку, которой такая правка обычно и кончается: пакет начинает объявлять
    пропуск там, где ничего не пропущено.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="reviewauto_order_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def _artifact(self, rel, text):
        review_auto_run.write_text(os.path.join(self.root, *rel.split("/")), text)
        return rel

    def _pack(self, rec, plans, held=()):
        counts = review_auto_run.line_counts(self.root, [review_auto.receipt_rel(rec)] + list(plans))
        case = review_auto.case_for_chain(rec, "2026-09-05", line_counts=counts,
                                          artifact_sources=list(plans), held_artifacts=list(held),
                                          artifact_plans=plans)
        return review_pack.build_review_pack(case, root=self.root, frame_version=_FRAME)

    # ── ОТРИЦАТЕЛЬНЫЙ 1: длиннее потолка ──
    def test_long_artifact_keeps_goal_criteria_result_and_cuts_the_middle(self):
        rec = _receipt(commits=2)
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(rec).split("/")),
            json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        rel = self._artifact("docs/artifacts/2026-09-05-длинный.md", _artifact_text(400))
        plans, held = review_auto_run.plan_artifacts(self.root, [rel], 60)
        self.assertEqual(held, [])
        plan = plans[rel]
        self.assertFalse(plan["full"])
        self.assertGreater(plan["total_lines"], plan["kept_lines"])

        pack = self._pack(rec, plans)
        self.assertEqual(pack["status"], "ok")
        text = review_pack.render_review_pack(pack)
        # Главное — ЦЕЛИКОМ, включая тело каждого раздела, а не только заголовок.
        for must in ("## ЦЕЛЬ", "место достаётся главному",
                     "## КРИТЕРИИ ПРИЁМКИ", "цель и результат видны ревьюеру целиком",
                     "## РЕЗУЛЬТАТ", "доля переданного выросла",
                     "## ЗАПРЕТЫ И ОГРАНИЧЕНИЯ", "## СПОРНОЕ ДОКАЗАТЕЛЬСТВО"):
            self.assertIn(must, text, "главное не доехало: %r" % must)
        # …а середина порезана, и порез НАЗВАН — в теле, в таблице и в сводке.
        self.assertNotIn("строка наполнителя номер 40", text)
        self.assertIn("[… ПРОПУЩЕНО", text)
        self.assertIn("## ПРОПУЩЕНО ВНУТРИ ИСТОЧНИКОВ", text)
        self.assertIn("НЕ ВОШЛО В ПАКЕТ", text)
        self.assertIn("· показано ", text)
        self.assertEqual(review_send.outbound_violations(review_send.build_prompt(text)), [])

    # ── ОТРИЦАТЕЛЬНЫЙ 2: короче потолка ──
    def test_short_artifact_travels_whole_with_no_gaps_and_no_gap_notice(self):
        rec = _receipt(commits=2)
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(rec).split("/")),
            json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        rel = self._artifact("docs/artifacts/2026-09-05-короткий.md", _artifact_text(8))
        plans, _ = review_auto_run.plan_artifacts(self.root, [rel], 200)
        plan = plans[rel]
        self.assertTrue(plan["full"])
        self.assertEqual(plan["kept_lines"], plan["total_lines"])
        self.assertEqual(plan["dropped"], [])
        self.assertIsNone(review_auto.gap_summary_line(rel, plan))

        pack = self._pack(rec, plans)
        text = review_pack.render_review_pack(pack)
        self.assertIn("строка наполнителя номер 1", text)
        for must_not in ("[… ПРОПУЩЕНО", "## ПРОПУЩЕНО ВНУТРИ ИСТОЧНИКОВ", "НЕ ВОШЛО В ПАКЕТ", "· показано "):
            self.assertNotIn(must_not, text, "пакет объявил пропуск, которого нет: %r" % must_not)
        source = [s for s in pack["sources"] if s["path"] == rel][0]
        self.assertNotIn("gaps", source)
        self.assertNotIn("line_ranges", source)

    def test_subsection_inherits_the_weight_of_its_parent(self):
        text = "\n".join([
            "# шапка", "", "## РЕЗУЛЬТАТ", "тело", "### 3.1 без ключевого слова", "тело",
            "## БОЛТОВНЯ", "тело",
        ]) + "\n"
        by_title = {s["title"]: s["weight"] for s in review_auto._sections(text.splitlines())}
        self.assertEqual(by_title["3.1 без ключевого слова"], by_title["РЕЗУЛЬТАТ"])
        self.assertEqual(by_title["БОЛТОВНЯ"], 0)

    def test_ranges_never_overlap_and_never_exceed_the_budget(self):
        plan = review_auto.plan_artifact_excerpt(_artifact_text(300), max_lines=45)
        self.assertLessEqual(plan["kept_lines"], 45)
        prev = 0
        for start, end in plan["ranges"]:
            self.assertGreater(start, prev)
            self.assertGreaterEqual(end, start)
            prev = end
        # Пропуски покрывают ровно то, что не вошло: сумма сходится, дыр в учёте нет.
        self.assertEqual(sum(g["lines"] for g in plan["dropped"]),
                         plan["total_lines"] - plan["kept_lines"])

    def test_zero_budget_is_refused_not_guessed(self):
        with self.assertRaises(review_auto.ReviewAutoError):
            review_auto.plan_artifact_excerpt("текст", max_lines=0)

    def test_fit_chain_spends_the_reserve_instead_of_leaving_it_empty(self):
        """ЗАМЕР лотка 05.09: медианный резерв 3517 знаков при потолке 15000.

        Подбор бюджета обязан отдать артефакту БОЛЬШЕ прежних 80 строк, когда место
        есть, и НЕ поднять при этом потолок пакета ни на знак.
        """
        rec = _receipt(commits=2)
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(rec).split("/")),
            json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        rel = self._artifact("docs/artifacts/2026-09-05-крупный.md", _artifact_text(600))
        rec["artifacts"] = [rel]
        case, pack = review_auto_run._fit_chain(rec, "2026-09-05", self.root,
                                                review_pack.REVIEW_MAX_CHARS, (_FRAME, None))
        self.assertEqual(pack["status"], "ok")
        source = [s for s in pack["sources"] if s["path"] == rel][0]
        self.assertGreater(source["excerpt_lines"], review_auto.ARTIFACT_HEAD_LINES)
        self.assertLessEqual(pack["text_chars"], review_pack.REVIEW_MAX_CHARS)
        self.assertEqual(pack["max_chars"], review_pack.REVIEW_MAX_CHARS)

    def test_artifact_that_never_fits_is_not_promised_in_the_summary(self):
        """Пакет не смеет обещать «показано N строк» у источника, которого в нём нет."""
        rec = _receipt(commits=2)
        review_auto_run.write_text(
            os.path.join(self.root, *review_auto.receipt_rel(rec).split("/")),
            json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        # Строки нарочно длинные: даже нижний бюджет строк не влезет в потолок пакета.
        fat = "\n".join(["# ЦЕЛЬ"] + ["ж" * 900 for _ in range(200)]) + "\n"
        rel = self._artifact("docs/artifacts/2026-09-05-неподъёмный.md", fat)
        rec["artifacts"] = [rel]
        case, pack = review_auto_run._fit_chain(rec, "2026-09-05", self.root,
                                                review_pack.REVIEW_MAX_CHARS, (_FRAME, None))
        text = review_pack.render_review_pack(pack)
        self.assertNotIn(rel, [s["path"] for s in pack["sources"]])
        self.assertNotIn("НЕ ВОШЛО В ПАКЕТ из %s" % rel, text)
        # …но и промолчать о нём пакет не смеет: имя, размер и причина названы.
        self.assertIn("НЕ ПРИЛОЖЕН по потолку пакета", text)
        self.assertIn(rel, text)


class TestTaskTextCeiling(unittest.TestCase):
    """ТЗ либо целиком до «ЧТО СДЕЛАТЬ» включительно, либо пакет ГОВОРИТ, что обрезано."""

    def test_short_task_text_travels_whole_without_any_mark(self):
        text = "ЦЕЛЬ: короткая постановка. ЧТО СДЕЛАТЬ: одно действие."
        self.assertEqual(review_auto.task_text_for_pack(text), text)
        self.assertNotIn("ОБРЕЗАНО", review_auto.sanitize(text))

    def test_long_task_keeps_everything_up_to_and_including_what_to_do(self):
        body = "ЦЕЛЬ: %s\n\nЗАПРЕТЫ: ничего не удалять.\n\nЧТО СДЕЛАТЬ\n\n1. Первый пункт.\n2. Второй пункт.\n" % ("подробность " * 250)
        tail = "\nАРИФМЕТИКА. Потолок 2700 с.\n\nПРЕДСМЕРТНЫЙ ВЗГЛЯД: провалится тем-то.\n"
        out = review_auto.task_text_for_pack(body + tail)
        self.assertIn("1. Первый пункт.", out)
        self.assertIn("2. Второй пункт.", out)
        self.assertNotIn("ПРЕДСМЕРТНЫЙ ВЗГЛЯД", out)
        self.assertIn("ДО раздела «ЧТО СДЕЛАТЬ» включительно", out)

    def test_clip_names_itself_with_numbers_and_never_exceeds_the_ceiling(self):
        long_text = "з" * 5000
        out = review_auto.clip_named(long_text, 300)
        self.assertLessEqual(len(out), 300)
        self.assertIn("ОБРЕЗАНО", out)
        self.assertIn("5000", out)

    def test_sanitized_hypothesis_stays_inside_the_stage_one_ceiling(self):
        """Прежняя обрезка давала 1202 знака при потолке 1200 — ступень 1 отказала бы."""
        out = review_auto.sanitize("я" * 9000)
        self.assertLessEqual(len(out), review_pack.HYPOTHESIS_TEXT_MAX)
        self.assertEqual(review_send.outbound_violations(out), [])

    def test_live_task_text_still_loses_the_absolute_path_after_the_new_clip(self):
        out = review_auto.sanitize(review_auto.task_text_for_pack(LIVE_TASK_TEXT))
        self.assertNotIn("turbobaby-bot", out)
        self.assertEqual(review_send.outbound_violations(out), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
