# funny_valuation 重构方案

> 版本: v1.0 | 日期: 2026-05-11 | 状态: 待审核

---

## 一、现状诊断

### 1.1 现有资产盘点

| 类别 | 资产 | 状态 | 评估 |
|------|------|------|------|
| **组件 Skill** | earnings-report-extractor | 成熟 | 保留，巨潮PDF下载+章节提取，核心能力 |
| **组件 Skill** | earnings-insight | 成熟 | 保留+增强，七层分析框架是核心资产 |
| **组件 Skill** | stock-valuation | 成熟 | 保留+重构，八模型估值，需解耦实时数据获取 |
| **组件 Skill** | a-stock-realtime | 成熟 | 保留，零依赖实时行情，三级数据源回退 |
| **组件 Skill** | china-macro-lite | 成熟 | 保留，零依赖宏观数据，12项指标 |
| **组件 Skill** | finance | 可用 | 保留，港美股数据（内部Gateway API） |
| **组件 Skill** | financial-news | 可用 | 保留，港美股新闻（ticker-cli） |
| **工作流** | financial-report-full | 成熟 | 重构，过于死板 |
| **工作流** | quick-valuation | 成熟 | 重构，融入新架构 |
| **工作流** | national-team-tracker | 成熟 | 保留，独立性好 |
| **Agent** | workflow-lead | 可用 | 重构为通用调度器 |
| **Agent** | workflow-reporter | 可用 | 保留+增强 |
| **基础设施** | validate_contract.py | 成熟 | 保留 |
| **基础设施** | http_utils.py | 成熟 | 保留 |
| **基础设施** | specs/contracts/ | 成熟 | 扩展 |

### 1.2 核心问题定位

**问题一：流水线过于死板**
- financial-report-full 是 8 阶段线性流水线，所有分析必须走完全程
- 无法根据任务灵活组合 skills（如"只做行业对比+估值"或"只做宏观+资金面分析"）
- 工作流要么全量执行，要么完全不执行，缺少中间态

**问题二：分析深度不够**
- earnings-insight 的七层分析中，行业分析（Layer 3）依赖用户手动提供信息
- 缺少独立的行业研究 skill，无法自动获取行业数据
- 缺少对宏观环境的系统性考量（china-macro-lite 存在但未深度整合进分析流程）
- 缺少同行对比分析（可比公司估值）
- 估值模型缺少历史估值分位数分析（PE Band / PB Band）

**问题三：数据管理不统一**
- result_docs/ 中大量报告散落在根目录，未按股票代码组织
- data/ 目录中部分旧数据未按 CLAUDE.md 规范存放（如 000021/ 直接在 data/ 下没有子目录结构）
- 不同 skill 的输出命名规范不完全统一
- 缺少研究过程的完整审计日志（类似 Dexter 的 Scratchpad）

---

## 二、重构目标

### 2.1 核心原则

1. **灵活编排**：从"固定流水线"转变为"Agent 按需调度 Skill"
2. **深度严谨**：每个分析结论必须有数据支撑，可追溯、可验证
3. **统一管理**：数据、报告、日志严格按规范存储
4. **零/低成本**：所有数据源保持免费，运行时依赖 Claude Code
5. **渐进兼容**：保留现有 skill 资产，增量重构

### 2.2 借鉴 Dexter 的设计元素

| Dexter 设计 | 借鉴方式 | 适配说明 |
|-------------|---------|---------|
| SOUL.md | → `SOUL.md` 投资理念文件 | 注入 CLAUDE.md，定义分析哲学和禁区 |
| SKILL.md | → 保持现有 skill 体系 | 已具备，继续使用 |
| Scratchpad | → `research-journal.jsonl` | 每次分析任务记录完整过程 |
| 元工具路由 | → 通用调度 Agent | Agent 理解意图后选择 skill 组合 |
| 三级上下文管理 | → 分析摘要机制 | 长分析链的中间摘要 |
| Skill dedup | → Skill 执行去重 | 同一股票同一 skill 不重复执行 |
| 工具调用计数 | → Skill 调用追踪 | state.json 中记录调用次数 |
| 缓存 | → data/{code}/cache/ | 已有 market-data 缓存，统一缓存层 |

---

## 三、新架构设计

### 3.1 整体架构图

