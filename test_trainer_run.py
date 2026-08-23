# -*- coding: utf-8 -*-
"""
test_trainer_run.py — ГОЛДЕНЫ безголового прогона тренажёра и ВЕРДИКТА для ворот.

Живого LLM здесь НЕТ (сам прогон корпуса — `trainer_run.py`, минуты и токены): проверяем то, что
обязано быть детерминированным, — критерий зелёного, формат вердикта, привязку к коммиту и
НЕРАСХОЖДЕНИЕ пайплайна раннера с боевым `_trainer_generate`.
"""
import test_isolation  # noqa: F401 — TESTING=1, боевой IPC заблокирован
import ast
import io
import json
import os
import shutil
import tempfile
import unittest

import client_contour as cc
import suggest
import trainer_run as tr

REPO = os.path.dirname(os.path.abspath(__file__))
C40 = "1597cbe194e2de181091309eae4008938a48d426"
OTHER40 = "d14d450aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _rec(**kw):
    """Заведомо ЗЕЛЁНАЯ запись вердикта; kw точечно портит одно поле."""
    base = {"commit": C40, "result": "green", "checks_passed": 96, "checks_total": 96,
            "cases": 12, "cases_total": 12, "runs": 2, "clean": True,
            "corpus": "trainer_cases.json", "corpus_sha": cc.corpus_sha(),
            "runner": "trainer_run.py", "ts": 1.0, "when": "2026-07-30 12:00:00"}
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="trrun_")
        self.v = os.path.join(self.d, "verdict.json")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def put(self, rec, key=None, box="green"):
        with io.open(self.v, "w", encoding="utf-8") as f:
            json.dump({box: {key or cc.short(rec["commit"]): rec}}, f)


# ─────────────────────── 1. КРИТЕРИЙ ЗЕЛЁНОГО (build_verdict) ───────────────────────

class TestKriteriiZelenogo(Base):
    def mk(self, **kw):
        a = {"commit": C40, "cases_total": 12, "passed": 12, "checks_ok": 96, "checks_all": 96,
             "runs": 2, "clean": True, "failed": [], "sha": "abc123", "now": 1.0}
        a.update(kw)
        return tr.build_verdict(a["commit"], a["cases_total"], a["passed"], a["checks_ok"],
                                a["checks_all"], a["runs"], a["clean"], a["failed"], a["sha"],
                                now=a["now"])

    def test_12_iz_12_dva_progona_chistoe_derevo_zelenyi(self):
        self.assertEqual(self.mk()["result"], "green")

    def test_odinnadcat_iz_12_krasnyi(self):
        """Не «почти зелено»: ворота двоичные, частичный зелёный на клиенте недопустим."""
        self.assertEqual(self.mk(passed=11, checks_ok=88, failed=["7/1 наличие"])["result"], "red")

    def test_odin_progon_krasnyi(self):
        """Генератор недетерминирован — один прогон ничего не доказывает."""
        self.assertEqual(self.mk(runs=1)["result"], "red")

    def test_gryaznoe_derevo_krasnyi(self):
        self.assertEqual(self.mk(clean=False)["result"], "red")

    def test_hot_odin_krasnyi_chek_krasit_verdikt(self):
        self.assertEqual(self.mk(checks_ok=95, failed=["3/2 доставка = цена зоны"])["result"], "red")

    def test_men_she_12_keisov_krasnyi(self):
        """Урезанный корпус — не основание, даже если все его кейсы зелёные."""
        self.assertEqual(self.mk(cases_total=3, passed=3, checks_ok=24, checks_all=24)["result"],
                         "red")


# ─────────────────────── 2. ФОРМАТ ВЕРДИКТА И ЗАПИСЬ НА ДИСК ───────────────────────

