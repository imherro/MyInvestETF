# ETF 深研队列提示词设计

## 目标

ETF 深研队列的提示词必须满足三个目标：

- Codex 每次只处理一只 ETF、一个 `research` 队列任务。
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
- `source_type`: `trackable_leader` 或 `manual_request`。
- `source_detail`: 上游或手动入口说明。

这些元数据用于追踪自动化执行，不替代报告 schema。

## Research 提示词

`research` 是唯一 ETF 深研任务，必须一次性完成产品结构、底层指数、持仓披露、估值输入、类型化模型输入、风险和组合角色研究。

提示词必须包含：

- 唯一研究对象：`{code} {name}`。
- `report_id`、`basis_date`、主题/资产类别。
- `valuation_model_type` 和 `sleeve_key`。
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
- 跟踪质量、流动性、证伪条件和组合角色。

执行流程：

```powershell
python scripts/build_research_report.py --audit-db data/local/myinvestetf.sqlite temp/assembly_inputs/{code}_research_{basis_date}.json > temp/reports/{code}_research_{basis_date}.json
python scripts/import_research_run.py temp/reports/{code}_research_{basis_date}.json
```

LLM 只能负责收集、清洗、归一化输入和解释脚本输出，不能重新计算参考价值区间、signal、grade、`report_hash` 或 `run_id`。

## 类型化研究依据

不同 ETF 的研究依据完全不同，提示词必须按 `valuation_model_type` 分流：

| 类型 | 五仓角色 | 研究依据 |
| --- | --- | --- |
| `broad_index` | `core_wide_etf` | 宽基 PE/PB 分位、股权风险溢价、ROE、市场仓位、折溢价、流动性和跟踪质量。 |
| `mainline_theme` | `mainline_etf` | 主线有效性、行业资金、成交持续、估值容错和拥挤退潮风险。 |
| `factor_defensive` | `defensive_quality` | 红利低波的股息利差、低波稳定性，或自由现金流 ETF 的 FCF yield、质量因子和风格机会成本。 |
| `cash_like` | `cash_like` | 不进入深度研究队列，只做现金替代资格监控：流动性、折溢价异常、久期、信用和收益稳定性。 |

## 自动化执行规则

Codex 自动化每小时运行一次：

1. 运行 `python scripts/ingest_index.py` 刷新当前可跟踪 ETF 队列。
2. 运行 `python scripts/generate_single_etf_prompt.py --next --claim` 领取一条 `research` 任务。
3. 如果没有可领取任务，验证 `http://127.0.0.1:8017/api/index` 和 `/api/latest`，然后结束。
4. 如果领取到任务，按提示词产出 `assembly_input`，再通过确定性脚本生成并导入报告。
5. 成功导入后确认 `task_queue` 状态为 `DONE`；失败时进入 `FAILED` 或 `BLOCKED`，不要让 `RUNNING` 永久卡住。

自动化不得：

- 一次处理多只 ETF。
- 研究同类低成交额 ETF 替代品，除非用户手动点名。
- 重新引入旧的两阶段研究任务。
- 把上游可跟踪 ETF 当成买入清单。
- 输出交易指令、现金金额或份额数量。