```
用户自然语言输入（Claude Code 对话）
    │
    ▼
┌──────────────────────────────────────────────────────┐
│  CLAUDE.md + SOUL.md                                  │
│  (系统级约束：投资理念、数据规范、禁止模式)              │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│  调度层：Agent 体系                                    │
│                                                       │
│  ┌─────────────────┐  分析复杂度判定                   │
│  │ research-lead   │─── 简单查询 → 直接调用单个 Skill   │
│  │ (通用调度 Agent) │─── 中等任务 → 编排 2-3 个 Skill   │
│  │                 │─── 复杂研究 → 委派给专业 Agent     │
│  └────────┬────────┘                                  │
│           │ 委派                                      │
│   ┌───────┼───────────────────┐                       │
│   ▼       ▼                   ▼                       │
│ ┌──────┐ ┌──────────────┐ ┌──────────────┐           │
│ │valua-│ │deep-research │ │industry-     │           │
│ │tion  │ │(深度研究     │ │analyst       │           │
│ │agent │ │  Agent)      │ │(行业研究     │           │
│ │      │ │              │ │  Agent)      │           │
│ └──────┘ └──────────────┘ └──────────────┘           │
│                                                       │
│  ┌─────────────┐                                      │
│  │ reporter    │ ← 任务完成后自动审计                   │
│  │ (审计Agent) │                                      │
│  └─────────────┘                                      │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│  执行层：Skill 体系                                    │
│                                                       │
│  数据采集 Skills          分析 Skills         输出 Skills│
│  ├── a-stock-realtime    ├── earnings-insight ├── report│
│  ├── china-macro-lite    ├── stock-valuation  │-genera-│
│  ├── earnings-extractor  ├── industry-compare │ tor    │
│  ├── finance (港美股)     ├── pe-band-analysis │        │
│  ├── financial-news      ├── macro-impact     │        │
│  └── industry-data(新)   └── risk-scanner(新) │        │
│                                                       │
└──────────────────┬───────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────┐
│  数据层                                               │
│  data/{stock_code}/                                   │
│    ├── cache/              → API 响应缓存             │
│    ├── raw/                → 原始数据（PDF、JSON）      │
│    ├── analysis/           → 分析中间产物              │
│    ├── reports/            → 最终报告                  │
│    ├── journal.jsonl       → 研究过程日志              │
│    └── state.json          → 状态追踪                 │
│  data/{function_name}/     → 非个股分析（如 national-team）│
└──────────────────────────────────────────────────────┘
```

### 3.2 Agent 体系设计

#### 3.2.1 research-lead（通用调度 Agent）

**定位**：替代原 workflow-lead，从"流水线管理者"升级为"研究任务理解+调度者"。

**核心变化**：不再按固定 Stage 推进，而是理解用户意图后**动态编排** skill 组合。

```markdown
# .claude/agents/research-lead.md

角色：你是研究调度中心，负责理解用户的研究需求，选择合适的 Skill 组合，
     管理执行状态，确保研究质量。

## 任务分级与路由

### Level 1 — 单 Skill 直接执行（调度者自身执行）
触发条件：用户需求可由单个 skill 完成
示例：
- "查一下 600519 的实时行情" → a-stock-realtime
- "看看最新的宏观数据" → china-macro-lite
- "下载 300014 的年报" → earnings-report-extractor

### Level 2 — 多 Skill 编排（调度者自身编排执行）
触发条件：用户需求需要 2-3 个 skill 协同，但逻辑简单
示例：
- "快速估值 002335" → a-stock-realtime → stock-valuation
- "看看 600309 的行业地位" → a-stock-realtime → industry-compare
- "国家队在干嘛，顺便看看宏观" → national-team-tracker + china-macro-lite

编排规则：
1. 先执行数据采集类 skill（并行）
2. 再执行分析类 skill（串行，依赖数据）
3. 每个 skill 完成后校验质量门
4. 中间结果写入 data/{code}/analysis/

### Level 3 — 委派给专业 Agent
触发条件：复杂研究任务，需要深度分析和多轮迭代
示例：
- "全面研究 300014" → deep-research agent
- "分析光伏行业格局" → industry-analyst agent
- "对比 002335 和 002049 的投资价值" → valuation-agent

委派协议：
1. 创建 data/{code}/state.json，设置 overall_status=running
2. 将任务描述、上下文、可用 skill 清单传递给专业 Agent
3. 专业 Agent 自主编排 skill，更新 state.json
4. 完成后调度者验收结果，触发 reporter 审计

## 全局约束
- 任何分析任务开始前，必须初始化 data/{code}/ 目录和 state.json
- 任何 skill 调用必须记录到 journal.jsonl
- 不允许跳过质量门（validate_contract.py）
- 不允许生成无数据支撑的结论
```

