from __future__ import annotations

import html
import json
import mimetypes
import re
from contextlib import closing
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from core.valuation import infer_valuation_model_type, sleeve_for_valuation_model

from .config import DB_PATH, DEFAULT_HOST, DEFAULT_PORT, FOOTER_SCRIPT_URL, HEADER_SCRIPT_URL, ROOT, STATIC_ASSET_VERSION
from .db import (
    connect,
    get_known_leader,
    get_latest_leader,
    latest_report,
    list_daily_prices,
    list_latest_leaders,
    list_queue,
    list_queue_for_etf,
    list_research_runs,
    list_trackable_history,
    queue_source_label,
    rows_to_dicts,
    valuation_runs,
)
from .config import LEADER_INDEX_URL
from .leader_index import enqueue_requested_etf

ETF_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def load_json(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


MODEL_TYPE_LABELS = {
    "broad_index": "宽基估值",
    "mainline_theme": "主线估值",
    "factor_defensive": "收益防御估值",
    "cash_like": "现金替代监控",
}

SLEEVE_LABELS = {
    "core_wide_etf": "核心宽基仓",
    "mainline_etf": "主线进攻仓",
    "defensive_quality": "收益防御仓",
    "cash_like": "现金替代仓",
}


def leader_model_info(row: object | None) -> dict[str, object]:
    if row is None:
        return {"valuation_model_type": "mainline_theme", "sleeve_key": "mainline_etf"}
    raw = load_json(_row_value(row, "raw_json"), {})
    raw_map = raw if isinstance(raw, dict) else {}
    model_type = str(raw_map.get("valuation_model_type") or "")
    if not model_type:
        model_type = infer_valuation_model_type(
            {
                "code": _row_value(row, "code"),
                "name": _row_value(row, "name"),
                "theme": _row_value(row, "theme"),
                "category_key": raw_map.get("category_key"),
            }
        )
    sleeve_key = str(raw_map.get("sleeve_key") or sleeve_for_valuation_model(model_type))
    return {
        "valuation_model_type": model_type,
        "valuation_model_label": MODEL_TYPE_LABELS.get(model_type, model_type),
        "sleeve_key": sleeve_key,
        "sleeve_label": SLEEVE_LABELS.get(sleeve_key, sleeve_key),
    }


def leader_category_key(row: object) -> str:
    raw = load_json(_row_value(row, "raw_json"), {})
    raw_map = raw if isinstance(raw, dict) else {}
    return str(raw_map.get("category_key") or _row_value(row, "theme") or "")


def leader_to_summary(row: object) -> dict[str, object]:
    market = load_json(row["market_json"], {})
    scores = load_json(row["scores_json"], {})
    upstream_signal = upstream_signal_summary(row)
    model_info = leader_model_info(row)
    return {
        "code": row["code"],
        "name": row["name"],
        **model_info,
        "theme": row["theme"],
        "category_key": leader_category_key(row),
        "themes": load_json(row["themes_json"], []),
        "deep_rating": row["deep_rating"],
        "deep_label": row["deep_label"],
        "deep_score": row["deep_score"],
        "shadow_observation_eligible": bool(row["shadow_observation_eligible"]),
        "candidate": {
            "leader_tier": row["candidate_leader_tier"],
            "leader_claim": row["candidate_leader_claim"],
            "evidence_score": row["candidate_evidence_score"],
            "evidence_count": row["candidate_evidence_count"],
            "hard_evidence_count": row["candidate_hard_evidence_count"],
        },
        "market": market,
        "scores": scores,
        "upstream_signal": upstream_signal,
        "risk_flags": load_json(row["risk_flags_json"], []),
        "data_gaps": load_json(row["data_gaps_json"], []),
        "links": {
            "page": f"/etfs/{row['code']}",
            "research_gateway": f"/research?etf={row['code']}",
            "api": f"/api/etfs/{row['code']}",
            "xueqiu": row["xueqiu_url"],
        },
    }


def research_run_to_summary(row: object) -> dict[str, object]:
    valuation_signal = valuation_signal_summary(row)
    raw = load_json(row["raw_json"], {})
    raw_map = raw if isinstance(raw, dict) else {}
    return {
        "id": row["id"],
        "task_type": row["task_type"],
        "research_date": row["research_date"],
        "status": row["status"],
        "valuation_model_type": raw_map.get("valuation_model_type"),
        "sleeve_key": raw_map.get("sleeve_key"),
        "title": row["title"],
        "summary": row["summary"],
        "valuation": {
            "low": row["valuation_low"],
            "mid": row["valuation_mid"],
            "high": row["valuation_high"],
            "unit": row["valuation_unit"],
            "method": row["valuation_method"],
            "confidence": row["valuation_confidence"],
        },
        "industry_position": row["industry_position"],
        "competition_landscape": row["competition_landscape"],
        "upstream_downstream": row["upstream_downstream"],
        "annual_growth": row["annual_growth"],
        "multi_bagger_potential": row["multi_bagger_potential"],
        "heavy_position_view": row["heavy_position_view"],
        "valuation_signal": valuation_signal,
        "evidence": load_json(row["evidence_json"], []),
        "assumptions": load_json(row["assumptions_json"], []),
        "risks": load_json(row["risks_json"], []),
    }


def latest_research_run(runs: list[object]) -> dict[str, object] | None:
    for row in runs:
        if row["task_type"] == "research":
            return research_run_to_summary(row)
    if runs:
        return research_run_to_summary(runs[0])
    return None


def valuation_history_payload(runs: list[object]) -> list[dict[str, object]]:
    history = []
    for row in runs:
        history.append(
            {
                "research_date": row["research_date"],
                "low": row["valuation_low"],
                "mid": row["valuation_mid"],
                "high": row["valuation_high"],
                "unit": row["valuation_unit"],
                "method": row["valuation_method"],
                "confidence": row["valuation_confidence"],
                "heavy_position_view": row["heavy_position_view"],
            }
        )
    return history


def render_layout(title: str, body: str) -> bytes:
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} | MyInvestETF</title>
  <link rel="stylesheet" href="/static/styles.css?v={STATIC_ASSET_VERSION}">
</head>
<body>
  <div data-myinvest-header></div>
  <main>
{body}
  </main>
  <div data-myinvest-footer></div>
  <script src="{HEADER_SCRIPT_URL}" data-target="[data-myinvest-header]" defer></script>
  <script src="{FOOTER_SCRIPT_URL}" data-target="[data-myinvest-footer]" defer></script>
