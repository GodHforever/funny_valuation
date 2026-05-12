# funny_valuation — AI 驱动的 A 股投研分析系统

## 项目概述

基于 Claude Code Skill + Agent 编排架构的个人投研分析工具。通过自然语言描述研究需求，由 Agent 自主调度 Skill 组合完成分析任务。

## 架构

```
用户自然语言输入（Claude Code 对话）
    │
    ▼
┌─────────────────────────────────────────┐
│  约束层：CLAUDE.md + SOUL.md             │
│  (投资理念、数据规范、禁止模式)            │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  调度层：Agent 体系                       │
│  research-lead → 按复杂度路由            │
│    ├── Level 1: 单 Skill 直接执行        │
│    ├── Level 2: 编排 2-3 个 Skill        │
│    └── Level 3: 委派专业 Agent           │
│        ├── deep-research    (深度研究)    │
│        ├── valuation-agent  (估值分析)    │
│        └── industry-analyst (行业研究)    │
│  reporter → 审计                         │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  执行层：Skill 体系                       │
│  数据采集      分析          输出         │
│  a-stock-     earnings-    report-       │
│  realtime     insight      generator     │
│  china-macro  stock-                     │
│  -lite        valuation                  │
│  earnings-    pe-band-                   │
│  report-      analysis                   │
│  extractor    industry-                  │
│  finance      compare                    │
│  financial-   macro-impact               │
│  news         risk-scanner               │
└──────────────┬──────────────────────────┘
               ▼
┌─────────────────────────────────────────┐
│  数据层：data/{stock_code}/              │
│  cache/ → raw/ → analysis/ → reports/   │
└─────────────────────────────────────────┘
```

## 目录结构

```
.claude/
  agents/              # Agent 定义（research-lead, deep-research, valuation-agent, industry-analyst, workflow-reporter）
  skills/
    components/        # 组件 Skill（可独立运行，也可 Agent 调用）
    workflows/         # 工作流 Skill（national-team-tracker）
    meta/              # 元 Skill（workflow-creator）
    deprecated/        # 已废弃的旧工作流和 Agent

specs/
  contracts/           # 数据契约（JSON Schema）
  templates/           # 工作流模板
  data-directory-spec.md  # 数据目录规范

scripts/               # 共享脚本（validate_contract.py, http_utils.py）
docs/                  # 项目文档归档
config/                # 运行时配置

data/{stock_code}/     # 个股执行产物（不入 Git）
  cache/               # API 缓存
  raw/                 # 原始资料（PDF 等）
  analysis/            # 分析中间产物
  reports/             # 最终报告
  journal.jsonl        # 研究过程日志
  state.json           # 状态追踪

data/compare/          # 多股对比报告
data/industry-{name}/  # 行业研究
data/national-team/    # 国家队动向追踪
```

## 核心 Skill

| Skill | 类型 | 说明 |
|-------|------|------|
| a-stock-realtime | 数据采集 | A股实时行情（零依赖） |
| china-macro-lite | 数据采集 | 宏观经济数据（零依赖） |
| earnings-report-extractor | 数据采集 | 财报 PDF 提取 |
| earnings-insight | 分析 | 七层递进财报研判 |
| stock-valuation | 分析 | 8 模型交叉验证估值 |
| pe-band-analysis | 分析 | 历史估值分位数 |
| industry-compare | 分析 | 同行对比排名 |
| macro-impact | 分析 | 宏观影响评估（纯 Prompt） |
| risk-scanner | 分析 | 15 项风险扫描 |
| report-generator | 输出 | 4 种标准报告模板（纯 Prompt） |

## 技术要求

- Python 3.6+（所有脚本零外部依赖，仅标准库）
- Claude Code 运行环境
- 数据源：东方财富、腾讯、新浪等公开 API
