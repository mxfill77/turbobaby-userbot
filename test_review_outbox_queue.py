# -*- coding: utf-8 -*-
"""Регресс очереди исходящих (ступень F ревью-контура).

Что здесь проверяется по существу, а не по форме:

* РАЗЛИЧЕНИЕ ПРОИСХОЖДЕНИЯ — таблицей на ЖИВОМ СЛОВАРЕ причин отправщика, а не
  на выдуманных строках: каждая ветка ``review_send.classify_*`` обязана иметь
  здесь свой ответ, иначе новая причина молча уедет в «повтора нет».
* ОТРИЦАТЕЛЬНАЯ ПРОБА — канал отвечает 500 ТРИЖДЫ, и отвечает ПО-НАСТОЯЩЕМУ:
  живой HTTP-сервер на петле, живой ``send_manus``, живой классификатор. Мок
  здесь запрещён правилом-классом «мок обязан копировать живой формат»: ровно на
  этом месте мок скрыл бы, что 500 приезжает исключением ``HTTPError``, а не
  возвратом.
* «ГОТОВО» НЕ ПОЯВЛЯЕТСЯ НИ НА ОДНОЙ ДОРОГЕ — исчерпание даёт ``unknown``, и это
  проверяется перебором всех состояний, а не одним счастливым случаем.
* БУТСТРАП ЧИТАЕТСЯ, А НЕ ТОЛЬКО ПИШЕТСЯ — два оборота подряд (урок ступени D:
  её бутстрап был декоративным, а тест смотрел один оборот).
"""

from __future__ import annotations

import ast
import datetime
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import review_auto
import review_outbox_queue as Q
import review_outbox_queue_run as R
import review_send
import review_send_run

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = datetime.timezone.utc


def at(h=12, m=0, s=0, day=1):
    return datetime.datetime(2026, 9, day, h, m, s, tzinfo=UTC)


# ═════════════════════════ 1. происхождение отказа ═════════════════════════


