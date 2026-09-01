# -*- coding: utf-8 -*-
"""
model_name.py — ПРАВИЛО РАЗРЕШЕНИЯ ИМЕНИ МОДЕЛИ из речи клиента. Чистая функция
«текст (+ снимок парка) → вердикт»: ни сети, ни диска, ни импорта боевых модулей.

ОСНОВАНИЕ — замер дыры 01.09.2026 (`docs/artifacts/2026-09-01-разрешение-имени-модели-замер.md`).
Правило закрывает названные там классы; заново ничего не меряем, работаем от его K1…K9.

    K1 · порядок и подстрока        → сопоставление ПО ГРАНИЦАМ ТОКЕНА, длинное имя раньше короткого
    K2 · разрыв в написании          → «x-max», «X-ADV-750», «мт 03» читаются как имя
    K3 · кириллица не доезжает       → кириллические формы дают ТОТ ЖЕ вердикт, что латиница
    K4 · склонение                   → «форзу»/«форзы»/«вулкана» — те же имена, что «форза»/«вулкан»
    K5 · гомоглифы                   → «рсх» (PCX), «мт» (MT-03) — латинское имя русскими буквами
    K6 · кубатура отброшена          → названная кубатура УЧАСТВУЕТ в резолве; чужой она не станет
    K7 · развилка недостижима        → голый серийный корень («cb») теперь ДОЕЗЖАЕТ до развилки
    K8 · поколение решается молча    → два поколения = отдельный исход HUMAN, а не тихий выбор
    K9 · нечёткого сравнения нет     → опечатка вердикта не получает: НЕ УГАДЫВАЕМ (осознанно)

ЧЕТЫРЕ ИСХОДА, И НИ ОДИН ИЗ НИХ НЕ «МОЛЧАНИЕ»:

    OK              — имя разрешилось в РОВНО ОДНУ модель парка;
    HUMAN           — имя разрешается НЕОДНОЗНАЧНО → зову человека. Причина названа:
                      `two_cc` (две кубатуры одного корня в парке: CB 300R / CB 650R),
                      `generation` (два поколения одной модели: XMAX 300 / XMAX 300 New Gen),
                      `two_models` (клиент назвал несколько РАЗНЫХ моделей);
    NOT_IN_PARK     — имя понято, но такой модели в парке нет (PCX, XSR 900);
    NOT_IN_CATALOG  — корень понят, а НАЗВАННОЙ кубатуры каталог не знает вовсе (CBR 500R).

Три последних исхода — это `needs_human(v) is True`: правило НЕ подставляет вместо них соседнюю
модель и НЕ молчит. Именно тихая подстановка соседа стоила в замере −1969 ฿/сут на X-ADV → ADV 350.

ПОЧЕМУ НЕ УГАДЫВАЕМ ПРИ ОПЕЧАТКЕ (K9). Нечёткое сравнение (difflib/Levenshtein) сюда СОЗНАТЕЛЬНО
не заведено: в парке живут `CB 300R` и `CB 650R`, `ADV 150/160/350`, `PCX 150/160` — расстояние
между ними 1–2 символа, и «ближайшее» имя ошибётся ровно на тех парах, где ошибка стоит денег.
Опечатка обязана кончаться вопросом человеку, а не удачной догадкой.

ГРАНИЦЫ. Модуль НИЧЕГО не решает про цену, наличие и поколение по существу — он только НАЗЫВАЕТ
имя и НАЗЫВАЕТ развилку. Кто и как задаёт вопрос клиенту — дело вызывающего.
"""

import re

