# -*- coding: utf-8 -*-
"""
test_pc_link_state.py — ГОЛДЕНЫ «СВЯЗИ НЕТ» ≠ «Я СЛОМАЛСЯ» ≠ «ПК СПАЛ» (класс 05.08.2026).

ЖИВОЕ ОКНО, НА КОТОРОМ СТОИТ ВЕСЬ ФАЙЛ — обрыв сети ПК 04.08.2026 18:02:55 → 19:18:11 UTC
(75 м 16 с). Разборы: docs/artifacts/2026-08-05-three-states-one-root.md,
docs/artifacts/2026-08-05-pc-lane-284-outage-rc.md. Пять независимых свидетелей, дословно:

    мост     : «get_pending(in_progress) ошибка: TimeoutError» … «ошибка: URLError» — 37 отказов
    GitHub   : «fatal: unable to access '…': Failed to connect to github.com port 443 after
                21115 ms: Could not connect to server»
    Telegram : «NetworkError: httpx.ConnectError: [Errno 11001] getaddrinfo failed»
    API      : «claude exit=1: API Error: Unable to connect to API (ConnectionRefused)»
    userbot  : «Attempt N at connecting failed: TimeoutError» на 91.108.56.105:443

Что система сказала вместо «связи нет» (это и есть красное, которое файл держит закрытым):
  • задаче 284 — `FAIL причина=exec_error`, «ошибка выполнения: claude exit=1», то есть ВНЕШНИЙ
    обрыв записан СОБСТВЕННОЙ поломкой; штаб дважды построил на этом следующий шаг;
  • витку — «детект пробуждения ПК» 18 раз подряд, и у всех 18 стоит собственное «из них сна 0с»;
  • за сутки таких строк 305, из них 303 (99,3 %) с пометкой «сна 0с», карточек «🛌 ПК СПАЛ» — 0.

ПРАВИЛО-КЛАСС «мок обязан копировать живой формат» соблюдено буквально: ни одной придуманной
формулировки отказа здесь нет — все строки ниже сняты из pc_orchestrator.log и артефактов.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_pc_link_state -v
"""

import os
import socket
import tempfile
import unittest
import urllib.error
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"   # боевой .env-рубильник не течёт в тесты (как в соседних файлах)

import bridge_http                     # noqa: E402
import pc_orchestrator as o            # noqa: E402


# ── ЖИВЫЕ СТРОКИ (дословно из лога/артефактов 04–05.08) ──────────────────────────────────────
LIVE_API_284 = "API Error: Unable to connect to API (ConnectionRefused)."
LIVE_GIT = ("fatal: unable to access 'https://github.com/x/y.git/': Failed to connect to "
            "github.com port 443 after 21115 ms: Could not connect to server")
LIVE_TG = "NetworkError: httpx.ConnectError: [Errno 11001] getaddrinfo failed"
LIVE_GAP_OUTAGE = 186        # «скачок wall-clock 186с (>60+60; из них сна 0с)» — 05.08 02:16:23
LIVE_GAP_TYPICAL = 165       # p50 скачка за сутки: 297 из 305 таких — ≤300 с
JUMP_141_144 = 3988          # три ложняка 01.08: все три — РАБОТА в витке, а не сон
JUMP_158 = 3103
JUMP_160 = 2845
SLEEP_3007 = 32381           # настоящий сон 30.07 (8 ч 59 м)
OUTAGE_SEC = 4516            # 75 м 16 с — измеренная длина обрыва 04.08


def _urlerror():
    """Живой отказ моста внутри окна: до Apps Script не дошли вовсе (DNS/сокет)."""
    return urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))


def _httperror(code=500):
    return urllib.error.HTTPError("https://script.google.com/macros/s/DEPLOY_ID/exec", code,
                                  "Internal Server Error", None, None)


class LinkBase(unittest.TestCase):
    """Общая гигиена: состояние связи — модульный синглтон, и тест обязан входить в него чистым
    И выходить чистым (иначе свидетельство одного теста судит задачу другого). Боевых файлов
    состояния не касаемся вовсе: всё временное — в свой каталог с уникальным суффиксом."""

    def setUp(self):
        o._link.reset()
        self.addCleanup(o._link.reset)
        self.tmp = tempfile.mkdtemp(prefix="pc_link_state_")
        self.log = mock.Mock()


