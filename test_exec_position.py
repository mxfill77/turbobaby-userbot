# -*- coding: utf-8 -*-
"""ПРАВИЛО ИСПОЛНЯЮЩЕЙ ПОЗИЦИИ на ПК — зеркало VPS 5ca761d/b30e9b8 (04.08.2026).

Краснит имя, стоящее там, где его ИСПОЛНИТ разборщик. Проза — комментарий, докстринг,
печатаемая строка, шаблон поиска, argv, ИМЯ ФАЙЛА — красного не рождает; фактический вызов
рождает как прежде.

ЧТО ЗДЕСЬ ЗАКРЫТО ЭТОЙ ПРАВКОЙ, А ЧТО БЫЛО ЗАКРЫТО РАНЬШЕ — сказано разделами, потому что
замер на ПК разошёлся с сервером:
  • секция 2 (ИМЯ ОПЕРАЦИИ В ИМЕНИ ФАЙЛА) на ПК была закрыта ещё 31.07 — `_py_write_call`
    судит ФОРМУ ВЫЗОВА, а не подстроку. Здесь это ЗАМОК от повторного открытия, а не фикс;
  • секция 4 (SQL-ГЛАГОЛ В ПЕЧАТАЕМОЙ СТРОКЕ) — течь была ЖИВОЙ и закрыта этой правкой:
    все три формы (print / докстринг / комментарий) до неё давали `ask sqlite`, причём ОБЪЕКТ
    карточки тянулся из той же прозы («other.db», «таблица rides»).

ГРАНИЦА, НАЗВАННАЯ ЧЕСТНО (секция 5, `test_inline_printed_sql_stays_red`): ТЕКСТ САМОЙ КОМАНДЫ
судится ПОЛНЫМ сканом, и инлайн `-c` — это текст команды. Печатаемый SQL внутри `-c` остаётся
КРАСНЫМ. Это не недосмотр и не расхождение с сервером: замер на пост-правочном гарде VPS даёт
там ровно то же красное, когда база названа (зелень VPS без имени базы даёт `_sql_write_is_foreign`,
механизм, которого на ПК нет вовсе).

Файл называется test_*.py осознанно: гард НЕ сканирует содержимое тест-целей (`_is_test_target`),
иначе собственные фикстуры (`create_booking`, `UPDATE …`) красили бы прогон корпуса — так же
устроены `test_pretool_guard.py` и `test_guard_action_not_text.py`."""
import os
import shutil
import tempfile
import unittest

import pretool_guard as G

PY = os.path.join(G.PROJECT, "venv", "Scripts", "python.exe")
CLEAN = "print(1)\n"
NO_ENV = {}      # чужих «да» в замере нет


def verdict(cmd):
    """Полный конвейер: decide → decide_for_role → card_decision. → (решение, вид, объект)."""
    data = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": G.PROJECT}
    action, kind, obj = G.decide_for_role(data, True, NO_ENV)
    if action == "ask":
        action = G.card_decision(kind, obj, cmd)[0]
    return action, kind, obj


def is_red(cmd):
    """Красное = владелец так или иначе остановлен: карточка (`ask`) либо отказ (`deny`).
    Схлопывать их в один бит здесь можно и нужно: секции спрашивают про ВЛАСТЬ, а не про текст."""
    return verdict(cmd)[0] in ("ask", "deny")


class ExecPositionBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="execpos_")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def script(self, name, body):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        return p


