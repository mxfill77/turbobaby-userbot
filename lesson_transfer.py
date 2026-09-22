# -*- coding: utf-8 -*-
"""
lesson_transfer.py — ДВЕРЬ ПЕРЕНОСА одного урока ЖИВОГО НАБОРА экзамена в базу правил бота
(22.09.2026, задание 71b MOSTUROKOV).

ЗАЧЕМ. Бот читает ровно одну базу уроков — `lesson_store.tsv` (`suggest.active_lesson_bullets`), а
вердикт владельца в живом наборе ложится в ДРУГУЮ — `exam_live/lessons.tsv` (`exam_show.LIVE_LESSONS`).
До 22.09 между ними не было ни функции, ни двери, ни вызывающего: замер 70x — действующих уроков набора
в том, что читает бот, 0 из 12. Решение владельца 22.09: «Чтобы бот запомнил». Перенос — здесь.

ЧЕТЫРЕ ПРАВИЛА ДВЕРИ, и каждое закрывает свой способ ошибиться:
  • ПО ОДНОМУ НОМЕРУ И ТОЛЬКО РУКОЙ. Ни автомата, ни пачки: один вызов — один урок набора, названный
    номером НАБОРА (`--from-set N`). В базе бота урок получает СВОЙ, новый номер, и карточка называет оба.
    Номера у двух баз свои, поэтому у каждого движения ключ назван по базе: `--from-set` — номер набора,
    `--rollback` — номер базы бота. Одного «номера вообще» здесь нет ни в одном ключе.
  • ПОД ПРАВОМ. `moderation_core.may_write_rule` (fail-closed: пустой список = НИКОМУ) — тот же гейт, что
    у записи, перевода и снятия. Имя берётся только из `--who`; подстановки имени из записи тренажёра
    (`trainer.peek_actor`, ею живёт `lesson_promote` при пустом `--who`) здесь нет: пусто — отказ.
  • ПРИЧИНУ НЕ ВЫДУМЫВАЕМ. Причина — слова владельца в `--why`. Пусто → урок ложится КАНДИДАТОМ с пустым
    «почему» и в ответ не входит. «Почему» строки набора (его писал критик) в причину НЕ переезжает: иначе
    кандидат включался бы словом «урок включи M» без единого слова владельца — `lesson_store.promote`
    берёт причину из строки, когда аргумент пуст.
  • ОТКАТ ПО НОМЕРУ — БАЙТ В БАЙТ. Перенос дописывает в конец базы ОДНУ строку и кладёт запись о переносе
    с отпечатками файла до и после. Откат переноса возвращает файл РОВНО к байтам до переноса — и только
    если после переноса база не изменилась ни байтом. Урок без записи о переносе откатить нельзя, и дверь
    говорит это словами.

ЛОЖЬ ПОКАЗА ЗАКРЫТА СВЕРКОЙ С ЖИВЫМ ЧИТАТЕЛЕМ. Слова «бот отвечает по уроку» печатаются ТОЛЬКО после того,
как живой читатель правил вернул строку урока в собранном промпте. Читатель — ровно тот вызов, которым
собирается ответ (`suggest.make_system_prompt(…, playbook=suggest.load_playbook())`), и адрес базы ему НЕ
передаётся: он читает свой. Мерило — число строк промпта, равных буллету урока, ДО записи и ПОСЛЕ: вошёл
только при «после = до + 1». Иначе перенос НЕ успех (код 3), и карточка говорит «записан, но в ответ ещё
не вошёл», называя, что читает читатель. Тот же замок стоит у двери перевода (`lesson_promote.py`).

ГДЕ ЖИВЁТ НОМЕР-ИСТОЧНИК. Строка базы несёт автора (`кто_записал` — кто переносил), время (`когда` —
момент переноса), свой номер, причину и источник `экзамен`. Графы для номера набора в формате базы нет, и
формат ради неё не переписывался (решение Штаба «хвост, а не переписывание»): номер набора, путь набора и
отпечатки лежат в записи о переносе `<база>.transfer`, связанной со строкой её номером.

ЧЕСТНО О ГРАНИЦАХ:
  • читатель сверяется В ПРОЦЕССЕ ДВЕРИ: тем же кодом и по тому же адресу, что у бота, но окружения
    процесса бота (например, `LESSON_BASE_READ_OFF` там) дверь не видит;
  • запись о переносе ложится ПОСЛЕ строки (ей нужны отпечатки «после»): оборвись процесс между ними —
    строка останется без записи, и откатить её переносом будет нельзя, только снятием («отмени урок N»);
  • откат освобождает номер: следующая запись в базу может получить тот же номер. Запись о переносе
    хранит отпечатки, поэтому байты не спутаются, но номер в истории станет неоднозначным.

С ТЕЛЕФОНА (22.09.2026, задание 71e TELEFONUROK). Та же дверь зовётся словом владельца в теме 205
(`pc_agent` → `_transfer_cli`): «урок перенос» → `--ready` (переносимые уроки номером и темой в
два-три слова), «урок перенеси N: причина» → `--from-set N --why-stdin`, «урок перенос откати M» →
`--rollback M`. Имя в `--who` агент берёт из ОПОЗНАННОГО ОТПРАВИТЕЛЯ сообщения, а не из текста;
причина едет через stdin в явном UTF-8 (`io_utf8.read_stdin_utf8`) — ровно тот путь, на котором
в набор легли три испорченных урока, закрыт тем же устройством, что у двери экзамена. Для боевой
базы карточка диктует откат СЛОВОМ, которое агент разбирает (`lesson_word_forms`), для копии —
консольную строку с явным адресом: слово без адреса ударило бы в боевую базу.

ЗАПУСК (боевые адреса — по умолчанию; `--set`/`--base` уводят на копии):
    venv/Scripts/python.exe lesson_transfer.py --list
    venv/Scripts/python.exe lesson_transfer.py --ready
    venv/Scripts/python.exe lesson_transfer.py --who <имя> --from-set 7 --why "причина словами"
    echo причина | venv/Scripts/python.exe lesson_transfer.py --who <имя> --from-set 7 --why-stdin
    venv/Scripts/python.exe lesson_transfer.py --who <имя> --rollback 10
    venv/Scripts/python.exe lesson_transfer.py --trace

Код возврата: 0 — сделано (или только чтение), 1 — отказано, ничего не записано, 2 — разбор командной
строки, 3 — ЗАПИСАНО, НО НЕ УСПЕХ: урок лёг в базу, а живой читатель его строки не вернул (или сверить
не удалось). Слова печатаются в stdout: их читает человек.
"""

