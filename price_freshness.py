# -*- coding: utf-8 -*-
"""СТОРОЖ СВЕЖЕСТИ ЗАПИСАННОГО ПРАВИЛА ЦЕНЫ. Чистая функция «факты → вердикт».

ОСНОВАНИЕ — записанное решение владельца 17.08.2026, узел `KB_business_rules`, раздел
«ИСТОЧНИК ЦЕНЫ: ЦЕЛЕВАЯ КОНСТРУКЦИЯ», пункт «ОБЯЗАТЕЛЬНОЕ УСЛОВИЕ ПЕРЕХОДА — СТОРОЖ
СВЕЖЕСТИ», дословно:

    Записанное правило есть снимок и устаревает молча. Пока цену считает человек,
    устаревание видит человек; когда считает бот и людей рядом нет, устаревший файл будет
    уверенно называть вчерашние числа месяцами. Поэтому файл обязан нести дату сборки
    и слепок положения ручек, а бот при расхождении обязан НЕ НАЗЫВАТЬ цену и позвать
    владельца.

ЧТО СУДИМ. Записанное правило — `price_source.json`. Живой лист виден через дверь моста
ровно одним положением: ТРИ РУЧКИ скидки категорий (`Календарь бронирования`!H3 «мото-1»,
I3 «мото-2», J3 «скутеры»; зеркала `цены альт`!B2/B9/B19). Их крутит РУКАМИ владелец при
смене сезона — это и есть та часть листа, чьё движение делает записанное правило вчерашним.
Прямой двери к ячейке у прода нет (разведка 15.08: 27 кандидатов, все `unknown_action`),
поэтому положение ручки снимается полем `season.global_discount` живой котировки.

ТРИ ИСХОДА, А НЕ ДВА (`СВЕЖЕЕ` / `УСТАРЕЛО` / `НЕИЗВЕСТНО`). Третий обязателен: «проверить
не удалось» — это НЕ «проверено и хорошо». Ни одна ветка здесь не превращает молчание
источника в подтверждение свежести. Второй и третий исходы ведут себя ОДИНАКОВО в сторону
осторожности: `may_quote=False`, `call_owner=True`. Разными их имена держатся не ради
красоты, а ради карточки владельцу: «ручку повернули» и «лист не ответил» чинятся по-разному.

ДОКАЗАННОЕ БЬЁТ НЕИЗВЕСТНОЕ. Улики копятся в два списка, и `УСТАРЕЛО` выигрывает у
`НЕИЗВЕСТНО`: если одна ручка разошлась ТОЧНО, а другая не снялась, вердикт — `УСТАРЕЛО`
(факт назван), а не `НЕИЗВЕСТНО` (факт спрятан). Осторожность от этого не страдает — цену
не называют оба, — а владелец получает верную причину.

ВОЗРАСТ СУДИТСЯ ОТДЕЛЬНО ОТ РУЧЕК, и это не перестраховка. Ручки — единственное, что видно
через дверь; всё остальное записанное правило (база по моделям, парк, кепки, колонка B листа)
через неё не видно ВОВСЕ. Значит «ручки сошлись» не означает «мир не поменялся»: сошедшиеся
ручки при мёртвом слепке — ровно тот случай, ради которого владелец и назвал ДАТУ СБОРКИ
отдельным требованием. Порог — одна ручка `PRICE_FRESH_MAX_AGE_DAYS`, дефолт 14 суток;
обоснование дефолта в `MAX_AGE_WHY` ниже, оно снято со слов самого владельца, а не назначено.

ОТКАТ, ОБЪЯВЛЕННЫЙ ЧИСЛОМ: `PRICE_FRESH_MAX_AGE_DAYS=0` — возрастная ветка МЕРТВА целиком
(судим только ручки). Пустое/мусор/отрицательное → дефолт. Ноль здесь значим, поэтому парсер
свой, а не общий `_env_int` демона: тот на «0» отдаёт дефолт, то есть выключить им ветку
нельзя.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ. Не считает цену ни одной строкой, не ходит в сеть, не читает
диск, не импортирует `price_source`, `suggest` и `pricing`. Факты приносят РУКИ
(`price_freshness_run.py`), решение принимает эта чистая функция — та же развязка, что
у `expectations_pc`. Наблюдатель не живёт на том, за чем следит.

НЕ ПОДКЛЮЧЁН. Врезки в путь ответа бота здесь нет и заводить её этот заход не вправе:
правка кода, который считает саму цену, запрещена заданием. Подключение — одна строка в
`price_source.reprice` перед счётом (`return QUENCH`, если `not verdict["may_quote"]`), и это
отдельное решение владельца. См. `docs/artifacts/2026-08-17-price-freshness-guard.md`.
"""

