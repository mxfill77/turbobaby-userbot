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
import pc_orchestrator as o

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


@unittest.skipUnless(GOLDEN, "real-LLM golden; run with GOLDEN_LLM=1")
class TestGoldenRevizor(unittest.TestCase):
    """Шаг 5/7 родителя 262 — GOLDEN думателя-РЕВИЗОРА на РЕАЛЬНОМ claude CLI, фикстуры из живых
    окон 13.07.2026. Прогоняем реальный аудитор (_revizor_consult → _thinker_exec, без мока) и
    проверяем, что он ловит РОВНО тот класс дефекта, что был в живом провале — и НЕ путает
    служебный канал карточки модератору с утечкой клиенту. Проверяем ПОВЕДЕНИЕ модели по чек-листу,
    а не «вшита ли буква в промпт» (это в test_pc_orchestrator). Клиенту НИЧЕГО не шлём: пакеты —
    статичные фикстуры, ревизор read-only по построению (_thinker_exec: --max-turns 1, tools='')."""

    def _classes(self, package, msg):
        """Прогон реального думателя-ревизора по пакету → множество классов находок. None (сбой CLI/
        таймаут/нераспарсенный ответ) — жёсткий провал теста с диагнозом (голден без CLI бессмыслен)."""
        findings = o._revizor_consult(package)
        self.assertIsNotNone(findings, f"{msg}: думатель-ревизор не ответил (CLI/таймаут/парс)")
        return {f.get("class") for f in findings}, findings

    def test_revizor_flags_questionnaire_on_first_msg_with_model_dates(self):
        # ж) КЕЙС 18:52 (живой провал 13.07): клиент ПЕРВЫМ же сообщением дал модель (Honda ADV350)
        # И даты (15–22 июля), а мы в ответ — анкету/список вопросов вместо тарифа. Надо было
        # котировать, а не допрашивать → находка класса ж (дословная реплика клиента в фикстуре).
        first = "Здравствуйте! Хочу арендовать Honda ADV350 с 15 по 22 июля. Сколько будет стоить?"
        pkg = {"client_id": 1852, "client_name": "Максим",
               "incoming": [first],
               "transcript": f"[client]: {first}",
               "sent": ["Здравствуйте! Чтобы подобрать вариант, уточните, пожалуйста:\n"
                        "1) какая модель вас интересует?\n2) на какие даты аренда?\n"
                        "3) есть ли опыт вождения?\n4) где вы находитесь?"],
               "drafts": [], "last_ts": "2026-07-13T18:52:00+00:00"}
        classes, findings = self._classes(pkg, "ж")
        self.assertIn("ж", classes,
                      f"ревизор НЕ поймал анкету на первом сообщении с моделью+датами: {findings}")

    def test_revizor_flags_sobrano_leak_in_sent(self):
        # г) КЕЙС «[собрано:…]» просочилось в ОТПРАВЛЕННЫЙ клиенту текст (живой провал 13.07):
        # служебный маркер карточки утёк в тело ответа — клиент видит внутреннюю кухню → класс г.
        # Форма маркера дословная из collected_manager_note (suggest.py): «[собрано: … ✅ …]».
        tr = ("[менеджер]: Скиньте гео, паспорт и телефон\n"
              "[client]: Вот всё\n[менеджер]: Спасибо!")
        pkg = {"client_id": 4201, "client_name": "Игорь",
               "incoming": ["Вот моя локация, паспорт и номер"],
               "transcript": tr,
               "sent": ["Спасибо, всё получил! [собрано: гео ✅ паспорт ✅ тел ✅] "
                        "Передаю менеджеру для оформления."],
               "drafts": [], "last_ts": "2026-07-13T15:10:00+00:00"}
        classes, findings = self._classes(pkg, "г")
        self.assertIn("г", classes,
                      f"ревизор НЕ поймал утечку «[собрано:…]» в отправленном клиенту: {findings}")

    def test_revizor_no_leak_when_sobrano_only_in_moderator_card(self):
        # г-КОНТРАСТ: ТА ЖЕ строка «[собрано:…]» живёт ТОЛЬКО в черновике-карточке модератору (draft),
        # а в ОТПРАВЛЕННОМ клиенту тексте её нет — это штатный служебный канал, НЕ утечка. Ревизор
        # обязан различать sent (клиент) и draft (модератор) → находки класса г быть НЕ должно.
        tr = ("[менеджер]: Скиньте гео, паспорт и телефон\n"
              "[client]: Вот всё\n[менеджер]: Спасибо!")
        pkg = {"client_id": 4202, "client_name": "Игорь",
               "incoming": ["Вот моя локация, паспорт и номер"],
               "transcript": tr,
               "sent": ["Спасибо, всё получил! Передаю менеджеру для оформления."],  # клиенту — чисто
               "drafts": ["Спасибо, всё получил! Передаю менеджеру для оформления.\n"
                          "[собрано: гео ✅ паспорт ✅ тел ✅]"],                     # карточка — норма
               "last_ts": "2026-07-13T15:20:00+00:00"}
        classes, findings = self._classes(pkg, "г-контраст")
        self.assertNotIn("г", classes,
                         f"ревизор ложно счёл служебную пометку карточки утечкой клиенту: {findings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
