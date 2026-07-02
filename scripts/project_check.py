from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def check(condition: bool, message: str) -> bool:
    status = "OK" if condition else "FAIL"
    print(f"{status} {message}")
    return condition


def main() -> int:
    ok = True
    ok &= check((ROOT / ".env.example").exists(), ".env.example exists")
    ok &= check((ROOT / ".gitignore").exists(), ".gitignore exists")
    try:
        ignored = subprocess.run(
            ["git", "check-ignore", ".env"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        ok &= check(ignored.returncode == 0, ".env is ignored by git")
    except FileNotFoundError:
        ok &= check(False, "git is available")

    for module in [
        "myinvestetf.db",
        "myinvestetf.leader_index",
        "myinvestetf.web",
        "core.decision",
        "core.governance",
        "core.interpreter",
        "core.market",
        "core.replay",
        "core.risk",
        "core.strategy",
        "core.taxonomy",
        "core.factors",
    ]:
        try:
            importlib.import_module(module)
            ok &= check(True, f"import {module}")
        except Exception as exc:  # pragma: no cover - diagnostic script
            print(exc)
            ok &= check(False, f"import {module}")

    config_source = (ROOT / "myinvestetf" / "config.py").read_text(encoding="utf-8")
    leader_source = (ROOT / "myinvestetf" / "leader_index.py").read_text(encoding="utf-8")
    web_source = (ROOT / "myinvestetf" / "web.py").read_text(encoding="utf-8")
    ok &= check("https://invest.okbbc.com/header.js" in config_source, "unified header script is wired")
    ok &= check("https://invest.okbbc.com/footer.js" in config_source, "unified footer script is wired")
    ok &= check("data-myinvest-header" in web_source and "data-myinvest-footer" in web_source, "unified shell mount points are wired")
    ok &= check('LEADER_INDEX_URL = "https://theme.okbbc.com/api/latest"' in config_source, "upstream source is theme /api/latest")
    ok &= check("themes[].stock_leaders" not in leader_source, "ingest does not expand from stock_leaders")
    ok &= check("ETFResearchReport" in leader_source, "ETF report prompt schema is wired")
    ok &= check("valuation_model_type" in leader_source and "model_specific_inputs" in leader_source, "type-aware ETF research prompts are wired")
    ok &= check("market_context" in leader_source and "price_series" in leader_source, "market context prompt inputs are wired")
    ok &= check("taxonomy_profile" in leader_source and "所有 ETF 评分必须绑定 taxonomy" in leader_source, "taxonomy-bound ETF prompts are wired")
    ok &= check("task_type 固定为 research" in leader_source, "ETF queue uses unified research task type")
    ok &= check(
        "build_profile_prompt" not in leader_source and "build_valuation_prompt" not in leader_source,
        "legacy profile/valuation prompt builders are removed",
    )
    ok &= check((ROOT / "core" / "valuation" / "classification.py").exists(), "ETF valuation classification layer exists")
    ok &= check((ROOT / "core" / "market" / "regime.py").exists(), "ETF market regime layer exists")
    ok &= check((ROOT / "core" / "market" / "structure.py").exists(), "ETF market structure layer exists")
    ok &= check((ROOT / "core" / "decision" / "engine.py").exists(), "ETF decision engine exists")
    ok &= check((ROOT / "core" / "governance" / "engine.py").exists(), "ETF governance engine exists")
    ok &= check((ROOT / "core" / "interpreter" / "decision_interpreter.py").exists(), "ETF decision interpreter exists")
    ok &= check((ROOT / "core" / "interpreter" / "question_router.py").exists(), "ETF question router exists")
    ok &= check((ROOT / "core" / "interpreter" / "answer_policy.py").exists(), "ETF answer policy engine exists")
    ok &= check((ROOT / "core" / "replay" / "engine.py").exists(), "ETF replay engine exists")
    ok &= check((ROOT / "core" / "risk" / "drawdown.py").exists(), "ETF drawdown layer exists")
    ok &= check((ROOT / "core" / "strategy" / "contrarian_mode.py").exists(), "ETF contrarian strategy layer exists")
    ok &= check((ROOT / "core" / "taxonomy" / "etf_classifier.py").exists(), "ETF taxonomy classifier exists")
    ok &= check((ROOT / "core" / "factors" / "standardization.py").exists(), "ETF factor standardization layer exists")
    ok &= check((ROOT / "core" / "factors" / "ic.py").exists(), "ETF factor IC layer exists")
    ok &= check("/api/score/{etf}" in web_source and "/api/decision/state/{etf}" in web_source, "decision score APIs are cataloged")
    ok &= check("/api/ask/{etf}" in web_source and "api_ask_for_etf" in web_source, "decision ask API is cataloged")
    ok &= check("render_ask_widget" in web_source and "常用问题" in web_source, "ETF ask page entry is wired")
    ok &= check("AnswerPolicyEngine" in (ROOT / "core" / "interpreter" / "decision_interpreter.py").read_text(encoding="utf-8"), "final answers route through answer policy")
    ok &= check("/api/replay/{etf}" in web_source and "/api/replay/{etf}/stability" in web_source, "decision replay APIs are cataloged")
    ok &= check("/api/strategy/contrarian/{etf}" in web_source and "api_contrarian_for_etf" in web_source, "contrarian strategy API is cataloged")
    ok &= check("/api/health/system" in web_source and "/api/health/report" in web_source, "research health APIs are cataloged")
    queue_prompt_doc = ROOT / "docs" / "QUEUE_PROMPTS.md"
    queue_script = (ROOT / "scripts" / "generate_single_etf_prompt.py").read_text(encoding="utf-8")
    ok &= check(queue_prompt_doc.exists(), "ETF queue prompt contract is documented")
    ok &= check("队列任务元数据" in queue_script and "run_id" in queue_script, "queue prompt output includes traceable metadata")
    ok &= check('"research"' in queue_script and '"profile"' not in queue_script and '"valuation"' not in queue_script, "queue script exposes only research task type")
    docs = (ROOT / "docs" / "DATA_SOURCES.md").read_text(encoding="utf-8")
    ok &= check("fund_basic" in docs and "fund_portfolio" in docs, "ETF data sources are documented")
    ok &= check("现金替代" in docs, "cash-like ETF boundary is documented")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
