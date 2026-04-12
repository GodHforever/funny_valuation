#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
估值整合器 (Valuation Integrator)

零依赖脚本(仅使用Python 3.6+标准库)，实现估值整合全流程：
  - 情景概率引擎 (Section 5.1)
  - 模型权重分配 (Section 5.3)
  - 估值矩阵构建 (Section 5.4)
  - 模型分歧分析 (Section 5.5)
  - 陷阱检测 (Section 7)
  - 安全边际计算 (Section 6)
  - 买入区间构建 (Section 6.3)
  - 特殊情景处理 (Section 8)

用法:
  python valuation_integrator.py \
    --data-file {code}_valuation_data.json \
    --preprocessed-file {code}_preprocessed.json \
    --model-results-file {code}_model_results.json \
    --output-dir ./output
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime


# ============================================================================
# 辅助工具函数
# ============================================================================

def _safe_get(d, *keys, default=None):
    """安全地从嵌套字典中取值"""
    current = d
    for k in keys:
        if isinstance(current, dict):
            current = current.get(k, default)
            if current is default:
                return default
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(k)]
            except (IndexError, ValueError, TypeError):
                return default
        else:
            return default
    return current


def _safe_float(val, default=0.0):
    """安全地转换为浮点数"""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_div(numerator, denominator, default=0.0):
    """安全除法，避免除零"""
    if denominator is None or denominator == 0:
        return default
    return numerator / denominator


def _round_to_5(val):
    """四舍五入到最近的5的整数倍"""
    return int(round(val / 5.0)) * 5


def _clamp(val, lo, hi):
    """将值限制在[lo, hi]范围内"""
    return max(lo, min(hi, val))


def _pct_round(val, decimals=1):
    """百分比四舍五入"""
    return round(val, decimals)


def _load_json(path):
    """加载JSON文件，失败时返回空字典"""
    if not os.path.isfile(path):
        print("[WARN] 文件不存在: {}".format(path), file=sys.stderr)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print("[ERROR] 无法解析JSON文件 {}: {}".format(path, e), file=sys.stderr)
        return {}


def _compute_growth_rates(series):
    """计算序列的同比增长率列表，series为时间正序(旧->新)"""
    if not series or len(series) < 2:
        return []
    rates = []
    for i in range(1, len(series)):
        prev = _safe_float(series[i - 1])
        curr = _safe_float(series[i])
        if prev and prev != 0:
            rates.append((curr - prev) / abs(prev))
        else:
            rates.append(0.0)
    return rates


# ============================================================================
# Section 5.1: 情景概率引擎
# ============================================================================

# 模式A概率模板: (pessimistic, base, optimistic)
_PROBABILITY_TEMPLATES = {
    ("high_certainty", "strong_growth"):  (15, 55, 30),
    ("high_certainty", "neutral_growth"): (20, 60, 20),
    ("medium_certainty", None):           (25, 50, 25),
    ("low_certainty", None):              (35, 45, 20),
    ("low_certainty", "weak_growth"):     (40, 45, 15),
}


def _classify_certainty(qualitative):
    """从定性分析中推断确定性等级"""
    if qualitative is None:
        return "medium_certainty"
    cert = _safe_get(qualitative, "certainty_level", default="medium")
    if isinstance(cert, str):
        cert = cert.lower()
    if cert in ("high", "high_certainty"):
        return "high_certainty"
    elif cert in ("low", "low_certainty"):
        return "low_certainty"
    return "medium_certainty"


def _classify_growth(qualitative):
    """从定性分析中推断成长前景"""
    if qualitative is None:
        return "neutral_growth"
    growth = _safe_get(qualitative, "growth_outlook", default="neutral")
    if isinstance(growth, str):
        growth = growth.lower()
    if growth in ("strong", "strong_growth"):
        return "strong_growth"
    elif growth in ("weak", "weak_growth"):
        return "weak_growth"
    return "neutral_growth"


def _select_template(certainty, growth):
    """根据确定性和成长前景选择概率模板"""
    # 精确匹配
    key = (certainty, growth)
    if key in _PROBABILITY_TEMPLATES:
        return _PROBABILITY_TEMPLATES[key]
    # 仅按确定性匹配(growth=None的通配)
    key_wild = (certainty, None)
    if key_wild in _PROBABILITY_TEMPLATES:
        return _PROBABILITY_TEMPLATES[key_wild]
    # 默认
    return (25, 50, 25)


def _adjust_for_traps(probs, warning_count):
    """根据陷阱信号调整概率"""
    pess, base, opti = probs
    if warning_count == 0:
        return (pess, base, opti)
    elif warning_count == 1:
        # 悲观+5%，乐观-5%
        pess += 5
        opti -= 5
    else:
        # 2+ 警告: 悲观+10%，基准-5%，乐观-5%
        pess += 10
        base -= 5
        opti -= 5
    return (pess, base, opti)


def _enforce_probability_constraints(pess, base, opti):
    """
    概率约束:
      1. 总和 = 100%
      2. 每项 >= 10%
      3. 每项 <= 70%
      4. 四舍五入到最近5%
    """
    # 先四舍五入到5的倍数
    pess = _round_to_5(pess)
    base = _round_to_5(base)
    opti = _round_to_5(opti)

    # 下限约束
    pess = max(10, pess)
    base = max(10, base)
    opti = max(10, opti)

    # 上限约束
    pess = min(70, pess)
    base = min(70, base)
    opti = min(70, opti)

    # 调整总和为100
    total = pess + base + opti
    if total != 100:
        diff = 100 - total
        # 优先调整base
        base += diff
        base = _clamp(base, 10, 70)
        # 再次检查
        total = pess + base + opti
        if total != 100:
            diff = 100 - total
            # 调整pess
            pess += diff
            pess = _clamp(pess, 10, 70)
            total = pess + base + opti
            if total != 100:
                diff = 100 - total
                opti += diff
                opti = _clamp(opti, 10, 70)

    # 最终确保四舍五入到5
    pess = _round_to_5(pess)
    base = _round_to_5(base)
    opti = _round_to_5(opti)

    # 最终总和修正(如果四舍五入导致偏差)
    total = pess + base + opti
    if total != 100:
        base += (100 - total)
        base = _clamp(base, 10, 70)

    return (pess, base, opti)


def compute_scenario_probabilities(mode, qualitative, warning_count):
    """
    计算情景概率

    Args:
        mode: "A" 或 "B"
        qualitative: 定性分析数据(模式A)或None(模式B)
        warning_count: 陷阱警告数量

    Returns:
        dict: {pessimistic, base, optimistic, rationale}
    """
    if mode == "B" or qualitative is None:
        # 模式B: 固定保守概率
        probs = (25, 50, 25)
        rationale = "模式B固定保守概率"
    else:
        # 模式A: 根据公司特征选择模板
        certainty = _classify_certainty(qualitative)
        growth = _classify_growth(qualitative)
        probs = _select_template(certainty, growth)
        rationale = "模式A概率模板: 确定性={}, 成长={}".format(certainty, growth)

    # 陷阱信号调整
    probs = _adjust_for_traps(probs, warning_count)
    if warning_count > 0:
        rationale += "; 陷阱信号调整({}个警告)".format(warning_count)

    # 约束执行
    pess, base, opti = _enforce_probability_constraints(*probs)

    return {
        "pessimistic": pess,
        "base": base,
        "optimistic": opti,
        "rationale": rationale,
    }


