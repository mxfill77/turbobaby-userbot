# -*- coding: utf-8 -*-
"""
test_card_terminal_log.py — ТЕРМИНАЛ КАРТОЧКИ В ЖУРНАЛЕ ПОЛОСЫ ПК.
БЕЗ моста/сети/боевого состояния: надзор уводится в temp, `_cowork` перехватывается.
Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_card_terminal_log -v

ЧЕТЫРЕ ЗАМКА ЗАХОДА, каждый — свой класс здесь:
  A. `TestGuardUntouched`   — поведение гарда прежнее: наблюдатель НЕ мутирует ничего и карточек
                              не касается (мост, роняющий тест на любой записи).
  B. `TestTraceAppears`     — след появляется у ВСЕХ исходов, на ТЕСТОВЫХ карточках.
  C. `TestNoFalseTrace`     — карточка без терминала строки НЕ порождает (в т.ч. при немом мосте).
  D. `TestNoLeak`           — тела команд, пути к секретам и содержимое карточки в строку не текут.
Плюс `TestMethodLock` — разности времени ТОЛЬКО через julianday() (ast-инвариант, не соглашение).
"""

import os
import ast
import json
import types
import inspect
import tempfile
import datetime
import unittest
from unittest import mock

os.environ["LESSON_LLM_ROUTE"] = "0"
for _marker in ("PRETOOL_APPROVED_KINDS", "PRETOOL_APPROVED_OBJECT", "PRETOOL_APPROVED_TASK"):
    os.environ.pop(_marker, None)

import card_terminal_log as ctl        # noqa: E402
import pc_orchestrator as o            # noqa: E402
import pretool_guard                   # noqa: E402


def iso(minutes_ago=0):
    """ISO-метка НАШЕЙ формы (UTC, без микросекунд) со сдвигом назад — та же, что пишет демон."""
    n = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return o._utc_iso(n)


def row(tid, status, result=""):
    return {"id": tid, "status": status, "lane": "pc", "result": result}


def idx(*rows):
    d = {}
    for r in rows:
        d[str(r["id"])] = {"status": r["status"], "result": r.get("result") or ""}
    return d


def entry(tid, opened=None, obj="цель.py", kinds=("py_write",)):
    return {"task": str(tid), "opened": opened or iso(60), "object": obj, "kinds": list(kinds)}


class ReadOnlyBridge:
    """Мост, который УМЕЕТ ТОЛЬКО ЧИТАТЬ. Любая мутация — падение теста, а не тихий побочный
    эффект: это и есть замок A в исполнимой форме (наблюдатель не смеет трогать карточки)."""

    def __init__(self, rows=(), ok=True):
        self.rows = list(rows)
        self.ok = ok
        self.reads = []

    def get_pending(self, status, lane="pc"):
        self.reads.append(status)
        if not self.ok:
            return {"ok": False, "error": "bridge down"}
        return {"ok": True, "items": [dict(r) for r in self.rows if r["status"] == status]}

    def _forbidden(self, name):
        raise AssertionError("наблюдатель терминала вызвал МУТАЦИЮ моста: " + name)

    def complete_task(self, *a, **k):
        self._forbidden("complete_task")

    def set_needs_approval(self, *a, **k):
        self._forbidden("set_needs_approval")

    def approve_task(self, *a, **k):
        self._forbidden("approve_task")

    def claim_task(self, *a, **k):
        self._forbidden("claim_task")

    def enqueue_task(self, *a, **k):
        self._forbidden("enqueue_task")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="turbobaby_TESTING_cardterm_")
        self.watch = os.path.join(self.dir, "card_watch.json")
        self.said = []
        p = mock.patch.object(o, "_cowork", side_effect=lambda s: self.said.append(s))
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("CARD_TERMINAL_OFF", None)

    def run_pass(self, entries, rows=(), ok=True):
        """Один проход наблюдателя на ТЕСТОВОМ надзоре и ТЕСТОВОМ мосте. → (сколько строк, мост)."""
        ctl.save(self.watch, entries)
        br = ReadOnlyBridge(rows, ok=ok)
        with mock.patch.object(o, "bc", br):
            n = o.process_card_terminals(path=self.watch)
        return n, br


# ─────────────────────────── ЗАМОК B: СЛЕД ПОЯВЛЯЕТСЯ ────────────────────────────────────────

