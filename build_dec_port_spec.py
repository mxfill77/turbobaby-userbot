# -*- coding: utf-8 -*-
# build_dec_port_spec.py — [шаг 3/5 родитель 221] эталон порт-спеки в docs/dec_port_spec.md.
# Через Bridge read_doc (BRIDGE_URL+токен из .env, образец — cowork_log_append.py) читает
# cc_log из Brain, дословно извлекает ОБЕ записи ПОРТ-СПЕКА (DONE 2026-07-11 16:38 и 16:43 UTC),
# кладёт в файл и ПРОГРАММНО сверяет целиковое вхождение каждой записи. Bridge — ТОЛЬКО чтение.
import os, re, json, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
OUT_PATH = os.path.join(HERE, "docs", "dec_port_spec.md")
DOC_NAME = "cc_log"

# верхнеуровневые границы записей журнала (маркер + дата-штамп UTC в начале строки)
ENTRY_RE = re.compile(r"(?m)^(?:DONE|PLAN|ASK|NOTE) \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC")
STAMP_1 = "DONE 2026-07-11 16:38 UTC"   # часть 1/2
STAMP_2 = "DONE 2026-07-11 16:43 UTC"   # часть 2/2


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
    # read_doc в doGet Bridge → GET, токен в query (в логи не печатаем)
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and key in obj and isinstance(obj[key], str):
            return obj[key]
    return None


def extract_entry(text, stamp):
    """Дословный кусок записи: от строки со штампом до следующей верхнеуровневой границы."""
    m = re.search(r"(?m)^" + re.escape(stamp), text)
    if not m:
        raise RuntimeError("запись не найдена в cc_log: " + stamp)
    start = m.start()
    nxt = ENTRY_RE.search(text, m.end())
    end = nxt.start() if nxt else len(text)
    return text[start:end].rstrip()  # без хвостового разделителя "  \n"


def main():
    env = load_env(ENV_PATH)
    url = env.get("BRIDGE_URL"); token = env.get("BRIDGE_TOKEN")
    if not url or not token:
        raise SystemExit("ОШИБКА: нет BRIDGE_URL/BRIDGE_TOKEN в .env")

    r = get(url, {"action": "read_doc", "token": token, "name": DOC_NAME})
    if not (isinstance(r, dict) and r.get("ok")):
        raise SystemExit("read_doc не ok: " + json.dumps(r, ensure_ascii=False)[:300])
    text = get_text(r)
    if text is None:
        raise SystemExit("read_doc ok, но текст не найден: " + json.dumps(r, ensure_ascii=False)[:300])

    rec1 = extract_entry(text, STAMP_1)   # часть 1/2 (16:38)
    rec2 = extract_entry(text, STAMP_2)   # часть 2/2 (16:43)

    header = (
        "# dec_port_spec — эталон порт-спеки мозга декомпозера\n\n"
        "Источник: Brain-док `cc_log`, скачано через Bridge `read_doc` (read-only,\n"
        "образец вызова — `cowork_log_append.py`). Две записи ПОРТ-СПЕКА от 2026-07-11 (UTC),\n"
        "извлечены дословно и сверены на целиковое вхождение:\n\n"
        "- часть 1/2 — `DONE 2026-07-11 16:38 UTC` (%d символов)\n"
        "- часть 2/2 — `DONE 2026-07-11 16:43 UTC` (%d символов)\n\n"
        "---\n\n"
        "## ПОРТ-СПЕКА часть 1/2 — DONE 2026-07-11 16:38 UTC\n\n"
        % (len(rec1), len(rec2))
    )
    body = header + rec1 + "\n\n---\n\n" + \
        "## ПОРТ-СПЕКА часть 2/2 — DONE 2026-07-11 16:43 UTC\n\n" + rec2 + "\n"

    # ПРОГРАММНАЯ СВЕРКА: целиковое вхождение каждой записи в итоговый файл
    for tag, rec in (("часть 1/2 (16:38)", rec1), ("часть 2/2 (16:43)", rec2)):
        if rec not in body:
            raise SystemExit("СВЕРКА ПРОВАЛЕНА: запись не входит целиком — " + tag)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(body)

    # финальная перечитка файла с диска + повторная сверка вхождения
    with open(OUT_PATH, "r", encoding="utf-8") as f:
        disk = f.read()
    ok1 = rec1 in disk
    ok2 = rec2 in disk
    print("часть 1/2 (16:38): %d символов, вхождение в файл: %s" % (len(rec1), ok1))
    print("часть 2/2 (16:43): %d символов, вхождение в файл: %s" % (len(rec2), ok2))
    print("файл: %s (%d символов)" % (OUT_PATH, len(disk)))
    if not (ok1 and ok2):
        raise SystemExit("ОШИБКА: сверка вхождения на диске провалена")
    print("СВЕРКА OK: обе записи входят целиком")


if __name__ == "__main__":
    main()
