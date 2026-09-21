# -*- coding: utf-8 -*-
"""Регресс ожидания «пробег получен». Гоняется на полосе ПК, живого дерева не трогает.

Наружу не уходит ничего: ни моста, ни Telegram, ни файлов. Весь вход — литералы.
Живые числа фикстур взяты из замеров 69u/69v/69w (21.09.2026), а не придуманы.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import odo_expect as OE  # noqa: E402

# --- ЖИВАЯ ФИКСТУРА: VULCAN 650 S 5065, тема 81, 21.09.2026 (69w п.2) ---------
BIKE = "VULCAN 650 S 5065"
CHAT = -1001234567890
TOPIC = 81
ASK1 = "2026-09-21T04:50:04Z"   # просьба №1, пол ответа
ASK6 = "2026-09-21T06:20:56Z"   # просьба №6, пол ответа — 91 мин спустя
NOW = "2026-09-21T06:22:00Z"
# штамп одометра у другого байка, лежащий с 12.09 (69u п.5) — «уже было»
OLD_STAMP = "2026-09-12T04:25:28Z"

OK_SUB = {"ok": True, "src": OE.SRC_SUB, "km": "12212", "updated_at_utc": None}


def code_only():
    """Исходник МИНУС докстринг модуля и минус строки-комментарии.

    Сверять запреты по всему файлу нельзя: докстринг НАЗЫВАЕТ то, чего в коде быть
    не должно («моста нет», «SROK_S = None»), и наивный поиск краснел бы на объяснении
    вместо кода. Это не придирка — на первом прогоне 22.09 так и вышло: 2 FAIL из 25,
    оба на объяснении.
    """
    with open(OE.__file__, encoding="utf-8") as f:
        src = f.read()
    body = src.split('"""', 2)[2]          # всё после закрывающих кавычек докстринга
    return "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))


def exp6():
    """Ожидание после шести просьб — ровно так, как его завёл бы живой путь."""
    e = OE.open_expect(CHAT, TOPIC, BIKE, ASK1, "пол ответа")
    for t in ("2026-09-21T04:54:11Z", "2026-09-21T04:54:16Z",
              "2026-09-21T06:15:37Z", "2026-09-21T06:17:16Z", ASK6):
        e = OE.merge_repeat(e, t)
    return e


class TestSlovar(unittest.TestCase):
    def test_outcomes_ровно_три_и_отказа_среди_них_нет(self):
        self.assertEqual(len(OE.OUTCOMES), 3)
        for bad in ("просрочено", "в порядке", "ok", "overdue", "просрочка"):
            self.assertNotIn(bad, OE.OUTCOMES)

    def test_срок_не_назначен_и_это_видно_полем(self):
        self.assertIsNone(OE.SROK_S)
        self.assertEqual(OE.SROK_ZAMER_PAR, 0)
        self.assertTrue(OE.SROK_CHEM_ZAMERIT.strip())

    def test_src_words_are_park_verdict_vocabulary(self):
        # дословно те три слова, что живут в park_verdict.py:70-73 (68u п.1)
        self.assertEqual(OE.SRC_OWN, "свой одометр")
        self.assertEqual(OE.SRC_SUB, "подстановка максимума регистров")
        self.assertEqual(OE.SRC_UNKNOWN, "неизвестно")

    def test_в_исходнике_нет_ни_одного_порога_секунд(self):
        # круглое число из головы — запрещено; присваивание срока ровно одно и оно None
        code = code_only()
        self.assertEqual(code.count("SROK_S ="), 1, "присваиваний SROK_S не одно")
        self.assertIn("SROK_S = None", code)


class TestZ1_IstochnikAMeneOtchyot(unittest.TestCase):
    """З1: годен только СВОЙ одометр; подстановка признаком не является."""

    def test_подстановка_не_поднимает_признак(self):
        v = OE.verdict(exp6(), NOW, OK_SUB)
        self.assertEqual(v["outcome"], OE.OUT_ZHDYOM)
        self.assertIn("не измерение", v["why"])

    def test_свой_одометр_после_просьбы_поднимает(self):
        f = {"ok": True, "src": OE.SRC_OWN, "km": "31040",
             "updated_at_utc": "2026-09-21T06:21:00Z"}
        v = OE.verdict(exp6(), NOW, f)
        self.assertEqual(v["outcome"], OE.OUT_POLUCHEN)
        self.assertGreater(v["delay_s"], 0)

    def test_источник_не_назван_закрытое_умолчание(self):
        for src in (None, "", "ok", "измерено"):
            v = OE.verdict(exp6(), NOW, {"ok": True, "src": src,
                                         "updated_at_utc": NOW})
            self.assertEqual(v["outcome"], OE.OUT_NEIZVESTNO, src)
            self.assertIn("не названо", v["why"])


