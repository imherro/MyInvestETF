from __future__ import annotations

import unittest

from core.strategy import StrategyRouter, strategy_decision_to_dict


def decision(score: float, regime: str = "risk_on", momentum: float = 75.0, flow: float = 70.0) -> dict[str, object]:
    return {
        "etf_code": "510300.SH",
        "score": score,
        "state": {"regime": regime},
        "component_scores": {"momentum": momentum, "flow": flow, "valuation": 55.0, "risk": 60.0},
    }


def contrarian(enabled: bool = False, score: float = 0.30, drawdown_extreme: bool = False) -> dict[str, object]:
    return {
        "enabled": enabled,
        "scores": {"reversal_probability": score, "exhaustion_score": score, "capitulation_score": score},
        "conditions": {"drawdown_extreme": drawdown_extreme, "regime_stress": enabled, "liquidity_stress": enabled},
        "adjusted_interpretation": {"final_view": "probabilistic_bottom_zone" if enabled else "not_active"},
    }


class StrategyRouterTests(unittest.TestCase):
    def test_routes_to_trend_when_risk_on_flow_and_momentum_confirm(self) -> None:
        router = StrategyRouter(
            decision(82.0, regime="risk_on", momentum=84.0, flow=78.0),
            contrarian(enabled=False, score=0.25, drawdown_extreme=False),
            {"regime": "risk_on", "confidence": 0.72},
            {"gate_status": "pass", "system_health_score": 82.0},
        )

        payload = strategy_decision_to_dict(router.route("510300.SH"))

        self.assertEqual(payload["active_mode"], "trend")
        self.assertIsNone(payload["suppressed_mode"])
        self.assertGreater(payload["signals"]["trend_score"], 0.75)
        self.assertTrue(payload["constraints"]["does_not_override_decision_score"])
        self.assertFalse(payload["constraints"]["contains_trade_orders"])

    def test_routes_to_contrarian_when_bottom_zone_is_enabled(self) -> None:
        router = StrategyRouter(
            decision(70.0, regime="shock", momentum=55.0, flow=60.0),
            contrarian(enabled=True, score=0.82, drawdown_extreme=True),
            {"regime": "shock", "confidence": 0.70},
            {"gate_status": "pass", "system_health_score": 80.0},
        )

        payload = strategy_decision_to_dict(router.route("159399.SZ"))

        self.assertEqual(payload["active_mode"], "contrarian")
        self.assertEqual(payload["suppressed_mode"], "trend")
        self.assertGreater(payload["signals"]["contrarian_score"], 0.80)
        self.assertIn("概率底部观察", payload["final_interpretation"])

    def test_governance_reject_forces_neutral(self) -> None:
        router = StrategyRouter(
            decision(82.0, regime="risk_on", momentum=84.0, flow=78.0),
            contrarian(enabled=False, score=0.25, drawdown_extreme=False),
            {"regime": "risk_on", "confidence": 0.72},
            {"gate_status": "reject", "system_health_score": 30.0},
        )

        payload = strategy_decision_to_dict(router.route("510300.SH"))

        self.assertEqual(payload["active_mode"], "neutral")
        self.assertEqual(payload["suppressed_mode"], "trend")
        self.assertLessEqual(payload["confidence"], 0.35)
        self.assertIn("governance reject", payload["final_interpretation"])


if __name__ == "__main__":
    unittest.main()
