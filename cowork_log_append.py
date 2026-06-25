# -*- coding: utf-8 -*-
# cowork_log_append.py — прямая запись строки-итога в мозг (cowork_log) через Bridge.
# Запуск: python cowork_log_append.py "DONE Dispatch <время>: что сделал"
import os, sys, json, datetime, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
DOC_NAME = "cowork_log"

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

def main():
    msg = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not msg:
        sys.stderr.write("ОШИБКА: пустая строка-итог\n"); sys.exit(1)
    env = load_env(ENV_PATH)
    url = env.get("BRIDGE_URL"); token = env.get("BRIDGE_TOKEN")
    if not url or not token:
        sys.stderr.write("ОШИБКА: нет BRIDGE_URL/BRIDGE_TOKEN в .env\nНЕ ЗАПИСАНО: " + msg + "\n"); sys.exit(1)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_line = msg if msg.startswith(("DONE", "NOTE")) else "DONE " + stamp + ": " + msg
    try:
        r = get(url, {"action": "read_doc", "token": token, "name": DOC_NAME})
        if not (isinstance(r, dict) and r.get("ok")):
            raise RuntimeError("read_doc не ok: " + json.dumps(r, ensure_ascii=False)[:300])
        old = get_text(r)
        if old is None:
            raise RuntimeError("read_doc ok, но текст не найден — НЕ пишу, чтобы не затереть. Ответ: " + json.dumps(r, ensure_ascii=False)[:300])
        new_text = new_line + "  \n" + old
        w = post(url, {"action": "write_doc", "token": token, "name": DOC_NAME, "text": new_text})
        if not (isinstance(w, dict) and w.get("ok")):
            raise RuntimeError("write_doc не ok: " + json.dumps(w, ensure_ascii=False)[:300])
        print("OK: записано в мозг, символов:", w.get("chars", "?"))
    except Exception as e:
        sys.stderr.write("ОШИБКА Bridge: " + str(e) + "\nНЕ ЗАПИСАНО (сохрани вручную): " + new_line + "\n"); sys.exit(1)

if __name__ == "__main__":
    main()
