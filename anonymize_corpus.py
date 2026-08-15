# -*- coding: utf-8 -*-
"""
anonymize_corpus.py — обезличивание корпуса живых диалогов (`client_chats.jsonl`).

ЗАЧЕМ. Корпус — 710 диалогов / 31 245 сообщений, роли размечены фактом Telegram
(`docs/artifacts/2026-08-15-client-dialog-corpus-recon.md`). Класть его в тесты, артефакты
или в промпт нельзя: в шапке записи лежат имя, @username, телефон и telegram-id живого
человека, а в тексте — телефоны, номера карт, документы и ГЕОПИНЫ доставки (пин доставки
это домашний адрес). Модуль кладёт РЯДОМ вычищенную копию; исходник не трогает.

ПОЧЕМУ ЗАМЕНА, А НЕ ВЫРЕЗАНИЕ. «Скинь номер» → «<телефон_1>» сохраняет ход разговора,
а дыра его рушит. Метки УСТОЙЧИВЫ ВНУТРИ ДИАЛОГА: одно и то же лицо (имя, @handle, номер,
пин) получает один и тот же ярлык от первой реплики до последней. Между диалогами
устойчивости НЕТ СОЗНАТЕЛЬНО: `Лицо_1` в записи 5 и в записи 300 — разные люди, и связать
их нечем. Соответствие «метка → человек» НЕ ПИШЕТСЯ НИКУДА: словари живут в памяти одного
вызова и умирают вместе с записью.

ЧЕСТНАЯ ГРАНИЦА. Телефон, карта, счёт, документ, геопин, координата, @handle — форма,
ловятся регулярным выражением и проверяемы пересчётом (`--verify`). ИМЯ формы не имеет:
ловится СЛОВАРЁМ (объявленное имя собеседника + газеттир, собранный из имён самого корпуса,
+ встроенный список + звательная/подписная позиция). Что не в словаре и не в звательной
позиции — останется. Доля названа замером в `--verify`, а не обещанием.

ЗАПУСК:
    venv/Scripts/python.exe anonymize_corpus.py                    # чистка + отчёт числами
    venv/Scripts/python.exe anonymize_corpus.py --verify FILE      # ЗАМОК: пересчёт остатка
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict

import io_utf8

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_DEFAULT = os.path.join(HERE, "client_chats.jsonl")
OUT_DEFAULT = os.path.join(HERE, "client_chats.anon.jsonl")

# Виды персонального — ключи счётчиков отчёта. Порядок = порядок в отчёте.
KIND_NAME_HEAD = "имя (шапка записи)"
KIND_USER_HEAD = "@username (шапка)"
KIND_PHONE_HEAD = "телефон (шапка)"
KIND_TGID_HEAD = "telegram-id (шапка)"
KIND_NAME_TEXT = "имя (в тексте)"
KIND_USER_TEXT = "@username (в тексте)"
KIND_PHONE = "телефон (в тексте)"
KIND_CARD = "номер карты / счёта"
KIND_ACCT = "банковский реквизит"
KIND_WALLET = "криптоадрес кошелька"
KIND_DOC = "номер документа"
KIND_GEO = "геопин (ссылка-карта)"
KIND_COORD = "координаты"
KIND_PROFILE = "ссылка на профиль"
KIND_ADDR = "адрес (текстом)"

KINDS = (KIND_NAME_HEAD, KIND_USER_HEAD, KIND_PHONE_HEAD, KIND_TGID_HEAD,
         KIND_NAME_TEXT, KIND_USER_TEXT, KIND_PHONE, KIND_CARD, KIND_ACCT, KIND_WALLET,
         KIND_DOC, KIND_GEO, KIND_COORD, KIND_PROFILE, KIND_ADDR)

# --------------------------------------------------------------------------------------
# ЗАЩИЩЁННЫЙ СЛОВАРЬ. Эти слова пишутся с большой буквы и ИМЕНАМИ НЕ ЯВЛЯЮТСЯ. Без него
# «Макс» (сокращение NMAX, 20 вхождений в корпусе) и «Камала» (район доставки, 659) уехали
# бы под метку лица — и корпус потерял бы ровно то, ради чего он нужен тренажёру.
# --------------------------------------------------------------------------------------
_MODELS = """nmax xmax adv xadv pcx click forza cbr cb xsr ninja mt aerox filano grand scoopy
vario lead zoomer msx rebel crf klx dtracker vespa burgman tmax fino jupiter exciter wave
dream giorno ct125 monkey pcx160 adv160 nmax155 xmax300 forza350 нмакс хмакс адв аэрокс
ниндзя форза клик виспа макс макси байк скутер мопед"""
_PLACES = """phuket kamala rawai patong kata karon naiharn nai harn bangtao bang tao chalong
surin laguna thalang kathu cherngtalay layan maikhao nairang panwa saiyuan thailand bali
samui phangan krabi bangkok pattaya airport town пхукет пхукете пхукета камала камале камалу
камалы раваи патонг ката карон найхарн бангтао банг тао чалонг сурин лагуна таланг кату
чернгталай лаян маикхао панва тайланд таиланд таилана бали самуи пханган краби бангкок
паттайя аэропорт"""
_BRANDS = """honda yamaha suzuki kawasaki bmw ducati telegram whatsapp line google maps apple
instagram facebook youtube usdt bitcoin btc eth trc trx visa mastercard mir тинькофф сбер
сбербанк альфа отп озон райффайзен kbank scb bbl krungthai wise revolut booking airbnb agoda
grab bolt lazada shopee turbobaby turbophuket мир"""
_COMMON = """скидка скидки наши наш наша ваши ваш ваша вам вас вами вы новое поколение
выдача обмениваем качественное отзыв отзывы банк банка если так да нет есть хочу можно
можете здравствуйте привет добрый доброе спасибо пожалуйста извините подскажите цена цены
депозит залог аренда прокат доставка договор паспорт права шлем бензин масло ремонт сервис
итого всего условия правила внимание важно акция бронь бронирование заказ клиент менеджер
компания фирма магазин чат канал бот админ поддержка неделя месяц день дни сутки год
январь февраль март апрель май июнь июль август сентябрь октябрь ноябрь декабрь
понедельник вторник среда четверг пятница суббота воскресенье
деньги денег деньгами оплата оплаты платеж платёж оформление оформления доброй добры
добрые доброго красный зелёный чёрный белый синий"""

PROTECTED = frozenset(w for chunk in (_MODELS, _PLACES, _BRANDS, _COMMON) for w in chunk.split())

# Слова, которые встречаются В ИМЕНАХ АККАУНТОВ, но именами не являются (бизнес-аккаунты
# вида «SEA Medicine Group»): без этого фильтра газеттир утащил бы «Group» и вырезал бы его
# по всему корпусу.
_BIZ = """rent rental bike bikes moto motor scooter group service services shop store market
company co ltd inc agency tour tours travel taxi transfer delivery team club center centre
home house villa hotel resort room car cars auto medicine med dental clinic spa salon cafe
bar restaurant food water gas oil wash repair garage parts sale sales buy sell admin support
manager info help online offical official best top new vip pro plus phuket thai thailand
аренда прокат байк байки байкам мото авто автомобиль скутер доставка сервис ремонт магазин
чат канал бот админ поддержка менеджер компания фирма отель вилла дом такси трансфер тур
туры еда вода бензин или просто недоступен имя объявление реклама"""
BIZ_WORDS = frozenset(_BIZ.split())

# Встроенный список личных имён (RU + распространённые латиницей). Ловит третьих лиц,
# которых корпус собеседниками не объявлял («перевод на … банк Эдуард»).
_RU_NAMES = """александр саша шура алексей лёша леша алёша алеша анатолий толя андрей андрюша
антон артём артем артур борис боря вадим валентин валера валерий василий вася виктор витя
виталий владимир вова володя владислав влад вячеслав слава геннадий гена георгий гоша
григорий гриша даниил данил данила денис дима дмитрий евгений женя егор иван ваня игорь
илья кирилл константин костя лев леонид лёня максим макс марк матвей михаил миша никита
николай коля олег павел паша пётр петр петя роман рома руслан сергей серёжа сережа станислав
стас степан тимофей тимур фёдор федор федя филипп филип эдуард эдик юрий юра ярослав
алла алина анастасия настя анна аня антонина валентина валерия лера вера вероника виктория
вика галина галя дарья даша диана евгения екатерина катя елена лена алёна алена елизавета
лиза жанна инна ирина ира карина кристина ксения ксюша лариса лидия любовь люба людмила люда
маргарита марина мария маша надежда надя наталья наташа нина оксана ольга оля полина раиса
светлана света софия соня тамара татьяна таня ульяна юлия юля яна
алекс никита влад артем даша саня марго ринат ильдар айгуль динара руслана камиль амир
тимур эльвира гульнара рустам загир мурад ахмед магомед зульфия"""
# Латиницей — только то, что НЕ является обычным словом. Тайские прозвища (Bank, May, View,
# Golf, Mint, Oak) и «Max» из списка ВЫНУТЫ СОЗНАТЕЛЬНО: замер корпуса показал 31 диалог с
# «Max» (сокращение NMAX/XMAX) и 6 с «Bank»/«View» — как имена они дали бы ложных замен
# больше, чем верных, а «Макс» и так под защитой моделей.
_EN_NAMES = """alexander alexey andrey andrei anton artem arthur boris daniel denis dmitry
ekaterina evgeny igor ilya irina konstantin ksenia maxim mikhail nikita nikolay natalia
pavel polina roman ruslan sergey stanislav svetlana tatiana vadim valeria victoria vladimir
yulia anastasia arina angelina karina somchai somsak niran preecha"""
BUILTIN_NAMES = frozenset(w for chunk in (_RU_NAMES, _EN_NAMES) for w in chunk.split()) - PROTECTED

# Латинские общие слова, попадающие в имена аккаунтов («See you», «Green», «Shell»).
_LAT_STOP = """see view your with green shell first last next new old big small good best free
open close more less and the for you are was get got can all any one two three real true fast
easy love life time day night sun sea sky top max min pro plus lite full half exchange
red blue black white gold silver freed frees"""

# Основа НЕ из встроенного списка принимается, пока она РЕДКА. Порог измерен по корпусу:
# частотные кандидаты из шапок — «или» 278 диалогов, «просто» 114, «авто» 33, «max» 31,
# «недоступен» 12 — словами не являются вовсе; настоящие имена сидят в хвосте (1-5 диалогов:
# 90 основ из 106). Частые НАСТОЯЩИЕ имена (елена 38, филипп 34, сергей 24, эдуард 24)
# приходят из встроенного списка и порогом не режутся.
GAZ_DIALOG_MAX = 5

# --------------------------------------------------------------------------------------
# ФОРМЫ. Порядок применения в _clean_text значим: ссылка идёт ПЕРВОЙ (в её теле лежат
# и цифры, и заглавные хвосты коротких ссылок — иначе они дробятся другими проходами).
# --------------------------------------------------------------------------------------
_RE_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'«»]+"
                     r"|\b(?:t\.me|goo\.gl|ge0\.me|line\.me|wa\.me|vk\.com|maps\.app\.goo\.gl)/[^\s<>\"'«»]+",
                     re.I)
_GEO_HOSTS = ("maps.app.goo.gl", "goo.gl", "maps.google", "google.com/maps", "maps.apple",
              "ge0.me", "2gis", "yandex.ru/maps", "maps.yandex", "osm.org", "openstreetmap",
              "/maps/", "maps.apple.com")
_PROFILE_HOSTS = ("t.me", "instagram.com", "line.me", "wa.me", "vk.com", "facebook.com",
                  "fb.me", "api.whatsapp.com", "m.me", "onelink.me")
_STAY_HOSTS = ("booking.com", "airbnb.", "agoda.", "hotels.com", "expedia.", "tripadvisor.",
               "resort", "hostel", "hotel")

# Криптоадрес — платёжный идентификатор того же рода, что карта, и в списке видов задачи
# ЕГО НЕТ: добавлен по замеру (26 разных адресов, 77 вхождений в 26 диалогах; один
# повторяется 42 раза — платёжный шаблон компании). Ложные срабатывания стоят дёшево:
# длинный буквенно-цифровой токен осмысленным текстом в чате не бывает.
_RE_WALLET = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b|\b0x[0-9a-fA-F]{40}\b"
                        r"|\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b")
_RE_CARD16 = re.compile(r"(?<![\d])(?:\d{4}[ \-]?){3}\d{4}(?![\d])")
_RE_ACCT_TH = re.compile(r"(?<![\d])\d{3}[ \-]\d[ \-]\d{5}[ \-]\d(?![\d])")
_RE_ACCT_RU = re.compile(r"(?<![\d])\d{20}(?![\d])")
# Реквизит: длинный номер берётся ТОЛЬКО рядом со словом-указателем (ИНН/КПП/БИК/счёт/
# account) — иначе под метку уедут цены и даты. Живой случай корпуса: шаблон счёта на оплату
# и «homeowner account» — номер счёта арендодателя.
_RE_ACCT_KW = re.compile(
    r"(?i:\b(?:инн|кпп|бик|огрн|счет|счёт|account|р/с|к/с|корр\w*"
    r"|scb|kbank|kasikorn|krungthai|bangkok\s+bank|ktb|tmb|uob))"
    r"[^\d]{0,30}(\d[\d\s\-]{7,24}\d)")

# Документ: номер берётся ТОЛЬКО рядом со словом-указателем — иначе под метку уедут цены.
_RE_DOC_KW = re.compile(
    r"(?i:(?:паспорт\w*|passport\w*|виз[аыуе]|visa|внж|id\s*card|ид|driver\w*|license\w*|прав[аох]))"
    r"[^\d\n]{0,25}([A-ZА-Я]{0,2}\s?\d[\d\s\-]{4,14}\d)")
_RE_DOC_SHAPE = re.compile(r"(?<![\w])[A-ZА-Я]{2}\s?\d{7}(?![\d])")

# Телефон: широкий кандидат + проверка ДЛИНОЙ и ПРЕФИКСОМ (см. _phone_ok). Точка в набор
# разделителей НЕ ВХОДИТ намеренно: ей разделены даты, времена работы и координаты.
# Взгляд назад запрещает ТОЛЬКО цифру. Буква или второй плюс впереди («тел+66…», «++66…»)
# блокировать не смеют: тогда движок находил ряд БЕЗ плюса, длина выходила 12, и правило
# «+ → 9–15» до него не доезжало — так уцелел живой международный номер (замер замка).
# Разделитель — ЛЮБОЙ пробельный, кроме перевода строки: живой корпус пишет номера
# неразрывным пробелом U+00A0 (так уцелел международный номер при первом замке), а обычный
# класс `[\d \-()]` его не знает. Перевод строки исключён намеренно — он склеивает соседние
# числа в мнимый «ряд».
_RE_PHONE = re.compile(r"(?<![\d])(\+?\d(?:[\d\-()]|[^\S\r\n]){7,20}\d)(?![\d])")
# Второй проход: СПЛОШНОЙ ряд 9–11 цифр с мобильного префикса. Нужен потому, что жадный
# кандидат выше склеивает номер с соседним числом («2 0812345678» = 11 цифр подряд через
# пробел) и проверка длины бракует склейку целиком, оставляя номер в живых.
_RE_PHONE_SOLID = re.compile(r"(?<![\d])[06789]\d{8,10}(?![\d])")
_RE_PHONE_KW = re.compile(r"(?i:тел|phone|номер|whats|вайбер|viber|line|звон|call|контакт|связ|моб)")

_RE_COORD_PAIR = re.compile(r"(?<![\d.])[+-]?\d{1,3}\.\d{4,8}\s*[,;]\s*[+-]?\d{1,3}\.\d{4,8}(?![\d])")
_RE_COORD_ONE = re.compile(r"(?<![\d.\w])[+-]?\d{1,3}\.\d{5,8}(?![\d])")

_RE_ADDR_SOI = re.compile(r"(?i:\b(?:soi|сой|moo|мубан|дом|house|unit|апарт\w*)\s*\.?\s*№?\s*\d{1,4})\b")
_RE_ADDR_ROOM = re.compile(r"(?i:\b(?:комнат\w*|room|кв\.?|apt\.?)\s*\.?\s*№?\s*[A-ZА-Я]?\s?\d{1,5})\b")
_RE_ADDR_POST = re.compile(r"(?i:phuket|пхукет)[\s,]+\d{5}\b")
_RE_ADDR_NAMED = re.compile(
    r"(?i:\b(?:вилл[аыуе]|villa|отел[ьяюе]|hotel|resort|резорт|кондо|condo|residence|резиденс))"
    r"\s+[«\"']?([A-ZА-ЯЁ][\w'\-]{2,}(?:\s+[A-ZА-ЯЁ][\w'\-]{2,}){0,2})")

_RE_AT = re.compile(r"(?<![\w@/])@([A-Za-z][A-Za-z0-9_]{3,31})")

_RE_GREET = re.compile(
    r"(?i:(?:здравствуйте|здравствуй|привет|доброе\s+утро|добрый\s+день|добрый\s+вечер|"
    r"hi|hello|hey|уважаем\w+))[,!\s]+([А-ЯЁ][а-яё]{2,}|[A-Z][a-z]{2,})\b")
_RE_SIGN = re.compile(
    r"(?i:(?:с\s+уважением|best\s+regards|regards|искренне\s+ваш\w*))[,\s]+"
    r"([А-ЯЁ][а-яё]{2,}|[A-Z][a-z]{2,})\b")

_SENT = "\x01%d\x02"
_RE_SENT = re.compile(r"\x01(\d+)\x02")
# Наши собственные метки: детектор обязан их узнавать, иначе повторный прогон принимает
# «вилла Лицо_1» за новый адрес и замок теряет право говорить «ноль».
_RE_OWN_LABEL = re.compile(r"^(?:Лицо|Клиент|профиль)_\d+$")


# --------------------------------------------------------------------------------------
# Служебное
# --------------------------------------------------------------------------------------

def _digits(s):
    return re.sub(r"\D", "", s)


def _phone_ok(raw, left):
    """Кандидат — телефон? Судим ДЛИНОЙ и ПРЕФИКСОМ, а не «похоже на номер».

    С `+` — международная форма, 9–15 цифр. Без `+` — 9–11 цифр с мобильного префикса
    (0/6/8/9: RU 7|8·10, TH 0·9): реквизиты полосы (ИНН 77…, КПП 77…, БИК 04…) на эти
    префиксы не попадают, а 9 цифр берём ещё и по слову-указателю слева."""
    d = _digits(raw)
    n = len(d)
    if raw.lstrip().startswith("+"):
        return 9 <= n <= 15
    if n in (9, 10, 11) and d[0] in "06789":
        return True
    if d.startswith("00") and 11 <= n <= 15:
        return True                       # международная форма через 00 вместо «+»
    if 9 <= n <= 15 and _RE_PHONE_KW.search(left[-40:]):
        return True                       # «номер тел: 628 1226982991» — код страны без «+»
    return False


def _url_kind(url):
    """Ссылка → вид персонального. Не персональная (сайт компании, магазин) → None."""
    h = re.sub(r"^https?://", "", url, flags=re.I).lower()
    head = h.split("/")[0]
    for g in _GEO_HOSTS:
        if g in h:
            return KIND_GEO
    for p in _PROFILE_HOSTS:
        if head.endswith(p):
            return KIND_PROFILE
    for s in _STAY_HOSTS:
        if s in head:
            return KIND_ADDR
    return None


def _stems(word):
    """Основа имени для сопоставления со склонениями: «Елена» → «елен» (+ хвост)."""
    w = word.lower()
    if len(w) >= 5:
        return w[:-1]
    return w


# Хвосты русских склонений: основа + хвост из этого набора = то же имя. Набор узкий
# СОЗНАТЕЛЬНО — с ним «лев» не утаскивает «левый», а «елен» берёт «Елену/Елене/Еленой».
_ENDINGS = frozenset(("", "а", "ы", "у", "е", "и", "ю", "й", "я", "ь", "ой", "ей",
                      "ом", "ем", "ов", "ым", "ья", "ье", "ку", "ка", "ке"))


def _name_hit(low, names):
    """Слово — известное имя? Точное совпадение либо основа (≥4 буквы) + русский хвост."""
    if low in names:
        return True
    for k in range(len(low) - 1, 3, -1):
        if low[:k] in names and low[k:] in _ENDINGS:
            return True
    return False


class _Labels(object):
    """Устойчивые метки ОДНОГО диалога. Живёт ровно на время обработки записи и умирает
    вместе с ней: соответствие «метка → человек» никуда не пишется."""

    def __init__(self):
        self.maps = {}
        self.counts = Counter()

    def _book(self, kind):
        book = self.maps.get(kind)
        if book is None:
            book = OrderedDict()
            self.maps[kind] = book
        return book

    def label(self, kind, key, tpl):
        book = self._book(kind)
        norm = key.strip().lower()
        got = book.get(norm)
        if got is None:
            self.counts[kind] += 1
            got = tpl % self.counts[kind]
            book[norm] = got
        return got

    def preset(self, kind, key, fixed):
        """Назначить КОНКРЕТНУЮ метку (собеседник записи): в шапке и в тексте он обязан
        называться одинаково, иначе диалог теряет главное лицо."""
        self._book(kind)[key.strip().lower()] = fixed


class _Sink(object):
    """Уже вычищенные куски прячутся за часовым, чтобы следующий проход их не резал."""

    def __init__(self):
        self.parts = []

    def put(self, label):
        self.parts.append(label)
        return _SENT % (len(self.parts) - 1)

    def restore(self, text):
        def back(m):
            return self.parts[int(m.group(1))]
        prev = None
        out = text
        while prev != out:
            prev = out
            out = _RE_SENT.sub(back, out)
        return out


# --------------------------------------------------------------------------------------
# Чистка текста сообщения
# --------------------------------------------------------------------------------------

def _clean_text(text, labels, names, stats):
    """Текст сообщения → вычищенный текст. `names` — набор основ имён этого прогона."""
    sink = _Sink()

    def sub_span(rx, kind, tpl, key_of=None, group=0):
        def rep(m):
            raw = m.group(group)
            key = raw if key_of is None else key_of(raw)
            if key is None:
                return m.group(0)
            stats[kind] += 1
            lab = sink.put(labels.label(kind, key, tpl))
            if group == 0:
                return lab
            return m.group(0)[:m.start(group) - m.start(0)] + lab + m.group(0)[m.end(group) - m.start(0):]
        return rx.sub(rep, out[0])

    out = [text]

    # 1) ссылки — первыми: в их теле и цифры, и заглавные хвосты
    def url_rep(m):
        raw = m.group(0)
        kind = _url_kind(raw)
        if kind is None:
            return raw
        tpl = {KIND_GEO: "<геопин_%d>", KIND_PROFILE: "<профиль_%d>", KIND_ADDR: "<адрес_%d>"}[kind]
        stats[kind] += 1
        return sink.put(labels.label(kind, raw, tpl))
    out[0] = _RE_URL.sub(url_rep, out[0])

    # 2) криптоадреса — ДО телефонов: внутри base58 попадаются девятизначные ряды
    out[0] = sub_span(_RE_WALLET, KIND_WALLET, "<кошелёк_%d>")

    # 3) карты, счета и реквизиты
    for rx in (_RE_CARD16, _RE_ACCT_TH, _RE_ACCT_RU):
        out[0] = sub_span(rx, KIND_CARD, "<карта_%d>")
    out[0] = sub_span(_RE_ACCT_KW, KIND_ACCT, "<реквизит_%d>", group=1)

    # 3) документы (номер — только рядом со словом-указателем)
    out[0] = sub_span(_RE_DOC_KW, KIND_DOC, "<документ_%d>", group=1)
    out[0] = sub_span(_RE_DOC_SHAPE, KIND_DOC, "<документ_%d>")

    # 4) телефоны
    def phone_rep(m):
        raw = m.group(1)
        if not _phone_ok(raw, out[0][:m.start(1)]):
            return m.group(0)
        stats[KIND_PHONE] += 1
        return sink.put(labels.label(KIND_PHONE, _digits(raw), "<телефон_%d>"))
    out[0] = _RE_PHONE.sub(phone_rep, out[0])
    out[0] = sub_span(_RE_PHONE_SOLID, KIND_PHONE, "<телефон_%d>", key_of=_digits)

    # 5) координаты
    out[0] = sub_span(_RE_COORD_PAIR, KIND_COORD, "<координаты_%d>")
    out[0] = sub_span(_RE_COORD_ONE, KIND_COORD, "<координаты_%d>")

    # 6) адрес текстом
    for rx in (_RE_ADDR_POST, _RE_ADDR_SOI, _RE_ADDR_ROOM):
        out[0] = sub_span(rx, KIND_ADDR, "<адрес_%d>")

    def named_ok(raw):
        if raw.split()[0].lower() in PROTECTED or _RE_OWN_LABEL.match(raw):
            return None
        return raw
    out[0] = sub_span(_RE_ADDR_NAMED, KIND_ADDR, "<адрес_%d>", key_of=named_ok, group=1)

    # 7) @handle. Собственный публичный контакт компании (@turbophuket) СОЗНАТЕЛЬНО остаётся:
    # это адрес фирмы, а не персональные данные клиента, и тренажёру он нужен живым.
    def at_ok(raw):
        if raw.lower() in PROTECTED:
            return None
        return raw
    out[0] = sub_span(_RE_AT, KIND_USER_TEXT, "@профиль_%d", key_of=at_ok, group=1)

    # 8) имена — словарём (объявленные + газеттир + встроенные)
    def word_rep(m):
        w = m.group(0)
        low = w.lower()
        if low in PROTECTED:
            return w
        if _name_hit(low, names):
            stats[KIND_NAME_TEXT] += 1
            return sink.put(labels.label(KIND_NAME_TEXT, _stems(w), "Лицо_%d"))
        return w
    out[0] = re.sub(r"\b[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]{2,}\b", word_rep, out[0])

    # 9) имена — позицией (звательная и подпись): ловит то, чего нет в словаре
    def pos_rep(m):
        w = m.group(1)
        if w.lower() in PROTECTED:
            return m.group(0)
        stats[KIND_NAME_TEXT] += 1
        lab = sink.put(labels.label(KIND_NAME_TEXT, _stems(w), "Лицо_%d"))
        return m.group(0)[:m.start(1) - m.start(0)] + lab
    for rx in (_RE_GREET, _RE_SIGN):
        out[0] = rx.sub(pos_rep, out[0])

    return sink.restore(out[0])


# --------------------------------------------------------------------------------------
# Газеттир и запись
# --------------------------------------------------------------------------------------

def name_tokens(peer_name):
    """Личные токены имени аккаунта. Бизнес-слова и защищённый словарь отсеиваются."""
    got = []
    for tok in re.findall(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]{2,}", peer_name):
        low = tok.lower()
        if low in BIZ_WORDS or low in PROTECTED or low in LAT_STOP:
            continue
        if tok.isupper() and len(tok) <= 3:
            continue
        got.append(tok)
    return got


_RE_WORD = re.compile(r"\b[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]{2,}\b")
LAT_STOP = frozenset(_LAT_STOP.split())


def build_gazetteer(records):
    """Имена, объявленные САМИМ корпусом, + встроенный список → основы для сопоставления.

    ОТСЕВ ЧАСТОТОЙ. Имя аккаунта — не обязательно имя человека: «Или», «Просто», «Авто»,
    «Недоступен» приходят из ников и, взятые дословно, режут корпус тысячами замен. Кандидат
    из шапки принимается, только если он РЕДОК в тексте (≤ GAZ_DIALOG_MAX диалогов) — настоящее
    имя третьего лица именно так себя и ведёт. Частотные настоящие имена не теряются: они
    приходят встроенным списком, которому порог не писан.

    Газеттир — производная данных, живёт в памяти прогона и на диск не ложится."""
    cand = set()
    for rec in records:
        pn = rec.get("peer_name")
        if not isinstance(pn, str):
            continue
        for tok in name_tokens(pn):
            low = tok.lower()
            if low not in LAT_STOP:
                cand.add(low)

    seen_in = Counter()
    for rec in records:
        here = set()
        for m in rec.get("messages", []):
            t = m.get("text")
            if not isinstance(t, str):
                continue
            for mt in _RE_WORD.finditer(t):
                low = mt.group(0).lower()
                if low in cand:
                    here.add(low)
        for w in here:
            seen_in[w] += 1

    stems = set()
    for w in BUILTIN_NAMES:
        stems.add(w)
        stems.add(_stems(w))
    for w in cand:
        if seen_in[w] <= GAZ_DIALOG_MAX:
            stems.add(w)
            stems.add(_stems(w))
    # Последний отсев — по ФАКТУ аудита сработавших форм: основа, совпавшая с защищённым
    # словом, бизнес-словом или общим латинским, имя не опознаёт, а корпус портит.
    return stems - PROTECTED - LAT_STOP - BIZ_WORDS


def anonymize_record(rec, ordinal, names, stats):
    """Запись корпуса → обезличенная запись. Возвращает (запись, сколько сообщений изменено)."""
    labels = _Labels()
    out = dict(rec)
    me_name = "Клиент_%03d" % ordinal
    me_user = "@профиль_%03d" % ordinal

    pn = rec.get("peer_name")
    if isinstance(pn, str) and pn.strip():
        stats[KIND_NAME_HEAD] += 1
        for tok in name_tokens(pn):
            labels.preset(KIND_NAME_TEXT, _stems(tok), me_name)
    out["peer_name"] = me_name

    un = rec.get("peer_username")
    if isinstance(un, str) and un.strip():
        stats[KIND_USER_HEAD] += 1
        labels.preset(KIND_USER_TEXT, un.strip().lstrip("@"), me_user)
    # Поле шапки живой корпус хранит БЕЗ «@» («SEAme», не «@SEAme») — метка обязана повторять
    # формат источника, иначе потребитель, склеивающий ссылку, получит «@@…».
    out["peer_username"] = me_user.lstrip("@")

    ph = rec.get("peer_phone")
    if isinstance(ph, str) and ph.strip():
        stats[KIND_PHONE_HEAD] += 1
        labels.preset(KIND_PHONE, _digits(ph), "<телефон_%03d>" % ordinal)
    out["peer_phone"] = ""

    if rec.get("peer_id") is not None:
        stats[KIND_TGID_HEAD] += 1
    out["peer_id"] = ordinal

    # имя и @handle ЭТОГО собеседника — в словарь прогона, чтобы в тексте они получили
    # ту же метку, что и в шапке
    local = set(names)
    if isinstance(pn, str):
        for tok in name_tokens(pn):
            local.add(tok.lower())
            local.add(_stems(tok))

    msgs = []
    changed = 0
    for m in rec.get("messages", []):
        nm = dict(m)
        t = m.get("text")
        if isinstance(t, str) and t:
            new = _clean_text(t, labels, local, stats)
            if new != t:
                changed += 1
            nm["text"] = new
        msgs.append(nm)
    out["messages"] = msgs
    return out, changed


def read_records(path):
    """Корпус → список записей. Битая строка НЕ пропускается молча: разбор падает вслух."""
    recs = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            try:
                recs.append(json.loads(s))
            except ValueError as e:
                raise ValueError("строка %d корпуса не разобрана: %s" % (n, e))
    return recs


def run(src, dst):
    """Чистка корпуса. → (сколько записей, сколько сообщений, сколько изменено, счётчики)."""
    recs = read_records(src)
    names = build_gazetteer(recs)
    stats = Counter()
    total_msgs = 0
    changed = 0
    with open(dst, "w", encoding="utf-8") as out:
        for i, rec in enumerate(recs, 1):
            new, ch = anonymize_record(rec, i, names, stats)
            total_msgs += len(new.get("messages", []))
            changed += ch
            out.write(json.dumps(new, ensure_ascii=False) + "\n")
    return len(recs), total_msgs, changed, stats, len(names)


# --------------------------------------------------------------------------------------
# ЗАМОК: независимый пересчёт остатка. Сети шире, чем у чистильщика, — иначе «ноль»
# доказывал бы сам себя.
# --------------------------------------------------------------------------------------
_V_DIGITRUN = re.compile(r"(?<![\d])[+(]?\d[\d\s\-().]{6,24}\d(?![\d])")
# Карта/счёт по ФОРМЕ ЗАПИСИ: либо сплошной ряд 13–20 цифр, либо группы по 4 (так их и
# пишут). Ряд «### ##########» под неё не подпадает — это счётчик рядом с телефоном.
_V_CARD = re.compile(r"(?<![\d])(?:\d{13,20}|\d{4}(?:[ \-]\d{4}){2,4})(?![\d])")


def luhn_ok(digits):
    """Контрольная сумма Луна — промышленный признак номера КАРТЫ. Даёт замку право
    сказать «карт нет» числом, а не «длинных чисел не осталось»."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return len(digits) >= 13 and total % 10 == 0