import argparse
import hashlib
import os
import shutil
import sys
from collections import namedtuple

import io_utf8
import lesson_store as LS

HERE = os.path.dirname(os.path.abspath(__file__))
# Тот же файл, что `exam_show.LIVE_LESSONS` (равенство сверяет тест): экзамен здесь не импортируется —
# дверь не тянет за собой модуль показа карточек ради одного пути.
LIVE_SET_PATH = os.path.join(HERE, "exam_live", "lessons.tsv")

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NOT_IN_ANSWER = 3

STATUS_IN_ANSWER = "in_answer"
STATUS_CANDIDATE = "candidate"
STATUS_NOT_IN_ANSWER = "not_in_answer"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_REFUSED = "refused"
STATUS_DENIED = "denied"
STATUS_NO_AUTHOR = "no_author"
STATUS_ERROR = "error"

# ---------------------------------------------------------------------------------------
# ЗАПИСЬ О ПЕРЕНОСЕ — своя дописываемая таблица рядом с базой (как `.promote` и `.batch`)
# ---------------------------------------------------------------------------------------
TRANSFER_LOG_SUFFIX = ".transfer"
ACT_TRANSFER = "перенос"
ACT_UNTRANSFER = "откат_переноса"
MOVE_COLUMNS = ("когда", "действие", "номер_бота", "номер_набора", "набор", "кто", "почему",
                "состояние", "размер_до", "отпечаток_до", "размер_после", "отпечаток_после")
MOVE_HEADER = "\t".join(MOVE_COLUMNS)

Move = namedtuple("Move", "stamp act number set_number set_key who why state size_before sha_before "
                          "size_after sha_after line")

REASON_NO_NUMBER = "номер «%s» не читается — назови число"
REASON_SAME_FILE = "набор и база бота — один и тот же файл (%s): переносить некуда"
REASON_NO_SET = "набора нет по адресу %s"
REASON_SET_MISSING = "урока #%s в наборе нет"
REASON_SET_GONE = "урок набора #%s не действует и не ждёт: его состояние «%s» — переносить снятое нельзя"
REASON_MOJIBAKE = ("текст урока набора #%s испорчен кодировкой (UTF-8, прочитанный как cp1251): в промпт "
                   "ушёл бы нечитаемый текст. Сначала восстановить текст в наборе, потом переносить")
REASON_NO_BASE = ("базы бота нет по адресу %s. Первой строкой её не заводим: пока базы нет, читатель "
                  "отдаёт в промпт книгу-снимок, и база из одного урока заменила бы собой все её правила")
REASON_BASE_BLIND = "база бота по адресу %s не читается (%s) — писать в неё нельзя"
REASON_ALREADY = ("урок набора #%s уже перенесён: он урок бота #%s (%s, @%s). Второй раз не переносим; "
                  "откатить перенос — --rollback %s")
