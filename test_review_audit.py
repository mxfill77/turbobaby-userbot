# -*- coding: utf-8 -*-
"""test_review_audit.py — регресс ступени D ревью-контура (находки → тема Аудит).

Шесть вещей здесь важнее остальных и потому стоят отдельными классами:

* ``TestPurity`` — инвариант ``REVIEW_AUDIT_PURE``: чистый модуль решения не смеет
  завести часы, сеть, диск или ``getenv``. Проверяется обходом AST.
* ``TestAddress`` — САМОЕ ДОРОГОЕ здесь. Тема не настроена → наружу не уходит
  НИЧЕГО; настроена → сообщение уходит РОВНО в неё, и ни одна ветка не знает
  фолбэка в инбокс 1160 или в тему постановки 328. Промах адреса у этой ступени
  дороже молчания: находка внешнего канала, севшая в тему заданий, читается как
  задание.
* ``TestWeight`` — «высокая важность» считается по УЖЕ ИЗМЕРЕННЫМ полям, а не по
  чтению текста; стоп-вид ВЫВОДИТСЯ из хвоста пакета, а не набран литералом.
* ``TestLiveCorpus`` — голдены сняты с ЖИВЫХ файлов лотка ``docs/review_inbox``,
  включая дословную фразу находки. Правило-класс полосы: тест детекта обязан
  стоять на реальной фразе, а формат мока — повторять формат источника.
* ``TestDigest`` — четыре числа сводки и ТРЕТИЙ ИСХОД: очередь не ответила →
  «неизвестно», а не «ноль».
* ``TestNegative`` — отправка отказала: реестр НЕ пополняется, причина названа,
  повтор возможен. И бутстрап: первый оборот backlog не вываливает.
"""
from __future__ import annotations

import ast
import io
import json
import os
import tempfile
import unittest

import review_audit as ra
import review_audit_run as run
import review_intake
import review_intake_run
import review_pack

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = review_intake_run.DEFAULT_INBOX

# Живые файлы лотка за 01.09 — на них стоит весь корпусный регресс. Первые два несут
# ОДНУ И ТУ ЖЕ находку («не заявлять «слепых пятен не заявлено»»), приехавшую двумя
# пакетами: ровно тот случай, ради которого заведён дедуп.
LIVE_CHAINS = "%s/2026-09-01-2026-09-01-chains-2026-08-31-codex.md" % INBOX
LIVE_DIGEST = "%s/2026-09-01-2026-09-01-digest-2026-09-01-codex.md" % INBOX
LIVE_REFUSED = "%s/2026-09-01-2026-09-01-digest-2026-09-01-manus.md" % INBOX

# Дословный кусок живой находки — той самой, что склеилась из двух пакетов.
LIVE_DUP_MARK = "слепых пятен не заявлено"

AUDIT_TOPIC_FIXTURE = 4242          # выдуманный номер: боевой живёт в настройке, а не в тесте
FORBIDDEN_TOPICS = (328, 1160, 829, 205)   # темы, в которые ступени D писать запрещено


def _alive_probe(address="review_auto.py:249", anchor="result_head"):
    return [{"anchor": anchor, "form": "literal", "found": True, "frozen": False,
             "address": address, "detail": ""}]


def _item(kind, *, sources=1, premise=None, quote="находка про `result_head`", key=None,
          pack="2026-09-01-digest-2026-09-01.md", channel="codex", answer=LIVE_DIGEST):
    srcs = []
    for n in range(sources):
        srcs.append({"pack": pack if n == 0 else pack.replace("digest", "chains"),
                     "pack_sha256": "%012d" % n, "channel": channel if n == 0 else "manus",
                     "send_date": "2026-09-01", "answer": answer, "index": n + 1,
                     "kind": kind, "quote": quote, "unsplit": False, "refs": []})
    claim = {"schema": review_intake.SCHEMA, "key": key or ("k%011d" % sources),
             "kind": kind, "quote": quote, "sources": srcs,
             "channels": sorted({s["channel"] for s in srcs}),
             "packs": sorted({s["pack"] for s in srcs})}
    return {"claim": claim, "premise": premise or review_intake.premise(_alive_probe())}


