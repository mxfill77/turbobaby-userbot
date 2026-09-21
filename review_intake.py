"""Ступень B внешнего ревью-контура — ОТВЕТ КАНАЛА → ЗАЯВКИ В ОЧЕРЕДИ.

Ступень A (:mod:`review_auto`) сама находит повод, собирает пакет второго мнения
и относит его в канал; ответ канала ложится файлом в лоток ``docs/review_inbox``
и адресован ЧЕЛОВЕКУ. На этом контур обрывался: находки внешнего ревьюера жили
текстом в лотке, и единственным способом что-то с ними сделать было прочитать
файл глазами. Ступень B добавляет ровно одно звено — находка становится
**ЗАЯВКОЙ в очереди** — и НИ ОДНОГО звена больше.

ЧТО ТАКОЕ ЗАЯВКА И ЧЕМ ОНА НЕ ЯВЛЯЕТСЯ. Заявка — ряд очереди, который ЖДЁТ
РЕШЕНИЯ ЧЕЛОВЕКА и не делает ничего. Она не исполняется, не порождает задачу, не
трогает код, не меняет операционного состояния. «Да» на карточке заявки означает
«принято к сведению», а НЕ «выполняй»: задачу по заявке ставит человек отдельной
постановкой. Это не осторожность ради осторожности — ответ внешнего канала
приходит СВОБОДНЫМ ТЕКСТОМ ИЗВНЕ, и путь «чужой текст → зелёная задача дирижёру»
был бы каналом исполнения чужих команд на этой машине. Замок стоит в трёх местах
сразу (гарды ``process_new``/``process_approved``/``process_approval_timeouts``
в демоне), а здесь — его словесная половина: маркер :data:`CLAIM_MARK`, по
которому эти гарды заявку и опознают.

ЧЕТЫРЕ ВЕЩИ, КОТОРЫЕ ЭТОТ МОДУЛЬ РЕШАЕТ (и ни одной сверх):

1. **Разбор ответа на отдельные находки.** Ответ канала — один текст; находок в
   нём столько, сколько вопросов в хвосте пакета. Каждая находка несёт ИСТОЧНИК
   (пакет), КАНАЛ, ДАТУ отправки, SHA256 пакета и ДОСЛОВНУЮ цитату. Дословность
   тут не украшение: заявка — это чужое мнение, и пересказ своими словами сделал
   бы её нашим.
2. **Склейка дублей ПО СМЫСЛУ.** Одна и та же находка, приехавшая двумя каналами
   (или двумя пакетами), обязана стать ОДНОЙ заявкой с ДВУМЯ источниками —
   иначе владелец решает один вопрос дважды. Мера склейки названа числом
   (:data:`MERGE_JACCARD`) и измерена на живом корпусе, а не назначена на глаз.
   FAIL-CLOSED: не уверены — НЕ склеиваем. Лишняя заявка стоит одной карточки, а
   ошибочная склейка тихо ХОРОНИТ вторую находку.
3. **Исход премисы: ЖИВА / ПРОТУХЛА / НЕИЗВЕСТНО.** Находка стоит на посылке —
   «в коде есть вот это». Ответ приходит с задержкой, и к моменту чтения посылка
   может быть уже неверна. Проверяется она ПО НАЗВАННОМУ АДРЕСУ (что именно
   искали и где нашли), а НЕ по времени: «ответ свежий, значит верен» — это не
   проверка, а гадание по часам. Третий исход обязателен ровно по той же
   причине, что и в слое ожиданий: «проверить не смог» никогда не превращается в
   «проверено и хорошо».
4. **Текст заявки.** Один ряд очереди, из которого человеку видно всё: вид
   находки, исход премисы с адресом, все источники и дословные цитаты.

ИНВАРИАНТЫ (те же, что у ступеней 1–2, A и слоя ожиданий, и по той же причине):

* **Чистота.** Ни сети, ни диска, ни подпроцессов, ни ``getenv``, ни часов.
  «Сегодня» и результаты проб — ПОЛЯ ВХОДА, а не ``datetime.now()`` и не
  ``open()``. Держит тест ``REVIEW_INTAKE_PURE`` обходом AST.
* **Ничего не выдумывать.** Ни вид находки, ни исход премисы не берутся из
  догадки: вид — из хвоста пакета (:data:`review_pack.TAIL_QUESTIONS`, один
  источник правды), премиса — из проб по названному адресу.
* **Разбор не смеет молча дать НОЛЬ находок.** Ответ, который не разобрался на
  пункты, становится ОДНОЙ находкой целиком с честной пометкой ``unsplit``:
  потерянная находка хуже неудобной.

ЧЕГО ЭТОТ МОДУЛЬ НЕ ДЕЛАЕТ НИ ОДНОЙ ВЕТКОЙ: не ставит задач, не исполняет
находок, не правит код, не ходит в очередь и не читает файлов. Всё это — руки
(:mod:`review_intake_run`), и даже там постановка заявки кончается статусом
«ждёт человека».
"""

