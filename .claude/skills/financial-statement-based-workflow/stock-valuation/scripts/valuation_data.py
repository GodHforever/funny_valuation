#!/usr/bin/env python3
"""
零依赖 A股估值数据采集工具。

通过东方财富等公开 API 获取估值所需的全部数据：
实时行情、5年财务报表、WACC输入、分红历史、行业对比、历史估值区间。

仅需 Python 3.6+，核心功能零第三方依赖。

用法:
    python valuation_data.py --code 600519 --output-dir ./output
    python valuation_data.py --code 300750 --output-dir ./output --skip-beta
"""

import argparse
import json
import math
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

# ============================================================
# 常量与配置
# ============================================================

FETCH_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EASTMONEY_REPORT_MAP = {
    "income": "RPT_DMSK_FN_INCOME",
    "balance": "RPT_DMSK_FN_BALANCE",
    "cashflow": "RPT_DMSK_FN_CASHFLOW",
}

EASTMONEY_REPORT_MAP_V2 = {
    "income": {"url": "https://datacenter.eastmoney.com/securities/api/data/get", "report": "RPT_F10_FINANCE_GINCOME"},
    "balance": {"url": "https://datacenter.eastmoney.com/securities/api/data/get", "report": "RPT_F10_FINANCE_GBALANCE"},
    "cashflow": {"url": "https://datacenter.eastmoney.com/securities/api/data/get", "report": "RPT_F10_FINANCE_GCASHFLOW"},
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


def _safe_float(val):
    """安全转换浮点数，处理 None/NaN/非数字。"""
    if val is None:
        return None
    try:
        v = float(val)
        return v if v == v else None  # NaN check
    except (ValueError, TypeError):
        return None


def _safe_div(a, b):
    """安全除法，避免除零。"""
    a, b = _safe_float(a), _safe_float(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _get_secid(code):
    """根据股票代码生成东方财富 secid。"""
    prefix = code[:2]
    if prefix in ("60", "68"):
        return f"1.{code}"
    else:
        return f"0.{code}"


def _find_value(record, keys):
    """在记录中查找字段值（尝试多个可能的键名）。"""
    if not record:
        return None
    for key in keys:
        val = record.get(key)
        if val is not None:
            return _safe_float(val)
    return None


# 东方财富字段名映射
FIELD_KEYS = {
    # 利润表
    "revenue": ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME"],
    "operating_cost": ["OPERATE_COST", "TOTAL_OPERATE_COST"],
    "net_profit": ["NETPROFIT", "NET_PROFIT"],
    "net_profit_parent": ["PARENT_NETPROFIT"],
    "operating_profit": ["OPERATE_PROFIT"],
    "total_profit": ["TOTAL_PROFIT"],
    "income_tax": ["INCOME_TAX"],
    "finance_expense": ["FINANCE_EXPENSE"],
    "rd_expense": ["RESEARCH_EXPENSE"],
    "sales_expense": ["SALE_EXPENSE"],
    "admin_expense": ["MANAGE_EXPENSE"],
    "non_recurring": ["DEDUCT_PARENT_NETPROFIT"],
    "basic_eps": ["BASIC_EPS"],
    # 资产负债表
    "total_assets": ["TOTAL_ASSETS"],
    "total_liab": ["TOTAL_LIABILITIES"],
    "total_equity": ["TOTAL_EQUITY", "TOTAL_PARENT_EQUITY"],
    "parent_equity": ["TOTAL_PARENT_EQUITY"],
    "cash_equiv": ["MONETARYFUNDS", "CURRENCY_FUNDS"],
    "accounts_recv": ["ACCOUNTS_RECE"],
    "inventory": ["INVENTORY"],
    "fixed_assets": ["FIXED_ASSET"],
    "goodwill": ["GOODWILL"],
    "short_loan": ["SHORT_LOAN"],
    "long_loan": ["LONG_LOAN"],
    "bonds_payable": ["BOND_PAYABLE"],
    "minority_interest": ["MINORITY_EQUITY"],
    "intangible_assets": ["INTANGIBLE_ASSET"],
    "prepayments": ["PREPAYMENT"],
    "accounts_payable": ["ACCOUNTS_PAYABLE"],
    "advance_receipts": ["ADVANCE_RECEIVABLES", "CONTRACT_LIAB"],
    "total_shares": ["TOTAL_SHARES"],
    # 现金流量表
    "operating_cf": ["NETCASH_OPERATE"],
    "investing_cf": ["NETCASH_INVEST"],
    "financing_cf": ["NETCASH_FINANCE"],
    "depreciation": ["FIXED_ASSET_DEPRECIATION", "FA_IR_DEPR"],
    "amortization": ["INTANGIBLE_ASSET_AMORTIZATION", "IA_AMORTIZE"],
    "long_asset_purchase": ["CONSTRUCT_LONG_ASSET"],
    # 日期
    "report_date": ["REPORT_DATE", "REPORTDATE"],
    "security_name": ["SECURITY_NAME_ABBR", "SECURITY_NAME"],
}


# ============================================================
# 实时行情获取
# ============================================================

class RealTimeQuote:
    """从东方财富获取实时行情数据。"""

    def fetch(self, code):
        """获取实时行情，返回 dict。"""
        secid = _get_secid(code)
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={secid}"
            f"&fields=f43,f44,f45,f46,f47,f55,f57,f58,f116,f117,"
            f"f127,f162,f163,f164,f167,f168,f170"
        )
        result = {
            "price": None, "change_pct": None, "volume": None,
            "volume_ratio_20d": None, "total_market_cap": None,
            "circulating_market_cap": None, "pe_ttm": None,
            "pb_mrq": None, "ps_ttm": None, "dividend_yield": None,
            "total_shares": None, "name": None, "industry": None,
            "status": "failed"
        }
        try:
            data = http_get_json(url)
            if not data or not data.get("data"):
                return result
            d = data["data"]
            # push2 API: f43 以分为单位, f162/f163/f164/f167/f170 已乘100
            raw_price = _safe_float(d.get("f43"))
            price = raw_price / 100 if raw_price else None
            raw_pe = _safe_float(d.get("f162"))
            raw_pb = _safe_float(d.get("f163"))
            raw_ps = _safe_float(d.get("f164"))
            raw_yield = _safe_float(d.get("f167"))
            raw_change = _safe_float(d.get("f170"))
            result.update({
                "price": price,
                "change_pct": raw_change / 100 if raw_change else None,
                "volume": _safe_float(d.get("f47")),
                "total_market_cap": _safe_div(d.get("f116"), 1e8),  # 转为亿元
                "circulating_market_cap": _safe_div(d.get("f117"), 1e8),
                "pe_ttm": raw_pe / 100 if raw_pe else None,
                "pb_mrq": raw_pb / 100 if raw_pb else None,
                "ps_ttm": raw_ps / 100 if raw_ps else None,
                "dividend_yield": raw_yield / 100 if raw_yield else None,
                "total_shares": _safe_div(d.get("f55"), 1e8) if d.get("f55") else None,  # 转为亿股
                "name": d.get("f58", ""),
                "industry": d.get("f127", ""),
                "status": "success"
            })
            # 尝试获取20日均量来计算量比
            result["volume_ratio_20d"] = self._fetch_volume_ratio(secid)
        except Exception as e:
            print(f"  [警告] 实时行情获取失败: {e}")
        return result

    def _fetch_volume_ratio(self, secid):
        """获取量比（当日成交量/20日均量）。"""
        try:
            url = (
                f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
                f"secid={secid}&klt=101&fqt=1&beg=0&end=20500101"
                f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55&lmt=21"
            )
            data = http_get_json(url)
            if data and data.get("data") and data["data"].get("klines"):
                klines = data["data"]["klines"]
                if len(klines) >= 21:
                    volumes = []
                    for k in klines[:-1]:  # 前20日
                        parts = k.split(",")
                        if len(parts) >= 5:
                            v = _safe_float(parts[4])
                            if v:
                                volumes.append(v)
                    if volumes:
                        avg_vol = sum(volumes) / len(volumes)
                        today_parts = klines[-1].split(",")
                        if len(today_parts) >= 5:
                            today_vol = _safe_float(today_parts[4])
                            if today_vol and avg_vol > 0:
                                return round(today_vol / avg_vol, 2)
        except Exception:
            pass
        return None


# ============================================================
# 财务报表获取
# ============================================================

class FinancialDataFetcher:
    """从东方财富获取5年财务报表数据。"""

    def _build_url(self, report_name, code, periods=20):
        """构建东方财富财务报表 API 请求 URL。"""
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

    def _build_url_v2(self, report_type, code, periods=20):
        """备用 API URL。"""
        conf = EASTMONEY_REPORT_MAP_V2.get(report_type)
        if not conf:
            return None
        params = {
            "type": conf["report"], "sty": "ALL",
            "ps": str(periods), "p": "1",
            "sr": "-1", "st": "REPORT_DATE",
            "filter": f'(SECURITY_CODE="{code}")',
        }
        return f"{conf['url']}?{urllib.parse.urlencode(params)}"

    def fetch_financial_data(self, code, report_type, periods=20):
        """获取财务报表数据，双源回退。返回 (records, source, error)。"""
        errors = []
        # 方法1: datacenter-web
        report_name = EASTMONEY_REPORT_MAP.get(report_type)
        if report_name:
            try:
                url = self._build_url(report_name, code, periods)
                data = http_get_json(url)
                if data and data.get("result") and data["result"].get("data"):
                    return data["result"]["data"], "eastmoney-web", None
                errors.append("eastmoney-web: 返回空数据")
            except Exception as e:
                errors.append(f"eastmoney-web: {e}")
        # 方法2: datacenter 备用
        try:
            url2 = self._build_url_v2(report_type, code, periods)
            if url2:
                data2 = http_get_json(url2)
                if data2 and data2.get("result") and data2["result"].get("data"):
                    return data2["result"]["data"], "eastmoney-v2", None
                errors.append("eastmoney-v2: 返回空数据")
        except Exception as e:
            errors.append(f"eastmoney-v2: {e}")
        return None, None, "; ".join(errors)

    def fetch_all(self, code, periods=20):
        """获取三张报表的全部数据。"""
        results = {}
        for rt in ["income", "balance", "cashflow"]:
            print(f"  [{rt}] 获取中...", end=" ", flush=True)
            records, source, error = self.fetch_financial_data(code, rt, periods)
            if records:
                results[rt] = {"records": records, "source": source}
                print(f"成功 ({len(records)}期, {source})")
            else:
                results[rt] = {"records": [], "source": None, "error": error}
                print(f"失败: {error}")
        return results

    def extract_annual_records(self, records, years=5):
        """从季报记录中提取年报记录（报告日期为12-31的）。"""
        annual = []
        for r in records:
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            if rd and "12-31" in rd:
                annual.append(r)
        # 按日期降序排列，取最近 years 年
        annual.sort(key=lambda x: x.get("REPORT_DATE", "") or x.get("REPORTDATE", ""), reverse=True)
        return annual[:years]

    def extract_series(self, annual_records, field_keys, to_yi=False):
        """从年报记录中提取某字段的年度序列。"""
        series = []
        for r in annual_records:
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            val = _find_value(r, field_keys)
            if to_yi and val is not None:
                val = val / 1e8  # 转为亿元
            series.append({"year": year, "value": val})
        series.sort(key=lambda x: x["year"] if x["year"] else 0)
        return series

    def build_income_series(self, income_records):
        """构建利润表年度序列。"""
        annuals = self.extract_annual_records(income_records)
        result = {}
        # 营业收入（亿元）
        result["revenue"] = self.extract_series(annuals, FIELD_KEYS["revenue"], to_yi=True)
        # 毛利率
        gm_series = []
        for r in annuals:
            rev = _find_value(r, FIELD_KEYS["revenue"])
            cost = _find_value(r, FIELD_KEYS["operating_cost"])
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            gm = None
            if rev and cost and rev != 0:
                gm = round((rev - cost) / rev * 100, 2)
            gm_series.append({"year": year, "value": gm})
        gm_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        result["gross_margin"] = gm_series
        # EBIT = 营业利润 + 财务费用（近似）
        ebit_series = []
        for r in annuals:
            op = _find_value(r, FIELD_KEYS["operating_profit"])
            fe = _find_value(r, FIELD_KEYS["finance_expense"])
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            ebit = None
            if op is not None:
                ebit = op
                if fe is not None:
                    ebit = op + fe  # EBIT ≈ 营业利润 + 财务费用
                ebit = ebit / 1e8
            ebit_series.append({"year": year, "value": ebit})
        ebit_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        result["ebit"] = ebit_series
        # EBITDA = EBIT + 折旧摊销（需要现金流量表，这里先放 None，后续合并）
        result["ebitda"] = []
        # 净利润（亿元）
        result["net_profit"] = self.extract_series(annuals, FIELD_KEYS["net_profit_parent"], to_yi=True)
        # EPS
        result["eps"] = self.extract_series(annuals, FIELD_KEYS["basic_eps"])
        # 非经常性损益 = 净利润 - 扣非净利润
        nr_series = []
        for r in annuals:
            np_val = _find_value(r, FIELD_KEYS["net_profit_parent"])
            dnp = _find_value(r, FIELD_KEYS["non_recurring"])
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            nr = None
            if np_val is not None and dnp is not None:
                nr = (np_val - dnp) / 1e8
            nr_series.append({"year": year, "value": nr})
        nr_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        result["non_recurring"] = nr_series
        # 实际有效税率
        tax_series = []
        for r in annuals:
            tax = _find_value(r, FIELD_KEYS["income_tax"])
            tp = _find_value(r, FIELD_KEYS["total_profit"])
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            rate = None
            if tax is not None and tp is not None and tp != 0:
                rate = round(tax / tp * 100, 2)
            tax_series.append({"year": year, "value": rate})
        tax_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        result["effective_tax_rate"] = tax_series
        return result

    def build_balance_data(self, balance_records):
        """构建资产负债表数据（最新值 + 年度序列）。"""
        if not balance_records:
            return {"latest": {}, "annual_series": {}, "status": "failed"}
        latest = balance_records[0]
        total_eq = _find_value(latest, FIELD_KEYS["parent_equity"]) or _find_value(latest, FIELD_KEYS["total_equity"])
        total_shares_val = _find_value(latest, FIELD_KEYS["total_shares"])
        # 尝试从利润表或行情获取总股本
        bvps = None
        if total_eq is not None and total_shares_val and total_shares_val > 0:
            bvps = round(total_eq / total_shares_val, 2)
        short_l = _find_value(latest, FIELD_KEYS["short_loan"]) or 0
        long_l = _find_value(latest, FIELD_KEYS["long_loan"]) or 0
        bonds = _find_value(latest, FIELD_KEYS["bonds_payable"]) or 0
        interest_bearing = (short_l + long_l + bonds) / 1e8
        latest_data = {
            "bvps": bvps,
            "goodwill": _safe_div(_find_value(latest, FIELD_KEYS["goodwill"]), 1e8),
            "total_shares": _safe_div(total_shares_val, 1e8),
            "interest_bearing_debt": round(interest_bearing, 2),
            "cash": _safe_div(_find_value(latest, FIELD_KEYS["cash_equiv"]), 1e8),
            "minority_interest": _safe_div(_find_value(latest, FIELD_KEYS["minority_interest"]), 1e8),
            "receivables": _safe_div(_find_value(latest, FIELD_KEYS["accounts_recv"]), 1e8),
            "inventory": _safe_div(_find_value(latest, FIELD_KEYS["inventory"]), 1e8),
            "total_assets": _safe_div(_find_value(latest, FIELD_KEYS["total_assets"]), 1e8),
            "total_equity": _safe_div(total_eq, 1e8),
            "fixed_assets": _safe_div(_find_value(latest, FIELD_KEYS["fixed_assets"]), 1e8),
        }
        # 年度序列
        annuals = self.extract_annual_records(balance_records)
        # ROE 序列（近3年）
        roe_series = []
        for i, r in enumerate(annuals[:3]):
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            np_r = _find_value(r, FIELD_KEYS["parent_equity"])
            net_p = None  # 需要利润表数据，后续合并
            roe_series.append({"year": year, "value": None})
        annual_series = {
            "roe": roe_series,
            "roa": [],
            "receivables": self.extract_series(annuals, FIELD_KEYS["accounts_recv"], to_yi=True),
            "inventory": self.extract_series(annuals, FIELD_KEYS["inventory"], to_yi=True),
            "inventory_turnover_days": [],
        }
        return {"latest": latest_data, "annual_series": annual_series, "status": "success"}

    def build_cashflow_series(self, cashflow_records):
        """构建现金流量表年度序列。"""
        annuals = self.extract_annual_records(cashflow_records)
        result = {
            "operating_cashflow": self.extract_series(annuals, FIELD_KEYS["operating_cf"], to_yi=True),
            "capex": [],
            "depreciation": [],
            "working_capital_change": [],
        }
        # 资本支出（投资活动现金流出中的固定资产等购建）
        capex_series = []
        for r in annuals:
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            cap = _find_value(r, FIELD_KEYS["long_asset_purchase"])
            if cap is not None:
                cap = abs(cap) / 1e8
            capex_series.append({"year": year, "value": cap})
        capex_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        result["capex"] = capex_series
        # 折旧摊销
        dep_series = []
        for r in annuals:
            rd = r.get("REPORT_DATE", "") or r.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            dep = _find_value(r, FIELD_KEYS["depreciation"])
            amort = _find_value(r, FIELD_KEYS["amortization"])
            total_dep = 0
            if dep is not None:
                total_dep += dep
            if amort is not None:
                total_dep += amort
            dep_series.append({"year": year, "value": total_dep / 1e8 if total_dep else None})
        dep_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        result["depreciation"] = dep_series
        return result

    def compute_derived_metrics(self, income_data, balance_data, cashflow_data):
        """计算衍生指标：ROE、ROA、EBITDA、存货周转天数、营运资本变化。"""
        # ROE = 净利润 / 平均股东权益
        income_recs = self.extract_annual_records(income_data.get("records", []))
        balance_recs = self.extract_annual_records(balance_data.get("records", []))
        roe_series = []
        roa_series = []
        for i, ir in enumerate(income_recs[:3]):
            rd = ir.get("REPORT_DATE", "") or ir.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            np_val = _find_value(ir, FIELD_KEYS["net_profit_parent"])
            # 找同年的资产负债表
            eq_val = None
            ta_val = None
            for br in balance_recs:
                brd = br.get("REPORT_DATE", "") or br.get("REPORTDATE", "")
                if brd and rd and brd[:4] == rd[:4]:
                    eq_val = _find_value(br, FIELD_KEYS["parent_equity"]) or _find_value(br, FIELD_KEYS["total_equity"])
                    ta_val = _find_value(br, FIELD_KEYS["total_assets"])
                    break
            roe = None
            if np_val is not None and eq_val and eq_val != 0:
                roe = round(np_val / eq_val * 100, 2)
            roa = None
            if np_val is not None and ta_val and ta_val != 0:
                roa = round(np_val / ta_val * 100, 2)
            roe_series.append({"year": year, "value": roe})
            roa_series.append({"year": year, "value": roa})
        roe_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        roa_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        # EBITDA = EBIT + 折旧摊销
        cashflow_recs = self.extract_annual_records(cashflow_data.get("records", []))
        ebitda_series = []
        for ir in income_recs:
            rd = ir.get("REPORT_DATE", "") or ir.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            op = _find_value(ir, FIELD_KEYS["operating_profit"])
            fe = _find_value(ir, FIELD_KEYS["finance_expense"])
            ebit = None
            if op is not None:
                ebit = op + (fe or 0)
            # 找同年折旧
            dep_total = 0
            for cr in cashflow_recs:
                crd = cr.get("REPORT_DATE", "") or cr.get("REPORTDATE", "")
                if crd and rd and crd[:4] == rd[:4]:
                    dep = _find_value(cr, FIELD_KEYS["depreciation"]) or 0
                    amort = _find_value(cr, FIELD_KEYS["amortization"]) or 0
                    dep_total = dep + amort
                    break
            ebitda = None
            if ebit is not None:
                ebitda = (ebit + dep_total) / 1e8
            ebitda_series.append({"year": year, "value": ebitda})
        ebitda_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        # 存货周转天数
        inv_days_series = []
        for i, ir in enumerate(income_recs[:3]):
            rd = ir.get("REPORT_DATE", "") or ir.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            cost = _find_value(ir, FIELD_KEYS["operating_cost"])
            inv_val = None
            for br in balance_recs:
                brd = br.get("REPORT_DATE", "") or br.get("REPORTDATE", "")
                if brd and rd and brd[:4] == rd[:4]:
                    inv_val = _find_value(br, FIELD_KEYS["inventory"])
                    break
            days = None
            if cost and inv_val and cost != 0:
                days = round(inv_val / cost * 365, 1)
            inv_days_series.append({"year": year, "value": days})
        inv_days_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        # 营运资本变化
        wc_series = []
        prev_wc = None
        for br in sorted(balance_recs, key=lambda x: x.get("REPORT_DATE", "")):
            rd = br.get("REPORT_DATE", "") or br.get("REPORTDATE", "")
            year = int(rd[:4]) if rd else None
            recv = _find_value(br, FIELD_KEYS["accounts_recv"]) or 0
            inv = _find_value(br, FIELD_KEYS["inventory"]) or 0
            prep = _find_value(br, FIELD_KEYS["prepayments"]) or 0
            payable = _find_value(br, FIELD_KEYS["accounts_payable"]) or 0
            adv = _find_value(br, FIELD_KEYS["advance_receipts"]) or 0
            wc = recv + inv + prep - payable - adv
            change = None
            if prev_wc is not None:
                change = (wc - prev_wc) / 1e8
            prev_wc = wc
            wc_series.append({"year": year, "value": change})
        wc_series.sort(key=lambda x: x["year"] if x["year"] else 0)
        return {
            "roe": roe_series,
            "roa": roa_series,
            "ebitda": ebitda_series,
            "inventory_turnover_days": inv_days_series,
            "working_capital_change": wc_series,
        }


# ============================================================
# WACC 输入数据获取
# ============================================================

class WACCDataFetcher:
    """获取 WACC 计算所需数据。"""

    def fetch_risk_free_rate(self):
        """获取10年期国债收益率。"""
        try:
            url = (
                "https://datacenter-web.eastmoney.com/api/data/v1/get?"
                "reportName=RPTA_WEB_TREASURYYIELD&columns=ALL"
                "&pageSize=3&pageNumber=1"
                "&sortColumns=SOLAR_DATE&sortTypes=-1"
            )
            data = http_get_json(url)
            if data and data.get("result") and data["result"].get("data"):
                rows = data["result"]["data"]
                rf = _safe_float(rows[0].get("EMM00166469"))
                date = rows[0].get("SOLAR_DATE", "")[:10]
                return {"value": rf, "date": date, "status": "success"}
        except Exception as e:
            print(f"  [警告] 国债收益率获取失败: {e}")
        return {"value": 2.85, "date": "", "status": "failed_using_default"}

    def estimate_kd(self, income_records, balance_records):
        """估算有息负债加权利率 Kd = 利息费用 / 平均有息负债。"""
        fetcher = FinancialDataFetcher()
        inc_annuals = fetcher.extract_annual_records(income_records)
        bal_annuals = fetcher.extract_annual_records(balance_records)
        if not inc_annuals or not bal_annuals:
            return None
        # 取最近年的利息费用
        fe = _find_value(inc_annuals[0], FIELD_KEYS["finance_expense"])
        if fe is None or fe <= 0:
            return 0.0  # 无利息费用
        # 计算平均有息负债
        debts = []
        for br in bal_annuals[:2]:
            sl = _find_value(br, FIELD_KEYS["short_loan"]) or 0
            ll = _find_value(br, FIELD_KEYS["long_loan"]) or 0
            bp = _find_value(br, FIELD_KEYS["bonds_payable"]) or 0
            debts.append(sl + ll + bp)
        if not debts or max(debts) == 0:
            return 0.0
        avg_debt = sum(debts) / len(debts)
        if avg_debt == 0:
            return 0.0
        return round(fe / avg_debt * 100, 2)

    def estimate_beta(self, code, benchmark="1.000300", skip=False):
        """从K线数据估算 Beta（1Y/3Y/5Y）。"""
        result = {
            "beta_1y": None, "beta_3y": None, "beta_5y": None,
            "industry_beta": None, "status": "success"
        }
        if skip:
            result["status"] = "skipped"
            return result
        try:
            secid = _get_secid(code)
            # 获取月度K线（5年约60个月）
            stock_klines = self._fetch_klines(secid, limit=65)
            bench_klines = self._fetch_klines(benchmark, limit=65)
            if not stock_klines or not bench_klines:
                result["status"] = "failed_no_kline_data"
                return result
            # 计算月度回报率
            stock_returns = self._compute_monthly_returns(stock_klines)
            bench_returns = self._compute_monthly_returns(bench_klines)
            # 对齐日期
            common_dates = sorted(set(stock_returns.keys()) & set(bench_returns.keys()))
            if len(common_dates) < 12:
                result["status"] = "failed_insufficient_data"
                return result
            # 计算不同期限的 Beta
            if len(common_dates) >= 12:
                dates_1y = common_dates[-12:]
                result["beta_1y"] = self._calc_beta(
                    [stock_returns[d] for d in dates_1y],
                    [bench_returns[d] for d in dates_1y]
                )
            if len(common_dates) >= 36:
                dates_3y = common_dates[-36:]
                result["beta_3y"] = self._calc_beta(
                    [stock_returns[d] for d in dates_3y],
                    [bench_returns[d] for d in dates_3y]
                )
            if len(common_dates) >= 60:
                dates_5y = common_dates[-60:]
                result["beta_5y"] = self._calc_beta(
                    [stock_returns[d] for d in dates_5y],
                    [bench_returns[d] for d in dates_5y]
                )
        except Exception as e:
            print(f"  [警告] Beta估算失败: {e}")
            result["status"] = f"failed: {e}"
        return result

    def _fetch_klines(self, secid, limit=65):
        """获取月度K线数据。"""
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={secid}&klt=103&fqt=1&beg=0&end=20500101"
            f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55&lmt={limit}"
        )
        data = http_get_json(url)
        if data and data.get("data") and data["data"].get("klines"):
            return data["data"]["klines"]
        return None

    def _compute_monthly_returns(self, klines):
        """从K线计算月度回报率。"""
        returns = {}
        prev_close = None
        for k in klines:
            parts = k.split(",")
            if len(parts) < 4:
                continue
            date_str = parts[0][:7]  # YYYY-MM
            close = _safe_float(parts[2])
            if close and prev_close and prev_close > 0:
                ret = (close - prev_close) / prev_close
                returns[date_str] = ret
            prev_close = close
        return returns

    def _calc_beta(self, stock_rets, bench_rets):
        """OLS 回归计算 Beta = Cov(Rs, Rm) / Var(Rm)。"""
        n = len(stock_rets)
        if n < 6:
            return None
        mean_s = sum(stock_rets) / n
        mean_b = sum(bench_rets) / n
        cov = sum((s - mean_s) * (b - mean_b) for s, b in zip(stock_rets, bench_rets)) / n
        var_b = sum((b - mean_b) ** 2 for b in bench_rets) / n
        if var_b == 0:
            return None
        return round(cov / var_b, 3)


