# ETF 深研队列提示词设计

## 目标

ETF 深研队列的提示词必须满足三个目标：

- Codex 单条任务只处理一只 ETF、一个 `research` 队列任务；自动化启动一次后可以连续处理多条队列任务。
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
- `task_type`: 固定为 `research`。
- `task_id` / `run_id`: 对应 `task_queue` 的可追踪 ID。
- `priority` / `stage`: 队列排序信息。
- `depends_on_task_type`: 统一为空。
- `source_type`: `broad_index_representative`、`defensive_representative`、`mainline_representative` 或 `manual_request`；历史任务可能仍为 `trackable_leader`。
- `source_detail`: 上游或手动入口说明。

这些元数据用于追踪自动化执行，不替代报告 schema。

## Research 提示词

`research` 是唯一 ETF 深研任务，必须一次性完成产品结构、底层指数、持仓披露、估值输入、类型化模型输入、风险和组合角色研究。

提示词必须包含：

- 唯一研究对象：`{code} {name}`。
- `report_id`、`basis_date`、主题/资产类别。
- `valuation_model_type` 和 `sleeve_key`。
- `taxonomy_profile`，包括 `etf_type`、`subtype`、`lifecycle_stage` 和分类理由。
- Tushare 优先数据源：`fund_basic`、`fund_daily`、`fund_nav`、`fund_share`、`fund_portfolio`、`index_daily`。
- `fund_portfolio` 只能作为已披露季报持仓，不得写成实时完整底仓。
- 输出必须构建 `research assembly_input`，最终报告由确定性脚本生成。
- `task_type` 固定为 `research`。
- 禁止输出交易指令、现金金额、份额数量。

Research 必须回答：

- 基金类型、跟踪指数、费率、规模和流动性。
- 底层指数编制逻辑、行业/主题暴露和集中度。
- ETF 属于核心宽基、主线进攻、收益防御、现金替代还是不适合作为组合工具。
- 持仓披露日期、前十大持仓、集中度、披露滞后和数据缺口。
- 净值、价格、折溢价、底层指数 PE/PB、估值分位和类型化 `model_specific_inputs`。
- ETF taxonomy；所有 ETF 评分必须绑定 taxonomy，不能只按旧四类估值模型解释。
- ETF 日行情和底层指数日行情；如可取得，写入 `assembly_input.price_series` 和 `assembly_input.index_price_series`，由系统生成市场状态与回撤。
- 标准化因子只由系统计算，必须保留 `as_of_date`、`lookback_window`、`source` 和 `leakage_guard`，不得在提示词中手工编造。
- 跟踪质量、流动性、证伪条件和组合角色。

执行流程：

```powershell
python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite temp/assembly_inputs/{code}_research_{basis_date}.json > temp/reports/{code}_research_{basis_date}.json
python scripts/import_research_run.py temp/reports/{code}_research_{basis_date}.json
```

LLM 只能负责收集、清洗、归一化输入和解释脚本输出，不能重新计算参考价值区间、signal、grade、`report_hash` 或 `run_id`。
taxonomy_profile 由系统根据 ETF 元数据、跟踪指数、行业/主题暴露、波动和流动性线索生成；LLM 只能提供证据，不手写最终分类结论。
市场状态 `market_context.regime` 与回撤 `market_context.drawdown` 也由系统根据行情序列生成；LLM 不手写这些最终字段。
factor exposure 与 IC 由 `core/factors` 根据本地行情计算；LLM 只提供数据来源和缺口说明。
MarketStructure 与 Regime v2 由系统根据 ETF 池行情、taxonomy 和流动性代理生成；LLM 不手写 breadth、liquidity contribution 或 confirmation_level。

## 类型化研究依据

不同 ETF 的研究依据完全不同，提示词必须按 `valuation_model_type` 分流：

| 类型 | 五仓角色 | 研究依据 |
| --- | --- | --- |
| `broad_index` | `core_wide_etf` | 宽基 PE/PB 分位、股权风险溢价、ROE、市场仓位、折溢价、流动性和跟踪质量。 |
| `mainline_theme` | `mainline_etf` | 主线有效性、行业资金、成交持续、估值容错和拥挤退潮风险。 |
| `factor_defensive` | `defensive_quality` | 红利低波的股息利差、低波稳定性，或自由现金流 ETF 的 FCF yield、质量因子和风格机会成本。 |
| `cash_like` | `cash_like` | 不进入深度研究队列，只做现金替代资格监控：流动性、折溢价异常、久期、信用和收益稳定性。 |

## 自动化执行规则

Codex 自动化启动一次后必须循环消化队列：

1. 运行 `python scripts/ingest_index.py` 刷新当前可跟踪 ETF 队列。
2. 运行 `python scripts/generate_single_etf_prompt.py --next --claim` 领取一条 `research` 任务。
3. 如果没有可领取任务，验证 `http://127.0.0.1:8017/api/index` 和 `/api/latest`，汇报本次累计处理结果，然后结束。
4. 如果领取到任务，按提示词产出 `assembly_input`，再通过确定性脚本生成并导入报告。
5. 成功导入后确认 `task_queue` 状态为 `DONE`；失败时进入 `FAILED` 或 `BLOCKED`，不要让 `RUNNING` 永久卡住。
6. 每完成一条任务后，如果仍有可领取任务，等待 10 分钟，再回到第 2 步。
7. 必须持续循环，直到 `generate_single_etf_prompt.py --next --claim` 返回没有可领取任务。

自动化不得：

- 把多只 ETF 合并为一条研究任务或一个研究结论。
- 研究同类低成交额 ETF 替代品，除非用户手动点名。
- 重新引入旧的两阶段研究任务。
- 把上游可跟踪 ETF 当成买入清单。
- 输出交易指令、现金金额或份额数量。