#### 3.2.2 deep-research（深度研究 Agent）

**定位**：替代原 financial-report-full 工作流，但不再是固定流水线，而是根据研究目标自主规划。

```markdown
# .claude/agents/deep-research.md

角色：你是深度研究分析师，负责对个股进行全面、深入的基本面研究。

## 研究流程（非固定顺序，按需执行）

### Phase 1: 信息收集（并行）
根据研究目标，从以下 skill 中选择需要的数据：
- a-stock-realtime: 实时行情、估值指标、资金流向
- earnings-report-extractor: 财报PDF下载+章节提取
- china-macro-lite: 宏观经济环境
- industry-data: 行业数据（如可用）

选择原则：
- 估值类研究 → 必须有实时行情 + 财务数据
- 基本面研究 → 必须有财报 + 行业数据
- 事件驱动研究 → 必须有新闻 + 实时行情

### Phase 2: 深度分析（串行，依赖 Phase 1）
- earnings-insight: 七层分析（基于财报数据 + 提取的章节文本）
- macro-impact: 宏观环境对个股的影响分析（新 skill）
- industry-compare: 同行对比分析（新 skill）

### Phase 3: 估值（串行，依赖 Phase 2）
- pe-band-analysis: 历史估值分位数分析（新 skill）
- stock-valuation: 多模型估值

### Phase 4: 综合研判
基于所有分析结果，生成综合研判报告，必须包含：
- 三情景分析（基准/乐观/悲观），每个情景有概率和证伪条件
- 关键指标跟踪清单
- 风险矩阵

### Phase 5: 报告输出
- 综合报告写入 data/{code}/reports/
- 触发 reporter 审计

## 分析严谨性约束

### 硬约束（违反则阻断）
- 任何数值结论必须注明数据来源（API/财报/计算）
- 估值结论必须有 ≥3 个模型交叉验证
- 三情景概率之和必须 = 100%
- 禁止使用 CLAUDE.md 中列出的模糊表述

### 软约束（违反则警告）
- 建议使用行业对比数据验证估值合理性
- 建议考虑宏观周期对盈利预测的影响
- 建议检查管理层表述与财务数据的一致性
```

#### 3.2.3 valuation-agent（估值 Agent）

**定位**：专注于估值分析，支持单股估值和多股对比估值。

```markdown
# .claude/agents/valuation-agent.md

角色：你是估值分析专家，专注于确定公司的合理价值区间。

## 能力
- 单股全面估值（多模型交叉验证）
- 多股对比估值（相对估值法为主）
- 历史估值分位数分析（PE/PB/PS Band）

## 估值流程
1. 获取数据: a-stock-realtime + data_fetcher.py
2. 历史估值: pe-band-analysis skill
3. 绝对估值: stock-valuation skill（DCF/DDM/FCFF 等）
4. 相对估值: industry-compare skill（可比公司法）
5. 交叉验证: 三种方法的结果比较
6. 综合结论: 加权估值区间 + 安全边际判断

## 质量要求
- 至少 3 种估值方法产出有效结果
- 估值区间的高低端差距不超过 100%（否则说明假设分歧过大，需细化）
- 必须注明每个模型的关键假设
```

#### 3.2.4 industry-analyst（行业研究 Agent）

**定位**：专注于行业层面分析，弥补当前缺失的行业研究能力。

```markdown
# .claude/agents/industry-analyst.md

角色：你是行业研究分析师，负责行业格局、竞争态势、产业链分析。

## 能力
- 行业内公司对比排名
- 产业链上下游分析
- 行业周期定位
- 竞争格局变化追踪

## 数据来源
- a-stock-realtime: 批量获取行业内公司行情
- industry-data skill: 行业分类和排名数据
- web search: 行业研报、政策信息
- china-macro-lite: 宏观环境对行业的影响

## 输出
- 行业研究报告（Markdown）
- 行业对比数据表（JSON，符合新的 industry-compare 契约）
```

#### 3.2.5 reporter（审计 Agent，升级版）

保留现有 workflow-reporter 的核心功能，增加：
- 数据溯源验证：检查报告中引用的数据是否能在 cache/ 中找到原始来源
- 分析一致性检查：检查不同 skill 产出的同一指标是否一致
- journal.jsonl 审计：检查研究过程是否完整

---

### 3.3 新增 Skill 设计

#### 3.3.1 pe-band-analysis（历史估值分位数分析）

