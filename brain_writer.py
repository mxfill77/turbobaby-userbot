# -*- coding: utf-8 -*-
"""
brain_writer.py — ДОВЕРЕННЫЙ ПИСАТЕЛЬ в мозг (Brain-доки) с ПК. Образец — VPS-cclog.

ЗАЧЕМ (класс 328, «утренний ASK про пульс»). Одноразовые скрипты записи в мозг сами читали
конфиг с секретами Bridge — а это доктринально КРАСНОЕ по гарду ПК: каждая дозапись строки в
док превращалась в ASK владельцу. Разрыв закрывается разделением ролей: секреты Bridge берёт
ТОЛЬКО этот модуль (сам процесс, из окружения или конфига репо), а агентские скрипты секретов
не читают и в коде не видят — они зовут функцию/CLI писателя. Гард держит канал зелёным ПО
ИМЕНИ модуля (_RE_SAFE_SCRIPTS в pretool_guard), содержимое не сканируется — ровно как у
cowork_log_append/dispatch_notify.

ИНТЕРФЕЙС — «дописать в док по file id (или имени манифеста)»:
  функция  brain_writer.append(text, doc_id=… | name=…, [anchor=…, place=…, require_above=…])
  движок   brain_writer.apply(mutate, …) — для одноразовых скриптов со СВОЕЙ якорной логикой
           (mutate: старый текст → новый | None=no-op; сам скрипт секретов не касается)
  чтение   brain_writer.read_text(doc_id=… | name=…)
  адрес    brain_writer.resolve_name("KB_INFRA") → ({"name":"infra"}, …) — ОДНО правило канона
           (casefold + долой «KB_» + разделители в «_») поверх ЖИВОГО реестра; включается само
           на промахе имени, поэтому рамка/инструкции/код зовут доки ИХ именами и попадают
           в живые доки. Разбор механизма — в разделе «разрешение имён» ниже
  создание brain_writer.create_plain(name="KB_имя", key="ключ_манифеста", text=…) — НОВЫЙ док
           в папке Brain И регистрация в реестре одним вызовом (канал дозаписи доки не создаёт)
  CLI      venv/Scripts/python.exe brain_writer.py (--id FILE_ID | --name ДОК) [ключи] "текст"
           текст «-» или пустой → читается из stdin (многострочные блоки, ЯВНО UTF-8)
           --probe → только чтение: длина и голова дока, ничего не пишем
           --name KB_имя --create ключ → создать док и зарегистрировать («-» = без регистрации)

FAIL-SAFE ВСЕЙ ЗАПИСИ (как у kb_master_append_2307, теперь встроено в канал):
  • адресация: РОВНО один из doc_id/name; Bridge принимает и id=, и name= (проба 23.07,
    см. trainer_log) — но создавать доки канал НЕ умеет, только перезаписывать существующие;
  • якорная вставка: --anchor обязан найтись РОВНО один раз (re.MULTILINE), --require-above
    обязан стоять ВЫШЕ точки вставки; не сошлось → НЕ пишем (остановка лучше записи мимо);
  • идемпотентность: дописываемый текст уже в доке → no-op, повтор ничего не задваивает;
  • бэкап: старый текст целиком ложится в tmp/brain_backup_<тег>_<штамп>.txt ДО записи
    (откат = запись этим текстом);
  • ГАРД УСЫХАНИЯ (класс 17.07.2026, образец VPS cclog.py:200-203): пустой текст от моста —
    это ОТКАЗ ЧТЕНИЯ, а не пустой док (тот же {"ok":true,"text":""} приходит и при сорванном
    чтении), а новый текст короче старого не пишется вовсе; осознанное сокращение объявляется
    явно — apply(…, allow_shrink=True);
  • ОТВЕТ МОСТА ФАКТОМ ЗАПИСИ НЕ ЯВЛЯЕТСЯ (класс, 01.08.2026): тело POST-а Apps Script отдаёт
    вторым плечом (`/exec` → 302 → `googleusercontent`), и падение ЭТОГО плеча неотличимо от
    падения первого — а мутация к тому моменту уже зафиксирована. Живой случай: HTTP 404 при
    ЛЁГШЕЙ записи. Поэтому отказ моста не обрывает проверку: судит ОБРАТНОЕ ЧТЕНИЕ, а «не
    записано» говорится ТОЛЬКО когда док прочитан и не изменился (см. BrainWriterError.written);
  • ОБРАТНОЕ ЧТЕНИЕ: после записи док читается заново, текст сверяется ДОСЛОВНО, маркер
    считается (ровно 1), СВЕРЯЕТСЯ ДЛИНА (наличие строки не отличает «легло целиком» от
    «легло вместо дока»), возвращается фрагмент вокруг вставки — FACT, не «наверное»;
  • гард тестового контекста: под гейтом/юнитами (log_setup.is_test_context) живой док не
    трогаем, если транспорт не инжектирован мок-тестом — урок инцидента KB_trainer_log 23.07
    (гейт залил 30 фикстурных строк в живой док).

Секреты не печатаем и наружу не отдаём; в исключениях и выводе — только имена/длины.
"""
import os
import re
import sys
import json
import time
import datetime
import urllib.request
import urllib.parse

import bridge_http   # durable-транспорт: ручной обход редиректа моста (класс 02.08.2026)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
BACKUP_DIR = os.path.join(BASE_DIR, "tmp")
HTTP_TIMEOUT = 30
# Запас на сверке длины обратного чтения: Docs при clear()+setText() нормализуют хвостовой
# перевод строки, поэтому строгое равенство давало бы ложный отказ на ±1 символ. Усечение
# (ради которого сверка и заведена) на порядки крупнее этого запаса.
_READBACK_SLACK = 2
# Символ-замена U+FFFD — след побитой кодировки (кириллица, приехавшая мусором). Задаём КОДОМ, а
# не литералом: литерал в исходнике сам первый кандидат на перекодировку и в диффе неотличим от мусора.
_FFFD = chr(0xFFFD)


class BrainWriterError(RuntimeError):
    """Отказ канала с честной причиной; .code — код выхода CLI (1 конфиг/аргументы,
    2 чтение, 3 якорь, 4 запись, 5 верификация обратным чтением, 6 гард усыхания).

    .written — СУДЬБА ЗАПИСИ, а не причина отказа, и врать ей нельзя:
      False — проверено, что в доке ничего не изменилось (повтор безопасен). Это дефолт,
              потому что все отказы ДО POST-а (конфиг, адрес, чтение, якорь, гард усыхания)
              именно таковы;
      None  — исход НЕ ПОДТВЕРЖДЁН: POST ушёл, а расписка/обратное чтение не дали факта.
              Слепой повтор здесь запрещён — там, где нет идемпотентности, он даёт дубль.
    Разделение заведено 01.08.2026: CLI печатал «НЕ ЗАПИСАНО» на любом отказе, в том числе на
    том, где запись ЛЕГЛА (живой случай — задача 166, HTTP 404 при легшем блоке)."""
    def __init__(self, msg, code=1, written=False):
        super().__init__(msg)
        self.code = code
        self.written = written


