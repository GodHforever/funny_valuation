#!/usr/bin/env python3
"""
national-team-tracker / Stage 4: 图文报告 (近期详细 + 长期简要)

报告结构 (用户校准后):
  1. 综合判断 + 操作建议       (核心结论)
  2. 近 5 日动向 (详细)        ← 第一重点
  3. 近 30/90 日资金面 (详细)  ← 第二重点
  4. 近 90 天国家队主体调仓     ← 第三重点 (datacenter 反查里的近期数据)
  5. 长期趋势参考 (简要)        ← 辅助: 季度净申购、机构占比时序
  6. B 类产业基金题材池
  7. 数据来源 & 局限
"""

import argparse
import json
import os
import sys
from datetime import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

HAS_MPL = False
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    pass


SIGNAL_BADGE = {
    "red_strong_buy": "🔴 极强买入",
    "orange_buy": "🟠 强买入",
    "yellow_neutral": "🟡 中性",
    "purple_top_warning": "🟣 顶部预警",
    "black_strong_sell": "⚫ 极强减持",
    "blue_industry": "🔵 产业信号",
}


def fmt_n(v, prec=2, default="-"):
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return f"{v:,.{prec}f}"
    return str(v)


def fmt_signed(v, prec=2, default="-"):
    if v is None:
        return default
    return f"{v:+,.{prec}f}"


# ============================================================
# 第 1 章: 综合判断
# ============================================================
def render_overall(signal):
    rec = signal.get("recommendation", {})
    md = []
    md.append(f"### {SIGNAL_BADGE.get(rec.get('overall_level'), rec.get('overall_level', '?'))}")
    md.append(f"\n> {rec.get('headline', '-')}\n")
    md.append("**操作建议**\n")
    for a in rec.get("actions", []):
        md.append(f"- {a}")
    if rec.get("risks"):
        md.append("\n**风险提示**\n")
        for r in rec.get("risks", []):
            md.append(f"- ⚠️ {r}")
    return "\n".join(md)


# ============================================================
# 第 2 章: 近 5 日动向 (详细)
# ============================================================
def render_d5_detail(signal):
    """对每只 ETF 展开近 5 日详细数据。"""
    md = []
    pool = signal.get("aggregate", {}).get("pool_recent_windows", {}).get("d5") or {}
    md.append(f"### 全池近 5 日资金活跃度概览\n")
    md.append(f"- **池内 8 只 ETF 近 5 日累计成交额**: **{fmt_n(pool.get('sum_yi'), 1)} 亿元**")
    md.append(f"- **平均日均成交 vs 60 日均比率**: **{fmt_n(pool.get('pool_avg_ratio_vs_60d'), 2)}** (>1.0 = 资金活跃, <1.0 = 资金清淡)")
    md.append("")

    md.append("### 各 ETF 近 5 日明细\n")
    md.append("| 代码 | 名称 | 5日累计(亿) | 5日均/60日均 | 5日价格变动 | 5日内最高 Z 日 | 信号 |")
    md.append("|---|---|---|---|---|---|---|")
    for e in signal.get("etf_signals", []):
        rw = (e.get("recent_windows") or {}).get("d5") or {}
        max_z = rw.get("max_zscore_day") or {}
        z_str = "-"
        if max_z:
            z_str = f"{max_z.get('date')}: Z={fmt_signed(max_z.get('z'))}, 成交{fmt_n(max_z.get('turnover_yi'),1)}亿"
        md.append(
            f"| {e['etf_code']} | {e['etf_name']} | "
            f"{fmt_n(rw.get('turnover_sum_yi'), 1)} | "
            f"{fmt_n(rw.get('vs_60d_ratio'), 2)} | "
            f"{fmt_signed(rw.get('price_change_pct'))}% | "
            f"{z_str} | "
            f"{SIGNAL_BADGE.get(e['signal_level'], e['signal_level'])} |"
        )
    md.append("")
    md.append("**解读要点**:")
    md.append("- 比率 ≥ 1.5 + 价格平稳 = 国家队疑似入场 (扛单特征)")
    md.append("- 比率 ≥ 2.0 + 价格走弱 = 国家队疑似减持 (压盘特征)")
    md.append("- 5 日内最高 Z 日 = 该窗口内单日成交额最异常的一天, 可能是国家队动作触发日")
    return "\n".join(md)