```markdown
# .claude/skills/components/pe-band-analysis/SKILL.md

---
name: pe-band-analysis
description: "A股个股历史估值分位数分析。获取近N年PE/PB/PS历史数据，
  计算当前估值在历史区间中的分位数位置，判断估值高低。
  零依赖（Python 3.6+ 标准库）。"
---

## 功能
1. 获取个股近 3/5/10 年的 PE(TTM)、PB(MRQ)、PS(TTM) 历史序列
2. 计算当前值在历史分布中的分位数
3. 绘制 PE Band / PB Band 图表（matplotlib 可用时）
4. 输出估值区间判断（极度低估/低估/合理/高估/极度高估）

## 数据源
- 东方财富 push2his API（历史K线 + 估值指标），零依赖
- 读取 data/{code}/cache/stock_info.json（如已缓存）

## 分位数判定标准
| 分位数范围 | 判定 | 说明 |
|-----------|------|------|
| 0-10% | 极度低估 | 历史最低区间 |
| 10-30% | 低估 | 低于历史均值一个标准差 |
| 30-70% | 合理 | 历史均值附近 |
| 70-90% | 高估 | 高于历史均值一个标准差 |
| 90-100% | 极度高估 | 历史最高区间 |

## 输出
- JSON: data/{code}/analysis/{code}_pe_band.json
- Markdown: data/{code}/analysis/{code}_pe_band_report.md
- PNG: data/{code}/analysis/{code}_pe_band.png（可选）

## 双模式
- Mode A: 独立运行，直接输出分析
- Mode B: 协同运行，输出 JSON 到指定目录
```

**实现脚本**：`pe_band.py`，零依赖，通过东方财富 push2his API 获取历史 K 线和估值数据。

#### 3.3.2 industry-compare（同行对比分析）

```markdown
# .claude/skills/components/industry-compare/SKILL.md

---
name: industry-compare
description: "A股同行业公司对比分析。自动识别目标公司所属行业，
  获取同行业上市公司的核心指标，进行排名和对比分析。
  零依赖（Python 3.6+ 标准库）。"
---

## 功能
1. 根据股票代码获取所属行业（申万分类）
2. 获取同行业所有上市公司列表
3. 批量获取核心指标（市值、PE、PB、ROE、营收增速、净利增速）
4. 排名和分位数定位
5. 可比公司估值法（选取 3-5 家可比公司）

## 数据源
- 东方财富行业板块 API（零依赖）
- 东方财富 push2 API（批量行情）
- 同花顺行业分类（备选）

## 输出
- JSON: data/{code}/analysis/{code}_industry_compare.json
- Markdown: 行业对比排名表 + 可比公司估值分析

## 契约
新建 specs/contracts/industry-compare.json
```

#### 3.3.3 macro-impact（宏观影响分析）

```markdown
# .claude/skills/components/macro-impact/SKILL.md

---
name: macro-impact
description: "宏观经济环境对个股影响的结构化分析。基于 china-macro-lite
  的宏观数据，结合个股所属行业特征，评估宏观因子对公司基本面的影响方向和程度。
  纯 Prompt 驱动（无脚本），调用 china-macro-lite 获取数据后由 LLM 分析。"
---

## 分析框架
1. 利率敏感性：LPR/国债收益率变化对公司融资成本和估值的影响
2. 通胀传导：CPI/PPI 变化对公司成本和定价能力的影响
3. 流动性环境：M1/M2 变化对行业资金面的影响
4. 汇率影响：（如适用）出口占比、外币负债
5. 政策周期：当前宏观政策取向对行业的倾斜/压制

## 输入
- china-macro-lite 输出的 macro_data.json
- 个股行业信息（来自 a-stock-realtime）
- 个股财务数据（来自 data_fetcher.py，特别是有息负债率、出口占比）

## 输出
- Markdown: 宏观影响评估（每个因子标注影响方向和程度）
- 写入 data/{code}/analysis/{code}_macro_impact.md
```

#### 3.3.4 risk-scanner（风险扫描）