from __future__ import annotations

import datetime
import hashlib
import re

import review_pack
import review_send

SCHEMA = "turbobaby.review_intake/v1"

# ───────────────────────────── маркер заявки ─────────────────────────────
# Форма списана с маркера задачи-находки ревизора («[ревизор дата=… класс=…]») и
# по той же причине: и БЮДЖЕТ, и ДЕДУП обязаны быть restart-proof, то есть
# читаться из САМОЙ ОЧЕРЕДИ, а не из памяти процесса. Слово в маркере — «заявка»,
# а не «задача», и это единственное, чем гарды демона отличают её от работы.
CLAIM_MARK = "[заявка-ревью"
_RE_CLAIM = re.compile(r"^\[заявка-ревью дата=(\d{4}-\d{2}-\d{2}) ключ=([0-9a-f]{6,32})\]")

# Шапка заявки. Первая строка — то, что человек читает первым, и она обязана
# сказать «НЕ задача» раньше, чем содержание находки. Ровно как «НЕ ИСПОЛНЯТЬ»
# первой строкой файла ответа на ступени 2.
CLAIM_HEAD = (
    "📥 ЗАЯВКА ВНЕШНЕГО КАНАЛА — НЕ ЗАДАЧА. Ничего не исполняет и в работу не уходит: "
    "задачу по ней ставит ЧЕЛОВЕК отдельной постановкой. «Да» здесь = «принято к сведению»."
)

PREMISE_ALIVE = "alive"
PREMISE_STALE = "stale"
PREMISE_UNKNOWN = "unknown"
PREMISE_TITLE = {
    PREMISE_ALIVE: "ЖИВА",
    PREMISE_STALE: "ПРОТУХЛА",
    PREMISE_UNKNOWN: "НЕИЗВЕСТНО",
}

# Каталоги, где адрес ЗАМОРОЖЕН: лотки ответов/пакетов, расписки и артефакты не
# меняются никогда. Найти якорь там — не то же самое, что найти его в живом коде:
# замороженная копия верна вечно и о сегодняшнем дне не говорит НИЧЕГО. Отдельный
# исход для этого случая — не педантизм: без него любая находка про собственный
# пакет вечно числилась бы «живой», то есть проверка вырождалась бы в самоответ.
FROZEN_DIRS = ("docs/review_outbox", "docs/review_inbox", "docs/review_receipts",
               "docs/artifacts")

# Якорь короче этого — не адрес, а шум: `ok`, `id`, `sha` найдутся в любом файле
# репозитория и превратят «премиса жива» в бессмысленное «буквы существуют».
ANCHOR_MIN_CHARS = 4
ANCHOR_MAX = 6                # больше шести адресов на находку не проверяем (цена пробы)
# ОДНО СЛОВО В «ЁЛОЧКАХ» — ИНТОНАЦИЯ, А НЕ АДРЕС. Пойман живьём на первом же
# прогоне корпуса: находка про «два параллельных счёта «побед»» дала якорь
# «побед», тот нашёлся в `moderation_card.py` — и премиса объявилась ЖИВОЙ по
# слову из чужого файла. В русском тексте одиночные ёлочки чаще кавычки-ирония,
# чем цитата; адресом считаем ФРАЗУ (≥2 слов). Обратные кавычки под это правило
# не идут: там пишут имена и пути, и одиночное имя — как раз адрес.
PHRASE_MIN_WORDS = 2

# Мера склейки — ИЗМЕРЕННЫЙ РАЗРЫВ живого корпуса, а не круглое число на глаз.
# Замер 01.09 по лотку docs/review_inbox (9 находок, 36 пар, основа слова 5):
#   • истинный дубль (одна находка двумя пакетами) — 0.173;
#   • ближайший ЛОЖНЫЙ кандидат того же вида      — 0.117;
#   • порог 0.145 — центр этого разрыва.
# Честная оговорка, которую нельзя опускать: истинная пара в корпусе ОДНА, и
# число названо разрывом на живых данных, а не доказанной границей класса. Цена
# промаха асимметрична и выбрана осознанно (см. :func:`same_meaning`): порог
# лучше держать ВЫШЕ, чем ниже.
MERGE_JACCARD = 0.145
STEM_LEN = 5                  # основа слова: «заявлено»/«заявлять» → «заявл»
WORD_MIN_CHARS = 4            # слова короче — служебные, в меру смысла не идут

QUOTE_MAX = 900               # дословная цитата в тексте заявки (полная — в файле ответа)
CLAIM_TEXT_MAX = 4400         # потолок ряда очереди (RESULT_MAX демона — 4500)

