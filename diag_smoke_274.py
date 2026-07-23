# -*- coding: utf-8 -*-
"""diag_smoke_274.py — диагностика провала смоук-шага цепи #274 (одноразовый).

Строит ноту и черновик тем же путём, что suggest.runLiveSmoke (_smoke_local_draft),
и печатает промежуточные артефакты: pricing_note (какие служебные блоки привезла),
черновик, чеки. Логи — в консоль (ротация файловых логов занята живыми ботами).
"""
import asyncio
import logging
import sys

import pc_orchestrator  # noqa: F401  (load_dotenv прод-окружения, как у демона)
import suggest

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)

probe = suggest._smoke_default_probe()
lines = suggest._smoke_client_lines(probe)
transcript = "\n".join("[клиент]: " + ln for ln in lines)
print("=== TRANSCRIPT ===")
print(transcript)

hints = suggest.extract_booking_hints(transcript, today=suggest._smoke_today(None))
print("=== HINTS ===")
print({k: v for k, v in hints.items() if v})

note = suggest.build_pricing_note(hints, lang="ru", getter=None,
                                  today=suggest._smoke_today(None))
print("=== NOTE (draft-ветка, живой Bridge) ===")
print(note)
print("=== БЛОКИ НОТЫ ===")
print("QUOTE:", repr(suggest._quote_block_from_note(note)))
print("DELIVERY:", repr(suggest._delivery_block_from_note(note)))

r = asyncio.run(suggest.runLiveSmoke())
print("=== SMOKE STATUS:", r.get("status"), "ok:", r.get("ok"), "===")
for c in r.get("checks") or []:
    print(("OK " if c.get("ok") else "FAIL ") + str(c.get("name")))
print("=== DRAFT ===")
print(r.get("draft"))
