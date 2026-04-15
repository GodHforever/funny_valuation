#!/usr/bin/env python3
"""零依赖中国宏观经济数据追踪脚本。

仅使用 Python 标准库（urllib, json），直接请求东方财富、新浪财经等公开 API，
获取 12 个核心宏观指标，自动与前值比较，输出 JSON + Markdown 报告。

用法:
    python macro_lite.py                          # 拉取全部指标
    python macro_lite.py --category 价格          # 拉取指定类别
    python macro_lite.py --output-dir ./data      # 指定输出目录
    python macro_lite.py --format json            # 仅输出 JSON
"""

import argparse
import glob
import json
import os
import re
import ssl
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 每个指标的请求超时（秒）
FETCH_TIMEOUT = 10

# User-Agent 避免被拒
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================
# HTTP 工具函数
# ============================================================

def _create_ssl_context():
    """创建不验证证书的 SSL 上下文（部分环境缺少根证书）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get_json(url, headers=None, timeout=FETCH_TIMEOUT):
    """发送 GET 请求并解析 JSON 响应。

    Args:
        url: 请求 URL
        headers: 额外的请求头
        timeout: 超时秒数

    Returns:
        解析后的 JSON 对象

    Raises:
        RuntimeError: 请求失败或响应非 JSON
    """
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    request = Request(url, headers=req_headers)
    ssl_ctx = _create_ssl_context()

    try:
        with urlopen(request, timeout=timeout, context=ssl_ctx) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset)
            return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"响应非 JSON 格式: {exc}") from exc
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络错误: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"请求失败: {type(exc).__name__}: {exc}") from exc


def http_get_text(url, headers=None, timeout=FETCH_TIMEOUT):
    """发送 GET 请求并返回文本响应。"""
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    request = Request(url, headers=req_headers)
    ssl_ctx = _create_ssl_context()

    try:
        with urlopen(request, timeout=timeout, context=ssl_ctx) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络错误: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"请求失败: {type(exc).__name__}: {exc}") from exc


# ============================================================
# 东方财富 API 封装
# ============================================================

EASTMONEY_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def fetch_eastmoney(report_name, sort_column, columns="ALL", page_size=3,
                    extra_params=None):
    """请求东方财富数据中心 API。

    Args:
        report_name: 报表名称
        sort_column: 排序列名
        columns: 返回列（默认 ALL）
        page_size: 返回行数
        extra_params: 额外的查询参数字典

    Returns:
        数据行列表 (list[dict])

    Raises:
        RuntimeError: API 返回错误
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "pageSize": str(page_size),
        "sortColumns": sort_column,
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
    }
    if extra_params:
        params.update(extra_params)

    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{EASTMONEY_BASE}?{query_string}"

    data = http_get_json(url)

    if not data.get("success"):
        error_msg = data.get("message", "未知错误")
        raise RuntimeError(f"东方财富 API 错误: {error_msg} (报表: {report_name})")

    result = data.get("result")
    if not result or not result.get("data"):
        raise RuntimeError(f"东方财富返回空数据 (报表: {report_name})")

    return result["data"]


# ============================================================
# 各指标的拉取函数
# ============================================================

