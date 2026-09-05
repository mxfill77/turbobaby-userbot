# -*- coding: utf-8 -*-
"""
done_judge_pc.py — СТУПЕНЬ C: закрытие задачи полосы ПК судит V0, а не слово исполнителя.

ПОВОД (своя полоса, проверяемо здесь). Перепись 15.08.2026 (`a545ad2`): 87 мест судят исход, 62 —
по ОТЧЁТУ исполнителя, и НИ ОДНО из 25 «чтений назад» не читает продукт задачи. С тех пор полоса
получила МЕСТО для адреса (`result_ref.py`) и СУДЬЮ адреса (`result_judge_pc.py`), но судья
намеренно не подключён и подключён быть не может: инвариант `RESULT_JUDGE_UNWIRED` роняет гейт на
первом же упоминании его имени в боевом коде. Поэтому судьёй здесь стои́т V0
(`content_product_verifier.py`) — у него такого замка нет, а отрицательный тест и независимая
приёмка за 01.09 у него есть.

ЧТО ЭТОТ МОДУЛЬ ДЕЛАЕТ, ОДНОЙ СТРОКОЙ: переводит факты полосы в БАНДЛ V0 и отдаёт V0 вердикт.
Своего суждения о «сделано» у него нет ни одной веткой, КРОМЕ одного честно названного случая —
читать нечего вовсе (§«ГДЕ СУДИТ АДАПТЕР»).

═══ ПРАВИЛО ═════════════════════════════════════════════════════════════════════════════════

    Задача становится `done`, только если продукт ПРОЧИТАН ПО НАЗВАННОМУ АДРЕСУ и прочитанное
    отвечает адресу. Нет адреса · по адресу пусто · по адресу лежит результат ПРЕЖНЕГО
    прогона — исход «неизвестно», а НЕ «сделано».

«Неизвестно» здесь — не «в порядке». Тот же закон, что у слоя ожиданий полосы (`CLAUDE.md`,
§О1–О4, п. 3) и у судьи адреса: «проверить невозможно» не закрывает эпизод.

═══ ПОЧЕМУ ОПОРНАЯ ЛИНИЯ СНИМАЕТСЯ ДЕМОНОМ, А НЕ ПРИНИМАЕТСЯ ОТ ИСПОЛНИТЕЛЯ ═════════════════

Отрицательный тест V0 (01.09, `docs/artifacts/2026-09-01-v0-negative-test.md`) назвал ДВЕ дыры.
Первую — «след покойника» — закрыла привязка к личности прогона (`V0_RUN_BINDING`). Вторая,
развилка 2, осталась открытой дословно: «пока „что было до“ приходит от исполнителя, C2b
неустраним в принципе; честный вариант — снимать baseline тем же прибором ДО захода».

Здесь она и закрывается: `baseline()` зовётся ДО `run_task`, руками демона, и меряет sha256 того,
что лежит по адресу. Исполнитель до этого снимка не касается ничем — он в это время ещё не
запущен. Поэтому «результат прежнего прогона» ловится ИЗМЕРЕНИЕМ, а не заявлением, и ловится он
не здесь, а гейтом `V0_SCOPE_EXACT`: адрес назван в `allowed_changed_paths`, а изменений по нему
ноль → `changed_scope_mismatch`, вердикт `DISPROVEN`.

СВЕЖЕСТЬ НИГДЕ НЕ МЕРИТСЯ ВРЕМЕНЕМ. Ни `mtime`, ни «моложе заявки»: свежий чужой файл доказывает
продукт захода ровно так же, как чужая запись доказывает авторство тика модербота (класс О3 этой
полосы). Меряется РАЗНИЦА ДВУХ СНИМКОВ, снятых одними руками.

═══ ГДЕ СУДИТ АДАПТЕР, А НЕ V0 (граница названа, а не спрятана) ══════════════════════════════

V0 судит ТОЛЬКО когда есть что читать. Четыре случая до него:

    адрес не назван         → «неизвестно» (адаптер): бандла нет, судить нечего;
    опорный снимок не снят  → «неизвестно» (адаптер): без «до» любая новизна недоказуема;
    по адресу пусто вовсе   → «неизвестно» (адаптер): нечего класть в манифест кандидата;
    ни один файл по адресу не отвечает СЛОВАМ АДРЕСА → «неизвестно» (адаптер): кандидата нет.

Четвёртый случай новым правилом НЕ является и мягче прежнего быть не может: слова адреса
адаптер меряет ТОЙ ЖЕ функцией `v0.address_hit`, которой их меряет сам гейт V0
(`path_or_text_contains_ci`). Файл, не попавший в `hits`, у V0 гарантированно получил бы
`text_condition_failed`, то есть `DISPROVEN`, то есть то же «неизвестно». Отсюда замок, который
держит тест: ни одно закрытие не может из-за этой ветки СТАТЬ доказанным или ПЕРЕСТАТЬ им быть —
меняется только то, ЧЬИМ файлом назван вердикт.

Во ВСЕХ остальных случаях вердикт — дословный ответ V0, и адаптер только переводит его в два
слова полосы: `PROVEN` → «сделано», всё прочее → «неизвестно».

═══ ЧЕГО ЭТОТ ПРИБОР НЕ ДЕЛАЕТ ══════════════════════════════════════════════════════════════

  • НЕ судит `failed` и `needs_approval`: предмет — только переход в `done`;
  • НЕ читает содержимого задачи дальше адреса и НЕ оценивает КАЧЕСТВО артефакта: «слова адреса
    найдены» — это адрес, а не рецензия;
  • НЕ ходит в сеть, к процессам и в базу (инвариант в тесте: ни `subprocess`, ни `socket`,
    ни `sqlite3`, ни `urllib`, ни часов);
  • пишет РОВНО в одну явно названную папку — `tmp/done_judge_pc/` (пакеты задачи и заявки,
    которых V0 требует файлами, и реестр вердиктов). Артефакта, дерева и очереди не касается
    ничем.

═══ РЕЕСТР ВЕРДИКТОВ: ПОЧЕМУ СЛЕД ОБЯЗАН ПЕРЕЖИТЬ САМ ЗАХОД (02.09.2026) ═════════════════════

Вердикт «неизвестно, адрес не назван» в режиме `addr` статуса НЕ МЕНЯЕТ (так и задумано) и в
отчёт задачи не приписывается (шум в каждом отчёте дороже сведения). До 02.09 из этого следовал
класс: след суда жил РОВНО в логе демона, а СЧЁТ СЕРИИ (`contour_digest.series`) читает слепок
очереди, где у сданной строки исход `done` и поле `why` ПУСТОЕ по построению
(`queue_snapshot_pc.apply_failed`: причина есть только у упавших). Значит закрытие, которого
судья не проверял, входило в серию наравне с доказанным — замер 02.09 по живому слепку: серия
18 подряд, из них ДОКАЗАНО судьёй 5, закрыто БЕЗ АДРЕСА 13.

Реестр (`tmp/done_judge_pc/judged.json`) — это и есть недостающий след: демон кладёт в него
исход суда на КАЖДОЕ заявленное `done`, независимо от режима, а счёт серии читает его и делит
закрытия на ДОКАЗАННЫЕ и ЗАКРЫТЫЕ БЕЗ ПРОВЕРКИ. Три вещи названы, а не подразумеваются:

  • ЧАСОВ У РЕЕСТРА НЕТ (их нет у всего модуля). Порядок держит `seq` — счётчик, растущий на
    единицу от максимума уже лежащего. Обрезка хвоста идёт по нему же, а не по времени.
  • ОТСУТСТВИЕ ЗАПИСИ — ЭТО «СУДЬЯ НЕ СУДИЛ», а не «всё хорошо». Строки, закрытые до появления
    ступени C, записи не имеют и чистыми не считаются: «не доказан сильнее неизвестно».
  • ЗАПИСЬ НЕ СМЕЕТ УРОНИТЬ ЗАХОД. Любой отказ диска — `None` и молчание: судья, падающий на
    своём реестре, закрыл бы задачу хуже, чем судья без реестра.

═══ ЧУЖОЙ ФАЙЛ ПО АДРЕСУ: ДВА МЕСТА, ГДЕ РАЗЛИЧИТЕЛЬ МОЛЧАЛ (03.09.2026) ═════════════════════

Повод: Штаб прочёл закрытие «задача 26 → сделано по адресу артефакта про карточку гарда» как
зачёт ЧУЖОГО файла. Замер опроверг и повод, и гипотезу («слова адреса слишком свободные»):
20 живых адресов × 500 файлов = 10 000 пар, чужих совпадений НОЛЬ, у каждой задачи ровно один
ответ и он свой; артефакт задачи 26 родился ВНУТРИ её окна. Ужесточать слова нельзя и незачем —
11 из 20 живых адресов несут слова ТОЛЬКО ИМЕНЕМ файла, и правило «слова обязаны быть в теле»
перекрасило бы 11 честных закрытий.

Но там, где искали, дыры не было, а две другие нашлись — обе в АДАПТЕРЕ, обе про «который из
файлов продукт», и обе закрыты здесь ИЗМЕРЕНИЕМ, без часов:

  1. **НЕОДНОЗНАЧНОСТЬ БЫЛА МОЛЧАЛИВЫМ ВЫБОРОМ.** Отвечающих словам файлов, изменённых за заход,
     могло быть несколько — брался ПЕРВЫЙ ПО АЛФАВИТУ, и если чужой файл заход тоже тронул, в
     бандл уходил он. V0 такое не ловит ничем: ему подают ОДИН путь, и он честно судит поданное.
     Теперь два ответа и больше — «неизвестно» с перечнем, а не выбор.
  2. **КОПИЯ ПРЕЖНЕГО СЧИТАЛАСЬ ПРОДУКТОМ.** В режиме `measured` любое изменение sha по адресу
     означает «родилось за заход». Переименованный или скопированный сюда чужой артефакт даёт
     ровно такое изменение — файл ПОЯВИЛСЯ, а содержимое его старше начала захода. Теперь sha
     кандидата сверяется со снимком ВСЕЙ адресной папки, снятым до захода (`prior`): совпало —
     «неизвестно» с именем того, чьей копией кандидат оказался.

Часов не прибавилось ни одной строкой: «старше начала захода» доказывается тем, что это же
содержимое ЛЕЖАЛО здесь до захода, а не сравнением времён (тот же закон, что в §«ПОЧЕМУ
ОПОРНАЯ ЛИНИЯ СНИМАЕТСЯ ДЕМОНОМ»). Роль V0 не урезана: «по адресу труп прежнего прогона» ловит
по-прежнему он (`changed_scope_mismatch`), а адаптер добавил только то, чего прибору не видно.

  3. **ТРЕТЬЯ ДЫРА ТОГО ЖЕ КЛАССА, И ОНА ОТКРЫВАЛАСЬ ПАРАЛЛЕЛЬЮ (05.09.2026).** Когда СЛОВАМ
     адреса не отвечал НИ ОДИН файл, выбор не прекращался, а съезжал на запасные ветки: «первый
     по алфавиту из ИЗМЕНЁННЫХ за заход», а если и таких нет — «первый по алфавиту из ЛЕЖАЩИХ по
     адресу». Обе берут файл, к словам ЭТОЙ задачи отношения не имеющий, и обе молчат об этом.

     Пока полоса брала по одной задаче за виток, «изменённое за заход» почти всегда было своим.
     С 05.09 демон берёт ДВЕ (`ПАРАЛЛЕЛЬ: беру 2 задач(и) одним витком`), а опорный снимок у
     каждой руки снят при СВОЁМ claim — значит в `changed` второй руки лежит и продукт ПЕРВОЙ.
     Живой замер того дня: 3 закрытых параллельных пары, в ДВУХ обе задачи закрыты по ОДНОМУ и
     тому же адресу — чужому для второй руки (16/17 → «…вердикт-тренажёра-16-кейсов…»,
     18/21 → «…сборка-пакета-не-выглядит-смертью-демона»). Третья пара (14/15) разошлась
     СЛУЧАЙНО: её файлы легли в алфавите так, что каждой руке достался свой.

     Теперь кандидат обязан отвечать словам своего адреса: не отвечает никто — «неизвестно» с
     НАЗВАННЫМ СВОИМ адресом, а изменённое за заход остаётся сведением в причине. Пропасть
     вердикту это не даёт (см. §«ГДЕ СУДИТ АДАПТЕР»: такой файл у V0 всё равно `DISPROVEN`),
     а вердикт о чужой работе — прекращает.

ОТКАТ: `DONE_JUDGE_PC=off` — вердикт считается и печатается, статус не меняется НИКОГДА.
Отдельно реестр: `DONE_JUDGE_PC_LEDGER=<путь>` уводит его в сторону; удалять файл не нужно —
пустой реестр честно читается как «судья не судил ни одной».
"""

