# -*- coding: utf-8 -*-
"""
test_trainer_run.py — ГОЛДЕНЫ безголового прогона тренажёра и ВЕРДИКТА для ворот.

Живого LLM здесь НЕТ (сам прогон корпуса — `trainer_run.py`, минуты и токены): проверяем то, что
обязано быть детерминированным, — критерий зелёного, формат вердикта, привязку к коммиту и
НЕРАСХОЖДЕНИЕ пайплайна раннера с боевым `_trainer_generate`.
"""
import test_isolation  # noqa: F401 — TESTING=1, боевой IPC заблокирован
import ast
import contextlib
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
        # ИЗОЛЯЦИЯ РУЧКИ ЗАМОРОЗКИ (05.09.2026): с этого дня она решает не только адрес отказа, но
        # и САМО основание пропуска (`release_reason` → `freeze_holds_release`). Боевой флаг лежит
        # с 21.08 — без увода пути голден «зелёный вердикт даёт основание trainer» краснел бы, не
        # изменившись ни строкой, и вердикт теста зависел бы от решения владельца об эту минуту.
        self.flag = os.path.join(self.d, "pc_orchestrator.contour_frozen")
        _s = cc.FREEZE_FLAG
        cc.FREEZE_FLAG = self.flag
        self.addCleanup(lambda: setattr(cc, "FREEZE_FLAG", _s))

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


# ───────── 2а. ПРОГОН-ЗАМЕР И ПРОГОН-ВЫКАТКА РАЗЛИЧАЮТСЯ УМОЛЧАНИЕМ (05.09.2026) ─────────
# ЗАЧЕМ. Запись вердикта — не «сохранение результата», а АКТ ВЫКАТКИ: зелёная запись открывает
# ворота, и демон применяет коммит к живым userbot/moderbot ближайшим витком (живой замер 26.08:
# 8 секунд от записи до «применяем»). До этого дня запись была УМОЛЧАНИЕМ, а безопасный замер
# требовал вспомнить `--no-write`. Частота вызовов обратная: замер зовут постоянно, запись в бою
# случилась один раз за всю жизнь ворот. Дешевле обязана быть ошибка, которая случается чаще.

class TestZamerNeVykatka(unittest.TestCase):
    def _a(self, argv):
        return tr.build_parser().parse_args(argv)

    def test_umolchanie_ne_pishet_reestr(self):
        """ГЛАВНОЕ: голый вызов — ЗАМЕР. Забытый ключ теперь стоит второго запуска, а не выкатки."""
        self.assertFalse(tr.writes_registry(self._a([])))
        self.assertFalse(tr.writes_registry(self._a(["--runs", "2"])))
        self.assertFalse(tr.writes_registry(self._a(["--only", "3", "--report", "x.md"])))

    def test_zapis_tolko_po_yavnomu_slovu(self):
        self.assertTrue(tr.writes_registry(self._a(["--write"])))
        self.assertTrue(tr.writes_registry(self._a(["--runs", "2", "--write"])))

    def test_no_write_zhiv_i_silnee_write(self):
        """Ключ `--no-write` стои́т в чужих командах и подсказках уроков (lesson_regress) — умереть
        молча он не имеет права. Противоречие двух ключей разрешается в сторону «не трогать»."""
        self.assertFalse(tr.writes_registry(self._a(["--no-write"])))
        self.assertFalse(tr.writes_registry(self._a(["--write", "--no-write"])))
        self.assertFalse(tr.writes_registry(self._a(["--runs", "1", "--only", "3", "--no-write"])))


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
        # ПОЛ, а не точное равенство (02.09.2026, заведение EN-зеркал 13–16). Предмет замка —
        # УРЕЗАНИЕ корпуса: `TRAINER_MIN_CASES` и по имени, и по смыслу МИНИМУМ, и ворота сверяют
        # `cases == cases_total` (client_contour.py:426), а не число 12 — то есть рост корпуса они
        # переживают, а вот `assertEqual` не переживал: он краснел на КАЖДОМ добавленном кейсе и
        # тем запрещал корпусу расти вовсе. Урезание ниже пола по-прежнему красное.
        self.assertGreaterEqual(len(self.cases), cc.TRAINER_MIN_CASES)
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


