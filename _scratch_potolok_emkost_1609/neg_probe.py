# -*- coding: utf-8 -*-
"""Отрицательная проверка п.6: ТЕ ЖЕ входы — на shtab_box.py до правки (HEAD, копия before/) и
после (рабочее дерево). Сквозной хвост — через стенд моста (stand.py): что доезжает до судьи."""
import datetime
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import stand                                   # noqa: E402  (ставит TESTING и путь репо)
import done_judge_pc as dj                     # noqa: E402
import shtab_box as new                        # noqa: E402

spec = importlib.util.spec_from_file_location("shtab_box_before", os.path.join(HERE, "before", "shtab_box.py"))
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def gate(mod, body, day):
    try:
        return mod.check({"key": "kk1", "body": body}, day)
    except TypeError:                          # старая подпись check(block) без дня
        return mod.check({"key": "kk1", "body": body})


DAY = "2026-09-16"
STALE = (datetime.date.fromisoformat(new.CAPACITY_RECORD["date"])
         + datetime.timedelta(days=new.CAPACITY_TTL_DAYS + 1)).isoformat()
print("sha256 до  (HEAD 8b6cb0f):", sha(os.path.join(HERE, "before", "shtab_box.py")))
print("sha256 после (дерево)    :", sha(os.path.join(stand.REPO, "shtab_box.py")))
print("BODY_MAX до/после:", old.BODY_MAX, new.BODY_MAX)

cases = [("тело 4500 u16 (ровно потолок)", stand.make_body(4500, "N4500"), DAY),
         ("тело 4501 u16", stand.make_body(4501, "N4501"), DAY),
         ("тело 4811 (живое 15.09)", stand.make_body(4811, "N4811"), DAY),
         ("тело 4926 (живое)", stand.make_body(4926, "N4926"), DAY),
         ("тело 5608 (живое)", stand.make_body(5608, "N5608"), DAY),
         ("эмодзи×40, u16 4501, len 4461", stand.make_body(4501, "E4501", emoji=40), DAY),
         ("тело 3742, день %s (протухла)" % STALE, stand.make_body(3742, "S3742"), STALE)]
out = []
for label, body, day in cases:
    a, b = gate(old, body, day), gate(new, body, day)
    row = {"вход": label, "до": [a[0], a[1]], "после": [b[0], b[1], b[2][:150]], "другой ответ": a[:2] != b[:2]}
    out.append(row)
    print(json.dumps(row, ensure_ascii=False))

print("== сквозь стенд моста: что видит судья")
st = stand.Stand()
try:
    for mod, name in ((old, "до"), (new, "после")):
        for n in (4500, 4700):
            body = stand.make_body(n, "W%d%s" % (n, "o" if mod is old else "n"))
            key = "k" * 40
            text = mod.task_text({"key": key, "body": body, "name": mod.doc_name(key), "id": "x" * 44}, DAY)
            if text is None:
                print("%-5s тело %d: ворота ОТКАЗАЛИ (%s) — в очередь не ушло" % (name, n, gate(mod, body, DAY)[1]))
                continue
            ok, tid, err = st.q.place_task(text, lane="pc")
            items = st.bc.get_pending("new").get("items") or []
            got = [str(i.get("task_text") or "") for i in items if str(i.get("id")) == str(tid)][0]
            addr = dj.read_address(got)
            print("%-5s тело %d: ряд %d u16 → доехало %d, целиком=%s, судья: %s"
                  % (name, n, stand.u16(text), stand.u16(got), got == text,
                     "адрес %r" % addr["words"] if addr else "адрес результата не назван задачей"))
finally:
    st.nb.close()
with open(os.path.join(HERE, "neg_probe_result.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=1)
