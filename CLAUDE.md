# funny_valuation -- AI 驱动的 A 股投研分析系统

## 项目性质
这是一个 Skill 编排仓库（非代码仓库）。核心产物是 SKILL.md 工作流定义和辅助 Python 脚本。

最高法则：对于任何行业，股票的分析，必须充分获取调研真实数据，结合数据分析，而绝对不能主观臆测。

## 投资理念
所有分析任务必须遵循 `SOUL.md` 中定义的投资分析理念。

## 核心原则

1. 个股的数据和分析结果必须组织在`data/{stock_code}/` 股票代码命名的文件夹下,不允许放置在其他位置。
2. **数据契约是法律**：每阶段输出必须通过 `specs/contracts/` 下对应的 JSON Schema 校验，缺失 critical 字段则阻断流程。
3. **单一数据源**：金融数据只获取一次，缓存在 `data/{stock_code}/cache/`，下游阶段从缓存读取。
4. **可审计性**：每次工作流执行产生 `data/{stock_code}/state.json` 追踪全流程状态。
5. **双模式 Skill**：每个组件 Skill 支持独立运行（Mode A）和工作流协同运行（Mode B）。

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
  state.json         # 研究状态追踪
  journal.jsonl      # 研究过程日志
  cache/             # API 缓存（统一缓存层）
    stock_info.json  # 实时行情快照（当日有效）
    macro_data.json  # 宏观数据（7天有效）
    industry_list.json # 行业公司列表（30天有效）
  raw/               # 原始资料
    earnings-pdf/    # 财报 PDF（永久缓存）
    earnings-extracted/ # 提取的章节 Markdown
  analysis/          # 分析中间产物
  reports/           # 最终报告
```

> **兼容说明**：`market-data/` 为旧缓存目录，新任务统一使用 `cache/`。存量数据无需迁移，新数据一律写入 `cache/`。

### 目录结构特例

非个股工作流的数据按"功能命名"而非股票代码组织, 在 `data/` 下与 `{stock_code}/` 同级:

```
data/national-team/                # 国家队动向追踪 (national-team-tracker workflow)
  state.json                       # 工作流状态
  reports/{YYYY-MM-DD}/            # 每次执行归档
    snapshot.json                  # Stage 1
    seasonal.json                  # Stage 2
    signal.json                    # Stage 3
    national-team-report.md        # Stage 4 主报告
    charts/*.png                   # 图表 (matplotlib 可用时)

data/industry-{name}/              # 行业研究
  state.json
  analysis/
  reports/

data/compare/                      # 多股对比分析
  reports/
```

判定原则: 若分析对象**不是单只股票**而是**全市场或某主体**, 则用功能命名目录。新增此类目录需在 CLAUDE.md 声明。

## 研究流程约束

### 研究日志
每次分析任务必须在 `data/{code}/journal.jsonl` 中记录完整过程。
日志格式遵循 `specs/contracts/research-journal.json` 契约。

记录时机：
- Skill 调用前: `{"type":"skill_start", "skill":"...", "args":{...}}`
- Skill 调用后: `{"type":"skill_end", "skill":"...", "status":"...", "duration_ms":...}`
- 质量门校验后: `{"type":"quality_gate", "schema":"...", "result":"..."}`
- 分析完成时: `{"type":"done", "report":"...", "total_duration_ms":...}`

### Agent 调度规则
- **Level 1**（单 Skill）：调度者直接执行
- **Level 2**（2-3 Skills）：调度者编排执行，先数据采集（并行），再分析（串行）
- **Level 3**（复杂研究）：委派给专业 Agent（deep-research / valuation-agent / industry-analyst）

### 报告存储规则
- 所有研究报告必须存储在 `data/{code}/reports/` 目录下
- 报告文件名格式: `{code}_{报告类型}_{YYYYMMDD}.md`
- 禁止将报告存放在项目根目录
- 多股对比报告存放在 `data/compare/reports/`
- 行业研究报告存放在 `data/industry-{name}/reports/`

### Skill 调用去重
同一研究任务中，同一 Skill 对同一股票代码只执行一次。
如需刷新数据，必须先清除 `cache/` 中的对应缓存文件。

## 禁止模式

- 禁止获取已缓存在 `data/{code}/cache/` 中的 API 数据
- 禁止静默吞掉 API 错误；必须设置 `"status": "failed"` 并记录错误信息
- 禁止在阶段间传递原始 API 响应结构；必须先规范化为契约格式
- 禁止跳过质量门校验直接进入下一阶段
- 禁止生成无数据支撑的分析结论
