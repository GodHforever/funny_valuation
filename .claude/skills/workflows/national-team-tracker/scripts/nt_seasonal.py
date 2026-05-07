#!/usr/bin/env python3
"""
national-team-tracker / Stage 2: 季度规模 + 持有人结构 + 国家队主体反查

数据源 (零依赖):
  fundf10 gmbd: 季度规模(申购/赎回/总份额/净资产)
  fundf10 cyrjg: 半年持有人结构(机构/个人/内部 + 总份额)
  datacenter RPT_F10_EH_HOLDERS: 反查国家队主体的所有 A 股持仓

Outputs:
  {output_dir}/seasonal.json
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
sys.path.insert(0, _REPO_ROOT)
try:
    from scripts.http_utils import http_get_json_with_retry, USER_AGENT, _create_ssl_context
except ImportError:
    print(f"ERROR: cannot import scripts.http_utils from {_REPO_ROOT}", file=sys.stderr)
    sys.exit(3)


GMBD_URL_FMT = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&mode=0&code={code}"
CYRJG_URL_FMT = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=cyrjg&code={code}"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def http_get_text(url, headers=None, timeout=15):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
            return resp.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return None, str(e)


def _parse_table_rows(html_str):
    """从 fundf10 返回的 HTML 解析 <tbody> 内行 -> [[cells, ...], ...]"""
    rows = []
    # 抓 tbody
    tbody_m = re.search(r"<tbody>(.+?)</tbody>", html_str, re.DOTALL)
    body = tbody_m.group(1) if tbody_m else html_str
    for tr_m in re.finditer(r"<tr[^>]*>(.+?)</tr>", body, re.DOTALL):
        tds = re.findall(r"<td[^>]*>(.+?)</td>", tr_m.group(1), re.DOTALL)
        # strip tags + entities
        cells = []
        for td in tds:
            t = re.sub(r"<[^>]+>", "", td).strip()
            t = html.unescape(t)
            cells.append(t)
        if cells:
            rows.append(cells)
    return rows


def _to_float(s, strip="%,"):
    if s in (None, "", "--"):
        return None
    for ch in strip:
        s = s.replace(ch, "")
    try:
        return float(s)
    except ValueError:
        return None


def fetch_gmbd(code):
    """季度规模 (申购/赎回/总份额/净资产/变动率)。

    HTML 表头: 日期 | 期间申购 | 期间赎回 | 期末总份额 | 期末净资产 | 净资产变动率
    """
    url = GMBD_URL_FMT.format(code=code)
    text, err = http_get_text(url, headers={"Referer": "https://fundf10.eastmoney.com/"})
    if err:
        return None, f"gmbd HTTP failed: {err}"
    rows = _parse_table_rows(text)
    out = []
    for cells in rows:
        if len(cells) < 6:
            continue
        out.append({
            "report_date": cells[0],
            "purchase_yi": _to_float(cells[1]),
            "redeem_yi": _to_float(cells[2]),
            "total_share_yi": _to_float(cells[3]),
            "net_assets_yi": _to_float(cells[4]),
            "nav_change_pct": _to_float(cells[5]),
        })
        # 计算净申购
        if out[-1]["purchase_yi"] is not None and out[-1]["redeem_yi"] is not None:
            out[-1]["net_purchase_yi"] = round(
                out[-1]["purchase_yi"] - out[-1]["redeem_yi"], 2
            )
        else:
            out[-1]["net_purchase_yi"] = None
    return out, None


def fetch_cyrjg(code):
    """半年持有人结构 (机构/个人/内部)。

    HTML 表头: 公告日期 | 机构持有比例 | 个人持有比例 | 内部持有比例 | 总份额(亿份)
    """
    url = CYRJG_URL_FMT.format(code=code)
    text, err = http_get_text(url, headers={"Referer": "https://fundf10.eastmoney.com/"})
    if err:
        return None, f"cyrjg HTTP failed: {err}"
    rows = _parse_table_rows(text)
    out = []
    for cells in rows:
        if len(cells) < 4:
            continue
        out.append({
            "report_date": cells[0],
            "institutional_pct": _to_float(cells[1]),
            "individual_pct": _to_float(cells[2]),
            "internal_pct": _to_float(cells[3]),
        })
    return out, None


def fetch_holder_holdings(holder_name, max_rows=50):
    """反查 datacenter: 一个国家队主体的所有 A 股持仓 (按最新季报)."""
    params = {
        "reportName": "RPT_F10_EH_HOLDERS",
        "columns": "SECUCODE,SECURITY_NAME_ABBR,HOLDER_NAME,HOLD_NUM,HOLD_NUM_RATIO,HOLDER_STATE,END_DATE",
        "filter": f"(HOLDER_NAME=\"{holder_name}\")",
        "pageNumber": "1",
        "pageSize": str(max_rows),
        "sortColumns": "END_DATE,HOLD_NUM",
        "sortTypes": "-1,-1",
    }
    qs = "&".join(f"{k}={urllib.request.quote(v)}" for k, v in params.items())
    url = f"{DATACENTER_URL}?{qs}"
    data, err = http_get_json_with_retry(url, timeout=20, max_retries=3)
    if err:
        return None, f"datacenter failed: {err}"
    result = (data or {}).get("result") or {}
    rows = result.get("data") or []
    return rows, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--etf-pool", default=None)
    ap.add_argument("--holders-dict", default=None)
    ap.add_argument("--extra-etfs", default="")
    ap.add_argument("--holders-filter", default="all")
    ap.add_argument("--max-quarters", type=int, default=8, help="保留最近 N 个季度数据")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    pool_path = args.etf_pool or os.path.join(
        os.path.dirname(__file__), "..", "config", "etf_pool.json"
    )
    holders_path = args.holders_dict or os.path.join(
        os.path.dirname(__file__), "..", "config", "holders_dict.json"
    )
    with open(pool_path, "r", encoding="utf-8") as f:
        pool = json.load(f)
    with open(holders_path, "r", encoding="utf-8") as f:
        holders_cfg = json.load(f)

    default_codes = pool.get("_default_pool", [])
    extra = [c.strip() for c in args.extra_etfs.split(",") if c.strip()]
    target_codes = list(dict.fromkeys(default_codes + extra))

    code_meta = {}
    for sec in pool.get("broad_index_etfs", []) + pool.get("growth_etfs", []):
        code_meta[sec["code"]] = sec

    # ETF 季度数据
    etf_seasonal = []
    success_count = 0
    for code in target_codes:
        meta = code_meta.get(code) or {"name": code}
        print(f"[nt_seasonal] etf {code} ...", file=sys.stderr)
        gmbd, gerr = fetch_gmbd(code)
        cyrjg, cerr = fetch_cyrjg(code)
        rec = {
            "etf_code": code,
            "etf_name": meta.get("name"),
            "quarters": (gmbd or [])[: args.max_quarters],
            "holder_structure": (cyrjg or [])[:6],
            "status": "success" if (gmbd and cyrjg) else (
                "partial" if (gmbd or cyrjg) else "failed"
            ),
            "error": "; ".join(filter(None, [gerr, cerr])) or None,
        }
        if rec["status"] == "success":
            success_count += 1
        etf_seasonal.append(rec)

    # 国家队主体反查
    holders_holdings = []
    a_class = holders_cfg.get("A_class_capital_market_stabilizer", {})
    b_class = holders_cfg.get("B_class_industry_funds", {})
    selected = []
    for hkey, h in a_class.items():
        if hkey.startswith("_"):
            continue
        if args.holders_filter not in ("all", hkey):
            continue
        for sname in h.get("search_names", []):
            selected.append((sname, h.get("category", "A_other"), h.get("display_name")))
    if args.holders_filter == "all":
        for hkey, h in b_class.items():
            if hkey.startswith("_"):
                continue
            for sname in h.get("search_names", []):
                selected.append((sname, h.get("category", "B_industry"), h.get("display_name")))

    for sname, cat, dname in selected:
        print(f"[nt_seasonal] holder reverse-lookup: {sname} ...", file=sys.stderr)
        rows, err = fetch_holder_holdings(sname, max_rows=80)
        if err:
            holders_holdings.append({
                "holder_name": sname,
                "holder_category": cat,
                "display_name": dname,
                "stocks_count": 0,
                "stocks": [],
                "status": "failed",
                "error": err,
                "latest_report_date": None,
            })
            continue
        # 提取最新季报对应的持仓 (按 END_DATE 取最大 + tie-break)
        latest_date = None
        for r in rows:
            d = r.get("END_DATE")
            if d and (latest_date is None or d > latest_date):
                latest_date = d
        latest_date_only = (latest_date or "").split(" ")[0]
        # 同时收集本季度内的全部持仓 (datacenter 同一季报里同一主体可能多个不同 END_DATE)
        latest_stocks = []
        for r in rows:
            d = (r.get("END_DATE") or "").split(" ")[0]
            if not d:
                continue
            # 只保留最近 90 天内的最新一批
            latest_stocks.append({
                "stock_code": (r.get("SECUCODE") or "").split(".")[0],
                "stock_name": r.get("SECURITY_NAME_ABBR"),
                "hold_num": r.get("HOLD_NUM"),
                "hold_ratio": r.get("HOLD_NUM_RATIO"),
                "holder_state": r.get("HOLDER_STATE"),
                "report_date": d,
            })
        # 按 stock_code 去重, 保留最新 report_date
        dedup = {}
        for s in latest_stocks:
            k = s["stock_code"]
            if k not in dedup or s["report_date"] > dedup[k]["report_date"]:
                dedup[k] = s
        stocks = sorted(dedup.values(), key=lambda x: -(x.get("hold_num") or 0))
        holders_holdings.append({
            "holder_name": sname,
            "holder_category": cat,
            "display_name": dname,
            "stocks_count": len(stocks),
            "stocks": stocks[:30],  # 只保留前 30 大
            "status": "success",
            "error": None,
            "latest_report_date": latest_date_only or None,
        })

    today = datetime.now().strftime("%Y-%m-%d")
    seasonal_doc = {
        "meta": {
            "pull_time": datetime.now().isoformat(),
            "report_date": today,
        },
        "etf_seasonal": etf_seasonal,
        "holders_holdings": holders_holdings,
        "history_window": {
            "available_dates": [],
            "etfs_history": [],
            "_note": "已弃用: 日级份额无公开接口, 改用 nt_realtime.py 的 60 日成交额时序作代理"
        },
        "status": "success" if success_count >= max(6, int(len(target_codes) * 0.75)) else (
            "partial" if success_count > 0 else "failed"
        ),
        "errors": [],
    }

    if args.probe:
        json.dump(seasonal_doc, sys.stdout, ensure_ascii=False, indent=2)
        print(f"\n[probe] etfs {success_count}/{len(target_codes)} | holders {len(holders_holdings)}",
              file=sys.stderr)
        return 0 if seasonal_doc["status"] == "success" else 1

    os.makedirs(args.output_dir, exist_ok=True)
    out_file = os.path.join(args.output_dir, "seasonal.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(seasonal_doc, f, ensure_ascii=False, indent=2)
    print(f"[nt_seasonal] wrote {out_file} ({success_count}/{len(target_codes)} ETFs, "
          f"{len([h for h in holders_holdings if h['status']=='success'])}/{len(holders_holdings)} holders OK)",
          file=sys.stderr)

    if seasonal_doc["status"] == "success":
        return 0
    elif seasonal_doc["status"] == "partial":
        return 1
    else:
        return 2


if __name__ == "__main__":
    sys.exit(main())
