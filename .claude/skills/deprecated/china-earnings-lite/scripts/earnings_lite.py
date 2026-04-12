#!/usr/bin/env python3
"""
零依赖 A股财报综合分析工具。

通过东方财富、巨潮资讯等公开 API 获取财务数据，
下载财报 PDF，获取行业基本面，并生成综合分析报告。

仅需 Python 3.6+，核心功能零第三方依赖。

用法:
    python earnings_lite.py --code 300014                          # 完整分析（数据+行业+报告）
    python earnings_lite.py --code 300014 --mode data              # 仅获取财务数据
    python earnings_lite.py --code 300014 --mode download          # 仅下载财报PDF
    python earnings_lite.py --code 300014 --mode industry          # 仅获取行业分析
    python earnings_lite.py --code 300014 --mode report            # 数据+行业+综合报告
    python earnings_lite.py --code 300014 --year 2024              # 指定年份
    python earnings_lite.py --code 300014 --periods 8              # 最近8期数据
    python earnings_lite.py --code 300014 --output-dir ./reports   # 指定输出目录
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

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

# 东方财富报表类型映射
EASTMONEY_REPORT_MAP = {
    "income": "RPT_DMSK_FN_INCOME",
    "balance": "RPT_DMSK_FN_BALANCE",
    "cashflow": "RPT_DMSK_FN_CASHFLOW",
}

# 板块映射 — 用于巨潮资讯
CNINFO_PLATE_MAP = {
    "00": "szse", "30": "szse",
    "60": "sse", "68": "sse",
    "83": "bse", "43": "bse",
}


# ============================================================
# HTTP 工具函数
# ============================================================

def _create_ssl_context():
    """创建 SSL 上下文（跳过证书验证）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get_json(url, headers=None, timeout=FETCH_TIMEOUT):
    """GET 请求，返回 JSON。"""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, data, headers=None, timeout=FETCH_TIMEOUT):
    """POST 请求，返回 JSON。"""
    hdrs = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)
    encoded = urllib.parse.urlencode(data).encode("utf-8") if isinstance(data, dict) else data.encode("utf-8")
    req = urllib.request.Request(url, data=encoded, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_download(url, save_path, headers=None, timeout=120):
    """下载文件到本地。"""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        downloaded = 0
        with open(save_path, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  下载进度: {pct}% ({downloaded}/{total})", end="", flush=True)
        print()
    return os.path.getsize(save_path)


# ============================================================
# 股票基本信息
# ============================================================

def get_stock_info(code):
    """
    获取股票基本信息（名称、行业、市值等）。
    数据源: 东方财富。
    返回 dict。
    """
    url = (
        f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
        f"reportName=RPT_USF10_INFO_ORGINFO&columns=ALL&pageSize=1&pageNumber=1"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
    )
    info = {"code": code, "name": "", "industry": "", "market": ""}

    # 方法1: 个股信息页面
    try:
        # 用简单行情接口
        prefix = code[:2]
        if prefix in ("60", "68"):
            secid = f"1.{code}"
        elif prefix in ("00", "30"):
            secid = f"0.{code}"
        elif prefix in ("83", "43"):
            secid = f"0.{code}"
        else:
            secid = f"0.{code}"

        quote_url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f127,f116,f117,f162,f167,f164,f163"
        data = http_get_json(quote_url)
        if data and data.get("data"):
            d = data["data"]
            info["name"] = d.get("f58", "")
            info["industry"] = d.get("f127", "")
            info["market_cap"] = _safe_float(d.get("f116", 0))
            info["circulating_cap"] = _safe_float(d.get("f117", 0))
            info["pe_ttm"] = _safe_float(d.get("f162", 0))
            info["pe_static"] = _safe_float(d.get("f167", 0))
            info["pb"] = _safe_float(d.get("f163", 0))
            info["ps_ttm"] = _safe_float(d.get("f164", 0))
    except Exception as e:
        print(f"  [警告] 获取行情信息失败: {e}")

    return info


# ============================================================
# 模块一: 财务数据获取（东方财富 API）
# ============================================================

def _build_eastmoney_url(report_name, code, periods=4):
    """构建东方财富财务报表API请求URL。"""
    # 东方财富 data-web API
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


def _build_eastmoney_url_v2(report_type, code, periods=4):
    """备用东方财富API（datacenter.eastmoney.com）。"""
    # 另一组API端点
    api_map = {
        "income": {
            "url": "https://datacenter.eastmoney.com/securities/api/data/get",
            "report": "RPT_F10_FINANCE_GINCOME",
        },
        "balance": {
            "url": "https://datacenter.eastmoney.com/securities/api/data/get",
            "report": "RPT_F10_FINANCE_GBALANCE",
        },
        "cashflow": {
            "url": "https://datacenter.eastmoney.com/securities/api/data/get",
            "report": "RPT_F10_FINANCE_GCASHFLOW",
        },
    }
    conf = api_map.get(report_type)
    if not conf:
        return None
    params = {
        "type": conf["report"],
        "sty": "ALL",
        "ps": str(periods),
        "p": "1",
        "sr": "-1",
        "st": "REPORT_DATE",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    return f"{conf['url']}?{urllib.parse.urlencode(params)}"


def fetch_financial_data(code, report_type, periods=4):
    """
    获取财务报表数据。多级回退:
    1. 东方财富 datacenter-web API
    2. 东方财富 datacenter API (备用)
    返回 (records_list, source, error)
    """
    errors = []

    # 方法1: datacenter-web
    report_name = EASTMONEY_REPORT_MAP.get(report_type)
    if report_name:
        try:
            url = _build_eastmoney_url(report_name, code, periods)
            data = http_get_json(url)
            if data and data.get("result") and data["result"].get("data"):
                records = data["result"]["data"]
                return records, "eastmoney-web", None
            else:
                errors.append(f"eastmoney-web: 返回空数据")
        except Exception as e:
            errors.append(f"eastmoney-web: {e}")

    # 方法2: datacenter 备用
    try:
        url2 = _build_eastmoney_url_v2(report_type, code, periods)
        if url2:
            data2 = http_get_json(url2)
            if data2 and data2.get("result") and data2["result"].get("data"):
                records = data2["result"]["data"]
                return records, "eastmoney-v2", None
            else:
                errors.append(f"eastmoney-v2: 返回空数据")
    except Exception as e:
        errors.append(f"eastmoney-v2: {e}")

    return None, None, "; ".join(errors)


def fetch_all_financial_data(code, periods=4):
    """获取所有类型的财报数据。"""
    results = {}
    for rt in REPORT_TYPES:
        print(f"  [{rt}] {REPORT_NAMES[rt]}...", end=" ", flush=True)
        records, source, error = fetch_financial_data(code, rt, periods)
        if records:
            results[rt] = {"records": records, "source": source}
            print(f"成功 ({len(records)} 期, {source})")
        else:
            results[rt] = {"records": [], "source": None, "error": error}
            print(f"失败: {error}")
    return results


# ============================================================
# 模块二: 财报 PDF 下载（巨潮资讯）
# ============================================================

def cninfo_get_org_id(code):
    """获取巨潮资讯的 orgId。"""
    url = "http://www.cninfo.com.cn/new/information/topSearch/query"
    headers = {
        "Origin": "http://www.cninfo.com.cn",
        "Referer": "http://www.cninfo.com.cn/",
    }
    data = {"keyWord": code, "maxSecNum": "10", "maxListNum": "5"}
    result = http_post_json(url, data, headers=headers)
    if isinstance(result, list) and result:
        return result[0].get("orgId", ""), result[0].get("zwjc", "")
    return "", ""


def cninfo_query_reports(code, org_id, category, page_size=30):
    """查询巨潮资讯公告列表。"""
    plate = CNINFO_PLATE_MAP.get(code[:2], "szse")
    stock_val = f"{code},{org_id}" if org_id else f"{code},"
    headers = {
        "Origin": "http://www.cninfo.com.cn",
        "Referer": "http://www.cninfo.com.cn/",
    }
    params = {
        "pageNum": "1", "pageSize": str(page_size),
        "column": plate, "tabName": "fulltext",
        "plate": "", "stock": stock_val,
        "searchkey": "", "secid": "",
        "category": category,
        "trade": "", "seDate": "",
        "sortName": "", "sortType": "",
        "isHLtitle": "true",
    }
    result = http_post_json("http://www.cninfo.com.cn/new/hisAnnouncement/query", params, headers=headers)
    return result.get("announcements") or []


def cninfo_filter_main_report(announcements, year=None):
    """过滤出正式财报，排除摘要、修订等。"""
    skip_keywords = [
        "摘要", "英文", "修订", "补充", "更正", "取消", "延期",
        "审计报告", "内部控制", "社会责任", "可持续发展",
        "独立董事", "监事会", "董事会决议", "股东大会",
    ]
    results = []
    for ann in announcements:
        title = re.sub(r"<.*?>", "", ann.get("announcementTitle", ""))
        if any(kw in title for kw in skip_keywords):
            continue
        if year and str(year) not in title:
            continue
        results.append({
            "title": title,
            "adjunctUrl": ann.get("adjunctUrl", ""),
            "announcementTime": ann.get("announcementTime", 0),
        })
    return results


def download_report_pdf(code, output_dir, year=None, report_type="annual"):
    """
    从巨潮资讯下载财报 PDF。
    report_type: annual/half/q1/q3
    """
    category_map = {
        "annual": "category_ndbg_szsh",
        "half": "category_bndbg_szsh",
        "q1": "category_yjdbg_szsh",
        "q3": "category_sjdbg_szsh",
    }
    category = category_map.get(report_type, "category_ndbg_szsh")

    print(f"  查询巨潮资讯({report_type})...")
    org_id, sec_name = cninfo_get_org_id(code)
    if not org_id:
        return None, "获取 orgId 失败"

    announcements = cninfo_query_reports(code, org_id, category)
    reports = cninfo_filter_main_report(announcements, year=year)

    if not reports:
        return None, f"未找到{year or ''}{'年' if year else ''}{report_type}报告"

    report = reports[0]  # 取最新的
    adjunct_url = report["adjunctUrl"]
    if not adjunct_url:
        return None, "无下载链接"

    safe_title = re.sub(r'[<>:"/\\|?*\s]+', '_', report['title']).strip('_')[:80]
    filename = f"{code}_{safe_title}.pdf"
    save_path = os.path.join(output_dir, filename)

    if os.path.exists(save_path):
        print(f"  文件已存在: {filename}")
        return save_path, None

    url = "http://static.cninfo.com.cn/" + adjunct_url
    print(f"  下载: {report['title']}")
    try:
        size = http_download(url, save_path, headers={"Referer": "http://www.cninfo.com.cn/"})
        print(f"  已保存: {filename} ({size / 1024 / 1024:.1f} MB)")
        return save_path, None
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        return None, str(e)


# ============================================================
# 模块三: 行业基本面分析（东方财富行业数据）
# ============================================================

def get_industry_data(industry_name, code):
    """
    获取行业整体数据。
    1. 行业板块行情（PE、涨跌幅等）
    2. 行业内公司排名对比
    3. 行业资金流向
    """
    industry_info = {
        "industry_name": industry_name,
        "sector_pe": None,
        "sector_change_pct": None,
        "peer_companies": [],
        "fund_flow": None,
    }

    if not industry_name:
        return industry_info

    # 1. 获取行业板块列表和行情
    try:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            "pn=1&pz=100&fid=f3&po=1&np=1&fltt=2&invt=2"
            "&fs=m:90+t:2"  # 行业板块
            "&fields=f2,f3,f4,f12,f14,f62,f184,f20,f128,f136,f115"
        )
        data = http_get_json(url)
        if data and data.get("data") and data["data"].get("diff"):
            for item in data["data"]["diff"]:
                name = item.get("f14", "")
                if industry_name in name or name in industry_name:
                    industry_info["sector_pe"] = _safe_float(item.get("f115"))
                    industry_info["sector_change_pct"] = _safe_float(item.get("f3"))
                    industry_info["sector_turnover"] = _safe_float(item.get("f136"))
                    industry_info["sector_cap"] = _safe_float(item.get("f20"))
                    industry_info["sector_net_inflow"] = _safe_float(item.get("f62"))
                    industry_info["sector_code"] = item.get("f12", "")
                    break
    except Exception as e:
        print(f"  [警告] 获取行业板块数据失败: {e}")

    # 2. 获取行业内同行公司对比
    try:
        url2 = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn=1&pz=20&fid=f20&po=1&np=1&fltt=2&invt=2"
            f"&fs=b:BK0{industry_info.get('sector_code', '').replace('BK', '')}" if industry_info.get("sector_code") else
            "https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn=1&pz=20&fid=f20&po=1&np=1&fltt=2&invt=2&fs=m:0+t:6+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:81+s:2048+f:!2"
        )

        # 使用行业成分股接口
        sector_code = industry_info.get("sector_code", "")
        if sector_code:
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
                    peer = {
                        "code": item.get("f12", ""),
                        "name": item.get("f14", ""),
                        "price": _safe_float(item.get("f2")),
                        "change_pct": _safe_float(item.get("f3")),
                        "pe_ttm": _safe_float(item.get("f9")),
                        "pb": _safe_float(item.get("f23")),
                        "roe": _safe_float(item.get("f37")),
                        "market_cap": _safe_float(item.get("f20")),
                    }
                    peers.append(peer)
                industry_info["peer_companies"] = peers
    except Exception as e:
        print(f"  [警告] 获取同行公司数据失败: {e}")

    # 3. 行业资金流向
    try:
        url3 = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            "pn=1&pz=100&fid=f62&po=1&np=1&fltt=2&invt=2"
            "&fs=m:90+t:2"
            "&fields=f12,f14,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87"
        )
        data3 = http_get_json(url3)
        if data3 and data3.get("data") and data3["data"].get("diff"):
            for item in data3["data"]["diff"]:
                name = item.get("f14", "")
                if industry_name in name or name in industry_name:
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
        print(f"  [警告] 获取资金流向失败: {e}")

    return industry_info


