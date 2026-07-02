# ETF 深研结构

`etf_research_runs` 是 ETF 页面历史记录来源。

新入库研究结果必须先通过 `core/schema/etf_report.py` 中的 `ETFResearchReport` 校验。

## 强 Schema

统一研究 JSON 的顶层字段：

- `schema_version`: 固定为 `etf_research_report.v1`
- `report_version`: 确定性报告组装器版本
- `report_hash`: 确定性回放 hash
- `run_id`: 由 `etf_code + task_type + research_date + schema_version` 计算
- `etf_code`: ETF 代码，例如 `510300.SH`
- `etf_name`: ETF 名称
- `source_report_id`
- `task_type`: 固定为 `research`
- `research_date`: `YYYY-MM-DD`
- `status`: `complete`、`draft` 或 `blocked`
- `valuation_model_type`: `broad_index`、`mainline_theme`、`factor_defensive` 或 `cash_like`
- `sleeve_key`: `core_wide_etf`、`mainline_etf`、`defensive_quality` 或 `cash_like`
- `title`、`summary`
- `product_profile`
- `holdings_profile`
- `valuation`
- `base_position_view`
- `risk`
- `conclusion`
- `market_context`: 可选，系统生成的市场状态与回撤上下文
- `evidence`
- `assumptions`
- `data_gaps`

强约束：

- schema 禁止额外字段。
- `task_type` 只接受 `research`。
- `report_hash` 如果提供，必须是 64 位小写 sha256 hex。
- `base_position_view` 必须等于 `conclusion.grade`。
- 顶层 `valuation_model_type` / `sleeve_key` 必须等于 `product_profile` 内部同名字段。
- 如果显式提供 `run_id`，必须等于系统计算值。
- `research` 必须写入完整参考价值区间，且 `low <= mid <= high`。
- `market_context` 如存在，`etf_code` 必须等于顶层 `etf_code`；该字段只作为上下文，不改变当前评分。

## 任务状态机

`task_queue` 是系统级任务控制表，也是唯一状态源。展示用 `research_queue` 只作为 prompt/projection/UI 表，通过 `run_id` 关联 `task_queue`。

合法状态转换：

```text
PENDING -> RUNNING
PENDING -> BLOCKED
RUNNING -> DONE
RUNNING -> FAILED
RUNNING -> BLOCKED
FAILED -> RETRY
RETRY -> PENDING
BLOCKED -> PENDING
BLOCKED -> FAILED
```

## 底仓资格标签

只允许使用研究标签，不输出买卖指令：

- `不适合底仓`
- `观察`
- `工具仓可用`
- `底仓候选`
- `估值或拥挤暂缓`

## Research Assembly Input

`research` 任务的 `assembly_input` 必须包含：

- `etf_code`, `etf_name`, `source_report_id`, `task_type`, `research_date`
- `valuation_model_type`
- `sleeve_key`
- `product_profile`
- `holdings_inputs`
- `valuation_inputs`
- `model_specific_inputs`
- `liquidity_inputs`
- `tracking_inputs`
- `risk_signals`
- `price_series`: 可选，ETF 日行情序列，支持 `trade_date`、`close`/`close_price`、`amount`、`volume`
- `index_price_series`: 可选，底层指数日行情序列，用于市场状态判断
- `evidence`, `assumptions`, `data_gaps`

系统会从 `price_series` 计算 `market_context.drawdown`，并优先用 `index_price_series` 计算 `market_context.regime`。如果没有行情序列，报告仍可生成，页面和 API 会尝试用本地 `etf_daily_prices` 缓存补充。

`model_specific_inputs` 按类型分流：

- `broad_index`: `equity_risk_premium`, `roe`, `market_position_score`
- `mainline_theme`: `theme_strength`, `fund_flow_score`, `crowding_score`, `valuation_tolerance`
- `factor_defensive`: `dividend_spread`, `fcf_yield`, `quality_score`, `style_opportunity_cost`
- `cash_like`: `duration_risk`, `credit_risk`, `yield_stability`

现金替代类 ETF 默认不进入深度研究队列；如生成监控型报告，也只能用于现金替代资格检查。
