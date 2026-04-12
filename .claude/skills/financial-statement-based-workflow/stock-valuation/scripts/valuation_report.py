#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stock Valuation Report Generator

Zero-dependency report generation (Python 3.6+ stdlib only, optional matplotlib).
Reads 4 JSON files from previous pipeline stages and produces a comprehensive
Markdown valuation report with optional PNG visualizations.

Usage:
    python valuation_report.py \\
        --integrated-file {code}_integrated.json \\
        --data-file {code}_valuation_data.json \\
        --preprocessed-file {code}_preprocessed.json \\
        --model-results-file {code}_model_results.json \\
        --output-dir ./output \\
        [--visualize] \\
        [--format full|focused] \\
        [--question "..."]
"""

import argparse
import json
import os
import sys
from datetime import datetime


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_get(d, *keys, default=None):
    """Safely traverse nested dicts/lists."""
    cur = d
    for k in keys:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(k)]
            except (IndexError, ValueError, TypeError):
                return default
        else:
            return default
    return cur if cur is not None else default


def _fmt_num(val, decimals=2, suffix="", fallback="-"):
    """Format a number with optional suffix; return *fallback* for None/NaN."""
    if val is None:
        return fallback
    try:
        v = float(val)
    except (TypeError, ValueError):
        return fallback
    if decimals == 0:
        return "{}{}".format(int(round(v)), suffix)
    fmt = "{{:.{}f}}{}".format(decimals, suffix)
    return fmt.format(v)


def _fmt_pct(val, decimals=2, fallback="-"):
    return _fmt_num(val, decimals=decimals, suffix="%", fallback=fallback)


def _fmt_price(val, decimals=2, fallback="-"):
    return _fmt_num(val, decimals=decimals, suffix="\u5143", fallback=fallback)


def _fmt_yi(val, decimals=2, fallback="-"):
    """Format a value in \u4ebf (hundred-millions)."""
    return _fmt_num(val, decimals=decimals, suffix="\u4ebf\u5143", fallback=fallback)


def _fmt_bei(val, decimals=2, fallback="-"):
    return _fmt_num(val, decimals=decimals, suffix="\u500d", fallback=fallback)


def _status_marker(status):
    """Return a status marker string."""
    mapping = {
        "normal": "[\u6b63\u5e38]",
        "watch": "[\u5173\u6ce8]",
        "warning": "[\u9884\u8b66]",
        "ok": "[\u6b63\u5e38]",
        "caution": "[\u5173\u6ce8]",
        "alert": "[\u9884\u8b66]",
    }
    if isinstance(status, str):
        return mapping.get(status.lower(), "[{}]".format(status))
    return "[-]"


def _confidence_emoji(level):
    mapping = {
        "high": "\U0001f7e2 \u9ad8",
        "medium": "\U0001f7e1 \u4e2d",
        "low": "\U0001f534 \u4f4e",
    }
    if isinstance(level, str):
        return mapping.get(level.lower(), level)
    return str(level) if level else "-"


def _trap_level_emoji(level):
    mapping = {
        "low": "\U0001f7e2 \u4f4e\u98ce\u9669",
        "medium": "\U0001f7e1 \u4e2d\u7b49\u98ce\u9669",
        "high": "\U0001f534 \u9ad8\u98ce\u9669",
        "safe": "\U0001f7e2 \u5b89\u5168",
        "caution": "\U0001f7e1 \u8c28\u614e",
        "danger": "\U0001f534 \u5371\u9669",
    }
    if isinstance(level, str):
        return mapping.get(level.lower(), level)
    return str(level) if level else "-"


def _circled_num(n):
    """Return a circled number character for 1-9."""
    circled = {
        1: "\u2460", 2: "\u2461", 3: "\u2462",
        4: "\u2463", 5: "\u2464", 6: "\u2465",
        7: "\u2466", 8: "\u2467", 9: "\u2468",
    }
    return circled.get(n, "({})".format(n))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json(path):
    """Load a JSON file and return its contents, or empty dict on failure."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as exc:
        print("[WARN] Failed to load {}: {}".format(path, exc), file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Visualizer
# ---------------------------------------------------------------------------

class ValuationVisualizer:
    """Optional matplotlib charts with ASCII fallback."""

    def __init__(self):
        self.has_matplotlib = False
        self.plt = None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            self.plt = plt
            self.has_matplotlib = True
        except ImportError:
            pass

    # -- public API ----------------------------------------------------------

    def plot_valuation_range(self, model_data, current_price, output_path):
        """Horizontal bar chart: each model's pessimistic-optimistic range
        with a vertical line for the current price.

        *model_data* is a list of dicts:
            [{"name": "DCF", "weight": 25,
              "pessimistic": 800, "base": 1200, "optimistic": 1600}, ...]

        Returns the ASCII string representation (always), and saves a PNG
        if matplotlib is available.
        """
        if self.has_matplotlib:
            self._mpl_range_chart(model_data, current_price, output_path)
        return self._ascii_range_chart(model_data, current_price)

    def plot_sensitivity_heatmap(self, matrix_data, output_path):
        """DCF sensitivity heatmap: WACC vs terminal growth rate.

        *matrix_data*: {"wacc_rates": [...], "growth_rates": [...],
                        "values": [[...],...],
                        "base_wacc_idx": int, "base_growth_idx": int}
        """
        if self.has_matplotlib and matrix_data:
            self._mpl_sensitivity_heatmap(matrix_data, output_path)

    # -- matplotlib implementations ------------------------------------------

    def _mpl_range_chart(self, model_data, current_price, output_path):
        plt = self.plt
        fig, ax = plt.subplots(
            figsize=(10, max(4, len(model_data) * 0.8 + 1)))

        cjk_fonts = ["SimHei", "Microsoft YaHei",
                      "WenQuanYi Micro Hei",
                      "Noto Sans CJK SC", "sans-serif"]

        labels = []
        for i, m in enumerate(model_data):
            name = m.get("name", "Model")
            weight = m.get("weight", 0)
            p = float(m.get("pessimistic", 0) or 0)
            b = float(m.get("base", 0) or 0)
            o = float(m.get("optimistic", 0) or 0)
            label = "{} ({}%)".format(name, _fmt_num(weight, 0))
            labels.append(label)
            ax.barh(i, o - p, left=p,
                    height=0.5, color="#5B9BD5", alpha=0.7)
            ax.plot(b, i, "D", color="#2E75B6", markersize=8)

        if current_price is not None:
            ax.axvline(x=current_price, color="red", linestyle="--",
                       linewidth=1.5,
                       label="\u5f53\u524d\u4ef7\u683c {:.2f}\u5143".format(
                           current_price))
            ax.legend(loc="upper right", fontsize=9,
                      prop={"family": cjk_fonts})

        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        ax.set_xlabel("\u4f30\u503c\uff08\u5143\uff09")
        ax.set_title("\u5404\u6a21\u578b\u4f30\u503c\u533a\u95f4")
        ax.invert_yaxis()

        for item in ([ax.title, ax.xaxis.label, ax.yaxis.label] +
                      ax.get_xticklabels() + ax.get_yticklabels()):
            item.set_fontfamily(cjk_fonts)

        fig.tight_layout()
        try:
            fig.savefig(output_path, dpi=150)
        except Exception as exc:
            print("[WARN] Failed to save range chart: {}".format(exc),
                  file=sys.stderr)
        plt.close(fig)

    def _mpl_sensitivity_heatmap(self, matrix_data, output_path):
        plt = self.plt
        wacc = matrix_data.get("wacc_rates", [])
        growth = matrix_data.get("growth_rates", [])
        values = matrix_data.get("values", [])
        if not wacc or not growth or not values:
            return

        cjk_fonts = ["SimHei", "Microsoft YaHei",
                      "WenQuanYi Micro Hei",
                      "Noto Sans CJK SC", "sans-serif"]

        fig, ax = plt.subplots(
            figsize=(max(6, len(wacc) * 1.2),
                     max(4, len(growth) * 0.8)))
        ax.set_axis_off()

        col_labels = ["WACC={:.1f}%".format(float(w)) for w in wacc]
        row_labels = ["g={:.1f}%".format(float(g)) for g in growth]
        cell_text = []
        for row in values:
            cell_text.append([_fmt_price(v) for v in row])

        table = ax.table(cellText=cell_text, rowLabels=row_labels,
                         colLabels=col_labels, loc="center",
                         cellLoc="center")
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1.2, 1.5)

        base_wi = matrix_data.get("base_wacc_idx")
        base_gi = matrix_data.get("base_growth_idx")
        if base_wi is not None and base_gi is not None:
            try:
                cell = table[base_gi + 1, base_wi]
                cell.set_facecolor("#FFD966")
                cell.set_text_props(weight="bold")
            except Exception:
                pass

        ax.set_title(
            "DCF \u654f\u611f\u6027\u77e9\u9635",
            fontsize=13, fontfamily=cjk_fonts)
        fig.tight_layout()
        try:
            fig.savefig(output_path, dpi=150, bbox_inches="tight")
        except Exception as exc:
            print("[WARN] Failed to save heatmap: {}".format(exc),
                  file=sys.stderr)
        plt.close(fig)

    # -- ASCII fallback ------------------------------------------------------

    def _ascii_range_chart(self, model_data, current_price):
        """Produce an ASCII art range chart."""
        if not model_data:
            return ""

        all_vals = []
        for m in model_data:
            for k in ("pessimistic", "base", "optimistic"):
                v = m.get(k)
                if v is not None:
                    all_vals.append(float(v))
        if current_price is not None:
            all_vals.append(float(current_price))
        if not all_vals:
            return ""

        lo = min(all_vals)
        hi = max(all_vals)
        span = hi - lo
        if span <= 0:
            span = 1.0

        width = 50

        def _label(m):
            return "{} ({}%)".format(
                m.get("name", "?"), _fmt_num(m.get("weight", 0), 0))

        name_width = max(len(_label(m)) for m in model_data)
        name_width = max(name_width, 6)

        def pos(v):
            return int(round((float(v) - lo) / span * (width - 1)))

        # header ticks
        tick_count = 6
        step_val = span / (tick_count - 1) if tick_count > 1 else span
        ticks = [lo + i * step_val for i in range(tick_count)]
        tick_strs = [str(int(round(t))) for t in ticks]
        # Build a ruler line
        ruler = " " * (name_width + 2)
        slot = width // (tick_count - 1) if tick_count > 1 else width
        for ts in tick_strs:
            ruler += ts.ljust(slot)

        lines = [
            "\u4f30\u503c\u533a\u95f4\u53ef\u89c6\u5316\uff08\u5143\uff09",
            ruler,
        ]

        cp_pos = pos(current_price) if current_price is not None else None

        for m in model_data:
            p = m.get("pessimistic")
            b = m.get("base")
            o = m.get("optimistic")
            label = _label(m).ljust(name_width)

            bar = list(" " * width)
            if p is not None and o is not None:
                pi = pos(p)
                oi = pos(o)
                for j in range(pi, min(oi + 1, width)):
                    bar[j] = "="
                if 0 <= pi < width:
                    bar[pi] = "["
                if 0 <= oi < width:
                    bar[oi] = "]"
            if b is not None:
                bi = pos(b)
                if 0 <= bi < width:
                    bar[bi] = "|"

            line = "{}  |{}|".format(label, "".join(bar))
            lines.append(line)

        if current_price is not None:
            marker_line = list(" " * width)
            if cp_pos is not None and 0 <= cp_pos < width:
                marker_line[cp_pos] = "\u2605"
            summary = "{}  |{}|".format(" " * name_width,
                                         "".join(marker_line))
            lines.append(summary)
            lines.append("{}   \u5f53\u524d\u4ef7\u683c \u2605 = {}".format(
                " " * name_width, _fmt_price(current_price)))

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report sections
# ---------------------------------------------------------------------------

