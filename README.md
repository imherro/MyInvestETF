# MyInvestETF

MyInvestETF 是一个 A 股 ETF 研究与估值工作台，用来沉淀单只 ETF 的产品结构、底层指数、持仓披露、流动性、折溢价、份额变化、跟踪质量和底仓/工具仓资格。

系统定位是“研究与展示”，不是交易系统。页面和接口均为只读输出，不生成买卖指令、不输出现金金额、不输出份额数量。

## 一句话逻辑

每只 ETF 拆成两个研究阶段：`profile` 生成产品结构底稿，`valuation` 多次刷新净值、折溢价、底层指数估值、流动性、份额变化和底仓适配评分，并在 ETF 详情页叠加历史参考价值区间。

## 核心边界

- 研究对象必须是唯一 ETF 代码，例如 `510300.SH` 或 `159915.SZ`。
- 深研必须一次只研究一只 ETF。
- `profile` 是产品结构、底层指数、持仓披露、跟踪质量和组合角色底稿，默认只做一次。
- `valuation` 是估值刷新任务，可以随着净值、价格、份额、流动性和底层指数估值多次刷新。
- `valuation` 必须依赖已完成的 `profile` 底稿；产品结构未完成时不提前领取估值任务。
- 新研究结果必须符合 `core/schema/etf_report.py` 的 `ETFResearchReport` schema，入库前强制校验。
- `run_id` 由 `etf_code + task_type + research_date + schema_version` 计算，数据库强制唯一。
- 队列任务使用 `core/task/state.py` 的状态机：`PENDING -> RUNNING -> DONE/FAILED/BLOCKED`。
- `task_queue` 是唯一状态源；`research_queue` 只作为 prompt/projection/UI 表。
- `valuation` 的参考价值区间和 signal 由 `core/valuation` 的确定性评分引擎生成，LLM 只负责构建输入和解释，不负责最终计算。
- `fund_portfolio` 只能作为已披露季报持仓，不等同实时完整底仓；缺口必须写入 `data_gaps`。
- Web 默认端口固定为 `8017`。
- 页面 footer 统一加载 `https://invest.okbbc.com/footer.js`。
- `.env`、本地 SQLite、原始抓取 JSON 和临时产物不提交。

## 系统架构

```text
ETF 输入 / 主动研究入口
        |
        v
SQLite:
  leader_reports
  trackable_leaders
  task_queue
  research_queue
  etf_research_runs
  etf_daily_prices
  audit_log
        |
        +--> scripts/generate_single_etf_prompt.py
        |       每次领取一条 ETF 研究任务
        |
        +--> scripts/import_research_run.py
        |       导入已校验 ETFResearchReport
        |
        +--> scripts/build_research_report.py
        |       从结构化输入生成确定性 ETFResearchReport
        |
        v
myinvestetf/web.py
  /                  Web 首页
  /research?etf=     主动研究入队与跳转
  /etfs/{code}       ETF 详情页
  /api/index         对外主结果
  /api/latest        对外研究成果
  /api/queue         本地队列
```

## 确定性 ETF 估值引擎

`core/valuation` 是非 LLM 的 ETF 估值和适配评分层，目标是 same input -> same output。

- `features.py`：抽取估值分位、折溢价、成交额、规模、份额变化、跟踪误差和集中度。
- `models.py`：基于净值和底层指数估值位置生成参考价值区间。
- `signal.py`：输出 `undervalued_score`、`liquidity_score`、`tracking_score`、`portfolio_role_score`、`risk_adjusted_score`。

`ETFResearchReport.valuation` 承接 `engine_version` 和五个 signal 分数，保证参考价值区间和底仓资格不由 prompt 临场生成。

## 研究任务

### `profile`

用途：形成 ETF 产品结构底稿。

必须覆盖：

- 产品结构：基金类型、跟踪指数、资产类别、费率、规模和流动性。
- 底层指数：指数编制逻辑、行业/主题暴露、适合作为什么组合角色。
- 底仓逻辑：是否适合作为底仓、工具仓、防守仓、现金替代或卫星仓。
- 持仓披露：前十大持仓、披露日期、集中度、披露滞后和实时完整持仓缺口。
- 跟踪质量：跟踪误差、折溢价、流动性和指数复制风险。
- 证伪条件：哪些规模、流动性、跟踪、估值或持仓变化会推翻当前角色判断。

限制：

- 不写参考价值区间。
- 不写买卖建议。
- 不输出现金金额或份额数量。

### `valuation`

用途：构建结构化 ETF 估值输入，并刷新确定性报告。

LLM 负责：

- 收集 Tushare 和必要网络补充资料。
- 构建 `assembly_input`：`product_profile`、`valuation_inputs`、`liquidity_inputs`、`tracking_inputs`、`holdings_inputs`、`risk_signals`、`evidence`、`assumptions`、`data_gaps`。
- 解释系统生成的报告，但不修改报告数值或结论。

系统负责：

- `core/valuation` 计算参考价值区间和 signal。
- `core/report` 生成最终 `ETFResearchReport`、`report_hash` 和结论等级。
- `core/observability` 写入审计 trace。

限制：

- 不手写最终 `ETFResearchReport`。
- 不重新计算参考价值区间。
- 不修改 `valuation`、`risk`、`conclusion`、`report_hash`。
- 不输出交易指令、现金金额或份额数量。

## 数据原则

- Tushare 是 ETF 结构化主源，通过本地 `.env` 读取 token。
- 优先接口：`fund_basic`、`fund_daily`、`fund_nav`、`fund_share`、`fund_portfolio`、`index_daily`。
- 网络资料只作为补充证据，必须记录来源、日期和用途。
- 不在日志、页面、接口、审计包中输出 token。
- 本地数据库在 `data/local/`，默认不提交。
- 原始接口快照在 `data/raw/`，默认不提交。

## 快速开始

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

启动 Web：

```powershell
python scripts/run_web.py
```

打开：

```text
http://127.0.0.1:8017/
```

主动加入 ETF 研究队列：

```text
http://127.0.0.1:8017/research?etf=510300.SH&name=沪深300ETF
```

查看下一条待研究任务：

```powershell
python scripts/generate_single_etf_prompt.py --next
```

领取下一条任务并标记为处理中：

```powershell
python scripts/generate_single_etf_prompt.py --next --claim
```

从结构化输入生成确定性报告：

```powershell
python scripts/build_research_report.py path\to\assembly_input.json
```

运行项目检查：

```powershell
python scripts/project_check.py
```

运行测试：

```powershell
python -m pytest tests -q
```

## Web 与接口

- `/`：ETF 首页，显示当前 ETF 和本地研究队列。
- `/etfs/{code}`：ETF 详情页，显示参考价值区间历史、产品结构、持仓披露、估值与流动性、风险与证伪、研究历史。
- `/research?etf={code}`：主动研究入口；没有详情页时入队并跳转，有详情页时直接跳转。
- `/api/index`：对外主结果接口。
- `/api/latest`：对外研究成果接口。
- `/api/queue`：本地研究队列接口。
- `/api/etfs`：当前 ETF 列表。
- `/api/etfs/{code}`：单只 ETF 研究数据、队列状态和历史。
