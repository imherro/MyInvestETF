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
- `taxonomy_profile`: 可选，系统生成的 ETF 分类画像
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
- `market_context` 如存在，`etf_code` 必须等于顶层 `etf_code`；该字段会进入只读 `DecisionSignal`，但不改写已入库研究报告。
- `taxonomy_profile` 如存在，`legacy_valuation_model_type` 和 `legacy_sleeve_key` 必须等于顶层兼容字段。

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

## 组合使用判断标签

只允许使用研究标签，不输出买卖指令；页面会把 `工具仓可用` 展示为“阶段性工具仓可用，不等于当前买入”：

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
- `taxonomy_profile`: 可选，系统分类画像；通常由 `core/taxonomy` 生成
- `price_series`: 可选，ETF 日行情序列，支持 `trade_date`、`close`/`close_price`、`amount`、`volume`
- `index_price_series`: 可选，底层指数日行情序列，用于市场状态判断
- `evidence`, `assumptions`, `data_gaps`

系统会从 `price_series` 计算 `market_context.drawdown`，并优先用 `index_price_series` 计算 `market_context.regime`。如果没有行情序列，报告仍可生成，页面和 API 会尝试用本地 `etf_daily_prices` 缓存补充。

系统会从 ETF 元数据、跟踪指数、资产类别、行业/主题暴露、波动和流动性线索生成 `taxonomy_profile`。所有 ETF 评分必须绑定 taxonomy；taxonomy 会影响 `DecisionSignal` 的权重和解释，但不替代四类兼容估值模型。

## Factor Output

标准化因子不写入 `ETFResearchReport` 强 schema，当前通过 `/api/factors/*` 暴露。每个因子输出必须包含：

- `raw_value`
- `normalized_value`
- `z_score`
- `percentile`
- `as_of_date`
- `lookback_window`
- `source`
- `leakage_guard`

所有因子默认使用 point-in-time lag 1，禁止用 forward return 窗口内的数据计算因子本身。

## Market Structure Output

市场结构不写入 `ETFResearchReport` 强 schema，当前通过 `/api/market/*` 暴露。字段包括：

- `index_breadth`
- `sector_breadth`
- `advance_decline_ratio`
- `liquidity_breadth`
- `dispersion`
- `breadth_score`
- `liquidity_score`
- `dispersion_score`
- `contributions`

Regime v2 使用 40% price trend、30% breadth、20% liquidity、10% volatility 的输入权重，并输出 `confirmation_level` 与解释文本。该结果会进入 `DecisionSignal` 的状态、动态权重和解释。

## Decision Signal Output

状态感知研究评分不写入 `ETFResearchReport` 强 schema，当前通过 `/api/score/*`、`/api/decision/state/*` 和 ETF 详情页生成。字段包括：

- `score`: 0-100 研究评分
- `regime`: Regime v2 输入快照
- `component_scores`: `momentum`、`flow`、`valuation`、`risk`
- `factor_contributions`: 四个组件对最终分的贡献分
- `adjusted_weights`: regime、taxonomy 和 factor effectiveness 调整后的动态权重
- `factor_effectiveness`: 当前 regime 下各组件有效性
- `state`: `regime`、`score_band`、`trend_state`、`taxonomy_type`、`state_code`
- `confidence`
- `constraints`: 必须声明只读、研究用途、不含交易动作、不含现金金额、不含份额数量

该层只用于解释和研究排序，不改写参考价格区间或入库报告。收益防御/自由现金流 ETF 的深回撤会形成 `drawdown_opportunity_score`，并可提升运行时估值组件分。

## Contrarian Signal Output

抄底概率模式不写入 `ETFResearchReport` 强 schema，当前通过 `/api/strategy/contrarian/*` 和 ETF 详情页生成。字段包括：

- `enabled`: 是否进入 Contrarian Mode。
- `scores.reversal_probability`: 反转概率，只表示概率底部观察，不表示买入指令。
- `scores.exhaustion_score`: 趋势衰竭分。
- `scores.capitulation_score`: 恐慌释放分。
- `conditions.drawdown_extreme`: 是否满足极端回撤。
- `conditions.regime_stress`: Regime v2 是否处于压力状态。
- `conditions.liquidity_stress`: 流动性或 flow 是否处于压力。
- `conditions.volatility_stress`: 20 日波动是否进入压力状态。
- `conditions.governance_allowed`: 研究健康度 gate 是否允许该解释层生效；`reject` 时为 false。
- `evidence.current_drawdown`: 当前回撤。
- `evidence.drawdown_percentile`: 当前回撤在历史回撤序列中的严重程度分位。
- `evidence.extreme_proximity`: 当前回撤接近滚动最大回撤的程度。
- `evidence.regime`: Regime v2 当前状态。
- `evidence.volatility_20`: 20 日波动。
- `evidence.liquidity_score`: 市场结构流动性分。
- `evidence.flow_score`: ETF flow 因子分。
- `evidence.governance_gate`: 研究健康度 gate。
- `adjusted_interpretation.final_view`: `probabilistic_bottom_zone`、`normal` 或 `not_active`。

