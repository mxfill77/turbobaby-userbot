# -*- coding: utf-8 -*-
"""series_pc.py — СЧЁТ СЕРИИ полосы ПК: сколько ЧИСТЫХ ЦЕПОЧЕК ПОДРЯД. Зеркало полосы VPS.

ПОВОД, измеренный здесь. Критерий фазы Штаба — «тридцать цепочек подряд, не оборванных шумом»
(`docs/artifacts/2026-08-09-shtab-frame-before-rewrite.txt:160`). На полосе VPS счёт серии живёт
файлом демона; НА ЭТОЙ ПОЛОСЕ ФАЙЛА СЧЁТА НЕ БЫЛО ВОВСЕ — замер захода тени 16.08.2026
(`docs/artifacts/2026-08-16-shadow-pc.md` §4.4, коммит da4c065): ни `pc_orchestrator`, ни любой
другой модуль такого счётчика не держал, а `pc_orchestrator.chain_cards.json` — дедуп карточек
(`{'sent': …, 'final': […]}`), не счёт. Понятие «цепочек подряд» жило РОВНО в одном месте, и это
был ТЕКСТ, а не машина. Пока счёта нет на одной из двух полос, критерий фазы мерит половину
системы. Этот модуль — вторая половина.

═══ ЧТО СЧИТАЕТСЯ ЧИСТОЙ ЦЕПОЧКОЙ (правило НЕ СВОЁ, а уже работающее) ══════════════════════

Правило взято у теневого цепочечного счёта этой полосы (`result_judge_pc.chain_shadow`) и НЕ
переписано: чистой признаётся ТОЛЬКО ДОКАЗАННАЯ цепочка, а `НЕ ДОКАЗАН` и `НЕИЗВЕСТНО` — ОБРЫВ.
Порядок силы (`НЕ ДОКАЗАН > НЕИЗВЕСТНО > ДОКАЗАН`) живёт ТАМ, в судье, и здесь не повторён ни
одной строкой: счётчику приходит УЖЕ СЛОЖЕННЫЙ вердикт цепочки, один из трёх слов.

Из трёх слов счётчик знает ровно одно — `ДОКАЗАН` (`PROVEN`). Дословность этого слова сторожит
тест сверкой с литералом судьи, а не дисциплина правки: два экземпляра одного слова расходятся
молча (класс полосы), поэтому равенство закреплено проверкой, которая упадёт при расхождении.
Импорта судьи здесь НЕТ намеренно — судья лежит НЕ ПОДКЛЮЧЁННЫМ под своим инвариантом, и
счётчик, потащив его в боевое дерево, снял бы этот замок боковой дверью.

«НЕИЗВЕСТНО — ОБРЫВ» звучит жёстко, и это осознанно: серия — обещание, а не надежда. Цепочка,
про которую нельзя доказать, что цель достигнута, серию не продолжает. Тот же закон держит слой
ожиданий этой полосы (`CLAUDE.md`, О1–О4, пункт 3).

═══ СЛУЖЕБНЫЕ КОРНИ В СЧЁТ НЕ ВХОДЯТ ═══════════════════════════════════════════════════════

ПРИЗНАК НАЗВАН ЯВНО И ОДИН: корень, чей текст начинается со СКОБКИ-ЯРЛЫКА, ПОСТАВЛЕННОЙ КОДОМ
(`SERVICE_RE` — дословный литерал переписи 467, `tmp_chain_refusals_20260811_c1/units2.py`).

Зачем вообще отсев. Контур сам себе ставит строки — сводные карточки ревизора, окна классов,
самопочинку. Если они входят в счёт, полоса УДЛИНЯЕТ СВОЮ СЕРИЮ СВОИМИ ЖЕ карточками, и число
«тридцать подряд» начинает мерить производительность ревизора, а не работу.

ЧИСЛОМ (корпус 148 цепочек, замер 17.08.2026, `_scratch_seriespc_0817/sign_report.txt`):
    ловит корней                     7 из 148 — ВСЕ семь `[ревизор…]`
        `[ревизор дата=… класс=…]`   3   (те самые цепочки со своими шагами: pid 4, 5, 44)
        `[ревизор-находки] …`        4   (сводная карточка находок; класс ЖИВОЙ — такая
                                         строка стои́т в очереди и сегодня, ряд #4)
    ложных из рабочих                0 — сверено ВТОРЫМ, независимым сигналом: колонка `from`
                                         (кто написал ряд). У пойманных `Filipp-revizor` 4 и
                                         `Filipp-pcloc-dec` 3; у рабочих `Filipp-328-dev` 139 и
                                         `Filipp` 2. Писателей в обеих половинах — 0, то есть
                                         признак не режет по живому
                                         (`_scratch_seriespc_0817/from_report.txt`).
    пропущено служебных              0 — корней, начинающихся со скобки, всего 7, и признак
                                         поймал все 7.

ЧЕСТНОЕ СЛЕДСТВИЕ, которое не спрятано: три ЕДИНСТВЕННЫЕ многошаговые цепочки полосы (pid 4, 5,
44) имеют служебный корень — значит из счёта уходят и они. Счёт остаётся на одношаговых, и это
названо, а не обойдено подгонкой признака.

═══ СЧЁТ НАЧИНАЕТСЯ С НУЛЯ ОТ МОМЕНТА ВКЛЮЧЕНИЯ ════════════════════════════════════════════

Прошлое НЕ ПЕРЕСЧИТЫВАЕТСЯ, и держится это ВРЕМЕНЕМ, а не рубежом по номеру. Причина —
измеренная мина полосы: `id` ряда очереди НЕ МОНОТОНЕН. В корпусе 11.08 номера доходят до 468,
а в живой очереди 17.08 они `2..46` — номер является строкой листа и ПЕРЕИСПОЛЬЗУЕТСЯ. Рубеж
«считаем всё, что больше N» на такой оси молчаливо проглотил бы либо всю новую работу (номера
меньше рубежа), либо всё прошлое (после переезда листа). Поэтому границей служит МЕТКА ВРЕМЕНИ
включения (`since`), а сравнение с ней идёт `julianday()`.

Два замка от двойного счёта, и ни один не смертелен в одиночку:
  • ВРЕМЯ: цепочка, закрывшаяся РАНЬШЕ включения, — прошлое, в счёт не входит никогда;
  • КЛЮЧ: уже учтённая цепочка второй раз не считается (`seen`, ключ `<id>@<created>` —
    номер СО меткой создания, потому что номер сам по себе переиспользуется).

Метка закрытия неизвестна или не разобрана → цепочка НЕ СЧИТАЕТСЯ И НЕ ПОМЕЧАЕТСЯ учтённой
(третий исход): «не смог проверить» не превращается ни в чистую, ни в обрыв. Открытая цепочка
приходит без метки закрытия и по этой же ветке ждёт своего исхода.

ПРЕЖНИЕ ТЕНЕВЫЕ ЧИСЛА НЕ ПОТЕРЯНЫ — они лежат тут же, в `PAST_SHADOW`, С ПОМЕТКОЙ, ПО КАКОМУ
ОПРЕДЕЛЕНИЮ считались (иное определение: служебные корни ВХОДИЛИ, реплей выведенных адресов), и
копируются в файл счёта при включении. Смешивать их с живым счётом нельзя: там 148 цепочек
истории по одному определению, здесь ноль по другому.

═══ ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ ═════════════════════════════════════════════════════════════

ХОДА ЦЕПИ ОН НЕ КАСАЕТСЯ НИ НА БАЙТ. Вердиктов он не выводит (приходят готовыми), очередь не
читает и не двигает, в мозг не пишет, в сеть не ходит, процессов не трогает. Кто подаёт ему
цепочки — решение владельца: на этой полосе судья адреса лежит НЕ ПОДКЛЮЧЁННЫМ, и счётчик
подключённым его не делает. Инвариант набора требует, чтобы и САМ СЧЁТЧИК не звался из боевого
хода: замок держится числом, а не этим абзацем.

ЗАМОК МЕТОДА: РАЗНОСТИ ВРЕМЕНИ — ТОЛЬКО ЧЕРЕЗ `julianday()`. Ни одного вычитания на Python
(ast-инвариант требует ноль узлов `ast.Sub`), ни `datetime`, ни `time`, ни `calendar`. Часы у
модуля одни — `SELECT datetime('now')` в `:memory:`-соединении; файл под это НЕ ОТКРЫВАЕТСЯ,
и весь SQL модуля — `SELECT` (проверено тестом). Живые метки полосы этот инструмент читает: у
очереди они вида `2026-08-14T15:30:22.664Z`, и `julianday` берёт их вместе с суффиксом `Z`.

ОТКАТ: `SERIES_PC_OFF=1` — ветка мертва целиком (ни чтения файла, ни записи). Полностью —
`git revert` коммита захода: боевой ход не изменится, потому что он этого модуля не касается.

Руки (счёт кормит вызывающий, файл ведёт модуль):
    venv\\Scripts\\python.exe series_pc.py --begin              # завести счёт с нуля
    venv\\Scripts\\python.exe series_pc.py --status             # что в файле, словами
    venv\\Scripts\\python.exe series_pc.py --feed <файл.json>   # сложить цепочки в счёт
    venv\\Scripts\\python.exe series_pc.py --feed <файл> --dry  # то же, файл не трогаем
"""
import collections
import json
import os
import re
import sqlite3
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = "series_pc.state.json"