_V_LETNUM = re.compile(r"(?<![\w])[A-Za-zА-Яа-яЁё]{1,2}\s?\d{6,9}(?![\d])")
_V_COORD = re.compile(r"(?<![\d.\w])\d{1,3}\.\d{4,8}(?![\d])")
_V_URLFULL = re.compile(r"(?:https?://|www\.)[^\s<>\"'«»]+"
                        r"|\b(?:t\.me|goo\.gl|ge0\.me|line\.me|wa\.me|vk\.com)/[^\s<>\"'«»]+", re.I)
_V_AT = re.compile(r"(?<![\w@/])@[A-Za-z][A-Za-z0-9_]{3,31}")


_RE_ACCT_NEAR = re.compile(r"(?i:инн|кпп|бик|огрн|счет|счёт|account|реквизит)")
_RE_RANGE = re.compile(r"^\(?\d[\d\s]{0,5}\s*[-–]\s*\d[\d\s]{0,6}\)?$")


def classify_run(raw, left):
    """Уцелевший цифровой ряд → чем он является. Классы ЗАКРЫТЫ: всё, что не опознано
    формой, попадает в «неопознанный» и в отчёте называется числом, а не замалчивается."""
    if "\n" in raw or "\r" in raw:
        return "склейка через перенос строки"
    if "." in raw or ":" in raw:
        return "дата / время"
    if _RE_RANGE.match(raw.strip()):
        return "диапазон или перечень (цены, сроки)"
    if _RE_ACCT_NEAR.search(left[-45:]):
        return "реквизит рядом со словом-указателем"
    d = _digits(raw)
    if raw.lstrip().startswith("+") or (len(d) in (9, 10, 11) and d[0] in "06789"):
        return "ФОРМА ТЕЛЕФОНА"
    return "неопознанный ряд (префикс не мобильный)"


