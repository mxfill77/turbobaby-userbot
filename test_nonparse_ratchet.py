# -*- coding: utf-8 -*-
"""
test_nonparse_ratchet.py — ХРАПОВИК класса «НУЛЬ ПО НЕРАЗБОРУ» + ТЕСТ САМОГО СЧЁТЧИКА.

КЛАСС. Читатель живого текста отдаёт наверх честный на вид нуль и когда источник молчал, и когда
он сам не разобрал ни строки. Перепись полосы: `docs/artifacts/2026-08-08-zero-by-nonparse-pc-recon.md`
(проход по постоянным источникам — 48 мест, 24 слепых; механический свип точек разбора — 182/102).

ПОЧЕМУ ХРАПОВИК, А НЕ ЗАМОК ПРИ ИМПОРТЕ. Замок («каждый читатель обязан вернуть Reading»)
детонирует разом на сотне старых мест — такую правку нельзя ни закончить за заход, ни откатить по
частям. Храповик не требует чинить старое: он запрещает СТАВИТЬ НОВОЕ. Метод счёта закреплён кодом
(`nonparse_scan`), число — базовой линией `nonparse_baseline.json`, гейт краснеет при РОСТЕ.

ЛИНИЯ ПЕРЕСНЯТА 07.09.2026, И ВОТ ЧЕМ ЭТО ОПЛАЧЕНО. Линия стоя́ла с 08.08.2026 (46 файлов, 845
точек, 503 слепых читателя) и с тех пор не пересъёмывалась ни разу, потому что ПОЛНЫЙ набор не
гонял никто (`mode=full` в живом логе демона за 22.07–07.09 → 0 раз, замер `008228e`): храповик
краснел месяц в тесте, которого никто не спрашивал. Пересъёмка тем же инструментом
(`venv\\Scripts\\python.exe nonparse_scan.py --json --measured 2026-09-07`) — ШТАТНЫЙ ход храповика,
уже применявшийся коммитом `9153557`, и она НЕ трогает ни метод, ни строгость: и метод, и три
подписи, и область остались прежними, ни один файл линии из области не выпал (0 из 37).
Что именно принято новым полом — числом, а не общим словом: точек 845 → 2295, и эти 1450 точек
раскладываются на 1221 от 56 НОВЫХ файлов (их линия была нулевой) и 229 от СЕМИ старых, выросших
против своей строки: `pc_orchestrator.py` +91, `trainer_run.py` +49, `trainer.py` +42,
`pretool_guard.py` +15, `client_contour.py` +13, `dispatch_notify.py` +12, `pc_agent.py` +7.
Ни одного файла с УМЕНЬШЕНИЕМ нет. Эти 229 — ровно то, что храповик обязан был остановить и не
остановил; пересъёмка их не чинит, а ПРИЗНАЁТ, и теперь они видны числом, а не тонут в красном.

ТРИ СЛОЯ, И КАЖДЫЙ НУЖЕН:
  1. `TestRatchet` — рост против базовой линии (файл превысил свою строку / новый файл принёс
     точки / файл линии выпал из области) валит гейт;
  2. `TestCounterOnKnownReaders` — счётчик проверен на ИЗВЕСТНЫХ примерах слепого и зрячего
     читателя: он обязан ловить каждую форму схлопывания и НЕ ловить честные;
  3. `TestMethodCannotBeWeakened` — ЗАМОК ПРОТИВ ПОДЛОГА: ослабление метода (убрать подпись,
     сузить словарь пустоты, расширить список исключённых файлов, заглушить собственные сбои
     счётчика) обязано ронять ЭТОТ тест, а не тихо красить гейт зелёным.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_nonparse_ratchet -v
"""

import ast
import os
import unittest

import nonparse_scan as ns
import parse_outcome

HERE = os.path.dirname(os.path.abspath(__file__))


def _scan(src):
    return ns.scan_source(src, "<sample>")


def _sigs(src):
    return sorted(p.sig for p in _scan(src))