</body>
</html>
"""
    return html_text.encode("utf-8")


def fmt_num(value: object, digits: int = 2) -> str:
    if value is None:
        return "待入库"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return esc(value)


def _num(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _signal_bucket(score: object, *, strong: float, weak: float) -> str:
    number = _num(score)
    if number is None:
        return "unknown"
    if number >= strong:
        return "strong"
    if number >= weak:
        return "watch"
    return "weak"


def _bucket_label(bucket: str, *, kind: str) -> str:
    if kind == "upstream":
        return {
            "strong": "上游主线信号强",
            "watch": "上游主线可跟踪",
            "weak": "上游主线偏弱",
            "unknown": "等待上游信号",
        }.get(bucket, "等待上游信号")
    return {
        "high": "ETF估值与底仓适配较好",
        "medium": "ETF估值与底仓适配中性",
        "low": "ETF估值或拥挤压力较高",
        "unknown": "等待ETF估值",
    }.get(bucket, "等待ETF估值")


def upstream_signal_summary(row: object | None) -> dict[str, object]:
    if row is None:
        return {
            "source": "theme.okbbc.com/api/latest",
            "theme": None,
            "bucket": "unknown",
            "label": _bucket_label("unknown", kind="upstream"),
            "explanation": "未找到主线主题接口入库信号。",
        }
    scores_value = load_json(row["scores_json"], {})
    market_value = load_json(row["market_json"], {})
    risk_flags_value = load_json(row["risk_flags_json"], [])
    themes_value = load_json(row["themes_json"], [])
    scores = scores_value if isinstance(scores_value, dict) else {}
    market = market_value if isinstance(market_value, dict) else {}
    risk_flags = risk_flags_value if isinstance(risk_flags_value, list) else []
    themes = themes_value if isinstance(themes_value, list) else []
    theme_binding = _num(scores.get("theme_binding"))
    leader_score = _num(row["deep_score"])
    evidence_quality = _num(scores.get("evidence_quality") or row["candidate_evidence_score"])
    trading_structure = _num(scores.get("trading_structure"))

    anchor_score = theme_binding if theme_binding is not None else leader_score
    bucket = _signal_bucket(anchor_score, strong=80.0, weak=60.0)
    if bucket == "strong" and leader_score is not None and leader_score < 65.0:
        bucket = "watch"
    label = _bucket_label(bucket, kind="upstream")
    parts = [
        f"主题绑定 {fmt_num(theme_binding)}",
        f"主线强度 {fmt_num(leader_score)}",
        f"证据质量 {fmt_num(evidence_quality)}",
        f"交易结构 {fmt_num(trading_structure)}",
    ]
    return {
        "source": "theme.okbbc.com/api/latest",
        "theme": row["theme"],
        "themes": themes,
        "bucket": bucket,
        "label": label,
        "theme_binding": theme_binding,
        "leader_score": leader_score,
        "evidence_quality": evidence_quality,
        "trading_structure": trading_structure,
        "rating": f"{row['deep_rating'] or ''} {row['deep_label'] or ''}".strip(),
        "leader_claim": row["candidate_leader_claim"],
        "market": {
            "r5": market.get("r5"),
            "r20": market.get("r20"),
            "r60": market.get("r60"),
            "turnover_rate": market.get("turnover_rate"),
        },
        "risk_flags": risk_flags,
        "explanation": "；".join(parts),
    }


def valuation_signal_explanation(
    *,
    model_type: str,
    undervalued_score: object,
    liquidity_score: object,
    tracking_score: object,
    portfolio_role_score: object,
    risk_adjusted_score: object,
    mainline_validity_score: object,
    valuation_tolerance_score: object,
    crowding_risk_score: object,
    factor_premium_score: object,
    cash_like_safety_score: object,
) -> str:
    if model_type == "mainline_theme":
        lead = (
            f"主线有效性 {fmt_num(mainline_validity_score)}；估值容错 {fmt_num(valuation_tolerance_score)}；"
            f"拥挤风险 {fmt_num(crowding_risk_score)}"
        )
    elif model_type == "factor_defensive":
        lead = f"防御因子溢价 {fmt_num(factor_premium_score)}"
    elif model_type == "cash_like":
        lead = f"现金替代安全 {fmt_num(cash_like_safety_score)}"
    else:
        lead = f"宽基估值安全 {fmt_num(undervalued_score)}"
    return (
        f"{lead}；流动性 {fmt_num(liquidity_score)}；跟踪质量 {fmt_num(tracking_score)}；"
        f"仓位角色 {fmt_num(portfolio_role_score)}；风险调整 {fmt_num(risk_adjusted_score)}"
    )


def valuation_signal_summary(row: object | None) -> dict[str, object]:
    if row is None:
        return {
            "source": "MyInvestETF deterministic valuation",
            "valuation_model_type": None,
            "valuation_model_label": "待估值",
            "sleeve_key": None,
            "sleeve_label": "待分类",
            "bucket": "unknown",
            "label": _bucket_label("unknown", kind="valuation"),
            "explanation": "等待ETF完整深研入库。",
        }
    raw = load_json(_row_value(row, "raw_json"), {})
    valuation = raw.get("valuation") if isinstance(raw, dict) else {}
    conclusion = raw.get("conclusion") if isinstance(raw, dict) else {}
    model_type = str(raw.get("valuation_model_type") or "") if isinstance(raw, dict) else ""
    sleeve_key = str(raw.get("sleeve_key") or "") if isinstance(raw, dict) else ""
    undervalued_score = _num(valuation.get("undervalued_score")) if isinstance(valuation, dict) else None
    risk_adjusted_score = _num(valuation.get("risk_adjusted_score")) if isinstance(valuation, dict) else None
    liquidity_score = _num(valuation.get("liquidity_score")) if isinstance(valuation, dict) else None
    tracking_score = _num(valuation.get("tracking_score")) if isinstance(valuation, dict) else None
    portfolio_role_score = _num(valuation.get("portfolio_role_score")) if isinstance(valuation, dict) else None
    current_price = _num(valuation.get("current_price")) if isinstance(valuation, dict) else None
    nav = _num(valuation.get("nav")) if isinstance(valuation, dict) else None
    premium_discount = _num(valuation.get("premium_discount")) if isinstance(valuation, dict) else None
    mainline_validity_score = _num(valuation.get("mainline_validity_score")) if isinstance(valuation, dict) else None
    valuation_tolerance_score = _num(valuation.get("valuation_tolerance_score")) if isinstance(valuation, dict) else None
    crowding_risk_score = _num(valuation.get("crowding_risk_score")) if isinstance(valuation, dict) else None
    factor_premium_score = _num(valuation.get("factor_premium_score")) if isinstance(valuation, dict) else None
    cash_like_safety_score = _num(valuation.get("cash_like_safety_score")) if isinstance(valuation, dict) else None
    if undervalued_score is None:
        bucket = "unknown"
    elif undervalued_score >= 70.0:
        bucket = "high"
    elif undervalued_score >= 40.0:
        bucket = "medium"
    else:
        bucket = "low"
    label = _bucket_label(bucket, kind="valuation")
    return {
        "source": "MyInvestETF deterministic valuation",
        "valuation_model_type": model_type or None,
        "valuation_model_label": MODEL_TYPE_LABELS.get(model_type, model_type or "待估值"),
        "sleeve_key": sleeve_key or None,
        "sleeve_label": SLEEVE_LABELS.get(sleeve_key, sleeve_key or "待分类"),
        "bucket": bucket,
        "label": label,
        "undervalued_score": undervalued_score,
        "liquidity_score": liquidity_score,
        "tracking_score": tracking_score,
        "portfolio_role_score": portfolio_role_score,
        "current_price": current_price,
        "nav": nav,
        "premium_discount": premium_discount,
        "risk_adjusted_score": risk_adjusted_score,
        "mainline_validity_score": mainline_validity_score,
        "valuation_tolerance_score": valuation_tolerance_score,
        "crowding_risk_score": crowding_risk_score,
        "factor_premium_score": factor_premium_score,
        "cash_like_safety_score": cash_like_safety_score,
        "valuation_range": {
            "low": _row_value(row, "valuation_low"),
            "mid": _row_value(row, "valuation_mid"),
            "high": _row_value(row, "valuation_high"),
            "unit": _row_value(row, "valuation_unit"),
            "method": _row_value(row, "valuation_method"),
        },
        "raw_grade": _row_value(row, "heavy_position_view"),
        "raw_summary": conclusion.get("summary") if isinstance(conclusion, dict) else None,
        "explanation": valuation_signal_explanation(
            model_type=model_type,
            undervalued_score=undervalued_score,
            liquidity_score=liquidity_score,
            tracking_score=tracking_score,
            portfolio_role_score=portfolio_role_score,
            risk_adjusted_score=risk_adjusted_score,
            mainline_validity_score=mainline_validity_score,
            valuation_tolerance_score=valuation_tolerance_score,
            crowding_risk_score=crowding_risk_score,
            factor_premium_score=factor_premium_score,
            cash_like_safety_score=cash_like_safety_score,
        ),
    }


def decision_matrix_summary(
    upstream_signal: dict[str, object],
    valuation_signal: dict[str, object],
) -> dict[str, object]:
    upstream_bucket = str(upstream_signal.get("bucket") or "unknown")
    valuation_bucket = str(valuation_signal.get("bucket") or "unknown")
    if upstream_bucket == "unknown":
        conclusion = "等待上游或产品结构信号"
        posture = "待确认"
    elif valuation_bucket == "unknown":
        conclusion = "等待ETF估值、流动性和跟踪质量验证"
        posture = "待完整深研"
    elif upstream_bucket == "strong" and valuation_bucket == "high":
        conclusion = "上游/产品信号强，ETF估值与底仓适配较好，可进入底仓候选研究"
        posture = "底仓候选"
    elif upstream_bucket == "strong" and valuation_bucket in {"medium", "low"}:
        conclusion = "上游/产品信号强，但估值、拥挤或流动性仍需观察，更适合作为工具仓跟踪"
        posture = "工具仓跟踪"
    elif upstream_bucket in {"watch", "weak"} and valuation_bucket == "high":
        conclusion = "估值与流动性较好，但上游/产品信号未确认，适合作为观察型配置工具"
        posture = "观察型工具"
    elif upstream_bucket == "watch" and valuation_bucket == "medium":
        conclusion = "产品信号和ETF估值都处于中性区间，继续观察净值、折溢价、份额和流动性"
        posture = "观察"
    else:
        conclusion = "产品信号偏弱且估值或拥挤压力较高，优先等待风险释放"
        posture = "暂缓底仓"
    return {
        "upstream_bucket": upstream_bucket,
        "valuation_bucket": valuation_bucket,
        "upstream_label": upstream_signal.get("label"),
        "valuation_label": valuation_signal.get("label"),
        "posture": posture,
        "conclusion": conclusion,
        "rule": "theme.okbbc.com upstream signal + MyInvestETF valuation safety margin matrix",
    }


def score_state(value: object, *, kind: str = "default") -> str:
    number = _num(value)
    if number is None:
        return "待入库"
    if kind == "valuation_safety":
        if number >= 85:
            return "估值安全边际较高"
        if number >= 70:
            return "估值相对可接受"
        if number >= 50:
            return "估值中性，需结合增长验证"
        return "估值压力较高"
    if kind == "evidence_quality":
        if number >= 85:
            return "证据强，龙头判断较扎实"
        if number >= 70:
            return "证据较充分"
        if number >= 60:
            return "证据可观察，仍需深研确认"
        return "证据偏弱"
    if kind == "deep_score":
        if number >= 80:
            return "高优先级深研对象"
        if number >= 70:
            return "可跟踪深研对象"
        if number >= 60:
            return "观察型候选"
        return "低优先级候选"
    if number >= 85:
        return "强"
    if number >= 70:
        return "较好"
    if number >= 60:
        return "中性"
    return "偏弱"


def score_signal(value: object, *, kind: str = "default") -> tuple[str, str]:
    number = _num(value)
    if number is None:
        return "unknown", "待入库"
    if kind == "valuation_safety":
        if number >= 85:
            return "safe", "低估/安全"
        if number >= 70:
            return "ok", "估值可接受"
        if number >= 50:
            return "watch", "估值中性"
        return "danger", "估值危险"
    if kind == "evidence_quality":
        if number >= 85:
            return "safe", "证据可信"
        if number >= 70:
            return "ok", "证据较足"
        if number >= 60:
            return "watch", "需确认"
        return "danger", "证据偏弱"
    if kind == "deep_score":
        if number >= 80:
            return "safe", "高优先级"
        if number >= 70:
            return "ok", "可跟踪"
        if number >= 60:
            return "watch", "观察"
        return "danger", "低优先"
    if number >= 85:
        return "safe", "强"
    if number >= 70:
        return "ok", "较好"
    if number >= 60:
        return "watch", "中性"
    return "danger", "偏弱"


def ratio_state(label: str, value: object) -> str:
    number = _num(value)
    if number is None:
        return "待入库"
    if label == "PE TTM":
        if number <= 0:
            return "亏损或口径不适用"
        if number < 15:
            return "低市盈率区间"
        if number < 30:
            return "中等市盈率区间"
        if number < 60:
            return "较高市盈率，需增长兑现"
        return "高市盈率，需强增长支撑"
    if label == "PB":
        if number < 1:
            return "低于净资产定价"
        if number < 3:
            return "常见市净率区间"
        if number < 6:
            return "较高市净率，需高 ROE 支撑"
        return "高市净率，需强盈利质量支撑"
    return "行情快照"


def ratio_signal(label: str, value: object) -> tuple[str, str]:
    number = _num(value)
    if number is None:
        return "unknown", "待入库"
    if label == "PE TTM":
        if number <= 0:
            return "danger", "亏损/异常"
        if number < 15:
            return "safe", "低估"
        if number < 30:
            return "ok", "合理"
        if number < 60:
            return "watch", "偏贵"
        return "danger", "危险"
    if label == "PB":
        if number < 1:
            return "safe", "资产折价"
        if number < 3:
            return "ok", "合理"
        if number < 6:
            return "watch", "偏贵"
        return "danger", "危险"
    return "neutral", "行情快照"


def metric_explanation(label: str, value: object) -> tuple[str, str]:
    if label in {"深研", "深研分"}:
        return (
            "综合入口评分",
            f"{score_state(value, kind='deep_score')}。衡量这只ETF是否值得进入深研队列，不等于最终底仓结论。",
        )
    if label == "当前价格":
        return (
            "价格快照",
            "优先使用本地日行情最新收盘价；如果行情未缓存，则使用最新完整深研中的 current_price 或入口收盘价。",
        )
    if label == "收盘":
        return (
            "行情快照",
            "基准数据中的收盘价，单位通常为元/股；它是价格参照，不代表合理估值。",
        )
    if label in {"PE TTM", "PB"}:
        return (
            "估值倍数",
            f"{ratio_state(label, value)}。这是入口快照口径，仍需结合行业、增长、ROE 和现金流判断。",
        )
    if label == "证据质量":
        return (
            "龙头证据强度",
            f"{score_state(value, kind='evidence_quality')}。分数越高，说明支持龙头地位的硬证据越充分。",
        )
    if label == "估值安全":
        return (
            "入口估值安全度",
            f"{score_state(value, kind='valuation_safety')}。分数越高，表示入口筛选看估值越不紧张；最终参考区间以ETF完整深研为准。",
        )
    return ("指标说明", "入口展示指标，用于辅助筛选和跟踪。")


def metric_signal(label: str, value: object) -> tuple[str, str]:
    if label in {"深研", "深研分"}:
        return score_signal(value, kind="deep_score")
    if label == "当前价格":
        return "neutral", "价格快照"
    if label == "收盘":
        return "neutral", "行情快照"
    if label in {"PE TTM", "PB"}:
        return ratio_signal(label, value)
    if label == "证据质量":
        return score_signal(value, kind="evidence_quality")
    if label == "估值安全":
        return score_signal(value, kind="valuation_safety")
    return "neutral", "参考"


def metric(label: str, value: object, unit: str = "") -> str:
    shown = fmt_num(value) if isinstance(value, (int, float)) else esc(value or "待入库")
    tooltip_title, tooltip_body = metric_explanation(label, value)
    signal_class, signal_label = metric_signal(label, value)
    tooltip_text = f"{tooltip_title}：{tooltip_body}"
    return f"""<div class="metric metric-signal-{esc(signal_class)}" tabindex="0" title="{esc(tooltip_text)}" aria-label="{esc(label)}：{esc(shown)}{esc(unit)}。{esc(signal_label)}。{esc(tooltip_text)}">
      <span>{esc(label)}</span>
      <strong>{shown}{esc(unit)}</strong>
      <small class="metric-signal-label">{esc(signal_label)}</small>
      <div class="metric-tooltip" role="tooltip">
        <b>{esc(tooltip_title)}</b>
        <em>{esc(tooltip_body)}</em>
      </div>
    </div>"""


def compact_metric(label: str, value: object, unit: str = "") -> str:
    shown = fmt_num(value) if isinstance(value, (int, float)) else esc(value or "待入库")
    tooltip_title, tooltip_body = metric_explanation(label, value)
    signal_class, signal_label = metric_signal(label, value)
    tooltip_text = f"{tooltip_title}：{tooltip_body}"
    return f"""<div class="compact-metric compact-metric-{esc(signal_class)}" title="{esc(tooltip_text)}" aria-label="{esc(label)}：{esc(shown)}{esc(unit)}。{esc(signal_label)}">
      <span>{esc(label)}</span>
      <strong>{shown}{esc(unit)}</strong>
      <small>{esc(signal_label)}</small>
    </div>"""


def xueqiu_url_for_code(code: object, preferred_url: object | None = None) -> str:
    if preferred_url:
        return str(preferred_url)
    text = str(code)
    if "." not in text:
        return "https://xueqiu.com/"
    symbol, exchange = text.split(".", 1)
    return f"https://xueqiu.com/S/{exchange.upper()}{symbol}"


def etf_page_link(code: object, label: object) -> str:
    safe_code = esc(code)
    return f"""<a class="table-link" href="/etfs/{safe_code}">{esc(label)}</a>"""


def xueqiu_etf_link(code: object, preferred_url: object | None = None) -> str:
    return (
        f"""<a class="code-link" href="{esc(xueqiu_url_for_code(code, preferred_url))}" """
        f"""target="_blank" rel="noopener noreferrer">{esc(code)}</a>"""
    )


def queue_display_rows(queue: list[object]) -> list[dict[str, object]]:
    rows_by_code: dict[str, dict[str, object]] = {}
    for row in queue:
        code = str(row["code"])
        item = rows_by_code.setdefault(
            code,
            {
                "priority": row["priority"],
                "stage": row["stage"],
                "source_labels": [],
                "code": code,
                "name": row["name"],
                "tasks": [],
                "keywords": [],
            },
        )
        label = queue_source_label(row["source_type"])
        if label not in item["source_labels"]:
            item["source_labels"].append(label)
        task_label = f"{row['task_type']}:{row['status']}"
        item["tasks"].append(task_label)
        keyword = str(row["task_keyword"] or "")
        if keyword and keyword not in item["keywords"]:
            item["keywords"].append(keyword)
    return list(rows_by_code.values())


def render_queue_rows(queue: list[object]) -> str:
    if not queue:
        return '<tr><td colspan="7" class="empty-cell">当前队列为空。</td></tr>'
    rows = queue_display_rows(queue)
    return "".join(
        f"""<tr>
      <td>{esc(row['priority'])}</td>
      <td>{esc(row['stage'])}</td>
      <td>{esc(' / '.join(row['source_labels']))}</td>
      <td>{xueqiu_etf_link(row['code'])}</td>
      <td>{etf_page_link(row['code'], row['name'])}</td>
      <td>{esc(' / '.join(row['tasks']))}</td>
      <td>{esc('；'.join(row['keywords']))}</td>
    </tr>"""
        for row in rows
    )


def is_broad_index_leader(row: object) -> bool:
    return leader_model_info(row).get("valuation_model_type") == "broad_index"


def render_etf_cards(rows: list[object]) -> str:
    cards = []
    for row in rows:
        market = load_json(_row_value(row, "market_json"), {})
        scores = load_json(_row_value(row, "scores_json"), {})
        model_info = leader_model_info(row)
        category_key = leader_category_key(row)
        cards.append(
            f"""<article class="etf-card">
        <div class="etf-card-top">
          <div>
            <a class="etf-title" href="/etfs/{esc(_row_value(row, 'code'))}">{esc(_row_value(row, 'name'))}</a>
            <div class="etf-code">{xueqiu_etf_link(_row_value(row, 'code'), _row_value(row, 'xueqiu_url'))}</div>
          </div>
          <a class="text-link card-action" href="/etfs/{esc(_row_value(row, 'code'))}">查看</a>
        </div>
        <div class="badges">
          <span class="badge badge-strong">{esc(_row_value(row, 'deep_rating') or '')} {esc(_row_value(row, 'deep_label') or '')}</span>
          <span class="badge">{esc(category_key)}</span>
          <span class="badge">{esc(model_info.get('valuation_model_label'))}</span>
          <span class="badge">{esc(model_info.get('sleeve_label'))}</span>
        </div>
        <div class="compact-metrics">
          {compact_metric("深研", _row_value(row, "deep_score"))}
          {compact_metric("收盘", market.get("close") if isinstance(market, dict) else None)}
          {compact_metric("PE TTM", market.get("pe_ttm") if isinstance(market, dict) else None)}
          {compact_metric("估值安全", scores.get("valuation_safety") if isinstance(scores, dict) else None)}
        </div>
      </article>"""
        )
    return "".join(cards) if cards else '<p class="empty">暂无代表 ETF。</p>'


def render_home() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
        queue = list_queue(conn)
    if not report:
        body = """
    <section class="page-band">
      <div class="content">
        <h1>可跟踪ETF</h1>
        <p class="muted">本地还没有入库数据。先运行 <code>python scripts/ingest_index.py</code>。</p>
      </div>
    </section>
