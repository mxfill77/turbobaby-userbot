# -*- coding: utf-8 -*-
"""
test_proc_identity.py — голдены общего правила личности процесса и ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА,
без которых заход по классу «номер процесса не является именем» не считается закрытым.

ПОЧЕМУ ЖИВЫЕ ПРОЦЕССЫ, А НЕ МОКИ. Правило-класс репозитория: мок внешнего ответа обязан
КОПИРОВАТЬ живой формат. Здесь внешний ответ — это сама операционная система, и подделать её
ответ так, чтобы тест что-то доказал, невозможно: именно расхождение «как удобно тесту» против
«как отвечает Windows» и породило класс. Поэтому отрицательные тесты работают на НАСТОЯЩИХ
живых процессах, которые сами же и заводят: чужой `cmd.exe` (образ не наш) и чужой python
(образ наш, а рождение — позже лока). Оба процесса — СВОИ ДЕТИ, снимаются по объекту запуска, а
не по номеру: тест не имеет права делать то, от чего лечит.

ЖИВЫЕ СЛУЧАИ, ПЕРЕЛОЖЕННЫЕ В ГОЛДЕНЫ ДОСЛОВНО (числа не выдуманы):
  • 26.08.2026 — `moderation_bot.lock` = 18200 от 23.08 18:29; после ребута 18200 = `PinWin.exe`,
    рождён 11:48:14. Простой 3 суток 15 часов, сторож трижды вставал в стоп.
  • 23.08.2026 — `pc_agent.lock` = 8960 после BSOD 0x7F; после загрузки 8960 = `wlanext.exe`.
    Полоса без агента 05:14 → 06:16.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import proc_identity as pi

WIN = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def probe_of(exists=None, started=None, image=None, how="фикстура"):
    return pi.Probe(exists, started, image, how)


def rec_of(pid=1234, started=None, image=None, mtime=None, script=None):
    return {"pid": pid, "started": started, "image": image, "script": script,
            "written": None, "mtime": mtime, "legacy": started is None, "raw": ""}


# ═════════════════════════ ГОЛДЕНЫ ПРИГОВОРА (чистая функция) ═════════════════════════════════

class TestJudgeGoldens(unittest.TestCase):
    """`judge` — чистая: факты на входе, вердикт и причина на выходе. Ни одной побочки."""

    def test_no_lock_is_stale(self):
        v, why = pi.judge(None, probe_of(True, 1.0, "python.exe"))
        self.assertEqual(v, pi.STALE)
        self.assertIn("нет", why)

    def test_empty_lock_is_stale(self):
        v, _ = pi.judge(rec_of(pid=0), probe_of(True, 1.0, "python.exe"))
        self.assertEqual(v, pi.STALE)

    def test_absent_process_is_stale(self):
        v, why = pi.judge(rec_of(), probe_of(False))
        self.assertEqual(v, pi.STALE)
        self.assertIn("НЕТ", why)

    def test_silent_probe_is_unknown_not_dead(self):
        """Главная асимметрия класса #171: «не смог проверить» ≠ «мёртв»."""
        v, why = pi.judge(rec_of(), probe_of(None, how="tasklist не ответил"))
        self.assertEqual(v, pi.UNKNOWN)
        self.assertIn("неизвестно", why)

    def test_live_case_2608_pinwin_taken_number(self):
        """26.08 ДОСЛОВНО: лок 18200 старого формата от 23.08 18:29, номер занял PinWin.exe,
        рождённый 26.08 11:48:14. Прежний гард отвечал «уже запущен» — новый обязан сказать
        «стухший» ДВАЖДЫ независимо: и по образу, и по рождению позже лока."""
        lock_mtime = time.mktime((2026, 8, 23, 18, 29, 2, 0, 0, -1))
        born = time.mktime((2026, 8, 26, 11, 48, 14, 0, 0, -1))
        v, why = pi.judge(rec_of(pid=18200, mtime=lock_mtime),
                          probe_of(True, born, r"C:\Program Files (x86)\Bluegrams\PinWin\PinWin.exe"))
        self.assertEqual(v, pi.STALE)
        self.assertIn("pinwin.exe", why.lower())

    def test_live_case_2308_wlanext_taken_number(self):
        """23.08 ДОСЛОВНО: лок агента 8960 после BSOD, номер занял wlanext.exe."""
        lock_mtime = time.mktime((2026, 8, 22, 20, 0, 0, 0, 0, -1))
        born = time.mktime((2026, 8, 23, 6, 4, 40, 0, 0, -1))
        v, why = pi.judge(rec_of(pid=8960, mtime=lock_mtime),
                          probe_of(True, born, r"C:\WINDOWS\System32\wlanext.exe"))
        self.assertEqual(v, pi.STALE)
        self.assertIn("wlanext.exe", why.lower())

    def test_legacy_foreign_python_born_after_lock_is_stale(self):
        """Образ наш, а рождение — позже записи лока. Автором он быть не может ни при каких
        обстоятельствах: пока лок писался, номер принадлежал другому."""
        lock_mtime = 1_700_000_000.0
        v, why = pi.judge(rec_of(pid=777, mtime=lock_mtime),
                          probe_of(True, lock_mtime + 3600, r"D:\venv\python.exe"))
        self.assertEqual(v, pi.STALE)
        self.assertIn("ПОЗЖЕ", why)

    def test_legacy_python_born_before_lock_is_ours(self):
        lock_mtime = 1_700_000_000.0
        v, _ = pi.judge(rec_of(pid=777, mtime=lock_mtime),
                        probe_of(True, lock_mtime - 0.4, r"D:\venv\python.exe"))
        self.assertEqual(v, pi.OURS_ALIVE)

    def test_new_format_started_matches_is_ours(self):
        v, _ = pi.judge(rec_of(pid=42, started=1_700_000_000.0, image=r"D:\venv\python.exe"),
                        probe_of(True, 1_700_000_000.0, r"D:\venv\python.exe"))
        self.assertEqual(v, pi.OURS_ALIVE)

    def test_new_format_started_differs_is_stale(self):
        """Номер тот же, образ тот же, а запуск ДРУГОЙ — единственный случай, который старый
        формат лока поймать не мог вовсе."""
        v, why = pi.judge(rec_of(pid=42, started=1_700_000_000.0, image=r"D:\venv\python.exe"),
                          probe_of(True, 1_700_000_500.0, r"D:\venv\python.exe"))
        self.assertEqual(v, pi.STALE)
        self.assertIn("ДРУГИМ запуском", why)

    def test_exists_without_age_is_held_not_stale(self):
        """Доступ закрыт, возраст не добыт: номер ЗАНЯТ — повод не лезть, а не повод забрать."""
        v, _ = pi.judge(rec_of(pid=4, mtime=1.0), probe_of(True, None, "System"))
        self.assertEqual(v, pi.STALE)          # чужой образ решает раньше возраста
        v2, _ = pi.judge(rec_of(pid=4, mtime=1.0), probe_of(True, None, None))
        self.assertEqual(v2, pi.HELD_UNVERIFIED)

    def test_legacy_without_mtime_is_held(self):
        v, _ = pi.judge(rec_of(pid=42, mtime=None), probe_of(True, 5.0, r"D:\venv\python.exe"))
        self.assertEqual(v, pi.HELD_UNVERIFIED)

    def test_image_compared_by_name_not_by_path(self):
        """Полный путь приходит то DOS-формой, то формой устройства; ложное «чужой» здесь стоит
        двойного запуска, поэтому сверяем ИМЯ."""
        v, _ = pi.judge(rec_of(pid=42, started=1.0, image=r"D:\turbobaby-bot\venv\Scripts\python.exe"),
                        probe_of(True, 1.0, r"\Device\HarddiskVolume3\other\python.exe"))
        self.assertEqual(v, pi.OURS_ALIVE)