# ============================================================
# 模块四: 综合分析引擎
# ============================================================

def _safe_float(val):
    """安全转换浮点数。"""
    if val is None:
        return None
    try:
        v = float(val)
        return v if v == v else None  # NaN check
    except (ValueError, TypeError):
        return None


def _fmt_val(value, unit="元"):
    """格式化大数值。"""
    if value is None:
        return "-", ""
    v = abs(float(value))
    sign = "-" if float(value) < 0 else ""
    if v >= 1e8:
        return f"{sign}{v / 1e8:.2f}", "亿元"
    elif v >= 1e4:
        return f"{sign}{v / 1e4:.2f}", "万元"
    else:
        return f"{sign}{v:.2f}", unit


def _yoy_change(current, previous):
    """计算同比变化率。"""
    c = _safe_float(current)
    p = _safe_float(previous)
    if c is None or p is None or p == 0:
        return None
    return round((c - p) / abs(p) * 100, 2)


def _find_value(record, keys):
    """在记录中查找字段值（尝试多个可能的键名）。"""
    if not record:
        return None
    for key in keys:
        val = record.get(key)
        if val is not None:
            return _safe_float(val)
    return None


# 东方财富 API 返回的标准字段名映射
FIELD_KEYS = {
    # 利润表
    "revenue": ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "营业总收入", "营业收入"],
    "operating_cost": ["OPERATE_COST", "OPERATE_EXPENSE", "TOTAL_OPERATE_COST", "营业总成本", "营业成本"],
    "gross_profit": ["OPERATE_PROFIT", "营业利润"],
    "net_profit": ["NETPROFIT", "NET_PROFIT", "PARENT_NETPROFIT", "净利润", "归属于母公司所有者的净利润"],
    "net_profit_parent": ["PARENT_NETPROFIT", "DEDUCT_PARENT_NETPROFIT",
                          "归属于母公司所有者的净利润", "归母净利润"],
    "operating_profit": ["OPERATE_PROFIT", "营业利润"],
    "total_profit": ["TOTAL_PROFIT", "利润总额"],
    "rd_expense": ["RESEARCH_EXPENSE", "研发费用"],
    "sales_expense": ["SALE_EXPENSE", "销售费用"],
    "admin_expense": ["MANAGE_EXPENSE", "管理费用"],
    "finance_expense": ["FINANCE_EXPENSE", "财务费用"],

    # 资产负债表
    "total_assets": ["TOTAL_ASSETS", "资产总计", "资产合计"],
    "total_liab": ["TOTAL_LIABILITIES", "负债合计", "负债总计"],
    "total_equity": ["TOTAL_EQUITY", "TOTAL_PARENT_EQUITY", "归属于母公司所有者权益合计",
                     "股东权益合计"],
    "cash_equiv": ["MONETARYFUNDS", "CURRENCY_FUNDS", "货币资金"],
    "accounts_recv": ["ACCOUNTS_RECE", "应收账款"],
    "inventory": ["INVENTORY", "存货"],
    "fixed_assets": ["FIXED_ASSET", "固定资产"],
    "goodwill": ["GOODWILL", "商誉"],
    "short_loan": ["SHORT_LOAN", "短期借款"],
    "long_loan": ["LONG_LOAN", "长期借款"],

    # 现金流量表
    "operating_cf": ["NETCASH_OPERATE", "经营活动产生的现金流量净额"],
    "investing_cf": ["NETCASH_INVEST", "投资活动产生的现金流量净额"],
    "financing_cf": ["NETCASH_FINANCE", "筹资活动产生的现金流量净额"],
    "cash_increase": ["CCE_ADD", "现金及现金等价物净增加额"],

    # 日期
    "report_date": ["REPORT_DATE", "REPORTDATE", "报告期"],
    "report_name": ["REPORT_DATE_NAME", "报告期名称"],
    "security_name": ["SECURITY_NAME_ABBR", "SECURITY_NAME", "股票简称"],
}