class TestOrigin(unittest.TestCase):
    """Причина → происхождение. Развилка поведения всей ступени."""

    def test_external_reasons_are_theirs(self):
        for reason in ("channel_unreachable", "timeout", "channel_error"):
            org, why = Q.origin(reason)
            self.assertEqual(org, "external", reason)
            self.assertTrue(why.strip(), "причина обязана быть названа словами")

    def test_five_hundred_family_is_theirs(self):
        for code in (500, 502, 503, 504, 599):
            self.assertEqual(Q.origin("http_%d" % code)[0], "external", code)

    def test_overload_and_their_timeout_are_theirs_even_though_4xx(self):
        for code in (408, 425, 429):
            self.assertEqual(Q.origin("http_%d" % code)[0], "external", code)

    def test_format_4xx_is_ours_and_never_retried(self):
        for code in (400, 401, 403, 404, 413, 422):
            org, why = Q.origin("http_%d" % code)
            self.assertEqual(org, "ours", code)
            self.assertIn("формат", why)
            self.assertEqual(Q.retry_kind(org)[0], "none")

    def test_paid_attempts_are_spent_not_refused(self):
        for reason in ("answer_lost", "no_status", "no_returncode", "accepted_no_answer", "empty_body"):
            self.assertEqual(Q.origin(reason)[0], "spent", reason)

    def test_our_defects_are_ours(self):
        for reason in ("no_credentials", "outbound_guard", "answer_unreadable", "no_last_message"):
            self.assertEqual(Q.origin(reason)[0], "ours", reason)

    def test_bad_answer_is_channel_answered(self):
        for reason in ("empty_answer", "answer_too_short", "truncated_answer"):
            self.assertEqual(Q.origin(reason)[0], "channel_answered", reason)

    def test_unknown_reason_fails_closed_into_no_retry(self):
        org, why = Q.origin("совершенно_новая_причина")
        self.assertEqual(org, "ours")
        self.assertIn("fail-closed", why)
        self.assertEqual(Q.retry_kind(org)[0], "none")

    def test_empty_reason_is_not_a_retry_ticket(self):
        self.assertEqual(Q.origin("")[0], "ours")

    def test_ok_is_not_a_failure_at_all(self):
        with self.assertRaises(Q.ReviewOutboxError) as ctx:
            Q.origin("ok")
        self.assertEqual(ctx.exception.reason, "not_a_failure")

    def test_every_live_reason_of_the_sender_has_an_answer_here(self):
        """Все причины ЖИВОГО отправщика разобраны поимённо, а не через fail-closed.

        Список снят с исходника ``review_send.py`` (литералы вторым аргументом
        ``make``), а не выписан по памяти: причина, добавленная там завтра,
        уронит этот тест — и это единственный способ узнать о ней вовремя.
        """
        with io.open(os.path.join(HERE, "review_send.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name not in ("make", "_verdict"):
                continue
            args = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            for a in args:
                if a.value in review_send.OUTCOMES:
                    idx = node.args.index(a)
                    if idx + 1 < len(node.args) and isinstance(node.args[idx + 1], ast.Constant):
                        found.add(node.args[idx + 1].value)
        known = set(Q._EXTERNAL) | set(Q._SPENT) | set(Q._OURS) | set(Q._ANSWERED) | {"ok"}
        unhandled = {r for r in found if isinstance(r, str) and not r.startswith("http_")} - known
        self.assertEqual(unhandled, set(), "причины отправщика без разбора: %s" % sorted(unhandled))
        self.assertGreaterEqual(len(found), 10, "разбор исходника не нашёл причин — тест ослеп")


# ═════════════════════════ 2. чем повторяем ═════════════════════════


class TestRetryKind(unittest.TestCase):

    def test_external_is_resend(self):
        self.assertEqual(Q.retry_kind("external")[0], "resend")

    def test_spent_with_task_id_is_refetch_not_resend(self):
        kind, why = Q.retry_kind("spent", task_id="tsk_42")
        self.assertEqual(kind, "refetch")
        self.assertIn("ЗАБОРОМ", why)
        self.assertIn("ноль", why)

    def test_spent_without_task_id_has_no_retry_at_all(self):
        kind, why = Q.retry_kind("spent")
        self.assertEqual(kind, "none")
        self.assertIn("вторую работу", why)

    def test_ours_and_answered_never_retry(self):
        self.assertEqual(Q.retry_kind("ours")[0], "none")
        self.assertEqual(Q.retry_kind("channel_answered")[0], "none")

    def test_invalid_origin_is_loud(self):
        with self.assertRaises(Q.ReviewOutboxError):
            Q.retry_kind("нечто")


# ═════════════════════════ 3. числа ═════════════════════════


class TestNumbers(unittest.TestCase):
    """Предел повторов и паузы — числами, и числа обоснованы соседями."""

    def test_limit_is_three_attempts(self):
        self.assertEqual(Q.MAX_ATTEMPTS, 3)
        self.assertEqual(Q.MAX_ATTEMPTS, len(Q.RESEND_PAUSES_SEC) + 1)
        self.assertEqual(len(Q.REFETCH_PAUSES_SEC), len(Q.RESEND_PAUSES_SEC))

    def test_pauses_named_by_number(self):
        self.assertEqual(Q.RESEND_PAUSES_SEC, (300, 900))
        self.assertEqual(Q.REFETCH_PAUSES_SEC, (600, 1800))
        self.assertEqual(Q.pause_sec("resend", 2), 300)
        self.assertEqual(Q.pause_sec("resend", 3), 900)
        self.assertIsNone(Q.pause_sec("resend", 4))
        self.assertEqual(Q.pause_sec("refetch", 2), 600)
        self.assertEqual(Q.pause_sec("refetch", 3), 1800)
        self.assertIsNone(Q.pause_sec("refetch", 4))

    def test_refetch_window_covers_the_measured_half_hour(self):
        """Полчаса нетерминального состояния задачи Manus (замер 76b4447)."""
        self.assertGreater(sum(Q.REFETCH_PAUSES_SEC), 30 * 60)

    def test_windows_fit_inside_review_auto_hour(self):
        """Оба окна обязаны уместиться внутри часа слоя поводов.

        Иначе повтор ступени A начнётся раньше, чем кончится наш, и один пакет
        уедет дважды. Литералы модуля сверяются с ЖИВЫМИ константами соседей —
        разойдутся, покраснеет здесь, а не в проде.
        """
        self.assertEqual(Q.REVIEW_AUTO_RETRY_AFTER_SEC, review_auto.RETRY_AFTER_SEC)
        self.assertEqual(Q.SEND_TIMEOUT_SEC, review_send_run.DEFAULT_TIMEOUT)
        worst_resend = sum(Q.RESEND_PAUSES_SEC) + len(Q.RESEND_PAUSES_SEC) * Q.SEND_TIMEOUT_SEC
        self.assertEqual(worst_resend, 3000)
        self.assertLess(worst_resend, Q.REVIEW_AUTO_RETRY_AFTER_SEC)
        self.assertLess(sum(Q.REFETCH_PAUSES_SEC), Q.REVIEW_AUTO_RETRY_AFTER_SEC)


# ═════════════════════════ 4. запись очереди ═════════════════════════


class TestRecord(unittest.TestCase):

    def rec(self, reason="http_503", task_id=None, moment=None):
        return Q.new_record(pack="docs/review_outbox/p.md", channel="manus", send_date="2026-09-01",
                            reason=reason, at=moment or at(12), task_id=task_id)

    def test_first_attempt_is_already_counted(self):
        rec = self.rec()
        self.assertEqual(rec["attempts"], 1)
        self.assertEqual(rec["state"], "queued")
        self.assertEqual(Q.parse_iso(rec["next_at"]), at(12) + datetime.timedelta(seconds=300))

    def test_key_holds_both_pack_and_channel(self):
        a = Q.key("docs/review_outbox/p.md", "manus")
        b = Q.key("docs/review_outbox/p.md", "codex")
        c = Q.key("docs/review_outbox/q.md", "manus")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_three_failures_exhaust_and_never_say_done(self):
        rec = self.rec()
        rec = Q.advance(rec, reason="http_500", at=at(12, 5))
        self.assertEqual(rec["attempts"], 2)
        self.assertEqual(rec["state"], "queued")
        self.assertEqual(Q.parse_iso(rec["next_at"]), at(12, 5) + datetime.timedelta(seconds=900))
        rec = Q.advance(rec, reason="http_500", at=at(12, 20))
        self.assertEqual(rec["attempts"], 3)
        self.assertEqual(rec["state"], "exhausted")
        self.assertIsNone(rec["next_at"])
        outcome, title, why = Q.outcome(rec)
        self.assertEqual(outcome, "unknown")
        self.assertEqual(title, "НЕИЗВЕСТНО")
        self.assertIn("http_500", why)

    def test_ours_is_exhausted_on_the_spot_without_a_single_retry(self):
        rec = self.rec(reason="http_400")
        self.assertEqual(rec["state"], "exhausted")
        self.assertEqual(rec["kind"], "none")
        self.assertIsNone(rec["next_at"])
        self.assertEqual(Q.outcome(rec)[0], "unknown")

    def test_spent_without_task_id_is_exhausted_on_the_spot(self):
        rec = self.rec(reason="accepted_no_answer")
        self.assertEqual(rec["origin"], "spent")
        self.assertEqual(rec["kind"], "none")
        self.assertEqual(rec["state"], "exhausted")

    def test_spent_with_task_id_waits_the_wider_pause(self):
        rec = self.rec(reason="accepted_no_answer", task_id="tsk_7")
        self.assertEqual(rec["kind"], "refetch")
        self.assertEqual(Q.parse_iso(rec["next_at"]), at(12) + datetime.timedelta(seconds=600))

    def test_origin_is_reconsidered_on_every_attempt(self):
        """503 на первом заходе и 400 на втором обязаны ОСТАНОВИТЬ повтор."""
        rec = Q.advance(self.rec("http_503"), reason="http_400", at=at(12, 6))
        self.assertEqual(rec["origin"], "ours")
        self.assertEqual(rec["state"], "exhausted")
        self.assertEqual(rec["attempts"], 2)

    def test_advance_does_not_mutate_the_record_in_place(self):
        rec = self.rec()
        before = json.dumps(rec, sort_keys=True)
        Q.advance(rec, reason="http_500", at=at(12, 5))
        self.assertEqual(json.dumps(rec, sort_keys=True), before)

    def test_due_is_false_before_the_pause_and_true_after(self):
        rec = self.rec()
        ok, why = Q.due(rec, at(12, 4))
        self.assertFalse(ok)
        self.assertIn("рано", why)
        ok, why = Q.due(rec, at(12, 5))
        self.assertTrue(ok)
        self.assertIn("попытка 2 из 3", why)

    def test_due_is_false_for_broken_stamp_rather_than_immediate(self):
        rec = self.rec()
        rec["next_at"] = "не время"
        ok, why = Q.due(rec, at(23))
        self.assertFalse(ok)
        self.assertIn("не разобрано", why)

    def test_closed_record_answers_answered(self):
        rec = Q.close(self.rec(), at=at(13))
        self.assertEqual(Q.outcome(rec)[0], "answered")
        self.assertEqual(rec["state"], "closed")

    def test_no_state_ever_yields_done(self):
        """Перебор ВСЕХ состояний: «готово» есть ровно у одного — у ответа."""
        seen = {}
        rec = self.rec()
        for state in Q.STATES:
            probe = dict(rec)
            probe["state"] = state
            if state == "closed":
                probe["closed_why"] = "канал ответил"
            seen[state] = Q.outcome(probe)[0]
        self.assertEqual(seen, {"queued": "unknown", "exhausted": "unknown", "closed": "answered"})

    def test_naive_time_is_refused(self):
        with self.assertRaises(Q.ReviewOutboxError):
            Q.new_record(pack="p.md", channel="manus", send_date="2026-09-01", reason="http_500",
                         at=datetime.datetime(2026, 9, 1, 12, 0, 0))


# ═════════════════════════ 5. отрицательная проба: 500 ТРИЖДЫ ═════════════════════════


class _FiveHundred(BaseHTTPRequestHandler):
    """Канал, который лежит. Отвечает 500 столько раз, сколько его спросят."""

    def do_POST(self):                              # noqa: N802 — имя диктует BaseHTTPRequestHandler
        self.server.hits += 1
        body = b'{"error":"internal","message":"upstream is down"}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                      # тишина в выводе теста
        return


class TestDeadChannelFiveHundredThrice(unittest.TestCase):
    """Канал отвечает 500 три раза подряд — пакет цел, исход «неизвестно».

    Сервер ЖИВОЙ и на петле, а порт берётся у ядра (0) и не назначается: жёсткая
    константа однажды оказалась бы занятой чужой службой, и отрицательная проба
    молча стала бы положительной — класс, уже пойманный ступенью 2.
    """

    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _FiveHundred)
        self.srv.hits = 0
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        self.tmp = tempfile.mkdtemp(prefix="outbox_500_")
        self.pack = os.path.join(self.tmp, "pack.md")
        with io.open(self.pack, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# ПАКЕТ ВТОРОГО МНЕНИЯ\n\nтекст пакета\n")

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _one_live_attempt(self):
        facts = review_send_run.send_manus(
            "исходящее", key="not-a-real-key", base=self.base, path="/v1/tasks",
            timeout=10, wait=0, chunk_chars=0,
        )
        verdict, _answer = review_send.classify_manus(
            channel_target=facts["target"], request_sent=facts["request_sent"],
            transport_error=facts["transport_error"], status=facts["status"], body=facts["body"],
            pack_name="pack.md", pack_sha256="deadbeef", prompt_sha256="cafe",
            send_date="2026-09-01",
        )
        return verdict

    def test_pack_survives_three_live_five_hundreds_and_outcome_is_unknown(self):
        rec = None
        moments = [at(12), at(12, 5), at(12, 20)]
        for i, moment in enumerate(moments):
            verdict = self._one_live_attempt()
            self.assertEqual(verdict["outcome"], "refused")
            self.assertEqual(verdict["reason"], "http_500", "живой 500 обязан приехать кодом, а не обрывом")
            if rec is None:
                rec = Q.new_record(pack="docs/review_outbox/pack.md", channel="manus",
                                   send_date="2026-09-01", reason=verdict["reason"], at=moment)
            else:
                rec = Q.advance(rec, reason=verdict["reason"], at=moment)
            self.assertEqual(rec["origin"], "external", "500 — их отказ, а не наш")
            self.assertEqual(rec["attempts"], i + 1)

        self.assertEqual(self.srv.hits, 3, "канал обязан быть спрошен ровно трижды")
        # 1. Пакет ЦЕЛ.
        self.assertTrue(os.path.exists(self.pack), "пакет не смеет пропасть")
        with io.open(self.pack, encoding="utf-8") as fh:
            self.assertIn("текст пакета", fh.read())
        # 2. Запись жива и названа.
        self.assertEqual(rec["state"], "exhausted")
        self.assertEqual(rec["pack"], "docs/review_outbox/pack.md")
        # 3. Исход — «неизвестно» с ПРИЧИНОЙ, и слова «готово» в нём нет.
        outcome, title, why = Q.outcome(rec)
        self.assertEqual(outcome, "unknown")
        self.assertEqual(title, "НЕИЗВЕСТНО")
        self.assertIn("http_500", why)
        # Слово «готово» в исходе есть ровно один раз и ровно в ОТРИЦАНИИ.
        self.assertIn("«готово» здесь не пишется", why)
        self.assertEqual(why.lower().count("готово"), 1)
        for bad in ("выполнено", "успех", "ответ получен"):
            self.assertNotIn(bad, why.lower())

    def test_the_silence_becomes_a_visible_line(self):
        rec = Q.new_record(pack="docs/review_outbox/pack.md", channel="manus", send_date="2026-09-01",
                           reason=self._one_live_attempt()["reason"], at=at(12))
        rec = Q.advance(rec, reason="http_500", at=at(12, 5))
        rec = Q.advance(rec, reason="http_500", at=at(12, 20))
        text = Q.audit_text(rec, queued_left=2)
        low = text.lower()
        self.assertIn("канал лежал", low)
        self.assertIn("пакет не доехал", low)
        self.assertIn("docs/review_outbox/pack.md", text)
        self.assertIn("попыток: 3 из 3", text)
        self.assertIn("http_500", text)
        self.assertIn("НЕИЗВЕСТНО", text)
        self.assertIn("review_send_run.py --pack", text, "сообщение обязано быть исполнимым без нас")
        self.assertIn("не значит «ревьюер ничего не нашёл»", low)
        self.assertNotIn("НЕ ИСПОЛНЯТЬ", text, "чужого текста здесь нет — шапка ступени D сюда не годится")


class TestAuditTextForOurOwnDefect(unittest.TestCase):

    def test_our_fault_is_not_called_a_dead_channel(self):
        rec = Q.new_record(pack="docs/review_outbox/p.md", channel="manus", send_date="2026-09-01",
                           reason="http_400", at=at(12))
        text = Q.audit_text(rec)
        self.assertIn("ПО НАШЕЙ ВИНЕ", text)
        self.assertNotIn("КАНАЛ ЛЕЖАЛ", text)
        self.assertIn("формат", text)


# ═════════════════════════ 6. руки: лоток → очередь ═════════════════════════


def _head(pack, channel, outcome, reason, rel, date="2026-09-01"):
    """Шапка захода в ЖИВОМ виде, который отдаёт `review_audit_run.answer_headers`.

    Ключей ДВА — ``pack`` (имя) и ``pack_path`` (путь), — потому что их два у
    живого разборщика. Мок с одним `pack` уже соврал однажды: очередь брала имя,
    повтор упирался в `pack_missing`, и сухой прогон печатал владельцу
    неисполнимую команду. Правило-класс «мок обязан копировать живой формат».
    """
    return {"rel": rel, "pack": pack.rsplit("/", 1)[-1], "pack_path": pack, "channel": channel,
            "send_date": date, "outcome": outcome, "reason": reason,
            "answered": outcome == "answered"}


class TestRunSync(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="outbox_run_")
        self.state = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tick(self, heads, **kw):
        return R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False,
                      clock=kw.pop("clock", lambda: at(13)), **kw)

    def test_bootstrap_holds_history_and_the_hold_is_read_on_the_second_turn(self):
        """Урок ступени D: дефект бутстрапа живёт со ВТОРОГО оборота."""
        heads = [_head("docs/review_outbox/a.md", "manus", "refused", "http_400", "docs/review_inbox/a-manus.md")]
        first = self.tick(heads)
        self.assertTrue(first["bootstrap"])
        self.assertEqual(len(first["added"]), 1)
        self.assertEqual(len(first["exhausted"]), 1)
        self.assertEqual(first["announce_texts"], [], "историю бутстрапа показывать нельзя")
        self.assertEqual(len(first["held"]), 1)

        second = self.tick(heads, clock=lambda: at(14))
        self.assertFalse(second["bootstrap"])
        self.assertEqual(second["announce_texts"], [], "и на втором обороте тоже — отметка ЧИТАЕТСЯ")
        self.assertEqual(len(second["held"]), 1)

        forced = self.tick(heads, clock=lambda: at(15), force=True)
        self.assertEqual(len(forced["announce_texts"]), 1, "--force снимает отметку")

    def test_failure_after_bootstrap_is_shown(self):
        self.tick([])
        heads = [_head("docs/review_outbox/b.md", "manus", "refused", "http_503", "docs/review_inbox/b-manus.md")]
        rep = self.tick(heads, clock=lambda: at(14))
        self.assertEqual(len(rep["added"]), 1)
        self.assertEqual(rep["queued"], 1, "503 обязан ЖДАТЬ повтора, а не исчерпаться")
        self.assertEqual(rep["announce_texts"], [], "пока ждёт — показывать нечего")

    def test_answer_closes_the_record(self):
        self.tick([])
        bad = [_head("docs/review_outbox/c.md", "manus", "refused", "http_503", "docs/review_inbox/c-manus.md")]
        self.tick(bad, clock=lambda: at(14))
        good = [_head("docs/review_outbox/c.md", "manus", "answered", "ok", "docs/review_inbox/c-manus.md")]
        rep = self.tick(good, clock=lambda: at(15))
        self.assertEqual(len(rep["closed"]), 1)
        self.assertEqual(rep["queued"], 0)
        self.assertEqual(len(rep["exhausted"]), 0)

    def test_two_channels_of_one_pack_are_two_records(self):
        self.tick([])
        heads = [
            _head("docs/review_outbox/d.md", "manus", "refused", "http_503", "docs/review_inbox/d-manus.md"),
            _head("docs/review_outbox/d.md", "codex", "answered", "ok", "docs/review_inbox/d-codex.md"),
        ]
        rep = self.tick(heads, clock=lambda: at(14))
        self.assertEqual(len(rep["added"]), 1, "ответивший канал записи не заводит")
        self.assertEqual(rep["queued"], 1)

    def test_state_survives_a_lost_registry(self):
        self.tick([])
        heads = [_head("docs/review_outbox/e.md", "manus", "refused", "http_503", "docs/review_inbox/e-manus.md")]
        self.tick(heads, clock=lambda: at(14))
        os.remove(self.state)
        rep = self.tick(heads, clock=lambda: at(15))
        self.assertEqual(rep["queued"] + len(rep["exhausted"]), 1, "пакет не теряется вместе с реестром")

    def test_journal_line_is_empty_on_an_empty_turn(self):
        rep = self.tick([])
        self.assertEqual(rep["line"], "")

    def test_journal_line_names_the_exhausted(self):
        rep = self.tick([_head("docs/review_outbox/f.md", "manus", "refused", "http_400",
                               "docs/review_inbox/f-manus.md")])
        self.assertIn("исчерпано 1", rep["line"])
        self.assertIn("НЕИЗВЕСТНО", rep["line"])


class TestRunRetry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="outbox_retry_")
        self.state = os.path.join(self.tmp, "state.json")
        os.makedirs(os.path.join(self.tmp, "docs", "review_outbox"))
        with io.open(os.path.join(self.tmp, "docs", "review_outbox", "g.md"), "w", encoding="utf-8") as fh:
            fh.write("пакет")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retry_uses_the_live_launch_format_of_the_sender(self):
        rec = Q.new_record(pack="docs/review_outbox/g.md", channel="manus", send_date="2026-09-01",
                           reason="http_503", at=at(12))
        argv = R.retry_argv(rec, root=self.tmp)
        self.assertIn("review_send_run.py", argv[1])
        self.assertIn("--pack", argv)
        self.assertIn("--channel", argv)
        self.assertIn("manus", argv)

    def test_refetch_asks_for_the_task_not_for_the_pack(self):
        rec = Q.new_record(pack="docs/review_outbox/g.md", channel="manus", send_date="2026-09-01",
                           reason="accepted_no_answer", at=at(12), task_id="tsk_9")
        argv = R.retry_argv(rec, root=self.tmp)
        self.assertIn("--manus-task", argv)
        self.assertIn("tsk_9", argv)
        self.assertNotIn("--pack", argv, "повторная ОТПРАВКА оплаченного захода запрещена")

    def test_the_queue_keeps_the_path_so_the_retry_can_open_the_pack(self):
        """Живой сухой прогон 02.09 поймал: очередь держала ИМЯ, а не путь.

        Повтор упирался бы в `pack_missing` на каждом пакете, а владелец получал
        бы в тему Аудит команду, которую нельзя выполнить.
        """
        heads = [_head("docs/review_outbox/g.md", "manus", "refused", "http_503",
                       "docs/review_inbox/g-manus.md")]
        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False,
                     clock=lambda: at(12))
        rec = rep["added"][0]
        self.assertEqual(rec["pack"], "docs/review_outbox/g.md")
        argv = R.retry_argv(rec, root=self.tmp)
        self.assertTrue(os.path.exists(argv[argv.index("--pack") + 1]),
                        "повтор обязан указывать на СУЩЕСТВУЮЩИЙ файл")
        self.assertIn("docs/review_outbox/g.md", Q.audit_text(rec))

    def test_live_headers_carry_the_path(self):
        """Живой разборщик отдаёт `pack_path` — если перестанет, покраснеет здесь."""
        heads, _skipped = R.lotok(root=HERE)
        if not heads:
            self.skipTest("лоток пуст")
        self.assertTrue(all(h.get("pack_path") for h in heads))
        # КАТАЛОГ обязателен, а вот какой именно — нет: живой замер 02.09 нашёл
        # 2 захода из 32, чей пакет собирался не в лотке, а в `tmp/v0_accept_0901/`
        # (независимая приёмка V0). Требовать `docs/review_outbox/` значило бы
        # записать в инвариант привычку вместо правила; такой пакет к моменту
        # повтора может уже не существовать, и это законный `pack_missing`.
        self.assertTrue(all("/" in h["pack_path"] for h in heads))
        outside = [h["pack_path"] for h in heads if not h["pack_path"].startswith("docs/review_outbox/")]
        self.assertLess(len(outside), len(heads), "все пакеты вне лотка — разбор шапок сломался")

    def test_missing_pack_is_named_not_retried(self):
        rec = Q.new_record(pack="docs/review_outbox/gone.md", channel="manus", send_date="2026-09-01",
                           reason="http_503", at=at(12))
        ok, reason, detail = R.retry_one(rec, root=self.tmp, runner=lambda *a, **k: self.fail("не звать канал"))
        self.assertFalse(ok)
        self.assertEqual(reason, "pack_missing")
        self.assertEqual(Q.origin(reason)[0], "ours")

    def test_one_retry_per_turn_by_default(self):
        heads = [
            _head("docs/review_outbox/g.md", "manus", "refused", "http_503", "docs/review_inbox/g-manus.md"),
            _head("docs/review_outbox/h.md", "manus", "refused", "http_503", "docs/review_inbox/h-manus.md"),
        ]
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, clock=lambda: at(12))
        calls = []

        class _Done:
            returncode = 3
            stdout = b"mimo"
            stderr = b""

        def runner(argv, **kw):
            calls.append(argv)
            return _Done()

        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, clock=lambda: at(12, 10),
                     runner=runner)
        self.assertEqual(len(calls), 1, "за оборот ровно один повтор — виток демона синхронный")
        self.assertEqual(len(rep["retried"]), 1)

    def test_failed_retry_costs_an_attempt(self):
        """Обрыв, который не стоит попытки, — это бесконечный повтор (инвариант ступени A)."""
        heads = [_head("docs/review_outbox/g.md", "manus", "refused", "http_503", "docs/review_inbox/g-manus.md")]
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, clock=lambda: at(12))

        class _Done:
            returncode = 4
            stdout = b""
            stderr = b""

        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, clock=lambda: at(12, 10),
                     runner=lambda *a, **k: _Done())
        rec = rep["queued_recs"][0] if rep["queued_recs"] else rep["exhausted"][0]
        self.assertEqual(rec["attempts"], 2)

    def test_retry_that_cannot_even_launch_is_named(self):
        rec = Q.new_record(pack="docs/review_outbox/g.md", channel="manus", send_date="2026-09-01",
                           reason="http_503", at=at(12))

        def boom(*a, **k):
            raise OSError("нет такого файла")

        ok, reason, detail = R.retry_one(rec, root=self.tmp, runner=boom)
        self.assertFalse(ok)
        self.assertEqual(reason, "retry_launch_failed")
        self.assertEqual(Q.origin(reason)[0], "ours")

    def test_daily_retry_budget_stops_the_flood(self):
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        heads = [_head("docs/review_outbox/g.md", "manus", "refused", "http_503", "docs/review_inbox/g-manus.md")]
        R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, clock=lambda: at(12))
        st = R.read_state(self.state)
        st["day"] = {"2026-09-01": {"retries": R.RETRY_BUDGET}}
        R.write_state(st, self.state)

        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, clock=lambda: at(12, 10),
                     runner=lambda *a, **k: self.fail("потолок обязан остановить повтор"))
        self.assertEqual(rep["retried"], [])
        self.assertTrue(any("потолок" in why for _k, why in rep["retry_skipped"]))