# ============================================================================
# Section 5.3: 模型权重分配
# ============================================================================

# 按公司阶段的默认权重模板
_WEIGHT_TEMPLATES = {
    "growth": {
        "DCF": 30, "PS": 30, "PE": 20,
        "PB": 5, "EV_EBITDA": 5, "DDM": 5, "SOTP": 0, "REVERSE": 5,
    },
    "mature": {
        "DCF": 25, "PE": 30, "EV_EBITDA": 25,
        "PB": 5, "PS": 5, "DDM": 5, "SOTP": 0, "REVERSE": 5,
    },
    "asset_heavy": {
        "PB": 30, "EV_EBITDA": 35, "DCF": 20,
        "PE": 5, "PS": 5, "DDM": 0, "SOTP": 0, "REVERSE": 5,
    },
    "high_dividend": {
        "DDM": 30, "PE": 25, "DCF": 25,
        "PB": 5, "PS": 0, "EV_EBITDA": 10, "SOTP": 0, "REVERSE": 5,
    },
    "diversified": {
        "SOTP": 50, "DCF": 25, "PE": 13, "PB": 12,
        "PS": 0, "EV_EBITDA": 0, "DDM": 0, "REVERSE": 0,
    },
    "unprofitable": {
        "PS": 40, "DCF": 30, "REVERSE": 30,
        "PE": 0, "PB": 0, "EV_EBITDA": 0, "DDM": 0, "SOTP": 0,
    },
    "distressed": {
        "PS": 35, "EV_EBITDA": 35, "REVERSE": 30,
        "PE": 0, "PB": 0, "DCF": 0, "DDM": 0, "SOTP": 0,
    },
}

# 所有估值模型名称
_ALL_MODELS = ["DCF", "PE", "PB", "EV_EBITDA", "PS", "DDM", "SOTP", "REVERSE"]


def _get_default_weights(stage):
    """获取指定阶段的默认权重模板"""
    template = _WEIGHT_TEMPLATES.get(stage, _WEIGHT_TEMPLATES["mature"])
    # 确保所有模型都有权重
    weights = {}
    for m in _ALL_MODELS:
        weights[m] = template.get(m, 0)
    return weights


def _redistribute_weights(weights, available_models, model_results):
    """
    将不可用模型的权重按比例重分配给可用模型

    REVERSE模型不参与加权估值计算(无pessimistic/base/optimistic价格)，
    但保留其权重用于显示。

    Args:
        weights: 初始权重字典
        available_models: 可用模型信息
        model_results: 模型结果数据

    Returns:
        dict: 重新分配后的权重
    """
    # 判断每个模型是否可用
    unavailable = []
    available = []
    for m in _ALL_MODELS:
        model_avail = False
        # 检查available_models中的标记
        if available_models:
            avail_info = available_models.get(m, {})
            if isinstance(avail_info, dict):
                model_avail = avail_info.get("available", False)
            elif isinstance(avail_info, bool):
                model_avail = avail_info

        # 检查model_results中的applicable标记
        if model_results:
            mr = model_results.get(m, {})
            if isinstance(mr, dict):
                applicable = mr.get("applicable", False)
                model_avail = model_avail and applicable

        if model_avail and weights.get(m, 0) > 0:
            available.append(m)
        else:
            unavailable.append(m)

    # 计算需要重新分配的权重总和
    redistribute_total = sum(weights.get(m, 0) for m in unavailable)

    if redistribute_total == 0 or not available:
        # 没有需要重新分配的，或没有可用模型
        result = {}
        for m in _ALL_MODELS:
            if m in available:
                result[m] = weights.get(m, 0)
            else:
                result[m] = 0
        return result

    # 可用模型的原始权重总和
    available_total = sum(weights.get(m, 0) for m in available)

    result = {}
    for m in _ALL_MODELS:
        if m in unavailable:
            result[m] = 0
        else:
            original = weights.get(m, 0)
            if available_total > 0:
                # 按比例分配
                extra = redistribute_total * (original / available_total)
                result[m] = original + extra
            else:
                result[m] = original

    # 归一化到100
    total = sum(result.values())
    if total > 0 and total != 100:
        factor = 100.0 / total
        for m in _ALL_MODELS:
            result[m] = result[m] * factor

    # 四舍五入到整数，确保总和为100
    for m in _ALL_MODELS:
        result[m] = round(result[m])

    total = sum(result.values())
    if total != 100:
        # 差额加到权重最大的可用模型上
        diff = 100 - total
        if available:
            max_m = max(available, key=lambda x: result.get(x, 0))
            result[max_m] += diff

    return result


def compute_model_weights(stage, available_models, model_results):
    """
    计算模型权重

    Args:
        stage: 公司阶段
        available_models: 可用模型信息
        model_results: 模型结果数据

    Returns:
        dict: 包含权重和说明
    """
    base_weights = _get_default_weights(stage)
    final_weights = _redistribute_weights(base_weights, available_models, model_results)

    # 生成说明
    unavailable = [m for m in _ALL_MODELS
                   if final_weights.get(m, 0) == 0 and base_weights.get(m, 0) > 0]

    note_parts = []
    stage_cn_map = {
        "growth": "成长型", "mature": "成熟型", "asset_heavy": "重资产型",
        "high_dividend": "高分红型", "diversified": "多元化", "unprofitable": "亏损型",
        "distressed": "困境型",
    }
    stage_cn = stage_cn_map.get(stage, stage)
    note_parts.append("{}公司权重模板".format(stage_cn))
    if unavailable:
        note_parts.append("{}不可用已重分配".format("/".join(unavailable)))

    result = {}
    for m in _ALL_MODELS:
        result[m] = final_weights.get(m, 0)
    result["note"] = "，".join(note_parts)

    return result


# ============================================================================
# Section 5.4: 估值矩阵
# ============================================================================

