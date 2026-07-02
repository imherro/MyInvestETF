from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import myinvestetf.web as web_module
from myinvestetf.db import (
    QUEUE_SOURCE_BROAD_INDEX,
    QUEUE_SOURCE_DEFENSIVE,
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
    api_catalog,
    build_common_ask_answers,
    decision_matrix_summary,
    leader_to_summary,
    openapi_json,
    portfolio_use_view,
    render_api_overview,
    render_ask_widget,
    render_contrarian_signal,
    render_current_decision_summary,
    render_etf_cards,
    render_market_context,
    render_reference_price_explanation,
    render_decision_signal,
    render_signal_matrix,
    render_strategy_decision,
    render_valuation_chart,
    render_queue_rows,
    render_layout,
    render_taxonomy_profile,
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
        self.assertIn("159201.SZ", {item["code"] for item in items})
        self.assertIn("512890.SH", {item["code"] for item in items})
        self.assertIn("159516.SZ", [item["code"] for item in representatives])
        self.assertIn("515050.SH", [item["code"] for item in representatives])
        self.assertIn("159201.SZ", [item["code"] for item in representatives])
        self.assertIn("512890.SH", [item["code"] for item in representatives])

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

    def test_defensive_seeds_are_research_representatives(self) -> None:
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
        expected = {
            "159201.SZ": "自由现金流",
            "512890.SH": "红利低波",
        }
        for code, category in expected.items():
            self.assertEqual(by_code[code]["valuation_model_type"], "factor_defensive")
            self.assertEqual(by_code[code]["sleeve_key"], "defensive_quality")
            self.assertEqual(by_code[code]["category_key"], category)

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
            self.assertIn("159201.SZ", queue_codes)
            self.assertIn("512890.SH", queue_codes)
            self.assertNotIn("588200.SH", queue_codes)
            self.assertEqual(source_by_code["159516.SZ"], QUEUE_SOURCE_MAINLINE)
            self.assertEqual(source_by_code["510300.SH"], QUEUE_SOURCE_BROAD_INDEX)
            self.assertEqual(source_by_code["159201.SZ"], QUEUE_SOURCE_DEFENSIVE)
            self.assertEqual(source_by_code["512890.SH"], QUEUE_SOURCE_DEFENSIVE)
            self.assertLess(priority_by_code["510300.SH"], priority_by_code["159516.SZ"])
            self.assertLess(priority_by_code["159201.SZ"], priority_by_code["159516.SZ"])
            self.assertLess(priority_by_code["512890.SH"], priority_by_code["159516.SZ"])

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
        self.assertEqual(summary["taxonomy_profile"]["etf_type"], "broad_index_core")
        self.assertEqual(summary["links"]["page"], "/etfs/510300.SH")
        self.assertEqual(summary["links"]["api"], "/api/etfs/510300.SH")

    def test_api_catalog_lists_public_endpoints_and_safety(self) -> None:
        catalog = api_catalog("http://127.0.0.1:8017")
        groups = catalog["groups"]
        endpoints = [
            endpoint
            for group in groups
            for endpoint in group["endpoints"]
        ]
        paths = {endpoint["path"]: endpoint for endpoint in endpoints}

        self.assertEqual(catalog["system"]["name"], "MyInvestETF")
        self.assertEqual(catalog["base_url"], "http://127.0.0.1:8017")
        self.assertEqual(catalog["docs"]["docs"], "/docs")
        self.assertEqual(catalog["docs"]["redoc"], "/redoc")
        self.assertEqual(catalog["docs"]["openapi_json"], "/openapi.json")
        self.assertEqual(catalog["total_endpoints"], len(endpoints))
        self.assertEqual(
            {group["name"] for group in groups},
            {"文档入口", "Web 页面", "当前数据", "历史数据", "分析结果", "系统状态"},
        )
        self.assertTrue(paths["/api"]["read_only"])
        self.assertFalse(paths["/research"]["read_only"])
        self.assertIn("/api/etf/{code}/profile", paths)
        self.assertIn("/api/factors/{etf}", paths)
        self.assertIn("/api/factors/ic/{factor}", paths)
        self.assertIn("/api/factors/exposure/{etf}", paths)
        self.assertIn("/api/score/{etf}", paths)
        self.assertIn("/api/ask/{etf}", paths)
        self.assertTrue(paths["/api/ask/{etf}"]["read_only"])
        self.assertIn("/api/score/decompose/{etf}", paths)
        self.assertIn("/api/decision/state/{etf}", paths)
        self.assertIn("/api/strategy/contrarian/{etf}", paths)
        self.assertIn("/api/strategy/route/{etf}", paths)
        self.assertIn("/api/replay/{etf}", paths)
        self.assertIn("/api/replay/{etf}/stability", paths)
        self.assertIn("/api/replay/{etf}/regime-path", paths)
        self.assertIn("/api/health/system", paths)
        self.assertIn("/api/health/data", paths)
        self.assertIn("/api/health/factors", paths)
        self.assertIn("/api/health/regime", paths)
        self.assertIn("/api/health/report", paths)
        self.assertIn("/api/market/structure", paths)
        self.assertIn("/api/market/breadth", paths)
        self.assertIn("/api/market/liquidity", paths)
        self.assertIn("/api/market/regime-v2", paths)
        self.assertIn("/api/latest", [item["path"] for item in catalog["recommended_entrypoints"]])
        self.assertIn("/api/ask/{etf}", [item["path"] for item in catalog["recommended_entrypoints"]])
        self.assertIn("/api/strategy/contrarian/{etf}", [item["path"] for item in catalog["recommended_entrypoints"]])
        self.assertIn("/api/strategy/route/{etf}", [item["path"] for item in catalog["recommended_entrypoints"]])
        self.assertIn("不触发重计算", " ".join(catalog["safety"]["boundaries"]))

    def test_openapi_json_contains_catalog_paths(self) -> None:
        payload = openapi_json("http://127.0.0.1:8017").decode("utf-8")
        parsed = json.loads(payload)
        self.assertEqual(parsed["openapi"], "3.0.3")
        self.assertIn("/api", parsed["paths"])
        self.assertIn("/api/latest", parsed["paths"])
        self.assertIn("/api/etfs/{code}", parsed["paths"])
        self.assertIn("/api/etf/{code}/profile", parsed["paths"])
        self.assertIn("/api/factors/{etf}", parsed["paths"])
        self.assertIn("/api/factors/ic/{factor}", parsed["paths"])
        self.assertIn("/api/score/{etf}", parsed["paths"])
        self.assertIn("/api/ask/{etf}", parsed["paths"])
        self.assertIn("/api/score/decompose/{etf}", parsed["paths"])
        self.assertIn("/api/decision/state/{etf}", parsed["paths"])
        self.assertIn("/api/strategy/contrarian/{etf}", parsed["paths"])
        self.assertIn("/api/strategy/route/{etf}", parsed["paths"])
        self.assertIn("/api/replay/{etf}", parsed["paths"])
        self.assertIn("/api/replay/{etf}/stability", parsed["paths"])
        self.assertIn("/api/replay/{etf}/regime-path", parsed["paths"])
        self.assertIn("/api/health/system", parsed["paths"])
        self.assertIn("/api/health/data", parsed["paths"])
        self.assertIn("/api/health/factors", parsed["paths"])
        self.assertIn("/api/health/regime", parsed["paths"])
        self.assertIn("/api/health/report", parsed["paths"])
        self.assertIn("/api/market/regime-v2", parsed["paths"])
        self.assertIn("303", parsed["paths"]["/research"]["get"]["responses"])

    def test_home_api_overview_mentions_entrypoints_and_boundaries(self) -> None:
        html = render_api_overview(api_catalog(""))
        self.assertIn("接口说明", html)
        self.assertIn("/api/latest", html)
        self.assertIn("安全边界", html)
        self.assertIn("不触发重计算", html)

    def test_api_ask_returns_unified_policy_answer(self) -> None:
        original_signal_payload = web_module.decision_signal_payload_for_etf
        original_health_payload = web_module.research_health_payload
        try:
            web_module.decision_signal_payload_for_etf = lambda code: {
                "schema_version": "myinvestetf.decision_signal.v1",
                "code": code,
                "name": "沪深300ETF",
                "taxonomy_profile": {"etf_type": "broad_index_core", "subtype": "core_beta"},
                "regime_v2": {"regime": "risk_on", "confidence": 0.72},
                "decision_signal": {
                    "score": 80.0,
                    "confidence": 0.81,
                    "taxonomy_type": "broad_index_core",
                    "state": {"regime": "risk_on"},
                },
                "constraints": {"read_only": True, "research_only": True},
            }
            web_module.research_health_payload = lambda: {
                "health_report": {
                    "data_quality": {"gate_status": "pass"},
                    "regime_quality": {"gate_status": "pass"},
                    "factor_quality": {"gate_status": "pass"},
                    "report_quality": {"gate_status": "pass"},
                }
            }

            payload = json.loads(web_module.api_ask_for_etf("510300.SH", "q=现在能不能买？").decode("utf-8"))
        finally:
            web_module.decision_signal_payload_for_etf = original_signal_payload
            web_module.research_health_payload = original_health_payload

        self.assertEqual(payload["schema_version"], "myinvestetf.ask.v1")
        self.assertEqual(payload["intent"]["type"], "buy_assessment")
        self.assertEqual(payload["decision"]["score"], 80.0)
        self.assertEqual(payload["final_answer"]["conclusion"]["type"], "participate")
        self.assertEqual(payload["constraints"]["final_answer_policy"], "AnswerPolicyEngine")
        self.assertTrue(payload["constraints"]["read_only"])
        rendered = json.dumps(payload, ensure_ascii=False)
        for forbidden in ["买入", "卖出", "现金金额", "份额数量"]:
            self.assertNotIn(forbidden, rendered)

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
        self.assertIn("中枢 4.8521 = 4.9707 * (1 - 2.39%)", html)
        self.assertIn("低位 4.4640 = 4.8521 * (1 - 8.00%)", html)
        self.assertIn("高位 5.2403 = 4.8521 * (1 + 8.00%)", html)
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
        self.assertIn("组合使用判断", html)
        self.assertIn("阶段性工具仓可用，不等于当前买入", html)
        self.assertNotIn("PE TTM", html)

    def test_portfolio_use_view_expands_tool_position_language(self) -> None:
        self.assertEqual(portfolio_use_view("工具仓可用"), "阶段性工具仓可用，不等于当前买入")
        self.assertEqual(portfolio_use_view("底仓候选"), "底仓候选，仍需结合估值和市场状态")
        self.assertEqual(portfolio_use_view(""), "待入库")

    def test_market_context_section_renders_drawdown_state(self) -> None:
        html = render_market_context(
            {
                "etf_code": "510300.SH",
                "regime": {"regime": "risk_off", "confidence": 0.7, "data_points": 60},
                "drawdown": {
                    "current_drawdown": 0.08,
                    "max_drawdown_rolling": 0.18,
                    "drawdown_percentile": 82.5,
                    "recovery_speed": 0.002,
                    "duration_days": 9,
                    "as_of_date": "2026-06-24",
                    "data_points": 60,
                },
            }
        )
        self.assertIn("市场状态与回撤", html)
        self.assertIn("风险收缩", html)
        self.assertIn("当前回撤", html)
        self.assertIn("8.00%", html)
        self.assertIn("82.50%", html)

    def test_taxonomy_profile_section_renders_reasons(self) -> None:
        html = render_taxonomy_profile(
            {
                "etf_type": "theme_lifecycle",
                "subtype": "structural_theme",
                "lifecycle_stage": "expansion",
                "classification_confidence": 0.82,
                "classification_reasons": ["source:theme lifecycle candidate"],
                "legacy_valuation_model_type": "mainline_theme",
                "legacy_sleeve_key": "mainline_etf",
            }
        )
        self.assertIn("ETF分类画像", html)
        self.assertIn("主题生命周期", html)
        self.assertIn("扩张", html)
        self.assertIn("82.00%", html)
        self.assertIn("source:theme lifecycle candidate", html)

    def test_decision_signal_section_renders_decomposition(self) -> None:
        html = render_decision_signal(
            {
                "score": 66.5,
                "confidence": 0.75,
                "state": {"regime": "risk_on", "score_band": "watch", "trend_state": "uptrend", "state_code": "risk_on:watch:uptrend"},
                "component_scores": {"momentum": 80.0, "flow": 60.0, "valuation": 50.0, "risk": 70.0},
                "adjusted_weights": {"momentum": 0.40, "flow": 0.25, "valuation": 0.15, "risk": 0.20},
                "factor_contributions": {"momentum": 32.0, "flow": 15.0, "valuation": 7.5, "risk": 14.0},
                "explanation": "Score 66.50 is driven by momentum under risk_on.",
            }
        )

        self.assertIn("状态感知研究评分", html)
        self.assertIn("Decision Score", html)
        self.assertIn("risk_on:watch:uptrend", html)
        self.assertIn("动态权重", html)
        self.assertIn("不输出交易动作", html)

    def test_contrarian_signal_section_renders_probability_mode(self) -> None:
        html = render_contrarian_signal(
            {
                "enabled": True,
                "scores": {"reversal_probability": 0.72, "exhaustion_score": 0.81, "capitulation_score": 0.68},
                "conditions": {
                    "drawdown_extreme": True,
                    "regime_stress": True,
                    "liquidity_stress": False,
                    "volatility_stress": True,
                    "governance_allowed": True,
                },
                "adjusted_interpretation": {
                    "risk_adjusted_score": 63.0,
                    "original_decision_score": 60.0,
                    "final_view": "probabilistic_bottom_zone",
                    "explanation": "不是趋势买点。",
                },
                "evidence": {
                    "current_drawdown": 0.25,
                    "drawdown_percentile": 0.96,
                    "extreme_proximity": 0.93,
                    "regime": "shock",
                    "volatility_20": 0.035,
                    "liquidity_score": 0.35,
                    "flow_score": 0.32,
                    "governance_gate": "pass",
                },
            }
        )

        self.assertIn("抄底概率模式", html)
        self.assertIn("概率底部观察区", html)
        self.assertIn("72.00%", html)
        self.assertIn("波动压力", html)
        self.assertIn("系统健康允许", html)
        self.assertIn("20日波动", html)
        self.assertIn("不覆盖 Decision Score", html)
        self.assertIn("不是趋势买点", html)

    def test_strategy_decision_section_renders_active_mode(self) -> None:
        html = render_strategy_decision(
            {
                "active_mode": "contrarian",
                "confidence": 0.74,
                "reasoning": {
                    "regime_reason": "regime=shock",
                    "flow_reason": "flow_score=0.35",
                    "drawdown_reason": "drawdown_extreme=True",
                    "governance_reason": "gate=pass",
                },
                "suppressed_mode": "trend",
                "signals": {"trend_score": 0.62, "contrarian_score": 0.81, "decision_score": 0.70},
                "final_interpretation": "当前由抄底概率模式主导。",
            }
        )

        self.assertIn("策略路由", html)
        self.assertIn("抄底概率模式", html)
        self.assertIn("81.00%", html)
        self.assertIn("trend", html)
        self.assertIn("不修改原始 Decision Score", html)

    def test_decision_matrix_uses_etf_language(self) -> None:
        matrix = decision_matrix_summary(
            {"bucket": "not_applicable", "label": "不依赖行业主线", "applies": False},
            {"bucket": "high", "valuation_model_type": "factor_defensive"},
            market_signal={"bucket": "watch", "label": "市场仓位信号中性"},
            taxonomy_profile={"etf_type": "factor_strategy"},
        )
        self.assertEqual(matrix["posture"], "收益防御候选")
        self.assertIn("策略型收益防御ETF不依赖行业主线", matrix["conclusion"])
        self.assertNotIn("上游/产品", matrix["conclusion"])
        self.assertFalse(matrix["theme_applicable"])

    def test_decision_matrix_uses_theme_only_for_mainline_etf(self) -> None:
        matrix = decision_matrix_summary(
            {"bucket": "strong", "label": "主题主线信号强", "applies": True},
            {"bucket": "high", "valuation_model_type": "mainline_theme"},
            market_signal={"bucket": "strong", "label": "市场仓位信号偏积极"},
            taxonomy_profile={"etf_type": "theme_lifecycle"},
        )
        self.assertEqual(matrix["posture"], "主线进攻候选")
        self.assertTrue(matrix["theme_applicable"])
        self.assertIn("theme行业主线", matrix["conclusion"])

    def test_current_decision_summary_surfaces_page_conclusion(self) -> None:
        html = render_current_decision_summary(
            {
                "posture": "工具仓跟踪",
                "conclusion": "theme行业主线较强，但ETF估值、拥挤或流动性仍需观察，更适合作为工具仓跟踪",
            },
            {
                "label": "ETF估值或拥挤压力较高",
                "valuation_range": {"low": 4.360868, "mid": 4.740074, "high": 5.11928},
            },
            {
                "score": 74.97408,
                "confidence": 0.791369,
                "state": {"state_code": "risk_on:strong:uptrend"},
            },
            4.998,
        )

        self.assertIn("当前研究结论", html)
        self.assertIn("工具仓跟踪", html)
        self.assertIn("更适合作为工具仓跟踪", html)
        self.assertIn("参考低 / 中 / 高", html)
        self.assertIn("4.36 / 4.74 / 5.12", html)
        self.assertIn("risk_on:strong:uptrend", html)

    def test_ask_widget_shows_entry_common_questions_and_answers(self) -> None:
        answers = build_common_ask_answers(
            code="510300.SH",
            decision_signal={
                "score": 80.0,
                "confidence": 0.81,
                "taxonomy_type": "broad_index_core",
                "state": {"regime": "risk_on"},
            },
            taxonomy_profile={"etf_type": "broad_index_core", "subtype": "core_beta"},
            market_regime={"regime": "risk_on", "confidence": 0.7},
            governance_report={
                "data_quality": {"gate_status": "pass"},
                "regime_quality": {"gate_status": "pass"},
                "factor_quality": {"gate_status": "pass"},
                "report_quality": {"gate_status": "pass"},
            },
        )
        html = render_ask_widget("510300.SH", answers)

        self.assertEqual(len(answers), 3)
        self.assertIn("问这个ETF", html)
        self.assertIn('/api/ask/510300.SH', html)
        self.assertIn("现在能不能参与？", html)
        self.assertIn("风险大不大？", html)
        self.assertIn("当前是什么状态？", html)
        self.assertIn("结构支持参与评估", html)
        self.assertIn("依据", html)
        self.assertIn("风险", html)
        self.assertIn("等待自定义提问", html)

    def test_report_meta_requires_report_id(self) -> None:
        with self.assertRaises(ValueError):
            report_meta({"report": {}})

    def test_xueqiu_link_for_etf(self) -> None:
        link = xueqiu_etf_link("510300.SH")
        self.assertIn('href="https://xueqiu.com/S/SH510300"', link)
        self.assertIn('target="_blank"', link)


if __name__ == "__main__":
    unittest.main()
