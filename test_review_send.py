"""Юниты отправщика пакетов второго мнения (ступень 2 ревью-контура).

Все фикстуры живут в свежем :class:`tempfile.TemporaryDirectory`; ни боевой
`.env`, ни логи, ни переписка клиентов, ни Bridge, ни мозг здесь не читаются,
и НИ ОДИН тест не ходит наружу: единственные сокеты — петля 127.0.0.1 на
заведомо свободном порту (отрицательная проба «недоступный адрес»), и она по
определению никуда не уезжает.

Запуск: ``python -m unittest test_review_send -v``
"""

import ast
import io
import os
import shutil
import socket
import subprocess
import tempfile
import unittest

import review_send
import review_send_run
from review_send import (
    ANSWER_MIN_CHARS,
    CHANNELS,
    OUTCOMES,
    ReviewSendError,
    answer_filename,
    build_prompt,
    classify_codex,
    classify_manus,
    extract_manus_answer,
    index_line,
    outbound_violations,
    parse_codex_tokens,
    refused_by_guard,
    render_answer,
)

PACK_NAME = "2026-09-01-demo-case.md"
PACK_SHA = "a" * 64
PROMPT_SHA = "b" * 64
DATE = "2026-09-01"

# Ответ длиннее пола осмысленности: тесты про ФОРМУ не должны спотыкаться о
# длину, а тесты про длину задают её сами.
LONG_ANSWER = "1. Упрощаемо: убрать отдельный статус. " * 12


def _common(**over):
    base = dict(
        channel_target="проба",
        pack_name=PACK_NAME,
        pack_sha256=PACK_SHA,
        prompt_sha256=PROMPT_SHA,
        send_date=DATE,
    )
    base.update(over)
    return base


def _free_port():
    """Порт, на котором ТОЧНО никто не слушает. → int.

    Берём у ядра свободный и сразу отпускаем: константа вроде 9 могла бы
    оказаться занятой чужой службой, и отрицательная проба «адрес недоступен»
    тихо превратилась бы в положительную.
    """
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="review_send_")
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def root(self):
        return self._tmp

    def write(self, rel, text):
        full = os.path.join(self._tmp, rel.replace("/", os.sep))
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with io.open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return full

    def read(self, path):
        with io.open(path, "r", encoding="utf-8") as fh:
            return fh.read()


# ───────────────────────────── исходящий текст ─────────────────────────────


class Prompt(_Base):
    def test_prompt_carries_pack_verbatim_and_is_deterministic(self):
        pack = "# ПАКЕТ\n\nтело\n\n1. Что упрощаемо?\n"
        first = build_prompt(pack)
        self.assertIn("тело", first)
        self.assertIn("1. Что упрощаемо?", first)
        self.assertEqual(first, build_prompt(pack))

    def test_prompt_forbids_running_anything(self):
        prompt = build_prompt("тело")
        self.assertIn("НИЧЕГО НЕ ЗАПУСКАЙ", prompt)
        self.assertIn("неизвестно", prompt)

    def test_empty_pack_is_refused_structurally(self):
        for bad in ("", "   \n", None, 17):
            with self.assertRaises(ReviewSendError):
                build_prompt(bad)


