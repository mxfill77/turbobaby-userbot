# -*- coding: utf-8 -*-
"""
test_delivery.py — мок-тесты клиента зон доставки (delivery.get_delivery_zones). БЕЗ реальной
сети/Bridge: транспорт инъектируется через _get (как _get у pricing.fleet/quote). Ключевые
инварианты: успех → список зон; ошибка/не-ok/исключение → None (резолвер даст «[уточнить]»);
повторный вызов в пределах TTL берётся из кэша, БЕЗ обращения к сети.
"""

import io
import os
import json
import email.message
import unittest

import delivery

# --- ЖИВАЯ фикстура ответа Bridge delivery_zones_get (снята с прода 2026-07-15) -------------
# Класс-урок (CLAUDE.md): мок внешнего ответа обязан КОПИРОВАТЬ живой формат. Живой Bridge
# отдаёт зоны ПОЗИЦИОННЫМ списком `[name, lat, lon, price, radius]`, а не словарём — раньше
# резолвер (dict-only) молча ронял все зоны. Голдены зон гоняем на этой реальной фикстуре.
_FIXT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fixtures", "delivery_zones_get.live.json")
with open(_FIXT, encoding="utf-8") as _f:
    LIVE_BRIDGE_RESPONSE = json.load(_f)
LIVE_ZONES = LIVE_BRIDGE_RESPONSE["zones"]   # 16 зон, позиционный формат прода


class TestDeliveryZones(unittest.TestCase):
    def setUp(self):
        # изолируем от .env и от кэша между тестами
        self._save = (delivery.BRIDGE_URL, delivery.BRIDGE_TOKEN,
                      delivery._ZONES_CACHE["ts"], delivery._ZONES_CACHE["data"])
        delivery.BRIDGE_URL = "https://x"
        delivery.BRIDGE_TOKEN = "t"
        delivery._ZONES_CACHE["ts"] = 0.0
        delivery._ZONES_CACHE["data"] = None

    def tearDown(self):
        (delivery.BRIDGE_URL, delivery.BRIDGE_TOKEN,
         delivery._ZONES_CACHE["ts"], delivery._ZONES_CACHE["data"]) = self._save

    # ------------------------------- успех -----------------------------------

    def test_success_returns_zones(self):
        zones = [{"name": "Раваи", "price": 200}, {"name": "Патонг", "price": 300}]
        got = delivery.get_delivery_zones(
            _get=lambda p: {"ok": True, "data": {"zones": zones}}, _now=lambda: 1000.0)
        self.assertEqual(got, zones)

    def test_success_flat_shape(self):
        # ответ без вложенного data: {ok, zones:[...]}
        zones = [{"name": "Чалонг", "price": 150}]
        got = delivery.get_delivery_zones(
            _get=lambda p: {"ok": True, "zones": zones}, _now=lambda: 1000.0)
        self.assertEqual(got, zones)

    def test_action_and_token_passed(self):
        seen = {}
        def getter(params):
            seen.update(params)
            return {"ok": True, "data": {"zones": []}}
        delivery.get_delivery_zones(_get=getter, _now=lambda: 1000.0)
        self.assertEqual(seen.get("action"), "delivery_zones_get")
        self.assertEqual(seen.get("token"), "t")

    # ------------------------------- ошибки ----------------------------------

    def test_non_ok_returns_none(self):
        self.assertIsNone(delivery.get_delivery_zones(
            _get=lambda p: {"ok": False}, _now=lambda: 1000.0))

    def test_missing_zones_returns_none(self):
        # ok, но нет списка zones → None (резолвер → «[уточнить]»)
        self.assertIsNone(delivery.get_delivery_zones(
            _get=lambda p: {"ok": True, "data": {}}, _now=lambda: 1000.0))

    def test_exception_swallowed_returns_none(self):
        def boom(p):
            raise RuntimeError("network down")
        self.assertIsNone(delivery.get_delivery_zones(_get=boom, _now=lambda: 1000.0))

    def test_no_bridge_config_returns_none(self):
        delivery.BRIDGE_URL = ""
        delivery.BRIDGE_TOKEN = ""
        self.assertIsNone(delivery.get_delivery_zones(_now=lambda: 1000.0))

    # ------------------------------- кэш/TTL ---------------------------------

    def test_second_call_uses_cache_no_network(self):
        calls = {"n": 0}
        zones = [{"name": "Раваи", "price": 200}]
        def getter(params):
            calls["n"] += 1
            return {"ok": True, "data": {"zones": zones}}
        t0 = 1000.0
        first = delivery.get_delivery_zones(_get=getter, _now=lambda: t0)
        # в пределах TTL: второй вызов НЕ должен трогать сеть (getter не вызывается снова)
        second = delivery.get_delivery_zones(_get=getter, _now=lambda: t0 + 60)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first, zones)
        self.assertEqual(second, zones)

    def test_cache_expires_after_ttl(self):
        calls = {"n": 0}
        def getter(params):
            calls["n"] += 1
            return {"ok": True, "data": {"zones": [{"name": f"z{calls['n']}"}]}}
        t0 = 1000.0
        delivery.get_delivery_zones(_get=getter, _now=lambda: t0)
        # за пределами TTL → повторный поход в сеть
        delivery.get_delivery_zones(_get=getter, _now=lambda: t0 + delivery.ZONES_TTL + 1)
        self.assertEqual(calls["n"], 2)

    def test_error_does_not_poison_cache(self):
        # успех кэшируется; последующая ошибка в пределах TTL отдаёт свежий кэш, не None
        good = [{"name": "Раваи", "price": 200}]
        t0 = 1000.0
        delivery.get_delivery_zones(_get=lambda p: {"ok": True, "data": {"zones": good}},
                                    _now=lambda: t0)
        def boom(p):
            raise RuntimeError("down")
        still = delivery.get_delivery_zones(_get=boom, _now=lambda: t0 + 60)
        self.assertEqual(still, good)   # валидные зоны не затёрты провалом Bridge


