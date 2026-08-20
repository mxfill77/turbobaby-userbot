# -*- coding: utf-8 -*-
"""
card_load.py — ЗАМЕР НАГРУЗКИ НАДЗОРА: сколько карточек в сутки и сколько человек держал
карточку до нажатия.

ЗАЧЕМ ЗАВЕДЁН (решение владельца 20.08.2026, дословная причина). Сейчас низкий сезон, клиентов
немного, ручной режим выдерживается. К ноябрю поток вырастет, и вопрос будет не «успевают ли»,
а «смотрят ли»: **падение времени на карточку до секунд означает, что надзор выродился в
рефлекс**. Это обязано быть видно ЧИСЛОМ, а не ощущением — потому счётчик ставится СРАЗУ, за
три месяца до того, как станет нужен. Числа КОПЯТСЯ и владельцу НЕ ПОКАЗЫВАЮТСЯ: в тексте
карточки их нет ни одной строкой, и ни один читатель этого модуля в карточку не ведёт
(замок — тест `TestLoadMeterIsNotShownToOwner`).

ЧТО ИМЕННО МЕРЯЕТСЯ — две РАЗНЫЕ величины, и путать их нельзя:

  • `wait`  — от ПОСЛЕДНЕГО показа предложения до нажатия. Это время на ТЕКУЩЕМ тексте:
              клиент дописал → бот пересобрал предложение → человек обязан прочитать ЗАНОВО,
              и отсчёт начинается заново. Именно `wait` вырождается в секунды при рефлексе.
  • `open`  — от ПЕРВОГО показа карточки разговора до первого нажатия. Это срок жизни карточки;
              он растёт от того, что клиент долго думает, а не от того, что человек невнимателен.

ТРЕТИЙ ИСХОД ОБЯЗАТЕЛЕН. Нажатие, перед которым показа не нашлось, НЕ считается нулевым
ожиданием — оно уходит в `unpaired` отдельным числом. Медиана пустого набора — `None`
(«не знаю»), а не `0`: ноль здесь означал бы «жали мгновенно», то есть ровно тот сигнал, ради
которого счётчик и заведён.

СВОИ СБОИ СЧЁТЧИК ГОВОРИТ ВСЛУХ. «Замер пуст» и «замер сломан» — разные вещи: неудачная запись
копится в `errors()` и попадает в сводку полем `meter_errors`. Молча вернуть «нарушений ноль»
этот модуль не умеет ни одной веткой.

Хранилище — JSONL (`tmp/card_load/events.jsonl`): дописывание строкой, никакой СУБД, никакой
сети. В боевую очередь `moderation_ipc.db` счётчик НЕ пишет ни байта.
"""

import datetime
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BASE_DIR, "tmp", "card_load", "events.jsonl")

EV_SHOWN = "shown"        # карточка разговора показана человеку (первый показ ИЛИ пересборка)
EV_PRESSED = "pressed"    # человек нажал (любое из действий карточки)
KINDS = (EV_SHOWN, EV_PRESSED)

_errors = []              # сбои САМОГО замера — вслух, а не молча


def errors():
    """Сбои записи, накопленные с начала процесса. → кортеж строк (пусто = сбоев не было)."""
    return tuple(_errors)


def reset_errors():
    """Сбросить накопленные сбои (нужен тестам и длинным прогонам)."""
    del _errors[:]


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _s(value):
    """Значение → строка. None приводим к пустой строке ЯВНО: «поля нет» и «поле пустое» —
    разные вещи, и решает это вызывающий, а не идиома `or`."""
    if value is None:
        return ""
    return str(value).strip()


def _dt(iso):
    """ISO-строка → datetime | None. Неразобранное — это «не знаю», а НЕ «сейчас»."""
    s = _s(iso)
    if len(s) == 0:
        return None
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def event(kind, card_key, ts=None, **extra):
    """Событие замера как словарь (чистая функция — пишет её `record`)."""
    if kind not in KINDS:
        raise ValueError("неизвестный вид события замера: " + repr(kind))
    if ts is None:
        stamp = _now_iso()
    else:
        stamp = _s(ts)
    rec = {"kind": kind, "card": _s(card_key), "ts": stamp}
    for k in sorted(extra):
        rec[k] = extra[k]
    return rec


