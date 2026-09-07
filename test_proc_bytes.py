# -*- coding: utf-8 -*-
"""
test_proc_bytes.py — голдены прибора «какие байты произвели ЭТОТ ответ» и ТРИ ОТРИЦАТЕЛЬНЫХ
ТЕСТА, без которых прибор не принят.

ПОЧЕМУ ЖИВЫЕ ИМПОРТЫ, А НЕ МОКИ. Правило-класс репозитория: мок обязан копировать живой формат.
Здесь «внешний ответ» — это САМА МАШИНА ИМПОРТА Python: подделать её так, чтобы тест что-то
доказал, невозможно. Поэтому двери открываются НАСТОЯЩИМ `import` внутри функции — той самой
формы, что живёт в 13 боевых рёбрах (`moderation_core.py:385`, `price_gate.py:180`, …), — а
файлы данных читаются НАСТОЯЩИМ `open`.

ГДЕ ЖИВУТ ПРОБНЫЕ ФАЙЛЫ. `tmp/proc_bytes_probe/` внутри репозитория (аудит-ловушка и искатель
судят только своё дерево, вне его прибор молчит по устройству). Файлы ПЕРЕПИСЫВАЮТСЯ, а не
удаляются: полоса не убирает за собой.

ТРИ ОТРИЦАТЕЛЬНЫХ ТЕСТА — приёмка, а не украшение (класс §6 артефакта 07.09):
  ОТ-1  зелёный рестарт: PID новый, коммит записан, а ленивая дверь не открывалась → «нет в
        отпечатке», и итог НЕ зелёный.
  ОТ-2  отпечаток со старта: совпал байт-в-байт, а через час дверь прочитала другую редакцию →
        отпечаток СТАРШЕ ответа отвергается отдельным исходом; свежий несёт НОВЫЕ байты.
  ОТ-3  все модули совпали, а текст ответа другой: ответ произвели данные вне git. Прибор,
        считающий ТОЛЬКО модули, здесь зеленеет — и тест это ПОКАЗЫВАЕТ, а не подразумевает.
"""

import importlib
import json
import os
import sys
import tempfile
import time
import unittest

import proc_bytes as pb

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "tmp", "proc_bytes_probe")


def setUpModule():
    os.makedirs(PROBE, exist_ok=True)
    if PROBE not in sys.path:
        sys.path.insert(0, PROBE)
    pb.install(do_backfill=False)


def write_probe(name, body):
    """Пробный модуль/файл данных. Перезапись, не удаление.

    Пишем БАЙТАМИ: текстовый режим на Windows подменил бы `\\n` на `\\r\\n`, и голден сравнивал бы
    хеш прибора с хешем того, чего на диске нет. Мок обязан копировать живой формат — здесь живой
    формат это САМ ФАЙЛ."""
    p = os.path.join(PROBE, name)
    with open(p, "wb") as f:
        f.write(body.encode("utf-8"))
    return p


def forget(name):
    """Убрать имя из `sys.modules` — это словарь процесса, а не файл на диске."""
    sys.modules.pop(name, None)
    importlib.invalidate_caches()


class Base(unittest.TestCase):
    def setUp(self):
        pb.reset()
        pb.install(do_backfill=False)

    def tearDown(self):
        pb.disarm()


# ─────────────────────────── РЕГИСТРАТОР КОДА ───────────────────────────────────────────────────

