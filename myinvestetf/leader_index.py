from __future__ import annotations

import json
import re
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from core.taxonomy import classify_etf, taxonomy_profile_to_dict
from core.valuation import normalize_valuation_model_type, sleeve_for_valuation_model

from .config import DB_PATH, LEADER_INDEX_URL, RAW_DATA_DIR
from .db import (
    QUEUE_SOURCE_BROAD_INDEX,
    QUEUE_SOURCE_DEFENSIVE,
    QUEUE_SOURCE_MAINLINE,
    QUEUE_SOURCE_REQUEST,
    QUEUE_SOURCE_SECONDARY,
    QUEUE_SOURCE_TRACKABLE,
    connect,
    init_db,
    prune_trackable_queue,
    prune_trackable_report,
    upsert_queue_item,
    upsert_report,
    upsert_trackable_leader,
    utc_now,
)

ETF_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
ETF_CATEGORY_FIELDS = (
    "category_key",
    "tracking_index",
    "underlying_index",
    "underlying_index_name",
    "target_index",
    "index_name",
    "benchmark",
    "category",
)
GENERIC_CATEGORY_VALUES = {"", "ETF", "主线ETF", "可跟踪ETF", "其他请求", "其他", "行业ETF", "主题ETF"}
ETF_ISSUER_PREFIXES = (
    "华泰柏瑞",
    "国联安",
    "易方达",
    "汇添富",
    "华夏",
    "鹏华",
    "国泰",
    "广发",
    "招商",
    "万家",
    "嘉实",
    "博时",
    "银华",
    "天弘",
    "华宝",
    "东财",
    "华安",
    "南方",
    "富国",
    "工银瑞信",
    "建信",
    "中银",
    "平安",
    "景顺长城",
    "大成",
    "海富通",
    "兴业",
    "申万菱信",
    "摩根",
    "诺安",
    "融通",
)
ETF_CATEGORY_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("现金替代", ("短融", "日利", "货币", "现金", "添利", "快线", "保证金", "逆回购")),
    ("上证综指", ("上证综指", "上证指数", "上证综合")),
    ("沪深300", ("沪深300",)),
    ("中证A500", ("中证A500", "中证A50", "A500")),
    ("上证50", ("上证50",)),
    ("中证500", ("中证500",)),
    ("中证1000", ("中证1000",)),
    ("创业板", ("创业板", "创业50")),
    ("红利低波", ("红利低波", "低波红利")),
    ("自由现金流", ("自由现金流", "现金流")),
    ("红利高股息", ("高股息", "股息", "红利")),
    ("半导体芯片", ("半导体", "芯片", "集成电路")),
    ("消费电子", ("消费电子",)),
    ("人工智能算力", ("人工智能", "AI", "算力", "云计算", "数据", "软件", "信创", "计算机")),
    ("机器人", ("机器人",)),
    ("新能源车电池", ("新能源车", "新能源汽车", "智能车", "电池", "锂电")),
    ("光伏储能", ("光伏", "储能", "新能源")),
    ("证券金融", ("证券公司", "证券", "券商", "银行", "保险", "金融科技")),
    ("军工国防", ("军工", "国防", "航天", "航空")),
    ("医药医疗", ("医药", "医疗", "创新药", "生物", "中药")),
    ("消费食品", ("食品饮料", "白酒", "酒", "消费")),
    ("有色贵金属", ("有色", "稀土", "黄金", "贵金属")),
    ("能源资源", ("煤炭", "石油", "能源")),
    ("科创成长", ("科创板成长", "科创成长")),
    ("科创50", ("科创板50", "科创50")),
)
ETF_TOP_TEXT_RE = re.compile(r"(\d{6}\.(?:SH|SZ|BJ))\s+([^、，,]+)")
SECONDARY_THEME_MIN_ETF_SCORE = 75.0
SECONDARY_THEME_MIN_MARKET_HEAT = 10.0
SECONDARY_THEME_MIN_COMBINED_SCORE = 10.0
SECONDARY_THEME_ALIASES: dict[str, tuple[str, ...]] = {
    "photovoltaic_wind_storage": ("光伏", "风电", "储能", "新能源"),
    "agriculture_breeding_pig_cycle": ("农业", "养殖", "畜牧", "猪周期", "猪"),
    "finance_brokerage_bank_insurance": ("大金融", "金融", "证券", "券商", "银行", "保险"),
    "innovative_medicine": ("创新药", "医药", "医疗", "生物"),
    "robotics": ("机器人",),
    "industrial_equipment": ("工业母机", "高端装备", "工程机械", "机床"),
    "infrastructure_materials": ("基建", "建材", "水泥", "稳增长"),
    "resources_gold": ("黄金", "贵金属"),
    "resources_coal_oil_gas": ("煤炭", "油气", "石油", "能源"),
    "resources_copper_aluminum": ("铜", "铝", "工业金属", "有色"),
    "real_estate_chain": ("地产", "家居", "建材"),
    "media_game_ai_application": ("传媒", "游戏", "AI应用", "人工智能"),
}
SECONDARY_EXCLUDED_TEXT_KEYWORDS = (
    "期货",
    "dax",
    "标普500",
    "标普",
    "s&p",
    "msci美国",
    "道琼斯",
    "cac40",
    "德国",
    "法国",
    "国际龙头",
)
BROAD_INDEX_SEED_ETFS: tuple[dict[str, Any], ...] = (
    {"code": "510210.SH", "name": "富国上证综指ETF", "theme": "上证综指宽基", "category_key": "上证综指"},
    {"code": "510050.SH", "name": "华夏上证50ETF", "theme": "上证50宽基", "category_key": "上证50"},
    {"code": "510300.SH", "name": "华泰柏瑞沪深300ETF", "theme": "沪深300宽基", "category_key": "沪深300"},
    {"code": "510500.SH", "name": "南方中证500ETF", "theme": "中证500宽基", "category_key": "中证500"},
    {"code": "512100.SH", "name": "南方中证1000ETF", "theme": "中证1000宽基", "category_key": "中证1000"},
    {"code": "159915.SZ", "name": "易方达创业板ETF", "theme": "创业板宽基", "category_key": "创业板"},
    {"code": "588000.SH", "name": "华夏上证科创板50ETF", "theme": "科创50宽基", "category_key": "科创50"},
)
BROAD_INDEX_CATEGORY_KEYS = {item["category_key"] for item in BROAD_INDEX_SEED_ETFS}
BROAD_INDEX_CATEGORY_ORDER = {item["category_key"]: index for index, item in enumerate(BROAD_INDEX_SEED_ETFS)}
DEFENSIVE_SEED_ETFS: tuple[dict[str, Any], ...] = (
    {
        "code": "159201.SZ",
        "name": "华夏国证自由现金流ETF",
        "theme": "自由现金流收益防御",
        "category_key": "自由现金流",
    },
    {
        "code": "512890.SH",
        "name": "华泰柏瑞中证红利低波动ETF",
        "theme": "红利低波收益防御",
        "category_key": "红利低波",
    },
)
DEFENSIVE_SEED_CODES = {item["code"] for item in DEFENSIVE_SEED_ETFS}
DEFENSIVE_CATEGORY_KEYS = {item["category_key"] for item in DEFENSIVE_SEED_ETFS}
DEFENSIVE_CATEGORY_ORDER = {item["category_key"]: index for index, item in enumerate(DEFENSIVE_SEED_ETFS)}


