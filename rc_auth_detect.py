# -*- coding: utf-8 -*-
"""
rc_auth_detect.py — детектор ПРОТУХШЕЙ авторизации rc-server (ВАРИАНТ «а» артефакта
`docs/artifacts/2026-07-29-rc-token-staleness-prevention.md`).

ЧТО ЛЕЧИТ. После перелогина/ротации кредов сервер моста чинит СВОЙ токен рефрешем, но СВЕЖЕ
ПОРОЖДЁННЫМ воркерам отдаёт протухший: каждая НОВАЯ сессия владельца умирает за 2–3 с с
`OAuth access token has been revoked`, а уже открытые продолжают работать как ни в чём не
бывало. Отсюда симптом «новая сессия висит в Connecting» при живом на вид контуре. Ни один
предикат живости этого не видит: процесс жив, лог свеж, соединения есть (замер артефакта —
ESTABLISHED 4/10 у сервера, 10/10 у воркера). Инцидент повторился 28.07 и 29.07 подряд.

ЛЕЧЕНИЕ: сторож рестартует ветку `rc-server` — новый сервер берёт свежие креды.

ДВА ПРАВИЛА, БЕЗ КОТОРЫХ ДЕТЕКТОР ВРЁТ (оба — из замеров артефакта):

1. **Признак — ДОСЛОВНАЯ фраза, а не число.** Голый греп по `401|revoked|Unauthorized` за
   сутки 28→29.07 дал 18 совпадений, настоящих из них 4: остальные — `401` внутри JSON
   (`estimated_tokens`, uuid, доли секунды `…:23.401Z`). 78 % ложных. Ловим только
   `failed 401: OAuth access token has been revoked`.

2. **Судить по ДЕЙСТВИЮ, а не по подстроке** (правило среды №5). Одной фразы мало: мост
   переживает разовый 401 рефрешем и работает дальше (живой пример — `StopWork: 401 received,
   attempting token refresh` → `Token refreshed, retrying request` → `200`). Рестартуем только
   когда за фразой ПОШЛО ДЕЙСТВИЕ: та же сессия УПАЛА за секунды (`status=failed`,
   `duration=<N>s`, N ≤ FAST_FAIL_MAX). Разовый сетевой 401, переживший рефреш, детектор не
   тронет вовсе.

СВЯЗЬ ФРАЗЫ И ПАДЕНИЯ — ПО ИДЕНТИФИКАТОРУ, а не по соседству строк: в логе параллельно живут
несколько сессий, и «следующая строка со status=failed» запросто окажется чужой. Строка отказа
несёт `session_<ID>`, строка падения — `workId=cse_<ID>`; ID у них ОДИН И ТОТ ЖЕ (живой факт
артефакта: `session_01VL18wKrkBWsKVwmifPHQ7c` ↔ `cse_01VL18wKrkBWsKVwmifPHQ7c`). Сверяем по
ОБЩЕМУ ПРЕФИКСУ (≥ ID_PREFIX_MIN символов), а не по полному равенству: в снятых образцах
длинные id местами усечены, а 8 символов base62 — уже 2×10^14 вариантов, столкновения нет.

ЧЕСТНО ПРО ОБРАЗЕЦ: на момент написания живой строки падения в логах ПК не осталось — боевой
`rc_server_debug.log` усекается сторожем при КАЖДОМ подъёме ветки (`rc_supervisor.default_spawn`),
и последний рестарт её стёр. Голдены построены на цитатах артефакта, снятых с живого лога
28→29.07 (`fixtures/rc_server_auth_revoked.log`), причём сопоставление НЕ зависит от того, что
стоит внутри многоточий цитаты: ищем куски по отдельности внутри строки, а не один сплошной
шаблон. Если живой формат окажется шире — детектор всё равно совпадёт.

УСЕЧЕНИЕ ЛОГА — отдельный случай (минус, названный в артефакте): файл обнуляется на каждом
подъёме ветки. Состояние держит СМЕЩЕНИЕ; если размер файла стал МЕНЬШЕ смещения — это новая
жизнь процесса: читаем с нуля и сбрасываем накопленное, иначе детектор перечитает старую улику
и рестартанёт по мёртвому поводу.

ПОРОГ FAILS_NEEDED = 1 (по умолчанию). Артефакт предлагает две неудачи подряд «по желанию»:
это практически исключает ложный рестарт, но ценой ВТОРОЙ умершей сессии — то есть владелец
должен вручную запустить задание ещё раз и снова получить труп. Правило «по действию» уже
отсекает разовый 401, поэтому цену платит не владелец. Поднять до 2 — `RC_AUTH_FAILS=2`.
"""

import os
import re

REVOKED_PHRASE = "failed 401: OAuth access token has been revoked"
_ID_RE = re.compile(r"(?:session|cse)_([A-Za-z0-9]{6,})")
_DURATION_RE = re.compile(r"duration=(\d+)s")
_FAILED_MARK = "status=failed"