# --------------------------- ИЗВЕСТНЫЕ СЛЕПЫЕ ЧИТАТЕЛИ -------------------------
# Каждый образец — форма, которая ЖИВЁТ на полосе (ссылки — строки переписи). Убери из метода
# любую из них, и соответствующий тест покраснеет: в этом и состоит замок против подлога.
BLIND = (
    ("except → pass (тихий пропуск сбоя)",
     "def r(p):\n    try:\n        f(p)\n    except Exception:\n        pass\n", ns.SIG_SWALLOW),
    ("except → continue (битая строка молча пропущена; перепись, место 1)",
     "def r(ls):\n    for l in ls:\n        try:\n            g(l)\n        except Exception:\n"
     "            continue\n", ns.SIG_SWALLOW),
    ("except → return [] (нет файла ≡ записей нет)",
     "def r(p):\n    try:\n        return read(p)\n    except Exception:\n        return []\n",
     ns.SIG_SWALLOW),
    ("except → return '' (сбой ≡ пустой ответ)",
     "def r(p):\n    try:\n        return read(p)\n    except Exception:\n        return ''\n",
     ns.SIG_SWALLOW),
    ("except → return 0 (перепись, место 44: нет маркера ≡ маркер со значением 0)",
     "def r(p):\n    try:\n        return n(p)\n    except Exception:\n        return 0\n",
     ns.SIG_SWALLOW),
    ("except → return {} (перепись, место 4: битый json ≡ тиков ещё не было)",
     "def r(p):\n    try:\n        return j(p)\n    except Exception:\n        return {}\n",
     ns.SIG_SWALLOW),
    ("except → return False (перепись, место 27)",
     "def r(p):\n    try:\n        return h(p)\n    except Exception:\n        return False\n",
     ns.SIG_SWALLOW),
    ("except → return () / set() / dict()",
     "def r(p):\n    try:\n        return h(p)\n    except Exception:\n        return ()\n"
     "def r2(p):\n    try:\n        return h(p)\n    except Exception:\n        return set()\n",
     ns.SIG_SWALLOW),
    ("except → return [], offset, False (полезная нагрузка схлопнута в кортеже)",
     "def r(p, off):\n    try:\n        return t(p)\n    except Exception:\n"
     "        return [], off, False\n", ns.SIG_SWALLOW),
    ("(out or '').splitlines() — уничтожение УЖЕ СУЩЕСТВУЮЩЕГО различения (перепись, место 9)",
     "def r(out):\n    return [x for x in (out or '').splitlines()]\n", ns.SIG_OR_EMPTY),
    ("for line in lines or []",
     "def r(lines):\n    for line in lines or []:\n        g(line)\n", ns.SIG_OR_EMPTY),
    ("(state or {}).get(...) — перепись, место 18: слепой детектор говорит «отказов 0»",
     "def r(state):\n    return int((state or {}).get('failures', 0))\n", ns.SIG_OR_EMPTY),
    ("int(x or 0) / str(x or '') / len(x or ())",
     "def r(x):\n    return int(x or 0) + len(str(x or '')) + len(x or ())\n", ns.SIG_OR_EMPTY),
    ("x if x else [] — та же идиома тернарником (обход запрета на or)",
     "def r(x):\n    return [i for i in (x if x else [])]\n", ns.SIG_GUARD_EMPTY),
    ("x if x is not None else {} — то же с явной проверкой",
     "def r(x):\n    return (x if x is not None else {}).get('k')\n", ns.SIG_GUARD_EMPTY),
)

# --------------------------- ИЗВЕСТНЫЕ ЗРЯЧИЕ ЧИТАТЕЛИ -------------------------
# Эталоны полосы: третье состояние доехало наверх ЛИБО отказ произнесён вслух. Начни метод
# считать их слепыми — покраснеет здесь, и никто не станет «чинить» правильный код.
SIGHTED = (
    ("except → return None (эталон _git_out: «не знаю» ≠ «пусто»)",
     "def r(p):\n    try:\n        return read(p)\n    except Exception:\n        return None\n"),
    ("except → return None, offset, False (третье состояние живёт в кортеже)",
     "def r(p, off):\n    try:\n        return t(p)\n    except Exception:\n"
     "        return None, off, False\n"),
    ("except → сказал вслух и вернул пустое (лог есть — это не немота)",
     "def r(p):\n    try:\n        return read(p)\n    except Exception:\n"
     "        log.warning('не смог')\n        return []\n"),
    ("except → raise (громко)",
     "def r(p):\n    try:\n        return read(p)\n    except Exception:\n        raise\n"),
    ("x or 'непустое' — подстановка ЗНАЧЕНИЯ, а не пустоты",
     "def r(x):\n    return x or 'аноним'\n"),
    ("a if режим else [] — честная развилка (разные имена), а не схлопывание «не знаю»",
     "def r(a, режим):\n    return a if режим else []\n"),
    ("голый return / return None",
     "def r(x):\n    if x:\n        return\n    return None\n"),
)


