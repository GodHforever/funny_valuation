---
name: financial-report-full
description: "A股财报完整投研工作流。端到端自动化：PDF下载 → 章节提取 → 七层深度分析 → 市场数据 → 八模型估值。Lead+Workers 模式，数据契约驱动，质量门控制。"
---

# A股财报完整投研工作流

## 架构

本工作流采用 Lead+Workers 模式：
- **Lead Agent**（workflow-lead）：管理状态机、分派任务、校验质量门
- **Component Skills**：各阶段执行，均以 Mode B（协同）模式调用
- **Reporter Agent**（workflow-reporter）：执行结束后自动审计

## 输入参数

| 参数 | 必须 | 默认值 | 说明 |
|---|---|---|---|
| stock_code | 是 | — | 6位A股代码 |
| report_year | 否 | 最新 | 财报年份 |
| report_type | 否 | annual | annual/half/q1/q3 |

## 触发方式
用户说"对 {stock_code} 执行完整财报投研分析"或类似表述时触发。

## 执行流程

### Stage 0: 初始化

**操作：**
1. 创建 `data/{stock_code}/` 及子目录（earnings-pdf, earnings-extracted, earnings-analysis, market-data, valuation-reports）
2. 初始化 `data/{stock_code}/state.json`（参照 specs/contracts/workflow-state.json）
3. 验证股票代码格式

**质量门：** 目录和 state.json 创建成功
**失败策略：** 阻断

### Stage 1: 下载财报 PDF

**组件：** earnings-report-extractor（下载模式）
**命令：**
```bash
python .claude/skills/components/earnings-report-extractor/scripts/report_extractor.py \
  --code {stock_code} --year {report_year} --report-type {report_type} \
  --output-dir data/{stock_code}/earnings-pdf/ --list-sections
```

**输出：** `data/{stock_code}/earnings-pdf/{code}_*.pdf`
**质量门：** PDF 文件存在且大小 > 500KB（年报）
**失败策略：** 提示用户手动提供 PDF 路径，用户提供后更新 state.json 继续
**状态转换：** init → stage1_running → stage1_validated

### Stage 2: 提取关键章节

**组件：** earnings-report-extractor（提取模式）
**命令：**
```bash
python .claude/skills/components/earnings-report-extractor/scripts/report_extractor.py \
  --pdf data/{stock_code}/earnings-pdf/{pdf_file} \
  --output-dir data/{stock_code}/earnings-extracted/ \
  --sections mda,risk,chairman_letter,business,audit,company_intro,important_matters
```

**输出：**
- `data/{stock_code}/earnings-extracted/{code}_第X节_*.md`
- `data/{stock_code}/earnings-extracted/{code}_structure.json`

**质量门：** MD&A（管理层讨论与分析）文件存在且字符数 > 1000
**失败策略：** 如 MD&A 缺失，记录 WARNING，后续 Stage 3 跳过 Layer 2（批判性阅读）
**状态转换：** stage1_validated → stage2_running → stage2_validated

### Stage 3: 金融数据获取 + 深度分析

本阶段包含 3 个并行子任务和 1 个串行任务：

**子任务 3a（并行）：获取结构化金融数据**
```bash
python .claude/skills/components/earnings-insight/scripts/data_fetcher.py \
  --code {stock_code} --mode collaborative --data-dir data/{stock_code}/
```
输出: `data/{stock_code}/earnings-analysis/{code}_insight_data.json` + `data/{stock_code}/market-data/stock_info.json`

**子任务 3b（并行）：获取宏观数据**
```bash
python .claude/skills/components/china-macro-lite/scripts/macro_lite.py \
  --category 价格,货币,利率,市场 --mode collaborative \
  --output-dir data/{stock_code}/market-data/
```
输出: `data/{stock_code}/market-data/macro_data.json`

**子任务 3c（并行）：获取实时市场数据（可选）**
如果 finance 或 stock-analysis skill 可用，获取行业对比、技术指标等补充数据。
输出: `data/{stock_code}/market-data/` 下补充文件

**串行任务 3d：七层深度分析**
等待 3a 和 Stage 2 完成后，基于 earnings-insight SKILL.md 的七层分析框架执行分析：
- 读取 `data/{stock_code}/earnings-analysis/{code}_insight_data.json`（金融数据）
- 读取 `data/{stock_code}/earnings-extracted/` 下的章节文件（MD&A、风险、业务描述等）
- 读取 `data/{stock_code}/market-data/macro_data.json`（宏观环境）
- 执行七层递进分析，生成研判报告

输出: `data/{stock_code}/earnings-analysis/{code}_{name}_深度研判报告.md`

**质量门：**
```bash
python scripts/validate_contract.py \
  --schema specs/contracts/financial-statements.json \
  --data data/{stock_code}/earnings-analysis/{code}_insight_data.json
```
- critical_missing 为空 → 通过
- critical_missing 非空但 income_data.status == "success" → 降级继续
- income_data.status == "failed" → 阻断

**失败策略：** 金融数据获取完全失败 → 阻断。部分失败 → 记录 WARNING，继续
**状态转换：** stage2_validated → stage3_running → stage3_validated

### Stage 4: 估值数据收集（读缓存）

