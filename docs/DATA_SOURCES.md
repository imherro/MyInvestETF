# 数据源

## 结构化 ETF 数据

- Tushare：A 股 ETF 结构化主源，使用本地 `.env` 中的 `TUSHARE_TOKEN`。
- `fund_basic`：基金基础信息。
- `fund_daily`：ETF 日行情。
- `fund_nav`：基金净值。
- `fund_share`：基金份额变化，可作为申赎和资金流代理。
- `fund_portfolio`：已披露持仓，注意披露滞后。
- `index_daily`：底层指数行情。

## 补充证据

- FRED：宏观序列，使用本地 `.env` 中的 `FRED_API_KEY`。
- yfinance：海外市场、海外 ETF 或可比指数补充。
- 网络公开资料：只作补充证据，必须记录来源和日期。

## 数据缺口必须披露

以下字段如果无法取得，必须写入 `data_gaps`：

- 官方净申购赎回金额
- 长期估值分位
- 实时完整持仓
- 组合重叠数据
- 持仓披露更新后的实时变化

## 密钥规则

- `.env` 不提交。
- `.env.example` 只放变量名和空值。
- 报告、页面、接口和审计包不得输出真实 token。