# ------------------------------- конфиг (секреты) ----------------------------

def _env_file(path=None):
    """Конфиг репо построчно (K=V, как cowork_log_append/trainer_log). Ошибок не бросает."""
    vals = {}
    try:
        with open(path or ENV_PATH, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return vals


def _config(env=None):
    """BRIDGE_URL/BRIDGE_TOKEN: окружение процесса → конфиг репо. env — инъекция тестов."""
    if env is not None:
        return env.get("BRIDGE_URL"), env.get("BRIDGE_TOKEN")
    vals = _env_file()
    url = (os.environ.get("BRIDGE_URL") or vals.get("BRIDGE_URL") or "").strip()
    token = (os.environ.get("BRIDGE_TOKEN") or vals.get("BRIDGE_TOKEN") or "").strip()
    return url, token


def _in_test_context():
    """Идёт тестовый прогон? Точка правды репо — log_setup.is_test_context (как trainer_log)."""
    try:
        from log_setup import is_test_context
        return bool(is_test_context())
    except Exception:
        return False


# ------------------------------- транспорт Bridge ----------------------------

# ХОДИМ ЧЕРЕЗ bridge_http, А НЕ ГОЛЫМ urlopen (класс 02.08.2026, разбор docs/artifacts/
# 2026-08-02-bridge-receipt-leg-not-token.md). Мост отвечает 302 на второе плечо, а голый
# `urlopen` идёт по нему сам и переигрывает POST как GET без тела — расписку на запись выдавал
# `doGet`. Форма ручного обхода взята с VPS (`bridge_client._exchange`). Контракт прежний:
# разобранный JSON либо исключение, поэтому `_retry_read`, обратное чтение и коды отказов
# ниже работают как работали. opener — точка инъекции тестов (как get/post-параметры).

def _get(url, params, opener=None):
    # read_doc живёт в doGet Bridge → GET, токен в query, в логи/вывод не попадает
    return bridge_http.request_json(url, "GET", params=params, timeout=HTTP_TIMEOUT, opener=opener)


# Повтор ЧИСТОГО ЧТЕНИЯ (образец cowork_log_append.with_retry). Заведён под разрешение имени:
# оно добавляет запрос `list_brain` на путь записи, а мост сегодня регулярно таймаутит (замер
# 01.08.2026: 5 транспортных отказов на 27 проб). Без повтора флап моста превратился бы из
# «повисло чтение» в «имя не резолвится» — новый режим отказа на ровном месте. Повторяем ТОЛЬКО
# GET-чтения: они идемпотентны; записи не повторяем никогда.
_READ_TRIES = 3
_READ_PAUSE = 1.5


def _retry_read(call):
    last = None
    for i in range(_READ_TRIES):
        try:
            return call()
        except Exception as e:
            last = e
            if i + 1 < _READ_TRIES:
                time.sleep(_READ_PAUSE * (i + 1))
    raise last


def _post(url, payload, opener=None):
    return bridge_http.request_json(url, "POST", payload=payload, timeout=HTTP_TIMEOUT,
                                    opener=opener)


def _doc_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and isinstance(obj.get(key), str):
            return obj[key]
    return None


def _ref(doc_id, name):
    """→ (params-часть адресации, человекочитаемый ref). РОВНО один из doc_id/name."""
    doc_id, name = (doc_id or "").strip(), (name or "").strip()
    if bool(doc_id) == bool(name):
        raise BrainWriterError("нужен РОВНО один адрес дока: doc_id (--id) ЛИБО name (--name)", 1)
    return ({"id": doc_id}, "id:" + doc_id) if doc_id else ({"name": name}, "name:" + name)


def _read(url, token, addr, ref, get, env=None, resolve=True):
    """→ (текст, addr, ref). АДРЕС ВОЗВРАЩАЕТСЯ ВМЕСТЕ С ТЕКСТОМ, потому что он мог измениться
    разрешением имени (ниже). Иначе apply() прочитал бы один док, а write_doc ушёл бы по
    старому адресу — правка легла бы мимо прочитанного."""
    try:
        r = (get or _get)(url, dict(addr, action="read_doc", token=token))
    except Exception as e:
        raise BrainWriterError("read_doc(%s) упал: %s: %s" % (ref, type(e).__name__, e), 2)
    # РАЗРЕШЕНИЕ ИМЕНИ — ровно здесь и только на промахе: пока мост имя знает, канал работает
    # как работал (ни лишнего запроса, ни смены поведения). Промах — единственный момент, когда
    # правило канона обязано включиться; повтор ровно один (resolve=False), петли нет.
    if (isinstance(r, dict) and not r.get("ok") and r.get("error") == "unknown_name"
            and resolve and addr.get("name")):
        try:
            addr2, ref2, _note = resolve_name(addr["name"], env=env, get=get)
        except BrainWriterError as e:
            if getattr(e, "verdict", False):
                raise                       # вердикт ПРО ИМЯ (мёртвый адрес / не разрешено) — он и есть ответ
            addr2 = addr                    # реестр не прочитался — отдаём исходный честный unknown_name
        if addr2 != addr:
            return _read(url, token, addr2, ref2, get, env=env, resolve=False)
    if not (isinstance(r, dict) and r.get("ok")):
        raise BrainWriterError("read_doc(%s) не ok: %s"
                               % (ref, json.dumps(r, ensure_ascii=False)[:300]), 2)
    text = _doc_text(r)
    if text is None:
        raise BrainWriterError("read_doc(%s) ok, но текста нет — НЕ пишу, чтобы не затереть" % ref, 2)
    # Пустой текст = ОТКАЗ ЧТЕНИЯ, а не пустой док: тот же ответ {"ok":true,"text":""} мост отдаёт
    # и при сорванном чтении, а канал доки не создаёт — только перезаписывает существующие.
    # Проверка стоит ЗДЕСЬ, поэтому закрывает и первое чтение, и обратное.
    if not text.strip():
        raise BrainWriterError("read_doc(%s) вернул ПУСТОЙ текст (%d символов) — считаю это ОТКАЗОМ "
                               "ЧТЕНИЯ, а не пустым доком: писать поверх значит затереть"
                               % (ref, len(text)), 2)
    return text, addr, ref


def read_text(doc_id="", name="", env=None, get=None, resolve=True):
    """Текст дока (read-only) или BrainWriterError. Секретов не раскрывает.

    resolve=False — «как было до переводчика»: имя уходит на мост КАК НАПИСАНО, и промах
    остаётся промахом. Заведено под замер регресса: обе полосы (сырой канал / с переводчиком)
    меряются ОДНИМ процессом на одном и том же живом мосту — иначе флап моста достаётся
    только одной из них, и число, ради которого замер делается, врёт."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    addr, ref = _ref(doc_id, name)
    return _read(url, token, addr, ref, get, env=env, resolve=resolve)[0]


def _norm_eol(s):
    """Текст для ДОСЛОВНОЙ сверки: перевод строки к «\\n», хвостовые «\\n» долой. Ровно та же
    поблажка, ради которой у apply() живёт _READBACK_SLACK, — Drive/Docs нормализуют хвост."""
    return s.replace("\r\n", "\n").rstrip("\n")


def create_plain(name, key="", text="", env=None, post=None, get=None):
    """Создать НОВЫЙ plain-док в папке Brain (Bridge-экшен create_brain_plain) и, если задан key,
    зарегистрировать его в манифесте — после этого док адресуется по имени, как остальные.

    Зачем отдельной функцией: канал дозаписи (append/apply) доки СОЗДАВАТЬ не умеет, только
    перезаписывать существующие. А создавать их скриптом-однодневкой нельзя — он читал бы
    BRIDGE_TOKEN сам (запрет класса 328). Значит создание живёт здесь, у доверенного писателя:
    секреты берёт этот модуль, вызывающий их не видит.

    ОБРАТНОЕ ЧТЕНИЕ обязательно, как у apply(): «мост ответил ok» фактом записи не является.
    Отличие в ЦЕНЕ ошибки — создание НЕидемпотентно (Drive держит одноимённые файлы спокойно),
    поэтому каждый отказ ниже НАЗЫВАЕТ id уже созданного файла: слепой повтор дал бы ДУБЛЬ.
    → dict ответа Bridge (ok, id, …) + verified/chars_back/fffd."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    if not (name or "").strip():
        raise BrainWriterError("нужно имя нового дока", 1)
    if (post is None or get is None) and _in_test_context():
        raise BrainWriterError("тестовый контекст (гейт/юнит): живой Brain НЕ трогаем — "
                               "инжектируй get/post мок-тестом", 1)
    # ГАРД МОХИБЕЙКА — на ВХОДЕ, а не на выходе: обратное чтение сверяет отправленное с легшим и
    # честно подтвердит побитый текст, если он приехал побитым УЖЕ к нам (локаль-декод stdin,
    # класс 29–30.07). Единственное место, где мусор ещё можно не пустить в мозг, — здесь.
    if _FFFD in text:
        raise BrainWriterError("в тексте %d символ(ов) U+FFFD: вход уже побит кодировкой — "
                               "в мозг такое не кладём, перекодируй источник" % text.count(_FFFD), 1)
    payload = {"action": "create_brain_plain", "token": token, "name": name, "text": text}
    if key:
        payload["key"] = key
    try:
        r = (post or _post)(url, payload)
    except Exception as e:
        # ТОТ ЖЕ КЛАСС, что у apply(): отказ мог прийти со второго плеча Apps Script, когда файл
        # УЖЕ создан. Прочитать назад нечего — id приходит только с распиской, — поэтому здесь
        # честный ответ ровно один: «не знаю». Цена ошибки выше, чем у append: создание
        # НЕидемпотентно, слепой повтор кладёт в папку Brain второй файл тем же именем.
        raise BrainWriterError("create_brain_plain(%s) — расписка не дошла (%s: %s): ИСХОД "
                               "НЕИЗВЕСТЕН, файл мог уже лечь в папку Brain. Посмотри папку/реестр; "
                               "СЛЕПОЙ ПОВТОР ДАСТ ДУБЛЬ" % (name, type(e).__name__, e), 4,
                               written=None)
    if not (isinstance(r, dict) and r.get("ok")):
        raise BrainWriterError("create_brain_plain(%s) не ok: %s"
                               % (name, json.dumps(r, ensure_ascii=False)[:300]), 4)
    new_id = str(r.get("id") or "").strip()
    if not new_id:
        raise BrainWriterError("create_brain_plain(%s): ответ ok, но БЕЗ id — что легло в папку "
                               "Brain, отсюда не видно; повтор создаст ДУБЛЬ" % name, 4)
    if not text.strip():
        return dict(r, verified=False)   # пустой док читать назад нечем: пустой ответ = отказ чтения
    try:
        back = _read(url, token, {"id": new_id}, "id:" + new_id, get, env=env)[0]
    except BrainWriterError as e:
        raise BrainWriterError("%s; но файл УЖЕ СОЗДАН (id %s) — проверь его, повтор создаст ДУБЛЬ"
                               % (e, new_id), e.code, written=None)
    if _norm_eol(back) != _norm_eol(text):
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (id:%s): текст в доке НЕ совпал дословно "
                               "(отправляли %d символов, в доке %d) — файл создан, повтор даст ДУБЛЬ"
                               % (new_id, len(text), len(back)), 5, written=None)
    # fffd в ответе — ЗАМЕР по живому файлу (после гарда входа обязан быть 0): отчёт владельцу
    # держится на числе, а не на «наверное, кодировка не побилась».
    return dict(r, verified=True, chars_back=len(back), fffd=back.count(_FFFD))