# ============================================================
# 分红历史获取
# ============================================================

class DividendDataFetcher:
    """获取分红历史数据。"""

    def fetch_dividend_history(self, code, years=5):
        """获取最近 years 年的分红记录。"""
        result = {"records": [], "status": "failed"}
        try:
            url = (
                f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
                f"reportName=RPT_SHAREBONUS_DET&columns=ALL"
                f"&pageSize=50&pageNumber=1"
                f"&sortColumns=EX_DIVIDEND_DATE&sortTypes=-1"
                f"&filter=(SECURITY_CODE%3D%22{code}%22)"
            )
            data = http_get_json(url)
            if not data or not data.get("result") or not data["result"].get("data"):
                return result
            records = data["result"]["data"]
            # 过滤已实施的分红
            div_records = []
            seen_years = set()
            for r in records:
                progress = r.get("ASSIGN_PROGRESS", "")
                if "实施" not in str(progress) and "已" not in str(progress):
                    continue
                bonus = _safe_float(r.get("PRETAX_BONUS_RMB"))
                if bonus is None or bonus <= 0:
                    continue
                ex_date = r.get("EX_DIVIDEND_DATE", "") or r.get("REPORT_DATE", "")
                report_date = r.get("REPORT_DATE", "")
                year = int(report_date[:4]) if report_date else None
                if year and year in seen_years:
                    continue  # 每年只取一条
                if year:
                    seen_years.add(year)
                dps = round(bonus / 10, 4)  # 每10股 → 每股
                div_records.append({
                    "year": year,
                    "dps": dps,
                    "payout_ratio": None,  # 需要 EPS 才能算
                    "ex_date": ex_date[:10] if ex_date else None,
                })
                if len(div_records) >= years:
                    break
            div_records.sort(key=lambda x: x["year"] if x["year"] else 0)
            result["records"] = div_records
            result["status"] = "success" if div_records else "no_data"
        except Exception as e:
            print(f"  [警告] 分红历史获取失败: {e}")
        return result


