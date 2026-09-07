# -*- coding: utf-8 -*-
"""
test_client_contour.py — ГОЛДЕНЫ ВОРОТ КЛИЕНТСКОГО КОНТУРА (30.07.2026).
БЕЗ реального git/рестарта/сети/пушей — всё инъектируется или мокается. Разрушительного нет.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_client_contour -v

Что стережём:
  • признак: клиентский файл ← транзитивное import-замыкание userbot_listen/moderation_bot;
  • ворота ВЫХОДА: клиентский файл в диффе → карточка и НИ ОДНОГО рестарта (все три точки
    авто-применения: дев-задача, self-update, реконсиляция на новый коммит);
  • ВНУТРЕННИЙ файл → авто-применение как раньше (эта половина не задета);
  • FAIL-CLOSED: признак не смог решить → держим;
  • основания пропуска: «да» владельца / зелёный тренажёр;
  • ворота ВХОДА: находка ревизора в клиентском → owner-карточка, во внутреннем → задача дирижёру;
  • ЖИВЫЕ ЦЕПИ 4 и 44: их файлы обязаны попадать под ворота.
"""

import os
import json
import shutil
import tempfile
import unittest
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (как в test_pc_orchestrator)

import client_contour as cc            # noqa: E402
import pc_orchestrator as o            # noqa: E402

REPO = os.path.dirname(os.path.abspath(__file__))

# КОММИТ ДЛЯ ГОЛДЕНОВ ВОРОТ БЕРЁТСЯ ЖИВОЙ (05.09.2026, задание 00-no-test-cards.0905).
# До этого дня здесь стоял литерал `new777`, и он же четырежды уехал владельцу в ночном потоке
# 05:14:37–05:14:56: карточка ворот просила решения про коммит, которого в git нет, а команда
# отката в ней (`git revert --no-edit new777`) была заведомо неисполнима. С 05.09 ворота такую
# карточку НЕ СОБИРАЮТ вовсе (`pc_orchestrator._gate_commit_known`) — несуществующий коммит это
# дефект вызова, а не повод спросить владельца. Предмет этих тестов — «клиентский файл в
# диапазоне удерживает детей», а не «на что похож хеш»: живой HEAD проверяет ровно предмет и
# перестаёт зависеть от выдуманной метки. Фолбэк на литерал оставлен для дерева без git —
# набор не обязан краснеть там, где git недоступен (тогда карточку не соберут, и голден это
# честно покажет).
LIVE_COMMIT = (o._git_out(["rev-parse", "HEAD"]) or "new777")


