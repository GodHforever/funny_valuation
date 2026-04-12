#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
估值模型计算引擎 (Valuation Model Engine)

零依赖(仅 Python 3.6+ 标准库), 实现 8 大估值模型:
  1. DCF      - 现金流折现
  2. PE       - 市盈率
  3. PB       - 市净率
  4. EV/EBITDA
  5. PS       - 市销率
  6. DDM      - 股息折现
  7. SOTP     - 分部估值
  8. REVERSE  - 逆向工程(市场隐含预期)

CLI:
  python valuation_models.py --preprocessed-file 600519_preprocessed.json --output-dir ./output
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# 常量 / 默认参数
# ---------------------------------------------------------------------------
DEFAULT_RF = 0.03              # 无风险利率 3%
DEFAULT_ERP = 0.06             # 股权风险溢价 6%
DEFAULT_TERMINAL_G = 0.03      # 默认终值增长率
A_SHARE_TERMINAL_G_CAP = 0.04  # A 股终值增长率上限
US_TERMINAL_G_CAP = 0.03       # 美股终值增长率上限
DEFAULT_PROJECTION_YEARS = 5   # DCF 显式预测期
BINARY_SEARCH_TOL = 0.01       # 逆向工程二分搜索容差
BINARY_SEARCH_MAX_ITER = 100   # 逆向工程最大迭代次数


# =========================================================================
# 工具函数
# =========================================================================

def safe_div(numerator, denominator, default=0.0):
    """安全除法, 避免 ZeroDivisionError"""
    if denominator is None or denominator == 0:
        return default
    return numerator / denominator


def safe_get(d, *keys, default=None):
    """多层 dict 安全取值, 任一层为 None 即返回 default"""
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        else:
            return default
        if cur is None:
            return default
    return cur


def _clamp(v, lo, hi):
    """将值限制在 [lo, hi] 区间"""
    return max(lo, min(v, hi))


# =========================================================================
# 基类
# =========================================================================

class BaseValuationModel:
    """估值模型基类 — 统一接口"""
    MODEL_NAME = ""

    def calculate(self, params):
        # type: (dict) -> dict
        raise NotImplementedError

    @staticmethod
    def _result(model_name, applicable, pessimistic=None, base=None,
                optimistic=None, details=None, warnings=None,
                applicability_note=""):
        """构造标准化的返回字典"""
        return {
            "model_name": model_name,
            "applicable": applicable,
            "applicability_note": applicability_note,
            "pessimistic": pessimistic,
            "base": base,
            "optimistic": optimistic,
            "details": details or {},
            "warnings": warnings or [],
        }


# =========================================================================
# 4.1  DCF 模型 — 现金流折现
# =========================================================================