class FakeBridge:
    def __init__(self, rows=None, ok=True):
        self.rows, self.ok = rows or [], ok
        self.calls = []

    def get_pending(self, status):
        self.calls.append(status)
        if not self.ok:
            return {"ok": False, "error": "мост не ответил"}
        return {"ok": True, "items": [r for r in self.rows if r.get("status") == status]}


class FakeDaemon:
    def __init__(self, topic=AUDIT_TOPIC_FIXTURE, rows=None, ok=True):
        self.AUDIT_TOPIC = topic
        self.bc = FakeBridge(rows, ok)


class FakeSender:
    """Дверь отправки, запоминающая КУДА и ЧТО уехало."""

    def __init__(self, ok=True, why="канал отказал"):
        self.ok, self.why, self.sent = ok, why, []

    def __call__(self, text, topic):
        self.sent.append({"topic": topic, "text": text})
        if not self.ok:
            return ("topic:%s" % topic, False, self.why)
        return ("topic:%s" % topic, True, str(1000 + len(self.sent)))


def _live_built(files=(LIVE_CHAINS, LIVE_DIGEST, LIVE_REFUSED)):
    """Корпус из НАЗВАННЫХ живых файлов, без прохода по всему дереву."""
    return run.build(HERE, files=list(files), tree=["review_auto.py", "review_pack.py"],
                     tree_why="")