class OutboundGuard(_Base):
    def test_clean_text_passes(self):
        self.assertEqual(outbound_violations("обычный пакет про очередь и пороги"), [])

    def test_relative_windows_command_is_not_a_unc_path(self):
        # Живой пакет из лотка несёт строку прогона тестов, у которой слэши
        # удвоены экранированием JSON. До 01.09 правило UNC ловило её и
        # задерживало КАЖДЫЙ пакет с командой прогона — то есть все.
        text = '"command": "venv\\\\Scripts\\\\python.exe -m unittest test_review_pack"'
        self.assertEqual(outbound_violations(text), [])

    def test_absolute_paths_are_caught(self):
        for text in ("лежит в D:\\turbobaby-bot\\suggest.py", "на сервере /root/manager-bot/bot.py"):
            self.assertTrue(outbound_violations(text), text)

    def test_secret_shapes_are_caught(self):
        samples = (
            "ключ sk-ant-api03-" + "x" * 30,
            "токен ghp_" + "A" * 30,
            "api_key = " + "q" * 24,
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
        )
        for text in samples:
            self.assertTrue(outbound_violations(text), text)

    def test_word_secret_alone_is_not_a_violation(self):
        # Пакет второго мнения САМ обсуждает запрет имён и печатает эти слова.
        # Правило по слову задержало бы его целиком, а настоящий ключ без
        # подписи пропустило: судим по форме значения, а не по словарю.
        text = "расписка лежит по пути со словом secret, поэтому источник в `excluded`; совпало `token`"
        self.assertEqual(outbound_violations(text), [])

    def test_client_contacts_are_caught(self):
        for text in ("пишите klient@example.com", "телефон +66 812 345 678", "ник @some_client_name"):
            self.assertTrue(outbound_violations(text), text)

    def test_sample_is_truncated_so_the_guard_does_not_leak_what_it_caught(self):
        found = outbound_violations("ключ sk-ant-api03-" + "z" * 60)
        self.assertEqual(len(found), 1)
        self.assertLessEqual(len(found[0]["sample"]), 25)
        self.assertIn("…", found[0]["sample"])


# ───────────────────────────── канал Codex ─────────────────────────────


class CodexVerdicts(_Base):
    def test_missing_binary_is_a_loud_refusal_not_silence(self):
        verdict, answer = classify_codex(launch_error="не найден codex", **_common())
        self.assertEqual(verdict["outcome"], "refused")
        self.assertEqual(verdict["reason"], "channel_unreachable")
        self.assertEqual(verdict["title"], "КАНАЛ ОТКАЗАЛ")
        self.assertEqual(answer, "")

    def test_timeout_is_refusal(self):
        verdict, _ = classify_codex(timed_out=True, stdout="частичный вывод", **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "timeout"))

    def test_nonzero_returncode_is_refusal_even_with_a_body(self):
        verdict, answer = classify_codex(returncode=1, stderr="stream error", last_message=LONG_ANSWER, **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "channel_error"))
        # Тело сохраняется доказательством, но исходом остаётся отказ.
        self.assertEqual(answer, LONG_ANSWER)
        self.assertGreater(verdict["answer_chars"], 0)

    def test_empty_answer_is_refusal(self):
        verdict, _ = classify_codex(returncode=0, last_message="   \n", **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "empty_answer"))

    def test_short_answer_is_refusal(self):
        verdict, _ = classify_codex(returncode=0, last_message="ок", **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "answer_too_short"))

    def test_missing_last_message_file_with_rc0_is_refusal(self):
        verdict, _ = classify_codex(returncode=0, stdout="что-то печаталось", last_message=None, **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "no_last_message"))

    def test_unreadable_answer_is_unknown_not_refusal(self):
        # Третий исход обязателен: работа могла быть сделана и оплачена, мы её
        # просто не видим. «Отказал» здесь так же неверно, как «ответил».
        verdict, _ = classify_codex(returncode=0, last_message_error="OSError: занят", **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("unknown", "answer_unreadable"))
        self.assertEqual(verdict["title"], "НЕИЗВЕСТНО")

    def test_good_answer_is_answered_with_cost(self):
        verdict, answer = classify_codex(
            returncode=0, stdout="tokens used\n24 118\n", last_message=LONG_ANSWER, **_common()
        )
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("answered", "ok"))
        self.assertEqual(verdict["title"], "ОТВЕТ ПОЛУЧЕН")
        self.assertEqual((verdict["cost_unit"], verdict["cost_value"]), ("tokens", 24118))
        self.assertEqual(answer, LONG_ANSWER)

    def test_token_counter_survives_the_separators_the_channel_actually_prints(self):
        # Канал печатает разряды ПРОБЕЛОМ (в живой пробе — «11 706»). Наивный
        # \\d+ прочитал бы 11 и занизил цену канала в тысячу раз.
        self.assertEqual(parse_codex_tokens("tokens used\n11 706\n"), 11706)
        self.assertEqual(parse_codex_tokens("tokens used\n11\u00a0706"), 11706)
        self.assertEqual(parse_codex_tokens("tokens used: 1,234,567"), 1234567)
        self.assertIsNone(parse_codex_tokens("ничего про цену"))
        self.assertIsNone(parse_codex_tokens(None))


