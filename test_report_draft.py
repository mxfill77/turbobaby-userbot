# -*- coding: utf-8 -*-
"""
test_report_draft.py — замки ЧЕРНОВИКА ДОКЛАДА (report_draft.py). Ни сети, ни моста, ни claude.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_report_draft -v

Голдены пишутся на ЖИВОМ формате: разделы взяты дословно теми формами, которыми исполнитель
их реально набирает («## СДЕЛАНО», «СДЕЛАНО:», «**НЕ СДЕЛАНО**»), а не одной идеализированной.
Правило репозитория: мок внешнего ответа обязан копировать живой формат, иначе зелёный тест
перестаёт задевать боевую ветку.
"""

import io
import os
import re
import tempfile
import unittest

import report_draft as rd


LIVE = """<!-- pc_report tid=21 token=1234-5678-21 -->
## СДЕЛАНО
- разобрал время по шагам, числа из следов
- коммит 0be1ea6 лёг, пуш прошёл
FACT: commit 0be1ea6 в git log

## НЕ СДЕЛАНО
- раздел в узел карты не записан

## НЕИЗВЕСТНО
- успеет ли доклад: бюджет на исходе
"""


class TestPath(unittest.TestCase):
    """Имя файла — единственный замок от чужого черновика (очередь переиспользует номера)."""

    def test_token_v_imeni_otsekaet_chuzhoi_zakhod(self):
        a = rd.draft_rel(21, "1000-1-21")
        b = rd.draft_rel(21, "2000-2-21")
        self.assertNotEqual(a, b, "два прогона одного номера обязаны иметь РАЗНЫЕ файлы")
        self.assertTrue(a.startswith(rd.DRAFT_REL_DIR + "/"), a)
        self.assertIn("task21-", a)

    def test_gryaznyi_token_ne_uvodit_iz_kataloga(self):
        for bad in ("../../secret", "a/b\\c", "x:y*z", None, ""):
            rel = rd.draft_rel(21, bad)
            with self.subTest(bad=bad):
                self.assertTrue(rel.startswith(rd.DRAFT_REL_DIR + "/"), rel)
                self.assertNotIn("..", rel)
                self.assertEqual(rel.count("/"), rd.DRAFT_REL_DIR.count("/") + 1, rel)

    def test_nomer_zadachi_tolko_tsifry(self):
        self.assertIn("task21-", rd.draft_rel("21; rm -rf", "t"))

    def test_draft_path_stroit_ot_perednogo_kornya(self):
        p = rd.draft_path(7, "tok", os.path.join("D:", "x"))
        self.assertTrue(p.endswith(os.path.join("tmp", "pc_report", "task7-tok.md")), p)


class TestSections(unittest.TestCase):
    def test_zhivoi_format_razbiraetsya_tselikom(self):
        got = rd.sections(LIVE)
        self.assertEqual(set(got), set(rd.HEADS))
        self.assertIn("0be1ea6", got[rd.HEAD_DONE])
        self.assertIn("узел карты", got[rd.HEAD_NOT_DONE])
        self.assertIn("бюджет", got[rd.HEAD_UNKNOWN])

    def test_tri_zhivye_formy_zagolovka(self):
        for form in ("## %s", "%s:", "**%s**", "### %s :", "%s"):
            txt = (form % rd.HEAD_DONE) + "\nтело\n"
            with self.subTest(form=form):
                self.assertEqual(rd.sections(txt).get(rd.HEAD_DONE), "тело")

    def test_nenazvannyi_razdel_v_slovar_ne_popadaet(self):
        got = rd.sections("## %s\nтело\n" % rd.HEAD_DONE)
        self.assertEqual(list(got), [rd.HEAD_DONE])
        self.assertEqual(rd.missing("## %s\nтело\n" % rd.HEAD_DONE),
                         (rd.HEAD_NOT_DONE, rd.HEAD_UNKNOWN))

    def test_zagolovok_vnutri_stroki_zagolovkom_ne_schitaetsya(self):
        """«что СДЕЛАНО, а что нет» в теле — проза, а не раздел: иначе разбор поедет на прозе."""
        self.assertEqual(rd.sections("текст про то, что СДЕЛАНО и что нет"), {})


class TestRenderThreeOutcomes(unittest.TestCase):
    """Три исхода чтения, а не два: нет · разобран · есть, но не разобран."""

    def test_net_faila_govorit_ob_etom_pryamo(self):
        outcome, block = rd.render("", path_rel="tmp/pc_report/task21-x.md")
        self.assertEqual(outcome, "absent")
        self.assertIn("task21-x.md", block)
        self.assertIn("не оставил", block)

    def test_tolko_shtamp_eto_tozhe_pusto(self):
        outcome, _ = rd.render(rd.stamp(21, "tok"))
        self.assertEqual(outcome, "absent", "штамп демона словами захода не является")

    def test_razobrannyi_nesyot_vse_tri_razdela(self):
        outcome, block = rd.render(LIVE)
        self.assertEqual(outcome, "parsed")
        for h in rd.HEADS:
            self.assertIn(h, block)
        self.assertIn("0be1ea6", block)
        self.assertNotIn("<!--", block, "штамп в итог задачи не едет")

    def test_nenazvannyi_razdel_nazyvaet_sebya_a_ne_molchit(self):
        outcome, block = rd.render("## %s\nвсё сделано\n" % rd.HEAD_DONE)
        self.assertEqual(outcome, "parsed")
        self.assertIn("%s — %s" % (rd.HEAD_NOT_DONE, rd.MISSING_NOTE), block)
        self.assertIn("%s — %s" % (rd.HEAD_UNKNOWN, rd.MISSING_NOTE), block)

    def test_est_no_ne_razobran_ne_vydayotsya_za_molchanie(self):
        outcome, block = rd.render("я работал, сделал вот это и то, а разделов не завёл")
        self.assertEqual(outcome, "nonparse")
        self.assertIn("разбор не удался", block)
        self.assertIn("я работал", block)

    def test_blok_ne_zhryot_ves_potolok_itoga(self):
        huge = "## %s\n%s\n" % (rd.HEAD_DONE, "очень длинная строка отчёта. " * 400)
        outcome, block = rd.render(huge)
        self.assertEqual(outcome, "parsed")
        self.assertLessEqual(len(block), rd.BLOCK_MAX)
        self.assertTrue(block.endswith("…"), "урезание обязано называть себя многоточием")

    def test_perevody_strok_skhlopnuty(self):
        """Итог задачи едет ОДНОЙ строкой в журнал и карточку — многострочный блок её порвёт."""
        _, block = rd.render(LIVE)
        self.assertNotIn("\n", block)