# ═════════════════════════ ПРАВИЛО ЧИСТОТЫ ═══════════════════════════════════════════════════
# ОДНО слово из трёх, и оно ЗАИМСТВОВАНО, а не заведено: равенство с литералом судьи адреса
# сторожит тест. Остальные два слова счётчику не нужны — всё, что не `ДОКАЗАН`, есть обрыв, и
# перечислять обрывы по именам значило бы завести второй список, расходящийся с первым.
PROVEN = "ДОКАЗАН"

# ═════════════════════════ ПРИЗНАК СЛУЖЕБНОСТИ КОРНЯ ═════════════════════════════════════════
# Дословный литерал переписи 467 (`tmp_chain_refusals_20260811_c1/units2.py`): скобка-ярлык,
# поставленная КОДОМ. Числа замера — в шапке модуля.
SERVICE_RE = re.compile(r"^\[(шаг \d|куратор |конверт |ревизор|сводка родитель|карточка родитель|"
                        r"самопочинка задачи)")

# ═════════════════════════ ПОЛЯ ФАЙЛА СЧЁТА ══════════════════════════════════════════════════
SINCE = "since"          # метка ВКЛЮЧЕНИЯ — граница прошлого
UPDATED = "updated"      # время обновления
STREAK = "streak"        # ДЛИНА СЕРИИ (хвостовая: чистых подряд прямо сейчас)
RECORD = "record"        # РЕКОРД серии
CHAINS = "chains"        # ЧИСЛО ЦЕПОЧЕК, вошедших в счёт (служебные не в счёте)
BREAKS = "breaks"        # ОБРЫВОВ
SERVICE = "service"      # служебных корней ОТСЕЯНО — видно числом, а не молча
SEEN = "seen"            # ключи уже учтённых цепочек
LAST = "last"            # последняя учтённая: ключ, вердикт, чистая ли
WHY = "why"              # чем оборвалась серия — СЛОВАМИ
FROZEN = "frozen"        # ПРЕЖНИЕ числа с пометкой определения (замок «прежние числа целы»)