```markdown
# .claude/skills/components/risk-scanner/SKILL.md

---
name: risk-scanner
description: "个股风险快速扫描工具。自动检测财务风险信号（商誉占比、
  质押率、大股东减持、审计意见、ST风险等）。
  零依赖（Python 3.6+ 标准库）。"
---

## 扫描项（15 项）
| 类别 | 检测项 | 红线 |
|------|--------|------|
| 财务 | 商誉/净资产 | >30% 红色警告 |
| 财务 | 应收账款/营收 | >50% 橙色警告 |
| 财务 | 有息负债率 | >60% 橙色警告 |
| 财务 | 经营现金流连续为负 | 2年以上红色警告 |
| 财务 | 扣非净利润连续下降 | 3年以上橙色警告 |
| 治理 | 大股东质押率 | >50% 红色警告 |
| 治理 | 近6月高管减持 | 金额>1000万 橙色 |
| 治理 | 审计意见 | 非标准无保留 红色 |
| 市场 | ST/*ST 标记 | 红色警告 |
| 市场 | 近一年最大回撤 | >50% 橙色 |
| 市场 | 换手率异常 | 连续5日>15% 橙色 |
| 合规 | 最近处罚记录 | 有则红色 |
| 集中度 | 前五大客户占比 | >70% 橙色 |
| 集中度 | 前五大供应商占比 | >70% 橙色 |
| 估值 | PE 分位数 | >95% 红色，<5% 提示 |

## 输出
- JSON: data/{code}/analysis/{code}_risk_scan.json
- 风险评级：低风险/中风险/高风险/极高风险
```

#### 3.3.5 report-generator（报告生成器）

```markdown
# .claude/skills/components/report-generator/SKILL.md

---
name: report-generator
description: "统一报告生成工具。读取 data/{code}/analysis/ 下的所有分析产物，
  按标准模板生成统一格式的研究报告。纯 Prompt 驱动。"
---

## 报告模板
1. 快速估值报告（1-2页）
2. 深度研究报告（5-10页）
3. 行业对比报告（3-5页）
4. 风险扫描报告（1页）

## 强制规范
- 每个数据结论必须标注 [数据来源: {skill_name}, {timestamp}]
- 每个分析结论必须标注信号可信度 [高/中/低/假设性]
- 必须包含免责声明
- 报告输出到 data/{code}/reports/{report_type}_{YYYYMMDD}.md
```

---

### 3.4 数据层重构

#### 3.4.1 统一目录结构

```
data/
├── {stock_code}/                    # 个股分析（如 600519/）
│   ├── state.json                   # 研究状态追踪
│   ├── journal.jsonl                # 研究过程日志（新增，借鉴 Scratchpad）
│   ├── cache/                       # API 缓存（合并原 market-data/）
│   │   ├── stock_info.json          # 实时行情快照（带 TTL）
│   │   ├── macro_data.json          # 宏观数据
│   │   ├── industry_list.json       # 行业公司列表
│   │   └── {api}_{timestamp}.json   # 其他 API 缓存
│   ├── raw/                         # 原始资料
│   │   ├── earnings-pdf/            # 财报 PDF
│   │   └── earnings-extracted/      # 提取的章节 Markdown
│   ├── analysis/                    # 分析中间产物
│   │   ├── {code}_insight_data.json # 财务数据（data_fetcher 输出）
│   │   ├── {code}_pe_band.json      # 历史估值分位数
│   │   ├── {code}_industry_compare.json  # 行业对比
│   │   ├── {code}_macro_impact.md   # 宏观影响评估
│   │   ├── {code}_risk_scan.json    # 风险扫描
│   │   ├── {code}_preprocessed.json # 估值预处理
│   │   ├── {code}_model_results.json # 估值模型结果
│   │   └── {code}_integrated.json   # 估值整合
│   └── reports/                     # 最终报告
│       ├── {code}_深度研判报告_{date}.md
│       ├── {code}_估值报告_{date}.md
│       ├── {code}_风险扫描_{date}.md
│       └── execution_audit_{date}.md
│
├── national-team/                   # 非个股分析（保持不变）
│   └── reports/{YYYY-MM-DD}/
│
└── industry-{name}/                 # 行业研究（新增）
    ├── state.json
    ├── analysis/
    └── reports/
```

#### 3.4.2 research-journal.jsonl（研究过程日志）

借鉴 Dexter 的 Scratchpad，为每次研究任务创建 JSONL 日志：