class DCFModel(BaseValuationModel):
    MODEL_NAME = "DCF"

    # -- Mode A 定性因子映射 --
    _SUSTAINABILITY = {"强": 1.15, "较强": 1.05, "中性": 1.0, "较弱": 0.85, "弱": 0.7}
    _CERTAINTY_RANGE = {"高": 0.15, "中": 0.25, "低": 0.40}
    _CYCLE = {"上行": 0.9, "下行": 1.1, "中性": 1.0}

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []

        # ---- 基础参数提取 ----
        wacc_raw = safe_get(params, "wacc", "wacc", default=10.0)
        wacc = wacc_raw / 100.0 if wacc_raw > 1 else wacc_raw

        base_fcf = safe_get(params, "fcf", "base_fcf", default=0.0)
        if base_fcf is None or base_fcf <= 0:
            return self._result(self.MODEL_NAME, False,
                                applicability_note="基础自由现金流 <= 0, 不适用 DCF")

        fcf_growth = safe_get(params, "fcf", "trend_slope", default=0.08)
        if fcf_growth is None:
            fcf_growth = 0.08

        mode = safe_get(params, "meta", "mode", default="B")
        ebitda_ttm = safe_get(params, "ebitda_ttm", default=0.0) or 0.0
        net_debt = safe_get(params, "real_time_snapshot", "net_debt", default=0.0) or 0.0
        total_shares = safe_get(params, "real_time_snapshot", "total_shares", default=1.0)
        if total_shares is None or total_shares <= 0:
            total_shares = 1.0
        industry_ev_ebitda = safe_get(params, "industry_data", "industry_ev_ebitda",
                                     default=15.0) or 15.0

        # ---- WACC 合理性检查 ----
        rf = DEFAULT_RF
        if wacc < rf + 0.02:
            warnings.append(
                "WACC({:.2f}%) 低于 Rf+2%({:.2f}%), 可能偏低".format(
                    wacc * 100, (rf + 0.02) * 100))
        if wacc > 0.18:
            warnings.append("WACC({:.2f}%) > 18%, 可能偏高".format(wacc * 100))

        # ---- 终值增长率 ----
        terminal_g = min(DEFAULT_TERMINAL_G, A_SHARE_TERMINAL_G_CAP)
        if wacc <= terminal_g:
            terminal_g = wacc * 0.5
            warnings.append(
                "终值增长率被调低至 {:.2f}% 以保证 WACC > g".format(terminal_g * 100))

        # ---- Mode A 定性因子调整 / Mode B 默认 ----
        scenario_range = 0.25          # 默认 ±25% (Mode B)
        growth_mult = 1.0
        cycle_mult = 1.0

        if mode == "A" and params.get("qualitative_adjustments"):
            qa = params["qualitative_adjustments"]
            growth_mult = self._SUSTAINABILITY.get(
                safe_get(qa, "growth_sustainability", default="中性"), 1.0)
            scenario_range = self._CERTAINTY_RANGE.get(
                safe_get(qa, "business_certainty", default="中"), 0.25)
            cycle_mult = self._CYCLE.get(
                safe_get(qa, "cycle_position", default="中性"), 1.0)

        adjusted_fcf = base_fcf * growth_mult * cycle_mult

        # ---- 5 年显式 FCF 预测 (基准) ----
        fcf_projections = []
        for t in range(1, DEFAULT_PROJECTION_YEARS + 1):
            fcf_t = adjusted_fcf * ((1 + fcf_growth) ** t)
            fcf_projections.append(round(fcf_t, 2))

        # ---- 终值 — 两种方法 ----
        # 永续增长法
        fcf_n1 = fcf_projections[-1] * (1 + terminal_g)
        tv_perpetuity = safe_div(fcf_n1, wacc - terminal_g, default=0.0)

        # 退出倍数法
        ebitda_n = (ebitda_ttm * ((1 + fcf_growth) ** DEFAULT_PROJECTION_YEARS)
                    if ebitda_ttm > 0 else 0.0)
        tv_exit_multiple = ebitda_n * industry_ev_ebitda if ebitda_n > 0 else 0.0

        # 终值分歧检查 & 取均值
        tv_divergence = 0.0
        tv_used = tv_perpetuity
        if tv_perpetuity > 0 and tv_exit_multiple > 0:
            tv_divergence = (abs(tv_perpetuity - tv_exit_multiple)
                             / max(tv_perpetuity, tv_exit_multiple))
            tv_used = (tv_perpetuity + tv_exit_multiple) / 2.0
        elif tv_exit_multiple > 0:
            tv_used = tv_exit_multiple

        if tv_divergence > 0.20:
            warnings.append(
                "终值计算方法存在显著分歧 ({:.1f}%)".format(tv_divergence * 100))

        # ---- 通用 EV 计算函数 ----
        def _calc_ev(fcf_list, w, tv):
            ev = 0.0
            for t_idx, fcf_val in enumerate(fcf_list, 1):
                ev += fcf_val / ((1 + w) ** t_idx)
            ev += tv / ((1 + w) ** DEFAULT_PROJECTION_YEARS)
            return ev

        # ---- 基准企业 / 权益 / 每股价值 ----
        ev_base = _calc_ev(fcf_projections, wacc, tv_used)
        equity_base = ev_base - net_debt
        ps_base = safe_div(equity_base, total_shares)

        # ---- 悲观场景: FCF*(1-range), WACC+0.5%, g-0.5% ----
        fcf_pess = [f * (1 - scenario_range) for f in fcf_projections]
        wacc_pess = wacc + 0.005
        g_pess = max(terminal_g - 0.005, 0.005)
        if wacc_pess <= g_pess:
            g_pess = wacc_pess * 0.5
        tv_pess = safe_div(fcf_pess[-1] * (1 + g_pess), wacc_pess - g_pess)
        ev_pess = _calc_ev(fcf_pess, wacc_pess, tv_pess)
        ps_pess = safe_div(ev_pess - net_debt, total_shares)

        # ---- 乐观场景: FCF*(1+range), WACC-0.5%, g+0.5% ----
        fcf_opt = [f * (1 + scenario_range) for f in fcf_projections]
        wacc_opt = max(wacc - 0.005, 0.01)
        g_opt = min(terminal_g + 0.005, A_SHARE_TERMINAL_G_CAP)
        if wacc_opt <= g_opt:
            g_opt = wacc_opt * 0.5
        tv_opt = safe_div(fcf_opt[-1] * (1 + g_opt), wacc_opt - g_opt)
        ev_opt = _calc_ev(fcf_opt, wacc_opt, tv_opt)
        ps_opt = safe_div(ev_opt - net_debt, total_shares)

        # ---- 敏感性矩阵: 3 WACC x 4 g = 12 值 ----
        sensitivity_matrix = []
        wacc_steps = [wacc - 0.01, wacc, wacc + 0.01]
        g_steps = [0.01, 0.02, 0.03, 0.04]
        for g_val in g_steps:
            for w_val in wacc_steps:
                if w_val <= g_val:
                    sensitivity_matrix.append({
                        "wacc": round(w_val * 100, 2),
                        "g": round(g_val * 100, 2),
                        "value": None,
                        "note": "WACC <= g, 无效",
                    })
                    continue
                tv_s = safe_div(fcf_projections[-1] * (1 + g_val), w_val - g_val)
                ev_s = _calc_ev(fcf_projections, w_val, tv_s)
                val_s = safe_div(ev_s - net_debt, total_shares)
                is_base = (abs(w_val - wacc) < 1e-6
                           and abs(g_val - terminal_g) < 1e-6)
                sensitivity_matrix.append({
                    "wacc": round(w_val * 100, 2),
                    "g": round(g_val * 100, 2),
                    "value": round(val_s, 2),
                    "is_base_case": is_base,
                })

        details = {
            "wacc": round(wacc * 100, 2),
            "terminal_g": round(terminal_g * 100, 2),
            "fcf_projections": [round(f, 2) for f in fcf_projections],
            "terminal_value_perpetuity": round(tv_perpetuity, 2),
            "terminal_value_exit_multiple": round(tv_exit_multiple, 2),
            "terminal_value_divergence": "{:.1f}%".format(tv_divergence * 100),
            "enterprise_value_base": round(ev_base, 2),
            "equity_value_base": round(equity_base, 2),
            "scenario_range": scenario_range,
            "sensitivity_matrix": sensitivity_matrix,
        }

        return self._result(
            self.MODEL_NAME, True,
            pessimistic=round(ps_pess, 2),
            base=round(ps_base, 2),
            optimistic=round(ps_opt, 2),
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 4.2  PE 模型 — 市盈率
# =========================================================================

class PEModel(BaseValuationModel):
    MODEL_NAME = "PE"

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []

        # ---- 基础数据 ----
        eps_pess = safe_get(params, "eps_forecast", "pessimistic", default=0.0) or 0.0
        eps_base = safe_get(params, "eps_forecast", "base", default=0.0) or 0.0
        eps_opt = safe_get(params, "eps_forecast", "optimistic", default=0.0) or 0.0
        eps_cagr = safe_get(params, "eps_forecast", "cagr", default=0.10) or 0.10

        pe_ttm = safe_get(params, "real_time_snapshot", "pe_ttm", default=0.0) or 0.0
        pe_mean = safe_get(params, "historical_valuation", "pe_range", "mean",
                           default=25.0) or 25.0
        pe_high = safe_get(params, "historical_valuation", "pe_range", "high",
                           default=50.0) or 50.0
        pe_low_hist = safe_get(params, "historical_valuation", "pe_range", "low",
                               default=10.0) or 10.0
        pe_percentile = safe_get(params, "historical_valuation", "pe_range",
                                 "current_percentile", default=0.5) or 0.5
        industry_pe = safe_get(params, "industry_data", "industry_pe",
                               default=20.0) or 20.0

        if eps_base <= 0:
            return self._result(self.MODEL_NAME, False,
                                applicability_note="预测 EPS <= 0, PE 模型不适用")

        # ---- Step 1: 历史 PE 锚定 & 百分位评估 ----
        if pe_percentile > 0.80:
            percentile_label = "偏贵"
        elif pe_percentile < 0.20:
            percentile_label = "偏低"
        else:
            percentile_label = "中性"

        # ---- Step 2: 行业 PE 对比 ----
        premium_to_industry = safe_div(pe_ttm - industry_pe, industry_pe)

        # ---- Step 3: PEG 检查 ----
        eps_growth_pct = eps_cagr * 100 if eps_cagr > 0 else 1.0
        peg = safe_div(pe_ttm, eps_growth_pct, default=None)
        if peg is not None:
            if peg < 0.8:
                peg_label = "低估"
            elif peg <= 1.2:
                peg_label = "合理"
            elif peg <= 1.5:
                peg_label = "偏高"
            else:
                peg_label = "显著偏高"
        else:
            peg_label = "N/A"

        # ---- Step 4: 利率调整 ----
        rf = DEFAULT_RF
        erp = DEFAULT_ERP
        # PE_adjustment = (3% + ERP) / (Rf + ERP)
        pe_adjustment = safe_div(0.03 + erp, rf + erp, default=1.0)
        adjusted_pe = pe_mean * pe_adjustment

        # ---- Step 5: 综合 PE 区间 ----
        pe_low = min(adjusted_pe, industry_pe) * 0.9
        pe_base_val = adjusted_pe
        pe_high_val = max(adjusted_pe, industry_pe) * 1.1

        # ---- 估值结果 ----
        val_pess = eps_pess * pe_low
        val_base = eps_base * pe_base_val
        val_opt = eps_opt * pe_high_val

        details = {
            "historical_pe_mean": round(pe_mean, 2),
            "historical_pe_percentile": round(pe_percentile, 4),
            "percentile_assessment": percentile_label,
            "industry_pe": round(industry_pe, 2),
            "premium_to_industry": round(premium_to_industry, 4),
            "peg": round(peg, 2) if peg is not None else None,
            "peg_assessment": peg_label,
            "pe_adjustment_factor": round(pe_adjustment, 4),
            "adjusted_pe": round(adjusted_pe, 2),
            "pe_low": round(pe_low, 2),
            "pe_base": round(pe_base_val, 2),
            "pe_high": round(pe_high_val, 2),
            "eps_pessimistic": eps_pess,
            "eps_base": eps_base,
            "eps_optimistic": eps_opt,
        }

        return self._result(
            self.MODEL_NAME, True,
            pessimistic=round(val_pess, 2),
            base=round(val_base, 2),
            optimistic=round(val_opt, 2),
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 4.3  PB 模型 — 市净率
# =========================================================================

class PBModel(BaseValuationModel):
    MODEL_NAME = "PB"

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []

        bvps = safe_get(params, "bvps", default=0.0) or 0.0
        roe_mean = safe_get(params, "roe_mean", default=0.10) or 0.10
        wacc_raw = safe_get(params, "wacc", "wacc", default=10.0)
        wacc = wacc_raw / 100.0 if wacc_raw > 1 else wacc_raw

        pb_mean = safe_get(params, "historical_valuation", "pb_range", "mean",
                           default=3.0) or 3.0
        pb_percentile = safe_get(params, "historical_valuation", "pb_range",
                                 "current_percentile", default=0.5)

        if bvps <= 0:
            return self._result(self.MODEL_NAME, False,
                                applicability_note="每股净资产 <= 0, PB 模型不适用")

        # ---- 理论 PB (Gordon 模型): PB = (ROE - g) / (WACC - g) ----
        g = DEFAULT_TERMINAL_G
        if wacc <= g:
            g = wacc * 0.5
            warnings.append(
                "g 被调低至 {:.2f}% 以保证 WACC > g".format(g * 100))

        pb_theory = safe_div(roe_mean - g, wacc - g, default=1.0)
        if pb_theory < 0:
            pb_theory = 1.0
            warnings.append("理论 PB 为负值, 已修正为 1.0")

        # ---- ROE vs WACC 定性判断 ----
        if roe_mean > wacc + 0.02:
            roe_assessment = "ROE 持续高于 WACC, PB > 1 合理"
        elif abs(roe_mean - wacc) <= 0.02:
            roe_assessment = "ROE ≈ WACC, PB ≈ 1 合理"
        else:
            roe_assessment = "ROE < WACC, PB > 1 存在高估风险"

        # ---- 理论 vs 历史偏离度 ----
        deviation = abs(pb_theory - pb_mean) / max(pb_mean, 0.01)
        if deviation > 0.20:
            consistency = ("存在偏离 ({:.1f}%), 需分析原因 "
                           "(常见: 账面价值低估实际资产)").format(deviation * 100)
            warnings.append(
                "理论 PB 与历史均值偏离 {:.1f}%".format(deviation * 100))
        else:
            consistency = "理论与历史基本一致 (偏离 {:.1f}%)".format(deviation * 100)

        # ---- 估值结果 ----
        val_pess = bvps * min(pb_mean, pb_theory) * 0.85
        val_base = bvps * (pb_mean + pb_theory) / 2.0
        val_opt = bvps * max(pb_mean, pb_theory) * 1.15

        details = {
            "bvps": round(bvps, 2),
            "roe_mean": round(roe_mean, 4),
            "pb_theoretical": round(pb_theory, 2),
            "pb_historical_mean": round(pb_mean, 2),
            "pb_current_percentile": (round(pb_percentile, 4)
                                      if pb_percentile else None),
            "roe_vs_wacc": roe_assessment,
            "theory_vs_history": consistency,
            "deviation": round(deviation, 4),
        }

        return self._result(
            self.MODEL_NAME, True,
            pessimistic=round(val_pess, 2),
            base=round(val_base, 2),
            optimistic=round(val_opt, 2),
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 4.4  EV/EBITDA 模型
# =========================================================================

class EVEBITDAModel(BaseValuationModel):
    MODEL_NAME = "EV_EBITDA"

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []

        ebitda_ttm = safe_get(params, "ebitda_ttm", default=0.0) or 0.0
        if ebitda_ttm <= 0:
            return self._result(self.MODEL_NAME, False,
                                applicability_note="EBITDA_TTM <= 0, EV/EBITDA 模型不适用")

        net_debt = safe_get(params, "real_time_snapshot", "net_debt", default=0.0) or 0.0
        total_shares = safe_get(params, "real_time_snapshot", "total_shares",
                                default=1.0)
        if total_shares is None or total_shares <= 0:
            total_shares = 1.0

        industry_ev_ebitda = safe_get(params, "industry_data", "industry_ev_ebitda",
                                     default=15.0) or 15.0

        # 历史 EV/EBITDA: 若无专门字段, 由当前 EV / EBITDA 近似
        ev_current = safe_get(params, "real_time_snapshot", "ev", default=0.0) or 0.0
        hist_ev_ebitda = safe_div(ev_current, ebitda_ttm, default=industry_ev_ebitda)
        hist_ev_ebitda_mean = safe_get(params, "historical_valuation",
                                       "ev_ebitda_mean", default=hist_ev_ebitda)

        # 并购可比数据 (可能缺失)
        ma_ev_ebitda = safe_get(params, "industry_data", "ma_ev_ebitda", default=None)

        # ---- 公允倍数 (加权平均) ----
        if ma_ev_ebitda and ma_ev_ebitda > 0:
            fair_multiple = (hist_ev_ebitda_mean + industry_ev_ebitda + ma_ev_ebitda) / 3.0
            weight_desc = "历史均值(1/3) + 行业均值(1/3) + 并购可比(1/3)"
        else:
            fair_multiple = (hist_ev_ebitda_mean + industry_ev_ebitda) / 2.0
            weight_desc = "历史均值(1/2) + 行业均值(1/2)"

        # ---- 估值结果 ----
        def _ps(mult, ebitda, nd, shares):
            return safe_div(mult * ebitda - nd, shares)

        val_pess = _ps(fair_multiple * 0.8, ebitda_ttm, net_debt, total_shares)
        val_base = _ps(fair_multiple, ebitda_ttm, net_debt, total_shares)
        val_opt = _ps(fair_multiple * 1.2, ebitda_ttm, net_debt, total_shares)

        details = {
            "ebitda_ttm": round(ebitda_ttm, 2),
            "historical_ev_ebitda": round(hist_ev_ebitda_mean, 2),
            "industry_ev_ebitda": round(industry_ev_ebitda, 2),
            "ma_ev_ebitda": round(ma_ev_ebitda, 2) if ma_ev_ebitda else None,
            "fair_multiple": round(fair_multiple, 2),
            "weighting": weight_desc,
            "net_debt": round(net_debt, 2),
        }

        return self._result(
            self.MODEL_NAME, True,
            pessimistic=round(val_pess, 2),
            base=round(val_base, 2),
            optimistic=round(val_opt, 2),
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 4.5  PS 模型 — 市销率
# =========================================================================

class PSModel(BaseValuationModel):
    MODEL_NAME = "PS"

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []

        rev_pess = safe_get(params, "revenue_forecast", "pessimistic", default=0.0) or 0.0
        rev_base = safe_get(params, "revenue_forecast", "base", default=0.0) or 0.0
        rev_opt = safe_get(params, "revenue_forecast", "optimistic", default=0.0) or 0.0
        rev_cagr = safe_get(params, "revenue_forecast", "cagr", default=0.10) or 0.10

        total_shares = safe_get(params, "real_time_snapshot", "total_shares",
                                default=1.0)
        if total_shares is None or total_shares <= 0:
            total_shares = 1.0

        gross_margin = safe_get(params, "gross_margin_latest", default=0.30) or 0.30
        industry_ps = safe_get(params, "industry_data", "industry_ps",
                               default=5.0) or 5.0

        if rev_base <= 0:
            return self._result(self.MODEL_NAME, False,
                                applicability_note="收入预测 <= 0, PS 模型不适用")

        # ---- PS 上限: 由毛利率决定 ----
        if gross_margin > 0.60:
            ps_cap = 10.0
        elif gross_margin > 0.40:
            ps_cap = 6.0
        elif gross_margin > 0.20:
            ps_cap = 3.0
        else:
            ps_cap = 1.5

        # ---- 增长调整: 收入增速每超出行业 10%, PS 上限 +10% ----
        industry_rev_growth = safe_get(params, "industry_data",
                                       "industry_revenue_growth",
                                       default=0.08) or 0.08
        growth_excess = max(rev_cagr - industry_rev_growth, 0)
        adjustment_steps = growth_excess / 0.10
        ps_cap *= (1.0 + 0.10 * adjustment_steps)

        # 公允 PS
        fair_ps = min((industry_ps + ps_cap) / 2.0, ps_cap)

        # ---- 估值结果 (收入单位: 亿元) ----
        val_pess = industry_ps * 0.7 * rev_pess / total_shares
        val_base = fair_ps * rev_base / total_shares
        val_opt = ps_cap * rev_opt / total_shares

        details = {
            "gross_margin": round(gross_margin, 4),
            "ps_cap_by_margin": round(ps_cap, 2),
            "industry_ps": round(industry_ps, 2),
            "fair_ps": round(fair_ps, 2),
            "revenue_pessimistic": rev_pess,
            "revenue_base": rev_base,
            "revenue_optimistic": rev_opt,
            "growth_adjustment_steps": round(adjustment_steps, 2),
        }

        return self._result(
            self.MODEL_NAME, True,
            pessimistic=round(val_pess, 2),
            base=round(val_base, 2),
            optimistic=round(val_opt, 2),
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 4.6  DDM 模型 — 股息折现
# =========================================================================

class DDMModel(BaseValuationModel):
    MODEL_NAME = "DDM"

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []
        fail_reasons = []

        # ---- 适用性四重检查 (全部满足方可运行) ----
        dps_data = params.get("dps_forecast") or {}
        d1 = safe_get(dps_data, "d1", default=0.0) or 0.0
        g_div = safe_get(dps_data, "growth_rate", default=0.05) or 0.05
        payout_stability = safe_get(dps_data, "payout_stability", default="unknown")
        ocf_coverage = safe_get(dps_data, "ocf_coverage", default=0.0) or 0.0

        # (1) 连续 3 年分红
        if d1 <= 0:
            fail_reasons.append("D1 <= 0, 缺少连续分红记录")
        # (2) 派息率波动 < 15pct
        if payout_stability not in ("stable", "稳定"):
            fail_reasons.append(
                "派息率稳定性不足 (当前: {})".format(payout_stability))
        # (3) OCF / 分红 > 1.2
        if ocf_coverage < 1.2:
            fail_reasons.append(
                "经营现金流覆盖率 ({:.2f}) < 1.2".format(ocf_coverage))
        # (4) 非高成长扩张期
        stage = safe_get(params, "company_stage", "stage", default="")
        if stage in ("high_growth", "expansion", "高速成长", "扩张期"):
            fail_reasons.append("公司处于高成长/扩张期, DDM 不适用")

        if fail_reasons:
            return self._result(
                self.MODEL_NAME, False,
                applicability_note="DDM 不适用: " + "; ".join(fail_reasons),
                warnings=warnings,
            )

        # ---- 计算 Ke (CAPM) ----
        beta = safe_get(params, "beta", "final_beta", default=1.0) or 1.0
        rf = DEFAULT_RF
        erp = DEFAULT_ERP
        ke = rf + beta * erp

        # ---- 基准估值: V = D1 / (Ke - g) ----
        if ke <= g_div:
            g_div = ke * 0.5
            warnings.append(
                "股息增长率被调低至 {:.2f}% 以保证 Ke > g".format(g_div * 100))

        val_base = safe_div(d1, ke - g_div)

        # ---- 悲观: g = hist*0.5, Ke + 0.5% ----
        g_pess = g_div * 0.5
        ke_pess = ke + 0.005
        if ke_pess <= g_pess:
            g_pess = ke_pess * 0.3
        val_pess = safe_div(d1, ke_pess - g_pess)

        # ---- 乐观: g = hist*1.3, Ke - 0.5% ----
        g_opt = g_div * 1.3
        ke_opt = max(ke - 0.005, 0.01)
        if ke_opt <= g_opt:
            g_opt = ke_opt * 0.5
        val_opt = safe_div(d1, ke_opt - g_opt)

        # ---- 股息吸引力评估 ----
        current_yield = safe_get(params, "real_time_snapshot", "dividend_yield",
                                 default=0.0) or 0.0
        # 若 > 1 则视为百分比形式, 转换为小数
        current_yield_dec = current_yield / 100.0 if current_yield > 1 else current_yield
        spread = current_yield_dec - rf
        if spread > 0.02:
            yield_assessment = "吸引力强, 可能存在低估"
        elif spread >= 0:
            yield_assessment = "中性"
        else:
            yield_assessment = "股息率低于无风险利率, 吸引力偏弱"

        details = {
            "d1": round(d1, 2),
            "dividend_growth_rate": round(g_div, 4),
            "ke": round(ke, 4),
            "beta": round(beta, 4),
            "current_yield": round(current_yield_dec, 4),
            "spread_vs_rf": round(spread, 4),
            "yield_assessment": yield_assessment,
            "payout_stability": payout_stability,
            "ocf_coverage": round(ocf_coverage, 2),
        }

        return self._result(
            self.MODEL_NAME, True,
            pessimistic=round(val_pess, 2),
            base=round(val_base, 2),
            optimistic=round(val_opt, 2),
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 4.7  SOTP 模型 — 分部估值
# =========================================================================

class SOTPModel(BaseValuationModel):
    MODEL_NAME = "SOTP"

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []
        segments = safe_get(params, "segments", default=None)

        # ---- 适用性检查 (任一满足即可) ----
        if not segments or not isinstance(segments, list) or len(segments) < 2:
            return self._result(
                self.MODEL_NAME, False,
                applicability_note="缺少分部数据或分部数 < 2, SOTP 不适用",
            )

        # 条件 (1): >= 2 个分部 — 已满足
        # 条件 (2): 任一非核心分部 > 20% 收入
        total_rev = sum(s.get("revenue", 0) for s in segments)
        any_gt_20 = False
        if total_rev > 0:
            any_gt_20 = any(
                safe_div(s.get("revenue", 0), total_rev) > 0.20
                for s in segments
            )

        # 条件 (3): 各分部倍数差异 > 50%
        multiples = [s.get("fair_multiple", 0) for s in segments
                     if s.get("fair_multiple")]
        mult_diff_gt_50 = False
        if len(multiples) >= 2:
            mult_diff_gt_50 = (max(multiples) / max(min(multiples), 0.01) - 1.0) > 0.50

        applicable_reasons = []
        if len(segments) >= 2:
            applicable_reasons.append("拥有 {} 个业务分部".format(len(segments)))
        if any_gt_20:
            applicable_reasons.append("存在非核心分部占比 > 20%")
        if mult_diff_gt_50:
            applicable_reasons.append("各分部合理倍数差异 > 50%")

        if not applicable_reasons:
            return self._result(
                self.MODEL_NAME, False,
                applicability_note="不满足 SOTP 适用条件 (分部数/收入占比/倍数差异)",
            )

        # ---- 逐分部独立估值 ----
        wacc_raw = safe_get(params, "wacc", "wacc", default=10.0)
        wacc = wacc_raw / 100.0 if wacc_raw > 1 else wacc_raw
        total_shares = safe_get(params, "real_time_snapshot", "total_shares",
                                default=1.0)
        if total_shares is None or total_shares <= 0:
            total_shares = 1.0

        segment_values = []
        total_base_value = 0.0
        for seg in segments:
            seg_name = seg.get("name", "未命名")
            rev = seg.get("revenue", 0.0) or 0.0
            ebitda = seg.get("ebitda", 0.0) or 0.0
            fm = seg.get("fair_multiple", 10.0) or 10.0
            metric = seg.get("valuation_metric", "EV/EBITDA")

            if metric in ("EV/EBITDA", "ev_ebitda"):
                seg_val = fm * ebitda
            elif metric in ("PS", "ps"):
                seg_val = fm * rev
            else:
                seg_val = fm * ebitda if ebitda > 0 else fm * rev

            segment_values.append({
                "name": seg_name,
                "revenue": rev,
                "ebitda": ebitda,
                "fair_multiple": fm,
                "metric": metric,
                "value": round(seg_val, 2),
            })
            total_base_value += seg_val

        # ---- 调整项: 总部成本资本化 / 多元化折价 / 其他 ----
        hq_cost = safe_get(params, "sotp_adjustments", "annual_hq_cost",
                           default=0.0) or 0.0
        hq_cap = safe_div(hq_cost, wacc) if wacc > 0 else 0.0

        cong_disc = safe_get(params, "sotp_adjustments", "conglomerate_discount",
                             default=0.15) or 0.15
        cong_disc = _clamp(cong_disc, 0.0, 0.50)

        other_adj = safe_get(params, "sotp_adjustments", "other_adjustments",
                             default=0.0) or 0.0
        net_debt = safe_get(params, "real_time_snapshot", "net_debt",
                            default=0.0) or 0.0

        adjusted = total_base_value - hq_cap - other_adj
        equity_base = adjusted * (1 - cong_disc) - net_debt
        ps_base = safe_div(equity_base, total_shares)

        # ---- 悲观: 每分部 x0.85, 折扣 +5% ----
        sum_pess = sum(sv["value"] * 0.85 for sv in segment_values)
        equity_pess = ((sum_pess - hq_cap - other_adj)
                       * (1 - cong_disc - 0.05) - net_debt)
        ps_pess = safe_div(equity_pess, total_shares)

        # ---- 乐观: 每分部 x1.15, 折扣 -5% ----
        sum_opt = sum(sv["value"] * 1.15 for sv in segment_values)
        disc_opt = max(cong_disc - 0.05, 0.0)
        equity_opt = (sum_opt - hq_cap - other_adj) * (1 - disc_opt) - net_debt
        ps_opt = safe_div(equity_opt, total_shares)

        details = {
            "segments": segment_values,
            "total_segment_value": round(total_base_value, 2),
            "hq_cost_capitalized": round(hq_cap, 2),
            "conglomerate_discount": round(cong_disc, 4),
            "other_adjustments": round(other_adj, 2),
            "equity_value_base": round(equity_base, 2),
            "applicable_reasons": applicable_reasons,
        }

        return self._result(
            self.MODEL_NAME, True,
            pessimistic=round(ps_pess, 2),
            base=round(ps_base, 2),
            optimistic=round(ps_opt, 2),
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 4.8  逆向工程 — 市场隐含预期
# =========================================================================

class ReverseEngineeringModel(BaseValuationModel):
    MODEL_NAME = "REVERSE"

    def calculate(self, params):
        # type: (dict) -> dict
        warnings = []

        price = safe_get(params, "real_time_snapshot", "price", default=0.0) or 0.0
        total_shares = safe_get(params, "real_time_snapshot", "total_shares",
                                default=1.0)
        if total_shares is None or total_shares <= 0:
            total_shares = 1.0
        net_debt = safe_get(params, "real_time_snapshot", "net_debt",
                            default=0.0) or 0.0
        wacc_raw = safe_get(params, "wacc", "wacc", default=10.0)
        wacc = wacc_raw / 100.0 if wacc_raw > 1 else wacc_raw

        base_fcf = safe_get(params, "fcf", "base_fcf", default=0.0) or 0.0
        terminal_g = DEFAULT_TERMINAL_G

        if price <= 0 or base_fcf <= 0:
            return self._result(
                self.MODEL_NAME, False,
                applicability_note="当前价格或基础 FCF <= 0, 无法进行逆向工程",
            )

        # ---- Step 1: 隐含增长率 — 二分法求解 ----
        # 目标: 找到 g_implied 使 DCF 每股价值 = 当前市价
        target_equity = price * total_shares
        target_ev = target_equity + net_debt

        def _dcf_ev(g_rate):
            """给定 FCF 增长率, 计算 5 年 DCF 企业价值"""
            ev = 0.0
            fcf_t = base_fcf
            for t in range(1, DEFAULT_PROJECTION_YEARS + 1):
                fcf_t = base_fcf * ((1 + g_rate) ** t)
                ev += fcf_t / ((1 + wacc) ** t)
            # 终值 (永续增长)
            fcf_n1 = fcf_t * (1 + terminal_g)
            denom = wacc - terminal_g
            if denom <= 0:
                return float('inf')
            tv = fcf_n1 / denom
            ev += tv / ((1 + wacc) ** DEFAULT_PROJECTION_YEARS)
            return ev

        g_lo, g_hi = -0.50, 1.00
        implied_growth = None
        converged = False
        for _ in range(BINARY_SEARCH_MAX_ITER):
            g_mid = (g_lo + g_hi) / 2.0
            ev_mid = _dcf_ev(g_mid)
            rel_err = abs(ev_mid - target_ev) / max(abs(target_ev), 1e-9)
            if rel_err < BINARY_SEARCH_TOL:
                implied_growth = g_mid
                converged = True
                break
            if ev_mid < target_ev:
                g_lo = g_mid
            else:
                g_hi = g_mid

        if not converged:
            implied_growth = (g_lo + g_hi) / 2.0
            warnings.append("隐含增长率二分搜索未完全收敛, 结果为近似值")

        # ---- Step 2: 隐含利润率 ----
        rev_base = safe_get(params, "revenue_forecast", "base", default=0.0) or 0.0
        implied_margin = None
        if rev_base > 0:
            implied_future_rev = rev_base * ((1 + implied_growth) ** DEFAULT_PROJECTION_YEARS)
            implied_future_fcf = base_fcf * ((1 + implied_growth) ** DEFAULT_PROJECTION_YEARS)
            implied_margin = safe_div(implied_future_fcf, implied_future_rev)

        # ---- Step 3: 与历史比较 ----
        hist_fcf_growth = safe_get(params, "fcf", "trend_slope", default=0.0) or 0.0
        hist_gross_margin = safe_get(params, "gross_margin_latest", default=0.0) or 0.0

        if hist_fcf_growth > 0 and implied_growth > hist_fcf_growth * 1.5:
            market_assessment = "市场预期极度乐观, 隐含增速远超历史, 存在高估风险"
            comparison = ("隐含增速 ({:.1f}%) 远高于历史均值 ({:.1f}%)"
                          .format(implied_growth * 100, hist_fcf_growth * 100))
        elif hist_fcf_growth > 0 and implied_growth < hist_fcf_growth * 0.5:
            market_assessment = "市场预期极度悲观, 隐含增速远低于历史, 可能存在低估"
            comparison = ("隐含增速 ({:.1f}%) 远低于历史均值 ({:.1f}%)"
                          .format(implied_growth * 100, hist_fcf_growth * 100))
        else:
            market_assessment = "市场预期中性"
            comparison = ("隐含增速 ({:.1f}%) ≈ 历史均值 ({:.1f}%)"
                          .format(implied_growth * 100, hist_fcf_growth * 100))

        # ---- Step 4: 与定性研究对比 (仅 Mode A) ----
        mode = safe_get(params, "meta", "mode", default="B")
        qualitative_comparison = None
        if mode == "A" and params.get("qualitative_adjustments"):
            qa = params["qualitative_adjustments"]
            qa_growth = safe_get(qa, "expected_growth", default=None)
            if qa_growth is not None:
                diff = implied_growth - qa_growth
                if abs(diff) > 0.05:
                    qualitative_comparison = (
                        "市场隐含增速与定性研究预期存在 {:.1f}% 偏差"
                        .format(diff * 100))
                else:
                    qualitative_comparison = "市场隐含增速与定性研究预期基本一致"

        # ---- Step 5: 关键分歧点 ----
        key_divergences = []
        if implied_growth is not None and hist_fcf_growth > 0:
            ratio = (abs(implied_growth - hist_fcf_growth)
                     / max(abs(hist_fcf_growth), 0.01))
            if ratio > 0.30:
                key_divergences.append(
                    "增速分歧: 隐含 {:.1f}% vs 历史 {:.1f}%".format(
                        implied_growth * 100, hist_fcf_growth * 100))
        if implied_margin is not None and hist_gross_margin > 0:
            ratio_m = (abs(implied_margin - hist_gross_margin)
                       / max(hist_gross_margin, 0.01))
            if ratio_m > 0.20:
                key_divergences.append(
                    "利润率分歧: 隐含 {:.1f}% vs 当前 {:.1f}%".format(
                        implied_margin * 100, hist_gross_margin * 100))

        details = {
            "implied_growth_rate": (round(implied_growth, 4)
                                    if implied_growth is not None else None),
            "implied_margin": (round(implied_margin, 4)
                               if implied_margin is not None else None),
            "market_assessment": market_assessment,
            "comparison_with_history": comparison,
            "qualitative_comparison": qualitative_comparison,
            "key_divergences": key_divergences,
            "current_price": price,
            "target_ev": round(target_ev, 2),
            "wacc_used": round(wacc, 4),
            "terminal_g_used": round(terminal_g, 4),
        }

        # 逆向工程不产生买卖价值, 仅输出分析结论
        return self._result(
            self.MODEL_NAME, True,
            pessimistic=None,
            base=None,
            optimistic=None,
            details=details,
            warnings=warnings,
        )


# =========================================================================
# 模型运行器
# =========================================================================

class ValuationModelRunner:
    """模型运行器: 遍历全部 8 个模型, 统一调度, 收集结果"""

    ALL_MODELS = [
        DCFModel,
        PEModel,
        PBModel,
        EVEBITDAModel,
        PSModel,
        DDMModel,
        SOTPModel,
        ReverseEngineeringModel,
    ]

    def run_all(self, preprocessed):
        # type: (dict) -> dict
        """运行所有可用模型, 返回汇总结果 dict"""
        results = {}
        available_models = preprocessed.get("available_models", {})

        for ModelClass in self.ALL_MODELS:
            model = ModelClass()
            name = model.MODEL_NAME

            # 预处理器标记不可用 → 直接跳过
            model_avail = available_models.get(name, {})
            if not model_avail.get("available", False):
                results[name] = {
                    "applicable": False,
                    "applicability_note": model_avail.get(
                        "note", "模型不可用 (预处理阶段标记)"),
                    "model_name": name,
                    "pessimistic": None,
                    "base": None,
                    "optimistic": None,
                    "details": {},
                    "warnings": [],
                }
                continue

            # 逐模型 try/except, 单个模型异常不影响其余
            try:
                result = model.calculate(preprocessed)
                results[name] = result
            except Exception as e:
                results[name] = {
                    "applicable": False,
                    "applicability_note": "计算错误: {}".format(str(e)),
                    "model_name": name,
                    "pessimistic": None,
                    "base": None,
                    "optimistic": None,
                    "details": {},
                    "warnings": [str(e)],
                }

        return results


# =========================================================================
# 输出组装 & 主入口
# =========================================================================

def build_output(preprocessed, model_results):
    # type: (dict, dict) -> dict
    """组装最终输出 JSON"""
    meta = preprocessed.get("meta", {})
    code = meta.get("code", "unknown")
    name = meta.get("name", "unknown")

    models_run = []
    models_skipped = {}

    for model_name, res in model_results.items():
        if res.get("applicable", False):
            models_run.append(model_name)
        else:
            models_skipped[model_name] = res.get("applicability_note", "")

    return {
        "meta": {
            "code": code,
            "name": name,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "model_results": model_results,
        "summary": {
            "models_run": models_run,
            "models_skipped": models_skipped,
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="估值模型计算引擎 -- 读取预处理 JSON, 输出模型结果 JSON"
    )
    parser.add_argument(
        "--preprocessed-file", required=True,
        help="预处理后的 JSON 文件路径, 如 600519_preprocessed.json",
    )
    parser.add_argument(
        "--output-dir", default="./output",
        help="输出目录 (默认 ./output)",
    )
    args = parser.parse_args()

    # ---- 读取输入 ----
    input_path = args.preprocessed_file
    if not os.path.isfile(input_path):
        print("[ERROR] 找不到文件: {}".format(input_path), file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        preprocessed = json.load(f)

    code = safe_get(preprocessed, "meta", "code", default="unknown")
    name = safe_get(preprocessed, "meta", "name", default="unknown")
    print("[INFO] 开始估值计算: {} ({})".format(name, code))

    # ---- 运行全部模型 ----
    runner = ValuationModelRunner()
    model_results = runner.run_all(preprocessed)

    # ---- 组装并写入输出 ----
    output = build_output(preprocessed, model_results)

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir,
                               "{}_model_results.json".format(code))
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # ---- 控制台摘要 ----
    summary = output["summary"]
    print("[INFO] 完成. 运行模型: {}".format(
        ", ".join(summary["models_run"]) or "无"))
    if summary["models_skipped"]:
        print("[INFO] 跳过模型:")
        for m, reason in summary["models_skipped"].items():
            print("       - {}: {}".format(m, reason))
    print("[INFO] 输出文件: {}".format(output_path))


if __name__ == "__main__":
    main()