ETF_REPORT_SCHEMA_INSTRUCTION = """ETFResearchReport 结构化输出要求：
- 最终只输出一个 JSON object，不要输出 Markdown 包裹。
- JSON 必须符合 core/schema/etf_report.py 中 ETFResearchReport。
- 顶层字段固定为：schema_version, report_version, report_hash, run_id, etf_code, etf_name, source_report_id, task_type, research_date, status, valuation_model_type, sleeve_key, title, summary, product_profile, holdings_profile, valuation, base_position_view, risk, conclusion, taxonomy_profile, market_context, evidence, assumptions, data_gaps。
- 禁止输出 schema 以外的额外字段；禁止把未定义内容塞进自由 dict。
- etf_code 使用唯一研究对象代码，etf_name 使用唯一研究对象名称，source_report_id 使用入口 report_id。
- research_date 必须使用入口 basis_date。
- run_id 必须等于 hash(etf_code + task_type + research_date + schema_version)，可省略让导入端自动生成；如果提供错误 run_id 会被拒绝。
- valuation_model_type 只能是 broad_index、mainline_theme、factor_defensive、cash_like。
- sleeve_key 只能是 core_wide_etf、mainline_etf、defensive_quality、cash_like。
- product_profile 必须包含 fund_type, tracking_index, asset_class, valuation_model_type, sleeve_key, portfolio_role, fee_note, liquidity_note, tracking_note。
- holdings_profile 必须包含 holdings_disclosure_date, top_holdings, concentration_note, overlap_note, disclosure_lag_note。
- valuation 必须包含 current_price, nav, premium_discount, underlying_pe, underlying_pb, valuation_percentile, reference_value_low, reference_value_mid, reference_value_high, unit, method, confidence, key_assumptions；可包含 engine_version, undervalued_score, liquidity_score, tracking_score, portfolio_role_score, risk_adjusted_score, mainline_validity_score, valuation_tolerance_score, crowding_risk_score, factor_premium_score, cash_like_safety_score。
- risk 必须包含 liquidity_risk, tracking_risk, concentration_risk, sentiment_risk, invalidation_conditions。
- conclusion 必须包含 grade, confidence, summary；grade 必须等于 base_position_view。
- taxonomy_profile 可为 null 或系统生成对象；如存在，必须包含 etf_type, subtype, lifecycle_stage, classification_confidence, classification_reasons, legacy_valuation_model_type, legacy_sleeve_key。
- market_context 可为 null；如存在，必须由系统根据 price_series / index_price_series 或本地行情缓存生成，不得手写。
- evidence 是对象数组，每项必须包含 source, date, url, purpose, detail。
- assumptions 和 data_gaps 都是字符串数组。
- base_position_view/grade 只能是：不适合底仓、观察、工具仓可用、底仓候选、估值或拥挤暂缓。
- task_type 固定为 research；status 只能是 complete、draft、blocked；confidence 只能是 low、medium、high。"""


RESEARCH_ASSEMBLY_INPUT_INSTRUCTION = """ETF research assembly_input 结构化要求：
- 你的角色是 ETF 完整深研输入构建器，不是最终报告计算器。
- 不要手写最终 ETFResearchReport；最终报告必须由 scripts/build_research_report.py 或 core/report.build_etf_report(...) 生成。
- 不要临场计算最终参考价值区间、signal、grade 或 report_hash；这些由 deterministic engine 生成。
- assembly_input 必须是一个 JSON object，至少包含 etf_code, etf_name, source_report_id, task_type, research_date, valuation_model_type, sleeve_key, product_profile, holdings_inputs, valuation_inputs, model_specific_inputs, liquidity_inputs, tracking_inputs, risk_signals, evidence, assumptions, data_gaps；可包含 taxonomy_profile、price_series 和 index_price_series。
- task_type 固定为 research；research_date 使用入口 basis_date。
- product_profile 放 ETF 产品结构、基金类型、跟踪指数、资产类别、费率、规模、流动性、组合角色。
- holdings_inputs 放 holdings_disclosure_date, top_holdings, concentration_ratio, concentration_note, overlap_note, disclosure_lag_note；必须说明 fund_portfolio 是披露滞后口径，不是实时完整持仓。
- valuation_inputs 放 ETF 估值输入：current_price, nav/unit_nav, premium_discount, underlying_pe, underlying_pb, valuation_percentile, unit。
- model_specific_inputs 必须按 valuation_model_type 分类型填写，不能把不同 ETF 类型混用同一套依据。
- 所有 ETF 评分必须绑定 taxonomy；taxonomy_profile 由系统根据 ETF 元数据分类生成，LLM 不得把未验证分类理由写成事实。
- liquidity_inputs 放 ETF 流动性输入：turnover_amount, fund_size, share_change_ratio；fund_share 是份额变化的可用代理。
- tracking_inputs 放 tracking_error、discount_premium_history_note、index_replication_note 等跟踪质量输入。
- price_series 放 ETF 日行情序列，index_price_series 放底层指数日行情序列；字段至少包括 trade_date 和 close/close_price，可包含 amount/volume。系统据此生成 market_context，LLM 不手写最终 market_context。
- risk_signals 放 liquidity_risk, tracking_risk, concentration_risk, sentiment_risk, invalidation_conditions。
- 所有来源必须进入 evidence：source, date, url, purpose, detail。
- 如无法取得官方净申购赎回、长期估值分位、实时完整持仓、组合重叠等字段，必须写入 data_gaps，不得假装已验证。
- 禁止输出交易指令、现金金额、份额数量或买卖建议。"""