import datetime

# ── ИМЕНА ИСХОДОВ — часть договора: по ним пишут условия потребители, менять нельзя молча. ──
FRESH = "СВЕЖЕЕ"
STALE = "УСТАРЕЛО"
UNKNOWN = "НЕИЗВЕСТНО"
STATES = (FRESH, STALE, UNKNOWN)

# ── ИМЕНА ПРИЧИН. Трёх исходов мало владельцу: под одним `НЕИЗВЕСТНО` живут ДВА разных
# события с ПРОТИВОПОЛОЖНЫМ действием — «ручку повернули» (чинится пересборкой слепка) и
# «ручку не удалось снять» (пересборка ЗАПРЕЩЕНА: слепок собрался бы с двери, которую как раз
# и не прочитали). До 06.09.2026 обе причины несли ОДИН хвост карточки «нужно пересобрать
# price_source.json» — то есть непрочитанная дверь советовала владельцу ровно то, чего делать
# нельзя. Имя причины идёт ПЕРВЫМ в карточке и потому попадает в журнал продукта дословно
# (`suggest.py` пишет карточку как есть), а значит греп по журналу различает их СЧЁТОМ, а не
# на глаз. Политику имена НЕ меняют: цену по-прежнему не называет ни одна причина, кроме
# `СВЕЖЕЕ`, и `may_quote` считается ровно по `state`, а не по имени причины.
KIND_FRESH = "СВЕЖЕЕ"
KIND_DIVERGED = "РУЧКА РАЗОШЛАСЬ"
KIND_OLD = "СЛЕПОК СТАР"
KIND_NOT_READ = "РУЧКА НЕ СНЯЛАСЬ"
KIND_NO_SNAPSHOT = "СЛЕПКА НЕТ"
KINDS = (KIND_FRESH, KIND_DIVERGED, KIND_OLD, KIND_NOT_READ, KIND_NO_SNAPSHOT)

# ЧТО ВЛАДЕЛЬЦУ ДЕЛАТЬ — по причине, а не по исходу. Разное действие и есть весь смысл разделения.
KIND_ACTION = {
    KIND_DIVERGED: "Нужно пересобрать price_source.json и снять слепок ручек заново.",
    KIND_OLD: "Нужно пересобрать price_source.json и снять слепок ручек заново.",
    KIND_NO_SNAPSHOT: "Нужно СОБРАТЬ price_source.json заново: слепка ручек в нём нет вовсе.",
    KIND_NOT_READ: ("Пересобирать price_source.json НЕ НАДО: слепок мог и не устареть — не "
                    "прочитан ЖИВОЙ лист. Смотреть надо дверь моста (quote_price), а не файл."),
}

