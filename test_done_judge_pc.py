# -*- coding: utf-8 -*-
"""Регресс судьи закрытия полосы ПК (`done_judge_pc`) — ступень C.

ДВА ОТРИЦАТЕЛЬНЫХ ОБЯЗАТЕЛЬНЫ И НАЗВАНЫ ЗАДАНИЕМ ВЛАДЕЛЬЦА (01.09.2026):
  • отчёт успешный, адрес назван, по адресу ПУСТО            → «неизвестно»;
  • отчёт успешный, по адресу лежит результат ПРЕЖНЕГО прогона → «неизвестно».
Позеленел любой из них — прибор в прод не идёт. Поэтому оба проверяют не только слово вердикта,
но и ПРИЧИНУ: зелёное по случайности отличается от зелёного по делу только ею.

Положительный контроль здесь обязателен по той же причине, что и у V0: прибор, отвечающий
«неизвестно» на всё, проходит любой отрицательный тест и бесполезен.

    venv\\Scripts\\python.exe -m unittest test_done_judge_pc
"""

import ast
import os
import shutil
import tempfile
import unittest
from unittest import mock

import content_product_verifier as v0
import done_judge_pc as dj

FOLDER = "docs/artifacts"
WORDS = "ступень C V0 судит done"
TASK = ("ЦЕЛЬ: проверить прибор. ЗАПРЕТЫ: нет.\n"
        "АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 01.09 со словами " + WORDS)
NAME = "2026-09-01-stupen-c.md"
REL = FOLDER + "/" + NAME
# Заголовок с БОЛЬШОЙ буквы — живая форма артефактов полосы (замер 01.09: 4 из 4 адресов
# отвечают заголовком, и ни один — дословной строчной фразой).
BODY = "# Ступень C V0 судит done — разбор\n\nтело\n"
OLD = "# Ступень C V0 судит done — прошлый прогон\n\nстарое тело\n"


def _write(root, rel, text):
    full = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(text)


class JudgeCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="done_judge_pc_")
        os.makedirs(os.path.join(self.root, FOLDER.replace("/", os.sep)))
        self.addCleanup(shutil.rmtree, self.root, True)

    def base(self, text=TASK):
        return dj.baseline(text, root=self.root)

    def judge(self, base, status="done", text=TASK, tid=90):
        return dj.judge(tid, text, status, base, run_id="pc-test-90", root=self.root)


class TestAddress(JudgeCase):
    def test_live_prose_form_parses(self):
        addr = dj.read_address(TASK)
        self.assertEqual((addr["folder"], addr["day"], addr["month"], addr["words"]),
                         (FOLDER, 1, 9, WORDS))

    def test_live_task_of_the_lane_parses(self):
        """Дословная строка закрытой задачи #72 — тест на реальной фразе, а не на идеальной."""
        addr = dj.read_address(
            "…3. Одно правило, КАК чинить весь класс — текстом, без применения. "
            "АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 01.09 со словами "
            "класс абсолютного корня в тестах")
        self.assertEqual(addr["words"], "класс абсолютного корня в тестах")
        self.assertEqual(addr["folder"], "docs/artifacts")

    def test_no_marker_is_no_address(self):
        self.assertIsNone(dj.read_address("ЦЕЛЬ: что-то сделать, адреса нет"))
        self.assertIsNone(dj.read_address(None))

    def test_address_out_of_tree_is_refused(self):
        self.assertIsNone(dj.read_address(dj.MARK + ": файл C:/Windows/win.ini"))
        self.assertIsNone(dj.read_address(dj.MARK + ": файл в ../чужое за 01.09 со словами x"))

    def test_direct_path_form(self):
        addr = dj.read_address(dj.MARK + ": файл docs/artifacts/a.md со словами " + WORDS)
        self.assertEqual((addr["path"], addr["words"]), ("docs/artifacts/a.md", WORDS))


class TestPositiveControl(JudgeCase):
    def test_fresh_product_at_the_named_address_is_done(self):
        base = self.base()
        _write(self.root, REL, BODY)
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.DONE, out["reason"])
        self.assertEqual(out["v0"]["verdict"], v0.PROVEN)
        self.assertEqual(out["v0"]["reason_code"], "all_gates_passed")
        self.assertEqual(out["chosen"], REL)

    def test_case_of_the_heading_is_not_evidence(self):
        """Слова адреса — строчными, в артефакте — заголовком. Это ОДНА фраза."""
        base = self.base()
        _write(self.root, REL, BODY)
        self.assertNotIn(WORDS, BODY)                      # дословно её в теле НЕТ
        self.assertEqual(self.judge(base)["verdict"], dj.DONE)

    def test_changed_old_file_counts_as_this_run(self):
        _write(self.root, REL, OLD)
        base = self.base()
        _write(self.root, REL, BODY)                       # заход дописал сам
        self.assertEqual(self.judge(base)["verdict"], dj.DONE)