# ═════════════════════════ ЧТЕНИЕ И ЗАПИСЬ ЛОКА ═══════════════════════════════════════════════

class TestLockFormat(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "x.lock")

    def test_legacy_bare_pid_still_readable(self):
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write("18200")
        rec = pi.read_lock(self.lock)
        self.assertEqual(rec["pid"], 18200)
        self.assertTrue(rec["legacy"])
        self.assertIsNone(rec["started"])

    def test_new_format_first_line_is_bare_number(self):
        """Совместимость наружу: наблюдатель ожиданий читает ПЕРВУЮ строку и обязан её понять."""
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write(pi.lock_text(script="x.py"))
        first = open(self.lock, encoding="utf-8").read().splitlines()[0].strip()
        self.assertEqual(int(first), os.getpid())
        rec = pi.read_lock(self.lock)
        self.assertFalse(rec["legacy"])
        self.assertEqual(rec["script"], "x.py")
        self.assertIsNotNone(rec["started"])

    def test_body_about_other_pid_is_ignored(self):
        """Хвост про ЧУЖОЙ номер верить опаснее, чем не иметь его вовсе: ложное «личность
        известна» ведёт к ложному приговору."""
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write('42\n{"v":1,"pid":777,"started":1.0,"image":"a.exe"}\n')
        rec = pi.read_lock(self.lock)
        self.assertEqual(rec["pid"], 42)
        self.assertTrue(rec["legacy"])
        self.assertIsNone(rec["started"])

    def test_missing_file_is_none(self):
        self.assertIsNone(pi.read_lock(os.path.join(self.tmp, "нет.lock")))

    def test_nonpositive_number_asks_nobody(self):
        """Пустой лок не должен стоить подпроцесса: спрашивать систему о номере 0 бессмысленно,
        а запасная проба — это запуск `tasklist`, то есть сотни лишних процессов на прогоне."""
        called = []
        p = pi.process_probe(0, tasklist=lambda pid: called.append(pid) or pi.Probe())
        self.assertIsNone(p.exists)
        self.assertEqual(called, [], "на непозитивный номер запасную пробу звать незачем")
        v, _ = pi.judge(rec_of(pid=0), p)
        self.assertEqual(v, pi.STALE)


