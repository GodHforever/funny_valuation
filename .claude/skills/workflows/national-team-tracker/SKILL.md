---
name: national-team-tracker
description: "A股国家队动向追踪工作流。监测中央汇金/证金/诚通/国新等主体在 8 只核心宽基 ETF (510300/510050/510500/510330/512100/588000/159915/510850) 中的持仓变化, 输出 T-1 详细 + 7/30 日成交额代理 + 季度精确净申购 + 5 级信号 + 操作建议。Pipeline 模式, 零依赖主路径(仅 Python 3.6+ 标准库), matplotlib 可选用于生成图表。当用户询问'国家队'、'汇金'、'平准基金'、'宽基 ETF 资金流'、'市场底'、'市场顶'、'国家队动向'、'谁在买/卖 ETF'、'国家队加仓/减仓'、'现在能不能进场'、'平准基金动向'时使用此 skill。"
---

# 国家队动向追踪工作流 (national-team-tracker)

## 触发关键词

- 国家队 / 中央汇金 / 平准基金 / 国家队动向 / 国家队加仓 / 国家队减仓
- 宽基 ETF 资金流 / 现在能不能进场 / 谁在买 ETF / 谁在卖 ETF
- 市场底 / 市场顶 / 平准基金动向

## 架构

Pipeline 模式: 单 Agent 按阶段顺序执行 4 个 Stage, 每阶段产出 JSON, 最后阶段聚合为 Markdown 图文报告。

## 执行前必读

1. **首先读取** `/rvhome/hao.gao/personal/funny_valuation/config/national-team-env.md`, 了解用户的 Python 环境/matplotlib 可用性/网络代理设置。若文件不存在, 提示用户先创建 (模板已生成)。
2. 根据 ENV.md 决定: 用系统 `python3` 还是 `conda activate ipex` 后的 python; 是否能渲染 matplotlib 图表。
3. ENV.md 中标注 `matplotlib_available: true` → 使用 ipex python, 报告含 PNG 图; 否则降级为纯 markdown 表格 + mermaid。

## 输入参数

| 参数 | 必须 | 默认值 | 说明 |
|---|---|---|---|
| `time_window` | 否 | `1,7,30,quarter` | 综合研判时间窗 (报告会全部展示, 此参数仅作信号优先级提示) |
| `extra_etfs` | 否 | -- | 在默认 8 只宽基外追加监测, 逗号分隔 (如 `510310,159919`) |
| `holders_filter` | 否 | `all` | 主体筛选: `all`/`huijin`/`zhengjin`/`chengtong`/`guoxin`/`waiguanju`/`shebao` |

## 数据可得性 (重要)

A 股 ETF 的**日级总份额数据无公开免费接口** (在交易所 PCF 文件中, 不开放; 集思录全市场拉取被限制 20 行/页, akshare 实际是同一接口)。

因此本 workflow 采取的策略:

| 时间窗 | 数据源 | 精确度 |
|---|---|---|
| **盘中实时** | 东方财富 push2 (现价/成交额) | 秒级 |
| **当日 (1天)** | push2 + push2his K线 (Z-Score 异常放量) | 日级精确 |
| **7 / 30 天** | push2his K线累计成交额 + 价格 | **代理信号** (用资金活跃度代替份额) |
| **季度** | fundf10 gmbd (申购/赎回/总份额) | **精确** |
| **机构占比** | fundf10 cyrjg (半年披露) | 精确 |
| **主体反查** | datacenter RPT_F10_EH_HOLDERS | 季度 |

## 执行流程

### Stage 0: 初始化

- 检测 Python 环境 (优先 ipex, 否则系统 python3)
- 创建目录 `data/national-team/reports/{YYYY-MM-DD}/`
- 写 `data/national-team/state.json` (status=running)

```bash
TODAY=$(date +%Y-%m-%d)
OUT=data/national-team/reports/$TODAY
mkdir -p $OUT
```

### Stage 1: 实时盘中 + 60日成交额时序 + 最新已披露份额 (`nt_realtime.py`)

```bash
python3 .claude/skills/workflows/national-team-tracker/scripts/nt_realtime.py \
  --output-dir $OUT \
  [--extra-etfs 510310,159919]
```

**输出**: `$OUT/snapshot.json`

**数据源**:
- L1 push2: 实时现价/成交额/涨跌幅 (零依赖)
- L2 push2his: 60 日 K 线 → 计算 Z-Score、7/30 日累计成交额、价格变动
- L3 fundf10 jbgk HTML: 最新已披露的总份额和净资产 (季度)

**契约**: `specs/contracts/national-team-snapshot.json`

**质量门**: 池内 ≥6/8 只 ETF status=success → pass; 否则 partial → 仍可继续 (Stage 3 会标注降级)

**失败策略**: <6/8 → 写 issues 并继续; HTTP 全失败 → 阻断

### Stage 2: 季度规模 + 持有人结构 + 国家队主体反查 (`nt_seasonal.py`)

```bash
python3 .claude/skills/workflows/national-team-tracker/scripts/nt_seasonal.py \
  --output-dir $OUT \
  --holders-filter all
```

**输出**: `$OUT/seasonal.json`

**数据源**:
- fundf10 gmbd: 池内每只 ETF 的近 8 季度规模时序
- fundf10 cyrjg: 池内每只 ETF 的半年持有人结构
- datacenter RPT_F10_EH_HOLDERS: 17 个国家队主体的最新季报持仓 (`config/holders_dict.json` 定义)

**契约**: `specs/contracts/national-team-seasonal.json`

