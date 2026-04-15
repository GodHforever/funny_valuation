---
name: a-stock-realtime
description: "A股实时行情与新闻轻量级分析工具。零依赖（Python 3.6+标准库），通过东方财富、腾讯、新浪等公开API获取个股实时行情（当前价/涨跌幅/成交量/盘口五档/PE/PB/市值）、新闻资讯（个股新闻/快讯/公告）、资金流向（主力资金/北向资金）、分时走势。三级数据源回退确保稳定性。适用于：A股个股实时行情查看、盘中快讯追踪、资金面监控、异动分析。当用户查询A股个股实时行情、最新新闻、资金流向、盘口数据时使用此skill。"
---

# A Stock Realtime — A股实时行情与新闻轻量级工具

## 功能概述

**零外部依赖**，仅用 Python 标准库（`urllib`、`json`），直接请求公开 API 获取 A 股个股实时数据。

### 核心能力

| 功能 | 数据项 | 实时性 |
|------|--------|--------|
| **实时行情** | 当前价、涨跌幅、成交量/额、换手率、今开/最高/最低/昨收 | 秒级 |
| **估值指标** | PE(TTM)、PB(MRQ)、PS(TTM)、总市值、流通市值 | 秒级 |
| **盘口五档** | 买1~买5价量、卖1~卖5价量 | 秒级 |
| **新闻资讯** | 个股新闻、财经快讯、公司公告 | 分钟级 |
| **资金流向** | 主力/超大单/大单/中单/小单净流入、北向资金 | 分钟级 |
| **分时走势** | 1分钟K线（当日） | 分钟级 |

### 数据源与回退机制

#### 行情（3级回退）

| 优先级 | 数据源 | 特点 | 编码 |
|--------|--------|------|------|
| **L1** | 东方财富 push2 | 字段最全（含PE/PB/PS/行业），JSON格式 | UTF-8 |
| **L2** | 腾讯财经 qt.gtimg.cn | 稳定性好，含盘口5档 | GBK |
| **L3** | 新浪财经 hq.sinajs.cn | 最终兜底 | GBK |

#### 新闻（3级回退）

| 优先级 | 数据源 | 特点 |
|--------|--------|------|
| **L1** | 东方财富搜索API | 按股票代码/名称搜索新闻 |
| **L2** | 东方财富7×24快讯 | 滚动快讯流，按个股过滤 |
| **L3** | 东方财富公告API | 公司公告（最可靠） |

#### 资金流向（2级）

| 优先级 | 数据源 | 特点 |
|--------|--------|------|
| **L1** | 东方财富push2资金流 | 个股主力/散户净流入 |
| **L2** | 东方财富北向资金 | 沪深港通净流入 |

## 使用方法

### 前置条件

**仅需 Python 3.6+**，无需安装任何第三方库。

### 运行脚本

```bash
# 基础行情
python {skill_path}/scripts/realtime_fetcher.py --code 600519

# 行情 + 新闻
python {skill_path}/scripts/realtime_fetcher.py --code 600519 --news

# 仅新闻
python {skill_path}/scripts/realtime_fetcher.py --code 600519 --news-only --count 15

# 完整快报（行情 + 新闻 + 资金流 + 分时）
python {skill_path}/scripts/realtime_fetcher.py --code 600519 --full

# JSON 格式输出
python {skill_path}/scripts/realtime_fetcher.py --code 600519 --full --format json

# 保存到文件
python {skill_path}/scripts/realtime_fetcher.py --code 600519 --full --output-dir ./output
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--code` | 股票代码（6位数字） | 必填 |
| `--news` | 同时获取新闻 | 否 |
| `--news-only` | 仅获取新闻 | 否 |
| `--count` | 新闻条数 | 10 |
| `--fund-flow` | 同时获取资金流向 | 否 |
| `--intraday` | 同时获取分时数据 | 否 |
| `--full` | 全部数据（行情+新闻+资金流+分时） | 否 |
| `--format` | 输出格式（text/json） | text |
| `--output-dir` | 输出目录 | 控制台输出 |
| `--mode` | 运行模式（standalone/collaborative） | standalone |