class TestActualCallStillRed(ExecPositionBase):
    """(1) ФАКТИЧЕСКИЙ ВЫЗОВ КРАСНЕЕТ КАК ПРЕЖДЕ — правило ничего не ослабило."""

    def test_bridge_call_is_red(self):
        p = self.script("s1.py", "import bridge_http\nbridge_http.create_booking(7)\n")
        self.assertEqual(verdict(PY + " " + p)[1], "py_write")

    def test_money_call_is_red(self):
        p = self.script("s2.py", "import bridge_http\nbridge_http.add_transaction(500)\n")
        self.assertEqual(verdict(PY + " " + p)[1], "py_write")

    def test_sql_given_to_cursor_is_red(self):
        p = self.script("s3.py", "import sqlite3\nc = sqlite3.connect('other_ep.db')\n"
                                 "c.execute('UPDATE rides SET price=1')\n")
        self.assertEqual(verdict(PY + " " + p)[1], "sqlite")

    def test_sql_built_by_concat_is_red(self):
        """Склейка — тоже строка, отданная курсору: литерал берётся из ВСЕГО поддерева аргумента."""
        p = self.script("s4.py", "import sqlite3\nc = sqlite3.connect('other_ep.db')\n"
                                 "c.execute('UPDATE rides SET price=' + str(1))\n")
        self.assertEqual(verdict(PY + " " + p)[1], "sqlite")

    def test_sql_in_fstring_is_red(self):
        p = self.script("s5.py", "import sqlite3\np = 1\nc = sqlite3.connect('other_ep.db')\n"
                                 "c.execute(f'UPDATE rides SET price={p}')\n")
        self.assertEqual(verdict(PY + " " + p)[1], "sqlite")

    def test_executescript_is_red(self):
        p = self.script("s6.py", "import sqlite3\n"
                                 "sqlite3.connect('other_ep.db').executescript('DROP TABLE rides')\n")
        self.assertEqual(verdict(PY + " " + p)[1], "sqlite")


class TestNameInFileName(ExecPositionBase):
    """(2) ИМЯ ОПЕРАЦИИ В ИМЕНИ ФАЙЛА при ЧИСТОМ теле. На ПК закрыто 31.07 (`_py_write_call`
    судит форму вызова) — здесь ЗАМОК от повторного открытия, а не фикс этой правки."""

    def test_operation_name_in_script_path_is_not_red(self):
        p = self.script("create_booking_report.py", CLEAN)
        self.assertFalse(is_red(PY + " " + p))

    def test_money_name_in_script_path_is_not_red(self):
        p = self.script("add_transaction_audit.py", CLEAN)
        self.assertFalse(is_red(PY + " " + p))

    def test_sql_verb_in_script_path_is_not_red(self):
        p = self.script("update_rides_report.py", CLEAN)
        self.assertFalse(is_red(PY + " " + p))


class TestNameInProse(ExecPositionBase):
    """(3) ИМЯ ОПЕРАЦИИ В КОММЕНТАРИИ (закрыто 31.07.2026) — регресс неослабления."""

    def test_comment_is_not_red(self):
        p = self.script("s7.py", "# create_booking не трогаем\nprint(1)\n")
        self.assertFalse(is_red(PY + " " + p))


class TestSqlInProse(ExecPositionBase):
    """(4) SQL-ГЛАГОЛ В ПРОЗЕ ТЕЛА — ТЕЧЬ, ЗАКРЫТАЯ ЭТОЙ ПРАВКОЙ. До неё все три формы давали
    `ask sqlite`, и ОБЪЕКТ карточки тянулся из той же прозы: карточка про запись, за которой
    никакой записи нет."""

    def test_printed_sql_is_not_red(self):
        p = self.script("s8.py", "import sqlite3\n"
                                 "print('UPDATE rides SET price=1 в other_ep.db')\n")
        self.assertFalse(is_red(PY + " " + p))

    def test_sql_in_docstring_is_not_red(self):
        p = self.script("s9.py", '"""отчёт: UPDATE rides SET price=1 в other_ep.db"""\n' + CLEAN)
        self.assertFalse(is_red(PY + " " + p))

    def test_sql_in_comment_is_not_red(self):
        p = self.script("s10.py", "# UPDATE rides SET price=1\n" + CLEAN)
        self.assertFalse(is_red(PY + " " + p))


