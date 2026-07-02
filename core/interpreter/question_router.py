from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal


QuestionType = Literal["buy_assessment", "risk_assessment", "market_state", "comparison", "unknown"]
QuestionFocus = Literal["risk", "return", "timing", "structure", "unknown"]

ETF_CODE_RE = re.compile(r"\b\d{6}\.(?:SH|SZ|BJ)\b", re.IGNORECASE)

BUY_KEYWORDS = ("能不能买", "适合买吗", "可以买吗", "买吗", "加仓", "参与", "入场", "配置")
RISK_KEYWORDS = ("风险", "安全吗", "安全性", "会不会跌", "下跌", "回撤", "波动", "危险")
STATE_KEYWORDS = ("什么状态", "现在状态", "市场怎么样", "市场状态", "市场环境", "regime", "趋势")
COMPARISON_KEYWORDS = (" vs ", " VS ", "比", "相比", "哪个更", "哪一个更", "谁更")


@dataclass(frozen=True)
class QuestionIntent:
    type: QuestionType
    confidence: float
    entities: dict[str, object]
    focus: QuestionFocus
    normalized_question: str


def _normalize_question(question: str) -> str:
    return " ".join(str(question or "").strip().split())


def _extract_etf_codes(question: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    for match in ETF_CODE_RE.finditer(question):
        code = match.group(0).upper()
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _classify(normalized: str, codes: list[str]) -> tuple[QuestionType, QuestionFocus, float]:
    if len(codes) >= 2 or _contains_any(normalized, COMPARISON_KEYWORDS):
        return "comparison", "structure", 0.86
    if _contains_any(normalized, RISK_KEYWORDS):
        return "risk_assessment", "risk", 0.88
    if _contains_any(normalized, BUY_KEYWORDS):
        return "buy_assessment", "timing", 0.88
    if _contains_any(normalized, STATE_KEYWORDS):
        return "market_state", "structure", 0.84
    return "unknown", "unknown", 0.25


def parse_question(question: str, *, etf_code: str | None = None) -> QuestionIntent:
    normalized = _normalize_question(question)
    codes = _extract_etf_codes(normalized)
    primary = str(etf_code or (codes[0] if codes else "")).upper() or None
    comparison_codes = [code for code in codes if code != primary]
    question_type, focus, confidence = _classify(normalized, codes)
    if question_type != "unknown" and not normalized:
        question_type, focus, confidence = "unknown", "unknown", 0.1
    return QuestionIntent(
        type=question_type,
        confidence=confidence,
        entities={
            "etf_code": primary,
            "comparison_etfs": comparison_codes,
        },
        focus=focus,
        normalized_question=normalized,
    )


def question_intent_to_dict(intent: QuestionIntent) -> dict[str, object]:
    return asdict(intent)