import hashlib
import json
import os
import re

import content_product_verifier as v0

REPO = os.path.dirname(os.path.abspath(__file__))

# ═════════════════════════ ДВА СЛОВА ПОЛОСЫ ══════════════════════════════════════════════════
# Их два, а не три, сознательно: закрытие задачи отвечает на ОДИН вопрос — «класть ли `done`».
# Три исхода V0 при этом не теряются: они целиком лежат в `v0` и `reason` вердикта.
DONE = "сделано"
UNKNOWN = "неизвестно"

# Маркер адреса в тексте задачи. Живая форма полосы (замер 01.09: 5 задач из 5 за сутки).
MARK = "АДРЕС РЕЗУЛЬТАТА"

PACKET_DIR = "tmp/done_judge_pc"

# ═════════════════════════ РЕЕСТР ВЕРДИКТОВ (след суда переживает заход) ═════════════════════
# Живёт В ТОЙ ЖЕ единственной папке — второй адрес записи модулю не заводится.
LEDGER = PACKET_DIR + "/judged.json"
LEDGER_ENV = "DONE_JUDGE_PC_LEDGER"
LEDGER_SCHEMA = "turbobaby.done_judge_pc.judged/v1"
# Хвост реестра НАЗВАН ЧИСЛОМ полосы: окно счёта серии — 30 цепочек, темп полосы 4.3 закрытых в
# сутки (замер слепка очереди). 300 записей это ≈70 суток и десятикратный запас над окном.
LEDGER_MAX = 300
LEDGER_READ_MAX = 512 * 1024      # потолок чтения: битый/раздутый реестр — это «судья не судил»
REASON_MAX = 200                  # причина в реестре — индекс, а не отчёт

