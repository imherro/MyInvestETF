# API Contract

本地服务默认运行在：

```text
http://127.0.0.1:8017
```

## `/api/index`

用途：输出 ETF 主结果信息，供其他系统集成。

关键字段：

- `schema_version`: `myinvestetf.index.v1`
- `key_results.primary_output.items`: 当前 ETF 池列表，包括主线接口 ETF、本地核心宽基种子、收益防御种子和二级主题候选
- `key_results.primary_output.items[].category_key`: ETF 投资暴露类别
- `key_results.primary_output.items[].valuation_model_type`: `broad_index`、`mainline_theme`、`factor_defensive` 或 `cash_like`
- `key_results.primary_output.items[].sleeve_key`: `core_wide_etf`、`mainline_etf`、`defensive_quality` 或 `cash_like`
- `key_results.primary_output.items[].taxonomy_profile`: ETF 分类画像
- `source.upstream_endpoint`: 默认 `https://theme.okbbc.com/api/latest`
- `source.upstream_result_path`: 默认 `result.theme_ranking[].top_etf + result.etf_top + taxonomy_v2_ranking`，兼容旧 `key_results.primary_output.items`
- `source.source_policy`: `/api/index` 保留 ETF 池；本地深研队列先列核心宽基和收益防御代表，再按每条主线保留一个流动性代表，最后补充未被主线覆盖的二级主题/行业反转代表。
- `links.latest`: `/api/latest`
- `constraints.read_only`: `true`
- `constraints.contains_trade_orders`: `false`
- `constraints.contains_cash_amounts`: `false`
- `constraints.contains_share_counts`: `false`

## `/api/latest`

用途：输出当前研究成果。

关键字段：

- `schema_version`: `myinvestetf.research.v2`
- `summary.etf_count`
- `summary.research_run_count`
- `summary.complete_research_count`
- `etfs[].leader.valuation_model_type`
- `etfs[].leader.sleeve_key`
- `etfs[].taxonomy_profile`
- `etfs[].research.latest`
- `etfs[].research.reference_value_history`
- `etfs[].research.run_count`
- `etfs[].market_context`: 市场状态和回撤上下文
- `etfs[].regime_v2`: Regime v2 市场状态；`market_signal` 与页面决策矩阵使用该口径
- `etfs[].market_signal`: market 研究/市场状态给出的仓位信号，适用于所有权益 ETF
- `etfs[].market_signal.defensive_factor_guidance`: 防御因子仓组合层仓位带；`risk_on` 为 `2%-5%`，`rotation` 为 `5%-8%`，`risk_off/shock` 为 `8%-12%`
- `etfs[].theme_signal`: theme 研究给出的行业主线信号，只适用于 `mainline_theme`
- `etfs[].product_signal`: ETF 自身估值、流动性、跟踪和回撤机会信号
- `etfs[].decision_matrix`: 按 ETF 类型组合 market/theme/product 的决策解释矩阵

`research.latest` 是单只 ETF 最新的完整深研结果，不再拆分旧的两阶段任务。

## `/research?etf={code}`

用途：从外部系统跳转到 ETF 研究页面。

行为：

- 如果本地已有该 ETF 页面、研究记录或队列任务，返回 `303` 跳转到 `/etfs/{code}`。
- 如果本地没有该 ETF，创建当天主动请求队列批次，再返回 `303` 跳转到 `/etfs/{code}?queued=1`。
- 如果 ETF 被识别为短融、日利、货币或现金替代类 `cash_like`，不创建深度研究任务，只返回 ETF 页面入口。
- 不直接执行深研，不绕过队列领取和单标的单任务规则。
- 兼容 `code={code}` 查询参数。

## `/api/queue`

用途：输出本地研究队列。

队列任务类型：

- `research`: ETF 完整深研

队列策略：

- 只展示当前主线报告的 `mainline_representative`、`broad_index_representative`、`defensive_representative`、`secondary_theme_representative` 任务，以及最新一次手动请求的 `manual_request` 任务；历史报告队列保留在数据库中但不作为当前队列输出。
- `trackable_leader` 仅作为历史兼容来源保留，不再作为当前自动队列的新写入来源。
- `broad_index`、`mainline_theme`、`factor_defensive` 会进入单一 `research` 队列。
- `cash_like` 不进入深度研究队列，只作为现金替代资格监控对象。

来源字段：

- `source_type`: `broad_index_representative`、`defensive_representative`、`mainline_representative`、`secondary_theme_representative`、`manual_request`；历史数据可能还有 `trackable_leader`
- `source_label`: `核心宽基`、`收益防御代表`、`主线代表`、`二级主题代表`、`其他请求`