class TestErrorKindsAreLiveForms(LinkBase):
    """«Дошли ли мы до моста ВООБЩЕ» — вопрос, которого у системы не было: 89 голых имён
    исключений в логе и ноль слов «сеть» за 14 суток."""

    def test_urlerror_i_timeout_eto_svyazi_net(self):
        for exc in (_urlerror(), TimeoutError("timed out"), ConnectionRefusedError(61, "refused"),
                    socket.timeout("timed out")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(bridge_http.error_kind(exc), bridge_http.KIND_NETWORK)
                self.assertIn("СВЯЗИ НЕТ", bridge_http.explain(exc))

    def test_http_otvet_eto_svyaz_est_i_kod_ne_teryaetsya(self):
        """401 и 500 приезжали в ОДНО поле в одной форме — «HTTPError», код терялся."""
        for code in (401, 429, 500):
            with self.subTest(code=code):
                exc = _httperror(code)
                self.assertEqual(bridge_http.error_kind(exc), bridge_http.KIND_HTTP)
                txt = bridge_http.explain(exc)
                self.assertIn(str(code), txt)
                self.assertIn("связь есть", txt)

    def test_bridge_transport_error_ne_obryv_seti(self):
        """ЖИВОЕ ЧИСЛО: `BridgeTransportError` в логе 354 раза за 02–05.08 при полностью живой
        сети (включая сегодняшние строки). Считать их обрывом = 354 ложных «связи нет»."""
        for exc in (bridge_http.BridgeTransportError("второе плечо моста"),
                    bridge_http.BridgeReceiptLost("на POST ответил doGet")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(bridge_http.error_kind(exc), bridge_http.KIND_BRIDGE)
                self.assertIn("связь есть", bridge_http.explain(exc))
                self.assertIs(o.link_ok_by_kind(bridge_http.error_kind(exc)), True)

    def test_neopoznannoe_ne_svidetelstvo(self):
        """«Не измерил» не смеет выдавать себя ни за «связь есть», ни за «связи нет»."""
        self.assertEqual(bridge_http.error_kind(ValueError("мусор")), bridge_http.KIND_OTHER)
        self.assertIsNone(o.link_ok_by_kind(bridge_http.KIND_OTHER))
        self.assertNotIn("unknown", bridge_http.KIND_OTHER)   # имя гарда не занимаем (класс ПК)

    def test_adres_deploya_v_razbore_ne_pechataetsya(self):
        """Текст уходит в лог, спул и журнал, а полный /exec несёт id деплоя."""
        self.assertNotIn("DEPLOY_ID", bridge_http.explain(_httperror(500)))
        self.assertNotIn("script.google.com", bridge_http.explain(_httperror(500)))


class TestLinkWatchVerdict(LinkBase):
    """Вердикт по бесплатным свидетелям: 0 новых запросов, 0 мс."""

    def test_odin_kanal_odin_otkaz_eto_ne_verdikt(self):
        w = o.LinkWatch()
        self.assertIsNone(w.witness(o.LINK_CH_BRIDGE, False, now=100))
        self.assertEqual(w.verdict(now=100)[0], o.LINK_OK)

    def test_odin_kanal_podryad_eto_svyaz_ne_podtverzhdena(self):
        w = o.LinkWatch()
        w.witness(o.LINK_CH_BRIDGE, False, now=100)
        w.witness(o.LINK_CH_BRIDGE, False, now=160)
        name, chans, since = w.verdict(now=160)
        self.assertEqual((name, chans, since), (o.LINK_UNSURE, [o.LINK_CH_BRIDGE], 100))

    def test_dva_kanala_eto_svyazi_net(self):
        """Живое совпадение 04.08: мост молчит, git fetch не доехал до GitHub."""
        w = o.LinkWatch()
        w.witness(o.LINK_CH_BRIDGE, False, detail="TimeoutError", now=100)
        self.assertEqual(w.witness(o.LINK_CH_GIT, False, detail=LIVE_GIT, now=140), "lost")
        self.assertEqual(w.verdict(now=140)[0], o.LINK_LOST)
        self.assertTrue(w.lost(now=140))

    def test_podtverzhdenie_lyubogo_kanala_gasit_verdikt(self):
        """Мы доказанно в сети → молчание прочих объясняется СЕРВИСОМ, а не связью."""
        w = o.LinkWatch()
        w.witness(o.LINK_CH_BRIDGE, False, now=100)
        w.witness(o.LINK_CH_GIT, False, now=140)
        self.assertEqual(w.witness(o.LINK_CH_GIT, True, now=200), "back")
        self.assertEqual(w.verdict(now=200)[0], o.LINK_OK)
        self.assertEqual(w.outages, [(100, 200)])

    def test_okno_obryva_schitaetsya_dlya_vozrasta_zadach(self):
        w = o.LinkWatch()
        w.witness(o.LINK_CH_BRIDGE, False, now=1000)
        w.witness(o.LINK_CH_API, False, now=1000)
        w.witness(o.LINK_CH_BRIDGE, True, now=1000 + OUTAGE_SEC)
        self.assertEqual(int(w.outage_seconds(0, 99999, now=99999)), OUTAGE_SEC)
        self.assertEqual(int(w.outage_seconds(1000 + 16, 1000 + 116, now=99999)), 100)  # пересечение
        self.assertEqual(int(w.outage_seconds(0, 999, now=99999)), 0)                   # мимо окна
        # ОТКРЫТОЕ окно (связь ещё не вернулась) тоже считается — иначе реапер судил бы полосу
        # ровно в тот момент, когда она непроверяема.
        w2 = o.LinkWatch()
        w2.witness(o.LINK_CH_BRIDGE, False, now=10)
        w2.witness(o.LINK_CH_API, False, now=10)
        self.assertEqual(int(w2.outage_seconds(0, 610, now=610)), 600)

    def test_protuhshee_svidetelstvo_ne_verdikt(self):
        """Старше LINK_STALE_SEC — не свидетельство: мир мог измениться, а мы не мерили."""
        w = o.LinkWatch()
        w.witness(o.LINK_CH_BRIDGE, False, now=100)
        w.witness(o.LINK_CH_BRIDGE, False, now=110)
        self.assertEqual(w.verdict(now=110)[0], o.LINK_UNSURE)
        self.assertEqual(w.verdict(now=110 + o.LINK_STALE_SEC + 1)[0], o.LINK_OK)

    def test_354_zhivyh_bridge_transport_ne_dayut_ni_odnogo_obryva(self):
        w = o.LinkWatch()
        for i in range(354):
            w.witness(o.LINK_CH_BRIDGE,
                      o.link_ok_by_kind(bridge_http.error_kind(
                          bridge_http.BridgeTransportError("второе плечо"))), now=100 + i)
        self.assertEqual(w.verdict(now=500)[0], o.LINK_OK)
        self.assertEqual(w.outages, [])

    def test_stroka_vladeltsu_odna_na_smenu_sostoyaniya(self):
        """Не на каждый отказ (их 37 за окно), а на переход: «связи нет» и «связь вернулась»."""
        w, journal = o.LinkWatch(), []
        o.net_witness(o.LINK_CH_BRIDGE, False, now=100, watch=w, logger=self.log)
        o.net_witness(o.LINK_CH_API, False, now=100, watch=w, logger=self.log,
                      detail=LIVE_API_284)
        for i in range(35):                       # ещё 35 отказов внутри того же окна
            o.net_witness(o.LINK_CH_BRIDGE, False, now=200 + i, watch=w, logger=self.log)
        self.assertEqual(self.log.error.call_count, 1, "об обрыве говорим ОДИН раз")
        o.net_witness(o.LINK_CH_BRIDGE, True, now=100 + OUTAGE_SEC, watch=w, logger=self.log,
                      journal=journal.append)
        self.assertEqual(self.log.warning.call_count, 1)
        self.assertEqual(len(journal), 1, "в журнал — одна строка, и только про длинное окно")
        self.assertIn("связи не было", journal[0])
        self.assertLessEqual(len(journal[0]), o.RESULT_MAX)

    def test_korotkiy_blip_zhurnal_ne_trogaet(self):
        w, journal = o.LinkWatch(), []
        o.net_witness(o.LINK_CH_BRIDGE, False, now=100, watch=w, logger=self.log)
        o.net_witness(o.LINK_CH_GIT, False, now=100, watch=w, logger=self.log)
        o.net_witness(o.LINK_CH_GIT, True, now=100 + o.LINK_JOURNAL_SEC - 1, watch=w,
                      logger=self.log, journal=journal.append)
        self.assertEqual(journal, [], "мелкий блип в индекс не пишем")


class TestBridgeClientFeedsWitness(LinkBase):
    """Проводка: каждый вызов моста — уже свидетель, новых запросов ноль."""

    def _bridge(self, exc):
        seen = []

        class Op:
            def open(self, req, timeout=None):
                raise exc

        return o.Bridge(url="https://x/exec", token="t", opener=Op(),
                        witness=lambda ch, ok, detail="": seen.append((ch, ok))), seen

    def test_setevoy_otkaz_daet_svidetelstvo_i_razbor(self):
        b, seen = self._bridge(_urlerror())
        r = b.get_pending("new")
        self.assertEqual(r["ok"], False)
        self.assertEqual(r["error"], "URLError", "имя класса в контракте осталось дословно")
        self.assertEqual(r["error_kind"], bridge_http.KIND_NETWORK)
        self.assertIn("getaddrinfo", r["error_text"])
        self.assertIsInstance(r["ms"], int)                  # латентность моста не мерилась вовсе
        self.assertEqual(seen, [(o.LINK_CH_BRIDGE, False)])

    def test_otvet_mosta_daet_podtverzhdenie_svyazi(self):
        b, seen = self._bridge(_httperror(500))
        self.assertEqual(b.get_pending("new")["error_kind"], bridge_http.KIND_HTTP)
        self.assertEqual(seen, [(o.LINK_CH_BRIDGE, True)])


class TestTaskFailureIsNamedByMeasuredCause(LinkBase):
    """284: внешний обрыв записан собственной поломкой. Здесь он называется своим именем."""

    def setUp(self):
        super().setUp()
        self._save = (o.run_claude, o._cowork, o._notify, o._claude_budget_gate, o._work_evidence,
                      o.TASK_START_FILE, o.COWORK_LEDGER, o._selfheal_on)
        o._cowork = lambda *a, **k: None
        o._notify = lambda *a, **k: None
        o._selfheal_on = lambda: False
        o._claude_budget_gate = lambda *a, **k: (True, "тест: бюджет пропущен")
        o._work_evidence = lambda since, until=None: {"commits": [], "journal": []}
        o.TASK_START_FILE = os.path.join(self.tmp, "task_started.json")
        o.COWORK_LEDGER = os.path.join(self.tmp, "cowork_log.ledger")
        self.addCleanup(lambda: (setattr(o, "run_claude", self._save[0]),
                                 setattr(o, "_cowork", self._save[1]),
                                 setattr(o, "_notify", self._save[2]),
                                 setattr(o, "_claude_budget_gate", self._save[3]),
                                 setattr(o, "_work_evidence", self._save[4]),
                                 setattr(o, "TASK_START_FILE", self._save[5]),
                                 setattr(o, "COWORK_LEDGER", self._save[6]),
                                 setattr(o, "_selfheal_on", self._save[7])))

    def _run(self, rc=1, out="", err="", tid=90284):
        calls = []

        def fake(prompt, timeout, cwd, env):
            calls.append(1)
            return (rc, out, err)

        o.run_claude = fake
        with mock.patch.object(o, "resolve_claude", lambda: r"C:\x\claude.exe"):
            status, result = o._run_task_impl(tid, "ultrathink\nсделай X")
        return status, result, len(calls)

    def test_zhivoy_proval_284_nazvan_svyazi_net(self):
        """ДОСЛОВНАЯ фикстура 04.08. Было: «[причина=exec_error · ошибка выполнения]»."""
        status, res, _ = self._run(rc=1, out=LIVE_API_284)
        self.assertEqual(status, "failed")
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_NETWORK_OUTAGE)
        self.assertIn("связи нет", res)
        self.assertNotIn(o.FAIL_EXEC_ERROR, res)
        self.assertTrue(res.startswith(o.NET_MARK), "маркер причины — ПЕРВЫМ символом")
        self.assertIn("Unable to connect to API", res, "дословный ответ ребёнка сохранён")

    def test_nastoyaschaya_oshibka_ispolneniya_nazyvaetsya_kak_prezhde(self):
        """Вторая половина регресса: своя поломка обязана остаться своей поломкой."""
        status, res, _ = self._run(rc=1, out="", err="Traceback: TypeError: NoneType is not callable")
        self.assertEqual(status, "failed")
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_EXEC_ERROR)
        self.assertIn("ошибка выполнения", res)
        self.assertNotIn(o.NET_MARK, res)

    def test_insufficient_output_ostalsya_exec_error(self):
        status, res, _ = self._run(rc=0, out="я подумал и ничего не вывел")
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_EXEC_ERROR)

    def test_uspeh_ne_zadet(self):
        status, res, _ = self._run(rc=0, out="сделал\nRESULT: сделал")
        self.assertEqual((status, "сделал" in res), ("done", True))

    def test_izmerennyy_obryv_polosy_nazyvaet_dazhe_nemogo_rebenka(self):
        """Ребёнок молчит о причине, но полоса измерена молчащей ДВУМЯ каналами."""
        o._link.witness(o.LINK_CH_BRIDGE, False, detail="TimeoutError")
        o._link.witness(o.LINK_CH_GIT, False, detail=LIVE_GIT)
        status, res, _ = self._run(rc=1, out="", err="exit code 1")
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_NETWORK_OUTAGE)
        self.assertIn("полоса измерена молчащей", res)

    def test_pustoy_vyvod_pri_obryve_ne_zhzhet_vtoruyu_popytku(self):
        """Авто-повтор заведён под транзиент; в молчащей сети он повторит тот же провал."""
        status, res, n = self._run(rc=0, out="", err=LIVE_TG)
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_NETWORK_OUTAGE)
        self.assertEqual(n, 1, "вторая попытка не потрачена")

    def test_pustoy_vyvod_bez_obryva_povtoryaetsya_kak_prezhde(self):
        status, res, n = self._run(rc=0, out="", err="(пуст)")
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_EXEC_ERROR)
        self.assertEqual(n, 2, "прежний авто-повтор цел")

    def test_samopochinka_obryv_ne_chinit(self):
        """04.08 думатель самопочинки сам упал в тот же обрыв («timed out after 180 seconds»)."""
        o._selfheal_on = lambda: True
        res = o.fail_result(o.FAIL_NETWORK_OUTAGE, "связи нет")
        self.assertTrue(res.lstrip().startswith(o.NO_HEAL_PREFIXES))
        boom = mock.Mock(side_effect=AssertionError("думатель не должен зваться"))
        with mock.patch.object(o, "_maybe_task_selfheal", boom):
            self.assertFalse(o._maybe_selfheal(90284, "задача", res, frm="Filipp"))
        boom.assert_not_called()

    def test_kartochka_vladeltsu_ne_govorit_provalena(self):
        res = o.fail_result(o.FAIL_NETWORK_OUTAGE, "связи нет")
        card = o._human("failed", 284, res)
        self.assertIn("СВЯЗИ НЕ БЫЛО", card)
        self.assertNotIn("провалена", card)
        self.assertIn("ни при чём", card)

    def test_rabota_v_okne_sohranyaetsya_kak_est(self):
        """«Работа в окне сохраняется как есть»: улики собираются тем же перечнем, что у прочих
        провалов, — обрыв не отменяет коммитов, которые задача успела сделать."""
        with mock.patch.object(o, "_work_evidence", lambda since, until=None: {
                "commits": [("a3f75dd", "правка X")], "journal": []}):
            res = o.fail_result(o.FAIL_NETWORK_OUTAGE, "связи нет",
                                since=o.datetime.datetime.now(o.datetime.timezone.utc))
        self.assertIn(o.WORK_DONE_MARK, res)
        self.assertIn("a3f75dd", res)

    def test_63c_long_stderr_in_the_result_says_how_much_was_cut(self):
        """Путь показа (63-c): рез stderr в итоге объявляет себя числом; решение судит полный поток."""
        status, res, _ = self._run(rc=1, out="", err="предупреждение\n" + "w" * 700)
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_EXEC_ERROR)
        self.assertIn("[показан хвост 500 из 715 симв.]", res)

    def test_63c_network_line_after_a_long_stderr_is_still_network(self):
        status, res, _ = self._run(rc=1, out="", err="w" * 900 + "\n" + LIVE_TG)
        self.assertEqual(o.FAIL_CODE_RE.search(res).group(1), o.FAIL_NETWORK_OUTAGE)


