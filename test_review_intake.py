# -*- coding: utf-8 -*-
"""test_review_intake.py — регресс ступени B ревью-контура (ответ канала → заявка очереди).

Пять вещей здесь важнее остальных и потому стоят отдельными классами:

* ``TestPurity`` — инвариант ``REVIEW_INTAKE_PURE``: чистый модуль решения не
  смеет завести часы, сеть, диск или ``getenv``. Проверяется обходом AST.
* ``TestLiveCorpus`` — голдены сняты с ЖИВЫХ файлов лотка ``docs/review_inbox``,
  а не с идеализированной схемы. Правило-класс полосы: формат мока = формат
  источника; сверх того здесь гоняется круг «render_answer → parse_answer», то
  есть разбор проверяется ТЕМ ЖЕ кодом, которым файл пишется.
* ``TestPremise`` — три исхода премисы и то, ради чего слой заведён: «свежо»
  исходом НЕ является, судим ЧТЕНИЕМ НАЗВАННОГО АДРЕСА.
* ``TestNotATask`` — заявка не становится задачей: три гарда демона плюс замок
  «постановка идёт мимо статуса new».
* ``TestNegativeQueue`` — ОТРИЦАТЕЛЬНАЯ ПРОБА: мост отказывает, и контур обязан
  не поставить НИЧЕГО, назвать причину и НЕ записать в реестр несделанного.

Дословные цитаты живых находок в этом файле не случайны и не украшают: тесты
детекта на этой полосе обязаны стоять на реальной фразе. Ровно поэтому корпус
проверки премис исключает собственные файлы контура (``SELF_FILES``) — иначе
голден отвечал бы сам себе.
"""
from __future__ import annotations

import ast
import io
import json
import os
import tempfile
import unittest

import review_intake as ri
import review_intake_run as run
import review_pack
import review_send

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(HERE, "docs", "review_inbox")

# Живые файлы лотка за 01.09 — на них стоит весь корпусный регресс.
LIVE_DIGEST = "2026-09-01-2026-09-01-digest-2026-09-01-codex.md"
LIVE_CHAINS = "2026-09-01-2026-09-01-chains-2026-08-31-codex.md"

# ГОЛДЕН ОТКАЗА ЖИВЁТ ФИКСТУРОЙ, А НЕ ЛОТКОМ (класс пойман живьём 01.09.2026).
# Раньше здесь стоял файл лотка `…-chain-pc-2026-09-01-72-codex.md`. Лоток — каталог,
# в который ПИШЕТ ЖИВОЙ ДЕМОН: ступень A переотправила пакет 72 после починки стражи,
# канал ответил, и файл ПЕРЕПИСАЛСЯ с `refused/outbound_guard` на `answered/ok`. Тест
# покраснел на main, ничего не сломавшись, а красный гейт значит «self-update не едет».
# Правило полосы («снимай живой ответ прода ФИКСТУРОЙ») сюда и относится: голден стоит
# на снимке, снимок неизменен, а лоток остаётся живым каталогом.
FIXTURES = os.path.join(HERE, "fixtures")
LIVE_REFUSED = "review_answer_refused_guard.live.md"    # снимок отказа стражи, 01.09

# Дословный кусок находки №2 дайджеста (тот самый дубль, что приехал двумя пакетами).
LIVE_DUP_MARK = "не заявлять «ничего не опущено»"


def _live(name):
    """Живой файл лотка ИЛИ замороженная фикстура — по месту, где он лежит."""
    path = os.path.join(INBOX, name)
    if not os.path.exists(path):
        path = os.path.join(FIXTURES, name)
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def _header(**kw):
    base = {"pack": "p.md", "pack_path": "docs/review_outbox/p.md", "pack_sha256": "a" * 64,
            "channel": "codex", "send_date": "2026-09-01", "answer_sha256": "b" * 12}
    base.update(kw)
    return base


def _rec(quote, *, kind=None, channel="codex", pack="p.md", index=1):
    head = _header(channel=channel, pack=pack)
    finding = {"index": index, "kind": kind or ri.kind_of(quote, index), "quote": quote,
               "unsplit": False}
    return ri.record(head, finding, answer_rel="docs/review_inbox/%s" % pack)