class TestCounterOnKnownReaders(unittest.TestCase):
    """СЛОЙ 2: счётчик проверен на известных примерах — иначе его число не значит ничего."""

    def test_every_blind_sample_is_caught(self):
        for label, src, sig in BLIND:
            with self.subTest(label):
                sigs = _sigs(src)
                self.assertGreater(len(sigs), 0, "слепой читатель не пойман: %s" % label)
                self.assertIn(sig, sigs, "поймано не той подписью: %s" % label)

    def test_no_sighted_sample_is_caught(self):
        for label, src in SIGHTED:
            with self.subTest(label):
                self.assertEqual(_scan(src), (), "зрячий читатель назван слепым: %s" % label)

    def test_point_names_the_reader_and_the_line(self):
        """Точка обязана называть ЧИТАТЕЛЯ и строку — иначе её нечем править."""
        src = "def outer(x):\n    def inner(y):\n        return y or []\n    return inner(x)\n"
        pts = _scan(src)
        self.assertEqual(len(pts), 1)
        self.assertEqual((pts[0].func, pts[0].line, pts[0].sig), ("outer.inner", 3, ns.SIG_OR_EMPTY))

    def test_module_level_point_is_attributed(self):
        pts = _scan("VALUE = os.getenv('X') or ''\n")
        self.assertEqual([p.func for p in pts], [ns.MODULE_LEVEL])

    def test_readers_denominator_counts_functions(self):
        """Знаменатель доли — все читатели области, а не только слепые."""
        walk = ns._walk_source("def a():\n    pass\n\n\nclass K:\n    def b(self):\n        pass\n",
                               "<s>")
        self.assertEqual(sorted(walk.readers), sorted([ns.MODULE_LEVEL, "a", "K.b"]))


