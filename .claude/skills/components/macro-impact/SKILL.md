---
name: macro-impact
description: "宏观经济环境对个股影响的结构化分析。基于 china-macro-lite 的宏观数据，结合个股所属行业特征，评估宏观因子对公司基本面的影响方向和程度。纯 Prompt 驱动（无脚本），调用 china-macro-lite 获取数据后由 LLM 分析。当用户需要了解宏观环境对某只股票的影响、利率/通胀/流动性等因子如何影响公司基本面时使用此skill。"
---

# Macro Impact — 宏观影响结构化分析

## 功能概述

**纯 Prompt 驱动**，无 Python 脚本。调用 china-macro-lite 获取宏观数据后，由 LLM 进行结构化分析。

### 分析框架（5 个维度）

| 维度 | 分析内容 | 数据来源 |
|------|---------|---------|
| **利率敏感性** | LPR/国债收益率变化对融资成本和估值的影响 | china-macro-lite 利率数据 |
| **通胀传导** | CPI/PPI 变化对公司成本和定价能力的影响 | china-macro-lite 价格数据 |
| **流动性环境** | M1/M2 变化对行业资金面的影响 | china-macro-lite 货币数据 |
| **汇率影响** | （如适用）出口占比、外币负债 | 财务数据 + 外汇储备 |
| **政策周期** | 当前宏观政策取向对行业的倾斜/压制 | 综合判断 |

### 每个维度的输出结构

```markdown
#### {维度名称}
- **影响方向**: 正面 / 负面 / 中性
- **影响程度**: 高 / 中 / 低
- **传导逻辑**: {一句话解释因果链}
- **数据支撑**: {引用具体宏观指标数值}
```

## 使用方法

### 前置步骤
1. 确保已运行 china-macro-lite 获取最新宏观数据
2. 确保已运行 a-stock-realtime 获取个股行情和行业信息

### 执行流程

#### Step 1: 获取宏观数据
```bash
python .claude/skills/components/china-macro-lite/scripts/macro_lite.py \
  --mode collaborative --output-dir data/{code}/cache/
```

#### Step 2: 获取个股行情
```bash
python .claude/skills/components/a-stock-realtime/scripts/realtime_fetcher.py \
  --code {code} --full --format json \
  --mode collaborative --output-dir data/{code}/cache/
```

#### Step 3: LLM 分析
基于上述数据，按 5 个维度逐一分析宏观环境对个股的影响。

### 输入
- `data/{code}/cache/macro_data.json` — 宏观数据
- `data/{code}/cache/stock_info.json` — 个股行情 + 行业信息
- `data/{code}/analysis/{code}_insight_data.json` — 财务数据（如可用，用于判断有息负债率/出口占比）

### 输出
- `data/{code}/analysis/{code}_macro_impact.md` — 宏观影响评估报告

## 输出模板

```markdown
# 宏观影响分析 — {stock_name}（{stock_code}）

> 分析日期: {date} | 行业: {industry}

## 综合影响评估
- **整体影响方向**: 正面 / 负面 / 中性
- **关键驱动因子**: {最重要的 1-2 个因子}

## 分项分析

### 1. 利率敏感性
- **影响方向**: {正面/负面/中性}
- **影响程度**: {高/中/低}
- **传导逻辑**: ...
- **数据支撑**: LPR-1Y={x}%, 10Y国债={y}%

### 2. 通胀传导
...

### 3. 流动性环境
...

### 4. 汇率影响
...

### 5. 政策周期
...

## 关注要点
- {未来需持续关注的宏观变量}
```

## 调用模式

### Mode A: 独立运行
用户直接调用，Skill 获取宏观数据 + 个股数据后生成分析报告。

### Mode B: 协同运行（工作流调用）
工作流调用时，假设宏观数据和个股数据已在 cache/ 中，直接读取分析。
输出写入 `data/{code}/analysis/{code}_macro_impact.md`。
