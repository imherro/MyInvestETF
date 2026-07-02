from __future__ import annotations

import unittest

from core.market import build_market_context, market_context_to_dict
from core.risk import build_drawdown_state


def price_rows(values: list[float]) -> list[dict[str, object]]:
    return [
        {
            "trade_date": f"2026-01-{index + 1:02d}",
            "close_price": close,
            "amount": 1000.0 + index * 10.0,
        }
        for index, close in enumerate(values)
    ]


class MarketContextTests(unittest.TestCase):
    def test_drawdown_state_tracks_current_and_max_drawdown(self) -> None:
        state = build_drawdown_state(price_rows([100.0, 110.0, 105.0, 99.0, 104.0]))

        self.assertAlmostEqual(state.current_drawdown, 0.054545, places=6)
        self.assertAlmostEqual(state.max_drawdown_rolling, 0.1, places=6)
        self.assertAlmostEqual(state.drawdown_percentile, 80.0, places=6)
        self.assertGreater(state.recovery_speed, 0.0)
        self.assertEqual(state.duration_days, 3)
        self.assertEqual(state.peak_date, "2026-01-02")
        self.assertEqual(state.trough_date, "2026-01-04")

    def test_market_context_detects_risk_on_from_rising_series(self) -> None:
        rows = price_rows([100.0 + index for index in range(90)])
        context = build_market_context("510300.SH", etf_prices=rows)

        self.assertEqual(context.regime.regime, "risk_on")
        self.assertGreater(context.regime.confidence, 0.5)
        self.assertEqual(context.drawdown.current_drawdown, 0.0)
        self.assertEqual(context.drawdown.data_points, 90)

    def test_market_context_to_dict_is_report_ready(self) -> None:
        context = build_market_context("510300.SH", etf_prices=price_rows([100.0, 95.0, 90.0, 92.0]))
        payload = market_context_to_dict(context)

        self.assertEqual(payload["etf_code"], "510300.SH")
        self.assertIn(payload["regime"]["regime"], {"risk_on", "risk_off", "shock", "rotation"})
        self.assertIn("drawdown_percentile", payload["drawdown"])


if __name__ == "__main__":
    unittest.main()