class TestFormatVerdikta(Base):
    def test_polya_kotorye_chitayut_vorota(self):
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], "sha16", now=1.0)
        for k in ("commit", "result", "checks_passed", "checks_total", "cases", "cases_total",
                  "runs", "clean", "corpus", "corpus_sha", "runner", "ts", "when"):
            self.assertIn(k, rec, f"ворота ждут поле {k}")

    def test_zapis_lozhitsya_pod_klyuch_semerku(self):
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], cc.corpus_sha(), now=1.0)
        ok, _p = tr.write_verdict(rec, self.v)
        self.assertTrue(ok)
        with io.open(self.v, encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn(cc.short(C40), d["green"])
        self.assertTrue(cc.trainer_green(C40, path=self.v, env={}))

    def test_krasnyi_progon_snosit_prezhnyuyu_zelen_togo_zhe_kommita(self):
        """FAIL-CLOSED: вчерашняя зелень не должна открывать ворота коммиту, который сегодня красен."""
        tr.write_verdict(tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], cc.corpus_sha(),
                                          now=1.0), self.v)
        self.assertTrue(cc.trainer_green(C40, path=self.v, env={}))
        tr.write_verdict(tr.build_verdict(C40, 12, 11, 95, 96, 2, True, ["7/1 наличие"],
                                          cc.corpus_sha(), now=2.0), self.v)
        self.assertFalse(cc.trainer_green(C40, path=self.v, env={}))
        with io.open(self.v, encoding="utf-8") as f:
            d = json.load(f)
        self.assertNotIn(cc.short(C40), d["green"])
        self.assertIn(cc.short(C40), d["red"])          # красный остаётся — карточке есть что сказать


# ─────────────────────── 3. ВОРОТА ЗАСЧИТЫВАЮТ ВЕРДИКТ ───────────────────────

class TestVorotaSchitayutVerdikt(Base):
    def test_zelenyi_verdikt_osnovanie_trainer(self):
        self.put(_rec())
        self.assertEqual(cc.release_reason(C40, trainer_path=self.v, path=os.path.join(self.d, "r.json"),
                                           env={}), "trainer")

    def test_verdikt_na_drugom_heade_ne_zaschityvaetsya(self):
        """Ключ-семёрка ЧУЖАЯ: вердикт снят на другом коммите — ворота его не видят вовсе."""
        self.put(_rec(commit=OTHER40))
        ok, why = cc.trainer_verdict(C40, path=self.v, env={})
        self.assertFalse(ok)
        self.assertIn("вердикта на этот коммит нет", why)

    def test_podlozhennyi_klyuch_s_chuzhim_kommitom_ne_zaschityvaetsya(self):
        """Ключ подставлен под наш коммит, а поле commit — чужое: ворота сверяют ПОЛЕ, а не ключ."""
        self.put(_rec(commit=OTHER40), key=cc.short(C40))
        ok, why = cc.trainer_verdict(C40, path=self.v, env={})
        self.assertFalse(ok)
        self.assertIn("на ДРУГОМ коммите", why)

    def test_krasnyi_verdikt_derzhit_i_nazyvaet_prichinu(self):
        with io.open(self.v, "w", encoding="utf-8") as f:
            json.dump({"red": {cc.short(C40): _rec(result="red", checks_passed=95, cases=11)}}, f)
        ok, why = cc.trainer_verdict(C40, path=self.v, env={})
        self.assertFalse(ok)
        self.assertIn("КРАСНЫЙ", why)
        self.assertIsNone(cc.release_reason(C40, trainer_path=self.v,
                                            path=os.path.join(self.d, "r.json"), env={}))

    def test_nepolnye_cheki_ne_osnovanie(self):
        self.put(_rec(checks_passed=95))
        self.assertFalse(cc.trainer_green(C40, path=self.v, env={}))

    def test_odin_progon_ne_osnovanie(self):
        self.put(_rec(runs=1))
        self.assertFalse(cc.trainer_green(C40, path=self.v, env={}))

    def test_gryaznoe_derevo_ne_osnovanie(self):
        self.put(_rec(clean=False))
        self.assertFalse(cc.trainer_green(C40, path=self.v, env={}))

    def test_chuzhoi_korpus_ne_osnovanie(self):
        self.put(_rec(corpus_sha="0" * 16))
        ok, why = cc.trainer_verdict(C40, path=self.v, env={})
        self.assertFalse(ok)
        self.assertIn("корпус", why)

    def test_men_she_12_keisov_ne_osnovanie(self):
        self.put(_rec(cases=3, cases_total=3))
        self.assertFalse(cc.trainer_green(C40, path=self.v, env={}))

    def test_rubilnik_gasit_osnovanie(self):
        """Аварийный возврат к «только да владельца» — без правки раннера и корпуса."""
        self.put(_rec())
        self.assertTrue(cc.trainer_green(C40, path=self.v, env={}))
        self.assertFalse(cc.trainer_green(C40, path=self.v, env={cc.TRAINER_GREEN_ENV: "0"}))
        self.assertFalse(cc.trainer_enabled({cc.TRAINER_GREEN_ENV: "off"}))
        self.assertTrue(cc.trainer_enabled({}))              # дефолт — основание ВКЛЮЧЕНО

    def test_progona_ne_bylo_kartochka_govorit_chem_snyat(self):
        """Причина коротка («прогона не было»), а КОМАНДУ, которой вердикт снимают, даёт карточка."""
        ok, why = cc.trainer_verdict(C40, path=os.path.join(self.d, "net.json"), env={})
        self.assertFalse(ok)
        self.assertIn("прогона не было", why)
        card = cc.card_text(["userbot"], C40, ["suggest.py"], trainer_available=True,
                            trainer_note=why)
        self.assertIn("прогона не было", card)
        self.assertIn(cc.TRAINER_RUNNER, card)


