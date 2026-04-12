#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
估值预处理器 (Valuation Preprocessor)

读取 valuation_data.py 输出的原始数据 JSON，执行以下处理：
  1. 数据完整性检查 (DataCompletenessChecker)     -- Section 3.1
  2. 公司阶段判定 (CompanyStageDeterminer)         -- Section 3.2
  3. 参数转换与计算 (ParameterConverter)            -- Section 3.3
  4. Beta 合成 (BetaSynthesizer)                   -- Section 3.4
  5. EPS / 营收预测 (EPSForecaster / RevenueForecaster) -- Section 3.5
  6. WACC 计算
  7. 定性调整读取（可选）

输出: {code}_preprocessed.json

零依赖，仅使用 Python 3.6+ 标准库。

用法:
  python valuation_preprocessor.py \
      --data-file 600519_valuation_data.json \
      --output-dir ./output \
      [--qualitative-doc /path/to/analysis.md]
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime


# ============================================================================
# 工具函数
# ============================================================================

def safe_div(numerator, denominator, default=0.0):
    """安全除法，防止除零错误。"""
    if denominator is None or denominator == 0:
        return default
    if numerator is None:
        return default
    return numerator / denominator


def safe_float(value, default=0.0):
    """安全类型转换为 float。"""
    if value is None:
        return default
    try:
        v = float(value)
        # 防御 NaN
        if v != v:
            return default
        return v
    except (ValueError, TypeError):
        return default


def extract_series_values(series, key="value"):
    """从 year/value 序列中提取 value 列表，按年份升序排列。"""
    if not series:
        return []
    sorted_series = sorted(series, key=lambda x: x.get("year", 0))
    return [safe_float(item.get(key)) for item in sorted_series if item.get(key) is not None]


def extract_series_years(series):
    """从 year/value 序列中提取 year 列表，按年份升序排列。"""
    if not series:
        return []
    sorted_series = sorted(series, key=lambda x: x.get("year", 0))
    return [int(item.get("year", 0)) for item in sorted_series]


def linear_regression(xs, ys):
    """
    纯 Python 线性回归: y = a + b*x
    返回 (intercept, slope)
    slope = sum((xi-x_mean)(yi-y_mean)) / sum((xi-x_mean)^2)
    """
    n = len(xs)
    if n < 2 or len(ys) < 2:
        return 0.0, 0.0
    n = min(n, len(ys))
    xs = xs[:n]
    ys = ys[:n]

    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    ss_xy = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    ss_xx = sum((xs[i] - x_mean) ** 2 for i in range(n))

    if ss_xx == 0:
        return y_mean, 0.0

    slope = ss_xy / ss_xx
    intercept = y_mean - slope * x_mean
    return intercept, slope


