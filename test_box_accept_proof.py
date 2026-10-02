# -*- coding: utf-8 -*-
"""ПРИЁМКА ЯЩИКА ОПИРАЕТСЯ НА ПРОВЕРЯЕМЫЙ РЕЗУЛЬТАТ, А НЕ НА ПОКРЫТИЕ СЛОВ (02.10.2026, 0124e-78m.0210).

Повод — находка 1 внешнего аудита 02.10: accept() принял копию задания и отчёт «доставка НЕ
проверена, идентификатор НЕ записан, коммит НЕ применён, версия НЕ подтверждена».

ОТРИЦАТЕЛЬНЫЕ — первыми: копия задания · отчёт из отрицаний дословно из повода · SHA, которого
нет на origin · «только ветка» при виде «живой main» · запуск без подтверждения. Каждый из них
сперва доказывает, что ПРЕЖНЯЯ мера (доля слов) его ПРИНЯЛА бы, — иначе тест ничего не стережёт.
ПОЛОЖИТЕЛЬНЫЕ — на трёх настоящих артефактах 02.10 и их настоящих заданиях (тела сняты из папки
мозга read-only, лежат фикстурой), читатель сервера — дословный вывод живого ssh 02.10 11:28 UTC.
"""

import os
import unittest

import done_judge_pc
import shtab_box_accept as acc
import shtab_box_run


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        if os.path.isdir(os.path.join(cur, "docs", "artifacts")):
            return cur
        cur = os.path.dirname(cur)
    return here


ROOT = _root()
FIX = os.path.join(ROOT, "fixtures", "box_proof_0210")


def _read(*parts):
    with open(os.path.join(*parts), "r", encoding="utf-8") as handle:
        return handle.read()


# ── живой вывод сервера 02.10.2026 11:28 UTC (ssh … git ls-remote origin …; git rev-parse) ──
LIVE_HEADS = {
    "wa-draft-fix-0210": "f6af7c5903aed7a88bebc0ef48f8f5a2789b2479",
    "wa-draft-safe-0210": "c9470abeb91f6a96d2900a7922616271c8ac8f67",
    "wa-watch10-0210": "5f78caf468a0d2be86d6e22bebced53a0605eb89",
}
LIVE_MAIN = "f8f65b37b46820a35ecb21a4dff927496c059e8c main"


def live_reader(what, arg=None):
    if what == "ls-remote":
        return {"ok": True, "value": LIVE_HEADS.get(arg, ""), "why": ""}
    if what == "live-main":
        return {"ok": True, "value": LIVE_MAIN, "why": ""}
    return {"ok": False, "value": "", "why": "?"}


def dead_reader(_what, _arg=None):
    return {"ok": False, "value": "", "why": "ssh rc=255: Connection timed out"}


TASK = u"""ЦЕЛЬ: карточка агента доходит до темы, и это видно.

ЗАПРЕТЫ (стандартный блок — идёт ПЕРВЫМ, не смягчать и не сокращать):
• ничего не удалять, включая уборку за собой;

ЧТО СДЕЛАТЬ:
1. Проверить доставку карточки в тему и записать идентификатор сообщения.
2. Применить коммит на живом сервере и подтвердить версию службы.

АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 02.10 со словами CARDPROOF0210
"""

# ДОСЛОВНО из повода (находка 1 внешнего аудита 02.10).
NEGATIONS = u"доставка НЕ проверена, идентификатор НЕ записан, коммит НЕ применён, версия НЕ подтверждена"

GOOD = u"""# CARDPROOF0210
Доставку карточки в тему проверил: сообщение легло, идентификатор записан — message_id 4412.
Коммит применён на живом сервере, версия службы подтверждена строкой баннера.
"""


def old_measure_accepts(body, art):
    """Прежняя мера (доля опорных слов по ВСЕМУ тексту) — приняла бы она артефакт?"""
    stems = acc.stems(art)
    return all(acc.answered(acc.terms(p["text"]), stems)[0] for p in acc.points(body))


def with_claim(body, claim):
    return body.replace(u"АДРЕС РЕЗУЛЬТАТА", u"РЕЗУЛЬТАТ: %s\n\nАДРЕС РЕЗУЛЬТАТА" % claim, 1)


