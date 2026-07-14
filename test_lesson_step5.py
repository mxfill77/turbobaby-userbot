# -*- coding: utf-8 -*-
"""
test_lesson_step5.py — СВОДНЫЙ голден цикла урока (родитель 306, шаг 5/6).

Шаги 2–4 закрыли частные разрывы «тест ≠ реальность» (message_id карточки, адресаты
дефолтных sink'ов, живой реплей окна). Шаг 5/6 сшивает ПЕРЕЧЕНЬ инвариантов спеки в один
приёмочный модуль на РЕАЛЬНЫХ функциях обоих модулей + фикстуре — и закрывает два ещё
незамкнутых разрыва:

  • ТРИГГЕР «3 слова, ТОЛЬКО в начале»: юниты проверяли, что префикс распознаётся, но НИКТО не
    проверял обратное — что тот же триггер В СЕРЕДИНЕ реплики НЕ перехватывается (иначе обычная
    реплика клиента со словами «не так» стала бы уроком). Плюс замок на состав: ровно 3 триггера.
  • Bridge/таблицы/деньги НЕ трогаются: спека и докстринг lesson_router обещают это словами —
    здесь ДОКАЗЫВАЕМ грепом/AST, что маршрутизатор урока не импортирует БД/Bridge/pricing и не
    несёт SQL-записи. Тихий добавленный `import sqlite3`/`import pricing` провалит этот тест.

Остальной перечень спеки шага 5/6 (права approver/чужой; классификация СТИЛЬ/ФАКТ/НАДЗОР;
неясное→карточка в 1160; подтверждение ТОЛЬКО после коммита; голден полного цикла реплай→
задача→диф→реплей→подтверждение) пере-утверждается здесь на реальных функциях как единый
чек-лист приёмки — defense-in-depth поверх точечных юнитов test_moderation/test_lesson_*.

БЕЗ Telegram/Bridge/Anthropic и БЕЗ боевых доков/очереди/1160: enqueue/checklist/notify/commit
инъектируются фейками, диф чек-листа пишется во ВРЕМЕННЫЙ файл. Golden-правило CLAUDE.md:
дословные формулировки учителя берём из fixtures/lesson_cycle_292.json.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_lesson_step5 -v
"""

import test_isolation  # noqa: F401 — ПЕРВОЙ строкой: TESTING=1, боевой IPC/токен недоступны

import ast
import json
import os
import tempfile
import unittest

import suggest
import moderation_core as mc
import lesson_router as lr

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "lesson_cycle_292.json")
TEACHERS = ("filipp", "filipp_alt", "danya", "dasha")


def _load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class _ApproverEnv(unittest.TestCase):
    """Общая обвязка: учителя фикстуры = INTAKE_APPROVERS; approve открыт всем (учить всё равно строже)."""

    def setUp(self):
        self._save = (suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES)
        suggest.INTAKE_APPROVERS = set(TEACHERS)
        suggest.APPROVER_USERNAMES = set()

    def tearDown(self):
        suggest.INTAKE_APPROVERS, suggest.APPROVER_USERNAMES = self._save


class TestTriggerThreeWordsOnlyAtStart(_ApproverEnv):
    """ТРИГГЕР: ровно 3 префикса-слова, распознаются ТОЛЬКО в начале реплики."""

    DRAFT = {"id": 7, "draft": "черновик", "client_id": 555, "client_ref": "@petya"}

    def test_exactly_three_triggers(self):
        # «3 слова» спеки — состав зафиксирован; тихое добавление/удаление триггера провалит замок
        self.assertEqual(("правка:", "урок:", "не так:"), mc.LESSON_TRIGGERS)
        self.assertEqual(3, len(mc.LESSON_TRIGGERS))

    def test_trigger_at_start_is_lesson(self):
        for trig, kind in (("правка:", "правка"), ("урок:", "урок"), ("не так:", "не так")):
            p = mc.parse_lesson(f"{trig} важное замечание")
            self.assertIsNotNone(p, trig)
            self.assertEqual(kind, p["kind"], trig)
            self.assertEqual("важное замечание", p["remark"], trig)

    def test_trigger_mid_message_is_not_lesson(self):
        # ГЛАВНЫЙ инвариант «только в начале»: тот же префикс В СЕРЕДИНЕ — обычная реплика, НЕ урок
        for text in (
            "клиент пишет правка: сделай скидку",       # «правка:» не в начале
            "тут что-то не так: перепроверь",            # «не так:» не в начале
            "дай урок: как отвечать",                    # «урок:» не в начале
            "это не так, переделай",                     # похоже, но без двоеточия и не в начале
        ):
            self.assertIsNone(mc.parse_lesson(text), text)
            self.assertEqual("not_lesson",
                             mc.process_lesson(self.DRAFT, text, "danya")["decision"], text)

    def test_leading_whitespace_and_case_still_trigger(self):
        # «в начале» терпимо к ведущим пробелам/переносам и регистру (lstrip + lower), но не к позиции
        p = mc.parse_lesson("  \n УРОК:  уточняй даты ")
        self.assertEqual({"kind": "урок", "remark": "уточняй даты"}, p)


