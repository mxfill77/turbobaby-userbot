#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pc_orchestrator.py — ПК-оркестратор ступень 1 (порт VPS orchestrator_daemon под Windows).

Демон поллит очередь Bridge (lane=pc, ~60с), берёт ОДНУ задачу за раз, исполняет headless
`claude -p` в D:\\turbobaby-bot с текущим .claude/settings.json + pretool_guard (трёхцветный
гард). Красное в headless → задача переходит в NEEDS_APPROVAL (карточку постит Splinter),
демон ждёт решения (approve→повтор шага / reject/таймаут 30мин→failed). Статусы + строка в
cowork_log + пуш Филиппу на done/failed/needs_approval.

БЕЗОПАСНОСТЬ / НЕ НАВРЕДИ:
  - Демон САМ красное не исполняет: headless-гард (pretool_guard) блокирует ask; claude по
    преамбуле выводит маркер NEEDS_APPROVAL и завершается. Демон детектит маркер (stdout ИЛИ
    файл-маркер PRETOOL_ASK_MARKER, который гард пишет в headless) → needs_approval.
  - lane=pc СТРОГО: обрабатываем ТОЛЬКО задачи с item.lane=='pc'. Нет поля lane → пропуск
    (fail-safe: чужие VPS-задачи на ПК не исполняем).
  - Жёсткий таймаут задачи (45 мин) → kill + failed. Одна задача за раз (не параллелим).
  - Рубильник: файл pc_orchestrator.stop → поллинг останавливается (без убийства процессов).
  - userbot/moderation_bot НЕ трогаем.

Автозапуск: schtasks (ONSTART) + watchdog (--watchdog): проверяет свежесть heartbeat, при
падении зовёт schtasks /Run, ЛОГИРУЕТ его вывод и ВЕРИФИЦИРУЕТ подъём (урок инцидента 205 —
тихого сбоя быть не должно).
"""
# self-update NOOP-триггер 2026-07-12: локальный дирижёр-декомпозер включён НАСОВСЕМ
# (PC_LOCAL_DEC=1 в .env). Коммит меняет только блоб → штатный self-update поднимет новый
# процесс демона, который на старте load_dotenv'ит .env и увидит флаг. Логику не трогает.
import os
import re
import sys
import glob
import time
import json
import shutil
import logging
import hashlib
import datetime
import tempfile
import subprocess
import urllib.request
import urllib.parse
import urllib.error

import io_utf8               # переключатель stdout/stderr в UTF-8 (класс «charmap can't encode 📊»)
import gate_selective         # селективный тест-гейт авто-применения (порт VPS GATE_STEP/SINGLE_SELECTIVE); чистый, без сети
import task_metrics           # единый формат строки METRICS (обе полосы) + norm_effort/extract_tokens/selfheal_count
import client_contour         # признак клиентского контура (граф импортов ботов) + реестр оснований пропуска ворот; чистый, без сети
import lesson_router          # обработчик задач-уроков (родитель 292, шаг 3): классификация+маршрут; suggest тянет лениво
try:
    # Словарь ВИДОВ красных операций и разбор карточки — у гарда, и только у него: демону нужно
    # понять, НА ЧТО именно владелец сказал «да» (`kinds_from_card`), а держать второй список
    # формулировок значит завести две расходящиеся правды. Модуль чистый (без сети/БД); при импорте
    # он форсит utf-8 на stdout/stdin — ровно то же делает io_utf8.force_utf8() на старте демона.
    import pretool_guard
except Exception:             # демон обязан подниматься даже без модуля гарда (fail-safe)
    pretool_guard = None
# ДЕПЛОЙ #334 шаг 6/6 (2026-07-14, одобрен владельцем): LLM-маршрут уроков в бою —
# LESSON_LLM_ROUTE=1 в .env ПОСТОЯННО; коммит-веха триггерит эстафету демона (новый процесс
# импортирует свежий lesson_router b088eb1→0b83582 и перечитает .env). Fail-safe в router:
# сбой думателя/рубильник off → keyword-путь; полный откат = флаг off + рестарт.
# #112 шаг 7/8 (2026-07-16): эстафета демона под ВТОРУЮ ОСЬ урока. Шаги 1–6 (4700211→11678d2)
# тронули lesson_router/moderation_core/suggest, но НЕ pc_orchestrator.py — авто-self-update
# (он ловит дифф ТОЛЬКО по blob'у оркестратора) не срабатывал, и живой демон де-факто держал в
# памяти СТАРЫЙ lesson_router (импорт выше — на старте процесса). Логика самого оркестратора здесь
# не меняется: эта веха — минимальный blob-дифф pc_orchestrator.py, чтобы next-poll self-update
# передал эстафету свежему процессу, который переимпортирует lesson_router со ВТОРОЙ осью type
# (behavior→playbook+ack / code→дев-очередь / unsure→переспрос кнопками), гардом playbook и /rules.
# ВЕХА 2026-07-22 (класс-фикс мисклассификации урока, живой кейс 16:49, 7eab874): та же механика —
# правка ТОЛЬКО в lesson_router.py (_wording_of_known_result: «формулировка уже вычисленного» →
# behavior, обратный #164), pc_orchestrator.py логикой не тронут ⇒ self-update по blob'у молчал бы,
# а живой демон держал бы в памяти СТАРЫЙ классификатор и продолжал слать урок-о-формулировке
# CODE-карточкой владельцу. Этот комментарий — минимальный blob-дифф, чтобы next-poll self-update
# передал эстафету свежему процессу с новым критерием. Логика демона здесь НЕ меняется.
# ВЕХА 2026-07-22 (завершающий вопрос клиентского контура, живой ТЕСТ-7, 8998298): та же механика —
# правка ТОЛЬКО в suggest.py (next_step/next_step_note + страховка ensure_closing_question: ответ
# заканчивается ОДНИМ следующим шагом по приоритету недостающего в collected_facts), pc_orchestrator.py
# логикой не тронут ⇒ self-update по blob'у молчал бы, а живые userbot/moderbot доживали бы на старом
# suggest и продолжали отвечать «в пустоту» (цена и доставка названы — дальше тишина). Этот комментарий —
# минимальный blob-дифф, чтобы next-poll self-update передал эстафету свежему процессу, а реконсиляция
# детей по карте _FILE_PROCESS_RULES (suggest* → userbot+moderbot) применила новый код обоим ботам.
# Логика демона здесь НЕ меняется.

REPO = r"D:\turbobaby-bot"

# .env грузим ДО чтения любых config-констант ниже (CLAUDE_BIN/LANE/POLL_SEC/…), иначе
# os.getenv вернёт дефолты вместо значений из .env — прод-баг: демон не увидел бы CLAUDE_BIN.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
except Exception:
    pass

VENV_PY = os.path.join(REPO, "venv", "Scripts", "python.exe")
DNOTIFY = os.path.join(REPO, "dispatch_notify.py")
TASK_NAME = "pc_orchestrator"          # имя задачи Планировщика (schtasks /Run /TN pc_orchestrator)

LANE = os.getenv("PC_LANE", "pc")
POLL_SEC = int(os.getenv("PC_POLL_SEC", "60") or "60")
TASK_TIMEOUT = int(os.getenv("PC_TASK_TIMEOUT", "2700") or "2700")     # 45 мин жёсткий таймаут
APPROVAL_TTL = int(os.getenv("PC_APPROVAL_TTL", "1800") or "1800")     # 30 мин ожидание approve
# ПК-side ливнесс ОДИНОЧЕК lane=pc (развязка 328-pc, этап 2): одиночка, застрявшая в in_progress
# дольше этого порога (ПК был выключен / процесс умер посреди прогона / self-update-гонка) →
# честный failed вместо тихого вечного зависания. Порог СТРОГО > TASK_TIMEOUT (45 мин): демон
# исполняет одну задачу за раз СИНХРОННО и в пределах TASK_TIMEOUT завершает статус ≠ in_progress,
# поэтому живой прогон под нож не попадёт — реапится только орфан мёртвого процесса. vps-реапер НЕ
# дублируем: он про VPS/цепочки, а тут ТОЛЬКО одиночки lane=pc, которых он не видит.
PC_SINGLE_STALE = int(os.getenv("PC_SINGLE_STALE", "5400") or "5400")  # 90 мин: орфан-одиночка in_progress → failed
# Вотчдог ЗАСТРЯВШЕЙ МЕЖДУ ШАГАМИ локальной цепи (инцидент 15.07, цепь 365: 3/7 c 23:06). Штатный
# тик релизит следующий шаг СОБЫТИЙНО (в момент done предыдущего). Если это событие ПОТЕРЯНО (спавн
# упал / ПК уснул посреди consult-думателя / Bridge проглотил enqueue), цепь висит: последний шаг
# done, следующий не релизнут, 0 открытых шагов — а stuck-single ловит только in_progress. Порог:
# сколько последний шаг цепи может простоять done БЕЗ движения, прежде чем вотчдог досдвинет её
# reconcile'ом ИЗ ОЧЕРЕДИ (не из потерянных событий). Щедро > длительности одного цикла демона, но
# сильно ниже наблюдённого зависания (1.5ч) — здоровая цепь релизит следующий шаг за секунды.
PC_CHAIN_STALE = int(os.getenv("PC_CHAIN_STALE", "900") or "900")     # 15 мин: цепь висит между шагами → авто-релиз
NEEDS_APPROVAL_TOPIC = int(os.getenv("PC_NA_TOPIC", "829") or "829")   # тема, куда Splinter постит карточку
INBOX_TOPIC_ID = int(os.getenv("PC_INBOX_TOPIC", "1160") or "1160")    # тема Инбокс HQ-форума: критические инциденты контура (личка — фолбэк)
HEARTBEAT_STALE = int(os.getenv("PC_HB_STALE", "180") or "180")        # watchdog: heartbeat протух
WATCH_VERIFY_SLEEP = int(os.getenv("PC_WATCH_VERIFY", "20") or "20")
# Контур-вотчдог клиентского контура (разбор #128, часть 3): демон — единственный надёжно
# выживающий процесс (его самого держит heartbeat+schtasks), поэтому он же следит за
# pc_agent/userbot/moderation_bot и поднимает мёртвых.
CLIENT_WATCH_SEC = int(os.getenv("PC_CLIENT_WATCH_SEC", "300") or "300")        # проверка контура каждые 5 мин
CLIENT_COOLDOWN_SEC = int(os.getenv("PC_CLIENT_COOLDOWN_SEC", "900") or "900")  # анти-флап: ≤1 подъём/процесс за 15 мин
CLIENT_MAX_DEATHS = int(os.getenv("PC_CLIENT_MAX_DEATHS", "3") or "3")          # 3 смерти подряд → стоп + громкий NOTE
# Фикс КЛАССА (вердикт #171): «не смог проверить» ≠ «мёртв». На просыпающемся/тормозящем ПК CIM-поиск
# finder'а падает по таймауту → раньше возвращал [] → вотчдог считал процесс мёртвым → лишний старт →
# дубль → Conflict (синглтон). Теперь: finder РАЗЛИЧАЕТ error/timeout от честной пустоты; рестарт
# требует ТРЁХ условий (finder успешен + процесса нет + лог протух); grace после пробуждения; алярм
# на слепоту вместо рестартов. Пороги — только СТРОЖЕ к рестарту, не слабее.
CLIENT_LOG_STALE = int(os.getenv("PC_CLIENT_LOG_STALE", "120") or "120")        # свежий лог (≤ этого) ВЕТИРУЕТ рестарт
CLIENT_BLIND_ALARM = int(os.getenv("PC_CLIENT_BLIND_ALARM", "3") or "3")        # N слепых циклов подряд → NOTE «вотчдог слеп»
WAKE_GRACE_SEC = int(os.getenv("PC_WAKE_GRACE", "120") or "120")                # после пробуждения ПК — окно без вердиктов
WAKE_JUMP_MARGIN = int(os.getenv("PC_WAKE_JUMP_MARGIN", "60") or "60")          # скачок wall-clock > POLL+это → «ПК проснулся»
# ─── СИГНАЛ О ДОЛГОМ СНЕ ПК (инцидент 30.07.2026) ────────────────────────────────────────────
# Живой факт: 30.07 ПК проспал 8 ч 58 м (S3, 03:43:15→12:41:21) — клиентский бот был недоступен
# всю ночь; за 7 суток сон съел 12 ч 10 м. Причина НЕ таймаут простоя (standby/hibernate-timeout
# уже 0 обеими полосами), а ЯВНОЕ усыпление из меню Пуск: Kernel-Power 187
# ApiCaller=StartMenuExperienceHost.exe, Kernel-Power 42 Reason=4 (Application API). Значит сон
# может вернуться в любой вечер — и узнавать об этом надо СРАЗУ, а не по жалобе клиента.
# Прежний детект пробуждения оставлял только строку INFO/WARNING в логе демона, которую никто
# не читает; здесь добавляем ГРОМКИЙ след: строка в журнал + карточка в тему постановки.
#
# ПОРОГ — не «на глаз». Скачок wall-clock сам по себе НЕ равен сну: демон исполняет задачу
# СИНХРОННО в главном цикле, поэтому ЧЕСТНЫЙ виток растягивается до TASK_TIMEOUT+POLL_SEC.
# Замер по живому pc_orchestrator.log (369 срабатываний детекта пробуждения за 22–30.07):
#   • порог 600 с  → 26 срабатываний, из них 24 ЛОЖНЫХ (длинные задачи; максимум ложного 1776 с);
#   • порог 1800 с → РОВНО 2 срабатывания, и это ровно два настоящих сна (11611 с и 32381 с).
# Поэтому дефолт = TASK_TIMEOUT+POLL_SEC (2760 с): всё, что дольше, длинной задачей объяснено
# быть НЕ МОЖЕТ по построению — ложных ноль, оба реальных сна проходят с 4–12-кратным запасом.
SLEEP_ALARM_SEC = int(os.getenv("PC_SLEEP_ALARM_SEC", "") or (TASK_TIMEOUT + POLL_SEC))
SLEEP_ALARM_TOPIC = int(os.getenv("PC_SLEEP_TOPIC", "328") or "328")   # тема постановки задач (как детектор немоты)
RESULT_MAX = 4500
TIMEOUT_MARK = "⏱"       # маркер таймаут/сирота-диагнозов: думатель самопочинки их НЕ чинит
MANUAL_MARK = "✋"        # маркер «headless доказанно не может» (снова красное ПОСЛЕ approve) —
                         # зеркало ручной карты VPS «✋ ТРЕБУЕТСЯ РУЧНОЕ ДЕЙСТВИЕ»: думатель НЕ чинит
                         # (переформулировка родила бы петлю ре-аппрувов), цепь = halt
CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")   # ФОЛБЭК: явный путь из .env (может протухнуть при автообновлении)
# Скрытый запуск ВСЕХ служебных консольных подпроцессов (git/powershell/schtasks/tasklist/
# unittest/claude/дочерние python): без этого флага каждый console-ребёнок демона создавал
# НОВОЕ окно → «мигающие чёрные окна» на ПК владельца (инцидент-каскад 22.07). POSIX → 0.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Бюджет процессов claude на ПК (зеркало VPS oom2 1520c68): ПЕРЕД спавном headless claude -p
# считаем ЖИВЫЕ CLI-процессы claude.exe; лимит достигнут → ждём слот, не дождались → отказ,
# 3 отказа подряд → карточка владельцу (инцидент 22.07: 18×claude.exe, ПК тормозит).
MAX_CLAUDE_PROCS = int(os.getenv("PC_MAX_CLAUDE_PROCS", "2") or "2")
CLAUDE_BUDGET_WAIT_SEC = int(os.getenv("PC_CLAUDE_BUDGET_WAIT_SEC", "60") or "60")
# Базовая папка версионных установок claude-code (AppData\Roaming\Claude\claude-code\<версия>\claude.exe).
# Резолвим НОВЕЙШУЮ установку сами → путь переживает автообновление, даже когда .env-путь протух (WinError 2).
_CLAUDE_BASE = os.path.join(os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
                            "Claude", "claude-code")

STOP_FLAG = os.path.join(REPO, "pc_orchestrator.stop")
HEARTBEAT_FILE = os.path.join(REPO, "pc_orchestrator.heartbeat")
LOCK_FILE = os.path.join(REPO, "pc_orchestrator.lock")       # OS-синглтон демона (разбор #128, часть 4)
CLIENT_WATCH_FILE = os.path.join(REPO, "pc_orchestrator.client_watch.json")  # снимок НАДЗОРА контур-вотчдога → читает status pc_agent (отдельный процесс, RAM демона ему недоступна)
SUPERSEDE_ENV = "PC_ORCH_SUPERSEDE_PID"                       # self-update: PID старого, которого сменяем
LOG_PATH = os.path.join(REPO, "pc_orchestrator.log")
NA_MARKER = "NEEDS_APPROVAL:"
ASK_MARKER_ENV = "PRETOOL_ASK_MARKER"   # env для pretool_guard: писать красную карточку в этот файл
MARKER_TOKEN_ENV = "PRETOOL_MARKER_TOKEN"   # env: токен нашего запуска — гард штампует им карточки;
APPROVED_KINDS_ENV = "PRETOOL_APPROVED_KINDS"   # env: виды, на которые владелец УЖЕ сказал «да»
APPROVED_TASK_ENV = "PRETOOL_APPROVED_TASK"     # env: id одобренной задачи (для лога гарда)
APPROVED_OBJECT_ENV = "PRETOOL_APPROVED_OBJECT"  # env: ОБЪЕКТ, названный владельцем в ответе, —
#   без него гард не пропускает высший вид карточки (необратимое): см. _approved_scope
MARKER_SEP = "\x1f"                          # демон принимает ТОЛЬКО карточки со своим токеном (fix ghost:
#   subprocess-тесты гарда наследовали боевой маркер и писали фикстурные карточки — «призрак PID 1»).

_ORCH_FMT = "%(asctime)s %(levelname)s %(message)s"
try:
    import log_setup                       # ротация + тестовый лог в temp (см. log_setup)
    _orch_h = log_setup.rotating_handler(LOG_PATH, fmt=_ORCH_FMT)
except Exception:
    _orch_h = None
if _orch_h is not None:
    logging.basicConfig(level=logging.INFO, handlers=[_orch_h])
else:
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format=_ORCH_FMT,
                        encoding="utf-8")   # без него фолбэк-лог берёт cp1251 → эмодзи в записи = charmap
log = logging.getLogger("pc_orchestrator")

# ТЕСТ-ПРОГОН ПРОТИВ БОЕВОГО (порт с VPS 25.07.2026). На ПК разведение УЖЕ своё и остаётся как
# есть: log_setup уводит лог теста в temp (а не глушит) и намеренно НЕ смотрит на «unittest в
# sys.modules» — демон импортирует gate_selective, который гоняет тесты, и боевой лог уехал бы в
# temp ровно тогда, когда нужен. Здесь добавляем ВТОРОЙ признак из общего модуля: он опознаёт
# тест ПО ИМЕНИ ВХОДНОГО ФАЙЛА и по тест-флагам окружения. Нужен для поля mode= в строке METRICS.
_UNDER_TEST = task_metrics.under_test((sys.argv[0] if sys.argv else ""), os.environ, sys.modules)
try:
    _UNDER_TEST = _UNDER_TEST or log_setup.is_test_context()
except Exception:                       # log_setup мог не импортироваться (см. блок выше)
    pass

BRIDGE_URL = os.getenv("BRIDGE_URL", "").strip()
BRIDGE_TOKEN = os.getenv("BRIDGE_TOKEN", "").strip()

_NA_FALLBACK = ("требует подтверждения", "нужно подтверждение", "нужно «да»", "needs approval",
                "permission to use", "не разрешено гейтом", "нет прав на approve")

PREAMBLE = (
    "Ты выполняешь задачу автономно в headless-режиме (без интерактивного подтверждения) в репо "
    "D:\\turbobaby-bot — .claude/settings.json и pretool_guard действуют. Зелёное/оранжевое "
    "(чтение, правки кода в проекте, тесты, git commit/push) делай САМ. НАСТОЯЩЕЕ КРАСНОЕ "
    "(удаление файлов, правка/чтение .env и секретов, taskkill/schtasks, sqlite/запись в БД, "
    "выход за пределы проекта, сеть кроме git/Bridge/Telegram) — НЕ выполняй и НЕ ищи обход: "
    "выведи РОВНО одну строку «NEEDS_APPROVAL: op=<класс> | <карточка: что · зачем · "
    "последствия>» и заверши работу (исполнит человек после «да»). <класс> — ОДНО слово из "
    "списка: delete, env, kill, schtasks, network, sqlite, clasp, outside, py_write, git_force, "
    "edit_claude, other. Класс обязателен: по нему «да» владельца вернётся ИМЕННО на эту "
    "операцию, а «other» означает «класс назвать не могу» — тогда повторный запуск снова упрётся "
    "в то же красное. userbot и moderation_bot НЕ трогай. Первая строка "
    "ответа — краткая сводка (≤400 символов). ПОСЛЕДНЯЯ строка вывода — ОБЯЗАТЕЛЬНО "
    "«RESULT: <краткий итог сделанного>»; без этой строки задача считается НЕ выполненной. ЗАДАЧА:\n"
)

_RE_RESULT = re.compile(r"(?m)^\s*RESULT:\s*\S")   # признак результата в stdout headless

# ── «ДА» ВЛАДЕЛЬЦА ДОЛЖНО ДОЕЗЖАТЬ ДО ИСПОЛНИТЕЛЯ (правка 30.07.2026) ────────────────────────
# Разбор живых логов: за историю демона 10 нажатий «да» дали 3 done и 7 ✋failed «одобрено, но шаг
# снова упирается в красное» (281, 338, 362, 363, 364, 48, 55). Одобрение доезжало до ре-рана
# ИСПРАВНО (в логе `RUN id=55 15:05:34` через 13 мин после кнопки), а терялось ВНУТРИ ре-рана —
# ДВУМЯ РАЗНЫМИ каналами, и оба надо чинить:
#   A) ГАРД в ребёнке про одобрение не знал: PreToolUse-хук — НОВЫЙ процесс на каждый вызов, он
#      читает только stdin-JSON и env. Живой факт: `ask | env` в 15:09:05 ВНУТРИ одобренного
#      ре-рана задачи 55 (`Test-Path "…\.env"`). Лечится env-маркером PRETOOL_APPROVED_KINDS.
#   B) САМА МОДЕЛЬ отказывалась снова: нота «[ОДОБРЕНО ЧЕЛОВЕКОМ]» стояла ПЕРЕД преамбулой, а
#      преамбула тут же запрещает красное безусловно и требует печатать NEEDS_APPROVAL. Живой
#      факт: в ре-ране задачи 48 (02:32:43→02:41:48) у гарда НЕТ НИ ОДНОЙ строки `ask` — красное
#      объявил ребёнок сам. Лечится этим абзацем, который стоит ПОСЛЕ преамбулы (последнее слово).
# Оба канала строго ПО КЛАССУ операции: имя класса берётся из карточки, на которую нажали «да».
APPROVED_CLAUSE = (
    "\nОДОБРЕНИЕ ВЛАДЕЛЬЦА (перебивает запрет выше ТОЛЬКО для названного класса): Филипп уже "
    "нажал «да» по ЭТОЙ задаче на операции класса: {kinds}. Такую операцию ВЫПОЛНИ САМ — гард "
    "на этот класс в этом запуске карточку не поставит, и повторный NEEDS_APPROVAL по нему "
    "ЗАПРЕЩЁН (он закроет задачу как «выполни вручную», то есть сожжёт «да» владельца). Красное "
    "ЛЮБОГО ДРУГОГО класса — по-прежнему только строкой NEEDS_APPROVAL: op=<класс> | …, и обход "
    "гарда искать НЕ нужно.\n")


def _tail(s, n=500):
    """Хвост строки для диагноза (последние ~n символов, без пустышек)."""
    s = (s or "").strip()
    return s[-n:] if s else ""


COWORK_RESULT_MAX = 1500   # полный текст RESULT/причины failed в cowork_log штаб читает без скринов


def _clip(s, n=COWORK_RESULT_MAX):
    """Однострочный итог для cowork_log: схлопываем пробелы/переносы (лог — одна строка),
    режем до n символов с явной пометкой «…обрезано», чтобы штаб видел усечение."""
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else (s[:n] + " …обрезано")


# ------------------------------- Bridge (очередь) ----------------------------

class Bridge:
    """Минимальный клиент очереди Bridge (совместим с протоколом VPS). read=GET, мутации=POST."""
    def __init__(self, url=None, token=None, timeout=90):
        self.url = url or BRIDGE_URL
        self.token = token or BRIDGE_TOKEN
        self.timeout = timeout

    def _get(self, action, **params):
        q = {"action": action, "token": self.token, **{k: v for k, v in params.items() if v is not None}}
        full = self.url + "?" + urllib.parse.urlencode(q)
        try:
            with urllib.request.urlopen(urllib.request.Request(full, method="GET"), timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}

    def _post(self, action, **fields):
        body = json.dumps({"action": action, "token": self.token, **fields}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}

    def get_pending(self, status, lane=LANE):
        return self._get("get_pending", status=status, lane=lane)

    def claim_task(self, tid):
        return self._post("claim_task", id=tid)

    def complete_task(self, tid, status, result):
        return self._post("complete_task", id=tid, status=status, result=(result or "")[:RESULT_MAX])

    def set_needs_approval(self, tid, what, topic=NEEDS_APPROVAL_TOPIC):
        # topic → Splinter постит красную карточку в эту тему (по уточнению Филиппа — 829).
        return self._post("set_needs_approval", id=tid, what=(what or "")[:RESULT_MAX], topic=topic)

    def task_heartbeat(self, tid):
        return self._post("task_heartbeat", id=tid)

    def enqueue_task(self, frm, text, lane=LANE):
        return self._post("enqueue_task", **{"from": frm, "task_text": text, "lane": lane})


bc = Bridge()


# ------------------------------- утилиты -------------------------------------

def _cowork(line):
    """Строка-итог в cowork_log через скрипт (fire-and-forget).

    КОНТРАКТ СТРОКИ ЖУРНАЛА (28.07.2026): «<ТИП> <ГГГГ-ММ-ДД ЧЧ:ММ UTC>: <текст>», ОДНА запись =
    ОДНА строка. ТИП ставим здесь (наши строки всегда NOTE — знает только источник), ДАТУ ставит
    единственная точка штампа cowork_log_append.stamp_line. Переносы схлопываем В ИСТОЧНИКЕ:
    result задачи бывает многострочным (_clip режет длину, но НЕ переносы), а многострочная
    запись рвёт разбор журнала по заголовкам — и может подсунуть сплиттеру ложный заголовок."""
    try:
        one = " ".join(str(line or "").split())
        subprocess.Popen([VENV_PY, os.path.join(REPO, "cowork_log_append.py"), "NOTE Orchestrator: " + one],
                         cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("cowork_log не записан: %s", e)


def _notify(text):
    """Пуш Филиппу через dispatch_notify (fire-and-forget, человекочитаемо)."""
    try:
        subprocess.Popen([VENV_PY, DNOTIFY, text], cwd=REPO,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("пуш не отправлен: %s", e)


# --- карточки цепи: restart-proof дедуп + мьют финализированных (спам-луп 14:25 14.07) ---
# Живой провал: залп «План цепи #101» (41 карточка исторических версий) в личку при рестарте
# демона 0bd46c0. Корень двойной: (1) гейт-тесты test_pc_local_dec звали НЕзамоканный
# _notify_chain_card — каждый прогон гейта стрелял реальными subprocess-карточками фикстурной
# цепи 101 (LocBase теперь мокает); (2) у самой функции не было ни restart-proof дедупа, ни
# исключения финализированных цепей. Класс: «отправлено» — в state-файле по ключу события
# (родитель + sha1 текста карточки: текст несёт версию плана и номер шага), финализированные
# цепи (сводка поставлена / помечены вручную) не анонсируются НИКОГДА.
CHAIN_CARD_STATE = os.path.join(REPO, "pc_orchestrator.chain_cards.json")
CHAIN_CARD_SENT_MAX = 500          # кап истории ключей (старые события дедупить незачем)


def _chain_cards_read(path=None):
    """State карточек цепей {'sent': [ключи], 'final': [pid]} → dict ({} при отсутствии/бое)."""
    try:
        with open(path or CHAIN_CARD_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _chain_cards_write(d, path=None):
    try:
        with open(path or CHAIN_CARD_STATE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        log.warning("state карточек цепей не записался (%s) — дедуп деградирует, не критично", e)


def _loc_mark_chain_final(pid, path=None):
    """Пометить цепь pid финализированной: её карточки БОЛЬШЕ НИКОГДА не анонсируются
    (restart-proof). Зовётся при постановке сводки (halt/finish/стоп) и при узнавании
    закрытой цепи после рестарта; вручную — для исторических артефактов (цепь 101)."""
    st = _chain_cards_read(path)
    fin = set(int(x) for x in (st.get("final") or []))
    if int(pid) in fin:
        return
    fin.add(int(pid))
    st["final"] = sorted(fin)
    _chain_cards_write(st, path)


def _notify_chain_card(pid, text, state_path=None, spawn=None):
    """Карточка цепи владельцу с кнопками [⏹ Стоп цепи][📊 Статус цепи] (dispatch_notify --card,
    fire-and-forget). callback слушает pc_agent (owner-gate). Сбой доставки тик демона не роняет.
    Дедуп restart-proof: ключ события (pid+sha1 текста) в CHAIN_CARD_STATE — рестарт демона НЕ
    повторяет уже отправленные; финализированные цепи (final) не анонсируются никогда."""
    st = _chain_cards_read(state_path)
    if int(pid) in set(int(x) for x in (st.get("final") or [])):
        log.info("карточка цепи %s подавлена: цепь финализирована", pid)
        return False
    key = f"{pid}:{hashlib.sha1(str(text).encode('utf-8')).hexdigest()[:12]}"
    sent = list(st.get("sent") or [])
    if key in sent:
        log.info("карточка цепи %s подавлена: событие уже отправлялось (restart-proof дедуп)", pid)
        return False
    try:
        if spawn is not None:
            spawn(pid, text)
        else:
            subprocess.Popen([VENV_PY, DNOTIFY, "--card", str(pid), str(text)], cwd=REPO,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("карточка цепи не отправлена (pid=%s): %s", pid, e)
        return False
    st["sent"] = (sent + [key])[-CHAIN_CARD_SENT_MAX:]
    _chain_cards_write(st, state_path)
    return True


def _notify_critical(text):
    """Критический инцидент КОНТУРА (доказанная смерть демона / 3-смерти-halt клиент-бота /
    halt-слепота контур-вотчдога) → тема Инбокс HQ-форума (INBOX_TOPIC_ID=1160); личка Филиппа —
    ФОЛБЭК при недоступности форума. Маршрут «форум → личка» реализует dispatch_notify --critical.
    Fire-and-forget: сбой доставки НЕ роняет тик демона. Гигиена пульта: сюда идут ТОЛЬКО реальные
    инциденты (не рутинные done/failed задач — те видны в темах постановки 328/829, см. _notify_task)."""
    try:
        subprocess.Popen([VENV_PY, DNOTIFY, "--critical", text], cwd=REPO,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("критический пуш не отправлен: %s", e)


def _notify_topic(topic, text):
    """Сигнал в КОНКРЕТНУЮ тему форума через `dispatch_notify --topic <id>` — тот же канал, что
    завёл детектор немоты (session_watch, 29.07): тема постановки задач 328, фолбэк инбокс 1160 →
    личка реализован внутри dispatch_notify. Fire-and-forget: сбой доставки НЕ роняет тик демона."""
    try:
        subprocess.Popen([VENV_PY, DNOTIFY, "--topic", str(topic), text], cwd=REPO,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("сигнал в тему %s не отправлен: %s", topic, e)


def _notify_task(kind, tid, text):
    """Уведомление о жизненном цикле ЗАДАЧИ (done/failed/needs_approval). Гигиена пульта:
    done/failed уже видны в темах постановки (328/задачи, 829/красное) — в личку их НЕ
    дублируем (личка тонула в дублях карточек). needs_approval оставляем пушем: это
    call-to-action, требующий реакции Филиппа. Итог в cowork_log и карточку темы пишет
    вызывающий (bc.complete_task/_cowork) — эта функция ТОЛЬКО про личку-пуш."""
    if kind in ("done", "failed"):
        return                       # тема постановки уже показала карточку — личку не дублируем
    _notify(_human(kind, tid, text))


def _stopped():
    return os.path.exists(STOP_FLAG)


def _write_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
    except Exception:
        pass


def _lane_ok(item):
    """СТРОГО: обрабатываем только задачи своей полосы. Нет поля lane → пропуск (fail-safe)."""
    return str(item.get("lane") or "") == LANE


def _age_sec(updated_iso, now=None):
    try:
        s = str(updated_iso).replace("Z", "+00:00")
        t = datetime.datetime.fromisoformat(s)
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        now = now or datetime.datetime.now(datetime.timezone.utc)
        return (now - t).total_seconds()
    except Exception:
        return None


# ───── ЧЕСТНЫЙ ИТОГ ПРОВАЛА: «не закрыта» ≠ «ничего не сделано» (класс 30.07.2026) ──────────────
# ЗАМЕР за двое суток (tmp/measure_status_truth.py, окно 28.07 13:55 → 30.07 13:55 UTC): из ШЕСТИ
# разобранных провалов ПК-полосы ТРИ несли за собой реальную работу — задача 54 (коммиты c00bb08 и
# 88eec9e плюс четыре записи в журнал, и только потом 30-минутный таймаут подтверждения), задача 55
# (коммит a8f8822), задача 48 (артефакт + ASK владельцу). Плюс задача 61: коммит a3f75dd и записи в
# журнал есть, а терминальной строки на ПК НЕТ ВОВСЕ — статус ей поставила чужая полоса. Владелец и
# планировщик читают «failed» как «работа не сделана» и строят следующий шаг на неверной строке; за
# сутки так вышло дважды.
#
# Два правила блока:
#   1) ПРИЧИНА называется КОДОМ, а не одним словом «failed». Таймаут подтверждения (ждали человека),
#      таймаут сердцебиения (процесс умер / ПК уснул), таймаут прогона (claude не уложился), отказ
#      модели (снова красное после «да») и ошибка выполнения — это РАЗНЫЕ следующие шаги: одно
#      повторить как есть, другое переформулировать, третье чинить руками.
#   2) Есть КОММИТ или ЗАПИСЬ В ЖУРНАЛ внутри окна задачи → итог обязан это НАЗВАТЬ: работа
#      выполнена, не состоялось формальное закрытие. Иначе владелец переделывает уже сделанное.
# Маркер (⏱/✋) остаётся ПЕРВЫМ символом строки: на него смотрят гейт самопочинки (_maybe_selfheal)
# и гейт надзора цепей (_loc_after_fail) — их поведение эта правка НЕ меняет.
FAIL_APPROVAL_TIMEOUT = "approval_timeout"     # ждали «да» владельца и не дождались
FAIL_HEARTBEAT_TIMEOUT = "heartbeat_timeout"   # задача перестала подавать признаки жизни (орфан)
FAIL_RUN_TIMEOUT = "run_timeout"               # headless не уложился в TASK_TIMEOUT
FAIL_MODEL_REFUSAL = "model_refusal"           # модель/гард снова объявили красное после одобрения
FAIL_EXEC_ERROR = "exec_error"                 # сбой исполнения: код возврата, пустой вывод, бюджет

FAIL_REASONS = {                               # код → (человеческое имя, маркер ПЕРВЫМ символом)
    FAIL_APPROVAL_TIMEOUT: ("таймаут подтверждения", TIMEOUT_MARK),
    FAIL_HEARTBEAT_TIMEOUT: ("таймаут сердцебиения", TIMEOUT_MARK),
    FAIL_RUN_TIMEOUT: ("таймаут прогона", TIMEOUT_MARK),
    FAIL_MODEL_REFUSAL: ("отказ модели", MANUAL_MARK),
    FAIL_EXEC_ERROR: ("ошибка выполнения", ""),
}
# Формулировка НАМЕРЕННО про ОКНО, а не про авторство. Живая проверка на окне задачи 61 показала:
# в её 90 минут попали 8 коммитов, из которых её собственный — один (a3f75dd), остальные сделали
# параллельные RC/Dispatch-сессии этого же ПК. Сказать «РАБОТА ВЫПОЛНЕНА» про чужой коммит — та же
# ложь, только с другого конца; поэтому итог показывает улики и честно называет их окном.
WORK_DONE_MARK = "В ОКНЕ ЗАДАЧИ ЕСТЬ РАБОТА"   # ищется и глазами владельца, и грепом по журналу
# Служебные строки самого демона («NOTE …: Orchestrator: взял задачу #61») в реестре журнала есть,
# но работой задачи НЕ являются — иначе уликой станет сама отметка о старте.
_LEDGER_SELF_RE = re.compile(r"^\w+\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+UTC:\s*Orchestrator:")
FAIL_CODE_RE = re.compile(r"причина=([a-z_]+)")   # разбор кода из готовой строки итога

# Отметка момента CLAIM — НА ДИСКЕ. Задачу часто закрывает УЖЕ ДРУГОЙ процесс демона (само-
# обновление, падение, реапер орфанов), и окно работы из памяти не восстановить: ровно так задача
# 61 осталась без окна вовсе. Файл маленький и с капом — это не состояние, а метки времени.
TASK_START_FILE = os.path.join(REPO, "pc_orchestrator.task_started.json")
TASK_START_KEEP = 200
# Локальный реестр УСПЕШНЫХ записей в журнал (пишет cowork_log_append). Спул хранит ПРОВАЛЬНЫЕ
# строки, реестр — прошедшие: другого местного следа «журнал записан» у демона нет, а именно он
# отличает «задача 61 работала» от «задача 61 молчала».
COWORK_LEDGER = os.path.join(REPO, "cowork_log.ledger")
EVIDENCE_MAX_COMMITS = 5      # в итог кладём первые N — остальные видны числом
EVIDENCE_MAX_JOURNAL = 3


def _task_started_read(path=None):
    try:
        with open(path or TASK_START_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _task_started_mark(tid, now=None, path=None):
    """Отметить момент CLAIM задачи на диске. → ISO отметки. ПЕРВАЯ отметка не перезаписывается:
    у одобренной задачи работа шла в ПЕРВОМ прогоне, а approve только вернул её в очередь."""
    st = _task_started_read(path)
    key = str(tid)
    if key in st:
        return st[key]
    st[key] = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat()
    if len(st) > TASK_START_KEEP:        # кап: отметки давно закрытых задач никому не нужны
        for k in sorted(st, key=lambda x: str(st[x]))[:len(st) - TASK_START_KEEP]:
            st.pop(k, None)
    try:
        with open(path or TASK_START_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:               # отметка — удобство, а не условие работы демона
        log.warning("отметка старта задачи %s не записана (%s) — окно работы будет неизвестно", tid, e)
    return st[key]


def _task_started_get(tid, path=None):
    """datetime старта задачи | None. None = окно неизвестно, и итог так и скажет (не соврёт)."""
    raw = _task_started_read(path).get(str(tid))
    if not raw:
        return None
    try:
        t = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def _git_commits_between(since, until):
    """Коммиты репо в окне → [(хеш, заголовок)]. Границы отдаём git'у в UTC с явной зоной."""
    # --reverse: от НАЧАЛА окна. Показываем первые EVIDENCE_MAX_COMMITS, а работа задачи обычно
    # ложится в начало её окна — при обратном (git-дефолтном) порядке живая проверка задачи 61
    # выкинула из показа её собственный a3f75dd, оставив пять чужих, более свежих.
    fmt = "%Y-%m-%dT%H:%M:%S%z"
    out = _git_out(["log", "--no-merges", "--reverse",
                    "--since=" + since.astimezone(datetime.timezone.utc).strftime(fmt),
                    "--until=" + until.astimezone(datetime.timezone.utc).strftime(fmt),
                    "--pretty=%h\x1f%s"])
    rows = []
    for line in (out or "").splitlines():
        parts = line.split("\x1f")
        if len(parts) == 2 and parts[0].strip():
            rows.append((parts[0].strip(), parts[1].strip()))
    return rows


def _journal_writes_between(since, until, path=None):
    """Записи, РЕАЛЬНО ушедшие в журнал в окне → [строка]. Источник — реестр cowork_log.ledger."""
    rows = []
    try:
        with open(path or COWORK_LEDGER, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                    t = datetime.datetime.fromisoformat(str(rec.get("ts") or "").replace("Z", "+00:00"))
                except Exception:
                    continue          # битая строка реестра не должна прятать остальные
                if t.tzinfo is None:
                    t = t.replace(tzinfo=datetime.timezone.utc)
                line = str(rec.get("line") or "")
                if since <= t <= until and not _LEDGER_SELF_RE.match(line):
                    rows.append(line)     # служебные NOTE самого демона уликой не считаем
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning("реестр записей журнала не прочитан (%s) — следы работы будут неполны", e)
    return rows


def _work_evidence(since, until=None):
    """Следы РАБОТЫ в окне [since, until]: коммиты репо + записи журнала.
    FAIL-SAFE по построению: любой сбой сбора = пустые следы. Это путь ПРОВАЛА задачи —
    второй сбой здесь недопустим, закрытие обязано состояться в любом случае."""
    until = until or datetime.datetime.now(datetime.timezone.utc)
    try:
        commits = _git_commits_between(since, until)
    except Exception as e:               # noqa: BLE001 — сбор следов не роняет закрытие задачи
        log.warning("следы: коммиты не собраны (%s)", e)
        commits = []
    try:
        journal = _journal_writes_between(since, until)
    except Exception as e:               # noqa: BLE001 — то же для реестра журнала
        log.warning("следы: записи журнала не собраны (%s)", e)
        journal = []
    return {"commits": commits, "journal": journal, "since": since, "until": until}


def fail_result(code, detail, since=None, now=None):
    """Текст итога ПРОВАЛА: причина КОДОМ + следы работы, если она была.

    Три формы, ровно по трём состояниям знания:
      • окно известно, следы ЕСТЬ  → «НЕ ЗАКРЫТА, но РАБОТА ВЫПОЛНЕНА …» + перечень улик;
      • окно известно, следов НЕТ  → «провал …» + прямое «следов в окне нет» (это тоже факт);
      • окна нет (отметки claim не сохранилось) → так и говорим, следы НЕ проверялись.
    Маркер причины идёт ПЕРВЫМ символом — гейты самопочинки/цепей смотрят именно на него."""
    label, mark = FAIL_REASONS.get(code, ("причина не названа", ""))
    lead = (mark + " ") if mark else ""
    head = "[причина=%s · %s]" % (code, label)
    if since is None:
        log.warning("FAIL причина=%s окно=неизвестно (нет отметки claim)", code)
        return ("%sпровал %s: %s. Окно работы неизвестно (отметки claim не сохранилось) — следы "
                "работы НЕ проверялись." % (lead, head, detail))[:RESULT_MAX]
    now = now or datetime.datetime.now(datetime.timezone.utc)
    ev = _work_evidence(since, now)
    commits, journal = list(ev.get("commits") or []), list(ev.get("journal") or [])
    win = "%s–%s UTC" % (since.astimezone(datetime.timezone.utc).strftime("%d.%m %H:%M"),
                         now.astimezone(datetime.timezone.utc).strftime("%H:%M"))
    log.warning("FAIL причина=%s окно=%s следы: коммитов=%d, записей журнала=%d",
                code, win, len(commits), len(journal))
    if not commits and not journal:
        return ("%sпровал %s: %s. Следов работы в окне %s нет (коммитов 0, записей журнала 0)."
                % (lead, head, detail, win))[:RESULT_MAX]
    parts = []
    if commits:
        parts.append("коммитов %d (%s)" % (len(commits), "; ".join(
            "%s «%s»" % (h, s[:60]) for h, s in commits[:EVIDENCE_MAX_COMMITS])))
    if journal:
        parts.append("записей журнала %d (%s)" % (len(journal), "; ".join(
            "«%s»" % _clip(x, 90) for x in journal[:EVIDENCE_MAX_JOURNAL])))
    return ("%sНЕ ЗАКРЫТА, но %s %s: %s. СЛЕДЫ в окне %s: %s. Формальное закрытие не состоялось — "
            "НЕ переделывай вслепую: сверь эти следы с заданием и закрой руками. (Окно, а не "
            "авторство: в него попадают и параллельные сессии ПК.)"
            % (lead, WORK_DONE_MARK, head, detail, win, ", ".join(parts)))[:RESULT_MAX]


def _detect_needs_approval(out, marker_content="", run_token=None):
    """Красная зона: файл-маркер гарда (headless-сигнал) ИЛИ маркер в stdout ИЛИ фолбэк-фразы.
    run_token задан → принимаем ТОЛЬКО карточки нашего запуска (строки '<token>\\x1f<текст>');
    чужие/старые (иной или пустой токен) — логируем и игнорируем (страховка от «призрака»)."""
    # 1) файл-маркер гарда (надёжный сигнал: гард форснул ask в headless).
    #    Дедуп строк: одно красное действие могло ретраиться → карточка набегала ×N (была ×5).
    if marker_content:
        seen, uniq, foreign = set(), [], 0
        for ln in marker_content.splitlines():
            if not ln.strip():
                continue
            tok, text = ln.split(MARKER_SEP, 1) if MARKER_SEP in ln else ("", ln)
            if run_token is not None and tok != run_token:
                foreign += 1            # чужая/старая карточка (не наш run_token) — не наш ask
                continue
            key = text.strip()
            if key and key not in seen:
                seen.add(key)
                uniq.append(text)
        if foreign:
            log.warning("маркер: игнорирую %d чужих/старых карточек (не наш run_token=%s)", foreign, run_token)
        if uniq:
            return ("NEEDS_APPROVAL (гард): " + "\n".join(uniq))[:RESULT_MAX]
        # только чужие карточки → это НЕ наш красный сигнал; идём к stdout-маркеру/фолбэку ниже
    t = out or ""
    for line in t.splitlines():
        i = line.find(NA_MARKER)
        if i >= 0:
            what = line[i + len(NA_MARKER):].strip()
            return (what or "(claude не уточнил красное действие)")[:RESULT_MAX]
    low = t.lower()
    if any(p in low for p in _NA_FALLBACK):
        return ("(гейт заблокировал красное; маркера нет — вывод:)\n" + t[:1400])
    return None


# ------------------------------- запуск claude -------------------------------

def _ver_key(name):
    """Ключ сортировки версии '2.1.197' → (2,1,197); нечисловое → (0,)."""
    nums = re.findall(r"\d+", name or "")
    return tuple(int(n) for n in nums) if nums else (0,)


_claude_cache = None


def _claude_candidates():
    """Явные кандидаты бинаря по ВСЕМ известным схемам установки claude-code (на случай, если
    автообновление сменит место): native-инсталлер (~/.local/bin), LOCALAPPDATA\\Programs, npm-шим."""
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    appdata = os.getenv("APPDATA") or os.path.join(home, "AppData", "Roaming")
    return [
        os.path.join(home, ".local", "bin", "claude.exe"),            # native-инсталлер (новая схема)
        os.path.join(home, ".local", "bin", "claude.cmd"),
        os.path.join(local, "Programs", "claude", "claude.exe"),      # LOCALAPPDATA\Programs
        os.path.join(local, "Programs", "claude-code", "claude.exe"),
        os.path.join(appdata, "npm", "claude.cmd"),                   # npm -g шим
    ]


def _claude_base_dirs():
    """Базы версий claude-code: обычная Roaming\\Claude\\claude-code + РЕАЛЬНАЯ MSIX-база
    AppData\\Local\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude\\claude-code. У Store/MSIX-установки
    Roaming\\Claude — виртуальный редирект, видимый ТОЛЬКО в интерактивной сессии; демон запущен
    Планировщиком и его не видит (FileNotFoundError). Реальный путь в Packages доступен всегда."""
    out = [_CLAUDE_BASE]
    home = os.path.expanduser("~")
    local = os.getenv("LOCALAPPDATA") or os.path.join(os.getenv("USERPROFILE") or home, "AppData", "Local")
    try:
        for cc in glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache",
                                         "Roaming", "Claude", "claude-code")):
            if cc not in out:
                out.append(cc)
    except Exception:
        pass
    return out


_RE_VER_DIR = re.compile(r"^\d+(?:\.\d+)+$")


def _pinned_version(path):
    """Версия ВЕРСИОННОЙ установки по пути (…/claude-code/2.1.217/claude.exe → (2,1,217)).
    Путь иной схемы (native-инсталлер, npm-шим, ручная копия) → None: такой пин трогать нельзя."""
    parent = os.path.basename(os.path.dirname(path or ""))
    return _ver_key(parent) if _RE_VER_DIR.match(parent or "") else None


def _newest_versioned():
    """(ключ_версии, путь) новейшей версионной установки по всем базам, или None.
    «Новейшая» — по ЧИСЛОВОМУ ключу: mtime каталога врёт (живой факт 22.07 — у 2.1.215 и 2.1.217
    совпал LastWriteTime, и выбор «по времени» отдавал СТАРУЮ)."""
    try:
        cands = []
        for base in _claude_base_dirs():
            for d in glob.glob(os.path.join(base, "*")):
                exe = os.path.join(d, "claude.exe")
                if os.path.isfile(exe):
                    cands.append((_ver_key(os.path.basename(d)), exe))
        if cands:
            cands.sort()
            return cands[-1]
    except Exception:
        pass
    return None


def _resolve_claude_once():
    """Один проход по всем местам (без ретраев). → путь | None. Порядок:
      1) PATH-шим: shutil.which('claude') — учитывает PATHEXT (claude.cmd/.exe/.bat);
      2) CLAUDE_BIN из .env — только если файл реально существует И не отстал от автообновления;
      3) НОВЕЙШАЯ версия claude-code по всем базам (вкл. реальную MSIX-Packages — фикс «не найден в бою»);
      4) явные кандидаты других схем установки (_claude_candidates).

    ПИН ВЕРСИИ — ПОЛ, А НЕ ПОТОЛОК. CLAUDE_BIN в .env указывает на конкретную версию и протухает
    на каждом автообновлении CLI. Мёртвый путь мы и раньше пропускали (os.path.isfile), но
    ЖИВОЙ, НО СТАРЫЙ пин молча побеждал новейшую установку — проверено живьём: при
    CLAUDE_BIN=…\\2.1.215\\claude.exe резолвер отдавал 2.1.215, хотя рядом стояла 2.1.217.
    Поэтому версионный пин уступает более новой версии. Пин ИНОЙ схемы (не …/<версия>/claude.exe)
    уважаем как есть — это осознанный выбор пути, а не отставшая версия. Осознанно замереть на
    старой версии можно рубильником CLAUDE_BIN_STRICT=1."""
    w = shutil.which("claude")                       # 1) PATH-шим (.cmd/.exe через PATHEXT)
    if w and os.path.isfile(w):
        return w
    newest = _newest_versioned()                     # 3) считаем заранее: нужен для сверки с пином
    if CLAUDE_BIN and os.path.isabs(CLAUDE_BIN) and os.path.isfile(CLAUDE_BIN):  # 2) .env, если жив
        strict = (os.getenv("CLAUDE_BIN_STRICT", "") or "").strip() not in ("", "0")
        pv = _pinned_version(CLAUDE_BIN)
        if strict or pv is None or newest is None or newest[0] <= pv:
            return CLAUDE_BIN
        log.info("CLAUDE_BIN(%s) отстал от автообновления — беру %s", CLAUDE_BIN, newest[1])
        return newest[1]
    if newest:
        return newest[1]
    for c in _claude_candidates():                   # 4) другие схемы установки
        if os.path.isfile(c):
            return c
    return None


def resolve_claude(retries=1, retry_sleep=2.0):
    """Найти исполняемый claude БЕЗ привязки к версии/месту установки. Кэшируем, но перепроверяем
    существование (переживаем автообновление). НЕ сдаёмся с первого прохода: transient-недоступность
    диска (стейджинг автообновления/антивирус — кейс задачи #35: файл был на месте, а isfile мигнул
    False) → короткий ретрай. → путь (str) | None (после ретраев — реально нигде нет)."""
    global _claude_cache
    if _claude_cache and os.path.isfile(_claude_cache):
        return _claude_cache
    _claude_cache = None
    for i in range(retries + 1):
        p = _resolve_claude_once()
        if p:
            _claude_cache = p
            return p
        if i < retries:
            log.warning("resolve_claude: не найден (проход %s: PATH/CLAUDE_BIN/%s/кандидаты) — "
                        "транзиент? повтор через %sс", i + 1, _CLAUDE_BASE, retry_sleep)
            time.sleep(retry_sleep)
    return None


# --- модель ИСПОЛНИТЕЛЯ headless-задач lane=pc (решение владельца 24.07.2026, тема 328;
# 30.07.2026 переведён на claude-opus-5 — «Opus 5 для наших задач лучше») ----------------------
# Раньше run_claude звал claude -p БЕЗ --model: модель ТИХО бралась из .claude/settings.json
# (дефолт интерактивных сессий репо). Env-ручки ORCH_MODEL / EXECUTOR_MODEL / EXECUTOR_EFFORT —
# имена VPS-полосы, ПК-код их НЕ читает: «запрошен sonnet — берётся чужая модель» ровно отсюда
# (мёртвая ручка в .env ничего не переключает). Правка .claude/settings.json — красная зона
# pretool_guard (вектор само-эскалации), поэтому источник правды исполнителя — ЭТА константа:
# смена модели = правка строки + коммит → self-update сам перезапустит демон штатным потоком.
# Env сознательно НЕ читаем (детерминизм: застрявший .env / унаследованный os.environ не смеют
# молча переключить модель — класс #194). SUGGEST_MODEL (клиентский suggest, sonnet) и
# THINKER_MODEL (думатель, claude-opus-5) не задеты — у них свои явные --model. Полный id
# обязателен: короткий алиас → HTTP 404 у claude -p (класс #194).
EXECUTOR_MODEL = "claude-opus-5"


def run_claude(prompt, timeout, cwd, env):
    """Запуск headless claude -p. Возврат (returncode, stdout, stderr). Таймаут → TimeoutError.
    Инъектируется в тестах (реальный claude не дёргаем). Путь резолвится версионно-независимо."""
    cbin = resolve_claude()
    if not cbin:
        raise FileNotFoundError("claude CLI не найден (PATH/.env/AppData)")
    try:
        # encoding=utf-8 + errors=replace: без явной кодировки text=True берёт локаль Windows
        # (cp1251) → кириллица в карточках 829 превращалась в кракозябры. replace → не падаем на
        # неведомом байте, а подставляем �. PYTHONIOENCODING=utf-8 ребёнку выставлен в run_task().
        # УРОВЕНЬ УСИЛИЙ ЯВНО (24.07.2026): claude -p принимает --effort. cwd=REPO уже даёт
        # effortLevel из .claude/settings.json НЕЯВНО, но клиентский флаг/cse-конфиг может его
        # перебить (класс rc-effort-override) — передаём явно из ТОГО ЖЕ источника правды
        # (settings.json через repo_thinking_settings, дефолт xhigh). МОДЕЛЬ ТОЖЕ ЯВНО
        # (24.07.2026, тема 328): --model EXECUTOR_MODEL — исполнитель на Opus, settings.json
        # остаётся про интерактивные сессии. prompt держим ПОСЛЕДНИМ.
        eff = task_metrics.norm_effort(repo_thinking_settings()[0])
        argv = [cbin, "-p", "--model", EXECUTOR_MODEL, "--effort", eff, prompt]
        p = subprocess.run(argv, cwd=cwd, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env,
                           creationflags=NO_WINDOW)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired:
        raise TimeoutError("claude -p timeout")


# --- бюджет процессов claude (зеркало VPS oom2 1520c68; инцидент-каскад 22.07) ---------------
# 22.07 владелец увидел 18×claude.exe (ПК тормозит): накопились агент-процессы старых сессий
# Claude Desktop. Демон каскад НЕ плодил (у всех PPID = Electron-app), но класс закрываем и
# здесь: ЛЮБОЙ наш спавн headless claude (run_task, думатель) сперва проверяет бюджет живых
# CLI-процессов claude.exe. Fail-open при недоступном счёте: бюджет — гард от каскада, а не
# жёсткий замок; битый CIM (класс #171) не смеет остановить работу демона.
#
# ВАРИАНТ A (счёт ПО РОДИТЕЛЮ, решение владельца 22.07 при переводе Remote Control в основной
# канал). Раньше считали ВСЕ CLI-claude на машине, поэтому в бюджет демона попадали ЧУЖИЕ
# процессы: интерактивные RC-сессии владельца и агент-сессии Claude Desktop (в живом снимке
# fixtures/claude_procs.live.json их 8 из 9!). При лимите 2 это глушило демона наглухо — работал
# бы не гард от каскада, а «владелец открыл сессию с телефона → демон встал». Теперь считаем
# ТОЛЬКО СВОИХ ПОТОМКОВ (цепочка PPID упирается в PID этого демона): headless-дети run_task/
# думателя и их субагенты. Почему не «поднять лимит до 4» (вариант B): потолок лишь отодвигается —
# 3 чужие сессии снова его выберут, а headless-каскад демона упрётся в тот же потолок. Счёт по
# родителю разделяет ДВА РАЗНЫХ явления, а не смешивает их в одном числе.

_claude_budget_denials = 0     # отказов бюджета ПОДРЯД (успешный проход сбрасывает)

# Снимок процессов для счёта по родителю. Фильтр claude.exe тот же (claude-code, без --type= —
# Electron-процессы UI это не CLI), но отдаём НЕ число, а PID/PPID/время создания + PID/PPID/время
# ВСЕХ процессов: по ним поднимаемся от каждого claude вверх, до себя. Формат снят живым пробой в
# fixtures/claude_procs.live.json — голдены стоят на нём (правило-класс «мок = живой формат»).
_PROC_SNAPSHOT_PS = (
    "$c = Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\" | "
    "Where-Object { $_.CommandLine -match 'claude-code' -and $_.CommandLine -notmatch '--type=' } | "
    "Select-Object ProcessId,ParentProcessId,"
    "@{n='Created';e={if ($_.CreationDate) { $_.CreationDate.ToString('yyyyMMddHHmmss') } else { '' }}}; "
    "$t = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,"
    "@{n='Created';e={if ($_.CreationDate) { $_.CreationDate.ToString('yyyyMMddHHmmss') } else { '' }}}; "
    "ConvertTo-Json -Compress -Depth 3 @{claude=@($c);tree=@($t)}"
)
_ANCESTRY_MAX_HOPS = 12        # предохранитель от петли/битого дерева PPID


def _is_descendant(pid, root, parents, created, hops=_ANCESTRY_MAX_HOPS):
    """pid — потомок root? Идём вверх по PPID до root или до обрыва. → bool.

    Цепь рвём, если родитель СОЗДАН ПОЗЖЕ ребёнка: Windows переиспользует PID, и PPID мёртвого
    родителя может указывать на чужой свежий процесс. Это не теория — живой факт ЭТОГО ПК:
    у userbot/moderbot/демона PPID=5980, а процесса 5980 в системе уже нет."""
    seen = set()
    cur = pid
    for _ in range(hops):
        if cur in seen:
            return False                       # петля в дереве — считаем «не наш»
        seen.add(cur)
        parent = parents.get(cur)
        if not parent or parent == cur:
            return False                       # корень дерева / самоссылка
        c_child, c_parent = created.get(cur, ""), created.get(parent, "")
        if c_child and c_parent and c_parent > c_child:
            return False                       # родитель моложе ребёнка → PID переиспользован
        if parent == root:
            return True
        cur = parent
    return False


def _count_claude_procs(runner=None, own_pid=None):
    """Число ЖИВЫХ CLI-claude.exe, порождённых ИМЕННО ЭТИМ демоном (headless-дети run_task/
    думателя и их субагенты). Интерактивные сессии владельца — RC-сессия (родитель
    rc_supervisor.py) и агент-сессии Claude Desktop (родитель Electron) — В БЮДЖЕТ НЕ ВХОДЯТ.
    → int | None (powershell/CIM не смог или ответ не разобрался — fail-open).
    own_pid — инъекция корня для тестов (по умолчанию PID текущего процесса)."""
    try:
        p = (runner or subprocess.run)(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PROC_SNAPSHOT_PS],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("бюджет claude: счёт процессов не удался (%s) — fail-open", e)
        return None
    out = (p.stdout or "").strip().lstrip("﻿")
    if p.returncode != 0 or not out.startswith("{"):
        return None
    try:
        data = json.loads(out)
        claude, tree = data["claude"], data["tree"]
    except Exception as e:
        log.warning("бюджет claude: снимок процессов не разобран (%s) — fail-open", e)
        return None
    if isinstance(claude, dict):          # ConvertTo-Json схлопывает список из ОДНОГО элемента
        claude = [claude]
    if isinstance(tree, dict):
        tree = [tree]
    parents, created = {}, {}
    for e in list(tree) + list(claude):
        try:
            pid = int(e["ProcessId"])
        except Exception:
            continue
        parents.setdefault(pid, int(e.get("ParentProcessId") or 0))
        created.setdefault(pid, str(e.get("Created") or ""))
    root = os.getpid() if own_pid is None else own_pid
    n = 0
    for e in claude:
        try:
            pid = int(e["ProcessId"])
        except Exception:
            continue
        if _is_descendant(pid, root, parents, created):
            n += 1
    return n


def _claude_budget_gate(counter=None, sleeper=None, notifier=None, wait_sec=None, poll_sec=5):
    """Гейт бюджета ПЕРЕД спавном headless claude: НАШИХ живых headless-claude ≥ MAX_CLAUDE_PROCS
    → ждём слот до wait_sec (кто-то завершится), не дождались → отказ. 3 отказа ПОДРЯД → карточка
    владельцу (демон плодит своих детей — каскад, нужен разбор) и счётчик заново (карточку не
    спамим каждый отказ). Счёт недоступен (CIM boom) → fail-open. Интерактивные RC-сессии
    владельца в счёт НЕ входят (вариант A, счёт по родителю) — они демона не блокируют.
    → (ok: bool, detail: str). counter/sleeper/notifier — инъекция для тестов."""
    global _claude_budget_denials
    count = (counter or _count_claude_procs)()
    if count is None or count < MAX_CLAUDE_PROCS:
        _claude_budget_denials = 0
        return True, f"наших headless-claude: {'?' if count is None else count}/{MAX_CLAUDE_PROCS}"
    wait_sec = CLAUDE_BUDGET_WAIT_SEC if wait_sec is None else wait_sec
    _sleep = sleeper or time.sleep
    slept = 0
    while slept < wait_sec:
        _sleep(poll_sec)
        slept += poll_sec
        count = (counter or _count_claude_procs)()
        if count is None or count < MAX_CLAUDE_PROCS:
            _claude_budget_denials = 0
            return True, f"дождались слота через {slept}s (живых: {'?' if count is None else count})"
    _claude_budget_denials += 1
    detail = (f"НАШИХ headless-claude {count} ≥ лимит {MAX_CLAUDE_PROCS}, слот не освободился за "
              f"{wait_sec}s (отказ №{_claude_budget_denials} подряд)")
    log.error("бюджет claude: %s", detail)
    if _claude_budget_denials >= 3:
        (notifier or _notify_critical)(
            f"⚠️ Бюджет claude-процессов ПК: {_claude_budget_denials} отказов подряд — НАШИХ "
            f"headless-claude {count} ≥ лимит {MAX_CLAUDE_PROCS} (счёт по родителю: интерактивные "
            "RC-сессии сюда НЕ входят). Значит каскад плодит сам демон — нужен разбор на ПК.")
        _claude_budget_denials = 0
    return False, detail


def _approved_kinds(item):
    """Виды красных операций, ОДОБРЕННЫЕ владельцем для этой задачи → frozenset (может быть пустым).

    Источник — СОХРАНЁННАЯ карточка needs_approval того же ряда очереди (`what`/`result`
    возвращаются вместе с approved-задачей; на полосе сервера этот же приём — `task.get("result")`
    в orchestrator_daemon.process_approved). Значит одобрение переживает и рестарт демона: класс
    едет через очередь, локального состояния не нужно.

    Пусто (карточки нет, класс не назван, `op=other`, модуль гарда не импортировался) → демон
    работает БАЙТ-В-БАЙТ как раньше: маркер в env не поедет, красное снова даст ✋."""
    txt = str((item or {}).get("what") or (item or {}).get("result") or "")
    if not txt.strip() or pretool_guard is None:
        return frozenset()
    try:
        return pretool_guard.kinds_from_card(txt)
    except Exception as e:                    # разбор карточки НИКОГДА не роняет обработку approve
        log.warning("класс одобренной карточки не разобран (%s) — прежний путь", e)
        return frozenset()


# ── ВЫСШИЙ ВИД КАРТОЧКИ ПОДТВЕРЖДАЕТСЯ ОБЪЕКТОМ, А НЕ «ДА N» (31.07.2026) ─────────────────────
# Повод: владелец подтвердил три карточки `live_sheet` подряд не читая. Гард теперь печатает у
# необратимых операций шапку с ОБЪЕКТОМ и ждёт ответ, который этот объект называет
# (`pretool_guard.approval_covers`). Демону остаётся достать ответ владельца из ряда очереди и
# прокинуть ребёнку — это и делают `_owner_reply` / `_approved_scope`.
#
# ЧЕСТНАЯ ГРАНИЦА, которую важно понимать при чтении: сегодня ряд очереди возвращает КАРТОЧКУ
# (`what`/`result`), а ТЕКСТ ОТВЕТА владельца в него не кладётся — «да» приезжает сюда только
# сменой статуса на approved. Поэтому поля ответа перебираются списком кандидатов, и пока ни
# одно из них не приходит, высший вид НЕ получает авто-пропуск: задача честно закрывается
# «выполни вручную» с названной причиной. Это FAIL-CLOSED и намеренно: канал, который объект не
# донёс, обязан закрывать необратимое, а не открывать его. Обычный вид работает как работал.
_REPLY_FIELDS = ("owner_reply", "reply", "answer", "approval_text", "approved_text",
                 "decision_text", "comment", "note")


def _looks_like_card(raw):
    """→ True ⇔ это текст САМОЙ карточки, а не ответ на неё. Узнаём ПО СТРУКТУРЕ (строка
    «Объект: …», строка класса), а не по вхождению подстроки: шапка высшего вида ПОКАЗЫВАЕТ
    образец ответа («да Зарплаты»), поэтому правильный ответ — всегда подстрока карточки, и
    отсев «ответ содержится в карточке» вычеркнул бы ровно то, что надо принять.
    Сбой разбора → True: принять карточку за ответ хуже, чем не принять ответ (fail-closed)."""
    r = str(raw or "")
    if not r.strip():
        return False
    if pretool_guard is None:
        return "Объект:" in r
    try:
        return bool(pretool_guard.object_from_card(r)) or pretool_guard.KIND_LINE_PREFIX in r
    except Exception:
        return True


def _owner_reply(item):
    """ТЕКСТ ОТВЕТА владельца на карточку → str ('' — ответа в ряду очереди нет).
    Сама карточка ответом НЕ считается: в ней объект напечатан, и приняв её за ответ, мы бы
    подтверждали карточку ею же самой."""
    it = item or {}
    card = " ".join(str(it.get("what") or "").split())
    out = []
    for f in _REPLY_FIELDS:
        raw = it.get(f)
        v = " ".join(str(raw or "").split())
        if v and v != card and not _looks_like_card(raw):
            out.append(v)
    return " ".join(out)


def _approved_scope(item):
    """→ (виды, объект). Обычный вид — по классу, как с 30.07. ВЫСШИЙ вид проходит ТОЛЬКО тогда,
    когда владелец НАЗВАЛ в ответе объект из карточки; иначе вид из одобрения вычёркивается, и
    шаг снова упрётся в красное (то есть операция не исполнится)."""
    kinds = _approved_kinds(item)
    if not kinds or pretool_guard is None:
        return kinds, ""
    try:
        top = frozenset(k for k in kinds if pretool_guard.is_top_tier(k))
        if not top:
            return kinds, ""
        card = str((item or {}).get("what") or (item or {}).get("result") or "")
        obj, reply = pretool_guard.object_from_card(card), _owner_reply(item)
        if obj and pretool_guard.reply_confirms_object(reply, obj):
            log.info("одобрение высшего вида: объект «%s» назван владельцем в ответе", obj)
            return kinds, reply
        log.info("одобрение высшего вида БЕЗ объекта (карточка: «%s», ответ: «%s») → классы %s "
                 "вычеркнуты", obj, _clip(reply, 80), ",".join(sorted(top)))
        return frozenset(kinds - top), ""
    except Exception as e:                    # разбор НИКОГДА не роняет обработку approve
        log.warning("объект одобрения не разобран (%s) — высший вид не пропускаем", e)
        return frozenset(), ""


def _run_task_impl(tid, text, note="", _mctx=None, approved=(), approved_object=""):
    """Исполнить задачу через headless claude -p. → (status, result). status ∈ done|failed|needs_approval.
    Контракт результата (фикс ложного done задачи #24): done ТОЛЬКО при непустом stdout со строкой
    «RESULT: <итог>»; пустой stdout → один авто-повтор (транзиент), снова пустой → failed с хвостом
    stderr; непустой без RESULT → failed insufficient_output. Диагноз (stderr) всегда в карточке+логе."""
    marker_path = os.path.join(REPO, f"pc_ask_{tid}.marker")
    cbin = resolve_claude()                       # версионно-независимый резолв (класс-фикс WinError 2)
    if not cbin:
        msg = ("claude CLI не найден (после ретрая): нет ни в PATH, ни в CLAUDE_BIN (.env), ни в "
               + os.path.join(_CLAUDE_BASE, "<версия>", "claude.exe")
               + ", ни в кандидатах (~/.local/bin, LOCALAPPDATA\\Programs, npm). "
               "Проверь установку/автообновление claude-code.")
        log.error("id=%s НЕ НАЙДЕН claude: %s", tid, msg)
        return "failed", fail_result(FAIL_EXEC_ERROR, msg, since=_task_started_get(tid))
    ok_budget, budget_detail = _claude_budget_gate()   # бюджет процессов claude ПЕРЕД спавном (1520c68)
    if not ok_budget:
        log.error("id=%s бюджет claude исчерпан: %s — headless НЕ запущен", tid, budget_detail)
        return "failed", fail_result(FAIL_EXEC_ERROR,
                                     f"бюджет claude-процессов исчерпан ({budget_detail}) — headless "
                                     "не запущен; задача уйдёт обычным путём ретрая",
                                     since=_task_started_get(tid))
    run_token = f"{os.getpid()}-{int(time.time() * 1000)}-{tid}"   # контекст запуска: pid+ts+tid
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # headless идёт по ~/.claude (подписка), не платный API
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"            # ребёнок пишет stdout/stderr в utf-8 → нет кракозябр (пара к encoding в run_claude)
    env[ASK_MARKER_ENV] = marker_path            # pretool_guard в headless пишет сюда красную карточку
    env[MARKER_TOKEN_ENV] = run_token            # …штампуя её нашим токеном — чужие карточки отсеем
    prompt = (note + PREAMBLE) if note else PREAMBLE
    # ОДОБРЕНИЕ — ДВА КАНАЛА (см. APPROVED_CLAUSE): гарду env-маркер, модели абзац ПОСЛЕ преамбулы.
    # Пустой approved → обе строки не выполняются, поведение прежнее байт-в-байт.
    kinds = sorted(k for k in (str(x).strip() for x in (approved or ())) if k)
    if kinds:
        env[APPROVED_KINDS_ENV] = ",".join(kinds)
        env[APPROVED_TASK_ENV] = str(tid)
        # Объект, НАЗВАННЫЙ владельцем в ответе: без него гард высший вид не пропустит
        # (`pretool_guard.approval_covers`). Пусто → переменной нет, обычный вид не задет.
        if approved_object:
            env[APPROVED_OBJECT_ENV] = approved_object
        prompt += APPROVED_CLAUSE.format(kinds=", ".join(kinds))
        log.info("id=%s ОДОБРЕНО владельцем, класс(ы)=%s объект=%s → маркер в env ребёнка + "
                 "абзац в промпт", tid, ",".join(kinds), approved_object or "—")
    prompt += text
    for attempt in (1, 2):
        if _mctx is not None:
            _mctx["attempts"] = attempt   # число headless-попыток этого запуска (1 или 2) для METRICS
        try:  # ЧИСТИМ маркер ПЕРЕД КАЖДОЙ попыткой headless (в т.ч. перед авто-повтором):
            if os.path.exists(marker_path):   # иначе красная карточка прошлого прогона протекла бы
                os.remove(marker_path)        # в результат нового (ложное needs_approval).
        except Exception:
            pass
        log.info("RUN id=%s (timeout=%ss, попытка %s/2)", tid, TASK_TIMEOUT, attempt)
        try:
            rc, out, err = run_claude(prompt, TASK_TIMEOUT, REPO, env)
        except TimeoutError:
            log.warning("id=%s ТАЙМАУТ %ss → failed", tid, TASK_TIMEOUT)
            return "failed", fail_result(FAIL_RUN_TIMEOUT,
                                         f"таймаут {TASK_TIMEOUT}s — headless прерван, задача не "
                                         "завершилась", since=_task_started_get(tid))
        except Exception as e:
            log.error("id=%s ошибка запуска: %s", tid, e)
            return "failed", fail_result(FAIL_EXEC_ERROR, f"ошибка запуска claude: {e}",
                                         since=_task_started_get(tid))
        # прочитать маркер ДО удаления (гард пишет туда красную карточку в headless)
        marker_content = ""
        try:
            if os.path.isfile(marker_path):
                with open(marker_path, encoding="utf-8", errors="ignore") as mf:
                    marker_content = mf.read().strip()
        except Exception:
            pass
        try:
            os.remove(marker_path)
        except Exception:
            pass
        card = _detect_needs_approval(out, marker_content, run_token)
        if card is not None:
            log.info("id=%s NEEDS_APPROVAL", tid)
            return "needs_approval", card
        out_s = (out or "").strip()
        err_tail = _tail(err)
        if rc != 0:
            # класс «самомодификация → ложный failed» (порт 48d9c64): claude погашен ПЛАНОВЫМ
            # self-update-рестартом демона (4 признака) — это НЕ сбой → done, думателя НЕ зовём.
            # Под флагом STEP_SELFHEAL (=0 → байт-в-байт прежний failed ниже).
            if _selfheal_on() and _killed_by_planned_restart(rc, text):
                log.info("id=%s claude погашен ПЛАНОВЫМ рестартом демона (rc=%s) → done (плановый)", tid, rc)
                return "done", ("🔁 Завершено плановым рестартом демона (самомодификация "
                                "pc_orchestrator): claude-процесс задачи штатно погашен в окне "
                                "управляемого self-update-рестарта — работа к этому моменту сделана "
                                "(RESULT в логе, коммит в git). Это НЕ сбой.")[:RESULT_MAX]
            return "failed", fail_result(FAIL_EXEC_ERROR,
                                         f"claude exit={rc}: " + (out_s or err_tail or "нет вывода"),
                                         since=_task_started_get(tid))
        if not out_s:
            if attempt == 1:   # один авто-повтор: пустой stdout бывает транзиентом
                log.warning("id=%s пустой stdout (rc=0) — авто-повтор; stderr: %s",
                            tid, err_tail or "(пуст)")
                continue
            log.error("id=%s ПУСТОЙ ВЫВОД ×2 → failed; stderr: %s", tid, err_tail or "(пуст)")
            # «работа не выполнялась» тут больше НЕ утверждаем от себя: пустой stdout говорит
            # только о молчании канала, а был ли коммит — скажут следы (класс 30.07).
            return "failed", fail_result(FAIL_EXEC_ERROR,
                                         "пустой вывод claude (2 попытки) | stderr: "
                                         + (err_tail or "(пуст)"), since=_task_started_get(tid))
        if not _RE_RESULT.search(out_s):   # вывод есть, но итог не подтверждён строкой RESULT:
            log.warning("id=%s insufficient_output (нет «RESULT:») → failed", tid)
            return "failed", fail_result(FAIL_EXEC_ERROR,
                                         "insufficient_output: нет строки «RESULT: <итог>» — "
                                         "выполнение не подтверждено. stdout(хвост): " + _tail(out_s)
                                         + (" | stderr(хвост): " + err_tail if err_tail else ""),
                                         since=_task_started_get(tid))
        return "done", out_s[:RESULT_MAX]


def run_task(tid, text, note="", approved=(), approved_object=""):
    """Обёртка-наблюдаемость над _run_task_impl: та же сигнатура/возврат, но по завершении пишет
    ОДНУ структурную строку METRICS в лог демона (модель/усилие/тайминги/исход/попытки/самопочинки/
    канал). tokens_in/out=na — ПК-исполнитель в ТЕКСТ-режиме (claude -p без --output-format json
    токены не отдаёт). Замер в try/except — его сбой НИКОГДА не меняет исход задачи."""
    _mctx = {"attempts": 0}   # 0 = ни одной headless-попытки (ранний выход: claude не найден / бюджет)
    _t0 = time.monotonic()
    _start = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    status, result = _run_task_impl(tid, text, note, _mctx, approved, approved_object)
    try:
        eff, _ = repo_thinking_settings()
        log.info(task_metrics.metrics_line(
            task=tid, lane="pc", model=EXECUTOR_MODEL, effort=task_metrics.norm_effort(eff),
            start_iso=_start,
            end_iso=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            dur_s=time.monotonic() - _t0, outcome=status, attempts=_mctx.get("attempts", 0),
            selfheals=task_metrics.selfheal_count(text, note),
            tokens_in=None, tokens_out=None,
            task_text=text,
            mode=("test" if _UNDER_TEST else "prod"),
            src=(os.path.basename((sys.argv[0] if sys.argv else "") or "") or None)))
    except Exception as _e:
        log.warning("METRICS не записан (pc id=%s): %s", tid, _e)
    return status, result


# ------------------------------- обработчики цикла ---------------------------

def _human(kind, tid, text):
    first = (str(text or "").strip().splitlines() or ["(пусто)"])[0][:300]
    if kind == "done":
        return f"✅ Оркестратор: задача #{tid} выполнена — {first}"
    if kind == "failed":
        # «провалена» ложь, когда работа В ОКНЕ ЗАДАЧИ была: заголовок обязан различать
        # незакрытую-но-сделанную и по-настоящему несделанную (класс 30.07, задачи 54/61).
        if WORK_DONE_MARK in str(text or ""):
            return (f"⚠️ Оркестратор: задача #{tid} — в окне есть работа, закрытие не состоялось "
                    f"— {first}")
        return f"❌ Оркестратор: задача #{tid} провалена — {first}"
    if kind == "needs_approval":
        return f"🔔 Оркестратор: задача #{tid} ждёт твоего «да» — {first}"
    return f"Оркестратор: задача #{tid}"


# ------------------- команды-рычаги (прямое исполнение без headless) ----------
# Одиночные lane=pc задачи-команды («рестартни userbot» / «рестартни модербот» / «статус контура»)
# демон исполняет САМ (полномочия вотчдога), мгновенно, без headless claude. Распознавание — по
# ЯКОРНЫМ паттернам (НЕ LLM, НЕ подстрока): совпадает, только когда ВЕСЬ текст задачи и есть команда,
# поэтому дев-задачи («тз: … рестартни …») и путь темы 205 не перехватываются. pc_agent командой не
# рестартим (агент себя чужими руками не трогает).
_CMD_RESTART_UB = re.compile(r"^\s*(?:рестартни|рестарт|перезапусти|restart)\s+(?:userbot|юзербот)\s*[.!]*\s*$", re.I)
_CMD_RESTART_MB = re.compile(r"^\s*(?:рестартни|рестарт|перезапусти|restart)\s+(?:модербот|moderbot|moderation[_ ]?bot|модербот)\s*[.!]*\s*$", re.I)
_CMD_STATUS = re.compile(r"^\s*статус\s+контура\s*[.!?]*\s*$", re.I)
# «да» воротам клиентского контура: разрешить применить ТЕКУЩИЙ HEAD к живым ботам. Отдельная
# команда, а не «рестартни userbot»: рычаг рестарта поднимает бота ПРЯМО СЕЙЧАС мимо всех проверок,
# а «выкати» — это ОСНОВАНИЕ, после которого применение идёт ШТАТНЫМ путём, со всеми прежними
# гейтами (тесты, анти-флап, запрет грязного дерева). Якорь тот же: совпадает, только когда ВЕСЬ
# текст задачи и есть команда, — дев-задача «тз: … выкати …» сюда не проваливается.
_CMD_RELEASE = re.compile(
    r"^\s*(?:да[,\s]+)?(?:выкат(?:и|ывай)|раскати|примени(?:ть)?)\s*"
    r"(?:это\s+|коммит\s*|правку\s*|на\s+)?\s*(?:клиент\w*|прод\w*|бот\w*|userbot|модербот)?\s*[.!]*\s*$",
    re.I)


def _match_command(text):
    """Распознать команду-рычаг по якорным паттернам (весь текст = команда). →
    'restart_userbot'|'restart_moderbot'|'status'|'release_client' | None (не команда → headless-путь)."""
    t = str(text or "")
    if _CMD_STATUS.match(t):
        return "status"
    if _CMD_RELEASE.match(t):
        return "release_client"
    if _CMD_RESTART_UB.match(t):
        return "restart_userbot"
    if _CMD_RESTART_MB.match(t):
        return "restart_moderbot"
    return None


def _proc_line(label, pids, extra=""):
    base = (f"{label}: жив (PID {', '.join(map(str, pids))})" if pids else f"{label}: НЕ ЖИВ")
    return base + (f" · {extra}" if extra else "")


def _contour_status(finder=None, items=None, revizor_state=None):
    """Статус клиентского контура: живость+PID userbot/moderation_bot/pc_agent/pc_orchestrator,
    свежесть heartbeat демона, секция «в работе» (ТОЛЬКО живые локальные цепи — без призраков
    финализированных родителей) и честная строка последнего тика ревизора. → многострочный текст
    (в результат задачи). finder/items/revizor_state — для тестов (иначе живые источники)."""
    find = finder or _find_pids_by_script
    ub, mb = find("userbot_listen.py"), find("moderation_bot.py")
    ag, orch = find("pc_agent.py"), find("pc_orchestrator.py")
    mb_extra = "" if mb else ("нет MODERBOT_TOKEN → reply-режим (штатно)"
                              if not os.getenv("MODERBOT_TOKEN", "").strip() else "")
    try:
        hb = open(HEARTBEAT_FILE, encoding="utf-8").read().strip()
        age = _age_sec(hb)
        hb_txt = ("нет" if age is None else
                  f"свеж ({int(age)}с назад)" if age <= HEARTBEAT_STALE else f"ПРОТУХ ({int(age)}с назад)")
    except Exception:
        hb_txt = "нет"
    lines = ["📊 Статус контура:",
             _proc_line("userbot", ub),
             _proc_line("moderation_bot", mb, mb_extra),
             _proc_line("pc_agent", ag),
             _proc_line("pc_orchestrator", orch, f"heartbeat {hb_txt}"),
             "🔧 В работе:"]
    # Снимок очереди берём сами (если не подан). None = Bridge молчит: НЕ выдаём «ТИХО» (это было бы
    # ложью «работы нет»), честно говорим «очередь недоступна». Пустой снимок/нет живых цепей → 🟢 ТИХО.
    snap = items if items is not None else _loc_fetch_items()
    if snap is None:
        lines.append("  очередь недоступна (Bridge молчит)")
    else:
        active = _loc_active_chains(snap)
        if active:
            lines += [f"  • цепь {ch['pid']}: {ch['label']}" for ch in active]
        else:
            lines.append("  🟢 ТИХО")
    lines.append(_revizor_tick_label(revizor_state))
    return "\n".join(lines)


def _exec_release_client(head_fn=None, approve_fn=None, cowork=None):
    """«ДА» ВЛАДЕЛЬЦА воротам клиентского контура: записать основание на ТЕКУЩИЙ HEAD.
    Сам рестарт здесь НЕ делаем — применение пойдёт штатной реконсиляцией детей, со всеми прежними
    проверками (гейт затронутых тестов, анти-флап, запрет грязного дерева). Так «да» остаётся
    решением о ПРАВЕ выкатить, а не обходом гейтов. → (status, result)."""
    commit = (head_fn or _head_commit)()
    ok, res = (approve_fn or client_contour.approve)(commit)
    if not ok:
        return "failed", f"основание не записано: {res}"
    _CLIENT_HELD_WARNED.clear()      # решение изменилось — следующий отказ снова заслуживает карточки
    log.info("ворота контура: владелец разрешил применение коммита %s", commit)
    (cowork or _cowork)(f"ворота клиентского контура: владелец сказал «да» на {commit} — "
                        f"применение пойдёт штатной реконсиляцией")
    return "done", (f"✅ Ворота клиентского контура открыты на коммит {commit}. Применю штатно "
                    f"(реконсиляция детей, ≤{CHILD_RECONCILE_SEC} с): гейт тестов, анти-флап и "
                    f"запрет грязного дерева остаются на месте.")


def _exec_command(cmd, restart_fn=None, status_fn=None):
    """Исполнить команду-рычаг НАПРЯМУЮ (полномочия вотчдога), без headless. → (status, result).
    Рестарт уважает рубильник и штампует анти-флап-реестр (не воюет с авто-применением кода)."""
    if cmd == "status":
        return "done", (status_fn or _contour_status)()
    if cmd == "release_client":
        return _exec_release_client()
    kind = "userbot" if cmd == "restart_userbot" else "moderbot"
    if _stopped():
        return "failed", "рубильник pc_orchestrator.stop активен — рестарт не выполняю"
    try:
        ok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
    except Exception as e:
        return "failed", f"{kind}: исключение рестарта: {e}"
    _stamp_apply_restart(kind)
    if ok and pids:
        return "done", f"🔁 {kind} перезапущен напрямую (рычаг вотчдога): {detail}, PID {', '.join(map(str, pids))}"
    if ok:
        return "done", f"🔁 {kind} (рычаг): {detail}"
    return "failed", f"{kind}: рестарт не удался — {detail}"


# ---- провал коммита урока НЕ смеет вечно блокировать авто-фетч (пакет «полнота лога», п.6) ----
# Класс: НАДЗОР-урок дописал строку в docs/revizor_checklist.md, а git commit сорвался (index.lock
# параллельного git / хук / битый конфиг). Раньше файл оставался ГРЯЗНЫМ навсегда →
# git_ff_pull_tick видел tracked-грязь и пропускал pull ВЕЧНО (+NOTE «грязная» каждые GIT_PULL_SEC).
# Фикс: добавленные уроком СТРОКИ уводим в спул (gitignored: pc_orchestrator.*.json), пути
# откатываем к HEAD (дерево чистое — авто-фетч жив), владельцу карточка, а коммит повторяется
# следующим циклом демона: строки НАКЛАДЫВАЮТСЯ на актуальный файл (за это время мог пройти
# ff-pull — тупая перезапись затёрла бы чужие строки чек-листа), затем штатный add+commit.

LESSON_RETRY_FILE = os.path.join(REPO, "pc_orchestrator.lesson_retry.json")
LESSON_RETRY_SEC = int(os.getenv("PC_LESSON_RETRY_SEC", "300") or "300")
_lesson_retry_last = 0.0


def _lesson_retry_load(path=None):
    """Спул недокоммиченного урока → dict | None (нет/бит/пуст)."""
    try:
        with open(path or LESSON_RETRY_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("added") else None
    except Exception:
        return None


def _lesson_retry_save(data, path=None):
    try:
        with open(path or LESSON_RETRY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)
        return True
    except Exception as e:                            # noqa: BLE001 — спул не роняет тик
        log.warning("LESSON retry: спул не записан: %s", e)
        return False


def _lesson_retry_clear(path=None):
    try:
        os.remove(path or LESSON_RETRY_FILE)
    except OSError:
        pass


def _lesson_added_lines(rel, call):
    """Строки, которые правка урока ДОБАВИЛА в tracked-файл против HEAD (по ним повторим коммит).
    git show недоступен → считаем базой пустоту: лишние «добавленные» строки безопасны — наложение
    на актуальный файл пропускает уже существующие."""
    try:
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            want = f.read()
    except OSError:
        return []
    show = call(["show", "HEAD:" + rel.replace(os.sep, "/")])
    base = show[1] if show and show[0] == 0 else ""
    have = set(base.splitlines())
    return [ln for ln in want.splitlines() if ln.strip() and ln not in have]


def _lesson_commit_spool(tid, route, subject, msg, paths, detail, ack_where=None,
                         card_msg_id=None, call=None, path=None, notify=None):
    """Провал коммита урока: правку — в спул, пути — к HEAD (дерево чистое), владельцу — карточку.
    Повтор сделает lesson_commit_retry_tick следующим циклом. FAIL-SAFE: сбои глотаем, но карточка
    честно говорит, удался ли откат."""
    call = call or _git_call
    added = {}
    for rel in paths:
        lines = _lesson_added_lines(rel, call)
        if lines:
            added[rel] = lines
    rb = call(["checkout", "HEAD", "--"] + paths)     # дерево к HEAD в ЛЮБОМ случае (блокер снят)
    rolled = bool(rb and rb[0] == 0)
    if not added:
        log.warning("LESSON id=%s: коммит не удался (%s), но добавленных строк не нашли — "
                    "только откат к HEAD (%s), повторять нечего",
                    tid, detail, "ok" if rolled else "не удался")
        return
    _lesson_retry_save({"tid": tid, "route": route, "msg": msg, "paths": paths, "added": added,
                        "ack_subject": subject or "", "ack_where": ack_where or "",
                        "card_msg_id": card_msg_id or "", "attempts": 0, "detail": detail}, path)
    (notify or _notify_critical)(
        f"⚠️ Урок #{tid} [{route}]: git commit не прошёл ({detail}). Правка чек-листа снята с "
        f"рабочего дерева (откат к HEAD{'' if rolled else ' НЕ УДАЛСЯ — дерево может быть грязным'}) "
        "и сохранена в спул — демон докоммитит её следующим циклом. Авто-фетч не заблокирован.")
    _cowork(f"урок #{tid}: коммит не удался ({detail}) — правка в спуле, откат к HEAD, "
            "повтор следующим циклом")


def _commit_lesson(tid, route, subject, paths, call_fn=None, ack_where=None, card_msg_id=None,
                   retry_path=None, notify=None):
    """Закоммитить ЗАКОММИЧЕННЫЙ-диф урока (родитель 292, шаг 4): tracked-файлы, которые правка урока
    изменила (НАДЗОР → docs/revizor_checklist.md). Стейджим ТОЛЬКО эти пути (без -A: служебные
    heartbeat/*.json и чужие правки не тащим); нет реальной правки в индексе → None (коммитить нечего,
    напр. СТИЛЬ пишет в gitignored playbook manager-bot — свой контур). → короткий хеш | None.
    ПРОВАЛ коммита НЕ оставляет пути грязными (вечный блокер авто-фетча): правка уходит в спул,
    пути откатываются к HEAD, владельцу карточка, повтор — следующим циклом (см. блок выше)."""
    call = call_fn or _git_call
    paths = [p for p in (paths or []) if p]
    if not paths:
        return None
    msg = f"#292 урок [{route}] задача #{tid}: {(subject or '').strip()[:80]}".rstrip(": ")
    ok_add = call(["add", "--"] + paths)
    if ok_add and ok_add[0] == 0:
        staged = call(["diff", "--cached", "--name-only", "--"] + paths)
        if staged and staged[0] == 0 and not (staged[1] or "").strip():
            return None                              # правки нет (дедуп/уже закоммичено) → без коммита
        rc = call(["commit", "-m", msg, "--"] + paths) if staged and staged[0] == 0 else None
        if rc and rc[0] == 0:
            nh = call(["rev-parse", "--short", "HEAD"])
            return nh[1] if nh and nh[0] == 0 else None
        detail = _tail(rc[2], 160) if rc else (_tail(staged[2], 160) if staged else "git недоступен")
    else:
        detail = _tail(ok_add[2], 160) if ok_add else "git недоступен"
    log.warning("LESSON id=%s: коммит урока не удался (%s) — правка в спул, пути к HEAD", tid, detail)
    _lesson_commit_spool(tid, route, subject, msg, paths, detail, ack_where=ack_where,
                         card_msg_id=card_msg_id, call=call, path=retry_path, notify=notify)
    return None


def lesson_commit_retry_tick(call_fn=None, path=None):
    """Повтор коммита урока из спула. Добавленные уроком строки накладываются на АКТУАЛЬНЫЙ файл
    (пропуская уже существующие), затем штатный add+diff+commit. Успех → спул снят + запоздалое
    подтверждение учителю (инвариант «ack только после коммита» цел). Строки уже в HEAD (pull/руки
    принесли) → спул снят без коммита. Провал → снова откат к HEAD и попытка следующим циклом.
    → строка-итог для лога | '' (спула нет)."""
    data = _lesson_retry_load(path)
    if not data:
        return ""
    call = call_fn or _git_call
    tid = data.get("tid")
    paths = [p for p in (data.get("paths") or []) if p]
    for rel, lines in (data.get("added") or {}).items():
        fp = os.path.join(REPO, rel)
        try:
            cur = ""
            try:
                with open(fp, encoding="utf-8") as f:
                    cur = f.read()
            except FileNotFoundError:
                pass
            have = set(cur.splitlines())
            missing = [ln for ln in lines if ln not in have]
            if not missing:
                continue
            body = (cur.rstrip("\n") + "\n") if cur.strip() else ""
            with open(fp, "w", encoding="utf-8") as f:
                f.write(body + "\n".join(missing) + "\n")
        except OSError as e:
            log.warning("LESSON retry: файл %s недоступен: %s", rel, e)
    ok_add = call(["add", "--"] + paths)
    staged = (call(["diff", "--cached", "--name-only", "--"] + paths)
              if ok_add and ok_add[0] == 0 else None)
    if staged and staged[0] == 0 and not (staged[1] or "").strip():
        _lesson_retry_clear(path)                     # всё уже в HEAD — повторять нечего
        _cowork(f"урок #{tid}: повтор коммита не нужен — строки уже в HEAD, спул снят")
        return "уже в HEAD — спул снят"
    rc = (call(["commit", "-m", str(data.get("msg") or f"#292 урок: повтор #{tid}"), "--"] + paths)
          if staged and staged[0] == 0 else None)
    if rc and rc[0] == 0:
        nh = call(["rev-parse", "--short", "HEAD"])
        commit = nh[1] if nh and nh[0] == 0 else None
        _lesson_retry_clear(path)
        log.info("LESSON id=%s: повторный коммит урока прошёл (%s) — спул снят", tid, commit or "?")
        _cowork(f"урок #{tid}: повторный коммит прошёл ({commit or '?'}) — спул снят")
        try:                                          # подтверждение учителю — ТОЛЬКО теперь (после коммита)
            ack = lesson_router.ack_after_commit(commit, data.get("ack_subject"),
                                                 data.get("ack_where"))
            if ack:
                _deliver_lesson_ack(data.get("card_msg_id"), ack)
        except Exception as e:                        # noqa: BLE001 — ack не роняет тик
            log.warning("LESSON retry: подтверждение учителю не собрано: %s", e)
        return f"повторный коммит {commit or '?'}"
    call(["checkout", "HEAD", "--"] + paths)          # снова к HEAD: дерево чистое, авто-фетч жив
    data["attempts"] = int(data.get("attempts") or 0) + 1
    _lesson_retry_save(data, path)
    detail = _tail(rc[2], 160) if rc else "git недоступен"
    log.warning("LESSON id=%s: повторный коммит снова не удался (попытка %s: %s) — следующий цикл",
                tid, data["attempts"], detail)
    return f"повтор не удался (попытка {data['attempts']})"


def maybe_lesson_commit_retry(now=None):
    """Троттлинг повтора коммита урока: не чаще LESSON_RETRY_SEC и только при живом спуле.
    → строка-итог | None (рано/спула нет)."""
    global _lesson_retry_last
    if not os.path.exists(LESSON_RETRY_FILE):
        return None
    now = time.time() if now is None else now
    if now - _lesson_retry_last < LESSON_RETRY_SEC:
        return None
    _lesson_retry_last = now
    return lesson_commit_retry_tick()


def _deliver_owner_card(text, send=None):
    """СИНХРОННАЯ доставка owner-карточки НЕЯСНОГО урока тем же рабочим каналом, что несёт
    критические/needs_approval карточки в 1160 (dispatch_notify.send_critical: инбокс 1160 первым,
    личка Филиппа — фолбэк). В отличие от fire-and-forget _notify_critical (Popen → None: доставку
    НЕ подтверждает, отсюда ложный рапорт «1160 недоступен» при реально дошедшей карточке), ЖДЁТ
    ответ Bot API и возвращает (канал, ok) → рапорт урока честный «доставлено через X».
    send инъектируется в тестах. FAIL-SAFE: любой сбой → ('', False) — урок не теряем (карточка/
    замечание остаются в результате задачи и логе; Bridge/деньги здесь не касаемся)."""
    try:
        if send is None:
            import dispatch_notify           # ленивый: тянем только на реальном неясном уроке
            send = dispatch_notify.send_critical
        channel, ok = send(text)
    except Exception as e:                    # noqa: BLE001 — доставка не должна ронять тик демона
        log.warning("owner-карточка неясного урока: доставка не удалась: %s", e)
        return ("", False)
    if not ok:
        return ("", False)
    human = {"inbox": f"инбокс {INBOX_TOPIC_ID}", "DM": "личку (фолбэк)"}.get(channel, channel)
    return (human, True)


def _deliver_lesson_ack(card_msg_id, text):
    """Доставка подтверждения «урок принят…» учителю. Реплай на карточку черновика в модер-группе
    делает moderation_bot (шаг 5 — здесь его НЕ трогаем): дирижёр лишь ФИКСИРУЕТ готовое, уже
    гейт-проверенное (после коммита) подтверждение с координатой карточки в журнал/лог. Fire-and-forget."""
    log.info("LESSON ack (card msg=%s): %s", card_msg_id or "?", text)
    _cowork(f"урок: подтверждение учителю (реплай на карточку msg={card_msg_id or '?'}) — {_clip(text)}")


def _finalize_lesson_dec(tid, text, dec):
    """Довести УЖЕ ПОЛУЧЕННЫЙ dec урока — общее ядро первичного _handle_lesson и resume_lesson_wait
    (возобновление по ответу учителя). Ветку waiting здесь НЕ трогаем (её ловит вызывающий): delegate →
    локальный планировщик; иначе complete_task + (для done, шаг 4) коммит tracked-дифа урока и ТОЛЬКО
    после реального коммита подтверждение «урок принят…» учителю (инвариант «подтверждение не уходит до
    коммита»). Нет коммита (сбой/СТИЛЬ-playbook gitignored/дедуп/UNCLEAR) → подтверждение НЕ шлём."""
    route = dec.get("route")
    if dec.get("delegate"):                    # ФАКТ/ЛОГИКА — правка+тест = работа думателя-планировщика
        log.info("LESSON id=%s route=%s → планировщик (правка+тест)", tid, route)
        _cowork(f"урок #{tid} [{route}] → локальному планировщику (правка+тест)")
        # подтверждение «урок принят…» уйдёт ПОСЛЕ коммита планировщика (диф+golden) — шаг 5
        _local_dec_plan(tid, dec.get("delegate_text") or text)
        return
    status = dec.get("status") or "done"
    result = (dec.get("result") or "урок обработан")[:RESULT_MAX]
    bc.complete_task(tid, status, result)
    log.info("LESSON id=%s route=%s → %s (%s)", tid, route, status, dec.get("reason"))
    _cowork(f"урок #{tid} [{route}] → {status} · {_clip(result)}")
    _notify_task(status, tid, result)
    if status == "done":
        # ack_where/card_msg_id прокидываем в _commit_lesson: при провале коммита они лягут в спул,
        # и запоздалое подтверждение учителю соберёт lesson_commit_retry_tick ПОСЛЕ повторного коммита
        commit = _commit_lesson(tid, route, dec.get("ack_subject"), dec.get("commit_paths"),
                                ack_where=dec.get("ack_where"), card_msg_id=dec.get("card_msg_id"))
        ack = lesson_router.ack_after_commit(commit, dec.get("ack_subject"), dec.get("ack_where"))
        if ack:
            _deliver_lesson_ack(dec.get("card_msg_id"), ack)


# ---- Боевой sink ПЕРЕСПРОСА/ПОНИМАНИЯ урока РЕПЛАЕМ в МОДЕР-ГРУППУ (класс-фикс инцидента #102) ----
# Инцидент #102 (00:36 16.07, повтор 14.07 18:04): демон писал в журнал «жду да/поправку», но ПЕРЕСПРОС
# «Понял так: … — верно?» в модер-группу НЕ уходил. Корень (read-only разбор): _handle_lesson звал
# lesson_router.handle_lesson_task БЕЗ reply_moderation → текст переспроса генерился, но молча падал в
# _default_reply_moderation (заглушка → False). Ни send/reply, ни chat_id/reply_to — ТИХИЙ no-op, без
# ошибки API. Учитель ничего не видел; урок висел in_progress «в ожидании» ответа, которого не спросили.
# Фикс: боевой sink РЕАЛЬНО шлёт реплай на карточку черновика (card_msg_id) в модер-группу MODERBOT_TOKEN'ом,
# ПОДТВЕРЖДАЕТ факт доставки по message_id из ответа Bot API, РЕТРАИТ; так и не доставил → False (тогда
# lesson_router._enter_low_wait роняет урок в failed + карточка владельцу — НЕ тихое ожидание).
# Ack приёма урока («📝 Принял замечание… учту») шлёт moderation_bot на intake — это ДРУГОЕ сообщение,
# здесь его НЕ дублируем (переспрос и ack — разные сообщения). Двух getUpdates не создаём (только
# sendMessage) → с живым moderation_bot не конфликтует. FAIL-SAFE: любой сбой → честный (False, …, диагноз).
MODREPLY_RETRIES = max(1, int(os.getenv("LESSON_MODREPLY_RETRIES", "2") or "2"))


def _moderation_reply_send(reply_to_msg_id, text):
    """Низкоуровневая доставка реплая в МОДЕР-ГРУППУ (Bot API sendMessage, MODERBOT_TOKEN → MOD_GROUP_ID,
    reply_to_message_id=карточка черновика). → (ok: bool, message_id: int|None, diag: str). Токен/URL НЕ
    логируем. Нет токена/группы → честный (False, None, диагноз), а не тихий no-op. Карточку могли удалить
    → allow_sending_without_reply, чтобы переспрос всё равно дошёл в группу (не пропал)."""
    import suggest                                    # ленивый: MODERBOT_TOKEN/MOD_GROUP_ID уже из .env
    token = (getattr(suggest, "MODERBOT_TOKEN", "") or "").strip()
    chat = getattr(suggest, "MOD_GROUP_ID", None)
    if not token:
        return (False, None, "нет MODERBOT_TOKEN — реплай в модер-группу невозможен")
    if chat is None:
        return (False, None, "нет MOD_GROUP_ID — некуда слать реплай в модер-группу")
    payload = {"chat_id": chat, "text": text}
    try:
        payload["reply_to_message_id"] = int(reply_to_msg_id)
        payload["allow_sending_without_reply"] = True
    except (TypeError, ValueError):
        pass                                          # нет валидной координаты → шлём в группу без реплая (не молчим)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage",
                                 data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "description": f"HTTP {getattr(e, 'code', '?')}"}
    except Exception as e:                            # noqa: BLE001 — сеть не роняет тик демона
        return (False, None, type(e).__name__)
    if body.get("ok"):
        mid = (body.get("result") or {}).get("message_id")
        if mid:
            return (True, int(mid), "")
        return (False, None, "Bot API ok, но нет message_id — факт доставки не подтверждён")
    return (False, None, f"Bot API отказал: {str(body.get('description'))[:80]}")


def _reply_moderation_lesson(card_msg_id, text, send=None, retries=None):
    """Боевой reply_moderation-sink дирижёра (инъектируется в lesson_router): доставляет ПЕРЕСПРОС/ПОНИМАНИЕ
    урока РЕПЛАЕМ в модер-группу (на карточку черновика card_msg_id) с ПРОВЕРКОЙ факта доставки по message_id
    и РЕТРАЕМ. Доставлено → message_id (int, truthy). Не доставлено после MODREPLY_RETRIES попыток → False:
    вызывающий (_enter_low_wait) уводит урок в failed + карточку владельцу (не тихое ожидание, инцидент #102).
    send/retries инъектируемы (голден без сети)."""
    snd = send or _moderation_reply_send
    attempts = max(1, int(MODREPLY_RETRIES if retries is None else retries))
    diag = "?"
    for i in range(1, attempts + 1):
        try:
            ok, mid, diag = snd(card_msg_id, text)
        except Exception as e:                        # noqa: BLE001 — sink не роняет дирижёра
            ok, mid, diag = False, None, f"{type(e).__name__}: {e}"
        if ok and mid:
            log.info("LESSON reply в модер-группу ДОСТАВЛЕН (msg=%s, попытка %d/%d, card=%s)",
                     mid, i, attempts, card_msg_id or "?")
            return mid
        log.warning("LESSON reply в модер-группу не доставлен (попытка %d/%d, card=%s): %s",
                    i, attempts, card_msg_id or "?", diag or "?")
    log.error("LESSON reply в модер-группу НЕ доставлен после %d попыток (card=%s): %s — урок упадёт failed",
              attempts, card_msg_id or "?", diag or "?")
    return False


def _handle_lesson(tid, text):
    """Обработать задачу-урок (родитель 112, шаг 8/8 + 292/334): дирижёр классифицирует замечание
    менеджера ВТОРОЙ осью (route_lesson_urok) и маршрутизирует. BEHAVIOR → правило в книгу правил
    (playbook) + реплай учителю «✅ Принято…» (мгновенное применение к черновику); CODE → карточка
    владельцу «нужен код-фикс: <суть>» + готовый текст задачи (код НЕ правим и НЕ делегируем — осознанная
    задача через гейт); UNSURE → ФОЛБЭК на 1-ю ось handle_lesson_task: СТИЛЬ → книга правил; НАДЗОР →
    строка-класс в чек-лист ревизора; ФАКТ/ЛОГИКА → локальному планировщику (правка+ТЕСТ); неясное →
    карточка-уточнение владельцу в 1160 (не угадываем). Боевые sink'и: playbook / чек-лист / инбокс
    1160 (_deliver_owner_card — СИНХРОННАЯ доставка с подтверждением канала, рапорт честный «доставлено
    через X», не гадательный) + reply_moderation (_reply_moderation_lesson — реплай переспроса/понимания
    в модер-группу с проверкой message_id и ретраем). Bridge/таблицы/деньги не трогаем — только текст
    доков или делегирование.
    confidence=low (родитель 334, шаг 3): урок В РАБОТУ НЕ БЕРЁМ — спросили учителя «верно?» РЕПЛАЕМ и,
    ТОЛЬКО ЕСЛИ переспрос реально доставлен, ЖДЁМ. status='waiting' → задача НЕ закрывается: сохраняем
    pending_low на диск и оставляем in_progress под защитой process_lesson_waits (реапер одиночек её НЕ
    трогает; heartbeat тикаем сами; предел ожидания — 24ч → карточка владельцу в 1160, шаг 4). Переспрос
    НЕ доставлен (класс-фикс #102) → dec.status='failed' → _finalize_lesson_dec закрывает урок failed с
    диагнозом (карточку владельцу уже отправил _enter_low_wait). Класс-фиксы инцидентов 354 и #102."""
    # #112 шаг 8/8: живой канал «урок:» РЕШАЕТ по 2-й оси (behavior→playbook+«принято» /
    # code→карточка владельцу «нужен код-фикс», без авто-правки / unsure→фолбэк на 1-ю ось
    # handle_lesson_task с её LLM/supervision/low-conf переспросом). Контракт dec и ветка
    # waiting+pending_low — прежние (фолбэк-путь их и порождает), поэтому обработка ниже не меняется.
    dec = lesson_router.route_lesson_urok(text, notify_owner=_deliver_owner_card,
                                          reply_moderation=_reply_moderation_lesson)
    if dec.get("status") == "waiting" and dec.get("pending_low"):
        _enter_lesson_wait(tid, dec)
        return
    _finalize_lesson_dec(tid, text, dec)


# ---------------- ПК-side ОЖИДАНИЕ low-урока: подтверждение учителя (родитель 334, шаги 3–4) ----------
# КЛАСС-ФИКС инцидента 354. confidence=low урок остаётся in_progress, ЗАКОННО ожидая ответа учителя
# «верно?» в модер-группе. Демон держит его живым САМ, чтобы одиночный ПК-ливнесс (process_stuck_singles,
# 90 мин) не убил его ложно как орфана:
#   (1) состояние pending_low лежит НА ДИСКЕ (restart-proof, ключ = tid);
#   (2) каждый тик мы ТИКАЕМ task_heartbeat (updated свеж → реапер одиночек не срубает) И реапер
#       ДОПОЛНИТЕЛЬНО исключает ждущие tid'ы (замок инварианта, не зависит от тайминга поллинга);
#   (3) ЕДИНСТВЕННЫЙ предел ожидания — check_low_wait_timeout (24ч, шаг 4): молчит учитель >24ч →
#       карточка владельцу в 1160, ожидание снято. Урок НЕ теряем, но и НЕ висит вечно.
# resume_lesson_wait — шов возобновления по ответу учителя (голден зовёт напрямую; боевой роутинг
# ответа из модер-группы делает moderation_bot отдельным шагом — его здесь НЕ трогаем).
LESSON_WAIT_STATE = os.path.join(REPO, "pc_orchestrator.lesson_waits.json")


def _lesson_waits_read(path=None):
    """State ждущих low-уроков {str(tid): pending_low} → dict ({} при отсутствии/бое — fail-safe)."""
    try:
        with open(path or LESSON_WAIT_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _lesson_waits_write(d, path=None):
    try:
        with open(path or LESSON_WAIT_STATE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        log.warning("state ждущих low-уроков не записался (%s) — ожидание деградирует, не критично", e)


def _lesson_wait_ids(path=None):
    """Множество tid законно-ждущих low-уроков (реапер одиночек их исключает). Битые ключи игнорируем."""
    ids = set()
    for k in _lesson_waits_read(path):
        try:
            ids.add(int(k))
        except (TypeError, ValueError):
            continue
    return ids


def _enter_lesson_wait(tid, dec, path=None):
    """Урок ушёл в ожидание подтверждения учителя (confidence=low): СОХРАНЯЕМ pending_low на диск под tid
    и ОСТАВЛЯЕМ задачу in_progress (НЕ закрываем). Тикаем heartbeat сразу — updated свеж с первой секунды
    ожидания. Задача теперь под защитой process_lesson_waits: реапер её не тронет, предел ожидания — 24ч."""
    st = _lesson_waits_read(path)
    st[str(tid)] = dec.get("pending_low") or {}
    _lesson_waits_write(st, path)
    try:
        bc.task_heartbeat(tid)
    except Exception as e:                       # noqa: BLE001 — heartbeat-тик не должен ронять обработку урока
        log.warning("LESSON wait id=%s: первый heartbeat не прошёл (%s) — тикнём следующим циклом", tid, e)
    log.info("LESSON id=%s → ОЖИДАНИЕ подтверждения учителя (low, класс %s) — держим in_progress, реапер исключает",
             tid, dec.get("llm_class") or dec.get("route"))
    _cowork(f"урок #{tid} [low] → жду «да»/поправку учителя (in_progress под защитой, предел ожидания 24ч)")


def process_lesson_waits(now=None, path=None):
    """Тик ждущих low-уроков (класс-фикс 354). Для КАЖДОГО сохранённого ожидания:
      • урок уже не in_progress (учитель ответил и resume закрыл, либо иное) → снимаем ожидание;
      • иначе ТИКАЕМ task_heartbeat (updated свеж → реапер одиночек не срубает законное ожидание);
      • check_low_wait_timeout: молчит учитель >24ч → карточка владельцу в 1160 + задача done, ожидание
        снято (ЕДИНСТВЕННЫЙ предел ожидания, шаг 4 родителя 334). Идемпотентно (timed_out метится в
        pending, персистим). now/path инъектируемы. FAIL-SAFE: сбой sink/heartbeat не роняет тик."""
    if _stopped():
        return
    st = _lesson_waits_read(path)
    if not st:
        return
    # какие из ждущих tid ещё реально in_progress на Bridge — остальные ожидания снимаем (self-heal).
    # get_pending упал → live=None: НЕ снимаем вслепую (fail-safe), только тикаем/таймаутим.
    r = bc.get_pending("in_progress")
    live = ({int(it.get("id")) for it in r.get("items", []) if _lane_ok(it) and it.get("id") is not None}
            if r.get("ok") else None)
    changed = False
    for key in list(st.keys()):
        try:
            tid = int(key)
        except (TypeError, ValueError):
            del st[key]; changed = True; continue
        pending = st.get(key) or {}
        if live is not None and tid not in live:
            del st[key]; changed = True
            log.info("LESSON wait id=%s снято: задача больше не in_progress", tid)
            continue
        try:
            bc.task_heartbeat(tid)               # держим updated свежим → реапер одиночек не тронет
        except Exception as e:                   # noqa: BLE001
            log.warning("LESSON wait id=%s: heartbeat не прошёл (%s)", tid, e)
        try:
            dec = lesson_router.check_low_wait_timeout(pending, now=now, notify_owner=_deliver_owner_card)
        except Exception as e:                   # noqa: BLE001 — тик таймаута не должен ронять поллинг
            log.warning("LESSON wait id=%s: check_low_wait_timeout упал (%s)", tid, e)
            dec = None
        st[key] = pending; changed = True        # pending мутирован (timed_out) → персистим (идемпотентность restart-proof)
        if dec is None:
            continue                             # ещё в пределах 24ч — ждём ответа учителя
        result = (dec.get("result") or "урок отдан владельцу по 24ч-таймауту")[:RESULT_MAX]
        bc.complete_task(tid, dec.get("status") or "done", result)
        _notify_task(dec.get("status") or "done", tid, result)
        log.warning("LESSON wait id=%s: 24ч без ответа учителя → %s, ожидание снято · %s",
                    tid, dec.get("status") or "done", _clip(result))
        _cowork(f"урок #{tid} [low]: 24ч без ответа учителя → отдан владельцу в 1160, ожидание снято")
        if dec.get("clear_wait"):
            del st[key]
    if changed:
        _lesson_waits_write(st, path)


def resume_lesson_wait(tid, reply_text, is_approver, path=None, classify=None,
                       append_style=None, append_checklist=None, reply_moderation=None):
    """Возобновить ждущий low-урок по ОТВЕТУ учителя (шов для будущего moderation_bot-роутинга ответа
    из модер-группы; голден зовёт напрямую). Грузим pending_low и зовём lesson_router.resume_low_lesson:
      • «да» НЕ от аппрувера → остаёмся в ожидании (state не трогаем, задача так же под защитой);
      • «да» от аппрувера / поправка→high → урок В РАБОТУ (finalize: коммит+подтверждение либо планировщик);
      • снова неясно после поправки → отложено владельцу в 1160 (done).
    Ожидание снимаем во ВСЕХ терминальных ветках (не-waiting). → dec | None (нет такого ожидания).
    Sink'и инъектируемы (голден без Telegram/1160/git); боевые дефолты — playbook/чек-лист/1160/думатель."""
    st = _lesson_waits_read(path)
    pending = st.get(str(tid))
    if pending is None:
        log.info("resume_lesson_wait id=%s: нет активного ожидания — игнор", tid)
        return None
    dec = lesson_router.resume_low_lesson(
        pending, reply_text, is_approver, classify=classify, append_style=append_style,
        append_checklist=append_checklist, notify_owner=_deliver_owner_card, reply_moderation=reply_moderation)
    if dec.get("status") == "waiting":           # «да» не от уполномоченного — ждём дальше, ожидание НЕ снимаем
        log.info("resume_lesson_wait id=%s: %s — остаёмся в ожидании", tid, dec.get("resumed"))
        return dec
    st.pop(str(tid), None)                        # терминально → снимаем ожидание ДО финализации задачи
    _lesson_waits_write(st, path)
    _finalize_lesson_dec(tid, str(pending.get("text") or ""), dec)
    log.info("resume_lesson_wait id=%s: %s → ожидание снято", tid, dec.get("resumed"))
    return dec


def process_new():
    """Взять СТАРЕЙШУЮ new-задачу своей полосы, исполнить, записать результат/needs_approval."""
    if _stopped():
        return
    r = bc.get_pending("new")
    if not r.get("ok"):
        log.warning("get_pending(new) ошибка: %s", r.get("error"))
        return
    items = [it for it in r.get("items", []) if _lane_ok(it)]
    if not items:
        return
    # ПРИОРИТЕТ pc-полосы: задачи ВЛАДЕЛЬЦА (328/829/Dispatch) впереди РЕВИЗОРСКИХ родителей — среди
    # new-задач ревизорский родитель ([ревизор дата=… класс=…]) клеймится ПОСЛЕ owner-работы; внутри
    # каждой группы — прежний FIFO по id. Ревизорские шаги в new не соперничают: они не релизятся, пока
    # есть owner-работа (уступка МЕЖДУ шагами, _loc_after_done/вотчдог) — а уже релизнутый «текущий шаг
    # дорабатывает» штатно. Нет owner-задач → ревизорский родитель клеймится как прежде.
    task = sorted(items, key=lambda x: (_is_revizor_parent_text(x.get("task_text")),
                                        int(x.get("id") or 0)))[0]
    tid = task.get("id")
    text = str(task.get("task_text") or "")
    cl = bc.claim_task(tid)
    if not cl.get("ok"):
        log.info("claim id=%s не удался (%s) — пропуск", tid, cl.get("error"))
        return
    log.info("CLAIM id=%s in_progress", tid)
    # Отметка старта НА ДИСКЕ, сразу после claim: закрывать задачу может уже другой процесс демона
    # (самообновление/падение/реапер), и без этой метки окно работы не построить → итог провала
    # не сможет назвать коммиты и записи журнала, которые задача успела сделать.
    _task_started_mark(tid)
    _cowork(f"взял задачу #{tid} (in_progress)")
    if _is_revizor_owner_card(text):
        # осиротевшая owner-карточка ревизора (краш между claim и set_needs_approval): закрыть,
        # НЕ гнать headless фиктивный текст (пересоздастся следующим прогоном ревизора). Штатно
        # сюда не попадаем — ревизор claim'ит и штампует needs_approval синхронно в maybe_revizor.
        bc.complete_task(tid, "done", "🔍 ревизор: осиротевшая owner-карточка закрыта")
        _cowork(f"ревизор: осиротевшая owner-карточка #{tid} закрыта")
        return
    cmd = _match_command(text)                 # команда-рычаг? исполняем САМИ, без headless claude
    if cmd:
        status, result = _exec_command(cmd)
        bc.complete_task(tid, status, result)
        log.info("COMMAND id=%s cmd=%s → %s", tid, cmd, status)
        _cowork(f"задача #{tid} (рычаг {cmd}) → {status} · {_clip(result)}")
        _notify_task(status, tid, result)
        return
    if _is_smoke_step(text):        # обязательный финальный смоук цепи — исполняем САМИ, без headless
        status, result = _exec_smoke_step()
        bc.complete_task(tid, status, result)
        log.info("SMOKE id=%s → %s", tid, status)
        _cowork(f"смоук-шаг #{tid} → {status} · {_clip(result)}")
        _notify_task(status, tid, result)
        return
    # Задача-урок (родитель 292, шаг 3): классифицируем замечание и маршрутизируем (СТИЛЬ/ФАКТ/
    # НАДЗОР/неясное). ПЕРЕХВАТ ДО local-dec-планировщика: урок enqueue'ится from=Filipp-pcloc-dec,
    # но это НЕ ТЗ на декомпозицию — свой обработчик (иначе _is_local_dec_parent отдал бы его планировщику).
    if lesson_router.is_lesson_task(text):
        _handle_lesson(tid, text)
        return
    # Локальный дирижёр (PC_LOCAL_DEC=1): осиротевшая synthetic (сводка/карточка — демон упал
    # между enqueue и complete) → довести done, НЕ исполняя; родитель Filipp-pcloc-dec → строим
    # план, НЕ исполняем как обычную задачу. Флаг off → False сразу (поведение байт-в-байт
    # прежнее; from=Filipp-pcloc-dec до порта не существовал — чужого не задеваем).
    frm = str(task.get("from") or "")
    if frm == PC_LOCAL_DEC_FROM and _loc_finalize_orphan_synthetic(tid, text):
        return
    if _is_local_dec_parent(frm, text):
        _local_dec_plan(tid, text)
        return
    # HEAD до задачи: для дев-задач («тз:») — всегда; для ШАГОВ цепи — под GATE_STEP_SELECTIVE
    # (иначе head_before=None → maybe_update_bots ничего не применит, прежнее поведение). Диффом
    # head_before..HEAD увидим новые коммиты задачи → нужен ли рестарт userbot/moderbot.
    head_before = (_git_out(["rev-parse", "HEAD"])
                   if _is_dev_task(text) or (_gate_step_selective_on() and gate_selective.parse_step(text)[0])
                   else None)
    status, result = run_task(tid, text)
    if status == "needs_approval":
        bc.set_needs_approval(tid, result)
        log.info("NEEDS_APPROVAL id=%s", tid)
        _cowork(f"задача #{tid} → needs_approval (красное, жду «да»)")
        _notify_task("needs_approval", tid, result)
    else:
        # САМОПОЧИНКА (STEP_SELFHEAL=1): провал ОДИНОЧНОЙ задачи lane=pc → думатель, РОВНО 1 попытка.
        # True = финализировано внутри (перерождение / терминальный failed с диагнозом); False =
        # прежний путь (fail-safe: флаг off / сбой думателя / очередь не приняла). Байт-в-байт
        # прежнее поведение при STEP_SELFHEAL=0 (короткое замыкание в _maybe_selfheal).
        if status == "failed" and _maybe_selfheal(tid, text, result, frm=str(task.get("from") or "")):
            return
        if status == "done":   # задача успешна → применить свежий код к боту(ам), если рантайм менялся
            result = (result + maybe_update_bots(tid, text, head_before))[:RESULT_MAX]
        bc.complete_task(tid, status, result)
        log.info("COMPLETE id=%s status=%s", tid, status)
        # полный текст RESULT (done) / причины failed → штаб читает итог из cowork_log без скринов
        _cowork(f"задача #{tid} → {status} · {_clip(result)}")
        _notify_task(status, tid, result)


def process_approved():
    """Одобренные красные задачи (status=approved) → повторить шаг headless. Снова красное →
    failed (не зацикливаемся; демон красное сам не исполняет). Истёк TTL → failed."""
    r = bc.get_pending("approved")
    if not r.get("ok"):
        return
    for task in sorted([it for it in r.get("items", []) if _lane_ok(it)], key=lambda x: int(x.get("id") or 0)):
        tid = task.get("id")
        if _stopped():
            return
        if _is_revizor_owner_card(task.get("task_text")):
            # info-карточка ревизора: approve = «принято», без headless-прогона фиктивного текста
            bc.complete_task(tid, "done", "🔍 owner-находки ревизора приняты Филиппом")
            _cowork(f"ревизор: owner-карточка #{tid} принята (approve)")
            log.info("APPROVED id=%s ревизор-owner-карточка → принято", tid)
            continue
        if (_age_sec(task.get("updated")) or 0) > APPROVAL_TTL:
            log.info("APPROVED id=%s истёк (>%ss) → failed", tid, APPROVAL_TTL)
            # ⏱ первым символом: для шага локальной цепи просрочка approve = halt без думателя
            # (гейт _loc_after_fail; переформулировка не вернёт ушедшего Филиппа)
            msg = fail_result(FAIL_APPROVAL_TIMEOUT, "approve истёк (>30 мин) — повтори задачу",
                              since=_task_started_get(tid))
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} (approved) → failed · {_clip(msg)}")
            _notify_task("failed", tid, "approve истёк")
            continue
        # Класс, на который нажали «да» → поедет ребёнку (env-маркер гарду + абзац модели). Раньше
        # ре-ран шёл БЕЗ этого, и тот же шаг снова краснел: 7 «да» из 10 сгорели именно здесь.
        appr, appr_obj = _approved_scope(task)
        dropped = _approved_kinds(task) - appr          # высший вид без названного объекта
        status, result = run_task(tid, str(task.get("task_text") or ""),
                                  note="[ОДОБРЕНО ЧЕЛОВЕКОМ] предыдущий шаг подтверждён. ",
                                  approved=appr, approved_object=appr_obj)
        if status == "needs_approval":
            # ✋ первым символом (зеркало ручной карты VPS): headless красное ДОКАЗАННО не проходит
            # даже после «да» — для шага локальной цепи это терминальный halt без думателя
            # (гейт _loc_after_fail; ре-аппрув/переформулировка = петля, рвём после ровно 1 круга)
            # Диагноз честный: сказано, ЧТО было одобрено — «класс не назван» и «одобрен класс X, а
            # уперлись в другой» это РАЗНЫЕ причины, и раньше владелец их не различал.
            # Три РАЗНЫЕ причины, и владелец обязан их различать: чужой класс, класс не назван и
            # (с 31.07) высший вид без объекта в ответе — последняя лечится не повтором «да», а
            # ответом, который называет объект.
            if dropped:
                why = ("необратимая операция (" + ", ".join(sorted(dropped)) + ") подтверждается "
                       "ТОЛЬКО ответом с объектом — короткое «да» её не открывает; ответь "
                       "«да <объект из карточки>»")
            elif appr:
                why = "одобрен класс " + ", ".join(sorted(appr)) + ", но упёрлись в ДРУГОЕ красное"
            else:
                why = ("класс операции в карточке не назван (op=other) — одобрение "
                       "не привязать к операции")
            msg = fail_result(FAIL_MODEL_REFUSAL,
                              f"одобрено, но шаг снова упирается в красное — выполни вручную "
                              f"[{why}]: " + result[:400], since=_task_started_get(tid))
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} (approved) → failed · {_clip(msg)}")
            _notify_task("failed", tid, "снова красное после approve — вручную")
        else:
            bc.complete_task(tid, status, result)
            _cowork(f"задача #{tid} (approved) → {status} · {_clip(result)}")
            _notify_task(status, tid, result)
        log.info("APPROVED id=%s → %s", tid, status)


def process_approval_timeouts():
    """needs_approval-задачи, провисевшие дольше APPROVAL_TTL без решения → failed (не ждём вечно).
    (reject Филиппа Splinter ставит сам — такие сюда не попадают.)"""
    r = bc.get_pending("needs_approval")
    if not r.get("ok"):
        return
    for task in [it for it in r.get("items", []) if _lane_ok(it)]:
        tid = task.get("id")
        if _is_revizor_owner_card(task.get("task_text")):
            continue      # info-карточка ревизора живёт до решения человека — не гасим по таймауту
        if (_age_sec(task.get("updated")) or 0) > APPROVAL_TTL:
            log.info("NEEDS_APPROVAL id=%s таймаут (>%ss) → failed", tid, APPROVAL_TTL)
            msg = fail_result(FAIL_APPROVAL_TIMEOUT,
                              "подтверждение не получено за 30 мин — задача закрыта без «да»",
                              since=_task_started_get(tid))
            bc.complete_task(tid, "failed", msg)
            _cowork(f"задача #{tid} → failed · {_clip(msg)}")
            _notify_task("failed", tid, "подтверждение не получено за 30 мин")


# ---------------- ПРЯМОЙ КАНАЛ ПК↔Bridge для ОДИНОЧЕК pc (развязка 328-pc, этап 1) --------------
# Поставить ОДИНОЧНУЮ lane=pc задачу прямо в очередь Bridge СУЩЕСТВУЮЩИМ экшеном enqueue_task,
# МИНУЯ девбот-в-splinter (тема 328). Демон claim'ит её обычным поллингом (process_new). Это
# ДОПОЛНИТЕЛЬНЫЙ путь, НЕ замена темы 328/девбота (тот остаётся рабочим). Инвариант: даже когда
# Splinter/девбот лежат — и enqueue, и claim идут ПК→Bridge напрямую, поэтому одиночка pc всё равно
# принимается и исполняется. lane ЖЁСТКО == LANE ('pc') — чужие полосы не трогаем. Эмпирически
# проверено с ПК: enqueue_task → id, get_pending видит, claim берёт (RECON развязки 328-pc).

def enqueue_pc_task(text, frm="Filipp", bridge=None):
    """ПРЯМОЙ enqueue одиночки lane=pc в очередь Bridge (существующий экшен, без нового Bridge-кода).
    Детерминированно, lane жёстко 'pc'. → (ok: bool, id|None, err|None). Демон подхватит её обычным
    process_new — enqueue+claim идут напрямую ПК→Bridge, независимо от Splinter/девбота (инвариант)."""
    t = str(text or "").strip()
    if not t:
        return False, None, "пустой текст задачи"
    b = bridge if bridge is not None else bc
    r = b.enqueue_task(frm or "Filipp", t, lane=LANE)   # LANE == 'pc' строго (чужие полосы не создаём)
    if not r.get("ok"):
        return False, None, str(r.get("error") or "enqueue отклонён Bridge")
    nid = r.get("id")
    log.info("direct-enqueue: одиночка lane=%s поставлена в очередь id=%s (минуя splinter)", LANE, nid)
    return True, nid, None


# ---------------- ПК-side таймаут ОДИНОЧЕК pc (развязка 328-pc, этап 2) --------------------------

def process_stuck_singles(now=None):
    """ПК-side ливнесс одиночек lane=pc: задача, застрявшая в in_progress дольше PC_SINGLE_STALE
    (ПК был выключен / процесс умер посреди прогона / гонка self-update), → честный failed с видимой
    пометкой вместо тихого вечного зависания. НЕ дублирует и НЕ ослабляет vps-реапер: тот про
    VPS/цепочки, здесь ТОЛЬКО одиночки lane=pc, которых он не видит. Порог > TASK_TIMEOUT, поэтому
    живой синхронный прогон демона (≤45 мин) под нож не попадёт — реапится лишь орфан мёртвого
    процесса. lane СТРОГО (чужие полосы не трогаем: _lane_ok). updated=None → не реапим (fail-safe,
    идиома `or 0` как в process_approval_timeouts). ИСКЛЮЧЕНИЕ (класс-фикс 354): урок в состоянии
    low-ожидания (_lesson_wait_ids) НЕ орфан — он ЗАКОННО ждёт ответа учителя in_progress; его предел —
    24ч → карточка 1160 (process_lesson_waits), а НЕ 90-мин ливнесс одиночек. Настоящие зависания
    (не в ожидании) реапер добивает как прежде — регресс цел."""
    if _stopped():
        return
    r = bc.get_pending("in_progress")
    if not r.get("ok"):
        log.warning("get_pending(in_progress) ошибка: %s", r.get("error"))
        return
    now = now or datetime.datetime.now(datetime.timezone.utc)
    waiting = _lesson_wait_ids()          # законно-ждущие low-уроки (родитель 334): НЕ орфаны — не реапим
    for task in [it for it in r.get("items", []) if _lane_ok(it)]:
        tid = task.get("id")
        if tid in waiting:
            continue                      # урок законно ждёт ответа учителя (in_progress); предел — 24ч, не реапер
        age = _age_sec(task.get("updated"), now=now) or 0
        if age <= PC_SINGLE_STALE:
            continue
        # since: отметка claim, а если её нет (задача клеймлена до появления реестра) — по возрасту
        started = _task_started_get(tid) or (now - datetime.timedelta(seconds=age))
        msg = fail_result(FAIL_HEARTBEAT_TIMEOUT,
                          f"ПК-таймаут одиночки: задача провисела in_progress {int(age)}с "
                          f"(> {PC_SINGLE_STALE}с) без движения — ПК был выключен, либо прогон "
                          "застрял/оборвался посреди исполнения", since=started, now=now)
        bc.complete_task(tid, "failed", msg)
        log.warning("stuck-single: id=%s in_progress %sс > %sс → failed (ПК-ливнесс одиночки)",
                    tid, int(age), PC_SINGLE_STALE)
        _cowork(f"задача #{tid} (одиночка) → failed по ПК-таймауту ({int(age)}с) · {_clip(msg)}")
        _notify_task("failed", tid, "ПК-таймаут одиночки (застряла in_progress)")


def poll_once():
    """Один цикл: держать ждущие low-уроки живыми → добить орфанов-одиночек → довести одобренное →
    просроченные ожидания → надзор локальных цепей (PC_LOCAL_DEC) → вотчдог застрявших между
    шагами цепей → новое → heartbeat."""
    process_lesson_waits()        # класс-фикс 354: heartbeat ждущим low-урокам + 24ч-таймаут → 1160 (ДО реапера)
    process_stuck_singles()       # этап 2: ПК-side ливнесс одиночек pc, застрявших в in_progress
    process_approved()
    process_approval_timeouts()
    process_local_chains()        # локальный дирижёр: done-шаг → релиз следующего, финал → сводка
    process_stuck_chains()        # вотчдог: цепь висит между шагами (потерянный релиз) → reconcile-досдвиг
    process_new()
    _write_heartbeat()


# ------------------------------- self-update ---------------------------------
# Порт VPS-паттерна: задача изменила pc_orchestrator.py (блоб HEAD != запущенной версии) →
# гейт (code_gate + unittest своих тестов) → управляемый рестарт: spawn нового процесса →
# лог/cowork «self-update: <старый коммит>→<новый>» → старый выходит. Watchdog — страховка:
# если новый упадёт на ходу, heartbeat протухнет и schtasks /Run поднимет демон.

RUNNING_BLOB = None       # git-блоб pc_orchestrator.py на момент старта («запущенная версия»)
RUNNING_COMMIT = "?"      # короткий коммит на момент старта (для строки self-update в логе)
_SU_REJECTED_BLOB = None  # блоб, уже проваливший гейт — не гоняем гейт каждый цикл, ждём нового коммита


def _git_out(args):
    """git в REPO → stdout.strip() | None (тихо: git недоступен/ошибка — self-update просто молчит)."""
    try:
        p = subprocess.run(["git"] + args, cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15, creationflags=NO_WINDOW)
        return p.stdout.strip() if p.returncode == 0 else None
    except Exception:
        return None


def _git_call(args, timeout=90):
    """git в REPO → (returncode, stdout, stderr) | None (git недоступен/исключение).
    В отличие от _git_out даёт returncode: для fetch/status/ff-only пустой stdout ≠ ошибка
    (fetch пишет в stderr, чистое дерево = пустой status, merge-base --is-ancestor кодирует
    ответ ТОЛЬКО кодом возврата). timeout щедрый — fetch ходит в сеть."""
    try:
        p = subprocess.run(["git"] + args, cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception:
        return None


def _blob_hash():
    """Хеш содержимого pc_orchestrator.py в HEAD — «версия» кода демона (дифф против запущенной)."""
    return _git_out(["rev-parse", "HEAD:pc_orchestrator.py"])


def _head_commit():
    return _git_out(["rev-parse", "--short", "HEAD"]) or "?"


def _init_running_version():
    global RUNNING_BLOB, RUNNING_COMMIT
    RUNNING_BLOB = _blob_hash()
    RUNNING_COMMIT = _head_commit()


def _gate_unittests():
    """Гейт self-update ступень 2: unittest собственных тестов демона (новым кодом). → (ok, msg).
    env-очистка THINKER_MODEL/FALLBACK: гейт обязан проверять новый код против ТЕКУЩЕГО .env
    (каким его перечитает перезапущенный демон), а НЕ против значения, унаследованного в память
    ЭТОГО демона со старта. Иначе прежний короткий алиас модели в env демона ронял бы golden-тест
    и блокировал self-update, а перечитать .env демон может только рестартом → дедлок (хвост #194)."""
    try:
        env = dict(os.environ)
        for k in ("THINKER_MODEL", "THINKER_FALLBACK"):
            env.pop(k, None)                     # подпроцесс перечитает их из .env (load_dotenv)
        p = subprocess.run([VENV_PY, "-m", "unittest", "test_pc_orchestrator", "test_pc_local_dec"], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, env=env, creationflags=NO_WINDOW)
        return p.returncode == 0, _tail((p.stderr or "") + (p.stdout or ""), 400)
    except Exception as e:
        return False, f"unittest-гейт не запустился: {e}"


def _spawn_daemon():
    """Поднять НОВЫЙ detached-процесс демона (тот же venv+скрипт). → True/False. Мокается в тестах.
    Передаём PC_ORCH_SUPERSEDE_PID=<свой PID>: новый в acquire_singleton дождётся смерти старого
    (нас) прежде чем забрать лок — эстафета без перекрытия (разбор #128, часть 4)."""
    try:
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | NO_WINDOW)
        env = dict(os.environ)
        env[SUPERSEDE_ENV] = str(os.getpid())
        subprocess.Popen([VENV_PY, os.path.join(REPO, "pc_orchestrator.py")], cwd=REPO,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags, env=env)
        return True
    except Exception as e:
        log.error("self-update: не смог запустить новый процесс: %s", e)
        return False


def maybe_self_update(blob_fn=None, code_gate=None, tests_gate=None, spawner=None, head_fn=None,
                      children_fn=None, dirty_fn=None):
    """→ True = гейт пройден, новый процесс запущен, ТЕКУЩИЙ должен выйти (эстафета передана).
    False = обновляться нечему/нельзя (нет диффа, рубильник, ГРЯЗНОЕ ДЕРЕВО, гейт провален, spawn
    не удался) — продолжаем на старом коде. Провал гейта запоминается по блобу (без перегона
    каждый цикл); грязное дерево по блобу НЕ запоминаем — вычистили, и обновление пойдёт само."""
    global _SU_REJECTED_BLOB
    if _stopped():
        return False
    new_blob = (blob_fn or _blob_hash)()
    if not new_blob or RUNNING_BLOB is None or new_blob == RUNNING_BLOB:
        return False                        # диффа нет (или git недоступен — не рискуем)
    if new_blob == _SU_REJECTED_BLOB:
        return False                        # этот код уже провалил гейт — ждём следующего коммита
    new_commit = (head_fn or _head_commit)()
    # ВОРОТА (28.07), пункт 4: тот же запрет — на само обновление демона. Новый процесс поднимется
    # из файлов НА ДИСКЕ, поэтому грязный pc_orchestrator.py (или его верхний импорт) увёз бы в бой
    # незакоммиченный код ровно так же, как это случилось с ботами.
    dirty = _dirty_block("orchestrator", new_commit, "self-update демона",
                         label="демон (self-update)", dirty_fn=dirty_fn)
    if dirty:
        return False              # блоб НЕ помечаем отвергнутым: чистое дерево обязано разблокировать
    log.info("self-update: pc_orchestrator.py изменился (%s→%s) — гоняю гейт", RUNNING_COMMIT, new_commit)
    ok, msg = (code_gate or self_update_ok)()
    if not ok:
        _SU_REJECTED_BLOB = new_blob
        log.error("self-update: code_gate ПРОВАЛЕН (%s→%s): %s — остаюсь на старом коде",
                  RUNNING_COMMIT, new_commit, msg)
        _notify(f"⚠️ Оркестратор: self-update {RUNNING_COMMIT}→{new_commit} провалил гейт кода — работаю на старом")
        return False
    ok, msg = (tests_gate or _gate_unittests)()
    if not ok:
        _SU_REJECTED_BLOB = new_blob
        log.error("self-update: unittest-гейт ПРОВАЛЕН (%s→%s): %s — остаюсь на старом коде",
                  RUNNING_COMMIT, new_commit, msg)
        _notify(f"⚠️ Оркестратор: self-update {RUNNING_COMMIT}→{new_commit} провалил unittest-гейт — работаю на старом")
        return False
    # Дети self-update: применить свежий код к userbot/moderbot по диффу RUNNING_COMMIT..new_commit
    # ДО передачи эстафеты (pc_agent — только пометка). Делаем это в СТАРОМ процессе (у него есть
    # ссылка на прежний коммит и общий анти-флап-реестр с maybe_update_bots — не дёрнем повторно то,
    # что дев-задача уже рестартнула). Best-effort: сбой рестарта детей НЕ блокирует хендовер демона
    # (дети — отдельные процессы; если что — их подхватит контур-вотчдог нового демона).
    try:
        (children_fn or _selfupdate_restart_children)(RUNNING_COMMIT, new_commit)
    except Exception as e:
        log.warning("self-update: реконсиляция детей упала (не блокирует хендовер): %s", e)
    if not (spawner or _spawn_daemon)():
        log.error("self-update: spawn нового демона НЕ УДАЛСЯ — продолжаю на старом коде "
                  "(упаду — watchdog поднимет через schtasks)")
        return False
    log.info("self-update: %s→%s — гейт пройден, новый процесс запущен, передаю управление", RUNNING_COMMIT, new_commit)
    _cowork(f"self-update: {RUNNING_COMMIT}→{new_commit} (гейт пройден, управляемый рестарт демона)")
    return True


# ------------------- САМОПОЧИНКА ОДИНОЧНЫХ задач (флаг STEP_SELFHEAL) -----------
# Порт VPS-образцов: самопочинка одиночных (9b0a496) + «думатель» (784e453) + класс «плановый
# рестарт ≠ падение» (48d9c64). Под флагом STEP_SELFHEAL: провал ОДИНОЧНОЙ задачи lane=pc,
# которая НЕ красный NEEDS_APPROVAL (отсечён раньше в run_task→process_new) и НЕ плановый
# self-update-рестарт (см. _killed_by_planned_restart ниже — стал done внутри run_task), →
# локальный ДУМАТЕЛЬ (тот же кондуктор-механизм --fallback-model, чистый генератор --max-turns 1),
# но со СВОЕЙ парой моделей THINKER_MODEL/THINKER_FALLBACK — НЕ завязан на SUGGEST_MODEL клиентского
# suggest (у думателя своя задача: строгий диагноз, не клиентский черновик) → строгий JSON
# {verdict:retry|halt, fixed_task, reason} →
#   retry: РОВНО одно перерождение «[самопочинка задачи N, попытка 1] …» в очередь lane=pc + карточка;
#   повторный провал уже МАРКИРОВАННОЙ задачи = терминальный failed (маркер = стоп, петля невозможна);
#   halt: сразу терминальный failed с причиной.
# FAIL-SAFE: ЛЮБОЙ сбой думателя (не-JSON / таймаут / пусто / исключение / очередь не приняла) =
# прежний ГОЛЫЙ failed (не хуже базового поведения). STEP_SELFHEAL=0/нет → ветка не зовётся вовсе
# (поведение байт-в-байт прежнее). Красное НЕ ослаблено: думатель ничего не исполняет
# (--allowed-tools '' + нейтральный cwd → без settings.json/pretool_guard), тема 829/инбокс 1160 нетронуты.
STEP_SELFHEAL_TIMEOUT = int(os.getenv("PC_SELFHEAL_TIMEOUT", "180") or "180")   # думатель — короткий ответ
# ФИКС-НАВСЕГДА класса «короткий алиас модели → claude -p HTTP 404 → exit=1» (родитель #194,
# провал 205–208): живой демон мог УНАСЛЕДОВАТЬ короткий THINKER_MODEL / THINKER_FALLBACK=opus-4.8
# в os.environ (ancestor стартовал со старым .env; load_dotenv по умолчанию override=False →
# унаследованное значение НЕ перезаписывается полным id из .env). claude -p с коротким алиасом →
# «model may not exist» 404 → exit=1 → планировщик/думатель молча падает (fail-safe). Нормализуем
# короткий алиас → ПОЛНЫЙ id ЗДЕСЬ, на старте: иммунно к застрявшему env, переживает наследование
# через _spawn_daemon и рестарты/перезагрузки ПК.
# 30.07.2026, решение владельца «убрать снятую голову из работы»: ВСЕ её имена — и короткие, и
# полное — ведут на claude-opus-5. Слева они живут ТОЛЬКО как ключи снятия: застрявшее в чьём-то
# окружении старое значение развернётся в Opus 5, а не вернёт снятую модель чёрным ходом мимо
# .env. Убрать ключи нельзя — тогда старое значение уйдёт в claude -p как есть и даст 404 (#194).
_MODEL_ALIAS_FULL = {"fable-5": "claude-opus-5", "fable5": "claude-opus-5",
                     "fable": "claude-opus-5", "claude-fable-5": "claude-opus-5",
                     "opus-4.8": "claude-opus-4-8", "opus-4-8": "claude-opus-4-8"}
def _norm_model_id(m):
    """Короткий алиас модели → ПОЛНЫЙ id (claude -p на коротком отвечает 404, #194); имена снятой
    головы → claude-opus-5. Неизвестное значение возвращаем как есть — не ломаем валидные полные
    id, «sonnet» и будущие модели."""
    m = (m or "").strip()
    return _MODEL_ALIAS_FULL.get(m, m)
THINKER_MODEL = _norm_model_id(os.getenv("THINKER_MODEL", "claude-opus-5")) or "claude-opus-5"  # своя голова думателя (НЕ SUGGEST_MODEL); нормализована в ПОЛНЫЙ id
THINKER_FALLBACK = _norm_model_id(os.getenv("THINKER_FALLBACK", "claude-opus-4-8"))               # свой фолбэк думателя; нормализован в ПОЛНЫЙ id
# Маркер перерождения одиночной задачи стоит ПЕРВЫМ в тексте → якорь ^ (страховка от ложного
# срабатывания на ТЗ, где маркер лишь упомянут в теле). N = id исходной задачи.
_HEAL_TASK_RE = re.compile(r"^\s*\[самопочинка задачи (\d+), попытка (\d+)\]")
# Конверт одобренной заявки (маркер ОБЯЗАН стоять ПЕРВЫМ — якорь ^): самопочинка его НЕ трогает,
# перерождение сдвинуло бы маркер и сломало бы разрыв петли ре-конвертов (спека, часть 2/2 §1).
_CONVERT_RE = re.compile(r"^\s*\[конверт одобренной заявки\b")
# Признак самомодификации демона для класса «плановый рестарт ≠ падение».
_SELFMOD_RE = re.compile(r"pc[-_ ]?orchestrator|самомодифика", re.IGNORECASE)

# Преамбула думателя ШАГА локальной цепи (порт THINKER_PREAMBLE VPS, спека часть 2/2 §1):
# та же схема/строгость, но ключ fixed_step и контекст с родителем/планом (даёт _loc_selfheal_consult).
THINKER_PREAMBLE = (
    "Ты — думательный слой самопочинки ПК-оркестратора TurboBaby (мета-дирижёр). Шаг декомпозиции "
    "упал при исполнении. Твоя задача — ТОЛЬКО диагноз и вердикт; ты НИЧЕГО не исполняешь, "
    "инструментов у тебя нет, файлы не читаешь — решай строго по данным ниже.\n"
    "Ответь СТРОГО ОДНИМ JSON-объектом, без текста до/после, без markdown-обёртки:\n"
    '{"verdict":"retry"|"halt","fixed_step":"<новая формулировка шага>","reason":"<1 строка диагноза>"}\n'
    "verdict=retry — ТОЛЬКО если провал починим переформулировкой шага (неверный путь/имя файла, "
    "недостающий контекст, кривая команда) и правка очевидна; fixed_step тогда — САМОДОСТАТОЧНОЕ "
    "дев-ТЗ ≤400 символов (исполнитель увидит ТОЛЬКО его, впиши нужный контекст). Во всех прочих "
    "случаях (причина неясна, нужен человек, красная зона, объём не влезает в таймаут) — "
    "verdict=halt и fixed_step пустой. Система даёт РОВНО ОДНУ попытку починки — не предлагай "
    "многошаговых планов.\n\n"
)
TASK_THINKER_PREAMBLE = (
    "Ты — думательный слой самопочинки ПК-оркестратора TurboBaby (мета-дирижёр). Одиночная "
    "headless-задача упала при исполнении. Твоя задача — ТОЛЬКО диагноз и вердикт; ты НИЧЕГО "
    "не исполняешь, инструментов у тебя нет, файлы не читаешь — решай строго по данным ниже.\n"
    "Ответь СТРОГО ОДНИМ JSON-объектом, без текста до/после, без markdown-обёртки:\n"
    '{"verdict":"retry"|"halt","fixed_task":"<новая формулировка задачи>","reason":"<1 строка диагноза>"}\n'
    "verdict=retry — ТОЛЬКО если провал починим переформулировкой задачи (неверный путь/имя файла, "
    "недостающий контекст, кривая команда) и правка очевидна; fixed_task тогда — САМОДОСТАТОЧНОЕ "
    "дев-ТЗ ≤400 символов (исполнитель увидит ТОЛЬКО его, впиши нужный контекст). Во всех прочих "
    "случаях (причина неясна, нужен человек, красная зона, объём не влезает в таймаут) — "
    "verdict=halt и fixed_task пустой. Система даёт РОВНО ОДНУ попытку починки — не предлагай "
    "многошаговых планов.\n\n"
)


def _flag_off_file(name):
    """Путь аварийного стоп-файла рубильника: pc_orchestrator.step_selfheal.off и т.п."""
    return os.path.join(REPO, f"pc_orchestrator.{name.lower()}.off")


def _flag_forced_off(name):
    """Стоп-файл рубильника взведён? Он БЬЁТ значение окружения и нужен потому, что выключение
    правкой .env имеет ТИХИЙ ОТКАЗ: новый процесс демона рождается из _spawn_daemon с env=dict(
    os.environ) РОДИТЕЛЯ, а load_dotenv по умолчанию override=False — унаследованная «1» НЕ
    перезаписывается нулём из файла. Тот же класс, ради которого живёт _norm_model_id (#194):
    включение так проходит (ключа в env не было), а ВЫКЛЮЧЕНИЕ по эстафете self-update — нет.
    Стоп-файл этим не болеет: проверяется НА КАЖДОМ вызове (действует со следующего тика, без
    рестарта демона) и живёт на диске, а не в наследуемом окружении.
    Направление отказа — КОНСЕРВАТИВНОЕ: не смогли ответить → считаем ВЫКЛЮЧЕНО. У аварийного
    рубильника «не знаю» обязано значить «стоп», а не «работай»; факт видно в баннере старта."""
    try:
        return os.path.exists(_flag_off_file(name))
    except Exception:
        return True


def _flag_on(name):
    """Рубильник думателя: env == "1" И не взведён стоп-файл. Подавление — строкой в лог, иначе
    расхождение «в .env единица, а веток нет» пришлось бы искать вслепую."""
    on = (os.environ.get(name) or "").strip() == "1"
    if on and _flag_forced_off(name):
        log.warning("%s=1, но взведён стоп-файл %s — рубильник ВЫКЛЮЧЕН (снять: удалить файл)",
                    name, os.path.basename(_flag_off_file(name)))
        return False
    return on


def _selfheal_on():
    """Флаг STEP_SELFHEAL=1 в .env (демон load_dotenv'ит на старте) + аварийный стоп-файл поверх.
    0/нет/стоп-файл → прежнее поведение (ветка не зовётся вовсе)."""
    return _flag_on("STEP_SELFHEAL")


def _killed_by_planned_restart(rc, task_text, blob_fn=None):
    """Класс «самомодификация демона → ложный failed» (порт 48d9c64) на ПК. True → claude задачи
    завершился НЕ из-за поломки, а потому что демон в окне управляемого self-update-рестарта
    (задача правила pc_orchestrator.py). РОВНО 4 обязательных признака, нужны ВСЕ (любое сомнение
    → False → прежний честный failed, fail-safe):
      1) rc != 0 — claude оборвался ненормально (логический no-RESULT при rc==0 сюда НЕ попадает);
      2) self-update РЕАЛЬНО назрел: HEAD-блоб pc_orchestrator.py != запущенной версии RUNNING_BLOB
         (единственная причина планового рестарта — задача закоммитила правку самого демона);
      3) текст задачи — про сам демон (pc_orchestrator / самомодификация);
      4) взведён рубильник pc_orchestrator.stop — демон в окне намеренной остановки/рестарта.
    Признаки взяты из реальных механизмов ПК (self-update-дифф + стоп-файл) — лишнего не выдумываем
    (schtasks/heartbeat — забота watchdog, не признак гибели конкретной задачи)."""
    if rc == 0:
        return False
    new_blob = (blob_fn or _blob_hash)()
    if not new_blob or RUNNING_BLOB is None or new_blob == RUNNING_BLOB:
        return False
    if not _SELFMOD_RE.search(str(task_text or "")):
        return False
    return _stopped()


def _parse_thinker_json(text, fix_key="fixed_task"):
    """Строгий парс ответа думателя → {"verdict",<fix_key>,"reason"} или None (fail-safe).
    Терпим обёртку-мусор вокруг JSON (берём от первой { до последней }), но verdict обязан быть
    retry|halt — иначе None."""
    t = (text or "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    v = str(d.get("verdict") or "").strip().lower()
    if v not in ("retry", "halt"):
        return None
    return {"verdict": v,
            fix_key: str(d.get(fix_key) or "").strip(),
            "reason": str(d.get("reason") or "").strip()}


REPO_SETTINGS = os.path.join(REPO, ".claude", "settings.json")
DEFAULT_EFFORT = "xhigh"
DEFAULT_MTT = "31999"
_thinking_cache = None


def repo_thinking_settings(path=None):
    """(effortLevel, MAX_THINKING_TOKENS) из .claude/settings.json — ЕДИНСТВЕННОГО источника
    правды по глубине мышления. Нужны путям, которые сам settings.json НЕ читают: думатель
    крутится в нейтральном cwd (tempdir), чтобы не тянуть hooks/pretool_guard репо, — но вместе
    с ними теряет и effortLevel. Файл не прочитан/битый → доктринальные дефолты репо."""
    global _thinking_cache
    if path is None and _thinking_cache is not None:
        return _thinking_cache
    eff, mtt = DEFAULT_EFFORT, DEFAULT_MTT
    try:
        with open(path or REPO_SETTINGS, "r", encoding="utf-8") as f:
            j = json.load(f)
        eff = str(j.get("effortLevel") or eff).strip() or DEFAULT_EFFORT
        mtt = str((j.get("env") or {}).get("MAX_THINKING_TOKENS") or mtt).strip() or DEFAULT_MTT
    except Exception:
        pass
    if path is None:
        _thinking_cache = (eff, mtt)
    return eff, mtt


def _thinker_exec(prompt, timeout, tag):
    """Думатель = ПЕРЕИСПОЛЬЗОВАННЫЙ кондуктор THINKER_MODEL→фолбэк (--fallback-model одним вызовом CLI),
    но ЧИСТЫЙ генератор: --max-turns 1 (один ответ, без инструментального цикла) + --allowed-tools ''
    + нейтральный cwd (tempdir, НЕ репо) → CLI не читает .claude/settings.json+pretool_guard и НИЧЕГО
    не исполняет (думатель красное не трогает). ANTHROPIC_API_KEY вычищен → подписка Max, не платный
    API. Возврат: текст ответа (распакован из --output-format json) или None при ЛЮБОМ сбое
    (запуск/таймаут/exit!=0/claude не найден) — fail-safe (upstream → прежний голый failed).
    Инъектируется в тестах (реальный claude не дёргаем)."""
    cbin = resolve_claude()
    if not cbin:
        log.warning("%s: claude не найден — думатель недоступен (fail-safe)", tag)
        return None
    # Бюджет процессов claude и для думателя (1520c68), но БЕЗ ожидания (wait_sec=0):
    # думатель живёт внутри тика демона — блокировать тик минуту нельзя, лимит занят → fail-safe
    # None (upstream отдаст прежний голый failed, это штатно).
    ok_budget, budget_detail = _claude_budget_gate(wait_sec=0)
    if not ok_budget:
        log.warning("%s: бюджет claude исчерпан (%s) — думатель пропущен (fail-safe)", tag, budget_detail)
        return None
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)          # идём по ~/.claude (подписка), не по платному ключу
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONIOENCODING"] = "utf-8"
    # Глубина размышления ЯВНО. Нейтральный cwd (tempdir) — сознательное решение выше: он
    # отсекает hooks/pretool_guard репо. Но вместе с ними отсекается и `effortLevel` из
    # .claude/settings.json, и думатель молча уходил на дефолт CLI — при доктрине «каждая
    # задача ultrathink» это тихая деградация ровно там, где рассуждение и нужно. Значения
    # берём из ТОГО ЖЕ settings.json (единственный источник правды), а не дублируем константой.
    eff, mtt = repo_thinking_settings()
    env["MAX_THINKING_TOKENS"] = mtt
    cmd = [cbin, "-p", prompt, "--model", THINKER_MODEL, "--effort", eff,
           "--output-format", "json", "--max-turns", "1", "--allowed-tools", ""]
    if THINKER_FALLBACK:                         # кондуктор: фолбэк исполняет сам CLI в этом же вызове
        cmd += ["--fallback-model", THINKER_FALLBACK]
    try:
        p = subprocess.run(cmd, cwd=tempfile.gettempdir(), capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env,
                           creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("%s: думатель не отработал (%s) — fail-safe", tag, e)
        return None
    if p.returncode != 0:
        log.warning("%s: думатель exit=%s — fail-safe", tag, p.returncode)
        return None
    raw = (p.stdout or "").strip()
    out = raw
    try:
        env_j = json.loads(raw)
        if isinstance(env_j, dict) and "result" in env_j:   # CLI-конверт --output-format json
            out = (env_j.get("result") or "").strip()
    except Exception:
        pass
    return out


def _task_selfheal_consult(task_text, fail_text):
    """Думатель самопочинки ОДИНОЧНОЙ задачи: контекст — текст задачи ДОСЛОВНО + суть провала
    (краткая). Возврат: dict {"verdict","fixed_task","reason"} или None (любой сбой думателя =
    None = fail-safe прежний голый failed)."""
    prompt = (TASK_THINKER_PREAMBLE +
              f"УПАВШАЯ ЗАДАЧА (текст дословно):\n{str(task_text or '')[:2000]}\n\n"
              f"СУТЬ ПРОВАЛА:\n{str(fail_text or '')[:1200]}\n")
    out = _thinker_exec(prompt, STEP_SELFHEAL_TIMEOUT, "task-selfheal")
    if out is None:
        return None
    verdict = _parse_thinker_json(out, fix_key="fixed_task")
    if verdict is None:
        log.warning("task-selfheal: ответ думателя не распарсился (fail-safe failed): %.200s", out)
    return verdict


def _maybe_task_selfheal(tid, text, fail_text, frm):
    """Провал ОДИНОЧНОЙ задачи lane=pc → тот же думательный слой, РОВНО 1 попытка. Возврат True =
    финализация сделана здесь (перерождение / терминальный failed с диагнозом); False = прежний
    голый failed в вызывающем коде (fail-safe). Красное НЕ ослаблено: сюда доходит только
    исполнительский failed — needs_approval отсечён раньше в run_task/process_new, а плановый рестарт
    самомод-задачи (фикс 48d9c64) уже стал done внутри run_task и думателя не видит."""
    hm = _HEAL_TASK_RE.match(str(text or ""))
    if hm:
        # перерождённая задача упала ПОВТОРНО → терминальный failed (без retry) — петля невозможна
        oid = hm.group(1)
        msg = (f"🛑 самопочинка не помогла (попытка 1 исчерпана): перерождение задачи {oid} упало "
               f"повторно — нужен человек.\n{str(fail_text or '')}")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.info("task-selfheal: id=%s (перерождение задачи %s) упал ПОВТОРНО → терминальный failed", tid, oid)
        _cowork(f"задача #{tid} (перерождение {oid}) → failed повторно · {_clip(msg)}")
        _notify_task("failed", tid, "самопочинка не помогла — нужен человек")
        return True
    verdict = _task_selfheal_consult(text, fail_text)
    if verdict is None:
        return False                              # fail-safe: сбой думателя = прежний голый failed
    reason = verdict["reason"] or "(без причины)"
    fixed = verdict["fixed_task"]
    if verdict["verdict"] != "retry" or not fixed:
        msg = (f"задача упала → думатель: halt, причина: {reason}\n"
               f"Перерождение не поможет (диагноз думателя выше), нужен человек.\n"
               f"{str(fail_text or '')}")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.info("task-selfheal: id=%s → думатель halt (%s)", tid, reason[:120])
        _cowork(f"задача #{tid} → failed (думатель: halt) · {_clip(reason)}")
        _notify_task("failed", tid, "думатель: halt — нужен человек")
        return True
    reborn = f"[самопочинка задачи {tid}, попытка 1] {fixed}"[:RESULT_MAX]
    r = bc.enqueue_task(frm or "Filipp", reborn)
    if not r.get("ok"):
        log.warning("task-selfheal: перерождение id=%s не встало в очередь (%s) — fail-safe failed",
                    tid, r.get("error"))
        return False                              # fail-safe: очередь не приняла → прежний голый failed
    nid = r.get("id")
    card = (f"🩹 задача упала → думатель: retry, правка: {fixed[:200]}, причина: {reason[:200]}\n"
            f"Перерождена задачей id {nid} (попытка 1 из 1; повторный провал = терминальный failed).\n"
            f"Исходный провал: {str(fail_text or '')[:400]}")[:RESULT_MAX]
    bc.complete_task(tid, "done", card)           # карточка решения → cowork_log/пуш (тема 829-красное нетронуто)
    log.info("task-selfheal: id=%s перерождён задачей %s (retry)", tid, nid)
    _cowork(f"задача #{tid} → самопочинка retry, перерождена #{nid} · {_clip(card)}")
    _notify_task("done", tid, f"самопочинка: перерождена задачей #{nid}")
    return True


def _maybe_selfheal(tid, text, fail_text, frm=""):
    """Провал одиночной задачи (исполнительский failed) → думательный слой, РОВНО 1 попытка.
    Возврат True = финализация сделана здесь; False = ничего не делал → прежний путь в вызывающем
    коде (fail-safe). STEP_SELFHEAL=0/нет → False сразу (поведение байт-в-байт прежнее)."""
    if _is_chain_artifact(frm, text):
        # артефакт ЧЬЕЙ-ТО цепи (шаг/сводка/карточка/коррекция) — НЕ одиночка: им владеет надзор
        # СВОЕЙ цепи (локальной — process_local_chains → _loc_after_fail со своим думателем шага;
        # ПК-театра — process_pc_chains на VPS). Одиночное перерождение сорвало бы маркеры и
        # порядок цепи. False = голый failed → надзор цепи разберётся сам.
        return False
    if not _selfheal_on():
        return False
    if str(fail_text or "").lstrip().startswith(NO_HEAL_PREFIXES):
        # ЕДИНЫЙ с гейтом цепи набор: отказ Филиппа / ⏱-диагнозы (таймаут задачи, ПК-ливнесс
        # застрявшей in_progress) / ✋-ручная карта (после «да» снова красное). Переформулировка
        # не вернёт ушедшего человека, не ускорит зависший claude и не откроет красное — честный failed
        return False
    if _CONVERT_RE.match(str(text or "")):
        # конверт одобренной заявки НЕ перерождаем: маркер конверта обязан стоять ПЕРВЫМ,
        # перерождение сдвинуло бы его → сломался бы разрыв петли ре-конвертов
        return False
    return _maybe_task_selfheal(tid, text, fail_text, frm)


# ------------------- ЛОКАЛЬНЫЙ ДИРИЖЁР-ДЕКОМПОЗЕР (флаг PC_LOCAL_DEC) -----------
# Порт мозга декомпозера VPS (docs/dec_port_spec.md в manager-bot, шаг 2/7 родителя 185):
# при PC_LOCAL_DEC=1 демон берёт РОДИТЕЛЯ from=Filipp-pcloc-dec (lane=pc, БЕЗ маркера
# «[шаг i/N родитель id]») и строит план ЛОКАЛЬНО — headless claude -p ТЕМ ЖЕ кондуктором
# THINKER_MODEL/THINKER_FALLBACK (_thinker_exec: --max-turns 1, --allowed-tools '',
# нейтральный cwd → планировщик НИЧЕГО не исполняет, красное не ослаблено). Парс плана —
# _PLAN_LINE_RE («N. шаг» / «N) шаг», прочие строки молча игнор), потолок MAX_STEPS=8.
# УРОК 166: красное в ТЗ — НЕ повод валить план (планировщик только планирует); NEEDS_APPROVAL
# в выводе учитывается ТОЛЬКО при пустом плане (fail-safe чисто-красного родителя → честный
# failed с картой, НЕ кнопка). 🔴-пометка красных шагов — только дисплей в result родителя
# (строка с 🔴 не матчит _PLAN_LINE_RE → restart-proof парс плана из result цел); текст шагов
# в очереди НЕ помечается. PC_LOCAL_DEC=0/нет → ветка не зовётся вовсе (поведение байт-в-байт
# прежнее: такой родитель ушёл бы обычным headless-путём run_task).
# РЕЛИЗ ШАГОВ (шаг 3/7 родителя 185) — SEQUENTIAL: в очереди живёт максимум ОДИН шаг цепи,
# следующий встаёт ТОЛЬКО после done предыдущего (веер дал бы гонку halt-on-fail и порядка
# перерождений — у process_new нет guard'а последовательности). Шаги/synthetic идут
# from=Filipp-pcloc-dec: VPS-надзор (process_pc_chains) группирует ТОЛЬКО from==Filipp-pc-dec —
# к локальным цепям он СЛЕП, дирижёр локальный целиком. Состояние цепи — ТОЛЬКО из очереди
# (restart-proof: план = нумерованный список в result родителя + карточки коррекций поверх);
# в памяти процесса лишь дедуп-кэши (_loc_summarized/_loc_adapt_finish). Сводки/карточки =
# synthetic-задачи lane=pc прямым каналом 86d8b03 (enqueue_pc_task → claim → complete done).
MAX_STEPS = 8                                     # потолок шагов плана (как на VPS)
PC_LOCAL_DEC_FROM = "Filipp-pcloc-dec"            # метка родителя локального дирижёра
# Метка цепей ПК-ТЕАТРА: дирижёр живёт на VPS (process_pc_chains), а шаги исполняет ЭТОТ демон —
# то есть их провал приходит в НАШ process_new. До 31.07 гейт самопочинки знал только метку выше,
# и такой шаг уходил бы в ОДИНОЧНОЕ перерождение: маркер [шаг i/N родитель pid] теряется (маркер
# самопочинки клеится ПЕРВЫМ), а исходный шаг закрывается done → VPS-надзор читает провал как
# успех и релизит следующий шаг. Цепь перешагивает через провал — риск Р1 разбора
# docs/artifacts/2026-07-31-selfheal-recon.md §3.
PC_DEC_FROM = "Filipp-pc-dec"                     # метка цепей ПК-театра (дирижёр на VPS)
CHAIN_FROMS = (PC_LOCAL_DEC_FROM, PC_DEC_FROM)    # ВСЕ дирижёрские метки полосы pc
PC_DEC_PLAN_TIMEOUT = int(os.getenv("PC_DEC_PLAN_TIMEOUT", "600") or "600")  # план думается дольше починки

# Маркеры цепи — БАЙТ-В-БАЙТ из порт-спеки (docs/dec_port_spec.md manager-bot, раздел 2):
_STEP_RE = re.compile(r"^\[шаг (\d+)/(\d+) родитель (\d+)\]")        # маркер шага цепи (match с начала)
_PLAN_LINE_RE = re.compile(r"^\s*(\d{1,2})[.)]\s+(\S.*)")            # строка плана «N. шаг» / «N) шаг»
_SUM_RE = re.compile(r"^\[сводка родитель (\d+)\]")                  # synthetic-сводка цепи
_HEAL_RE = re.compile(r"\[самопочинка шага (\d+), попытка (\d+)\]")  # search: маркер идёт ПОСЛЕ [шаг i/N]
_ADAPT_MARK_RE = re.compile(r"\[коррекция плана (\d+)\]")            # K-происхождение шага (restart-proof счётчик)
_ADAPT_CARD_RE = re.compile(r"^\[коррекция плана родитель (\d+)\]")  # карточка адаптации (остаток плана в result)
_CARD_RE = re.compile(r"^\[карточка родитель (\d+)\]")               # событийная карточка цепи (🩹/🛑/🧭/⚠️/🏁)
_ADAPT_BASE_RE = re.compile(r"после шага (\d+)")                     # база коррекции из task_text карточки
ADAPT_REPLACED_MARK = "♻️ заменён коррекцией плана"                  # done-карта шага, заменённого адаптацией
ADAPT_FINISH_MARK = "⏭ закрыт досрочно"                              # done-карта шага, закрытого finish'ем
_REJECT_PREFIX = "отклонено Филиппом"     # префикс result devbot-отказа (halt цепи без думателя)
# ЕДИНЫЙ на ОБА гейта набор «думателя не звать» (класс №9 свода: фикс сразу на обе полосы, здесь —
# на оба гейта одной полосы). Раньше гейт шага цепи (_loc_after_fail) знал три префикса, а гейт
# одиночки (_maybe_selfheal) — только ⏱: зеркальная дыра. Смысл каждого: «отклонено Филиппом» —
# человек сказал НЕТ; ⏱ — таймаут/орфан (переформулировка не ускорит зависший claude); ✋ — после
# «да» headless СНОВА упёрся в красное, доказано, что автоматом не берётся. Общий знаменатель:
# самопочинка НЕ переформулирует то, что упало на РЕШЕНИИ ЧЕЛОВЕКА или на КРАСНОМ.
NO_HEAL_PREFIXES = (_REJECT_PREFIX, TIMEOUT_MARK, MANUAL_MARK)


def _is_chain_artifact(frm, text):
    """Задача — артефакт ЧЬЕЙ-ТО цепи (шаг / сводка / карточка / коррекция плана), а не одиночка?
    ДВА замка, срабатывает любой:
      1) МЕТКА — from из CHAIN_FROMS (перечень всех дирижёрских меток полосы pc);
      2) ФОРМА — текст начинается с маркера цепи, КАКОВА БЫ НИ БЫЛА метка.
    Второй замок и есть ответ на «защищены все метки, а не одна»: перечень меток стухает (новый
    дирижёр — новая метка, и гейт по перечню молча откроется), а форма маркера — часть контракта
    цепи: именно по ней надзор группирует шаги, и именно её ломает одиночное перерождение.
    Ложных срабатываний не даёт: чтобы совпасть, одиночка обязана НАЧИНАТЬСЯ с «[шаг i/N родитель
    N]» — тогда она и есть шаг цепи по построению."""
    if str(frm or "") in CHAIN_FROMS:
        return True
    t = str(text or "")
    return bool(_STEP_RE.match(t) or _SUM_RE.match(t) or _CARD_RE.match(t) or _ADAPT_CARD_RE.match(t))


PLAN_ADAPT_MAX = 2                        # потолок коррекций на цепь; третий adjust = дрейф плана → halt
PLAN_ADAPT_TIMEOUT = int(os.getenv("PC_PLAN_ADAPT_TIMEOUT", "180") or "180")  # думатель адаптации — короткий ответ
# Преамбула думателя адаптации плана (порт ADAPT_PREAMBLE VPS, спека часть 2/2 §2).
ADAPT_PREAMBLE = (
    "Ты — думательный слой адаптации плана ПК-оркестратора TurboBaby (мета-дирижёр). Очередной шаг "
    "декомпозиции успешно завершён. Твоя задача — сверить результаты сделанного с целью родителя "
    "и решить, верен ли ЕЩЁ оставшийся план; ты НИЧЕГО не исполняешь, инструментов у тебя нет, "
    "файлы не читаешь — решай строго по данным ниже.\n"
    "Ответь СТРОГО ОДНИМ JSON-объектом, без текста до/после, без markdown-обёртки:\n"
    '{"verdict":"keep"|"adjust"|"finish","adjusted_steps":["<шаг>",...],"reason":"<1 строка>"}\n'
    "verdict=keep — оставшийся план верен, исполнять как есть (adjusted_steps пустой). Это "
    "ДЕФОЛТ: при малейшем сомнении — keep.\n"
    "verdict=adjust — ТОЛЬКО если результаты сделанных шагов сделали оставшиеся лишними/"
    "неверными и правка очевидна; adjusted_steps = НОВЫЙ полный список ОСТАВШИХСЯ шагов "
    "(сделанные не трогай), каждый — САМОДОСТАТОЧНОЕ дев-ТЗ ≤400 символов (исполнитель увидит "
    "ТОЛЬКО его текст, впиши нужный контекст).\n"
    "verdict=finish — цель родителя УЖЕ достигнута, оставшиеся шаги не нужны вовсе "
    "(adjusted_steps пустой).\n\n"
)


def _plan_adapt_on():
    """Флаг PLAN_ADAPT=1 в .env (отдельно от STEP_SELFHEAL, независимый откат) + свой аварийный
    стоп-файл. 0/нет/стоп-файл → прежнее поведение (после done-шага всегда keep: релиз следующего
    шага прежнего плана). Стоп-файл СВОЙ у каждого рубильника: гасить адаптацию, выключая
    самопочинку, — это тот самый «одобренный env не открывает Read секретов» наоборот."""
    return _flag_on("PLAN_ADAPT")


def _parse_adapt_json(text):
    """Строгий парс ответа думателя адаптации → {"verdict","adjusted_steps","reason"} или None
    (None = fail-safe keep у вызывающего). Терпим обёртку-мусор вокруг JSON; verdict обязан быть
    keep|adjust|finish; adjust без непустых adjusted_steps → None (пустой adjust = keep)."""
    t = (text or "").strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    v = str(d.get("verdict") or "").strip().lower()
    if v not in ("keep", "adjust", "finish"):
        return None
    raw_steps = d.get("adjusted_steps")
    steps = ([str(s).strip() for s in raw_steps if str(s).strip()]
             if isinstance(raw_steps, list) else [])
    if v == "adjust" and not steps:
        return None
    return {"verdict": v, "adjusted_steps": steps, "reason": str(d.get("reason") or "").strip()}
# Красность шага (дословно с VPS): красное действие исполнитель шага спросит кнопкой сам —
# пометка нужна владельцу заранее увидеть, где цепь встанет на «да».
_HEADLESS_IMPOSSIBLE_RE = re.compile(
    r"clasp|redeploy|\bsqlite3\b|set_fleet_(?:oil|service)|delete_event|confirmed\s*=\s*true|"
    r"лист\s*1|\bcrm\b|зарплат|байки|транзакц|проводк|деньг|касс|удал(?:и|ени|яе|ён)|календар",
    re.IGNORECASE)

# Порт PLANNER_PREAMBLE под репо D:\turbobaby-bot (PC_NOTE не нужен: исполнитель шагов — ЭТОТ ПК).
# Отличие от VPS-оригинала: планировщик здесь — чистый генератор без инструментов (_thinker_exec),
# поэтому строка про read-only разведку заменена честной «решай по тексту ТЗ».
PLANNER_PREAMBLE = (
    "Ты — планировщик декомпозиции ПК-театра TurboBaby (репо D:\\turbobaby-bot). Твоя задача — "
    "РАЗБИТЬ крупное ТЗ на шаги, НЕ выполняя его: ты чистый генератор без инструментов — файлы "
    "не читаешь и не правишь, не коммитишь, не деплоишь, в таблицы не пишешь; решай по тексту ТЗ.\n"
    "ФОРМАТ ОТВЕТА — СТРОГО и ТОЛЬКО нумерованный список шагов, каждый с новой строки "
    "«N. <шаг>», без заголовков, без кода, без текста до/после списка. Шагов 2–7. Каждый шаг — "
    "САМОДОСТАТОЧНОЕ дев-ТЗ (до 45 мин, ≤400 символов): исполнитель — headless-агент на ЭТОМ ПК "
    "в репо D:\\turbobaby-bot — увидит ТОЛЬКО текст шага, поэтому впиши в каждый нужный контекст "
    "(файлы, функции, что сделать, как проверить). Шаги строго в порядке исполнения; правки кода "
    "раньше, деплой/рестарт/проверка — последними.\n"
    "КРАСНАЯ ЗОНА В ТЗ — НЕ ПОВОД ОТКАЗЫВАТЬСЯ ОТ ПЛАНА (урок задачи 166): ты ТОЛЬКО планируешь "
    "и сам ничего не исполняешь, поэтому упоминание clasp/деплоя/рабочих таблиц/денег/удаления в "
    "ТЗ НЕ требует подтверждения на этапе плана — НЕ выводи NEEDS_APPROVAL из-за содержимого ТЗ. "
    "Красное действие оформи ОТДЕЛЬНЫМ шагом (обычно последним): исполнитель ЭТОГО шага сам "
    "спросит «да» Филиппа кнопкой по штатной механике. Если ТЗ явно говорит, что прод-применение "
    "(деплой/рестарт) делается отдельно/хвостом — тем более просто строй план. ЕДИНСТВЕННОЕ "
    "исключение: ВСЁ ТЗ целиком = одно красное действие и разбивать не на что (например «задеплой "
    "прод») — тогда вместо списка выведи РОВНО одну строку "
    "«NEEDS_APPROVAL: op=other | <карточка: что · куда · последствия>».\n\n"
    "КРУПНОЕ ТЗ:\n"
)


def _local_dec_on():
    """Флаг PC_LOCAL_DEC=1 в .env (демон load_dotenv'ит на старте). 0/нет → прежнее поведение."""
    return (os.environ.get("PC_LOCAL_DEC") or "").strip() == "1"


def _is_local_dec_parent(frm, text):
    """Родитель локальной декомпозиции: флаг взведён + from=Filipp-pcloc-dec + текст БЕЗ маркера
    «[шаг i/N родитель id]» (маркированный = шаг чужой/своей цепи, не родитель)."""
    if not _local_dec_on():
        return False
    if str(frm or "") != PC_LOCAL_DEC_FROM:
        return False
    return not _STEP_RE.match(str(text or "").strip())


def _plan_steps(out):
    """Строки плана из вывода планировщика по _PLAN_LINE_RE (прочие строки молча игнор;
    порядок — как в выводе). → список текстов шагов."""
    steps = []
    for ln in str(out or "").splitlines():
        m = _PLAN_LINE_RE.match(ln)
        if m:
            steps.append(m.group(2).strip())
    return steps


def _dec_red_note(steps):
    """🔴-пометка красных шагов плана (только дисплей в result родителя, тексты шагов не трогаем).
    → строка с \\n на конце или '' (красных нет)."""
    red = [str(i) for i, s in enumerate(steps, 1) if _HEADLESS_IMPOSSIBLE_RE.search(s)]
    if not red:
        return ""
    return ("🔴 красные шаги: " + ", ".join(red)
            + " — исполнитель шага спросит «да» кнопкой, сам не исполнит.\n")


# ---- ОБЯЗАТЕЛЬНЫЙ ФИНАЛЬНЫЙ СМОУК ЦЕПИ (родитель 92, шаг 4/6) ----------------
# Любая цепь, ТРОГАЮЩАЯ suggest/delivery/moderation, ОБЯЗАНА завершаться сквозным e2e-смоуком
# suggest.runLiveSmoke (уже в репо): авто-дописываем его ПОСЛЕДНИМ шагом плана — после шага(ов)
# применения (правки кода/деплой идут раньше, проверка последней — контракт PLANNER_PREAMBLE).
# Смоук исполняет САМ демон НАПРЯМУЮ (как команду-рычаг), без headless — детерминированный вердикт:
# runLiveSmoke status='failed' → шаг цепи failed (регрессия транспорта QUOTE/DELIVERY встаёт цепью
# штатным гейтом _loc_after_fail), passed/skipped → done. Без SUGGEST_TEST_MODE смоук skipped
# (живого клиента не касается) → шаг done. Смоук — сверх потолка MAX_STEPS (обязателен, не «шаг ТЗ»).
_SMOKE_DOMAIN_RE = re.compile(
    r"suggest|delivery|moderation|модерац|доставк|прайс|\bquote\b|черновик|зон[аеуой]",
    re.IGNORECASE)
SMOKE_STEP_MARK = "🔬 обязательный смоук-шаг"        # якорь авто-добавленного финального шага цепи
_SMOKE_STEP_RE = re.compile(re.escape(SMOKE_STEP_MARK))
SMOKE_STEP_TEXT = (
    SMOKE_STEP_MARK + " цепи (родитель 92, авто-добавлен): прогони СКВОЗНОЙ e2e-смоук живого "
    "контура suggest — демон исполнит его САМ через suggest.runLiveSmoke под SUGGEST_TEST_MODE, "
    "headless тут не нужен. Провал смоука (транспорт QUOTE/DELIVERY донёс не то: строка столбца J "
    "не дословно / цена зоны не та / год утёк / ложное «уточним») помечает ЭТОТ шаг failed и "
    "останавливает цепь; passed/skipped (TEST_MODE off) → done.")


def _chain_touches_suggest_domain(steps):
    """Цепь трогает suggest/delivery/moderation? True, если ЛЮБОЙ шаг плана поминает домен (имена
    модулей suggest/delivery/moderation или их RU-синонимы: доставка/модерация/прайс/зона/…). → bool."""
    return any(_SMOKE_DOMAIN_RE.search(str(s or "")) for s in (steps or []))


def _append_smoke_step(steps):
    """Дописать ОБЯЗАТЕЛЬНЫЙ финальный смоук-шаг, если цепь трогает домен и его ещё нет
    (идемпотентно: авто-добавление поверх уже добавленного / вписанного планировщиком смоука не
    дублируем). Смоук всегда ПОСЛЕДНИЙ. → новый список (исходный не мутируем)."""
    steps = list(steps or [])
    if not _chain_touches_suggest_domain(steps):
        return steps
    if any(_SMOKE_STEP_RE.search(str(s or "")) for s in steps):
        return steps
    return steps + [SMOKE_STEP_TEXT]


def _is_smoke_step(text):
    """Текст задачи — авто-добавленный обязательный смоук-шаг цепи (по якорю)? Работает и с
    префиксом «[шаг i/N родитель id] …» (search, не match). → bool."""
    return bool(_SMOKE_STEP_RE.search(str(text or "")))


def _run_live_smoke():
    """Прогнать suggest.runLiveSmoke живого контура (ленивый импорт suggest — тяжёлый модуль;
    вне TEST_MODE смоук сам вернёт skipped, живого клиента не касаясь). → dict-результат смоука."""
    import asyncio
    import suggest
    return asyncio.run(suggest.runLiveSmoke())


def _exec_smoke_step(smoke_fn=None):
    """Исполнить смоук-шаг цепи НАПРЯМУЮ (без headless), детерминированный вердикт → (status, result):
    runLiveSmoke status='failed' → ('failed', карточка ОЖИДАНИЕ/ФАКТ) — шаг встаёт failed, цепь
    halt'ится штатным гейтом; passed/skipped → ('done', итог). Любой СБОЙ самого прогона → failed
    (недостоверный смоук = регрессия не исключена → цепь не пускаем дальше). smoke_fn — для тестов."""
    fn = smoke_fn or _run_live_smoke
    try:
        r = fn() or {}
    except Exception as e:
        return "failed", (f"{SMOKE_STEP_MARK}: смоук НЕ отработал — {type(e).__name__}: {e} "
                          "(недостоверный прогон → шаг failed, цепь остановлена)")[:RESULT_MAX]
    status = str(r.get("status") or "")
    if status == "failed":
        body = (str(r.get("card") or "").strip() or str(r.get("reason") or "").strip()
                or "провал без карточки")
        return "failed", (f"{SMOKE_STEP_MARK}: смоук ПРОВАЛЕН — цепь остановлена.\n{body}")[:RESULT_MAX]
    if status in ("passed", "skipped"):
        tail = ("SUGGEST_TEST_MODE off — смоук пропущен (живого клиента не касались)"
                if status == "skipped" else "инварианты транспорта QUOTE/DELIVERY на месте")
        return "done", f"{SMOKE_STEP_MARK}: смоук {status} — {tail}."[:RESULT_MAX]
    return "failed", (f"{SMOKE_STEP_MARK}: смоук вернул неизвестный статус {status!r} → "
                      "трактую как провал (шаг failed)")[:RESULT_MAX]


def _local_dec_plan(tid, text):
    """Построить план декомпозиции для родителя Filipp-pcloc-dec ЛОКАЛЬНО (кондуктор
    THINKER_MODEL/фолбэк, чистый генератор) и закрыть родителя: done с планом в result
    (restart-proof источник плана) или честный failed с диагнозом. Красное НЕ ослаблено:
    планировщик ничего не исполняет; урок 166 — NEEDS_APPROVAL в выводе валит родителя
    ТОЛЬКО при пустом плане (чисто-красное ТЗ), и это failed-карта, НЕ кнопка."""
    out = _thinker_exec(PLANNER_PREAMBLE + str(text or ""), PC_DEC_PLAN_TIMEOUT, "pcloc-dec-plan")
    if out is None:
        msg = ("планировщик локальной декомпозиции не отработал (кондуктор "
               f"{THINKER_MODEL}/фолбэк {THINKER_FALLBACK or 'нет'}): сбой/таймаут claude -p — "
               "план не построен, повтори задачу")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.warning("pcloc-dec: id=%s планировщик не отработал → failed", tid)
        _cowork(f"родитель #{tid} (pcloc-dec) → failed · {_clip(msg)}")
        _notify_task("failed", tid, "планировщик декомпозиции не отработал")
        return
    steps = _plan_steps(out)
    if not steps:
        card = _detect_needs_approval(out)
        if card:   # чисто-красный родитель (единственное исключение урока 166) → failed, НЕ кнопка
            msg = ("планировщик needs_approval: " + card)[:RESULT_MAX]
        else:
            msg = ("план пуст: планировщик не вернул нумерованный список «N. <шаг>». "
                   "Вывод (хвост): " + _tail(out, 700))[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.warning("pcloc-dec: id=%s пустой план → failed (%s)", tid, _clip(msg, 160))
        _cowork(f"родитель #{tid} (pcloc-dec) → failed · {_clip(msg)}")
        _notify_task("failed", tid, msg)
        return
    if len(steps) > MAX_STEPS:
        msg = (f"план из {len(steps)} шагов превышает потолок {MAX_STEPS} — "
               "упрости ТЗ или разбей на два «декомпозируй:»")[:RESULT_MAX]
        bc.complete_task(tid, "failed", msg)
        log.warning("pcloc-dec: id=%s план %s шагов > %s → failed", tid, len(steps), MAX_STEPS)
        _cowork(f"родитель #{tid} (pcloc-dec) → failed · {_clip(msg)}")
        _notify_task("failed", tid, msg)
        return
    # ОБЯЗАТЕЛЬНЫЙ финальный смоук (родитель 92): цепь домена suggest/delivery/moderation ДОЛЖНА
    # заканчиваться сквозным e2e-смоуком. Дописываем СВЕРХ потолка (проверка MAX_STEPS выше — на
    # шагах ТЗ; смоук обязателен, не «шаг плана»). Идемпотентно: уже вписанный смоук не дублируем.
    steps = _append_smoke_step(steps)
    plan_txt = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    # РЕЛИЗ ШАГА 1 — ДО закрытия родителя (crash-окно спеки: шаг не встал → родитель остаётся
    # in_progress, зависание честно добьёт ПК-ливнесс process_stuck_singles; done-родитель
    # без шага 1 осиротил бы цепь молча). Только при ok шага 1 родитель закрывается done.
    if not _loc_release(tid, 1, len(steps), steps[0]):
        log.warning("pcloc-dec: id=%s шаг 1 не встал в очередь — родитель остаётся in_progress", tid)
        return
    result = (f"🧩 Декомпозиция (локальный дирижёр PC): {len(steps)} шагов — исполняю ПО ОДНОМУ "
              "(lane=pc, sequential-релиз: следующий шаг встаёт только после done предыдущего).\n"
              + plan_txt + "\n" + _dec_red_note(steps)
              + "Шаг 1 уже в очереди. Красный шаг спросит «да» кнопкой. "
                "После последнего шага пришлю сводку.")[:RESULT_MAX]
    bc.complete_task(tid, "done", result)
    log.info("pcloc-dec: id=%s план из %s шагов построен (done), шаг 1 релизнут", tid, len(steps))
    _cowork(f"родитель #{tid} (pcloc-dec) → done: план {len(steps)} шагов, шаг 1 в очереди · {_clip(result)}")
    _notify_task("done", tid, f"декомпозиция: план из {len(steps)} шагов, шаг 1 в очереди")
    _notify_chain_card(tid, f"🧩 План цепи #{tid}: {len(steps)} шагов — исполняю по одному, "
                            "шаг 1 в очереди. Управляй кнопками ниже.")


# ---- sequential-релиз и надзор локальной цепи (шаг 3/7 родителя 185) ----
# Порт process_pc_chains/_pc_chain_tick VPS-демона (docs/dec_port_spec.md, раздел 3) на локальную
# почву: тот же снимок очереди, те же маркеры, но исполнитель шагов — ЭТОТ ЖЕ демон (process_new),
# поэтому детект «ПК молчит» (PC_STEP_TIMEOUT) не нужен: зависший in_progress добьёт ПК-ливнесс
# process_stuck_singles → failed → halt цепи следующим тиком; new ждёт своего FIFO-клейма;
# needs_approval ждёт Филиппа (просрочку закроет process_approval_timeouts → failed → halt).
#
# КРАСНАЯ МЕХАНИКА ЦЕПИ (шаг 5/7 родителя 185, спека §4 «КРАСНОЕ В ЦЕПИ») — ШТАТНЫМ путём
# одиночек, без спец-веток: шаг цепи = обычная задача очереди, NEEDS_APPROVAL в его выводе →
# process_new → set_needs_approval (карточку с кнопками несёт devbot VPS: инбокс INBOX_TOPIC_ID,
# прод 1160; from=Filipp-pcloc-dec включён в его QUEUE_FROMS_PC); тик цепи на needs_approval →
# return (цепь ЖДЁТ «да», следующий шаг не релизится — sequential-модель). Approve → devbot ставит
# approved → process_approved ре-ран с нотой [ОДОБРЕНО ЧЕЛОВЕКОМ] → done → тик релизит следующий
# шаг (продолжение цепи). Reject («нет»/❌ — devbot финализирует failed «отклонено Филиппом»),
# просрочка needs_approval/approved (⏱-диагнозы) и повторное красное после approve (✋-ручная
# карта: headless доказанно не может) → halt цепи БЕЗ думателя (гейт _loc_after_fail) + сводка.
# Красное НЕ ослаблено: перерождённые/скорректированные шаги идут тем же путём — красное снова
# даст кнопку; approve обхода гейтов не создаёт.

_LOC_STATUSES = ("new", "in_progress", "needs_approval", "approved", "done", "failed")
_loc_summarized = set()    # родители, по которым сводка уже отправлена (дедуп-кэш памяти процесса;
                           # после рестарта от дублей защищает скан очереди _loc_summary_exists)
_loc_adapt_finish = {}     # pid → причина досрочного finish (кэш для 🏁-шапки сводки; сводка идёт
                           # ТЕМ ЖЕ вызовом _loc_after_done — рестарт между ними не страшен)
_loc_adapted = set()       # (pid, step_i), по которым думатель адаптации уже спрошен (память
                           # процесса; после рестарта — максимум один лишний keep-вопрос)


def _loc_enqueue(text):
    """Задача/synthetic локальной цепи в очередь lane=pc от имени дирижёра — ПРЯМОЙ канал
    ПК↔Bridge (86d8b03: enqueue_pc_task, минуя splinter/devbot). → dict в форме Bridge-ответа."""
    ok, nid, err = enqueue_pc_task(text, frm=PC_LOCAL_DEC_FROM)
    return {"ok": ok, "id": nid, "error": err}


def _loc_fetch_items():
    """Все задачи полосы pc по статусам _LOC_STATUSES → items (у каждого есть status) | None
    (ошибка чтения ЛЮБОГО статуса → пропустить цикл надзора ЦЕЛИКОМ: частичная картина цепи
    опаснее ожидания)."""
    items = []
    for st in _LOC_STATUSES:
        r = bc.get_pending(st)
        if not r.get("ok"):
            return None
        for it in r.get("items", []):
            if isinstance(it, dict):
                it.setdefault("status", st)
                items.append(it)
    return items


def _loc_group_chains(items):
    """items полосы pc → {pid: [(step_i, step_n, item), …]} ТОЛЬКО локальных цепей
    (from=Filipp-pcloc-dec + паттерн шага). Одиночки pc, цепи VPS-театра (from=Filipp-pc-dec)
    и synthetic без [шаг i/N] (сводки/карточки/родитель) не попадают."""
    chains = {}
    for it in items:
        if str(it.get("from") or "") != PC_LOCAL_DEC_FROM:
            continue
        m = _STEP_RE.match(str(it.get("task_text") or ""))
        if m:
            chains.setdefault(int(m.group(3)), []).append(
                (int(m.group(1)), int(m.group(2)), it))
    return chains


def _loc_chain_steps(pid):
    """Шаги цепи родителя pid (для осиротевшей сводки). Ошибка чтения → []."""
    items = _loc_fetch_items()
    return (_loc_group_chains(items).get(int(pid)) or []) if items is not None else []


_LOC_OPEN = ("new", "in_progress", "needs_approval", "approved")   # НЕЗАКРЫТЫЙ статус шага/родителя


def _loc_parent_status(items):
    """{pid: status} по РОДИТЕЛЬСКИМ задачам pcloc-dec: from=Filipp-pcloc-dec, БЕЗ маркера шага и
    БЕЗ synthetic-маркеров (сводка/карточка/коррекция). Родитель — носитель ТЗ, его id = pid цепи."""
    parents = {}
    for it in items:
        if str(it.get("from") or "") != PC_LOCAL_DEC_FROM:
            continue
        txt = str(it.get("task_text") or "")
        if (_STEP_RE.match(txt) or _SUM_RE.match(txt)
                or _CARD_RE.match(txt) or _ADAPT_CARD_RE.match(txt)):
            continue                                   # шаг/сводка/карточка — не родитель
        pid = it.get("id")
        if isinstance(pid, int):
            parents[pid] = str(it.get("status") or "")
    return parents


def _loc_active_chains(items):
    """Живые локальные цепи из снимка очереди → [{"pid": int, "label": str}], сорт. по pid.
    ЖИВАЯ цепь (класс-фикс призраков статуса) — ТОЛЬКО по живым признакам:
      • родитель pcloc-dec в new/in_progress (план строится / шаги ещё идут), ИЛИ
      • есть НЕЗАКРЫТЫЙ шаг [шаг i/N родитель pid] (статус ∈ _LOC_OPEN).
    Терминальный родитель (done/failed) без незакрытых шагов — ПРИЗРАК финализированной цепи: её
    step-карточки живут в очереди как done/failed (диагноз-ноты июльских тест-эпизодов), но работы
    за ней нет → в «в работе» НЕ показываем, сводка не обязательна (мёртвые родители её не имеют).
    Ярлык живого шага — текущий (максимальный номер среди открытых); иначе «план строится»."""
    parents = _loc_parent_status(items)
    chains = _loc_group_chains(items)
    active = {}
    for pid, steps in chains.items():                  # 1) незакрытый шаг → цепь жива
        open_steps = [(i, n, it) for (i, n, it) in steps if str(it.get("status")) in _LOC_OPEN]
        if open_steps:
            i, n, _it = max(open_steps, key=lambda s: (s[0], int(s[2].get("id") or 0)))
            active[pid] = f"шаг {i}/{n}"
    for pid, st in parents.items():                    # 2) живой родитель без открытых шагов
        if pid not in active and st in ("new", "in_progress"):
            active[pid] = "план строится"
    return [{"pid": pid, "label": active[pid]} for pid in sorted(active)]


def _loc_summary_exists(pid):
    """Сводка по родителю pid уже есть в очереди (в любом живом статусе)? Защита от дубля
    после рестарта демона (кэш _loc_summarized живёт только в памяти процесса)."""
    mark = f"[сводка родитель {pid}]"
    for st in ("done", "new", "in_progress"):
        try:
            r = bc.get_pending(st)
        except Exception:
            continue
        if r.get("ok") and any(str(it.get("task_text") or "").startswith(mark)
                               for it in r.get("items", [])):
            return True
    return False


def _loc_post_card(pid, text):
    """Событийная карточка цепи (🩹 retry / 🛑 terminal / 🏁 finish / ⚠️ план не восстановился) —
    synthetic-задачей прямым каналом (enqueue → claim → complete done): очередь — единственный
    канал дирижёра наружу, devbot принесёт done-рапортом. (🧭-карточка коррекции идёт отдельным
    маркером _ADAPT_CARD_RE — она же restart-proof носитель остатка плана.)"""
    r = _loc_enqueue(f"[карточка родитель {pid}] событие локальной цепи")
    if not r.get("ok"):
        log.warning("pcloc-dec: карточка родителя %s не встала в очередь (%s)", pid, r.get("error"))
        return
    sid = r.get("id")
    bc.claim_task(sid)                    # даже если claim не прошёл — complete финализирует
    bc.complete_task(sid, "done", str(text)[:RESULT_MAX])


def _loc_summary_counts(steps):
    """Дедуп-строки цепи → (rows, n_done, total) — общий для текста сводки и NOTE-журнала.
    Дубли номера (провал + перерождение самопочинки) — последняя запись по id; total =
    n-маркер последнего релизнутого шага (несёт актуальный итог после коррекций плана).
    Пустой набор → ([], 0, 0)."""
    rows = sorted([s for s in steps if str(s[2].get("status")) in ("done", "failed")],
                  key=lambda x: (x[0], int(x[2].get("id") or 0)))
    last = {}
    for i, n, it in rows:
        last[i] = (i, n, it)
    rows = [last[k] for k in sorted(last)]
    if not rows:
        return [], 0, 0
    n_done = sum(1 for _i, _n, it in rows if str(it.get("status")) == "done")
    total = rows[-1][1]
    return rows, n_done, total


def _loc_summary_text(pid, steps, note=None):
    """Сводка локальной цепи из переданных шагов (зеркало _pc_summary_text VPS). note — нота
    в шапке (напр. «⏹ остановлено владельцем»): она ЖЕ уходит в task_text/result сводки, поэтому
    причина стопа restart-proof читается из очереди, без памяти процесса."""
    rows, n_done, total = _loc_summary_counts(steps)
    if not rows:
        base = f"🧩 Сводка декомпозиции (родитель {pid}, локальный дирижёр): "
        return (base + (f"0 шагов done — {note}" if note else "шагов не найдено (очередь пуста?)"))[:RESULT_MAX]
    head = f"🧩 Сводка декомпозиции (родитель {pid}, локальный дирижёр): {n_done}/{total} шагов done"
    fin = _loc_adapt_finish.get(pid)
    if note:
        head += f", {note}"
    elif fin:
        head += f", 🏁 завершено досрочно: {fin}"
    elif n_done < len(rows):
        head += ", есть упавшие/пропущенные"
    lines = [head]
    for i, n, it in rows:
        emoji = "✅" if str(it.get("status")) == "done" else "❌"
        first = (str(it.get("result") or "").strip().splitlines() or ["(пусто)"])[0]
        lines.append(f"{emoji} шаг {i}/{n}: {first[:400]}")
    return "\n".join(lines)[:RESULT_MAX]


def _loc_post_summary(pid, steps):
    """Финал цепи → сводка synthetic-задачей прямым каналом (enqueue → claim → complete done).
    Идемпотентно: _loc_summarized (память) + _loc_summary_exists (скан очереди — restart-proof)."""
    if pid in _loc_summarized:
        return
    if _loc_summary_exists(pid):
        _loc_summarized.add(pid)
        return
    text = _loc_summary_text(pid, steps)
    _rows, n_done, total = _loc_summary_counts(steps)
    r = _loc_enqueue(f"[сводка родитель {pid}] сводный отчёт по шагам")
    if not r.get("ok"):
        log.warning("pcloc-dec: сводка родителя %s не встала в очередь (%s)", pid, r.get("error"))
        return
    sid = r.get("id")
    bc.claim_task(sid)
    cm = bc.complete_task(sid, "done", text)
    _loc_summarized.add(pid)
    _loc_mark_chain_final(pid)      # финализированная цепь карточек больше не анонсирует (restart-proof)
    _cowork(f"сводка родитель {pid}: {n_done}/{total} done")  # NOTE в журнал в момент постановки
    log.info("pcloc-dec: сводка родителя %s → задача %s (bridge_ok=%s)", pid, sid, cm.get("ok"))


# ---- ручное управление цепью из карточки дирижёра (кнопки, owner-gate у pc_agent) ----
# Механика «стоп» = вмешательство 21:05 13.07: текущий шаг доигрывает сам, дальше НЕ релизим.
# Реализуется ТЕМ ЖЕ штатным путём, что halt/finish — постановкой сводки «[сводка родитель pid]»:
# _loc_chain_tick проверяет _loc_summary_exists ДО _loc_after_done/_loc_after_fail, поэтому наличие
# сводки короткозамыкает и релиз следующего шага, и самопочинку/адаптацию. Осиротевших шагов нет:
# единственный живой шаг цепи (sequential — их максимум один) доводит надзор демона до терминала,
# а тик, увидев сводку, релиз не делает. Нота «остановлено владельцем» лежит в тексте сводки →
# restart-proof (причина стопа читается из очереди, без памяти процесса).

def _loc_stop_chain(pid, by="владельцем"):
    """Стоп цепи pid по кнопке владельца → (ok: bool, человекочит. текст). Идемпотентно: цепь уже
    закрыта (сводка есть) → ничего не постим. Не нашли цепь → ok=False. Читаем очередь ЦЕЛИКОМ
    (частичная картина опаснее ожидания — как весь надзор)."""
    pid = int(pid)
    items = _loc_fetch_items()
    if items is None:
        return False, f"цепь #{pid}: очередь Bridge недоступна — стоп не выполнен, повтори позже."
    steps = _loc_group_chains(items).get(pid)
    if not steps and pid not in _loc_parent_status(items):
        return False, f"цепь #{pid} не найдена в очереди — стоп не требуется (возможно, уже закрыта)."
    if _loc_summary_exists(pid):
        _loc_summarized.add(pid)
        return True, f"цепь #{pid} уже закрыта (сводка есть) — стоп не требуется."
    note = f"⏹ остановлено {by}"
    steps = steps or []
    text = _loc_summary_text(pid, steps, note=note)
    r = _loc_enqueue(f"[сводка родитель {pid}] {note}")
    if not r.get("ok"):
        log.warning("pcloc-dec: стоп-сводка родителя %s не встала (%s)", pid, r.get("error"))
        return False, f"цепь #{pid}: не удалось поставить сводку-стоп ({r.get('error')}) — повтори."
    sid = r.get("id")
    bc.claim_task(sid)
    bc.complete_task(sid, "done", text)
    _loc_summarized.add(pid)
    _loc_mark_chain_final(pid)      # стоп владельцем = финал: карточки цепи замолкают навсегда
    _rows, n_done, total = _loc_summary_counts(steps)
    _cowork(f"цепь #{pid} остановлена ({by}): {n_done}/{total} done, следующий шаг не релизится")
    log.info("pcloc-dec: цепь %s остановлена (%s) → сводка id=%s", pid, by, sid)
    return True, (f"⏹ цепь #{pid} остановлена ({by}): {n_done}/{total} шагов done, дальше не релизится. "
                  "Текущий шаг (если идёт) доработает сам.")


def _loc_chain_status(pid):
    """Честный статус цепи pid по снимку очереди → человекочит. строка «шаг i/N, последний done: …».
    Никакой памяти процесса — только очередь."""
    pid = int(pid)
    items = _loc_fetch_items()
    if items is None:
        return f"цепь #{pid}: очередь Bridge недоступна — статус неизвестен, повтори позже."
    steps = _loc_group_chains(items).get(pid) or []
    if not steps and pid not in _loc_parent_status(items):
        return f"цепь #{pid} не найдена в очереди (нет шагов и родителя — возможно, уже закрыта)."
    active = {c["pid"]: c["label"] for c in _loc_active_chains(items)}
    rows, n_done, total = _loc_summary_counts(steps)
    done_rows = [r for r in rows if str(r[2].get("status")) == "done"]
    if done_rows:
        di, dn, dit = done_rows[-1]
        first = (str(dit.get("result") or "").strip().splitlines() or ["(пусто)"])[0][:200]
        last_done = f"шаг {di}/{dn}: {first}"
    else:
        last_done = "пока нет"
    if pid in active:
        head = f"📊 цепь #{pid}: {active[pid]} (в работе), done {n_done}"
    elif _loc_summary_exists(pid):
        head = f"📊 цепь #{pid}: закрыта, {n_done}/{total} шагов done"
    else:
        head = f"📊 цепь #{pid}: активных шагов нет, {n_done}/{total} шагов done"
    return f"{head}. Последний done: {last_done}."


def _parse_numbered(text):
    """Нумерованные строки «N. <текст>» → {N: <текст>} (восстановление плана из result родителя /
    карточки коррекции). Прочие строки игнорируются."""
    out = {}
    for line in (text or "").splitlines():
        m = _PLAN_LINE_RE.match(line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _loc_current_plan(pid):
    """ТЕКУЩИЙ план цепи, restart-proof ИЗ ОЧЕРЕДИ (никакой памяти процесса): план родителя
    (нумерованный список в result, done lane=pc) + карточки «[коррекция плана родитель pid]
    после шага B…» (нумерованный остаток в result) поверх, в порядке id. → (plan: {номер:
    (текст, K-происхождение; 0=исходный)}, K всего коррекций, база последней коррекции | None).
    Ошибка чтения / родитель не найден → ({}, 0, None) — вызывающий даст честный halt-диагноз."""
    try:
        r = bc.get_pending("done")
    except Exception as e:
        log.warning("pcloc-dec: план родителя %s не прочитан (%s)", pid, e)
        return {}, 0, None
    if not r.get("ok"):
        return {}, 0, None
    parent_result, cards = "", []
    for it in r.get("items", []):
        if int(it.get("id") or 0) == int(pid):
            parent_result = str(it.get("result") or "")
        m = _ADAPT_CARD_RE.match(str(it.get("task_text") or ""))
        if m and int(m.group(1)) == int(pid):
            cards.append(it)
    plan = {num: (txt, 0) for num, txt in _parse_numbered(parent_result).items()}
    last_base = None
    cards.sort(key=lambda x: int(x.get("id") or 0))
    for k, card in enumerate(cards, 1):
        bm = _ADAPT_BASE_RE.search(str(card.get("task_text") or ""))
        nums = _parse_numbered(str(card.get("result") or ""))
        if not bm or not nums:
            continue          # осиротевшая/пустая коррекция — план не меняла (fail-safe keep)
        base = int(bm.group(1))
        plan = {num: v for num, v in plan.items() if num <= base}
        plan.update({num: (txt, k) for num, txt in nums.items()})
        last_base = base
    return plan, len(cards), last_base


def _loc_release(pid, j, total, text, k=0):
    """Релиз шага j/total цепи pid (sequential: следующий шаг встаёт ТОЛЬКО после done
    предыдущего — максимум один шаг цепи в очереди). k>0 → шаг из коррекции плана (маркер
    для restart-proof счётчика/глаз). Enqueue-fail → warn, повтор следующим тиком (done-шаг
    остаётся последним в снимке). → ok-флаг."""
    mark = f"[коррекция плана {k}] " if k else ""
    r = _loc_enqueue(f"[шаг {j}/{total} родитель {pid}] {mark}{text}"[:RESULT_MAX])
    if not r.get("ok"):
        log.warning("pcloc-dec: релиз шага %s/%s родителя %s не встал (%s) — повтор следующим циклом",
                    j, total, pid, r.get("error"))
        return False
    log.info("pcloc-dec: шаг %s/%s родителя %s релизнут (id %s, lane=pc)", j, total, pid, r.get("id"))
    _notify_chain_card(pid, f"▶️ Цепь #{pid}: шаг {j}/{total} в очереди."
                            + (f" (коррекция плана {k})" if k else ""))
    return True


def _loc_parent_context(pid):
    """Контекст родителя pid для думателей: (исходная цель дословно, план шагов). Родитель после
    декомпозиции лежит в done (task_text = цель, result = план). Не нашли → заглушки (не валимся)."""
    try:
        r = bc.get_pending("done")
        if r.get("ok"):
            for it in r.get("items", []):
                if int(it.get("id") or 0) == int(pid):
                    return (str(it.get("task_text") or "").strip()[:1500],
                            str(it.get("result") or "").strip()[:2000])
    except Exception as e:
        log.warning("pcloc-selfheal: контекст родителя %s не прочитан (%s)", pid, e)
    return (f"(родитель {pid} не найден в очереди)", "(план недоступен)")


def _loc_selfheal_consult(pid, step_i, step_n, step_text, fail_text):
    """Думатель самопочинки шага локальной цепи: промпт = цель родителя ДОСЛОВНО + план шагов +
    упавший шаг + суть провала. Возврат: dict {"verdict","fixed_step","reason"} или None
    (любой сбой думателя = None = fail-safe прежний halt-on-fail)."""
    goal, plan = _loc_parent_context(pid)
    prompt = (THINKER_PREAMBLE +
              f"ИСХОДНАЯ ЦЕЛЬ РОДИТЕЛЯ (дословно):\n{goal}\n\n"
              f"ПЛАН ШАГОВ РОДИТЕЛЯ:\n{plan}\n\n"
              f"УПАВШИЙ ШАГ {step_i}/{step_n} (текст дословно):\n{step_text}\n\n"
              f"СУТЬ ПРОВАЛА:\n{str(fail_text or '')[:1200]}\n")
    out = _thinker_exec(prompt, STEP_SELFHEAL_TIMEOUT, "pcloc-selfheal")
    if out is None:
        return None
    verdict = _parse_thinker_json(out, fix_key="fixed_step")
    if verdict is None:
        log.warning("pcloc-selfheal: ответ думателя не распарсился (fail-safe halt): %.200s", out)
    return verdict


def _loc_adapt_consult(pid, step_i, steps, remaining):
    """Думатель адаптации локальной цепи — ТА ЖЕ схема (ADAPT_PREAMBLE, кондуктор, --max-turns 1,
    строгий JSON): сделанное берём из done-шагов снапшота (дубли номера — последний по id:
    перерождение самопочинки затирает провал), оставшееся — из восстановленного плана (в очереди
    его нет — шаги релизятся по одному). None = fail-safe keep."""
    goal, plan_txt = _loc_parent_context(pid)
    done_last = {}
    for i, _n, it in sorted([s for s in steps if str(s[2].get("status")) == "done"],
                            key=lambda x: (x[0], int(x[2].get("id") or 0))):
        done_last[i] = str(it.get("result") or "").strip()
    done_lines = [f"шаг {i}: {(done_last[i].splitlines() or ['(пусто)'])[0][:300]}"
                  for i in sorted(done_last)] or ["(результатов пока нет)"]
    rem_lines = [f"шаг {j}: {t[:400]}" for j, t in remaining]
    prompt = (ADAPT_PREAMBLE +
              f"ИСХОДНАЯ ЦЕЛЬ РОДИТЕЛЯ (дословно):\n{goal}\n\n"
              f"ИСХОДНЫЙ ПЛАН ШАГОВ:\n{plan_txt}\n\n"
              f"РЕЗУЛЬТАТЫ СДЕЛАННЫХ ШАГОВ (сжато; только что завершён шаг {step_i}):\n"
              + "\n".join(done_lines) + "\n\n"
              "ОСТАВШИЕСЯ ШАГИ ПЛАНА:\n" + "\n".join(rem_lines) + "\n")
    out = _thinker_exec(prompt, PLAN_ADAPT_TIMEOUT, "pcloc-plan-adapt")
    if out is None:
        return None
    v = _parse_adapt_json(out)
    if v is None:
        log.warning("pcloc-plan-adapt: ответ думателя не распарсился/пуст (fail-safe keep): %.200s", out)
    return v


def _loc_after_fail(pid, i, n, it, steps):
    """Провал шага локальной цепи (уже failed в очереди). Отказ Филиппа / ⏱-диагноз
    (таймаут headless, ПК-ливнесс застрявшей in_progress, просрочка approve/needs_approval) /
    ✋-ручная карта (одобрено, но headless снова упёрся в красное) → halt без
    думателя; перерождение упало ПОВТОРНО → терминальный halt (🛑-карта); STEP_SELFHEAL=0 →
    прежний halt-on-fail; иначе думатель самопочинки → РОВНО одно перерождение с маркером
    «[самопочинка шага i, попытка 1]» + 🩹-карта. Halt в sequential-модели = просто НЕ релизить
    дальше + сводка (пропускать нечего — остальных шагов в очереди нет). FAIL-SAFE: любой сбой
    думателя/enqueue = halt (не хуже прежнего). Конверты сюда не попадают структурно: маркер
    конверта обязан стоять ПЕРВЫМ (^), а текст шага начинается с «[шаг i/N…»."""
    text = str(it.get("task_text") or "")
    fail_text = str(it.get("result") or "")
    if fail_text.lstrip().startswith(NO_HEAL_PREFIXES):   # ТОТ ЖЕ набор, что у гейта одиночки
        _loc_post_summary(pid, steps)         # человек сказал «нет» / ⏱ / ✋ — чинить нечего
        return
    if _HEAL_RE.search(text):
        _loc_post_card(pid, f"🛑 самопочинка не помогла (попытка 1 исчерпана): шаг {i}/{n} "
                            f"родителя {pid} упал повторно — цепочка остановлена, нужен человек.\n"
                            f"{fail_text[:400]}")
        _loc_post_summary(pid, steps)
        return
    if not _selfheal_on():
        _loc_post_summary(pid, steps)         # прежний halt-on-fail (провал уже отрапортован ❌)
        return
    verdict = _loc_selfheal_consult(pid, i, n, text, fail_text)
    if verdict is None or verdict["verdict"] != "retry" or not verdict["fixed_step"]:
        reason = (verdict or {}).get("reason") or "(сбой думателя — fail-safe halt)"
        _loc_post_card(pid, f"шаг {i}/{n} упал → думатель: halt, причина: {reason}\n"
                            f"Цепочка остановлена (диагноз думателя выше).")
        _loc_post_summary(pid, steps)
        return
    fixed, reason = verdict["fixed_step"], verdict["reason"] or "(без причины)"
    r = _loc_enqueue((f"[шаг {i}/{n} родитель {pid}] "
                      f"[самопочинка шага {i}, попытка 1] {fixed}")[:RESULT_MAX])
    if not r.get("ok"):
        log.warning("pcloc-dec: перерождение шага %s родителя %s не встало (%s) — fail-safe halt",
                    i, pid, r.get("error"))
        _loc_post_summary(pid, steps)         # очередь не приняла → halt (не хуже прежнего)
        return
    _loc_post_card(pid, f"🩹 шаг {i}/{n} упал → думатель: retry, правка: {fixed[:200]}, "
                        f"причина: {reason[:200]}\n"
                        f"Перерождён задачей id {r.get('id')} (lane=pc; попытка 1 из 1; повторный "
                        f"провал = терминальный halt).\nИсходный провал: {fail_text[:400]}")
    log.info("pcloc-dec: шаг %s/%s родителя %s перерождён задачей %s (retry)", i, n, pid, r.get("id"))


# ---------------- ПРИОРИТЕТ pc-полосы: owner (328/829/Dispatch) ⟩ ревизорские цепи ------------
# Правило приоритета очереди pc: постановки ВЛАДЕЛЬЦА (темы 328/829/Dispatch — всё, что НЕ порождено
# самим ревизором) всегда впереди РЕВИЗОРСКИХ цепей. Ревизорская цепь — pcloc-dec-родитель, чей
# task_text несёт маркер «[ревизор дата=… класс=…]» (_REVIZOR_TASK_RE); её шаги/сводки/карточки —
# та же дирижёрская механика. Рычагов два: (1) КЛЕЙМ — process_new клеймит ревизорского родителя
# ПОСЛЕ owner-задач; (2) УСТУПКА МЕЖДУ ШАГАМИ — done-шаг ревизорской цепи НЕ релизит следующий, пока
# есть незакрытая owner-работа (текущий шаг доработан, следующий не берётся). Всё restart-proof из
# очереди: родитель после декомпозиции живёт в done с исходным task_text (маркер [ревизор …] цел).

def _is_revizor_parent_text(text):
    """task_text — родитель РЕВИЗОРСКОЙ цепи (маркер «[ревизор дата=… класс=…]», _REVIZOR_TASK_RE)?
    Клейм-гейт process_new (owner впереди ревизорских родителей) и признак ревизорской цепи в снимке."""
    return bool(_REVIZOR_TASK_RE.match(str(text or "")))


def _revizor_chain_pids(items):
    """pid-ы РЕВИЗОРСКИХ цепей из снимка полосы: id pcloc-dec-родителя, чей task_text — [ревизор …].
    Родитель после декомпозиции лежит в done с исходным task_text → restart-proof из очереди, без памяти."""
    pids = set()
    for it in (items or []):
        if str(it.get("from") or "") != PC_LOCAL_DEC_FROM:
            continue
        if _is_revizor_parent_text(it.get("task_text")):
            pid = it.get("id")
            if isinstance(pid, int):
                pids.add(pid)
    return pids


def _is_owner_work(it, revizor_pids):
    """Задача полосы — OWNER-работа (постановка 328/829/Dispatch), которой ревизорская цепь уступает?
    НЕ owner: info-карточка ревизора (from=Filipp-revizor), ревизорский родитель/шаг ревизорской цепи,
    synthetic-артефакты дирижёра (сводка/карточка/коррекция). Owner: одиночки/дев-ТЗ/уроки владельца +
    owner-родители декомпозиции и шаги owner-цепей (pcloc-dec, чей pid НЕ ревизорский)."""
    frm = str(it.get("from") or "")
    txt = str(it.get("task_text") or "")
    if frm == REVIZOR_OWNER_FROM:
        return False                                   # info-карточка ревизора живёт до решения — не работа
    if frm != PC_LOCAL_DEC_FROM:
        return True                                    # одиночка/дев-ТЗ/урок владельца на полосе pc
    if _is_revizor_parent_text(txt):
        return False                                   # ревизорский родитель
    m = _STEP_RE.match(txt)
    if m:
        return int(m.group(3)) not in revizor_pids     # шаг owner-цепи = owner; шаг ревизорской = нет
    if _SUM_RE.match(txt) or _CARD_RE.match(txt) or _ADAPT_CARD_RE.match(txt):
        return False                                   # synthetic-артефакт дирижёра — не owner-работа
    return True                                        # owner-родитель декомпозиции


def _owner_work_pending(items, revizor_pids=None):
    """В снимке полосы есть НЕЗАКРЫТАЯ (new/in_progress/needs_approval/approved) owner-работа, перед
    которой ревизорская цепь обязана уступить? revizor_pids можно передать заранее (не пересчитывать)."""
    pids = revizor_pids if revizor_pids is not None else _revizor_chain_pids(items)
    for it in (items or []):
        if str(it.get("status") or "") in _LOC_OPEN and _is_owner_work(it, pids):
            return True
    return False


def _loc_after_done(pid, i, n, it, steps, items=None):
    """Done шага локальной цепи: план restart-proof из очереди → последний по плану → сводка
    (думатель НЕ зовётся — экономия лимитов); план не восстановился → ⚠️-карточка + halt; иначе
    при PLAN_ADAPT=1 адаптация (keep/adjust/finish): keep → релиз следующего шага прежнего плана;
    adjust → карточка коррекции (restart-proof остаток плана в result) + релиз первого
    скорректированного (счётчик K = карточки коррекций в очереди, лимит PLAN_ADAPT_MAX, дальше
    halt «план дрейфует»); finish → 🏁-карта + сводка «завершено досрочно». Дедуп консультаций:
    (pid, i) в _loc_adapted; шаг сам из свежей коррекции (last_base == i) → не переспрашиваем.
    ЛЮБОЙ сбой думателя/карточки/потолок MAX_STEPS = fail-safe keep."""
    plan, k_cnt, last_base = _loc_current_plan(pid)
    total = max(plan) if plan else n
    if i >= total:
        _loc_post_summary(pid, steps)
        return
    if plan.get(i + 1) is None:
        _loc_post_card(pid, f"⚠️ план родителя {pid} не восстановился из очереди (шаг {i + 1} "
                            f"не найден в result родителя/коррекций) — цепочка остановлена, "
                            f"поставь «декомпозируй:» заново.")
        _loc_post_summary(pid, steps)
        return
    # УСТУПКА МЕЖДУ ШАГАМИ: ревизорская цепь НЕ релизит следующий шаг, пока в очереди есть незакрытая
    # owner-работа (328/829/Dispatch). Текущий шаг доработан (done), owner-задачу возьмёт process_new
    # следующим слотом; цепь продолжится, когда owner-очередь опустеет (переоценка КАЖДЫЙ тик — состояние
    # из очереди, кэшей нет). Думателя адаптации тоже не тратим на уступке. items не передан → прежний путь.
    if items is not None:
        rev_pids = _revizor_chain_pids(items)
        if pid in rev_pids and _owner_work_pending(items, rev_pids):
            log.info("pcloc-dec: ревизорская цепь %s уступает owner-задачам — шаг %s/%s не релизим",
                     pid, i + 1, total)
            return
    consult = (_plan_adapt_on() and last_base != i and (pid, i) not in _loc_adapted)
    if consult:
        _loc_adapted.add((pid, i))
        remaining = [(j, plan[j][0]) for j in sorted(plan) if j > i]
        verdict = _loc_adapt_consult(pid, i, steps, remaining)
        if verdict is not None and verdict["verdict"] == "finish":
            reason = (verdict["reason"] or "(без причины)")[:300]
            _loc_adapt_finish[pid] = reason
            _loc_post_card(pid, f"🏁 после шага {i} думатель решил: цель родителя {pid} достигнута "
                                f"досрочно ({reason}) — оставшиеся шаги {i + 1}–{total} не релизятся.")
            _loc_post_summary(pid, steps)
            log.info("pcloc-plan-adapt: родитель %s finish после шага %s (%s)", pid, i, reason[:120])
            return
        if verdict is not None and verdict["verdict"] == "adjust":
            new_steps = verdict["adjusted_steps"]
            reason = (verdict["reason"] or "(без причины)")[:300]
            k = k_cnt + 1
            if k > PLAN_ADAPT_MAX:
                _loc_post_card(pid, f"🛑 план дрейфует: думатель запросил коррекцию №{k} (лимит "
                                    f"{PLAN_ADAPT_MAX} на цепь) — цепочка остановлена, нужен "
                                    f"владелец. Диагноз думателя: {reason}")
                _loc_post_summary(pid, steps)
                log.info("pcloc-plan-adapt: родитель %s — adjust №%s (> лимита %s) → halt (дрейф)",
                         pid, k, PLAN_ADAPT_MAX)
                return
            if i + len(new_steps) > MAX_STEPS:
                log.warning("pcloc-plan-adapt: родитель %s adjust дал %s шагов (итог > потолка %s) "
                            "— fail-safe keep", pid, len(new_steps), MAX_STEPS)
            else:
                new_total = i + len(new_steps)
                numbered = "\n".join(f"{j}. {s}" for j, s in enumerate(new_steps, start=i + 1))
                card = (f"🧭 после шага {i} думатель скорректировал план (коррекция "
                        f"{k}/{PLAN_ADAPT_MAX}): {reason}\n"
                        f"НОВЫЙ ОСТАВШИЙСЯ ПЛАН (шаги {i + 1}–{new_total}, релизятся по одному):\n"
                        f"{numbered}\n"
                        f"Итог плана {new_total} шагов; сделанные шаги 1–{i} не тронуты. "
                        f"Третья коррекция = halt «план дрейфует».")
                cr = _loc_enqueue(f"[коррекция плана родитель {pid}] после шага {i} (K={k})")
                if cr.get("ok"):
                    bc.claim_task(cr.get("id"))
                    bc.complete_task(cr.get("id"), "done", card[:RESULT_MAX])
                    _loc_release(pid, i + 1, new_total, new_steps[0], k=k)
                    log.info("pcloc-plan-adapt: родитель %s adjust K=%s после шага %s → релиз "
                             "скорректированного шага %s/%s", pid, k, i, i + 1, new_total)
                    return
                log.warning("pcloc-plan-adapt: карточка коррекции родителя %s не встала (%s) — "
                            "fail-safe keep", pid, cr.get("error"))
    # keep / fail-safe / адаптация выключена / уже спрошено → следующий шаг прежнего плана
    txt, k_origin = plan[i + 1]
    _loc_release(pid, i + 1, total, txt, k=k_origin)


def _loc_chain_tick(pid, steps, items=None):
    """Один тик надзора локальной цепи: смотрим ПОСЛЕДНИЙ шаг (максимальный номер, при дублях —
    старший id: перерождение самопочинки). Ожидание → return (исполнитель — этот же демон:
    new ждёт FIFO-клейма process_new, in_progress-зомби добьёт process_stuck_singles,
    needs_approval ждёт Филиппа / process_approval_timeouts); done/failed → хуки цепи."""
    i, n, it = max(steps, key=lambda s: (s[0], int(s[2].get("id") or 0)))
    st = str(it.get("status") or "")
    if st in ("new", "in_progress", "needs_approval", "approved"):
        return
    # терминальный статус: закрытая ранее цепь (рестарт демона) → в кэш и не трогать
    if _loc_summary_exists(pid):
        _loc_summarized.add(pid)
        _loc_mark_chain_final(pid)  # узнанная закрытая цепь — карточки замолкают (restart-proof)
        return
    if st == "failed":
        _loc_after_fail(pid, i, n, it, steps)
    elif st == "done":
        _loc_after_done(pid, i, n, it, steps, items)   # items → уступка ревизорской цепи owner-задачам


def process_local_chains():
    """Надзор локальных цепей (каждый цикл демона): read-only снимок полосы pc → тик по каждой
    СВОЕЙ цепи (from=Filipp-pcloc-dec). Чужое на полосе (одиночки, цепи VPS-театра Filipp-pc-dec)
    не трогаем. Сбой тика одной цепи не валит остальные (доберём следующим циклом).
    PC_LOCAL_DEC=0/нет → return сразу (поведение демона байт-в-байт прежнее)."""
    if not _local_dec_on() or _stopped():
        return
    items = _loc_fetch_items()
    if items is None:
        return
    chains = _loc_group_chains(items)
    for pid in sorted(set(chains) - _loc_summarized):
        try:
            _loc_chain_tick(pid, chains[pid], items)
        except Exception as e:
            log.warning("pcloc-dec: тик цепи родителя %s упал (%s) — следующим циклом", pid, e)


# ---- вотчдог застрявшей МЕЖДУ ШАГАМИ цепи (инцидент 15.07, цепь 365: 3/7 c 23:06) ----
# Штатный _loc_chain_tick релизит следующий шаг СОБЫТИЙНО — в момент, когда видит предыдущий done.
# Класс инцидента: это событие ПОТЕРЯНО (спавн шага упал / ПК уснул посреди consult-думателя /
# Bridge проглотил enqueue / ветка _loc_after_done погибла исключением на КАЖДОМ тике), и цепь
# зависает навсегда: последний шаг done, следующий не релизнут, 0 открытых шагов. process_stuck_singles
# ловит только орфана in_progress — межшаговый провал ему не виден. Вотчдог — СТРАХОВКА ПОВЕРХ тика,
# идущая тем же поллом ПОСЛЕ него: цепь без единого открытого шага, чей последний шаг done дольше
# PC_CHAIN_STALE, а план ещё не исчерпан → досдвиг следующего шага RECONCILE'ом ИЗ ОЧЕРЕДИ (текущий
# план восстановлен из result родителя/коррекций — как весь надзор, БЕЗ памяти процесса и БЕЗ
# потерянных событий) + карточка владельцу. Думателя-consult вотчдог НЕ зовёт: consult — как раз
# ветка, которая могла гибнуть; вотчдог только ПРОДОЛЖАЕТ цепь прежним планом, адаптацию доверенных
# шагов делает штатный тик на их done. Идемпотентно/restart-proof: после релиза max-шаг становится
# new (открыт) → следующим тиком цепь снова «в движении», вотчдог её не трогает; повторный запуск
# до персиста релиза лишь честно доводит тот же reconcile (сходится); финализированную цепь узнаём
# по _loc_summary_exists и молчим — финал-сводка НЕ дублируется.

def _loc_watchdog_tick(pid, steps, now, items=None):
    """Один тик вотчдога цепи pid: застряла ли она МЕЖДУ шагами и надо ли досдвинуть следующий.
    Реагирует ТОЛЬКО на явный межшаговый провал (все признаки — из снимка очереди):
      • нет ни одного открытого шага (new/in_progress/needs_approval/approved) — цепь не движется;
      • последний терминальный шаг именно DONE (failed — епархия _loc_after_fail: halt/самопочинка);
      • он done дольше PC_CHAIN_STALE (штатный тик имел много циклов и не сдвинул — это провал, не гонка);
      • сводки ещё нет (цепь не финализирована);
      • план восстановился из очереди и в нём ЕСТЬ следующий шаг (иначе досдвигать нечего/незачем).
    Тогда — прямой релиз следующего шага прежнего плана (k-происхождение сохраняем: шаг из коррекции
    останется помечен) + 🩺-карточка владельцу. Иначе — return (штатный тик/финал разберутся сами)."""
    open_steps = [s for s in steps if str(s[2].get("status")) in _LOC_OPEN]
    if open_steps:
        return                                   # цепь движется (ждёт клейма/approve/прогон) — не наш случай
    i, n, it = max(steps, key=lambda s: (s[0], int(s[2].get("id") or 0)))
    if str(it.get("status")) != "done":
        return                                   # последний шаг failed → halt/самопочинка ведёт _loc_after_fail
    age = _age_sec(it.get("updated"), now=now) or 0
    if age <= PC_CHAIN_STALE:
        return                                   # done недавно — даём штатному тику досдвинуть (не гонка)
    if _loc_summary_exists(pid):
        _loc_summarized.add(pid)
        return                                   # цепь уже закрыта сводкой — релизить нечего
    plan, _k, _lb = _loc_current_plan(pid)
    total = max(plan) if plan else n
    if i >= total:
        return                                   # план исчерпан — финал/сводка это работа штатного тика, не вотчдога
    nxt = plan.get(i + 1)
    if nxt is None:
        return                                   # план не восстановился — шаг не выдумываем (⚠️-halt ведёт штатный тик)
    txt, k_origin = nxt
    # УСТУПКА МЕЖДУ ШАГАМИ (та же, что в штатном _loc_after_done): ревизорская цепь, застрявшая между
    # шагами, НЕ досдвигается вотчдогом, пока есть незакрытая owner-работа — приоритет owner держится и
    # на страховочном пути. Ни карточки, ни релиза: продолжим, когда owner-очередь опустеет.
    if items is not None and pid in _revizor_chain_pids(items) and _owner_work_pending(items):
        log.info("pcloc-wd: ревизорская цепь %s застряла, но уступает owner-задачам — авто-релиз "
                 "шага %s/%s отложен", pid, i + 1, total)
        return
    _notify_chain_card(pid, f"🩺 Вотчдог цепи #{pid}: шаг {i + 1}/{total} не был релизнут штатно "
                            f"(потерянное событие) — авто-релиз reconcile'ом из очереди.")
    if _loc_release(pid, i + 1, total, txt, k=k_origin):
        log.warning("pcloc-wd: цепь %s застряла между шагами (шаг %s done %sс > %sс, 0 открытых) → "
                    "авто-релиз шага %s/%s", pid, i, int(age), PC_CHAIN_STALE, i + 1, total)
        _cowork(f"вотчдог цепи {pid}: застряла между шагами (шаг {i} done {int(age // 60)}м, "
                f"0 in_progress) → авто-релиз шага {i + 1}/{total}")


def process_stuck_chains(now=None):
    """Вотчдог застрявших МЕЖДУ ШАГАМИ локальных цепей (каждый цикл демона, ПОСЛЕ process_local_chains):
    read-only снимок полосы pc → тик-вотчдог по каждой СВОЕЙ цепи (from=Filipp-pcloc-dec). Досдвигает
    цепь, у которой последний шаг done > PC_CHAIN_STALE, а открытых шагов нет и план не исчерпан —
    reconcile ИЗ ОЧЕРЕДИ (потерянный релиз восстанавливается из состояния, не из событий). Чужое на
    полосе (одиночки, цепи VPS-театра) не трогаем — как весь надзор. Сбой тика одной цепи не валит
    остальные. PC_LOCAL_DEC=0/нет → return сразу (поведение демона байт-в-байт прежнее, очередь не читаем)."""
    if not _local_dec_on() or _stopped():
        return
    items = _loc_fetch_items()
    if items is None:
        return
    now = now or datetime.datetime.now(datetime.timezone.utc)
    chains = _loc_group_chains(items)
    for pid in sorted(set(chains) - _loc_summarized):
        try:
            _loc_watchdog_tick(pid, chains[pid], now, items)
        except Exception as e:
            log.warning("pcloc-wd: вотчдог цепи родителя %s упал (%s) — следующим циклом", pid, e)


def _loc_finalize_orphan_synthetic(tid, text):
    """Осиротевшая synthetic-задача локальной цепи (демон упал между enqueue и complete) →
    довести done, НЕ исполняя headless'ом и НЕ отдавая планировщику как «родителя»
    (порт одноимённой ветки process_new VPS-демона). True = финализирована здесь."""
    sm = _SUM_RE.match(text)
    if sm:
        pid = int(sm.group(1))
        bc.complete_task(tid, "done", _loc_summary_text(pid, _loc_chain_steps(pid)))
        log.info("pcloc-dec: осиротевшая сводка id=%s доведена", tid)
        return True
    if _ADAPT_CARD_RE.match(text):
        bc.complete_task(tid, "done", "🧭 карточка коррекции плана (осиротела при рестарте "
                                      "демона; шаги коррекции уже в цепочке родителя)")
        log.info("pcloc-dec: осиротевшая карточка адаптации id=%s доведена", tid)
        return True
    if _CARD_RE.match(text):
        bc.complete_task(tid, "done", "🃏 карточка события цепи (осиротела при рестарте демона; "
                                      "цепь родителя идёт своим ходом)")
        log.info("pcloc-dec: осиротевшая карточка id=%s доведена", tid)
        return True
    return False


# ------------------- авто-обновление userbot/moderbot после дев-задач ----------
# Убираем ручное «обнови userbot» из темы 205: после done дев-задачи («тз:…»), если она
# создала НОВЫЕ коммиты, затронувшие рантайм-файлы бота → гейт (unittest затронутых тестов)
# → зелено → рестарт бота ТОЙ ЖЕ механикой pc_agent (CIM-поиск PID + taskkill + venv-spawn,
# PID-контроль) → верификация (PID поднялся + лог свежий) → строка в карточку/cowork.
# Гейт красный → НЕ рестартим (код запушен, применится позже). Стоп-флаг уважаем.
# suggest.py/pricing.py импортят ОБА бота → их правка рестартит и userbot, и moderbot.

# ЯВНАЯ карта «файл → какие процессы рестартить» (НЕ эвристика префиксов): единственный источник
# правды и для авто-обновления после дев-задач (maybe_update_bots), и для реконсиляции детей после
# self-update демона (_selfupdate_restart_children). Значения — подмножество
# {"userbot","moderbot","pc_agent"}. pc_agent НИКОГДА не рестартим чужими руками (только пометка
# «ждёт ручного рестарта»). Правила проверяются по имени файла (basename, lower); один файл может
# задеть НЕСКОЛЬКО процессов (suggest/pricing импортят и userbot, и moderation_bot — общий рантайм).
# moderation_ipc.py специально → userbot (IPC модерации исполняет userbot), а НЕ moderbot —
# поэтому карта явная, без общего префикса «moderation».
_FILE_PROCESS_RULES = (
    (lambda n: n.startswith("userbot"),   ("userbot",)),              # userbot_listen.py и т.п.
    (lambda n: n.startswith("suggest"),   ("userbot", "moderbot")),   # suggest*.py — общий рантайм
    (lambda n: n.startswith("pricing"),   ("userbot", "moderbot")),   # pricing*.py — общий рантайм
    (lambda n: n.startswith("delivery"),  ("userbot", "moderbot")),   # delivery*.py — резолвер доставки, импортит suggest → общий рантайм (живой кейс dae330a: без правила userbot жил на старом коде 2ч+)
    (lambda n: n.startswith("booking"),   ("moderbot",)),             # booking_draft.py — ПО ИМПОРТАМ грузит ТОЛЬКО moderation_bot (make_booking_and_intake/build_intake); userbot booking* не импортит (ни напрямую, ни через suggest) → рестарт moderbot, НЕ userbot (мисроут c809924)
    (lambda n: n == "trainer.py",         ("userbot", "moderbot")),   # trainer.py — ПО ФАКТУ импортят ОБА: userbot_listen.py:44 (рендер/турны тренажёра) и moderation_bot.py:36 (панель, гипотезы, уроки); без правила правка ТОЛЬКО trainer.py не рестартила никого и живые боты доживали на старом коде (тот же класс, что delivery.py dae330a). Точное имя, не префикс: trainer_rules.json — данные, читаются на вызове, рестарта не требуют
    (lambda n: n == "trainer_log.py",     ("userbot", "moderbot")),   # trainer_log.py — ПО ФАКТУ импортят ОБА: userbot_listen.py (реплики/ответы/команды) и moderation_bot.py (нажатия кнопок, уроки). Точное имя, а не префикс «trainer»: рядом лежат данные trainer_rules.json / trainer_log_doc.json — они читаются на вызове и рестарта НЕ требуют. Без правила правка ТОЛЬКО этого модуля не рестартила бы никого (класс delivery.py dae330a)
    (lambda n: n == "log_setup.py",       ("userbot", "moderbot", "pc_agent")),  # общая ротация логов — импортят ВСЕ долгоживущие процессы (userbot_listen/moderation_bot/pc_agent/сам демон); хендлер вешается на импорте, значит новый порог/путь подхватывается ТОЛЬКО рестартом. Без правила правка одного log_setup.py не рестартила бы никого — ровно класс delivery.py dae330a (живой бот на старом коде часами)
    (lambda n: n == "moderation_ipc.py",  ("userbot",)),              # IPC модерации → рестарт userbot
    (lambda n: n == "moderation_bot.py",  ("moderbot",)),             # сам модербот
    (lambda n: n == "moderation_core.py", ("moderbot",)),             # ядро модерации
    (lambda n: n == "pc_agent.py",        ("pc_agent",)),             # агент — ТОЛЬКО пометка (ручной рестарт)
)
_RE_DEV_TASK = re.compile(r"^\s*тз\b", re.I)   # дев-задача: текст начинается с «тз:/тз …»


def _procs_for_file(path):
    """Множество процессов, которые надо рестартить из-за правки данного файла (по ЯВНОЙ карте).
    Пусто → файл рантайм ботов не задевает (README/*.md/тесты и т.п.)."""
    name = os.path.basename(str(path or "")).lower()
    procs = set()
    for pred, targets in _FILE_PROCESS_RULES:
        if pred(name):
            procs.update(targets)
    return procs


def _is_dev_task(text):
    """Дев-задача («тз:…») — только после таких обновляем боты (обычные задачи не трогают рантайм)."""
    return bool(_RE_DEV_TASK.match(str(text or "")))


def _gate_step_selective_on():
    """Флаг GATE_STEP_SELECTIVE=1 (.env): селективный гейт затронутых тестов на ПРОМЕЖУТОЧНЫХ шагах
    цепи (финальный шаг всё равно полный, неубираемо). 0/нет → прежний гейт затронутых модулей
    (байт-в-байт). Порт VPS-образца d288947; независимый откат от GATE_SINGLE_SELECTIVE."""
    return gate_selective.env_on("GATE_STEP_SELECTIVE")


def _gate_single_selective_on():
    """Флаг GATE_SINGLE_SELECTIVE=1 (.env): селективный гейт для ОДИНОЧНЫХ (не-цепь) дев-задач с
    fail-safe в полный гейт. 0/нет → прежнее поведение. Порт VPS-образца 9c3c3e7."""
    return gate_selective.env_on("GATE_SINGLE_SELECTIVE")


def _changed_files_since(head_before):
    """Файлы, изменённые НОВЫМИ коммитами задачи (head_before..HEAD). → список путей.
    Нет head_before / git молчит / нет новых коммитов → [] (обновлять нечего)."""
    if not head_before:
        return []
    head_now = _git_out(["rev-parse", "HEAD"])
    if not head_now or head_now == head_before:
        return []
    out = _git_out(["diff", "--name-only", f"{head_before}..{head_now}"])
    if not out:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _classify_changed(paths):
    """Разложить изменённые файлы по цели рестарта бота по ЯВНОЙ карте _FILE_PROCESS_RULES. →
    (userbot_files, moderbot_files). Файл может попасть в обе группы (suggest/pricing — общий
    рантайм). pc_agent сюда НЕ попадает: maybe_update_bots рестартит только боты (агент — пометка)."""
    ub, mb = [], []
    for p in paths:
        procs = _procs_for_file(p)
        if "userbot" in procs:
            ub.append(p)
        if "moderbot" in procs:
            mb.append(p)
    return ub, mb


# ───────── ЗАПРЕТ АВТО-РЕСТАРТА ПОВЕРХ ГРЯЗНОГО ДЕРЕВА (класс 28.07.2026) ─────────
# Живой инцидент: демон обновил себя 1f5d10d→580d0d4, диффил от СВОЕГО запущенного коммита,
# поймал в диапазон старый 7a3c9b6 (suggest.py, pricing.py) и рестартнул userbot 16900 и
# moderbot 9592. Python грузит модули С ДИСКА, а не из коммита — и вместе с коммитом в бой уехал
# НЕЗАКОММИЧЕННЫЙ detectVehicleType из рабочего дерева. Обошлось только потому, что функцию
# никто не вызывает: повезло, а не защитило.
#
# Правило: авто-рестарт боевого процесса разрешён ТОЛЬКО когда чисты файлы, которые ЭТОТ процесс
# несёт. Грязно — рестарт НЕ выполняется, владелец получает карточку с поимённым списком.
# РУЧНОЙ рычаг владельца (_exec_command «рестартни userbot») под запрет НЕ попадает: там решает
# человек, он дерево видит.
# Считаем только ОТСЛЕЖИВАЕМЫЕ правки: untracked-файла в коммите не было вовсе, сказать про него
# «коммит уехал не целиком» нельзя — та же граница, что у авто-фетча ниже.
_ORCH_RUNTIME = ("pc_orchestrator.py", "gate_selective.py", "task_metrics.py",
                 "lesson_router.py", "log_setup.py",
                 "client_contour.py",
                 # 30.07.2026: демон импортирует их СВЕРХУ, значит незакоммиченная правка уедет в
                 # бой вместе с рестартом. pretool_guard — новый импорт (словарь видов красного для
                 # разбора одобренной карточки), io_utf8 стоял в импортах и в список не попал.
                 "pretool_guard.py", "io_utf8.py")     # что несёт САМ демон (его верхние импорты)
_DIRTY_WARNED = {}      # процесс → (коммит, кортеж грязных файлов), о которых уже сказали


def _dirty_tracked(runner=None):
    """Отслеживаемые файлы с незакоммиченными правками (индекс + рабочее дерево против HEAD).
    → список путей | None, если git не ответил: «не знаю» это НЕ «чисто», и решать рестарт по
    незнанию мы не станем.

    Берём `git diff --name-only HEAD`, а НЕ `status --porcelain`: у porcelain имя лежит в
    ФИКСИРОВАННОЙ колонке (ln[3:]), а наш _git_call делает .strip() ВСЕГО вывода — первая строка
    теряла ведущий пробел, и имя приезжало обрезанным («c_orchestrator.py»), мимо карты процессов.
    То есть гард молча открывался. Поймано ЖИВОЙ проверкой на реальном дереве 28.07 — здесь имя
    приходит отдельной строкой без колонок, обрезать нечего.
    Untracked в этот список не входят по определению — и правильно: в коммите их не было вовсе."""
    r = (runner or _git_call)(["diff", "--name-only", "HEAD"], timeout=30)
    if not r or r[0] != 0:
        return None
    return [ln.strip() for ln in (r[1] or "").splitlines() if ln.strip()]


def _dirty_for_proc(kind, dirty_fn=None):
    """Грязные файлы, которые несёт процесс kind. Для ботов — по той же карте, что решает рестарт;
    для демона — его собственный модуль и верхние импорты. → список | None (состояние неизвестно)."""
    dirty = (dirty_fn or _dirty_tracked)()
    if dirty is None:
        return None
    if kind == "orchestrator":
        return sorted(p for p in dirty if os.path.basename(p).lower() in _ORCH_RUNTIME)
    return sorted(p for p in dirty if kind in _procs_for_file(p))


def _dirty_block(kind, commit, where, label=None, dirty_fn=None, notifier=None,
                 cowork=None, state=None):
    """ВОРОТА авто-рестарта. → список грязных файлов (рестарт ЗАПРЕЩЁН) | [] (можно).

    Отказ — не молчание: лог + строка в журнал + карточка владельцу с именем процесса, коммитом и
    ПОИМЁННЫМ списком. Ровно ОДИН раз на пару «коммит + состав грязного»: пока ничего не менялось,
    долбить владельца каждым циклом незачем; изменилось — сказать обязаны заново. Дерево вычистили
    → память сбрасываем, следующий отказ снова заслуживает карточки."""
    st = _DIRTY_WARNED if state is None else state
    files = _dirty_for_proc(kind, dirty_fn)
    if files is None:
        files = ["<git не ответил: состояние дерева неизвестно>"]
    if not files:
        st.pop(kind, None)
        return []
    key = (str(commit), tuple(files))
    if st.get(kind) != key:
        st[kind] = key
        name = label or kind
        log.error("%s: дерево ГРЯЗНОЕ — авто-рестарт «%s» ЗАПРЕЩЁН; файлы: %s",
                  where, name, ", ".join(files))
        (cowork or _cowork)(
            "авто-рестарт %s ОТМЕНЁН при %s (%s): дерево грязное, коммит в бой уехал бы НЕ целиком "
            "— %s" % (name, commit, where, ", ".join(files)))
        (notifier or _notify)(
            "⛔ Оркестратор: авто-рестарт «%s» ОТМЕНЁН — рабочее дерево грязное.\n"
            "Коммит %s в бой уехал бы НЕ целиком: python грузит модули С ДИСКА, вместе с коммитом "
            "поднялся бы незакоммиченный код.\n"
            "Грязные файлы этого процесса (%d): %s\n"
            "Закоммить или откати их — рестарт пойдёт сам." % (name, commit, len(files), ", ".join(files)))
    return files


# ───────── ВОРОТА КЛИЕНТСКОГО КОНТУРА (класс 30.07.2026) ─────────
# Живой инцидент: цепь ревизора id=44 правила suggest.py, и авто-реконсайл САМ выкатил коммит
# 4528917 на боевые боты (01:48:22 userbot PID 6692 … 01:53:31 userbot PID 7064) за 17 минут ДО
# того, как владелец успел цепь остановить (02:05:51). За двое суток ревизор поставил ДВЕ такие
# цепи (id=4 «тип ТС», id=44 «гард приветствий»); текст от клиента удержал только SUGGEST_TEST_MODE
# — то есть ВТОРОЙ слой, а не ворота. Гейт на пути был, но ТЕСТОВЫЙ: зелёные тесты ≠ решение
# человека о том, что живой клиент увидит другой текст.
#
# Правило: коммит, задевший КЛИЕНТСКИЙ файл, к живым ботам автоматически НЕ применяется. Признак
# клиентского — client_contour (транзитивное import-замыкание userbot_listen.py/moderation_bot.py,
# считается по факту с диска, а не списком имён). Вместо применения — карточка владельцу: что
# меняется, какие файлы, какой коммит, как откатить. Ворота открывают РОВНО ДВА основания:
#   • «да» владельца (команда-рычаг «выкати» → client_contour.approve);
#   • ЗЕЛЁНЫЙ ПРОГОН ЧЕРЕЗ ТРЕНАЖЁР (30.07.2026 заглушка ЗАКРЫТА): безголовый раннер
#     `trainer_run.py` гоняет корпус `trainer_cases.json` ВНЕ клиентского контура (боевой userbot и
#     его singleton не трогает) и кладёт вердикт в pc_orchestrator.client_trainer_green.json.
#     Ворота засчитывают его, только если 12 кейсов из 12 прошли ВСЕ чеки в ДВУХ прогонах, дерево
#     было чистым и коммит вердикта РОВНО тот, что выкатывается (client_contour.trainer_verdict).
# FAIL-CLOSED: признак не смог решить (граф не строится, файл не читается, путь пуст) → файл
# считается КЛИЕНТСКИМ и применение не идёт.
# ВНУТРЕННИЙ контур ворот НЕ касается: self-update самого демона и его эстафета работают как
# работали — ворота стоят ТОЛЬКО на рестарте детей-ботов. Ирония на месте и она правильная: эта
# правка живёт в pc_orchestrator.py, файле ВНУТРЕННЕМ, и применится сама.
# РУЧНОЙ рычаг «рестартни userbot» (_exec_command) под ворота не попадает — там решает человек,
# ровно как у запрета грязного дерева выше.
_CLIENT_HELD_WARNED = {}     # «кого держим» → (коммит, кортеж клиентских файлов), о чём уже сказали


def _client_paths(paths):
    """Клиентские пути из списка — ПРИЗНАК (граф импортов) ∪ явная карта рестарта ботов.
    Объединение, а не «или-или»: карта ловит имя, которого ещё никто не импортит (новый
    suggest_*.py), граф ловит модуль, которого в карте нет (lesson_router.py — его тянет
    trainer.py, а карта про него не знает). Признак упал → считаем клиентским (fail-closed)."""
    out = []
    for p in (paths or []):
        try:
            hit = client_contour.is_client(p, REPO)
        except Exception as e:                       # noqa: BLE001 — «не знаю» это НЕ «внутренний»
            log.error("ворота контура: признак упал на «%s» (%s) — считаю КЛИЕНТСКИМ", p, e)
            hit = True
        if hit or (_procs_for_file(p) & {"userbot", "moderbot"}):
            out.append(p)
    return out


def _commit_subject(commit):
    """Тема коммита для карточки (владельцу нужен смысл правки, а не только хеш). Молчит git → ''."""
    return _git_out(["log", "-1", "--format=%s", str(commit)]) or ""


def _client_block(kinds, commit, paths, where, subject=None, notifier=None, cowork=None,
                  state=None, reason_fn=None, client_fn=None, subject_fn=None, trainer_fn=None):
    """ВОРОТА клиентского контура. → список клиентских файлов (применять НЕЛЬЗЯ) | [] (можно).

    Отказ — не молчание: лог + строка в журнал + карточка владельцу с коммитом, поимённым списком
    и командой отката. Ровно ОДИН раз на пару «коммит + состав клиентских файлов»: реконсиляция
    приходит каждые 60 с, долбить владельца каждым тиком нельзя, а изменился состав — сказать
    обязаны заново. Основание пропуска («да» / тренажёр) снимает ворота и чистит память."""
    held = (client_fn or _client_paths)(paths)
    st = _CLIENT_HELD_WARNED if state is None else state
    kk = ",".join(kinds) if kinds else "боты"
    if not held:
        st.pop(kk, None)
        return []
    reason = (reason_fn or client_contour.release_reason)(commit)
    if reason:
        st.pop(kk, None)
        log.info("ворота контура (%s): основание пропуска «%s» — коммит %s применяем к %s",
                 where, reason, commit, kk)
        (cowork or _cowork)(
            "ворота клиентского контура ОТКРЫТЫ по основанию «%s»: применяю %s к %s (%s)"
            % (reason, commit, kk, ", ".join(held)))
        return []
    key = (str(commit), tuple(held))
    if st.get(kk) != key:
        st[kk] = key
        log.error("ворота контура (%s): применение %s к «%s» ОСТАНОВЛЕНО — клиентские файлы: %s",
                  where, commit, kk, ", ".join(held))
        (cowork or _cowork)(
            "ворота клиентского контура: применение %s к %s ОСТАНОВЛЕНО (%s) — клиентские файлы: "
            "%s; жду «да» владельца или зелёный тренажёр" % (commit, kk, where, ", ".join(held)))
        (notifier or _notify)(client_contour.card_text(
            kinds or ["боты"], commit, held,
            subject=subject if subject is not None else (subject_fn or _commit_subject)(commit),
            where=where,
            # ВТОРОЕ основание живое (trainer_run.py отдаёт вердикт) — карточка обязана сказать,
            # ПОЧЕМУ оно не сработало на этом коммите: вердикта нет / КРАСНЫЙ / снят на чужом HEAD.
            # Причина берётся ТЕМ ЖЕ инжектом, что и основание: если решение читает один источник,
            # а карточка другой, владельцу приедет причина не про тот отказ (поймано живой пробой
            # 30.07 — карточка говорила «прогона не было» на подложенный чужой вердикт).
            trainer_available=client_contour.trainer_enabled(),
            trainer_note=(trainer_fn or client_contour.trainer_status)(commit)))
    return held


def _affected_test_modules(paths):
    """Тест-модули для изменённых .py: сам test_*.py и test_<stem> при наличии на диске. → отсортированный список."""
    mods = set()
    for p in paths:
        base = os.path.basename(p)
        if not base.endswith(".py"):
            continue
        stem = base[:-3]
        if stem.startswith("test_"):
            mods.add(stem)
            continue
        cand = "test_" + stem
        if os.path.isfile(os.path.join(REPO, cand + ".py")):
            mods.add(cand)
    return sorted(mods)


def _gate_test_modules(mods):
    """Гейт обновления бота: unittest затронутых тест-модулей новым кодом. → (ok, msg).
    Нет затронутых тестов → (True, '…') — рестарт без гейта (config-правка; код уже прошёл тесты в задаче)."""
    if not mods:
        return True, "нет затронутых тестов — рестарт без гейта"
    try:
        p = subprocess.run([VENV_PY, "-m", "unittest", *mods], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, creationflags=NO_WINDOW)
        return p.returncode == 0, _tail((p.stderr or "") + (p.stdout or ""), 400)
    except Exception as e:
        return False, f"unittest-гейт не запустился: {e}"


def _log_size(path):
    try:
        return os.path.getsize(str(path))
    except Exception:
        return -1


def _restart_via_pc_agent(kind, settle=0.5, wait_cycles=20):
    """Рестарт бота ТОЙ ЖЕ механикой pc_agent: стоп (CIM-поиск PID + taskkill) → дождаться
    чистоты → старт через venv-python → PID-контроль. kind ∈ 'userbot'|'moderbot'.
    → (ok, pids, detail). Реальные процессы — в тестах функция мокается целиком."""
    try:
        import pc_agent   # lazy: не тянем telegram в импорт оркестратора
    except Exception as e:
        return False, [], f"не смог импортировать механику pc_agent: {e}"
    if kind == "userbot":
        proc, finder, logfile = pc_agent.UserbotProcess(), pc_agent._find_userbot_pids, pc_agent.USERBOT_LOG
    else:
        if not os.getenv("MODERBOT_TOKEN", "").strip():
            # модербот без токена не поднимается (reply-режим userbot) — рестарт не требуется,
            # новый код применится сам, когда/если модербот запустят с токеном.
            return True, [], "модербот в reply-режиме (нет MODERBOT_TOKEN) — рестарт не требуется"
        proc, finder, logfile = pc_agent.ModerbotProcess(), pc_agent._find_moderbot_pids, os.path.join(REPO, "moderation_bot.log")
    try:
        proc.stop()
        for _ in range(wait_cycles):        # ждём смерти старых экземпляров (не поднимаем поверх живого)
            if not finder():
                break
            time.sleep(settle)
        size_before = _log_size(logfile)
        start_msg = proc.start()
        pids = finder()
        if not pids:
            return False, [], f"процесс не поднялся: {start_msg}"
        fresh = _log_size(logfile) > size_before   # свежая строка лога = файл вырос после старта
        return True, pids, ("PID поднят, лог свежий" if fresh else f"PID поднят, лог без новых строк ({start_msg})")
    except Exception as e:
        return False, [], f"ошибка рестарта: {e}"


def maybe_update_bots(tid, text, head_before, changed_fn=None, gate_fn=None,
                      restart_fn=None, is_dev_fn=None, head_fn=None, dirty_fn=None,
                      client_block_fn=None):
    """После done дев-задачи применить свежий код к боту(ам). → строка-суффикс для карточки/cowork
    ('' если обновлять нечего). Уважает стоп-флаг и ЗАПРЕТ грязного дерева (_dirty_block):
    рестарт идёт только если чисты файлы, которые несёт ЭТОТ бот. Всё внешнее инъектируется."""
    if _stopped():
        return ""
    # Дев-задача («тз:…») применяется всегда; ШАГ цепи («[шаг i/N…]») — только под GATE_STEP_SELECTIVE
    # (иначе шаги бот не авто-применяют — прежнее поведение байт-в-байт). Контент шага не помечен «тз»,
    # поэтому дев-признак шага — сам git-дифф рантайм-файлов (ниже _classify_changed отсеет не-рантайм).
    is_step, step_i, step_n = gate_selective.parse_step(text)
    step_apply = is_step and _gate_step_selective_on()
    if not ((is_dev_fn or _is_dev_task)(text) or step_apply):
        return ""
    changed = (changed_fn or _changed_files_since)(head_before)
    if not changed:
        return ""
    ub_files, mb_files = _classify_changed(changed)
    if not (ub_files or mb_files):
        return ""
    commit = (head_fn or _head_commit)()
    # ВОРОТА КЛИЕНТСКОГО КОНТУРА (30.07): коммит задел файл, доезжающий до ЖИВОГО клиента → не
    # применяем, владельцу карточка. Смотрим ВЕСЬ дифф задачи (не только выбранное картой) и ДО
    # гейта: рестарт поднимает состояние диска ЦЕЛИКОМ — «частично выкатить» нельзя, а гонять
    # тесты ради рестарта, которого не будет, незачем. Ровно та же очерёдность, что у грязного дерева.
    _kinds = [k for k, f in (("userbot", ub_files), ("moderbot", mb_files)) if f]
    _held = (client_block_fn or _client_block)(_kinds, commit, changed, "авто-обновление после задачи")
    if _held:
        return (" | авто-обновление ОСТАНОВЛЕНО воротами клиентского контура (%s): боты остались на "
                "прежнем коде, владельцу отправлена карточка" % ", ".join(_held))
    # Селективный гейт (порт VPS): промежуточный шаг цепи / одиночка под флагом → только затронутые
    # тесты; финальный шаг → полный гейт (неубираем); сбой селектора → полный (fail-safe). Флаги
    # off (дефолт) → mode=off → прежний путь _affected_test_modules байт-в-байт.
    notes = []
    for kind, label, files in (("userbot", "userbot", ub_files), ("moderbot", "модербот", mb_files)):
        if not files:
            continue
        # ВОРОТА (28.07): грязное дерево по файлам ЭТОГО бота → рестарта нет. Проверяем ДО гейта:
        # гонять тесты ради рестарта, которого не будет, незачем.
        dirty = _dirty_block(kind, commit, "авто-обновление после задачи", label=label,
                             dirty_fn=dirty_fn)
        if dirty:
            notes.append(f"{label}: рестарт ОТМЕНЁН — грязное дерево: {', '.join(dirty)}")
            continue
        run_mods, gmode = gate_selective.decide(
            files, REPO, is_step=is_step, step_i=step_i, step_n=step_n,
            step_selective=_gate_step_selective_on(), single_selective=_gate_single_selective_on())
        mods = _affected_test_modules(files) if gmode == gate_selective.MODE_OFF else run_mods
        if gmode != gate_selective.MODE_OFF:
            log.info("авто-обновление %s: гейт mode=%s, модулей=%s", kind, gmode, len(mods or []))
        ok, gmsg = (gate_fn or _gate_test_modules)(mods)
        if not ok:
            log.error("авто-обновление %s: гейт КРАСНЫЙ (%s) — рестарт отложен", kind, gmsg)
            notes.append(f"{label}: код запушен, рестарт отложен: тесты красные ({_tail(gmsg, 200)})")
            _notify(f"⚠️ Оркестратор: {label} НЕ перезапущен — тесты красные (код запушен, применится после фикса)")
            continue
        rok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
        if rok and pids:
            _stamp_apply_restart(kind)          # реестр анти-флапа: реконсиляция self-update не дёрнет повторно
            log.info("авто-обновление %s: обновлён до %s, PID %s", kind, commit, pids)
            notes.append(f"{label} обновлён до {commit}, PID {', '.join(map(str, pids))}")
        elif rok:                               # рестарт не требовался (напр. модербот без токена)
            log.info("авто-обновление %s: %s", kind, detail)
            notes.append(f"{label}: {detail}")
        else:
            log.error("авто-обновление %s: рестарт НЕ УДАЛСЯ — %s", kind, detail)
            notes.append(f"{label}: код запушен, рестарт НЕ удался — {_tail(detail, 200)}")
            _notify(f"⚠️ Оркестратор: {label} — рестарт не удался после обновления: {_tail(detail, 200)}")
    return (" | авто-обновление: " + " ; ".join(notes)) if notes else ""


# ------------------- анти-флап авто-рестартов + реконсиляция детей self-update ----
# Лёгкий реестр «когда последний раз авто-рестартили процесс» (в памяти демона). Его СТАМПИТ и
# maybe_update_bots (рестарт после дев-задачи), и командой-рычагом _exec_command, и реконсиляция
# детей после self-update; реконсиляция ЧИТАЕТ его и ПОДАВЛЯЕТ рестарт, если процесс дёргали недавно
# (< APPLY_COOLDOWN_SEC). Так авто-применение кода не воюет с только что случившимся рестартом
# (уважение анти-флапа — требование задачи). Контур-вотчдог держит СВОЙ отдельный анти-флап
# (_client_watch_state) — его забор не трогаем.
APPLY_COOLDOWN_SEC = int(os.getenv("PC_APPLY_COOLDOWN_SEC", "120") or "120")
_apply_restart_at = {}     # name -> time.time() последнего авто-рестарта (userbot/moderbot)


def _stamp_apply_restart(name, now=None, state=None):
    (state if state is not None else _apply_restart_at)[name] = time.time() if now is None else now


def _apply_antiflap(name, now, cooldown, state):
    """True → рестарт подавить (этот процесс уже авто-рестартили меньше cooldown назад)."""
    last = state.get(name)
    return last is not None and (now - last) < cooldown


def _diff_names(old_commit, new_commit):
    """Файлы, изменённые между двумя коммитами (old..new). git молчит/ошибка/нет диффа → []."""
    if not old_commit or not new_commit or old_commit == new_commit:
        return []
    out = _git_out(["diff", "--name-only", f"{old_commit}..{new_commit}"])
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]


def _selfupdate_restart_children(old_commit, new_commit, diff_fn=None, restart_fn=None,
                                 now=None, cooldown=None, state=None, dirty_fn=None,
                                 client_block_fn=None):
    """После УСПЕШНОГО self-update демона: рестарт затронутых детей по ЯВНОЙ карте на основе диффа
    old..new. → строка-итог для лога/cowork ('' если никого не трогали). Правила:
      • userbot/moderbot → штатный рестарт механикой вотчдога (_restart_via_pc_agent), с уважением
        анти-флапа (недавно рестартили → пропуск);
      • pc_agent → НЕ трогаем чужими руками, только пометка «ждёт ручного рестарта»;
      • дифф пуст / тронуты только не-код-файлы (README/*.md/тесты) → никого не рестартим.
    На каждое применение — NOTE в cowork «авто-применил <коммит>: рестарт <кто>». Всё внешнее
    (дифф/рестарт/время/реестр) инъектируется — в тестах боевое не дёргаем."""
    if _stopped():
        return ""
    now = time.time() if now is None else now
    cooldown = APPLY_COOLDOWN_SEC if cooldown is None else cooldown
    state = _apply_restart_at if state is None else state
    changed = (diff_fn or _diff_names)(old_commit, new_commit)
    if not changed:
        return ""
    procs = {}          # name -> [files]
    for p in changed:
        for name in _procs_for_file(p):
            procs.setdefault(name, []).append(p)
    if not procs:
        return ""                                  # тронуты только не-код-файлы — никого не рестартим
    notes = []
    if "pc_agent" in procs:                        # агент себя чужими руками не рестартует — только пометка
        procs.pop("pc_agent")
        msg = ("pc_agent изменён — ЖДЁТ РУЧНОГО рестарта (Планировщик/сам подхватит), "
               "чужими руками не трогаю")
        log.info("self-update дети: %s", msg)
        _cowork(f"авто-применил {new_commit}: {msg}")
        _notify(f"ℹ️ Оркестратор: {msg} (self-update {new_commit})")
        notes.append(msg)
    # ВОРОТА КЛИЕНТСКОГО КОНТУРА (30.07): self-update демона имеет право обновить СЕБЯ, но не имеет
    # права молча выкатить клиентскую правку, попавшую в тот же диапазон коммитов, — ровно так 28.07
    # старый suggest.py уехал «прицепом» к чужому self-update. Держим только ДЕТЕЙ-ботов: сам демон
    # уже обновился и это внутренний контур.
    _bots = [k for k in ("userbot", "moderbot") if k in procs]
    if _bots:
        _held = (client_block_fn or _client_block)(_bots, new_commit, changed,
                                                   "реконсиляция детей после self-update")
        if _held:
            for _k in _bots:
                procs.pop(_k, None)
            notes.append("применение к %s ОСТАНОВЛЕНО воротами клиентского контура (%s) — "
                         "жду «да» владельца" % (", ".join(_bots), ", ".join(_held)))
    for kind, label in (("userbot", "userbot"), ("moderbot", "модербот")):
        if kind not in procs:
            continue
        if _apply_antiflap(kind, now, cooldown, state):
            log.info("self-update дети: %s недавно рестартили — анти-флап, рестарт пропущен", kind)
            notes.append(f"{label}: анти-флап (недавно рестартили) — рестарт пропущен")
            continue
        # ВОРОТА (28.07): именно здесь 28.07 в бой уехал незакоммиченный код. Строка «авто-применил
        # <коммит>» ниже имеет право появиться ТОЛЬКО когда уехал ИМЕННО коммит; грязно — вместо неё
        # честная строка отказа с поимённым списком (её пишет сам _dirty_block).
        dirty = _dirty_block(kind, new_commit, "self-update детей", label=label, dirty_fn=dirty_fn)
        if dirty:
            notes.append(f"{label}: рестарт ОТМЕНЁН — грязное дерево: {', '.join(dirty)}")
            continue
        try:
            ok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
        except Exception as e:
            ok, pids, detail = False, [], f"исключение рестарта: {e}"
        _stamp_apply_restart(kind, now, state)
        if ok and pids:
            log.info("self-update дети: %s рестартнут до %s, PID %s", kind, new_commit, pids)
            _cowork(f"авто-применил {new_commit}: рестарт {kind} (PID {', '.join(map(str, pids))})")
            notes.append(f"{label} рестартнут (PID {', '.join(map(str, pids))})")
        elif ok:                                    # рестарт не требовался (напр. модербот без токена)
            log.info("self-update дети: %s — %s", kind, detail)
            _cowork(f"авто-применил {new_commit}: рестарт {kind} — {_tail(detail, 160)}")
            notes.append(f"{label}: {detail}")
        else:
            log.error("self-update дети: рестарт %s НЕ УДАЛСЯ — %s", kind, detail)
            _cowork(f"авто-применил {new_commit}: рестарт {kind} НЕ удался — {_tail(detail, 160)}")
            _notify(f"⚠️ Оркестратор: {label} не рестартнут после self-update {new_commit}: {_tail(detail, 160)}")
            notes.append(f"{label}: рестарт НЕ удался — {_tail(detail, 160)}")
    return " ; ".join(notes)


# ------------------- периодический git fetch + FAST-FORWARD-ONLY pull ---------
# Родитель #221: демон сам подтягивает свежий origin/main, чтобы штатный путь тика (self-update
# с гейтом + реконсиляция детей по диффу) применил чужие/удалённые коммиты БЕЗ ручного git pull.
# СТРОГИЕ условия pull: копия ЧИСТАЯ (нет незакоммиченных правок), мы на целевой ветке, локальный
# HEAD строго ПОЗАДИ origin/<branch> И является её предком (fast-forward возможен). Любое иное
# (грязно / detached / расхождение с локальными коммитами / не-ff) → ПРОПУСК + NOTE в cowork_log.
# НИКОГДА merge/rebase: только ff (перематываем указатель, локальную историю не переписываем и не
# смешиваем). После ff новый HEAD в ТОМ ЖЕ тике подхватят maybe_reconcile_children / maybe_self_update.
GIT_PULL_SEC = int(os.getenv("PC_GIT_PULL_SEC", "300") or "300")     # авто-фетч не чаще раза в N сек
GIT_PULL_BRANCH = os.getenv("PC_GIT_PULL_BRANCH", "main")            # тянем ff ТОЛЬКО эту ветку
_git_pull_last_run = 0.0                                             # троттлинг тела авто-фетча

# ── строки состояния авто-фетча пишем при СМЕНЕ состояния, а НЕ каждый цикл ──
# Живой замер (лог демона за сутки 28→29.07): 47 строк «рабочая копия грязная» = 7 заходов грязи
# по 5-минутному циклу. Одинаковая строка каждые GIT_PULL_SEC — главный двигатель роста журнала и
# лишняя нагрузка на мост (каждая строка = отдельный вызов cowork_log_append → запись в Brain).
# Правило (как у _dirty_block / _DIRTY_WARNED, но ПЕРЕЖИВАЕТ рестарт демона — признак на ДИСКЕ):
#   стало грязным — ОДНА строка с поимённым списком; сменился состав грязного — новая строка;
#   стало чистым — ОДНА строка; между ними ТИШИНА. Перезапуск демона в том же состоянии строку
#   НЕ родит (источник истины — файл, а не память процесса). Файл gitignored (pc_orchestrator.*.json)
#   — сам он грязью для авто-фетча не станет. То же правило накрывает ВСЕ периодические ветки тика
#   (грязно / fetch не удался / не-ff / pull не удался); ff — событие, пишется всегда.
AUTOFETCH_STATE_FILE = os.path.join(REPO, "pc_orchestrator.autofetch.json")
_AUTOFETCH_CLEAN_MSG = "авто-фетч: рабочее дерево снова чистое — пропуск снят, фетч/pull штатны"


def _autofetch_state_read(path=None):
    """Признак состояния авто-фетча → dict ({} при отсутствии/бое: «не знаю» = чистый лист)."""
    try:
        with open(path or AUTOFETCH_STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _autofetch_state_write(d, path=None):
    try:
        with open(path or AUTOFETCH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:                            # noqa: BLE001 — дедуп не роняет тик
        log.warning("state авто-фетча не записался (%s) — дедуп журнала деградирует, не критично", e)


def _autofetch_note(cond, msg, *, quiet_from_fresh=False, path=None, cowork=None):
    """Строку о состоянии авто-фетча в журнал ТОЛЬКО при СМЕНЕ состояния.
    cond — сигнатура состояния ('ok' | 'dirty:<файлы>' | 'fetch_failed' | 'non_ff' | 'pull_failed').
    msg  — что писать при ВХОДЕ в состояние ('' = состояние фиксируем молча, без строки: так делает
           ff — свою строку он уже написал сам).
    quiet_from_fresh — не писать msg, если ПРЕЖНЕГО состояния не было: первый чистый тик демона не
           должен объявлять «снова чистое» на ровном месте.
    Признак фиксируем ДО журнала (restart-proof, как _dirty_block). → True, если состояние сменилось."""
    d = _autofetch_state_read(path)
    prev = d.get("cond")
    if prev == cond:
        return False                                  # то же состояние — тишина
    d["cond"] = cond
    _autofetch_state_write(d, path)
    if msg and not (quiet_from_fresh and prev is None):
        (cowork or _cowork)(msg)
    return True


def git_ff_pull_tick(call_fn=None):
    """Тело авто-фетча (без троттлинга — троттлит maybe_git_ff_pull). git-вызовы инъектируются для тестов.
    → строка-итог для лога/NOTE ('' = нечего делать / тихий пропуск: git недоступен, detached, уже
    актуально, рубильник). Никогда не merge/rebase — только fast-forward."""
    call = call_fn or _git_call
    if _stopped():
        return ""
    branch = GIT_PULL_BRANCH
    # 1. на целевой ветке? (detached HEAD / другая ветка — не наш случай, молча пропускаем)
    r = call(["rev-parse", "--abbrev-ref", "HEAD"])
    if not r or r[0] != 0 or r[1] != branch:
        return ""
    # 2. чистое дерево? «Чистота» = нет изменений TRACKED-файлов. Строки '?? ' (untracked) НЕ
    # считаем грязью: в репо ПОСТОЯННО живут untracked pc_orchestrator.heartbeat /
    # fetch_delivery.py — по старому условию (любой непустой porcelain) авто-фетч был
    # заблокирован ВЕЧНО + NOTE-спам каждые GIT_PULL_SEC. ff-pull untracked не трогает
    # (конфликт с приходящим одноимённым tracked-файлом отвергнет сам git → ветка «pull не удался»).
    r = call(["status", "--porcelain"])
    if not r or r[0] != 0:
        return ""                              # git недоступен — тихо
    tracked_dirty = [ln for ln in (r[1] or "").splitlines()
                     if ln.strip() and not ln.startswith("??")]
    if tracked_dirty:                           # есть незакоммиченные правки TRACKED-файлов
        # Поимённый список берём у _dirty_tracked (diff --name-only HEAD — чистые имена без колонок
        # porcelain, чей ведущий пробел .strip() съедает у первой строки, см. класс 28.07). Строку
        # шлём РОВНО при смене состава грязного: та же грязь = тишина, перезапуск демона тоже молчит.
        files = sorted(_dirty_tracked(call) or [])
        listed = ", ".join(files) if files else "(git не назвал файлы)"
        if _autofetch_note("dirty:" + "|".join(files),
                           f"авто-фетч: рабочая копия грязная — git pull пропущен, жду чистого дерева; "
                           f"файлы ({len(files)}): {listed}"):
            log.info("авто-фетч: рабочая копия грязная — git pull пропущен; файлы: %s", listed)
        return "грязно — пропуск"
    # 3. fetch origin (сеть; сбой не критичен — повторим на следующем интервале)
    r = call(["fetch", "origin"])
    if not r or r[0] != 0:
        detail = _tail(r[2], 160) if r else "git недоступен"
        if _autofetch_note("fetch_failed",
                           f"авто-фетч: git fetch origin не удался — {detail} (пропуск, повтор позже)"):
            log.warning("авто-фетч: git fetch origin не удался — %s", detail)
        return "fetch не удался"
    # 4. локальный HEAD vs origin/<branch>
    loc = call(["rev-parse", "HEAD"])
    rem = call(["rev-parse", f"origin/{branch}"])
    if not loc or loc[0] != 0 or not rem or rem[0] != 0:
        return ""                              # нет отслеживаемой ветки/ref — тихо
    if loc[1] == rem[1]:
        _autofetch_note("ok", _AUTOFETCH_CLEAN_MSG, quiet_from_fresh=True)  # снимет прежний «пропуск»
        return ""                              # уже актуально — нечего тянуть
    # 5. ff возможен ТОЛЬКО если локальный HEAD — предок origin/<branch> (код 0 = предок)
    anc = call(["merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"])
    if not anc:
        return ""                              # git недоступен — тихо
    if anc[0] != 0:
        # HEAD НЕ предок origin → ff НЕВОЗМОЖЕН. Различаем два случая по обратному merge-base:
        #   (а) origin/<branch> — предок HEAD (код 0) → мы строго ВПЕРЕДИ: локальные коммиты ещё не
        #       отправлены, fetch применять НЕЧЕГО. Норма после локального коммита до push → ТИХИЙ
        #       пропуск (без NOTE: спамить cowork_log каждый интервал о штатном состоянии незачем).
        #   (б) ни один не предок другого → истинное РАСХОЖДЕНИЕ (не-ff) → пропуск + NOTE, нужен разбор.
        # В обоих случаях НИКОГДА не merge/rebase.
        fwd = call(["merge-base", "--is-ancestor", f"origin/{branch}", "HEAD"])
        if fwd and fwd[0] == 0:
            # чистое дерево, просто впереди origin (штатно до push) → состояние ok, восстановление
            # объявляем той же строкой «снова чистое», если раньше был пропуск; иначе тишина.
            if _autofetch_note("ok", _AUTOFETCH_CLEAN_MSG, quiet_from_fresh=True):
                log.info("авто-фетч: локальный %s впереди origin/%s — тянуть нечего (пропуск)", branch, branch)
            return "впереди origin — пропуск"
        if _autofetch_note("non_ff",
                           f"авто-фетч: локальный {branch} разошёлся с origin (не-ff) — git pull пропущен, нужен разбор"):
            log.warning("авто-фетч: локальный %s разошёлся с origin/%s (не-ff) — git pull пропущен", branch, branch)
        return "не-ff (расхождение) — пропуск"
    # 6. чистое дерево + строго позади + ff возможен → тянем fast-forward-only
    r = call(["pull", "--ff-only", "origin", branch])
    if not r or r[0] != 0:
        detail = _tail(r[2], 160) if r else "git недоступен"
        if _autofetch_note("pull_failed", f"авто-фетч: git pull --ff-only не удался — {detail}"):
            log.error("авто-фетч: git pull --ff-only не удался — %s", detail)
        return "pull не удался"
    nh = call(["rev-parse", "--short", "HEAD"])
    short = nh[1] if nh and nh[0] == 0 else "?"
    log.info("авто-фетч: fast-forward %s → %s (origin/%s) — штатный путь применит self-update/детей", branch, short, branch)
    _cowork(f"авто-фетч: fast-forward {branch} → {short} (штатный путь тика применит self-update/детей)")
    _autofetch_note("ok", "", quiet_from_fresh=True)   # состояние → ok молча: ff-строка уже сказала о движении
    return f"ff → {short}"


def maybe_git_ff_pull(now=None):
    """Троттлинг авто-фетча: тело не чаще GIT_PULL_SEC. → строка|None (None = рано)."""
    global _git_pull_last_run
    now = time.time() if now is None else now
    if now - _git_pull_last_run < GIT_PULL_SEC:
        return None
    _git_pull_last_run = now
    return git_ff_pull_tick()


# ------------------- реконсиляция детей на ЛЮБОЙ новый коммит (класс-фикс) ----
# РАЗБОР c6d8a30: свежий код ДЕТЕЙ (suggest/pricing/booking/moderation_*) применялся к живым
# процессам ТОЛЬКО когда (а) правку принёс дев-таск самого демона (maybe_update_bots по
# head_before..HEAD), ЛИБО (б) вместе с ней сменился блоб pc_orchestrator.py (self-update →
# _selfupdate_restart_children). Коммит, тронувший ТОЛЬКО файлы детей и пришедший ВНЕ дев-таска
# (cowork/ручной коммит/git pull) — оставлял детей на СТАРОМ коде до следующей смены
# pc_orchestrator.py. Так c6d8a30 (только suggest.py+тест) не подхватился, а f89da43 (менял
# pc_orchestrator.py) — подхватился self-update'ом. ФИКС: каждый цикл демон сверяет HEAD с
# последним ПРИМЕНЁННЫМ к детям коммитом; HEAD ушёл вперёд и в диффе есть файлы детей → рестарт
# затронутых (ТА ЖЕ явная карта _FILE_PROCESS_RULES + гейт затронутых тестов + анти-флап +
# рубильник — гейты НЕ ослабляем). Реагирует на изменённые файлы детей в диффе ДАЖЕ когда сам
# pc_orchestrator.py не менялся. Красный гейт → метку НЕ двигаем (диапазон со стале-кодом доживёт
# до фикс-коммита), но HEAD запоминаем, чтобы не гонять гейт каждый цикл (как _SU_REJECTED_BLOB).
CHILD_RECONCILE_SEC = int(os.getenv("PC_CHILD_RECONCILE_SEC", "60") or "60")
_last_child_commit = None         # коммит, чьи изменения детей уже применены (init = старт демона)
_child_reconcile_rejected = None  # HEAD с КРАСНЫМ гейтом детей — не гоняем гейт каждый цикл
_child_reconcile_last_run = 0.0   # троттлинг тела реконсиляции


def _full_head():
    """Полный хеш HEAD (для метки/диффа реконсиляции). git молчит → None."""
    return _git_out(["rev-parse", "HEAD"])


def reconcile_children_tick(head_fn=None, diff_fn=None, gate_fn=None, restart_fn=None,
                            now=None, cooldown=None, state=None, client_block_fn=None):
    """Тело реконсиляции детей на новый коммит (без троттлинга — троттлит maybe_reconcile_children).
    → строка-итог для лога ('' если нечего/рубильник). Всё внешнее инъектируется для тестов.
    Метку/rejected хранит в модульных глобалах (переживают тики; рестарт демона сбрасывает —
    первый прогон просто примет текущий HEAD как применённый, т.к. дети стартовали с ним)."""
    global _last_child_commit, _child_reconcile_rejected
    if _stopped():
        return ""
    head = (head_fn or _full_head)()
    if not head:
        return ""
    if _last_child_commit is None:        # первый прогон: дети стартовали с текущим HEAD → он уже «применён»
        _last_child_commit = head
        return ""
    if head == _last_child_commit or head == _child_reconcile_rejected:
        return ""                         # нет нового коммита ИЛИ этот HEAD уже провалил гейт — ждём новый
    changed = (diff_fn or _diff_names)(_last_child_commit, head)
    ub_files, mb_files = _classify_changed(changed)
    pc_agent_hit = any("pc_agent" in _procs_for_file(p) for p in changed)   # pc_agent.py в диффе → та же ручная карта, что в self-update
    if not (ub_files or mb_files or pc_agent_hit):
        _last_child_commit = head         # тронуты только не-код-файлы детей (pc_orchestrator/доки/тесты) — двигаем метку
        return ""
    now = time.time() if now is None else now
    cooldown = APPLY_COOLDOWN_SEC if cooldown is None else cooldown
    state = _apply_restart_at if state is None else state
    short = head[:9]
    # ВОРОТА КЛИЕНТСКОГО КОНТУРА (30.07) — ГЛАВНАЯ дыра класса: ИМЕННО этот тик 30.07 в 01:53
    # рестартнул userbot на коммит цепи ревизора 4528917 без чьего-либо решения. Держим ВЕСЬ тик и
    # метку _last_child_commit НЕ двигаем: придёт «да» — следующий тик применит ЭТОТ ЖЕ коммит.
    # _child_reconcile_rejected здесь СПЕЦИАЛЬНО не ставим: он глушит повторную проверку этого HEAD
    # насовсем, а нам ровно наоборот — ждать решения владельца и применить, когда оно будет.
    # Пометку про pc_agent тоже придержим: коммит удержан целиком, полуприменения не бывает.
    if ub_files or mb_files:
        _kinds = [k for k, f in (("userbot", ub_files), ("moderbot", mb_files)) if f]
        _held = (client_block_fn or _client_block)(_kinds, short, changed,
                                                   "реконсиляция детей на новый коммит")
        if _held:
            return ("ворота клиентского контура: применение %s ОСТАНОВЛЕНО, боты на прежнем коде "
                    "(%s)" % (short, ", ".join(_held)))
    notes, gate_red = [], False
    if pc_agent_hit:                      # агент себя чужими руками не рестартует — только пометка (ручная карта, как в _selfupdate_restart_children)
        msg = ("pc_agent изменён — ЖДЁТ РУЧНОГО рестарта (Планировщик/сам подхватит), "
               "чужими руками не трогаю")
        log.info("реконсиляция детей: %s", msg)
        _cowork(f"авто-применил {short}: {msg}")
        _notify(f"ℹ️ Оркестратор: {msg} (реконсиляция {short})")
        notes.append(msg)
    for kind, label, files in (("userbot", "userbot", ub_files), ("moderbot", "модербот", mb_files)):
        if not files:
            continue
        mods = _affected_test_modules(files)
        ok, gmsg = (gate_fn or _gate_test_modules)(mods)
        if not ok:
            gate_red = True
            log.error("реконсиляция детей %s: гейт КРАСНЫЙ (%s) — рестарт отложен", kind, _tail(gmsg, 200))
            _notify(f"⚠️ Оркестратор: {label} НЕ перезапущен — тесты красные (код запушен, применится после фикса)")
            notes.append(f"{label}: тесты красные — рестарт отложен")
            continue
        if _apply_antiflap(kind, now, cooldown, state):   # уже рестартнули (дев-таск/self-update/прошлый тик) → код применён
            log.info("реконсиляция детей %s: недавно рестартили — анти-флап, пропуск", kind)
            notes.append(f"{label}: анти-флап (недавно рестартили) — пропуск")
            continue
        try:
            rok, pids, detail = (restart_fn or _restart_via_pc_agent)(kind)
        except Exception as e:
            rok, pids, detail = False, [], f"исключение рестарта: {e}"
        _stamp_apply_restart(kind, now, state)
        if rok and pids:
            log.info("реконсиляция детей: %s рестартнут до %s, PID %s", kind, short, pids)
            _cowork(f"авто-применил {short}: рестарт {kind} (PID {', '.join(map(str, pids))})")
            notes.append(f"{label} рестартнут (PID {', '.join(map(str, pids))})")
        elif rok:                                          # рестарт не требовался (напр. модербот без токена)
            log.info("реконсиляция детей: %s — %s", kind, detail)
            _cowork(f"авто-применил {short}: рестарт {kind} — {_tail(detail, 160)}")
            notes.append(f"{label}: {detail}")
        else:
            log.error("реконсиляция детей: рестарт %s НЕ УДАЛСЯ — %s", kind, detail)
            _cowork(f"авто-применил {short}: рестарт {kind} НЕ удался — {_tail(detail, 160)}")
            _notify(f"⚠️ Оркестратор: {label} не рестартнут (реконсиляция {short}): {_tail(detail, 160)}")
            notes.append(f"{label}: рестарт НЕ удался")
    if gate_red:
        _child_reconcile_rejected = head   # метку НЕ двигаем: стале-код детей доживёт до фикс-коммита
    else:
        _last_child_commit = head
        _child_reconcile_rejected = None
    return " ; ".join(notes)


def maybe_reconcile_children(now=None):
    """Троттлинг реконсиляции детей: тело не чаще CHILD_RECONCILE_SEC. → строка|None (None = рано)."""
    global _child_reconcile_last_run
    now = time.time() if now is None else now
    if now - _child_reconcile_last_run < CHILD_RECONCILE_SEC:
        return None
    _child_reconcile_last_run = now
    return reconcile_children_tick()


# ------------------- контур-вотчдог клиентского контура (часть 3) -------------
# Демон каждые CLIENT_WATCH_SEC (5 мин) проверяет живость pc_agent/userbot/moderation_bot по
# PID (CIM-поиск процесса = источник правды) + свежести их логов (диагностика). Мёртвого
# поднимает ШТАТНО: pc_agent — через Планировщик (schtasks /Run), userbot/moderation_bot — той
# же механикой pc_agent (venv-spawn, CIM-guard от дубля). На каждый подъём — NOTE в cowork_log.
# Анти-флаппинг: не чаще 1 подъёма на процесс за CLIENT_COOLDOWN_SEC (15 мин); CLIENT_MAX_DEATHS
# (3) смертей подряд → СТОП попыток по этому процессу + громкий NOTE «нужен разбор». Живой снова
# → счётчик смертей сброшен. moderation_bot без MODERBOT_TOKEN — не «мёртв», а штатно не поднят
# (reply-режим) → пропускаем. Состояние в памяти демона (переживает тики; рестарт демона его
# сбрасывает — не страшно). Всё внешнее (finder/raiser/now/state) инъектируется для тестов.

_client_watch_state = {}      # name -> {"last_raise": float, "deaths": int, "halted": bool}
                              # спец-ключ "__blind__" -> счётчик подряд СЛЕПЫХ циклов (finder не смог)
_client_watch_last_run = 0.0  # монотонная метка последнего прогона контура (троттлинг 5 мин)
_client_grace_until = 0.0     # wall-clock: до этого момента вердикты «мёртв»/рестарты подавлены (ПК проснулся)
_loop_prev_wall = None        # wall-clock старта прошлой итерации главного цикла (детект скачка = сна)


def _find_pids_by_script(script_name):
    """PID python-процессов, исполняющих <script_name> (фиксированный CIM-запрос, read-only).
    ТРИ исхода — ключ фикса #171 («не смог проверить» ≠ «мёртв»):
      • список PID — CIM отработал, процесс(ы) найдены;
      • []         — CIM отработал, процессов НЕТ (честная пустота → повод к проверке смерти);
      • None       — CIM упал/таймаут/скрытый сбой (просыпающийся/тормозящий ПК) — исход НЕИЗВЕСТЕН.
    Раньше ошибка глушилась в [] → вотчдог принимал таймаут за смерть → лишний рестарт → дубль.
    script_name — литерал из спецификации контура (не пользовательский ввод)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
          "| Where-Object { $_.CommandLine -like '*" + script_name + "*' } "
          "| Select-Object -ExpandProperty ProcessId")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=20, creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("контур-вотчдог: CIM-поиск %s не удался (%s) — исход НЕИЗВЕСТЕН, НЕ считаем мёртвым", script_name, e)
        return None
    pids = [int(x) for x in (p.stdout or "").split() if x.strip().isdigit()]
    if pids:
        return pids
    # Пусто: отличаем ЧЕСТНУЮ пустоту (rc=0, тихий stderr) от СКРЫТОГО сбоя CIM (rc!=0 / ошибка в stderr).
    if p.returncode != 0 or (p.stderr or "").strip():
        log.warning("контур-вотчдог: CIM-поиск %s пуст, но rc=%s / stderr=%r — исход НЕИЗВЕСТЕН, НЕ считаем мёртвым",
                    script_name, p.returncode, _tail((p.stderr or "").strip(), 120))
        return None
    return []


def _raise_pc_agent():
    """Поднять pc_agent через Планировщик (у него отдельная задача schtasks). → (ok, detail)."""
    rc, out = _schtasks_run("pc_agent")
    return rc == 0, f"schtasks /Run /TN pc_agent rc={rc}: {_tail(out, 160)}"


def _raise_client_bot(kind):
    """Поднять userbot/moderation_bot ТОЙ ЖЕ механикой pc_agent (venv-spawn + CIM-guard). → (ok, detail).
    kind ∈ 'userbot'|'moderbot'. start() идемпотентен: если процесс уже есть — второй не создаст."""
    try:
        import pc_agent   # lazy: не тянем telegram в общий импорт демона
    except Exception as e:
        return False, f"импорт pc_agent не удался: {e}"
    try:
        proc = pc_agent.UserbotProcess() if kind == "userbot" else pc_agent.ModerbotProcess()
        return True, proc.start()
    except Exception as e:
        return False, f"ошибка старта {kind}: {e}"


def _client_watch_specs():
    """Спецификации процессов клиентского контура. skip() → «не применимо» (не считаем мёртвым)."""
    return [
        {"name": "pc_agent",
         "finder": lambda: _find_pids_by_script("pc_agent.py"),
         "raiser": _raise_pc_agent,
         "logfile": os.path.join(REPO, "pc_agent.log"),
         "skip": lambda: False},
        {"name": "userbot",
         "finder": lambda: _find_pids_by_script("userbot_listen.py"),
         "raiser": lambda: _raise_client_bot("userbot"),
         "logfile": os.path.join(REPO, "userbot.log"),
         "skip": lambda: False},
        {"name": "moderation_bot",
         "finder": lambda: _find_pids_by_script("moderation_bot.py"),
         "raiser": lambda: _raise_client_bot("moderbot"),
         "logfile": os.path.join(REPO, "moderation_bot.log"),
         # без токена модербот НЕ должен работать (reply-режим) — это не смерть, а штатный простой
         "skip": lambda: not os.getenv("MODERBOT_TOKEN", "").strip()},
    ]


def _log_age_sec(path, now):
    """Возраст (сек) последней записи в лог. now — time.time(). None — файла нет/ошибка."""
    try:
        return max(0.0, now - os.path.getmtime(path))
    except Exception:
        return None


def _client_watch_step(name, alive, now, state, cooldown, max_deaths):
    """Чистое решение по ОДНОМУ процессу. → (action, new_state_entry). Побочек нет — подъём делает
    вызывающий. action ∈ 'alive'|'raise'|'cooldown'|'halted'|'halt_now'."""
    st = dict(state.get(name) or {"last_raise": 0.0, "deaths": 0, "halted": False})
    if alive:
        return "alive", {"last_raise": st["last_raise"], "deaths": 0, "halted": False}
    if st["halted"]:
        return "halted", st                        # уже сдались (громкий NOTE был при переходе)
    if now - st["last_raise"] < cooldown:
        return "cooldown", st                       # анти-флап: рано поднимать снова
    st["deaths"] += 1
    st["last_raise"] = now
    if st["deaths"] >= max_deaths:
        st["halted"] = True
        return "halt_now", st                       # смерть №max_deaths подряд → стоп, не поднимаем
    return "raise", st


def _persist_client_watch(state, now, path=None):
    """Снимок НАДЗОРА контур-вотчдога на диск — читает status pc_agent (ОТДЕЛЬНЫЙ процесс, RAM
    демона ему недоступна). Пишем только реальных детей (dict-записи); спец-счётчики (__blind__ —
    int) пропускаем. Атомарно (tmp+os.replace). Сбой записи НЕ должен ронять тик — тихий warning.
    Схема: {ts, cooldown, max_deaths, children:{name:{deaths,halted,last_raise}}}."""
    path = CLIENT_WATCH_FILE if path is None else path
    children = {}
    for name, ent in state.items():
        if not isinstance(ent, dict):
            continue                                    # __blind__ и прочие спец-счётчики — не дети
        children[name] = {"deaths": int(ent.get("deaths") or 0),
                          "halted": bool(ent.get("halted")),
                          "last_raise": float(ent.get("last_raise") or 0.0)}
    blob = {"ts": float(now), "cooldown": CLIENT_COOLDOWN_SEC,
            "max_deaths": CLIENT_MAX_DEATHS, "children": children}
    try:
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        log.warning("контур-вотчдог: снимок надзора не записан (%s): %s", path, e)


def client_watchdog_tick(now=None, specs=None, state=None, cooldown=None, max_deaths=None,
                         grace_until=None, log_stale=None, blind_alarm=None):
    """Один прогон контур-вотчдога. → dict name->action (для тестов/лога). Побочки: raiser()+NOTE.
    Уважает рубильник pc_orchestrator.stop (клиентский контур при намеренной остановке не трогаем).
    Фикс #171: finder РАЗЛИЧАЕТ три исхода (см. _find_pids_by_script); «мёртв» требует ТРЁХ условий
    (finder успешен + процесса нет + лог протух); grace после пробуждения ПК; слепой-счётчик → алярм."""
    now = time.time() if now is None else now
    specs = _client_watch_specs() if specs is None else specs
    state = _client_watch_state if state is None else state
    cooldown = CLIENT_COOLDOWN_SEC if cooldown is None else cooldown
    max_deaths = CLIENT_MAX_DEATHS if max_deaths is None else max_deaths
    grace_until = _client_grace_until if grace_until is None else grace_until
    log_stale = CLIENT_LOG_STALE if log_stale is None else log_stale
    blind_alarm = CLIENT_BLIND_ALARM if blind_alarm is None else blind_alarm
    if _stopped():
        return {"_": "stopped"}
    # (2) GRACE после пробуждения ПК: в окне WAKE_GRACE_SEC никаких вердиктов «мёртв»/рестартов —
    # CIM на только что проснувшемся ПК медленный, «пусто» здесь недостоверно. Только логируем.
    if grace_until and now < grace_until:
        log.info("контур-вотчдог: grace после пробуждения ПК (%sс осталось) — вердикты отложены",
                 int(grace_until - now))
        return {"_": "grace"}
    out = {}
    considered = 0        # сколько процессов реально проверяли (не skip)
    any_success = False   # хоть один finder дал достоверный ответ (OK+список или OK+пусто)
    for sp in specs:
        name = sp["name"]
        # skip (штатный простой, напр. модербот без токена) — это НЕ смерть, вне слепого-счётчика
        try:
            if sp.get("skip") and sp["skip"]():
                out[name] = "skip"
                state[name] = {"last_raise": 0.0, "deaths": 0, "halted": False}
                continue
        except Exception as e:
            log.warning("контур-вотчдог: skip-проверка %s упала: %s", name, e)
        considered += 1
        # (1) finder РАЗЛИЧАЕТ три исхода. None (или исключение) = «не смог проверить» → SKIP цикла
        # проверки ЭТОГО процесса, БЕЗ рестарта. Это НЕ смерть.
        try:
            pids = sp["finder"]()
        except Exception as e:
            log.warning("контур-вотчдог: finder %s упал (%s) — SKIP, без рестарта", name, e)
            pids = None
        if pids is None:
            out[name] = "check_failed"
            log.warning("контур-вотчдог: не смог проверить %s (finder слеп) — SKIP цикла, БЕЗ рестарта", name)
            continue
        any_success = True
        alive = bool(pids)
        if not alive:
            # (1) «мёртв» (повод к рестарту) = finder УСПЕШЕН И процесса нет И лог протух > порога.
            # Свежий лог = недавняя активность/возможная гонка CIM → рестарт ВЕТИРУЕМ (строже к рестарту).
            log_age = _log_age_sec(sp.get("logfile"), now)
            if log_age is not None and log_age <= log_stale:
                out[name] = "fresh_log"
                log.warning("контур-вотчдог: %s без PID, но лог свеж (%sс ≤ %sс) — смерть НЕ доказана, рестарт отложен",
                            name, int(log_age), log_stale)
                continue
        action, st = _client_watch_step(name, alive, now, state, cooldown, max_deaths)
        state[name] = st
        out[name] = action
        if action == "raise":
            try:
                ok, detail = sp["raiser"]()
            except Exception as e:
                ok, detail = False, f"raiser упал: {e}"
            log_age = _log_age_sec(sp.get("logfile"), now)
            log.warning("контур-вотчдог: %s МЁРТВ (смерть %s/%s, лог %s) → подъём ok=%s: %s",
                        name, st["deaths"], max_deaths,
                        (f"{int(log_age)}с назад" if log_age is not None else "нет"), ok, _tail(str(detail), 200))
            _cowork(f"вотчдог поднял {name} (смерть {st['deaths']}/{max_deaths}): {_tail(str(detail), 160)}")
            if not ok:
                _notify(f"⚠️ Оркестратор: контур-вотчдог не смог поднять {name}: {_tail(str(detail), 160)}")
        elif action == "halt_now":
            log.error("контур-вотчдог: %s умер %s раз подряд — СТОП попыток, нужен разбор", name, st["deaths"])
            _cowork(f"вотчдог: {name} умер {st['deaths']} раза подряд — СТОП, нужен разбор")
            # КРИТИЧЕСКИЙ инцидент (3-смерти-halt клиент-бота) → Инбокс 1160 (личка — фолбэк)
            _notify_critical(f"⚠️ Оркестратор: {name} умер {max_deaths} раза подряд — контур-вотчдог остановлен, нужен разбор")
    # (1) Слепой-счётчик: цикл СЛЕП, если процессы проверяли, но НИ ОДИН finder не смог ответить.
    # blind_alarm подряд слепых → NOTE «вотчдог слеп — глянь ПК» (алярм, НЕ рестарты). Любой успешный
    # finder обнуляет счётчик. (На спящем/тормозящем ПК все CIM-запросы таймаутят вместе.)
    if considered and not any_success:
        blind = int(state.get("__blind__", 0)) + 1
        state["__blind__"] = blind
        out["__blind__"] = blind
        if blind >= blind_alarm:
            log.error("контур-вотчдог: %s циклов подряд СЛЕП (CIM не отвечает) — нужен глаз на ПК", blind)
            _cowork(f"вотчдог слеп {blind} цикла подряд (CIM не отвечает) — глянь ПК (спит/тормозит?)")
            # КРИТИЧЕСКИЙ инцидент (halt-слепота вотчдога) → Инбокс 1160 (личка — фолбэк)
            _notify_critical(f"⚠️ Оркестратор: контур-вотчдог слеп {blind} цикла подряд — CIM не отвечает, глянь ПК")
            state["__blind__"] = 0   # сброс после алярма (не спамим каждый тик; ре-алярм ещё через blind_alarm)
    elif any_success:
        state["__blind__"] = 0
    return out


def maybe_client_watchdog(now=None):
    """Троттлинг контур-вотчдога: тело прогоняем не чаще CLIENT_WATCH_SEC. → dict|None (None = ещё рано)."""
    global _client_watch_last_run
    now = time.time() if now is None else now
    if now - _client_watch_last_run < CLIENT_WATCH_SEC:
        return None
    _client_watch_last_run = now
    out = client_watchdog_tick(now=now)
    _persist_client_watch(_client_watch_state, now)   # снимок надзора на диск → status pc_agent видит под вотчдогом/cooldown/halt
    return out


# ---------------- ДЕТЕКТОР НЕМОТЫ СЕССИЙ (session_watch, инцидент 29.07) ----------------
# Немая сессия — это «процесс жив, а работы нет»: транскрипт .jsonl не создан и ни одного
# tool_use. Живой случай: RC-сессия PID 21216 за 2 ч 53 мин выдала 529 heartbeat, 0 запросов к
# модели и 0 транскрипта — владелец узнал через два часа и только потому, что спросил. Ни
# контур-вотчдог (он спрашивает «процесс жив?»), ни ливнесс одиночек (он судит по статусу задачи
# в Bridge), ни сторож RC (у него лог свеж — heartbeat же пишется) такую сессию не видят.
# Хозяин детектора — ЭТОТ тик: демон и так «единственный надёжно выживающий процесс» (см.
# CLIENT_WATCH_SEC) и подхватывает новый код self-update'ом, без ручного рестарта чего-либо.
# Разбор и пороги — session_watch.py + docs/artifacts/2026-07-29-mute-session-21216-evidence.md.
SESSION_WATCH_SEC = int(os.getenv("PC_SESSION_WATCH_SEC", "60") or "60")
_session_watch_last_run = 0.0


def maybe_session_watch(now=None, ticker=None):
    """Троттлинг детектора немоты: тело прогоняем не чаще SESSION_WATCH_SEC.
    → список отсигналенных находок | None (None = ещё рано / детектор недоступен).
    НЕ НАВРЕДИ: любой срыв детектора глотаем — надзор не смеет уронить демона."""
    global _session_watch_last_run
    now = time.time() if now is None else now
    if now - _session_watch_last_run < SESSION_WATCH_SEC:
        return None
    _session_watch_last_run = now
    fn = ticker
    if fn is None:
        try:
            import session_watch
            fn = session_watch.tick
        except Exception as e:
            log.warning("детектор немоты недоступен (%s)", type(e).__name__)
            return None
    try:
        sent = fn(now=now)
    except Exception as e:
        log.warning("детектор немоты сорвался (%s) — тик пропущен", type(e).__name__)
        return None
    for f in sent or []:
        log.error("НЕМАЯ сессия: pid=%s sid=%s прожила %sс без транскрипта и tool_use — "
                  "карточка владельцу ушла", f.get("pid"), str(f.get("session_id"))[:8],
                  int(f.get("age") or 0))
    return sent


def _woke_from_sleep(now, prev, poll=None, margin=None):
    """(2) Детект пробуждения ПК: между соседними итерациями главного цикла wall-clock скакнул
    много больше интервала поллинга (ПК спал в S3/гибернации — состояния доступны, см. powercfg /a).
    Чистая/тестируемая. prev=None (первый виток) → пробуждением НЕ считаем."""
    poll = POLL_SEC if poll is None else poll
    margin = WAKE_JUMP_MARGIN if margin is None else margin
    return prev is not None and (now - prev) > (poll + margin)


def fmt_sleep(gap):
    """Длительность сна по-человечески: «8 ч 58 м» / «29 м 36 с» / «45 с». Чистая (голден)."""
    gap = max(0, int(gap))
    h, rem = divmod(gap, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%d ч %02d м" % (h, m)
    if m:
        return "%d м %02d с" % (m, s)
    return "%d с" % s


def _pc_backlog(getter=None):
    """Сколько одиночек lane=pc ЖДАЛО исполнения на момент пробуждения: new + approved.
    Read-only, очередь не мутируем. Мост недоступен → None, а НЕ 0: «ноль задач» и «не смог
    посчитать» — разные новости, и подменять вторую первой значит врать владельцу в карточке."""
    getter = bc.get_pending if getter is None else getter
    total = 0
    for st in ("new", "approved"):
        r = getter(st)
        if not r.get("ok"):
            return None
        total += len([it for it in r.get("items", []) if _lane_ok(it)])
    return total


def sleep_alarm_text(gap, backlog, threshold):
    """Текст карточки «ПК спал» — чистая функция, на неё положен голден."""
    return (
        "🛌 ПК СПАЛ %s — весь контур стоял\n"
        "скачок wall-clock: %sс (порог %sс — длинной задачей не объяснить)\n"
        "задач lane=pc накопилось в очереди: %s (new+approved)\n"
        "клиентский бот всё это время был НЕДОСТУПЕН"
        % (fmt_sleep(gap), int(gap), int(threshold),
           "не смог посчитать (мост недоступен)" if backlog is None else backlog)
    )


def report_long_sleep(gap, threshold=None, backlog_fn=None, journal=None, notifier=None):
    """СУЩЕСТВЕННЫЙ сон ПК → РОВНО ОДНА строка в журнал + РОВНО ОДИН сигнал в тему 328.
    Мелкий скачок (тормоз ПК / длинная синхронная задача) → ТИШИНА, побочек ноль.
    → текст карточки (str) при сигнале | None при тишине. Всё внешнее (порог, счётчик очереди,
    журнал, отправка) инъектируется — голден гоняется без сети и без Bridge."""
    threshold = SLEEP_ALARM_SEC if threshold is None else threshold
    if int(gap) <= int(threshold):
        return None
    backlog = (_pc_backlog if backlog_fn is None else backlog_fn)()
    text = sleep_alarm_text(gap, backlog, threshold)
    (journal or _cowork)(" ".join(text.split()))   # журнал — строго ОДНА строка (контракт 28.07)
    (notifier or _notify_topic)(SLEEP_ALARM_TOPIC, text)
    return text


# ------------------- РЕВИЗОР ДИАЛОГОВ (флаг DIALOG_REVIZOR) --------------------
# ШАГ 7/7 родителя 262 (2026-07-13): ревизор ВКЛЮЧЁН БОЕВЫМ владельцем — DIALOG_REVIZOR=1
# в .env ПОСТОЯННО (ручное включение, красное действие одобрено ТЗ шага 7/7). Этот коммит —
# веха включения и триггер штатной эстафеты демона (новый процесс перечитает .env).
# Шаг 2/7 родителя 262. Демон раз в REVIZOR_HOURS (дефолт 6) отбирает клиентские окна, где была
# активность С ПРОШЛОГО ПРОГОНА, и собирает по каждому ПАКЕТ для ревизии (следующие шаги 262 его
# обработают — здесь ТОЛЬКО read-only отбор+сборка, ничего не пишем ни в БД, ни клиенту).
# Источник правды по окнам — таблица `drafts` в moderation_ipc.db (recon §A, docs/revizor_recon.md):
# одна строка на реплику окна; client_id = peer_id клиента = идентификатор окна; updated_ts —
# метка активности строки; incoming/transcript — реплики клиента; final_text при status ∈
# ready/sent/test_held — НАШ отправленный клиенту текст; draft — черновик (до модерации).
# RESTART-PROOF: метка прошлого прогона лежит НА ДИСКЕ (REVIZOR_STATE_FILE), период мерим от неё —
# рестарт демона НЕ запускает ревизора раньше срока и НЕ теряет «докуда дошли». Первый прогон без
# метки — БУТСТРАП: ставим метку, backlog истории не разгребаем (следующий прогон возьмёт since=она).
# DIALOG_REVIZOR=0/нет → ветка не зовётся вовсе (поведение демона байт-в-байт прежнее).
REVIZOR_HOURS = float(os.getenv("REVIZOR_HOURS", "6") or "6")   # период ревизии окон (дефолт 6 ч)
REVIZOR_SEC = REVIZOR_HOURS * 3600.0
REVIZOR_DB = os.path.join(REPO, "moderation_ipc.db")            # боевая очередь окон (read-only отбор)
REVIZOR_STATE_FILE = os.path.join(REPO, "pc_orchestrator.revizor_state.json")  # метка прошлого прогона
REVIZOR_TIMEOUT = int(os.getenv("REVIZOR_TIMEOUT", "300") or "300")            # думатель-ревизор окна (одно окно, длинный контекст)

# Чек-лист классов ревизора вынесен в ЖИВОЙ файл docs/revizor_checklist.md (механика «урок навсегда»:
# класс дописывается одной строкой «- [класс X] описание» и попадает в преамбулу СЛЕДУЮЩЕГО тика без
# правки кода/рестарта). _revizor_checklist() читает файл на каждом тике; файла нет/пуст/ошибка →
# встроенный REVIZOR_CHECKLIST_DEFAULT (fail-safe). Ниже — дословная копия тела файла (правило
# автогритинга + классы а–ж) на случай его пропажи; ИСТОЧНИК ПРАВДЫ — сам файл.
REVIZOR_CHECKLIST_FILE = os.path.join(REPO, "docs", "revizor_checklist.md")
REVIZOR_CHECKLIST_DEFAULT = (
    "ВАЖНО об АВТОПРИВЕТСТВИИ: первое «от нас» сообщение в окне может быть АВТОПРИВЕТСТВИЕМ Telegram "
    "Business — статичным текстом настроек аккаунта (шлётся мгновенно при первом контакте / раз в 14 "
    "дней, МИМО бота и модерации; бот его НЕ сочинял). Если в пакете есть секция «АВТОПРИВЕТСТВИЕ "
    "TELEGRAM BUSINESS»: (1) на его СОДЕРЖАНИЕ находки не заводи никогда — это не продукт бота; "
    "(2) анкету/вопросы ИЗ автоприветствия не считай классом ж; (3) если бот дальше здоровается "
    "ПОВТОРНО — это находка класса е.\n"
    "Найди нарушения РОВНО из этого чек-листа (класс = буква):\n"
    "- [класс а] числа не из quote — цена/депозит/скидка/срок в отправленном, которых НЕТ во входных "
    "данных (похоже на выдуманную цифру, а не взятую из тарифа);\n"
    "- [класс б] чужая модель — в ответе фигурирует модель/марка техники, которую клиент НЕ спрашивал "
    "и о которой мы речи не вели (подмена модели);\n"
    "- [класс в] переспрос данного — мы переспрашиваем то, что клиент УЖЕ явно сообщил в этом окне "
    "(модель, даты, срок, опыт и т.п.);\n"
    "- [класс г] утечка служебного — в ОТПРАВЛЕННОМ клиенту тексте просочились внутренние маркеры: "
    "«собрано», «[уточнить», строка сезонной пометки — то, что клиент видеть не должен;\n"
    "- [класс д] ложная ✅ трекера — галочка ✅ у пункта, который на самом деле НЕ собран/не "
    "подтверждён (трекер врёт, что данные есть);\n"
    "- [класс е] повтор приветствия/канцелярит — повторное приветствие в НЕ первом ответе окна, ЛИБО "
    "приветствие бота ПОСЛЕ автоприветствия Telegram Business (оно уже поздоровалось — второе "
    "«Здравствуйте» лишнее), либо канцелярский официоз вместо живой речи;\n"
    "- [класс ж] анкета на первом сообщении — клиент ПЕРВЫМ же СОДЕРЖАТЕЛЬНЫМ сообщением дал модель И "
    "даты, а БОТ в ОТВЕТ шлёт анкету/список вопросов вместо тарифа (надо было котировать, а не "
    "допрашивать). НЕ путай с автоприветствием: если «анкета» — это САМ статичный автогритинг Telegram "
    "Business (секция выше), а не ответ бота, находку НЕ заводи (action=noise)."
)

# Преамбула ДУМАТЕЛЯ-РЕВИЗОРА окна (шаг 3/7 родителя 262) собирается на КАЖДОМ тике:
# статичная РОЛЬ (_PREFIX) + ЖИВОЙ чек-лист классов (_revizor_checklist) + статичный КОНТРАКТ ВЫВОДА
# (_SUFFIX). Тот же _thinker_exec-паттерн, что и самопочинка (read-only, --max-turns 1, --allowed-tools
# '', нейтральный cwd → НИЧЕГО не исполняет, файлы не читает — судит строго по данным пакета). Модель
# THINKER_MODEL=claude-opus-5, фолбэк claude-opus-4-8, таймаут REVIZOR_TIMEOUT=300с. Задача —
# ПОСТ-ФАКТУМ аудит одного клиентского окна (реплики клиента + наши отправленные + черновики) по
# чек-листу дефектов ответа. Выход — СТРОГО JSON-массив находок (пустой [] = нарушений нет); каждая
# находка маршрутизируется позднейшими шагами 262 по полю action. Думатель ничего не чинит сам.
REVIZOR_PREAMBLE_PREFIX = (
    "Ты — думатель-ревизор клиентских диалогов ассистента аренды мототехники TurboBaby. Тебе дают "
    "ОДНО клиентское окно ПОСТ-ФАКТУМ: реплики клиента, ТЕКСТ, отправленный клиенту (наши ответы), и "
    "черновики до модерации. Твоя задача — ТОЛЬКО аудит по чек-листу ниже; ты НИЧЕГО не исполняешь, "
    "инструментов нет, файлы не читаешь — суди строго по данным пакета.\n"
)
REVIZOR_PREAMBLE_SUFFIX = (
    "Для КАЖДОЙ находки укажи action:\n"
    "  task — дефект чинится правкой кода/промпта suggest (детект/гард/шаблон) → дай task_text: "
    "САМОДОСТАТОЧНОЕ дев-ТЗ ≤400 символов (исполнитель увидит ТОЛЬКО его, впиши класс, окно и "
    "дословную улику). task_text описывает ТОЛЬКО правку КОДА + ТЕСТЫ/ГОЛДЕНЫ — НЕ вписывай в него "
    "шаги деплоя, рестарта/перезапуска ботов, taskkill, schtasks: применение правки и рестарт делает "
    "авто-reconcile контура САМ. Если дефект требует ОСОЗНАННОГО рестарта/деплоя прода — это НЕ task, "
    "а action=owner (решение принимает человек);\n"
    "  owner — нужно решение человека (спорный тариф, политика, неоднозначный кейс, ОСОЗНАННЫЙ "
    "рестарт/деплой прод-ботов) — task_text пустой;\n"
    "  noise — по факту не нарушение (ложное срабатывание) — task_text пустой; такие НЕ включай, если "
    "сомневаешься — лучше noise, чем ложная задача.\n"
    "Ответь СТРОГО ОДНИМ JSON-массивом, без текста до/после, без markdown-обёртки:\n"
    '[{"class":"а".."ж","evidence":"<дословная улика ≤200 символов>","action":"task"|"owner"|"noise",'
    '"task_text":"<дев-ТЗ ≤400 (ТОЛЬКО код+тесты, без деплоя/рестарта) или пусто>"}]\n'
    "Нарушений нет → верни пустой массив []. Не выдумывай находок сверх чек-листа.\n\n"
)


def _revizor_checklist(path=None):
    """Живой чек-лист классов ревизора. Читаем docs/revizor_checklist.md КАЖДЫЙ тик (дописанный класс
    попадает в преамбулу следующего прогона без рестарта); md-комментарии «<!-- ... -->» вырезаем — они
    для человека, не для думателя. Файла нет / пуст / ошибка чтения → встроенный
    REVIZOR_CHECKLIST_DEFAULT (fail-safe: ревизор судит по дефолтному чек-листу, не падает). path —
    инъекция для тестов."""
    p = path or REVIZOR_CHECKLIST_FILE
    try:
        with open(p, encoding="utf-8") as f:
            body = re.sub(r"<!--.*?-->", "", f.read(), flags=re.S).strip()
        if body:
            return body
    except Exception as e:
        log.warning("ревизор: чек-лист (%s) не прочитан — встроенный дефолт (fail-safe): %s", p, e)
    return REVIZOR_CHECKLIST_DEFAULT


def _revizor_preamble(path=None):
    """Преамбула думателя-ревизора = статичная роль + ЖИВОЙ чек-лист классов + статичный контракт
    вывода. Собирается на КАЖДОМ вызове (читает файл через _revizor_checklist), чтобы новый пункт
    чек-листа попал в следующий прогон думателя. path пробрасывается в тестах."""
    return REVIZOR_PREAMBLE_PREFIX + _revizor_checklist(path) + "\n" + REVIZOR_PREAMBLE_SUFFIX


def _revizor_on():
    """Флаг DIALOG_REVIZOR=1 в .env (демон load_dotenv'ит на старте). 0/нет → ревизор выключен."""
    return (os.environ.get("DIALOG_REVIZOR") or "").strip() == "1"


def _parse_iso(s):
    """ISO-строка (updated_ts/метка прогона) → aware datetime UTC | None (не распарсилось)."""
    try:
        t = datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def _revizor_newer(ts_iso, since_iso):
    """updated_ts строки строго новее метки прошлого прогона? since_iso falsy → True (нет метки —
    всё считаем новым). ts_iso не парсится → False (не можем доказать активность → не тянем окно
    каждый прогон)."""
    if not since_iso:
        return True
    a, b = _parse_iso(ts_iso), _parse_iso(since_iso)
    return bool(a and b and a > b)


def _revizor_read_state(path=None):
    """Метка прошлого прогона ревизора (restart-proof). → dict {last_run: iso, ts: float} | {}
    (файла нет / битый json — пустой dict → бутстрап)."""
    path = REVIZOR_STATE_FILE if path is None else path
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _fmt_tick(iso):
    """ISO-метку тика → компактно «YYYY-MM-DD HH:MM[ UTC]». Непарсибельное → как есть (не врём)."""
    s = str(iso)
    try:
        d, t = s.split("T", 1)
        tz = " UTC" if ("+00:00" in t or t.endswith("Z")) else ""
        return f"{d} {t[:5]}{tz}"
    except Exception:
        return s


def _revizor_tick_label(state=None):
    """Честная строка последнего тика ревизора для статуса. Метка на диске (restart-proof) есть →
    «надзор: ревизор — последний тик <время>»; метки нет (ни разу не тикал / файл пуст/битый) →
    «надзор: ревизор — тиков ещё не было». НЕ пишем «время неизвестно» — либо честное время из
    метки, либо честное «тиков ещё не было»."""
    st = _revizor_read_state() if state is None else state
    last = (st or {}).get("last_run")
    if not last:
        return "надзор: ревизор — тиков ещё не было"
    return f"надзор: ревизор — последний тик {_fmt_tick(last)}"


def _revizor_write_state(now, path=None):
    """Метка ЭТОГО прогона на диск, атомарно (tmp+os.replace). ts (float wall-clock) — троттлинг
    между прогонами переживает рестарт демона; last_run (iso) — since для отбора окон СЛЕДУЮЩЕГО
    прогона. Сбой записи НЕ роняет тик — тихий warning (как _persist_client_watch)."""
    path = REVIZOR_STATE_FILE if path is None else path
    iso = datetime.datetime.fromtimestamp(float(now), datetime.timezone.utc).isoformat()
    blob = {"last_run": iso, "ts": float(now)}
    try:
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        log.warning("ревизор: метка прогона не записана (%s): %s", path, e)


def _revizor_db_rows(db_path=None):
    """Read-only чтение строк drafts (одна строка = реплика окна). Тянем нужные колонки (recon §A).
    → list[dict] (пусто при любой ошибке чтения/отсутствии БД — fail-safe, ревизор просто молчит)."""
    path = db_path or REVIZOR_DB
    import sqlite3
    try:
        c = sqlite3.connect(path, timeout=5.0)
        c.row_factory = sqlite3.Row
        try:
            cur = c.execute("SELECT id, client_id, client_name, incoming, transcript, draft, "
                            "final_text, status, updated_ts FROM drafts "
                            "WHERE client_id IS NOT NULL ORDER BY client_id, id")
            return [dict(r) for r in cur.fetchall()]
        finally:
            c.close()
    except Exception as e:
        log.warning("ревизор: чтение drafts (%s) не удалось: %s", path, e)
        return []


def _revizor_select_windows(rows, since_iso):
    """Отбор окон с активностью ПОСЛЕ since_iso (метка прошлого прогона). Группируем строки drafts по
    client_id (окно); окно активно, если ЛЮБАЯ его строка новее since_iso (_revizor_newer).
    since_iso None → все окна (но maybe_revizor на бутстрапе сюда не заходит). → sorted list[client_id]."""
    by = {}
    for r in rows:
        cid = r.get("client_id")
        if cid is None:
            continue
        by.setdefault(cid, []).append(r)
    return sorted(cid for cid, rs in by.items()
                  if any(_revizor_newer(x.get("updated_ts"), since_iso) for x in rs))


# --- Автоприветствие Telegram Business (дополнение к цепи ревизора #262) ----------------------
# Первое «от нас» сообщение в окне может быть АВТОПРИВЕТСТВИЕМ Telegram Business: статичный текст
# настроек аккаунта, летит МГНОВЕННО при первом контакте / раз в 14 дней — МИМО userbot и модерации,
# бот его НЕ сочинял. Ревизор помечает его в пакете (поле greeting) → думатель на его СОДЕРЖАНИЕ
# находок не заводит, анкету ИЗ него не считает классом ж, а повторное приветствие бота ПОСЛЕ него
# ловит как класс е. Матчим ОБА поколения текста владельца (он недавно сменил формулировку):
#   • старое: «спасибо, что выбрали нас» (+ ссылки наших точек БангТао/Камала);
#   • новое:  «Уже смотрю ваше сообщение…».
_REVIZOR_AUTOGREETING_RES = (
    re.compile(r"спасибо\W+что\W+выбрали\W+нас", re.IGNORECASE),
    re.compile(r"уже\W+смотрю\W+ваше\W+сообщени", re.IGNORECASE),
)
_REVIZOR_ROLE_RE = re.compile(r"^\s*\[[^\]]*\]:\s*")   # снять ярлык роли «[менеджер]: » из строки транскрипта


def _revizor_is_autogreeting(text):
    """Текст — статичное автоприветствие Telegram Business (не ответ бота)? Матчим ОБА поколения
    текста владельца по сигнатурной фразе (ё→е, пунктуация-агностично). Детерминированно; пусто → False."""
    t = (text or "").replace("ё", "е")
    return any(rx.search(t) for rx in _REVIZOR_AUTOGREETING_RES)


def _revizor_greeting_line(sent, transcript):
    """Строка автоприветствия Telegram Business в окне (первое «от нас»; летит мимо модерации →
    живёт в ТРАНСКРИПТЕ реальной истории TG, в sent обычно его нет). Ищем сигнатуру обоих поколений
    сначала в транскрипте (снимая ярлык роли), затем в sent. → текст приветствия | None (метка пакета)."""
    for ln in str(transcript or "").split("\n"):
        if _revizor_is_autogreeting(ln):
            return _REVIZOR_ROLE_RE.sub("", ln).strip()
    for s in (sent or []):
        if _revizor_is_autogreeting(s):
            return str(s).strip()
    return None


def _revizor_build_package(client_id, rows):
    """Пакет по ОДНОМУ окну (client_id) из строк drafts этого окна (recon §A). Чистая (строки уже
    прочитаны) → тестируется без БД. → dict:
      incoming   — реплики клиента (колонка incoming, непустые, в порядке id);
      transcript — самый свежий транскрипт окна ('[роль]: текст', колонка transcript);
      sent       — НАШИ отправленные клиенту (final_text при status ∈ ready/sent/test_held);
      drafts     — черновики до модерации (колонка draft, любой статус);
      greeting   — текст автоприветствия Telegram Business, если распознано (метка для думателя) | None."""
    rs = sorted((r for r in rows if r.get("client_id") == client_id),
                key=lambda r: int(r.get("id") or 0))
    incoming = [r.get("incoming") for r in rs if (r.get("incoming") or "").strip()]
    transcript = next((r.get("transcript") for r in reversed(rs)
                       if (r.get("transcript") or "").strip()), None)
    sent = [r.get("final_text") for r in rs
            if r.get("status") in ("ready", "sent", "test_held") and (r.get("final_text") or "").strip()]
    drafts = [r.get("draft") for r in rs if (r.get("draft") or "").strip()]
    client_name = next((r.get("client_name") for r in reversed(rs) if r.get("client_name")), None)
    last_ts = max((r.get("updated_ts") for r in rs if r.get("updated_ts")), default=None)
    return {"client_id": client_id, "client_name": client_name,
            "incoming": incoming, "transcript": transcript,
            "sent": sent, "drafts": drafts,
            "greeting": _revizor_greeting_line(sent, transcript), "last_ts": last_ts}


_REVIZOR_ACTIONS = ("task", "owner", "noise")
_REVIZOR_EVIDENCE_MAX = 200
_REVIZOR_TASK_MAX = 400


def _revizor_pkg_text(package):
    """Пакет окна (_revizor_build_package) → компактный текст для думателя. Клипуем секции — одно окно
    должно влезть в контекст/таймаут; транскрипт даёт полный ход диалога, incoming/sent/drafts —
    явные срезы (клиент / наши отправленные / черновики)."""
    p = package or {}

    def _join(items, cap):
        return "\n".join(f"- {str(x).strip()}" for x in (items or []) if str(x).strip())[:cap] or "—"

    cid = p.get("client_id")
    name = (p.get("client_name") or "").strip() or "?"
    transcript = (p.get("transcript") or "").strip()[:4000] or "—"
    greeting = (p.get("greeting") or "").strip()
    greet_block = ""
    if greeting:
        greet_block = ("АВТОПРИВЕТСТВИЕ TELEGRAM BUSINESS (статичный текст настроек аккаунта; летит "
                       "МИМО бота и модерации — бот его НЕ сочинял; на СОДЕРЖАНИЕ находок НЕ заводить, "
                       "анкету/вопросы из него не считать классом ж; приветствие бота ПОСЛЕ него — "
                       f"класс е):\n- {greeting[:600]}\n\n")
    return (f"ОКНО: client_id={cid} client_name={name}\n\n"
            f"{greet_block}"
            f"ТРАНСКРИПТ ОКНА (ход диалога, '[роль]: текст'):\n{transcript}\n\n"
            f"РЕПЛИКИ КЛИЕНТА (входящие):\n{_join(p.get('incoming'), 2000)}\n\n"
            f"ОТПРАВЛЕНО КЛИЕНТУ (наши одобренные ответы):\n{_join(p.get('sent'), 3000)}\n\n"
            f"ЧЕРНОВИКИ (до модерации):\n{_join(p.get('drafts'), 2000)}\n")


def _parse_revizor_json(text):
    """Строгий парс ответа думателя-ревизора → list находок [{class,evidence,action,task_text}] или None
    (fail-safe: массив вообще не распарсился). Терпим обёртку-мусор вокруг массива (от первой [ до
    последней ]). Каждый элемент валидируем: dict с action ∈ task|owner|noise; class/evidence/task_text —
    строки (клипуем evidence≤200, task_text≤400). Кривые элементы отбрасываем; пустой [] → []."""
    t = (text or "").strip()
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j <= i:
        return None
    try:
        arr = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(arr, list):
        return None
    out = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        action = str(it.get("action") or "").strip().lower()
        if action not in _REVIZOR_ACTIONS:
            continue
        out.append({"class": str(it.get("class") or "").strip()[:8],
                    "evidence": str(it.get("evidence") or "").strip()[:_REVIZOR_EVIDENCE_MAX],
                    "action": action,
                    "task_text": str(it.get("task_text") or "").strip()[:_REVIZOR_TASK_MAX]})
    return out


def _revizor_consult(package):
    """Думатель-ревизор ОДНОГО окна: _thinker_exec(_revizor_preamble() + текст пакета) → список находок
    чек-листа. Преамбула собирается ПЕР-ВЫЗОВ (живой чек-лист docs/revizor_checklist.md читается на этом
    тике). Read-only, --max-turns 1, THINKER_MODEL/фолбэк, таймаут REVIZOR_TIMEOUT=300с. Возврат:
    list находок (пустой [] = нарушений нет) или None при ЛЮБОМ сбое думателя / нераспарсенном ответе
    (fail-safe: upstream просто пропускает окно, ничего не ломается). Инъектируется в тестах."""
    prompt = _revizor_preamble() + _revizor_pkg_text(package)
    out = _thinker_exec(prompt, REVIZOR_TIMEOUT, "dialog-revizor")
    if out is None:
        return None
    findings = _parse_revizor_json(out)
    if findings is None:
        log.warning("revizor: ответ думателя-ревизора не распарсился (fail-safe пропуск): %.200s", out)
    return findings


def revizor_tick(since=None, now=None, db_path=None, rows=None):
    """Один прогон ревизора: отобрать окна с активностью ПОСЛЕ since (метка прошлого прогона) и
    собрать по каждому пакет (реплики клиента + наши отправленные + черновики; источник — drafts
    moderation_ipc.db, recon §A). READ-ONLY: ничего не пишет ни в БД, ни клиенту — только строит
    пакеты (следующие шаги 262 их обработают). rows — инъекция для тестов (без БД). → list пакетов."""
    rows = _revizor_db_rows(db_path) if rows is None else rows
    active = _revizor_select_windows(rows, since)
    packages = [_revizor_build_package(cid, rows) for cid in active]
    log.info("ревизор: since=%s → окон с активностью %d (всего строк drafts %d)",
             since, len(packages), len(rows))
    if packages:
        _cowork(f"ревизор: {len(packages)} окон с активностью с прошлого прогона — пакеты собраны")
    return packages


def maybe_revizor(now=None, db_path=None, state_path=None):
    """Троттлинг ревизора по МЕТКЕ НА ДИСКЕ (restart-proof: период мерим от last_run прошлого прогона,
    а не от старта процесса). DIALOG_REVIZOR=0/нет → None сразу. Первый прогон без метки — БУТСТРАП:
    ставим метку, backlog не разгребаем (следующий прогон возьмёт since=эта метка). → list пакетов |
    None (выключено / период не прошёл / бутстрап)."""
    if not _revizor_on():
        return None
    now = time.time() if now is None else now
    st = _revizor_read_state(state_path)
    prev_ts = st.get("ts")
    if prev_ts is not None:
        try:
            if (now - float(prev_ts)) < REVIZOR_SEC:
                return None                          # период ещё не прошёл (мерим по диску → restart-proof)
        except Exception:
            pass
    since = st.get("last_run")                       # None на самом первом прогоне → бутстрап (не разгребаем)
    result = revizor_tick(since=since, now=now, db_path=db_path) if since else None
    if result:
        try:
            _revizor_route(result, now=now)          # шаг 4/7 (262): думатель по окну + маршрутизация находок
        except Exception as e:
            log.warning("ревизор: маршрутизация находок упала (fail-safe, метку всё равно ставим): %s", e)
    _revizor_write_state(now, state_path)            # метку ставим ВСЕГДА (вкл. бутстрап) → следующий прогон ограничен
    return result


# ------------------- РЕВИЗОР: МАРШРУТИЗАЦИЯ НАХОДОК (шаг 4/7 родителя 262) -----
# revizor_tick собрал пакеты активных окон (шаг 2), _revizor_consult судит окно думателем (шаг 3).
# Здесь — РАЗВОДКА находок по каналам (сам ревизор НИЧЕГО не правит и клиентам НЕ пишет):
#   task  → зелёная задача дирижёру from=Filipp-pcloc-dec (тот декомпозирует и исполнит; красные
#           шаги спросят «да» кнопкой). Бюджет ≤REVIZOR_DAILY_BUDGET/сутки и дедуп ПО КЛАССУ — оба
#           RESTART-PROOF из маркеров очереди «[ревизор дата=… класс=…]», не из памяти процесса.
#   owner → ОДНА сводная карточка «🔍 Ревизор: находки» в инбокс 1160 (NEEDS_APPROVAL_TOPIC): цитаты
#           по окнам, редактируем СУЩЕСТВУЮЩУЮ (тот же tid, маркер [ревизор-находки]) с дедупом строк.
#   noise → только лог (ложные срабатывания наружу не выносим).
# Пусто (окна чисты) → тишина + NOTE «ревизор: N окон, чисто». Сбой думателя по окну → fail-safe
# пропуск окна; все окна без ответа → NOTE о сбое (наружу тишина). Очередь недоступна (бюджет/дедуп
# не сверить) → находки отложены до следующего прогона (НЕ флудим вслепую) + NOTE.
REVIZOR_DAILY_BUDGET = int(os.getenv("REVIZOR_DAILY_BUDGET", "2") or "2")   # потолок задач-находок/сутки
REVIZOR_OWNER_FROM = "Filipp-revizor"      # from синтетической owner-карточки (НЕ дирижёрская цепь pcloc-dec)
REVIZOR_OWNER_MARK = "[ревизор-находки]"   # маркер сводной owner-карточки в инбоксе 1160 (дедуп/гарды)
_REVIZOR_TASK_RE = re.compile(r"^\[ревизор дата=(\d{4}-\d{2}-\d{2}) класс=([^\]]*)\]")  # маркер задачи-находки
_REVIZOR_LIVE = ("new", "in_progress", "needs_approval", "approved")


def _is_revizor_owner_card(text):
    """task_text синтетической owner-карточки ревизора? Маркер-гейт для гардов process_new/
    process_approved/process_approval_timeouts (info-карточка живёт до решения, не headless-задача)."""
    return str(text or "").startswith(REVIZOR_OWNER_MARK)


def _revizor_today(now):
    """UTC-дата прогона 'YYYY-MM-DD' — ключ суточного бюджета задач-находок (restart-proof из маркера)."""
    return datetime.datetime.fromtimestamp(float(now), datetime.timezone.utc).strftime("%Y-%m-%d")


def _revizor_task_markers(items):
    """items очереди lane=pc → list[(дата, класс)] уже поставленных задач-находок ревизора (по маркеру
    _REVIZOR_TASK_RE). Источник бюджета/дедупа — ОЧЕРЕДЬ (restart-proof), не память процесса."""
    out = []
    for it in (items or []):
        m = _REVIZOR_TASK_RE.match(str(it.get("task_text") or ""))
        if m:
            out.append((m.group(1), (m.group(2) or "").strip()))
    return out


# МАНДАТ РЕВИЗОРА (живой урок 14.07): авто-задачи-находки НЕ заказывают деплой/рестарт прода —
# применение правки и рестарт делает авто-reconcile контура (maybe_update_bots) САМ. Цепи 310/311 из
# находок ревизора доходили до КРАСНЫХ шагов «деплой+рестарт прод-ботов» (✋ владельцу), хотя это
# делается автоматически. Осознанный рестарт/деплой прода — решение ЧЕЛОВЕКА → owner-карточка, не
# зелёная задача. Первый рубеж — промпт (SUFFIX выше велит думателю не вписывать такие шаги и
# помечать их action=owner); ЭТО — страховка кодом: task_text с деплой/рестарт-словами перехватываем
# ниже (в _revizor_route → owner-карточка; в enqueue → last-resort пропуск, чтобы деплой-шаг НИКОГДА
# не встал зелёной задачей). Red: Bridge/таблицы/деньги не трогаем — только маршрутизация находок.
_REVIZOR_DEPLOY_RE = re.compile(
    r"деплой|редеплой|redeploy|deploy|рестарт|перезапус|перезагруз|restart|reboot|"
    r"taskkill|schtasks|kill\s+(?:process|процесс)|убей\s+процесс",
    re.I)


def _revizor_task_wants_deploy(task_text):
    """Дев-ТЗ находки ревизора требует деплоя/рестарта/taskkill/schtasks? Мандат: авто-задача
    ограничена кодом+тестами+голденами — применение и рестарт делает авто-reconcile. Совпадение →
    находку переводим в owner-карточку (осознанный рестарт прода — решение человека, не задача)."""
    return bool(_REVIZOR_DEPLOY_RE.search(str(task_text or "")))


def _revizor_demote_deploy_task(f):
    """Находка action=task, чей task_text просит деплой/рестарт → owner-карточка (мандат: авто-задачи
    не заказывают деплой/рестарт прода). Улику для карточки берём из evidence, а если пусто — из
    самого task_text (чтобы владелец видел суть). task_text гасим (owner-карточка его не несёт)."""
    g = dict(f)
    g["action"] = "owner"
    if not (str(g.get("evidence") or "").strip()):
        g["evidence"] = str(f.get("task_text") or "").strip()[:_REVIZOR_EVIDENCE_MAX]
    g["task_text"] = ""
    return g


# ───── ВОРОТА ВХОДА В КЛИЕНТСКИЙ КОНТУР (30.07.2026) ─────
# Ревизор чинил живого клиентского бота САМ: находки классов а–ж по замыслу правят suggest.py
# (промпт SUFFIX прямо велит думателю писать дев-ТЗ на «правку кода/промпта suggest»), и цепи id=4
# «тип ТС» / id=44 «гард приветствий» ровно это и делали — обе остановлены владельцем руками.
# Теперь находка, чья правка попадает в КЛИЕНТСКИЙ контур, становится owner-карточкой, а не
# зелёной задачей дирижёру. По ВНУТРЕННИМ файлам ревизор работает как работал.
# Точка контроля честно слабая, и мы это признаём вслух: ревизор ставит ТЕКСТ дев-ТЗ, а не список
# файлов, — судить можно только по именам, НАЗВАННЫМ в тексте (ловим и «suggest.py», и голое
# «suggest»). Отсюда FAIL-CLOSED: не названо ни одного файла репозитория → контур неопределим →
# owner-карточка. Практическое следствие говорим прямо, а не прячем: почти все task-находки станут
# карточками — именно потому, что почти каждая из них по замыслу правит клиентского бота.


def _revizor_finding_touches_client(task_text):
    """Дев-ТЗ находки правит клиентский контур? → (да, названные клиентские файлы, определимо).
    Признак упал → (True, [], False): «не знаю» — это НЕ «внутренний»."""
    try:
        return client_contour.mentions(task_text, REPO)
    except Exception as e:                    # noqa: BLE001 — сбой признака не пускает задачу в бой
        log.error("ревизор: признак контура упал на находке (%s) — считаю КЛИЕНТСКОЙ", e)
        return True, [], False


def _revizor_demote_client_task(f, hits, determinate):
    """Находка action=task, задевающая клиентский контур → owner-карточка (правка живого бота —
    решение человека). Улику сохраняем: без неё владелец не поймёт, ЧТО именно предлагалось."""
    g = dict(f)
    g["action"] = "owner"
    if hits:
        why = "клиентские файлы: " + ", ".join(hits)
    elif not determinate:
        why = "файлы не названы — контур неопределим, держим по fail-closed"
    else:
        why = "клиентский контур"
    ev = str(f.get("evidence") or "").strip() or str(f.get("task_text") or "").strip()
    g["evidence"] = f"[клиентский контур, нужна твоя отмашка; {why}] {ev}"[:_REVIZOR_EVIDENCE_MAX]
    g["task_text"] = ""
    return g


def _revizor_enqueue_tasks(task_findings, items, now):
    """Находки action=task → зелёные родители дирижёру from=Filipp-pcloc-dec. Бюджет
    ≤REVIZOR_DAILY_BUDGET/сутки и дедуп ПО КЛАССУ — оба restart-proof из маркеров очереди (items).
    Дедуп: класс уже среди поставленных ревизором задач → пропуск. → (enqueued:int, skipped:int)."""
    today = _revizor_today(now)
    prior = _revizor_task_markers(items)
    today_count = sum(1 for d, _c in prior if d == today)
    seen_classes = {c for _d, c in prior if c}          # дедуп по классу среди всех задач-находок в очереди
    enq = skip = 0
    for idx, f in enumerate(task_findings):
        if today_count >= REVIZOR_DAILY_BUDGET:
            rem = len(task_findings) - idx
            skip += rem
            log.info("ревизор: суточный бюджет задач (%d) исчерпан — %d находок отложено", REVIZOR_DAILY_BUDGET, rem)
            break
        cls = (f.get("class") or "").strip()
        tt = (f.get("task_text") or "").strip()
        if not tt:
            skip += 1
            continue
        if _revizor_task_wants_deploy(tt):              # страховка: деплой/рестарт-шаг НЕ ставим зелёной задачей
            skip += 1
            log.warning("ревизор: задача-находка класса '%s' содержит деплой/рестарт-слова в task_text — "
                        "в очередь НЕ ставим (должна была стать owner-карточкой), окно %s", cls, f.get("client_id"))
            continue
        _cl, _hits, _det = _revizor_finding_touches_client(tt)   # last-resort ворот входа: клиентская находка НИКОГДА не встаёт зелёной задачей
        if _cl:
            skip += 1
            log.warning("ревизор: задача-находка класса '%s' задевает КЛИЕНТСКИЙ контур (%s) — в очередь "
                        "НЕ ставим (должна была стать owner-карточкой), окно %s",
                        cls, ", ".join(_hits) or "файлы не названы", f.get("client_id"))
            continue
        if cls and cls in seen_classes:
            skip += 1
            log.info("ревизор: задача класса '%s' уже в очереди — дедуп, пропуск (окно %s)", cls, f.get("client_id"))
            continue
        text = f"[ревизор дата={today} класс={cls}] {tt}"[:RESULT_MAX]
        ok, nid, err = enqueue_pc_task(text, frm=PC_LOCAL_DEC_FROM)
        if ok:
            enq += 1
            today_count += 1
            if cls:
                seen_classes.add(cls)
            log.info("ревизор: задача-находка класса '%s' → дирижёр id=%s (окно %s)", cls, nid, f.get("client_id"))
        else:
            skip += 1
            log.warning("ревизор: enqueue задачи-находки не удался (%s)", err)
    return enq, skip


def _revizor_owner_card_text(owner_findings, prior_lines=()):
    """Сводная owner-карточка «🔍 Ревизор: находки» → текст для инбокса 1160. Цитаты по окнам,
    строка = «• [класс X] окно cid: <цитата>»; дедуп по ТОЧНОЙ строке (мердж со строками прошлой
    карточки prior_lines, чтобы не потерять неразобранные находки прошлых прогонов). Пусто → ''."""
    seen, lines = set(), []
    for ln in prior_lines:
        s = str(ln).strip()
        if s.startswith("•") and s not in seen:
            seen.add(s)
            lines.append(s)
    for f in owner_findings:
        cls = (f.get("class") or "?").strip() or "?"
        ev = (f.get("evidence") or "").strip()
        if not ev:
            continue
        line = f"• [класс {cls}] окно {f.get('client_id')}: {ev}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    if not lines:
        return ""
    head = ("🔍 Ревизор: находки — требуют твоего решения (спорный тариф / политика / неоднозначный "
            "кейс). Ревизор сам ничего не правит и клиентам не пишет.")
    return (head + "\n" + "\n".join(lines))[:RESULT_MAX]


def _revizor_prior_lines(item):
    """Строки-цитаты «• …» из текста прошлой owner-карточки (best-effort: поле what|result item)."""
    txt = str((item or {}).get("what") or (item or {}).get("result") or "")
    return [ln for ln in txt.splitlines() if ln.strip().startswith("•")]


def _revizor_find_owner_card(items):
    """Существующая owner-карточка ревизора в очереди (needs_approval, по маркеру) → item | None."""
    for it in (items or []):
        if str(it.get("status")) == "needs_approval" and _is_revizor_owner_card(it.get("task_text")):
            return it
    return None


def _revizor_post_owner_card(owner_findings, items):
    """ОДНА сводная owner-карточка в инбокс 1160 (NEEDS_APPROVAL_TOPIC): редактируем СУЩЕСТВУЮЩУЮ
    (тот же tid, маркер) либо создаём (enqueue → claim → set_needs_approval, синхронно — в 'new' не
    задерживается; гард process_new подстрахует краш). Всё через Bridge; ревизор клиентам не пишет."""
    prior = _revizor_find_owner_card(items)
    prior_lines = _revizor_prior_lines(prior) if prior is not None else ()
    what = _revizor_owner_card_text(owner_findings, prior_lines)
    if not what:
        return
    if prior is not None:
        tid = prior.get("id")
        bc.set_needs_approval(tid, what, topic=NEEDS_APPROVAL_TOPIC)      # редактируем существующую карточку
        log.info("ревизор: owner-карточка обновлена (tid=%s, инбокс %s)", tid, NEEDS_APPROVAL_TOPIC)
        return
    ok, tid, err = enqueue_pc_task(REVIZOR_OWNER_MARK + " сводная карточка находок ревизора", frm=REVIZOR_OWNER_FROM)
    if not ok:
        log.warning("ревизор: owner-карточка не встала в очередь (%s)", err)
        return
    bc.claim_task(tid)                          # new → in_progress → needs_approval (штатный красный путь)
    bc.set_needs_approval(tid, what, topic=NEEDS_APPROVAL_TOPIC)
    log.info("ревизор: owner-карточка создана (tid=%s, инбокс %s)", tid, NEEDS_APPROVAL_TOPIC)


# ---------- РЕВИЗОР: ПОСТ-РЕЛИЗНАЯ СВЕРКА ЖИВЫХ ЧЕРНОВИКОВ (шаг 5/6 родителя 92) ----------
# К LLM-думателю (классы а–ж) добавлен ДЕТЕРМИНИРОВАННЫЙ пост-релизный проход по ЖИВЫМ черновикам
# окна ТЕМ ЖЕ чек-листом #92, что гоняет e2e-смоук suggest.runLiveSmoke (suggest._smoke_checks) —
# один источник правды на проверку и смоука, и пост-релиза (правило-класс: «мок = живой формат»,
# а здесь ещё жёстче — тот же КОД проверки):
#   1) строка столбца J — ДОСЛОВНО в отправленном/черновике;
#   2) доставка = цена ЗОНЫ пина (Раваи → 590), а не выдумка;
#   3) год поколения (20xx) в тело не утёк;
#   4) при ПОЛНЫХ данных нет отписки «вернусь/уточню и вернусь»;
#   5) нет УТВЕРЖДЕНИЙ о наличии без данных Bridge;
#   6) депозит без самопротиворечия.
# Эталон (строка J / строка доставки / зона / цена / полнота данных) строится из ТЕХ ЖЕ живых
# источников, что питают черновик (suggest._smoke_expectations по клиентской модели+датам+пину).
# Пост-релизная особинка: если эталон J/доставки НЕ выводится (нет заявочного контекста / Bridge
# недоступен) — эти два чека НЕ заводят находок (нет ground-truth ≠ «черновик врёт»); text-only
# чеки (годы/наличие/депозит/вернусь) идут ВСЕГДА. Провалы → owner-карточка (класс #92). Ревизор
# сам ничего не правит и клиентам не пишет. READ-ONLY: Bridge зовём только на quote/delivery-эталон.
_REVIZOR_PR_CLASS = "#92"                 # класс пост-релизных находок чек-листа транспорта QUOTE/DELIVERY
# Чеки, которым нужен ВНЕШНИЙ эталон: без него (пустой эталон) провал — НЕ находка (пост-релиз).
_REVIZOR_PR_NEEDS_J = "строка J дословно"
_REVIZOR_PR_NEEDS_DELIVERY = "доставка = цена зоны"


def _revizor_live_texts(package):
    """ЖИВЫЕ клиентские тексты окна для пост-релизной сверки: отправленные клиенту (sent) + черновики
    до модерации (drafts). Отдаём СЫРОЙ текст — сами чеки снимают служебные блоки (client_facing_text).
    → list[(метка, текст)] (пусто → сверять нечего)."""
    p = package or {}
    out = []
    for s in (p.get("sent") or []):
        t = str(s or "").strip()
        if t:
            out.append(("sent", t))
    for d in (p.get("drafts") or []):
        t = str(d or "").strip()
        if t:
            out.append(("draft", t))
    return out


def _revizor_expectations(package, getter=None, resolve_delivery=None, today=None):
    """Эталон чек-листа #92 для ЖИВОГО окна из ТЕХ ЖЕ источников, что питают черновик (как в
    suggest e2e-смоуке): строку столбца J и строку доставки — из pricing_note по клиентской
    модели+датам, зону/цену — из резолвера пина клиента. getter/resolve_delivery=None → ЖИВОЙ
    Bridge (read-only). Нет модели И пина в окне → эталон не строим (Bridge не дёргаем, останутся
    только text-only чеки). ЛЮБОЙ сбой → эталон без J/доставки (fail-safe: те два чека находок не
    заводят). → dict(j_line, delivery_line, zone, zone_price, full_data)."""
    empty = {"j_line": None, "delivery_line": None, "zone": None, "zone_price": None, "full_data": False}
    try:
        import suggest
        p = package or {}
        transcript = (p.get("transcript") or "\n".join(str(x) for x in (p.get("incoming") or [])))
        if not str(transcript).strip():
            return empty
        hints = suggest.extract_booking_hints(transcript, today=today)
        if not (hints.get("model") or hints.get("maps_link")):
            return empty                 # нет заявочного контекста — эталон не нужен, Bridge не зовём
        probe = {"lang": "ru", "maps_link": hints.get("maps_link")}
        return suggest._smoke_expectations(transcript, probe, getter, resolve_delivery, today)
    except Exception as e:
        log.warning("ревизор: эталон #92 окна %s не построен (fail-safe, только text-only чеки): %s",
                    (package or {}).get("client_id"), e)
        return empty


def _revizor_checklist_findings(text, exp, checks_fn=None):
    """ОДИН живой текст через чек-лист #92 (suggest._smoke_checks — ТОТ ЖЕ код, что e2e-смоук) →
    список ПРОВАЛИВШИХСЯ чеков. Пост-релизная особинка: провал чека, которому нужен ВНЕШНИЙ эталон
    (строка J / доставка), но эталона нет — НЕ находка (мы не смогли вывести ground-truth, а не
    «черновик врёт»); text-only чеки (годы/наличие/депозит/вернусь) — всегда. checks_fn инъектируется
    в тестах. → list dict-чеков (name, ok=False, expected, fact)."""
    e = exp or {}
    if checks_fn is None:
        import suggest
        checks_fn = suggest._smoke_checks
    out = []
    for c in checks_fn(text, e):
        if c.get("ok"):
            continue
        name = c.get("name")
        if name == _REVIZOR_PR_NEEDS_J and not str(e.get("j_line") or "").strip():
            continue                      # нет эталонной строки J — не находка
        if name == _REVIZOR_PR_NEEDS_DELIVERY and (not str(e.get("delivery_line") or "").strip()
                                                   or e.get("zone_price") is None):
            continue                      # зона/цена пина не выведены — не находка
        out.append(c)
    return out


def _revizor_postrelease_findings(package, exp=None, getter=None, resolve_delivery=None,
                                  today=None, checks_fn=None, exp_fn=None):
    """Пост-релизная сверка ЖИВЫХ черновиков окна чек-листом #92 → находки owner-карточки (action=
    owner, класс #92). Прогоняем КАЖДЫЙ отправленный/черновиковый текст; дедуп по имени чека (одна
    находка на класс дефекта в окне). exp — готовый эталон (инъекция теста); None → строим из окна
    (_revizor_expectations, живой Bridge). exp_fn/checks_fn инъектируются в тестах. → list находок."""
    p = package or {}
    cid = p.get("client_id")
    texts = _revizor_live_texts(p)
    if not texts:
        return []
    if exp is None:
        exp = (exp_fn or _revizor_expectations)(p, getter=getter,
                                                resolve_delivery=resolve_delivery, today=today)
    seen, out = set(), []
    for _label, txt in texts:
        for c in _revizor_checklist_findings(txt, exp, checks_fn=checks_fn):
            name = c.get("name")
            if name in seen:
                continue
            seen.add(name)
            out.append({"class": _REVIZOR_PR_CLASS, "client_id": cid, "action": "owner",
                        "check": name,      # имя чека отдельным полем (owner-карточка его игнорит) — для теста/лога
                        "evidence": (f"чек «{name}»: {c.get('fact')}")[:_REVIZOR_EVIDENCE_MAX],
                        "task_text": ""})
    return out


# ------------- ДЕТЕРМИНИРОВАННЫЕ ЧЕКИ ЧЕРНОВИКОВ (модуль reviewer, подключены 26.07.2026) ------
# Модуль reviewer.py спасён из забытого клона (коммит dc7ecd1) и до сих пор не вызывался ниоткуда.
# Из трёх его чеков подключаем ДВА — те, что не покрывает ни чек-лист думателя (классы а–ж), ни
# пост-релизная сверка #92:
#   depcheck  — депозит требует ОБА (деньги И паспорт одновременно); «или» = нормальный выбор, не находка;
#   yearcheck — год ПОКОЛЕНИЯ (рядом с моделью / «г.в.»), но НЕ в денежном контексте.
# Третий чек модуля (_jcheck) НЕ подключаем НАМЕРЕННО: он дублирует работающий #92 «строка J
# дословно». Поэтому зовём две функции ПОИМЁННО, а не review_record — так дубль не только не
# используется, но и физически не исполняется (сторожит test_jcheck_duplicate_is_not_called).
_REVIZOR_IPC_CLASS = "#93"                       # класс находок детерминированных чеков черновиков
_REVIZOR_IPC_CHECKS = ("depcheck", "yearcheck")  # ровно эти два; jcheck сюда не входит


def _revizor_ipc_findings(package, checks_fn=None):
    """Детерминированные чеки черновиков окна → находки owner-карточки (action=owner, класс #93).
    Импорт reviewer ЛЕНИВЫЙ — ровно как moderation_ipc внутри самого reviewer: нет модуля → тик
    работает как раньше, а не падает. STAFF-окна пропускаем тем же списком, что и review_record.
    Дедуп по имени чека — одна находка на класс дефекта в окне, как у #92. Упавший чек пропускаем
    поштучно, окно не роняем. checks_fn инъектируется в тестах. → list находок."""
    p = package or {}
    cid = p.get("client_id")
    texts = _revizor_live_texts(p)
    if not texts:
        return []
    try:
        import reviewer as _rv
    except Exception as e:                       # модуля нет/сломан → тик как раньше, без находок
        log.warning("ревизор: модуль чеков черновиков недоступен (fail-safe): %s", e)
        return []
    try:
        if cid in _rv.STAFF_IDS:                 # внутренний контур клиентскими чеками не проверяем
            return []
    except Exception:
        pass
    if checks_fn is None:
        checks_fn = (("depcheck", _rv._depcheck), ("yearcheck", _rv._yearcheck))
    seen, out = set(), []
    for _label, txt in texts:
        for name, fn in checks_fn:
            if name in seen:
                continue
            try:
                detail = fn(txt)
            except Exception as e:
                log.warning("ревизор: чек «%s» упал на окне %s (fail-safe, чек пропущен): %s",
                            name, cid, e)
                continue
            if not detail:
                continue
            seen.add(name)
            out.append({"class": _REVIZOR_IPC_CLASS, "client_id": cid, "action": "owner",
                        "check": name,
                        "evidence": (f"чек «{name}»: {detail}")[:_REVIZOR_EVIDENCE_MAX],
                        "task_text": ""})
    return out


def _revizor_route(packages, now=None):
    """Шаг 4/7 (262). Каждое окно → думатель-ревизор (_revizor_consult); находки маршрутизируем по
    action: task → дирижёр (бюджет/дедуп), owner → карточка 1160, noise → лог. Пусто → тишина +
    NOTE «N окон, чисто». Сбой думателя по окну → fail-safe пропуск; все окна без ответа → NOTE о
    сбое. Ревизор сам НИЧЕГО не правит и клиентам НЕ пишет. → dict-сводка (для теста/лога)."""
    pkgs = list(packages or [])
    n = len(pkgs)
    if not n:
        return {"windows": 0, "tasks": 0, "owner": 0, "noise": 0, "failed": 0}
    now = time.time() if now is None else now
    task_f, owner_f, noise_n, failed, demoted = [], [], 0, 0, 0
    demoted_client = 0                      # находки, отданные владельцу воротами входа (клиентский контур)
    # ДЕТЕРМИНИРОВАННЫЕ ПРОХОДЫ — НЕ зависят от LLM-думателя (даже если он упадёт ниже, регрессии
    # поймаем). Их два, и оба текут в ТУ ЖЕ owner-карточку, что классы а–ж, с тем же бюджетом и
    # дедупом; сбой по окну — fail-safe пропуск, прогон не роняется:
    #   #92 — пост-релизная сверка транспорта QUOTE/DELIVERY (шаг 5/6 родителя 92);
    #   #93 — чеки черновиков depcheck/yearcheck (модуль reviewer, подключены 26.07.2026).
    for pkg in pkgs:
        try:
            pr = _revizor_postrelease_findings(pkg)
            if pr:
                owner_f.extend(pr)
                log.info("ревизор: пост-релиз окно %s — %d находок чек-листа #92 → owner-карточка",
                         (pkg or {}).get("client_id"), len(pr))
        except Exception as e:
            log.warning("ревизор: пост-релизная сверка окна %s упала (fail-safe): %s",
                        (pkg or {}).get("client_id"), e)
        try:
            ipc = _revizor_ipc_findings(pkg)
            if ipc:
                owner_f.extend(ipc)
                log.info("ревизор: чеки черновиков окно %s — %d находок класса %s → owner-карточка",
                         (pkg or {}).get("client_id"), len(ipc), _REVIZOR_IPC_CLASS)
        except Exception as e:
            log.warning("ревизор: чеки черновиков окна %s упали (fail-safe): %s",
                        (pkg or {}).get("client_id"), e)
    for pkg in pkgs:
        findings = _revizor_consult(pkg)
        if findings is None:                    # думатель упал/не распарсился → fail-safe пропуск окна
            failed += 1
            continue
        cid = (pkg or {}).get("client_id")
        for raw in findings:
            f = dict(raw)
            f["client_id"] = cid
            act = f.get("action")
            if act == "task":
                if _revizor_task_wants_deploy(f.get("task_text")):   # мандат: авто-задача деплой/рестарт не заказывает
                    owner_f.append(_revizor_demote_deploy_task(f))   # → owner-карточка (осознанный рестарт — решение человека)
                    demoted += 1
                    log.info("ревизор: находка класса '%s' просит деплой/рестарт в task_text → "
                             "owner-карточка (авто-задача деплой не заказывает), окно %s", f.get("class"), cid)
                    continue
                cl_hit, cl_files, cl_det = _revizor_finding_touches_client(f.get("task_text"))
                if cl_hit:                              # ворота ВХОДА: правка живого бота — решение человека
                    owner_f.append(_revizor_demote_client_task(f, cl_files, cl_det))
                    demoted_client += 1
                    log.info("ревизор: находка класса '%s' правит КЛИЕНТСКИЙ контур (%s) → owner-карточка "
                             "вместо зелёной задачи, окно %s", f.get("class"),
                             ", ".join(cl_files) or "файлы не названы (fail-closed)", cid)
                else:
                    task_f.append(f)
            elif act == "owner":
                owner_f.append(f)
            else:
                noise_n += 1
    if noise_n:
        log.info("ревизор: %d находок класса noise (ложные срабатывания) — только лог", noise_n)
    if demoted:
        log.info("ревизор: %d находок с деплой/рестарт-шагами переведены из задач в owner-карточки", demoted)
    if demoted_client:
        log.info("ревизор: %d находок по КЛИЕНТСКОМУ контуру переведены из задач в owner-карточки "
                 "(ворота входа — правку живого бота решает человек)", demoted_client)
    if not task_f and not owner_f:              # окна чисты (или только шум/сбой) → наружу тишина, NOTE в журнал
        if failed >= n:
            _cowork(f"ревизор: думатель не ответил ни по одному из {n} окон — прогон пропущен")
        else:
            tail = f" (+{noise_n} шум)" if noise_n else ""
            _cowork(f"ревизор: {n} окон, чисто{tail}")
        return {"windows": n, "tasks": 0, "owner": 0, "noise": noise_n, "failed": failed}
    items = _loc_fetch_items()                  # снимок очереди (все статусы) — бюджет/дедуп/поиск карточки
    if items is None:                           # частичная картина опаснее ожидания → откладываем, не флудим
        _cowork(f"ревизор: очередь недоступна — {len(task_f)} задач и {len(owner_f)} owner-находок отложены до след. прогона")
        return {"windows": n, "tasks": 0, "owner": 0, "noise": noise_n, "failed": failed,
                "demoted": demoted, "deferred": True}
    enq = skip = 0
    if task_f:
        enq, skip = _revizor_enqueue_tasks(task_f, items, now)
    if owner_f:
        _revizor_post_owner_card(owner_f, items)
    parts = []
    if enq:
        parts.append(f"{enq} задач дирижёру")
    if owner_f:
        parts.append(f"owner-карточка 1160 ({len(owner_f)} цитат)")
    if demoted:
        parts.append(f"{demoted} с деплой-шагом → owner (не задача)")
    if demoted_client:
        parts.append(f"{demoted_client} по клиентскому контуру → owner (ворота входа)")
    if failed:
        parts.append(f"{failed} окон без ответа думателя")
    if not parts:                               # находки были, но все отсеяны бюджетом/дедупом
        parts.append(f"находки отсеяны (бюджет/дедуп): task {len(task_f)}, skip {skip}")
    _cowork("ревизор: " + ", ".join(parts))
    return {"windows": n, "tasks": enq, "owner": len(owner_f), "noise": noise_n, "failed": failed,
            "demoted": demoted, "demoted_client": demoted_client}


# ------------------- OS-синглтон демона (разбор #128, часть 4) ----------------
# Инцидент: ДВА pc_orchestrator одновременно (оба стартовали в одну секунду от Планировщика
# поверх уже живого) → оба поллят очередь и наперегонки claim'ят задачи (двойное исполнение).
# Класс-фикс: атомарный lock-файл с PID (O_CREAT|O_EXCL, как в pc_agent). Второй живой демон
# при старте видит лок живого и выходит. Дубль, поднявшийся БЕЗ лока (старый код), схлопнется
# сам: при первом же self-update оба старых спавнят новых, но лок эксклюзивен — выживает ОДИН.
# Self-update-эстафета: старый спавнит нового с env PC_ORCH_SUPERSEDE_PID=<свой PID>; новый,
# если лок держит именно этот PID, ЖДЁТ его смерти (старый снимает лок в finally main()) —
# гарантированно «гасим старого перед стартом нового», перекрытия нет.

def _lock_pid_alive(pid):
    """Жив ли процесс по PID (Windows, без psutil). Инъектируется в тестах."""
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=10, creationflags=NO_WINDOW)
        return f'"{pid}"' in r.stdout or f",{pid}," in r.stdout
    except Exception:
        return False


def _read_lock_pid(path=None):
    try:
        with open(path or LOCK_FILE, encoding="utf-8") as f:   # закрываем сразу: на Windows
            return int(f.read().strip() or "0")                # висящий хэндл блокирует os.remove
    except Exception:
        return 0


def acquire_singleton(lock_path=None, pid_alive=None, supersede_wait=30.0, sleep=0.5):
    """True — лок наш, стартуем; False — другой ЖИВОЙ демон уже держит лок, выходим (второй не поднимаем).
    Мёртвый холдер → забираем лок. Холдер == наш supersede-PID (self-update) → ждём его смерти до
    supersede_wait, затем забираем (эстафета). pid_alive/lock_path инъектируются в тестах."""
    lock_path = lock_path or LOCK_FILE
    pid_alive = pid_alive or _lock_pid_alive
    sup = os.getenv(SUPERSEDE_ENV, "").strip()
    sup = int(sup) if sup.lstrip("-").isdigit() else 0
    deadline = time.time() + supersede_wait
    for _ in range(200):                       # верхняя граница итераций (страховка от вечного цикла)
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            holder = _read_lock_pid(lock_path)
            if holder == os.getpid():          # уже наш (перезабор) — считаем успехом
                return True
            if holder == 0 or not pid_alive(holder):
                log.warning("singleton: устаревший лок (PID %s мёртв) — забираю", holder or "?")
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue                        # заберём на следующем витке
            if holder == sup and time.time() < deadline:
                time.sleep(sleep)               # self-update: ждём смерти сменяемого старого демона
                continue
            if holder == sup:                   # старый завис дольше окна — successor всё равно забирает
                log.warning("singleton: сменяемый PID %s не умер за %sс — забираю лок (successor)", holder, supersede_wait)
                try:
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass
                continue
            log.warning("singleton: pc_orchestrator уже запущен (живой PID %s) — второй НЕ стартую", holder)
            return False
    log.error("singleton: не смог получить лок за 200 итераций — НЕ стартую (страховка)")
    return False


def release_singleton(lock_path=None):
    """Снять лок ТОЛЬКО если он наш (чужой/successor'ский лок не трогаем)."""
    lock_path = lock_path or LOCK_FILE
    try:
        if _read_lock_pid(lock_path) == os.getpid():
            os.remove(lock_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("singleton: не смог снять лок: %s", e)


def main():
    # OS-синглтон ПЕРВЫМ действием: два демона одновременно недопустимы (двойной claim задач).
    if not acquire_singleton():
        log.warning("=== ВТОРОЙ ЭКЗЕМПЛЯР ДЕМОНА — выхожу (singleton-лок держит живой процесс) ===")
        return
    try:
        _main_loop()
    finally:
        release_singleton()


def _main_loop():
    _init_running_version()
    # claude= печатаем РЕЗОЛЬВНУТЫЙ путь, а не сырой CLAUDE_BIN из .env. Живой прокол 22.07:
    # баннер бодро писал `claude=…\2.1.197\claude.exe`, хотя такого каталога на диске уже НЕТ
    # (автообновлятор его снёс) — демон при этом прекрасно работал на 2.1.217, но диагностика
    # по логу вела строго не туда. Лог обязан показывать то, что исполняется.
    log.info("=== ДЕМОН СТАРТ (lane=%s, poll=%ss, task_timeout=%ss, approval_ttl=%ss, claude=%s, commit=%s) ===",
             LANE, POLL_SEC, TASK_TIMEOUT, APPROVAL_TTL,
             (resolve_claude() or "НЕ НАЙДЕН"), RUNNING_COMMIT)
    # Селективный тест-гейт авто-применения (порт VPS): подтверждаем состояние флагов в баннере,
    # чтобы факт включения/дефолта (0=полный прогон всех тестов) читался прямо из лога старта.
    log.info("=== ФЛАГИ ГЕЙТА: GATE_STEP_SELECTIVE=%s GATE_SINGLE_SELECTIVE=%s (селективный тест-гейт авто-применения; 0=полный прогон) ===",
             int(_gate_step_selective_on()), int(_gate_single_selective_on()))
    # ФЛАГИ ДУМАТЕЛЯ в баннер (разбор 31.07, ловушка №2: до этой строки факт подхвата рубильника
    # не читался НИОТКУДА — доказательства не было вовсе до первого срабатывания). Печатаем то,
    # что видит ЖИВОЙ процесс в своём os.environ ПОСЛЕ load_dotenv, а не строку файла .env: это
    # разные вещи ровно в том классе, ради которого стоит _norm_model_id — унаследованное от
    # предка значение load_dotenv НЕ перезаписывает (override=False), и файл может врать. Рядом
    # СЫРОЕ значение: «1» с пробелом/«true»/«yes» дают флаг=0 при бодро выглядящем .env.
    log.info("=== ФЛАГИ ДУМАТЕЛЯ: STEP_SELFHEAL=%s PLAN_ADAPT=%s PC_LOCAL_DEC=%s "
             "(сырое os.environ ЖИВОГО процесса: %r / %r / %r; сравнение строгое == \"1\"; "
             "аварийные стоп-файлы взведены: step_selfheal=%s plan_adapt=%s) ===",
             int(_selfheal_on()), int(_plan_adapt_on()), int(_local_dec_on()),
             os.environ.get("STEP_SELFHEAL"), os.environ.get("PLAN_ADAPT"),
             os.environ.get("PC_LOCAL_DEC"),
             int(_flag_forced_off("STEP_SELFHEAL")), int(_flag_forced_off("PLAN_ADAPT")))
    # Бюджет headless-claude (зеркало VPS oom2 1520c68, инцидент-каскад 22.07) — в баннер,
    # чтобы действующий лимит читался прямо из лога старта.
    log.info("=== БЮДЖЕТ CLAUDE: MAX_CLAUDE_PROCS=%s (wait=%ss; счёт ПО РОДИТЕЛЮ — только НАШИ headless-потомки, интерактивные RC-сессии не в счёт; 3 отказа подряд → карточка) ===",
             MAX_CLAUDE_PROCS, CLAUDE_BUDGET_WAIT_SEC)
    if _stopped():
        log.info("рубильник pc_orchestrator.stop активен — не стартую поллинг")
        return
    global _loop_prev_wall, _client_grace_until
    while not _stopped():
        try:
            # (2) Детект сна/пробуждения ДО вотчдога: если ПК спал, wall-clock скакнёт — взводим grace,
            # чтобы первый пост-пробуждение прогон вотчдога (троттлинг уже истёк) не принял медленный
            # CIM за смерть и не рестартнул зря. Взводим ТОЛЬКО на реальном скачке; норм. виток не трогаем.
            now = time.time()
            if _woke_from_sleep(now, _loop_prev_wall):
                _client_grace_until = now + WAKE_GRACE_SEC
                gap = int(now - _loop_prev_wall)
                log.warning("детект пробуждения ПК: скачок wall-clock %sс (>%s+%s) — grace вотчдога %sс",
                            gap, POLL_SEC, WAKE_JUMP_MARGIN, WAKE_GRACE_SEC)
                # инцидент 30.07 (сон 8 ч 58 м): существенный сон — не только grace, но и ГРОМКИЙ
                # след. Строка в журнал + карточка в тему 328 с числом накопившихся задач lane=pc.
                # Обёрнуто в try: сигнал НИКОГДА не должен ронять тик демона (как _notify_*).
                try:
                    if report_long_sleep(gap):
                        log.warning("ПК спал %sс (>%s) — строка в журнал и сигнал в тему %s отправлены",
                                    gap, SLEEP_ALARM_SEC, SLEEP_ALARM_TOPIC)
                except Exception as e:
                    log.warning("сигнал о долгом сне не отправлен: %s", e)
            _loop_prev_wall = now
            poll_once()
            maybe_client_watchdog()   # часть 3: следим за pc_agent/userbot/moderation_bot (троттлинг 5 мин)
            maybe_session_watch()     # инцидент 29.07: немая сессия (жива, но не работает) → карточка в 328
            maybe_revizor()           # шаг 2/7 (262): ревизор диалогов за DIALOG_REVIZOR (троттлинг REVIZOR_HOURS)
            maybe_lesson_commit_retry()  # пакет «полнота лога» п.6: докоммитить урок из спула (провал коммита ≠ вечная грязь)
            maybe_git_ff_pull()       # родитель #221: подтянуть origin/main ff-only ДО реконсиляции/self-update (тот же тик применит)
            maybe_reconcile_children()  # класс-фикс c6d8a30: применить свежий код детей на ЛЮБОЙ новый коммит
            if maybe_self_update():   # задача цикла обновила pc_orchestrator.py → эстафета новому
                log.info("=== ДЕМОН ВЫШЕЛ ПО SELF-UPDATE (эстафета новому процессу) ===")
                return
        except Exception as e:
            log.exception("ошибка цикла: %s", e)
        for _ in range(POLL_SEC):
            if _stopped():
                break
            time.sleep(1)
    log.info("=== ДЕМОН ОСТАНОВЛЕН (рубильник/стоп-флаг) ===")


# ------------------------------- watchdog / автозапуск -----------------------

def _heartbeat_fresh(now=None, threshold=HEARTBEAT_STALE):
    try:
        hb = open(HEARTBEAT_FILE, encoding="utf-8").read().strip()
        age = _age_sec(hb, now=now)
        return age is not None and age <= threshold
    except Exception:
        return False


def _schtasks_run(task_name=TASK_NAME):
    """Запустить задачу Планировщика. Возврат (rc, output). Инъектируется в тестах."""
    try:
        p = subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True,
                           encoding="utf-8", errors="replace", timeout=30,   # utf-8: русский вывод schtasks читаем в логе
                           creationflags=NO_WINDOW)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return -1, f"schtasks не запустился: {e}"


# --- внешний вотчдог: живость ПО ФАКТУ + антиспам (класс-фикс ложняка 13.07) ---
# Живой провал: heartbeat пишется в КОНЦЕ poll_once, а run_task (claude -p) законно молотит
# до 45 мин → на ЛЮБОЙ задаче длиннее HEARTBEAT_STALE (180с) heartbeat замирал, внешний
# вотчдог считал живой демон мёртвым, schtasks /Run поднимал второй экземпляр (его штатно
# гасил singleton-лок), heartbeat оставался старым → «не смог поднять» + пуш КАЖДЫЕ 5 мин
# весь день (13.07: каждый алерт-слот лежит внутри окна CLAIM..COMPLETE задач 256–260).
# Класс: живость = heartbeat свеж ИЛИ процесс демона реально жив (CIM по CommandLine, как в
# client_watchdog — переживает и длинные задачи, и смену PID после self-update); алерт — один
# на инцидент (переход жив→мёртв), состояние в файле (вотчдог — короткоживущий процесс,
# память между тиками не живёт).
WD_STATE_FILE = os.path.join(REPO, "pc_orchestrator.watchdog_state.json")
WD_ALERT_COOLDOWN = int(os.getenv("PC_WD_ALERT_COOLDOWN", "900") or "900")   # флап-защита алерта, с


def _find_daemon_pids():
    """PID процессов САМОГО демона pc_orchestrator.py (без --watchdog-запусков — вотчдог тоже
    исполняет pc_orchestrator.py! — и без своего PID). ТРИ исхода как у _find_pids_by_script
    (#171): список | [] (честная пустота) | None (CIM не смог — смерть НЕ доказана)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
          "| Where-Object { $_.CommandLine -like '*pc_orchestrator.py*' "
          "-and $_.CommandLine -notlike '*--watchdog*' } "
          "| Select-Object -ExpandProperty ProcessId")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=20, creationflags=NO_WINDOW)
    except Exception as e:
        log.warning("watchdog: CIM-поиск демона не удался (%s) — исход НЕИЗВЕСТЕН", e)
        return None
    pids = [int(x) for x in (p.stdout or "").split() if x.strip().isdigit()]
    pids = [x for x in pids if x != os.getpid()]
    if pids:
        return pids
    if p.returncode != 0 or (p.stderr or "").strip():
        log.warning("watchdog: CIM-поиск демона пуст, но rc=%s / stderr=%r — исход НЕИЗВЕСТЕН",
                    p.returncode, _tail((p.stderr or "").strip(), 120))
        return None
    return []


def _wd_state_read(path=None):
    """Состояние вотчдога {'state': 'alive'|'down', 'last_alert': ts} из файла → dict ({} = нет/бит)."""
    try:
        with open(path or WD_STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _wd_state_write(d, path=None):
    try:
        with open(path or WD_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception as e:
        log.warning("watchdog: state-файл не записался (%s) — антиспам деградирует, не критично", e)


def watchdog(now=None, runner=None, verify_sleep=None, finder=None, state_path=None,
             notify=None, cowork=None, wall_now=None):
    """НАДЁЖНЫЙ вотчдог (урок 205 — тихого сбоя быть не должно; класс-фикс 13.07 — и ЛОЖНОГО
    шума быть не должно): живость ПО ФАКТУ — heartbeat свеж ИЛИ процесс демона жив (длинная
    задача, замершая heartbeat, или смена PID после self-update ≠ смерть). ДОКАЗАННО мёртв
    (heartbeat протух И процессов нет) → schtasks /Run + верификация (heartbeat ИЛИ процесс);
    не поднялся → РОВНО один алерт на инцидент (переход жив→мёртв, кулдаун WD_ALERT_COOLDOWN,
    состояние в файле) + NOTE в cowork. CIM не смог → страховочный /Run БЕЗ алерта (смерть не
    доказана; второй экземпляр штатно гасит singleton).
    → 'stopped'|'alive'|'restarted'|'failed_to_start'|'unknown'."""
    if _stopped():
        log.info("watchdog: рубильник активен — демон намеренно не поднимается")
        return "stopped"
    find = finder or _find_daemon_pids
    tnow = wall_now if wall_now is not None else time.time()

    def _mark_alive():
        st = _wd_state_read(state_path)
        if st.get("state") == "down":
            log.info("watchdog: инцидент закрыт — демон снова жив")
            (cowork or _cowork)("watchdog: демон снова жив — инцидент закрыт")
        _wd_state_write({"state": "alive", "last_alert": float(st.get("last_alert") or 0.0)},
                        state_path)

    if _heartbeat_fresh(now=now):
        _mark_alive()
        return "alive"
    pids = find()
    if pids:
        # heartbeat замер, но демон ЖИВ по процессу: длинная задача держит poll_once (корень
        # ложняка 13.07) либо только что был self-update (новый PID). НЕ поднимаем, НЕ шумим.
        log.info("watchdog: heartbeat старше %sс, но процесс демона жив (PID %s) — "
                 "длинная задача/рестарт, не трогаю", HEARTBEAT_STALE, pids)
        _mark_alive()
        return "alive"
    if pids is None:
        # Смерть НЕ доказана (#171: «не смог проверить» ≠ «мёртв») → страховочный /Run
        # (живому не повредит: второй экземпляр гасит singleton), алерт НЕ шлём.
        rc, out = (runner or _schtasks_run)()
        log.warning("watchdog: heartbeat протух, CIM неясен — страховочный schtasks /Run rc=%s | %s "
                    "(без алерта: смерть не доказана)", rc, _tail(out, 120))
        return "unknown"
    # ДОКАЗАННО мёртв: heartbeat протух И процессов демона нет → поднимаем.
    log.warning("watchdog: heartbeat протух (>%ss) и процессов демона НЕТ — поднимаю через schtasks",
                HEARTBEAT_STALE)
    rc, out = (runner or _schtasks_run)()
    log.warning("watchdog: schtasks /Run /TN %s → rc=%s | %s", TASK_NAME, rc, out)   # ЛОГ вывода (205)
    if verify_sleep is None:
        verify_sleep = WATCH_VERIFY_SLEEP
    if verify_sleep:
        time.sleep(verify_sleep)
    ver_pids = find()
    if _heartbeat_fresh(now=now) or ver_pids:
        log.info("watchdog: демон поднялся (heartbeat свежий / процесс жив)")
        _mark_alive()
        return "restarted"
    if ver_pids is None:
        log.warning("watchdog: после /Run CIM неясен — подъём не подтверждён и не опровергнут, "
                    "без алерта (перепроверим следующим тиком)")
        return "unknown"
    log.error("watchdog: ТИХИЙ СБОЙ ПРЕДОТВРАЩЁН — демон НЕ поднялся после schtasks /Run (rc=%s). "
              "Нужно вмешательство.", rc)
    st = _wd_state_read(state_path)
    if st.get("state") != "down" and (tnow - float(st.get("last_alert") or 0.0)) >= WD_ALERT_COOLDOWN:
        # КРИТИЧЕСКИЙ инцидент (доказанная смерть демона: heartbeat протух + процессов нет +
        # schtasks /Run не поднял) → Инбокс 1160 (личка — фолбэк). notify инъектируется в тестах.
        (notify or _notify_critical)("⚠️ Оркестратор: watchdog не смог поднять демон через schtasks — "
                                     "проверь pc_orchestrator.log")
        (cowork or _cowork)("watchdog: демон мёртв, schtasks /Run не поднял — нужен разбор")
        _wd_state_write({"state": "down", "last_alert": tnow}, state_path)
    else:   # инцидент уже заявлен (или флап внутри кулдауна) — тишина, фиксируем только state
        _wd_state_write({"state": "down", "last_alert": float(st.get("last_alert") or 0.0)},
                        state_path)
    return "failed_to_start"


def self_update_ok():
    """Гейт самообновления (если демон обновляется): py_compile+import-smoke нового кода. Битый → False."""
    try:
        import selfupdate_gate
        return selfupdate_gate.code_gate(VENV_PY, REPO, ["pc_orchestrator.py"], "pc_orchestrator")
    except Exception as e:
        return False, f"гейт не запустился: {e}"


if __name__ == "__main__":
    # ПЕРВОЙ КОМАНДОЙ: stdout/stderr → UTF-8. CLI-ветки ниже печатают статус цепи с эмодзи
    # (📊/⏹), а pc_agent зовёт нас субпроцессом и ЧИТАЕТ этот вывод. Без явного UTF-8 print с
    # 📊 в пайп падал `'charmap' codec can't encode '\U0001f4ca'`, и в кнопку «Статус цепи»
    # уезжал traceback вместо статуса (третий укус класса, 30.07.2026).
    io_utf8.force_utf8()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--watchdog":
        print(watchdog())
    elif arg == "--stop":
        open(STOP_FLAG, "w", encoding="utf-8").close()
        print("рубильник поставлен: pc_orchestrator.stop")
    elif arg == "--start":
        try:
            os.remove(STOP_FLAG)
        except Exception:
            pass
        print("рубильник снят")
    elif arg == "--chain-stop":
        # стоп цепи из карточки дирижёра (pc_agent зовёт субпроцессом после owner-gate)
        pid = sys.argv[2] if len(sys.argv) > 2 else ""
        try:
            ok, msg = _loc_stop_chain(int(pid))
        except Exception as e:
            print(f"цепь #{pid}: стоп не выполнен ({e})")
            sys.exit(1)
        print(msg)
        sys.exit(0 if ok else 1)
    elif arg == "--chain-status":
        # честный статус цепи из карточки дирижёра
        pid = sys.argv[2] if len(sys.argv) > 2 else ""
        try:
            print(_loc_chain_status(int(pid)))
        except Exception as e:
            print(f"цепь #{pid}: статус недоступен ({e})")
            sys.exit(1)
    elif arg == "--enqueue":
        # ПРЯМОЙ КАНАЛ (этап 1): инъекция одиночки lane=pc прямо в очередь Bridge, минуя splinter.
        # Демон подхватит обычным поллингом. Работает даже при лежащем Splinter/девботе.
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        ok, nid, err = enqueue_pc_task(text)
        if ok:
            print(f"OK: одиночка lane={LANE} поставлена в очередь id={nid} (прямой канал, минуя splinter)")
        else:
            print(f"FAIL enqueue: {err}")
            sys.exit(1)
    else:
        main()