class TestMethodCannotBeWeakened(unittest.TestCase):
    """СЛОЙ 3: ЗАМОК ПРОТИВ ПОДЛОГА. Каждый способ «улучшить» число, не улучшая код, — красный."""

    def test_signature_set_is_exactly_the_agreed_one(self):
        """Убрать подпись из метода = молча обнулить целый вид слепоты."""
        self.assertEqual(ns.SIGNATURES, (ns.SIG_SWALLOW, ns.SIG_OR_EMPTY, ns.SIG_GUARD_EMPTY))

    def test_every_signature_still_fires(self):
        """У КАЖДОЙ подписи есть живой образец: подпись, не ловящая ничего, — мёртвая подпись."""
        caught = set()
        for _, src, sig in BLIND:
            caught.update(_sigs(src))
            del sig
        self.assertEqual(caught, set(ns.SIGNATURES))

    def test_empty_vocabulary_is_not_narrowed(self):
        """Словарь пустоты — часть договора: сузишь его, и целый класс схлопываний станет «зрячим»."""
        for text in ("''", '""', "b''", "0", "0.0", "False", "[]", "{}", "()",
                     "list()", "dict()", "set()", "tuple()", "frozenset()"):
            with self.subTest(text):
                node = ast.parse("X = " + text).body[0].value
                self.assertTrue(ns._is_empty_const(node), "перестал считаться пустотой: " + text)

    def test_none_is_never_empty(self):
        """`None` — честное «не знаю» и эталон третьего состояния. Считать его пустотой значит
        наказывать за правильную форму (`return None, offset, False`)."""
        self.assertFalse(ns._is_empty_const(ast.parse("X = None").body[0].value))
        self.assertFalse(ns._is_empty_const(ast.parse("X = 'что-то'").body[0].value))
        self.assertFalse(ns._is_empty_const(ast.parse("X = 1").body[0].value))

    def test_out_of_scope_is_exactly_the_frozen_three(self):
        """Список исключённых — граница, а не ручка громкости: расширил → красный."""
        self.assertEqual(ns.OUT_OF_SCOPE,
                         ("userbot_listen.py", "moderation_bot.py", "suggest.py"))

    def test_scope_is_tracked_root_only(self):
        """Область = отслеживаемые git .py КОРНЯ. Ни мин дерева, ни тестов, ни чужих копий."""
        files = ns.repo_files(HERE)
        self.assertIn("pc_orchestrator.py", files)
        self.assertIn("rc_auth_detect.py", files)
        for name in files:
            self.assertNotIn("/", name)
            self.assertNotIn("\\", name)
            self.assertFalse(name.startswith("test_"), name)
            self.assertNotIn(name, ns.OUT_OF_SCOPE)

    def test_counter_does_not_swallow_its_own_failures(self):
        """Счётчик, глотающий собственный сбой, отдал бы «нарушений ноль» — тот самый класс."""
        missing = ns.scan_file(os.path.join(HERE, "нет-такого-файла.py"))
        self.assertIsNone(missing.points, "нечитаемый файл обязан давать None, а не пустоту")
        self.assertFalse(missing.ok)
        broken = ns.scan_file(os.path.join(HERE, "fixtures", "rc_server_idle_poll.live.log"))
        self.assertIsNone(broken.points, "неразбираемый файл обязан давать None, а не пустоту")
        self.assertIn("SyntaxError", broken.err)

    def test_growth_detector_sees_a_worse_file(self):
        """Логика храповика — на синтетической линии, без зависимости от состояния репо."""
        res = ns.scan_repo(HERE)
        per = ns.by_file(res)
        victim = sorted(per)[0]
        lower = {"files": dict((f, dict(v)) for f, v in per.items())}
        lower["files"][victim] = {"readers": 0, "points": 0}
        self.assertTrue(any(victim in b for b in ns.growth(res, base=lower)))

    def test_growth_detector_sees_a_narrowed_method(self):
        """Файл базовой линии пропал из области → «метод сузили», а не «стало лучше»."""
        res = ns.scan_repo(HERE)
        base = {"files": dict(ns.by_file(res))}
        base["files"]["никогда-не-существовавший.py"] = {"readers": 1, "points": 1}
        bad = ns.growth(res, base=base)
        self.assertTrue(any("ВЫПАЛ из области" in b for b in bad), bad)

    def test_baseline_is_measured_by_this_very_method(self):
        """Линия и метод склеены строкой метода: тронул набор подписей — обнови линию замером."""
        base = ns.load_baseline()
        self.assertEqual(base["method"], "nonparse_scan/" + ",".join(ns.SIGNATURES))


class TestRatchet(unittest.TestCase):
    """СЛОЙ 1: сам храповик. Красный = кто-то поставил НОВОЕ слепое место."""

    @classmethod
    def setUpClass(cls):
        cls.res = ns.scan_repo(HERE)
        cls.base = ns.load_baseline()

    def test_no_growth_against_baseline(self):
        bad = ns.growth(self.res, base=self.base)
        self.assertEqual(bad, [], "ВЫРОСЛО число слепых читателей:\n  " + "\n  ".join(bad))

    def test_totals_do_not_grow(self):
        now, was = ns.totals(self.res), self.base["totals"]
        self.assertLessEqual(now["points"], was["points"])
        self.assertLessEqual(now["readers"], was["readers"])

    def test_counter_itself_is_not_blind(self):
        """Счётчик живёт по своему же контракту: осмотрено файлов > 0 и разобраны ВСЕ."""
        self.assertEqual(self.res.reading.outcome, parse_outcome.PARSED, self.res.reading.say())
        self.assertEqual(self.res.unreadable, (), "файлы области не считаны — это НЕ «чисто»")
        self.assertGreater(len(self.res.points), 0, "метод, не находящий НИЧЕГО, сломан")

    def test_converted_reader_stays_at_zero(self):
        """Переведённый на контракт читатель (rc_auth_detect) — 0 точек; откат туда = красный."""
        self.assertEqual([p for p in self.res.points if p.file == "rc_auth_detect.py"], [])
        self.assertNotIn("rc_auth_detect.py", self.base["files"])


if __name__ == "__main__":
    unittest.main()
