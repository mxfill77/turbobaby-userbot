# -*- coding: utf-8 -*-
"""Регресс СЛЕПКА ОЧЕРЕДИ ПОЛОСЫ ПК + инвариант QSNAP_PC_PURE.

Четыре вещи, ради которых этот файл существует:
  1. ЗАМОК СВЕЖЕСТИ ПОКРЫТ В ОБЕ СТОРОНЫ. Мало проверить, что при отказе моста слепок говорит
     «НЕ СВЕРЕНО»; рядом стои́т кейс, где при живом мосте он называет ЭТО время. Инвариант,
     проверенный только с одной стороны, — это молчание, а не замок. Отдельно проверено самое
     дорогое: свежий штамп `снято:` при несверенном чтении не появляется НИ НА ОДНОЙ дороге.
  2. «ПИШЕМ ПО СМЕНЕ СОСТОЯНИЯ» ПРОВЕРЯЕТСЯ СЧЁТОМ ЗАПИСЕЙ, а не намерением: одна и та же
     очередь, прогнанная пять раз, обязана дать РОВНО одну запись, и каждая настоящая смена
     (взял · сдал · упал · ждёт владельца · встал в очередь) — ровно одну.
  3. ФОРМАТ ФИКСТУР = ЖИВОЙ ФОРМАТ МОСТА (снят 14.08.2026 read-only пробой, lane=pc): ответ несёт
     `ok/items/statuses/lane/action/_status`, строка — `id` СТРОКОЙ ("553", не 553), `created` и
     `updated` вида `2026-08-14T15:30:22.664Z`, `task_text` с ведущей строкой `ultrathink`,
     пустой `result` у открытых. Идеализированной схемы «как удобно тесту» здесь нет ни одной.
  4. ГРАНИЦА ДЕРЖИТСЯ УСТРОЙСТВОМ: ast-разбор доказывает, что чистый слой не умеет ничего, кроме
     арифметики над переданным, а модуль целиком не умеет мутировать очередь. И проверяется, что
     инвариант ЛОВИТ внесённое нарушение.

Запуск — тем же способом, что и весь гейт (способ запуска — часть формата):
    venv\\Scripts\\python.exe -m unittest test_queue_snapshot_pc
"""
import ast
import os
import tempfile
import unittest

import done_judge_pc as dj
import queue_snapshot_pc as q

T0 = 1786721422.0          # 2026-08-14 15:30:22 UTC — время живой пробы (сверено арифметикой)
CFG = {"stale": 900, "window": 86400, "failed_max": 3600, "off": False}


def row(tid, status, updated, text="ultrathink\n\nЦЕЛЬ: слепок очереди своей полосы в мозг.",
        result=""):
    """Строка очереди ДОСЛОВНО в формате моста (снята пробой 14.08.2026)."""
    return {"id": str(tid), "created": "2026-08-14T15:30:22.664Z", "from": "Filipp-328-dev",
            "task_text": text, "status": status, "result": result, "approved_by": "",
            "updated": updated, "lane": "pc"}


def answer(items, statuses=q.OPEN_STATUSES):
    """Конверт моста ДОСЛОВНО: ok/items/statuses/lane/action/_status."""
    return {"ok": True, "items": list(items), "statuses": list(statuses),
            "lane": "pc", "action": "get_pending", "_status": 200}


class Bridge(object):
    """Мост-фикстура. Считает вызовы и умеет ломаться — обе роли нужны разным кейсам."""

    def __init__(self, open_rows=(), failed_rows=(), done_rows=(), broken=False):
        self.open_rows, self.failed_rows = list(open_rows), list(failed_rows)
        self.done_rows, self.broken = list(done_rows), broken
        self.asked = []

    def __call__(self, status):
        self.asked.append(status)
        if self.broken:
            return {"ok": False, "error": "unauthorized", "_status": 401}
        if status in ("failed", "done"):
            return answer(getattr(self, status + "_rows"), [status])
        return answer(self.open_rows)


class Writer(object):
    """Писатель-фикстура: живой мозг в тестах не трогается ни одной дорогой."""

    def __init__(self):
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return {"status": "ok", "doc": "fake"}


class Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.environ.pop, "QSNAP_PC_STATE", None)
        self.state = self.fresh_state()

    def fresh_state(self):
        """Новый пустой файл состояния. Отдельным методом: подтесты обязаны быть изолированы —
        общий файл превратил бы вторую дорогу в «без изменений» и замок проверял бы не то."""
        fd, path = tempfile.mkstemp(prefix="turbobaby_TESTING_qsnap_", suffix=".json")
        os.close(fd)
        os.unlink(path)
        os.environ["QSNAP_PC_STATE"] = path
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        return path

    def tick(self, bridge, writer, now, cfg=None, dry=False):
        return q.tick(get=bridge, writer=writer, now=now, cfg=cfg if cfg else CFG, dry=dry)


# ══════════════════════ 1. ЗАМОК СВЕЖЕСТИ — ОБЕ СТОРОНЫ ════════════════════════════════════
class TestFreshnessLock(Base):
    def test_живой_мост_ставит_время_ЭТОГО_снятия(self):
        w = Writer()
        out = self.tick(Bridge([row(553, "in_progress", "2026-08-14T15:31:36.719Z")]), w, T0)
        self.assertEqual(out["action"], "записал")
        self.assertIn("снято: 2026-08-14 15:30:22 UTC", w.texts[0])
        self.assertNotIn("НЕ СВЕРЕНО", w.texts[0])

    def test_одиночный_промах_моста_НЕ_пишет_ничего(self):
        """Флап моста состоянием не является: ни свежего штампа, ни «не сверено»."""
        w = Writer()
        self.tick(Bridge([row(553, "in_progress", "2026-08-14T15:31:36.719Z")]), w, T0)
        out = self.tick(Bridge(broken=True), w, T0 + 60)
        self.assertEqual(out["action"], "молчим")
        self.assertEqual(len(w.texts), 1, "второй записи быть не должно")

    def test_затянувшийся_промах_говорит_НЕ_СВЕРЕНО_и_называет_последний_верный(self):
        w = Writer()
        self.tick(Bridge([row(553, "in_progress", "2026-08-14T15:31:36.719Z")]), w, T0)
        out = self.tick(Bridge(broken=True), w, T0 + 901)
        self.assertEqual(out["action"], "записал")
        text = w.texts[-1]
        self.assertIn("НЕ СВЕРЕНО", text)
        self.assertIn("последний верный снимок: 2026-08-14 15:30:22 UTC", text)
        self.assertIn("#553", text, "тело последнего верного снимка обязано остаться")

    def test_штамп_снято_НЕ_появляется_ни_на_одной_несверенной_дороге(self):
        """Самое дорогое утверждение файла — проверяем его перебором дорог, а не одной."""
        for name, warm in (("верного снимка не было ни разу", False),
                           ("после верного снимка", True)):
            with self.subTest(дорога=name):
                self.fresh_state()
                w = Writer()
                if warm:
                    self.tick(Bridge([row(553, "new", "2026-08-14T15:30:22.664Z")]), w, T0)
                out = self.tick(Bridge(broken=True), w, T0 + 100000)
                self.assertEqual(out["action"], "записал")
                self.assertNotIn("снято: ", w.texts[-1])
                self.assertIn("НЕ СВЕРЕНО", w.texts[-1])

    def test_без_верного_снимка_тело_НЕ_выдумывается(self):
        w = Writer()
        out = self.tick(Bridge(broken=True), w, T0)
        self.assertEqual(out["action"], "записал")
        self.assertIn("верного снимка не случилось ни разу", w.texts[0])
        self.assertIn("последний верный снимок: НЕ БЫЛО НИ РАЗУ", w.texts[0])

    def test_мост_не_подтвердил_статусы_снимку_не_верим(self):
        """Старый мост CSV не разбирает и отдаёт НЕ ТО, что спросили: половина снимка хуже нуля."""
        got = q.read_open(lambda st: {"ok": True, "items": [row(1, "new", None)]})
        self.assertFalse(got["ok"])
        self.assertIn("не подтвердил список статусов", got["err"])

    def test_возвращение_моста_снова_даёт_свежий_штамп(self):
        w = Writer()
        rows = [row(553, "in_progress", "2026-08-14T15:31:36.719Z")]
        self.tick(Bridge(rows), w, T0)
        self.tick(Bridge(broken=True), w, T0 + 901)
        out = self.tick(Bridge(rows), w, T0 + 1800)
        self.assertEqual(out["action"], "записал")
        self.assertIn("снято: 2026-08-14 16:00:22 UTC", w.texts[-1])
        self.assertNotIn("НЕ СВЕРЕНО", w.texts[-1])

    def test_на_лежащем_мосте_вторая_запись_не_идёт(self):
        """Долбёжка канала: «не сверено» пишется ОДИН раз на обрыв, а не каждый оборот."""
        w = Writer()
        self.tick(Bridge([row(553, "new", "2026-08-14T15:30:22.664Z")]), w, T0)
        for step in range(1, 6):
            self.tick(Bridge(broken=True), w, T0 + 900 + step * 60)
        self.assertEqual(len(w.texts), 2, "верный снимок + одно «не сверено»")


