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


if __name__ == "__main__":
    unittest.main(verbosity=2)