# Поля цепочки на входе.
KEY = "key"
VERDICT = "verdict"
ROOT = "root"
CLOSED_AT = "closed_at"

# Ключей учтённых цепочек храним ХВОСТ, и размер назван числом полосы: темп 4.3 закрытых в сутки
# (замер слепка очереди) — 500 ключей это ≈116 суток. Окно закрытых у живой очереди 24 часа,
# поэтому выпавший из хвоста ключ вернуться в поле зрения не может. Даже если вернётся, второй
# замок (время) прошлое всё равно не пустит.
SEEN_MAX = 500

# ПРЕЖНИЕ ТЕНЕВЫЕ ЧИСЛА — ПО ДРУГОМУ ОПРЕДЕЛЕНИЮ, и это написано внутри, а не рядом. Живут
# КОДОМ (а не только в файле состояния), чтобы потеря runtime-файла их не стёрла.
PAST_SHADOW = {
    "источник": "docs/artifacts/2026-08-16-shadow-pc.md §4.4 (коммит da4c065)",
    "корпус": "снимок очереди полосы ПК 11.08.2026, 158 рядов, 148 цепочек, охват 13.4 сут",
    "определение": "СЧИТАНО ИНАЧЕ, чем считает этот файл: служебные корни ВХОДИЛИ в счёт; "
                   "адрес не прочитан записанным, а ВЫВЕДЕН из текста итога (реплей — "
                   "канонический маркер в истории носили 0 строк из 158); цепочки по номеру",
    "теневой счёт": {"серия": 0, "рекорд": 22, "доказанных": 103, "из цепочек": 148},
    "настоящий счёт": {"серия": 0, "рекорд": 17, "зелёных": 93, "из цепочек": 148},
    "почему не перенесены": "определения разные — сложение дало бы число, которое не значит "
                            "ничего; счёт начинается с нуля от момента включения",
}