**质量门**: 池内 ≥6/8 只 ETF + 主体反查 ≥10 个成功

**失败策略**: 个别主体反查失败 → 写 issue 继续; ETF 季度数据全失败 → 阻断 (无法构造信号)

### Stage 3: 信号识别 + 综合建议 (`nt_signals.py`)

```bash
python3 .claude/skills/workflows/national-team-tracker/scripts/nt_signals.py \
  --snapshot-file $OUT/snapshot.json \
  --seasonal-file $OUT/seasonal.json \
  --output-dir $OUT
```

**输出**: `$OUT/signal.json`

**信号体系** (硬编码阈值, 注释中标明出处):

| 等级 | 触发条件 | 操作建议 |
|---|---|---|
| 🔴 极强买入 | ≥3 只宽基同日 Z≥2 + 涨跌幅 ∈ [-0.5%, +0.5%] | 跟仓宽基 ETF, 定投加速 |
| 🟠 强买入 | 30/60均比 ≥1.3 + 30日累涨 ≤0% **或** 季度净申购 ≥30 亿份 | 现有持仓维持, 可小幅加仓 |
| 🟡 中性 | 默认 | 按既定策略 |
| 🟣 顶部预警 | 单日 Z≥2 + 涨跌幅 < -0.5% + 30日累涨 ≥+5% | 已重仓者考虑分批减持 |
| ⚫ 极强减持 | ≥3 只 Z≥3 + 涨跌幅 < -0.5% + 30日累涨 ≥+10% | 重仓减半止盈, 切换防御 |
| 🔵 产业信号 | B 类基金 (大基金一/二/三期等) 季报新增加仓 | 题材池更新, 长线主题配置 |

**契约**: `specs/contracts/national-team-signal.json`

**质量门**: signal.json 至少含 recommendation.headline

### Stage 4: 图文报告 (`nt_report.py`)

```bash
python3 .claude/skills/workflows/national-team-tracker/scripts/nt_report.py \
  --snapshot-file $OUT/snapshot.json \
  --seasonal-file $OUT/seasonal.json \
  --signal-file $OUT/signal.json \
  --output-dir $OUT
```

**输出**:
- `$OUT/national-team-report.md` (主报告, ≥4000 字符)
- `$OUT/charts/*.png` (matplotlib 可用时, 含季度净申购堆叠图、机构占比折线图、Z-Score 柱状图)

**降级**: matplotlib 不可用时, 自动跳过 PNG, 报告里只用 markdown 表格 + mermaid。

**质量门**: 报告 ≥4000 字符

### Stage 5: 呈现给用户

读取 `$OUT/national-team-report.md` 完整呈现给用户; 重点提取:
- 综合判断 (overall_level + headline)
- 操作建议 (actions)
- 风险提示 (risks)
- 触发等级 ≥ orange 的 ETF 详情

## 状态机

```
init → stage1_running → stage1_passed → stage2_running → stage2_passed/partial
  → stage3_running → stage3_passed → stage4_running → completed
任一 stageN_failed → overall_status=failed, 写 issues 给修复建议
```

## 双模式

- **Mode A (独立)**: 默认, 用户通过触发关键词激活, 完整执行 4 个 Stage
- **Mode B (协同)**: 由其他工作流调用 (如未来的"市场环境分析"工作流), 传 `--output-dir` 写到调用方目录

## 错误处理

1. **HTTP 重试**: 复用 `scripts/http_utils.py` 的 `http_get_json_with_retry` (3 次指数退避)
2. **多源回退**: push2 主源, 失败已在 stage 报错 (无回退源, 因 push2 是最稳的)
3. **严格阻断**: Stage 1/3/4 任一失败 → state.json 标记 failed + 退出码非零
4. **降级 partial**: Stage 2 季报数据缺失部分主体 → 标记 partial 继续
5. **环境检测**: 没有 ipex python / 没装 matplotlib → Stage 4 降级为纯 markdown

## 复用现有模块

- `scripts/http_utils.py`: HTTP GET + 指数退避 (零依赖)
- `scripts/validate_contract.py`: JSON Schema 校验

## 配置文件

- `config/etf_pool.json`: 国家队跟踪 ETF 池 (8 只核心宽基 + 7 只候选)
- `config/holders_dict.json`: 17 个国家队主体规范名 + 类别 (A_平准 / B_产业)
- `config/industry_funds_pool.json`: B 类产业基金被投上市公司池 (题材标的)
- `/rvhome/hao.gao/personal/funny_valuation/config/national-team-env.md`: 用户环境配置

## 数据契约

- `specs/contracts/national-team-state.json` — 工作流状态
- `specs/contracts/national-team-snapshot.json` — Stage 1 输出
- `specs/contracts/national-team-seasonal.json` — Stage 2 输出
- `specs/contracts/national-team-signal.json` — Stage 3 输出

## 字段命名

遵循 CLAUDE.md 规范字段 (`current_price`/`pe_ttm`/`pb_mrq` 等)。新增字段:
- `etf_code`/`etf_name`/`tracking_index`
- `total_share_yi`/`net_assets_yi` (亿份/亿元)
- `turnover_zscore`/`turnover_60d_avg_yi`
- `signal_level`/`institutional_pct_latest`

## 触发示例对话

> 用户: "国家队最近在干什么?"
>
> Skill 触发 → 跑完 4 个 Stage → 呈现:
> "🟡 中性 — 国家队动向中性, 维持既定策略。本季度有 5 个国家队主体季报显示加仓 (诚通系/国新系/外管局系)。588000 科创50 触发橙色信号 (季度净申购 +55.4 亿份)..."
