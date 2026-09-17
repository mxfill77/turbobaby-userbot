# -*- coding: utf-8 -*-
"""Набор обучения (`exam_set.py`): версия, источник у каждого кейса, откат целиком по источнику.

БОЕВЫХ ФАЙЛОВ НЕ КАСАЕТСЯ НИ ОДИН ТЕСТ: набор, журнал, архив и база переписки — во временном
каталоге; база синтетическая, живой переписки здесь нет ни строки; сеть и голова не зовутся.
"""

import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

import exam_set
import exam_show
import style_examples

FP = "36d68c71d6019fe2"
STRICT = "anonstable@%s/strict" % FP
SOFT = "anonstable@%s/soft" % FP


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _case(dialog, text="Здравствуйте, хочу арендовать байк"):
    return {"name": "кейс д%d" % dialog, "source": "синтетика д%d·р0" % dialog, "lang": "ru",
            "lines": [text], "expect": {},
            "reference": {"who": "менеджер", "text": "Добрый день, на какие даты?",
                          "mark": "д%d·р1" % dialog, "parts": 1, "gap_sec": 60, "base": FP}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exam_set_test_")
        self.path = os.path.join(self.tmp, "cases.json")
        self.shots = os.path.join(self.tmp, "shots")
        os.makedirs(self.shots)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def legacy(self, cases):
        """Набор в том виде, в каком его завёл 63-n: тем же json.dump(indent=1) и переводом строки."""
        doc = {"version": 1, "about": "живой набор", "cases": cases}
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
            f.write("\n")
        return doc


class TestVersionIsTheFingerprint(Base):
    def test_absent_set_has_no_version_not_an_empty_one(self):
        self.assertIsNone(exam_set.version(self.path))

    def test_version_is_the_same_value_the_shot_records_as_corpus(self):
        self.legacy([dict(_case(206), id=1)])
        self.assertEqual(exam_set.version(self.path), exam_show.corpus_fingerprint(self.path))

    def test_rewrite_of_an_untouched_legacy_set_keeps_its_bytes(self):
        doc = self.legacy([dict(_case(206), id=1)])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), exam_set._dump(doc))

    def test_an_event_moves_the_version_and_the_ledger_names_both_sides(self):
        self.legacy([dict(_case(206), id=1)])
        before = exam_set.version(self.path)
        ok, words, ids = exam_set.add([_case(57)], STRICT, "тест", path=self.path)
        self.assertTrue(ok, words)
        after = exam_set.version(self.path)
        self.assertNotEqual(before, after)
        row = exam_set.ledger(self.path)[-1]
        self.assertEqual((row["событие"], row["версия_до"], row["версия_после"]),
                         (exam_set.EV_ADD, before, after))


class TestSourceIsAClosedKey(Base):
    def test_good_keys_pass(self):
        self.assertEqual(exam_set.validate_source(STRICT), ("anonstable", FP, "strict"))
        self.assertEqual(exam_set.validate_source(SOFT)[2], "soft")

    def test_foreign_base_rule_and_form_are_refused_loudly(self):
        for bad in ("anon@%s/strict" % FP, "anonstable@%s/magic" % FP, "anonstable/strict", "",
                    None, "anonstable@XYZ/strict"):
            with self.assertRaises(exam_set.SetRejected, msg=bad):
                exam_set.validate_source(bad)

    def test_case_without_a_valid_key_is_unknown(self):
        self.assertEqual(exam_set.source_of(_case(1)), exam_set.SOURCE_UNKNOWN)
        self.assertEqual(exam_set.source_of(dict(_case(1), source_key="руками")),
                         exam_set.SOURCE_UNKNOWN)
        self.assertEqual(exam_set.source_of(dict(_case(1), source_key=STRICT)), STRICT)

    def test_add_with_a_foreign_key_writes_nothing(self):
        self.legacy([dict(_case(206), id=1)])
        sha = _sha(self.path)
        with self.assertRaises(exam_set.SetRejected):
            exam_set.add([_case(57)], "anon@%s/strict" % FP, "тест", path=self.path)
        self.assertEqual(_sha(self.path), sha)
        self.assertEqual(exam_set.ledger(self.path), [])

    def test_set_source_names_only_the_unknown_and_never_rewrites(self):
        self.legacy([dict(_case(206), id=1)])
        ok, words = exam_set.set_source(1, SOFT, "тест", path=self.path)
        self.assertTrue(ok, words)
        ok, words = exam_set.set_source(1, STRICT, "тест", path=self.path)
        self.assertFalse(ok)
        self.assertEqual(exam_set.load(self.path)["cases"][0]["source_key"], SOFT)


