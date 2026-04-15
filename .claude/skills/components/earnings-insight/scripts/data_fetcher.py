#!/usr/bin/env python3
"""
earnings-insight 数据获取模块。

A股数据通过东方财富公开API零依赖获取，包含：
- 三大财务报表（利润表/资产负债表/现金流量表）
- 领先指标提取（合同负债/预收账款/资本支出等）
- 行业对比与同业排名
- 核心衍生指标计算（ROE/ROA/周转率等）
- 风险预警信号自动生成

仅需 Python 3.6+，零第三方依赖。

用法:
    python data_fetcher.py --code 600519
    python data_fetcher.py --code 600519 --periods 8 --output-dir ./reports
"""

import argparse
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# 尝试导入共享重试工具
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))
    from scripts.http_utils import http_get_json_with_retry as _http_retry
    _HAS_RETRY = True
except ImportError:
    _HAS_RETRY = False

# ============================================================
# 常量与配置
# ============================================================

FETCH_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

REPORT_TYPES = ["income", "balance", "cashflow"]
REPORT_NAMES = {
    "income": "利润表",
    "balance": "资产负债表",
    "cashflow": "现金流量表",
}

EASTMONEY_REPORT_MAP = {
    "income": "RPT_DMSK_FN_INCOME",
    "balance": "RPT_DMSK_FN_BALANCE",
    "cashflow": "RPT_DMSK_FN_CASHFLOW",
}


# ============================================================
# HTTP 工具
# ============================================================

def _create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get_json(url, headers=None, timeout=FETCH_TIMEOUT):
    """GET 请求，返回 JSON。支持自动重试（如果共享工具可用）。"""
    if _HAS_RETRY:
        data, err = _http_retry(url, headers=headers, timeout=timeout)
        if err:
            raise RuntimeError(err)
        return data
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ============================================================
# 股票基本信息
# ============================================================

def _safe_float(val):
    if val is None:
        return None
    try:
        v = float(val)
        return v if v == v else None
    except (ValueError, TypeError):
        return None


def _fmt_val(value):
    if value is None:
        return "-", ""
    v = abs(float(value))
    sign = "-" if float(value) < 0 else ""
    if v >= 1e8:
        return f"{sign}{v / 1e8:.2f}", "亿元"
    elif v >= 1e4:
        return f"{sign}{v / 1e4:.2f}", "万元"
    else:
        return f"{sign}{v:.2f}", "元"


def _yoy_change(current, previous):
    c = _safe_float(current)
    p = _safe_float(previous)
    if c is None or p is None or p == 0:
        return None
    return round((c - p) / abs(p) * 100, 2)


def _find_value(record, keys):
    if not record:
        return None
    for key in keys:
        val = record.get(key)
        if val is not None:
            return _safe_float(val)
    return None


def _secid(code):
    prefix = code[:2]
    if prefix in ("60", "68"):
        return f"1.{code}"
    return f"0.{code}"


def _fetch_sina_fallback(code):
    """新浪财经实时行情 fallback（当 push2 API 限流时使用）"""
    prefix = "sz" if code.startswith(("0", "3")) else "sh"
    url = f"https://hq.sinajs.cn/list={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://finance.sina.com.cn"
        })
        resp = urllib.request.urlopen(req, timeout=15, context=_create_ssl_context())
        text = resp.read().decode("gbk")
        match = re.search(r'="(.+)"', text)
        if match:
            fields = match.group(1).split(",")
            if len(fields) >= 4 and fields[0]:
                return {"name": fields[0], "current_price": _safe_float(fields[3])}
    except Exception:
        pass
    return None


def get_stock_info(code):
    info = {"code": code, "name": "", "industry": "", "market": "A股"}
    try:
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={_secid(code)}&fields=f43,f57,f58,f127,f116,f117,f162,f167,f164,f163"
        )
        data = http_get_json(url)
        if data and data.get("data"):
            d = data["data"]
            info["name"] = d.get("f58", "")
            info["industry"] = d.get("f127", "")
            # f43 以分为单位, f116/f117 以元为单位, f162/f163/f164/f167 已乘100
            raw_price = _safe_float(d.get("f43"))
            info["current_price"] = raw_price / 100 if raw_price else None
            info["total_market_cap"] = _safe_float(d.get("f116", 0))
            info["circulating_cap"] = _safe_float(d.get("f117", 0))
            raw_pe = _safe_float(d.get("f162", 0))
            info["pe_ttm"] = raw_pe / 100 if raw_pe else None
            raw_pe_s = _safe_float(d.get("f167", 0))
            info["pe_static"] = raw_pe_s / 100 if raw_pe_s else None
            raw_pb = _safe_float(d.get("f163", 0))
            info["pb_mrq"] = raw_pb / 100 if raw_pb else None
            raw_ps = _safe_float(d.get("f164", 0))
            info["ps_ttm"] = raw_ps / 100 if raw_ps else None
            # 兼容字段：保留旧名称指向新值
            info["market_cap"] = info["total_market_cap"]
            info["pb"] = info["pb_mrq"]
    except Exception as e:
        print(f"  [警告] 获取行情信息失败: {e}", file=sys.stderr)

    # push2 调用失败或 name/price 为空时，使用新浪财经 fallback
    if not info.get("name") or not info.get("current_price"):
        sina_data = _fetch_sina_fallback(code)
        if sina_data:
            if not info.get("name") and sina_data.get("name"):
                info["name"] = sina_data["name"]
            if not info.get("current_price") and sina_data.get("current_price"):
                info["current_price"] = sina_data["current_price"]

    return info


# ============================================================
# 财务报表字段映射
# ============================================================

