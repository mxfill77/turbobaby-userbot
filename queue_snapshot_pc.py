# -*- coding: utf-8 -*-
"""queue_snapshot_pc.py — СЛЕПОК СОСТОЯНИЯ ОЧЕРЕДИ ПОЛОСЫ ПК В МОЗГ (14.08.2026).

ЗАЧЕМ. Штаб читает мозг САМ, а состояния очереди там не было: между «взял» и «сдал» — пустота,
и из этой пустоты на полосе VPS родились четыре дубля за трое суток. Класс-фикс кладётся на ОБЕ
полосы; там слепок уже стои́т и проверен живьём (ключ `queue_state`, цель 539), здесь его не было.

ЧТО ПЕРЕНЕСЕНО, А ЧТО НЕТ (правило зеркала: переносится ПОСЫЛКА, а не строки).

  Переносимо ЦЕЛИКОМ: предмет (состояние, а не история — файл заменяется каждый раз), разрез
  (в работе · ждёт владельца · стои́т в очереди · закрыто за сутки), повод записи (СМЕНА состояния,
  а не таймер) и шапка с временем снятия.

  НЕПЕРЕНОСИМО — МЕСТО НАБЛЮДАТЕЛЯ, и подгонять его нечем. На VPS слепок снимает слой ожиданий
  ВНЕ демона, и в его шапке честно написано почему: «он живёт ОТДЕЛЬНО от демона и потому видит
  „в работе“ ПОКА задача идёт». На ПК этот путь закрыт с двух сторон: слой ожиданий и задачу
  `TurboBabyExpectPC` трогать запрещено, а завести СВОЮ задачу Планировщика — красная операция.
  Значит писатель здесь живёт В ДЕМОНЕ, и «в работе» он кладёт САМ, в момент claim — ДО того как
  встанет на синхронный заход до 45 минут (`pc_orchestrator.TASK_TIMEOUT`). Выигрыш против
  оригинала: смена состояния видна МГНОВЕННО, а не с точностью до десятиминутного тика.
  Цена, и она названа вслух: замерший демон замораживает слепок — этот писатель НЕ СУДЬЯ полосы
  и права сказать «очередь стои́т» не имеет ни одной строкой. Смерть демона по-прежнему судят
  О2/О4 (наблюдатель, живущий вне), а здесь стои́т ровно ЗАМОК СВЕЖЕСТИ (ниже).

ЗАМОК: СЛЕПОК НЕ ВРЁТ СВЕЖЕСТЬЮ. Инвариант в одну строку — КАЖДОЕ время в файле ≤ реальности,
файл никогда не объявляет себя свежее, чем он есть. Три следствия, каждое покрыто тестом:
  • успешное снятие ставит в шапку `снято: <время>` — время ЭТОГО чтения очереди, не прошлого;
  • очередь не прочиталась — штамп НЕ ДВИГАЕТСЯ. Пока не прошёл порог `QSNAP_PC_STALE_MIN`,
    не пишется вообще ничего (одиночный промах моста состоянием не является);
  • промах затянулся — шапка МЕНЯЕТСЯ на «НЕ СВЕРЕНО», называет дату последнего верного снимка
    и прямо говорит, что строки ниже с того снимка. Свежего `снято:` при несверенном чтении не
    бывает ни на одной дороге.
Порог `QSNAP_PC_STALE_MIN` = 15 минут не выдуман: ровно это число стои́т в подвале серверного
слепка как правило ДЛЯ ЧИТАТЕЛЯ («старше 15 минут — считай состояние неизвестным»). Писатель
объявляет себя несверенным ровно тогда, когда читателю уже велено ему не верить.

ПОЧЕМУ ПОЛОВИНКИ «ЗАКРЫТО ЗА СУТКИ» РАЗНОГО ПРОИСХОЖДЕНИЯ (замер 14.08.2026, живой мост, lane=pc):
`get_pending("failed")` — 54 строки за 2.9с, `get_pending("done")` — 120 строк за 26.8с. Читать
`done` на каждом обороте нельзя, поэтому УПАВШИЕ читаются из очереди (дёшево и ПОЛНО за сутки,
вместе с причиной), а СДАННЫЕ выводятся из наблюдения: строка, ушедшая из открытых статусов и не
найденная среди упавших, — сдана. Отсюда честная асимметрия, названная в подвале самого файла:
половина «упало» полна всегда, половина «сдано» полна настолько, насколько работал писатель.
Между уходом строки и ближайшим чтением упавших исход называется «не сверен» — третий исход, а не
молчаливое «сдано».

ЧЕГО ЭТОТ МОДУЛЬ НЕ УМЕЕТ ПО УСТРОЙСТВУ: мутировать очередь (ни claim, ни complete, ни enqueue,
ни heartbeat — ни одного такого имени в файле нет), ставить задачи, писать владельцу, трогать
серверный ключ `queue_state`, доску владельца и рабочие таблицы. Только GET очереди и замена
СВОЕГО дока в мозге доверенным писателем.

ОТКАТ: `QSNAP_PC_OFF=1` в окружении демона — ветка мертва целиком, ни одного обращения наружу;
полностью — снять два вызова `_queue_snapshot(...)` в `pc_orchestrator.py`.

ЗАПУСК:
    venv\\Scripts\\python.exe queue_snapshot_pc.py --tick      # боевой оборот (пишет при смене)
    venv\\Scripts\\python.exe queue_snapshot_pc.py --dry       # что легло бы, мозг не трогаем
    venv\\Scripts\\python.exe queue_snapshot_pc.py --status    # то же, плюс решение словами
    venv\\Scripts\\python.exe queue_snapshot_pc.py --create    # создать док в мозге (один раз)
"""
import json
import os
import sys
import time