def analyze_income_detail(records):
    """深度利润表分析。"""
    if not records:
        return {"status": "no_data"}

    latest = records[0]
    prev = records[1] if len(records) >= 2 else None
    prev2 = records[3] if len(records) >= 4 else None  # 去年同期

    analysis = {"report_type": "income", "period_count": len(records), "metrics": {}, "trends": []}

    # 核心指标
    revenue = _find_value(latest, FIELD_KEYS["revenue"])
    cost = _find_value(latest, FIELD_KEYS["operating_cost"])
    net_profit = _find_value(latest, FIELD_KEYS["net_profit"])
    net_profit_parent = _find_value(latest, FIELD_KEYS["net_profit_parent"])
    op_profit = _find_value(latest, FIELD_KEYS["operating_profit"])
    total_profit = _find_value(latest, FIELD_KEYS["total_profit"])
    rd = _find_value(latest, FIELD_KEYS["rd_expense"])
    sales_exp = _find_value(latest, FIELD_KEYS["sales_expense"])
    admin_exp = _find_value(latest, FIELD_KEYS["admin_expense"])
    fin_exp = _find_value(latest, FIELD_KEYS["finance_expense"])

    # 毛利率、净利率
    gross_margin = round((revenue - cost) / revenue * 100, 2) if revenue and cost and revenue != 0 else None
    net_margin = round(net_profit / revenue * 100, 2) if revenue and net_profit and revenue != 0 else None
    rd_ratio = round(rd / revenue * 100, 2) if revenue and rd and revenue != 0 else None

    # 同比
    prev_revenue = _find_value(prev2 or prev, FIELD_KEYS["revenue"])
    prev_net_profit = _find_value(prev2 or prev, FIELD_KEYS["net_profit"])
    prev_cost = _find_value(prev2 or prev, FIELD_KEYS["operating_cost"])

    revenue_yoy = _yoy_change(revenue, prev_revenue)
    profit_yoy = _yoy_change(net_profit, prev_net_profit)

    prev_gross_margin = None
    if prev_revenue and prev_cost and prev_revenue != 0:
        prev_gross_margin = round((prev_revenue - prev_cost) / prev_revenue * 100, 2)

    analysis["metrics"] = {
        "营业收入": {"value": revenue, "formatted": _fmt_val(revenue), "yoy": revenue_yoy},
        "营业成本": {"value": cost, "formatted": _fmt_val(cost)},
        "净利润": {"value": net_profit, "formatted": _fmt_val(net_profit), "yoy": profit_yoy},
        "归母净利润": {"value": net_profit_parent, "formatted": _fmt_val(net_profit_parent)},
        "营业利润": {"value": op_profit, "formatted": _fmt_val(op_profit)},
        "毛利率": {"value": gross_margin, "formatted": (f"{gross_margin:.2f}", "%") if gross_margin else ("-", ""),
                   "prev": prev_gross_margin},
        "净利率": {"value": net_margin, "formatted": (f"{net_margin:.2f}", "%") if net_margin else ("-", "")},
        "研发费用": {"value": rd, "formatted": _fmt_val(rd)},
        "研发占比": {"value": rd_ratio, "formatted": (f"{rd_ratio:.2f}", "%") if rd_ratio else ("-", "")},
        "销售费用": {"value": sales_exp, "formatted": _fmt_val(sales_exp)},
        "管理费用": {"value": admin_exp, "formatted": _fmt_val(admin_exp)},
        "财务费用": {"value": fin_exp, "formatted": _fmt_val(fin_exp)},
    }

    # 趋势分析 — 多期数据
    if len(records) >= 2:
        trend_data = []
        for r in records:
            rev = _find_value(r, FIELD_KEYS["revenue"])
            np_ = _find_value(r, FIELD_KEYS["net_profit"])
            c = _find_value(r, FIELD_KEYS["operating_cost"])
            date = r.get("REPORT_DATE", r.get("REPORTDATE", ""))
            if date and len(date) > 10:
                date = date[:10]
            gm = round((rev - c) / rev * 100, 2) if rev and c and rev != 0 else None
            nm = round(np_ / rev * 100, 2) if rev and np_ and rev != 0 else None
            trend_data.append({
                "period": date,
                "revenue": rev,
                "net_profit": np_,
                "gross_margin": gm,
                "net_margin": nm,
            })
        analysis["trends"] = trend_data

    analysis["status"] = "success"
    return analysis


