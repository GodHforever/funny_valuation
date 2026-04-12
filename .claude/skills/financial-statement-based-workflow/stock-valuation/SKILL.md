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
2. 输出目录（默认当前目录）
3. 是否有财报定性分析文档（可选，决定模式A/B）
   - 若有，需包含：业务确定性评级、增长可持续性评级、主要风险、研判置信度

### Phase 1: 数据获取（使用 Agent 并行）

使用 Agent 工具并行获取数据，提高效率：

**Agent 1 — 估值数据采集：**
```bash
python {skill_path}/scripts/valuation_data.py --code {code} --output-dir {output_dir}
```

**Agent 2（并行）— 宏观数据交叉验证：**
```bash
python ~/.claude/skills/china-macro-lite/scripts/macro_lite.py --category 利率 --output-dir {output_dir}
```

**Agent 3（并行，可选）— 定性分析文档解析：**
若用户提供了定性分析文档路径，读取并确认其包含所需评级信息。

### Phase 2: 预处理

```bash
python {skill_path}/scripts/valuation_preprocessor.py \
    --data-file {output_dir}/{code}_valuation_data.json \
    --output-dir {output_dir} \
    [--qualitative-doc {qualitative_doc_path}]
```

### Phase 3: 模型计算

```bash
python {skill_path}/scripts/valuation_models.py \
    --preprocessed-file {output_dir}/{code}_preprocessed.json \
    --output-dir {output_dir}
```

### Phase 4: 整合分析

```bash
python {skill_path}/scripts/valuation_integrator.py \
    --data-file {output_dir}/{code}_valuation_data.json \
    --preprocessed-file {output_dir}/{code}_preprocessed.json \
    --model-results-file {output_dir}/{code}_model_results.json \
    --output-dir {output_dir}
```

### Phase 5: 报告生成

```bash
python {skill_path}/scripts/valuation_report.py \
    --integrated-file {output_dir}/{code}_integrated.json \
    --data-file {output_dir}/{code}_valuation_data.json \
    --preprocessed-file {output_dir}/{code}_preprocessed.json \
    --model-results-file {output_dir}/{code}_model_results.json \
    --output-dir {output_dir} [--visualize]
```

### Phase 6: 结果呈现

读取生成的估值分析报告 Markdown 文件，向用户呈现关键结论：
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

```bash
python {skill_path}/scripts/valuation_report.py \
    --format focused --question "{用户问题}" \
    --integrated-file {output_dir}/{code}_integrated.json \
    --data-file {output_dir}/{code}_valuation_data.json \
    --preprocessed-file {output_dir}/{code}_preprocessed.json \
    --model-results-file {output_dir}/{code}_model_results.json \
    --output-dir {output_dir}
```

问题路由：
| 用户问题 | 调用模块 |
|---------|---------|
| "便宜吗/贵不贵" | 估值矩阵 + 当前定位 |
| "值不值得买" | 买入区间 + 陷阱检测 |
| "什么价格可以买" | 动态买入区间详情 |
| "风险多大" | 悲观情景 + 陷阱检测 |
| "估值可信吗" | 模型分歧度 + 置信度 |

## 输出文件说明

| 文件 | 说明 |
|------|------|
| `{code}_valuation_data.json` | 原始采集数据 |
| `{code}_preprocessed.json` | 预处理参数 |
| `{code}_model_results.json` | 8模型计算结果 |
| `{code}_integrated.json` | 整合分析结果 |
| `{code}_{name}_估值分析报告.md` | 完整 Markdown 报告 |
| `{code}_valuation_range.png` | 估值区间图（可选） |
| `{code}_sensitivity.png` | DCF敏感性热力图（可选） |

## 错误处理

- 数据获取失败：每个API独立try/except，主API失败自动回退备用API
- 模型不可用：缺少数据的模型自动跳过，权重重新分配至可用模型
- 实时行情完全失败：提示用户检查股票代码是否正确
- matplotlib未安装：自动降级为ASCII文本可视化

## 依赖要求

- **必须**：Python 3.6+（零第三方依赖）
- **可选**：matplotlib（用于生成图表，无则使用ASCII回退）

## 方法论参考

详细估值方法论见 `references/valuation_methodology.md`。