_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_HEAD = re.compile(r"^#\s*ОТВЕТ ВНЕШНЕГО РЕВЬЮЕРА\s*—\s*(.+?)\s*·\s*канал\s+(\S+)\s*$", re.M)
_RE_OUTCOME = re.compile(r"^исход:\s*\*\*(.+?)\*\*\s*\(`([a-z_]+)`\)\s*$", re.M)
_RE_REASON = re.compile(r"^причина:\s*`([^`]*)`\s*$", re.M)
_RE_SENT = re.compile(r"^отправлено:\s*\*\*(\d{4}-\d{2}-\d{2})\*\*\s*·\s*канал:\s*\*\*(\S+?)\*\*\s*$", re.M)
_RE_PACK = re.compile(r"^пакет:\s*`([^`]*)`\s*$", re.M)
# Задача канала. Поля может не быть вовсе — файлы лотка старше 21.09.2026 писаны
# рендером без него, и «нет строки» здесь значит «не знаем», а не «задачи не было».
_RE_TASK = re.compile(r"^задача канала:\s*`([^`]*)`\s*$", re.M)
_RE_PACK_SHA = re.compile(r"^sha256 пакета:\s*`([^`]*)`\s*$", re.M)
_RE_ANSWER_SHA = re.compile(r"^ответ:\s*(\d+)\s*знаков\s*·\s*sha256\s*`([^`]*)`\s*$", re.M)
_RE_BODY_HEAD = re.compile(r"^##\s*ОТВЕТ КАНАЛА \(дословно\)\s*$", re.M)
_RE_FENCE = re.compile(r"^(`{3,})\s*$")

# Пункт ответа: «1. …» / «2) …». Пробелов слева терпим до трёх — канал иногда
# выравнивает список. Больше — уже вложенный список внутри пункта, и дробить его
# значило бы рвать одну находку на куски.
_RE_ITEM = re.compile(r"^ {0,3}(\d{1,2})[.)]\s+(.*)$")
_RE_BULLET = re.compile(r"^ {0,3}[-*•]\s+(.*)$")

# Якоря — то, что находка НАЗВАЛА адресом сама: имя в обратных кавычках либо
# фраза в «ёлочках». Ничего третьего: угадывать за ревьюера, что он «имел в виду»,
# — это и есть подмена проверки догадкой.
_RE_BACKTICK = re.compile(r"`([^`\n]+)`")
_RE_QUOTED = re.compile(r"«([^»\n]+)»")
# Ссылка на номер очереди (#77) адресом НЕ является: номера переиспользуются
# очередью (живая строка лога 01.09), и проверять по ним нечего.
_RE_REF = re.compile(r"#(\d{1,5})\b")
_RE_PATHISH = re.compile(r"^[\w./\\-]+\.(?:py|md|json|txt|gs|jsonl)$")
_RE_WORD = re.compile(r"[0-9a-zA-Zа-яёА-ЯЁ_]+")


