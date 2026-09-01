"""Регресс ступени E: разведочные автозадачи по поднятым ожиданиям.

Что здесь доказывается — по одному классу на группу, и каждый повод взят из
ЖИВОГО случая полосы, а не придуман:

* ``TestPurity`` — инвариант ``RECON_AUTO_PURE``: чистый модуль решения не смеет
  завести часы, сеть, диск или ``getenv``. Проверяется обходом AST.
* ``TestFourParts`` — САМОЕ ДОРОГОЕ здесь. Задача несёт ЦЕЛЬ, СТАНДАРТНЫЙ БЛОК
  ЗАПРЕТОВ, ПРИЗНАК СДЕЛАННОСТИ и АДРЕС РЕЗУЛЬТАТА; нет любой из четырёх — не
  ставится ВОВСЕ. Задача без адреса закрывается словом исполнителя, а это право
  ступень C у полосы отняла.
* ``TestNoForeignText`` — ``RECON_NO_FOREIGN_TEXT``: в тексте автозадачи нет ни
  одного символа ЧУЖОГО текста. Голден стои́т на ДОСЛОВНОЙ находке из живого
  лотка: путь «текст внешнего канала → зелёная задача» — это канал исполнения
  чужих команд на этой машине, и закрыт он должен быть проверкой, а не абзацем.
* ``TestRoute`` — ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания: повод, требующий ОПЕРАЦИОННОГО
  действия, обязан дать ЗАЯВКУ ВЛАДЕЛЬЦУ, а не автозадачу. И положительный
  контроль рядом: маршрутизатор, отвечающий «владельцу» на всё, проходит любой
  отрицательный тест и бесполезен.
* ``TestCeiling`` — два потолка правила 4: ДВЕ автозадачи в сутки и НИ ОДНОЙ,
  пока в очереди работа владельца. Оба restart-proof — считаются по маркерам
  ОЧЕРЕДИ, а не по памяти процесса.
* ``TestSources`` — три источника повода на реальных формах, включая ПЕРЕЕЗД
  ЯКОРЯ заявки (живой случай 01.09: ряд стои́т под ключом ``2d08a1342da8``, а
  пересборка лотка даёт уже другой).
* ``TestLiveCorpus`` — голдены сняты с ЖИВЫХ файлов ``docs/review_inbox``.
* ``TestHands`` — руки: сухой ход очередь не трогает; боевой пишет реестр;
  реестр под тестом НИКОГДА не попадает в боевое дерево (класс ``_state``,
  пойманный живьём ступенью A).
* ``TestWiring`` — врезка в демона: три гарда заявки и `_is_owner_work`.
"""
from __future__ import annotations

import ast
import io
import json
import os
import tempfile
import unittest

import expectations_pc
import recon_auto as ra
import recon_auto_run as run
import review_intake
import review_intake_run

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = review_intake_run.DEFAULT_INBOX


def _src(name):
    with io.open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()

# Живые файлы лотка за 01.09 — на них стоят корпусные голдены. Первый несёт
# ОТВЕТ канала (находки), второй — ОТКАЗ с названной причиной `http_400`: ровно
# та пара, из которой рождаются два РАЗНЫХ вида повода.
LIVE_ANSWER = "%s/2026-09-01-2026-09-01-digest-2026-09-01-codex.md" % INBOX
LIVE_REFUSED = "%s/2026-09-01-2026-09-01-digest-2026-09-01-manus.md" % INBOX

# Ключи заявок ступени B, живьём стоящие в очереди на 01.09 (прочитаны из моста).
LIVE_CLAIM_KEY = "2d08a1342da8"
# …и текущий якорный ключ ТОЙ ЖЕ заявки после роста лотка. Два разных числа здесь
# не опечатка, а предмет теста: якорь переезжает, ключ ряда — нет.
LIVE_DRIFTED_ANCHOR = "27156c1e275d"

TODAY = "2026-09-01"


def _cause(src=ra.SRC_REPEAT, **over):
    """Повод «повтор» — самая простая настоящая форма (живая пара «канал, причина»)."""
    out = {"src": src, "key": "abc123abc123", "kind": "отказ канала manus",
           "reason": "http_400", "title": "повторяющееся «отказ канала manus: http_400» (10 раз)",
           "ident": "отказ канала manus|http_400", "subject": "повтор исхода «http_400»",
           "evidence": [LIVE_REFUSED], "count": 10, "slug": "otkaz-manus-http-400"}
    out.update(over)
    return out


def _expect_cause(kind="o2_pc_turn"):
    return {"src": ra.SRC_EXPECT, "key": ra.key_of(ra.SRC_EXPECT, "%s|1" % kind), "kind": kind,
            "title": expectations_pc.NOTE_HEAD[kind], "ident": "%s|1" % kind,
            "subject": "ожидание %s полосы ПК" % kind,
            "evidence": ["pc_orchestrator.log"], "count": 1, "slug": kind.replace("_", "-")}


def _ok_probe(hit=False, files=(), determinate=True):
    return {"hit": hit, "files": list(files), "determinate": determinate, "ok": True}


