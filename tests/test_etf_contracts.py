from __future__ import annotations

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from myinvestetf.db import (
    QUEUE_SOURCE_REQUEST,
    connect,
    init_db,
    list_queue,
    upsert_report,
    upsert_trackable_leader,
)
from myinvestetf.leader_index import (
    build_profile_prompt,
    build_requested_profile_prompt,
    build_requested_valuation_prompt,
    build_valuation_prompt,
    enqueue_requested_etf,
    primary_items,
    report_meta,
)
from myinvestetf.web import (
    decision_matrix_summary,
    leader_to_summary,
    render_layout,
    valuation_signal_summary,
    xueqiu_etf_link,
)


class ETFContractTests(unittest.TestCase):
    def test_primary_items_keeps_only_etf_sh_sz_bj_codes(self) -> None:
        payload = {
            "report": {"report_id": "r1"},
            "key_results": {
                "primary_output": {
                    "items": [
                        {"code": "510300.SH", "name": "沪深300ETF", "score": 80},
                        {"code": "bad", "name": "bad"},
                    ]
                }
            },
        }
        items = primary_items(payload)
        self.assertEqual([item["code"] for item in items], ["510300.SH"])

    def test_profile_prompt_is_single_etf_only(self) -> None:
        report = {"report_id": "r1", "basis_date": "2026-06-24"}
        item = {"code": "510300.SH", "name": "沪深300ETF", "theme": "宽基"}
        prompt = build_profile_prompt(item, report)
        self.assertIn("唯一研究对象：510300.SH 沪深300ETF", prompt)
        self.assertIn("task_type 必须为 profile", prompt)
        self.assertIn("fund_portfolio 只能作为已披露季报持仓", prompt)

    def test_valuation_prompt_depends_on_profile_and_uses_etf_inputs(self) -> None:
        report = {"report_id": "r1", "basis_date": "2026-06-24"}
        item = {"code": "510300.SH", "name": "沪深300ETF", "theme": "宽基"}
        prompt = build_valuation_prompt(item, report)
        self.assertIn("task_type='profile'", prompt)
        self.assertIn("fund_share 是份额变化的可用代理", prompt)
        self.assertIn("不要手写最终 ETFResearchReport", prompt)
        self.assertIn("data_gaps", prompt)

    def test_requested_etf_enqueue_marks_source(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "manual.sqlite"
            result = enqueue_requested_etf("510300.SH", name="沪深300ETF", db_path=db_path)
            with closing(connect(db_path)) as conn:
                rows = list_queue(conn)
            self.assertEqual(result["queued"], ["profile", "valuation"])
            self.assertEqual({row["source_type"] for row in rows}, {QUEUE_SOURCE_REQUEST})
            self.assertEqual([row["task_type"] for row in rows], ["profile", "valuation"])

    def test_requested_prompts_do_not_require_api_index_membership(self) -> None:
        report = {"report_id": "manual_etf_research_request_2026-06-24", "basis_date": "2026-06-24"}
        item = {"code": "510300.SH", "name": "沪深300ETF"}
        self.assertIn("不要求出现在 /api/index", build_requested_profile_prompt(item, report))
        self.assertIn("不要求出现在 /api/index", build_requested_valuation_prompt(item, report))

    def test_layout_uses_footer_and_etf_brand(self) -> None:
        page = render_layout("title", "<p>body</p>").decode("utf-8")
        self.assertIn("MyInvestETF", page)
        self.assertIn("https://invest.okbbc.com/footer.js", page)

    def test_leader_summary_links_to_etf_routes(self) -> None:
        row = {
            "code": "510300.SH",
            "name": "沪深300ETF",
            "theme": "宽基",
            "themes_json": '["宽基"]',
            "deep_rating": "A",
            "deep_label": "可跟踪ETF",
            "deep_score": 80.0,
            "shadow_observation_eligible": 1,
            "candidate_leader_tier": "ETF工具",
            "candidate_leader_claim": "宽基底仓工具",
            "candidate_evidence_score": 80.0,
            "candidate_evidence_count": 3,
            "candidate_hard_evidence_count": 2,
            "market_json": '{"close":4.05}',
            "scores_json": '{"theme_binding":80,"evidence_quality":80}',
            "risk_flags_json": "[]",
            "data_gaps_json": "[]",
            "xueqiu_url": "https://xueqiu.com/S/SH510300",
        }
        summary = leader_to_summary(row)
        self.assertEqual(summary["links"]["page"], "/etfs/510300.SH")
        self.assertEqual(summary["links"]["api"], "/api/etfs/510300.SH")

    def test_valuation_signal_reads_etf_scores(self) -> None:
        row = {
            "valuation_low": 3.7,
            "valuation_mid": 4.0,
            "valuation_high": 4.3,
            "valuation_unit": "CNY/fund_share",
            "valuation_method": "NAV+index-valuation",
            "heavy_position_view": "工具仓可用",
            "raw_json": (
                '{"valuation":{"undervalued_score":65,"liquidity_score":80,'
                '"tracking_score":90,"portfolio_role_score":70,"risk_adjusted_score":72},'
                '"conclusion":{"summary":"确定性规则评分"}}'
            ),
        }
        signal = valuation_signal_summary(row)
        self.assertEqual(signal["bucket"], "medium")
        self.assertEqual(signal["liquidity_score"], 80.0)

    def test_decision_matrix_uses_etf_language(self) -> None:
        matrix = decision_matrix_summary({"bucket": "strong"}, {"bucket": "high"})
        self.assertEqual(matrix["posture"], "底仓候选")
        self.assertIn("底仓候选", matrix["conclusion"])

    def test_report_meta_requires_report_id(self) -> None:
        with self.assertRaises(ValueError):
            report_meta({"report": {}})

    def test_xueqiu_link_for_etf(self) -> None:
        link = xueqiu_etf_link("510300.SH")
        self.assertIn('href="https://xueqiu.com/S/SH510300"', link)
        self.assertIn('target="_blank"', link)


if __name__ == "__main__":
    unittest.main()
