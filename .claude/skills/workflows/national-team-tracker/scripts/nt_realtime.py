#!/usr/bin/env python3
"""
national-team-tracker / Stage 1: 实时盘中行情 + 60 日成交额时序 + 最新已披露份额

国家队动向监测的核心信号:
  - 当日成交额异常 (Z-Score) → 盘中入场/退出信号
  - 7/30 日累计成交额变化 → 中期资金活跃度
  - 价格走势 vs 成交额 → 资金扛单识别 (价格不涨而成交额暴增 = 国家队入场)

数据源 (零依赖, 仅 Python 3.6+ urllib):
  L1: 东方财富 push2 (实时行情)
  L2: 东方财富 push2his K线 (60 日成交额时序)
  L3: 东方财富 fundf10 jbgk HTML (最新已披露份额, 季度)

注意: 集思录 etf_list 接口对未登录用户限制 20 行/页, 无法全市场抓取, 已弃用。
日级总份额无任何公开 API, 改用日级成交额作为代理信号。

Outputs:
  {output_dir}/snapshot.json (国家队 ETF 池实时 + 60 日时序 + 最新已披露份额)

Exit codes:
  0 = success  1 = partial (≥6/8 only)  2 = failed (<6/8)  3 = arg error
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from datetime import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)
try:
    from scripts.http_utils import http_get_json_with_retry, USER_AGENT, _create_ssl_context
except ImportError:
    print(f"ERROR: cannot import scripts.http_utils from {_REPO_ROOT}", file=sys.stderr)
    sys.exit(3)


PUSH2_URL = "https://push2.eastmoney.com/api/qt/stock/get"
PUSH2_FIELDS = "f43,f47,f48,f57,f58,f60"
PUSH2HIS_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
JBGK_URL_FMT = "http://fundf10.eastmoney.com/jbgk_{code}.html"


def http_get_text(url, headers=None, timeout=15):
    """GET 返回 text (HTML 页面用), 用于 fundf10 jbgk."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
            raw = resp.read()
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    return raw.decode(enc), None
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="ignore"), None
    except Exception as e:
        return None, str(e)


def fetch_push2_realtime(secid):
    """L1: push2 实时行情。"""
    url = f"{PUSH2_URL}?secid={secid}&fields={PUSH2_FIELDS}"
    headers = {"Referer": "https://quote.eastmoney.com/"}
    data, err = http_get_json_with_retry(url, headers=headers, timeout=15, max_retries=3)
    if err:
        return None, f"push2 failed: {err}"
    d = (data or {}).get("data") or {}
    if not d.get("f57"):
        return None, "push2 returned empty data"
    raw_price = d.get("f43")
    raw_pre = d.get("f60")
    price = raw_price / 1000.0 if raw_price else None
    pre_close = raw_pre / 1000.0 if raw_pre else None
    change_pct = None
    if price is not None and pre_close not in (None, 0):
        change_pct = (price - pre_close) / pre_close * 100
    turnover_yuan = d.get("f48")
    return {
        "etf_code": d.get("f57"),
        "etf_name": d.get("f58"),
        "current_price": round(price, 4) if price else None,
        "pre_close": round(pre_close, 4) if pre_close else None,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "volume_lots": d.get("f47"),
        "turnover_yi": round(turnover_yuan / 1e8, 4) if turnover_yuan else None,
    }, None


def fetch_kline_120d(secid):
    """L2: push2his 120 日 K 线 (足够算 5/30/60/90 日切片), 返回每日 (date, close, turnover_yi) 列表。"""
    url = (
        f"{PUSH2HIS_URL}?secid={secid}&klt=101&fqt=1&end=20990101&lmt=120"
        f"&fields1=f1,f2,f3,f4,f5"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
    )
    headers = {"Referer": "https://quote.eastmoney.com/"}
    data, err = http_get_json_with_retry(url, headers=headers, timeout=15, max_retries=3)
    if err:
        return None, f"kline failed: {err}"
    klines = ((data or {}).get("data") or {}).get("klines") or []
    series = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 7:
            try:
                series.append({
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "turnover_yi": round(float(parts[6]) / 1e8, 4),
                })
            except (ValueError, IndexError):
                continue
    if not series:
        return None, "kline empty"
    return series, None


