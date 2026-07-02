from __future__ import annotations

import unittest

from core.governance import (
    build_data_quality_report,
    build_factor_quality_report,
    build_regime_quality_report,
    build_report_quality_report,
    build_research_health_report,
)


def prices(count: int) -> list[dict[str, object]]:
    return [
        {"trade_date": f"2026-01-{index + 1:03d}", "close_price": 100.0 + index, "amount": 1000.0 + index}
        for index in range(count)
    ]


class GovernanceEngineTests(unittest.TestCase):
    def test_data_quality_detects_missing_and_stale_price_data(self) -> None:
        report = build_data_quality_report(
            {
                "510300.SH": prices(60),
                "588170.SH": prices(20),
                "512890.SH": [],
            },
            min_observations=45,
        )

        self.assertGreater(report.missing_data_ratio, 0.0)
        self.assertLess(report.coverage_score, 100.0)
        self.assertTrue(report.missing_fields)
        self.assertIn(report.gate_status, {"warn", "reject"})

    def test_factor_quality_detects_decay_and_redundancy(self) -> None:
        report = build_factor_quality_report(
            {
                "price_momentum_20": [
                    {"horizon_days": 5, "ic_mean": 0.08, "ic_std": 0.02, "observations": 100},
                    {"horizon_days": 60, "ic_mean": 0.01, "ic_std": 0.04, "observations": 100},
                ],
                "liquidity_trend_20": [
                    {"horizon_days": 5, "ic_mean": 0.005, "ic_std": 0.06, "observations": 20},
                ],
            },
            {
                "a": {"factors": [{"factor_name": "x", "normalized_value": 0.1}, {"factor_name": "y", "normalized_value": 0.11}]},
                "b": {"factors": [{"factor_name": "x", "normalized_value": 0.2}, {"factor_name": "y", "normalized_value": 0.21}]},
                "c": {"factors": [{"factor_name": "x", "normalized_value": 0.3}, {"factor_name": "y", "normalized_value": 0.31}]},
            },
        )

        self.assertTrue(report.ic_decay_alerts)
        self.assertTrue(report.unstable_factors)
        self.assertTrue(report.redundant_factors)
        self.assertIn(report.gate_status, {"warn", "reject"})

    def test_regime_quality_flags_high_flip_rate(self) -> None:
        report = build_regime_quality_report(
            {
                "regime_flip_rate": 0.73,
                "regime_duration_distribution": [{"regime": "risk_on", "duration": 1}],
            },
            [
                {"regime": "risk_on", "confirmation_level": "weak"},
                {"regime": "risk_off", "confirmation_level": "medium"},
            ],
        )

        self.assertTrue(report.overfit_warning)
        self.assertLess(report.stability_score, 70.0)
        self.assertEqual(report.gate_status, "reject")

    def test_research_health_report_can_reject_low_quality_report(self) -> None:
        data = build_data_quality_report({"510300.SH": prices(60)}, min_observations=45)
        factor = build_factor_quality_report({}, {"510300.SH": {"factors": []}})
        regime = build_regime_quality_report({"regime_flip_rate": 0.10, "regime_duration_distribution": []}, [])
        report_quality = build_report_quality_report([])
        health = build_research_health_report(
            data_quality=data,
            factor_quality=factor,
            regime_quality=regime,
            report_quality=report_quality,
        )

        self.assertEqual(health.report_quality.gate_status, "reject")
        self.assertEqual(health.gate_status, "reject")
        self.assertFalse(health.constraints["contains_trade_orders"])


if __name__ == "__main__":
    unittest.main()
