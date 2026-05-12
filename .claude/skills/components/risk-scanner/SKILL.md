---
name: risk-scanner
description: "个股风险快速扫描工具。自动检测15项风险信号（财务风险5项+治理风险3项+市场风险3项+合规1项+集中度2项+估值1项），输出红/橙/绿三级告警和综合风险评级。零依赖（Python 3.6+ 标准库）。当用户需要快速了解某只股票的风险水平、是否存在财务风险信号、大股东质押/减持等问题时使用此skill。"
---

# Risk Scanner — 个股风险快速扫描

## 功能概述

**零外部依赖**，仅用 Python 标准库（`urllib`、`json`），通过东方财富公开 API 检测 15 项风险指标。

### 扫描项（15 项）

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

### 综合风险评级

| 评级 | 判定条件 |
|------|---------|
| 低风险 | 0 红色，≤2 橙色 |
| 中风险 | 0 红色，>2 橙色 |
| 高风险 | 1-2 红色 |
| 极高风险 | ≥3 红色 |

### 数据源

- **东方财富 push2 API** — 基础行情（ST 标记、换手率）
- **东方财富财务数据 API** — 财务报表数据（商誉、应收、负债、现金流）
- **东方财富质押/减持 API** — 股东质押率、高管减持
- 读取 `data/{code}/cache/` 和 `data/{code}/analysis/` 下已有数据

## 使用方法

### 前置条件

**仅需 Python 3.6+**，无需安装任何第三方库。

### 运行脚本

```bash
# 执行风险扫描
python {skill_path}/scripts/risk_scanner.py --code 600519

# 指定输出目录
python {skill_path}/scripts/risk_scanner.py --code 600519 --output-dir data/600519/analysis/

# JSON 格式
python {skill_path}/scripts/risk_scanner.py --code 600519 --format json
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--code` | 股票代码（6位数字） | 必填 |
| `--output-dir` | 输出目录 | 控制台输出 |
| `--format` | 输出格式（text/json） | text |
| `--mode` | 运行模式（standalone/collaborative） | standalone |

### 输出文件

- `{code}_risk_scan.json` — 结构化 JSON（符合 `specs/contracts/risk-scan.json` 契约）
- `{code}_risk_scan.md` — 可读的风险扫描报告

## 错误处理

1. **数据不可用**：单项检测数据缺失时标记 `alert_level: "green"` 并注明 "数据不可用"
2. **API 回退**：主 API 失败时尝试备用数据源
3. **已有数据复用**：优先从 `cache/` 和 `analysis/` 读取已有数据，减少重复请求

## 调用模式

### Mode A: 独立运行

```bash
python {skill_path}/scripts/risk_scanner.py --code 600519
```

### Mode B: 协同运行（工作流调用）

```bash
python {skill_path}/scripts/risk_scanner.py \
  --code 600519 --format json \
  --mode collaborative --output-dir data/600519/analysis/
```

**输出：** `data/{code}/analysis/{code}_risk_scan.json`

**输出契约:** `specs/contracts/risk-scan.json`

**质量门：**
```bash
python scripts/validate_contract.py \
  --schema specs/contracts/risk-scan.json \
  --data data/{code}/analysis/{code}_risk_scan.json
```