# ═════════════════════════ ВРЕМЯ: ТОЛЬКО julianday() ═════════════════════════════════════════
# Единственные часы модуля и единственная его разность. Файл под это не открывается: соединение
# `:memory:` нужно ради двух функций SQLite, а не ради базы.

def now_stamp():
    """ТЕКУЩЕЕ ВРЕМЯ (UTC) — часами SQLite. Единственное место модуля, знающее «сейчас»; всё
    остальное принимает время ПАРАМЕТРОМ, иначе счёт был бы непроверяем в наборе."""
    con = sqlite3.connect(":memory:")
    try:
        row = con.execute("SELECT datetime('now')").fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if row is None:
        return None
    return row[0]


def days_between(later, earlier):
    """Разность СУТКАМИ (later минус earlier) | None «одну из меток не прочитать».

    None здесь — полноценный ответ, а не авария: `julianday` от мусора возвращает NULL, и звать
    мусорную метку «раньше» или «позже» значило бы судить по неразобранному."""
    if later is None or earlier is None:
        return None
    con = sqlite3.connect(":memory:")
    try:
        row = con.execute("SELECT julianday(?) - julianday(?)", (str(later), str(earlier))).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    if row is None:
        return None
    return row[0]


def not_before(stamp, since):
    """Метка НЕ РАНЬШЕ границы? True / False / None «сравнить не удалось». Тристейт обязателен:
    иначе неразобранная метка тихо превратилась бы в «прошлое» или в «новое»."""
    days = days_between(stamp, since)
    if days is None:
        return None
    return bool(days >= 0)


# ═════════════════════════ ЧИСТЫЙ СЛОЙ: ФАКТЫ → СОСТОЯНИЕ ════════════════════════════════════
# Ни одна функция ниже не ходит в мир: ни файла, ни сети, ни часов сверх переданного `now`.
# Граница держится инвариантом SERIES_PC_PURE в наборе, а не этим абзацем.

def is_clean(verdict):
    """ЧИСТОЙ признаётся ТОЛЬКО ДОКАЗАННАЯ. Всё прочее — обрыв, включая `НЕИЗВЕСТНО`."""
    return verdict == PROVEN


def is_service(root_text):
    """Корень СЛУЖЕБНЫЙ? Признак — скобка-ярлык, поставленная кодом (см. шапку, числа замера)."""
    if root_text is None:
        return False
    return SERVICE_RE.match(str(root_text)) is not None


def make_entry(key, verdict, root, closed_at):
    """Цепочка на входе счётчика. `closed_at` = None означает «исход ещё не наступил либо метка
    закрытия неизвестна» — такая цепочка ждёт, а не судится."""
    return {KEY: str(key), VERDICT: verdict, ROOT: root, CLOSED_AT: closed_at}


def zero_state(since, frozen=None):
    """СОСТОЯНИЕ ВКЛЮЧЕНИЯ: все числа НОЛЬ, граница прошлого названа, прежние числа приложены."""
    past = PAST_SHADOW if frozen is None else frozen
    return {SINCE: since, UPDATED: since, STREAK: 0, RECORD: 0, CHAINS: 0, BREAKS: 0,
            SERVICE: 0, SEEN: [], LAST: None, WHY: "", FROZEN: past}


def _order(one):
    """Порядок сложения — по метке закрытия, при равенстве по ключу. Без метки идёт первым, но
    в счёт всё равно не входит: порядок обязан быть ОДНИМ И ТЕМ ЖЕ при любом порядке подачи."""
    stamp = one.get(CLOSED_AT)
    if stamp is None:
        return ("", str(one.get(KEY)))
    return (str(stamp), str(one.get(KEY)))


def _seen_tail(values):
    """Хвост ключей длиной не больше `SEEN_MAX`. Хранилище само себя ограничивает (`maxlen`) —
    арифметики границ здесь нет ни одной, и замок метода нечем обойти."""
    return collections.deque(values, maxlen=SEEN_MAX)


