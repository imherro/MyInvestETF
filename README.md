# MyInvestETF

MyInvestETF 是一个 A 股 ETF 研究与估值工作台，用来沉淀单只 ETF 的产品结构、底层指数、持仓披露、流动性、折溢价、份额变化、跟踪质量和组合使用判断。

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
- 市场状态和回撤由 `core/market`、`core/risk` 生成，既用于页面解释，也进入状态感知研究评分；收益防御/自由现金流 ETF 出现深回撤时会生成 `drawdown_opportunity_score`。
- ETF taxonomy 由 `core/taxonomy` 生成，增强产品分类、队列路由、API profile 和状态感知评分权重；不替代四类估值模型。
- 标准化因子由 `core/factors` 生成，所有因子带 `as_of_date`、`lookback_window`、`source` 和 `leakage_guard`；用于因子暴露、IC、DecisionSignal 组件评分和解释。
- 市场结构由 `core/market/structure.py` 生成，当前用 ETF 池代理 breadth、liquidity 和 dispersion；Regime v2 使用结构输入，并参与状态感知研究评分。
- 状态感知研究评分由 `core/decision` 生成，将 Regime v2、taxonomy、因子暴露和类型化估值组合成 `DecisionSignal`；它只输出研究评分、权重拆解、状态和解释，不输出交易动作、现金金额或份额数量。
- 策略层由 `core/strategy` 生成：Contrarian Mode 根据极端回撤、压力状态、波动压力、流动性压力和系统健康 gate 输出 `ContrarianSignal`；Strategy Router 在 trend、contrarian、neutral 间做解释层编排，不覆盖原始评分。
- 历史回放由 `core/replay` 生成，按 `as_of_date` 截断本地行情重建 DecisionSignal path，并输出稳定性、regime path、回撤敏感性和无未来函数校验。
- 研究治理由 `core/governance` 生成，输出数据完整性、因子有效性、regime 稳定性、报告质量和系统健康分，用来判断研究结果是否可信、是否应被 gate 阻断。
- 自然语言决策解释由 `core/interpreter` 生成，把已有 `DecisionSignal + taxonomy + governance` 翻译成结构化解释；最终回答统一由 `AnswerPolicyEngine` 生成。该层只读，不输出买卖动作、现金金额或份额数量。
- 问题意图识别由 `core/interpreter/question_router.py` 生成，把自然语言问题归类为 `buy_assessment`、`risk_assessment`、`market_state`、`comparison` 或 `unknown`；`unknown` 是合法结果，不强行分类。
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
  /api/score         单只 ETF 状态感知研究评分
  /api/ask           单只 ETF 自然语言决策解释
  /api/decision      单只 ETF 状态机输出
  /api/strategy      单只 ETF 抄底概率模式和策略路由
  /api/replay        单只 ETF 历史评分回放
  /api/health        系统研究健康度与治理 gate
  /api/queue         本地队列

core/interpreter
  DecisionInterpreter
        把现有结构化研究输出转成“是否适合参与”的只读结构化解释
  Question Router
        把自然语言问题转成稳定 QuestionIntent
  AnswerPolicyEngine
        统一生成 FinalAnswer，避免散落文本结论
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

主题 ETF 的 `lifecycle_stage` 可为 `early`、`expansion`、`crowded`、`distribution`、`collapse`。生命周期用于分类说明、研究路由和 DecisionSignal 权重解释。

## 市场状态与回撤

系统为每只有本地日行情的 ETF 生成 `market_context`：

- `regime`: `risk_on`、`risk_off`、`shock`、`rotation`，由趋势、波动和流动性代理判断。
- `confidence`: 市场状态置信度。
- `drawdown.current_drawdown`: 最新收盘价相对本轮高点的回撤。
- `drawdown.max_drawdown_rolling`: 当前行情序列中的最大回撤。
- `drawdown.drawdown_percentile`: 当前回撤在历史回撤中的严重程度分位。
- `drawdown.recovery_speed`: 从本轮低点到最新收盘价的日均修复速度。
- `drawdown.duration_days`: 从本轮高点以来的交易日数。