def fetch_cpi():
    """CPI 同比（月度）。"""
    rows = fetch_eastmoney("RPT_ECONOMY_CPI", "REPORT_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    date_str = latest["REPORT_DATE"][:10]
    value = latest["NATIONAL_SAME"]

    return {
        "value": value,
        "prev_value": prev["NATIONAL_SAME"] if prev else None,
        "data_date": date_str[:7],
        "source": "eastmoney",
    }


def fetch_ppi():
    """PPI 同比（月度）。"""
    rows = fetch_eastmoney("RPT_ECONOMY_PPI", "REPORT_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest["BASE_SAME"],
        "prev_value": prev["BASE_SAME"] if prev else None,
        "data_date": latest["REPORT_DATE"][:7],
        "source": "eastmoney",
    }


def fetch_m2():
    """M2 同比增速（月度）。"""
    rows = fetch_eastmoney("RPT_ECONOMY_CURRENCY_SUPPLY", "REPORT_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest["BASIC_CURRENCY_SAME"],
        "prev_value": prev["BASIC_CURRENCY_SAME"] if prev else None,
        "data_date": latest["REPORT_DATE"][:7],
        "source": "eastmoney",
    }


def fetch_m1():
    """M1 同比增速（月度）。"""
    rows = fetch_eastmoney("RPT_ECONOMY_CURRENCY_SUPPLY", "REPORT_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest["CURRENCY_SAME"],
        "prev_value": prev["CURRENCY_SAME"] if prev else None,
        "data_date": latest["REPORT_DATE"][:7],
        "source": "eastmoney",
    }


def fetch_lpr_1y():
    """LPR 1年期（月度）。"""
    rows = fetch_eastmoney("RPTA_WEB_RATE", "TRADE_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest["LPR1Y"],
        "prev_value": prev["LPR1Y"] if prev else None,
        "data_date": latest["TRADE_DATE"][:10],
        "source": "eastmoney",
    }


def fetch_lpr_5y():
    """LPR 5年期以上（月度）。"""
    rows = fetch_eastmoney("RPTA_WEB_RATE", "TRADE_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest["LPR5Y"],
        "prev_value": prev["LPR5Y"] if prev else None,
        "data_date": latest["TRADE_DATE"][:10],
        "source": "eastmoney",
    }


def fetch_treasury_10y():
    """中国 10 年期国债收益率（日度）。"""
    rows = fetch_eastmoney("RPTA_WEB_TREASURYYIELD", "SOLAR_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest.get("EMM00166469"),
        "prev_value": prev.get("EMM00166469") if prev else None,
        "data_date": latest["SOLAR_DATE"][:10],
        "source": "eastmoney",
    }


def fetch_treasury_1y():
    """中国 1 年期国债收益率（日度）。"""
    rows = fetch_eastmoney("RPTA_WEB_TREASURYYIELD", "SOLAR_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest.get("EMM00588704"),
        "prev_value": prev.get("EMM00588704") if prev else None,
        "data_date": latest["SOLAR_DATE"][:10],
        "source": "eastmoney",
    }


def fetch_northbound():
    """北向资金净流入（日度）。

    使用 push2his.eastmoney.com 的陆股通历史数据接口。
    字段: f51=日期, f52=沪股通净流入, f53=深股通净流入, f54=北向合计, f55=沪股通当日余额, f56=深股通当日余额
    """
    url = (
        "https://push2his.eastmoney.com/api/qt/kamt.kline/get?"
        "fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
        "&klt=101&lmt=3"
        "&ut=b2884a393a59ad64002292a3e90d46a5"
    )
    data = http_get_json(url)

    if data.get("rc") != 0:
        raise RuntimeError(f"北向资金 API 错误: rc={data.get('rc')}")

    # 合并沪股通和深股通数据
    hk2sh = data.get("data", {}).get("hk2sh", [])
    hk2sz = data.get("data", {}).get("hk2sz", [])

    if not hk2sh or not hk2sz:
        raise RuntimeError("北向资金数据为空")

    # 解析最新一天的数据: "日期,净流入,额度,累计"
    def parse_kamt_line(line):
        parts = line.split(",")
        return {"date": parts[0], "net_inflow": float(parts[1])}

    latest_sh = parse_kamt_line(hk2sh[-1])
    latest_sz = parse_kamt_line(hk2sz[-1])
    total_inflow = latest_sh["net_inflow"] + latest_sz["net_inflow"]

    prev_total = None
    if len(hk2sh) > 1 and len(hk2sz) > 1:
        prev_sh = parse_kamt_line(hk2sh[-2])
        prev_sz = parse_kamt_line(hk2sz[-2])
        prev_total = prev_sh["net_inflow"] + prev_sz["net_inflow"]

    # 单位转换: 万元 → 亿元
    value_yi = round(total_inflow / 10000, 2)
    prev_yi = round(prev_total / 10000, 2) if prev_total is not None else None

    return {
        "value": value_yi,
        "prev_value": prev_yi,
        "data_date": latest_sh["date"],
        "source": "eastmoney",
    }


def fetch_forex_reserves():
    """外汇储备（月度）。"""
    rows = fetch_eastmoney("RPT_ECONOMY_GOLD_CURRENCY", "REPORT_DATE")
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None

    return {
        "value": latest.get("FOREX"),
        "prev_value": prev.get("FOREX") if prev else None,
        "data_date": latest["REPORT_DATE"][:7],
        "source": "eastmoney",
    }


def fetch_sina_commodity(symbol, name):
    """从新浪财经获取国际商品期货价格。

    Args:
        symbol: 新浪代码（如 hf_CL, hf_GC）
        name: 商品名称（用于错误提示）
    """
    url = f"https://hq.sinajs.cn/list={symbol}"
    headers = {"Referer": "https://finance.sina.com.cn"}
    text = http_get_text(url, headers=headers)

    # 格式: var hq_str_hf_CL="98.422,,98.580,...";
    match = re.search(r'"([^"]*)"', text)
    if not match:
        raise RuntimeError(f"新浪财经 {name} 数据解析失败: 无法匹配引号内容")

    fields = match.group(1).split(",")
    if not fields or not fields[0]:
        raise RuntimeError(f"新浪财经 {name} 数据为空")

    try:
        price = float(fields[0])
    except ValueError:
        raise RuntimeError(f"新浪财经 {name} 价格解析失败: '{fields[0]}'")

    # 尝试获取昨收价（第3个字段）
    prev_price = None
    if len(fields) > 2 and fields[2]:
        try:
            prev_price = float(fields[2])
        except ValueError:
            pass

    # 日期在倒数第2个字段
    data_date = ""
    if len(fields) >= 2:
        for field in reversed(fields):
            if re.match(r"\d{4}-\d{2}-\d{2}", field):
                data_date = field
                break

    return {
        "value": price,
        "prev_value": prev_price,
        "data_date": data_date,
        "source": "sina",
    }


def fetch_wti():
    """WTI 原油期货价格。"""
    return fetch_sina_commodity("hf_CL", "WTI原油")


def fetch_gold():
    """COMEX 黄金期货价格。"""
    return fetch_sina_commodity("hf_GC", "COMEX黄金")


# ============================================================
# 指标注册表
# ============================================================

INDICATORS = [
    {"name": "CPI", "category": "价格", "unit": "%", "description": "居民消费价格指数同比", "fetcher": fetch_cpi},
    {"name": "PPI", "category": "价格", "unit": "%", "description": "工业生产者出厂价格指数同比", "fetcher": fetch_ppi},
    {"name": "M2同比", "category": "货币", "unit": "%", "description": "广义货币供应量同比增速", "fetcher": fetch_m2},
    {"name": "M1同比", "category": "货币", "unit": "%", "description": "狭义货币供应量同比增速", "fetcher": fetch_m1},
    {"name": "LPR-1Y", "category": "利率", "unit": "%", "description": "1年期贷款市场报价利率", "fetcher": fetch_lpr_1y},
    {"name": "LPR-5Y", "category": "利率", "unit": "%", "description": "5年期以上贷款市场报价利率", "fetcher": fetch_lpr_5y},
    {"name": "国债收益率-10Y", "category": "利率", "unit": "%", "description": "中国10年期国债收益率", "fetcher": fetch_treasury_10y},
    {"name": "国债收益率-1Y", "category": "利率", "unit": "%", "description": "中国1年期国债收益率", "fetcher": fetch_treasury_1y},
    {"name": "北向资金净流入", "category": "市场", "unit": "亿元", "description": "沪深股通北向资金当日净流入", "fetcher": fetch_northbound},
    {"name": "WTI原油", "category": "商品", "unit": "美元/桶", "description": "WTI原油期货价格", "fetcher": fetch_wti},
    {"name": "COMEX黄金", "category": "商品", "unit": "美元/盎司", "description": "COMEX黄金期货价格", "fetcher": fetch_gold},
    {"name": "外汇储备", "category": "储备", "unit": "亿美元", "description": "中国外汇储备", "fetcher": fetch_forex_reserves},
]


# ============================================================
# 核心逻辑
# ============================================================

def fetch_single_indicator(indicator_def):
    """拉取单个指标。返回结果字典。"""
    name = indicator_def["name"]
    result = {
        "name": name,
        "category": indicator_def["category"],
        "unit": indicator_def["unit"],
        "description": indicator_def["description"],
        "value": None,
        "prev_value": None,
        "change": None,
        "change_pct": None,
        "data_date": "",
        "source": "",
        "status": "failed",
        "error": None,
    }

    try:
        fetched = indicator_def["fetcher"]()

        value = fetched.get("value")
        if value is None:
            result["status"] = "no_data"
            result["error"] = "数据值为空"
            return result

        result["value"] = round(float(value), 4) if isinstance(value, (int, float)) else value
        result["data_date"] = fetched.get("data_date", "")
        result["source"] = fetched.get("source", "")
        result["status"] = "success"

        prev_value = fetched.get("prev_value")
        if prev_value is not None:
            result["prev_value"] = round(float(prev_value), 4)
            change = float(value) - float(prev_value)
            result["change"] = round(change, 4)
            if abs(float(prev_value)) > 1e-10:
                result["change_pct"] = round(change / abs(float(prev_value)) * 100, 4)

    except Exception as exc:
        result["error"] = str(exc)

    return result


def load_previous_data(output_dir):
    """加载上次运行的数据用于前值比较。"""
    output_path = Path(output_dir)
    json_files = sorted(glob.glob(str(output_path / "macro_*.json")), reverse=True)
    if not json_files:
        return {}

    try:
        with open(json_files[0], "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {
            ind["name"]: ind.get("value")
            for ind in data.get("indicators", [])
            if ind.get("value") is not None
        }
    except Exception:
        return {}


def merge_with_previous(results, previous_data):
    """用上次数据补充前值比较（当 API 未返回前值时）。"""
    for result in results:
        name = result["name"]
        if result["prev_value"] is None and name in previous_data:
            prev_val = previous_data[name]
            result["prev_value"] = prev_val
            if result["value"] is not None and prev_val is not None:
                try:
                    change = float(result["value"]) - float(prev_val)
                    result["change"] = round(change, 4)
                    if abs(float(prev_val)) > 1e-10:
                        result["change_pct"] = round(
                            change / abs(float(prev_val)) * 100, 4
                        )
                except (ValueError, TypeError):
                    pass


def generate_markdown(results, summary, pull_time):
    """生成 Markdown 报告。"""
    lines = [
        "# 中国宏观经济数据报告",
        "",
        "**拉取时间**: {}".format(pull_time),
        "**统计**: 成功 {}/{}, 失败 {}".format(
            summary["success"], summary["total"], summary["failed"]
        ),
        "**数据源**: 东方财富、新浪财经（公开 API，零依赖）",
        "",
    ]

    categories = {}
    for result in results:
        cat = result["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(result)

    for cat, items in categories.items():
        lines.append("## {}".format(cat))
        lines.append("")
        lines.append("| 指标 | 最新值 | 前值 | 变化 | 数据日期 | 状态 |")
        lines.append("|------|--------|------|------|----------|------|")

        for item in items:
            if item["status"] == "success":
                val_str = "{}{}".format(item["value"], item["unit"])
                prev_str = (
                    "{}{}".format(item["prev_value"], item["unit"])
                    if item["prev_value"] is not None
                    else "-"
                )
                if item["change"] is not None:
                    direction = "+" if item["change"] >= 0 else ""
                    change_str = "{}{}{}".format(
                        direction, item["change"], item["unit"]
                    )
                    if item["change_pct"] is not None:
                        change_str += " ({}{}%)".format(
                            direction, item["change_pct"]
                        )
                else:
                    change_str = "-"
                date_str = item["data_date"] or "-"
                lines.append(
                    "| {} | {} | {} | {} | {} | ✅ |".format(
                        item["name"], val_str, prev_str, change_str, date_str
                    )
                )
            else:
                error_short = (item["error"] or "未知错误")[:50]
                lines.append(
                    "| {} | - | - | - | - | ❌ {} |".format(
                        item["name"], error_short
                    )
                )

        lines.append("")

    if summary["failed"] > 0:
        lines.append("## 错误详情")
        lines.append("")
        for item in results:
            if item["status"] == "failed":
                lines.append("- **{}**: {}".format(item["name"], item["error"]))
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="零依赖中国宏观经济数据追踪（纯标准库）"
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="指定拉取类别，支持逗号分隔多类别（价格/货币/利率/市场/商品/储备）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="输出目录（默认当前目录）",
    )
    parser.add_argument(
        "--format",
        type=str,
        default="all",
        choices=["all", "json", "markdown"],
        help="输出格式",
    )
    parser.add_argument(
        "--mode",
        choices=["standalone", "collaborative"],
        default="standalone",
        help="运行模式：standalone=独立运行, collaborative=工作流协同",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  中国宏观经济数据追踪（零依赖版）")
    print("=" * 60)
    print()
    print("Python {}".format(sys.version.split()[0]))
    print("数据源: 东方财富 + 新浪财经（公开 API，无需 token）")

    indicators = INDICATORS
    if args.category:
        # 支持逗号分隔的多类别
        categories = [c.strip() for c in args.category.split(",")]
        valid_cats = {"价格", "货币", "利率", "市场", "商品", "储备"}
        invalid = [c for c in categories if c not in valid_cats]
        if invalid:
            parser.error(
                "未知类别: {}。可用: {}".format(
                    ", ".join(invalid), ", ".join(sorted(valid_cats))
                )
            )
        indicators = [ind for ind in indicators if ind["category"] in categories]
        if not indicators:
            print(
                "\n[错误] 指定类别下无指标",
                file=sys.stderr,
            )
            sys.exit(2)
        print("筛选类别: {}".format(", ".join(categories)))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    previous_data = load_previous_data(output_dir)
    if previous_data:
        print(
            "\n📂 已加载上次数据（{} 个指标）用于前值比较".format(
                len(previous_data)
            )
        )

    print(
        "\n📊 开始拉取 {} 个宏观指标...\n".format(len(indicators))
    )

    results = []
    for i, indicator_def in enumerate(indicators, 1):
        name = indicator_def["name"]
        print(
            "  [{}/{}] {} ({})...".format(
                i, len(indicators), name, indicator_def["description"]
            ),
            end=" ",
            flush=True,
        )
        result = fetch_single_indicator(indicator_def)
        results.append(result)

        if result["status"] == "success":
            val_str = "{}{}".format(result["value"], result["unit"])
            change_str = ""
            if result["change"] is not None:
                direction = "+" if result["change"] >= 0 else ""
                change_str = " (变化: {}{}{})".format(
                    direction, result["change"], result["unit"]
                )
            print("✅ {}{}".format(val_str, change_str))
        else:
            print("❌ {}".format(result["error"]))

    merge_with_previous(results, previous_data)

    summary = {
        "total": len(results),
        "success": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] != "success"),
    }

    pull_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_data = {
        "pull_time": pull_time,
        "indicators": results,
        "summary": summary,
        "errors": [
            {"name": r["name"], "error": r["error"]}
            for r in results
            if r["status"] != "success"
        ],
    }

    if args.format in ("all", "json"):
        json_path = output_dir / "macro_{}.json".format(timestamp)
        with open(str(json_path), "w", encoding="utf-8") as fh:
            json.dump(output_data, fh, ensure_ascii=False, indent=2)
        print("\n📄 JSON 报告: {}".format(json_path))

    if args.format in ("all", "markdown"):
        md_content = generate_markdown(results, summary, pull_time)
        md_path = output_dir / "macro_{}.md".format(timestamp)
        with open(str(md_path), "w", encoding="utf-8") as fh:
            fh.write(md_content)
        print("📝 Markdown 报告: {}".format(md_path))

    # collaborative 模式下额外输出标准化文件名（合并写入，避免多次调用覆盖）
    if args.mode == "collaborative":
        collab_path = output_dir / "macro_data.json"
        macro_path = str(collab_path)
        new_data = output_data
        if os.path.exists(macro_path):
            with open(macro_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            # 合并 indicators（以 name 去重，新数据覆盖旧数据）
            existing_map = {ind["name"]: ind for ind in existing.get("indicators", [])}
            for ind in new_data["indicators"]:
                existing_map[ind["name"]] = ind
            merged_indicators = list(existing_map.values())
            # 更新
            existing["indicators"] = merged_indicators
            existing["pull_time"] = new_data["pull_time"]
            existing["summary"] = {
                "total": len(merged_indicators),
                "success": sum(1 for i in merged_indicators if i.get("status") == "success"),
                "failed": sum(1 for i in merged_indicators if i.get("status") == "failed"),
            }
            new_data = existing
        with open(macro_path, "w", encoding="utf-8") as fh:
            json.dump(new_data, fh, ensure_ascii=False, indent=2)
        print("📄 协同模式输出: {}".format(collab_path))

    print("\n{}".format("=" * 60))
    print(
        "  完成: 成功 {}/{}, 失败 {}".format(
            summary["success"], summary["total"], summary["failed"]
        )
    )
    print("{}".format("=" * 60))

    if summary["failed"] > 0:
        print("\n⚠️  部分指标拉取失败，详见输出文件中的 errors 字段。")
        print("常见原因: 网络超时、API 临时不可用、非交易时段无数据。")
        print("建议: 稍后重试。")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