def build_valuation_matrix(model_weights, model_results, current_price):
    """
    构建估值矩阵

    REVERSE模型不参与加权计算(无价格估值)。

    Args:
        model_weights: 模型权重字典
        model_results: 模型结果数据
        current_price: 当前股价

    Returns:
        dict: 估值矩阵
    """
    models_data = {}
    weighted_pess = 0.0
    weighted_base = 0.0
    weighted_opti = 0.0
    total_weight_for_valuation = 0  # 参与加权估值的权重总和

    for m in _ALL_MODELS:
        w = model_weights.get(m, 0)
        if w == 0:
            continue

        mr = model_results.get(m, {})
        if not isinstance(mr, dict) or not mr.get("applicable", False):
            continue

        # REVERSE模型没有pessimistic/base/optimistic价格
        if m == "REVERSE":
            models_data[m] = {
                "weight": w,
                "pessimistic": None,
                "base": None,
                "optimistic": None,
                "details": mr.get("details", {}),
            }
            continue

        pess = _safe_float(mr.get("pessimistic"), default=None)
        base = _safe_float(mr.get("base"), default=None)
        opti = _safe_float(mr.get("optimistic"), default=None)

        if base is None:
            continue

        models_data[m] = {
            "weight": w,
            "pessimistic": round(pess, 2) if pess is not None else None,
            "base": round(base, 2) if base is not None else None,
            "optimistic": round(opti, 2) if opti is not None else None,
        }

        # 加权计算
        w_frac = w / 100.0
        total_weight_for_valuation += w
        if pess is not None:
            weighted_pess += pess * w_frac
        if base is not None:
            weighted_base += base * w_frac
        if opti is not None:
            weighted_opti += opti * w_frac

    # 如果参与加权的权重总和不等于100，需要归一化
    if total_weight_for_valuation > 0 and total_weight_for_valuation != 100:
        scale = 100.0 / total_weight_for_valuation
        weighted_pess *= scale
        weighted_base *= scale
        weighted_opti *= scale

    weighted_pess = round(weighted_pess, 2)
    weighted_base = round(weighted_base, 2)
    weighted_opti = round(weighted_opti, 2)

    # 计算溢价/折价
    base_pd = _pct_round(
        _safe_div(current_price - weighted_base, weighted_base, 0) * 100)
    pess_downside = _pct_round(
        _safe_div(weighted_pess - current_price, current_price, 0) * 100)
    opti_upside = _pct_round(
        _safe_div(weighted_opti - current_price, current_price, 0) * 100)

    return {
        "models": models_data,
        "weighted": {
            "pessimistic": weighted_pess,
            "base": weighted_base,
            "optimistic": weighted_opti,
        },
        "current_price": current_price,
        "base_premium_discount_pct": base_pd,
        "pessimistic_downside_pct": pess_downside,
        "optimistic_upside_pct": opti_upside,
    }


# ============================================================================
# Section 5.5: 模型分歧分析
# ============================================================================

def analyze_divergence(model_results, model_weights):
    """
    分析模型间的分歧程度

    CV = 基准值的标准差 / 基准值的均值 (仅使用applicable=true且有base值的模型)

    Args:
        model_results: 模型结果数据
        model_weights: 模型权重(用于识别活跃模型)

    Returns:
        dict: 分歧分析结果
    """
    base_values = []
    model_bases = {}

    for m in _ALL_MODELS:
        if m == "REVERSE":
            continue
        mr = model_results.get(m, {})
        if not isinstance(mr, dict) or not mr.get("applicable", False):
            continue
        base = mr.get("base")
        if base is not None:
            bv = _safe_float(base)
            if bv > 0:
                base_values.append(bv)
                model_bases[m] = bv

    if len(base_values) < 2:
        return {
            "cv": 0.0,
            "level": "insufficient_data",
            "level_cn": "模型数量不足，无法计算分歧度",
            "outliers": [],
            "analysis": "可用模型少于2个，跳过分歧分析",
        }

    mean_val = sum(base_values) / len(base_values)
    variance = sum((v - mean_val) ** 2 for v in base_values) / len(base_values)
    std_dev = math.sqrt(variance)
    cv = _safe_div(std_dev, mean_val, 0)
    cv = round(cv, 4)

    # 判断分歧等级
    if cv < 0.15:
        level = "high_consistency"
        level_cn = "各模型高度一致"
        analysis = "CV={:.1%}，各模型估值结果高度一致，可信度较高".format(cv)
    elif cv <= 0.30:
        level = "moderate_divergence"
        level_cn = "模型间存在一定分歧"
        analysis = "CV={:.1%}，模型间存在分歧，需关注差异原因".format(cv)
    else:
        level = "significant_divergence"
        level_cn = "模型间分歧显著"
        analysis = ("CV={:.1%}，模型间分歧显著，"
                    "不宜简单取平均，需审查各模型假设").format(cv)

    # 识别离群值(偏离均值超过1.5个标准差的模型)
    outliers = []
    if std_dev > 0:
        for m, bv in model_bases.items():
            z = abs(bv - mean_val) / std_dev
            if z > 1.5:
                direction = "偏高" if bv > mean_val else "偏低"
                outliers.append({
                    "model": m,
                    "base_value": round(bv, 2),
                    "deviation": "{}{:.1%}".format(
                        direction, abs(bv - mean_val) / mean_val),
                })

    if outliers:
        outlier_names = [o["model"] for o in outliers]
        analysis += "；离群模型: {}".format(", ".join(outlier_names))

    return {
        "cv": cv,
        "level": level,
        "level_cn": level_cn,
        "outliers": outliers,
        "analysis": analysis,
    }


# ============================================================================
# Section 7: 陷阱检测
# ============================================================================

def _detect_trap_a_cashflow(valuation_data, model_results):
    """
    A. 现金流质量: OCF/净利润比率(近3年)
    全部>0.8 -> normal; 任一0.5-0.8 -> attention;
    2年<0.5或3年累计<0.7 -> WARNING
    """
    ocf_series = _safe_get(valuation_data, "cashflow_data", "annual_series",
                           "operating_cashflow", default=[])
    np_series = _safe_get(valuation_data, "income_data", "annual_series",
                          "net_profit", default=[])

    if not ocf_series or not np_series:
        return {
            "id": "A", "name": "现金流质量",
            "level": "normal",
            "detail": "数据不足，跳过现金流质量检测",
        }

    # 取最近3年(序列末尾)
    recent_ocf = [_safe_float(x) for x in ocf_series[-3:]]
    recent_np = [_safe_float(x) for x in np_series[-3:]]
    n = min(len(recent_ocf), len(recent_np))
    if n == 0:
        return {
            "id": "A", "name": "现金流质量",
            "level": "normal",
            "detail": "数据不足，跳过现金流质量检测",
        }

    ratios = []
    for i in range(n):
        r = _safe_div(recent_ocf[i], recent_np[i], default=None)
        if r is not None:
            ratios.append(r)

    if not ratios:
        return {
            "id": "A", "name": "现金流质量",
            "level": "normal",
            "detail": "无法计算OCF/净利润比率",
        }

    below_05_count = sum(1 for r in ratios if r < 0.5)
    below_08_count = sum(1 for r in ratios if r < 0.8)
    cumulative_np_abs = sum(abs(x) for x in recent_np[:n])
    cumulative = sum(recent_ocf[:n]) / max(cumulative_np_abs, 1e-10)

    if below_05_count >= 2 or (len(ratios) >= 3 and cumulative < 0.7):
        return {
            "id": "A", "name": "现金流质量",
            "level": "warning",
            "detail": ("近{}年中{}年OCF/净利润<0.5，"
                       "累计比率{:.2f}，现金流质量堪忧").format(
                n, below_05_count, cumulative),
        }
    elif below_08_count > 0:
        return {
            "id": "A", "name": "现金流质量",
            "level": "attention",
            "detail": "近{}年中{}年OCF/净利润在0.5-0.8之间，需关注".format(
                n, below_08_count),
        }
    else:
        return {
            "id": "A", "name": "现金流质量",
            "level": "normal",
            "detail": "近{}年OCF/净利润均>0.8，现金流质量良好".format(n),
        }


