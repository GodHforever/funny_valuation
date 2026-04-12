# funny_valuation

AI 驱动的 A 股投研分析体系。基于财报数据的全链路自动化投研工作流。

## 项目结构

```
funny_valuation/
├── .claude/skills/                          # AI Skill 定义
│   ├── financial-statement-based-workflow/   # 核心工作流（编排层）
│   │   ├── SKILL.md                         # 工作流编排 prompt
│   │   ├── earnings-report-extractor/       # 财报 PDF 下载与提取
│   │   ├── earnings-insight/                # 七层深度研判分析
│   │   └── stock-valuation/                 # 多模型交叉估值
│   ├── real-time-expert-analysis/           # 实时专家分析（规划中）
│   └── deprecated/                          # 已废弃的 skill
│
├── data/                                    # 分析数据（不纳入版本控制）
│   ├── earnings-pdf/                        # 财报 PDF 原文
│   ├── earnings-extracted/                  # 财报结构化提取结果
│   ├── earnings-analysis/                   # 深度研判分析报告
│   ├── market-data/                         # 行情、财务、宏观数据
│   └── valuation-reports/                   # 估值分析报告
│
└── README.md
```

## 工作流

### 财报驱动投研（financial-statement-based-workflow）

五阶段递进的端到端投研流程：

```
下载财报 PDF → 提取关键章节 → 七层深度研判 → 采集行情数据 → 多模型估值
```

**快速开始：**

```
请对 {股票代码} 执行完整的财报投研分析
```

**前置依赖：**

```bash
pip install pdfplumber   # PDF 提取需要
```

详见 `.claude/skills/financial-statement-based-workflow/SKILL.md`。

## 数据目录说明

`data/` 下的所有分析产物**不纳入 Git 版本控制**（通过各子目录的 `.gitignore` 管理），但文件夹结构本身会保留。

| 目录 | 内容 | 来源阶段 |
|------|------|---------|
| `data/earnings-pdf/` | 年报/半年报/季报 PDF 原文 | Stage 1 |
| `data/earnings-extracted/` | 章节提取的 Markdown + JSON | Stage 2 |
| `data/earnings-analysis/` | 七层研判报告 + 财务数据 | Stage 3 |
| `data/market-data/` | 实时行情、行业对比、宏观数据 | Stage 4 |
| `data/valuation-reports/` | 估值模型结果 + 估值报告 | Stage 5 |