# ИМЕНА ПОЛЕЙ ЗАПИСИ. Их читает ЧУЖОЙ модуль (`contour_digest`), поэтому они константы, а не
# литералы по месту: два экземпляра одного имени расходятся молча — класс полосы.
F_PROVED = "proved"           # судья ДОКАЗАЛ продукт по названному адресу
F_ADDRESSED = "addressed"     # адрес результата задачей НАЗВАН (было чем судить)
F_REASON = "reason"           # причина словами — для человека, счёт по ней не идёт
F_SEQ = "seq"                 # порядок записи; часов у модуля нет, порядок держит счётчик

GATE_ID = "ADDRESS_WORDS"
ARTIFACT_ID = "result"
# Потолок прибора, не наш: `content_product_verifier._max_bytes` отвергает всё крупнее.
MAX_ARTIFACT_BYTES = v0.DEFAULT_MAX_BYTES

# Режимы врезки. `addr` — дефолт: судим там, где адрес НАЗВАН. `all` — полное правило владельца
# (нет адреса → тоже не «сделано»); почему не дефолт — в разборе ступени C, числом.
MODE_OFF, MODE_ADDR, MODE_ALL = "off", "addr", "all"
MODES = (MODE_OFF, MODE_ADDR, MODE_ALL)
MODE_ENV = "DONE_JUDGE_PC"