def record(kind, card_key, ts=None, path=None, **extra):
    """Записать событие в копилку. → сам словарь события | None, если записать НЕ вышло.

    FAIL-SAFE: сбой диска не смеет уронить модерацию — но и промолчать не смеет, поэтому
    ветка `except` не пустая: она КЛАДЁТ причину в `errors()` (обработчик, сказавший вслух,
    слепым читателем не является)."""
    rec = event(kind, card_key, ts=ts, **extra)
    target = path
    if target is None:
        target = DEFAULT_PATH
    folder = os.path.dirname(target)
    try:
        if len(folder) > 0:
            os.makedirs(folder, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except (OSError, ValueError) as e:
        # ValueError здесь не для полноты: путь с нулевым байтом/битой кодировкой роняет
        # os.makedirs именно им, а не OSError, — и такой сбой замера обязан кончиться записью
        # в errors(), а не исключением, всплывшим в модерацию клиента.
        _errors.append(type(e).__name__ + ": " + str(e) + " (" + target + ")")
        return None
    return rec


def load(path=None):
    """Прочитать копилку. → список событий. Файла нет → пустой список (это ЧЕСТНО «событий не
    было»: файл создаётся первой же записью). Битая строка — в `errors()`, а не под ковёр."""
    target = path
    if target is None:
        target = DEFAULT_PATH
    if not os.path.exists(target):
        return []
    out = []
    try:
        with open(target, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        _errors.append("чтение: " + type(e).__name__ + ": " + str(e))
        return []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if len(s) == 0:
            continue
        try:
            out.append(json.loads(s))
        except ValueError as e:
            _errors.append("строка " + str(i + 1) + " не разобралась: " + str(e))
    return out


def _pct(values, share):
    """Перцентиль тем же приёмом, что в замере 20.08 (`v[int(len(v)*share)]`) — чтобы числа
    счётчика и числа замера считались ОДНОЙ линейкой. Пусто → None, а не 0."""
    if len(values) == 0:
        return None
    idx = int(len(values) * share)
    if idx > len(values) - 1:
        idx = len(values) - 1
    return values[idx]


def _median(values):
    """Медиана. Пусто → None («не знаю»), а не 0 («жали мгновенно»)."""
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def summarize(events):
    """Сводка по копилке. Чистая функция: ни диска, ни времени «сейчас».

    → dict:
      cards          — сколько РАЗЛИЧНЫХ карточек-разговоров вообще показывалось;
      per_day        — {дата: сколько карточек ПОЯВИЛОСЬ в этот день} (по ПЕРВОМУ показу);
      presses_per_day— {дата: сколько нажатий};
      shows          — сколько показов всего (пересборка предложения — это новый показ);
      waits          — отсортированные секунды «последний показ → нажатие»;
      opens          — отсортированные секунды «первый показ карточки → первое нажатие»;
      wait_median/wait_p90/wait_min, open_median — None при пустом наборе;
      unpaired       — нажатий, перед которыми показа НЕ НАЙДЕНО (третий исход, НЕ нули);
      undated        — событий с неразобранной меткой времени;
      meter_errors   — сбои самого замера (см. errors())."""
    shown = {}          # карточка → [метки показов]
    first_press = {}    # карточка → метка первого нажатия
    per_day = {}
    presses_per_day = {}
    waits = []
    opens = []
    unpaired = 0
    undated = 0
    shows = 0
    for ev in events:
        kind = _s(ev.get("kind"))
        card = _s(ev.get("card"))
        when = _dt(ev.get("ts"))
        if when is None:
            undated += 1
            continue
        if kind == EV_SHOWN:
            shows += 1
            if card not in shown:
                shown[card] = []
                day = when.date().isoformat()
                per_day[day] = per_day.get(day, 0) + 1
            shown[card].append(when)
            continue
        if kind == EV_PRESSED:
            day = when.date().isoformat()
            presses_per_day[day] = presses_per_day.get(day, 0) + 1
            marks = shown.get(card)
            if marks is None:
                unpaired += 1          # показа не было — НЕ ноль ожидания, а отдельное число
                continue
            earlier = [m for m in marks if m <= when]
            if len(earlier) == 0:
                unpaired += 1
                continue
            waits.append((when - max(earlier)).total_seconds())
            if card not in first_press:
                first_press[card] = when
                opens.append((when - min(marks)).total_seconds())
    waits.sort()
    opens.sort()
    return {"cards": len(shown), "shows": shows, "per_day": per_day,
            "presses_per_day": presses_per_day, "waits": waits, "opens": opens,
            "wait_median": _median(waits), "wait_p90": _pct(waits, 0.9),
            "wait_min": (waits[0] if len(waits) > 0 else None),
            "open_median": _median(opens), "unpaired": unpaired, "undated": undated,
            "meter_errors": errors()}


def stats(path=None):
    """Сводка прямо по файлу копилки (см. summarize)."""
    return summarize(load(path=path))
