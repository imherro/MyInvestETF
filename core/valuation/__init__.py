"""Deterministic ETF valuation and suitability scoring."""

from .features import ETFFeatures, extract_etf_features
from .classification import (
    SLEEVE_KEYS,
    VALUATION_MODEL_TYPES,
    SleeveKey,
    ValuationModelType,
    infer_valuation_model_type,
    normalize_sleeve_key,
    normalize_valuation_model_type,
    sleeve_for_valuation_model,
)
from .models import ETFReferenceValueRange, reference_range_from_inputs, reference_range_from_nav
from .signal import ETFValuationSignal, build_etf_signal

__all__ = [
    "ETFFeatures",
    "ETFReferenceValueRange",
    "ETFValuationSignal",
    "SLEEVE_KEYS",
    "SleeveKey",
    "ValuationModelType",
    "VALUATION_MODEL_TYPES",
    "build_etf_signal",
    "extract_etf_features",
    "infer_valuation_model_type",
    "normalize_sleeve_key",
    "normalize_valuation_model_type",
    "reference_range_from_inputs",
    "reference_range_from_nav",
    "sleeve_for_valuation_model",
]