class TestNotWeakened(ExecPositionBase):
    """(5) НЕОСЛАБЛЕНИЕ: жёсткий класс, текст самой команды, слепой режим и прежде закрытые
    классы (шаблон поиска, argv скрипта) судятся ровно как судились."""

    def test_secrets_file_operand_still_red(self):
        """Путь к файлу секретов операндом — красное. Файла нет и он не открывается: краснит ИМЯ."""
        p = self.script("s11.py", CLEAN)
        self.assertEqual(verdict(PY + " " + p + " " + os.path.join(self.dir, ".env"))[1], "env")

    def test_sql_in_command_text_stays_red(self):
        """argv при инлайн `-c` НЕ режется (`_strip_script_cli_args`) — SQL остаётся текстом команды."""
        self.assertEqual(verdict(PY + ' -c "print(1)" "UPDATE rides SET price=1"')[1], "sqlite")

    def test_inline_printed_sql_stays_red(self):
        """ГРАНИЦА, названная честно: инлайн `-c` — это ТЕКСТ КОМАНДЫ, он под ПОЛНЫМ сканом.
        Пост-правочный гард VPS на той же форме с названной базой красен ровно так же."""
        self.assertEqual(verdict(PY + ' -c "print(\'UPDATE rides SET price=1\')"')[1], "sqlite")

    def test_unreadable_script_stays_red(self):
        cmd = PY + " " + os.path.join(self.dir, "нет_такого.py") + \
            ' "UPDATE rides SET price=1" other_ep.db'
        self.assertTrue(is_red(cmd))

    def test_blind_body_stays_red(self):
        """Разбора нет (тело не питон) → прежняя подстрока байт-в-байт, fail-closed."""
        self.assertTrue(is_red(PY + " - <<'EOF'\ncreate_booking(7)\nEOF"))

    def test_blind_body_sql_stays_red(self):
        p = self.script("s12.py", "def (:\n UPDATE rides SET price=1\n")   # синтаксис битый нарочно
        self.assertEqual(verdict(PY + " " + p)[1], "sqlite")

    def test_search_pattern_is_data(self):
        p = self.script("s13.py", CLEAN)
        self.assertFalse(is_red(PY + " " + p + " && grep -n create_booking nope_ep.log"))

    def test_script_argv_is_data(self):
        p = self.script("s14.py", CLEAN)
        self.assertFalse(is_red(PY + " " + p + ' "DONE закрыл create_booking"'))


class TestOrderSpecificBeforeGeneral(ExecPositionBase):
    """(6) ПОРЯДОК ПРОВЕРОК: обобщённый признак — ПОСЛЕ конкретных. На VPS этот порядок пришлось
    ВОССТАНАВЛИВАТЬ (снятие быстрого грепа подняло `confirmed` выше конкретных операций); на ПК
    правка ни одной проверки не сдвинула, и здесь это ЗАМЕРЕНО, а не обещано."""

    def test_named_operation_wins_over_sql_inside_body(self):
        """ВНУТРИ `_scan_python` порядок «конкретное раньше обобщённого» цел: имя боевой операции
        и SQL курсору в одном теле → владелец видит ИМЯ ОПЕРАЦИИ, а не «БД». Правка сдвинула
        только СОДЕРЖАНИЕ проверки SQL, не её место."""
        cmd = PY + ' -c "import bridge_http,sqlite3; bridge_http.create_booking(1); ' \
                   'sqlite3.connect(\'o.db\').execute(\'UPDATE t SET a=1\')"'
        self.assertEqual(verdict(cmd)[1:], ("py_write", "create_booking"))

    def test_both_ops_of_one_segment_are_known_to_guard(self):
        """ОСТАТОК, НАЗВАННЫЙ ЗАМЕРОМ (не этой правкой заведён и не ею чинится).

        Если перед `sqlite3` стоит пробел, шелловый признак `_RE_SQLITE_WORD` срабатывает в цикле
        `_RED_CMD` РАНЬШЕ, чем `_scan_python` доберётся до тела, и КАРТОЧКУ получает `sqlite`.
        Имя денежной операции при этом в карточку не попадает: `other_ops_in_command` по
        построению смотрит ДРУГИЕ сегменты («два вида из одного сегмента — одно действие с двумя
        ярлыками»), а здесь сегмент один. Цена: «да» на запись в БД исполняет и `create_booking`.

        Тест держит то, что от этого действительно зависит и что верно при ЛЮБОМ исходе развилки:
        разборщик обе операции ВИДИТ. Точки касания, если владелец решит менять порядок:
        `pretool_guard._decide_bash_body` (цикл `_RED_CMD` против `_scan_python`) и
        `pretool_guard.other_ops_in_command` (границa «другой сегмент»)."""
        cmd = PY + ' -c "import bridge_http, sqlite3; bridge_http.create_booking(1); ' \
                   'sqlite3.connect(\'o.db\').execute(\'UPDATE t SET a=1\')"'
        kinds = [k for k, _o in G.red_kinds_bash(cmd, G.PROJECT)]
        self.assertIn("py_write", kinds)
        self.assertIn("sqlite", kinds)

    def test_general_sign_is_still_last(self):
        """«без внятной цели» — обобщённый признак; он на месте и НИЖЕ конкретных."""
        self.assertEqual(verdict(PY)[1:], ("py_write", "без внятной цели"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