class TestAnnounceBudgetAndAddress(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="outbox_ann_")
        self.state = os.path.join(self.tmp, "state.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unset_topic_sends_nothing_at_all(self):
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        heads = [_head("docs/review_outbox/i.md", "manus", "refused", "http_400", "docs/review_inbox/i-manus.md")]
        sent = []
        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, send=True,
                     clock=lambda: at(12), daemon=type("D", (), {"AUDIT_TOPIC": 0})(),
                     sender=lambda t, tid: sent.append((t, tid)) or ("x", True, "1"))
        self.assertEqual(sent, [], "ненастроенная тема = молчание, а не «куда-нибудь»")
        self.assertEqual(rep["topic"], 0)
        self.assertIn("НЕ НАСТРОЕНА", rep["topic_why"])

    def test_everything_goes_to_the_named_topic_only(self):
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        heads = [_head("docs/review_outbox/j.md", "manus", "refused", "http_400", "docs/review_inbox/j-manus.md")]
        sent = []
        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, send=True,
                     clock=lambda: at(12), daemon=type("D", (), {"AUDIT_TOPIC": 161})(),
                     sender=lambda t, tid: sent.append((t, tid)) or ("topic:161", True, "9100"))
        self.assertEqual([tid for _t, tid in sent], [161])
        self.assertEqual(len(rep["announced"]), 1)

    def test_shown_once_not_twice(self):
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        heads = [_head("docs/review_outbox/k.md", "manus", "refused", "http_400", "docs/review_inbox/k-manus.md")]
        sent = []
        kw = dict(root=self.tmp, state_path=self.state, headers=heads, retry=False, send=True,
                  daemon=type("D", (), {"AUDIT_TOPIC": 161})(),
                  sender=lambda t, tid: sent.append(t) or ("topic:161", True, "1"))
        R.tick(clock=lambda: at(12), **kw)
        R.tick(clock=lambda: at(13), **kw)
        self.assertEqual(len(sent), 1)

    def test_daily_announce_budget_holds_the_rest_by_name(self):
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        heads = [_head("docs/review_outbox/%d.md" % i, "manus", "refused", "http_400",
                       "docs/review_inbox/%d-manus.md" % i) for i in range(5)]
        sent = []
        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, send=True,
                     clock=lambda: at(12), daemon=type("D", (), {"AUDIT_TOPIC": 161})(),
                     sender=lambda t, tid: sent.append(t) or ("topic:161", True, "1"))
        self.assertEqual(len(sent), R.ANNOUNCE_BUDGET)
        self.assertEqual(len(rep["held"]), 5 - R.ANNOUNCE_BUDGET)
        self.assertTrue(all("потолок" in why for _k, why in rep["held"]))

    def test_refused_delivery_is_reported_not_swallowed(self):
        R.tick(root=self.tmp, state_path=self.state, headers=[], retry=False, clock=lambda: at(11))
        heads = [_head("docs/review_outbox/l.md", "manus", "refused", "http_400", "docs/review_inbox/l-manus.md")]
        rep = R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, send=True,
                     clock=lambda: at(12), daemon=type("D", (), {"AUDIT_TOPIC": 161})(),
                     sender=lambda t, tid: ("topic:161", False, "code=400 chat not found"))
        self.assertEqual(len(rep["announce_failed"]), 1)
        self.assertIn("chat not found", rep["announce_failed"][0]["why"])
        st = R.read_state(self.state)
        self.assertNotIn("manus|l.md", st["announced"], "неудачный показ не смеет считаться показанным")


