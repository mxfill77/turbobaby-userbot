# -*- coding: utf-8 -*-
"""
test_rc_auth_detect.py — голдены детектора ПРОТУХШЕЙ АВТОРИЗАЦИИ (вариант «а» артефакта
docs/artifacts/2026-07-29-rc-token-staleness-prevention.md).

Голдены гоняются по ФИКСТУРЕ `fixtures/rc_server_auth_revoked.live.log`, снятой с живого лога
(происхождение каждой строки названо в шапке фикстуры), а не по идеализированным строкам.
Три вещи, которые обязаны выполняться одновременно:
  • настоящий отказ (дословная фраза + падение за секунды) → рестарт;
  • разовый 401, переживший рефреш, и `401` внутри JSON → БЕЗ холостого рестарта;
  • усечение лога (сторож обнуляет его при подъёме ветки) → улики сбрасываются, повтора нет.
"""

import io
import os
import unittest

import parse_outcome
import rc_auth_detect as ad

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "rc_server_auth_revoked.live.log")
IDLE_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "rc_server_idle_poll.live.log")

# Дословные строки живого лога (в фикстуре — они же).
LINE_REVOKED = ("2026-07-29T05:03:43.827Z [DEBUG] [code-session] Get "
                "session_01VL18wKrkBWsKVwmifPHQ7c failed 401: OAuth access token has been revoked.")
LINE_EXIT_FAST = ("2026-07-29T05:03:46.068Z [DEBUG] [bridge:session] … workId=cse_01VL18wK… "
                  "exited status=failed duration=2s")
LINE_REFRESH_OK = ("2026-07-29T05:03:46.478Z [DEBUG] [bridge:api] StopWork: 401 received, "
                   "attempting token refresh")
LINE_JSON_NOISE = ("2026-07-29T05:03:23.401Z [DEBUG] [auto-mode] context comparison: "
                   "mainLoopTokens=[REDACTED] estimated_tokens=401")


def fixture_lines():
    with io.open(FIXTURE, encoding="utf-8") as f:
        return f.read().splitlines()


def idle_lines():
    """Живой лог ветки В ПРОСТОЕ без строк-комментариев фикстуры (в настоящем логе их нет)."""
    with io.open(IDLE_FIXTURE, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if not ln.startswith("#")]


class TestFixtureIsReal(unittest.TestCase):
    """Фикстура обязана ехать в репозиторий и нести ЖИВОЙ формат, а не пересказ."""

    def test_fixture_exists(self):
        self.assertTrue(os.path.isfile(FIXTURE), "фикстура голдена обязана лежать в репозитории")

    def test_fixture_carries_both_cases(self):
        text = "\n".join(fixture_lines())
        self.assertIn(ad.REVOKED_PHRASE, text)          # настоящий отказ
        self.assertIn("Token refreshed, retrying request", text)   # пережитый рефрешем 401
        self.assertIn("estimated_tokens=401", text)     # мусор, на котором врёт голый греп


class TestRealFailure(unittest.TestCase):
    """Настоящий отказ: дословная фраза + падение той же сессии за секунды."""

    def test_fixture_triggers_restart(self):
        st = ad.feed(fixture_lines(), ad.new_state())
        self.assertEqual(st["failures"], 1)
        self.assertTrue(ad.should_restart(st))

    def test_pair_matched_by_session_id(self):
        st = ad.feed([LINE_REVOKED, LINE_EXIT_FAST], ad.new_state())
        self.assertEqual(st["failures"], 1)

    def test_describe_names_the_literal_phrase(self):
        st = ad.feed([LINE_REVOKED, LINE_EXIT_FAST], ad.new_state())
        self.assertIn(ad.REVOKED_PHRASE, ad.describe(st))


class TestNoFalseRestart(unittest.TestCase):
    """Всё, что НЕ должно рестартовать сервер (цена ложного рестарта — смена Environment)."""

    def test_refresh_recovered_401_is_silent(self):
        lines = [LINE_REFRESH_OK,
                 "2026-07-29T05:03:46.489Z [DEBUG] [bridge:api] StopWork: Token refreshed, retrying request",
                 "2026-07-29T05:03:47.362Z [DEBUG] [bridge:api] POST .../stop -> 200"]
        st = ad.feed(lines, ad.new_state())
        self.assertFalse(ad.should_restart(st))

    def test_json_noise_401_is_silent(self):
        """78 % совпадений голого грепа по `401` — мусор внутри JSON (замер артефакта, п.4)."""
        st = ad.feed([LINE_JSON_NOISE] * 20, ad.new_state())
        self.assertEqual(st["failures"], 0)

    def test_phrase_without_action_is_silent(self):
        """Фраза есть, а сессия НЕ упала (мост отрефрешил и поехал) → рестарта нет: судим
        по ДЕЙСТВИЮ, а не по подстроке (правило среды №5)."""
        st = ad.feed([LINE_REVOKED] + ["2026-07-29T05:03:50.000Z [DEBUG] всё хорошо"] * 5,
                     ad.new_state())
        self.assertFalse(ad.should_restart(st))

    def test_slow_failure_is_not_auth(self):
        """Сессия упала, но через 40 минут — это не отказ токена (тот убивает за 2–3 с)."""
        slow = LINE_EXIT_FAST.replace("duration=2s", "duration=2400s")
        st = ad.feed([LINE_REVOKED, slow], ad.new_state())
        self.assertEqual(st["failures"], 0)

    def test_failure_of_another_session_not_paired(self):
        """В логе параллельно живут разные сессии: чужое падение НЕ засчитывается отказу."""
        other = LINE_EXIT_FAST.replace("cse_01VL18wK…", "cse_09ZZZZZZZZ")
        st = ad.feed([LINE_REVOKED, other], ad.new_state())
        self.assertEqual(st["failures"], 0)

    def test_pending_expires_after_window(self):
        """Фраза без падения «протухает»: далёкое падение той же сессии не воскрешает улику."""
        filler = ["2026-07-29T05:04:00.000Z [DEBUG] шум"] * (ad.PAIR_WINDOW + 5)
        st = ad.feed([LINE_REVOKED] + filler + [LINE_EXIT_FAST], ad.new_state())
        self.assertEqual(st["failures"], 0)