# ─────────────────────────────── каталог (зеркало suggest.KNOWN_MODELS) ──────────────────────
# ДУБЛЬ НАМЕРЕННЫЙ: модуль обязан быть чистым (его импортирует `suggest`, обратный импорт дал бы
# круг). Чтобы дубль не разъехался молча, `test_model_name` СВЕРЯЕТ обе таблицы литерал-в-литерал
# и краснеет на любом расхождении — тот же замок, что у пары ожиданий тренажёра.
CATALOG = (
    ("NMAX 155", "nmax155"), ("XMAX 300", "xmax300"), ("ADV 350", "adv350"),
    ("ADV 160", "adv160"), ("ADV 150", "adv150"), ("PCX 160", "pcx160"), ("PCX 150", "pcx150"),
    ("FORZA 300", "forza300"), ("XADV 750", "xadv750"),
    ("XSR 155", "xsr155"), ("XSR 900", "xsr900"),
    ("CBR 650R", "cbr650r"), ("CB 650R", "cb650r"), ("CB 300R", "cb300r"),
    ("REBEL 300", "rebel300"), ("MT-03", "mt03"), ("NINJA 400", "ninja400"),
    ("VULCAN 650S", "vulcan650s"), ("R7", "r7"), ("CLICK 125", "click125"),
)

# Канонический словарь имён (зеркало suggest._MODEL_TOKENS, тот же замок тестом). Нужен ровно для
# одного: НЕ менять словарь canon'ов, которым уже разговаривает боевой контур (`hints.model`,
# `requested_models`, `filter_rows_by_requested`). Порядок значим — при совпадении берём ПЕРВЫЙ,
# как брал прежний код («mt-03» раньше «mt03»).
CANON_TOKENS = (
    "nmax", "pcx", "adv350", "adv 350", "adv150", "adv 150", "adv160", "adv 160", "adv",
    "xmax", "forza", "xadv", "xsr", "cb300", "cb 300", "cb650", "cb 650",
    "cbr650", "cbr 650", "cbr", "rebel", "mt-03", "mt03", "mt 03", "ninja", "vulcan", "r7", "click",
)

# ─────────────────────────────────────── исходы ──────────────────────────────────────────────
OK = "ok"
HUMAN = "human"
NOT_IN_PARK = "not_in_park"
NOT_IN_CATALOG = "not_in_catalog"
NONE = "none"                     # имени в тексте нет вовсе
PARK_UNKNOWN = "park_unknown"     # парк недоступен — судить нечем (fail-safe, как resolve_park_model)

TWO_CC = "two_cc"
GENERATION = "generation"
TWO_MODELS = "two_models"

# Исходы, требующие ЧЕЛОВЕКА (а не тихой подстановки соседа и не молчания).
_NEEDS_HUMAN = frozenset((HUMAN, NOT_IN_PARK, NOT_IN_CATALOG))


def needs_human(verdict) -> bool:
    """Вердикт обязан кончиться вопросом человеку (не ценой и не молчанием)."""
    return bool(verdict) and verdict.get("outcome") in _NEEDS_HUMAN


# ──────────────────────────── разбор каталога на корни и кубатуры ────────────────────────────
# Ключ каталога → (корень, кубатура, хвостовая буква): «cb650r» → ('cb', 650, 'r'), «r7» → атомарный
# (корень длиной 1 именем модели быть не может — «r» матчило бы половину текста).
_KEY_RE = re.compile(r"^([a-z]+?)(\d+)([a-z]*)$")


def _split_key(key):
    m = _KEY_RE.match(key)
    if not m or len(m.group(1)) < 2:
        return key, None, ""
    return m.group(1), int(m.group(2)), m.group(3)


FAMILIES = {}          # корень → {кубатура|None: ключ каталога}
for _disp, _key in CATALOG:
    _root, _cc, _sfx = _split_key(_key)
    FAMILIES.setdefault(_root, {})[_cc] = _key

DISPLAY = {key: disp for disp, key in CATALOG}

# ───────────────────────────── формы имени: разрыв, кириллица, гомоглифы ─────────────────────
# K2. Имя пишут с разрывом («x-max», «X-ADV-750», «н-макс»), поэтому корень задан ЧАСТЯМИ, между
# которыми допустим разделитель. Части перечислены явно — «любой разрыв между любыми буквами»
# ловил бы «a d v» в произвольном тексте.
_SEP = r"[-\s._]{0,2}"
_LATIN_PARTS = {
    "nmax": ("n", "max"),
    "xmax": ("x", "max"),
    "xadv": ("x", "adv"),
}

