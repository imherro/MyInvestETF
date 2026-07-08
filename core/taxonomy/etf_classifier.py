from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from core.valuation import normalize_valuation_model_type, sleeve_for_valuation_model

from .etf_types import ETFType, ThemeLifecycleStage


@dataclass(frozen=True)
class TaxonomyProfile:
    etf_type: ETFType
    subtype: str
    lifecycle_stage: ThemeLifecycleStage | None
    classification_confidence: float
    classification_reasons: list[str]
    legacy_valuation_model_type: str
    legacy_sleeve_key: str


def _compact_text(*values: object) -> str:
    text = " ".join(str(value or "") for value in values)
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text).lower()


def _safe_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _source(item: Mapping[str, object]) -> dict[str, object]:
    product = item.get("product_profile")
    product_map = product if isinstance(product, Mapping) else {}
    scores = item.get("scores")
    scores_map = scores if isinstance(scores, Mapping) else {}
    market = item.get("market")
    market_map = market if isinstance(market, Mapping) else {}
    return {
        **dict(item),
        "tracking_index": item.get("tracking_index") or product_map.get("tracking_index"),
        "asset_class": item.get("asset_class") or product_map.get("asset_class"),
        "portfolio_role": item.get("portfolio_role") or product_map.get("portfolio_role"),
        "score": item.get("score") or item.get("deep_score") or scores_map.get("mainline_strength"),
        "r20": item.get("r20") or market_map.get("r20"),
    }


def _contains(text: str, *keywords: str) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _factor_strategy_subtype(text: str) -> str | None:
    if _contains(text, "自由现金流", "现金流"):
        return "free_cash_flow"
    if _contains(text, "红利", "低波", "高股息", "股息"):
        return "dividend_low_vol"
    if _contains(text, "质量因子", "质量策略", "质量etf", "高质量"):
        return "quality_factor"
    return None


def _confidence(base: float, reason_count: int) -> float:
    return round(max(0.0, min(1.0, base + min(0.10, reason_count * 0.02))), 6)


def _theme_lifecycle(source: Mapping[str, object], text: str) -> ThemeLifecycleStage:
    risk_text = _compact_text(*(source.get("risk_flags") or [])) if isinstance(source.get("risk_flags"), list) else ""
    score = _safe_float(source.get("score")) or 0.0
    r20 = _safe_float(source.get("r20")) or 0.0
    if _contains(text + risk_text, "崩塌", "退潮", "collapse"):
        return "collapse"
    if _contains(text + risk_text, "派发", "分歧", "退坡", "distribution"):
        return "distribution"
    if _contains(text + risk_text, "拥挤", "crowded") or score >= 95.0:
        return "crowded"
    if score >= 80.0 or r20 > 8.0:
        return "expansion"
    return "early"


class ETFClassifier:
    def classify(self, etf_meta: Mapping[str, object] | None) -> TaxonomyProfile:
        source = _source(etf_meta or {})
        text = _compact_text(
            source.get("code"),
            source.get("name"),
            source.get("fund_name"),
            source.get("theme"),
            source.get("themes"),
            source.get("category_key"),
            source.get("tracking_index"),
            source.get("asset_class"),
            source.get("portfolio_role"),
            source.get("source_path"),
        )
        reasons: list[str] = []
        legacy_model = normalize_valuation_model_type(source.get("valuation_model_type"), source)

        def result(etf_type: ETFType, subtype: str, base_confidence: float) -> TaxonomyProfile:
            lifecycle = _theme_lifecycle(source, text) if etf_type == "theme_lifecycle" else None
            return TaxonomyProfile(
                etf_type=etf_type,
                subtype=subtype,
                lifecycle_stage=lifecycle,
                classification_confidence=_confidence(base_confidence, len(reasons)),
                classification_reasons=list(reasons),
                legacy_valuation_model_type=legacy_model,
                legacy_sleeve_key=sleeve_for_valuation_model(legacy_model),
            )

        factor_subtype = _factor_strategy_subtype(text)
        if factor_subtype is not None:
            reasons.append("keyword:factor or defensive strategy")
            return result("factor_strategy", factor_subtype, 0.88)

        if _contains(text, "短融", "日利", "货币", "现金", "添利", "快线", "保证金", "逆回购"):
            reasons.append("keyword:cash-like instrument")
            return result("cash_equivalent", "cash_like", 0.90)

        if _contains(text, "黄金", "白银", "原油", "商品", "贵金属"):
            reasons.append("keyword:commodity exposure")
            return result("commodity_etf", "commodity_proxy", 0.86)

        if _contains(text, "债", "国债", "政金债", "信用债", "可转债", "公司债"):
            reasons.append("keyword:bond exposure")
            return result("bond_etf", "bond_duration_based", 0.84)

        if legacy_model == "mainline_theme" and _contains(text, "主线", "主题", "行业主题", "theme_ranking"):
            reasons.append("legacy:model mainline theme route")
            return result("theme_lifecycle", "mainline_theme", 0.76)

        if _contains(text, "创业板", "创业50", "科创50", "科创板50", "科创100", "科创板100", "中证1000", "成长宽基"):
            reasons.append("keyword:growth broad index")
            return result("broad_index_growth", "growth_beta", 0.86)

        if _contains(text, "价值", "央企", "国企", "上证50", "低估值"):
            reasons.append("keyword:value broad index")
            return result("broad_index_value", "value_beta", 0.84)

        if _contains(text, "上证综指", "上证指数", "沪深300", "中证a500", "中证500", "宽基"):
            reasons.append("keyword:core broad index")
            return result("broad_index_core", "core_beta", 0.88)

        if _contains(text, "煤炭", "石油", "能源", "有色", "稀土", "钢铁", "化工", "证券", "券商", "银行", "保险", "地产"):
            reasons.append("keyword:cyclical sector")
            return result("sector_cyclical", "cyclical_sector_beta", 0.82)

        if _contains(text, "半导体", "芯片", "集成电路", "人工智能", "ai", "算力", "通信", "机器人", "新能源", "电池", "光伏", "储能", "医药", "医疗", "创新药", "军工", "消费电子"):
            reasons.append("keyword:structural sector")
            if _contains(text, "主线", "theme_ranking", "主题"):
                reasons.append("source:theme lifecycle candidate")
                return result("theme_lifecycle", "structural_theme", 0.80)
            return result("sector_structural", "structural_sector_beta", 0.80)

        if _contains(text, "主线", "主题", "theme_ranking") or source.get("source_path") == "result.theme_ranking.top_etf":
            reasons.append("source:theme lifecycle route")
            return result("theme_lifecycle", "mainline_theme", 0.74)

        reasons.append("fallback:legacy valuation model route")
        legacy_model = normalize_valuation_model_type(source.get("valuation_model_type"), source)
        if legacy_model == "broad_index":
            return result("broad_index_core", "core_beta", 0.60)
        if legacy_model == "factor_defensive":
            return result("factor_strategy", "defensive_factor", 0.60)
        if legacy_model == "cash_like":
            return result("cash_equivalent", "cash_like", 0.60)
        return result("theme_lifecycle", "unclassified_theme", 0.55)


def classify_etf(etf_meta: Mapping[str, object] | None) -> TaxonomyProfile:
    return ETFClassifier().classify(etf_meta)


def taxonomy_profile_to_dict(profile: TaxonomyProfile) -> dict[str, Any]:
    return asdict(profile)
