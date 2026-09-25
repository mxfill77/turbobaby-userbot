# -*- coding: utf-8 -*-
u"""test_vps_token_install.py — регресс инструмента вписывания годового токена (23.09.2026).

Держит ПЯТЬ инвариантов, каждый из которых уже был бы дефектом молча:
  1) ФОРМА ОТКАЗЫВАЕТ. Одиннадцать негодных значений (пусто, без префикса, обрезка окном
     80 колонок, кириллица, кавычка, краевой пробел…) не доходят до текста файла: `plan_write`
     возвращает ИСХОДНЫЙ текст и пустой отчёт. Без этой пары зелёный не значит ничего.
  2) СЛОТ B НЕ ТРОГАЕМ. Имени слота B нет в `WRITE_NAMES`, и его строка выходит из `env_rewrite`
     байт в байт. Он исправен и принадлежит второй учётке — переписав его, мы уничтожили бы
     единственный работающий вход полосы вместе с починкой.
  3) СЕКРЕТ НЕ В argv. Значение уходит на сервер ТОЛЬКО в stdin-блобе; в командной строке ssh
     его нет ни в одном виде (иначе `ps` на обеих сторонах показал бы токен целиком).
  4) СУДИМ МАШИННЫМ ПОЛЕМ. `verdict_of_probe` не зеленеет ни на одном отказном конверте, а 429
     и 401 различает — это разные починки («вход принят, лимит кончился» / «вход не принят»).
  5) ЧИСТАЯ ЧАСТЬ ЧИСТА (VPS_TOKEN_PURE). Функции-судьи не знают ни `subprocess`, ни `open`, ни
     двери наружу — судья, умеющий ходить в сеть, однажды соврёт молча.

Фикстуры путей НАМЕРЕННО не повторяют суффикс боевого файла окружения: разбор `parse_env_files`
на суффикс не смотрит вовсе (он режет пробелы и скобку `(ignore_errors=no)`), а путь секретного
файла строковой константой кода гард полосы считает обращением к секрету — и по существу прав.

Запуск: D:\\turbobaby-bot\\venv\\Scripts\\python.exe -m unittest test_vps_token_install -v
"""
import ast
import base64
import inspect
import textwrap
import unittest

import vps_token_install as vti


class FormShape(unittest.TestCase):
    def test_all_bad_values_refused_and_text_untouched(self):
        u"""Одиннадцать негодных → одиннадцать отказов, текст не сдвинулся ни разу."""
        text = vti.synth_env()
        refused = untouched = 0
        for title, value in vti.bad_cases():
            ok, why, new_text, report = vti.plan_write(text, value, vti.WRITE_NAMES)
            self.assertFalse(ok, u"негодное прошло: %s" % title)
            self.assertEqual(new_text, text, u"текст изменён при отказе: %s" % title)
            self.assertEqual(report, [], u"отчёт непустой при отказе: %s" % title)
            self.assertTrue(why, u"отказ без причины: %s" % title)
            refused += 1
            untouched += 1
        self.assertEqual(refused, len(vti.bad_cases()))
        self.assertEqual(untouched, len(vti.bad_cases()))

    def test_good_value_accepted(self):
        good = vti.synth_value("Nn9")
        self.assertEqual(len(good), vti.LEN_REF)
        shape = vti.check_shape(good)
        self.assertTrue(shape["ok"])
        self.assertEqual(shape["n"], vti.LEN_REF)

    def test_reason_never_carries_the_value(self):
        u"""Наружу уходит признак и число знаков, но не кусок значения и не его хеш."""
        good = vti.synth_value("Nn9")
        for value in [good] + [v for _t, v in vti.bad_cases() if v.strip()]:
            reason = vti.check_shape(value)["reason"]
            self.assertNotIn(value, reason)
            self.assertNotIn(value[-16:], reason)

    def test_length_floor_refuses_the_80_column_break(self):
        u"""Класс 28.07: строка, порванная окном в 80 колонок, короче пола и обязана отпасть."""
        broken = vti.TOKEN_PREFIX + "-" + "z" * (79 - len(vti.TOKEN_PREFIX) - 1)
        self.assertEqual(len(broken), 79)
        self.assertFalse(vti.check_shape(broken)["ok"])


class Rewrite(unittest.TestCase):
    def test_writes_both_names_and_leaves_slot_b_byte_identical(self):
        text = vti.synth_env()
        good = vti.synth_value("Nn9")
        new_text, report = vti.env_rewrite(text, good, vti.WRITE_NAMES)
        self.assertEqual(sorted(r["name"] for r in report), sorted(vti.WRITE_NAMES))
        before_b = [l for l in text.splitlines() if l.startswith(vti.NAME_SLOT_B)]
        after_b = [l for l in new_text.splitlines() if l.startswith(vti.NAME_SLOT_B)]
        self.assertEqual(before_b, after_b)
        self.assertEqual(len(after_b), 1)

    def test_slot_b_not_in_write_names(self):
        u"""Замок на само НАМЕРЕНИЕ: имя слота B не должно появиться в списке записи никогда."""
        self.assertNotIn(vti.NAME_SLOT_B, vti.WRITE_NAMES)
        self.assertEqual(vti.WRITE_NAMES[0], vti.NAME_ACTIVE)

    def test_missing_name_is_appended_not_skipped(self):
        text = vti.synth_env((vti.NAME_ACTIVE, vti.NAME_SLOT_B))
        good = vti.synth_value("Nn9")
        new_text, report = vti.env_rewrite(text, good, vti.WRITE_NAMES)
        appended = [r for r in report if r["action"] == "append"]
        self.assertEqual(len(appended), 1)
        self.assertEqual(appended[0]["name"], vti.NAME_SLOT_A)
        self.assertIn(vti.NAME_SLOT_A + "=" + good, new_text)

    def test_quoted_and_exported_forms_are_replaced_whole(self):
        u"""Правая часть меняется ЦЕЛИКОМ: прежние кавычки не могут подмешаться в значение."""
        good = vti.synth_value("Nn9")
        text = u"export %s=\"старое-значение\"\n" % vti.NAME_ACTIVE
        new_text, report = vti.env_rewrite(text, good, (vti.NAME_ACTIVE,))
        self.assertEqual(new_text, u"export %s=%s\n" % (vti.NAME_ACTIVE, good))
        self.assertEqual(report[0]["action"], "replace")
        self.assertNotIn(u'"', new_text)

    def test_crlf_and_missing_trailing_newline_survive(self):
        good = vti.synth_value("Nn9")
        text = u"# шапка\r\n%s=старое\r\n" % vti.NAME_ACTIVE
        new_text, _r = vti.env_rewrite(text, good, (vti.NAME_ACTIVE,))
        self.assertIn(u"\r\n", new_text)
        self.assertTrue(new_text.endswith(u"\r\n"))
        no_nl = u"%s=старое" % vti.NAME_ACTIVE          # файла без перевода строки в конце
        new2, _r2 = vti.env_rewrite(no_nl, good, vti.WRITE_NAMES)
        self.assertIn(vti.NAME_SLOT_A + "=" + good, new2)
        self.assertNotIn(good + vti.NAME_SLOT_A, new2)   # строки не слиплись

    def test_report_carries_lengths_not_values(self):
        good = vti.synth_value("Nn9")
        _new, report = vti.env_rewrite(vti.synth_env(), good, vti.WRITE_NAMES)
        for r in report:
            self.assertEqual(set(r), {"line", "name", "action", "old_len", "new_len"})
            self.assertEqual(r["new_len"], len(good))
            for v in r.values():
                self.assertNotEqual(v, good)


