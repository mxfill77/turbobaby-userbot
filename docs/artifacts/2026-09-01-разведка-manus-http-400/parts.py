# -*- coding: utf-8 -*-
"""Замер цены закрытия: во сколько ЧАСТЕЙ живой код режет каждый из 10 отбитых пакетов.

Зовёт боевую review_send_run.manus_parts на тех же текстах, которые канал отбил
кодом 400. Ничего не отправляет: чистая функция над строкой.
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
while ROOT != os.path.dirname(ROOT) and not os.path.exists(os.path.join(ROOT, "review_send.py")):
    ROOT = os.path.dirname(ROOT)
sys.path.insert(0, ROOT)
os.environ.setdefault("TURBOBABY_TEST_LOGS", "")
import review_send  # noqa: E402
import review_send_run  # noqa: E402

INBOX = os.path.join(ROOT, "docs", "review_inbox")
_RE_PACK = re.compile(r"^пакет: `([^`]+)`", re.M)
_RE_REASON = re.compile(r"^причина: `([^`]+)`", re.M)


def read(p):
    with io.open(p, encoding="utf-8") as fh:
        return fh.read()


print("потолок сообщения MANUS_INLINE_MAX = %d знаков, рамка части = %d"
      % (review_send_run.MANUS_INLINE_MAX, review_send_run.MANUS_PART_OVERHEAD))
print("")
print("причина        | знаков | частей | max часть | склейка = исходник")
total_parts = 0
n400 = 0
for name in sorted(os.listdir(INBOX)):
    if not name.endswith("-manus.md"):
        continue
    body = read(os.path.join(INBOX, name))
    m_pack, m_reason = _RE_PACK.search(body), _RE_REASON.search(body)
    if not (m_pack and m_reason):
        continue
    pack_abs = os.path.join(ROOT, m_pack.group(1).replace("/", os.sep))
    if not os.path.exists(pack_abs):
        continue
    prompt = review_send.build_prompt(read(pack_abs))
    parts = review_send_run.manus_parts(prompt)
    reason = m_reason.group(1)
    # склейка тел частей обязана дать исходный текст знак в знак
    glued = "".join(review_send_run.manus_chunks(prompt, max(200, review_send_run.MANUS_CHUNK_CHARS - review_send_run.MANUS_PART_OVERHEAD))) if len(parts) > 1 else parts[0]
    print("%-14s | %6d | %6d | %9d | %s"
          % (reason, len(prompt), len(parts), max(len(p) for p in parts), "да" if glued == prompt else "НЕТ"))
    if reason == "http_400":
        total_parts += len(parts)
        n400 += 1
print("")
print("итог по 10 отбитым: %d захода вместо %d (было по 1 на пакет)" % (total_parts, n400))
print("средняя цена одного пакета: %.1f захода" % (total_parts / float(n400)))
