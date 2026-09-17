#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""НАБОР ОБУЧЕНИЯ — живые кейсы экзамена как ОТДЕЛЬНАЯ СУЩНОСТЬ: версия, источник, откат.

ГДЕ ЖИВЁТ. Каталог `exam_live/` (под .gitignore: внутри живой текст клиента и менеджера):
  `cases.json`        — сам набор, тот же корпус, который читает `exam_show.py --live`;
  `shots/`            — замороженные черновики кейсов набора (пишет `exam_show --live --freeze`);
  `ledger.tsv`        — журнал событий набора: завёл / назвал источник / откатил, по строке на событие;
  `rolled_back.jsonl` — откаченные кейсы ЦЕЛИКОМ, по строке на кейс. Откат ничего не уничтожает.
Корпус тренажёра `trainer_cases.json` (17 кейсов) сущностью набора НЕ является и этим модулем не
открывается даже на чтение: его отпечаток держит 20 снимков основного набора и вердикты ворот, и
любой байт, дописанный туда, отказал бы показу каждого из них.

ВЕРСИЯ — ОТПЕЧАТОК ФАЙЛА НАБОРА (sha256, 16 hex), а не число. Идиома взята у базы уроков
(`lesson_store.version`, 11.09) и у корпуса экзамена (`exam_show.corpus_fingerprint`): число
вписывается руками и после этого врёт молча — так уже случилось дважды, поле `"version": 1` стоит и
в корпусе тренажёра, и в живом наборе, и не сдвинулось ни разу, хотя корпус тренажёра за это время
менялся пятью коммитами. Отпечаток не назначается, а вычисляется, поэтому поднять его без события
нельзя. Это ТО ЖЕ значение, что ложится в снимок полем `corpus`: снимок называет версию набора, на
которой собран. Порядка редакций отпечаток не даёт — порядок даёт `ledger.tsv` (строка на событие,
в ней версия до и после).

ИСТОЧНИК — МАШИННЫЙ КЛЮЧ У КАЖДОГО КЕЙСА, поле `source_key`, форма `<база>@<отпечаток базы>/<правило>`:
  `anonstable@36d68c71d6019fe2/strict` — обезличенная база со стабильными метками, строгая сцепка;
  `anonstable@36d68c71d6019fe2/soft`   — та же база, второй проход (шаблоны компании пропускаются).
Список баз и правил ЗАКРЫТ: чужой ключ — громкий отказ (`SetRejected`), а не «принято». Соседний
`client_chats.anon.jsonl` в список не входит намеренно: его метка — функция номера строки, и адрес по
ней ложен (замок 1 `style_examples`). Прежнее текстовое поле `source` остаётся как было — это
описание для человека, а ключ отката — `source_key`. Кейс без ключа — третий исход «источник
неизвестен», и откат по источнику его не заденет.

ОТКАТ — ОДНИМ ДЕЙСТВИЕМ ПО ИСТОЧНИКУ: `exam_set.py --rollback <ключ> --who <имя>`. Порядок записи
выбран так, чтобы обрыв на любом шаге не терял ни кейса:
  1. откачиваемые кейсы дописываются в `rolled_back.jsonl` (с версией до и штампом);
  2. `cases.json` копируется в `cases.json.bak`;
  3. новый набор пишется во временный файл и подменяет прежний атомарно (`os.replace`);
  4. событие ложится строкой в `ledger.tsv`.