def analyze_balance_detail(records):
    """深度资产负债表分析。"""
    if not records:
        return {"status": "no_data"}

    latest = records[0]
    prev = records[1] if len(records) >= 2 else None

    analysis = {"report_type": "balance", "period_count": len(records), "metrics": {}, "structure": {}}

    total_assets = _find_value(latest, FIELD_KEYS["total_assets"])
    total_liab = _find_value(latest, FIELD_KEYS["total_liab"])
    total_equity = _find_value(latest, FIELD_KEYS["total_equity"])
    cash = _find_value(latest, FIELD_KEYS["cash_equiv"])
    recv = _find_value(latest, FIELD_KEYS["accounts_recv"])
    inventory = _find_value(latest, FIELD_KEYS["inventory"])
    fixed = _find_value(latest, FIELD_KEYS["fixed_assets"])
    goodwill = _find_value(latest, FIELD_KEYS["goodwill"])
    short_loan = _find_value(latest, FIELD_KEYS["short_loan"])
    long_loan = _find_value(latest, FIELD_KEYS["long_loan"])

    debt_ratio = round(total_liab / total_assets * 100, 2) if total_assets and total_liab else None
    equity_ratio = round(total_equity / total_assets * 100, 2) if total_assets and total_equity else None

    # 同比
    prev_assets = _find_value(prev, FIELD_KEYS["total_assets"]) if prev else None
    assets_yoy = _yoy_change(total_assets, prev_assets)

    analysis["metrics"] = {
        "总资产": {"value": total_assets, "formatted": _fmt_val(total_assets), "yoy": assets_yoy},
        "总负债": {"value": total_liab, "formatted": _fmt_val(total_liab)},
        "股东权益": {"value": total_equity, "formatted": _fmt_val(total_equity)},
        "资产负债率": {"value": debt_ratio, "formatted": (f"{debt_ratio:.2f}", "%") if debt_ratio else ("-", "")},
        "权益比率": {"value": equity_ratio, "formatted": (f"{equity_ratio:.2f}", "%") if equity_ratio else ("-", "")},
        "货币资金": {"value": cash, "formatted": _fmt_val(cash)},
        "应收账款": {"value": recv, "formatted": _fmt_val(recv)},
        "存货": {"value": inventory, "formatted": _fmt_val(inventory)},
        "固定资产": {"value": fixed, "formatted": _fmt_val(fixed)},
        "商誉": {"value": goodwill, "formatted": _fmt_val(goodwill)},
        "短期借款": {"value": short_loan, "formatted": _fmt_val(short_loan)},
        "长期借款": {"value": long_loan, "formatted": _fmt_val(long_loan)},
    }

    # 资产结构
    if total_assets and total_assets > 0:
        structure = {}
        for label, val in [("货币资金", cash), ("应收账款", recv), ("存货", inventory),
                           ("固定资产", fixed), ("商誉", goodwill)]:
            if val is not None:
                structure[label] = round(val / total_assets * 100, 2)
        analysis["structure"] = structure

    analysis["status"] = "success"
    return analysis


def analyze_cashflow_detail(records):
    """深度现金流量表分析。"""
    if not records:
        return {"status": "no_data"}

    latest = records[0]
    prev = records[1] if len(records) >= 2 else None

    analysis = {"report_type": "cashflow", "period_count": len(records), "metrics": {}}

    op_cf = _find_value(latest, FIELD_KEYS["operating_cf"])
    inv_cf = _find_value(latest, FIELD_KEYS["investing_cf"])
    fin_cf = _find_value(latest, FIELD_KEYS["financing_cf"])
    cash_inc = _find_value(latest, FIELD_KEYS["cash_increase"])

    prev_op_cf = _find_value(prev, FIELD_KEYS["operating_cf"]) if prev else None
    op_cf_yoy = _yoy_change(op_cf, prev_op_cf)

    analysis["metrics"] = {
        "经营活动现金流净额": {"value": op_cf, "formatted": _fmt_val(op_cf), "yoy": op_cf_yoy},
        "投资活动现金流净额": {"value": inv_cf, "formatted": _fmt_val(inv_cf)},
        "筹资活动现金流净额": {"value": fin_cf, "formatted": _fmt_val(fin_cf)},
        "现金净增加额": {"value": cash_inc, "formatted": _fmt_val(cash_inc)},
    }

    # 现金流质量判断
    cf_quality = []
    if op_cf is not None and op_cf > 0:
        cf_quality.append("经营活动产生正现金流，造血能力良好")
    elif op_cf is not None:
        cf_quality.append("经营活动现金流为负，需关注经营效率")
    if inv_cf is not None and inv_cf < 0:
        cf_quality.append("投资活动现金流出，处于扩张投入阶段")
    if fin_cf is not None and fin_cf > 0:
        cf_quality.append("筹资活动现金净流入，存在外部融资")
    elif fin_cf is not None and fin_cf < 0:
        cf_quality.append("筹资活动现金净流出，可能在偿还债务或分红")
    analysis["quality_notes"] = cf_quality

    analysis["status"] = "success"
    return analysis


def calculate_derived_metrics(income_analysis, balance_analysis, cashflow_analysis):
    """计算交叉指标（ROE、周转率等需要多表联合计算的指标）。"""
    derived = {}

    net_profit = None
    total_equity = None
    total_assets = None
    revenue = None
    op_cf = None

    if income_analysis.get("status") == "success":
        net_profit = income_analysis["metrics"].get("净利润", {}).get("value")
        revenue = income_analysis["metrics"].get("营业收入", {}).get("value")
    if balance_analysis.get("status") == "success":
        total_equity = balance_analysis["metrics"].get("股东权益", {}).get("value")
        total_assets = balance_analysis["metrics"].get("总资产", {}).get("value")
        recv = balance_analysis["metrics"].get("应收账款", {}).get("value")
        inventory_val = balance_analysis["metrics"].get("存货", {}).get("value")
    else:
        recv = None
        inventory_val = None
    if cashflow_analysis.get("status") == "success":
        op_cf = cashflow_analysis["metrics"].get("经营活动现金流净额", {}).get("value")

    # ROE
    if net_profit and total_equity and total_equity != 0:
        derived["ROE"] = round(net_profit / total_equity * 100, 2)

    # ROA
    if net_profit and total_assets and total_assets != 0:
        derived["ROA"] = round(net_profit / total_assets * 100, 2)

    # 总资产周转率
    if revenue and total_assets and total_assets != 0:
        derived["总资产周转率"] = round(revenue / total_assets, 4)

    # 应收账款周转率
    if revenue and recv and recv != 0:
        derived["应收账款周转率"] = round(revenue / recv, 2)

    # 存货周转率
    cost = None
    if income_analysis.get("status") == "success":
        cost = income_analysis["metrics"].get("营业成本", {}).get("value")
    if cost and inventory_val and inventory_val != 0:
        derived["存货周转率"] = round(cost / inventory_val, 2)

    # 现金流净利润比
    if op_cf is not None and net_profit and net_profit != 0:
        derived["经营现金流/净利润"] = round(op_cf / net_profit, 2)

    return derived