class TestGapIsNamedByMeasuredCause(LinkBase):
    """305 строк «детект пробуждения ПК» за сутки, 303 с собственной пометкой «сна 0с»."""

    def _report(self, gap, slept, link_lost=False, worked=False):
        name, num = o.loop_gap_report(gap, slept, link_lost=link_lost, worked=worked,
                                      logger=self.log)
        # Строку собираем КАК В ЛОГЕ (шаблон + аргументы): проверять надо то, что прочитает
        # владелец, а не формат — иначе голден зелен на «%s» вместо числа.
        said = " | ".join((str(c.args[0]) % tuple(c.args[1:])) if len(c.args) > 1 else str(c.args[0])
                          for c in (self.log.warning.call_args_list + self.log.info.call_args_list))
        return name, num, said

    def test_obryv_04_08_nazyvaetsya_svyazyu_a_ne_probuzhdeniem(self):
        """18 живых строк окна: «скачок 186с … из них сна 0с» → было «пробуждение ПК»."""
        name, num, said = self._report(LIVE_GAP_OUTAGE, slept=0, link_lost=True)
        self.assertEqual(name, o.GAP_LINK)
        self.assertIn("СВЯЗИ НЕТ", said)
        self.assertNotIn("пробуждени", said)
        self.assertEqual(num, LIVE_GAP_OUTAGE)

    def test_bez_sna_i_bez_obryva_detektor_molchit(self):
        """297 строк из 305 за сутки — ровно этот случай (скачок ≤300 с, сна 0 с)."""
        name, _num, said = self._report(LIVE_GAP_TYPICAL, slept=0)
        self.assertEqual(name, o.GAP_STALL)
        self.assertEqual(said, "", "сна не было — детектор молчит")
        self.assertEqual(self.log.warning.call_count, 0)

    def test_tri_zhivyh_lozhnyaka_0108_eto_rabota_a_ne_son(self):
        for jump in (JUMP_141_144, JUMP_158, JUMP_160):
            with self.subTest(jump=jump):
                self.log.reset_mock()
                name, _num, said = self._report(jump, slept=0, worked=True)
                self.assertEqual(name, o.GAP_WORK)
                self.assertNotIn("пробуждени", said)
                self.assertEqual(self.log.warning.call_count, 0)

    def test_nastoyaschiy_son_zovetsya_snom_i_govorit_gromko(self):
        name, num, said = self._report(SLEEP_3007 + 140, slept=SLEEP_3007)
        self.assertEqual((name, num), (o.GAP_SLEEP, SLEEP_3007))
        self.assertIn("детект пробуждения ПК", said)

    def test_son_koroche_izmerennogo_pola_snom_ne_zovetsya(self):
        """Две строки из 305 несли «сна 1с/2с» — суточная поправка службы времени, не сон."""
        for slept in (0, 1, 2):
            with self.subTest(slept=slept):
                self.assertEqual(o.diagnose_gap(LIVE_GAP_TYPICAL, slept)[0], o.GAP_STALL)
        self.assertEqual(o.diagnose_gap(LIVE_GAP_TYPICAL, o.SLEEP_FLOOR_SEC + 1)[0], o.GAP_SLEEP)

    def test_bez_chasov_bodrstvovaniya_povedenie_prezhnee(self):
        """slept=None: сон НЕ ИЗМЕРЕН → прежний разбор по скачку (fail-open, сигнал не теряем)."""
        name, _num, said = self._report(LIVE_GAP_TYPICAL, slept=None)
        self.assertEqual(name, o.GAP_SLEEP)
        self.assertIn("замерить нечем", said)

    def test_bez_chasov_no_s_izmerennym_obryvom_pravda_u_obryva(self):
        self.assertEqual(o.diagnose_gap(LIVE_GAP_OUTAGE, None, link_lost=True)[0], o.GAP_LINK)
        # длинный скачок при неизмеренном сне — прежний громкий путь остаётся
        self.assertEqual(o.diagnose_gap(SLEEP_3007, None, link_lost=True)[0], o.GAP_SLEEP)

    def test_gromkiy_signal_i_grace_tolko_u_sna_i_obryva(self):
        """Смысловой инвариант правки: grace вотчдога взводится ровно на двух исходах из четырёх.
        Прежнее «на любой скачок» стоило 71 подавленного тика в сутки ≈ 5,9 ч надзора."""
        armed = {o.GAP_SLEEP, o.GAP_LINK}
        self.assertNotIn(o.GAP_STALL, armed)
        self.assertNotIn(o.GAP_WORK, armed)
        src = o.__loader__.get_source("pc_orchestrator")
        self.assertIn("if name in (GAP_SLEEP, GAP_LINK):", src)
        self.assertIn("if name == GAP_SLEEP:", src)


