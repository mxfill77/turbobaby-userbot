# -*- coding: utf-8 -*-
"""
test_model_name.py — голдены ПРАВИЛА разрешения имени модели (`model_name.py`).

Фразы взяты ДОСЛОВНО из замера дыры 01.09.2026
(`docs/artifacts/2026-09-01-разрешение-имени-модели-замер.md`) — правило-класс репозитория
«golden-тесты детекта = РЕАЛЬНЫЕ фразы клиента»: идеализированная формулировка зелёная и там,
где живая падает. Четыре случая §3 («в расчёт попала ЧУЖАЯ модель») вынесены отдельным тестом и
проверяются ровно в том виде, в каком их написал клиент.

Парк — снимок ТОГО ЖЕ замера (38 юнитов, 13 моделей): вердикт «в парке» обязан считаться от
данных, а не от каталога.
"""

import re
import unittest

import model_name as mn
import suggest


# Ключи каталога, реально стоящие в парке по снимку замера (park_list.md, 38 юнитов).
PARK = frozenset(("nmax155", "xmax300", "adv350", "forza300", "xadv750", "xsr155",
                  "cbr650r", "cb650r", "cb300r", "mt03", "ninja400", "vulcan650s", "click125"))
# Два поколения XMAX в парке (Лист1: юниты с токеном NEW и без него) — вход развилки K8.
GENS = {"xmax300": ["XMAX 300", "XMAX 300 New Gen"]}


def _source(mod):
    """Исходник модуля одной строкой (файл закрываем — иначе ResourceWarning в прогоне)."""
    with open(mod.__file__, encoding="utf-8") as f:
        return f.read()


def resolve(text, **kw):
    kw.setdefault("present", PARK)
    kw.setdefault("generations", GENS)
    return mn.resolve(text, **kw)


class TestFourMeasuredCases(unittest.TestCase):
    """§3 замера: четыре реплики, где в расчёт попадала ЧУЖАЯ модель. Ни одна не смеет
    разрешиться в модель, которую клиент не называл."""

    # Дословный список из черновика id 278 — рекламная рассылка конкурента с 16 моделями.
    AD_16 = ("Xmax, adv350, forza, nmax, pcx, cb650r, ninja, vulcan, "
             "mt-03, click, xsr, rebel, r7, cbr, adv, cb")

    def test_xadv_ne_stanovitsya_adv350(self):
        # id 362 и id 496: «adv» стои́т в словаре раньше «xadv», матчинг был подстрокой без границ.
        for s in ("X-ADV-750", "Xadv750", "XADV 750", "x-adv 750", "хадв 750"):
            v = resolve(s)
            self.assertEqual(v["outcome"], mn.OK, s)
            self.assertEqual(v["model"], "XADV 750", s)
            self.assertNotEqual(v["model"], "ADV 350", s)

    def test_cbr500r_ne_stanovitsya_cbr650r(self):
        # id 465: откат на корень «cbr» отбрасывал названную клиентом кубатуру 500.
        for s in ("Cbr500r", "CBR 500R", "cbr-500"):
            v = resolve(s)
            self.assertEqual(v["outcome"], mn.NOT_IN_CATALOG, s)
            self.assertIsNone(v["model"], s)
            self.assertTrue(mn.needs_human(v), s)

    def test_spisok_16_modeley_ne_stanovitsya_nmax(self):
        # id 278: брался ПЕРВЫЙ токен по порядку СЛОВАРЯ — им всегда оказывался «nmax».
        v = resolve(self.AD_16)
        self.assertEqual(v["outcome"], mn.HUMAN)
        self.assertEqual(v["reason"], mn.TWO_MODELS)
        self.assertIsNone(v["model"])
        self.assertGreater(len(v["options"]), 5)

    def test_ni_odin_iz_chetyryoh_ne_dayot_chuzhuyu_model(self):
        """Сводный инвариант четвёрки: ни одна реплика не отдаёт ГОТОВУЮ чужую модель."""
        chuzhie = {"ADV 350": ("X-ADV-750", "Xadv750"),
                   "CBR 650R": ("Cbr500r",),
                   "NMAX 155": (self.AD_16,)}
        for wrong, phrases in chuzhie.items():
            for s in phrases:
                self.assertNotEqual(resolve(s)["model"], wrong, s)


