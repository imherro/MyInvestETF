# ETF 深研队列提示词设计

## 目标

ETF 深研队列的提示词必须满足三个目标：

- Codex 每次只处理一只 ETF、一个队列任务。
- 任务提示词可以直接执行，不依赖聊天上下文补充关键信息。
- 研究输出只进入 `ETFResearchReport` 和本地数据库，不扩展成交易指令、现金金额或份额数量。

队列领取入口固定为：

```powershell
python scripts/generate_single_etf_prompt.py --next --claim
```

该命令输出一个标准化执行包：

1. `task_keyword`
2. 队列任务元数据
3. Codex 执行边界
4. 队列任务提示词正文

自动化任务必须把这个输出视为唯一任务来源，不从 `/api/index` 或上游接口临时扩展研究对象。

## 队列任务元数据

每个提示词执行包必须包含：

- `report_id`: 当前上游报告或手动请求批次。
- `code`: 唯一 ETF 代码。
- `name`: ETF 名称。
- `task_type`: `profile` 或 `valuation`。
- `task_id` / `run_id`: 对应 `task_queue` 的可追踪 ID。
- `priority` / `stage`: 队列排序信息。
- `depends_on_task_type`: 前置任务类型；`valuation` 固定依赖 `profile`。
- `source_type`: `trackable_leader` 或 `manual_request`。
- `source_detail`: 上游或手动入口说明。

这些元数据用于追踪自动化执行，不替代报告 schema。

## Profile 提示词

`profile` 是产品结构深研任务，只做底稿，不做最终参考价值区间。

提示词必须包含：

- 唯一研究对象：`{code} {name}`。
- `report_id`、`basis_date`、主题/资产类别。
- `valuation_model_type` 和 `sleeve_key`。
- Tushare 优先数据源：`fund_basic`、`fund_daily`、`fund_nav`、`fund_share`、`fund_portfolio`、`index_daily`。
- `fund_portfolio` 只能作为已披露季报持仓，不得写成实时完整底仓。
- 输出必须符合 `ETFResearchReport`。
- `task_type` 必须是 `profile`。
- `valuation.reference_value_low/mid/high` 必须为 `null`。
- 禁止输出交易指令、现金金额、份额数量。

Profile 必须回答：

- 基金类型、跟踪指数、费率、规模和流动性。
- 底层指数编制逻辑、行业/主题暴露和集中度。
- ETF 属于核心宽基、主线进攻、收益防御、现金替代还是不适合作为组合工具。
- 持仓披露日期、前十大持仓、集中度、披露滞后和数据缺口。
- 跟踪质量、折溢价、流动性和证伪条件。

## Valuation 提示词

`valuation` 是类型化估值刷新任务，只构建 deterministic report 的输入，不手写最终报告。

提示词必须包含：

- 先读取同一 ETF 最新 `task_type='profile'` 记录。
- 如果 profile 不存在，停止并把本任务标记为 `BLOCKED` 或失败原因明确的 blocked 状态。
- 构建 `assembly_input`，包含：
  - `etf_code`, `etf_name`, `source_report_id`, `task_type`, `research_date`
  - `valuation_model_type`, `sleeve_key`
  - `product_profile`
  - `holdings_inputs`
  - `valuation_inputs`
  - `model_specific_inputs`
  - `liquidity_inputs`
  - `tracking_inputs`
  - `risk_signals`
  - `evidence`, `assumptions`, `data_gaps`
- 运行确定性报告生成：

```powershell
python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite temp/assembly_inputs/{code}_valuation_{basis_date}.json > temp/reports/{code}_valuation_{basis_date}.json
python scripts/import_research_run.py temp/reports/{code}_valuation_{basis_date}.json
```

LLM 只能负责收集、清洗、归一化输入和解释脚本输出，不能重新计算参考价值区间、signal、grade、`report_hash` 或 `run_id`。

## 类型化估值依据

不同 ETF 的投资建议依据完全不同，提示词必须按 `valuation_model_type` 分流：

| 类型 | 五仓角色 | 研究依据 |
| --- | --- | --- |
| `broad_index` | `core_wide_etf` | 宽基 PE/PB 分位、股权风险溢价、ROE、市场仓位、折溢价、流动性和跟踪质量。 |
| `mainline_theme` | `mainline_etf` | 主线有效性、行业资金、成交持续、估值容错和拥挤退潮风险。 |
| `factor_defensive` | `defensive_quality` | 红利低波的股息利差、低波稳定性，或自由现金流 ETF 的 FCF yield、质量因子和风格机会成本。 |
| `cash_like` | `cash_like` | 不进入深度估值队列，只做现金替代资格监控：流动性、折溢价异常、久期、信用和收益稳定性。 |

## 自动化执行规则

Codex 自动化每小时运行一次：

1. 运行 `python scripts/ingest_index.py` 刷新当前可跟踪 ETF 队列。
2. 运行 `python scripts/generate_single_etf_prompt.py --next --claim` 领取一条任务。
3. 如果没有可领取任务，验证 `http://127.0.0.1:8017/api/index` 和 `/api/latest`，然后结束。
4. 如果领取到 `profile`，按 profile 提示词产出并导入 `ETFResearchReport`。
5. 如果领取到 `valuation`，按 valuation 提示词产出 `assembly_input`，再通过确定性脚本生成并导入报告。
6. 成功导入后确认 `task_queue` 状态为 `DONE`；失败时进入 `FAILED` 或 `BLOCKED`，不要让 `RUNNING` 永久卡住。

自动化不得：

- 一次处理多只 ETF。
- 研究同类低成交额 ETF 替代品，除非用户手动点名。
- 绕过 `profile -> valuation` 依赖。
- 把上游可跟踪 ETF 当成买入清单。
- 输出交易指令、现金金额或份额数量。
