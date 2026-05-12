#!/usr/bin/env python3
"""
A股同行业公司对比分析工具。

自动识别目标股票所属行业，批量获取同行业公司核心指标并排名，
选取可比公司进行相对估值。

零第三方依赖（仅用 Python 3.6+ 标准库），数据来源东方财富 push2 公开 API。

用法:
    python industry_compare.py --code 600519
    python industry_compare.py --code 600519 --top-n 30 --format json
    python industry_compare.py --code 600519 --output-dir ./out --mode collaborative
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime

# ============================================================
# 导入共享工具（可选，独立运行时自带回退实现）
# ============================================================

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..', '..', '..'))

try:
    sys.path.insert(0, _PROJECT_ROOT)
    from scripts.http_utils import (
        http_get_json_with_retry as _shared_retry,
        _get_secid as _shared_get_secid,
        _safe_float as _shared_safe_float,
    )
    _HAS_SHARED = True
except ImportError:
    _HAS_SHARED = False

# ============================================================
# 常量
# ============================================================

FETCH_TIMEOUT = 15
REQUEST_INTERVAL = 0.5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# push2 API 字段映射
# 个股行业信息
STOCK_INFO_FIELDS = "f57,f58,f127,f128,f129,f130,f131,f132,f133,f134,f135,f14,f43,f162,f167"
# 行业成分股批量行情
CLIST_FIELDS = "f2,f3,f9,f12,f14,f20,f21,f23,f24,f25,f115,f128,f140,f141"

# ============================================================
# 基础工具函数（独立运行时的内置实现）
# ============================================================


def _create_ssl_context():
    """创建 SSL 上下文（跳过证书验证，适用于内网环境）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _safe_float(val, default=None):
    """安全转换浮点数，处理 None/NaN/非数字/'-'。"""
    if _HAS_SHARED:
        return _shared_safe_float(val, default)
    if val is None or val == '-' or val == '':
        return default
    try:
        v = float(val)
        return v if v == v else default  # NaN check
    except (ValueError, TypeError):
        return default


def _get_secid(code):
    """根据股票代码前缀判断交易所，生成东方财富 secid。"""
    if _HAS_SHARED:
        return _shared_get_secid(code)
    prefix = code[:2]
    if prefix in ("60", "68"):
        return f"1.{code}"
    return f"0.{code}"


def http_get_json(url, headers=None, timeout=FETCH_TIMEOUT,
                  max_retries=3, backoff_base=2):
    """
    GET 请求并解析 JSON，带指数退避重试。

    返回 (data_dict, error_str) 元组：
    - 成功时: (parsed_json, None)
    - 失败时: (None, "错误描述")
    """
    if _HAS_SHARED:
        return _shared_retry(url, headers, timeout, max_retries, backoff_base)

    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)

    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            ctx = _create_ssl_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data, None
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                wait = backoff_base ** attempt
                time.sleep(wait)

    return None, last_error


# ============================================================
# 数据获取
# ============================================================


def fetch_stock_industry(code):
    """
    获取个股行业信息。

    返回 dict:
        name: 股票名称
        price: 当前价格
        pe_ttm: PE(TTM)
        pb_mrq: PB(MRQ)
        industry_name: 行业名称
        industry_code: 行业代码 (BK开头)
    或 (None, error_str)
    """
    secid = _get_secid(code)
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&fields={STOCK_INFO_FIELDS}"
    )
    data, err = http_get_json(url)
    if err:
        return None, f"获取个股信息失败: {err}"

    d = data.get("data")
    if not d:
        return None, "个股信息返回数据为空，请检查代码是否正确"

    industry_name = d.get("f127")
    industry_code = d.get("f128")
    if not industry_name or not industry_code or industry_name == '-':
        return None, f"无法识别行业信息: industry_name={industry_name}, industry_code={industry_code}"

    result = {
        "code": str(d.get("f57", code)),
        "name": d.get("f58") or d.get("f14") or code,
        "price": _safe_float(d.get("f43")),
        "pe_ttm": _safe_float(d.get("f162")),
        "pb_mrq": _safe_float(d.get("f167")),
        "industry_name": str(industry_name),
        "industry_code": str(industry_code),
    }
    # push2 stock/get 的 f43 价格可能是 *1000 格式，也可能是真实值
    # 通常 f43 返回的是实际价格 * 1000 (取决于接口版本)，这里做安全处理
    # 如果价格明显过大（>10000），认为是 *1000 格式
    if result["price"] is not None and result["price"] > 10000:
        result["price"] = result["price"] / 1000.0

    return result, None


