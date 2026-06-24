# API Contract

本地服务默认运行在：

```text
http://127.0.0.1:8017
```

## `/api/index`

用途：输出 ETF 主结果信息，供其他系统集成。

关键字段：

- `schema_version`: `myinvestetf.index.v1`
- `key_results.primary_output.items`: 当前 ETF 列表
- `key_results.primary_output.items[].category_key`: 去重后的 ETF 投资暴露类别
- `key_results.primary_output.items[].valuation_model_type`: `broad_index`、`mainline_theme`、`factor_defensive` 或 `cash_like`
- `key_results.primary_output.items[].sleeve_key`: `core_wide_etf`、`mainline_etf`、`defensive_quality` 或 `cash_like`
- `source.upstream_endpoint`: 默认 `https://theme.okbbc.com/api/latest`
- `source.upstream_result_path`: 默认 `result.etf_top`，兼容旧 `key_results.primary_output.items`
- `source.source_policy`: 同一 ETF 投资暴露只保留成交额最大的代表进入本地结果；半导体材料设备、芯片、半导体产业会合并为同一研究类。
- `links.latest`: `/api/latest`
- `constraints.read_only`: `true`
- `constraints.contains_trade_orders`: `false`
- `constraints.contains_cash_amounts`: `false`
- `constraints.contains_share_counts`: `false`

## `/api/latest`

用途：输出当前研究成果。

关键字段：

- `schema_version`: `myinvestetf.research.v1`
- `summary.etf_count`
- `summary.research_run_count`
- `summary.valuation_run_count`
- `etfs[].research.profile`
- `etfs[].research.valuation`
- `etfs[].leader.valuation_model_type`
- `etfs[].leader.sleeve_key`
- `etfs[].research.valuation_history`
- `etfs[].decision_matrix`

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

- `profile`: ETF 产品结构深研
- `valuation`: ETF 估值刷新

队列策略：

- 只展示当前主线报告的 `trackable_leader` 任务，以及最新一次手动请求的 `manual_request` 任务；历史报告队列保留在数据库中但不作为当前队列输出。
- `broad_index`、`mainline_theme`、`factor_defensive` 会进入 `profile -> valuation`。
- `cash_like` 不进入深度研究队列，只作为现金替代资格监控对象。

来源字段：

- `source_type`: `trackable_leader` 或 `manual_request`
- `source_label`: `可跟踪龙头` 或 `其他请求`

## `/api/etfs/{code}`

用途：输出单只 ETF 页面数据。

关键字段：

- `leader_summary`: 入口摘要，没有则为 `null`
- `leader_summary.valuation_model_type`
- `leader_summary.sleeve_key`
- `research_runs`: 研究历史
- `decision_matrix`: 产品信号与 ETF 估值适配矩阵
- `queue`: 队列状态
- `trackable_history`: 历史入口记录

## 约束

所有接口只读，不包含交易指令、现金金额或份额数量。
