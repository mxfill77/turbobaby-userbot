# -*- coding: utf-8 -*-
"""
test_delivery.py — мок-тесты клиента зон доставки (delivery.get_delivery_zones). БЕЗ реальной
сети/Bridge: транспорт инъектируется через _get (как _get у pricing.fleet/quote). Ключевые
инварианты: успех → список зон; ошибка/не-ok/исключение → None (резолвер даст «[уточнить]»);
повторный вызов в пределах TTL берётся из кэша, БЕЗ обращения к сети.
"""

import unittest

import delivery


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

    # ------------------------- ЖИВОЙ place-линк: пин только в ТЕЛЕ ------------

    # Реальный провал из tmp/geo-recon.md: короткая share-ссылка на объект разворачивается в
    # place-URL, где координат НЕТ вовсе (только текстовый адрес + hex-CID). Реальный пин
    # `8.0407335, 98.3433216` (Сайюан/Раваи, Пхукет) лежит ЛИШЬ в теле — staticmap `center=…`.
    # Продовый _default_expand отдаёт «конечный URL\n+тело», парсер тем же _LATLON берёт center=.
    GEO_RECON_FINAL_URL = (
        "https://www.google.com/maps/place/79,+Meat+Point+%7C+steaks,+burgers,+skewers,"
        "+79+Soi+Saiyuan,+Mueang,+Phuket,+83100/data=!4m2!3m1!1s0x30502f24a5443265:"
        "0xa4cc15728db01dbd!18m1!1e1?utm_source=mstt_1&entry=gps&coh=192189&g_st=ac"
    )
    # Фрагмент тела: центр статической карты — пара в порядке lat,lon (как в живом ответе).
    GEO_RECON_BODY = (
        "<html>…\"https://maps.googleapis.com/maps/api/staticmap?"
        "center=8.0407335%2C98.3433216&zoom=16&size=800x600\"…</html>"
    )
    GEO_RECON_PIN = (8.0407335, 98.3433216)

    def test_recon_place_link_coords_from_body_on_phuket(self):
        # ГОЛДЕН живого формата: в конечном URL координат нет вовсе — доказываем это, затем
        # показываем, что резолвер достаёт пин из ТЕЛА и точка определяется «на Пхукете».
        self.assertIsNone(delivery._parse_coords_from_url(self.GEO_RECON_FINAL_URL))
        short = "https://maps.app.goo.gl/c4G4B3sNrfJZBSue6?g_st=ac"
        # _expand имитирует продовый разворот: «конечный URL\n+тело» (redirect → финальный URL из recon)
        page = self.GEO_RECON_FINAL_URL + "\n" + self.GEO_RECON_BODY
        got = delivery.resolve_maps_link(short, _expand=lambda u: page)
        self._assert_close(got, self.GEO_RECON_PIN)
        # «на Пхукете»: точка внутри bbox острова (lat 7.6–8.3, lon 98.2–98.5), НЕ свап (lat≤90)
        self.assertTrue(7.6 <= got[0] <= 8.3 and 98.2 <= got[1] <= 98.5)

    def test_recon_place_link_no_body_coords_is_none(self):
        # тот же конечный URL, но тело без координат → честный None (fail-safe не ослаблен)
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


class TestDeliveryGoldens(unittest.TestCase):
    """ДЕТЕРМИНИРОВАННЫЕ ГОЛДЕНЫ сквозного конвейера доставки — вся сеть ЗАМОКАНА.

    Прогоняем реальные реплики клиента через resolve_delivery_from_text (текст →
    extract_maps_link → resolve_maps_link → зоны Bridge → resolve_delivery), инъектируя
    зоны и разворот коротких ссылок. Ни один голден не ходит в интернет/Bridge — вход
    фиксирован, выход детерминирован. Шесть канонических сценариев родителя #12:
      1) точка в зоне                          → цена зоны
      2) точка на границе двух зон             → БЛИЖАЙШИЙ якорь (не первый в списке)
      3) точка в поясе +5 км за границей зоны  → 1490 (OUT_BELT_PRICE)
      4) точка вне острова (Москва)            → отказ [уточнить]
      5) битая/обрезанная maps-ссылка          → [уточнить] (цену не выдумываем)
      6) place-ссылка без координат            → [уточнить]

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

    # 7) ЖИВОЙ place-линк, пин только в теле → точка «на Пхукете», не [уточнить] --------
    # Зона Пхукета вокруг реального пина из tmp/geo-recon.md (Сайюан/Раваи, 8.0407,98.3433).
    ZONES_PHUKET = [{"name": "Сайюан", "lat": 8.0407, "lon": 98.3433,
                     "radius_km": 5, "price": 350}]
    # Конечный URL из recon (координат в нём нет) + фрагмент тела с пином в staticmap center=.
    _RECON_FINAL_URL = (
        "https://www.google.com/maps/place/79,+Meat+Point+%7C+steaks,+burgers,+skewers,"
        "+79+Soi+Saiyuan,+Mueang,+Phuket,+83100/data=!4m2!3m1!1s0x30502f24a5443265:"
        "0xa4cc15728db01dbd!18m1!1e1?utm_source=mstt_1&entry=gps&g_st=ac"
    )
    _RECON_PAGE = (
        _RECON_FINAL_URL + "\n<html>…\"https://maps.googleapis.com/maps/api/staticmap?"
        "center=8.0407335%2C98.3433216&zoom=16&size=800x600\"…</html>"
    )

    def test_golden_recon_place_link_resolves_on_phuket(self):
        # Живой сценарий #22: клиент кинул короткую share-ссылку на заведение. Разворот
        # (замокан конечным URL из recon + тело) даёт пин из тела → точка попадает в зону
        # Пхукета, а НЕ в ложный [уточнить]. redirect имитируем через _resolve_maps→resolve_maps_link.
        text = "локация тут https://maps.app.goo.gl/c4G4B3sNrfJZBSue6?g_st=ac спасибо"
        r, z = self._run(text, self.ZONES_PHUKET, resolve_maps=lambda u: delivery.resolve_maps_link(
            u, _expand=lambda _u: self._RECON_PAGE))
        self.assertEqual(r["status"], "zone")     # определилась «на Пхукете», не [уточнить]
        self.assertEqual(r["zone"], "Сайюан")
        self.assertEqual(r["price"], 350)
        self.assertIsNone(r["marker"])
        self.assertEqual(z, 1)                    # зоны Bridge запрошены — координаты нашлись


if __name__ == "__main__":
    unittest.main(verbosity=2)