class TestMandatoryNegatives(JudgeCase):
    def test_report_says_done_but_the_address_is_empty(self):
        """ОТРИЦАТЕЛЬНЫЙ 1 (задание, п. 3). Отчёт успешен, адрес назван, по адресу ПУСТО.

        11.09.2026: исход назван третьим словом — `не доказано`, а не `неизвестно`. Чтение по
        адресу УДАЛОСЬ и вернуло «нет», а это самый сильный из трёх исходов. Разница не
        косметическая: льгота неизвестности (серию не рвёт) сюда НЕ распространяется."""
        base = self.base()
        _write(self.root, FOLDER + "/2026-08-30-другое.md", BODY)   # соседний день — не адрес
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNPROVEN)
        self.assertNotEqual(out["verdict"], dj.DONE)
        self.assertIn("ПУСТО", out["reason"])
        self.assertIsNone(out["v0"])

    def test_report_says_done_but_the_address_holds_the_previous_run(self):
        """ОТРИЦАТЕЛЬНЫЙ 2 (задание, п. 4). По адресу — продукт ПРЕЖНЕГО прогона, не тронут.

        Ловит это не адаптер, а V0: адрес назван в `allowed_changed_paths`, изменений по нему
        ноль → `changed_scope_mismatch`. Слова адреса при этом НАЙДЕНЫ — то есть внешний признак
        зелёный, как у сданной работы, и отличается ровно одно."""
        _write(self.root, REL, OLD)
        base = self.base()                                  # опорный снимок ВИДИТ труп
        out = self.judge(base)                              # заход не тронул ничего
        # 11.09: `DISPROVEN` прибора переводится СВОИМ словом полосы, а не общим «неизвестно».
        self.assertEqual(out["verdict"], dj.UNPROVEN)
        self.assertEqual(out["v0"]["verdict"], v0.DISPROVEN)
        self.assertEqual(out["v0"]["reason_code"], "changed_scope_mismatch")
        self.assertIn(WORDS.casefold(), OLD.casefold())     # признак был зелёным

    def test_previous_run_survives_a_lying_report(self):
        """Тот же труп, но отчёт исполнителя кричит об успехе. Отчёт в бандл не входит вовсе."""
        _write(self.root, REL, OLD)
        base = self.base()
        out = dj.judge(90, TASK, "done", base, run_id="pc-test-90", root=self.root)
        self.assertEqual(out["verdict"], dj.UNPROVEN)


