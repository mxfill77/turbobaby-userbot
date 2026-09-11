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
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest

import client_contour as cc
import price_source
import season_gate
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

    @staticmethod
    def _importers_of(stem, files, repo=None):
        """Файлы замыкания, ИМПОРТИРУЮЩИЕ модуль `stem` (ast, а не подстрока). → sorted список."""
        repo = REPO if repo is None else repo
        out = []
        for name in sorted(files):
            try:
                with io.open(os.path.join(repo, name), encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=name)
            except (OSError, SyntaxError):
                out.append(name + " (НЕ РАЗОБРАН)")     # fail-closed: незнание ≠ «не импортирует»
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Import) and any(
                        a.name.split(".")[0] == stem for a in n.names):
                    out.append(name)
                    break
                if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == stem:
                    out.append(name)
                    break
        return out

    def test_raner_ne_v_klientskom_konture(self):
        """Раннер НЕ попадает в замыкание ботов — иначе он сам упрётся в ворота.

        СТОРОЖ ВОССТАНОВЛЕН, А НЕ ПЕРЕПИСАН (07.09.2026). Он требовал этого с самого начала;
        05.09 владелец завёл РЕГРЕССИЮ УРОКА (`a42c8ee`, задача 231), прогонщик въехал в
        замыкание одним ребром `lesson_regress.py:297`, и сторож покраснел. 07.09 его привели к
        этому факту — то есть починили СТОРОЖА, а не причину; сегодня убрана ПРИЧИНА, и требование
        вернулось дословно.

        ПОЧЕМУ РЕБРО ПОБОЧНОЕ (замер, а не рассуждение): `import trainer_run` живёт ВНУТРИ
        `measure()`, а `measure()` исполняется только в ОТСОЕДИНЁННОМ процессе, который поднимает
        `lesson_regress.spawn()` (`Popen [python, lesson_regress.py, --after-lesson]`,
        DETACHED_PROCESS). Из живого бота зовётся ровно `spawn()` (`trainer.py:1366` →
        `lesson_regress.spawn`), и правка прогонщика не меняет ответ клиента ни одной дорогой.
        Ребро объявлено ГРАНИЦЕЙ ПРОЦЕССА — `client_contour.PROCESS_BOUNDARY_EDGES`.

        ЦЕНА НАЗВАНА ЧИСЛОМ: замыкание ворот (`cut=()`, `pc_orchestrator._CLIENT_CUT`) 78 → 77
        файлов, вышел РОВНО `trainer_run.py`; данных 50 → 50 — не вышло ни одного имени.

        КОРПУС ОСТАЛСЯ КЛИЕНТСКИМ, И ЭТО ВЕРНОЕ КРАСНОЕ, а не недоделка. `trainer_cases.json`
        держится литералом `client_contour.py` (`TRAINER_CASES_FILE`), а сами ворота с 07.09
        приезжают в замыкание ШЕСТЬЮ импортёрами (`deploy_voice`, `lesson_regress`,
        `pc_orchestrator`, `recon_auto_run`, `trainer_run`, `zayavki_pc_run`) — после снятия среза
        ворот. Вывести корпус можно только выведя из замыкания САМИ ВОРОТА, а это сужение охраны
        далеко за пределы «раннер и корпус» и решение владельца, не этого теста."""
        cl = cc.closure(REPO)
        self.assertTrue(cl.ok, cl.reason)
        self.assertFalse(cc.is_client("trainer_run.py", REPO),
                         "прогонщик снова в клиентском замыкании — правка экзамена опять упрётся "
                         "в ворота контура")
        gate = cc.closure(REPO, entries=cc.CLIENT_ENTRIES, cut=())
        self.assertTrue(gate.ok, gate.reason)
        self.assertNotIn("trainer_run.py", gate.files,
                         "прогонщик в замыкании ЖИВЫХ ВОРОТ (cut=()) — именно им судит _client_block")
        self.assertEqual(self._importers_of("trainer_run", gate.files), ["lesson_regress.py"],
                         "прогонщик тащат в контур НЕ ОДНИМ названным ребром")
        self.assertTrue(cc.is_client("trainer_cases.json", REPO),
                        "корпус вышел из замыкания — значит вышли и сами ворота client_contour.py, "
                        "а это сужение охраны, о котором обязан решать владелец")

    def test_import_ranera_ostayotsya_lenivym(self):
        """ЗАМОК ГРАНИЦЫ: право не считать ребро стои́т на ЛЕНИВОСТИ импорта, и ничем другим.

        Поднимут `import trainer_run` на верхний уровень `lesson_regress.py` — бот начнёт грузить
        прогонщик В СВОЮ ПАМЯТЬ на первом же `import lesson_regress` из `trainer.py`, и граница
        станет ложью, а ворота — дырой. Судим по AST (ветвь `body` модуля), а не по отступам."""
        with io.open(os.path.join(REPO, "lesson_regress.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename="lesson_regress.py")
        top = set()
        for node in tree.body:                       # РОВНО верхний уровень, без ast.walk
            if isinstance(node, ast.Import):
                top |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        self.assertNotIn("trainer_run", top,
                         "импорт прогонщика поднят на верхний уровень lesson_regress.py — "
                         "граница процесса в client_contour.PROCESS_BOUNDARY_EDGES стала ложью")
        lazy = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Import)
                and any(a.name.split(".")[0] == "trainer_run" for a in n.names)]
        self.assertTrue(lazy, "импорт прогонщика исчез из lesson_regress.py вовсе — граница "
                              "описывает ребро, которого нет: снять её из ворот")

    def test_edge_check_catches_a_second_importer(self):
        """ОТРИЦАТЕЛЬНЫЙ: второй импортёр прогонщика ловится, а проза о нём — нет.

        Прибор кормится синтетическим деревом во временном каталоге: боевые файлы не трогаются."""
        with tempfile.TemporaryDirectory() as d:
            names = {"чистый.py": "X = 1\n",
                     "проза.py": '"""словами про trainer_run и корпус"""\nY = 2\n',
                     "подлог.py": "import trainer_run\n",
                     "подлог2.py": "from trainer_run import run_corpus\n",
                     "битый.py": "def (\n"}
            for name, src in names.items():
                with io.open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(src)
            got = self._importers_of("trainer_run", list(names), repo=d)
        self.assertEqual(got, ["битый.py (НЕ РАЗОБРАН)", "подлог.py", "подлог2.py"],
                         "подлог не пойман либо проза принята за импорт: %s" % got)

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
        res, passed, ok, allc, failed, unknown, _plan = tr.run_corpus(
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
        # Седьмое значение — `plan` (критерий F, 07.09.2026): стенд повторяет ЖИВОЙ контракт
        # `run_corpus`, иначе `main` распакует шесть значений из шести и класс «мок отстал от
        # прода» вернётся молча. `point` — контрольная точка (07.09.2026): `main` подаёт её ВСЕГДА
        # (None, когда `--state` не назвали), и стенд без этого имени падал бы TypeError.
        tr.run_corpus = lambda cases, runs=2, ph=None, log=print, point=None: (
            [], passed, ok, allc, list(failed), [],
            {str(c.get("id")): {"class": "", "rounds": runs, "need": runs, "green": runs,
                                "red": 0, "unknown": 0, "ok": True, "tolerated": []}
             for c in cases})

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


# ───────────── 10. КРИТЕРИЙ НАБОРА — ВАРИАНТ F ПО ИЗМЕРЕННОЙ ЛОТЕРЕЕ (07.09.2026) ─────────────
# ЗАЧЕМ ЭТИ ГОЛДЕНЫ. Критерий — это ПОСЛАБЛЕНИЕ, а послабление, у которого нет отрицательных
# тестов, через месяц незаметно превращается в «зелёное всегда». Здесь их три и все живые:
# стабильно красный кейс краснеет и на трёх кругах; два красных из трёх — красный кейс; молчащая
# голова большинством не лечится. Живой головы нет ни в одном тесте — `run_case` подменён,
# круги задаются списком: предмет здесь ПРАВИЛО ЗАЧЁТА, а не продукт.

class KriterijNaboraF(unittest.TestCase):
    KLASS = {"id": 5, "name": "класс", "require_any": ["опыт", "ездил"]}
    PROSTOY = {"id": 1, "name": "обычный"}

    # ── принадлежность классу берётся ИЗ КОРПУСА, а не списком номеров ───────────────────────
    def test_klass_beryotsya_iz_korpusa_a_ne_spiskom_nomerov(self):
        self.assertEqual(tr.case_class(self.KLASS), tr.CLASS_NAME)
        self.assertEqual(tr.case_class(self.PROSTOY), "")
        self.assertEqual(tr.case_class({"id": 5}), "",
                         "класс присвоен по НОМЕРУ кейса — список номеров протухнет молча")
        self.assertEqual(tr.case_class({"id": 99, "require_any": ["что угодно"]}), tr.CLASS_NAME,
                         "новый кейс с require_any в класс не попал — вернулась лотерея")

    def test_zhivoy_korpus_daet_rovno_chetyre_keisa_klassa_i_38_krugov(self):
        """Цена критерия — не оценка, а СЧЁТ по живому корпусу: 17 кейсов, 4 в классе → 38 кругов.

        ЧИСЛА ПОИМЁННО (07.09.2026, заведение кейса 17 «третий исход»). Прежние — 16 / 3 / 35:
          • 16 → 17: корпус вырос РОВНО на один кейс — станцию, где верный ответ есть «не считаю,
            зовём человека». До него такой станции не было ни одной, и экзамен не различал третий
            исход вовсе;
          • 3 → 4 в классе: класс присваивает НЕ номер, а признак `require_any` в самом кейсе
            (`case_class`). У кейса 17 он непустой по существу дела, а не ради круга: слова
            передачи человеку — это и есть его ответ, и они сочиняются ГОЛОВОЙ, то есть подвержены
            той же измеренной лотерее 4.3% на круг, ради которой критерий F и заведён;
          • 35 → 38: 13 обычных кейсов × 2 круга = 26, плюс 4 кейса класса × 3 круга = 12. Ровно
            +3 круга, и все три — новому кейсу; ни одному прежнему цена не изменилась.
        Строгость НЕ ослаблена: здесь по-прежнему `assertEqual`, а не «не меньше трёх»."""
        cases, _sha = tr.load_cases()
        klass = [c for c in cases if tr.case_class(c)]
        self.assertEqual(len(cases), 17)
        self.assertEqual(sorted(str(c["id"]) for c in klass), ["10", "17", "5", "6"])
        self.assertEqual(sum(tr.rounds_for(c, 2) for c in cases), 38)
        self.assertEqual(13 * 2 + 4 * 3, 38, "разбор 38 кругов на слагаемые не сходится")
        self.assertEqual(sum(2 for _c in cases), 34, "прежняя цена посчитана не по корпусу")

    def test_razvedka_i_regress_uroka_lishnih_krugov_ne_platyat(self):
        """`runs=1` зелёным не бывает ни одной веткой — большинству там нечего защищать."""
        self.assertEqual(tr.rounds_for(self.KLASS, 1), 1)
        self.assertEqual(tr.rounds_for(self.KLASS, 2), 3)
        self.assertEqual(tr.rounds_for(self.KLASS, 5), 5, "пол CLASS_RUNS стал прибавкой +1")
        self.assertEqual(tr.rounds_for(self.PROSTOY, 2), 2)

    def test_porog_zachyota_bolshinstvom_tolko_u_klassa(self):
        self.assertEqual(tr.need_green(3, tr.CLASS_NAME), 2)
        self.assertEqual(tr.need_green(5, tr.CLASS_NAME), 3)
        self.assertEqual(tr.need_green(2, ""), 2, "обычному кейсу разрешили красный круг")

    def test_variant_d_ne_priehal_pod_vidom_uproshcheniya(self):
        """ОТКЛОНЁННЫЙ вариант D («хватает одного зелёного») роняет поимку регрессии с 51% до 9%.
        Замок числом: порог класса обязан расти с числом кругов, а не стоять на единице."""
        self.assertGreater(tr.need_green(3, tr.CLASS_NAME), 1)
        self.assertGreater(tr.need_green(9, tr.CLASS_NAME), tr.need_green(3, tr.CLASS_NAME))

    # ── обвязка: круги задаются списком, живой головы нет ────────────────────────────────────
    def _rounds(self, case, outcomes):
        """`outcomes` — список кругов: True зелёный, False красный, str «неизвестно»."""
        box = {"i": 0}
        saved = tr.run_case
        self.addCleanup(setattr, tr, "run_case", saved)

        def fake(c, ph, log=print):
            out = outcomes[min(box["i"], len(outcomes) - 1)]
            box["i"] += 1
            unk = out if isinstance(out, str) else None
            checks = [{"name": "обязательное упоминание", "ok": out is True}]
            return {"id": c["id"], "name": c.get("name"), "ok": out is True, "unknown": unk,
                    "checks": [dict(c2, unknown=True) for c2 in checks] if unk else checks,
                    "draft": "d", "note": ""}
        tr.run_case = fake
        return tr.run_corpus([case], runs=2, ph={}, log=lambda *a, **k: None)

    def test_dva_zelenyh_iz_treh_keis_zachten_a_krasnyi_krug_proshchen_i_nazvan(self):
        res, passed, ok, allc, failed, unknown, plan = self._rounds(self.KLASS, [True, False, True])
        self.assertEqual(len(res), 3, "кейсу класса дали не три круга")
        self.assertEqual((passed, failed, unknown), (1, [], []))
        self.assertEqual((ok, allc), (2, 2), "чеки прощённого круга попали в зачёт")
        self.assertEqual(plan["5"]["tolerated"], ["5/2 обязательное упоминание"])
        self.assertIn("прощён критерием F", res[1]["tolerated"])

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ ПЕРВЫЙ: СТАБИЛЬНО красный остаётся красным и на трёх кругах ───────
    def test_otr1_stabilno_krasnyi_keis_krasen_i_na_treh_krugah(self):
        res, passed, ok, allc, failed, unknown, plan = self._rounds(self.KLASS,
                                                                    [False, False, False])
        self.assertEqual(len(res), 3)
        self.assertEqual(passed, 0, "стабильно красный кейс зачтён большинством")
        self.assertEqual(len(failed), 3, "красные круги не названы пофамильно")
        self.assertEqual((ok, allc), (0, 3))
        self.assertEqual(plan["5"]["tolerated"], [], "красное большинство прощено")
        self.assertEqual(tr.build_verdict(C40, 16, passed, ok, allc, 2, True, failed,
                                          cc.corpus_sha(), now=1.0, plan=plan)["result"], "red")

    def test_otr1_dva_krasnyh_iz_treh_tozhe_krasnyi(self):
        """Граница послабления: прощается МЕНЬШИНСТВО, а не «хотя бы один зелёный» (вариант D)."""
        _res, passed, _ok, _allc, failed, _unk, plan = self._rounds(self.KLASS,
                                                                     [True, False, False])
        self.assertEqual(passed, 0)
        self.assertEqual(len(failed), 2)
        self.assertEqual(plan["5"]["tolerated"], [])

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ ВТОРОЙ: «неизвестно» большинством НЕ лечится ──────────────────────
    def test_otr2_neizvestno_bolshinstvom_ne_lechitsya(self):
        _res, passed, _ok, _allc, failed, unknown, plan = self._rounds(
            self.KLASS, [True, True, "голова промолчала"])
        self.assertEqual(passed, 0, "молчащая голова зачтена большинством зелёных")
        self.assertEqual(failed, [])
        self.assertEqual(len(unknown), 1)
        self.assertEqual(plan["5"]["unknown"], 1)

    # ── ОТРИЦАТЕЛЬНЫЙ ТЕСТ ТРЕТИЙ: обычному кейсу послабления не досталось ───────────────────
    def test_otr3_obychnomu_keisu_krasnyi_krug_ne_proshchen(self):
        _res, passed, _ok, _allc, failed, _unk, plan = self._rounds(self.PROSTOY, [True, False])
        self.assertEqual(plan["1"]["rounds"], 2, "обычному кейсу дали лишние круги")
        self.assertEqual(passed, 0, "красный круг прощён кейсу ВНЕ класса")
        self.assertEqual(failed, ["1/2 обязательное упоминание"])

    # ── вердикт НАЗЫВАЕТ правило и число кругов у каждого кейса ──────────────────────────────
    def test_verdikt_nazyvaet_kriterii_i_krugi_kazhdogo_keisa(self):
        _res, passed, ok, allc, failed, _unk, plan = self._rounds(self.KLASS, [True, False, True])
        rec = tr.build_verdict(C40, 1, passed, ok, allc, 2, True, failed, cc.corpus_sha(),
                               now=1.0, plan=plan)
        self.assertIn("F", rec["criterion"])
        self.assertIn(tr.CLASS_NAME, rec["criterion"])
        self.assertIn("require_any", rec["criterion"], "правило названо номерами, а не признаком")
        self.assertEqual(rec["criterion_id"], "F")
        self.assertEqual(rec["runs_by_case"], {"5": 3})
        self.assertEqual(rec["runs_line"], "5:3")
        self.assertEqual(rec["runs_max"], 3)
        self.assertEqual(rec["tolerated"], 1)
        self.assertEqual(rec["tolerated_why"], ["5/2 обязательное упоминание"])
        self.assertEqual(rec["runs"], 2, "базовое число кругов подменено фактическим")

    def test_stroka_krugov_chitaema_i_uporyadochena_chislami(self):
        self.assertEqual(tr.rounds_line({"10": 3, "2": 2, "5": 3}), "2:2 5:3 10:3")
        self.assertEqual(tr.rounds_line({}), "—")

    def test_vorota_prezhnego_verdikta_ne_zametili_pribavki_poley(self):
        """Соседнее место: ворота читают ту же запись — новые поля не смеют её сломать."""
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], cc.corpus_sha(), now=1.0,
                               plan={"5": {"rounds": 3, "tolerated": []}})
        d = tempfile.mkdtemp(prefix="trrun_f_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        v = os.path.join(d, "verdict.json")
        self.assertTrue(tr.write_verdict(rec, v)[0])
        self.assertTrue(cc.trainer_green(C40, path=v, env={}))


# ─────────────────── 8. ТРЕТИЙ ИСХОД: станция «цену называть НЕЛЬЗЯ» ─────────────────────────
# Заведено 07.09.2026. До этого дня у экзамена не было НИ ОДНОЙ станции третьего исхода: корпус
# спрашивал либо «число обязано прозвучать» (1,3,7,8,13,16), либо «сетку вываливать нельзя»
# (4,15) — и оба вопроса про то, ЧТО СКАЗАТЬ, а не про право не считать вовсе. Кейс 17 несёт
# третий: числа быть не должно НИ В КАКОМ ВИДЕ, и при этом ответ обязан ПРОЗВУЧАТЬ словами
# передачи человеку. Живой головы здесь нет — предмет тестов ниже — сборка окна и чеки.

class TretiyIskhodStanciya(unittest.TestCase):
    DOC = {"season": {"periods": [
        {"key": "P1", "name": "ИЮНЬ-СЕНТЯБРЬ", "from": "06-01", "to": "09-30"},
        {"key": "P2", "name": "ОКТЯБРЬ", "from": "10-01", "to": "10-31"},
        {"key": "P5", "name": "ПИК", "from": "12-15", "to": "02-05", "crosses_year": True},
    ]}}

    def case17(self):
        cases, _sha = tr.load_cases()
        by = {c["id"]: c for c in cases}
        self.assertIn(17, by, "кейса третьего исхода в корпусе нет")
        return by[17]

    # ── окно СЧИТАЕТСЯ из живой таблицы и СВЕРЯЕТСЯ, а не объявляется ───────────────────────
    def test_okno_schitaetsya_i_peresekaet_granicu(self):
        w = tr.cross_window(today=datetime.date(2026, 9, 7), doc=self.DOC)
        self.assertTrue(w["ok"], w["why"])
        self.assertEqual((w["iso_start"], w["iso_end"]), ("2026-09-26", "2026-10-05"))
        self.assertEqual(season_gate.span(w["iso_start"], w["iso_end"], doc=self.DOC)[0],
                         season_gate.SEASON_CROSSES)
        self.assertEqual(w["text"], "с 26.09 по 05.10")

    def test_okno_edet_za_tablicey_a_ne_za_kalendarem(self):
        """Сдвинули границу в таблице — поехало окно. Это и есть «даты не зашиты»."""
        doc = {"season": {"periods": [
            {"key": "A", "name": "А", "from": "01-01", "to": "11-14"},
            {"key": "B", "name": "Б", "from": "11-15", "to": "12-31"}]}}
        w = tr.cross_window(today=datetime.date(2026, 9, 7), doc=doc)
        self.assertTrue(w["ok"], w["why"])
        self.assertEqual((w["iso_start"], w["iso_end"]), ("2026-11-10", "2026-11-19"))

    def test_tablicy_net_okna_net_i_prichina_nazvana(self):
        """Мёртвая таблица → «не знаю», а не «наверное не пересекает»: молчание источника
        выздоровлением не является (то же правило, что у слоя ожиданий ПК)."""
        saved = price_source.load
        self.addCleanup(setattr, price_source, "load", saved)
        price_source.load = lambda *a, **k: None
        w = tr.cross_window(today=datetime.date(2026, 9, 7))
        self.assertFalse(w["ok"])
        self.assertIn("price_source", w["why"])

    def test_granicy_net_vovse_ne_zelenoe_a_prichina(self):
        doc = {"season": {"periods": [{"key": "A", "name": "А", "from": "01-01", "to": "12-31"}]}}
        w = tr.cross_window(today=datetime.date(2026, 9, 7), doc=doc)
        self.assertFalse(w["ok"])
        self.assertIn("границы сезонов", w["why"])

    # ── ГЛАВНЫЙ ЗАМОК: не пересеклось → НЕИЗВЕСТНО, а не зелёное ────────────────────────────
    def test_nepersechenie_daet_neizvestno_a_ne_zelenoe(self):
        """КОНТРФАКТ зашитых дат: буквальные даты живой реплики (27.12→18.01) лежат в одном
        периоде — кейс на них обязан стать «неизвестно», а не тихо позеленеть."""
        case = dict(self.case17(), lines=["Подскажите x-max с 27.12 по 18.01 сколько будет стоить"])
        tr_txt = tr.build_transcript(case, {})
        why = tr.cross_guard(case, tr_txt, {"cross": {"ok": True}})
        self.assertTrue(why, "непересекающее окно прошло как годное — зелень по неверной причине")
        self.assertIn("НЕ пересекают", why)

    def test_run_case_na_nepersekayushchem_okne_daet_neizvestno_bez_kruga_golovy(self):
        """ЖИВОЙ `run_case`, а не только гард: на зашитых датах кейс отдаёт НЕИЗВЕСТНО и не
        тратит круг головы вовсе (голова здесь не подменена — её просто не зовут)."""
        case = dict(self.case17(), lines=["Подскажите x-max с 27.12 по 18.01 сколько будет стоить"])
        res = tr.run_case(case, {"cross": {"ok": True}}, log=lambda *a, **k: None)
        self.assertFalse(res["ok"], "непересекающее окно дало ЗЕЛЁНЫЙ круг")
        self.assertTrue(res["unknown"], "исход назван не «неизвестно»")
        self.assertEqual(res["checks"], [], "чеки посчитаны там, где судить было нечем")
        self.assertEqual(res["draft"], "", "круг головы потрачен впустую")

    def test_zhivaya_replika_svoimi_datami_granicu_ne_peresekaet(self):
        """Тот же факт, снятый прямо с таблицы: основание запрета зашивать даты — измеренное."""
        self.assertEqual(season_gate.span("2026-12-27", "2027-01-18", doc=self.DOC)[0],
                         season_gate.SEASON_ONE)

    def test_guard_molchit_na_chuzhih_keisah(self):
        self.assertEqual(tr.cross_guard({"id": 1}, "что угодно", {}), "")

    def test_nesobrannoe_okno_gasit_keis_s_prichinoy(self):
        why = tr.cross_guard({"id": 17, "needs": "season_cross"}, "",
                             {"cross": {"ok": False, "why": "таблица не прочиталась"}})
        self.assertIn("таблица не прочиталась", why)

    # ── ЧЕКИ РАЗЛИЧАЮТ ТРИ ИСХОДА: число / молчание / зов человека ──────────────────────────
    def test_lyubaya_cenovaya_cifra_krasnaya(self):
        for bad in ("итого 12500 ฿", "от 350 ฿ в сутки", "примерно 1200–1500 за весь срок",
                    "в среднем 1350 в день", "выйдет 8 400 бат", "около 700 THB"):
            self.assertTrue(tr.price_hits(bad, ["300"]), f"цена прошла молча: {bad}")

    def test_razreshennye_cifry_modeli_ne_cena_a_s_valyutoy_snova_cena(self):
        self.assertEqual(tr.price_hits("Уточню по XMAX 300 у коллеги", ["300"]), [])
        self.assertTrue(tr.price_hits("XMAX 300 стоит 300 ฿ в день", ["300"]),
                        "разрешённое число с валютой перестало быть ценой")

    def test_razreshenie_deystvuet_tolko_v_imeni_modeli(self):
        """Прощается КОНТЕКСТ, а не число: голый «примерно 650» — ценовой ориентир, а не имя."""
        self.assertEqual(tr.price_hits("Подойдёт CB 650R", ["650"]), [])
        self.assertTrue(tr.price_hits("Ну, где-то примерно 650, точнее скажет коллега", ["650"]),
                        "ориентир цены прощён по списку разрешённых номеров")

    def test_daty_srok_i_god_cenoy_ne_schitayutsya(self):
        self.assertEqual(tr.price_hits("Окно с 26.09 по 05.10, это 10 суток", []), [])
        self.assertEqual(tr.price_hits("поколение 2024", []), [],
                         "год отнят у соседнего чека «нет годов» — одна вина, два красных")

    def test_chek_treh_ishodov_sobran_i_nazvan(self):
        case = self.case17()
        exp = {"j_line": "", "delivery_line": "", "sheet_line": "", "min_term_line": "",
               "min_term_pairs": [], "avail": "", "full_data": False,
               "zone": None, "zone_price": None}
        case = dict(case, _transcript="")
        by = {c["name"]: c for c in tr.case_checks(
            case, "Здравствуйте! Даты попали на стык сезонов — цену на такой срок считает "
                  "человек, коллега вернётся с точной суммой.", exp)}
        self.assertIn("цены нет ни в каком виде", by)
        self.assertTrue(by["цены нет ни в каком виде"]["ok"])
        self.assertTrue(by["обязательное упоминание"]["ok"], "зов человека не засчитан")

    def test_chislo_krasnit_a_molchanie_tozhe_krasnoe(self):
        case = dict(self.case17(), _transcript="")
        exp = {"j_line": "", "delivery_line": "", "sheet_line": "", "min_term_line": "",
               "min_term_pairs": [], "avail": "", "full_data": False,
               "zone": None, "zone_price": None}
        # ИСХОД «ЧИСЛО»: цена прозвучала → красный
        by = {c["name"]: c for c in tr.case_checks(
            case, "Здравствуйте! Ориентировочно от 450 ฿ в сутки, точнее скажет коллега.", exp)}
        self.assertFalse(by["цены нет ни в каком виде"]["ok"], "названная цена прошла")
        # ИСХОД «МОЛЧАНИЕ»: числа нет, но и человека не позвали → тоже красный
        by = {c["name"]: c for c in tr.case_checks(
            case, "Здравствуйте! Отличный выбор, XMAX 300 — надёжный скутер.", exp)}
        self.assertTrue(by["цены нет ни в каком виде"]["ok"])
        self.assertFalse(by["обязательное упоминание"]["ok"],
                         "уход от ответа зачтён — станция перестала различать три исхода")

    # ── корпус: станция ОДНА и она объявлена ────────────────────────────────────────────────
    def test_keis_17_obyavlyaet_uslovie_i_zapret(self):
        case = self.case17()
        self.assertEqual(case.get("needs"), tr.NEEDS_SEASON_CROSS)
        self.assertTrue(case.get("forbid_price"))
        self.assertTrue(case.get("require_any"))
        self.assertNotIn("price_figure", case.get("expect") or {},
                         "станция третьего исхода требует цифру — это второй исход, а не третий")
        self.assertIn("{when_cross}", " ".join(case["lines"]), "даты зашиты в реплику")


# ═══════════ 9. КОНТРОЛЬНАЯ ТОЧКА НАБОРА: цена обрыва — ОДИН КЕЙС (07.09.2026) ═══════════════

def _case_stub(outcomes=None, calls=None, spy=None, boom=()):
    """Вместо ЖИВОЙ головы: исход круга задан таблицей id → True | False | 'причина неизвестности'.

    Формат возврата дословно повторяет живой `run_case` (id, name, ok, unknown, checks, draft,
    note) — иначе `run_one_case` считал бы не то, что считает в бою."""
    outcomes = outcomes or {}

    def fake(case, ph, log=print):
        cid = str(case.get("id"))
        if spy is not None:
            spy(cid)
        if calls is not None:
            calls.append(cid)
        if cid in boom:
            raise RuntimeError("обрыв захода на кейсе " + cid)
        out = outcomes.get(cid, True)
        unk = out if isinstance(out, str) else None
        checks = [{"name": "чек", "ok": out is True, "expected": "ожидание", "fact": "факт"}]
        if unk:
            checks = [dict(c, unknown=True) for c in checks]
        return {"id": case.get("id"), "name": case.get("name"), "ok": out is True,
                "unknown": unk, "checks": checks, "draft": "черновик", "note": ""}
    return fake


class KontrolnayaTochkaNabora(unittest.TestCase):
    """ТАЙМАУТ ОБЯЗАН СТОИТЬ ОДИН КЕЙС, А НЕ ВЕСЬ ЗАХОД (07.09.2026).

    Живой повод, числом: корпус 17 кейсов даёт 38 кругов (13×2 + 4×3 классу), круг 43.7–46.6 с ⇒
    1661–1771 с при потолке захода 2700 с. Промежуточной записи у прогона не было ни одной —
    обрыв на 40-й минуте стоил ВСЁ и повтор за этим.

    Живой головы здесь нет: `run_case` подменяется стендом, `head_commit`/`dirty_tracked`/
    `tree_key`/`placeholders` — тоже, git не зовётся вовсе. Предмет класса — НЕ качество ответов, а
    единица записи, привязка к основанию и отказ на чужом основании."""

    IDS = (1, 2)                                   # два обычных кейса → по 2 круга каждому

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="trpoint_")
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.state = os.path.join(self.d, "точка.json")
        self.v = os.path.join(self.d, "verdict.json")
        self.ph = {"when": "с 6 по 11 октября"}
        self.cases = [{"id": i, "name": "кейс %d" % i} for i in self.IDS]
        self.casefile = os.path.join(self.d, "cases.json")
        with io.open(self.casefile, "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": i, "name": "кейс %d" % i} for i in range(1, 13)]}, f)
        keep = (tr.run_case, tr.head_commit, tr.dirty_tracked, tr.tree_key, tr.placeholders)

        def _restore():
            (tr.run_case, tr.head_commit, tr.dirty_tracked, tr.tree_key,
             tr.placeholders) = keep
        self.addCleanup(_restore)

    def quiet(self, *_a, **_k):
        pass

    def basis(self, **kw):
        b = {"commit": C40, "corpus": "cases.json", "corpus_sha": "3bae79ae6967a195", "runs": 2,
             "criterion": tr.criterion_text(2), "criterion_id": tr.CRITERION,
             "tree": "0000dead0000beef", "ph": tr.ph_key(self.ph)}
        b.update(kw)
        return b

    def doc(self):
        with io.open(self.state, encoding="utf-8") as f:
            return json.load(f)

    def cases_in_point(self):
        """Сколько ЗАКРЫТЫХ кейсов лежит в файле точки ПРЯМО СЕЙЧАС (по всем наборам)."""
        try:
            sets = self.doc().get("sets") or {}
        except (OSError, ValueError):
            return 0
        return sum(len((s or {}).get("cases") or {}) for s in sets.values())

    def gonka(self, point, outcomes=None, calls=None, spy=None, boom=()):
        tr.run_case = _case_stub(outcomes, calls=calls, spy=spy, boom=boom)
        return tr.run_corpus(self.cases, runs=2, ph=self.ph, log=self.quiet, point=point)

    # ── п.1: ТОЧКА СТАВИТСЯ ПОСЛЕ КЕЙСА, А НЕ ПОСЛЕ КРУГА ───────────────────────────────────
    def test_tochka_stavitsya_posle_keisa_a_ne_posle_kruga(self):
        """Половина кругов исходом НЕ является: точка обязана появиться после ВСЕХ кругов кейса."""
        seen = []
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt, spy=lambda _cid: seen.append(self.cases_in_point()))
        # четыре круга (2 кейса × 2): к началу 1-го и 2-го круга кейса 1 в точке НЕТ НИЧЕГО,
        # к началу обоих кругов кейса 2 — ровно один закрытый кейс.
        self.assertEqual(seen, [0, 0, 1, 1],
                         "точка встала после круга, а не после закрытого кейса")
        self.assertEqual(self.cases_in_point(), 2)

    def test_v_tochke_lezhit_ves_keis_celikom_a_ne_ego_itog(self):
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        rec = self.doc()["sets"][pt.key]["cases"]["1"]
        self.assertEqual(len(rec["results"]), 2, "круги кейса в точку не легли")
        self.assertEqual(rec["plan"]["rounds"], 2)
        self.assertEqual((rec["checks_ok"], rec["checks_all"]), (2, 2))
        self.assertEqual((rec["failed"], rec["unknown"]), ([], []))
        self.assertEqual(rec["results"][0]["draft"], "черновик", "черновик круга потерян")

    # ── п.5: ЗАКРЫТЫЙ КЕЙС НЕ ГОНЯЕТСЯ ЗАНОВО — ЧИСЛАМИ ─────────────────────────────────────
    def test_zakrytyi_keis_ne_gonyaetsya_zanovo_chislami(self):
        pt = tr.open_point(self.state, self.basis())
        c1 = []
        r1 = self.gonka(pt, calls=c1)
        self.assertEqual(len(c1), 4, "первый заход прогнал не четыре круга")
        self.assertEqual((pt.live_rounds, pt.taken_rounds), (4, 0))

        pt2 = tr.open_point(self.state, self.basis())      # ТО ЖЕ основание
        self.assertEqual(pt2.reset, "", "сброс на том же основании")
        c2 = []
        r2 = self.gonka(pt2, calls=c2)
        self.assertEqual(c2, [], "уже закрытые кейсы гонялись ВТОРОЙ раз")
        self.assertEqual((pt2.live_rounds, pt2.taken_rounds), (0, 4))
        # числа набора совпали дословно: возобновление даёт ТОТ ЖЕ вердикт, а не похожий
        self.assertEqual(r1[1:], r2[1:], "возобновлённый набор дал другие числа")
        self.assertEqual(len(r2[0]), 4, "круги из точки не доехали до отчёта")

    def test_obryv_posredi_nabora_stoit_odin_keis(self):
        """Обрыв на кейсе 2 → кейс 1 закрыт и уцелел; второй заход платит только за кейс 2."""
        pt = tr.open_point(self.state, self.basis())
        with self.assertRaises(RuntimeError):
            self.gonka(pt, boom=("2",))
        self.assertEqual(self.cases_in_point(), 1, "закрытый кейс не пережил обрыва")
        self.assertEqual(pt.live_rounds, 2)

        pt2 = tr.open_point(self.state, self.basis())
        c2 = []
        res, passed, ok, allc, failed, unknown, plan = self.gonka(pt2, calls=c2)
        self.assertEqual(c2, ["2", "2"], "второй заход заплатил не за один кейс")
        self.assertEqual((pt2.taken, pt2.live), (["1"], ["2"]))
        self.assertEqual((pt2.taken_rounds, pt2.live_rounds), (2, 2))
        self.assertEqual((passed, ok, allc), (2, 4, 4), "склеенный набор посчитан неверно")
        self.assertEqual(sorted(plan), ["1", "2"])

    def test_krasnyi_i_neizvestno_lozhatsya_v_tochku_naravne_s_zelyonym(self):
        """Точка — запись СОСТОЯВШЕГОСЯ замера, а не механизм повтора: перегон красного кейса
        перекатывал бы лотерею головы до нужного исхода."""
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt, outcomes={"1": False, "2": "голова смолчала"})
        pt2 = tr.open_point(self.state, self.basis())
        c2 = []
        _res, passed, ok, allc, failed, unknown, _plan = self.gonka(pt2, calls=c2)
        self.assertEqual(c2, [], "красный и неизвестный кейсы перегнали заново")
        self.assertEqual(passed, 0)
        self.assertEqual((ok, allc), (0, 2), "счёт чеков красного кейса не пережил точку")
        self.assertEqual(len(failed), 2, "красные круги из точки не назвались")
        self.assertEqual(len(unknown), 2, "«неизвестно» из точки потерялось")

    # ── п.2/п.3: ОТРИЦАТЕЛЬНЫЕ — ТОЧКА ВЫГЛЯДИТ ГОДНОЙ, А ОСНОВАНИЕ ДРУГОЕ ──────────────────
    def _green_point_then(self, **other):
        """Настоящий ЗЕЛЁНЫЙ набор под основанием А → открыть его основанием Б. → Point(Б)."""
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        sets = self.doc()["sets"]
        stored = list(sets.values())[0]["cases"]
        self.assertEqual(len(stored), 2, "стенд не собрал годной на вид точки")
        self.assertTrue(all(c["plan"]["ok"] for c in stored.values()), "кейсы в точке не зелёные")
        return tr.open_point(self.state, self.basis(**other))

    def test_otr_chuzhoi_kommit_otkaz_a_ne_zelyonoe(self):
        pt2 = self._green_point_then(commit=OTHER40)
        self.assertTrue(pt2.reset, "чужое основание принято МОЛЧА")
        self.assertIn("коммит", pt2.reset)
        self.assertEqual(pt2.done, {}, "зелёные кейсы чужого коммита доиспользованы")
        c2 = []
        self.gonka(pt2, calls=c2)
        self.assertEqual(len(c2), 4, "набор на новом основании начат не заново")

    def test_otr_chuzhoi_korpus_otkaz(self):
        pt2 = self._green_point_then(corpus_sha="ffffffffffffffff")
        self.assertIn("отпечаток корпуса", pt2.reset)
        self.assertEqual(pt2.done, {})

    def test_otr_drugoe_chislo_krugov_otkaz(self):
        pt2 = self._green_point_then(runs=3, criterion=tr.criterion_text(3))
        self.assertIn("кругов базово", pt2.reset)
        self.assertEqual(pt2.done, {})

    def test_otr_drugoi_kriterii_otkaz(self):
        pt2 = self._green_point_then(criterion="E: всем по два круга, зелёными обязаны быть все")
        self.assertIn("критерий", pt2.reset)
        self.assertEqual(pt2.done, {})

    def test_prichina_pokazyvaet_DVA_RAZNYH_znacheniya_a_ne_dva_odinakovyh(self):
        """Живой замер 07.09: критерий на 2 и на 3 круга совпадает первыми 46 символами, и обрезка
        печатала «X против X» — прибор называл расхождение и показывал два одинаковых значения."""
        why = tr.basis_diff(self.basis(criterion=tr.criterion_text(2)),
                            self.basis(criterion=tr.criterion_text(3)))
        self.assertEqual(len(why), 1)
        left, right = why[0].split(" против ")
        self.assertNotEqual(left.split(": ", 1)[1], right,
                            "причина показала два одинаковых значения — читатель решит, что врут")
        self.assertIn("2", left)
        self.assertIn("3", right)

    def test_otr_pravki_dereva_otkaz(self):
        """Коммит тот же, а код на диске другой: правка дерева — это другой замер."""
        pt2 = self._green_point_then(tree="11112222aaaabbbb")
        self.assertIn("правки дерева", pt2.reset)
        self.assertEqual(pt2.done, {})

    def test_otr_drugie_podstanovki_otkaz(self):
        """Даты уезжают сами (месяц, живая таблица периодов) — кейс на других датах не тот же."""
        pt2 = self._green_point_then(ph=tr.ph_key({"when": "с 6 по 11 ноября"}))
        self.assertIn("подстановки", pt2.reset)
        self.assertEqual(pt2.done, {})

    def test_otr_ne_znayu_sovpadeniem_ne_schitaetsya(self):
        """Два «не знаю» — это не «то же самое», а два неизмеренных факта."""
        self.assertTrue(tr.basis_diff(self.basis(tree="?"), self.basis(tree="?")),
                        "«?» против «?» прошло как совпадение")
        self.assertTrue(tr.basis_diff(self.basis(ph="?"), self.basis()))
        self.assertEqual(tr.basis_diff(self.basis(), self.basis()), [],
                         "одинаковые основания разошлись")

    def test_otr_podlog_klyucha_ne_prokhodit_verim_polyam(self):
        """Ключ основания подменён на наш, а поля внутри чужие → верим ПОЛЯМ, а не ключу."""
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        d = self.doc()
        mine = d["sets"].pop(pt.key)
        mine["basis"]["commit"] = OTHER40                  # поля чужие, ключ прежний
        d["sets"][pt.key] = mine
        with io.open(self.state, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        pt2 = tr.open_point(self.state, self.basis())
        self.assertIn("основание НЕ то", pt2.reset)
        self.assertEqual(pt2.done, {})

    def test_prezhnii_nabor_ne_stiraetsya_a_lozhitsya_ryadom(self):
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        pt2 = tr.open_point(self.state, self.basis(commit=OTHER40))
        self.gonka(pt2)
        sets = self.doc()["sets"]
        self.assertEqual(len(sets), 2, "прежний набор затёрт новым основанием")
        self.assertIn(pt.key, sets)
        self.assertEqual(len(sets[pt.key]["cases"]), 2, "прежний набор потерял кейсы")
        # вернулись на прежнее основание — прежний набор нашёлся и доигрывается
        pt3 = tr.open_point(self.state, self.basis())
        self.assertEqual(pt3.reset, "")
        self.assertEqual(sorted(pt3.done), ["1", "2"])

    # ── битая/чужая запись КЕЙСА: гоняем заново и называем причину ──────────────────────────
    def test_bitaya_zapis_keisa_gonyaetsya_zanovo_s_prichinoi(self):
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        pt2 = tr.open_point(self.state, self.basis())
        pt2.done["1"] = {"id": "1", "plan": {"rounds": 2}}          # без кругов и счёта чеков
        rec, why = pt2.get(self.cases[0], 2)
        self.assertIsNone(rec)
        self.assertIn("кругов", why)
        c2 = []
        self.gonka(pt2, calls=c2)
        self.assertEqual(c2, ["1", "1"], "битая запись доиспользована")

    def test_zapis_pod_chuzhim_klyuchom_ne_prinimaetsya(self):
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        pt.done["1"]["id"] = "9"
        self.assertIn("чужим ключом", pt.get(self.cases[0], 2)[1])

    def test_men_she_krugov_chem_trebuet_kriterii_ne_prinimaetsya(self):
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        pt.done["1"]["results"] = pt.done["1"]["results"][:1]
        self.assertIn("кругов в записи", pt.get(self.cases[0], 2)[1])

    def test_faila_tochki_ne_bylo_eto_ne_sbros(self):
        pt = tr.open_point(os.path.join(self.d, "нет.json"), self.basis())
        self.assertEqual((pt.reset, pt.done), ("", {}))

    def test_bityi_fail_tochki_nazyvaet_prichinu_i_nachinaet_zanovo(self):
        with io.open(self.state, "w", encoding="utf-8") as f:
            f.write("{это не json")
        pt = tr.open_point(self.state, self.basis())
        self.assertIn("не разобран", pt.reset)
        self.assertEqual(pt.done, {})

    # ── вердикт ОБЯЗАН СКАЗАТЬ, что собран возобновлением ───────────────────────────────────
    def test_verdikt_govorit_chto_sobran_vozobnovleniem(self):
        pt = tr.open_point(self.state, self.basis())
        self.gonka(pt)
        pt2 = tr.open_point(self.state, self.basis())
        _r, passed, ok, allc, failed, _unk, plan = self.gonka(pt2)
        rec = tr.build_verdict(C40, 2, passed, ok, allc, 2, True, failed, "sha", now=1.0,
                               plan=plan, point=tr.point_info(pt2))
        self.assertEqual(rec["resumed"], 2)
        self.assertEqual(rec["resumed_rounds"], 4)
        self.assertEqual(sorted(rec["resumed_why"]), ["1", "2"])
        self.assertEqual(rec["point_reset"], "")

    def test_verdikt_nesyot_prichinu_sbrosa(self):
        pt2 = self._green_point_then(commit=OTHER40)
        rec = tr.build_verdict(OTHER40, 2, 2, 4, 4, 2, True, [], "sha", now=1.0,
                               point=tr.point_info(pt2))
        self.assertIn("коммит", rec["point_reset"])
        self.assertEqual(rec["resumed"], 0)

    def test_staryi_vyzov_bez_tochki_ne_slomalsya(self):
        rec = tr.build_verdict(C40, 12, 12, 96, 96, 2, True, [], "sha", now=1.0)
        self.assertEqual((rec["resumed"], rec["resumed_rounds"], rec["point_reset"]), (0, 0, ""))

    # ── ГЛАВНЫЙ ОТРИЦАТЕЛЬНЫЙ ТЕСТ (п.3): годная на вид точка чужого основания НЕ зеленит ────
    def test_otr_glavnyi_zelyonaya_tochka_chuzhogo_osnovaniya_ne_dayot_zelyonogo_verdikta(self):
        """Точка на месте, 12 кейсов внутри, ВСЕ чеки зелёные — а основание другое. Прибор обязан
        показать ОТКАЗ и померить заново, а не отдать чужую зелень за свою."""
        big = [{"id": i, "name": "кейс %d" % i} for i in range(1, 13)]
        tr.head_commit = lambda: C40
        tr.dirty_tracked = lambda: []
        tr.tree_key = lambda: "0000dead0000beef"
        tr.placeholders = lambda: self.ph
        # 1) ЗЕЛЁНЫЙ набор под коммитом C40 — настоящий, собранный живым писателем точки
        basis_a = tr.point_basis(C40, cc.corpus_sha(self.casefile), 2, self.ph,
                                 corpus="cases.json", tree="0000dead0000beef")
        pt = tr.open_point(self.state, basis_a)
        tr.run_case = _case_stub()
        tr.run_corpus(big, runs=2, ph=self.ph, log=self.quiet, point=pt)
        self.assertEqual(len(pt.done), 12)
        self.assertTrue(all(v["plan"]["ok"] for v in pt.done.values()))

        # 2) тот же файл точки, но ВЕРШИНА ДРУГАЯ, и живой прогон на ней КРАСНЫЙ
        tr.head_commit = lambda: OTHER40
        tr.run_case = _case_stub({str(i): False for i in range(1, 13)})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = tr.main(["--cases", self.casefile, "--out", self.v, "--runs", "2",
                          "--no-write", "--state", self.state])
        txt = out.getvalue()
        self.assertEqual(rc, 1, "чужая зелёная точка выдана за свой зелёный вердикт")
        self.assertIn("ИТОГ: RED", txt)
        self.assertIn("ТОЧКА СБРОШЕНА", txt)
        self.assertIn("коммит", txt)
        self.assertIn("взято из неё кейсов 0", txt)
        self.assertFalse(os.path.exists(self.v), "замер тронул реестр вердиктов")
        # прежний зелёный набор при этом НЕ УНИЧТОЖЕН — он лежит рядом под своим основанием
        self.assertEqual(len(self.doc()["sets"]), 2)
        self.assertEqual(len(self.doc()["sets"][pt.key]["cases"]), 12)

    def test_tochka_ne_otkryvaet_zapis_reestra(self):
        """`--state` — это память о закрытых кейсах, а не вердикт: реестра он не касается."""
        a = tr.build_parser().parse_args(["--state", self.state])
        self.assertFalse(tr.writes_registry(a))


# ───────── ПУСТОЕ ОЖИДАНИЕ = ТРЕТИЙ ИСХОД, А НЕ ПРОВАЛ (класс, заведён 11.09.2026) ─────────
# ЖИВОЙ СЛУЧАЙ, из которого класс родился: кейс 3 предполёта 11.09 покраснел чеком «строка J
# дословно», хотя ни сборка, ни голова не виноваты — записка круга 1 quote-блока НЕ НЕСЛА, и
# чек искал строку, которой не существует (docs/artifacts/2026-09-11-ЗАПИСКА-kejs3-sborka-1109.md).
#
# ГОЛОВЫ ЗДЕСЬ НЕТ НИ ОДНОГО КРУГА и сети нет ни одного запроса: обе записки — строки ниже.
# Это ОТРИЦАТЕЛЬНЫЙ тест класса: фикстура A обязана дать «неизвестно», фикстура B — остаться
# ПРОВАЛОМ, потому что там ожидание есть и судить ЕСТЬ чем. Ослабь любую из двух — и правка
# превратится либо в заплату на один чек, либо в глушилку настоящих красных.
J_LINE = "NMAX — 307 ฿/день; итого 1535 ฿; депозит 3000 ฿; свободен на эти даты."
D_LINE = "Доставка в Раваи — 590 ฿ (один раз, туда)."
NOTE_WITH = ("ЦЕНА (данные Календаря): NMAX 155, 06.10.2026–11.10.2026.\n"
             "<<<QUOTE>>>\n" + J_LINE + "\n<<<END_QUOTE>>>\n"
             "<<<DELIVERY>>>\n" + D_LINE + "\n<<<END_DELIVERY>>>\n")
NOTE_WITHOUT = ("ЦЕНА: не удалось однозначно разобрать модель/даты для Календаря — НЕ называй "
                "цифр и попроси уточнить модель и даты.\n")
DRAFT_NO_FIGURE = ("Здравствуйте! Спасибо за обращение. Подскажите, пожалуйста, точную модель и "
                   "даты — подберу вариант и всё оформим.")
TRANSCRIPT = ("[клиент]: Здравствуйте! Хочу арендовать NMAX 155 с 6 по 11 октября.\n"
              "[клиент]: Вот точка доставки: https://www.google.com/maps/place/Rawai+Beach/"
              "@7.771,98.327,15z")
CASE_PUSTO = {"id": 3, "name": "фикстура кейса 3 (голова не звалась)", "lang": "ru",
              "zone": "Раваи", "zone_price": 590,
              "expect": {"quote": True, "delivery": True, "price_figure": True, "sheet": True}}
# ЧЕТЫРЕ ЧЕКА КЛАССА: ожидание каждого приходит из ЦЕНОВОЙ ЗАПИСКИ и может не собраться вовсе.
CLASS_CHECKS = ("строка J дословно", "доставка = цена зоны", "цена цифрой",
                "сетка прайса дословно")


class PustoeOzhidanie(unittest.TestCase):
    """Правило ОДНО и на все чеки класса: ожидание не собралось → «судить нечем»."""

    def checks(self, note, draft=DRAFT_NO_FIGURE, case=None):
        case = dict(case or CASE_PUSTO, _transcript=TRANSCRIPT)
        hints = suggest.extract_booking_hints(TRANSCRIPT)
        exp = tr.expectations(case, TRANSCRIPT, note, hints)
        return {c["name"]: c for c in tr.case_checks(case, draft, exp)}

    def test_pustoe_ozhidanie_daet_neizvestno_a_ne_proval(self):
        """ФИКСТУРА A. Записки без quote-блока достаточно, чтобы чек перестал судить."""
        got = self.checks(NOTE_WITHOUT)
        chk = got["строка J дословно"]
        self.assertTrue(chk.get("unknown"),
                        "чек красит RED там, где ожидания нет вовсе — судить было нечем")
        self.assertIn("quote-блок", chk["unknown"])

    def test_pravilo_odno_na_vse_cheki_klassa_a_ne_zaplata(self):
        """Заплата на один чек не засчитана: все ЧЕТЫРЕ чека класса обязаны отдать третий исход."""
        got = self.checks(NOTE_WITHOUT)
        got_unknown = [n for n in CLASS_CHECKS if got.get(n, {}).get("unknown")]
        self.assertEqual(sorted(got_unknown), sorted(CLASS_CHECKS),
                         "правило накрыло не все чеки класса: " + repr(got_unknown))

    def test_ozhidanie_est_otvet_bez_cifry_ostayotsya_provalom(self):
        """ФИКСТУРА B. Блок в записке ЕСТЬ — судить есть чем, и провал обязан остаться провалом."""
        got = self.checks(NOTE_WITH)
        for name in ("строка J дословно", "доставка = цена зоны", "цена цифрой"):
            chk = got[name]
            self.assertFalse(chk["ok"], f"[{name}] ответ без цифры внезапно зелёный")
            self.assertFalse(chk.get("unknown"),
                             f"[{name}] настоящий красный проглочен «неизвестным»")

    def test_neizvestnoe_ne_prevrashchaetsya_v_zelyonoe(self):
        """ПРЯМОЙ ЗАПРЕТ задания: пустое ожидание в ЗЕЛЁНОЕ не переводится.

        Ловушка была реальной: у чека «цена цифрой» есть запасная ветка «хоть какая-то цифра с
        валютой», и на записке БЕЗ блоков она красила зелёным цифру, взятую головой из воздуха."""
        draft = "Здравствуйте! Аренда выйдет 1500 ฿ за весь срок."
        chk = self.checks(NOTE_WITHOUT, draft=draft)["цена цифрой"]
        self.assertTrue(chk.get("unknown"),
                        "цифра из воздуха зачтена зелёным: сверять было не с чем")

    def test_krug_uhodit_v_neizvestno_a_ne_v_krasnoe(self):
        """ЖИВОЙ `run_case`: круг с несобранным ожиданием — НЕ зелёный и НЕ красный.

        Голова подменена стендом (сети нет), но наблюдатель `_HeadWatch` остаётся настоящим:
        стенд её «спрашивает», поэтому молчанием исход не объясняется — он объясняется ЗАПИСКОЙ."""
        case = dict(CASE_PUSTO, lines=["Здравствуйте! Хочу арендовать NMAX 155 с 6 по 11 октября."])
        keep = (tr.generate, suggest._default_llm)
        self.addCleanup(lambda: (setattr(tr, "generate", keep[0]),
                                 setattr(suggest, "_default_llm", keep[1])))
        suggest._default_llm = lambda system, user: DRAFT_NO_FIGURE
        tr.generate = lambda transcript, first: (
            suggest._default_llm("s", "u"), NOTE_WITHOUT,
            suggest.extract_booking_hints(transcript))
        res = tr.run_case(case, {}, log=lambda *a, **k: None)
        self.assertTrue(res.get("unknown"), "круг без ожидания назван красным")
        self.assertFalse(res["ok"], "круг без ожидания зачтён зелёным")
        self.assertIn("судить нечем", res["unknown"])

    def test_grafa_ishodov_i_trevoga_pro_pribor(self):
        """Число неизвестных звучит СЛОВАМИ, и доля выше пятой части объявляется тревогой."""
        rows = [{"ok": True, "unknown": None}, {"ok": True, "unknown": None},
                {"ok": False, "unknown": None}, {"ok": False, "unknown": "судить нечем: X"}]
        c = tr.outcome_counts(rows)
        self.assertEqual((c["green"], c["red"], c["unknown"], c["total"]), (2, 1, 1, 4))
        self.assertTrue(c["alarm"], "доля неизвестных 1/4 выше пятой части, а тревоги нет")
        self.assertIn("ПРИБОР", c["alarm"])
        self.assertIn("НЕИЗВЕСТНО 1 из 4", tr.outcome_line(rows))
        tihie = [{"ok": True, "unknown": None}] * 9 + [{"ok": False, "unknown": "судить нечем: X"}]
        self.assertFalse(tr.outcome_counts(tihie)["alarm"], "тревога поднята на доле 1/10")

    def test_priznaki_zapiski_ryadom_s_chernovikom(self):
        """Причина красного перестаёт быть невосстановимой: признаки записки видны в отчёте."""
        self.assertIn("quote-блок: есть", tr.note_marks(NOTE_WITH))
        self.assertIn("доставка: есть", tr.note_marks(NOTE_WITH))
        bez = tr.note_marks(NOTE_WITHOUT)
        self.assertIn("quote-блок: НЕТ", bez)
        self.assertIn("не удалось однозначно разобрать", bez, "ветка записки себя не назвала")
        self.assertEqual(tr.note_marks(""), "записки нет вовсе")

    def test_otchyot_pokazyvaet_neizvestnye_otdelno(self):
        """Круг «неизвестно» не проваливается между строк: свой раздел, причина и записка."""
        results = [{"id": "3", "name": "кейс 3", "run": 1, "ok": False,
                    "unknown": "[строка J дословно] судить нечем: quote-блока нет",
                    "checks": [], "draft": DRAFT_NO_FIGURE, "note": NOTE_WITHOUT}]
        md = tr.report_md(_rec(result="unknown"), results, C40, 1)
        self.assertIn("НЕИЗВЕСТНО — судить было нечем", md)
        self.assertIn("Исходы кейсо-прогонов:", md)
        self.assertIn("НЕИЗВЕСТНО 1 из 1", md)
        self.assertIn("quote-блок: НЕТ", md)


class TestPodborOdnaModelChekKorpusa(unittest.TestCase):
    """ЧЕК, КОТОРЫЙ СУДИТ СЛОВО ВЛАДЕЛЬЦА 12.09.2026 («только про спрошенную модель и одну-две
    альтернативы»). Предмет — QUOTE-БЛОК ЗАПИСКИ: строки в него вставляет КОД, и правило говорит
    ровно о том, сколько моделей код посчитал и подал. Текст головы предметом быть не может —
    пересказ блока своими словами краснил бы здоровый ответ.

    ОТРИЦАТЕЛЬНЫЙ ТЕСТ ЧИСЛОМ (требование задания). Фикстура `NOTE_10` — ДОСЛОВНАЯ записка из
    снимка `exam_shots/case-1.json`, собранного 11.09.2026 на коммите 1c2ccee: спрошен XMAX 300
    (макси-скутер), в блоке ДЕСЯТЬ моделей, из них семь мотоциклов. На ней:
      • НАБОР ЧЕКОВ БЕЗ НОВОГО ОЖИДАНИЯ — зелёный ЦЕЛИКОМ (это и есть «прошёл 12 из 12» 11.09);
      • тот же набор С ожиданием `one_model` — КРАСНЫЙ ровно одним чеком, новым.
    На записке с тремя моделями (спрошенная + две альтернативы) чек зелёный в обоих наборах."""

    HEAD = ("ЦЕНА из Календаря бронирования ПОСЧИТАНА по НЕСКОЛЬКИМ моделям: первой в блоке идёт "
            "та, что спросил клиент.\n")
    LINES_10 = [
        "XMAX 300 New Gen — 682 ฿/день; итого 3410 ฿; депозит: паспорт; свободен на эти даты.",
        "ADV 350 — 634 ฿/день; итого 3170 ฿; депозит: паспорт; свободен на эти даты.",
        "FORZA 300 — 502 ฿/день; итого 2510 ฿; депозит: паспорт; свободен на эти даты.",
        "XSR 155 — 457 ฿/день; итого 2285 ฿; депозит: паспорт; свободен на эти даты.",
        "CBR 650R — 1486 ฿/день; итого 7430 ฿; депозит: паспорт; свободен на эти даты.",
        "CB 650R — 1486 ฿/день; итого 7430 ฿; депозит: паспорт; свободен на эти даты.",
        "CB 300R — 656 ฿/день; итого 3280 ฿; депозит: паспорт; свободен на эти даты.",
        "MT-03 — 745 ฿/день; итого 3725 ฿; депозит: паспорт; свободен на эти даты.",
        "NINJA 400 — 1179 ฿/день; итого 5895 ฿; депозит: паспорт; свободен на эти даты.",
        "VULCAN 650S — 1100 ฿/день; итого 5500 ฿; депозит: паспорт; свободен на эти даты.",
    ]
    CASE = {"id": 1, "lang": "ru", "expect": {"quote": True, "price_figure": True},
            "_transcript": "[клиент]: Здравствуйте! Хочу XMAX 300 с 6 по 11 октября"}
    CHECK = "подбор: спрошенная первая, альтернатив не больше двух"

    def _note(self, lines):
        return (self.HEAD + suggest._QUOTE_OPEN + "\n" + "\n".join(lines) + "\n"
                + suggest._QUOTE_CLOSE)

    def _draft(self, lines):
        return "Здравствуйте! Вот что можем предложить на эти даты:\n" + "\n".join(lines)

    def _checks(self, lines, one_model=None):
        case = dict(self.CASE)
        if one_model:
            case["expect"] = dict(case["expect"], one_model=one_model)
        note = self._note(lines)
        exp = tr.expectations(case, case["_transcript"], note,
                              {"model": "XMAX 300", "has_dates": True})
        return {c["name"]: c for c in tr.case_checks(case, self._draft(lines), exp)}

    def test_desyat_modelei_do_pravki_zelyono_posle_krasno(self):
        before = self._checks(self.LINES_10)
        red_before = [n for n, c in before.items() if not c["ok"]]
        self.assertEqual(red_before, [], "набор БЕЗ нового ожидания обязан быть зелёным на десяти "
                                         "моделях — иначе отрицательный тест меряет чужой чек")
        self.assertNotIn(self.CHECK, before, "без one_model чека не существует вовсе")
        after = self._checks(self.LINES_10, one_model="XMAX 300")
        self.assertFalse(after[self.CHECK]["ok"], "десять моделей прошли новый чек")
        self.assertIn("моделей 10", after[self.CHECK]["fact"])
        red_after = [n for n, c in after.items() if not c["ok"]]
        self.assertEqual(red_after, [self.CHECK], "покраснел не только новый чек: %s" % red_after)

    def test_tri_modeli_zelyono_i_do_i_posle(self):
        three = self.LINES_10[:3]
        self.assertEqual([n for n, c in self._checks(three).items() if not c["ok"]], [])
        after = self._checks(three, one_model="XMAX 300")
        self.assertTrue(after[self.CHECK]["ok"], after[self.CHECK]["fact"])
        self.assertEqual([n for n, c in after.items() if not c["ok"]], [])

    def test_pervaya_ne_sproshennaya_krasnit_dazhe_na_dvuh(self):
        """Не только ЧИСЛО: спрошенная обязана быть ПЕРВОЙ. Две модели, но первая чужая — красно."""
        by = self._checks(self.LINES_10[1:3], one_model="XMAX 300")
        self.assertFalse(by[self.CHECK]["ok"])
        self.assertIn("ПЕРВАЯ НЕ СПРОШЕННАЯ", by[self.CHECK]["fact"])

    def test_bez_quote_bloka_ishod_neizvesten_a_ne_zelyon(self):
        """Записки нет — судить НЕЧЕМ. Третий исход, а не зелёное (правило класса `no_basis`)."""
        case = dict(self.CASE, expect=dict(self.CASE["expect"], one_model="XMAX 300"))
        exp = tr.expectations(case, case["_transcript"], "ЦЕНА: уточню и вернусь",
                              {"model": "XMAX 300", "has_dates": True})
        by = {c["name"]: c for c in tr.case_checks(case, "Уточню и вернусь.", exp)}
        self.assertTrue(by[self.CHECK].get("unknown"), "пустая записка дала вердикт вместо «нечем»")

    def test_imena_modelei_beryotsya_iz_bloka_a_ne_iz_prozy(self):
        """`quote_models` читает ОБА формата строк кода и не считает моделью служебную строку."""
        self.assertEqual(tr.quote_models("\n".join(self.LINES_10[:2])),
                         ["XMAX 300 New Gen", "ADV 350"])
        self.assertEqual(tr.quote_models("- NMAX 155: 307 ฿/день; итого 1535 ฿."), ["NMAX 155"])
        self.assertEqual(tr.quote_models(""), [])
        self.assertEqual(tr.quote_models("цена и депозит указаны ЗА КАЖДЫЙ юнит"), [])

    def test_fikstura_doslovna_zhivomu_snimku_1109(self):
        """Литерал теста — ДОСЛОВНАЯ копия живой записки, а не пересказ. Снимка нет на диске —
        исход НЕИЗВЕСТНО (тест пропущен), а не тихое «сошлось»."""
        path = os.path.join(REPO, "exam_shots", "case-1.json")
        if not os.path.exists(path):
            self.skipTest("снимка 11.09 нет на диске — сверять литерал не с чем")
        with io.open(path, encoding="utf-8") as f:
            shot = json.load(f)
        live = suggest._quote_block_from_note(shot.get("note") or "")
        self.assertEqual([ln for ln in (live or "").split("\n") if ln.strip()], self.LINES_10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
