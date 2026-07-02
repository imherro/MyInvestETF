from __future__ import annotations

import unittest

from core.interpreter import DecisionInterpreter


def decision_signal(score: float, regime: str = "risk_on") -> dict[str, object]:
    return {
        "score": score,
        "confidence": 0.791369,
        "regime": {"regime": regime, "confidence": 0.74},
        "taxonomy_type": "broad_index_core",
        "state": {
            "regime": regime,
            "score_band": "strong",
            "trend_state": "uptrend",
            "state_code": f"{regime}:strong:uptrend",
        },
    }


def taxonomy_profile() -> dict[str, object]:
    return {
        "etf_type": "broad_index_core",
        "subtype": "core_beta",
    }


def governance_report() -> dict[str, object]:
    return {
        "data_quality": {"gate_status": "pass", "missing_fields": []},
        "regime_quality": {"gate_status": "warn", "overfit_warning": True},
        "factor_quality": {"gate_status": "pass", "unstable_factors": []},
        "report_quality": {"gate_status": "pass", "rejection_reasons": []},
    }


class DecisionInterpreterTests(unittest.TestCase):
    def test_interpreter_returns_strict_structured_answer(self) -> None:
        result = DecisionInterpreter().interpret(
            "510300.SH",
            "现在能不能买？",
            decision_signal=decision_signal(78.2),
            taxonomy_profile=taxonomy_profile(),
            governance_report=governance_report(),
        )

        self.assertEqual(
            set(result),
            {"question", "etf", "regime", "taxonomy", "intent", "decision", "explanation", "risk", "final_answer"},
        )
        self.assertEqual(result["etf"], "510300.SH")
        self.assertEqual(result["regime"]["state"], "risk_on")
        self.assertEqual(result["taxonomy"]["type"], "broad_index_core")
        self.assertEqual(result["taxonomy"]["subtype"], "core_beta")
        self.assertEqual(result["intent"]["type"], "buy_assessment")
        self.assertEqual(result["intent"]["focus"], "timing")
        self.assertEqual(result["decision"]["band"], "high")
        self.assertEqual(result["decision"]["directional_bias"], "bullish")
        self.assertIn("参与评估", result["final_answer"])
        self.assertEqual(result["risk"]["regime_stability"], "warn")
        self.assertIn("regime_quality: warn", result["risk"]["warnings"])

    def test_shock_regime_overrides_score_answer(self) -> None:
        result = DecisionInterpreter().interpret(
            "510300.SH",
            "现在是什么状态？",
            decision_signal=decision_signal(82.0, regime="shock"),
            taxonomy_profile=taxonomy_profile(),
            governance_report=governance_report(),
        )

        self.assertEqual(result["decision"]["band"], "high")
        self.assertEqual(result["decision"]["directional_bias"], "bearish")
        self.assertIn("市场结构不稳定", result["final_answer"])
        self.assertEqual(result["intent"]["type"], "market_state")

    def test_output_does_not_emit_trade_order_cash_or_shares(self) -> None:
        result = DecisionInterpreter().interpret(
            "512100.SH",
            "风险大吗？",
            decision_signal=decision_signal(52.0, regime="risk_off"),
            taxonomy_profile={"etf_type": "sector_cyclical", "subtype": "cyclical_sector_beta"},
            governance_report={"data_quality": {"gate_status": "reject"}, "regime_quality": {"gate_status": "reject"}},
        )
        rendered = str(result)

        self.assertEqual(result["decision"]["band"], "low")
        self.assertEqual(result["decision"]["directional_bias"], "bearish")
        self.assertIn("结构不支持参与", result["final_answer"])
        self.assertEqual(result["intent"]["type"], "risk_assessment")
        for forbidden in ["买入", "卖出", "现金金额", "份额数量", "适合配置", "小仓位"]:
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
