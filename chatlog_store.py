# -*- coding: utf-8 -*-
"""
chatlog_store.py — КОРЕНЬ ХРАНИЛИЩА РАБОЧЕЙ ПЕРЕПИСКИ полосы ПК (первая очередь, 05.09.2026).

ЗАЧЕМ ОН ЕСТЬ. Перепись 05.09 (`docs/artifacts/2026-09-05-перепись-рабочих-тем-и-цена-архива.md`,
§1 п.3 и §8 п.4) замерила класс, который уже действует: переписка ложится на диск каждый день —
и каждый день молча выбрасывается ротацией. `log_setup.py` держит потолок 5 МБ × 4 бэкапа, и
`userbot.log.1` в день переписи стоял РОВНО НА ПОТОЛКЕ (5 242 827 Б). Внутри этих пяти мегабайт
переписки — 2.2 %: сто килобайт живой истории умирают от объёма чужого процессного шума.
Владелец потребовал прямо: «архивировать, а НЕ удалять».

ЧЕМ ЭТО ХРАНИЛИЩЕ ОТЛИЧАЕТСЯ ОТ ЛОГА — ОДНОЙ СТРОКОЙ: **ротации нет ни в одной ветке.**
Отсюда всё устройство ниже, и отсюда же то, чего в модуле НЕТ и не должно появиться:

  * `log_setup` здесь НЕ импортируется. Он — общая ротация ПК-контура, и один его вызов вернул
    бы сюда ровно тот потолок, ради ухода от которого хранилище и заводится. Инвариант держится
    тестом (`test_chatlog.TestNoRotation`), а не обещанием в комментарии;
  * ни один файл дня, ни один индекс НЕ открывается на `"w"` — только `"a"`. Затирание — это та
    же потеря, что и ротация, просто под другим именем;
  * потолков длины строки, числа строк и размера каталога нет ни одного.

ГДЕ ЛЕЖИТ (проект переписи §7.1, реализован буквально):

    chatlog/                                 ← корень; ВНЕ git (первой строкой в .gitignore)
      <slug-группы>/<ГГГГ>/<ММ>/<ГГГГ-ММ-ДД>.jsonl   ← одна строка = одно сообщение
      _index/<ГГГГ-ММ>.tsv                   ← дата · группа · тема · id · псевдоним · 200 симв.
      _manifest.json                         ← какие группы захвачены, чем и с какого дня
      _salt                                  ← соль псевдонимов (см. ниже)

Разбивка по ДНЮ и по ГРУППЕ выбрана не для красоты: Штаб ищет «что писали про такую-то
проблему», и дата у него всегда на руках (падение, отказ, инцидент). Тема лежит ПОЛЕМ внутри
строки, а не папкой: темы форума Обслуживания заводятся под каждый новый байк, и папка на тему
дала бы сорок с лишним каталогов, растущих без потолка.

ЛЮДЕЙ В АРХИВЕ НЕТ (перепись §8 п.1 — замок, без которого архив заводить нельзя):

  * автор — ТОЛЬКО стабильный псевдоним `p<12 hex>` = sha256(соль + сырое имя). Полей `name`,
    `username`, `phone` в строке нет ни одного, и завести их нечем: `line()` их не принимает;
  * соль лежит в `chatlog/_salt`, то есть ВНЕ git вместе со всем корнем. Без неё псевдоним не
    разворачивается обратно даже нами; с ней он стабилен между запусками, а значит по нему
    можно проследить одного человека через год переписки, ни разу его не назвав;
  * ТЕКСТ тоже чистится: `@ник` → `@p<8 hex>`, телефон (10–15 цифр) → `[tel:p<8 hex>]`. Это не
    перестраховка, а буквальное чтение требования «ника и телефона нет ни в одном поле»: поле
    `text` — тоже поле. Поиск от этого не страдает: `chatlog_find` чистит СВОЙ запрос той же
    функцией, поэтому искать по нику и по телефону по-прежнему можно — совпадёт псевдоним.
    Имена людей внутри текста машиной не опознаются и остаются; это названо вслух и в артефакте.
  * ПАРОЛИ И КЛЮЧИ (23.09.2026) → `[secret:p<8 hex>]`: значение после метки («пароль: …»,
    «pin 4521», «token=…») и ключи, узнаваемые сами по себе (токен бота, `sk-…`, `ghp_…`).
    Метка остаётся — Штабу важно видеть, ЧТО здесь передавали, а не само значение.

ЧТО СЧИТАЕТСЯ ОДНИМ СООБЩЕНИЕМ (ключ дедупа `k`). Повторный прогон захвата обязан быть
безвредным, иначе первый же перезапуск удвоит архив. Ключ — `m<message_id>`, когда номер есть
(форум Штаба, выгрузки `data_export`), и `h<16 hex от ts+автор+текст>`, когда его нет
(`delivery_6m.jsonl` номеров не сохранил вовсе). Дедуп идёт ВНУТРИ файла дня: ключи читаются из
уже лежащего файла, и в него дописывается только новое.
"""