def mkrepo(files):
    """Мини-репозиторий на диске под граф импортов. → путь (чистится в tearDown)."""
    d = tempfile.mkdtemp(prefix="cc_repo_")
    for name, src in files.items():
        p = os.path.join(d, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
    return d


# ─────────────────────────── 1. ПРИЗНАК (граф импортов) ───────────────────────────

class TestZakrytie(unittest.TestCase):
    """Замыкание считается по ФАКТУ с диска, а не по списку имён."""

    def setUp(self):
        self.dirs = []

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def repo(self, files):
        d = mkrepo(files)
        self.dirs.append(d)
        return d

    def test_tranzitivnyi_import_popadaet_v_kontur(self):
        """b импортится из a, a — из входной точки: в контуре ОБА (транзитивность, не один уровень)."""
        d = self.repo({"userbot_listen.py": "import a\n", "a.py": "import b\n", "b.py": "x = 1\n",
                       "moderation_bot.py": "y = 1\n", "chuzhoy.py": "z = 1\n"})
        c = cc.closure(d)
        self.assertTrue(c.ok, c.reason)
        self.assertEqual(c.files, frozenset({"userbot_listen.py", "moderation_bot.py", "a.py", "b.py"}))
        self.assertTrue(cc.is_client("b.py", d))
        self.assertFalse(cc.is_client("chuzhoy.py", d))

    def test_lenivyi_import_vnutri_funkcii_tozhe_schitaetsya(self):
        """userbot_listen тянет moderation_ipc ИМЕННО так (внутри функции) — ast.walk обязан видеть."""
        d = self.repo({"userbot_listen.py": "def f():\n    import lazy_mod\n    return lazy_mod\n",
                       "moderation_bot.py": "y = 1\n", "lazy_mod.py": "x = 1\n"})
        self.assertTrue(cc.is_client("lazy_mod.py", d))

    def test_novyi_modul_popadaet_v_kontur_bez_pravki_vorot(self):
        """ГЛАВНОЕ свойство признака: новый файл становится клиентским сам, как только его импортят."""
        d = self.repo({"userbot_listen.py": "x = 1\n", "moderation_bot.py": "y = 1\n"})
        self.assertFalse(cc.is_client("novyi.py", d))          # ещё никем не импортится
        with open(os.path.join(d, "novyi.py"), "w", encoding="utf-8") as f:
            f.write("z = 1\n")
        with open(os.path.join(d, "userbot_listen.py"), "w", encoding="utf-8") as f:
            f.write("import novyi\n")
        self.assertTrue(cc.is_client("novyi.py", d))           # кэш обязан переинвалидироваться по mtime/размеру

    def test_srez_na_chuzhom_processe(self):
        """Обход не идёт сквозь входную точку чужого процесса: демон и то, что под ним, — внутреннее."""
        d = self.repo({"userbot_listen.py": "def f():\n    import pc_orchestrator\n",
                       "moderation_bot.py": "y = 1\n",
                       "pc_orchestrator.py": "import tolko_demona\n", "tolko_demona.py": "x = 1\n"})
        c = cc.closure(d)
        self.assertNotIn("pc_orchestrator.py", c.files)
        self.assertNotIn("tolko_demona.py", c.files)

    def test_dannye_po_literalam(self):
        """Не-.py файл, названный литералом в модуле контура, — тоже контур (меняет клиента без рестарта)."""
        d = self.repo({"userbot_listen.py": "P = 'rules.json'\n", "moderation_bot.py": "y = 1\n",
                       "rules.json": "{}\n", "chuzhie.json": "{}\n"})
        self.assertTrue(cc.is_client("rules.json", d))
        self.assertFalse(cc.is_client("chuzhie.json", d))

    def test_fail_closed_net_vhodnoi_tochki(self):
        d = self.repo({"moderation_bot.py": "y = 1\n"})
        c = cc.closure(d)
        self.assertFalse(c.ok)
        self.assertTrue(cc.is_client("chto_ugodno.py", d))     # не знаем → клиентский

    def test_fail_closed_bityi_sintaksis_v_konture(self):
        d = self.repo({"userbot_listen.py": "import bityi\n", "moderation_bot.py": "y = 1\n",
                       "bityi.py": "def (((\n"})
        c = cc.closure(d)
        self.assertFalse(c.ok)
        self.assertTrue(cc.is_client("readme.md", d))

    def test_fail_closed_pustoi_put(self):
        d = self.repo({"userbot_listen.py": "x = 1\n", "moderation_bot.py": "y = 1\n"})
        self.assertTrue(cc.is_client("", d))
        self.assertTrue(cc.is_client(None, d))


class TestZhivoyKontur(unittest.TestCase):
    """Признак на РЕАЛЬНОМ репозитории — голдены по живому графу, а не по синтетике."""

    def test_zhivye_klientskie_fayly(self):
        c = cc.closure(REPO)
        self.assertTrue(c.ok, c.reason)
        for name in ("userbot_listen.py", "moderation_bot.py", "suggest.py", "pricing.py",
                     "delivery.py", "booking_draft.py", "trainer.py", "trainer_log.py",
                     "moderation_core.py", "moderation_ipc.py", "log_setup.py", "lesson_router.py"):
            self.assertTrue(cc.is_client(name, REPO), f"{name} обязан быть клиентским")

    def test_zhivye_vnutrennie_fayly(self):
        # ДВА имени убраны 05.09.2026, и оба — не ради зелёного, а потому что перестали быть
        # правдой ПО ФАКТУ (цепи названы голденом ниже):
        #   • client_contour.py — с a42c8ee (задача 231, регрессия урока);
        #   • brain_writer.py — РАНЬШЕ сегодняшнего происшествия: он был клиентским уже на
        #     d92fd0f (замер 05.09), то есть этот тест лежал красным и МОЛЧА — он не входит в
        #     набор гейта самообновления (там только test_pc_orchestrator + test_pc_local_dec),
        #     поэтому красноту никто не видел.
        # Признак — самообновляющийся граф; поимённый список «внутренних» устаревает без единого
        # сигнала, и это его врождённое свойство, а не поломка.
        # ТРЕТЬЕ имя убрано 06.09.2026 — task_metrics.py, и снова не ради зелёного: 1928e46 завёл
        # season_gate.py, и цепь userbot_listen → suggest → season_gate → dispatch_notify →
        # task_metrics стала живым ребром (замер: замыкание 27f3b9c 37 → вершина 40 файлов, вошли
        # season_gate.py, dispatch_notify.py, task_metrics.py). Цепь названа голденом ниже.
        # Этот файл в набор гейта самообновления НЕ входит — значит и сегодня он лежал красным
        # молча, ровно как 05.09 с brain_writer.py. Класс живой, и это его вторая встреча.
        for name in ("pc_orchestrator.py", "pc_agent.py", "gate_selective.py",
                     "selfupdate_gate.py", "pretool_guard.py",
                     "cowork_log_append.py", "README.md"):
            self.assertFalse(cc.is_client(name, REPO), f"{name} обязан быть внутренним")

    def test_client_contour_sam_stal_klientskim_05_09(self):
        """ЗАПИСЬ СМЕНЫ, а не подгонка: модуль самих ворот въехал в клиентское замыкание, и цепь
        названа поимённо. Замыкание выросло 30 → 33 файла (вошли client_contour.py,
        lesson_regress.py, trainer_run.py) — замер 05.09.2026 на d92fd0f против вершины.
        ПОПРАВКА 07.09.2026: из этих трёх `trainer_run.py` в замыкании БОЛЬШЕ НЕТ — ребро
        `lesson_regress → trainer_run` объявлено границей процесса (`PROCESS_BOUNDARY_EDGES`,
        класс `TestGranicaProcessa` ниже). Два других звена цепи живы и проверяются здесь.

        Направление ошибки безопасное (лишний файл держим и спрашиваем владельца, клиентского не
        пропускаем), поэтому чинится ГОЛДЕН, а не признак. Красный здесь = ребро исчезло, и это
        новость, о которой надо знать: значит регрессия урока отвязалась от тренажёра."""
        c = cc.closure(REPO)
        self.assertTrue(c.ok, c.reason)
        for zveno in ("trainer.py", "lesson_regress.py", "client_contour.py"):
            self.assertIn(zveno, c.files, f"{zveno} выпал из клиентского замыкания — цепь порвана")
        self.assertTrue(cc.is_client("client_contour.py", REPO))
        # Вторая цепь, старее первой: userbot_listen → suggest → price_gate → queue_snapshot_pc →
        # brain_writer. Писатель журнала клиентский не «по смыслу», а потому что ворота цены его
        # тянут; список имён этого не знал, граф знал всегда.
        for zveno in ("suggest.py", "price_gate.py", "queue_snapshot_pc.py", "brain_writer.py"):
            self.assertIn(zveno, c.files, f"{zveno} выпал из клиентского замыкания — цепь порвана")
        self.assertTrue(cc.is_client("brain_writer.py", REPO))

    def test_tretii_ishod_privel_dispatch_i_metriki_06_09(self):
        """ЗАПИСЬ СМЕНЫ 06.09.2026: третий исход на границе сезонов (1928e46) сделал клиентскими
        ТРИ файла — season_gate.py, dispatch_notify.py, task_metrics.py. Замыкание 37 → 40
        (замер одним признаком: дерево 27f3b9c против вершины), не вышел ни один.

        Цепь: userbot_listen.py → suggest.py → season_gate.py → dispatch_notify.py →
        task_metrics.py. Два последних ребра ЛЕНИВЫЕ (season_gate._default_sender,
        dispatch_notify._session_metrics_line) — признак считает импорты внутри функций
        сознательно, и здесь это ровно то, что нужно: карточку владельцу третий исход шлёт из
        живого процесса бота.

        Красный здесь = третий исход перестал звать человека. Это правильный красный."""
        c = cc.closure(REPO)
        self.assertTrue(c.ok, c.reason)
        for zveno in ("suggest.py", "season_gate.py", "dispatch_notify.py", "task_metrics.py"):
            self.assertIn(zveno, c.files, f"{zveno} выпал из клиентского замыкания — цепь порвана")
        self.assertTrue(cc.is_client("task_metrics.py", REPO))

    def test_vtoroy_tretii_ishod_ne_privel_ni_odnogo_lishnego_rebra_07_09(self):
        """ЗАПИСЬ СМЕНЫ 07.09.2026: второй третий исход — «модель без цены» — ввёл в клиентское
        замыкание РОВНО ОДИН файл, `noprice_gate.py`. Замер: 40 → 41, не вышел ни один.

        Цепь: userbot_listen.py → suggest.py → noprice_gate.py → dispatch_notify.py →
        task_metrics.py. Второе и третье рёбра ЛЕНИВЫЕ (noprice_gate._default_sender,
        dispatch_notify._session_metrics_line) — то же устройство, что у соседа по классу.

        ПОИМЁННО, ЗАЧЕМ КАЖДОЕ РЕБРО, и почему побочных здесь нет:
          • suggest → noprice_gate — путь ответа обязан УЗНАТЬ имя исхода, иначе третьего исхода
            не существует;
          • noprice_gate → price_source — только за КОДАМИ причин (`MODEL_HAS_NO_PRICE`); файл
            модуль не читает и чисел не считает. Ребро не новое: `price_source` уже был
            клиентским через `suggest` и `season_gate`;
          • noprice_gate → dispatch_notify — этим карточка доезжает до владельца. Красный здесь =
            третий исход перестал звать человека, и это правильный красный.
        Обратных рёбер модуль не завёл ни одного: `import suggest` в нём запрещён замком
        test_noprice_gate.TestBoundaries.test_module_does_not_import_suggest."""
        c = cc.closure(REPO)
        self.assertTrue(c.ok, c.reason)
        for zveno in ("suggest.py", "noprice_gate.py", "dispatch_notify.py", "task_metrics.py"):
            self.assertIn(zveno, c.files, f"{zveno} выпал из клиентского замыкания — цепь порвана")
        self.assertTrue(cc.is_client("noprice_gate.py", REPO))
        # Ворота ВЫХОДА обязаны накрывать новый файл ровно как соседа по классу.
        self.assertEqual(o._client_paths(["noprice_gate.py", "season_gate.py"]),
                         ["noprice_gate.py", "season_gate.py"])

    def test_lesson_router_est_v_grafe_no_net_v_karte_processov(self):
        """Живой разрыв, который список имён не видит, а граф видит: trainer.py импортит
        lesson_router.py (значит правка доедет до обоих ботов), а _FILE_PROCESS_RULES про него
        не знает — рестарта по нему карта не назначает. Ворота накрывают, потому что признак ∪ карта."""
        self.assertTrue(cc.is_client("lesson_router.py", REPO))
        self.assertEqual(o._procs_for_file("lesson_router.py"), set())
        self.assertEqual(o._client_paths(["lesson_router.py"]), ["lesson_router.py"])

    def test_put_docs_i_testy_vnutrennie(self):
        self.assertFalse(cc.is_client("docs/ENV_PLAYBOOK.md", REPO))
        self.assertFalse(cc.is_client("test_suggest.py", REPO))


class TestGranicaProcessa(unittest.TestCase):
    """ГРАНИЦА ПРОЦЕССА НА ОТДЕЛЬНОМ РЕБРЕ (`PROCESS_BOUNDARY_EDGES`, 07.09.2026).

    Направление ошибки здесь, в отличие от всего остального в этом файле, НЕ безопасное: граница
    СУЖАЕТ охрану. Поэтому её меряют с обеих сторон — сколько вышло (обязано быть ровно одно имя)
    и что обязано остаться (отрицательные тесты ниже)."""

    def _bez_granicy(self, cut):
        """То же замыкание, посчитанное БЕЗ границы — база для «что именно вышло»."""
        save = cc._BOUNDARY
        cc._BOUNDARY = frozenset()
        try:
            cc._cache.clear()
            cl = cc.closure(REPO, entries=cc.CLIENT_ENTRIES, cut=cut)
        finally:
            cc._BOUNDARY = save
            cc._cache.clear()
        return cl

    def test_vyshel_rovno_odin_i_eto_raner(self):
        """ЧИСЛОМ ДО И ПОСЛЕ, обоими замыканиями: живыми воротами (`cut=()`, 78 → 77) и умолчанием
        признака (41 → 40). Вышло РОВНО одно имя — `trainer_run.py`, и ни одного имени данных."""
        for cut, (was_f, now_f) in ((), (78, 77)), (None, (41, 40)):
            with self.subTest(cut=cut):
                base = self._bez_granicy(cut)
                cl = cc.closure(REPO, entries=cc.CLIENT_ENTRIES, cut=cut)
                self.assertTrue(base.ok and cl.ok, cl.reason)
                self.assertEqual((len(base.files), len(cl.files)), (was_f, now_f))
                self.assertEqual(set(base.files) - set(cl.files), {"trainer_run.py"},
                                 "из охраны вышло НЕ ровно то, что объявлено границей")
                self.assertEqual(set(base.data) - set(cl.data), set(),
                                 "граница увела за собой ДАННЫЕ — этого она делать не вправе")
                self.assertEqual(set(cl.files) - set(base.files), set())

    def test_ostalis_te_kto_obyazan_ostatsya(self):
        """ОТРИЦАТЕЛЬНЫЙ: файлы, обязанные остаться в замыкании, в нём и остались — ПРОГОНОМ.

        Первым идёт сам импортёр (`lesson_regress.py`): граница снимает РЕБРО, а не файл, и путать
        её со срезом нельзя — бот зовёт из него `spawn()` по-настоящему. Дальше — вторая половина
        того же файла (`client_contour.py` приезжает из него же вторым ребром) и корпус, который
        на воротах и висит."""
        cl = cc.closure(REPO)
        gate = cc.closure(REPO, entries=cc.CLIENT_ENTRIES, cut=())
        self.assertTrue(cl.ok and gate.ok, cl.reason)
        for name in ("lesson_regress.py", "trainer.py", "client_contour.py", "suggest.py",
                     "userbot_listen.py", "moderation_bot.py", "lesson_router.py"):
            self.assertIn(name, cl.files, f"{name} выпал из замыкания — граница съела лишнее")
            self.assertIn(name, gate.files, f"{name} выпал из замыкания ВОРОТ")
        self.assertTrue(cc.is_client("trainer_cases.json", REPO))
        self.assertEqual(o._client_paths(["lesson_regress.py"]), ["lesson_regress.py"])

    def test_granica_derzhit_rovno_svoyo_rebro(self):
        """ОТРИЦАТЕЛЬНЫЙ на синтетическом дереве: граница снимает ОДНО ребро, а не модуль.

        Тот же `цель.py`, импортированный ЛЮБЫМ другим файлом замыкания, въезжает как раньше —
        иначе объявление одного ребра тихо прятало бы модуль от всех ворот сразу."""
        d = mkrepo({"userbot_listen.py": "import мост\n", "moderation_bot.py": "x = 1\n",
                    "мост.py": "def f():\n    import цель\n", "цель.py": "Y = 1\n",
                    "второй.py": "import цель\n"})
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        save = cc._BOUNDARY
        try:
            cc._BOUNDARY = frozenset({("мост.py", "цель")})
            cc._cache.clear()
            self.assertNotIn("цель.py", cc.closure(d).files)      # ребро снято
            with open(os.path.join(d, "мост.py"), "a", encoding="utf-8") as f:
                f.write("import второй\n")                        # обход теперь идёт вторым путём
            cc._cache.clear()
            self.assertIn("цель.py", cc.closure(d).files,
                          "граница спрятала модуль от ДРУГОГО импортёра — это уже не ребро, а срез")
        finally:
            cc._BOUNDARY = save
            cc._cache.clear()

    def test_kortezh_granicy_nazvan_poimenno(self):
        """Кортеж границ читается глазами и обязан оставаться коротким: каждое ребро — сужение
        охраны, и новое заводится замером, а не по аналогии."""
        self.assertEqual(cc.PROCESS_BOUNDARY_EDGES, (("lesson_regress.py", "trainer_run"),))
        self.assertEqual(cc._BOUNDARY, frozenset(cc.PROCESS_BOUNDARY_EDGES))


# ─────────────────────────── 2. ОСНОВАНИЯ ПРОПУСКА ───────────────────────────

class TestOsnovaniya(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cc_rel_")
        self.rel = os.path.join(self.d, "release.json")
        self.tr = os.path.join(self.d, "trainer.json")
        # ИЗОЛЯЦИЯ РУЧКИ ЗАМОРОЗКИ — с 05.09.2026 она решает не только адрес отказа, но и САМО
        # основание пропуска (`release_reason` → `freeze_holds_release`). Без увода пути голдены
        # этого класса читали бы БОЕВОЙ флаг: он лежит с 21.08, и «зелёный вердикт открывает
        # ворота» краснел бы, не изменившись ни строкой. Ровно та же течь, что закрыта в GateBase.
        self.flag = os.path.join(self.d, "pc_orchestrator.contour_frozen")
        _s = cc.FREEZE_FLAG
        cc.FREEZE_FLAG = self.flag
        self.addCleanup(lambda: setattr(cc, "FREEZE_FLAG", _s))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def freeze(self):
        with open(self.flag, "w", encoding="utf-8") as f:
            f.write("контур заморожен (тест)\n")

    def test_bez_osnovanii_derzhim(self):
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))

    def test_da_vladelca(self):
        ok, _ = cc.approve("abc1234def", path=self.rel)
        self.assertTrue(ok)
        self.assertEqual(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}), "owner")

    def test_da_normalizuet_dlinu_hesha(self):
        """Одобрили короткий — применяем полный (и наоборот): 7/9/40 символов это ОДИН коммит."""
        cc.approve("4528917", path=self.rel)
        self.assertEqual(cc.release_reason("452891745abcdef", path=self.rel, trainer_path=self.tr, env={}), "owner")

    def _verdict(self, **kw):
        """Заведомо ЗЕЛЁНАЯ запись вердикта тренажёра на коммит abc1234; kw портит одно поле."""
        rec = {"commit": "abc1234", "result": "green", "checks_passed": 96, "checks_total": 96,
               "cases": 12, "cases_total": 12, "runs": 2, "clean": True,
               "corpus": "trainer_cases.json", "corpus_sha": cc.corpus_sha(),
               "runner": cc.TRAINER_RUNNER, "ts": 1.0, "when": "2026-07-30 12:00:00"}
        rec.update(kw)
        with open(self.tr, "w", encoding="utf-8") as f:
            json.dump({"green": {"abc1234": rec}}, f)

    def test_zapis_bez_verdikta_ne_osnovanie(self):
        """Запись есть, а доказательств нет (старая заглушка `{"ok": true}`) — ворота держат."""
        with open(self.tr, "w", encoding="utf-8") as f:
            json.dump({"green": {"abc1234": {"ok": True}}}, f)
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))

    def test_trener_zelenyi_verdikt_otkryvaet_vorota(self):
        """ВТОРОЕ основание живое: 12/12 кейсов, все чеки, 2 прогона, чистое дерево, тот же коммит."""
        self._verdict()
        self.assertEqual(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr,
                                           env={}), "trainer")

    def test_trener_progona_net(self):
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))

    def test_trener_rubilnik_gasit_osnovanie(self):
        """PC_TRAINER_GREEN=0 — аварийный возврат к «только да владельца»."""
        self._verdict()
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr,
                                            env={cc.TRAINER_GREEN_ENV: "0"}))

    def test_trener_verdikt_na_chuzhom_kommite_ne_zaschityvaetsya(self):
        """Ключ подставлен под наш коммит, а снят вердикт на другом HEAD — ворота сверяют ПОЛЕ."""
        self._verdict(commit="d14d450")
        ok, why = cc.trainer_verdict("abc1234", path=self.tr, env={})
        self.assertFalse(ok)
        self.assertIn("на ДРУГОМ коммите", why)
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))

    def test_trener_krasnyi_verdikt_derzhit(self):
        self._verdict(result="red", checks_passed=95, cases=11)
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))

    def test_trener_nepolnyi_progon_ne_osnovanie(self):
        for bad in ({"runs": 1}, {"clean": False}, {"cases": 3, "cases_total": 3},
                    {"checks_passed": 95}, {"corpus_sha": "0" * 16}):
            self._verdict(**bad)
            self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr,
                                                env={}), f"открылось на {bad}")

    def test_ne_hesh_ne_osnovanie(self):
        ok, msg = cc.approve("не-коммит")
        self.assertFalse(ok)
        self.assertIn("не похоже", msg)

    # ─────── ОТРИЦАТЕЛЬНЫЙ ТЕСТ №1 ЗАДАНИЯ (05.09.2026) ───────
    # ПОД ЗАМОРОЗКОЙ ЗЕЛЁНЫЙ ВЕРДИКТ ВОРОТ НЕ ОТКРЫВАЕТ.
    # Живой замер класса: 26.08 03:35:35 в реестр легла зелёная запись 74be777 → 03:35:43 демон
    # написал «основание пропуска «trainer» — применяем к userbot,moderbot». Восемь секунд, и
    # заморозка (лежит с 21.08) не удержала ничего: она жила только в адресе отказа.

    def test_pod_zamorozkoi_zelenyi_verdikt_ne_otkryvaet_vorota(self):
        """ГЛАВНЫЙ замок задания: вердикт зелёный ПО СУЩЕСТВУ (его же и проверяем строкой ниже),
        а ворота под заморозкой держат. Не «вердикт испортился» — основание не действует."""
        self._verdict()
        self.assertTrue(cc.trainer_green("abc1234", path=self.tr, env={}),
                        "вердикт обязан быть зелёным САМ ПО СЕБЕ, иначе тест доказывает не то")
        self.freeze()
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}),
                          "под заморозкой зелёный вердикт открыл ворота")

    def test_pod_zamorozkoi_poimyonnoe_da_vladelca_prohodit(self):
        """Заморозка глушит МАШИНУ, а не человека: «да» на ЭТОТ коммит работает и под флагом."""
        self.freeze()
        cc.approve("abc1234", path=self.rel)
        self.assertEqual(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}),
                         "owner")

    def test_snyatie_zamorozki_vozvrashchaet_vtoroe_osnovanie_tem_zhe_tikom(self):
        """Ручка одна и снимается одним движением: файла нет → прежнее поведение байт-в-байт."""
        self._verdict()
        self.freeze()
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))
        os.remove(self.flag)
        self.assertEqual(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}),
                         "trainer")

    def test_sostoyanie_zamorozki_neizvestno_derzhim(self):
        """FAIL-CLOSED и направление ОБРАТНОЕ `frozen()`: «не знаю, лежит ли флаг» → не открываем.
        Право выкатить на живого клиента — не то место, где незнание толкуют в сторону движения."""
        self._verdict()
        with mock.patch.object(cc, "freeze_known", lambda flag=None: None):
            self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr,
                                                env={}))
            self.assertFalse(cc.frozen(), "а вот АДРЕС отказа на незнании остаётся громким")

    # ─────── ОТРИЦАТЕЛЬНЫЙ ТЕСТ №2 ЗАДАНИЯ (05.09.2026) ───────
    # ВЕРДИКТ НА КОММИТЕ A НЕ ОТКРЫВАЕТ ВОРОТА КОММИТУ B С ТЕМ ЖЕ ПРЕФИКСОМ.
    # Живой путь ровно такой: запись несёт 40 hex (bind_head), а спрашивают ворота коротким —
    # `_reconcile_children` подаёт head[:9]. Прежнее условие включало полную сверку, только когда
    # ОБЕ стороны 40-символьные, то есть в бою сверяло РОВНО СЕМЁРКУ.

    A40 = "abc1234" + "a" * 33                       # кандидат, на котором снят вердикт
    B40 = "abc1234" + "b" * 33                       # HEAD: тот же префикс abc1234, другой коммит

    def _expand(self, s):
        """Раскрыватель-двойник git: короткую метку доводит до полного хеша ТОГО коммита."""
        for full in (self.A40, self.B40):
            if full.startswith(s):
                return full
        return ""

    def test_verdikt_kandidata_ne_vykatyvaet_head_s_tem_zhe_prefiksom(self):
        self._verdict(commit=self.A40)
        ok, why = cc.trainer_verdict(self.B40[:9], path=self.tr, env={}, expand=self._expand)
        self.assertFalse(ok, "вердикт кандидата открыл ворота ДРУГОМУ коммиту")
        self.assertIn("ДРУГОМ коммите", why)
        self.assertIsNone(cc.release_reason(self.B40[:9], path=self.rel, trainer_path=self.tr,
                                            env={}, expand=self._expand))

    def test_svoi_kommit_korotkoi_metkoi_po_prezhnemu_otkryvaet(self):
        """Замок не должен убить живое: та же короткая метка СВОЕГО коммита ворота открывает."""
        self._verdict(commit=self.A40)
        self.assertEqual(cc.release_reason(self.A40[:9], path=self.rel, trainer_path=self.tr,
                                           env={}, expand=self._expand), "trainer")

    def test_bez_raskryvatelya_sverka_otkazyvaet_a_ne_schitaet_prefiks(self):
        """Нечем привести короткую сторону к длинной → сверка ОТКАЗЫВАЕТ (fail-closed), и это
        касается СВОЕГО коммита тоже: молчание git не повод верить семёрке."""
        self._verdict(commit=self.A40)
        for probe in (self.A40[:9], self.B40[:9]):
            ok, why = cc.trainer_verdict(probe, path=self.tr, env={})
            self.assertFalse(ok, probe)
            self.assertIn("ОТКАЗАНА", why)

    def test_devyatka_protiv_devyatki_tozhe_ne_prefiks(self):
        """Одинаковая длина префикса не спасает: 7 символов режет `short()` с обеих сторон."""
        self._verdict(commit=self.A40[:9])
        ok, why = cc.same_commit(self.A40[:9], self.B40[:9], self._expand)
        self.assertFalse(ok)
        self.assertIn("РАЗНЫЕ", why)

    def test_krivoi_raskryvatel_ne_otkryvaet_vorota(self):
        """Ответ раскрывателя ПРОВЕРЯЕТСЯ: не 40 hex или не начинается с запрошенного — ''."""
        for bad in (lambda s: "z" * 40, lambda s: "0" * 40, lambda s: "abc", lambda s: None,
                    lambda s: (_ for _ in ()).throw(RuntimeError("git молчит"))):
            self.assertEqual(cc.full_commit("abc1234", bad), "", bad)


