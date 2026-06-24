from __future__ import annotations

import unittest

from core.report import build_etf_report
from core.schema.etf_report import ETFResearchReport


ASSEMBLY_INPUT = {
    "etf_code": "510300.SH",
    "etf_name": "沪深300ETF",
    "source_report_id": "manual",
    "task_type": "valuation",
    "research_date": "2026-06-24",
    "portfolio_role": "宽基底仓候选",
    "product_profile": {
        "fund_type": "ETF",
        "tracking_index": "沪深300",
        "asset_class": "宽基权益",
        "portfolio_role": "宽基底仓候选",
        "fee_note": "费率待核实。",
        "liquidity_note": "日均成交额较好。",
        "tracking_note": "跟踪误差待持续验证。",
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
        self.assertEqual(first.valuation.method, "NAV+index-valuation")
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


if __name__ == "__main__":
    unittest.main()