LANE = "pc"
DOC_KEY = "queue_state_pc"
DOC_TITLE = "KB_queue_state_pc"
OPEN_STATUSES = ("new", "in_progress", "needs_approval", "approved")
STATE_FILE = "queue_snapshot_pc.state.json"

GOAL_MAX = 90
WHY_MAX = 110

# Слова состояния — в одном месте: они едут и в файл, и в подпись.
WORD = {"new": "стои́т в очереди", "in_progress": "в работе",
        "needs_approval": "ждёт владельца", "approved": "одобрено, ждёт исполнения"}


def _int_from(mapping, name, default):
    """Целое из отображения; мусор и пустота → значение по умолчанию (о подмене говорим вслух)."""
    raw = mapping.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        print("%s=%r не число — беру %d" % (name, raw, default), file=sys.stderr)
        return default


def config(env=None):
    """Пороги ветки. `env` — любое отображение: тест задаёт пороги, не трогая окружение."""
    e = os.environ if env is None else env
    off = e.get("QSNAP_PC_OFF")
    return {"stale": _int_from(e, "QSNAP_PC_STALE_MIN", 15) * 60,
            "window": _int_from(e, "QSNAP_PC_WINDOW_H", 24) * 3600,
            "failed_max": _int_from(e, "QSNAP_PC_FAILED_MAX_MIN", 60) * 60,
            "off": off is not None and str(off).strip() not in ("", "0")}


# ═══════════════════ ЧИСТЫЙ СЛОЙ: ФАКТЫ → ТЕКСТ И ПОДПИСЬ ═══════════════════════════════════
# Ни одна функция ниже не ходит в мир: ни файла, ни сети, ни часов сверх переданного `now`.
# Граница держится инвариантом QSNAP_PC_PURE в тесте, а не обещанием этого абзаца.

def one_line(value, limit):
    """Первая НЕПУСТАЯ строка значения, схлопнутая в одну и обрезанная. Нечего показать → ""."""
    if value is None:
        return ""
    for raw in str(value).splitlines():
        piece = " ".join(raw.split())
        if piece:
            return piece if len(piece) <= limit else piece[:limit - 1] + "…"
    return ""


# Строки-пустышки в НАЧАЛЕ задания: ручки мощности, а не цель. Список не выдуман — в этом репо
# ИЗМЕРЕНО (24.07.2026, свод правил среды §2), что `ultrathink` уровень усилий не меняет вовсе и
# стоял в 10 заданиях из 11. Слепок, у которого КАЖДАЯ строка называется «ultrathink», не сообщает
# ничего: серверный оригинал показывает ровно это. Расхождение с ним здесь сознательное.
_NO_SIGNAL = frozenset(("ultrathink", "think", "think hard", "think harder", "think a lot",
                        "megathink", "ультраthink"))


