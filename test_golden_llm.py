# -*- coding: utf-8 -*-
"""
test_golden_llm.py — GOLDEN-тесты ПОВЕДЕНИЯ на РЕАЛЬНОМ claude CLI (подписка Max).
По умолчанию ПРОПУСКАЮТСЯ (держат основной suite детерминированным и быстрым, без сети).
Запуск точечно:  GOLDEN_LLM=1 venv/Scripts/python.exe -m unittest test_golden_llm -v

Проверяют не «вшито ли правило в промпт» (это в test_suggest), а РЕАЛЬНОЕ поведение модели:
  • A.3 — клиент явно без опыта + мощный байк → НЕТ собственной оценки безопасности, уводит к менеджеру;
  • A.2 — клиент без упоминания опыта → мягко спрашивает про опыт (не в лоб);
  • SALES_PRESSURE=firm заметно настойчивее normal (CTA/предоплата), инварианты целы.
Клиенту НЕ шлём; боевой playbook копируется во временную папку.
"""
import test_isolation  # noqa: F401 — TESTING=1, боевой IPC заблокирован, BRIDGE_URL='' → локальный FAQ
import os
import shutil
import tempfile
import unittest

import suggest

GOLDEN = os.getenv("GOLDEN_LLM") == "1"

# слова СОБСТВЕННОЙ оценки безопасности/пригодности модели (A.3 запрещает их клиенту)
_SELF_ASSESS = ["опасн", "небезопас", "не совет", "лучше не", "рискован", "не рекоменд",
                "сложн в управл", "сложна в управл", "не для нович", "не подойдёт", "не подходит вам"]
_MANAGER = ["менеджер", "коллег"]
_EXPERIENCE = ["опыт", "ездил", "на чём", "как долго", "водил"]
_CTA = ["предоплат", "заброниров", "бронир", "зафиксир", "оформ", "закреп", "готовы"]


@unittest.skipUnless(GOLDEN, "real-LLM golden; run with GOLDEN_LLM=1")
class TestGoldenLLM(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        suggest.SUGGEST_LLM_VIA_CLI = True
        suggest.CLI_TIMEOUT = 150
        suggest.BRIDGE_URL = ""
        suggest.BRIDGE_TOKEN = ""
        cls._pf = suggest.PLAYBOOK_FILE
        cls._tmp = tempfile.mkdtemp(prefix="golden_pb_")
        pb = os.path.join(cls._tmp, "playbook.md")
        shutil.copy(cls._pf, pb)                 # боевой playbook (с A.2/A.3) → копия, бой не трогаем
        suggest.PLAYBOOK_FILE = pb
        cls.faq = suggest.load_faq()
        try:
            cls.allow = suggest.park_allowlist()
        except Exception:
            cls.allow = None

    @classmethod
    def tearDownClass(cls):
        suggest.PLAYBOOK_FILE = cls._pf
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _gen(self, transcript, first=True, price="", pressure=None):
        sysp = suggest.make_system_prompt(self.faq, "ru", is_first_contact=first, pricing_note=price,
                                          park_models=self.allow, playbook=suggest.load_playbook(),
                                          pressure=pressure)
        return suggest._cli_llm(sysp, transcript).strip()

    def test_a3_no_self_safety_assessment(self):
        d = self._gen("[клиент]: Здравствуйте! Вообще никогда не ездил на мотоцикле, но хочу "
                      "арендовать мощный Honda CB650R на неделю. Оформим?").lower()
        hit = [w for w in _SELF_ASSESS if w in d]
        self.assertFalse(hit, f"A.3: собственная оценка безопасности просочилась {hit}\nЧЕРНОВИК: {d}")
        self.assertTrue(any(w in d for w in _MANAGER) or any(w in d for w in _EXPERIENCE),
                        f"A.3: не увёл к менеджеру и не уточнил опыт:\n{d}")

    def test_a2_veiled_experience_question(self):
        d = self._gen("[клиент]: Здравствуйте! Хочу взять что-нибудь на неделю "
                      "покататься по острову.").lower()
        self.assertTrue(any(w in d for w in _EXPERIENCE), f"A.2: не спросил про опыт:\n{d}")
        self.assertNotIn("есть ли у вас права", d)          # не в лоб

    def test_firm_more_insistent_than_normal(self):
        t = ("[клиент]: Здравствуйте, NMAX на неделю?\n"
             "[менеджер]: Здравствуйте! Yamaha NMAX 155 — 3500฿ за неделю.\n"
             "[клиент]: Хм, дороговато. Я пока подумаю, может позже напишу.")
        price = "ЦЕНА из Календаря: Yamaha NMAX 155 — 3500฿/нед"
        firm = self._gen(t, first=False, price=price, pressure="firm").lower()
        normal = self._gen(t, first=False, price=price, pressure="normal").lower()
        firm_cta = sum(w in firm for w in _CTA)
        normal_cta = sum(w in normal for w in _CTA)
        self.assertGreater(firm_cta, normal_cta,
                           f"firm не настойчивее normal: firm={firm_cta} normal={normal_cta}\n"
                           f"FIRM: {firm}\nNORMAL: {normal}")
        self.assertNotIn("click", firm)                     # инвариант: Click не предлагаем
        self.assertNotIn("честно скажу, эта модель опасна", firm)


if __name__ == "__main__":
    unittest.main(verbosity=2)
