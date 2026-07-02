# MyInvestETF

MyInvestETF 是一个 A 股 ETF 研究与估值工作台，用来沉淀单只 ETF 的产品结构、底层指数、持仓披露、流动性、折溢价、份额变化、跟踪质量和底仓/工具仓资格。

系统定位是“研究与展示”，不是交易系统。除 `/research` 主动研究入口只写入本地研究队列外，页面展示和 `/api/*` 接口均为只读输出，不生成买卖指令、不输出现金金额、不输出份额数量。

## 一句话逻辑

每只 ETF 只生成一个 `research` 完整深研任务。这个任务一次性完成产品结构、底层指数、持仓披露、估值输入、类型化模型输入、风险和组合角色研究，再由确定性引擎生成参考价值区间、signal、市场状态与回撤上下文、`ETFResearchReport` 和页面展示结果。

## 核心边界

- 研究对象必须是唯一 ETF 代码，例如 `510300.SH` 或 `159915.SZ`。
- 深研必须一次只研究一只 ETF、一个 `research` 任务。
- ETF 研究不再区分 `profile` 和 `valuation` 两个任务类型。
- `research` 必须识别 `valuation_model_type`: `broad_index`、`mainline_theme`、`factor_defensive`、`cash_like`。
- `research` 必须识别 `sleeve_key`: `core_wide_etf`、`mainline_etf`、`defensive_quality`、`cash_like`。
- `research` 必须绑定 `taxonomy_profile`；taxonomy 是认知和路由层，不替代现有四类估值模型。
- 短融、日利、货币、现金类 ETF 归为 `cash_like`，默认不进入深度研究队列，只作为现金替代资格监控对象。
- 新研究结果必须符合 `core/schema/etf_report.py` 的 `ETFResearchReport` schema，入库前强制校验。
- `run_id` 由 `etf_code + task_type + research_date + schema_version` 计算，数据库强制唯一。
- 队列任务使用 `core/task/state.py` 的状态机：`PENDING -> RUNNING -> DONE/FAILED/BLOCKED`。
- `task_queue` 是唯一状态源；`research_queue` 只作为 prompt/projection/UI 表。
- 参考价值区间和 signal 由 `core/valuation` 的确定性评分引擎生成，LLM 只负责构建输入和解释，不负责最终计算。
- 市场状态和回撤由 `core/market`、`core/risk` 旁路生成，只作为研究上下文展示；当前版本不改变既有 ETF 类型化评分。
- ETF taxonomy 由 `core/taxonomy` 旁路生成，只增强产品分类、队列路由和 API profile；当前版本不改变既有 signal。
- 标准化因子由 `core/factors` 生成，所有因子带 `as_of_date`、`lookback_window`、`source` 和 `leakage_guard`；当前版本只展示因子暴露和 IC，不改最终评分。
- 市场结构由 `core/market/structure.py` 生成，当前用 ETF 池代理 breadth、liquidity 和 dispersion；Regime v2 使用结构输入，但仍不改最终评分。
- `fund_portfolio` 只能作为已披露季报持仓，不等同实时完整底仓；缺口必须写入 `data_gaps`。
- Web 默认端口固定为 `8017`。
- 页面首尾统一加载 `https://invest.okbbc.com/header.js` 和 `https://invest.okbbc.com/footer.js`。
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
        |       每次领取一条 ETF 完整深研任务
        |
        +--> scripts/build_research_report.py
        |       从 research assembly_input 生成确定性 ETFResearchReport
        |       旁路生成 MarketContext: regime + drawdown
        |
        +--> scripts/import_research_run.py
        |       导入已校验 ETFResearchReport
        |
        v
myinvestetf/web.py
  /                  Web 首页
  /api               统一接口目录
  /docs              浏览器接口文档
  /redoc             浏览器接口文档
  /openapi.json      OpenAPI 描述
  /research?etf=     主动研究入队与跳转
  /etfs/{code}       ETF 详情页
  /api/index         对外主结果
  /api/latest        对外研究成果
  /api/queue         本地队列