REASON_NO_MOVE = ("откат переноса НЕ сделан: урок бота #%s из набора не переносился — записи о переносе "
                  "нет. Откатывать нечего. Убрать действующий урок можно снятием («отмени урок %s») — это "
                  "новое состояние, а не возврат байтов")
REASON_MOVE_UNDONE = "откат переноса НЕ сделан: перенос урока бота #%s уже откачен (%s), второй раз нечего"
REASON_BASE_MOVED = ("откат переноса НЕ сделан: база после переноса урока #%s изменилась (размер был %s, "
                     "стал %s) — откат вернул бы не те байты. Ничего не тронуто. Если урок после переноса "
                     "переводили в действующие — сначала «урок откати %s», потом откат переноса")
REASON_BASE_GONE = "откат переноса НЕ сделан: базы бота нет по адресу %s"


def transfer_log_path(base=None):
    """Путь записи о переносе — рядом со своей базой и производный от неё (тест, подменивший базу,
    уводит туда же и запись)."""
    return LS._path(base) + TRANSFER_LOG_SUFFIX


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def set_key(path):
    """Имя набора в записи о переносе: путь от корня репо прямыми косыми (с другого тома — полный)."""
    full = os.path.abspath(path)
    try:
        return os.path.relpath(full, HERE).replace("\\", "/")
    except ValueError:
        return full.replace("\\", "/")


def _to_int(n):
    try:
        return int(str(n).strip())
    except (TypeError, ValueError):
        return None


def _move_append(base, stamp, act, number, set_number, key, who, why, state,
                 size_before, sha_before, size_after, sha_after):
    """Одна строка записи о переносе. Дозапись + `fsync` тем же писателем, что у базы и её следов."""
    log_path = transfer_log_path(base)
    need_header = (not os.path.exists(log_path)) or os.path.getsize(log_path) == 0
    row = "\t".join((stamp, act, str(number), str(set_number), LS.esc(key), LS.esc(who),
                     LS.esc(why), state, str(size_before), sha_before, str(size_after), sha_after))
    LS._append_row(log_path, row, need_header, header=MOVE_HEADER)


def load_moves(base=None, number=None, set_number=None, key=None):
    """Записи о переносе → кортеж Move в порядке файла. Фильтры — номер БОТА, номер НАБОРА и имя набора.
    Битая строка пропускается для читателя, но из файла не исчезает."""
    log_path = transfer_log_path(base)
    if not os.path.exists(log_path):
        return ()
    out = []
    with open(log_path, encoding="utf-8", newline="") as f:
        for idx, line in enumerate(f, start=1):
            body = line.rstrip("\n").rstrip("\r")
            if idx == 1 and body == MOVE_HEADER:
                continue
            p = body.split("\t")
            if len(p) != len(MOVE_COLUMNS):
                continue
            nums = [_to_int(p[i]) for i in (2, 3, 8, 10)]
            if any(x is None for x in nums):
                continue
            mv = Move(p[0], p[1], nums[0], nums[1], LS.unesc(p[4]), LS.unesc(p[5]), LS.unesc(p[6]),
                      p[7], nums[2], p[9], nums[3], p[11], idx)
            if number is not None and mv.number != int(number):
                continue
            if set_number is not None and mv.set_number != int(set_number):
                continue
            if key is not None and mv.set_key != key:
                continue
            out.append(mv)
    return tuple(out)


def open_transfer(base, set_number, key):
    """Действующий (не откаченный) перенос урока набора → Move | None. Судит ПОСЛЕДНЯЯ запись о нём."""
    seen = load_moves(base, set_number=set_number, key=key)
    if len(seen) == 0 or seen[-1].act != ACT_TRANSFER:
        return None
    return seen[-1]


# ---------------------------------------------------------------------------------------
# ИСПОРЧЕННАЯ КОДИРОВКА — UTF-8, прочитанный как cp1251 (разбор 68v: три кандидата живого набора)
# ---------------------------------------------------------------------------------------
# Кириллическая буква UTF-8 — это два байта: ведущий D0/D1 и продолжение 80–BF. Прочитанные как cp1251,
# они дают «Р»/«С» и знак из верхней половины таблицы (0x80–0xBF). В живом русском тексте такая пара
# почти не встречается, в испорченном — на каждой букве.
_CP1251_HIGH = frozenset(bytes(range(0x80, 0xC0)).decode("cp1251", errors="ignore"))


def looks_mojibake(text):
    """Текст похож на UTF-8, прочитанный как cp1251 → True. Порог: не меньше трёх пар и пары дают не
    меньше четверти кириллических знаков."""
    s = str(text or "")
    pairs = sum(1 for a, b in zip(s, s[1:]) if a in "РС" and b in _CP1251_HIGH)
    letters = sum(1 for ch in s if "Ѐ" <= ch <= "ӿ")
    return pairs >= 3 and pairs * 4 >= letters