# ============================================================
# 第 3 章: 近 30/90 日资金面 (详细)
# ============================================================
def render_d30_d90_detail(signal):
    md = []
    agg = signal.get("aggregate", {}).get("pool_recent_windows", {})
    p30 = agg.get("d30") or {}
    p90 = agg.get("d90") or {}

    md.append("### 全池近 30 / 90 日累计概览\n")
    md.append("| 窗口 | 累计成交额(亿) | 池内日均/60日均 | 解读 |")
    md.append("|---|---|---|---|")
    md.append(
        f"| 近 30 日 | {fmt_n(p30.get('sum_yi'), 0)} | {fmt_n(p30.get('pool_avg_ratio_vs_60d'), 2)} | "
        f"{'活跃' if (p30.get('pool_avg_ratio_vs_60d') or 0) > 1.1 else ('清淡' if (p30.get('pool_avg_ratio_vs_60d') or 0) < 0.9 else '正常')} |"
    )
    md.append(
        f"| 近 90 日 | {fmt_n(p90.get('sum_yi'), 0)} | {fmt_n(p90.get('pool_avg_ratio_vs_60d'), 2)} | "
        f"{'活跃' if (p90.get('pool_avg_ratio_vs_60d') or 0) > 1.1 else ('清淡' if (p90.get('pool_avg_ratio_vs_60d') or 0) < 0.9 else '正常')} |"
    )
    md.append("")

    md.append("### 各 ETF 近 30 / 90 日明细\n")
    md.append("| 代码 | 名称 | 30日累计(亿) | 30日均/60日均 | 30日价变 | 90日累计(亿) | 90日均/60日均 | 90日价变 | 90日内最高 Z 日 |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for e in signal.get("etf_signals", []):
        rw = e.get("recent_windows") or {}
        d30 = rw.get("d30") or {}
        d90 = rw.get("d90") or {}
        max_z90 = d90.get("max_zscore_day") or {}
        z_str = (f"{max_z90.get('date')}: Z={fmt_signed(max_z90.get('z'))}"
                 if max_z90 else "-")
        md.append(
            f"| {e['etf_code']} | {e['etf_name']} | "
            f"{fmt_n(d30.get('turnover_sum_yi'), 0)} | "
            f"{fmt_n(d30.get('vs_60d_ratio'), 2)} | "
            f"{fmt_signed(d30.get('price_change_pct'))}% | "
            f"{fmt_n(d90.get('turnover_sum_yi'), 0)} | "
            f"{fmt_n(d90.get('vs_60d_ratio'), 2)} | "
            f"{fmt_signed(d90.get('price_change_pct'))}% | "
            f"{z_str} |"
        )
    return "\n".join(md)


# ============================================================
# 第 4 章: 近 90 天国家队主体调仓 (datacenter 反查里的近期数据)
# ============================================================
def render_recent_holder_changes(signal):
    changes = signal.get("aggregate", {}).get("recent_holder_changes_90d", []) or []
    md = []
    if not changes:
        md.append("_近 90 天 datacenter 暂无国家队主体披露持仓变动 (可能是季报披露窗口未到)_")
        return "\n".join(md)
    total_records = sum(c.get("recent_changes_count", 0) for c in changes)
    md.append(f"近 90 天 datacenter 反查到 **{len(changes)} 个主体** 共 **{total_records} 条** 持仓披露记录:\n")
    for c in changes:
        cat_emoji = "💰" if c.get("category", "").startswith("A_") else "🏭"
        md.append(f"#### {cat_emoji} {c.get('display_name')} ({c['holder_name']})")
        md.append(f"近 90 天披露 {c['recent_changes_count']} 条变动:\n")
        md.append("| 披露日 | 股票 | 持仓股数 | 持股比例 | 状态 |")
        md.append("|---|---|---|---|---|")
        for s in c["recent_changes"]:
            hn = s.get("hold_num")
            hn_str = f"{hn / 1e8:.2f}亿" if isinstance(hn, (int, float)) and abs(hn) >= 1e7 else fmt_n(hn, 0)
            md.append(
                f"| {s.get('report_date')} | "
                f"{s.get('stock_code')} {s.get('stock_name')} | "
                f"{hn_str} | "
                f"{fmt_n(s.get('hold_ratio'), 2)}% | "
                f"{s.get('holder_state') or '-'} |"
            )
        md.append("")
    return "\n".join(md)


# ============================================================
# 第 5 章: 长期趋势参考 (简要)
# ============================================================
def render_long_term_brief(seasonal):
    md = []
    md.append("> 以下数据为长周期辅助参照, 用于判断当前近期信号在历史中的位置。详细分析请见上方近期章节。\n")

    # 季度净申购 (合并简表 - 只展示最近 4 季 + 累计)
    md.append("### 季度净申购 (近 4 季汇总)\n")
    etfs = seasonal.get("etf_seasonal", [])
    dates_set = set()
    for e in etfs:
        for q in (e.get("quarters") or [])[:4]:
            dates_set.add(q.get("report_date"))
    dates = sorted(dates_set, reverse=True)[:4]
    if dates:
        md.append("| ETF | " + " | ".join(dates) + " | 4季累计 |")
        md.append("|---|" + "---|" * (len(dates) + 1))
        for e in etfs:
            row_vals = {q.get("report_date"): q.get("net_purchase_yi") for q in e.get("quarters", [])}
            cells = []
            total = 0.0
            for d in dates:
                v = row_vals.get(d)
                cells.append(fmt_signed(v, 1))
                if v is not None:
                    total += v
            md.append(f"| {e['etf_name']} ({e['etf_code']}) | " + " | ".join(cells) + f" | **{fmt_signed(total, 0)}** |")
    md.append("")

    # 机构占比 — 只显示首尾对比
    md.append("### 机构占比演变 (首尾对比)\n")
    md.append("| ETF | 最早期数据 | 最新期数据 | 累计变动 |")
    md.append("|---|---|---|---|")
    for e in etfs:
        h = e.get("holder_structure") or []
        if len(h) < 2:
            continue
        latest = h[0]
        earliest = h[-1]
        chg = (latest.get("institutional_pct") or 0) - (earliest.get("institutional_pct") or 0)
        md.append(
            f"| {e['etf_name']} ({e['etf_code']}) | "
            f"{earliest.get('report_date')}: {fmt_n(earliest.get('institutional_pct'), 1)}% | "
            f"{latest.get('report_date')}: **{fmt_n(latest.get('institutional_pct'), 1)}%** | "
            f"**{fmt_signed(chg, 1)} pp** |"
        )
    md.append("")
    md.append("> 历史趋势: 多数核心宽基机构占比从 2023.12 的 ~64% 持续上升到 2025.12 的 ~90%, 即'国家队接盘曲线'。")
    return "\n".join(md)


# ============================================================
# 第 6 章: 主体趋势综览 (简要表格)
# ============================================================
def render_holders_summary(signal, seasonal):
    md = []
    cat_map = {h["holder_name"]: h.get("holder_category")
               for h in seasonal.get("holders_holdings", [])}
    md.append("| 主体 | 类别 | 持仓股数 | 季报趋势 | 证据 |")
    md.append("|---|---|---|---|---|")
    for h in signal.get("aggregate", {}).get("holders_summary", []):
        cat = cat_map.get(h["holder_name"], "?")
        cat_short = "A平准" if cat and cat.startswith("A_") else ("B产业" if cat and cat.startswith("B_") else "?")
        trend_emoji = {"accumulating": "📈 加仓", "trimming": "📉 减仓",
                       "stable": "➡️ 稳定", "unknown": "❓ 数据不全"}.get(h["trend"], "?")
        md.append(
            f"| {h.get('display_name', h['holder_name'])} | {cat_short} | "
            f"{h['stocks_count']} | {trend_emoji} | {h['trend_evidence']} |"
        )
    return "\n".join(md)


# ============================================================
# 图表
# ============================================================
def make_chart_recent_pool(signal, out_path):
    """近 5/30/90 日累计成交额柱状图 (按 ETF)."""
    if not HAS_MPL:
        return None
    sigs = signal.get("etf_signals", [])
    codes = [e["etf_code"] for e in sigs]
    d5 = [(e.get("recent_windows", {}).get("d5") or {}).get("turnover_sum_yi", 0) for e in sigs]
    d30 = [(e.get("recent_windows", {}).get("d30") or {}).get("turnover_sum_yi", 0) for e in sigs]
    d90 = [(e.get("recent_windows", {}).get("d90") or {}).get("turnover_sum_yi", 0) for e in sigs]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
    for ax, vals, title in zip(axes, [d5, d30, d90], ["Last 5d Turnover", "Last 30d Turnover", "Last 90d Turnover"]):
        ax.bar(codes, vals, color="steelblue")
        ax.set_title(title + " (yi yuan)")
        ax.tick_params(axis='x', rotation=30, labelsize=8)
        ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def make_chart_zscore_recent(signal, out_path):
    """各 ETF 近 90 日内最大单日 Z-Score (堆叠图: d5/d30/d90)."""
    if not HAS_MPL:
        return None
    sigs = signal.get("etf_signals", [])
    codes = [e["etf_code"] for e in sigs]
    z5 = [((e.get("recent_windows", {}).get("d5") or {}).get("max_zscore_day") or {}).get("z", 0) for e in sigs]
    z30 = [((e.get("recent_windows", {}).get("d30") or {}).get("max_zscore_day") or {}).get("z", 0) for e in sigs]
    z90 = [((e.get("recent_windows", {}).get("d90") or {}).get("max_zscore_day") or {}).get("z", 0) for e in sigs]
    fig, ax = plt.subplots(figsize=(11, 4.5))
    x = list(range(len(codes)))
    width = 0.27
    ax.bar([xi - width for xi in x], z5, width, label="Last 5d max Z", color="#d62728")
    ax.bar(x, z30, width, label="Last 30d max Z", color="#ff7f0e")
    ax.bar([xi + width for xi in x], z90, width, label="Last 90d max Z", color="#1f77b4")
    ax.axhline(2, color="red", linestyle="--", lw=0.8, label="Z=+2 threshold")
    ax.set_xticks(x)
    ax.set_xticklabels(codes, rotation=30, fontsize=8)
    ax.set_ylabel("Max Single-Day Z-Score in Window")
    ax.set_title("Max Z-Score per Window (turnover anomaly)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


def make_chart_long_term_institutional(seasonal, out_path):
    """长期机构占比折线 (辅助)."""
    if not HAS_MPL:
        return None
    etfs = seasonal.get("etf_seasonal", [])[:8]
    fig, ax = plt.subplots(figsize=(10, 4))
    has_any = False
    for e in etfs:
        h = list(reversed(e.get("holder_structure") or []))[-6:]
        if len(h) < 2:
            continue
        ax.plot([r.get("report_date") for r in h],
                [r.get("institutional_pct") for r in h],
                marker="o", label=e["etf_code"])
        has_any = True
    if not has_any:
        plt.close(fig)
        return None
    ax.set_ylabel("Institutional Holding %")
    ax.set_title("Long-Term Institutional Holding Trend (Reference)")
    ax.legend(loc="best", fontsize=8, ncol=4)
    ax.grid(alpha=0.3)
    plt.xticks(rotation=20, fontsize=9)
    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path


# ============================================================
# main
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-file", required=True)
    ap.add_argument("--seasonal-file", required=True)
    ap.add_argument("--signal-file", required=True)
    ap.add_argument("--industry-pool", default=None)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    with open(args.snapshot_file, "r", encoding="utf-8") as f:
        snap = json.load(f)
    with open(args.seasonal_file, "r", encoding="utf-8") as f:
        seasonal = json.load(f)
    with open(args.signal_file, "r", encoding="utf-8") as f:
        signal = json.load(f)
    industry_pool = {}
    pool_path = args.industry_pool or os.path.join(
        os.path.dirname(__file__), "..", "config", "industry_funds_pool.json"
    )
    if os.path.exists(pool_path):
        with open(pool_path, "r", encoding="utf-8") as f:
            industry_pool = json.load(f)

    os.makedirs(args.output_dir, exist_ok=True)
    charts_dir = os.path.join(args.output_dir, "charts")
    if HAS_MPL:
        os.makedirs(charts_dir, exist_ok=True)
    chart_files = []

    if HAS_MPL:
        cf = make_chart_recent_pool(signal, os.path.join(charts_dir, "recent-turnover-pool.png"))
        if cf:
            chart_files.append(("近 5/30/90 日累计成交额", os.path.relpath(cf, args.output_dir)))
        cf = make_chart_zscore_recent(signal, os.path.join(charts_dir, "max-zscore-by-window.png"))
        if cf:
            chart_files.append(("各窗口最大单日 Z-Score", os.path.relpath(cf, args.output_dir)))
        cf = make_chart_long_term_institutional(seasonal, os.path.join(charts_dir, "long-term-institutional.png"))
        if cf:
            chart_files.append(("长期机构占比 (辅助参照)", os.path.relpath(cf, args.output_dir)))

    md = []
    md.append(f"# 国家队动向追踪报告 — {signal['meta']['report_date']}")
    md.append("")
    md.append(f"**生成时间**: {signal['meta']['analysis_time']}")
    md.append(f"**数据快照截至**: {snap['meta']['report_date']}")
    md.append(f"**追踪 ETF 数**: {len(snap.get('etfs', []))} 只 | **国家队主体反查**: {len(seasonal.get('holders_holdings', []))} 个")
    md.append("")
    md.append("**报告焦点**: 近 5 日详细动向 + 近 30/90 日资金面 + 近 90 天主体调仓 (主); 长期季度趋势 (辅)")
    md.append("")

    md.append("---")
    md.append("## 一、综合判断")
    md.append("")
    md.append(render_overall(signal))
    md.append("")

    md.append("---")
    md.append("## 二、近 5 日 ETF 动向 (核心重点)")
    md.append("")
    md.append(render_d5_detail(signal))
    md.append("")

    md.append("---")
    md.append("## 三、近 30 / 90 日资金面 (中期视角)")
    md.append("")
    md.append(render_d30_d90_detail(signal))
    md.append("")

    if chart_files:
        md.append("### 关键图表 (近期窗口)")
        md.append("")
        for title, path in chart_files[:2]:  # 前两张是近期, 第三张是长期
            md.append(f"#### {title}")
            md.append("")
            md.append(f"![{title}]({path})")
            md.append("")

    md.append("---")
    md.append("## 四、近 90 天国家队主体调仓 (datacenter 最新披露)")
    md.append("")
    md.append(render_recent_holder_changes(signal))
    md.append("")

    md.append("---")
    md.append("## 五、信号触发依据")
    md.append("")
    triggered = [e for e in signal.get("etf_signals", []) if e["signal_level"] != "yellow_neutral"]
    if triggered:
        for e in triggered:
            md.append(f"### {e['etf_name']} ({e['etf_code']}) — {SIGNAL_BADGE.get(e['signal_level'])}")
            for r in e.get("signal_reasons", []):
                md.append(f"- {r}")
            md.append("")
    else:
        md.append("_所有 ETF 当前均为中性, 无触发等级 ≥ 强买入的信号。_")
        md.append("")

    md.append("---")
    md.append("## 六、长期趋势参考 (辅助, 简要)")
    md.append("")
    md.append(render_long_term_brief(seasonal))
    md.append("")

    if len(chart_files) > 2:
        md.append(f"### {chart_files[2][0]}")
        md.append("")
        md.append(f"![{chart_files[2][0]}]({chart_files[2][1]})")
        md.append("")

    md.append("### 主体季报趋势综览")
    md.append("")
    md.append(render_holders_summary(signal, seasonal))
    md.append("")
    rec = signal.get("recommendation", {})
    if rec.get("industry_pool_highlights"):
        md.append("### 产业基金动向")
        for h in rec["industry_pool_highlights"]:
            md.append(f"- 🔵 {h}")
        md.append("")

    md.append("---")
    md.append("## 七、B 类产业基金题材池 (长线主题, 不参与择时)")
    md.append("")
    md.append("> B 类基金锁定期 5-15 年, 仅作为长线主题配置参考。")
    md.append("")
    if industry_pool:
        for cat_key, cat_val in industry_pool.items():
            if cat_key.startswith("_") or not isinstance(cat_val, dict):
                continue
            md.append(f"### {cat_val.get('_description', cat_key)}")
            for sub_key, sub_val in cat_val.items():
                if sub_key.startswith("_"):
                    continue
                if isinstance(sub_val, list):
                    names = [f"{s.get('code')} {s.get('name')}" for s in sub_val[:6]]
                    md.append(f"- **{sub_key}**: {', '.join(names)}{' ...' if len(sub_val) > 6 else ''}")
            md.append("")

    md.append("---")
    md.append("## 八、数据来源 & 局限")
    md.append("")
    md.append("**数据源** (零依赖, 仅 Python urllib):")
    md.append("- 实时行情: 东方财富 push2")
    md.append("- 120 日 K 线: 东方财富 push2his (用于近 5/30/90 日切片)")
    md.append("- 季度规模/持有人结构: 东方财富 fundf10 (`gmbd`/`cyrjg`)")
    md.append("- 国家队主体反查: 东方财富 datacenter (`RPT_F10_EH_HOLDERS`)")
    md.append("")
    md.append("**已确认局限**:")
    md.append("- A 股 ETF 的**日级总份额数据无公开免费接口** (在交易所 PCF 文件中, 不开放)")
    md.append("- 因此 1/7/30 日的'份额变化'用**成交额异常**作为代理信号, 仅季度数据是精确的")
    md.append("- ETF 一季报披露窗口为季末后 30 天, 当前 (2026-04 中下旬) 池内 ETF 一季报多数尚未披露, 季度部分仍为 2025-Q4 数据; **datacenter 个股层反查已有 2026-04 最新数据**, 上方第四章已展示")
    md.append("- 季报 holder_state 字段在部分主体披露中可能为空, 此时趋势会标'❓ 数据不全'")
    md.append("")
    md.append(f"---")
    md.append(f"_由 `national-team-tracker` workflow 生成。matplotlib: {HAS_MPL}_")

    out_md = os.path.join(args.output_dir, "national-team-report.md")
    md_text = "\n".join(md)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md_text)
    print(f"[nt_report] wrote {out_md} ({len(md_text)} chars, {len(chart_files)} charts)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
