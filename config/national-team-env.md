# national-team-tracker — 用户环境配置

> **Skill 执行前必须读取本文件**, 用于决定数据源策略和图表降级。
> 修改后无需重启, 下次执行时自动生效。

## Python 环境

```yaml
python_path: /rvhome/hao.gao/tools/miniconda3/envs/ipex/bin/python
# 备选: /usr/bin/python3 (系统自带, 无 matplotlib)

# 是否使用 ipex 环境 (含 matplotlib, 可生成 PNG 图表)
use_ipex_env: true

# 激活方式 (use_ipex_env: true 时)
ipex_activate_command: "source ~/tools/miniconda3/etc/profile.d/conda.sh && conda activate ipex"
```

## 第三方依赖可用性

```yaml
matplotlib_available: true   # ipex 环境已装, 用于生成 PNG 图表
pandas_available: false      # 未装, 但本 skill 不依赖 pandas
akshare_available: false     # 未装, 不影响主路径 (akshare 实际只是封装东财, 已弃用)
tushare_token: ""            # 无积分, 不可用
```

## 网络配置

```yaml
# 如果在企业内网或需走代理, 在此声明
http_proxy: ""
https_proxy: ""

# 重试次数 / 超时 (默认值通常足够)
http_max_retries: 3
http_timeout_seconds: 15
```

## 自定义跟踪 ETF (可选)

在默认 8 只宽基外追加监测的 ETF:

```yaml
extra_etfs:
  - "510310"  # 沪深300ETF易方达
  # - "159919"  # 沪深300ETF嘉实
  # - "159922"  # 中证500ETF嘉实
```

## 主体筛选偏好

```yaml
# all = 全部 17 个主体
# huijin = 仅汇金 (最快, 适合日常监控)
# chengtong / guoxin / waiguanju / shebao = 单独主体
# 多选用逗号分隔: "huijin,chengtong"
holders_filter: all
```

## 数据可得性 (重要, 不要修改)

A 股 ETF 的**日级总份额数据无公开免费接口** — 这是公开数据的根本限制, 不是配置问题:

- 集思录 etf_list 对未登录用户限制 20 行/页
- akshare/tushare 实际调用东财同一个接口, 只返回**净值**不返回**份额**
- 东财 fundf10 jbgk 只暴露季报披露日的总份额 (季度更新)

因此 1/7/30 日的"份额变化"用**成交额异常**作为代理信号, 仅季度数据 (fundf10 gmbd) 是精确的。

## 故障排查

| 症状 | 可能原因 | 解决 |
|---|---|---|
| `No module named 'scripts.http_utils'` | 工作目录不对 | `cd /rvhome/hao.gao/personal/funny_valuation` 后再跑 |
| `f-string expression part cannot include a backslash` | Python 3.10 以下 | 切到 ipex (3.11) |
| Stage 4 没生成 PNG | matplotlib 未装 | 报告自动降级, 仅 markdown 表格 |
| 池内 ETF <6/8 success | push2 限流 | 等几分钟重试 |
| 主体反查全失败 | datacenter 限流或网络问题 | 减少 holders_filter 范围 |

## 修改本配置后的验证

跑一次 probe 模式确认环境就绪:

```bash
cd /rvhome/hao.gao/personal/funny_valuation
source ~/tools/miniconda3/etc/profile.d/conda.sh && conda activate ipex
python3 .claude/skills/workflows/national-team-tracker/scripts/nt_realtime.py \
  --output-dir /tmp/nt-probe --probe 2>&1 | tail -3
# 期望: [probe] 8/8 ETFs OK
```