class EnvFileAddress(unittest.TestCase):
    u"""Адрес файла окружения спрашивается у юнита. Форма ответа снята с живого systemd."""

    def test_single_file_with_flag_tail(self):
        out = u"EnvironmentFiles=/root/.config/executor-environment (ignore_errors=no)\n"
        self.assertEqual(vti.parse_env_files(out), ["/root/.config/executor-environment"])

    def test_empty_means_unit_names_nothing(self):
        self.assertEqual(vti.parse_env_files(u"EnvironmentFiles=\nActiveState=active\n"), [])
        self.assertEqual(vti.parse_env_files(u""), [])

    def test_several_files_are_all_reported(self):
        out = (u"EnvironmentFiles=/etc/a/one (ignore_errors=no) /etc/a/two (ignore_errors=yes)\n")
        self.assertEqual(vti.parse_env_files(out), ["/etc/a/one", "/etc/a/two"])

    def test_other_properties_ignored(self):
        out = u"ActiveState=active\nMainPID=1234\nEnvironmentFiles=/etc/a/one (ignore_errors=no)\n"
        self.assertEqual(vti.parse_env_files(out), ["/etc/a/one"])


class Verdict(unittest.TestCase):
    def test_no_bad_envelope_turns_green(self):
        for title, rc, text in vti.bad_envelopes():
            word, why = vti.verdict_of_probe(rc, text)
            self.assertNotEqual(word, vti.GREEN, u"позеленело на отказе: %s" % title)
            self.assertTrue(why)

    def test_429_and_401_are_different_words(self):
        self.assertEqual(vti.verdict_of_probe(1, '{"is_error":true,"apiErrorStatus":429}')[0], vti.LIMIT)
        self.assertEqual(vti.verdict_of_probe(1, '{"is_error":true,"apiErrorStatus":401}')[0], vti.DENIED)
        self.assertNotEqual(vti.LIMIT, vti.DENIED)

    def test_machine_field_beats_text(self):
        u"""Мина 71r: текст «resets Sep 27, 9am (UTC)» встречается в чужих цитатах. Судит поле."""
        envelope = ('{"is_error":true,"apiErrorStatus":401,'
                    '"result":"earlier we saw weekly limit resets Sep 27, 9am (UTC)"}')
        self.assertEqual(vti.verdict_of_probe(1, envelope)[0], vti.DENIED)

    def test_green_only_on_clean_envelope(self):
        self.assertEqual(vti.verdict_of_probe(0, '{"is_error":false,"result":"OK"}')[0], vti.GREEN)
        self.assertEqual(vti.verdict_of_probe(1, '{"is_error":false,"result":"OK"}')[0], vti.OTHER)


class LiveEnvelopeForm(unittest.TestCase):
    u"""Голдены на ЖИВОЙ форме конверта (71u, 22.09.2026), а не на форме транскрипта.

    Живой конверт 71r (`tmp/vps_token_switch/probe_neg.json`) несёт поле `api_error_status`, а
    под ним — строку хука `SessionEnd hook … failed`. Прежний судья читал `apiErrorStatus` (имя
    из транскрипта) и `json.loads` с хвостом. Первые два теста до правки давали НЕВЕРНЫЙ вердикт."""

    HOOK_TAIL = ("\nSessionEnd hook [D:/turbobaby-bot/venv/Scripts/python.exe "
                 "D:/turbobaby-bot/dispatch_notify.py --hook session_end] failed: Hook cancelled\n")

    def test_snake_field_beats_limit_words(self):
        u"""До правки: поле не найдено → текст «weekly limit» → 429. Верно — 401."""
        envelope = ('{"is_error":true,"subtype":"success","api_error_status":401,'
                    '"result":"earlier we saw weekly limit resets Sep 27, 9am (UTC)","type":"result"}')
        self.assertEqual(vti.verdict_of_probe(1, envelope)[0], vti.DENIED)

    def test_clean_envelope_with_hook_tail_is_green(self):
        u"""До правки: «Extra data» → конверт не разобран → «иное» на чистом ответе."""
        envelope = '{"is_error":false,"subtype":"success","api_error_status":null,"result":"OK"}'
        self.assertEqual(vti.verdict_of_probe(0, envelope + self.HOOK_TAIL)[0], vti.GREEN)

    def test_live_71r_negative_envelope(self):
        envelope = ('{"duration_api_ms":0,"modelUsage":{},"terminal_reason":"api_error",'
                    '"is_error":true,"num_turns":1,"subtype":"success","api_error_status":401,'
                    '"result":"Failed to authenticate. API Error: 401 Invalid bearer token","type":"result"}')
        data = vti.parse_envelope(envelope + self.HOOK_TAIL)
        self.assertEqual(vti.status_of(data, envelope), 401)
        self.assertEqual(vti.verdict_of_probe(1, envelope + self.HOOK_TAIL)[0], vti.DENIED)

    def test_429_snake_is_limit(self):
        envelope = ('{"is_error":true,"api_error_status":429,'
                    '"result":"You\'ve hit your weekly limit · resets Sep 27, 9am (UTC)"}')
        self.assertEqual(vti.verdict_of_probe(1, envelope)[0], vti.LIMIT)

    def test_transcript_name_still_read(self):
        self.assertEqual(vti.status_of({"apiErrorStatus": "429"}, ""), 429)
        self.assertIsNone(vti.status_of({"api_error_status": True}, ""))


def _probe_out(active, filled="1", rc=1, envelope=None, loaded="1", mtime=100, start=200):
    u"""Синтетический вывод программы пробы: `k=v` сверху, конверт, `probe_rc` снизу."""
    head = "loaded=%s\n" % loaded
    if loaded != "1":
        return head
    head += "slot_filled=%s\n" % filled
    if filled != "1":
        return head
    head += "is_active=%s\nfile_mtime=%d\ndaemon_start=%d\n" % (active, mtime, start)
    return head + (envelope or "") + "\nprobe_rc=%d\n" % rc


ENV_429 = ('{"is_error":true,"subtype":"success","api_error_status":429,'
           '"result":"You\'ve hit your weekly limit · resets Sep 27, 9am (UTC)","type":"result"}')
ENV_401 = ('{"is_error":true,"subtype":"success","api_error_status":401,'
           '"result":"Failed to authenticate. API Error: 401 OAuth access token is invalid.","type":"result"}')
ENV_OK = '{"is_error":false,"subtype":"success","api_error_status":null,"result":"OK","type":"result"}'