class ReviewIntakeError(Exception):
    """Отказ ступени B с НАЗВАННОЙ причиной (зеркало :class:`review_send.ReviewSendError`)."""

    def __init__(self, reason, detail=None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# ───────────────────────────── виды находок ─────────────────────────────


def kind_labels():
    """Виды находок — ИЗ ХВОСТА ПАКЕТА, а не из списка, набранного здесь руками.

    Пакет спрашивает ровно три вещи (:data:`review_pack.TAIL_QUESTIONS`), конверт
    ступени 2 требует ответить «по пунктам 1, 2, 3», и канал отвечает тремя
    пунктами. Значит вид находки — это НОМЕР ВОПРОСА, а не слово, угаданное в
    тексте. Список видов, набранный здесь литералами, разошёлся бы с вопросами на
    первой же их правке и молча начал бы врать — поэтому он выводится.

    → tuple заглавных ярлыков вопросов ('УПРОЩАЕМО', 'НЕ ДЕЛАТЬ ВОВСЕ', …).
    """
    out = []
    for question in review_pack.TAIL_QUESTIONS:
        runs = re.findall(r"[А-ЯЁ]{2,}(?:\s+[А-ЯЁ]{2,})*", question)
        out.append(max(runs, key=len) if runs else question.split(":")[0].strip())
    return tuple(out)


def _norm_kind(text):
    return re.sub(r"\s+", " ", (text or "").replace("Ё", "Е").strip()).upper()


def kind_of(quote, index):
    """Вид находки: сначала СЛОВО самого канала, потом номер пункта. → str.

    Порядок именно такой, а не обратный. Слово («ПЕРЕУСЛОЖНЕНО:») сказал сам
    ревьюер — оно старше нашей нумерации; номер работает там, где слова нет.
    Не выводится ни то ни другое → 'иное', и это честный ответ: приписать
    находке чужой вид значило бы отсортировать её в разряд, о котором никто не
    говорил.
    """
    labels = kind_labels()
    head = _norm_kind((quote or "").split(":")[0][:60])
    for label in labels:
        if _norm_kind(label) in head:
            return label
    if isinstance(index, int) and 1 <= index <= len(labels):
        return labels[index - 1]
    return "иное"


# ───────────────────────────── разбор ответа ─────────────────────────────


def parse_answer(text):
    """Файл ответа канала (:func:`review_send.render_answer`) → поля + тело. → dict.

    Разбор идёт по ТЕМ ЖЕ меткам, которыми файл написан, — и регресс это
    доказывает не верой, а прогоном: голден снят с ЖИВЫХ файлов лотка, а сверх
    того тест гоняет круг «render_answer → parse_answer» на сгенерированном
    вердикте. Правило-класс полосы: формат мока = формат источника; здесь
    источник — сам живой рендер.

    Ключ ``ok`` отвечает на один вопрос: есть ли здесь находки вообще. Отказ
    канала (``refused``) находок не несёт — и это НЕ ошибка разбора, а исход:
    причина отказа переносится в ``why`` дословно.
    """
    if not isinstance(text, str) or not text.strip():
        raise ReviewIntakeError("empty_answer", "answer text must be a non-empty str")
    head = _RE_HEAD.search(text)
    outcome = _RE_OUTCOME.search(text)
    sent = _RE_SENT.search(text)
    if not (head and outcome and sent):
        raise ReviewIntakeError(
            "not_an_answer_file",
            "нет обязательных меток файла ответа (заголовок / исход / отправлено)")
    pack = _RE_PACK.search(text)
    pack_sha = _RE_PACK_SHA.search(text)
    reason = _RE_REASON.search(text)
    answer = _RE_ANSWER_SHA.search(text)
    task = _RE_TASK.search(text)
    task_id = (task.group(1) if task else "").strip()
    if task_id in ("—", "-"):
        # Прочерк рендера — это «канал задачи не заводил», и пустая строка здесь
        # честнее прочерка: дальше по пути идентификатор либо есть, либо его нет.
        task_id = ""
    out = {
        "schema": SCHEMA,
        "pack": head.group(1).strip(),
        "channel": sent.group(2).strip(),
        "send_date": sent.group(1),
        "outcome": outcome.group(2),
        "outcome_title": outcome.group(1).strip(),
        "reason": (reason.group(1) if reason else "") or "",
        "pack_path": (pack.group(1) if pack else "") or "",
        "pack_sha256": (pack_sha.group(1) if pack_sha else "") or "",
        "task_id": task_id,
        "answer_chars": int(answer.group(1)) if answer else 0,
        "answer_sha256": (answer.group(2) if answer else "") or "",
        "body": "",
        "ok": False,
        "why": "",
    }
    if out["channel"] != head.group(2).strip():
        # Две метки канала в файле (заголовок и строка отправки) обязаны совпасть.
        # Расхождение означает, что файл склеен из двух разных заходов, и брать
        # из него источник заявки нельзя: заявка соврала бы про канал.
        raise ReviewIntakeError("channel_mismatch",
                                "канал в заголовке (%s) не равен каналу в строке отправки (%s)"
                                % (head.group(2).strip(), out["channel"]))
    body = _body(text)
    out["body"] = body
    if out["outcome"] != "answered":
        out["why"] = "исход канала «%s» (%s) — находок в файле нет" % (
            out["outcome_title"], out["reason"] or out["outcome"])
        return out
    if not body.strip():
        out["why"] = "исход «ответ получен», но тела ответа в файле нет"
        return out
    out["ok"] = True
    return out


def _body(text):
    """Дословное тело ответа из-под ограды. → str ('' — тела нет).

    Ограда переменной длины (:func:`review_send._fence` берёт её на знак длиннее
    самой длинной серии в теле), поэтому закрывающей считается строка РОВНО ТОЙ ЖЕ
    длины: искать первые попавшиеся три кавычки значило бы обрезать тело на первом
    же куске кода внутри ответа.
    """
    head = _RE_BODY_HEAD.search(text or "")
    if not head:
        return ""
    lines = text[head.end():].splitlines()
    fence = None
    body = []
    for line in lines:
        match = _RE_FENCE.match(line)
        if fence is None:
            if match:
                fence = match.group(1)
            continue
        if match and match.group(1) == fence:
            break
        body.append(line)
    return "\n".join(body).strip("\n")


def split_findings(body):
    """Тело ответа → отдельные находки. → list[dict(index, kind, quote, unsplit)].

    Цитата остаётся ДОСЛОВНОЙ вместе с номером пункта: это текст канала, и любое
    наше причёсывание превращает чужое мнение в наш пересказ.

    НОЛЯ НАХОДОК ЗДЕСЬ НЕ БЫВАЕТ. Ответ, не разобравшийся на пункты, становится
    ОДНОЙ находкой целиком с пометкой ``unsplit`` — потому что молчание разбора
    неотличимо от «канал не нашёл ничего», а это разные новости.
    """
    text = (body or "").strip("\n")
    if not text.strip():
        return []
    items = _split_by(text, _RE_ITEM, numbered=True)
    if not items:
        items = _split_by(text, _RE_BULLET, numbered=False)
    if not items:
        return [{"index": 1, "kind": kind_of(text, None), "quote": text.strip(),
                 "unsplit": True}]
    out = []
    for i, (num, chunk) in enumerate(items, 1):
        quote = chunk.strip()
        if not quote:
            continue
        out.append({"index": num if num is not None else i,
                    "kind": kind_of(quote, num if num is not None else i),
                    "quote": quote, "unsplit": False})
    return out


def _split_by(text, rx, numbered):
    """Разрезать тело по началам пунктов. → list[(номер|None, кусок)]."""
    starts = []
    for i, line in enumerate(text.splitlines()):
        match = rx.match(line)
        if match:
            starts.append((i, int(match.group(1)) if numbered else None))
    if not starts:
        return []
    lines = text.splitlines()
    out = []
    for pos, (start, num) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        out.append((num, "\n".join(lines[start:end])))
    return out


# ───────────────────────────── адреса находки ─────────────────────────────


def anchors(quote):
    """Адреса, НАЗВАННЫЕ самой находкой. → list[str] (порядок сохранён, дедуп).

    Берём ровно две формы: имя в обратных кавычках (``result_head``, путь, ключ
    словаря) и фразу в «ёлочках» (дословная строка, на которую находка жалуется).
    Обе — то, что ревьюер НАЗВАЛ сам. Всё остальное — проза; вылавливать адреса
    из прозы значило бы проверять премису по собственной догадке, а не по слову
    источника, и «премиса жива» становилось бы утверждением о нашей фантазии.

    Номера очереди (``#77``) адресами не считаются: очередь номера
    ПЕРЕИСПОЛЬЗУЕТ, и проверять по ним нечего (см. :func:`refs`).
    """
    out, seen = [], set()
    for rx, phrase in ((_RE_BACKTICK, False), (_RE_QUOTED, True)):
        for match in rx.finditer(quote or ""):
            for token in _anchor_tokens(match.group(1), phrase=phrase):
                if token not in seen:
                    seen.add(token)
                    out.append(token)
    return out[:ANCHOR_MAX]


def _anchor_tokens(raw, phrase=False):
    """Содержимое кавычек → якоря. Составное («class`, `id») уже разрезано регуляркой.

    ``phrase=True`` — содержимое «ёлочек»: адресом считается только ФРАЗА
    (:data:`PHRASE_MIN_WORDS` слов и больше), см. разбор у константы.
    """
    token = (raw or "").strip().strip(".,;:!?")
    if not token:
        return []
    if _RE_REF.fullmatch(token):
        return []
    if phrase:
        return ([token] if len(token.split()) >= PHRASE_MIN_WORDS
                and len(token) >= ANCHOR_MIN_CHARS else [])
    # «change_reason: commit_verified» — два имени в одной обёртке. Режем по
    # разделителям пары, но НЕ по пробелу: составное имя ищется целиком.
    parts = re.split(r"[:=,]\s*", token)
    out = []
    for part in parts:
        part = part.strip().strip("`\"'.,;:")
        if len(part) >= ANCHOR_MIN_CHARS and not _RE_REF.fullmatch(part):
            out.append(part)
    return out


def refs(quote):
    """Номера, названные находкой (#77). → list[str]. НЕ адреса — только показания."""
    seen, out = set(), []
    for match in _RE_REF.finditer(quote or ""):
        num = match.group(1)
        if num not in seen:
            seen.add(num)
            out.append(num)
    return out


def anchor_form(anchor):
    """Форма якоря: 'path' (ищем файл) | 'literal' (ищем строку в дереве). → str."""
    return "path" if _RE_PATHISH.match(anchor or "") else "literal"


def probe_plan(claim):
    """Что именно проверять у заявки. → list[dict(anchor, form)].

    План строится ЗДЕСЬ, а исполняют его руки: решение «какой адрес считается
    названным» обязано быть воспроизводимым без диска.
    """
    out, seen = [], set()
    for source in claim.get("sources") or []:
        for anchor in anchors(source.get("quote")):
            if anchor in seen:
                continue
            seen.add(anchor)
            out.append({"anchor": anchor, "form": anchor_form(anchor)})
    return out[:ANCHOR_MAX]


def frozen_address(address):
    """Адрес — замороженная копия (лоток/расписка/артефакт)? → bool."""
    norm = str(address or "").replace("\\", "/").lstrip("./")
    return any(norm.startswith(d + "/") for d in FROZEN_DIRS)


def premise(probes):
    """Пробы по названным адресам → исход премисы. → dict(outcome, why, address).

    ТРИ ИСХОДА, И ТРЕТИЙ ОБЯЗАТЕЛЕН — та же доктрина, что в слое ожиданий: ни
    одна ветка не превращает «проверить не смог» в «проверено и хорошо».

    * ЖИВА — якорь найден в ЖИВОМ файле дерева (не в замороженной копии).
    * ПРОТУХЛА — все названные адреса проверены, и ни по одному якоря нет:
      находка говорит о том, чего в дереве больше нет.
    * НЕИЗВЕСТНО — адрес не назван вовсе; проба не удалась; либо якорь нашёлся
      ТОЛЬКО в замороженном адресе. Последнее — не педантизм: артефакт и лоток
      неизменны, и «нашлось в собственном пакете» о живом коде не говорит ничего.

    Ни один исход не выводится из ВОЗРАСТА ответа. Свежесть — не проверка.
    """
    probes = [p for p in (probes or []) if isinstance(p, dict)]
    if not probes:
        return {"outcome": PREMISE_UNKNOWN, "address": "",
                "why": "адрес не назван находкой — проверять нечего (не по времени судим)"}
    live = [p for p in probes if p.get("found") is True and not p.get("frozen")]
    if live:
        best = live[0]
        return {"outcome": PREMISE_ALIVE, "address": best.get("address") or "",
                "why": "`%s` найден по адресу %s" % (best.get("anchor"), best.get("address"))}
    frozen = [p for p in probes if p.get("found") is True]
    if frozen:
        best = frozen[0]
        return {"outcome": PREMISE_UNKNOWN, "address": best.get("address") or "",
                "why": "`%s` нашёлся ТОЛЬКО в замороженном адресе %s — копия неизменна, "
                       "о живом коде не говорит" % (best.get("anchor"), best.get("address"))}
    unknown = [p for p in probes if p.get("found") is None]
    if unknown:
        best = unknown[0]
        return {"outcome": PREMISE_UNKNOWN, "address": best.get("address") or "",
                "why": "проба по `%s` не удалась: %s" % (best.get("anchor"),
                                                         best.get("detail") or "причина не названа")}
    names = ", ".join("`%s`" % p.get("anchor") for p in probes[:3])
    return {"outcome": PREMISE_STALE, "address": probes[0].get("address") or "",
            "why": "названного нет в дереве (искали %s по %d адресам)" % (names, len(probes))}


# ───────────────────────────── смысл и склейка ─────────────────────────────


def meaning_words(text):
    """Мера смысла находки — множество основ значимых слов. → frozenset.

    Основа (первые :data:`STEM_LEN` знаков) вместо целого слова — потому что
    русский текст склоняется: «заявлено» и «заявлять» об одном, а как строки они
    разные. Слова короче :data:`WORD_MIN_CHARS` выброшены: предлоги и союзы есть
    в любом тексте и склеили бы что угодно с чем угодно.
    """
    out = set()
    for match in _RE_WORD.finditer((text or "").lower().replace("ё", "е")):
        word = match.group(0)
        if len(word) >= WORD_MIN_CHARS:
            out.add(word[:STEM_LEN])
    return frozenset(out)


def jaccard(a, b):
    """Доля общего в объединении двух множеств основ. → float 0..1."""
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def same_meaning(left, right):
    """Две находки — об одном? → bool.

    ДВА условия сразу, и оба обязательны. ВИД должен совпасть: «это упрощаемо» и
    «этого не делать вовсе» про один предмет — РАЗНЫЕ находки, и склеить их
    значило бы потерять одну. И мера смысла должна перевалить :data:`MERGE_JACCARD`
    — число измеренное, а не назначенное.

    FAIL-CLOSED сознательно: сомнение решается в пользу ДВУХ заявок. Лишняя
    заявка стоит владельцу одной карточки; ошибочная склейка ХОРОНИТ вторую
    находку молча, и узнать о ней будет неоткуда.
    """
    if (left or {}).get("kind") != (right or {}).get("kind"):
        return False
    return jaccard(meaning_words(left.get("quote")), meaning_words(right.get("quote"))) >= MERGE_JACCARD


def record(header, finding, answer_rel=""):
    """Находка + шапка файла → ЗАПИСЬ ИСТОЧНИКА. → dict.

    Пять полей задания стоят здесь вместе не для порядка, а потому что заявка без
    любого из них теряет проверяемость: источник (пакет), канал, дата, sha256
    пакета и ДОСЛОВНАЯ цитата.
    """
    return {
        "pack": header.get("pack") or "",
        "pack_path": header.get("pack_path") or "",
        "pack_sha256": header.get("pack_sha256") or "",
        "channel": header.get("channel") or "",
        "send_date": header.get("send_date") or "",
        "answer": answer_rel,
        "answer_sha256": header.get("answer_sha256") or "",
        "index": finding.get("index"),
        "kind": finding.get("kind"),
        "quote": finding.get("quote") or "",
        "unsplit": bool(finding.get("unsplit")),
        "refs": refs(finding.get("quote")),
    }


def _order(rec):
    """Порядок записей — ПОЛНЫЙ и детерминированный (дата, канал, пакет, номер).

    Порядок здесь не косметика: он выбирает ЯКОРЬ группы, а якорь даёт заявке
    ключ. Недетерминированный порядок означал бы, что тот же корпус даёт разные
    ключи от прогона к прогону, и дедуп «уже ставили» перестал бы работать.
    """
    return (rec.get("send_date") or "", rec.get("channel") or "",
            rec.get("pack") or "", int(rec.get("index") or 0))


def claim_key(rec):
    """Ключ заявки — из ВИДА и МЕРЫ СМЫСЛА якорной записи. → str (12 hex).

    Не из текста целиком: тогда лишний пробел канала рождал бы новую заявку на ту
    же находку. Не из одного вида: тогда все находки одного вида слиплись бы в
    одну.
    """
    words = " ".join(sorted(meaning_words(rec.get("quote"))))
    raw = "%s|%s" % (rec.get("kind") or "", words)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def claim_keys(claim):
    """ВСЕ ключи, которыми заявка опознаётся: свой и ключ КАЖДОГО источника. → list.

    Зачем не один. Ключ заявки — это ключ её ЯКОРНОЙ записи, а якорь выбирается
    порядком по корпусу. Придёт завтра второй канал с той же находкой раньше по
    порядку — и у той же по смыслу заявки будет ДРУГОЙ ключ; дедуп «уже ставили»
    промахнулся бы, и владелец получил бы карточку второй раз. Поэтому в реестр
    ложится весь набор, и узнавание идёт по пересечению.
    """
    keys = {claim.get("key")} | {claim_key(src) for src in (claim.get("sources") or [])}
    return sorted(k for k in keys if k)


def merge(records):
    """Записи источников → ЗАЯВКИ (дубли по смыслу склеены). → list[dict].

    Жадная группировка по ЯКОРЮ группы (первая запись в детерминированном
    порядке): запись присоединяется к первой группе, чей якорь ей «по смыслу»
    родня. Сравнение с ЯКОРЕМ, а не «с любым членом группы», выбрано намеренно —
    иначе цепочка попарно похожих находок слепляется в один ком, где первый и
    последний друг другу уже чужие.
    """
    out = []
    for rec in sorted([r for r in (records or []) if isinstance(r, dict)], key=_order):
        for group in out:
            if same_meaning(group["anchor"], rec):
                group["sources"].append(rec)
                break
        else:
            out.append({"anchor": rec, "sources": [rec]})
    claims = []
    for group in out:
        anchor = group["anchor"]
        claims.append({
            "schema": SCHEMA,
            "key": claim_key(anchor),
            "kind": anchor.get("kind"),
            "quote": anchor.get("quote"),
            "sources": group["sources"],
            "channels": sorted({s.get("channel") for s in group["sources"] if s.get("channel")}),
            "packs": sorted({s.get("pack") for s in group["sources"] if s.get("pack")}),
        })
    return claims


# ───────────────────────────── текст заявки ─────────────────────────────


def _clip_quote(quote):
    text = (quote or "").strip()
    if len(text) <= QUOTE_MAX:
        return text
    return text[:QUOTE_MAX].rstrip() + " …[цитата урезана, полная %d знаков — в файле ответа]" % len(text)


def claim_text(claim, premise_out, today):
    """Заявка → текст ряда очереди. → str (≤ :data:`CLAIM_TEXT_MAX`).

    Порядок кусков — порядок чтения человеком: маркер (по нему заявку опознают
    гарды демона), «НЕ ЗАДАЧА», вид, исход премисы С АДРЕСОМ, источники,
    дословные цитаты. Исход премисы стоит ВЫШЕ цитаты сознательно: решать по
    находке, стоящей на протухшей посылке, не нужно вовсе, и узнать это владелец
    обязан раньше, чем прочтёт саму находку.
    """
    if not _RE_DATE.match(today or ""):
        raise ReviewIntakeError("invalid_date", "today must be YYYY-MM-DD, got %r" % (today,))
    key = claim.get("key") or ""
    lines = ["[заявка-ревью дата=%s ключ=%s]" % (today, key), CLAIM_HEAD, ""]
    lines.append("ВИД НАХОДКИ: %s" % (claim.get("kind") or "иное"))
    outcome = (premise_out or {}).get("outcome") or PREMISE_UNKNOWN
    lines.append("ПРЕМИСА: %s — %s" % (PREMISE_TITLE.get(outcome, outcome),
                                       (premise_out or {}).get("why") or "причина не названа"))
    lines.append("  (проверено ЧТЕНИЕМ НАЗВАННОГО АДРЕСА, а не свежестью ответа)")
    sources = claim.get("sources") or []
    lines.append("")
    lines.append("ИСТОЧНИКОВ: %d" % len(sources))
    for src in sources:
        lines.append("  • канал %s · %s · пакет %s · sha256 пакета %s · ответ %s"
                     % (src.get("channel") or "?", src.get("send_date") or "?",
                        src.get("pack") or "?", (src.get("pack_sha256") or "—")[:12],
                        src.get("answer") or "—"))
    lines.append("")
    lines.append("ЦИТАТА ДОСЛОВНО:")
    for src in sources:
        if len(sources) > 1:
            lines.append("— из %s (канал %s):" % (src.get("pack") or "?", src.get("channel") or "?"))
        lines.append(_clip_quote(src.get("quote")))
    lines.append("")
    lines.append("ЧТО ДЕЛАЕТ ЭТА ЗАЯВКА: ничего. Она ждёт твоего решения и никуда не едет.")
    text = "\n".join(lines)
    return text[:CLAIM_TEXT_MAX]


def claim_markers(items):
    """Ряды очереди → [(дата, ключ)] уже поставленных заявок. → list.

    Источник дедупа и бюджета — САМА ОЧЕРЕДЬ, как у задач-находок ревизора:
    память процесса не переживает рестарта, а заявка обязана не приезжать дважды
    именно после него.
    """
    out = []
    for it in (items or []):
        match = _RE_CLAIM.match(str((it or {}).get("task_text") or ""))
        if match:
            out.append((match.group(1), match.group(2)))
    return out


def is_claim(text):
    """task_text — заявка внешнего канала? Маркер-гейт для гардов демона. → bool."""
    return str(text or "").startswith(CLAIM_MARK)


def budget_left(markers, today, budget):
    """Сколько заявок ещё можно поставить сегодня. → int ≥ 0."""
    used = sum(1 for day, _key in (markers or []) if day == today)
    return max(0, int(budget) - used)


def select(claims, *, placed=(), markers=(), today="", budget=0, limit=None):
    """Какие заявки ставить СЕЙЧАС. → (к постановке, отложенные[(заявка, причина)]).

    Три причины отказа, и каждая названа отдельным словом, потому что означают
    они разное: ``уже поставлена`` (ключ в реестре или в очереди), ``бюджет
    суток`` (отсрочка политикой — вернётся завтра) и ``сверх лимита захода``
    (ручной потолок пробы).
    """
    seen = set(placed or ()) | {key for _day, key in (markers or ())}
    take, held = [], []
    left = budget_left(markers, today, budget)
    for claim in claims or []:
        keys = claim_keys(claim)
        if seen.intersection(keys):
            held.append((claim, "уже поставлена"))
            continue
        key = claim.get("key")
        if limit is not None and len(take) >= limit:
            held.append((claim, "сверх лимита захода"))
            continue
        if len(take) >= left:
            held.append((claim, "бюджет суток исчерпан"))
            continue
        take.append(claim)
        seen.update(keys)
    return take, held


def index_line(claim, premise_out, queue_id):
    """Строка-индекс заявки для журнала. → str (одна строка).

    Журнал — индекс, а не хранилище тел: сюда идут ключ, вид, исход премисы,
    число источников и НОМЕР РЯДА, по которому заявка находится в очереди.
    """
    return ("ARTIFACT ревью-заявка ключ=%s вид=%s премиса=%s источников=%d каналы=%s → очередь #%s"
            % (claim.get("key"), claim.get("kind"),
               PREMISE_TITLE.get((premise_out or {}).get("outcome"), "?"),
               len(claim.get("sources") or []), "+".join(claim.get("channels") or []) or "—",
               queue_id))


def today_utc(now_iso):
    """ISO-момент → календарный день UTC. → 'YYYY-MM-DD'.

    Часы сюда не заводятся: «сейчас» приезжает полем входа, иначе решение о
    бюджете суток перестало бы воспроизводиться на тех же фактах.
    """
    stamp = str(now_iso or "").replace("Z", "+00:00")
    return datetime.datetime.fromisoformat(stamp).astimezone(
        datetime.timezone.utc).strftime("%Y-%m-%d")


def outbound_safe(text):
    """Текст заявки не несёт наружу лишнего? → list находок стражи (пусто = чисто).

    Заявка НАРУЖУ НЕ ЕДЕТ — она ложится в свою очередь, — но цитата в ней чужая и
    прилетела ИЗВНЕ. Проверяем её ТОЙ ЖЕ стражей, что держит исходящее ступени 2:
    один страж на полосу, а не второе мнение о том, что считать утечкой.
    """
    return review_send.outbound_violations(text)