def fold(state, entries, now):
    """СОСТОЯНИЕ + ЦЕПОЧКИ → (новое состояние, отчёт оборота). Чистая функция: ни диска, ни часов.

    Пять исходов у одной цепочки, и каждый назван числом в отчёте оборота:
      учтена чистой · учтена обрывом · служебная (в счёт не входит) · прошлое (раньше включения)
      · ждёт (метки закрытия нет либо её не прочитать).
    """
    since = state.get(SINCE)
    seen = _seen_tail(state.get(SEEN, []))
    streak = state.get(STREAK, 0)
    record = state.get(RECORD, 0)
    chains = state.get(CHAINS, 0)
    breaks = state.get(BREAKS, 0)
    service = state.get(SERVICE, 0)
    last = state.get(LAST)
    why = state.get(WHY, "")

    turn = {"clean": 0, "broken": 0, "service": 0, "past": 0, "waiting": 0, "again": 0}
    for one in sorted(entries, key=_order):
        key = str(one.get(KEY))
        if key in seen:
            turn["again"] += 1
            continue
        fresh = not_before(one.get(CLOSED_AT), since)
        if fresh is None:
            # НЕ помечаем учтённой: «не смог проверить» не закрывает цепочку ни в какую сторону.
            turn["waiting"] += 1
            continue
        if fresh is False:
            turn["past"] += 1
            continue
        if is_service(one.get(ROOT)):
            # Служебный корень СЕРИЮ НЕ ПОДНИМАЕТ И НЕ ОБРЫВАЕТ: он просто не участвует.
            seen.append(key)
            service += 1
            turn["service"] += 1
            continue
        seen.append(key)
        chains += 1
        verdict = one.get(VERDICT)
        if is_clean(verdict):
            streak += 1
            if streak > record:
                record = streak
            last = {KEY: key, VERDICT: verdict, "clean": True}
            turn["clean"] += 1
            continue
        streak = 0
        breaks += 1
        last = {KEY: key, VERDICT: verdict, "clean": False}
        why = "серию оборвала цепочка %s: %s" % (key, verdict)
        turn["broken"] += 1

    out = {SINCE: since, UPDATED: now, STREAK: streak, RECORD: record, CHAINS: chains,
           BREAKS: breaks, SERVICE: service, SEEN: list(seen), LAST: last, WHY: why,
           FROZEN: state.get(FROZEN)}
    return out, turn


def render(state, now=None):
    """Файл счёта СЛОВАМИ. Возраст счёта — единственная разность, и она через `julianday()`."""
    if not isinstance(state, dict):
        return "файла счёта нет"
    lines = ["СЧЁТ СЕРИИ полосы ПК (чистая цепочка = ДОКАЗАННАЯ; служебные корни не в счёте)",
             "  серия сейчас   %d" % state.get(STREAK, 0),
             "  рекорд         %d" % state.get(RECORD, 0),
             "  цепочек в счёт %d" % state.get(CHAINS, 0),
             "  обрывов        %d" % state.get(BREAKS, 0),
             "  служебных мимо %d" % state.get(SERVICE, 0),
             "  включён        %s" % state.get(SINCE),
             "  обновлён       %s" % state.get(UPDATED)]
    age = days_between(state.get(UPDATED), state.get(SINCE))
    if age is None:
        lines.append("  возраст счёта  неизвестен (метки не прочитаны)")
    else:
        lines.append("  возраст счёта  %.2f сут" % age)
    if now is not None:
        stale = days_between(now, state.get(UPDATED))
        if stale is not None:
            lines.append("  молчит         %.2f сут" % stale)
    words = state.get(WHY)
    if words:
        lines.append("  %s" % words)
    frozen = state.get(FROZEN)
    if isinstance(frozen, dict):
        lines.append("  ПРЕЖНИЕ ЧИСЛА (иное определение, не складывать): %s"
                     % json.dumps(frozen.get("теневой счёт"), ensure_ascii=False))
    return "\n".join(lines)


# ═════════════════════════ РУКИ: ФАЙЛ СЧЁТА ══════════════════════════════════════════════════

def config(env=None):
    """Ручка отката. `env` — любое отображение: набор не трогает настоящее окружение."""
    e = os.environ if env is None else env
    off = e.get("SERIES_PC_OFF")
    return {"off": off is not None and str(off).strip() not in ("", "0")}


