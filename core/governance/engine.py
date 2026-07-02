from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from math import log2
from statistics import mean
from typing import Any

from core.risk import normalize_price_series


@dataclass(frozen=True)
class DataQualityReport:
    completeness_score: float
    missing_data_ratio: float
    stale_data_ratio: float
    alignment_score: float
    coverage_score: float
    missing_fields: list[str]
    stale_items: list[str]
    observations: int
    gate_status: str


@dataclass(frozen=True)
class FactorQualityReport:
    ic_validity_score: float
    factor_coverage_score: float
    unstable_factors: list[str]
    redundant_factors: list[str]
    ic_decay_alerts: list[str]
    factor_count: int
    gate_status: str


@dataclass(frozen=True)
class RegimeQualityReport:
    stability_score: float
    flip_rate: float | None
    smoothed_flip_rate: float | None
    regime_entropy: float
    confirmation_score: float
    overfit_warning: bool
    regime_duration_distribution: list[dict[str, object]]
    gate_status: str


@dataclass(frozen=True)
class ReportQualityReport:
    completeness: float
    consistency: float
    leakage_risk: float
    interpretability: float
    rejection_reasons: list[str]
    gate_status: str


@dataclass(frozen=True)
class ResearchHealthReport:
    data_quality: DataQualityReport
    factor_quality: FactorQualityReport
    regime_quality: RegimeQualityReport
    report_quality: ReportQualityReport
    system_health_score: float
    gate_status: str
    constraints: dict[str, bool]


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _safe_float(value: object, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _date_key(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits or str(value or "")


def _gate_status(score: float, *, reject_below: float = 50.0, warn_below: float = 70.0) -> str:
    if score < reject_below:
        return "reject"
    if score < warn_below:
        return "warn"
    return "pass"


def build_data_quality_report(
    price_series_by_code: Mapping[str, Sequence[object]],
    *,
    min_observations: int = 45,
) -> DataQualityReport:
    total = len(price_series_by_code)
    if total == 0:
        return DataQualityReport(
            completeness_score=0.0,
            missing_data_ratio=1.0,
            stale_data_ratio=1.0,
            alignment_score=0.0,
            coverage_score=0.0,
            missing_fields=["price_universe"],
            stale_items=[],
            observations=0,
            gate_status="reject",
        )

    normalized = {code: normalize_price_series(rows) for code, rows in price_series_by_code.items()}
    latest_dates = {code: points[-1].trade_date for code, points in normalized.items() if points}
    max_date = max(latest_dates.values(), key=_date_key) if latest_dates else None
    missing_fields: list[str] = []
    stale_items: list[str] = []
    enough_count = 0
    aligned_count = 0
    for code, points in normalized.items():
        if not points:
            missing_fields.append(f"{code}: missing price series")
            continue
        if len(points) >= min_observations:
            enough_count += 1
        else:
            missing_fields.append(f"{code}: price observations below {min_observations}")
        if max_date and _date_key(points[-1].trade_date) == _date_key(max_date):
            aligned_count += 1
        else:
            stale_items.append(f"{code}: latest {points[-1].trade_date}, expected {max_date}")

    missing_ratio = len(missing_fields) / total
    stale_ratio = len(stale_items) / total
    coverage = enough_count / total
    alignment = aligned_count / total
    completeness = _clamp((1.0 - missing_ratio) * 35.0 + (1.0 - stale_ratio) * 25.0 + coverage * 25.0 + alignment * 15.0)
    return DataQualityReport(
        completeness_score=round(completeness, 6),
        missing_data_ratio=round(missing_ratio, 6),
        stale_data_ratio=round(stale_ratio, 6),
        alignment_score=round(alignment * 100.0, 6),
        coverage_score=round(coverage * 100.0, 6),
        missing_fields=missing_fields,
        stale_items=stale_items,
        observations=total,
        gate_status=_gate_status(completeness),
    )


def _ic_score(summary: Mapping[str, object]) -> float:
    ic_mean = abs(_safe_float(summary.get("ic_mean"), 0.0) or 0.0)
    observations = _safe_float(summary.get("observations"), 0.0) or 0.0
    observation_penalty = min(1.0, observations / 60.0)
    return min(100.0, ic_mean * 800.0) * observation_penalty


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    if x_var <= 0 or y_var <= 0:
        return None
    return numerator / (x_var**0.5 * y_var**0.5)


def _redundant_factor_pairs(factor_exposures_by_code: Mapping[str, Mapping[str, object]]) -> list[str]:
    values_by_factor: dict[str, dict[str, float]] = {}
    for code, exposure in factor_exposures_by_code.items():
        factors = exposure.get("factors")
        if not isinstance(factors, list):
            continue
        for row in factors:
            if not isinstance(row, Mapping):
                continue
            name = str(row.get("factor_name") or "")
            value = _safe_float(row.get("normalized_value"))
            if name and value is not None:
                values_by_factor.setdefault(name, {})[code] = value
    names = sorted(values_by_factor)
    redundant: list[str] = []
    for left_index, left in enumerate(names):
        for right in names[left_index + 1 :]:
            common = sorted(set(values_by_factor[left]).intersection(values_by_factor[right]))
            if len(common) < 3:
                continue
            corr = _pearson([values_by_factor[left][code] for code in common], [values_by_factor[right][code] for code in common])
            if corr is not None and abs(corr) >= 0.92:
                redundant.append(f"{left}~{right}: corr={corr:.3f}")
    return redundant


def build_factor_quality_report(
    ic_summaries_by_factor: Mapping[str, Sequence[Mapping[str, object]]],
    factor_exposures_by_code: Mapping[str, Mapping[str, object]],
) -> FactorQualityReport:
    factor_scores: list[float] = []
    unstable: list[str] = []
    decay_alerts: list[str] = []
    for factor_name, summaries in ic_summaries_by_factor.items():
        if not summaries:
            unstable.append(f"{factor_name}: missing IC summaries")
            continue
        scores = [_ic_score(summary) for summary in summaries]
        factor_scores.append(mean(scores))
        by_horizon = {int(summary.get("horizon_days", 0) or 0): summary for summary in summaries}
        h5 = by_horizon.get(5)
        h60 = by_horizon.get(60)
        if h5 and h60:
            ic5 = abs(_safe_float(h5.get("ic_mean"), 0.0) or 0.0)
            ic60 = abs(_safe_float(h60.get("ic_mean"), 0.0) or 0.0)
            if ic5 > 0.02 and ic60 < ic5 * 0.35:
                decay_alerts.append(f"{factor_name}: 60d IC decays below 35% of 5d IC")
        for summary in summaries:
            ic_mean = abs(_safe_float(summary.get("ic_mean"), 0.0) or 0.0)
            ic_std = _safe_float(summary.get("ic_std"), 0.0) or 0.0
            observations = _safe_float(summary.get("observations"), 0.0) or 0.0
            if observations < 30 or (ic_mean > 0 and ic_std > ic_mean * 3.0):
                unstable.append(f"{factor_name}: unstable horizon {summary.get('horizon_days')}")
                break
    coverage_total = len(factor_exposures_by_code)
    covered = sum(1 for exposure in factor_exposures_by_code.values() if isinstance(exposure.get("factors"), list) and exposure.get("factors"))
    coverage_score = (covered / coverage_total * 100.0) if coverage_total else 0.0
    ic_validity = mean(factor_scores) if factor_scores else 0.0
    redundant = _redundant_factor_pairs(factor_exposures_by_code)
    penalty = min(25.0, len(unstable) * 4.0 + len(decay_alerts) * 3.0 + len(redundant) * 3.0)
    final_score = _clamp(ic_validity * 0.70 + coverage_score * 0.30 - penalty)
    return FactorQualityReport(
        ic_validity_score=round(final_score, 6),
        factor_coverage_score=round(coverage_score, 6),
        unstable_factors=unstable,
        redundant_factors=redundant,
        ic_decay_alerts=decay_alerts,
        factor_count=len(ic_summaries_by_factor),
        gate_status=_gate_status(final_score, reject_below=35.0, warn_below=60.0),
    )


def _entropy(values: Sequence[str]) -> float:
    if not values:
        return 0.0
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    total = len(values)
    if len(counts) <= 1:
        return 0.0
    raw = -sum((count / total) * log2(count / total) for count in counts.values())
    return raw / log2(len(counts))


def build_regime_quality_report(
    replay_stability: Mapping[str, object],
    current_regimes: Sequence[Mapping[str, object]],
) -> RegimeQualityReport:
    flip_rate = _safe_float(replay_stability.get("regime_flip_rate"))
    smoothed_flip_rate = round((flip_rate or 0.0) * 0.65, 6) if flip_rate is not None else None
    duration_distribution = replay_stability.get("regime_duration_distribution")
    durations = duration_distribution if isinstance(duration_distribution, list) else []
    current_values = [str(item.get("regime") or "") for item in current_regimes if item.get("regime")]
    entropy = _entropy(current_values)
    level_map = {"strong": 1.0, "medium": 0.65, "weak": 0.35}
    confirmation_values = [
        level_map.get(str(item.get("confirmation_level") or ""), 0.50)
        for item in current_regimes
    ]
    confirmation_score = mean(confirmation_values) * 100.0 if confirmation_values else 0.0
    flip_component = (1.0 - min(1.0, flip_rate if flip_rate is not None else 0.5)) * 55.0
    entropy_component = (1.0 - min(1.0, entropy)) * 15.0
    score = _clamp(flip_component + confirmation_score * 0.30 + entropy_component)
    overfit_warning = bool(flip_rate is not None and flip_rate > 0.45)
    return RegimeQualityReport(
        stability_score=round(score, 6),
        flip_rate=round(flip_rate, 6) if flip_rate is not None else None,
        smoothed_flip_rate=smoothed_flip_rate,
        regime_entropy=round(entropy, 6),
        confirmation_score=round(confirmation_score, 6),
        overfit_warning=overfit_warning,
        regime_duration_distribution=[dict(item) for item in durations if isinstance(item, Mapping)],
        gate_status=_gate_status(score, reject_below=40.0, warn_below=65.0),
    )


def build_report_quality_report(research_runs: Sequence[Mapping[str, object]]) -> ReportQualityReport:
    if not research_runs:
        return ReportQualityReport(
            completeness=0.0,
            consistency=0.0,
            leakage_risk=60.0,
            interpretability=0.0,
            rejection_reasons=["missing research runs"],
            gate_status="reject",
        )
    required = ("title", "summary", "valuation_low", "valuation_mid", "valuation_high", "valuation_method", "heavy_position_view")
    completeness_scores: list[float] = []
    consistency_scores: list[float] = []
    interpretability_scores: list[float] = []
    leakage_risks: list[float] = []
    reasons: list[str] = []
    for index, run in enumerate(research_runs):
        present = sum(1 for field in required if run.get(field) not in (None, ""))
        completeness_scores.append(present / len(required) * 100.0)
        low = _safe_float(run.get("valuation_low"))
        mid = _safe_float(run.get("valuation_mid"))
        high = _safe_float(run.get("valuation_high"))
        if low is not None and mid is not None and high is not None and low <= mid <= high:
            consistency_scores.append(100.0)
        else:
            consistency_scores.append(35.0)
            reasons.append(f"run[{index}]: invalid valuation range")
        text_length = len(str(run.get("summary") or "")) + len(str(run.get("heavy_position_view") or ""))
        interpretability_scores.append(min(100.0, text_length / 4.0))
        raw_text = str(run.get("raw_json") or "")
        leakage_risks.append(15.0 if "market_context" in raw_text and "taxonomy_profile" in raw_text else 30.0)
    completeness = mean(completeness_scores)
    consistency = mean(consistency_scores)
    interpretability = mean(interpretability_scores)
    leakage_risk = mean(leakage_risks)
    if completeness < 70:
        reasons.append("report completeness below 70")
    if consistency < 70:
        reasons.append("report consistency below 70")
    if leakage_risk > 45:
        reasons.append("leakage risk above threshold")
    combined = completeness * 0.35 + consistency * 0.30 + interpretability * 0.20 + (100.0 - leakage_risk) * 0.15
    gate = "reject" if reasons and combined < 70 else _gate_status(combined)
    return ReportQualityReport(
        completeness=round(completeness, 6),
        consistency=round(consistency, 6),
        leakage_risk=round(leakage_risk, 6),
        interpretability=round(interpretability, 6),
        rejection_reasons=reasons,
        gate_status=gate,
    )


def build_research_health_report(
    *,
    data_quality: DataQualityReport,
    factor_quality: FactorQualityReport,
    regime_quality: RegimeQualityReport,
    report_quality: ReportQualityReport,
) -> ResearchHealthReport:
    score = (
        data_quality.completeness_score * 0.30
        + factor_quality.ic_validity_score * 0.25
        + regime_quality.stability_score * 0.20
        + (
            report_quality.completeness * 0.30
            + report_quality.consistency * 0.30
            + (100.0 - report_quality.leakage_risk) * 0.20
            + report_quality.interpretability * 0.20
        )
        * 0.25
    )
    statuses = {data_quality.gate_status, factor_quality.gate_status, regime_quality.gate_status, report_quality.gate_status}
    if "reject" in statuses:
        gate = "reject"
    elif "warn" in statuses:
        gate = "warn"
    else:
        gate = "pass"
    return ResearchHealthReport(
        data_quality=data_quality,
        factor_quality=factor_quality,
        regime_quality=regime_quality,
        report_quality=report_quality,
        system_health_score=round(_clamp(score), 6),
        gate_status=gate,
        constraints={
            "read_only": True,
            "research_only": True,
            "contains_trade_orders": False,
            "contains_cash_amounts": False,
            "contains_share_counts": False,
            "executes_rebalance": False,
        },
    )


def research_health_report_to_dict(report: ResearchHealthReport) -> dict[str, Any]:
    return asdict(report)