def _detect_trap_b_receivables(valuation_data):
    """
    B. 应收账款膨胀: AR增速 vs 营收增速(2年均值)
    AR < 营收+10% -> normal; +10-20% -> attention; >+20% -> WARNING
    """
    ar_series = _safe_get(valuation_data, "balance_data", "annual_series",
                          "receivables", default=[])
    rev_series = _safe_get(valuation_data, "income_data", "annual_series",
                           "revenue", default=[])

    if len(ar_series) < 3 or len(rev_series) < 3:
        return {
            "id": "B", "name": "应收账款膨胀",
            "level": "normal",
            "detail": "数据不足(少于3年)，跳过应收账款膨胀检测",
        }

    ar_growth = _compute_growth_rates(ar_series[-3:])
    rev_growth = _compute_growth_rates(rev_series[-3:])

    if not ar_growth or not rev_growth:
        return {
            "id": "B", "name": "应收账款膨胀",
            "level": "normal",
            "detail": "无法计算增长率",
        }

    avg_ar = sum(ar_growth) / len(ar_growth)
    avg_rev = sum(rev_growth) / len(rev_growth)

    excess = avg_ar - avg_rev

    if excess > 0.20:
        return {
            "id": "B", "name": "应收账款膨胀",
            "level": "warning",
            "detail": "应收增速超收入增速{:.0%}，可能存在收入质量问题".format(
                excess),
        }
    elif excess > 0.10:
        return {
            "id": "B", "name": "应收账款膨胀",
            "level": "attention",
            "detail": "应收增速超收入增速{:.0%}，需关注".format(excess),
        }
    else:
        return {
            "id": "B", "name": "应收账款膨胀",
            "level": "normal",
            "detail": "应收增速与收入增速基本匹配",
        }


def _detect_trap_c_inventory(valuation_data, stage):
    """
    C. 存货异常(制造/零售行业): 存货周转天数同比变化
    <15%恶化 -> normal; 15-30% -> attention; >30% -> WARNING
    """
    turnover_days = _safe_get(valuation_data, "balance_data", "annual_series",
                              "inventory_turnover_days", default=[])

    if not turnover_days or len(turnover_days) < 2:
        return {
            "id": "C", "name": "存货异常",
            "level": "normal",
            "detail": "存货周转天数数据不足，跳过检测",
        }

    recent = [_safe_float(x) for x in turnover_days[-2:]]
    if len(recent) < 2 or recent[0] <= 0:
        return {
            "id": "C", "name": "存货异常",
            "level": "normal",
            "detail": "存货周转天数数据异常，跳过检测",
        }

    # 周转天数增加表示恶化
    yoy_change = (recent[1] - recent[0]) / recent[0]

    if yoy_change > 0.30:
        return {
            "id": "C", "name": "存货异常",
            "level": "warning",
            "detail": "存货周转天数同比恶化{:.0%}，存货积压风险".format(
                yoy_change),
        }
    elif yoy_change > 0.15:
        return {
            "id": "C", "name": "存货异常",
            "level": "attention",
            "detail": "存货周转天数同比增加{:.0%}，需关注".format(yoy_change),
        }
    else:
        return {
            "id": "C", "name": "存货异常",
            "level": "normal",
            "detail": "存货周转天数变化在正常范围内",
        }


def _detect_trap_d_debt(valuation_data):
    """
    D. 偿债能力: 短期债务/现金 AND 总债务/EBITDA
    均良好 -> normal; 任一边缘 -> attention; 任一恶劣 -> WARNING
    """
    latest = _safe_get(valuation_data, "balance_data", "latest", default={})
    cash = _safe_float(latest.get("cash", 0))
    ibd = _safe_float(latest.get("interest_bearing_debt", 0))

    # 短期债务/现金比
    debt_cash_ratio = _safe_div(ibd, cash, default=0)

    # 总债务/EBITDA: 需要EBITDA估算
    np_series = _safe_get(valuation_data, "income_data", "annual_series",
                          "net_profit", default=[])
    latest_np = _safe_float(np_series[-1]) if np_series else 0
    # 粗估EBITDA = 净利润 x 1.3 (简化处理)
    ebitda_est = latest_np * 1.3 if latest_np > 0 else 1
    debt_ebitda = _safe_div(ibd, ebitda_est, default=0)

    bad_count = 0
    marginal_count = 0

    # 短期债务/现金判断
    if debt_cash_ratio > 2.0:
        bad_count += 1
    elif debt_cash_ratio > 1.0:
        marginal_count += 1

    # 总债务/EBITDA判断
    if debt_ebitda > 4.0:
        bad_count += 1
    elif debt_ebitda > 2.5:
        marginal_count += 1

    if bad_count > 0:
        return {
            "id": "D", "name": "偿债能力",
            "level": "warning",
            "detail": "债务/现金={:.1f}，债务/EBITDA={:.1f}，偿债压力较大".format(
                debt_cash_ratio, debt_ebitda),
        }
    elif marginal_count > 0:
        return {
            "id": "D", "name": "偿债能力",
            "level": "attention",
            "detail": "债务/现金={:.1f}，债务/EBITDA={:.1f}，偿债能力边缘".format(
                debt_cash_ratio, debt_ebitda),
        }
    else:
        return {
            "id": "D", "name": "偿债能力",
            "level": "normal",
            "detail": "债务/现金={:.1f}，债务/EBITDA={:.1f}，偿债能力良好".format(
                debt_cash_ratio, debt_ebitda),
        }


def _detect_trap_e_low_pe(preprocessed, valuation_data):
    """
    E. 低PE陷阱: PE < 20th percentile AND
       (非经常/利润>30% OR 利润连降2年)
    """
    pe_percentile = _safe_get(preprocessed, "historical_valuation", "pe_range",
                              "current_percentile", default=0.5)

    if pe_percentile is None or pe_percentile >= 0.20:
        return {
            "id": "E", "name": "低PE陷阱",
            "level": "normal",
            "detail": "PE分位数{:.0%}，不在低PE区间".format(
                pe_percentile if pe_percentile is not None else 0.5),
        }

    # 检查非经常性损益占比
    np_series = _safe_get(valuation_data, "income_data", "annual_series",
                          "net_profit", default=[])
    nr_series = _safe_get(valuation_data, "income_data", "annual_series",
                          "non_recurring", default=[])

    trap_signals = []

    if np_series and nr_series:
        latest_np = _safe_float(np_series[-1])
        latest_nr = _safe_float(nr_series[-1]) if nr_series else 0
        if latest_np > 0 and abs(latest_nr) / latest_np > 0.30:
            trap_signals.append("非经常性损益占净利润{:.0%}".format(
                abs(latest_nr) / latest_np))

    # 检查利润是否连降2年
    if len(np_series) >= 3:
        nps = [_safe_float(x) for x in np_series[-3:]]
        if nps[2] < nps[1] < nps[0]:
            trap_signals.append("净利润连续2年下降")

    if trap_signals:
        return {
            "id": "E", "name": "低PE陷阱",
            "level": "warning",
            "detail": "PE处于历史低位(分位数{:.0%})，且{}".format(
                pe_percentile, "、".join(trap_signals)),
        }
    else:
        return {
            "id": "E", "name": "低PE陷阱",
            "level": "normal",
            "detail": "PE处于低位但无其他异常信号",
        }