class NegativeCopyOfTheTask(unittest.TestCase):
    """Копия тела задания — НЕИЗВЕСТНО, а не ПРИНЯТО."""

    def test_old_measure_accepted_the_copy(self):
        self.assertTrue(old_measure_accepts(TASK, TASK))

    def test_copy_is_unknown_with_reason(self):
        out = acc.accept(TASK, TASK)
        self.assertEqual(out["verdict"], acc.UNKNOWN, out["why"])
        self.assertEqual(out["rule"], acc.RULE_COPY)
        self.assertIn(u"копия", out["why"])

    def test_copy_under_a_header_is_still_a_copy(self):
        out = acc.accept(TASK, u"# CARDPROOF0210\nОтчёт.\n\n" + TASK)
        self.assertEqual(out["verdict"], acc.UNKNOWN, out["why"])
        self.assertEqual(out["rule"], acc.RULE_COPY)

    def test_points_quoted_verbatim_do_not_answer_themselves(self):
        art = u"# CARDPROOF0210\nЗаход закрыт, итог ниже, всё по плану и в срок.\n" + "\n".join(
            u"%d. %s — да." % (p["n"], p["text"]) for p in acc.points(TASK)) + \
            u"\nОтвет дан коротко, подробности у исполнителя, вопросов к Штабу нет пока что."
        self.assertTrue(old_measure_accepts(TASK, art))
        out = acc.accept(TASK, art)
        self.assertNotEqual(out["verdict"], acc.ACCEPTED, out["why"])


class NegativeReportOfNegations(unittest.TestCase):
    """Отчёт из отрицаний — дословно из повода — НЕИЗВЕСТНО."""

    def test_old_measure_accepted_the_negations(self):
        self.assertTrue(old_measure_accepts(TASK, NEGATIONS))

    def test_negations_are_unknown_with_reason(self):
        out = acc.accept(TASK, NEGATIONS)
        self.assertEqual(out["verdict"], acc.UNKNOWN, out["why"])
        self.assertEqual(out["rule"], acc.RULE_NEGATION)
        self.assertEqual(out["hollow"], [1, 2])
        self.assertIn(u"под отрицанием", out["why"])

    def test_the_same_words_said_affirmatively_are_accepted(self):
        out = acc.accept(TASK, GOOD)
        self.assertEqual(out["verdict"], acc.ACCEPTED, out["why"])


class NegativeShaNotOnOrigin(unittest.TestCase):
    BODY = with_claim(TASK, u"ветка wa-draft-fix-0210")

    def test_sha_absent_on_origin_is_unproven(self):
        art = GOOD + u"\nFACT: commit 1234abc на GitHub, ветка wa-draft-fix-0210\n"
        self.assertTrue(old_measure_accepts(self.BODY, art))
        out = acc.accept(self.BODY, art, reader=live_reader)
        self.assertEqual(out["verdict"], acc.UNPROVEN, out["why"])
        self.assertEqual(out["rule"], acc.RULE_RESULT)
        self.assertIn(u"f6af7c5903ae", out["why"])

    def test_branch_absent_on_origin_is_unproven(self):
        body = with_claim(TASK, u"ветка wa-no-such-0210")
        art = GOOD + u"\nFACT: commit f6af7c5 на GitHub, ветка wa-no-such-0210\n"
        out = acc.accept(body, art, reader=live_reader)
        self.assertEqual(out["verdict"], acc.UNPROVEN, out["why"])
        self.assertIn(u"НЕТ", out["why"])

    def test_no_reader_is_unknown_not_accepted(self):
        art = GOOD + u"\nFACT: commit f6af7c5 на GitHub, ветка wa-draft-fix-0210\n"
        out = acc.accept(self.BODY, art)
        self.assertEqual(out["verdict"], acc.UNKNOWN, out["why"])
        self.assertEqual(out["rule"], acc.RULE_RESULT)


class NegativeBranchOnlyWhenLiveMainClaimed(unittest.TestCase):
    """«Только ветка» при виде «живой main» — НЕ ДОКАЗАН. Артефакт — настоящий WADRAFTFIX0210."""

    def test_real_branch_only_artifact_is_unproven_for_live_main(self):
        art = _read(ROOT, "docs", "artifacts", "2026-10-02-WADRAFTFIX0210.md")
        out = acc.prove(with_claim(TASK, u"живой main"), art, reader=live_reader)
        self.assertEqual(out["verdict"], acc.UNPROVEN, out["why"])
        self.assertIn(u"f8f65b37b468", out["why"])

    def test_head_line_of_the_untouched_server_is_not_a_claim(self):
        # «живой сервер не менялся — HEAD f8f65b3» совпадает с живым main, но заявкой работы не
        # является: хеш работы — только после «commit».
        art = u"FACT: живой сервер не менялся — HEAD f8f65b3\nFACT: commit f6af7c5 ветка x-0210\n"
        self.assertEqual(acc.fact_commits(art), ["f6af7c5"])
        out = acc.prove(with_claim(TASK, u"живой main"), art, reader=live_reader)
        self.assertEqual(out["verdict"], acc.UNPROVEN, out["why"])

    def test_tree_not_on_main_is_unproven(self):
        art = u"FACT: commit f8f65b3 применён\n"
        reader = lambda w, a=None: {"ok": True, "value": "f8f65b37b468 wa-x", "why": ""}  # noqa: E731
        out = acc.prove(with_claim(TASK, u"живой main"), art, reader=reader)
        self.assertEqual(out["verdict"], acc.UNPROVEN, out["why"])