class TestTraceAppears(Base):
    """Каждый исход карточки оставляет РОВНО ОДНУ строку журнала. Карточки тестовые."""

    def test_approved_gives_line(self):
        n, br = self.run_pass([entry(11)], [row(11, "approved")])
        self.assertEqual(n, 1)
        self.assertEqual(len(self.said), 1)
        self.assertIn("терминал карточки #11", self.said[0])
        self.assertIn("разрешено", self.said[0])

    def test_rejected_gives_line(self):
        res = o._REJECT_PREFIX + " (кнопка, Filipp)"
        n, _ = self.run_pass([entry(12)], [row(12, "failed", res)])
        self.assertEqual(n, 1)
        self.assertIn("терминал карточки #12", self.said[0])
        self.assertIn("отказано", self.said[0])

    def test_expired_gives_line(self):
        res = "⏱ провал [причина=approval_timeout · ждали «да»]: подтверждение не получено"
        n, _ = self.run_pass([entry(13)], [row(13, "failed", res)])
        self.assertEqual(n, 1)
        self.assertIn("истекло", self.said[0])

    def test_unproven_terminal_gets_fourth_word_not_a_guess(self):
        """Строка легла в failed без обоих маркеров: карточка ЗАКРЫТА — да, чем — не доказано."""
        n, _ = self.run_pass([entry(14)], [row(14, "failed", "провал: тесты красные")])
        self.assertEqual(n, 1)
        self.assertIn("закрыто", self.said[0])
        for word in ("разрешено", "отказано", "истекло"):
            self.assertNotIn(word, self.said[0])

    def test_line_carries_the_four_named_fields(self):
        """Владелец назвал состав строки: номер · исход одним словом · время · объект."""
        line = ctl.terminal_line(6, ctl.OUT_REJECTED, iso(63), iso(0), "pc_orchestrator.py",
                                 ["py_write"])
        self.assertIn("#6", line)
        self.assertIn("отказано", line)
        self.assertRegex(line, r"открыта \d\d:\d\d UTC, закрыта \d\d:\d\d UTC \(6[23] мин\)")
        self.assertIn("объект: pc_orchestrator.py", line)
        self.assertIn("классы: py_write", line)

    def test_object_absent_is_said_out_loud(self):
        line = ctl.terminal_line(7, ctl.OUT_EXPIRED, iso(5), iso(0), "", [])
        self.assertIn("объект не назван", line)

    def test_terminal_written_once_and_card_leaves_watch(self):
        """Второй проход по той же карточке строки НЕ повторяет — дубля в журнале не будет."""
        self.run_pass([entry(15)], [row(15, "approved")])
        self.assertEqual(len(self.said), 1)
        left = ctl.load(self.watch)
        self.assertEqual(left, [])
        br = ReadOnlyBridge([row(15, "approved")])
        with mock.patch.object(o, "bc", br):
            self.assertEqual(o.process_card_terminals(path=self.watch), 0)
        self.assertEqual(len(self.said), 1)

    def test_all_four_words_are_single_words(self):
        for w in (ctl.OUT_APPROVED, ctl.OUT_REJECTED, ctl.OUT_EXPIRED, ctl.OUT_CLOSED):
            self.assertEqual(len(w.split()), 1, w)

    def test_registration_takes_card_under_watch(self):
        card = pretool_guard._card("py_write", obj="tmp/x.py", raw_cmd="python tmp/x.py")
        o._card_watch_add(41, card, path=self.watch)
        got = ctl.load(self.watch)
        self.assertEqual([e["task"] for e in got], ["41"])
        self.assertTrue(got[0]["opened"])

    def test_first_registration_wins(self):
        """Карточку редактируют (ревизор так и делает) — возраст обязан считаться от ПЕРВОЙ."""
        first = iso(120)
        e = ctl.watch_add([], 9, first, "a", ["delete"])
        e = ctl.watch_add(e, 9, iso(0), "b", ["kill"])
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["opened"], first)


# ─────────────────────── ЗАМОК C: ОТРИЦАТЕЛЬНЫЙ ТЕСТ ─────────────────────────────────────────

