from __future__ import annotations

from dataclasses import dataclass

from core.valuation import ETFValuationSignal


@dataclass(frozen=True)
class ConclusionRuleResult:
    grade: str
    confidence: float
    summary: str


def _score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _confidence(value: float) -> float:
    return round(_score(value) / 100.0, 4)


def build_conclusion(signal: ETFValuationSignal, *, model_type: str = "broad_index") -> ConclusionRuleResult:
    undervalued = _score(signal.undervalued_score)
    liquidity = _score(signal.liquidity_score)
    tracking = _score(signal.tracking_score)
    role = _score(signal.portfolio_role_score)
    risk_adjusted = _score(signal.risk_adjusted_score)

    if undervalued >= 65.0 and liquidity >= 65.0 and tracking >= 70.0 and role >= 65.0:
        grade = "底仓候选"
    elif liquidity >= 55.0 and tracking >= 60.0 and risk_adjusted >= 55.0:
        grade = "工具仓可用"
    elif undervalued <= 25.0 or risk_adjusted < 35.0:
        grade = "估值或拥挤暂缓"
    elif risk_adjusted >= 40.0:
        grade = "观察"
    else:
        grade = "不适合底仓"

    if model_type == "mainline_theme":
        lead = f"主线有效性 {signal.mainline_validity_score:.1f}，估值容错 {signal.valuation_tolerance_score:.1f}，拥挤风险 {signal.crowding_risk_score:.1f}"
    elif model_type == "factor_defensive":
        lead = f"防御因子溢价 {signal.factor_premium_score:.1f}，风格机会成本已纳入约束"
    elif model_type == "cash_like":
        lead = f"现金替代安全性 {signal.cash_like_safety_score:.1f}"
    else:
        lead = f"宽基估值安全 {undervalued:.1f}"
    summary = f"确定性规则评分：{lead}，流动性 {liquidity:.1f}，跟踪质量 {tracking:.1f}，组合角色 {role:.1f}，风险调整 {risk_adjusted:.1f}。"
    return ConclusionRuleResult(grade=grade, confidence=_confidence(risk_adjusted), summary=summary)