class TestCorruptRegistryIsNamed(unittest.TestCase):
    """Реестр ПОВРЕЖДЁН — очередь обязана СКАЗАТЬ, а не начать с нуля молча.

    Замер 02.09 (проба детерминизма с отодвинутым боевым состоянием) поймал ровно
    этот разрыв: битый файл давал ту же картину, что честный первый оборот —
    ``bootstrap=True``, счётчик попыток с единицы, — и слова «повреждён» не было
    нигде. При живом лотке это стоило только счётчика (записи собирались заново,
    14 → 14), а при ПУСТОМ лотке очередь уходила с 14 записей на 0 и отдавала
    ПУСТУЮ строку-индекс. Здесь проверяется, что она теперь говорит.

    Порча берётся ТРЕХ ВИДОВ, потому что живой диск ломает по-разному: обрыв
    файла посередине, валидный JSON не того вида, и разобранный словарь с
    испорченным разделом. Один вид проверял бы одну ветку из трёх.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="outbox_corrupt_")
        self.state = os.path.join(self.tmp, "state.json")
        self.heads = [_head("docs/review_outbox/m%d.md" % i, "manus", "refused", "http_400",
                            "docs/review_inbox/m%d-manus.md" % i) for i in range(3)]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tick(self, heads, **kw):
        kw.setdefault("clock", lambda: at(12))
        return R.tick(root=self.tmp, state_path=self.state, headers=heads, retry=False, **kw)

    def _break(self, text):
        with io.open(self.state, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_missing_file_is_not_damage_and_stays_silent(self):
        """Файла НЕТ — это первый оборот, а не беда: молчание остаётся молчанием.

        Замок от шума: назвать бедой отсутствие реестра значило бы кричать на
        каждом новом развёртывании, и настоящая порча утонула бы в этом крике.
        """
        state, why = R.read_state_why(self.state)
        self.assertEqual(why, "")
        self.assertEqual(state["packs"], {})
        self.assertEqual(self._tick([])["line"], "", "пустой оборот без реестра — не новость")

    def test_truncated_json_is_named_in_report_and_index(self):
        self._tick(self.heads)
        with io.open(self.state, "rb") as fh:
            raw = fh.read()
        with io.open(self.state, "wb") as fh:
            fh.write(raw[: len(raw) // 2])
        rep = self._tick(self.heads, clock=lambda: at(13))
        self.assertIn("ПОВРЕЖДЁН", rep["state_why"])
        self.assertIn("ПОВРЕЖДЁН", rep["line"], "порча обязана доехать до журнала, а не остаться в stdout")

    def test_damage_on_an_empty_lotok_still_speaks(self):
        """ГЛАВНЫЙ случай: восстанавливать неоткуда — и молчать тем более нельзя.

        Именно здесь до правки очередь уходила в ноль без единого слова: лоток
        пуст, значит ни одной записи не добавится, а строка-индекс пустого
        оборота по правилу журнала-индекса пуста.
        """
        self._tick(self.heads)
        self._break("{ это не json")
        rep = self._tick([], clock=lambda: at(13))
        self.assertEqual(rep["queued"], 0)
        self.assertEqual(len(rep["exhausted"]), 0, "лоток пуст — собирать не из чего")
        self.assertTrue(rep["line"], "очередь ушла с записей на ноль и промолчала — это и есть дефект")
        self.assertIn("ПОВРЕЖДЁН", rep["line"])

    def test_valid_json_of_the_wrong_shape_is_damage_too(self):
        self._break("[1, 2, 3]\n")
        state, why = R.read_state_why(self.state)
        self.assertIn("ПОВРЕЖДЁН", why)
        self.assertIn("list", why, "вид найденного назван — иначе чинить нечего")
        self.assertEqual(state["packs"], {})

    def test_partial_damage_keeps_what_survived(self):
        """Раздел испорчен — уцелевшие разделы НЕ выбрасываются вместе с ним."""
        self._tick(self.heads)
        with io.open(self.state, encoding="utf-8") as fh:
            data = json.load(fh)
        data["day"] = "не словарь"
        with io.open(self.state, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        state, why = R.read_state_why(self.state)
        self.assertIn("ЧАСТИЧНО", why)
        self.assertIn("day", why)
        self.assertEqual(len(state["packs"]), 3, "испорченный раздел не смеет унести целые")

    def test_packs_survive_damage_while_the_lotok_lives(self):
        """Порча стоит счётчика попыток, а НЕ пакетов: лоток пересобирает очередь."""
        before = self._tick(self.heads)
        self.assertEqual(len(before["exhausted"]), 3)
        self._break("{ это не json")
        after = self._tick(self.heads, clock=lambda: at(13))
        self.assertEqual(len(after["exhausted"]), 3, "пакет не теряется вместе с реестром")

    def test_damage_never_turns_into_done(self):
        """Ни на одной дороге порча не рождает «готово»: исход только «неизвестно»."""
        self._tick(self.heads)
        self._break("{ это не json")
        rep = self._tick(self.heads, clock=lambda: at(13))
        outs = {Q.outcome(r)[0] for r in rep["exhausted"]}
        self.assertEqual(outs, {"unknown"})
        self.assertNotIn("answered", outs)


# ═════════════════════════ 7. живой корпус 01.09 ═════════════════════════


class TestLiveCorpus20260901(unittest.TestCase):
    """Реплей ЖИВЫХ отказов 01.09 — тех самых, что сегодня не доехали в Manus.

    Числа снимаются с настоящих файлов лотка, а не выписываются в тест: корпус
    меняется каждый день, и застывшее число превратилось бы в голден, врущий про
    вчерашний день.
    """

    def setUp(self):
        heads, _skipped = R.lotok(root=HERE)
        self.manus = [h for h in heads if h.get("channel") == "manus" and h.get("send_date") == "2026-09-01"]
        if not self.manus:
            self.skipTest("живого корпуса 01.09 в лотке нет")

    def test_every_live_failure_gets_a_named_origin(self):
        for h in self.manus:
            if h.get("answered"):
                continue
            org, why = Q.origin(h.get("reason") or "")
            self.assertIn(org, Q.ORIGINS)
            self.assertTrue(why.strip())

    def test_todays_manus_failures_are_ours_not_the_channels(self):
        """Главный честный вывод дня: канал 01.09 НЕ лежал — не помещался пакет."""
        fails = [h for h in self.manus if not h.get("answered")]
        origins = {}
        for h in fails:
            origins[Q.origin(h["reason"])[0]] = origins.get(Q.origin(h["reason"])[0], 0) + 1
        self.assertGreater(len(fails), 0)
        self.assertEqual(origins.get("external", 0), 0,
                         "внешних отказов 01.09 не было — повтор отправкой закрыл бы ноль из них")
        self.assertGreater(origins.get("ours", 0), 0)

    def test_accepted_no_answer_cannot_be_refetched_without_a_task_id(self):
        """Известная дыра, названная числом: идентификатора задачи в лотке нет."""
        spent = [h for h in self.manus if not h.get("answered") and Q.origin(h["reason"])[0] == "spent"]
        for h in spent:
            kind, why = Q.retry_kind("spent", task_id=None)
            self.assertEqual(kind, "none")
            self.assertIn("идентификатора задачи нет", why)


# ═════════════════════════ 8. инварианты ═════════════════════════


class TestInvariants(unittest.TestCase):

    def _tree(self, name):
        with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
            return ast.parse(fh.read())

    def test_pure_module_reads_no_clock_no_disk_no_net(self):
        """OUTBOX_PURE: суждения не смеют зависеть от часов, диска и сети.

        Иначе повторится класс, уже пойманный слоем ожиданий: функция, читающая
        время сама, в тесте неуправляема, а в проде судит по стенным часам
        спящей машины.
        """
        tree = self._tree("review_outbox_queue.py")
        banned_mod = {"os", "subprocess", "urllib", "socket", "requests", "time", "io", "shutil"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotIn(a.name.split(".")[0], banned_mod, "чистый модуль импортировал %s" % a.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], banned_mod, node.module)
        # Проверяем ВЫЗОВЫ, а не всякое вхождение имени: параметр `now` у чистой
        # функции — ровно то, чего мы хотим (момент называет вызывающий), и
        # запрет по подстроке ловил бы его вместо `datetime.datetime.now()`.
        banned_call = {"now", "utcnow", "today", "time", "monotonic", "open", "sleep"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            self.assertNotIn(name, banned_call, "чистый модуль ЗОВЁТ %s()" % name)

    def test_the_queue_never_touches_the_pc_task_queue(self):
        """Ступень F очередь ПК не трогает вовсе — ни чтением, ни записью."""
        for name in ("review_outbox_queue.py", "review_outbox_queue_run.py"):
            src = io.open(os.path.join(HERE, name), encoding="utf-8").read()
            for bad in ("enqueue_pc_task", "claim_task", "complete_task", "get_pending", "update_task"):
                self.assertNotIn(bad, src, "%s трогает очередь ПК словом %s" % (name, bad))

    def test_nothing_is_ever_deleted(self):
        for name in ("review_outbox_queue.py", "review_outbox_queue_run.py"):
            src = io.open(os.path.join(HERE, name), encoding="utf-8").read()
            for bad in ("os.remove", "os.unlink", "shutil.rmtree", "os.rmdir"):
                self.assertNotIn(bad, src, "%s удаляет: %s" % (name, bad))

    def test_no_secret_is_read_here(self):
        """Ключ канала берёт САМ отправщик в своём процессе (запрет класса 328)."""
        for name in ("review_outbox_queue.py", "review_outbox_queue_run.py"):
            src = io.open(os.path.join(HERE, name), encoding="utf-8").read()
            for bad in ("load_dotenv", "MANUS_API_KEY", "BRIDGE_TOKEN", "AGENT_BOT_TOKEN"):
                self.assertNotIn(bad, src, "%s читает секрет: %s" % (name, bad))

    def test_the_word_done_is_absent_from_every_exhaustion_path(self):
        rec = Q.new_record(pack="p.md", channel="manus", send_date="2026-09-01", reason="http_500", at=at(12))
        rec = Q.advance(rec, reason="http_502", at=at(12, 5))
        rec = Q.advance(rec, reason="http_503", at=at(12, 20))
        self.assertEqual(Q.outcome(rec)[0], "unknown")
        self.assertEqual(Q.audit_text(rec).count("НЕИЗВЕСТНО"), 1)


class TestStageFWiring(unittest.TestCase):
    """Врезка в демона — по ИСХОДНИКУ, без импорта.

    Импортировать `pc_orchestrator` ради семи проверок нельзя: известный класс —
    тест, зовущий живую функцию демона, пишет в БОЕВОЙ лог и БОЕВОЕ состояние.
    Здесь довольно текста файла, и он же — единственный источник правды о том,
    что ветка действительно стои́т в витке, а не только объявлена.
    """

    @classmethod
    def setUpClass(cls):
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_the_branch_is_called_from_the_loop_exactly_once(self):
        self.assertEqual(self.src.count("maybe_review_outbox()"), 1,
                         "ветка обязана стоять в витке ровно один раз")
        self.assertIn("def maybe_review_outbox(", self.src)

    def test_it_stands_after_stage_e_not_before(self):
        loop_e = self.src.index("maybe_recon_auto()        #")
        loop_f = self.src.index("maybe_review_outbox()     #")
        self.assertLess(loop_e, loop_f, "F читает лоток ПОСЛЕ того, как остальные его наполнили")

    def test_state_and_tick_go_through_the_state_gate(self):
        for name in ("REVIEW_OUTBOX_TICK_FILE", "REVIEW_OUTBOX_STATE_FILE"):
            idx = self.src.index("%s = " % name)
            self.assertIn("_state(", self.src[idx:idx + 120],
                          "%s обязан идти через _state: иначе тест напишет в БОЕВОЙ реестр" % name)

    def test_both_switches_exist(self):
        self.assertIn('os.environ.get("REVIEW_OUTBOX")', self.src)
        self.assertIn('_flag_forced_off("REVIEW_OUTBOX")', self.src)

    def test_self_update_knows_the_two_new_leaves(self):
        """Без этого правка ступени F не доедет до демона — он не увидит своих файлов."""
        self.assertIn('"review_outbox_queue_run.py", "review_outbox_queue.py"', self.src)

    def test_the_turn_is_fail_safe(self):
        idx = self.src.index("def maybe_review_outbox(")
        body = self.src[idx:idx + 2600]
        self.assertIn("except Exception", body, "упавший оборот не смеет ронять виток демона")
        self.assertIn("fail-safe", body)

    def test_the_daemon_branch_never_deletes_or_reads_secrets(self):
        idx = self.src.index("def maybe_review_outbox(")
        body = self.src[idx:idx + 2600]
        for bad in ("os.remove", "shutil.rmtree", "BRIDGE_TOKEN", "MANUS_API_KEY", "enqueue_pc_task"):
            self.assertNotIn(bad, body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
