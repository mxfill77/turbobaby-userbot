# -*- coding: utf-8 -*-
# cowork_log_append.py — прямая запись строки-итога в мозг (cowork_log) через Bridge.
# Запуск: python cowork_log_append.py "DONE Dispatch <время>: что сделал"
#
# ГАРД УСЫХАНИЯ (класс 17.07.2026, образец — VPS cclog.py:200-203). Запись идёт циклом
# read_doc → склейка → write_doc, то есть документ ПЕРЕЗАПИСЫВАЕТСЯ целиком. Значит любая
# кривизна чтения превращается в потерю журнала: мост отвечает {"ok":true,"text":""} и когда
# док действительно пуст, и когда чтение сорвалось. Прежний гард ловил только None, а ""
# проходил насквозь — и 758 575 символов уехали бы в одну строку. Отсюда два правила:
#   1) пустой текст от моста = ОТКАЗ ЧТЕНИЯ, а не пустой документ;
#   2) новый текст короче старого = аномалия сборки, запись отменяется.
# Оба отказа падают в общий except main(), поэтому строка НЕ теряется, а уходит в спул ровно
# как при таймауте: защита не смеет превращаться в потерю записи.
import os, sys, json, time, socket, datetime, urllib.request, urllib.parse, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
DOC_NAME = "cowork_log"
SPOOL_PATH = os.path.join(HERE, "cowork_log.spool")   # имя по образцу trainer_log.spool

# Мост — веб-приложение Apps Script: /exec отвечает 302 на googleusercontent, и цель редиректа
# ИНОГДА отдаёт 404. 28.07 строка журнала потерялась именно так, хотя живые пробы тем же адресом
# с этого же ПК отвечали 200 (docs/artifacts/2026-07-28-journal-write-fixes.md §1). Значит 404
# здесь ВРЕМЕННЫЙ и подлежит повтору; постоянные ошибки (401/403) повторять бессмысленно.
_RETRY_HTTP = (404, 429, 500, 502, 503, 504)
RETRY_TRIES = int(os.getenv("BRIDGE_RETRY_TRIES", "2") or "2")
RETRY_PAUSE_SEC = float(os.getenv("BRIDGE_RETRY_PAUSE", "1") or "1")


class ShrinkGuard(RuntimeError):
    """Отказ гарда усыхания: писать НЕ будем, чтобы не укоротить журнал.

    Отдельный тип — ради честного заголовка в stderr: это не сбой моста, а наш сознательный
    отказ. Наследуется от RuntimeError, чтобы общий except в main() отработал как обычно и
    положил строку в спул."""


def load_env(path):
    vals = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return vals

def get(url, params):
    # read_doc живёт в doGet Bridge → шлём GET с параметрами в query-строке
    # (ровно как pc_agent._bridge_read_doc). Токен идёт в query, в логи не печатаем.
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and key in obj and isinstance(obj[key], str):
            return obj[key]
    return None

