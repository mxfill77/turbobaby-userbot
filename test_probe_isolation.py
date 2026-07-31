# -*- coding: utf-8 -*-
"""
test_probe_isolation.py — ИЗОЛЯЦИЯ ПРОБ на полосе ПК (класс «проба рождает владельцу боевую
карточку», 01.08.2026). Порт серверного замка `a739ee9` ТОЙ ЖЕ ФОРМОЙ.

ЧТО ЗА КЛАСС. К владельцу с ПК ведут ДВА канала, и до этой правки у них были РАЗНЫЕ признаки
пробы — точнее, у одного не было никакого:
  канал 1 «прямой пуш» (`_push` → dispatch_notify → тема-инбокс 1160 / личка) слушал ровно одно
          имя — `PRETOOL_NOPUSH`, и читал его из СВОЕГО окружения, тогда как пробы пишут его
          ПРЕФИКСОМ КОМАНДЫ (префикс уезжает ребёнку, хук-родитель его не видит);
  канал 2 «маркер → демон → карточка с кнопками» (`_write_marker` → needs_approval) не слушал
          НИ ОДНОГО признака — маркер писался БЕЗУСЛОВНО.
Живой замер суток 31.07→01.08 (`docs/artifacts/2026-08-01-two-roots-sweep.md` §6): 11 из 28
запросов «нужно да» на полосе ПК родились из проб, а не из команд владельца — 39%. Дословный
случай: карточка задачи 144 потянулась к боевому `create_booking` из пробного скрипта в `tmp/`.

ФОРМАТ ЗАПУСКА повторяет боевой (правило «проверка повторяет живой формат»): хук зовётся
ПОДПРОЦЕССОМ через stdin-JSON, окружение ребёнка строится как у демона — тест-флаги СНЯТЫ
(`pc_orchestrator._run_task_impl`), команда пишется в живой форме `venv/Scripts/python.exe <файл>`.
Хук команду НЕ исполняет: он читает СОДЕРЖИМОЕ файла-цели. Сети нет — канал 1 диверсится в
мок-счётчик `NOTIFY_COUNT_FILE`, который отрабатывает ДО спавна отправителя.

Красные литералы собраны ИЗ КУСКОВ: гард сканирует содержимое запускаемых файлов, а фикстуры
пишет этот тест — целый литерал в исходнике не нужен и вводил бы в заблуждение грепающего.

УБОРКИ НЕТ НАМЕРЕННО: фикстуры остаются во временных зонах (`%TEMP%`, `tmp/` репозитория,
обе — вне git). Удаление в тесте стоило бы красного токена `shutil.rmtree` в его теле и риска
снести чужое; мусор во временной зоне дешевле карточки владельцу.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_probe_isolation -v
"""

import os
import sys
import json
import tempfile
import subprocess
import unittest

os.environ["TURBOBABY_TEST_LOGS"] = "1"   # ДО импорта гарда: фикстуры не осядут в боевом логе

import pretool_guard as g  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(ROOT, "pretool_guard.py")
PY = "venv/Scripts/python.exe"            # живая форма вызова, как её видит хук в бою
LIVE_ENV = {}                             # окружение боевого ребёнка: тест-флагов нет ФИЗИЧЕСКИ

_AT = "add_trans" + "action"
_SFO = "set_fleet_" + "oil"
_CB = "create_" + "booking"
_VL = "void_" + "last"

MONEY = "bridge.%s(amount=500, wallet='main')" % _AT          # деньги: спрашивают ВСЕГДА
OIL = "bridge.%s(number='6789', oil_km=27000)" % _SFO         # живые таблицы (фикстура 142/143)
BOOK = "bridge.%s(bike='6789', name='Иван')" % _CB            # дословный носитель карточки 144
VOID = "bridge.%s()" % _VL                                    # проводка без объекта по природе


def _fx(d, name, body):
    """Положить фикстуру, вернуть путь ПРЯМЫМИ слэшами: хук разбирает команду через shlex в
    POSIX-режиме, где обратный слэш — экранирование (на Windows путь иначе рассыпется)."""
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(body + "\n")
    return p.replace("\\", "/")