def _detect_trap_f_low_pb(preprocessed, valuation_data, wacc):
    """
    F. 低PB陷阱: PB < 20th percentile AND
       (商誉/净资产>30% OR ROE<WACC连续3年)
    """
    pb_percentile = _safe_get(preprocessed, "historical_valuation", "pb_range",
                              "current_percentile", default=0.5)

    if pb_percentile is None or pb_percentile >= 0.20:
        return {
            "id": "F", "name": "低PB陷阱",
            "level": "normal",
            "detail": "PB分位数{:.0%}，不在低PB区间".format(
                pb_percentile if pb_percentile is not None else 0.5),
        }

    trap_signals = []

    # 检查商誉/净资产
    latest = _safe_get(valuation_data, "balance_data", "latest", default={})
    goodwill = _safe_float(latest.get("goodwill", 0))
    # 净资产粗估
    total_shares = _safe_get(preprocessed, "real_time_snapshot",
                             "total_shares", default=1)
    pb = _safe_get(preprocessed, "real_time_snapshot", "pb_mrq", default=1)
    price = _safe_get(preprocessed, "real_time_snapshot", "price", default=0)
    equity_est = _safe_div(
        price * _safe_float(total_shares), _safe_float(pb), default=1)

    if equity_est > 0 and goodwill / equity_est > 0.30:
        trap_signals.append("商誉/净资产={:.0%}".format(goodwill / equity_est))

    # 检查ROE < WACC连续3年
    roe_series = _safe_get(valuation_data, "balance_data", "annual_series",
                           "roe", default=[])
    wacc_val = _safe_float(wacc)
    if len(roe_series) >= 3 and wacc_val > 0:
        recent_roe = [_safe_float(x) for x in roe_series[-3:]]
        if all(r < wacc_val for r in recent_roe):
            trap_signals.append(
                "ROE连续3年低于WACC({:.1f}%)".format(wacc_val))

    if trap_signals:
        return {
            "id": "F", "name": "低PB陷阱",
            "level": "warning",
            "detail": "PB处于历史低位(分位数{:.0%})，且{}".format(
                pb_percentile, "、".join(trap_signals)),
        }
    else:
        return {
            "id": "F", "name": "低PB陷阱",
            "level": "normal",
            "detail": "PB处于低位但无其他异常信号",
        }


def _detect_trap_g_high_dividend(preprocessed, valuation_data):
    """
    G. 高分红陷阱: 当前股息率 > 1.5x历史均值 AND
       (OCF/分红<1.2 OR 派息率>90% OR 举债维持分红)
    """
    current_yield = _safe_get(preprocessed, "real_time_snapshot",
                              "dividend_yield", default=0)
    dps_info = _safe_get(preprocessed, "dps_forecast", default={})
    ocf_coverage = _safe_float(
        _safe_get(dps_info, "ocf_coverage", default=2.0))

    if not current_yield or current_yield <= 0:
        return {
            "id": "G", "name": "高分红陷阱",
            "level": "normal",
            "detail": "当前股息率为零或不可用，跳过高分红陷阱检测",
        }

    # 粗估历史平均股息率
    div_records = _safe_get(valuation_data, "dividend_history",
                            "records", default=[])
    hist_yields = []
    if div_records:
        for rec in div_records:
            y = _safe_float(
                rec.get("yield", 0)) if isinstance(rec, dict) else 0
            if y > 0:
                hist_yields.append(y)
    hist_avg = (sum(hist_yields) / len(hist_yields)
                if hist_yields else current_yield)

    if current_yield <= 1.5 * hist_avg:
        return {
            "id": "G", "name": "高分红陷阱",
            "level": "normal",
            "detail": "当前股息率{:.1f}%，未显著高于历史均值".format(
                current_yield),
        }

    trap_signals = []
    if ocf_coverage < 1.2:
        trap_signals.append(
            "OCF/分红覆盖率仅{:.1f}".format(ocf_coverage))

    # 检查派息率(粗估)
    np_series = _safe_get(valuation_data, "income_data", "annual_series",
                          "net_profit", default=[])
    if div_records and np_series:
        latest_np = _safe_float(np_series[-1]) if np_series else 0
        latest_div_total = 0
        if div_records and isinstance(div_records[-1], dict):
            latest_div_total = _safe_float(
                div_records[-1].get("total_dividend", 0))
        if latest_np > 0 and latest_div_total > 0:
            payout = latest_div_total / latest_np
            if payout > 0.90:
                trap_signals.append(
                    "派息率{:.0%}超过90%".format(payout))

    # 检查是否举债维持分红
    ibd_series = _safe_get(valuation_data, "balance_data", "annual_series",
                           "interest_bearing_debt", default=[])
    if ibd_series and len(ibd_series) >= 2:
        latest_ibd = _safe_float(ibd_series[-1])
        prev_ibd = _safe_float(ibd_series[-2])
        if prev_ibd > 0 and latest_ibd > prev_ibd * 1.1 and div_records:
            trap_signals.append("有息负债上升同时维持高分红")

    if trap_signals:
        return {
            "id": "G", "name": "高分红陷阱",
            "level": "warning",
            "detail": "股息率{:.1f}%显著高于历史({:.1f}%)，且{}".format(
                current_yield, hist_avg, "、".join(trap_signals)),
        }
    else:
        return {
            "id": "G", "name": "高分红陷阱",
            "level": "attention",
            "detail": ("股息率{:.1f}%高于历史均值1.5倍({:.1f}%)，"
                       "但暂无明确异常").format(current_yield, hist_avg),
        }


def _detect_trap_h_structural(qualitative):
    """H. 结构性替代风险 (仅模式A)"""
    if qualitative is None:
        return None  # 模式B跳过

    risk = _safe_get(qualitative, "structural_substitution_risk", default=None)
    if risk and isinstance(risk, str) and risk.lower() in (
            "high", "significant"):
        return {
            "id": "H", "name": "结构性替代风险",
            "level": "warning",
            "detail": "定性分析指出存在显著的结构性替代风险",
        }
    elif risk and isinstance(risk, str) and risk.lower() in (
            "medium", "moderate"):
        return {
            "id": "H", "name": "结构性替代风险",
            "level": "attention",
            "detail": "定性分析指出存在中等程度的结构性替代风险",
        }
    return {
        "id": "H", "name": "结构性替代风险",
        "level": "normal",
        "detail": "未发现明显结构性替代风险",
    }


