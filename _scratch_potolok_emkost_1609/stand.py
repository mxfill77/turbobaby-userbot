# -*- coding: utf-8 -*-
"""СТЕНД ЁМКОСТИ СТРОКИ ОЧЕРЕДИ (задание 62-n3, 16.09.2026). Место временного — этот каталог.

Путь пробного тела — ТОТ ЖЕ, каким едет задание ящика, до места, где его читает судья:

  shtab_box.task_text (настоящий)                              ← шапка ящика + тело
  → shtab_box_run.Queue.place_task (настоящий)
  → pc_orchestrator.enqueue_pc_task (настоящий, .strip())
  → pc_orchestrator.Bridge.enqueue_task → bridge_http (настоящие; JSON-тело POST)
  → doPost → enqueueTask_ (НАСТОЯЩИЙ JS моста, копия, node vm)   ← лист очереди в памяти
  → Bridge.get_pending → doGet → getPending_ (настоящие)
  → task_text ряда = то, что _process_one отдаёт исполнителю и судье
  → done_judge_pc.read_address (настоящий)                     ← место, где читает судья

Сети нет: вместо сокета — opener, передающий байты запроса в node-процесс. Живой очереди,
живых заданий и мозга стенд не касается. Пробные тела — свои строки с меткой STEND.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.environ["TESTING"] = "1"                 # логи демона — в тестовый файл, а не в боевой
os.environ["TURBOBABY_TEST_LOGS"] = "1"

import done_judge_pc as dj                 # noqa: E402
import pc_orchestrator as o                # noqa: E402
import shtab_box as sb                     # noqa: E402
import shtab_box_run as sbr                # noqa: E402

BRIDGE_COPY = os.path.join(REPO, "tmp", "drive_public_recon_20260826", "bridge_head")
DAY = "2026-09-16"
LIVE_ID = "1MRjG8SUAhh5vlR5htIM4E0QfhYx-Kwsw"          # форма живого file id (33 симв.) — только длина
LIVE_KEY = "62-n3-potolok-emkost.1609"


def u16(s):
    return len(s.encode("utf-16-le")) // 2


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Resp:
    def __init__(self, body):
        self.status = 200
        self.headers = {}
        self._b = body

    def read(self):
        return self._b

    def getcode(self):
        return 200

    def close(self):
        pass


class NodeBridge:
    """opener для bridge_http: байты запроса → node с настоящим кодом моста → ответ."""

    def __init__(self):
        self.p = subprocess.Popen(["node", os.path.join(HERE, "stand_bridge.js"), BRIDGE_COPY],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  encoding="utf-8", bufsize=1)
        self.posts = []

    def open(self, req, timeout=None):
        if req.get_method() == "POST":
            contents = req.data.decode("utf-8")
            self.posts.append(len(req.data))
            msg = {"method": "POST", "contents": contents}
        else:
            q = urllib.parse.urlsplit(req.full_url).query
            msg = {"method": "GET", "parameter": {k: v[0] for k, v in
                                                  urllib.parse.parse_qs(q, keep_blank_values=True).items()}}
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        ans = json.loads(self.p.stdout.readline())
        if "error" in ans:
            raise RuntimeError("стенд моста: " + ans["error"])
        return Resp(ans["content"].encode("utf-8"))

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=30)


def make_body(n, tag, lane_line=True, emoji=0, ascii_fill=False):
    """Пробное тело РОВНО n единиц UTF-16: запреты + полоса + заполнитель + адрес последней строкой."""
    head = ("ПОЛОСА: пк\n\n" if lane_line else "") + (
        "ЦЕЛЬ: ПРОБНОЕ ТЕЛО СТЕНДА ёмкости строки очереди, не задание.\n\n" + sb.PROHIBITIONS + "\n\n")
    addr = "\n\nАДРЕС РЕЗУЛЬТАТА: файл в docs/artifacts за 16.09 со словами STEND-%s 1609" % tag
    left = n - u16(head) - u16(addr)
    if left < 2 * emoji + 1:
        raise ValueError("тело %d слишком короткое" % n)
    unit = ("proba emkosti stroki ocheredi " if ascii_fill else "проба ёмкости строки очереди ")
    line = (unit * 4)[:99] + "\n"
    fill = "\U0001F4E5" * emoji
    rest = left - 2 * emoji
    fill += (line * (rest // len(line) + 2))[:rest]
    if fill[-1].isspace():
        fill = fill[:-1] + "ж"
    body = head + fill + addr
    assert u16(body) == n, (u16(body), n)
    return body


class Stand:
    def __init__(self):
        self.nb = NodeBridge()
        self.bc = o.Bridge(url="https://stand.invalid/macros/s/STAND/exec", token="stand",
                           opener=self.nb, witness=lambda *a, **k: None)
        shim = types.SimpleNamespace(
            enqueue_pc_task=lambda text, frm="Filipp": o.enqueue_pc_task(text, frm=frm, bridge=self.bc),
            bc=self.bc)
        self.q = sbr.Queue(daemon=shim, root=tempfile.mkdtemp(prefix="stand_q_"))

    def ride(self, body, key=LIVE_KEY, fid=LIVE_ID, tag=""):
        block = {"key": key, "body": body, "name": sb.doc_name(key), "id": fid}
        text = sb.task_text(block, DAY)
        if text is None:
            return {"refused": sb.check(block)}
        ok, tid, err = self.q.place_task(text, lane="pc")
        if not ok:
            raise RuntimeError("постановка не удалась: %s" % err)
        r = self.bc.get_pending("new")
        if not r.get("ok"):
            raise RuntimeError("get_pending: %r" % (r,))
        row = [it for it in r.get("items") or [] if str(it.get("id")) == str(tid)]
        got = str(row[0].get("task_text") or "")
        addr = dj.read_address(got)
        want = dj.read_address(text)
        return {"body_u16": u16(body), "body_py": len(body), "row_u16": u16(text), "row_py": len(text),
                "row_bytes": len(text.encode("utf-8")), "post_bytes": self.nb.posts[-1],
                "back_u16": u16(got), "back_py": len(got), "whole": got == text.strip(),
                "addr": ("нет" if not addr else ("целый" if addr == want else
                                                 "ОБРЕЗАН: слова %r" % addr["words"])),
                "header_u16": u16(text) - u16(body)}


def main():
    orig_max = sb.BODY_MAX
    sb.BODY_MAX = 10 ** 9           # ТОЛЬКО В ЭТОМ ПРОЦЕССЕ: мерим канал, а не ворота
    st = Stand()
    res = {"day": DAY, "bridge_copy": os.path.relpath(BRIDGE_COPY, REPO).replace("\\", "/"),
           "bridge_sha256": {f: sha(os.path.join(BRIDGE_COPY, f)) for f in ("BotData.js", "Bridge.js")},
           "body_max_at_head": orig_max, "runs": {}}
    try:
        def run(label, body, **kw):
            r = st.ride(body, **kw)
            res["runs"][label] = r
            print("%-34s %s" % (label, json.dumps(r, ensure_ascii=False)))
            return r

        print("== 1. грубая сетка (Кириллица, шапка живой формы: ключ %d, id %d)" % (len(LIVE_KEY), len(LIVE_ID)))
        for n in (3000, 3672, 3742, 4200, 4500, 4600, 4700, 4811, 4926, 5000, 5608, 6000):
            run("сетка тело=%d" % n, make_body(n, "g%d" % n))

        print("== 2. сужение: наибольшая целая / наименьшая с потерей хвоста")
        lo, hi = 3000, 6000          # lo целая (п.1), hi резаная (п.1)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if st.ride(make_body(mid, "b%d" % mid))["whole"]:
                lo = mid
            else:
                hi = mid
        a = run("граница: целая тело=%d" % lo, make_body(lo, "L%d" % lo))
        b = run("граница: резаная тело=%d" % hi, make_body(hi, "H%d" % hi))
        res["max_whole_row_u16"] = a["row_u16"]
        res["min_cut_row_u16"] = b["row_u16"]
        cap = a["row_u16"]

        print("== 3. единица счёта")
        hdr = a["header_u16"]
        run("ASCII ряд = cap", make_body(cap - hdr, "A0", ascii_fill=True))
        run("ASCII ряд = cap+1", make_body(cap - hdr + 1, "A1", ascii_fill=True))
        run("эмодзи×40 ряд u16 = cap", make_body(cap - hdr, "E0", emoji=40))
        run("эмодзи×40 ряд u16 = cap+1", make_body(cap - hdr + 1, "E1", emoji=40))

        print("== 4. где пропадает адрес целиком (судья: «адрес не назван»)")
        lo, hi = cap - hdr, cap - hdr + 300          # lo: адрес целый; hi: хвост в 300 единиц срезан
        assert st.ride(make_body(hi, "Z%05d" % hi))["addr"] == "нет"
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if st.ride(make_body(mid, "Z%05d" % mid))["addr"] == "нет":
                hi = mid
            else:
                lo = mid
        run("адрес ещё читается тело=%d" % lo, make_body(lo, "Z%05d" % lo))
        run("адрес исчез с тела=%d" % hi, make_body(hi, "Z%05d" % hi))

        print("== 5. шапка ящика: от чего зависит")
        body = make_body(3000, "h1")
        nolane = make_body(3000, "h2", lane_line=False)
        heads = {}
        for label, key, fid, bd in (
                ("живая форма (ключ 25, id 33, полоса названа)", LIVE_KEY, LIVE_ID, body),
                ("ключ 40, id 33, полоса названа", "k" * sb.KEY_MAX, LIVE_ID, body),
                ("ключ 40, id 44, полоса названа", "k" * sb.KEY_MAX, "x" * 44, body),
                ("ключ 40, id 44, полоса НЕ названа", "k" * sb.KEY_MAX, "x" * 44, nolane)):
            t = sb.task_text({"key": key, "body": bd, "name": sb.doc_name(key), "id": fid}, DAY)
            heads[label] = u16(t) - u16(bd)
            print("   шапка %-48s %d" % (label, heads[label]))
        res["headers_u16"] = heads
    finally:
        st.nb.close()
        sb.BODY_MAX = orig_max
    with io.open(os.path.join(HERE, "stand_result.json"), "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print("ИТОГ: наибольший целый ряд %s u16, наименьший резаный %s u16"
          % (res["max_whole_row_u16"], res["min_cut_row_u16"]))


if __name__ == "__main__":
    main()
