---
name: quick-valuation
description: "快速A股估值工作流。跳过PDF提取和深度分析，直接获取金融数据执行八模型估值。Pipeline模式，单Agent执行，适合快速估值参考。"
---

# 快速估值工作流

## 架构

Pipeline 模式：单 Agent 按阶段顺序执行，无需 Lead+Workers 协调。

## 输入参数

| 参数 | 必须 | 默认值 | 说明 |
|---|---|---|---|
| stock_code | 是 | — | 6位A股代码 |

## 触发方式
用户说"快速估值 {stock_code}"或类似表述时触发。

## 执行流程

### Stage 1: 初始化 + 数据获取
创建 data/{stock_code}/ 目录，初始化 state.json。
执行 valuation_data.py（standalone 模式），获取金融数据。

```bash
mkdir -p data/{stock_code}/valuation-reports
python .claude/skills/components/stock-valuation/scripts/valuation_data.py \
  --code {stock_code} --output-dir data/{stock_code}/valuation-reports/
```

质量门: `{code}_valuation_data.json` 存在，stock_info.current_price 非空

### Stage 2: 预处理
执行 valuation_preprocessor.py（Mode B 数据模式，无定性输入）。

```bash
python .claude/skills/components/stock-valuation/scripts/valuation_preprocessor.py \
  --data-file data/{stock_code}/valuation-reports/{code}_valuation_data.json \
  --output-dir data/{stock_code}/valuation-reports/
```

质量门: available_models >= 3

### Stage 3: 模型计算
执行 valuation_models.py。

```bash
python .claude/skills/components/stock-valuation/scripts/valuation_models.py \
  --preprocessed-file data/{stock_code}/valuation-reports/{code}_preprocessed.json \
  --output-dir data/{stock_code}/valuation-reports/
```

质量门: >= 3 个模型 applicable

### Stage 4: 加权整合
执行 valuation_integrator.py。

```bash
python .claude/skills/components/stock-valuation/scripts/valuation_integrator.py \
  --data-file data/{stock_code}/valuation-reports/{code}_valuation_data.json \
  --preprocessed-file data/{stock_code}/valuation-reports/{code}_preprocessed.json \
  --model-results-file data/{stock_code}/valuation-reports/{code}_model_results.json \
  --output-dir data/{stock_code}/valuation-reports/
```

质量门: 至少一个 weight > 0

### Stage 5: 报告生成
执行 valuation_report.py。

```bash
python .claude/skills/components/stock-valuation/scripts/valuation_report.py \
  --integrated-file data/{stock_code}/valuation-reports/{code}_integrated.json \
  --data-file data/{stock_code}/valuation-reports/{code}_valuation_data.json \
  --preprocessed-file data/{stock_code}/valuation-reports/{code}_preprocessed.json \
  --model-results-file data/{stock_code}/valuation-reports/{code}_model_results.json \
  --output-dir data/{stock_code}/valuation-reports/
```

质量门: 报告 > 2000 字符

### Stage 6: 呈现结果
向用户呈现价值区间、安全边际、买入区间建议。

## 与完整工作流的区别

| 维度 | 完整工作流 | 快速估值 |
|---|---|---|
| 架构 | Lead+Workers | Pipeline (单Agent) |
| PDF 提取 | 有 | 无 |
| 深度分析 | 七层分析 | 无 |
| 估值模式 | Mode A（含定性） | Mode B（纯数据） |
| 宏观数据 | 有 | 无 |
| 审计报告 | 有 | 无 |
| 适用场景 | 完整投研报告 | 快速估值参考 |
