#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Entry point for fetching papers from all configured sources.

当前版本：
- 调用 arXiv（fetch_arxiv）和 bioRxiv（fetch_biorxiv）
- 将结果以 jsonl 形式保存在 data/raw/YYYY-MM-DD_<source>.jsonl
"""


from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from fetch_arxiv import fetch_arxiv  # 与 scripts/fetch_arxiv.py 同目录
from fetch_biorxiv import fetch_biorxiv  # 新增：bioRxiv 抓取



# 仓库根目录：.../bio-ml-papers/
REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def save_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    """
    将记录列表写入 jsonl 文件，每行一条 JSON。
    """
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(verbose: bool = True) -> None:
    """
    主流程：
    - 调用各数据源的 fetch_* 函数
    - 将结果分别写入 data/raw/ 下带日期前缀的文件
    """
    today_str = date.today().isoformat()
    if verbose:
        print(f"[fetch_all] Fetching papers for {today_str}")

    total = 0

    # 1) arXiv
    if verbose:
        print("[fetch_all] Fetching from arxiv...")
    arxiv_papers = fetch_arxiv(verbose=verbose)
    arxiv_path = RAW_DIR / f"{today_str}_arxiv.jsonl"
    save_jsonl(arxiv_path, arxiv_papers)
    total += len(arxiv_papers)
    if verbose:
        print(f"[fetch_all] Saved {len(arxiv_papers)} arxiv papers to {arxiv_path}")

    # 2) bioRxiv
    if verbose:
        print("[fetch_all] Fetching from biorxiv...")
    biorxiv_papers = fetch_biorxiv(verbose=verbose)
    biorxiv_path = RAW_DIR / f"{today_str}_biorxiv.jsonl"
    save_jsonl(biorxiv_path, biorxiv_papers)
    total += len(biorxiv_papers)
    if verbose:
        print(f"[fetch_all] Saved {len(biorxiv_papers)} biorxiv papers to {biorxiv_path}")


    # 3) 预留其它数据源扩展位（如 bioRxiv、PubMed 等）
    # 未来你可以类似地：
    # from fetch_biorxiv import fetch_biorxiv
    # biorxiv_papers = fetch_biorxiv(verbose=verbose)
    # save_jsonl(RAW_DIR / f"{today_str}_biorxiv.jsonl", biorxiv_papers)
    # total += len(biorxiv_papers)

    if verbose:
        print(f"[fetch_all] Done. Total records fetched today: {total}")


if __name__ == "__main__":
    main()
