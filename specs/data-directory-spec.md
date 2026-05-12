# 数据目录结构规范

> 版本: v1.0 | 日期: 2026-05-12

## 个股分析目录

```
data/{stock_code}/
├── state.json              # 研究状态追踪（参照 specs/contracts/workflow-state.json）
├── journal.jsonl            # 研究过程日志（参照 specs/contracts/research-journal.json）
├── cache/                   # API 缓存（统一缓存层）
│   ├── stock_info.json      # 实时行情快照
│   ├── macro_data.json      # 宏观数据
│   ├── industry_list.json   # 行业公司列表
│   └── {api}_{timestamp}.json  # 其他 API 缓存
├── raw/                     # 原始资料
│   ├── earnings-pdf/        # 财报 PDF
│   └── earnings-extracted/  # 提取的章节 Markdown
├── analysis/                # 分析中间产物
│   ├── {code}_insight_data.json      # 财务数据（data_fetcher 输出）
│   ├── {code}_pe_band.json           # 历史估值分位数
│   ├── {code}_industry_compare.json  # 行业对比
│   ├── {code}_macro_impact.md        # 宏观影响评估
│   ├── {code}_risk_scan.json         # 风险扫描
│   ├── {code}_preprocessed.json      # 估值预处理
│   ├── {code}_model_results.json     # 估值模型结果
│   └── {code}_integrated.json        # 估值整合
└── reports/                 # 最终报告
    ├── {code}_深度研判报告_{YYYYMMDD}.md
    ├── {code}_估值报告_{YYYYMMDD}.md
    ├── {code}_风险扫描_{YYYYMMDD}.md
    ├── {code}_行业对比_{YYYYMMDD}.md
    └── execution_audit_{YYYYMMDD}.md
```

## 非个股分析目录

```
data/national-team/          # 国家队动向追踪
  state.json
  reports/{YYYY-MM-DD}/

data/industry-{name}/        # 行业研究（新增）
  state.json
  analysis/
  reports/
```

判定原则：分析对象不是单只股票而是全市场或某主体时，使用功能命名目录。

## 缓存 TTL 策略

| 数据类型 | 缓存位置 | TTL | 说明 |
|---------|----------|-----|------|
| 实时行情 | `cache/stock_info.json` | 当日有效 | 盘中数据隔日失效 |
| 财务报表数据 | `cache/financial_*.json` | 季度有效 | 季报发布后失效 |
| 宏观数据 | `cache/macro_data.json` | 7 天 | 月度数据 |
| 行业列表 | `cache/industry_list.json` | 30 天 | 行业分类变化缓慢 |
| 财报 PDF | `raw/earnings-pdf/` | 永久 | 历史文件不变 |

缓存检查逻辑由各 Skill 脚本自行实现（参照 data_fetcher.py 的 collaborative 模式）。

## 新旧目录映射

| 旧目录 | 新目录 | 说明 |
|--------|--------|------|
| `market-data/` | `cache/` | 统一缓存层，旧数据保留不迁移 |
| `earnings-pdf/` | `raw/earnings-pdf/` | 移入 raw/ 子目录 |
| `earnings-extracted/` | `raw/earnings-extracted/` | 移入 raw/ 子目录 |
| `earnings-analysis/` | `analysis/` | 统一分析产物目录 |
| `valuation-reports/` | `reports/` | 统一报告目录 |

> **兼容策略**：新任务统一使用新目录结构。存量数据保留原位，不强制迁移。Skill 脚本在读取时应优先检查新目录，回退检查旧目录。
