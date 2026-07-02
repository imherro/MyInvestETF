from __future__ import annotations

import unittest

from core.market import build_market_regime_v2, build_market_structure


def price_rows(values: list[float], amount_start: float = 1000.0) -> list[dict[str, object]]:
    return [
        {
            "trade_date": f"2026-01-{index + 1:03d}",
            "close_price": close,
            "amount": amount_start + index * 20.0,
        }
        for index, close in enumerate(values)
    ]


class MarketStructureTests(unittest.TestCase):
    def test_market_structure_outputs_breadth_and_liquidity(self) -> None:
        structure = build_market_structure(
            {
                "510300.SH": price_rows([100.0, 101.0, 102.0]),
                "588170.SH": price_rows([100.0, 99.0, 98.0]),
                "512890.SH": price_rows([100.0, 100.5, 101.0]),
            },
            {
                "510300.SH": {"etf_type": "broad_index_core"},
                "588170.SH": {"etf_type": "theme_lifecycle"},
                "512890.SH": {"etf_type": "factor_strategy"},
            },
        )

        self.assertGreater(structure.index_breadth, 0.0)
        self.assertGreater(structure.advance_decline_ratio, 0.0)
        self.assertIn("breadth", structure.contributions)
        self.assertEqual(structure.observations, 3)

    def test_same_etf_regime_changes_with_structure_input(self) -> None:
        etf_prices = price_rows([100.0 + index for index in range(90)])
        strong_structure = build_market_structure(
            {
                "a": price_rows([100.0, 102.0]),
                "b": price_rows([100.0, 103.0]),
                "c": price_rows([100.0, 104.0]),
            }
        )
        weak_structure = build_market_structure(
            {
                "a": price_rows([100.0, 98.0]),
                "b": price_rows([100.0, 97.0]),
                "c": price_rows([100.0, 96.0]),
            }
        )

        strong = build_market_regime_v2("510300.SH", etf_prices, strong_structure)
        weak = build_market_regime_v2("510300.SH", etf_prices, weak_structure)

        self.assertEqual(strong.regime, "risk_on")
        self.assertNotEqual(strong.regime, weak.regime)
        self.assertEqual(weak.confirmation_level, "weak")
        self.assertIn("breadth_contribution", weak.evidence)


if __name__ == "__main__":
    unittest.main()