class TestPurity(unittest.TestCase):
    """REVIEW_INTAKE_PURE — чистая логика остаётся чистой."""

    BANNED_CALLS = {"now", "utcnow", "time", "monotonic", "getenv", "open", "run", "Popen", "urlopen"}
    BANNED_IMPORTS = {"os", "subprocess", "socket", "urllib", "time", "shutil", "requests"}

    def test_no_clock_no_disk_no_network_no_env(self):
        with io.open(os.path.join(HERE, "review_intake.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="review_intake.py")
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
        """Руки не решают: мера склейки и порог живут ТОЛЬКО в чистом модуле."""
        with io.open(os.path.join(HERE, "review_intake_run.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("MERGE_JACCARD =", body)
        self.assertNotIn("PHRASE_MIN_WORDS =", body)

    def test_kinds_come_from_the_pack_tail_not_from_a_hand_written_list(self):
        """Виды находок выведены из хвоста пакета — один источник правды."""
        labels = ri.kind_labels()
        self.assertEqual(len(labels), len(review_pack.TAIL_QUESTIONS))
        self.assertEqual(labels, ("УПРОЩАЕМО", "НЕ ДЕЛАТЬ ВОВСЕ", "ПЕРЕУСЛОЖНЕНО"))
        for label, question in zip(labels, review_pack.TAIL_QUESTIONS):
            self.assertIn(label, question)


class TestLiveCorpus(unittest.TestCase):
    """Голдены — ЖИВЫЕ файлы лотка, а не идеализированная схема."""

    def test_parse_live_digest_answer(self):
        head = ri.parse_answer(_live(LIVE_DIGEST))
        self.assertTrue(head["ok"])
        self.assertEqual(head["channel"], "codex")
        self.assertEqual(head["send_date"], "2026-09-01")
        self.assertEqual(head["pack"], "2026-09-01-digest-2026-09-01.md")
        self.assertEqual(head["pack_sha256"],
                         "bedc9feb27bc2b0467053cf338a1b16d6be5f3a541f33dab411bdf211556426c")
        self.assertEqual(head["answer_chars"], 1041)
        self.assertIn(LIVE_DUP_MARK, head["body"])

    def test_refused_answer_carries_no_findings_and_names_why(self):
        head = ri.parse_answer(_live(LIVE_REFUSED))
        self.assertFalse(head["ok"])
        self.assertEqual(head["outcome"], "refused")
        self.assertIn("outbound_guard", head["why"])
        self.assertEqual(ri.split_findings(head["body"]), [])

    def test_live_digest_splits_into_three_verbatim_findings(self):
        head = ri.parse_answer(_live(LIVE_DIGEST))
        findings = ri.split_findings(head["body"])
        self.assertEqual([f["index"] for f in findings], [1, 2, 3])
        self.assertEqual([f["kind"] for f in findings], list(ri.kind_labels()))
        self.assertTrue(findings[1]["quote"].startswith("2. НЕ ДЕЛАТЬ ВОВСЕ:"))
        self.assertIn(LIVE_DUP_MARK, findings[1]["quote"])
        for finding in findings:                      # дословность: цитата лежит в теле как есть
            self.assertIn(finding["quote"], head["body"])

    def test_render_then_parse_is_a_full_circle(self):
        """Разбор проверяется ТЕМ ЖЕ кодом, которым файл пишется."""
        answer = ("1. УПРОЩАЕМО: убрать лишний слой учёта.\n\n2. НЕ ДЕЛАТЬ ВОВСЕ: не заявлять "
                  "полноту дня, пока расписки приложены не ко всем цепочкам корпуса.")
        verdict, got = review_send.classify_codex(
            channel_target="codex.CMD", pack_name="2026-09-01-x.md", pack_sha256="c" * 64,
            prompt_sha256="d" * 64, send_date="2026-09-01", returncode=0,
            stdout=answer, last_message=answer, min_chars=10)
        text = review_send.render_answer(verdict, got, pack_rel="docs/review_outbox/2026-09-01-x.md")
        head = ri.parse_answer(text)
        self.assertTrue(head["ok"], head["why"])
        self.assertEqual(head["pack"], "2026-09-01-x.md")
        self.assertEqual(head["pack_sha256"], "c" * 64)
        self.assertEqual(len(ri.split_findings(head["body"])), 2)

    def test_body_with_inner_fence_survives(self):
        """Ограда переменной длины: тело с ``` внутри не рвётся на куски."""
        answer = "1. УПРОЩАЕМО: смотри код ```py\nx = 1\n``` — он лишний в этом пакете совсем."
        verdict, got = review_send.classify_codex(
            channel_target="t", pack_name="p.md", pack_sha256="e" * 64, prompt_sha256="f" * 64,
            send_date="2026-09-01", returncode=0, stdout=answer, last_message=answer, min_chars=10)
        head = ri.parse_answer(review_send.render_answer(verdict, got, pack_rel="docs/review_outbox/p.md"))
        self.assertIn("x = 1", head["body"])
        self.assertIn("```py", head["body"])

    def test_unsplit_answer_becomes_one_finding_not_zero(self):
        body = "Сплошной абзац без нумерации: слой учёта дороже своей пользы."
        findings = ri.split_findings(body)
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["unsplit"])
        self.assertEqual(findings[0]["quote"], body)


class TestAnchors(unittest.TestCase):
    """Адрес — то, что находка НАЗВАЛА сама; всё прочее — догадка."""

    def test_backticked_names_are_anchors(self):
        got = ri.anchors("одновременное хранение `claimed_commits`, `verified_commits`")
        self.assertEqual(got, ["claimed_commits", "verified_commits"])

    def test_pair_in_one_wrapper_splits(self):
        self.assertEqual(ri.anchors("`change_reason: commit_verified`"),
                         ["change_reason", "commit_verified"])

    def test_queue_numbers_are_not_addresses(self):
        quote = "Для `#77`, `#78`, `#80` содержание неизвестно"
        self.assertEqual(ri.anchors(quote), [])
        self.assertEqual(ri.refs(quote), ["77", "78", "80"])

    def test_single_word_in_guillemets_is_intonation_not_an_address(self):
        """Живой случай первого прогона: «побед» нашлось в moderation_card.py."""
        self.assertEqual(ri.anchors("два параллельных счёта «побед» по разным полосам"), [])

    def test_phrase_in_guillemets_is_an_address(self):
        self.assertEqual(ri.anchors("не заявлять «слепых пятен не заявлено»"),
                         ["слепых пятен не заявлено"])

    def test_form_tells_a_path_from_a_literal(self):
        self.assertEqual(ri.anchor_form("review_auto.py"), "path")
        self.assertEqual(ri.anchor_form("docs/review_outbox/p.md"), "path")
        self.assertEqual(ri.anchor_form("claimed_commits"), "literal")


class TestMerge(unittest.TestCase):
    """Склейка по смыслу: один вопрос владельцу, а не два."""

    def _live_claims(self):
        records = []
        for name in (LIVE_DIGEST, LIVE_CHAINS):
            head = ri.parse_answer(_live(name))
            for finding in ri.split_findings(head["body"]):
                records.append(ri.record(head, finding, answer_rel="docs/review_inbox/" + name))
        return records, ri.merge(records)

    def test_live_duplicate_from_two_packs_becomes_one_claim_with_two_sources(self):
        records, claims = self._live_claims()
        self.assertEqual(len(records), 6)
        self.assertEqual(len(claims), 5, "живой дубль обязан склеиться ровно один раз")
        merged = [c for c in claims if len(c["sources"]) > 1]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["kind"], "НЕ ДЕЛАТЬ ВОВСЕ")
        self.assertEqual(sorted(s["pack"] for s in merged[0]["sources"]),
                         ["2026-09-01-chains-2026-08-31.md", "2026-09-01-digest-2026-09-01.md"])
        for src in merged[0]["sources"]:              # каждый источник несёт СВОЮ дословную цитату
            self.assertIn("слепых пятен не заявлено", src["quote"])

    def test_near_miss_pair_is_not_merged(self):
        """Разрыв измерен: третьи пункты обоих ответов похожи, но об РАЗНОМ."""
        records, _claims = self._live_claims()
        third = [r for r in records if r["index"] == 3]
        self.assertEqual(len(third), 2)
        score = ri.jaccard(ri.meaning_words(third[0]["quote"]), ri.meaning_words(third[1]["quote"]))
        self.assertLess(score, ri.MERGE_JACCARD)
        self.assertFalse(ri.same_meaning(third[0], third[1]))

    def test_same_text_different_kind_never_merges(self):
        text = "слой учёта дороже своей пользы и не даёт независимой верификации"
        left = _rec("1. УПРОЩАЕМО: " + text)
        right = _rec("3. ПЕРЕУСЛОЖНЕНО: " + text, index=3)
        self.assertFalse(ri.same_meaning(left, right))
        self.assertEqual(len(ri.merge([left, right])), 2)

    def test_key_is_stable_against_whitespace_but_not_against_meaning(self):
        one = _rec("1. УПРОЩАЕМО: убрать   лишний  слой   учёта пакета")
        two = _rec("1. УПРОЩАЕМО: убрать лишний слой учёта пакета")
        three = _rec("1. УПРОЩАЕМО: добавить независимую проверку содержимого схемы")
        self.assertEqual(ri.claim_key(one), ri.claim_key(two))
        self.assertNotEqual(ri.claim_key(one), ri.claim_key(three))

    def test_merge_order_is_deterministic(self):
        records, claims = self._live_claims()
        self.assertEqual([c["key"] for c in ri.merge(list(reversed(records)))],
                         [c["key"] for c in claims])


class TestPremise(unittest.TestCase):
    """Жива / протухла / НЕИЗВЕСТНО — по НАЗВАННОМУ АДРЕСУ, а не по времени."""

    def test_alive_names_the_address(self):
        out = ri.premise([{"anchor": "claimed_commits", "found": True, "frozen": False,
                           "address": "review_auto.py:197"}])
        self.assertEqual(out["outcome"], ri.PREMISE_ALIVE)
        self.assertIn("review_auto.py:197", out["why"])

    def test_stale_when_every_named_address_was_checked_and_empty(self):
        out = ri.premise([{"anchor": "gone_symbol", "found": False, "frozen": False, "address": ""}])
        self.assertEqual(out["outcome"], ri.PREMISE_STALE)
        self.assertIn("gone_symbol", out["why"])

    def test_unknown_without_any_named_address(self):
        out = ri.premise([])
        self.assertEqual(out["outcome"], ri.PREMISE_UNKNOWN)
        self.assertIn("не по времени", out["why"])

    def test_frozen_only_hit_is_unknown_not_alive(self):
        """Найтись в собственном пакете — не доказательство: копия неизменна."""
        out = ri.premise([{"anchor": "[p25…p75]", "found": True, "frozen": True,
                           "address": "docs/review_outbox/2026-09-01-chain.md:129"}])
        self.assertEqual(out["outcome"], ri.PREMISE_UNKNOWN)
        self.assertIn("замороженном", out["why"])

    def test_failed_probe_never_becomes_a_green_verdict(self):
        out = ri.premise([{"anchor": "x_symbol", "found": None, "frozen": False, "address": "",
                           "detail": "git ls-files не ответил"}])
        self.assertEqual(out["outcome"], ri.PREMISE_UNKNOWN)
        self.assertIn("git ls-files", out["why"])

    def test_live_hit_beats_frozen_hit(self):
        out = ri.premise([{"anchor": "a", "found": True, "frozen": True, "address": "docs/artifacts/x.md:1"},
                          {"anchor": "b", "found": True, "frozen": False, "address": "review_pack.py:788"}])
        self.assertEqual(out["outcome"], ri.PREMISE_ALIVE)
        self.assertIn("review_pack.py:788", out["why"])

    def test_freshness_is_not_a_verdict(self):
        """Ответ сегодняшний, а якоря в дереве нет → ПРОТУХЛА, и дата ни при чём."""
        claim = ri.merge([_rec("1. УПРОЩАЕМО: убрать `symbol_that_never_existed_here`")])[0]
        with tempfile.TemporaryDirectory() as tmp:
            probes = run.probe_anchors(ri.probe_plan(claim), root=tmp,
                                       files=["a.py"], reader=lambda p: "пусто")
        self.assertEqual(ri.premise(probes)["outcome"], ri.PREMISE_STALE)

    def test_frozen_dirs_are_recognised(self):
        self.assertTrue(ri.frozen_address("docs/review_outbox/p.md"))
        self.assertTrue(ri.frozen_address("docs/artifacts/x.md"))
        self.assertFalse(ri.frozen_address("review_auto.py"))


class TestTreeProbe(unittest.TestCase):
    """Проба по дереву: корпус, замок от самоответа, третий исход."""

    def test_self_files_are_out_of_the_corpus(self):
        """Голден цитирует находки дословно — и не смеет подтверждать сам себя."""
        files, why = run.tracked_files(HERE)
        self.assertTrue(files, why)
        for name in run.SELF_FILES:
            self.assertNotIn(name, [os.path.basename(f) for f in files])

    def test_live_anchor_is_found_in_the_live_tree(self):
        plan = [{"anchor": "слепых пятен не заявлено", "form": "literal"}]
        probes = run.probe_anchors(plan, root=HERE)
        self.assertTrue(probes[0]["found"])
        self.assertFalse(probes[0]["frozen"])
        self.assertTrue(probes[0]["address"].startswith("review_pack.py:"))

    def test_dead_corpus_gives_unknown_for_every_anchor(self):
        probes = run.probe_anchors([{"anchor": "x", "form": "literal"}], root=HERE, files=[],
                                   why="git ls-files не ответил")
        self.assertIsNone(probes[0]["found"])
        self.assertIn("git ls-files", probes[0]["detail"])

    def test_path_anchor_checks_the_file_itself(self):
        probes = run.probe_anchors([{"anchor": "review_intake.py", "form": "path"}], root=HERE,
                                   files=["review_auto.py"])
        self.assertTrue(probes[0]["found"])           # файл есть в дереве, хоть и вне корпуса
        probes = run.probe_anchors([{"anchor": "no_such_file.py", "form": "path"}], root=HERE,
                                   files=["review_auto.py"])
        self.assertFalse(probes[0]["found"])


class TestClaimText(unittest.TestCase):
    """Текст заявки: «не задача» раньше содержания, все пять полей источника."""

    def _claim(self, sources=1):
        recs = [_rec("2. НЕ ДЕЛАТЬ ВОВСЕ: не заявлять «слепых пятен не заявлено» при трёх расписках"
                     " из шести", index=2)]
        if sources > 1:
            recs.append(_rec("2. НЕ ДЕЛАТЬ ВОВСЕ: не заявлять «слепых пятен не заявлено» и полное"
                             " покрытие дня при трёх расписках", index=2, pack="q.md",
                             channel="manus"))
        return ri.merge(recs)[0]

    def test_marker_and_not_a_task_stand_first(self):
        text = ri.claim_text(self._claim(), {"outcome": ri.PREMISE_ALIVE, "why": "ок"}, "2026-09-01")
        self.assertTrue(text.startswith("[заявка-ревью дата=2026-09-01 ключ="))
        self.assertTrue(ri.is_claim(text))
        self.assertIn("НЕ ЗАДАЧА", text.split("\n")[1])
        self.assertIn("задачу по ней ставит ЧЕЛОВЕК", text)

    def test_every_source_field_is_present(self):
        claim = self._claim()
        text = ri.claim_text(claim, {"outcome": ri.PREMISE_ALIVE, "why": "ок"}, "2026-09-01")
        src = claim["sources"][0]
        for piece in (src["channel"], src["send_date"], src["pack"], src["pack_sha256"][:12]):
            self.assertIn(piece, text)
        self.assertIn("слепых пятен не заявлено", text)

    def test_two_sources_keep_both_verbatim_quotes(self):
        claim = self._claim(sources=2)
        self.assertEqual(len(claim["sources"]), 2)
        text = ri.claim_text(claim, {"outcome": ri.PREMISE_UNKNOWN, "why": "нет адреса"}, "2026-09-01")
        self.assertIn("ИСТОЧНИКОВ: 2", text)
        self.assertIn("полное покрытие дня", text)
        self.assertIn("из шести", text)

    def test_premise_stands_above_the_quote(self):
        text = ri.claim_text(self._claim(), {"outcome": ri.PREMISE_STALE, "why": "нет в дереве"},
                             "2026-09-01")
        self.assertLess(text.index("ПРЕМИСА: ПРОТУХЛА"), text.index("ЦИТАТА ДОСЛОВНО"))

    def test_text_fits_the_queue_row(self):
        long_quote = "1. УПРОЩАЕМО: " + ("очень длинная находка канала. " * 200)
        claim = ri.merge([_rec(long_quote)])[0]
        text = ri.claim_text(claim, {"outcome": ri.PREMISE_UNKNOWN, "why": "нет"}, "2026-09-01")
        self.assertLessEqual(len(text), ri.CLAIM_TEXT_MAX)
        self.assertIn("цитата урезана", text)

    def test_markers_read_back_from_the_queue(self):
        text = ri.claim_text(self._claim(), {"outcome": ri.PREMISE_ALIVE, "why": "ок"}, "2026-09-01")
        rows = [{"task_text": text}, {"task_text": "обычная задача"}]
        markers = ri.claim_markers(rows)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0][0], "2026-09-01")
        self.assertEqual(ri.budget_left(markers, "2026-09-01", 3), 2)
        self.assertEqual(ri.budget_left(markers, "2026-09-02", 3), 3)

    def test_outbound_guard_sees_nothing_in_the_live_claim(self):
        """Цитата чужая и прилетела извне — проверяем ТОЙ ЖЕ стражей, что держит исходящее."""
        head = ri.parse_answer(_live(LIVE_DIGEST))
        for claim in ri.merge([ri.record(head, f, answer_rel="docs/review_inbox/" + LIVE_DIGEST)
                               for f in ri.split_findings(head["body"])]):
            text = ri.claim_text(claim, {"outcome": ri.PREMISE_UNKNOWN, "why": "—"}, "2026-09-01")
            self.assertEqual(ri.outbound_safe(text), [])