class NegativeLaunchWithoutConfirmation(unittest.TestCase):
    BODY = with_claim(TASK, u"живой main сервера")

    def test_launch_claimed_without_commit_in_fact_is_unproven(self):
        art = GOOD + u"\nПрименено в живой main и запущено.\nFACT: служба перезапущена, всё работает\n"
        self.assertTrue(old_measure_accepts(self.BODY, art))
        out = acc.accept(self.BODY, art, reader=live_reader)
        self.assertEqual(out["verdict"], acc.UNPROVEN, out["why"])
        self.assertIn(u"не называют коммит", out["why"])

    def test_launch_with_hash_but_unreadable_server_is_unknown(self):
        art = GOOD + u"\nFACT: commit f8f65b3 в живом main, служба перезапущена\n"
        out = acc.accept(self.BODY, art, reader=dead_reader)
        self.assertEqual(out["verdict"], acc.UNKNOWN, out["why"])
        self.assertIn(u"Connection timed out", out["why"])

    def test_launch_confirmed_by_live_main_is_accepted(self):
        art = GOOD + u"\nFACT: commit f8f65b3 в живом main, служба перезапущена\n"
        out = acc.accept(self.BODY, art, reader=live_reader)
        self.assertEqual(out["verdict"], acc.ACCEPTED, out["why"])


class DecisionKind(unittest.TestCase):
    BODY = with_claim(TASK, u"решение")

    def test_decision_without_line_is_unproven(self):
        out = acc.accept(self.BODY, GOOD)
        self.assertEqual(out["verdict"], acc.UNPROVEN, out["why"])

    def test_negative_decision_is_a_legal_answer(self):
        art = NEGATIONS + u"\nРЕШЕНИЕ: нельзя, потому что доставка карточки идёт через чужой мост.\n"
        out = acc.accept(self.BODY, art)
        self.assertEqual(out["verdict"], acc.ACCEPTED, out["why"])

    def test_unknown_kind_is_unknown(self):
        out = acc.accept(with_claim(TASK, u"что-нибудь хорошее"), GOOD, reader=live_reader)
        self.assertEqual(out["verdict"], acc.UNKNOWN, out["why"])


# ── положительные: три настоящих артефакта 02.10 и их настоящие задания ──
REAL = (("0123-78f.0210", "2026-10-02-WADRAFTFIX0210.md", "wa-draft-fix-0210"),
        ("0124a-78h.0210", "2026-10-02-WAWATCHTEN0210.md", "wa-watch10-0210"),
        ("0124d-78l.0210", "2026-10-02-WADRAFTSAFE0210.md", "wa-draft-safe-0210"))


class PositiveRealArtifacts(unittest.TestCase):

    def _case(self, key, name):
        body = _read(FIX, key + ".txt")
        art = _read(ROOT, "docs", "artifacts", name)
        return body, art

    def test_real_artifacts_are_accepted_without_result_line(self):
        for key, name, _br in REAL:
            body, art = self._case(key, name)
            out = acc.accept(body, art)
            self.assertEqual(out["verdict"], acc.ACCEPTED, "%s: %s" % (name, out["why"]))

    def test_real_artifacts_are_accepted_with_branch_proven_on_origin(self):
        for key, name, branch in REAL:
            body, art = self._case(key, name)
            out = acc.accept(with_claim(body, u"ветка " + branch), art, reader=live_reader)
            self.assertEqual(out["verdict"], acc.ACCEPTED, "%s: %s" % (name, out["why"]))
            self.assertIn(u"совпала с FACT", out["why"])

    def test_real_artifacts_branch_taken_from_fact_when_not_named(self):
        for key, name, _br in REAL:
            body, art = self._case(key, name)
            out = acc.accept(with_claim(body, u"ветка на origin сервера"), art, reader=live_reader)
            self.assertEqual(out["verdict"], acc.ACCEPTED, "%s: %s" % (name, out["why"]))

    def test_point_that_is_text_to_reproduce_is_not_hollow(self):
        # 0106a-77k: пункт 1 — шесть шаблонов дословно; артефакт обязан их повторить. Прежний
        # ПРИНЯТО обязан остаться ПРИНЯТО (до сужения правила «в цитате» было НЕИЗВЕСТНО).
        body = _read(FIX, "0106a-77k.0210.txt")
        art = _read(ROOT, "docs", "artifacts", "2026-10-02-WATEMPLATES0210.md")
        out = acc.accept(body, art)
        self.assertEqual(out["verdict"], acc.ACCEPTED, out["why"])

    def test_real_artifacts_are_unproven_for_live_main(self):
        for key, name, _br in REAL:
            body, art = self._case(key, name)
            out = acc.accept(with_claim(body, u"живой main"), art, reader=live_reader)
            self.assertEqual(out["verdict"], acc.UNPROVEN, "%s: %s" % (name, out["why"]))


