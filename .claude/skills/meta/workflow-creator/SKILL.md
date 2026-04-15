---
name: workflow-creator
description: "工作流创建向导。引导用户设计新工作流，自动生成 SKILL.md、数据契约和状态机定义。确保所有新工作流遵循项目统一范式。"
---

# 工作流创建向导

## 用途
当需要创建新的分析工作流时使用此 Skill。通过三步向导流程，确保新工作流遵循项目的统一范式。

## 触发方式
用户说"创建新工作流"、"新建一个工作流"或类似表述时触发。

## 三步流程

### Step 1: 需求分析与复杂度评估

向用户收集以下信息：

1. **工作流名称和目标**：这个工作流要完成什么？
2. **涉及的数据源**：需要哪些数据？（财报PDF、API数据、用户输入等）
3. **处理阶段**：需要几个处理步骤？每步做什么？
4. **可复用的组件**：是否可以使用已有的组件 Skill？

**复杂度判定规则：**

| 条件 | 判定 |
|---|---|
| 阶段数 <= 3 且无并行任务 | **Pipeline**（单Agent顺序执行） |
| 阶段数 > 3 | **Lead+Workers**（多Agent协作） |
| 有并行数据获取 | **Lead+Workers** |
| 需要中途等待用户输入 | **Lead+Workers** |
| 仅串行数据处理 | **Pipeline** |

### Step 2: 阶段定义

对每个阶段，确定：

1. **阶段名称**：简洁描述（如"数据获取"、"深度分析"）
2. **执行组件**：使用哪个组件 Skill？（从 .claude/skills/components/ 中选择）
   - 如果没有现成组件，记录为"自定义"并描述具体操作
3. **输入**：从哪里读取数据？（前序阶段输出 / 用户输入 / API）
4. **输出**：产出什么文件？写入什么路径？
5. **数据契约**：
   - 是否有已有的 specs/contracts/ 下的 schema 可以复用？
   - 如果没有，需要创建新的 schema
6. **质量门**：校验条件是什么？（validate_contract.py 命令 / 文件大小检查 / 自定义条件）
7. **失败策略**：失败时阻断还是降级？如何恢复？

### Step 3: 产物生成

根据 Step 1-2 的结果，生成以下文件：

#### 3a. 工作流 SKILL.md

根据复杂度判定选择模板：
- Pipeline → 参考 `specs/templates/pipeline-workflow.md.template`
- Lead+Workers → 参考 `specs/templates/team-workflow.md.template`

生成到 `.claude/skills/workflows/{workflow-name}/SKILL.md`

包含：
- 工作流描述和触发方式
- 输入参数表
- 每阶段的完整定义（组件、命令、输入、输出、契约、质量门、失败策略）
- 状态机定义
- 数据流图
- 断点续传说明

#### 3b. 新数据契约（如需）

如果 Step 2 中确定需要新的 schema，创建到 `specs/contracts/{name}.json`。
遵循现有 schema 的格式规范（参照 stock-basic-info.json）。

#### 3c. 更新 CLAUDE.md（如需）

如果新工作流引入了新的字段名或数据单位规范，更新 CLAUDE.md 中的规范表。

## 已有组件 Skill 清单

| 组件 | 路径 | 功能 | Mode B 命令示例 |
|---|---|---|---|
| earnings-report-extractor | components/earnings-report-extractor/ | PDF下载+章节提取 | `python .../report_extractor.py --code {code} --output-dir data/{code}/earnings-pdf/` |
| earnings-insight | components/earnings-insight/ | 金融数据获取+七层分析 | `python .../data_fetcher.py --code {code} --mode collaborative --data-dir data/{code}/` |
| stock-valuation | components/stock-valuation/ | 八模型估值 | `python .../valuation_data.py --code {code} --mode collaborative --data-dir data/{code}/` |
| china-macro-lite | components/china-macro-lite/ | 宏观经济数据 | `python .../macro_lite.py --mode collaborative --output-dir data/{code}/market-data/` |
| finance | components/finance/ | 实时市场数据(API) | 通过 Finance API 调用 |
| financial-news | components/financial-news/ | 财经新闻 | 通过 ticker-cli 调用 |
| stock-analysis | components/stock-analysis/ | 技术分析 | 通过 ticker-cli 调用 |

## 已有数据契约清单

| 契约 | 路径 | 用途 |
|---|---|---|
| stock-basic-info | specs/contracts/stock-basic-info.json | 股票基本信息+实时行情 |
| financial-statements | specs/contracts/financial-statements.json | 统一原始财务数据 |
| valuation-input | specs/contracts/valuation-input.json | 估值预处理输出 |
| market-data | specs/contracts/market-data.json | 宏观经济数据 |
| workflow-state | specs/contracts/workflow-state.json | 工作流执行状态 |

## 已有工作流范例

| 工作流 | 路径 | 模式 | 阶段数 |
|---|---|---|---|
| financial-report-full | workflows/financial-report-full/ | Lead+Workers | 8 |
| quick-valuation | workflows/quick-valuation/ | Pipeline | 6 |

## 范式检查清单

生成工作流后，对照以下清单确认：

- [ ] 每个阶段都有明确的输入和输出路径
- [ ] 每个阶段都有质量门定义
- [ ] 每个阶段都有失败策略（阻断 / 降级 / 恢复）
- [ ] 状态机覆盖所有阶段的 running/validated/failed 状态
- [ ] 所有脚本路径引用有效
- [ ] 所有 schema 路径引用有效
- [ ] 字段名符合 CLAUDE.md 规范
- [ ] 数据单位符合 CLAUDE.md 规范
- [ ] 数据目录遵循 data/{stock_code}/ 组织结构
- [ ] 包含断点续传说明
