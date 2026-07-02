from __future__ import annotations

import unittest

from core.factors import build_factor_exposure, compute_factor_ic, get_factor_definition
from core.factors.point_in_time import point_in_time_prices
from core.factors.standardization import build_factor_value


def price_rows(count: int = 120) -> list[dict[str, object]]:
    rows = []
    for index in range(count):
        rows.append(
            {
                "trade_date": f"2026-01-{index + 1:03d}",
                "close_price": 100.0 + index * 0.8 + (index % 7) * 0.1,
                "amount": 10_000.0 + index * 120.0,
            }
        )
    return rows


class FactorEngineTests(unittest.TestCase):
    def test_point_in_time_prices_apply_lag(self) -> None:
        points = point_in_time_prices(price_rows(10), lag_days=1)

        self.assertEqual(len(points), 9)
        self.assertEqual(points[-1].trade_date, "2026-01-009")

    def test_factor_value_has_standardized_fields(self) -> None:
        definition = get_factor_definition("price_momentum_20")
        assert definition is not None
        value = build_factor_value(
            definition,
            etf_code="588170.SH",
            price_series=price_rows(90),
            lag_days=1,
        )

        self.assertIsNotNone(value)
        assert value is not None
        self.assertEqual(value.factor_name, "price_momentum_20")
        self.assertGreaterEqual(value.percentile, 0.0)
        self.assertLessEqual(value.percentile, 100.0)
        self.assertEqual(value.leakage_guard, "point_in_time_lag_1")
        self.assertEqual(value.as_of_date, "2026-01-089")

    def test_taxonomy_selects_theme_factor_subset(self) -> None:
        exposure = build_factor_exposure(
            etf_code="588170.SH",
            price_series=price_rows(120),
            taxonomy_profile={"etf_type": "theme_lifecycle"},
            lag_days=1,
        )
        factor_names = {factor.factor_name for factor in exposure.factors}

        self.assertIn("price_momentum_20", factor_names)
        self.assertIn("liquidity_trend_20", factor_names)
        self.assertEqual(exposure.leakage_guard, "point_in_time_lag_1")
        self.assertTrue(exposure.attribution)

    def test_factor_ic_outputs_horizons(self) -> None:
        definition = get_factor_definition("price_momentum_20")
        assert definition is not None
        summaries = compute_factor_ic(
            definition,
            {
                "588170.SH": price_rows(140),
                "512880.SH": price_rows(130),
            },
            horizons=(5, 20),
        )

        self.assertEqual([item.horizon_days for item in summaries], [5, 20])
        self.assertGreater(summaries[0].observations, 0)
        self.assertEqual(summaries[0].leakage_guard, "factor_date_before_forward_return_window")


if __name__ == "__main__":
    unittest.main()
