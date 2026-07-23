# -*- coding: utf-8 -*-
"""
kb_master_append_2307.py — одноразовый (23.07.2026): дозаписать в KB_MASTER (Brain-док `index`)
в КОНЕЦ Раздела 3 блок «⭐ 23.07 — ШТОРМ ГАРДА И ИТОГИ ДНЯ».

Канал — Bridge read_doc/write_doc по имени дока (образец вызова — cowork_log_append.py).
ЗАПУСК ДОКТРИНАЛЬНО КРАСНЫЙ: скрипт читает BRIDGE_URL/BRIDGE_TOKEN из конфига с секретами,
поэтому исполняется ТОЛЬКО по «да» владельца:

    venv/Scripts/python.exe kb_master_append_2307.py

Безопасность (fail-safe, как у cowork_log_append):
  • якорная вставка ПЕРЕД разделителем заголовка «РАЗДЕЛ 4» — якорь обязан найтись РОВНО один
    раз, над ним обязана стоять линия «═», выше обязан существовать «РАЗДЕЛ 3»; иначе НЕ пишем;
  • идемпотентность: маркер блока уже в доке → no-op, второй запуск ничего не задвоит;
  • бэкап: старый текст дока целиком ложится в tmp/kb_master_index_before_2307.txt ДО записи
    (откат = write_doc этим текстом);
  • ОБРАТНОЕ ЧТЕНИЕ: после write_doc док читается заново, блок сверяется дословно, маркер
    считается (должен быть ровно 1), печатается фрагмент вокруг вставки — FACT, не «наверное».

Логика вставки покрыта офлайн-тестом tmp/test_kb_master_append.py на снапшоте живого дока
(tmp/kb_index.txt) — сам тест сеть и секреты не трогает.
"""
import os
import re
import sys
import json
import urllib.request
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
BACKUP_PATH = os.path.join(HERE, "tmp", "kb_master_index_before_2307.txt")
DOC_NAME = "index"          # KB_MASTER живёт в манифесте Bridge под именем index
TIMEOUT = 30

MARKER = "⭐ 23.07 — ШТОРМ ГАРДА И ИТОГИ ДНЯ"

BLOCK = "\n".join([
    "- " + MARKER + ":",
    "  • ТРЕНАЖЁР v2 ПРИНЯТ (fa3746a, ПК-репо): корень провала был не в воронке, а в живой форме",
    "    числа в датах клиента («25ого»/«30ое»).",
    "  • KB_trainer_log ПОЛНЫЙ (758f1de, ПК-репо): ретрай+спул TRN-строк, ВСЕ ветки обучения пишутся",
    "    в лог; архив-док ротации заведён — file id 1lOco1SI58UNT0a4-TBuU3yYeZzbPkWUP.",
    "  • ЦЕПЬ #274 ЗАКРЫТА 7/7 (86a9ac8, вариант Б развилки): строки J/доставки клиенту вставляет",
    "    КОД всегда — маркерный [QUOTE]-режим, дедуп #365 снят; смоук цепей PASS 2/2.",
    "  • VPS-ГАРД ПЕРЕСОБРАН (2a46e66, VPS): доктринальный список вместо default-deny, конверты",
    "    живут 24ч, команда в карточке всегда, петля тестов-призраков разорвана.",
    "  • КУРАТОР теперь пишет цели в тему 1160 (инбокс).",
    "  • ЗАМЕР VPS: железо ок; узкое место — таймаут 2700с и proc_gate=2.",
    "  • ПК-ГАРД НА ТОЙ ЖЕ ДОКТРИНЕ (fbd164e): незнакомая команда сама по себе НЕ красная, Allow",
    "    только по доктринальному списку _stays_red.",
    "  • ПЕРЕВЫПУСК ЗАДАЧ ПОСЛЕ ШТОРМА: одометр ТО, чекпоинты, denial_memory, ревизор-чеки.",
])

# Заголовок раздела-якоря строго в начале строки; упоминания «РАЗДЕЛ 4 …» посреди строки
# (например, в навигации Раздела 5) якорем НЕ считаются.
_RE_SECTION4 = re.compile(r"(?m)^РАЗДЕЛ 4\b")


def out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8"))