FIELD_KEYS = {
    # 利润表
    "revenue": ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME"],
    "operating_cost": ["OPERATE_COST", "OPERATE_EXPENSE", "TOTAL_OPERATE_COST"],
    "net_profit": ["NETPROFIT", "NET_PROFIT", "PARENT_NETPROFIT"],
    "net_profit_parent": ["PARENT_NETPROFIT", "DEDUCT_PARENT_NETPROFIT"],
    "deduct_net_profit": ["DEDUCT_PARENT_NETPROFIT"],
    "operating_profit": ["OPERATE_PROFIT"],
    "total_profit": ["TOTAL_PROFIT"],
    "rd_expense": ["RESEARCH_EXPENSE"],
    "sales_expense": ["SALE_EXPENSE"],
    "admin_expense": ["MANAGE_EXPENSE"],
    "finance_expense": ["FINANCE_EXPENSE"],
    "asset_impairment": ["ASSET_IMPAIRMENT_LOSS"],
    "credit_impairment": ["CREDIT_IMPAIRMENT_LOSS"],
    "invest_income": ["INVEST_INCOME"],
    "fair_value_change": ["FAIRVALUE_CHANGE_INCOME"],
    "other_income": ["OTHER_INCOME"],
    "non_recurring": ["NON_OPERATING_INCOME"],
    "non_recurring_expense": ["NON_OPERATING_EXPENSE"],
    "income_tax": ["INCOME_TAX"],
    "minority_interest": ["MINORITY_INTEREST"],

    # 资产负债表
    "total_assets": ["TOTAL_ASSETS"],
    "total_liab": ["TOTAL_LIABILITIES"],
    "total_equity": ["TOTAL_EQUITY", "TOTAL_PARENT_EQUITY"],
    "cash_equiv": ["MONETARYFUNDS", "CURRENCY_FUNDS"],
    "trading_assets": ["TRADE_FINASSET_NOTFVTPL", "TRADABLE_FNNCL_ASSETS"],
    "accounts_recv": ["ACCOUNTS_RECE"],
    "note_recv": ["NOTE_ACCOUNTS_RECE", "NRECE"],
    "prepayment": ["PREPAYMENT", "ADVANCE_RECEIVABLES"],
    "inventory": ["INVENTORY"],
    "contract_assets": ["CONTRACT_ASSET"],
    "other_current_assets": ["OTHER_CURRENT_ASSET"],
    "fixed_assets": ["FIXED_ASSET"],
    "construction_in_progress": ["CONSTRUCTION_IN_PROCESS", "CIP"],
    "intangible_assets": ["INTANGIBLE_ASSET"],
    "goodwill": ["GOODWILL"],
    "long_term_equity_invest": ["LONG_EQUITY_INVEST"],
    "other_noncurrent_assets": ["OTHER_NONCURRENT_ASSET"],
    "short_loan": ["SHORT_LOAN"],
    "note_payable": ["NOTE_ACCOUNTS_PAYABLE"],
    "accounts_payable": ["ACCOUNTS_PAYABLE"],
    "contract_liab": ["CONTRACT_LIAB"],
    "advance_receivables": ["ADVANCE_RECEIVABLES"],
    "employee_payable": ["EMPLOYEE_PAYABLE"],
    "tax_payable": ["TAX_PAYABLE"],
    "other_current_liab": ["OTHER_CURRENT_LIAB"],
    "long_loan": ["LONG_LOAN"],
    "bonds_payable": ["BOND_PAYABLE"],
    "lease_liab": ["LEASE_LIAB"],
    "long_term_payable": ["LONG_PAYABLE"],
    "total_current_assets": ["TOTAL_CURRENT_ASSETS"],
    "total_noncurrent_assets": ["TOTAL_NONCURRENT_ASSETS"],
    "total_current_liab": ["TOTAL_CURRENT_LIAB"],
    "total_noncurrent_liab": ["TOTAL_NONCURRENT_LIAB"],

    # 现金流量表
    "operating_cf": ["NETCASH_OPERATE"],
    "investing_cf": ["NETCASH_INVEST"],
    "financing_cf": ["NETCASH_FINANCE"],
    "cash_increase": ["CCE_ADD"],
    "cash_received_sales": ["SALES_SERVICES"],
    "cash_paid_goods": ["BUY_SERVICES"],
    "cash_paid_employees": ["PAY_STAFF_CASH"],
    "cash_paid_taxes": ["PAY_ALL_TAX"],
    "capex": ["CONSTRUCT_LONG_ASSET"],
    "cash_invest_paid": ["INVEST_PAY_CASH"],
    "cash_received_invest": ["INVEST_INCOME_CASH", "WITHDRAW_INVEST"],
    "cash_received_borrow": ["RECEIVE_LOAN_CASH"],
    "cash_paid_debt": ["PAY_DEBT_CASH"],
    "cash_paid_dividend": ["PAY_OTHER_FINANCE"],
    "depreciation_amortization": ["DEPRECIATION_ETC"],

    # 通用
    "report_date": ["REPORT_DATE", "REPORTDATE"],
    "report_name": ["REPORT_DATE_NAME"],
    "security_name": ["SECURITY_NAME_ABBR", "SECURITY_NAME"],
}


# ============================================================
# 财务数据获取
# ============================================================

def _build_eastmoney_url(report_name, code, periods=8):
    base = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": report_name,
        "columns": "ALL",
        "pageSize": str(periods),
        "pageNumber": "1",
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