MODEL_RESEARCH_INSTRUCTIONS = {
    "broad_index": """宽基 ETF 完整深研依据：
- 识别是否适合作为核心宽基底仓，不按主线追强逻辑研究。
- 必须写清底层指数覆盖范围、指数编制、行业分散度、长期权益 beta、费率、流动性和跟踪质量。
- model_specific_inputs 必须包含 equity_risk_premium、roe、market_position_score。
- valuation_inputs 应优先填底层指数 PE/PB、估值分位、NAV、折溢价。
- 研究结论关注核心仓适配度、估值安全垫和长期底仓资格。""",
    "mainline_theme": """主线 ETF 完整深研依据：
- 识别产业 beta 和主题暴露，不把行业主题 ETF 当成长期宽基底仓。
- 必须写清主线主题、产业链位置、行业集中度、主线生命周期、可能退潮条件。
- model_specific_inputs 必须包含 theme_strength、fund_flow_score、crowding_score、valuation_tolerance。
- 不得把主线 ETF 简化为“便宜/贵”；核心是主线是否有效、估值容错多大、拥挤是否过高。
- 研究结论关注参与价值、退潮风险和是否应暂缓，不给买卖指令。""",
    "factor_defensive": """红利低波 / 自由现金流 ETF 完整深研依据：
- 识别收益型防御属性，不按主线 ETF 的趋势追强逻辑研究。
- 红利低波重点看股息、低波、行业集中度和分红稳定性；自由现金流重点看现金流质量、ROE/ROIC 和质量因子。
- model_specific_inputs 必须包含 dividend_spread、fcf_yield、quality_score、style_opportunity_cost。
- 研究结论关注收益型防御仓适配度、风格过热风险和切回进攻仓的机会成本。""",
    "cash_like": """现金替代 ETF 完整深研依据：
- 短融、日利、货币、现金类 ETF 只做现金替代资格检查，不判断传统权益估值贵贱。
- model_specific_inputs 必须包含 duration_risk、credit_risk、yield_stability。
- 只检查流动性、折溢价异常、久期风险、信用风险、收益稳定性和申赎便利。
- 研究结论只能是可作为现金替代、暂不适合作为现金替代，或数据不足。""",
}


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


def _xueqiu_url(code: str) -> str:
    symbol, exchange = code.split(".", 1)
    return f"https://xueqiu.com/S/{exchange.upper()}{symbol}"


def _score_rating(score: object) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ""
    if value >= 90.0:
        return "A"
    if value >= 80.0:
        return "B"
    if value >= 70.0:
        return "C"
    return "Watch"


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def etf_model_type(item: dict[str, Any]) -> str:
    return normalize_valuation_model_type(item.get("valuation_model_type"), item)


def etf_sleeve_key(item: dict[str, Any]) -> str:
    return sleeve_for_valuation_model(etf_model_type(item))


def etf_taxonomy_profile(item: dict[str, Any]) -> dict[str, Any]:
    model_type = etf_model_type(item)
    source = {
        **item,
        "valuation_model_type": model_type,
        "sleeve_key": sleeve_for_valuation_model(model_type),
    }
    return taxonomy_profile_to_dict(classify_etf(source))


def _attach_taxonomy_profile(item: dict[str, Any]) -> None:
    item["taxonomy_profile"] = etf_taxonomy_profile(item)


def _model_context(item: dict[str, Any]) -> dict[str, str]:
    model_type = etf_model_type(item)
    sleeve_key = sleeve_for_valuation_model(model_type)
    taxonomy = etf_taxonomy_profile({**item, "valuation_model_type": model_type, "sleeve_key": sleeve_key})
    return {
        "valuation_model_type": model_type,
        "sleeve_key": sleeve_key,
        "etf_type": str(taxonomy.get("etf_type")),
        "subtype": str(taxonomy.get("subtype")),
        "lifecycle_stage": str(taxonomy.get("lifecycle_stage") or ""),
        "classification_confidence": str(taxonomy.get("classification_confidence")),
        "research_instruction": MODEL_RESEARCH_INSTRUCTIONS[model_type],
    }


def is_cash_like_etf(item: dict[str, Any]) -> bool:
    return etf_model_type(item) == "cash_like"


def _compact_text(value: object) -> str:
    text = str(value or "").strip()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def _strip_issuer_prefix(name: str) -> str:
    text = _compact_text(name)
    for prefix in ETF_ISSUER_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _keyword_category_key(item: dict[str, Any]) -> str:
    explicit_category = _compact_text(item.get("category_key"))
    if explicit_category and explicit_category not in GENERIC_CATEGORY_VALUES:
        return explicit_category
    fields = [
        item.get("tracking_index"),
        item.get("underlying_index"),
        item.get("underlying_index_name"),
        item.get("target_index"),
        item.get("index_name"),
        item.get("benchmark"),
        item.get("category"),
        item.get("theme"),
        item.get("name"),
        item.get("fund_name"),
    ]
    text = _compact_text(" ".join(str(value or "") for value in fields)).upper()
    for category, keywords in ETF_CATEGORY_KEYWORD_RULES:
        if any(_compact_text(keyword).upper() in text for keyword in keywords):
            return category
    return ""


def etf_category_key(item: dict[str, Any]) -> str:
    keyword_key = _keyword_category_key(item)
    if keyword_key:
        return keyword_key
    for field in ETF_CATEGORY_FIELDS:
        value = _compact_text(item.get(field))
        if value and value not in GENERIC_CATEGORY_VALUES:
            return value
    name_key = _strip_issuer_prefix(str(item.get("name") or item.get("fund_name") or ""))
    return name_key or str(item.get("code") or item.get("ts_code") or "")


def _liquidity_amount(item: dict[str, Any]) -> float:
    for field in ("amount", "turnover_amount", "成交额", "volume", "vol"):
        value = _safe_float(item.get(field))
        if value:
            return value
    market = item.get("market")
    if isinstance(market, dict):
        for field in ("amount", "turnover_amount", "volume", "vol"):
            value = _safe_float(market.get(field))
            if value:
                return value
    return 0.0


def _deduplicate_by_category(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, tuple[float, float, int, dict[str, Any]]] = {}
    for index, item in enumerate(items):
        category_key = etf_category_key(item)
        candidate = dict(item)
        candidate["category_key"] = category_key
        amount = _liquidity_amount(candidate)
        score = _safe_float(candidate.get("deep_score") or candidate.get("score"))
        current = selected.get(category_key)
        if current is None or (amount, score, -index) > (current[0], current[1], current[2]):
            selected[category_key] = (amount, score, -index, candidate)
    return [entry[3] for entry in sorted(selected.values(), key=lambda entry: (-entry[1], -entry[0], entry[3]["code"]))]


def _flatten_text_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        texts: list[str] = []
        for nested in value.values():
            texts.extend(_flatten_text_values(nested))
        return texts
    if isinstance(value, (list, tuple, set)):
        texts = []
        for nested in value:
            texts.extend(_flatten_text_values(nested))
        return texts
    return []


def _taxonomy_v2_theme_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return []
    for key in ("taxonomy_v2_ranking", "themes"):
        rows = result.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    backfill = result.get("taxonomy_v2_backfill")
    if isinstance(backfill, dict):
        rows = backfill.get("themes") or backfill.get("ranking")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _theme_is_secondary_researchable(theme: dict[str, Any]) -> bool:
    stage = str(theme.get("stage") or "")
    if stage in {"可观察", "弱观察"}:
        return True
    return (
        _safe_float(theme.get("market_heat_score")) >= SECONDARY_THEME_MIN_MARKET_HEAT
        or _safe_float(theme.get("combined_score")) >= SECONDARY_THEME_MIN_COMBINED_SCORE
    )