# ---------------------------------------------------------------------------------------
# ЖИВОЙ ЧИТАТЕЛЬ ПРАВИЛ — сверка «вошёл ли урок в собранный промпт»
# ---------------------------------------------------------------------------------------
# `count is None` — ТРЕТИЙ ИСХОД: промпт не собрался или буллета нет. Он никогда не читается как «вошёл».
Answer = namedtuple("Answer", "count say address off")
Seen = namedtuple("Seen", "ok before after say")


def reader_address():
    """Что читает читатель правил сейчас → (адрес базы, выключено ли чтение)."""
    import suggest                                  # noqa: PLC0415 — тяжёлый модуль, только по делу
    path = suggest.LESSON_BASE_PATH if suggest.LESSON_BASE_PATH is not None else LS.STORE_PATH
    return path, bool(suggest.LESSON_BASE_OFF)


def live_prompt():
    """Системный промпт, собранный БОЕВЫМ путём: `load_playbook()` → `make_system_prompt(…, playbook=…)`
    — тот же вызов, что у сборки ответа и у `test_lesson_read`. Адрес базы читателю НЕ передаётся."""
    import suggest                                  # noqa: PLC0415
    return suggest.make_system_prompt("", "ru", playbook=suggest.load_playbook())


def bullet_of(lesson):
    """Урок → буллет в той форме, в какой читатель кладёт его в промпт (`suggest._lesson_bullet`)."""
    import suggest                                  # noqa: PLC0415
    return suggest._lesson_bullet(lesson)


def row_bullet(number, base=None):
    """Строка урока #number базы → её буллет, либо пустая строка (строки нет / база не читается)."""
    try:
        store = LS.load(base)
    except Exception:                               # noqa: BLE001 — «не знаю» вместо падения двери
        return ""
    for les in store.lessons:
        if les.number == _to_int(number):
            return bullet_of(les)
    return ""


def answer_count(bullet, build=None):
    """Сколько строк собранного промпта равно буллету → Answer."""
    if not bullet:
        return Answer(None, "буллета урока нет — сверять нечего", None, None)
    try:
        address, off = reader_address()
        text = (build or live_prompt)()
    except Exception as e:                          # noqa: BLE001 — третий исход, а не падение
        return Answer(None, "промпт не собран (%s: %s)" % (type(e).__name__, e), None, None)
    got = sum(1 for ln in str(text or "").splitlines() if ln.strip() == bullet)
    return Answer(got, "", address, off)


def answer_verdict(bullet, before, build=None):
    """ДО записи (`before`) и ПОСЛЕ → вошёл ли урок в ответ. → Seen.

    Вошёл — ТОЛЬКО при «после = до + 1». Совпадение того же текста в промпте из чужой строки или книги
    вхождением не считается: до записи оно уже было и в разности не участвует."""
    after = answer_count(bullet, build)
    if before is None or before.count is None or after.count is None:
        why = (before.say if before is not None and before.say else after.say) or "нет замера до записи"
        return Seen(False, None if before is None else before.count, after.count, "НЕ СВЕРЕНО: " + why)
    if after.count == before.count + 1:
        return Seen(True, before.count, after.count,
                    "живой читатель вернул строку урока в собранном промпте (таких строк было %d, "
                    "стало %d)" % (before.count, after.count))
    return Seen(False, before.count, after.count,
                "живой читатель строку урока НЕ вернул: таких строк в собранном промпте до записи %d, "
                "после %d; читатель читает %s%s"
                % (before.count, after.count, after.address,
                   ", и чтение базы ВЫКЛЮЧЕНО (LESSON_BASE_READ_OFF)" if after.off else ""))


def promoted_not_in_answer_card(dec, seen, path=None):
    """Карточка перевода (`trainer._promote_lesson`), у которой читатель урока не увидел: первая строка
    («ВКЛЮЧЁН — бот отвечает…») заменяется правдой, остальные (причина, след, откат) остаются."""
    rest = str(dec.get("card") or "").split("\n")[1:]
    head = ("📝 Урок #%s переведён в действующие базы %s, но в ответ бота ещё НЕ вошёл: %s. Перевод "
            "записан; слов «бот отвечает» нет, пока читатель правил не вернул его строку."
            % (dec.get("n"), set_key(LS._path(path)), seen.say))
    return "\n".join([head] + rest)


# ---------------------------------------------------------------------------------------
# ПЕРЕНОС И ОТКАТ ПЕРЕНОСА
# ---------------------------------------------------------------------------------------
def _default_may_write(username):
    from moderation_core import may_write_rule      # noqa: PLC0415 — ленивый: тяжёлый модуль
    return may_write_rule(username)