class TestCodeRecorder(Base):

    def test_lazy_import_inside_function_is_recorded_with_bytes_and_moment(self):
        """ГЛАВНОЕ СВОЙСТВО (п.4 задания): момент открытия ленивой двери записан.

        Форма ребра — дословно боевая: `def f(): import X` в модуле, который УЖЕ в процессе."""
        body = "# lazy door probe\nMARK = 'v1'\n"
        write_probe("pbprobe_lazy.py", body)
        forget("pbprobe_lazy")

        self.assertNotIn("pbprobe_lazy", pb.ledger().modules,
                         "до вызова дверь не открыта — записи быть не должно")

        def door():                      # ровно та форма, что стои́т в 13 боевых рёбрах
            import pbprobe_lazy
            return pbprobe_lazy.MARK

        t0 = time.time()
        self.assertEqual(door(), "v1")

        rec = pb.ledger().modules.get("pbprobe_lazy")
        self.assertIsNotNone(rec, "прибор обязан видеть ленивый импорт")
        self.assertEqual(rec["путь"], "tmp/proc_bytes_probe/pbprobe_lazy.py")
        self.assertEqual(rec["sha"], pb.sha_bytes(body.encode("utf-8")),
                         "хеш обязан быть от байтов, которые процесс скомпилировал")
        self.assertIn(rec["как"], (pb.VIA_READ, pb.VIA_PYC))
        self.assertGreaterEqual(rec["ts"], t0 - 1,
                                "записан МОМЕНТ открытия двери, а не время старта процесса")

    def test_module_outside_repo_is_not_recorded(self):
        """Чужое дерево — не предмет: stdlib и venv в отпечаток не лезут."""
        import base64                    # noqa: F401  (stdlib, вне репозитория)
        names = list(pb.ledger().modules.keys())
        self.assertNotIn("base64", names)

    def test_reload_replaces_bytes_and_moment(self):
        """Та же дверь, открытая второй раз, несёт НОВЫЕ байты. Старый хеш здесь был бы ложью."""
        write_probe("pbprobe_twice.py", "VALUE = 1\n")
        forget("pbprobe_twice")
        import pbprobe_twice              # noqa: F401
        first = dict(pb.ledger().modules["pbprobe_twice"])

        write_probe("pbprobe_twice.py", "VALUE = 22222\n# другая длина, другой mtime\n")
        forget("pbprobe_twice")
        import pbprobe_twice              # noqa: F401,F811
        second = pb.ledger().modules["pbprobe_twice"]

        self.assertNotEqual(first["sha"], second["sha"])
        self.assertGreater(second["seq"], first["seq"])

    def test_backfill_is_marked_weak_and_verify_calls_it_unverified(self):
        """Модуль, вошедший ДО прибора, честно зовётся `не проверено`, а не «совпало».

        Если бы добор выглядел сильным, прибор врал бы ровно тем способом, от которого лечит:
        хеш диска выдавался бы за байты процесса."""
        pb.reset()
        pb.install(do_backfill=True)
        recs = [r for r in pb.ledger().modules.values() if r["как"] == pb.VIA_BACKFILL]
        self.assertTrue(recs, "сам proc_bytes и тест уже в процессе — доборные записи обязаны быть")
        rec = next(r for r in recs if r["имя"] == "proc_bytes")
        fp = pb.fingerprint(answer="A")
        self.assertIn("proc_bytes", fp["слабых"])
        # Ожидание строим по САМОМУ отпечатку: все хеши совпадают до последнего знака —
        # и всё равно зелёного быть не должно, потому что байты взяты с диска, а не у процесса.
        expect = {r["путь"]: r["sha"] for r in fp["модули"]}
        res = pb.verify(fp, expect, strict=True)
        self.assertEqual(res["по_именам"][rec["путь"]], pb.V_WEAK)
        self.assertEqual(res["итог"], pb.PARTIAL,
                         "совпавший хеш при слабом провенансе — это НЕ зелёный")
        loose = pb.verify(fp, expect, strict=False)
        self.assertEqual(loose["по_именам"][rec["путь"]], pb.V_MATCH)
        self.assertEqual(loose["итог"], pb.OK)


# ─────────────────────────── РЕГИСТРАТОР ДАННЫХ ─────────────────────────────────────────────────

