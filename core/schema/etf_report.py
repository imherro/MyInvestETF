from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictStr, model_validator

from core.task.state import compute_task_run_id
from core.taxonomy import ETFType, ThemeLifecycleStage, taxonomy_type_matches_valuation_model
from core.valuation.classification import SleeveKey, ValuationModelType


ETF_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")

TaskType = Literal["research"]
RunStatus = Literal["complete", "draft", "blocked"]
Confidence = Literal["low", "medium", "high"]
BasePositionView = Literal["不适合底仓", "观察", "工具仓可用", "底仓候选", "估值或拥挤暂缓"]
MarketRegimeValue = Literal["risk_on", "risk_off", "shock", "rotation"]


class StrictSchemaModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class EvidenceItem(StrictSchemaModel):
    source: StrictStr
    date: StrictStr | None = None
    url: StrictStr | None = None
    purpose: StrictStr
    detail: StrictStr


class ETFProductProfile(StrictSchemaModel):
    fund_type: StrictStr
    tracking_index: StrictStr | None = None
    asset_class: StrictStr
    valuation_model_type: ValuationModelType
    sleeve_key: SleeveKey
    portfolio_role: StrictStr
    fee_note: StrictStr
    liquidity_note: StrictStr
    tracking_note: StrictStr


class ETFHoldingsProfile(StrictSchemaModel):
    holdings_disclosure_date: StrictStr | None = None
    top_holdings: list[StrictStr] = Field(default_factory=list)
    concentration_note: StrictStr
    overlap_note: StrictStr
    disclosure_lag_note: StrictStr