class TestZ2_NeSovershivSobytiya(unittest.TestCase):
    """З2: признак обязан требовать ВРЕМЯ позже просьбы.

    Без этого он поднимается у любого байка, у которого штамп уже лежал, — таких
    в живом парке 9 из 38 (69u п.5), и ни один из них события не совершал.
    """

    def test_старый_штамп_признаком_не_является(self):
        f = {"ok": True, "src": OE.SRC_OWN, "km": "16433",
             "updated_at_utc": OLD_STAMP}
        v = OE.verdict(exp6(), NOW, f)
        self.assertEqual(v["outcome"], OE.OUT_ZHDYOM)
        self.assertIn("старше просьбы", v["why"])

    def test_девять_живых_штампов_все_старше_просьбы(self):
        # поимённо из 69u п.5 — все девять лежали ДО 21.09 04:50
        stamps = ["2026-09-12T04:25:28Z", "2026-09-12T06:13:33Z",
                  "2026-09-13T05:33:13Z", "2026-09-14T06:51:27Z",
                  "2026-09-15T09:38:43Z", "2026-09-16T03:45:02Z",
                  "2026-09-18T09:23:42Z", "2026-09-18T09:32:37Z",
                  "2026-09-19T04:21:47Z"]
        got = [OE.verdict(exp6(), NOW, {"ok": True, "src": OE.SRC_OWN,
                                        "updated_at_utc": s})["outcome"]
               for s in stamps]
        self.assertEqual(got.count(OE.OUT_POLUCHEN), 0)
        self.assertEqual(got.count(OE.OUT_ZHDYOM), 9)

    def test_ровно_на_границе_не_засчитывается(self):
        f = {"ok": True, "src": OE.SRC_OWN, "updated_at_utc": ASK1}
        self.assertEqual(OE.verdict(exp6(), NOW, f)["outcome"], OE.OUT_ZHDYOM)


class TestOtricatelnyTest(unittest.TestCase):
    """Механик написал число в ЧАТ, а в источник не легло → прибор ОБЯЗАН отказать.

    Живой случай 21.09: шесть просьб, `vision conf=low` и числа в тексте, запись
    `_odo_store` не состоялась ни разу, `#l` в hint_dedup нет (69w п.2).
    """

    def test_число_в_чате_вердикта_не_меняет(self):
        base = OE.verdict(exp6(), NOW, OK_SUB)
        # у verdict() нет ни одного входа под текст сообщения — это и есть замок
        import inspect
        args = list(inspect.signature(OE.verdict).parameters)
        self.assertEqual(args, ["exp", "now_utc", "fact"])
        for bad in ("text", "message", "reply", "chat", "answer", "mechanic"):
            self.assertNotIn(bad, args)
        self.assertEqual(base["outcome"], OE.OUT_ZHDYOM)

    def test_шесть_просьб_в_чате_источник_пуст_итог_ждём(self):
        f = {"ok": True, "src": OE.SRC_UNKNOWN, "km": "", "updated_at_utc": None}
        v = OE.verdict(exp6(), NOW, f)
        self.assertEqual(v["outcome"], OE.OUT_ZHDYOM)
        self.assertEqual(exp6()["repeats"], 6)


