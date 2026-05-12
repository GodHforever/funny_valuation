---
name: stock-valuation
description: "A股综合估值分析工具。实现DCF、PE、PB、EV/EBITDA、PS、DDM、SOTP、逆向工程共8个估值模型的多模型交叉验证估值。支持完整模式（定性分析+财务数据）和数据模式（仅财务数据）。输出价值区间、安全边际、动态买入区间和估值陷阱检测报告。当用户需要对股票进行估值分析、判断股票是否值得买入、计算内在价值、确定合理买入价格、分析市场隐含预期时使用此skill。"
---

# stock-valuation — A股综合估值分析

> 估值不是算出一个"正确价格"，而是**建立价值区间与安全边际的认知体系**。单一模型的结果没有意义，多模型交叉验证、结合研判情景才能形成有效判断。

## 核心能力

- 8大估值模型：DCF、PE、PB、EV/EBITDA、PS、DDM、SOTP、逆向工程
- 多模型交叉验证 + 加权综合估值
- 7种公司阶段自动判定 + 差异化权重分配
- 三情景分析（悲观/基准/乐观）+ DCF 敏感性矩阵
- 估值陷阱检测（7项财务/估值/业务检测）
- 动态安全边际 + 5级买入区间
- 逆向工程分析市场隐含预期

## 两种运行模式

- **模式A（完整）**：财报定性分析 + 财务数据，置信度最高
- **模式B（数据）**：仅财务数据，采用保守默认值，置信度下调

## 使用流程

### Phase 0: 输入解析

确认以下信息：
1. 股票代码（6位纯数字 → A股）
2. 输出目录（默认 `data/{code}/analysis/`）
3. 是否有财报定性分析文档（可选，决定模式A/B）

### Phase 1: 数据准备（从缓存读取）

> **解耦变更**：不再直接调用 API 获取实时数据。数据应由上游 Skill 预先获取并缓存。

**前置条件**：以下 Skill 应已执行，数据已在 `data/{code}/cache/` 中：
- `a-stock-realtime`：实时行情（stock_info.json）
- `earnings-insight` 的 data_fetcher：财务数据（insight_data.json）

**估值数据采集（从缓存组装）：**
```bash
python {skill_path}/scripts/valuation_data.py --code {code} \
  --mode collaborative --data-dir data/{code}/ \
  --output-dir data/{code}/analysis/
```

> 当 `--data-dir` 指定且 cache/ 中有数据时，valuation_data.py 优先从缓存读取而非重新请求 API。

**宏观数据（如需，从缓存读取）：**
```bash
# 如 cache/macro_data.json 不存在，运行 china-macro-lite
python .claude/skills/components/china-macro-lite/scripts/macro_lite.py \
  --category 利率 --mode collaborative --output-dir data/{code}/cache/
```

### Phase 2: 预处理

```bash
python {skill_path}/scripts/valuation_preprocessor.py \
    --data-file data/{code}/analysis/{code}_valuation_data.json \
    --output-dir data/{code}/analysis/ \
    [--qualitative-doc data/{code}/analysis/{code}_深度研判报告.md]
```

### Phase 3: 模型计算

```bash
python {skill_path}/scripts/valuation_models.py \
    --preprocessed-file data/{code}/analysis/{code}_preprocessed.json \
    --output-dir data/{code}/analysis/
```

### Phase 4: 整合分析

```bash
python {skill_path}/scripts/valuation_integrator.py \
    --data-file data/{code}/analysis/{code}_valuation_data.json \
    --preprocessed-file data/{code}/analysis/{code}_preprocessed.json \
    --model-results-file data/{code}/analysis/{code}_model_results.json \
    --output-dir data/{code}/analysis/
```

### Phase 5: 报告生成

```bash
python {skill_path}/scripts/valuation_report.py \
    --integrated-file data/{code}/analysis/{code}_integrated.json \
    --data-file data/{code}/analysis/{code}_valuation_data.json \
    --preprocessed-file data/{code}/analysis/{code}_preprocessed.json \
    --model-results-file data/{code}/analysis/{code}_model_results.json \
    --output-dir data/{code}/reports/ [--visualize]
```

### Phase 6: 结果呈现

读取生成的估值分析报告，向用户呈现关键结论：
- 加权综合估值区间（悲观/基准/乐观）
- 当前价格定位（5级区间判断）
- 买入建议（安全边际 + 买入区间）
- 主要风险提示（陷阱检测结果）

## 上下文管理

- 每个阶段的中间数据保存到磁盘（JSON文件），避免上下文膨胀
- 如果数据获取阶段返回内容过多，不将原始JSON内容放入上下文，仅保留摘要
- 报告生成阶段使用独立 Agent 运行，防止累积上下文溢出

## 聚焦回答模式

当用户已有估值报告，针对具体问题无需重新运行全流程：

| 用户问题 | 调用模块 |
|---------|---------|
| "便宜吗/贵不贵" | 估值矩阵 + 当前定位 |
| "值不值得买" | 买入区间 + 陷阱检测 |
| "什么价格可以买" | 动态买入区间详情 |
| "风险多大" | 悲观情景 + 陷阱检测 |
| "估值可信吗" | 模型分歧度 + 置信度 |

## 输出文件说明

| 文件 | 位置 | 说明 |
|------|------|------|
| `{code}_valuation_data.json` | `analysis/` | 原始采集数据 |
| `{code}_preprocessed.json` | `analysis/` | 预处理参数 |
| `{code}_model_results.json` | `analysis/` | 8模型计算结果 |
| `{code}_integrated.json` | `analysis/` | 整合分析结果 |
| `{code}_估值报告_{YYYYMMDD}.md` | `reports/` | 完整 Markdown 报告 |

## 错误处理

- 缓存数据不存在：提示需先运行 a-stock-realtime 和 data_fetcher
- 模型不可用：缺少数据的模型自动跳过，权重重新分配至可用模型
- matplotlib未安装：自动降级为ASCII文本可视化

## 依赖要求

- **必须**：Python 3.6+（零第三方依赖）
- **可选**：matplotlib（用于生成图表，无则使用ASCII回退）

## 方法论参考

详细估值方法论见 `references/valuation_methodology.md`。

## 调用模式

### Mode A: 独立运行
用户直接调用。Skill 先检查 cache/ 是否有数据，如无则触发 a-stock-realtime 获取，然后执行估值。

### Mode B: 协同运行（工作流调用）
工作流调用，数据已在 cache/ 中，直接读取计算。

**输入契约:** `specs/contracts/financial-statements.json`
**中间契约:** `specs/contracts/valuation-input.json`
**输出:** `data/{code}/reports/{code}_估值报告_{YYYYMMDD}.md`

**质量门（每步）：**
- 数据收集后: validate financial-statements.json，critical fields 完整
- 预处理后: available_models count >= 3, wacc 非零
- 模型计算后: 至少 3 个模型 applicable
- 整合后: 至少一个模型 weight > 0
- 报告后: 报告文件 > 2000 字符

```bash
python scripts/validate_contract.py \
  --schema specs/contracts/valuation-input.json \
  --data data/{code}/analysis/{code}_preprocessed.json
```