Снимки откаченных кейсов остаются на диске НЕТРОНУТЫМИ — набор их больше не отдаёт
(`exam_show --live` отказывает кейсу, которого в наборе нет). Номера кейсов НЕ ПЕРЕИСПОЛЬЗУЮТСЯ
никогда: слот снимка ключуется номером (`case-N@коммит.json`), и новый кейс с номером откаченного
получил бы в «новейший слот» чужой ответ.
"""

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time

import io_utf8  # noqa: F401  — stdout/stderr в UTF-8 до первой печати кириллицы

REPO = os.path.dirname(os.path.abspath(__file__))
SET_DIR = os.path.join(REPO, "exam_live")
SET_CASES = os.path.join(SET_DIR, "cases.json")
LEDGER_NAME = "ledger.tsv"
ARCHIVE_NAME = "rolled_back.jsonl"
BAK_SUFFIX = ".bak"

LEDGER_HEADER = "время\tсобытие\tисточник\tкейсы\tверсия_до\tверсия_после\tкто"
EV_ADD, EV_SOURCE, EV_ROLLBACK = "завёл", "назвал источник", "откатил"

# ── ЗАКРЫТЫЙ СПИСОК ИСТОЧНИКОВ ────────────────────────────────────────────────────────────────
SOURCE_BASES = {"anonstable": "client_chats.anonstable.jsonl"}
RULE_STRICT, RULE_SOFT = "strict", "soft"
SOURCE_RULES = (RULE_STRICT, RULE_SOFT)
SOURCE_KEY = "source_key"
SOURCE_UNKNOWN = "источник неизвестен"
_SOURCE_RE = re.compile(r"^([a-z]+)@([0-9a-f]{16})/([a-z]+)$")

# Порог сцепки по времени — тот же, что у отбора 63-n: ответ позже суток — уже не ответ «тогда».
GAP_MAX_SEC = 24 * 3600
# Шаблон — реплика компании, дословно повторённая в стольких диалогах и больше (приём 12.09).
TEMPLATE_MIN_DIALOGS = 10
_MARK_DIALOG_RE = re.compile(r"^д(\d+)·р\d+$")


class SetRejected(ValueError):
    """Набор отказал громко: чужой ключ источника, битый корпус, кейс без текста."""


# ---------------------------------------------------------------------------------------------
# версия и источник
# ---------------------------------------------------------------------------------------------

def version(path=None):
    """Версия набора — sha256 файла, 16 hex. Файла нет — None (не пустая строка: два отсутствующих
    набора не смеют оказаться «одной версии»)."""
    try:
        with open(path or SET_CASES, "rb") as f:
            # рез: решение — версия обязана совпасть с полем `corpus` снимка, а его режет до 16 hex `exam_show.corpus_fingerprint`
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def source_key(base, base_fp, rule):
    key = "%s@%s/%s" % (base, base_fp, rule)
    validate_source(key)
    return key


def validate_source(key):
    """Ключ источника из закрытого списка → (база, отпечаток, правило). Чужой — `SetRejected`."""
    m = _SOURCE_RE.match(str(key or ""))
    if not m:
        raise SetRejected("ключ источника «%s» не по форме <база>@<16 hex>/<правило>" % key)
    base, fp, rule = m.groups()
    if base not in SOURCE_BASES:
        raise SetRejected("база «%s» не в списке источников набора (%s)"
                          % (base, ", ".join(sorted(SOURCE_BASES))))
    if rule not in SOURCE_RULES:
        raise SetRejected("правило «%s» не в списке (%s)" % (rule, ", ".join(SOURCE_RULES)))
    return base, fp, rule


def source_of(case):
    """Ключ источника кейса | SOURCE_UNKNOWN. Ключ, не прошедший закрытый список, известным не
    считается: откат по нему означал бы доверие полю, вписанному мимо двери."""
    key = (case or {}).get(SOURCE_KEY)
    try:
        validate_source(key)
    except SetRejected:
        return SOURCE_UNKNOWN
    return key


def dialog_of(case):
    """Номер диалога базы, из которого кейс, → int | None. Берётся из метки эталона (`дN·рM`)."""
    ref = (case or {}).get("reference")
    m = _MARK_DIALOG_RE.match(str((ref or {}).get("mark") or "")) if isinstance(ref, dict) else None
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------------------------
# чтение набора и следов
# ---------------------------------------------------------------------------------------------

def _side(path, name):
    return os.path.join(os.path.dirname(os.path.abspath(path or SET_CASES)), name)


def load(path=None):
    """Документ набора целиком → dict. Файла нет — пустой набор (это не ошибка: набор ещё не заведён)."""
    path = path or SET_CASES
    if not os.path.exists(path):
        return {"version": 1, "about": "", "cases": []}
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("cases"), list):
        raise SetRejected("набор %s не разобран: нет списка cases" % path)
    return doc


def ledger(path=None):
    """Строки журнала событий → list[dict]. Журнала нет — []."""
    out = []
    try:
        with open(_side(path, LEDGER_NAME), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    names = LEDGER_HEADER.split("\t")
    for line in lines[1:]:
        if line.strip():
            cells = line.split("\t") + [""] * len(names)
            out.append(dict(zip(names, cells)))
    return out


def archived(path=None):
    """Откаченные кейсы → list[dict] (строки архива)."""
    out = []
    try:
        with open(_side(path, ARCHIVE_NAME), encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
    except OSError:
        pass
    return out


def next_id(path=None, doc=None):
    """Следующий номер кейса: больше ВСЕХ, какие набор когда-либо выдавал — живых, откаченных и
    названных журналом. Номер не переиспользуется (шапка модуля)."""
    doc = doc if doc is not None else load(path)
    seen = [0]
    for c in doc["cases"]:
        seen.append(_int(c.get("id")))
    for row in archived(path):
        seen.append(_int((row.get("case") or {}).get("id")))
    for row in ledger(path):
        seen.extend(_int(x) for x in str(row.get("кейсы") or "").split(","))
    return max(seen) + 1


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def census(path=None):
    """Перепись набора → dict: версия, кейсов, по источникам, неизвестных, событий, откачено."""
    doc = load(path)
    by = collections.Counter(source_of(c) for c in doc["cases"])
    return {"version": version(path), "cases": len(doc["cases"]),
            "by_source": dict(sorted(by.items())), "unknown": by.get(SOURCE_UNKNOWN, 0),
            "ids": [c.get("id") for c in doc["cases"]],
            "events": len(ledger(path)), "archived": len(archived(path))}


# ---------------------------------------------------------------------------------------------
# запись: одна дисциплина на все события
# ---------------------------------------------------------------------------------------------

def _dump(doc):
    # Формат записи ТОТ ЖЕ, которым набор заведён 17.09 (`indent=1`, кириллица как есть, перевод
    # строки в конце): иначе первое же событие переписало бы байты нетронутых кейсов.
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def _stamp(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))


def _clean(text):
    return " ".join(str(text or "").split())


def _rewrite(doc, path, event, key, ids, who, now=None, before=None):
    """Записать набор атомарно и положить событие в журнал. → версия после."""
    path = path or SET_CASES
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if os.path.exists(path):
        shutil.copyfile(path, path + BAK_SUFFIX)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(_dump(doc))
    os.replace(tmp, path)
    after = version(path)
    led = _side(path, LEDGER_NAME)
    fresh = not os.path.exists(led)
    with open(led, "a", encoding="utf-8", newline="\n") as f:
        if fresh:
            f.write(LEDGER_HEADER + "\n")
        f.write("\t".join((_stamp(now), event, key, ",".join(str(i) for i in ids),
                           before or "", after or "", _clean(who))) + "\n")
    return after


def add(cases, key, who, path=None, now=None):
    """Завести кейсы ОДНОГО источника. → (ok, слова, номера).

    Кейс из диалога, который в наборе уже есть, не заводится второй раз (счёт назван словами).
    Номера выдаёт набор, а не вызывающий."""
    validate_source(key)
    if not _clean(who):
        return False, "автор события не назван (--who) — ничего не записано", []
    doc = load(path)
    before = version(path)
    have = set(d for d in (dialog_of(c) for c in doc["cases"]) if d is not None)
    nid = next_id(path, doc)
    ids, dup = [], 0
    for case in cases:
        if not (case.get("lines") or [""])[0].strip():
            raise SetRejected("кейс без вопроса — в набор не идёт")
        d = dialog_of(case)
        if d is not None and d in have:
            dup += 1
            continue
        c = dict(case)
        c["id"] = nid
        c[SOURCE_KEY] = key
        doc["cases"].append(c)
        ids.append(nid)
        have.add(d)
        nid += 1
    if not ids:
        return False, "заводить нечего: кандидатов %d, из них уже в наборе %d" % (len(cases), dup), []
    after = _rewrite(doc, path, EV_ADD, key, ids, who, now, before)
    return True, ("заведено %d (номера %s) из источника %s; уже были в наборе %d; версия %s → %s"
                  % (len(ids), _span(ids), key, dup, before, after)), ids


def set_source(case_id, key, who, path=None, now=None):
    """Назвать источник кейсу, у которого его НЕТ. Уже названный не переписывается. → (ok, слова)."""
    validate_source(key)
    doc = load(path)
    before = version(path)
    for c in doc["cases"]:
        if str(c.get("id")) != str(case_id):
            continue
        if source_of(c) != SOURCE_UNKNOWN:
            return False, "у кейса %s источник уже назван: %s — не переписываю" % (case_id, c[SOURCE_KEY])
        c[SOURCE_KEY] = key
        after = _rewrite(doc, path, EV_SOURCE, key, [c.get("id")], who, now, before)
        return True, "кейсу %s назван источник %s; версия %s → %s" % (case_id, key, before, after)
    return False, "кейса %s в наборе нет" % case_id


def rollback(key, who, path=None, now=None, shots_dir=None):
    """ОТКАТ ЦЕЛИКОМ ПО ИСТОЧНИКУ — одно действие. → (ok, слова, номера)."""
    validate_source(key)
    if not _clean(who):
        return False, "автор отката не назван (--who) — ничего не сделано", []
    path = path or SET_CASES
    doc = load(path)
    before = version(path)
    gone = [c for c in doc["cases"] if source_of(c) == key]
    if not gone:
        return False, "кейсов источника %s в наборе нет — откатывать нечего" % key, []
    keep = [c for c in doc["cases"] if source_of(c) != key]
    ids = [c.get("id") for c in gone]
    stamp = _stamp(now)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(_side(path, ARCHIVE_NAME), "a", encoding="utf-8", newline="\n") as f:
        for c in gone:
            f.write(json.dumps({"rolled_back_at": stamp, "source_key": key, "version_before": before,
                                "who": _clean(who), "case": c}, ensure_ascii=False) + "\n")
    doc = dict(doc, cases=keep)
    after = _rewrite(doc, path, EV_ROLLBACK, key, ids, who, now, before)
    shots = shots_dir or os.path.join(os.path.dirname(os.path.abspath(path)), "shots")
    left = _shots_of(shots, ids)
    return True, ("откачено %d (номера %s) источника %s; осталось в наборе %d; версия %s → %s; "
                  "кейсы целиком в %s; снимков откаченных на диске %d — не тронуты, набор их не отдаёт"
                  % (len(ids), _span(ids), key, len(keep), before, after, ARCHIVE_NAME, left)), ids


def _shots_of(shots_dir, ids):
    try:
        names = os.listdir(shots_dir)
    except OSError:
        return 0
    want = tuple("case-%s@" % i for i in ids) + tuple("case-%s.json" % i for i in ids)
    return sum(1 for n in names if n.startswith(want))


def _span(ids):
    return ",".join(str(i) for i in ids) if len(ids) <= 6 else "%s…%s" % (ids[0], ids[-1])


# ---------------------------------------------------------------------------------------------
# строгая сцепка: первое сообщение клиента → ответ человека (правило 63-n, строгий проход)
# ---------------------------------------------------------------------------------------------

def _ts(m):
    try:
        return datetime.datetime.fromisoformat(str(m.get("date")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def read_base(base_path):
    recs = []
    with open(base_path, encoding="utf-8") as f:
        for no, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                recs.append((no, json.loads(line)))
            except ValueError:
                recs.append((no, None))
    return recs


def templates(recs, min_dialogs=TEMPLATE_MIN_DIALOGS):
    seen = collections.defaultdict(set)
    for no, r in recs:
        if isinstance(r, dict):
            for m in r.get("messages") or []:
                if isinstance(m, dict) and m.get("who") == "company" and _clean(m.get("text")):
                    seen[_clean(m.get("text"))].add(no)
    return set(t for t, ds in seen.items() if len(ds) >= min_dialogs)


def strict_links(recs, S):
    """Строгая сцепка по всей базе → (сцепленные, причины Counter). Чистая функция от записей.

    Правила ТЕ ЖЕ, что дали 12 из 710 в 63-n: клиент открывает диалог; первое обращение — одно
    сообщение; вопрос без переноса строки, 8–400 знаков, без метки обезличивания и ссылки; первый
    ответ компании — НЕ шаблон; ответ — все подряд реплики компании, без метки и ссылки; ответ не
    раньше вопроса и не позже суток. `S` — модуль `style_examples` (пороги и регулярки оттуда)."""
    tpl = templates(recs)
    why = collections.Counter()
    out = []
    for no, r in recs:
        if not isinstance(r, dict):
            why["битая строка"] += 1
            continue
        ms = [m for m in (r.get("messages") or []) if isinstance(m, dict)]
        ci = next((i for i, m in enumerate(ms) if m.get("who") == "client"), None)
        if ci is None:
            why["нет реплики клиента"] += 1
            continue
        if ci != 0:
            why["первой пишет компания"] += 1
            continue
        j = ci
        while j < len(ms) and ms[j].get("who") == "client":
            j += 1
        if j >= len(ms):
            why["клиенту не ответили"] += 1
            continue
        if j - ci > 1:
            why["первое обращение — серия"] += 1
            continue
        q = str(ms[ci].get("text") or "").strip()
        if "\n" in q:
            why["в вопросе перенос строки"] += 1
            continue
        if not (S.Q_MIN_CHARS <= len(q) <= S.Q_MAX_CHARS):
            why["длина вопроса вне 8–400"] += 1
            continue
        if S._ANON_MARK_RE.search(q) or S._LEAK_RE.search(q) or "<" in q:
            why["в вопросе метка или ссылка"] += 1
            continue
        k = j
        while k < len(ms) and ms[k].get("who") == "company":
            k += 1
        parts = [str(ms[x].get("text") or "").strip() for x in range(j, k)]
        a = "\n".join(p for p in parts if p)
        if not a:
            why["ответ без текста"] += 1
            continue
        if _clean(parts[0]) in tpl:
            why["первый ответ — шаблон"] += 1
            continue
        if S._ANON_MARK_RE.search(a) or S._LEAK_RE.search(a):
            why["в ответе метка или ссылка"] += 1
            continue
        tq, ta = _ts(ms[ci]), _ts(ms[j])
        if tq is None or ta is None or not (0 <= ta - tq <= GAP_MAX_SEC):
            why["ответ позже суток или без времени"] += 1
            continue
        out.append({"dialog": no, "q_msg": ci, "a_msg": j, "parts": k - j, "q": q, "a": a,
                    "gap": int(ta - tq), "cyr": bool(re.search(r"[а-яё]", q, re.I))})
    return out, why


def own_dialog_hidden(link, S, index):
    """Замок своего диалога: голова не увидит ответа ЭТОГО диалога в образцах. → bool."""
    got = S.similar(link["q"], index=index)
    return not any(e.get("dialog") == link["dialog"] for e in got)


def case_from_link(link, base_name, base_fp):
    """Сцепка → кейс набора (без номера и ключа: их выдаёт `add`)."""
    return {
        "name": "живое первое сообщение (д%d, строгая сцепка)" % link["dialog"],
        "source": "%s д%d·р%d, база %s" % (base_name, link["dialog"], link["q_msg"], base_fp),
        "lang": "ru" if link["cyr"] else "en",
        "lines": [link["q"].replace("{", "{{").replace("}", "}}")],
        "expect": {},
        "reference": {"who": "менеджер", "text": link["a"],
                      "mark": "д%d·р%d" % (link["dialog"], link["a_msg"]),
                      "parts": link["parts"], "gap_sec": link["gap"], "base": base_fp},
    }


def import_strict(who, path=None, base_path=None, now=None):
    """Завести в набор все кейсы строгой сцепки, у которых стоит замок своего диалога. → (ok, слова, номера).
    Печатает и возвращает только числа и метки — текста переписки ни строкой."""
    import style_examples as S
    base_path = base_path or os.path.join(REPO, SOURCE_BASES["anonstable"])
    fp = S.fingerprint(base_path)
    if not fp:
        return False, "базы %s нет — заводить нечего" % base_path, []
    recs = read_base(base_path)
    links, why = strict_links(recs, S)
    index = S.get_index(base_path)
    hidden = [x for x in links if own_dialog_hidden(x, S, index)]
    key = source_key("anonstable", fp, RULE_STRICT)
    cases = [case_from_link(x, SOURCE_BASES["anonstable"], fp) for x in hidden]
    head = ("база %s: диалогов %d, строгая сцепка %d, замок своего диалога стоит у %d"
            % (fp, len(recs), len(links), len(hidden)))
    if not cases:
        return False, head + " — заводить нечего", []
    ok, words, ids = add(cases, key, who, path=path, now=now)
    return ok, head + "; " + words, ids


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(description="Набор обучения: перепись, завести строгую сцепку, "
                                            "назвать источник, откат по источнику.")
    p.add_argument("--census", action="store_true", help="перепись: версия, кейсы по источникам")
    p.add_argument("--import-strict", action="store_true",
                   help="завести кейсы строгой сцепки из обезличенной базы")
    p.add_argument("--set-source", nargs=2, metavar=("N", "KEY"),
                   help="назвать источник кейсу N, у которого его нет")
    p.add_argument("--rollback", metavar="KEY", help="откатить ВСЕ кейсы источника одним действием")
    p.add_argument("--who", default="", help="автор события")
    return p


def main(argv=None):
    io_utf8.force_utf8()
    a = build_parser().parse_args(argv)
    try:
        if a.import_strict:
            ok, words, _ = import_strict(a.who)
        elif a.set_source:
            ok, words = set_source(a.set_source[0], a.set_source[1], a.who)
        elif a.rollback:
            ok, words, _ = rollback(a.rollback, a.who)
        elif a.census:
            print(json.dumps(census(), ensure_ascii=False, sort_keys=True))
            return 0
        else:
            print("⛔ не назван ни один ключ действия (--census / --import-strict / --set-source / "
                  "--rollback)")
            return 2
    except SetRejected as e:
        print("⛔ %s — ничего не сделано" % e)
        return 2
    print(("✅ " if ok else "⛔ ") + words)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
