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


if __name__ == "__main__":
    unittest.main(verbosity=2)