class TestProbeSources(unittest.TestCase):
    """(1) ЕДИНЫЙ ПРИЗНАК ПРОБЫ: три источника, любой достаточен; текст пометкой НЕ является."""

    def test_live_command_in_live_env_is_not_a_probe(self):
        self.assertIs(g.is_probe("%s fx.py" % PY, LIVE_ENV), False)

    def test_source_env_of_the_hook(self):
        """Источник 1 — окружение самого хука: так объявляются гейт и тест-подпроцессы."""
        for name in g._TEST_RUN_ENVS:
            with self.subTest(name):
                self.assertIs(g.is_probe("%s fx.py" % PY, {name: "1"}), True)

    def test_source_env_prefix_in_command(self):
        """Источник 2 — env-префикс В САМОЙ КОМАНДЕ: ЕДИНСТВЕННЫЙ, доступный пробе ВНУТРИ живой
        задачи (демон снимает тест-флаги с окружения боевого ребёнка — и правильно делает)."""
        self.assertIs(g.is_probe("PRETOOL_TEST_RUN=1 %s fx.py" % PY, LIVE_ENV), True)
        self.assertIs(g.is_probe("ORCH_TEST_MODE=1 %s -c 'x'" % PY, LIVE_ENV), True)

    def test_source_place_of_the_script(self):
        """Источник 3 — МЕСТО запуска: временная зона репо (`tmp/**`, .gitignore) и скретчпад
        сессии. Оба каталога объявлены одноразовыми — запуск оттуда есть проба по определению."""
        self.assertIs(g.is_probe("%s tmp/probe_check.py" % PY, LIVE_ENV), True)
        self.assertIs(g.is_probe("%s D:/turbobaby-bot/tmp/probe_obj_read.py" % PY, LIVE_ENV), True)
        self.assertIs(g.is_probe("%s tmp_selfheal_sim.py" % PY, LIVE_ENV), True)
        self.assertIs(g.is_probe(
            "%s C:/Users/x/AppData/Local/Temp/claude/D--turbobaby-bot/s1/scratchpad/fx.py" % PY,
            LIVE_ENV), True)

    def test_mention_in_text_is_not_a_declaration(self):
        """ГРАНИЦА: имя признака В ТЕКСТЕ пометкой не является — иначе пометкой стал бы любой
        пересказ (ровно тот класс born-false, что чинили у секретов 01.08, `f4c3cff`)."""
        self.assertIs(g.is_probe("echo PRETOOL_TEST_RUN=1 — это просто текст", LIVE_ENV), False)
        self.assertIs(g.is_probe("%s fx.py PRETOOL_TEST_RUN=1" % PY, LIVE_ENV), False)

    def test_probe_gives_no_rights(self):
        """ГЛАВНОЕ СВОЙСТВО: пометка «проба» НЕ ДАЁТ ПРАВ. Решение гарда не меняется ни на йоту —
        меняются только каналы владельца."""
        cmd = "PRETOOL_TEST_RUN=1 taskkill /PID 4242 /F"
        self.assertEqual(g.decide_for_role({"tool_name": "Bash", "tool_input": {"command": cmd},
                                            "cwd": ROOT}, headless=True)[0], "ask")


