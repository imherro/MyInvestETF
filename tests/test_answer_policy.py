from __future__ import annotations

import unittest

from core.interpreter import AnswerPolicyEngine, final_answer_to_dict, parse_question


def signal(score: float, regime: str = "risk_on") -> dict[str, object]:
    return {
        "score": score,
        "confidence": 0.8,
        "taxonomy_type": "broad_index_core",
        "state": {"regime": regime},
    }


class AnswerPolicyTests(unittest.TestCase):
    def test_policy_generates_unified_participate_answer(self) -> None:
        answer = AnswerPolicyEngine().generate_answer(
            decision_signal=signal(80.0),
            regime={"regime": "risk_on"},
            intent=parse_question("510300.SH 现在能不能买？"),
            governance={"data_quality": {"gate_status": "pass"}, "regime_quality": {"gate_status": "pass"}},
            taxonomy={"etf_type": "broad_index_core"},
        )
        payload = final_answer_to_dict(answer)

        self.assertEqual(payload["conclusion"]["type"], "participate")
        self.assertTrue(payload["conclusion"]["non_trading"])
        self.assertIn("参与评估", payload["headline"])
        self.assertIn("AnswerPolicyEngine", " ".join(payload["reasoning"]))

    def test_governance_reject_forces_avoid(self) -> None:
        answer = AnswerPolicyEngine().generate_answer(
            decision_signal=signal(88.0),
            regime={"regime": "risk_on"},
            intent=parse_question("风险大吗？"),
            governance={"data_quality": {"gate_status": "reject"}, "regime_quality": {"gate_status": "pass"}},
            taxonomy={"etf_type": "theme_lifecycle"},
        )

        self.assertEqual(answer.conclusion["type"], "avoid")
        self.assertIn("结构不支持参与", answer.headline)
        self.assertIn("data_quality: reject", answer.risk_notes)

    def test_shock_regime_forces_avoid(self) -> None:
        answer = AnswerPolicyEngine().generate_answer(
            decision_signal=signal(90.0, regime="shock"),
            regime={"regime": "shock"},
            intent=parse_question("现在是什么状态？"),
            governance={"data_quality": {"gate_status": "pass"}, "regime_quality": {"gate_status": "pass"}},
            taxonomy={"etf_type": "broad_index_core"},
        )

        self.assertEqual(answer.conclusion["type"], "avoid")
        self.assertIn("regime: shock", answer.risk_notes)


if __name__ == "__main__":
    unittest.main()
