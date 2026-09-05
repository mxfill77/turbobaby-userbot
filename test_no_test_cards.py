# -*- coding: utf-8 -*-
"""test_no_test_cards.py — «ПРОБЫ НЕ ПИШУТ ВЛАДЕЛЬЦУ ВЖИВУЮ» (задание 00-no-test-cards.0905).

ПОВОД, ЗАМЕРЕННЫЙ ПО ЖИВЫМ ЛОГАМ. В ночь на 05.09 владельцу за 19 секунд (05:14:37–05:14:56)
упало 16 карточек ворот. Живые ворота в эту минуту не отказывали ни разу — в
`pc_orchestrator.log` за 05:14 нет ни строки «ОСТАНОВЛЕНО», только вотчдог. Весь поток дал
ПРОГОН ЮНИТ-ТЕСТА `test_client_contour.py`, ходивший боевым путём отправки: коммит `new777` в
четырёх из шестнадцати карточек — литерал того самого теста (`test_client_contour.py:356`), в
git его нет и не было.

ЧТО ЗАКРЫВАЕТ НАБОР — ровно три обязательных отрицательных теста задания:
  1) проба не доходит до владельца НИ ПРИ КАКИХ УСЛОВИЯХ ПО УМОЛЧАНИЮ;
  2) настоящая карточка по настоящему поводу ДОХОДИТ (замок не глушит боевой путь);
  3) карточка с несуществующим коммитом НЕ СОБИРАЕТСЯ и говорит об этом в журнал.

СЕТИ ЗДЕСЬ НЕТ НИ В ОДНОМ ТЕСТЕ. Там, где проверяется «дошло бы», транспорт подменён датчиком,
который взрывается при вызове или считает вызовы, — но наружу не идёт. Тест, доказывающий, что
пробы не пишут владельцу, не имеет права сам написать владельцу.
"""
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import dispatch_notify as dn            # noqa: E402
import pc_orchestrator as o             # noqa: E402