import io
import os
import re
import json
import time
import hashlib
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))

# Имя корня — не константа-литерал в десяти местах: тесты обязаны уметь увести хранилище во
# временный каталог, НЕ КАСАЯСЬ боевого (запрет задания: «тесты не касаются боевых файлов
# состояния»). Ручка одна и читается здесь.
ROOT_ENV = "CHATLOG_ROOT"
DEFAULT_ROOT = os.path.join(HERE, "chatlog")

INDEX_DIR = "_index"
MANIFEST = "_manifest.json"
SALT_FILE = "_salt"

# Головка строки в месячном индексе. 200 символов — из проекта переписи §7.3: индекс отвечает
# «в какие дни и в какой теме вообще звучало это слово», а не заменяет собой день.
INDEX_HEAD = 200

SCHEMA = 1


# ─────────────────────────── пути ───────────────────────────

def root():
    """Корень хранилища. Env-ручка перекрывает дефолт — этим пользуются тесты."""
    return (os.getenv(ROOT_ENV) or "").strip() or DEFAULT_ROOT


def _ensure(path):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def day_path(group_slug, day):
    """<корень>/<группа>/<ГГГГ>/<ММ>/<ГГГГ-ММ-ДД>.jsonl. Каталоги заводит сам."""
    y, m = day[:4], day[5:7]
    return os.path.join(_ensure(os.path.join(root(), group_slug, y, m)), day + ".jsonl")


def index_path(month):
    """<корень>/_index/<ГГГГ-ММ>.tsv."""
    return os.path.join(_ensure(os.path.join(root(), INDEX_DIR)), month + ".tsv")


def manifest_path():
    return os.path.join(_ensure(root()), MANIFEST)


# ─────────────────────────── псевдонимы и чистка текста ───────────────────────────

_SALT_CACHE = {}


def salt():
    """Соль псевдонимов. Заводится ОДИН раз и потом только читается.

    Кеш по корню — не микрооптимизация: `who_ref` зовётся на КАЖДОЕ сообщение, а захват идёт
    десятками тысяч за прогон. Без кеша один захват открывал бы файл соли пятьдесят тысяч раз.
    Ключ — корень, потому что тесты уводят хранилище во временный каталог, и соль там своя.

    Почему не константа в коде: константа уехала бы в git, а псевдоним, чья соль лежит в
    публичной истории, псевдонимом не является — любой, у кого есть список user_id, развернёт
    его перебором за секунду. Почему не `.env`: `.env` под запретом задания и вообще не место
    для файла, который обязан жить рядом с самим архивом (перенесли архив — перенесли соль,
    иначе все псевдонимы разъезжаются и история одного человека рвётся надвое)."""
    base = root()
    if base in _SALT_CACHE:
        return _SALT_CACHE[base]
    p = os.path.join(_ensure(base), SALT_FILE)
    try:
        with io.open(p, encoding="utf-8") as f:
            got = f.read().strip()
        if got:
            _SALT_CACHE[base] = got
            return got
    except (IOError, OSError):
        pass
    import secrets
    new = secrets.token_hex(32)
    # "x" — создать и упасть, если уже есть: гонка двух захватов не смеет ПЕРЕПИСАТЬ соль.
    # Переписанная соль тише всего ломает архив: старые псевдонимы остаются валидными строками,
    # просто перестают совпадать с новыми, и один человек навсегда становится двумя.
    try:
        with io.open(p, "x", encoding="utf-8", newline="\n") as f:
            f.write(new + "\n")
        _SALT_CACHE[base] = new
        return new
    except (IOError, OSError):
        with io.open(p, encoding="utf-8") as f:
            got = f.read().strip()
        _SALT_CACHE[base] = got
        return got


