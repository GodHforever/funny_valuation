#!/usr/bin/env python3
"""
共享 HTTP 工具函数。
供 data_fetcher.py 和 valuation_data.py 等脚本使用。

仅需 Python 3.6+，零第三方依赖。
"""

import json
import ssl
import time
import urllib.request

# ============================================================
# 常量
# ============================================================

FETCH_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EASTMONEY_REPORT_MAP = {
    "income": "RPT_DMSK_FN_INCOME",
    "balance": "RPT_DMSK_FN_BALANCE",
    "cashflow": "RPT_DMSK_FN_CASHFLOW",
}


# ============================================================
# SSL
# ============================================================

def _create_ssl_context():
    """创建 SSL 上下文（跳过证书验证，适用于企业内网环境）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ============================================================
# HTTP
# ============================================================

def http_get_json_with_retry(url, headers=None, timeout=FETCH_TIMEOUT,
                             max_retries=3, backoff_base=2):
    """
    GET 请求并解析 JSON，带指数退避重试。

    返回 (data_dict, error_str) 元组：
    - 成功时: (parsed_json, None)
    - 失败时: (None, "错误描述")

    与原有 http_get_json 返回签名兼容（成功时直接返回 data_dict）。
    """
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
# 数据工具
# ============================================================

def _safe_float(val, default=None):
    """安全转换浮点数，处理 None/NaN/非数字。"""
    if val is None:
        return default
    try:
        v = float(val)
        return v if v == v else default  # NaN check
    except (ValueError, TypeError):
        return default


def _find_value(record, keys):
    """从记录中查找第一个可用值（尝试多个可能的键名）。"""
    if not record:
        return None
    for key in keys:
        val = record.get(key)
        if val is not None:
            return _safe_float(val)
    return None


def _get_secid(code):
    """根据股票代码前缀判断交易所，生成东方财富 secid。

    规则:
    - 60/68 开头 -> 上交所 (1.{code})
    - 0/3 开头   -> 深交所 (0.{code})
    - 其他       -> 默认深交所
    """
    prefix = code[:2]
    if prefix in ("60", "68"):
        return f"1.{code}"
    return f"0.{code}"
