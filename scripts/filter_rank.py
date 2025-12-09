#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Filter and rank fetched papers based on config/keywords.yml.

- 读取 data/raw/YYYY-MM-DD_*.jsonl 中的原始记录
- 使用 config/keywords.yml 中的关键词配置打分与过滤
- 输出 data/processed/YYYY-MM-DD.jsonl
- 更新仓库根目录的 papers.jsonl 作为全量索引

用法示例（在仓库根目录）:
    python scripts/filter_rank.py
    python scripts/filter_rank.py --date 2025-12-09 --verbose
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import yaml

# 仓库根目录：.../bio-ml-papers/
REPO_ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
KEYWORDS_PATH = REPO_ROOT / "config" / "keywords.yml"
INDEX_PATH = REPO_ROOT / "papers.jsonl"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_keywords() -> Dict[str, Any]:
    """
    加载关键词配置 config/keywords.yml。
    """
    if not KEYWORDS_PATH.exists():
        raise FileNotFoundError(f"Keywords config not found: {KEYWORDS_PATH}")
    with KEYWORDS_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_raw_for_date(date_str: str, verbose: bool = False) -> List[Dict[str, Any]]:
    """
    读取指定日期的所有 raw jsonl 文件（data/raw/YYYY-MM-DD_*.jsonl）。
    """
    records: List[Dict[str, Any]] = []
    if not RAW_DIR.exists():
        if verbose:
            print(f"[filter_rank] RAW_DIR not exists: {RAW_DIR}")
        return records

    pattern = f"{date_str}_*.jsonl"
    files = list(RAW_DIR.glob(pattern))
    if verbose:
        print(f"[filter_rank] Looking for raw files with pattern: {pattern}")
        print(f"[filter_rank] Found {len(files)} raw files.")

    for fp in files:
        if verbose:
            print(f"[filter_rank] Loading {fp}")
        with fp.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    records.append(rec)
                except json.JSONDecodeError:
                    if verbose:
                        print(f"[filter_rank] Skip invalid JSON line in {fp}")
                    continue
    if verbose:
        print(f"[filter_rank] Loaded {len(records)} raw records for {date_str}")
    return records


def score_paper(paper: Dict[str, Any], kw_cfg: Dict[str, Any]) -> int:
    """
    根据关键词配置对论文进行简单打分：

    - 在 title + abstract 中查找 include_keywords / bio_keywords
    - 每个命中 +1
    - 命中 exclude_keywords 则直接返回一个很小的分数（淘汰）

    返回整数分数（可为负数）。
    """
    title = paper.get("title", "") or ""
    abstract = paper.get("abstract", "") or ""
    text = f"{title} {abstract}".lower()

    include_keywords = [k.lower() for k in kw_cfg.get("include_keywords", [])]
    bio_keywords = [k.lower() for k in kw_cfg.get("bio_keywords", [])]
    exclude_keywords = [k.lower() for k in kw_cfg.get("exclude_keywords", [])]

    # 排除词优先：命中任何一个直接淘汰
    for word in exclude_keywords:
        if word and word in text:
            return -10_000

    score = 0

    for word in include_keywords:
        if word and word in text:
            score += 1

    for word in bio_keywords:
        if word and word in text:
            score += 1

    return score


def deduplicate(records: List[Dict[str, Any]], verbose: bool = False) -> List[Dict[str, Any]]:
    """
    基于 paper["id"] 去重，如果同一个 id 出现多次，保留分数最高的一条。
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    for p in records:
        pid = p.get("id")
        if not pid:
            if verbose:
                print("[filter_rank] Found record without 'id', skipping.")
            continue
        current = by_id.get(pid)
        if current is None or p.get("score", 0) > current.get("score", 0):
            by_id[pid] = p
    if verbose:
        print(f"[filter_rank] Deduplicated from {len(records)} to {len(by_id)} records.")
    return list(by_id.values())


def load_index(verbose: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    读取已有的全量索引 papers.jsonl，返回 id -> paper dict。
    """
    index: Dict[str, Dict[str, Any]] = {}
    if not INDEX_PATH.exists():
        if verbose:
            print(f"[filter_rank] Index file not found, will create new: {INDEX_PATH}")
        return index

    with INDEX_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                if verbose:
                    print("[filter_rank] Skip invalid JSON line in index.")
                continue
            pid = rec.get("id")
            if pid:
                index[pid] = rec
    if verbose:
        print(f"[filter_rank] Loaded {len(index)} records from existing index.")
    return index


def save_index(index: Dict[str, Dict[str, Any]], verbose: bool = False) -> None:
    """
    将 id -> paper dict 写回 papers.jsonl。
    """
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        for rec in index.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if verbose:
        print(f"[filter_rank] Saved {len(index)} records to index: {INDEX_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter and rank fetched papers.")
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date to process in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args()

    verbose = args.verbose
    target_date = args.date or date.today().isoformat()

    if verbose:
        print(f"[filter_rank] Processing date: {target_date}")
        print(f"[filter_rank] RAW_DIR = {RAW_DIR}")
        print(f"[filter_rank] PROCESSED_DIR = {PROCESSED_DIR}")

    kw_cfg = load_keywords()
    min_score = int(kw_cfg.get("min_score", 1))

    raw_records = load_raw_for_date(target_date, verbose=verbose)
    if not raw_records:
        if verbose:
            print(f"[filter_rank] No raw records found for {target_date}, nothing to do.")
        return

    # 打分 + 过滤
    filtered: List[Dict[str, Any]] = []
    for p in raw_records:
        s = score_paper(p, kw_cfg)
        if s >= min_score:
            p["score"] = s
            filtered.append(p)

    if verbose:
        print(
            f"[filter_rank] {len(filtered)} records passed min_score={min_score} "
            f"out of {len(raw_records)} raw records."
        )

    # 去重
    dedup_list = deduplicate(filtered, verbose=verbose)

    # 排序：按 score 降序
    dedup_list.sort(key=lambda x: x.get("score", 0), reverse=True)

    # 写当日 processed 文件
    processed_path = PROCESSED_DIR / f"{target_date}.jsonl"
    with processed_path.open("w", encoding="utf-8") as f:
        for p in dedup_list:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    if verbose:
        print(
            f"[filter_rank] Saved {len(dedup_list)} processed records to {processed_path}"
        )

    # 更新全量索引 papers.jsonl
    index = load_index(verbose=verbose)
    for p in dedup_list:
        pid = p.get("id")
        if not pid:
            continue
        index[pid] = p  # 如果已存在则覆盖为最新版本

    save_index(index, verbose=verbose)

    if verbose:
        print("[filter_rank] Done.")


if __name__ == "__main__":
    main()
