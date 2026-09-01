# -*- coding: utf-8 -*-
"""Замер разведки: сверка отправленного текста по sha256 и его размер.

Читает файлы ответов канала manus из docs/review_inbox, берёт из них
`sha256 отправленного текста` и `пакет`, строит конверт review_send.build_prompt
над телом пакета и сверяет хеш. Совпал — размер отправленного известен ТОЧНО.
Ничего не пишет и никуда не ходит.
"""
import hashlib
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
while ROOT != os.path.dirname(ROOT) and not os.path.exists(os.path.join(ROOT, "review_send.py")):
    ROOT = os.path.dirname(ROOT)
sys.path.insert(0, ROOT)
import review_send  # noqa: E402

INBOX = os.path.join(ROOT, "docs", "review_inbox")

_RE_PROMPT = re.compile(r"^sha256 отправленного текста: `([0-9a-f]+)`", re.M)
_RE_PACK = re.compile(r"^пакет: `([^`]+)`", re.M)
_RE_REASON = re.compile(r"^причина: `([^`]+)`", re.M)
_RE_ANSWER = re.compile(r"^ответ: (\d+) знаков", re.M)


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


rows = []
for name in sorted(os.listdir(INBOX)):
    if not name.endswith("-manus.md"):
        continue
    body = read(os.path.join(INBOX, name))
    m_prompt = _RE_PROMPT.search(body)
    m_pack = _RE_PACK.search(body)
    m_reason = _RE_REASON.search(body)
    if not (m_prompt and m_pack and m_reason):
        continue
    want = m_prompt.group(1)
    pack_rel = m_pack.group(1)
    pack_abs = os.path.join(ROOT, pack_rel.replace("/", os.sep))
    if not os.path.exists(pack_abs) or want == "—":
        rows.append((m_reason.group(1), pack_rel, None, None, "пакета нет на диске"))
        continue
    pack_text = read(pack_abs)
    prompt = review_send.build_prompt(pack_text)
    got = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    m_ans = _RE_ANSWER.search(body)
    rows.append(
        (
            m_reason.group(1),
            pack_rel,
            len(prompt),
            len(prompt.encode("utf-8")),
            "СВЕРЕН" if got == want else "хеш РАЗОШЁЛСЯ (%s ≠ %s)" % (got[:12], want[:12]),
        )
    )

print("причина        | знаков | байт  | сверка хеша | пакет")
for reason, pack, chars, nbytes, note in sorted(rows, key=lambda r: (r[0], -(r[2] or 0))):
    print(
        "%-14s | %6s | %5s | %-11s | %s"
        % (reason, chars if chars is not None else "—", nbytes if nbytes is not None else "—", note, os.path.basename(pack))
    )

ok = [r for r in rows if r[0] == "ok" and r[2]]
bad = [r for r in rows if r[0] == "http_400" and r[2]]
acc = [r for r in rows if r[0] == "accepted_no_answer" and r[2]]
print("")
print("ok            : n=%d  знаков min=%s max=%s" % (len(ok), min(r[2] for r in ok), max(r[2] for r in ok)))
print("accepted      : n=%d  знаков min=%s max=%s" % (len(acc), min(r[2] for r in acc), max(r[2] for r in acc)))
print("http_400      : n=%d  знаков min=%s max=%s" % (len(bad), min(r[2] for r in bad), max(r[2] for r in bad)))
print("сверено хешей : %d из %d" % (sum(1 for r in rows if r[4] == "СВЕРЕН"), len(rows)))