# ═════════ ОТРИЦАТЕЛЬНЫЙ ТЕСТ №1: ЛОК С НОМЕРОМ ЧУЖОГО ЖИВОГО ПРОЦЕССА ════════════════════════

@unittest.skipUnless(WIN, "живые пробы процессов — только Windows")
class TestNegativeOneForeignLiveNumber(unittest.TestCase):
    """ПОДДЕЛЫВАЕМ ЛОК ТАК, ЧТОБЫ НОМЕР ПРИНАДЛЕЖАЛ ЧУЖОМУ ЖИВОМУ ПРОЦЕССУ.
    Программа обязана счесть лок стухшим и СТАРТОВАТЬ. Без моков: чужие процессы настоящие."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "victim.lock")
        self.kids = []

    def tearDown(self):
        for p in self.kids:
            try:
                p.terminate()                  # свой ребёнок — снимаем ПО ОБЪЕКТУ ЗАПУСКА, не по номеру
                p.wait(timeout=10)
            except Exception:
                pass

    def _spawn_foreign_cmd(self):
        """Настоящий чужой процесс НЕ-python: живёт минуту, снимается своим же объектом."""
        p = subprocess.Popen(["cmd", "/c", "ping -n 60 127.0.0.1 > NUL"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
        self.kids.append(p)
        for _ in range(50):                    # ждём, пока ОС действительно заведёт процесс
            if pi.process_probe(p.pid).exists is True:
                return p
            time.sleep(0.1)
        return p

    def _spawn_foreign_python(self):
        """Настоящий чужой процесс python: образ наш, а рождение будет позже лока."""
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
        self.kids.append(p)
        for _ in range(50):
            if pi.process_probe(p.pid).exists is True:
                return p
            time.sleep(0.1)
        return p

    def test_1a_foreign_image_lock_is_stale_and_we_start(self):
        """ФОРМА 26.08: номер из лока достался процессу С ДРУГИМ ОБРАЗОМ (тогда — PinWin.exe)."""
        foreign = self._spawn_foreign_cmd()
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write(str(foreign.pid))          # лок СТАРОГО формата, как боевые до этой правки
        verdict, why = pi.judge(pi.read_lock(self.lock), pi.process_probe(foreign.pid))
        self.assertEqual(verdict, pi.STALE, why)
        ok, v, why2 = pi.acquire(self.lock, script="victim.py")
        self.assertTrue(ok, "программа обязана СТАРТОВАТЬ на стухшем локе: %s" % why2)
        # и номер в локе теперь НАШ, а не чужого живого процесса
        self.assertEqual(pi.read_lock(self.lock)["pid"], os.getpid())

    def test_1a_stale_lock_is_retired_by_the_program_itself(self):
        """Пункт «стухший лок снимает САМА программа»: руками ничего не делаем, а файл-улика
        уезжает в резерв рядом с локом — не удаляется."""
        foreign = self._spawn_foreign_cmd()
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write(str(foreign.pid))
        ok, _, _ = pi.acquire(self.lock, script="victim.py")
        self.assertTrue(ok)
        reserve = pi.reserve_dir(self.lock)
        saved = os.listdir(reserve) if os.path.isdir(reserve) else []
        self.assertTrue(saved, "улика стухшего лока обязана остаться файлом в %s" % reserve)
        body = open(os.path.join(reserve, saved[0]), encoding="utf-8").read().strip()
        self.assertEqual(body.splitlines()[0], str(foreign.pid))

    def test_1b_foreign_python_born_after_lock_is_stale(self):
        """ФОРМА ХУЖЕ: номер достался ЧУЖОМУ PYTHON — имя образа совпадает, и одного имени мало.
        Решает вторая примета: процесс рождён ПОЗЖЕ, чем написан лок."""
        foreign = self._spawn_foreign_python()
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write(str(foreign.pid))
        old = time.time() - 3600
        os.utime(self.lock, (old, old))        # лок «написан» час назад — как боевой после ребута
        verdict, why = pi.judge(pi.read_lock(self.lock), pi.process_probe(foreign.pid))
        self.assertEqual(verdict, pi.STALE, why)
        self.assertIn("ПОЗЖЕ", why)
        ok, _, why2 = pi.acquire(self.lock, script="victim.py")
        self.assertTrue(ok, why2)

    def test_1c_new_format_wrong_start_is_stale(self):
        """Лок нового формата, номер ЖИВОЙ и наш собственный, а записанный момент старта — чужой.
        Сверка точная, поэтому вердикт «стухший» без всяких запасов."""
        me = pi.whoami()
        blob = {"v": 1, "pid": me["pid"], "started": (me["started"] or 0.0) - 99999.0,
                "image": me["image"], "script": "victim.py"}
        with open(self.lock, "w", encoding="utf-8") as f:
            f.write("%d\n%s\n" % (me["pid"], json.dumps(blob, ensure_ascii=False)))
        verdict, why = pi.judge(pi.read_lock(self.lock), pi.process_probe(me["pid"]))
        self.assertEqual(verdict, pi.STALE, why)
        ok, _, why2 = pi.acquire(self.lock, script="victim.py")
        self.assertTrue(ok, why2)


# ═════════ ОТРИЦАТЕЛЬНЫЙ ТЕСТ №2: ПРИ ЖИВОЙ НАСТОЯЩЕЙ КОПИИ ВТОРАЯ НЕ СТАРТУЕТ ════════════════

@unittest.skipUnless(WIN, "живые пробы процессов — только Windows")
class TestNegativeTwoSecondCopyRefused(unittest.TestCase):
    """УЖЕСТОЧЕНИЕ НЕ СМЕЕТ СНЯТЬ ЗАЩИТУ. Держатель — НАСТОЯЩИЙ второй процесс python, взявший
    лок своими руками тем же общим правилом; мы пробуем взять его же лок и обязаны получить
    отказ. Затем держатель умирает — и лок обязан стать забираемым, иначе «усиление» превратится
    в вечный запрет старта (ровно та беда, от которой заход и лечит)."""

    HOLDER = (
        "import os, sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import proc_identity as pi\n"
        "ok, v, why = pi.acquire(sys.argv[2], script='holder.py')\n"
        "open(sys.argv[3], 'w', encoding='utf-8').write('%s|%s' % (ok, v))\n"
        "time.sleep(120)\n"
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "holder.lock")
        self.flag = os.path.join(self.tmp, "got.txt")
        self.script = os.path.join(self.tmp, "holder_main.py")
        with open(self.script, "w", encoding="utf-8") as f:
            f.write(self.HOLDER)
        self.holder = None

    def tearDown(self):
        if self.holder is not None:
            try:
                self.holder.terminate()        # свой ребёнок, снимаем по объекту запуска
                self.holder.wait(timeout=10)
            except Exception:
                pass

    def test_second_copy_refused_then_allowed_after_holder_dies(self):
        repo = os.path.dirname(os.path.abspath(__file__))
        self.holder = subprocess.Popen(
            [sys.executable, self.script, repo, self.lock, self.flag],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        for _ in range(100):                   # ждём, пока держатель ДЕЙСТВИТЕЛЬНО возьмёт лок
            if os.path.exists(self.flag):
                break
            time.sleep(0.1)
        self.assertTrue(os.path.exists(self.flag), "держатель не успел взять лок")
        self.assertTrue(open(self.flag, encoding="utf-8").read().startswith("True"))
        self.assertEqual(pi.read_lock(self.lock)["pid"], self.holder.pid)

        # ── СОБСТВЕННО ОТРИЦАТЕЛЬНЫЙ ТЕСТ: второй экземпляр НЕ стартует ──────────────────────
        ok, verdict, why = pi.acquire(self.lock, script="holder.py")
        self.assertFalse(ok, "при ЖИВОЙ настоящей копии второй обязан получить отказ")
        self.assertEqual(verdict, pi.OURS_ALIVE, why)
        self.assertEqual(pi.read_lock(self.lock)["pid"], self.holder.pid,
                         "отказавший экземпляр не смеет трогать чужой живой лок")

        # ── и обратная половина: смерть держателя обязана освобождать лок ────────────────────
        self.holder.terminate()
        self.holder.wait(timeout=15)
        for _ in range(50):
            if pi.process_probe(self.holder.pid).exists is not True:
                break
            time.sleep(0.1)
        ok2, _, why2 = pi.acquire(self.lock, script="holder.py")
        self.assertTrue(ok2, "после смерти держателя лок обязан забираться, иначе это не "
                             "усиление, а вечный запрет старта: %s" % why2)


# ═════════ ЗАМОК ПЕРЕД СНЯТИЕМ ПРОЦЕССА: НЕ ОПОЗНАЛИ — НЕ БЬЁМ ════════════════════════════════

class TestKillGuard(unittest.TestCase):

    def test_born_after_sighting_is_refused(self):
        seen = 1_700_000_000.0
        ok, why = pi.kill_ok(555, seen, probe=lambda p: probe_of(True, seen + 30, "python.exe"))
        self.assertFalse(ok)
        self.assertIn("ПОЗЖЕ", why)

    def test_foreign_image_is_refused(self):
        seen = 1_700_000_000.0
        ok, why = pi.kill_ok(555, seen, probe=lambda p: probe_of(True, seen - 5, "PinWin.exe"))
        self.assertFalse(ok)

    def test_unknown_is_refused(self):
        ok, _ = pi.kill_ok(555, 1.0, probe=lambda p: probe_of(None))
        self.assertFalse(ok)

    def test_same_process_is_allowed(self):
        seen = 1_700_000_000.0
        ok, _ = pi.kill_ok(555, seen, probe=lambda p: probe_of(True, seen - 1, "python.exe"))
        self.assertTrue(ok)

    @unittest.skipUnless(WIN, "живая проба — только Windows")
    def test_exited_child_with_held_handle_is_not_alive(self):
        """ЛОВУШКА WINDOWS, пойманная отрицательным тестом №2 и оттого попавшая в голдены: пока
        `Popen` держит дескриптор, `OpenProcess` на ЗАВЕРШИВШИЙСЯ процесс успешно открывается и
        отдаёт его прежний момент старта. Прибор обязан назвать такой процесс мёртвым — иначе
        гард не даст поднять ребёнка заново, то есть воспроизведёт саму беду захода."""
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
        for _ in range(50):
            if pi.process_probe(p.pid).exists is True:
                break
            time.sleep(0.1)
        self.assertIs(pi.process_probe(p.pid).exists, True, "ребёнок не успел завестись")
        p.terminate()
        p.wait(timeout=15)                     # объект Popen ЖИВ и дескриптор удерживает
        probe = pi.process_probe(p.pid)
        self.assertIs(probe.exists, False, "завершённый процесс назван живым: %s" % probe.how)
        self.assertFalse(pi.kill_ok(p.pid, time.time())[0])

    @unittest.skipUnless(WIN, "живая проба — только Windows")
    def test_live_self_is_allowed_and_foreign_is_not(self):
        self.assertTrue(pi.kill_ok(os.getpid(), time.time())[0])
        p = subprocess.Popen(["cmd", "/c", "ping -n 30 127.0.0.1 > NUL"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
        try:
            for _ in range(50):
                if pi.process_probe(p.pid).exists is True:
                    break
                time.sleep(0.1)
            ok, why = pi.kill_ok(p.pid, time.time())
            self.assertFalse(ok, "чужой образ бить нельзя: %s" % why)
        finally:
            p.terminate()
            p.wait(timeout=10)


# ═════════ ХРАПОВИК: НИ ОДИН ГАРД ПОЛОСЫ БОЛЬШЕ НЕ СУДИТ ПО ГОЛОМУ НОМЕРУ ═════════════════════

class TestNoBareNumberLeft(unittest.TestCase):
    """Замок против отката руками: правило одно, и оно обязано стоять во ВСЕХ гардах сразу.

    СУДИМ КОД, А НЕ ТЕКСТ ФАЙЛА. Первая версия этого замка искала подстроку и упала на
    СОБСТВЕННОМ комментарии, который цитирует снятую пробу, — то есть ровно на том, что репозиторий
    зовёт «судить по подстроке». Разбираем дерево: комментариев в нём нет вовсе, докстринги
    отбрасываем явно, и остаются только настоящие строки-литералы кода."""

    GUARDS = ("moderation_bot.py", "userbot_listen.py", "pc_agent.py")

    def _tree(self, name):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        with open(p, encoding="utf-8") as f:
            return ast.parse(f.read(), filename=name)

    @staticmethod
    def _code_strings(tree):
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.body and isinstance(node.body[0], ast.Expr) \
                        and isinstance(node.body[0].value, ast.Constant) \
                        and isinstance(node.body[0].value.value, str):
                    docs.add(id(node.body[0].value))
        return [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]

    def test_bots_have_no_local_bare_pid_probe(self):
        for name in self.GUARDS:
            tree = self._tree(name)
            lits = " | ".join(self._code_strings(tree))
            self.assertNotIn("PID eq", lits,
                             "%s снова спрашивает живость ОДНИМ номером (tasklist /FI \"PID eq …\")" % name)
            self.assertNotIn("tasklist", lits.lower(),
                             "%s снова завёл собственную пробу процессов" % name)
            names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
            self.assertNotIn("_pid_alive", names, "%s вернул локальную пробу живости" % name)
            self.assertNotIn("_agent_pid_alive", names, "%s вернул локальную пробу живости" % name)

    def test_all_guards_use_the_common_rule(self):
        for name in self.GUARDS + ("pc_orchestrator.py",):
            imported = set()
            for n in ast.walk(self._tree(name)):
                if isinstance(n, ast.Import):
                    imported.update(a.name for a in n.names)
                elif isinstance(n, ast.ImportFrom) and n.module:
                    imported.add(n.module)
            self.assertIn("proc_identity", imported,
                          "%s не подключает общее правило личности" % name)

    def test_taskkill_never_called_without_sighting_moment(self):
        """У снятия процесса нет значения по умолчанию для момента наблюдения, и ни один вызов не
        смеет его забыть: забыть — значит снова бить по голому номеру."""
        tree = self._tree("pc_agent.py")
        defs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_taskkill"]
        self.assertEqual(len(defs), 1)
        self.assertEqual([a.arg for a in defs[0].args.args], ["pid", "seen_at"])
        self.assertEqual(defs[0].args.defaults, [], "у момента наблюдения не должно быть умолчания")
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_taskkill"]
        self.assertTrue(calls, "вызовов снятия не нашлось — замок сторожит пустоту")
        for c in calls:
            self.assertEqual(len(c.args), 2, "вызов снятия без момента наблюдения (строка %d)" % c.lineno)


if __name__ == "__main__":
    unittest.main()
