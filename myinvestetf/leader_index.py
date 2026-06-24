from __future__ import annotations

import json
import re
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH, LEADER_INDEX_URL, RAW_DATA_DIR
from .db import (
    QUEUE_SOURCE_REQUEST,
    connect,
    has_profile_work,
    init_db,
    upsert_queue_item,
    upsert_report,
    upsert_trackable_leader,
    utc_now,
)

ETF_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


ETF_REPORT_SCHEMA_INSTRUCTION = """ETFResearchReport 结构化输出要求：
- 最终只输出一个 JSON object，不要输出 Markdown 包裹。
- JSON 必须符合 core/schema/etf_report.py 中 ETFResearchReport。
- 顶层字段固定为：schema_version, report_version, report_hash, run_id, etf_code, etf_name, source_report_id, task_type, research_date, status, title, summary, product_profile, holdings_profile, valuation, base_position_view, risk, conclusion, evidence, assumptions, data_gaps。
- 禁止输出 schema 以外的额外字段；禁止把未定义内容塞进自由 dict。
- etf_code 使用唯一研究对象代码，etf_name 使用唯一研究对象名称，source_report_id 使用入口 report_id。
- research_date 必须使用入口 basis_date。
- run_id 必须等于 hash(etf_code + task_type + research_date + schema_version)，可省略让导入端自动生成；如果提供错误 run_id 会被拒绝。
- product_profile 必须包含 fund_type, tracking_index, asset_class, portfolio_role, fee_note, liquidity_note, tracking_note。
- holdings_profile 必须包含 holdings_disclosure_date, top_holdings, concentration_note, overlap_note, disclosure_lag_note。
- valuation 必须包含 current_price, nav, premium_discount, underlying_pe, underlying_pb, valuation_percentile, reference_value_low, reference_value_mid, reference_value_high, unit, method, confidence, key_assumptions；可包含 engine_version, undervalued_score, liquidity_score, tracking_score, portfolio_role_score, risk_adjusted_score。
- risk 必须包含 liquidity_risk, tracking_risk, concentration_risk, sentiment_risk, invalidation_conditions。
- conclusion 必须包含 grade, confidence, summary；grade 必须等于 base_position_view。
- evidence 是对象数组，每项必须包含 source, date, url, purpose, detail。
- assumptions 和 data_gaps 都是字符串数组。
- base_position_view/grade 只能是：不适合底仓、观察、工具仓可用、底仓候选、估值或拥挤暂缓。
- task_type 只能是 profile 或 valuation；status 只能是 complete、draft、blocked；confidence 只能是 low、medium、high。"""


VALUATION_ASSEMBLY_INPUT_INSTRUCTION = """ETF valuation assembly_input 结构化要求：
- 你的角色是 ETF 结构化输入构建器，不是最终报告生成器。
- 不要手写最终 ETFResearchReport；最终报告必须由 scripts/build_research_report.py 或 core/report.build_etf_report(...) 生成。
- 不要临场计算最终参考价值区间、signal、grade 或 report_hash；这些由 deterministic engine 生成。
- assembly_input 必须是一个 JSON object，至少包含 etf_code, etf_name, source_report_id, task_type, research_date, product_profile, holdings_inputs, valuation_inputs, liquidity_inputs, tracking_inputs, risk_signals, evidence, assumptions, data_gaps。
- task_type 固定为 valuation；research_date 使用入口 basis_date。
- valuation_inputs 放 ETF 估值输入：current_price, nav/unit_nav, premium_discount, underlying_pe, underlying_pb, valuation_percentile, unit。
- liquidity_inputs 放 ETF 流动性输入：turnover_amount, fund_size, share_change_ratio；fund_share 是份额变化的可用代理。
- tracking_inputs 放 tracking_error、discount_premium_history_note、index_replication_note 等跟踪质量输入。
- holdings_inputs 放 holdings_disclosure_date, top_holdings, concentration_ratio, concentration_note, overlap_note, disclosure_lag_note；必须说明 fund_portfolio 是披露滞后口径，不是实时完整持仓。
- risk_signals 放 liquidity_risk, tracking_risk, concentration_risk, sentiment_risk, invalidation_conditions。
- 所有来源必须进入 evidence：source, date, url, purpose, detail。
- 如无法取得官方净申购赎回、长期估值分位、实时完整持仓、组合重叠等字段，必须写入 data_gaps，不得假装已验证。
- 禁止输出交易指令、现金金额、份额数量或买卖建议。"""


