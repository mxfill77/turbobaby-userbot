# -*- coding: utf-8 -*-
"""
test_result_spill.py — обрезка отчёта, которая называет себя, и хвост, который её переживает.
Диска касаемся ТОЛЬКО во временном каталоге (tempfile), боевой docs/artifacts/reports не трогаем.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_result_spill -v
"""

import os
import re
import shutil
import tempfile
import datetime
import unittest

import result_spill as rs

CAP = 4500          # боевой RESULT_MAX полосы ПК
NOW = datetime.datetime(2026, 8, 11, 10, 45, 3, tzinfo=datetime.timezone.utc)

# Признак ЧУЖОГО читателя: ровно этой регуляркой серверный devbot достаёт полную длину из пометки
# (`devbot._TRUNC_FULL_RE`). Копия здесь — чтобы тест ловил расхождение формата, а не веру в него.
SRV_TRUNC_RE = re.compile(r"ОБРЕЗАН ДЕМОНОМ: полная длина (\d+) симв")
SRV_FACT_RE = re.compile(r"\bFACT\s*:", re.IGNORECASE)          # `devbot._FACT_RE`, дословно


def report(chars, fact_at=None, result_tail=True):
    """Отчёт заданной длины из живых по форме предложений. fact_at: 'head'|'tail'|None."""
    body = ("Сделано: правка модуля, гейт зелёный, коммит на месте. " * 200)[:chars]
    if fact_at == "head":
        body = "FACT: commit abc1234 в git log origin/main. " + body
    elif fact_at == "tail":
        body = body + "\nFACT: commit abc1234 в git log origin/main."
    if result_tail:
        body += "\nRESULT: хвост отчёта закрыт, гейт зелёный"
    return body


class TestShortReportUnchanged(unittest.TestCase):
    """Короткий отчёт проходит КАК РАНЬШЕ: ни файла, ни пометки, ни замка."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rs_short_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_korotkii_otchyot_bait_v_bait(self):
        for n in (0, 1, 500, CAP - 1, CAP):
            src = report(n)[:n]
            got, rel = rs.cap_result(src, tid=473, cap=CAP, now=NOW, reports_dir=self.dir)
            self.assertEqual(got, src, "длина %d" % n)
            self.assertIsNone(rel, "тела короткому отчёту не нужно")
        self.assertEqual(os.listdir(self.dir), [], "короткий отчёт файлов не плодит")

    def test_korotkii_bez_bloka_fact_pometki_ne_poluchaet(self):
        """Замок висит на ОБРЕЗКЕ, а не на всякой записи: иначе им пометилась бы вся полоса ПК
        (замер 11.08: блок FACT встречается в 0 строках из 156) и он не значил бы ничего."""
        got, _ = rs.cap_result(report(300, fact_at=None), tid=1, cap=CAP, now=NOW,
                               reports_dir=self.dir)
        self.assertNotIn(rs.UNVERIFIED_MARK, got)
        self.assertNotIn(rs.TRUNC_HEAD, got)


class TestLongReportKeepsTail(unittest.TestCase):
    """Длинный отчёт: тело на диске ЦЕЛИКОМ, в очереди — голова с пометкой."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rs_long_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _spill(self, src, tid=473):
        return rs.cap_result(src, tid=tid, cap=CAP, now=NOW, reports_dir=self.dir)

    def test_khvost_lozhitsya_na_disk_polnostyu(self):
        src = report(9000, fact_at="head")
        got, rel = self._spill(src)
        self.assertTrue(rel and rel.startswith(rs.REPORTS_REL_DIR + "/"), rel)
        with open(os.path.join(self.dir, os.path.basename(rel)), encoding="utf-8") as f:
            disk = f.read()
        self.assertIn(src, disk, "в файле обязан лежать ИСХОДНЫЙ текст целиком, а не голова")
        self.assertIn("473", disk)                       # id задачи, а не «ищи по времени»
        self.assertLessEqual(len(got), CAP, "потолок очереди НЕ поднимаем")

    def test_imya_fayla_nesyot_id_zadachi(self):
        _, rel = self._spill(report(9000), tid=473)
        self.assertIn("task473", os.path.basename(rel))

    def test_obrezka_nazyvaet_chisla_i_adres(self):
        src = report(9000, fact_at="head")
        got, rel = self._spill(src)
        self.assertIn(rs.TRUNC_HEAD, got)
        self.assertIn(rel, got, "пометка обязана назвать МЕСТО полного текста")
        m = SRV_TRUNC_RE.search(got)
        self.assertTrue(m, "формат пометки обязан читаться серверным devbot._TRUNC_FULL_RE")
        self.assertEqual(int(m.group(1)), len(src), "полная длина — ДО обрезки")
        head = got.split("\n\n" + rs.TRUNC_HEAD)[0]
        m2 = re.search(r"в очередь попало (\d+) — срезано (\d+) симв", got)
        self.assertEqual(int(m2.group(1)), len(head), "«попало» — это ФАКТ длины головы")
        self.assertEqual(int(m2.group(2)), len(src) - len(head))

    def test_rez_ne_poseredine_slova(self):
        """У задачи 473 обрыв пришёлся посреди слова — читает строку человек, а не греп."""
        src = report(9000, fact_at="head")
        got, _ = self._spill(src)
        head = got.split("\n\n" + rs.TRUNC_HEAD)[0]
        self.assertTrue(src.startswith(head), "голова обязана быть префиксом исходника")
        self.assertFalse(head[-1].isalpha() and src[len(head)].isalpha(),
                         "рез посреди слова: %r|%r" % (head[-30:], src[len(head):len(head) + 10]))

    def test_stroka_itoga_spasena_iz_khvosta(self):
        """RESULT: живёт в конце и срезается первой: демон подтверждал итог по строке, которой в
        очереди уже нет (38 случаев из 45). Спасаем её в пометку."""
        got, _ = self._spill(report(9000, fact_at="head"))
        self.assertIn("RESULT: хвост отчёта закрыт", got)
        self.assertIn(rs.RESCUE_MARK, got)

    def test_dva_tela_v_odnu_sekundu_ne_zatirayut_drug_druga(self):
        a = self._spill(report(9000), tid=1)[1]
        b = self._spill(report(9100), tid=1)[1]
        self.assertNotEqual(a, b)
        self.assertEqual(len(os.listdir(self.dir)), 2)

    def test_potolok_ne_prevyshen_nikogda(self):
        for n in (CAP + 1, CAP + 40, 9000, 60000):
            got, _ = self._spill(report(n))
            self.assertLessEqual(len(got), CAP, "длина исходника %d" % n)
            self.assertIn(rs.TRUNC_HEAD, got, "длина исходника %d" % n)

    def test_sboi_diska_ne_pryachetsya(self):
        """FAIL-SAFE: тело не записалось → обрезка всё равно называет себя и говорит о потере.
        Молчаливой потерей защита от потери быть не может."""
        busy = os.path.join(self.dir, "zanyato")     # ФАЙЛ на месте каталога → makedirs сорвётся
        with open(busy, "w", encoding="utf-8") as f:
            f.write("не каталог")
        got, rel = rs.cap_result(report(9000), tid=7, cap=CAP, now=NOW,
                                 reports_dir=os.path.join(busy, "sub"))
        self.assertIsNone(rel)
        self.assertIn(rs.TRUNC_HEAD, got)
        self.assertIn(rs.NO_DUMP_NOTE, got)
        self.assertLessEqual(len(got), CAP)


