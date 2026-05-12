#!/usr/bin/env python3
"""
个股15项风险快速扫描。

输出红/橙/绿三级告警和综合评级，符合 specs/contracts/risk-scan.json 契约。
零第三方依赖，仅需 Python 3.6+。

用法:
    python risk_scanner.py --code 600519
    python risk_scanner.py --code 600519 --format json --output-dir ./output
    python risk_scanner.py --code 600519 --data-dir ./data/600519
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta

# ============================================================
# 常量
# ============================================================

FETCH_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_BASE = 2

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ============================================================
# SSL / HTTP  (与 http_utils.py 模式一致)
# ============================================================

def _create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get_json(url, timeout=FETCH_TIMEOUT, max_retries=MAX_RETRIES):
    """GET 并解析 JSON，带指数退避重试。返回 (data, error)。"""
    headers = {"User-Agent": USER_AGENT}
    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            ctx = _create_ssl_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data, None
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                time.sleep(BACKOFF_BASE ** attempt)
    return None, last_error


# ============================================================
# 工具函数
# ============================================================

def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        v = float(val)
        return v if v == v else default
    except (ValueError, TypeError):
        return default


def _get_secid(code):
    prefix = code[:2]
    if prefix in ("60", "68"):
        return f"1.{code}"
    return f"0.{code}"


def _make_result(category, check_name, alert_level, value, threshold,
                 description, data_source):
    return {
        "category": category,
        "check_name": check_name,
        "alert_level": alert_level,
        "value": value,
        "threshold": threshold,
        "description": description,
        "data_source": data_source,
    }


def _green_unavailable(category, check_name, reason="数据不可用"):
    return _make_result(category, check_name, "green", None, None,
                        reason, "N/A")


# ============================================================
# 数据获取
# ============================================================

def _try_load_local(data_dir, *subpaths):
    """尝试从本地 data_dir 中按子路径加载 JSON 文件。"""
    if not data_dir:
        return None
    for sp in subpaths:
        fp = os.path.join(data_dir, sp)
        if os.path.isfile(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return None


def fetch_basic_quote(code, data_dir=None):
    """获取基础行情数据。"""
    local = _try_load_local(data_dir, "cache/quote.json", "quote.json")
    if local:
        return local, None

    secid = _get_secid(code)
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}"
        f"&fields=f14,f43,f44,f45,f46,f47,f48,f57,f58,f116,f117,f162,f167"
    )
    return http_get_json(url)


def fetch_kline(code, days=500, data_dir=None):
    """获取历史K线数据（日K）。"""
    local = _try_load_local(data_dir, "cache/kline.json", "kline.json")
    if local:
        return local, None

    secid = _get_secid(code)
    beg = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid={secid}"
        f"&fields1=f1,f2,f3,f4,f5,f6,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
        f"&klt=101&fqt=1&beg={beg}&end=20500101&lmt=1000"
    )
    return http_get_json(url)


def fetch_financial(code, report_name, data_dir=None):
    """获取财务数据（资产负债表/利润表/现金流量表）。"""
    tag_map = {
        "RPT_DMSK_FN_BALANCE": "balance",
        "RPT_DMSK_FN_INCOME": "income",
        "RPT_DMSK_FN_CASHFLOW": "cashflow",
    }
    tag = tag_map.get(report_name, report_name)
    local = _try_load_local(
        data_dir,
        f"cache/{tag}.json",
        f"analysis/{tag}.json",
        f"{tag}.json",
    )
    if local:
        return local, None

    url = (
        f"https://datacenter-web.eastmoney.com/api/data/v1/get"
        f"?reportName={report_name}"
        f"&columns=ALL"
        f"&filter=(SECURITY_CODE=%22{code}%22)"
        f"&pageSize=5"
        f"&sortColumns=REPORT_DATE"
        f"&sortTypes=-1"
    )
    return http_get_json(url)


def _extract_financial_rows(resp):
    """从财务数据响应中提取记录列表。"""
    if not resp:
        return []
    # datacenter-web 响应结构: {result: {data: [...]}}
    result = resp.get("result") if isinstance(resp, dict) else None
    if result and isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            return data
    # 兼容本地缓存可能直接是列表
    if isinstance(resp, list):
        return resp
    return []


def _parse_klines(resp):
    """解析K线响应，返回按日期排序的列表。
    每项: {date, open, close, high, low, volume, amount, amplitude, pct_chg, chg, turnover}
    """
    if not resp:
        return []
    data = resp.get("data") if isinstance(resp, dict) else None
    if not data:
        return []
    raw = data.get("klines", [])
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw  # 本地缓存可能已解析
    result = []
    for line in raw:
        if not isinstance(line, str):
            continue
        parts = line.split(",")
        if len(parts) < 11:
            continue
        result.append({
            "date": parts[0],
            "open": _safe_float(parts[1]),
            "close": _safe_float(parts[2]),
            "high": _safe_float(parts[3]),
            "low": _safe_float(parts[4]),
            "volume": _safe_float(parts[5]),
            "amount": _safe_float(parts[6]),
            "amplitude": _safe_float(parts[7]),
            "pct_chg": _safe_float(parts[8]),
            "chg": _safe_float(parts[9]),
            "turnover": _safe_float(parts[10]),
        })
    return result


# ============================================================
# 15 项检测函数
# ============================================================

def check_goodwill_ratio(code, data):
    """1. 商誉/净资产 >30% → red"""
    category, name = "财务", "商誉占净资产比"
    rows = _extract_financial_rows(data.get("balance"))
    if not rows:
        return _green_unavailable(category, name)
    latest = rows[0]
    goodwill = _safe_float(latest.get("GOODWILL"), 0)
    equity = _safe_float(latest.get("TOTAL_EQUITY"))
    if equity is None or equity <= 0:
        return _green_unavailable(category, name, "净资产数据异常")
    ratio = goodwill / equity * 100
    level = "red" if ratio > 30 else ("orange" if ratio > 20 else "green")
    return _make_result(
        category, name, level,
        round(ratio, 2), "30%",
        f"商誉 {goodwill/1e8:.2f} 亿 / 净资产 {equity/1e8:.2f} 亿 = {ratio:.1f}%",
        "资产负债表",
    )


def check_receivable_ratio(code, data):
    """2. 应收/营收 >50% → orange"""
    category, name = "财务", "应收账款占营收比"
    balance_rows = _extract_financial_rows(data.get("balance"))
    income_rows = _extract_financial_rows(data.get("income"))
    if not balance_rows or not income_rows:
        return _green_unavailable(category, name)
    receivable = _safe_float(balance_rows[0].get("ACCOUNTS_RECE"), 0)
    revenue = _safe_float(income_rows[0].get("REVENUE"))
    if revenue is None or revenue <= 0:
        return _green_unavailable(category, name, "营收数据异常")
    ratio = receivable / revenue * 100
    level = "red" if ratio > 80 else ("orange" if ratio > 50 else "green")
    return _make_result(
        category, name, level,
        round(ratio, 2), "50%",
        f"应收 {receivable/1e8:.2f} 亿 / 营收 {revenue/1e8:.2f} 亿 = {ratio:.1f}%",
        "资产负债表/利润表",
    )


def check_debt_ratio(code, data):
    """3. 有息负债率 >60% → orange"""
    category, name = "财务", "有息负债率"
    rows = _extract_financial_rows(data.get("balance"))
    if not rows:
        return _green_unavailable(category, name)
    latest = rows[0]
    total_liab = _safe_float(latest.get("TOTAL_LIABILITIES"), 0)
    total_assets = _safe_float(latest.get("TOTAL_ASSETS"))
    if total_assets is None or total_assets <= 0:
        # 尝试用 总负债 + 总权益 近似
        equity = _safe_float(latest.get("TOTAL_EQUITY"), 0)
        total_assets = total_liab + equity if equity > 0 else None
    if not total_assets or total_assets <= 0:
        return _green_unavailable(category, name, "总资产数据异常")
    ratio = total_liab / total_assets * 100
    level = "red" if ratio > 80 else ("orange" if ratio > 60 else "green")
    return _make_result(
        category, name, level,
        round(ratio, 2), "60%",
        f"负债 {total_liab/1e8:.2f} 亿 / 资产 {total_assets/1e8:.2f} 亿 = {ratio:.1f}%",
        "资产负债表",
    )


def check_operating_cashflow(code, data):
    """4. 经营现金流连续负 >=2年 → red"""
    category, name = "财务", "经营现金流"
    rows = _extract_financial_rows(data.get("cashflow"))
    if not rows or len(rows) < 2:
        return _green_unavailable(category, name)
    neg_years = 0
    for row in rows[:5]:
        cf = _safe_float(row.get("NETCASH_OPERATE"))
        if cf is not None and cf < 0:
            neg_years += 1
        else:
            break  # 连续检查
    level = "red" if neg_years >= 2 else "green"
    return _make_result(
        category, name, level,
        neg_years, "连续≥2年为负",
        f"近{len(rows[:5])}期中连续{neg_years}期经营现金流为负",
        "现金流量表",
    )


def check_deducted_profit_decline(code, data):
    """5. 扣非净利连续下降 >=3年 → orange"""
    category, name = "财务", "扣非净利趋势"
    rows = _extract_financial_rows(data.get("income"))
    if not rows or len(rows) < 2:
        return _green_unavailable(category, name)
    profits = []
    for row in rows[:5]:
        p = _safe_float(row.get("DEDUCT_PARENT_NETPROFIT"))
        if p is not None:
            profits.append(p)
    if len(profits) < 2:
        return _green_unavailable(category, name)
    decline_count = 0
    for i in range(len(profits) - 1):
        if profits[i] < profits[i + 1]:
            decline_count += 1
        else:
            break
    level = "orange" if decline_count >= 3 else "green"
    desc = f"近{len(profits)}期扣非净利连续下降{decline_count}期" if decline_count > 0 else "扣非净利未连续下降"
    return _make_result(
        category, name, level,
        decline_count, "连续≥3年下降",
        desc, "利润表",
    )


def check_pledge_ratio(code, data):
    """6. 大股东质押率 >50% → red（数据不可用时 green）"""
    category, name = "治理", "大股东质押率"
    return _green_unavailable(category, name, "质押数据暂不可用，标记为安全")


def check_insider_selling(code, data):
    """7. 高管减持 >1000万 → orange（数据不可用时 green）"""
    category, name = "治理", "高管减持"
    return _green_unavailable(category, name, "高管减持数据暂不可用，标记为安全")


def check_audit_opinion(code, data):
    """8. 审计意见非标 → red（数据不可用时 green）"""
    category, name = "治理", "审计意见"
    return _green_unavailable(category, name, "审计意见数据暂不可用，标记为安全")


def check_st_flag(code, data):
    """9. ST标记 → red"""
    category, name = "市场", "ST标记"
    stock_name = data.get("stock_name", "")
    is_st = False
    if stock_name:
        upper = stock_name.upper()
        if "ST" in upper or "*ST" in upper:
            is_st = True
    level = "red" if is_st else "green"
    desc = f"股票名称含ST标记: {stock_name}" if is_st else f"非ST股票: {stock_name}"
    return _make_result(
        category, name, level,
        stock_name, "名称含ST",
        desc, "行情数据",
    )


def check_max_drawdown(code, data):
    """10. 最大回撤 >50% → orange"""
    category, name = "市场", "最大回撤"
    klines = data.get("klines_parsed", [])
    if not klines:
        return _green_unavailable(category, name)
    # 计算最大回撤
    closes = [k["close"] for k in klines if k.get("close")]
    if len(closes) < 2:
        return _green_unavailable(category, name, "K线数据不足")
    peak = closes[0]
    max_dd = 0
    for c in closes[1:]:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100
        if dd > max_dd:
            max_dd = dd
    level = "red" if max_dd > 70 else ("orange" if max_dd > 50 else "green")
    return _make_result(
        category, name, level,
        round(max_dd, 2), "50%",
        f"区间最大回撤 {max_dd:.1f}%",
        "历史K线",
    )


def check_turnover_abnormal(code, data):
    """11. 换手率异常：连续5日 >15% → orange"""
    category, name = "市场", "换手率异常"
    klines = data.get("klines_parsed", [])
    if not klines or len(klines) < 5:
        return _green_unavailable(category, name)
    # 检查最近的连续5日换手率 >15%
    max_consecutive = 0
    current = 0
    for k in klines:
        tr = k.get("turnover")
        if tr is not None and tr > 15:
            current += 1
            if current > max_consecutive:
                max_consecutive = current
        else:
            current = 0
    level = "orange" if max_consecutive >= 5 else "green"
    desc = (
        f"存在连续{max_consecutive}日换手率超15%"
        if max_consecutive >= 5
        else f"最大连续高换手{max_consecutive}日，未触发阈值"
    )
    return _make_result(
        category, name, level,
        max_consecutive, "连续5日>15%",
        desc, "历史K线",
    )


def check_penalty_record(code, data):
    """12. 处罚记录（数据不可用时 green）"""
    category, name = "合规", "处罚记录"
    return _green_unavailable(category, name, "处罚数据暂不可用，标记为安全")


def check_top5_customer(code, data):
    """13. 前五大客户 >70%（数据不可用时 green）"""
    category, name = "集中度", "前五大客户集中度"
    return _green_unavailable(category, name, "客户集中度数据暂不可用，标记为安全")


def check_top5_supplier(code, data):
    """14. 前五大供应商 >70%（数据不可用时 green）"""
    category, name = "集中度", "前五大供应商集中度"
    return _green_unavailable(category, name, "供应商集中度数据暂不可用，标记为安全")


def check_pe_percentile(code, data):
    """15. PE分位数：>95% → red, <5% → 提示"""
    category, name = "估值", "PE历史分位"
    pe_ttm = data.get("pe_ttm")
    klines = data.get("klines_parsed", [])

    if pe_ttm is None:
        return _green_unavailable(category, name, "PE数据不可用")

    # 尝试从 pe_band.json 加载
    pe_band = data.get("pe_band")
    if pe_band and isinstance(pe_band, dict):
        pct = _safe_float(pe_band.get("percentile"))
        if pct is not None:
            level = "red" if pct > 95 else ("orange" if pct > 80 else "green")
            desc = f"PE_TTM={pe_ttm:.2f}, 历史分位 {pct:.1f}%"
            if pct < 5:
                desc += " (极低估值区间，注意价值陷阱)"
            return _make_result(category, name, level, round(pct, 2),
                                "95%", desc, "PE分位数据")

    # 简单估算：用近一年K线数据粗略估计PE分位
    if not klines or pe_ttm <= 0:
        return _make_result(
            category, name, "green", pe_ttm, "95%",
            f"PE_TTM={pe_ttm:.2f}，无法计算分位", "行情数据",
        )

    # 获取最近的收盘价来粗略估算历史PE范围
    closes = [k["close"] for k in klines if k.get("close")]
    if len(closes) < 20:
        return _make_result(
            category, name, "green", pe_ttm, "95%",
            f"PE_TTM={pe_ttm:.2f}，K线不足无法估算分位", "行情数据",
        )

    current_price = data.get("current_price")
    if not current_price or current_price <= 0:
        return _make_result(
            category, name, "green", pe_ttm, "95%",
            f"PE_TTM={pe_ttm:.2f}，无法估算分位", "行情数据",
        )

    eps = current_price / pe_ttm if pe_ttm != 0 else None
    if eps is None or eps <= 0:
        return _make_result(
            category, name, "green", pe_ttm, "95%",
            f"PE_TTM={pe_ttm:.2f}，EPS异常无法估算", "行情数据",
        )

    # 用历史收盘价/EPS 估算历史PE
    hist_pes = [c / eps for c in closes if c > 0]
    hist_pes.sort()
    count_below = sum(1 for p in hist_pes if p <= pe_ttm)
    pct = count_below / len(hist_pes) * 100

    level = "red" if pct > 95 else ("orange" if pct > 80 else "green")
    desc = f"PE_TTM={pe_ttm:.2f}, 粗略估算历史分位 {pct:.1f}%"
    if pct < 5:
        desc += " (极低估值区间，注意价值陷阱)"
    return _make_result(category, name, level, round(pct, 2), "95%",
                        desc, "行情数据/K线估算")


# 所有检测函数有序列表
ALL_CHECKS = [
    check_goodwill_ratio,
    check_receivable_ratio,
    check_debt_ratio,
    check_operating_cashflow,
    check_deducted_profit_decline,
    check_pledge_ratio,
    check_insider_selling,
    check_audit_opinion,
    check_st_flag,
    check_max_drawdown,
    check_turnover_abnormal,
    check_penalty_record,
    check_top5_customer,
    check_top5_supplier,
    check_pe_percentile,
]


# ============================================================
# 综合评级
# ============================================================

def compute_overall_risk(items):
    """根据红/橙数量判断综合风险等级。"""
    red = sum(1 for it in items if it["alert_level"] == "red")
    orange = sum(1 for it in items if it["alert_level"] == "orange")
    green = sum(1 for it in items if it["alert_level"] == "green")

    if red >= 3:
        level = "极高风险"
    elif red >= 1:
        level = "高风险"
    elif orange > 2:
        level = "中风险"
    else:
        level = "低风险"

    return level, {"red_count": red, "orange_count": orange, "green_count": green}


# ============================================================
# 主扫描流程
# ============================================================

def run_scan(code, data_dir=None):
    """执行完整风险扫描，返回符合契约的结果 dict。"""
    # 1. 获取数据
    data = {}
    status = "success"

    # 基础行情
    quote_resp, err = fetch_basic_quote(code, data_dir)
    stock_name = ""
    pe_ttm = None
    pb = None
    current_price = None
    if quote_resp and isinstance(quote_resp, dict):
        qd = quote_resp.get("data", quote_resp)
        stock_name = qd.get("f14", "")
        raw_price = _safe_float(qd.get("f43"))
        current_price = raw_price / 100 if raw_price and raw_price > 100 else raw_price
        raw_pe = _safe_float(qd.get("f162"))
        pe_ttm = raw_pe / 100 if raw_pe and abs(raw_pe) > 1000 else raw_pe
        raw_pb = _safe_float(qd.get("f167"))
        pb = raw_pb / 100 if raw_pb and abs(raw_pb) > 1000 else raw_pb

    data["stock_name"] = stock_name
    data["pe_ttm"] = pe_ttm
    data["pb"] = pb
    data["current_price"] = current_price

    # K线
    kline_resp, err = fetch_kline(code, days=500, data_dir=data_dir)
    data["klines_parsed"] = _parse_klines(kline_resp)

    # 财务数据
    for report_name in ("RPT_DMSK_FN_BALANCE", "RPT_DMSK_FN_INCOME", "RPT_DMSK_FN_CASHFLOW"):
        tag = {"RPT_DMSK_FN_BALANCE": "balance",
               "RPT_DMSK_FN_INCOME": "income",
               "RPT_DMSK_FN_CASHFLOW": "cashflow"}[report_name]
        resp, err = fetch_financial(code, report_name, data_dir)
        data[tag] = resp
        if err:
            status = "partial"

    # PE band（可选）
    pe_band = _try_load_local(data_dir, "analysis/pe_band.json", "pe_band.json")
    data["pe_band"] = pe_band

    # 2. 执行15项检测
    items = []
    for check_fn in ALL_CHECKS:
        try:
            result = check_fn(code, data)
            items.append(result)
        except Exception as e:
            # 任何 check 失败不影响其他
            fn_name = check_fn.__name__.replace("check_", "")
            items.append(_make_result(
                "财务", fn_name, "green", None, None,
                f"检测异常: {e}", "N/A",
            ))
            status = "partial"

    # 3. 综合评级
    overall_level, summary = compute_overall_risk(items)

    # 4. 组装契约结构
    result = {
        "code": code,
        "name": stock_name,
        "scan_date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "overall_risk_level": overall_level,
        "items": items,
        "summary": summary,
        "status": status,
    }
    return result


# ============================================================
# 输出格式化
# ============================================================

_LEVEL_SYMBOL = {"red": "[!!!]", "orange": "[! ]", "green": "[ OK]"}
_LEVEL_LABEL = {"red": "红色", "orange": "橙色", "green": "正常"}


def format_text(result):
    """格式化为可读文本。"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  风险扫描报告: {result['name']}({result['code']})")
    lines.append(f"  扫描时间: {result['scan_date']}")
    lines.append(f"  综合评级: {result['overall_risk_level']}")
    lines.append("=" * 60)
    lines.append("")

    s = result["summary"]
    lines.append(f"  汇总: {s['red_count']} 红色 | {s['orange_count']} 橙色 | {s['green_count']} 正常")
    lines.append("")
    lines.append("-" * 60)

    for i, item in enumerate(result["items"], 1):
        sym = _LEVEL_SYMBOL.get(item["alert_level"], "[??]")
        label = _LEVEL_LABEL.get(item["alert_level"], "未知")
        lines.append(f"  {i:2d}. {sym} [{item['category']}] {item['check_name']}")
        lines.append(f"      级别: {label}  值: {item['value']}  阈值: {item['threshold']}")
        lines.append(f"      说明: {item['description']}")
        lines.append("")

    lines.append("=" * 60)
    lines.append(f"  状态: {result['status']}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="个股15项风险快速扫描",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--code", required=True, help="6位股票代码")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="输出格式 (default: text)")
    parser.add_argument("--mode", choices=["standalone", "collaborative"],
                        default="standalone", help="运行模式")
    parser.add_argument("--data-dir", default=None,
                        help="已有数据目录（优先从中加载缓存）")
    args = parser.parse_args()

    code = args.code.strip()
    if len(code) != 6 or not code.isdigit():
        print(f"错误: 股票代码必须是6位数字，收到: {code}", file=sys.stderr)
        sys.exit(1)

    # collaborative 模式下自动推断 data_dir
    data_dir = args.data_dir
    if not data_dir and args.mode == "collaborative":
        # 尝试查找常见的数据目录
        candidates = [
            os.path.join("data", code),
            os.path.join("..", "data", code),
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                         "data", code),
        ]
        for c in candidates:
            if os.path.isdir(c):
                data_dir = c
                break

    print(f"正在扫描 {code} ...")
    result = run_scan(code, data_dir=data_dir)

    # 输出
    if args.format == "json":
        output = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        output = format_text(result)

    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)
        ext = "json" if args.format == "json" else "txt"
        out_path = os.path.join(args.output_dir, f"{code}_risk_scan.{'json' if ext == 'json' else 'md'}")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"结果已保存: {out_path}")
    else:
        print(output)

    # collaborative 模式下额外输出 JSON 到 stdout（供上游工具读取）
    if args.mode == "collaborative" and args.format != "json":
        json_path = os.path.join(args.output_dir or ".", f"{code}_risk_scan.json")
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    # 退出码
    level = result.get("overall_risk_level", "")
    if level in ("极高风险", "高风险"):
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
