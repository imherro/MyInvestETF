from __future__ import annotations

from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from myinvestetf.db import (
    QUEUE_SOURCE_BROAD_INDEX,
    QUEUE_SOURCE_MAINLINE,
    QUEUE_SOURCE_REQUEST,
    connect,
    init_db,
    latest_report,
    list_latest_leaders,
    list_queue,
    upsert_report,
    upsert_trackable_leader,
)
from myinvestetf.config import FOOTER_SCRIPT_URL, HEADER_SCRIPT_URL, STATIC_ASSET_VERSION
from myinvestetf.leader_index import (
    build_research_prompt,
    build_requested_research_prompt,
    enqueue_requested_etf,
    ingest_payload,
    primary_items,
    research_representatives,
    report_meta,
)
from myinvestetf.web import (
    decision_matrix_summary,
    leader_to_summary,
    render_etf_cards,
    render_reference_price_explanation,
    render_signal_matrix,
    render_valuation_chart,
    render_queue_rows,
    render_layout,
    valuation_signal_summary,
    xueqiu_etf_link,
)
from scripts.generate_single_etf_prompt import format_queue_prompt


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

    def test_primary_items_reads_theme_latest_etf_top(self) -> None:
        payload = {
            "report_id": "mainline_review_2026-06-23_173855",
            "result": {
                "generated_at_iso": "2026-06-23T17:38:55+08:00",
                "basis_date": "2026-06-23",
                "etf_top": [
                    {
                        "ts_code": "588170.SH",
                        "name": "华夏上证科创板半导体材料设备主题ETF",
                        "r1": 0.37,
                        "r5": 15.214286,
                        "r20": 18.733898,
                        "amount": 3613825.102,
                        "score": 98.751705,
                    },
                    {"ts_code": "bad", "name": "bad"},
                ],
            },
        }
        meta = report_meta(payload)
        items = primary_items(payload)
        self.assertEqual(meta["report_id"], "mainline_review_2026-06-23_173855")
        self.assertEqual(meta["basis_date"], "2026-06-23")
        self.assertEqual([item["code"] for item in items], ["588170.SH"])
        self.assertEqual(items[0]["deep_label"], "可跟踪主线ETF")
        self.assertEqual(items[0]["scores"]["mainline_strength"], 98.751705)

    def test_primary_items_keeps_all_upstream_etfs(self) -> None:
        payload = {
            "report_id": "mainline_review_2026-06-23_173855",
            "result": {
                "basis_date": "2026-06-23",
                "etf_top": [
                    {
                        "ts_code": "588170.SH",
                        "name": "华夏上证科创板半导体材料设备主题ETF",
                        "amount": 100.0,
                        "score": 95.0,
                    },
                    {
                        "ts_code": "588710.SH",
                        "name": "华泰柏瑞上证科创板半导体材料设备主题ETF",
                        "amount": 300.0,
                        "score": 90.0,
                    },
                    {
                        "ts_code": "588200.SH",
                        "name": "嘉实上证科创板芯片ETF",
                        "amount": 900.0,
                        "score": 89.0,
                    },
                    {
                        "ts_code": "159842.SZ",
                        "name": "银华中证全指证券公司ETF",
                        "amount": 10.0,
                        "score": 92.0,
                    },
                    {
                        "ts_code": "512880.SH",
                        "name": "国泰中证全指证券公司ETF",
                        "amount": 500.0,
                        "score": 91.0,
                    },
                ],
            },
        }
        items = primary_items(payload)
        self.assertEqual(
            [item["code"] for item in items],
            ["588170.SH", "588710.SH", "588200.SH", "159842.SZ", "512880.SH"],
        )

    def test_research_representatives_keep_largest_amount_per_etf_category(self) -> None:
        payload = {
            "report_id": "mainline_review_2026-06-23_173855",
            "result": {
                "basis_date": "2026-06-23",
                "etf_top": [
                    {
                        "ts_code": "588170.SH",
                        "name": "华夏上证科创板半导体材料设备主题ETF",
                        "amount": 100.0,
                        "score": 95.0,
                    },
                    {
                        "ts_code": "588710.SH",
                        "name": "华泰柏瑞上证科创板半导体材料设备主题ETF",
                        "amount": 300.0,
                        "score": 90.0,
                    },
                    {
                        "ts_code": "588200.SH",
                        "name": "嘉实上证科创板芯片ETF",
                        "amount": 900.0,
                        "score": 89.0,
                    },
                    {
                        "ts_code": "159842.SZ",
                        "name": "银华中证全指证券公司ETF",
                        "amount": 10.0,
                        "score": 92.0,
                    },
                    {
                        "ts_code": "512880.SH",
                        "name": "国泰中证全指证券公司ETF",
                        "amount": 500.0,
                        "score": 91.0,
                    },
                ],
            },
        }
        representatives = research_representatives(primary_items(payload))
        self.assertEqual({item["code"] for item in representatives}, {"588200.SH", "512880.SH"})
        self.assertEqual({item["category_key"] for item in representatives}, {"半导体芯片", "证券金融"})

    def test_theme_ranking_adds_one_research_representative_per_mainline(self) -> None:
        payload = {
            "report_id": "mainline_r1",
            "result": {
                "basis_date": "2026-06-24",
                "theme_ranking": [
                    {"theme": "硬科技电子/半导体", "mainline_score_v6": 99, "top_etf": "588170.SH 半导体ETF、159516.SZ 半导体材料ETF"},
                    {"theme": "AI算力/通信", "mainline_score_v6": 90, "top_etf": "515050.SH 5GETF、159994.SZ 5G通信ETF"},
                ],
                "etf_top": [
                    {"ts_code": "588170.SH", "name": "半导体ETF", "amount": 100.0, "score": 99.0},
                    {"ts_code": "159516.SZ", "name": "半导体材料ETF", "amount": 900.0, "score": 98.0},
                ],
            },
        }
        items = primary_items(payload)
        representatives = research_representatives(items, payload)
        self.assertIn("515050.SH", {item["code"] for item in items})
        self.assertIn("510300.SH", {item["code"] for item in items})
        self.assertIn("159516.SZ", [item["code"] for item in representatives])
        self.assertIn("515050.SH", [item["code"] for item in representatives])

    def test_core_broad_index_seeds_are_research_representatives(self) -> None:
        payload = {
            "report_id": "mainline_r1",
            "result": {
                "basis_date": "2026-06-24",
                "theme_ranking": [
                    {"theme": "AI算力/通信", "mainline_score_v6": 90, "top_etf": "515050.SH 5GETF"},
                ],
                "etf_top": [],
            },
        }
        representatives = research_representatives(primary_items(payload), payload)
        by_code = {item["code"]: item for item in representatives}
        for code in ["510210.SH", "510050.SH", "510300.SH", "510500.SH", "512100.SH", "159915.SZ", "588000.SH"]:
            self.assertEqual(by_code[code]["valuation_model_type"], "broad_index")

    def test_research_prompt_is_single_etf_only(self) -> None:
        report = {"report_id": "r1", "basis_date": "2026-06-24"}
        item = {"code": "510300.SH", "name": "沪深300ETF", "theme": "宽基"}
        prompt = build_research_prompt(item, report)
        self.assertIn("唯一研究对象：510300.SH 沪深300ETF", prompt)
        self.assertIn("task_type 固定为 research", prompt)
        self.assertIn("fund_portfolio 只能作为已披露季报持仓", prompt)
        self.assertIn("valuation_model_type：broad_index", prompt)
        self.assertIn("核心宽基", prompt)

    def test_research_prompt_builds_complete_assembly_inputs(self) -> None:
        report = {"report_id": "r1", "basis_date": "2026-06-24"}
        item = {"code": "510300.SH", "name": "沪深300ETF", "theme": "宽基"}
        prompt = build_research_prompt(item, report)
        self.assertIn("本任务一次性完成产品结构", prompt)
        self.assertIn("fund_share 是份额变化的可用代理", prompt)
        self.assertIn("不要手写最终 ETFResearchReport", prompt)
        self.assertIn("data_gaps", prompt)
        self.assertIn("equity_risk_premium", prompt)
        self.assertIn("market_position_score", prompt)

    def test_queue_prompt_output_includes_traceable_execution_package(self) -> None:
        text = format_queue_prompt(
            {
                "report_id": "r1",
                "code": "510300.SH",
                "name": "沪深300ETF",
                "task_type": "research",
                "task_id": "task_abc",
                "run_id": "run_abc",
                "priority": 1,
                "stage": 1,
                "depends_on_task_type": "",
                "source_type": QUEUE_SOURCE_MAINLINE,
                "source_detail": "theme.okbbc.com/api/latest",
                "task_keyword": "MyInvestETF ETF完整深研 510300.SH 沪深300ETF",
                "prompt": "只研究这一只 ETF。",
            }
        )
        self.assertIn("队列任务元数据", text)
        self.assertIn("run_id：run_abc", text)
        self.assertIn("task_type：research", text)
        self.assertIn("depends_on_task_type：", text)
        self.assertIn("只执行本队列任务元数据对应的一只 ETF、一个 task_type", text)

    def test_type_specific_prompts_use_different_investment_bases(self) -> None:
        report = {"report_id": "r1", "basis_date": "2026-06-24"}
        mainline = build_research_prompt({"code": "588170.SH", "name": "科创半导体材料设备ETF", "theme": "半导体"}, report)
        defensive = build_research_prompt({"code": "512890.SH", "name": "红利低波ETF", "theme": "红利低波"}, report)
        cash_like = build_research_prompt({"code": "511360.SH", "name": "短融ETF", "theme": "现金替代"}, report)

        self.assertIn("valuation_model_type：mainline_theme", mainline)
        self.assertIn("theme_strength", mainline)
        self.assertIn("crowding_score", mainline)
        self.assertIn("valuation_model_type：factor_defensive", defensive)
        self.assertIn("dividend_spread", defensive)
        self.assertIn("style_opportunity_cost", defensive)
        self.assertIn("valuation_model_type：cash_like", cash_like)
        self.assertIn("duration_risk", cash_like)
        self.assertIn("只做现金替代资格检查", cash_like)

    def test_requested_etf_enqueue_marks_source(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "manual.sqlite"
            result = enqueue_requested_etf("510300.SH", name="沪深300ETF", db_path=db_path)
            with closing(connect(db_path)) as conn:
                rows = list_queue(conn)
            self.assertEqual(result["queued"], ["research"])
            self.assertEqual({row["source_type"] for row in rows}, {QUEUE_SOURCE_REQUEST})
            self.assertEqual([row["task_type"] for row in rows], ["research"])

    def test_cash_like_requested_etf_does_not_enqueue_deep_research(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "cash.sqlite"
            result = enqueue_requested_etf("511360.SH", name="短融ETF", db_path=db_path)
            with closing(connect(db_path)) as conn:
                rows = list_queue(conn)
            self.assertEqual(result["valuation_model_type"], "cash_like")
            self.assertEqual(result["queued"], [])
            self.assertEqual(result["skipped"], ["cash_like_no_deep_research"])
            self.assertEqual(rows, [])

    def test_latest_report_ignores_manual_etf_requests(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "latest.sqlite"
            init_db(db_path)
            with closing(connect(db_path)) as conn:
                upsert_report(
                    conn,
                    report_id="mainline_r1",
                    schema_version=None,
                    generated_at="2026-06-23T17:38:55+08:00",
                    basis_date="2026-06-23",
                    theme_report_id=None,
                    source_url="https://theme.okbbc.com/api/latest",
                    fetched_at="2026-06-24T07:15:49+00:00",
                    raw_path=None,
                )
                upsert_trackable_leader(
                    conn,
                    report_id="mainline_r1",
                    item={"code": "588170.SH", "name": "半导体材料设备ETF", "deep_score": 98.0},
                    created_at="2026-06-24T07:15:49+00:00",
                )
                upsert_report(
                    conn,
                    report_id="manual_etf_research_request_2026-06-24",
                    schema_version="manual_etf_research_request.v1",
                    generated_at="2026-06-24T15:02:47",
                    basis_date="2026-06-24",
                    theme_report_id=None,
                    source_url="/research?etf=510300.SH",
                    fetched_at="2026-06-24T07:02:47+00:00",
                    raw_path=None,
                )
                conn.commit()

                report = latest_report(conn)
                leaders = list_latest_leaders(conn)
            self.assertEqual(report["report_id"], "mainline_r1")
            self.assertEqual([row["code"] for row in leaders], ["588170.SH"])

    def test_ingest_prunes_lower_liquidity_same_category_from_existing_report(self) -> None:
        first_payload = {
            "report_id": "mainline_r1",
            "result": {
                "basis_date": "2026-06-23",
                "etf_top": [
                    {"ts_code": "588170.SH", "name": "华夏上证科创板半导体材料设备主题ETF", "amount": 100.0, "score": 95.0},
                    {"ts_code": "159842.SZ", "name": "银华中证全指证券公司ETF", "amount": 10.0, "score": 92.0},
                ],
            },
        }
        second_payload = {
            "report_id": "mainline_r1",
            "result": {
                "basis_date": "2026-06-23",
                "etf_top": [
                    {"ts_code": "588710.SH", "name": "华泰柏瑞上证科创板半导体材料设备主题ETF", "amount": 300.0, "score": 90.0},
                    {"ts_code": "512880.SH", "name": "国泰中证全指证券公司ETF", "amount": 500.0, "score": 91.0},
                ],
            },
        }
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "prune.sqlite"
            ingest_payload(first_payload, source_url="https://theme.okbbc.com/api/latest", db_path=db_path)
            ingest_payload(second_payload, source_url="https://theme.okbbc.com/api/latest", db_path=db_path)
            with closing(connect(db_path)) as conn:
                leaders = list_latest_leaders(conn)
                queue = list_queue(conn)
            self.assertEqual([row["code"] for row in leaders], ["512880.SH", "588710.SH"])
            self.assertEqual({row["code"] for row in queue}, {"512880.SH", "588710.SH"})

    def test_ingest_prunes_stale_queue_rows_not_in_research_representatives(self) -> None:
        first_payload = {
            "report_id": "mainline_r1",
            "result": {
                "basis_date": "2026-06-24",
                "etf_top": [
                    {"ts_code": "588200.SH", "name": "嘉实上证科创板芯片ETF", "amount": 900.0, "score": 98.0},
                ],
            },
        }
        second_payload = {
            "report_id": "mainline_r1",
            "result": {
                "basis_date": "2026-06-24",
                "theme_ranking": [
                    {
                        "theme": "硬科技电子/半导体",
                        "mainline_score_v6": 99.0,
                        "top_etf": "588170.SH 半导体ETF、159516.SZ 半导体材料ETF",
                    },
                ],
                "etf_top": [
                    {"ts_code": "588170.SH", "name": "半导体ETF", "amount": 100.0, "score": 99.0},
                    {"ts_code": "159516.SZ", "name": "半导体材料ETF", "amount": 900.0, "score": 98.0},
                    {"ts_code": "588200.SH", "name": "嘉实上证科创板芯片ETF", "amount": 800.0, "score": 97.0},
                ],
            },
        }
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "queue-prune.sqlite"
            ingest_payload(first_payload, source_url="https://theme.okbbc.com/api/latest", db_path=db_path)
            ingest_payload(second_payload, source_url="https://theme.okbbc.com/api/latest", db_path=db_path)
            with closing(connect(db_path)) as conn:
                queue = list_queue(conn)
                queue_codes = {row["code"] for row in queue}
                source_by_code = {row["code"]: row["source_type"] for row in queue}
                priority_by_code = {row["code"]: row["priority"] for row in queue}
            self.assertIn("159516.SZ", queue_codes)
            self.assertNotIn("588200.SH", queue_codes)
            self.assertEqual(source_by_code["159516.SZ"], QUEUE_SOURCE_MAINLINE)
            self.assertEqual(source_by_code["510300.SH"], QUEUE_SOURCE_BROAD_INDEX)
            self.assertLess(priority_by_code["510300.SH"], priority_by_code["159516.SZ"])

    def test_requested_prompts_do_not_require_api_index_membership(self) -> None:
        report = {"report_id": "manual_etf_research_request_2026-06-24", "basis_date": "2026-06-24"}
        item = {"code": "510300.SH", "name": "沪深300ETF"}
        self.assertIn("不要求出现在 /api/index", build_requested_research_prompt(item, report))

    def test_layout_uses_unified_header_footer_shell(self) -> None:
        page = render_layout("title", "<p>body</p>").decode("utf-8")
        self.assertIn('<div data-myinvest-header></div>', page)
        self.assertIn('<div data-myinvest-footer></div>', page)
        self.assertIn(f'<script src="{HEADER_SCRIPT_URL}" data-target="[data-myinvest-header]" defer></script>', page)
        self.assertIn(f'<script src="{FOOTER_SCRIPT_URL}" data-target="[data-myinvest-footer]" defer></script>', page)
        self.assertIn(f'/static/styles.css?v={STATIC_ASSET_VERSION}', page)

    def test_layout_removes_local_header_navigation(self) -> None:
        page = render_layout("title", "<p>body</p>").decode("utf-8")
        self.assertNotIn('<header class="app-header">', page)
        self.assertNotIn('<nav class="top-nav">', page)

    def test_home_queue_rows_are_grouped_by_etf(self) -> None:
        rows = [
            {
                "priority": 1,
                "stage": 1,
                "source_type": QUEUE_SOURCE_MAINLINE,
                "code": "588170.SH",
                "name": "华夏上证科创板半导体材料设备主题ETF",
                "task_type": "research",
                "status": "pending",
                "task_keyword": "research keyword",
            },
        ]
        html = render_queue_rows(rows)
        self.assertEqual(html.count("<tr>"), 1)
        self.assertIn("research:pending", html)
        self.assertIn("research keyword", html)

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
            "raw_json": '{"category_key":"沪深300","valuation_model_type":"broad_index"}',
            "xueqiu_url": "https://xueqiu.com/S/SH510300",
        }
        summary = leader_to_summary(row)
        self.assertEqual(summary["category_key"], "沪深300")
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
                '{"valuation":{"current_price":4.05,"valuation_percentile":88.066369,'
                '"undervalued_score":65,"liquidity_score":80,'
                '"tracking_score":90,"portfolio_role_score":70,"risk_adjusted_score":72},'
                '"conclusion":{"summary":"确定性规则评分"}}'
            ),
        }
        signal = valuation_signal_summary(row)
        self.assertEqual(signal["bucket"], "medium")
        self.assertEqual(signal["liquidity_score"], 80.0)
        self.assertEqual(signal["current_price"], 4.05)
        self.assertEqual(signal["valuation_percentile"], 88.066369)

    def test_signal_matrix_surfaces_valuation_percentile(self) -> None:
        row = {
            "valuation_low": 4.46,
            "valuation_mid": 4.85,
            "valuation_high": 5.24,
            "valuation_unit": "CNY/fund_share",
            "valuation_method": "broad-index-valuation+ERP",
            "heavy_position_view": "工具仓可用",
            "raw_json": (
                '{"valuation_model_type":"broad_index","sleeve_key":"core_wide_etf",'
                '"valuation":{"valuation_percentile":88.066369,"undervalued_score":17.65,'
                '"liquidity_score":100,"tracking_score":84.5,"portfolio_role_score":95.72,'
                '"risk_adjusted_score":66.45},"conclusion":{"summary":"工具仓跟踪"}}'
            ),
        }
        valuation_signal = valuation_signal_summary(row)
        html = render_signal_matrix(
            {"risk_flags": []},
            valuation_signal,
            {"posture": "工具仓跟踪", "conclusion": "测试"},
        )
        self.assertIn("估值分位", html)
        self.assertIn("88.07%", html)

    def test_valuation_chart_uses_price_language_and_explains_missing_close_line(self) -> None:
        runs = [
            {
                "valuation_low": 3.7,
                "valuation_mid": 4.0,
                "valuation_high": 4.3,
                "valuation_method": "NAV+index-valuation",
                "heavy_position_view": "工具仓可用",
                "research_date": "2026-06-24",
            }
        ]
        html = render_valuation_chart(runs, [])
        self.assertIn("ETF参考价格区间历史", html)
        self.assertIn("2024-09-24以来收盘价待入库", html)
        self.assertIn('class="reference-level-line reference-level-line-low"', html)
        self.assertIn('class="reference-level-line reference-level-line-mid"', html)
        self.assertIn('class="reference-level-line reference-level-line-high"', html)
        self.assertNotIn("ETF参考价值区间历史", html)

    def test_valuation_chart_renders_close_line_when_price_cache_exists(self) -> None:
        runs = [
            {
                "valuation_low": 3.7,
                "valuation_mid": 4.0,
                "valuation_high": 4.3,
                "valuation_method": "NAV+index-valuation",
                "heavy_position_view": "工具仓可用",
                "research_date": "2026-06-24",
            }
        ]
        prices = [
            {"trade_date": "2026-06-23", "open_price": 4.0, "high_price": 4.1, "low_price": 3.9, "close_price": 4.05},
            {"trade_date": "2026-06-24", "open_price": 4.05, "high_price": 4.2, "low_price": 4.0, "close_price": 4.1},
        ]
        html = render_valuation_chart(runs, prices)
        self.assertIn("2024-09-24以来收盘价", html)
        self.assertIn("2024-09-24以来收盘价折线叠加ETF参考价格区间图", html)
        self.assertIn('class="close-price-line"', html)
        self.assertIn('class="current-price-line"', html)
        self.assertIn('class="reference-level-line reference-level-line-low"', html)
        self.assertIn('class="reference-level-line reference-level-line-mid"', html)
        self.assertIn('class="reference-level-line reference-level-line-high"', html)
        self.assertIn("低 3.70", html)
        self.assertIn("中枢 4.00", html)
        self.assertIn("高 4.30", html)
        self.assertIn("当前价 4.10", html)
        self.assertIn("当前价格", html)
        self.assertIn("最新低/中枢/高", html)
        self.assertIn(">4.35<", html)
        self.assertIn(">3.65<", html)
        self.assertNotIn(">5.30<", html)
        self.assertNotIn(">2.70<", html)
        self.assertNotIn("kline-candle", html)

    def test_reference_price_explanation_shows_formula_and_current_run_math(self) -> None:
        latest = {
            "valuation_method": "broad-index-valuation+ERP",
        }
        valuation_signal = {
            "valuation_model_type": "broad_index",
            "nav": 4.9707,
            "current_price": 4.967,
            "valuation_range": {
                "low": 4.46395,
                "mid": 4.85212,
                "high": 5.24029,
                "method": "broad-index-valuation+ERP",
            },
        }
        html = render_reference_price_explanation(latest, valuation_signal)
        self.assertIn("参考价格口径", html)
        self.assertIn("参考低位", html)
        self.assertIn("参考中枢", html)
        self.assertIn("参考高位", html)
        self.assertIn("本页采用 broad-index-valuation+ERP", html)
        self.assertIn("单位净值 NAV 4.9707", html)
        self.assertIn("综合调整 -2.39%", html)
        self.assertIn("带宽约 +/-8.00%", html)
        self.assertIn("估值分位调整", html)
        self.assertIn("股权风险溢价调整", html)

    def test_home_card_uses_research_and_price_cache_for_broad_seed(self) -> None:
        leader = {
            "code": "510210.SH",
            "name": "富国上证综指ETF",
            "deep_rating": "B",
            "deep_label": "核心宽基ETF",
            "deep_score": 80.0,
            "market_json": "{}",
            "raw_json": '{"category_key":"上证综指","valuation_model_type":"broad_index","sleeve_key":"core_wide_etf"}',
            "xueqiu_url": "https://xueqiu.com/S/SH510210",
        }
        research = {
            "task_type": "research",
            "valuation_mid": 1.003848,
            "heavy_position_view": "工具仓可用",
            "raw_json": '{"valuation":{"current_price":1.032}}',
        }
        prices = [{"trade_date": "2026-06-24", "open_price": 1.03, "high_price": 1.04, "low_price": 1.02, "close_price": 1.032}]
        html = render_etf_cards([leader], {"510210.SH": research}, {"510210.SH": prices})
        self.assertIn("当前价格", html)
        self.assertIn("参考中枢", html)
        self.assertIn("底仓资格", html)
        self.assertIn("工具仓可用", html)
        self.assertNotIn("PE TTM", html)

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