class TestResolveMapsLink(unittest.TestCase):
    """Ссылка Google Maps → (lat, lon) | None. Сеть замокана через _expand (короткие
    ссылки НЕ ходят в интернет). На каждый формат координат — позитив, на place/битые — None."""

    PHUKET = (7.8804, 98.3923)  # ориентир: точка на Пхукете (lat≈7.88, lon≈98.39)

    def _assert_close(self, got, want):
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got[0], want[0], places=4)
        self.assertAlmostEqual(got[1], want[1], places=4)

    # ------------------------- прямые координаты в URL -----------------------

    def test_format_q_param(self):
        url = "https://www.google.com/maps?q=7.8804,98.3923"
        self._assert_close(delivery.resolve_maps_link(url), self.PHUKET)

    def test_format_at(self):
        url = "https://www.google.com/maps/@7.8804,98.3923,17z"
        self._assert_close(delivery.resolve_maps_link(url), self.PHUKET)

    def test_format_percent2c_encoded(self):
        # запятая закодирована как %2C (частый вид при пересылке)
        url = "https://maps.google.com/?q=7.8804%2C98.3923"
        self._assert_close(delivery.resolve_maps_link(url), self.PHUKET)

    def test_format_3d4d_pin(self):
        # точный пин места в data-хвосте развёрнутого URL
        url = ("https://www.google.com/maps/place/Villa/@7.9,98.4,17z/"
               "data=!3m1!4b1!3d7.8804!4d98.3923")
        self._assert_close(delivery.resolve_maps_link(url), self.PHUKET)

    def test_format_query_param_alias(self):
        url = "https://www.google.com/maps/search/?api=1&query=7.8804,98.3923"
        self._assert_close(delivery.resolve_maps_link(url), self.PHUKET)

    def test_truncated_messenger_tail(self):
        # мессенджер обрезал длинный хвост после координат — точка ещё в query
        url = "https://www.google.com/maps?q=7.8804,98.3923&z=17&hl=ru&ent"  # хвост &entry=… срезан
        self._assert_close(delivery.resolve_maps_link(url), self.PHUKET)

    def test_truncated_tail_mid_coordinate(self):
        # хвост срезан ПОСЛЕ валидной дробной части lon — координата всё ещё парсится
        url = "https://www.google.com/maps?q=7.8804,98.39"
        got = delivery.resolve_maps_link(url)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got[0], 7.8804, places=3)
        self.assertAlmostEqual(got[1], 98.39, places=2)

    def test_direct_coords_do_not_hit_network(self):
        # если координаты уже в ссылке — _expand НЕ должен вызываться
        calls = {"n": 0}
        def expander(u):
            calls["n"] += 1
            return u
        delivery.resolve_maps_link("https://maps.google.com/?q=7.88,98.39", _expand=expander)
        self.assertEqual(calls["n"], 0)

    # ------------------------- короткие ссылки (мок сети) --------------------

    def test_short_link_expands_to_coords(self):
        short = "https://maps.app.goo.gl/AbCdEf123"
        final = "https://www.google.com/maps/place/X/@7.8804,98.3923,17z/data=!3d7.8804!4d98.3923"
        got = delivery.resolve_maps_link(short, _expand=lambda u: final)
        self._assert_close(got, self.PHUKET)

    def test_short_link_gooogl_expands(self):
        short = "https://goo.gl/maps/xyz"
        got = delivery.resolve_maps_link(short, _expand=lambda u: "https://maps.google.com/?q=7.88,98.39")
        self.assertIsNotNone(got)

    def test_short_link_calls_expander_with_url(self):
        seen = {}
        short = "https://maps.app.goo.gl/Zzz"
        def expander(u):
            seen["u"] = u
            return "https://maps.google.com/?q=7.88,98.39"
        delivery.resolve_maps_link(short, _expand=expander)
        self.assertEqual(seen.get("u"), short)

    # ------------------------------- None-случаи -----------------------------

    def test_short_link_place_without_coords_is_none(self):
        # развернулась в place-ссылку без координат (ни в URL, ни в теле) → None
        short = "https://maps.app.goo.gl/NoCoords"
        final = "https://www.google.com/maps/place/Some+Cafe/"
        self.assertIsNone(delivery.resolve_maps_link(short, _expand=lambda u: final))

    # ------- ЖИВОЙ place-линк «Meat Point»: в теле ТОЛЬКО вьюпорт-центроид (ASK #51) -------

    # Реальный кейс из tmp/geo-recon.md: короткая share-ссылка на объект разворачивается в
    # place-URL, где координат НЕТ вовсе (только текстовый адрес + hex-CID). В теле единственная
    # координатная пара — `center=8.0407335,98.3433216` статической карты, т.е. ВЬЮПОРТ-ЦЕНТРОИД
    # (центр картинки), а НЕ пин заведения. Решение ASK #51: по центроиду цену не называем —
    # нет точного пина `!3d!4d` в теле ⇒ ссылка честно уходит в [уточнить].
    GEO_RECON_FINAL_URL = (
        "https://www.google.com/maps/place/79,+Meat+Point+%7C+steaks,+burgers,+skewers,"
        "+79+Soi+Saiyuan,+Mueang,+Phuket,+83100/data=!4m2!3m1!1s0x30502f24a5443265:"
        "0xa4cc15728db01dbd!18m1!1e1?utm_source=mstt_1&entry=gps&coh=192189&g_st=ac"
    )
    # Фрагмент ЖИВОГО тела: единственная пара — center= статической карты (вьюпорт-центроид).
    GEO_RECON_BODY = (
        "<html>…\"https://maps.googleapis.com/maps/api/staticmap?"
        "center=8.0407335%2C98.3433216&zoom=16&size=800x600\"…</html>"
    )
    GEO_RECON_CENTROID = (8.0407335, 98.3433216)   # НЕ пин — вьюпорт; цену по нему НЕ даём

    def test_golden_meatpoint_centroid_body_is_none(self):
        # ГОЛДЕН ASK #51 (живая ссылка Meat Point): в конечном URL координат нет вовсе, а в теле —
        # лишь вьюпорт-центроид center=. Точного пина `!3d!4d` нет → resolve_maps_link=None
        # (честный [уточнить]). Центроид НЕ выдаём как точку клиента.
        self.assertIsNone(delivery._parse_coords_from_url(self.GEO_RECON_FINAL_URL))
        # тело с одним лишь center= НЕ даёт координат: из тела берём только точный пин `!3d!4d`
        self.assertIsNone(delivery._parse_pin_from_body(self.GEO_RECON_BODY))
        short = "https://maps.app.goo.gl/c4G4B3sNrfJZBSue6?g_st=ac"
        page = self.GEO_RECON_FINAL_URL + "\n" + self.GEO_RECON_BODY
        self.assertIsNone(delivery.resolve_maps_link(short, _expand=lambda u: page))

    def test_place_link_exact_pin_in_body_resolves(self):
        # КОНТРАСТ к центроиду: если в теле place-страницы ЕСТЬ точный пин `!3d!4d` (метаданные
        # place) — берём именно его (надёжный источник), а вьюпорт-центроид center= рядом игнорим.
        short = "https://maps.app.goo.gl/PinInBody"
        body = ("<html>…/data=!3m1!4b1!3d7.771!4d98.327!… "
                "staticmap?center=8.0407335%2C98.3433216&zoom=16…</html>")   # центроид рядом — игнор
        page = self.GEO_RECON_FINAL_URL + "\n" + body
        got = delivery.resolve_maps_link(short, _expand=lambda u: page)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got[0], 7.771, places=3)   # взят пин !3d!4d, НЕ центроид 8.04…
        self.assertAlmostEqual(got[1], 98.327, places=3)

    def test_recon_place_link_no_body_coords_is_none(self):
        # тот же конечный URL, но тело вообще без координат → честный None (fail-safe не ослаблен)
        short = "https://maps.app.goo.gl/c4G4B3sNrfJZBSue6?g_st=ac"
        page = self.GEO_RECON_FINAL_URL + "\n<html>no coordinates here</html>"
        self.assertIsNone(delivery.resolve_maps_link(short, _expand=lambda u: page))

    def test_short_link_expand_raises_is_none(self):
        def boom(u):
            raise RuntimeError("timeout")
        self.assertIsNone(delivery.resolve_maps_link("https://maps.app.goo.gl/x", _expand=boom))

    def test_short_link_expand_returns_non_string_is_none(self):
        self.assertIsNone(delivery.resolve_maps_link("https://maps.app.goo.gl/x", _expand=lambda u: None))

    def test_place_link_without_coords_is_none(self):
        url = "https://www.google.com/maps/place/Villa+Sunrise/"
        self.assertIsNone(delivery.resolve_maps_link(url))

    def test_broken_link_is_none(self):
        self.assertIsNone(delivery.resolve_maps_link("https://example.com/not-a-map"))
        self.assertIsNone(delivery.resolve_maps_link("just some text, no url"))

    def test_non_string_is_none(self):
        self.assertIsNone(delivery.resolve_maps_link(None))
        self.assertIsNone(delivery.resolve_maps_link(12345))
        self.assertIsNone(delivery.resolve_maps_link(["https://maps.app.goo.gl/x"]))

    def test_empty_is_none(self):
        self.assertIsNone(delivery.resolve_maps_link(""))
        self.assertIsNone(delivery.resolve_maps_link("   "))

    def test_out_of_range_coords_is_none(self):
        # lat 98 недопустима (>90) — не путаем порядок lat/lng
        url = "https://www.google.com/maps?q=98.3923,7.8804&extra=999.999,999.999"
        self.assertIsNone(delivery.resolve_maps_link(url))

    def test_non_map_short_domain_not_expanded(self):
        # не короткая ссылка Maps — сеть не дёргаем, координат нет → None
        calls = {"n": 0}
        def expander(u):
            calls["n"] += 1
            return "https://maps.google.com/?q=7.88,98.39"
        self.assertIsNone(delivery.resolve_maps_link("https://bit.ly/abc", _expand=expander))
        self.assertEqual(calls["n"], 0)