# K5. Гомоглифы: латинская буква и её русская зрительная двойняшка — один и тот же символ на вид.
# Матчим ИМЕННО форму корня (класс на каждую букву), а не «свернём кириллицу и посмотрим, что
# вышло»: слепое сворачивание превращает русские слова в латинский мусор и рождает ложные имена.
_TWIN = {"a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о",
         "p": "р", "s": "ѕ", "t": "т", "x": "х", "y": "у", "i": "і", "j": "ј"}
# «cb» целиком набирается двойняшками («св») и этим стал бы русским сокращением. Корень короткий,
# исход у него — развилка, поэтому цена ложного срабатывания выше пользы: гомоглифы ему запрещены.
_NO_HOMOGLYPH = frozenset(("cb",))

# K3+K4. Кириллические написания (по частям — тот же разрыв, что у латиницы) и склонение:
# «форза/форзу/форзы», «вулкан/вулкана». Стем даётся БЕЗ окончания, окончание — отдельным хвостом.
_CYRILLIC = {
    "nmax": (("н", "макс"), ("эн", "макс")),
    "xmax": (("х", "макс"), ("икс", "макс")),
    "xadv": (("х", "адв"), ("икс", "адв")),
    "pcx": (("пцх",),),
    "adv": (("адв",),),
    "forza": (("форз",),),
    "mt": (("мт",),),
    "ninja": (("ниндз",), ("нинз",)),
    "vulcan": (("вулкан",),),
    "rebel": (("ребел",),),
    "click": (("клик",),),
}
# Окончание русского склонения. Перечень ЗАКРЫТЫЙ (не «любые 1–2 гласные»): открытый хвост съедал
# бы начало следующего слога и пускал «адв|окат» и «клик|ните» как имена моделей.
_CYR_END = r"(?:ой|ом|ах|ам|ов|а|у|ы|е|и|ю|я|ь)?"

# Граница токена (K1): слева и справа от имени не должно быть ни буквы, ни цифры. Именно её
# отсутствие делало «adv» подстрокой «xadv» и «advise».
_LEFT = r"(?<![a-z0-9а-яё])"
_RIGHT = r"(?![a-z0-9а-яё])"
# Хвост после кубатуры: единица объёма («160cc», «300 кубов») либо буква модели («500r», «650s»).
_CC_TAIL = r"(?:\s*(?:cc|сс|см3|см³|куб\w*)|(?P<sfx>[rsрѕ]))?"


def _cc_pattern(root):
    """Что за корнем СЧИТАЕТСЯ кубатурой. НЕ «любые цифры»: за именем модели в живой речи стоят
    ещё даты и сроки («nmax 10 июля», «xmax на 5 дней»), и съесть их как кубатуру значило бы
    выдумать модель «NMAX 10». Берём ровно два вида: кубатуры, которые каталог знает для ЭТОГО
    корня (включая «03» у MT-03), и любое трёхзначное число — трёхзначной кубатура и бывает
    (500 у CBR 500R из замера, 160, 750, 900). Числа дат и сроков в парке двузначны и короче."""
    known = sorted({re.sub(r"\D", "", k) for k in (FAMILIES.get(root) or {}).values()
                    if re.search(r"\d", k)}, key=len, reverse=True)
    return "(?:%s)" % "|".join([re.escape(x) for x in known if len(x) != 3] + [r"\d{3}"])


def _homoglyph_part(part):
    """Латинская часть корня → шаблон, где каждая буква = она сама ИЛИ её зрительная двойняшка."""
    return "".join(("[%s%s]" % (ch, _TWIN[ch]) if ch in _TWIN else re.escape(ch)) for ch in part)


def _form_pattern(parts, latin):
    """Части корня → шаблон с допустимым разрывом между ними."""
    render = _homoglyph_part if latin else (lambda p: re.escape(p))
    return _SEP.join(render(p) for p in parts)