class TestForeignFileAtTheAddress(JudgeCase):
    """ЧУЖОЙ ФАЙЛ ПО АДРЕСУ (03.09.2026): доказательством он быть не смеет.

    Замер повода назвал два места, где различитель молчал, и оба лежат в АДАПТЕРЕ, а не в
    словах адреса: молчаливый выбор при нескольких ответах и копия прежнего, засчитанная за
    продукт. Гипотеза «слова слишком свободные» тем же замером опровергнута (10 000 пар слов и
    файлов, чужих совпадений 0), поэтому здесь же стои́т замок на ОБРАТНОЕ ужесточение: 11 из 20
    живых адресов отвечают одним ИМЕНЕМ файла, и правило «слова обязаны быть в теле» перекрасило
    бы 11 честных закрытий.
    """

    def test_a_foreign_file_with_a_partial_word_match_is_not_evidence(self):
        """ОТРИЦАТЕЛЬНЫЙ: чужой файл ловит ЧАСТЬ слов адреса — этого мало.

        05.09: исход прежний («неизвестно»), а ДОРОГА до него другая и это намеренно. Раньше
        чужой файл уходил в бандл и приговор приходил от V0 (`text_condition_failed`) — то есть
        вердикт задачи НАЗЫВАЛСЯ чужим путём. Теперь такой файл кандидатом не становится вовсе:
        `chosen` пуст, прибор не зовётся, а сам файл остаётся СВЕДЕНИЕМ в причине."""
        base = self.base()
        alien = FOLDER + "/2026-09-01-ступень-C-про-другое.md"
        _write(self.root, alien, "# Ступень C — совсем другая работа\n\nтут нет полной фразы адреса\n")
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNPROVEN, out["reason"])
        self.assertIsNone(out["chosen"], "чужой файл продуктом задачи не объявляется")
        self.assertIsNone(out["v0"], "до прибора такой кандидат не доходит")
        self.assertIn("НИ ОДИН", out["reason"])
        self.assertIn(WORDS, out["reason"], "причина называет СВОИ слова адреса")

    def test_own_file_answering_the_address_is_proven(self):
        """ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ рядом с отрицательными: прибор, глухой ко всему, бесполезен."""
        base = self.base()
        _write(self.root, REL, BODY)
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.DONE, out["reason"])
        self.assertEqual(out["chosen"], REL)

    def test_the_address_words_may_live_in_the_name_alone(self):
        """Замок от обратного ужесточения: 11 из 20 живых адресов отвечают ИМЕНЕМ файла."""
        base = self.base()
        _write(self.root, FOLDER + "/2026-09-01-ступень-C-V0-судит-done.md",
               "# Разбор\n\nтело про то же самое, дословной фразы адреса в нём нет\n")
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.DONE, out["reason"])

    def test_a_file_that_appeared_but_is_older_than_the_run_is_not_evidence(self):
        """ОТРИЦАТЕЛЬНЫЙ: файл ПОЯВИЛСЯ по адресу, но содержимое лежало тут ДО захода.

        Копия (переименование, `git checkout`, перенос из соседнего дня) даёт по адресу ровно
        такое же изменение sha, как рождённый продукт, — в режиме `measured` V0 не отличает их
        ничем. Часы тут не при чём: «старше начала захода» доказано тем, что это содержимое
        лежало в папке до захода, а не сравнением времён."""
        old = FOLDER + "/2026-08-30-чужая-работа.md"
        _write(self.root, old, BODY)                       # лежит ДО захода, дата не адресная
        base = self.base()
        _write(self.root, REL, BODY)                       # заход «принёс» её под адресным именем
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNPROVEN, out["reason"])
        self.assertIn("ЛЕЖАЛО тут до захода", out["reason"])
        self.assertIn("2026-08-30-чужая-работа.md", out["reason"])
        self.assertIsNone(out["v0"], "до прибора такой кандидат не доходит")

    def test_two_answering_files_changed_in_one_run_is_unknown_not_a_pick(self):
        """ОТРИЦАТЕЛЬНЫЙ: два отвечающих файла изменились за заход — судья не выбирает.

        До правки брался ПЕРВЫЙ ПО АЛФАВИТУ, и чужой файл, который заход тоже тронул, уходил
        в бандл вместо продукта. V0 такое не ловит ничем: ему подают один путь."""
        base = self.base()
        _write(self.root, FOLDER + "/2026-09-01-a-ступень-C-V0-судит-done.md", "# чужой\n")
        _write(self.root, REL, BODY)
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNKNOWN, out["reason"])
        self.assertIn("который из них продукт", out["reason"])
        self.assertIsNone(out["v0"])

    def test_a_hand_made_baseline_without_the_folder_snapshot_is_unknown(self):
        """Снимка папки нет → «отличить нечем», а не «сделано». Третий исход обязателен."""
        _write(self.root, REL, BODY)
        base = self.base()
        _write(self.root, REL, BODY + "ещё\n")
        base.pop(dj.F_PRIOR)
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertIn("не снят", out["reason"])

    def test_the_wide_snapshot_is_taken_by_the_daemon_before_the_run(self):
        _write(self.root, FOLDER + "/2026-08-30-чужая-работа.md", BODY)
        base = self.base()
        self.assertIsInstance(base[dj.F_PRIOR], dict)
        self.assertIn(FOLDER + "/2026-08-30-чужая-работа.md", base[dj.F_PRIOR].values())