# ══════════════════════ 2. ПИШЕМ ПО СМЕНЕ СОСТОЯНИЯ, А НЕ ПО ТИКУ ══════════════════════════
class TestWriteOnChange(Base):
    def test_пять_оборотов_без_смен_дают_одну_запись(self):
        w = Writer()
        rows = [row(553, "in_progress", "2026-08-14T15:31:36.719Z")]
        for step in range(5):
            self.tick(Bridge(rows), w, T0 + step * 60)
        self.assertEqual(len(w.texts), 1)

    def test_возраст_строки_записью_не_является(self):
        """Возраст растёт каждый оборот; будь он в подписи — писали бы каждый тик."""
        rows = [row(553, "in_progress", "2026-08-14T15:31:36.719Z")]
        view = {"ok": True, "at": T0, "rows": q._rows_from(rows, "new"), "closed": {},
                "failed_at": T0}
        later = dict(view, at=T0 + 9999)
        self.assertEqual(q.signature(view), q.signature(later))
        self.assertNotEqual(q.render(view, T0), q.render(later, T0 + 9999))

    def test_каждая_смена_состояния_даёт_ровно_одну_запись(self):
        w = Writer()
        new = row(553, "new", "2026-08-14T15:30:22.664Z")
        taken = row(553, "in_progress", "2026-08-14T15:31:36.719Z")
        self.tick(Bridge([new]), w, T0)                        # встал в очередь
        self.assertEqual(len(w.texts), 1)
        self.tick(Bridge([taken]), w, T0 + 60)                 # взял
        self.assertEqual(len(w.texts), 2)
        self.tick(Bridge([taken]), w, T0 + 120)                # ничего не менялось
        self.assertEqual(len(w.texts), 2)
        self.tick(Bridge([]), w, T0 + 180)                     # сдал
        self.assertEqual(len(w.texts), 3)
        self.assertIn("сдано: #553", w.texts[-1])

    def test_ждёт_владельца_это_смена_состояния(self):
        w = Writer()
        self.tick(Bridge([row(553, "in_progress", "2026-08-14T15:31:36.719Z")]), w, T0)
        self.tick(Bridge([row(553, "needs_approval", "2026-08-14T15:40:00.000Z")]), w, T0 + 60)
        self.assertEqual(len(w.texts), 2)
        self.assertIn("ЖДЁТ ВЛАДЕЛЬЦА (1)", w.texts[-1])

    def test_сухой_прогон_не_пишет_и_не_двигает_состояние(self):
        w = Writer()
        out = self.tick(Bridge([row(553, "new", "2026-08-14T15:30:22.664Z")]), w, T0, dry=True)
        self.assertEqual(out["action"], "написал бы")
        self.assertEqual(w.texts, [])
        self.assertFalse(os.path.exists(self.state), "сухой прогон состояние не пишет")

    def test_откат_ручкой_отключает_ветку_целиком(self):
        w = Writer()
        b = Bridge([row(553, "new", "2026-08-14T15:30:22.664Z")])
        out = self.tick(b, w, T0, cfg=dict(CFG, off=True))
        self.assertEqual(out["action"], "выключено")
        self.assertEqual(w.texts, [])
        self.assertEqual(b.asked, [], "при откате мост не спрашивается вовсе")