def fetch_jbgk_share(code):
    """L3: fundf10 jbgk HTML 解析最新已披露份额 (季度)。

    返回 {latest_share_yi, latest_net_assets_yi, share_disclosure_date} 或 (None, err)
    """
    url = JBGK_URL_FMT.format(code=code)
    html, err = http_get_text(url, timeout=15)
    if err:
        return None, f"jbgk failed: {err}"

    # HTML 里有两处"亿份": (a) 成立时规模, (b) 当前份额规模
    # 使用上下文标签精确匹配 "份额规模" 字段的值
    m_assets = re.search(r"净资产规模[^<]*</th>\s*<td[^>]*>([\d,]+\.?\d*)亿元（截止至：(\d{4})-?(\d{2})-?(\d{2})", html)
    m_share = re.search(r"份额规模[^<]*</th>\s*<td[^>]*>(?:<[^>]+>)?([\d,]+\.?\d+)亿份", html)
    if not m_assets:
        # 兼容旧版页面格式 "（截止至：2025年12月31日）"
        m_assets = re.search(
            r"净资产规模[^<]*</th>\s*<td[^>]*>([\d,]+\.?\d*)亿元（截止至：(\d{4})年(\d{2})月(\d{2})日",
            html,
        )
    out = {
        "latest_share_yi": None,
        "latest_net_assets_yi": None,
        "share_disclosure_date": None,
    }
    if m_assets:
        try:
            out["latest_net_assets_yi"] = float(m_assets.group(1).replace(",", ""))
            out["share_disclosure_date"] = f"{m_assets.group(2)}-{m_assets.group(3)}-{m_assets.group(4)}"
        except (ValueError, IndexError):
            pass
    if m_share:
        try:
            out["latest_share_yi"] = float(m_share.group(1).replace(",", ""))
        except ValueError:
            pass
    if out["latest_share_yi"] is None and out["latest_net_assets_yi"] is None:
        return None, "jbgk HTML parse: no share/assets found"
    return out, None


def compute_kline_metrics(series, today_turnover_yi):
    """从 120 日 K 线计算各项统计指标 (用最近 60 日做基准)。"""
    if not series or len(series) < 5:
        return {}
    # 用最近 60 日做基准 (排除今日)
    base = series[-61:-1] if len(series) >= 61 else series[:-1]
    turnovers_base = [s["turnover_yi"] for s in base]
    closes = [s["close"] for s in series]
    turnovers = [s["turnover_yi"] for s in series]

    avg_60 = statistics.mean(turnovers_base) if turnovers_base else None
    std_60 = statistics.stdev(turnovers_base) if len(turnovers_base) >= 2 else None

    today_t = today_turnover_yi if today_turnover_yi is not None else turnovers[-1]
    zscore = None
    if avg_60 is not None and std_60 not in (None, 0):
        zscore = (today_t - avg_60) / std_60

    sum_7 = sum(turnovers[-7:]) if len(turnovers) >= 7 else None
    sum_30 = sum(turnovers[-30:]) if len(turnovers) >= 30 else None
    avg_7 = sum_7 / 7 if sum_7 is not None else None
    avg_30 = sum_30 / 30 if sum_30 is not None else None
    ratio_7 = (avg_7 / avg_60) if (avg_7 is not None and avg_60) else None
    ratio_30 = (avg_30 / avg_60) if (avg_30 is not None and avg_60) else None

    pchg_7 = pchg_30 = None
    if len(closes) >= 8:
        pchg_7 = (closes[-1] / closes[-8] - 1) * 100
    if len(closes) >= 31:
        pchg_30 = (closes[-1] / closes[-31] - 1) * 100

    return {
        "turnover_60d_avg_yi": round(avg_60, 2) if avg_60 else None,
        "turnover_60d_std_yi": round(std_60, 2) if std_60 else None,
        "turnover_zscore": round(zscore, 2) if zscore is not None else None,
        "turnover_7d_sum_yi": round(sum_7, 2) if sum_7 is not None else None,
        "turnover_30d_sum_yi": round(sum_30, 2) if sum_30 is not None else None,
        "turnover_7d_vs_60d_ratio": round(ratio_7, 2) if ratio_7 is not None else None,
        "turnover_30d_vs_60d_ratio": round(ratio_30, 2) if ratio_30 is not None else None,
        "price_change_7d_pct": round(pchg_7, 2) if pchg_7 is not None else None,
        "price_change_30d_pct": round(pchg_30, 2) if pchg_30 is not None else None,
    }


