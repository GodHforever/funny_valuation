#!/usr/bin/env python3
"""A股历史估值分位数分析（PE Band）。

零依赖，仅使用 Python 标准库。获取近 N 年 PE(TTM)/PB(MRQ)/PS(TTM)
日频数据，计算分位数，给出估值判定。

数据源: 东方财富 datacenter-web / push2his / push2 公开 API。

用法:
    python pe_band.py --code 600519                       # 默认5年
    python pe_band.py --code 000858 --years 10            # 10年
    python pe_band.py --code 300750 --format json          # 仅JSON
    python pe_band.py --code 600519 --output-dir ./out     # 指定输出目录
    python pe_band.py --code 600519 --mode collaborative   # 协作模式
"""

import argparse
import json
import math
import os
import ssl
import sys
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ============================================================
# 常量
# ============================================================

FETCH_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

VERDICT_RANGES = [
    (10, "极度低估"),
    (30, "低估"),
    (70, "合理"),
    (90, "高估"),
    (100, "极度高估"),
]

# ============================================================
# SSL
# ============================================================

def _create_ssl_context():
    """创建不验证证书的 SSL 上下文（适用于企业内网等缺少根证书的环境）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ============================================================
# HTTP 工具函数
# ============================================================

def http_get_json(url, headers=None, timeout=FETCH_TIMEOUT,
                  max_retries=3, backoff_base=2):
    """GET 请求并解析 JSON，带指数退避重试。

    Returns:
        (parsed_json, None) 成功时
        (None, error_str)   失败时
    """
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)

    last_error = None
    for attempt in range(max_retries):
        try:
            req = Request(url, headers=hdrs)
            ctx = _create_ssl_context()
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                data = json.loads(raw.decode(charset))
                return data, None
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                wait = backoff_base ** attempt
                time.sleep(wait)

    return None, last_error


# ============================================================
# 数据工具
# ============================================================

def _safe_float(val, default=None):
    """安全转换浮点数，处理 None / NaN / 非数字。"""
    if val is None:
        return default
    try:
        v = float(val)
        return v if v == v else default  # NaN check: NaN != NaN
    except (ValueError, TypeError):
        return default


def _get_secid(code):
    """根据股票代码前缀判断交易所，生成东方财富 secid。

    规则:
    - 60/68 开头 -> 上交所 (1.{code})
    - 其他       -> 深交所 (0.{code})
    """
    prefix = code[:2]
    if prefix in ("60", "68"):
        return f"1.{code}"
    return f"0.{code}"


def _round_or_none(val, ndigits=2):
    """安全四舍五入，None 返回 None。"""
    if val is None:
        return None
    return round(val, ndigits)


def _percentile(sorted_data, current):
    """计算分位数: 小于等于 current 的数据占比 x 100。"""
    if not sorted_data or current is None:
        return None
    count_le = sum(1 for v in sorted_data if v <= current)
    return round(count_le / len(sorted_data) * 100, 2)


def _verdict(pct):
    """根据分位数给出估值判定。"""
    if pct is None:
        return "合理"
    for threshold, label in VERDICT_RANGES:
        if pct <= threshold:
            return label
    return "极度高估"


def _median(sorted_data):
    """计算中位数。"""
    n = len(sorted_data)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 0:
        return (sorted_data[mid - 1] + sorted_data[mid]) / 2
    return sorted_data[mid]


def _mean(data):
    """计算均值。"""
    if not data:
        return None
    return sum(data) / len(data)


def _std(data, avg=None):
    """计算标准差（总体标准差）。"""
    if not data or len(data) < 2:
        return None
    if avg is None:
        avg = _mean(data)
    variance = sum((x - avg) ** 2 for x in data) / len(data)
    return math.sqrt(variance)


# ============================================================
# 数据获取: datacenter 估值接口（优先）
# ============================================================

DATACENTER_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_VALUEANALYSIS_DET"
    "&columns=ALL"
    '&filter=(SECURITY_CODE="{code}")'
    "&pageSize=2000"
    "&sortColumns=TRADE_DATE"
    "&sortTypes=-1"
    "&pageNumber={page}"
    "&source=WEB"
    "&client=WEB"
)


def _fetch_datacenter_valuation(code, beg_date):
    """从 datacenter 估值明细接口获取历史 PE/PB/PS 数据。

    Returns:
        (records_list, error_str)
        records_list: [{date, pe, pb, ps, close, market_cap}, ...]
    """
    all_records = []
    page = 1
    max_pages = 20  # 防止无限翻页

    while page <= max_pages:
        url = DATACENTER_URL.format(code=code, page=page)
        data, err = http_get_json(url)
        if err:
            if all_records:
                break  # 已有部分数据，不再继续
            return None, f"datacenter 请求失败: {err}"

        if not data or not data.get("success"):
            if all_records:
                break
            msg = data.get("message", "未知错误") if data else "空响应"
            return None, f"datacenter API 错误: {msg}"

        result = data.get("result", {})
        rows = result.get("data") or []
        if not rows:
            break

        reached_boundary = False
        for row in rows:
            trade_date_str = row.get("TRADE_DATE", "")
            # 日期格式: "2024-01-15 00:00:00" 或 "2024-01-15"
            date_part = trade_date_str[:10] if trade_date_str else ""
            if date_part < beg_date:
                reached_boundary = True
                break

            pe = _safe_float(row.get("PE_TTM"))
            pb = _safe_float(row.get("PB_MRQ"))
            ps = _safe_float(row.get("PS_TTM"))
            close = _safe_float(row.get("CLOSE_PRICE"))
            mcap = _safe_float(row.get("TOTAL_MARKET_CAP"))

            all_records.append({
                "date": date_part,
                "pe": pe,
                "pb": pb,
                "ps": ps,
                "close": close,
                "market_cap": mcap,
            })

        if reached_boundary:
            break

        # 检查是否有更多页
        total_pages = result.get("pages", 1)
        if page >= total_pages:
            break
        page += 1

    if not all_records:
        return None, "datacenter 无数据"

    return all_records, None


# ============================================================
# 数据获取: K线接口（回退方案）
# ============================================================

KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?secid={secid}"
    "&fields1=f1,f2,f3,f4,f5,f6,f7"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    "&klt=101&fqt=1"
    "&beg={beg}&end=20500101&lmt=5000"
)


def _fetch_kline_data(code, beg_yyyymmdd):
    """从 push2his K线接口获取历史数据。

    K线 fields2 对应: f51=日期, f52=开, f53=收, f54=高, f55=低,
    f56=成交量, f57=成交额, f58=振幅, f59=涨跌幅, f60=涨跌额, f61=换手率

    注意: K线接口不包含 PE/PB 等估值指标，仅能获取价格和成交量。
    若 datacenter 接口可用，K线数据主要用于补充收盘价信息。

    Returns:
        (records_list, error_str)
    """
    secid = _get_secid(code)
    url = KLINE_URL.format(secid=secid, beg=beg_yyyymmdd)
    data, err = http_get_json(url)
    if err:
        return None, f"K线请求失败: {err}"

    klines_raw = (data.get("data") or {}).get("klines") or []
    if not klines_raw:
        return None, "K线无数据"

    records = []
    for line in klines_raw:
        parts = line.split(",")
        if len(parts) < 7:
            continue
        date_str = parts[0]       # YYYY-MM-DD
        close = _safe_float(parts[2])
        volume = _safe_float(parts[5])
        turnover = _safe_float(parts[6])

        records.append({
            "date": date_str,
            "close": close,
            "volume": volume,
            "turnover": turnover,
        })

    if not records:
        return None, "K线解析后无有效数据"

    return records, None


# ============================================================
# 数据获取: 当前估值（push2 实时接口）
# ============================================================

REALTIME_URL = (
    "https://push2.eastmoney.com/api/qt/stock/get"
    "?secid={secid}"
    "&fields=f14,f43,f57,f58,f162,f167"
)


def _fetch_current_valuation(code):
    """获取当前股价和估值指标。

    f14=名称, f43=最新价(需/100), f162=PE(TTM)(需/100), f167=PB(MRQ)(需/100)

    Returns:
        (info_dict, error_str)
    """
    secid = _get_secid(code)
    url = REALTIME_URL.format(secid=secid)
    data, err = http_get_json(url)
    if err:
        return None, f"实时行情请求失败: {err}"

    d = data.get("data")
    if not d:
        return None, "实时行情无数据"

    name = d.get("f14", "")
    price_raw = _safe_float(d.get("f43"))
    pe_raw = _safe_float(d.get("f162"))
    pb_raw = _safe_float(d.get("f167"))

    # push2 接口原始值需要除以100
    price = price_raw / 100 if price_raw is not None else None
    pe = pe_raw / 100 if pe_raw is not None else None
    pb = pb_raw / 100 if pb_raw is not None else None

    return {
        "name": name,
        "price": price,
        "pe": pe,
        "pb": pb,
    }, None


# ============================================================
# 核心分析逻辑
# ============================================================

def _compute_band(values, current):
    """计算估值 band 统计指标。

    Args:
        values: 已过滤的有效历史值列表
        current: 当前值

    Returns:
        dict 包含 min, max, median, mean, std, percentile, verdict
    """
    if not values:
        return {
            "current": _round_or_none(current),
            "min": None,
            "max": None,
            "median": None,
            "mean": None,
            "std": None,
            "percentile": None,
            "verdict": "合理",
        }

    sorted_vals = sorted(values)
    avg = _mean(sorted_vals)

    return {
        "current": _round_or_none(current),
        "min": _round_or_none(sorted_vals[0]),
        "max": _round_or_none(sorted_vals[-1]),
        "median": _round_or_none(_median(sorted_vals)),
        "mean": _round_or_none(avg),
        "std": _round_or_none(_std(sorted_vals, avg)),
        "percentile": _percentile(sorted_vals, current) if current is not None else None,
        "verdict": _verdict(_percentile(sorted_vals, current) if current is not None else None),
    }


def analyze(code, years=5):
    """执行估值分位数分析。

    Args:
        code: 6位A股代码
        years: 分析年限 (3/5/10)

    Returns:
        dict 符合 pe-band.json 契约
    """
    today = datetime.now()
    beg_dt = today - timedelta(days=years * 365)
    beg_date = beg_dt.strftime("%Y-%m-%d")       # datacenter 用
    beg_yyyymmdd = beg_dt.strftime("%Y%m%d")      # kline 用
    analysis_date = today.strftime("%Y-%m-%dT%H:%M:%S")

    errors = []

    # -- 1. 获取当前估值 --
    current_info, cur_err = _fetch_current_valuation(code)
    if cur_err:
        errors.append(f"当前估值: {cur_err}")
        current_info = {"name": "", "price": None, "pe": None, "pb": None}

    stock_name = current_info.get("name", "")
    current_pe = current_info.get("pe")
    current_pb = current_info.get("pb")
    current_ps = None  # push2 不提供 PS，后续从 datacenter 补充

    # -- 2. 获取历史估值数据（优先 datacenter）--
    hist_pe_values = []
    hist_pb_values = []
    hist_ps_values = []
    data_source = "datacenter"
    data_points = 0

    dc_records, dc_err = _fetch_datacenter_valuation(code, beg_date)
    if dc_err:
        errors.append(f"datacenter: {dc_err}")
        data_source = "kline_fallback"

        # 回退: 从 K线获取价格数据（但无法得到 PE/PB/PS）
        kl_records, kl_err = _fetch_kline_data(code, beg_yyyymmdd)
        if kl_err:
            errors.append(f"K线回退: {kl_err}")
        else:
            data_points = len(kl_records)
            # K线不含估值指标，只能用当前值做单点分析
            # 此情况下历史数组为空，percentile 为 None
    else:
        data_points = len(dc_records)

        # 从 datacenter 最新记录补充当前值（若 push2 获取失败）
        if dc_records:
            latest = dc_records[0]  # 按日期降序，第一条是最新
            if current_pe is None:
                current_pe = latest.get("pe")
            if current_pb is None:
                current_pb = latest.get("pb")
            if current_ps is None:
                current_ps = latest.get("ps")

        for rec in dc_records:
            pe = rec.get("pe")
            pb = rec.get("pb")
            ps = rec.get("ps")

            # PE < 0 排除（亏损股 PE 无意义）
            if pe is not None and pe > 0:
                hist_pe_values.append(pe)
            if pb is not None:
                hist_pb_values.append(pb)
            if ps is not None and ps > 0:
                hist_ps_values.append(ps)

    # -- 3. 计算 band --
    pe_band = _compute_band(hist_pe_values, current_pe)
    pe_band["data_source"] = data_source

    pb_band = _compute_band(hist_pb_values, current_pb)
    pb_band["data_source"] = data_source

    ps_band = _compute_band(hist_ps_values, current_ps)
    ps_band["data_source"] = data_source

    # -- 4. 组装输出 --
    if not errors:
        status = "success"
    elif hist_pe_values or hist_pb_values:
        status = "partial"
    else:
        status = "failed"

    result = {
        "code": code,
        "name": stock_name,
        "analysis_date": analysis_date,
        "period_years": years,
        "pe_band": pe_band,
        "pb_band": pb_band,
        "ps_band": ps_band,
        "data_points": data_points,
        "status": status,
    }

    if errors:
        result["errors"] = errors

    return result


# ============================================================
# 报告生成
# ============================================================

def _band_table_row(label, band):
    """生成 band 表格的一行。"""
    def fmt(v):
        return f"{v:.2f}" if v is not None else "-"

    pct = band.get("percentile")
    pct_str = f"{pct:.1f}%" if pct is not None else "-"

    return (
        f"| {label} | {fmt(band.get('current'))} | "
        f"{fmt(band.get('min'))} | {fmt(band.get('max'))} | "
        f"{fmt(band.get('median'))} | {fmt(band.get('mean'))} | "
        f"{pct_str} | {band.get('verdict', '-')} |"
    )


def generate_report(result):
    """生成 Markdown 格式的估值分析报告。

    Args:
        result: analyze() 返回的结果字典

    Returns:
        str Markdown 文本
    """
    code = result["code"]
    name = result.get("name", "")
    years = result["period_years"]
    date = result["analysis_date"][:10]
    points = result["data_points"]
    status = result["status"]

    lines = [
        f"# {name}({code}) 估值分位数分析报告",
        "",
        f"**分析日期**: {date}  ",
        f"**分析区间**: 近 {years} 年  ",
        f"**有效数据点**: {points}  ",
        f"**状态**: {status}  ",
        "",
        "---",
        "",
        "## 估值概览",
        "",
        "| 指标 | 当前值 | 历史最低 | 历史最高 | 中位数 | 均值 | 分位数 | 判定 |",
        "|------|--------|----------|----------|--------|------|--------|------|",
        _band_table_row("PE(TTM)", result.get("pe_band", {})),
        _band_table_row("PB(MRQ)", result.get("pb_band", {})),
        _band_table_row("PS(TTM)", result.get("ps_band", {})),
        "",
    ]

    # 详细分析段落
    pe = result.get("pe_band", {})
    pb = result.get("pb_band", {})

    lines.append("## 详细分析")
    lines.append("")

    if pe.get("current") is not None and pe.get("percentile") is not None:
        lines.append(f"### PE(TTM) 分析")
        lines.append("")
        lines.append(
            f"当前 PE(TTM) 为 **{pe['current']:.2f}** 倍，"
            f"处于近 {years} 年 **{pe['percentile']:.1f}%** 分位，"
            f"判定为「{pe['verdict']}」。"
        )
        if pe.get("mean") is not None:
            diff = pe["current"] - pe["mean"]
            direction = "高于" if diff > 0 else "低于"
            lines.append(
                f"相比历史均值 {pe['mean']:.2f} 倍{direction} {abs(diff):.2f} 倍。"
            )
        lines.append("")

    if pb.get("current") is not None and pb.get("percentile") is not None:
        lines.append(f"### PB(MRQ) 分析")
        lines.append("")
        lines.append(
            f"当前 PB(MRQ) 为 **{pb['current']:.2f}** 倍，"
            f"处于近 {years} 年 **{pb['percentile']:.1f}%** 分位，"
            f"判定为「{pb['verdict']}」。"
        )
        lines.append("")

    ps = result.get("ps_band", {})
    if ps.get("current") is not None and ps.get("percentile") is not None:
        lines.append(f"### PS(TTM) 分析")
        lines.append("")
        lines.append(
            f"当前 PS(TTM) 为 **{ps['current']:.2f}** 倍，"
            f"处于近 {years} 年 **{ps['percentile']:.1f}%** 分位，"
            f"判定为「{ps['verdict']}」。"
        )
        lines.append("")

    # 判定说明
    lines.extend([
        "---",
        "",
        "## 分位数判定标准",
        "",
        "| 分位数区间 | 判定 |",
        "|-----------|------|",
        "| 0% - 10%  | 极度低估 |",
        "| 10% - 30% | 低估 |",
        "| 30% - 70% | 合理 |",
        "| 70% - 90% | 高估 |",
        "| 90% - 100% | 极度高估 |",
        "",
        "---",
        "",
        f"*数据来源: 东方财富（{pe.get('data_source', 'N/A')}）*  ",
        f"*报告生成时间: {date}*",
    ])

    if result.get("errors"):
        lines.extend(["", "## 警告", ""])
        for e in result["errors"]:
            lines.append(f"- {e}")

    return "\n".join(lines)


# ============================================================
# 输出
# ============================================================

def _write_outputs(result, output_dir, fmt):
    """将 JSON 和 Markdown 报告写入文件。

    Args:
        result: analyze() 返回的结果字典
        output_dir: 输出目录路径
        fmt: "text" / "json" / "both"（text 模式也同时输出 JSON）
    """
    os.makedirs(output_dir, exist_ok=True)
    code = result["code"]

    # 始终输出 JSON
    json_path = os.path.join(output_dir, f"{code}_pe_band.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[OK] JSON -> {json_path}")

    # 生成 Markdown 报告
    md_path = os.path.join(output_dir, f"{code}_pe_band_report.md")
    report = generate_report(result)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[OK] Report -> {md_path}")

    # text 模式同时打印到终端
    if fmt == "text":
        print()
        print(report)


# ============================================================
# CLI
# ============================================================

def parse_args(argv=None):
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="A股历史估值分位数分析（PE Band）"
    )
    parser.add_argument(
        "--code", required=True,
        help="6位A股代码，如 600519"
    )
    parser.add_argument(
        "--years", type=int, default=5, choices=[3, 5, 10],
        help="分析年限，默认5年"
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="输出目录，默认当前目录"
    )
    parser.add_argument(
        "--format", dest="fmt", default="text", choices=["text", "json"],
        help="输出格式: text(终端+文件) / json(仅JSON)"
    )
    parser.add_argument(
        "--mode", default="standalone", choices=["standalone", "collaborative"],
        help="运行模式: standalone(独立) / collaborative(协作，仅输出JSON到stdout)"
    )
    return parser.parse_args(argv)


def main(argv=None):
    """主入口。"""
    args = parse_args(argv)
    code = args.code.strip()

    # 校验代码格式
    if not code.isdigit() or len(code) != 6:
        print(f"[ERROR] 无效股票代码: {code}（需6位数字）", file=sys.stderr)
        sys.exit(1)

    # 执行分析
    result = analyze(code, years=args.years)

    # 协作模式: 仅输出 JSON 到 stdout，供上游脚本解析
    if args.mode == "collaborative":
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    # 独立模式: 写入文件
    _write_outputs(result, args.output_dir, args.fmt)

    # 非成功状态提示
    if result["status"] == "failed":
        print(f"\n[WARN] 分析失败，请检查股票代码或网络", file=sys.stderr)
        sys.exit(2)
    elif result["status"] == "partial":
        print(f"\n[WARN] 部分数据获取失败，结果可能不完整", file=sys.stderr)


if __name__ == "__main__":
    main()