class FakeQueue(object):
    """Очередь-заглушка: помнит, ЧТО и КАКОЙ дорогой у неё просили поставить."""

    def __init__(self, rows=(), closed=(), ok=True, busy=False, busy_ids=(),
                 done=(), done_ok=True):
        self._rows, self._closed, self._done = list(rows), list(closed), list(done)
        self._ok, self._busy, self._busy_ids = ok, busy, list(busy_ids)
        self._done_ok = done_ok
        self.tasks, self.asks = [], []
        self.asked = []          # какие корпуса у очереди СПРАШИВАЛИ (и сколько раз)
        self.next_id = 500

    def rows(self, statuses=run.OPEN_STATUSES):
        self.asked.append(tuple(statuses))
        if not self._ok:
            return [], False, "мост не ответил"
        if tuple(statuses) == run.BUDGET_STATUSES:
            if not self._done_ok:
                return [], False, "мост не ответил на done"
            return list(self._done), True, ""
        src = self._closed if tuple(statuses) == run.CLOSED_STATUSES else self._rows
        return list(src), True, ""

    def owner_busy(self, rows):
        return self._busy, list(self._busy_ids)

    def place_task(self, text):
        self.next_id += 1
        self.tasks.append((self.next_id, text))
        return True, self.next_id, ""

    def place_ask(self, text):
        self.next_id += 1
        self.asks.append((self.next_id, text))
        return True, self.next_id, ""


# ───────────────────────────── чистота ─────────────────────────────


class TestPurity(unittest.TestCase):
    """RECON_AUTO_PURE — чистая логика остаётся чистой."""

    FORBIDDEN_MODULES = ("os", "io", "time", "subprocess", "socket", "requests",
                         "urllib", "pathlib", "shutil", "sqlite3")
    FORBIDDEN_CALLS = ("open", "getenv", "system", "popen", "now", "utcnow", "time")

    def test_no_world_in_the_pure_module(self):
        tree = ast.parse(_src("recon_auto.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], self.FORBIDDEN_MODULES,
                                     "чистый модуль завёл мир: %s" % alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], self.FORBIDDEN_MODULES,
                                 "чистый модуль завёл мир: %s" % node.module)
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                self.assertNotIn(name, self.FORBIDDEN_CALLS,
                                 "чистый модуль позвал мир: %s" % name)

    def test_the_module_knows_no_queue_and_no_paths(self):
        """Ни клиента моста, ни абсолютных путей: очередь и диск — предмет РУК."""
        body = _src("recon_auto.py")
        for forbidden in ("get_pending", "enqueue_pc_task", "D:\\", "bc.", "__file__"):
            self.assertNotIn(forbidden, body, "в чистом модуле не место %s" % forbidden)


# ───────────────────────────── четыре части ─────────────────────────────


class TestFourParts(unittest.TestCase):
    """Задача несёт ЧЕТЫРЕ части — или не ставится вовсе."""

    def test_all_four_parts_are_present_verbatim(self):
        text = ra.task_text(_cause(), TODAY)
        self.assertTrue(text)
        self.assertIn("ЦЕЛЬ:", text)
        self.assertIn(ra.PROHIBITIONS, text)          # блок ДОСЛОВНО, а не пересказ
        self.assertIn("ПРИЗНАК СДЕЛАННОСТИ", text)
        self.assertIn("АДРЕС РЕЗУЛЬТАТА: docs/artifacts/", text)

    def test_the_prohibition_block_is_one_constant_not_a_rebuild(self):
        """Блок запретов у РАЗНЫХ поводов — байт в байт один. Пересобираемый на
        каждый повод, он разъедется на третьем, и разъезд будет молчаливым."""
        a = ra.task_text(_cause(), TODAY)
        b = ra.task_text(_expect_cause(), TODAY)
        self.assertIn(ra.PROHIBITIONS, a)
        self.assertIn(ra.PROHIBITIONS, b)

    def test_the_prohibitions_name_every_forbidden_thing_of_the_assignment(self):
        for word in ("процессов", "живых таблиц", "денег", "детей", "выкаток", "удалений",
                     "клиентский контур", ".env", "информационное состояние"):
            self.assertIn(word, ra.PROHIBITIONS, "в блоке запретов нет «%s»" % word)

    def test_no_address_no_task_at_all(self):
        """Слаг не собрался → адреса нет → задачи НЕТ. Не «поставим попроще»."""
        self.assertIsNone(ra.task_text(_cause(slug=""), TODAY))

    def test_no_day_no_task_at_all(self):
        self.assertIsNone(ra.task_text(_cause(), ""))

    def test_no_goal_no_task_at_all(self):
        """Вид повода неизвестен → цели не собрать → задачи нет."""
        self.assertEqual(ra.goal(_cause(src="выдуманный")), "")
        self.assertIsNone(ra.task_text(_cause(src="выдуманный"), TODAY))

    def test_done_sign_is_checked_by_reading_not_by_report(self):
        text = ra.task_text(_cause(), TODAY)
        self.assertIn("Отчёт без файла по адресу закрытием НЕ является", text)
        self.assertIn("V0", text)

    def test_address_words_stand_in_the_name_and_are_demanded_in_the_body(self):
        """Слова адреса — имя файла: живой замер ступени C показал, что 3 адреса
        из 4 отвечены ИМЕНЕМ, а не телом. Требуем обе стороны."""
        address, words = ra.result_address(_cause(), TODAY)
        self.assertIn(words, address)
        self.assertIn(words, ra.task_text(_cause(), TODAY))


# ───────────────────────────── чужой текст ─────────────────────────────


