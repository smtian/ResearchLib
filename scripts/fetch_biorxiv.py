#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Fetch papers from bioRxiv according to config/sources.yml and
return them in a unified schema.

Endpoint (per https://api.biorxiv.org/):

    https://api.biorxiv.org/details/[server]/[start_date]/[end_date]/[cursor]

- server: "biorxiv" (或 "medrxiv")
- start_date, end_date: YYYY-MM-DD
- cursor: 起始偏移量，分页步长通常为 100

Intended to be called from scripts/fetch_all.py as:
    from fetch_biorxiv import fetch_biorxiv
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
import yaml

# 仓库根目录：.../bio-ml-papers/
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "sources.yml"


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _normalize_authors(authors_str: str) -> List[str]:
    """
    将 bioRxiv 返回的作者字符串转换为作者列表。

    API 文档列出的 authors 字段一般是用分号分隔的字符串，
    例如: "Smith, John; Doe, Jane; ...".
    """
    if not authors_str:
        return []

    parts = re.split(r";", authors_str)
    authors: List[str] = []
    for p in parts:
        name = p.strip()
        if name:
            authors.append(name)
    return authors


def _parse_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    将单条 bioRxiv "details" 接口返回的 JSON item 解析成统一 schema 的 dict。

    参考 docs: doi, title, authors, date, version, category, abstract, published, server 等字段。
    """
    doi = (item.get("doi") or "").strip()
    title = (item.get("title") or "").strip()
    authors_str = (item.get("authors") or "").strip()
    abstract = (item.get("abstract") or "").strip()
    category = (item.get("category") or "").strip()
    date_str = (item.get("date") or "").strip()
    version = str(item.get("version") or "").strip()
    published = (item.get("published") or "").strip()
    server = (item.get("server") or "biorxiv").strip().lower()  # biorxiv / medrxiv

    # 统一 id，例如: biorxiv:10.1101/2025.12.09.123456
    paper_id = f"{server}:{doi}" if doi else ""

    # 构造网页 URL 和 PDF URL
    # 参考官方站点：https://www.biorxiv.org/content/<doi>v<version>
    # medRxiv 类似：https://www.medrxiv.org/content/<doi>v<version>
    if server == "biorxiv":
        host = "https://www.biorxiv.org"
    elif server == "medrxiv":
        host = "https://www.medrxiv.org"
    else:
        # fallback，理论上不会走到
        host = "https://www.biorxiv.org"

    url = None
    pdf_url = None
    if doi:
        ver = version if version else "1"
        base = f"{host}/content/{doi}v{ver}"
        url = base
        pdf_url = base + ".full.pdf"

    authors = _normalize_authors(authors_str)

    paper = {
        "id": paper_id,
        "source": server,  # 与 arxiv 的 "source": "arxiv" 对齐，用在后续 source_weight
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "categories": [category] if category else [],
        "published": date_str or published,
        "updated": date_str or published,
        "url": url,
        "pdf_url": pdf_url,
    }
    return paper


def _compute_date_range(days_back: int) -> Tuple:
    """
    根据 days_back 计算 [start_date, end_date] 区间（UTC），
    返回 YYYY-MM-DD 字符串。

    这里约定：区间覆盖“最近 days_back 天”的日期，包括今天在内，
    例如 days_back=1 时：start = today - 1 day, end = today
    这样稍微有重叠，但靠下游去重保证不会重复积累。
    """
    if days_back <= 0:
        days_back = 1

    today_utc = datetime.utcnow().date()
    start_date = today_utc - timedelta(days=days_back)
    end_date = today_utc
    return start_date.isoformat(), end_date.isoformat()


def fetch_biorxiv(verbose: bool = False) -> List[Dict[str, Any]]:
    """
    主函数：根据 config/sources.yml 中的 biorxiv 配置抓取论文（preprint 元数据）。

    使用 bioRxiv API 的 “details” 端点 + 日期区间模式：

        https://api.biorxiv.org/details/[server]/[start_date]/[end_date]/[cursor]

    返回：统一 schema 的论文列表。
    """
    config = load_config()
    biorxiv_cfg = config.get("biorxiv", {})

    if not biorxiv_cfg.get("enabled", False):
        if verbose:
            print("[fetch_biorxiv] biorxiv source disabled in config.")
        return []

    base_url: str = biorxiv_cfg.get("base_url", "https://api.biorxiv.org/details")
    server: str = biorxiv_cfg.get("server", "biorxiv").lower()
    days_back: int = int(biorxiv_cfg.get("days_back", 1))
    max_results: int = int(biorxiv_cfg.get("max_results", 300))
    page_size: int = int(biorxiv_cfg.get("page_size", 100))

    start_date, end_date = _compute_date_range(days_back)

    if verbose:
        print(
            f"[fetch_biorxiv] base_url={base_url}, server={server}, "
            f"start_date={start_date}, end_date={end_date}, "
            f"max_results={max_results}, page_size={page_size}"
        )

    collected: List[Dict[str, Any]] = []
    cursor = 0

    while len(collected) < max_results:
        # docs: https://api.biorxiv.org/details/biorxiv/2018-08-21/2018-08-28/45
        url = f"{base_url}/{server}/{start_date}/{end_date}/{cursor}"
        if verbose:
            print(f"[fetch_biorxiv] Requesting: {url}")

        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            if verbose:
                print(f"[fetch_biorxiv] Request failed: {e}")
            break

        try:
            data = resp.json()
        except ValueError:
            if verbose:
                print("[fetch_biorxiv] Failed to parse JSON response.")
            break

        messages = data.get("messages") or []
        collection = data.get("collection") or []

        if verbose:
            # 打印一下 messages 里的关键信息（status / count / new_papers 等）
            if messages:
                try:
                    msg0 = messages[0]
                    print(
                        "[fetch_biorxiv] message:",
                        f"status={msg0.get('status')}, ",
                        f"count={msg0.get('count')}, ",
                        f"new_papers={msg0.get('new_papers')}",
                    )
                except Exception:
                    print("[fetch_biorxiv] messages:", messages)
            print(f"[fetch_biorxiv] Retrieved {len(collection)} items at cursor={cursor}")

        # 按官方示例逻辑：status 不是 ok 或 collection 为空时停止
        status_ok = True
        if messages:
            status_ok = (messages[0] or {}).get("status") == "ok"

        if not status_ok or not collection:
            if verbose:
                print("[fetch_biorxiv] No more items or status not ok, stopping.")
            break

        for item in collection:
            paper = _parse_item(item)
            collected.append(paper)
            if len(collected) >= max_results:
                break

        if len(collection) < page_size:
            # 少于 page_size 说明已经到结尾了
            break

        cursor += page_size

    if verbose:
        print(f"[fetch_biorxiv] Total collected: {len(collected)} papers.")

    return collected


if __name__ == "__main__":
    # 本地调试：
    #   python scripts/fetch_biorxiv.py > biorxiv_debug.jsonl
    results = fetch_biorxiv(verbose=True)
    for p in results:
        print(json.dumps(p, ensure_ascii=False))