```

## ETF 类型化研究依据

| 类型 | 五仓角色 | 研究依据 |
| --- | --- | --- |
| `broad_index` | `core_wide_etf` | 底层宽基 PE/PB 分位、股权风险溢价、ROE、市场仓位分、折溢价、流动性和跟踪质量。 |
| `mainline_theme` | `mainline_etf` | 主线有效性、行业资金、成交持续、估值容错和拥挤退潮风险。 |
| `factor_defensive` | `defensive_quality` | 红利低波的股息利差、低波稳定性，或自由现金流 ETF 的 FCF yield、质量因子和风格机会成本。 |
| `cash_like` | `cash_like` | 不做传统权益估值；只监控流动性、折溢价异常、久期风险、信用风险和收益稳定性。 |

## ETF Taxonomy

`taxonomy_profile` 将 ETF 细分为：

- `broad_index_core`
- `broad_index_growth`
- `broad_index_value`
- `sector_cyclical`
- `sector_structural`
- `theme_lifecycle`
- `factor_strategy`
- `cash_equivalent`
- `bond_etf`
- `commodity_etf`

字段包括 `etf_type`、`subtype`、`lifecycle_stage`、`classification_confidence`、`classification_reasons`、`legacy_valuation_model_type` 和 `legacy_sleeve_key`。其中 `legacy_*` 用来保证新 taxonomy 与旧评分入口兼容。

主题 ETF 的 `lifecycle_stage` 可为 `early`、`expansion`、`crowded`、`distribution`、`collapse`。当前生命周期只做分类说明，不直接改变估值评分。

## 市场状态与回撤

系统为每只有本地日行情的 ETF 生成 `market_context`：

- `regime`: `risk_on`、`risk_off`、`shock`、`rotation`，由趋势、波动和流动性代理判断。
- `confidence`: 市场状态置信度。
- `drawdown.current_drawdown`: 最新收盘价相对本轮高点的回撤。
- `drawdown.max_drawdown_rolling`: 当前行情序列中的最大回撤。
- `drawdown.drawdown_percentile`: 当前回撤在历史回撤中的严重程度分位。
- `drawdown.recovery_speed`: 从本轮低点到最新收盘价的日均修复速度。
- `drawdown.duration_days`: 从本轮高点以来的交易日数。

该层用于解释“当前是追涨、轮动、风险收缩还是冲击下跌”，并为后续状态机评分和回测提供上下文。它不直接给出买卖建议，也不在本版本改变 `ETFValuationSignal` 分数。

## 市场结构与 Regime v2

`MarketStructure` 输出：

- `index_breadth`
- `sector_breadth`
- `advance_decline_ratio`
- `liquidity_breadth`
- `dispersion`
- `breadth_score`
- `liquidity_score`
- `dispersion_score`
- `contributions`

Regime v2 的组合权重为：40% 价格趋势、30% 宽度、20% 流动性、10% 波动。它额外输出 `confirmation_level`，用于解释“价格趋势与市场结构是否互相确认”。当前版本使用 ETF 池作为 breadth 代理；未来可替换为指数成分股上涨/下跌家数、行业扩散和资金流结构。

## 因子标准化与 IC

`core/factors` 提供四层能力：

- Factor Standardization：输出 `raw_value`、`normalized_value`、`z_score`、`percentile`、`as_of_date`、`lookback_window`、`source`、`leakage_guard`。
- Point-in-Time：默认使用 `point_in_time_lag_1`，避免用最新未知数据计算当前因子。
- IC Analysis：计算因子相对 5/20/60 日 forward return 的 IC 摘要。
- Factor Registry：按 taxonomy 选择可用因子集合。

当前内置因子包括 `price_momentum_20`、`price_momentum_60`、`volatility_20`、`drawdown_current`、`liquidity_trend_20`。这些因子只用于研究暴露、归因和 IC 验证，不直接改写 `ETFValuationSignal`。

## 完整深研任务

`research` 任务必须覆盖：

- 产品结构：基金类型、跟踪指数、资产类别、费率、规模和流动性。
- 底层指数：指数编制逻辑、行业/主题暴露、适合宽基/行业/主题/债券/现金替代的哪种角色。
- 底仓逻辑：是否适合作为底仓、工具仓、防守仓、现金替代或卫星仓。
- 持仓披露：前十大持仓、披露日期、集中度、披露滞后和实时完整持仓缺口。
- 跟踪质量：跟踪误差、折溢价、流动性和指数复制风险。
- 估值输入：净值、价格、折溢价、底层指数 PE/PB、估值分位和类型化 `model_specific_inputs`。
- 行情上下文：如能取得 ETF 和底层指数日行情，可在 `assembly_input.price_series` / `index_price_series` 中提供；最终 `market_context` 由系统生成。
- 证伪条件：哪些规模、流动性、跟踪、指数估值或持仓变化会推翻当前角色判断。

LLM 负责收集 Tushare 和必要网络补充资料，构建 `assembly_input`。系统负责通过 `core/valuation` 和 `core/report` 生成最终 `ETFResearchReport`、`report_hash`、参考价值区间和结论等级。

限制：

- 不手写最终 `ETFResearchReport`。
- 不重新计算参考价值区间、signal、grade、`report_hash` 或 `run_id`。
- 不输出交易指令、现金金额或份额数量。

## 数据原则

- 可跟踪 ETF 默认入口为 `https://theme.okbbc.com/api/latest` 的 `result.theme_ranking[].top_etf` 和 `result.etf_top`，并兼容旧 `key_results.primary_output.items` 结构。
- 每条主线至少保留一个 ETF 代表进入研究队列；同一主线内优先选择有成交额且成交额最大的 ETF，缺成交额时使用 `top_etf` 的第一位。
- 本地独立补齐上证综指、上证50、沪深300、中证500、中证1000、创业板、科创50 等核心宽基 ETF 研究对象；宽基研究来源不标记为可跟踪主线龙头。
- ETF 深研队列先显示核心宽基代表，再显示主线代表；来源分别为 `核心宽基` 和 `主线代表`。
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

