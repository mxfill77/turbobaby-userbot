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
        for name in ("pc_orchestrator.py", "pc_agent.py", "gate_selective.py", "task_metrics.py",
                     "client_contour.py", "selfupdate_gate.py", "pretool_guard.py",
                     "cowork_log_append.py", "brain_writer.py", "README.md"):
            self.assertFalse(cc.is_client(name, REPO), f"{name} обязан быть внутренним")

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


# ─────────────────────────── 2. ОСНОВАНИЯ ПРОПУСКА ───────────────────────────

class TestOsnovaniya(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cc_rel_")
        self.rel = os.path.join(self.d, "release.json")
        self.tr = os.path.join(self.d, "trainer.json")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

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

    def test_trener_vyklyuchen_po_umolchaniyu(self):
        with open(self.tr, "w", encoding="utf-8") as f:
            json.dump({"green": {"abc1234": {"ok": True}}}, f)
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr, env={}))

    def test_trener_zelenyi_pri_vklyuchennom_flage(self):
        with open(self.tr, "w", encoding="utf-8") as f:
            json.dump({"green": {"abc1234": {"ok": True}}}, f)
        self.assertEqual(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr,
                                           env={cc.TRAINER_GREEN_ENV: "1"}), "trainer")

    def test_trener_flag_est_no_progona_net(self):
        self.assertIsNone(cc.release_reason("abc1234", path=self.rel, trainer_path=self.tr,
                                            env={cc.TRAINER_GREEN_ENV: "1"}))

    def test_ne_hesh_ne_osnovanie(self):
        ok, msg = cc.approve("не-коммит")
        self.assertFalse(ok)
        self.assertIn("не похоже", msg)


# ─────────────────────────── 3. ВОРОТА ВЫХОДА ───────────────────────────

class GateBase(unittest.TestCase):
    """Общая обвязка: боевые _notify/_cowork/reason замоканы, живое НЕ дёргается."""

    def setUp(self):
        self.cards, self.cowork, self.restarts = [], [], []
        o._CLIENT_HELD_WARNED.clear()
        self.p_notify = mock.patch.object(o, "_notify", lambda t: self.cards.append(t))
        self.p_cowork = mock.patch.object(o, "_cowork", lambda t: self.cowork.append(t))
        self.p_subj = mock.patch.object(o, "_commit_subject", lambda c: "тема коммита")
        self.p_reason = mock.patch.object(cc, "release_reason", lambda *a, **k: None)
        for p in (self.p_notify, self.p_cowork, self.p_subj, self.p_reason):
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

    def test_vnutrennii_kommit_primenyaetsya_kak_ranshe(self):
        """Контроль «не задели вторую половину»: не-клиентский рантайм-файл едет сам.
        pc_agent.py карта ведёт на процесс pc_agent — рестарт бота не назначается, но и ворот нет."""
        res = o.maybe_update_bots(1, "тз: правка агента", "old",
                                  changed_fn=lambda h: ["pc_agent.py"],
                                  gate_fn=self.gate_green, restart_fn=self.restart,
                                  head_fn=lambda: "aaaa111", dirty_fn=lambda: [])
        self.assertEqual(self.cards, [])
        self.assertEqual(res, "")               # карта на ботов не указывает → прежний путь байт-в-байт

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
        out = o._selfupdate_restart_children("old", "new777",
                                             diff_fn=lambda a, b: ["suggest.py", "pc_orchestrator.py"],
                                             restart_fn=self.restart)
        self.assertEqual(self.restarts, [])
        self.assertIn("ОСТАНОВЛЕНО воротами", out)
        self.assertEqual(len(self.cards), 1)

    def test_selfupdate_vnutrennii_diapazon_edet(self):
        """В диапазоне только внутреннее, но карта ведёт на userbot → применяем как раньше."""
        with mock.patch.object(o, "_dirty_block", lambda *a, **k: []):
            out = o._selfupdate_restart_children("old", "new777",
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

    def test_rekonsilyaciya_vnutrennego_edet_kak_ranshe(self):
        """ВНУТРЕННИЙ контур не задет: pc_agent.py в диффе → прежняя пометка, ворота молчат."""
        o._last_child_commit = "aaaaaaaaaaaa"
        o._child_reconcile_rejected = None
        out = o.reconcile_children_tick(head_fn=lambda: "bbbbbbbbbbbb",
                                        diff_fn=lambda a, b: ["pc_agent.py"],
                                        gate_fn=self.gate_green, restart_fn=self.restart)
        self.assertIn("ЖДЁТ РУЧНОГО рестарта", out)
        self.assertEqual(o._last_child_commit, "bbbbbbbbbbbb")   # метка ушла вперёд — прежний путь
        self.assertEqual([c for c in self.cards if "ворота" in c.lower()], [])
        self.assertIn("ЖДЁТ РУЧНОГО рестарта", self.cards[0])    # штатная старая пометка на месте

    def test_kartochka_ne_povtoryaetsya_kazhdyi_tik(self):
        """Реконсиляция приходит каждые 60 с — карточка обязана уйти РОВНО один раз на коммит+состав."""
        for _ in range(3):
            o._last_child_commit = "aaaaaaaaaaaa"
            o._child_reconcile_rejected = None
            o.reconcile_children_tick(head_fn=lambda: "4528917456789",
                                      diff_fn=lambda a, b: ["suggest.py"],
                                      gate_fn=self.gate_green, restart_fn=self.restart)
        self.assertEqual(len(self.cards), 1)

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
            enq, skip = o._revizor_enqueue_tasks(
                [{"class": "б", "task_text": "поправь detectVehicleType в suggest.py", "client_id": 1}],
                [], 0)
        self.assertEqual((enq, skip), (0, 1))
        self.assertEqual(calls, [])

    def test_enqueue_vnutrennyuyu_zadachu_stavit_kak_ranshe(self):
        calls = []
        with mock.patch.object(o, "enqueue_pc_task", lambda t, frm=None: (calls.append(t), (True, 7, None))[1]):
            enq, skip = o._revizor_enqueue_tasks(
                [{"class": "ж", "task_text": "в pc_orchestrator.py почини троттлинг тика", "client_id": 1}],
                [], 0)
        self.assertEqual((enq, skip), (1, 0))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
