# funny_valuation -- AI 驱动的 A 股投研分析系统

## 项目性质
这是一个 Skill 编排仓库（非代码仓库）。核心产物是 SKILL.md 工作流定义和辅助 Python 脚本。

## 核心原则

1. **数据契约是法律**：每阶段输出必须通过 `specs/contracts/` 下对应的 JSON Schema 校验，缺失 critical 字段则阻断流程。
2. **单一数据源**：金融数据只获取一次，缓存在 `data/{stock_code}/market-data/`，下游阶段从缓存读取。
3. **可审计性**：每次工作流执行产生 `data/{stock_code}/state.json` 追踪全流程状态。
4. **双模式 Skill**：每个组件 Skill 支持独立运行（Mode A）和工作流协同运行（Mode B）。

## 字段名规范（强制）

所有阶段间 JSON 数据必须使用以下规范字段名：

| 规范字段名 | 禁止使用 | 说明 |
|---|---|---|
| `current_price` | `price` | 当前股价（元） |
| `total_market_cap` | `market_cap` | 总市值（亿元） |
| `circulating_market_cap` | -- | 流通市值（亿元） |
| `total_shares` | `shares` | 总股本（亿股） |
| `pe_ttm` | `pe` | 市盈率TTM（倍） |
| `pb_mrq` | `pb` | 市净率MRQ（倍） |
| `ps_ttm` | `ps` | 市销率TTM（倍） |
| `eps_ttm` | `eps` | 每股收益TTM（元） |
| `bvps` | -- | 每股净资产（元） |

## 数据单位规范（强制）

| 数据项 | 单位 | 说明 |
|---|---|---|
| 股价 | 元 | push2 API 原始值需 /100 |
| 市值 | 亿元 | push2 API f116（元）需 /1e8 |
| 营收/利润 | 亿元 | 财务报表原始值需 /1e8 |
| 股本 | 亿股 | push2 API f55 需 /1e8 |
| PE/PB/PS | 倍 | push2 API 原始值需 /100 |
| WACC/利率 | 小数 | 0.08 表示 8%，展示层转换 |

## 目录结构

```
.claude/skills/
  components/        # 组件 Skill（可独立运行，也可工作流调用）
  workflows/         # 工作流 Skill（编排组件 Skill）
  meta/              # 元 Skill（管理工作流本身）
specs/contracts/     # 数据契约（JSON Schema）
scripts/             # 共享脚本（校验、HTTP 工具等）
data/{stock_code}/   # 执行产物（按股票代码组织，不入 Git）
  state.json         # 工作流执行状态
  earnings-pdf/      # Stage 1 产物
  earnings-extracted/ # Stage 2 产物
  earnings-analysis/ # Stage 3 产物
  market-data/       # 市场数据缓存（单一数据源）
  valuation-reports/ # Stage 5 产物
```

## 禁止模式

- 禁止获取已缓存在 `data/{code}/market-data/` 中的 API 数据
- 禁止静默吞掉 API 错误；必须设置 `"status": "failed"` 并记录错误信息
- 禁止在阶段间传递原始 API 响应结构；必须先规范化为契约格式
- 禁止跳过质量门校验直接进入下一阶段