class TestClause(unittest.TestCase):
    def test_abzats_nazyvaet_put_i_tri_razdela(self):
        rel = rd.draft_rel(21, "tok")
        c = rd.clause(rel)
        self.assertIn(rel, c)
        for h in rd.HEADS:
            self.assertIn(h, c)

    def test_abzats_nazyvaet_prichinu_a_ne_tolko_prikaz(self):
        """Исполнитель, знающий ЦЕНУ, пишет черновик по вехам сам. Тем же приёмом чинили абзац
        про NEEDS_APPROVAL — текст, обещавший кнопку, которой нет."""
        c = rd.clause("tmp/pc_report/x.md")
        self.assertIn("stdout", c)
        self.assertIn("пропадает", c)

    def test_abzats_ne_otmenyaet_finalnyi_otchyot(self):
        c = rd.clause("tmp/pc_report/x.md")
        self.assertIn("RESULT:", c)
        self.assertIn("FACT", c)


class TestReadIsFailSafe(unittest.TestCase):
    """«НЕ ПРОЧИТАЛ» и «прочитал, там пусто» — РАЗНЫЕ новости, и вторая не смеет прятать первую."""

    def test_net_faila_daet_None_i_prichinu_slovami(self):
        text, why = rd.read(os.path.join(tempfile.gettempdir(), "нет-такого-файла-xyz.md"))
        self.assertIsNone(text, "«не прочитал» обязано отличаться от «прочитал пусто»")
        self.assertTrue(why, "причина отказа обязана быть названа словами")

    def test_prichina_otkaza_doezzhaet_do_vladeltsa(self):
        text, why = rd.read(os.path.join(tempfile.gettempdir(), "нет-такого-файла-xyz.md"))
        _, block = rd.render(text, path_rel="tmp/pc_report/task9-t.md", why=why)
        self.assertIn("чтение:", block)

    def test_chitaet_utf8_s_diska(self):
        d = tempfile.mkdtemp(prefix="rdraft_")
        p = os.path.join(d, "task1-t.md")
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write(LIVE)
        text, why = rd.read(p)
        self.assertEqual(why, "")
        self.assertIn("0be1ea6", text)
        outcome, block = rd.render(text, why=why)
        self.assertEqual(outcome, "parsed")
        self.assertIn("0be1ea6", block)

    def test_ensure_dir_sozdayot_i_ne_padaet_povtorno(self):
        d = tempfile.mkdtemp(prefix="rdraft_")
        p = os.path.join(d, "глубже", "task1-t.md")
        self.assertEqual(rd.ensure_dir(p), "", "пустая причина = каталог есть")
        self.assertEqual(rd.ensure_dir(p), "", "повторный вызов — не ошибка")
        self.assertTrue(os.path.isdir(os.path.dirname(p)))


class TestPurity(unittest.TestCase):
    """REPORT_DRAFT_PURE: модуль чистый И НИЧЕГО НЕ УДАЛЯЕТ. Второе — не педантизм: черновик
    оборванного захода единственный, и уборщик, ошибившийся на один прогон, стирает то, ради
    чего модуль заведён. Зеркало `RESULT_SPILL_PURE`."""

    def test_ni_seti_ni_sekretov_ni_podprotsessov(self):
        with io.open(rd.__file__.replace(".pyc", ".py"), encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("urllib", "requests", "socket", "dotenv", "BRIDGE_TOKEN",
                    "subprocess", "sqlite3"):
            self.assertNotIn(bad, src, "модуль обязан остаться чистым: найдено %r" % bad)

    def test_nichego_ne_udalyaet(self):
        with io.open(rd.__file__.replace(".pyc", ".py"), encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("os.remove", "os.unlink", "shutil.rmtree", "rmdir", ".unlink("):
            self.assertNotIn(bad, src, "черновик стирать нельзя ничем: найдено %r" % bad)

    def test_katalog_nazvan_yavno_odnim_mestom(self):
        self.assertEqual(rd.DRAFT_REL_DIR, "tmp/pc_report")
        with io.open(rd.__file__.replace(".pyc", ".py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(len(re.findall(r'"tmp/pc_report"', src)), 1,
                         "место для временного названо ОДИН раз — иначе разъедется")


if __name__ == "__main__":
    unittest.main(verbosity=2)