# ------------------------------- реестр (BRAIN_MANIFEST) ---------------------

def list_brain(env=None, get=None):
    """Манифест Brain ЦЕЛИКОМ (ключ → file id) — GET list_brain. ТОЛЬКО ЧТЕНИЕ.

    Зачем здесь, а не в разовом скрипте: реестр живёт в Script Properties проекта Apps Script
    (`BRAIN_MANIFEST`) и наружу отдаётся ЕДИНСТВЕННЫМ экшеном моста; читать его скриптом-однодневкой
    значит читать BRIDGE_TOKEN самому — запрет класса 328. → dict манифеста."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    try:
        r = _retry_read(lambda: (get or _get)(url, {"action": "list_brain", "token": token}))
    except Exception as e:
        raise BrainWriterError("list_brain упал (%d попытки): %s: %s"
                               % (_READ_TRIES, type(e).__name__, e), 2)
    if not (isinstance(r, dict) and r.get("ok") and isinstance(r.get("manifest"), dict)):
        raise BrainWriterError("list_brain не ok: %s"
                               % json.dumps(r, ensure_ascii=False)[:300], 2)
    return r["manifest"]


# ------------------------------- разрешение имён ------------------------------
#
# ПОЧЕМУ ИМЕНА НЕ РЕЗОЛВИЛИСЬ (механизм, снят пробой 01.08.2026, не гипотеза).
# В системе ДВА словаря имён, и между ними не было ни одного преобразователя:
#   • КЛЮЧИ РЕЕСТРА — то единственное, что понимает мост: `index`, `infra`, `cc_userbot_log`…
#     Совпадение ТОЧНОЕ и РЕГИСТРОЗАВИСИМОЕ (проба 01.08: `infra` → OK 6 702 симв.; `INFRA` и
#     `Infra` → unknown_name). НИ ОДИН ключ живого реестра не содержит ни заглавной буквы, ни
#     префикса `KB_`, и содержать не может: мост валидирует ключ как [a-z0-9_].
#   • ИМЕНА ДОКУМЕНТОВ — то, чем их зовут рамка Штаба, инструкции и код: `KB_MASTER`,
#     `KB_INFRA`, `KB_NORTH_STAR`. Это ТИТУЛЫ файлов Drive, а не ключи.
# Отсюда замер «13 из 14 unknown_name»: доки живы, спрашивали их в чужом словаре. Ни «мёртвые
# документы», ни «изменившиеся заголовки», ни «сломанный реестр» тут ни при чём.
#
# ПОЧЕМУ ПРАВИЛО ЖИВЁТ НА ПК. Ключ на мосту валидируется как [a-z0-9_] — ключа `KB_MASTER` там
# не может быть в принципе, никакой алиас в реестре титул Drive не примет. Значит превращать
# титул в ключ обязан ПК — и делать это в ЕДИНСТВЕННОМ легальном канале к мозгу, через который
# проходит каждое обращение по имени.
#
# ПРАВИЛО ОДНО (канон имени), а не таблица исключений:
#   имя → casefold → всё не [a-z0-9] в «_» → долой ведущий «kb_» → сопоставить с ключами ЖИВОГО
#   реестра, приведёнными К ТОМУ ЖЕ канону; совпадение обязано быть РОВНО ОДНО.
# Добор к правилу — то же сопоставление по ГРАНИЦЕ ТОКЕНА (`userbot_log` ↔ `cc_userbot_log`,
# `claude_review_archive` ↔ `review_archive`), тоже с требованием единственности.
#
# ЧЕГО ЗДЕСЬ СОЗНАТЕЛЬНО НЕТ — словаря «имя → file id». Переименование (титул `KB_MASTER` при
# ключе `index`) и «файл лежит вне реестра» лексикой не выводятся: это ДАННЫЕ той же природы,
# что реестр. Их место — РЕЕСТР НА ПРОДЕ, а не второй словарь в коде, который разойдётся с ним
# молча. Путь туда открыт и проверен: орфан `KB_NORTH_STAR` (жил вне реестра с 03.07)
# зарегистрирован 01.08.2026 под ключом `north_star` — и с этого момента ловится ТЕМ ЖЕ
# правилом канона, без единой строки данных здесь. Так же закрыты (01.08.2026, живой мост):
#   • `KB_MASTER` — ВТОРОЙ ключ `master` на файл карты, `index` цел и не трогался. Два ключа на
#     один файл — штатный вид реестра, а не хак: `faq`/`turbobaby_faq` живут так изначально.
#     Отсюда же: считать доки по числу ключей нельзя — 31 ключ, 29 файлов;
#   • `KB_trainer_log` — ключ `trainer_log` на id из боевого сайдкара `trainer_log_doc.json`.
#
# ЧЕГО РЕГИСТРАЦИЯ НЕ МОЖЕТ (замер 01.08, ответ моста): файл ВНЕ папки Brain ключа не получает —
# `register_brain_doc` отдаёт `not_in_brain` (и заодно `title`, чем годится как проба места).
# Так отказал живой `roadmap_v2` (8 745 симв., лежит в архивной подпапке): его лечение —
# сперва `move_into_brain`, то есть ПЕРЕМЕЩЕНИЕ архивного файла, и это решение владельца, а не
# побочный шаг. Отказ канала тут честен: имя без ключа не выдумывается.
#
# СЛЕДСТВИЕ, КОТОРОЕ НАДО ЗНАТЬ: ключ `master` ловит по границе токена и МЁРТВОЕ имя
# `roadmap_master` (файл удалён из Drive 27.06, содержимое перенесено в карту) — оно теперь
# ведёт в карту, то есть в своего преемника. Это принято сознательно и закреплено юнитом
# (`test_dead_name_lands_on_its_successor_and_this_is_deliberate`), а не случилось молча.
#
# ОСТОРОЖНО, КЛАСС (01.08.2026): список экшенов, который мост отдаёт в ответ на неизвестный
# action, НЕПОЛОН. `--probe-actions` возвращает 62 имени, и `register_brain_doc` среди них НЕТ —
# а вызов проходит и реестр правит (живая проба: ok, registered, доков стало 29 + folder_id).
# Отсюда родился ложный вывод «реестр с этой стороны не правится вообще»
# (docs/artifacts/2026-08-01-brain-name-resolution-mechanism.md §1.5), из-за которого адреса чуть
# не переехали в код таблицей. Возможности прода судить ВЫЗОВОМ, а не списком.

# Разделители канона — всё, что не буква и не цифра. Класс задан через \W (юникод-осознанный),
# а НЕ через [^a-z0-9]: последний стёр бы кириллическое имя в пустую строку, и честный вердикт
# «такого ключа нет» подменился бы бессмысленным «имя не содержит ни буквы» (поймано юнитом).
_RE_NOT_CANON = re.compile(r"\W+", re.UNICODE)


def canon_name(name):
    """КАНОН имени дока — единственное правило разрешения имён (разбор механизма см. выше).
    `KB_INFRA`, `kb-infra`, `KB_Infra` → `infra`. Пустая строка = имя нераспознаваемо."""
    s = _RE_NOT_CANON.sub("_", (name or "").strip().lower()).strip("_")
    return s[3:] if s.startswith("kb_") else s


def _verdict(msg, code=2):
    """Отказ ПРО ИМЯ (а не про транспорт/реестр): такой вердикт вызывающий обязан показать
    как есть, потому что он и есть ответ «что с этим именем». Отказ реестра — не вердикт:
    на нём канал откатывается к исходному честному `unknown_name` моста."""
    e = BrainWriterError(msg, code)
    e.verdict = True
    return e


def _token_tail(a, b):
    """Совпадение по ГРАНИЦЕ ТОКЕНА в любую сторону: `userbot_log` ↔ `cc_userbot_log`,
    `claude_review_archive` ↔ `review_archive`. Голая подстрока не считается — иначе `log`
    «совпал» бы с половиной реестра, а спрос на уникальность потерял бы смысл."""
    return a.endswith("_" + b) or b.endswith("_" + a)


def resolve_name(name, env=None, get=None, manifest=None):
    """ИМЯ → АДРЕС по правилу канона. → (addr, ref, note): addr — готовая адресация для моста
    ({"name": ключ реестра}), ref — человекочитаемо, note — чем разрешилось.

    Порядок: точный ключ реестра → канон-совпадение → совпадение по границе токена → честный
    отказ со списком похожих ключей. ЕДИНСТВЕННЫЙ источник адресов — ЖИВОЙ реестр: имя, которого
    в нём нет, здесь не выдумывается (см. «чего здесь сознательно нет» выше).
    Неоднозначность (совпало 2+) — отказ, а не выбор наугад: угадать чужой док хуже, чем встать."""
    raw = (name or "").strip()
    canon = canon_name(raw)
    if not canon:
        raise _verdict("имя %r не содержит ни буквы, ни цифры — разрешать нечего" % name, 1)
    man = manifest if manifest is not None else list_brain(env=env, get=get)
    keys = [k for k in man if k != "folder_id"]
    if raw in keys:                                  # уже ключ реестра — канал работает как работал
        return {"name": raw}, "name:" + raw, ""
    hits = sorted(k for k in keys if canon_name(k) == canon)
    if not hits:
        hits = sorted(k for k in keys if _token_tail(canon, canon_name(k)))
    if len(hits) == 1:
        return ({"name": hits[0]}, "name:" + hits[0],
                "имя %s → ключ реестра %s (канон %s)" % (raw, hits[0], canon))
    if len(hits) > 1:
        raise _verdict("имя %r (канон %r) подходит СРАЗУ к %d ключам реестра: %s — "
                       "не угадываю, назови ключ точно или адресуй по file id"
                       % (raw, canon, len(hits), ", ".join(hits)), 1)
    near = sorted(k for k in keys if canon[:4] and (canon[:4] in k or canon_name(k)[:4] in canon))
    raise _verdict("ИМЯ НЕ РАЗРЕШЕНО: %r → канон %r. В живом реестре такого ключа нет "
                   "(ключей %d). Похожие ключи: %s. Адресуй по file id (--id), назови ключ "
                   "реестра точно — либо ЗАРЕГИСТРИРУЙ файл под ключом (--register КЛЮЧ=ID), "
                   "и правило канона подхватит имя само"
                   % (raw, canon, len(keys), ", ".join(near) or "нет"), 2)


def register_doc(name, doc_id, overwrite=False, env=None, post=None):
    """POST register_brain_doc: ключ манифеста `name` → СУЩЕСТВУЮЩИЙ файл `doc_id`.

    Мост сам проверяет: файл существует, лежит В папке Brain (иначе not_in_brain), имя ключа —
    [a-z0-9_]; доки не создаёт, текст не трогает, прочие ключи мержит, а не затирает.
    Ответ отдаётся КАК ЕСТЬ, включая ok:false: «not_in_brain»/«exists»/«bad_name» — это ФАКТ
    для отчёта (и заодно проба места файла), а не авария канала."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    name, doc_id = (name or "").strip(), (doc_id or "").strip()
    if not name or not doc_id:
        raise BrainWriterError("нужны и ключ (name), и file id", 1)
    if post is None and _in_test_context():
        raise BrainWriterError("тестовый контекст (гейт/юнит): живой реестр НЕ трогаем — "
                               "инжектируй post мок-тестом", 1)
    payload = {"action": "register_brain_doc", "token": token, "name": name, "id": doc_id}
    if overwrite:
        payload["overwrite"] = True
    try:
        r = (post or _post)(url, payload)
    except Exception as e:
        raise BrainWriterError("register_brain_doc(%s) упал: %s: %s"
                               % (name, type(e).__name__, e), 4)
    if not isinstance(r, dict):
        raise BrainWriterError("register_brain_doc(%s): ответ не JSON-объект" % name, 4)
    return r