class OneVerdictOnBothPaths(unittest.TestCase):
    """accept() и путь V0 (`shtab_box_run.v0_proof`) дают одно слово."""

    def row(self, body):
        return u"[от Штаба дата=2026-10-02 ключ=proof-0210]\nИсточник: документ shtab_task_proof-0210 " \
               u"папки мозга (file id x)\nвставка\n\n" + body

    def test_negations_map_to_unknown_on_v0(self):
        word, why = shtab_box_run.v0_proof(self.row(TASK), NEGATIONS, reader=live_reader)
        self.assertEqual(word, done_judge_pc.UNKNOWN)
        self.assertIn(acc.UNKNOWN, why)

    def test_sha_not_on_origin_maps_to_unproven_on_v0(self):
        body = with_claim(TASK, u"ветка wa-draft-fix-0210")
        art = GOOD + u"\nFACT: commit 1234abc на GitHub, ветка wa-draft-fix-0210\n"
        word, _why = shtab_box_run.v0_proof(self.row(body), art, reader=live_reader)
        self.assertEqual(word, done_judge_pc.UNPROVEN)

    def test_retry_is_not_carried_to_v0(self):
        # ДОЖАТЬ живёт на `done` — путь V0 его не переносит, иначе дожим умер бы в `failed`.
        self.assertEqual(acc.accept(TASK, u"ничего по делу")["verdict"], acc.RETRY)
        self.assertEqual(shtab_box_run.v0_proof(self.row(TASK), u"ничего по делу")[0], None)

    def test_legacy_unknown_is_not_carried_to_v0(self):
        # Нет раздела «ЧТО СДЕЛАТЬ» — прежнее НЕИЗВЕСТНО приёмки, не правило доказательства.
        body = u"ЦЕЛЬ: что-то.\n\nАДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 02.10 со словами X0210"
        self.assertEqual(acc.accept(body, NEGATIONS)["verdict"], acc.UNKNOWN)
        self.assertEqual(shtab_box_run.v0_proof(self.row(body), NEGATIONS)[0], None)

    def test_accepted_stays_done_on_v0(self):
        self.assertEqual(shtab_box_run.v0_proof(self.row(TASK), GOOD)[0], None)

    def test_not_a_box_row_is_not_judged(self):
        self.assertEqual(shtab_box_run.v0_proof(TASK, NEGATIONS)[0], None)

    def test_hands_pass_the_reader_to_accept(self):
        body = with_claim(TASK, u"ветка wa-draft-fix-0210")
        art = GOOD + u"\nFACT: commit 1234abc на GitHub, ветка wa-draft-fix-0210\n"
        rows = [{"id": 5, "status": "done", "task_text": self.row(body)}]
        _blocks, notes = shtab_box_run.retry_blocks(rows, set(), reader=live_reader,
                                                    artifact_fn=lambda _t: (art, True, "", "a.md"))
        self.assertEqual(notes[0]["verdict"], acc.UNPROVEN)
        self.assertEqual(notes[0]["next"], "")