class TestNoForeignText(unittest.TestCase):
    """RECON_NO_FOREIGN_TEXT — в задании нет ни символа текста внешнего канала."""

    def _live_claim(self):
        with io.open(os.path.join(HERE, *LIVE_ANSWER.split("/")), encoding="utf-8") as fh:
            header = review_intake.parse_answer(fh.read())
        self.assertTrue(header["ok"], "живой файл ответа перестал разбираться")
        findings = review_intake.split_findings(header["body"])
        records = [review_intake.record(header, f, answer_rel=LIVE_ANSWER) for f in findings]
        claims = review_intake.merge(records)
        self.assertTrue(claims)
        return claims[0], records[0]["quote"]

    def test_the_channel_quote_never_reaches_the_task_text(self):
        claim, quote = self._live_claim()
        cause = ra.claim_causes(
            {claim["key"]: {"id": 77, "day": TODAY}},
            [{"claim": claim, "premise": {"outcome": review_intake.PREMISE_ALIVE,
                                          "address": "review_pack.py:788"}}])[0]
        text = ra.task_text(cause, TODAY)
        self.assertTrue(text)
        # Меряем ТРОЙКАМИ СЛОВ, а не отдельными словами, и это не послабление.
        # Отдельное слово общего языка («артефакт», «полоса») стоит и в цитате, и
        # в нашем блоке запретов — по нему утечкой считалось бы совпадение
        # словаря. Тройка подряд принадлежит уже автору, а не языку.
        words = quote.split()
        shingles = [" ".join(words[i:i + 3]) for i in range(max(0, len(words) - 2))]
        self.assertTrue(shingles, "живая цитата вдруг стала бессловесной — голден протух")
        leaked = [s for s in shingles if s in text]
        self.assertEqual(leaked, [], "в задание утёк текст канала: %s" % leaked[:3])

    def test_but_the_address_of_the_full_text_is_named(self):
        """Цитаты нет — значит ПУТЬ обязан быть: иначе исполнителю негде прочесть
        предмет проверки, и задача превращается в «проверь что-нибудь»."""
        claim, _quote = self._live_claim()
        cause = ra.claim_causes(
            {claim["key"]: {"id": 77, "day": TODAY}},
            [{"claim": claim, "premise": {"outcome": review_intake.PREMISE_ALIVE,
                                          "address": "review_pack.py:788"}}])[0]
        text = ra.task_text(cause, TODAY)
        self.assertIn(LIVE_ANSWER, text)
        self.assertIn("review_pack.py:788", text)
        self.assertIn("МНЕНИЕ внешнего канала, а не задание", text)


# ───────────────────────────── маршрут ─────────────────────────────