# ═══ ДВЕ РУКИ ОДНИМ ВИТКОМ (05.09.2026) ══════════════════════════════════════════════════════
# С 05.09 демон берёт по ДВЕ задачи за виток («ПАРАЛЛЕЛЬ: беру 2 задач(и) одним витком»), а
# опорная линия у каждой руки снята при СВОЁМ claim — значит в «изменилось за заход» у второй
# руки лежит и продукт ПЕРВОЙ. Ниже — оба живых расклада того дня, дословно по числам.
WORDS_A = "первая рука сдала продукт"
WORDS_B = "вторая рука продукта не сдала"
TASK_A = ("ЦЕЛЬ: рука A.\nАДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 01.09 со словами " + WORDS_A)
TASK_B = ("ЦЕЛЬ: рука B.\nАДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 01.09 со словами " + WORDS_B)
# Имена нарочно расставлены по алфавиту так, как легли живые 16/17: продукт ПЕРВОЙ руки —
# первым. Именно порядок решал, чей файл достанется второй руке.
REL_A = FOLDER + "/2026-09-01-а-первая-рука-сдала-продукт.md"
REL_B = FOLDER + "/2026-09-01-я-вторая-рука-сдала-своё.md"
# Живая форма ТЗ, на которой судья 05.09 споткнулся пять раз подряд (задачи 14–18): строка
# адреса ПЕРЕНЕСЕНА, а `words` читается до конца ПЕРВОЙ строки — в слова попадает обрывок
# инструкции. Настоящие слова остаются в хвосте и не участвуют ни в чём.
TASK_B_WRAPPED = ("ЦЕЛЬ: рука B.\n"
                  "АДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 01.09 со словами адреса. "
                  "Отдельной строкой\nв артефакте — ровно слова адреса подряд: "
                  "вторая рука сдала своё")
WRAPPED_WORDS = "адреса. Отдельной строкой"


class TestParallelHands(JudgeCase):
    """ЧУЖОЙ ПРОДУКТ — НЕ МОЙ АДРЕС. Вердикт каждой руки снимается с ЕЁ СОБСТВЕННОГО задания.

    Замер 05.09: три закрытых параллельных пары, в ДВУХ обе задачи закрыты по ОДНОМУ адресу —
    чужому для второй руки (16/17 и 18/21). Третья пара (14/15) разошлась СЛУЧАЙНО: её файлы
    легли в алфавите так, что каждой руке достался свой. Случайность лечению не подлежит —
    лечится правило, а не расклад.
    """

    def test_the_hand_without_a_product_is_not_judged_by_the_other_hands_file(self):
        """ОТРИЦАТЕЛЬНЫЙ (задание владельца): один виток, у каждой руки СВОЙ адрес, продукт
        только у первой. Первая — доказана, вторая — недоказана, и НИ ОДНА не судится чужим."""
        base_a = self.base(TASK_A)
        base_b = self.base(TASK_B)                      # обе линии сняты ДО любого продукта
        _write(self.root, REL_A, "# Первая рука сдала продукт\n\nтело\n")
        out_a = dj.judge(16, TASK_A, "done", base_a, run_id="pc-test-16", root=self.root)
        out_b = dj.judge(17, TASK_B, "done", base_b, run_id="pc-test-17", root=self.root)

        self.assertEqual(out_a["verdict"], dj.DONE, out_a["reason"])
        self.assertEqual(out_a["chosen"], REL_A)

        self.assertEqual(out_b["verdict"], dj.UNPROVEN, out_b["reason"])
        self.assertIsNone(out_b["chosen"], "чужой продукт кандидатом второй руки не становится")
        self.assertIsNone(out_b["v0"], "чужой файл прибору не подаётся вовсе")
        self.assertEqual(out_b["address"]["words"], WORDS_B, "судится СВОИМ адресом")
        self.assertNotIn("«%s»" % REL_A, out_b["reason"], "адресом второй руки чужой файл не зовут")
        self.assertIn(WORDS_B, out_b["reason"])

    def test_both_hands_deliver_but_the_second_address_answers_nothing(self):
        """ЖИВОЙ РАСКЛАД 16/17: обе руки сдали продукт, слова второго адреса не отвечают ничему.

        До правки вторая рука получала вердикт по файлу ПЕРВОЙ — просто потому, что он первый по
        алфавиту среди изменённого за заход. Порядок здесь закреплён замером, а не надеждой."""
        self.assertEqual(sorted([REL_A, REL_B])[0], REL_A, "премиса расклада: чужой файл первый")
        base_a = self.base(TASK_A)
        base_b = self.base(TASK_B_WRAPPED)
        self.assertEqual(dj.read_address(TASK_B_WRAPPED)["words"], WRAPPED_WORDS,
                         "перенос строки адреса читается как обрывок — так и было 05.09")
        _write(self.root, REL_A, "# Первая рука сдала продукт\n\nтело\n")
        out_a = dj.judge(16, TASK_A, "done", base_a, run_id="pc-test-16", root=self.root)
        _write(self.root, REL_B, "# Вторая рука сдала своё\n\nтело\n")
        out_b = dj.judge(17, TASK_B_WRAPPED, "done", base_b, run_id="pc-test-17", root=self.root)

        self.assertEqual(out_a["verdict"], dj.DONE, out_a["reason"])
        self.assertEqual(out_a["chosen"], REL_A)
        self.assertEqual(out_b["verdict"], dj.UNPROVEN, out_b["reason"])
        self.assertIsNone(out_b["chosen"])
        self.assertNotIn("«%s»" % REL_A, out_b["reason"])
        self.assertIn(REL_B, out_b["reason"], "свой файл назван СВЕДЕНИЕМ — диагноз не потерян")

    def test_the_refused_candidate_is_exactly_the_one_the_instrument_fails(self):
        """ЗАМОК ОТ ПЕРЕКРАСКИ ЗЕЛЁНОГО: снятые ветки не могли дать «сделано» НИ ОДНОЙ строкой.

        Кандидатов адаптер отбирает `v0.address_hit`, и ТОТ ЖЕ вызов стои́т внутри гейта
        `path_or_text_contains_ci`, которым судит прибор. Значит файл, которого адаптер не берёт,
        у прибора получил бы `text_condition_failed` → `DISPROVEN` → то же «неизвестно»."""
        alien, text = FOLDER + "/2026-09-01-чужая-работа.md", "# Чужая работа\n\nслов адреса нет\n"
        self.assertFalse(v0.address_hit(alien, text, WORDS), "в кандидаты не попадает")
        gate = v0._content_gate(                                          # noqa: SLF001 — гейт прибора
            {"gate_id": dj.GATE_ID, "artifact_id": dj.ARTIFACT_ID,
             "type": "path_or_text_contains_ci", "params": {"text": WORDS}},
            {dj.ARTIFACT_ID: {"path": alien, "text": text, "content_type": "text"}})
        self.assertEqual((gate["status"], gate["reason_code"]), ("FAIL", "text_condition_failed"))