更新可跟踪 ETF 数据：

```powershell
python scripts/ingest_index.py
```

启动 Web：

```powershell
python scripts/run_web.py
```

打开：

```text
http://127.0.0.1:8017/
```

统一接口目录：

```text
http://127.0.0.1:8017/api
```

主动加入 ETF 研究队列：

```text
http://127.0.0.1:8017/research?etf=510300.SH&name=沪深300ETF
```

领取下一条完整深研任务：

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

- `/`：ETF 首页，显示当前 ETF、本地研究队列和“接口说明”模块。
- `/api`：统一接口目录，返回系统名称、版本、说明、`base_url`、文档入口、推荐入口、安全边界、分组和公开入口总数。
- `/docs`：轻量浏览器接口文档。
- `/redoc`：ReDoc 风格轻量浏览器接口文档。
- `/openapi.json`：当前公开接口的 OpenAPI 3.0 描述。
- `/etfs/{code}`：ETF 详情页，显示参考价格区间历史、产品结构、持仓披露、估值与流动性、风险与证伪、研究历史。
- `/research?etf={code}`：主动研究入口；没有详情页时入队并跳转，有详情页时直接跳转。
- `/api/index`：对外主结果接口。
- `/api/latest`：对外研究成果接口，包含每只 ETF 的 `taxonomy_profile` 和 `market_context`。
- `/api/queue`：本地研究队列接口。
- `/api/etfs`：当前 ETF 列表。
- `/api/etfs/{code}`：单只 ETF 研究数据、`taxonomy_profile`、`market_context`、队列状态和历史。
- `/api/etf/{code}/profile`：单只 ETF taxonomy profile。
- `/api/factors/{etf}`：单只 ETF 标准化因子暴露。
- `/api/factors/exposure/{etf}`：单只 ETF 因子暴露显式别名。
- `/api/factors/ic/{factor}`：单个因子的 5/20/60 日 IC 摘要。
- `/api/market/structure`：市场结构层。
- `/api/market/breadth`：市场宽度摘要。
- `/api/market/liquidity`：流动性结构摘要。
- `/api/market/regime-v2`：结构驱动的市场状态。

接口目录按“文档入口、Web 页面、当前数据、历史数据、分析结果、系统状态”分组。`/api` 只返回说明，不触发重计算、写入、交易、同步或外部请求；所有 `/api/*` 数据接口只读取本地结果。`/research` 是 Web 主动研究入口，可能写入本地研究队列，因此在目录中会单独标记为非只读公开路由。
