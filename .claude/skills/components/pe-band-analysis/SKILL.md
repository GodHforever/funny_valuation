---
name: pe-band-analysis
description: "A股个股历史估值分位数分析。获取近N年PE/PB/PS历史数据，计算当前估值在历史区间中的分位数位置，判断估值高低。零依赖（Python 3.6+ 标准库）。当用户需要了解某只股票当前估值在历史中的位置、PE/PB/PS Band图、估值是否偏高偏低时使用此skill。"
---

# PE Band Analysis — A股历史估值分位数分析

## 功能概述

**零外部依赖**，仅用 Python 标准库（`urllib`、`json`），通过东方财富公开 API 获取个股历史估值数据。

### 核心能力

| 功能 | 说明 |
|------|------|
| **历史估值序列** | 获取近 3/5/10 年的 PE(TTM)、PB(MRQ)、PS(TTM) 历史日频数据 |
| **分位数计算** | 当前值在历史分布中的百分位排名 |
| **统计指标** | 最小值、最大值、中位数、均值、标准差 |
| **估值判定** | 基于分位数的五级判定 |

### 分位数判定标准

| 分位数范围 | 判定 | 说明 |
|-----------|------|------|
| 0-10% | 极度低估 | 历史最低区间 |
| 10-30% | 低估 | 低于历史均值一个标准差 |
| 30-70% | 合理 | 历史均值附近 |
| 70-90% | 高估 | 高于历史均值一个标准差 |
| 90-100% | 极度高估 | 历史最高区间 |

### 数据源

- **东方财富 push2his API** — 历史 K 线 + 估值指标（PE/PB/PS），零依赖
- 读取 `data/{code}/cache/stock_info.json`（如已缓存，获取当前值）

## 使用方法

### 前置条件

**仅需 Python 3.6+**，无需安装任何第三方库。

### 运行脚本

```bash
# 默认 5 年分析
python {skill_path}/scripts/pe_band.py --code 600519

# 指定年限
python {skill_path}/scripts/pe_band.py --code 600519 --years 10

# 指定输出目录
python {skill_path}/scripts/pe_band.py --code 600519 --output-dir data/600519/analysis/

# JSON 格式输出
python {skill_path}/scripts/pe_band.py --code 600519 --format json
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--code` | 股票代码（6位数字） | 必填 |
| `--years` | 分析年限（3/5/10） | 5 |
| `--output-dir` | 输出目录 | 控制台输出 |
| `--format` | 输出格式（text/json） | text |
| `--mode` | 运行模式（standalone/collaborative） | standalone |

### 输出文件

- `{code}_pe_band.json` — 结构化 JSON（符合 `specs/contracts/pe-band.json` 契约）
- `{code}_pe_band_report.md` — 可读的 Markdown 分析报告

### 字段规范

遵循项目 CLAUDE.md 规范：

| 字段名 | 单位 | 说明 |
|--------|------|------|
| `pe_ttm` | 倍 | 市盈率TTM |
| `pb_mrq` | 倍 | 市净率MRQ |
| `ps_ttm` | 倍 | 市销率TTM |

## 错误处理

1. **数据不足**：历史数据少于 60 个交易日时，标记 `status: "partial"`，在报告中注明
2. **API 回退**：主 API 失败时尝试备用接口
3. **异常值过滤**：PE 为负值时排除（亏损期不参与分位数计算）

## 调用模式

### Mode A: 独立运行

```bash
python {skill_path}/scripts/pe_band.py --code 600519
```

### Mode B: 协同运行（工作流调用）

```bash
python {skill_path}/scripts/pe_band.py \
  --code 600519 --years 5 --format json \
  --mode collaborative --output-dir data/600519/analysis/
```

**输出：** `data/{code}/analysis/{code}_pe_band.json`

**输出契约:** `specs/contracts/pe-band.json`

**质量门：**
```bash
python scripts/validate_contract.py \
  --schema specs/contracts/pe-band.json \
  --data data/{code}/analysis/{code}_pe_band.json
```