# ============================================================
# 行业与可比公司数据
# ============================================================

class IndustryDataFetcher:
    """获取行业均值估值和可比公司数据。"""

    def fetch_industry_data(self, industry_name, code):
        """获取行业估值均值和可比公司。"""
        result = {
            "industry_name": industry_name,
            "industry_pe": None, "industry_pb": None,
            "industry_ev_ebitda": None, "industry_ps": None,
            "peers": [], "sector_code": None,
            "status": "failed"
        }
        if not industry_name:
            return result
        try:
            # 获取行业板块列表
            url = (
                "https://push2.eastmoney.com/api/qt/clist/get?"
                "pn=1&pz=100&fid=f3&po=1&np=1&fltt=2&invt=2"
                "&fs=m:90+t:2"
                "&fields=f2,f3,f4,f12,f14,f20,f62,f115,f128,f136,f184"
            )
            data = http_get_json(url)
            if data and data.get("data") and data["data"].get("diff"):
                for item in data["data"]["diff"]:
                    name = item.get("f14", "")
                    if industry_name in name or name in industry_name:
                        result["industry_pe"] = _safe_float(item.get("f115"))
                        result["sector_code"] = item.get("f12", "")
                        break
            # 获取同行业公司
            if result["sector_code"]:
                result["peers"] = self._fetch_peers(result["sector_code"], code)
                # 从同行计算行业均值 PB
                if result["peers"]:
                    pbs = [p["pb"] for p in result["peers"] if p.get("pb") and p["pb"] > 0]
                    if pbs:
                        result["industry_pb"] = round(sum(pbs) / len(pbs), 2)
                    pes = [p["pe"] for p in result["peers"] if p.get("pe") and 0 < p["pe"] < 200]
                    if pes:
                        if result["industry_pe"] is None:
                            result["industry_pe"] = round(sum(pes) / len(pes), 2)
            result["status"] = "success" if result["industry_pe"] else "partial"
        except Exception as e:
            print(f"  [警告] 行业数据获取失败: {e}")
        return result

    def _fetch_peers(self, sector_code, exclude_code, count=5):
        """获取同行业可比公司。"""
        peers = []
        try:
            url = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"pn=1&pz=15&fid=f20&po=1&np=1&fltt=2&invt=2"
                f"&fs=b:{sector_code}+f:!50"
                f"&fields=f2,f3,f9,f12,f14,f20,f23,f37"
            )
            data = http_get_json(url)
            if data and data.get("data") and data["data"].get("diff"):
                for item in data["data"]["diff"]:
                    peer_code = item.get("f12", "")
                    if peer_code == exclude_code:
                        continue
                    peers.append({
                        "code": peer_code,
                        "name": item.get("f14", ""),
                        "pe": _safe_float(item.get("f9")),
                        "pb": _safe_float(item.get("f23")),
                        "roe": _safe_float(item.get("f37")),
                        "market_cap": _safe_div(item.get("f20"), 1e8),
                    })
                    if len(peers) >= count:
                        break
        except Exception as e:
            print(f"  [警告] 可比公司获取失败: {e}")
        return peers

    def estimate_historical_valuation_range(self, code, years=5):
        """估算历史估值区间（PE/PB的高/低/均值/分位）。"""
        result = {
            "pe_range": {"high": None, "low": None, "mean": None, "current_percentile": None},
            "pb_range": {"high": None, "low": None, "mean": None, "current_percentile": None},
            "status": "failed"
        }
        try:
            secid = _get_secid(code)
            # 获取日K线（约1250个交易日=5年）
            url = (
                f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
                f"secid={secid}&klt=101&fqt=1&beg=0&end=20500101"
                f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55&lmt=1300"
            )
            data = http_get_json(url)
            if not data or not data.get("data") or not data["data"].get("klines"):
                return result
            klines = data["data"]["klines"]
            # 用日K线估算PE历史区间，需要当前PE和价格的关系
            # 简化方法：假设 EPS 在年内不变，用价格变化推算PE变化
            prices = []
            for k in klines:
                parts = k.split(",")
                if len(parts) >= 3:
                    close = _safe_float(parts[2])
                    if close:
                        prices.append(close)
            if not prices:
                return result
            current_price = prices[-1]
            price_high = max(prices)
            price_low = min(prices)
            price_mean = sum(prices) / len(prices)
            # 用价格比例推算PE/PB区间
            # 获取实时PE/PB作为基准
            quote = RealTimeQuote()
            rt = quote.fetch(code)
            current_pe = rt.get("pe_ttm")
            current_pb = rt.get("pb_mrq")
            if current_pe and current_pe > 0 and current_price > 0:
                ratio_high = price_high / current_price
                ratio_low = price_low / current_price
                ratio_mean = price_mean / current_price
                pe_high = round(current_pe * ratio_high, 1)
                pe_low = round(current_pe * ratio_low, 1)
                pe_mean = round(current_pe * ratio_mean, 1)
                # 当前分位
                below = sum(1 for p in prices if p <= current_price)
                percentile = round(below / len(prices), 2)
                result["pe_range"] = {
                    "high": pe_high, "low": pe_low,
                    "mean": pe_mean, "current_percentile": percentile
                }
            if current_pb and current_pb > 0 and current_price > 0:
                ratio_high = price_high / current_price
                ratio_low = price_low / current_price
                ratio_mean = price_mean / current_price
                pb_high = round(current_pb * ratio_high, 1)
                pb_low = round(current_pb * ratio_low, 1)
                pb_mean = round(current_pb * ratio_mean, 1)
                below = sum(1 for p in prices if p <= current_price)
                percentile = round(below / len(prices), 2)
                result["pb_range"] = {
                    "high": pb_high, "low": pb_low,
                    "mean": pb_mean, "current_percentile": percentile
                }
            result["status"] = "success"
        except Exception as e:
            print(f"  [警告] 历史估值区间估算失败: {e}")
        return result