def verify(path, names):
    """ЗАМОК из ДВУХ частей.

    Часть A — ПОВТОР ЧИСТКИ по уже вычищенному файлу: тем же детектором, что делал замену.
    Ненулевой счётчик здесь означает пропущенное вхождение (проход не идемпотентен).
    Часть B — ШИРОКАЯ СЕТЬ, заведомо грубее детектора, с полной классификацией остатка:
    «ноль» части A сам себя не доказывает, поэтому всё, что сеть подняла, называется классом.
    """
    again = Counter()
    left = Counter()
    caps_unknown = Counter()
    hosts = Counter()
    shapes = Counter()
    recs = read_records(path)
    for i, rec in enumerate(recs, 1):
        anonymize_record(rec, i, names, again)          # часть A
        for m in rec.get("messages", []):
            t = m.get("text")
            if not isinstance(t, str):
                continue
            for mt in _V_DIGITRUN.finditer(t):          # часть B
                raw = mt.group(0)
                if len(_digits(raw)) < 9:
                    continue
                cls = classify_run(raw, t[:mt.start()])
                left[cls] += 1
                if cls.startswith(("ФОРМА", "неопозн")):
                    shapes["".join("#" if c.isdigit() else c for c in raw)[:26]] += 1
            for mt in _V_CARD.finditer(t):
                d = _digits(mt.group(0))
                if len(d) >= 13:
                    left["ряд 13-20 цифр карточной ЗАПИСИ"] += 1
                    if luhn_ok(d):
                        left["из них ПРОХОДИТ ЛУНА (форма карты)"] += 1
            left["буквы+6-9 цифр (документ)"] += len(_V_LETNUM.findall(t))
            left["дробное N.NNNNN (координата)"] += len(_V_COORD.findall(t))
            left["@handle"] += len(_V_AT.findall(t))
            for mt in _V_URLFULL.finditer(t):
                left["ссылка (любая)"] += 1
                hosts[re.sub(r"^https?://", "", mt.group(0), flags=re.I).lower().split("/")[0]] += 1
                if _url_kind(mt.group(0)) is not None:
                    left["ссылка ПЕРСОНАЛЬНАЯ (карта/профиль/жильё)"] += 1
            for mt in re.finditer(r"\b[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-]{2,}\b", t):
                w = mt.group(0)
                before = t[:mt.start()].rstrip()
                if before == "" or before[-1] in ".!?:\n":
                    continue
                if w.lower() in PROTECTED or _name_hit(w.lower(), names):
                    continue
                caps_unknown[w] += 1
    return again, left, caps_unknown, hosts, shapes


