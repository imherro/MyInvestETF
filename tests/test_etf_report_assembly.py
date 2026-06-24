from __future__ import annotations

import unittest

from core.report import build_etf_report
from core.schema.etf_report import ETFResearchReport


ASSEMBLY_INPUT = {
    "etf_code": "510300.SH",
    "etf_name": "沪深300ETF",
    "source_report_id": "manual",
    "task_type": "research",
    "valuation_model_type": "broad_index",
    "sleeve_key": "core_wide_etf",
    "research_date": "2026-06-24",
    "portfolio_role": "宽基底仓候选",
    "product_profile": {
        "fund_type": "ETF",
        "tracking_index": "沪深300",
        "asset_class": "宽基权益",
        "valuation_model_type": "broad_index",
        "sleeve_key": "core_wide_etf",
        "portfolio_role": "宽基底仓候选",
        "fee_note": "费率待核实。",
        "liquidity_note": "日均成交额较好。",
        "tracking_note": "跟踪误差待持续验证。",
    },
    "model_specific_inputs": {
        "equity_risk_premium": 62.0,
        "roe": 58.0,
        "market_position_score": 45.0,
    },
    "holdings_inputs": {
        "holdings_disclosure_date": "2026-03-31",
        "top_holdings": [{"name": "贵州茅台", "weight": "5%"}],
        "concentration_ratio": 0.28,
        "concentration_note": "前十大集中度适中。",
        "overlap_note": "与现有宽基会有重叠。",
        "disclosure_lag_note": "fund_portfolio 为披露滞后口径。",
    },
    "valuation_inputs": {
        "current_price": 4.05,
        "nav": 4.0,
        "premium_discount": 0.005,
        "underlying_pe": 12.0,
        "underlying_pb": 1.4,
        "valuation_percentile": 30.0,
    },
    "liquidity_inputs": {
        "turnover_amount": 50_000_000,
        "fund_size": 12_000_000_000,
        "share_change_ratio": 0.02,
    },
    "tracking_inputs": {"tracking_error": 0.005},
    "risk_signals": {
        "liquidity_risk": "成交额下降会降低底仓可用性。",
        "tracking_risk": "跟踪误差扩大。",
        "concentration_risk": "前十大集中度需观察。",
        "sentiment_risk": "市场风险偏好下降。",
        "invalidation_conditions": ["估值分位过热"],
    },
    "evidence": [
        {
            "source": "Tushare",
            "date": "2026-06-24",
            "url": "local",
            "purpose": "ETF估值输入",
            "detail": "fund_nav/fund_daily/fund_share/fund_portfolio。",
        }
    ],
    "data_gaps": ["实时完整持仓不可得"],
}


class ETFReportAssemblyTests(unittest.TestCase):
    def test_build_etf_report_is_schema_first_and_deterministic(self) -> None:
        first = build_etf_report(ASSEMBLY_INPUT)
        second = build_etf_report(ASSEMBLY_INPUT)
        self.assertIsInstance(first, ETFResearchReport)
        self.assertEqual(first.report_hash, second.report_hash)
        self.assertEqual(first.valuation_model_type, "broad_index")
        self.assertEqual(first.sleeve_key, "core_wide_etf")
        self.assertEqual(first.valuation.method, "broad-index-valuation+ERP")
        self.assertIsNotNone(first.valuation.reference_value_mid)

    def test_hash_changes_when_inputs_change(self) -> None:
        baseline = build_etf_report(ASSEMBLY_INPUT)
        changed = build_etf_report(
            {
                **ASSEMBLY_INPUT,
                "valuation_inputs": {
                    **ASSEMBLY_INPUT["valuation_inputs"],
                    "valuation_percentile": 70.0,
                },
            }
        )
        self.assertNotEqual(baseline.report_hash, changed.report_hash)

    def test_mainline_theme_uses_theme_strength_method(self) -> None:
        report = build_etf_report(
            {
                **ASSEMBLY_INPUT,
                "valuation_model_type": "mainline_theme",
                "sleeve_key": "mainline_etf",
                "portfolio_role": "主线进攻工具",
                "product_profile": {
                    **ASSEMBLY_INPUT["product_profile"],
                    "asset_class": "行业主题",
                    "valuation_model_type": "mainline_theme",
                    "sleeve_key": "mainline_etf",
                    "portfolio_role": "主线进攻工具",
                },
                "model_specific_inputs": {
                    "theme_strength": 82.0,
                    "fund_flow_score": 76.0,
                    "crowding_score": 35.0,
                    "valuation_tolerance": 68.0,
                },
            }
        )
        self.assertEqual(report.valuation.method, "theme-strength+valuation-tolerance")
        self.assertGreater(report.valuation.mainline_validity_score or 0, 70)

    def test_factor_defensive_uses_factor_premium_method(self) -> None:
        report = build_etf_report(
            {
                **ASSEMBLY_INPUT,
                "etf_code": "512890.SH",
                "etf_name": "红利低波ETF",
                "valuation_model_type": "factor_defensive",
                "sleeve_key": "defensive_quality",
                "portfolio_role": "收益防御仓候选",
                "product_profile": {
                    **ASSEMBLY_INPUT["product_profile"],
                    "tracking_index": "红利低波指数",
                    "asset_class": "收益防御权益",
                    "valuation_model_type": "factor_defensive",
                    "sleeve_key": "defensive_quality",
                    "portfolio_role": "收益防御仓候选",
                },
                "model_specific_inputs": {
                    "dividend_spread": 72.0,
                    "fcf_yield": 64.0,
                    "quality_score": 70.0,
                    "style_opportunity_cost": 25.0,
                },
            }
        )
        self.assertEqual(report.valuation.method, "factor-premium+style-opportunity-cost")
        self.assertGreater(report.valuation.factor_premium_score or 0, 55)

    def test_cash_like_uses_monitor_method(self) -> None:
        report = build_etf_report(
            {
                **ASSEMBLY_INPUT,
                "etf_code": "511360.SH",
                "etf_name": "短融ETF",
                "valuation_model_type": "cash_like",
                "sleeve_key": "cash_like",
                "portfolio_role": "现金替代",
                "product_profile": {
                    **ASSEMBLY_INPUT["product_profile"],
                    "tracking_index": "短融债指数",
                    "asset_class": "现金替代",
                    "valuation_model_type": "cash_like",
                    "sleeve_key": "cash_like",
                    "portfolio_role": "现金替代",
                },
                "model_specific_inputs": {
                    "duration_risk": 12.0,
                    "credit_risk": 10.0,
                    "yield_stability": 92.0,
                },
            }
        )
        self.assertEqual(report.valuation.method, "cash-like-liquidity-monitor")
        self.assertGreater(report.valuation.cash_like_safety_score or 0, 50)


if __name__ == "__main__":
    unittest.main()