def who_ref(raw, kind="p"):
    """Сырой идентификатор человека → стабильный псевдоним `p<12 hex>`.

    На вход идёт то, что источник знает об авторе: `@ник`, `id12345`, голый user_id. Разные
    источники зовут одного человека по-разному, и склеить их мы не беремся — врать о склейке
    хуже, чем честно держать два псевдонима. `kind` оставлен для нечеловеческих авторов
    (см. `SELF_REF`)."""
    s = str(raw or "").strip()
    if not s:
        return "p" + "0" * 12
    h = hashlib.sha256((salt() + "\x00" + s).encode("utf-8")).hexdigest()
    return kind + h[:12]


# Наша собственная полоса — НЕ человек, и псевдонимить её нечего и незачем: за этим именем нет
# ни имени, ни ника, ни телефона, а Штабу нужно видеть, что строка исходящая, а не входящая.
SELF_REF = "bot:pc"

# Лукбехайнд держит ровно одно: не дать сработать на «собаке» внутри уже подставленного куска.
# Ни `\w`, ни `/` в нём НЕТ, и это замер, а не вкус: с ними аудит живого захвата 05.09 нашёл
# пять ников, уцелевших ровно потому, что перед «собакой» стояла буква (`[B@rtolu4i](…)` —
# отображаемое имя из чата-пересыльщика). Почту и ссылки `t.me/` теперь снимают отдельные
# правила ниже, поэтому исключать их лукбехайндом больше незачем.
_RE_NICK = re.compile(r"(?<!@)@([A-Za-z][A-Za-z0-9_]{4,31})\b")

# ССЫЛКА `t.me/<ник>` — САМЫЙ КРУПНЫЙ носитель ников, и «собаки» в нём нет вовсе. Замер живого
# захвата 05.09: 6145 РАЗНЫХ таких ссылок против пяти уцелевших «собачьих» ников. Почти все —
# из чата-пересыльщика, который каждую пересылку подписывает ссылкой на автора: без этого
# правила архив нёс бы поимённый список людей, ни разу не назвав их в поле автора.
_TME_KEEP = frozenset(("joinchat", "addstickers", "addtheme", "addlist", "proxy", "share",
                       "socks", "setlanguage", "iv", "bg", "confirmphone"))
_RE_TME = re.compile(r"(?i)\bt\.me/([A-Za-z][A-Za-z0-9_]{4,31})")