# ───────────────────────────── канал Manus ─────────────────────────────


class ManusVerdicts(_Base):
    def test_no_key_refuses_before_the_socket(self):
        verdict, _ = classify_manus(credentials_present=False, key_env_name="ИМЯ", **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "no_credentials"))
        self.assertEqual(verdict["cost_value"], 0)

    def test_unreachable_before_send_is_refusal(self):
        verdict, _ = classify_manus(transport_error="ConnectionRefusedError", request_sent=False, **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "channel_unreachable"))
        self.assertEqual(verdict["cost_value"], 0)

    def test_break_after_send_is_unknown_not_refusal(self):
        # Граница «расписка ≠ судьба»: запрос мог быть принят и оплачен.
        # Назвать это отказом значит пригласить повторную отправку.
        verdict, _ = classify_manus(transport_error="TimeoutError", request_sent=True, **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("unknown", "answer_lost"))
        self.assertEqual(verdict["cost_value"], 1)

    def test_http_error_is_refusal(self):
        verdict, _ = classify_manus(request_sent=True, status=503, body='{"error":"unavailable"}', **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "http_503"))

    def test_2xx_without_text_is_refusal(self):
        verdict, _ = classify_manus(request_sent=True, status=200, body="{}", **_common())
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "empty_body"))

    def test_2xx_with_task_id_only_is_unknown(self):
        verdict, _ = classify_manus(
            request_sent=True, status=200, body='{"task_id":"t-1","task_url":"https://x/y"}', **_common()
        )
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("unknown", "accepted_no_answer"))

    def test_channel_declared_truncation_is_refusal(self):
        verdict, _ = classify_manus(
            request_sent=True,
            status=200,
            body='{"answer": %s, "finish_reason": "length"}' % _json_str(LONG_ANSWER),
            **_common()
        )
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "truncated_answer"))

    def test_good_answer_is_answered(self):
        verdict, answer = classify_manus(
            request_sent=True, status=200, body='{"answer": %s}' % _json_str(LONG_ANSWER), **_common()
        )
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("answered", "ok"))
        self.assertEqual(answer, LONG_ANSWER)
        self.assertEqual(verdict["cost_unit"], "request")

    def test_extractor_is_honest_when_it_finds_nothing(self):
        answer, note = extract_manus_answer('{"foo": 1}')
        self.assertIsNone(answer)
        self.assertIn("ни одно из полей", note)
        self.assertEqual(extract_manus_answer(None)[0], None)
        self.assertEqual(extract_manus_answer("  ")[0], None)
        self.assertEqual(extract_manus_answer("простой текст")[0], "простой текст")


def _json_str(text):
    import json

    return json.dumps(text, ensure_ascii=False)


# ───────────────────────────── адрес, рендер, индекс ─────────────────────────────


