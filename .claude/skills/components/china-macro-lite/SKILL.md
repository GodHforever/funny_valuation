---
name: china-macro-lite
description: "零依赖的中国宏观经济数据追踪工具（仅用 Python 标准库）。直接通过 HTTP 请求东方财富、新浪财经等公开 API，获取 CPI、PPI、M1/M2、LPR、国债收益率、融资余额、北向资金、WTI原油、COMEX黄金等核心宏观指标，自动计算变化趋势并输出结构化报告。无需安装任何第三方库，Python 3.6+ 即可运行。适用于：宏观经济分析、A股市场环境判断、利率走势跟踪、资金面监控、大宗商品价格追踪。当用户询问中国宏观经济数据、CPI/PPI、货币供应量、利率、北向资金、融资余额等问题时使用此 skill。"
---

# China Macro Lite — 零依赖中国宏观经济数据追踪

## 功能概述

**零外部依赖**，仅用 Python 标准库（`urllib`、`json`），直接请求公开 API 获取中国核心宏观经济指标。

### 覆盖指标（12 个）

| 类别 | 指标 | 数据源 | 频率 |
|------|------|--------|------|
| **价格** | CPI 同比、PPI 同比 | 东方财富 | 月度 |
| **货币** | M2 同比、M1 同比 | 东方财富 | 月度 |
| **利率** | LPR-1Y、LPR-5Y | 东方财富 | 月度 |
| **利率** | 10Y 国债收益率、1Y 国债收益率 | 东方财富 | 日度 |
| **市场** | 北向资金净流入 | 东方财富 | 日度 |
| **商品** | WTI 原油、COMEX 黄金 | 新浪财经 | 实时 |
| **储备** | 外汇储备 | 东方财富 | 月度 |

### 数据源

所有数据源均为**公开免费 API**，无需任何 token 或注册：
- **东方财富** `datacenter-web.eastmoney.com` — 宏观经济数据
- **东方财富** `push2his.eastmoney.com` — 北向资金
- **新浪财经** `hq.sinajs.cn` — 国际商品期货

## 使用方法

### 前置条件

**仅需 Python 3.6+**，无需安装任何第三方库。

### 运行脚本

```bash
# 拉取全部指标
python {skill_path}/scripts/macro_lite.py

# 拉取指定类别
python {skill_path}/scripts/macro_lite.py --category 价格
python {skill_path}/scripts/macro_lite.py --category 利率
python {skill_path}/scripts/macro_lite.py --category 商品

# 指定输出目录
python {skill_path}/scripts/macro_lite.py --output-dir ./macro_data

# 仅输出 JSON（不生成 Markdown）
python {skill_path}/scripts/macro_lite.py --format json
```

### 可用类别

`价格`、`货币`、`利率`、`市场`、`商品`、`储备`

### 输出文件

运行后在输出目录生成：
- `macro_YYYYMMDD_HHMMSS.json` — 结构化 JSON 数据
- `macro_YYYYMMDD_HHMMSS.md` — 可读的 Markdown 报告

### JSON 输出格式

```json
{
  "pull_time": "2026-04-10 18:30:00",
  "indicators": [
    {
      "name": "CPI",
      "category": "价格",
      "value": 1.0,
      "prev_value": 1.3,
      "change": -0.3,
      "unit": "%",
      "data_date": "2026-03",
      "source": "eastmoney",
      "status": "success"
    }
  ],
  "summary": {
    "total": 12,
    "success": 11,
    "failed": 1
  }
}
```

## 错误处理

1. **网络错误**：每个指标独立请求，单个超时（10s）不影响其他
2. **API 变更**：检测到非预期响应时记录详细错误信息
3. **前值比较**：自动读取上次输出文件，首次运行无前值
4. **退出码**：全部成功返回 0，部分失败返回 1

## 与 china-macro-tracker 的区别

| 维度 | china-macro-lite（本 skill） | china-macro-tracker |
|------|---------------------------|-------------------|
| **依赖** | 零依赖（纯标准库） | 需要 akshare（Python 3.9+） |
| **安装** | 无需安装，开箱即用 | 需要 pip install akshare |
| **Python 版本** | 3.6+ | 3.9+ |
| **数据源** | 东方财富/新浪公开 API | AkShare 封装 |
| **启动速度** | 秒级 | akshare 导入可能需要 60s+ |

## 调用模式

### Mode A: 独立运行
用户直接调用 `/china-macro-lite`。Skill 获取宏观数据并直接输出给用户。

### Mode B: 协同运行（工作流调用）
工作流调用，输出到指定目录。

```bash
python .claude/skills/components/china-macro-lite/scripts/macro_lite.py \
  --category 价格,货币,利率 --mode collaborative \
  --output-dir data/{stock_code}/market-data/
```

**输出：**
- `data/{stock_code}/market-data/macro_data.json`

**输出契约:** `specs/contracts/market-data.json`

**质量门：**
```bash
python scripts/validate_contract.py \
  --schema specs/contracts/market-data.json \
  --data data/{stock_code}/market-data/macro_data.json
```
