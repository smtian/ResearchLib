#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate daily markdown report from processed papers and update README.

功能：
- 从 data/processed/YYYY-MM-DD.jsonl 读取当日筛选后的论文
- 生成 data/daily/YYYY-MM-DD.md
- 更新根目录 README.md，列出最近若干天的日报链接

用法（在仓库根目录）:
    python scripts/generate_report.py
    python scripts/generate_report.py --date 2025-12-09 --verbose
    python scripts/generate_report.py --window 7
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

# 仓库根目录：.../bio-ml-papers/
REPO_ROOT = Path(__file__).resolve().parents[1]

PROCESSED_DIR = REPO_ROOT / "data" / "processed"
DAILY_DIR = REPO_ROOT / "data" / "daily"
README_PATH = REPO_ROOT / "README.md"

DAILY_DIR.mkdir(parents=True, exist_ok=True)


def load_processed(date_str: str, verbose: bool = False) -> List[Dict[str, Any]]:
    """
    读取指定日期的 processed 文件 data/processed/YYYY-MM-DD.jsonl。
    如果文件不存在，返回空列表。
    """
    path = PROCESSED_DIR / f"{date_str}.jsonl"
    if not path.exists():
        if verbose:
            print(f"[generate_report] No processed file for {date_str}: {path}")
        return []

    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                records.append(rec)
            except json.JSONDecodeError:
                if verbose:
                    print(f"[generate_report] Skip invalid JSON line in {path}")
                continue

    if verbose:
        print(f"[generate_report] Loaded {len(records)} processed records from {path}")
    return records


def _safe_str_list(values: Any) -> List[str]:
    """
    将任意列表-like 转成 str 列表，避免 authors 等字段格式不规范导致报错。
    """
    if not isinstance(values, (list, tuple)):
        return []
    result: List[str] = []
    for v in values:
        try:
            result.append(str(v))
        except Exception:
            continue
    return result


def generate_daily_md(date_str: str, records: List[Dict[str, Any]]) -> str:
    """
    根据指定日期和记录列表，生成日报 markdown 字符串。
    """
    lines: List[str] = []
    lines.append(f"# Papers for {date_str}")
    lines.append("")
    lines.append(f"Total: {len(records)} papers")
    lines.append("")
    if not records:
        lines.append("_No papers for this day._")
        lines.append("")
        return "\n".join(lines)

    for p in records:
        title = (p.get("title") or "").strip()
        pid = p.get("id") or ""
        source = p.get("source") or ""
        score = p.get("score", 0)
        authors = ", ".join(_safe_str_list(p.get("authors")))
        published = p.get("published") or ""
        categories = p.get("categories") or []
        categories_str = ", ".join(categories) if isinstance(categories, (list, tuple)) else str(categories)
        url = p.get("url") or ""
        pdf_url = p.get("pdf_url") or ""
        abstract = (p.get("abstract") or "").strip()

        lines.append(f"## {title if title else '(no title)'}")
        lines.append("")
        meta_parts = []
        if pid:
            meta_parts.append(f"ID: `{pid}`")
        if source:
            meta_parts.append(f"Source: `{source}`")
        meta_parts.append(f"Score: {score}")
        lines.append("- " + "  |  ".join(meta_parts))

        if authors:
            lines.append(f"- Authors: {authors}")
        if published:
            lines.append(f"- Date: {published}")
        if categories_str:
            lines.append(f"- Categories: {categories_str}")
        if url:
            lines.append(f"- Link: {url}")
        if pdf_url:
            lines.append(f"- PDF: {pdf_url}")

        lines.append("")
        if abstract:
            lines.append(abstract)
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def update_readme(target_date: date, window: int = 7, verbose: bool = False) -> None:
    """
    更新 README.md，展示最近 window 天内存在日报的链接。
    """
    if verbose:
        print(f"[generate_report] Updating README for last {window} days")

    lines: List[str] = []
    lines.append("# Bio/ML Papers Daily Digest")
    lines.append("")
    lines.append("Automatically collected and filtered computational biology & ML papers.")
    lines.append("")
    lines.append(f"Recent days (up to last {window} days):")
    lines.append("")

    for i in range(window):
        d = target_date - timedelta(days=i)
        date_str = d.isoformat()
        daily_file = DAILY_DIR / f"{date_str}.md"
        if daily_file.exists():
            rel_path = f"data/daily/{date_str}.md"
            lines.append(f"- [{date_str}]({rel_path})")

    if len(lines) == 6:
        # 没有任何日报文件被找到
        lines.append("- _No daily reports generated yet._")

    README_PATH.write_text("\n".join(lines), encoding="utf-8")

    if verbose:
        print(f"[generate_report] README updated: {README_PATH}")


def parse_date(date_str: str) -> date:
    """
    将 YYYY-MM-DD 字符串解析为 date 对象，简单封装，出错时抛异常。
    """
    try:
        return date.fromisoformat(date_str)
    except ValueError as e:
        raise ValueError(f"Invalid date format (expected YYYY-MM-DD): {date_str}") from e


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily report and update README.")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to generate report for in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=7,
        help="How many days to list in README (default: 7).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args()

    verbose = args.verbose

    if args.date:
        target_date = parse_date(args.date)
    else:
        target_date = date.today()

    date_str = target_date.isoformat()

    if verbose:
        print(f"[generate_report] Generating report for date: {date_str}")
        print(f"[generate_report] PROCESSED_DIR = {PROCESSED_DIR}")
        print(f"[generate_report] DAILY_DIR = {DAILY_DIR}")

    records = load_processed(date_str, verbose=verbose)
    daily_md = generate_daily_md(date_str, records)

    daily_path = DAILY_DIR / f"{date_str}.md"
    daily_path.write_text(daily_md, encoding="utf-8")

    if verbose:
        print(f"[generate_report] Daily report written to: {daily_path}")

    update_readme(target_date, window=args.window, verbose=verbose)

    if verbose:
        print("[generate_report] Done.")


if __name__ == "__main__":
    main()