def _promote_hint(number, base_path):
    """Как включить кандидата #number ЭТОЙ базы → строка. Слово в теме переводит в БОЕВОЙ базе (без
    `--path`), поэтому оно диктуется только боевой базе; копии — консольная строка с явным адресом.
    Иначе слово ударило бы в одноимённый номер чужой базы — ровно класс 70x (дверь A2)."""
    import lesson_word_forms                        # noqa: PLC0415 — форма живёт там же, где разбор
    if _same_file(base_path, LS.STORE_PATH):
        # падеж — из атома темы (`PROMOTE_TOPIC`): «в тема «PC-дев»» читалось с телефона криво (71e)
        return "в теме %s: «%s»" % (lesson_word_forms.PROMOTE_TOPIC,
                                    lesson_word_forms.promote_phrase(number))
    return ("консолью: venv/Scripts/python.exe lesson_promote.py --path %s --who <имя> --promote %s "
            "--why \"%s\"" % (set_key(base_path), number, lesson_word_forms.PROMOTE_REASON_SLOT))


def _rollback_hint(number, base_path):
    """Как откатить перенос урока бота #number ЭТОЙ базы → строка. Слово в теме 205 бьёт в БОЕВУЮ
    базу (агент зовёт дверь без `--base`), поэтому диктуется только ей; копии — консольная строка с
    явным адресом. Довод тот же, что у `_promote_hint`: слово не должно ударить в чужую базу."""
    import lesson_word_forms                        # noqa: PLC0415
    if _same_file(base_path, LS.STORE_PATH):
        return "одним сообщением в теме %s: «%s»" % (lesson_word_forms.PROMOTE_TOPIC,
                                                     lesson_word_forms.transfer_back_phrase(number))
    return ("консолью: venv/Scripts/python.exe lesson_transfer.py --base %s --who <имя> --rollback %s"
            % (set_key(base_path), number))


def _refused(reason, **kw):
    return dict(kw, status=STATUS_REFUSED, reason=reason, card="⛔ %s." % reason)


def _gate(num, who, may_write, what):
    """Номер, автор, право → (имя, отказ-или-None)."""
    author = str(who or "").strip().lstrip("@")
    if num is None:
        return author, {"status": STATUS_REFUSED, "card": "⛔ %s НЕ сделан: номер не читается." % what}
    if not author:
        return author, {"status": STATUS_NO_AUTHOR,
                        "card": "⛔ %s НЕ сделан: не назван автор (--who). Движение правилом бота "
                                "безымянным не бывает, а имя из чужой записи не подставляем." % what}
    if not (may_write or _default_may_write)(author):
        return author, {"status": STATUS_DENIED,
                        "card": "⛔ Нет прав — %s НЕ сделан. Нужно то же право, что у записи урока."
                                % what}
    return author, None