class _FakeQueue:
    """Очередь-заглушка: помнит поставленное, умеет отказывать НАЗВАННОЙ причиной."""

    def __init__(self, *, ok=True, why="", markers=(), fail_place=None):
        self.ok, self.why, self._markers = ok, why, list(markers)
        self.fail_place = fail_place
        self.placed = []
        self.next_id = 100

    def markers(self):
        return list(self._markers), self.ok, self.why

    def place(self, text, topic=None):
        if self.fail_place:
            return False, None, self.fail_place
        self.next_id += 1
        self.placed.append((self.next_id, text))
        self._markers.extend(ri.claim_markers([{"task_text": text}]))
        return True, self.next_id, ""


class _Tmp(unittest.TestCase):
    """Общий каркас: свой корень с лотком, реестр — в temp, БОЕВОЕ дерево не трогаем."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="intake_test_")
        self.addCleanup(self._rm)
        os.makedirs(os.path.join(self.dir, "docs", "review_inbox"))
        self.state = os.path.join(self.dir, "review_intake_state.json")

    def _rm(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def put(self, name, text):
        with io.open(os.path.join(self.dir, "docs", "review_inbox", name), "w",
                     encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def put_live(self, name):
        self.put(name, _live(name))

    def tick(self, **kw):
        kw.setdefault("root", self.dir)
        kw.setdefault("state_path", self.state)
        kw.setdefault("place", True)
        kw.setdefault("queue", self.q)
        return run.tick(**kw)


class TestPlacement(_Tmp):
    """Постановка заявок: бюджет, дедуп, бутстрап — всё restart-proof."""

    def setUp(self):
        super().setUp()
        self.q = _FakeQueue()

    def test_bootstrap_places_nothing_and_names_the_backlog(self):
        self.put_live(LIVE_DIGEST)
        report = self.tick()
        self.assertEqual(report["placed"], [])
        self.assertEqual(self.q.placed, [])
        state = run.read_state(self.state)
        self.assertTrue(state["bootstrap_at"])
        self.assertEqual(len(state["held"]), 3)
        for rec in state["held"].values():
            self.assertIn("бутстрап", rec["why"])

    def test_answer_arriving_after_the_bootstrap_becomes_a_claim(self):
        self.put_live(LIVE_DIGEST)
        self.tick()                                   # бутстрап
        self.put_live(LIVE_CHAINS)
        report = self.tick()
        self.assertEqual(len(report["placed"]), 2, "склеенный дубль уже был отложен бутстрапом")
        self.assertEqual(len(self.q.placed), 2)
        for _tid, text in self.q.placed:
            self.assertTrue(ri.is_claim(text))

    def test_manual_place_takes_only_the_named_pack_and_bootstraps_the_rest(self):
        self.put_live(LIVE_DIGEST)
        self.put_live(LIVE_CHAINS)
        report = self.tick(pack="digest", limit=3)
        self.assertEqual(len(report["placed"]), 3)
        state = run.read_state(self.state)
        self.assertEqual(len(state["claims"]), 3)
        self.assertTrue(state["held"], "остальное обязано быть названо словом, а не потеряно")

    def test_second_turn_never_places_the_same_claim_twice(self):
        self.put_live(LIVE_DIGEST)
        self.tick(pack="digest")
        first = len(self.q.placed)
        report = self.tick(pack="digest")
        self.assertEqual(len(self.q.placed), first)
        self.assertTrue(all(why == "уже поставлена" for _k, why in report["held"]))

    def test_queue_markers_alone_stop_a_duplicate_after_a_lost_ledger(self):
        """Реестр потерян — дедуп обязан выжить: маркеры лежат в САМОЙ очереди."""
        self.put_live(LIVE_DIGEST)
        self.tick(pack="digest")
        placed_text = self.q.placed[0][1]
        os.remove(self.state)
        q2 = _FakeQueue(markers=ri.claim_markers([{"task_text": placed_text}]))
        report = run.tick(root=self.dir, state_path=self.state, place=True, queue=q2, pack="digest")
        keys = [row["key"] for row in report["placed"]]
        self.assertNotIn(ri.claim_markers([{"task_text": placed_text}])[0][1], keys)

    def test_daily_budget_holds_the_rest_by_name(self):
        self.put_live(LIVE_DIGEST)
        self.tick()                                   # бутстрап
        self.put_live(LIVE_CHAINS)
        report = self.tick(budget=1)
        self.assertEqual(len(report["placed"]), 1)
        self.assertIn(("бюджет суток исчерпан"), [why for _k, why in report["held"]])

    def test_force_takes_the_held_but_never_the_placed(self):
        """Ручной заход поверх бутстрапа: отложенное берём, поставленное — никогда."""
        self.put_live(LIVE_DIGEST)
        self.tick()                                   # бутстрап: 3 ключа отложены словом
        self.assertEqual(len(run.read_state(self.state)["held"]), 3)
        report = self.tick(pack="digest", force=True)
        self.assertEqual(len(report["placed"]), 3)
        again = self.tick(pack="digest", force=True)
        self.assertEqual(again["placed"], [])
        self.assertTrue(all(why == "уже поставлена" for _k, why in again["held"]))

    def test_dry_run_touches_nothing(self):
        self.put_live(LIVE_DIGEST)
        report = run.tick(root=self.dir, state_path=self.state, place=False)
        self.assertEqual(report["placed"], [])
        self.assertFalse(os.path.exists(self.state))

    def test_journal_line_is_an_index_not_a_body(self):
        self.put_live(LIVE_DIGEST)
        self.tick()
        self.put_live(LIVE_CHAINS)
        lines = []
        self.tick(write_journal=True, journal_fn=lambda line, repo=None: lines.append(line) or (0, ""))
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("ARTIFACT ревью-заявка ключ="))
            self.assertLess(len(line), 300)
            self.assertIn("очередь #", line)


class TestNegativeQueue(_Tmp):
    """Мост отказывает: не поставить НИЧЕГО, назвать причину, не соврать реестром."""

    def setUp(self):
        super().setUp()
        self.q = _FakeQueue()

    def test_unreadable_queue_stops_placement_entirely(self):
        self.put_live(LIVE_DIGEST)
        self.q = _FakeQueue(ok=False, why="HTTP 500")
        report = self.tick()
        self.assertEqual(report["placed"], [])
        self.assertIn("HTTP 500", report["why"])
        self.assertIn("дедуп не сверить", report["why"])
        self.assertFalse(os.path.exists(self.state), "реестр не пишем: бутстрапа не было")

    def test_failed_place_is_not_recorded_as_placed(self):
        self.put_live(LIVE_DIGEST)
        self.q = _FakeQueue(fail_place="мост не ответил распиской")
        report = self.tick(pack="digest")
        self.assertEqual(report["placed"], [])
        self.assertEqual(len(report["failed"]), 3)
        self.assertEqual(run.read_state(self.state)["claims"], {})

    def test_failed_claim_returns_next_turn(self):
        """Сорвавшаяся постановка бутстрапом НЕ хоронится: заход не потерян."""
        self.put_live(LIVE_DIGEST)
        self.q = _FakeQueue(fail_place="мост не ответил распиской")
        self.tick(pack="digest")
        self.assertEqual(run.read_state(self.state)["held"], {})
        self.q = _FakeQueue()
        report = self.tick(pack="digest")
        self.assertEqual(len(report["placed"]), 3)

    def test_broken_file_in_the_tray_is_skipped_by_name(self):
        self.put_live(LIVE_DIGEST)
        self.put("мусор.md", "это не файл ответа вовсе")
        built = run.build(self.dir)
        skipped = dict(built["skipped"])
        self.assertIn("docs/review_inbox/мусор.md", skipped)
        self.assertIn("не файл ответа", skipped["docs/review_inbox/мусор.md"])
        self.assertEqual(len(built["claims"]), 3)


class TestNotATask(unittest.TestCase):
    """Заявка не становится задачей — и это держится КОДОМ демона, а не обещанием."""

    def setUp(self):
        os.environ.setdefault("TURBOBABY_TEST_LOGS", "1")
        import pc_orchestrator
        self.o = pc_orchestrator

    def _text(self):
        claim = ri.merge([_rec("1. УПРОЩАЕМО: убрать `лишний_слой`")])[0]
        return ri.claim_text(claim, {"outcome": ri.PREMISE_ALIVE, "why": "ок"}, "2026-09-01")

    def test_marker_of_the_daemon_matches_the_pure_module(self):
        """Гарды опознают заявку по маркеру — он обязан быть ОДИН на два модуля."""
        self.assertEqual(self.o.REVIEW_CLAIM_MARK, ri.CLAIM_MARK)
        self.assertEqual(self.o.REVIEW_CLAIM_FROM, run.CLAIM_FROM)
        self.assertTrue(self.o._is_review_claim(self._text()))
        self.assertFalse(self.o._is_review_claim("задача: сделай что-нибудь"))

    def test_claim_is_not_owner_work_for_revizor_chains(self):
        item = {"from": self.o.REVIEW_CLAIM_FROM, "task_text": self._text(),
                "status": "needs_approval"}
        self.assertFalse(self.o._is_owner_work(item, set()))
        self.assertFalse(self.o._owner_work_pending([item]))

    def test_placement_goes_through_needs_approval_not_through_new(self):
        """Ряд обязан уехать в ожидание решения СИНХРОННО: в `new` его подобрал бы демон."""
        with io.open(os.path.join(HERE, "review_intake_run.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("set_needs_approval", body)
        self.assertIn("enqueue_pc_task", body)


class TestStateIsolation(_Tmp):
    """Тест не смеет писать в БОЕВОЙ реестр — класс, пойманный ступенью A живьём."""

    def setUp(self):
        super().setUp()
        self.q = _FakeQueue()

    def test_live_registry_is_untouched_by_a_full_turn(self):
        live = os.path.join(HERE, run.DEFAULT_STATE)
        before = os.path.exists(live) and os.path.getmtime(live)
        self.put_live(LIVE_DIGEST)
        self.tick(pack="digest")
        after = os.path.exists(live) and os.path.getmtime(live)
        self.assertEqual(before, after)
        self.assertTrue(os.path.exists(self.state))

    def test_state_file_is_json_and_survives_a_reread(self):
        self.put_live(LIVE_DIGEST)
        self.tick(pack="digest")
        with io.open(self.state, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw["schema"], ri.SCHEMA)
        self.assertEqual(run.read_state(self.state)["claims"].keys(), raw["claims"].keys())


if __name__ == "__main__":
    unittest.main(verbosity=2)