def generate_risk_alerts(income_a, balance_a, cashflow_a, derived):
    """生成风险提示。"""
    alerts = []
    warnings = []
    positives = []

    # 利润表风险
    if income_a.get("status") == "success":
        m = income_a["metrics"]
        profit_yoy = m.get("净利润", {}).get("yoy")
        revenue_yoy = m.get("营业收入", {}).get("yoy")
        gross_margin = m.get("毛利率", {}).get("value")
        net_margin = m.get("净利率", {}).get("value")

        if profit_yoy is not None and profit_yoy < -30:
            alerts.append(f"净利润同比大幅下滑 {profit_yoy}%，盈利能力严重恶化")
        elif profit_yoy is not None and profit_yoy < -10:
            warnings.append(f"净利润同比下降 {profit_yoy}%，需关注盈利持续性")
        elif profit_yoy is not None and profit_yoy > 30:
            positives.append(f"净利润同比增长 {profit_yoy}%，盈利高速增长")

        if revenue_yoy is not None and revenue_yoy < -15:
            alerts.append(f"营收同比下降 {revenue_yoy}%，业务收缩明显")
        elif revenue_yoy is not None and revenue_yoy > 20:
            positives.append(f"营收同比增长 {revenue_yoy}%，业务扩张态势良好")

        if gross_margin is not None and gross_margin < 15:
            warnings.append(f"毛利率仅 {gross_margin}%，盈利空间较薄")
        elif gross_margin is not None and gross_margin > 50:
            positives.append(f"毛利率 {gross_margin}%，具有较强定价能力")

        if net_margin is not None and net_margin < 0:
            alerts.append(f"净利率为 {net_margin}%，处于亏损状态")

    # 资产负债表风险
    if balance_a.get("status") == "success":
        m = balance_a["metrics"]
        debt_ratio = m.get("资产负债率", {}).get("value")
        goodwill = m.get("商誉", {}).get("value")
        total_assets = m.get("总资产", {}).get("value")

        if debt_ratio is not None and debt_ratio > 75:
            alerts.append(f"资产负债率 {debt_ratio}%，杠杆水平较高，偿债压力大")
        elif debt_ratio is not None and debt_ratio > 60:
            warnings.append(f"资产负债率 {debt_ratio}%，杠杆处于中高水平")
        elif debt_ratio is not None and debt_ratio < 30:
            positives.append(f"资产负债率仅 {debt_ratio}%，财务结构稳健")

        if goodwill and total_assets and total_assets > 0:
            goodwill_ratio = goodwill / total_assets * 100
            if goodwill_ratio > 15:
                alerts.append(f"商誉占总资产 {goodwill_ratio:.1f}%，存在减值风险")
            elif goodwill_ratio > 5:
                warnings.append(f"商誉占总资产 {goodwill_ratio:.1f}%，需关注被收购资产质量")

    # 现金流风险
    if cashflow_a.get("status") == "success":
        m = cashflow_a["metrics"]
        op_cf = m.get("经营活动现金流净额", {}).get("value")
        if op_cf is not None and op_cf < 0:
            alerts.append("经营活动现金流为负，盈利质量存疑")
        elif op_cf is not None and op_cf > 0:
            positives.append("经营活动产生正现金流，经营质量良好")

    # 交叉指标
    roe = derived.get("ROE")
    if roe is not None:
        if roe > 20:
            positives.append(f"ROE {roe}%，资本回报优秀")
        elif roe > 10:
            positives.append(f"ROE {roe}%，资本运用效率良好")
        elif roe < 5:
            warnings.append(f"ROE 仅 {roe}%，资本回报偏低")

    cf_ratio = derived.get("经营现金流/净利润")
    if cf_ratio is not None and cf_ratio < 0.5:
        warnings.append(f"经营现金流/净利润仅 {cf_ratio}，盈利含金量偏低")

    return {"alerts": alerts, "warnings": warnings, "positives": positives}


# ============================================================
# 模块五: 报告生成
# ============================================================