class TestOtherRefusals(JudgeCase):
    def test_product_without_the_named_words_is_not_proven(self):
        """Файл по адресу есть, слов адреса в нём нет → «не доказано», и файл НАЗВАН сведением.

        Диагноз не теряется: путь по-прежнему в причине, но как «за заход изменилось», а не как
        «адрес». Разница видна ровно тогда, когда файл чужой (см. `TestParallelHands`)."""
        base = self.base()
        _write(self.root, REL, "# Совсем про другое\n")
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNPROVEN)
        self.assertIsNone(out["v0"])
        self.assertIn(REL, out["reason"], "что заход тронул — сведение, а не адрес")
        self.assertIn("НИ ОДИН", out["reason"])

    def test_no_address_is_unknown_not_done(self):
        text = "ЦЕЛЬ: что-то сделать. Адреса результата нет."
        out = dj.judge(90, text, "done", dj.baseline(text, root=self.root), root=self.root)
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertIn("не назван", out["reason"])

    def test_baseline_not_taken_is_unknown(self):
        out = self.judge({"address": dj.read_address(TASK), "files": None, "ok": False})
        self.assertEqual(out["verdict"], dj.UNKNOWN)
        self.assertIn("НЕ СНЯТ", out["reason"])

    def test_disappeared_product_is_unproven_not_unknown(self):
        """Продукт СНЕСЛИ за заход. Папка прочитана, ответ получен — «не доказано», не «неизвестно»:
        исчезновение это ДЕЙСТВИЕ захода, а не слепота судьи, и льготу оно не покупает."""
        _write(self.root, REL, BODY)
        base = self.base()
        os.remove(os.path.join(self.root, REL.replace("/", os.sep)))
        out = self.judge(base)
        self.assertEqual(out["verdict"], dj.UNPROVEN)
        self.assertIn("ИСЧЕЗЛО", out["reason"])
        self.assertNotIn("нет ни одного файла", out["reason"])   # «не появилось» ≠ «снесли»

    def test_only_done_is_judged(self):
        base = self.base()
        for status in ("failed", "needs_approval", "new"):
            self.assertIsNone(self.judge(base, status=status), status)

    def test_judge_never_raises_and_a_crash_is_not_done(self):
        for base in (None, {}, {"address": {"folder": 1}, "files": {}, "ok": True}):
            out = dj.judge(90, TASK, "done", base, root=self.root)
            self.assertIsNotNone(out)
            self.assertNotEqual(out["verdict"], dj.DONE)