class TestResolveDelivery(unittest.TestCase):
    """Чистый резолвер зоны/цены доставки по (lat, lon). Без сети — зоны передаются прямо.
    Инварианты: покрытие радиусом → цена зоны; на границе двух зон побеждает БЛИЖАЙШИЙ якорь;
    за границей в поясе OUT_BELT_KM → 1490; далеко/битая точка/нет зон → маркер [уточнить]."""

    # Два якоря на одной широте (7.88), разнесены по долготе примерно на ~5.5 км.
    # На lon≈98.39 расстояние: до A(98.36)≈3.3 км, до B(98.42)≈2.2 км — B ближе.
    ZONES = [
        {"name": "ЗонаA", "lat": 7.88, "lon": 98.36, "radius_km": 6, "price": 300},
        {"name": "ЗонаB", "lat": 7.88, "lon": 98.42, "radius_km": 6, "price": 500},
    ]

    def test_inside_single_zone(self):
        # точка прямо на якоре ЗонаB → её цена
        r = delivery.resolve_delivery(7.88, 98.42, self.ZONES)
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "ЗонаB")
        self.assertEqual(r["price"], 500)
        self.assertIsNone(r["marker"])

    def test_border_nearest_anchor_wins(self):
        # КЛЮЧЕВОЙ: точка в перекрытии обеих зон (обе покрывают радиусом 6 км), но ближе к B
        # (до A≈4.4 км, до B≈2.2 км). Победитель — ближайший якорь (B, 500), а НЕ первый
        # в списке (A, 300).
        r = delivery.resolve_delivery(7.88, 98.40, self.ZONES)
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "ЗонаB")
        self.assertEqual(r["price"], 500)
        # и если поменять порядок зон — результат тот же (побеждает близость, не позиция)
        r2 = delivery.resolve_delivery(7.88, 98.40, list(reversed(self.ZONES)))
        self.assertEqual(r2["zone"], "ЗонаB")
        self.assertEqual(r2["price"], 500)

    def test_border_nearest_to_A(self):
        # симметрия: точка ближе к A → цена A
        r = delivery.resolve_delivery(7.88, 98.37, self.ZONES)
        self.assertEqual(r["zone"], "ЗонаA")
        self.assertEqual(r["price"], 300)

    def test_out_belt_price(self):
        # маленький радиус: точка вне радиуса, но в пределах радиус+OUT_BELT_KM → 1490
        zones = [{"name": "Z", "lat": 7.88, "lon": 98.39, "radius_km": 1, "price": 300}]
        # ~3 км восточнее якоря: вне радиуса 1, но внутри 1+5
        r = delivery.resolve_delivery(7.88, 98.415, zones)
        self.assertEqual(r["status"], "out_belt")
        self.assertEqual(r["price"], delivery.OUT_BELT_PRICE)
        self.assertEqual(r["price"], 1490)

    def test_far_returns_marker(self):
        # далеко за поясом (другой конец света) → [уточнить]
        r = delivery.resolve_delivery(55.75, 37.62, self.ZONES)  # Москва
        self.assertEqual(r["status"], "uncertain")
        self.assertIsNone(r["price"])
        self.assertEqual(r["marker"], "[уточнить]")

    def test_no_zones_marker(self):
        for z in (None, [], ()):
            r = delivery.resolve_delivery(7.88, 98.39, z)
            self.assertEqual(r["status"], "uncertain")
            self.assertEqual(r["marker"], "[уточнить]")

    def test_bad_point_marker(self):
        # битые/вне диапазона координаты → [уточнить], НЕ падение
        for lat, lon in ((None, 98.39), (7.88, None), (200, 300), ("x", "y")):
            r = delivery.resolve_delivery(lat, lon, self.ZONES)
            self.assertEqual(r["status"], "uncertain")
            self.assertEqual(r["marker"], "[уточнить]")

    def test_zone_without_anchor_skipped(self):
        # зона без якоря/радиуса молча пропускается, валидная — работает
        zones = [
            {"name": "Битая", "price": 999},                       # нет lat/lon/radius
            {"name": "OK", "lat": 7.88, "lon": 98.39, "radius_km": 5, "price": 250},
        ]
        r = delivery.resolve_delivery(7.88, 98.39, zones)
        self.assertEqual(r["zone"], "OK")
        self.assertEqual(r["price"], 250)

    def test_covered_zone_without_price_is_marker(self):
        # покрыт радиусом, но у зоны нет цены → fail-safe [уточнить] (не выдумываем цену)
        zones = [{"name": "БезЦены", "lat": 7.88, "lon": 98.39, "radius_km": 5}]
        r = delivery.resolve_delivery(7.88, 98.39, zones)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")

    def test_nested_anchor_shape(self):
        # вложенный anchor-словарь и alias lng — тоже понимаем
        zones = [{"name": "Nested", "anchor": {"lat": 7.88, "lng": 98.39},
                  "radius_km": 5, "price": 200}]
        r = delivery.resolve_delivery(7.88, 98.39, zones)
        self.assertEqual(r["zone"], "Nested")
        self.assertEqual(r["price"], 200)

    def test_cfg_override(self):
        # cfg перекрывает пояс и цену/маркер
        zones = [{"name": "Z", "lat": 7.88, "lon": 98.39, "radius_km": 1, "price": 300}]
        r = delivery.resolve_delivery(7.88, 98.415, zones,
                                      cfg={"out_belt_price": 1700})
        self.assertEqual(r["status"], "out_belt")
        self.assertEqual(r["price"], 1700)