# «файл в <папке> за <дд.мм[.гггг]> со словами <слова>» — живая форма адреса на этой полосе.
_RE_ADDR_PROSE = re.compile(
    MARK + r"\s*:?\s*файл\s+в\s+(?P<folder>[0-9A-Za-z_][0-9A-Za-z_./-]*)"
    r"\s+за\s+(?P<day>\d{1,2})\.(?P<month>\d{1,2})(?:\.(?P<year>\d{4}))?"
    r"\s+со\s+словами\s+(?P<words>[^\r\n]+)", re.IGNORECASE)
# «файл <путь>[ со словами <слова>]» — прямой указатель (канон `result_ref`, вид `file`).
_RE_ADDR_PATH = re.compile(
    MARK + r"\s*:?\s*файл\s+(?P<path>[0-9A-Za-z_][0-9A-Za-z_./-]*\.[0-9A-Za-z]+)"
    r"(?:\s+со\s+словами\s+(?P<words>[^\r\n]+))?", re.IGNORECASE)


def enforce_mode(env=None):
    """Режим врезки из окружения. Незнакомое значение → дефолт, а не тишина и не падение."""
    raw = (env if env is not None else os.environ).get(MODE_ENV)
    raw = str(raw).strip().lower() if raw is not None else ""
    if raw in ("0", "no", "false", MODE_OFF):
        return MODE_OFF
    if raw in ("1", "yes", "true", "on", MODE_ALL):
        return MODE_ALL
    return MODE_ADDR