def _detect_trap_i_management(valuation_data, qualitative):
    """I. 管理层诚信信号"""
    mgmt = _safe_get(valuation_data, "management_signals", default={})
    signals = []

    audit_qualified = mgmt.get("audit_qualified", False)
    restatement = mgmt.get("restatement", False)
    insider_net_buy = _safe_float(mgmt.get("insider_net_buy", 0))

    if audit_qualified:
        signals.append("审计报告存在保留意见")
    if restatement:
        signals.append("存在财务重述")
    if insider_net_buy < -5.0:
        signals.append(
            "内部人大幅净减持{:.1f}%".format(insider_net_buy))

    # 模式A时从定性分析中补充
    if qualitative:
        integrity = _safe_get(qualitative, "management_integrity",
                              default=None)
        if (integrity and isinstance(integrity, str)
                and integrity.lower() in ("low", "poor")):
            signals.append("定性分析评估管理层诚信度较低")

    if len(signals) >= 2:
        return {
            "id": "I", "name": "管理层诚信",
            "level": "warning",
            "detail": "{}".format("、".join(signals)),
        }
    elif signals:
        return {
            "id": "I", "name": "管理层诚信",
            "level": "attention",
            "detail": "{}".format("、".join(signals)),
        }
    return {
        "id": "I", "name": "管理层诚信",
        "level": "normal",
        "detail": "未发现明显管理层诚信问题",
    }


def run_trap_detection(valuation_data, preprocessed, model_results, mode):
    """
    执行全部陷阱检测

    Returns:
        dict: 包含items, warning_count, attention_count, overall_level等
    """
    stage = _safe_get(preprocessed, "company_stage", "stage",
                      default="mature")
    wacc = _safe_get(preprocessed, "wacc", "wacc", default=8.0)
    qualitative = _safe_get(preprocessed, "qualitative_adjustments",
                            default=None)

    items = []

    # 财务陷阱 A-D
    items.append(_detect_trap_a_cashflow(valuation_data, model_results))
    items.append(_detect_trap_b_receivables(valuation_data))
    items.append(_detect_trap_c_inventory(valuation_data, stage))
    items.append(_detect_trap_d_debt(valuation_data))

    # 估值陷阱 E-G
    items.append(_detect_trap_e_low_pe(preprocessed, valuation_data))
    items.append(_detect_trap_f_low_pb(preprocessed, valuation_data, wacc))
    items.append(_detect_trap_g_high_dividend(preprocessed, valuation_data))

    # 商业陷阱 H-I (H仅模式A)
    trap_h = _detect_trap_h_structural(qualitative)
    if trap_h is not None:
        items.append(trap_h)

    items.append(_detect_trap_i_management(valuation_data, qualitative))

    # 统计
    warning_count = sum(1 for it in items if it["level"] == "warning")
    attention_count = sum(1 for it in items if it["level"] == "attention")

    # 综合等级
    if warning_count >= 2:
        overall = "red"
        overall_cn = "多个陷阱警告，风险较高"
    elif warning_count == 1:
        overall = "orange"
        overall_cn = "存在1个陷阱警告，需重点关注"
    elif attention_count >= 3:
        overall = "yellow"
        overall_cn = "多个关注信号，建议深入分析"
    else:
        overall = "green"
        overall_cn = "无明显陷阱信号"

    return {
        "items": items,
        "warning_count": warning_count,
        "attention_count": attention_count,
        "overall_level": overall,
        "overall_level_cn": overall_cn,
    }


# ============================================================================
# Section 6: 安全边际
# ============================================================================

# 按公司类型的基础安全边际
_BASE_MARGINS = {
    "high_dividend": (0.15, 0.20),   # 消费龙头/高分红
    "mature": (0.25, 0.30),          # 成熟稳定
    "asset_heavy": (0.30, 0.40),     # 重资产周期
    "growth": (0.40, 0.50),          # 成长型(利润不稳)
    "distressed": (0.50, 0.60),      # 困境型
    "diversified": (0.30, 0.40),     # 多元化
    "unprofitable": (0.40, 0.50),    # 亏损型
}


def compute_safety_margin(stage, divergence_cv, trap_warning_count,
                          qualitative, preprocessed):
    """
    计算安全边际

    基础边际 + 利率调整 + 分歧调整 + 陷阱调整 + 置信度调整
    最终结果限制在[10%, 60%]

    Args:
        stage: 公司阶段
        divergence_cv: 模型分歧CV值
        trap_warning_count: 陷阱警告数
        qualitative: 定性分析数据
        preprocessed: 预处理数据

    Returns:
        dict: 包含final_margin, breakdown, detail
    """
    # 基础安全边际(取区间中值)
    margin_range = _BASE_MARGINS.get(stage, (0.25, 0.30))
    base_margin = (margin_range[0] + margin_range[1]) / 2.0

    # 利率调整: (Rf - 3%) x 5%
    # Rf 从WACC数据中推断(简化: 使用ke的一部分)
    ke = _safe_get(preprocessed, "wacc", "ke", default=8.0)
    rf_est = max(2.0, _safe_float(ke) * 0.3)  # 粗估无风险利率
    interest_adj = (rf_est - 3.0) / 100.0 * 5.0  # 转换为小数
    interest_adj = round(interest_adj, 4)

    # 分歧调整
    divergence_adj = 0.0
    if divergence_cv > 0.30:
        divergence_adj = 0.10
    elif divergence_cv > 0.15:
        divergence_adj = 0.05

    # 陷阱调整
    trap_adj = 0.0
    if trap_warning_count >= 2:
        trap_adj = 0.15
    elif trap_warning_count == 1:
        trap_adj = 0.05

    # 置信度调整(仅模式A)
    confidence_adj = 0.0
    if qualitative is not None:
        confidence = _safe_get(qualitative, "research_confidence",
                               default="medium")
        if isinstance(confidence, str):
            if confidence.lower() in ("low",):
                confidence_adj = 0.10
            elif confidence.lower() in ("high",):
                # 最多降低至基础的80%
                confidence_adj = -0.05
                if base_margin + confidence_adj < base_margin * 0.80:
                    confidence_adj = -(base_margin * 0.20)

    # 最终安全边际
    final = base_margin + interest_adj + divergence_adj + trap_adj + confidence_adj
    final = _clamp(final, 0.10, 0.60)
    final = round(final, 4)

    # 生成说明
    detail_parts = ["基础{:.0%}".format(base_margin)]
    if interest_adj != 0:
        detail_parts.append("利率调整{:+.0%}".format(interest_adj))
    if divergence_adj != 0:
        detail_parts.append("分歧调整+{:.0%}".format(divergence_adj))
    if trap_adj != 0:
        detail_parts.append("陷阱调整+{:.0%}".format(trap_adj))
    if confidence_adj != 0:
        detail_parts.append("置信度调整{:+.0%}".format(confidence_adj))

    raw_total = (base_margin + interest_adj + divergence_adj
                 + trap_adj + confidence_adj)
    if raw_total != final:
        detail_parts.append("边界约束调整至{:.0%}".format(final))

    detail = " + ".join(detail_parts).replace(
        "+ -", "- ").replace("+ 边界", "，边界")

    return {
        "final_margin": final,
        "breakdown": {
            "base": round(base_margin, 4),
            "interest_rate_adj": interest_adj,
            "divergence_adj": divergence_adj,
            "trap_adj": trap_adj,
            "confidence_adj": confidence_adj,
        },
        "detail": detail,
    }