class ETFValuation(StrictSchemaModel):
    current_price: StrictFloat | None = None
    nav: StrictFloat | None = None
    premium_discount: StrictFloat | None = None
    underlying_pe: StrictFloat | None = None
    underlying_pb: StrictFloat | None = None
    valuation_percentile: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    reference_value_low: StrictFloat | None = None
    reference_value_mid: StrictFloat | None = None
    reference_value_high: StrictFloat | None = None
    unit: StrictStr = "CNY/fund_share"
    method: StrictStr
    confidence: Confidence
    key_assumptions: list[StrictStr] = Field(default_factory=list)
    engine_version: StrictStr | None = None
    undervalued_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    liquidity_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    tracking_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    portfolio_role_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    risk_adjusted_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    mainline_validity_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    valuation_tolerance_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    crowding_risk_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    factor_premium_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)
    cash_like_safety_score: StrictFloat | None = Field(default=None, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def validate_range_order(self) -> ETFValuation:
        values = [self.reference_value_low, self.reference_value_mid, self.reference_value_high]
        if all(value is not None for value in values):
            low, mid, high = values
            if not (low <= mid <= high):
                raise ValueError("reference value range must satisfy low <= mid <= high")
        elif any(value is not None for value in values):
            raise ValueError("reference value range must provide low, mid, and high together")
        return self


class ETFRisk(StrictSchemaModel):
    liquidity_risk: StrictStr
    tracking_risk: StrictStr
    concentration_risk: StrictStr
    sentiment_risk: StrictStr
    invalidation_conditions: list[StrictStr] = Field(default_factory=list)


class ETFConclusion(StrictSchemaModel):
    grade: BasePositionView
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    summary: StrictStr


class ETFMarketRegime(StrictSchemaModel):
    regime: MarketRegimeValue
    confidence: StrictFloat = Field(ge=0.0, le=1.0)
    as_of_date: StrictStr | None = None
    evidence: dict[str, StrictFloat | StrictStr | None] = Field(default_factory=dict)
    data_points: int = Field(default=0, ge=0)


class ETFDrawdownState(StrictSchemaModel):
    current_drawdown: StrictFloat = Field(ge=0.0, le=1.0)
    max_drawdown_rolling: StrictFloat = Field(ge=0.0, le=1.0)
    drawdown_percentile: StrictFloat = Field(ge=0.0, le=100.0)
    recovery_speed: StrictFloat
    duration_days: int = Field(ge=0)
    drawdown_acceleration: StrictFloat = 0.0
    as_of_date: StrictStr | None = None
    peak_date: StrictStr | None = None
    trough_date: StrictStr | None = None
    data_points: int = Field(default=0, ge=0)


class ETFMarketContext(StrictSchemaModel):
    etf_code: StrictStr
    regime: ETFMarketRegime
    drawdown: ETFDrawdownState


class ETFTaxonomyProfile(StrictSchemaModel):
    etf_type: ETFType
    subtype: StrictStr
    lifecycle_stage: ThemeLifecycleStage | None = None
    classification_confidence: StrictFloat = Field(ge=0.0, le=1.0)
    classification_reasons: list[StrictStr] = Field(default_factory=list)
    legacy_valuation_model_type: ValuationModelType
    legacy_sleeve_key: SleeveKey


class ETFResearchReport(StrictSchemaModel):
    schema_version: Literal["etf_research_report.v1"] = "etf_research_report.v1"
    report_version: StrictStr | None = None
    report_hash: StrictStr | None = None
    run_id: StrictStr | None = None
    etf_code: StrictStr
    etf_name: StrictStr
    source_report_id: StrictStr | None = None
    task_type: TaskType
    research_date: StrictStr
    status: RunStatus = "complete"
    valuation_model_type: ValuationModelType
    sleeve_key: SleeveKey
    title: StrictStr
    summary: StrictStr
    product_profile: ETFProductProfile
    holdings_profile: ETFHoldingsProfile
    valuation: ETFValuation
    base_position_view: BasePositionView
    risk: ETFRisk
    conclusion: ETFConclusion
    taxonomy_profile: ETFTaxonomyProfile | None = None
    market_context: ETFMarketContext | None = None
    evidence: list[EvidenceItem] = Field(min_length=1)
    assumptions: list[StrictStr] = Field(default_factory=list)
    data_gaps: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report(self) -> ETFResearchReport:
        if not ETF_CODE_RE.match(self.etf_code):
            raise ValueError("etf_code must match 000000.SH/SZ/BJ")
        if not DATE_RE.match(self.research_date):
            raise ValueError("research_date must use YYYY-MM-DD")
        if self.report_hash is not None and not HASH_RE.match(self.report_hash):
            raise ValueError("report_hash must be a 64-character lowercase sha256 hex digest")
        if self.base_position_view != self.conclusion.grade:
            raise ValueError("base_position_view must equal conclusion.grade")
        if self.product_profile.valuation_model_type != self.valuation_model_type:
            raise ValueError("product_profile.valuation_model_type must equal report valuation_model_type")
        if self.product_profile.sleeve_key != self.sleeve_key:
            raise ValueError("product_profile.sleeve_key must equal report sleeve_key")
        if self.taxonomy_profile is not None:
            if self.taxonomy_profile.legacy_valuation_model_type != self.valuation_model_type:
                raise ValueError("taxonomy legacy valuation_model_type must equal report valuation_model_type")
            if self.taxonomy_profile.legacy_sleeve_key != self.sleeve_key:
                raise ValueError("taxonomy legacy sleeve_key must equal report sleeve_key")
            if not taxonomy_type_matches_valuation_model(self.taxonomy_profile.etf_type, self.valuation_model_type):
                raise ValueError("taxonomy etf_type must match report valuation_model_type")
        if self.market_context is not None and self.market_context.etf_code != self.etf_code:
            raise ValueError("market_context.etf_code must equal report etf_code")

        values = (
            self.valuation.reference_value_low,
            self.valuation.reference_value_mid,
            self.valuation.reference_value_high,
        )
        has_range = all(value is not None for value in values)
        if not has_range:
            raise ValueError("ETF research must include a complete reference value range")

        expected_run_id = compute_task_run_id(self.etf_code, self.task_type, self.research_date, self.schema_version)
        if self.run_id is None:
            object.__setattr__(self, "run_id", expected_run_id)
        elif self.run_id != expected_run_id:
            raise ValueError("run_id must equal hash(etf_code + task_type + date + schema_version)")
        return self


def validate_etf_research_report(raw_output: dict[str, Any]) -> ETFResearchReport:
    return ETFResearchReport(**raw_output)