class TestThreeOutcomes(unittest.TestCase):
    """ТРЕТИЙ ИСХОД (11.09.2026): «не смог прочитать» перестало называться падением.

    ПОВОД ЖИВОЙ. Заход 245 (10.09.2026 01:04) сделал работу, положил коммит 22f57a2 и артефакт
    по названному адресу — и лёг в очередь провалом с причиной `V0: UNKNOWN / sensitive_content`.
    Судья не смог ПРОЧИТАТЬ доказательство, потому что доказательство было про секреты.
    """

    def test_three_words_of_the_instrument_map_to_three_words_of_the_lane(self):
        """Схлопывание трёх в два и было корнем: в очередь ехал один маркер на оба исхода."""
        self.assertEqual(len({dj.DONE, dj.UNPROVEN, dj.UNKNOWN}), 3, "слова полосы совпали")
        self.assertEqual(dj._V0_WORD,                                # noqa: SLF001 — предмет
                         {v0.PROVEN: dj.DONE, v0.DISPROVEN: dj.UNPROVEN, v0.UNKNOWN: dj.UNKNOWN})
        self.assertEqual(sorted(dj._V0_WORD), sorted(v0.VERDICTS),   # noqa: SLF001
                         "перевод знает не все слова прибора")

    def test_an_unrecognised_instrument_word_does_not_buy_the_lenient_outcome(self):
        """Мусорный вердикт прибора → НЕ ДОКАЗАНО. У неизвестности есть льгота, и купить её
        неразобранным ответом нельзя."""
        self.assertEqual(dj._V0_WORD.get("МУСОР", dj.UNPROVEN), dj.UNPROVEN)  # noqa: SLF001

    def test_the_two_markers_are_not_substrings_of_each_other(self):
        """Разбор идёт перебором голов: будь одна головой другой — исход читался бы неверно."""
        self.assertNotIn(dj.UNKNOWN_PREFIX, dj.UNPROVEN_PREFIX)
        self.assertNotIn(dj.UNPROVEN_PREFIX, dj.UNKNOWN_PREFIX)
        self.assertEqual(set(dj.PREFIX), {dj.UNKNOWN, dj.UNPROVEN})

    def test_one_parser_reads_both_markers_and_the_reason(self):
        """ОДНО МЕСТО РАЗБОРА. Второй экземпляр разошёлся бы с первым молча — класс полосы."""
        for word, why in ((dj.UNKNOWN, "V0: UNKNOWN / sensitive_content по адресу «a.md»"),
                          (dj.UNPROVEN, "по адресу ПУСТО: за 10.09 нет ни одного файла")):
            text = dj.fail_result({"verdict": word, "reason": why}, "отчёт исполнителя")
            self.assertEqual(dj.outcome_of(text), word, why)
            self.assertEqual(dj.reason_of(text), why)
            self.assertNotIn(dj.PREFIX[word], dj.strip_marks(text))
            self.assertIn("отчёт исполнителя", text, "отчёт исполнителя не выброшен")

    def test_a_failure_outside_the_court_carries_no_word(self):
        """Таймаут и обрыв связи судья не судил: приписать ему слово нельзя ни одно."""
        self.assertIsNone(dj.outcome_of("[причина=run_timeout · таймаут прогона]"))
        self.assertIsNone(dj.outcome_of(""))
        self.assertIsNone(dj.reason_of("[причина=run_timeout · таймаут прогона]"))

    def test_unknown_without_a_named_reason_is_not_accepted(self):
        """ОТРИЦАТЕЛЬНЫЙ 3 задания. Неизвестность без причины льготы не получает НИГДЕ:

        ни в принятом слове, ни в маркере очереди, ни в реестре. Замок стои́т у источника —
        значит счётчику серии подложить безпричинную неизвестность попросту нечем."""
        for reason in (None, "", "   "):
            bad = {"verdict": dj.UNKNOWN, "reason": reason, "address": {"words": "x"}}
            self.assertEqual(dj.accepted(bad), dj.UNPROVEN, repr(reason))
            self.assertEqual(dj.outcome_of(dj.fail_result(bad, "отчёт")), dj.UNPROVEN, repr(reason))
        good = {"verdict": dj.UNKNOWN, "reason": "sensitive_content по адресу «a.md»"}
        self.assertEqual(dj.accepted(good), dj.UNKNOWN)
        self.assertEqual(dj.accepted({"verdict": dj.DONE, "reason": ""}), dj.DONE)
        self.assertEqual(dj.accepted(None), dj.UNPROVEN)

    def test_the_status_did_not_move_with_the_word(self):
        """ОТРИЦАТЕЛЬНЫЙ 1 задания на этом слое: оба недоказанных исхода по-прежнему уводят ряд
        в `failed`. Статусов в очереди шесть, своего у неизвестности нет, и заводить его —
        не решение этого захода."""
        for word in (dj.UNPROVEN, dj.UNKNOWN):
            addressed = {"verdict": word, "reason": "почему", "address": {"words": "x"}}
            self.assertTrue(dj.enforces(addressed, dj.MODE_ADDR), word)
            self.assertFalse(dj.enforces(addressed, dj.MODE_OFF), word)