class DaemonCloseUsesTheSameRule(unittest.TestCase):
    """Врезка в `pc_orchestrator._judge_done`: «сделано» V0 на ряде ящика спрашивает те же правила."""

    TID = 987654322

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("LESSON_LLM_ROUTE", "0")
        os.environ.setdefault("REVIZOR_PACHKA_OFF", "1")
        import pc_orchestrator
        cls.o = pc_orchestrator

    def close(self, text, art):
        o = self.o
        done = {"verdict": o.done_judge_pc.DONE, "reason": "V0: PROVEN / ok",
                "address": {"folder": "docs/artifacts", "words": "CARDPROOF0210"},
                "chosen": "docs/artifacts/2026-10-02-CARDPROOF0210.md"}
        from unittest import mock
        with mock.patch.object(o.done_judge_pc, "judge", lambda *a, **k: dict(done)), \
                mock.patch.object(o.done_judge_pc, "note", lambda *a, **k: None), \
                mock.patch.object(o.done_judge_pc, "_read_text", lambda *a, **k: art), \
                mock.patch.dict(os.environ, {"DONE_JUDGE_PC": "addr"}, clear=False):
            return o._judge_done(self.TID, text, "done", "RESULT: ок", {})

    def test_negation_report_closes_failed_unknown(self):
        status, text = self.close(OneVerdictOnBothPaths().row(TASK), NEGATIONS)
        self.assertEqual(status, "failed")
        self.assertEqual(self.o.done_judge_pc.outcome_of(text), self.o.done_judge_pc.UNKNOWN)
        self.assertIn(u"приёмка ящика", text)

    def test_good_report_stays_done(self):
        status, _text = self.close(OneVerdictOnBothPaths().row(TASK), GOOD)
        self.assertEqual(status, "done")

    def test_non_box_row_is_untouched(self):
        status, _text = self.close(TASK, NEGATIONS)
        self.assertEqual(status, "done")


class ServerReaderShape(unittest.TestCase):
    """Читатель сервера: ровно опции задания, только чтение, ветка в кавычках."""

    def test_argv_is_literal(self):
        seen = []

        def runner(argv):
            seen.append(argv)
            return 0, "f6af7c5903aed7a88bebc0ef48f8f5a2789b2479\trefs/heads/wa-draft-fix-0210\n", ""
        got = shtab_box_run.server_reader("ls-remote", "wa-draft-fix-0210", runner=runner)
        self.assertEqual(got, {"ok": True, "value": LIVE_HEADS["wa-draft-fix-0210"], "why": ""})
        argv = seen[0]
        self.assertEqual(argv[0], "ssh")
        joined = " ".join(argv)
        self.assertIn("-o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 "
                      "-o BatchMode=yes root@5.223.94.179", joined)
        self.assertTrue(argv[2].endswith("turbobaby_vps"))
        self.assertIn("git ls-remote origin refs/heads/wa-draft-fix-0210", argv[-1])

    def test_live_main_reads_two_lines(self):
        got = shtab_box_run.server_reader(
            "live-main", runner=lambda argv: (0, "f8f65b37b46820a35ecb21a4dff927496c059e8c\nmain\n", ""))
        self.assertEqual(got["value"], LIVE_MAIN)
        self.assertIn("git rev-parse HEAD", shtab_box_run._ssh_argv("x")[-1] + " git rev-parse HEAD")

    def test_only_the_exact_ref_counts(self):
        # ls-remote сверяет шаблон С ХВОСТА: чужая ссылка с тем же хвостом не голова нашей ветки.
        out = ("1111111111111111111111111111111111111111\trefs/heads/old/refs/heads/wa-x-0210\n"
               "f6af7c5903aed7a88bebc0ef48f8f5a2789b2479\trefs/heads/wa-x-0210\n")
        got = shtab_box_run.server_reader("ls-remote", "wa-x-0210", runner=lambda a: (0, out, ""))
        self.assertEqual(got["value"], "f6af7c5903aed7a88bebc0ef48f8f5a2789b2479")
        got = shtab_box_run.server_reader("ls-remote", "wa-x-0210",
                                          runner=lambda a: (0, out.splitlines()[0] + "\n", ""))
        self.assertEqual(got["value"], "")

    def test_missing_branch_is_empty_not_failure(self):
        got = shtab_box_run.server_reader("ls-remote", "wa-no-such-0210", runner=lambda a: (0, "", ""))
        self.assertEqual(got, {"ok": True, "value": "", "why": ""})

    def test_ssh_failure_is_not_ok(self):
        got = shtab_box_run.server_reader("live-main", runner=lambda a: (255, "", "timed out"))
        self.assertFalse(got["ok"])
        self.assertIn("255", got["why"])

    def test_bad_branch_never_reaches_ssh(self):
        calls = []

        def runner(argv):
            calls.append(argv)
            return 0, "", ""
        self.assertFalse(shtab_box_run.server_reader("ls-remote", "x; touch /tmp/x", runner=runner)["ok"])
        self.assertEqual(calls, [])

    def test_only_read_commands(self):
        import inspect
        src = inspect.getsource(shtab_box_run.server_reader)
        for verb in ("push", "fetch", "checkout", "reset", "merge", "pull", "commit"):
            self.assertNotIn("git " + verb, src)


if __name__ == "__main__":
    unittest.main()
