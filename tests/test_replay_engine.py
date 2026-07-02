from __future__ import annotations

import unittest

from core.replay import build_replay_report


def price_rows(values: list[float], amount_start: float = 1000.0) -> list[dict[str, object]]:
    return [
        {
            "trade_date": f"2026-01-{index + 1:03d}",
            "close_price": close,
            "amount": amount_start + index * 25.0,
        }
        for index, close in enumerate(values)
    ]


class ReplayEngineTests(unittest.TestCase):
    def test_replay_report_rebuilds_decision_path_without_future_data(self) -> None:
        values = [100.0 + index * 0.35 for index in range(60)] + [121.0 - index * 0.45 for index in range(35)]
        report = build_replay_report(
            etf_code="510300.SH",
            price_series_by_code={
                "510300.SH": price_rows(values),
                "588170.SH": price_rows([80.0 + index * 0.2 for index in range(95)], amount_start=2000.0),
                "512890.SH": price_rows([50.0 + (index % 10) * 0.1 for index in range(95)], amount_start=1500.0),
            },
            taxonomy_by_code={
                "510300.SH": {"etf_type": "broad_index_core", "classification_confidence": 0.88},
                "588170.SH": {"etf_type": "theme_lifecycle"},
                "512890.SH": {"etf_type": "factor_strategy"},
            },
            valuation_signal={"valuation_model_type": "broad_index", "undervalued_score": 65.0, "risk_adjusted_score": 70.0},
            valuation_as_of_date="2026-01-080",
            min_observations=45,
            max_points=30,
        )

        self.assertEqual(report.etf, "510300.SH")
        self.assertGreater(len(report.points), 0)
        self.assertTrue(report.validation["as_of_enforced"])
        self.assertTrue(report.validation["no_future_data"])
        self.assertEqual(report.validation["violations"], [])
        self.assertEqual(len(report.time_series["score_series"]), len(report.points))
        self.assertIn("regime_flip_rate", report.stability)
        self.assertIn("regime_duration_distribution", report.stability)
        self.assertIn("factor_stability_ic", report.stability)
        self.assertIn("score_vs_drawdown_correlation", report.drawdown_sensitivity)
        self.assertGreaterEqual(report.consistency_score, 0.0)
        self.assertLessEqual(report.consistency_score, 100.0)
        self.assertFalse(report.constraints["contains_trade_orders"])

    def test_replay_is_deterministic_for_same_inputs(self) -> None:
        values = [100.0 + index * 0.25 for index in range(80)]
        kwargs = {
            "etf_code": "588170.SH",
            "price_series_by_code": {
                "588170.SH": price_rows(values),
                "510300.SH": price_rows([90.0 + index * 0.1 for index in range(80)]),
            },
            "taxonomy_by_code": {
                "588170.SH": {"etf_type": "theme_lifecycle", "classification_confidence": 0.82},
                "510300.SH": {"etf_type": "broad_index_core"},
            },
            "valuation_signal": {"valuation_model_type": "mainline_theme", "valuation_tolerance_score": 55.0},
            "valuation_as_of_date": "2026-01-060",
            "min_observations": 45,
            "max_points": 20,
        }

        first = build_replay_report(**kwargs)
        second = build_replay_report(**kwargs)

        self.assertEqual(first.time_series["score_series"], second.time_series["score_series"])
        self.assertEqual(first.stability["regime_transition_matrix"], second.stability["regime_transition_matrix"])


if __name__ == "__main__":
    unittest.main()