def _transfer(set_number, who, why, src, base, may_write, now, build):
    num = _to_int(set_number)
    author, stop = _gate(num, who, may_write, "перенос урока набора #%s" % set_number)
    if stop:
        return stop
    src_path = LIVE_SET_PATH if src is None else src
    base_path = LS._path(base)
    if _same_file(src_path, base_path):
        return _refused(REASON_SAME_FILE % set_key(base_path))
    got = LS.load(src_path)
    if not got.exists:
        return _refused(REASON_NO_SET % set_key(src_path))
    les = next((x for x in got.lessons if x.number == num), None)
    if les is None:
        return _refused(REASON_SET_MISSING % num)
    if not (LS.is_active(les) or LS.is_candidate(les)):
        return _refused(REASON_SET_GONE % (num, les.state))
    if looks_mojibake(les.correct):
        return _refused(REASON_MOJIBAKE % num)
    if not os.path.isfile(base_path):
        return _refused(REASON_NO_BASE % set_key(base_path))
    have = LS.load(base_path)
    if have.reading.blind:
        return _refused(REASON_BASE_BLIND % (set_key(base_path), have.reading.say("строк")))
    key = set_key(src_path)
    prior = open_transfer(base_path, num, key)
    if prior is not None:
        return _refused(REASON_ALREADY % (num, prior.number, prior.stamp, prior.who, prior.number))

    reason = " ".join(str(why or "").split())
    stamp = LS.now_stamp(now)
    data_before = _read_bytes(base_path)
    expect, before = "", None
    if reason:
        # Буллет, который ДОЛЖЕН появиться, считается до записи — из тех же полей, той же чисткой и тем
        # же штампом, что уйдут в строку. Замер «до» берётся по нему же: иначе разность не о чём.
        clean = LS.scrub_lesson(les.question, les.bot_answer, les.correct, reason)
        expect = bullet_of(LS.Lesson(0, "", "", clean.correct, "", "", stamp, "", 0))
        before = answer_count(expect, build)
    common = dict(question=les.question, bot_answer=les.bot_answer, correct=les.correct, who=author,
                  source=LS.SOURCE_EXAM, when=stamp, path=base_path)
    try:
        if reason:
            number = LS.add(why=reason, **common)
        else:
            number = LS.add_candidate(**common)
    except LS.LessonRejected as e:
        return _refused("урок набора #%s НЕ перенесён: %s" % (num, e.reason))
    data_after = _read_bytes(base_path)
    state = LS.STATE_ACTIVE if reason else LS.STATE_CANDIDATE
    _move_append(base_path, stamp, ACT_TRANSFER, number, num, key, author, reason, state,
                 len(data_before), _sha(data_before), len(data_after), _sha(data_after))

    out = {"n": number, "set_n": num, "who": author, "why": reason, "state": state, "stamp": stamp,
           "sha_before": _sha(data_before), "sha_after": _sha(data_after)}
    trail = ("↩️ Откатить перенос %s.\n📌 След: перенёс @%s, %s; урок набора #%s (%s) → урок бота #%s "
             "(%s)." % (_rollback_hint(number, base_path), author, stamp, num, key, number,
                        set_key(base_path)))
    if not reason:
        return dict(out, status=STATUS_CANDIDATE, card=(
            "📝 Урок набора #%s записан в базу бота КАНДИДАТОМ как урок #%s: причина не названа, поэтому в "
            "ответ он НЕ входит и бот по нему не отвечает. Включить — %s\n%s"
            % (num, number, _promote_hint(number, base_path), trail)))
    landed = next((x for x in LS.load(base_path).lessons if x.number == number), None)
    if landed is None or bullet_of(landed) != expect:
        seen = Seen(False, before.count, None,
                    "НЕ СВЕРЕНО: строка легла не в том виде, по которому мерили промпт до записи")
    else:
        seen = answer_verdict(expect, before, build)
    out.update(answer_before=seen.before, answer_after=seen.after, seen=seen.say)
    if seen.ok:
        return dict(out, status=STATUS_IN_ANSWER, card=(
            "✅ Урок набора #%s перенесён в базу бота как урок #%s и ВОШЁЛ В ОТВЕТ: %s — бот отвечает по "
            "нему со следующего ответа.\n📌 Причина: %s\n%s" % (num, number, seen.say, reason, trail)))
    return dict(out, status=STATUS_NOT_IN_ANSWER, card=(
        "⛔ Перенос НЕ состоялся как правило: урок набора #%s записан в базу %s как урок #%s («%s»), но в "
        "ответ ещё НЕ вошёл — %s.\n%s" % (num, set_key(base_path), number, state, seen.say, trail)))


def transfer_lesson(set_number, who=None, why=None, src=None, base=None, may_write=None, now=None,
                    build=None):
    """«перенеси урок набора N» → dict(status, card, …). Статусы: `in_answer` | `candidate` |
    `not_in_answer` | `refused` | `denied` | `no_author` | `error`. НИКОГДА не бросает.

    `src` — набор (по умолчанию живой), `base` — база бота (по умолчанию та, что читает бот); `may_write`,
    `now`, `build` (сборщик промпта) подменяются в тестах."""
    try:
        return _transfer(set_number, who, why, src, base, may_write, now, build)
    except Exception as e:                          # noqa: BLE001 — дверь отвечает словами
        return {"status": STATUS_ERROR,
                "card": "⚠️ Перенос урока набора #%s — внутренняя ошибка (%s: %s). Успехом это не "
                        "считается; что легло, смотри --trace и базу." % (set_number, type(e).__name__, e)}


def _truncate_to(target, data):
    """Файл базы → ровно `data`. Та же дисциплина, что у `lesson_store._rewrite`: копия `.bak` ДО,
    новое тело во ВРЕМЕННЫЙ файл (усекается ОН), атомарный `os.replace`, подъём версии по факту."""
    shutil.copyfile(target, target + LS.BACKUP_SUFFIX)
    tmp = target + LS.TMP_SUFFIX
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    LS._stamp_version(target)