class TestExtractMapsLink(unittest.TestCase):
    """Из СВОБОДНОГО текста реплики клиента вытащить именно maps-ссылку (не любой URL)."""

    def test_full_google_maps_url(self):
        t = "Вот моя вилла: https://www.google.com/maps?q=7.8804,98.3923 приезжайте"
        self.assertEqual(delivery.extract_maps_link(t),
                         "https://www.google.com/maps?q=7.8804,98.3923")

    def test_short_maps_link(self):
        t = "локация https://maps.app.goo.gl/AbCdEf123 спасибо"
        self.assertEqual(delivery.extract_maps_link(t), "https://maps.app.goo.gl/AbCdEf123")

    def test_short_link_case_preserved(self):
        # токен короткой ссылки регистрозависим — extract НЕ должен его портить
        t = "here https://maps.app.goo.gl/aZ9xQ2"
        self.assertEqual(delivery.extract_maps_link(t), "https://maps.app.goo.gl/aZ9xQ2")

    def test_maps_google_host(self):
        self.assertEqual(delivery.extract_maps_link("https://maps.google.com/?q=7.88,98.39"),
                         "https://maps.google.com/?q=7.88,98.39")

    def test_trailing_punctuation_stripped(self):
        t = "адрес (https://www.google.com/maps?q=7.88,98.39)."
        self.assertEqual(delivery.extract_maps_link(t), "https://www.google.com/maps?q=7.88,98.39")

    def test_non_maps_url_ignored(self):
        self.assertIsNone(delivery.extract_maps_link("сайт виллы https://bit.ly/abc и всё"))
        self.assertIsNone(delivery.extract_maps_link("https://example.com/villa"))

    def test_mention_without_url_is_none(self):
        # «вилла/локация» без ссылки — не гео
        self.assertIsNone(delivery.extract_maps_link("моя вилла в Раваи, локация рядом с пляжем"))

    def test_first_of_several(self):
        t = ("https://example.com/x потом "
             "https://maps.app.goo.gl/First далее https://maps.google.com/?q=1.1,2.2")
        self.assertEqual(delivery.extract_maps_link(t), "https://maps.app.goo.gl/First")

    def test_non_string_is_none(self):
        self.assertIsNone(delivery.extract_maps_link(None))
        self.assertIsNone(delivery.extract_maps_link(123))