### 输出文件

运行后在输出目录生成：
- `{code}_realtime_{timestamp}.json` — 结构化 JSON 数据
- `{code}_realtime_{timestamp}.md` — 可读的 Markdown 快报

### JSON 输出格式

```json
{
  "meta": {
    "code": "600519",
    "name": "贵州茅台",
    "timestamp": "2026-04-15T14:30:00",
    "data_version": "1.0"
  },
  "quote": {
    "code": "600519",
    "name": "贵州茅台",
    "current_price": 1680.00,
    "change_pct": 1.25,
    "volume": 25000,
    "turnover": 42.5,
    "pe_ttm": 28.50,
    "pb_mrq": 8.20,
    "total_market_cap": 21100.00,
    "bid_ask": {
      "bids": [{"price": 1679.5, "volume": 100}],
      "asks": [{"price": 1680.5, "volume": 50}]
    },
    "data_source": "eastmoney_push2",
    "status": "success"
  },
  "news": {
    "news": [
      {
        "title": "贵州茅台一季度净利增长15%",
        "time": "2026-04-15 10:00:00",
        "source": "东方财富",
        "summary": "...",
        "url": "...",
        "type": "news"
      }
    ],
    "total": 10,
    "status": "success"
  },
  "fund_flow": {
    "stock_flow": {
      "main_net_inflow": 2.35,
      "main_net_pct": 15.2,
      "status": "success"
    },
    "northbound": {
      "sh_connect": 10.5,
      "sz_connect": 8.2,
      "total": 18.7,
      "status": "success"
    }
  }
}
```

### 字段规范

遵循项目 CLAUDE.md 规范：

| 字段名 | 单位 | 说明 |
|--------|------|------|
| `current_price` | 元 | 当前股价 |
| `total_market_cap` | 亿元 | 总市值 |
| `circulating_market_cap` | 亿元 | 流通市值 |
| `pe_ttm` | 倍 | 市盈率TTM |
| `pb_mrq` | 倍 | 市净率MRQ |
| `ps_ttm` | 倍 | 市销率TTM |
| `turnover` | 亿元 | 成交额 |
| `main_net_inflow` | 亿元 | 主力净流入 |

## 错误处理

1. **多级回退**：每个数据模块独立获取，单一数据源失败自动尝试下一级
2. **指数退避重试**：每次 HTTP 请求自带 3 次重试（1s/2s/4s 间隔）
3. **独立失败**：行情、新闻、资金流模块彼此独立，一个失败不影响其他
4. **状态追踪**：每个模块返回 `status` 字段（success/partial/failed/no_data）
5. **编码兼容**：自动处理 UTF-8/GBK 编码差异

## 调用模式

### Mode A: 独立运行

用户直接调用，Skill 获取数据并输出给用户。

```bash
# 用户查看茅台实时行情
python {skill_path}/scripts/realtime_fetcher.py --code 600519

# 用户查看宁德时代完整快报
python {skill_path}/scripts/realtime_fetcher.py --code 300750 --full
```

### Mode B: 协同运行（工作流调用）

工作流调用，输出到指定目录，供下游阶段使用。

```bash
python {skill_path}/scripts/realtime_fetcher.py \
  --code 600519 --full --format json \
  --mode collaborative --output-dir data/600519/market-data/
```

**输出：**
- `data/{stock_code}/market-data/{code}_realtime_{timestamp}.json`

## 与其他 Skill 的区别

| 维度 | a-stock-realtime（本 skill） | earnings-insight | stock-valuation |
|------|---------------------------|-----------------|-----------------|
| **定位** | 实时快报 | 深度研判 | 估值分析 |
| **速度** | 秒级 | 分钟级 | 分钟级 |
| **数据范围** | 实时行情+新闻+资金流 | 5年财报+MD&A | 5年财报+8模型 |
| **分析深度** | 数据展示为主 | 七层递进分析 | 多模型交叉验证 |
| **依赖** | 零依赖 | 零依赖 | 零依赖 |
| **使用场景** | 盘中快速查看 | 财报季深度研究 | 买入价格判断 |