REPORT_EXPLAINER_INSTRUCTION = """你是 A 股 ETF 研究报告解释器。

输入是已经通过 schema 校验的 ETFResearchReport。你的任务是解释，不是计算。

禁止：
- 不得修改任何数值、参考价值区间、signal、grade、report_hash 或 run_id。
- 不得重新估值。
- 不得引入新外部数据。
- 不得给出新的买卖建议、现金金额或份额数量。
- 不得用自己的判断覆盖系统结论。

输出只做五件事：
1. 解释 ETF 产品结构和组合角色。
2. 解释已有净值、折溢价、底层指数估值和 signal 的含义。
3. 解释 risk 字段中的主要风险来源。
4. 解释系统为什么给出当前 base_position_view / conclusion.grade。
5. 用通俗语言总结结论。"""


def fetch_index(url: str = LEADER_INDEX_URL, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MyInvestETF/0.1 (+local research workbench)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def primary_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    key_results = payload.get("key_results") or {}
    primary = key_results.get("primary_output") or {}
    items = primary.get("items") or []
    if not isinstance(items, list):
        raise ValueError("key_results.primary_output.items is not a list")
    clean_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("ts_code") or "")
        name = str(item.get("name") or item.get("fund_name") or "")
        if ETF_CODE_RE.match(code) and name:
            normalized = dict(item)
            normalized["code"] = code
            normalized["name"] = name
            clean_items.append(normalized)
    return clean_items