# ─────────────────────────── 3. ВОРОТА ВЫХОДА ───────────────────────────

class GateBase(unittest.TestCase):
    """Общая обвязка: боевые _notify/_cowork/reason замоканы, живое НЕ дёргается."""

    def setUp(self):
        self.cards, self.cowork, self.restarts = [], [], []
        o._CLIENT_HELD_WARNED.clear()
        # ЗЕРКАЛО ИЗОЛЯЦИИ ЗАМОРОЗКИ (22.08.2026, правило «класс-фикс — сразу на ОБЕ полосы»).
        # Течь нашли и закрыли в test_pc_orchestrator.Base, а ЭТА половина осталась открытой — и
        # текла в обе стороны ровно так же:
        #   • ЧТЕНИЕ: владелец заморозил контур 21.08 19:36 → голдены ворот здесь покраснели, не
        #     изменившись ни строкой (test_klientskii_kommit_ne_primenyaetsya_i_daet_kartochku ждёт
        #     карточку, а под заморозкой повтор формы уходит в ленту). Вердикт голдена не смеет
        #     зависеть от того, заморожен ли контур в эту минуту;
        #   • ЗАПИСЬ: `gate_route` дописывал БОЕВОЙ реестр форм, а записанная форма гасит владельцу
        #     ПЕРВУЮ настоящую карточку по ней. Замер 22.08 00:36 — n=160 по «moderbot,userbot|
        #     suggest.py»; счётчик нарастили прогоны тестов, демон на 57166ab туда писать не мог.
        # Заморозку ПО СУЩЕСТВУ проверяет test_pc_orchestrator.TestZamorozkaKontura своим флагом.
        _frz = tempfile.mkdtemp(prefix="frz_cc_")
        self.addCleanup(shutil.rmtree, _frz, ignore_errors=True)
        for _a, _v in (("FREEZE_FLAG", os.path.join(_frz, "pc_orchestrator.contour_frozen")),
                       ("GATE_SEEN_FILE", os.path.join(_frz, "gate_seen.json"))):
            _s = getattr(cc, _a)
            setattr(cc, _a, _v)
            self.addCleanup(lambda a=_a, v=_s: setattr(cc, a, v))
        self.p_notify = mock.patch.object(o, "_notify", lambda t: self.cards.append(t))
        # Карточка ворот с 05.09.2026 уходит своей дверью (_notify_gate_card: коммит + текст) —
        # она несёт КНОПКИ, а _notify их не умеет. Голдены ниже читают cards[0] как текст карточки.
        self.p_gcard = mock.patch.object(o, "_notify_gate_card",
                                         lambda c, t: self.cards.append(t))
        self.p_cowork = mock.patch.object(o, "_cowork", lambda t: self.cowork.append(t))
        self.p_subj = mock.patch.object(o, "_commit_subject", lambda c: "тема коммита")
        self.p_reason = mock.patch.object(cc, "release_reason", lambda *a, **k: None)
        for p in (self.p_notify, self.p_gcard, self.p_cowork, self.p_subj, self.p_reason):
            p.start()
            self.addCleanup(p.stop)
        o._apply_restart_at.clear()

    def restart(self, kind):
        self.restarts.append(kind)
        return True, [4242], "PID поднят, лог свежий"

    def gate_green(self, mods):
        return True, "ok"


