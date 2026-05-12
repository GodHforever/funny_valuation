---
name: industry-compare
description: "A股同行业公司对比分析。自动识别目标公司所属行业（申万分类），获取同行业上市公司的核心指标，进行排名和对比分析，支持可比公司估值法。零依赖（Python 3.6+ 标准库）。当用户需要了解某只股票在行业中的排名地位、与同行对比、可比公司估值时使用此skill。"
---

# Industry Compare — A股同行对比分析

## 功能概述

**零外部依赖**，仅用 Python 标准库（`urllib`、`json`），通过东方财富公开 API 获取行业数据。

### 核心能力

| 功能 | 说明 |
|------|------|
| **行业识别** | 根据股票代码自动获取所属行业（申万分类） |
| **行业排名** | 同行业公司按核心指标排名（市值/PE/PB/ROE/营收增速/净利增速） |
| **分位数定位** | 各指标在行业中的百分位排名 |
| **可比公司选取** | 自动选取 3-5 家规模/业务相近的可比公司 |
| **可比公司估值** | 基于可比公司平均估值推算隐含股价 |

### 数据源

- **东方财富行业板块 API** — 行业分类 + 成分股列表
- **东方财富 push2 API** — 批量行情（PE/PB/市值/涨跌幅）
- 读取 `data/{code}/cache/stock_info.json`（如已缓存）

## 使用方法

### 前置条件

**仅需 Python 3.6+**，无需安装任何第三方库。

### 运行脚本

```bash
# 基本行业对比
python {skill_path}/scripts/industry_compare.py --code 600519

# 指定输出目录
python {skill_path}/scripts/industry_compare.py --code 600519 --output-dir data/600519/analysis/

# JSON 格式
python {skill_path}/scripts/industry_compare.py --code 600519 --format json
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--code` | 股票代码（6位数字） | 必填 |
| `--top-n` | 排名展示数量 | 20 |
| `--output-dir` | 输出目录 | 控制台输出 |
| `--format` | 输出格式（text/json） | text |
| `--mode` | 运行模式（standalone/collaborative） | standalone |

### 输出文件

- `{code}_industry_compare.json` — 结构化 JSON（符合 `specs/contracts/industry-compare.json` 契约）
- `{code}_industry_compare.md` — 可读的行业对比报告

### 字段规范

遵循项目 CLAUDE.md 规范：`total_market_cap`（亿元）、`pe_ttm`（倍）、`pb_mrq`（倍）

## 错误处理

1. **行业识别失败**：尝试多种 API 路径获取行业分类
2. **批量请求限流**：分批请求，每批间隔 0.5 秒
3. **数据缺失**：部分公司指标缺失时标注，不影响整体排名

## 调用模式

### Mode A: 独立运行

```bash
python {skill_path}/scripts/industry_compare.py --code 600519
```

### Mode B: 协同运行（工作流调用）

```bash
python {skill_path}/scripts/industry_compare.py \
  --code 600519 --format json \
  --mode collaborative --output-dir data/600519/analysis/
```

**输出：** `data/{code}/analysis/{code}_industry_compare.json`

**输出契约:** `specs/contracts/industry-compare.json`

**质量门：**
```bash
python scripts/validate_contract.py \
  --schema specs/contracts/industry-compare.json \
  --data data/{code}/analysis/{code}_industry_compare.json
```
