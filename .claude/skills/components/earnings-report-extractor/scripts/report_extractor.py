#!/usr/bin/env python3
"""
A股财报PDF结构化提取工具。

从巨潮资讯下载年报PDF，智能识别章节结构，完整无损提取关键章节内容。
侧重非财务数据类内容：管理层讨论与分析、风险因素、董事长致辞、业务描述、审计报告等。

依赖: pdfplumber (pip install pdfplumber)

用法:
    python report_extractor.py --code 300014                        # 下载并提取最新年报
    python report_extractor.py --code 300014 --year 2024            # 指定年份
    python report_extractor.py --pdf /path/to/report.pdf            # 直接处理本地PDF
    python report_extractor.py --code 300014 --list-sections        # 仅列出章节结构
    python report_extractor.py --code 300014 --sections mda,risk    # 仅提取指定类别
    python report_extractor.py --code 300014 --all-sections         # 提取全部章节（含财务报表）
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

# ============================================================
# 模块零: 依赖检测与环境验证
# ============================================================

VERSION = "1.0.0"

def check_dependencies():
    """检测必要依赖，不满足时打印安装命令并退出。"""
    missing = []
    try:
        import pdfplumber
    except ImportError:
        missing.append("pdfplumber")

    if missing:
        print("=" * 60)
        print("错误: 缺少必要的 Python 依赖包")
        print("=" * 60)
        for pkg in missing:
            print(f"  - {pkg}")
        print()
        print("请安装依赖:")
        print(f"  pip install {' '.join(missing)}")
        print()
        print("如果使用 conda 环境:")
        print(f"  conda run -n <env_name> pip install {' '.join(missing)}")
        print("=" * 60)
        sys.exit(2)

    if sys.version_info < (3, 6):
        print(f"错误: 需要 Python 3.6+，当前版本: {sys.version}")
        sys.exit(2)


check_dependencies()

import pdfplumber

# ============================================================
# 常量与配置
# ============================================================

FETCH_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 180
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CNINFO_PLATE_MAP = {
    "00": "szse", "30": "szse",
    "60": "sse", "68": "sse",
    "83": "bse", "43": "bse",
}

# 章节号的中文数字到阿拉伯数字映射
CN_NUM_MAP = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100,
}

# --- 章节分类与优先级 ---
SECTION_CLASSIFICATION = {
    "mda": {
        "name": "管理层讨论与分析",
        "keywords": [
            "管理层讨论与分析", "经营情况讨论与分析", "经营情况的讨论与分析",
            "董事会报告", "报告期内公司经营情况",
        ],
        "section_keywords": ["管理层讨论", "经营情况讨论", "董事会报告"],
        "priority": 1,
    },
    "risk": {
        "name": "风险因素",
        "keywords": [
            "风险因素", "公司面临的风险", "可能面对的风险",
            "重大风险", "风险提示", "公司面临的风险和应对措施",
        ],
        "section_keywords": ["风险因素", "风险"],
        "priority": 1,
    },
    "chairman_letter": {
        "name": "董事长致辞",
        "keywords": [
            "致股东", "董事长致辞", "致投资者", "董事长报告",
            "致全体股东", "致公司全体股东",
        ],
        "section_keywords": ["致辞", "致股东"],
        "priority": 1,
    },
    "business": {
        "name": "业务与产品描述",
        "keywords": [
            "公司业务", "业务概要", "主营业务", "核心竞争力",
            "公司所从事的主要业务", "业务概述", "经营情况",
        ],
        "section_keywords": ["业务", "主营"],
        "priority": 1,
    },
    "audit": {
        "name": "审计报告",
        "keywords": [
            "审计报告", "审计意见", "注册会计师", "审计师报告",
        ],
        "section_keywords": ["审计报告", "审计意见"],
        "priority": 1,
    },
    "company_intro": {
        "name": "公司简介与主要财务指标",
        "keywords": [
            "公司简介", "基本情况", "公司概况", "公司简介和主要财务指标",
        ],
        "section_keywords": ["公司简介", "基本情况"],
        "priority": 2,
    },
    "important_tips": {
        "name": "重要提示",
        "keywords": [
            "重要提示", "目录和释义", "重要提示、目录和释义",
        ],
        "section_keywords": ["重要提示"],
        "priority": 2,
    },
    "governance": {
        "name": "公司治理",
        "keywords": ["公司治理", "治理结构", "公司治理结构"],
        "section_keywords": ["公司治理"],
        "priority": 2,
    },
    "important_matters": {
        "name": "重要事项",
        "keywords": ["重要事项", "重大事项"],
        "section_keywords": ["重要事项", "重大事项"],
        "priority": 2,
    },
    "esg": {
        "name": "环境与社会责任",
        "keywords": [
            "环境和社会责任", "社会责任", "ESG", "可持续发展",
            "环境保护", "环境信息",
        ],
        "section_keywords": ["社会责任", "环境"],
        "priority": 2,
    },
    "shareholders": {
        "name": "股份变动及股东情况",
        "keywords": ["股份变动", "股东情况", "股本变动", "股份变动及股东情况"],
        "section_keywords": ["股份变动", "股东"],
        "priority": 2,
    },
    "directors_supervisors": {
        "name": "董事、监事、高管情况",
        "keywords": [
            "董事、监事、高级管理人员", "董监高",
            "董事、监事、高级管理人员和员工情况",
        ],
        "section_keywords": ["董事", "监事"],
        "priority": 2,
    },
    "preferred_stock": {
        "name": "优先股相关情况",
        "keywords": ["优先股", "优先股相关情况"],
        "section_keywords": ["优先股"],
        "priority": 3,
    },
    "bonds": {
        "name": "债券相关情况",
        "keywords": ["债券相关", "公司债券", "债券"],
        "section_keywords": ["债券"],
        "priority": 3,
    },
    "financial_notes": {
        "name": "财务报表附注",
        "keywords": ["财务报表附注", "报表附注", "会计政策"],
        "section_keywords": ["附注"],
        "priority": 3,
    },
    "financial_statements": {
        "name": "财务报告",
        "keywords": [
            "财务报告", "财务报表", "合并资产负债表",
            "合并利润表", "合并现金流量表",
        ],
        "section_keywords": ["财务报告", "财务报表"],
        "priority": 3,
    },
    "other_info": {
        "name": "备查文件",
        "keywords": ["备查文件", "备查文件目录"],
        "section_keywords": ["备查文件"],
        "priority": 3,
    },
}


# ============================================================
# 数据类
# ============================================================

@dataclass
class PageContent:
    """一页的提取结果。"""
    page_num: int
    text: str
    lines: List[str] = field(default_factory=list)
    tables: List[List[List[str]]] = field(default_factory=list)
    char_count: int = 0
    header_chars: List[Dict] = field(default_factory=list)


@dataclass
class Section:
    """检测到的一个章节。"""
    title: str
    level: int  # 1=节, 2=子章节, 3=小标题
    category: str  # 分类标签，如 "mda", "risk", ""
    start_page: int
    start_line: int
    end_page: int = -1
    end_line: int = -1
    section_num: str = ""  # 如 "第三节"
    priority: int = 0
    char_count: int = 0
    subsections: List["Section"] = field(default_factory=list)


# ============================================================
# 模块一: HTTP 工具与 PDF 下载
# ============================================================

def _create_ssl_context():
    """创建 SSL 上下文（跳过证书验证）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get_json(url, headers=None, timeout=FETCH_TIMEOUT):
    """GET 请求，返回 JSON。"""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, data, headers=None, timeout=FETCH_TIMEOUT):
    """POST 请求，返回 JSON。"""
    hdrs = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)
    encoded = (
        urllib.parse.urlencode(data).encode("utf-8")
        if isinstance(data, dict)
        else data.encode("utf-8")
    )
    req = urllib.request.Request(url, data=encoded, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_download(url, save_path, headers=None, timeout=DOWNLOAD_TIMEOUT):
    """下载文件到本地。"""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        downloaded = 0
        with open(save_path, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  下载进度: {pct}% ({downloaded // 1024}KB/{total // 1024}KB)", end="", flush=True)
        if total:
            print()
    return os.path.getsize(save_path)


def cninfo_get_org_id(code):
    """获取巨潮资讯的 orgId。"""
    url = "http://www.cninfo.com.cn/new/information/topSearch/query"
    headers = {
        "Origin": "http://www.cninfo.com.cn",
        "Referer": "http://www.cninfo.com.cn/",
    }
    data = {"keyWord": code, "maxSecNum": "10", "maxListNum": "5"}
    result = http_post_json(url, data, headers=headers)
    if isinstance(result, list) and result:
        return result[0].get("orgId", ""), result[0].get("zwjc", "")
    return "", ""


def cninfo_query_reports(code, org_id, category, page_size=30):
    """查询巨潮资讯公告列表。"""
    plate = CNINFO_PLATE_MAP.get(code[:2], "szse")
    stock_val = f"{code},{org_id}" if org_id else f"{code},"
    headers = {
        "Origin": "http://www.cninfo.com.cn",
        "Referer": "http://www.cninfo.com.cn/",
    }
    params = {
        "pageNum": "1",
        "pageSize": str(page_size),
        "column": plate,
        "tabName": "fulltext",
        "plate": "",
        "stock": stock_val,
        "searchkey": "",
        "secid": "",
        "category": category,
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    result = http_post_json(
        "http://www.cninfo.com.cn/new/hisAnnouncement/query",
        params,
        headers=headers,
    )
    return result.get("announcements") or []


def cninfo_filter_main_report(announcements, year=None):
    """过滤出正式财报，排除摘要、修订等。"""
    skip_keywords = [
        "摘要", "英文", "修订", "补充", "更正", "取消", "延期",
        "内部控制", "社会责任", "可持续发展",
        "独立董事", "监事会", "董事会决议", "股东大会",
    ]
    results = []
    for ann in announcements:
        title = re.sub(r"<.*?>", "", ann.get("announcementTitle", ""))
        if any(kw in title for kw in skip_keywords):
            continue
        if year and str(year) not in title:
            continue
        results.append({
            "title": title,
            "adjunctUrl": ann.get("adjunctUrl", ""),
            "announcementTime": ann.get("announcementTime", 0),
        })
    return results


def download_report_pdf(code, output_dir, year=None, report_type="annual"):
    """
    从巨潮资讯下载财报 PDF。
    report_type: annual/half/q1/q3
    返回 (pdf_path, error_msg)
    """
    category_map = {
        "annual": "category_ndbg_szsh",
        "half": "category_bndbg_szsh",
        "q1": "category_yjdbg_szsh",
        "q3": "category_sjdbg_szsh",
    }
    type_name_map = {
        "annual": "年报", "half": "半年报", "q1": "一季报", "q3": "三季报",
    }
    category = category_map.get(report_type, "category_ndbg_szsh")
    type_name = type_name_map.get(report_type, "年报")

    print(f"  查询巨潮资讯 ({type_name})...")
    try:
        org_id, sec_name = cninfo_get_org_id(code)
    except Exception as e:
        return None, f"查询 orgId 失败: {e}"

    if not org_id:
        return None, "获取 orgId 失败，请检查股票代码是否正确"

    print(f"  公司: {sec_name} ({code})")

    try:
        announcements = cninfo_query_reports(code, org_id, category)
    except Exception as e:
        return None, f"查询公告列表失败: {e}"

    reports = cninfo_filter_main_report(announcements, year=year)

    if not reports:
        return None, f"未找到 {year or '最新'} {type_name}"

    report = reports[0]
    adjunct_url = report["adjunctUrl"]
    if not adjunct_url:
        return None, "无下载链接"

    safe_title = re.sub(r'[<>:"/\\|?*\s]+', "_", report["title"]).strip("_")[:80]
    filename = f"{code}_{safe_title}.pdf"
    save_path = os.path.join(output_dir, filename)

    if os.path.exists(save_path):
        size_mb = os.path.getsize(save_path) / 1024 / 1024
        print(f"  文件已存在: {filename} ({size_mb:.1f} MB)")
        return save_path, None

    url = "http://static.cninfo.com.cn/" + adjunct_url
    print(f"  下载: {report['title']}")
    try:
        size = http_download(url, save_path, headers={"Referer": "http://www.cninfo.com.cn/"})
        print(f"  已保存: {filename} ({size / 1024 / 1024:.1f} MB)")
        return save_path, None
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        return None, f"下载失败: {e}"


# ============================================================
# 模块二: PDF 全文提取引擎
# ============================================================

def extract_full_text(pdf_path: str) -> Tuple[List[PageContent], Dict[str, Any]]:
    """
    提取 PDF 全文。

    返回:
        pages: 每页的提取结果列表
        meta: PDF元信息 (总页数、总字符数、是否疑似扫描件等)
    """
    pages = []
    total_chars = 0
    low_text_pages = 0

    print(f"  正在提取 PDF 文本...")
    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)
        print(f"  总页数: {num_pages}")

        for i, page in enumerate(pdf.pages):
            if (i + 1) % 50 == 0 or i == 0 or i == num_pages - 1:
                print(f"\r  处理页面: {i + 1}/{num_pages}", end="", flush=True)

            # 提取文本（保持布局）
            try:
                text = page.extract_text(layout=True) or ""
            except Exception:
                text = page.extract_text() or ""

            lines = text.split("\n") if text else []
            char_count = len(text.replace(" ", "").replace("\n", ""))

            # 提取表格
            tables = []
            try:
                raw_tables = page.extract_tables() or []
                for tbl in raw_tables:
                    if tbl:
                        tables.append(tbl)
            except Exception:
                pass

            # 提取字符级信息（用于标题检测）
            header_chars = []
            try:
                chars = page.chars or []
                # 收集大字体字符信息（用于判断标题）
                if chars:
                    sizes = [c.get("size", 0) for c in chars if c.get("size", 0) > 0]
                    if sizes:
                        median_size = sorted(sizes)[len(sizes) // 2]
                        # 比正文大20%以上的认为是标题字体
                        threshold = median_size * 1.2
                        current_line_chars = []
                        current_top = -1
                        for c in chars:
                            c_top = round(c.get("top", 0), 1)
                            c_size = c.get("size", 0)
                            if c_size >= threshold:
                                if current_top < 0 or abs(c_top - current_top) < 3:
                                    current_line_chars.append(c)
                                    current_top = c_top
                                else:
                                    if current_line_chars:
                                        text_str = "".join(
                                            ch.get("text", "") for ch in current_line_chars
                                        )
                                        if text_str.strip():
                                            header_chars.append({
                                                "text": text_str.strip(),
                                                "size": current_line_chars[0].get("size", 0),
                                                "top": current_top,
                                            })
                                    current_line_chars = [c]
                                    current_top = c_top
                        if current_line_chars:
                            text_str = "".join(
                                ch.get("text", "") for ch in current_line_chars
                            )
                            if text_str.strip():
                                header_chars.append({
                                    "text": text_str.strip(),
                                    "size": current_line_chars[0].get("size", 0),
                                    "top": current_top,
                                })
            except Exception:
                pass

            if char_count < 20 and not tables:
                low_text_pages += 1

            total_chars += char_count
            pages.append(PageContent(
                page_num=i + 1,
                text=text,
                lines=lines,
                tables=tables,
                char_count=char_count,
                header_chars=header_chars,
            ))

        print()

    is_scanned = low_text_pages > num_pages * 0.5
    if is_scanned:
        print(f"  警告: 超过50%的页面文字极少({low_text_pages}/{num_pages})，可能是扫描件")
        print(f"  扫描件无法直接提取文本，建议使用OCR工具预处理后再使用本工具")

    meta = {
        "total_pages": num_pages,
        "total_chars": total_chars,
        "low_text_pages": low_text_pages,
        "is_scanned": is_scanned,
        "pdf_path": pdf_path,
        "pdf_filename": os.path.basename(pdf_path),
    }

    print(f"  提取完成: {total_chars} 字符, {num_pages} 页")
    return pages, meta


def table_to_markdown(table: List[List[str]]) -> str:
    """将表格数据转为 Markdown 表格。"""
    if not table or not table[0]:
        return ""

    # 清理单元格
    cleaned = []
    for row in table:
        cleaned_row = []
        for cell in row:
            cell_text = str(cell).strip() if cell else ""
            cell_text = cell_text.replace("|", "\\|").replace("\n", " ")
            cleaned_row.append(cell_text)
        cleaned.append(cleaned_row)

    # 统一列数
    max_cols = max(len(row) for row in cleaned)
    for row in cleaned:
        while len(row) < max_cols:
            row.append("")

    lines = []
    # 表头
    lines.append("| " + " | ".join(cleaned[0]) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    # 数据行
    for row in cleaned[1:]:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


# ============================================================
# 模块三: 章节智能检测引擎
# ============================================================

# Level 1: 标准 "第X节" 匹配
SECTION_NUM_PATTERN = re.compile(
    r"^[　\s]*第[一二三四五六七八九十百零〇]+节[　\s]+(.+?)$",
    re.MULTILINE,
)

# Level 2: 子章节/特殊章节关键词
# 注意：这些模式必须严格匹配独立标题行，避免匹配段落内文本
SUB_SECTION_PATTERNS = {
    "chairman_letter": [
        # "致股东" 必须在行首或仅有少量前缀，且不能是 "导致股东" 等
        re.compile(r"^[　\s]*致[全体]?股东[书函信]?[　\s]*$"),
        re.compile(r"^[　\s]*董事长致辞[　\s]*$"),
        re.compile(r"^[　\s]*致投资者[　\s]*$"),
    ],
    "audit": [
        # 严格匹配独立的"审计报告"标题行，排除 "内部控制审计报告" 等
        re.compile(r"^[　\s]*(?!.*内部控制)审计报告[　\s]*$"),
    ],
}

# Level 3: 带编号的二级标题
NUMBERED_TITLE_PATTERN = re.compile(
    r"^[　\s]*[（(]?[一二三四五六七八九十]+[)）、][　\s]*(.+?)$",
    re.MULTILINE,
)


def _cn_num_to_int(cn_str: str) -> int:
    """中文数字转阿拉伯数字。"""
    if not cn_str:
        return 0
    total = 0
    current = 0
    for ch in cn_str:
        val = CN_NUM_MAP.get(ch, -1)
        if val < 0:
            continue
        if val == 10:
            if current == 0:
                current = 1
            total += current * 10
            current = 0
        elif val == 100:
            if current == 0:
                current = 1
            total += current * 100
            current = 0
        else:
            current = val
    total += current
    return total


def _is_header_or_footer(text: str, all_pages_lines: List[List[str]], page_idx: int) -> bool:
    """判断某行是否是页眉/页脚（在多页中反复出现）。"""
    text_clean = text.strip()
    if not text_clean or len(text_clean) < 2:
        return True

    # 纯数字（页码）
    if re.match(r"^\d+$", text_clean):
        return True

    # 检查是否在多个页面的相似位置出现
    count = 0
    check_pages = min(20, len(all_pages_lines))
    for i in range(check_pages):
        if i == page_idx:
            continue
        page_lines = all_pages_lines[i]
        # 检查页面前3行和后3行
        check_lines = page_lines[:3] + page_lines[-3:]
        for line in check_lines:
            if line.strip() == text_clean:
                count += 1
                break
    return count >= 3


def _classify_section(title: str) -> Tuple[str, int]:
    """
    根据标题文本判断属于哪个分类。
    返回 (category_key, priority)。
    支持复合标题（如"公司治理、环境和社会"同时匹配多个关键词）。
    """
    title_clean = title.strip()
    # 首先尝试精确匹配
    for cat_key, cat_info in SECTION_CLASSIFICATION.items():
        for kw in cat_info["keywords"]:
            if kw in title_clean:
                return cat_key, cat_info["priority"]
    # 再尝试章节级关键词匹配（更宽松）
    for cat_key, cat_info in SECTION_CLASSIFICATION.items():
        for kw in cat_info.get("section_keywords", []):
            if kw in title_clean:
                return cat_key, cat_info["priority"]
    return "", 0


def detect_sections(pages: List[PageContent]) -> List[Section]:
    """
    智能检测 PDF 中的所有章节。

    三级检测策略:
    1. Level 1: "第X节 ..." 标准节号
    2. Level 2: 特殊关键词子章节（如"董事长致辞"）
    3. Level 3: 带编号的二级标题（如"一、经营情况"）

    返回检测到的章节列表（按出现顺序排列）。
    """
    sections = []
    all_lines = [p.lines for p in pages]

    # 收集所有大字体文本信息，辅助判断
    large_font_texts = {}
    for p in pages:
        for hc in p.header_chars:
            key = (p.page_num, hc["text"])
            large_font_texts[key] = hc["size"]

    # === Level 1: "第X节" 标准匹配 ===
    for page in pages:
        for line_idx, line in enumerate(page.lines):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            match = re.match(
                r"^[　\s]*第([一二三四五六七八九十百零〇]+)节[　\s]+(.+?)$",
                line_stripped,
            )
            if not match:
                continue

            # 验证：不是页眉/页脚
            if _is_header_or_footer(line_stripped, all_lines, page.page_num - 1):
                continue

            # 验证：行较短（真正标题通常不超过50字符）
            if len(line_stripped) > 60:
                continue

            cn_num = match.group(1)
            rest = match.group(2).strip()
            num_int = _cn_num_to_int(cn_num)
            section_num = f"第{cn_num}节"
            full_title = f"{section_num} {rest}"

            category, priority = _classify_section(full_title)

            sections.append(Section(
                title=full_title,
                level=1,
                category=category,
                start_page=page.page_num,
                start_line=line_idx,
                section_num=section_num,
                priority=priority,
            ))

    # === Level 2: 特殊关键词子章节 ===
    # 这些模式用于检测不以"第X节"开头的独立标题（如"致股东"、"审计报告"）
    # 必须是短行、独立标题，不能是段落内的文本片段
    for page in pages:
        for line_idx, line in enumerate(page.lines):
            line_stripped = line.strip()
            # 严格限制：标题行必须短（<= 30字符），且非空
            if not line_stripped or len(line_stripped) > 30:
                continue

            for cat_key, patterns in SUB_SECTION_PATTERNS.items():
                matched = False
                for pat in patterns:
                    if pat.match(line_stripped):  # 使用 match 而非 search，更严格
                        matched = True
                        break
                if not matched:
                    continue

                # 检查是否已被 Level 1 覆盖
                already = False
                for s in sections:
                    if (s.start_page == page.page_num
                            and abs(s.start_line - line_idx) < 3):
                        already = True
                        break
                if already:
                    break

                # 验证：不是页眉/页脚
                if _is_header_or_footer(line_stripped, all_lines, page.page_num - 1):
                    break

                cat_info = SECTION_CLASSIFICATION.get(cat_key, {})
                sections.append(Section(
                    title=line_stripped,
                    level=2,
                    category=cat_key,
                    start_page=page.page_num,
                    start_line=line_idx,
                    priority=cat_info.get("priority", 2),
                ))
                break

    # 按出现顺序排序
    sections.sort(key=lambda s: (s.start_page, s.start_line))

    # 去重：同一位置只保留最高级别的
    deduped = []
    for s in sections:
        skip = False
        for existing in deduped:
            if (existing.start_page == s.start_page
                    and abs(existing.start_line - s.start_line) < 5):
                # 保留 level 更低（更高级别）的
                if s.level < existing.level:
                    deduped.remove(existing)
                else:
                    skip = True
                break
        if not skip:
            deduped.append(s)
    sections = deduped

    # 计算每个章节的结束位置（到下一个同级或更高级章节开始之前）
    for i, s in enumerate(sections):
        if i + 1 < len(sections):
            next_s = sections[i + 1]
            s.end_page = next_s.start_page
            s.end_line = next_s.start_line - 1
            if s.end_line < 0:
                s.end_page -= 1
                if s.end_page >= 1 and s.end_page <= len(pages):
                    s.end_line = len(pages[s.end_page - 1].lines) - 1
                else:
                    s.end_line = 0
        else:
            # 最后一个章节到文档末尾
            s.end_page = len(pages)
            s.end_line = len(pages[-1].lines) - 1 if pages else 0

    # 计算每个章节的字符数
    for s in sections:
        char_count = 0
        for page_num in range(s.start_page, s.end_page + 1):
            if page_num < 1 or page_num > len(pages):
                continue
            page = pages[page_num - 1]
            if page_num == s.start_page and page_num == s.end_page:
                for line_idx in range(s.start_line, min(s.end_line + 1, len(page.lines))):
                    char_count += len(page.lines[line_idx].strip())
            elif page_num == s.start_page:
                for line_idx in range(s.start_line, len(page.lines)):
                    char_count += len(page.lines[line_idx].strip())
            elif page_num == s.end_page:
                for line_idx in range(0, min(s.end_line + 1, len(page.lines))):
                    char_count += len(page.lines[line_idx].strip())
            else:
                char_count += page.char_count
        s.char_count = char_count

    return sections


# ============================================================
# 模块四: 内容提取与存储
# ============================================================

def extract_section_content(
    pages: List[PageContent],
    section: Section,
    include_tables: bool = True,
) -> str:
    """
    提取指定章节的完整内容。
    - 按行级别精确提取
    - 插入 <!-- page:N --> 标记
    - 保留表格（转为 Markdown）
    - 零修改原文
    """
    content_parts = []

    for page_num in range(section.start_page, section.end_page + 1):
        if page_num < 1 or page_num > len(pages):
            continue
        page = pages[page_num - 1]

        # 页码标记
        content_parts.append(f"\n<!-- page:{page_num} -->\n")

        # 确定该页需要提取的行范围
        if page_num == section.start_page and page_num == section.end_page:
            start_ln = section.start_line
            end_ln = section.end_line
        elif page_num == section.start_page:
            start_ln = section.start_line
            end_ln = len(page.lines) - 1
        elif page_num == section.end_page:
            start_ln = 0
            end_ln = section.end_line
        else:
            start_ln = 0
            end_ln = len(page.lines) - 1

        # 提取行
        for line_idx in range(start_ln, min(end_ln + 1, len(page.lines))):
            content_parts.append(page.lines[line_idx])

        # 附加该页的表格
        if include_tables and page.tables:
            for tbl_idx, tbl in enumerate(page.tables):
                md_table = table_to_markdown(tbl)
                if md_table:
                    content_parts.append(f"\n<!-- table:{page_num}-{tbl_idx + 1} -->\n")
                    content_parts.append(md_table)
                    content_parts.append("")

    return "\n".join(content_parts)


def save_section_file(
    content: str,
    section: Section,
    output_dir: str,
    code: str,
    company_name: str,
    pdf_filename: str,
    report_year: str = "",
    report_type: str = "annual",
) -> str:
    """保存章节内容为 Markdown 文件。"""
    # 文件名：避免 section_num 和 title 重复
    if section.section_num and section.title.startswith(section.section_num):
        # 标题已包含节号，直接用标题
        safe_title = re.sub(r'[<>:"/\\|?*\s　]+', "_", section.title).strip("_")[:60]
        filename = f"{code}_{safe_title}.md"
    elif section.section_num:
        safe_title = re.sub(r'[<>:"/\\|?*\s　]+', "_", section.title).strip("_")[:50]
        filename = f"{code}_{section.section_num}_{safe_title}.md"
    else:
        safe_title = re.sub(r'[<>:"/\\|?*\s　]+', "_", section.title).strip("_")[:60]
        filename = f"{code}_{safe_title}.md"
    filepath = os.path.join(output_dir, filename)

    type_name_map = {
        "annual": "年报", "half": "半年报", "q1": "一季报", "q3": "三季报",
    }
    report_type_cn = type_name_map.get(report_type, "年报")

    # YAML frontmatter
    header = f"""---
source: {pdf_filename}
company: {company_name}
stock_code: {code}
report_year: {report_year}
report_type: {report_type_cn}
section_title: {section.title}
section_category: {section.category}
page_range: {section.start_page}-{section.end_page}
total_chars: {section.char_count}
extracted_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
extractor_version: {VERSION}
---

# {section.title}

"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(content)

    return filepath


def save_extraction_summary(
    sections: List[Section],
    extracted_sections: List[Section],
    output_dir: str,
    code: str,
    company_name: str,
    pdf_meta: Dict,
    report_year: str = "",
) -> str:
    """保存提取汇总索引文件。"""
    filepath = os.path.join(output_dir, f"{code}_提取汇总.md")

    lines = []
    lines.append(f"# {company_name}({code}) 年报提取汇总\n")
    lines.append(f"## 源文件信息\n")
    lines.append(f"- **PDF文件**: {pdf_meta.get('pdf_filename', '')}")
    lines.append(f"- **总页数**: {pdf_meta.get('total_pages', 0)}")
    lines.append(f"- **总字符数**: {pdf_meta.get('total_chars', 0):,}")
    lines.append(f"- **报告年份**: {report_year or '未指定'}")
    if pdf_meta.get("is_scanned"):
        lines.append(f"- **警告**: 疑似扫描件，文字提取可能不完整")
    lines.append(f"- **提取时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    lines.append(f"## 检测到的全部章节 ({len(sections)} 个)\n")
    lines.append("| 序号 | 章节标题 | 级别 | 分类 | 页码范围 | 字符数 | 优先级 |")
    lines.append("|------|----------|------|------|----------|--------|--------|")
    for i, s in enumerate(sections, 1):
        level_name = {1: "节", 2: "子章节", 3: "小标题"}.get(s.level, str(s.level))
        cat_name = SECTION_CLASSIFICATION.get(s.category, {}).get("name", s.category or "-")
        pri = f"P{s.priority}" if s.priority > 0 else "-"
        lines.append(
            f"| {i} | {s.title} | {level_name} | {cat_name} | "
            f"{s.start_page}-{s.end_page} | {s.char_count:,} | {pri} |"
        )
    lines.append("")

    lines.append(f"## 已提取章节 ({len(extracted_sections)} 个)\n")
    total_extracted_chars = 0
    for s in extracted_sections:
        if s.section_num and s.title.startswith(s.section_num):
            safe_title = re.sub(r'[<>:"/\\|?*\s　]+', "_", s.title).strip("_")[:60]
            filename = f"{code}_{safe_title}.md"
        elif s.section_num:
            safe_title = re.sub(r'[<>:"/\\|?*\s　]+', "_", s.title).strip("_")[:50]
            filename = f"{code}_{s.section_num}_{safe_title}.md"
        else:
            safe_title = re.sub(r'[<>:"/\\|?*\s　]+', "_", s.title).strip("_")[:60]
            filename = f"{code}_{safe_title}.md"
        lines.append(f"- [{s.title}]({filename}) — 第{s.start_page}-{s.end_page}页, {s.char_count:,}字符")
        total_extracted_chars += s.char_count
    lines.append("")

    lines.append("## 提取统计\n")
    total_chars = pdf_meta.get("total_chars", 1)
    coverage = total_extracted_chars / total_chars * 100 if total_chars > 0 else 0
    lines.append(f"- **已提取字符数**: {total_extracted_chars:,}")
    lines.append(f"- **文档总字符数**: {total_chars:,}")
    lines.append(f"- **覆盖率**: {coverage:.1f}%")
    lines.append("")

    # 按类别统计
    cat_stats = {}
    for s in extracted_sections:
        cat = s.category or "未分类"
        cat_stats[cat] = cat_stats.get(cat, 0) + s.char_count
    if cat_stats:
        lines.append("### 各类别字符数\n")
        lines.append("| 类别 | 字符数 | 占比 |")
        lines.append("|------|--------|------|")
        for cat, chars in sorted(cat_stats.items(), key=lambda x: -x[1]):
            cat_name = SECTION_CLASSIFICATION.get(cat, {}).get("name", cat)
            pct = chars / total_extracted_chars * 100 if total_extracted_chars > 0 else 0
            lines.append(f"| {cat_name} | {chars:,} | {pct:.1f}% |")
        lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


def save_structure_json(
    sections: List[Section],
    output_dir: str,
    code: str,
    company_name: str,
    pdf_meta: Dict,
    report_year: str = "",
) -> str:
    """保存结构化章节树为 JSON。"""
    filepath = os.path.join(output_dir, f"{code}_structure.json")

    data = {
        "code": code,
        "company": company_name,
        "report_year": report_year,
        "pdf": {
            "filename": pdf_meta.get("pdf_filename", ""),
            "total_pages": pdf_meta.get("total_pages", 0),
            "total_chars": pdf_meta.get("total_chars", 0),
            "is_scanned": pdf_meta.get("is_scanned", False),
        },
        "sections": [],
        "extracted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "extractor_version": VERSION,
    }

    for s in sections:
        section_data = {
            "title": s.title,
            "level": s.level,
            "category": s.category,
            "category_name": SECTION_CLASSIFICATION.get(s.category, {}).get("name", ""),
            "section_num": s.section_num,
            "priority": s.priority,
            "start_page": s.start_page,
            "end_page": s.end_page,
            "char_count": s.char_count,
        }
        data["sections"].append(section_data)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return filepath


# ============================================================
# 模块五: 主流程
# ============================================================

def infer_report_year(pdf_path: str, sections: List[Section], pages: List[PageContent]) -> str:
    """从文件名或内容推断报告年份。"""
    basename = os.path.basename(pdf_path)
    # 尝试从文件名提取年份
    m = re.search(r"20[12]\d", basename)
    if m:
        return m.group()

    # 尝试从前几页文本提取
    for page in pages[:5]:
        m = re.search(r"(20[12]\d)\s*年.*(?:年度|年报)", page.text)
        if m:
            return m.group(1)
        m = re.search(r"(20[12]\d)\s*(?:年度报告|Annual\s*Report)", page.text)
        if m:
            return m.group(1)

    return ""


def infer_company_name(pdf_path: str, pages: List[PageContent]) -> str:
    """从文件名或内容推断公司名称。"""
    # 尝试从前几页提取
    for page in pages[:3]:
        # 常见格式: "XX股份有限公司" "XX有限公司"
        m = re.search(r"([\u4e00-\u9fa5]{2,20}(?:股份有限|有限)公司)", page.text)
        if m:
            return m.group(1)
    return ""


def run_extraction(
    pdf_path: str,
    output_dir: str,
    code: str = "",
    year: str = "",
    report_type: str = "annual",
    target_sections: Optional[List[str]] = None,
    all_sections: bool = False,
    list_only: bool = False,
) -> int:
    """
    执行 PDF 提取主流程。

    参数:
        pdf_path: PDF 文件路径
        output_dir: 输出目录
        code: 股票代码
        year: 报告年份
        report_type: 报告类型
        target_sections: 要提取的章节类别列表（如 ["mda", "risk"]），None 为默认（P1+P2）
        all_sections: 是否提取全部章节
        list_only: 仅列出章节，不提取

    返回退出码:
        0: 成功
        1: 部分失败
        4: 提取失败
    """
    print(f"\n{'=' * 60}")
    print(f"  财报 PDF 结构化提取")
    print(f"{'=' * 60}")
    print(f"  PDF: {os.path.basename(pdf_path)}")
    print(f"  输出: {output_dir}")
    print()

    # Step 1: 提取全文
    try:
        pages, pdf_meta = extract_full_text(pdf_path)
    except Exception as e:
        print(f"\n错误: PDF 文本提取失败 — {e}")
        return 4

    if not pages or pdf_meta.get("total_chars", 0) == 0:
        print("\n错误: PDF 未提取到任何文本内容")
        return 4

    if pdf_meta.get("is_scanned"):
        print("\n警告: 疑似扫描件PDF，提取结果可能不完整")

    # Step 2: 推断元信息
    report_year = year or infer_report_year(pdf_path, [], pages)
    company_name = infer_company_name(pdf_path, pages)
    if code and not company_name:
        company_name = code

    print(f"\n  公司: {company_name}")
    print(f"  年份: {report_year or '未知'}")

    # Step 3: 检测章节
    print(f"\n  正在检测章节结构...")
    sections = detect_sections(pages)

    if not sections:
        print("\n警告: 未检测到任何章节结构")
        print("  可能原因: PDF格式特殊、扫描件、或非标准年报格式")
        # 仍然保存全文
        return 4

    print(f"  检测到 {len(sections)} 个章节:\n")

    # 打印章节列表
    for i, s in enumerate(sections, 1):
        level_mark = "  " * (s.level - 1)
        cat_name = SECTION_CLASSIFICATION.get(s.category, {}).get("name", "")
        cat_display = f" [{cat_name}]" if cat_name else ""
        pri_display = f" P{s.priority}" if s.priority > 0 else ""
        print(
            f"  {i:2d}. {level_mark}{s.title}"
            f"  (第{s.start_page}-{s.end_page}页, {s.char_count:,}字)"
            f"{cat_display}{pri_display}"
        )

    if list_only:
        print(f"\n  仅列出章节结构，不执行提取。")
        # 保存结构 JSON
        json_path = save_structure_json(sections, output_dir, code, company_name, pdf_meta, report_year)
        print(f"  已保存结构: {os.path.basename(json_path)}")
        return 0

    # Step 4: 确定要提取的章节
    if target_sections:
        # 用户指定的类别
        to_extract = [s for s in sections if s.category in target_sections]
        matched_cats = set(s.category for s in to_extract)
        unmatched_cats = [c for c in target_sections if c not in matched_cats]
        print(f"\n  指定提取类别: {', '.join(target_sections)}")
        print(f"  匹配成功: {len(matched_cats)}/{len(target_sections)} 个类别")
        if unmatched_cats:
            print(f"  未匹配类别: {', '.join(unmatched_cats)}")
    elif all_sections:
        to_extract = sections
        print(f"\n  提取全部 {len(sections)} 个章节")
    else:
        # 默认提取 P1 + P2
        to_extract = [s for s in sections if s.priority in (1, 2)]
        print(f"\n  默认提取 P1+P2 章节 ({len(to_extract)} 个)")

    if not to_extract:
        print("\n警告: 没有匹配的章节需要提取")
        # 保存结构
        save_structure_json(sections, output_dir, code, company_name, pdf_meta, report_year)
        return 1

    # Step 5: 提取并保存
    print(f"\n  开始提取内容...\n")
    extracted = []
    errors = []

    for s in to_extract:
        try:
            content = extract_section_content(pages, s)
            filepath = save_section_file(
                content, s, output_dir, code, company_name,
                pdf_meta.get("pdf_filename", ""),
                report_year, report_type,
            )
            extracted.append(s)
            print(f"  [OK] {s.title} → {os.path.basename(filepath)} ({s.char_count:,}字)")
        except Exception as e:
            errors.append((s, str(e)))
            print(f"  [ERR] {s.title} — {e}")

    # Step 6: 保存汇总和结构
    print(f"\n  保存汇总...")
    summary_path = save_extraction_summary(
        sections, extracted, output_dir, code, company_name, pdf_meta, report_year,
    )
    print(f"  汇总: {os.path.basename(summary_path)}")

    json_path = save_structure_json(
        sections, output_dir, code, company_name, pdf_meta, report_year,
    )
    print(f"  结构: {os.path.basename(json_path)}")

    # Step 7: 总结
    print(f"\n{'=' * 60}")
    print(f"  提取完成")
    print(f"{'=' * 60}")
    print(f"  成功提取: {len(extracted)}/{len(to_extract)} 个章节")
    total_chars = sum(s.char_count for s in extracted)
    print(f"  总字符数: {total_chars:,}")
    print(f"  输出目录: {output_dir}")

    if errors:
        print(f"\n  失败章节 ({len(errors)} 个):")
        for s, err in errors:
            print(f"    - {s.title}: {err}")
        return 1

    return 0


# ============================================================
# 模块六: CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="A股财报PDF结构化提取工具 — 完整无损提取年报关键章节",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --code 300014                        下载并提取最新年报
  %(prog)s --code 300014 --year 2024             指定年份
  %(prog)s --pdf report.pdf                      处理本地PDF
  %(prog)s --code 300014 --list-sections          仅查看章节结构
  %(prog)s --code 300014 --sections mda,risk      只提取特定章节
  %(prog)s --code 300014 --all-sections           提取全部章节

章节类别代码:
  mda          管理层讨论与分析
  risk         风险因素
  chairman_letter  董事长致辞
  business     业务与产品描述
  audit        审计报告
  company_intro    公司简介
  governance   公司治理
  important_matters  重要事项
  esg          环境与社会责任
  shareholders 股份变动及股东
  financial_notes  财务报表附注
  financial_statements  财务报告
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--code", help="股票代码 (6位数字)，自动从巨潮资讯下载")
    group.add_argument("--pdf", help="直接处理本地 PDF 文件路径")

    parser.add_argument("--year", help="指定报告年份 (如 2024)")
    parser.add_argument(
        "--report-type",
        choices=["annual", "half", "q1", "q3"],
        default="annual",
        help="报告类型 (默认: annual)",
    )
    parser.add_argument("--output-dir", default=".", help="输出目录 (默认: 当前目录)")
    parser.add_argument(
        "--sections",
        help="指定要提取的章节类别，逗号分隔 (如: mda,risk,audit)",
    )
    parser.add_argument(
        "--all-sections",
        action="store_true",
        help="提取全部章节（含财务报表等低优先级）",
    )
    parser.add_argument(
        "--list-sections",
        action="store_true",
        help="仅列出检测到的章节结构，不执行提取",
    )

    args = parser.parse_args()

    # 准备输出目录
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # 获取 PDF
    pdf_path = None
    code = args.code or ""

    if args.pdf:
        pdf_path = os.path.abspath(args.pdf)
        if not os.path.exists(pdf_path):
            print(f"错误: PDF 文件不存在 — {pdf_path}")
            sys.exit(3)
        # 尝试从文件名推断股票代码
        m = re.match(r"(\d{6})", os.path.basename(pdf_path))
        if m:
            code = m.group(1)
    else:
        # 通过股票代码下载
        code = args.code
        if not re.match(r"^\d{6}$", code):
            print(f"错误: 股票代码格式错误 '{code}'，应为6位数字")
            sys.exit(2)

        print(f"\n  准备下载财报 PDF...")
        pdf_path, err = download_report_pdf(
            code, output_dir, year=args.year, report_type=args.report_type,
        )
        if not pdf_path:
            print(f"\n错误: 下载失败 — {err}")
            sys.exit(3)

    # 解析 sections 参数
    target_sections = None
    if args.sections:
        target_sections = [s.strip() for s in args.sections.split(",") if s.strip()]
        invalid = [s for s in target_sections if s not in SECTION_CLASSIFICATION]
        if invalid:
            print(f"警告: 未知的章节类别 {invalid}，将被忽略")
            target_sections = [s for s in target_sections if s in SECTION_CLASSIFICATION]

    # 执行提取
    exit_code = run_extraction(
        pdf_path=pdf_path,
        output_dir=output_dir,
        code=code,
        year=args.year or "",
        report_type=args.report_type,
        target_sections=target_sections,
        all_sections=args.all_sections,
        list_only=args.list_sections,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