class TestVorotaVyhoda(GateBase):
    def test_klientskii_kommit_ne_primenyaetsya_i_daet_kartochku(self):
        """Дев-задача тронула suggest.py → рестарта НЕТ, карточка ЕСТЬ."""
        res = o.maybe_update_bots(1, "тз: поправь детект", "old",
                                  changed_fn=lambda h: ["suggest.py", "test_suggest.py"],
                                  gate_fn=self.gate_green, restart_fn=self.restart,
                                  head_fn=lambda: "4528917", dirty_fn=lambda: [])
        self.assertEqual(self.restarts, [])
        self.assertIn("ОСТАНОВЛЕНО воротами клиентского контура", res)
        self.assertEqual(len(self.cards), 1)
        self.assertIn("suggest.py", self.cards[0])

    def test_kommit_v_okrugu_demona_teper_derzhitsya_07_09(self):
        """ЗАПИСЬ СМЕНЫ 07.09.2026, а не подгонка: коммит в округу демона ворота ДЕРЖАТ.

        Голден звался `test_vnutrennii_kommit_primenyaetsya_kak_ranshe` и требовал `res == ""` со
        словами «карта на ботов не указывает → прежний путь байт-в-байт». Ровно это и было дырой:
        `pc_agent.py` лежит в замыкании живых ботов (цепь userbot_listen → moderation_core →
        ЛЕНИВЫЙ `import pc_orchestrator` → pc_agent), то есть правка доезжает до процесса бота с
        диска, а ворота о ней не спрашивали вовсе — их вход стоял ЗА картой рестарта.

        Строгость не ослаблена, а поднята: было «проезжает молча», стало «удержано и записано».
        Красный здесь = ребро исчезло ИЛИ вопрос воротам снова сузили до карты."""
        res = o.maybe_update_bots(1, "тз: правка агента", "old",
                                  changed_fn=lambda h: ["pc_agent.py"],
                                  gate_fn=self.gate_green, restart_fn=self.restart,
                                  head_fn=lambda: "aaaa111", dirty_fn=lambda: [])
        self.assertEqual(self.restarts, [])
        self.assertIn("ОСТАНОВЛЕНО воротами клиентского контура", res)
        self.assertIn("pc_agent.py", res)
        # Отказ не молчит: строка идёт в журнал. Карточки здесь нет и быть не может — метка
        # «aaaa111» в репозитории не резолвится, и `_gate_commit_known` карточку не собирает
        # (правило 05.09: не спрашивать владельца про несуществующий коммит). Ворот это не
        # касается — они уже отказали, и `res` выше это говорит.
        self.assertTrue([c for c in self.cowork if "ОСТАНОВЛЕНО" in c and "pc_agent.py" in c],
                        "отказ ворот обязан лечь строкой в журнал: %s" % self.cowork)

    def test_stroka_otkaza_ne_utverzhdaet_chto_kartochka_ushla(self):
        """Рапорт не смеет обещать карточку, которой под заморозкой не бывает (07.09.2026).

        Адрес отказа выбирает `gate_route`, и под заморозкой он ВСЕГДА лента. Строка «владельцу
        отправлена карточка» врала и до правки, но правка делает отказ частым (14 → 47 остановок
        на корпусе 200), а рапорт о неотправленной карточке — молчаливый ложный зелёный."""
        res = o.maybe_update_bots(1, "тз: поправь детект", "old",
                                  changed_fn=lambda h: ["suggest.py"],
                                  gate_fn=self.gate_green, restart_fn=self.restart,
                                  head_fn=lambda: "4528917", dirty_fn=lambda: [])
        self.assertIn("ОСТАНОВЛЕНО воротами клиентского контура", res)
        self.assertNotIn("карточка", res)
        self.assertIn("отказ записан воротами", res)

    def test_fail_closed_priznak_upal(self):
        """Признак кинул исключение → файл считаем клиентским, применения нет."""
        with mock.patch.object(cc, "is_client", side_effect=RuntimeError("граф не построился")):
            held = o._client_paths(["chto_to.py"])
        self.assertEqual(held, ["chto_to.py"])

    def test_osnovanie_da_otkryvaet_vorota(self):
        with mock.patch.object(cc, "release_reason", lambda *a, **k: "owner"):
            res = o.maybe_update_bots(1, "тз: поправь детект", "old",
                                      changed_fn=lambda h: ["suggest.py"],
                                      gate_fn=self.gate_green, restart_fn=self.restart,
                                      head_fn=lambda: "4528917", dirty_fn=lambda: [])
        self.assertEqual(sorted(self.restarts), ["moderbot", "userbot"])
        self.assertIn("обновлён до 4528917", res)
        self.assertEqual(self.cards, [])

    def test_osnovanie_trener_otkryvaet_vorota(self):
        with mock.patch.object(cc, "release_reason", lambda *a, **k: "trainer"):
            o.maybe_update_bots(1, "тз: поправь детект", "old",
                                changed_fn=lambda h: ["suggest.py"],
                                gate_fn=self.gate_green, restart_fn=self.restart,
                                head_fn=lambda: "4528917", dirty_fn=lambda: [])
        self.assertEqual(sorted(self.restarts), ["moderbot", "userbot"])

    def test_selfupdate_deti_derzhatsya_a_agent_net(self):
        """self-update демона: клиентский файл в диапазоне → детей-ботов не рестартим."""
        out = o._selfupdate_restart_children("old", LIVE_COMMIT,
                                             diff_fn=lambda a, b: ["suggest.py", "pc_orchestrator.py"],
                                             restart_fn=self.restart)
        self.assertEqual(self.restarts, [])
        self.assertIn("ОСТАНОВЛЕНО воротами", out)
        self.assertEqual(len(self.cards), 1)

    def test_selfupdate_vnutrennii_diapazon_edet(self):
        """В диапазоне только внутреннее, но карта ведёт на userbot → применяем как раньше."""
        with mock.patch.object(o, "_dirty_block", lambda *a, **k: []):
            out = o._selfupdate_restart_children("old", LIVE_COMMIT,
                                                 diff_fn=lambda a, b: ["moderation_ipc.py"],
                                                 restart_fn=self.restart)
        # moderation_ipc.py — КЛИЕНТСКИЙ по графу (его импортит userbot_listen), значит держим
        self.assertEqual(self.restarts, [])
        self.assertIn("ОСТАНОВЛЕНО воротами", out)

    def test_rekonsilyaciya_derzhit_i_ne_dvigaet_metku(self):
        """Главная дыра класса: тик реконсиляции. Метка НЕ двигается — придёт «да», тик применит."""
        o._last_child_commit = "aaaaaaaaaaaa"
        o._child_reconcile_rejected = None
        out = o.reconcile_children_tick(head_fn=lambda: "4528917456789",
                                        diff_fn=lambda a, b: ["suggest.py"],
                                        gate_fn=self.gate_green, restart_fn=self.restart)
        self.assertEqual(self.restarts, [])
        self.assertIn("ОСТАНОВЛЕНО", out)
        self.assertEqual(o._last_child_commit, "aaaaaaaaaaaa")     # метка на месте
        self.assertIsNone(o._child_reconcile_rejected)             # HEAD не «отвергнут» — ждём решения

    def test_rekonsilyaciya_posle_da_primenyaet_tot_zhe_kommit(self):
        o._last_child_commit = "aaaaaaaaaaaa"
        o._child_reconcile_rejected = None
        with mock.patch.object(cc, "release_reason", lambda *a, **k: "owner"):
            o.reconcile_children_tick(head_fn=lambda: "4528917456789",
                                      diff_fn=lambda a, b: ["suggest.py"],
                                      gate_fn=self.gate_green, restart_fn=self.restart)
        self.assertEqual(sorted(self.restarts), ["moderbot", "userbot"])
        self.assertEqual(o._last_child_commit, "4528917456789")

    def test_rekonsilyaciya_derzhit_okrugu_demona_i_ne_teryaet_pometku_07_09(self):
        """ЗАПИСЬ СМЕНЫ 07.09.2026: тик реконсиляции ДЕРЖИТ коммит в округу демона, а пометка
        агента при этом НЕ ТЕРЯЕТСЯ — она едет той же строкой отказа.

        Голден звался `test_rekonsilyaciya_vnutrennego_edet_kak_ranshe` и требовал, чтобы метка
        ушла вперёд, а ворота промолчали. Это и была дыра достижимости: вход в ворота стоял ЗА
        картой рестарта, карта про `pc_agent.py` знает только процесс `pc_agent` — и коммит
        проезжал мимо вопроса.

        Почему пометка едет строкой отказа, а не громким каналом: удержанный коммит НЕ двигает
        метку, тик приходит каждые 60 с, и `_cowork`/`_notify` дали бы спам без единого нового
        факта (дедуп есть у ворот, у пометки его нет). Красный здесь = либо ворота снова
        пропускают округу демона, либо пометку опять потеряли."""
        o._last_child_commit = "aaaaaaaaaaaa"
        o._child_reconcile_rejected = None
        out = o.reconcile_children_tick(head_fn=lambda: "bbbbbbbbbbbb",
                                        diff_fn=lambda a, b: ["pc_agent.py"],
                                        gate_fn=self.gate_green, restart_fn=self.restart)
        self.assertIn("ОСТАНОВЛЕНО", out)
        self.assertIn("pc_agent.py", out)
        self.assertIn("ждёт РУЧНОГО рестарта", out)              # пометка не потеряна
        self.assertEqual(self.restarts, [])
        self.assertEqual(o._last_child_commit, "aaaaaaaaaaaa")   # метка НЕ ушла — коммит удержан
        self.assertIsNone(o._child_reconcile_rejected)           # не «отвергнут» — ждём решения
        # Громких каналов пометка НЕ занимает: спама раз в 60 с быть не должно.
        self.assertEqual([c for c in self.cards if "ЖДЁТ РУЧНОГО" in c], [])

    def test_kartochka_ne_povtoryaetsya_kazhdyi_tik(self):
        """Реконсиляция приходит каждые 60 с — карточка обязана уйти РОВНО один раз на коммит+состав."""
        for _ in range(3):
            o._last_child_commit = "aaaaaaaaaaaa"
            o._child_reconcile_rejected = None
            o.reconcile_children_tick(head_fn=lambda: "4528917456789",
                                      diff_fn=lambda a, b: ["suggest.py"],
                                      gate_fn=self.gate_green, restart_fn=self.restart)
        self.assertEqual(len(self.cards), 1)

    # ─────── ВИДИМОСТЬ ДОЛГА ДЕТЕЙ ПОСЛЕ РЕСТАРТА ДЕМОНА (22.08.2026) ───────
    # ОТРИЦАТЕЛЬНЫЙ ТЕСТ КЛАССА. Живой случай: self-update 22.08 01:08:21 (=21.08 18:08 UTC)
    # поднял новый процесс демона; метка родилась заново и приняла вершину за применённое, а
    # ЖИВЫЕ дети стояли на 538bbbd — 97 коммитов позади. Ворота замолчали: последний отказ в
    # логе 01:05:20, дальше тишина при пяти клиентских файлах в диффе.
    # Прибор обязан отказать РОВНО в состоянии «метка выглядит правильной, дети старые» и
    # обязан ПОГАСНУТЬ, когда дети на вершине. Оба исхода — здесь, на одной обвязке.

    _OLD, _TIP = "538bbbd18a7b", "d860a9ad143f"

    def _svezhii_demon(self, live, ancestor=True, changed=("suggest.py",)):
        """Тик РОДИВШЕГОСЯ ЗАНОВО демона (метка None — ровно как после self-update)."""
        o._last_child_commit, o._child_reconcile_rejected = None, None
        with mock.patch.object(o, "_is_ancestor", lambda a, b, **k: ancestor):
            return o.reconcile_children_tick(
                head_fn=lambda: self._TIP, diff_fn=lambda a, b: list(changed),
                gate_fn=self.gate_green, restart_fn=self.restart, live_base_fn=lambda: live)

    def test_metka_vyglyadit_pravilnoi_a_deti_starye_pribor_OTKAZYVAET(self):
        """«Метка = вершина, дети позади» → ворота ОБЯЗАНЫ отказать, а не молчать."""
        out = self._svezhii_demon((self._OLD, "ok", "userbot жив с 2026-08-18 20:27:55"))
        self.assertEqual(self.restarts, [])                      # живых детей не тронули
        self.assertIn("ОСТАНОВЛЕНО", out)                        # ПРИБОР ПОКАЗАЛ ОТКАЗ
        self.assertEqual(o._last_child_commit, self._OLD)        # метка отведена НАЗАД, к живому факту
        self.assertEqual(len(self.cards), 1)
        self.assertIn("suggest.py", self.cards[0])

    def test_deti_na_vershine_metka_GASNET(self):
        """Обратное: живые дети уже на вершине → отказа нет, карточек нет, метка = вершина."""
        out = self._svezhii_demon((self._TIP, "ok", "userbot жив с сегодня"), ancestor=False)
        self.assertEqual((out, self.restarts, self.cards), ("", [], []))
        self.assertEqual(o._last_child_commit, self._TIP)        # метка погасла — долга нет

    def test_zamok_tolko_nazad_ne_predok_ne_prinimaetsya(self):
        """Признак умеет ТОЛЬКО раскрывать долг: база НЕ предок вершины → берём вершину."""
        out = self._svezhii_demon(("ffffffffffff", "ok", "чужой хеш"), ancestor=False)
        self.assertEqual((out, self.restarts, self.cards), ("", [], []))
        self.assertEqual(o._last_child_commit, self._TIP)        # чужой хеш метку НЕ занял

    def test_zamok_ne_znayu_ne_znachit_da(self):
        """git не смог ответить про предка (None) → база не принята, прежний путь."""
        out = self._svezhii_demon((self._OLD, "ok", "предок не доказан"), ancestor=None)
        self.assertEqual((out, self.restarts, self.cards), ("", [], []))
        self.assertEqual(o._last_child_commit, self._TIP)

    def test_baza_ne_dokazana_prezhnee_povedenie_bait_v_bait(self):
        """CIM/git не ответили → забор на месте: метка = вершина, рестартов ноль, карточек ноль."""
        out = self._svezhii_demon((None, "unknown", "CIM не ответил"), ancestor=None)
        self.assertEqual((out, self.restarts, self.cards), ("", [], []))
        self.assertEqual(o._last_child_commit, self._TIP)

    def test_zhivyh_detei_net_metka_beret_vershinu(self):
        """Честная пустота (детей нет вовсе) — стале-коду взяться неоткуда, прежний путь."""
        out = self._svezhii_demon((None, "none", "живых детей нет"))
        self.assertEqual((out, self.restarts, self.cards), ("", [], []))
        self.assertEqual(o._last_child_commit, self._TIP)

    def test_ruchnoi_rychag_vladelca_vorota_ne_trogayut(self):
        """«рестартни userbot» — решение человека, ворота туда не лезут (как и запрет грязного дерева)."""
        status, res = o._exec_command("restart_userbot", restart_fn=lambda k: (True, [777], "ok"))
        self.assertEqual(status, "done")
        self.assertIn("777", res)
        self.assertEqual(self.cards, [])

    def test_komanda_vykati_pishet_osnovanie(self):
        seen = {}
        status, res = o._exec_release_client(head_fn=lambda: "4528917",
                                             approve_fn=lambda c: (seen.setdefault("c", c), (True, c))[1],
                                             cowork=self.cowork.append)
        self.assertEqual(status, "done")
        self.assertEqual(seen["c"], "4528917")
        self.assertIn("4528917", res)

    def test_raspoznavanie_komandy_vykati(self):
        for t in ("выкати", "выкатывай", "Да, выкати", "выкати на ботов", "применить коммит", "раскати"):
            self.assertEqual(o._match_command(t), "release_client", t)
        for t in ("тз: выкати новый детект и поправь suggest", "рестартни userbot", "статус контура"):
            self.assertNotEqual(o._match_command(t), "release_client", t)