class ReportBuilder:
    """Assembles the Markdown report from 4 input data dicts."""

    def __init__(self, integrated, data, preprocessed, model_results,
                 visualize=False, output_dir=".", visualizer=None):
        self.integrated = integrated or {}
        self.data = data or {}
        self.preprocessed = preprocessed or {}
        self.model_results = model_results or {}
        self.visualize = visualize
        self.output_dir = output_dir
        self.visualizer = visualizer or ValuationVisualizer()

        # Commonly needed fields
        self.code = (self.integrated.get("code")
                     or self.data.get("code")
                     or self.preprocessed.get("code")
                     or "000000")
        self.name = (self.integrated.get("name")
                     or self.data.get("name")
                     or self.preprocessed.get("name")
                     or "\u672a\u77e5\u516c\u53f8")
        self.price = self._find_price()
        self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # -- helpers -------------------------------------------------------------

    def _find_price(self):
        for src in (self.integrated, self.data, self.preprocessed):
            for key in ("current_price", "price", "close"):
                v = _safe_get(src, key)
                if v is not None:
                    return float(v)
            v = _safe_get(src, "basic_snapshot", "current_price")
            if v is not None:
                return float(v)
            v = _safe_get(src, "market_data", "current_price")
            if v is not None:
                return float(v)
        return None

    def _get_snapshot_field(self, *keys, default=None):
        for src in (self.integrated, self.data, self.preprocessed):
            for key in keys:
                v = _safe_get(src, key)
                if v is not None:
                    return v
                v = _safe_get(src, "basic_snapshot", key)
                if v is not None:
                    return v
                v = _safe_get(src, "market_data", key)
                if v is not None:
                    return v
        return default

    def _get_mode(self):
        mode = (_safe_get(self.integrated, "mode")
                or _safe_get(self.preprocessed, "mode"))
        if mode is None:
            return "A", "\u6807\u51c6\u4f30\u503c\u6d41\u7a0b"
        mode_str = str(mode).upper()
        descriptions = {
            "A": "\u6807\u51c6\u4f30\u503c\u6d41\u7a0b",
            "B": "\u7b80\u5316\u4f30\u503c\u6d41\u7a0b",
        }
        return mode_str, descriptions.get(mode_str, "")

    def _get_stage(self):
        stage = (_safe_get(self.integrated, "company_stage")
                 or _safe_get(self.preprocessed, "company_stage")
                 or _safe_get(self.preprocessed, "stage"))
        stage_map = {
            "growth": "\u6210\u957f\u671f",
            "mature": "\u6210\u719f\u671f",
            "decline": "\u8870\u9000\u671f",
            "startup": "\u521d\u521b\u671f",
            "turnaround": "\u8f6c\u578b\u671f",
        }
        if isinstance(stage, dict):
            stage_name = (stage.get("stage")
                          or stage.get("name")
                          or "unknown")
            stage_cn = (stage.get("stage_cn")
                        or stage_map.get(str(stage_name).lower(),
                                         str(stage_name)))
            return stage_cn
        if isinstance(stage, str):
            return stage_map.get(stage.lower(), stage)
        return "-"

    def _get_confidence(self):
        return (_safe_get(self.integrated, "confidence_level")
                or _safe_get(self.integrated, "confidence")
                or _safe_get(self.preprocessed, "confidence_level")
                or "-")

    def _get_models_summary(self):
        """Return (model_list, skipped_list).
        Each model_list item:
            {name, weight, pessimistic, base, optimistic, details,
             warnings, raw}
        """
        models = []
        skipped = []

        results = (_safe_get(self.integrated, "model_results")
                   or self.model_results)

        def _parse_model(key, val):
            if not isinstance(val, dict):
                return
            if val.get("skipped") or val.get("status") == "skipped":
                skipped.append({
                    "name": (val.get("model_name")
                             or val.get("name") or key),
                    "reason": (val.get("reason")
                               or val.get("skip_reason") or "-"),
                })
                return
            m = {
                "name": (val.get("model_name")
                         or val.get("name") or key),
                "weight": (val.get("weight")
                           or val.get("final_weight") or 0),
                "pessimistic": (_safe_get(val, "pessimistic")
                                or _safe_get(val, "scenarios",
                                             "pessimistic")),
                "base": (_safe_get(val, "base")
                         or _safe_get(val, "scenarios", "base")),
                "optimistic": (_safe_get(val, "optimistic")
                               or _safe_get(val, "scenarios",
                                            "optimistic")),
                "details": (val.get("details")
                            or val.get("assumptions") or {}),
                "warnings": val.get("warnings") or [],
                "raw": val,
            }
            models.append(m)

        if isinstance(results, dict):
            for key, val in results.items():
                _parse_model(key, val)
        elif isinstance(results, list):
            for val in results:
                _parse_model("?", val)

        return models, skipped

    def _get_weighted_composite(self, models):
        """Compute weighted composite for 3 scenarios."""
        wp = wb = wo = 0.0
        total_w = 0.0
        for m in models:
            w = float(m.get("weight", 0) or 0)
            p = m.get("pessimistic")
            b = m.get("base")
            o = m.get("optimistic")
            if w > 0 and b is not None:
                total_w += w
                wp += w * float(p or b)
                wb += w * float(b)
                wo += w * float(o or b)
        if total_w > 0:
            return wp / total_w, wb / total_w, wo / total_w
        return None, None, None

    def _get_sensitivity_matrix(self):
        """Extract DCF sensitivity matrix data."""
        search_keys = ["sensitivity_matrix"]
        model_keys = ["dcf", "DCF"]
        for src in (self.integrated, self.model_results):
            for sk in search_keys:
                sm = _safe_get(src, sk)
                if sm:
                    return sm
            for mk in model_keys:
                for sk in search_keys:
                    sm = _safe_get(src, mk, sk)
                    if sm:
                        return sm
                    sm = _safe_get(src, "model_results", mk, sk)
                    if sm:
                        return sm
        return None

    def _get_traps(self):
        """Extract valuation trap detection results."""
        trap_keys = ["valuation_traps", "traps", "trap_detection"]
        for src in (self.integrated, self.preprocessed, self.data):
            for tk in trap_keys:
                t = _safe_get(src, tk)
                if t:
                    return t
        return None

    def _get_safety_margin(self):
        margin_keys = ["safety_margin", "margin_of_safety"]
        for src in (self.integrated, self.preprocessed):
            for mk in margin_keys:
                sm = _safe_get(src, mk)
                if sm:
                    return sm
        return None

    def _get_buy_zone(self):
        zone_keys = ["buy_zone", "dynamic_buy_zone"]
        for src in (self.integrated, self.preprocessed):
            for zk in zone_keys:
                bz = _safe_get(src, zk)
                if bz:
                    return bz
        return None

    def _get_reverse_engineering(self):
        re_keys = ["reverse_engineering", "implied_expectations"]
        for src in (self.integrated, self.model_results):
            for rk in re_keys:
                re_data = _safe_get(src, rk)
                if re_data:
                    return re_data
        return None

    def _get_divergence(self):
        div_keys = ["divergence", "model_divergence"]
        for src in (self.integrated, self.model_results):
            for dk in div_keys:
                d = _safe_get(src, dk)
                if d:
                    return d
        return None

    # -- section builders ----------------------------------------------------

    def section_overview(self):
        """Section 1: report overview."""
        mode_str, mode_desc = self._get_mode()
        stage_cn = self._get_stage()
        confidence = self._get_confidence()

        pe = self._get_snapshot_field("pe", "pe_ttm")
        pb = self._get_snapshot_field("pb", "pb_mrq")
        market_cap = self._get_snapshot_field(
            "market_cap", "total_market_cap")
        div_yield = self._get_snapshot_field(
            "dividend_yield", "yield", "div_yield")
        industry = self._get_snapshot_field("industry", "industry_name")

        lines = []
        lines.append("# {}\uff08{}\uff09\u4f30\u503c\u5206\u6790\u62a5\u544a\n".format(
            self.name, self.code))
        lines.append("> \u62a5\u544a\u751f\u6210\u65f6\u95f4\uff1a{}".format(
            self.timestamp))
        lines.append("> \u8fd0\u884c\u6a21\u5f0f\uff1a\u6a21\u5f0f{}\uff08{}\uff09".format(
            mode_str, mode_desc))
        lines.append("> \u516c\u53f8\u9636\u6bb5\uff1a{}".format(stage_cn))
        lines.append("> \u7efc\u5408\u7f6e\u4fe1\u5ea6\uff1a{}\n".format(
            _confidence_emoji(confidence)))
        lines.append("## \u57fa\u7840\u6570\u636e\u5feb\u7167\n")
        lines.append("| \u6307\u6807 | \u6570\u503c |")
        lines.append("|------|------|")
        lines.append("| \u5f53\u524d\u80a1\u4ef7 | {} |".format(
            _fmt_price(self.price)))
        lines.append("| \u603b\u5e02\u503c | {} |".format(
            _fmt_yi(market_cap)))
        lines.append("| PE(TTM) | {} |".format(_fmt_bei(pe)))
        lines.append("| PB(MRQ) | {} |".format(_fmt_bei(pb)))
        lines.append("| \u80a1\u606f\u7387 | {} |".format(
            _fmt_pct(div_yield)))
        lines.append("| \u884c\u4e1a | {} |".format(industry or "-"))
        return "\n".join(lines)

    def section_preprocessing(self):
        """Section 2: preprocessing conclusions."""
        pre = self.preprocessed
        lines = []
        lines.append("## \u9884\u5904\u7406\u7ed3\u8bba\n")

        # Stage determination
        stage_info = (_safe_get(pre, "company_stage")
                      or _safe_get(pre, "stage"))
        if isinstance(stage_info, dict):
            lines.append("### \u516c\u53f8\u9636\u6bb5\u5224\u5b9a\n")
            stage_name = (stage_info.get("stage_cn")
                          or stage_info.get("stage") or "-")
            reason = (stage_info.get("reason")
                      or stage_info.get("description") or "-")
            lines.append("- **\u9636\u6bb5**\uff1a{}".format(stage_name))
            lines.append("- **\u5224\u5b9a\u4f9d\u636e**\uff1a{}".format(
                reason))
            lines.append("")

        # Data completeness
        completeness = (_safe_get(pre, "data_completeness")
                        or _safe_get(pre, "completeness"))
        if isinstance(completeness, dict):
            lines.append("### \u6570\u636e\u5b8c\u6574\u5ea6\n")
            lines.append("| \u6570\u636e\u9879 | \u72b6\u6001 |")
            lines.append("|--------|------|")
            for field, status in completeness.items():
                if isinstance(status, bool):
                    mark = "\u2705" if status else "\u274c"
                elif isinstance(status, str):
                    mark = status
                else:
                    mark = str(status)
                lines.append("| {} | {} |".format(field, mark))
            lines.append("")

        # Available / skipped models
        models, skipped = self._get_models_summary()
        if models or skipped:
            lines.append("### \u6a21\u578b\u53ef\u7528\u6027\n")
            if models:
                lines.append("**\u53ef\u7528\u6a21\u578b**\uff1a{}".format(
                    "\u3001".join(m["name"] for m in models)))
            if skipped:
                lines.append("**\u8df3\u8fc7\u6a21\u578b**\uff1a")
                for s in skipped:
                    lines.append("- {} \u2014 {}".format(
                        s["name"], s["reason"]))
            lines.append("")

        # WACC
        wacc_data = (_safe_get(pre, "wacc")
                     or _safe_get(self.data, "wacc"))
        if isinstance(wacc_data, dict):
            lines.append("### WACC \u8ba1\u7b97\n")
            ke = (_safe_get(wacc_data, "ke")
                  or _safe_get(wacc_data, "cost_of_equity"))
            kd = (_safe_get(wacc_data, "kd")
                  or _safe_get(wacc_data, "cost_of_debt"))
            we = (_safe_get(wacc_data, "we")
                  or _safe_get(wacc_data, "equity_weight"))
            wd = (_safe_get(wacc_data, "wd")
                  or _safe_get(wacc_data, "debt_weight"))
            wacc_final = (_safe_get(wacc_data, "wacc")
                          or _safe_get(wacc_data, "final_wacc"))
            lines.append("| \u53c2\u6570 | \u503c |")
            lines.append("|------|-----|")
            lines.append("| \u80a1\u6743\u6210\u672c Ke | {} |".format(
                _fmt_pct(ke)))
            lines.append("| \u503a\u52a1\u6210\u672c Kd | {} |".format(
                _fmt_pct(kd)))
            lines.append("| \u80a1\u6743\u6743\u91cd | {} |".format(
                _fmt_pct(we)))
            lines.append("| \u503a\u52a1\u6743\u91cd | {} |".format(
                _fmt_pct(wd)))
            lines.append("| **\u6700\u7ec8 WACC** | **{}** |".format(
                _fmt_pct(wacc_final)))
            lines.append("")

        # Beta
        beta_data = (_safe_get(pre, "beta")
                     or _safe_get(self.data, "beta"))
        if beta_data is not None:
            lines.append("### Beta \u5408\u6210\n")
            if isinstance(beta_data, dict):
                beta_val = (beta_data.get("final")
                            or beta_data.get("value")
                            or beta_data.get("beta"))
                method = (beta_data.get("method")
                          or beta_data.get("source") or "-")
                lines.append("- **\u6700\u7ec8 Beta**\uff1a{}".format(
                    _fmt_num(beta_val)))
                lines.append("- **\u5408\u6210\u65b9\u6cd5**\uff1a{}".format(
                    method))
            else:
                lines.append("- **Beta**\uff1a{}".format(
                    _fmt_num(beta_data)))
            lines.append("")

        # EPS forecast
        eps_data = (_safe_get(pre, "eps_forecast")
                    or _safe_get(pre, "eps"))
        if isinstance(eps_data, dict):
            lines.append("### EPS \u9884\u6d4b\n")
            trend = (eps_data.get("trend_type")
                     or eps_data.get("trend") or "-")
            lines.append("- **\u8d8b\u52bf\u7c7b\u578b**\uff1a{}".format(
                trend))
            scenarios = eps_data.get("scenarios") or {}
            if isinstance(scenarios, dict):
                for sname, sval in scenarios.items():
                    lines.append("- **{}**\uff1a{}\u5143".format(
                        sname, _fmt_num(sval)))
            elif isinstance(scenarios, list):
                for s in scenarios:
                    if isinstance(s, dict):
                        lines.append("- **{}**\uff1a{}\u5143".format(
                            s.get("name", "?"), _fmt_num(s.get("value"))))
            lines.append("")

        return "\n".join(lines)

    def section_model_details(self):
        """Section 3: model valuation details."""
        models, _skipped = self._get_models_summary()
        if not models:
            return ("## \u5404\u6a21\u578b\u4f30\u503c\u8be6\u60c5\n\n"
                    "\u6682\u65e0\u53ef\u7528\u6a21\u578b\u7ed3\u679c\u3002\n")

        lines = ["## \u5404\u6a21\u578b\u4f30\u503c\u8be6\u60c5\n"]
        for m in models:
            lines.append("### {}\n".format(m["name"]))
            lines.append("**\u6743\u91cd**\uff1a{}%\n".format(
                _fmt_num(m["weight"], 1)))

            # Key assumptions
            details = m.get("details", {})
            if isinstance(details, dict) and details:
                lines.append(
                    "**\u5173\u952e\u5047\u8bbe\u4e0e\u53c2\u6570**\uff1a\n")
                for dk, dv in details.items():
                    if isinstance(dv, (dict, list)):
                        lines.append("- **{}**\uff1a{}".format(
                            dk, json.dumps(dv, ensure_ascii=False)))
                    else:
                        lines.append("- **{}**\uff1a{}".format(dk, dv))
                lines.append("")

            # Scenario results
            lines.append("| \u60c5\u666f | \u4f30\u503c |")
            lines.append("|------|------|")
            lines.append("| \u60b2\u89c2 | {} |".format(
                _fmt_price(m["pessimistic"])))
            lines.append("| \u57fa\u51c6 | {} |".format(
                _fmt_price(m["base"])))
            lines.append("| \u4e50\u89c2 | {} |".format(
                _fmt_price(m["optimistic"])))
            lines.append("")

            # Model-specific extras
            raw = m.get("raw", {})

            # PEG check for PE model
            peg = (_safe_get(raw, "peg")
                   or _safe_get(raw, "details", "peg"))
            if peg is not None:
                lines.append("**PEG \u68c0\u67e5**\uff1a{}\n".format(
                    _fmt_num(peg)))

            # Warnings
            warnings = m.get("warnings", [])
            if warnings:
                lines.append("**\u6ce8\u610f\u4e8b\u9879**\uff1a\n")
                for w in warnings:
                    lines.append("- {}".format(w))
                lines.append("")

            lines.append("---\n")

        return "\n".join(lines)

    def section_sensitivity(self):
        """Section 4: DCF sensitivity analysis."""
        sm = self._get_sensitivity_matrix()
        if not sm:
            return ("## DCF \u654f\u611f\u6027\u5206\u6790\n\n"
                    "\u5f53\u524d\u6570\u636e\u65e0\u654f\u611f\u6027"
                    "\u77e9\u9635\u53ef\u7528\u3002\n")

        wacc_rates = sm.get("wacc_rates", [])
        growth_rates = sm.get("growth_rates", [])
        values = sm.get("values", [])
        base_wi = sm.get("base_wacc_idx")
        base_gi = sm.get("base_growth_idx")

        if not wacc_rates or not growth_rates or not values:
            return ("## DCF \u654f\u611f\u6027\u5206\u6790\n\n"
                    "\u654f\u611f\u6027\u77e9\u9635\u6570\u636e"
                    "\u4e0d\u5b8c\u6574\u3002\n")

        lines = ["## DCF \u654f\u611f\u6027\u77e9\u9635\n"]

        # Build table header
        header = "|          |"
        sep = "|----------|"
        for w in wacc_rates:
            header += " WACC={:.1f}% |".format(float(w))
            sep += "-----------|"
        lines.append(header)
        lines.append(sep)

        all_vals = []
        for ri, g in enumerate(growth_rates):
            row_str = "| g={:.1f}%   |".format(float(g))
            for ci, _w in enumerate(wacc_rates):
                v = None
                if ri < len(values) and ci < len(values[ri]):
                    v = values[ri][ci]
                    if v is not None:
                        all_vals.append(float(v))
                cell = _fmt_price(v)
                is_base = (base_wi is not None
                           and base_gi is not None
                           and ci == base_wi
                           and ri == base_gi)
                if is_base:
                    cell = "**{}**".format(cell)
                row_str += " {} |".format(cell)
            lines.append(row_str)

        if all_vals:
            vmin = min(all_vals)
            vmax = max(all_vals)
            mid = (vmin + vmax) / 2.0
            range_pct = (
                ((vmax - vmin) / mid * 100) if mid != 0 else 0)
            lines.append("")
            lines.append(
                "> **\u52a0\u7c97**\u4e3a\u57fa\u51c6\u5047\u8bbe"
                "\uff0c\u533a\u95f4\u5e45\u5ea6\uff1a{:.1f}%".format(
                    range_pct))

        # Visualization
        if self.visualize and sm:
            chart_path = os.path.join(
                self.output_dir,
                "{}_{}_sensitivity.png".format(self.code, self.name))
            try:
                self.visualizer.plot_sensitivity_heatmap(sm, chart_path)
                if os.path.isfile(chart_path):
                    rel = os.path.basename(chart_path)
                    lines.append("")
                    lines.append(
                        "![DCF\u654f\u611f\u6027\u77e9\u9635]({})".format(
                            rel))
            except Exception:
                pass

        return "\n".join(lines)

    def section_valuation_matrix(self):
        """Section 5: valuation result summary matrix."""
        models, _skipped = self._get_models_summary()
        lines = ["## \u4f30\u503c\u7ed3\u679c\u6c47\u603b\n"]

        if not models:
            lines.append(
                "\u6682\u65e0\u53ef\u7528\u6a21\u578b\u7ed3\u679c\u3002\n")
            return "\n".join(lines)

        lines.append(
            "| \u6a21\u578b | \u6743\u91cd | \u60b2\u89c2\u60c5\u666f "
            "| \u57fa\u51c6\u60c5\u666f | \u4e50\u89c2\u60c5\u666f |")
        lines.append("|------|------|---------|---------|---------|")

        for m in models:
            lines.append("| {} | {}% | {} | {} | {} |".format(
                m["name"],
                _fmt_num(m["weight"], 1),
                _fmt_price(m["pessimistic"]),
                _fmt_price(m["base"]),
                _fmt_price(m["optimistic"]),
            ))

        wp, wb, wo = self._get_weighted_composite(models)
        lines.append(
            "| **\u52a0\u6743\u7efc\u5408** | **100%** "
            "| **{}** | **{}** | **{}** |".format(
                _fmt_price(wp), _fmt_price(wb), _fmt_price(wo)))
        lines.append("")

        # Current price comparison
        lines.append("\u5f53\u524d\u80a1\u4ef7\uff1a{}\n".format(
            _fmt_price(self.price)))
        if (wb is not None and self.price is not None and wb != 0):
            pct = (self.price - wb) / wb * 100
            direction = ("\u6ea2\u4ef7" if pct > 0
                         else "\u6298\u4ef7")
            lines.append(
                "\u57fa\u51c6\u60c5\u666f{}\uff1a{:.1f}%\n".format(
                    direction, abs(pct)))

        # Visualization
        if self.visualize:
            chart_path = os.path.join(
                self.output_dir,
                "{}_{}_range.png".format(self.code, self.name))
            ascii_chart = self.visualizer.plot_valuation_range(
                models, self.price, chart_path)
            if ascii_chart:
                lines.append("```")
                lines.append(ascii_chart)
                lines.append("```")
            if os.path.isfile(chart_path):
                rel = os.path.basename(chart_path)
                lines.append("")
                lines.append(
                    "![\u4f30\u503c\u533a\u95f4\u56fe]({})".format(rel))

        return "\n".join(lines)

    def section_traps(self):
        """Section 6: valuation trap detection."""
        traps = self._get_traps()
        lines = ["## \u4f30\u503c\u9677\u9631\u68c0\u6d4b\n"]

        if not traps:
            lines.append(
                "\u6682\u65e0\u4f30\u503c\u9677\u9631\u68c0\u6d4b"
                "\u6570\u636e\u3002\n")
            return "\n".join(lines)

        overall = (_safe_get(traps, "overall_level")
                   or _safe_get(traps, "level") or "-")
        lines.append(
            "\u7efc\u5408\u9884\u8b66\u7b49\u7ea7\uff1a{}\n".format(
                _trap_level_emoji(overall)))

        items = (_safe_get(traps, "items")
                 or _safe_get(traps, "checks"))
        if isinstance(items, list):
            lines.append(
                "| \u68c0\u6d4b\u9879 | \u72b6\u6001 | \u8be6\u60c5 |")
            lines.append("|--------|------|------|")
            for item in items:
                if isinstance(item, dict):
                    name = (item.get("name")
                            or item.get("check") or "-")
                    status = (item.get("status")
                              or item.get("level") or "-")
                    detail = (item.get("detail")
                              or item.get("description")
                              or item.get("message") or "-")
                    lines.append("| {} | {} | {} |".format(
                        name, _status_marker(status), detail))
        elif isinstance(items, dict):
            lines.append(
                "| \u68c0\u6d4b\u9879 | \u72b6\u6001 | \u8be6\u60c5 |")
            lines.append("|--------|------|------|")
            for k, v in items.items():
                if isinstance(v, dict):
                    status = (v.get("status")
                              or v.get("level") or "-")
                    detail = (v.get("detail")
                              or v.get("description")
                              or v.get("message") or "-")
                    lines.append("| {} | {} | {} |".format(
                        k, _status_marker(status), detail))
                else:
                    lines.append("| {} | {} | - |".format(
                        k, _status_marker(v)))

        return "\n".join(lines)

    def _determine_position(self, price, overval, optim,
                            fair, ideal, strong):
        """Heuristic position label based on price vs zone boundaries."""
        if price is None:
            return "-"
        try:
            p = float(price)
        except (TypeError, ValueError):
            return "-"

        thresholds = []
        for val, label_above, label_below in [
            (overval, "\u9ad8\u4f30\u533a\u57df", None),
            (optim, "\u504f\u9ad8\u533a\u57df", None),
            (fair, "\u5408\u7406\u504f\u9ad8",
             "\u5408\u7406\u504f\u4f4e"),
            (ideal, "\u5408\u7406\u504f\u4f4e",
             "\u7406\u60f3\u4e70\u5165\u533a\u95f4"),
            (strong, "\u7406\u60f3\u4e70\u5165\u533a\u95f4",
             "\u5f3a\u529b\u4e70\u5165\u533a\u95f4"),
        ]:
            if val is not None:
                thresholds.append(
                    (float(val), label_above, label_below))

        if not thresholds:
            return "-"

        thresholds.sort(key=lambda x: x[0], reverse=True)
        for val, above, below in thresholds:
            if p >= val:
                return above or "-"
        return thresholds[-1][2] or "-"

    def section_safety_margin(self):
        """Section 7: safety margin and buy zone."""
        sm = self._get_safety_margin()
        bz = self._get_buy_zone()
        models, _ = self._get_models_summary()
        _wp, wb, wo = self._get_weighted_composite(models)

        lines = ["## \u5b89\u5168\u8fb9\u9645\n"]

        if isinstance(sm, dict):
            total = (_safe_get(sm, "total")
                     or _safe_get(sm, "final")
                     or _safe_get(sm, "margin"))
            base_m = (_safe_get(sm, "base")
                      or _safe_get(sm, "base_margin"))
            ir_adj = (_safe_get(sm, "interest_rate_adj")
                      or _safe_get(sm, "ir_adjustment"))
            div_adj = (_safe_get(sm, "divergence_adj")
                       or _safe_get(sm, "divergence_adjustment"))
            trap_adj = (_safe_get(sm, "trap_adj")
                        or _safe_get(sm, "trap_adjustment"))
            conf_adj = (_safe_get(sm, "confidence_adj")
                        or _safe_get(sm, "confidence_adjustment"))
            lines.append(
                "\u6700\u7ec8\u5b89\u5168\u8fb9\u9645\uff1a{}\n".format(
                    _fmt_pct(total)))
            lines.append(
                "\u8ba1\u7b97\u660e\u7ec6\uff1a"
                "\u57fa\u7840{} + "
                "\u5229\u7387\u8c03\u6574{} + "
                "\u5206\u6b67\u8c03\u6574{} + "
                "\u9884\u8b66\u8c03\u6574{} + "
                "\u7f6e\u4fe1\u5ea6\u8c03\u6574{}\n".format(
                    _fmt_pct(base_m, fallback="0%"),
                    _fmt_pct(ir_adj, fallback="0%"),
                    _fmt_pct(div_adj, fallback="0%"),
                    _fmt_pct(trap_adj, fallback="0%"),
                    _fmt_pct(conf_adj, fallback="0%"),
                ))
        else:
            lines.append(
                "\u5b89\u5168\u8fb9\u9645\u6570\u636e\u4e0d\u53ef"
                "\u7528\u3002\n")

        # Dynamic buy zone
        lines.append("## \u52a8\u6001\u4e70\u5165\u533a\u95f4\n")

        if isinstance(bz, dict):
            overval = (_safe_get(bz, "overvaluation_warning")
                       or _safe_get(bz, "overval_line"))
            optim = (_safe_get(bz, "optimistic_value")
                     or _safe_get(bz, "optimistic") or wo)
            fair = (_safe_get(bz, "fair_value")
                    or _safe_get(bz, "intrinsic_value") or wb)
            ideal = (_safe_get(bz, "ideal_buy")
                     or _safe_get(bz, "ideal_buy_upper"))
            strong = (_safe_get(bz, "strong_buy")
                      or _safe_get(bz, "strong_buy_upper"))
            position = (_safe_get(bz, "position")
                        or _safe_get(bz, "current_position")
                        or self._determine_position(
                            self.price, overval, optim,
                            fair, ideal, strong))

            box_w = 41
            lines.append("```")
            lines.append("\u250c" + "\u2500" * box_w + "\u2510")
            lines.append(
                "\u2502  \u9ad8\u4f30\u9884\u8b66\u7ebf\uff1a{:<28s}\u2502".format(
                    _fmt_price(overval)))
            lines.append(
                "\u2502  \u4e50\u89c2\u60c5\u666f\u503c\uff1a{:<28s}\u2502".format(
                    _fmt_price(optim)))
            lines.append(
                "\u2502  \u2605 \u5f53\u524d\u80a1\u4ef7\uff1a{:<7s}"
                " \u2190 {:<19s}\u2502".format(
                    _fmt_price(self.price), str(position)))
            lines.append(
                "\u2502  \u5185\u5728\u4ef7\u503c\u4e2d\u67a2"
                "\uff1a{:<26s}\u2502".format(
                    _fmt_price(fair)))
            lines.append(
                "\u2502  \u7406\u60f3\u4e70\u5165\u4e0a\u9650"
                "\uff1a{:<26s}\u2502".format(
                    _fmt_price(ideal)))
            lines.append(
                "\u2502  \u5f3a\u529b\u4e70\u5165\u4e0a\u9650"
                "\uff1a{:<26s}\u2502".format(
                    _fmt_price(strong)))
            lines.append("\u2514" + "\u2500" * box_w + "\u2518")
            lines.append("```\n")
            lines.append(
                "\u5f53\u524d\u72b6\u6001\u5224\u65ad\uff1a"
                "\u3010{}\u3011\n".format(position))
        else:
            lines.append(
                "\u4e70\u5165\u533a\u95f4\u6570\u636e\u4e0d\u53ef"
                "\u7528\u3002\n")

        return "\n".join(lines)

    def section_reverse_engineering(self):
        """Section 8: reverse engineering analysis."""
        re_data = self._get_reverse_engineering()
        lines = ["## \u5e02\u573a\u9690\u542b\u9884\u671f\u5206\u6790\n"]

        if not re_data:
            lines.append(
                "\u6682\u65e0\u9006\u5411\u5de5\u7a0b\u5206\u6790"
                "\u6570\u636e\u3002\n")
            return "\n".join(lines)

        implied_fcf = (_safe_get(re_data, "implied_fcf_growth")
                       or _safe_get(re_data, "implied_growth_rate"))
        implied_margin = (_safe_get(re_data, "implied_margin")
                          or _safe_get(re_data, "implied_profit_margin"))
        comparison = (_safe_get(re_data, "comparison")
                      or _safe_get(re_data, "historical_comparison")
                      or "-")
        assessment = (_safe_get(re_data, "assessment")
                      or _safe_get(re_data, "market_assessment")
                      or "-")
        divergences = (_safe_get(re_data, "divergences")
                       or _safe_get(re_data, "key_divergences"))

        lines.append(
            "\u5f53\u524d\u80a1\u4ef7\u9690\u542b\u7684\u5173\u952e"
            "\u5047\u8bbe\uff1a")
        lines.append("- \u9690\u542bFCF\u589e\u957f\u7387\uff1a{}".format(
            _fmt_pct(implied_fcf)))
        lines.append("- \u9690\u542b\u5229\u6da6\u7387\uff1a{}".format(
            _fmt_pct(implied_margin)))
        lines.append("")
        lines.append(
            "\u4e0e\u5386\u53f2\u5bf9\u6bd4\uff1a{}\n".format(comparison))
        lines.append(
            "\u5e02\u573a\u9884\u671f\u8bc4\u4f30\uff1a{}\n".format(
                assessment))

        if divergences:
            lines.append("\u6838\u5fc3\u5206\u6b67\u70b9\uff1a")
            if isinstance(divergences, list):
                for d in divergences:
                    if isinstance(d, dict):
                        lines.append("- {}\uff1a{}".format(
                            d.get("point", "?"),
                            d.get("detail", "")))
                    else:
                        lines.append("- {}".format(d))
            elif isinstance(divergences, str):
                lines.append(divergences)
            lines.append("")

        return "\n".join(lines)

    def section_divergence(self):
        """Section 9: model divergence analysis."""
        div_data = self._get_divergence()
        lines = ["## \u6a21\u578b\u95f4\u5206\u6b67\u5206\u6790\n"]

        if not div_data:
            # Compute basic CV from model results
            models, _ = self._get_models_summary()
            base_vals = [float(m["base"])
                         for m in models
                         if m.get("base") is not None]
            if len(base_vals) >= 2:
                mean_v = sum(base_vals) / len(base_vals)
                if mean_v > 0:
                    var = sum((x - mean_v) ** 2
                              for x in base_vals) / len(base_vals)
                    std = var ** 0.5
                    cv = std / mean_v * 100
                    if cv < 10:
                        level = "\u4f4e"
                    elif cv < 20:
                        level = "\u4e2d\u7b49"
                    else:
                        level = "\u9ad8"
                    lines.append(
                        "\u5206\u6b67\u5ea6\uff08CV\uff09\uff1a"
                        "{:.1f}% \u2014 {}\n".format(cv, level))
                    if cv > 15:
                        lines.append(
                            "\u5404\u6a21\u578b\u4f30\u503c\u7ed3"
                            "\u679c\u5b58\u5728\u663e\u8457\u5206"
                            "\u6b67\uff0c\u5efa\u8bae\u5173\u6ce8"
                            "\u6a21\u578b\u5047\u8bbe\u5dee\u5f02"
                            "\uff0c\u4ee5\u6700\u4fdd\u5b88\u4f30"
                            "\u8ba1\u4e3a\u53c2\u8003\u57fa\u51c6"
                            "\u3002\n")
                else:
                    lines.append(
                        "\u6a21\u578b\u57fa\u51c6\u4f30\u503c\u5747"
                        "\u503c\u4e3a\u96f6\uff0c\u65e0\u6cd5\u8ba1"
                        "\u7b97\u5206\u6b67\u5ea6\u3002\n")
            else:
                lines.append(
                    "\u53ef\u7528\u6a21\u578b\u6570\u4e0d\u8db3"
                    "\uff08<2\uff09\uff0c\u65e0\u6cd5\u8ba1\u7b97"
                    "\u5206\u6b67\u5ea6\u3002\n")
            return "\n".join(lines)

        cv = (_safe_get(div_data, "cv")
              or _safe_get(div_data, "coefficient_of_variation"))
        level_cn = (_safe_get(div_data, "level_cn")
                    or _safe_get(div_data, "level") or "-")
        analysis = (_safe_get(div_data, "analysis")
                    or _safe_get(div_data, "detail") or "")

        lines.append(
            "\u5206\u6b67\u5ea6\uff08CV\uff09\uff1a{} \u2014 {}\n".format(
                _fmt_pct(cv), level_cn))
        if analysis:
            lines.append("{}\n".format(analysis))

        return "\n".join(lines)

    def section_disclaimer(self):
        """Section 10: disclaimer."""
        models, skipped = self._get_models_summary()
        mode_str, mode_note = self._get_mode()

        applicable = ("\u3001".join(m["name"] for m in models)
                      if models else "\u65e0")
        skipped_names = ("\u3001".join(s["name"] for s in skipped)
                         if skipped else "\u65e0")
        skipped_reasons = ("\u3001".join(s["reason"] for s in skipped)
                           if skipped else "\u65e0")

        lines = [
            "## \u91cd\u8981\u58f0\u660e\n",
            ("\u2460 \u60c5\u666f\u6982\u7387\u4e3a\u4e3b\u89c2"
             "\u6743\u91cd\uff0c\u975e\u7cbe\u786e\u7edf\u8ba1"
             "\u6982\u7387"),
            ("\u2461 \u4f30\u503c\u7ed3\u679c\u4e3a\u533a\u95f4"
             "\u5224\u65ad\uff0c\u975e\u7cbe\u786e\u9884\u6d4b"),
            ("\u2462 \u672c\u62a5\u544a\u4e0d\u6784\u6210\u6295"
             "\u8d44\u5efa\u8bae\uff0c\u4e0d\u6d89\u53ca\u4ed3"
             "\u4f4d\u6307\u5bfc"),
            ("\u2463 \u6a21\u578b\u5c40\u9650\u6027\uff1a"
             "{}\u53ef\u7528\uff0c{}\u56e0{}\u8df3\u8fc7".format(
                 applicable, skipped_names, skipped_reasons)),
            ("\u2464 \u8fd0\u884c\u6a21\u5f0f\uff1a\u6a21\u5f0f"
             "{}\uff0c{}".format(mode_str, mode_note)),
        ]
        return "\n".join(lines)

    # -- full report ---------------------------------------------------------

    def build_full_report(self):
        sections = [
            self.section_overview(),
            self.section_preprocessing(),
            self.section_model_details(),
            self.section_sensitivity(),
            self.section_valuation_matrix(),
            self.section_traps(),
            self.section_safety_margin(),
            self.section_reverse_engineering(),
            self.section_divergence(),
            self.section_disclaimer(),
        ]
        return "\n\n".join(sections) + "\n"

    # -- focused answer mode -------------------------------------------------

    def build_focused_report(self, question):
        """Map a question to relevant sections and produce a concise
        answer."""

        question_lower = question.lower() if question else ""

        # Determine which data to surface
        conclusion = ""
        points = []
        caveats = []

        models, skipped = self._get_models_summary()
        wp, wb, wo = self._get_weighted_composite(models)
        bz = self._get_buy_zone()
        traps = self._get_traps()
        sm = self._get_safety_margin()
        div_data = self._get_divergence()
        re_data = self._get_reverse_engineering()

        price_str = _fmt_price(self.price)
        base_str = _fmt_price(wb)

        # Question routing --------------------------------------------------
        if any(kw in question_lower for kw in [
            "\u4fbf\u5b9c", "\u8d35", "\u4f30\u503c",
            "\u9ad8\u4f30", "\u4f4e\u4f30",
        ]):
            if (wb is not None and self.price is not None
                    and wb != 0):
                pct = (self.price - wb) / wb * 100
                direction = ("\u6ea2\u4ef7" if pct > 0
                             else "\u6298\u4ef7")
                conclusion = (
                    "\u5f53\u524d\u80a1\u4ef7{}\u76f8\u5bf9"
                    "\u57fa\u51c6\u4f30\u503c{}{}\u4e86"
                    "{:.1f}%\u3002".format(
                        price_str, base_str,
                        direction, abs(pct)))
            else:
                conclusion = (
                    "\u5f53\u524d\u80a1\u4ef7{}\uff0c"
                    "\u57fa\u51c6\u4f30\u503c{}\u3002".format(
                        price_str, base_str))
            points.append(
                "\u52a0\u6743\u57fa\u51c6\u4f30\u503c\uff1a{}".format(
                    base_str))
            points.append(
                "\u60b2\u89c2-\u4e50\u89c2\u533a\u95f4\uff1a"
                "{} ~ {}".format(_fmt_price(wp), _fmt_price(wo)))
            position = (_safe_get(bz, "position")
                        or _safe_get(bz, "current_position")
                        or "-")
            points.append(
                "\u5f53\u524d\u6240\u5904\u533a\u95f4\uff1a{}".format(
                    position))
            caveats.append(
                "\u4f30\u503c\u57fa\u4e8e\u6a21\u578b\u5047\u8bbe"
                "\uff0c\u5b9e\u9645\u504f\u5dee\u53ef\u80fd\u8f83"
                "\u5927")

        elif any(kw in question_lower for kw in [
            "\u503c\u5f97\u4e70", "\u4e70\u5165",
            "\u4e70\u4e0d\u4e70", "\u80fd\u4e70",
        ]):
            position = (_safe_get(bz, "position")
                        or _safe_get(bz, "current_position")
                        or "-")
            conclusion = (
                "\u5f53\u524d\u80a1\u4ef7{}\u5904\u4e8e"
                "\u3010{}\u3011\u3002".format(price_str, position))
            if bz:
                ideal = (_safe_get(bz, "ideal_buy")
                         or _safe_get(bz, "ideal_buy_upper"))
                strong = (_safe_get(bz, "strong_buy")
                          or _safe_get(bz, "strong_buy_upper"))
                points.append(
                    "\u7406\u60f3\u4e70\u5165\u4e0a\u9650\uff1a{}".format(
                        _fmt_price(ideal)))
                points.append(
                    "\u5f3a\u529b\u4e70\u5165\u4e0a\u9650\uff1a{}".format(
                        _fmt_price(strong)))
            if traps:
                trap_level = (_safe_get(traps, "overall_level")
                              or _safe_get(traps, "level") or "-")
                points.append(
                    "\u4f30\u503c\u9677\u9631\u9884\u8b66\u7b49"
                    "\u7ea7\uff1a{}".format(
                        _trap_level_emoji(trap_level)))
            caveats.append(
                "\u672c\u62a5\u544a\u4e0d\u6784\u6210\u6295\u8d44"
                "\u5efa\u8bae")
            caveats.append(
                "\u9700\u7ed3\u5408\u4e2a\u4eba\u98ce\u9669\u504f"
                "\u597d\u548c\u8d44\u91d1\u60c5\u51b5\u5224\u65ad")

        elif any(kw in question_lower for kw in [
            "\u98ce\u9669", "\u5371\u9669", "\u4e8f\u635f",
        ]):
            conclusion = (
                "\u60b2\u89c2\u60c5\u666f\u4f30\u503c\u4e3a"
                "{}\u3002".format(_fmt_price(wp)))
            if (self.price is not None and wp is not None
                    and self.price != 0):
                downside = ((self.price - wp) / self.price * 100)
                points.append(
                    "\u60b2\u89c2\u60c5\u666f\u4e0b\u884c\u7a7a"
                    "\u95f4\uff1a{:.1f}%".format(downside))
            if traps:
                trap_level = (_safe_get(traps, "overall_level")
                              or _safe_get(traps, "level") or "-")
                points.append(
                    "\u4f30\u503c\u9677\u9631\u9884\u8b66\u7b49"
                    "\u7ea7\uff1a{}".format(
                        _trap_level_emoji(trap_level)))
                items = (_safe_get(traps, "items")
                         or _safe_get(traps, "checks") or [])
                if isinstance(items, list):
                    warnings_list = [
                        i for i in items
                        if isinstance(i, dict) and str(
                            i.get("status", "")).lower()
                        in ("warning", "alert", "caution")]
                    for w in warnings_list[:3]:
                        points.append(
                            "\u9884\u8b66\uff1a{} \u2014 {}".format(
                                w.get("name", "?"),
                                w.get("detail",
                                      w.get("description", ""))))
            caveats.append(
                "\u60b2\u89c2\u60c5\u666f\u5047\u8bbe\u7ecf\u6d4e"
                "\u6216\u884c\u4e1a\u51fa\u73b0\u4e0d\u5229\u53d8"
                "\u5316")

        elif any(kw in question_lower for kw in [
            "\u4ec0\u4e48\u4ef7\u683c\u4e70",
            "\u591a\u5c11\u94b1\u4e70",
            "\u4e70\u5165\u4ef7",
        ]):
            if bz:
                ideal = (_safe_get(bz, "ideal_buy")
                         or _safe_get(bz, "ideal_buy_upper"))
                strong = (_safe_get(bz, "strong_buy")
                          or _safe_get(bz, "strong_buy_upper"))
                fair = (_safe_get(bz, "fair_value")
                        or _safe_get(bz, "intrinsic_value") or wb)
                conclusion = (
                    "\u5185\u5728\u4ef7\u503c\u4e2d\u67a2{}\uff0c"
                    "\u5efa\u8bae\u5173\u6ce8{}\u4ee5\u4e0b\u7684"
                    "\u4e70\u5165\u673a\u4f1a\u3002".format(
                        _fmt_price(fair), _fmt_price(ideal)))
                points.append(
                    "\u7406\u60f3\u4e70\u5165\u4e0a\u9650\uff1a{}".format(
                        _fmt_price(ideal)))
                points.append(
                    "\u5f3a\u529b\u4e70\u5165\u4e0a\u9650\uff1a{}".format(
                        _fmt_price(strong)))
                points.append(
                    "\u5f53\u524d\u80a1\u4ef7\uff1a{}".format(
                        price_str))
            else:
                conclusion = (
                    "\u4e70\u5165\u533a\u95f4\u6570\u636e\u4e0d"
                    "\u53ef\u7528\u3002")
            if sm:
                total_margin = (
                    _safe_get(sm, "total")
                    or _safe_get(sm, "final")
                    or _safe_get(sm, "margin"))
                caveats.append(
                    "\u5b89\u5168\u8fb9\u9645\u8981\u6c42\uff1a{}".format(
                        _fmt_pct(total_margin)))
            caveats.append(
                "\u5b9e\u9645\u4e70\u5165\u9700\u8003\u8651\u5e02"
                "\u573a\u60c5\u7eea\u548c\u6d41\u52a8\u6027")

        elif any(kw in question_lower for kw in [
            "\u4ec0\u4e48\u65f6\u5019\u5356",
            "\u5356\u51fa", "\u76ee\u6807\u4ef7",
        ]):
            if bz:
                overval = (_safe_get(bz, "overvaluation_warning")
                           or _safe_get(bz, "overval_line"))
                optim = (_safe_get(bz, "optimistic_value")
                         or _safe_get(bz, "optimistic") or wo)
                conclusion = (
                    "\u4e50\u89c2\u60c5\u666f\u4f30\u503c{}\uff0c"
                    "\u9ad8\u4f30\u9884\u8b66\u7ebf{}\u3002".format(
                        _fmt_price(optim), _fmt_price(overval)))
                points.append(
                    "\u4e50\u89c2\u60c5\u666f\u503c\uff1a{}".format(
                        _fmt_price(optim)))
                points.append(
                    "\u9ad8\u4f30\u9884\u8b66\u7ebf\uff1a{}".format(
                        _fmt_price(overval)))
            else:
                conclusion = (
                    "\u5356\u51fa\u53c2\u8003\u6570\u636e\u4e0d"
                    "\u53ef\u7528\u3002")
            caveats.append(
                "\u5356\u51fa\u65f6\u673a\u9700\u7ed3\u5408\u57fa"
                "\u672c\u9762\u53d8\u5316\u52a8\u6001\u5224\u65ad")

        elif any(kw in question_lower for kw in [
            "\u5e02\u573a\u9884\u671f", "\u9690\u542b",
            "\u9884\u671f\u4ec0\u4e48",
        ]):
            if re_data:
                implied_fcf = (
                    _safe_get(re_data, "implied_fcf_growth")
                    or _safe_get(re_data, "implied_growth_rate"))
                assessment = (
                    _safe_get(re_data, "assessment")
                    or _safe_get(re_data, "market_assessment")
                    or "-")
                conclusion = (
                    "\u5e02\u573a\u9884\u671f\u8bc4\u4f30\uff1a"
                    "{}\u3002".format(assessment))
                points.append(
                    "\u9690\u542bFCF\u589e\u957f\u7387\uff1a{}".format(
                        _fmt_pct(implied_fcf)))
                implied_margin = (
                    _safe_get(re_data, "implied_margin")
                    or _safe_get(re_data, "implied_profit_margin"))
                points.append(
                    "\u9690\u542b\u5229\u6da6\u7387\uff1a{}".format(
                        _fmt_pct(implied_margin)))
            else:
                conclusion = (
                    "\u9006\u5411\u5de5\u7a0b\u5206\u6790\u6570"
                    "\u636e\u4e0d\u53ef\u7528\u3002")
            caveats.append(
                "\u9690\u542b\u9884\u671f\u4e3a\u6a21\u578b\u9006"
                "\u63a8\u7ed3\u679c\uff0c\u975e\u5e02\u573a\u5171"
                "\u8bc6")

        elif any(kw in question_lower for kw in [
            "\u53ef\u4fe1", "\u51c6\u786e", "\u53ef\u9760",
            "\u7f6e\u4fe1",
        ]):
            confidence = self._get_confidence()
            conclusion = (
                "\u7efc\u5408\u7f6e\u4fe1\u5ea6\uff1a{}\u3002".format(
                    _confidence_emoji(confidence)))
            if div_data:
                cv = (_safe_get(div_data, "cv")
                      or _safe_get(div_data,
                                   "coefficient_of_variation"))
                level = (_safe_get(div_data, "level_cn")
                         or _safe_get(div_data, "level") or "-")
                points.append(
                    "\u6a21\u578b\u5206\u6b67\u5ea6\uff08CV\uff09"
                    "\uff1a{} \u2014 {}".format(
                        _fmt_pct(cv), level))
            points.append(
                "\u53ef\u7528\u6a21\u578b\u6570\uff1a{}".format(
                    len(models)))
            if skipped:
                points.append(
                    "\u8df3\u8fc7\u6a21\u578b\uff1a{}".format(
                        "\u3001".join(s["name"] for s in skipped)))
            caveats.append(
                "\u6a21\u578b\u8d8a\u5c11\u3001\u5206\u6b67\u8d8a"
                "\u5927\uff0c\u7ed3\u8bba\u53ef\u9760\u6027\u8d8a"
                "\u4f4e")

        elif any(kw in question_lower for kw in [
            "\u540c\u884c", "\u884c\u4e1a", "\u5bf9\u6bd4",
            "\u6bd4\u8f83",
        ]):
            pe = self._get_snapshot_field("pe", "pe_ttm")
            pb = self._get_snapshot_field("pb", "pb_mrq")
            industry = self._get_snapshot_field(
                "industry", "industry_name")
            conclusion = (
                "{}\u6240\u5c5e\u884c\u4e1a\u4e3a{}\u3002".format(
                    self.name, industry or "\u672a\u77e5"))
            points.append("\u5f53\u524dPE(TTM)\uff1a{}".format(
                _fmt_bei(pe)))
            points.append("\u5f53\u524dPB(MRQ)\uff1a{}".format(
                _fmt_bei(pb)))
            ind_pe = self._get_snapshot_field(
                "industry_pe", "industry_avg_pe")
            ind_pb = self._get_snapshot_field(
                "industry_pb", "industry_avg_pb")
            if ind_pe is not None:
                points.append(
                    "\u884c\u4e1a\u5e73\u5747PE\uff1a{}".format(
                        _fmt_bei(ind_pe)))
            if ind_pb is not None:
                points.append(
                    "\u884c\u4e1a\u5e73\u5747PB\uff1a{}".format(
                        _fmt_bei(ind_pb)))
            caveats.append(
                "\u540c\u884c\u4e1a\u4e0d\u540c\u516c\u53f8\u7684"
                "\u4e1a\u52a1\u7ed3\u6784\u53ef\u80fd\u5dee\u5f02"
                "\u8f83\u5927")

        else:
            # Default: overview + valuation summary
            conclusion = (
                "\u5f53\u524d\u80a1\u4ef7{}\uff0c\u52a0\u6743"
                "\u57fa\u51c6\u4f30\u503c{}\u3002".format(
                    price_str, base_str))
            if (wb is not None and self.price is not None
                    and wb != 0):
                pct = (self.price - wb) / wb * 100
                direction = ("\u6ea2\u4ef7" if pct > 0
                             else "\u6298\u4ef7")
                points.append(
                    "\u76f8\u5bf9\u57fa\u51c6{}\uff1a"
                    "{:.1f}%".format(direction, abs(pct)))
            points.append(
                "\u60b2\u89c2-\u4e50\u89c2\u533a\u95f4\uff1a"
                "{} ~ {}".format(
                    _fmt_price(wp), _fmt_price(wo)))
            confidence = self._get_confidence()
            points.append(
                "\u7efc\u5408\u7f6e\u4fe1\u5ea6\uff1a{}".format(
                    _confidence_emoji(confidence)))
            caveats.append(
                "\u672c\u62a5\u544a\u4e0d\u6784\u6210\u6295\u8d44"
                "\u5efa\u8bae")

        # Ensure we have at least something
        if not points:
            points.append(
                "\u8be6\u89c1\u5b8c\u6574\u4f30\u503c\u62a5\u544a")
        if not caveats:
            caveats.append(
                "\u672c\u62a5\u544a\u4ec5\u4f9b\u53c2\u8003")

        # Build output
        out_lines = [
            "\u2501" * 31,
            "\u9488\u5bf9\u60a8\u7684\u95ee\u9898\uff1a"
            "\u300c{}\u300d".format(question),
            "\u2500" * 32,
            "\u6838\u5fc3\u7ed3\u8bba\uff1a{}\n".format(conclusion),
            "\u652f\u6491\u4f9d\u636e\uff1a",
        ]
        for i, pt in enumerate(points[:5], 1):
            out_lines.append("{}  {}".format(
                _circled_num(i), pt))
        out_lines.append("")
        out_lines.append("\u9700\u8981\u6ce8\u610f\uff1a")
        for c in caveats[:3]:
            out_lines.append("- {}".format(c))
        out_lines.append("")
        out_lines.append(
            "\u5982\u9700\u67e5\u770b\u5b8c\u6574\u4f30\u503c"
            "\u62a5\u544a\uff0c\u8bf7\u544a\u77e5\u3002")
        out_lines.append("\u2501" * 31)

        return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def generate_report(integrated_file, data_file, preprocessed_file,
                    model_results_file, output_dir,
                    visualize=False, fmt="full", question=None):
    """Top-level function: load data, build report, write output."""

    integrated = load_json(integrated_file)
    data = load_json(data_file)
    preprocessed = load_json(preprocessed_file)
    model_results = load_json(model_results_file)

    os.makedirs(output_dir, exist_ok=True)

    visualizer = ValuationVisualizer()

    builder = ReportBuilder(
        integrated=integrated,
        data=data,
        preprocessed=preprocessed,
        model_results=model_results,
        visualize=visualize,
        output_dir=output_dir,
        visualizer=visualizer,
    )

    if fmt == "focused" and question:
        report_content = builder.build_focused_report(question)
    else:
        report_content = builder.build_full_report()

    # Determine output filename
    code = builder.code
    name = builder.name
    filename = "{}_{}_\u4f30\u503c\u5206\u6790\u62a5\u544a.md".format(
        code, name)
    output_path = os.path.join(output_dir, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    print("Report saved to: {}".format(output_path))
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate a comprehensive stock valuation "
                    "report in Markdown.")
    parser.add_argument(
        "--integrated-file", required=True,
        help="Path to {code}_integrated.json")
    parser.add_argument(
        "--data-file", required=True,
        help="Path to {code}_valuation_data.json")
    parser.add_argument(
        "--preprocessed-file", required=True,
        help="Path to {code}_preprocessed.json")
    parser.add_argument(
        "--model-results-file", required=True,
        help="Path to {code}_model_results.json")
    parser.add_argument(
        "--output-dir", default="./output",
        help="Directory for output files (default: ./output)")
    parser.add_argument(
        "--visualize", action="store_true",
        help="Generate charts (matplotlib PNG + ASCII fallback)")
    parser.add_argument(
        "--format", dest="fmt", choices=["full", "focused"],
        default="full",
        help="Report format: full (default) or focused")
    parser.add_argument(
        "--question", default=None,
        help="Question for focused mode (Chinese)")

    args = parser.parse_args()

    if args.fmt == "focused" and not args.question:
        parser.error("--question is required when --format is 'focused'")

    generate_report(
        integrated_file=args.integrated_file,
        data_file=args.data_file,
        preprocessed_file=args.preprocessed_file,
        model_results_file=args.model_results_file,
        output_dir=args.output_dir,
        visualize=args.visualize,
        fmt=args.fmt,
        question=args.question,
    )


if __name__ == "__main__":
    main()