def err(s, code):
    sys.stderr.buffer.write((s + "\n").encode("utf-8"))
    sys.exit(code)


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
    # read_doc живёт в doGet Bridge → GET, токен в query, в логи не печатаем
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(data=data, url=url,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_text(obj):
    for key in ("text", "content", "fileContent", "body"):
        if isinstance(obj, dict) and key in obj and isinstance(obj[key], str):
            return obj[key]
    return None


def insert_block(text, block=BLOCK, marker=MARKER):
    """Якорная вставка блока в конец Раздела 3 (перед линией «═» заголовка «РАЗДЕЛ 4»).
    → новый текст; None = маркер уже в доке (идемпотентный no-op); ValueError = якорь не
    распознан однозначно — НЕ пишем (лучше остановка, чем запись мимо структуры)."""
    if marker in text:
        return None
    hits = list(_RE_SECTION4.finditer(text))
    if len(hits) != 1:
        raise ValueError("якорь 'РАЗДЕЛ 4' в начале строки найден %d раз (ожидал 1)" % len(hits))
    head = text[:hits[0].start()]
    nl = head.rfind("\n")
    if nl < 0:
        raise ValueError("над 'РАЗДЕЛ 4' нет предыдущей строки — формат неожиданный")
    sep_start = head.rfind("\n", 0, nl) + 1          # начало строки-разделителя над заголовком
    sep_line = head[sep_start:nl]
    if not sep_line.strip().startswith("═"):
        raise ValueError("над 'РАЗДЕЛ 4' не линия '═', а %r — формат неожиданный" % sep_line[:40])
    prefix = head[:sep_start]
    if "РАЗДЕЛ 3" not in prefix:
        raise ValueError("'РАЗДЕЛ 3' выше якоря не найден — вставлять некуда")
    return prefix.rstrip("\n") + "\n\n" + block + "\n\n" + text[sep_start:]


def main():
    env = load_env(ENV_PATH)
    url, token = env.get("BRIDGE_URL"), env.get("BRIDGE_TOKEN")
    if not url or not token:
        err("ОШИБКА: нет BRIDGE_URL/BRIDGE_TOKEN в конфиге", 1)
    r = get(url, {"action": "read_doc", "token": token, "name": DOC_NAME})
    if not (isinstance(r, dict) and r.get("ok")):
        err("read_doc не ok: " + json.dumps(r, ensure_ascii=False)[:300], 2)
    old = get_text(r)
    if old is None:
        err("read_doc ok, но текст не найден — НЕ пишу, чтобы не затереть", 2)
    out("прочитан %s: %d символов" % (DOC_NAME, len(old)))
    try:
        new = insert_block(old)
    except ValueError as e:
        err("ЯКОРЬ НЕ ПРОШЁЛ: %s — док НЕ тронут" % e, 3)
    if new is None:
        out("блок «%s» уже в доке — no-op, второй раз не пишу" % MARKER)
        return
    with open(BACKUP_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(old)
    out("бэкап старого текста: tmp/kb_master_index_before_2307.txt (%d символов)" % len(old))
    w = post(url, {"action": "write_doc", "token": token, "name": DOC_NAME, "text": new})
    if not (isinstance(w, dict) and w.get("ok")):
        err("write_doc не ok: " + json.dumps(w, ensure_ascii=False)[:300], 4)
    out("записано: %s символов (ответ Bridge: chars=%s)" % (len(new), w.get("chars", "?")))
    # ОБРАТНОЕ ЧТЕНИЕ — верификация по живому доку, не по локальной склейке
    r2 = get(url, {"action": "read_doc", "token": token, "name": DOC_NAME})
    back = get_text(r2) if isinstance(r2, dict) and r2.get("ok") else None
    if back is None:
        err("ОБРАТНОЕ ЧТЕНИЕ НЕ УДАЛОСЬ: запись прошла, но верификации нет — проверь глазами", 5)
    if BLOCK not in back:
        err("ОБРАТНОЕ ЧТЕНИЕ: блок в доке НЕ найден дословно — разберись перед повтором", 5)
    if back.count(MARKER) != 1:
        err("ОБРАТНОЕ ЧТЕНИЕ: маркер встречается %d раз (ожидал 1)" % back.count(MARKER), 5)
    i = back.find(MARKER)
    out("ОБРАТНОЕ ЧТЕНИЕ OK: блок дословно на месте (маркер 1 раз, док %d символов)" % len(back))
    out("--- фрагмент вокруг вставки ---")
    out(back[max(0, i - 200):i + len(BLOCK) + 120])


if __name__ == "__main__":
    main()