# ══════════════════════ 3. РАЗРЕЗ СЛЕПКА: ЧТО ИМЕННО ВИДИТ ШТАБ ════════════════════════════
class TestSnapshotBody(Base):
    def test_в_работе_с_номером_и_первой_ЗНАЧАЩЕЙ_строкой_цели(self):
        w = Writer()
        self.tick(Bridge([row(553, "in_progress", "2026-08-14T15:31:36.719Z")]), w, T0)
        text = w.texts[0]
        self.assertIn("В РАБОТЕ (1):", text)
        self.assertIn("#553", text)
        self.assertIn("ЦЕЛЬ: слепок очереди своей полосы в мозг.", text)

    def test_ultrathink_целью_не_называется(self):
        """Замер репо: ключевое слово стояло в 10 заданиях из 11 — сигнала в нём нет."""
        self.assertEqual(q.goal_line("ultrathink\n\nЦЕЛЬ: разобрать обрыв"), "ЦЕЛЬ: разобрать обрыв")
        self.assertEqual(q.goal_line("ULTRATHINK.\nсделай X"), "сделай X")
        self.assertEqual(q.goal_line("ultrathink"), "ultrathink", "выдумывать цель нельзя")
        self.assertEqual(q.goal_line(None), "")

    def test_упавшее_названо_с_причиной(self):
        w = Writer()
        failed = row(505, "failed", "2026-08-14T15:20:00.000Z",
                     text="ultrathink\n\nЦЕЛЬ: сторож судит ПРОДУКТ работы.",
                     result="⏱ НЕ ЗАКРЫТА, но В ОКНЕ ЗАДАЧИ ЕСТЬ РАБОТА [причина=approval_timeout]")
        self.tick(Bridge([], [failed]), w, T0)
        text = w.texts[0]
        self.assertIn("упало:", text)
        self.assertIn("#505", text)
        self.assertIn("approval_timeout", text)

    def test_пустая_очередь_говорит_словами_а_не_пустотой(self):
        w = Writer()
        self.tick(Bridge([]), w, T0)
        text = w.texts[0]
        for words in ("никто не работает", "владельца никто не ждёт", "очередь пуста",
                      "за сутки не закрылось ничего"):
            self.assertIn(words, text)

    def test_исход_не_сверен_пока_упавшие_не_прочитаны(self):
        """Третий исход: ушла из открытых, а упавшие не прочитались → НЕ «сдано»."""
        closed = q.merge_closed({"553": {"goal": "ЦЕЛЬ: X"}}, {}, {}, T0, 86400)
        self.assertIsNone(closed["553"]["outcome"])
        text = q.render_body([], closed, None, T0)
        self.assertIn("исход не сверен: #553", text)
        self.assertIn("исход не сверен 1", text)
        self.assertNotIn("сдано: #553", text)

    def test_ушедшая_и_не_найденная_среди_упавших_это_сдано(self):
        closed = q.merge_closed({"553": {"goal": "ЦЕЛЬ: X"}}, {}, {}, T0, 86400)
        closed = q.apply_failed(closed, [], T0, 86400)
        self.assertEqual(closed["553"]["outcome"], "done")

    def test_строка_старше_суток_из_слепка_уходит(self):
        old = {"553": {"id": "553", "at": T0 - 90000, "goal": "ЦЕЛЬ: X", "outcome": "done"}}
        self.assertEqual(q.merge_closed({}, {}, old, T0, 86400), {})

    def test_сдано_за_сутки_засевается_один_раз_а_не_каждый_оборот(self):
        """Дорогое чтение (`done` — 26.8с против 2.9с у `failed`, замер 14.08) зовётся однажды."""
        w = Writer()
        done = row(551, "done", "2026-08-14T14:00:00.000Z", text="ultrathink\n\nЦЕЛЬ: прошлая")
        b = Bridge([row(553, "in_progress", "2026-08-14T15:31:36.719Z")], done_rows=[done])
        self.tick(b, w, T0)
        self.assertIn("сдано: #551", w.texts[0])
        self.assertEqual(b.asked.count("done"), 1)
        b2 = Bridge([row(553, "in_progress", "2026-08-14T15:31:36.719Z")], done_rows=[done])
        self.tick(b2, w, T0 + 60)
        self.assertEqual(b2.asked.count("done"), 0, "второй раз дорогое чтение не зовётся")

    def test_засев_старше_суток_не_берётся(self):
        old = q._rows_from([row(400, "done", "2026-08-12T00:00:00.000Z")], "done")
        self.assertEqual(q.seed_done({}, old, T0, 86400), {})

    def test_подвал_называет_асимметрию_половин(self):
        text = q.render_body([], {}, T0, T0)
        self.assertIn("эта половина за сутки ПОЛНА", text)
        self.assertIn("по наблюдению писателя", text)