class TestTokenBoundaryAndLongestFirst(unittest.TestCase):
    """K1: сопоставление по границам токена, длинное имя раньше короткого."""

    def test_podstroka_vnutri_slova_ne_imya(self):
        for s in ("advise me please", "адвокат посоветовал вашу компанию",
                  "кликните по ссылке", "ninjas are cool", "just a myth of advertising"):
            self.assertEqual(resolve(s)["outcome"], mn.NONE, s)

    def test_izvestniy_ostatok_angliyskoe_slovo_click(self):
        """НАЗВАННЫЙ ОСТАТОК, а не находка. «click» и «forza» — сами по себе слова английского и
        итальянского, и границами токена они от имени модели НЕ отличаются: «click here» отдаёт
        CLICK 125 и здесь, и в прежнем коде (тот матчил ту же подстроку). Правило этот класс не
        закрывает и не ухудшает; закрыть его можно только контекстом реплики, не именем.
        Тест держит остаток ВИДИМЫМ — молча он бы выглядел закрытым."""
        self.assertEqual(resolve("click here")["model"], "CLICK 125")
        self.assertEqual(suggest._detect_model("click here"), "CLICK")   # так было и до правила

    def test_dlinnoe_imya_pobezhdaet_korotkoe(self):
        # «xadv» съедает «adv» внутри себя, «cbr650» — «cb».
        self.assertEqual([m["root"] for m in mn.find_mentions("xadv750")], ["xadv"])
        self.assertEqual([m["root"] for m in mn.find_mentions("cbr 650r")], ["cbr"])
        self.assertEqual([m["root"] for m in mn.find_mentions("cb 650r")], ["cb"])

    def test_poryadok_po_tekstu_a_ne_po_slovaryu(self):
        # «pcx» стои́т в словаре РАНЬШЕ «forza», но в тексте — позже.
        self.assertEqual([m["root"] for m in mn.find_mentions("forza или pcx")],
                         ["forza", "pcx"])

    def test_data_i_srok_ne_kubatura(self):
        # Число за именем — не всегда объём: «nmax 10 июля» не имеет права стать «NMAX 10».
        for s in ("нужен nmax 10 июля", "хочу xmax на 5 дней", "forza с 6 по 11"):
            v = resolve(s)
            self.assertIn(v["outcome"], (mn.OK, mn.HUMAN), s)
            self.assertIsNone(mn.find_mentions(s)[0]["cc"], s)


class TestBlindness(unittest.TestCase):
    """K2–K5: разрыв в написании, кириллица со склонением, гомоглифы."""

    def test_razryv_v_napisanii(self):
        for s in ("x-max", "x max", "н-макс", "мт 03", "X-ADV-750"):
            self.assertNotEqual(resolve(s)["outcome"], mn.NONE, s)

    def test_kirillica_iz_slovarya_bota(self):
        # Замер: «адв», «хмакс» знал ТОЛЬКО фильтр сетки, до цены они не доезжали.
        self.assertEqual(resolve("адв 350")["model"], "ADV 350")
        self.assertEqual(resolve("хмакс 300")["reason"], mn.GENERATION)

    def test_sklonenie(self):
        # Живые формы замера: «форзу», «форзы» синоним целым словом не ловил.
        for s in ("форза", "форзу", "форзы", "форзе"):
            self.assertEqual(resolve(s)["model"], "FORZA 300", s)
        self.assertEqual(resolve("вулкана")["model"], "VULCAN 650S")
        self.assertEqual(resolve("ниндзю")["model"], "NINJA 400")
        self.assertEqual(resolve("клика")["model"], "CLICK 125")

    def test_gomoglify(self):
        # «рсх» — PCX русскими буквами (в парке PCX нет → честный not_in_park, а не молчание);
        # «МТ» — MT-03. Оба в замере кончались выкладкой всей сетки.
        v = resolve("рсх")
        self.assertEqual(v["outcome"], mn.NOT_IN_PARK)
        self.assertEqual(v["canon"], "PCX")
        self.assertEqual(resolve("МТ")["model"], "MT-03")

    def test_gomoglif_cb_zapreschyon(self):
        # «св» — русское сокращение, а не имя модели: корню cb гомоглифы закрыты намеренно.
        self.assertEqual(resolve("св 300")["outcome"], mn.NONE)