class TestKartochkaGolden(GateBase):
    def test_tekst_kartochki_neset_minimum(self):
        """Минимум владельца: ЧТО меняется, КАКИЕ файлы, КАКОЙ коммит, КАК откатить + чем открыть."""
        o.maybe_update_bots(1, "тз: гард приветствий", "old",
                            changed_fn=lambda h: ["suggest.py"],
                            gate_fn=self.gate_green, restart_fn=self.restart,
                            head_fn=lambda: "4528917", dirty_fn=lambda: [])
        card = self.cards[0]
        self.assertIn("4528917", card)                       # какой коммит
        self.assertIn("тема коммита", card)                  # что меняется
        self.assertIn("suggest.py", card)                    # какие файлы
        self.assertIn("git revert --no-edit 4528917", card)  # как откатить
        self.assertIn("«да»", card)                          # основание 1
        self.assertIn("тренажёр", card)                      # основание 2
        self.assertIn("SUGGEST_TEST_MODE", card)             # второй слой не трогали


# ─────────────────────────── 4. ВОРОТА ВХОДА (ревизор) ───────────────────────────

class TestVorotaVhoda(unittest.TestCase):
    def test_nahodka_v_klientskom_fayle_uhodit_vladelcu(self):
        cl, hits, det = cc.mentions("поправь детект намерения в suggest.py и добавь голдены", REPO)
        self.assertTrue(cl)
        self.assertIn("suggest.py", hits)
        self.assertTrue(det)

    def test_goloe_imya_modulya_bez_rasshireniya_tozhe_lovitsya(self):
        """Обход формулировкой («поправь suggest», без .py) не работает."""
        cl, hits, _ = cc.mentions("поправь гард приветствий в suggest", REPO)
        self.assertTrue(cl)
        self.assertIn("suggest.py", hits)

    def test_nahodka_vo_vnutrennem_ostaetsya_zelenoi_zadachei(self):
        cl, hits, det = cc.mentions("в pc_orchestrator.py добавь строку METRICS в лог", REPO)
        self.assertFalse(cl)
        self.assertEqual(hits, [])
        self.assertTrue(det)

    def test_fail_closed_faily_ne_nazvany(self):
        cl, hits, det = cc.mentions("почини детект, он врёт на длинных окнах", REPO)
        self.assertTrue(cl)          # неопределимо → клиентский
        self.assertFalse(det)

    def test_route_klientskuyu_nahodku_perevodit_v_owner(self):
        f = {"action": "task", "class": "е", "task_text": "поправь stripLeadingGreeting в suggest.py",
             "evidence": "", "client_id": 1716492857}
        cl, hits, det = o._revizor_finding_touches_client(f["task_text"])
        self.assertTrue(cl)
        g = o._revizor_demote_client_task(f, hits, det)
        self.assertEqual(g["action"], "owner")
        self.assertEqual(g["task_text"], "")
        self.assertIn("клиентский контур", g["evidence"])
        self.assertIn("suggest.py", g["evidence"])

    def test_enqueue_last_resort_ne_stavit_klientskuyu_zadachu(self):
        """Даже если находка каким-то путём дошла до enqueue — зелёной задачей она не встанет."""
        calls = []
        with mock.patch.object(o, "enqueue_pc_task", lambda t, frm=None: (calls.append(t), (True, 1, None))[1]):
            enq, skip, left = o._revizor_enqueue_tasks(
                [{"class": "б", "task_text": "поправь detectVehicleType в suggest.py", "client_id": 1}],
                [], 0)
        self.assertEqual((enq, skip, left), (0, 1, []))   # клиентская находка — не в спул: её место в owner-карточке
        self.assertEqual(calls, [])

    def test_enqueue_vnutrennyuyu_zadachu_stavit_kak_ranshe(self):
        calls = []
        with mock.patch.object(o, "enqueue_pc_task", lambda t, frm=None: (calls.append(t), (True, 7, None))[1]):
            enq, skip, left = o._revizor_enqueue_tasks(
                [{"class": "ж", "task_text": "в pc_orchestrator.py почини троттлинг тика", "client_id": 1}],
                [], 0)
        self.assertEqual((enq, skip, left), (1, 0, []))
        self.assertEqual(len(calls), 1)


# ─────────────────────────── 5. ЖИВЫЕ ЦЕПИ 4 и 44 ───────────────────────────

class TestZhivyeCepi(unittest.TestCase):
    """Доказательство на реальных цепях: при новых воротах они бы до бота НЕ доехали."""

    def test_cep_4_tip_ts(self):
        """id=4 (класс б, окно 45349667): правила suggest.py — detectVehicleType + промпт «ТИП ТС»."""
        self.assertEqual(o._client_paths(["suggest.py", "test_suggest.py"]), ["suggest.py", "test_suggest.py"]
                         if cc.is_client("test_suggest.py", REPO) else ["suggest.py"])
        self.assertTrue(cc.is_client("suggest.py", REPO))
        cl, hits, _ = cc.mentions("класс б: чужая модель — поправь detectVehicleType в suggest.py "
                                  "и промпт-блок ТИП ТС, добавь голдены", REPO)
        self.assertTrue(cl, "цепь 4 обязана попасть под ворота ВХОДА")
        self.assertIn("suggest.py", hits)

    def test_cep_44_gard_privetstvii(self):
        """id=44 (класс е, окно 1716492857): коммит 4528917 — hasGreetingInWindow/stripLeadingGreeting."""
        self.assertTrue(cc.is_client("suggest.py", REPO), "цепь 44 обязана попасть под ворота ВЫХОДА")
        cl, hits, _ = cc.mentions("класс е: повтор приветствия — хелперы hasGreetingInWindow/"
                                  "stripLeadingGreeting в suggest, тесты", REPO)
        self.assertTrue(cl, "цепь 44 обязана попасть под ворота ВХОДА")
        self.assertIn("suggest.py", hits)