def _secondary_theme_terms(theme: dict[str, Any]) -> set[str]:
    texts: list[str] = [
        str(theme.get("theme_name") or ""),
        str(theme.get("parent_name") or ""),
        str(theme.get("theme_id") or ""),
        str(theme.get("parent_id") or ""),
    ]
    texts.extend(SECONDARY_THEME_ALIASES.get(str(theme.get("theme_id") or ""), ()))
    texts.extend(_flatten_text_values((theme.get("matched_keywords") or {})))
    for evidence in theme.get("evidence_sources") or []:
        if isinstance(evidence, dict):
            texts.extend(_flatten_text_values(evidence.get("matched_keywords")))
    terms: set[str] = set()
    for text in texts:
        for part in re.split(r"[/、,，|；;()\s]+", str(text)):
            compact = _compact_text(part).lower()
            if len(compact) >= 2 and compact not in {"etf", "qdii", "主题", "行业", "指数"}:
                terms.add(compact)
    return terms


def _secondary_theme_evidence_matches(item: dict[str, Any], theme: dict[str, Any]) -> bool:
    item_name = _compact_text(item.get("name") or item.get("fund_name")).lower()
    if not item_name:
        return False
    for evidence in theme.get("evidence_sources") or []:
        if not isinstance(evidence, dict) or evidence.get("source") != "etf_top":
            continue
        label = _compact_text(evidence.get("label")).lower()
        if label and (label == item_name or label in item_name or item_name in label):
            return True
    return False


def _secondary_theme_match_score(item: dict[str, Any], theme: dict[str, Any]) -> tuple[float, list[str]] | None:
    if not _theme_is_secondary_researchable(theme):
        return None
    category_key = etf_category_key(item)
    item_text = _compact_text(
        " ".join(
            str(value or "")
            for value in (
                item.get("name"),
                item.get("fund_name"),
                category_key,
                item.get("theme"),
                item.get("tracking_index"),
                item.get("asset_class"),
            )
        )
    ).lower()
    if not item_text:
        return None
    terms = _secondary_theme_terms(theme)
    hits = sorted(term for term in terms if term and term in item_text)
    evidence_match = _secondary_theme_evidence_matches(item, theme)
    if not hits and not evidence_match:
        return None
    score = (
        (100.0 if evidence_match else 0.0)
        + len(hits) * 12.0
        + _safe_float(theme.get("market_heat_score")) * 0.35
        + _safe_float(theme.get("combined_score")) * 0.20
        + _safe_float(item.get("score") or item.get("deep_score")) * 0.10
    )
    return score, hits


def _is_excluded_secondary_etf(item: dict[str, Any]) -> bool:
    text = _compact_text(
        " ".join(
            str(value or "")
            for value in (
                item.get("name"),
                item.get("fund_name"),
                item.get("theme"),
                etf_category_key(item),
            )
        )
    ).lower()
    return any(keyword.lower() in text for keyword in SECONDARY_EXCLUDED_TEXT_KEYWORDS)


def _best_secondary_theme_for_item(item: dict[str, Any], themes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]] | None:
    best: tuple[float, dict[str, Any], list[str]] | None = None
    for theme in themes:
        match = _secondary_theme_match_score(item, theme)
        if match is None:
            continue
        score, hits = match
        if best is None or score > best[0]:
            best = (score, theme, hits)
    if best is None:
        return None
    return best[1], best[2]


def _mark_secondary_theme_item(item: dict[str, Any], theme: dict[str, Any], matched_terms: list[str]) -> dict[str, Any]:
    candidate = dict(item)
    theme_name = str(theme.get("theme_name") or theme.get("theme") or "二级主题")
    parent_name = str(theme.get("parent_name") or "")
    scores = candidate.get("scores")
    scores_map = dict(scores) if isinstance(scores, dict) else {}
    scores_map.update(
        {
            "secondary_theme_score": theme.get("combined_score"),
            "secondary_market_heat": theme.get("market_heat_score"),
            "secondary_policy_score": theme.get("policy_score_100") or theme.get("policy_score"),
            "secondary_confidence_score": theme.get("confidence_score"),
        }
    )
    themes = []
    for value in candidate.get("themes") or []:
        if value and value not in themes and value not in GENERIC_CATEGORY_VALUES:
            themes.append(value)
    for value in (theme_name, parent_name):
        if value and value not in themes:
            themes.append(value)
    candidate.update(
        {
            "theme": theme_name,
            "themes": themes or [theme_name],
            "deep_label": "二级主题ETF候选",
            "candidate_leader_tier": "二级主题ETF",
            "candidate_leader_claim": (
                f"来自 theme.okbbc.com taxonomy_v2 二级主题：{parent_name + ' / ' if parent_name else ''}{theme_name}；"
                "用于行业底部反转和轮动观察"
            ),
            "candidate_evidence_score": theme.get("combined_score") or candidate.get("score") or candidate.get("deep_score"),
            "candidate_evidence_count": len(theme.get("evidence_sources") or []),
            "candidate_hard_evidence_count": len([term for term in matched_terms if term]),
            "scores": scores_map,
            "source_path": "result.etf_top + result.taxonomy_v2_ranking",
            "secondary_theme_id": theme.get("theme_id"),
            "secondary_theme_name": theme_name,
            "secondary_parent_id": theme.get("parent_id"),
            "secondary_parent_name": parent_name,
            "secondary_stage": theme.get("stage"),
            "secondary_confidence_label": theme.get("confidence_label"),
            "secondary_matched_terms": matched_terms,
            "data_gaps": candidate.get("data_gaps")
            or ["二级主题 ETF 需要通过 Tushare 补齐净值、折溢价、份额、持仓和底层指数估值。"],
        }
    )
    model_type = etf_model_type(candidate)
    candidate["valuation_model_type"] = model_type
    candidate["sleeve_key"] = sleeve_for_valuation_model(model_type)
    candidate["category_key"] = etf_category_key(candidate)
    _attach_taxonomy_profile(candidate)
    return candidate