def linear_regression_slope(values):
    """纯 Python 线性回归斜率。x = 0,1,2,..."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    ys = list(values)
    _, slope = linear_regression([float(x) for x in xs], ys)
    return slope


def calc_cagr(start_value, end_value, years):
    """
    计算 CAGR = (end/start)^(1/years) - 1
    处理负值情况：若 start 或 end 为负或零，返回 None
    """
    if years is None or years <= 0:
        return None
    if start_value is None or end_value is None:
        return None
    if start_value <= 0 or end_value <= 0:
        return None
    try:
        ratio = end_value / start_value
        return ratio ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def calc_volatility(values):
    """
    计算年度变化的波动率 = std_dev(年度变化率)
    至少需要 2 个数据点。
    """
    if len(values) < 2:
        return 0.0

    mean_val = sum(values) / len(values)
    if abs(mean_val) < 1e-9:
        return float("inf")

    # 计算年度变化率
    changes = []
    for i in range(1, len(values)):
        if abs(values[i - 1]) > 1e-9:
            changes.append((values[i] - values[i - 1]) / abs(values[i - 1]))

    if not changes:
        return 0.0

    # 标准差
    ch_mean = sum(changes) / len(changes)
    variance = sum((c - ch_mean) ** 2 for c in changes) / len(changes)
    std_dev = math.sqrt(variance)
    return std_dev


def calc_std(values):
    """计算标准差（总体标准差）。"""
    if len(values) < 2:
        return 0.0
    mean_val = sum(values) / len(values)
    variance = sum((v - mean_val) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def get_latest_value(series, key="value"):
    """获取最新一年的值（按年份最大）。"""
    if not series:
        return None
    valid = [item for item in series if item.get(key) is not None]
    if not valid:
        return None
    latest = max(valid, key=lambda x: x.get("year", 0))
    val = latest.get(key)
    return safe_float(val) if val is not None else None


def get_values(series):
    """从年度序列中提取非空值列表（按年份排序）。"""
    if not series:
        return []
    sorted_s = sorted(series, key=lambda x: x.get("year", 0))
    return [s["value"] for s in sorted_s if s.get("value") is not None]


# ============================================================================
# 3.1 DataCompletenessChecker - 数据完整性检查
# ============================================================================

class DataCompletenessChecker:
    """
    检查各估值模型的数据要求是否满足。

    DCF   -> 历史现金流 >=3 年 + WACC 输入
    PE    -> 历史利润数据（盈利）
    PB    -> BVPS + ROE 数据
    EV_EBITDA -> EBITDA 数据
    PS    -> 营收 + 毛利率数据
    DDM   -> 股息历史 >=3 年 + WACC
    SOTP  -> 分部数据
    REVERSE -> real_time + WACC + FCF 历史
    """

    def __init__(self, data):
        self.data = data

    def check(self):
        """返回各模型的可用性字典。"""
        return {
            "DCF": self._check_dcf(),
            "PE": self._check_pe(),
            "PB": self._check_pb(),
            "EV_EBITDA": self._check_ev_ebitda(),
            "PS": self._check_ps(),
            "DDM": self._check_ddm(),
            "SOTP": self._check_sotp(),
            "REVERSE": self._check_reverse(),
        }

    def _check_dcf(self):
        """DCF -> 历史现金流 >=3 年 + WACC 输入完整"""
        cf_data = self.data.get("cashflow_data", {})
        cf_series = cf_data.get("annual_series", {})
        ocf = cf_series.get("operating_cashflow", [])
        wacc = self.data.get("wacc_inputs", {})

        cf_years = len(get_values(ocf))
        has_wacc = (
            wacc.get("status") == "success"
            and wacc.get("risk_free_rate") is not None
            and wacc.get("market_risk_premium") is not None
        )

        if cf_years >= 3 and has_wacc:
            return {"available": True, "note": "现金流数据%d年，WACC输入完整" % cf_years}
        notes = []
        if cf_years < 3:
            notes.append("现金流数据仅%d年（需>=3年）" % cf_years)
        if not has_wacc:
            notes.append("WACC输入不完整")
        return {"available": False, "note": "；".join(notes)}

    def _check_pe(self):
        """PE -> 历史利润数据 + 已盈利"""
        inc = self.data.get("income_data", {})
        series = inc.get("annual_series", {})
        net_profit = series.get("net_profit", [])
        profits = get_values(net_profit)

        if not profits:
            return {"available": False, "note": "缺少净利润数据"}

        # 检查是否盈利（历史中有盈利记录）
        if any(p > 0 for p in profits):
            return {"available": True, "note": "存在盈利年度，%d年数据" % len(profits)}
        return {"available": False, "note": "历史数据中无盈利年度，PE模型不适用"}

    def _check_pb(self):
        """PB -> BVPS + ROE 数据"""
        bal = self.data.get("balance_data", {})
        latest = bal.get("latest", {})
        bvps = latest.get("bvps")
        roe_series = bal.get("annual_series", {}).get("roe", [])

        if bvps is not None and safe_float(bvps) > 0 and len(get_values(roe_series)) >= 1:
            return {"available": True, "note": "BVPS=%s, ROE数据%d年" % (bvps, len(get_values(roe_series)))}
        notes = []
        if bvps is None or safe_float(bvps) <= 0:
            notes.append("BVPS不可用或为负")
        if len(get_values(roe_series)) < 1:
            notes.append("缺少ROE数据")
        return {"available": False, "note": "；".join(notes)}

    def _check_ev_ebitda(self):
        """EV/EBITDA -> EBITDA 数据（正值）"""
        inc = self.data.get("income_data", {})
        ebitda = inc.get("annual_series", {}).get("ebitda", [])
        ebitda_vals = get_values(ebitda)

        if ebitda_vals and any(v > 0 for v in ebitda_vals):
            return {"available": True, "note": "EBITDA数据%d年" % len(ebitda_vals)}
        return {"available": False, "note": "缺少有效EBITDA数据"}

    def _check_ps(self):
        """PS -> 营收 + 毛利率数据"""
        inc = self.data.get("income_data", {})
        revenue = inc.get("annual_series", {}).get("revenue", [])
        gm = inc.get("annual_series", {}).get("gross_margin", [])
        rev_vals = get_values(revenue)
        gm_vals = get_values(gm)

        if rev_vals and gm_vals:
            return {"available": True, "note": "营收数据%d年，毛利率数据%d年" % (len(rev_vals), len(gm_vals))}
        notes = []
        if not rev_vals:
            notes.append("缺少营收数据")
        if not gm_vals:
            notes.append("缺少毛利率数据")
        return {"available": False, "note": "；".join(notes)}

    def _check_ddm(self):
        """DDM -> 股息历史 >=3 年 + WACC 输入"""
        div = self.data.get("dividend_history", {})
        records = div.get("records", [])
        wacc = self.data.get("wacc_inputs", {})

        div_years = len(records)
        has_wacc = wacc.get("status") == "success"

        if div_years >= 3 and has_wacc:
            return {"available": True, "note": "股息数据%d年，WACC输入完整" % div_years}
        notes = []
        if div_years < 3:
            notes.append("股息数据仅%d年（需>=3年）" % div_years)
        if not has_wacc:
            notes.append("WACC输入不完整")
        return {"available": False, "note": "；".join(notes)}

    def _check_sotp(self):
        """SOTP -> 分部数据"""
        segment_data = self.data.get("segment_data")
        if segment_data and isinstance(segment_data, dict) and segment_data.get("segments"):
            return {"available": True, "note": "分部数据可用"}
        return {"available": False, "note": "缺少分部数据"}

    def _check_reverse(self):
        """REVERSE -> real_time + WACC + FCF 历史"""
        rt = self.data.get("real_time", {})
        wacc = self.data.get("wacc_inputs", {})
        cf = self.data.get("cashflow_data", {})
        ocf = cf.get("annual_series", {}).get("operating_cashflow", [])

        has_rt = rt.get("price") is not None
        has_wacc = wacc.get("status") == "success"
        has_fcf = len(get_values(ocf)) >= 1

        if has_rt and has_wacc and has_fcf:
            return {"available": True, "note": "实时数据/WACC/现金流均可用"}
        notes = []
        if not has_rt:
            notes.append("缺少实时行情数据")
        if not has_wacc:
            notes.append("WACC输入不完整")
        if not has_fcf:
            notes.append("缺少现金流历史")
        return {"available": False, "note": "；".join(notes)}


# ============================================================================
# 3.2 CompanyStageDeterminer - 公司阶段判定（优先级匹配，命中即停）
# ============================================================================

# 阶段中文名称映射
STAGE_CN_MAP = {
    "unprofitable": "未盈利公司",
    "distressed": "困境反转型",
    "growth": "成长期公司",
    "high_dividend": "高分红公司",
    "diversified": "多元化集团",
    "asset_heavy": "重资产行业",
    "mature": "成熟期公司",
}

# 重资产行业关键词列表
ASSET_HEAVY_KEYWORDS = [
    "银行", "保险", "能源", "电力", "公用事业", "公用",
    "制造", "钢铁", "水泥", "石化", "煤炭", "石油",
    "化工", "建筑",
    "banking", "insurance", "energy", "utilities", "manufacturing",
]


class CompanyStageDeterminer:
    """公司阶段判定器 - 7种类型优先级匹配，命中第一个即停止。"""

    def __init__(self, data):
        self.data = data
        self.warnings = []

    def determine(self):
        """按优先级顺序判定公司阶段。"""
        checkers = [
            self._check_a_unprofitable,
            self._check_b_distressed,
            self._check_c_growth,
            self._check_d_high_dividend,
            self._check_e_diversified,
            self._check_f_asset_heavy,
        ]

        for checker in checkers:
            result = checker()
            if result is not None:
                return result

        # 条件 G: 均不满足 -> mature（默认）
        return {
            "stage": "mature",
            "stage_cn": STAGE_CN_MAP["mature"],
            "reason": "不满足其他阶段条件，默认为成熟期",
            "extra_handling": None,
        }

    def _get_net_profit_values(self):
        """获取净利润值列表（按年份升序）。"""
        inc = self.data.get("income_data", {})
        np_series = inc.get("annual_series", {}).get("net_profit", [])
        return get_values(np_series)

    def _get_revenue_values(self):
        """获取营收值列表（按年份升序）。"""
        inc = self.data.get("income_data", {})
        rev_series = inc.get("annual_series", {}).get("revenue", [])
        return get_values(rev_series)

    def _check_a_unprofitable(self):
        """条件 A: 净利润连续 3+ 年为负 -> unprofitable
        Extra: 禁用PE/DDM，PS权重>=50%
        """
        np_vals = self._get_net_profit_values()
        if len(np_vals) < 3:
            return None

        # 从最近一年往回数，计算连续亏损年数
        consecutive_losses = 0
        for v in reversed(np_vals):
            if v < 0:
                consecutive_losses += 1
            else:
                break

        if consecutive_losses >= 3:
            return {
                "stage": "unprofitable",
                "stage_cn": STAGE_CN_MAP["unprofitable"],
                "reason": "连续%d年净利润为负" % consecutive_losses,
                "extra_handling": {
                    "disable_models": ["PE", "DDM"],
                    "ps_weight_min": 0.5,
                    "note": "停用PE/DDM，PS权重>=50%",
                },
            }
        return None

    def _check_b_distressed(self):
        """条件 B: 负净资产 或 连续亏损后近期改善 -> distressed
        Extra: 启用清算价值作为估值下限
        """
        bal = self.data.get("balance_data", {})
        latest = bal.get("latest", {})

        # 检查负净资产 -- 尝试 bvps 和 total_equity 两个字段
        bvps = latest.get("bvps")
        total_equity = latest.get("total_equity")
        if bvps is not None and safe_float(bvps) < 0:
            return {
                "stage": "distressed",
                "stage_cn": STAGE_CN_MAP["distressed"],
                "reason": "净资产为负（BVPS=%s）" % bvps,
                "extra_handling": {
                    "enable_liquidation_floor": True,
                    "note": "启用清算价值作为估值下限",
                },
            }
        if total_equity is not None and safe_float(total_equity) < 0:
            return {
                "stage": "distressed",
                "stage_cn": STAGE_CN_MAP["distressed"],
                "reason": "净资产为负（total_equity=%s）" % total_equity,
                "extra_handling": {
                    "enable_liquidation_floor": True,
                    "note": "启用清算价值作为估值下限",
                },
            }

        # 连续亏损后近期改善
        np_vals = self._get_net_profit_values()
        if len(np_vals) >= 3:
            recent = np_vals[-1]
            # 从倒数第二项往回检查连续亏损
            consecutive_prior_losses = 0
            for v in reversed(np_vals[:-1]):
                if v < 0:
                    consecutive_prior_losses += 1
                else:
                    break
            # 至少 2 年连续亏损且最新年度改善（转正或减亏）
            if consecutive_prior_losses >= 2 and (
                recent > 0 or (len(np_vals) >= 2 and recent > np_vals[-2])
            ):
                return {
                    "stage": "distressed",
                    "stage_cn": STAGE_CN_MAP["distressed"],
                    "reason": "连续亏损后最近期出现改善（此前连续%d年亏损）" % consecutive_prior_losses,
                    "extra_handling": {
                        "enable_liquidation_floor": True,
                        "note": "启用清算价值作为估值下限",
                    },
                }
        return None

    def _check_c_growth(self):
        """条件 C: 3年营收CAGR > 25% 且 (最新净利率 < 10% 或 利润波动 > 50%) -> growth"""
        rev_vals = self._get_revenue_values()
        np_vals = self._get_net_profit_values()

        # 需要至少计算 3 年 CAGR（即至少 4 个数据点，或使用最近 3 年跨度）
        if len(rev_vals) < 3:
            return None

        # 用所有可用数据计算最近 3 年跨度的 CAGR
        # 如果有 4+ 个点，取 [-4] 和 [-1] 之间的 3 年 CAGR
        # 如果恰好 3 个点，取 [-3] 和 [-1] 之间的 2 年 CAGR（近似）
        if len(rev_vals) >= 4:
            rev_cagr = calc_cagr(rev_vals[-4], rev_vals[-1], 3)
        else:
            rev_cagr = calc_cagr(rev_vals[0], rev_vals[-1], len(rev_vals) - 1)

        if rev_cagr is None or rev_cagr <= 0.25:
            return None

        # 检查净利率
        latest_rev = rev_vals[-1]
        latest_np = np_vals[-1] if np_vals else 0
        net_margin_low = False
        net_margin = 0.0
        if latest_rev and latest_rev > 0:
            net_margin = latest_np / latest_rev
            if net_margin < 0.10:
                net_margin_low = True

        # 检查利润波动率
        volatility_high = False
        np_volatility = 0.0
        if len(np_vals) >= 3:
            np_volatility = calc_volatility(np_vals)
            if np_volatility > 0.50:
                volatility_high = True

        if net_margin_low or volatility_high:
            reason_parts = ["3年营收CAGR=%.0f%%(>25%%)" % (rev_cagr * 100)]
            if net_margin_low:
                reason_parts.append("净利率%.1f%%(<10%%)" % (net_margin * 100))
            if volatility_high:
                reason_parts.append("利润波动率%.0f%%(>50%%)" % (np_volatility * 100))
            return {
                "stage": "growth",
                "stage_cn": STAGE_CN_MAP["growth"],
                "reason": "，".join(reason_parts),
                "extra_handling": None,
            }
        return None

    def _check_d_high_dividend(self):
        """条件 D: 连续3年分红 且 平均派息率>30% 且 营收CAGR<10% -> high_dividend"""
        div = self.data.get("dividend_history", {})
        records = div.get("records", [])

        if len(records) < 3:
            return None

        # 按年份降序排列，取最近 3 年
        sorted_records = sorted(records, key=lambda x: x.get("year", 0), reverse=True)
        recent_3 = sorted_records[:3]

        # 检查连续性（年份差为1）
        years = [r.get("year", 0) for r in recent_3]
        consecutive = all(years[i] - years[i + 1] == 1 for i in range(len(years) - 1))

        if not consecutive:
            return None

        # 平均派息率
        payout_ratios = [safe_float(r.get("payout_ratio"), default=0.0) for r in recent_3]
        avg_payout = sum(payout_ratios) / len(payout_ratios) if payout_ratios else 0

        if avg_payout <= 30.0:
            return None

        # 营收 CAGR < 10%
        rev_vals = self._get_revenue_values()
        if len(rev_vals) >= 4:
            rev_cagr = calc_cagr(rev_vals[-4], rev_vals[-1], 3)
            if rev_cagr is not None and rev_cagr >= 0.10:
                return None  # 增速太快，不算高分红
        elif len(rev_vals) >= 3:
            rev_cagr = calc_cagr(rev_vals[0], rev_vals[-1], len(rev_vals) - 1)
            if rev_cagr is not None and rev_cagr >= 0.10:
                return None

        return {
            "stage": "high_dividend",
            "stage_cn": STAGE_CN_MAP["high_dividend"],
            "reason": "近3年均有分红，平均派息率%.0f%%(>30%%)，收入增速较低" % avg_payout,
            "extra_handling": None,
        }

    def _check_e_diversified(self):
        """条件 E: 2+个独立业务分部（任何非核心 >20% 营收） -> diversified"""
        segment_data = self.data.get("segment_data")
        if not segment_data or not isinstance(segment_data, dict):
            return None

        segments = segment_data.get("segments", [])
        if len(segments) < 2:
            return None

        total_rev = sum(safe_float(s.get("revenue", 0)) for s in segments)
        if total_rev <= 0:
            return None

        # 按收入占比降序
        revenue_shares = []
        for s in segments:
            share = safe_div(safe_float(s.get("revenue", 0)), total_rev)
            revenue_shares.append((s.get("name", ""), share))
        revenue_shares.sort(key=lambda x: x[1], reverse=True)

        # 核心业务是占比最大的，其他 > 20% 的视为显著非核心
        non_core_significant = [
            (name, share)
            for name, share in revenue_shares[1:]
            if share > 0.20
        ]

        if non_core_significant:
            names = ", ".join(
                "%s(%.0f%%)" % (name, share * 100)
                for name, share in non_core_significant
            )
            return {
                "stage": "diversified",
                "stage_cn": STAGE_CN_MAP["diversified"],
                "reason": "存在%d个业务分部，非核心业务占比>20%%: %s" % (len(segments), names),
                "extra_handling": None,
            }
        return None

    def _check_f_asset_heavy(self):
        """条件 F: 行业属于银行/保险/能源/公用/制造等 且 固定资产/总资产>40% -> asset_heavy"""
        meta = self.data.get("meta", {})
        industry = meta.get("industry", "")

        is_heavy_industry = any(
            kw.lower() in industry.lower()
            for kw in ASSET_HEAVY_KEYWORDS
        )

        if not is_heavy_industry:
            return None

        bal = self.data.get("balance_data", {})
        latest = bal.get("latest", {})
        fixed_assets = latest.get("fixed_assets")
        total_assets = latest.get("total_assets")

        if fixed_assets is not None and total_assets is not None and safe_float(total_assets) > 0:
            ratio = safe_div(safe_float(fixed_assets), safe_float(total_assets))
            if ratio > 0.40:
                return {
                    "stage": "asset_heavy",
                    "stage_cn": STAGE_CN_MAP["asset_heavy"],
                    "reason": "行业为%s，固定资产占总资产%.0f%%(>40%%)" % (industry, ratio * 100),
                    "extra_handling": None,
                }
            return None  # 行业匹配但资产比率不够
        else:
            # 无固定资产数据时，仅靠行业判定
            return {
                "stage": "asset_heavy",
                "stage_cn": STAGE_CN_MAP["asset_heavy"],
                "reason": "行业为%s（属重资产行业），固定资产数据不可用" % industry,
                "extra_handling": None,
            }


# ============================================================================
# 3.3 ParameterConverter - 参数转换
# ============================================================================

class ParameterConverter:
    """
    参数转换器：
    - FCF 计算: FCF = NOPAT + depreciation - capex - working_capital_increase
      where NOPAT = EBIT * (1 - effective_tax_rate)
    - 税率标准化: 3年均值，排除偏离>5pct的异常值
    - EBITDA(TTM)
    - BVPS / ROE / ROA / 毛利率
    """

    def __init__(self, data):
        self.data = data
        self.warnings = []

    def compute_fcf_series(self):
        """
        计算 FCF 历史序列。
        FCF = NOPAT + depreciation - capex - working_capital_change
        返回: (fcf_series_list, base_fcf, trend_label, trend_slope)
        """
        inc = self.data.get("income_data", {}).get("annual_series", {})
        cf = self.data.get("cashflow_data", {}).get("annual_series", {})

        ebit_s = inc.get("ebit", [])
        tax_s = inc.get("effective_tax_rate", [])
        dep_s = cf.get("depreciation", [])
        capex_s = cf.get("capex", [])
        wc_s = cf.get("working_capital_change", [])

        # 按年份构建映射
        by_year = {}
        for s_list, key in [(ebit_s, "ebit"), (tax_s, "tax"), (dep_s, "dep"),
                            (capex_s, "capex"), (wc_s, "wc")]:
            for item in (s_list or []):
                y = item.get("year")
                if y is not None:
                    if y not in by_year:
                        by_year[y] = {}
                    by_year[y][key] = item.get("value")

        # 标准化税率（用于缺失年份填充）
        norm_tax = self.normalize_tax_rate()

        fcf_list = []
        for year in sorted(by_year.keys()):
            vals = by_year[year]
            ebit = vals.get("ebit")
            tax_rate = vals.get("tax")
            dep = safe_float(vals.get("dep"), default=0.0)
            capex = safe_float(vals.get("capex"), default=0.0)
            wc = safe_float(vals.get("wc"), default=0.0)

            if ebit is None:
                # 尝试备选: 使用经营现金流 - 资本支出
                continue

            ebit = safe_float(ebit)
            # 税率处理
            if tax_rate is not None:
                t = safe_float(tax_rate)
                if t > 1:
                    t = t / 100.0
            else:
                t = norm_tax

            nopat = ebit * (1 - t)
            capex_abs = abs(capex)  # capex 通常为负值
            fcf = nopat + dep - capex_abs - wc
            fcf_list.append({"year": year, "value": round(fcf, 2)})

        # 如果标准方法无数据，尝试备选方案
        if not fcf_list:
            ocf_s = cf.get("operating_cashflow", [])
            if ocf_s and capex_s:
                ocf_map = {item["year"]: safe_float(item.get("value")) for item in ocf_s if item.get("year")}
                capex_map = {item["year"]: safe_float(item.get("value")) for item in capex_s if item.get("year")}
                common_years = sorted(set(ocf_map.keys()) & set(capex_map.keys()))
                for year in common_years:
                    ocf = ocf_map[year]
                    capex = abs(capex_map[year])
                    fcf = ocf - capex
                    fcf_list.append({"year": year, "value": round(fcf, 2)})
                if fcf_list:
                    self.warnings.append("FCF使用经营现金流-资本支出估算（缺少EBIT分项数据）")

        if not fcf_list:
            self.warnings.append("无法计算FCF：缺少必要数据")
            return [], 0.0, "无数据", 0.0

        base_fcf, trend_label, trend_slope = self._calc_base_fcf(fcf_list)
        return fcf_list, base_fcf, trend_label, trend_slope

    def _calc_base_fcf(self, fcf_list):
        """
        从历史 FCF 推导基准值（线性回归趋势判断）:
        - 趋势上升 -> base_fcf = 3年均值 * (1 + slope*50%)
        - 趋势平稳 -> base_fcf = 5年均值
        - 趋势下降 -> base_fcf = 3年均值
        """
        if not fcf_list:
            return 0.0, "无数据", 0.0

        sorted_fcf = sorted(fcf_list, key=lambda x: x["year"])
        values = [item["value"] for item in sorted_fcf]

        if len(values) < 2:
            return values[0] if values else 0.0, "数据不足", 0.0

        # 线性回归判断趋势
        slope = linear_regression_slope(values)
        mean_val = sum(values) / len(values)
        if abs(mean_val) > 1e-9:
            norm_slope = slope / abs(mean_val)
        else:
            norm_slope = 0.0

        # 取最近 3 年和 5 年均值
        recent_3 = values[-3:] if len(values) >= 3 else values
        recent_5 = values[-5:] if len(values) >= 5 else values
        avg_3 = sum(recent_3) / len(recent_3)
        avg_5 = sum(recent_5) / len(recent_5)

        if norm_slope > 0.05:  # 上升趋势
            trend_label = "上升"
            base_fcf = avg_3 * (1 + abs(norm_slope) * 0.5)
        elif norm_slope < -0.05:  # 下降趋势
            trend_label = "下降"
            base_fcf = avg_3
        else:  # 平稳
            trend_label = "平稳"
            base_fcf = avg_5

        return round(base_fcf, 2), trend_label, round(norm_slope, 4)

    def normalize_tax_rate(self):
        """
        税率标准化: 近3年均值，排除偏离均值 >5 个百分点 的异常值。
        返回小数形式的税率（如 0.25）。
        """
        inc = self.data.get("income_data", {}).get("annual_series", {})
        tax_series = inc.get("effective_tax_rate", [])
        values = get_values(tax_series)

        if not values:
            return 0.25  # 默认企业所得税率

        # 确保是百分比形式（统一处理）
        # 如果所有值 > 1，认为是百分比；否则认为是小数
        if all(v > 1 for v in values if v > 0):
            values = [v / 100.0 for v in values]

        # 取最近 3 年
        recent = values[-3:] if len(values) >= 3 else values

        if len(recent) <= 1:
            return recent[0] if recent else 0.25

        mean_tax = sum(recent) / len(recent)
        # 排除偏离均值 >5 个百分点的异常值
        filtered = [v for v in recent if abs(v - mean_tax) <= 0.05]

        if not filtered:
            return round(mean_tax, 4)

        return round(sum(filtered) / len(filtered), 4)

    def get_ebitda_ttm(self):
        """获取 EBITDA(TTM) -- 使用最新年度值。"""
        inc = self.data.get("income_data", {}).get("annual_series", {})
        ebitda_vals = get_values(inc.get("ebitda", []))
        return ebitda_vals[-1] if ebitda_vals else None

    def get_bvps(self):
        """获取最新 BVPS。"""
        bal = self.data.get("balance_data", {})
        bvps = bal.get("latest", {}).get("bvps")
        return safe_float(bvps) if bvps is not None else None

    def get_roe_mean(self):
        """计算 ROE 均值（返回小数）。"""
        bal = self.data.get("balance_data", {}).get("annual_series", {})
        values = get_values(bal.get("roe", []))
        if not values:
            return None
        # 如果值看起来是百分比则转换
        if all(abs(v) > 1 for v in values if v != 0):
            values = [v / 100.0 for v in values]
        return round(sum(values) / len(values), 4)

    def get_roa_mean(self):
        """计算 ROA 均值（返回小数）。"""
        bal = self.data.get("balance_data", {}).get("annual_series", {})
        values = get_values(bal.get("roa", []))
        if not values:
            return None
        if all(abs(v) > 1 for v in values if v != 0):
            values = [v / 100.0 for v in values]
        return round(sum(values) / len(values), 4)

    def get_gross_margin_latest(self):
        """获取最新毛利率（返回小数）。"""
        inc = self.data.get("income_data", {}).get("annual_series", {})
        gm_vals = get_values(inc.get("gross_margin", []))
        if not gm_vals:
            return None
        val = gm_vals[-1]
        if val > 1:
            val = val / 100.0
        return round(val, 4)


# ============================================================================
# 3.4 BetaSynthesizer - Beta 合成（4步流程）
# ============================================================================

class BetaSynthesizer:
    """
    Beta 合成器 - 4 步流程:
    1. 根据上市年限列出可用 Beta
    2. 一致性检查（spread）
    3. 加权混合
    4. 基准标记（A股用 CSI300）
    """

    def __init__(self, data):
        self.data = data
        self.warnings = []

    def synthesize(self):
        """执行 4 步 Beta 合成。"""
        wacc_inputs = self.data.get("wacc_inputs", {})
        beta_values = wacc_inputs.get("beta_values", {})

        b1y = self._to_float(beta_values.get("beta_1y"))
        b3y = self._to_float(beta_values.get("beta_3y"))
        b5y = self._to_float(beta_values.get("beta_5y"))
        ind = self._to_float(beta_values.get("industry_beta"))

        # Step 1: 估算上市年限 & 确定可用 Beta
        listing_years = self._estimate_listing_years()
        available = self._get_available_betas(b1y, b3y, b5y, ind, listing_years)

        if not available:
            self.warnings.append("无可用Beta数据，使用默认值1.0")
            return {
                "final_beta": 1.0,
                "method": "默认值",
                "stability": "unknown",
                "listing_years": listing_years,
                "warning": "无可用Beta数据",
            }

        # Step 2: 一致性检查
        beta_vals = list(available.values())
        stability = self._check_consistency(beta_vals)

        # Step 3: 加权混合
        final_beta, method = self._weighted_blend(
            b1y, b3y, b5y, ind, listing_years, stability
        )

        # Step 4: 基准标记
        meta = self.data.get("meta", {})
        market = meta.get("market", "")
        benchmark = "CSI300" if market in ("SH", "SZ", "BJ") else "unknown"

        warning = None
        if final_beta < 0.3:
            warning = "Beta=%.3f异常偏低，请核实" % final_beta
            self.warnings.append(warning)
        elif final_beta > 2.0:
            warning = "Beta=%.3f异常偏高，请核实" % final_beta
            self.warnings.append(warning)

        if stability == "highly_unstable":
            w = "Beta不稳定（差值>0.6），WACC计算存在较大不确定性"
            self.warnings.append(w)
            if warning:
                warning = warning + "；" + w
            else:
                warning = w

        return {
            "final_beta": round(final_beta, 3),
            "method": method,
            "stability": stability,
            "listing_years": listing_years,
            "benchmark": benchmark,
            "warning": warning,
        }

    def _to_float(self, val):
        """安全转换，None 保持 None。"""
        if val is None:
            return None
        try:
            v = float(val)
            return v if v == v else None
        except (ValueError, TypeError):
            return None

    def _estimate_listing_years(self):
        """估算上市年限 -- 基于可用数据长度。"""
        max_years = 0
        inc = self.data.get("income_data", {}).get("annual_series", {})
        for key in ["revenue", "net_profit", "eps"]:
            series = inc.get(key, [])
            if series:
                years = extract_series_years(series)
                if years:
                    span = max(years) - min(years) + 1
                    max_years = max(max_years, span)

        wacc_inputs = self.data.get("wacc_inputs", {})
        beta_values = wacc_inputs.get("beta_values", {})
        if beta_values.get("beta_5y") is not None:
            max_years = max(max_years, 5)
        elif beta_values.get("beta_3y") is not None:
            max_years = max(max_years, 3)
        elif beta_values.get("beta_1y") is not None:
            max_years = max(max_years, 1)

        return max_years if max_years > 0 else 1

    def _get_available_betas(self, b1y, b3y, b5y, ind, listing_years):
        """Step 1: 根据上市年限列出可用 Beta。"""
        available = {}
        if listing_years >= 5:
            if b5y is not None:
                available["5y"] = b5y
            if b3y is not None:
                available["3y"] = b3y
            if b1y is not None:
                available["1y"] = b1y
        elif 3 <= listing_years < 5:
            if b3y is not None:
                available["3y"] = b3y
            if b1y is not None:
                available["1y"] = b1y
            if ind is not None:
                available["ind"] = ind
        elif 1 <= listing_years < 3:
            if b1y is not None:
                available["1y"] = b1y
            if ind is not None:
                available["ind"] = ind
        else:
            # <1yr: industry_beta * 1.2 only
            if ind is not None:
                available["ind"] = ind * 1.2
        return available

    def _check_consistency(self, beta_vals):
        """Step 2: 一致性检查。"""
        if len(beta_vals) < 2:
            return "stable"
        spread = max(beta_vals) - min(beta_vals)
        if spread <= 0.3:
            return "stable"
        elif spread <= 0.6:
            return "unstable"
        else:
            return "highly_unstable"

    def _weighted_blend(self, b1y, b3y, b5y, ind, listing_years, stability):
        """Step 3: 加权混合。"""
        major_change = stability in ("unstable", "highly_unstable")

        if listing_years >= 5 and b5y is not None and b3y is not None and b1y is not None:
            if not major_change:
                # >=5yr, no major change: 1Y*20% + 3Y*30% + 5Y*50%
                beta = b1y * 0.20 + b3y * 0.30 + b5y * 0.50
                method = "5Y*50%+3Y*30%+1Y*20%"
            else:
                # >=5yr, major change: 1Y*50% + 3Y*35% + 5Y*15%
                beta = b1y * 0.50 + b3y * 0.35 + b5y * 0.15
                method = "1Y*50%+3Y*35%+5Y*15%(近期重大变化)"
            # 如果 unstable，额外增加行业权重
            if stability == "unstable" and ind is not None:
                beta = beta * 0.7 + ind * 0.3
                method += "(不稳定，增加行业权重)"
        elif listing_years >= 5 and b5y is not None:
            beta = b5y
            method = "5Y（其他周期Beta不可用）"
        elif 3 <= listing_years < 5 or (listing_years >= 5 and b5y is None):
            # 3-5yr: 3Y*50% + 1Y*20% + industry*30%
            weights = {}
            if b3y is not None:
                weights["3Y"] = (b3y, 0.50)
            if b1y is not None:
                weights["1Y"] = (b1y, 0.20)
            if ind is not None:
                weights["ind"] = (ind, 0.30)

            if not weights:
                return 1.0, "默认值（数据不足）"

            total_w = sum(w for _, w in weights.values())
            beta = sum(v * w / total_w for v, w in weights.values())
            parts = ["%s*%.0f%%" % (k, w / total_w * 100) for k, (_, w) in weights.items()]
            method = "+".join(parts)
        elif 1 <= listing_years < 3:
            # 1-3yr: 1Y*50% + industry*50%
            if b1y is not None and ind is not None:
                beta = b1y * 0.50 + ind * 0.50
                method = "1Y*50%+行业*50%"
            elif b1y is not None:
                beta = b1y
                method = "1Y（行业Beta不可用）"
            elif ind is not None:
                beta = ind
                method = "行业Beta（1Y不可用）"
            else:
                beta = 1.0
                method = "默认值"
        else:
            # <1yr
            if ind is not None:
                beta = ind * 1.2
                method = "行业Beta*1.2（上市不足1年）"
            else:
                beta = 1.0
                method = "默认值（上市不足1年且无行业Beta）"

        return beta, method


# ============================================================================
# 3.5 EPSForecaster - EPS 预测（5 种趋势 x 3 种情景）
# ============================================================================

# 趋势类型中文名称映射
TREND_TYPE_CN = {
    "stable_growth": "稳定增长型",
    "high_growth": "高速增长型",
    "cyclical": "周期波动型",
    "declining": "下行趋势型",
    "distressed_recovery": "困境恢复型",
    "insufficient_data": "数据不足",
}

# 可持续高增长行业（消费/医药）
SUSTAINABLE_KEYWORDS = [
    "消费", "白酒", "食品", "饮料", "医药", "医疗", "生物",
    "化妆品", "日用品", "家电", "零售",
    "consumer", "pharma", "biotech", "healthcare",
]


class EPSForecaster:
    """EPS 预测器 - 5 种趋势类型 x 3 种情景。"""

    def __init__(self, data):
        self.data = data
        self.warnings = []

    def forecast(self):
        """生成 EPS 三情景预测。"""
        inc = self.data.get("income_data", {}).get("annual_series", {})
        eps_series = inc.get("eps", [])
        vals = get_values(eps_series)

        if len(vals) < 2:
            self.warnings.append("EPS数据不足，无法进行预测")
            return {
                "trend_type": "insufficient_data",
                "trend_type_cn": TREND_TYPE_CN.get("insufficient_data", "数据不足"),
                "cagr": None,
                "volatility": None,
                "pessimistic": None,
                "base": None,
                "optimistic": None,
                "analyst_comparison": None,
            }

        ttm_eps = vals[-1]

        # 计算 CAGR
        n_years = len(vals) - 1
        cagr_val = calc_cagr(vals[0], vals[-1], n_years) if n_years > 0 else None
        # 如果标准 CAGR 失败（负值），尝试从正值子集计算
        if cagr_val is None:
            positives = [(i, v) for i, v in enumerate(vals) if v > 0]
            if len(positives) >= 2:
                cagr_val = calc_cagr(positives[0][1], positives[-1][1],
                                     positives[-1][0] - positives[0][0])
            if cagr_val is None:
                cagr_val = 0.0

        # 计算波动率
        volatility = calc_volatility(vals)

        # 判定趋势类型
        trend_type = self._identify_trend(vals, cagr_val, volatility)

        # 生成三情景预测
        industry = self.data.get("meta", {}).get("industry", "")
        pess, base, opti = self._generate_forecasts(
            trend_type, ttm_eps, vals, cagr_val, industry
        )

        # 分析师比较
        analyst_comparison = self._compare_analyst()

        return {
            "trend_type": trend_type,
            "trend_type_cn": TREND_TYPE_CN.get(trend_type, trend_type),
            "cagr": round(cagr_val, 4) if cagr_val is not None else None,
            "volatility": round(volatility, 4),
            "pessimistic": round(pess, 2) if pess is not None else None,
            "base": round(base, 2) if base is not None else None,
            "optimistic": round(opti, 2) if opti is not None else None,
            "analyst_comparison": analyst_comparison,
        }

    def _identify_trend(self, vals, cagr_val, volatility):
        """
        趋势识别:
          困境反转: 近期大幅亏损后改善（优先检查）
          CAGR > 20% -> high_growth
          CAGR > 0 且 volatility < 20% -> stable_growth
          CAGR < 0 -> declining
          volatility > 30% -> cyclical
          默认 -> stable_growth
        """
        # 优先检查困境反转
        if self._is_distressed_recovery(vals):
            return "distressed_recovery"

        if cagr_val is not None and cagr_val > 0.20:
            return "high_growth"
        if cagr_val is not None and cagr_val > 0 and volatility < 0.20:
            return "stable_growth"
        if cagr_val is not None and cagr_val < 0:
            return "declining"
        if volatility > 0.30:
            return "cyclical"
        return "stable_growth"

    def _is_distressed_recovery(self, vals):
        """判断是否属于困境反转型：之前有大幅亏损，最新值改善。"""
        if len(vals) < 3:
            return False
        has_loss = any(v < 0 for v in vals[:-1])
        latest_improved = vals[-1] > vals[-2]
        if has_loss and latest_improved:
            loss_count = sum(1 for v in vals[:-1] if v < 0)
            if loss_count >= 2:
                return True
        return False

    def _generate_forecasts(self, trend_type, ttm_eps, vals, cagr_val, industry):
        """根据趋势类型生成三情景预测。"""

        if trend_type == "stable_growth" and cagr_val is not None:
            p = ttm_eps * (1 + cagr_val * 0.5)
            b = ttm_eps * (1 + cagr_val)
            o = ttm_eps * (1 + cagr_val * 1.5)
            return p, b, o

        elif trend_type == "high_growth" and cagr_val is not None:
            is_sustainable = any(kw.lower() in industry.lower() for kw in SUSTAINABLE_KEYWORDS)
            if is_sustainable:
                # 可持续型（消费/医药）
                p = ttm_eps * (1 + cagr_val * 0.4)
                b = ttm_eps * (1 + cagr_val * 0.7)
                o = ttm_eps * (1 + cagr_val * 1.0)
            else:
                # 爆发式（科技/周期）
                p = ttm_eps * (1 + cagr_val * 0.2)
                b = ttm_eps * (1 + cagr_val * 0.5)
                o = ttm_eps * (1 + cagr_val * 0.8)
            return p, b, o

        elif trend_type == "cyclical":
            # 均值回归
            p = min(vals) if vals else ttm_eps
            b = sum(vals) / len(vals) if vals else ttm_eps
            o = max(vals) if vals else ttm_eps
            return p, b, o

        elif trend_type == "declining" and cagr_val is not None:
            # 下降型（optimistic = 跌幅更小）
            p = ttm_eps * (1 + cagr_val * 1.5)  # cagr < 0，跌更多
            b = ttm_eps * (1 + cagr_val)
            o = ttm_eps * (1 + cagr_val * 0.3)  # 跌得少
            return p, b, o

        elif trend_type == "distressed_recovery":
            # 困境反转型：PE模型参考价值低，建议 DCF/PS
            self.warnings.append("困境反转型公司，PE模型参考价值低，建议优先使用DCF/PS")
            if vals:
                p = ttm_eps * 0.5 if ttm_eps > 0 else min(vals[-2:]) if len(vals) >= 2 else vals[-1]
                b = ttm_eps
                o = ttm_eps * 1.5 if ttm_eps > 0 else 0.0
            else:
                return None, None, None
            return p, b, o

        # 默认
        p = ttm_eps * 0.8
        b = ttm_eps
        o = ttm_eps * 1.2
        return p, b, o

    def _compare_analyst(self):
        """与分析师预测比较（如有数据）。"""
        analyst = self.data.get("analyst_forecast")
        if not analyst or not isinstance(analyst, dict):
            return None

        analyst_eps = analyst.get("eps_forecast")
        if analyst_eps is None:
            return None

        inc = self.data.get("income_data", {}).get("annual_series", {})
        eps_vals = get_values(inc.get("eps", []))
        our_eps = eps_vals[-1] if eps_vals else None

        if our_eps is None or our_eps == 0:
            return None

        deviation = abs(safe_float(analyst_eps) - our_eps) / abs(our_eps)

        if deviation < 0.10:
            level = "一致"
            note = "偏差%.1f%%，与分析师预测基本一致" % (deviation * 100)
        elif deviation < 0.25:
            level = "偏差"
            note = "偏差%.1f%%，需要解释偏差原因" % (deviation * 100)
        else:
            level = "重大偏差"
            note = "偏差%.1f%%，与分析师预测存在重大分歧" % (deviation * 100)

        return {
            "analyst_eps": safe_float(analyst_eps),
            "deviation": round(deviation, 4),
            "level": level,
            "note": note,
        }


# ============================================================================
# RevenueForecaster - 营收预测（用于 PS 模型）
# ============================================================================

class RevenueForecaster:
    """营收预测器 - 类似 EPS 的趋势分类 + 三情景。"""

    def __init__(self, data):
        self.data = data
        self.warnings = []

    def forecast(self):
        """生成营收三情景预测。"""
        inc = self.data.get("income_data", {}).get("annual_series", {})
        rev_series = inc.get("revenue", [])
        vals = get_values(rev_series)

        if len(vals) < 2:
            self.warnings.append("营收数据不足，无法进行预测")
            return {"pessimistic": None, "base": None, "optimistic": None, "cagr": None}

        ttm_rev = vals[-1]
        n_years = len(vals) - 1
        cagr_val = calc_cagr(vals[0], vals[-1], n_years) if n_years > 0 else None
        if cagr_val is None:
            cagr_val = 0.0

        volatility = calc_volatility(vals)
        trend = self._classify(vals, cagr_val, volatility)

        p, b, o = self._gen_scenarios(trend, ttm_rev, vals, cagr_val)

        return {
            "pessimistic": round(p, 2) if p is not None else None,
            "base": round(b, 2) if b is not None else None,
            "optimistic": round(o, 2) if o is not None else None,
            "cagr": round(cagr_val, 4),
        }

    def _classify(self, vals, cagr_val, volatility):
        """营收趋势分类。"""
        if cagr_val > 0.20:
            return "high_growth"
        elif cagr_val > 0 and volatility < 0.20:
            return "stable_growth"
        elif cagr_val < 0:
            return "declining"
        elif volatility > 0.30:
            return "cyclical"
        return "stable_growth"

    def _gen_scenarios(self, trend, ttm_rev, vals, cagr_val):
        """生成三情景。"""
        industry = self.data.get("meta", {}).get("industry", "")

        if trend == "stable_growth":
            return (
                ttm_rev * (1 + cagr_val * 0.5),
                ttm_rev * (1 + cagr_val),
                ttm_rev * (1 + cagr_val * 1.5),
            )
        elif trend == "high_growth":
            is_sustainable = any(kw.lower() in industry.lower() for kw in SUSTAINABLE_KEYWORDS)
            if is_sustainable:
                return (
                    ttm_rev * (1 + cagr_val * 0.4),
                    ttm_rev * (1 + cagr_val * 0.7),
                    ttm_rev * (1 + cagr_val * 1.0),
                )
            else:
                return (
                    ttm_rev * (1 + cagr_val * 0.2),
                    ttm_rev * (1 + cagr_val * 0.5),
                    ttm_rev * (1 + cagr_val * 0.8),
                )
        elif trend == "cyclical":
            return (min(vals), sum(vals) / len(vals), max(vals))
        elif trend == "declining":
            return (
                ttm_rev * (1 + cagr_val * 1.5),
                ttm_rev * (1 + cagr_val),
                ttm_rev * (1 + cagr_val * 0.3),
            )
        return None, None, None


# ============================================================================
# WACC Calculator - WACC 计算
# ============================================================================

class WACCCalculator:
    """
    WACC 计算器:
      Ke = risk_free_rate + beta * market_risk_premium
      WACC = Ke * (E/V) + Kd * (1-T) * (D/V)
      V = E + D

    合理性检查:
      WACC < risk_free_rate + 2% -> 检查 beta/debt
      WACC > 18% -> 检查异常
    """

    def __init__(self, data, final_beta, tax_rate):
        self.data = data
        self.final_beta = final_beta
        self.tax_rate = tax_rate
        self.warnings = []

    def calculate(self):
        """计算并返回 WACC 结果字典。"""
        wacc_inputs = self.data.get("wacc_inputs", {})

        # 无风险利率
        rf = safe_float(wacc_inputs.get("risk_free_rate"), default=2.85)
        if rf > 0.5:
            rf = rf / 100.0

        # 市场风险溢价
        mrp = safe_float(wacc_inputs.get("market_risk_premium"), default=6.0)
        if mrp > 0.5:
            mrp = mrp / 100.0

        # 债务成本 Kd
        kd = self._get_kd(wacc_inputs)

        # Ke = Rf + Beta * MRP
        ke = rf + self.final_beta * mrp

        # 资本结构
        equity = safe_float(wacc_inputs.get("equity_market_cap"), default=0.0)
        debt = safe_float(wacc_inputs.get("debt_total"), default=0.0)
        if debt == 0.0:
            bal = self.data.get("balance_data", {}).get("latest", {})
            debt = safe_float(bal.get("interest_bearing_debt"), default=0.0)

        v = equity + debt
        if v <= 0:
            self.warnings.append("总价值(V=E+D)<=0，WACC计算不可靠")
            kd_at = kd * (1 - self.tax_rate)
            return {
                "ke": round(ke, 4),
                "kd_after_tax": round(kd_at, 4),
                "wacc": round(ke, 4),
                "ev_ratio": 1.0,
                "dv_ratio": 0.0,
                "warning": "总价值为零或负，使用Ke作为WACC",
            }

        ev_ratio = equity / v
        dv_ratio = debt / v
        kd_at = kd * (1 - self.tax_rate)

        wacc = ke * ev_ratio + kd_at * dv_ratio

        # 合理性检查
        warning = None
        if wacc < rf + 0.02:
            warning = "WACC(%.2f%%)低于无风险利率+2%%，请检查Beta或债务占比" % (wacc * 100)
            self.warnings.append(warning)
        elif wacc > 0.18:
            warning = "WACC(%.2f%%)异常偏高(>18%%)，请检查债务成本或Beta" % (wacc * 100)
            self.warnings.append(warning)

        return {
            "ke": round(ke, 4),
            "kd_after_tax": round(kd_at, 4),
            "wacc": round(wacc, 4),
            "ev_ratio": round(ev_ratio, 4),
            "dv_ratio": round(dv_ratio, 4),
            "warning": warning,
        }

    def _get_kd(self, wacc_inputs):
        """
        获取税前债务成本 Kd:
        - 使用提供的 kd 值
        - 或从 interest_expense / avg_interest_bearing_debt 估算
        - 或默认 risk_free_rate + 1.5%
        """
        kd = wacc_inputs.get("kd")
        if kd is not None and safe_float(kd) > 0:
            kd_val = safe_float(kd)
            if kd_val > 0.5:
                kd_val = kd_val / 100.0
            return kd_val

        # 估算
        bal = self.data.get("balance_data", {}).get("latest", {})
        ibd = safe_float(bal.get("interest_bearing_debt"), default=0.0)
        inc = self.data.get("income_data", {}).get("annual_series", {})
        ie_series = inc.get("interest_expense", [])
        ie = get_latest_value(ie_series) if ie_series else None

        if ie is not None and ibd > 0:
            estimated = abs(ie) / ibd
            if estimated > 0.5:
                estimated = estimated / 100.0
            return estimated

        # 默认
        rf = safe_float(wacc_inputs.get("risk_free_rate"), default=2.85)
        if rf > 0.5:
            rf = rf / 100.0
        return rf + 0.015


# ============================================================================
# DPSForecaster - 股息预测（用于 DDM 模型）
# ============================================================================

class DPSForecaster:
    """股息预测器：计算 D1、增长率、派息稳定性、OCF 覆盖率。"""

    def __init__(self, data, ke):
        self.data = data
        self.ke = ke
        self.warnings = []

    def forecast(self):
        """预测下一年度股息。"""
        div = self.data.get("dividend_history", {})
        records = div.get("records", [])

        if not records or len(records) < 2:
            return None

        sorted_records = sorted(records, key=lambda x: x.get("year", 0))
        dps_values = [safe_float(r.get("dps", 0)) for r in sorted_records]

        if not dps_values or all(d == 0 for d in dps_values):
            return None

        # DPS 增长率
        growth_rates = []
        for i in range(1, len(dps_values)):
            if dps_values[i - 1] > 0:
                growth_rates.append(
                    (dps_values[i] - dps_values[i - 1]) / dps_values[i - 1]
                )
        avg_growth = sum(growth_rates) / len(growth_rates) if growth_rates else 0.0

        # 也可尝试 CAGR
        positive_start = next((v for v in dps_values if v > 0), None)
        positive_end = dps_values[-1]
        if positive_start and positive_end and positive_start > 0:
            start_idx = dps_values.index(positive_start)
            span = len(dps_values) - 1 - start_idx
            cagr_growth = calc_cagr(positive_start, positive_end, span) if span > 0 else None
            if cagr_growth is not None:
                avg_growth = cagr_growth

        # 增长率不能超过 Ke（DDM 收敛条件）
        if avg_growth >= self.ke:
            avg_growth = self.ke * 0.8
            self.warnings.append("股息增长率接近或超过Ke，已截断至Ke*80%%")

        # D1
        latest_dps = dps_values[-1]
        d1 = latest_dps * (1 + avg_growth)

        # 派息稳定性
        payout_ratios = [
            safe_float(r.get("payout_ratio", 0))
            for r in sorted_records
        ]
        payout_ratios = [p for p in payout_ratios if p > 0]
        if len(payout_ratios) >= 3:
            pr_std = calc_std(payout_ratios)
            pr_mean = sum(payout_ratios) / len(payout_ratios)
            cv = safe_div(pr_std, pr_mean)
            if cv < 0.15:
                stability = "stable"
            elif cv < 0.30:
                stability = "moderate"
            else:
                stability = "unstable"
        elif len(payout_ratios) >= 2:
            pr_std = calc_std(payout_ratios)
            stability = "stable" if pr_std < 15 else "unstable"
        else:
            stability = "unknown"

        # OCF 覆盖率
        cf = self.data.get("cashflow_data", {}).get("annual_series", {})
        ocf_vals = get_values(cf.get("operating_cashflow", []))
        total_shares = safe_float(
            self.data.get("balance_data", {}).get("latest", {}).get("total_shares")
            or self.data.get("real_time", {}).get("total_shares"),
            default=1.0,
        )

        ocf_coverage = None
        if ocf_vals and latest_dps > 0 and total_shares > 0:
            latest_ocf = ocf_vals[-1]
            total_div = latest_dps * total_shares
            if total_div > 0:
                ocf_coverage = round(latest_ocf / total_div, 2)

        return {
            "d1": round(d1, 4),
            "growth_rate": round(avg_growth, 4),
            "payout_stability": stability,
            "ocf_coverage": ocf_coverage,
        }


# ============================================================================
# QualitativeDocParser - 定性文档解析
# ============================================================================

def parse_qualitative_doc(filepath):
    """
    解析定性分析 Markdown 文档中的评级信息。
    查找:
      - 业务确定性评级：【高/中高/中/中低/低】
      - 增长可持续性评级：【强/较强/中性/较弱/弱】
      - 研判置信度：【高/中/低】
    返回字典，若文件不存在或无有效评级返回 None。
    """
    if not filepath or not os.path.isfile(filepath):
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, UnicodeDecodeError) as e:
        print("[WARNING] 无法读取定性文档 %s: %s" % (filepath, e), file=sys.stderr)
        return None

    if not content.strip():
        return None

    result = {}

    # 业务确定性评级
    m = re.search(r"业务确定性评级[：:]\s*【(高|中高|中|中低|低)】", content)
    if m:
        result["business_certainty"] = m.group(1)

    # 增长可持续性评级
    m = re.search(r"增长可持续性评级[：:]\s*【(强|较强|中性|较弱|弱)】", content)
    if m:
        result["growth_sustainability"] = m.group(1)

    # 研判置信度
    m = re.search(r"研判置信度[：:]\s*【(高|中|低)】", content)
    if m:
        result["confidence"] = m.group(1)

    # 额外：主要风险解析（兼容旧格式）
    risks = re.findall(r"风险[A-Za-z]?[：:]\s*(.+?)，影响程度【?(高|中|低)】?", content)
    if risks:
        result["risks"] = [{"description": r[0].strip(), "impact": r[1]} for r in risks]

    return result if result else None


# ============================================================================
# 主预处理流程
# ============================================================================

class ValuationPreprocessor:
    """估值预处理主控器。"""

    def __init__(self, data, qualitative_doc=None):
        self.data = data
        self.qualitative_doc = qualitative_doc
        self.warnings = []

    def process(self):
        """执行全部预处理步骤，生成预处理参数 JSON。"""
        meta = self.data.get("meta", {})
        code = meta.get("code", "unknown")
        name = meta.get("name", "")

        # 定性分析（可选）
        qual = parse_qualitative_doc(self.qualitative_doc) if self.qualitative_doc else None
        mode = "A" if qual else "B"
        if mode == "B":
            self.warnings.append("模式B运行：缺少定性分析，所有定性调整因子跳过，置信度自动下调")

        # 1. 数据完整性检查
        checker = DataCompletenessChecker(self.data)
        available_models = checker.check()

        # 2. 公司阶段判定
        stage_det = CompanyStageDeterminer(self.data)
        company_stage = stage_det.determine()
        self.warnings.extend(stage_det.warnings)

        # 根据阶段调整模型可用性
        self._apply_stage_adjustments(available_models, company_stage)

        # 3. 参数转换
        converter = ParameterConverter(self.data)
        fcf_series, base_fcf, fcf_trend, fcf_slope = converter.compute_fcf_series()
        tax_rate = converter.normalize_tax_rate()
        ebitda_ttm = converter.get_ebitda_ttm()
        bvps = converter.get_bvps()
        roe_mean = converter.get_roe_mean()
        roa_mean = converter.get_roa_mean()
        gross_margin_latest = converter.get_gross_margin_latest()
        self.warnings.extend(converter.warnings)

        # 4. Beta 合成
        beta_synth = BetaSynthesizer(self.data)
        beta_result = beta_synth.synthesize()
        final_beta = beta_result["final_beta"]
        self.warnings.extend(beta_synth.warnings)

        # 5. WACC 计算
        wacc_calc = WACCCalculator(self.data, final_beta, tax_rate)
        wacc_result = wacc_calc.calculate()
        self.warnings.extend(wacc_calc.warnings)

        # 6. EPS 预测
        eps_fc = EPSForecaster(self.data)
        eps_forecast = eps_fc.forecast()
        self.warnings.extend(eps_fc.warnings)

        # 7. 营收预测
        rev_fc = RevenueForecaster(self.data)
        rev_forecast = rev_fc.forecast()
        self.warnings.extend(rev_fc.warnings)

        # 8. 股息预测
        ke = wacc_result.get("ke", 0.08)
        dps_fc = DPSForecaster(self.data, ke)
        dps_forecast = dps_fc.forecast()
        self.warnings.extend(dps_fc.warnings)

        # 9. 实时快照
        real_time_snapshot = self._build_real_time_snapshot()

        # 10. 历史估值透传
        hist_val = self.data.get("historical_valuation", {})

        # 11. 行业数据透传
        ind_data = self.data.get("industry_data", {})
        industry_out = {
            "industry_pe": ind_data.get("industry_pe"),
            "industry_pb": ind_data.get("industry_pb"),
            "industry_ev_ebitda": ind_data.get("industry_ev_ebitda"),
            "industry_ps": ind_data.get("industry_ps"),
            "peers": ind_data.get("peers", []),
        }

        # 去重 warnings
        seen = set()
        unique_warnings = []
        for w in self.warnings:
            if w and w not in seen:
                seen.add(w)
                unique_warnings.append(w)

        return {
            "meta": {
                "code": code,
                "name": name,
                "mode": mode,
                "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            },
            "company_stage": company_stage,
            "available_models": available_models,
            "wacc": wacc_result,
            "beta": beta_result,
            "eps_forecast": eps_forecast,
            "revenue_forecast": rev_forecast,
            "fcf": {
                "historical_series": fcf_series,
                "base_fcf": base_fcf,
                "trend": fcf_trend,
                "trend_slope": fcf_slope,
            },
            "tax_rate": tax_rate,
            "ebitda_ttm": ebitda_ttm,
            "bvps": bvps,
            "roe_mean": roe_mean,
            "roa_mean": roa_mean,
            "dps_forecast": dps_forecast,
            "gross_margin_latest": gross_margin_latest,
            "qualitative_adjustments": qual,
            "real_time_snapshot": real_time_snapshot,
            "historical_valuation": hist_val,
            "industry_data": industry_out,
            "warnings": unique_warnings,
        }

    def _build_real_time_snapshot(self):
        """构建实时数据快照，补充 EV 和净债务。"""
        rt = self.data.get("real_time", {})
        bal = self.data.get("balance_data", {}).get("latest", {})

        price = rt.get("price")
        total_market_cap = safe_float(rt.get("total_market_cap"), default=0.0)
        total_shares = bal.get("total_shares") or rt.get("total_shares")

        interest_bearing_debt = safe_float(bal.get("interest_bearing_debt"), default=0.0)
        cash = safe_float(bal.get("cash"), default=0.0)
        minority_interest = safe_float(bal.get("minority_interest"), default=0.0)

        net_debt = interest_bearing_debt - cash
        ev = total_market_cap + net_debt + minority_interest

        return {
            "price": price,
            "total_market_cap": total_market_cap,
            "pe_ttm": rt.get("pe_ttm"),
            "pb_mrq": rt.get("pb_mrq"),
            "ps_ttm": rt.get("ps_ttm"),
            "dividend_yield": rt.get("dividend_yield"),
            "total_shares": total_shares,
            "ev": round(ev, 2),
            "net_debt": round(net_debt, 2),
        }

    def _apply_stage_adjustments(self, available_models, company_stage):
        """根据公司阶段调整模型可用性。"""
        extra = company_stage.get("extra_handling")
        if not extra or not isinstance(extra, dict):
            return

        disabled = extra.get("disable_models", [])
        for model in disabled:
            if model in available_models:
                old_note = available_models[model].get("note", "")
                stage_cn = company_stage.get("stage_cn", company_stage.get("stage"))
                available_models[model] = {
                    "available": False,
                    "note": "因公司阶段(%s)禁用" % stage_cn
                    + ("；原始状态: %s" % old_note if old_note else ""),
                }


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    """命令行入口。"""
    parser = argparse.ArgumentParser(
        description="估值预处理器 - 读取原始数据JSON并生成预处理参数JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "\n示例:\n"
            "  python valuation_preprocessor.py "
            "--data-file 600519_valuation_data.json --output-dir ./output\n"
            "  python valuation_preprocessor.py "
            "--data-file data.json --qualitative-doc analysis.md --output-dir ./\n"
        ),
    )
    parser.add_argument(
        "--data-file",
        required=True,
        help="输入数据文件路径 ({code}_valuation_data.json)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="输出目录路径（默认当前目录）",
    )
    parser.add_argument(
        "--qualitative-doc",
        default=None,
        help="可选：定性分析 Markdown 文档路径",
    )

    args = parser.parse_args()

    # 检查输入文件
    if not os.path.isfile(args.data_file):
        print("[ERROR] 输入文件不存在: %s" % args.data_file, file=sys.stderr)
        sys.exit(1)

    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)

    # 读取输入数据
    print("[INFO] 读取数据文件: %s" % args.data_file)
    try:
        with open(args.data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print("[ERROR] JSON 解析失败: %s" % e, file=sys.stderr)
        sys.exit(1)
    except IOError as e:
        print("[ERROR] 文件读取失败: %s" % e, file=sys.stderr)
        sys.exit(1)

    # 提取股票信息
    meta = data.get("meta", {})
    code = meta.get("code", "unknown")
    name = meta.get("name", "")

    print("\n" + "=" * 60)
    print("  估值预处理: %s (%s)" % (name, code))
    print("=" * 60 + "\n")

    # 执行预处理
    preprocessor = ValuationPreprocessor(data, args.qualitative_doc)
    try:
        result = preprocessor.process()
    except Exception as e:
        print("[ERROR] 预处理过程出错: %s" % e, file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    # 打印摘要
    print("  运行模式: 模式%s" % result["meta"]["mode"])
    print("  公司阶段: %s (%s)" % (result["company_stage"]["stage_cn"], result["company_stage"]["reason"]))

    avail = [k for k, v in result["available_models"].items() if v["available"]]
    unavail = [k for k, v in result["available_models"].items() if not v["available"]]
    print("  可用模型 (%d): %s" % (len(avail), ", ".join(avail)))
    if unavail:
        print("  不可用模型 (%d): %s" % (len(unavail), ", ".join(unavail)))

    wacc_pct = result["wacc"]["wacc"] * 100 if result["wacc"]["wacc"] < 1 else result["wacc"]["wacc"]
    ke_pct = result["wacc"]["ke"] * 100 if result["wacc"]["ke"] < 1 else result["wacc"]["ke"]
    print("  WACC: %.2f%% (Ke=%.2f%%, Beta=%.3f)" % (wacc_pct, ke_pct, result["beta"]["final_beta"]))

    if result["eps_forecast"].get("base") is not None:
        print("  EPS预测: %s (悲观%.2f, 基准%.2f, 乐观%.2f)" % (
            result["eps_forecast"]["trend_type_cn"],
            result["eps_forecast"]["pessimistic"],
            result["eps_forecast"]["base"],
            result["eps_forecast"]["optimistic"],
        ))

    # 警告
    if result["warnings"]:
        print("\n  警告:")
        for w in result["warnings"]:
            print("    - %s" % w)

    # 保存结果
    output_filename = "%s_preprocessed.json" % code
    output_path = os.path.join(args.output_dir, output_filename)

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    except IOError as e:
        print("[ERROR] 写入输出文件失败: %s" % e, file=sys.stderr)
        sys.exit(1)

    print("\n  预处理结果已保存: %s" % output_path)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