**组件：** stock-valuation（数据收集阶段）
**命令：**
```bash
python .claude/skills/components/stock-valuation/scripts/valuation_data.py \
  --code {stock_code} --mode collaborative \
  --data-dir data/{stock_code}/ \
  --output-dir data/{stock_code}/valuation-reports/
```

**关键行为：** collaborative 模式会读取 `data/{stock_code}/market-data/stock_info.json`（Stage 3a 产出），避免重复 API 调用。

**输出：** `data/{stock_code}/valuation-reports/{code}_valuation_data.json`

**质量门：**
```bash
python scripts/validate_contract.py \
  --schema specs/contracts/financial-statements.json \
  --data data/{stock_code}/valuation-reports/{code}_valuation_data.json
```
关键检查：
- `stock_info.current_price` 非空
- `stock_info.total_market_cap` 非空
- `income_data.status` == "success"

**失败策略：** current_price 或 total_market_cap 缺失 → 阻断（无法估值）
**状态转换：** stage3_validated → stage4_running → stage4_validated

### Stage 5: 估值流水线

四步串行子阶段，每步有独立质量门：

**5.1 预处理**
```bash
python .claude/skills/components/stock-valuation/scripts/valuation_preprocessor.py \
  --data-file data/{stock_code}/valuation-reports/{code}_valuation_data.json \
  --output-dir data/{stock_code}/valuation-reports/ \
  --qualitative-doc data/{stock_code}/earnings-analysis/{code}_{name}_深度研判报告.md
```
质量门: available_models count >= 3, wacc 非零
失败策略: wacc 为零 → 使用 Ke 作为 fallback，记录 WARNING

**5.2 模型计算**
```bash
python .claude/skills/components/stock-valuation/scripts/valuation_models.py \
  --preprocessed-file data/{stock_code}/valuation-reports/{code}_preprocessed.json \
  --output-dir data/{stock_code}/valuation-reports/
```
质量门: 至少 3 个模型 applicable
失败策略: < 3 个模型 → 记录 WARNING，继续（降低置信度）

**5.3 加权整合**
```bash
python .claude/skills/components/stock-valuation/scripts/valuation_integrator.py \
  --data-file data/{stock_code}/valuation-reports/{code}_valuation_data.json \
  --preprocessed-file data/{stock_code}/valuation-reports/{code}_preprocessed.json \
  --model-results-file data/{stock_code}/valuation-reports/{code}_model_results.json \
  --output-dir data/{stock_code}/valuation-reports/
```
质量门: 至少一个模型 weight > 0
失败策略: 所有权重为 0 → 阻断（数据质量不足以进行估值）

**5.4 报告生成**
```bash
python .claude/skills/components/stock-valuation/scripts/valuation_report.py \
  --integrated-file data/{stock_code}/valuation-reports/{code}_integrated.json \
  --data-file data/{stock_code}/valuation-reports/{code}_valuation_data.json \
  --preprocessed-file data/{stock_code}/valuation-reports/{code}_preprocessed.json \
  --model-results-file data/{stock_code}/valuation-reports/{code}_model_results.json \
  --output-dir data/{stock_code}/valuation-reports/
```
质量门: 报告文件存在且 > 2000 字符

**状态转换：** stage4_validated → stage5_running → stage5_validated

### Stage 6: 执行审计

**组件：** workflow-reporter Agent
**操作：** 启动一个新 Agent（使用 .claude/agents/workflow-reporter.md 定义），传入 `data/{stock_code}/state.json` 路径
**输出：** `data/{stock_code}/execution_audit_{YYYYMMDD}.md`
**状态转换：** stage5_validated → audit_running → done

### Stage 7: 结果呈现

向用户呈现：
1. 深度研判报告的核心结论（Layer 6）
2. 估值分析报告的价值区间和买入建议
3. 执行审计报告的关键发现
4. 如有问题，给出改进建议

## 状态机

```
init → stage1_running → stage1_validated
     → stage2_running → stage2_validated
     → stage3_running → stage3_validated
     → stage4_running → stage4_validated
     → stage5_running → stage5_validated
     → audit_running → done

每阶段可进入 {stage}_failed:
- stage1_failed: PDF下载失败 → 等待用户提供 → stage1_validated
- stage2_failed: 提取失败 → 降级继续（跳过 Layer 2）→ stage2_validated(degraded)
- stage3_failed: API 全部失败 → 阻断
- stage4_failed: 关键字段缺失 → 阻断
- stage5_failed: 估值失败 → 阻断
```

## 数据流图

```
Stage 1          Stage 2          Stage 3                  Stage 4        Stage 5
[PDF下载]  →  [章节提取]  →  [数据获取+深度分析]  →  [估值数据]  →  [估值流水线]
                              ├── data_fetcher (3a)
                              ├── macro_lite (3b)        读取3a缓存      读取4产出
                              ├── market_data (3c)       避免重复API
                              └── 七层分析 (3d)
                                   ↓
                              深度研判报告 ──────────────────────→ 5.1 定性输入
```

## 数据单位规范

遵循 CLAUDE.md 中定义的单位规范。所有阶段间 JSON 数据使用规范字段名（current_price, total_market_cap, pb_mrq 等）。

## 断点续传

如果工作流中断，可通过读取 `data/{stock_code}/state.json` 的 `current_phase` 字段，从最后完成的阶段之后继续执行。已完成阶段的产出文件不会重新生成。