## `/api/etfs/{code}`

用途：输出单只 ETF 页面数据。

关键字段：

- `leader_summary`: 入口摘要，没有则为 `null`
- `leader_summary.valuation_model_type`
- `leader_summary.sleeve_key`
- `research_runs`: 研究历史，任务类型统一为 `research`
- `market_context`: `regime` 与 `drawdown`，由本地日行情缓存计算
- `regime_v2`: Regime v2 市场状态；`market_signal` 与页面决策矩阵使用该口径
- `market_signal`: market 研究/市场状态仓位信号，适用于所有权益 ETF
- `market_signal.defensive_factor_guidance`: 红利低波、自由现金流、质量因子等防御因子仓的组合层区间；这是仓位篮子口径，不是单只 ETF 比例
- `theme_signal`: theme 研究行业主线信号，只适用于主线/行业/主题 ETF；宽基和收益防御策略 ETF 显示为不适用
- `product_signal`: ETF 产品估值、流动性、跟踪质量、回撤机会和风险调整信号
- `decision_matrix`: 按 ETF 类型组合 market/theme/product 的决策解释矩阵
- `queue`: 队列状态
- `trackable_history`: 历史入口记录

## `/api/etf/{code}/profile`

用途：输出单只 ETF 的 taxonomy profile。

关键字段：

- `schema_version`: `myinvestetf.etf_profile.v1`
- `code`
- `name`
- `type`: ETF taxonomy 类型
- `subtype`
- `lifecycle`: 主题生命周期阶段，非主题可为 `null`
- `confidence`: 分类置信度
- `classification_reasons`: 命中的分类规则
- `legacy_valuation_model_type`: 兼容旧评分入口
- `legacy_sleeve_key`: 兼容旧五仓角色

该接口只读，不触发队列写入、重估值或交易动作。

## `/api/factors/{etf}`

用途：输出单只 ETF 的 point-in-time 标准化因子暴露。

关键字段：

- `schema_version`: `myinvestetf.factor_exposure.v1`
- `taxonomy_profile`
- `factor_exposure.as_of_date`
- `factor_exposure.selected_factor_names`
- `factor_exposure.factors[]`
- `factor_exposure.factors[].raw_value`
- `factor_exposure.factors[].normalized_value`
- `factor_exposure.factors[].z_score`
- `factor_exposure.factors[].percentile`
- `factor_exposure.factors[].as_of_date`
- `factor_exposure.factors[].lookback_window`
- `factor_exposure.factors[].source`
- `factor_exposure.factors[].leakage_guard`
- `factor_exposure.attribution`

`/api/factors/exposure/{etf}` 是同一输出的显式别名。

## `/api/factors/ic/{factor}`

用途：输出单个因子的 IC 摘要。

关键字段：

- `schema_version`: `myinvestetf.factor_ic.v1`
- `factor`: 因子定义，包括 `name`、`factor_type`、`category`、`lookback_window`、`source`、`valid_universe`
- `summaries[]`: 5/20/60 日 IC 摘要
- `summaries[].ic_mean`
- `summaries[].ic_std`
- `summaries[].ic_decay`
- `summaries[].observations`
- `summaries[].leakage_guard`

该接口只读，只基于本地 `etf_daily_prices` 计算，不触发写入或重估值。

## `/api/score/{etf}`

用途：输出单只 ETF 的 Regime-Aware DecisionSignal 研究评分。

关键字段：

- `schema_version`: `myinvestetf.decision_signal.v1`
- `taxonomy_profile`
- `market_structure`
- `regime_v2`
- `factor_exposure`
- `valuation_signal`
- `decision_signal.score`
- `decision_signal.component_scores`: `momentum`、`flow`、`valuation`、`risk`
- `decision_signal.factor_contributions`: 四个组件对最终分的贡献分
- `decision_signal.adjusted_weights`: 当前 regime 和 taxonomy 调整后的动态权重
- `decision_signal.factor_effectiveness`: 当前 regime 下各组件有效性
- `decision_signal.state`: `regime`、`score_band`、`trend_state`、`state_code`
- `decision_signal.confidence`
- `constraints.contains_trade_orders`: `false`
- `constraints.contains_cash_amounts`: `false`
- `constraints.contains_share_counts`: `false`

该接口只读，不写入研究报告、队列或数据库。接口会按当前 taxonomy、Regime v2、因子和运行时估值上下文生成 `DecisionSignal`，但不触发重算、同步或交易动作。