class TestHumanOutcome(unittest.TestCase):
    """Пункт 4 задания: неоднозначность → «зову человека» ОТДЕЛЬНЫМ исходом."""

    def test_dve_kubatury_odnogo_kornya(self):
        # K7: голый «cb» — единственная развилка живого парка, и до неё раньше не доезжали.
        v = resolve("нужен cb")
        self.assertEqual((v["outcome"], v["reason"]), (mn.HUMAN, mn.TWO_CC))
        self.assertEqual(sorted(v["options"]), ["CB 300R", "CB 650R"])
        self.assertIsNone(v["model"])

    def test_dva_pokoleniya(self):
        # K8: девять реплик замера — все XMAX; решение принималось молча.
        for s in ("xmax", "XMAX 300", "хмакс"):
            v = resolve(s)
            self.assertEqual((v["outcome"], v["reason"]), (mn.HUMAN, mn.GENERATION), s)
            self.assertEqual(len(v["options"]), 2, s)

    def test_pokolenie_nazvano_klientom_razvilki_net(self):
        self.assertEqual(resolve("старый xmax", gen_cue=True)["outcome"], mn.OK)

    def test_odno_pokolenie_v_parke_razvilki_net(self):
        self.assertEqual(mn.resolve("xmax", present=PARK,
                                    generations={"xmax300": ["XMAX 300 New Gen"]})["outcome"],
                         mn.OK)

    def test_vse_neodnoznachnye_ishody_zovut_cheloveka(self):
        for s in ("нужен cb", "xmax", "Cbr500r", "pcx", "XSR 900"):
            self.assertTrue(mn.needs_human(resolve(s)), s)

    def test_ni_odin_ishod_ne_molchanie(self):
        """«Не догадка» не означает «молчание»: у каждого исхода есть ИМЯ и причина."""
        for s in ("нужен cb", "xmax", "Cbr500r", "pcx", "X-ADV-750", "какие цены?"):
            v = resolve(s)
            self.assertIn(v["outcome"], (mn.OK, mn.HUMAN, mn.NOT_IN_PARK,
                                         mn.NOT_IN_CATALOG, mn.NONE, mn.PARK_UNKNOWN), s)

    def test_park_nedostupen_tretiy_ishod(self):
        # Парка нет → «судить нечем», а НЕ «модели нет» (fail-safe, как у resolve_park_model).
        self.assertEqual(mn.resolve("nmax", present=None)["outcome"], mn.PARK_UNKNOWN)


class TestNoGuessing(unittest.TestCase):
    """K9: опечатка вердикта не получает — нечёткого сравнения в правиле НЕТ сознательно."""

    def test_opechatka_ne_ugadyvaetsya(self):
        # Дословная опечатка черновика id 413.
        self.assertEqual(resolve("Honda ADB350")["outcome"], mn.NONE)

    def test_v_module_net_nechyotkogo_sravneniya(self):
        # Ищем ВЫЗОВ и ИМПОРТ, а не слово: сами эти имена в шапке модуля названы — там объяснено,
        # почему их там нет. Проверка по подстроке краснела бы на собственном объяснении.
        src = _source(mn)
        self.assertIsNone(re.search(r"^\s*(?:import|from)\s+(?:difflib|Levenshtein)", src, re.M))
        for call in ("get_close_matches(", "SequenceMatcher(", "ratio("):
            self.assertNotIn(call, src, call)