class TestProbaNeDohodit(unittest.TestCase):
    """(1) ПРОБА НЕ ДОХОДИТ ДО ВЛАДЕЛЬЦА НИ ПРИ КАКИХ УСЛОВИЯХ ПО УМОЛЧАНИЮ."""

    def setUp(self):
        # Разрешение из пробы — процесс-локальное; чужой тест не смеет протечь в этот.
        self._save = dn._LIVE_FROM_PROBE
        dn._LIVE_FROM_PROBE = ""
        self.addCleanup(lambda: setattr(dn, "_LIVE_FROM_PROBE", self._save))

    def test_etot_progon_sam_priznan_proboi(self):
        """Самый честный из всех: ЭТОТ процесс — тест, и замок обязан звать его пробой."""
        is_probe, why = dn.probe_verdict()
        self.assertTrue(is_probe, f"прогон теста признан боевым: {why}")
        self.assertFalse(dn.live_send_verdict()[0])

    def test_api_ne_hodit_v_set_iz_proby(self):
        """ГЛАВНЫЙ: `_api` из пробы не трогает сеть ВООБЩЕ — датчик взорвался бы."""
        def vzryv(*a, **k):
            raise AssertionError("проба ушла в сеть — замок не сработал")
        with mock.patch.object(dn.urllib.request, "urlopen", vzryv):
            ok, body = dn._api("sendMessage", {"chat_id": 1, "text": "проба"})
        self.assertFalse(ok)
        self.assertIn("проба", body.get("description", ""))

    def test_vse_dveri_modulya_zakryty_odnim_zamkom(self):
        """Замок стои́т в горлышке: молчат ВСЕ двери, а не те, что кто-то вспомнил."""
        def vzryv(*a, **k):
            raise AssertionError("проба ушла в сеть — замок не сработал")
        with mock.patch.object(dn.urllib.request, "urlopen", vzryv), \
                mock.patch.object(dn, "TOKEN", "x"):
            self.assertFalse(dn.send("t")[1])
            self.assertFalse(dn.send_critical("t")[1])
            self.assertFalse(dn.send_topic("t")[1])
            self.assertFalse(dn.send_topic_strict("t", 328)[1])
            self.assertFalse(dn.edit_topic_strict("t", 328, 7)[1])

    def test_priznak_strukturnyi_a_ne_flag(self):
        """Проба опознаётся ПО ТОЧКЕ ВХОДА — автору пробы не надо помнить ни о чём.

        Предсмертный взгляд задания: «заведут флаг „это тест“, который следующий заход забудет
        выставить». Здесь забывать нечего — скрипт из `_scratch_*` молчит сам собой."""
        for entry, hint in (
            (os.path.join(REPO, "_scratch_no_test_cards_0905", "premise.py"), "_scratch"),
            (os.path.join(REPO, "tmp", "probe.py"), "tmp"),
            (os.path.join(REPO, "test_client_contour.py"), "тест"),
            (os.path.join(REPO, "sub", "dir", "x.py"), "подкаталог"),
            (r"C:\gde-to\esche\probe.py", "чужое дерево"),
            ("", "python -c / REPL"),
        ):
            is_probe, why = dn.probe_verdict(entry=entry, env={})
            self.assertTrue(is_probe, f"{hint}: признан боевым ({why})")

    def test_fail_closed_nerazobrannyi_vhod(self):
        """Не смогли доказать, что вызов боевой → он НЕ боевой (третьего исхода нет)."""
        self.assertTrue(dn.probe_verdict(entry=None, env={"PYTEST_CURRENT_TEST": "x"})[0])
        self.assertTrue(dn.probe_verdict(entry="", env={})[0])

    def test_razreshenie_tolko_yavnym_deistviem(self):
        """Чтобы ушло — ЯВНОЕ намеренное действие с названной СЛОВАМИ причиной."""
        with self.assertRaises(ValueError):
            dn.allow_live_from_probe("")
        with self.assertRaises(ValueError):
            dn.allow_live_from_probe("   ")
        self.assertFalse(dn.live_send_verdict()[0])
        dn.allow_live_from_probe("ручная проверка канала владельцем")
        self.assertTrue(dn.live_send_verdict()[0])

    def test_razreshenie_ne_zhivet_v_okruzhenii(self):
        """Разрешение НЕ переменная окружения: иначе один `LIVE=1` в оболочке открыл бы канал
        всем следующим пробам смены — тот самый класс, ради которого замок и заведён."""
        for name in ("DISPATCH_NOTIFY_LIVE", "DISPATCH_NOTIFY_LIVE_OK", "LIVE", "TURBOBABY_LIVE"):
            self.assertTrue(dn.probe_verdict(entry="", env={name: "1"})[0],
                            f"{name} открыла живую отправку из пробы")