```jsonl
{"type":"init","timestamp":"2026-05-11T10:00:00","query":"全面研究 300014","agent":"deep-research"}
{"type":"skill_start","timestamp":"2026-05-11T10:00:01","skill":"a-stock-realtime","args":{"code":"300014","mode":"full"}}
{"type":"skill_end","timestamp":"2026-05-11T10:00:03","skill":"a-stock-realtime","status":"success","output":"cache/stock_info.json","duration_ms":2100}
{"type":"skill_start","timestamp":"2026-05-11T10:00:03","skill":"earnings-report-extractor","args":{"code":"300014","year":2025}}
{"type":"skill_end","timestamp":"2026-05-11T10:00:15","skill":"earnings-report-extractor","status":"success","output":"raw/earnings-pdf/300014_2025年年度报告.pdf","duration_ms":12000}
{"type":"quality_gate","timestamp":"2026-05-11T10:00:16","schema":"financial-statements","result":"pass","warnings":[]}
{"type":"analysis","timestamp":"2026-05-11T10:01:00","layer":"earnings-insight","summary":"七层分析完成，核心发现：营收增速25%超行业平均..."}
{"type":"done","timestamp":"2026-05-11T10:05:00","report":"reports/300014_深度研判报告_20260511.md","total_duration_ms":300000}
```

**实现方式**：不需要 Python 脚本，由 Agent 在 CLAUDE.md 中约束 —— 每次调用 skill 前后，用 bash `echo` 追加 JSONL 行到 journal.jsonl。

#### 3.4.3 缓存策略

| 数据类型 | 缓存 TTL | 说明 |
|---------|----------|------|
| 实时行情 (stock_info.json) | 当日有效 | 盘中数据隔日失效 |
| 财务报表数据 | 季度有效 | 季报发布后失效 |
| 宏观数据 | 7 天 | 月度数据，7天够用 |
| 行业列表 | 30 天 | 行业分类变化缓慢 |
| 财报 PDF | 永久 | 历史文件不变 |

缓存检查逻辑写入各 skill 的脚本中（已有模式：data_fetcher.py 中的 collaborative 模式）。

#### 3.4.4 新增数据契约

| 契约 | 文件 | 用途 |
|------|------|------|
| pe-band | `specs/contracts/pe-band.json` | PE/PB/PS 历史分位数 |
| industry-compare | `specs/contracts/industry-compare.json` | 行业对比分析输出 |
| risk-scan | `specs/contracts/risk-scan.json` | 风险扫描结果 |
| research-journal | `specs/contracts/research-journal.json` | 研究日志条目 |

---

### 3.5 SOUL.md — 投资理念文件

新增 `SOUL.md`，定义分析哲学（注入 CLAUDE.md 引用）：

```markdown
# SOUL.md — 投资分析理念

## 核心原则

1. **数据先行，结论在后**
   先收集数据，再形成观点。绝不先有结论再找数据支撑。

2. **逆向思维优先**
   先问"什么会让这笔投资失败"，再问"为什么会成功"。

3. **可证伪是好分析的标志**
   每个结论必须附带证伪条件。无法证伪的判断不是分析，是信仰。

4. **安全边际是底线**
   估值永远留出犯错空间。对未来的预测越乐观，要求的安全边际越大。

5. **区分事实与推测**
   已发生的（财务数据）> 可验证的趋势 > 管理层表述 > 情景推演。
   每个结论标注所依赖的信号层级。

## 分析禁区

- 不做短线技术分析式的买卖建议
- 不依赖单一估值模型的结果
- 不使用模糊的形容词替代具体数据
- 不忽视宏观环境和行业周期的影响
- 不将管理层的"愿景"等同于"事实"

## 偏好

- 偏好有稳定自由现金流的公司
- 关注 ROE 的来源（杜邦拆解）
- 重视行业竞争格局的变化
- 关注领先指标（合同负债、在建工程、研发投入）
- 重视估值的历史分位数位置
```

---

### 3.6 CLAUDE.md 修订

在现有 CLAUDE.md 基础上增加以下内容：

```markdown
## 研究流程约束（新增）

### 强制引用 SOUL.md
所有分析任务必须遵循 SOUL.md 中定义的投资分析理念。

### 研究日志（新增）
每次分析任务必须在 data/{code}/journal.jsonl 中记录完整过程。
日志格式遵循 specs/contracts/research-journal.json 契约。

记录时机：
- skill 调用前: {"type":"skill_start", ...}
- skill 调用后: {"type":"skill_end", ...}
- 质量门校验后: {"type":"quality_gate", ...}
- 分析完成时: {"type":"done", ...}

### Agent 调度规则（新增）
- Level 1 任务（单 skill）：调度者直接执行
- Level 2 任务（2-3 skills）：调度者编排执行
- Level 3 任务（复杂研究）：委派给专业 Agent

### 报告存储规则（新增，替代原规则）
- 所有研究报告必须存储在 data/{code}/reports/ 目录下
- 报告文件名格式: {code}_{报告类型}_{YYYYMMDD}.md
- 禁止将报告存放在 result_docs/ 或项目根目录
- result_docs/ 仅保留历史归档，不再新增

### Skill 调用去重（新增）
同一研究任务中，同一 skill 对同一股票代码只执行一次。
如需刷新数据，必须先清除 cache/ 中的对应缓存文件。
```