class FakeBridgeSingles:
    """Минимальный мост: только то, что читает реапер одиночек."""

    def __init__(self, items):
        self.items = items
        self.completed = []
        self.reads = 0

    def get_pending(self, status, lane="pc"):
        self.reads += 1
        return {"ok": True, "items": [dict(i) for i in self.items]}

    def complete_task(self, tid, status, result):
        self.completed.append((tid, status, result))
        return {"ok": True}


class TestStuckSingleNotJudgedWhileLinkWasDown(LinkBase):
    """«Задача, умершая от обрыва связи, не объявляется зависшей»."""

    def setUp(self):
        super().setUp()
        self._save = (o.bc, o._cowork, o._notify_task, o._stopped, o._work_evidence,
                      o.TASK_START_FILE, o.COWORK_LEDGER, o._lesson_wait_ids)
        o._cowork = lambda *a, **k: None
        o._notify_task = lambda *a, **k: None
        o._stopped = lambda: False
        o._lesson_wait_ids = lambda *a, **k: set()
        o._work_evidence = lambda since, until=None: {"commits": [], "journal": []}
        o.TASK_START_FILE = os.path.join(self.tmp, "task_started.json")
        o.COWORK_LEDGER = os.path.join(self.tmp, "cowork_log.ledger")
        self.addCleanup(lambda: (setattr(o, "bc", self._save[0]),
                                 setattr(o, "_cowork", self._save[1]),
                                 setattr(o, "_notify_task", self._save[2]),
                                 setattr(o, "_stopped", self._save[3]),
                                 setattr(o, "_work_evidence", self._save[4]),
                                 setattr(o, "TASK_START_FILE", self._save[5]),
                                 setattr(o, "COWORK_LEDGER", self._save[6]),
                                 setattr(o, "_lesson_wait_ids", self._save[7])))

    def _reap(self, age_sec):
        now = o.datetime.datetime.now(o.datetime.timezone.utc)
        upd = (now - o.datetime.timedelta(seconds=age_sec)).isoformat()
        fb = FakeBridgeSingles([{"id": 284, "lane": "pc", "status": "in_progress", "updated": upd}])
        o.bc = fb
        o.process_stuck_singles(now=now)
        return fb

    def test_bez_obryva_reaper_rabotaet_kak_prezhde(self):
        fb = self._reap(o.PC_SINGLE_STALE + 600)
        self.assertEqual([c[1] for c in fb.completed], ["failed"])
        self.assertEqual(o.FAIL_CODE_RE.search(fb.completed[0][2]).group(1),
                         o.FAIL_HEARTBEAT_TIMEOUT)

    def test_vozrast_ne_rastet_poka_svyazi_net(self):
        """Живая длина обрыва 04.08 — 4516 с. Задача, простоявшая аварию, не зависла."""
        now_ts = o.time.time()
        age = o.PC_SINGLE_STALE + 600
        o._link.witness(o.LINK_CH_BRIDGE, False, now=now_ts - age + 60)
        o._link.witness(o.LINK_CH_API, False, now=now_ts - age + 60)
        o._link.witness(o.LINK_CH_BRIDGE, True, now=now_ts - age + 60 + OUTAGE_SEC)
        fb = self._reap(age)
        self.assertEqual(fb.completed, [], "чужой простой не делает задачу зависшей")

    def test_pri_zhivom_obryve_reaper_ne_chitaet_polosu_vovse(self):
        o._link.witness(o.LINK_CH_BRIDGE, False)
        o._link.witness(o.LINK_CH_API, False)
        fb = self._reap(o.PC_SINGLE_STALE + 6000)
        self.assertEqual((fb.reads, fb.completed), (0, []))

    def test_korotkiy_obryv_ne_spasaet_ot_realnogo_zavisaniya(self):
        """Обрыв вычитается ровно своей длиной, а не отменяет реапер вовсе."""
        now_ts = o.time.time()
        age = o.PC_SINGLE_STALE + 6000
        o._link.witness(o.LINK_CH_BRIDGE, False, now=now_ts - age + 10)
        o._link.witness(o.LINK_CH_API, False, now=now_ts - age + 10)
        o._link.witness(o.LINK_CH_BRIDGE, True, now=now_ts - age + 10 + 600)
        fb = self._reap(age)
        self.assertEqual([c[1] for c in fb.completed], ["failed"])


