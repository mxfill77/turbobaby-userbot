# -*- coding: utf-8 -*-
"""season_gate.py — ТРЕТИЙ ИСХОД НА ГРАНИЦЕ СЕЗОНОВ: «не считаю, зову человека».

ОСНОВАНИЕ — правило владельца 19.08.2026, три класса «не считаю»: период через границу
сезонов, срок от 30 суток, модель без цены. Здесь подключён РОВНО ОДИН из трёх — граница
сезонов. Не потому, что остальные не важны, а потому, что класс закрывается после того, как
доказан на одном месте, а три подключения разом дают владельцу поток карточек вместо одного
честного сигнала (это и есть предсмертный взгляд задания, исполненный буквально).

ЧТО ТАКОЕ ТРЕТИЙ ИСХОД, ДОСЛОВНО. Ответ клиенту УХОДИТ, но БЕЗ числа: ни точного, ни
ориентира, ни диапазона, ни СРЕДНЕГО по периоду. Клиенту сказано вслух, что даты попали на
стык сезонов и цену на такой срок считает человек. Владельцу поднимается карточка с датами,
моделью и причиной. Молчание, выдуманное число и среднее запрещены ОДИНАКОВО — поэтому
записка ниже требует ПРОИЗНЕСТИ ответ, а не просто гасит цену.

ПОЧЕМУ ГРАНИЦА СЕЗОНОВ ВООБЩЕ ЛОМАЕТ ЦЕНУ. Записанное правило (`price_source`) выбирает
период по дате НАЧАЛА аренды (`period_of(doc, ds)`), другой даты у него нет. Срок,
переходящий из P1 в P2, получил бы цену целиком по первому периоду — то есть число, которого
владелец не называл. Это не «неточность», а утверждение вне данных: считать такой срок
правилом нечем, и честный исход здесь один — человек.

ЧЕГО МОДУЛЬ НЕ ДЕЛАЕТ (границы, каждая — замок с тестом):

* НЕ правит ни одного числа: коэффициенты, границы периодов и корзины срока — решения
  владельца, читаются из `price_source.json` и здесь не трогаются;
* НЕ судит исход `unknown` (таблицы нет, дат нет, дата вне периодов) — на нём путь ответа
  идёт КАК ПРЕЖДЕ. «Не смог проверить» карточкой владельцу не является: мёртвый
  `price_source.json` поднял бы её на КАЖДЫЙ диалог, и один честный сигнал утонул бы;
* НЕ отправляет клиенту ничего и клиента не знает: отдаёт ТЕКСТ инструкции для промпта;
* НЕ рисует кнопок владельцу — карточка СООБЩАЕТ, а не спрашивает разрешения;
* НЕ является третьей реализацией гарда: вердикт живёт здесь ОДИН, `moderation_card.season_span`
  зовёт эту же функцию (раньше она жила там, и боевого вызывающего у неё не было ни одного).

ЗАВИСИМОСТЕЙ ОТ `suggest` ЗДЕСЬ НЕТ И БЫТЬ НЕ ДОЛЖНО: `moderation_card` импортирует `suggest`,
а `suggest` импортирует этот модуль — вердикт вынесен сюда именно затем, чтобы петли импорта
не возникло. Появится `import suggest` в этом файле — петля вернётся.

ОТКАТ, ОБЪЯВЛЕННЫЙ СЛОВОМ: `SEASON_GATE_OFF=1` — ветка МЕРТВА целиком, путь ответа идёт как
до неё (цена считается по дате начала, карточки нет). Ноль веток «включить обратно самой».
"""

import datetime
import logging
import os

import price_source

log = logging.getLogger("season_gate")

OFF_ENV = "SEASON_GATE_OFF"
_TRUE = ("1", "true", "yes", "on", "да")

SEASON_ONE = "one"           # старт и конец в одном периоде
SEASON_CROSSES = "crosses"   # период пересекает границу сезонов
SEASON_UNKNOWN = "unknown"   # проверить нечем — ТРЕТИЙ исход вердикта, а не «всё хорошо»

# Карточки, уже поднятые в этом процессе. ОДНА НА СЛУЧАЙ, а не на сообщение: клиент пишет по
# три реплики подряд, и без замка владелец получил бы три одинаковые карточки. Память
# процессная СОЗНАТЕЛЬНО — пережившая перезапуск отметка означала бы «владельцу уже сказали»
# в процессе, который этого не делал; лишняя карточка после перезапуска честнее пропавшей.
_seen = {}
_SEEN_CAP = 512