# Почтовый адрес — тоже человек. Их в живом захвате всего два, то есть правило почти ничего не
# стоит; стои́т оно того, что без него `имя@домен` оставался бы в тексте целиком.
_RE_MAIL = re.compile(r"(?<![\w.@-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Телефон: 10–15 цифр, разрешены пробелы/дефисы/скобки внутри. Порог в десять цифр взят не с
# потолка — он разводит телефон и ЧИСЛА РАБОЧЕЙ ПЕРЕПИСКИ, которых здесь полно: цена (3–5 цифр),
# пробег, номер байка, дата (8). Ниже десяти маска съедала бы смысл ради безопасности.
_RE_PHONE = re.compile(r"(?<![\d])(\+?\d[\d\s\-()]{8,18}\d)(?![\d])")

# ПАРОЛИ И КЛЮЧИ (23.09.2026, задание ОБСЛУЖФОРУМЖУРНАЛФОТО2309: «пароли/ключи в тексте резать
# псевдонимизацией как весь chatlog»). До этого дня чистка знала ников, почту и телефоны — и ни
# одного секрета: «пароль от вайфая: …», код замка, токен в пересланном конфиге ложились дословно.
# Форм две, и у них РАЗНАЯ строгость, потому что цена ложного срабатывания разная:
#   * «метка: значение» / «метка=значение» — двоеточие само говорит «дальше значение», берём любое
#     значение от трёх знаков. Между меткой и двоеточием допускается до 30 знаков («пароль от
#     вайфая: …»), но НЕ `[`: иначе вторая чистка приняла бы двоеточие ВНУТРИ уже подставленного
#     `[secret:p…]` за двоеточие метки и замаскировала бы псевдоним ещё раз;
#   * «метка значение» без двоеточия — только когда в значении есть ЦИФРА. Иначе «pass the bike»
#     или «пароль от вайфая спроси» съели бы слово, которое секретом не является.
# Меток «код» и «ключ» НЕТ сознательно: в переписке Обслуживания это «код ошибки P0301» и «ключ от
# байка», то есть смысл архива, а не секрет.
_SECRET_LABELS = (r"парол[ьяюие]\w{0,2}|password|passwd|pwd|pass|пин-?код|pin|token|токен"
                  r"|api[ _-]?key|secret|секрет|รหัสผ่าน")
# `(?<!\[)` — метка `secret` совпадает со словом внутри нашей же подстановки `[secret:p…]`; без
# этого замка вторая чистка маскировала бы псевдоним (регресс поймал: `[secret:[secret:p…]`).
_RE_SECRET_COLON = re.compile(r"(?i)(?<!\[)\b(%s)([^:=\n\[]{0,30}?[:=]\s*)([^\s,;]{3,})"
                              % _SECRET_LABELS)
_RE_SECRET_SPACE = re.compile(r"(?i)(?<!\[)\b(%s)(\s+)(?=[^\s,;]*\d)([^\s,;]{4,})" % _SECRET_LABELS)
# Ключи, опознаваемые БЕЗ метки: по форме, которую сами выдают сервисы.
_RE_SECRET_BARE = re.compile(
    r"\b(\d{8,10}:[A-Za-z0-9_-]{35}"                 # токен Telegram-бота
    r"|sk-(?:ant-)?[A-Za-z0-9_-]{16,}"               # ключи API моделей
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,})")
SECRET_MARK = "[secret:"


# Уже подставленный псевдоним САМ ПОХОЖ НА НИК (`@p32c57754` — буква и восемь знаков), и без
# этого замка вторая чистка того же текста хешировала бы его заново. Цена промаха не
# теоретическая: `chatlog_find` чистит ЗАПРОС той же функцией, и владелец, скопировавший
# псевдоним из выдачи поиска обратно в запрос, не нашёл бы им ничего. Ложное срабатывание
# (настоящий ник вида `@p` + восемь hex-знаков) оставит в тексте строку, которая и так
# неотличима от псевдонима, — то есть промах здесь безопасен по построению.
_RE_ALREADY_REF = re.compile(r"^p[0-9a-f]{8}$")


def _nick_sub(m):
    nick = m.group(1)
    if _RE_ALREADY_REF.match(nick):
        return m.group(0)
    return "@" + who_ref(nick.lower())[:9]


def _phone_sub(m):
    raw = m.group(1)
    digits = re.sub(r"\D", "", raw)
    if not (10 <= len(digits) <= 15):
        return raw                      # не телефон — вернуть дословно, ничего не портя
    return "[tel:" + who_ref(digits)[:9] + "]"


def _tme_sub(m):
    nick = m.group(1)
    if nick.lower() in _TME_KEEP or _RE_ALREADY_REF.match(nick):
        return m.group(0)                    # служебный путь Telegram, а не имя человека
    return "t.me/" + who_ref(nick.lower())[:9]


def _mail_sub(m):
    return "[mail:" + who_ref(m.group(0).lower())[:9] + "]"


def _secret_ref(value):
    return SECRET_MARK + who_ref(value)[:9] + "]"


def _secret_labeled_sub(m):
    value = m.group(3)
    if value.startswith(SECRET_MARK):
        return m.group(0)                    # уже подставленный псевдоним: вторая чистка — no-op
    return m.group(1) + m.group(2) + _secret_ref(value)


def _secret_bare_sub(m):
    return _secret_ref(m.group(1))