class TestThresholdKnob(unittest.TestCase):
    def test_two_failures_needed_when_configured(self):
        st = ad.feed([LINE_REVOKED, LINE_EXIT_FAST], ad.new_state())
        self.assertTrue(ad.should_restart(st, needed=1))
        self.assertFalse(ad.should_restart(st, needed=2))
        st = ad.feed([LINE_REVOKED, LINE_EXIT_FAST], st)
        self.assertEqual(st["failures"], 2)
        self.assertTrue(ad.should_restart(st, needed=2))

    def test_default_is_one_failure(self):
        self.assertEqual(ad.FAILS_NEEDED, 1)


class TestIdMatch(unittest.TestCase):
    """Сверка по общему префиксу: живой id в снятых образцах местами усечён многоточием."""

    def test_truncated_id_matches_full(self):
        self.assertTrue(ad.ids_match("01VL18wKrkBWsKVwmifPHQ7c", "01VL18wK"))
        self.assertTrue(ad.ids_match("01VL18wK", "01VL18wKrkBWsKVwmifPHQ7c"))

    def test_different_ids_do_not_match(self):
        self.assertFalse(ad.ids_match("01VL18wKrkBWsKVwmifPHQ7c", "01Xa5KTXp3RbcmFFXqeoAcJx"))

    def test_too_short_never_matches(self):
        self.assertFalse(ad.ids_match("01VL", "01VL"))


class TestOffsetAndTruncation(unittest.TestCase):
    """Минус варианта «а», названный артефактом: лог обнуляется при подъёме ветки — детектор
    обязан держать смещение и переживать усечение, иначе рестартанёт по мёртвой улике."""

    def setUp(self):
        self.size = [0]
        self.body = [""]

    def _sizer(self, p):
        return self.size[0]

    def _opener(self, p):
        return io.StringIO(self.body[0])

    def _set(self, text):
        self.body[0] = text
        self.size[0] = len(text)

    def test_offset_advances_and_lines_not_reread(self):
        st = ad.new_state()
        self._set("первая\nвторая\n")
        st = ad.scan("x", st, opener=self._opener, sizer=self._sizer)
        self.assertEqual(st["offset"], self.size[0])
        seen_first = st["seen"]
        st = ad.scan("x", st, opener=self._opener, sizer=self._sizer)
        self.assertEqual(st["seen"], seen_first, "тот же хвост не должен перечитываться")

    def test_truncation_resets_evidence(self):
        st = ad.feed([LINE_REVOKED, LINE_EXIT_FAST], ad.new_state())
        st["offset"] = 10 ** 6                       # как будто дочитали до конца большого лога
        self.assertTrue(ad.should_restart(st))
        self._set("свежий лог новой жизни процесса\n")   # сторож усёк файл при подъёме ветки
        st = ad.scan("x", st, opener=self._opener, sizer=self._sizer)
        self.assertEqual(st["failures"], 0, "улики прошлой жизни процесса недействительны")
        self.assertFalse(ad.should_restart(st))

    def test_missing_log_is_not_fatal(self):
        st = ad.scan("Z:\\нет\\такого.log", ad.new_state())
        self.assertEqual(st["failures"], 0)

    def test_failure_split_across_two_reads_still_counts(self):
        """Фраза и падение попали в РАЗНЫЕ чтения хвоста — пара обязана сойтись через состояние."""
        st = ad.new_state()
        self._set(LINE_REVOKED + "\n")
        st = ad.scan("x", st, opener=self._opener, sizer=self._sizer)
        self.assertEqual(st["failures"], 0)
        self._set(LINE_REVOKED + "\n" + LINE_EXIT_FAST + "\n")
        st = ad.scan("x", st, opener=self._opener, sizer=self._sizer)
        self.assertEqual(st["failures"], 1)


