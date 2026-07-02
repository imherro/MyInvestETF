from __future__ import annotations

import unittest

from core.strategy import ContrarianModeEngine, contrarian_signal_to_dict


class ContrarianModeTests(unittest.TestCase):
    def test_extreme_drawdown_shock_enables_bottom_zone_without_overriding_score(self) -> None:
        engine = ContrarianModeEngine(
            {
                "drawdown": {
                    "current_drawdown": 0.25,
                    "max_drawdown_rolling": 0.27,
                    "drawdown_percentile": 96.0,
                    "recovery_speed": 0.002,
                    "drawdown_acceleration": -0.01,
                },
                "regime_v2": {
                    "regime": "shock",
                    "evidence": {"volatility_20": 0.035, "current_drawdown": 0.25},
                    "structure": {"breadth_score": 0.28, "liquidity_score": 0.35},
                },
            },
            {"factors": [{"factor_name": "liquidity_trend_20", "factor_type": "flow", "normalized_value": 0.32}]},
            {"gate_status": "pass", "system_health_score": 82.0},
        )

        signal = engine.adjust_decision({"etf_code": "159399.SZ", "score": 60.0})
        payload = contrarian_signal_to_dict(signal)

        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["final_view"], "probabilistic_bottom_zone")
        self.assertGreater(payload["reversal_probability"], 0.70)
        self.assertEqual(payload["adjusted_interpretation"]["original_decision_score"], 60.0)
        self.assertTrue(payload["constraints"]["does_not_override_decision_score"])
        self.assertFalse(payload["constraints"]["contains_trade_orders"])

    def test_extreme_drawdown_without_stress_does_not_enable_mode(self) -> None:
        engine = ContrarianModeEngine(
            {
                "drawdown": {"current_drawdown": 0.25, "max_drawdown_rolling": 0.25, "drawdown_percentile": 99.0},
                "regime_v2": {
                    "regime": "rotation",
                    "evidence": {"volatility_20": 0.015, "current_drawdown": 0.25},
                    "structure": {"breadth_score": 0.55, "liquidity_score": 0.60},
                },
            },
            {"factors": [{"factor_name": "liquidity_trend_20", "factor_type": "flow", "normalized_value": 0.55}]},
            {"gate_status": "pass", "system_health_score": 80.0},
        )

        payload = contrarian_signal_to_dict(engine.adjust_decision({"etf_code": "159399.SZ", "score": 60.0}))

        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["conditions"]["drawdown_extreme"], True)
        self.assertEqual(payload["conditions"]["regime_stress"], False)
        self.assertEqual(payload["final_view"], "normal")
        self.assertEqual(payload["adjusted_interpretation"]["risk_adjusted_score"], 60.0)

    def test_governance_reject_blocks_enabled_mode(self) -> None:
        engine = ContrarianModeEngine(
            {
                "drawdown": {"current_drawdown": 0.25, "max_drawdown_rolling": 0.26, "drawdown_percentile": 95.0},
                "regime_v2": {
                    "regime": "shock",
                    "evidence": {"volatility_20": 0.04, "current_drawdown": 0.25},
                    "structure": {"breadth_score": 0.20, "liquidity_score": 0.25},
                },
            },
            {"factors": [{"factor_name": "liquidity_trend_20", "factor_type": "flow", "normalized_value": 0.25}]},
            {"gate_status": "reject", "system_health_score": 30.0},
        )

        payload = contrarian_signal_to_dict(engine.adjust_decision({"etf_code": "159399.SZ", "score": 60.0}))

        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["evidence"]["governance_gate"], "reject")
        self.assertLess(payload["adjusted_interpretation"]["confidence"], 0.2)


if __name__ == "__main__":
    unittest.main()