class TestResolveDeliveryFromText(unittest.TestCase):
    """Оркестратор текст→доставка. Сеть/Bridge инъектируются: без ссылки → None; ссылка без
    координат → [уточнить]; координаты+зоны → цена; исключение внутри → None (fail-safe)."""

    ZONES = [{"name": "Раваи", "lat": 7.88, "lon": 98.33, "radius_km": 5, "price": 250}]

    def test_no_link_returns_none(self):
        # нет ссылки → None (доставку не трогаем — сеть/зоны НЕ дёргаем)
        called = {"z": 0}
        def zones():
            called["z"] += 1
            return self.ZONES
        r = delivery.resolve_delivery_from_text("просто текст без ссылки", _get_zones=zones)
        self.assertIsNone(r)
        self.assertEqual(called["z"], 0)

    def test_link_with_coords_and_zones_gives_price(self):
        t = "локация https://www.google.com/maps?q=7.88,98.33"
        r = delivery.resolve_delivery_from_text(t, _get_zones=lambda: self.ZONES)
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "Раваи")
        self.assertEqual(r["price"], 250)

    def test_link_but_no_coords_is_uncertain(self):
        # place-ссылка без координат → [уточнить], НЕ падение и НЕ выдуманная цена
        t = "вот https://www.google.com/maps/place/Villa+Sunrise/"
        r = delivery.resolve_delivery_from_text(t, _get_zones=lambda: self.ZONES)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")

    def test_coords_but_no_zones_is_uncertain(self):
        # координаты есть, но Bridge зоны не отдал (None) → [уточнить]
        t = "https://www.google.com/maps?q=7.88,98.33"
        r = delivery.resolve_delivery_from_text(t, _get_zones=lambda: None)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")

    def test_short_link_expands_via_injected_resolver(self):
        t = "loc https://maps.app.goo.gl/Xyz"
        r = delivery.resolve_delivery_from_text(
            t, _get_zones=lambda: self.ZONES, _resolve_maps=lambda u: (7.88, 98.33))
        self.assertEqual(r["price"], 250)

    def test_exception_inside_returns_none(self):
        def boom():
            raise RuntimeError("bridge down")
        r = delivery.resolve_delivery_from_text(
            "https://www.google.com/maps?q=7.88,98.33", _get_zones=boom)
        self.assertIsNone(r)


class TestResolveDeliveryFromCoords(unittest.TestCase):
    """Гео-ПИН (готовые координаты) → доставка. Зоны инъектируются: координаты+зоны → цена;
    координаты без зон → [уточнить]; битые координаты → None (пина нет); исключение → None."""

    ZONES = [{"name": "Раваи", "lat": 7.88, "lon": 98.33, "radius_km": 5, "price": 250}]

    def test_coords_and_zones_give_price(self):
        r = delivery.resolve_delivery_from_coords(7.88, 98.33, _get_zones=lambda: self.ZONES)
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "Раваи")
        self.assertEqual(r["price"], 250)

    def test_coords_but_no_zones_is_uncertain(self):
        # Bridge зон не отдал → [уточнить] (цену по пину не выдумываем)
        r = delivery.resolve_delivery_from_coords(7.88, 98.33, _get_zones=lambda: None)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")

    def test_far_point_out_of_zones_is_uncertain(self):
        # Бангкок — вне всех зон Пхукета → [уточнить], НЕ случайная цена
        r = delivery.resolve_delivery_from_coords(13.7563, 100.5018, _get_zones=lambda: self.ZONES)
        self.assertEqual(r["status"], "uncertain")

    def test_bad_coords_return_none(self):
        # нет/битые координаты → None (пина по сути нет — не [уточнить])
        self.assertIsNone(delivery.resolve_delivery_from_coords(None, None, _get_zones=lambda: self.ZONES))
        self.assertIsNone(delivery.resolve_delivery_from_coords(999, 999, _get_zones=lambda: self.ZONES))

    def test_exception_inside_returns_none(self):
        def boom():
            raise RuntimeError("bridge down")
        self.assertIsNone(delivery.resolve_delivery_from_coords(7.88, 98.33, _get_zones=boom))


class TestLivePositionalZones(unittest.TestCase):
    """Bug B (трасса #51): живой Bridge отдаёт зоны ПОЗИЦИОННЫМ списком
    `[name, lat, lon, price, radius]`, не словарём — резолвер обязан парсить ОБА формата.
    Гоняем на реальной фикстуре прода (fixtures/delivery_zones_get.live.json), а не на
    идеализированном dict-моке (класс-урок «мок = живой формат»)."""

    def test_extract_zones_from_live_response(self):
        # сырой ответ прода → 16 позиционных зон (первая — Раваи из живого листа)
        zones = delivery._extract_zones(LIVE_BRIDGE_RESPONSE)
        self.assertEqual(len(zones), 16)
        self.assertEqual(zones[0], ["Раваи", 7.771, 98.327, 590, 4])

    def test_positional_zone_helpers(self):
        # парсеры якоря/радиуса/цены понимают позиционный список дословно как из прода
        z = ["Раваи", 7.771, 98.327, 590, 4]
        self.assertEqual(delivery._zone_anchor(z), (7.771, 98.327))
        self.assertEqual(delivery._zone_radius_km(z), 4.0)
        self.assertEqual(delivery._zone_price(z), 590)

    def test_positional_zone_short_list_is_broken(self):
        # список <5 полей — битая зона: молча отфильтровывается (fail-safe, не выдумка)
        self.assertIsNone(delivery._zone_anchor(["Раваи", 7.771]))
        self.assertIsNone(delivery._zone_radius_km(["Раваи", 7.771, 98.327]))

    def test_golden_rawai_point_resolves_590(self):
        # ГОЛДЕН «Раваи → 590»: точка ровно на якоре Раваи из живой фикстуры → зона Раваи, 590.
        # Именно позиционный формат зон — если бы парсер остался dict-only, тут был бы [уточнить].
        r = delivery.resolve_delivery(7.771, 98.327, LIVE_ZONES)
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "Раваи")
        self.assertEqual(r["price"], 590)
        self.assertIsNone(r["marker"])

    def test_golden_dict_and_positional_agree(self):
        # тот же результат на dict-эквиваленте живой зоны — оба формата сходятся
        dict_zone = [{"name": "Раваи", "lat": 7.771, "lon": 98.327,
                      "price": 590, "radius_km": 4}]
        r = delivery.resolve_delivery(7.771, 98.327, dict_zone)
        self.assertEqual((r["zone"], r["price"]), ("Раваи", 590))

    # NB: удалён прежний test_golden_meatpoint_pin_out_belt_on_live_zones — он подавал координату
    # вьюпорт-центроида (8.0407335, 98.3433216) в resolve_delivery и ждал out_belt/1490. Решение
    # ASK #51: по центроиду цену не называем ВООБЩЕ — такая координата до resolve_delivery не
    # доходит (resolve_maps_link отдаёт None → [уточнить]). Голден Meat Point теперь — [уточнить]
    # (см. TestResolveMapsLink.test_golden_meatpoint_centroid_body_is_none и
    # TestDeliveryGoldens.test_golden_meatpoint_centroid_uncertain).