def report_meta(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("report") or {}
    report_id = report.get("report_id") or payload.get("report_id")
    if not report_id:
        raise ValueError("Missing report.report_id")
    return {
        "report_id": report_id,
        "schema_version": report.get("schema_version"),
        "generated_at": report.get("generated_at"),
        "basis_date": report.get("basis_date"),
        "theme_report_id": report.get("theme_report_id"),
    }


def save_raw_payload(payload: dict[str, Any], report_id: str, raw_dir: Path = RAW_DATA_DIR) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_report_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", report_id)
    path = raw_dir / f"{safe_report_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_profile_prompt(item: dict[str, Any], report: dict[str, Any]) -> str:
    code = item["code"]
    name = item["name"]
    theme = item.get("theme") or item.get("asset_class") or ""
    report_id = report["report_id"]
    basis_date = report.get("basis_date") or ""
    return f"""在 C:\\Users\\kunpeng\\Documents\\MyInvestETF 中执行 ETF 产品结构深研。

唯一研究对象：{code} {name}。

入口信息：
- report_id：{report_id}
- basis_date：{basis_date}
- 主题/资产类别：{theme}

硬约束：
- 只研究这一只 ETF，禁止同时研究其他 ETF。
- Tushare 是结构化主源，优先使用 fund_basic、fund_daily、fund_nav、fund_share、fund_portfolio 和 index_daily。
- 网络资料只作为补充证据，必须记录来源和日期。
- 本任务只做产品结构、指数、持仓、流动性、跟踪和组合角色底稿，不给最终参考价值区间。
- fund_portfolio 只能作为已披露季报持仓，不得表述为实时完整底仓。
- 不输出交易指令、不输出现金金额、不输出份额数量。

必须覆盖：
- 产品结构：基金类型、跟踪指数、资产类别、费率、规模和流动性。
- 底层指数：指数编制逻辑、行业/主题暴露、适合宽基/行业/主题/债券/现金替代的哪种角色。
- 底仓逻辑：是否适合作为底仓、工具仓、防守仓、现金替代或卫星仓。
- 持仓披露：前十大持仓、披露日期、集中度、披露滞后和实时完整持仓缺口。
- 跟踪质量：跟踪误差、折溢价、流动性和指数复制风险。
- 证伪条件：哪些规模、流动性、跟踪、指数估值或持仓变化会推翻当前角色判断。

profile 任务 schema 规则：
- task_type 必须为 profile。
- valuation.reference_value_low / reference_value_mid / reference_value_high 必须为 null。
- valuation.method 写“profile-only”，valuation.confidence 写 low/medium/high。

{ETF_REPORT_SCHEMA_INSTRUCTION}

完成后将 task_type='profile' 的结构化结果通过 scripts/import_research_run.py 入库。"""


def build_valuation_prompt(item: dict[str, Any], report: dict[str, Any]) -> str:
    code = item["code"]
    name = item["name"]
    theme = item.get("theme") or item.get("asset_class") or ""
    report_id = report["report_id"]
    basis_date = report.get("basis_date") or ""
    return f"""在 C:\\Users\\kunpeng\\Documents\\MyInvestETF 中执行 ETF 估值刷新输入构建。

唯一研究对象：{code} {name}。

入口信息：
- report_id：{report_id}
- basis_date：{basis_date}
- 主题/资产类别：{theme}

前置依赖：
- 先读取本地 etf_research_runs 中 {code} 的 task_type='profile' 最新记录。
- 如果产品结构底稿不存在，先停止并把本任务标记为 blocked，不要跳过前置依赖。

硬约束：
- 只研究这一只 ETF，禁止同时研究其他 ETF。
- Tushare 是 ETF 净值、行情、份额、持仓披露和指数行情的结构化主源。
- 本任务可以多次重复执行，用最新净值、折溢价、底层指数估值、份额和流动性数据刷新结论。
- 本任务只构建 deterministic report 所需的 assembly_input，不直接生成最终 ETFResearchReport。
- LLM 只能负责搜集、清洗、归一化输入和解释脚本输出；不能重新计算参考价值区间，不能给出新的 grade。
- 不输出交易指令、不输出现金金额、不输出份额数量。

执行流程：
1. 收集 Tushare 和必要网络补充资料，形成 assembly_input JSON。
2. 将 assembly_input 写入 temp/assembly_inputs/{code}_valuation_{basis_date}.json。
3. 运行 python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite temp/assembly_inputs/{code}_valuation_{basis_date}.json > temp/reports/{code}_valuation_{basis_date}.json。
4. 用 python scripts/import_research_run.py temp/reports/{code}_valuation_{basis_date}.json 入库。
5. 导入成功后，汇报 run_id、report_hash、audit_log stage 覆盖、verify_run 结果和系统生成的主要结论摘要。

{VALUATION_ASSEMBLY_INPUT_INSTRUCTION}

完成后保证 /etfs/{code} 能看到由 deterministic pipeline 生成并入库的参考价值区间历史叠加。"""


def build_requested_profile_prompt(item: dict[str, Any], report: dict[str, Any]) -> str:
    code = item["code"]
    name = item["name"]
    return f"""在 C:\\Users\\kunpeng\\Documents\\MyInvestETF 中执行 ETF 产品结构深研。

唯一研究对象：{code} {name}。

入口信息：
- 入口来源：用户主动请求 /research?etf={code}
- report_id：{report["report_id"]}
- basis_date：{report.get("basis_date")}

硬约束：
- 这只 ETF 不要求出现在 /api/index。
- 只研究这一只 ETF，禁止同时研究其他 ETF。
- Tushare 是结构化主源，网络资料只作补充证据。
- 本任务只做产品结构、指数、持仓、流动性、跟踪和组合角色底稿，不给最终参考价值区间。
- 不输出交易指令、不输出现金金额、不输出份额数量。

{ETF_REPORT_SCHEMA_INSTRUCTION}
JSON 必须符合 ETFResearchReport：task_type 为 profile，valuation.reference_value_low/mid/high 均为 null。"""


def build_requested_valuation_prompt(item: dict[str, Any], report: dict[str, Any]) -> str:
    code = item["code"]
    name = item["name"]
    return f"""在 C:\\Users\\kunpeng\\Documents\\MyInvestETF 中执行 ETF 估值刷新输入构建。

唯一研究对象：{code} {name}。

入口信息：
- 入口来源：用户主动请求 /research?etf={code}
- report_id：{report["report_id"]}
- basis_date：{report.get("basis_date")}

前置依赖：
- 先读取本地 etf_research_runs 中 {code} 的 task_type='profile' 最新记录。
- 如果产品结构底稿不存在，先停止并把本任务标记为 blocked，不要跳过前置依赖。

硬约束：
- 这只 ETF 不要求出现在 /api/index。
- 只研究这一只 ETF，禁止同时研究其他 ETF。
- 本任务只构建 deterministic report 所需的 assembly_input，不直接生成最终 ETFResearchReport。
- 不输出交易指令、不输出现金金额、不输出份额数量。

{VALUATION_ASSEMBLY_INPUT_INSTRUCTION}"""


def build_report_explainer_prompt(report_output: dict[str, Any] | str) -> str:
    report_text = (
        report_output
        if isinstance(report_output, str)
        else json.dumps(report_output, ensure_ascii=False, indent=2, sort_keys=True)
    )
    return f"""{REPORT_EXPLAINER_INSTRUCTION}

ETFResearchReport:
{report_text}
"""


def enqueue_requested_etf(
    code: str,
    *,
    name: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    etf_code = code.strip().upper()
    if not ETF_CODE_RE.match(etf_code):
        raise ValueError(f"invalid ETF code: {code}")
    etf_name = (name or etf_code).strip() or etf_code
    basis_date = datetime.now().date().isoformat()
    report = {
        "report_id": f"manual_etf_research_request_{basis_date}",
        "schema_version": "manual_etf_research_request.v1",
        "generated_at": datetime.now().replace(microsecond=0).isoformat(),
        "basis_date": basis_date,
        "theme_report_id": None,
    }
    item = {"code": etf_code, "name": etf_name, "theme": "其他请求"}
    now = utc_now()
    db_target = Path(db_path) if db_path is not None else DB_PATH
    init_db(db_target)
    queued: list[str] = []
    with closing(connect(db_target)) as conn:
        upsert_report(
            conn,
            report_id=report["report_id"],
            schema_version=report["schema_version"],
            generated_at=report["generated_at"],
            basis_date=report["basis_date"],
            theme_report_id=None,
            source_url=f"/research?etf={etf_code}",
            fetched_at=now,
            raw_path=None,
        )
        if not has_profile_work(conn, etf_code):
            upsert_queue_item(
                conn,
                report_id=report["report_id"],
                code=etf_code,
                name=etf_name,
                priority=900,
                stage=1,
                task_type="profile",
                task_keyword=f"MyInvestETF ETF产品结构深研 {etf_code} {etf_name}",
                prompt=build_requested_profile_prompt(item, report),
                depends_on_task_type=None,
                task_date=basis_date,
                now=now,
                source_type=QUEUE_SOURCE_REQUEST,
                source_detail="/research",
            )
            queued.append("profile")
        upsert_queue_item(
            conn,
            report_id=report["report_id"],
            code=etf_code,
            name=etf_name,
            priority=900,
            stage=2,
            task_type="valuation",
            task_keyword=f"MyInvestETF ETF估值刷新 {etf_code} {etf_name}",
            prompt=build_requested_valuation_prompt(item, report),
            depends_on_task_type="profile",
            task_date=basis_date,
            now=now,
            source_type=QUEUE_SOURCE_REQUEST,
            source_detail="/research",
        )
        queued.append("valuation")
        conn.commit()
    return {
        "code": etf_code,
        "name": etf_name,
        "report_id": report["report_id"],
        "basis_date": basis_date,
        "queued": queued,
    }


enqueue_requested_stock = enqueue_requested_etf


def ingest_payload(
    payload: dict[str, Any],
    *,
    source_url: str = LEADER_INDEX_URL,
    raw_path: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    db_target = Path(db_path) if db_path is not None else DB_PATH
    init_db(db_target)
    report = report_meta(payload)
    items = primary_items(payload)
    now = utc_now()
    with closing(connect(db_target)) as conn:
        upsert_report(
            conn,
            report_id=report["report_id"],
            schema_version=report.get("schema_version"),
            generated_at=report.get("generated_at"),
            basis_date=report.get("basis_date"),
            theme_report_id=report.get("theme_report_id"),
            source_url=source_url,
            fetched_at=now,
            raw_path=raw_path,
        )
        for priority, item in enumerate(
            sorted(items, key=lambda row: row.get("score") or row.get("deep_score") or 0, reverse=True),
            start=1,
        ):
            upsert_trackable_leader(conn, report_id=report["report_id"], item=item, created_at=now)
            if not has_profile_work(conn, item["code"]):
                upsert_queue_item(
                    conn,
                    report_id=report["report_id"],
                    code=item["code"],
                    name=item["name"],
                    priority=priority,
                    stage=1,
                    task_type="profile",
                    task_keyword=f"MyInvestETF ETF产品结构深研 {item['code']} {item['name']}",
                    prompt=build_profile_prompt(item, report),
                    depends_on_task_type=None,
                    task_date=report.get("basis_date"),
                    now=now,
                )
            upsert_queue_item(
                conn,
                report_id=report["report_id"],
                code=item["code"],
                name=item["name"],
                priority=priority,
                stage=2,
                task_type="valuation",
                task_keyword=f"MyInvestETF ETF估值刷新 {item['code']} {item['name']}",
                prompt=build_valuation_prompt(item, report),
                depends_on_task_type="profile",
                task_date=report.get("basis_date"),
                now=now,
            )
        conn.commit()
    return {
        "report_id": report["report_id"],
        "basis_date": report.get("basis_date"),
        "count": len(items),
        "codes": [item["code"] for item in items],
        "names": [item["name"] for item in items],
    }