def state_file():
    """Путь файла счёта. Под тест-прогоном уводится в temp — боевой счёт гейт не переписывает."""
    named = os.environ.get("SERIES_PC_STATE")
    if named is not None and str(named).strip():
        return str(named).strip()
    import log_setup
    return log_setup.state_path(STATE_FILE)


def load_state():
    """Файл счёта → состояние | None «счёта нет». `None` и «счёт с нулями» — РАЗНЫЕ ответы:
    первое означает «не заведён», второе «заведён и пуст», и путать их нельзя."""
    path = state_file()
    try:
        with open(path, encoding="utf-8") as handle:
            got = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print("счёт не прочитан (%s) — считаю, что его нет" % exc, file=sys.stderr)
        return None
    if isinstance(got, dict):
        return got
    print("счёт не словарь (%s) — считаю, что его нет" % type(got).__name__, file=sys.stderr)
    return None


def save_state(state):
    """Запись через `.tmp` + `os.replace`: оборванная запись не оставляет полусчёта."""
    path = state_file()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)
        return True
    except OSError as exc:
        print("счёт не сохранён (%s)" % exc, file=sys.stderr)
        return False


def begin(now=None, env=None, force=False):
    """ЗАВЕСТИ СЧЁТ С НУЛЯ. Существующий файл НЕ ПЕРЕЗАПИСЫВАЕТСЯ без `force`: повторное
    включение обнулило бы рекорд, а рекорд — единственное, чего не восстановить."""
    if config(env)["off"]:
        return {"action": "выключено", "why": "SERIES_PC_OFF=1"}
    stamp = now_stamp() if now is None else now
    if stamp is None:
        return {"action": "часы не ответили", "why": "часы SQLite метки не дали"}
    was = load_state()
    if was is not None and not force:
        return {"action": "уже заведён", "why": "включён %s" % was.get(SINCE), "state": was}
    # Обнулять существующий счёт можно только по прямому `--again`: см. докстринг.
    state = zero_state(stamp)
    saved = save_state(state)
    return {"action": "заведён" if saved else "не сохранён", "state": state, "at": stamp}


def update(entries, now=None, env=None, dry=False):
    """СЛОЖИТЬ ЦЕПОЧКИ В СЧЁТ. Цепочки подаёт вызывающий — очередь этот модуль не читает."""
    if config(env)["off"]:
        return {"action": "выключено", "why": "SERIES_PC_OFF=1"}
    state = load_state()
    if state is None:
        return {"action": "счёт не заведён", "why": "нет файла %s" % state_file()}
    stamp = now_stamp() if now is None else now
    if stamp is None:
        return {"action": "часы не ответили", "why": "часы SQLite метки не дали"}
    out, turn = fold(state, entries, stamp)
    if dry:
        return {"action": "сухой прогон", "state": out, "turn": turn}
    saved = save_state(out)
    return {"action": "сложено" if saved else "не сохранён", "state": out, "turn": turn}


def read_feed(path):
    """Файл цепочек → список цепочек. Ждём список словарей с полями `key/verdict/root/closed_at`."""
    with open(path, encoding="utf-8") as handle:
        got = json.load(handle)
    if not isinstance(got, list):
        raise ValueError("корм не список, а %s" % type(got).__name__)
    out = []
    for one in got:
        if not isinstance(one, dict):
            raise ValueError("цепочка не словарь, а %s" % type(one).__name__)
        out.append(make_entry(one.get(KEY), one.get(VERDICT), one.get(ROOT), one.get(CLOSED_AT)))
    return out


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if "--begin" in args:
        got = begin(force="--again" in args)
        print(got["action"] + ((" · " + got["why"]) if got.get("why") else ""))
        if isinstance(got.get("state"), dict):
            print(render(got["state"]))
        return 0
    if "--feed" in args:
        where = args[args.index("--feed") + 1]
        got = update(read_feed(where), dry="--dry" in args)
        print(got["action"] + ((" · " + got["why"]) if got.get("why") else ""))
        if got.get("turn") is not None:
            print("оборот: " + json.dumps(got["turn"], ensure_ascii=False))
        if isinstance(got.get("state"), dict):
            print(render(got["state"]))
        return 0
    state = load_state()
    if state is None:
        print("счёт НЕ ЗАВЕДЁН (нет файла %s)" % state_file())
        return 0
    print(render(state, now_stamp()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