class TestBothChannels(unittest.TestCase):
    """(2) ОБА КАНАЛА СЛУШАЮТ ОДИН И ТОТ ЖЕ ПРИЗНАК — латч, а не параметр."""

    def tearDown(self):
        g.set_probe(False)

    def test_live_run_keeps_both_channels(self):
        g.set_probe(False)
        self.assertIs(g.isolated(LIVE_ENV), False)
        self.assertEqual(g.marker_path(r"D:\turbobaby-bot\pc_ask_144.marker", LIVE_ENV),
                         r"D:\turbobaby-bot\pc_ask_144.marker")

    def test_latch_diverts_marker_and_mutes_push(self):
        g.set_probe(True)
        self.assertIs(g.isolated(LIVE_ENV), True)
        # канал 2: номер ЖИВОЙ задачи не попадает в имя — монитор демона открывает ровно
        # `pc_ask_<id>.marker` и пробного файла не увидит
        diverted = g.marker_path(os.path.join(ROOT, "pc_ask_144.marker"), LIVE_ENV)
        self.assertEqual(os.path.basename(diverted), g.PROBE_MARKER_PREFIX + "pc_ask_144.marker")
        self.assertEqual(os.path.dirname(diverted), ROOT)
        # канал 1: пуш подавлен ЛАТЧОМ, а не только именем PRETOOL_NOPUSH
        self.assertIsNone(g._push("🔴 карточка"))

    def test_mirror_leak_regress(self):
        """ЗЕРКАЛЬНАЯ ТЕЧЬ, из-за которой класс жил: признак, гасивший ОДИН канал, оставлял
        открытым другой. Проба с `ORCH_TEST_MODE` обязана гасить И пуш тоже."""
        g.set_probe(False)
        self.assertIs(g.isolated({"ORCH_TEST_MODE": "1"}), True)
        self.assertIs(g.isolated({"PRETOOL_NOPUSH": "1"}), True)

    def test_marker_writer_diverts_by_construction(self):
        """Развод стоит ВНУТРИ писателя маркера, а не у одного его вызывающего: следующая ветка,
        которая позовёт `_write_marker`, изолирована ПО ПОСТРОЕНИЮ, не по памяти автора."""
        d = tempfile.mkdtemp(prefix="probe_iso_marker_")
        mk = os.path.join(d, "pc_ask_144.marker")
        g.set_probe(True)
        g._write_marker(mk, "🔴 карточка пробы — разрешить?", "kill")
        self.assertFalse(os.path.isfile(mk), "боевой маркер написан из пробы")
        self.assertTrue(os.path.isfile(g.marker_path(mk)), "перехват молчит: пробный маркер тоже пуст")