class TestAdd(Base):
    def test_ids_come_from_the_set_and_the_same_dialog_is_not_added_twice(self):
        self.legacy([dict(_case(206), id=1)])
        ok, words, ids = exam_set.add([_case(57), _case(206), _case(88)], STRICT, "тест",
                                      path=self.path)
        self.assertTrue(ok, words)
        self.assertEqual(ids, [2, 3])
        self.assertIn("уже были в наборе 1", words)
        doc = exam_set.load(self.path)
        self.assertEqual([c["source_key"] for c in doc["cases"][1:]], [STRICT, STRICT])

    def test_no_author_no_write(self):
        self.legacy([])
        sha = _sha(self.path)
        ok, words, ids = exam_set.add([_case(57)], STRICT, "  ", path=self.path)
        self.assertFalse(ok)
        self.assertEqual(_sha(self.path), sha)


class TestRollbackWhole(Base):
    def setUp(self):
        super(TestRollbackWhole, self).setUp()
        self.legacy([dict(_case(206), id=1)])
        exam_set.set_source(1, SOFT, "тест", path=self.path)
        self.neighbour = os.path.join(self.tmp, "trainer_cases.json")
        with open(self.neighbour, "w", encoding="utf-8") as f:
            f.write('{"cases": [1, 2, 3]}\n')
        self.neighbour_sha = _sha(self.neighbour)
        self.keep_before = json.dumps(exam_set.load(self.path)["cases"][0], ensure_ascii=False)
        self.pre = _sha(self.path)
        exam_set.add([_case(57), _case(88), _case(401)], STRICT, "тест", path=self.path)
        with open(os.path.join(self.shots, "case-2@abc1234.json"), "w") as f:
            f.write("{}")
        self.shot_sha = _sha(os.path.join(self.shots, "case-2@abc1234.json"))

    def test_one_action_takes_every_case_of_the_source_and_nothing_else(self):
        before_rb = _sha(self.path)
        ok, words, ids = exam_set.rollback(STRICT, "тест", path=self.path, shots_dir=self.shots)
        self.assertTrue(ok, words)
        self.assertEqual(ids, [2, 3, 4])
        doc = exam_set.load(self.path)
        self.assertEqual([c["id"] for c in doc["cases"]], [1])
        self.assertEqual(json.dumps(doc["cases"][0], ensure_ascii=False), self.keep_before)
        # набор вернулся к версии ДО завода источника — побайтно
        self.assertEqual(_sha(self.path), self.pre)
        self.assertEqual(_sha(self.path + exam_set.BAK_SUFFIX), before_rb)
        self.assertEqual(_sha(self.neighbour), self.neighbour_sha)
        self.assertIn("снимков откаченных на диске 1", words)
        self.assertEqual(_sha(os.path.join(self.shots, "case-2@abc1234.json")), self.shot_sha)

    def test_rolled_back_cases_live_on_whole_in_the_archive(self):
        exam_set.rollback(STRICT, "тест", path=self.path)
        rows = exam_set.archived(self.path)
        self.assertEqual([r["case"]["id"] for r in rows], [2, 3, 4])
        self.assertEqual(rows[0]["case"]["reference"]["mark"], "д57·р1")
        self.assertEqual(exam_set.ledger(self.path)[-1]["событие"], exam_set.EV_ROLLBACK)
        self.assertEqual(exam_set.ledger(self.path)[-1]["кейсы"], "2,3,4")

    def test_ids_are_never_reused_after_rollback(self):
        exam_set.rollback(STRICT, "тест", path=self.path)
        ok, words, ids = exam_set.add([_case(57)], STRICT, "тест", path=self.path)
        self.assertTrue(ok, words)
        self.assertEqual(ids, [5])

    def test_absent_source_changes_nothing(self):
        sha = _sha(self.path)
        events = len(exam_set.ledger(self.path))
        ok, words, ids = exam_set.rollback("anonstable@%s/soft" % ("0" * 16), "тест", path=self.path)
        self.assertFalse(ok)
        self.assertEqual((_sha(self.path), len(exam_set.ledger(self.path))), (sha, events))

    def test_unknown_source_is_not_taken_by_any_rollback(self):
        doc = exam_set.load(self.path)
        doc["cases"].append(dict(_case(9), id=9))
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(exam_set._dump(doc))
        exam_set.rollback(STRICT, "тест", path=self.path)
        exam_set.rollback(SOFT, "тест", path=self.path)
        self.assertEqual([c["id"] for c in exam_set.load(self.path)["cases"]], [9])
        self.assertEqual(exam_set.census(self.path)["unknown"], 1)

    def test_cli_foreign_key_is_rc2_and_writes_nothing(self):
        sha = _sha(self.path)
        buf = io.StringIO()
        was = exam_set.SET_CASES
        exam_set.SET_CASES = self.path
        try:
            with redirect_stdout(buf):
                rc = exam_set.main(["--rollback", "anon@%s/strict" % FP, "--who", "тест"])
        finally:
            exam_set.SET_CASES = was
        self.assertEqual(rc, 2)
        self.assertEqual(_sha(self.path), sha)