class TestEnforcement(unittest.TestCase):
    def test_mode_from_environment(self):
        self.assertEqual(dj.enforce_mode({}), dj.MODE_ADDR)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "off"}), dj.MODE_OFF)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "0"}), dj.MODE_OFF)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "all"}), dj.MODE_ALL)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "1"}), dj.MODE_ALL)
        self.assertEqual(dj.enforce_mode({"DONE_JUDGE_PC": "мусор"}), dj.MODE_ADDR)

    def test_who_changes_the_status(self):
        addressed = {"verdict": dj.UNKNOWN, "address": {"words": "x"}}
        blind = {"verdict": dj.UNKNOWN, "address": None}
        proven = {"verdict": dj.DONE, "address": {"words": "x"}}
        self.assertTrue(dj.enforces(addressed, dj.MODE_ADDR))
        self.assertFalse(dj.enforces(blind, dj.MODE_ADDR))      # безадресную задачу не трогаем
        self.assertTrue(dj.enforces(blind, dj.MODE_ALL))        # полное правило владельца
        self.assertFalse(dj.enforces(proven, dj.MODE_ALL))
        self.assertFalse(dj.enforces(addressed, dj.MODE_OFF))
        self.assertFalse(dj.enforces(None, dj.MODE_ALL))

    def test_line_puts_the_verdict_in_words(self):
        self.assertEqual(dj.line(None), "")
        self.assertIn(dj.UNKNOWN, dj.line({"verdict": dj.UNKNOWN, "reason": "почему"}))

    def test_run_token_comes_from_the_daemon_not_the_executor(self):
        self.assertEqual(dj.run_token(90, "tmp/pc_report/task90-11480-1788265605336-90.md"),
                         "11480-1788265605336-90")
        self.assertEqual(dj.run_token(90, None), "pc-task-90")