# ── ПОРОГ. Имя — ключ окружения; 0 → возрастная ветка мертва (объявленный откат). ──────────
MAX_AGE_ENV = "PRICE_FRESH_MAX_AGE_DAYS"
# ПАСПОРТ ПОРОГА: терпит=возраст ОДНОГО слепка цен; исчерпать его многим операциям нечем
#   | замер=слово самого владельца, узел KB_business_rules → «ПИК: ДАТЫ, ВЕЛИЧИНА, ЧАСТОТА
#   ПЕРЕСМОТРА»: 14 суток — самая ДЛИННАЯ названная владельцем частота пересмотра цен.
#   Текст обоснования дословно — в MAX_AGE_WHY ниже
#   | снят=2026-08-17 | делится=НЕ ДЕЛИТСЯ
#   | предел-единицы=НЕ НУЖЕН: единица одна — слепок
#   | род=решение | артефакт=docs/artifacts/2026-08-17-price-freshness-guard.md
# (род «решение»: по календарю не протухает — протухнет, когда владелец назовёт другую частоту.
#  Горизонта проверяющий к нему не применяет и честно об этом говорит, а не молчит.)
MAX_AGE_DEFAULT = 14.0
MAX_AGE_WHY = (
    "14 суток — самая ДЛИННАЯ частота пересмотра цен, названная владельцем самим "
    "(KB_business_rules → «ПИК: ДАТЫ, ВЕЛИЧИНА, ЧАСТОТА ПЕРЕСМОТРА»: октябрь раз в 1-2 недели, "
    "ноябрь раз в неделю, до 20 декабря раз в неделю, с середины февраля раз в 2 недели, апрель "
    "раз в 2 недели). Слепок старше 14 суток пережил хотя бы один объявленный круг пересмотра "
    "в любом месяце, где круг назван. Число НЕ круглое и НЕ назначено нами."
)

# Ручки — числа с двух знаков после запятой; сравниваем с допуском, а не побитно.
EPS = 1e-9

# Форма гашения цены. КОПИЯ литерала `price_source.reprice` намеренно: сторож не импортирует
# того, за чем следит. Совпадение формы держится тестом, а не обещанием.
QUENCH = {"status": "error", "quote": None}

_BLOCK = "freshness"


def max_age_days(env=None):
    """Порог возраста в СУТКАХ. Пусто/мусор/отрицательное → дефолт; 0 → ветка мертва.

    Окружение передаётся словарём, а не читается отсюда: вердикт обязан быть чистой функцией,
    и тест задаёт порог, не трогая процесс."""
    source = env if isinstance(env, dict) else {}
    raw = source.get(MAX_AGE_ENV)
    if raw is None:
        return MAX_AGE_DEFAULT
    text = str(raw).strip().replace(",", ".")
    if not text:
        return MAX_AGE_DEFAULT
    try:
        value = float(text)
    except (TypeError, ValueError):
        return MAX_AGE_DEFAULT
    if value < 0:
        return MAX_AGE_DEFAULT
    return value