def _rollback(number, who, base, may_write, now):
    num = _to_int(number)
    author, stop = _gate(num, who, may_write, "откат переноса урока бота #%s" % number)
    if stop:
        return stop
    base_path = LS._path(base)
    seen = load_moves(base_path, number=num)
    if len(seen) == 0:
        return _refused(REASON_NO_MOVE % (num, num))
    last = seen[-1]
    if last.act != ACT_TRANSFER:
        return _refused(REASON_MOVE_UNDONE % (num, last.stamp))
    if not os.path.isfile(base_path):
        return _refused(REASON_BASE_GONE % set_key(base_path))
    data = _read_bytes(base_path)
    if len(data) != last.size_after or _sha(data) != last.sha_after:
        return _refused(REASON_BASE_MOVED % (num, last.size_after, len(data), num))
    prefix = data[:last.size_before]
    if _sha(prefix) != last.sha_before:
        return _refused(REASON_BASE_MOVED % (num, last.size_after, len(data), num))
    stamp = LS.now_stamp(now)
    _truncate_to(base_path, prefix)
    after = _read_bytes(base_path)
    _move_append(base_path, stamp, ACT_UNTRANSFER, num, last.set_number, last.set_key, author,
                 last.why, last.state, len(data), _sha(data), len(after), _sha(after))
    return {"status": STATUS_ROLLED_BACK, "n": num, "set_n": last.set_number, "who": author,
            "sha_before": last.sha_before, "sha_after": _sha(after),
            "card": ("↩️ Перенос урока бота #%s ОТКАЧЕН: база %s вернулась к байтам до переноса "
                     "(отпечаток %s, сверен с записью о переносе: %s). Урок набора #%s в наборе не тронут.\n"
                     "📌 След: откатил @%s, %s."
                     % (num, set_key(base_path), _sha(after)[:16],
                        "совпал" if _sha(after) == last.sha_before else "НЕ СОВПАЛ",
                        last.set_number, author, stamp))}


def rollback_transfer(number, who=None, base=None, may_write=None, now=None):
    """«откати перенос урока бота M» → dict(status, card, …). `rolled_back` | `refused` | `denied` |
    `no_author` | `error`. НИКОГДА не бросает."""
    try:
        return _rollback(number, who, base, may_write, now)
    except Exception as e:                          # noqa: BLE001
        return {"status": STATUS_ERROR,
                "card": "⚠️ Откат переноса урока бота #%s — внутренняя ошибка (%s: %s). Успехом это не "
                        "считается; смотри --trace и базу." % (number, type(e).__name__, e)}


# ---------------------------------------------------------------------------------------
# ЧТЕНИЕ: перечень набора и след переносов. `--list`/`--trace` текстов уроков не печатают — только
# номера и признаки; `--ready` (телефон) добавляет к номеру тему в два-три слова — начало правила.
# ---------------------------------------------------------------------------------------
def list_card(src=None, base=None):
    src_path = LIVE_SET_PATH if src is None else src
    got = LS.load(src_path)
    if not got.exists:
        return "Набора нет по адресу %s." % set_key(src_path)
    key = set_key(src_path)
    out = ["Урок набора %s (номер · состояние · причина критика · текст · перенос):" % key]
    ready = 0
    for les in got.lessons:
        prior = open_transfer(LS._path(base), les.number, key)
        spoiled = looks_mojibake(les.correct)
        live = LS.is_active(les) or LS.is_candidate(les)
        if live and not spoiled and prior is None:
            ready += 1
        out.append("  #%-3d %-9s %-13s %-22s %s" % (
            les.number, les.state, "есть" if les.why.strip() else "нет",
            "испорчен кодировкой" if spoiled else "цел",
            ("урок бота #%d" % prior.number) if prior is not None else "не перенесён"))
    out.append("— строк %d; можно переносить сейчас: %d. Перенести: --who <имя> --from-set N --why "
               "\"причина словами\"." % (len(got.lessons), ready))
    return "\n".join(out)


# ТЕМА УРОКА В ДВА-ТРИ СЛОВА — для перечня С ТЕЛЕФОНА (71e). Графы темы в формате набора нет, и
# формат ради неё не переписывался; выдумывать тему за владельца тоже нечем. Поэтому тема — это
# НАЧАЛО самого правила («как правильно»): первые три слова, а если третье — связка («не», «на»,
# «под»…), то до первого слова со смыслом, но не длиннее пяти. Отрицание не срезается: «не повторяй»
# и «повторяй» — противоположные уроки. Текст вопроса клиента и ответа бота в перечень не попадает.
_TOPIC_GLUE = frozenset("не ни на в во к ко о об обо и а с со у по под над для при про из от до "
                        "как чем что если пока когда это то же ли бы или".split())
TOPIC_WORDS = 3
TOPIC_MAX = 5
_TOPIC_STRIP = ".,;:!?()[]«»\"'—–-"


def topic_of(text):
    """Правило урока → тема в два-три слова (его начало) → str; пустое правило → «(без текста)»."""
    words = [w.strip(_TOPIC_STRIP) for w in str(text).split()]
    words = [w for w in words if w]
    if not words:
        return "(без текста)"
    out = []
    for w in words:
        out.append(w)
        if len(out) >= TOPIC_MAX:
            break
        if len(out) >= TOPIC_WORDS and out[-1].lower() not in _TOPIC_GLUE:
            break
    return " ".join(out) + ("…" if len(words) > len(out) else "")