"""
        return render_layout("可跟踪ETF", body)

    leader_by_code = {row["code"]: row for row in leaders}
    queue_codes = []
    for row in queue:
        code = row["code"]
        if code not in queue_codes:
            queue_codes.append(code)
    display_leaders = [leader_by_code[code] for code in queue_codes if code in leader_by_code]
    if not display_leaders:
        display_leaders = leaders

    broad_leaders = [row for row in display_leaders if is_broad_index_leader(row)]
    mainline_leaders = [row for row in display_leaders if not is_broad_index_leader(row)]

    queue_summary_count = len(queue_display_rows(queue))
    queue_rows = render_queue_rows(queue)
    body = f"""
    <section class="page-band">
      <div class="content">
        <div class="page-title-row">
          <div>
            <h1>ETF研究代表</h1>
            <p class="muted">ETF 池来自 <code>theme_ranking.top_etf</code>、<code>result.etf_top</code> 和本地核心宽基种子。</p>
            <p class="muted">当前 ETF 池 {esc(len(leaders))} 只；主屏显示 {esc(len(display_leaders))} 只研究代表：核心宽基 {esc(len(broad_leaders))} 只，主线代表 {esc(len(mainline_leaders))} 只。</p>
          </div>
          <div class="report-box">
            <span>report_id</span>
            <strong>{esc(report['report_id'])}</strong>
            <span>basis_date {esc(report['basis_date'])}</span>
          </div>
        </div>
      </div>
    </section>
    <section class="content representative-section">
      <div class="section-heading-row">
        <div>
          <h2>核心宽基ETF</h2>
          <p class="muted">来源为本地核心宽基种子和宽基类别代表，不从主线代表推导。</p>
        </div>
        <span class="section-count">{esc(len(broad_leaders))} 只</span>
      </div>
      <div class="etf-grid">{render_etf_cards(broad_leaders)}</div>
    </section>
    <section class="content representative-section">
      <div class="section-heading-row">
        <div>
          <h2>主线ETF代表</h2>
          <p class="muted">来源为 <code>theme_ranking.top_etf</code>，每条主线只保留一个流动性代表。</p>
        </div>
        <span class="section-count">{esc(len(mainline_leaders))} 只</span>
      </div>
      <div class="etf-grid">{render_etf_cards(mainline_leaders)}</div>
    </section>
    <section class="content section-block">
      <h2>ETF深研队列</h2>
      <p class="muted">按 ETF 合并显示：{esc(queue_summary_count)} 只 ETF，{esc(len(queue))} 个研究任务。</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>优先级</th><th>阶段</th><th>来源</th><th>代码</th><th>名称</th><th>任务状态</th><th>任务关键词</th></tr></thead>
          <tbody>{queue_rows}</tbody>
        </table>
      </div>
    </section>