# ── 6. ЗАМОРОЗКА КОНТУРА: под заморозкой владельца не спрашивают вовсе (05.09.2026) ──
# Живой факт, из которого раздел заведён 21.08 (замер по pc_orchestrator.log): 14 отказов ворот,
# 14 карточек владельцу, и все 14 — про ОДНУ форму «userbot,moderbot | price_gate.py,
# price_source.py, suggest.py». Размножал их КОММИТ в подписи дедупа, а не смена отказа: все 14
# коммитов трогали только docs/. Тогда в ленту увели ПОВТОР формы, оставив первый отказ громким.
#
# ПОЧЕМУ ПРАВИЛО ПЕРЕПИСАНО (замер 05.09.2026, тот же лог): 25 отказов, 21 подавлен — и всё равно
# ЧЕТЫРЕ карточки, потому что форма включает состав клиентских файлов, а он растёт с каждой задачей
# ночи (7 файлов → 8 → 10 → 3). Мера «новая форма — новость» под заморозкой ложна: решение владельца
# от состава файлов не зависит. Постоянное решение владельца 05.09.2026 дословно: «пока контур
# заморожен — про выкатку детей не спрашивать вовсе».
#
# Здесь стережём ЧЕТЫРЕ вещи: под заморозкой карточки нет НИ ПРИ КАКОЙ смене состава; без заморозки
# карточка уходит на каждом отказе как раньше; молчание не трогает право на выкатку; отказ остаётся
# в ленте целиком.