---

## 四、重构实施步骤

### Phase 1: 基础设施（第 1-2 天）

| 步骤 | 内容 | 产物 |
|------|------|------|
| 1.1 | 创建 SOUL.md | `SOUL.md` |
| 1.2 | 修订 CLAUDE.md（增加新约束） | 更新 `CLAUDE.md` |
| 1.3 | 创建新数据契约 | `specs/contracts/pe-band.json`, `industry-compare.json`, `risk-scan.json`, `research-journal.json` |
| 1.4 | 统一数据目录结构 | 更新目录规范说明，不迁移历史数据 |
| 1.5 | 创建 journal.jsonl 写入辅助脚本 | `scripts/journal.py`（可选，也可纯 bash echo） |

### Phase 2: 新 Skill 开发（第 3-5 天）

| 步骤 | 内容 | 产物 |
|------|------|------|
| 2.1 | 开发 pe-band-analysis skill | SKILL.md + `pe_band.py` |
| 2.2 | 开发 industry-compare skill | SKILL.md + `industry_compare.py` |
| 2.3 | 开发 macro-impact skill | SKILL.md（纯 Prompt，无脚本） |
| 2.4 | 开发 risk-scanner skill | SKILL.md + `risk_scanner.py` |
| 2.5 | 开发 report-generator skill | SKILL.md（纯 Prompt，无脚本） |

**优先级**：pe-band-analysis > industry-compare > risk-scanner > macro-impact > report-generator

### Phase 3: Agent 体系（第 5-7 天）

| 步骤 | 内容 | 产物 |
|------|------|------|
| 3.1 | 创建 research-lead Agent | `.claude/agents/research-lead.md` |
| 3.2 | 创建 deep-research Agent | `.claude/agents/deep-research.md` |
| 3.3 | 创建 valuation-agent Agent | `.claude/agents/valuation-agent.md` |
| 3.4 | 创建 industry-analyst Agent | `.claude/agents/industry-analyst.md` |
| 3.5 | 升级 reporter Agent | 更新 `.claude/agents/workflow-reporter.md` |

### Phase 4: 存量改造（第 7-8 天）

| 步骤 | 内容 | 说明 |
|------|------|------|
| 4.1 | stock-valuation 解耦 | 从 stock-valuation 中移除实时数据获取（按 todo.md），改为读取 cache/ |
| 4.2 | earnings-insight 增强 | Layer 3 行业分析增加自动调用 industry-compare 的引导 |
| 4.3 | 旧工作流标记废弃 | financial-report-full 和 quick-valuation 移入 deprecated/，由 Agent 体系替代 |
| 4.4 | 清理 result_docs/ | 历史报告按股票代码归档到 data/{code}/reports/（可选） |

### Phase 5: 验证与调优（第 8-10 天）

| 步骤 | 内容 |
|------|------|
| 5.1 | 端到端测试：选 2-3 只股票执行完整研究流程 |
| 5.2 | 验证 journal.jsonl 的完整性和可审计性 |
| 5.3 | 验证各 skill 的缓存和去重机制 |
| 5.4 | 验证 reporter 审计报告的质量 |
| 5.5 | 根据测试结果调整 Agent prompt 和 Skill 参数 |

---

## 五、新旧对照

### 5.1 用户场景对照

| 用户需求 | 旧方案 | 新方案 |
|---------|--------|--------|
| "全面研究 300014" | 必须跑 financial-report-full 全8阶段 | research-lead → deep-research Agent，自主编排 |
| "快速估值 002335" | quick-valuation 工作流（固定5阶段） | research-lead → Level 2 编排（realtime + valuation） |
| "对比 600309 和 300750" | 不支持 | research-lead → valuation-agent（多股对比模式） |
| "光伏行业分析" | 不支持 | research-lead → industry-analyst Agent |
| "看看 600519 的行情" | 直接调用 a-stock-realtime | research-lead → Level 1 直接执行 |
| "国家队最近动向" | national-team-tracker | 保持不变 |
| "估值 + 风险扫描" | 不支持组合 | research-lead → Level 2 编排（valuation + risk-scanner） |
| "300014 在行业里排第几" | 不支持 | research-lead → Level 1 调用 industry-compare |
| "当前宏观环境对 002335 影响" | 不支持 | research-lead → Level 2（macro-lite + macro-impact） |