class TestRightsApproverVsStranger(_ApproverEnv):
    """ПРАВА: учить может только INTAKE_APPROVERS; чужому — вежливый отказ, окно/замечание не отдаём."""

    DRAFT = {"id": 7, "draft": "черновик", "client_id": 555, "client_ref": "@petya"}

    def test_each_teacher_accepted(self):
        for who in TEACHERS:
            d = mc.process_lesson(self.DRAFT, "урок: уточняй опыт", who)
            self.assertEqual("lesson", d["decision"], who)
            self.assertEqual(555, d["window"], who)         # окно диалога = client_id карточки

    def test_stranger_denied_no_leak(self):
        d = mc.process_lesson(self.DRAFT, "правка: пиши мягче", "stranger")
        self.assertEqual("denied", d["decision"])
        self.assertIn("только Филипп", d["card"])
        self.assertNotIn("window", d)                       # чужому окно/замечание не отдаём
        self.assertNotIn("remark", d)

    def test_intake_stricter_than_approve(self):
        # approve открыт всем (APPROVER пуст), но учить вне INTAKE_APPROVERS нельзя
        self.assertTrue(suggest.is_approver("stranger"))
        self.assertFalse(suggest.is_intake_approver("stranger"))
        self.assertTrue(suggest.is_intake_approver("@Danya"))   # @ и регистр нормализуются


class TestClassificationThreeClasses(unittest.TestCase):
    """КЛАССИФИКАЦИЯ СТИЛЬ/ФАКТ/НАДЗОР (+ НЕЯСНОЕ) на ДОСЛОВНЫХ репликах учителя из фикстуры."""

    @classmethod
    def setUpClass(cls):
        cls.fx = _load_fixture()

    def _remark_of(self, name):
        for sc in self.fx["cycles"]:
            if sc["name"] == name:
                return mc.parse_lesson(sc["reply_text"])["remark"]
        self.fail(f"нет цикла {name} в фикстуре")

    def test_live_remarks_route_to_named_classes(self):
        cases = (
            ("style_dry_tone", lr.STYLE),
            ("fact_price_nmax", lr.FACT),
            ("supervision_fake_price", lr.SUPERVISION),
            ("unclear_redo", lr.UNCLEAR),
        )
        for name, want in cases:
            remark = self._remark_of(name)
            self.assertEqual(want, lr.classify_lesson_remark(remark)[0], remark)

    def test_priority_supervision_and_fact_over_style(self):
        # старший маркер бьёт попутный младший: НАДЗОР>ФАКТ>СТИЛЬ
        self.assertEqual(lr.SUPERVISION, lr.classify_lesson_remark("ревизор не поймал повтор приветствия")[0])
        self.assertEqual(lr.FACT, lr.classify_lesson_remark("звучит нормально, но цена неверная")[0])


class TestUnclearToOwnerCard1160(unittest.TestCase):
    """НЕЯСНОЕ: не угадываем → карточка-уточнение ВЛАДЕЛЬЦУ в 1160 (замечание не теряется)."""

    def _task(self, remark):
        return (f"[урок:правка от @danya] родитель 306 — замечание менеджера\n"
                f"окно диалога: Света (999) (client_id=999) · черновик #33\n"
                f"карточка модер-группы: msg=90510\n"
                f"Замечание: {remark}\n"
                f"Исходный черновик: Здравствуйте! Чем помочь?")

    def test_unclear_cards_owner(self):
        sent = {}
        dec = lr.handle_lesson_task(self._task("плохо, переделай"),
                                    append_style=lambda r: self.fail("style не должен звать"),
                                    append_checklist=lambda r: self.fail("checklist не должен звать"),
                                    notify_owner=lambda c: sent.__setitem__("card", c) or True)
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertFalse(dec["delegate"])
        self.assertTrue(dec["delivered"])
        self.assertIn("плохо, переделай", sent["card"])     # исходное замечание — в карточке владельцу
        self.assertIn("Неясный урок", sent["card"])
        self.assertIn("1160", dec["result"])

    def test_owner_channel_down_is_failsafe(self):
        # канал 1160 упал → не роняем, замечание видно в результате (повторят)
        dec = lr.handle_lesson_task(self._task("что-то не то"),
                                    notify_owner=lambda c: (_ for _ in ()).throw(RuntimeError("1160 down")))
        self.assertEqual(lr.UNCLEAR, dec["route"])
        self.assertFalse(dec["delivered"])
        self.assertIn("не доставлена", dec["result"])