def main(argv=None):
    io_utf8.force_utf8()
    ap = argparse.ArgumentParser(description="обезличивание корпуса диалогов")
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--verify", dest="verify_path", default=None,
                    help="только ЗАМОК: пересчитать остаток в готовом файле")
    a = ap.parse_args(argv)

    if a.verify_path:
        src_recs = read_records(a.src)
        names = build_gazetteer(src_recs)
        again, left, caps, hosts, shapes = verify(a.verify_path, names)
        print("ЗАМОК по %s" % os.path.basename(a.verify_path))
        print("A. ПОВТОР ЧИСТКИ тем же детектором (обязан быть нулём по каждому виду):")
        for k in KINDS:
            if k.endswith("(шапка)") or k.endswith("(шапка записи)"):
                continue
            print("     %-26s %d" % (k, again[k]))
        print("B. ШИРОКАЯ СЕТЬ — что уцелело и чем является:")
        for k, n in sorted(left.items(), key=lambda kv: -kv[1]):
            print("     %-46s %d" % (k, n))
        print("     %-46s %d вхождений, %d разных"
              % ("заглавных не-в-начале вне словаря", sum(caps.values()), len(caps)))
        if shapes:
            print("   формы неопознанных рядов:")
            for s, n in shapes.most_common(10):
                print("      %-28s %d" % (s, n))
        if hosts:
            print("   хосты уцелевших ссылок:")
            for h, n in hosts.most_common(12):
                print("      %-32s %d" % (h, n))
        return 0

    if not os.path.isfile(a.src):
        print("корпус не найден: %s" % a.src)
        return 2
    n, msgs, changed, stats, gaz = run(a.src, a.out)
    print("исходник : %s" % a.src)
    print("вычищено : %s" % a.out)
    print("записей %d, сообщений %d, ИЗМЕНЕНО сообщений %d (%.1f%%)"
          % (n, msgs, changed, 100.0 * changed / msgs if msgs else 0.0))
    print("газеттир имён (в памяти прогона): %d основ" % gaz)
    print("--- вхождений заменено по видам ---")
    for k in KINDS:
        print("  %-26s %6d" % (k, stats[k]))
    print("  %-26s %6d" % ("ИТОГО", sum(stats[k] for k in KINDS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