# ══════════════════════ 3-бис. ОТКАЗ ВЛАДЕЛЬЦА — ОТДЕЛЬНЫЙ ИСХОД ═══════════════════════════
# Живой формат отказа снят read-only пробой 15.08.2026 (lane=pc): статуса `rejected` в очереди
# НЕТ (`get_pending("rejected")` → ok=True, items=0), отказ лежит в `failed`, а отличает его
# ровно префикс `result` — «отклонено Филиппом (кнопка)» / «отклонено Филиппом (ответ с ПК: …)».
# 23 строки из 55 живых `failed` — именно такие. Идеализированного «status: rejected» здесь нет.
def rejected_row(tid, updated, why="(кнопка)", goal="ЦЕЛЬ: снять слой ожиданий"):
    return row(tid, "failed", updated, text="ultrathink\n\n" + goal,
               result=(q.REJECT_MARK + " " + why).strip())


class TestOwnerRejection(Base):
    def test_маркер_отказа_ДОСЛОВНО_тот_что_пишет_демон(self):
        """Слепок читает НЕ статус, а маркер, поэтому расхождение с демоном сделало бы ветку
        вечно пустой ПРИ ЖИВОМ КЛАССЕ — молча. Читаем исходник, а не импортируем: импорт демона
        вешает хендлер на боевой лог (закрытый класс «тесты сорят в боевой лог»)."""
        import re
        with open(os.path.join(os.path.dirname(os.path.abspath(q.__file__)),
                               "pc_orchestrator.py"), encoding="utf-8") as f:
            src = f.read()
        got = re.search(r'^_REJECT_PREFIX\s*=\s*"([^"]+)"', src, re.M)
        self.assertIsNotNone(got, "литерал отказа исчез из демона — слепок обязан упасть, а не врать")
        self.assertEqual(q.REJECT_MARK, got.group(1))

    def test_отказ_владельца_назван_отдельно_а_не_упало(self):
        w = Writer()
        self.tick(Bridge([], [rejected_row(555, "2026-08-14T16:40:00.000Z")]), w, T0)
        text = w.texts[0]
        self.assertIn("ОТКЛОНЕНО ВЛАДЕЛЬЦЕМ", text)
        self.assertIn("#555", text)
        self.assertIn("отклонено владельцем 1", text)
        self.assertIn("упало 0", text, "решение человека не смеет считаться сбоем")
        self.assertNotIn("  упало:", text)

    def test_переотправка_запрещена_словами_а_не_подразумевается(self):
        """Штаб читает мозг САМ: разница «сбой ↔ решение» обязана быть В ТЕКСТЕ, не в голове."""
        text = q.render_body([], {"555": {"id": "555", "at": T0, "goal": "ЦЕЛЬ: X",
                                          "outcome": "rejected", "why": q.REJECT_MARK}}, T0, T0)
        self.assertIn("переотправке не подлежит", text)

    def test_неизвестность_судьи_падением_НЕ_называется(self):
        """ТРЕТИЙ ИСХОД (11.09.2026). Живой заход 245 сделал работу, положил коммит 22f57a2 и
        артефакт по названному адресу — и лёг в слепок «упало», потому что судья не смог его
        ПРОЧИТАТЬ. Штаб читает слепок и видел провал там, где провала не было."""
        self.assertEqual(q.JUDGE_UNKNOWN_WORD, dj.UNKNOWN, "слово судьи разошлось со слепком")
        why = "V0: UNKNOWN / sensitive_content по адресу «docs/artifacts/…-1009.md»"
        text = dj.fail_result({"verdict": dj.UNKNOWN, "reason": why}, "отчёт исполнителя")
        self.assertEqual(q.outcome_of(text), q.OUT_UNKNOWN)
        self.assertEqual(q.judge_reason(text), why, "причина судьи не читается обратно")

    def test_не_доказано_остаётся_падением(self):
        """ОТРИЦАТЕЛЬНЫЙ 1: прибор ПРОЧИТАЛ и ответил «нет» — это по-прежнему `failed`.

        Отдельный разрез положен ровно тому исходу, где о работе не сказано ничего; раздать его
        всем недоказанным значило бы убрать из слепка падения вовсе."""
        text = dj.fail_result({"verdict": dj.UNPROVEN, "reason": "по адресу ПУСТО"}, "отчёт")
        self.assertEqual(q.outcome_of(text), q.OUT_FAILED)

    def test_безпричинная_неизвестность_льготы_не_получает(self):
        """ОТРИЦАТЕЛЬНЫЙ 3: «неизвестно» без названной причины в слепок неизвестностью не идёт.

        Замок стои́т у источника — маркер выбирает `done_judge_pc.accepted`, — поэтому подложить
        слепку безпричинную неизвестность нечем."""
        text = dj.fail_result({"verdict": dj.UNKNOWN, "reason": ""}, "отчёт")
        self.assertEqual(q.outcome_of(text), q.OUT_FAILED)

    def test_отказ_владельца_сильнее_маркера_судьи(self):
        """Порядок разбора назван: отказ человека не смеет получить чужое имя."""
        why = "V0: UNKNOWN / sensitive_content по адресу «a.md»"
        judged = dj.fail_result({"verdict": dj.UNKNOWN, "reason": why}, "отчёт")
        self.assertEqual(q.outcome_of(q.REJECT_MARK + " (кнопка) " + judged), "rejected")

    def test_неизвестность_видна_отдельной_строкой_и_числом(self):
        """Штаб читает мозг САМ: разница «упало ↔ неизвестно» обязана быть В ТЕКСТЕ."""
        why = "V0: UNKNOWN / sensitive_content по адресу «a.md»"
        text = q.render_body([], {"245": {"id": "245", "at": T0, "goal": "ЦЕЛЬ: закрыть ключ",
                                          "outcome": q.OUT_UNKNOWN,
                                          "why": dj.fail_result({"verdict": dj.UNKNOWN,
                                                                 "reason": why}, "отчёт")}},
                             T0, T0)
        self.assertIn("НЕИЗВЕСТНО 1", text)
        self.assertIn("упало 0", text, "неизвестность посчитана падением")
        self.assertIn("про СУДЬЮ, а не про работу", text)
        self.assertIn("sensitive_content", text, "причина судьи не доехала до Штаба")

    def test_настоящее_падение_отказом_НЕ_называется(self):
        self.assertEqual(q.outcome_of("⏱ таймаут 45 мин"), "failed")
        self.assertEqual(q.outcome_of("подтверждение не получено за 46 мин"), "failed",
                         "владелец не ответил — это НЕ отказ владельца")
        self.assertEqual(q.outcome_of(""), "failed")
        self.assertEqual(q.outcome_of(None), "failed")
        self.assertEqual(q.outcome_of(q.REJECT_MARK + " (кнопка)"), "rejected")
        self.assertEqual(q.outcome_of("  " + q.REJECT_MARK), "rejected", "пробелы решают исход?")

    def test_пояснение_отказа_видно_а_маркер_не_повторяется(self):
        self.assertEqual(q.reject_words(q.REJECT_MARK + " (ответ с ПК: Filipp/console): не нужно"),
                         "(ответ с ПК: Filipp/console): не нужно")
        self.assertEqual(q.reject_words(q.REJECT_MARK), "пояснения не оставлено")

    def test_отказ_мимо_наблюдения_всё_равно_виден(self):
        """Демон стоял, владелец нажал «нет» — строка не была открытой ни на одном обороте."""
        w = Writer()
        self.tick(Bridge([], [rejected_row(416, "2026-08-14T16:00:00.000Z")]), w, T0)
        self.assertIn("#416", w.texts[0])
        self.assertIn("отклонено владельцем 1", w.texts[0])

    def test_разделение_исхода_НЕ_добавляет_записей_в_сутки(self):
        """Цена разреза числом: исход поменял ИМЯ, а не количество смен. Отказ и падение дают
        по одной записи на закрытие — сколько давали до разреза."""
        counts = {}
        for kind, closing in (("отказ", rejected_row(555, "2026-08-14T16:40:00.000Z")),
                              ("падение", row(555, "failed", "2026-08-14T16:40:00.000Z",
                                              result="⏱ таймаут"))):
            self.fresh_state()
            w = Writer()
            live = row(555, "in_progress", "2026-08-14T15:31:36.719Z")
            self.tick(Bridge([live]), w, T0)                       # взял
            self.tick(Bridge([live]), w, T0 + 60)                  # тот же виток — записи нет
            self.tick(Bridge([], [closing]), w, T0 + 120)          # закрыл
            self.tick(Bridge([], [closing]), w, T0 + 180)          # снова тот же — записи нет
            counts[kind] = len(w.texts)
        self.assertEqual(counts["отказ"], counts["падение"])
        self.assertEqual(counts["отказ"], 2, "две смены — две записи, не больше")

    def test_отказ_и_падение_в_одних_сутках_не_смешиваются(self):
        w = Writer()
        self.tick(Bridge([], [rejected_row(555, "2026-08-14T16:40:00.000Z"),
                              row(505, "failed", "2026-08-14T16:00:00.000Z",
                                  text="ultrathink\n\nЦЕЛЬ: сторож судит ПРОДУКТ",
                                  result="⏱ НЕ ЗАКРЫТА [причина=approval_timeout]")]), w, T0)
        text = w.texts[0]
        self.assertIn("отклонено владельцем 1", text)
        self.assertIn("упало 1", text)
        self.assertIn("approval_timeout", text)
        self.assertLess(text.index("  упало:"), text.index("ОТКЛОНЕНО ВЛАДЕЛЬЦЕМ"))