# ============================================================================
# Section 6.3: 买入区间构建
# ============================================================================

def build_buy_zones(weighted_base, weighted_optimistic,
                    safety_margin, current_price):
    """
    构建买入区间

    强买上限 = 内在价值 x (1 - 安全边际 - 10%)
    理想买入上限 = 内在价值 x (1 - 安全边际)
    合理价值中枢 = 基准情景加权值
    乐观价值 = 加权乐观值
    高估预警 = 乐观值 x 120%

    Args:
        weighted_base: 加权基准值
        weighted_optimistic: 加权乐观值
        safety_margin: 安全边际(0-1)
        current_price: 当前股价

    Returns:
        dict: 买入区间数据
    """
    intrinsic_value = weighted_base
    fair_value = weighted_base
    optimistic = weighted_optimistic

    strong_buy = round(intrinsic_value * (1 - safety_margin - 0.10), 2)
    ideal_buy = round(intrinsic_value * (1 - safety_margin), 2)
    overvaluation = round(optimistic * 1.20, 2)

    # 判断当前价位
    if current_price > overvaluation:
        position = "明显高估"
        position_detail = "当前价格显著高于乐观情景120%，估值泡沫风险"
    elif current_price > optimistic:
        position = "偏高估"
        position_detail = "当前价格高于乐观情景估值，存在回调风险"
    elif current_price > fair_value:
        position = "合理偏高"
        position_detail = "当前价格位于内在价值中枢与乐观情景之间"
    elif current_price > ideal_buy:
        position = "合理"
        position_detail = "当前价格位于理想买入区间与内在价值中枢之间"
    elif current_price > strong_buy:
        position = "合理偏低，进入关注区间"
        position_detail = "当前价格低于理想买入价，具备一定安全边际"
    else:
        position = "偏低估，进入积极关注区间"
        position_detail = "当前价格低于强买区间上限，安全边际充足"

    return {
        "strong_buy_ceiling": strong_buy,
        "ideal_buy_ceiling": ideal_buy,
        "fair_value_center": round(fair_value, 2),
        "optimistic_value": round(optimistic, 2),
        "overvaluation_warning": overvaluation,
        "current_price": current_price,
        "current_position": position,
        "current_position_detail": position_detail,
    }


# ============================================================================
# Section 8: 特殊情景
# ============================================================================

def detect_special_scenarios(preprocessed, valuation_data, qualitative,
                             scenario_probs, safety_margin_data):
    """
    检测特殊情景:
      - 并购进行中
      - 行业颠覆(模式A: 强制悲观>=35%)
      - 极端宏观(Rf月变化>50bp -> 所有边际+15%，DCF折现+100bp)

    Returns:
        list: 特殊情景列表
    """
    scenarios = []

    # 并购检测(从定性分析或管理层信号)
    if qualitative:
        ma_status = _safe_get(qualitative, "ma_in_progress", default=False)
        if ma_status:
            scenarios.append({
                "type": "ma_in_progress",
                "description": "并购交易进行中",
                "impact": "估值可能受并购溢价/折价影响，建议参考交易对价",
            })

    # 行业颠覆(模式A)
    if qualitative:
        disruption = _safe_get(qualitative, "industry_disruption",
                               default=None)
        if (disruption and isinstance(disruption, str)
                and disruption.lower() in (
                    "yes", "true", "significant", "high")):
            # 强制悲观 >= 35%
            current_pess = scenario_probs.get("pessimistic", 25)
            if current_pess < 35:
                scenario_probs["pessimistic"] = 35
                # 重新平衡
                remaining = 100 - 35
                old_base = scenario_probs.get("base", 50)
                old_opti = scenario_probs.get("optimistic", 25)
                old_sum = old_base + old_opti
                if old_sum > 0:
                    scenario_probs["base"] = round(
                        remaining * old_base / old_sum)
                    scenario_probs["optimistic"] = (
                        100 - 35 - scenario_probs["base"])
                scenario_probs["rationale"] += (
                    "; 行业颠覆调整悲观概率至35%")

            scenarios.append({
                "type": "industry_disruption",
                "description": "行业面临结构性颠覆风险",
                "impact": "悲观概率强制调整至>=35%",
            })

    # 极端宏观: 检测Rf月度变化
    macro_alert = _safe_get(preprocessed, "macro_alert", default=None)
    if macro_alert:
        rf_change = _safe_get(macro_alert, "rf_monthly_change_bp", default=0)
        if abs(_safe_float(rf_change)) > 50:
            scenarios.append({
                "type": "extreme_macro",
                "description": "无风险利率月度变化超过50bp",
                "impact": "所有安全边际+15%，DCF折现率+100bp",
                "rf_change_bp": rf_change,
            })
            # 调整安全边际
            if safety_margin_data:
                old_margin = safety_margin_data.get("final_margin", 0.25)
                new_margin = _clamp(old_margin + 0.15, 0.10, 0.60)
                safety_margin_data["final_margin"] = round(new_margin, 4)
                safety_margin_data["detail"] += "; 极端宏观调整+15%"
                safety_margin_data["breakdown"]["macro_adj"] = 0.15

    return scenarios


# ============================================================================
# 置信度评估
# ============================================================================

def _assess_confidence(mode, divergence, trap_detection,
                       available_model_count):
    """
    综合评估估值置信度

    Returns:
        (level, note) 元组
    """
    score = 50  # 基准分

    # 模式调整
    if mode == "A":
        score += 10
    else:
        score -= 10

    # 模型数量
    if available_model_count >= 5:
        score += 10
    elif available_model_count >= 3:
        score += 5
    elif available_model_count < 2:
        score -= 15

    # 分歧度
    cv = divergence.get("cv", 0)
    if cv < 0.15:
        score += 15
    elif cv < 0.30:
        score += 0
    else:
        score -= 15

    # 陷阱检测
    wc = trap_detection.get("warning_count", 0)
    ac = trap_detection.get("attention_count", 0)
    if wc >= 2:
        score -= 20
    elif wc == 1:
        score -= 10
    if ac >= 3:
        score -= 5

    # 判断等级
    notes = []
    if mode == "B":
        notes.append("模式B运行，缺少定性分析")
    if cv > 0.30:
        notes.append("模型间分歧显著")
    if wc > 0:
        notes.append("存在{}个陷阱警告".format(wc))
    if available_model_count < 3:
        notes.append("可用模型不足")

    if score >= 70:
        level = "高"
    elif score >= 40:
        level = "中"
    else:
        level = "低"

    note = "；".join(notes) if notes else "多模型一致，数据充分"

    return level, note


# ============================================================================
# 主流程
# ============================================================================

