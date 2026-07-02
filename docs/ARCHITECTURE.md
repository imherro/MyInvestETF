# 架构设计

## 目标

MyInvestETF 为单只 ETF 提供研究页面和只读 API，展示：

- 产品结构和跟踪指数
- 底层资产类别和组合角色
- 持仓披露、集中度和披露滞后
- 净值、价格、折溢价和底层指数估值位置
- ETF 类型化估值模型和五仓角色
- ETF taxonomy profile
- 标准化因子暴露和 IC 摘要
- 市场结构 breadth/liquidity/dispersion 和 Regime v2
- Regime-aware 状态感知研究评分、动态权重和贡献拆解
- Contrarian Mode 抄底概率模式和概率底部观察区解释
- 历史 DecisionSignal 回放、稳定性指标和无未来函数校验
- 研究治理健康度、数据质量、因子有效性、regime 稳定性和报告 gate
- 流动性、份额变化和跟踪质量
- 市场状态、当前回撤、最大回撤和回撤分位
- 组合使用判断
- ETF 历史研究记录

## 数据流

```mermaid
flowchart LR
  A["ETF 输入 / 主动研究入口"] --> B["research_queue"]
  B --> C["generate_single_etf_prompt.py"]
  C --> D["Codex ETF 完整深研 research"]
  D --> E["research assembly_input"]
  E --> T["core/taxonomy"]
  T --> X["core/factors"]
  X --> F["core/report.build_etf_report"]
  F --> M["core/market + core/risk"]
  M --> S["MarketStructure + Regime v2"]
  S --> W["core/decision: DecisionSignal"]
  W --> Y["core/strategy: ContrarianSignal"]
  M --> G["SQLite: etf_research_runs"]
  G --> H["8017 Web ETF 页"]
  W --> H
  Y --> H
  W --> R["core/replay: Decision replay"]
  R --> H
  R --> Q["core/governance: Research health gates"]
  Q --> H
```

## 分层

- `myinvestetf/leader_index.py`：ETF 输入解析、投资暴露归类、同类成交额代表筛选、主动研究入队和提示词生成。
- `myinvestetf/db.py`：SQLite schema、队列状态机和研究记录入库。
- `myinvestetf/web.py`：只读 Web 页面和 JSON API。
- `core/schema/etf_report.py`：`ETFResearchReport` 强 schema。
- `core/valuation/classification.py`：ETF 类型识别，输出 `broad_index`、`mainline_theme`、`factor_defensive`、`cash_like`。
- `core/taxonomy/`：ETF taxonomy 画像，输出 10 个 ETF 认知类型、主题生命周期、置信度和分类理由。
- `core/factors/`：point-in-time 因子标准化、factor registry、IC 分析和因子暴露归因。
- `core/valuation/`：ETF 类型化估值、流动性、跟踪质量和仓位角色确定性评分。
- `core/market/`：根据 ETF 或底层指数行情判断 `risk_on`、`risk_off`、`shock`、`rotation`。
- `core/market/structure.py`：市场结构层，当前用 ETF 池代理 breadth、liquidity 和 dispersion。
- `core/decision/`：Regime-Aware Decision Engine，基于 Regime v2、taxonomy、factor exposure 和 valuation signal 输出研究评分、动态权重和状态机。
- `core/strategy/`：Contrarian Mode 抄底概率模式，基于极端回撤、压力状态、流动性压力和治理可信度输出概率底部观察，不覆盖原始 Decision Score。
- `core/replay/`：Decision Replay & Stability Engine，按 `as_of_date` 截断历史行情回放 DecisionSignal，并输出稳定性、regime path 和无未来函数校验。
- `core/governance/`：Research Governance & Data Quality Layer，输出数据、因子、regime、报告和系统健康 gate。
- `core/risk/`：根据 ETF 收盘价计算当前回撤、最大回撤、回撤分位、修复速度和持续天数。
- `core/report/`：确定性报告组装和 `report_hash`。
- `core/observability/`：旁路 trace 和审计日志。

## Web 路由

- `/`：ETF 首页。
- `/research?etf={code}`：主动研究入口。
- `/etfs/{code}`：ETF 详情页。
- `/api/index`：主结果接口。
- `/api/latest`：研究成果接口。
- `/api/etfs`：ETF 列表接口。
- `/api/etfs/{code}`：单只 ETF 详情接口。
- `/api/etf/{code}/profile`：单只 ETF taxonomy profile。
- `/api/factors/{etf}`：单只 ETF 标准化因子暴露。
- `/api/factors/exposure/{etf}`：单只 ETF 标准化因子暴露别名。
- `/api/factors/ic/{factor}`：单因子 IC 摘要。
- `/api/score/{etf}`：单只 ETF 状态感知研究评分。
- `/api/score/decompose/{etf}`：单只 ETF 评分拆解。
- `/api/decision/state/{etf}`：单只 ETF 状态机输出。
- `/api/strategy/contrarian/{etf}`：单只 ETF 抄底概率模式。
- `/api/replay/{etf}`：单只 ETF 历史评分回放。
- `/api/replay/{etf}/stability`：单只 ETF 回放稳定性。
- `/api/replay/{etf}/regime-path`：单只 ETF 历史 regime path。
- `/api/health/system`：系统研究健康度总览。
- `/api/health/data`：数据质量 gate。
- `/api/health/factors`：因子有效性 gate。
- `/api/health/regime`：regime 稳定性 gate。
- `/api/health/report`：研究报告质量 gate。
- `/api/market/structure`：市场结构层。
- `/api/market/breadth`：市场宽度摘要。
- `/api/market/liquidity`：流动性结构摘要。
- `/api/market/regime-v2`：结构驱动市场状态。
- `/api/queue`：研究队列接口。

## 页面约束

所有页面首尾统一加载：

```html
<script src="https://invest.okbbc.com/header.js" data-target="[data-myinvest-header]" defer></script>
<script src="https://invest.okbbc.com/footer.js" data-target="[data-myinvest-footer]" defer></script>
```

Web 侧只读展示。主动研究入口只创建队列和跳转，不直接执行深研。

短融、日利、货币和现金类 ETF 被视为 `cash_like`，不进入深度研究队列，只做现金替代资格监控。