# ─────────────────────── 4. ПАЙПЛАЙН НЕ РАЗОШЁЛСЯ С БОЕВЫМ ───────────────────────

def _suggest_calls(path, func):
    """Имена вызовов `suggest.<name>` внутри функции `func` файла `path`, В ПОРЯДКЕ вызова."""
    with io.open(os.path.join(REPO, path), encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            out = []
            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    fn = n.func
                    if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                            and fn.value.id == "suggest"):
                        out.append((n.lineno, fn.attr))
                    # asyncio.to_thread(suggest.X, …) — боевой вызов того же самого
                    elif isinstance(fn, ast.Attribute) and fn.attr == "to_thread":
                        for a in n.args:
                            if (isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                                    and a.value.id == "suggest"):
                                out.append((n.lineno, a.attr))
            return [name for _ln, name in sorted(out)]
    raise AssertionError(f"{path}: функции {func} нет")


class TestPipelineNeRazoshelsya(unittest.TestCase):
    """Честный минус варианта А (артефакт §4): раннер КОПИРУЕТ `_trainer_generate`, и копия может
    молча разъехаться с боевой. Здесь она разъехаться молча не может."""

    def test_pipeline_ne_razoshelsya(self):
        live = _suggest_calls("userbot_listen.py", "_trainer_generate")
        mine = _suggest_calls("trainer_run.py", "generate")
        self.assertEqual(
            mine, live,
            "раннер разошёлся с боевым _trainer_generate: живой %s ≠ раннер %s. Вердикт тренажёра "
            "перестал удостоверять то, что увидит владелец в группе." % (live, mine))

    def test_raner_ne_v_klientskom_konture(self):
        """Раннер и корпус НЕ должны попасть в замыкание ботов — иначе он сам упрётся в ворота."""
        self.assertFalse(cc.is_client("trainer_run.py", REPO))
        self.assertFalse(cc.is_client("trainer_cases.json", REPO))

    def test_raner_ne_trogaet_zhivoi_userbot(self):
        """Ни Telethon, ни singleton-лока, ни отправки: прогон не может задеть боевой процесс.
        Судим по ДЕЙСТВИЮ (импорты и вызовы из AST), а не по подстроке: слова «userbot.lock» и
        «send_message» законно стоят в шапке модуля — там они ОБЕЩАНИЕ их не трогать (правило 5
        свода ENV_PLAYBOOK: подстрока — не признак)."""
        with io.open(os.path.join(REPO, "trainer_run.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="trainer_run.py")
        imports, calls = set(), set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imports |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                imports.add(n.module.split(".")[0])
            elif isinstance(n, ast.Call):
                f_ = n.func
                calls.add(f_.attr if isinstance(f_, ast.Attribute)
                          else (f_.id if isinstance(f_, ast.Name) else ""))
        for bad in ("telethon", "userbot_listen", "trainer_log", "moderation_bot"):
            self.assertNotIn(bad, imports, f"раннер импортирует боевое: {bad}")
        for bad in ("acquire_lock", "send_message", "safe_append", "enqueue", "set_transcript",
                    "bump_seq", "reset"):
            self.assertNotIn(bad, calls, f"раннер зовёт боевое: {bad}")


# ─────────────────────── 5. КОРПУС ───────────────────────

class TestKorpus(unittest.TestCase):
    def setUp(self):
        self.cases, self.sha = tr.load_cases()

    def test_dvenadcat_keisov_s_istochnikami(self):
        self.assertEqual(len(self.cases), cc.TRAINER_MIN_CASES)
        for c in self.cases:
            self.assertTrue(str(c.get("source") or "").strip(), f"кейс {c.get('id')} без источника")
            self.assertTrue(c.get("lines"), f"кейс {c.get('id')} без реплик")

    def test_id_unikalny(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_snyatye_cheki_nazyvayut_prichinu(self):
        """Снять чек можно только С ПРИЧИНОЙ в корпусе — иначе зелёный покупается молчанием."""
        for c in self.cases:
            for name, why in (c.get("skip") or {}).items():
                self.assertGreater(len(str(why)), 30, f"кейс {c['id']}: чек «{name}» снят без причины")

    def test_daty_podstavlyayutsya_v_budushchee(self):
        ph = tr.placeholders()
        txt = tr.build_transcript(self.cases[0], ph)
        self.assertNotIn("{when", txt)
        self.assertTrue(txt.startswith("[клиент]:"))

    def test_sha_korpusa_sovpadaet_s_vorotami(self):
        self.assertEqual(self.sha, cc.corpus_sha())


# ─────────────────── 6. МОЛЧАЩАЯ ГОЛОВА = НЕИЗВЕСТНО (22.08.2026) ───────────────────
# Замер, из-за которого узел заведён (docs/artifacts/2026-08-22-honest-trainer-checks.md):
# 66 живых чеков из 112 были ЗЕЛЕНЫ при МОЛЧАЩЕЙ голове — их закрывал текст, который дописывает
# КОД (compose_quote_draft / ensure_price_figure / ensure_closing_question). Молчащий бот набирал
# 58.9% набора, а промах снимка границы давал «12 из 12» МОЛЧА.

class TestMolchashchayaGolova(unittest.TestCase):
    def setUp(self):
        self.orig = {a: getattr(suggest, a) for a in tr._HeadWatch.ATTRS}
        self.addCleanup(self._restore)

    def _restore(self):
        for a, f in self.orig.items():
            setattr(suggest, a, f)

    def test_nablyudatel_vozvrashchaet_originaly(self):
        """Наблюдатель обязан быть беспоследственным: боевые точки входа возвращаются на место."""
        with tr._HeadWatch():
            self.assertIsNot(suggest._cli_llm, self.orig["_cli_llm"])
        for a, f in self.orig.items():
            self.assertIs(getattr(suggest, a), f, f"наблюдатель не вернул {a} на место")

    def test_golova_otvetila_sudit_mozhno(self):
        suggest._cli_llm = lambda s, u: "ответ головы"
        with tr._HeadWatch() as hw:
            suggest._cli_llm("s", "u")
        self.assertIsNone(hw.silence(), "живой ответ не должен объявляться неизвестностью")

    def test_pustoi_otvet_eto_neizvestno(self):
        suggest._cli_llm = lambda s, u: ""
        with tr._HeadWatch() as hw:
            suggest._cli_llm("s", "u")
        self.assertIn("промолчала", hw.silence() or "")

    def test_hot_odin_pustoi_zahod_eto_neizvestno(self):
        """У кейса 2 заходов ДВА (черновик + пост-чек). Пустой ЛЮБОЙ — судить нечего: в здоровом
        прогоне пустых ответов нет ни одного (замер 22.08: 13 заходов, минимальный 48 симв.),
        значит пустой заход штатным не бывает."""
        suggest._cli_llm = lambda s, u: ("текст" if u == "1" else "")
        with tr._HeadWatch() as hw:
            suggest._cli_llm("s", "1")
            suggest._cli_llm("s", "2")
        self.assertIn("промолчала", hw.silence() or "")

    def test_golovu_ne_sprosili_tozhe_neizvestno(self):
        """Ни одного захода — это тоже «ответа продукта не существует», а не «всё хорошо»."""
        with tr._HeadWatch() as hw:
            pass
        self.assertIn("не спросили", hw.silence() or "")

    def test_neizvestno_gasit_zelenyi_verdikt(self):
        """Все чеки зелёные, все кейсы зачтены — и всё равно НЕ зелёный: «неизвестно» сильнее."""
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], cc.corpus_sha(), now=1.0,
                               unknown=["8/1 голова промолчала"])
        self.assertEqual(rec["result"], "unknown")
        self.assertEqual(rec["unknown"], 1)

    def test_neizvestno_ne_otkryvaet_vorota_i_ne_zovetsya_krasnym(self):
        """Ворота держат (fail-closed), но владельцу не врут словом «КРАСНЫЙ» про неизмеренное."""
        d = tempfile.mkdtemp(prefix="trrun_unk_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        v = os.path.join(d, "verdict.json")
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], cc.corpus_sha(), now=1.0,
                               unknown=["8/1 голова промолчала"])
        ok, _p = tr.write_verdict(rec, v)
        self.assertTrue(ok)
        self.assertFalse(cc.trainer_green(C40, path=v, env={}))
        why = cc.trainer_status(C40, path=v, env={})
        self.assertIn("НЕИЗВЕСТНО", why)
        self.assertNotIn("КРАСНЫЙ", why)

    def test_cheki_neizvestnogo_keisa_ne_idut_ni_v_zachet_ni_v_provaly(self):
        """Зелёный чек над текстом, который дописал КОД, не доказывает ничего — но и продукт в
        молчании границы не виноват: такие чеки не считаются НИ зелёными, НИ красными."""
        saved = tr.run_case
        self.addCleanup(setattr, tr, "run_case", saved)
        tr.run_case = lambda case, ph, log=print: {
            "id": case["id"], "name": "тест", "ok": False, "unknown": "голова промолчала",
            "checks": [{"name": "нет годов", "ok": True, "unknown": True}], "draft": "d", "note": ""}
        res, passed, ok, allc, failed, unknown = tr.run_corpus(
            [{"id": 1}], runs=1, ph={}, log=lambda *a, **k: None)
        self.assertEqual((passed, ok, allc, failed), (0, 0, 0, []))
        self.assertEqual(len(unknown), 1)
        self.assertIn("голова промолчала", unknown[0])
        self.assertEqual(len(res), 1)

    def test_run_case_zovet_nablyudatelya(self):
        """Структурный замок: правило можно снять только ЯВНО, а не тем, что кто-то однажды
        вынесет наблюдателя из `run_case` и не заметит возврата 66 зелёных на пустой голове."""
        with io.open(os.path.join(REPO, "trainer_run.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="trainer_run.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_case":
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                self.assertIn("_HeadWatch", names, "run_case перестал наблюдать голову")
                return
        raise AssertionError("функции run_case нет")


class SravnenieSlovomVKhekakh(unittest.TestCase):
    """ЧЕКИ ТРЕНАЖЁРА судят ПО СЛОВУ, а не по куску строки (правило `suggest.word_hit`, 23.08.2026).

    Живой повод: чек «без «опасн»» покраснел на слове «безопасным» — то есть уличил бота ровно в
    том, чего голден A.3 и добивается (3 живых прогона из 7, артефакт 2026-08-23-trainer-live-gap).
    Оба отрицательных теста здесь: настоящее слово ОБЯЗАНО краснить, кусок внутри чужого — НЕТ.
    """
    EXP = {"j_line": "", "delivery_line": "", "sheet_line": "",
           "zone": None, "zone_price": None, "full_data": False}

    def _chk(self, case, draft, name):
        by = {c["name"]: c for c in tr.case_checks(case, draft, dict(self.EXP))}
        self.assertIn(name, by, f"чека «{name}» в наборе нет вовсе")
        return by[name]

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПЕРВЫЙ: ради чего чек написан — по-прежнему краснит ──────────────
    def test_forbid_nastoyashchee_slovo_krasnit(self):
        case = {"id": 5, "forbid": ["опасн", "небезопас", "лучше не", "не рекоменд"]}
        for word, draft in (("опасн", "CB650R — опасный выбор для новичка."),
                            ("опасн", "Брать его новичку опасно."),
                            ("небезопас", "Для первого раза это небезопасно."),
                            ("лучше не", "Лучше не брать эту модель сразу."),
                            ("не рекоменд", "Я не рекомендую этот байк новичку.")):
            c = self._chk(case, draft, f"без «{word}»")
            self.assertFalse(c["ok"], f"«{word}» перестал краснить на «{draft}»")
            self.assertEqual(c["fact"], f"есть: «{word}»")

    def test_require_any_nastoyashchee_slovo_zachityvaetsya(self):
        case = {"id": 6, "require_any": ["опыт", "ездил", "на чём", "как долго", "водил"]}
        for draft in ("Подскажите, есть ли опыт вождения?",
                      "На чём ездили раньше?",
                      "Как долго уже катаетесь?",
                      "Водили ли вы мотоцикл прежде?"):
            c = self._chk(case, draft, "обязательное упоминание")
            self.assertTrue(c["ok"], f"обязательное упоминание перестало засчитываться на «{draft}»")

    def test_tsena_tsifroy_nastoyashchee_chislo_zachityvaetsya(self):
        case = {"id": 10, "expect": {"price_figure": True}}
        exp = dict(self.EXP, j_line="стоимость: 1535 (307 в день)")
        by = {c["name"]: c for c in tr.case_checks(case, "NMAX 155 на 5 дней: 1535 ฿ (307 ฿/день)", exp)}
        self.assertTrue(by["цена цифрой"]["ok"])
        self.assertIn("1535", by["цена цифрой"]["fact"])

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ ВТОРОЙ: кусок внутри чужого слова больше не судит ───────────────
    def test_forbid_kusok_vnutri_slova_bolshe_ne_krasnit(self):
        case = {"id": 5, "forbid": ["опасн", "лучше не", "рискован"]}
        # дословная фраза живого красного прогона 81/п5 — из-за неё и заведено правило
        live = ("CB650R — довольно мощный мотоцикл, и для него важно понимание по опыту вождения: "
                "чтобы подобрать вариант, который будет комфортным и безопасным именно для вас, "
                "я уточню детали с менеджером и вернусь с рекомендацией.")
        c = self._chk(case, live, "без «опасн»")
        self.assertTrue(c["ok"], "«безопасным» всё ещё краснит чек «без «опасн»»")
        self.assertEqual(c["fact"], "нет")
        for word, draft in (("опасн", "Мы заботимся о вашей безопасности и дадим шлем."),
                            ("опасн", "Дадим шлем для безопасной поездки."),
                            ("рискован", "Это нерискованный вариант.")):
            self.assertTrue(self._chk(case, draft, f"без «{word}»")["ok"],
                            f"«{word}» всё ещё краснит на «{draft}»")

    def test_chestnaya_granitsa_pravila_frazovyy_tokon_ono_ne_lechit(self):
        """ЧЕСТНАЯ ГРАНИЦА, записанная тестом: «лучше не бывает» правилом НЕ лечится и краснит
        по-прежнему. Здесь «не» — целое слово с начала слова, кусок внутри чужого слова ни при чём;
        это ДРУГОЙ класс (фразовый токен без глагола), и лечится он словарём кейса, а не границей.
        Тест стои́т, чтобы никто не приписал правилу эффекта, которого у него нет."""
        case = {"id": 5, "forbid": ["лучше не"]}
        self.assertFalse(self._chk(case, "Условия — лучше не бывает.", "без «лучше не»")["ok"])

    def test_require_any_kusok_vnutri_slova_bolshe_ne_zachityvaetsya(self):
        case = {"id": 6, "require_any": ["опыт", "ездил", "на чём", "как долго", "водил"]}
        for draft in ("Менеджер проводил осмотр перед выдачей.",
                      "Байк заводился с пол-оборота, всё исправно."):
            c = self._chk(case, draft, "обязательное упоминание")
            self.assertFalse(c["ok"], f"кусок внутри чужого слова засчитал кейс на «{draft}»")
            self.assertEqual(c["fact"], "нет ни одного")

    def test_require_any_chislo_vnutri_chisla_bolshe_ne_zachityvaetsya(self):
        case = {"id": 10, "require_any": ["от 5 дней", "5 дней", "от 3"]}
        for draft in ("Скидка действует от 15 дней аренды.",
                      "Депозит от 3000 ฿ наличными."):
            c = self._chk(case, draft, "обязательное упоминание")
            self.assertFalse(c["ok"], f"число внутри числа засчитало кейс на «{draft}»")
        self.assertTrue(self._chk(case, "Скутеры сдаём от 5 дней.", "обязательное упоминание")["ok"])

    def test_tsena_tsifroy_chislo_vnutri_chisla_bolshe_ne_zachityvaetsya(self):
        case = {"id": 10, "expect": {"price_figure": True}}
        exp = dict(self.EXP, j_line="стоимость: 590 (118 в день)")
        by = {c["name"]: c for c in tr.case_checks(case, "Доставка обойдётся в 5900 ฿ за 1180 км", exp)}
        self.assertFalse(by["цена цифрой"]["ok"], "число внутри числа зачло чек «цена цифрой»")

    def test_zamok_vse_leksicheskie_cheki_zovut_odno_pravilo(self):
        """Структурный замок: правило одно на все три места, и вернуть подстроку молча нельзя."""
        with io.open(os.path.join(REPO, "trainer_run.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="trainer_run.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "case_checks":
                calls = [n for n in ast.walk(node)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                         and n.func.attr == "word_hit"]
                self.assertGreaterEqual(len(calls), 3,
                                        "case_checks зовёт правило по слову меньше трёх раз")
                return
        raise AssertionError("функции case_checks нет")


if __name__ == "__main__":
    unittest.main(verbosity=2)