# ============================================================
# 数据采集总编排
# ============================================================

class ValuationDataCollector:
    """编排所有数据采集，输出统一 JSON。"""

    def __init__(self, code, output_dir=".", periods=20, skip_beta=False):
        self.code = code
        self.output_dir = output_dir
        self.periods = periods
        self.skip_beta = skip_beta

    def collect_all(self):
        """采集全部数据。"""
        print(f"\n{'='*60}")
        print(f"  估值数据采集: {self.code}")
        print(f"{'='*60}\n")
        result = {
            "meta": {
                "code": self.code, "name": "", "industry": "",
                "market": "SH" if self.code[:2] in ("60", "68") else "SZ",
                "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "data_version": "1.0"
            }
        }
        # 1. 实时行情
        print("[1/7] 获取实时行情...")
        quote = RealTimeQuote()
        rt = quote.fetch(self.code)
        result["real_time"] = rt
        result["meta"]["name"] = rt.get("name", "")
        result["meta"]["industry"] = rt.get("industry", "")
        # 2. 财务报表
        print(f"\n[2/7] 获取财务报表（{self.periods}期）...")
        fetcher = FinancialDataFetcher()
        fin_data = fetcher.fetch_all(self.code, self.periods)
        # 构建利润表序列
        income_series = fetcher.build_income_series(fin_data["income"].get("records", []))
        result["income_data"] = {
            "annual_series": income_series,
            "status": "success" if fin_data["income"].get("records") else "failed"
        }
        # 构建资产负债表数据
        balance_built = fetcher.build_balance_data(fin_data["balance"].get("records", []))
        result["balance_data"] = balance_built
        # 构建现金流量表序列
        cashflow_series = fetcher.build_cashflow_series(fin_data["cashflow"].get("records", []))
        result["cashflow_data"] = {
            "annual_series": cashflow_series,
            "status": "success" if fin_data["cashflow"].get("records") else "failed"
        }
        # 计算衍生指标
        derived = fetcher.compute_derived_metrics(fin_data["income"], fin_data["balance"], fin_data["cashflow"])
        # 合并 ROE/ROA 到 balance_data
        if "annual_series" in result["balance_data"]:
            result["balance_data"]["annual_series"]["roe"] = derived["roe"]
            result["balance_data"]["annual_series"]["roa"] = derived["roa"]
            result["balance_data"]["annual_series"]["inventory_turnover_days"] = derived["inventory_turnover_days"]
        # 合并 EBITDA 到 income_data
        result["income_data"]["annual_series"]["ebitda"] = derived["ebitda"]
        # 合并营运资本变化到 cashflow_data
        result["cashflow_data"]["annual_series"]["working_capital_change"] = derived["working_capital_change"]
        # 用行情总股本补充 balance_data
        if rt.get("total_shares") and result["balance_data"].get("latest"):
            if not result["balance_data"]["latest"].get("total_shares"):
                result["balance_data"]["latest"]["total_shares"] = rt["total_shares"]
            # 重新计算BVPS
            total_eq = result["balance_data"]["latest"].get("total_equity")
            ts = result["balance_data"]["latest"].get("total_shares")
            if total_eq and ts and ts > 0:
                result["balance_data"]["latest"]["bvps"] = round(total_eq / ts, 2)
        # 补充分红的派息率
        eps_series = income_series.get("eps", [])
        # 3. WACC 输入
        print("\n[3/7] 获取WACC输入数据...")
        wacc_fetcher = WACCDataFetcher()
        rf_data = wacc_fetcher.fetch_risk_free_rate()
        kd = wacc_fetcher.estimate_kd(
            fin_data["income"].get("records", []),
            fin_data["balance"].get("records", [])
        )
        print(f"  Beta估算...", end=" ", flush=True)
        beta_data = wacc_fetcher.estimate_beta(self.code, skip=self.skip_beta)
        print(f"状态: {beta_data['status']}")
        debt_total = result["balance_data"].get("latest", {}).get("interest_bearing_debt", 0) or 0
        eq_cap = rt.get("total_market_cap", 0) or 0
        result["wacc_inputs"] = {
            "risk_free_rate": rf_data["value"],
            "rf_date": rf_data["date"],
            "kd": kd,
            "beta_values": {
                "beta_1y": beta_data.get("beta_1y"),
                "beta_3y": beta_data.get("beta_3y"),
                "beta_5y": beta_data.get("beta_5y"),
                "industry_beta": beta_data.get("industry_beta"),
            },
            "market_risk_premium": 6.0,
            "debt_total": debt_total,
            "equity_market_cap": eq_cap,
            "status": "success" if rf_data["value"] else "partial"
        }
        # 4. 分红历史
        print("\n[4/7] 获取分红历史...")
        div_fetcher = DividendDataFetcher()
        div_data = div_fetcher.fetch_dividend_history(self.code)
        # 补充派息率
        if div_data["records"] and eps_series:
            eps_by_year = {e["year"]: e["value"] for e in eps_series if e.get("year") and e.get("value")}
            for dr in div_data["records"]:
                if dr["year"] and dr["year"] in eps_by_year and eps_by_year[dr["year"]] > 0:
                    dr["payout_ratio"] = round(dr["dps"] / eps_by_year[dr["year"]] * 100, 1)
        result["dividend_history"] = div_data
        # 5. 行业数据
        print("\n[5/7] 获取行业数据...")
        ind_fetcher = IndustryDataFetcher()
        ind_data = ind_fetcher.fetch_industry_data(rt.get("industry", ""), self.code)
        result["industry_data"] = ind_data
        # 6. 历史估值区间
        print("\n[6/7] 估算历史估值区间...")
        hist_val = ind_fetcher.estimate_historical_valuation_range(self.code)
        result["historical_valuation"] = hist_val
        # 7. 管理层行为信号（简化版）
        print("\n[7/7] 管理层信号...")
        result["management_signals"] = {
            "insider_net_buy": None,
            "pledge_ratio_change": None,
            "audit_qualified": False,
            "restatement": False,
            "status": "partial"
        }
        # 数据质量评估
        result["data_quality"] = self._assess_quality(result)
        print(f"\n{'='*60}")
        print(f"  数据采集完成")
        avail = result["data_quality"].get("available_models", [])
        unavail = result["data_quality"].get("unavailable_models", {})
        print(f"  可用模型: {', '.join(avail)}")
        if unavail:
            print(f"  不可用模型: {unavail}")
        print(f"{'='*60}\n")
        return result

    def _assess_quality(self, data):
        """评估数据完整性，确定可用模型。"""
        c = {}
        c["real_time"] = data["real_time"].get("status") == "success"
        c["income_5y"] = len(data["income_data"]["annual_series"].get("revenue", [])) >= 3
        c["balance"] = data["balance_data"].get("status") == "success"
        c["cashflow_5y"] = len(data["cashflow_data"]["annual_series"].get("operating_cashflow", [])) >= 3
        c["wacc_inputs"] = data["wacc_inputs"].get("risk_free_rate") is not None
        c["bvps_roe"] = data["balance_data"].get("latest", {}).get("bvps") is not None
        c["ebitda"] = len(data["income_data"]["annual_series"].get("ebitda", [])) >= 1
        c["revenue_margin"] = c["income_5y"]
        c["dividend_history"] = len(data["dividend_history"].get("records", [])) >= 3
        c["segment_data"] = False
        c["qualitative_analysis"] = False
        available = []
        unavailable = {}
        if c["cashflow_5y"] and c["wacc_inputs"]:
            available.append("DCF")
        else:
            unavailable["DCF"] = "缺少历史现金流或WACC数据"
        if c["income_5y"]:
            available.append("PE")
        else:
            unavailable["PE"] = "缺少历史利润数据"
        if c["bvps_roe"]:
            available.append("PB")
        else:
            unavailable["PB"] = "缺少BVPS/ROE数据"
        if c["ebitda"]:
            available.append("EV_EBITDA")
        else:
            unavailable["EV_EBITDA"] = "缺少EBITDA数据"
        if c["revenue_margin"]:
            available.append("PS")
        else:
            unavailable["PS"] = "缺少收入/毛利率数据"
        if c["dividend_history"] and c["wacc_inputs"]:
            available.append("DDM")
        else:
            unavailable["DDM"] = "缺少股息历史或WACC数据"
        if c["segment_data"]:
            available.append("SOTP")
        else:
            unavailable["SOTP"] = "缺少分部数据"
        if c["real_time"] and c["wacc_inputs"] and c["cashflow_5y"]:
            available.append("REVERSE")
        else:
            unavailable["REVERSE"] = "缺少实时行情或WACC或现金流数据"
        return {
            "completeness": c,
            "available_models": available,
            "unavailable_models": unavailable,
            "warnings": []
        }

    def save(self, data):
        """保存数据到 JSON 文件。"""
        os.makedirs(self.output_dir, exist_ok=True)
        filename = f"{self.code}_valuation_data.json"
        filepath = os.path.join(self.output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        print(f"  数据已保存: {filepath}")
        return filepath


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="A股估值数据采集工具")
    parser.add_argument("--code", required=True, help="股票代码（如 600519）")
    parser.add_argument("--output-dir", default=".", help="输出目录")
    parser.add_argument("--periods", type=int, default=20, help="获取财报期数（默认20期=5年季报）")
    parser.add_argument("--skip-beta", action="store_true", help="跳过Beta估算（加速）")
    args = parser.parse_args()

    # 验证代码格式
    code = args.code.strip()
    if not code.isdigit() or len(code) != 6:
        print(f"错误: 股票代码必须为6位数字，当前输入: {code}")
        sys.exit(1)

    collector = ValuationDataCollector(
        code=code,
        output_dir=args.output_dir,
        periods=args.periods,
        skip_beta=args.skip_beta
    )
    data = collector.collect_all()
    collector.save(data)


if __name__ == "__main__":
    main()