def _date(value):
    """ISO-строка или `datetime.date` → дата, иначе None. None здесь — честное «не знаю»:
    пустотой оно не подменяется, иначе «дата не разобрана» слилось бы с «даты нет»."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        return None


def snapshot_of(doc):
    """Записанное правило (dict `price_source.json`) → слепок или None.

    Слепок = дата + положение ручек. Нет блока, нет даты, нет ни одной разобранной ручки →
    None, и это «сверять не с чем» (исход НЕИЗВЕСТНО), а не «ручек ноль, значит все сошлись».
    """
    if not isinstance(doc, dict):
        return None
    block = doc.get(_BLOCK)
    if not isinstance(block, dict):
        return None
    rows = block.get("handles")
    if not isinstance(rows, list):
        return None
    handles, seen = {}, 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        seen += 1
        cell, value = row.get("cell"), row.get("global_discount")
        if not isinstance(cell, str) or not cell.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        handles[cell.strip()] = float(value)
    # ДАТА СЛЕПКА ОТСУТСТВУЕТ → падаем на дату сборки: правило собрано и снято одним заходом,
    # и другой даты у него нет. ДАТА ЕСТЬ, НО НЕ РАЗОБРАНА → не падаем НИКУДА: это отказ
    # читателя, а подменять его соседним числом значит выдавать неразбор за факт. Разница
    # поймана собственным тестом: с падением сюда правило с датой-мусором объявлялось СВЕЖИМ.
    raw_taken = block.get("snapshot_on")
    missing = raw_taken is None or (isinstance(raw_taken, str) and not raw_taken.strip())
    taken = _date(block.get("built_on")) if missing else _date(raw_taken)
    if taken is None and not handles:
        return None
    return {"snapshot_on": block.get("snapshot_on"),
            "built_on": block.get("built_on"),
            "taken": taken,
            "handles": handles,
            "seen": seen,
            "sheet": block.get("sheet")}


def judge(snapshot, live, now=None, max_age=None):
    """СЛЕПОК + ЖИВОЙ ЛИСТ → вердикт. Чистая функция: ни диска, ни сети, ни окружения.

    `live` — факты рук: {"ok": bool, "handles": {"H3": 0.15, …}, "error": "…"}. Значение
    ручки None означает «эта ручка НЕ снята» и идёт в непроверенные, а не в сошедшиеся.
    """
    limit = MAX_AGE_DEFAULT if max_age is None else float(max_age)
    today = _date(now)
    if today is None and now is None:
        today = datetime.date.today()

    proven, unproven = [], []          # доказанное устаревание / не удалось проверить
    diverged, unverified, agreed = [], [], []
    age = None

    if not isinstance(snapshot, dict) or not snapshot.get("handles"):
        unproven.append("записанное правило не несёт слепка ручек — сверять не с чем")
        return _verdict(UNKNOWN, unproven[0], age, limit, diverged, unverified, agreed,
                        proven, unproven, kind=KIND_NO_SNAPSHOT)

    # Порог 0 — ОБЪЯВЛЕННЫЙ ОТКАТ: возрастная ветка мертва целиком, улик не даёт ни в одну
    # сторону, судим только ручки. Поэтому её не «пропускают молча», а не входят в неё вовсе.
    taken = snapshot.get("taken")
    if limit > 0:
        if taken is None:
            unproven.append("дата слепка не разобрана (%r) — возраст правила неизвестен"
                            % (snapshot.get("snapshot_on"),))
        elif today is None:
            unproven.append("сегодняшняя дата не разобрана (%r) — возраст правила неизвестен"
                            % (now,))
        else:
            age = float((today - taken).days)
            if age > limit:
                proven.append("слепок старше порога: %.0f сут при пороге %.0f (ручки при этом "
                              "могли и не двигаться — через дверь виден только их угол, "
                              "а не весь лист)" % (age, limit))

    want = snapshot["handles"]
    facts = live if isinstance(live, dict) else {}
    got = facts.get("handles")
    if facts.get("ok") is not True or not isinstance(got, dict):
        reason = facts.get("error")
        unproven.append("живой лист недоступен: %s"
                        % (reason if isinstance(reason, str) and reason.strip()
                           else "руки не принесли положения ручек"))
    else:
        for cell in sorted(want):
            value = got.get(cell)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                unverified.append(cell)
                continue
            if abs(float(value) - want[cell]) > EPS:
                diverged.append({"cell": cell, "was": want[cell], "now": float(value)})
            else:
                agreed.append(cell)
        extra = [c for c in sorted(got) if c not in want]
        if diverged:
            proven.append("ручки разошлись со слепком: "
                          + ", ".join("%s %s→%s" % (d["cell"], d["was"], d["now"])
                                      for d in diverged))
        if extra:
            proven.append("живой лист несёт ручки, которых в слепке нет: %s" % ", ".join(extra))
        if unverified:
            unproven.append("не снялись ручки: %s" % ", ".join(unverified))

    if proven:
        return _verdict(STALE, "; ".join(proven), age, limit, diverged, unverified, agreed,
                        proven, unproven)
    if unproven:
        return _verdict(UNKNOWN, "; ".join(unproven), age, limit, diverged, unverified, agreed,
                        proven, unproven)
    return _verdict(FRESH, "слепок сошёлся с живым листом (%s) и не старше порога"
                    % ", ".join(agreed), age, limit, diverged, unverified, agreed,
                    proven, unproven)


def _verdict(state, why, age, limit, diverged, unverified, agreed, proven, unproven, kind=None):
    """Сборка вердикта одним местом: `may_quote` истинен РОВНО у одного исхода из трёх.

    `kind` — ИМЯ ПРИЧИНЫ, а не второй исход: считается из уже собранных улик и на `may_quote`
    не влияет ни одной веткой. Доказанное устаревание при этом различается по УЛИКЕ, а не по
    порядку: разошедшаяся ручка сильнее возраста, потому что она названа числом («было→стало»),
    а возраст говорит лишь «мог устареть»."""
    if kind is None:
        if state == FRESH:
            kind = KIND_FRESH
        elif state == STALE:
            kind = KIND_DIVERGED if diverged else KIND_OLD
        else:
            kind = KIND_NOT_READ
    return {"state": state,
            "kind": kind,
            "may_quote": (state == FRESH),
            "call_owner": (state != FRESH),
            "why": why,
            "age_days": age,
            "max_age_days": limit,
            "diverged": diverged,
            "unverified": unverified,
            "agreed": agreed,
            "proven": proven,
            "unproven": unproven}


def bot_action(verdict):
    """Вердикт → ЧТО ДЕЛАЕТ БОТ. Одно место, где записано поведение при несвежем правиле.

    Несвежее (и «не смог проверить» — тоже) значит: цена клиенту НЕ называется, вместо неё
    молчание, и владелец позван. Вчерашнее число клиенту не уходит ни на одной дороге —
    другого числа у ветки нет по построению.
    """
    state = verdict.get("state") if isinstance(verdict, dict) else None
    if state not in STATES:
        state = UNKNOWN                    # чужой/битый вердикт — это НЕ разрешение назвать цену
        verdict = {"state": state, "why": "вердикт не разобран"}
    ok = (state == FRESH)
    if ok:
        return {"name_price_to_client": True, "client_price": "из записанного правила",
                "call_owner": False, "owner_card": None, "quote": None}
    return {"name_price_to_client": False,
            "client_price": None,
            "call_owner": True,
            "owner_card": owner_card(verdict),
            "quote": dict(QUENCH)}


def reason_kind(verdict):
    """Вердикт → ОДНО имя причины из `KINDS`. Чужой/битый вердикт — «РУЧКА НЕ СНЯЛАСЬ»:
    неразобранное не смеет притвориться ни свежестью, ни доказанным устареванием."""
    kind = verdict.get("kind") if isinstance(verdict, dict) else None
    if kind in KINDS:
        return kind
    state = verdict.get("state") if isinstance(verdict, dict) else None
    if state == FRESH:
        return KIND_FRESH
    if state == STALE:
        return KIND_DIVERGED if (isinstance(verdict, dict) and verdict.get("diverged")) else KIND_OLD
    return KIND_NOT_READ


def owner_card(verdict):
    """Текст владельцу. Называет ИСХОД, ПРИЧИНУ и ДЕЙСТВИЕ: «ручку повернули» и «лист не
    ответил» чинятся по-разному, и слить их в одно «что-то не так» значит отнять у него действие.

    ИМЯ ПРИЧИНЫ СТОИТ ПЕРВЫМ и в квадратных скобках — не ради вида: карточку `suggest` кладёт
    в журнал продукта ДОСЛОВНО, поэтому имя оказывается в начале строки `price_gate: …` и
    греп различает причины СЧЁТОМ. Хвост-действие тоже разный: до 06.09.2026 непрочитанная
    дверь советовала «пересобрать price_source.json», то есть собрать слепок с той самой двери,
    которую прочитать не удалось."""
    state = verdict.get("state") if isinstance(verdict, dict) else UNKNOWN
    why = verdict.get("why") if isinstance(verdict, dict) else None
    kind = reason_kind(verdict)
    head = ("ЦЕНА НЕ НАЗВАНА: записанное правило цены УСТАРЕЛО." if state == STALE
            else "ЦЕНА НЕ НАЗВАНА: свежесть записанного правила ПРОВЕРИТЬ НЕ УДАЛОСЬ.")
    tail = "Клиенту цена не ушла. %s" % KIND_ACTION.get(kind, KIND_ACTION[KIND_NOT_READ])
    return "[%s] %s %s %s" % (kind, head,
                              why if isinstance(why, str) and why.strip() else "причина не названа",
                              tail)
