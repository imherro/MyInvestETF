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

    for module in ["myinvestetf.db", "myinvestetf.leader_index", "myinvestetf.web"]:
        try:
            importlib.import_module(module)
            ok &= check(True, f"import {module}")
        except Exception as exc:  # pragma: no cover - diagnostic script
            print(exc)
            ok &= check(False, f"import {module}")

    config_source = (ROOT / "myinvestetf" / "config.py").read_text(encoding="utf-8")
    leader_source = (ROOT / "myinvestetf" / "leader_index.py").read_text(encoding="utf-8")
    ok &= check("https://invest.okbbc.com/footer.js" in config_source, "unified footer script is wired")
    ok &= check('LEADER_INDEX_URL = "https://theme.okbbc.com/api/latest"' in config_source, "upstream source is theme /api/latest")
    ok &= check("themes[].stock_leaders" not in leader_source, "ingest does not expand from stock_leaders")
    ok &= check("ETFResearchReport" in leader_source, "ETF report prompt schema is wired")
    ok &= check("valuation_model_type" in leader_source and "model_specific_inputs" in leader_source, "type-aware ETF valuation prompts are wired")
    ok &= check((ROOT / "core" / "valuation" / "classification.py").exists(), "ETF valuation classification layer exists")
    queue_prompt_doc = ROOT / "docs" / "QUEUE_PROMPTS.md"
    queue_script = (ROOT / "scripts" / "generate_single_etf_prompt.py").read_text(encoding="utf-8")
    ok &= check(queue_prompt_doc.exists(), "ETF queue prompt contract is documented")
    ok &= check("队列任务元数据" in queue_script and "run_id" in queue_script, "queue prompt output includes traceable metadata")
    docs = (ROOT / "docs" / "DATA_SOURCES.md").read_text(encoding="utf-8")
    ok &= check("fund_basic" in docs and "fund_portfolio" in docs, "ETF data sources are documented")
    ok &= check("现金替代" in docs, "cash-like ETF boundary is documented")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