def off(env=None):
    """Ветка выключена объявленным откатом? Пусто/мусор → ВКЛЮЧЕНА (fail-safe в сторону человека)."""
    source = env if isinstance(env, dict) else os.environ
    return str(source.get(OFF_ENV) or "").strip().lower() in _TRUE


def reset():
    """Забыть поднятые карточки. Нужен тестам и ручной пробе; боевой путь не зовёт."""
    _seen.clear()


def _s(value):
    return "" if value is None else str(value).strip()


def _date(iso):
    """'ГГГГ-ММ-ДД' → date | None. Не дата — это «не знаю», а НЕ сегодняшний день."""
    s = _s(iso)
    if len(s) < 10:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _period_name(period):
    """Период файла цен → человеческое имя («P5 ПИК»)."""
    key, name = _s(period.get("key")), _s(period.get("name"))
    if len(key) > 0 and len(name) > 0:
        return key + " " + name
    return name if len(name) > 0 else key


def span(iso_start, iso_end, doc=None):
    """Сезонные периоды на КРАЯХ срока аренды. → (вердикт, (имя старта, имя конца), пояснение).

    Вердикт SEASON_UNKNOWN — полноправный третий исход: таблицы периодов нет, дат нет, дата вне
    периодов файла. «Не смог проверить» НЕ превращается в «проверено и хорошо» ни одной веткой.

    Конец срока берём КАК НАЗВАН клиентом: в «Календаре» обе даты входят в срок. На самой
    границе это назовёт пересечением срок, задевающий её последним днём, — перекос СОЗНАТЕЛЬНО
    в сторону «спроси человека»: лишний вопрос дешевле неверной цены."""
    d = price_source.load() if doc is None else doc
    if d is None:
        return SEASON_UNKNOWN, None, "таблица периодов не прочиталась (price_source.json)"
    ds, de = _date(iso_start), _date(iso_end)
    if ds is None or de is None:
        return SEASON_UNKNOWN, None, "дат аренды нет — сезон не определяю"
    try:
        p1, p2 = price_source.period_of(d, ds), price_source.period_of(d, de)
    except Exception as exc:                      # noqa: BLE001 — отказ файла ≠ «границы нет»
        return SEASON_UNKNOWN, None, "периоды не разобрались (%s)" % type(exc).__name__
    if p1 is None or p2 is None:
        return SEASON_UNKNOWN, None, "дата вне периодов файла цен"
    n1, n2 = _period_name(p1), _period_name(p2)
    if p1.get("key") == p2.get("key"):
        return SEASON_ONE, (n1, n2), n1
    return SEASON_CROSSES, (n1, n2), n1 + " → " + n2


# ------------------------------- текст клиенту --------------------------------
# НИ ОДНОЙ ЦИФРЫ в этих двух строках, и это не стиль. Белый список пост-чека черновика
# строится из ЛЮБЫХ чисел ценовой записки (`suggest._pc_wl_price_numbers`): попади сюда имя
# периода «P5» или сама дата — цифра стала бы РАЗРЕШЁННОЙ к произнесению ценой, и третий исход
# принёс бы клиенту ровно то число, ради запрета которого заведён. Даты и периоды живут в
# карточке ВЛАДЕЛЬЦУ — она в промпт не попадает.
_NOTE_RU = (
    "ЦЕНА: срок аренды пересекает границу сезонов, а записанное правило цены считает период по "
    "дате НАЧАЛА — значит любое посчитанное число было бы неверным. НЕ называй НИКАКОЙ цены: "
    "ни точной, ни за сутки, ни за весь срок, ни «от … ฿», ни диапазоном, ни СРЕДНИМ по периоду, "
    "ни ориентиром, ни из FAQ. МОЛЧАТЬ ТОЖЕ НЕЛЬЗЯ, ответ обязан прозвучать: скажи прямо, что "
    "даты попадают на стык сезонов, поэтому цену на такой срок считает человек, и коллега "
    "вернётся с точной суммой в ближайшее время. Модель, даты и пожелания уточнить можно — цену "
    "нет. Отдельной строкой в конце поставь пометку менеджеру: «[уточнить: период через границу "
    "сезонов — цену считает человек]».")
_NOTE_EN = (
    "PRICE: the rental term crosses a season boundary, while the written price rule prices the "
    "period by its START date — so any number computed here would be wrong. Do NOT quote ANY "
    "price: no exact figure, no daily rate, no total, no «from … ฿», no range, no AVERAGE over "
    "the period, no ballpark, nothing from the FAQ. STAYING SILENT IS ALSO FORBIDDEN, you must "
    "answer: say plainly that the dates fall on the seam between seasons, so a human prices such "
    "a term, and a colleague will come back with the exact amount shortly. You may clarify the "
    "model, the dates and the client's wishes — but not the price. On a separate final line add "
    "the note for the manager: «[to check: period crosses a season boundary — priced by a human]».")


