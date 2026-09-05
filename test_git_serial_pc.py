# -*- coding: utf-8 -*-
"""
test_git_serial_pc.py — замки ТОЧКИ СЕРИАЛИЗАЦИИ GIT полосы ПК (git_serial_pc.py).

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_git_serial_pc -v

БОЕВОГО РЕПОЗИТОРИЯ ЗДЕСЬ НЕТ НИ ОДНОЙ ВЕТКОЙ. Все git-пробы идут в ОДНОРАЗОВЫЙ репозиторий во
временном каталоге (`tempfile.mkdtemp`), созданный `git init` в этом же тесте; замок тоже уводится
туда. Это не осторожность ради осторожности: предмет теста — порча индекса, и ставить такой опыт
на живом дереве значило бы проверять фикс ценой того, что он защищает.

ОТРИЦАТЕЛЬНЫЙ ТЕСТ (`TestTwoLanesCommitAtOnce`) — главный здесь. Он поднимает ДВА НАСТОЯЩИХ
процесса python, которые в одну и ту же миллисекунду коммитят в один репозиторий через обёртку, и
спрашивает три вещи: оба ли коммита на месте, цел ли индекс, не остался ли `.git/index.lock`.
Контрольный опыт рядом (`test_bez_zamka_klass_zhivoy`) гоняет ТО ЖЕ САМОЕ мимо обёртки — он
доказывает, что класс не выдуман: без замка git проигравшего падает на `index.lock`.
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import git_serial_pc as gs

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
GIT_OK = shutil.which("git") is not None

# Скрипт-заход: ждёт общего старта (файл-семафор), затем коммитит СВОЙ файл. Ровно то, что делает
# живой ребёнок: `git add -- <файл>` и `git commit -m …`. Через обёртку или мимо — решает argv.
_LANE_SRC = r'''
import os, subprocess, sys, time
sys.path.insert(0, %(here)r)
repo, name, gate, mode, lock = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
open(os.path.join(repo, name + ".txt"), "w", encoding="utf-8").write(name)
while not os.path.exists(gate):          # общий старт: оба захода срываются с места ВМЕСТЕ
    time.sleep(0.01)
def call(args):
    if mode == "serial":
        import git_serial_pc as gs
        gs.DEFAULT_TIMEOUT = 120.0
        p = gs.run(args, cwd=repo, owner=name, lock_timeout=120.0)
    else:
        p = subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    return p.returncode, (p.stderr or "")
rc1, e1 = call(["add", "--", name + ".txt"])
rc2, e2 = call(["commit", "-m", "commit " + name])
print("%%s|%%s|%%s|%%s" %% (rc1, rc2, e1.replace("\n", " ")[-300:], e2.replace("\n", " ")[-300:]))
'''


def _git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _make_repo():
    """Одноразовый git-репозиторий с одним коммитом. Возвращает путь; уборку делает вызывающий."""
    d = tempfile.mkdtemp(prefix="gs_pc_")
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    _git(d, "config", "commit.gpgsign", "false")
    _git(d, "config", "core.hooksPath", os.path.join(d, ".no-hooks"))
    with open(os.path.join(d, "seed.txt"), "w", encoding="utf-8") as f:
        f.write("seed")
    _git(d, "add", "--", "seed.txt")
    _git(d, "commit", "-q", "-m", "seed")
    return d


class TestNeedsLock(unittest.TestCase):
    """Кого пускать мимо замка — решает ЧИСТАЯ функция, и она обязана быть скучно точной."""

    def test_pishushchie_berut_zamok(self):
        for sub in ("add", "commit", "reset", "checkout", "merge", "stash", "pull", "push", "rm"):
            self.assertTrue(gs.needs_lock(["git", sub]), sub)
            self.assertTrue(gs.needs_lock([sub, "--anything"]), sub)   # argv демона — без "git"

    def test_chitayushchie_zamok_ne_berut(self):
        for sub in ("log", "status", "diff", "rev-parse", "show", "merge-base", "cat-file",
                    "ls-files", "describe", "blame"):
            self.assertFalse(gs.needs_lock(["git", sub]), sub)

    def test_globalnye_klyuchi_ne_sbivayut_razbor(self):
        # `-C <путь>` и `-c k=v` несут значение ОТДЕЛЬНЫМ словом: наивный argv[1] увидел бы путь.
        self.assertTrue(gs.needs_lock(["git", "-C", "D:/x", "commit", "-m", "y"]))
        self.assertTrue(gs.needs_lock(["git", "-c", "user.name=x", "add", "--", "a"]))
        self.assertFalse(gs.needs_lock(["git", "-C", "D:/commit", "log"]))   # «commit» в ПУТИ — не команда
        self.assertFalse(gs.needs_lock([]))
        self.assertFalse(gs.needs_lock(["git"]))

    def test_polnyy_put_k_git_tozhe_uznayotsya(self):
        self.assertTrue(gs.needs_lock([r"C:\Program Files\Git\cmd\git.exe", "commit", "-m", "x"]))


class TestHold(unittest.TestCase):
    """Замок: второй ЖДЁТ, реентранс не вешает сам себя, отказ называет себя своим типом."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gs_lock_")
        self.lock = os.path.join(self.dir, "git.lock")
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))

    def test_reentrans_ne_veshaet_potok(self):
        with gs.hold(owner="a", path=self.lock, timeout=5):
            with gs.hold(owner="a-vlozhenny", path=self.lock, timeout=5) as how:
                self.assertIn("реентранс", how)

    def test_vtoroy_potok_zhdyot_a_ne_padaet(self):
        order, started = [], threading.Event()

        def first():
            with gs.hold(owner="first", path=self.lock, timeout=10):
                order.append("first-in")
                started.set()
                time.sleep(0.4)
                order.append("first-out")

        def second():
            started.wait(5)
            with gs.hold(owner="second", path=self.lock, timeout=10):
                order.append("second-in")

        t1, t2 = threading.Thread(target=first), threading.Thread(target=second)
        t1.start(); t2.start(); t1.join(10); t2.join(10)
        # Порядок — вот всё доказательство: второй вошёл ПОСЛЕ выхода первого, а не рядом с ним.
        self.assertEqual(order, ["first-in", "first-out", "second-in"])

    def test_ne_dozhdalsya_eto_svoy_tip_oshibki(self):
        # «Не дождался очереди» и «git отказал» — разные новости; тип ошибки их и разводит.
        done = threading.Event()

        def holder():
            with gs.hold(owner="holder", path=self.lock, timeout=10):
                done.wait(3)

        t = threading.Thread(target=holder)
        t.start()
        time.sleep(0.2)
        try:
            with self.assertRaises(gs.GitSerialTimeout):
                with gs.hold(owner="loser", path=self.lock, timeout=0.3):
                    pass                                     # pragma: no cover — сюда не доходим
        finally:
            done.set()
            t.join(10)

    def test_smert_derzhatelya_osvobozhdaet_zamok(self):
        """КЛАСС НОЧИ 03.09: держатель умер, а замок остался. Здесь он умереть НЕ МОЖЕТ так.

        Убиваем процесс-держатель НЕ дав ему выйти по-хорошему (никакого finally он не исполнит) и
        показываем, что замок свободен сразу: его снимает ядро при закрытии дескриптора, а не
        уборка в коде. Именно поэтому файл `git.lock` никогда не удаляется — он и не решает."""
        src = ("import sys, time\n"
               "sys.path.insert(0, %r)\n" % HERE +
               "import git_serial_pc as gs\n"
               "with gs.hold(owner='doomed', path=sys.argv[1], timeout=10):\n"
               "    print('held', flush=True)\n"
               "    time.sleep(60)\n")
        f = os.path.join(self.dir, "doomed.py")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(src)
        p = subprocess.Popen([PY, f, self.lock], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual((p.stdout.readline() or "").strip(), "held")
            p.kill()
            p.wait(10)
            # Файл замка на месте (мы его не удаляли ни одной веткой) — а замок свободен.
            self.assertTrue(os.path.exists(self.lock))
            with gs.hold(owner="next", path=self.lock, timeout=5) as how:
                self.assertNotIn("реентранс", how)
        finally:
            if p.poll() is None:                             # pragma: no cover
                p.kill()


class TestIndexLockReport(unittest.TestCase):
    """Про чужой замок индекса модуль умеет ГОВОРИТЬ и не умеет резать. Это осознанный предел."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gs_rep_")
        os.makedirs(os.path.join(self.dir, ".git"))
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))

    def test_zamka_net(self):
        r = gs.index_lock_report(self.dir)
        self.assertFalse(r["exists"])
        self.assertIsNone(r["age_sec"])

    def test_svezhiy_zamok_eto_zhivoy_git(self):
        p = os.path.join(self.dir, ".git", "index.lock")
        open(p, "w").close()
        r = gs.index_lock_report(self.dir)
        self.assertTrue(r["exists"])
        self.assertIn("живой git", r["note"])

    def test_staryy_zamok_nazyvaetsya_stukhshim_no_ne_snosistya(self):
        p = os.path.join(self.dir, ".git", "index.lock")
        open(p, "w").close()
        r = gs.index_lock_report(self.dir, now=time.time() + 3600)
        self.assertIn("СТУХШИЙ", r["note"])
        self.assertIn("вручную", r["note"])          # решение о ноже — человеку, а не модулю
        self.assertTrue(os.path.exists(p))           # и файл на месте: отчёт ничего не удалил


@unittest.skipUnless(GIT_OK, "git в PATH не найден")
class TestTwoLanesCommitAtOnce(unittest.TestCase):
    """ОТРИЦАТЕЛЬНЫЙ ТЕСТ задания: два захода коммитят ОДНОВРЕМЕННО в одно дерево.

    Заходы — НАСТОЯЩИЕ процессы (не потоки и не моки): внутрипроцессный замок между процессами не
    виден вовсе, и опыт на потоках доказал бы не то. Общий старт — файл-семафор, чтобы оба
    сорвались с места вместе, а не по очереди."""

    def setUp(self):
        self.repo = _make_repo()
        self.lock = os.path.join(self.repo, "lane.lock")
        self.addCleanup(lambda: shutil.rmtree(self.repo, ignore_errors=True))

    def _run_two(self, mode):
        src = os.path.join(self.repo, "_lane.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write(_LANE_SRC % {"here": HERE})
        gate = os.path.join(self.repo, "_gate")
        env = dict(os.environ)
        env["GIT_SERIAL_PC_TIMEOUT"] = "120"
        procs = [subprocess.Popen([PY, src, self.repo, name, gate, mode, self.lock],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  encoding="utf-8", errors="replace", env=env)
                 for name in ("laneA", "laneB")]
        time.sleep(0.6)                       # дать обоим дойти до семафора
        open(gate, "w").close()
        outs = [p.communicate(timeout=180)[0] for p in procs]
        return [(o or "").strip().splitlines()[-1] if (o or "").strip() else "" for o in outs]

    def test_oba_kommita_na_meste_i_indeks_tsel(self):
        lines = self._run_two("serial")
        for ln in lines:
            rc_add, rc_commit = ln.split("|")[0], ln.split("|")[1]
            self.assertEqual(rc_add, "0", f"git add провалился: {ln}")
            self.assertEqual(rc_commit, "0", f"git commit провалился: {ln}")
        # 1) ОБА коммита на месте
        log = _git(self.repo, "log", "--pretty=%s").stdout
        self.assertIn("commit laneA", log)
        self.assertIn("commit laneB", log)
        # 2) ИНДЕКС ЦЕЛ: git его читает, дерево чистое, оба файла отслеживаются
        self.assertEqual(_git(self.repo, "status", "--porcelain", "--", "laneA.txt",
                              "laneB.txt").stdout.strip(), "")
        self.assertEqual(_git(self.repo, "fsck", "--no-progress").returncode, 0)
        tracked = _git(self.repo, "ls-files").stdout.split()
        self.assertIn("laneA.txt", tracked)
        self.assertIn("laneB.txt", tracked)
        # 3) ЗАМКА ИНДЕКСА НЕ ОСТАЛОСЬ — ни одного стухшего файла после двух рук
        self.assertFalse(gs.index_lock_report(self.repo)["exists"])

    def test_bez_zamka_klass_zhivoy(self):
        """КОНТРОЛЬНЫЙ ОПЫТ. Без обёртки тот же сценарий обязан ЛОМАТЬСЯ — иначе зелёный тест выше
        не значит ничего (он мог бы зеленеть на том, что гонки просто не случилось).

        Мягкость утверждения намеренна: гонка — явление вероятностное, и требовать «падает ВСЕГДА»
        значило бы завести мигающий тест. Мы требуем меньшего и достаточного: хотя бы один из двух
        заходов мимо замка либо падает, либо жалуется на `index.lock`; если же оба прошли — тест
        честно ГОВОРИТ, что окно на этой машине не поймалось, и не притворяется доказательством."""
        lines = self._run_two("plain")
        bad = [ln for ln in lines
               if ln.split("|")[0] != "0" or ln.split("|")[1] != "0" or "index.lock" in ln]
        if not bad:
            self.skipTest("окно гонки не поймано на этой машине — класс не воспроизведён, "
                          "и это НЕ доказательство его отсутствия (см. докстринг)")
        self.assertTrue(bad)


class TestPurity(unittest.TestCase):
    """GIT_SERIAL_PURE: решающая функция обязана оставаться ЧИСТОЙ — иначе её нечем проверять."""

    def test_needs_lock_ne_trogaet_ni_disk_ni_protsessy(self):
        src = open(os.path.join(HERE, "git_serial_pc.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name in ("needs_lock", "_git_subcommand"))
        forbidden = {"open", "run", "Popen", "makedirs", "remove", "unlink", "sleep", "getmtime"}
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                self.assertNotIn(name, forbidden, f"{name} внутри чистой функции")

    def test_modul_nikogda_ne_udalyaet_zamok_indeksa(self):
        """Запрет «ничего не удалять» здесь ещё и техника: снос ЖИВОГО чужого замка портит индекс
        ровно тем способом, от которого мы защищаемся. Ищем сам факт удаления в коде модуля."""
        src = open(os.path.join(HERE, "git_serial_pc.py"), encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                self.assertNotIn(name, {"remove", "unlink", "rmtree"},
                                 "модуль не смеет удалять файлы — ни свои, ни чужие")


if __name__ == "__main__":
    unittest.main(verbosity=2)
