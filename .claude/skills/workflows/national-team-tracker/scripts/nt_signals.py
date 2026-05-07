#!/usr/bin/env python3
"""
national-team-tracker / Stage 3: 信号识别 + 操作建议

读取 stage1 snapshot.json + stage2 seasonal.json, 输出综合信号。

信号体系 (硬编码阈值, 来自 2024-2026 历史复盘):
  red    极强买入: ≥3 只宽基同日 Z≥2 + price_change_pct < +0.5%        (国家队系统性入场)
  orange 强买入:   单只 ratio_30/60 ≥ 1.3 + price_30d ≤ 0%             (持续吸纳)
  yellow 中性:     默认
  purple 顶部预警: 单日 Z≥2 + price_change_pct < 0% + price_30d ≥ +5%  (高位放量+ETF 走弱)
  black  极强减持: ≥3 只 Z≥3 + price_change_pct < -0.5% + price_30d ≥ +10% (顶部集中卖压)
  blue   产业信号: B_industry 类持仓有最新季度新增

时间窗口 (用户需求 1/7/30/季度):
  d1     当日 Z-Score + 涨跌幅 (push2 + push2his)
  d7     近 7 日累计成交额 / 7 日均价
  d30    近 30 日累计成交额 / 30 日均价
  quarter 季度净申购 (fundf10, 精确)

注: 日级总份额无公开接口, d1/d7/d30 用成交额作为资金活跃度代理。

Outputs:
  {output_dir}/signal.json
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timedelta

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))


# ============================================================
# 阈值常量 (来自 2024-2026 历史事件复盘)
# ============================================================
ZSCORE_STRONG = 2.0       # Z >= 2 视为异常放量 (≈ 2σ)
ZSCORE_EXTREME = 3.0      # Z >= 3 视为极端放量 (2026.1 国家队卖盘日)
PRICE_FLAT_THRESHOLD = 0.5    # ±0.5% 视为价格平稳
PRICE_TOP_RISE_30D = 5.0      # 30 日累涨 ≥ 5% = 高位
PRICE_TOP_BIG_RISE_30D = 10.0 # 30 日累涨 ≥ 10% = 显著高位
RATIO_30_60_HIGH = 1.3        # 30 日均 / 60 日均 ≥ 1.3 = 中期资金活跃
PURPLE_MIN_ETF_COUNT = 1      # 顶部预警最少触发 ETF 数
RED_MIN_ETF_COUNT = 3         # 极强买入最少触发 ETF 数
BLACK_MIN_ETF_COUNT = 3       # 极强减持最少触发 ETF 数


SIGNAL_LEVELS = ["red_strong_buy", "orange_buy", "yellow_neutral",
                 "purple_top_warning", "black_strong_sell", "blue_industry"]
SIGNAL_PRIORITY = {  # 数字越大越严重 / 越值得展示
    "yellow_neutral": 0,
    "blue_industry": 1,
    "orange_buy": 2,
    "red_strong_buy": 3,
    "purple_top_warning": 4,
    "black_strong_sell": 5,
}


def compute_recent_windows(kline_series, today_turnover_yi):
    """从 K 线计算 5/30/90 日详细切片。
    每窗口包含: sum, avg, vs_60d_ratio, price_change_pct, max_zscore_day(date,z,turnover)"""
    if not kline_series or len(kline_series) < 5:
        return {}
    # 60 日基准 (排除今日)
    base = kline_series[-61:-1] if len(kline_series) >= 61 else kline_series[:-1]
    base_turnovers = [s["turnover_yi"] for s in base]
    avg_60 = statistics.mean(base_turnovers) if base_turnovers else None
    std_60 = statistics.stdev(base_turnovers) if len(base_turnovers) >= 2 else None

    def _slice(n_days):
        if len(kline_series) < n_days:
            return None
        win = kline_series[-n_days:]
        turnovers = [s["turnover_yi"] for s in win]
        closes = [s["close"] for s in win]
        s_sum = sum(turnovers)
        s_avg = s_sum / len(turnovers)
        ratio = (s_avg / avg_60) if avg_60 else None
        price_chg = (closes[-1] / closes[0] - 1) * 100 if closes[0] else None
        # 找窗口内最大单日 Z-Score
        max_z_day = None
        if std_60 not in (None, 0) and avg_60 is not None:
            best_z = float("-inf")
            for s in win:
                z = (s["turnover_yi"] - avg_60) / std_60
                if z > best_z:
                    best_z = z
                    max_z_day = {"date": s["date"], "z": round(z, 2),
                                 "turnover_yi": round(s["turnover_yi"], 2),
                                 "close": s["close"]}
        # 价格区间
        price_high = max(closes)
        price_low = min(closes)
        return {
            "n_days": n_days,
            "turnover_sum_yi": round(s_sum, 2),
            "turnover_avg_yi": round(s_avg, 2),
            "vs_60d_ratio": round(ratio, 2) if ratio else None,
            "price_change_pct": round(price_chg, 2) if price_chg is not None else None,
            "price_high": price_high,
            "price_low": price_low,
            "max_zscore_day": max_z_day,
            "start_date": win[0]["date"],
            "end_date": win[-1]["date"],
        }

    return {
        "d5": _slice(5),
        "d30": _slice(30),
        "d90": _slice(90),
    }


def filter_recent_holder_changes(holders_holdings, days=90, today_str=None):
    """筛选近 N 天内国家队主体的持仓变动 (END_DATE >= today - N)."""
    today = datetime.strptime(today_str, "%Y-%m-%d") if today_str else datetime.now()
    cutoff = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for h in holders_holdings:
        if h.get("status") != "success":
            continue
        recent_stocks = [s for s in h.get("stocks", [])
                         if s.get("report_date", "") >= cutoff]
        if not recent_stocks:
            continue
        # 按日期降序
        recent_stocks.sort(key=lambda s: s.get("report_date", ""), reverse=True)
        out.append({
            "holder_name": h["holder_name"],
            "display_name": h.get("display_name"),
            "category": h.get("holder_category"),
            "recent_changes_count": len(recent_stocks),
            "recent_changes": recent_stocks[:15],  # top 15
        })
    return out



def classify_one_etf(snap_etf, quarters):
    """对单只 ETF 计算信号等级。返回 (signal_level, reasons)."""
    z = snap_etf.get("turnover_zscore")
    chg = snap_etf.get("change_pct")
    chg_30 = snap_etf.get("price_change_30d_pct")
    chg_7 = snap_etf.get("price_change_7d_pct")
    ratio_30_60 = snap_etf.get("turnover_30d_vs_60d_ratio")

    reasons = []
    level = "yellow_neutral"

    # 顶部预警: 高位 + 异常放量 + ETF 走弱 (国家队卖盘)
    if (z is not None and z >= ZSCORE_STRONG and
        chg is not None and chg < -PRICE_FLAT_THRESHOLD and
        chg_30 is not None and chg_30 >= PRICE_TOP_RISE_30D):
        level = "purple_top_warning"
        reasons.append(f"30日累涨{chg_30:.1f}% (高位)")
        reasons.append(f"今日 Z={z:.1f} (放量) 但 ETF 跌{chg:.1f}%")
        reasons.append("符合'国家队压盘'特征 (2026.1.14 同模式)")
        return level, reasons

    # 极强减持: 极放量 + 大跌 + 长涨
    if (z is not None and z >= ZSCORE_EXTREME and
        chg is not None and chg < -PRICE_FLAT_THRESHOLD and
        chg_30 is not None and chg_30 >= PRICE_TOP_BIG_RISE_30D):
        level = "black_strong_sell"
        reasons.append(f"30日累涨{chg_30:.1f}% (显著高位)")
        reasons.append(f"今日 Z={z:.1f} (极端放量) + 跌{chg:.1f}%")
        return level, reasons

    # 极强买入候选: 异常放量 + 价格平稳 (在低位/中位)
    if (z is not None and z >= ZSCORE_STRONG and
        chg is not None and -PRICE_FLAT_THRESHOLD <= chg < PRICE_FLAT_THRESHOLD and
        (chg_30 is None or chg_30 < PRICE_TOP_RISE_30D)):
        level = "red_strong_buy"
        reasons.append(f"今日 Z={z:.1f} (放量) 但 ETF 平开 {chg:+.1f}%")
        reasons.append("典型'国家队扛单'特征 (价不涨但量异常)")
        return level, reasons

    # 强买入: 中期资金活跃 + 价格未明显上涨
    if (ratio_30_60 is not None and ratio_30_60 >= RATIO_30_60_HIGH and
        (chg_30 is None or chg_30 <= 0)):
        level = "orange_buy"
        reasons.append(f"30日均成交 / 60日均 = {ratio_30_60:.2f} (中期活跃)")
        reasons.append(f"30日累涨 {chg_30 or 0:.1f}% (未抬升)")
        return level, reasons

    # 用季度数据补充: 最新季度大额净申购
    if quarters:
        latest_q = quarters[0]
        net_pur = latest_q.get("net_purchase_yi")
        if net_pur is not None and net_pur >= 30:
            level = "orange_buy"
            reasons.append(f"最新季报 ({latest_q.get('report_date')}) 净申购 {net_pur:+.1f} 亿份")
            return level, reasons
        if net_pur is not None and net_pur <= -30:
            # 季度大额赎回 + 当前还没有顶部预警, 只标 yellow + 备注
            reasons.append(f"季度净赎回 {net_pur:+.1f} 亿份 (国家队潜在退出)")

    if z is not None:
        reasons.append(f"今日 Z={z:.1f}, 30日累涨 {chg_30 or 0:.1f}%")
    return level, reasons


def compute_windows(snap_etf, quarters):
    """构造 windows 字段 (d1/d7/d30/quarter)."""
    z = snap_etf.get("turnover_zscore")
    t1 = snap_etf.get("turnover_yi")
    avg60 = snap_etf.get("turnover_60d_avg_yi")
    s7 = snap_etf.get("turnover_7d_sum_yi")
    s30 = snap_etf.get("turnover_30d_sum_yi")
    r7 = snap_etf.get("turnover_7d_vs_60d_ratio")
    r30 = snap_etf.get("turnover_30d_vs_60d_ratio")

    d1 = {
        "turnover_yi": t1,
        "turnover_zscore": z,
        "data_source": "push2_realtime + push2his_kline",
        "is_estimated": False,
        "note": "Z = (今日成交额 - 60日均) / 60日std",
    }
    d7 = {
        "turnover_sum_yi": s7,
        "turnover_avg_vs_60d": r7,
        "price_change_pct": snap_etf.get("price_change_7d_pct"),
        "data_source": "push2his_kline",
        "is_estimated": False,
        "note": "用累计成交额代理资金活跃度 (日级份额无公开接口)",
    }
    d30 = {
        "turnover_sum_yi": s30,
        "turnover_avg_vs_60d": r30,
        "price_change_pct": snap_etf.get("price_change_30d_pct"),
        "data_source": "push2his_kline",
        "is_estimated": False,
        "note": "用累计成交额代理资金活跃度",
    }
    quarter = {
        "net_purchase_yi": None,
        "purchase_yi": None,
        "redeem_yi": None,
        "report_date": None,
        "data_source": "fundf10_gmbd",
        "is_estimated": False,
        "note": "精确季度净申购",
    }
    if quarters:
        latest_q = quarters[0]
        quarter["net_purchase_yi"] = latest_q.get("net_purchase_yi")
        quarter["purchase_yi"] = latest_q.get("purchase_yi")
        quarter["redeem_yi"] = latest_q.get("redeem_yi")
        quarter["report_date"] = latest_q.get("report_date")
    return {"d1": d1, "d7": d7, "d30": d30, "quarter": quarter}


def aggregate_holders(holders_holdings):
    """从 holders_holdings 推断主体趋势 (基于 holder_state 字段统计)."""
    summaries = []
    for h in holders_holdings:
        if h.get("status") != "success":
            summaries.append({
                "holder_name": h["holder_name"],
                "display_name": h.get("display_name"),
                "stocks_count": h.get("stocks_count", 0),
                "trend": "unknown",
                "trend_evidence": h.get("error", "no data"),
            })
            continue
        states = [s.get("holder_state") for s in h.get("stocks", []) if s.get("holder_state")]
        # holder_state 可能是 "新进"/"加仓"/"减仓"/"不变"/"退出" 或 None
        accumulating = sum(1 for s in states if s in ("新进", "加仓", "increase"))
        trimming = sum(1 for s in states if s in ("减仓", "退出", "decrease"))
        stable = len(states) - accumulating - trimming
        if accumulating > trimming and accumulating >= 2:
            trend = "accumulating"
            ev = f"加仓/新进 {accumulating} 只 vs 减仓 {trimming} 只"
        elif trimming > accumulating and trimming >= 2:
            trend = "trimming"
            ev = f"减仓/退出 {trimming} 只 vs 加仓 {accumulating} 只"
        elif states:
            trend = "stable"
            ev = f"加仓 {accumulating}, 减仓 {trimming}, 不变 {stable}"
        else:
            trend = "unknown"
            ev = "持仓变动字段缺失"
        summaries.append({
            "holder_name": h["holder_name"],
            "display_name": h.get("display_name"),
            "stocks_count": h.get("stocks_count", 0),
            "trend": trend,
            "trend_evidence": ev,
        })
    return summaries


def overall_recommend(etf_signals, holders_summary):
    """综合判断 + 操作建议."""
    counts = {lv: 0 for lv in SIGNAL_LEVELS}
    for e in etf_signals:
        counts[e["signal_level"]] = counts.get(e["signal_level"], 0) + 1

    actions = []
    risks = []
    overall = "yellow_neutral"
    headline = "国家队动向中性, 维持既定策略"

    # 极强减持: 多只
    if counts["black_strong_sell"] >= BLACK_MIN_ETF_COUNT:
        overall = "black_strong_sell"
        headline = "国家队疑似集中减持高位宽基, 警惕短期顶"
        actions += [
            "重仓宽基 ETF 者考虑减半止盈",
            "观察接下来 3-5 日是否持续放量下跌",
            "切换至防御板块 (银行/公用事业)",
        ]
        risks.append("减持信号可能仅是单日噪音, 需观察持续性")
    # 顶部预警
    elif counts["purple_top_warning"] >= PURPLE_MIN_ETF_COUNT:
        overall = "purple_top_warning"
        headline = f"{counts['purple_top_warning']} 只宽基触发顶部预警 (高位放量+走弱)"
        actions += [
            "已重仓者考虑分批减持宽基 ETF",
            "新增仓位暂缓",
            "重点观察 510300/510050 的次日成交额是否延续",
        ]
        risks.append("单日预警可能误报, 建议结合下一交易日确认")
    # 极强买入: 多只
    elif counts["red_strong_buy"] >= RED_MIN_ETF_COUNT:
        overall = "red_strong_buy"
        headline = f"{counts['red_strong_buy']} 只宽基同日'放量+扛单', 国家队疑似系统性入场"
        actions += [
            "跟仓核心宽基 (510300 / 510050) 分批建仓",
            "定投策略可临时加速",
            "观察 7 日内是否有公告确认 (汇金/诚通)",
        ]
    # 单只极强买入或多只强买入
    elif counts["red_strong_buy"] >= 1 or counts["orange_buy"] >= 3:
        overall = "orange_buy"
        headline = "宽基 ETF 中期资金活跃, 国家队可能在持续吸纳"
        actions += [
            "现有持仓维持, 可小幅加仓",
            "重点观察持续性 (连续 5 日)",
        ]

    # 主体确认
    accumulating_holders = [h for h in holders_summary if h["trend"] == "accumulating"]
    trimming_holders = [h for h in holders_summary if h["trend"] == "trimming"]
    if accumulating_holders:
        actions.append(
            f"主体确认: {len(accumulating_holders)} 个国家队主体季报显示加仓 "
            f"({', '.join(h['display_name'] for h in accumulating_holders[:3])})"
        )
    if trimming_holders:
        actions.append(
            f"主体警示: {len(trimming_holders)} 个主体季报显示减仓 "
            f"({', '.join(h['display_name'] for h in trimming_holders[:3])})"
        )

    return {
        "overall_level": overall if overall != "blue_industry" else "yellow_neutral",
        "headline": headline,
        "actions": actions or ["按既定策略执行, 暂无强信号"],
        "risks": risks,
        "industry_pool_highlights": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-file", required=True)
    ap.add_argument("--seasonal-file", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--industry-pool", default=None)
    args = ap.parse_args()

    with open(args.snapshot_file, "r", encoding="utf-8") as f:
        snap = json.load(f)
    with open(args.seasonal_file, "r", encoding="utf-8") as f:
        seasonal = json.load(f)

    # 索引 quarters
    seasonal_idx = {}
    for s in seasonal.get("etf_seasonal", []):
        seasonal_idx[s["etf_code"]] = s

    etf_signals = []
    aggregate = {
        "broad_etfs_share_change_d1_yi": 0.0,
        "broad_etfs_share_change_d7_yi": None,
        "broad_etfs_share_change_d30_yi": None,
        "etfs_buying_count": 0,
        "etfs_selling_count": 0,
    }
    # 全池近期窗口聚合
    pool_recent = {"d5": {"sum_yi": 0.0, "avg_ratio": []},
                   "d30": {"sum_yi": 0.0, "avg_ratio": []},
                   "d90": {"sum_yi": 0.0, "avg_ratio": []}}
    today_str = snap.get("meta", {}).get("report_date") or datetime.now().strftime("%Y-%m-%d")

    for e in snap.get("etfs", []):
        code = e["etf_code"]
        sea = seasonal_idx.get(code, {})
        quarters = sea.get("quarters", [])
        level, reasons = classify_one_etf(e, quarters)
        windows = compute_windows(e, quarters)
        # 近期窗口详细切片 (基于 K 线)
        recent_windows = compute_recent_windows(e.get("kline_series") or [], e.get("turnover_yi"))
        # 池级聚合
        for k in ("d5", "d30", "d90"):
            w = recent_windows.get(k)
            if w:
                pool_recent[k]["sum_yi"] += w.get("turnover_sum_yi") or 0
                if w.get("vs_60d_ratio") is not None:
                    pool_recent[k]["avg_ratio"].append(w["vs_60d_ratio"])

        # 简易加总: 用季度净申购 / 季度交易日 (~63) × N
        if quarters and quarters[0].get("net_purchase_yi") is not None:
            daily_est = quarters[0]["net_purchase_yi"] / 63.0
            if aggregate["broad_etfs_share_change_d7_yi"] is None:
                aggregate["broad_etfs_share_change_d7_yi"] = 0.0
                aggregate["broad_etfs_share_change_d30_yi"] = 0.0
            aggregate["broad_etfs_share_change_d7_yi"] += daily_est * 7
            aggregate["broad_etfs_share_change_d30_yi"] += daily_est * 30
        if e.get("change_pct") is not None and e.get("turnover_yi") is not None:
            if e["change_pct"] > 0.3 and (e.get("turnover_zscore") or 0) > 0:
                aggregate["etfs_buying_count"] += 1
            elif e["change_pct"] < -0.3 and (e.get("turnover_zscore") or 0) > 0:
                aggregate["etfs_selling_count"] += 1

        h_struct = sea.get("holder_structure") or []
        latest_pct = h_struct[0].get("institutional_pct") if h_struct else None
        prev_pct = h_struct[1].get("institutional_pct") if len(h_struct) >= 2 else None
        change_pp = round(latest_pct - prev_pct, 2) if (latest_pct is not None and prev_pct is not None) else None

        etf_signals.append({
            "etf_code": code,
            "etf_name": e.get("etf_name"),
            "tracking_index": e.get("tracking_index"),
            "current_price": e.get("current_price"),
            "change_pct": e.get("change_pct"),
            "windows": windows,
            "recent_windows": recent_windows,  # 新增: 5/30/90 日详细
            "turnover_zscore": e.get("turnover_zscore"),
            "institutional_pct_latest": latest_pct,
            "institutional_pct_change_pp": change_pp,
            "signal_level": level,
            "signal_reasons": reasons,
        })

    # 池级近期汇总 (avg of ratios)
    for k in ("d5", "d30", "d90"):
        ratios = pool_recent[k]["avg_ratio"]
        pool_recent[k]["pool_avg_ratio_vs_60d"] = round(sum(ratios) / len(ratios), 2) if ratios else None
        pool_recent[k]["sum_yi"] = round(pool_recent[k]["sum_yi"], 2)
        pool_recent[k].pop("avg_ratio")

    # round aggregate
    for k in ("broad_etfs_share_change_d1_yi", "broad_etfs_share_change_d7_yi", "broad_etfs_share_change_d30_yi"):
        v = aggregate[k]
        if v is not None:
            aggregate[k] = round(v, 2)

    # 主体趋势
    holders_summary = aggregate_holders(seasonal.get("holders_holdings", []))
    aggregate["holders_summary"] = holders_summary
    aggregate["pool_recent_windows"] = pool_recent

    # 近 90 天国家队主体调仓 (重点)
    recent_holder_changes = filter_recent_holder_changes(
        seasonal.get("holders_holdings", []), days=90, today_str=today_str
    )
    aggregate["recent_holder_changes_90d"] = recent_holder_changes

    # 产业基金池规模 (从 industry_funds_pool.json 数)
    pool_path = args.industry_pool or os.path.join(
        os.path.dirname(__file__), "..", "config", "industry_funds_pool.json"
    )
    industry_count = 0
    if os.path.exists(pool_path):
        with open(pool_path, "r", encoding="utf-8") as f:
            ip = json.load(f)
        for catkey, cat in ip.items():
            if catkey.startswith("_"):
                continue
            if isinstance(cat, dict):
                for sub in cat.values():
                    if isinstance(sub, list):
                        industry_count += len(sub)
    aggregate["industry_funds_pool_count"] = industry_count

    # 综合建议
    rec = overall_recommend(etf_signals, holders_summary)

    # 添加产业题材 highlights (B 类如果有 trim/accumulate)
    b_holders = [h for h in holders_summary if "B_" in (
        next((hh.get("holder_category") for hh in seasonal.get("holders_holdings", [])
              if hh.get("holder_name") == h["holder_name"]), "") or "")]
    if b_holders:
        for h in b_holders:
            if h["trend"] == "accumulating":
                rec["industry_pool_highlights"].append(
                    f"B 类 {h['display_name']} 季报显示加仓 ({h['trend_evidence']})"
                )

    today = datetime.now().strftime("%Y-%m-%d")
    signal_doc = {
        "meta": {
            "analysis_time": datetime.now().isoformat(),
            "report_date": today,
        },
        "etf_signals": etf_signals,
        "aggregate": aggregate,
        "recommendation": rec,
        "status": "success",
        "errors": [],
    }

    os.makedirs(args.output_dir, exist_ok=True)
    out_file = os.path.join(args.output_dir, "signal.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(signal_doc, f, ensure_ascii=False, indent=2)
    print(f"[nt_signals] wrote {out_file}", file=sys.stderr)
    print(f"[nt_signals] overall: {rec['overall_level']} | {rec['headline']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
