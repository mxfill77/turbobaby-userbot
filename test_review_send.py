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
import json
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

    def test_cost_is_found_in_stderr_where_the_channel_actually_prints_it(self):
        # Замер живого канала 01.09: при stdout-трубе Codex отдаёт в stdout
        # ТОЛЬКО ответ, а свой протокол со строкой цены — в stderr. Разбор
        # одного stdout молча объявлял бы цену неизвестной при напечатанной.
        verdict, _ = classify_codex(
            returncode=0,
            stdout=LONG_ANSWER,
            stderr="OpenAI Codex v0.151.0\n--------\ncodex\n…\ntokens used\n16 086\n",
            last_message=LONG_ANSWER,
            **_common()
        )
        self.assertEqual((verdict["outcome"], verdict["cost_value"]), ("answered", 16086))

    def test_token_counter_survives_the_separators_the_channel_actually_prints(self):
        # Канал печатает разряды ПРОБЕЛОМ (в живой пробе — «11 706»). Наивный
        # \\d+ прочитал бы 11 и занизил цену канала в тысячу раз.
        self.assertEqual(parse_codex_tokens("tokens used\n11 706\n"), 11706)
        self.assertEqual(parse_codex_tokens("tokens used\n11\u00a0706"), 11706)
        self.assertEqual(parse_codex_tokens("tokens used: 1,234,567"), 1234567)
        # Вторая форма новых версий CLI: пробел здесь разделяет ПОЛЯ, а не
        # разряды, и жадный разбор слепил бы 27393+25171 в одно число.
        self.assertEqual(
            parse_codex_tokens("Token usage: total=27,393 input=25,171 (+ 0 cached) output=2,222"), 27393
        )
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


# ───────────── ожидание Мануса: завершённое пустое против живой работы ─────────────
#
# Повод корпусом, а не мнением (замер 05.09.2026 по `docs/review_inbox/*-manus.md`):
# 25 промахов одной формы, каждый 1802–1807 с, все с последним состоянием
# `completed`, все на ПЕРВОЙ части — 45 095 с (12.53 ч) ожидания пустоты, ответов
# ноль. Ниже — отрицательные пробы ровно на то, чем этот класс держался, и на
# два соседних случая, которые правка обязана НЕ сломать.

_LIVE_EMPTY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "manus_task_completed_empty.live.json")
_TASK_ID = "9mdzM3AoxrRK9vbeJ3pUcc"


def _live_empty_body():
    """Дословное тело живого «завершено и молчит». → str.

    Читается с диска, а не собирается в тесте: сочинённое телo проверяло бы наше
    представление о канале. Это тот самый ответ, на котором прибор простоял
    1802 с (задача 9mdzM3AoxrRK9vbeJ3pUcc, снято из протокола захода).
    """
    with io.open(_LIVE_EMPTY, encoding="utf-8") as fh:
        return fh.read()


def _body_with_reply(text, *, state="running"):
    """То же тело, но ассистент СКАЗАЛ. → str.

    Строится ИЗ живого: наше сообщение с меткой части остаётся на месте
    посимвольно, ответ добавляется следующим — ровно так канал и отвечает.
    """
    obj = json.loads(_live_empty_body())
    obj["status"] = state
    obj["output"].append(
        {
            "id": "assistantMsg",
            "status": "completed",
            "role": "assistant",
            "type": "message",
            "content": [{"type": "output_text", "text": text}],
        }
    )
    return json.dumps(obj, ensure_ascii=False)


