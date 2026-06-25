# Codex 自动化设计

## 任务拆分

ETF 深研必须一次只研究一只 ETF。自动化只分为两类：

1. `MyInvestETF ETF 输入读取入队`
2. `MyInvestETF ETF 完整深研队列消化`

队列任务提示词的完整执行包设计见 [QUEUE_PROMPTS.md](QUEUE_PROMPTS.md)。自动化必须通过 `python scripts/generate_single_etf_prompt.py --next --claim` 领取任务，并使用该命令输出的“队列任务元数据 + 队列任务提示词”作为唯一执行依据。

## ETF 输入读取入队

用途：读取 ETF 输入源，更新今日待研队列，不做深研。

规则：

- 默认读取 `https://theme.okbbc.com/api/latest` 的 `result.theme_ranking[].top_etf` 和 `result.etf_top`，并兼容旧 `/api/index` 结构。
- 每条主线至少保留一个 ETF 代表进入研究队列；同一主线内优先选择有成交额且成交额最大的 ETF，缺成交额时使用 `top_etf` 的第一位。
- 本地补齐上证综指、上证50、沪深300、中证500、中证1000、创业板、科创50 等核心宽基 ETF 研究对象。
- 非现金替代 ETF 每只只生成一条 `research` 完整深研任务。
- 短融、日利、货币、现金类 ETF 默认不生成深度研究任务。
- 本任务只做入队和状态汇总，不领取研究任务。

## ETF 完整深研队列消化

用途：每小时运行一次，持续消化本地待研究队列。每次运行只处理一条任务。

规则：

- 只从本地 `research_queue` 领取 pending 任务。
- 领取任务时把状态标记为 `RUNNING`。
- `task_queue` 是唯一状态源。
- 每次只研究一只 ETF、一个 `research` 任务类型。
- `research` 最终报告必须由 `core/report.build_etf_report(...)` 或 `scripts/build_research_report.py` 生成。
- 报告生成时应记录 `feature`、`valuation`、`signal`、`report` 四个 trace stage。
- 领取脚本输出的 `run_id`、`task_id`、`source_type` 和 `depends_on_task_type` 必须保留在自动化汇报中。
- 如果队列为空，验证 `/api/index` 和 `/api/latest` 可用，然后结束。

## 数据原则

- Tushare 是 A 股 ETF 结构化主源，使用本地 `.env`，不得输出 token。
- ETF 重点接口：`fund_basic`、`fund_daily`、`fund_nav`、`fund_share`、`fund_portfolio`、`index_daily`。
- `fund_share` 是份额变化的可用代理。
- `fund_portfolio` 只代表已披露季报持仓，不是实时完整底仓。
- 网络资料只作为补充证据，必须记录来源、日期和用途。
- 不输出交易指令、现金金额或份额数量。

## 完整深研流程

1. 运行 `python scripts/ingest_index.py` 更新上游 ETF 队列。
2. 领取下一条 `research` 队列任务。
3. 构建 `assembly_input`，包括 `product_profile`、`holdings_inputs`、`valuation_inputs`、`model_specific_inputs`、`liquidity_inputs`、`tracking_inputs`、`risk_signals`、`evidence`、`assumptions`、`data_gaps`。
4. 运行 `python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite <assembly_input>`。
5. 运行 `python scripts/import_research_run.py <report_json>` 入库。
6. 验证 `/api/index`、`/api/latest` 和 ETF 详情页可用。