class AddressAndRender(_Base):
    def test_answer_filename_holds_date_pack_and_channel(self):
        name = answer_filename(PACK_NAME, "codex", DATE)
        self.assertEqual(name, "2026-09-01-2026-09-01-demo-case-codex.md")
        self.assertTrue(name.startswith(DATE))
        self.assertIn("demo-case", name)
        self.assertTrue(name.endswith("-codex.md"))

    def test_answer_filename_rejects_bad_input(self):
        with self.assertRaises(ReviewSendError):
            answer_filename(PACK_NAME, "нетакойканал", DATE)
        with self.assertRaises(ReviewSendError):
            answer_filename(PACK_NAME, "codex", "01.09.2026")

    def test_render_puts_the_do_not_execute_ban_first(self):
        verdict, answer = classify_codex(returncode=0, last_message=LONG_ANSWER, **_common())
        text = render_answer(verdict, answer, pack_rel="docs/review_outbox/%s" % PACK_NAME)
        head = "\n".join(text.splitlines()[:5])
        self.assertIn("НЕ ИСПОЛНЯТЬ", head)
        self.assertIn("задач не порождают", text)

    def test_render_keeps_the_answer_verbatim_even_with_fences_inside(self):
        answer = "1. код:\n```\nprint(1)\n```\n2. ещё\n" + LONG_ANSWER
        verdict, _ = classify_codex(returncode=0, last_message=answer, **_common())
        text = render_answer(verdict, answer, pack_rel="x.md")
        self.assertIn("```\nprint(1)\n```", text)
        self.assertIn("````", text)  # ограда длиннее внутренней

    def test_render_of_a_refusal_says_so_and_has_no_body(self):
        verdict, answer = classify_codex(launch_error="нет бинаря", **_common())
        text = render_answer(verdict, answer, pack_rel="x.md")
        self.assertIn("КАНАЛ ОТКАЗАЛ", text)
        self.assertIn("тела ответа нет", text)

    def test_index_line_is_one_line_and_fits_the_journal_index_rule(self):
        verdict, answer = classify_codex(
            returncode=0, stdout="tokens used 24 118", last_message=LONG_ANSWER, **_common()
        )
        line = index_line(verdict, "docs/review_inbox/%s" % answer_filename(PACK_NAME, "codex", DATE))
        self.assertNotIn("\n", line)
        # Журнал — индекс, а не хранилище тел: писатель выносит тело строки
        # длиннее 600 знаков в отдельный файл. Строка-индекс обязана быть
        # короче порога, иначе ссылка на ответ уедет в вынесенное тело.
        self.assertLess(len(line), 600)
        self.assertIn("ОТВЕТ ПОЛУЧЕН", line)

    def test_guard_refusal_names_the_kind_and_sends_nothing(self):
        violations = outbound_violations("ключ sk-ant-api03-" + "z" * 30)
        verdict = refused_by_guard(
            channel="codex",
            pack_name=PACK_NAME,
            pack_sha256=PACK_SHA,
            prompt_sha256=PROMPT_SHA,
            send_date=DATE,
            violations=violations,
        )
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("refused", "outbound_guard"))
        self.assertEqual(verdict["cost_value"], 0)
        self.assertIn("наружу не отправлено ничего", verdict["detail"])
        self.assertNotIn("sk-ant", verdict["detail"])


# ───────────────────────────── руки ─────────────────────────────


class ChildEnvScrub(_Base):
    def test_secret_shaped_names_never_reach_the_child(self):
        base = {
            "PATH": "C:/bin",
            "ANTHROPIC_API_KEY": "x",
            "OPENAI_API_KEY": "y",
            "BRIDGE_TOKEN": "z",
            "SOME_SECRET": "s",
            "USERPROFILE": "C:/Users/x",
        }
        out = review_send_run.child_env(base)
        self.assertEqual(sorted(out), ["PATH", "USERPROFILE"])

    def test_keep_list_is_honoured(self):
        out = review_send_run.child_env({"PATH": "p", "SOME_KEY": "k"}, keep=("SOME_KEY",))
        self.assertIn("SOME_KEY", out)


