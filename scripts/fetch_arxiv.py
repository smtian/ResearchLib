#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fetch papers from arXiv according to config/sources.yml and
return them in a unified schema.

Intended to be called from scripts/fetch_all.py as:
    from fetch_arxiv import fetch_arxiv
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode  # 新增

import feedparser
import yaml

# 仓库根目录：.../bio-ml-papers/
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "sources.yml"


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_search_query(categories: List[str]) -> str:
    """
    构造 arXiv search_query 字符串。

    示例：
        categories = ["q-bio.BM", "q-bio.QM"]
        -> "cat:q-bio.BM OR cat:q-bio.QM"

    真正拼到 URL 时用 urlencode 做编码。
    """
    parts = [f"cat:{c}" for c in categories]
    # 中间保留空格和 OR，交给 urlencode 统一编码
    return " OR ".join(parts)


def _entry_datetime(entry: Any) -> Optional[dt.datetime]:
    """
    从 feedparser 的 entry 中拿到一个 datetime（优先 updated，其次 published）。
    如果缺失则返回 None。
    """
    tm = getattr(entry, "updated_parsed", None) or getattr(
        entry, "published_parsed", None
    )
    if tm is None:
        return None
    return dt.datetime(*tm[:6])


def _is_recent(entry: Any, days_back: int) -> bool:
    """
    判断这篇论文是否在最近 days_back 天内（基于 updated/published 时间）。
    """
    entry_dt = _entry_datetime(entry)
    if entry_dt is None:
        # 没有时间信息就先保留，宁可多不少
        return True

    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days_back)
    # entry_dt 是 naive 时间，视为 UTC，这里同样使用 UTC naive 比较
    return entry_dt >= cutoff


def _parse_entry(entry: Any) -> Dict[str, Any]:
    """
    将单条 arXiv entry 解析成统一 schema 的 dict。
    """
    # arxiv id 通常在 entry.id 中，形如 'http://arxiv.org/abs/1234.56789v1'
    raw_id = getattr(entry, "id", "")
    arxiv_id = raw_id.split("/")[-1] if raw_id else ""
    if arxiv_id:
        # 去掉版本号，仅保留主体 ID（可按需保留版本号）
        base_id = arxiv_id.split("v")[0]
        paper_id = f"arxiv:{base_id}"
    else:
        paper_id = ""

    # authors
    authors = []
    for a in getattr(entry, "authors", []):
        name = getattr(a, "name", None)
        if name:
            authors.append(name)

    # categories
    categories = []
    tags = getattr(entry, "tags", None)
    if tags:
        for t in tags:
            term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
            if term:
                categories.append(term)

    # links
    url = getattr(entry, "link", None)
    pdf_url = None
    links = getattr(entry, "links", None)
    if links:
        for l in links:
            href = getattr(l, "href", None) or l.get("href")
            ltype = getattr(l, "type", None) or l.get("type")
            if href and ltype == "application/pdf":
                pdf_url = href
                break

    published = getattr(entry, "published", "")
    updated = getattr(entry, "updated", "") or published

    paper = {
        "id": paper_id,
        "source": "arxiv",
        "title": getattr(entry, "title", "").strip(),
        "authors": authors,
        "abstract": getattr(entry, "summary", "").strip(),
        "categories": categories,
        "published": published,
        "updated": updated,
        "url": url,
        "pdf_url": pdf_url,
    }
    return paper


def fetch_arxiv(verbose: bool = False) -> List[Dict[str, Any]]:
    """
    主函数：根据 config/sources.yml 中的 arxiv 配置抓取论文。

    返回：统一 schema 的论文列表。
    """
    config = load_config()
    arxiv_cfg = config.get("arxiv", {})

    if not arxiv_cfg.get("enabled", False):
        if verbose:
            print("[fetch_arxiv] arxiv source disabled in config.")
        return []

    base_url: str = arxiv_cfg.get("base_url", "http://export.arxiv.org/api/query")
    categories: List[str] = arxiv_cfg.get("categories", [])
    max_results: int = int(arxiv_cfg.get("max_results", 50))
    days_back: int = int(arxiv_cfg.get("days_back", 1))

    if not categories:
        raise ValueError("No arxiv categories configured in config/sources.yml")

    search_query = build_search_query(categories)

    # 关键修改：使用 urlencode 对所有参数进行 URL 编码，避免空格等非法字符
    params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }
    url = f"{base_url}?{urlencode(params)}"

    if verbose:
        print(f"[fetch_arxiv] Requesting: {url}")

    feed = feedparser.parse(url)

    if feed.bozo:
        # feed.bozo 为 True 表示解析出现问题
        raise RuntimeError(f"Failed to parse arxiv feed: {feed.bozo_exception}")

    entries = getattr(feed, "entries", [])
    if verbose:
        print(f"[fetch_arxiv] Retrieved {len(entries)} entries from arxiv.")

    # import pdb
    # pdb.set_trace()
    
    papers: List[Dict[str, Any]] = []
    for entry in entries:
        if not _is_recent(entry, days_back=days_back):
            continue
        paper = _parse_entry(entry)
        papers.append(paper)

    if verbose:
        print(f"[fetch_arxiv] {len(papers)} entries remain after date filter.")

    return papers


if __name__ == "__main__":
    # 本地调试：python scripts/fetch_arxiv.py > arxiv_debug.jsonl
    results = fetch_arxiv(verbose=True)
    for p in results:
        print(json.dumps(p, ensure_ascii=False))