class TestDefaultExpandRedirectChain(unittest.TestCase):
    """Bug A (трасса #51): реальный goo.gl отдаёт `302 Found` + `Location`, а _NoRedirectHandler
    заставляет urllib поднять HTTPError на opener.open (а НЕ вернуть 3xx-ответ). Раньше это
    роняло разворот на первом же 302 → короткие ссылки молча деградировали в None (ложный
    [уточнить]). Проверяем, что _default_expand ловит HTTPError КАК ОТВЕТ, читает Location,
    проходит цепочку до 200 и — для place-ссылки без координат в URL — догружает тело. Механика
    разворота (Bug A) ортогональна ASK #51 и остаётся валидной; но координаты из тела Meat Point
    НЕ извлекаем — там лишь вьюпорт-центроид center= (не пин). Сеть замокана — без интернета."""

    # Живые артефакты кейса «79 Meat Point» (tmp/geo-recon.md): короткая share-ссылка,
    # конечный place-URL (координат в нём НЕТ) и фрагмент живого тела с единственной парой —
    # center= статической карты (ВЬЮПОРТ-ЦЕНТРОИД, не пин), запятая %2C-кодирована как в проде.
    SHORT = "https://maps.app.goo.gl/c4G4B3sNrfJZBSue6?g_st=ac"
    MID = "https://maps.app.goo.gl/_intermediate_hop_"
    FINAL = ("https://www.google.com/maps/place/79,+Meat+Point+%7C+steaks,+burgers,+skewers,"
             "+79+Soi+Saiyuan,+Mueang,+Phuket,+83100/data=!4m2!3m1!1s0x30502f24a5443265:"
             "0xa4cc15728db01dbd!18m1!1e1?utm_source=mstt_1&entry=gps&g_st=ac")
    BODY = ('<html>…<meta content="https://maps.googleapis.com/maps/api/staticmap?'
            'center=8.0407335%2C98.3433216&zoom=16&size=800x600&markers=…">…</html>')
    CENTROID = (8.0407335, 98.3433216)   # вьюпорт-центроид тела — цену по нему НЕ даём (ASK #51)

    class _Resp200:
        """Фейк конечного 200-ответа (как HTTPResponse после разворота)."""
        headers = {}
        def __init__(self, url): self._url = url
        def getcode(self): return 200
        def geturl(self): return self._url
        def close(self): pass

    def _mock_net(self, chain):
        """Подменить сеть delivery: build_opener → opener с 302+Location по `chain` (url→Location),
        поднимая HTTPError ровно как живой _NoRedirectHandler; _fetch_body → BODY. С откатом."""
        test = self

        class _Opener:
            def open(self, req, timeout=None):
                u = req.full_url
                if u in chain:                     # 3xx-хоп: HTTPError с Location (как в проде)
                    h = email.message.Message()
                    h["Location"] = chain[u]
                    raise delivery.urllib.error.HTTPError(u, 302, "Found", h, io.BytesIO(b""))
                return test._Resp200(u)            # конечная страница 200

        saved_build = delivery.urllib.request.build_opener
        saved_fetch = delivery._fetch_body
        delivery.urllib.request.build_opener = lambda *a, **k: _Opener()
        delivery._fetch_body = lambda url: test.BODY
        self.addCleanup(setattr, delivery.urllib.request, "build_opener", saved_build)
        self.addCleanup(setattr, delivery, "_fetch_body", saved_fetch)

    def test_single_302_hop_then_body(self):
        # один 302 → конечный URL без координат → тело догружено → «URL\n+тело» (механика Bug A).
        # Тело несёт лишь вьюпорт-центроид center= → координат из него НЕ извлекаем (ASK #51).
        self._mock_net({self.SHORT: self.FINAL})
        out = delivery._default_expand(self.SHORT)
        self.assertEqual(out, self.FINAL + "\n" + self.BODY)          # редирект пройден, тело добавлено
        self.assertIsNone(delivery._parse_pin_from_body(self.BODY))   # точного пина нет — центроид игнор
        self.assertIsNone(delivery.resolve_maps_link(self.SHORT))     # сквозь публичный API → None

    def test_multi_hop_chain_walked(self):
        # цепочка 302→302→200 проходится целиком (не падает на первом Location, механика Bug A)
        self._mock_net({self.SHORT: self.MID, self.MID: self.FINAL})
        out = delivery._default_expand(self.SHORT)
        self.assertTrue(out.startswith(self.FINAL))                   # дошли до конечного URL
        self.assertIsNone(delivery.resolve_maps_link(self.SHORT))     # тело — центроид → None (ASK #51)

    def test_resolve_maps_link_full_path_yields_none(self):
        # сквозь публичный resolve_maps_link (реальный _default_expand, мок-сеть): тело Meat Point
        # несёт лишь вьюпорт-центроид → None → честный [уточнить] (ASK #51), НЕ координата.
        self._mock_net({self.SHORT: self.FINAL})
        self.assertIsNone(delivery.resolve_maps_link(self.SHORT))

    def test_end_to_end_meatpoint_uncertain(self):
        # СКВОЗНОЙ живой сценарий #51 (после решения ASK #51): короткая ссылка → 302+Location →
        # place-URL → тело с одним вьюпорт-центроидом → точного пина нет → [уточнить]. Цену по
        # центроиду НЕ называем (раньше здесь ошибочно выходило out_belt/1490).
        self._mock_net({self.SHORT: self.FINAL})
        text = f"локация тут {self.SHORT} спасибо"
        r = delivery.resolve_delivery_from_text(text, _get_zones=lambda: LIVE_ZONES)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")
        self.assertIsNone(r["price"])