## `/api/score/decompose/{etf}`

用途：输出单只 ETF 的评分组件、动态权重和贡献拆解。

关键字段：

- `score`
- `component_scores`
- `factor_contributions`
- `adjusted_weights`
- `factor_effectiveness`
- `inputs.factor_names_by_type`
- `inputs.fallbacks`

用于解释最终研究评分如何由动量、流动性、估值和风险组成。

## `/api/decision/state/{etf}`

用途：输出单只 ETF 的状态机结果。

关键字段：

- `score`
- `confidence`
- `state.regime`
- `state.score_band`
- `state.trend_state`
- `state.taxonomy_type`
- `state.state_code`
- `state.explanation`

该接口只输出研究状态，不输出买卖动作、仓位、现金金额或份额数量。

## `/api/strategy/contrarian/{etf}`

用途：输出单只 ETF 的 Contrarian Mode 抄底概率模式。

关键字段：

- `schema_version`: `myinvestetf.contrarian_signal.v1`
- `market_context.drawdown`
- `regime_v2`
- `decision_signal.score`: 原始 Decision Score
- `contrarian_signal.enabled`
- `contrarian_signal.scores.reversal_probability`
- `contrarian_signal.scores.exhaustion_score`
- `contrarian_signal.scores.capitulation_score`
- `contrarian_signal.conditions.drawdown_extreme`
- `contrarian_signal.conditions.regime_stress`
- `contrarian_signal.conditions.liquidity_stress`
- `contrarian_signal.conditions.volatility_stress`
- `contrarian_signal.conditions.governance_allowed`
- `contrarian_signal.evidence.current_drawdown`
- `contrarian_signal.evidence.drawdown_percentile`
- `contrarian_signal.evidence.extreme_proximity`
- `contrarian_signal.evidence.regime`
- `contrarian_signal.evidence.volatility_20`
- `contrarian_signal.evidence.liquidity_score`
- `contrarian_signal.evidence.flow_score`
- `contrarian_signal.evidence.governance_gate`
- `contrarian_signal.adjusted_interpretation.risk_adjusted_score`
- `contrarian_signal.adjusted_interpretation.original_decision_score`
- `contrarian_signal.adjusted_interpretation.final_view`: `probabilistic_bottom_zone`、`normal` 或 `not_active`
- `contrarian_signal.constraints.does_not_override_decision_score`: `true`

`contrarian_signal.enabled=true` 的硬触发条件是 `drawdown_extreme && regime_stress && volatility_stress && governance_allowed`。`liquidity_stress` 参与恐慌释放和反转概率计算，但不是当前版本的硬门槛；页面仍会展示该条件，帮助解释资金流或流动性压力。

该接口只读，不写库、不写队列、不重算研究报告、不覆盖原始 Decision Score、不输出交易动作、现金金额或份额数量。

## `/api/strategy/route/{etf}`

用途：输出单只 ETF 的 Strategy Router 策略编排结果。

关键字段：

- `schema_version`: `myinvestetf.strategy_route.v1`
- `decision_signal.score`: 原始 Decision Score
- `contrarian_signal`: Contrarian Mode 旁路结果
- `strategy_decision.active_mode`: `trend`、`contrarian` 或 `neutral`
- `strategy_decision.confidence`
- `strategy_decision.reasoning.regime_reason`
- `strategy_decision.reasoning.flow_reason`
- `strategy_decision.reasoning.drawdown_reason`
- `strategy_decision.reasoning.governance_reason`
- `strategy_decision.suppressed_mode`: `trend`、`contrarian` 或 `null`
- `strategy_decision.signals.trend_score`
- `strategy_decision.signals.contrarian_score`
- `strategy_decision.final_interpretation`
- `strategy_decision.constraints.does_not_override_decision_score`: `true`

该接口只读，只做策略解释层路由；不修改 scoring system、不修改 Decision Engine、不写入数据库、不输出交易建议、现金金额或份额数量。

## `/api/replay/{etf}`

用途：输出单只 ETF 的历史 DecisionSignal 回放报告。

关键字段：

- `schema_version`: `myinvestetf.replay_report.v1`
- `replay_report.etf`
- `replay_report.time_series.score_series`
- `replay_report.time_series.regime_series`
- `replay_report.time_series.factor_series`
- `replay_report.stability.score_std`
- `replay_report.stability.regime_flip_rate`
- `replay_report.stability.regime_duration_distribution`
- `replay_report.stability.regime_transition_matrix`
- `replay_report.stability.factor_stability_ic`
- `replay_report.drawdown_sensitivity.score_vs_drawdown_correlation`
- `replay_report.consistency_score`
- `replay_report.validation.no_future_data`
- `replay_report.validation.valuation_policy`