def _rel(path):
    """Путь → относительный с прямыми слэшами | None (наружу дерева, пустой, `..`)."""
    if not isinstance(path, str) or not path.strip():
        return None
    if os.path.isabs(path) or re.match(r"^[A-Za-z]:", path) or path.startswith(("//", "\\\\")):
        return None
    parts = [p for p in re.split(r"[\\/]", path) if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def read_address(text):
    """Текст ЗАДАЧИ → адрес результата | None («адрес не назван»).

    Адрес берётся из ЗАДАЧИ, а не из отчёта: у V0 это уже принятая асимметрия — «The task, not
    the executor bundle, names the checks that prove it». Исполнитель, называющий адрес сам,
    доказывал бы себя своим же словом."""
    s = "" if text is None else str(text)
    match = _RE_ADDR_PROSE.search(s)
    if match:
        folder = _rel(match.group("folder"))
        words = (match.group("words") or "").strip()
        if not folder or not words:
            return None
        return {"folder": folder, "day": int(match.group("day")), "month": int(match.group("month")),
                "year": int(match.group("year")) if match.group("year") else None,
                "words": words, "path": None}
    match = _RE_ADDR_PATH.search(s)
    if match:
        path = _rel(match.group("path"))
        words = (match.group("words") or "").strip()
        if not path:
            return None
        return {"folder": path.rsplit("/", 1)[0] if "/" in path else "", "day": None, "month": None,
                "year": None, "words": words, "path": path}
    return None


def _name_matches(name, addr):
    """Имя файла отвечает ДАТЕ адреса? Год НЕ подставляется часами — он берётся из имени.

    Разбор года по календарю сессии был бы часами боковой дверью; вместо этого дата читается
    как `<любой год>-ММ-ДД`, а «прежний год» отсекается не датой, а измеренной новизной."""
    if addr.get("year"):
        head = "%04d-%02d-%02d" % (addr["year"], addr["month"], addr["day"])
        return name.startswith(head) and len(name) > len(head) and name[len(head)] in "-."
    return bool(re.match(r"^\d{4}-%02d-%02d[-.]" % (addr["month"], addr["day"]), name))


def _sha256_file(full, cap):
    """sha256 файла | None (не файл, ссылка, крупнее потолка прибора, не прочитан)."""
    try:
        if os.path.islink(full) or not os.path.isfile(full) or os.path.getsize(full) > cap:
            return None
        with open(full, "rb") as handle:
            return hashlib.sha256(handle.read(cap + 1)).hexdigest()
    except OSError:
        return None


def scan(addr, root=REPO, cap=MAX_ARTIFACT_BYTES):
    """Снимок по адресу: {относительный путь: sha256}. Ошибка чтения папки → None («не снят»).

    Пустой словарь и None — РАЗНЫЕ ответы: «по адресу ничего нет» против «посмотреть не смог»."""
    if not addr:
        return None
    if addr.get("path"):
        digest = _sha256_file(os.path.join(root, addr["path"].replace("/", os.sep)), cap)
        return {addr["path"]: digest} if digest else {}
    folder = os.path.join(root, addr["folder"].replace("/", os.sep))
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    out = {}
    for name in names:
        if not _name_matches(name, addr):
            continue
        digest = _sha256_file(os.path.join(folder, name), cap)
        if digest:
            out["%s/%s" % (addr["folder"], name)] = digest
    return out


# Ключ второго снимка в опорной линии. Имя — константа, а не литерал по месту: его читает
# `judge`, и два экземпляра одного имени расходятся молча (класс полосы).
F_PRIOR = "prior"


def _addr_folder(addr):
    """Адресная ПАПКА — та, где будет лежать продукт (у прямого пути это его каталог)."""
    if not addr:
        return None
    if addr.get("path"):
        return addr["path"].rsplit("/", 1)[0] if "/" in addr["path"] else ""
    return addr.get("folder")


def prior_shas(addr, root=REPO, cap=MAX_ARTIFACT_BYTES):
    """Что ЛЕЖАЛО в адресной папке до захода: {sha256: относительный путь} | None («не снят»).

    Снимается по ВСЕЙ папке, а не за названную дату: копию сюда приносят из соседнего дня чаще,
    чем из того же. Хранится sha→путь, а не путь→sha, потому что вопрос ровно один — «не
    является ли кандидат содержимым, которое тут уже было, и чьим именно»."""
    folder = _addr_folder(addr)
    if folder is None:
        return None
    full = os.path.join(root, folder.replace("/", os.sep)) if folder else root
    try:
        names = sorted(os.listdir(full))
    except OSError:
        return None
    out = {}
    for name in names:
        digest = _sha256_file(os.path.join(full, name), cap)
        if digest and digest not in out:
            out[digest] = ("%s/%s" % (folder, name)) if folder else name
    return out


def baseline(text, root=REPO):
    """ОПОРНЫЙ СНИМОК, снятый ДО захода РУКАМИ ДЕМОНА. Зовётся перед `run_task`, не позже.

    Именно этот вызов отнимает у исполнителя право говорить, «что было до»: снимок сделан
    тогда, когда исполнителя ещё нет. Снимков ДВА: узкий (файлы за названную дату — им меряется
    новизна) и широкий (`prior` — содержимое ВСЕЙ адресной папки, им отличается продукт от
    копии того, что тут уже лежало)."""
    addr = read_address(text)
    files = scan(addr, root) if addr else None
    return {"address": addr, "files": files, F_PRIOR: prior_shas(addr, root) if addr else None,
            "ok": addr is not None and files is not None}


def _read_text(full, cap):
    try:
        with open(full, "rb") as handle:
            return handle.read(cap + 1).decode("utf-8", "replace")
    except OSError:
        return ""


def _verdict(verdict, reason, addr=None, chosen=None, result=None, run_id=None, extra=None):
    out = {"verdict": verdict, "reason": reason, "address": addr, "chosen": chosen,
           "v0": result, "run_id": run_id}
    if extra:
        out.update(extra)
    return out


def run_token(tid, draft_rel=None):
    """ЛИЧНОСТЬ ПРОГОНА для бандла: токен черновика доклада, иначе номер задачи.

    Токен рождает демон при заходе (`report_draft`), исполнитель его не выбирает. При режиме
    `measured` привязка стои́т не на нём, а на измеренной новизне, поэтому запасной вариант
    (номер задачи) вердикта не подменяет — он только заполняет обязательное поле схемы."""
    name = os.path.basename(str(draft_rel or ""))
    match = re.search(r"task\d+-(?P<token>[0-9A-Za-z][0-9A-Za-z_.-]{3,79})\.md$", name)
    if match:
        return match.group("token")
    return "pc-task-%s" % (tid if tid not in (None, "") else "unknown")


def _packets(case_id, run_id, addr, gates, status, root):
    """Пакет задачи и пакет заявки — файлами, как требует V0. Пишет ДЕМОН, не исполнитель."""
    rel_dir = "%s/%s" % (PACKET_DIR, case_id)
    full_dir = os.path.join(root, rel_dir.replace("/", os.sep))
    os.makedirs(full_dir, exist_ok=True)
    task = {"schema_version": "v0.1", "case_id": case_id, "run_id": run_id,
            v0.RUN_BINDING_FIELD: v0.RUN_BINDING_MEASURED,
            "address": {"folder": addr["folder"], "path": addr.get("path"), "words": addr["words"]},
            "required_content_gates": gates}
    claim = {"schema_version": "v0.1", "case_id": case_id, "reported_claim": "reported_done",
             "reported_status": str(status), "unknowns": []}
    out = {}
    for key, obj in (("task_packet", task), ("result_packet", claim)):
        data = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        with open(os.path.join(full_dir, key + ".json"), "wb") as handle:
            handle.write(data)
        out[key] = {"path": "%s/%s.json" % (rel_dir, key),
                    "sha256": hashlib.sha256(data).hexdigest(), "content_type": "json"}
    return out


def judge(tid, text, status, base, run_id=None, root=REPO):
    """ВЕРДИКТ ЗАКРЫТИЯ: «сделано» | «неизвестно». `None` — задача не про `done`, судить нечего.

    Не поднимает исключений НИ ОДНОЙ веткой: сбой самого судьи — это «неизвестно» с названной
    виной, а не проход мимо. Судья, падающий на своей задаче, закрыл бы её как зелёную."""
    if str(status) != "done":
        return None
    try:
        addr = (base or {}).get("address") or read_address(text)
        if not addr:
            return _verdict(UNKNOWN, "адрес результата не назван задачей — доказывать нечем")
        if not addr.get("words"):
            return _verdict(UNKNOWN, "адрес без слов: доказывать таким адресом нечего", addr)
        if not (base or {}).get("ok") or (base or {}).get("files") is None:
            return _verdict(UNKNOWN, "опорный снимок по адресу НЕ СНЯТ до захода — новизна "
                                     "продукта недоказуема", addr)
        before = dict(base["files"])
        now = scan(addr, root)
        if now is None:
            return _verdict(UNKNOWN, "чтение по адресу сорвалось (папка недоступна)", addr)
        changed = sorted(p for p in set(before) | set(now) if before.get(p) != now.get(p))
        words = addr["words"]
        # ОДНА реализация правила «отвечает адресу» на два вызова: здесь она ВЫБИРАЕТ файл,
        # у V0 она СУДИТ. Разъедься они — адаптер подавал бы прибору не тот файл.
        hits = [p for p in sorted(now)
                if v0.address_hit(p, _read_text(os.path.join(root, p.replace("/", os.sep)),
                                                MAX_ARTIFACT_BYTES), words)]
        # НЕОДНОЗНАЧНОСТЬ — НЕ ВЫБОР (§«ЧУЖОЙ ФАЙЛ ПО АДРЕСУ», п. 1). Отвечающих словам и
        # изменённых за заход обязан быть ОДИН. Двое и больше — судья не знает, который из них
        # продукт задачи, и обязан сказать это, а не взять первого по алфавиту.
        fresh_hits = [p for p in changed if p in hits and p in now]
        if len(fresh_hits) > 1:
            return _verdict(UNKNOWN, "по адресу за заход изменилось %d файла(ов), отвечающих "
                            "словам адреса (%s) — который из них продукт задачи, судья не знает"
                            % (len(fresh_hits), ", ".join(fresh_hits[:4])), addr)
        # КАНДИДАТ ОБЯЗАН ОТВЕЧАТЬ СЛОВАМ СВОЕГО АДРЕСА (§«ЧУЖОЙ ФАЙЛ ПО АДРЕСУ», п. 3). Прежние
        # запасные ветки — «первый изменённый» и «первый лежащий» — брали файл, к словам ЭТОЙ
        # задачи отношения не имеющий; в параллельном витке это ровно продукт ВТОРОЙ РУКИ.
        chosen = (next((p for p in changed if p in hits), None)
                  or (hits[0] if hits else None))
        if chosen is None:
            # Два разных «пусто», и путать их нельзя: «не появилось» — это молчание захода,
            # «ИСЧЕЗЛО» — его действие. Исход один, диагноз разный.
            if not now:
                gone = ("ИСЧЕЗЛО за заход (до захода лежало %d)" % len(before) if before
                        else "нет ни одного файла")
                return _verdict(UNKNOWN, "по адресу ПУСТО: за названную дату в «%s» %s"
                                % (addr["folder"], gone), addr)
            # Файлы по адресу ЕСТЬ, но ни один не отвечает словам ЭТОЙ задачи. Назвать любой из
            # них «адресом» — значит вынести вердикт о чужой работе; что заход тронул, остаётся
            # СВЕДЕНИЕМ в причине и продуктом не объявляется.
            fresh = [p for p in changed if p in now]
            return _verdict(UNKNOWN, "по адресу «%s» НИ ОДИН из %d файлов не отвечает словам "
                            "адреса «%s» — доказывать нечем; за заход изменилось: %s"
                            % (addr["folder"], len(now), words,
                               ", ".join(fresh[:4]) if fresh else "ничего"), addr)
        # КОПИЯ ПРЕЖНЕГО — НЕ ПРОДУКТ (§«ЧУЖОЙ ФАЙЛ ПО АДРЕСУ», п. 2). Спрашивается только у
        # кандидата, который заход ИЗМЕНИЛ: неизменённый и так уйдёт к V0 и вернётся оттуда
        # `changed_scope_mismatch` — отнимать у прибора его ветку мы не собираемся.
        prior = (base or {}).get(F_PRIOR)
        if chosen in changed:
            if not isinstance(prior, dict):
                return _verdict(UNKNOWN, "снимок адресной ПАПКИ до захода не снят — отличить "
                                         "продукт от копии прежнего нечем", addr, chosen)
            twin = prior.get(now[chosen])
            if twin is not None:
                return _verdict(UNKNOWN, "по адресу появился «%s», но это содержимое ЛЕЖАЛО тут "
                                "до захода (дословная копия «%s») — файл старше начала захода, "
                                "продуктом он не стал" % (chosen, twin), addr, chosen)
        run_id = run_id or run_token(tid)
        case_id = "pc-done-%s" % (tid if tid not in (None, "") else "unknown")
        gates = [{"gate_id": GATE_ID, "artifact_id": ARTIFACT_ID, "type": "path_or_text_contains_ci",
                  "params": {"text": words}}]
        packets = _packets(case_id, run_id, addr, gates, status, root)
        bundle = {
            "schema_version": "v0.1", "case_id": case_id, "workspace_root": root, "run_id": run_id,
            "task_packet": packets["task_packet"], "result_packet": packets["result_packet"],
            # Опорная линия — НАШ замер до захода; кандидат — НАШ замер после. Слова исполнителя
            # в манифестах нет ни одного.
            "baseline_manifest": [{"path": chosen, "sha256": before.get(chosen)}],
            "candidate_manifest": [{"path": chosen, "sha256": now[chosen],
                                    "max_bytes": MAX_ARTIFACT_BYTES}],
            # Адрес назвал ОДИН продукт — его и требуем. Ноль изменений по нему = результат
            # прежнего прогона, и это `changed_scope_mismatch` у V0, а не суждение адаптера.
            "allowed_changed_paths": [chosen],
            "required_artifacts": [{"artifact_id": ARTIFACT_ID, "path": chosen, "required": True,
                                    "sha256": now[chosen], "content_type": "text",
                                    "max_bytes": MAX_ARTIFACT_BYTES}],
            "content_gates": gates, "test_evidence": [],
        }
        result = v0.verify_case(bundle)
        proven = result.get("verdict") == v0.PROVEN
        return _verdict(DONE if proven else UNKNOWN,
                        "V0: %s / %s по адресу «%s»" % (result.get("verdict"),
                                                        result.get("reason_code"), chosen),
                        addr, chosen, result, run_id,
                        {"changed": changed, "candidates": sorted(now)})
    except Exception as exc:                                   # noqa: BLE001 — см. докстринг
        return _verdict(UNKNOWN, "СБОЙ СУДЬИ (%s: %s) — это не «сделано»"
                        % (type(exc).__name__, str(exc)[:120]))


# Маркер исхода в тексте отчёта. Своего СТАТУСА у «неизвестно» в очереди нет — статусов ровно
# шесть, — поэтому исход живёт маркером в причине, ровно как «отклонено Филиппом» у отказа
# владельца (`queue_snapshot_pc`, класс 15.08). Искать по нему, а не по статусу.
UNKNOWN_PREFIX = "НЕИЗВЕСТНО (V0): результат по названному адресу НЕ ПРОЧИТАН"


def line(verdict):
    """Одна строка в отчёт задачи. Ставится В НАЧАЛО: обрезка отчёта режет с хвоста."""
    if not verdict:
        return ""
    return "[V0 судит done: %s · %s]" % (verdict["verdict"], verdict["reason"])


def fail_result(verdict, result):
    """Текст закрытия, когда «сделано» не доказано. Отчёт исполнителя НЕ выбрасывается — он
    съезжает под вердикт: он остаётся сведениями и перестаёт быть доказательством."""
    return "%s — %s\n\n%s" % (UNKNOWN_PREFIX, (verdict or {}).get("reason", ""), result or "")


def enforces(verdict, mode):
    """Меняет ли этот вердикт статус задачи при этом режиме."""
    if not verdict or verdict.get("verdict") == DONE or mode == MODE_OFF:
        return False
    return mode == MODE_ALL or bool(verdict.get("address"))


# ═════════════════════════ РЕЕСТР ВЕРДИКТОВ: СЛЕД, КОТОРЫЙ УВИДИТ СЧЁТ ═══════════════════════
# Разбор и повод — в шапке модуля, §«РЕЕСТР ВЕРДИКТОВ». Здесь только устройство.

def ledger_path(root=None):
    """Путь реестра вердиктов.

    `root` НАЗВАН ЯВНО — реестр уезжает вместе с деревом (так его кладёт тест на своём корне).
    `root=None` — боевой вызов демона: путь идёт через `log_setup.state_path`, и под тест-прогоном
    он уводится в temp. Без этого крюка регресс врезки писал бы в БОЕВОЙ реестр полосы (класс
    «гейт съел спул», 05.08.2026)."""
    named = os.environ.get(LEDGER_ENV)
    if named is not None and str(named).strip():
        return str(named).strip()
    rel = LEDGER.replace("/", os.sep)
    if root is not None:
        return os.path.join(root, rel)
    import log_setup
    return log_setup.state_path(os.path.join(REPO, rel))


def read_ledger(root=None):
    """Реестр → `{номер задачи: запись}`. Любой отказ → `{}` = «судья не судил ни одной».

    Пустой ответ здесь НЕ означает «всё хорошо»: он означает отсутствие доказательств, и
    читающий (счёт серии) обязан обходиться с ним именно так."""
    try:
        with open(ledger_path(root), "rb") as handle:
            got = json.loads(handle.read(LEDGER_READ_MAX).decode("utf-8", "replace"))
    except (OSError, ValueError):
        return {}
    rows = got.get("rows") if isinstance(got, dict) else None
    if not isinstance(rows, dict):
        return {}
    return {str(k): v for k, v in rows.items() if isinstance(v, dict)}


def _seq_of(entry):
    try:
        return int((entry or {}).get(F_SEQ, 0))
    except (TypeError, ValueError):
        return 0


def note(tid, verdict, root=None):
    """Положить исход суда в реестр. → запись | `None` («не записали»).

    Зовётся на КАЖДОЕ заявленное `done`, независимо от режима: реестр отвечает на вопрос «что
    судья сказал», а режим — на вопрос «что с этим сделали со статусом». Смешай их — и в режиме
    `addr` след безадресного закрытия снова стал бы невидимым, ровно как до 02.09.

    Не поднимает исключений ни одной веткой (см. шапку, третий пункт §«РЕЕСТР»)."""
    if not verdict:
        return None
    try:
        rows = read_ledger(root)
        top = 0
        for one in rows.values():
            top = max(top, _seq_of(one))
        entry = {F_PROVED: verdict.get("verdict") == DONE,
                 F_ADDRESSED: bool(verdict.get("address")),
                 F_REASON: str(verdict.get("reason") or "")[:REASON_MAX],
                 F_SEQ: top + 1}
        rows[str(tid)] = entry
        if len(rows) > LEDGER_MAX:
            keep = sorted(rows.items(), key=lambda pair: _seq_of(pair[1]))[-LEDGER_MAX:]
            rows = dict(keep)
        data = json.dumps({"schema_version": LEDGER_SCHEMA, "rows": rows},
                          ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8")
        path = ledger_path(root)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Через `.tmp` + `os.replace`: оборванная запись не оставляет полуреестра, и читатель
        # никогда не видит файл в середине правки.
        with open(path + ".tmp", "wb") as handle:
            handle.write(data)
        os.replace(path + ".tmp", path)
        return entry
    except Exception:                                          # noqa: BLE001 — см. докстринг
        return None