class TestPurity(unittest.TestCase):
    """MODEL_NAME_PURE: правило — чистая функция. Ни сети, ни диска, ни обратного импорта."""

    def test_module_ne_importiruet_boevye(self):
        # Импорты — по строкам импорта (см. соседний тест: проверка по подстроке краснеет на
        # собственных объяснениях модуля). Разрешён ровно один модуль — `re`.
        src = _source(mn)
        imports = re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src, re.M)
        self.assertEqual(sorted(set(imports)), ["re"])
        for io_call in ("open(", "subprocess", "urlopen", "socket"):
            self.assertNotIn(io_call, src, io_call)

    def test_odin_i_tot_zhe_vhod_odin_i_tot_zhe_verdikt(self):
        for s in ("X-ADV-750", "нужен cb", "форзу"):
            self.assertEqual(resolve(s), resolve(s), s)


class TestCatalogLocks(unittest.TestCase):
    """Дубли каталога и словаря canon'ов НАМЕРЕННЫ (чистота модуля) — замок против расхождения."""

    def test_catalog_zerkalit_suggest_known_models(self):
        self.assertEqual(list(mn.CATALOG), list(suggest.KNOWN_MODELS))

    def test_canon_tokens_zerkalyat_suggest_model_tokens(self):
        self.assertEqual(list(mn.CANON_TOKENS), list(suggest._MODEL_TOKENS))

    def test_kornei_hvataet_na_ves_katalog(self):
        for _disp, key in mn.CATALOG:
            self.assertTrue(any(key in fam.values() for fam in mn.FAMILIES.values()), key)


class TestWiredIntoSuggest(unittest.TestCase):
    """Правило вживлено: боевые точки входа говорят его вердиктом, словарь canon'ов не сломан."""

    def test_detect_model_bolshe_ne_dayot_chuzhuyu(self):
        self.assertEqual(suggest._detect_model("x-adv-750"), "XADV")
        self.assertEqual(suggest._detect_model("xadv750"), "XADV")
        self.assertEqual(suggest._detect_model("cbr500r"), "CBR500R")   # не «CBR» → не CBR 650R
        self.assertIsNone(suggest._detect_model("advise me"))

    def test_detect_model_beryot_pervuyu_po_tekstu(self):
        self.assertEqual(suggest._detect_model("forza или nmax"), "FORZA")

    def test_detect_model_vidit_slepye_formy(self):
        for s, want in (("x-max", "XMAX"), ("хмакс 300", "XMAX"), ("форзу", "FORZA"),
                        ("рсх", "PCX"), ("мт", "MT-03"), ("н-макс", "NMAX")):
            self.assertEqual(suggest._detect_model(s), want, s)

    def test_goliy_cb_doezzhaet_do_razvilki_parka(self):
        # K7: canon «CB» — вход в ветку `ambiguous` resolve_park_model, до которой не доходили.
        self.assertEqual(suggest._detect_model("нужен cb"), "CB")
        self.assertEqual(suggest.resolve_park_model("CB", getter=self._fleet)[0], "ambiguous")

    @staticmethod
    def _fleet(action, **kw):
        names = ["CB 300CC R 9011", "CB 650CC R 9012", "NMAX 155CC 1001"]
        return {"ok": True, "bikes": [{"name": n, "available": True} for n in names]}

    def test_slovar_canonov_ne_sloman(self):
        # Те же ответы, что и до правила (голдены #274 и test_pricing).
        self.assertEqual(suggest._detect_models("nmax и pcx на 10.07-17.07"), ["NMAX", "PCX"])
        self.assertEqual(suggest._detect_models("adv 350 на 10.07-17.07"), ["ADV350"])
        self.assertEqual(suggest.extract_requested_models("PCX 160cc есть?"),
                         [{"type": "model", "canon": "PCX"}])
        self.assertEqual(suggest.extract_requested_models("adv 160cc есть?"),
                         [{"type": "model", "canon": "ADV160"}])
        self.assertEqual(suggest.extract_requested_models("Есть ли MT-03 на июль?"),
                         [{"type": "model", "canon": "MT-03"}])

    def test_setka_teper_vidit_slepye_formy(self):
        # K3/K4/K5 в фильтре сетки: склонение и гомоглифы раньше давали None.
        for s, want in (("форзу на неделю", "FORZA"), ("рсх свободен?", "PCX"),
                        ("хочу x-max", "XMAX")):
            got = suggest.extract_requested_models(s)
            self.assertEqual([it["canon"] for it in got or []], [want], s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