def generate_comprehensive_report(code, stock_info, financial_data, income_a, balance_a,
                                  cashflow_a, derived, risk_result, industry_info,
                                  pdf_paths, year=None):
    """生成详细的综合分析报告（Markdown）。"""

    name = stock_info.get("name", "")
    industry = stock_info.get("industry", "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append(f"# {code} {name} 财务综合分析报告")
    lines.append("")
    lines.append(f"> 生成时间: {now}")
    if year:
        lines.append(f"> 分析年份: {year} 年")
    lines.append(f"> 数据来源: 东方财富公开API、巨潮资讯")
    lines.append("")

    # ---- 公司概况 ----
    lines.append("## 一、公司概况")
    lines.append("")
    lines.append(f"| 项目 | 内容 |")
    lines.append(f"|------|------|")
    lines.append(f"| 股票代码 | {code} |")
    lines.append(f"| 公司简称 | {name} |")
    lines.append(f"| 所属行业 | {industry or '未知'} |")

    mc = stock_info.get("market_cap")
    if mc:
        mc_fmt, mc_unit = _fmt_val(mc)
        lines.append(f"| 总市值 | {mc_fmt} {mc_unit} |")
    cc = stock_info.get("circulating_cap")
    if cc:
        cc_fmt, cc_unit = _fmt_val(cc)
        lines.append(f"| 流通市值 | {cc_fmt} {cc_unit} |")

    pe = stock_info.get("pe_ttm")
    if pe:
        lines.append(f"| 市盈率(TTM) | {pe:.2f} |")
    pb = stock_info.get("pb")
    if pb:
        lines.append(f"| 市净率 | {pb:.2f} |")
    lines.append("")

    # ---- 关键财务指标总览 ----
    lines.append("## 二、关键财务指标总览")
    lines.append("")
    lines.append("| 指标 | 最新值 | 同比变化 | 评价 |")
    lines.append("|------|--------|----------|------|")

    def _add_metric_row(label, metrics_dict, key):
        m = metrics_dict.get(key, {})
        val = m.get("formatted", ("-", ""))
        yoy = m.get("yoy")
        yoy_str = f"{'+' if yoy and yoy >= 0 else ''}{yoy}%" if yoy is not None else "-"
        evaluation = ""
        if yoy is not None:
            if yoy > 20:
                evaluation = "高增长"
            elif yoy > 0:
                evaluation = "正增长"
            elif yoy > -10:
                evaluation = "小幅下降"
            else:
                evaluation = "显著下降"
        lines.append(f"| {label} | {val[0]} {val[1]} | {yoy_str} | {evaluation} |")

    if income_a.get("status") == "success":
        m = income_a["metrics"]
        _add_metric_row("营业收入", m, "营业收入")
        _add_metric_row("净利润", m, "净利润")
        # 毛利率和净利率特殊处理
        gm = m.get("毛利率", {})
        gm_val = gm.get("formatted", ("-", ""))
        gm_prev = gm.get("prev")
        gm_change = ""
        if gm.get("value") is not None and gm_prev is not None:
            diff = round(gm["value"] - gm_prev, 2)
            gm_change = f"{'+' if diff >= 0 else ''}{diff}pp"
        lines.append(f"| 毛利率 | {gm_val[0]}{gm_val[1]} | {gm_change or '-'} | |")

        nm = m.get("净利率", {})
        nm_val = nm.get("formatted", ("-", ""))
        lines.append(f"| 净利率 | {nm_val[0]}{nm_val[1]} | - | |")

    if balance_a.get("status") == "success":
        m = balance_a["metrics"]
        _add_metric_row("总资产", m, "总资产")
        dr = m.get("资产负债率", {})
        dr_val = dr.get("formatted", ("-", ""))
        lines.append(f"| 资产负债率 | {dr_val[0]}{dr_val[1]} | - | |")

    if cashflow_a.get("status") == "success":
        m = cashflow_a["metrics"]
        _add_metric_row("经营现金流净额", m, "经营活动现金流净额")

    # 衍生指标
    if derived:
        lines.append("")
        lines.append("### 核心效率指标")
        lines.append("")
        lines.append("| 指标 | 值 | 说明 |")
        lines.append("|------|-----|------|")
        desc = {
            "ROE": "净资产收益率，衡量股东资本回报",
            "ROA": "总资产收益率，衡量资产利用效率",
            "总资产周转率": "资产运营效率，越高越好",
            "应收账款周转率": "应收账款回收效率",
            "存货周转率": "存货流转效率",
            "经营现金流/净利润": "盈利含金量，>1为佳",
        }
        for k, v in derived.items():
            unit = "%" if k in ("ROE", "ROA") else ("次" if "周转" in k else "倍" if "/" in k else "")
            lines.append(f"| {k} | {v}{unit} | {desc.get(k, '')} |")
    lines.append("")

    # ---- 利润表深度分析 ----
    lines.append("## 三、利润表分析")
    lines.append("")
    if income_a.get("status") == "success":
        m = income_a["metrics"]
        lines.append("### 3.1 收入与利润")
        lines.append("")
        for label in ["营业收入", "营业成本", "营业利润", "净利润", "归母净利润"]:
            metric = m.get(label, {})
            val = metric.get("formatted", ("-", ""))
            yoy = metric.get("yoy")
            yoy_text = f"，同比{'+' if yoy >= 0 else ''}{yoy}%" if yoy is not None else ""
            lines.append(f"- **{label}**: {val[0]} {val[1]}{yoy_text}")
        lines.append("")

        lines.append("### 3.2 盈利能力")
        lines.append("")
        for label in ["毛利率", "净利率"]:
            metric = m.get(label, {})
            val = metric.get("formatted", ("-", ""))
            lines.append(f"- **{label}**: {val[0]}{val[1]}")
        lines.append("")

        lines.append("### 3.3 费用结构")
        lines.append("")
        lines.append("| 费用类型 | 金额 | 营收占比 |")
        lines.append("|----------|------|----------|")
        revenue_val = m.get("营业收入", {}).get("value")
        for label in ["研发费用", "销售费用", "管理费用", "财务费用"]:
            metric = m.get(label, {})
            val = metric.get("formatted", ("-", ""))
            raw = metric.get("value")
            ratio = f"{raw / revenue_val * 100:.2f}%" if raw and revenue_val and revenue_val != 0 else "-"
            lines.append(f"| {label} | {val[0]} {val[1]} | {ratio} |")
        lines.append("")

        # 趋势
        if income_a.get("trends"):
            lines.append("### 3.4 历史趋势")
            lines.append("")
            lines.append("| 报告期 | 营业收入 | 净利润 | 毛利率 | 净利率 |")
            lines.append("|--------|----------|--------|--------|--------|")
            for t in income_a["trends"]:
                rev_f = _fmt_val(t.get("revenue"))
                np_f = _fmt_val(t.get("net_profit"))
                gm = f"{t['gross_margin']:.2f}%" if t.get("gross_margin") is not None else "-"
                nm = f"{t['net_margin']:.2f}%" if t.get("net_margin") is not None else "-"
                lines.append(f"| {t.get('period', '-')} | {rev_f[0]} {rev_f[1]} | {np_f[0]} {np_f[1]} | {gm} | {nm} |")
            lines.append("")
    else:
        lines.append("*利润表数据获取失败*")
        lines.append("")

    # ---- 资产负债表分析 ----
    lines.append("## 四、资产负债表分析")
    lines.append("")
    if balance_a.get("status") == "success":
        m = balance_a["metrics"]
        lines.append("### 4.1 资产规模与结构")
        lines.append("")
        for label in ["总资产", "总负债", "股东权益", "资产负债率", "权益比率"]:
            metric = m.get(label, {})
            val = metric.get("formatted", ("-", ""))
            yoy = metric.get("yoy")
            yoy_text = f"，同比{'+' if yoy >= 0 else ''}{yoy}%" if yoy is not None else ""
            lines.append(f"- **{label}**: {val[0]}{val[1]}{yoy_text}")
        lines.append("")

        # 资产结构
        if balance_a.get("structure"):
            lines.append("### 4.2 资产结构分布")
            lines.append("")
            lines.append("| 资产项目 | 金额 | 占总资产比例 |")
            lines.append("|----------|------|-------------|")
            for label in ["货币资金", "应收账款", "存货", "固定资产", "商誉"]:
                metric = m.get(label, {})
                val = metric.get("formatted", ("-", ""))
                pct = balance_a["structure"].get(label)
                pct_str = f"{pct:.2f}%" if pct is not None else "-"
                lines.append(f"| {label} | {val[0]} {val[1]} | {pct_str} |")
            lines.append("")

        lines.append("### 4.3 有息负债")
        lines.append("")
        for label in ["短期借款", "长期借款"]:
            metric = m.get(label, {})
            val = metric.get("formatted", ("-", ""))
            lines.append(f"- **{label}**: {val[0]} {val[1]}")
        lines.append("")
    else:
        lines.append("*资产负债表数据获取失败*")
        lines.append("")

    # ---- 现金流量表分析 ----
    lines.append("## 五、现金流量表分析")
    lines.append("")
    if cashflow_a.get("status") == "success":
        m = cashflow_a["metrics"]
        for label in ["经营活动现金流净额", "投资活动现金流净额", "筹资活动现金流净额", "现金净增加额"]:
            metric = m.get(label, {})
            val = metric.get("formatted", ("-", ""))
            yoy = metric.get("yoy")
            yoy_text = f"，同比{'+' if yoy >= 0 else ''}{yoy}%" if yoy is not None else ""
            lines.append(f"- **{label}**: {val[0]} {val[1]}{yoy_text}")
        lines.append("")

        if cashflow_a.get("quality_notes"):
            lines.append("**现金流质量判断:**")
            lines.append("")
            for note in cashflow_a["quality_notes"]:
                lines.append(f"- {note}")
            lines.append("")
    else:
        lines.append("*现金流量表数据获取失败*")
        lines.append("")

    # ---- 行业对比分析 ----
    lines.append("## 六、行业对比分析")
    lines.append("")
    if industry_info and industry_info.get("industry_name"):
        lines.append(f"### 所属行业: {industry_info['industry_name']}")
        lines.append("")

        if industry_info.get("sector_pe") is not None:
            lines.append("### 6.1 行业整体情况")
            lines.append("")
            lines.append("| 指标 | 值 |")
            lines.append("|------|-----|")
            if industry_info.get("sector_pe"):
                lines.append(f"| 行业平均PE | {industry_info['sector_pe']:.2f} |")
            if industry_info.get("sector_change_pct") is not None:
                lines.append(f"| 行业涨跌幅 | {industry_info['sector_change_pct']:.2f}% |")
            if industry_info.get("sector_cap"):
                cap_f = _fmt_val(industry_info["sector_cap"])
                lines.append(f"| 行业总市值 | {cap_f[0]} {cap_f[1]} |")
            lines.append("")

            # 对比公司PE
            if pe and industry_info.get("sector_pe"):
                pe_val = stock_info.get("pe_ttm", 0)
                sector_pe = industry_info["sector_pe"]
                if pe_val and sector_pe and sector_pe > 0:
                    pe_premium = round((pe_val / sector_pe - 1) * 100, 1)
                    if pe_premium > 0:
                        lines.append(f"公司PE(TTM) {pe_val:.2f}，较行业平均溢价 {pe_premium}%。")
                    else:
                        lines.append(f"公司PE(TTM) {pe_val:.2f}，较行业平均折价 {abs(pe_premium)}%。")
                    lines.append("")

        # 资金流向
        if industry_info.get("fund_flow"):
            ff = industry_info["fund_flow"]
            lines.append("### 6.2 行业资金流向")
            lines.append("")
            main_flow = ff.get("main_net_inflow")
            if main_flow is not None:
                flow_f = _fmt_val(main_flow)
                direction = "流入" if main_flow > 0 else "流出"
                lines.append(f"- 主力资金净{direction}: {flow_f[0]} {flow_f[1]}")
            main_pct = ff.get("main_net_pct")
            if main_pct is not None:
                lines.append(f"- 主力净占比: {main_pct:.2f}%")
            lines.append("")

        # 同行对比
        if industry_info.get("peer_companies"):
            lines.append("### 6.3 同行公司对比（按市值排序）")
            lines.append("")
            lines.append("| 代码 | 公司 | 股价 | 涨跌幅 | PE(TTM) | PB | ROE | 市值 |")
            lines.append("|------|------|------|--------|---------|-----|-----|------|")
            for p in industry_info["peer_companies"][:10]:
                price = f"{p['price']:.2f}" if p.get("price") else "-"
                chg = f"{p['change_pct']:.2f}%" if p.get("change_pct") is not None else "-"
                pe_p = f"{p['pe_ttm']:.2f}" if p.get("pe_ttm") else "-"
                pb_p = f"{p['pb']:.2f}" if p.get("pb") else "-"
                roe_p = f"{p['roe']:.2f}%" if p.get("roe") else "-"
                mc_p = _fmt_val(p.get("market_cap"))
                highlight = " **" if p.get("code") == code else ""
                highlight_end = "**" if highlight else ""
                lines.append(f"| {highlight}{p.get('code', '')}{highlight_end} | {highlight}{p.get('name', '')}{highlight_end} | {price} | {chg} | {pe_p} | {pb_p} | {roe_p} | {mc_p[0]} {mc_p[1]} |")
            lines.append("")
    else:
        lines.append("*行业数据未获取或行业信息缺失*")
        lines.append("")

    # ---- 风险与亮点 ----
    lines.append("## 七、风险提示与投资亮点")
    lines.append("")

    if risk_result.get("alerts"):
        lines.append("### 风险警示")
        lines.append("")
        for a in risk_result["alerts"]:
            lines.append(f"- :warning: **{a}**")
        lines.append("")

    if risk_result.get("warnings"):
        lines.append("### 需关注")
        lines.append("")
        for w in risk_result["warnings"]:
            lines.append(f"- :eyes: {w}")
        lines.append("")

    if risk_result.get("positives"):
        lines.append("### 投资亮点")
        lines.append("")
        for p in risk_result["positives"]:
            lines.append(f"- :white_check_mark: {p}")
        lines.append("")

    # ---- 综合评估 ----
    lines.append("## 八、综合评估")
    lines.append("")
    lines.append("### 财务健康度评分")
    lines.append("")

    score = 60  # 基础分
    if income_a.get("status") == "success":
        profit_yoy = income_a["metrics"].get("净利润", {}).get("yoy")
        gm = income_a["metrics"].get("毛利率", {}).get("value")
        if profit_yoy is not None:
            if profit_yoy > 20:
                score += 10
            elif profit_yoy > 0:
                score += 5
            elif profit_yoy < -20:
                score -= 15
            else:
                score -= 5
        if gm is not None:
            if gm > 40:
                score += 5
            elif gm < 15:
                score -= 5

    if balance_a.get("status") == "success":
        dr = balance_a["metrics"].get("资产负债率", {}).get("value")
        if dr is not None:
            if dr < 40:
                score += 5
            elif dr > 70:
                score -= 10

    if cashflow_a.get("status") == "success":
        op_cf = cashflow_a["metrics"].get("经营活动现金流净额", {}).get("value")
        if op_cf is not None and op_cf > 0:
            score += 5
        elif op_cf is not None and op_cf < 0:
            score -= 10

    roe = derived.get("ROE")
    if roe is not None:
        if roe > 15:
            score += 10
        elif roe > 10:
            score += 5
        elif roe < 5:
            score -= 5

    score = max(0, min(100, score))

    if score >= 80:
        grade = "A (优秀)"
    elif score >= 65:
        grade = "B (良好)"
    elif score >= 50:
        grade = "C (一般)"
    else:
        grade = "D (偏弱)"

    lines.append(f"**综合评分: {score}/100 — {grade}**")
    lines.append("")

    dimensions = []
    if income_a.get("status") == "success":
        gm = income_a["metrics"].get("毛利率", {}).get("value")
        nm = income_a["metrics"].get("净利率", {}).get("value")
        if gm is not None and nm is not None:
            profitability = "强" if gm > 35 and nm > 10 else ("中" if gm > 20 else "弱")
            dimensions.append(f"盈利能力: {profitability}")

    if income_a.get("status") == "success":
        rev_yoy = income_a["metrics"].get("营业收入", {}).get("yoy")
        if rev_yoy is not None:
            growth = "高" if rev_yoy > 15 else ("中" if rev_yoy > 0 else "负增长")
            dimensions.append(f"成长性: {growth}")

    if balance_a.get("status") == "success":
        dr = balance_a["metrics"].get("资产负债率", {}).get("value")
        if dr is not None:
            safety = "高" if dr < 40 else ("中" if dr < 65 else "低")
            dimensions.append(f"财务安全: {safety}")

    if cashflow_a.get("status") == "success":
        op_cf = cashflow_a["metrics"].get("经营活动现金流净额", {}).get("value")
        quality = "好" if op_cf and op_cf > 0 else "差"
        dimensions.append(f"现金流质量: {quality}")

    if roe is not None:
        efficiency = "高" if roe > 15 else ("中" if roe > 8 else "低")
        dimensions.append(f"资本效率: {efficiency}")

    if dimensions:
        lines.append("| 维度 | 评价 |")
        lines.append("|------|------|")
        for d in dimensions:
            parts = d.split(": ")
            lines.append(f"| {parts[0]} | {parts[1]} |")
        lines.append("")

    # ---- 已下载文件 ----
    if pdf_paths:
        lines.append("## 附录: 已下载财报文件")
        lines.append("")
        for p in pdf_paths:
            lines.append(f"- {os.path.basename(p)}")
        lines.append("")

    # ---- 免责 ----
    lines.append("---")
    lines.append("")
    lines.append("*本报告数据来源于东方财富、巨潮资讯等公开API，仅供参考，不构成投资建议。"
                 "数据可能存在延迟，请以官方披露为准。*")

    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="零依赖 A股财报综合分析工具")
    parser.add_argument("--code", type=str, required=True, help="股票代码（6位数字）")
    parser.add_argument("--year", type=int, default=None, help="分析年份（默认最新）")
    parser.add_argument("--periods", type=int, default=4, help="获取最近几期数据（默认4）")
    parser.add_argument("--mode", type=str, default="report",
                        choices=["data", "download", "industry", "report"],
                        help="运行模式: data=仅财务数据, download=仅下载PDF, industry=仅行业分析, report=完整报告")
    parser.add_argument("--output-dir", type=str, default=".", help="输出目录")
    parser.add_argument("--download-type", type=str, default="annual",
                        choices=["annual", "half", "q1", "q3"],
                        help="下载财报类型（默认年报）")

    args = parser.parse_args()

    code = args.code.strip()
    if not re.match(r"^\d{6}$", code):
        print(f"[错误] 无效的股票代码: {code}")
        sys.exit(2)

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    print("=" * 60)
    print("  A股财报综合分析（零依赖版）")
    print("=" * 60)
    print()

    # 获取基本信息
    print("[1/5] 获取公司基本信息...")
    stock_info = get_stock_info(code)
    name = stock_info.get("name", "")
    industry = stock_info.get("industry", "")
    print(f"  公司: {name}  行业: {industry}")
    print()

    financial_data = {}
    income_a = {"status": "no_data"}
    balance_a = {"status": "no_data"}
    cashflow_a = {"status": "no_data"}
    derived = {}
    risk_result = {"alerts": [], "warnings": [], "positives": []}
    industry_info = {}
    pdf_paths = []

    # 模块1: 财务数据
    if args.mode in ("data", "report"):
        print("[2/5] 获取财务报表数据...")
        financial_data = fetch_all_financial_data(code, args.periods)

        # 保存原始数据
        for rt, rd in financial_data.items():
            if rd.get("records"):
                json_path = os.path.join(output_dir, f"{code}_{rt}.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(rd["records"], f, ensure_ascii=False, indent=2)
                print(f"  已保存: {json_path}")
        print()

        # 分析
        print("[3/5] 结构化分析...")
        income_records = financial_data.get("income", {}).get("records", [])
        balance_records = financial_data.get("balance", {}).get("records", [])
        cashflow_records = financial_data.get("cashflow", {}).get("records", [])

        income_a = analyze_income_detail(income_records)
        balance_a = analyze_balance_detail(balance_records)
        cashflow_a = analyze_cashflow_detail(cashflow_records)
        derived = calculate_derived_metrics(income_a, balance_a, cashflow_a)
        risk_result = generate_risk_alerts(income_a, balance_a, cashflow_a, derived)

        # 保存分析JSON
        analysis_output = {
            "stock_code": code,
            "stock_name": name,
            "analysis_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "income": income_a,
            "balance": balance_a,
            "cashflow": cashflow_a,
            "derived_metrics": derived,
            "risk_assessment": risk_result,
        }
        analysis_json = os.path.join(output_dir, f"{code}_analysis.json")
        with open(analysis_json, "w", encoding="utf-8") as f:
            json.dump(analysis_output, f, ensure_ascii=False, indent=2, default=str)
        print(f"  分析JSON已保存: {analysis_json}")
        print()
    else:
        print("[2/5] 跳过财务数据获取")
        print("[3/5] 跳过结构化分析")
        print()

    # 模块2: PDF下载
    if args.mode in ("download", "report"):
        print("[4/5] 下载财报PDF...")
        try:
            path, err = download_report_pdf(code, output_dir, year=args.year,
                                            report_type=args.download_type)
            if path:
                pdf_paths.append(path)
            elif err:
                print(f"  PDF下载失败: {err}")
        except Exception as e:
            print(f"  PDF下载异常: {e}")
        print()
    else:
        print("[4/5] 跳过PDF下载")
        print()

    # 模块3: 行业分析
    if args.mode in ("industry", "report"):
        print("[5/5] 获取行业基本面数据...")
        industry_info = get_industry_data(industry, code)
        peer_count = len(industry_info.get("peer_companies", []))
        print(f"  行业: {industry_info.get('industry_name', '未知')}, 同行公司: {peer_count} 家")
        if industry_info.get("sector_pe"):
            try:
                print(f"  行业PE: {float(industry_info['sector_pe']):.2f}")
            except (ValueError, TypeError):
                print(f"  行业PE: {industry_info['sector_pe']}")

        # 保存行业数据
        industry_json = os.path.join(output_dir, f"{code}_industry.json")
        with open(industry_json, "w", encoding="utf-8") as f:
            json.dump(industry_info, f, ensure_ascii=False, indent=2, default=str)
        print(f"  行业数据已保存: {industry_json}")
        print()
    else:
        print("[5/5] 跳过行业分析")
        print()

    # 生成报告
    if args.mode == "report":
        print("生成综合分析报告...")
        report = generate_comprehensive_report(
            code, stock_info, financial_data,
            income_a, balance_a, cashflow_a,
            derived, risk_result, industry_info,
            pdf_paths, year=args.year,
        )
        report_name = f"{code}_{name}_综合分析报告" if name else f"{code}_综合分析报告"
        if args.year:
            report_name += f"_{args.year}年"
        md_path = os.path.join(output_dir, f"{report_name}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"  报告已保存: {md_path}")

    print()
    print("=" * 60)
    print("  完成!")
    print("=" * 60)

    # 退出码
    has_data = any(
        financial_data.get(rt, {}).get("records")
        for rt in REPORT_TYPES
    )
    sys.exit(0 if has_data or args.mode in ("download", "industry") else 1)


if __name__ == "__main__":
    main()