class ProbeSlots(unittest.TestCase):
    u"""Режим `--probe-slots`: один вызов на имя, имена — из закрытого списка, значений наружу нет."""

    def test_unknown_name_is_refused(self):
        with self.assertRaises(ValueError):
            vti.probe_slot_script("/etc/a/one", "PATH; rm -rf /")

    def test_script_closes_every_named_channel(self):
        for name in vti.PROBE_NAMES:
            s = vti.probe_slot_script("/etc/a/one", name)
            lines = s.splitlines()
            self.assertEqual(lines[0], "exec 2>/dev/null")        # обломок токена не напечатается
            self.assertIn("cd / ", s)
            self.assertIn("</dev/null", s)                        # stdin — программа, не подсказка
            self.assertIn("--no-session-persistence", s)
            self.assertIn("--setting-sources project", s)         # хуки сервера пробой не зовутся
            self.assertIn("env -u ANTHROPIC_API_KEY", s)          # как у демона
            self.assertIn('CLAUDE_CODE_OAUTH_TOKEN="${%s}"' % name, s)
            self.assertNotIn(vti.TOKEN_PREFIX, s)
            calls = [l for l in lines if "--output-format" in l]
            self.assertEqual(len(calls), 1, u"вызов поставщика на имя — ровно один")
            self.assertLess(lines.index("echo slot_filled=1"), lines.index(calls[0]))

    def test_script_parses_as_bash(self):
        import shutil
        import subprocess
        bash = shutil.which("bash")
        if not bash:
            self.skipTest(u"bash на этой машине не найден")
        for name in vti.PROBE_NAMES:
            p = subprocess.run([bash, "-n"], input=vti.probe_slot_script("/etc/a/one", name).encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout)

    def test_three_ssh_calls_script_in_stdin(self):
        seen = []
        real = vti.ssh_run

        def fake(cmd, stdin_text=None, timeout=None):
            seen.append((cmd, stdin_text, timeout))
            return 0, _probe_out("1", envelope=ENV_429)

        vti.ssh_run = fake
        try:
            rows = vti.probe_slots("/etc/a/one", save=False)
        finally:
            vti.ssh_run = real
        self.assertEqual(len(seen), 3)
        self.assertEqual([c for c, _s, _t in seen], ["bash -s"] * 3)
        self.assertEqual([t for _c, _s, t in seen], [vti.SSH_PROBE_TIMEOUT] * 3)
        for (_c, script, _t), name in zip(seen, vti.PROBE_NAMES):
            self.assertIn('"${%s}"' % name, script)
        self.assertEqual([r["code"] for r in rows], [429, 429, 429])

    def test_row_words(self):
        r = vti.slot_row(vti.NAME_SLOT_A, _probe_out("0", envelope=ENV_401))
        self.assertEqual((r["code"], r["word"], r["is_error"], r["active"]), (401, vti.DENIED, True, "0"))
        self.assertIn("OAuth access token is invalid", r["text"])
        r = vti.slot_row(vti.NAME_ACTIVE, _probe_out("1", rc=0, envelope=ENV_OK))
        self.assertEqual((r["code"], r["word"], r["is_error"]), (200, vti.GREEN, False))
        r = vti.slot_row(vti.NAME_SLOT_B, _probe_out("0", filled="0"))
        self.assertFalse(r["called"])
        self.assertEqual(r["word"], u"пуст")
        r = vti.slot_row(vti.NAME_SLOT_B, u"ssh не ответил за 300 с", channel_rc=255)
        self.assertFalse(r["called"])
        self.assertIsNone(r["code"])
        r = vti.slot_row(vti.NAME_ACTIVE, _probe_out("1", loaded="0"))
        self.assertFalse(r["called"])

    def test_summary_premise_71r_shape(self):
        u"""Форма 71r: действующее и B — 429 и одно значение, A — 401."""
        rows = [vti.slot_row(vti.NAME_ACTIVE, _probe_out("1", envelope=ENV_429)),
                vti.slot_row(vti.NAME_SLOT_A, _probe_out("0", envelope=ENV_401)),
                vti.slot_row(vti.NAME_SLOT_B, _probe_out("1", envelope=ENV_429))]
        counts, lines = vti.summarize_slots(rows)
        self.assertEqual((counts[200], counts[429], counts[401]), (0, 2, 1))
        text = u"\n".join(lines)
        self.assertIn(u"ДЕЙСТВУЮЩИЙ отвечает 429", text)
        self.assertIn(vti.NAME_SLOT_B, text.split(u"тем же значением")[1].splitlines()[0])
        self.assertIn(u"не менялся после старта демона", text)

    def test_summary_owner_shape(self):
        u"""Форма слов владельца: действующий 200, лимит на одном."""
        rows = [vti.slot_row(vti.NAME_ACTIVE, _probe_out("1", rc=0, envelope=ENV_OK)),
                vti.slot_row(vti.NAME_SLOT_A, _probe_out("1", rc=0, envelope=ENV_OK)),
                vti.slot_row(vti.NAME_SLOT_B, _probe_out("0", envelope=ENV_429, mtime=300, start=200))]
        counts, lines = vti.summarize_slots(rows)
        self.assertEqual((counts[200], counts[429], counts[401]), (2, 1, 0))
        text = u"\n".join(lines)
        self.assertIn(u"ДЕЙСТВУЮЩИЙ отвечает 200", text)
        self.assertIn(u"ВНИМАНИЕ: файл окружения изменён ПОСЛЕ старта демона", text)


class SecretChannel(unittest.TestCase):
    u"""Значение уходит ТОЛЬКО в stdin-блобе. В argv его нет — иначе `ps` покажет токен целиком."""

    def _capture(self, fn):
        seen = {}

        class FakeProc(object):
            returncode = 0
            stdout = (b"backup=/tmp/x.bak\nbackup_ok=1\nbytes_before=10\nbytes_after=10\n"
                      b"readback_ok=1\nnamed_ok=1\nothers_untouched=1\ndone=1\n")

        def fake_run(argv, **kw):
            seen["argv"] = list(argv)
            seen["input"] = kw.get("input")
            seen["timeout"] = kw.get("timeout")
            return FakeProc()

        real = vti.subprocess.run
        vti.subprocess.run = fake_run
        try:
            fn()
        finally:
            vti.subprocess.run = real
        return seen

    def test_value_never_appears_in_argv(self):
        good = vti.synth_value("Nn9")
        seen = self._capture(lambda: vti.apply_value("/etc/a/one", good, "20260923-000000Z"))
        joined = u" ".join(seen["argv"])
        self.assertNotIn(good, joined)
        self.assertNotIn(good[:40], joined)
        self.assertNotIn(vti.TOKEN_PREFIX, joined)
        self.assertEqual(seen["argv"][-1], vti._REMOTE_BOOT)

    def test_value_travels_inside_the_stdin_blob(self):
        good = vti.synth_value("Nn9")
        seen = self._capture(lambda: vti.apply_value("/etc/a/one", good, "20260923-000000Z"))
        program = base64.b64decode(seen["input"]).decode("utf-8")
        self.assertIn(good, program)
        compile(program, "<remote>", "exec")          # программа на сервер обязана быть разборной

    def test_remote_program_reuses_the_tested_function(self):
        u"""На сервере работает ИСХОДНИК той самой `env_rewrite`, которую меряет этот тест."""
        program = vti._remote_program(vti._RS_APPLY, TARGET="/etc/a/one",
                                      NAMES=vti.WRITE_NAMES, VALUE="x", BAK="/etc/a/one.bak")
        self.assertIn(inspect.getsource(vti.env_rewrite).strip().splitlines()[0], program)
        self.assertIn("def env_rewrite", program)

    def test_ssh_carries_canon_timeouts(self):
        seen = self._capture(lambda: vti.ssh_run("true"))
        joined = u" ".join(seen["argv"])
        self.assertIn("ConnectTimeout=10", joined)
        self.assertIn("BatchMode=yes", joined)
        self.assertTrue(seen["timeout"] and seen["timeout"] > 0)