class TestReadingContract(unittest.TestCase):
    """КОНТРАКТ ЧИТАТЕЛЯ (parse_outcome): наверх идёт ПАРА «осмотрено/опознано» и исход.

    Класс, который тут заперт: 08.08 на 292 живых строках не встретился НИ ОДИН из четырёх
    маркеров, а наверх ушло `failures = 0` — то же самое, что говорит здоровый канал. Молчание
    источника и слепота читателя были неразличимы ничем."""

    def test_idle_live_log_is_recognized_not_blind(self):
        """Живой лог ПРОСТОЯ: 0 маркеров — но форма опознана в КАЖДОЙ строке. Это честный ноль,
        а не слепота, и контракт обязан сказать именно так."""
        lines = idle_lines()
        st = ad.feed(lines, ad.new_state())
        self.assertEqual(st["seen"], len(lines))
        self.assertEqual(st["parsed"], len(lines))          # форма живого лога совпала на всех
        self.assertEqual(st["marks"], 0)                    # и ни одного из четырёх маркеров
        self.assertEqual(ad.reading(st).outcome, parse_outcome.PARSED)
        self.assertFalse(ad.is_blind(st))
        self.assertFalse(ad.should_restart(st))

    def test_unknown_format_is_the_third_outcome(self):
        """Формат уехал (метки времени нет ни у одной строки) → НЕРАЗБОР, а не «отказов нет»."""
        alien = ["[08/08/26 07:30] bridge poll ok, no work"] * 292
        st = ad.feed(alien, ad.new_state())
        r = ad.reading(st)
        self.assertEqual((r.seen, r.parsed, r.outcome), (292, 0, parse_outcome.NONPARSE))
        self.assertTrue(ad.is_blind(st))
        self.assertFalse(ad.should_restart(st), "слепой читатель не смеет рестартовать ветку")

    def test_third_outcome_is_spoken_aloud_with_numbers(self):
        """Третий исход обязан ПРОИЗНОСИТЬСЯ: числа + прямо названное последствие."""
        st = ad.feed(["строка чужого формата"] * 5, ad.new_state())
        note = ad.blind_note(st)
        self.assertIn("НЕ ОПОЗНАЛ формат", note)
        self.assertIn("осмотрено 5", note)
        self.assertIn("НЕ значит «авторизация цела»", note)

    def test_empty_tail_is_not_blindness(self):
        """Хвост пуст (сервер молчит) — это ПЕРВЫЙ исход, а не отказ читателя."""
        st = ad.feed([], ad.new_state())
        self.assertEqual(ad.reading(st).outcome, parse_outcome.EMPTY)
        self.assertFalse(ad.is_blind(st))

    def test_unread_log_is_not_an_empty_tail(self):
        """«Не прочитал» ≠ «прочитал пусто»: у read_new это None, и оно считается отдельно."""
        lines, off, trunc = ad.read_new("Z:\\нет\\такого.log", 0)
        self.assertIsNone(lines, "нечитаемый лог обязан отдавать «не знаю», а не пустой список")
        self.assertEqual((off, trunc), (0, False))
        st = ad.scan("Z:\\нет\\такого.log", ad.new_state())
        self.assertEqual((st["unread"], st["seen"]), (1, 0))
        self.assertFalse(ad.is_blind(st))

    def test_describe_names_reading_numbers(self):
        """Причина в логе/карточке несёт ЧИСЛА чтения, а не один счётчик отказов."""
        st = ad.feed(fixture_lines(), ad.new_state())
        text = ad.describe(st)
        self.assertIn("осмотрено %d строк" % st["seen"], text)
        self.assertIn("маркеров", text)

    def test_missing_state_is_not_zero_seen(self):
        """«Не смотрел» НЕ приводится к «осмотрено 0»: это и есть схлопывание, от которого лечим."""
        with self.assertRaises(ValueError):
            ad.reading(None)
        self.assertFalse(ad.is_blind(None))
        self.assertIn("не заводился", ad.blind_note(None))

    def test_describe_of_missing_state_does_not_claim_zero_failures(self):
        """Состояния нет (детектор не заводился) — это НЕ «отказов ноль»."""
        text = ad.describe(None)
        self.assertIn("не заводился", text)
        self.assertFalse(ad.should_restart(None))

    def test_truncation_resets_the_reading_too(self):
        """Усечение = новая жизнь процесса: пара контракта считает ТЕКУЩУЮ жизнь ветки."""
        st = ad.feed(idle_lines(), ad.new_state())
        self.assertGreater(st["seen"], 0)
        st["offset"] = 10 ** 6
        body = ["2026-08-08T08:00:00.000Z [DEBUG] [bridge:api] новая жизнь"]
        text = "\n".join(body) + "\n"
        st = ad.scan("x", st, opener=lambda p: io.StringIO(text), sizer=lambda p: len(text))
        self.assertEqual((st["seen"], st["parsed"]), (1, 1))

    def test_shape_matches_the_revoked_fixture_too(self):
        """Форма — общая для лога: строки инцидента 29.07 тоже опознаются (иначе признак врёт)."""
        real = [ln for ln in fixture_lines() if not ln.startswith("#") and len(ln.strip()) > 0]
        st = ad.feed(real, ad.new_state())
        self.assertEqual(st["parsed"], len(real))
        self.assertGreater(st["marks"], 0)


if __name__ == "__main__":
    unittest.main()
