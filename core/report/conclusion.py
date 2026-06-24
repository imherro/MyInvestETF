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


def build_conclusion(signal: ETFValuationSignal) -> ConclusionRuleResult:
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

    summary = (
        f"确定性规则评分：估值位置 {undervalued:.1f}，流动性 {liquidity:.1f}，"
        f"跟踪质量 {tracking:.1f}，组合角色 {role:.1f}，风险调整 {risk_adjusted:.1f}。"
    )
    return ConclusionRuleResult(grade=grade, confidence=_confidence(risk_adjusted), summary=summary)