class TestNoFalseTrace(Base):
    """Карточка, чей терминал НЕ наступил, строки терминала не порождает ни одной веткой."""

    def test_still_waiting_gives_no_line(self):
        n, _ = self.run_pass([entry(21)], [row(21, "needs_approval")])
        self.assertEqual(n, 0)
        self.assertEqual(self.said, [])
        self.assertEqual([e["task"] for e in ctl.load(self.watch)], ["21"])

    def test_bridge_silent_gives_no_line_and_keeps_episode_open(self):
        """Мост ответил не-ok: это «неизвестно», а не «карточку закрыли». Эпизод остаётся открыт."""
        n, br = self.run_pass([entry(22)], [row(22, "failed", o._REJECT_PREFIX)], ok=False)
        self.assertEqual(n, 0)
        self.assertEqual(self.said, [])
        self.assertEqual([e["task"] for e in ctl.load(self.watch)], ["22"])

    def test_row_absent_from_snapshot_gives_no_line(self):
        n, _ = self.run_pass([entry(23)], [])
        self.assertEqual(n, 0)
        self.assertEqual(self.said, [])
        self.assertEqual([e["task"] for e in ctl.load(self.watch)], ["23"])

    def test_in_progress_and_new_are_not_terminals(self):
        for st in ("new", "in_progress", "needs_approval"):
            self.assertIsNone(ctl.classify({"status": st, "result": ""}), st)

    def test_empty_watch_does_not_touch_the_bridge_at_all(self):
        """Цена ветки при пустом надзоре — НОЛЬ чтений моста (утверждение из блока в демоне)."""
        n, br = self.run_pass([], [row(1, "approved")])
        self.assertEqual(n, 0)
        self.assertEqual(br.reads, [])

    def test_horizon_drop_writes_nothing_to_the_journal(self):
        """Снятие по горизонту — БЕЗ строки: терминала не наблюдали, писать о нём нельзя."""
        old = [entry(24, opened=iso(60 * 24 * 40))]
        n, _ = self.run_pass(old, [])
        self.assertEqual(n, 0)
        self.assertEqual(self.said, [])
        self.assertEqual(ctl.load(self.watch), [])

    def test_off_switch_kills_the_branch_whole(self):
        ctl.save(self.watch, [entry(25)])
        os.environ["CARD_TERMINAL_OFF"] = "1"
        self.addCleanup(lambda: os.environ.pop("CARD_TERMINAL_OFF", None))
        br = ReadOnlyBridge([row(25, "approved")])
        with mock.patch.object(o, "bc", br):
            self.assertEqual(o.process_card_terminals(path=self.watch), 0)
        self.assertEqual(br.reads, [])
        self.assertEqual(self.said, [])
        o._card_watch_add(99, "🔴 что-то — разрешить?", path=self.watch)
        self.assertEqual([e["task"] for e in ctl.load(self.watch)], ["25"])


# ────────────────────── ЗАМОК D: ПДн И СЕКРЕТЫ НЕ ТЕКУТ ──────────────────────────────────────

class TestNoLeak(Base):
    """В строку идут ТОЛЬКО названные поля. Показано на самом неудобном случае: карточка чтения
    секрета, где и объект — путь к файлу секретов, и в теле карточки лежит сама команда."""

    def test_secret_card_leaks_neither_path_nor_command(self):
        secret = os.path.join("D:\\turbobaby-bot", ".env")
        cmd = "Get-Content " + secret
        card = pretool_guard._card("read_secret", obj=secret, raw_cmd=cmd)
        self.assertIn("Команда:", card)          # ФИКСИРУЕМ: тело команды в карточке ЕСТЬ
        self.assertIn(".env", card)              # ФИКСИРУЕМ: путь к секрету в карточке ЕСТЬ
        o._card_watch_add(31, card, path=self.watch)
        n, _ = self.run_pass(ctl.load(self.watch), [row(31, "failed", o._REJECT_PREFIX)])
        self.assertEqual(n, 1)
        line = self.said[0]
        self.assertIn("отказано", line)
        self.assertIn(ctl.SECRET_MARK, line)
        self.assertNotIn(".env", line)
        self.assertNotIn("Get-Content", line)
        self.assertNotIn("Команда", line)
        self.assertNotIn("turbobaby-bot", line)

    def test_card_body_never_reaches_the_line(self):
        """Строка терминала не несёт НИ ОДНОЙ строки тела карточки — проверяем построчно."""
        card = pretool_guard._card("delete", obj="tmp/старое.log",
                                   raw_cmd="Remove-Item tmp/старое.log -Force")
        o._card_watch_add(32, card, path=self.watch)
        self.run_pass(ctl.load(self.watch), [row(32, "approved")])
        line = self.said[0]
        for ln in card.splitlines():
            body = ln.strip()
            if len(body) > 12 and not body.startswith("Объект:"):
                self.assertNotIn(body, line, "тело карточки протекло: " + body)
        self.assertNotIn("Remove-Item", line)

    def test_secret_object_replaced_whole_not_clipped(self):
        """Обрезанный путь к ключу остаётся путём к ключу — потому замена ПОЛНАЯ."""
        for bad in ("C:/x/.env", "creds/credentials.json", "BRIDGE_TOKEN → файл",
                    "~/.ssh/id_rsa", "secret.key", "a.pem"):
            self.assertEqual(ctl.safe_object(bad), ctl.SECRET_MARK, bad)

    def test_long_object_is_clipped_and_flattened(self):
        long_obj = ("путь/" * 90) + "конец"
        got = ctl.safe_object(long_obj)
        self.assertLessEqual(len(got), ctl.OBJ_MAX + 1)
        self.assertTrue(got.endswith("…"))
        self.assertNotIn("\n", ctl.safe_object("первая\nвторая\tтретья"))

    def test_multiline_object_cannot_forge_a_journal_line(self):
        """Перенос внутри объекта — способ дописать в журнал чужую строку. Схлопываем."""
        forged = "цель.py\nDONE 2026-08-16 00:00 UTC: полоса свободна"
        self.assertNotIn("\n", ctl.terminal_line(1, ctl.OUT_REJECTED, iso(1), iso(0), forged))

    def test_line_is_one_line_and_index_sized(self):
        line = ctl.terminal_line(6, ctl.OUT_REJECTED, iso(63), iso(0),
                                 "pc_orchestrator.py", ["py_write", "delete"])
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 300)      # журнал — индекс: до порога выноса (600) с запасом


