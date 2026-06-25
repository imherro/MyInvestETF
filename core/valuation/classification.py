from __future__ import annotations

from collections.abc import Mapping
from typing import Literal


ValuationModelType = Literal["broad_index", "mainline_theme", "factor_defensive", "cash_like"]
SleeveKey = Literal["core_wide_etf", "mainline_etf", "defensive_quality", "cash_like"]


VALUATION_MODEL_TYPES: tuple[str, ...] = ("broad_index", "mainline_theme", "factor_defensive", "cash_like")
SLEEVE_KEYS: tuple[str, ...] = ("core_wide_etf", "mainline_etf", "defensive_quality", "cash_like")


def _compact_text(*values: object) -> str:
    text = " ".join(str(value or "") for value in values)
    return text.replace("（", "(").replace("）", ")").replace(" ", "").lower()


def infer_valuation_model_type(item: Mapping[str, object] | None = None, **hints: object) -> ValuationModelType:
    source = dict(item or {})
    source.update({key: value for key, value in hints.items() if value is not None})
    text = _compact_text(
        source.get("code"),
        source.get("name"),
        source.get("theme"),
        source.get("asset_class"),
        source.get("portfolio_role"),
        source.get("tracking_index"),
        source.get("category"),
        source.get("category_key"),
    )

    if any(word in text for word in ("短融", "货币", "现金", "日利", "添利", "快线", "保证金", "国债逆回购")):
        return "cash_like"
    if any(word in text for word in ("红利", "低波", "自由现金流", "现金流", "质量", "高股息", "股息")):
        return "factor_defensive"
    if any(
        word in text
        for word in (
            "上证综指",
            "上证指数",
            "沪深300",
            "中证a500",
            "中证500",
            "中证1000",
            "上证50",
            "创业板",
            "科创板50",
            "科创50",
            "科创板100",
            "科创100",
            "宽基",
        )
    ):
        return "broad_index"
    return "mainline_theme"


def sleeve_for_valuation_model(model_type: str) -> SleeveKey:
    if model_type == "broad_index":
        return "core_wide_etf"
    if model_type == "factor_defensive":
        return "defensive_quality"
    if model_type == "cash_like":
        return "cash_like"
    return "mainline_etf"


def normalize_valuation_model_type(value: object, item: Mapping[str, object] | None = None) -> ValuationModelType:
    text = str(value or "").strip()
    if text in VALUATION_MODEL_TYPES:
        return text  # type: ignore[return-value]
    return infer_valuation_model_type(item)


def normalize_sleeve_key(value: object, model_type: str) -> SleeveKey:
    text = str(value or "").strip()
    if text in SLEEVE_KEYS:
        return text  # type: ignore[return-value]
    return sleeve_for_valuation_model(model_type)