# ══════════════════════ 4. ИНВАРИАНТ QSNAP_PC_PURE ═════════════════════════════════════════
# Граница держится отсутствием инструментов, а не докстрингом (зеркало EXPECT_PC_PURE полосы).
_PURE = ("one_line", "goal_line", "fmt_ts", "age_min", "as_float", "_age_words", "_section",
         "render_body", "_row_key", "_closed_key", "render", "signature", "merge_closed",
         "apply_failed", "outcome_of", "reject_words")
_FORBIDDEN_CALLS = frozenset(("open", "exec", "eval", "compile", "__import__", "input", "print"))
_FORBIDDEN_ROOTS = frozenset(("os", "sys", "subprocess", "socket", "urllib", "time", "shutil",
                              "pathlib", "tempfile", "sqlite3", "brain_writer", "pc_orchestrator",
                              "bridge_http", "logging"))
# Слова, которыми в этой системе МЕНЯЮТ мир. Слепок только смотрит: ни одного из них во ВСЁМ
# модуле быть не может — ни как вызова, ни как имени, ни как строки действия моста.
_FORBIDDEN_ANYWHERE = frozenset(("claim_task", "complete_task", "enqueue_task", "task_heartbeat",
                                 "approve_task", "set_needs_approval", "taskkill", "schtasks",
                                 "rmtree", "unlink", "kill"))


