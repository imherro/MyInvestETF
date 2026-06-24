# Codex 自动化设计

## 任务拆分

ETF 深研必须一次只研究一只 ETF。研究分为两类：

1. `MyInvestETF ETF 输入读取入队`
2. `MyInvestETF ETF 深研队列消化`
3. `MyInvestETF ETF 产品结构深研 {code} {name}`
4. `MyInvestETF ETF 估值刷新 {code} {name}`

## ETF 输入读取入队

用途：读取 ETF 输入源，更新今日待研队列，不做深研。

规则：

- 默认读取 `https://theme.okbbc.com/api/latest` 的 `result.etf_top`，并兼容旧 `/api/index` 结构。
- 同一 ETF 投资暴露只保留成交额最大的一个代表进入研究队列；分类优先使用 ETF 名称、指数、主题和类别字段中的投资暴露关键词，缺字段时再退回指数名或去掉基金公司前缀后的名称。
- 每只 ETF 如果没有 `profile` 底稿，生成一条产品结构深研任务。
- 每次进入输入源时都可以生成 `valuation` 估值刷新任务。
- 本任务只做入队和状态汇总，不领取研究任务。

## ETF 深研队列消化

用途：高频运行，持续消化本地待研究队列。每次运行只处理一条任务。

规则：

- 只从本地 `research_queue` 领取 pending 任务。
- 领取任务时把状态标记为 `in_progress`。
- `task_queue` 是唯一状态源。
- 每次只研究一只 ETF、一个任务类型。
- `profile` 不写参考价值区间。
- `valuation` 必须依赖已有 `profile` 底稿。
- `valuation` 最终报告必须由 `core/report.build_etf_report(...)` 或 `scripts/build_research_report.py` 生成。
- 报告生成时应记录 `feature`、`valuation`、`signal`、`report` 四个 trace stage。
- 如果队列为空，汇报队列为空，不生成研究正文。

## 数据原则

- Tushare 是 A 股 ETF 结构化主源，使用本地 `.env`，不得输出 token。
- ETF 重点接口：`fund_basic`、`fund_daily`、`fund_nav`、`fund_share`、`fund_portfolio`、`index_daily`。
- `fund_share` 是份额变化的可用代理。
- `fund_portfolio` 只代表已披露季报持仓，不是实时完整底仓。
- 网络资料只作为补充证据，必须记录来源、日期和用途。
- 不输出交易指令、现金金额或份额数量。

## 估值刷新流程

1. 领取下一条 `valuation` 队列任务。
2. 确认本地已有同一 ETF 的 `profile` 底稿。
3. 构建 `assembly_input`，包括 `product_profile`、`holdings_inputs`、`valuation_inputs`、`liquidity_inputs`、`tracking_inputs`、`risk_signals`、`evidence`、`assumptions`、`data_gaps`。
4. 运行 `python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite <assembly_input>`。
5. 运行 `python scripts/import_research_run.py <report_json>` 入库。
6. 验证 `/api/index` 和 `/api/latest` 可用。