class TestPurity(unittest.TestCase):
    """REVIEW_AUDIT_PURE — чистая логика остаётся чистой."""

    BANNED_CALLS = {"now", "utcnow", "time", "monotonic", "getenv", "open", "run", "Popen",
                    "urlopen"}
    BANNED_IMPORTS = {"os", "subprocess", "socket", "urllib", "time", "shutil", "requests",
                      "datetime"}

    def test_no_clock_no_disk_no_network_no_env(self):
        with io.open(os.path.join(HERE, "review_audit.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="review_audit.py")
        bad_imports, bad_calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad_imports |= {a.name.split(".")[0] for a in node.names} & self.BANNED_IMPORTS
            elif isinstance(node, ast.ImportFrom) and node.module:
                bad_imports |= {node.module.split(".")[0]} & self.BANNED_IMPORTS
            elif isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "attr", None) or getattr(fn, "id", None)
                if name in self.BANNED_CALLS:
                    bad_calls.add(name)
        self.assertEqual(bad_imports, set(), "чистый модуль завёл запрещённый импорт")
        self.assertEqual(bad_calls, set(), "чистый модуль завёл часы/диск/сеть/env")

    def test_module_knows_no_topic_number(self):
        """Номер темы в чистом модуле НЕ ЖИВЁТ — ни дефолтом, ни литералом.

        Адрес — настройка полосы, и зашитое здесь число пережило бы любую её правку.
        """
        with io.open(os.path.join(HERE, "review_audit.py"), encoding="utf-8") as fh:
            text = fh.read()
        for topic in FORBIDDEN_TOPICS + (AUDIT_TOPIC_FIXTURE,):
            self.assertNotIn(str(topic), text, "номер темы %s зашит в чистый модуль" % topic)


class TestWeight(unittest.TestCase):
    """Вес — арифметика по измеренным полям, а не мнение о тексте."""

    def test_stop_kind_is_derived_from_the_pack_tail_not_a_literal(self):
        """Стоп-вид ВЫВЕДЕН из хвоста пакета: правка вопроса поедет сюда сама."""
        self.assertEqual(ra.stop_kind(), review_intake.kind_labels()[1])
        self.assertIn(ra.stop_kind().replace("Ё", "Е"),
                      review_pack.TAIL_QUESTIONS[1].replace("Ё", "Е"))

    def test_premise_is_a_gate_not_a_summand(self):
        """Премиса не ЖИВА → не высокая НИКОГДА, каким бы тяжёлым ни был вид.

        Решать по находке, стоящей на непроверенной посылке, нечего: сперва надо
        узнать, о живом ли коде речь. Такие идут суточной сводкой.
        """
        stop = ra.stop_kind()
        for probes, outcome in ((None, review_intake.PREMISE_UNKNOWN),
                                ([{"anchor": "x", "form": "literal", "found": False,
                                   "frozen": False, "address": "", "detail": "нет"}],
                                 review_intake.PREMISE_STALE)):
            item = _item(stop, sources=3, premise=review_intake.premise(probes))
            self.assertEqual(item["premise"]["outcome"], outcome)
            self.assertFalse(ra.is_high(item), "находка с премисой %s объявлена высокой" % outcome)

    def test_stop_finding_is_high_alone_others_need_agreement(self):
        """Один источник хватает стоп-находке и НЕ хватает остальным видам."""
        stop = ra.stop_kind()
        others = [k for k in review_intake.kind_labels() if k != stop]
        self.assertTrue(ra.is_high(_item(stop, sources=1)))
        for kind in others:
            self.assertFalse(ra.is_high(_item(kind, sources=1)),
                             "«%s» с одним источником объявлен высоким" % kind)
            self.assertTrue(ra.is_high(_item(kind, sources=2)),
                            "«%s» с ДВУМЯ источниками не признан высоким" % kind)

    def test_agreement_is_a_step_not_a_ladder(self):
        """Третий и последующий источник веса уже не добавляют.

        Иначе один многословный канал, повторивший себя в четырёх пакетах,
        перевешивал бы стоп-находку — то есть согласие с самим собой считалось бы
        независимым подтверждением.
        """
        kinds = [k for k in review_intake.kind_labels() if k != ra.stop_kind()]
        alive = review_intake.premise(_alive_probe())
        w1 = ra.weight(_item(kinds[0], sources=1)["claim"], alive)
        w2 = ra.weight(_item(kinds[0], sources=2)["claim"], alive)
        w3 = ra.weight(_item(kinds[0], sources=3)["claim"], alive)
        w9 = ra.weight(_item(kinds[0], sources=9)["claim"], alive)
        self.assertEqual(w1["score"], 0)
        self.assertEqual(w2["score"], ra.AGREEMENT_WEIGHT)
        self.assertEqual(w3["score"], w9["score"])
        self.assertEqual(w3["score"], w2["score"])

    def test_rank_is_deterministic(self):
        """Порядок «трёх самых весомых» не меняется от прогона к прогону."""
        items = [_item(k, sources=n, key="key%d%d" % (i, n))
                 for i, k in enumerate(review_intake.kind_labels()) for n in (1, 2, 3)]
        first = [i["claim"]["key"] for i in ra.rank(items)]
        second = [i["claim"]["key"] for i in ra.rank(list(reversed(items)))]
        self.assertEqual(first, second)


class TestLiveCorpus(unittest.TestCase):
    """Голдены — на живых файлах лотка и дословных фразах канала."""

    @classmethod
    def setUpClass(cls):
        cls.built = _live_built()

    def test_duplicate_from_two_packs_is_one_message_with_two_sources(self):
        """Требование дедупа — ОДНО сообщение и ОБА источника в нём.

        Находка «не заявлять «слепых пятен не заявлено»» приехала двумя пакетами;
        склейку сделала ступень B, а здесь проверяется, что показ её не разорвал
        обратно и не потерял половину.
        """
        dup = [i for i in self.built["claims"]
               if LIVE_DUP_MARK in (i["claim"]["quote"] or "")
               and len(i["claim"]["sources"]) > 1]
        self.assertTrue(dup, "живой дубль двух пакетов в корпусе не найден")
        item = dup[0]
        text = ra.finding_message(item, "2026-09-01")
        self.assertEqual(text.count("\nпакет: "), 1)     # ОДНА строка пакетов, а не два сообщения
        packs = [s["pack"] for s in item["claim"]["sources"]]
        for pack in packs:
            self.assertIn(pack, text, "источник %s потерян в сообщении" % pack)
        self.assertIn("источников: %d" % len(packs), text)
        for src in item["claim"]["sources"]:
            self.assertIn(src["answer"], ra.artifact_line(item["claim"]))

    def test_refused_channel_carries_no_findings_but_is_counted(self):
        """Отказ канала находок не несёт — и всё равно попадает в числа суток.

        Слить «канал отказал» с «канал ничего не нашёл» значило бы объявить
        молчание мнением.
        """
        heads = {h["rel"]: h for h in self.built["headers"]}
        self.assertIn(LIVE_REFUSED, heads)
        self.assertFalse(heads[LIVE_REFUSED]["answered"])
        stats = ra.day_stats(self.built["headers"], self.built["claims"], "2026-09-01")
        self.assertGreaterEqual(stats["refused"], 1)
        self.assertTrue(stats["reasons"], "причина отказа не названа ни одна")
        self.assertNotIn(LIVE_REFUSED,
                         [s["answer"] for i in self.built["claims"]
                          for s in i["claim"]["sources"]])


class TestMessage(unittest.TestCase):
    """Сообщение: первая строка, пять полей задания, один экран."""

    def test_first_line_says_do_not_execute(self):
        text = ra.finding_message(_item(ra.stop_kind()), "2026-09-01")
        self.assertEqual(text.splitlines()[0], ra.NOT_EXEC_HEAD)
        self.assertEqual(text.splitlines()[0], "НЕ ИСПОЛНЯТЬ: мнение внешнего канала")

    def test_five_required_fields_are_present(self):
        """Пакет, канал, вердикт, находка одной строкой, имя артефакта."""
        item = _item(ra.stop_kind(), quote="1. НЕ ДЕЛАТЬ ВОВСЕ: не заявлять `result_head` дважды.")
        text = ra.finding_message(item, "2026-09-01", queue_id=91)
        for mark in ("пакет: ", "канал: ", "вердикт: ", "находка: ", "полный текст: "):
            self.assertIn(mark, text, "поле «%s» пропало из сообщения" % mark)
        self.assertIn("2026-09-01-digest-2026-09-01.md", text)
        self.assertIn(ra.stop_kind(), text)
        self.assertIn("премиса ЖИВА", text)
        self.assertIn("#91", text)
        # «одной строкой» — буквально: у находки ровно одна строка.
        body = [ln for ln in text.splitlines() if ln.startswith("находка: ")]
        self.assertEqual(len(body), 1)

    def test_one_line_cuts_on_a_phrase_boundary_and_says_so(self):
        long = "1. " + ("Это довольно длинная фраза про адрес `review_auto.py`. " * 20)
        line = ra.one_line(long)
        self.assertLessEqual(len(line), ra.FINDING_LINE_MAX + 2)
        self.assertTrue(line.endswith("…"))
        self.assertFalse(line.startswith("1. "), "номер пункта не снят")

    def test_message_fits_the_telegram_ceiling(self):
        item = _item(ra.stop_kind(), sources=6, quote="ц" * 5000)
        self.assertLessEqual(len(ra.finding_message(item, "2026-09-01")), ra.MESSAGE_MAX)

    def test_quote_that_trips_the_outbound_guard_is_named_not_dropped(self):
        """Задержанная цитата НЕ глушит сообщение: строку заменяет пометка.

        Молчание было бы хуже — владелец не узнал бы даже, что находка есть, а
        путь к файлу с полным текстом в сообщении и так стоит.
        """
        item = _item(ra.stop_kind(), quote=r"смотри путь D:\turbobaby-bot\suggest.py")
        text = ra.finding_message(item, "2026-09-01")
        self.assertIn("страж исходящего задержал форму", text)
        self.assertNotIn("turbobaby-bot", text)
        self.assertIn("полный текст: ", text)
        self.assertEqual(ra.outbound_safe(text), [])


class TestDigest(unittest.TestCase):
    """Суточная сводка: четыре числа задания и третий исход."""

    def setUp(self):
        self.headers = [
            {"rel": "a", "pack": "p1", "channel": "codex", "send_date": "2026-09-01",
             "outcome": "answered", "reason": "", "answered": True},
            {"rel": "b", "pack": "p1", "channel": "manus", "send_date": "2026-09-01",
             "outcome": "refused", "reason": "http_400", "answered": False},
            {"rel": "c", "pack": "p2", "channel": "manus", "send_date": "2026-09-01",
             "outcome": "refused", "reason": "http_400", "answered": False},
            {"rel": "d", "pack": "p9", "channel": "codex", "send_date": "2026-08-31",
             "outcome": "answered", "reason": "", "answered": True},
        ]
        self.items = [_item(ra.stop_kind(), sources=2, key="aaa"),
                      _item(review_intake.kind_labels()[0], sources=1, key="bbb")]

    def test_numbers_count_attempts_not_findings(self):
        st = ra.day_stats(self.headers, self.items, "2026-09-01")
        self.assertEqual(st["packs"], 2)          # p1 и p2; p9 — другой день
        self.assertEqual(st["attempts"], 3)
        self.assertEqual(st["answered"], 1)
        self.assertEqual(st["refused"], 2)
        self.assertEqual(st["reasons"], [("http_400", 2)])
        self.assertEqual(st["findings"], 3)       # 2 источника + 1
        self.assertEqual(st["claims"], 2)
        self.assertEqual(st["merged"], 1)
        self.assertEqual(st["high"], 1)

    def test_queue_silence_is_unknown_not_zero(self):
        """Мост не ответил → «неизвестно». Ноль означал бы «заявок не заводили»."""
        st = ra.day_stats(self.headers, self.items, "2026-09-01")
        text = ra.digest_message(st, "2026-09-01", placed=None, shown_today=0)
        self.assertIn("заявок заведено в очередь: неизвестно", text)
        text2 = ra.digest_message(st, "2026-09-01", placed=3, placed_ids=[87, 88, 89],
                                  shown_today=0)
        self.assertIn("заявок заведено в очередь: 3 (#87, #88, #89)", text2)

    def test_three_heaviest_and_the_named_remainder(self):
        items = [_item(ra.stop_kind(), sources=n, key="k%d" % n) for n in (1, 2, 3, 4)]
        st = ra.day_stats(self.headers, items, "2026-09-01")
        text = ra.digest_message(st, "2026-09-01", placed=0, shown_today=1,
                                 note="остаток назван")
        self.assertIn("ТРИ САМЫХ ВЕСОМЫХ", text)
        self.assertEqual(len([ln for ln in text.splitlines() if ln[:2] in ("1.", "2.", "3.")]), 3)
        self.assertNotIn("\n4. ", text)
        self.assertIn("высокой важности: 4 · показано немедленно: 1", text)
        self.assertIn("остаток назван", text)

    def test_first_line_says_do_not_execute(self):
        st = ra.day_stats(self.headers, self.items, "2026-09-01")
        text = ra.digest_message(st, "2026-09-01", placed=0, shown_today=0)
        self.assertEqual(text.splitlines()[0], ra.NOT_EXEC_HEAD)
        self.assertLessEqual(len(text), ra.MESSAGE_MAX)

    def test_digest_covers_a_finished_day(self):
        """Автоматическая сводка — про ЗАКОНЧЕННЫЙ день, включая границы месяца и года."""
        self.assertEqual(ra.previous_day("2026-09-01"), "2026-08-31")
        self.assertEqual(ra.previous_day("2026-03-01"), "2026-02-28")
        self.assertEqual(ra.previous_day("2024-03-01"), "2024-02-29")
        self.assertEqual(ra.previous_day("2026-01-01"), "2025-12-31")
        self.assertEqual(ra.previous_day("2026-09-15"), "2026-09-14")
        with self.assertRaises(ra.ReviewAuditError):
            ra.previous_day("вчера")


class TestAddress(unittest.TestCase):
    """Адрес: живая настройка, ровно одна тема, фолбэков нет ни одного."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="review_audit_")
        self.state = os.path.join(self.tmp, "state.json")
        self.built = _live_built()

    def test_unset_topic_sends_nothing_at_all(self):
        """Тема не настроена → наружу НЕ УХОДИТ НИЧЕГО, и причина названа."""
        sender = FakeSender()
        report = run.tick(HERE, state_path=self.state, send=True, force=True,
                          daemon=FakeDaemon(topic=0), sender=sender, built=self.built)
        self.assertEqual(sender.sent, [], "при ненастроенной теме что-то уехало наружу")
        self.assertIn("PC_AUDIT_TOPIC", report["why"])
        self.assertFalse(os.path.exists(self.state), "реестр записан при несделанном показе")

    def test_everything_goes_to_the_named_topic_only(self):
        """Всё, что уехало, уехало РОВНО в тему настройки — и ни во что другое."""
        sender = FakeSender()
        run.tick(HERE, state_path=self.state, send=True, force=True, limit=2,
                 daemon=FakeDaemon(), sender=sender, digest=True, day="2026-09-01",
                 built=self.built)
        self.assertTrue(sender.sent, "не ушло ничего — проба бессмысленна")
        for msg in sender.sent:
            self.assertEqual(msg["topic"], AUDIT_TOPIC_FIXTURE)
            self.assertNotIn(msg["topic"], FORBIDDEN_TOPICS)

    def test_no_fallback_cascade_in_the_source(self):
        """У ступени нет ни одной ветки в инбокс/личку — проверяем ТЕКСТОМ модуля.

        Каскад ``send_topic`` (тема → 1160 → личка) прав там, где он живёт, и
        запрещён здесь: чужое мнение, севшее в тему ответа владельца, читается как
        задание. Дверь одна — ``send_topic_strict``.
        """
        with io.open(os.path.join(HERE, "review_audit_run.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("send_topic_strict", text)
        # Ищем ВЫЗОВЫ (со скобкой), а не упоминания: имена соседних тем законно стоят в
        # докстринге — именно там объяснено, почему туда писать нельзя.
        for banned in ("send_critical(", "send_topic(", "dispatch_notify.send(",
                       "INBOX_THREAD_ID", "TASKS_THREAD_ID", "INBOX_TOPIC_ID"):
            self.assertNotIn(banned, text, "ступень D знает запрещённый канал: %s" % banned)

    def test_strict_door_refuses_an_unnamed_topic(self):
        import dispatch_notify

        channel, ok, why = dispatch_notify.send_topic_strict("текст", 0)
        self.assertFalse(ok)
        self.assertEqual(channel, "none")
        self.assertIn("тема не названа", why)


class TestNegative(unittest.TestCase):
    """Отрицательные пробы: отказ канала, бутстрап, повторный показ."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="review_audit_neg_")
        self.state = os.path.join(self.tmp, "state.json")
        self.built = _live_built()

    def test_failed_send_is_not_registered_and_can_repeat(self):
        """Отправка не прошла → в реестр НЕ пишем: иначе находка исчезнет молча."""
        sender = FakeSender(ok=False, why="code=403 bot was kicked")
        report = run.tick(HERE, state_path=self.state, send=True, force=True, limit=1,
                          daemon=FakeDaemon(), sender=sender, built=self.built)
        self.assertEqual(report["sent"], [])
        self.assertTrue(report["failed"])
        self.assertIn("403", report["failed"][0]["why"])
        with io.open(self.state, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["shown"], {}, "несделанный показ попал в реестр")

    def test_bootstrap_does_not_flood_the_topic(self):
        """Первый оборот backlog НЕ вываливает, но и не теряет: остаток назван словом."""
        sender = FakeSender()
        report = run.tick(HERE, state_path=self.state, send=True, digest=False,
                          daemon=FakeDaemon(), sender=sender, built=self.built)
        self.assertEqual(report["sent"], [])
        self.assertEqual(sender.sent, [])
        self.assertTrue(report["bootstrap"])
        with io.open(self.state, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertTrue(state["bootstrap_at"])
        self.assertTrue(state["held"], "backlog не назван в реестре — он потерян")
        for row in state["held"].values():
            self.assertIn("не показывали", row["why"])

    def test_shown_once_is_not_shown_twice(self):
        """Реестр держит дедуп ПО НАБОРУ ключей — и после «рестарта» тоже."""
        sender = FakeSender()
        first = run.tick(HERE, state_path=self.state, send=True, force=True, limit=1,
                         digest=False, daemon=FakeDaemon(), sender=sender, built=self.built)
        self.assertEqual(len(first["sent"]), 1)
        again = run.tick(HERE, state_path=self.state, send=True, limit=1, digest=False,
                         daemon=FakeDaemon(), sender=sender, built=self.built)
        self.assertNotIn(first["sent"][0]["key"], [r["key"] for r in again["sent"]])
        self.assertEqual(len(sender.sent), 1 + len(again["sent"]))

    def test_daily_budget_caps_the_flow(self):
        """Потолок суток держится, а остаток называется словом, а не молчанием."""
        sender = FakeSender()
        report = run.tick(HERE, state_path=self.state, send=True, force=True, budget=1,
                          digest=False, daemon=FakeDaemon(), sender=sender, built=self.built)
        self.assertLessEqual(len(report["sent"]), 1)
        report2 = run.tick(HERE, state_path=self.state, send=True, budget=1, digest=False,
                           daemon=FakeDaemon(), sender=sender, built=self.built)
        held = dict((why, 1) for _k, why in report2["held"])
        self.assertTrue(any("бюджет" in why or "уже показана" in why or "не высокой" in why
                            for why in held), "остаток не назван словом")

    def test_queue_silence_does_not_stop_the_show(self):
        """Мост молчит → номер ряда просто не появится, но находка покажется.

        Показ не зависит от очереди ни одной веткой: путь к полному тексту в
        сообщении есть и без неё.
        """
        sender = FakeSender()
        report = run.tick(HERE, state_path=self.state, send=True, force=True, limit=1,
                          digest=False, daemon=FakeDaemon(ok=False), sender=sender,
                          built=self.built)
        self.assertEqual(len(report["sent"]), 1)
        self.assertNotIn("заявка очереди", sender.sent[0]["text"])

    def test_stage_d_never_writes_to_the_queue(self):
        """Ступень D очередь только ЧИТАЕТ: ни одного вызова записи в её руках."""
        with io.open(os.path.join(HERE, "review_audit_run.py"), encoding="utf-8") as fh:
            text = fh.read()
        for banned in ("enqueue_pc_task", "claim_task", "set_needs_approval", "complete_task"):
            self.assertNotIn(banned, text, "ступень D умеет писать в очередь: %s" % banned)


class TestDaemonWiring(unittest.TestCase):
    """Врезка в демона: ручка, рубильники, троттлинг."""

    def setUp(self):
        import pc_orchestrator

        self.o = pc_orchestrator

    def test_topic_knob_has_no_default_number(self):
        """У темы Аудит дефолта нет СОЗНАТЕЛЬНО (см. комментарий у ручки)."""
        self.assertTrue(hasattr(self.o, "AUDIT_TOPIC"))
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('AUDIT_TOPIC = int(os.getenv("PC_AUDIT_TOPIC", "0") or "0")', text)

    def test_turn_is_throttled_and_switchable(self):
        tmp = tempfile.mkdtemp(prefix="review_audit_tick_")
        tick_path = os.path.join(tmp, "tick.json")
        calls = []

        def runner(**kw):
            calls.append(kw)
            return {"acted": False, "why": "нечего", "topic": 1, "sent": [], "failed": []}

        self.o.maybe_review_audit(now=1000.0, tick_path=tick_path, runner=runner)
        self.assertEqual(len(calls), 1)
        self.o.maybe_review_audit(now=1000.0 + self.o.REVIEW_AUDIT_MIN_SEC - 1,
                                  tick_path=tick_path, runner=runner)
        self.assertEqual(len(calls), 1, "пол паузы не держит")
        self.o.maybe_review_audit(now=1000.0 + self.o.REVIEW_AUDIT_MIN_SEC + 1,
                                  tick_path=tick_path, runner=runner)
        self.assertEqual(len(calls), 2)

    def test_turn_is_called_from_the_loop(self):
        with io.open(os.path.join(HERE, "pc_orchestrator.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("maybe_review_audit()", text)

    def test_lazy_dependency_is_a_named_remainder(self):
        self.assertIn("review_audit_run.py", self.o._ORCH_LAZY_UNCOVERED)
        self.assertIn("review_audit.py", self.o._ORCH_LAZY_UNCOVERED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