def transient(e):
    """Временный ли сбой (стоит повторить). HTTPError проверяем ПЕРВЫМ: он наследник URLError."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in _RETRY_HTTP
    return isinstance(e, (urllib.error.URLError, TimeoutError, socket.timeout, OSError))


def with_retry(fn, tries=None, _sleep=None):
    """fn() с повтором при ВРЕМЕННОМ сбое. Постоянную ошибку пробрасывает сразу."""
    n = RETRY_TRIES if tries is None else tries
    sleeper = _sleep if _sleep is not None else time.sleep
    for i in range(max(1, n)):
        try:
            return fn()
        except Exception as e:
            if i + 1 >= n or not transient(e):
                raise
            sys.stderr.write(f"повтор после временного сбоя ({type(e).__name__}) — "
                             f"попытка {i + 2}/{n}\n")
            try:
                sleeper(RETRY_PAUSE_SEC)
            except Exception:
                pass


def spool_read(path=None):
    """Отложенные прошлыми сбоями строки (старые сверху). → list[str]."""
    try:
        with open(path or SPOOL_PATH, "r", encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except Exception:
        return []


def spool_add(line, path=None):
    """Отложить строку на диск, чтобы она НЕ потерялась при сбое моста. → путь спула."""
    p = path or SPOOL_PATH
    with open(p, "a", encoding="utf-8") as f:
        f.write(line.replace("\n", " ") + "\n")
    return p


def spool_clear(path=None):
    try:
        os.remove(path or SPOOL_PATH)
    except Exception:
        pass


def compose(new_line, pending, old):
    """Текст дока после дозаписи: новейшая строка сверху, отложенные следом (от новых к старым),
    ниже — прежний текст целиком. Вынесено отдельной функцией, чтобы гард монотонности можно было
    проверить тестом, подменив сборку на «усыхающую»: по построению склейка только ДОБАВЛЯЕТ."""
    return "  \n".join([new_line] + list(reversed(pending))) + "  \n" + old


def main():
    msg = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not msg:
        sys.stderr.write("ОШИБКА: пустая строка-итог\n"); sys.exit(1)
    env = load_env(ENV_PATH)
    url = env.get("BRIDGE_URL"); token = env.get("BRIDGE_TOKEN")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_line = msg if msg.startswith(("DONE", "NOTE", "ASK")) else "DONE " + stamp + ": " + msg
    if not url or not token:
        spool_add(new_line)
        sys.stderr.write("ОШИБКА: нет BRIDGE_URL/BRIDGE_TOKEN в .env\nОТЛОЖЕНО в "
                         + SPOOL_PATH + " (строка не потеряна): " + new_line + "\n")
        sys.exit(1)
    pending = spool_read()      # строки прошлых сбоев — дошлём вместе с новой
    try:
        r = with_retry(lambda: get(url, {"action": "read_doc", "token": token, "name": DOC_NAME}))
        if not (isinstance(r, dict) and r.get("ok")):
            raise RuntimeError("read_doc не ok: " + json.dumps(r, ensure_ascii=False)[:300])
        old = get_text(r)
        if old is None:
            raise RuntimeError("read_doc ok, но текст не найден — НЕ пишу, чтобы не затереть. Ответ: " + json.dumps(r, ensure_ascii=False)[:300])
        # Новейшая строка сверху; отложенные — следом, от новых к старым (порядок журнала цел).
        new_text = compose(new_line, pending, old)
        # ГАРД 1 (класс 17.07): пустой текст — ОТКАЗ ЧТЕНИЯ, а не пустой журнал. Тот же ответ
        # {"ok":true,"text":""} мост отдаёт и при сорванном чтении; писать поверх = затереть док.
        if not old.strip():
            raise ShrinkGuard("read_doc вернул ПУСТОЙ текст — считаю это отказом чтения, а не "
                              "пустым журналом (в доке %d символов, записали бы %d)"
                              % (len(old), len(new_text)))
        # ГАРД 2: монотонность длины (образец cclog.py:200-203). Склейка выше только ДОБАВЛЯЕТ,
        # поэтому срабатывание — аномалия сборки: лучше не записать, чем укоротить журнал.
        if len(new_text) < len(old):
            raise ShrinkGuard("новый текст КОРОЧЕ старого (было %d символов, стало бы %d) — "
                              "запись отменена" % (len(old), len(new_text)))
        w = with_retry(lambda: post(url, {"action": "write_doc", "token": token,
                                          "name": DOC_NAME, "text": new_text}))
        if not (isinstance(w, dict) and w.get("ok")):
            raise RuntimeError("write_doc не ok: " + json.dumps(w, ensure_ascii=False)[:300])
        spool_clear()
        extra = f" | досланы отложенные: {len(pending)}" if pending else ""
        print("OK: записано в мозг, символов:", w.get("chars", "?"), extra)
    except Exception as e:
        spool_add(new_line)
        head = "ГАРД УСЫХАНИЯ (запись отменена)" if isinstance(e, ShrinkGuard) else "ОШИБКА Bridge"
        sys.stderr.write(head + ": " + str(e) + "\nОТЛОЖЕНО (строка НЕ потеряна, уйдёт "
                         "следующим успешным вызовом): " + new_line + "\nСпул: " + SPOOL_PATH + "\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