该接口按 `as_of_date` 截断本地历史行情重建评分路径；Web API 默认均匀采样 24 个回放点并保留最新交易日。不写库、不触发重估值、不输出交易内容。

## `/api/replay/{etf}/stability`

用途：输出单只 ETF 回放稳定性摘要。

关键字段：

- `stability.score_mean`
- `stability.score_std`
- `stability.score_range`
- `stability.regime_flip_rate`
- `stability.regime_duration_distribution`
- `stability.dominant_factor_rate`
- `stability.taxonomy_consistency_drift`
- `drawdown_sensitivity`
- `consistency_score`
- `validation`

## `/api/replay/{etf}/regime-path`

用途：输出单只 ETF 的历史 regime path 和状态切换结构。

关键字段：

- `regime_series`
- `regime_duration_distribution`
- `regime_transition_matrix`
- `validation.no_future_data`

## `/api/health/system`

用途：输出系统级研究可信度健康报告。

关键字段：

- `schema_version`: `myinvestetf.research_health.v1`
- `replay_reference_etf`
- `health_report.data_quality`
- `health_report.factor_quality`
- `health_report.regime_quality`
- `health_report.report_quality`
- `health_report.system_health_score`
- `health_report.gate_status`: `pass`、`warn` 或 `reject`

健康报告只读；因子 IC 健康检查使用代表性采样，并使用 120 秒短 TTL 缓存。

## `/api/health/data`

用途：输出数据完整性 gate。

关键字段：

- `data_quality.completeness_score`
- `data_quality.missing_data_ratio`
- `data_quality.stale_data_ratio`
- `data_quality.alignment_score`
- `data_quality.coverage_score`
- `data_quality.missing_fields`
- `data_quality.stale_items`
- `data_quality.gate_status`

## `/api/health/factors`

用途：输出因子有效性 gate。

关键字段：

- `factor_quality.ic_validity_score`
- `factor_quality.factor_coverage_score`
- `factor_quality.unstable_factors`
- `factor_quality.redundant_factors`
- `factor_quality.ic_decay_alerts`
- `factor_quality.gate_status`

## `/api/health/regime`

用途：输出 regime 稳定性 gate。

关键字段：

- `regime_quality.stability_score`
- `regime_quality.flip_rate`
- `regime_quality.smoothed_flip_rate`
- `regime_quality.regime_entropy`
- `regime_quality.confirmation_score`
- `regime_quality.overfit_warning`
- `regime_quality.gate_status`

## `/api/health/report`

用途：输出研究报告质量 gate。

关键字段：

- `report_quality.completeness`
- `report_quality.consistency`
- `report_quality.leakage_risk`
- `report_quality.interpretability`
- `report_quality.rejection_reasons`
- `report_quality.gate_status`

## `/api/market/structure`

用途：输出市场结构层。

关键字段：

- `market_structure.index_breadth`
- `market_structure.sector_breadth`
- `market_structure.advance_decline_ratio`
- `market_structure.liquidity_breadth`
- `market_structure.dispersion`
- `market_structure.breadth_score`
- `market_structure.liquidity_score`
- `market_structure.dispersion_score`
- `market_structure.contributions`

## `/api/market/breadth`

用途：输出市场宽度摘要，包括 breadth contribution。

## `/api/market/liquidity`

用途：输出流动性结构摘要，包括 liquidity contribution。

## `/api/market/regime-v2`

用途：输出结构驱动的市场状态。

关键字段：

- `market_structure`
- `items[].code`
- `items[].taxonomy_profile`
- `items[].regime_v2.regime`
- `items[].regime_v2.confidence`
- `items[].regime_v2.structure.breadth_score`
- `items[].regime_v2.structure.liquidity_score`
- `items[].regime_v2.structure.dispersion_score`
- `items[].regime_v2.confirmation_level`
- `items[].regime_v2.explanation`
- `items[].regime_v2.evidence.breadth_contribution`
- `items[].regime_v2.evidence.liquidity_contribution`

该接口只读，不写入 `/api/factors`、taxonomy、report 或数据库；Regime v2 会作为 `/api/score/*` 和 ETF 页面 DecisionSignal 的输入。

## 约束

所有接口只读，不包含交易指令、现金金额或份额数量。