def _build_eastmoney_url_v2(report_type, code, periods=8):
    api_map = {
        "income": "RPT_F10_FINANCE_GINCOME",
        "balance": "RPT_F10_FINANCE_GBALANCE",
        "cashflow": "RPT_F10_FINANCE_GCASHFLOW",
    }
    report = api_map.get(report_type)
    if not report:
        return None
    params = {
        "type": report,
        "sty": "ALL",
        "ps": str(periods),
        "p": "1",
        "sr": "-1",
        "st": "REPORT_DATE",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    return f"https://datacenter.eastmoney.com/securities/api/data/get?{urllib.parse.urlencode(params)}"


def fetch_financial_data(code, report_type, periods=8):
    errors = []
    report_name = EASTMONEY_REPORT_MAP.get(report_type)
    if report_name:
        try:
            url = _build_eastmoney_url(report_name, code, periods)
            data = http_get_json(url)
            if data and data.get("result") and data["result"].get("data"):
                return data["result"]["data"], "eastmoney-web", None
            else:
                errors.append("eastmoney-web: 返回空数据")
        except Exception as e:
            errors.append(f"eastmoney-web: {e}")

    try:
        url2 = _build_eastmoney_url_v2(report_type, code, periods)
        if url2:
            data2 = http_get_json(url2)
            if data2 and data2.get("result") and data2["result"].get("data"):
                return data2["result"]["data"], "eastmoney-v2", None
            else:
                errors.append("eastmoney-v2: 返回空数据")
    except Exception as e:
        errors.append(f"eastmoney-v2: {e}")

    return None, None, "; ".join(errors)


def fetch_all_financial_data(code, periods=8):
    results = {}
    for rt in REPORT_TYPES:
        print(f"  [{rt}] {REPORT_NAMES[rt]}...", end=" ", flush=True)
        records, source, error = fetch_financial_data(code, rt, periods)
        if records:
            results[rt] = {"records": records, "source": source}
            print(f"OK ({len(records)}期, {source})")
        else:
            results[rt] = {"records": [], "source": None, "error": error}
            print(f"FAIL: {error}")
    return results


# ============================================================
# 领先指标提取
# ============================================================

def extract_leading_indicators(balance_records, cashflow_records, income_records):
    """
    提取领先指标 — 这些指标往往先于业绩变化而变化。
    包含：合同负债、预收账款、在手订单代理指标、资本支出、研发投入趋势等。
    """
    indicators = {}

    if not balance_records:
        return indicators

    latest_b = balance_records[0]
    prev_b = balance_records[1] if len(balance_records) >= 2 else None

    # 合同负债（预收款项的新准则名称）
    contract_liab = _find_value(latest_b, FIELD_KEYS["contract_liab"])
    prev_contract_liab = _find_value(prev_b, FIELD_KEYS["contract_liab"]) if prev_b else None
    advance_recv = _find_value(latest_b, FIELD_KEYS["advance_receivables"])
    prev_advance_recv = _find_value(prev_b, FIELD_KEYS["advance_receivables"]) if prev_b else None

    # 合同负债 + 预收账款 合并看
    combined_prepaid = 0
    prev_combined_prepaid = 0
    if contract_liab:
        combined_prepaid += contract_liab
    if advance_recv:
        combined_prepaid += advance_recv
    if prev_contract_liab:
        prev_combined_prepaid += prev_contract_liab
    if prev_advance_recv:
        prev_combined_prepaid += prev_advance_recv

    if combined_prepaid > 0:
        indicators["合同负债+预收账款"] = {
            "value": combined_prepaid,
            "formatted": _fmt_val(combined_prepaid),
            "yoy": _yoy_change(combined_prepaid, prev_combined_prepaid) if prev_combined_prepaid > 0 else None,
            "signal_type": "leading",
            "interpretation": "反映未来收入保障程度，持续增长说明需求真实存在",
        }

    # 存货变化（可能预示产能扩张或滞销）
    inventory = _find_value(latest_b, FIELD_KEYS["inventory"])
    prev_inventory = _find_value(prev_b, FIELD_KEYS["inventory"]) if prev_b else None
    if inventory:
        inv_yoy = _yoy_change(inventory, prev_inventory)
        indicators["存货"] = {
            "value": inventory,
            "formatted": _fmt_val(inventory),
            "yoy": inv_yoy,
            "signal_type": "leading",
            "interpretation": "需结合营收增速判断：存货增速>营收增速可能意味着滞销风险",
        }

    # 应收账款变化
    recv = _find_value(latest_b, FIELD_KEYS["accounts_recv"])
    prev_recv = _find_value(prev_b, FIELD_KEYS["accounts_recv"]) if prev_b else None
    if recv:
        recv_yoy = _yoy_change(recv, prev_recv)
        indicators["应收账款"] = {
            "value": recv,
            "formatted": _fmt_val(recv),
            "yoy": recv_yoy,
            "signal_type": "leading",
            "interpretation": "应收增速显著超过营收增速，可能暗示放宽信用条件或回款困难",
        }

    # 在建工程（资本支出的前瞻信号）
    cip = _find_value(latest_b, FIELD_KEYS["construction_in_progress"])
    prev_cip = _find_value(prev_b, FIELD_KEYS["construction_in_progress"]) if prev_b else None
    if cip:
        indicators["在建工程"] = {
            "value": cip,
            "formatted": _fmt_val(cip),
            "yoy": _yoy_change(cip, prev_cip),
            "signal_type": "leading",
            "interpretation": "在建工程增长反映管理层对未来需求的真实判断（行动信号>语言信号）",
        }

    # 资本支出（来自现金流量表）
    if cashflow_records:
        latest_cf = cashflow_records[0]
        prev_cf = cashflow_records[1] if len(cashflow_records) >= 2 else None
        capex = _find_value(latest_cf, FIELD_KEYS["capex"])
        prev_capex = _find_value(prev_cf, FIELD_KEYS["capex"]) if prev_cf else None
        if capex:
            indicators["资本支出"] = {
                "value": capex,
                "formatted": _fmt_val(capex),
                "yoy": _yoy_change(capex, prev_capex),
                "signal_type": "leading",
                "interpretation": "扩产性投资是管理层对未来需求最真实的判断",
            }

    # 研发费用趋势
    if income_records and len(income_records) >= 2:
        rd_trend = []
        for r in income_records[:4]:
            rd = _find_value(r, FIELD_KEYS["rd_expense"])
            rev = _find_value(r, FIELD_KEYS["revenue"])
            date = r.get("REPORT_DATE", "")[:10]
            ratio = round(rd / rev * 100, 2) if rd and rev and rev != 0 else None
            rd_trend.append({"period": date, "rd_expense": rd, "rd_ratio": ratio})
        indicators["研发费用趋势"] = {
            "trend": rd_trend,
            "signal_type": "leading",
            "interpretation": "研发占比持续提升可能预示未来产品竞争力增强（需验证方向合理性）",
        }

    # 员工薪酬（应付职工薪酬变化，反映人员扩张/收缩）
    emp_pay = _find_value(latest_b, FIELD_KEYS["employee_payable"])
    prev_emp_pay = _find_value(prev_b, FIELD_KEYS["employee_payable"]) if prev_b else None
    if emp_pay:
        indicators["应付职工薪酬"] = {
            "value": emp_pay,
            "formatted": _fmt_val(emp_pay),
            "yoy": _yoy_change(emp_pay, prev_emp_pay),
            "signal_type": "supporting",
            "interpretation": "大幅增长可能意味着人员扩张，反映管理层对业务前景的真实判断",
        }

    return indicators


# ============================================================
# 深度分析引擎
# ============================================================

def analyze_income(records):
    if not records:
        return {"status": "no_data"}

    latest = records[0]
    prev = records[1] if len(records) >= 2 else None
    # 尝试找到同期数据（去年同一季度）
    yoy_base = None
    latest_date = latest.get("REPORT_DATE", "")
    for r in records[1:]:
        r_date = r.get("REPORT_DATE", "")
        # 如果月日相同（同一季度），用它做同比
        if latest_date[5:10] == r_date[5:10]:
            yoy_base = r
            break
    if yoy_base is None:
        yoy_base = prev

    analysis = {"report_type": "income", "period_count": len(records), "metrics": {}, "trends": []}

    revenue = _find_value(latest, FIELD_KEYS["revenue"])
    cost = _find_value(latest, FIELD_KEYS["operating_cost"])
    net_profit = _find_value(latest, FIELD_KEYS["net_profit"])
    net_profit_parent = _find_value(latest, FIELD_KEYS["net_profit_parent"])
    deduct_np = _find_value(latest, FIELD_KEYS["deduct_net_profit"])
    op_profit = _find_value(latest, FIELD_KEYS["operating_profit"])
    total_profit = _find_value(latest, FIELD_KEYS["total_profit"])
    rd = _find_value(latest, FIELD_KEYS["rd_expense"])
    sales_exp = _find_value(latest, FIELD_KEYS["sales_expense"])
    admin_exp = _find_value(latest, FIELD_KEYS["admin_expense"])
    fin_exp = _find_value(latest, FIELD_KEYS["finance_expense"])
    asset_impair = _find_value(latest, FIELD_KEYS["asset_impairment"])
    credit_impair = _find_value(latest, FIELD_KEYS["credit_impairment"])
    invest_income = _find_value(latest, FIELD_KEYS["invest_income"])
    other_income = _find_value(latest, FIELD_KEYS["other_income"])
    income_tax = _find_value(latest, FIELD_KEYS["income_tax"])

    gross_margin = round((revenue - cost) / revenue * 100, 2) if revenue and cost and revenue != 0 else None
    net_margin = round(net_profit / revenue * 100, 2) if revenue and net_profit and revenue != 0 else None
    rd_ratio = round(rd / revenue * 100, 2) if revenue and rd and revenue != 0 else None

    prev_revenue = _find_value(yoy_base, FIELD_KEYS["revenue"]) if yoy_base else None
    prev_net_profit = _find_value(yoy_base, FIELD_KEYS["net_profit"]) if yoy_base else None
    prev_cost = _find_value(yoy_base, FIELD_KEYS["operating_cost"]) if yoy_base else None
    prev_np_parent = _find_value(yoy_base, FIELD_KEYS["net_profit_parent"]) if yoy_base else None
    prev_gross_margin = round((prev_revenue - prev_cost) / prev_revenue * 100, 2) if prev_revenue and prev_cost and prev_revenue != 0 else None

    analysis["metrics"] = {
        "营业收入": {"value": revenue, "formatted": _fmt_val(revenue), "yoy": _yoy_change(revenue, prev_revenue)},
        "营业成本": {"value": cost, "formatted": _fmt_val(cost), "yoy": _yoy_change(cost, prev_cost)},
        "净利润": {"value": net_profit, "formatted": _fmt_val(net_profit), "yoy": _yoy_change(net_profit, prev_net_profit)},
        "归母净利润": {"value": net_profit_parent, "formatted": _fmt_val(net_profit_parent), "yoy": _yoy_change(net_profit_parent, prev_np_parent)},
        "扣非归母净利润": {"value": deduct_np, "formatted": _fmt_val(deduct_np)},
        "营业利润": {"value": op_profit, "formatted": _fmt_val(op_profit)},
        "利润总额": {"value": total_profit, "formatted": _fmt_val(total_profit)},
        "毛利率": {"value": gross_margin, "formatted": (f"{gross_margin:.2f}", "%") if gross_margin is not None else ("-", ""), "prev": prev_gross_margin},
        "净利率": {"value": net_margin, "formatted": (f"{net_margin:.2f}", "%") if net_margin is not None else ("-", "")},
        "研发费用": {"value": rd, "formatted": _fmt_val(rd)},
        "研发占比": {"value": rd_ratio, "formatted": (f"{rd_ratio:.2f}", "%") if rd_ratio is not None else ("-", "")},
        "销售费用": {"value": sales_exp, "formatted": _fmt_val(sales_exp)},
        "管理费用": {"value": admin_exp, "formatted": _fmt_val(admin_exp)},
        "财务费用": {"value": fin_exp, "formatted": _fmt_val(fin_exp)},
        "资产减值损失": {"value": asset_impair, "formatted": _fmt_val(asset_impair)},
        "信用减值损失": {"value": credit_impair, "formatted": _fmt_val(credit_impair)},
        "投资收益": {"value": invest_income, "formatted": _fmt_val(invest_income)},
        "其他收益": {"value": other_income, "formatted": _fmt_val(other_income)},
        "所得税费用": {"value": income_tax, "formatted": _fmt_val(income_tax)},
    }

    # 利润质量分析：扣非净利润 vs 归母净利润
    if deduct_np and net_profit_parent and net_profit_parent != 0:
        non_recurring_ratio = round((net_profit_parent - deduct_np) / abs(net_profit_parent) * 100, 2)
        analysis["metrics"]["非经常损益占比"] = {
            "value": non_recurring_ratio,
            "formatted": (f"{non_recurring_ratio:.2f}", "%"),
            "interpretation": "占比过高（>30%）说明利润质量较差，依赖非经常性项目",
        }

    # 有效税率
    if income_tax and total_profit and total_profit > 0:
        effective_tax_rate = round(income_tax / total_profit * 100, 2)
        analysis["metrics"]["有效税率"] = {
            "value": effective_tax_rate,
            "formatted": (f"{effective_tax_rate:.2f}", "%"),
        }

    # 多期趋势
    trend_data = []
    for r in records:
        rev = _find_value(r, FIELD_KEYS["revenue"])
        np_ = _find_value(r, FIELD_KEYS["net_profit"])
        np_parent = _find_value(r, FIELD_KEYS["net_profit_parent"])
        c = _find_value(r, FIELD_KEYS["operating_cost"])
        rd_val = _find_value(r, FIELD_KEYS["rd_expense"])
        date = r.get("REPORT_DATE", "")[:10]
        gm = round((rev - c) / rev * 100, 2) if rev and c and rev != 0 else None
        nm = round(np_ / rev * 100, 2) if rev and np_ and rev != 0 else None
        rd_r = round(rd_val / rev * 100, 2) if rd_val and rev and rev != 0 else None
        trend_data.append({
            "period": date,
            "revenue": rev,
            "net_profit": np_,
            "net_profit_parent": np_parent,
            "gross_margin": gm,
            "net_margin": nm,
            "rd_ratio": rd_r,
        })
    analysis["trends"] = trend_data
    analysis["status"] = "success"
    return analysis


def analyze_balance(records):
    if not records:
        return {"status": "no_data"}

    latest = records[0]
    prev = records[1] if len(records) >= 2 else None

    analysis = {"report_type": "balance", "period_count": len(records), "metrics": {}, "structure": {}}

    total_assets = _find_value(latest, FIELD_KEYS["total_assets"])
    total_liab = _find_value(latest, FIELD_KEYS["total_liab"])
    total_equity = _find_value(latest, FIELD_KEYS["total_equity"])
    cash = _find_value(latest, FIELD_KEYS["cash_equiv"])
    trading_assets = _find_value(latest, FIELD_KEYS["trading_assets"])
    recv = _find_value(latest, FIELD_KEYS["accounts_recv"])
    note_recv = _find_value(latest, FIELD_KEYS["note_recv"])
    prepayment = _find_value(latest, FIELD_KEYS["prepayment"])
    inventory = _find_value(latest, FIELD_KEYS["inventory"])
    contract_assets = _find_value(latest, FIELD_KEYS["contract_assets"])
    fixed = _find_value(latest, FIELD_KEYS["fixed_assets"])
    cip = _find_value(latest, FIELD_KEYS["construction_in_progress"])
    intangible = _find_value(latest, FIELD_KEYS["intangible_assets"])
    goodwill = _find_value(latest, FIELD_KEYS["goodwill"])
    lt_invest = _find_value(latest, FIELD_KEYS["long_term_equity_invest"])
    short_loan = _find_value(latest, FIELD_KEYS["short_loan"])
    accounts_payable = _find_value(latest, FIELD_KEYS["accounts_payable"])
    contract_liab = _find_value(latest, FIELD_KEYS["contract_liab"])
    long_loan = _find_value(latest, FIELD_KEYS["long_loan"])
    bonds = _find_value(latest, FIELD_KEYS["bonds_payable"])
    total_current_assets = _find_value(latest, FIELD_KEYS["total_current_assets"])
    total_noncurrent_assets = _find_value(latest, FIELD_KEYS["total_noncurrent_assets"])
    total_current_liab = _find_value(latest, FIELD_KEYS["total_current_liab"])
    total_noncurrent_liab = _find_value(latest, FIELD_KEYS["total_noncurrent_liab"])

    debt_ratio = round(total_liab / total_assets * 100, 2) if total_assets and total_liab else None
    equity_ratio = round(total_equity / total_assets * 100, 2) if total_assets and total_equity else None

    # 流动比率 & 速动比率
    current_ratio = round(total_current_assets / total_current_liab, 2) if total_current_assets and total_current_liab and total_current_liab != 0 else None
    quick_assets = (total_current_assets or 0) - (inventory or 0)
    quick_ratio = round(quick_assets / total_current_liab, 2) if total_current_liab and total_current_liab != 0 and total_current_assets else None

    # 有息负债
    interest_bearing_debt = (short_loan or 0) + (long_loan or 0) + (bonds or 0)

    # 净现金（货币资金+交易性金融资产-有息负债）
    liquid_assets = (cash or 0) + (trading_assets or 0)
    net_cash = liquid_assets - interest_bearing_debt

    prev_assets = _find_value(prev, FIELD_KEYS["total_assets"]) if prev else None

    analysis["metrics"] = {
        "总资产": {"value": total_assets, "formatted": _fmt_val(total_assets), "yoy": _yoy_change(total_assets, prev_assets)},
        "总负债": {"value": total_liab, "formatted": _fmt_val(total_liab)},
        "股东权益": {"value": total_equity, "formatted": _fmt_val(total_equity)},
        "资产负债率": {"value": debt_ratio, "formatted": (f"{debt_ratio:.2f}", "%") if debt_ratio is not None else ("-", "")},
        "流动比率": {"value": current_ratio, "formatted": (f"{current_ratio:.2f}", "倍") if current_ratio is not None else ("-", "")},
        "速动比率": {"value": quick_ratio, "formatted": (f"{quick_ratio:.2f}", "倍") if quick_ratio is not None else ("-", "")},
        "货币资金": {"value": cash, "formatted": _fmt_val(cash)},
        "交易性金融资产": {"value": trading_assets, "formatted": _fmt_val(trading_assets)},
        "应收账款": {"value": recv, "formatted": _fmt_val(recv)},
        "存货": {"value": inventory, "formatted": _fmt_val(inventory)},
        "合同资产": {"value": contract_assets, "formatted": _fmt_val(contract_assets)},
        "固定资产": {"value": fixed, "formatted": _fmt_val(fixed)},
        "在建工程": {"value": cip, "formatted": _fmt_val(cip)},
        "无形资产": {"value": intangible, "formatted": _fmt_val(intangible)},
        "商誉": {"value": goodwill, "formatted": _fmt_val(goodwill)},
        "长期股权投资": {"value": lt_invest, "formatted": _fmt_val(lt_invest)},
        "短期借款": {"value": short_loan, "formatted": _fmt_val(short_loan)},
        "应付账款": {"value": accounts_payable, "formatted": _fmt_val(accounts_payable)},
        "合同负债": {"value": contract_liab, "formatted": _fmt_val(contract_liab)},
        "长期借款": {"value": long_loan, "formatted": _fmt_val(long_loan)},
        "应付债券": {"value": bonds, "formatted": _fmt_val(bonds)},
        "有息负债合计": {"value": interest_bearing_debt, "formatted": _fmt_val(interest_bearing_debt)},
        "净现金": {"value": net_cash, "formatted": _fmt_val(net_cash),
                  "interpretation": "正值表示公司手持现金超过有息负债，财务安全"},
    }

    # 资产结构
    if total_assets and total_assets > 0:
        structure = {}
        for label, val in [("货币资金", cash), ("应收账款", recv), ("存货", inventory),
                           ("固定资产", fixed), ("商誉", goodwill), ("在建工程", cip),
                           ("无形资产", intangible), ("长期股权投资", lt_invest)]:
            if val is not None:
                structure[label] = round(val / total_assets * 100, 2)
        analysis["structure"] = structure

    # 多期趋势
    trend_data = []
    for r in records:
        ta = _find_value(r, FIELD_KEYS["total_assets"])
        tl = _find_value(r, FIELD_KEYS["total_liab"])
        te = _find_value(r, FIELD_KEYS["total_equity"])
        date = r.get("REPORT_DATE", "")[:10]
        dr = round(tl / ta * 100, 2) if ta and tl else None
        trend_data.append({"period": date, "total_assets": ta, "total_liab": tl, "total_equity": te, "debt_ratio": dr})
    analysis["trends"] = trend_data
    analysis["status"] = "success"
    return analysis


def analyze_cashflow(records):
    if not records:
        return {"status": "no_data"}

    latest = records[0]
    prev = records[1] if len(records) >= 2 else None

    analysis = {"report_type": "cashflow", "period_count": len(records), "metrics": {}}

    op_cf = _find_value(latest, FIELD_KEYS["operating_cf"])
    inv_cf = _find_value(latest, FIELD_KEYS["investing_cf"])
    fin_cf = _find_value(latest, FIELD_KEYS["financing_cf"])
    cash_inc = _find_value(latest, FIELD_KEYS["cash_increase"])
    cash_from_sales = _find_value(latest, FIELD_KEYS["cash_received_sales"])
    cash_paid_goods = _find_value(latest, FIELD_KEYS["cash_paid_goods"])
    cash_paid_emp = _find_value(latest, FIELD_KEYS["cash_paid_employees"])
    capex = _find_value(latest, FIELD_KEYS["capex"])
    cash_paid_dividend = _find_value(latest, FIELD_KEYS["cash_paid_dividend"])
    cash_received_borrow = _find_value(latest, FIELD_KEYS["cash_received_borrow"])
    cash_paid_debt = _find_value(latest, FIELD_KEYS["cash_paid_debt"])

    prev_op_cf = _find_value(prev, FIELD_KEYS["operating_cf"]) if prev else None

    analysis["metrics"] = {
        "经营活动现金流净额": {"value": op_cf, "formatted": _fmt_val(op_cf), "yoy": _yoy_change(op_cf, prev_op_cf)},
        "投资活动现金流净额": {"value": inv_cf, "formatted": _fmt_val(inv_cf)},
        "筹资活动现金流净额": {"value": fin_cf, "formatted": _fmt_val(fin_cf)},
        "现金净增加额": {"value": cash_inc, "formatted": _fmt_val(cash_inc)},
        "销售商品收到现金": {"value": cash_from_sales, "formatted": _fmt_val(cash_from_sales)},
        "购买商品支付现金": {"value": cash_paid_goods, "formatted": _fmt_val(cash_paid_goods)},
        "支付职工薪酬": {"value": cash_paid_emp, "formatted": _fmt_val(cash_paid_emp)},
        "购建固定资产支付现金": {"value": capex, "formatted": _fmt_val(capex)},
        "分配股利支付现金": {"value": cash_paid_dividend, "formatted": _fmt_val(cash_paid_dividend)},
        "取得借款收到现金": {"value": cash_received_borrow, "formatted": _fmt_val(cash_received_borrow)},
        "偿还债务支付现金": {"value": cash_paid_debt, "formatted": _fmt_val(cash_paid_debt)},
    }

    # 现金流类型判断（经典八种类型）
    cf_type = ""
    if op_cf is not None and inv_cf is not None and fin_cf is not None:
        signs = (op_cf >= 0, inv_cf >= 0, fin_cf >= 0)
        cf_type_map = {
            (True, True, True): "全面流入型（罕见，需验证）",
            (True, True, False): "成熟期回馈型（经营+投资流入，偿还债务/分红）",
            (True, False, True): "扩张期融资型（经营流入+融资补充，积极投资）",
            (True, False, False): "稳健经营型（经营造血覆盖投资和偿债）",
            (False, True, True): "困境转型型（经营亏损，变卖资产+融资续命）",
            (False, True, False): "收缩型（经营和融资均流出，靠变卖资产）",
            (False, False, True): "激进扩张型（经营亏损仍大举投资，高度依赖融资）",
            (False, False, False): "全面流出型（高风险，现金持续消耗）",
        }
        cf_type = cf_type_map.get(signs, "")

    analysis["cashflow_type"] = cf_type

    # 收现比（销售收现/营收）
    # 这里只记录原始值，收现比在衍生指标中和营收一起算

    # 多期趋势
    trend_data = []
    for r in records:
        o = _find_value(r, FIELD_KEYS["operating_cf"])
        i = _find_value(r, FIELD_KEYS["investing_cf"])
        f = _find_value(r, FIELD_KEYS["financing_cf"])
        date = r.get("REPORT_DATE", "")[:10]
        trend_data.append({"period": date, "operating_cf": o, "investing_cf": i, "financing_cf": f})
    analysis["trends"] = trend_data
    analysis["status"] = "success"
    return analysis


# ============================================================
# 衍生指标
# ============================================================

def calculate_derived_metrics(income_a, balance_a, cashflow_a):
    derived = {}

    net_profit = income_a.get("metrics", {}).get("净利润", {}).get("value") if income_a.get("status") == "success" else None
    revenue = income_a.get("metrics", {}).get("营业收入", {}).get("value") if income_a.get("status") == "success" else None
    cost = income_a.get("metrics", {}).get("营业成本", {}).get("value") if income_a.get("status") == "success" else None
    total_equity = balance_a.get("metrics", {}).get("股东权益", {}).get("value") if balance_a.get("status") == "success" else None
    total_assets = balance_a.get("metrics", {}).get("总资产", {}).get("value") if balance_a.get("status") == "success" else None
    recv = balance_a.get("metrics", {}).get("应收账款", {}).get("value") if balance_a.get("status") == "success" else None
    inventory_val = balance_a.get("metrics", {}).get("存货", {}).get("value") if balance_a.get("status") == "success" else None
    op_cf = cashflow_a.get("metrics", {}).get("经营活动现金流净额", {}).get("value") if cashflow_a.get("status") == "success" else None
    cash_from_sales = cashflow_a.get("metrics", {}).get("销售商品收到现金", {}).get("value") if cashflow_a.get("status") == "success" else None

    if net_profit and total_equity and total_equity != 0:
        derived["ROE"] = round(net_profit / total_equity * 100, 2)
    if net_profit and total_assets and total_assets != 0:
        derived["ROA"] = round(net_profit / total_assets * 100, 2)
    if revenue and total_assets and total_assets != 0:
        derived["总资产周转率"] = round(revenue / total_assets, 4)
    if revenue and recv and recv != 0:
        derived["应收账款周转率"] = round(revenue / recv, 2)
        derived["应收账款周转天数"] = round(365 / (revenue / recv), 1)
    if cost and inventory_val and inventory_val != 0:
        derived["存货周转率"] = round(cost / inventory_val, 2)
        derived["存货周转天数"] = round(365 / (cost / inventory_val), 1)
    if op_cf is not None and net_profit and net_profit != 0:
        derived["经营现金流/净利润"] = round(op_cf / net_profit, 2)
    if cash_from_sales and revenue and revenue != 0:
        derived["收现比"] = round(cash_from_sales / revenue, 2)

    # DuPont 分析
    if all(v is not None for v in [net_profit, revenue, total_assets, total_equity]) and all(v != 0 for v in [revenue, total_assets, total_equity]):
        derived["杜邦_净利率"] = round(net_profit / revenue * 100, 2)
        derived["杜邦_资产周转率"] = round(revenue / total_assets, 4)
        derived["杜邦_权益乘数"] = round(total_assets / total_equity, 2)

    return derived


# ============================================================
# 风险预警
# ============================================================

def generate_risk_alerts(income_a, balance_a, cashflow_a, derived):
    alerts = []     # 高风险
    warnings = []   # 中度关注
    positives = []  # 积极信号

    if income_a.get("status") == "success":
        m = income_a["metrics"]
        profit_yoy = m.get("净利润", {}).get("yoy")
        revenue_yoy = m.get("营业收入", {}).get("yoy")
        gross_margin = m.get("毛利率", {}).get("value")
        net_margin = m.get("净利率", {}).get("value")
        nr_ratio = m.get("非经常损益占比", {}).get("value")

        if profit_yoy is not None and profit_yoy < -30:
            alerts.append(f"净利润同比大幅下滑 {profit_yoy}%")
        elif profit_yoy is not None and profit_yoy < -10:
            warnings.append(f"净利润同比下降 {profit_yoy}%")
        elif profit_yoy is not None and profit_yoy > 30:
            positives.append(f"净利润同比增长 {profit_yoy}%")

        if revenue_yoy is not None and revenue_yoy < -15:
            alerts.append(f"营收同比下降 {revenue_yoy}%，业务收缩明显")
        elif revenue_yoy is not None and revenue_yoy > 20:
            positives.append(f"营收同比增长 {revenue_yoy}%")

        if gross_margin is not None and gross_margin < 15:
            warnings.append(f"毛利率仅 {gross_margin}%，盈利空间薄")
        elif gross_margin is not None and gross_margin > 50:
            positives.append(f"毛利率 {gross_margin}%，定价能力强")

        if net_margin is not None and net_margin < 0:
            alerts.append(f"净利率 {net_margin}%，处于亏损状态")

        # 增收不增利
        if revenue_yoy is not None and profit_yoy is not None:
            if revenue_yoy > 10 and profit_yoy < -5:
                alerts.append(f"增收不增利：营收增长{revenue_yoy}%但净利润下降{profit_yoy}%")

        if nr_ratio is not None and abs(nr_ratio) > 30:
            warnings.append(f"非经常损益占净利润{nr_ratio}%，利润质量需关注")

    if balance_a.get("status") == "success":
        m = balance_a["metrics"]
        debt_ratio = m.get("资产负债率", {}).get("value")
        goodwill = m.get("商誉", {}).get("value")
        total_assets = m.get("总资产", {}).get("value")
        net_cash = m.get("净现金", {}).get("value")
        current_ratio = m.get("流动比率", {}).get("value")

        if debt_ratio is not None and debt_ratio > 75:
            alerts.append(f"资产负债率 {debt_ratio}%，杠杆过高")
        elif debt_ratio is not None and debt_ratio > 60:
            warnings.append(f"资产负债率 {debt_ratio}%，杠杆中高水平")
        elif debt_ratio is not None and debt_ratio < 30:
            positives.append(f"资产负债率 {debt_ratio}%，财务结构稳健")

        if goodwill and total_assets and total_assets > 0:
            gw_ratio = goodwill / total_assets * 100
            if gw_ratio > 15:
                alerts.append(f"商誉占总资产 {gw_ratio:.1f}%，减值风险高")
            elif gw_ratio > 5:
                warnings.append(f"商誉占总资产 {gw_ratio:.1f}%")

        if net_cash is not None and net_cash < 0:
            warnings.append(f"净现金为负（{_fmt_val(net_cash)[0]}{_fmt_val(net_cash)[1]}），有息负债超过现金储备")
        elif net_cash is not None and net_cash > 0:
            positives.append(f"净现金为正（{_fmt_val(net_cash)[0]}{_fmt_val(net_cash)[1]}），现金充裕")

        if current_ratio is not None and current_ratio < 1:
            alerts.append(f"流动比率 {current_ratio}，短期偿债压力大")

    if cashflow_a.get("status") == "success":
        m = cashflow_a["metrics"]
        op_cf = m.get("经营活动现金流净额", {}).get("value")
        if op_cf is not None and op_cf < 0:
            alerts.append("经营活动现金流为负，盈利质量存疑")
        elif op_cf is not None and op_cf > 0:
            positives.append("经营活动产生正现金流")

        if cashflow_a.get("cashflow_type"):
            # 记录现金流类型
            pass

    roe = derived.get("ROE")
    if roe is not None:
        if roe > 20:
            positives.append(f"ROE {roe}%，资本回报优秀")
        elif roe < 5 and roe >= 0:
            warnings.append(f"ROE {roe}%，资本回报偏低")
        elif roe < 0:
            alerts.append(f"ROE {roe}%，股东权益负回报")

    cf_ratio = derived.get("经营现金流/净利润")
    if cf_ratio is not None and 0 < cf_ratio < 0.5:
        warnings.append(f"经营现金流/净利润仅 {cf_ratio}，盈利含金量偏低")

    cash_collect = derived.get("收现比")
    if cash_collect is not None and cash_collect < 0.8:
        warnings.append(f"收现比 {cash_collect}，收入转化现金能力偏弱")
    elif cash_collect is not None and cash_collect > 1.1:
        positives.append(f"收现比 {cash_collect}，收入变现能力强")

    return {"alerts": alerts, "warnings": warnings, "positives": positives}


# ============================================================
# 行业对比
# ============================================================

def _normalize_industry_name(name):
    """标准化行业名称，去掉级别后缀（如 Ⅱ、Ⅲ、II、III 等）用于模糊匹配。"""
    import unicodedata
    cleaned = re.sub(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹⅺⅻ]', '', name)
    cleaned = re.sub(r'\b[IVX]+\b', '', cleaned)
    return cleaned.strip()


def get_industry_data(industry_name, code):
    industry_info = {
        "industry_name": industry_name,
        "sector_pe": None,
        "sector_change_pct": None,
        "peer_companies": [],
        "fund_flow": None,
    }
    if not industry_name:
        return industry_info

    base_name = _normalize_industry_name(industry_name)

    # 行业板块行情（需分页，每页最多100条）
    try:
        for page in range(1, 6):  # 最多5页，覆盖500个行业
            url = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"pn={page}&pz=100&fid=f3&po=1&np=1&fltt=2&invt=2"
                f"&fs=m:90+t:2"
                f"&fields=f2,f3,f4,f12,f14,f62,f184,f20,f128,f136,f115"
            )
            data = http_get_json(url)
            if not (data and data.get("data") and data["data"].get("diff")):
                break
            for item in data["data"]["diff"]:
                name = item.get("f14", "")
                item_base = _normalize_industry_name(name)
                # 匹配规则：精确匹配 > 基础名精确匹配 > 基础名起始匹配
                if (industry_name == name or
                        base_name == item_base or
                        (len(base_name) >= 2 and item_base.startswith(base_name)) or
                        (len(item_base) >= 2 and base_name.startswith(item_base))):
                    industry_info["sector_pe"] = _safe_float(item.get("f115"))
                    industry_info["sector_change_pct"] = _safe_float(item.get("f3"))
                    industry_info["sector_cap"] = _safe_float(item.get("f20"))
                    industry_info["sector_net_inflow"] = _safe_float(item.get("f62"))
                    industry_info["sector_code"] = item.get("f12", "")
                    break
            if industry_info.get("sector_code"):
                break
    except Exception as e:
        print(f"  [警告] 获取行业板块数据失败: {e}", file=sys.stderr)

    # 同行公司
    sector_code = industry_info.get("sector_code", "")
    if sector_code:
        try:
            url2 = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"pn=1&pz=15&fid=f20&po=1&np=1&fltt=2&invt=2"
                f"&fs=b:{sector_code}+f:!50"
                f"&fields=f2,f3,f4,f9,f12,f14,f20,f23,f37,f115,f152"
            )
            data2 = http_get_json(url2)
            if data2 and data2.get("data") and data2["data"].get("diff"):
                peers = []
                for item in data2["data"]["diff"]:
                    peers.append({
                        "code": item.get("f12", ""),
                        "name": item.get("f14", ""),
                        "price": _safe_float(item.get("f2")),
                        "change_pct": _safe_float(item.get("f3")),
                        "pe_ttm": _safe_float(item.get("f9")),
                        "pb": _safe_float(item.get("f23")),
                        "roe": _safe_float(item.get("f37")),
                        "market_cap": _safe_float(item.get("f20")),
                    })
                industry_info["peer_companies"] = peers
        except Exception as e:
            print(f"  [警告] 获取同行数据失败: {e}", file=sys.stderr)

    # 资金流向
    try:
        url3 = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            "pn=1&pz=500&fid=f62&po=1&np=1&fltt=2&invt=2"
            "&fs=m:90+t:2"
            "&fields=f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
        )
        data3 = http_get_json(url3)
        if data3 and data3.get("data") and data3["data"].get("diff"):
            for item in data3["data"]["diff"]:
                name = item.get("f14", "")
                item_base = _normalize_industry_name(name)
                if (industry_name in name or name in industry_name or
                        base_name in item_base or item_base in base_name):
                    industry_info["fund_flow"] = {
                        "main_net_inflow": _safe_float(item.get("f62")),
                        "main_net_pct": _safe_float(item.get("f184")),
                        "super_large_inflow": _safe_float(item.get("f66")),
                        "large_inflow": _safe_float(item.get("f72")),
                        "medium_inflow": _safe_float(item.get("f78")),
                        "small_inflow": _safe_float(item.get("f84")),
                    }
                    break
    except Exception as e:
        print(f"  [警告] 获取资金流向失败: {e}", file=sys.stderr)

    return industry_info


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="earnings-insight A股数据获取模块")
    parser.add_argument("--code", type=str, required=True, help="股票代码（6位数字）")
    parser.add_argument("--periods", type=int, default=8, help="获取历史期数（默认8）")
    parser.add_argument("--output-dir", type=str, default=".", help="输出目录")
    parser.add_argument('--mode', choices=['standalone', 'collaborative'], default='standalone',
                        help='运行模式：standalone=独立运行, collaborative=工作流协同')
    parser.add_argument('--data-dir', type=str, default=None,
                        help='Mode B: 数据目录路径 (data/{code}/)')

    args = parser.parse_args()
    code = args.code.strip()
    if not re.match(r"^\d{6}$", code):
        print(f"[错误] 无效的A股代码: {code}", file=sys.stderr)
        sys.exit(2)

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print("  earnings-insight 数据获取")
    print("=" * 60)
    print()

    # 1. 基本信息
    print("[1/5] 获取公司基本信息...")
    stock_info = get_stock_info(code)
    name = stock_info.get("name", "")
    industry = stock_info.get("industry", "")
    print(f"  公司: {name}  行业: {industry}")
    print()

    # 2. 财务数据
    print("[2/5] 获取财务报表数据...")
    financial_data = fetch_all_financial_data(code, args.periods)

    # 保存原始数据
    for rt, rd in financial_data.items():
        if rd.get("records"):
            json_path = os.path.join(output_dir, f"{code}_{rt}_raw.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(rd["records"], f, ensure_ascii=False, indent=2)
    print()

    # 3. 分析
    print("[3/5] 结构化分析...")
    income_records = financial_data.get("income", {}).get("records", [])
    balance_records = financial_data.get("balance", {}).get("records", [])
    cashflow_records = financial_data.get("cashflow", {}).get("records", [])

    income_a = analyze_income(income_records)
    balance_a = analyze_balance(balance_records)
    cashflow_a = analyze_cashflow(cashflow_records)
    derived = calculate_derived_metrics(income_a, balance_a, cashflow_a)
    risk_result = generate_risk_alerts(income_a, balance_a, cashflow_a, derived)
    print("  分析完成")
    print()

    # 4. 领先指标
    print("[4/5] 提取领先指标...")
    leading = extract_leading_indicators(balance_records, cashflow_records, income_records)
    leading_count = len([v for v in leading.values() if isinstance(v, dict) and v.get("value") is not None])
    print(f"  提取到 {leading_count} 个领先指标")
    print()

    # 5. 行业对比
    print("[5/5] 获取行业对比数据...")
    industry_info = get_industry_data(industry, code)
    peer_count = len(industry_info.get("peer_companies", []))
    print(f"  行业: {industry_info.get('industry_name', '未知')}, 同行: {peer_count} 家")
    print()

    # 判断 stock_info 的 status
    has_name = bool(stock_info.get("name"))
    has_price = stock_info.get("current_price") is not None
    if has_name and has_price:
        stock_info["status"] = "success"
    elif has_name or has_price:
        stock_info["status"] = "partial"
    else:
        stock_info["status"] = "failed"

    # 汇总输出
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output = {
        "meta": {
            # 契约字段
            "code": code,
            "name": name,
            "timestamp": now_ts,
            "market": "A股",
            "industry": industry,
            "data_periods": args.periods,
            # 向后兼容别名
            "stock_code": code,
            "stock_name": name,
            "analysis_time": now_ts,
        },
        "stock_info": stock_info,
        # 契约字段
        "income_data": income_a,
        "balance_data": balance_a,
        "cashflow_data": cashflow_a,
        # 向后兼容别名（指向同一对象）
        "income_analysis": income_a,
        "balance_analysis": balance_a,
        "cashflow_analysis": cashflow_a,
        "derived_metrics": derived,
        "leading_indicators": leading,
        "risk_assessment": risk_result,
        "industry_comparison": industry_info,
        "raw_financial_data": {
            "income_records": income_records,
            "balance_records": balance_records,
            "cashflow_records": cashflow_records,
        },
    }

    # collaborative 模式下的额外输出
    if args.mode == 'collaborative' and args.data_dir:
        # stock_info 单独保存到 market-data/
        market_data_dir = os.path.join(args.data_dir, 'market-data')
        os.makedirs(market_data_dir, exist_ok=True)
        stock_info_path = os.path.join(market_data_dir, 'stock_info.json')
        with open(stock_info_path, "w", encoding="utf-8") as f:
            json.dump(stock_info, f, ensure_ascii=False, indent=2)
        print(f"  [collaborative] stock_info 已保存: {stock_info_path}")

        # 完整输出保存到 earnings-analysis/
        earnings_dir = os.path.join(args.data_dir, 'earnings-analysis')
        os.makedirs(earnings_dir, exist_ok=True)
        output_dir = earnings_dir

    output_path = os.path.join(output_dir, f"{code}_insight_data.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    print("=" * 60)
    print(f"  数据已保存: {output_path}")
    print("=" * 60)

    has_data = any(financial_data.get(rt, {}).get("records") for rt in REPORT_TYPES)
    sys.exit(0 if has_data else 1)


if __name__ == "__main__":
    main()