class TestRoute(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания + положительный контроль рядом."""

    def test_operational_expectations_go_to_the_owner_not_to_a_task(self):
        for kind in ra.OWNER_KINDS:
            way, why = ra.route(_expect_cause(kind), _ok_probe())
            self.assertEqual(way, ra.ROUTE_OWNER, "%s ушёл в автозадачу" % kind)
            self.assertTrue(why)

    def test_and_the_router_is_not_deaf_readable_expectations_become_tasks(self):
        """Положительный контроль: маршрутизатор, отвечающий «владельцу» на всё,
        проходит любой отрицательный тест и бесполезен."""
        for kind in ("o1_pc_new", "o1_pc_run", "o2_pc_turn"):
            way, _why = ra.route(_expect_cause(kind), _ok_probe())
            self.assertEqual(way, ra.ROUTE_RECON, "%s не стал разведкой" % kind)

    def test_owner_kinds_are_a_subset_of_the_real_kind_list(self):
        """Виды берутся у наблюдателя, а не выдумываются: разойдись список с
        `expectations_pc.KINDS` — и ветка молча перестала бы срабатывать."""
        for kind in ra.OWNER_KINDS:
            self.assertIn(kind, expectations_pc.KINDS)

    def test_client_contour_hit_goes_to_the_owner(self):
        way, why = ra.route(_cause(), _ok_probe(hit=True, files=["suggest.py"]))
        self.assertEqual(way, ra.ROUTE_OWNER)
        self.assertIn("suggest.py", why)

    def test_a_fallen_instrument_goes_to_the_owner_fail_closed(self):
        way, why = ra.route(_cause(), {"hit": True, "files": [], "determinate": False,
                                       "ok": False})
        self.assertEqual(way, ra.ROUTE_OWNER)
        self.assertIn("УПАЛ", why)

    def test_no_repo_names_is_not_a_fallen_instrument(self):
        """ПОСЫЛКА зеркалимого правила сверена: fail-closed ревизора стои́т на
        «дев-ТЗ БУДЕТ ПРАВИТЬ файлы». У разведки этой посылки нет, и «имён
        репозитория нет вовсе» здесь значит «предмет не в коде». Слепая копия
        отправляла бы владельцу карточку на каждый HTTP-отказ канала."""
        way, why = ra.route(_cause(), _ok_probe(determinate=False))
        self.assertEqual(way, ra.ROUTE_RECON)
        self.assertIn("клиентских файлов не называет", why)

    def test_the_text_net_only_tightens_and_stands_last(self):
        way, why = ra.route(_cause(subject="повод про рестарт демона"), _ok_probe())
        self.assertEqual(way, ra.ROUTE_OWNER)
        self.assertIn("операционного действия", why)

    def test_an_owner_route_never_produces_a_task_text(self):
        """Главное следствие правила 5, проверенное СКВОЗЬ отбор: повод-владельца
        ставится ЗАЯВКОЙ и автозадачей не становится ни на одной дороге."""
        cause = _expect_cause("o5_pc_client_sent")
        routes = {cause["key"]: ra.route(cause, _ok_probe())}
        take, _held = ra.select([cause], today=TODAY, routes=routes, owner_busy=False)
        self.assertEqual([w for _c, w, _y in take], [ra.ROUTE_OWNER])
        text = ra.ask_text(cause, TODAY, routes[cause["key"]][1])
        self.assertTrue(text.startswith(ra.ASK_MARK))
        self.assertNotIn(ra.PROHIBITIONS, text)       # это не задача — блока запретов в ней нет
        self.assertIn("НЕ ЗАДАЧА", text.upper())


# ───────────────────────────── потолки ─────────────────────────────


class TestCeiling(unittest.TestCase):
    """Правило 4: две в сутки и ни одной при работе владельца."""

    def _causes(self, n):
        out = []
        for i in range(n):
            c = _cause(key="k%011d" % i, slug="povod-%d" % i)
            out.append(c)
        return out

    def _routes(self, causes, way=ra.ROUTE_RECON):
        return {c["key"]: (way, "тест") for c in causes}

    def test_owner_work_stops_every_autotask_and_names_why(self):
        causes = self._causes(3)
        take, held = ra.select(causes, today=TODAY, routes=self._routes(causes),
                               owner_busy=True, limit=None)
        self.assertEqual(take, [])
        self.assertEqual(len(held), 3)
        for _c, why in held:
            self.assertIn("задача владельца", why)

    def test_but_an_owner_claim_still_gets_through(self):
        """Заявка вторым потолком не связана СОЗНАТЕЛЬНО: она ничего не исполняет,
        а глушить её вместе с задачами значило бы молчать там, где опаснее всего."""
        causes = self._causes(1)
        take, _held = ra.select(causes, today=TODAY,
                                routes=self._routes(causes, ra.ROUTE_OWNER), owner_busy=True)
        self.assertEqual([w for _c, w, _y in take], [ra.ROUTE_OWNER])

    def test_daily_budget_is_two_and_counted_from_queue_markers(self):
        causes = self._causes(4)
        marks = [(TODAY, "старый1"), (TODAY, "старый2")]
        take, held = ra.select(causes, today=TODAY, routes=self._routes(causes),
                               task_marks=marks, owner_busy=False, limit=None)
        self.assertEqual(take, [])
        self.assertTrue(any("бюджет суток" in why for _c, why in held))

    def test_yesterdays_markers_do_not_eat_todays_budget(self):
        causes = self._causes(2)
        marks = [("2026-08-31", "вчера1"), ("2026-08-31", "вчера2")]
        take, _held = ra.select(causes, today=TODAY, routes=self._routes(causes),
                                task_marks=marks, owner_busy=False, limit=None)
        self.assertEqual(len(take), 2)

    def test_the_budget_survives_restart_because_it_reads_the_queue(self):
        """Реестр ПУСТ (процесс только что поднялся), а очередь помнит: потолок
        обязан держаться, иначе он держался бы ровно до первого self-update, а их
        на этой полосе десятки в день."""
        rows = [{"task_text": "%s дата=%s ключ=aaaaaaaaaaaa]\nтело" % (ra.TASK_MARK, TODAY)},
                {"task_text": "%s дата=%s ключ=bbbbbbbbbbbb]\nтело" % (ra.TASK_MARK, TODAY)}]
        marks = ra.markers(rows, "task")
        self.assertEqual(len(marks), 2)
        self.assertEqual(ra.budget_left(marks, TODAY, ra.DAILY_BUDGET), 0)
        causes = self._causes(1)
        take, _held = ra.select(causes, placed=set(), task_marks=marks, today=TODAY,
                                routes=self._routes(causes), owner_busy=False)
        self.assertEqual(take, [])

    def test_task_and_ask_markers_do_not_shadow_each_other(self):
        """Маркер заявки начинается с маркера задачи как подстроки — разбор обязан
        их различать, иначе один потолок молча съест другой."""
        rows = [{"task_text": "%s дата=%s ключ=cccccccccccc]" % (ra.ASK_MARK, TODAY)}]
        self.assertEqual(ra.markers(rows, "task"), [])
        self.assertEqual(ra.markers(rows, "ask"), [(TODAY, "cccccccccccc")])

    # ── потолок считается по СУТКАМ, а не по одновременности (класс 01.09) ──

    def _mark(self, day, key, mark=None):
        return {"task_text": "%s дата=%s ключ=%s]\nтело" % (mark or ra.TASK_MARK, day, key)}

    def test_two_closed_recons_of_today_leave_no_room_though_none_is_open(self):
        """ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЖИВОГО КЛАССА 01.09.2026. За ночь полоса поставила
        себе ПЯТЬ разведок при потолке 2 (#93 15:53, #94 16:25, #97 17:42,
        #98 18:13, #99 18:45 UTC): каждая закрывалась за 8–20 минут, маркер уходил
        из открытых рядов, и следующая видела ПУСТОЙ день. Здесь ровно та
        расстановка — два ЗАКРЫТЫХ ряда сегодняшним числом и НОЛЬ открытых."""
        closed = [self._mark(TODAY, "aaaaaaaaaaaa"), self._mark(TODAY, "bbbbbbbbbbbb")]
        marks = ra.markers(closed, "task")          # открытых рядов ноль
        self.assertEqual(len(marks), 2)
        self.assertEqual(ra.budget_left(marks, TODAY, ra.DAILY_BUDGET), 0)
        causes = self._causes(1)
        take, held = ra.select(causes, today=TODAY, routes=self._routes(causes),
                               task_marks=marks, owner_busy=False, limit=None)
        self.assertEqual(take, [], "закрывшаяся разведка снова освободила место")
        self.assertTrue(any("бюджет суток" in why for _c, why in held))

    def test_unread_closed_rows_exhaust_the_day_they_do_not_empty_it(self):
        """ТРЕТИЙ ИСХОД. Пустой список маркеров при УПАВШЕМ чтении неотличим от
        пустого при честном нуле — различает их отдельный признак, а не длина."""
        causes = self._causes(1)
        take, held = ra.select(causes, today=TODAY, routes=self._routes(causes),
                               task_marks=(), owner_busy=False, limit=None, marks_ok=False)
        self.assertEqual(take, [])
        self.assertTrue(any("НЕИЗВЕСТНО" in why for _c, why in held))
        self.assertTrue(any("закрытые ряды" in why for _c, why in held))
        self.assertEqual(ra.budget_left((), TODAY, ra.DAILY_BUDGET, False), 0)
        self.assertEqual(ra.budget_left((), TODAY, ra.DAILY_BUDGET, True), 2)

    def test_unread_closed_rows_stop_the_owner_claim_too(self):
        """Потолок ЗАЯВОК устроен так же и течёт тем же местом: заявка свободна от
        замка «работа владельца», но не от суточного счёта."""
        causes = self._causes(1)
        take, held = ra.select(causes, today=TODAY,
                               routes=self._routes(causes, ra.ROUTE_OWNER),
                               ask_marks=(), owner_busy=False, marks_ok=False)
        self.assertEqual(take, [])
        self.assertTrue(any("закрытые ряды" in why for _c, why in held))

    def test_closed_asks_of_today_eat_the_ask_budget(self):
        closed = [self._mark(TODAY, "cccccccccccc", ra.ASK_MARK),
                  self._mark(TODAY, "dddddddddddd", ra.ASK_MARK)]
        marks = ra.markers(closed, "ask")
        causes = self._causes(1)
        take, held = ra.select(causes, today=TODAY,
                               routes=self._routes(causes, ra.ROUTE_OWNER),
                               ask_marks=marks, owner_busy=False)
        self.assertEqual(take, [])
        self.assertTrue(any("заявок владельцу" in why for _c, why in held))

    def test_yesterdays_closed_rows_are_not_counted_at_all(self):
        """Положительный контроль рядом с отрицательным: правило, глушащее ВСЁ,
        прошло бы оба теста выше и было бы бесполезно. Вчерашние закрытые ряды
        сегодняшнего дня не съедают — иначе потолок стал бы вечным."""
        closed = [self._mark("2026-08-31", "eeeeeeeeeeee"),
                  self._mark("2026-08-31", "ffffffffffff")]
        marks = ra.markers(closed, "task")
        self.assertEqual(len(marks), 2)
        self.assertEqual(ra.budget_left(marks, TODAY, ra.DAILY_BUDGET), 2)
        causes = self._causes(2)
        take, _held = ra.select(causes, today=TODAY, routes=self._routes(causes),
                                task_marks=marks, owner_busy=False, limit=None)
        self.assertEqual(len(take), 2)

    def test_already_placed_cause_is_not_placed_twice(self):
        causes = self._causes(1)
        take, held = ra.select(causes, placed={causes[0]["key"]}, today=TODAY,
                               routes=self._routes(causes), owner_busy=False)
        self.assertEqual(take, [])
        self.assertIn("уже ставили", held[0][1])


# ───────────────────────────── источники ─────────────────────────────


class TestSources(unittest.TestCase):

    def test_expect_cause_takes_its_title_from_the_observer_not_from_a_literal(self):
        causes = ra.expect_causes({"o2t|1788270000": {"first": 1788270000.0,
                                                      "kind": "o2_pc_turn"}})
        self.assertEqual(len(causes), 1)
        self.assertEqual(causes[0]["title"], expectations_pc.NOTE_HEAD["o2_pc_turn"])

    def test_unknown_expectation_kind_raises_nothing(self):
        """Чужой/битый ключ разбирать нечем — молчим, а не выдумываем повод."""
        self.assertEqual(ra.expect_causes({"xx|1": {"kind": "выдуманное"}}), [])

    def test_claim_cause_is_keyed_by_the_queue_row_not_by_the_drifting_anchor(self):
        """ЖИВОЙ СЛУЧАЙ 01.09: ряд #88 стои́т под ключом `2d08a1342da8`, а
        пересборка того же лотка даёт заявке уже `27156c1e275d` — корпус вырос,
        якорь переехал. Возьми мы текущий якорь, и повод менял бы имя вместе с
        ним: дедуп промахивался бы, а разведка приезжала бы заново на каждый
        новый ответ в лотке."""
        kind = review_intake.kind_labels()[0]
        src = {"kind": kind, "quote": "не заявлять «слепых пятен не заявлено» и полное покрытие",
               "answer": LIVE_ANSWER, "send_date": TODAY, "channel": "codex"}
        row_key = review_intake.claim_key(src)          # ключ, под которым ряд ВСТАЛ
        claim = {"key": LIVE_DRIFTED_ANCHOR, "kind": kind, "sources": [src]}
        self.assertNotEqual(row_key, claim["key"], "якорь и ключ ряда совпали — проба пуста")
        built = [{"claim": claim, "premise": {"outcome": review_intake.PREMISE_ALIVE,
                                              "address": "review_auto.py:249"}}]
        causes = ra.claim_causes({row_key: {"id": 88, "day": TODAY}}, built)
        self.assertEqual(len(causes), 1)
        self.assertEqual(causes[0]["key"], ra.key_of(ra.SRC_CLAIM, row_key))
        self.assertIn(row_key, causes[0]["slug"])
        self.assertEqual(causes[0]["queue_id"], 88)

    def test_a_dead_premise_is_not_a_cause(self):
        claim = {"key": "z" * 12, "kind": review_intake.kind_labels()[0],
                 "keys": ["z" * 12], "sources": [{"answer": LIVE_ANSWER}]}
        for outcome in (review_intake.PREMISE_UNKNOWN, review_intake.PREMISE_STALE):
            built = [{"claim": claim, "premise": {"outcome": outcome, "address": "a.py:1"}}]
            self.assertEqual(ra.claim_causes({"z" * 12: {"id": 1, "day": TODAY}}, built), [])

    def test_a_finding_without_a_queue_row_is_not_a_cause(self):
        """«Заявка» — это РЯД в очереди. Находка, до ряда не дошедшая, поводом не
        является: иначе ступень E разведывала бы то, о чём ступень B ещё молчит."""
        claim = {"key": "y" * 12, "kind": review_intake.kind_labels()[0],
                 "keys": ["y" * 12], "sources": []}
        built = [{"claim": claim, "premise": {"outcome": review_intake.PREMISE_ALIVE,
                                              "address": "a.py:1"}}]
        self.assertEqual(ra.claim_causes({}, built), [])

    def test_one_red_is_not_a_cause_two_is(self):
        """Единица — не повод. Один отказ бывает от чего угодно; повторившийся —
        уже свойство. Вся разница между шумом и предметом разведки."""
        one = [{"class": "отказ канала manus", "reason": "http_400", "count": 1, "where": ["a"]}]
        two = [{"class": "отказ канала manus", "reason": "http_400", "count": 2,
                "where": ["a", "b"]}]
        self.assertEqual(ra.repeat_causes(one), [])
        self.assertEqual(len(ra.repeat_causes(two)), 1)

    def test_analysis_already_on_disk_closes_the_cause(self):
        cause = _cause(slug="otkaz-manus-http-400")
        names = ["2026-08-30-разведка-otkaz-manus-http-400.md", "прочее.md"]
        self.assertEqual(ra.analysed([cause], names), {cause["key"]})
        self.assertEqual(ra.analysed([cause], ["прочее.md"]), set())

    def test_order_puts_the_event_before_the_standing_signal(self):
        causes = [_cause(key="r" * 12), _expect_cause("o2_pc_turn")]
        self.assertEqual([c["src"] for c in ra.order(causes)],
                         [ra.SRC_EXPECT, ra.SRC_REPEAT])


# ───────────────────────────── живой корпус ─────────────────────────────


class TestLiveCorpus(unittest.TestCase):
    """Голдены сняты с ЖИВЫХ файлов лотка. Счёт сверяется «не меньше», а не
    равенством СОЗНАТЕЛЬНО: ступень A досыпает в лоток каждый свой оборот, и
    точное число покраснело бы само по себе в ближайший час — то есть тест
    ловил бы рост корпуса, а не поломку кода."""

    def test_refusal_reasons_are_counted_from_the_live_tray(self):
        signals = run.repeat_signals(HERE)
        by = {(s["class"], s["reason"]): s["count"] for s in signals}
        self.assertIn(("отказ канала manus", "http_400"), by)
        self.assertGreaterEqual(by[("отказ канала manus", "http_400")], 10)

    def test_the_refusal_field_is_structural_not_read_from_prose(self):
        with io.open(os.path.join(HERE, *LIVE_REFUSED.split("/")), encoding="utf-8") as fh:
            header = review_intake.parse_answer(fh.read())
        self.assertEqual(header["outcome"], "refused")
        self.assertEqual(header["reason"], "http_400")

    def test_live_causes_carry_a_result_address_every_one(self):
        signals = run.repeat_signals(HERE)
        causes = ra.repeat_causes(signals)
        self.assertTrue(causes, "живой корпус перестал давать поводы повтора")
        for cause in causes:
            address, words = ra.result_address(cause, TODAY)
            self.assertTrue(address.startswith("docs/artifacts/"))
            self.assertTrue(words)
            self.assertTrue(ra.task_text(cause, TODAY))


# ───────────────────────────── руки ─────────────────────────────


class TestHands(unittest.TestCase):

    def _root(self):
        root = tempfile.mkdtemp(prefix="recon_e_")
        os.makedirs(os.path.join(root, "docs", "artifacts"))
        os.makedirs(os.path.join(root, "docs", "review_inbox"))
        os.makedirs(os.path.join(root, "tmp", "expect_pc"))
        return root

    def _observer(self, root, eps):
        with io.open(os.path.join(root, "tmp", "expect_pc", "state.json"), "w",
                     encoding="utf-8") as fh:
            json.dump({"open": eps}, fh)

    def test_dry_run_touches_nothing(self):
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue()
        report = run.tick(root, place=False, queue=q, prober=lambda t, r, l=None: (False, [], True))
        self.assertEqual(q.tasks, [])
        self.assertEqual(q.asks, [])
        self.assertFalse(os.path.exists(os.path.join(root, run.DEFAULT_STATE)))
        self.assertTrue(any("сухой ход" in why for _k, why in report["held"]))

    def test_live_run_places_a_task_and_remembers_it(self):
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue()
        report = run.tick(root, place=True, queue=q, journal_fn=lambda *a, **k: (0, ""),
                          prober=lambda t, r, l=None: (False, [], True))
        self.assertEqual(len(q.tasks), 1)
        self.assertTrue(q.tasks[0][1].startswith(ra.TASK_MARK))
        self.assertEqual(len(report["placed"]), 1)
        state = run.read_state(os.path.join(root, run.DEFAULT_STATE))
        self.assertEqual(len(state["placed"]), 1)

    def test_second_turn_in_a_row_stays_silent_by_the_registry(self):
        """Проба гоняет ДВА оборота, а не один: ровно та дыра, которой ступень D
        пропустила декоративный бутстрап (§11.6) — дефект жил со второго тика."""
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue()
        kw = dict(place=True, queue=q, journal_fn=lambda *a, **k: (0, ""),
                  prober=lambda t, r, l=None: (False, [], True))
        run.tick(root, **kw)
        run.tick(root, **kw)
        self.assertEqual(len(q.tasks), 1, "второй оборот поставил дубль")

    def test_an_unreadable_queue_places_nothing(self):
        """Третий исход: мост молчит → замок владельца и потолок суток не сверить
        → не ставим НИЧЕГО. Слепая постановка дороже отсрочки."""
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue(ok=False)
        report = run.tick(root, place=True, queue=q,
                          prober=lambda t, r, l=None: (False, [], True))
        self.assertEqual(q.tasks, [])
        self.assertIn("очередь недоступна", report["why"])

    def test_a_missing_observer_is_unknown_not_all_clear(self):
        root = self._root()
        os.remove(os.path.join(root, "tmp", "expect_pc")) if False else None
        eps, why = run.expect_open(root)
        self.assertEqual(eps, {})
        self.assertTrue(why, "молчание наблюдателя обязано быть НАЗВАНО")

    def test_owner_busy_is_judged_by_the_daemons_own_function(self):
        """Свой список исключений разошёлся бы с демоном на первом новом виде ряда
        — и разошёлся бы молча. Спрашиваем ЕГО."""
        body = _src("recon_auto_run.py")
        self.assertIn("self._d._is_owner_work", body)

    def test_the_registry_never_lands_in_the_live_tree_under_test(self):
        """Класс `_state`, пойманный ступенью A живьём: ветка включена по
        умолчанию, и тест, зовущий живую функцию, писал бы в БОЕВОЙ реестр."""
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        before = os.path.exists(os.path.join(HERE, run.DEFAULT_STATE))
        run.tick(root, place=True, queue=FakeQueue(), journal_fn=lambda *a, **k: (0, ""),
                 prober=lambda t, r, l=None: (False, [], True))
        self.assertEqual(os.path.exists(os.path.join(HERE, run.DEFAULT_STATE)), before)

    def test_the_owner_route_is_placed_as_an_ask_not_as_a_task(self):
        root = self._root()
        self._observer(root, {"o5s|1": {"first": 1.0, "kind": "o5_pc_client_sent"}})
        q = FakeQueue()
        run.tick(root, place=True, queue=q, journal_fn=lambda *a, **k: (0, ""),
                 prober=lambda t, r, l=None: (False, [], True))
        self.assertEqual(q.tasks, [], "повод клиентского контура стал ЗАДАЧЕЙ")
        self.assertEqual(len(q.asks), 1)
        self.assertTrue(q.asks[0][1].startswith(ra.ASK_MARK))

    # ── тот же класс на ЖИВОМ пути рук (поддельные ряды, боевой очереди нет) ──

    def _clock(self, hour=18):
        """Часы теста: 01.09.2026 18:45 UTC — минута пятой ночной постановки."""
        import datetime

        return lambda tz: datetime.datetime(2026, 9, 1, hour, 45, tzinfo=tz)

    def _closed_recon(self, key, day=TODAY):
        return {"id": 90, "status": "done", "result": "готово",
                "task_text": "%s дата=%s ключ=%s]\nтело" % (ra.TASK_MARK, day, key)}

    def _kw(self, q):
        return dict(place=True, queue=q, clock=self._clock(),
                    journal_fn=lambda *a, **k: (0, ""),
                    prober=lambda t, r, l=None: (False, [], True))

    def test_the_counted_corpus_names_done_or_the_whole_fix_is_a_no_op(self):
        """Разведка закрывается в `done` (лог 01.09: «V0-DONE id=93 … COMPLETE
        id=93 status=done»), а не в `failed`. Не спроси мы `done` — счёт «по
        закрытым рядам» не увидел бы НИ ОДНОЙ из пяти ночных постановок, и правка
        была бы зелёной пустышкой."""
        self.assertIn("done", run.BUDGET_STATUSES)
        self.assertIn("failed", run.CLOSED_STATUSES)
        self.assertNotIn("done", run.OPEN_STATUSES)

    def test_closed_recons_of_today_block_the_next_one_on_the_live_path(self):
        """Руки обязаны СПРОСИТЬ закрытые ряды и посчитать по ним. Ряды подделаны:
        боевой очереди тест не касается ни одной веткой."""
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue(done=[self._closed_recon("aaaaaaaaaaaa"),
                            self._closed_recon("bbbbbbbbbbbb")])
        report = run.tick(root, **self._kw(q))
        self.assertEqual(q.tasks, [], "закрытые разведки не съели бюджет дня")
        self.assertTrue(any("бюджет суток" in why for _k, why in report["held"]))
        self.assertIn(run.BUDGET_STATUSES, q.asked, "корпус `done` не спрашивали вовсе")

    def test_unread_closed_rows_place_nothing_and_the_reason_reaches_the_log(self):
        """Третий исход на живом пути: `done` не отдался → ставить нельзя, и
        причина едет СЛОВАМИ в ту самую строку, которую демон пишет в лог."""
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue(done_ok=False)
        report = run.tick(root, **self._kw(q))
        self.assertEqual(q.tasks, [])
        self.assertEqual(q.asks, [])
        self.assertIn("закрытые ряды", report["why"])
        self.assertIn("НЕИЗВЕСТНО", report["why"])
        self.assertFalse(report["marks_ok"])

    def test_yesterdays_closed_recons_do_not_block_today_on_the_live_path(self):
        """Положительный контроль: правило, глушащее всё, прошло бы оба теста выше."""
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue(done=[self._closed_recon("aaaaaaaaaaaa", "2026-08-31"),
                            self._closed_recon("bbbbbbbbbbbb", "2026-08-31")])
        run.tick(root, **self._kw(q))
        self.assertEqual(len(q.tasks), 1, "вчерашние закрытые съели сегодняшний день")

    def test_the_expensive_corpus_is_not_read_when_there_is_nothing_to_place(self):
        """`get_pending("done")` — 120 строк за 26.8с. Оборот «поводов нет» (5 из 5
        последних тиков живого лога) платить их не обязан."""
        root = self._root()
        self._observer(root, {})                      # ни одного открытого эпизода
        q = FakeQueue()
        report = run.tick(root, **self._kw(q))
        self.assertEqual(report["causes"], 0)
        self.assertNotIn(run.BUDGET_STATUSES, q.asked)
        self.assertIn("поводов нет", report["why"])

    def test_owner_busy_live_path_places_nothing_and_says_why(self):
        root = self._root()
        self._observer(root, {"o2t|1": {"first": 1.0, "kind": "o2_pc_turn"}})
        q = FakeQueue(busy=True, busy_ids=[92])
        report = run.tick(root, place=True, queue=q,
                          prober=lambda t, r, l=None: (False, [], True))
        self.assertEqual(q.tasks, [])
        self.assertIn("#92", report["why"])


# ───────────────────────────── врезка ─────────────────────────────


class TestWiring(unittest.TestCase):
    """Врезка в демона: заявка не исполняется НИ ОДНОЙ веткой."""

    def setUp(self):
        import pc_orchestrator

        self.o = pc_orchestrator

    def test_the_ask_marker_is_recognised_and_the_task_marker_is_not(self):
        self.assertTrue(self.o._is_recon_ask(ra.ask_text(_expect_cause("o5_pc_client_sent"),
                                                         TODAY, "тест")))
        self.assertFalse(self.o._is_recon_ask(ra.task_text(_cause(), TODAY)))

    def test_the_ask_is_not_owner_work(self):
        """Числись заявка работой — одна заявка, повисшая в needs_approval, глушила
        бы разведку НАВСЕГДА: ступень E выключала бы себя первым своим вопросом."""
        row = {"from": self.o.RECON_ASK_FROM, "task_text": "%s дата=x ключ=y]" % ra.ASK_MARK}
        self.assertFalse(self.o._is_owner_work(row, set()))

    def test_but_the_autotask_IS_owner_work(self):
        """И это не упущение: автозадача — обычная работа полосы, и пока она
        открыта, второй такой не будет. Потолок «две в сутки» подкреплён замком
        «одна в работе» бесплатно."""
        row = {"from": "Filipp-recon", "task_text": ra.task_text(_cause(), TODAY)}
        self.assertTrue(self.o._is_owner_work(row, set()))

    def test_the_tick_is_wired_exactly_once(self):
        body = _src("pc_orchestrator.py")
        self.assertEqual(body.count("maybe_recon_auto()"), 1)

    def test_the_stage_is_a_named_lazy_dependency(self):
        for mod in ("recon_auto.py", "recon_auto_run.py", "expectations_pc.py"):
            self.assertIn(mod, self.o._ORCH_LAZY_UNCOVERED)

    def test_the_switches_exist_and_default_to_on(self):
        self.assertEqual(self.o.RECON_AUTO_BUDGET, ra.DAILY_BUDGET)
        self.assertTrue(self.o.RECON_AUTO_MIN_SEC >= 600)


if __name__ == "__main__":
    unittest.main()
