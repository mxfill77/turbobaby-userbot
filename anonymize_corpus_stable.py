# -*- coding: utf-8 -*-
"""anonymize_corpus_stable.py — обезличивание корпуса меткой, УСТОЙЧИВОЙ ПО ЧЕЛОВЕКУ.

ЗАЧЕМ. У `anonymize_corpus.py` метка есть функция НОМЕРА СТРОКИ, а не человека
(замер `docs/artifacts/2026-08-16-dialog-link-labels.md`, коммит bb9c569): 1000 разных лиц
на одной строке дают ОДНУ метку, одно лицо на 710 строках — 710 меток. Такой корпус нельзя
сцепить ни с чем: ключ уничтожен в момент записи. Здесь метка считается КРИПТОГРАФИЧЕСКИМ
хешем от НОРМАЛИЗОВАННОГО значения, поэтому один и тот же телефон (ник, telegram-id) даёт
одну и ту же метку в любой строке корпуса и в любом другом источнике, прогнанном через ту
же функцию.

ЧТО НЕ МЕНЯЛОСЬ. Детектор персонального — целиком чужой: `anonymize_corpus._clean_text`
вызывается ДОСЛОВНО, ни одно регулярное выражение здесь не переписано. Меняется ровно
распределитель меток (`_Labels.label`) и шапка записи. Поэтому полнота обезличивания
обязана совпасть с прежней до вхождения — и это замок C.

ЧТО СТАЛО МЕТКОЙ ЧЕЛОВЕКА, А ЧТО ОСТАЛОСЬ ПОЗИЦИОННЫМ (решение, а не недосмотр):

    телефон, @ник, telegram-id  → ХЕШ ОТ ЗНАЧЕНИЯ. Это идентификаторы: они называют
                                  человека и сравнимы между источниками.
    имя (Лицо_N), геопин, карта → СЧЁТЧИК ВНУТРИ ДИАЛОГА, как было. Имя человека НЕ
                                  идентифицирует (два разных Сергея слились бы в одного
                                  под общей меткой — это хуже, чем отсутствие сцепки),
                                  а сквозная метка геопина — это трекер домашнего адреса
                                  через весь корпус, которого задача не просила.

СОЛЬ. Хеш солёный: телефонов на свете мало, и несолёный SHA-256 от номера перебирается
насквозь за минуты — метка была бы обратимой. Соль генерируется ОДИН раз в
`tmp/anon_label_salt.txt` (каталог под `.gitignore`); в файл секретов проекта она НЕ
кладётся и оттуда НЕ читается — этот модуль конфигурацию не открывает ни одной веткой.
Файл соли не печатается ни одной веткой; для сверки «та же соль?» есть отпечаток
(`--salt-status`) — HMAC от постоянной строки, по нему соль не восстанавливается.

ТАБЛИЦЫ СООТВЕТСТВИЙ «метка ↔ оригинал» НЕТ и не создаётся: словари живут в памяти вызова,
наружу выходят только метки и счётчики.

ЗАПУСК:
    venv/Scripts/python.exe anonymize_corpus_stable.py            # сборка + четыре замка
    venv/Scripts/python.exe anonymize_corpus_stable.py --salt-status
    venv/Scripts/python.exe anonymize_corpus_stable.py --norm-demo   # правило нормализации
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from collections import Counter

import anonymize_corpus as A
import io_utf8

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DEFAULT = os.path.join(HERE, "client_chats.jsonl")
OUT_DEFAULT = os.path.join(HERE, "client_chats.anonstable.jsonl")
SALT_PATH = os.path.join(HERE, "tmp", "anon_label_salt.txt")

# Алфавит метки — ТОЛЬКО СОГЛАСНЫЕ, и это не украшение. Шестнадцатеричная метка с шансом
# ~0.5 % выходит целиком из цифр, и тогда `<телефон_123456789012>` при повторном проходе
# снова опознаётся телефоном (слева стоит «тел», ветка слова-указателя в `_phone_ok`
# срабатывает) — идемпотентность рушится, а вместе с ней право замка говорить «ноль».
# Буквенная метка не задевает НИ ОДНО выражение детектора: цифр в ней нет вовсе, а `_`
# перед ней не даёт границы слова `\b` проходу имён. Гласные выкинуты, чтобы метка не
# складывалась в похожее на имя слово и не ловилась газеттиром.
ALPHABET = "bcdfghjklmnpqrstvwxz"
TAG_LEN = 12                       # 20^12 ≈ 4.1e15 ≈ 51.7 бита

PHONE_LABEL = "<телефон_%s>"
NICK_LABEL = "профиль_%s"          # «@» перед меткой ставит сам текст корпуса
PROFILE_URL_LABEL = "<профиль_%s>"
CLIENT_LABEL = "Клиент_%s"
TGID_LABEL = "tgid_%s"

DOM_PHONE = "phone"
DOM_NICK = "nick"
DOM_TGID = "tgid"

# Детектор обязан узнавать СВОИ метки, иначе повторный проход принимает «вилла Клиент_kmqx»
# за новый адрес (`anonymize_corpus:411`, `named_ok`). Родное выражение ждёт `_\d+` и на
# буквенную метку не срабатывает — расширяем его прямо в чужом модуле, СОЗНАТЕЛЬНО и в одном
# месте: правка делает выражение только ШИРЕ, поведение исходного инструмента не меняет.
A._RE_OWN_LABEL = re.compile(r"^(?:Лицо|Клиент|профиль)_[0-9A-Za-z]+$")

# Значения для ОТРИЦАТЕЛЬНОГО замка D. Выдуманы здесь, в корпусе их нет — и это проверяется
# замером, а не заявляется.
SYN_PHONES = ("+66 55 000 0001", "+7 555 000 00 02", "0550000003", "00 66 55 000 0004")
SYN_NICKS = ("@synthetic_ghost_one", "synthetic_ghost_two", "@SYNTHETIC_Ghost_Three")


# --------------------------------------------------------------------------------------
# НОРМАЛИЗАЦИЯ. Без неё устойчивости нет: «+66 81 234 5678» и «081-234-5678» — один человек
# и обязаны дать одну метку. Правило названо дословно в артефакте и повторено здесь.
# --------------------------------------------------------------------------------------

def phone_norm(value):
    """Телефон → канонический вид (междунар. форма без «+»). Шаги — по порядку:

    1) оставить ТОЛЬКО цифры («+», пробелы, скобки, дефисы, U+00A0 выбрасываются);
    2) если ряд начинается на «00» — снять эти два знака (международный доступ);
    3) снять ВСЕ ведущие нули (национальный «0» перед кодом оператора);
    4) достроить код страны по ДВУМ кодам, которые покрывают полосу:
         11 цифр с «8»     → «7» + хвост   (РФ: междугородний «8» ≡ код страны «7»)
         10 цифр с «9»     → «7» + весь    (РФ: мобильный записан без кода страны)
          9 цифр с 6/8/9   → «66» + весь   (Таиланд: мобильный, ведущий «0» уже снят)
    5) всё, что не подошло под п.4, остаётся как есть.

    Шаг 4 — единственное место, где правило ЗНАЕТ о странах, и он сознательно узкий:
    достройка кода может только СКЛЕИТЬ два написания одного номера, а разорвать
    ничего не может. Остаточный класс назван замером (тайские городские: 8 цифр после
    снятия нуля, кода страны им здесь не приписывается)."""
    d = re.sub(r"\D", "", str(value or ""))
    if not d:
        return ""
    if d.startswith("00"):
        d = d[2:]
    d = d.lstrip("0")
    if len(d) == 11 and d[0] == "8":
        return "7" + d[1:]
    if len(d) == 10 and d[0] == "9":
        return "7" + d
    if len(d) == 9 and d[0] in "689":
        return "66" + d
    return d


_RE_TME = re.compile(r"(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{3,32})", re.I)


def nick_norm(value):
    """@ник → канонический вид. Шаги:

    1) обрезать пробелы по краям;
    2) если это ссылка `t.me/<имя>` — взять из неё имя (ссылка и `@имя` — один человек);
    3) снять ведущие «@» (сколько бы их ни было);
    4) отбросить хвостовые знаки вне [A-Za-z0-9_] — свободное поле базы пишет «@ivanov,»;
    5) НИЖНИЙ РЕГИСТР (у телеграма ник регистронезависим)."""
    v = str(value or "").strip()
    m = _RE_TME.search(v)
    if m:
        v = m.group(1)
    v = v.lstrip("@").strip()
    v = re.sub(r"[^A-Za-z0-9_]+$", "", v)
    return v.lower()


def tgid_norm(value):
    """telegram-id → канонический вид: только цифры, без ведущих нулей и знака."""
    d = re.sub(r"\D", "", str(value or ""))
    return d.lstrip("0") or d


# --------------------------------------------------------------------------------------
# Метка
# --------------------------------------------------------------------------------------

def load_salt(path=SALT_PATH):
    """Соль прогона. Есть файл — берём его; нет — создаём ОДИН раз и больше не трогаем.

    Значение не возвращается наружу ни печатью, ни исключением: оно живёт в памяти вызова.
    Создание — через O_EXCL, чтобы гонка двух прогонов не перетёрла чужую соль (перетёртая
    соль = все метки корпуса поменялись молча)."""
    if os.path.isfile(path):
        with open(path, "rb") as f:
            raw = f.read().strip()
        if len(raw) < 32:
            raise ValueError("файл соли короче 32 байт: %s — метка была бы слабой" % path)
        return raw
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    fresh = secrets.token_hex(32).encode("ascii")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, fresh)
    finally:
        os.close(fd)
    return fresh


def salt_fingerprint(salt):
    """Отпечаток соли: HMAC от ПОСТОЯННОЙ строки. Позволяет сказать «соль та же», не
    показывая соль; обратно из отпечатка соль не считается."""
    return hmac.new(salt, b"anon-salt-fingerprint", hashlib.sha256).hexdigest()[:16]


def tag(domain, norm, salt):
    """Нормализованное значение → метка. HMAC-SHA256 на соли, разделение доменов внутри
    подписи: телефон и ник с одинаковым текстом метку не разделят никогда."""
    if not norm:
        return ""
    mac = hmac.new(salt, (domain + "|" + norm).encode("utf-8"), hashlib.sha256).digest()
    n = int.from_bytes(mac, "big")
    out = []
    for _ in range(TAG_LEN):
        out.append(ALPHABET[n % len(ALPHABET)])
        n //= len(ALPHABET)
    return "".join(out)


class Collector(object):
    """Копилка замков. Держит ТОЛЬКО соответствия внутри памяти прогона и никуда их не
    пишет: наружу из неё выходят исключительно ЧИСЛА."""

    def __init__(self):
        self.norm_tags = {}        # домен|норма -> множество меток (замок A: обязан быть 1)
        self.tag_norms = {}        # домен|метка -> множество норм (замок B: обязан быть 1)
        self.rows = {}             # домен|метка -> множество номеров записей корпуса
        self.spellings = {}        # домен|норма -> множество СЫРЫХ написаний
        self.hits = Counter()      # домен|метка -> вхождений
        self.empty = Counter()

    def note(self, domain, raw, norm, label, row):
        if not norm:
            self.empty[domain] += 1
            return
        k = domain + "|" + norm
        self.norm_tags.setdefault(k, set()).add(label)
        self.spellings.setdefault(k, set()).add(str(raw))
        t = domain + "|" + label
        self.tag_norms.setdefault(t, set()).add(norm)
        self.rows.setdefault(t, set()).add(row)
        self.hits[t] += 1

    def has_norm(self, domain, norm):
        return (domain + "|" + norm) in self.norm_tags

    def has_tag(self, domain, label):
        return (domain + "|" + label) in self.tag_norms


class StableLabels(A._Labels):
    """Распределитель меток. Для идентификаторов метка считается ОТ ЗНАЧЕНИЯ и словарём
    диалога не пользуется вовсе; для всего остального работает родной счётчик."""

    def __init__(self, salt, collector, row):
        A._Labels.__init__(self)
        self.salt = salt
        self.col = collector
        self.row = row

    def _stable(self, domain, raw, norm, tpl):
        lab = tag(domain, norm, self.salt)
        if not lab:
            return None
        self.col.note(domain, raw, norm, lab, self.row)
        return tpl % lab

    def label(self, kind, key, tpl):
        if kind == A.KIND_PHONE:
            got = self._stable(DOM_PHONE, key, phone_norm(key), PHONE_LABEL)
            if got:
                return got
        elif kind == A.KIND_USER_TEXT:
            got = self._stable(DOM_NICK, key, nick_norm(key), NICK_LABEL)
            if got:
                return got
        elif kind == A.KIND_PROFILE:
            # ссылка `t.me/<ник>` — тот же человек, что и «@<ник>»: одна метка на обе формы.
            n = nick_norm(key) if _RE_TME.search(str(key)) else ""
            got = self._stable(DOM_NICK, key, n, PROFILE_URL_LABEL) if n else None
            if got:
                return got
        return A._Labels.label(self, kind, key, tpl)


# --------------------------------------------------------------------------------------
# Запись
# --------------------------------------------------------------------------------------

def anonymize_record(rec, ordinal, names, stats, salt, collector):
    """Запись корпуса → обезличенная запись с меткой, устойчивой по человеку.

    Порядок и счётчики повторяют `anonymize_corpus.anonymize_record` вхождение в вхождение
    (замок C это меряет). Отличий ровно три, и все — в шапке: имя собеседника выводится из
    его telegram-id, а не из номера строки; ник и телефон шапки получают метку от значения;
    телефон шапки больше НЕ СТИРАЕТСЯ в пустоту, а становится меткой — иначе сцепка по
    телефону невозможна в принципе."""
    labels = StableLabels(salt, collector, ordinal)
    out = dict(rec)

    pid = rec.get("peer_id")
    person = tag(DOM_TGID, tgid_norm(pid), salt) if pid is not None else ""
    if person:
        collector.note(DOM_TGID, pid, tgid_norm(pid), person, ordinal)
    else:
        # запасной ключ личности: ник, а если нет и его — номер строки (позиционный, и это
        # честно названо в отчёте отдельным числом)
        un0 = rec.get("peer_username")
        nn = nick_norm(un0) if isinstance(un0, str) else ""
        person = tag(DOM_NICK, nn, salt) if nn else "row%04d" % ordinal
        stats["личность без telegram-id"] += 1

    me_name = CLIENT_LABEL % person

    pn = rec.get("peer_name")
    if isinstance(pn, str) and pn.strip():
        stats[A.KIND_NAME_HEAD] += 1
        for tok in A.name_tokens(pn):
            labels.preset(A.KIND_NAME_TEXT, A._stems(tok), me_name)
    out["peer_name"] = me_name

    un = rec.get("peer_username")
    if isinstance(un, str) and un.strip():
        stats[A.KIND_USER_HEAD] += 1
        nn = nick_norm(un)
        if nn:
            lab = tag(DOM_NICK, nn, salt)
            collector.note(DOM_NICK, un, nn, lab, ordinal)
            out["peer_username"] = NICK_LABEL % lab
        else:
            out["peer_username"] = NICK_LABEL % person
    else:
        # ника нет — метка профиля выводится из telegram-id. Устойчива по человеку, но с
        # ником базы не сцепится НИКОГДА: домены подписи разные, совпадение исключено.
        out["peer_username"] = NICK_LABEL % person

    ph = rec.get("peer_phone")
    if isinstance(ph, str) and ph.strip():
        stats[A.KIND_PHONE_HEAD] += 1
        pnorm = phone_norm(ph)
        if pnorm:
            lab = tag(DOM_PHONE, pnorm, salt)
            collector.note(DOM_PHONE, ph, pnorm, lab, ordinal)
            out["peer_phone"] = PHONE_LABEL % lab
        else:
            out["peer_phone"] = ""
    else:
        out["peer_phone"] = ""

    if pid is not None:
        stats[A.KIND_TGID_HEAD] += 1
    out["peer_id"] = TGID_LABEL % person

    local = set(names)
    if isinstance(pn, str):
        for tok in A.name_tokens(pn):
            local.add(tok.lower())
            local.add(A._stems(tok))

    msgs = []
    changed = 0
    for m in rec.get("messages", []):
        nm = dict(m)
        t = m.get("text")
        if isinstance(t, str) and t:
            new = A._clean_text(t, labels, local, stats)
            if new != t:
                changed += 1
            nm["text"] = new
        msgs.append(nm)
    out["messages"] = msgs
    return out, changed


def run(src, dst, salt, collector):
    recs = A.read_records(src)
    names = A.build_gazetteer(recs)
    stats = Counter()
    total_msgs = 0
    changed = 0
    with open(dst, "w", encoding="utf-8") as out:
        for i, rec in enumerate(recs, 1):
            new, ch = anonymize_record(rec, i, names, stats, salt, collector)
            total_msgs += len(new.get("messages", []))
            changed += ch
            out.write(json.dumps(new, ensure_ascii=False) + "\n")
    return len(recs), total_msgs, changed, stats, len(names)


# --------------------------------------------------------------------------------------
# ЗАМКИ
# --------------------------------------------------------------------------------------

BASE_TOTAL = 5718                  # прежний прогон, docs/artifacts/2026-08-15-corpus-pii-anonymize.md


def _dom(col, mapping, domain):
    return {k.split("|", 1)[1]: v for k, v in mapping.items() if k.startswith(domain + "|")}


def locks(col, stats, salt):
    """Четыре замка числами. Ни одно значение наружу не выходит — только счётчики."""
    print("=" * 78)
    print("ЗАМОК A — УСТОЙЧИВОСТЬ: одно значение → одна метка, в любой строке")
    ok_a = True
    for dom, word in ((DOM_PHONE, "телефон"), (DOM_NICK, "ник"), (DOM_TGID, "telegram-id")):
        nt = _dom(col, col.norm_tags, dom)
        sp = _dom(col, col.spellings, dom)
        rows = _dom(col, col.rows, dom)
        bad = sum(1 for v in nt.values() if len(v) != 1)
        multi_row = sum(1 for v in rows.values() if len(v) >= 2)
        max_row = max((len(v) for v in rows.values()), default=0)
        multi_spell = sum(1 for v in sp.values() if len(v) >= 2)
        occ = sum(n for k, n in col.hits.items() if k.startswith(dom + "|"))
        print("  %-12s значений %5d · вхождений %5d · значений с >1 меткой %d"
              % (word, len(nt), occ, bad))
        print("  %-12s   меток, встреченных в ≥2 РАЗНЫХ записях корпуса: %d (максимум %d записей"
              " на одну метку)" % ("", multi_row, max_row))
        print("  %-12s   значений, склеенных из ≥2 РАЗНЫХ написаний: %d"
              % ("", multi_spell))
        ok_a = ok_a and bad == 0
    print("  ИТОГ A: %s" % ("УСТОЙЧИВА" if ok_a else "ОТКАЗ"))

    print("=" * 78)
    print("ЗАМОК B — РАЗЛИЧИМОСТЬ: разные значения → разные метки")
    coll_total = 0
    for dom, word in ((DOM_PHONE, "телефон"), (DOM_NICK, "ник"), (DOM_TGID, "telegram-id")):
        tn = _dom(col, col.tag_norms, dom)
        nt = _dom(col, col.norm_tags, dom)
        coll = sum(len(v) - 1 for v in tn.values() if len(v) > 1)
        coll_total += coll
        print("  %-12s разных значений %5d · разных меток %5d · КОЛЛИЗИЙ %d"
              % (word, len(nt), len(tn), coll))
    # перекрёстная проверка доменов: метка телефона не смеет совпасть с меткой ника
    cross = 0
    for a, b in ((DOM_PHONE, DOM_NICK), (DOM_PHONE, DOM_TGID), (DOM_NICK, DOM_TGID)):
        cross += len(set(_dom(col, col.tag_norms, a)) & set(_dom(col, col.tag_norms, b)))
    print("  межвидовых совпадений метки (телефон/ник/tg-id): %d" % cross)
    print("  ИТОГ B: %s (всего коллизий %d)"
          % ("РАЗЛИЧИМА" if coll_total == 0 and cross == 0 else "ОТКАЗ", coll_total + cross))

    print("=" * 78)
    print("ЗАМОК C — ПОЛНОТА ОБЕЗЛИЧИВАНИЯ против прежнего прогона (%d вхождений)" % BASE_TOTAL)
    total = sum(stats[k] for k in A.KINDS)
    for k in A.KINDS:
        print("     %-26s %6d" % (k, stats[k]))
    print("     %-26s %6d   (прежде %d, разница %+d)"
          % ("ИТОГО", total, BASE_TOTAL, total - BASE_TOTAL))
    print("  ИТОГ C: %s" % ("НЕ ХУЖЕ ПРЕЖНЕЙ" if total >= BASE_TOTAL else "ХУЖЕ ПРЕЖНЕЙ"))

    print("=" * 78)
    print("ЗАМОК D — ОТРИЦАТЕЛЬНЫЙ: значение, которого в корпусе нет, ни с чем не совпало")
    absent = hit = 0
    for raw in SYN_PHONES:
        n = phone_norm(raw)
        in_corpus = col.has_norm(DOM_PHONE, n)
        t = tag(DOM_PHONE, n, salt)
        if in_corpus:
            print("  ВНИМАНИЕ: выдуманный телефон оказался в корпусе — проба негодна")
            continue
        absent += 1
        if col.has_tag(DOM_PHONE, t):
            hit += 1
    for raw in SYN_NICKS:
        n = nick_norm(raw)
        if col.has_norm(DOM_NICK, n):
            print("  ВНИМАНИЕ: выдуманный ник оказался в корпусе — проба негодна")
            continue
        absent += 1
        if col.has_tag(DOM_NICK, tag(DOM_NICK, n, salt)):
            hit += 1
    print("  проверено выдуманных значений: %d (в корпусе ОТСУТСТВУЮТ все)" % absent)
    print("  их меток, совпавших с метками корпуса: %d" % hit)
    # положительный контроль: без него «ноль совпадений» доказывал бы лишь сломанное сравнение
    pos = pos_ok = 0
    for dom in (DOM_PHONE, DOM_NICK):
        keys = sorted(k for k in col.norm_tags if k.startswith(dom + "|"))
        for k in keys[:3]:
            pos += 1
            if col.has_tag(dom, tag(dom, k.split("|", 1)[1], salt)):
                pos_ok += 1
    print("  положительный контроль (значение ИЗ корпуса → его метка обязана найтись):"
          " %d из %d" % (pos_ok, pos))
    print("  ИТОГ D: %s" % ("ЧИСТО" if hit == 0 and pos_ok == pos and pos > 0 else "ОТКАЗ"))
    print("=" * 78)
    return ok_a, coll_total + cross, total, hit


def residual_report(col):
    """Остаточные классы нормализации — названы числом, а не умолчанием."""
    ph = _dom(col, col.norm_tags, DOM_PHONE)
    lens = Counter(len(k) for k in ph)
    print("длины канонических телефонов (цифр): %s"
          % ", ".join("%d→%d" % (k, lens[k]) for k in sorted(lens)))
    suffix = 0
    keys = sorted(ph, key=len)
    longs = [k for k in keys if len(k) >= 10]
    for s in keys:
        if len(s) >= 10:
            continue
        for l in longs:
            if l.endswith(s):
                suffix += 1
                break
    print("коротких канонов, являющихся ХВОСТОМ длинного (недостроенный код страны): %d"
          % suffix)


def norm_demo():
    """Правило нормализации на ВЫДУМАННЫХ значениях — документация, проверяемая глазом."""
    print("ТЕЛЕФОН (выдуманные значения):")
    for s in ("+66 81 234 5678", "081-234-5678", "0812345678", "66812345678",
              "0066812345678", "+7 999 123-45-67", "8 (999) 123 45 67", "9991234567",
              "+66 81 234 5678", "076 123456"):
        print("   %-24s → %s" % (repr(s)[1:-1], phone_norm(s) or "(пусто)"))
    print("НИК (выдуманные значения):")
    for s in ("@Ivanov", "ivanov", "  @IVANOV  ", "@ivanov,", "t.me/Ivanov",
              "https://t.me/ivanov", "@@ivanov"):
        print("   %-24s → %s" % (repr(s)[1:-1], nick_norm(s) or "(пусто)"))


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description="обезличивание корпуса устойчивой меткой")
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--salt-status", action="store_true")
    ap.add_argument("--norm-demo", action="store_true")
    ap.add_argument("--verify", dest="verify_path", default=None,
                    help="прежний двухчастный замок остатка, но со ЗНАНИЕМ буквенных меток")
    a = ap.parse_args(argv)

    if a.norm_demo:
        norm_demo()
        return 0

    if a.verify_path:
        # Родной замок `anonymize_corpus.verify` — дословно. Разница ровно одна: `_RE_OWN_LABEL`
        # уже расширен импортом этого модуля, поэтому «вилла Клиент_kmqx» не считается новым
        # адресом. Запускать замок из чужого модуля НАПРЯМУЮ нельзя: там выражение ждёт `_\d+`.
        names = A.build_gazetteer(A.read_records(a.src))
        again, left, caps, hosts, shapes = A.verify(a.verify_path, names)
        print("ЗАМОК ОСТАТКА по %s" % os.path.basename(a.verify_path))
        print("A. ПОВТОР ЧИСТКИ тем же детектором (обязан быть нулём по каждому виду):")
        for k in A.KINDS:
            if k.endswith("(шапка)") or k.endswith("(шапка записи)"):
                continue
            print("     %-26s %d" % (k, again[k]))
        print("B. ШИРОКАЯ СЕТЬ — что уцелело и чем является:")
        for k, v in sorted(left.items(), key=lambda kv: -kv[1]):
            print("     %-46s %d" % (k, v))
        print("     %-46s %d вхождений, %d разных"
              % ("заглавных не-в-начале вне словаря", sum(caps.values()), len(caps)))
        for h, v in hosts.most_common(12):
            print("     хост уцелевшей ссылки: %-28s %d" % (h, v))
        return 0

    salt = load_salt()
    if a.salt_status:
        print("файл соли  : %s" % SALT_PATH)
        print("существует : %s" % os.path.isfile(SALT_PATH))
        print("длина      : %d символов" % len(salt))
        print("отпечаток  : %s   (HMAC от постоянной строки; соль по нему не считается)"
              % salt_fingerprint(salt))
        return 0

    if not os.path.isfile(a.src):
        print("корпус не найден: %s" % a.src)
        return 2

    col = Collector()
    n, msgs, changed, stats, gaz = run(a.src, a.out, salt, col)
    print("исходник : %s" % a.src)
    print("собрано  : %s" % a.out)
    print("отпечаток соли: %s" % salt_fingerprint(salt))
    print("записей %d, сообщений %d, ИЗМЕНЕНО сообщений %d (%.1f%%)"
          % (n, msgs, changed, 100.0 * changed / msgs if msgs else 0.0))
    print("газеттир имён (в памяти прогона): %d основ" % gaz)
    print("записей без telegram-id (личность выведена запасным ключом): %d"
          % stats.get("личность без telegram-id", 0))
    residual_report(col)
    ok_a, coll, total, neg = locks(col, stats, salt)
    return 0 if (ok_a and coll == 0 and neg == 0 and total >= BASE_TOTAL) else 1


if __name__ == "__main__":
    sys.exit(main())