该层用于解释“当前是追涨、轮动、风险收缩还是冲击下跌”，并为状态机评分和回测提供输入。它不直接给出买卖建议，但会影响 `DecisionSignal`；对自由现金流、红利低波等收益防御 ETF，深回撤会提升 `drawdown_opportunity_score`，再进入估值组件。

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

当前内置因子包括 `price_momentum_20`、`price_momentum_60`、`volatility_20`、`drawdown_current`、`liquidity_trend_20`。这些因子用于研究暴露、归因、IC 验证和 `DecisionSignal` 组件评分；不会生成交易动作，也不会输出金额或份额数量。

## 状态感知研究评分

`core/decision` 提供 Regime-Aware Decision Engine。它不替代 `core/valuation` 的确定性 ETF 估值信号，而是在只读 API 和页面上额外输出研究排序层：

- `DecisionSignal.score`: 0-100 研究评分。
- `component_scores`: `momentum`、`flow`、`valuation`、`risk` 四个组件分。
- `adjusted_weights`: 由 `regime + taxonomy + factor_effectiveness` 调整后的动态权重。
- `factor_contributions`: 每个组件对最终分的贡献分。
- `state`: `{regime, score_band, trend_state, state_code}`。
- `confidence`: 由 Regime v2 置信度、taxonomy 置信度和因子覆盖度组合。

基础权重随市场状态变化：`risk_on` 更重动量和流动性，`risk_off` 更重估值和风险，`shock` 更重风险和流动性，`rotation` 保持均衡。taxonomy 会二次调整权重，例如主线主题更重动量和流动性，核心宽基更重估值和风险，收益防御策略更重估值质量和风险稳定性。

该层只用于“解释 + 评分 + 状态驱动权重”，不输出买卖动作、仓位比例、现金金额、份额数量，也不执行 rebalance。

## 防御因子仓位口径

`market_signal.defensive_factor_guidance` 用来解释红利低波、自由现金流和质量因子 ETF 在组合层的仓位带。它不是单只 ETF 的买入比例，而是“防御因子仓”整体在组合中的参考区间：

- `risk_on`: 防御因子仓靠近下沿，约 `2%-5%`。
- `rotation`: 防御因子仓中位，约 `5%-8%`。
- `risk_off` / `shock`: 防御因子仓靠近上沿，约 `8%-12%`。

因此，自由现金流 ETF 可以在 `risk_on` 中保留为组合稳定器，但不应替代主线、成长宽基或强行业 ETF 的进攻预算；在 `risk_off` 或 `shock` 中，防御因子仓的重要性上升。

## 抄底概率模式

`core/strategy` 提供 Contrarian Mode。它不是买入信号，而是极端回撤下的概率底部观察层：

- `drawdown_extreme`: 当前回撤接近历史滚动最大回撤、回撤分位较高，或回撤本身进入极端区。
- `regime_stress`: Regime v2 处于 `risk_off` 或 `shock`。
- `volatility_stress`: 20 日波动率进入压力区，当前硬阈值约为 2.8%。
- `liquidity_stress`: 流动性或 flow 因子显示压力。
- `governance_allowed`: 研究健康度 gate 不是 `reject`，否则禁止进入概率底部观察区。
- `reversal_probability`: 由极端回撤、恐慌释放、趋势衰竭和治理可信度组合生成。
- `final_view`: `probabilistic_bottom_zone`、`normal` 或 `not_active`。

`enabled=true` 的硬触发条件是：`drawdown_extreme && regime_stress && volatility_stress && governance_allowed`。`liquidity_stress` 会进入恐慌释放和反转概率计算，也会在页面展示，但不是当前版本进入 Contrarian Mode 的硬门槛。

该层只读，不修改 `DecisionSignal.score`，不输出交易动作、现金金额或份额数量。Web 详情页和 `GET /api/strategy/contrarian/{etf}` 会展示它的概率、触发条件和“不是趋势买点”的解释。

## 自然语言决策解释层