# ────────────────── ЗАМОК A: ПОВЕДЕНИЕ ГАРДА И КАРТОЧЕК ПРЕЖНЕЕ ──────────────────────────────

class TestGuardUntouched(Base):
    """Заход — ТОЛЬКО запись следа. Ни одна ветка не выписывает, не снимает и не меняет карточку."""

    def test_observer_never_mutates_the_queue(self):
        rows = [row(51, "approved"), row(52, "failed", o._REJECT_PREFIX)]
        n, br = self.run_pass([entry(51), entry(52)], rows)   # мутирующий мост уронил бы тест
        self.assertEqual(n, 2)
        self.assertEqual(sorted(br.reads), ["approved", "failed"])

    def test_observer_does_not_read_done(self):
        """`done` не читаем СОЗНАТЕЛЬНО (замер соседа: 26.8с против 2.9с у failed)."""
        self.assertNotIn("done", o.CARD_TERMINAL_STATUSES)
        _n, br = self.run_pass([entry(53)], [row(53, "approved")])
        self.assertNotIn("done", br.reads)

    def test_module_does_not_touch_the_guard_at_all(self):
        """Число выписанных карточек совпадает ПО ПОСТРОЕНИЮ: ядро гарда не зовёт вовсе.

        Пинуем ВЕСЬ набор импортов, а не отсутствие одного имени: так замок держит и то, что
        модуль не дотянется до моста, сети и подпроцессов. Проверка по ast, а не грепом по
        тексту — в докстринге гард УПОМЯНУТ, и греп дал бы ложное срабатывание."""
        tree = ast.parse(inspect.getsource(ctl))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        self.assertEqual(imported, {"json", "os", "re", "sqlite3"})
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertNotIn("pretool_guard", names)

    def test_observer_runs_before_process_approved(self):
        """ПОРЯДОК — часть контракта: после process_approved наблюдатель читал бы исход ре-рана
        и звал бы одобренную карточку «закрыто»."""
        src = inspect.getsource(o.poll_once)
        self.assertLess(src.index("process_card_terminals()"), src.index("process_approved()"))
        self.assertLess(src.index("process_card_terminals()"), src.index("process_approval_timeouts()"))

    def test_heartbeat_is_still_the_last_line_of_the_turn(self):
        """О2/О4 стоят на том, что heartbeat — ПОСЛЕДНЯЯ строка оборота. Врезка это не сдвинула."""
        body = [ln.strip() for ln in inspect.getsource(o.poll_once).splitlines() if ln.strip()]
        calls = [ln for ln in body if ln.endswith(")") and "(" in ln and not ln.startswith('"')]
        self.assertTrue(calls[-1].startswith("_write_heartbeat()"), calls[-1])

    def test_markers_are_bound_to_the_daemon_literals(self):
        """Дословность маркеров сторожит ТЕСТ, а не комментарий (идиома queue_snapshot_pc)."""
        self.assertEqual(ctl.REJECT_MARK, o._REJECT_PREFIX)
        self.assertIn(o.FAIL_APPROVAL_TIMEOUT, ctl.TIMEOUT_MARK)

    def test_timeout_marker_matches_a_real_fail_result(self):
        """Маркер сверен с ЖИВЫМ текстом, который пишет сам демон, а не с идеальной схемой."""
        msg = o.fail_result(o.FAIL_APPROVAL_TIMEOUT, "подтверждение не получено за 30 мин")
        self.assertEqual(ctl.classify({"status": "failed", "result": msg}), ctl.OUT_EXPIRED)

    def test_reject_marker_matches_the_pc_answer_channel(self):
        """Отказ, поставленный СВОИМ каналом ответа (`--reject N`), тоже читается как отказ."""
        txt = o._REJECT_PREFIX + " (ответ с ПК: Filipp/human)"
        self.assertEqual(ctl.classify({"status": "failed", "result": txt}), ctl.OUT_REJECTED)