# ─────────────── 7. ПРИВЯЗКА ЗАМЕРА К КОММИТУ (bind_head → verify_head → запись) ─────────────

class PrivyazkaZameraKKommitu(unittest.TestCase):
    """ЗАМЕР ПРИНАДЛЕЖИТ НАЗВАННОМУ КОММИТУ, а не вершине дерева (23.08.2026).

    Живой повод, числом: шесть живых прогонов набора 23.08 легли на ТРИ РАЗНЫХ коммита
    (`_scratch_livenum_0823/live_run1[1-6].json` — 59edfdb×3, 9f14c1a, 4d5691f×2), причём ВСЕ ТРИ
    прогона с чистым числом 12 из 12 (п3, п4, п6) — на трёх разных. Вершину двигала чужая сессия.

    Живую вершину здесь НЕ ТРОГАЕМ ни одной веткой: `head_commit`/`dirty_tracked`/`run_corpus`
    подменяются в модуле на время теста, git не зовётся вовсе (правило-класс «мок копирует ЖИВОЙ
    формат»: подменённая ручка отдаёт ровно то, что отдаёт живая, — 40 hex либо '')."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="trbind_")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.v = os.path.join(self.d, "verdict.json")
        self.cases = os.path.join(self.d, "cases.json")
        with io.open(self.cases, "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": i, "name": "к%d" % i} for i in range(1, 13)]}, f)
        keep = (tr.head_commit, tr.dirty_tracked, tr.run_corpus)

        def _restore():
            tr.head_commit, tr.dirty_tracked, tr.run_corpus = keep
        self.addCleanup(_restore)
        self.clean_tree()

    # ── обвязка: живая вершина не трогается, git не зовётся ──────────────────────────────────
    def fake_heads(self, seq):
        """HEAD по списку ответов В ПОРЯДКЕ ВЫЗОВА: [до, после]. → счётчик вызовов."""
        box = {"n": 0}

        def _h():
            i = min(box["n"], len(seq) - 1)
            box["n"] += 1
            return seq[i]
        tr.head_commit = _h
        return box

    def clean_tree(self, paths=()):
        tr.dirty_tracked = lambda: (None if paths is None else list(paths))

    def fake_run(self, passed=11, ok=95, allc=96, failed=("10/1 нет утверждений о наличии",)):
        """Корпус НЕ гоняем (живая голова — минуты и токены): числа замера здесь не предмет."""
        tr.run_corpus = lambda cases, runs=2, ph=None: ([], passed, ok, allc, list(failed), [])

    def run_main(self, extra=()):
        # `--write` СТОИТ ЗДЕСЬ ЯВНО (05.09.2026): предмет этого класса — привязка записи к
        # коммиту, а с этого дня запись реестра идёт только по явному ключу (умолчание = замер).
        # Реестр — во ВРЕМЕННОМ `--out`, боевого не касаемся ни одним вызовом.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tr.main(["--cases", self.cases, "--out", self.v, "--runs", "2", "--write", *extra])
        return rc, out.getvalue()

    def last_rec(self):
        with io.open(self.v, encoding="utf-8") as f:
            return json.load(f)["last"]

    # ── п.2: КОММИТ ФИКСИРУЕТСЯ ОДИН РАЗ, НА ВХОДЕ ───────────────────────────────────────────
    def test_kommit_snimaetsya_rovno_odin_raz_na_vhode(self):
        box = self.fake_heads([C40, OTHER40])
        b = tr.bind_head()
        self.assertEqual(box["n"], 1, "фиксация спросила вершину не один раз")
        self.assertEqual(b["commit"], C40)
        self.assertEqual(b["head_moved"], "", "до сверки сдвига быть не может")

    def test_kommit_lozhitsya_v_rezultat_vmeste_s_chislom(self):
        self.fake_heads([C40])
        self.fake_run()
        rc, _txt = self.run_main()
        self.assertEqual(rc, 1)                       # красный: мок отдал 11 из 12
        rec = self.last_rec()
        self.assertEqual(rec["commit"], C40)          # коммит — в той же записи, что и число
        self.assertEqual((rec["cases"], rec["cases_total"]), (11, 12))

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПЕРВЫЙ: сдвиг вершины ВО ВРЕМЯ прогона замечен и НАЗВАН ───────────
    def test_otr1_sdvig_vershiny_zamechen_i_nazvan_v_privyazke(self):
        self.fake_heads([C40, OTHER40])
        b = tr.verify_head(tr.bind_head())
        self.assertEqual(b["head_moved"], "1597cbe→d14d450", "сдвиг не назван")
        self.assertEqual(b["head_after"], OTHER40)
        self.assertEqual(b["commit"], C40,
                         "привязка переписала себя НОВОЙ вершиной — ровно то, что чинится")

    def test_otr1_pometka_dohodit_do_zapisi_i_gasit_zelenoe(self):
        """Запись обязана СКАЗАТЬ про сдвиг, а не молча приписать замер новому коммиту."""
        b = {"commit": C40, "head_after": OTHER40, "head_moved": "1597cbe→d14d450",
             "dirty_paths": [], "tree_known": True}
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], cc.corpus_sha(), now=1.0, bind=b)
        self.assertEqual(rec["head_moved"], "1597cbe→d14d450")
        self.assertEqual(rec["head_after"], OTHER40)
        self.assertEqual(rec["commit"], C40)
        self.assertNotEqual(rec["result"], "green", "12 из 12 на уехавшей вершине названы зелёными")

    def test_otr1_progon_celikom_nazyvaet_sdvig_i_ne_pishet_verdikt(self):
        self.fake_heads([C40, OTHER40])
        self.fake_run(passed=12, ok=96, allc=96, failed=())
        rc, txt = self.run_main()
        self.assertEqual(rc, 2, "прогон на уехавшей вершине засчитан")
        self.assertIn("head_moved: 1597cbe→d14d450", txt, "прогон промолчал о сдвиге")
        self.assertIn("ПРОГОН НЕ ЗАСЧИТАН", txt)
        self.assertIn("1597cbe", txt)                 # замер назван принадлежащим ФИКСИРОВАННОМУ
        self.assertFalse(os.path.exists(self.v), "вердикт на уехавшей вершине всё-таки записан")

    def test_otr1_molchanie_git_na_vyhode_tozhe_schitaetsya_sdvigom(self):
        """Третий исход: «не смог проверить» ≠ «вершина стояла». Fail-closed и НАЗВАНО."""
        self.fake_heads([C40, ""])
        self.fake_run(passed=12, ok=96, allc=96, failed=())
        rc, txt = self.run_main()
        self.assertEqual(rc, 2)
        self.assertIn("1597cbe→?", txt)

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ ВТОРОЙ: неподвижная вершина → ОДИН коммит в обоих прогонах ────────
    def test_otr2_dva_progona_na_nepodvizhnoi_vershine_dayut_odin_kommit(self):
        self.fake_heads([C40])                        # вершина не двигается ни разу
        self.fake_run()
        got, moved = [], []
        for _ in range(2):
            rc, _txt = self.run_main()
            self.assertEqual(rc, 1)
            rec = self.last_rec()
            got.append(rec["commit"])
            moved.append(rec["head_moved"])
        self.assertEqual(got, [C40, C40], "два прогона на одной вершине разошлись по коммитам")
        self.assertEqual(len(set(got)), 1)
        self.assertEqual(moved, ["", ""], "на неподвижной вершине придуман сдвиг")

    # ── п.5: ГРЯЗЬ ДЕРЕВА ВИДНА В РЕЗУЛЬТАТЕ (показывает, но не судит) ───────────────────────
    def test_gryaznoe_derevo_vidno_v_zapisi_i_nazyvaet_faily(self):
        self.fake_heads([C40])
        self.clean_tree(["suggest.py", "trainer_run.py"])
        self.fake_run()
        rc, txt = self.run_main()
        self.assertEqual(rc, 1)
        rec = self.last_rec()
        self.assertIs(rec["tree_dirty"], True)
        self.assertEqual(rec["dirty_paths"], ["suggest.py", "trainer_run.py"])
        self.assertIs(rec["clean"], False)
        self.assertIn("tree_dirty: true", txt)

    def test_chistoe_derevo_tozhe_nazvano_yavno(self):
        self.fake_heads([C40])
        self.fake_run()
        self.run_main()
        rec = self.last_rec()
        self.assertIs(rec["tree_dirty"], False)
        self.assertEqual(rec["dirty_paths"], [])
        self.assertIs(rec["tree_known"], True)

    def test_git_ne_otvetil_pro_diff_tretii_ishod_nazvan(self):
        """`tree_known: false` — незнание названо отдельно от грязи (fail-closed остаётся)."""
        self.fake_heads([C40])
        self.clean_tree(None)
        self.fake_run()
        self.run_main()
        rec = self.last_rec()
        self.assertIs(rec["tree_known"], False)
        self.assertIs(rec["clean"], False)            # fail-closed, как и было

    def test_tree_dirty_zerkalo_clean_a_ne_vtoroi_istochnik_pravdy(self):
        """Два независимых поля про один факт разъехались бы. Здесь одно ведёт другое."""
        for clean in (True, False):
            rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, clean, [], "sha16", now=1.0,
                                   bind={"dirty_paths": ["x.py"]})
            self.assertIs(rec["tree_dirty"], not clean)
            self.assertIs(rec["clean"], clean)

    # ── ЗАМКИ: условие не тронуто, старые вызывающие не сломаны, вершину не переснимают ──────
    def test_staryi_vyzov_bez_privyazki_ne_slomalsya(self):
        """Вызов без `bind` (как звали до 23.08) обязан дать ТОТ ЖЕ вердикт: новые поля не судят."""
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], cc.corpus_sha(), now=1.0)
        self.assertEqual(rec["result"], "green")
        self.assertEqual(rec["head_moved"], "")
        self.assertIs(rec["tree_dirty"], False)

    def test_zamok_main_fiksiruet_odin_raz_i_ne_perespashivaet_vershinu(self):
        """Структурный замок: в `main` ровно одна фиксация, ровно одна сверка и НИ ОДНОГО
        прямого `head_commit()` — иначе коммит снова начнёт браться «какой сейчас».

        Читаем ФАЙЛ САМОГО МОДУЛЯ (`tr.__file__`), а не путь от корня: иначе замок судил бы файл
        в дереве, даже когда под тестом лежит другой — и отрицательный контроль на дофиксовой
        копии проходил бы ложно (проверено 23.08: ровно так он и прошёл на первой попытке)."""
        with io.open(os.path.abspath(tr.__file__), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="trainer_run.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                names = [n.func.id for n in ast.walk(node)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
                self.assertEqual(names.count("bind_head"), 1, "фиксация коммита не одна")
                self.assertEqual(names.count("verify_head"), 1, "сверка вершины не одна")
                self.assertEqual(names.count("head_commit"), 0,
                                 "main снова спрашивает «какая вершина сейчас» мимо привязки")
                return
        raise AssertionError("функции main нет")


class DvaProizvoditelyaOzhidaniy(unittest.TestCase):
    """ЗАМОК НА ПАРУ ПРОИЗВОДИТЕЛЕЙ ОЖИДАНИЙ (заведён 23.08.2026 живым провалом).

    Словарь ожиданий строят ДВЕ функции — `trainer_run.expectations` (тренажёр) и
    `suggest._smoke_expectations` (e2e-смоук), — а читает его ОДНА (`suggest._smoke_checks`).
    Ключ наличия завели сперва только у второй, и гейт этого НЕ ПОЙМАЛ: живой прогон дал
    6 из 12, где все шесть красных — один и тот же чек «нет утверждений о наличии» на ЗДОРОВЫХ
    кейсах ветки `ok`; читатель получал `None` и звал выдумкой строку, которую напечатал сам КОД.

    Замок стои́т на СВОЙСТВЕ, а не на списке: всё, что читатель берёт из `exp`, обязан класть
    КАЖДЫЙ производитель. Появится третий производитель или уедет имя ключа — покраснеет здесь,
    а не на живом прогоне через полчаса."""

    NOTE_FREE = ("ЦЕНА: NMAX 155, 5 дн.\n<<<QUOTE>>>\nNMAX — 307 ฿/день; итого 1535 ฿; "
                 "депозит 3000 ฿; свободен на эти даты.\n<<<END_QUOTE>>>")
    NOTE_MIN_FREE = ("ЦЕНА: скутеры сдаём от 5 дней (короче срок не оформляем); цена за 5 дн: "
                     "307 ฿/день; итого 1535 ฿; депозит 3000 ฿; свободен на эти даты.")
    NOTE_BUSY = "ЦЕНА: на эти даты все подходящие байки заняты — НЕ называй числа."
    DRAFT = ("Здравствуйте! NMAX — 307 ฿/день; итого 1535 ฿; депозит 3000 ฿; "
             "свободен на эти даты. Бронируем?")
    CHECK = "нет утверждений о наличии"

    def _exp(self, note):
        return tr.expectations({"id": 1}, "[клиент]: NMAX 155 с 6 по 11 сентября", note,
                               {"model": "NMAX 155", "has_dates": True})

    def _avail_check(self, note, draft=None):
        by = {c["name"]: c for c in suggest._smoke_checks(draft if draft is not None else self.DRAFT,
                                                          self._exp(note))}
        self.assertIn(self.CHECK, by, "чека наличия в наборе нет вовсе")
        return by[self.CHECK]

    def test_klyuch_nalichiya_est_u_oboikh_proizvoditeley(self):
        """Ключ наличия обязан быть у ОБОИХ — и нести ОДНО значение на одной и той же записке."""
        self.assertIn("avail", self._exp(self.NOTE_FREE))
        self.assertIs(self._exp(self.NOTE_FREE)["avail"], True)
        self.assertIs(self._exp(self.NOTE_MIN_FREE)["avail"], True)   # ветка минимальной котировки
        self.assertIsNone(self._exp(self.NOTE_BUSY)["avail"])          # fail-closed
        # оба производителя читают ОДИН источник — значение обязано совпасть
        for note in (self.NOTE_FREE, self.NOTE_MIN_FREE, self.NOTE_BUSY):
            self.assertEqual(self._exp(note)["avail"], suggest.availability_from_note(note), note[:40])

    def test_chitatel_ne_beret_iz_exp_nichego_mimo_proizvoditeley(self):
        """СВОЙСТВО, а не список: КАЖДЫЙ ключ, который читатель достаёт из `exp`, обязан класть
        производитель тренажёра. Ровно этой проверки не было — и ключ разъехался молча."""
        with io.open(os.path.abspath(suggest.__file__), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="suggest.py")
        used = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name == "_smoke_checks"):
                continue
            for n in ast.walk(node):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == "exp" and n.args
                        and isinstance(n.args[0], ast.Constant)):
                    used.add(n.args[0].value)
                if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                        and n.value.id == "exp" and isinstance(n.slice, ast.Constant)):
                    used.add(n.slice.value)
        self.assertTrue(used, "не нашёл ни одного обращения к exp — замок ослеп")
        have = set(self._exp(self.NOTE_FREE))
        self.assertEqual(used - have, set(),
                         "читатель берёт из exp ключи, которых производитель тренажёра не кладёт")

    def test_pravda_koda_prokhodit_a_vydumka_krasnit(self):
        """Сквозь ПАРУ: правдивая строка КОДА проходит на обеих ветках, выдумка краснит."""
        self.assertTrue(self._avail_check(self.NOTE_FREE)["ok"])       # ветка ok
        self.assertTrue(self._avail_check(self.NOTE_MIN_FREE)["ok"])   # ветка min
        self.assertFalse(self._avail_check(self.NOTE_BUSY)["ok"])      # данных нет → выдумка
        # дефицит краснит при любом носителе — ослаблением чека правка не является
        self.assertFalse(self._avail_check(self.NOTE_FREE,
                                           "Остался последний, успевайте!")["ok"])


class TestChekMinimalnogoSrokaRazdelyon(unittest.TestCase):
    """РАЗДЕЛЕНИЕ ЧЕКА (06.09.2026). Минимальный срок с этого дня держит КОД
    (`suggest.ensure_min_term`), значит экзамен головы этого себе засчитывать не имеет права:
    зелёный чек над текстом, который дописал код, о голове не говорит НИЧЕГО (тот же класс, из-за
    которого 22.08 молчащая голова набирала 58.9% набора).

    Половина КОДА — новый чек «минимальный срок доехал»: ПОСТУСЛОВИЕ гарантии, то есть правило
    звучит клиенту, чьими бы словами оно ни прозвучало. Дословность строки кода предметом чека
    быть НЕ МОЖЕТ: сказала голова сама — гарантия по построению молчит, и посимвольного совпадения
    не будет никогда (живой замер 06.09: так вышло на 2 кругах из 3).
    Половина ГОЛОВЫ — всё остальное; `require_any` при сработавшей гарантии СНИМАЕТСЯ С ПРИЧИНОЙ
    и в зачёт не идёт."""

    NOTE_MIN = ("ЦЕНА: скутеры сдаём от 5 дней (короче срок не оформляем); цена за 5 дн: "
                "307 ฿/день; итого 1535 ฿; депозит 3000 ฿; свободен на эти даты.")
    NOTE_OK = ("ЦЕНА: NMAX 155, 5 дн.\n<<<QUOTE>>>\nNMAX — 307 ฿/день; итого 1535 ฿; "
               "депозит 3000 ฿; свободен на эти даты.\n<<<END_QUOTE>>>")
    CASE10 = {"id": 10, "lang": "ru", "expect": {},
              "require_any": ["от 5 дней", "5 дней", "от 3"],
              "_transcript": "[клиент]: Нужен NMAX 155 с 6 по 7 октября, всего на сутки. Сколько?"}
    CASE6 = {"id": 6, "lang": "ru", "expect": {},
             "require_any": ["опыт", "ездил", "на чём", "как долго", "водил"],
             "_transcript": "[клиент]: Хочу взять что-нибудь на неделю покататься по острову."}

    def _exp(self, note, lang="ru"):
        return tr.expectations({"id": 10, "lang": lang},
                               "[клиент]: NMAX 155 на сутки", note,
                               {"model": "NMAX 155", "has_dates": True})

    def _by_name(self, case, draft, note):
        return {c["name"]: c for c in tr.case_checks(case, draft, self._exp(note))}

    def test_ozhidanie_beretsya_iz_toi_zhe_zapiski(self):
        """Ожидание берётся из ТОЙ ЖЕ записки, что питает черновик, — не из литерала теста."""
        exp = self._exp(self.NOTE_MIN)
        self.assertEqual(exp["min_term_line"], suggest.min_term_line_from_note(self.NOTE_MIN, "ru"))
        self.assertEqual(exp["min_term_pairs"], suggest.min_term_pairs_from_note(self.NOTE_MIN))
        self.assertIsNone(self._exp(self.NOTE_OK)["min_term_line"])
        self.assertEqual(self._exp(self.NOTE_OK)["min_term_pairs"], [])

    def test_chek_koda_zelen_ot_LYUBOGO_avtora_i_krasen_kogda_pravila_net(self):
        red = "Здравствуйте! Учли — NMAX 155 на 6–7 октября. Бронируем?"
        after = suggest.ensure_min_term(red, self.NOTE_MIN, "ru")
        by = self._by_name(self.CASE10, after, self.NOTE_MIN)
        self.assertTrue(by["минимальный срок доехал"]["ok"])
        self.assertIn("строку дописал КОД", by["минимальный срок доехал"]["fact"])
        # ГОЛОВА сказала правило сама, строки кода в тексте нет — чек всё равно ЗЕЛЁН: предмет
        # чека — постусловие, а не авторство. (Ровно здесь ломался первый вариант чека.)
        said = "Здравствуйте! Скутеры мы сдаём от 5 дней, на сутки, к сожалению, не оформляем."
        by_said = self._by_name(self.CASE10, said, self.NOTE_MIN)
        self.assertTrue(by_said["минимальный срок доехал"]["ok"])
        self.assertIn("своими словами головы", by_said["минимальный срок доехал"]["fact"])
        # правила нет ни в каком виде → чек КОДА КРАСЕН (ослаблением он не является)
        self.assertFalse(self._by_name(self.CASE10, red, self.NOTE_MIN)
                         ["минимальный срок доехал"]["ok"])

    def test_zhivoi_defekt_0609_krasnit_chek(self):
        """РЕГРЕСС на живой дефект 06.09: голова ответила верно, а `drop_answered_questions` +
        `ensure_closing_question` вырезали отказ и подставили «Бронируем?». Текст, уехавший
        клиенту, — согласие на срок, которого мы не сдаём; чек обязан быть КРАСНЫМ."""
        eaten = "Здравствуйте! Учли — NMAX 155 на 6–7 октября. Бронируем?"
        self.assertFalse(self._by_name(self.CASE10, eaten, self.NOTE_MIN)
                         ["минимальный срок доехал"]["ok"])

    def test_golove_ne_zaschityvaetsya_to_chto_dopisal_kod(self):
        red = "Здравствуйте! Учли — NMAX 155 на 6–7 октября. Бронируем?"
        after = suggest.ensure_min_term(red, self.NOTE_MIN, "ru")
        chk = self._by_name(self.CASE10, after, self.NOTE_MIN)["обязательное упоминание"]
        self.assertTrue(chk.get("skipped"), "чек головы не снят — код зеленит экзамен за голову")
        self.assertIn("ensure_min_term", chk["skipped"])
        # и в отчёте видно, что голова сама не сказала ничего
        self.assertIn("голова сама: не сказала ничего", chk["fact"])

    def test_chto_golova_skazala_sama_vidno_chislom(self):
        said = ("NMAX 155 на сутки, к сожалению, не оформляем — скутеры от 5 дней. "
                "Рассмотрите 5 дней?")
        chk = self._by_name(self.CASE10, said, self.NOTE_MIN)["обязательное упоминание"]
        self.assertTrue(chk.get("skipped"))
        self.assertIn("голова сама: от 5 дней", chk["fact"])

    def test_chuzhie_keisy_ostayutsya_meroi_golovy(self):
        """РЕЖЕТ ПО ФАКТУ, а не по номеру кейса: у 5/6 гарантия не срабатывает вовсе, и их
        `require_any` (опыт/менеджер) остаётся полноценным чеком ГОЛОВЫ."""
        chk = self._by_name(self.CASE6, "Здравствуйте! А на чём раньше ездили?", self.NOTE_OK)
        self.assertNotIn("минимальный срок доехал", chk)
        self.assertFalse(chk["обязательное упоминание"].get("skipped"))
        self.assertTrue(chk["обязательное упоминание"]["ok"])
        red = self._by_name(self.CASE6, "Здравствуйте! Отличный выбор, бронируем?", self.NOTE_OK)
        self.assertFalse(red["обязательное упоминание"]["ok"])   # голову по-прежнему краснит

    def test_snyatyi_chek_v_zachyot_ne_idyot(self):
        """Снятый чек не считается ни зелёным, ни красным — так же, как `skip` корпуса."""
        red = "Здравствуйте! Учли — NMAX 155 на 6–7 октября. Бронируем?"
        after = suggest.ensure_min_term(red, self.NOTE_MIN, "ru")
        checks = tr.case_checks(self.CASE10, after, self._exp(self.NOTE_MIN))
        live = [c for c in checks if not c.get("skipped")]
        self.assertTrue(all(c["name"] != "обязательное упоминание" for c in live))
        self.assertIn("минимальный срок доехал", [c["name"] for c in live])

    def test_proza_golovy_ne_soderzhit_stroku_koda(self):
        """`llm_prose` вычитает вставку КОДА — иначе чек языка (и любой будущий чек прозы) мерил
        бы голову чужим текстом."""
        exp = self._exp(self.NOTE_MIN)
        after = suggest.ensure_min_term("Здравствуйте! Бронируем?", self.NOTE_MIN, "ru")
        prose = tr.llm_prose(suggest.client_facing_text(after), exp)
        self.assertNotIn(exp["min_term_line"], prose)
        self.assertIn("Бронируем?", prose)


if __name__ == "__main__":
    unittest.main(verbosity=2)