class _Clock(object):
    """Часы и сон одной парой: сон ДВИГАЕТ часы, наружу не уходит ничего."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _FakeManus(object):
    """Подставной канал на границе сокета (`manus_http`), а не мимо неё.

    Подменяется САМЫЙ НИЖНИЙ наш слой: всё, что выше (разбор тела, отсечка по
    метке части, счёт опросов), работает боевым кодом. Мок, вставленный выше,
    прятал бы именно ту ветку, ради которой тест и пишется.
    """

    def __init__(self, polls):
        self.polls = list(polls)
        self.gets = 0
        self.posts = 0

    def __call__(self, url, *, key, data=None, method="GET", timeout=None):
        if method == "POST":
            self.posts += 1
            return {
                "target": url,
                "request_sent": True,
                "transport_error": None,
                "status": 200,
                "body": json.dumps({"task_id": _TASK_ID, "task_url": "https://manus.im/app/" + _TASK_ID}),
            }
        self.gets += 1
        item = self.polls[min(self.gets - 1, len(self.polls) - 1)]
        out = {"target": url, "request_sent": True, "transport_error": None, "status": 200, "body": None}
        out.update(item)
        return out


class ManusEmptyAnswerTest(unittest.TestCase):
    """Четыре исхода ожидания, и ни один не выдаёт пустое за ответ."""

    POLL = 10
    WAIT = 1800

    def setUp(self):
        self._real_http = review_send_run.manus_http
        self.addCleanup(setattr, review_send_run, "manus_http", self._real_http)

    def _wait(self, polls, *, parts=9, part=1, wait=None):
        """Прогнать боевое место ожидания на записанных ответах. → (исход, факты, часы)."""
        fake = _FakeManus(polls)
        review_send_run.manus_http = fake
        clock = _Clock()
        facts = {
            "task_id": _TASK_ID,
            "polls": 0,
            "waited_sec": 0,
            "last_state": None,
            "last_poll_error": None,
            "poll_note": None,
            "request_sent": False,
            "transport_error": None,
            "status": None,
            "body": None,
            "idle_error": None,
            "poll_timeout": False,
            "credit_usage": None,
        }
        started = clock()
        outcome = review_send_run._wait_reply(
            facts,
            marker=review_send_run._PART_MARK % (part, parts),
            key="проба",
            base="https://api.manus.ai",
            task_path=review_send_run.DEFAULT_MANUS_TASK_PATH,
            poll=self.POLL,
            deadline=started + (self.WAIT if wait is None else wait),
            started=started,
            clock=clock,
            sleep=clock.sleep,
            tag=" на часть %d/%d" % (part, parts),
        )
        return outcome, facts, clock

    # ─── отрицательная проба 1: завершённое пустое кончается за секунды ───

    def test_completed_without_body_ends_the_wait_in_seconds(self):
        """Живое «completed, сообщений 0» обязано кончить ожидание СРАЗУ.

        Именно здесь и стоял класс: та же самая запись держала прибор 1802 с.
        """
        outcome, facts, _clock = self._wait([{"body": _live_empty_body()}])

        self.assertEqual(outcome, "idle")
        # Число, которым теперь стоит этот случай.
        self.assertEqual(facts["waited_sec"], self.POLL * review_send_run.MANUS_EMPTY_CONFIRM_POLLS)
        self.assertEqual(facts["waited_sec"], 20)
        self.assertEqual(facts["polls"], review_send_run.MANUS_EMPTY_CONFIRM_POLLS)
        # Живой промах стоил 1802 с — снято 1782 с, то есть 98.9%.
        self.assertLess(facts["waited_sec"], 1802 / 50.0)
        # Квитанция канала прочитана, а не додумана.
        self.assertEqual(facts["credit_usage"], 0)
        self.assertTrue(facts["idle_error"])
        self.assertIn("channel_idle", facts["idle_error"])
        self.assertFalse(facts["poll_timeout"], "терминальная пустота — это не исчерпанный потолок")
        self.assertIsNone(facts["transport_error"], "терминальная пустота — это не обрыв транспорта")

    def test_idle_verdict_says_channel_idle_and_charges_by_receipt(self):
        """Ярлык у пустого СВОЙ, а цена — по квитанции канала, а не по ярлыку."""
        _outcome, facts, _clock = self._wait([{"body": _live_empty_body()}])
        verdict, answer = classify_manus(
            request_sent=facts["request_sent"],
            transport_error=facts["transport_error"],
            status=facts["status"],
            body=facts["body"],
            idle_error=facts["idle_error"],
            poll_timeout=facts["poll_timeout"],
            credit_usage=facts["credit_usage"],
            **_common()
        )
        self.assertEqual(verdict["reason"], "channel_idle")
        self.assertNotEqual(verdict["reason"], "answer_lost", "ярлык свалки расширять запрещено")
        self.assertEqual(verdict["outcome"], "unknown")
        self.assertEqual(verdict["cost_value"], 0, "канал напечатал credit_usage=0 — платить нечем")
        self.assertEqual(answer, "", "пустое ответом не становится ни одной веткой")
        self.assertEqual(verdict["answer_chars"], 0)

    # ─── отрицательная проба 2: настоящий ответ через минуту НЕ теряется ───

    def test_real_answer_after_a_minute_is_not_called_empty(self):
        """Шесть опросов молчания (60 с), затем разбор — это ОТВЕТ, а не пустота."""
        silent = {"body": json.dumps({"status": "running", "output": [], "credit_usage": 0})}
        reply = {"body": _body_with_reply("1. Упрощаемо: снять отдельный статус.\n2. Остальное неснимаемо.")}
        outcome, facts, _clock = self._wait([silent] * 6 + [reply])

        self.assertEqual(outcome, "ready")
        self.assertEqual(facts["polls"], 7)
        self.assertEqual(facts["waited_sec"], 70)
        self.assertIsNone(facts["idle_error"], "живой ответ не смеет получить ярлык пустого")
        self.assertFalse(facts["poll_timeout"])

    def test_answer_arriving_together_with_completed_still_wins(self):
        """Задача закончилась ВМЕСТЕ с ответом — это ответ, а не немота.

        Замок на порядок веток внутри ожидания: поменяй их местами — и законный
        разбор был бы выброшен как «канал промолчал».
        """
        outcome, facts, _clock = self._wait([{"body": _body_with_reply(LONG_ANSWER, state="completed")}])
        self.assertEqual(outcome, "ready")
        self.assertIsNone(facts["idle_error"])
        self.assertEqual(facts["polls"], 1)

    def test_single_completed_poll_is_not_enough_to_declare_silence(self):
        """Немота ПОДТВЕРЖДАЕТСЯ, а не объявляется с одного взгляда.

        Гонка «статус терминален раньше, чем дописан текст» наблюдением не
        закрыта, поэтому один опрос немотой не считается: пришедший вторым ответ
        забирает исход себе.
        """
        outcome, facts, _clock = self._wait(
            [{"body": _live_empty_body()}, {"body": _body_with_reply(LONG_ANSWER, state="completed")}]
        )
        self.assertEqual(outcome, "ready")
        self.assertIsNone(facts["idle_error"])

    # ─── отрицательная проба 3: обрыв транспорта — СВОЙ исход, а не «пусто» ───

    def test_transport_break_keeps_its_own_outcome(self):
        """Мёртвый сокет до самого потолка — `answer_lost`, и не `channel_idle`."""
        broken = {"transport_error": "ConnectionResetError: [Errno 104] Connection reset by peer", "status": None}
        outcome, facts, _clock = self._wait([broken], wait=60)

        self.assertEqual(outcome, "timeout")
        self.assertIsNone(facts["idle_error"], "обрыв транспорта пустотой канала не является")
        self.assertIn("Connection reset", facts["transport_error"])

        verdict, _answer = classify_manus(
            request_sent=facts["request_sent"],
            transport_error=facts["transport_error"],
            status=facts["status"],
            body=facts["body"],
            idle_error=facts["idle_error"],
            poll_timeout=facts["poll_timeout"],
            credit_usage=facts["credit_usage"],
            **_common()
        )
        self.assertEqual(verdict["reason"], "poll_timeout")
        self.assertNotEqual(verdict["reason"], "channel_idle")

    def test_transport_break_before_ceiling_is_answer_lost(self):
        """Обрыв, донесённый наверх без исчерпания потолка, остаётся `answer_lost`."""
        verdict, _answer = classify_manus(
            request_sent=True,
            transport_error="TimeoutError: timed out",
            idle_error=None,
            poll_timeout=False,
            **_common()
        )
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("unknown", "answer_lost"))
        self.assertEqual(verdict["cost_value"], 1)

    # ─── «ещё работает»: потолок исчерпан на ЖИВОЙ задаче ───

    def test_still_running_at_the_ceiling_is_its_own_label(self):
        """Нетерминальное состояние до конца бюджета — `poll_timeout`, не пустота."""
        running = {"body": json.dumps({"status": "running", "output": [], "credit_usage": 0})}
        outcome, facts, _clock = self._wait([running], wait=60)

        self.assertEqual(outcome, "timeout")
        self.assertTrue(facts["poll_timeout"])
        self.assertIsNone(facts["idle_error"])
        self.assertEqual(facts["last_state"], "running")

        verdict, _answer = classify_manus(
            request_sent=facts["request_sent"],
            transport_error=facts["transport_error"],
            status=facts["status"],
            body=facts["body"],
            idle_error=facts["idle_error"],
            poll_timeout=facts["poll_timeout"],
            credit_usage=facts["credit_usage"],
            **_common()
        )
        self.assertEqual(verdict["reason"], "poll_timeout")
        # Работа НЕ кончена — квитанция ещё не окончательна, цена консервативна.
        self.assertEqual(verdict["cost_value"], 1)

    # ─── четыре исхода РАЗВЕДЕНЫ ───

    def test_four_outcomes_carry_four_different_labels(self):
        """`answer_lost` перестал быть свалкой: у каждого исхода своё имя."""
        answered, _a = classify_manus(
            request_sent=True, status=200, body=LONG_ANSWER, credit_usage=1, **_common()
        )
        idle, _b = classify_manus(
            request_sent=True, status=200, body=_live_empty_body(),
            idle_error="channel_idle: задача пуста", credit_usage=0, **_common()
        )
        working, _c = classify_manus(
            request_sent=True, status=200, body=None,
            transport_error="poll_timeout: не ответила", poll_timeout=True, **_common()
        )
        broken, _d = classify_manus(
            request_sent=True, transport_error="OSError: сокет умер", **_common()
        )

        labels = [v["reason"] for v in (answered, idle, working, broken)]
        self.assertEqual(labels, ["ok", "channel_idle", "poll_timeout", "answer_lost"])
        self.assertEqual(len(set(labels)), 4, "исходы обязаны РАЗЛИЧАТЬСЯ, а не сливаться")
        self.assertEqual(answered["outcome"], "answered")
        for verdict in (idle, working, broken):
            self.assertEqual(verdict["outcome"], "unknown")
            self.assertEqual(verdict["answer_chars"], 0)

    def test_receipt_names_the_price_of_an_answered_run(self):
        """Цену захода называет квитанция канала, а не наш ярлык."""
        paid, _a = classify_manus(request_sent=True, status=200, body=LONG_ANSWER, credit_usage=7, **_common())
        self.assertEqual(paid["cost_value"], 7)
        # Квитанции нет — прежнее правило захода, и это НЕ ноль.
        silent, _b = classify_manus(request_sent=True, status=200, body=LONG_ANSWER, **_common())
        self.assertEqual(silent["cost_value"], 1)

    def test_credit_usage_reader_tells_zero_from_absent(self):
        """Ноль — ответ квитанции, отсутствие поля — молчание о цене."""
        self.assertEqual(review_send_run.manus_credit_usage(_live_empty_body()), 0)
        self.assertEqual(review_send_run.manus_credit_usage('{"credit_usage": 12}'), 12)
        self.assertIsNone(review_send_run.manus_credit_usage('{"status": "completed"}'))
        self.assertIsNone(review_send_run.manus_credit_usage('{"credit_usage": true}'))
        self.assertIsNone(review_send_run.manus_credit_usage("не json"))

    def test_live_fixture_is_the_case_it_claims_to_be(self):
        """Фикстура обязана быть тем самым случаем, иначе голден врёт."""
        obj = json.loads(_live_empty_body())
        self.assertEqual(obj["status"], "completed")
        self.assertEqual(obj["credit_usage"], 0)
        self.assertEqual([m["role"] for m in obj["output"]], ["user"])
        # Канал замер за 3 секунды — ровно поэтому ждать его 1800 с бессмысленно.
        self.assertEqual(int(obj["updated_at"]) - int(obj["created_at"]), 3)
        state, text, note = review_send_run.manus_output_text(obj)
        self.assertEqual(state, "completed")
        self.assertEqual(text, "")
        self.assertIn("текстовых кусков 0", note)


# ───────────────────── досылка частями: все части в ОДНУ задачу ─────────────────────


class _TaskChannel(object):
    """Подставной канал С ПАМЯТЬЮ ЗАДАЧ на границе сокета (`manus_http`).

    Досылка с `taskId` кладёт сообщение в ту же задачу, опрос отдаёт `output`
    задачи — форма тела та же, что у живого 05.09/17.09. Режимы:

    * ``silent`` — живое поведение с 04.09: `completed`, ассистент молчит, квитанция 0;
    * ``reply``  — поведение 01.09: «ПРИНЯТО» на каждую часть, разбор на последнюю;
    * ``split``  — досылка кодом 200, но в НОВУЮ задачу;
    * ``drop``   — досылка кодом 200 в ту же задачу, а сообщения в ней нет.
    """

    def __init__(self, mode="silent"):
        self.mode = mode
        self.tasks = {}
        self.posts = 0
        self.gets = 0

    def _message(self, task, role, text):
        task["output"].append(
            {
                "id": "m%d" % len(task["output"]),
                "status": "completed",
                "role": role,
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        )

    def _new_task(self):
        tid = "task%d" % (len(self.tasks) + 1)
        credit = 4 if self.mode == "reply" else 0
        self.tasks[tid] = {"id": tid, "status": "completed", "output": [], "credit_usage": credit}
        return tid

    def __call__(self, url, *, key, data=None, method="GET", timeout=None):
        out = {"target": url, "request_sent": True, "transport_error": None, "status": 200}
        if method == "POST":
            self.posts += 1
            payload = json.loads(data.decode("utf-8"))
            tid = payload.get("taskId")
            if not tid or self.mode == "split":
                tid = self._new_task()
            task = self.tasks[tid]
            if not (self.mode == "drop" and payload.get("taskId")):
                self._message(task, "user", payload["prompt"])
                if self.mode == "reply":
                    last = "— ПОСЛЕДНЯЯ." in payload["prompt"].split("\n", 1)[0]
                    self._message(task, "assistant", LONG_ANSWER if last else "ПРИНЯТО")
            out["body"] = json.dumps({"task_id": tid, "task_url": "https://manus.im/app/" + tid})
            return out
        self.gets += 1
        out["body"] = json.dumps(self.tasks[url.rsplit("/", 1)[-1]], ensure_ascii=False)
        return out

    def user_messages(self, tid="task1"):
        return [m for m in self.tasks[tid]["output"] if m["role"] == "user"]


class ManusPartsIntoOneTaskTest(unittest.TestCase):
    """Все части пакета уходят в ОДНУ задачу, и сколько доехало — говорит канал."""

    # 60 строк по 100 знаков: заведомо длиннее потолка сообщения, частей несколько.
    PROMPT = "".join("строка %02d " % i + "ж" * 90 + "\n" for i in range(60))

    def setUp(self):
        self._real_http = review_send_run.manus_http
        self.addCleanup(setattr, review_send_run, "manus_http", self._real_http)

    def _send(self, mode):
        channel = _TaskChannel(mode)
        review_send_run.manus_http = channel
        clock = _Clock()
        facts = review_send_run.send_manus(
            self.PROMPT, key="проба", wait=1800, poll=10, sleep=clock.sleep, clock=clock
        )
        verdict, answer = classify_manus(
            request_sent=facts["request_sent"],
            transport_error=facts["transport_error"],
            status=facts["status"],
            body=facts["body"],
            idle_error=facts["idle_error"],
            poll_timeout=facts["poll_timeout"],
            credit_usage=facts["credit_usage"],
            split_error=facts["split_error"],
            **_common()
        )
        return channel, facts, verdict, answer

    def test_prompt_really_needs_parts(self):
        self.assertGreaterEqual(len(review_send_run.manus_parts(self.PROMPT)), 3)

    def test_silent_channel_receives_every_part_in_one_task(self):
        """Живое поведение с 04.09: немота на части 1 больше НЕ кончает заход.

        До правки тот же вход давал `parts_sent 1` и одну часть в задаче — ровно
        то, что владелец увидел у ревьюера 17.09.
        """
        n = len(review_send_run.manus_parts(self.PROMPT))
        channel, facts, verdict, answer = self._send("silent")

        self.assertEqual(channel.posts, n, "входов в канал ровно столько, сколько частей")
        self.assertEqual(len(channel.tasks), 1, "задача у ревьюера одна")
        self.assertEqual(len(channel.user_messages()), n, "в задаче лежат ВСЕ части")
        self.assertEqual(facts["parts_sent"], n)
        self.assertEqual(facts["parts_seen"], list(range(1, n + 1)), "число доехавших называет канал")
        self.assertEqual(facts["silent_turns"], n - 1)
        self.assertIsNone(facts["split_error"])
        # Протокол помнит код КАЖДОГО POST (создание + досылки) и ответ на досылку.
        self.assertEqual(facts["part_status"], [200] * n, "код ответа на каждую часть")
        self.assertEqual(len(facts["part_replies"]), n - 1, "ответ на каждую досылку")
        # Склейка тел частей в задаче — исходный текст знак в знак.
        heads = [review_send_run._part_head(k, n) for k in range(1, n + 1)]
        bodies = [m["content"][0]["text"][len(h):] for m, h in zip(channel.user_messages(), heads)]
        self.assertEqual("".join(bodies), self.PROMPT)
        # Ревьюер получил всё и промолчал — это немота, а не ответ и не обрезок.
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("unknown", "channel_idle"))
        self.assertEqual(answer, "")
        # Цена немоты по часам захода: по два подтверждающих опроса на часть.
        self.assertEqual(facts["waited_sec"], n * 10 * review_send_run.MANUS_EMPTY_CONFIRM_POLLS)

    def test_refused_follow_up_keeps_its_own_code_in_the_protocol(self):
        """Досылка k/N отбита кодом — протокол называет КОД ЭТОЙ части, а не «< 400 у всех»."""
        base = _TaskChannel("silent")

        def channel(url, *, key, data=None, method="GET", timeout=None):
            if method == "POST" and base.posts == 2:
                base.posts += 1
                return {"target": url, "request_sent": True, "transport_error": None,
                        "status": 429, "body": '{"code":"too_many"}'}
            return base(url, key=key, data=data, method=method, timeout=timeout)

        review_send_run.manus_http = channel
        clock = _Clock()
        facts = review_send_run.send_manus(
            self.PROMPT, key="проба", wait=1800, poll=10, sleep=clock.sleep, clock=clock
        )
        self.assertEqual(facts["part_status"], [200, 200, 429])
        self.assertEqual(facts["parts_sent"], 3)
        self.assertIn("часть 3/", facts["part_error"])
        self.assertEqual(facts["part_replies"][-1], '{"code":"too_many"}')

    def test_replying_channel_still_gets_an_answer(self):
        """Поведение 01.09 («ПРИНЯТО» на каждую часть) не сломано правкой."""
        n = len(review_send_run.manus_parts(self.PROMPT))
        channel, facts, verdict, answer = self._send("reply")

        self.assertEqual(channel.posts, n)
        self.assertEqual(facts["parts_seen"], list(range(1, n + 1)))
        self.assertEqual(facts["silent_turns"], 0)
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("answered", "ok"))
        self.assertEqual(answer.strip(), LONG_ANSWER.strip(), "в ответ не подмешаны «ПРИНЯТО» прежних частей")
        self.assertEqual(verdict["cost_value"], 4)

    def test_part_in_another_task_is_split_not_answer(self):
        """Код 200 на досылку, но канал назвал ДРУГУЮ задачу — признак из ответа канала."""
        channel, facts, verdict, answer = self._send("split")

        self.assertEqual(channel.posts, 2, "дальше второй части слать некуда")
        self.assertIn("ДРУГУЮ задачу", facts["split_error"])
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("unknown", "parts_split"))
        self.assertEqual(answer, "")

    def test_part_accepted_but_absent_is_split(self):
        """Код 200 и тот же `task_id`, а сообщения в задаче нет — часть не доехала."""
        channel, facts, verdict, _answer = self._send("drop")

        self.assertEqual(channel.posts, 2)
        self.assertEqual(len(channel.user_messages()), 1)
        self.assertIn("не видна в задаче", facts["split_error"])
        self.assertEqual((verdict["outcome"], verdict["reason"]), ("unknown", "parts_split"))

    def test_quoted_mark_inside_a_part_is_not_a_delivered_part(self):
        """Метка, ПРОЦИТИРОВАННАЯ внутри текста, частью не считается — только рамка в начале."""
        body = {
            "status": "completed",
            "output": [
                {"role": "user", "content": [{"type": "output_text", "text": review_send_run._part_head(1, 3) + "цитата: [ЧАСТЬ 2/3] — продолжение"}]},
                {"role": "assistant", "content": [{"type": "output_text", "text": review_send_run._part_head(3, 3)}]},
            ],
        }
        self.assertEqual(review_send_run.manus_parts_seen(json.dumps(body, ensure_ascii=False), 3), [1])

    def test_live_body_holds_exactly_part_one(self):
        """Живое тело 05.09 (`completed`, квитанция 0): канал держит часть 1 из 9, и только её."""
        self.assertEqual(review_send_run.manus_parts_seen(_live_empty_body(), 9), [1])


# ───────── ручка модели канала codex (заведена 20.09.2026, ключ Штаба 68z.2009) ─────────
#
# Повод корпусом, а не мнением (замер 20.09 по `docs/review_inbox/*-codex.md`):
# 26 карточек подряд с 12.09 по 20.09 — `КАНАЛ ОТКАЗАЛ` / `channel_error`, и у
# всех одна причина: их сервер отвечает `400 invalid_request_error` на модель
# `gpt-6-astra`, которую выбирает САМ CLI v0.151.0, потому что ни одна из трёх
# боевых дверей (демон · повтор очереди · ручной вызов) имени модели не называла —
# `--codex-model` у всех трёх пуст (default=None), и `-m` в argv не появлялся ни
# разу. Ниже — замок на то, что имя доезжает до argv ЛЮБОЙ дверью, что явный
# флаг по-прежнему сильнее умолчания и что значение лежит в ОДНОМ месте, а не
# переписано трижды.


class _CodexArgvSpy(object):
    """Перехват запуска канала: argv записан, наружу не ушло ни байта."""

    def __init__(self):
        self.argv = None

    def __call__(self, argv, **kw):
        self.argv = list(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")


class CodexModelHandle(unittest.TestCase):
    def _spy(self):
        spy = _CodexArgvSpy()
        real = subprocess.run
        subprocess.run = spy
        self.addCleanup(setattr, subprocess, "run", real)
        return spy

    def _ctx(self, tmp):
        return dict(
            pack_name=PACK_NAME,
            pack_sha256=PACK_SHA,
            prompt_sha256=PROMPT_SHA,
            send_date=DATE,
            root=tmp,
            workdir=tmp,
        )

    def test_model_reaches_argv_when_the_caller_names_nothing(self):
        """Дверь молчит об имени — имя берётся из умолчания полосы, а не у CLI."""
        spy = self._spy()
        with tempfile.TemporaryDirectory() as tmp:
            # `binary=__file__` — заведомо существующий файл: резолвер вернёт его,
            # а запуска не будет вовсе (subprocess.run перехвачен).
            facts = review_send_run.send_codex("текст пакета", root=tmp, workdir=tmp, binary=__file__)
        argv = spy.argv
        self.assertIn("-m", argv, "имени модели нет в строке запуска; argv двери: %r" % (argv,))
        self.assertEqual(argv[argv.index("-m") + 1], review_send_run.DEFAULT_CODEX_MODEL)
        self.assertIn(review_send_run.DEFAULT_CODEX_MODEL, facts["target"],
                      "адрес канала в карточке обязан называть модель: %r" % (facts["target"],))

    def test_explicit_flag_is_stronger_than_the_default(self):
        """`--codex-model` остаётся ручкой: названное имя сильнее умолчания."""
        spy = self._spy()
        with tempfile.TemporaryDirectory() as tmp:
            review_send_run.send_codex("текст", root=tmp, workdir=tmp, binary=__file__, model="имя-из-ручки")
        argv = spy.argv
        self.assertEqual(argv[argv.index("-m") + 1], "имя-из-ручки")
        self.assertNotIn(review_send_run.DEFAULT_CODEX_MODEL, argv)

    def test_daemon_door_carries_the_model_too(self):
        """Дверь 1: демон собирает ровно `_ChannelArgs` с `codex_model=None`."""
        import review_auto_run

        args = review_auto_run._ChannelArgs(30, review_send_run.DEFAULT_KEY_ENV)
        self.assertIsNone(args.codex_model, "проверяем именно случай «демон имени не называет»")
        args.codex_bin = __file__
        spy = self._spy()
        with tempfile.TemporaryDirectory() as tmp:
            review_send_run.run_channel("codex", "текст пакета", self._ctx(tmp), args)
        self.assertIn("-m", spy.argv, "argv двери демона: %r" % (spy.argv,))
        self.assertEqual(spy.argv[spy.argv.index("-m") + 1], review_send_run.DEFAULT_CODEX_MODEL)

    def test_retry_door_names_no_model_and_still_gets_one(self):
        """Дверь 2: повтор очереди зовёт ТОТ ЖЕ отправщик без флага — имя приходит умолчанием."""
        import review_outbox_queue_run

        argv = review_outbox_queue_run.retry_argv(
            {"kind": "resend", "pack": "docs/review_outbox/x.md", "channel": "codex"},
            root=os.path.dirname(os.path.abspath(__file__)),
        )
        self.assertNotIn("--codex-model", argv, "повтор флага не несёт — и не обязан: %r" % (argv,))
        self.assertTrue(argv[1].endswith(review_outbox_queue_run.SENDER))
        self.assertTrue(review_send_run.DEFAULT_CODEX_MODEL.strip(), "пустое умолчание вернуло бы выбор CLI")

    def test_value_lives_in_exactly_one_place(self):
        """Имя написано ОДИН раз и только у отправщика: три копии разъехались бы молча."""
        here = os.path.dirname(os.path.abspath(__file__))
        name = review_send_run.DEFAULT_CODEX_MODEL
        with io.open(review_send_run.__file__, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read().count('"%s"' % name), 1)
        for rel in ("review_auto_run.py", "review_outbox_queue_run.py", "pc_orchestrator.py"):
            with io.open(os.path.join(here, rel), "r", encoding="utf-8") as fh:
                self.assertNotIn(name, fh.read(), "%s держит вторую копию имени модели" % rel)


if __name__ == "__main__":
    unittest.main()