# ═══════════ 63-c: «сеть или наша ошибка» судит полный поток, знак — на последней строке ═══════════
# Живые строки смерти CLI (дословно из расписок и перепись 62-p): все — ОДНА ПОСЛЕДНЯЯ строка stdout.
LIVE_CLI_NOT_NET = ("Failed to authenticate: OAuth session expired and could not be refreshed",  # #51
                    "You've hit your session limit · resets 10:30pm",                           # #33
                    "API Error: Server error mid-response. The response above may be incomplete.")  # #100
LIVE_NET = (LIVE_API_284, LIVE_GIT, LIVE_TG)


def _head_verdict_63c(out, err):
    """Решение HEAD (до 63-c) дословным выражением: хвосты по 500 знаков."""
    out_s = (out or "").strip()
    return o._RE_NET_TEXT.search(o._tail(out_s) + "\n" + o._tail(o._tail(err))) is not None


class TestNetSignIsReadOnItsLine63c(LinkBase):
    """Рез в 500 знаков не якорь: цитата покупала «сеть», длинная строка CLI её теряла."""

    def _new(self, out, err):
        return bool(o._fail_is_network((out or "").strip(), err, watch=o._link,
                                       witness=lambda *a, **k: None))

    def test_contrafact_a_quote_before_the_cli_line_no_longer_buys_network(self):
        out = "доклад: 62-p разбирал «%s» у #284.\n%s" % (LIVE_API_284, LIVE_CLI_NOT_NET[0])
        self.assertTrue(_head_verdict_63c(out, ""), "HEAD называл это сетью")
        self.assertFalse(self._new(out, ""))

    def test_contrafact_a_long_cli_line_keeps_its_head(self):
        out = LIVE_API_284 + " " + "x" * 600
        self.assertFalse(_head_verdict_63c(out, ""), "HEAD срезал голову строки с признаком")
        self.assertTrue(self._new(out, ""))

    def test_price_named_a_sign_not_on_the_last_line_is_not_read(self):
        err = "Error: fetch failed\n  code: 'ECONNREFUSED'\n}"
        self.assertTrue(_head_verdict_63c("", err))
        self.assertFalse(self._new("", err))

    def test_sweep_lock_a_quote_anywhere_before_the_cli_line_is_ours(self):
        """Замок: всё, что HEAD называл НАШЕЙ ошибкой, названо нашей и теперь; цитата — никогда не сеть."""
        n = lost = 0
        for net in LIVE_NET:
            for cli in LIVE_CLI_NOT_NET:
                for pad in range(0, 1500, 11):
                    out = "доклад «%s» %s\n%s" % (net, "," * pad, cli)
                    for stream in ("out", "err"):
                        a, b = (out, "") if stream == "out" else ("", out)
                        head, new = _head_verdict_63c(a, b), self._new(a, b)
                        n += 1
                        lost += (not head) and new
                        self.assertFalse(new)
        self.assertEqual((n, lost), (3 * 3 * 137 * 2, 0))

    def test_sweep_live_network_line_is_named_network_as_before(self):
        """Что HEAD называл сетью на живой форме (строка CLI последней), названо сетью и теперь."""
        n = 0
        for net in LIVE_NET:
            for pad in range(0, 1500, 11):
                text = "частичный ответ %s\n%s" % ("," * pad, net)
                for a, b in ((text, ""), ("", text), (text, text)):
                    self.assertEqual(_head_verdict_63c(a, b), self._new(a, b))
                    self.assertTrue(self._new(a, b))
                    n += 1
        self.assertEqual(3 * 137 * 3, n)

    def test_the_cut_of_the_shown_stderr_names_itself_by_number(self):
        self.assertEqual(o._tail("  короткий  "), o._tail_shown("  короткий  "))
        shown = o._tail_shown("я" * 800)
        self.assertTrue(shown.startswith("[показан хвост 500 из 800 симв.] "))
        self.assertTrue(shown.endswith("я" * 500))


if __name__ == "__main__":
    unittest.main(verbosity=2)