class TestZamorozkaVorot(unittest.TestCase):

    def setUp(self):
        d = tempfile.mkdtemp(prefix="cc_freeze_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.flag = os.path.join(d, "pc_orchestrator.contour_frozen")
        self.seen = os.path.join(d, "pc_orchestrator.gate_seen.json")

    def _freeze(self):
        with open(self.flag, "w", encoding="utf-8") as f:
            f.write("контур заморожен (тест)\n")

    def _route(self, kinds, held, **kw):
        return cc.gate_route(kinds, held, flag=self.flag, path=self.seen, **kw)

    # ── ручка ──
    def test_ruchka_odna_i_eto_fail_nalichie(self):
        self.assertFalse(cc.frozen(self.flag))
        self._freeze()
        self.assertTrue(cc.frozen(self.flag))

    def test_bez_zamorozki_reestr_dazhe_ne_trogaem(self):
        """Заморозки нет → прежнее поведение байт-в-байт: карточка И ни одной записи на диск."""
        route, why = self._route(["userbot"], ["suggest.py"])
        self.assertEqual(route, cc.ROUTE_CARD)
        self.assertIn("заморозки нет", why)
        self.assertFalse(os.path.exists(self.seen), "без заморозки реестр форм не создаётся")

    # ── подпись отказа ──
    def test_podpis_ne_soderzhit_kommita(self):
        a = cc.refusal_shape(["userbot", "moderbot"], ["suggest.py", "price_gate.py"])
        b = cc.refusal_shape(["moderbot", "userbot"], ["price_gate.py", "suggest.py"])
        self.assertEqual(a, b, "порядок и регистр перечислений подпись менять не смеют")
        self.assertEqual(a, "moderbot,userbot|price_gate.py,suggest.py")
        self.assertNotEqual(a, cc.refusal_shape(["userbot", "moderbot"], ["suggest.py"]))

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1: под заморозкой карточки нет ни при какой смене состава ──
    def test_pod_zamorozkoi_dazhe_pervyi_otkaz_v_lentu(self):
        """Ветка, рождавшая все четыре карточки 05.09, снята: первый отказ формы больше НЕ громкий."""
        self._freeze()
        r1, why1 = self._route(["userbot"], ["suggest.py"])
        r2, why2 = self._route(["userbot"], ["suggest.py"])
        self.assertEqual((r1, r2), (cc.ROUTE_FEED, cc.ROUTE_FEED))
        self.assertIn("не спрашиваю ВОВСЕ", why1)
        self.assertIn("отказ №1", why1)
        self.assertIn("отказ №2", why2)

    def test_smena_sostava_faylov_kartochku_ne_rozhdaet(self):
        """ГЛАВНЫЙ отрицательный тест задания: состав клиентских файлов менялся 05.09 четыре раза и
        четыре раза покупал карточку. Теперь не покупает НИ ОДНОЙ — ни ростом, ни убылью, ни сменой
        того, кого держим."""
        self._freeze()
        sostavy = (["suggest.py"],
                   ["suggest.py", "pricing.py"],                       # состав вырос
                   ["suggest.py", "pricing.py", "price_source.py"],    # ещё вырос
                   ["pricing.py"],                                     # состав убыл и сменился
                   ["a.py", "b.py", "c.py", "d.py", "e.py"])           # состав совсем чужой
        for s in sostavy:
            for kinds in (["userbot"], ["userbot", "moderbot"], ["moderbot"]):
                with self.subTest(sostav=s, kinds=kinds):
                    self.assertEqual(self._route(kinds, s)[0], cc.ROUTE_FEED)

    def test_novyi_epizod_schet_zanovo_no_vopros_ne_vozvrashchaetsya(self):
        """Новая заморозка начинает СЧЁТ заново — но не вопрос: пока флаг лежит, адрес один."""
        self._freeze()
        self.assertIn("отказ №1", self._route(["userbot"], ["suggest.py"])[1])
        self.assertIn("отказ №2", self._route(["userbot"], ["suggest.py"])[1])
        ep0 = cc.freeze_episode(self.flag)
        os.utime(self.flag, (ep0 + 3600, ep0 + 3600))              # разморозили и заморозили снова
        self.assertNotEqual(cc.freeze_episode(self.flag), ep0)
        r, why = self._route(["userbot"], ["suggest.py"])
        self.assertEqual(r, cc.ROUTE_FEED, "новый эпизод заморозки — всё ещё заморозка")
        self.assertIn("отказ №1", why, "счёт эпизода начинается заново")

    def test_reestr_ne_zapisalsya_no_molchim(self):
        """Сбой реестра адреса больше не решает: счёт — бухгалтерия, решение стои́т на ФЛАГЕ.
        Прежний fail-loud снят СОЗНАТЕЛЬНО — он охранял правило, которого больше нет."""
        self._freeze()
        with mock.patch.object(cc, "_seen_save", return_value=(None, "OSError: диск только на чтение")):
            r1, why1 = self._route(["userbot"], ["suggest.py"])
            r2, why2 = self._route(["userbot"], ["suggest.py"])
        self.assertEqual((r1, r2), (cc.ROUTE_FEED, cc.ROUTE_FEED))
        self.assertIn("счётчик форм НЕ записан", why1)
        self.assertIn("номер назвать не смог", why2)

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2: без заморозки — карточка как раньше ──
    def test_bez_zamorozki_kartochka_na_kazhdom_otkaze(self):
        """ПРЕДСМЕРТНЫЙ ВЗГЛЯД задания: провалимся, если заглушим карточку целиком. Снятая заморозка
        обязана вернуть вопрос НА КАЖДОМ отказе — включая те самые составы, что молчали выше."""
        for s in (["suggest.py"], ["suggest.py", "pricing.py"], ["pricing.py"]):
            for _ in range(3):                       # повтор той же формы тоже громкий: заморозки нет
                with self.subTest(sostav=s):
                    r, why = self._route(["userbot"], s)
                    self.assertEqual(r, cc.ROUTE_CARD)
                    self.assertIn("заморозки нет", why)
        self.assertFalse(os.path.exists(self.seen), "без заморозки реестр форм не трогаем вовсе")

    def test_snyatie_zamorozki_vozvrashchaet_vopros_tem_zhe_tikom(self):
        """Отмена — одно движение и без перезапуска: тот же вызов, тот же состав, флага нет → карточка."""
        self._freeze()
        self.assertEqual(self._route(["userbot"], ["suggest.py"])[0], cc.ROUTE_FEED)
        os.remove(self.flag)                          # ← ровно то, чем владелец отменяет правило
        self.assertEqual(self._route(["userbot"], ["suggest.py"])[0], cc.ROUTE_CARD)

    # ── реплей ЖИВОГО корпуса 21.08 ──
    _ZHIVYE_14 = ("3b85c06", "ce464ee", "c8b0d09", "e081ba4", "9dc1f5d", "5ac6a53", "ab16dcd",
                  "f64bc3c", "93b8f22", "1dfb625", "a8ad93d", "803911d", "a6f7533", "046e9c3")
    _ZHIVAYA_FORMA = (["userbot", "moderbot"], ["price_gate.py", "price_source.py", "suggest.py"])

    def test_replei_14_otkazov_2108_pod_zamorozkoi_ni_odnoi_kartochki(self):
        """Дословный корпус 21.08: 14 отказов, 14 РАЗНЫХ коммитов, форма одна. Было — 14 карточек,
        стало с 21.08 — одна, стало с 05.09 — НИ ОДНОЙ."""
        self._freeze()
        routes = [self._route(*self._ZHIVAYA_FORMA)[0] for _c in self._ZHIVYE_14]
        self.assertEqual(len(routes), 14)
        self.assertEqual(routes.count(cc.ROUTE_CARD), 0)
        self.assertEqual(routes.count(cc.ROUTE_FEED), 14)

    def test_replei_14_otkazov_bez_zamorozki_vse_14_kartochek(self):
        """Контроль: ручки нет → те же 14 отказов дают те же 14 карточек, что и было."""
        routes = [self._route(*self._ZHIVAYA_FORMA)[0] for _c in self._ZHIVYE_14]
        self.assertEqual(routes.count(cc.ROUTE_CARD), 14)

    # ── реплей ЖИВОГО корпуса 05.09 (тот, из-за которого правило переписано) ──
    # Дословный замер по pc_orchestrator.log за 05.09: 25 отказов ворот, ПЯТЬ разных форм, порядок
    # «CCCCCCCADDDDDDDDEEEEEEEEB». Форма A («trainer.py») карточки не купила — она была названа
    # раньше в ту же заморозку; остальные ЧЕТЫРЕ родились за сутки заново и дали 4 карточки.
    _FORMY_0509 = {
        "A": ["trainer.py"],
        "B": ["expectations_pc.py", "lesson_regress.py", "trainer.py"],
        "C": ["brain_writer.py", "content_product_verifier.py", "expectations_pc.py",
              "price_source.json", "price_source.py", "shtab_box.py", "trainer.py"],
        "D": ["brain_writer.py", "content_product_verifier.py", "expectations_pc.py",
              "price_source.json", "price_source.py", "review_pack.py", "shtab_box.py",
              "trainer.py"],
        "E": ["brain_writer.py", "client_contour.py", "content_product_verifier.py",
              "expectations_pc.py", "lesson_regress.py", "price_source.json", "price_source.py",
              "review_pack.py", "shtab_box.py", "trainer.py"],
    }
    _PORYADOK_0509 = "CCCCCCCADDDDDDDDEEEEEEEEB"

    def test_replei_25_otkazov_0509_nol_kartochek(self):
        """ЧИСЛО ЗАДАНИЯ: живые 25 отказов 05.09 стоили владельцу 4 карточек. Стоят 0."""
        self._freeze()
        # Форма A была названа ДО 05.09 в ту же заморозку — воспроизводим это состояние реестра.
        self._route(["userbot", "moderbot"], self._FORMY_0509["A"])
        routes = [self._route(["userbot", "moderbot"], self._FORMY_0509[ch])[0]
                  for ch in self._PORYADOK_0509]
        self.assertEqual(len(routes), 25)
        self.assertEqual(routes.count(cc.ROUTE_CARD), 0, "владельцу не уходит НИ ОДНОЙ")
        self.assertEqual(routes.count(cc.ROUTE_FEED), 25, "и все 25 остаются в ленте")

    def test_replei_25_otkazov_0509_bez_zamorozki_vse_25_gromkie(self):
        """Контроль того же корпуса: снимут заморозку — те же 25 отказов снова спросят владельца."""
        routes = [self._route(["userbot", "moderbot"], self._FORMY_0509[ch])[0]
                  for ch in self._PORYADOK_0509]
        self.assertEqual(routes.count(cc.ROUTE_CARD), 25)


# ────────── 7. «НЕТ» ВЛАДЕЛЬЦА: у карточки две двери (05.09.2026) ──────────
# Живой повод: владелец ответил на карточку ворот «нет» и не получил НИЧЕГО — разбор такого ответа
# не знал, записи не осталось, а молчание (которое работает как отказ) неотличимо от «не увидел
# карточку». Здесь стережём ровно три вещи: отказ ОСТАВЛЯЕТ СЛЕД, отказ НИЧЕГО НЕ ОТКРЫВАЕТ и отказ
# НЕ ВЕЧЕН.

class TestOtkazVladeltsa(unittest.TestCase):

    def setUp(self):
        d = tempfile.mkdtemp(prefix="cc_deny_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.rel = os.path.join(d, "client_release.json")
        self.tr = os.path.join(d, "trainer_green.json")
        self.flag = os.path.join(d, "pc_orchestrator.contour_frozen")
        self.seen = os.path.join(d, "gate_seen.json")

    def _route(self, kinds, held, commit, **kw):
        return cc.gate_route(kinds, held, flag=self.flag, path=self.seen, commit=commit,
                             release_path=self.rel, **kw)

    # ── след ──
    def test_otkaz_ostavlyaet_sled_ryadom_s_povodom(self):
        """Запись живёт в ТОМ ЖЕ реестре решений, что и «да»: одна развилка — одно место."""
        ok, rec = cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        self.assertTrue(ok)
        self.assertEqual(rec["commit"], "abc1234")
        self.assertEqual(rec["shape"], "userbot|suggest.py")
        self.assertEqual(cc.owner_denied("abc1234", path=self.rel)["who"], "owner")
        with open(self.rel, encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn("denied", d)
        self.assertEqual(cc.owner_denied("d14d450", path=self.rel), {}, "чужой коммит не закрыт")

    def test_povod_berjotsya_iz_zadannogo_voprosa(self):
        """Ответ «нет» приезжает отдельной задачей и формы в себе не несёт, а HEAD успевает уехать.
        Значит закрывается ПОВОД, о котором спросили, а не то, что случайно лежит в HEAD."""
        cc.remember_asked("abc1234", ["userbot", "moderbot"], ["suggest.py"], path=self.rel, now=50.0)
        ok, rec = cc.deny("f00dfee", path=self.rel, now=100.0)      # HEAD уже другой
        self.assertTrue(ok)
        self.assertEqual(rec["commit"], "abc1234", "закрыт повод, о котором спрашивали")
        self.assertEqual(rec["shape"], "moderbot,userbot|suggest.py")

    def test_ne_hesh_otkazom_ne_stanovitsya(self):
        ok, msg = cc.deny("не-коммит", path=self.rel)
        self.assertFalse(ok)
        self.assertIn("не похоже", msg)

    # ── ОТРИЦАТЕЛЬНЫЙ №1: отказ не открывает НИЧЕГО ──
    def test_posle_otkaza_primenenie_kommita_detyam_nevozmozhno(self):
        """Главный замок задания: «нет» — это расписка, а не рычаг. После него оснований как не
        было, так и нет; ворота держат тот же коммит, а `_client_block` возвращает клиентские файлы
        (то есть применять НЕЛЬЗЯ) ровно как до отказа."""
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        self.assertFalse(cc.owner_approved("abc1234", path=self.rel))
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))
        held = o._client_block(["userbot"], "abc1234", ["suggest.py"], "проба",
                               notifier=lambda *a, **k: None, cowork=lambda *a, **k: None,
                               state={}, client_fn=lambda p: list(p),
                               reason_fn=lambda c: cc.release_reason(c, path=self.rel,
                                                                     trainer_path=self.tr, env={}),
                               subject_fn=lambda c: "", trainer_fn=lambda c: "",
                               route_fn=lambda k, h, **kw: self._route(k, h, "abc1234"),
                               asked_fn=lambda *a, **k: (True, ""))
        self.assertEqual(held, ["suggest.py"], "после отказа файл всё так же НЕ применяем")

    def test_otkaz_ne_trogaet_reestr_verdikta(self):
        """Состояние ворот отказом не правится: файл вердикта тренажёра не создан и не изменён."""
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        self.assertFalse(os.path.exists(self.tr), "отказ создал файл вердикта")

    # ── повод закрыт, но не навсегда ──
    def test_po_etomu_povodu_bolshe_ne_sprashivaem(self):
        r0, _ = self._route(["userbot"], ["suggest.py"], "abc1234")
        self.assertEqual(r0, cc.ROUTE_CARD, "до отказа — карточка, как было")
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        r1, why = self._route(["userbot"], ["suggest.py"], "abc1234")
        self.assertEqual(r1, cc.ROUTE_FEED)
        self.assertIn("«нет» на этот повод", why)

    def test_otkaz_glushit_i_bez_zamorozki(self):
        """«Нет» — ответ на конкретный вопрос, и ручка заморозки к нему отношения не имеет."""
        self.assertFalse(cc.frozen(self.flag))
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        self.assertEqual(self._route(["userbot"], ["suggest.py"], "abc1234")[0], cc.ROUTE_FEED)
        self.assertFalse(os.path.exists(self.seen), "реестр форм заморозки тут ни при чём")

    def test_ta_zhe_forma_na_drugom_kommite_v_lentu(self):
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        r, why = self._route(["userbot"], ["suggest.py"], "d14d450", now=100.0 + 3600)
        self.assertEqual(r, cc.ROUTE_FEED)
        self.assertIn("ту же форму", why)

    def test_otkaz_ne_vechen_dlya_pary_deti_plus_faily(self):
        """ПРЕДСМЕРТНЫЙ ВЗГЛЯД задания: сделай отказ вечным для пары «дети + файлы» — и следующий,
        уже НУЖНЫЙ, вопрос владелец не увидит никогда. Через DENY_MUTE_SEC карточка возвращается."""
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        pozdno = 100.0 + cc.DENY_MUTE_SEC + 1
        self.assertEqual(self._route(["userbot"], ["suggest.py"], "d14d450", now=pozdno)[0],
                         cc.ROUTE_CARD, "через сутки та же форма на НОВОМ коммите снова громкая")

    def test_novaya_forma_sprashivaet_srazu(self):
        """«Новый коммит с новой формой спросить вправе» — и не ждёт никаких суток."""
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        self.assertEqual(self._route(["userbot"], ["suggest.py", "price_gate.py"], "d14d450",
                                     now=100.0 + 60)[0], cc.ROUTE_CARD)
        self.assertEqual(self._route(["userbot", "moderbot"], ["suggest.py"], "d14d450",
                                     now=100.0 + 60)[0], cc.ROUTE_CARD)

    def test_nechitaemyi_reestr_znachit_gromko(self):
        """FAIL-LOUD, как у реестра форм: не смогли прочитать решения → спрашиваем, а не молчим."""
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        with mock.patch.object(cc, "_load", return_value={}):
            self.assertEqual(self._route(["userbot"], ["suggest.py"], "abc1234")[0], cc.ROUTE_CARD)

    def test_peredumal_da_posle_neta_rabotaet(self):
        """Отказ закрывает повод, а не право владельца передумать: «выкати» после «нет» открывает."""
        cc.deny("abc1234", path=self.rel, now=100.0, kinds=["userbot"], held=["suggest.py"])
        cc.approve("abc1234", path=self.rel)
        self.assertEqual(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}),
                         "owner")
        self.assertTrue(cc.owner_denied("abc1234", path=self.rel), "история решения не стёрта")

    # ── карточка называет ОБА ответа ──
    def test_kartochka_nazyvaet_oba_otveta(self):
        txt = cc.card_text(["userbot"], "abc1234", ["suggest.py"], where="проба",
                           trainer_available=True, trainer_note="причина")
        self.assertIn("«выкати»", txt)
        self.assertIn(cc.DENY_WORD, txt, "дверь отказа обязана быть НАЗВАНА")
        self.assertIn("Отказ ничего не применяет", txt)


# ──────── 9. КАРТОЧКА ОТВЕЧАЕТСЯ ОТТУДА ГДЕ ПОКАЗАНА (05.09.2026, жалоба владельца) ────────
# Повод: «я в этой группе вообще ничего нажать не могу». Замер того же дня
# (_scratch_card_where_0905/probe_where.py): карточка уезжает в тему-инбокс (deliver →
# send_critical → 1160), слово ответа читается ТОЛЬКО из текста задачи очереди, а в самом тексте
# карточки не было НИ ОДНОГО слова про место ответа (9 признаков места из 9 — False).