# ───────────────── ЗАМОК МЕТОДА: РАЗНОСТИ ВРЕМЕНИ — ТОЛЬКО julianday() ───────────────────────

class TestMethodLock(unittest.TestCase):

    def test_no_python_subtraction_in_the_module(self):
        """Инвариант, а не соглашение: ни одного узла ast.Sub во всём модуле (зеркало
        `result_judge_pc`). Разность времени на Python — ровно тот класс, ради которого замок."""
        tree = ast.parse(inspect.getsource(ctl))
        subs = [n for n in ast.walk(tree) if isinstance(n, ast.Sub)]
        self.assertEqual(len(subs), 0, "разность на Python: %d узлов" % len(subs))

    def test_module_has_no_clock_and_no_datetime(self):
        src = inspect.getsource(ctl)
        for banned in ("import datetime", "import time", "timedelta", "datetime.now"):
            self.assertNotIn(banned, src, banned)

    def test_difference_is_computed_by_julianday(self):
        self.assertIn("julianday", inspect.getsource(ctl.minutes_between))
        got = ctl.minutes_between("2026-08-16T09:44:00+00:00", "2026-08-16T08:41:00+00:00")
        self.assertAlmostEqual(got, 63.0, places=3)

    def test_our_iso_form_survives_julianday(self):
        """ЗАМЕР, А НЕ ДОГАДКА (16.08.2026). Гипотеза захода была «шесть знаков дроби ломают
        julianday в NULL» — она ОПРОВЕРГНУТА тут же: SQLite разбирает и шестизначную дробь, и
        разность на ней верна. Значит срез микросекунд в `_utc_iso` держится НЕ на этой мине;
        честная причина — форма метки (её читает человек в файле надзора), а не разбор.
        Тест оставлен именно как замок против возврата ложного объяснения."""
        a = datetime.datetime(2026, 8, 16, 8, 41, 0, 123456, tzinfo=datetime.timezone.utc)
        b = datetime.datetime(2026, 8, 16, 9, 44, 0, 654321, tzinfo=datetime.timezone.utc)
        self.assertNotIn(".", o._utc_iso(a))
        self.assertAlmostEqual(ctl.minutes_between(o._utc_iso(b), o._utc_iso(a)), 63.0, places=3)
        self.assertAlmostEqual(ctl.minutes_between(b.isoformat(), a.isoformat()), 63.0, places=1)

    def test_broken_time_loses_the_duration_not_the_line(self):
        self.assertIsNone(ctl.minutes_between("не время", "тоже не время"))
        line = ctl.terminal_line(8, ctl.OUT_APPROVED, "мусор", iso(0), "цель")
        self.assertIn("терминал карточки #8", line)
        self.assertIn("разрешено", line)


# ─────────────────────────── ФАЙЛ НАДЗОРА ────────────────────────────────────────────────────

class TestWatchFile(Base):

    def test_roundtrip(self):
        e = [entry(61), entry(62)]
        self.assertTrue(ctl.save(self.watch, e))
        self.assertEqual(ctl.load(self.watch), e)

    def test_missing_and_broken_file_do_not_raise(self):
        self.assertEqual(ctl.load(os.path.join(self.dir, "нет.json")), [])
        with open(self.watch, "w", encoding="utf-8") as f:
            f.write("{не json")
        self.assertEqual(ctl.load(self.watch), [])

    def test_save_is_atomic_no_truncate_in_place(self):
        """tmp+os.replace: обрыв записи не смеет унести список открытых карточек."""
        self.assertIn("os.replace", inspect.getsource(ctl.save))

    def test_watch_is_capped(self):
        e = []
        for i in range(ctl.WATCH_KEEP + 25):
            e = ctl.watch_add(e, i, iso(1))
        self.assertEqual(len(e), ctl.WATCH_KEEP)

    def test_state_path_is_isolated_under_test(self):
        """Боевой файл надзора под тестом уведён в temp — иначе гейт правил бы живое состояние."""
        self.assertNotEqual(o.CARD_WATCH_FILE, os.path.join(o.REPO, "pc_orchestrator.card_watch.json"))

    def test_watch_file_is_gitignored(self):
        with open(os.path.join(o.REPO, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("pc_orchestrator.*.json", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