`enabled=true` 只在 `drawdown_extreme`、`regime_stress`、`volatility_stress` 和 `governance_allowed` 同时满足时触发。`liquidity_stress` 是概率和解释输入，不是当前版本的硬触发门槛。

该层是 `DecisionSignal` 的再解释层，不覆盖原始 `DecisionSignal.score`，不输出交易动作、现金金额或份额数量。

## Strategy Route Output

策略路由不写入 `ETFResearchReport` 强 schema，当前通过 `/api/strategy/route/*` 和 ETF 详情页生成。字段包括：

- `active_mode`: `trend`、`contrarian` 或 `neutral`。
- `confidence`: 路由置信度。
- `reasoning.regime_reason`: 市场状态原因。
- `reasoning.flow_reason`: flow 和流动性原因。
- `reasoning.drawdown_reason`: 回撤与抄底概率原因。
- `reasoning.governance_reason`: 研究治理 gate 原因。
- `suppressed_mode`: 被压制的模式，允许为 `null`。
- `signals.trend_score`: 顺势模式分。
- `signals.contrarian_score`: 抄底概率模式分。
- `final_interpretation`: 最终策略解释文本。

该层只做 trend、contrarian、neutral 的解释层选择，不改写 `DecisionSignal.score`，不输出交易动作、现金金额或份额数量。

## Replay Report Output

历史回放不写入 `ETFResearchReport` 强 schema，当前通过 `/api/replay/*` 输出。字段包括：

- `time_series.score_series`: 历史 DecisionSignal score
- `time_series.regime_series`: 历史 regime path
- `time_series.factor_series`: 每日组件贡献
- `stability.score_std`
- `stability.regime_flip_rate`
- `stability.regime_duration_distribution`
- `stability.regime_transition_matrix`
- `stability.factor_stability_ic`
- `stability.taxonomy_consistency_drift`
- `drawdown_sensitivity.score_vs_drawdown_correlation`
- `consistency_score`
- `validation.as_of_enforced`
- `validation.no_future_data`
- `validation.valuation_policy`

Replay 必须按 `as_of_date` 截断本地价格序列。研究日之前没有历史估值信号时，使用中性估值输入，不能复用最新估值分数。

## Research Health Output

研究治理健康报告不写入 `ETFResearchReport` 强 schema，当前通过 `/api/health/*` 输出。字段包括：

- `data_quality.completeness_score`
- `data_quality.missing_data_ratio`
- `data_quality.stale_data_ratio`
- `data_quality.alignment_score`
- `data_quality.coverage_score`
- `factor_quality.ic_validity_score`
- `factor_quality.unstable_factors`
- `factor_quality.redundant_factors`
- `factor_quality.ic_decay_alerts`
- `regime_quality.stability_score`
- `regime_quality.flip_rate`
- `regime_quality.smoothed_flip_rate`
- `regime_quality.overfit_warning`
- `report_quality.completeness`
- `report_quality.consistency`
- `report_quality.leakage_risk`
- `report_quality.rejection_reasons`
- `system_health_score`
- `gate_status`: `pass`、`warn` 或 `reject`

当 gate 为 `reject` 时，系统应把对应研究输出视为低可信度结果，不作为对外发布或自动化后续任务的可靠输入。

`model_specific_inputs` 按类型分流：

- `broad_index`: `equity_risk_premium`, `roe`, `market_position_score`
- `mainline_theme`: `theme_strength`, `fund_flow_score`, `crowding_score`, `valuation_tolerance`
- `factor_defensive`: `dividend_spread`, `fcf_yield`, `quality_score`, `style_opportunity_cost`
- `cash_like`: `duration_risk`, `credit_risk`, `yield_stability`

解释层按 ETF 类型使用不同上游信号：`market_signal` 代表 market 研究/市场状态给出的仓位口径，适用于所有权益 ETF；`theme_signal` 只代表 theme 研究的行业主线，只有 `mainline_theme` 使用它作为确认条件；`product_signal` 只描述 ETF 自身估值、流动性、跟踪质量、折溢价、回撤机会和风险调整。

收益防御 ETF 还会使用 `market_signal.defensive_factor_guidance` 解释组合层防御因子仓位带：`risk_on` 靠近下沿 `2%-5%`，`rotation` 使用中位 `5%-8%`，`risk_off/shock` 靠近上沿 `8%-12%`。该字段是红利低波、自由现金流、质量因子等防御因子仓的整体区间，不是单只 ETF 的买入比例。

现金替代类 ETF 默认不进入深度研究队列；如生成监控型报告，也只能用于现金替代资格检查。