class Pure(unittest.TestCase):
    u"""VPS_TOKEN_PURE: судьи не знают двери наружу. Зеркало EXPECT_PC_PURE слоя ожиданий."""

    PURE = ("parse_env_files", "check_shape", "env_rewrite", "plan_write", "verdict_of_probe",
            "backup_name", "synth_value", "synth_env", "bad_cases", "bad_envelopes",
            "parse_envelope", "status_of", "probe_slot_script", "slot_row", "summarize_slots")
    FORBIDDEN = {"subprocess", "ssh_run", "run_remote_py", "getpass", "input", "open",
                 "restart_unit", "probe", "restore", "apply_value", "preflight", "find_env_path"}

    def test_pure_functions_touch_nothing_outside(self):
        for name in self.PURE:
            src = textwrap.dedent(inspect.getsource(getattr(vti, name)))
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    self.assertNotIn(node.id, self.FORBIDDEN,
                                     u"%s зовёт %s — чистая часть перестала быть чистой"
                                     % (name, node.id))
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, self.FORBIDDEN,
                                     u"%s зовёт .%s" % (name, node.attr))


# ═══════════════════ 25.09.2026: слоты любой буквы, `--use`, `--slot` — НА КОПИИ ═══════════════════
import io
import os
import re
import subprocess
import sys
import tempfile


def _copy_env(slots):
    u"""Синтетическая КОПИЯ файла окружения во временном каталоге (системный temp, префикс
    `uchetki_2509_`; не удаляется — запрет «ничего не удалять»). `slots` — {буква: значение|""}."""
    d = tempfile.mkdtemp(prefix="uchetki_2509_")
    path = os.path.join(d, "executor-environment-copy")
    lines = [u"# синтетика, боевых значений нет", vti.NAME_ACTIVE + "=" + vti.synth_value("Act")]
    for letter, val in sorted(slots.items()):
        lines.append(vti.SLOT_PREFIX + letter + "=" + val)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(u"\n".join(lines) + u"\n")
    return d, path


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _val(text, name):
    found = ""
    for line in text.splitlines():
        k, _, v = line.partition("=")
        if k.strip() == name:
            found = v.strip()
    return found