def scrub(text):
    """Текст сообщения → текст без ников, почты и телефонов, с сохранением всего остального.

    Обратима только совпадением: одинаковый ник даёт одинаковый псевдоним, поэтому поиск
    работает — `chatlog_find` прогоняет запрос через эту же функцию.

    Порядок правил не случаен: почта снимается ПЕРВОЙ (иначе правило ника откусило бы от неё
    домен), затем секреты (ДО телефона: цифровой пин из десяти знаков иначе стал бы «телефоном»),
    затем ссылки `t.me/`, и только потом «собачьи» ники и телефоны. Псевдоним у ника из
    ссылки и у того же ника с «собакой» ОДИН И ТОТ ЖЕ — иначе один человек стал бы двумя, а
    поиск по нему нашёл бы половину."""
    s = str(text or "")
    s = _RE_MAIL.sub(_mail_sub, s)
    s = _RE_SECRET_BARE.sub(_secret_bare_sub, s)
    s = _RE_SECRET_COLON.sub(_secret_labeled_sub, s)
    s = _RE_SECRET_SPACE.sub(_secret_labeled_sub, s)
    s = _RE_TME.sub(_tme_sub, s)
    s = _RE_NICK.sub(_nick_sub, s)
    s = _RE_PHONE.sub(_phone_sub, s)
    return s


_RE_SLUG_BAD = re.compile(r"[^0-9a-zA-Zа-яА-ЯёЁ_-]+")


def slugify(name, fallback="group"):
    """Имя группы → имя каталога. Кириллицу НЕ транслитерируем: каталог `Отметка` читается
    человеком, а `otmetka` — нет, и обе файловые системы в игре её держат."""
    s = unicodedata.normalize("NFKC", str(name or "")).strip()
    s = _RE_SLUG_BAD.sub("_", s).strip("_")
    return (s[:60] or fallback).lower() if s.isascii() else (s[:60] or fallback)


# ─────────────────────────── строка сообщения ───────────────────────────

def msg_key(mid, ts, ref, text):
    """Ключ дедупа. См. шапку модуля: номер, когда он есть, иначе хеш содержимого."""
    if mid not in (None, "", 0, "0"):
        return "m%s" % mid
    payload = "%s\x00%s\x00%s" % (ts or "", ref or "", text or "")
    return "h" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def line(ts, ref, text, mid=None, topic_id=None, topic_name=None, media=None, extra=None):
    """Одно сообщение → dict строки дня. Полей с именем, ником и телефоном тут нет НАМЕРЕННО:
    их нельзя передать, потому что их некуда положить."""
    raw = str(text or "")
    t = scrub(raw)
    # Ключ считается по СЫРОМУ тексту, а не по вычищенному, и это не мелочь. Чистка будет
    # улучшаться (аудит 05.09 добавил к ней три правила за один заход); если бы ключ зависел от
    # её результата, каждое такое улучшение меняло бы ключи всех сообщений без номера — и
    # СЛЕДУЮЩИЙ захват дописал бы их в архив ВТОРОЙ РАЗ как новые. Сырой текст не меняется
    # никогда, поэтому ключ вечен. Утечки в ключе нет: это хеш.
    d = {
        "k": msg_key(mid, ts, ref, raw),
        "mid": mid,
        "ts": ts,
        "topic_id": topic_id,
        "topic_name": topic_name,
        "who_ref": ref,
        "text": t,
        "media": list(media or []),
    }
    if extra:
        for k, v in extra.items():
            if k not in d:                       # своим полям чужое имя не перебить
                d[k] = v
    return d