`core/interpreter` 提供 `DecisionInterpreter`，把既有 `DecisionSignal`、taxonomy profile 和治理健康度转换为结构化解释：

- `regime`: 市场状态和置信度。
- `taxonomy`: ETF 类型和子类。
- `intent`: `QuestionIntent`，包含 `type`、`confidence`、`entities`、`focus` 和 `normalized_question`。
- `decision`: 评分、`low | medium | high` 评分带和方向偏向。
- `explanation`: 结构化原因列表。
- `risk`: regime 稳定性、数据质量和治理警告。
- `final_answer`: 统一 `FinalAnswer`，包含 `headline`、`conclusion.type`、`reasoning`、`risk_notes` 和 `confidence`。

`parse_question(question)` 支持 `buy_assessment`、`risk_assessment`、`market_state`、`comparison` 和 `unknown`。`DecisionInterpreter` 会先调用 router，随后把 `decision_signal`、`regime`、`intent`、`governance` 和 `taxonomy` 交给 `AnswerPolicyEngine` 生成最终回答；router 只识别问题意图，不直接生成答案文本。

Web 提供只读问答入口：`GET /api/ask/{etf}?q=问题文本`。该接口复用本地已有研究输入，不写入队列、不触发外部同步、不输出交易指令、现金金额或份额数量。统一返回 `intent`、`decision`、`regime`、`final_answer` 和 `risk`。

## 策略路由

`core/strategy/router.py` 提供 Strategy Router。它自动判断当前主导解释模式：

- `trend`: Regime 为 `risk_on`，flow 和 momentum 为正，且没有极端回撤。
- `contrarian`: 极端回撤、压力 Regime 和反转概率共同支持抄底概率观察。
- `neutral`: rotation 不稳定、信号冲突、governance 为 warn/reject 或没有清晰主导。

Router 输出 `StrategyDecision`，包含 `active_mode`、`confidence`、四类 `reasoning`、`suppressed_mode`、`signals.trend_score`、`signals.contrarian_score` 和 `final_interpretation`。该层不修改 `DecisionSignal.score`，不输出交易动作、现金金额或份额数量。Web 详情页和 `GET /api/strategy/route/{etf}` 会展示策略编排结果。

## 决策回放与稳定性

`core/replay` 提供 Decision Replay & Stability Engine。它按 ETF 历史交易日截断本地行情，重新计算当日可见的 MarketStructure、Regime v2、factor exposure 和 DecisionSignal，用来验证评分系统在时间维度上的稳定性。Web API 默认使用均匀采样的 24 个回放点覆盖历史路径，并保留最新交易日。

输出包括：

- `time_series.score_series`: 历史评分序列。
- `time_series.regime_series`: 历史 regime path。
- `time_series.factor_series`: 每日四组件贡献。
- `stability.score_std`: 评分波动。
- `stability.regime_flip_rate`: regime 切换频率。
- `stability.regime_duration_distribution`: regime 持续期分布。
- `stability.regime_transition_matrix`: regime 状态切换矩阵。
- `stability.factor_stability_ic`: 四组件贡献的 lag-1 稳定性近似。
- `drawdown_sensitivity.score_vs_drawdown_correlation`: 评分与当前回撤的相关性。
- `validation.no_future_data`: 无未来函数校验结果。
- `consistency_score`: 综合稳定性评分。

没有历史估值信号的日期不会复用最新估值，而使用中性估值输入，并在 `validation.valuation_policy` 中说明。该层只做回放验证，不输出交易动作或仓位。

## 研究治理与健康度

`core/governance` 提供 Research Governance & Data Quality Layer，用来让系统判断“当前研究输出是否可信”。它聚合四类 gate：

- `data_quality`: 数据完整性、缺失率、陈旧率、日期对齐和价格覆盖率。
- `factor_quality`: IC 有效性、IC 衰减、因子不稳定和因子冗余。
- `regime_quality`: regime flip rate、entropy、confirmation score、过敏警告和稳定性评分。
- `report_quality`: 研究报告完整性、一致性、泄漏风险和可解释性。