def client_note(lang="ru"):
    """Ценовая записка третьего исхода: НИ ОДНОГО числа + прямое требование позвать человека."""
    return _NOTE_EN if lang == "en" else _NOTE_RU


# ------------------------------- карточка владельцу ---------------------------

CARD_HEAD = "⛔ НЕ СЧИТАЮ: период через границу сезонов"
CARD_FOOT = "Кнопок нет: карточка сообщает, а не спрашивает разрешения."


def _model_of(hints):
    """Модель для карточки: список моделей → через запятую; пусто → честное «не разобрана»."""
    models = hints.get("models") or ([hints["model"]] if hints.get("model") else [])
    names = [_s(m) for m in models if len(_s(m)) > 0]
    return ", ".join(names) if names else "не разобрана"


def owner_card(hints, detail):
    """Текст карточки владельцу: даты, модель, причина. Чистая строка — ни сети, ни Telegram."""
    ds, de = _s(hints.get("iso_start")), _s(hints.get("iso_end"))
    days = hints.get("hint_days")
    term = ds + " → " + de
    if isinstance(days, int):
        term += " (" + str(days) + " сут)"
    return "\n".join([
        CARD_HEAD,
        "• модель: " + _model_of(hints),
        "• даты: " + term,
        "• причина: " + _s(detail) + " — цена в файле берётся по дате НАЧАЛА, на такой срок её "
        "считает человек",
        "• что сделал бот: цену клиенту НЕ назвал и не усреднил, ответил, что посчитает человек",
        CARD_FOOT,
    ])


def _case_key(hints):
    """Ключ случая: модель + обе даты. Идентификатора клиента в hints НЕТ (их собирает
    `extract_booking_hints` из транскрипта), поэтому случаем считается сама бронь — то есть
    два клиента с одной моделью и одними датами получат ОДНУ карточку на двоих. Это осознанный
    перекос в тишину: задание требует один честный сигнал, а не полный журнал обращений."""
    return (_model_of(hints), _s(hints.get("iso_start")), _s(hints.get("iso_end")))


def _default_sender():
    """Боевой отправитель — доставка Штаба. Импорт ЛЕНИВЫЙ: тесты его не поднимают вовсе, и
    модуль остаётся чистым от конфигурации Telegram при обычном импорте."""
    import dispatch_notify
    return dispatch_notify.deliver


def raise_card(hints, detail, sender=None):
    """Поднять карточку владельцу ОДИН раз на случай. → True, если карточка ушла.

    ОТКАЗ ОТПРАВИТЕЛЯ НЕ МЕНЯЕТ ИСХОДА. Ни одной веткой: сюда приходят уже решившись не
    называть цену, и «карточку не доставили» обязано означать «сигнал потерян», а не «раз
    человека не позвали — назовём число сами». Поэтому функция НИКОГДА не бросает, а её
    False никем не читается как разрешение считать."""
    key = _case_key(hints)
    if key in _seen:
        return False
    _seen[key] = True
    if len(_seen) > _SEEN_CAP:                    # память процесса не растёт без предела
        for old in list(_seen)[:len(_seen) - _SEEN_CAP]:
            _seen.pop(old, None)
    text = owner_card(hints, detail)
    log.warning("season_gate: %s", text.replace("\n", " | "))
    try:
        send = sender if sender is not None else _default_sender()
        send(text)
        return True
    except Exception as exc:                      # noqa: BLE001 — нужен сам факт отказа
        log.warning("season_gate: карточка владельцу НЕ ушла (%s) — исход не меняется",
                    type(exc).__name__)
        return False


# ------------------------------- единственная точка подключения ---------------

def note_for(hints, lang="ru", doc=None, sender=None, env=None):
    """ЕДИНСТВЕННОЕ, что зовёт путь ответа. → текст записки ИЛИ None (граница не пересечена).

    None означает «иди дальше прежней дорогой» и возвращается на ВСЕХ исходах, кроме
    `crosses`: на `one` считать можно как раньше, на `unknown` — тоже (см. границы в шапке).
    """
    if off(env):
        return None
    hints = hints or {}
    verdict, _names, detail = span(hints.get("iso_start"), hints.get("iso_end"), doc=doc)
    if verdict != SEASON_CROSSES:
        return None
    raise_card(hints, detail, sender=sender)
    return client_note(lang)