"""
    return render_layout("可跟踪ETF", body)


def render_empty_section(title: str) -> str:
    return f"""<section class="section-block">
      <h2>{esc(title)}</h2>
      <p class="empty">等待ETF深研入库。</p>
    </section>"""


def short_date(value: object) -> str:
    text = str(value or "")
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text[5:]
    return text


def _chart_x(index: int, count: int, left: float, width: float) -> float:
    if count <= 1:
        return left + width / 2.0
    return left + width * index / (count - 1)


def _chart_y(value: float, lower: float, upper: float, top: float, height: float) -> float:
    if upper <= lower:
        return top + height / 2.0
    return top + (upper - value) / (upper - lower) * height


def _valuation_chart_points(runs: list[object]) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for row in runs:
        try:
            low = float(row["valuation_low"])
            mid = float(row["valuation_mid"])
            high = float(row["valuation_high"])
        except (KeyError, TypeError, ValueError):
            continue
        if high < low:
            low, high = high, low
        points.append(
            {
                "date": str(row["research_date"]),
                "low": low,
                "mid": mid,
                "high": high,
                "method": row["valuation_method"] or "待入库",
                "grade": row["heavy_position_view"] or "待入库",
            }
        )
    return points


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        if isinstance(row, dict):
            return row.get(key)
        return None


def _daily_price_points(prices: list[object]) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for row in prices:
        try:
            open_price = float(_row_value(row, "open_price"))
            high_price = float(_row_value(row, "high_price"))
            low_price = float(_row_value(row, "low_price"))
            close_price = float(_row_value(row, "close_price"))
        except (TypeError, ValueError):
            continue
        if high_price < low_price:
            high_price, low_price = low_price, high_price
        points.append(
            {
                "date": str(_row_value(row, "trade_date")),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )
    return points


def _latest_cached_close(prices: list[object]) -> float | None:
    points = _daily_price_points(prices)
    if not points:
        return None
    points.sort(key=lambda item: str(item["date"]))
    return _num(points[-1].get("close"))


def _research_current_price(row: object | None) -> float | None:
    if row is None:
        return None
    raw = load_json(_row_value(row, "raw_json"), {})
    valuation = raw.get("valuation") if isinstance(raw, dict) else {}
    if not isinstance(valuation, dict):
        return None
    return _num(valuation.get("current_price"))


def _display_current_price(latest: object | None, prices: list[object], market: object) -> float | None:
    cached_close = _latest_cached_close(prices)
    if cached_close is not None:
        return cached_close
    research_price = _research_current_price(latest)
    if research_price is not None:
        return research_price
    if isinstance(market, dict):
        return _num(market.get("close"))
    return None


def _parsed_date(value: object) -> object | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _valuation_price_start(runs: list[object]) -> str | None:
    dates = [_parsed_date(_row_value(row, "research_date")) for row in runs]
    valid_dates = [item for item in dates if item is not None]
    if not valid_dates:
        return None
    return (min(valid_dates) - timedelta(days=45)).isoformat()


def _render_plain_valuation_chart(points: list[dict[str, object]]) -> str:
    if not points:
        return render_empty_section("ETF参考价格区间历史")

    width = 760.0
    height = 320.0
    left = 64.0
    right = 24.0
    top = 28.0
    bottom = 52.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    lows = [float(item["low"]) for item in points]
    highs = [float(item["high"]) for item in points]
    lower = min(lows)
    upper = max(highs)
    span = upper - lower
    pad = max(span * 0.08, max(abs(upper), 1.0) * 0.02, 1.0)
    y_min = lower - pad
    y_max = upper + pad

    positioned = []
    count = len(points)
    for index, point in enumerate(points):
        x = _chart_x(index, count, left, plot_width)
        positioned.append(
            {
                **point,
                "x": x,
                "y_low": _chart_y(float(point["low"]), y_min, y_max, top, plot_height),
                "y_mid": _chart_y(float(point["mid"]), y_min, y_max, top, plot_height),
                "y_high": _chart_y(float(point["high"]), y_min, y_max, top, plot_height),
            }
        )

    tick_lines = []
    for index in range(5):
        value = y_max - (y_max - y_min) * index / 4.0
        y = _chart_y(value, y_min, y_max, top, plot_height)
        tick_lines.append(
            f"""<g>
          <line class="valuation-grid-line" x1="{left:.1f}" y1="{y:.1f}" x2="{width - right:.1f}" y2="{y:.1f}"></line>
          <text class="valuation-axis-label" x="{left - 10:.1f}" y="{y + 4:.1f}" text-anchor="end">{fmt_num(value)}</text>
        </g>"""
        )

    if count > 1:
        upper_points = " ".join(f"{item['x']:.1f},{item['y_high']:.1f}" for item in positioned)
        lower_points = " ".join(f"{item['x']:.1f},{item['y_low']:.1f}" for item in reversed(positioned))
        band_svg = f"""<polygon class="valuation-band" points="{upper_points} {lower_points}"></polygon>"""
        high_line = f"""<polyline class="valuation-boundary-line" points="{upper_points}"></polyline>"""
        low_line = f"""<polyline class="valuation-boundary-line" points="{" ".join(f"{item['x']:.1f},{item['y_low']:.1f}" for item in positioned)}"></polyline>"""
        mid_line = f"""<polyline class="valuation-mid-line" points="{" ".join(f"{item['x']:.1f},{item['y_mid']:.1f}" for item in positioned)}"></polyline>"""
    else:
        band_svg = ""
        high_line = ""
        low_line = ""
        mid_line = ""

    label_step = max(1, (count + 5) // 6)
    x_labels = []
    markers = []
    for index, item in enumerate(positioned):
        if index % label_step == 0 or index == count - 1:
            x_labels.append(
                f"""<text class="valuation-date-label" x="{item['x']:.1f}" y="{height - 18:.1f}" text-anchor="middle">{esc(short_date(item['date']))}</text>"""
            )
        tooltip = (
            f"{item['date']} | 保守 {fmt_num(item['low'])} | 合理 {fmt_num(item['mid'])} | "
            f"乐观 {fmt_num(item['high'])} | {item['method']} | {item['grade']}"
        )
        markers.append(
            f"""<g class="valuation-point">
          <title>{esc(tooltip)}</title>
          <line class="valuation-whisker" x1="{item['x']:.1f}" y1="{item['y_high']:.1f}" x2="{item['x']:.1f}" y2="{item['y_low']:.1f}"></line>
          <circle class="valuation-mid-dot" cx="{item['x']:.1f}" cy="{item['y_mid']:.1f}" r="4.5"></circle>
        </g>"""
        )

    return f"""<section class="section-block">
      <h2>ETF参考价格区间历史</h2>
      <div class="valuation-chart">
        <svg class="valuation-history-svg" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="ETF参考价格区间随时间变化图">
          <title>ETF参考价格区间随时间变化图</title>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{height - bottom:.1f}"></line>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{height - bottom:.1f}" x2="{width - right:.1f}" y2="{height - bottom:.1f}"></line>
          <text class="valuation-axis-title" x="{left:.1f}" y="16" text-anchor="start">价格 CNY/fund_share</text>
          {''.join(tick_lines)}
          {band_svg}
          {high_line}
          {low_line}
          {mid_line}
          {''.join(markers)}
          {''.join(x_labels)}
        </svg>
        <div class="valuation-legend">
          <span><i class="legend-band"></i>保守-乐观区间</span>
          <span><i class="legend-line"></i>参考价格中枢</span>
          <span><i class="legend-dot"></i>单次完整深研</span>
        </div>
        <p class="chart-note">行情K线待入库，当前仅显示完整深研生成的参考价格区间。</p>
      </div>
    </section>"""


def _price_index_on_or_after(price_dates: list[object], date_value: object) -> int:
    target = _parsed_date(date_value)
    if target is None:
        return 0
    for index, price_date in enumerate(price_dates):
        if price_date is not None and price_date >= target:
            return index
    return max(len(price_dates) - 1, 0)


def _render_kline_valuation_chart(
    valuation_points: list[dict[str, object]],
    price_points: list[dict[str, object]],
) -> str:
    if len(price_points) < 2:
        return _render_plain_valuation_chart(valuation_points)

    width = 760.0
    height = 360.0
    left = 64.0
    right = 24.0
    top = 30.0
    bottom = 58.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    plot_right = width - right
    plot_bottom = height - bottom

    price_lows = [float(item["low"]) for item in price_points]
    price_highs = [float(item["high"]) for item in price_points]
    valuation_lows = [float(item["low"]) for item in valuation_points]
    valuation_highs = [float(item["high"]) for item in valuation_points]
    lower = min(price_lows + valuation_lows)
    upper = max(price_highs + valuation_highs)
    span = upper - lower
    pad = max(span * 0.08, max(abs(upper), 1.0) * 0.02, 1.0)
    y_min = lower - pad
    y_max = upper + pad

    price_dates = [_parsed_date(item["date"]) for item in price_points]
    price_count = len(price_points)
    spacing = plot_width / (price_count - 1)
    candle_width = min(max(spacing * 0.58, 2.4), 7.5)

    tick_lines = []
    for index in range(5):
        value = y_max - (y_max - y_min) * index / 4.0
        y = _chart_y(value, y_min, y_max, top, plot_height)
        tick_lines.append(
            f"""<g>
          <line class="valuation-grid-line" x1="{left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}"></line>
          <text class="valuation-axis-label" x="{left - 10:.1f}" y="{y + 4:.1f}" text-anchor="end">{fmt_num(value)}</text>
        </g>"""
        )

    candles = []
    label_step = max(1, (price_count + 5) // 6)
    x_labels = []
    for index, item in enumerate(price_points):
        x = _chart_x(index, price_count, left, plot_width)
        y_open = _chart_y(float(item["open"]), y_min, y_max, top, plot_height)
        y_close = _chart_y(float(item["close"]), y_min, y_max, top, plot_height)
        y_high = _chart_y(float(item["high"]), y_min, y_max, top, plot_height)
        y_low = _chart_y(float(item["low"]), y_min, y_max, top, plot_height)
        body_top = min(y_open, y_close)
        body_height = max(abs(y_close - y_open), 1.4)
        trend_class = "kline-up" if float(item["close"]) >= float(item["open"]) else "kline-down"
        tooltip = (
            f"{item['date']} | 开 {fmt_num(item['open'])} | 高 {fmt_num(item['high'])} | "
            f"低 {fmt_num(item['low'])} | 收 {fmt_num(item['close'])}"
        )
        candles.append(
            f"""<g class="kline-candle {trend_class}">
          <title>{esc(tooltip)}</title>
          <line class="kline-wick" x1="{x:.1f}" y1="{y_high:.1f}" x2="{x:.1f}" y2="{y_low:.1f}"></line>
          <rect class="kline-body" x="{x - candle_width / 2:.1f}" y="{body_top:.1f}" width="{candle_width:.1f}" height="{body_height:.1f}"></rect>
        </g>"""
        )
        if index % label_step == 0 or index == price_count - 1:
            x_labels.append(
                f"""<text class="valuation-date-label" x="{x:.1f}" y="{height - 18:.1f}" text-anchor="middle">{esc(short_date(item['date']))}</text>"""
            )

    positioned_valuations = []
    for point in valuation_points:
        price_index = _price_index_on_or_after(price_dates, point["date"])
        x = _chart_x(price_index, price_count, left, plot_width)
        positioned_valuations.append(
            {
                **point,
                "price_index": price_index,
                "x": x,
                "y_low": _chart_y(float(point["low"]), y_min, y_max, top, plot_height),
                "y_mid": _chart_y(float(point["mid"]), y_min, y_max, top, plot_height),
                "y_high": _chart_y(float(point["high"]), y_min, y_max, top, plot_height),
            }
        )

    bands = []
    boundary_lines = []
    mid_lines = []
    markers = []
    for index, item in enumerate(positioned_valuations):
        start_x = float(item["x"])
        if index + 1 < len(positioned_valuations):
            end_x = float(positioned_valuations[index + 1]["x"])
            if end_x <= start_x:
                end_x = min(plot_right, start_x + spacing)
        else:
            end_x = plot_right
        width_value = max(end_x - start_x, 2.0)
        band_y = float(item["y_high"])
        band_height = max(float(item["y_low"]) - band_y, 1.0)
        tooltip = (
            f"{item['date']} 起 | 低位 {fmt_num(item['low'])} | 中枢 {fmt_num(item['mid'])} | "
            f"乐观 {fmt_num(item['high'])} | {item['method']} | {item['grade']}"
        )
        bands.append(
            f"""<rect class="valuation-step-band" x="{start_x:.1f}" y="{band_y:.1f}" width="{width_value:.1f}" height="{band_height:.1f}">
          <title>{esc(tooltip)}</title>
        </rect>"""
        )
        boundary_lines.append(
            f"""<line class="valuation-step-boundary-line" x1="{start_x:.1f}" y1="{item['y_high']:.1f}" x2="{end_x:.1f}" y2="{item['y_high']:.1f}"></line>
          <line class="valuation-step-boundary-line" x1="{start_x:.1f}" y1="{item['y_low']:.1f}" x2="{end_x:.1f}" y2="{item['y_low']:.1f}"></line>"""
        )
        mid_lines.append(
            f"""<line class="valuation-mid-line" x1="{start_x:.1f}" y1="{item['y_mid']:.1f}" x2="{end_x:.1f}" y2="{item['y_mid']:.1f}"></line>"""
        )
        markers.append(
            f"""<g class="valuation-point">
          <title>{esc(tooltip)}</title>
          <line class="valuation-whisker" x1="{start_x:.1f}" y1="{item['y_high']:.1f}" x2="{start_x:.1f}" y2="{item['y_low']:.1f}"></line>
          <circle class="valuation-mid-dot" cx="{start_x:.1f}" cy="{item['y_mid']:.1f}" r="4.5"></circle>
        </g>"""
        )

    first_date = price_points[0]["date"]
    last_date = price_points[-1]["date"]
    return f"""<section class="section-block">
      <h2>ETF参考价格区间历史</h2>
      <div class="valuation-chart">
        <svg class="valuation-history-svg" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="K线叠加ETF参考价格区间图">
          <title>K线叠加ETF参考价格区间图</title>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{top:.1f}" x2="{left:.1f}" y2="{plot_bottom:.1f}"></line>
          <line class="valuation-axis-line" x1="{left:.1f}" y1="{plot_bottom:.1f}" x2="{plot_right:.1f}" y2="{plot_bottom:.1f}"></line>
          <text class="valuation-axis-title" x="{left:.1f}" y="16" text-anchor="start">价格 CNY/fund_share</text>
          <text class="valuation-range-label" x="{plot_right:.1f}" y="16" text-anchor="end">{esc(short_date(first_date))} - {esc(short_date(last_date))}</text>
          {''.join(tick_lines)}
          <g class="kline-layer">{''.join(candles)}</g>
          <g class="valuation-overlay-layer">
            {''.join(bands)}
            {''.join(boundary_lines)}
            {''.join(mid_lines)}
            {''.join(markers)}
          </g>
          {''.join(x_labels)}
        </svg>
        <div class="valuation-legend">
          <span><i class="legend-kline"></i>近期价格K线</span>
          <span><i class="legend-band"></i>保守-乐观区间</span>
          <span><i class="legend-line"></i>参考价格中枢</span>
          <span><i class="legend-dot"></i>完整深研点</span>
        </div>
      </div>
    </section>"""


def render_valuation_chart(runs: list[object], prices: list[object] | None = None) -> str:
    points = _valuation_chart_points(runs)
    if not points:
        return render_empty_section("ETF参考价格区间历史")
    price_points = _daily_price_points(prices or [])
    if price_points:
        return _render_kline_valuation_chart(points, price_points)
    return _render_plain_valuation_chart(points)


def render_etf_queue_status(queue: list[object]) -> str:
    if not queue:
        return ""
    rows = "".join(
        f"""<tr>
      <td>{esc(row['task_type'])}</td>
      <td>{esc(queue_source_label(row['source_type']))}</td>
      <td>{esc(row['status'])}</td>
      <td>{esc(row['task_keyword'])}</td>
      <td>{esc(row['updated_at'])}</td>
    </tr>"""
        for row in queue
    )
    return f"""<section class="section-block">
        <h2>研究队列状态</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>类型</th><th>来源</th><th>状态</th><th>任务关键词</th><th>更新时间</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      </section>"""


def signal_item(label: str, value: object, detail: object | None = None) -> str:
    detail_html = f"<small>{esc(detail)}</small>" if detail is not None else ""
    return f"""<div class="signal-item">
      <span>{esc(label)}</span>
      <strong>{esc(value if value is not None else '待入库')}</strong>
      {detail_html}
    </div>"""


def render_signal_matrix(
    upstream_signal: dict[str, object],
    valuation_signal: dict[str, object],
    matrix: dict[str, object],
) -> str:
    upstream_risk_flags = upstream_signal.get("risk_flags")
    if not isinstance(upstream_risk_flags, list):
        upstream_risk_flags = []
    risk_text = "；".join(str(item) for item in upstream_risk_flags) or "暂无上游风险提示"
    valuation_range = valuation_signal.get("valuation_range")
    range_text = "等待ETF估值"
    if isinstance(valuation_range, dict) and valuation_range.get("mid") is not None:
        range_text = (
            f"{fmt_num(valuation_range.get('low'))} / {fmt_num(valuation_range.get('mid'))} / "
            f"{fmt_num(valuation_range.get('high'))}"
        )
    model_type = str(valuation_signal.get("valuation_model_type") or "")
    if model_type == "mainline_theme":
        model_specific_items = (
            signal_item("主线有效性", fmt_num(valuation_signal.get("mainline_validity_score")))
            + signal_item("估值容错", fmt_num(valuation_signal.get("valuation_tolerance_score")))
            + signal_item("拥挤风险", fmt_num(valuation_signal.get("crowding_risk_score")))
        )
    elif model_type == "factor_defensive":
        model_specific_items = signal_item("防御因子溢价", fmt_num(valuation_signal.get("factor_premium_score")))
    elif model_type == "cash_like":
        model_specific_items = signal_item("现金替代安全", fmt_num(valuation_signal.get("cash_like_safety_score")))
    else:
        model_specific_items = signal_item("宽基估值安全", fmt_num(valuation_signal.get("undervalued_score")))
    return f"""<section class="section-block">
        <h2>产品信号与ETF估值适配</h2>
        <div class="signal-matrix">
          <div class="signal-panel signal-panel-upstream">
            <h3>上游主线信号</h3>
            <p class="muted">来自 theme.okbbc.com 主线主题接口，不在本项目重复研究主线。</p>
            <div class="signal-grid">
              {signal_item("所属主题", upstream_signal.get("theme"))}
              {signal_item("主线状态", upstream_signal.get("label"), upstream_signal.get("rating"))}
              {signal_item("主题绑定", fmt_num(upstream_signal.get("theme_binding")))}
              {signal_item("主线强度", fmt_num(upstream_signal.get("leader_score")))}
              {signal_item("证据质量", fmt_num(upstream_signal.get("evidence_quality")))}
              {signal_item("交易结构", fmt_num(upstream_signal.get("trading_structure")))}
            </div>
            <p class="signal-note">主线证据：{esc(upstream_signal.get("leader_claim") or "待入库")}</p>
            <p class="signal-note">上游风险：{esc(risk_text)}</p>
          </div>
          <div class="signal-panel signal-panel-valuation">
            <h3>ETF类型化估值与仓位适配</h3>
            <p class="muted">来自 MyInvestETF 确定性评分；不同 ETF 类型使用不同估值依据。</p>
            <div class="signal-grid">
              {signal_item("估值框架", valuation_signal.get("valuation_model_label"))}
              {signal_item("五仓角色", valuation_signal.get("sleeve_label"))}
              {signal_item("适配状态", valuation_signal.get("label"))}
              {signal_item("参考价格区间", range_text, valuation_signal.get("source"))}
              {model_specific_items}
              {signal_item("流动性", fmt_num(valuation_signal.get("liquidity_score")))}
              {signal_item("跟踪质量", fmt_num(valuation_signal.get("tracking_score")))}
              {signal_item("仓位角色", fmt_num(valuation_signal.get("portfolio_role_score")))}
              {signal_item("风险调整", fmt_num(valuation_signal.get("risk_adjusted_score")))}
            </div>
            <p class="signal-note">ETF模型原始标签：{esc(valuation_signal.get("raw_grade") or "待入库")}</p>
          </div>
          <div class="matrix-conclusion">
            <span>矩阵结论</span>
            <strong>{esc(matrix.get("posture"))}</strong>
            <p>{esc(matrix.get("conclusion"))}</p>
          </div>
        </div>
      </section>"""


def render_trackable_history(rows: list[object]) -> str:
    if not rows:
        return """<section class="section-block">
        <h2>可跟踪ETF历史</h2>
        <p class="empty">尚未在本地记录中被列为 可跟踪ETF。</p>
      </section>"""
    body = "".join(
        f"""<tr>
      <td>{esc(row['basis_date'] or short_date(row['generated_at'] or row['fetched_at']))}</td>
      <td>{esc(row['deep_rating'] or '')} {esc(row['deep_label'] or '')}</td>
      <td>{fmt_num(row['deep_score'])}</td>
      <td>{esc(row['theme'] or '待入库')}</td>
      <td>{esc(row['candidate_leader_claim'] or '待入库')}</td>
      <td>{esc(row['report_id'])}</td>
    </tr>"""
        for row in rows
    )
    return f"""<section class="section-block">
        <h2>可跟踪ETF历史</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>日期</th><th>评级</th><th>深研分</th><th>主题</th><th>龙头证据</th><th>report_id</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
      </section>"""


def _first_queue_name(queue: list[object], code: str) -> str:
    if queue:
        return str(queue[0]["name"] or code)
    return code


def _etf_exists(conn: object, code: str) -> tuple[bool, str | None]:
    leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
    if leader is not None:
        return True, str(leader["name"])
    runs = list_research_runs(conn, code)
    if runs:
        return True, str(runs[0]["name"])
    queue = list_queue_for_etf(conn, code)
    if queue:
        return True, str(queue[0]["name"])
    return False, None


def normalize_etf_query(params: dict[str, list[str]]) -> tuple[str | None, str | None]:
    etf = (params.get("etf") or params.get("code") or [""])[0].strip().upper()
    name = (params.get("name") or [""])[0].strip()
    return (etf or None), (name or None)


def render_etf_page(code: str) -> bytes:
    if not ETF_CODE_RE.match(code):
        return render_layout("无效代码", "<section class=\"content\"><h1>无效ETF代码</h1></section>")
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = list_research_runs(conn, code)
        etf_queue = list_queue_for_etf(conn, code)
        trackable_history = list_trackable_history(conn, code)
        chart_runs = valuation_runs(conn, code)
        price_start = _valuation_price_start(chart_runs)
        chart_prices = list_daily_prices(conn, code, start_date=price_start, limit=260) if price_start else []
        report = latest_report(conn)
    if leader is None and not runs and not etf_queue:
        return render_layout(
            "未找到",
            f"""<section class="content">
        <div class="section-block">
          <h1>未找到 {esc(code)}</h1>
          <p class="muted">可以通过 <a class="text-link" href="/research?etf={esc(code)}">加入ETF深研队列</a> 生成研究页面。</p>
        </div>
      </section>""",
        )

    market = load_json(leader["market_json"], {}) if leader is not None else {}
    scores = load_json(leader["scores_json"], {}) if leader is not None else {}
    risk_flags = load_json(leader["risk_flags_json"], []) if leader is not None else []
    latest = next((dict(row) for row in runs if row["task_type"] == "research"), dict(runs[0]) if runs else {})
    risks = load_json(latest.get("risks_json"), []) if latest else []
    risk_items = "".join(f"<li>{esc(item)}</li>" for item in (risks or risk_flags or []))
    etf_name = (
        str(leader["name"])
        if leader is not None
        else str((latest or {}).get("name") or _first_queue_name(etf_queue, code))
    )
    etf_theme = leader["theme"] if leader is not None else "其他请求"
    etf_claim = leader["candidate_leader_claim"] if leader is not None else "主动研究请求"
    xueqiu_url = leader["xueqiu_url"] if leader is not None else None
    model_info = leader_model_info(leader)
    upstream_signal = upstream_signal_summary(leader)
    valuation_signal = valuation_signal_summary(latest if latest else None)
    if valuation_signal.get("valuation_model_type") is None:
        valuation_signal.update(model_info)
    decision_matrix = decision_matrix_summary(upstream_signal, valuation_signal)
    current_price = _display_current_price(latest if latest else None, chart_prices, market)
    rating_label = (
        f"{leader['deep_rating'] or ''} {leader['deep_label'] or ''}".strip()
        if leader is not None
        else (queue_source_label(etf_queue[0]["source_type"]) if etf_queue else "待研究")
    )
    report_date = report["basis_date"] if report else ""
    queue_status_section = render_etf_queue_status(etf_queue)
    signal_matrix_section = render_signal_matrix(upstream_signal, valuation_signal, decision_matrix)
    trackable_history_section = render_trackable_history(trackable_history)

    history_rows = "".join(
        f"""<tr>
      <td>{esc(row['research_date'])}</td>
      <td>{esc(row['task_type'])}</td>
      <td>{esc(row['status'])}</td>
      <td>{esc(row['valuation_method'] or '待入库')}</td>
      <td>{fmt_num(row['valuation_low'])} / {fmt_num(row['valuation_mid'])} / {fmt_num(row['valuation_high'])}</td>
      <td>{esc(row['heavy_position_view'] or '待入库')}</td>
    </tr>"""
        for row in runs
    )
    if not history_rows:
        history_rows = "<tr><td colspan=\"6\" class=\"empty-cell\">等待ETF深研入库。</td></tr>"

    body = f"""
    <section class="page-band">
      <div class="content">
        <div class="page-title-row">
          <div>
            <h1>{esc(etf_name)}</h1>
            <p class="muted">{xueqiu_etf_link(code, xueqiu_url)} · {esc(etf_theme)} · {esc(etf_claim)}</p>
          </div>
          <div class="report-box">
            <span>研究来源</span>
            <strong>{esc(rating_label)}</strong>
            <span>{esc(report_date)}</span>
          </div>
        </div>
        <div class="summary-grid">
          {metric("深研分", leader["deep_score"] if leader is not None else None)}
          {metric("当前价格", current_price)}
          {metric("PE TTM", market.get("pe_ttm"))}
          {metric("PB", market.get("pb"))}
          {metric("估值框架", model_info.get("valuation_model_label"))}
          {metric("五仓角色", model_info.get("sleeve_label"))}
          {metric("证据质量", scores.get("evidence_quality"))}
          {metric("估值安全", scores.get("valuation_safety"))}
        </div>
      </div>
    </section>
    <section class="content">
      {queue_status_section}
      {signal_matrix_section}
      {render_valuation_chart(chart_runs, chart_prices)}
      {trackable_history_section}
      <section class="two-col">
        <div class="section-block">
          <h2>产品结构</h2>
          <p>{esc(latest.get('industry_position') or '等待ETF完整深研入库。')}</p>
        </div>
        <div class="section-block">
          <h2>持仓披露</h2>
          <p>{esc(latest.get('competition_landscape') or '等待ETF完整深研入库。')}</p>
        </div>
      </section>
      <section class="two-col">
        <div class="section-block">
          <h2>前十大持仓</h2>
          <p>{esc(latest.get('upstream_downstream') or '等待ETF完整深研入库。')}</p>
        </div>
        <div class="section-block">
          <h2>估值与流动性</h2>
          <p>{esc(latest.get('annual_growth') or '等待ETF完整深研入库。')}</p>
        </div>
      </section>
      <section class="two-col">
        <div class="section-block">
          <h2>组合角色</h2>
          <p>{esc(latest.get('multi_bagger_potential') or '等待ETF完整深研入库。')}</p>
        </div>
        <div class="section-block">
          <h2>底仓/工具仓资格</h2>
          <p>{esc(decision_matrix.get('conclusion') or latest.get('heavy_position_view') or '等待ETF完整深研入库。')}</p>
        </div>
      </section>
      <section class="section-block">
        <h2>风险与证伪</h2>
        <ul class="risk-list">{risk_items or '<li>等待ETF深研入库。</li>'}</ul>
      </section>
      <section class="section-block">
        <h2>研究历史</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>日期</th><th>类型</th><th>状态</th><th>估值方法</th><th>参考价格低 / 中枢 / 高</th><th>底仓资格</th></tr></thead>
            <tbody>{history_rows}</tbody>
          </table>
        </div>
      </section>
    </section>
