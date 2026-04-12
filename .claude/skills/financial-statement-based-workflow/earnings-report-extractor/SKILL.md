---
name: earnings-report-extractor
description: "A股财报PDF结构化提取工具。从巨潮资讯下载年报/半年报/季报PDF，智能识别章节结构（第X节），完整无损提取管理层讨论与分析(MD&A)、风险因素、董事长致辞、业务描述、审计报告等关键非财务章节内容，保存为独立Markdown文件并生成结构化索引。严格保持原文，零删减零改写。当用户需要提取年报PDF文本内容、分析年报章节结构、获取管理层讨论分析、风险因素、业务描述等非财务文本信息时使用此skill。"
---

# Earnings Report Extractor — A股财报PDF结构化提取

## 定位

将年报PDF转化为可搜索、可分析的结构化文本。**侧重非财务数据类内容**的完整、无损提取。

## 核心能力

1. **PDF下载** — 通过巨潮资讯API，按股票代码+年份自动下载年报/半年报/季报PDF
2. **章节识别** — 三级智能检测引擎，自动识别"第X节"标准结构及子章节
3. **完整提取** — 逐行精确提取，保持原文排版，嵌入表格（Markdown格式）
4. **原文保真** — 严格保持原始内容，不做任何删减、改写或摘要
5. **结构化输出** — 每章节独立文件 + 汇总索引 + JSON结构树

## 提取章节（按优先级）

### P1 — 核心章节（默认必须提取）

| 类别代码 | 章节 | 价值 |
|---------|------|------|
| `mda` | 管理层讨论与分析 | 最重要 — 业绩解读、业务板块分析、毛利率变化解释 |
| `risk` | 风险因素 | 新增/历史风险对比、措辞变化、行业与监管风险 |
| `chairman_letter` | 董事长致辞 | 战略信号、未来展望、情绪措辞 |
| `business` | 业务与产品描述 | 业务布局、边界变化、竞争优势、客户市场 |
| `audit` | 审计报告 | 意见类型、关键审计事项、持续经营假设 |

### P2 — 重要章节（默认提取）

| 类别代码 | 章节 |
|---------|------|
| `company_intro` | 公司简介与主要财务指标 |
| `important_tips` | 重要提示 |
| `governance` | 公司治理 |
| `important_matters` | 重要事项 |
| `esg` | 环境与社会责任 |
| `shareholders` | 股份变动及股东情况 |
| `directors_supervisors` | 董事、监事、高管情况 |

### P3 — 可选章节（需 `--all-sections`）

| 类别代码 | 章节 |
|---------|------|
| `financial_notes` | 财务报表附注 |
| `financial_statements` | 财务报告（三大报表） |
| `preferred_stock` | 优先股相关情况 |
| `bonds` | 债券相关情况 |

## 依赖

- **Python 3.6+**
- **pdfplumber** — PDF文本和表格提取

```bash
pip install pdfplumber
```

## 使用方法

```bash
# 下载并提取最新年报（默认提取P1+P2章节）
python {skill_path}/scripts/report_extractor.py --code 300014

# 指定年份
python {skill_path}/scripts/report_extractor.py --code 300014 --year 2024

# 直接处理本地PDF
python {skill_path}/scripts/report_extractor.py --pdf /path/to/report.pdf

# 仅查看章节结构（不提取）
python {skill_path}/scripts/report_extractor.py --code 300014 --list-sections

# 仅提取指定类别
python {skill_path}/scripts/report_extractor.py --code 300014 --sections mda,risk,audit

# 提取全部章节（含财务报表等）
python {skill_path}/scripts/report_extractor.py --code 300014 --all-sections

# 指定输出目录
python {skill_path}/scripts/report_extractor.py --code 300014 --output-dir ./output
```

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--code` | 股票代码(6位)，自动下载 | 与--pdf二选一 |
| `--pdf` | 本地PDF路径，直接处理 | 与--code二选一 |
| `--year` | 报告年份 | 最新 |
| `--report-type` | annual/half/q1/q3 | annual |
| `--output-dir` | 输出目录 | 当前目录 |
| `--sections` | 提取类别(逗号分隔) | P1+P2全部 |
| `--all-sections` | 提取全部章节 | false |
| `--list-sections` | 仅列出章节结构 | false |

## 输出文件

| 文件 | 说明 |
|------|------|
| `{code}_第X节_章节名.md` | 各章节的完整提取内容（含YAML元信息头） |
| `{code}_提取汇总.md` | 提取索引（所有章节列表、已提取列表、统计） |
| `{code}_structure.json` | 结构化章节树（JSON，便于程序使用） |
| `{code}_*.pdf` | 原始PDF文件 |

### 章节文件格式

每个提取文件包含 YAML frontmatter 元信息：

```markdown
---
source: PDF文件名
company: 公司名称
stock_code: 股票代码
report_year: 报告年份
section_title: 章节标题
page_range: 起始页-结束页
total_chars: 字符数
extracted_at: 提取时间
---

# 章节标题

（完整原文内容，含 <!-- page:N --> 页码标记）
```

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 部分章节提取失败 |
| 2 | 依赖/环境错误 |
| 3 | PDF下载失败 |
| 4 | 文本提取失败 |
