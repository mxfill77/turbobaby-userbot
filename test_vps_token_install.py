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
    тем же интерпретатором, что и тест, и правят КОПИЮ по-настоящему. Подменены только три вещи,
    которых у копии нет: список процессов (`busy`), рестарт юнита (счётчик) и ответ поставщика
    (`answer` по имени входа). Проба `bash -s` читает копию и сравнивает значения так же, как
    программа пробы на сервере, — наружу из неё уходят только признаки."""

    def __init__(self, target, answer=None, busy=0, busy_after=None, restart_ok=True):
        self.target, self.busy, self.busy_after, self.restart_ok = target, busy, busy_after, restart_ok
        self.answer = answer or {}
        self.calls, self.restarts, self.preflights, self.probed = [], 0, 0, []

    def ssh_run(self, cmd, stdin_text=None, timeout=None):
        self.calls.append(cmd)
        if cmd == vti._REMOTE_BOOT:
            program = base64.b64decode(stdin_text).decode("utf-8")
            is_pre = "busy_claude" in program
            if is_pre:
                self.preflights += 1
                # список процессов копии не принадлежит: /proc подменяем пустым (на Windows его нет)
                program = program.replace("os.listdir(\"/proc\")", "[]")
            p = subprocess.run([sys.executable, "-"], input=program.encode("utf-8"),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
                               env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            out = p.stdout.decode("utf-8", "replace")
            if is_pre:
                busy = self.busy if (self.busy_after is None or self.preflights == 1) else self.busy_after
                out = re.sub(r"busy_claude=\d+", "busy_claude=%d" % busy, out)
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
            return 0, _probe_out("1" if val == act else "0", rc=rc, envelope=env_text)
        if cmd.startswith("systemctl restart"):
            self.restarts += 1
            if not self.restart_ok:
                return 1, "ActiveState=failed\n"
            return 0, "MainPID=4242\nActiveState=active\nSubState=running\nNRestarts=0\n"
        return 1, u"неожиданная команда в тесте: %s" % cmd


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


class SlotArg(unittest.TestCase):
    def test_letter_passes_and_garbage_refused(self):
        self.assertEqual(vti.resolve_slot_arg("c")[0], "C")
        self.assertEqual(vti.resolve_slot_arg("CC")[0], "")
        self.assertEqual(vti.resolve_slot_arg("")[0], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