def _root_forms(root):
    """Все письменные формы корня: латиница (с гомоглифами) + кириллица (со склонением).
    → список (шаблон, длина_эталона) — длина нужна для «длинное имя раньше короткого»."""
    forms = []
    parts = _LATIN_PARTS.get(root, (root,))
    latin = _form_pattern(parts, latin=(root not in _NO_HOMOGLYPH))
    forms.append(latin)
    for cyr_parts in _CYRILLIC.get(root, ()):
        forms.append(_form_pattern(cyr_parts, latin=False) + _CYR_END)
    return forms


def _compile_root(root):
    body = "|".join("(?:%s)" % f for f in sorted(_root_forms(root), key=len, reverse=True))
    return re.compile("%s(?:%s)(?:%s(?P<cc>%s)%s)?%s"
                      % (_LEFT, body, _SEP, _cc_pattern(root), _CC_TAIL, _RIGHT), re.IGNORECASE)


# Корни отсортированы по убыванию длины — ДЛИННОЕ ИМЯ РАНЬШЕ КОРОТКОГО (K1). Порядок здесь
# страхует, а решает всё равно длина реального совпадения (см. _pick_non_overlapping).
ROOTS = tuple(sorted(FAMILIES, key=len, reverse=True))
_ROOT_RE = {root: _compile_root(root) for root in ROOTS}


# ────────────────────────────────── поиск имён в тексте ──────────────────────────────────────

def _pick_non_overlapping(found):
    """Из всех совпадений оставить непересекающиеся, предпочитая ДЛИННОЕ (K1: «xadv750» съедает
    любое «adv» внутри себя), при равной длине — ЛЕВОЕ. → отсортированный по позиции список."""
    kept = []
    for m in sorted(found, key=lambda x: (-(x["end"] - x["start"]), x["start"])):
        if any(m["start"] < k["end"] and k["start"] < m["end"] for k in kept):
            continue
        kept.append(m)
    return sorted(kept, key=lambda x: x["start"])


def find_mentions(text):
    """Все упоминания модели в тексте → список dict(root, cc, cc_text, sfx, start, end, raw),
    В ПОРЯДКЕ ПОЯВЛЕНИЯ В ТЕКСТЕ (а не в порядке словаря — именно порядок словаря подставлял
    NMAX 155 на рекламный список из 16 моделей, замер §3)."""
    t = str(text or "").lower()
    if not t.strip():
        return []
    found = []
    for root in ROOTS:
        for m in _ROOT_RE[root].finditer(t):
            cc_text = m.group("cc")
            found.append({"root": root,
                          "cc": int(cc_text) if cc_text else None,
                          "cc_text": cc_text or "",
                          "sfx": (m.group("sfx") or ""),
                          "start": m.start(), "end": m.end(), "raw": m.group(0)})
    return _pick_non_overlapping(found)