class TestZ3_TretiyIskhod(unittest.TestCase):
    """З3: «проверить невозможно» → НЕИЗВЕСТНО с причиной. Намеренные состояния."""

    def test_мост_молчит(self):
        v = OE.verdict(exp6(), NOW, {"ok": False, "why": "мост не ответил"})
        self.assertEqual(v["outcome"], OE.OUT_NEIZVESTNO)
        self.assertIn("мост не ответил", v["why"])

    def test_у_измерения_нет_времени(self):
        f = {"ok": True, "src": OE.SRC_OWN, "km": "31040", "updated_at_utc": None}
        v = OE.verdict(exp6(), NOW, f)
        self.assertEqual(v["outcome"], OE.OUT_NEIZVESTNO)
        self.assertIn("не сравнить", v["why"])

    def test_часов_нет(self):
        f = {"ok": True, "src": OE.SRC_OWN, "updated_at_utc": NOW}
        v = OE.verdict(exp6(), None, f)
        self.assertEqual(v["outcome"], OE.OUT_NEIZVESTNO)
        self.assertIn("часов нет", v["why"])

    def test_у_просьбы_нет_времени(self):
        e = OE.open_expect(CHAT, TOPIC, BIKE, None, "пол ответа")
        v = OE.verdict(e, NOW, {"ok": True, "src": OE.SRC_OWN,
                                "updated_at_utc": NOW})
        self.assertEqual(v["outcome"], OE.OUT_NEIZVESTNO)

    def test_ожидания_нет_вовсе(self):
        self.assertEqual(OE.verdict(None, NOW, {"ok": True})["outcome"],
                         OE.OUT_NEIZVESTNO)

    def test_ни_одно_неизвестно_не_называется_получен(self):
        states = [
            ({"ok": False, "why": "мост"}, NOW),
            ({"ok": True, "src": OE.SRC_OWN, "updated_at_utc": None}, NOW),
            ({"ok": True, "src": OE.SRC_OWN, "updated_at_utc": NOW}, None),
            ({"ok": True, "src": "хз", "updated_at_utc": NOW}, NOW),
            ({"ok": True, "src": OE.SRC_OWN, "updated_at_utc": "не дата"}, NOW),
        ]
        outs = [OE.verdict(exp6(), n, f)["outcome"] for f, n in states]
        self.assertEqual(outs.count(OE.OUT_POLUCHEN), 0)
        self.assertEqual(outs.count(OE.OUT_NEIZVESTNO), 5)


class TestZ4_BezOtkazaTolkoVozrast(unittest.TestCase):
    """З4: пока срок не замерен, прибор показывает возраст и не выносит приговора."""

    def test_возраст_показан_числом(self):
        v = OE.verdict(exp6(), NOW, OK_SUB)
        self.assertAlmostEqual(v["age_s"], 91.93 * 60, delta=60)

    def test_отказа_нет_ни_при_каком_возрасте(self):
        for now in ("2026-09-21T06:22:00Z", "2026-09-22T06:22:00Z",
                    "2026-10-21T06:22:00Z"):
            v = OE.verdict(exp6(), now, OK_SUB)
            self.assertEqual(v["outcome"], OE.OUT_ZHDYOM)
            self.assertIsNone(v["srok_s"])


class TestZ5_IdempotentnostKlyucha(unittest.TestCase):
    """З5: шесть просьб — ОДНО ожидание, и возраст считается от первой."""

    def test_ключ_не_содержит_времени(self):
        k = OE.key(CHAT, TOPIC, BIKE)
        self.assertEqual(k, ("одометр", CHAT, TOPIC, BIKE))
        self.assertEqual(len({OE.key(CHAT, TOPIC, BIKE) for _ in range(6)}), 1)

    def test_шесть_просьб_дают_одно_ожидание(self):
        e = exp6()
        self.assertEqual(e["repeats"], 6)
        self.assertEqual(e["key"], OE.key(CHAT, TOPIC, BIKE))

    def test_повтор_не_обнуляет_возраст(self):
        e = exp6()
        self.assertEqual(e["first_asked_at_utc"], ASK1)
        self.assertEqual(e["asked_at_utc"], ASK6)
        v = OE.verdict(e, NOW, OK_SUB)
        self.assertGreater(v["age_s"], 60 * 60)  # 91 мин, а не 3.7


class TestZapisOzhidaniya(unittest.TestCase):
    """Пять полей, которых у просьбы не было ни одного (69w п.2)."""

    def test_имя_предмет_время_адресат_вид(self):
        e = OE.open_expect(CHAT, TOPIC, BIKE, ASK1, "пол ответа")
        self.assertEqual(e["name"], "пробег байка")
        self.assertEqual(e["predmet"], BIKE)
        self.assertEqual(e["asked_at_utc"], ASK1)
        self.assertIn("тема байка", e["addressee"])
        self.assertIn(e["ask_kind"], OE.ASK_KINDS)


class TestNaruzhuNichego(unittest.TestCase):
    """Путь до человека закрыт: в модуле нет ни сети, ни отправки, ни записи."""

    def test_v_module_net_dveri_naruzhu(self):
        code = code_only()
        for bad in ("send_message", "reply_text", "requests", "urllib",
                    "socket", "bridge", "open(", "subprocess", "os.environ"):
            self.assertNotIn(bad, code, "дверь наружу: " + bad)

    def test_edinstvenny_import_modulya_eto_datetime(self):
        imports = [l.strip() for l in code_only().splitlines()
                   if l.strip().startswith(("import ", "from "))]
        self.assertEqual(imports, ["import datetime"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
