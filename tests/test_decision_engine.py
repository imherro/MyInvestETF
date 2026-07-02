from __future__ import annotations

import unittest

from core.decision import build_decision_signal


def factor_exposure() -> dict[str, object]:
    return {
        "etf_code": "510300.SH",
        "as_of_date": "2026-06-24",
        "taxonomy_type": "theme_lifecycle",
        "factors": [
            {"factor_name": "price_momentum_20", "factor_type": "momentum", "normalized_value": 0.95},
            {"factor_name": "liquidity_trend_20", "factor_type": "flow", "normalized_value": 0.70},
            {"factor_name": "volatility_20", "factor_type": "risk", "normalized_value": 0.35},
        ],
    }


def regime(value: str, trend: float = 0.65) -> dict[str, object]:
    return {
        "regime": value,
        "confidence": 0.72,
        "structure": {
            "price_trend_score": trend,
            "breadth_score": 0.66,
            "liquidity_score": 0.61,
            "dispersion_score": 0.58,
        },
    }


def valuation_signal(score: float = 25.0) -> dict[str, object]:
    return {
        "valuation_model_type": "mainline_theme",
        "valuation_tolerance_score": score,
        "undervalued_score": score,
        "liquidity_score": 65.0,
        "risk_adjusted_score": 55.0,
    }


class DecisionEngineTests(unittest.TestCase):
    def test_regime_changes_score_sensitivity(self) -> None:
        risk_on = build_decision_signal(
            etf_code="510300.SH",
            factor_exposure=factor_exposure(),
            market_regime=regime("risk_on"),
            taxonomy_profile={"etf_type": "theme_lifecycle", "classification_confidence": 0.8},
            valuation_signal=valuation_signal(20.0),
        )
        risk_off = build_decision_signal(
            etf_code="510300.SH",
            factor_exposure=factor_exposure(),
            market_regime=regime("risk_off"),
            taxonomy_profile={"etf_type": "theme_lifecycle", "classification_confidence": 0.8},
            valuation_signal=valuation_signal(20.0),
        )

        self.assertGreater(risk_on.adjusted_weights["momentum"], risk_off.adjusted_weights["momentum"])
        self.assertGreater(risk_off.adjusted_weights["valuation"], risk_on.adjusted_weights["valuation"])
        self.assertGreater(risk_on.score, risk_off.score)
        self.assertNotEqual(risk_on.state.state_code, risk_off.state.state_code)

    def test_taxonomy_changes_weight_structure(self) -> None:
        broad = build_decision_signal(
            etf_code="510300.SH",
            factor_exposure=factor_exposure(),
            market_regime=regime("rotation", trend=0.52),
            taxonomy_profile={"etf_type": "broad_index_core", "classification_confidence": 0.8},
            valuation_signal=valuation_signal(60.0),
        )
        theme = build_decision_signal(
            etf_code="588170.SH",
            factor_exposure=factor_exposure(),
            market_regime=regime("rotation", trend=0.52),
            taxonomy_profile={"etf_type": "theme_lifecycle", "classification_confidence": 0.8},
            valuation_signal=valuation_signal(60.0),
        )

        self.assertGreater(theme.adjusted_weights["momentum"], broad.adjusted_weights["momentum"])
        self.assertGreater(broad.adjusted_weights["valuation"], theme.adjusted_weights["valuation"])

    def test_score_decomposition_is_stable_and_read_only(self) -> None:
        signal = build_decision_signal(
            etf_code="512890.SH",
            factor_exposure=factor_exposure(),
            market_regime=regime("shock", trend=0.38),
            taxonomy_profile={"etf_type": "factor_strategy", "classification_confidence": 0.9},
            valuation_signal={"valuation_model_type": "factor_defensive", "factor_premium_score": 75.0, "risk_adjusted_score": 68.0},
        )

        self.assertEqual(set(signal.factor_contributions), {"momentum", "flow", "valuation", "risk"})
        self.assertAlmostEqual(sum(signal.factor_contributions.values()), signal.score, places=5)
        self.assertEqual(signal.state.regime, "shock")
        self.assertEqual(signal.state.trend_state, "downtrend")
        self.assertIn("Score", signal.explanation)
        self.assertTrue(signal.constraints["read_only"])
        self.assertFalse(signal.constraints["contains_trade_orders"])
        self.assertFalse(signal.constraints["contains_cash_amounts"])
        self.assertFalse(signal.constraints["contains_share_counts"])


if __name__ == "__main__":
    unittest.main()