class TestShchelSpavna(unittest.TestCase):
    """(1-бис) ЩЕЛЬ, КОТОРОЙ ПОТОК И ВЫШЕЛ: демон зовёт notify ОТДЕЛЬНЫМ ПРОЦЕССОМ.

    Замок внутри `dispatch_notify` смотрит на точку входа СВОЕГО процесса, а у спавненного CLI она
    всегда `dispatch_notify.py`, то есть всегда «боевая». Ровно так прогон `test_client_contour.py`
    выпустил 15 карточек: тест → `_notify_gate_card` → Popen → Telegram. Судить о пробе может
    только ТОТ, КТО ЗОВЁТ, — эти тесты стерегут проверку в процессе-родителе."""

    def setUp(self):
        self.spawned = []
        p = mock.patch.object(o.subprocess, "Popen",
                              lambda *a, **k: self.spawned.append(a[0]) or mock.MagicMock())
        p.start()
        self.addCleanup(p.stop)
        self._save = dn._LIVE_FROM_PROBE
        dn._LIVE_FROM_PROBE = ""
        self.addCleanup(lambda: setattr(dn, "_LIVE_FROM_PROBE", self._save))

    def test_iz_proby_process_otpravki_ne_spavnitsya(self):
        """ГЛАВНЫЙ: из теста НИ ОДНА дверь демона наружу не спавнит процесс отправки."""
        o._notify("проба")
        o._notify_gate_card("4528917", "карточка ворот")
        o._notify_critical("проба")
        o._notify_topic(328, "проба")
        self.assertEqual(self.spawned, [], f"проба спавнила отправку: {self.spawned}")

    def test_dver_govorit_o_svoyom_otkaze_v_zhurnal(self):
        """Молчание без строки в журнале неотличимо от потерянной карточки."""
        with mock.patch.object(o, "log") as zhurnal:
            self.assertFalse(o._dnotify_spawn(["--gate-card", "4528917", "т"], "карточка ворот"))
        skazano = " ".join(str(c) for c in zhurnal.warning.call_args_list)
        self.assertIn("НЕ ОТПРАВЛЕНО", skazano)

    def test_boevoi_vyzov_spavnit_kak_ranshe(self):
        """Контроль «не задели вторую половину»: боевой вызов спавнит и argv не потерял."""
        with mock.patch.object(dn, "probe_verdict", lambda *a, **k: (False, "боевой вход")):
            self.assertTrue(o._dnotify_spawn(["--gate-card", "4528917", "текст"], "карточка ворот"))
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(self.spawned[0][1:], [o.DNOTIFY, "--gate-card", "4528917", "текст"])

    def test_slomannyi_priznak_ne_glushit_nastoyashchee(self):
        """FAIL-OPEN осознанный: признак не спросился → отправляем. Глушить настоящее нельзя."""
        with mock.patch.object(dn, "probe_verdict", mock.Mock(side_effect=RuntimeError("нет"))), \
                mock.patch.object(o, "log"):
            ok, _why = o._live_send_allowed()
        self.assertTrue(ok)


class TestNastoyashchayaKartochkaDohodit(unittest.TestCase):
    """(2) НАСТОЯЩАЯ КАРТОЧКА ПО НАСТОЯЩЕМУ ПОВОДУ ДОХОДИТ.

    Запрет задания касается проб и тестов, а НЕ боевого пути. Если бы замок глушил и его, лечение
    было бы хуже болезни: владелец перестал бы получать карточки вовсе."""

    def test_boevye_tochki_vhoda_polosy_priznany_boevymi(self):
        """Все живые входы полосы лежат в корне репозитория — и обязаны проходить."""
        for name in ("pc_orchestrator.py", "pc_agent.py", "dispatch_notify.py", "pretool_guard.py",
                     "expectations_pc_run.py", "deploy_voice.py", "lesson_regress.py",
                     "rc_supervisor.py", "review_audit_run.py"):
            self.assertTrue(os.path.exists(os.path.join(REPO, name)), f"{name} пропал из корня")
            is_probe, why = dn.probe_verdict(entry=os.path.join(REPO, name), env={})
            self.assertFalse(is_probe, f"боевой вход {name} признан пробой: {why}")

    def test_iz_boevogo_vyzova_soobshchenie_uhodit(self):
        """`_api` при боевой точке входа ИДЁТ в транспорт — замок его не трогает."""
        hodil = []

        class Otvet:
            def read(self):
                return b'{"ok":true,"result":{"message_id":11}}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def transport(req, timeout=None):
            hodil.append(getattr(req, "full_url", "")[:34])
            return Otvet()

        with mock.patch.object(dn, "probe_verdict", lambda *a, **k: (False, "боевой вход")), \
                mock.patch.object(dn.urllib.request, "urlopen", transport), \
                mock.patch.object(dn, "TOKEN", "x"):
            ok, body = dn._api("sendMessage", {"chat_id": 1, "text": "настоящая карточка"})
        self.assertTrue(ok)
        self.assertEqual(len(hodil), 1, "боевой вызов до транспорта не дошёл")

    def test_kartochka_vorot_na_zhivom_kommite_sobiraetsya_i_uhodit(self):
        """Настоящий повод целиком: живой коммит → ворота держат, карточка СОБРАНА и отдана."""
        head = o._git_out(["rev-parse", "HEAD"])
        self.assertTrue(head, "git не отвечает — тест не о чем")
        cards, cows, asked = [], [], []
        held = o._client_block(
            ["userbot"], head, ["suggest.py"], "проба настоящего повода",
            notifier=lambda c, t: cards.append((c, t)),
            cowork=cows.append, state={}, reason_fn=lambda *a, **k: None,
            client_fn=lambda p: ["suggest.py"], subject_fn=lambda c: "тема",
            trainer_fn=lambda c: "вердикта нет", route_fn=lambda *a, **k: ("card", ""),
            asked_fn=lambda *a, **k: asked.append(a))
        self.assertEqual(held, ["suggest.py"])
        self.assertEqual(len(cards), 1, "настоящая карточка не собралась")
        self.assertEqual(cards[0][0], head)
        self.assertIn("suggest.py", cards[0][1])
        self.assertIn(head, cards[0][1])