class TestAckOnlyAfterCommit(unittest.TestCase):
    """ПОДТВЕРЖДЕНИЕ «урок принят…» формируется ТОЛЬКО после ДОКАЗАННОГО реального коммита."""

    def test_withheld_without_commit(self):
        self.assertIsNone(lr.ack_after_commit("", "суть", "чек-лист",
                                              verify=lambda r: self.fail("verify не должен зваться на пустом ref")))
        self.assertIsNone(lr.ack_after_commit(None, "суть", "чек-лист"))
        self.assertIsNone(lr.ack_after_commit("deadbeef", "суть", "чек-лист", verify=lambda r: False))

    def test_ack_after_real_commit(self):
        ack = lr.ack_after_commit("abc1234", "цена нмакс исправлена", "код + golden-тест",
                                  verify=lambda ref: True)
        self.assertIsNotNone(ack)
        self.assertIn("Урок принят", ack)
        self.assertIn("цена нмакс исправлена", ack)
        self.assertIn("применится со следующего ответа", ack)


class TestBridgeMoneyTablesUntouched(unittest.TestCase):
    """Bridge/таблицы/деньги НЕ трогаются маршрутизатором урока — доказываем грепом/AST по исходнику
    lesson_router.py (докстринг обещает это словами, тест доказывает по построению)."""

    SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lesson_router.py")

    # Разрешённый импортный контур урока: файловые правки доков + ленивый playbook-sink (текст) +
    # read-only git-verify коммита. Ничего из БД/Bridge/сети/pricing тут быть НЕ должно.
    _ALLOWED = {"os", "re", "suggest", "subprocess"}
    _FORBIDDEN = {"sqlite3", "moderation_ipc", "pricing", "requests", "httpx", "urllib",
                  "urllib.request", "socket", "aiohttp", "telethon", "psycopg2"}

    def _imported_modules(self):
        with open(self.SRC, encoding="utf-8") as f:
            tree = ast.parse(f.read(), self.SRC)
        mods = set()
        for node in ast.walk(tree):          # и top-level, и ленивые импорты внутри функций
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        return mods

    def test_imports_are_within_allowed_contour(self):
        mods = self._imported_modules()
        self.assertTrue(mods, "не удалось разобрать импорты lesson_router")
        # ни одного forbidden-импорта (Bridge/БД/сеть/деньги)
        self.assertEqual(set(), mods & self._FORBIDDEN, f"запретный импорт: {mods & self._FORBIDDEN}")
        # и вообще ничего сверх разрешённого контура (тихий новый import провалит тест)
        self.assertLessEqual(mods, self._ALLOWED, f"импорт вне контура урока: {mods - self._ALLOWED}")

    def test_no_queue_or_sql_write_in_source(self):
        # Отсутствие Bridge доказано контуром импортов (тронуть Bridge нельзя без импорта). Здесь —
        # что нет ВЫЗОВОВ очереди/Bridge-lane и SQL-записи в таблицы. Слово «Bridge» само по себе тут
        # НЕ ищем: оно легитимно стоит в докстринге и delegate-note как ЗАПРЕТ (не как обращение).
        low = self._code_only().lower()
        for token in ("enqueue_pc_task", "lane=", "bridge_call", "bridge("):
            self.assertNotIn(token, low, token)             # маршрутизатор урока не зовёт очередь/Bridge
        # DB-специфичные конструкции (не общие list.insert/dict.update): их тут быть не должно —
        # строковые литералы уже вырезаны токенайзером, так что «insert» из SQL сюда не попал бы.
        for db in (".execute(", "executemany", "cursor(", "sqlite", ".commit("):
            self.assertNotIn(db, low, db)                   # и не пишет в таблицы

    def _code_only(self):
        """Исходник БЕЗ строковых литералов и комментариев (через tokenize) — чтобы грепать реальный
        код, а не прозу докстрингов, где «Bridge/деньги/таблицы» упомянуты как ЗАПРЕТ."""
        import io
        import tokenize
        with open(self.SRC, encoding="utf-8") as f:
            src = f.read()
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            out.append(tok.string)
        return " ".join(out)

    def test_only_readonly_git_in_ack_gate(self):
        # subprocess в модуле только для ПРОВЕРКИ коммита (rev-parse), не для записи (commit/push/add)
        with open(self.SRC, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("rev-parse", src)
        for write_verb in ('"commit"', "'commit'", '"push"', '"add"'):
            self.assertNotIn(write_verb, src, write_verb)


class TestGoldenFullCycleOnFixture(_ApproverEnv):
    """ГОЛДЕН полного цикла на фикстуре: реплай → задача → диф → живой реплей → подтверждение.
    Сквозной прогон РЕАЛЬНЫМИ функциями обоих модулей на НАДЗОР-цикле (несёт все 5 стадий:
    реальный диф чек-листа + живой реплей преамбулы ревизора + ack после коммита)."""

    @classmethod
    def setUpClass(cls):
        cls.fx = _load_fixture()

    def _cycle(self, name):
        for sc in self.fx["cycles"]:
            if sc["name"] == name:
                return sc
        self.fail(f"нет цикла {name}")

    def test_supervision_cycle_end_to_end(self):
        import pc_orchestrator as o
        sc = self._cycle("supervision_fake_price")
        exp = sc["expect"]

        # 1) РЕПЛАЙ учителя → ШТАТНАЯ задача в очередь дирижёра (from=Filipp-pcloc-dec)
        calls = []
        fake = lambda text, frm: (calls.append((text, frm)) or (True, 4242, None))
        dec = mc.submit_lesson(sc["card"], sc["reply_text"], sc["teacher"], enqueue=fake)
        self.assertEqual("lesson", dec["decision"])
        self.assertTrue(dec["queued"])
        self.assertEqual(1, len(calls))                     # ровно один enqueue — прямого исполнения нет
        task_text, frm = calls[0]
        self.assertEqual(mc.LESSON_TASK_FROM, frm)
        self.assertTrue(lr.is_lesson_task(task_text))

        # 2) ДИФ: дирижёр классифицирует → НАДЗОР → реальная строка-класс в ВРЕМЕННЫЙ чек-лист
        fd, ck_path = tempfile.mkstemp(suffix=".md", text=True)
        os.close(fd)
        with open(ck_path, "w", encoding="utf-8") as f:
            f.write(sc["checklist_seed"])
        self.addCleanup(lambda: os.path.exists(ck_path) and os.remove(ck_path))
        dec2 = lr.handle_lesson_task(task_text,
                                     append_checklist=lambda r, p=ck_path: lr.append_checklist_class(r, p),
                                     append_style=lambda r: self.fail("style не должен звать"))
        self.assertEqual(lr.SUPERVISION, dec2["route"])
        self.assertEqual("done", dec2["status"])
        with open(ck_path, encoding="utf-8") as f:
            diff = f.read()
        for frag in exp["checklist_diff_has"]:
            self.assertIn(frag, diff, frag)                 # реальный диф (новый класс дописан)
        self.assertIn(sc["checklist_seed"].split("\n")[1], diff)     # старое не затёрто
        self.assertIn(exp["commit_paths_rel"].replace("/", os.sep), dec2["commit_paths"])
        self.assertEqual(exp["card_msg_id"], dec2["card_msg_id"])    # координата карточки для реплая

        # 3) ЖИВОЙ РЕПЛЕЙ окна: преамбула СЛЕДУЮЩЕГО тика ревизора собирается из живого чек-листа
        preamble = o._revizor_preamble(ck_path)
        for frag in sc["replay"]["next_gen_has"]:
            self.assertIn(frag, preamble, frag)             # выученный класс долетел до промпта дословно
        base = o._revizor_preamble(os.path.join(tempfile.gettempdir(), "no_such_checklist_step5.md"))
        self.assertNotIn(sc["replay"]["absent_before"], base)       # анти-тавтология: до применения — нет

        # 4) ПОДТВЕРЖДЕНИЕ «урок принят…» — ТОЛЬКО после (симулированного) реального коммита
        ack = lr.ack_after_commit("abc1234", dec2["ack_subject"], dec2["ack_where"],
                                  verify=lambda ref: True)
        self.assertIsNotNone(ack)
        for frag in exp["ack_after_commit_has"]:
            self.assertIn(frag, ack, frag)
        # инвариант: до коммита подтверждения НЕТ
        self.assertIsNone(lr.ack_after_commit("", dec2["ack_subject"], dec2["ack_where"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