class NegativeAddress(_Base):
    """Отрицательная проба задания: заведомо недоступный адрес обязан дать ОТКАЗ.

    Она гоняет НАСТОЯЩИЕ руки (`review_send_run.main`), а не заглушку: смысл
    пробы в том, что отправщик рапортует, а не молчит, — а молчит или нет,
    видно только на полном пути «отправить → классифицировать → написать файл».
    """

    def _pack(self):
        return self.write(
            "docs/review_outbox/%s" % PACK_NAME,
            "# ПАКЕТ ВТОРОГО МНЕНИЯ — demo\n\nтело пакета\n\n1. Что упрощаемо?\n",
        )

    def test_unreachable_codex_binary_and_dead_http_port_both_refuse(self):
        pack = self._pack()
        port = _free_port()
        key_name = "REVIEW_SEND_TEST_KEY_2026"
        os.environ[key_name] = "dummy-not-a-real-key"
        self.addCleanup(os.environ.pop, key_name, None)

        code = review_send_run.main(
            [
                "--pack", pack,
                "--root", self.root(),
                "--date", DATE,
                "--timeout", "20",
                "--codex-bin", os.path.join(self.root(), "нет-такого-codex.exe"),
                "--manus-base", "http://127.0.0.1:%d" % port,
                "--key-env", key_name,
            ]
        )
        self.assertEqual(code, 3, "недоступный адрес обязан дать ненулевой код, а не тихий успех")

        inbox = os.path.join(self.root(), "docs", "review_inbox")
        written = sorted(os.listdir(inbox))
        self.assertEqual(len(written), 2, "оба канала обязаны оставить файл-след: %r" % written)
        for name in written:
            text = self.read(os.path.join(inbox, name))
            self.assertIn("КАНАЛ ОТКАЗАЛ", text, name)
            self.assertIn("channel_unreachable", text, name)
            self.assertIn("тела ответа нет", text, name)

    def test_dry_run_writes_nothing_at_all(self):
        pack = self._pack()
        code = review_send_run.main(["--pack", pack, "--root", self.root(), "--date", DATE, "--dry"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.isdir(os.path.join(self.root(), "docs", "review_inbox")))

    def test_guard_stops_the_send_before_any_channel(self):
        pack = self.write(
            "docs/review_outbox/%s" % PACK_NAME,
            "# ПАКЕТ\n\nключ sk-ant-api03-%s\n" % ("z" * 30),
        )
        code = review_send_run.main(
            ["--pack", pack, "--root", self.root(), "--date", DATE, "--codex-bin", "нет-такого", "--channel", "codex"]
        )
        self.assertEqual(code, 3)
        text = self.read(
            os.path.join(self.root(), "docs", "review_inbox", answer_filename(PACK_NAME, "codex", DATE))
        )
        self.assertIn("outbound_guard", text)
        self.assertNotIn("sk-ant-api03", text)

    def test_missing_pack_is_an_input_error_without_files(self):
        code = review_send_run.main(["--pack", os.path.join(self.root(), "нет.md"), "--root", self.root()])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.isdir(os.path.join(self.root(), "docs", "review_inbox")))

    def test_unknown_channel_name_is_rejected(self):
        pack = self._pack()
        code = review_send_run.main(["--pack", pack, "--root", self.root(), "--channel", "нетакой"])
        self.assertEqual(code, 2)


# ───────────────────────────── чистота ─────────────────────────────


class NoSideEffects(_Base):
    def test_classification_makes_no_network_no_subprocess_no_write(self):
        before = sorted(os.listdir(self.root()))

        def _boom_socket(*a, **kw):
            raise AssertionError("разбор ответа не имеет права ходить в сеть")

        def _boom_popen(*a, **kw):
            raise AssertionError("разбор ответа не имеет права звать подпроцесс")

        real_socket, real_popen = socket.socket, subprocess.Popen
        socket.socket, subprocess.Popen = _boom_socket, _boom_popen
        try:
            prompt = build_prompt("# ПАКЕТ\n\nтело\n")
            self.assertEqual(outbound_violations(prompt), [])
            verdict, answer = classify_codex(returncode=0, last_message=LONG_ANSWER, **_common())
            render_answer(verdict, answer, pack_rel="x.md")
            index_line(verdict, "y.md")
        finally:
            socket.socket, subprocess.Popen = real_socket, real_popen

        self.assertEqual(verdict["outcome"], "answered")
        self.assertEqual(sorted(os.listdir(self.root())), before)

    def test_module_calls_no_clock_no_env_no_randomness(self):
        # День отправки — ПОЛЕ ВХОДА. Часы в чистом модуле означали бы, что
        # вердикт вчерашней отправки завтра пересобирается другим и его sha256
        # перестаёт быть адресом. Судим по ВЫЗОВАМ (AST), а не по подстроке:
        # греп нашёл бы `getenv` в докстринге, где он назван запрещённым, и
        # объявил модуль виновным за собственное правило.
        with io.open(review_send.__file__, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        forbidden = {"now", "utcnow", "today", "time", "time_ns", "monotonic", "random", "getenv", "urlopen", "run"}
        called = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr)
            elif isinstance(func, ast.Name):
                called.add(func.id)
        self.assertEqual(called & forbidden, set(), "чистый модуль зовёт недетерминированный источник")

    def test_three_outcomes_and_two_channels_are_the_whole_contract(self):
        self.assertEqual(OUTCOMES, ("answered", "refused", "unknown"))
        self.assertEqual(CHANNELS, ("codex", "manus"))
        self.assertGreaterEqual(ANSWER_MIN_CHARS, 100)


if __name__ == "__main__":
    unittest.main()