def _tree():
    with open(q.__file__, encoding="utf-8") as f:
        return ast.parse(f.read())


class TestPurityInvariant(unittest.TestCase):
    def test_чистый_слой_не_умеет_ходить_в_мир(self):
        found = {n.name: n for n in ast.walk(_tree()) if isinstance(n, ast.FunctionDef)}
        for name in _PURE:
            self.assertIn(name, found, "чистая функция %s исчезла — инвариант обязан упасть" % name)
            for node in ast.walk(found[name]):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, _FORBIDDEN_CALLS,
                                     "%s зовёт %s" % (name, node.func.id))
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    self.assertNotIn(node.value.id, _FORBIDDEN_ROOTS,
                                     "%s трогает %s" % (name, node.value.id))

    def test_модуль_целиком_не_умеет_мутировать_очередь(self):
        with open(q.__file__, encoding="utf-8") as f:
            src = f.read()
        for word in _FORBIDDEN_ANYWHERE:
            self.assertNotIn(word, src, "слово мутации %s не смеет жить в слепке" % word)

    def test_инвариант_ловит_внесённое_нарушение(self):
        """Замок без этой проверки — обещание: убеждаемся, что он ловит подложенное."""
        bad = ast.parse("def render(v, now):\n    return open('x').read()\n")
        hit = []
        for node in ast.walk(bad):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in _FORBIDDEN_CALLS:
                    hit.append(node.func.id)
        self.assertEqual(hit, ["open"])

    def test_флаг_тестовых_логов_возвращается_как_был(self):
        """Оставленный флаг заставил бы brain_writer молча отказать в живой записи."""
        key = "TURBOBABY_TEST_LOGS"
        had, prev = key in os.environ, os.environ.get(key)
        if had:
            self.addCleanup(os.environ.__setitem__, key, prev)
        os.environ.pop(key, None)
        seen = {}

        def fake_import():
            seen["во_время"] = os.environ.get(key)
            raise ImportError("демон здесь не нужен")

        with self.assertRaises(ImportError):
            q._guard_test_logs(fake_import)
        self.assertEqual(seen["во_время"], "1")
        self.assertIsNone(os.environ.get(key), "флаг обязан быть снят даже при падении импорта")


if __name__ == "__main__":
    unittest.main()