def move_into_brain(doc_id, env=None, post=None):
    """POST move_into_brain: перенести существующий KB_*-файл в КОРЕНЬ папки Brain (наружу→внутрь).

    Штатный `move_brain_file` двигает только то, что УЖЕ в Brain, и только в подпапку — поэтому
    живой док, созданный штабом вне папки, реестром не виделся. Ответ отдаётся как есть
    (`not_kb_file`/`already_in_brain` — факты, а не авария)."""
    return _brain_admin_post("move_into_brain", {"id": (doc_id or "").strip()},
                             "move_into_brain(%s)" % doc_id, env, post)


def unregister_doc(name, env=None, post=None):
    """POST unregister_brain_doc: СНЯТЬ ключ из BRAIN_MANIFEST. Операция ОДНОСТОРОННЯЯ —
    вернуть ключ на удалённый файл нельзя (register требует существующий файл), поэтому
    `confirm` мост требует явно и мы посылаем его явно. `folder_id` мост защищает сам."""
    return _brain_admin_post("unregister_brain_doc",
                             {"name": (name or "").strip(), "confirm": True},
                             "unregister_brain_doc(%s)" % name, env, post)


def _brain_admin_post(action, fields, ref, env=None, post=None):
    """Общий транспорт админ-экшенов реестра: секреты берёт сам писатель, ответ — как есть."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    if not all(str(v).strip() for k, v in fields.items() if k != "confirm"):
        raise BrainWriterError("%s: пустой обязательный аргумент" % ref, 1)
    if post is None and _in_test_context():
        raise BrainWriterError("тестовый контекст (гейт/юнит): живой мозг НЕ трогаем — "
                               "инжектируй post мок-тестом", 1)
    try:
        r = (post or _post)(url, dict(fields, action=action, token=token))
    except Exception as e:
        # Тот же класс: реестр мог УЖЕ измениться, а расписка не дойти. Особенно у
        # unregister_brain_doc — операция односторонняя, «упал» тут читался бы как «ключ на месте».
        raise BrainWriterError("%s — расписка не дошла (%s: %s): ИСХОД НЕИЗВЕСТЕН, реестр мог уже "
                               "измениться. Сверься с --list-brain ПЕРЕД повтором"
                               % (ref, type(e).__name__, e), 4, written=None)
    if not isinstance(r, dict):
        raise BrainWriterError("%s: ответ не JSON-объект" % ref, 4)
    return r


def bridge_post_actions(env=None, post=None):
    """Список POST-экшенов ЖИВОГО прода: мост отдаёт его сам в ответ на неизвестный action.
    Нужен, чтобы судить о возможностях канала ПО ФАКТУ прода, а не по локальной копии кода
    (прод закреплён на номере версии, HEAD мог уехать). Ничего не меняет. → list."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    try:
        r = (post or _post)(url, {"action": "__probe_unknown_action__", "token": token})
    except Exception as e:
        raise BrainWriterError("проба экшенов упала: %s: %s" % (type(e).__name__, e), 2)
    if not isinstance(r, dict) or not isinstance(r.get("actions"), list):
        raise BrainWriterError("проба экшенов: в ответе нет списка actions: %s"
                               % json.dumps(r, ensure_ascii=False)[:300], 2)
    return r["actions"]