### 5.2 分析深度对照

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| 宏观环境 | china-macro-lite 存在但未整合 | macro-impact skill 结构化评估宏观影响 |
| 行业对比 | 依赖用户手动提供 | industry-compare skill 自动获取排名 |
| 历史估值 | 无 | pe-band-analysis 提供分位数定位 |
| 风险识别 | 散落在 earnings-insight Layer 5 | risk-scanner 独立扫描 15 项风险指标 |
| 数据溯源 | 无 | journal.jsonl 记录完整研究过程 |
| 结论可证伪性 | 依赖 earnings-insight 的提示 | SOUL.md 全局约束 + 硬约束检查 |

---

## 六、文件变更清单

### 新增文件

```
SOUL.md                                          # 投资理念
.claude/agents/research-lead.md                  # 通用调度 Agent
.claude/agents/deep-research.md                  # 深度研究 Agent
.claude/agents/valuation-agent.md                # 估值 Agent
.claude/agents/industry-analyst.md               # 行业研究 Agent
.claude/skills/components/pe-band-analysis/      # 估值分位数 Skill
  ├── SKILL.md
  └── scripts/pe_band.py
.claude/skills/components/industry-compare/      # 行业对比 Skill
  ├── SKILL.md
  └── scripts/industry_compare.py
.claude/skills/components/macro-impact/          # 宏观影响 Skill
  └── SKILL.md
.claude/skills/components/risk-scanner/          # 风险扫描 Skill
  ├── SKILL.md
  └── scripts/risk_scanner.py
.claude/skills/components/report-generator/      # 报告生成 Skill
  └── SKILL.md
specs/contracts/pe-band.json                     # 估值分位数契约
specs/contracts/industry-compare.json            # 行业对比契约
specs/contracts/risk-scan.json                   # 风险扫描契约
specs/contracts/research-journal.json            # 研究日志契约
```

### 修改文件

```
CLAUDE.md                                        # 增加新约束规则
.claude/agents/workflow-reporter.md              # 增加日志审计能力
.claude/skills/components/stock-valuation/       # 解耦实时数据获取
.claude/skills/components/earnings-insight/      # Layer 3 增强
```

### 废弃文件（移入 deprecated/）

```
.claude/skills/workflows/financial-report-full/  → deprecated/
.claude/skills/workflows/quick-valuation/        → deprecated/
.claude/agents/workflow-lead.md                  → deprecated/（被 research-lead 替代）
```

### 保持不变

```
.claude/skills/components/a-stock-realtime/      # 保持
.claude/skills/components/china-macro-lite/       # 保持
.claude/skills/components/earnings-report-extractor/ # 保持
.claude/skills/components/finance/               # 保持
.claude/skills/components/financial-news/        # 保持
.claude/skills/workflows/national-team-tracker/  # 保持
.claude/skills/meta/workflow-creator/            # 保持（更新组件清单）
scripts/validate_contract.py                     # 保持
scripts/http_utils.py                            # 保持
specs/contracts/（现有契约）                       # 保持
```

---

## 七、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 新 skill 的脚本开发质量 | 数据获取失败影响分析 | 遵循现有零依赖模式，多级数据源回退 |
| Agent prompt 调度准确性 | 选错 skill 组合 | 在 research-lead 中详细列出路由规则和示例 |
| 东方财富 API 变更 | 数据源中断 | 已有 http_utils.py 重试 + 多源回退模式 |
| 历史数据迁移 | 旧报告丢失 | 不强制迁移，旧数据保留原位，新数据按新规范 |
| 工作流向 Agent 切换的过渡期 | 用户习惯改变 | 旧工作流标记 deprecated 但不删除，保留 30 天过渡 |

---

## 八、成功标准

1. **灵活性**：用户可以用自然语言描述任意组合的分析需求，research-lead 能正确路由
2. **深度**：每份深度报告必须包含宏观环境分析、行业对比、历史估值分位数
3. **严谨性**：每个数据结论可通过 journal.jsonl 追溯到原始 API 响应
4. **可控性**：所有分析产物在 data/{code}/ 下统一管理，state.json 完整记录状态
5. **零额外成本**：所有新 skill 使用免费公开 API，Python 标准库零依赖