class TestStrictLinks(Base):
    def base(self, dialogs):
        p = os.path.join(self.tmp, "base.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for msgs in dialogs:
                f.write(json.dumps({"peer_name": "", "messages": msgs}, ensure_ascii=False) + "\n")
        return p

    @staticmethod
    def m(who, text, t):
        return {"who": who, "text": text, "date": "2026-02-01T10:%02d:00+00:00" % t}

    def test_rules_of_the_strict_pass(self):
        m = self.m
        tpl = "Здравствуйте! Спасибо за обращение, скоро ответим"
        good = [m("client", "Добрый день, хочу взять байк на месяц", 0),
                m("company", "Здравствуйте, на какие даты вам нужен байк?", 2)]
        company_first = [m("company", "Привет, у нас скидки на аренду", 0), m("client", "Сколько стоит байк", 1),
                         m("company", "Смотря какой и на какой срок", 2)]
        template_first = [m("client", "Хочу арендовать скутер завтра", 0), m("company", tpl, 0),
                          m("company", "Какой скутер вам интересен?", 3)]
        series = [m("client", "Здравствуйте", 0), m("client", "хочу байк на неделю", 0),
                  m("company", "Добрый день, какие даты нужны?", 1)]
        newline = [m("client", "Здравствуйте\nхочу байк", 0), m("company", "Добрый день, какие даты?", 1)]
        fillers = [[m("client", "вопрос номер %d про аренду" % i, 0), m("company", tpl, 0)]
                   for i in range(10)]
        recs = exam_set.read_base(self.base([good, company_first, template_first, series, newline]
                                            + fillers))
        links, why = exam_set.strict_links(recs, style_examples)
        self.assertEqual([x["dialog"] for x in links], [0])
        self.assertEqual(why["первой пишет компания"], 1)
        self.assertEqual(why["первый ответ — шаблон"], 11)
        self.assertEqual(why["первое обращение — серия"], 1)
        self.assertEqual(why["в вопросе перенос строки"], 1)
        case = exam_set.case_from_link(links[0], "база.jsonl", FP)
        self.assertEqual(case["reference"]["mark"], "д0·р1")
        self.assertNotIn("id", case)
        self.assertNotIn("source_key", case)


class TestLiveDoorRefusesACaseNotInTheSet(Base):
    def setUp(self):
        super(TestLiveDoorRefusesACaseNotInTheSet, self).setUp()
        self.was = (exam_show.CASES, exam_show.SHOTS_DIR, exam_show.LIVE_CASES, exam_show.LIVE_SHOTS)
        exam_show.LIVE_CASES, exam_show.LIVE_SHOTS = self.path, self.shots
        self.legacy([dict(_case(206), id=1)])

    def tearDown(self):
        (exam_show.CASES, exam_show.SHOTS_DIR, exam_show.LIVE_CASES, exam_show.LIVE_SHOTS) = self.was
        super(TestLiveDoorRefusesACaseNotInTheSet, self).tearDown()

    def run_main(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = exam_show.main(argv)
        return rc, buf.getvalue()

    def test_gone_case_is_refused_even_when_its_shot_is_on_disk(self):
        with open(os.path.join(self.shots, "case-2@abc1234.json"), "w", encoding="utf-8") as f:
            json.dump({"case": 2, "draft": "ответ", "built_at": "2026-09-17T00:00:00Z"}, f)
        for door in ("--card", "--slots", "--freeze"):
            rc, out = self.run_main(["--live", "--case", "2", door])
            self.assertEqual(rc, 1, door)
            self.assertIn("в живом наборе нет", out, door)

    def test_case_in_the_set_passes_the_door(self):
        rc, out = self.run_main(["--live", "--case", "1", "--slots"])
        self.assertNotIn("в живом наборе нет", out)
        self.assertIn("слотов кейса 1 нет", out)


class TestReadinessForAHumanRun(Base):
    DRAFT, NOTE, REF = "ответ бота целиком", "записка головы", "Добрый день, на какие даты?"

    def shot(self, case_id, commit, built_at, **fields):
        shot = {"case": case_id, "draft": self.DRAFT, "note": self.NOTE, "built_at": built_at,
                "hints": [{"observation": "замечание", "rule": "правило"}], "critic": {"outcome": "hints"},
                "reference": {"who": "менеджер", "text": self.REF, "mark": "д%d·р1" % case_id},
                "corpus": exam_set.version(self.path), "owner_cards": []}
        shot.update(fields)
        with open(os.path.join(self.shots, "case-%s@%s.json" % (case_id, commit)), "w",
                  encoding="utf-8") as f:
            json.dump(shot, f, ensure_ascii=False)

    def setUp(self):
        super(TestReadinessForAHumanRun, self).setUp()
        self.legacy([dict(_case(5), id=13, source_key=STRICT), dict(_case(22), id=14, source_key=STRICT),
                     dict(_case(57), id=15, source_key=STRICT)])

    def test_each_missing_part_is_named_and_counted(self):
        self.shot(13, "aaa1111", "2026-09-17T14:00:00Z")
        self.shot(14, "aaa1111", "2026-09-17T14:00:00Z", note="", hints=[], critic={"outcome": "silent"})
        got = exam_set.readiness(self.path)
        self.assertEqual((got["cases"], got["ready"]), (3, 1))
        self.assertEqual(got["have"], {"снимок": 2, "ответ бота": 2, "причина": 1, "подсказки": 1,
                                       "эталон": 2})
        rows = dict((r["id"], r) for r in got["rows"])
        self.assertEqual(rows[13]["missing"], [])
        self.assertEqual(rows[14]["missing"], ["причина", "подсказки"])
        self.assertEqual(rows[15]["missing"], list(exam_set.READY_PARTS))
        self.assertIsNone(rows[15]["shot"])

    def test_the_newest_slot_is_judged_because_the_card_shows_it(self):
        self.shot(13, "aaa1111", "2026-09-17T14:00:00Z")
        self.shot(13, "bbb2222", "2026-09-17T15:00:00Z", hints=[])
        row = exam_set.readiness(self.path)["rows"][0]
        self.assertEqual((row["shot"], row["slots"], row["missing"]),
                         ("case-13@bbb2222.json", 2, ["подсказки"]))

    def test_shot_built_on_another_set_version_is_counted_not_hidden(self):
        self.shot(13, "aaa1111", "2026-09-17T14:00:00Z", corpus="e68a554d1c38e960")
        self.shot(14, "aaa1111", "2026-09-17T14:00:00Z")
        got = exam_set.readiness(self.path)
        self.assertEqual(got["corpus_is_set_version"], 1)
        self.assertEqual([r["corpus_is_set_version"] for r in got["rows"]], [False, True, None])

    def test_no_text_leaves_and_nothing_is_written(self):
        self.shot(13, "aaa1111", "2026-09-17T14:00:00Z")
        sha, shot_dir, was = _sha(self.path), sorted(os.listdir(self.shots)), exam_show.SHOTS_DIR
        buf = io.StringIO()
        prev = exam_set.SET_CASES
        exam_set.SET_CASES = self.path
        try:
            with redirect_stdout(buf):
                rc = exam_set.main(["--ready"])
        finally:
            exam_set.SET_CASES = prev
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        for text in (self.DRAFT, self.NOTE, self.REF, "Здравствуйте", "замечание"):
            self.assertNotIn(text, out)
        self.assertEqual(json.loads(out)["ready"], 1)
        self.assertEqual((_sha(self.path), sorted(os.listdir(self.shots)), exam_show.SHOTS_DIR),
                         (sha, shot_dir, was))


if __name__ == "__main__":
    unittest.main(verbosity=2)