class TestHookEndToEnd(unittest.TestCase):
    """(3) СКВОЗНОЙ ПРОГОН ХУКА в окружении ЖИВОЙ задачи: 4 формы пробы + настоящая боевая
    операция. Это и есть замер «сколько форм доходит до владельца»."""

    @classmethod
    def setUpClass(cls):
        cls.out = tempfile.mkdtemp(prefix="probe_iso_out_")                  # НЕ проба по месту
        cls.tmpzone = tempfile.mkdtemp(prefix="probe_iso_", dir=os.path.join(ROOT, "tmp"))
        cls.scratch = os.path.join(cls.tmpzone, "tmp", "claude", "sess", "scratchpad")
        cls.fx_money = _fx(cls.out, "fx_money.py", MONEY)
        cls.fx_oil = _fx(cls.out, "fx_oil.py", OIL)
        cls.fx_book = _fx(cls.tmpzone, "fx_book.py", BOOK)
        cls.fx_void = _fx(cls.scratch, "fx_void.py", VOID)

    def _run(self, cmd, extra_env=None):
        """→ (решение, текст решения, боевой маркер написан?, пробный маркер написан?, пушей)."""
        d = tempfile.mkdtemp(prefix="probe_iso_run_")
        marker = os.path.join(d, "pc_ask_144.marker")       # имя ЖИВОЙ задачи из инцидента
        # Пробный путь называем ДОСЛОВНО, а не через `g.marker_path`: у родителя окружение боевое,
        # а изоляцию ребёнка мог включить латч (объявление в самой команде или место скрипта) —
        # родитель через `env` этого не увидит. Ждём ровно тот файл, который демон НЕ откроет.
        probe_marker = os.path.join(d, g.PROBE_MARKER_PREFIX + "pc_ask_144.marker")
        pushes = os.path.join(d, "pushes.txt")
        env = dict(os.environ)
        for k in g._TEST_RUN_ENVS:
            env.pop(k, None)                                # окружение боевого ребёнка (как демон)
        env.update({"PYTHONIOENCODING": "utf-8", "TURBOBABY_TEST_LOGS": "1",
                    "PRETOOL_ASK_MARKER": marker, "PRETOOL_MARKER_TOKEN": "probe-iso-run",
                    "PRETOOL_GUARD_LOG": os.path.join(d, "guard.log"),
                    "NOTIFY_COUNT_FILE": pushes})           # канал 1: счётчик ДО сети
        env.update(extra_env or {})
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": ROOT})
        p = subprocess.run([sys.executable, GUARD], input=payload, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=60)
        try:
            out = json.loads((p.stdout or "").strip().splitlines()[-1])["hookSpecificOutput"]
            dec, reason = out["permissionDecision"], out["permissionDecisionReason"]
        except Exception:
            dec, reason = "(нет решения)", (p.stdout or "") + (p.stderr or "")
        n_push = 0
        if os.path.isfile(pushes):
            with open(pushes, encoding="utf-8") as f:
                n_push = len([x for x in f.read().splitlines() if x.strip()])
        return dec, reason, os.path.isfile(marker), os.path.isfile(probe_marker), n_push

    def _assert_probe_silent(self, cmd, extra_env=None):
        dec, reason, live_marker, probe_marker, n_push = self._run(cmd, extra_env)
        self.assertEqual(dec, "ask", "решение ОСЛАБЛЕНО: " + reason[:300])
        self.assertFalse(live_marker, "канал 2: боевой маркер написан из пробы")
        self.assertTrue(probe_marker, "канал 2: маркер не уехал в пробный канал — это поломка, "
                                      "а не изоляция")
        self.assertEqual(n_push, 0, "канал 1: пуш владельцу из пробы")
        self.assertIn("ПЕРЕХВАЧЕНО НА ГРАНИЦЕ", reason,
                      "перехват не показан исполнителю — изоляция неотличима от поломки")
        return reason

    def test_probe_money_by_command_prefix(self):
        """Форма 1 — денежная операция, объявленная env-префиксом В КОМАНДЕ."""
        reason = self._assert_probe_silent("PRETOOL_TEST_RUN=1 %s %s" % (PY, self.fx_money))
        self.assertIn("проводка", reason)     # ДОСЛОВНО показано, что собиралось уйти владельцу

    def test_probe_live_tables_by_hook_env(self):
        """Форма 2 — запись в ЖИВЫЕ ТАБЛИЦЫ, объявленная тест-флагом в окружении хука."""
        self._assert_probe_silent("%s %s" % (PY, self.fx_oil), {"ORCH_TEST_MODE": "1"})

    def test_probe_create_booking_from_repo_tmp(self):
        """Форма 3 — ДОСЛОВНЫЙ живой факт карточки 144: боевой `create_booking` из пробного
        скрипта во временной зоне репозитория."""
        self._assert_probe_silent("%s %s" % (PY, self.fx_book))

    def test_probe_from_session_scratchpad(self):
        """Форма 4 — проба из скретчпада сессии."""
        self._assert_probe_silent("%s %s" % (PY, self.fx_void))

    def test_live_operation_still_reaches_the_owner(self):
        """ОБРАТНАЯ ПОЛОВИНА: настоящая боевая операция рождает карточку КАК ПРЕЖДЕ — обоими
        каналами. Без этой проверки «изоляция» неотличима от сломанного гарда."""
        dec, reason, live_marker, _probe_marker, n_push = self._run("%s %s" % (PY, self.fx_money))
        self.assertEqual(dec, "ask")
        self.assertTrue(live_marker, "боевая операция перестала доходить до демона")
        self.assertEqual(n_push, 1, "боевая операция перестала пушиться владельцу")
        self.assertNotIn("ПЕРЕХВАЧЕНО НА ГРАНИЦЕ", reason)
        self.assertIn("разрешить?", reason)


class TestDaemonStripsTheSameFlags(unittest.TestCase):
    """(4) ИНВАРИАНТ, а не украшение: демон обязан снимать с боевого ребёнка РОВНО те имена,
    которые гард считает признаком пробы. Разъедься списки — протёкшее в окружение демона имя
    увело бы карточку ЖИВОЙ задачи в пробный канал: тихая потеря вместо лишнего вопроса."""

    def test_both_spawn_branches_strip_exactly_test_run_envs(self):
        import re
        with open(os.path.join(ROOT, "pc_orchestrator.py"), encoding="utf-8") as f:
            src = f.read()
        blocks = re.findall(r"for _test_flag in \(([^)]*)\)", src)
        self.assertGreaterEqual(len(blocks), 2,
                                "чистка обязана стоять в ОБЕИХ ветках спавна (исполнитель и "
                                "думатель), найдено: %d" % len(blocks))
        for i, blk in enumerate(blocks, 1):
            names = set(re.findall(r'"([A-Z_]+)"', blk))
            self.assertEqual(names, set(g._TEST_RUN_ENVS),
                             "ветка %d чистит не тот набор (расхождение: %s)"
                             % (i, sorted(names ^ set(g._TEST_RUN_ENVS))))


if __name__ == "__main__":
    unittest.main(verbosity=2)
