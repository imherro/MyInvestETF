# API Contract

本地服务默认运行在：

```text
http://127.0.0.1:8017
```

## `/api/index`

用途：输出 ETF 主结果信息，供其他系统集成。

关键字段：

- `schema_version`: `myinvestetf.index.v1`
- `key_results.primary_output.items`: 当前 ETF 池列表，包括主线接口 ETF 和本地核心宽基种子
- `key_results.primary_output.items[].category_key`: ETF 投资暴露类别
- `key_results.primary_output.items[].valuation_model_type`: `broad_index`、`mainline_theme`、`factor_defensive` 或 `cash_like`
- `key_results.primary_output.items[].sleeve_key`: `core_wide_etf`、`mainline_etf`、`defensive_quality` 或 `cash_like`
- `source.upstream_endpoint`: 默认 `https://theme.okbbc.com/api/latest`
- `source.upstream_result_path`: 默认 `result.theme_ranking[].top_etf + result.etf_top`，兼容旧 `key_results.primary_output.items`
- `source.source_policy`: `/api/index` 保留 ETF 池；本地深研队列先列核心宽基 ETF 代表，再按每条主线保留一个流动性代表。
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
- `etfs[].research.latest`
- `etfs[].research.reference_value_history`
- `etfs[].research.run_count`
- `etfs[].market_context`: 市场状态和回撤上下文
- `etfs[].decision_matrix`

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

- 只展示当前主线报告的 `mainline_representative`、`broad_index_representative`、`defensive_representative` 任务，以及最新一次手动请求的 `manual_request` 任务；历史报告队列保留在数据库中但不作为当前队列输出。
- `trackable_leader` 仅作为历史兼容来源保留，不再作为当前自动队列的新写入来源。
- `broad_index`、`mainline_theme`、`factor_defensive` 会进入单一 `research` 队列。
- `cash_like` 不进入深度研究队列，只作为现金替代资格监控对象。

来源字段：

- `source_type`: `broad_index_representative`、`defensive_representative`、`mainline_representative`、`manual_request`；历史数据可能还有 `trackable_leader`
- `source_label`: `核心宽基`、`收益防御代表`、`主线代表`、`其他请求`

## `/api/etfs/{code}`

用途：输出单只 ETF 页面数据。

关键字段：

- `leader_summary`: 入口摘要，没有则为 `null`
- `leader_summary.valuation_model_type`
- `leader_summary.sleeve_key`
- `research_runs`: 研究历史，任务类型统一为 `research`
- `market_context`: `regime` 与 `drawdown`，由本地日行情缓存计算
- `decision_matrix`: 产品信号与 ETF 估值适配矩阵
- `queue`: 队列状态
- `trackable_history`: 历史入口记录

## 约束

所有接口只读，不包含交易指令、现金金额或份额数量。