# ------------------------------- запись (движок) -----------------------------

def _backup(old, tag, backup_dir=None):
    d = backup_dir or BACKUP_DIR
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.-]+", "_", tag or "doc") or "doc"
    path = os.path.join(d, "brain_backup_%s_%s.txt" % (safe, stamp))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(old)
    return path


def apply(mutate, doc_id="", name="", expect=None, marker=None,
          backup_tag="", backup_dir=None, env=None, get=None, post=None, allow_shrink=False):
    """Движок записи: read → mutate(старый текст) → бэкап → write → ОБРАТНОЕ ЧТЕНИЕ.

    mutate: str → str | None (None = no-op, ничего не пишем); ValueError из mutate = «якорь
    не прошёл» — док НЕ тронут. expect — строка, обязанная лечь в док ДОСЛОВНО (сверка по
    живому доку после записи); marker — строка-маркер, обязанная встретиться РОВНО 1 раз.
    allow_shrink — снять ГАРД УСЫХАНИЯ (по умолчанию новый текст короче старого = отказ, код 6).
    Объявляется ЯВНО одноразовыми скриптами, которые режут док осознанно; молчаливое
    укорачивание журнала запрещено — это и есть класс 17.07.
    → dict: status ok|noop, doc, backup, back (текст обратного чтения), before_chars,
    after_chars, fragment.
    Любой отказ — BrainWriterError с честной причиной (см. коды в классе)."""
    url, token = _config(env)
    if not url or not token:
        raise BrainWriterError("нет BRIDGE_URL/BRIDGE_TOKEN в окружении/конфиге", 1)
    addr, ref = _ref(doc_id, name)
    if (get is None or post is None) and _in_test_context():
        raise BrainWriterError("тестовый контекст (гейт/юнит): живой док %s НЕ трогаем — "
                               "инжектируй get/post мок-тестом" % ref, 1)
    # Адрес берём ТОТ, по которому чтение реально состоялось (имя могло разрешиться в ключ
    # реестра или в file id) — и записываем потом ровно по нему.
    old, addr, ref = _read(url, token, addr, ref, get, env=env)
    try:
        new = mutate(old)
    except ValueError as e:
        raise BrainWriterError("ЯКОРЬ НЕ ПРОШЁЛ (%s): %s — док НЕ тронут" % (ref, e), 3)
    if new is None or new == old:
        return {"status": "noop", "doc": ref, "backup": None, "back": old,
                "before_chars": len(old), "after_chars": len(old), "fragment": "",
                "receipt": "none", "receipt_error": None}
    # ГАРД УСЫХАНИЯ (класс 17.07, образец cclog.py:200-203): молча укоротить док нельзя.
    if not allow_shrink and len(new) < len(old):
        raise BrainWriterError("ГАРД УСЫХАНИЯ (%s): новый текст короче старого (было %d символов, "
                               "стало бы %d) — НЕ пишу; осознанное сокращение объявляется "
                               "allow_shrink=True" % (ref, len(old), len(new)), 6)
    backup = _backup(old, backup_tag or (name or doc_id), backup_dir)
    # ОТВЕТ МОСТА — НЕ ФАКТ ЗАПИСИ (класс, живой случай 01.08.2026: задача 166 — HTTP 404, а блок
    # в KB_MASTER ЛЁГ; дубля не возникло только потому, что append идемпотентен).
    # МЕХАНИЗМ, не гипотеза. Тело POST-а Apps Script отдаёт ВТОРЫМ ПЛЕЧОМ (`/exec` → 302 → echo),
    # и падение ЭТОГО плеча приходит сюда тем же исключением, что и падение первого. А мутация к
    # тому моменту уже зафиксирована: writeDoc_ зовёт brainTextWrite_ и лишь ПОТОМ собирает
    # {"ok":true} (копия прода — tmp/bridge_v75/ReadDocs.js). Значит код ответа описывает доставку
    # РАСПИСКИ, а не судьбу записи; замер флакости — docs/artifacts/2026-07-28-journal-write-fixes.md §1.
    # С 02.08.2026 второе плечо обходит `bridge_http` (не urlopen): 404/таймаут на нём повторяются,
    # а голым GET на свой же `/exec` мы больше не ходим — но сама развилка «расписка ≠ факт»
    # остаётся, потому что плечо может умереть насовсем уже ПОСЛЕ исполнения записи.
    # ПОЭТОМУ отказ моста больше не короткое замыкание: он ЗАПОМИНАЕТСЯ, а судит ОБРАТНОЕ ЧТЕНИЕ —
    # оно одно отличает «не легло» (повтор безопасен) от «легло, потерялась расписка» (повтор даст
    # дубль там, где идемпотентности нет). Сам POST не повторяем по-прежнему никогда.
    receipt_err = None
    try:
        w = (post or _post)(url, dict(addr, action="write_doc", token=token, text=new))
    except Exception as e:
        receipt_err = "%s: %s" % (type(e).__name__, e)
    else:
        if not (isinstance(w, dict) and w.get("ok")):
            receipt_err = json.dumps(w, ensure_ascii=False)[:300]
    # ОБРАТНОЕ ЧТЕНИЕ — верификация по живому доку, не по локальной склейке (адрес уже
    # разрешён выше; повторно разрешать нечего — resolve=False)
    try:
        back = _read(url, token, addr, ref, get, env=env, resolve=False)[0]
    except BrainWriterError as e:
        # Оба канала факта молчат. Единственный честный ответ — «не знаю», а не «не записано».
        raise BrainWriterError("%s; расписка моста%s. ИСХОД ЗАПИСИ НЕ ПОДТВЕРЖДЁН: судить по коду "
                               "ответа нельзя, а чтение не удалось — посмотри док глазами, СЛЕПОЙ "
                               "ПОВТОР ЗАПРЕЩЁН (бэкап: %s)"
                               % (e, (" — ОТКАЗ (%s)" % receipt_err) if receipt_err else " была ok",
                                  backup), e.code, written=None)
    # «Док не изменился» — единственный признак «запись НЕ ЛЕГЛА», который можно предъявить как
    # факт. Здесь и только здесь повтор безопасен, поэтому так и говорим.
    if _norm_eol(back) == _norm_eol(old):
        if receipt_err is not None:
            raise BrainWriterError("write_doc(%s) не прошёл (%s); ОБРАТНОЕ ЧТЕНИЕ: док не изменился "
                                   "(%d символов) — запись НЕ ЛЕГЛА, повтор безопасен (бэкап: %s)"
                                   % (ref, receipt_err, len(back), backup), 4)
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): мост ответил ok, а док НЕ ИЗМЕНИЛСЯ "
                               "(%d символов) — запись не легла (бэкап: %s)"
                               % (ref, len(back), backup), 5)
    # СВЕРКА ДЛИНЫ. Проверка «expect есть в тексте» НЕ отличает «легло целиком» от «легло ВМЕСТО
    # дока»: усечённый док тоже содержит новую строку. Сравниваем с длиной ДО записи и с длиной
    # отправленного (последнее — с запасом _READBACK_SLACK на нормализацию хвоста в Docs).
    # Ниже док УЖЕ не тот, что был: любой отказ здесь — «записано не то», а не «не записано».
    # Отсюда written=None: слепой повтор поверх нештатного состояния запрещён.
    if not allow_shrink and len(back) < len(old):
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): док УКОРОТИЛСЯ — было %d символов, стало %d "
                               "(бэкап: %s)" % (ref, len(old), len(back), backup), 5, written=None)
    if len(back) < len(new) - _READBACK_SLACK:
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): отправляли %d символов, в доке %d — запись "
                               "легла НЕ ЦЕЛИКОМ (бэкап: %s)"
                               % (ref, len(new), len(back), backup), 5, written=None)
    if expect is not None and expect not in back:
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): текст в доке НЕ найден дословно — "
                               "разберись перед повтором (бэкап: %s)" % (ref, backup), 5, written=None)
    if marker is not None and back.count(marker) != 1:
        raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): маркер встречается %d раз, ожидал 1 "
                               "(бэкап: %s)" % (ref, back.count(marker), backup), 5, written=None)
    probe = marker if marker is not None else expect
    fragment = ""
    if probe:
        i = back.find(probe)
        fragment = back[max(0, i - 200):i + len(expect or probe) + 120]
    # receipt — судьба РАСПИСКИ, а не записи: «lost» значит «мост ответил отказом, а запись легла и
    # подтверждена чтением». Отдаём наружу, чтобы отчёт владельцу назвал это словом, а не молчал.
    return {"status": "ok", "doc": ref, "backup": backup, "back": back,
            "before_chars": len(old), "after_chars": len(back), "fragment": fragment,
            "receipt": "lost" if receipt_err else "ok", "receipt_error": receipt_err}


