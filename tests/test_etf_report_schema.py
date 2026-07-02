from __future__ import annotations

import unittest

from core.schema.etf_report import validate_etf_research_report


def base_report() -> dict:
    return {
        "schema_version": "etf_research_report.v1",
        "etf_code": "510300.SH",
        "etf_name": "沪深300ETF",
        "source_report_id": "manual",
        "task_type": "research",
        "research_date": "2026-06-24",
        "status": "complete",
        "valuation_model_type": "broad_index",
        "sleeve_key": "core_wide_etf",
        "title": "沪深300ETF完整深研",
        "summary": "完整深研。",
        "product_profile": {
            "fund_type": "ETF",
            "tracking_index": "沪深300",
            "asset_class": "宽基权益",
            "valuation_model_type": "broad_index",
            "sleeve_key": "core_wide_etf",
            "portfolio_role": "底仓候选",
            "fee_note": "费率待核实。",
            "liquidity_note": "流动性较好。",
            "tracking_note": "跟踪质量待持续验证。",
        },
        "holdings_profile": {
            "holdings_disclosure_date": "2026-03-31",
            "top_holdings": ["贵州茅台", "宁德时代"],
            "concentration_note": "前十大集中度适中。",
            "overlap_note": "组合重叠待核实。",
            "disclosure_lag_note": "fund_portfolio 为披露滞后口径。",
        },
        "valuation": {
            "current_price": 4.1,
            "nav": 4.0,
            "premium_discount": 0.01,
            "underlying_pe": 12.0,
            "underlying_pb": 1.4,
            "valuation_percentile": 35.0,
            "reference_value_low": 3.7,
            "reference_value_mid": 4.0,
            "reference_value_high": 4.3,
            "unit": "CNY/fund_share",
            "method": "NAV+index-valuation",
            "confidence": "medium",
            "key_assumptions": ["估值分位为结构化输入。"],
            "engine_version": "etf_valuation_engine.v1",
            "undervalued_score": 55.0,
            "liquidity_score": 80.0,
            "tracking_score": 90.0,
            "portfolio_role_score": 70.0,
            "risk_adjusted_score": 68.0,
            "mainline_validity_score": 50.0,
            "valuation_tolerance_score": 50.0,
            "crowding_risk_score": 50.0,
            "factor_premium_score": 50.0,
            "cash_like_safety_score": 50.0,
        },
        "base_position_view": "工具仓可用",
        "risk": {
            "liquidity_risk": "成交额下降。",
            "tracking_risk": "跟踪误差扩大。",
            "concentration_risk": "持仓集中。",
            "sentiment_risk": "市场情绪波动。",
            "invalidation_conditions": ["跟踪误差显著扩大"],
        },
        "conclusion": {
            "grade": "工具仓可用",
            "confidence": 0.68,
            "summary": "确定性评分。",
        },
        "evidence": [
            {
                "source": "Tushare",
                "date": "2026-06-24",
                "url": "local",
                "purpose": "ETF估值输入",
                "detail": "fund_nav/fund_daily/fund_share。",
            }
        ],
        "assumptions": ["same input same output"],
        "data_gaps": ["实时完整持仓不可得"],
    }


class ETFReportSchemaTests(unittest.TestCase):
    def test_research_report_accepts_complete_reference_range(self) -> None:
        report = validate_etf_research_report(base_report())
        self.assertEqual(report.etf_code, "510300.SH")
        self.assertEqual(report.task_type, "research")

    def test_research_report_accepts_market_context(self) -> None:
        payload = base_report()
        payload["market_context"] = {
            "etf_code": "510300.SH",
            "regime": {
                "regime": "rotation",
                "confidence": 0.62,
                "as_of_date": "2026-06-24",
                "evidence": {"momentum_20": 0.0123, "reason": "test"},
                "data_points": 88,
            },
            "drawdown": {
                "current_drawdown": 0.04,
                "max_drawdown_rolling": 0.12,
                "drawdown_percentile": 68.0,
                "recovery_speed": 0.003,
                "duration_days": 6,
                "drawdown_acceleration": -0.01,
                "as_of_date": "2026-06-24",
                "peak_date": "2026-06-18",
                "trough_date": "2026-06-21",
                "data_points": 88,
            },
        }
        report = validate_etf_research_report(payload)
        self.assertEqual(report.market_context.regime.regime, "rotation")
        self.assertEqual(report.market_context.drawdown.duration_days, 6)

    def test_research_report_requires_complete_reference_range(self) -> None:
        payload = base_report()
        payload["valuation"]["reference_value_low"] = None
        payload["valuation"]["reference_value_mid"] = None
        payload["valuation"]["reference_value_high"] = None
        payload["base_position_view"] = "观察"
        payload["conclusion"]["grade"] = "观察"
        with self.assertRaises(ValueError):
            validate_etf_research_report(payload)

    def test_legacy_task_type_is_rejected(self) -> None:
        payload = base_report()
        payload["task_type"] = "valuation"
        with self.assertRaises(ValueError):
            validate_etf_research_report(payload)

    def test_grade_must_match_base_position_view(self) -> None:
        payload = base_report()
        payload["conclusion"]["grade"] = "底仓候选"
        with self.assertRaises(ValueError):
            validate_etf_research_report(payload)

    def test_product_model_type_must_match_top_level(self) -> None:
        payload = base_report()
        payload["product_profile"]["valuation_model_type"] = "mainline_theme"
        with self.assertRaises(ValueError):
            validate_etf_research_report(payload)


if __name__ == "__main__":
    unittest.main()