def day_of(ts):
    """ISO-время → 'ГГГГ-ММ-ДД'. Чужих форматов не угадываем: не разобрали — вернём ''."""
    s = str(ts or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return ""


# ─────────────────────────── запись ───────────────────────────

def existing_keys(path):
    """Ключи, уже лежащие в файле дня. Битая строка НЕ роняет чтение: архив дописывается
    несколькими источниками, и одна испорченная строка не смеет отменить дедуп остальных."""
    keys = set()
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    o = json.loads(raw)
                except ValueError:
                    continue
                k = o.get("k") if isinstance(o, dict) else None
                if k:
                    keys.add(k)
    except (IOError, OSError):
        pass
    return keys


def _index_keys(path):
    """Ключи, уже лежащие в месячном индексе → set((группа, ключ))."""
    keys = set()
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                parts = raw.rstrip("\n").split("\t")
                if len(parts) >= 4:
                    keys.add((parts[1], parts[3]))
    except (IOError, OSError):
        pass
    return keys


def _index_row(day, group_slug, rec):
    head = " ".join(str(rec.get("text") or "").split())[:INDEX_HEAD]
    topic = str(rec.get("topic_name") or rec.get("topic_id") or "-")
    topic = topic.replace("\t", " ")
    return "\t".join([day, group_slug, topic, str(rec.get("k") or "-"),
                      str(rec.get("who_ref") or "-"), head])


class Writer(object):
    """Накопитель строк с записью пачкой. Открывать файл дня на каждое сообщение — это 50 000
    открытий на одном захвате; пачка даёт одно открытие на день."""

    def __init__(self):
        self.buf = {}                    # (group_slug, day) → [rec, …]
        self.groups = {}                 # group_slug → сведения для манифеста
        self.skipped_no_day = 0

    def note_group(self, group_slug, title=None, group_id=None, kind=None, source=None):
        g = self.groups.setdefault(group_slug, {"title": None, "group_id": None,
                                                "kind": None, "sources": []})
        if title and not g["title"]:
            g["title"] = title
        if group_id is not None and g["group_id"] is None:
            g["group_id"] = group_id
        if kind and not g["kind"]:
            g["kind"] = kind
        if source and source not in g["sources"]:
            g["sources"].append(source)

    def add(self, group_slug, rec):
        day = day_of(rec.get("ts"))
        if not day:
            self.skipped_no_day += 1     # без дня строке некуда лечь: день — ключ хранилища
            return False
        self.buf.setdefault((group_slug, day), []).append(rec)
        return True

    def commit(self):
        """Дописать всё накопленное. → dict со статистикой захвата.

        Дописать, а не записать: `"a"` — единственный режим открытия во всём модуле."""
        stats = {"written": 0, "dup": 0, "days": 0, "groups": set(),
                 "index_rows": 0, "skipped_no_day": self.skipped_no_day}
        by_month = {}
        for (group_slug, day) in sorted(self.buf):
            recs = self.buf[(group_slug, day)]
            path = day_path(group_slug, day)
            seen = existing_keys(path)
            fresh = []
            for r in recs:
                k = r.get("k")
                if k in seen:
                    stats["dup"] += 1
                    continue
                seen.add(k)
                fresh.append(r)
            if not fresh:
                continue
            with io.open(path, "a", encoding="utf-8", newline="\n") as f:
                for r in fresh:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            stats["written"] += len(fresh)
            stats["days"] += 1
            stats["groups"].add(group_slug)
            by_month.setdefault(day[:7], []).extend((day, group_slug, r) for r in fresh)
            g = self.groups.setdefault(group_slug, {"title": None, "group_id": None,
                                                    "kind": None, "sources": []})
            g["first_day"] = min(g.get("first_day") or day, day)
            g["last_day"] = max(g.get("last_day") or day, day)
            g["messages"] = int(g.get("messages") or 0) + len(fresh)

        for month in sorted(by_month):
            path = index_path(month)
            seen = _index_keys(path)
            rows = []
            for (day, group_slug, r) in by_month[month]:
                key = (group_slug, str(r.get("k") or "-"))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(_index_row(day, group_slug, r))
            if rows:
                with io.open(path, "a", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(rows) + "\n")
                stats["index_rows"] += len(rows)

        _merge_manifest(self.groups)
        stats["groups"] = sorted(stats["groups"])
        return stats


def read_manifest():
    try:
        with io.open(manifest_path(), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def _merge_manifest(groups):
    """Манифест — СВОДКА о захвате, а не сами данные, и потому единственное место, которое
    переписывается целиком. Данным это не грозит ничем: манифест пересобирается из хранилища,
    а хранилище из манифеста — нет. Пишем через `.tmp` + `os.replace`, чтобы обрыв не оставил
    полуфайла."""
    m = read_manifest()
    m["schema"] = SCHEMA
    m.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%S"))
    m["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    m.setdefault("note", "Ротации нет ни в одной ветке. Автор — только псевдоним; "
                         "имени, ника и телефона в архиве нет ни в одном поле.")
    gs = m.setdefault("groups", {})
    for slug, info in groups.items():
        cur = gs.setdefault(slug, {})
        for key in ("title", "group_id", "kind"):
            if info.get(key) is not None and cur.get(key) is None:
                cur[key] = info[key]
        srcs = cur.setdefault("sources", [])
        for s in info.get("sources", []):
            if s not in srcs:
                srcs.append(s)
        if info.get("first_day"):
            cur["first_day"] = min(cur.get("first_day") or info["first_day"], info["first_day"])
        if info.get("last_day"):
            cur["last_day"] = max(cur.get("last_day") or info["last_day"], info["last_day"])
        if info.get("messages"):
            cur["messages"] = int(cur.get("messages") or 0) + int(info["messages"])
    tmp = manifest_path() + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(m, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, manifest_path())
    return m


# ─────────────────────────── чтение назад ───────────────────────────

def walk_days(group_slug=None):
    """Все файлы дней → [(группа, день, путь)], отсортировано. Дверь для `chatlog_find` и для
    доказательства чтением назад."""
    out = []
    base = root()
    if not os.path.isdir(base):
        return out
    for slug in sorted(os.listdir(base)):
        if slug.startswith("_") or (group_slug and slug != group_slug):
            continue
        gdir = os.path.join(base, slug)
        if not os.path.isdir(gdir):
            continue
        for dirpath, _dirnames, files in os.walk(gdir):
            for fn in sorted(files):
                if fn.endswith(".jsonl") and len(fn) == 16:
                    out.append((slug, fn[:-6], os.path.join(dirpath, fn)))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def stats():
    """Что лежит в хранилище прямо сейчас → dict. Считает по ФАЙЛАМ, а не по манифесту:
    манифест — наше слово о захвате, а файлы — сам архив."""
    res = {"root": root(), "groups": {}, "messages": 0, "days": 0, "bytes": 0,
           "index_months": [], "index_rows": 0}
    for slug, day, path in walk_days():
        n = 0
        try:
            with io.open(path, encoding="utf-8", errors="replace") as f:
                for raw in f:
                    if raw.strip():
                        n += 1
            res["bytes"] += os.path.getsize(path)
        except (IOError, OSError):
            continue
        g = res["groups"].setdefault(slug, {"messages": 0, "days": 0,
                                            "first_day": day, "last_day": day})
        g["messages"] += n
        g["days"] += 1
        g["first_day"] = min(g["first_day"], day)
        g["last_day"] = max(g["last_day"], day)
        res["messages"] += n
        res["days"] += 1
    idir = os.path.join(root(), INDEX_DIR)
    if os.path.isdir(idir):
        for fn in sorted(os.listdir(idir)):
            if not fn.endswith(".tsv"):
                continue
            res["index_months"].append(fn[:-4])
            try:
                with io.open(os.path.join(idir, fn), encoding="utf-8", errors="replace") as f:
                    res["index_rows"] += sum(1 for raw in f if raw.strip())
            except (IOError, OSError):
                pass
    return res


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Состояние хранилища переписки (только чтение).")
    ap.add_argument("--stats", action="store_true", help="что лежит в хранилище")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    a = ap.parse_args(argv)
    s = stats()
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print("корень: %s" % s["root"])
    print("сообщений: %d в %d файлах дней, %.1f КБ" % (s["messages"], s["days"], s["bytes"] / 1024.0))
    print("индекс: %d строк, месяцы: %s" % (s["index_rows"], ", ".join(s["index_months"]) or "-"))
    for slug in sorted(s["groups"]):
        g = s["groups"][slug]
        print("  %-28s %6d сообщ.  %4d дн.  %s … %s"
              % (slug, g["messages"], g["days"], g["first_day"], g["last_day"]))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main(sys.argv[1:]))