def fetch_one_etf(meta):
    """对一只 ETF 完整抓取: 实时 + 60 日 K + 最新份额."""
    code = meta["code"]
    secid = meta["secid"]
    rec = {
        "etf_code": code,
        "etf_name": meta.get("name"),
        "tracking_index": meta.get("tracking_index"),
        "status": "success",
        "error": None,
    }
    errors = []

    # L1 实时
    rt, err = fetch_push2_realtime(secid)
    if err:
        errors.append(f"L1: {err}")
    elif rt:
        for k in ("current_price", "pre_close", "change_pct", "volume_lots", "turnover_yi"):
            rec[k] = rt.get(k)

    # L2 K 线 120 日 (写入 snapshot 给下游做近期窗口切片)
    series, err = fetch_kline_120d(secid)
    if err:
        errors.append(f"L2: {err}")
    else:
        rec["kline_series"] = series  # 保留! 下游 nt_signals 会用
        m = compute_kline_metrics(series, rec.get("turnover_yi"))
        rec.update(m)

    # L3 最新份额 (HTML 解析)
    sh, err = fetch_jbgk_share(code)
    if err:
        errors.append(f"L3: {err}")
    elif sh:
        rec.update(sh)

    if errors:
        rec["status"] = "partial" if (rec.get("current_price") or rec.get("turnover_zscore")) else "failed"
        rec["error"] = "; ".join(errors)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--etf-pool", default=None)
    ap.add_argument("--extra-etfs", default="")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    pool_path = args.etf_pool or os.path.join(
        os.path.dirname(__file__), "..", "config", "etf_pool.json"
    )
    with open(pool_path, "r", encoding="utf-8") as f:
        pool = json.load(f)

    default_codes = pool.get("_default_pool", [])
    extra = [c.strip() for c in args.extra_etfs.split(",") if c.strip()]
    target_codes = list(dict.fromkeys(default_codes + extra))

    code_meta = {}
    for sec in pool.get("broad_index_etfs", []) + pool.get("growth_etfs", []):
        code_meta[sec["code"]] = sec

    etfs_out = []
    success_count = 0
    for i, code in enumerate(target_codes):
        if i > 0:
            time.sleep(0.6)  # 平滑限流: push2his 短时间多次会被拒
        meta = code_meta.get(code)
        if not meta:
            secid = f"1.{code}" if code.startswith(("60", "68", "5")) else f"0.{code}"
            meta = {"code": code, "secid": secid}
        print(f"[nt_realtime] {code} ({meta['secid']}) ...", file=sys.stderr)
        rec = fetch_one_etf(meta)
        if rec["status"] == "success":
            success_count += 1
        # kline_series 保留给下游
        etfs_out.append(rec)

    today = datetime.now().strftime("%Y-%m-%d")
    snapshot_doc = {
        "meta": {
            "pull_time": datetime.now().isoformat(),
            "report_date": today,
            "data_sources": ["eastmoney_push2", "eastmoney_push2his_kline", "eastmoney_fundf10_jbgk"],
        },
        "etfs": etfs_out,
        "status": ("success" if success_count >= max(6, int(len(target_codes) * 0.75))
                   else ("partial" if success_count > 0 else "failed")),
        "errors": [],
    }

    if args.probe:
        json.dump(snapshot_doc, sys.stdout, ensure_ascii=False, indent=2)
        print(f"\n[probe] {success_count}/{len(target_codes)} ETFs OK", file=sys.stderr)
        return 0 if snapshot_doc["status"] == "success" else 1

    os.makedirs(args.output_dir, exist_ok=True)
    snap_file = os.path.join(args.output_dir, "snapshot.json")
    with open(snap_file, "w", encoding="utf-8") as f:
        json.dump(snapshot_doc, f, ensure_ascii=False, indent=2)
    print(f"[nt_realtime] wrote {snap_file} ({success_count}/{len(target_codes)} OK)", file=sys.stderr)

    if snapshot_doc["status"] == "success":
        return 0
    elif snapshot_doc["status"] == "partial":
        return 1
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