# ------------------------------- дозапись (интерфейс) ------------------------

def _whole_line_count(body, text):
    """Сколько раз body стоит в text ЦЕЛЬНЫМИ строками (границы — край текста или «\\n»).
    Подстрока внутри чужой строки НЕ считается: «PASS» внутри «PASS 2/2» — не вхождение.
    Иначе идемпотентность давала бы ложный no-op (короткая строка «уже есть» в чужой длинной =
    молча потерянная запись), а сверка — ложный провал на легальном повторе подстроки."""
    n, i = 0, text.find(body)
    while i != -1:
        j = i + len(body)
        if (i == 0 or text[i - 1] == "\n") and (j == len(text) or text[j] == "\n"):
            n += 1
        i = text.find(body, i + 1)
    return n


def append(text, doc_id="", name="", anchor=None, place=None, require_above=None,
           backup_tag="", backup_dir=None, env=None, get=None, post=None):
    """ДОЗАПИСАТЬ текст в док по file id (или имени манифеста). → результат apply().

    Без anchor: place «top» (дефолт, новое сверху — стиль cowork_log) или «bottom».
    С anchor (regex, re.MULTILINE, обязан найтись ровно 1 раз): place «before» (дефолт) или
    «after» — вставка отдельным абзацем (пустая строка до и после). require_above — подстрока,
    обязанная существовать ВЫШЕ точки вставки (страховка структуры дока). Идемпотентность и
    обратная сверка считают текст ЦЕЛЬНЫМИ строками (_whole_line_count)."""
    body = (text or "").rstrip("\n")
    if not body.strip():
        raise BrainWriterError("пустой текст — дописывать нечего", 1)
    place = place or ("before" if anchor else "top")
    if anchor and place not in ("before", "after"):
        raise BrainWriterError("с anchor допустимы place=before|after, а не %r" % place, 1)
    if not anchor and place not in ("top", "bottom"):
        raise BrainWriterError("без anchor допустимы place=top|bottom, а не %r" % place, 1)

    def mutate(old):
        if _whole_line_count(body, old):
            return None                      # идемпотентность: уже стоит цельными строками → no-op
        if not anchor:
            if place == "top":
                return body + "\n" + old
            return old + ("" if old.endswith("\n") else "\n") + body
        hits = list(re.finditer(anchor, old, re.MULTILINE))
        if len(hits) != 1:
            raise ValueError("якорь %r найден %d раз (ожидал 1)" % (anchor, len(hits)))
        pos = hits[0].start() if place == "before" else hits[0].end()
        if require_above and require_above not in old[:pos]:
            raise ValueError("выше якоря нет обязательной подстроки %r — вставлять некуда"
                             % require_above)
        if place == "before":
            return old[:pos].rstrip("\n") + "\n\n" + body + "\n\n" + old[pos:]
        return old[:pos] + "\n\n" + body + "\n\n" + old[pos:].lstrip("\n")

    res = apply(mutate, doc_id=doc_id, name=name, expect=body, marker=None,
                backup_tag=backup_tag, backup_dir=backup_dir, env=env, get=get, post=post)
    if res["status"] == "ok":
        cnt = _whole_line_count(body, res["back"])
        if cnt != 1:
            raise BrainWriterError("ОБРАТНОЕ ЧТЕНИЕ (%s): текст лёг %d раз(а) цельными "
                                   "строками, ожидал 1 (бэкап: %s)"
                                   % (res["doc"], cnt, res["backup"]), 5, written=None)
    return res


