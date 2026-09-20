# -*- coding: utf-8 -*-
"""
test_exam_own_stdin_utf8.py — СТРАЖ ВХОДНОЙ полосы кодировки у двери экзамена (20.09.2026, 68v).

ЧТО СЛУЧИЛОСЬ. Три урока, написанные владельцем СВОИМИ СЛОВАМИ в ночь 18→19.09.2026, легли в
базу живого набора мохибейком: «РљР°СЃР°РµРјРѕ …» вместо «Касаемо …». Ловец получил текст
ЦЕЛЫМ — `pc_agent.log` насчитал 175 символов, — а на диске оказалось 305. Порча между ловцом и
строкой файла ровно одна: родитель (`pc_agent._exam_cli`) пишет в трубу UTF-8
(`subprocess.run(input=…, encoding="utf-8")`), а ребёнок (`exam_show.py --own-text`) читал её
ГОЛЫМ `sys.stdin.read()` — то есть кодировкой ЛОКАЛИ, на этой машине cp1251.

ПОЧЕМУ БЕЗ СТРАЖА КЛАСС ВЕРНЁТСЯ. Мохибейк не роняет НИЧЕГО: длина растёт, текст непустой,
обратное чтение сходится с записанным, все гарды зелёные. Ловится он только глазами владельца —
и то через сутки. На полосе это ЧЕТВЁРТЫЙ укус того же класса (29.07 журнал, `brain_writer`,
`dispatch_notify`/`pretool_guard`), и первые три чинили ПО ОДНОМУ месту.

ТРИ ПРОВЕРКИ, и все три с числом:
  A) ЖИВОЙ ПУТЬ ЗАПИСИ НА КОПИИ — настоящий родитель `pc_agent._exam_cli`, настоящий
     `exam_show.py --live --own-text` отдельным процессом, настоящий `lesson_store` и настоящая
     TSV-строка. Боевая база не открывается ни на чтение, ни на запись: дерево собирается из
     ПОБАЙТНЫХ копий модулей во временном каталоге.
  B) МУТАНТ — в копии дерева правка снимается (строка возвращается к `sys.stdin.read()`), вход
     тот же. Ответ обязан стать ДРУГИМ, и он другой числом.
  C) СТАТИКА — голого `sys.stdin.read()` в двери нет ни одной строкой, и читает она ЛАНЕВОЕ
     устройство `io_utf8`, а не свой переключатель (класс 539: второй переключатель в модуле
     расходится с полосой молча).

ТРЕТИЙ ИСХОД ОБЪЯВЛЕН. Мутант воспроизводим только там, где локаль процесса НЕ utf-8: на машине
с `PYTHONUTF8=1`/utf-8-локалью голый `sys.stdin.read()` прочитает трубу верно, и «мутант не
покраснел» будет значить «здесь этого класса нет», а не «правка не нужна». Такой прогон
ЧЕСТНО пропускается с названной причиной, а не зеленеет молча.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_exam_own_stdin_utf8 -v
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
if not os.path.isfile(PY):
    PY = sys.executable

import io_utf8
import pc_agent

# Модули, которые дверь тянет по дороге к строке файла. Копируются ПОБАЙТНО: подменять их
# значило бы мерить не тот путь, ради которого заведён этот файл.
TREE_MODULES = ("exam_show.py", "io_utf8.py", "lesson_store.py", "anonymize_corpus.py",
                "parse_outcome.py")

# ЕДИНСТВЕННАЯ подмена в дереве — ПРАВО. Оно лежит не на пути текста (`_may` спрашивается ДО
# первого касания к нему), а настоящий `moderation_core` тянет `suggest`, а тот конфиг с
# секретами. Подмена названа вслух здесь, а не спрятана в фикстуре.
RIGHT_STUB = ("# -*- coding: utf-8 -*-\n"
              "def may_write_rule(username):\n"
              "    return True\n")

# ВХОД. Кириллица И эмодзи, как велит задание. Двойных пробелов нет намеренно: `own_take`
# нормализует текст (`\" \".join(text.split())`), и лишний пробел смазал бы сверку длин.
OWN_TEXT = ("Касаемо вопросов по обмену отвечай, что уточню у менеджера 🎓 "
            "и не называй цены, пока не ясны даты.")

CASE_ID = "1"
OLD_LINE = "ok, msg = own_take(io_utf8.read_stdin_utf8(), a.who)"
MUT_LINE = "ok, msg = own_take(sys.stdin.read(), a.who)"


def _src(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


def child_stdin_encoding():
    """Кодировка `sys.stdin` у РЕБЁНКА, поднятого ровно так, как его поднимает дверь.

    Спрашивается у настоящего процесса, а не у `locale` родителя: предмет замера — тот декодер,
    который и портил текст владельца."""
    r = subprocess.run([PY, "-c", "import sys; print(sys.stdin.encoding)"],
                       input="", capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    return (r.stdout or "").strip().lower()


class LiveDoorOnACopy(unittest.TestCase):
    """Дерево-копия: побайтные модули двери + фикстуры живого набора во временном каталоге."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="exam_own_utf8_")
        self.tree = os.path.join(self.tmp, "tree")
        self.build(self.tree)
        self.saved_repo = pc_agent.REPO_DIR
        self.saved_env = {k: os.environ.get(k) for k in
                          ("LESSON_REGRESS_OFF", "PYTHONUTF8", "PYTHONIOENCODING")}
        # Рубильник прибора регрессии — БОЕВОЙ (`lesson_regress.OFF_ENV`), а не выдуманный:
        # отсоединённый замер на копии поднимал бы чужой процесс мимо предмета этого теста.
        os.environ["LESSON_REGRESS_OFF"] = "1"
        # Локаль ребёнка не переопределяем: предмет теста — та кодировка, с которой он живёт.
        for k in ("PYTHONUTF8", "PYTHONIOENCODING"):
            os.environ.pop(k, None)

    def tearDown(self):
        pc_agent.REPO_DIR = self.saved_repo
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- дерево-копия
    def build(self, root):
        os.makedirs(root)
        for name in TREE_MODULES:
            shutil.copy2(os.path.join(HERE, name), os.path.join(root, name))
        with open(os.path.join(root, "moderation_core.py"), "w", encoding="utf-8") as f:
            f.write(RIGHT_STUB)
        live = os.path.join(root, "exam_live")
        shots = os.path.join(live, "shots")
        os.makedirs(shots)
        cases = os.path.join(live, "cases.json")
        with open(cases, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"cases": [{"id": int(CASE_ID), "lines": ["сколько стоит аренда"]}]},
                      f, ensure_ascii=False)
        import hashlib
        with open(cases, "rb") as f:
            fp = hashlib.sha256(f.read()).hexdigest()[:16]
        shot = {"case": CASE_ID, "total": 1, "corpus": fp, "commit": "0000000",
                "rules": "0" * 16, "built_at": "2026-09-20T00:00:00Z",
                "question": "сколько стоит аренда", "draft": "ответ бота под судом",
                "question_raw": "сколько стоит аренда", "hints": []}
        with open(os.path.join(shots, "case-%s@0000000.json" % CASE_ID), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(shot, f, ensure_ascii=False)
        with open(os.path.join(live, "desk.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"case": CASE_ID, "own_until": int(time.time()) + 600,
                       "own_who": "filipp"}, f, ensure_ascii=False)

    def mutate(self, root):
        """Снять правку в КОПИИ дерева: дверь снова читает stdin голым. → путь дерева."""
        src = _src("exam_show.py")
        self.assertEqual(src.count(OLD_LINE), 1,
                         "в двери нет строки с ланевым чтением stdin — контрфакт был бы пустым")
        with open(os.path.join(root, "exam_show.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(src.replace(OLD_LINE, MUT_LINE))
        return root

    def knock(self, root, text=OWN_TEXT):
        """Живой путь: настоящий `pc_agent._exam_cli` → настоящая дверь отдельным процессом."""
        import pathlib
        pc_agent.REPO_DIR = pathlib.Path(root)
        return pc_agent._exam_cli("own", "", "filipp", text, True)

    def stored_rule(self, root):
        """Что ЛЕГЛО В ФАЙЛ: поле `как_правильно` последней строки базы уроков дерева."""
        path = os.path.join(root, "exam_live", "lessons.tsv")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8", newline="") as f:
            lines = [x for x in f.read().split("\n") if x.strip()]
        if len(lines) < 2:
            return None
        head = lines[0].split("\t")
        return lines[-1].split("\t")[head.index("как_правильно")]

    # ---------------------------------------------------------------- A: живой путь
    def test_the_owner_text_reaches_the_file_whole(self):
        said = self.knock(self.tree)
        self.assertIn("Записан КАНДИДАТ", said, said)
        got = self.stored_rule(self.tree)
        self.assertIsNotNone(got, "строка в базу уроков копии не легла: %s" % said)
        self.assertEqual(got, OWN_TEXT)
        self.assertEqual(len(got), len(OWN_TEXT))
        # Отдельной строкой — ЧИСЛО, а не только равенство: длина и есть тот прибор, которым
        # порча была замечена на живых уроках (175 у ловца против 305 на диске).
        self.assertEqual(len(got), len(OWN_TEXT.encode("utf-8").decode("utf-8")))

    # ---------------------------------------------------------------- B: мутант
    def test_switching_the_fix_off_gives_a_different_answer_by_number(self):
        enc = child_stdin_encoding()
        if enc in ("utf-8", "utf8"):
            self.skipTest("НЕИЗВЕСТНО: у ребёнка stdin уже utf-8 (%s) — класса «локаль ≠ utf-8» "
                          "на этой машине нет, и мутант здесь не воспроизводим" % enc)
        good = self.knock(self.tree)
        self.assertIn("Записан КАНДИДАТ", good, good)
        whole = self.stored_rule(self.tree)

        mut_root = os.path.join(self.tmp, "mut")
        self.build(mut_root)
        self.mutate(mut_root)
        said = self.knock(mut_root)
        broken = self.stored_rule(mut_root)

        self.assertIsNotNone(broken, "мутант не дописал строку вовсе: %s" % said)
        self.assertNotEqual(broken, whole,
                            "контрфакт дал ТОТ ЖЕ ответ — значит правка ничего не решает")
        self.assertGreater(len(broken), len(whole),
                           "мохибейк обязан быть ДЛИННЕЕ целого текста (%d против %d)"
                           % (len(broken), len(whole)))
        # Порча названа ПОИМЁННО: это ровно «UTF-8 прочитан как cp1251», а не любая другая.
        self.assertEqual(broken.encode("cp1251").decode("utf-8"), whole,
                         "порча мутанта не сводится обратным cp1251→utf-8 — класс другой")

    # ---------------------------------------------------------------- C: статика
    def test_the_door_has_no_bare_stdin_read_and_uses_the_lane_device(self):
        """ПО ДЕРЕВУ РАЗБОРА, А НЕ ПО ПОДСТРОКЕ. Слова `sys.stdin.read()` стоя́т в этом файле и
        в самой двери — в комментариях, которыми класс и объяснён. Текстовая сверка красила бы
        объяснение в дефект, а настоящий вызов пропустила бы, назовись он `stdin.read()`."""
        src = _src("exam_show.py")
        bad = [n.lineno for n in ast.walk(ast.parse(src, filename="exam_show.py"))
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "read" and isinstance(n.func.value, ast.Attribute)
               and n.func.value.attr == "stdin"]
        self.assertEqual(bad, [],
                         "голое чтение stdin вернулось (строки %s): труба UTF-8, декодер — локаль"
                         % bad)
        self.assertIn("io_utf8.read_stdin_utf8()", src,
                      "дверь перестала читать ланевым устройством")

    def test_the_parent_still_writes_the_pipe_in_utf8(self):
        """Вторая половина пары. Починка ребёнка бессмысленна, если родитель перестанет
        объявлять кодировку трубы: тогда байты в неё пойдут локалью, и класс вернётся с
        другого конца."""
        src = _src("pc_agent.py")
        i = src.index("def _exam_cli(")
        body = src[i:i + 2000]
        self.assertIn("input=stdin_text", body)
        self.assertIn('encoding="utf-8"', body)


class LaneDeviceReadsBytes(unittest.TestCase):
    """Само ланевое устройство: числа в обе стороны, без двери и без файлов."""

    def test_utf8_pipe_is_decoded_whole_and_cp1251_console_is_not_lost(self):
        import io

        class Fake(object):
            def __init__(self, raw):
                self.buffer = io.BytesIO(raw)

        self.assertEqual(io_utf8.read_stdin_utf8(Fake(OWN_TEXT.encode("utf-8"))), OWN_TEXT)
        # Родная консоль Windows шлёт cp1251 — текст не теряем и не подменяем пустотой.
        cyr = "Цены называть, только когда ясны даты"
        self.assertEqual(io_utf8.read_stdin_utf8(Fake(cyr.encode("cp1251"))), cyr)
        # Подменённый поток без .buffer (StringIO в тесте) — отдаём как есть, не падаем.
        self.assertEqual(io_utf8.read_stdin_utf8(io.StringIO(cyr)), cyr)
        # Байты, которые не разбирает ни один из двух, — замена, но НЕ пустота.
        got = io_utf8.read_stdin_utf8(Fake(b"\xff\xfe\x00\x41"))
        self.assertTrue(got, "поток с битыми байтами отдан пустым — текст владельца потерян")

    def test_a_process_without_stdin_at_all_is_answered_with_words_not_a_traceback(self):
        """Третий исход входной полосы: stdin у процесса может не быть вовсе (pythonw, DEVNULL),
        и `sys.stdin` тогда `None`. Падение здесь стоило бы владельцу трассы вместо расписки —
        отдаём пусто, а «пустой урок» вызывающий уже умеет назвать словами."""
        saved = io_utf8.sys.stdin
        try:
            io_utf8.sys.stdin = None
            self.assertEqual(io_utf8.read_stdin_utf8(), "")
        finally:
            io_utf8.sys.stdin = saved

    def test_the_bare_read_would_have_broken_it_by_number(self):
        """КОНТРФАКТ устройства: тот же вход через декодер локали даёт ДРУГУЮ длину."""
        import io
        raw = OWN_TEXT.encode("utf-8")
        mojibake = raw.decode("cp1251")
        self.assertNotEqual(len(mojibake), len(OWN_TEXT))
        self.assertEqual(len(mojibake), len(raw))
        self.assertEqual(io_utf8.read_stdin_utf8(
            type("F", (), {"buffer": io.BytesIO(raw)})()), OWN_TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