def _secondary_theme_representatives(items: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    themes = _taxonomy_v2_theme_rows(payload)
    if not themes:
        return []
    selected: dict[str, tuple[float, float, str, dict[str, Any]]] = {}
    for item in items:
        if item.get("source_path") != "result.etf_top":
            continue
        if item.get("top_etf_rank") is not None or item.get("theme_rank") is not None:
            continue
        if is_broad_index_item(item) or is_defensive_seed_item(item) or is_cash_like_etf(item):
            continue
        if _is_excluded_secondary_etf(item):
            continue
        if _safe_float(item.get("score") or item.get("deep_score")) < SECONDARY_THEME_MIN_ETF_SCORE:
            continue
        match = _best_secondary_theme_for_item(item, themes)
        if match is None:
            continue
        theme, matched_terms = match
        candidate = _mark_secondary_theme_item(item, theme, matched_terms)
        theme_key = str(candidate.get("secondary_theme_id") or candidate["category_key"])
        amount = _liquidity_amount(candidate)
        score = _safe_float(candidate.get("score") or candidate.get("deep_score"))
        current = selected.get(theme_key)
        if current is None or (amount, score, candidate["code"]) > (current[0], current[1], current[2]):
            selected[theme_key] = (amount, score, candidate["code"], candidate)
    representatives = [entry[3] for entry in selected.values()]
    representatives = _deduplicate_by_category(representatives)
    return sorted(
        representatives,
        key=lambda item: (
            -_safe_float(item.get("scores", {}).get("secondary_theme_score") if isinstance(item.get("scores"), dict) else 0.0),
            -_liquidity_amount(item),
            item["code"],
        ),
    )


def _normalize_theme_latest_item(item: dict[str, Any]) -> dict[str, Any]:
    code = str(item.get("code") or item.get("ts_code") or "")
    name = str(item.get("name") or item.get("fund_name") or "")
    score = item.get("score")
    normalized = dict(item)
    normalized.update(
        {
            "code": code,
            "name": name,
            "xueqiu_url": item.get("xueqiu_url") or (_xueqiu_url(code) if ETF_CODE_RE.match(code) else None),
            "theme": item.get("theme") or "主线ETF",
            "themes": item.get("themes") or ["主线ETF"],
            "deep_rating": item.get("deep_rating") or _score_rating(score),
            "deep_label": item.get("deep_label") or "可跟踪主线ETF",
            "deep_score": item.get("deep_score") or score,
            "shadow_observation_eligible": item.get("shadow_observation_eligible", True),
            "candidate_leader_tier": item.get("candidate_leader_tier") or "ETF工具",
            "candidate_leader_claim": item.get("candidate_leader_claim") or "来自 theme.okbbc.com 主线 ETF 强度池",
            "candidate_evidence_score": item.get("candidate_evidence_score") or score,
            "candidate_evidence_count": item.get("candidate_evidence_count") or 4,
            "candidate_hard_evidence_count": item.get("candidate_hard_evidence_count") or 4,
            "market": item.get("market")
            or {
                "r1": item.get("r1"),
                "r5": item.get("r5"),
                "r20": item.get("r20"),
                "amount": item.get("amount"),
            },
            "scores": item.get("scores")
            or {
                "mainline_strength": score,
                "theme_binding": score,
                "evidence_quality": score,
                "trading_structure": item.get("amount_rank"),
                "r1_rank": item.get("r1_rank"),
                "r5_rank": item.get("r5_rank"),
                "r20_rank": item.get("r20_rank"),
                "amount_rank": item.get("amount_rank"),
            },
            "risk_flags": item.get("risk_flags") or [],
            "data_gaps": item.get("data_gaps") or ["theme.okbbc.com/api/latest 不提供 ETF 净值、折溢价、份额变化和完整持仓披露。"],
            "source_path": item.get("source_path") or "result.etf_top",
        }
    )
    return normalized


def _theme_score(item: dict[str, Any]) -> object:
    return (
        item.get("mainline_score_v6")
        or item.get("theme_score_v5")
        or item.get("theme_score_v4_stance_adjusted")
        or item.get("theme_score_v4")
        or item.get("theme_score_v3")
        or item.get("etf_score")
    )


def _normalize_theme_ranking_etf_item(theme_item: dict[str, Any], code: str, name: str, rank: int) -> dict[str, Any]:
    theme = str(theme_item.get("theme") or "主线ETF")
    score = _theme_score(theme_item)
    normalized = {
        "code": code,
        "name": name,
        "xueqiu_url": _xueqiu_url(code) if ETF_CODE_RE.match(code) else None,
        "theme": theme,
        "themes": [theme],
        "deep_rating": _score_rating(score),
        "deep_label": "主线ETF候选",
        "deep_score": score,
        "shadow_observation_eligible": True,
        "candidate_leader_tier": "主线ETF",
        "candidate_leader_claim": f"来自 theme.okbbc.com theme_ranking：{theme}",
        "candidate_evidence_score": score,
        "candidate_evidence_count": theme_item.get("evidence_count") or theme_item.get("event_count_90d") or 0,
        "candidate_hard_evidence_count": theme_item.get("primary_event_count") or 0,
        "market": {},
        "scores": {
            "mainline_strength": score,
            "theme_binding": theme_item.get("theme_score_v5") or theme_item.get("theme_score_v4"),
            "evidence_quality": theme_item.get("evidence_score"),
            "etf_score": theme_item.get("etf_score"),
        },
        "risk_flags": [],
        "data_gaps": ["theme_ranking.top_etf 只提供代码和名称；成交额、净值、份额和持仓需由 Tushare 补齐。"],
        "source_path": "result.theme_ranking.top_etf",
        "theme_rank": theme_item.get("rank"),
        "top_etf_rank": rank,
    }
    model_type = etf_model_type(normalized)
    normalized["valuation_model_type"] = model_type
    normalized["sleeve_key"] = sleeve_for_valuation_model(model_type)
    normalized["category_key"] = etf_category_key(normalized)
    _attach_taxonomy_profile(normalized)
    return normalized


def _normalize_broad_index_seed_item(seed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(seed)
    code = str(normalized["code"])
    score = normalized.get("deep_score") or 80.0
    normalized.update(
        {
            "code": code,
            "xueqiu_url": normalized.get("xueqiu_url") or _xueqiu_url(code),
            "themes": [normalized.get("theme") or normalized.get("category_key") or "核心宽基"],
            "deep_rating": normalized.get("deep_rating") or _score_rating(score),
            "deep_label": normalized.get("deep_label") or "核心宽基ETF",
            "deep_score": score,
            "shadow_observation_eligible": True,
            "candidate_leader_tier": "核心宽基",
            "candidate_leader_claim": "本地核心宽基 ETF 种子，用于补齐主线接口未覆盖的宽基研究对象",
            "candidate_evidence_score": score,
            "candidate_evidence_count": 1,
            "candidate_hard_evidence_count": 1,
            "market": normalized.get("market") or {},
            "scores": normalized.get("scores") or {"mainline_strength": score, "theme_binding": score, "evidence_quality": score},
            "risk_flags": normalized.get("risk_flags") or [],
            "data_gaps": normalized.get("data_gaps")
            or ["宽基种子需要通过 Tushare 补齐净值、成交额、份额、估值分位和持仓披露。"],
            "source_path": "local.broad_index_seed",
        }
    )
    normalized["valuation_model_type"] = "broad_index"
    normalized["sleeve_key"] = sleeve_for_valuation_model("broad_index")
    _attach_taxonomy_profile(normalized)
    return normalized


def _normalize_defensive_seed_item(seed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(seed)
    code = str(normalized["code"])
    score = normalized.get("deep_score") or 78.0
    normalized.update(
        {
            "code": code,
            "xueqiu_url": normalized.get("xueqiu_url") or _xueqiu_url(code),
            "themes": [normalized.get("theme") or normalized.get("category_key") or "收益防御"],
            "deep_rating": normalized.get("deep_rating") or _score_rating(score),
            "deep_label": normalized.get("deep_label") or "收益防御ETF",
            "deep_score": score,
            "shadow_observation_eligible": True,
            "candidate_leader_tier": "收益防御",
            "candidate_leader_claim": "本地收益防御 ETF 种子，用于纳入自由现金流和红利低波研究对象",
            "candidate_evidence_score": score,
            "candidate_evidence_count": 1,
            "candidate_hard_evidence_count": 1,
            "market": normalized.get("market") or {},
            "scores": normalized.get("scores") or {"mainline_strength": score, "theme_binding": score, "evidence_quality": score},
            "risk_flags": normalized.get("risk_flags") or [],
            "data_gaps": normalized.get("data_gaps")
            or ["收益防御种子需要通过 Tushare 补齐净值、成交额、份额、估值分位、股息或自由现金流因子输入。"],
            "source_path": "local.defensive_seed",
        }
    )
    normalized["valuation_model_type"] = "factor_defensive"
    normalized["sleeve_key"] = sleeve_for_valuation_model("factor_defensive")
    _attach_taxonomy_profile(normalized)
    return normalized


def _theme_ranking_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return []
    items: list[dict[str, Any]] = []
    for theme_item in result.get("theme_ranking") or []:
        if not isinstance(theme_item, dict):
            continue
        for rank, (code, name) in enumerate(ETF_TOP_TEXT_RE.findall(str(theme_item.get("top_etf") or "")), start=1):
            items.append(_normalize_theme_ranking_etf_item(theme_item, code, name.strip(), rank))
    return items


def _has_theme_ranking(payload: dict[str, Any]) -> bool:
    result = payload.get("result") or {}
    return isinstance(result, dict) and isinstance(result.get("theme_ranking"), list) and bool(result.get("theme_ranking"))


def _merge_item(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    theme = incoming.get("theme")
    if theme and existing.get("theme") in GENERIC_CATEGORY_VALUES:
        existing["theme"] = theme
    themes = []
    for value in existing.get("themes") or []:
        if value not in themes:
            themes.append(value)
    for value in incoming.get("themes") or ([theme] if theme else []):
        if value and value not in themes:
            themes.append(value)
    if themes:
        existing["themes"] = themes
    for key in ("theme_rank", "top_etf_rank", "source_path"):
        if key not in existing and incoming.get(key) is not None:
            existing[key] = incoming[key]


def _raw_primary_items(payload: dict[str, Any]) -> tuple[list[Any], str]:
    key_results = payload.get("key_results") or {}
    primary = key_results.get("primary_output") or {}
    if "items" in primary:
        items = primary.get("items") or []
        if not isinstance(items, list):
            raise ValueError("key_results.primary_output.items is not a list")
        return items, "key_results.primary_output.items"

    result = payload.get("result") or {}
    if isinstance(result, dict) and "etf_top" in result:
        items = result.get("etf_top") or []
        if not isinstance(items, list):
            raise ValueError("result.etf_top is not a list")
        return items, "result.etf_top"

    return [], "unknown"


def primary_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items, source_path = _raw_primary_items(payload)
    clean_items_by_code: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_theme_latest_item(item) if source_path == "result.etf_top" else dict(item)
        code = str(normalized.get("code") or normalized.get("ts_code") or "")
        name = str(normalized.get("name") or normalized.get("fund_name") or "")
        if ETF_CODE_RE.match(code) and name:
            normalized["code"] = code
            normalized["name"] = name
            model_type = etf_model_type(normalized)
            normalized["valuation_model_type"] = model_type
            normalized["sleeve_key"] = sleeve_for_valuation_model(model_type)
            normalized["category_key"] = etf_category_key(normalized)
            _attach_taxonomy_profile(normalized)
            clean_items_by_code[code] = normalized
    for normalized in _theme_ranking_items(payload):
        existing = clean_items_by_code.get(normalized["code"])
        if existing is None:
            clean_items_by_code[normalized["code"]] = normalized
        else:
            _merge_item(existing, normalized)
    if _has_theme_ranking(payload):
        for seed in BROAD_INDEX_SEED_ETFS:
            normalized = _normalize_broad_index_seed_item(seed)
            existing = clean_items_by_code.get(normalized["code"])
            if existing is None:
                clean_items_by_code[normalized["code"]] = normalized
            else:
                _merge_item(existing, normalized)
        for seed in DEFENSIVE_SEED_ETFS:
            normalized = _normalize_defensive_seed_item(seed)
            existing = clean_items_by_code.get(normalized["code"])
            if existing is None:
                clean_items_by_code[normalized["code"]] = normalized
            else:
                _merge_item(existing, normalized)
    for item in clean_items_by_code.values():
        model_type = etf_model_type(item)
        item["valuation_model_type"] = model_type
        item["sleeve_key"] = sleeve_for_valuation_model(model_type)
        item["category_key"] = etf_category_key(item)
        _attach_taxonomy_profile(item)
    return list(clean_items_by_code.values())


def _top_etf_codes(theme_item: dict[str, Any]) -> list[str]:
    return [code for code, _name in ETF_TOP_TEXT_RE.findall(str(theme_item.get("top_etf") or ""))]


def _choose_liquid_representative(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(
        enumerate(candidates),
        key=lambda pair: (
            1 if _liquidity_amount(pair[1]) > 0 else 0,
            _liquidity_amount(pair[1]),
            -pair[0],
        ),
    )[1]


def _append_unique(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    if not any(existing["code"] == item["code"] for existing in items):
        items.append(item)


def is_broad_index_item(item: dict[str, Any]) -> bool:
    return etf_model_type(item) == "broad_index" or etf_category_key(item) in BROAD_INDEX_CATEGORY_KEYS


def is_defensive_seed_item(item: dict[str, Any]) -> bool:
    return str(item.get("code") or "") in DEFENSIVE_SEED_CODES


def is_secondary_theme_item(item: dict[str, Any]) -> bool:
    return bool(item.get("secondary_theme_id")) or str(item.get("source_path") or "") == "result.etf_top + result.taxonomy_v2_ranking"


def queue_source_for_item(item: dict[str, Any]) -> str:
    if is_broad_index_item(item):
        return QUEUE_SOURCE_BROAD_INDEX
    if is_defensive_seed_item(item):
        return QUEUE_SOURCE_DEFENSIVE
    if is_secondary_theme_item(item):
        return QUEUE_SOURCE_SECONDARY
    if (
        item.get("theme_rank") is not None
        or item.get("top_etf_rank") is not None
        or item.get("source_path") == "result.theme_ranking.top_etf"
    ):
        return QUEUE_SOURCE_MAINLINE
    return QUEUE_SOURCE_TRACKABLE


def queue_source_detail_for_item(item: dict[str, Any]) -> str:
    category_key = etf_category_key(item)
    source_path = str(item.get("source_path") or "")
    if is_broad_index_item(item):
        return f"核心宽基代表：{category_key or item.get('theme') or item['code']}；来源：{source_path or 'local.broad_index_seed/result.etf_top'}"
    if is_defensive_seed_item(item):
        return f"收益防御代表：{category_key or item.get('theme') or item['code']}；来源：{source_path or 'local.defensive_seed'}"
    if is_secondary_theme_item(item):
        theme = item.get("secondary_theme_name") or item.get("theme") or category_key
        parent = item.get("secondary_parent_name")
        stage = item.get("secondary_stage")
        prefix = f"{parent} / " if parent else ""
        stage_text = f"，阶段：{stage}" if stage else ""
        return f"二级主题代表：{prefix}{theme}{stage_text}；来源：{source_path or 'result.etf_top + result.taxonomy_v2_ranking'}"
    if item.get("top_etf_rank") is not None and source_path != "result.theme_ranking.top_etf":
        source_path = f"{source_path or 'result.etf_top'} + result.theme_ranking.top_etf"
    return f"主线代表：{item.get('theme') or category_key or item['code']}；来源：{source_path or 'result.theme_ranking.top_etf'}"


def research_queue_sort_key(item: dict[str, Any]) -> tuple[int, int, float, str]:
    if is_broad_index_item(item):
        return (0, BROAD_INDEX_CATEGORY_ORDER.get(etf_category_key(item), 999), -_liquidity_amount(item), item["code"])
    if is_defensive_seed_item(item):
        return (1, DEFENSIVE_CATEGORY_ORDER.get(etf_category_key(item), 999), -_liquidity_amount(item), item["code"])
    if is_secondary_theme_item(item):
        scores = item.get("scores")
        secondary_score = _safe_float(scores.get("secondary_theme_score") if isinstance(scores, dict) else None)
        return (3, 999, -secondary_score, item["code"])
    return (2, 999, -_safe_float(item.get("score") or item.get("deep_score")), item["code"])


def research_representatives(items: list[dict[str, Any]], payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if payload is None or not _has_theme_ranking(payload):
        return _deduplicate_by_category(items)
    by_code = {item["code"]: item for item in items}
    mainline_representatives: list[dict[str, Any]] = []
    result = payload.get("result") or {}
    for theme_item in result.get("theme_ranking") or []:
        if not isinstance(theme_item, dict):
            continue
        candidates = [by_code[code] for code in _top_etf_codes(theme_item) if code in by_code]
        selected = _choose_liquid_representative(candidates)
        if selected is not None:
            _append_unique(mainline_representatives, selected)
    broad_items = [item for item in items if etf_category_key(item) in BROAD_INDEX_CATEGORY_KEYS]
    broad_representatives: list[dict[str, Any]] = []
    for item in _deduplicate_by_category(broad_items):
        _append_unique(broad_representatives, item)
    defensive_items = [item for item in items if is_defensive_seed_item(item)]
    defensive_representatives: list[dict[str, Any]] = []
    for item in _deduplicate_by_category(defensive_items):
        _append_unique(defensive_representatives, item)
    mainline_categories = {etf_category_key(item) for item in mainline_representatives}
    mainline_themes = {str(item.get("theme") or "") for item in mainline_representatives}
    secondary_representatives = [
        item
        for item in _secondary_theme_representatives(items, payload)
        if etf_category_key(item) not in mainline_categories and str(item.get("secondary_theme_name") or item.get("theme") or "") not in mainline_themes
    ]
    for item in secondary_representatives:
        original = by_code.get(item["code"])
        if original is not None:
            original.update(item)
    representatives = broad_representatives + defensive_representatives + mainline_representatives + secondary_representatives
    return representatives or _deduplicate_by_category(items)


def report_meta(payload: dict[str, Any]) -> dict[str, Any]:
    report = payload.get("report") or {}
    result = payload.get("result") or {}
    result = result if isinstance(result, dict) else {}
    report_id = report.get("report_id") or payload.get("report_id")
    if not report_id:
        raise ValueError("Missing report.report_id")
    return {
        "report_id": report_id,
        "schema_version": report.get("schema_version") or result.get("schema_version"),
        "generated_at": report.get("generated_at") or result.get("generated_at_iso") or result.get("generated_at"),
        "basis_date": report.get("basis_date") or result.get("basis_date"),
        "theme_report_id": report.get("theme_report_id") or result.get("theme_report_id"),
    }


def save_raw_payload(payload: dict[str, Any], report_id: str, raw_dir: Path = RAW_DATA_DIR) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_report_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", report_id)
    path = raw_dir / f"{safe_report_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _secondary_theme_prompt_block(item: dict[str, Any]) -> str:
    if not is_secondary_theme_item(item):
        return ""
    scores = item.get("scores")
    scores_map = scores if isinstance(scores, dict) else {}
    return f"""
二级主题/行业反转研究口径：
- 二级主题：{item.get('secondary_parent_name') or '未标注'} / {item.get('secondary_theme_name') or item.get('theme') or '未标注'}。
- 二级主题阶段：{item.get('secondary_stage') or '未标注'}；置信度：{item.get('secondary_confidence_label') or '未标注'}。
- 二级主题分：{scores_map.get('secondary_theme_score') or '未入库'}；市场热度：{scores_map.get('secondary_market_heat') or '未入库'}；政策分：{scores_map.get('secondary_policy_score') or '未入库'}。
- 研究目标不是证明它已经成为一级主线，而是判断是否出现行业底部反转、轮动修复或技术右侧确认。
- 必须单独覆盖技术反转观察：最大回撤、当前回撤、回撤分位、20/60/120 日均线、成交额放大、份额变化、相对沪深300/中证500强弱。
- 结论只允许表达为反转观察、左侧布局候选、右侧确认候选、过热暂缓或不适合研究，不得直接写成长期底仓。"""


def build_research_prompt(item: dict[str, Any], report: dict[str, Any]) -> str:
    code = item["code"]
    name = item["name"]
    theme = item.get("theme") or item.get("asset_class") or ""
    model = _model_context(item)
    report_id = report["report_id"]
    basis_date = report.get("basis_date") or ""
    return f"""在 C:\\Users\\kunpeng\\Documents\\MyInvestETF 中执行 ETF 完整深研。

唯一研究对象：{code} {name}。

入口信息：
- report_id：{report_id}
- basis_date：{basis_date}
- 主题/资产类别：{theme}
- valuation_model_type：{model['valuation_model_type']}
- sleeve_key：{model['sleeve_key']}
- taxonomy.etf_type：{model['etf_type']}
- taxonomy.subtype：{model['subtype']}
- taxonomy.lifecycle_stage：{model['lifecycle_stage'] or '不适用'}
{_secondary_theme_prompt_block(item)}

硬约束：
- 只研究这一只 ETF，禁止同时研究其他 ETF。
- Tushare 是结构化主源，优先使用 fund_basic、fund_daily、fund_nav、fund_share、fund_portfolio 和 index_daily。
- 网络资料只作为补充证据，必须记录来源和日期。
- 本任务一次性完成产品结构、指数、持仓、流动性、跟踪、估值输入、风险和组合角色研究。
- fund_portfolio 只能作为已披露季报持仓，不得表述为实时完整底仓。
- 最终参考价值区间、signal、grade、report_hash 和 run_id 必须由 deterministic pipeline 生成，LLM 不得手写。
- ETF scoring 必须绑定 taxonomy；分类理由只能来自产品、指数、持仓、行业暴露、波动或流动性证据。
- 不输出交易指令、不输出现金金额、不输出份额数量。

类型化研究要求：
{model['research_instruction']}

必须覆盖：
- 产品结构：基金类型、跟踪指数、资产类别、费率、规模和流动性。
- 底层指数：指数编制逻辑、行业/主题暴露、适合宽基/行业/主题/债券/现金替代的哪种角色。
- 底仓逻辑：是否适合作为底仓、工具仓、防守仓、现金替代或卫星仓。
- 持仓披露：前十大持仓、披露日期、集中度、披露滞后和实时完整持仓缺口。
- 跟踪质量：跟踪误差、折溢价、流动性和指数复制风险。
- 估值输入：净值、价格、折溢价、底层指数 PE/PB、估值分位和类型化 model_specific_inputs。
- 证伪条件：哪些规模、流动性、跟踪、指数估值或持仓变化会推翻当前角色判断。

执行流程：
1. 收集 Tushare 和必要网络补充资料，形成 research assembly_input JSON。
2. 将 assembly_input 写入 temp/assembly_inputs/{code}_research_{basis_date}.json。
3. 运行 python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite temp/assembly_inputs/{code}_research_{basis_date}.json > temp/reports/{code}_research_{basis_date}.json。
4. 用 python scripts/import_research_run.py temp/reports/{code}_research_{basis_date}.json 入库。
5. 导入成功后，汇报 run_id、report_hash、audit_log stage 覆盖、verify_run 结果、参考价值区间、data_gaps 和系统生成的主要结论摘要。

{ETF_REPORT_SCHEMA_INSTRUCTION}

{RESEARCH_ASSEMBLY_INPUT_INSTRUCTION}

完成后保证 /etfs/{code} 能看到由 deterministic pipeline 生成并入库的完整 ETF 深研结果。"""


def build_requested_research_prompt(item: dict[str, Any], report: dict[str, Any]) -> str:
    code = item["code"]
    name = item["name"]
    model = _model_context(item)
    basis_date = report.get("basis_date") or ""
    return f"""在 C:\\Users\\kunpeng\\Documents\\MyInvestETF 中执行 ETF 完整深研。

唯一研究对象：{code} {name}。

入口信息：
- 入口来源：用户主动请求 /research?etf={code}
- report_id：{report["report_id"]}
- basis_date：{basis_date}
- valuation_model_type：{model['valuation_model_type']}
- sleeve_key：{model['sleeve_key']}
- taxonomy.etf_type：{model['etf_type']}
- taxonomy.subtype：{model['subtype']}
- taxonomy.lifecycle_stage：{model['lifecycle_stage'] or '不适用'}

硬约束：
- 这只 ETF 不要求出现在 /api/index。
- 只研究这一只 ETF，禁止同时研究其他 ETF。
- Tushare 是结构化主源，网络资料只作补充证据。
- 本任务一次性完成产品结构、持仓披露、估值输入、类型化模型输入、风险和组合角色研究。
- 最终 ETFResearchReport 必须由 scripts/build_research_report.py 或 core/report.build_etf_report(...) 生成。
- LLM 不能重新计算参考价值区间、signal、grade、report_hash 或 run_id。
- ETF scoring 必须绑定 taxonomy；分类理由只能来自产品、指数、持仓、行业暴露、波动或流动性证据。
- 不输出交易指令、不输出现金金额、不输出份额数量。

类型化研究要求：
{model['research_instruction']}

执行流程：
1. 收集 Tushare 和必要网络补充资料，形成 research assembly_input JSON。
2. 将 assembly_input 写入 temp/assembly_inputs/{code}_research_{basis_date}.json。
3. 运行 python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite temp/assembly_inputs/{code}_research_{basis_date}.json > temp/reports/{code}_research_{basis_date}.json。
4. 用 python scripts/import_research_run.py temp/reports/{code}_research_{basis_date}.json 入库。
5. 导入成功后，汇报 run_id、report_hash、audit_log stage 覆盖、verify_run 结果、参考价值区间、data_gaps 和系统生成的主要结论摘要。

{ETF_REPORT_SCHEMA_INSTRUCTION}

{RESEARCH_ASSEMBLY_INPUT_INSTRUCTION}"""


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
    model_type = etf_model_type(item)
    item["valuation_model_type"] = model_type
    item["sleeve_key"] = sleeve_for_valuation_model(model_type)
    _attach_taxonomy_profile(item)
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
        if is_cash_like_etf(item):
            conn.commit()
            return {
                "code": etf_code,
                "name": etf_name,
                "report_id": report["report_id"],
                "basis_date": basis_date,
                "queued": queued,
                "skipped": ["cash_like_no_deep_research"],
                "valuation_model_type": model_type,
                "sleeve_key": item["sleeve_key"],
            }
        upsert_queue_item(
            conn,
            report_id=report["report_id"],
            code=etf_code,
            name=etf_name,
            priority=900,
            stage=1,
            task_type="research",
            task_keyword=f"MyInvestETF ETF完整深研 {etf_code} {etf_name}",
            prompt=build_requested_research_prompt(item, report),
            depends_on_task_type=None,
            task_date=basis_date,
            now=now,
            source_type=QUEUE_SOURCE_REQUEST,
            source_detail="/research",
        )
        queued.append("research")
        conn.commit()
    return {
        "code": etf_code,
        "name": etf_name,
        "report_id": report["report_id"],
        "basis_date": basis_date,
        "queued": queued,
        "skipped": [],
        "valuation_model_type": model_type,
        "sleeve_key": item["sleeve_key"],
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
    research_items = research_representatives(items, payload)
    keep_codes = [item["code"] for item in items]
    research_codes = [item["code"] for item in research_items]
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
        prune_trackable_report(conn, report_id=report["report_id"], keep_codes=keep_codes)
        prune_trackable_queue(conn, report_id=report["report_id"], keep_codes=research_codes)
        for item in sorted(items, key=lambda row: row.get("score") or row.get("deep_score") or 0, reverse=True):
            upsert_trackable_leader(conn, report_id=report["report_id"], item=item, created_at=now)
        for priority, item in enumerate(sorted(research_items, key=research_queue_sort_key), start=1):
            if is_cash_like_etf(item):
                continue
            upsert_queue_item(
                conn,
                report_id=report["report_id"],
                code=item["code"],
                name=item["name"],
                priority=priority,
                stage=1,
                task_type="research",
                task_keyword=f"MyInvestETF ETF完整深研 {item['code']} {item['name']}",
                prompt=build_research_prompt(item, report),
                depends_on_task_type=None,
                task_date=report.get("basis_date"),
                now=now,
                source_type=queue_source_for_item(item),
                source_detail=queue_source_detail_for_item(item),
            )
        conn.commit()
    return {
        "report_id": report["report_id"],
        "basis_date": report.get("basis_date"),
        "count": len(items),
        "research_count": len(research_items),
        "codes": [item["code"] for item in items],
        "names": [item["name"] for item in items],
        "research_codes": [item["code"] for item in research_items],
        "research_names": [item["name"] for item in research_items],
    }