class TestKartochkaNazyvaetAdresOtveta(unittest.TestCase):
    """Карточка обязана быть отвечаемой оттуда, где показана: кнопка — основной путь, слово с
    НАЗВАННЫМ адресом — запасной."""

    def _card(self, **kw):
        return cc.card_text(["userbot"], "abc1234", ["suggest.py"], where="проба",
                            trainer_available=True, trainer_note="причина", **kw)

    def test_adres_otveta_nazvan_chelovecheskimi_slovami(self):
        """ПОЗИТИВ: в карточке есть и кнопка, и запасной словесный адрес — оба человеческими
        словами, без имён функций и номеров тем в коде."""
        txt = self._card()
        self.assertIn("ГДЕ ОТВЕЧАТЬ", txt)
        self.assertIn("кнопку под этим сообщением", txt)
        self.assertIn("PC-дев", txt, "запасной адрес обязан быть назван")
        self.assertIn("«задача: выкати»", txt, "владельцу нужен ТОЧНЫЙ текст сообщения")
        self.assertIn("«задача: не выкатывай»", txt)

    def test_kartochka_chestno_govorit_chto_slovo_v_inbokse_ne_srabotaet(self):
        """Замер 05.09: голое слово в теме-инбоксе рычага не поднимает (devbot отвечает «это
        тема-инбокс подтверждений», слова «выкати» не знает вовсе). Карточка обязана это сказать,
        иначе владелец пишет слово туда, где его показали, и оно пропадает."""
        self.assertIn("НЕ", self._card())
        self.assertIn("инбокс", self._card())

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 1: карточка без адреса ответа НЕ СОБИРАЕТСЯ ВОВСЕ ──
    def test_bez_adresa_kartochka_ne_sobiraetsya(self):
        """Карточка, просящая ответа и молчащая о его месте, — худший вид шума. Поэтому пустой
        адрес не «карточка без адреса», а отказ собрать её: ValueError на ЛЮБОЙ пустоте."""
        for bad in ("", "   ", "\n", 0):
            with self.assertRaises(ValueError, msg=f"собралась без адреса на {bad!r}"):
                self._card(answer_at=bad)

    def test_none_eto_defolt_polosy_a_ne_pustota(self):
        """Замолчать адрес можно только ЯВНОЙ пустотой: None — «возьми дефолт», и он непуст."""
        self.assertIn("ГДЕ ОТВЕЧАТЬ", self._card(answer_at=None))
        self.assertTrue(cc.ANSWER_AT.strip() and cc.ANSWER_AT_WORDS.strip())

    def test_bez_knopok_kartochka_ne_obeschaet_knopku(self):
        """Доставка без кнопок (личка-фолбэк) → карточка называет ТОЛЬКО словесный путь. Обещать
        кнопку, которой владелец не увидит, — та же жалоба, что и была."""
        txt = self._card(buttons=False)
        self.assertIn("PC-дев", txt)
        self.assertNotIn("кнопку под этим сообщением", txt)

    def test_adres_ne_vytesnil_ni_odnogo_prezhnego_fakta(self):
        """Замок: новая строка ДОБАВЛЕНА, а не подменила собой карточку. Прежние факты на месте."""
        txt = self._card()
        for must in ("Коммит: abc1234", "suggest.py", "«выкати»", cc.DENY_WORD,
                     "git revert --no-edit abc1234", "SUGGEST_TEST_MODE"):
            self.assertIn(must, txt, f"карточка потеряла «{must}»")


class TestOtvetVorotamOdnimSlovom(unittest.TestCase):
    """`gate_word_exec` — точка входа КНОПКИ. Ворота ею не меняются: тот же разбор, те же слова,
    тот же исполнитель. Проверяем именно это, а не «кнопка что-то делает»."""

    def test_knopka_da_zovet_tot_zhe_rychag(self):
        seen = []
        ok, msg = o.gate_word_exec("выкати",
                                   exec_fn=lambda cmd, text="": (seen.append((cmd, text)),
                                                                 ("done", "OK"))[1])
        self.assertTrue(ok)
        self.assertEqual(seen, [("release_client", "выкати")])
        self.assertEqual(msg, "OK")

    def test_knopka_net_zovet_tot_zhe_rychag_otkaza(self):
        seen = []
        ok, _ = o.gate_word_exec(cc.DENY_WORD,
                                 exec_fn=lambda cmd, text="": (seen.append((cmd, text)),
                                                               ("done", "OK"))[1])
        self.assertTrue(ok)
        self.assertEqual(seen, [("deny_client", cc.DENY_WORD)])

    def test_nabor_slov_ne_rasshiren(self):
        """Замок задания: кнопка не заводит новых слов. Разбор — ЖИВОЙ `_match_command`."""
        for word in ("выкати", "не выкатывай", "нет", "отбой"):
            self.assertIn(o._match_command(word), o.GATE_WORD_CMDS, word)
        self.assertEqual(o.GATE_WORD_CMDS, ("release_client", "deny_client"))

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 2: ответ НЕ ИЗ ТОГО МЕСТА одобрением не становится и не теряется ──
    def test_chuzhoe_slovo_ne_odobrenie_i_ne_tishina(self):
        """Слово, не являющееся ответом воротам, НЕ исполняется (в т.ч. настоящий рычаг рестарта —
        он не воротный), и владельцу НАЗЫВАЕТСЯ адрес ответа. Молчание тут было бы тем же
        дефектом, который чиним."""
        for word in ("рестартни userbot", "статус контура", "не сейчас", "ага", "", "   "):
            called = []
            ok, msg = o.gate_word_exec(word, exec_fn=lambda *a, **k: called.append(a) or ("done", "!"))
            self.assertFalse(ok, f"«{word}» стало одобрением")
            self.assertEqual(called, [], f"«{word}» что-то исполнило")
            self.assertIn("ГДЕ ОТВЕЧАТЬ", msg, f"«{word}» потерялось молча")
            self.assertIn("PC-дев", msg)

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ 3: отказ НИЧЕГО не применяет и НИЧЕГО не откатывает ──
    def test_otkaz_nichego_ne_primenyaet_i_ne_otkatyvaet(self):
        """Кнопка «⛔ Не выкатывай» обязана вести себя ровно как слово: пишется СЛЕД, не трогается
        ни один процесс, ни один коммит, ни одно основание пропуска."""
        d = tempfile.mkdtemp(prefix="ccgw_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        rel = os.path.join(d, "release.json")
        restarts, approved = [], []
        real_deny = cc.deny                     # берём ДО патча: иначе лямбда звала бы сама себя
        with mock.patch.object(o, "_head_commit", lambda: "abc1234"), \
             mock.patch.object(o, "_cowork", lambda *a, **k: None), \
             mock.patch.object(o, "_restart_via_pc_agent",
                               lambda *a, **k: restarts.append(a) or (True, "x")), \
             mock.patch.object(cc, "approve", lambda *a, **k: approved.append(a) or (True, "x")), \
             mock.patch.object(cc, "deny", lambda c, **k: real_deny(c, path=rel, **k)):
            ok, msg = o.gate_word_exec(cc.DENY_WORD)
        self.assertTrue(ok)
        self.assertEqual(restarts, [], "отказ поднял процесс")
        self.assertEqual(approved, [], "отказ записал ОСНОВАНИЕ пропуска")
        self.assertIn("Ничего не применяю и не откатываю", msg)
        self.assertIsNone(cc.release_reason("abc1234", path=rel, trainer_path=rel + ".t", env={}),
                          "после отказа ворота открылись")


# ────────── 8. ПРАВДА ПРО ВЕРДИКТ: три состояния, а не два (05.09.2026) ──────────
# Живой замер 05.09: единственная зелёная запись ящика — на 74be777 (12 кейсов, corpus_sha
# 98ad5e3e…), а на диске корпус на 16 кейсов (6d5d78f0…). Вердикт не открывает даже сам 74be777,
# но карточка звала его «последним зелёным» — то есть верила файлу на слово ровно там, где сам
# вердикт файлу не верит.

class TestPravdaProVerdikt(unittest.TestCase):

    def setUp(self):
        d = tempfile.mkdtemp(prefix="cc_verd_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.tr = os.path.join(d, "trainer_green.json")
        self.cases = os.path.join(d, "trainer_cases.json")
        with open(self.cases, "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": i} for i in range(16)]}, f)

    def _put(self, key, **kw):
        rec = {"result": "green", "commit": key, "checks_passed": 224, "checks_total": 224,
               "cases": 12, "cases_total": 12, "runs": 2, "clean": True,
               "corpus_sha": cc.corpus_sha(self.cases), "when": "2026-09-04 12:00:00"}
        rec.update(kw)
        d = {}
        if os.path.exists(self.tr):
            with open(self.tr, encoding="utf-8") as f:
                d = json.load(f)
        d.setdefault("green", {})[key] = rec
        with open(self.tr, "w", encoding="utf-8") as f:
            json.dump(d, f)

    def _status(self, commit):
        return cc.trainer_status(commit, path=self.tr, env={}, cases_path=self.cases)

    # ── ОТРИЦАТЕЛЬНЫЙ №3 ──
    def test_verdikt_s_chuzhim_korpusom_zelenym_ne_zovetsya(self):
        """Запись есть, но снята на ДРУГОМ корпусе — значит не открывает НИ ОДНОГО коммита, и
        словом «зелёный» её называть нельзя ни на своём коммите, ни на чужом."""
        self._put("74be777", corpus_sha="98ad5e3e2c256542")
        self.assertFalse(cc.trainer_verdict("74be777", path=self.tr, env={}, cases_path=self.cases)[0])
        why = self._status("4aadeb0")
        self.assertIn("НИ НА ОДНОМ", why)
        self.assertIn("корпус кейсов не тот", why)
        self.assertNotIn("последний зелёный", why)
        self.assertNotIn("ДЕЙСТВУЮЩИЙ зелёный", why)

    def test_tri_sostoyaniya_razlichimy(self):
        """Требование задания дословно: вердикта нет вовсе · есть, но на другом коммите · есть и
        на этом. Раньше первые два состояния сливались в одну обнадёживающую строку."""
        self.assertIn("прогона не было", self._status("4aadeb0"))          # (1) нет вовсе
        self._put("74be777")                                                # (2) есть, но чужой
        why2 = self._status("4aadeb0")
        self.assertIn("ДЕЙСТВУЮЩИЙ зелёный — на 74be777", why2)
        self.assertIn("на этот коммит нет", why2)
        self._put("4aadeb0")                                                # (3) есть и на этом
        ok, why3 = cc.trainer_verdict("4aadeb0", path=self.tr, env={}, cases_path=self.cases)
        self.assertTrue(ok)
        self.assertIn("зелёный: кейсов", why3)

    def test_zhivoi_sluchai_05_09_kartochka_ne_obnadezhivaet(self):
        """Реплей живого случая: ящик держит 74be777 на 12-кейсовом корпусе, на диске — 16-кейсовый.
        Карточка обязана сказать правду «зелени нет нигде», а не «зелень есть, но не тут»."""
        self._put("74be777", corpus_sha="98ad5e3e2c256542")
        txt = cc.card_text(["userbot", "moderbot"], "4aadeb0", ["suggest.py"], where="проба",
                           trainer_available=True, trainer_note=self._status("4aadeb0"))
        self.assertIn("НИ НА ОДНОМ", txt)
        self.assertNotIn("последний зелёный — на 74be777", txt)

    def test_pravila_sravneniya_korpusa_ne_oslableny(self):
        """Замок задания: несовпадение корпуса и дальше означает «вердикта нет» — на своём коммите
        тоже, и ни одна новая ветка этого не смягчает."""
        for bad in ({"corpus_sha": "0" * 16}, {"runs": 1}, {"clean": False}, {"cases": 3},
                    {"checks_passed": 95}, {"result": "unknown"}):
            self._put("74be777", **bad)
            self.assertFalse(cc.trainer_verdict("74be777", path=self.tr, env={},
                                                cases_path=self.cases)[0], f"открылось на {bad}")
            self.assertNotIn("ДЕЙСТВУЮЩИЙ зелёный", self._status("4aadeb0"), f"назвали зелёным: {bad}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