class TestLedger(unittest.TestCase):
    """РЕЕСТР ВЕРДИКТОВ (02.09.2026): след суда обязан пережить заход.

    Предмет — не вердикт, а ПАМЯТЬ о нём: в режиме `addr` безадресное закрытие не
    меняет статуса и не приписывается к отчёту, а слепок очереди у сданной строки
    причины не несёт вовсе. Значит без реестра счёт серии не отличал бы доказанное
    закрытие от закрытого без проверки — замер 02.09: серия 18, доказано 5.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="done_judge_ledger_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_an_empty_ledger_reads_as_the_judge_said_nothing(self):
        self.assertEqual(dj.read_ledger(self.root), {})

    def test_a_proven_verdict_lands_as_proven(self):
        dj.note(11, {"verdict": dj.DONE, "reason": "V0: PROVEN", "address": {"words": "x"}},
                root=self.root)
        got = dj.read_ledger(self.root)["11"]
        self.assertTrue(got[dj.F_PROVED])
        self.assertTrue(got[dj.F_ADDRESSED])

    def test_an_addressless_verdict_lands_and_says_it_had_no_address(self):
        """Тот самый исход, который до 02.09 не оставлял следа НИГДЕ, кроме лога."""
        dj.note(12, {"verdict": dj.UNKNOWN, "reason": "адрес результата не назван задачей",
                     "address": None}, root=self.root)
        got = dj.read_ledger(self.root)["12"]
        self.assertFalse(got[dj.F_PROVED])
        self.assertFalse(got[dj.F_ADDRESSED], "«без адреса» обязано быть отличимо от «не доказал»")
        self.assertIn("не назван", got[dj.F_REASON])

    def test_an_addressed_but_unproven_verdict_is_a_third_answer(self):
        dj.note(13, {"verdict": dj.UNKNOWN, "reason": "по адресу ПУСТО",
                     "address": {"words": "x"}}, root=self.root)
        got = dj.read_ledger(self.root)["13"]
        self.assertFalse(got[dj.F_PROVED])
        self.assertTrue(got[dj.F_ADDRESSED])

    def test_order_is_kept_by_a_counter_because_the_module_has_no_clock(self):
        for tid in (21, 22, 23):
            dj.note(tid, {"verdict": dj.DONE, "address": {"words": "x"}}, root=self.root)
        rows = dj.read_ledger(self.root)
        self.assertEqual([rows[str(t)][dj.F_SEQ] for t in (21, 22, 23)], [1, 2, 3])

    def test_the_tail_is_trimmed_by_the_counter_not_by_time(self):
        """Хвост назван числом (см. шапку): старое уходит, свежее остаётся."""
        for tid in range(dj.LEDGER_MAX + 5):
            dj.note(tid, {"verdict": dj.DONE, "address": {"words": "x"}}, root=self.root)
        rows = dj.read_ledger(self.root)
        self.assertEqual(len(rows), dj.LEDGER_MAX)
        self.assertIn(str(dj.LEDGER_MAX + 4), rows, "свежая запись вытеснена")
        self.assertNotIn("0", rows, "старая запись не вытеснена")

    def test_a_rewritten_row_keeps_one_entry_not_two(self):
        dj.note(31, {"verdict": dj.UNKNOWN, "address": None}, root=self.root)
        dj.note(31, {"verdict": dj.DONE, "address": {"words": "x"}}, root=self.root)
        rows = dj.read_ledger(self.root)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows["31"][dj.F_PROVED], "переотправленная задача обязана обновить исход")

    def test_a_broken_ledger_reads_as_silence_not_as_a_crash(self):
        path = dj.ledger_path(self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write("{ это не json".encode("utf-8"))
        self.assertEqual(dj.read_ledger(self.root), {})

    def test_writing_never_raises_and_a_failure_is_silent(self):
        """Судья, падающий на своём реестре, закрыл бы задачу хуже, чем судья без реестра."""
        self.assertIsNone(dj.note(41, None, root=self.root))
        blocked = os.path.join(self.root, "занято.txt")
        with open(blocked, "wb") as handle:
            handle.write(b"x")
        # путь через ФАЙЛ как через каталог — makedirs откажет, и это не должно ронять заход
        self.assertIsNone(dj.note(42, {"verdict": dj.DONE, "address": {"words": "x"}},
                                  root=os.path.join(blocked, "внутрь")))

    def test_the_ledger_lives_in_the_one_named_folder(self):
        self.assertTrue(dj.LEDGER.startswith(dj.PACKET_DIR + "/"),
                        "второй адрес записи модулю не заводится")

    def test_the_env_switch_moves_the_ledger_aside(self):
        aside = os.path.join(self.root, "в-сторону.json")
        with mock.patch.dict(os.environ, {dj.LEDGER_ENV: aside}, clear=False):
            dj.note(51, {"verdict": dj.DONE, "address": {"words": "x"}}, root=self.root)
            self.assertEqual(dj.ledger_path(self.root), aside)
            self.assertIn("51", dj.read_ledger(self.root))
        self.assertTrue(os.path.exists(aside))
        self.assertEqual(dj.read_ledger(self.root), {}, "боевой путь остался нетронутым")

    def test_the_default_path_is_test_aware(self):
        """Крюк изоляции: без явного корня путь идёт через `log_setup` и под тестом уезжает."""
        self.assertNotEqual(dj.ledger_path(None),
                            os.path.join(dj.REPO, dj.LEDGER.replace("/", os.sep)),
                            "регресс писал бы в БОЕВОЙ реестр полосы")


class TestPurity(unittest.TestCase):
    """Судья исхода не смеет иметь рук сильнее чтения — и не смеет смотреть на часы."""

    def source(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "done_judge_pc.py"),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_no_network_no_processes_no_database(self):
        tree = ast.parse(self.source())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []) or []:
                    names.add((getattr(node, "module", None) or alias.name).split(".")[0])
        self.assertEqual(names.intersection(
            {"subprocess", "socket", "requests", "urllib", "sqlite3", "bridge_http",
             "brain_writer", "pc_orchestrator"}), set())

    def test_freshness_is_measured_not_clocked(self):
        """Класс О3 этой полосы: свежий ЧУЖОЙ файл не доказывает авторство. Часов здесь нет."""
        source = self.source()
        tree = ast.parse(source)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []) or []:
                    names.add((getattr(node, "module", None) or alias.name).split(".")[0])
        self.assertEqual(names.intersection({"time", "datetime", "calendar"}), set())
        for forbidden in ("st_mtime", "getmtime", "getctime", "utcnow", "monotonic"):
            self.assertNotIn(forbidden, source)

    def test_writes_only_into_the_one_named_folder(self):
        self.assertEqual(dj.PACKET_DIR, "tmp/done_judge_pc")
        for node in ast.walk(ast.parse(self.source())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                mode = node.args[1].value if len(node.args) > 1 else "r"
                self.assertIn(mode, ("rb", "wb"), "неожиданный режим открытия: %s" % mode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