class TestDeliveryGoldens(unittest.TestCase):
    """ДЕТЕРМИНИРОВАННЫЕ ГОЛДЕНЫ сквозного конвейера доставки — вся сеть ЗАМОКАНА.

    Прогоняем реальные реплики клиента через resolve_delivery_from_text (текст →
    extract_maps_link → resolve_maps_link → зоны Bridge → resolve_delivery), инъектируя
    зоны и разворот коротких ссылок. Ни один голден не ходит в интернет/Bridge — вход
    фиксирован, выход детерминирован. Канонические сценарии родителя #12 + решение ASK #51:
      1) точка в зоне                          → цена зоны
      2) точка на границе двух зон             → БЛИЖАЙШИЙ якорь (не первый в списке)
      3) точка в поясе +5 км за границей зоны  → 1490 (OUT_BELT_PRICE)
      4) точка вне острова (Москва)            → отказ [уточнить]
      5) битая/обрезанная maps-ссылка          → [уточнить] (цену не выдумываем)
      6) place-ссылка без координат            → [уточнить]
      7) place-ссылка, в теле лишь ВЬЮПОРТ-ЦЕНТРОИД (Meat Point) → [уточнить] (ASK #51)
      8) place-ссылка, в теле ТОЧНЫЙ пин Раваи → цена зоны 590 (надёжная координата)

    Зоны — синтетические, но с прозрачной геометрией (общая широта 7.88; 1° долготы на
    этой широте ≈ 110.3 км), чтобы дистанции/победитель проверялись глазами."""

    # Два перекрывающихся якоря на широте 7.88, разнесены по долготе на ~6.6 км (r=6 → зоны
    # перекрываются в середине). Плюс отдельная «узкая» зона для пояса. Цены различны, чтобы
    # голден однозначно указывал зону-победителя.
    ZONES = [
        {"name": "ЗонаA", "lat": 7.88, "lon": 98.36, "radius_km": 6, "price": 300},
        {"name": "ЗонаB", "lat": 7.88, "lon": 98.42, "radius_km": 6, "price": 500},
    ]
    # Узкая зона (r=1) для пояса: точка вне радиуса, но в пределах 1+OUT_BELT_KM.
    ZONES_NARROW = [{"name": "Узкая", "lat": 7.88, "lon": 98.39, "radius_km": 1, "price": 300}]

    def _run(self, text, zones, resolve_maps=None):
        """Сквозной прогон с замоканной сетью: зоны и (опц.) разворот ссылки инъектируются."""
        called = {"z": 0}
        def _zones():
            called["z"] += 1
            return zones
        kw = {"_get_zones": _zones}
        if resolve_maps is not None:
            kw["_resolve_maps"] = resolve_maps
        r = delivery.resolve_delivery_from_text(text, **kw)
        return r, called["z"]

    # 1) точка в зоне ---------------------------------------------------------
    def test_golden_point_inside_zone(self):
        # координата ровно на якоре ЗонаB → её цена 500
        text = "Вилла тут https://www.google.com/maps?q=7.88,98.42 заберите завтра к 15:00"
        r, _ = self._run(text, self.ZONES)
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "ЗонаB")
        self.assertEqual(r["price"], 500)
        self.assertIsNone(r["marker"])

    # 2) граница двух зон → ближайший якорь ------------------------------------
    def test_golden_border_nearest_anchor_wins(self):
        # точка в перекрытии обеих зон, но ближе к B (до A≈4.4 км, до B≈2.2 км) → цена B (500),
        # а НЕ первого в списке A (300). Обрезанный мессенджером хвост запятой снимается extract'ом.
        text = "локация виллы https://www.google.com/maps?q=7.88,98.40, приезжайте пораньше"
        r, _ = self._run(text, self.ZONES)
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "ЗонаB")
        self.assertEqual(r["price"], 500)
        # порядок зон не влияет — побеждает близость, не позиция
        r2, _ = self._run(text, list(reversed(self.ZONES)))
        self.assertEqual(r2["zone"], "ЗонаB")
        self.assertEqual(r2["price"], 500)

    # 3) пояс +5 км → 1490 ----------------------------------------------------
    def test_golden_out_belt_1490(self):
        # ~2.8 км восточнее узкого якоря (r=1): вне радиуса, но внутри 1+OUT_BELT_KM(5) → 1490
        text = "тут https://www.google.com/maps?q=7.88,98.415 спасибо"
        r, _ = self._run(text, self.ZONES_NARROW)
        self.assertEqual(r["status"], "out_belt")
        self.assertEqual(r["price"], 1490)
        self.assertEqual(r["price"], delivery.OUT_BELT_PRICE)
        self.assertIsNone(r["marker"])

    # 4) вне острова → [уточнить] ---------------------------------------------
    def test_golden_off_island_uncertain(self):
        # координата в Москве — далеко за поясом любой зоны Пхукета → честный отказ
        text = "адрес https://www.google.com/maps?q=55.7558,37.6173 квартира 5"
        r, _ = self._run(text, self.ZONES)
        self.assertEqual(r["status"], "uncertain")
        self.assertIsNone(r["price"])
        self.assertEqual(r["marker"], "[уточнить]")

    # 5) битая/обрезанная ссылка → [уточнить] ---------------------------------
    def test_golden_truncated_link_uncertain(self):
        # мессенджер обрезал ссылку ДО второй координаты: q=7.88 без пары. Ссылка распознана как
        # maps (host+/maps), но координат нет → resolve_maps=None → [уточнить] (цену не выдумываем).
        text = "вот https://www.google.com/maps?q=7.88"
        r, z = self._run(text, self.ZONES)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")
        self.assertEqual(z, 0)          # зоны Bridge даже не запрашивались — координат ведь нет

    def test_golden_broken_short_link_uncertain(self):
        # обрезанная КОРОТКАЯ ссылка: разворот (замокан) отдал place-страницу без координат → [уточнить]
        text = "локация https://maps.app.goo.gl/AbC"
        final_no_coords = "https://www.google.com/maps/place/Some+Villa/"
        r, _ = self._run(text, self.ZONES, resolve_maps=lambda u: delivery.resolve_maps_link(
            u, _expand=lambda _u: final_no_coords))
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")

    # 6) place-ссылка без координат → [уточнить] ------------------------------
    def test_golden_place_link_without_coords_uncertain(self):
        # полная place-ссылка без пары координат в URL → [уточнить] (не выдумываем зону/цену)
        text = "приезжайте https://www.google.com/maps/place/Villa+Sunrise+Rawai/ спасибо"
        r, _ = self._run(text, self.ZONES)
        self.assertEqual(r["status"], "uncertain")
        self.assertEqual(r["marker"], "[уточнить]")

    # 7) ЖИВОЙ place-линк Meat Point: в теле лишь ВЬЮПОРТ-ЦЕНТРОИД → [уточнить] (ASK #51) -----
    # Конечный URL из recon (координат в нём нет) + фрагмент ЖИВОГО тела, где единственная
    # координатная пара — center= статической карты (вьюпорт-центроид, %2C-кодирован как в проде).
    # По центроиду цену НЕ называем: точного пина `!3d!4d` в теле нет → честный [уточнить].
    _RECON_FINAL_URL = (
        "https://www.google.com/maps/place/79,+Meat+Point+%7C+steaks,+burgers,+skewers,"
        "+79+Soi+Saiyuan,+Mueang,+Phuket,+83100/data=!4m2!3m1!1s0x30502f24a5443265:"
        "0xa4cc15728db01dbd!18m1!1e1?utm_source=mstt_1&entry=gps&g_st=ac"
    )
    _RECON_PAGE = (
        _RECON_FINAL_URL + "\n<html>…\"https://maps.googleapis.com/maps/api/staticmap?"
        "center=8.0407335%2C98.3433216&zoom=16&size=800x600\"…</html>"
    )

    def test_golden_meatpoint_centroid_uncertain(self):
        # ГОЛДЕН ASK #51 (сценарий 7): клиент кинул короткую share-ссылку Meat Point. Разворот
        # (замокан конечным URL из recon + тело) даёт в теле ЛИШЬ вьюпорт-центроид center= —
        # точного пина `!3d!4d` нет. По центроиду цену не называем: [уточнить], а НЕ out_belt/1490
        # (прежнее ошибочное поведение). Против ЖИВЫХ зон прода (позиционный формат).
        text = "локация тут https://maps.app.goo.gl/c4G4B3sNrfJZBSue6?g_st=ac спасибо"
        r, z = self._run(text, LIVE_ZONES, resolve_maps=lambda u: delivery.resolve_maps_link(
            u, _expand=lambda _u: self._RECON_PAGE))
        self.assertEqual(r["status"], "uncertain")   # цену по центроиду НЕ выдаём
        self.assertEqual(r["marker"], "[уточнить]")
        self.assertIsNone(r["price"])
        self.assertEqual(z, 0)                        # координат нет → зоны Bridge не запрошены

    # 8) ЖИВОЙ place-линк, в теле ТОЧНЫЙ пин Раваи → надёжная координата → цена зоны 590 --------
    # Пара к сценарию 7: тело place-страницы несёт точный пин `!3d7.771!4d98.327` (якорь Раваи,
    # метаданные place) — это НАДЁЖНЫЙ источник (не вьюпорт). Против ЖИВЫХ зон прода → зона
    # Раваи, 590. Центроид center= рядом в теле НАМЕРЕННО отличается — берём пин, не его.
    _RAWAI_PIN_PAGE = (
        "https://www.google.com/maps/place/Rawai+Beach/data=!4m2!3m1!1s0x0:0x0"
        "\n<html>…/data=!3m1!4b1!3d7.771!4d98.327!8m2… "
        "\"https://maps.googleapis.com/maps/api/staticmap?"
        "center=8.0407335%2C98.3433216&zoom=16&size=800x600\"…</html>"
    )

    def test_golden_reliable_rawai_pin_resolves_590(self):
        # ГОЛДЕН ASK #51 (сценарий 8): «надёжная координата Раваи → 590». Клиент кинул короткую
        # ссылку; тело даёт точный пин `!3d7.771!4d98.327` (надёжно) — берём его, вьюпорт-центроид
        # 8.04… игнорим. Против ЖИВЫХ зон прода → зона Раваи, 590 (не [уточнить]).
        text = "локация тут https://maps.app.goo.gl/RawaiPinInBody спасибо"
        r, z = self._run(text, LIVE_ZONES, resolve_maps=lambda u: delivery.resolve_maps_link(
            u, _expand=lambda _u: self._RAWAI_PIN_PAGE))
        self.assertEqual(r["status"], "zone")
        self.assertEqual(r["zone"], "Раваи")
        self.assertEqual(r["price"], 590)
        self.assertIsNone(r["marker"])
        self.assertEqual(z, 1)                        # пин найден → зоны Bridge запрошены


if __name__ == "__main__":
    unittest.main(verbosity=2)
