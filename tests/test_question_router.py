from __future__ import annotations

import unittest

from core.interpreter import parse_question, question_intent_to_dict


class QuestionRouterTests(unittest.TestCase):
    def test_routes_buy_assessment_question(self) -> None:
        intent = parse_question("510300.SH 现在能不能买？")

        self.assertEqual(intent.type, "buy_assessment")
        self.assertEqual(intent.focus, "timing")
        self.assertEqual(intent.entities["etf_code"], "510300.SH")
        self.assertGreaterEqual(intent.confidence, 0.8)

    def test_routes_risk_assessment_question(self) -> None:
        intent = parse_question("512100.SH 风险大吗？")

        self.assertEqual(intent.type, "risk_assessment")
        self.assertEqual(intent.focus, "risk")
        self.assertEqual(intent.entities["etf_code"], "512100.SH")

    def test_routes_market_state_question(self) -> None:
        intent = parse_question("515700.SH 现在是什么状态？")

        self.assertEqual(intent.type, "market_state")
        self.assertEqual(intent.focus, "structure")
        self.assertEqual(intent.entities["etf_code"], "515700.SH")

    def test_routes_comparison_question_with_entities(self) -> None:
        intent = parse_question("512890.SH vs 588000.SH 哪个更好？")

        self.assertEqual(intent.type, "comparison")
        self.assertEqual(intent.entities["etf_code"], "512890.SH")
        self.assertEqual(intent.entities["comparison_etfs"], ["588000.SH"])

    def test_unknown_question_is_not_forced_into_known_type(self) -> None:
        intent = parse_question("帮我看看")
        payload = question_intent_to_dict(intent)

        self.assertEqual(intent.type, "unknown")
        self.assertEqual(intent.focus, "unknown")
        self.assertLess(intent.confidence, 0.5)
        self.assertEqual(payload["type"], "unknown")


if __name__ == "__main__":
    unittest.main()
