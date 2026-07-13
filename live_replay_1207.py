# -*- coding: utf-8 -*-
"""Живой реплей окна 12.07 (родитель #243, шаг 7/7) через РЕАЛЬНЫЙ claude CLI.
Проверяем три поведения на дословных фразах 12.07:
  (1) XMAX двумя строками (старое / New Gen) в сетке готового черновика;
  (2) черновик БЕЗ утверждений о цвете/наличии (пост-чек перепишет в «уточню»);
  (3) черновик БЕЗ переспросов уже собранного (три «Вот» гео/паспорт/тел).
Bridge НЕ трогаем — прайс-нот строится локальным стаб-getter'ом (как в test_suggest)."""
import test_isolation  # noqa: F401 — TESTING=1, боевой IPC заблокирован, BRIDGE_URL=''
import datetime
import suggest

suggest.SUGGEST_LLM_VIA_CLI = True
suggest.CLI_TIMEOUT = 150
suggest.BRIDGE_URL = ""
suggest.BRIDGE_TOKEN = ""

FAQ = suggest.load_faq()
PB = suggest.load_playbook()

# ---- локальный стаб-getter парка (Календарь НЕ трогаем): два поколения XMAX ----
OLD_XMAX = ["XMAX 300CC GREY PHUKET 4246", "XMAX 300CC BLUE PHUKET 4247", "XMAX 300CC BLACK PHUKET 4248"]
NEW_XMAX = ["XMAX 300CC NEW 2023 PHUKET 7701"]
FLEET = OLD_XMAX + NEW_XMAX + ["NMAX 155CC BLACK PHUKET 4255"]
OLD = (790, 4700, 23700, 5000, True, 8900)
NEW = (939, 5600, 28170, 7000, True, 9900)
NMAX = (450, 2800, 9000, 5000, True, 8500)


def _tar(bike):
    if "NMAX" in bike.upper():
        return NMAX
    if "2023" in bike or "NEW" in bike.upper():
        return NEW
    return OLD


def _getter(params):
    if params.get("action") == "fleet":
        return {"ok": True, "data": {"bikes": [{"name": n} for n in FLEET]}}
    bike = params.get("bike", "")
    ds, de = params.get("date_start"), params.get("date_end")
    days = (datetime.date.fromisoformat(de) - datetime.date.fromisoformat(ds)).days
    d1, d7, d30, dep, ca, cp = _tar(bike)
    total = {1: d1, 7: d7, 30: d30}.get(days, d1)
    return {"ok": True, "data": {"day_price": round(total / max(days, 1)), "total": total,
            "deposit": dep, "available": True, "days": days, "cap_active": ca,
            "cap_price": cp, "text": f"{bike} {days}d {total}"}}


suggest.pricing.PRICING_ACTION = "quote_price"
suggest.pricing.BRIDGE_URL = "https://x"
suggest.pricing.BRIDGE_TOKEN = "t"
suggest.pricing._FLEET_CACHE["data"] = None
suggest.pricing._FLEET_CACHE["ts"] = 0
suggest._sheet_cache.update(key=None, ts=0.0, rows=None)

TODAY = datetime.date(2026, 7, 11)
fails = []


def check(name, cond, detail):
    mark = "OK " if cond else "FAIL"
    print(f"[{mark}] {name}")
    if not cond:
        print("   ", detail.replace("\n", "\n    "))
        fails.append(name)


# ===== (1)+(2) дословная фраза 12.07 «какие модели и цены» =====
phrase = ("Какие марки и модели байков вы предлагаете? Какие у вас цены на аренду? "
          "(Стоимость за день, неделю и месяц для разных моделей.) Требуется ли депозит?")
tr = f"[клиент]: {phrase}"
hints = suggest.extract_booking_hints(tr, today=TODAY)
note = suggest.build_pricing_note(hints, lang="ru", getter=_getter, today=TODAY)
draft = suggest.generate_draft(tr, "ru", FAQ, is_first_contact=True,
                               pricing_note=note, park_models=None, playbook=PB)
print("\n===== ЧЕРНОВИК #1 (модели/цены, живой LLM) =====\n" + draft + "\n")

has_old = "XMAX 300\n• Сутки: 790 ฿" in draft
has_new = "XMAX 300 New Gen\n• Сутки: 939 ฿" in draft
check("XMAX двумя строками (старое поколение 790)", has_old, draft)
check("XMAX двумя строками (New Gen 939)", has_new, draft)

# без утверждений о цвете/наличии в LLM-интро/оутро (сетка КОДА исключается из проверки)
low = draft.lower()
color_words = ["чёрн", "черн", "белый", "синий", "серый", "красн", "цвет доступ", "в наличии есть",
               "сейчас в наличии", "есть в наличии", "доступн"]
# из проверки убираем строку сетки-кода (не про цвет — про цену); ищем ложные утверждения наличия
leak = [w for w in color_words if w in low]
check("нет утверждений о цвете/наличии (или переписаны в «уточню»)",
      not leak or "уточню" in low, f"leak={leak}")


# ===== (3) три «Вот» реплаями 12.07 — без переспросов =====
tr3 = ("[менеджер]: Скиньте гео, паспорт и телефон\n"
       "[клиент]: Вот ↩[в ответ на — клиент: Моя вилла: https://maps.app.goo.gl/abc123XYZ]\n"
       "[клиент]: Вот ↩[в ответ на — клиент: фото (вероятно паспорт)]\n"
       "[клиент]: Вот ↩[в ответ на — клиент: Мой номер +66 81 234 5678]")
draft3 = suggest.generate_draft(tr3, "ru", FAQ, is_first_contact=False,
                                pricing_note="", park_models=None, playbook=PB)
print("\n===== ЧЕРНОВИК #2 (три «Вот», живой LLM) =====\n" + draft3 + "\n")
low3 = draft3.lower()
reask = ["скиньте гео", "пришлите гео", "нужна локация", "пришлите паспорт", "скиньте паспорт",
         "фото паспорта", "ваш номер", "пришлите телефон", "скиньте телефон", "укажите телефон"]
hit = [w for w in reask if w in low3]
check("нет переспросов уже собранного (гео/паспорт/тел)", not hit, f"reask={hit}")
check("подтвердил получение данных",
      any(w in low3 for w in ["получил", "приняли", "принял", "есть", "спасибо"]), draft3)

print("\n===== ИТОГ ЖИВОГО РЕПЛЕЯ 12.07 =====")
print("ПРОВАЛЫ:", fails if fails else "нет — гейт живого прогона пройден")
raise SystemExit(1 if fails else 0)