"""
    return render_layout(f"{etf_name} {code}", body)


def api_etfs() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
    return json.dumps(
        {"report": dict(report) if report else None, "items": [leader_to_summary(row) for row in leaders]},
        ensure_ascii=False,
    ).encode("utf-8")


def api_index() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
    items = [leader_to_summary(row) for row in leaders]
    payload = {
        "schema_version": "myinvestetf.index.v1",
        "page": {
            "title": "MyInvestETF",
            "primary_endpoint": "/api/index",
            "latest_endpoint": "/api/latest",
            "primary_result_path": "key_results.primary_output.items",
        },
        "source": {
            "upstream_endpoint": LEADER_INDEX_URL,
            "upstream_result_path": "result.theme_ranking[].top_etf + result.etf_top",
            "compatible_result_path": "key_results.primary_output.items",
            "source_policy": (
                "default to theme.okbbc.com/api/latest theme_ranking[].top_etf plus result.etf_top; "
                "/api/index keeps the ETF pool and appends local core broad-index ETF seeds; "
                "research queue lists independent core broad-index representatives first, then one representative per mainline theme"
            ),
        },
        "report": dict(report) if report else None,
        "key_results": {
            "primary_output": {
                "title": "可跟踪ETF",
                "count": len(items),
                "items": items,
            }
        },
        "links": {
            "web": "/",
            "latest": "/api/latest",
            "queue": "/api/queue",
            "etfs": "/api/etfs",
        },
        "constraints": {
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_latest() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        report = latest_report(conn)
        leaders = list_latest_leaders(conn)
        etfs = []
        research_run_count = 0
        complete_research_count = 0
        for leader in leaders:
            runs = list_research_runs(conn, leader["code"])
            reference_runs_for_etf = valuation_runs(conn, leader["code"])
            research_run_count += len(runs)
            complete_research_count += len(reference_runs_for_etf)
            latest = latest_research_run(runs)
            leader_summary = leader_to_summary(leader)
            decision_matrix = decision_matrix_summary(
                leader_summary["upstream_signal"],
                latest["valuation_signal"] if latest else valuation_signal_summary(None),
            )
            etfs.append(
                {
                    "leader": leader_summary,
                    "research": {
                        "latest": latest,
                        "reference_value_history": valuation_history_payload(reference_runs_for_etf),
                        "run_count": len(runs),
                    },
                    "decision_matrix": decision_matrix,
                }
            )
    payload = {
        "schema_version": "myinvestetf.research.v2",
        "report": dict(report) if report else None,
        "summary": {
            "etf_count": len(etfs),
            "research_run_count": research_run_count,
            "complete_research_count": complete_research_count,
        },
        "etfs": etfs,
        "constraints": {
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
        },
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def api_etf(code: str) -> bytes:
    with closing(connect(DB_PATH)) as conn:
        leader = get_latest_leader(conn, code) or get_known_leader(conn, code)
        runs = rows_to_dicts(list_research_runs(conn, code))
        queue = rows_to_dicts(list_queue_for_etf(conn, code))
        trackable = rows_to_dicts(list_trackable_history(conn, code))
    for row in queue:
        row["source_label"] = queue_source_label(row.get("source_type"))
    leader_summary = leader_to_summary(leader) if leader else None
    latest = next((row for row in runs if row.get("task_type") == "research"), runs[0] if runs else None)
    decision_matrix = decision_matrix_summary(
        leader_summary["upstream_signal"] if leader_summary else upstream_signal_summary(None),
        valuation_signal_summary(latest) if latest else valuation_signal_summary(None),
    )
    return json.dumps(
        {
            "leader": dict(leader) if leader else None,
            "leader_summary": leader_summary,
            "upstream_signal": leader_summary["upstream_signal"] if leader_summary else upstream_signal_summary(None),
            "research_runs": runs,
            "decision_matrix": decision_matrix,
            "queue": queue,
            "trackable_history": trackable,
        },
        ensure_ascii=False,
    ).encode("utf-8")


def api_queue() -> bytes:
    with closing(connect(DB_PATH)) as conn:
        rows = rows_to_dicts(list_queue(conn))
    for row in rows:
        row["source_label"] = queue_source_label(row.get("source_type"))
    return json.dumps({"items": rows}, ensure_ascii=False).encode("utf-8")


class MyInvestETFHandler(BaseHTTPRequestHandler):
    server_version = "MyInvestETF/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self.send_bytes(render_home(), "text/html; charset=utf-8")
            return
        if path == "/research":
            self.handle_research_gateway(parsed.query)
            return
        if path == "/api/index":
            self.send_bytes(api_index(), "application/json; charset=utf-8")
            return
        if path == "/api/latest":
            self.send_bytes(api_latest(), "application/json; charset=utf-8")
            return
        if path == "/api/etfs":
            self.send_bytes(api_etfs(), "application/json; charset=utf-8")
            return
        if path == "/api/queue":
            self.send_bytes(api_queue(), "application/json; charset=utf-8")
            return
        if path.startswith("/api/etfs/"):
            code = path.removeprefix("/api/etfs/").upper()
            self.send_bytes(api_etf(code), "application/json; charset=utf-8")
            return
        if path.startswith("/etfs/"):
            code = path.removeprefix("/etfs/").upper()
            self.send_bytes(render_etf_page(code), "text/html; charset=utf-8")
            return
        if path.startswith("/static/"):
            self.send_static(path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_research_gateway(self, query: str) -> None:
        code, requested_name = normalize_etf_query(parse_qs(query))
        if code is None or not ETF_CODE_RE.match(code):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid etf code")
            return
        with closing(connect(DB_PATH)) as conn:
            exists, known_name = _etf_exists(conn, code)
        queued = False
        if not exists:
            enqueue_requested_etf(code, name=requested_name or known_name or code)
            queued = True
        location = f"/etfs/{quote(code)}"
        if queued:
            location += "?queued=1"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, relative_path: str) -> None:
        safe = Path(relative_path)
        if safe.is_absolute() or ".." in safe.parts:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        path = ROOT / "web" / "static" / safe
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_bytes(path.read_bytes(), content_type)

    def log_message(self, format: str, *args: object) -> None:
        return


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    httpd = ThreadingHTTPServer((host, port), MyInvestETFHandler)
    print(f"MyInvestETF Web running at http://{host}:{port}/", flush=True)
    httpd.serve_forever()