def fetch_industry_stocks(bk_code, page_size=500):
    """
    获取行业成分股批量行情。

    返回 (list_of_dict, error_str):
    每个 dict 包含:
        code, name, total_market_cap(亿元), pe_ttm, pb_mrq,
        revenue_growth(%), net_profit_growth(%)
    """
    url = (
        f"https://push2.eastmoney.com/api/qt/clist/get"
        f"?pn=1&pz={page_size}&po=1&np=1&fltt=2&invt=2"
        f"&fid=f3&fs=b:{bk_code}&fields={CLIST_FIELDS}"
    )
    data, err = http_get_json(url)
    if err:
        return None, f"获取行业成分股失败: {err}"

    d = data.get("data")
    if not d:
        return None, "行业成分股返回数据为空"

    diff = d.get("diff")
    if not diff:
        return [], None

    stocks = []
    for item in diff:
        code = str(item.get("f12", ""))
        name = item.get("f14", "")
        if not code or code == '-':
            continue

        # f20: 总市值(元) -> 转亿元
        cap_raw = _safe_float(item.get("f20"))
        total_market_cap = cap_raw / 1e8 if cap_raw is not None else None

        # f9: PE(TTM)
        pe_ttm = _safe_float(item.get("f9"))
        # f115 也是 PE(TTM) 的备用字段
        if pe_ttm is None:
            pe_ttm = _safe_float(item.get("f115"))

        # f23: PB(MRQ)
        pb_mrq = _safe_float(item.get("f23"))

        # f24: 营收增速(%)  f25: 净利增速(%)
        revenue_growth = _safe_float(item.get("f24"))
        net_profit_growth = _safe_float(item.get("f25"))

        # f140: ROE (如果有)
        roe = _safe_float(item.get("f140"))

        stocks.append({
            "code": code,
            "name": name,
            "total_market_cap": total_market_cap,
            "pe_ttm": pe_ttm,
            "pb_mrq": pb_mrq,
            "roe": roe,
            "revenue_growth": revenue_growth,
            "net_profit_growth": net_profit_growth,
        })

    return stocks, None


# ============================================================
# 排名计算
# ============================================================

# 排名方向: True=越大越好(排名1), False=越小越好(排名1)
METRIC_CONFIG = {
    "total_market_cap": {"unit": "亿元", "ascending": False, "label": "总市值"},
    "pe_ttm":           {"unit": "倍",   "ascending": True,  "label": "PE(TTM)"},
    "pb_mrq":           {"unit": "倍",   "ascending": True,  "label": "PB(MRQ)"},
    "roe":              {"unit": "%",    "ascending": False, "label": "ROE"},
    "revenue_growth":   {"unit": "%",    "ascending": False, "label": "营收增速"},
    "net_profit_growth": {"unit": "%",   "ascending": False, "label": "净利增速"},
}


def compute_rankings(target_code, stocks):
    """
    计算目标股票在各维度的排名。

    PE/PB 排名：值越小越好（ascending=True, 正值从小到大排，排名1最小）
    市值/ROE/增速：值越大越好（ascending=False, 从大到小排，排名1最大）

    对于 PE/PB，负值（亏损）排到最后。

    返回 list of ranking dict（仅包含有数据的指标）。
    """
    rankings = []

    for metric, cfg in METRIC_CONFIG.items():
        # 收集有效值的股票
        valid = []
        for s in stocks:
            val = s.get(metric)
            if val is not None:
                valid.append((s["code"], val))

        if not valid:
            continue

        # 找到目标股票的值
        target_val = None
        for s in stocks:
            if s["code"] == target_code:
                target_val = s.get(metric)
                break

        if target_val is None:
            continue

        total = len(valid)

        if cfg["ascending"]:
            # PE/PB: 越小越好，但负值排到最后
            # 分为正值组（从小到大）和负值组（排在最后）
            positive = [(c, v) for c, v in valid if v > 0]
            negative = [(c, v) for c, v in valid if v <= 0]
            positive.sort(key=lambda x: x[1])
            negative.sort(key=lambda x: x[1], reverse=True)
            sorted_list = positive + negative
        else:
            # 市值/ROE/增速: 越大越好
            sorted_list = sorted(valid, key=lambda x: x[1], reverse=True)

        # 找排名
        rank = total  # 默认最后
        for i, (c, v) in enumerate(sorted_list):
            if c == target_code:
                rank = i + 1
                break

        percentile = round((1 - (rank - 1) / max(total - 1, 1)) * 100, 1) if total > 1 else 100.0

        rankings.append({
            "metric": metric,
            "value": round(target_val, 4) if target_val is not None else None,
            "unit": cfg["unit"],
            "rank": rank,
            "total": total,
            "percentile": percentile,
        })

    return rankings


