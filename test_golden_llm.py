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
import re
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
# Маркеры НАСТОЙЧИВОСТИ firm-режима — ровно те рычаги, что предписывает _pressure_block:
# (1) явный CTA к брони/предоплате/фиксации + ближайший шаг; (2) правдивый дефицит
# «популярные даты/байки быстро разбирают». Стем «брон» (не «бронир») ловит и «бронью/бронь».
# Меряем ОБЕ стороны (firm и normal) одним набором → сравнение честное, сетка не ослаблена.
_CTA = ["предоплат", "заброниров", "брон", "зафиксир", "оформ", "закреп", "готовы",
        "ближайш", "следующ шаг", "конкретн шаг",
        "разбирают", "не затягив", "не тяните", "успе", "популярн", "не упуст", "поспеш"]


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

    def test_collected_no_reask_after_three_vot(self):
        # §243/6 голден 12.07: клиент тремя «Вот» реплаями прислал гео/паспорт/телефон (подтянуто
        # в транскрипт). Черновик ПОДТВЕРЖДАЕТ получение и НЕ переспрашивает то же самое.
        tr = ("[менеджер]: Скиньте гео, паспорт и телефон\n"
              "[клиент]: Вот ↩[в ответ на — клиент: Моя вилла: https://maps.app.goo.gl/abc123XYZ]\n"
              "[клиент]: Вот ↩[в ответ на — клиент: фото (вероятно паспорт)]\n"
              "[клиент]: Вот ↩[в ответ на — клиент: Мой номер +66 81 234 5678]")
        facts = suggest.collected_facts(tr)
        sysp = suggest.make_system_prompt(self.faq, "ru", is_first_contact=False,
                                          park_models=self.allow, playbook=suggest.load_playbook(),
                                          collected=facts)
        d = suggest._cli_llm(sysp, tr).strip().lower()
        # RE-ASK = ПРОСЬБА прислать заново (императив/«нужно/нужен» + сущность), а НЕ упоминание
        # сущности в ПОДТВЕРЖДЕНИИ получения («…локацию, фото паспорта и номер получил»). Живой
        # провал матчера-подстроки 13.07: голая «фото паспорта» ловилась внутри корректного
        # подтверждения. Ловим связку request-глагол↔сущность в любом порядке рядом; глаголы
        # приёма (получил/принял/есть) в _REQ не входят → подтверждение не триггерит (тест ≠ реальность).
        _REQ = r"(?:скинь\w*|пришли\w*|отправь\w*|укажи\w*|напиши\w*|дай(?:те)?|нужн\w*|нужен|поделит\w*)"
        _ENT = r"(?:гео|локаци\w*|адрес|паспорт\w*|телефон\w*|номер\w*)"
        reask_re = re.compile(_REQ + r"[^.!?\n]{0,40}?" + _ENT + r"|" +
                              _ENT + r"[^.!?\n]{0,20}?" + _REQ, re.I)
        m = reask_re.search(d)
        hit = m.group(0) if m else None
        self.assertIsNone(m, f"§243/6: черновик переспросил уже собранное "
                             f"{hit!r}\nЧЕРНОВИК: {d}")
        self.assertTrue(any(w in d for w in ["получил", "приняли", "принял", "есть", "спасибо"]),
                        f"§243/6: не подтвердил получение данных:\n{d}")

    def test_style_step6_no_regreet_no_kanc_short(self):
        # шаг 6/7 #253 стилевой голден: ПРОДОЛЖЕНИЕ диалога (приветствие уже уходило) →
        # без повторного приветствия, без канцелярита, ≤3 коротких абзаца; клиент уже
        # назвал скутер (NMAX) → опыт на скутерах, «на чём ездили» повторно НЕ спрашиваем.
        tr = ("[менеджер]: Здравствуйте! Что хотели бы арендовать и на какие даты?\n"
              "[клиент]: Раньше катал NMAX, хочу такой же на неделю. Оплата картой можно?")
        d = self._gen(tr, first=False).strip()
        low = d.lower()
        # 1) без повторного приветствия в начале ответа
        head = d.lstrip("!.,:;-—–()«\"' \t\n")
        self.assertIsNone(suggest._GREETING_RE.match(head),
                          f"повторное приветствие в начале: {d}")
        # 2) без канцелярита
        for kanc in ("спасибо за информацию", "принято к сведению", "зафиксировала запрос",
                     "записала ваш запрос", "уточню и вернусь с расчётом"):
            self.assertNotIn(kanc, low, f"канцелярит просочился ({kanc!r}): {d}")
        # 3) не переспрашивает опыт на скутерах в лоб (он уже дан)
        for reask in ("на чём ездили", "на чем ездили", "какой был опыт", "какие модели были"):
            self.assertNotIn(reask, low, f"переспросил уже данный опыт ({reask!r}): {d}")
        # 4) ≤3 коротких абзаца (тон STYLE_GUIDE: коротко и по делу)
        paras = [p for p in re.split(r"\n\s*\n", d) if p.strip()]
        self.assertLessEqual(len(paras), 3, f"больше 3 абзацев (простыня): {d}")

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
