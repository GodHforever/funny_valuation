#!/usr/bin/env python3
"""
A股实时行情与新闻轻量级数据获取工具。

零第三方依赖（仅用 Python 3.6+ 标准库），通过东方财富、腾讯、新浪等
公开 API 获取 A 股实时行情、新闻资讯、资金流向数据。

多级数据源回退确保稳定性：
  行情：东方财富 push2 → 腾讯财经 → 新浪财经
  新闻：东方财富搜索 → 东方财富快讯 → 东方财富公告
  资金：东方财富个股资金流 → 东方财富北向资金

用法:
    python realtime_fetcher.py --code 600519
    python realtime_fetcher.py --code 600519 --news
    python realtime_fetcher.py --code 600519 --full
    python realtime_fetcher.py --code 600519 --full --format json
    python realtime_fetcher.py --code 600519 --full --mode collaborative --output-dir ./out
"""

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
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

FETCH_TIMEOUT = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ============================================================
# 基础工具函数（独立运行时的内置实现）
# ============================================================

def _create_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _safe_float(val, default=None):
    if _HAS_SHARED:
        return _shared_safe_float(val, default)
    if val is None:
        return default
    try:
        v = float(val)
        return v if v == v else default
    except (ValueError, TypeError):
        return default


def _safe_div(a, b):
    a, b = _safe_float(a), _safe_float(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _get_secid(code):
    if _HAS_SHARED:
        return _shared_get_secid(code)
    prefix = code[:2]
    if prefix in ("60", "68"):
        return f"1.{code}"
    return f"0.{code}"


def http_get_json_with_retry(url, headers=None, timeout=FETCH_TIMEOUT,
                              max_retries=3, backoff_base=2):
    if _HAS_SHARED:
        return _shared_retry(url, headers=headers, timeout=timeout,
                             max_retries=max_retries, backoff_base=backoff_base)
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
                time.sleep(backoff_base ** attempt)
    return None, last_error


def http_get_text(url, headers=None, timeout=FETCH_TIMEOUT, encoding="utf-8",
                  max_retries=3, backoff_base=2):
    """GET 请求返回文本，带重试。"""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            ctx = _create_ssl_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode(encoding), None
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                time.sleep(backoff_base ** attempt)
    return None, last_error


# ============================================================
# 1. QuoteFetcher — 实时行情（3级回退）
# ============================================================

class QuoteFetcher:
    """
    A股实时行情获取，三级数据源回退：
    L1: 东方财富 push2（最全字段）
    L2: 腾讯财经 qt.gtimg.cn（盘口5档）
    L3: 新浪财经 hq.sinajs.cn（基础兜底）
    """

    def fetch(self, code):
        """获取实时行情，返回结构化 dict。"""
        result = self._empty_result(code)

        # L1: 东方财富 push2
        print("  [行情] L1 东方财富 push2...", end=" ", flush=True)
        l1 = self._fetch_eastmoney(code)
        if l1 and l1.get("current_price") is not None:
            result.update(l1)
            result["data_source"] = "eastmoney_push2"
            result["status"] = "success"
            print(f"成功 (¥{l1['current_price']})")
            return result
        print("失败")

        # L2: 腾讯财经
        print("  [行情] L2 腾讯财经...", end=" ", flush=True)
        l2 = self._fetch_tencent(code)
        if l2 and l2.get("current_price") is not None:
            result.update(l2)
            result["data_source"] = "tencent"
            result["status"] = "success"
            print(f"成功 (¥{l2['current_price']})")
            return result
        print("失败")

        # L3: 新浪财经
        print("  [行情] L3 新浪财经...", end=" ", flush=True)
        l3 = self._fetch_sina(code)
        if l3 and l3.get("current_price") is not None:
            result.update(l3)
            result["data_source"] = "sina"
            result["status"] = "success"
            print(f"成功 (¥{l3['current_price']})")
            return result
        print("失败")

        result["status"] = "failed"
        result["error"] = "所有数据源均未返回有效行情"
        print("  [行情] 全部数据源失败")
        return result

    def _empty_result(self, code):
        return {
            "code": code,
            "name": None,
            "current_price": None,
            "change_amount": None,
            "change_pct": None,
            "open": None,
            "high": None,
            "low": None,
            "yesterday_close": None,
            "volume": None,
            "turnover": None,
            "turnover_rate": None,
            "pe_ttm": None,
            "pb_mrq": None,
            "ps_ttm": None,
            "total_market_cap": None,
            "circulating_market_cap": None,
            "total_shares": None,
            "industry": None,
            "bid_ask": None,
            "data_source": None,
            "status": "pending",
            "error": None,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # --- L1: 东方财富 push2 ---
    def _fetch_eastmoney(self, code):
        secid = _get_secid(code)
        # 扩展字段：含盘口5档、成交额(f48)、换手率(f168)、最高(f44)、最低(f45)、今开(f46)、昨收(f60)
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get?"
            f"secid={secid}"
            f"&fields=f43,f44,f45,f46,f47,f48,f55,f57,f58,f60,f116,f117,"
            f"f127,f162,f163,f164,f167,f168,f169,f170,"
            f"f31,f32,f33,f34,f35,f36,f37,f38,f39,f40,"
            f"f19,f20,f17,f18,f15,f16,f13,f14,f11,f12"
        )
        data, err = http_get_json_with_retry(url)
        if err or not data or not data.get("data"):
            return None
        d = data["data"]

        raw_price = _safe_float(d.get("f43"))
        price = raw_price / 100 if raw_price else None
        # push2 字段: f44=最高, f45=最低, f46=今开, f60=昨收
        raw_high = _safe_float(d.get("f44"))
        raw_low = _safe_float(d.get("f45"))
        raw_open = _safe_float(d.get("f46"))
        raw_change = _safe_float(d.get("f169"))
        raw_change_pct = _safe_float(d.get("f170"))
        raw_pe = _safe_float(d.get("f162"))
        raw_pb = _safe_float(d.get("f163"))
        raw_ps = _safe_float(d.get("f164"))
        raw_turnover_rate = _safe_float(d.get("f168"))
        raw_yclose = _safe_float(d.get("f60"))

        # PE/PB/PS 交叉验证：push2 有时三个字段返回相同值，此时标记异常
        pe_val = raw_pe / 100 if raw_pe else None
        pb_val = raw_pb / 100 if raw_pb else None
        ps_val = raw_ps / 100 if raw_ps else None
        if raw_pe and raw_pb and raw_ps and raw_pe == raw_pb == raw_ps:
            # 三值相同说明数据异常，仅保留PE（最可能正确），PB/PS 置空
            pb_val = None
            ps_val = None

        # 盘口5档
        bid_ask = self._parse_eastmoney_bid_ask(d)

        result = {
            "name": d.get("f58", ""),
            "current_price": price,
            "change_amount": raw_change / 100 if raw_change else None,
            "change_pct": raw_change_pct / 100 if raw_change_pct else None,
            "open": raw_open / 100 if raw_open else None,
            "high": raw_high / 100 if raw_high else None,
            "low": raw_low / 100 if raw_low else None,
            "yesterday_close": raw_yclose / 100 if raw_yclose else None,
            "volume": _safe_float(d.get("f47")),
            "turnover": _safe_div(d.get("f48"), 1e8),
            "turnover_rate": raw_turnover_rate / 100 if raw_turnover_rate else None,
            "pe_ttm": pe_val,
            "pb_mrq": pb_val,
            "ps_ttm": ps_val,
            "total_market_cap": _safe_div(d.get("f116"), 1e8),
            "circulating_market_cap": _safe_div(d.get("f117"), 1e8),
            "total_shares": _safe_float(d.get("f55")),  # push2 f55 已是亿股
            "industry": d.get("f127", ""),
            "bid_ask": bid_ask,
        }
        return result

    def _parse_eastmoney_bid_ask(self, d):
        """解析东方财富盘口5档数据。"""
        bid_ask = {"bids": [], "asks": []}
        # 买盘: f11/f12(买1), f13/f14(买2), f15/f16(买3), f17/f18(买4), f19/f20(买5)
        bid_fields = [(f"f{11+i*2}", f"f{12+i*2}") for i in range(5)]
        for pf, vf in bid_fields:
            p = _safe_float(d.get(pf))
            v = _safe_float(d.get(vf))
            if p is not None:
                bid_ask["bids"].append({"price": p / 100, "volume": v})
        # 卖盘: f31/f32(卖1), f33/f34(卖2), f35/f36(卖3), f37/f38(卖4), f39/f40(卖5)
        ask_fields = [(f"f{31+i*2}", f"f{32+i*2}") for i in range(5)]
        for pf, vf in ask_fields:
            p = _safe_float(d.get(pf))
            v = _safe_float(d.get(vf))
            if p is not None:
                bid_ask["asks"].append({"price": p / 100, "volume": v})
        return bid_ask if (bid_ask["bids"] or bid_ask["asks"]) else None

    # --- L2: 腾讯财经 ---
    def _fetch_tencent(self, code):
        prefix = "sz" if code.startswith(("0", "3")) else "sh"
        url = f"https://qt.gtimg.cn/q={prefix}{code}"
        text, err = http_get_text(url, encoding="gbk")
        if err or not text:
            return None
        match = re.search(r'="(.+)"', text)
        if not match:
            return None
        fields = match.group(1).split("~")
        if len(fields) < 45:
            return None
        try:
            # 腾讯行情字段索引（0-based）
            # 1:name, 3:price, 4:yclose, 5:open, 6:volume(手), 7:外盘, 8:内盘
            # 9-28: 盘口5档(卖5价,卖5量,...,买1价,买1量)
            # 31:涨跌额, 32:涨跌%, 33:high, 34:low, 35:价格/成交量/成交额
            # 36:成交量(手), 37:成交额(万), 38:换手率
            # 39:PE, 43:最高, 44:最低
            result = {
                "name": fields[1],
                "current_price": _safe_float(fields[3]),
                "yesterday_close": _safe_float(fields[4]),
                "open": _safe_float(fields[5]),
                "volume": _safe_float(fields[36]),
                "turnover": _safe_div(fields[37], 1e4) if fields[37] else None,  # 万→亿
                "turnover_rate": _safe_float(fields[38]),
                "change_amount": _safe_float(fields[31]),
                "change_pct": _safe_float(fields[32]),
                "high": _safe_float(fields[33]),
                "low": _safe_float(fields[34]),
                "pe_ttm": _safe_float(fields[39]),
                "total_market_cap": _safe_div(fields[45], 1) if len(fields) > 45 else None,  # 已是亿
            }
            # 腾讯盘口5档
            bid_ask = self._parse_tencent_bid_ask(fields)
            if bid_ask:
                result["bid_ask"] = bid_ask
            return result
        except (IndexError, ValueError):
            return None

    def _parse_tencent_bid_ask(self, fields):
        """解析腾讯盘口5档。"""
        try:
            bid_ask = {"bids": [], "asks": []}
            # 买盘: 9(买1价),10(买1量), 11(买2价),12(买2量),...
            for i in range(5):
                p = _safe_float(fields[9 + i * 2])
                v = _safe_float(fields[10 + i * 2])
                if p:
                    bid_ask["bids"].append({"price": p, "volume": v})
            # 卖盘: 19(卖1价),20(卖1量), 21(卖2价),22(卖2量),...
            for i in range(5):
                p = _safe_float(fields[19 + i * 2])
                v = _safe_float(fields[20 + i * 2])
                if p:
                    bid_ask["asks"].append({"price": p, "volume": v})
            return bid_ask if (bid_ask["bids"] or bid_ask["asks"]) else None
        except (IndexError, ValueError):
            return None

    # --- L3: 新浪财经 ---
    def _fetch_sina(self, code):
        prefix = "sz" if code.startswith(("0", "3")) else "sh"
        url = f"https://hq.sinajs.cn/list={prefix}{code}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        text, err = http_get_text(url, headers=headers, encoding="gbk")
        if err or not text:
            return None
        match = re.search(r'="(.+)"', text)
        if not match:
            return None
        fields = match.group(1).split(",")
        if len(fields) < 32:
            return None
        try:
            # 新浪行情字段：0:name, 1:open, 2:yclose, 3:price, 4:high, 5:low
            # 6:买1价, 7:卖1价, 8:volume(股), 9:turnover(元)
            # 10-19: 买1-5量价, 20-29: 卖1-5量价
            result = {
                "name": fields[0],
                "current_price": _safe_float(fields[3]),
                "open": _safe_float(fields[1]),
                "yesterday_close": _safe_float(fields[2]),
                "high": _safe_float(fields[4]),
                "low": _safe_float(fields[5]),
                "volume": _safe_float(fields[8]),
                "turnover": _safe_div(fields[9], 1e8) if fields[9] else None,
            }
            yclose = _safe_float(fields[2])
            price = _safe_float(fields[3])
            if yclose and price and yclose > 0:
                result["change_amount"] = round(price - yclose, 4)
                result["change_pct"] = round((price - yclose) / yclose * 100, 2)
            # 新浪盘口5档
            bid_ask = {"bids": [], "asks": []}
            for i in range(5):
                bv = _safe_float(fields[10 + i * 2])
                bp = _safe_float(fields[11 + i * 2])
                if bp:
                    bid_ask["bids"].append({"price": bp, "volume": bv})
            for i in range(5):
                av = _safe_float(fields[20 + i * 2])
                ap = _safe_float(fields[21 + i * 2])
                if ap:
                    bid_ask["asks"].append({"price": ap, "volume": av})
            if bid_ask["bids"] or bid_ask["asks"]:
                result["bid_ask"] = bid_ask
            return result
        except (IndexError, ValueError):
            return None

    # --- 分时数据 ---
    def fetch_intraday(self, code, minutes=240):
        """获取当日分时数据（5分钟K线，比1分钟更稳定）。"""
        secid = _get_secid(code)
        # 优先尝试5分钟K线
        for klt, label in [(5, "5分钟"), (15, "15分钟"), (101, "日K")]:
            lmt = {5: 48, 15: 16, 101: 5}[klt]
            url = (
                f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
                f"secid={secid}&klt={klt}&fqt=1&beg=0&end=20500101"
                f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56&lmt={lmt}"
            )
            data, err = http_get_json_with_retry(url, max_retries=2, timeout=8)
            if data and data.get("data") and data["data"].get("klines"):
                klines = data["data"]["klines"]
                points = []
                for k in klines:
                    parts = k.split(",")
                    if len(parts) >= 6:
                        points.append({
                            "time": parts[0],
                            "open": _safe_float(parts[1]),
                            "close": _safe_float(parts[2]),
                            "high": _safe_float(parts[3]),
                            "low": _safe_float(parts[4]),
                            "volume": _safe_float(parts[5]),
                        })
                return {"points": points, "interval": label, "status": "success"}
        return {"points": [], "interval": None, "status": "failed", "error": "分时数据不可用"}


# ============================================================
# 2. NewsFetcher — 新闻资讯（3级回退）
# ============================================================

class NewsFetcher:
    """
    A股个股新闻获取，三级数据源回退：
    L1: 东方财富搜索API（按股票代码/名称搜索）
    L2: 东方财富财经快讯（滚动新闻流）
    L3: 东方财富公告API（公司公告）
    """

    def fetch(self, code, name=None, count=10):
        """获取新闻，返回 dict。"""
        news_list = []

        # L1: 东方财富搜索API
        print("  [新闻] L1 东方财富搜索...", end=" ", flush=True)
        l1 = self._fetch_eastmoney_search(code, name, count)
        if l1:
            news_list.extend(l1)
            print(f"成功 ({len(l1)}条)")
        else:
            print("失败")

        # L2: 东方财富财经快讯（补充到目标数量）
        remaining = count - len(news_list)
        if remaining > 0:
            print("  [新闻] L2 东方财富快讯...", end=" ", flush=True)
            l2 = self._fetch_eastmoney_kuaixun(code, remaining)
            if l2:
                news_list.extend(l2)
                print(f"成功 ({len(l2)}条)")
            else:
                print("失败")

        # L3: 东方财富公告（补充到目标数量）
        remaining = count - len(news_list)
        if remaining > 0:
            print("  [新闻] L3 东方财富公告...", end=" ", flush=True)
            l3 = self._fetch_eastmoney_notice(code, remaining)
            if l3:
                news_list.extend(l3)
                print(f"成功 ({len(l3)}条)")
            else:
                print("失败")

        # 去重
        news_list = self._deduplicate(news_list)

        return {
            "news": news_list[:count],
            "total": len(news_list),
            "status": "success" if news_list else "no_data",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # --- L1: 东方财富资讯API（按个股过滤） ---
    def _fetch_eastmoney_search(self, code, name=None, count=10):
        """通过东方财富资讯API获取个股新闻（与L2使用不同的pageIndex）。"""
        market = "1" if code[:2] in ("60", "68") else "0"
        # 使用资讯流API，获取更多页的数据以与L2互补
        url = (
            f"https://np-listapi.eastmoney.com/comm/wap/getListInfo?"
            f"client=wap&type=1&mTypeAndCode={market}.{code}"
            f"&pageSize={count}&pageIndex=1&callback=cb"
        )
        try:
            text, err = http_get_text(url)
            if err or not text:
                return None
            json_str = re.sub(r'^cb\(', '', text)
            json_str = re.sub(r'\);?\s*$', '', json_str)
            data = json.loads(json_str)
            if not data or not data.get("data") or not data["data"].get("list"):
                return None
            news = []
            for item in data["data"]["list"]:
                title = item.get("Art_Title", "")
                if not title:
                    continue
                news.append({
                    "title": title,
                    "time": item.get("Art_ShowTime", ""),
                    "source": item.get("Art_MediaName", "东方财富"),
                    "summary": "",
                    "url": item.get("Art_Url", ""),
                    "type": "news",
                })
            return news if news else None
        except Exception:
            return None

    # --- L2: 东方财富财经快讯 ---
    def _fetch_eastmoney_kuaixun(self, code, count=10):
        """从东方财富7x24快讯获取与股票相关的新闻。"""
        market = "1" if code[:2] in ("60", "68") else "0"
        url = (
            f"https://np-listapi.eastmoney.com/comm/wap/getListInfo?"
            f"client=wap&type=1&mTypeAndCode={market}.{code}"
            f"&pageSize={count}&pageIndex=0&callback=cb"
        )
        try:
            text, err = http_get_text(url)
            if err or not text:
                return None
            json_str = re.sub(r'^cb\(', '', text)
            json_str = re.sub(r'\);?\s*$', '', json_str)
            data = json.loads(json_str)
            if not data or not data.get("data") or not data["data"].get("list"):
                return None
            news = []
            for item in data["data"]["list"]:
                title = item.get("Art_Title", "")
                if not title:
                    continue
                news.append({
                    "title": title,
                    "time": item.get("Art_ShowTime", ""),
                    "source": item.get("Art_MediaName", "东方财富"),
                    "summary": "",
                    "url": item.get("Art_Url", ""),
                    "type": "kuaixun",
                })
            return news if news else None
        except Exception:
            return None

    # --- L3: 东方财富公告API ---
    def _fetch_eastmoney_notice(self, code, count=10):
        """从东方财富获取公司公告。"""
        url = (
            f"https://np-anotice-stock.eastmoney.com/api/security/ann?"
            f"stock_list={code}"
            f"&page_index=1&page_size={count}"
            f"&ann_type=A&client_source=web"
            f"&f_node=0&s_node=0"
        )
        try:
            data, err = http_get_json_with_retry(url)
            if err or not data or not data.get("data") or not data["data"].get("list"):
                return None
            news = []
            for item in data["data"]["list"]:
                title = item.get("title", "")
                if not title:
                    continue
                ann_url = ""
                if item.get("art_code"):
                    ann_url = f"https://data.eastmoney.com/notices/detail/{code}/{item['art_code']}.html"
                col_name = "公告"
                if item.get("columns") and len(item["columns"]) > 0:
                    col_name = item["columns"][0].get("column_name", "公告")
                news.append({
                    "title": title,
                    "time": item.get("display_time", "")[:19],
                    "source": col_name,
                    "summary": "",
                    "url": ann_url,
                    "type": "notice",
                })
            return news if news else None
        except Exception:
            return None

    def _deduplicate(self, news_list):
        """按标题哈希去重。"""
        seen = set()
        result = []
        for item in news_list:
            key = hashlib.md5(item["title"].encode("utf-8")).hexdigest()
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result


# ============================================================
# 3. FundFlowFetcher — 资金流向
# ============================================================

class FundFlowFetcher:
    """个股资金流向 + 北向资金。"""

    def fetch(self, code):
        """获取资金流向数据。"""
        result = {
            "stock_flow": self._fetch_stock_flow(code),
            "northbound": self._fetch_northbound(),
            "status": "success",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if result["stock_flow"]["status"] == "failed" and result["northbound"]["status"] == "failed":
            result["status"] = "failed"
        elif result["stock_flow"]["status"] == "failed" or result["northbound"]["status"] == "failed":
            result["status"] = "partial"
        return result

    def _fetch_stock_flow(self, code):
        """获取个股主力资金流向（使用 fflow/kline API）。"""
        secid = _get_secid(code)
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?"
            f"secid={secid}&klt=1&lmt=1"
            f"&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
        )
        data, err = http_get_json_with_retry(url)
        if err or not data or not data.get("data") or not data["data"].get("klines"):
            return {"status": "failed", "error": err or "无资金流数据"}
        latest = data["data"]["klines"][-1]
        parts = latest.split(",")
        if len(parts) < 6:
            return {"status": "failed", "error": "资金流数据格式异常"}
        # fflow/kline 字段顺序：时间, 主力净流入, 小单, 中单, 大单, 超大单
        main_net = _safe_float(parts[1])
        small_net = _safe_float(parts[2])
        medium_net = _safe_float(parts[3])
        large_net = _safe_float(parts[4])
        super_large_net = _safe_float(parts[5])
        return {
            "time": parts[0],
            "main_net_inflow": round(main_net / 1e8, 4) if main_net is not None else None,
            "super_large_net": round(super_large_net / 1e8, 4) if super_large_net is not None else None,
            "large_net": round(large_net / 1e8, 4) if large_net is not None else None,
            "medium_net": round(medium_net / 1e8, 4) if medium_net is not None else None,
            "small_net": round(small_net / 1e8, 4) if small_net is not None else None,
            "status": "success",
        }

    def _fetch_northbound(self):
        """获取北向资金当日净流入。"""
        url = (
            "https://push2his.eastmoney.com/api/qt/kamt.kline/get?"
            "fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
            "&klt=101&lmt=3"
            "&ut=b05f3c3b5174f8be"
        )
        data, err = http_get_json_with_retry(url)
        if err or not data or not data.get("data"):
            return {"status": "failed", "error": err}
        d = data["data"]
        result = {
            "sh_connect": None,
            "sz_connect": None,
            "total": None,
            "date": None,
            "status": "failed",
        }
        # 取最新一天的数据
        for key in ["s2n"]:
            klines = d.get(key, {}).get("klines", []) if isinstance(d.get(key), dict) else []
            if klines:
                latest = klines[-1]
                parts = latest.split(",")
                if len(parts) >= 4:
                    result["date"] = parts[0]
                    result["sh_connect"] = _safe_div(parts[1], 1e4)   # 万→亿
                    result["sz_connect"] = _safe_div(parts[2], 1e4)
                    result["total"] = _safe_div(parts[3], 1e4)
                    result["status"] = "success"
                break
        return result


# ============================================================
# 4. 输出模块
# ============================================================

def format_markdown(quote_data, news_data=None, fund_data=None, intraday_data=None):
    """生成 Markdown 格式快报。"""
    lines = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 标题
    name = quote_data.get("name", "")
    code = quote_data.get("code", "")
    lines.append(f"# {name}({code}) 实时快报")
    lines.append(f"\n> 生成时间: {ts}")
    lines.append(f"> 数据源: {quote_data.get('data_source', 'N/A')}")

    # 行情概览
    if quote_data.get("status") == "success":
        lines.append("\n## 行情概览\n")
        price = quote_data.get("current_price")
        change = quote_data.get("change_pct")
        change_str = f"+{change}%" if change and change > 0 else f"{change}%" if change else "N/A"
        arrow = "🔴" if change and change < 0 else "🟢" if change and change > 0 else "⚪"

        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 当前价 | ¥{price} {arrow} {change_str} |")
        lines.append(f"| 今开 | ¥{quote_data.get('open', 'N/A')} |")
        lines.append(f"| 最高 | ¥{quote_data.get('high', 'N/A')} |")
        lines.append(f"| 最低 | ¥{quote_data.get('low', 'N/A')} |")
        lines.append(f"| 昨收 | ¥{quote_data.get('yesterday_close', 'N/A')} |")
        vol = quote_data.get('volume')
        vol_str = f"{vol/10000:.0f}万手" if vol and vol > 10000 else f"{vol}手" if vol else "N/A"
        lines.append(f"| 成交量 | {vol_str} |")
        to = quote_data.get('turnover')
        to_str = f"{to:.2f}亿" if to else "N/A"
        lines.append(f"| 成交额 | {to_str} |")
        tr = quote_data.get('turnover_rate')
        lines.append(f"| 换手率 | {f'{tr}%' if tr else 'N/A'} |")

        lines.append(f"\n### 估值指标\n")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        pe = quote_data.get('pe_ttm')
        lines.append(f"| PE(TTM) | {f'{pe:.2f}倍' if pe else 'N/A'} |")
        pb = quote_data.get('pb_mrq')
        lines.append(f"| PB(MRQ) | {f'{pb:.2f}倍' if pb else 'N/A'} |")
        ps = quote_data.get('ps_ttm')
        lines.append(f"| PS(TTM) | {f'{ps:.2f}倍' if ps else 'N/A'} |")
        mc = quote_data.get('total_market_cap')
        lines.append(f"| 总市值 | {f'{mc:.2f}亿' if mc else 'N/A'} |")
        cmc = quote_data.get('circulating_market_cap')
        lines.append(f"| 流通市值 | {f'{cmc:.2f}亿' if cmc else 'N/A'} |")

        # 盘口
        bid_ask = quote_data.get("bid_ask")
        if bid_ask:
            lines.append(f"\n### 盘口五档\n")
            lines.append(f"| 档位 | 买盘价 | 买盘量 | 卖盘价 | 卖盘量 |")
            lines.append(f"|------|--------|--------|--------|--------|")
            bids = bid_ask.get("bids", [])
            asks = bid_ask.get("asks", [])
            for i in range(5):
                bp = f"¥{bids[i]['price']}" if i < len(bids) else "-"
                bv = str(bids[i].get('volume', '')) if i < len(bids) else "-"
                ap = f"¥{asks[i]['price']}" if i < len(asks) else "-"
                av = str(asks[i].get('volume', '')) if i < len(asks) else "-"
                lines.append(f"| {i+1} | {bp} | {bv} | {ap} | {av} |")

    # 资金流向
    if fund_data and fund_data.get("status") != "failed":
        lines.append(f"\n## 资金流向\n")
        sf = fund_data.get("stock_flow", {})
        if sf.get("status") == "success":
            lines.append(f"### 个股资金 ({sf.get('time', '')})\n")
            lines.append(f"| 类型 | 净流入(亿) |")
            lines.append(f"|------|-----------|")
            for label, nk in [
                ("主力", "main_net_inflow"),
                ("超大单", "super_large_net"),
                ("大单", "large_net"),
                ("中单", "medium_net"),
                ("小单", "small_net"),
            ]:
                nv = sf.get(nk)
                nv_str = f"{nv:.4f}" if nv is not None else "N/A"
                lines.append(f"| {label} | {nv_str} |")

        nb = fund_data.get("northbound", {})
        if nb.get("status") == "success":
            lines.append(f"\n### 北向资金 ({nb.get('date', '')})\n")
            lines.append(f"| 通道 | 净流入(亿) |")
            lines.append(f"|------|-----------|")
            sh = nb.get("sh_connect")
            sz = nb.get("sz_connect")
            total = nb.get("total")
            lines.append(f"| 沪股通 | {f'{sh:.2f}' if sh is not None else 'N/A'} |")
            lines.append(f"| 深股通 | {f'{sz:.2f}' if sz is not None else 'N/A'} |")
            lines.append(f"| **合计** | **{f'{total:.2f}' if total is not None else 'N/A'}** |")

    # 新闻
    if news_data and news_data.get("news"):
        lines.append(f"\n## 最新资讯 ({len(news_data['news'])}条)\n")
        for i, item in enumerate(news_data["news"], 1):
            title = item.get("title", "")
            t = item.get("time", "")
            src = item.get("source", "")
            url = item.get("url", "")
            ntype = item.get("type", "")
            type_tag = {"news": "📰", "kuaixun": "⚡", "notice": "📋"}.get(ntype, "📄")
            if url:
                lines.append(f"{i}. {type_tag} [{title}]({url})")
            else:
                lines.append(f"{i}. {type_tag} {title}")
            meta_parts = []
            if t:
                meta_parts.append(t)
            if src:
                meta_parts.append(src)
            if meta_parts:
                lines.append(f"   *{' | '.join(meta_parts)}*")
            if item.get("summary"):
                lines.append(f"   > {item['summary'][:100]}")

    lines.append(f"\n---")
    lines.append(f"*免责声明：以上数据仅供参考，不构成投资建议。*")
    return "\n".join(lines)


def build_json_output(code, quote_data, news_data=None, fund_data=None, intraday_data=None):
    """构建结构化 JSON 输出。"""
    output = {
        "meta": {
            "code": code,
            "name": quote_data.get("name", ""),
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "data_version": "1.0",
        },
        "quote": quote_data,
    }
    if news_data:
        output["news"] = news_data
    if fund_data:
        output["fund_flow"] = fund_data
    if intraday_data:
        output["intraday"] = intraday_data
    return output


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="A股实时行情与新闻轻量级获取工具")
    parser.add_argument("--code", required=True, help="股票代码（如 600519）")
    parser.add_argument("--news", action="store_true", help="同时获取新闻")
    parser.add_argument("--news-only", action="store_true", help="仅获取新闻")
    parser.add_argument("--count", type=int, default=10, help="新闻条数（默认10）")
    parser.add_argument("--fund-flow", action="store_true", help="同时获取资金流向")
    parser.add_argument("--intraday", action="store_true", help="同时获取分时数据")
    parser.add_argument("--full", action="store_true", help="获取全部数据（行情+新闻+资金流+分时）")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    parser.add_argument("--output-dir", default=None, help="输出目录（默认输出到控制台）")
    parser.add_argument("--mode", choices=["standalone", "collaborative"], default="standalone",
                        help="运行模式")
    args = parser.parse_args()

    code = args.code.strip()
    if not code.isdigit() or len(code) != 6:
        print(f"错误: 股票代码必须为6位数字，当前输入: {code}")
        sys.exit(1)

    if args.full:
        args.news = True
        args.fund_flow = True
        args.intraday = True

    print(f"\n{'='*50}")
    print(f"  A股实时快报: {code}")
    print(f"{'='*50}\n")

    quote_data = None
    news_data = None
    fund_data = None
    intraday_data = None

    # 行情
    if not args.news_only:
        print("[1] 获取实时行情...")
        quote_fetcher = QuoteFetcher()
        quote_data = quote_fetcher.fetch(code)
    else:
        quote_data = QuoteFetcher()._empty_result(code)

    # 新闻
    if args.news or args.news_only:
        print(f"\n[2] 获取新闻资讯 (目标{args.count}条)...")
        news_fetcher = NewsFetcher()
        stock_name = quote_data.get("name") if quote_data else None
        news_data = news_fetcher.fetch(code, name=stock_name, count=args.count)

    # 资金流
    if args.fund_flow:
        print("\n[3] 获取资金流向...")
        fund_fetcher = FundFlowFetcher()
        fund_data = fund_fetcher.fetch(code)
        sf = fund_data.get("stock_flow", {})
        if sf.get("status") == "success":
            mn = sf.get("main_net_inflow")
            print(f"  主力净流入: {f'{mn:.4f}亿' if mn is not None else 'N/A'}")
        nb = fund_data.get("northbound", {})
        if nb.get("status") == "success":
            print(f"  北向资金: {nb.get('total', 'N/A')}亿 ({nb.get('date', '')})")

    # 分时
    if args.intraday:
        print("\n[4] 获取分时数据...")
        quote_fetcher = QuoteFetcher()
        intraday_data = quote_fetcher.fetch_intraday(code)
        pts = len(intraday_data.get("points", []))
        print(f"  分时数据点: {pts}")

    print(f"\n{'='*50}")

    # 输出
    if args.format == "json":
        json_output = build_json_output(code, quote_data, news_data, fund_data, intraday_data)
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(args.output_dir, f"{code}_realtime_{ts}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(json_output, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n  JSON 已保存: {filepath}")
        else:
            print(json.dumps(json_output, ensure_ascii=False, indent=2, default=str))
    else:
        md = format_markdown(quote_data, news_data, fund_data, intraday_data)
        if args.output_dir:
            os.makedirs(args.output_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = os.path.join(args.output_dir, f"{code}_realtime_{ts}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"\n  报告已保存: {filepath}")
            # 同时保存JSON
            json_output = build_json_output(code, quote_data, news_data, fund_data, intraday_data)
            json_path = os.path.join(args.output_dir, f"{code}_realtime_{ts}.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_output, f, ensure_ascii=False, indent=2, default=str)
            print(f"  JSON 已保存: {json_path}")
        else:
            print(md)


if __name__ == "__main__":
    main()