# ============================================================
# 可比公司选取
# ============================================================


def select_comparable(target_code, target_cap, stocks, min_count=3, max_count=5):
    """
    选取可比公司：市值在目标的 0.3x ~ 3x 范围内，排除自身。
    按市值接近程度排序，选取 3-5 家。
    """
    if target_cap is None or target_cap <= 0:
        return []

    lower = target_cap * 0.3
    upper = target_cap * 3.0

    candidates = []
    for s in stocks:
        if s["code"] == target_code:
            continue
        cap = s.get("total_market_cap")
        if cap is None or cap <= 0:
            continue
        if lower <= cap <= upper:
            # 市值差距比
            ratio = abs(cap - target_cap) / target_cap
            candidates.append((s, ratio))

    # 按市值接近程度排序
    candidates.sort(key=lambda x: x[1])

    selected = []
    for s, ratio in candidates[:max_count]:
        reason_parts = []
        cap = s.get("total_market_cap")
        if cap is not None:
            reason_parts.append(f"市值{cap:.1f}亿元({cap/target_cap:.1f}x)")
        if s.get("pe_ttm") is not None and s["pe_ttm"] > 0:
            reason_parts.append(f"PE {s['pe_ttm']:.1f}")
        if s.get("pb_mrq") is not None and s["pb_mrq"] > 0:
            reason_parts.append(f"PB {s['pb_mrq']:.2f}")

        selected.append({
            "code": s["code"],
            "name": s["name"],
            "total_market_cap": round(cap, 2) if cap is not None else None,
            "pe_ttm": round(s["pe_ttm"], 2) if s.get("pe_ttm") is not None else None,
            "pb_mrq": round(s["pb_mrq"], 4) if s.get("pb_mrq") is not None else None,
            "roe": round(s["roe"], 2) if s.get("roe") is not None else None,
            "revenue_growth": round(s["revenue_growth"], 2) if s.get("revenue_growth") is not None else None,
            "net_profit_growth": round(s["net_profit_growth"], 2) if s.get("net_profit_growth") is not None else None,
            "selection_reason": ", ".join(reason_parts) if reason_parts else "同行业可比",
        })

    return selected


# ============================================================
# 可比估值
# ============================================================


def compute_peer_valuation(target_info, comparables):
    """
    基于可比公司平均 PE/PB 计算隐含股价。

    implied_price_by_pe = peer_avg_pe * EPS
       EPS = price / pe_ttm
    implied_price_by_pb = peer_avg_pb * BVPS
       BVPS = price / pb_mrq
    """
    result = {
        "peer_avg_pe": None,
        "peer_avg_pb": None,
        "implied_price_by_pe": None,
        "implied_price_by_pb": None,
        "premium_discount_pe": None,
        "premium_discount_pb": None,
    }

    if not comparables:
        return result

    price = target_info.get("price")
    target_pe = target_info.get("pe_ttm")
    target_pb = target_info.get("pb_mrq")

    # 收集可比公司正 PE
    pe_vals = [c["pe_ttm"] for c in comparables if c.get("pe_ttm") and c["pe_ttm"] > 0]
    pb_vals = [c["pb_mrq"] for c in comparables if c.get("pb_mrq") and c["pb_mrq"] > 0]

    if pe_vals:
        avg_pe = sum(pe_vals) / len(pe_vals)
        result["peer_avg_pe"] = round(avg_pe, 2)

        if price and target_pe and target_pe > 0:
            eps = price / target_pe
            implied = avg_pe * eps
            result["implied_price_by_pe"] = round(implied, 2)
            result["premium_discount_pe"] = round((price / implied - 1) * 100, 2)

    if pb_vals:
        avg_pb = sum(pb_vals) / len(pb_vals)
        result["peer_avg_pb"] = round(avg_pb, 4)

        if price and target_pb and target_pb > 0:
            bvps = price / target_pb
            implied = avg_pb * bvps
            result["implied_price_by_pb"] = round(implied, 2)
            result["premium_discount_pb"] = round((price / implied - 1) * 100, 2)

    return result


# ============================================================
# Markdown 报告
# ============================================================