最终输出 `system_health_score` 和 `gate_status`：`pass`、`warn` 或 `reject`。`reject` 表示系统可以识别并阻断低质量研究输出。健康接口为只读；因子 IC 健康检查使用代表性采样，并使用 120 秒短 TTL 缓存，避免每次请求全量重算。

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
- ETF 深研队列先显示核心宽基代表，再显示收益防御代表，最后显示主线代表；来源分别为 `核心宽基`、`收益防御代表` 和 `主线代表`。
- 上游信号分为两类：`market_signal` 来自 market 研究/市场状态层，用于建议总权益仓位；`theme_signal` 来自 theme 研究，只对主线、行业和主题 ETF 生效。宽基 ETF、自由现金流 ETF、红利低波 ETF 不等待行业主线确认。
- `product_signal` 只描述 ETF 自身估值、流动性、跟踪质量、折溢价、回撤机会和风险调整，不再混称为上游信号。
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
- `/etfs/{code}`：ETF 详情页，显示当前研究结论、“问这个ETF”常用问答、参考价格区间历史、产品结构、持仓披露、估值与流动性、风险与证伪、研究历史。
- `/research?etf={code}`：主动研究入口；没有详情页时入队并跳转，有详情页时直接跳转。
- `/api/index`：对外主结果接口。
- `/api/latest`：对外研究成果接口，包含每只 ETF 的 `taxonomy_profile`、`market_context`、`market_signal`、`theme_signal`、`product_signal` 和类型化 `decision_matrix`。
- `/api/queue`：本地研究队列接口。
- `/api/etfs`：当前 ETF 列表。
- `/api/etfs/{code}`：单只 ETF 研究数据、`taxonomy_profile`、`market_context`、市场/主题/产品信号、队列状态和历史。
- `/api/etf/{code}/profile`：单只 ETF taxonomy profile。
- `/api/factors/{etf}`：单只 ETF 标准化因子暴露。
- `/api/factors/exposure/{etf}`：单只 ETF 因子暴露显式别名。
- `/api/factors/ic/{factor}`：单个因子的 5/20/60 日 IC 摘要。
- `/api/score/{etf}`：单只 ETF 状态感知研究评分。
- `/api/ask/{etf}?q={question}`：单只 ETF 自然语言决策解释，最终回答统一由 `AnswerPolicyEngine` 生成。
- `/api/score/decompose/{etf}`：单只 ETF 评分组件、动态权重和贡献拆解。
- `/api/decision/state/{etf}`：单只 ETF 状态机输出。
- `/api/strategy/contrarian/{etf}`：单只 ETF 抄底概率模式，不覆盖原 Decision Score。
- `/api/strategy/route/{etf}`：单只 ETF 策略路由，在 trend、contrarian、neutral 间做解释层选择。
- `/api/replay/{etf}`：单只 ETF 历史 DecisionSignal 回放报告。
- `/api/replay/{etf}/stability`：单只 ETF 回放稳定性指标。
- `/api/replay/{etf}/regime-path`：单只 ETF 历史 regime path 和状态切换矩阵。
- `/api/health/system`：系统级研究可信度健康报告。
- `/api/health/data`：数据完整性、陈旧度、对齐和覆盖检查。
- `/api/health/factors`：因子 IC 有效性、衰减和冗余检查。
- `/api/health/regime`：regime 稳定性、过敏和确认度检查。
- `/api/health/report`：研究报告完整性、一致性、泄漏风险和可解释性 gate。
- `/api/market/structure`：市场结构层。
- `/api/market/breadth`：市场宽度摘要。
- `/api/market/liquidity`：流动性结构摘要。
- `/api/market/regime-v2`：结构驱动的市场状态。

接口目录按“文档入口、Web 页面、当前数据、历史数据、分析结果、系统状态”分组。`/api` 只返回说明，不触发重计算、写入、交易、同步或外部请求；所有 `/api/*` 数据接口只读取本地结果。`/research` 是 Web 主动研究入口，可能写入本地研究队列，因此在目录中会单独标记为非只读公开路由。