class TestDataRecorder(Base):

    def test_read_of_repo_data_file_is_recorded_without_touching_call_site(self):
        """Аудит-ловушка видит чтение БЕЗ единой правки на месте вызова — это и делает
        достижимыми 10 путей разряда B, разбросанных по семи файлам."""
        body = '{"цена": 1}\n'
        p = write_probe("pbprobe_data.json", body)
        with open(p, "r", encoding="utf-8") as f:
            f.read()
        rec = pb.ledger().data.get("tmp/proc_bytes_probe/pbprobe_data.json")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["sha"], pb.sha_bytes(body.encode("utf-8")))

    def test_hot_file_changed_between_answers_shows_new_hash(self):
        """Горячий файл, сменившийся между двумя ответами, обязан дать ДРУГОЙ хеш — иначе
        `price_source.json` редакции 06.09 был бы неотличим от вчерашней."""
        p = write_probe("pbprobe_hot.json", '{"v": 1}\n')
        with open(p, "r", encoding="utf-8") as f:
            f.read()
        first = pb.ledger().data["tmp/proc_bytes_probe/pbprobe_hot.json"]["sha"]
        time.sleep(0.01)
        write_probe("pbprobe_hot.json", '{"v": 2, "иная длина": true}\n')
        with open(p, "r", encoding="utf-8") as f:
            f.read()
        second = pb.ledger().data["tmp/proc_bytes_probe/pbprobe_hot.json"]["sha"]
        self.assertNotEqual(first, second)

    def test_write_is_not_a_read(self):
        """`playbook.md` бот и читает, и ПИШЕТ. Запись входом ответа не является."""
        p = os.path.join(PROBE, "pbprobe_written.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# запись, а не чтение\n")
        self.assertNotIn("tmp/proc_bytes_probe/pbprobe_written.md", pb.ledger().data)

    def test_extension_allowlist_is_positive_not_a_denylist(self):
        """Перечень расширений ПОЛОЖИТЕЛЬНЫЙ: файл с неназванным расширением не попадает в
        отпечаток ни одной веткой. Так секретам туда не попасть, не называя их в коде."""
        p = write_probe("pbprobe_secretish.cfgx", "k=v\n")
        with open(p, "r", encoding="utf-8") as f:
            f.read()
        self.assertNotIn("tmp/proc_bytes_probe/pbprobe_secretish.cfgx", pb.ledger().data)
        self.assertNotIn(".cfgx", pb.DATA_EXT)

    def test_outside_repo_is_silent(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            f.write("{}")
            alien = f.name
        with open(alien, "r", encoding="utf-8") as f:
            f.read()
        self.assertFalse([k for k in pb.ledger().data if os.path.basename(alien) in k])

    def test_read_mode_discriminator(self):
        self.assertTrue(pb._is_read_mode("r", None))
        self.assertTrue(pb._is_read_mode("rb", None))
        self.assertFalse(pb._is_read_mode("w", None))
        self.assertFalse(pb._is_read_mode("a", None))
        self.assertFalse(pb._is_read_mode("r+", None))
        self.assertTrue(pb._is_read_mode(None, os.O_RDONLY))
        self.assertFalse(pb._is_read_mode(None, os.O_WRONLY))


# ─────────────────────────── ОТПЕЧАТОК И СТРОКА НАРУЖУ ──────────────────────────────────────────

class TestFingerprint(Base):

    def test_line_is_greppable_and_carries_answer_id(self):
        fp = pb.fingerprint(answer="msg-4242")
        s = pb.line(fp)
        self.assertTrue(s.startswith(pb.LINE_TAG))
        self.assertIn("ответ=msg-4242", s)
        self.assertIn("digest=", s)

    def test_digest_changes_with_one_byte(self):
        a = [{"имя": "m", "путь": "m.py", "sha": "aa"}]
        b = [{"имя": "m", "путь": "m.py", "sha": "ab"}]
        self.assertNotEqual(pb.digest_of(a, []), pb.digest_of(b, []))
        self.assertEqual(pb.digest_of(a, []), pb.digest_of(list(a), []))

    def test_doors_since_previous_answer_are_named(self):
        """Двери, открывшиеся МЕЖДУ двумя ответами, названы поимённо — иначе «прибор видит
        ленивую дверь» осталось бы словами."""
        write_probe("pbprobe_between.py", "X = 1\n")
        forget("pbprobe_between")
        pb.fingerprint(answer="первый", mark=True)

        def door():
            import pbprobe_between
            return pbprobe_between.X
        door()

        fp2 = pb.fingerprint(answer="второй", mark=True)
        self.assertIn("pbprobe_between", fp2["двери"]["код"])
        fp3 = pb.fingerprint(answer="третий", mark=True)
        self.assertNotIn("pbprobe_between", fp3["двери"]["код"],
                         "к следующему ответу дверь уже не новая")

    def test_emit_writes_json_and_log_line_into_test_sandbox(self):
        """Боевой `proc_bytes.jsonl` тестом не трогается: адрес идёт через `log_setup.state_path`."""
        path = pb.ledger_path()
        self.assertNotEqual(os.path.dirname(os.path.abspath(path)), HERE,
                            "под тестом журнал отпечатков обязан жить вне репозитория")
        said = []

        class Log(object):
            def info(self, s):
                said.append(s)

        fp, text = pb.emit(answer="e-1", logger=Log())
        self.assertEqual(said, [text])
        back = pb.read_last(answer="e-1")
        self.assertIsNotNone(back)
        self.assertEqual(back["digest"], fp["digest"])
        json.dumps(back)          # запись обязана быть читаемой обратно целиком


# ─────────────────────────── ОТРИЦАТЕЛЬНЫЕ ТЕСТЫ — ПРИЁМКА ──────────────────────────────────────

class TestNegativeOT1(Base):
    """ОТ-1. Зелёный рестарт: признак верен, результата нет.

    Бот поднят на коммите X, PID новый, `child_raise_commit.json` записал X, баннер в логе.
    Но дверь A4 за заход не открывалась — 59 имён в процесс не вошли ВОВСЕ. Прибор обязан
    ответить «нет в отпечатке», а не «совпало»."""

    def test_unopened_door_is_absent_not_matched(self):
        started = [{"имя": "suggest", "путь": "suggest.py", "sha": "s1", "как": pb.VIA_READ},
                   {"имя": "price_gate", "путь": "price_gate.py", "sha": "g1",
                    "как": pb.VIA_READ}]
        fp = {"v": 1, "ответ": "после рестарта", "ts": time.time(), "снят": "-",
              "модули": started, "данные": [], "слабых": [],
              "счёт": {"модулей": 2, "данных": 0, "слабых": 0,
                       "новых_модулей": 2, "новых_данных": 0},
              "digest": "x"}
        expect = {"suggest.py": "s1", "price_gate.py": "g1",
                  "pc_orchestrator.py": "o1", "queue_snapshot_pc.py": "q1"}

        res = pb.verify(fp, expect)
        self.assertEqual(res["по_именам"]["pc_orchestrator.py"], pb.V_ABSENT)
        self.assertEqual(res["по_именам"]["queue_snapshot_pc.py"], pb.V_ABSENT)
        self.assertEqual(res["по_именам"]["suggest.py"], pb.V_MATCH)
        self.assertNotEqual(res["итог"], pb.OK,
                            "«перезапущен на X» — верный ПРИЗНАК, но не результат")
        self.assertEqual(res["итог"], pb.PARTIAL)
        self.assertEqual(res["разрез"][pb.V_ABSENT], 2)


class TestNegativeOT2(Base):
    """ОТ-2. Отпечаток, снятый НА СТАРТЕ: совпал байт-в-байт, а через час дверь прочитала
    редакцию суток новее. Два замка, и оба обязаны сработать."""

    def test_snapshot_older_than_answer_is_rejected_outright(self):
        answer_at = time.time()
        fp = {"v": 1, "ответ": "ответ в 10:00", "ts": answer_at - 3600, "снят": "09:00",
              "модули": [{"имя": "m", "путь": "m.py", "sha": "a", "как": pb.VIA_READ}],
              "данные": [], "слабых": [], "счёт": {}, "digest": "d"}
        res = pb.verify(fp, {"m.py": "a"}, answer_at=answer_at)
        self.assertEqual(res["итог"], pb.STALE,
                         "снимок старта не имеет права судить ответ, данный часом позже")
        self.assertNotEqual(res["итог"], pb.OK)

    def test_live_snapshot_carries_the_bytes_the_door_actually_read(self):
        """Живая половина того же случая, настоящими байтами: старый снимок несёт v1, свежий — v2."""
        write_probe("pbprobe_ot2.py", "EDITION = 'v1'\n")
        forget("pbprobe_ot2")

        def door():
            import pbprobe_ot2
            return pbprobe_ot2.EDITION

        self.assertEqual(door(), "v1")
        at_start = pb.fingerprint(answer="старт")
        sha_v1 = next(r["sha"] for r in at_start["модули"] if r["имя"] == "pbprobe_ot2")

        write_probe("pbprobe_ot2.py", "EDITION = 'v2 — редакция суток новее'\n")
        forget("pbprobe_ot2")
        self.assertEqual(door(), "v2 — редакция суток новее")

        at_answer = pb.fingerprint(answer="час спустя")
        sha_v2 = next(r["sha"] for r in at_answer["модули"] if r["имя"] == "pbprobe_ot2")
        self.assertNotEqual(sha_v1, sha_v2)

        # Снимок СТАРТА против того, что реально произвело ответ: «совпало» превращается в
        # «разошлось» — и это ровно то, чего сегодня не умеет никто.
        res = pb.verify(at_start, {"tmp/proc_bytes_probe/pbprobe_ot2.py": sha_v2}, strict=False)
        self.assertEqual(res["по_именам"]["tmp/proc_bytes_probe/pbprobe_ot2.py"], pb.V_DIFF)
        self.assertEqual(res["итог"], pb.BROKEN)


class TestNegativeOT3(Base):
    """ОТ-3, ГЛАВНЫЙ. Все модули совпали — а текст ответа другой.

    Ответ произвели ещё и данные, которых коммит не знает: `playbook.md` (вне git, живой
    `suggest.py` его и читает, и пишет) и `lesson_store.tsv` (вне git, сменился 06.09 04:54).
    Признак «код совпал с коммитом» верен и зелен — а клиент получил текст, которого в коммите
    нет ни одной строкой."""

    def _fp(self):
        return {
            "v": 1, "ответ": "ответ клиенту", "ts": time.time(), "снят": "-",
            "модули": [{"имя": "suggest", "путь": "suggest.py", "sha": "s1", "как": pb.VIA_READ},
                       {"имя": "pricing", "путь": "pricing.py", "sha": "p1", "как": pb.VIA_READ}],
            "данные": [{"путь": "manager-bot/docs/playbook.md", "sha": "book-новый"},
                       {"путь": "lesson_store.tsv", "sha": "урок-новый"},
                       {"путь": "price_source.json", "sha": "цена-06.09"}],
            "слабых": [], "счёт": {}, "digest": "d",
        }

    def _git(self, args):
        """Живой формат ответа git: `show` на отсутствующем пути даёт НЕНУЛЕВОЙ код возврата."""
        target = args[-1].split(":", 1)[1]
        blobs = {"suggest.py": b"s1-src", "pricing.py": b"p1-src",
                 "price_source.json": "цена старая".encode("utf-8")}
        if target in blobs:
            return 0, blobs[target]
        return 128, b""

    def test_instrument_that_counts_only_modules_goes_green_and_is_therefore_rejected(self):
        """Тест ПОКАЗЫВАЕТ провал негодного прибора, а не подразумевает его."""
        fp = self._fp()
        only_modules = dict(fp, данные=[])
        expect = {"suggest.py": "s1", "pricing.py": "p1"}
        self.assertEqual(pb.verify(only_modules, expect)["итог"], pb.OK,
                         "прибор без файлов данных проходит ОТ-3 — потому он и не принят")

    def test_full_instrument_names_the_files_the_commit_does_not_know(self):
        fp = self._fp()
        paths = [r["путь"] for r in fp["модули"]] + [r["путь"] for r in fp["данные"]]
        expect = pb.expect_from_git("HEAD", paths, run=self._git)

        self.assertIsNone(expect["manager-bot/docs/playbook.md"])
        self.assertIsNone(expect["lesson_store.tsv"])

        expect["suggest.py"] = "s1"          # код совпал с коммитом целиком
        expect["pricing.py"] = "p1"

        res = pb.verify(fp, expect)
        self.assertEqual(res["по_именам"]["suggest.py"], pb.V_MATCH)
        self.assertEqual(res["по_именам"]["manager-bot/docs/playbook.md"], pb.V_UNTRACKED)
        self.assertEqual(res["по_именам"]["lesson_store.tsv"], pb.V_UNTRACKED)
        self.assertEqual(res["по_именам"]["price_source.json"], pb.V_DIFF,
                         "файл в git, но горячий: редакция 06.09 вошла в ответ без рестарта")
        self.assertEqual(res["итог"], pb.BROKEN)

    def test_untracked_path_is_a_separate_outcome_from_diverged(self):
        """«Вне git» и «разошлось» — разные новости: первое чинится не пересборкой, а тем, что
        файл вообще не под учётом."""
        self.assertNotEqual(pb.V_UNTRACKED, pb.V_DIFF)
        self.assertNotEqual(pb.V_ABSENT, pb.V_MATCH)


# ─────────────────────────── ИНВАРИАНТЫ ПРИБОРА ─────────────────────────────────────────────────

class TestInstrumentInvariants(Base):

    def test_five_outcomes_are_distinct(self):
        vals = {pb.V_MATCH, pb.V_DIFF, pb.V_ABSENT, pb.V_UNTRACKED, pb.V_WEAK, pb.V_UNEXPECTED}
        self.assertEqual(len(vals), 6, "исходы обязаны быть различимы словами, а не оттенками")

    def test_audit_hook_never_raises(self):
        """Исключение из аудит-ловушки ОТМЕНЯЕТ саму операцию: сломанный прибор уронил бы боевое
        чтение. Кормим её мусором всех форм."""
        for args in ((), (None,), (b"\xff\xfe", "r", 0), (123, None, None), ("x", 5, "нет")):
            pb._audit("open", args)
        pb._audit("exec", (None,))
        pb._audit("неизвестное событие", ("x",))

    def test_emit_survives_unwritable_ledger(self):
        """Наблюдение не имеет права стоить ответа клиенту."""
        fp, text = pb.emit(answer="x", logger=None,
                           path=os.path.join(HERE, "нет", "такого", "каталога", "l.jsonl"))
        self.assertTrue(text.startswith(pb.LINE_TAG))

    def test_disarm_silences_both_recorders(self):
        pb.disarm()
        write_probe("pbprobe_silent.py", "Y = 1\n")
        forget("pbprobe_silent")
        import pbprobe_silent              # noqa: F401
        p = write_probe("pbprobe_silent.json", "{}\n")
        with open(p, "r", encoding="utf-8") as f:
            f.read()
        self.assertNotIn("pbprobe_silent", pb.ledger().modules)
        self.assertNotIn("tmp/proc_bytes_probe/pbprobe_silent.json", pb.ledger().data)

    def test_pyc_provenance_is_tied_to_the_source_and_says_so_aloud(self):
        """Провенанс `кэш` не голословен: заголовок `.pyc` сверяется со `stat` исходника.

        Замер 07.09: тёплый `__pycache__` (275 файлов) делает `кэш` ОСНОВНЫМ провенансом живой
        работы — 13 целей из 13 пришли именно им. Значит связь обязана быть названа, а не
        подразумеваться."""
        write_probe("pbprobe_tie.py", "T = 1\n")
        forget("pbprobe_tie")
        import pbprobe_tie                # noqa: F401
        forget("pbprobe_tie")
        pb.reset()
        pb.install(do_backfill=False)
        import pbprobe_tie                # noqa: F401,F811
        rec = pb.ledger().modules["pbprobe_tie"]
        self.assertEqual(rec["как"], pb.VIA_PYC, "кэш прогрет первым импортом")
        self.assertEqual(rec.get("пометка"), "кэш сверен с исходником")
        self.assertEqual(pb.pyc_ties_source(None, "нет"), "кэш не назван")
        self.assertEqual(pb.pyc_ties_source("нет такого файла.pyc", "x"), "кэш не назван")

    def test_exec_counter_does_not_count_normal_module_startup(self):
        """Разряд C сегодня ноль, и счётчик обязан это показывать. Голое событие `exec` дало бы
        865 на 49 честно загруженных модулей — готовый ложный диагноз «динамика есть»."""
        before = pb.ledger().exec_events
        write_probe("pbprobe_execnoise.py", "Z = 1\n")
        forget("pbprobe_execnoise")
        import pbprobe_execnoise          # noqa: F401
        self.assertEqual(pb.ledger().exec_events, before,
                         "штатный запуск модуля динамическим исполнением не является")

    def test_fabricated_code_filename_is_not_mistaken_for_a_repo_file(self):
        """ОТРИЦАТЕЛЬНЫЙ ГОЛДЕН НА САМ ПРИБОР. `os.path.abspath('<string>')` разрешает выдуманное
        имя ОТ РАБОЧЕГО КАТАЛОГА — а он у демона равен корню репозитория. Замер 07.09: без этой
        проверки счётчик разряда C показал 24 события «нашего дерева», из них ложных 24 из 24."""
        before = pb.ledger().exec_events
        exec(compile("PB_FAKE = 1", "<string>", "exec"), {})
        exec(compile("PB_FAKE = 2", "нет-такого-файла.py", "exec"), {})
        self.assertEqual(pb.ledger().exec_events, before)
        self.assertEqual(pb.ledger().exec_where, [])

    def test_module_does_not_write_anything_by_itself(self):
        """Прибор НАБЛЮДАЕТ. Ни подъёма процессов, ни правки боевых файлов, ни ключа --write."""
        with open(os.path.join(HERE, "proc_bytes.py"), "r", encoding="utf-8") as f:
            src = f.read()
        for forbidden in ("--write", "taskkill", "schtasks", "Popen(", "os.remove", "rmtree"):
            self.assertNotIn(forbidden, src, "прибор обязан только смотреть: %s" % forbidden)


if __name__ == "__main__":
    unittest.main(verbosity=2)