def ready_card(src=None, base=None):
    """Перечень для телефона: какие уроки набора можно перенести СЕЙЧАС — номер и тема в два-три
    слова; что не переносится и почему — номерами. Только чтение."""
    import lesson_word_forms                        # noqa: PLC0415
    src_path = LIVE_SET_PATH if src is None else src
    got = LS.load(src_path)
    if not got.exists:
        return "Набора нет по адресу %s." % set_key(src_path)
    key = set_key(src_path)
    ready, moved, spoiled, gone = [], [], [], []
    for les in got.lessons:
        prior = open_transfer(LS._path(base), les.number, key)
        if prior is not None:
            moved.append("#%d → урок бота #%d" % (les.number, prior.number))
        elif not (LS.is_active(les) or LS.is_candidate(les)):
            gone.append("#%d" % les.number)
        elif looks_mojibake(les.correct):
            spoiled.append("#%d" % les.number)
        else:
            ready.append("#%d — %s" % (les.number, topic_of(les.correct)))
    out = ["📚 Уроки набора экзамена, которые можно перенести в базу бота: %d из %d."
           % (len(ready), len(got.lessons))]
    out += ready
    if moved:
        out.append("Уже в базе бота: %s." % ", ".join(moved))
    if spoiled:
        out.append("Не переносятся — текст испорчен кодировкой: %s." % ", ".join(spoiled))
    if gone:
        out.append("Не переносятся — сняты: %s." % ", ".join(gone))
    out.append("Перенести: «%s». Номер — из этого перечня (номер НАБОРА). Без причины урок ляжет "
               "кандидатом и в ответ бота не войдёт." % lesson_word_forms.transfer_phrase())
    return "\n".join(out)


def trace_card(base=None):
    seen = load_moves(base)
    if len(seen) == 0:
        return "Записей о переносе нет: ни один урок набора в базу бота ещё не переносили."
    out = ["Записи о переносе (когда · что · урок бота · урок набора · кто):"]
    for mv in seen:
        out.append("  %s  %-15s #%-4d ← набор #%-4d @%s" % (mv.stamp, mv.act, mv.number, mv.set_number,
                                                          mv.who))
    out.append("— всего движений: %d" % len(seen))
    return "\n".join(out)


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(
        description="перенос ОДНОГО урока набора экзамена в базу правил бота (право + причина + откат)")
    ap.add_argument("--who", default="", help="кто переносит (имя в Telegram, без @)")
    ap.add_argument("--from-set", type=int, metavar="N", dest="from_set",
                    help="перенести урок НАБОРА #N в базу бота")
    ap.add_argument("--why", default="", help="причина СЛОВАМИ; пусто — урок ляжет кандидатом")
    ap.add_argument("--why-stdin", action="store_true", dest="why_stdin",
                    help="причину прочесть со stdin в UTF-8 (так её везёт агент темы 205)")
    ap.add_argument("--rollback", type=int, metavar="M", help="откатить перенос урока БАЗЫ БОТА #M")
    ap.add_argument("--list", action="store_true", help="уроки набора и их перенос (чтение)")
    ap.add_argument("--ready", action="store_true",
                    help="переносимые уроки номером и темой в два-три слова (чтение, для телефона)")
    ap.add_argument("--trace", action="store_true", help="записи о переносе (чтение)")
    ap.add_argument("--set", dest="src", help="другой файл набора (по умолчанию exam_live/lessons.tsv)")
    ap.add_argument("--base", help="другой файл базы бота (по умолчанию lesson_store.tsv)")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.list:
        print(list_card(args.src, args.base))
        return EXIT_OK
    if args.ready:
        print(ready_card(args.src, args.base))
        return EXIT_OK
    if args.trace:
        print(trace_card(args.base))
        return EXIT_OK
    named = [x for x in (args.from_set, args.rollback) if x is not None]
    if len(named) != 1:
        print("Назови РОВНО одно движение: --from-set N (номер НАБОРА) либо --rollback M (номер БАЗЫ "
              "БОТА). Посмотреть: --list / --trace.")
        return EXIT_REFUSED
    if args.from_set is not None:
        why = args.why
        if args.why_stdin:
            if why.strip():
                print("Причину назови ОДНИМ путём: --why либо --why-stdin. Ничего не перенесено.")
                return EXIT_REFUSED
            why = io_utf8.read_stdin_utf8()
        dec = transfer_lesson(args.from_set, who=args.who, why=why, src=args.src, base=args.base)
    else:
        dec = rollback_transfer(args.rollback, who=args.who, base=args.base)
    print(dec["card"])
    if dec["status"] in (STATUS_IN_ANSWER, STATUS_CANDIDATE, STATUS_ROLLED_BACK):
        return EXIT_OK
    if dec["status"] == STATUS_NOT_IN_ANSWER:
        return EXIT_NOT_IN_ANSWER
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
