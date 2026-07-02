"""Regime-aware ETF decision scoring layer."""

from .engine import (
    COMPONENT_TYPES,
    BASE_WEIGHT_MATRIX,
    REGIME_FACTOR_EFFECTIVENESS,
    TAXONOMY_WEIGHT_ADJUSTMENTS,
    DecisionSignal,
    DecisionState,
    build_decision_signal,
    decision_signal_to_dict,
)

__all__ = [
    "BASE_WEIGHT_MATRIX",
    "COMPONENT_TYPES",
    "DecisionSignal",
    "DecisionState",
    "REGIME_FACTOR_EFFECTIVENESS",
    "TAXONOMY_WEIGHT_ADJUSTMENTS",
    "build_decision_signal",
    "decision_signal_to_dict",
]