def format_markdown(result):
    """将分析结果格式化为 Markdown 文本。"""
    lines = []
    lines.append(f"# {result['name']}({result['code']}) 同行业对比分析")
    lines.append(f"")
    lines.append(f"**行业**: {result['industry_name']}  ")
    lines.append(f"**同行业公司数**: {result['peer_count']}  ")
    lines.append(f"**分析日期**: {result['analysis_date']}  ")
    lines.append(f"**状态**: {result['status']}")
    lines.append("")

    # 排名
    rankings = result.get("rankings", [])
    if rankings:
        lines.append("## 行业排名")
        lines.append("")
        lines.append("| 指标 | 当前值 | 排名 | 分位数 |")
        lines.append("|------|--------|------|--------|")
        for r in rankings:
            label = METRIC_CONFIG.get(r["metric"], {}).get("label", r["metric"])
            val_str = f"{r['value']}" if r["value"] is not None else "N/A"
            unit = r.get("unit", "")
            pct = f"{r['percentile']:.1f}%" if r.get("percentile") is not None else "N/A"
            lines.append(f"| {label} | {val_str} {unit} | {r['rank']}/{r['total']} | {pct} |")
        lines.append("")

    # 可比公司
    comparables = result.get("comparable_companies", [])
    if comparables:
        lines.append("## 可比公司")
        lines.append("")
        lines.append("| 代码 | 名称 | 市值(亿) | PE(TTM) | PB(MRQ) | 选取理由 |")
        lines.append("|------|------|----------|---------|---------|----------|")
        for c in comparables:
            cap_str = f"{c['total_market_cap']:.1f}" if c.get('total_market_cap') is not None else "N/A"
            pe_str = f"{c['pe_ttm']:.1f}" if c.get('pe_ttm') is not None else "N/A"
            pb_str = f"{c['pb_mrq']:.2f}" if c.get('pb_mrq') is not None else "N/A"
            lines.append(f"| {c['code']} | {c['name']} | {cap_str} | {pe_str} | {pb_str} | {c.get('selection_reason', '')} |")
        lines.append("")

    # 估值
    val = result.get("valuation_by_peers", {})
    if val and (val.get("peer_avg_pe") or val.get("peer_avg_pb")):
        lines.append("## 可比公司估值")
        lines.append("")
        if val.get("peer_avg_pe") is not None:
            lines.append(f"- 可比公司平均 PE: **{val['peer_avg_pe']:.2f}** 倍")
            if val.get("implied_price_by_pe") is not None:
                lines.append(f"- PE 法隐含股价: **{val['implied_price_by_pe']:.2f}** 元")
            if val.get("premium_discount_pe") is not None:
                pd = val["premium_discount_pe"]
                tag = "溢价" if pd > 0 else "折价"
                lines.append(f"- 当前{tag}: {abs(pd):.1f}%")
        if val.get("peer_avg_pb") is not None:
            lines.append(f"- 可比公司平均 PB: **{val['peer_avg_pb']:.4f}** 倍")
            if val.get("implied_price_by_pb") is not None:
                lines.append(f"- PB 法隐含股价: **{val['implied_price_by_pb']:.2f}** 元")
            if val.get("premium_discount_pb") is not None:
                pd = val["premium_discount_pb"]
                tag = "溢价" if pd > 0 else "折价"
                lines.append(f"- 当前{tag}: {abs(pd):.1f}%")
        lines.append("")

    return "\n".join(lines)


# ============================================================
# 行业排名表（Top N）
# ============================================================


def format_top_n_table(stocks, target_code, top_n=20):
    """生成行业市值 Top N 排名表的 Markdown。"""
    # 按市值降序
    valid = [s for s in stocks if s.get("total_market_cap") is not None]
    valid.sort(key=lambda x: x["total_market_cap"], reverse=True)
    top = valid[:top_n]

    lines = []
    lines.append(f"## 行业市值 Top {min(top_n, len(top))}")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 市值(亿) | PE(TTM) | PB(MRQ) |")
    lines.append("|---|------|------|----------|---------|---------|")

    for i, s in enumerate(top, 1):
        marker = " **<-**" if s["code"] == target_code else ""
        cap = f"{s['total_market_cap']:.1f}"
        pe = f"{s['pe_ttm']:.1f}" if s.get("pe_ttm") is not None else "-"
        pb = f"{s['pb_mrq']:.2f}" if s.get("pb_mrq") is not None else "-"
        lines.append(f"| {i} | {s['code']} | {s['name']}{marker} | {cap} | {pe} | {pb} |")

    lines.append("")
    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================