# Падение «за секунды» (в живом образце duration=2s). Держим с запасом: медленный ПК/диск
# может растянуть тот же отказ до нескольких секунд, а НОРМАЛЬНАЯ сессия живёт минутами.
FAST_FAIL_MAX = int(os.getenv("RC_AUTH_FAST_FAIL", "20") or "20")
# Сколько строк ждём падения после фразы отказа, прежде чем счесть 401 пережитым (мост
# отрефрешил и поехал дальше). Щедро: между ними в живом образце ~3 строки, но в логе рядом
# крутятся другие сессии.
PAIR_WINDOW = int(os.getenv("RC_AUTH_PAIR_WINDOW", "200") or "200")
FAILS_NEEDED = int(os.getenv("RC_AUTH_FAILS", "1") or "1")
ID_PREFIX_MIN = 8
ENABLED = os.getenv("RC_AUTH_WATCH", "1") != "0"    # запасной клапан: выключить детектор целиком


def new_state():
    """Чистое состояние детектора: смещение в логе, ожидающие пары, счётчик подтверждённых."""
    return {"offset": 0, "pending": {}, "failures": 0, "seen": 0}


def ids_match(a, b, minlen=ID_PREFIX_MIN):
    """Один ли это идентификатор сессии. Сверка по ОБЩЕМУ ПРЕФИКСУ: в снятых образцах длинные
    id усечены многоточием, а полного равенства требовать нельзя (см. шапку)."""
    a, b = str(a or ""), str(b or "")
    if len(a) < minlen or len(b) < minlen:
        return False
    return a.startswith(b[:minlen]) or b.startswith(a[:minlen])


def read_new(path, offset, opener=None, sizer=None):
    """Прочитать ХВОСТ лога с сохранённого смещения → (строки, новое смещение, усечён).

    Усечение (размер < смещения) — это новая жизнь процесса ветки: читаем с нуля и говорим об
    этом вызывающему, чтобы он сбросил накопленные улики."""
    _size = sizer or (lambda p: os.path.getsize(p))
    _open = opener or (lambda p: open(p, encoding="utf-8", errors="replace"))
    try:
        size = _size(path)
    except Exception:
        return [], offset, False
    truncated = size < offset
    start = 0 if truncated else offset
    try:
        with _open(path) as f:
            try:
                f.seek(start)
            except Exception:
                pass
            text = f.read() or ""
    except Exception:
        return [], offset, truncated
    return text.splitlines(), start + len(text), truncated


def feed(lines, state):
    """Скормить строки хвоста → обновлённое состояние (тот же словарь).

    Фраза отказа кладёт id в ожидание; строка падения с ТЕМ ЖЕ id и малой длительностью
    подтверждает отказ ДЕЙСТВИЕМ и увеличивает счётчик. Ожидание живёт PAIR_WINDOW строк —
    пережитый рефрешем 401 так и уходит в никуда, ничего не ломая."""
    pending = state.setdefault("pending", {})
    for line in lines or []:
        state["seen"] = state.get("seen", 0) + 1
        for key in list(pending):
            pending[key] += 1
            if pending[key] > PAIR_WINDOW:
                pending.pop(key, None)
        if REVOKED_PHRASE in line:
            m = _ID_RE.search(line)
            if m:
                pending[m.group(1)] = 0
            continue
        if _FAILED_MARK not in line or not pending:
            continue
        d = _DURATION_RE.search(line)
        if not d or int(d.group(1)) > FAST_FAIL_MAX:
            continue                       # упала не «за секунды» — это не тот отказ
        for mid in _ID_RE.findall(line):
            hit = [k for k in pending if ids_match(k, mid)]
            if hit:
                for k in hit:
                    pending.pop(k, None)
                state["failures"] = state.get("failures", 0) + 1
                break
    return state


def scan(path, state, opener=None, sizer=None):
    """Один проход по логу ветки: дочитать хвост → скормить детектору. → состояние."""
    lines, offset, truncated = read_new(path, state.get("offset", 0),
                                        opener=opener, sizer=sizer)
    if truncated:                          # новая жизнь процесса — старые улики недействительны
        state["pending"] = {}
        state["failures"] = 0
    state["offset"] = offset
    return feed(lines, state)


def should_restart(state, needed=None):
    """Пора ли рестартовать ветку rc-server."""
    n = FAILS_NEEDED if needed is None else needed
    return int((state or {}).get("failures", 0)) >= max(1, n)


def describe(state):
    """Короткая причина для лога/карточки владельцу."""
    return ("протухшая авторизация: %d новых сессий подряд убито дословным "
            "«%s» + падение за секунды" % (int((state or {}).get("failures", 0)), REVOKED_PHRASE))