def integrate_valuation(valuation_data, preprocessed, model_results_data):
    """
    主估值整合函数

    Args:
        valuation_data: 估值原始数据
        preprocessed: 预处理后数据
        model_results_data: 模型估值结果

    Returns:
        dict: 完整的整合结果
    """
    # ---- 提取关键字段 ----
    meta = _safe_get(preprocessed, "meta", default={})
    code = meta.get("code", "unknown")
    name = meta.get("name", "unknown")
    mode = meta.get("mode", "B")

    stage = _safe_get(preprocessed, "company_stage", "stage",
                      default="mature")
    available_models = _safe_get(preprocessed, "available_models", default={})
    qualitative = _safe_get(preprocessed, "qualitative_adjustments",
                            default=None)
    current_price = _safe_float(
        _safe_get(preprocessed, "real_time_snapshot", "price", default=0))

    model_results = _safe_get(model_results_data, "model_results", default={})

    # ---- Step 1: 陷阱检测(需要先于概率计算) ----
    trap_detection = run_trap_detection(
        valuation_data, preprocessed, model_results, mode)
    warning_count = trap_detection["warning_count"]

    # ---- Step 2: 情景概率 ----
    scenario_probs = compute_scenario_probabilities(
        mode, qualitative, warning_count)

    # ---- Step 3: 模型权重 ----
    model_weight_result = compute_model_weights(
        stage, available_models, model_results)
    weight_note = model_weight_result.pop("note", "")
    model_weights = model_weight_result  # 剩余为模型->权重映射

    # ---- Step 4: 估值矩阵 ----
    valuation_matrix = build_valuation_matrix(
        model_weights, model_results, current_price)

    # ---- Step 5: 分歧分析 ----
    divergence = analyze_divergence(model_results, model_weights)

    # ---- Step 6: 安全边际 ----
    safety = compute_safety_margin(
        stage, divergence["cv"], warning_count, qualitative, preprocessed)

    # ---- Step 7: 特殊情景 ----
    special = detect_special_scenarios(
        preprocessed, valuation_data, qualitative, scenario_probs, safety)

    # ---- Step 8: 买入区间 ----
    weighted = valuation_matrix.get("weighted", {})
    buy_zones = build_buy_zones(
        weighted.get("base", 0),
        weighted.get("optimistic", 0),
        safety["final_margin"],
        current_price,
    )

    # ---- Step 9: 置信度评估 ----
    available_count = sum(
        1 for m in _ALL_MODELS
        if model_weights.get(m, 0) > 0 and m != "REVERSE"
    )
    confidence_level, confidence_note = _assess_confidence(
        mode, divergence, trap_detection, available_count)

    # ---- 收集全局警告 ----
    warnings = []
    if divergence["level"] == "significant_divergence":
        warnings.append(
            "模型间分歧显著(CV={:.1%})，估值结果仅供参考".format(
                divergence["cv"]))
    if trap_detection["overall_level"] in ("orange", "red"):
        warnings.append(
            "检测到陷阱信号({}个警告)，请审慎评估".format(warning_count))
    if current_price <= 0:
        warnings.append("当前股价不可用，买入区间分析可能不准确")
    if available_count < 2:
        warnings.append("有效估值模型不足2个，结果可靠性降低")

    # ---- 组装输出 ----
    output = {
        "meta": {
            "code": code,
            "name": name,
            "mode": mode,
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "scenario_probabilities": scenario_probs,
        "model_weights": {m: model_weights.get(m, 0) for m in _ALL_MODELS},
        "valuation_matrix": valuation_matrix,
        "divergence": divergence,
        "trap_detection": trap_detection,
        "safety_margin": safety,
        "buy_zones": buy_zones,
        "special_scenarios": special,
        "confidence_level": confidence_level,
        "confidence_note": confidence_note,
        "warnings": warnings,
    }

    # 将权重说明添加到model_weights中
    output["model_weights"]["note"] = weight_note

    return output


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="估值整合器 - 整合多模型估值结果并构建买入区间")
    parser.add_argument(
        "--data-file", required=True,
        help="估值原始数据文件路径 ({code}_valuation_data.json)")
    parser.add_argument(
        "--preprocessed-file", required=True,
        help="预处理数据文件路径 ({code}_preprocessed.json)")
    parser.add_argument(
        "--model-results-file", required=True,
        help="模型结果文件路径 ({code}_model_results.json)")
    parser.add_argument(
        "--output-dir", default=".",
        help="输出目录 (默认当前目录)")

    args = parser.parse_args()

    # 加载输入文件
    print("[INFO] 加载估值数据: {}".format(args.data_file))
    valuation_data = _load_json(args.data_file)

    print("[INFO] 加载预处理数据: {}".format(args.preprocessed_file))
    preprocessed = _load_json(args.preprocessed_file)

    print("[INFO] 加载模型结果: {}".format(args.model_results_file))
    model_results_data = _load_json(args.model_results_file)

    # 校验必要数据
    if not preprocessed:
        print("[ERROR] 预处理数据为空或加载失败", file=sys.stderr)
        sys.exit(1)
    if not model_results_data:
        print("[ERROR] 模型结果数据为空或加载失败", file=sys.stderr)
        sys.exit(1)

    # 执行整合
    print("[INFO] 开始估值整合...")
    result = integrate_valuation(
        valuation_data, preprocessed, model_results_data)

    # 输出结果
    code = _safe_get(preprocessed, "meta", "code", default="unknown")
    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(
        args.output_dir, "{}_integrated.json".format(code))

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("[INFO] 估值整合完成，结果已保存至: {}".format(output_path))

    # 打印摘要
    weighted = result.get("valuation_matrix", {}).get("weighted", {})
    buy_zones = result.get("buy_zones", {})
    margin = result.get("safety_margin", {})
    traps = result.get("trap_detection", {})

    print("\n" + "=" * 60)
    print("  估值整合摘要 - {} ({})".format(
        result["meta"]["name"], result["meta"]["code"]))
    print("=" * 60)
    print("  模式: {}".format(result["meta"]["mode"]))
    print("  当前价格: {:.2f}元".format(
        buy_zones.get("current_price", 0)))
    print("  加权估值: 悲观={:.2f} / 基准={:.2f} / 乐观={:.2f}".format(
        weighted.get("pessimistic", 0),
        weighted.get("base", 0),
        weighted.get("optimistic", 0)))
    print("  安全边际: {:.0%}".format(margin.get("final_margin", 0)))
    print("  买入区间: 强买<{:.2f} / 理想<{:.2f} / 合理={:.2f}".format(
        buy_zones.get("strong_buy_ceiling", 0),
        buy_zones.get("ideal_buy_ceiling", 0),
        buy_zones.get("fair_value_center", 0)))
    print("  当前定位: 【{}】".format(
        buy_zones.get("current_position", "N/A")))
    print("  陷阱检测: {} (警告{}个，关注{}个)".format(
        traps.get("overall_level_cn", "N/A"),
        traps.get("warning_count", 0),
        traps.get("attention_count", 0)))
    print("  置信度: {} - {}".format(
        result.get("confidence_level", "N/A"),
        result.get("confidence_note", "")))
    if result.get("warnings"):
        print("\n  警告:")
        for w in result["warnings"]:
            print("    - {}".format(w))
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