class TestVerificationLock(unittest.TestCase):
    """ЗАМОК: запись со срезанным блоком FACT проверенной не считается. Исходов ТРИ."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rs_lock_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _spill(self, src):
        return rs.cap_result(src, tid=473, cap=CAP, now=NOW, reports_dir=self.dir)[0]

    def test_blok_ucelel_v_golove_pometki_net(self):
        got = self._spill(report(9000, fact_at="head"))
        self.assertNotIn(rs.UNVERIFIED_MARK, got)
        self.assertTrue(SRV_FACT_RE.search(got), "уцелевший блок читается и чужим судьёй")

    def test_blok_srezan_pometka_est_i_nazyvaet_prichinu(self):
        got = self._spill(report(9000, fact_at="tail"))
        self.assertIn(rs.UNVERIFIED_MARK, got)
        self.assertIn(rs.CAUSE_CUT, got)
        self.assertNotIn(rs.CAUSE_ABSENT, got)

    def test_bloka_ne_bylo_vovse_eto_tretii_iskhod(self):
        """«Доказательство потеряли мы» и «доказательства не давали» — разные диагнозы."""
        got = self._spill(report(9000, fact_at=None))
        self.assertIn(rs.UNVERIFIED_MARK, got)
        self.assertIn(rs.CAUSE_ABSENT, got)
        self.assertNotIn(rs.CAUSE_CUT, got)

    def test_pometka_stavitsya_po_tekstu_a_ne_po_pamyati(self):
        """Признак — чистая функция от текста: тот же текст даёт тот же вердикт всегда."""
        self.assertTrue(rs.has_fact("шапка\nFACT: commit abc"))
        self.assertTrue(rs.has_fact("fact : read-only"))       # регистр/пробел — как у судьи
        self.assertFalse(rs.has_fact("факт: сделано"))
        self.assertFalse(rs.has_fact("FACTUM: латынь"))

    def test_pometka_sama_ne_schitaetsya_blokom_fact(self):
        """ЗАМОК ЗАМКА. Пометка жалуется на пропажу блока — и не смеет ею же удовлетворить чужого
        судью: иначе обрезанная запись прочиталась бы сервером как проверенная ИМЕННО потому, что
        мы сообщили о потере. Проверяем все тексты, которые пометка складывает."""
        for s in (rs.TRUNC_HEAD, rs.UNVERIFIED_MARK, rs.CAUSE_CUT, rs.CAUSE_ABSENT,
                  rs.NO_DUMP_NOTE, rs.RESCUE_MARK,
                  rs.note(9000, 4000, CAP, "docs/artifacts/reports/x.md", "", rs.CAUSE_CUT),
                  rs.note(9000, 4000, CAP, None, "", rs.CAUSE_ABSENT)):
            self.assertIsNone(SRV_FACT_RE.search(s), s)

    def test_srezannyi_blok_ne_vernulsya_spasyonnym_itogom(self):
        got = self._spill(report(9000, fact_at="tail"))
        self.assertIn(rs.UNVERIFIED_MARK, got)
        self.assertIsNone(SRV_FACT_RE.search(got), "спасаем ИТОГ, а не доказательство")


class TestPureFunction(unittest.TestCase):
    """Инвариант RESULT_SPILL_PURE: модуль не ходит в сеть, не читает конфиги, не удаляет."""

    def test_modul_ne_tyanet_seti_i_sekretov(self):
        with open(rs.__file__, encoding="utf-8") as f:
            src = f.read()
        for bad in ("urllib", "requests", "socket", "load_env", "dotenv", "BRIDGE_TOKEN",
                    "os.remove", "shutil.rmtree", "unlink", "subprocess"):
            self.assertNotIn(bad, src, bad)

    def test_save_off_diska_ne_kasaetsya(self):
        got, rel = rs.cap_result(report(9000), tid=1, cap=CAP, now=NOW, save=False)
        self.assertIsNone(rel)
        self.assertIn(rs.NO_DUMP_NOTE, got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