# ------------------------------- CLI -----------------------------------------

def _out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8"))


def _stdin_text():
    """Текст со stdin ЯВНО как UTF-8. На Windows sys.stdin декодирует ЛОКАЛЬЮ (cp1251), и
    многострочный кириллический блок приезжал бы в мозг мусором — ровно тем U+FFFD, который
    обратное чтение потом и ловит. Симметрично _out(), пишущему через sys.stdout.buffer."""
    raw = getattr(sys.stdin, "buffer", None)
    if raw is None:                                   # stdin подменён (тест, обёртка) — как есть
        return sys.stdin.read()
    try:
        return raw.read().decode("utf-8")
    except UnicodeDecodeError as e:
        raise BrainWriterError("stdin не UTF-8 (%s) — перекодируй вход: класть в мозг битую "
                               "кириллицу нельзя" % e, 1)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="brain_writer",
        description="Доверенный писатель в Brain-док: дозапись по file id/имени с бэкапом, "
                    "якорной вставкой и обратным чтением. Секреты Bridge читает сам.")
    ap.add_argument("--id", dest="doc_id", default="", help="file id дока (Drive)")
    ap.add_argument("--name", default="", help="имя дока в манифесте Bridge (index, cowork_log, …)")
    ap.add_argument("--probe", action="store_true", help="только чтение: длина и голова дока")
    ap.add_argument("--list-brain", dest="list_brain", action="store_true",
                    help="только чтение: весь манифест Brain (ключ → id) как JSON")
    ap.add_argument("--probe-actions", dest="probe_actions", action="store_true",
                    help="только чтение: список POST-экшенов живого прода")
    ap.add_argument("--resolve", default="", metavar="ИМЯ",
                    help="только чтение: во что разрешается имя (KB_INFRA → ключ infra, …)")
    ap.add_argument("--create", default="", metavar="КЛЮЧ",
                    help="СОЗДАТЬ новый plain-док в папке Brain с именем --name и зарегистрировать "
                         "его под ключом КЛЮЧ манифеста ([a-z0-9_]); «-» → создать без регистрации")
    ap.add_argument("--register", default="", metavar="КЛЮЧ=ID",
                    help="зарегистрировать существующий файл Brain-папки под ключом манифеста")
    ap.add_argument("--overwrite", action="store_true",
                    help="с --register: сменить id у уже занятого ключа")
    ap.add_argument("--move-into-brain", dest="move_into_brain", default="", metavar="ID",
                    help="перенести KB_*-файл в КОРЕНЬ папки Brain (наружу→внутрь)")
    ap.add_argument("--unregister", default="", metavar="КЛЮЧ",
                    help="СНЯТЬ ключ из манифеста (односторонне: вернуть на удалённый файл нельзя)")
    ap.add_argument("--anchor", default=None, help="regex якоря (re.MULTILINE), ровно 1 совпадение")
    ap.add_argument("--place", default=None, choices=("top", "bottom", "before", "after"))
    ap.add_argument("--require-above", dest="require_above", default=None,
                    help="подстрока, обязанная стоять выше точки вставки")
    ap.add_argument("--backup-tag", dest="backup_tag", default="", help="тег имени файла бэкапа")
    ap.add_argument("text", nargs="*", help="текст дозаписи; «-» или пусто → из stdin")
    a = ap.parse_args(argv)
    try:
        if a.list_brain:
            man = list_brain()
            _out(json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True))
            _out("КЛЮЧЕЙ ВСЕГО: %d (включая folder_id — это папка, не док)" % len(man))
            return 0
        if a.probe_actions:
            acts = bridge_post_actions()
            _out(json.dumps(acts, ensure_ascii=False))
            _out("POST-экшенов у живого прода: %d" % len(acts))
            return 0
        if a.resolve:
            addr, ref, note = resolve_name(a.resolve)
            _out("%s → %s%s" % (a.resolve, ref, ("  [%s]" % note) if note else "  [ключ реестра]"))
            return 0
        if a.move_into_brain:
            r = move_into_brain(a.move_into_brain)
            _out(json.dumps(r, ensure_ascii=False, sort_keys=True))
            return 0 if r.get("ok") else 4
        if a.unregister:
            r = unregister_doc(a.unregister)
            _out(json.dumps(r, ensure_ascii=False, sort_keys=True))
            return 0 if r.get("ok") else 4
        if a.register:
            if "=" not in a.register:
                raise BrainWriterError("формат --register КЛЮЧ=ID", 1)
            key, rid = a.register.split("=", 1)
            r = register_doc(key.strip(), rid.strip(), overwrite=a.overwrite)
            _out(json.dumps(r, ensure_ascii=False, sort_keys=True))
            return 0 if r.get("ok") else 4
        if a.create:
            body = " ".join(a.text).strip()
            if not body or body == "-":
                body = _stdin_text()
            if not body.strip():
                raise BrainWriterError("пустой текст — создавать пустой док этим ключом не будем", 1)
            r = create_plain(a.name, key=("" if a.create == "-" else a.create), text=body)
            _out(json.dumps(r, ensure_ascii=False, sort_keys=True))
            _out("ОБРАТНОЕ ЧТЕНИЕ OK: отправлено %d символов, в доке %d, U+FFFD: %d"
                 % (len(body), r.get("chars_back", 0), r.get("fffd", 0)))
            return 0
        if a.probe:
            t = read_text(doc_id=a.doc_id, name=a.name)
            _out("PROBE ok: %d символов, строк %d, U+FFFD %d; голова:"
                 % (len(t), t.count("\n") + 1, t.count(_FFFD)))
            _out(t[:600])
            return 0
        text = " ".join(a.text).strip()
        if not text or text == "-":
            text = _stdin_text()
        res = append(text, doc_id=a.doc_id, name=a.name, anchor=a.anchor, place=a.place,
                     require_above=a.require_above, backup_tag=a.backup_tag)
    except BrainWriterError as e:
        # «НЕ ЗАПИСАНО» — УТВЕРЖДЕНИЕ О ФАКТЕ, и печатать его можно только там, где факт проверен
        # чтением (written=False). Раньше строка стояла на любом отказе — и на 404 с легшей
        # записью тоже; ровно она и толкала исполнителя на повтор.
        verdict = ("НЕ ЗАПИСАНО (проверено обратным чтением: док не изменился)."
                   if e.written is False else
                   "ИСХОД ЗАПИСИ НЕ ПОДТВЕРЖДЁН — проверь док глазами, СЛЕПОЙ ПОВТОР ЗАПРЕЩЁН.")
        sys.stderr.buffer.write(("ОШИБКА: %s\n%s\n" % (e, verdict)).encode("utf-8"))
        return e.code
    if res["status"] == "noop":
        _out("уже в доке (%s) — no-op, идемпотентность (док %d символов)"
             % (res["doc"], res["after_chars"]))
        return 0
    _out("записано в %s: %d → %d символов; бэкап: %s"
         % (res["doc"], res["before_chars"], res["after_chars"], res["backup"]))
    if res.get("receipt") == "lost":
        _out("МОСТ ОТВЕТИЛ ОТКАЗОМ (%s) — но потеряна РАСПИСКА, А НЕ ЗАПИСЬ: обратное чтение "
             "подтверждает, что текст в доке. Повторять НЕ НУЖНО." % res["receipt_error"])
    _out("ОБРАТНОЕ ЧТЕНИЕ OK: текст дословно на месте (вхождение 1 раз)")
    _out("--- фрагмент вокруг вставки ---")
    _out(res["fragment"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