def run_analysis(code, top_n=20):
    """
    执行同行业对比分析。

    返回 (result_dict, status_str):
    result_dict 符合 industry-compare.json 契约。
    """
    analysis_date = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    status = "success"

    # 1. 获取目标股票行业信息
    target_info, err = fetch_stock_industry(code)
    if err:
        return {
            "code": code,
            "name": code,
            "industry_name": "",
            "industry_code": "",
            "analysis_date": analysis_date,
            "peer_count": 0,
            "rankings": [],
            "comparable_companies": [],
            "valuation_by_peers": {},
            "status": "failed",
            "_error": err,
        }, "failed"

    time.sleep(REQUEST_INTERVAL)

    # 2. 获取行业成分股
    stocks, err = fetch_industry_stocks(target_info["industry_code"])
    if err:
        return {
            "code": target_info["code"],
            "name": target_info["name"],
            "industry_name": target_info["industry_name"],
            "industry_code": target_info["industry_code"],
            "analysis_date": analysis_date,
            "peer_count": 0,
            "rankings": [],
            "comparable_companies": [],
            "valuation_by_peers": {},
            "status": "partial",
            "_error": err,
        }, "partial"

    peer_count = len(stocks)

    # 确保目标股票在列表中（补充数据）
    target_in_list = False
    for s in stocks:
        if s["code"] == code:
            target_in_list = True
            # 用 stock/get 的精确值更新
            if target_info.get("pe_ttm") is not None:
                s["pe_ttm"] = target_info["pe_ttm"]
            if target_info.get("pb_mrq") is not None:
                s["pb_mrq"] = target_info["pb_mrq"]
            break

    if not target_in_list:
        # 目标不在成分股列表，手动添加
        stocks.append({
            "code": code,
            "name": target_info["name"],
            "total_market_cap": None,
            "pe_ttm": target_info.get("pe_ttm"),
            "pb_mrq": target_info.get("pb_mrq"),
            "roe": None,
            "revenue_growth": None,
            "net_profit_growth": None,
        })
        status = "partial"

    # 3. 排名计算
    rankings = compute_rankings(code, stocks)
    if not rankings:
        status = "partial"

    # 4. 选取可比公司
    target_cap = None
    for s in stocks:
        if s["code"] == code:
            target_cap = s.get("total_market_cap")
            break
    comparables = select_comparable(code, target_cap, stocks)

    # 5. 可比公司估值
    valuation = compute_peer_valuation(target_info, comparables)

    result = {
        "code": target_info["code"],
        "name": target_info["name"],
        "industry_name": target_info["industry_name"],
        "industry_code": target_info["industry_code"],
        "analysis_date": analysis_date,
        "peer_count": peer_count,
        "rankings": rankings,
        "comparable_companies": comparables,
        "valuation_by_peers": valuation,
        "status": status,
    }

    return result, status


# ============================================================
# CLI
# ============================================================


def parse_args():
    parser = argparse.ArgumentParser(
        description="A股同行业公司对比分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--code", required=True, help="6位A股代码，如 600519")
    parser.add_argument("--top-n", type=int, default=20, help="行业排名显示条数（默认20）")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认标准输出）")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="输出格式: text(Markdown) / json")
    parser.add_argument("--mode", choices=["standalone", "collaborative"], default="standalone",
                        help="运行模式: standalone(独立) / collaborative(协作)")
    return parser.parse_args()


def main():
    args = parse_args()
    code = args.code.strip()

    # 校验代码格式
    if not code.isdigit() or len(code) != 6:
        print(f"错误: 股票代码格式不正确 '{code}'，需要6位数字", file=sys.stderr)
        sys.exit(1)

    # 执行分析
    result, status = run_analysis(code, top_n=args.top_n)

    if args.format == "json":
        output = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        # Markdown 输出
        output = format_markdown(result)
        # 追加 Top N 表
        if result.get("peer_count", 0) > 0 and status != "failed":
            # 重新获取 stocks 用于生成排名表（避免存储冗余数据）
            stocks, _ = fetch_industry_stocks(result.get("industry_code", ""))
            if stocks:
                output += "\n" + format_top_n_table(stocks, code, args.top_n)

    # 输出
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        ext = "json" if args.format == "json" else "md"
        filename = f"{code}_industry_compare.{ext}"
        filepath = os.path.join(args.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"已保存至: {filepath}", file=sys.stderr)

        # collaborative 模式同时输出 JSON 供其他组件读取
        if args.mode == "collaborative":
            json_path = os.path.join(args.output_dir, f"{code}_industry_compare.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"协作 JSON: {json_path}", file=sys.stderr)
    else:
        print(output)

    # 退出码
    if status == "failed":
        sys.exit(1)
    elif status == "partial":
        sys.exit(0)  # partial 仍然算成功，有部分数据
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
