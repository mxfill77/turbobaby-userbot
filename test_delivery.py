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
        # развернулась в place-ссылку без координат → None
        short = "https://maps.app.goo.gl/NoCoords"
        final = "https://www.google.com/maps/place/Some+Cafe/"
        self.assertIsNone(delivery.resolve_maps_link(short, _expand=lambda u: final))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