# ──────────────────────────────────────── canon ──────────────────────────────────────────────
def _flat(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _token_canon(flat_want):
    """Первый канонический токен с такой же «плоской» формой → canon боевого формата
    («mt03» → 'MT-03'). Нет такого токена → None."""
    for tok in CANON_TOKENS:
        if _flat(tok) == flat_want:
            return tok.upper().replace(" ", "")
    return None


def _bare_canon(root):
    """Canon голого корня. Своего токена у корня может не быть («mt» — в словаре только «mt-03»):
    у однозначного семейства берём токен его ЕДИНСТВЕННОЙ модели, у развилки («cb») — сам корень,
    потому что именно голый корень и обязан доехать до развилки (K7)."""
    bare = _token_canon(root)
    if bare:
        return bare
    fam = FAMILIES.get(root) or {}
    if len(fam) == 1:
        only = _token_canon(_flat(list(fam.values())[0]))
        if only:
            return only
    return root.upper()


def canon_for(mention, compat=False):
    """Упоминание → canon в ТОМ ЖЕ словаре, которым говорит боевой контур.

    `compat=True` — путь СЕТКИ (`extract_requested_models`): при неизвестном канону сочетании
    корня и кубатуры отдаём голый корень, как отдавал прежний код. Сетка фильтрует строки: шире
    показать — не соврать. `compat=False` — путь ЦЕНЫ (`_detect_model`): здесь голый корень
    означал бы, что названная клиентом кубатура ОТБРОШЕНА (K6), поэтому она остаётся в canon'е и
    цена скорее не назовётся вовсе, чем назовётся чужая."""
    root, cc, cc_text, sfx = mention["root"], mention["cc"], mention["cc_text"], mention["sfx"]
    if cc_text:
        exact = _token_canon(root + cc_text)
        if exact:
            return exact
    bare = _bare_canon(root)
    if not cc_text or compat:
        return bare
    fam = FAMILIES.get(root) or {}
    if cc in fam and len(fam) <= 1:
        return bare                       # семейство одно — кубатура ничего не выбирает
    return (root + cc_text + sfx).upper()


# ──────────────────────────────────────── резолв ─────────────────────────────────────────────
def _verdict(outcome, reason="", key=None, options=(), canon="", mentions=()):
    return {"outcome": outcome, "reason": reason, "key": key,
            "model": DISPLAY.get(key) if key else None,
            "options": list(options), "canon": canon, "mentions": list(mentions)}


def resolve_mention(mention, present=None, generations=None, gen_cue=False, compat=False):
    """Одно упоминание → вердикт. `present` — ключи каталога, РЕАЛЬНО стоящие в парке (None = парк
    недоступен); `generations` — ключ → список меток поколений этой модели в парке;
    `gen_cue` — клиент САМ назвал поколение (тогда развилки поколений нет)."""
    root = mention["root"]
    cc = mention["cc"]
    fam = FAMILIES.get(root) or {}
    canon = canon_for(mention, compat=compat)
    if cc is None:
        keys = [fam[c] for c in sorted(fam, key=lambda x: (x is None, x))]
    elif cc in fam:
        keys = [fam[cc]]
    else:
        # K6. Названа кубатура, которой у этого корня НЕТ («CBR 500R»). Прежний код откатывался на
        # корень и отдавал CBR 650R — чужую модель по цене вдвое. Откат запрещён.
        return _verdict(NOT_IN_CATALOG, reason="cc", canon=canon, mentions=[mention],
                        options=[DISPLAY[k] for k in fam.values()])
    if present is None:
        return _verdict(PARK_UNKNOWN, canon=canon, mentions=[mention])
    avail = [k for k in keys if k in present]
    if not avail:
        return _verdict(NOT_IN_PARK, reason="park", canon=canon, mentions=[mention],
                        options=[DISPLAY[k] for k in keys])
    if len(avail) > 1:
        # K7. Две кубатуры одного корня в парке (CB 300R / CB 650R) — НЕ угадываем.
        return _verdict(HUMAN, reason=TWO_CC, canon=canon, mentions=[mention],
                        options=[DISPLAY[k] for k in avail])
    key = avail[0]
    gens = list((generations or {}).get(key) or ())
    if len(gens) > 1 and not gen_cue:
        # K8. Два поколения одной модели с разными тарифами — тоже развилка, а не тихий выбор.
        return _verdict(HUMAN, reason=GENERATION, key=key, canon=canon,
                        options=gens, mentions=[mention])
    return _verdict(OK, key=key, canon=canon, mentions=[mention])


def resolve(text, present=None, generations=None, gen_cue=False, compat=False):
    """Текст клиента → ОДИН вердикт (см. шапку модуля). Несколько РАЗНЫХ моделей в одной реплике —
    это тоже развилка (`two_models`): прежний код брал первую по порядку СЛОВАРЯ и на рекламном
    списке из 16 моделей объявлял выбором клиента NMAX 155."""
    mentions = find_mentions(text)
    if not mentions:
        return _verdict(NONE)
    per = [resolve_mention(m, present, generations, gen_cue, compat) for m in mentions]
    seen, uniq = set(), []
    for m, v in zip(mentions, per):
        sig = (m["root"], m["cc"])
        if sig not in seen:
            seen.add(sig)
            uniq.append(v)
    if len(uniq) > 1:
        opts = [v["model"] or v["canon"] for v in uniq]
        return _verdict(HUMAN, reason=TWO_MODELS, options=opts,
                        canon=uniq[0]["canon"], mentions=mentions)
    return per[0]