def goal_line(text):
    """Первая ЗНАЧАЩАЯ строка задания. Все строки оказались пустышками → отдаём первую как есть:
    выдумывать цель нельзя, а молчать о строке — хуже, чем показать её «ultrathink»."""
    first = one_line(text, GOAL_MAX)
    if text is None:
        return ""
    for raw in str(text).splitlines():
        piece = " ".join(raw.split())
        if not piece:
            continue
        if piece.strip(" .!:—-").casefold() in _NO_SIGNAL:
            continue
        return piece if len(piece) <= GOAL_MAX else piece[:GOAL_MAX - 1] + "…"
    return first


def fmt_ts(ts):
    """Секунды эпохи → «ГГГГ-ММ-ДД ЧЧ:ММ:СС UTC». Времени нет → так и говорим словами."""
    if ts is None:
        return "НЕ БЫЛО НИ РАЗУ"
    try:
        num = float(ts)
    except (TypeError, ValueError):
        return "НЕ БЫЛО НИ РАЗУ"
    if num <= 0:
        return "НЕ БЫЛО НИ РАЗУ"
    import datetime
    return datetime.datetime.fromtimestamp(num, datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC")


def as_float(value):
    """Значение → число | None. `None` здесь — «не разобрали», и КАЖДАЯ ветка его различает:
    подставить вместо неразобранного времени ноль значило бы объявить строку вечно свежей."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def age_min(since, now):
    """Возраст в минутах | None, если времени начала нет. None — «не знаю», а не ноль."""
    a, b = as_float(since), as_float(now)
    if a is None or b is None:
        return None
    return int(max(0.0, b - a) // 60)


def _age_words(since, now):
    got = age_min(since, now)
    if got is None:
        return "возраст неизвестен"
    return "%d мин" % got


def _section(title, lines, empty_words):
    head = "%s (%d):" % (title, len(lines))
    if not lines:
        return head + "\n  " + empty_words
    return head + "\n" + "\n".join("  " + s for s in lines)


def render_body(rows, closed, failed_at, now):
    """Тело слепка (без шапки) — разрез очереди на момент `now`."""
    by = {}
    for row in rows:
        by.setdefault(str(row.get("status")), []).append(row)

    def lines_for(status):
        out = []
        for row in sorted(by.get(status, []), key=_row_key):
            out.append("%s · #%s · %s · %s · %s"
                       % (LANE, row.get("id"), WORD.get(status, status),
                          _age_words(row.get("since"), now), one_line(row.get("goal"), GOAL_MAX)))
        return out

    parts = [_section("В РАБОТЕ", lines_for("in_progress"), "никто не работает"),
             "",
             _section("ЖДЁТ ВЛАДЕЛЬЦА", lines_for("needs_approval"), "владельца никто не ждёт"),
             "",
             _section("СТОИТ В ОЧЕРЕДИ", lines_for("new") + lines_for("approved"),
                      "очередь пуста"),
             ""]

    done_ids, failed_rows, unsure_ids = [], [], []
    for tid, item in sorted(closed.items(), key=_closed_key):
        outcome = item.get("outcome")
        if outcome == "failed":
            failed_rows.append(item)
        elif outcome == "done":
            done_ids.append(tid)
        else:
            unsure_ids.append(tid)
    parts.append("ЗАКРЫТО ЗА СУТКИ — на момент снятия (%d: сдано %d, упало %d, исход не сверен %d):"
                 % (len(closed), len(done_ids), len(failed_rows), len(unsure_ids)))
    if done_ids:
        parts.append("  сдано: " + " ".join("#" + str(t) for t in done_ids))
    if failed_rows:
        parts.append("  упало:")
        for item in failed_rows:
            parts.append("    #%s · %s · причина: %s"
                         % (item.get("id"), one_line(item.get("goal"), GOAL_MAX),
                            one_line(item.get("why"), WHY_MAX) or "причина не названа очередью"))
    if unsure_ids:
        parts.append("  исход не сверен: " + " ".join("#" + str(t) for t in unsure_ids))
    if not closed:
        parts.append("  за сутки не закрылось ничего")
    parts.append("  (упавшие сверены с очередью %s — эта половина за сутки ПОЛНА;" % fmt_ts(failed_at))
    parts.append("   половина «сдано» — по наблюдению писателя, полна настолько, сколько он работал)")
    return "\n".join(parts)


def _row_key(row):
    try:
        return (0, int(row.get("id")))
    except (TypeError, ValueError):
        return (1, 0)


def _closed_key(pair):
    try:
        return (0, -int(pair[0]))
    except (TypeError, ValueError):
        return (1, 0)


HEAD = "СОСТОЯНИЕ ОЧЕРЕДИ ПОЛОСЫ ПК · слепок, заменяется целиком"
FOOT = """ЧТО ЭТО ЗА ФАЙЛ. Слепок состояния очереди полосы ПК — СОСТОЯНИЕ, а не история: каждая
запись затирает файл целиком, прошлых слепков здесь нет. История — в cowork_log.
Пишется ПО СМЕНЕ СОСТОЯНИЯ (взял · сдал · упал · ждёт владельца · встал в очередь), а не по
таймеру: между сменами записей нет вовсе.
Снимает САМ демон полосы (`pc_orchestrator`, отдельным процессом `queue_snapshot_pc.py`) — в
момент claim, то есть «в работе» появляется здесь ДО того, как демон встанет на синхронный заход.
Обратная сторона названа прямо: демон замер — замер и слепок. Судить по нему о ЖИЗНИ полосы
нельзя, для этого есть О2/О4; здесь только состояние очереди на названное время.
ПЕРЕД ОТПРАВКОЙ ЗАДАЧИ: сверься с временем снятия в шапке. Оно старше 15 минут — считай
состояние неизвестным и спроси очередь напрямую, а не по этому файлу."""


def render(view, now):
    """Полный текст слепка. `view` — то, что удалось узнать; шапка ЧЕСТНО называет его сорт."""
    if view.get("ok"):
        head = [HEAD,
                "снято: %s   ← ВОЗРАСТ СЧИТАТЬ ОТ ЭТОГО ВРЕМЕНИ" % fmt_ts(view.get("at")),
                "снял: демон полосы ПК, по смене состояния · возраст строк — на момент снятия"]
        body = render_body(view.get("rows", []), view.get("closed", {}),
                           view.get("failed_at"), view.get("at"))
    else:
        last = view.get("last_good")
        why = one_line(view.get("err"), WHY_MAX) or "причина не названа"
        when = "последний верный снимок: %s" % fmt_ts(last)
        if age_min(last, now) is not None:
            when += " (%s назад)" % _age_words(last, now)
        head = [HEAD,
                "НЕ СВЕРЕНО: очередь не читается — %s" % why,
                when + "   ← ВОЗРАСТ СЧИТАТЬ ОТ ЭТОГО ВРЕМЕНИ",
                "строки ниже — С ТОГО снимка и могли устареть; свежести у них нет"]
        body = view.get("body")
        if not body:
            body = "ТЕЛА НЕТ: верного снимка не случилось ни разу — состояние очереди неизвестно."
    return "\n".join(head) + "\n\n" + body + "\n\n" + FOOT + "\n"


def signature(view):
    """Ключ СМЕНЫ СОСТОЯНИЯ. Возрастов и штампов здесь нет СОЗНАТЕЛЬНО: они меняются каждый
    оборот, и подпись с ними означала бы запись каждый тик — ровно то, что запрещено."""
    if not view.get("ok"):
        return "stale|%s" % fmt_ts(view.get("last_good"))
    rows = ";".join("%s:%s" % (r.get("id"), r.get("status"))
                    for r in sorted(view.get("rows", []), key=_row_key))
    closed = ";".join("%s:%s" % (t, i.get("outcome"))
                      for t, i in sorted(view.get("closed", {}).items(), key=_closed_key))
    return "ok|%s|%s" % (rows, closed)


def merge_closed(prev_open, now_open, prev_closed, now, window):
    """Строки, ушедшие из открытых статусов, → в реестр закрытых с исходом «не сверен».
    Реестр подрезается окном суток. Чистая арифметика над переданным."""
    closed = {}
    for tid, item in prev_closed.items():
        at = as_float(item.get("at"))
        if at is not None and now - at <= window:
            closed[str(tid)] = dict(item)
    for tid, item in prev_open.items():
        if str(tid) in now_open:
            continue
        if str(tid) in closed:
            continue
        closed[str(tid)] = {"id": tid, "at": now, "goal": item.get("goal"),
                            "outcome": None, "why": ""}
    return closed


def apply_failed(closed, failed_items, read_at, window):
    """Итог чтения упавших → в реестр. Строка есть среди упавших — «упало» с причиной; ушла
    ДО этого чтения и там не найдена — «сдано»; ушла ПОСЛЕ — исход остаётся не сверенным."""
    out = {str(t): dict(i) for t, i in closed.items()}
    seen = set()
    for it in failed_items:
        if not isinstance(it, dict):
            continue
        tid = str(it.get("id"))
        seen.add(tid)
        at = as_float(it.get("since"))
        if at is None:
            at = read_at            # времени у строки нет — считаем её увиденной СЕЙЧАС, не вечной
        if read_at - at > window:
            continue
        out[tid] = {"id": it.get("id"), "at": at, "goal": it.get("goal"),
                    "outcome": "failed", "why": it.get("result")}
    for tid, item in out.items():
        at = as_float(item.get("at"))
        if item.get("outcome") is None and tid not in seen and at is not None and at <= read_at:
            item["outcome"] = "done"
    return out


# ═══════════════════ РУКИ: ЧТЕНИЕ ОЧЕРЕДИ, СОСТОЯНИЕ НА ДИСКЕ, ЗАПИСЬ В МОЗГ ════════════════

def state_file():
    """Путь файла состояния. Под тестом уводится в temp — боевой файл гейт не переписывает."""
    named = os.environ.get("QSNAP_PC_STATE")
    if named is not None and str(named).strip():
        return str(named).strip()
    import log_setup
    return log_setup.state_path(STATE_FILE)


def load_state():
    path = state_file()
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
    except FileNotFoundError:
        print("состояния нет (%s) — оборот первый" % path, file=sys.stderr)
        return {}
    except (OSError, ValueError) as e:
        print("состояние не прочитано (%s) — считаю оборот первым" % e, file=sys.stderr)
        return {}
    if isinstance(got, dict):
        return got
    print("состояние не словарь (%s) — считаю оборот первым" % type(got).__name__, file=sys.stderr)
    return {}


def save_state(st):
    """Best-effort: диска нет → худшее, что случится, — лишняя запись на следующем обороте."""
    path = state_file()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        return True
    except OSError as e:
        print("состояние не сохранено (%s)" % e, file=sys.stderr)
        return False


def _guard_test_logs(fn):
    """Выполнить `fn` под `TURBOBABY_TEST_LOGS=1` и вернуть окружение КАК БЫЛО. Не аккуратность.

    Флаг нужен на время импорта демона, иначе наш процесс вешает хендлер на БОЕВОЙ лог того, у
    кого мы одалживаем клиента (закрытый класс «тесты сорят в боевой лог»). Но ТОТ ЖЕ флаг читает
    `log_setup.is_test_context`, а по нему `brain_writer` ОТКАЗЫВАЕТСЯ трогать живой мозг. Оставь
    мы флаг стоять — писатель отказал бы в записи, и слепок не лёг бы ни разу при зелёном на вид
    прогоне: отказ пришёл бы ПОСЛЕ всей работы и выглядел бы сбоем мозга, а не нашим флагом.
    Возврат стои́т в `finally` СОЗНАТЕЛЬНО: падение импорта не смеет оставить флаг стоять."""
    key = "TURBOBABY_TEST_LOGS"
    had, prev = key in os.environ, os.environ.get(key)
    os.environ[key] = "1"
    try:
        return fn()
    finally:
        if had:
            os.environ[key] = prev
        else:
            del os.environ[key]


def _daemon():
    import pc_orchestrator as daemon
    return daemon


def bridge_getter():
    """Боевой клиент моста, одолженный у демона. Секреты берёт САМ импортируемый модуль —
    здесь их не читают и не видят (запрет класса 328)."""
    return _guard_test_logs(_daemon).bc.get_pending


def _rows_from(items, fallback_status):
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        status = it.get("status")
        out.append({"id": it.get("id"),
                    "status": str(status) if status else fallback_status,
                    "lane": it.get("lane"),
                    "since": _parse_iso(it.get("updated")) or _parse_iso(it.get("created")),
                    "goal": goal_line(it.get("task_text")),
                    "result": one_line(it.get("result"), WHY_MAX)})
    return out


def _parse_iso(value):
    """ISO-время моста → секунды эпохи | None. None — «времени нет», а не «сейчас»."""
    if value is None:
        return None
    import datetime
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        got = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=datetime.timezone.utc)
    return got.timestamp()


def read_open(get):
    """Открытые строки полосы ОДНИМ запросом (мост понимает CSV и подтверждает полем `statuses`).

    Подтверждения нет → CSV не разобран старым мостом, и `items` описывает НЕ ТО, что мы спросили;
    честный ответ — «не прочитали», а не половина снимка."""
    asked = ",".join(OPEN_STATUSES)
    try:
        r = get(asked)
    except Exception as e:                                            # noqa: BLE001
        return {"ok": False, "rows": [], "err": "мост не ответил (%s: %s)" % (type(e).__name__, e)}
    if not (isinstance(r, dict) and r.get("ok")):
        why = r.get("error") if isinstance(r, dict) else type(r).__name__
        return {"ok": False, "rows": [], "err": "мост ответил без ok (%s)" % why}
    got = r.get("statuses")
    if not (isinstance(got, (list, tuple)) and set(got) == set(OPEN_STATUSES)):
        return {"ok": False, "rows": [],
                "err": "мост не подтвердил список статусов (%r) — снимку верить нельзя" % (got,)}
    items = r.get("items")
    if not isinstance(items, list):
        return {"ok": False, "rows": [], "err": "мост подтвердил статусы, но строк не прислал"}
    rows = [x for x in _rows_from(items, "new")
            if x.get("lane") is None or str(x.get("lane")) == LANE]
    return {"ok": True, "rows": rows, "err": ""}


def read_status(get, status):
    """Строки ОДНОГО закрытого статуса. Отдельно от `read_open`: там подтверждение CSV — часть
    договора, здесь спрошен ровно один статус и подтверждать нечего."""
    try:
        r = get(status)
    except Exception as e:                                            # noqa: BLE001
        return {"ok": False, "items": [], "err": "%s: %s" % (type(e).__name__, e)}
    if not (isinstance(r, dict) and r.get("ok")):
        why = r.get("error") if isinstance(r, dict) else type(r).__name__
        return {"ok": False, "items": [], "err": str(why)}
    items = r.get("items")
    if not isinstance(items, list):
        return {"ok": False, "items": [], "err": "ответ ok, но строк нет — исход не сверен"}
    return {"ok": True, "items": _rows_from(items, status), "err": ""}


def read_failed(get):
    return read_status(get, "failed")


def seed_done(closed, done_items, read_at, window):
    """ПЕРВЫЙ оборот: половину «сдано» за сутки взять из очереди, а не ждать сутки наблюдений.

    Зовётся ровно один раз на жизнь состояния СОЗНАТЕЛЬНО: замер 14.08.2026 по живому мосту —
    `get_pending("done", lane=pc)` отдаёт 120 строк за 26.8с против 2.9с у `failed`. Такое чтение
    на каждом обороте недопустимо, разовое — дёшево, и оно закрывает ровно ту дыру, ради которой
    класс заводится: без него первые сутки слепок честно, но бесполезно показывал бы «сдано 0»."""
    out = {str(t): dict(i) for t, i in closed.items()}
    for it in done_items:
        if not isinstance(it, dict):
            continue
        at = as_float(it.get("since"))
        if at is None or read_at - at > window:
            continue
        tid = str(it.get("id"))
        if tid in out:
            continue
        out[tid] = {"id": it.get("id"), "at": at, "goal": it.get("goal"),
                    "outcome": "done", "why": ""}
    return out


def write_doc(text):
    """Замена дока целиком доверенным писателем. Слепок КОРОЧЕ прежнего — норма (очередь
    опустела), поэтому страж усушки снят ОСОЗНАННО и только здесь."""
    import brain_writer
    return brain_writer.apply(lambda old: text, name=DOC_KEY, expect=text,
                              backup_tag="queue_snapshot_pc", allow_shrink=True)


def create_doc(text=""):
    """Создать док слепка в мозге и зарегистрировать под ключом. Зовётся РУКАМИ, один раз:
    молчаливое создание из демона дало бы дубль на каждый сбой реестра."""
    import brain_writer
    return brain_writer.create_plain(DOC_TITLE, key=DOC_KEY,
                                     text=text if text else HEAD + "\n(док заведён, слепка ещё нет)")


# ═══════════════════════════════ ОБОРОТ ═════════════════════════════════════════════════════

def build_view(st, get, now, cfg):
    """Собрать то, что удалось узнать, → (view, новое состояние). Мир трогается ТОЛЬКО здесь."""
    prev_open = st.get("open")
    prev_closed = st.get("closed")
    if not isinstance(prev_open, dict):
        prev_open = {}
    if not isinstance(prev_closed, dict):
        prev_closed = {}
    opened = read_open(get)
    if not opened.get("ok"):
        return ({"ok": False, "err": opened.get("err"), "last_good": st.get("at"),
                 "body": st.get("body")}, st)

    now_open = {str(r.get("id")): {"goal": r.get("goal"), "status": r.get("status")}
                for r in opened["rows"]}
    closed = merge_closed(prev_open, now_open, prev_closed, now, cfg["window"])
    failed_at = as_float(st.get("failed_at"))
    unsure = [t for t, i in closed.items() if i.get("outcome") is None]
    stale_failed = failed_at is None or (now - failed_at) > cfg["failed_max"]
    if unsure or stale_failed:
        got = read_failed(get)
        if got.get("ok"):
            closed = apply_failed(closed, got["items"], now, cfg["window"])
            failed_at = now
        else:
            print("упавшие не прочитаны (%s) — исход остаётся не сверенным" % got.get("err"),
                  file=sys.stderr)
    seeded = st.get("seeded") is True
    if not seeded:
        got = read_status(get, "done")
        if got.get("ok"):
            closed = seed_done(closed, got["items"], now, cfg["window"])
            seeded = True
        else:
            print("сдано за сутки не засеяно (%s) — половина наполнится наблюдением"
                  % got.get("err"), file=sys.stderr)
    view = {"ok": True, "at": now, "rows": opened["rows"], "closed": closed, "failed_at": failed_at}
    return view, {"open": now_open, "closed": closed, "failed_at": failed_at, "seeded": seeded}


def tick(get=None, writer=None, now=None, cfg=None, dry=False):
    """Один оборот: снять → сравнить подпись → писать ТОЛЬКО при смене. → словарь итога."""
    now = time.time() if now is None else float(now)
    cfg = config() if cfg is None else cfg
    if cfg.get("off"):
        return {"action": "выключено", "why": "QSNAP_PC_OFF=1"}
    st = load_state()
    view, nxt = build_view(st, get if get is not None else bridge_getter(), now, cfg)

    if not view.get("ok"):
        last = as_float(st.get("at"))
        if last is not None and (now - last) < cfg["stale"]:
            # ОДИНОЧНЫЙ ПРОМАХ МОСТА СОСТОЯНИЕМ НЕ ЯВЛЯЕТСЯ: не пишем НИЧЕГО — ни свежего
            # штампа (это была бы ложь), ни «не сверено» (это была бы долбёжка на флапе моста).
            return {"action": "молчим", "why": "очередь не прочитана (%s), порог «не сверено» ещё "
                                               "не пройден" % view.get("err"),
                    "sig": st.get("sig"), "text": render(view, now)}
    text = render(view, now)
    sig = signature(view)
    if sig == st.get("sig"):
        return {"action": "без изменений", "why": "подпись состояния та же", "sig": sig}
    if dry:
        return {"action": "написал бы", "why": "сухой прогон", "sig": sig, "text": text}
    res = (writer if writer is not None else write_doc)(text)
    new_state = dict(st)
    new_state.update(nxt)
    new_state["sig"] = sig
    if view.get("ok"):
        new_state["at"] = now
        new_state["body"] = render_body(view["rows"], view["closed"], view["failed_at"], now)
    new_state["last_run"] = {"at": now, "action": "записал",
                             "status": res.get("status") if isinstance(res, dict) else str(res)}
    save_state(new_state)
    return {"action": "записал", "why": "смена состояния", "sig": sig,
            "res": res if isinstance(res, dict) else {"status": str(res)}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--create" in argv:
        print(json.dumps(create_doc(), ensure_ascii=False, indent=2))
        return 0
    dry = ("--dry" in argv) or ("--status" in argv)
    out = tick(dry=dry)
    if "--status" in argv or "--dry" in argv:
        print(out.get("text", ""))
        print("─" * 60)
    print("%s: %s" % (out.get("action"), out.get("why")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