class _LocalServer(object):
    u"""Сервер, который исполняет СВОИ программы ЛОКАЛЬНО против КОПИИ файла окружения.

    Серверные программы (`_RS_PREFLIGHT`, `_RS_USE`, `_RS_APPLY`, `_RS_RESTORE`) идут НАСТОЯЩИЕ —
    тем же интерпретатором, что и тест, и правят КОПИЮ по-настоящему. Подменено только то, чего у
    копии нет: каталог процессов (`proc` — пустой либо фальшивое дерево теста; счёт заходов можно
    задать прямо `busy`), рестарт юнита (счётчик), отметка жизни демона (`marks` — по вызову) и
    ответ поставщика (`answer` по имени входа). Проба `bash -s` читает копию и сравнивает значения
    так же, как программа пробы на сервере, — наружу из неё уходят только признаки."""

    MARK = "MainPID=4242\nExecMainStartTimestampMonotonic=100\n"

    def __init__(self, target, answer=None, busy=None, busy_after=None, restart_ok=True, proc=None,
                 marks=None, fail_preflight_after=None):
        self.target, self.busy, self.busy_after, self.restart_ok = target, busy, busy_after, restart_ok
        self.answer = answer or {}
        self.proc = proc or tempfile.mkdtemp(prefix="uchetki_2509_proc_")
        self.marks = list(marks) if marks is not None else None
        self.fail_preflight_after = fail_preflight_after
        self.calls, self.restarts, self.preflights, self.probed = [], 0, 0, []
        self.file_at_restart = []

    def ssh_run(self, cmd, stdin_text=None, timeout=None):
        self.calls.append(cmd)
        if cmd == vti._REMOTE_BOOT:
            program = base64.b64decode(stdin_text).decode("utf-8")
            is_pre = "busy_claude" in program
            if is_pre:
                self.preflights += 1
                if self.fail_preflight_after is not None and self.preflights > self.fail_preflight_after:
                    return 255, u"ssh не ответил за 90 с"
                # список процессов копии не принадлежит: каталог процессов — свой у теста
                program = program.replace("PROC = '/proc'", "PROC = %r" % self.proc)
            p = subprocess.run([sys.executable, "-"], input=program.encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
                               env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            out = p.stdout.decode("utf-8", "replace")
            want = self.busy if (self.busy_after is None or self.preflights == 1) else self.busy_after
            if is_pre and want is not None:
                out = re.sub(r"busy_claude=\d+", "busy_claude=%d" % want, out)
            return p.returncode, out
        if cmd == "bash -s":
            m = re.search(r'if \[ -z "\$\{(\w+)\}" \]', stdin_text)
            name = m.group(1)
            self.probed.append(name)
            with io.open(self.target, encoding="utf-8") as f:
                text = f.read()
            val, act = _val(text, name), _val(text, vti.NAME_ACTIVE)
            if not val:
                return 0, _probe_out("0", filled="0")
            env_text = self.answer.get(name, self.answer.get("*", ENV_OK))
            rc = 0 if env_text == ENV_OK else 1
            after = self.answer.get("file_after_start")
            return 0, _probe_out("1" if val == act else "0", rc=rc, envelope=env_text,
                                 mtime=300 if after else 100, start=200)
        if cmd.startswith("systemctl restart"):
            self.restarts += 1
            self.file_at_restart.append(_read(self.target))   # С КАКИМ файлом поднимается демон
            # Живая форма: `restart; show` отдаёт код ПОСЛЕДНЕЙ команды (show) = 0 даже при провале
            # рестарта — провал виден только полем ActiveState. Код 1 здесь спрятал бы эту проверку.
            if not self.restart_ok:
                return 0, "MainPID=0\nActiveState=failed\nSubState=failed\nNRestarts=1\n"
            return 0, "MainPID=4242\nActiveState=active\nSubState=running\nNRestarts=0\n"
        if cmd.startswith("systemctl show") and "ExecMainStartTimestampMonotonic" in cmd:
            if self.marks is None:
                return 0, self.MARK
            mark = self.marks.pop(0) if len(self.marks) > 1 else self.marks[0]
            return (0, mark) if mark else (1, u"")
        return 1, u"неожиданная команда в тесте: %s" % cmd


def _fake_proc(procs):
    u"""Фальшивый каталог процессов: {pid: (cmdline-байты, cgroup-текст | None)}."""
    d = tempfile.mkdtemp(prefix="uchetki_2509_proc_")
    for pid, (cmd, cg) in procs.items():
        os.mkdir(os.path.join(d, str(pid)))
        with open(os.path.join(d, str(pid), "cmdline"), "wb") as f:
            f.write(cmd)
        if cg is not None:
            with io.open(os.path.join(d, str(pid), "cgroup"), "w", encoding="utf-8") as f:
                f.write(cg)
    os.mkdir(os.path.join(d, "self"))                  # нечисловое имя обязано пропускаться
    return d


class _WithServer(unittest.TestCase):
    def serve(self, target, **kw):
        srv = _LocalServer(target, **kw)
        self.addCleanup(setattr, vti, "ssh_run", vti.ssh_run)
        self.addCleanup(setattr, vti, "ENV_PATH_FORCED", vti.ENV_PATH_FORCED)
        vti.ssh_run = srv.ssh_run
        vti.ENV_PATH_FORCED = target            # адрес «назвал человек» — systemctl show не зовём
        return srv

    def backups(self, d):
        return sorted(x for x in os.listdir(d) if ".bak-" in x)


class SlotNames(unittest.TestCase):
    def test_letter_to_name_and_refusals(self):
        self.assertEqual(vti.slot_name("c"), "TB_CLAUDE_TOKEN_C")
        for bad in ("", "AB", "1", "Ж", "C; rm", None):
            with self.assertRaises(ValueError):
                vti.slot_name(bad)

    def test_probe_script_accepts_any_letter_but_not_garbage(self):
        vti.probe_slot_script("/etc/a/one", "TB_CLAUDE_TOKEN_Q")
        for bad in ("TB_CLAUDE_TOKEN_QQ", "TB_CLAUDE_TOKEN_", "HOME", "TB_CLAUDE_TOKEN_A; id",
                    "TB_CLAUDE_TOKEN_A\n"):                        # `$` регулярки пропустил бы перевод строки
            with self.assertRaises(ValueError):
                vti.probe_slot_script("/etc/a/one", bad)

    def test_names_from_preflight_and_order(self):
        names = vti.names_from_preflight("2:CLAUDE_CODE_OAUTH_TOKEN:108,3:TB_CLAUDE_TOKEN_B:108,"
                                         "4:TB_CLAUDE_TOKEN_A:108,5:TB_CLAUDE_TOKEN_C:0,6:PATH:9")
        self.assertEqual(names["TB_CLAUDE_TOKEN_C"], 0)
        self.assertEqual(vti.probe_names_of(names),
                         (vti.NAME_ACTIVE, "TB_CLAUDE_TOKEN_A", "TB_CLAUDE_TOKEN_B", "TB_CLAUDE_TOKEN_C"))
        self.assertEqual(vti.probe_names_of({}), vti.PROBE_NAMES)      # разведки нет — прежние три

    def test_reset_only_on_429_and_in_provider_words(self):
        r = vti.slot_row(vti.NAME_SLOT_B, _probe_out("0", envelope=ENV_429))
        self.assertEqual(r["reset"], u"Sep 27, 9am (UTC)")
        r = vti.slot_row(vti.NAME_SLOT_A, _probe_out("0", envelope=ENV_401))
        self.assertEqual(r["reset"], u"")                          # мина 71r: на 401 не ищем
        env = '{"is_error":true,"api_error_status":429,"result":"Claude AI usage limit reached|1790499600"}'
        self.assertIn(u"UTC", vti.slot_row(vti.NAME_SLOT_A, _probe_out("0", envelope=env))["reset"])
        env = ('{"is_error":true,"api_error_status":429,'
               '"result":"You\'ve hit your session limit · resets 4am (Asia/Bangkok)."}')
        self.assertEqual(vti.slot_row(vti.NAME_SLOT_A, _probe_out("0", envelope=env))["reset"],
                         u"4am (Asia/Bangkok)")


class UseSlotOnCopy(_WithServer):
    u"""`--use X` на КОПИИ. Отрицательные тесты задания: пустой слот и 401 после записи."""

    def test_empty_slot_never_becomes_active(self):
        u"""ПУСТОЙ СЛОТ НЕ СТАНОВИТСЯ ДЕЙСТВУЮЩИМ: файл побайтно тот же, копии нет, рестартов 0."""
        d, path = _copy_env({"A": vti.synth_value("Aa1"), "C": ""})
        before = _read(path)
        srv = self.serve(path)
        res = vti.use_slot("C", work=None, stamp="20260925-000000Z")
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "slot_empty")
        self.assertEqual(_read(path), before)
        self.assertEqual(self.backups(d), [])
        self.assertEqual((srv.restarts, srv.probed), (0, []))
        self.assertIn(u"пустой слот", u" ".join(res["lines"]))

    def test_empty_slot_refused_by_server_program_itself(self):
        u"""Второй рубеж: сама серверная программа отказывает пустому слоту ДО копии и записи."""
        d, path = _copy_env({"C": ""})
        before = _read(path)
        srv = self.serve(path)
        prog = vti._remote_program(vti._RS_USE, TARGET=path, SLOT_NAME="TB_CLAUDE_TOKEN_C",
                                   ACTIVE_NAME=vti.NAME_ACTIVE, BAK=vti.backup_name(path, "X"))
        rc, fields, _raw = vti.run_remote_py(prog)
        self.assertEqual((fields.get("slot_filled"), fields.get("err")), ("0", "slot_empty"))
        self.assertEqual(_read(path), before)
        self.assertEqual(self.backups(d), [])
        self.assertEqual(srv.restarts, 0)

    def test_401_after_use_rolls_back_the_copy(self):
        u"""401 ПОСЛЕ --use ОТКАТЫВАЕТ КОПИЮ: файл побайтно прежний, копия на месте, рестартов 0."""
        d, path = _copy_env({"A": vti.synth_value("Aa1"), "B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, answer={vti.NAME_ACTIVE: ENV_401})
        res = vti.use_slot("B", work=None, stamp="20260925-000001Z")
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "probe")
        self.assertTrue(res["rolled_back"])
        self.assertEqual(_read(path), before, u"файл после 401 не вернулся к копии")
        baks = self.backups(d)
        self.assertEqual(len(baks), 1, u"копия обязана остаться на диске")
        self.assertEqual(_read(os.path.join(d, baks[0])), before)
        self.assertEqual(srv.restarts, 0, u"демон не должен был перезапускаться")
        self.assertEqual(srv.probed, [vti.NAME_ACTIVE])            # ровно одна проба

    def test_429_after_use_rolls_back_too(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, answer={vti.NAME_ACTIVE: ENV_429})
        res = vti.use_slot("B", work=None, stamp="20260925-000002Z")
        self.assertEqual((res["ok"], res["stage"], srv.restarts), (False, "probe", 0))
        self.assertEqual(_read(path), before)

    def test_green_use_switches_active_and_restarts_once(self):
        u"""Близнец отрицательных: зелёная проба → действующее = слот B, прочие строки те же, ОДИН рестарт."""
        slot_b = vti.synth_value("Bb2")
        d, path = _copy_env({"A": vti.synth_value("Aa1"), "B": slot_b})
        before = _read(path).decode("utf-8")
        srv = self.serve(path)
        res = vti.use_slot("B", work=None, stamp="20260925-000003Z")
        self.assertTrue(res["ok"], res["lines"])
        self.assertEqual(res["stage"], "done")
        with io.open(path, encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(_val(after, vti.NAME_ACTIVE), slot_b)
        for name in ("TB_CLAUDE_TOKEN_A", "TB_CLAUDE_TOKEN_B"):
            self.assertEqual(_val(after, name), _val(before, name))
        self.assertEqual(srv.restarts, 1)
        self.assertEqual(len(self.backups(d)), 1)
        with io.open(path, "rb") as f:
            self.assertEqual(srv.file_at_restart, [f.read()])      # поднялся уже на новом файле
        joined = u"\n".join(res["lines"])
        self.assertNotIn(slot_b, joined)                           # значение не выходит наружу
        self.assertNotIn(vti.TOKEN_PREFIX, joined)

    def test_busy_server_is_not_restarted_and_file_untouched(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, busy=1)
        res = vti.use_slot("B", work=None)
        self.assertEqual((res["ok"], res["stage"], srv.restarts), (False, "busy", 0))
        self.assertEqual(_read(path), before)
        self.assertEqual(self.backups(d), [])
        self.assertIn(u"НЕ перезапускаю", u" ".join(res["lines"]))

    def test_task_started_during_probe_rolls_back_without_restart(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, busy=0, busy_after=1)
        res = vti.use_slot("B", work=None, stamp="20260925-000004Z")
        self.assertEqual((res["ok"], res["stage"], srv.restarts), (False, "busy_late", 0))
        self.assertEqual(_read(path), before)

    def test_failed_restart_restores_and_raises_back(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, restart_ok=False)
        res = vti.use_slot("B", work=None, stamp="20260925-000005Z")
        self.assertEqual((res["ok"], res["stage"]), (False, "restart"))
        self.assertEqual(_read(path), before)
        self.assertEqual(srv.restarts, 2)                          # неудачный + «поднять обратно»

    def test_already_active_writes_nothing(self):
        d, path = _copy_env({"B": ""})
        with io.open(path, encoding="utf-8") as f:
            text = f.read()
        act = _val(text, vti.NAME_ACTIVE)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace(vti.SLOT_PREFIX + "B=", vti.SLOT_PREFIX + "B=" + act))
        before = _read(path)
        srv = self.serve(path)
        res = vti.use_slot("B", work=None)
        self.assertEqual((res["stage"], srv.restarts), ("already", 0))
        self.assertEqual(_read(path), before)
        self.assertEqual(self.backups(d), [])


DAEMON_CG = "0::/system.slice/orchestrator-daemon.service\n"
OWNER_CG = "0::/user.slice/user-0.slice/session-3.scope\n"          # tmux `cc` владельца


class BusyByDaemonCgroup(_WithServer):
    u"""«Идёт заход» — это claude ВНУТРИ cgroup демона. Живая сессия владельца в tmux `cc` слову
    «учётка N» не мешает; процесс, чью cgroup не прочитать, — «не проверено», рестарта нет."""

    def facts(self, procs):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        self.serve(path, proc=_fake_proc(procs))
        ok, facts, _w = vti.preflight(path)
        self.assertTrue(ok)
        return facts

    def test_owner_session_is_not_a_daemon_task(self):
        facts = self.facts({101: (b"claude\x00--resume", OWNER_CG), 102: (b"python3\x00x.py", DAEMON_CG)})
        self.assertEqual(vti.busy_of(facts)[0], 0)
        self.assertEqual(facts.get("busy_claude_all"), "1")

    def test_daemon_child_is_busy(self):
        facts = self.facts({201: (b"/usr/bin/claude\x00-p\x00x", DAEMON_CG), 202: (b"claude", OWNER_CG)})
        self.assertEqual(vti.busy_of(facts)[0], 1)

    def test_unreadable_cgroup_is_unknown_not_zero(self):
        facts = self.facts({301: (b"claude\x00-p", None)})
        n, words = vti.busy_of(facts)
        self.assertIsNone(n)
        self.assertIn(u"неизвестно", words)

    def test_missing_field_is_unknown(self):
        self.assertIsNone(vti.busy_of({})[0])
        self.assertIsNone(vti.busy_of({"busy_claude": ""})[0])

    def test_use_goes_through_with_owner_session_open(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        srv = self.serve(path, proc=_fake_proc({101: (b"claude", OWNER_CG)}))
        res = vti.use_slot("B", work=None, stamp="20260925-000010Z")
        self.assertEqual((res["ok"], res["stage"], srv.restarts), (True, "done", 1), res["lines"])

    def test_use_refuses_on_unknown_busy(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, proc=_fake_proc({301: (b"claude", None)}))
        res = vti.use_slot("B", work=None)
        self.assertEqual((res["ok"], res["stage"], srv.restarts), (False, "busy", 0))
        self.assertEqual(_read(path), before)
        self.assertIn(u"не проверено", u" ".join(res["lines"]))


class DaemonRestartedDuringProbe(_WithServer):
    u"""Проба стоит ДО рестарта. Если демон САМ перезапустился, пока шла проба (Restart=always,
    отложенный рестарт задачи), он схватил отвергнутый вход — возврата файла мало, нужен один рестарт."""

    M0 = "MainPID=4242\nExecMainStartTimestampMonotonic=100\n"
    M1 = "MainPID=5555\nExecMainStartTimestampMonotonic=900\n"

    def test_refused_probe_and_daemon_restarted_meanwhile(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, answer={vti.NAME_ACTIVE: ENV_401}, marks=[self.M0, self.M1])
        res = vti.use_slot("B", work=None, stamp="20260925-000011Z")
        self.assertEqual((res["ok"], res["stage"], res["rolled_back"]), (False, "probe", True))
        self.assertEqual(_read(path), before)
        self.assertEqual(srv.restarts, 1, u"демон с отвергнутым входом обязан подняться на прежнем")
        self.assertEqual(srv.file_at_restart, [before], u"рестарт обязан идти ПОСЛЕ возврата файла")
        self.assertIn(u"поднят заново на прежнем значении", u" ".join(res["lines"]))

    def test_refused_probe_and_mark_unknown_restarts_once(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        srv = self.serve(path, answer={vti.NAME_ACTIVE: ENV_401}, marks=[""])
        res = vti.use_slot("B", work=None, stamp="20260925-000012Z")
        self.assertEqual((res["stage"], srv.restarts), ("probe", 1))

    def test_refused_probe_daemon_steady_no_restart(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        srv = self.serve(path, answer={vti.NAME_ACTIVE: ENV_401}, marks=[self.M0, self.M0])
        res = vti.use_slot("B", work=None, stamp="20260925-000013Z")
        self.assertEqual((res["stage"], srv.restarts), ("probe", 0))

    def test_second_preflight_lost_is_not_a_green_light(self):
        u"""Повторная разведка оборвалась → «не проверено», а не «не занято»: рестарта нет, файл назад."""
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path, fail_preflight_after=1)
        res = vti.use_slot("B", work=None, stamp="20260925-000014Z")
        self.assertEqual((res["ok"], res["stage"], srv.restarts), (False, "busy_late", 0))
        self.assertEqual(_read(path), before)

    def test_already_active_but_daemon_older_than_file(self):
        d, path = _copy_env({"B": ""})
        with io.open(path, encoding="utf-8") as f:
            text = f.read()
        act = _val(text, vti.NAME_ACTIVE)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace(vti.SLOT_PREFIX + "B=", vti.SLOT_PREFIX + "B=" + act))
        before = _read(path)
        srv = self.serve(path, answer={"file_after_start": True})
        res = vti.use_slot("B", work=None)
        self.assertEqual((res["stage"], res["ok"], srv.restarts), ("already", True, 1))
        self.assertEqual(_read(path), before)

    def test_already_active_older_daemon_but_busy_now(self):
        d, path = _copy_env({"B": ""})
        with io.open(path, encoding="utf-8") as f:
            text = f.read()
        act = _val(text, vti.NAME_ACTIVE)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace(vti.SLOT_PREFIX + "B=", vti.SLOT_PREFIX + "B=" + act))
        srv = self.serve(path, answer={"file_after_start": True}, busy=0, busy_after=1)
        res = vti.use_slot("B", work=None)
        self.assertEqual((res["stage"], res["ok"], srv.restarts), ("already", False, 0))
        self.assertIn(u"рестарт НЕ делаю", u" ".join(res["lines"]))


class InstallSlotOnCopy(_WithServer):
    u"""`--slot X` на КОПИИ: пишется ТОЛЬКО слот X; 401 → откат; 429 → оставлен; рестартов нет."""

    def test_only_named_slot_changes(self):
        d, path = _copy_env({"A": vti.synth_value("Aa1"), "B": vti.synth_value("Bb2")})
        before = _read(path).decode("utf-8")
        srv = self.serve(path, answer={"TB_CLAUDE_TOKEN_C": ENV_429})
        new = vti.synth_value("Cc3")
        res = vti.install_slot("C", new, work=None, stamp="20260925-000006Z")
        self.assertTrue(res["ok"], res["lines"])
        with io.open(path, encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(_val(after, "TB_CLAUDE_TOKEN_C"), new)
        for name in (vti.NAME_ACTIVE, "TB_CLAUDE_TOKEN_A", "TB_CLAUDE_TOKEN_B"):
            self.assertEqual(_val(after, name), _val(before, name))
        self.assertEqual(srv.restarts, 0)
        self.assertEqual(srv.probed, ["TB_CLAUDE_TOKEN_C"])
        self.assertNotIn(new, u"\n".join(res["lines"]))

    def test_401_on_slot_rolls_back(self):
        d, path = _copy_env({"C": vti.synth_value("Old")})
        before = _read(path)
        self.serve(path, answer={"TB_CLAUDE_TOKEN_C": ENV_401})
        res = vti.install_slot("C", vti.synth_value("Cc3"), work=None, stamp="20260925-000007Z")
        self.assertEqual((res["ok"], res["stage"], res["rolled_back"]), (False, "probe", True))
        self.assertEqual(_read(path), before)

    def test_bad_shape_never_reaches_server(self):
        d, path = _copy_env({"C": ""})
        srv = self.serve(path)
        res = vti.install_slot("C", "fake-" + "x" * 100, work=None)
        self.assertEqual(res["stage"], "shape")
        self.assertEqual(srv.calls, [])


class ProbeAllSlotsOnCopy(_WithServer):
    u"""`--probe-slots` меряет действующее и КАЖДЫЙ слот из файла: одна проба на имя, пустой — без вызова."""

    def test_every_slot_letter_is_probed(self):
        d, path = _copy_env({"A": vti.synth_value("Aa1"), "B": vti.synth_value("Bb2"), "D": ""})
        before = _read(path)
        srv = self.serve(path, answer={"TB_CLAUDE_TOKEN_B": ENV_429})
        res = vti.probe_all_slots(work=None)
        self.assertTrue(res["ok"])
        self.assertEqual([r["name"] for r in res["rows"]],
                         [vti.NAME_ACTIVE, "TB_CLAUDE_TOKEN_A", "TB_CLAUDE_TOKEN_B", "TB_CLAUDE_TOKEN_D"])
        self.assertEqual(srv.probed, [r["name"] for r in res["rows"]])
        words = {r["name"]: (r["code"], r["word"]) for r in res["rows"]}
        self.assertEqual(words["TB_CLAUDE_TOKEN_B"], (429, vti.LIMIT))
        self.assertEqual(words["TB_CLAUDE_TOKEN_D"], (None, u"пуст"))
        self.assertEqual((_read(path), srv.restarts), (before, 0))   # перемер ничего не пишет


class ChannelCutAndBadCopy(_WithServer):
    u"""Канал оборвался на записи и копия не сошлась с оригиналом: исход НЕ угадывается.

    Обрыв после записи — файл уже другой, строки `backup=` в выводе нет: без возврата по ожидаемому
    имени копии действующим остался бы НЕПРОВЕРЕННЫЙ вход. Обрыв до записи — копии нет, возвращать
    нечего, и об этом сказано. Копия не сошлась — возврат из неё был бы порчей, его нет."""

    def _cut(self, srv, ran):
        real = srv.ssh_run

        def cut(cmd, stdin_text=None, timeout=None):
            if cmd == vti._REMOTE_BOOT and "already_active" in base64.b64decode(stdin_text).decode("utf-8"):
                if ran:
                    real(cmd, stdin_text, timeout)              # программа отработала, ответ потерян
                return 255, u"ssh не ответил за 90 с"
            return real(cmd, stdin_text, timeout)
        vti.ssh_run = cut

    def test_cut_after_write_restores_by_expected_backup(self):
        d, path = _copy_env({"A": vti.synth_value("Aa1"), "B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path)
        self._cut(srv, ran=True)
        res = vti.use_slot("B", work=None, stamp="20260925-000010Z")
        self.assertEqual((res["ok"], res["stage"], res["rolled_back"]), (False, "write", True))
        self.assertEqual(_read(path), before, u"после обрыва файл не вернулся")
        self.assertEqual(self.backups(d), [os.path.basename(vti.backup_name(path, "20260925-000010Z"))])
        self.assertEqual((srv.restarts, srv.probed), (0, []))
        self.assertIn(u"НЕИЗВЕСТНО", u" ".join(res["lines"]))

    def test_cut_before_write_says_nothing_to_restore(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        before = _read(path)
        srv = self.serve(path)
        self._cut(srv, ran=False)
        res = vti.use_slot("B", work=None, stamp="20260925-000011Z")
        self.assertEqual((res["ok"], res["stage"], res["rolled_back"]), (False, "write", False))
        self.assertEqual(_read(path), before)
        self.assertEqual(self.backups(d), [])
        self.assertEqual(srv.restarts, 0)
        self.assertIn(u"запись до файла не дошла", u" ".join(res["lines"]))

    def test_backup_mismatch_is_never_restored_from(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        srv = self.serve(path)
        real, seen = srv.ssh_run, []

        def mismatch(cmd, stdin_text=None, timeout=None):
            if cmd == vti._REMOTE_BOOT:
                prog = base64.b64decode(stdin_text).decode("utf-8")
                seen.append(prog)
                if "already_active" in prog:
                    return 4, u"slot_filled=1\nalready_active=0\nbackup=%s.bak-x\nerr=backup_mismatch\n" % path
            return real(cmd, stdin_text, timeout)
        vti.ssh_run = mismatch
        res = vti.use_slot("B", work=None, stamp="20260925-000012Z")
        self.assertEqual((res["ok"], res["stage"], res["rolled_back"], srv.restarts), (False, "write", False, 0))
        self.assertFalse(any("restored_ok" in p for p in seen), u"возврат из несошедшейся копии")
        self.assertIn(u"не возвращаем", u" ".join(res["lines"]))

    def test_twin_clean_write_is_not_treated_as_cut(self):
        d, path = _copy_env({"B": vti.synth_value("Bb2")})
        srv = self.serve(path)
        res = vti.use_slot("B", work=None, stamp="20260925-000013Z")
        self.assertEqual((res["ok"], res["stage"], srv.restarts), (True, "done", 1))


class MainRefusals(unittest.TestCase):
    u"""`--slot` и `--use` разом — отказ ДО ssh: неясно, какой слот сделать действующим."""

    def test_slot_and_use_together_refused_without_ssh(self):
        from unittest import mock
        boom = mock.Mock(side_effect=AssertionError("ssh при --slot+--use"))
        clean = dict((k, v) for k, v in os.environ.items()
                     if k not in ("PRETOOL_ASK_MARKER", "PRETOOL_MARKER_TOKEN", "GIT_SERIAL_PC_OWNER"))
        with mock.patch.dict(os.environ, clean, clear=True), mock.patch.object(vti, "ssh_run", boom), \
                mock.patch.object(vti, "_ask_value", side_effect=AssertionError("спросили значение")):
            self.assertEqual(vti.main(["--slot", "B", "--use", "C"]), 2)
        boom.assert_not_called()

    def test_remote_boot_is_unbuffered(self):
        u"""`python3 -u`: при обрыве канала уже напечатанные строки (`backup=`) не застревают в буфере."""
        self.assertEqual(vti._REMOTE_BOOT.split("|")[-1].split(), ["python3", "-u"])


class RemoteWritesAreBinary(unittest.TestCase):
    u"""Замок Windows: каждая запись `os.open` в программах для сервера — с `O_BINARY`.

    Без флага Windows пишет `os.write` в ТЕКСТОВОМ режиме (\\n → \\r\\n), обратное чтение расходится
    с записанным, и тесты «на копии» краснеют в гейте самообновления ПК (на Linux это не видно)."""

    def test_every_os_open_write_carries_o_binary(self):
        for name in ("_RS_APPLY", "_RS_USE"):
            src = getattr(vti, name)
            opens = [l for l in src.splitlines() if "os.open(" in l]
            self.assertTrue(opens, name)
            for l in opens:
                self.assertIn('getattr(os, "O_BINARY", 0)', l, u"%s: %s" % (name, l.strip()))


class LegacyModeRespectsRegistry(unittest.TestCase):
    u"""Старый режим пишет в действующее И в A. Когда A по реестру чей-то — отказ ДО ввода значения."""

    @staticmethod
    def _loader(text):
        import accounts_registry as accounts
        return lambda: (accounts.decode(text, "", "x"), accounts.ST_OK, accounts.ST_ABSENT)

    def test_absent_registry_keeps_legacy(self):
        self.assertEqual(vti.legacy_registry_guard(self._loader(None)), u"")

    def test_slot_a_owned_refuses(self):
        import json
        text = json.dumps({"builders": 3, "accounts": {
            "1": {"profile": u"ОСНОВНОЙ", "slot": "", "label": u"основная"},
            "3": {"profile": "D:\\p3", "slot": "A", "label": u"третья"}}}, ensure_ascii=False)
        words = vti.legacy_registry_guard(self._loader(text))
        self.assertIn(u"№3", words)
        self.assertIn(u"--slot", words)

    def test_slot_a_free_keeps_legacy(self):
        import json
        text = json.dumps({"accounts": {"2": {"profile": "D:\\p2", "slot": "B", "label": u"вторая"}}})
        self.assertEqual(vti.legacy_registry_guard(self._loader(text)), u"")

    def test_broken_registry_refuses(self):
        self.assertIn(u"неизвестно", vti.legacy_registry_guard(self._loader(u"{битый")))

    def test_guard_runs_before_the_value_is_asked(self):
        src = inspect.getsource(vti.main)
        # последний вызов ввода — старый режим (ветка --slot спрашивает значение раньше и сама)
        self.assertLess(src.index("legacy_registry_guard()"), src.rindex("_ask_value()"))


class LegacyProbeIsCareful(_WithServer):
    u"""Прежняя проба вписывания идёт той же аккуратной программой: stderr заглушён ДО чтения файла."""

    def test_legacy_probe_uses_the_probe_program(self):
        d, path = _copy_env({})
        srv = self.serve(path, answer={vti.NAME_ACTIVE: ENV_401})
        word, _why, _raw = vti.probe(path)
        self.assertEqual(word, vti.DENIED)
        self.assertEqual(srv.calls, ["bash -s"])
        self.assertEqual(srv.probed, [vti.NAME_ACTIVE])
        src = inspect.getsource(vti.probe)
        self.assertIn("probe_slot_script(target, NAME_ACTIVE)", src)

    def test_legacy_probe_green(self):
        d, path = _copy_env({})
        self.serve(path)
        self.assertEqual(vti.probe(path)[0], vti.GREEN)


class BuilderGate(unittest.TestCase):
    u"""`--use`/`--slot`/`--probe-slots` и вписывание — ход владельца; из захода строителей отказ ДО ssh."""

    def test_builder_child_refused_without_ssh(self):
        from unittest import mock
        boom = mock.Mock(side_effect=AssertionError("ssh из захода строителей"))
        with mock.patch.dict(os.environ, {"PRETOOL_ASK_MARKER": "/tmp/m"}), \
                mock.patch.object(vti, "ssh_run", boom), mock.patch.object(vti, "_ask_value", boom):
            # запрос значения — тоже ловушка: без неё снятый гейт вёл `--slot` в input() и тест ВИСЕЛ
            # (мутант M6b в фоне: 900 с без вердикта), вместо того чтобы покраснеть
            for argv in (["--use", "A"], ["--slot", "C"], ["--probe-slots"], []):
                with self.subTest(argv=argv):
                    self.assertEqual(vti.main(argv), 3)
        boom.assert_not_called()

    def test_check_still_allowed(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"PRETOOL_ASK_MARKER": "/tmp/m"}), \
                mock.patch.object(vti, "find_env_path", lambda: ("", u"нет адреса")):
            self.assertEqual(vti.main(["--check"]), 2)          # дошёл до разведки, а не отказ по метке


class SlotArg(unittest.TestCase):
    def test_letter_passes_and_garbage_refused(self):
        self.assertEqual(vti.resolve_slot_arg("c")[0], "C")
        self.assertEqual(vti.resolve_slot_arg("CC")[0], "")
        self.assertEqual(vti.resolve_slot_arg("")[0], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