class TestNesushchestvuyushchiiKommit(unittest.TestCase):
    """(3) КАРТОЧКА С НЕСУЩЕСТВУЮЩИМ КОММИТОМ НЕ СОБИРАЕТСЯ И ГОВОРИТ ОБ ЭТОМ В ЖУРНАЛ."""

    def test_new777_v_repozitorii_net(self):
        """Дословный литерал ночи 05.09 — и он не коммит, а строка из юнит-теста."""
        self.assertFalse(o._gate_commit_known("new777"))
        self.assertFalse(o._gate_commit_known(""))
        self.assertFalse(o._gate_commit_known(None))
        self.assertFalse(o._gate_commit_known("deadbeef"),
                         "похожая на хеш выдумка принята за коммит")

    def test_zhivoi_kommit_priznaetsya(self):
        """Контроль «не задели вторую половину»: настоящий HEAD признаётся."""
        head = o._git_out(["rev-parse", "HEAD"])
        self.assertTrue(head)
        self.assertTrue(o._gate_commit_known(head))
        self.assertTrue(o._gate_commit_known(head[:7]))

    def test_kartochka_ne_sobiraetsya_i_stroka_uhodit_v_zhurnal(self):
        """ГЛАВНЫЙ: карточки НЕТ, повод НЕ запомнен, а журнал про это ГОВОРИТ."""
        cards, cows, asked = [], [], []
        with mock.patch.object(o, "log") as zhurnal:
            held = o._client_block(
                ["userbot"], "new777", ["suggest.py"], "проба подлога входа",
                notifier=lambda c, t: cards.append((c, t)),
                cowork=cows.append, state={}, reason_fn=lambda *a, **k: None,
                client_fn=lambda p: ["suggest.py"], subject_fn=lambda c: "тема",
                trainer_fn=lambda c: "вердикта нет", route_fn=lambda *a, **k: ("card", ""),
                asked_fn=lambda *a, **k: asked.append(a),
                commit_known_fn=lambda c: False)
        self.assertEqual(cards, [], "карточка на несуществующий коммит СОБРАЛАСЬ")
        self.assertEqual(asked, [], "несуществующий повод запомнен как заданный вопрос")
        # ВОРОТА НЕ ТРОНУТЫ: отказ в силе, боты остаются на прежнем коде.
        self.assertEqual(held, ["suggest.py"])
        # ЖУРНАЛ ГОВОРИТ — молча не собрать карточку значит потерять её неотличимо от аварии.
        skazano = " ".join(str(c) for c in zhurnal.error.call_args_list)
        self.assertIn("НЕ СОБРАНА", skazano)
        self.assertIn("new777", skazano)
        self.assertTrue(any("НЕ СОБРАНА" in s and "new777" in s for s in cows),
                        f"в ленту не ушло ни строки про подлог: {cows}")

    def test_deffekt_vyzova_a_ne_vopros_vladeltsu(self):
        """Формулировка обязана называть это дефектом ВЫЗОВА, а не поводом спросить владельца."""
        cows = []
        with mock.patch.object(o, "log"):
            o._client_block(["userbot"], "new777", ["suggest.py"], "проба",
                            notifier=lambda c, t: None, cowork=cows.append, state={},
                            reason_fn=lambda *a, **k: None,
                            client_fn=lambda p: ["suggest.py"],
                            route_fn=lambda *a, **k: ("card", ""),
                            commit_known_fn=lambda c: False)
        self.assertTrue(any("дефект вызова" in s for s in cows), cows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
