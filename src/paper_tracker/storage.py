# src/paper_tracker/storage.py
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Paper


# ===== 路径设定 =====

# 假设目录结构：<repo_root>/src/paper_tracker/storage.py
# data/index/papers.jsonl 作为主索引文件
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
INDEX_DIR = DATA_DIR / "index"
DEFAULT_INDEX_FILE = INDEX_DIR / "papers.jsonl"


# ===== 内部工具函数 =====

_DOI_RE_WHITESPACE = re.compile(r"\s+")
_TITLE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    s = doi.strip().lower()
    # DOI 中的空白一律压缩/去掉
    s = _DOI_RE_WHITESPACE.sub("", s)
    return s or None


def _normalize_title_journal(title: str, journal: Optional[str]) -> str:
    """
    将 title + journal 归一化为适合 hash 的字符串：
    - 全部小写
    - 去掉非 a-z0-9 字符
    """
    base = f"{title or ''} {journal or ''}".strip().lower()
    if not base:
        return ""
    normalized = _TITLE_NORMALIZE_RE.sub("", base)
    return normalized


def _title_hash(title: str, journal: Optional[str]) -> str:
    """
    根据归一化的 title + journal 生成一个稳定的 hash，用于：
    - 去重索引 by_title_hash
    - 在无 DOI 情况下生成 id 的一部分
    """
    normalized = _normalize_title_journal(title, journal)
    if not normalized:
        return ""
    h = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return h


def generate_paper_id(paper: Paper) -> str:
    """
    生成 Paper 的 id 规则：
    - 如果已有 id，则直接返回（不覆盖现有 id）
    - 否则若有 DOI：id = "doi:" + normalized_doi
    - 否则：id = "tj:" + sha256(normalized(title+journal))[:16]
    """
    if getattr(paper, "id", None):
        return paper.id

    doi_norm = _normalize_doi(paper.doi)
    if doi_norm:
        return f"doi:{doi_norm}"

    h = _title_hash(paper.title, paper.journal)
    if h:
        return f"tj:{h}"

    # 极端情况：既没有 DOI，也没有 title/journal（理论上不会发生）
    # 退回一个基于 title 的 hash
    fallback = hashlib.sha256(paper.title.encode("utf-8")).hexdigest()[:16]
    return f"tj:{fallback}"


# ===== 索引构建 =====

def _build_indices(
    papers: List[Paper],
) -> Tuple[Dict[str, Paper], Dict[str, Paper], Dict[str, Paper]]:
    """
    根据已有 Paper 列表构建三个索引：
    - by_id: id -> Paper
    - by_doi: normalized_doi -> Paper
    - by_title_hash: title_hash -> Paper
    """
    by_id: Dict[str, Paper] = {}
    by_doi: Dict[str, Paper] = {}
    by_title_hash: Dict[str, Paper] = {}

    for p in papers:
        # 确保 id 存在（以防早期数据缺失）
        if not getattr(p, "id", None):
            p.id = generate_paper_id(p)

        by_id[p.id] = p

        doi_norm = _normalize_doi(p.doi)
        if doi_norm:
            # 如果同一个 DOI 对应多条记录，后出现的覆盖前面
            by_doi[doi_norm] = p

        h = _title_hash(p.title, p.journal)
        if h:
            by_title_hash[h] = p

    return by_id, by_doi, by_title_hash


# ===== JSONL 读写 =====

def _ensure_index_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_jsonl(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    items: List[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # 在这里可以选择记录日志；当前实现简单忽略坏行
                continue
            if isinstance(obj, dict):
                items.append(obj)
    return items


def _write_jsonl(path: Path, items: List[dict]) -> None:
    _ensure_index_dir(path)
    with path.open("w", encoding="utf-8") as f:
        for obj in items:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ===== 高层 API =====

def load_all_papers(index_path: Optional[Path] = None) -> List[Paper]:
    """
    从 JSONL 文件中加载所有 Paper。
    - 默认使用 data/index/papers.jsonl
    - 不存在时返回空列表
    """
    path = index_path or DEFAULT_INDEX_FILE
    raw_items = _read_jsonl(path)
    papers: List[Paper] = []
    for obj in raw_items:
        try:
            p = Paper.from_dict(obj)
        except TypeError:
            # 字段缺失等导致 dataclass 构造失败，可以选择记录日志；当前忽略该条
            continue
        papers.append(p)
    return papers


def save_all_papers(papers: List[Paper], index_path: Optional[Path] = None) -> None:
    """
    将所有 Paper 全量写回到 JSONL 文件。
    当前采用全量重写策略，逻辑简单可靠。
    """
    path = index_path or DEFAULT_INDEX_FILE
    items = [p.to_dict() for p in papers]
    _write_jsonl(path, items)


def upsert_papers(
    new_papers: List[Paper],
    index_path: Optional[Path] = None,
) -> List[Paper]:
    """
    将 new_papers 插入索引文件，并返回真正新增的 Paper 列表。

    去重规则（顺序）：
    1. 若 new_paper 的 DOI 在 existing.by_doi 中已存在 -> 视为重复，跳过插入。
    2. 否则若 title_hash(title + journal) 在 existing.by_title_hash 中已存在 -> 视为重复，跳过插入。
    3. 否则若 new_paper.id 在 existing.by_id 中已存在 -> 视为重复，跳过插入。
    4. 否则视为“新论文”，追加到列表并写回文件。

    注意：
    - 如果 new_paper.id 为空，会在这里用 generate_paper_id() 填充。
    - 对已存在的论文，这个函数当前不会更新字段（真正的“更新”操作可以在后续 pipeline 中按需实现）。
    """
    path = index_path or DEFAULT_INDEX_FILE

    # 1. 读取已有数据并构建索引
    existing_papers = load_all_papers(path)
    by_id, by_doi, by_title_hash = _build_indices(existing_papers)

    actually_new: List[Paper] = []

    for p in new_papers:
        # 确保 id 存在
        if not getattr(p, "id", None):
            p.id = generate_paper_id(p)

        doi_norm = _normalize_doi(p.doi)
        title_h = _title_hash(p.title, p.journal)

        # 先按 DOI 去重
        if doi_norm and doi_norm in by_doi:
            continue

        # 再按 title_hash 去重
        if title_h and title_h in by_title_hash:
            continue

        # 再按 id 去重
        if p.id in by_id:
            continue

        # 真正新增
        existing_papers.append(p)
        actually_new.append(p)

        # 更新索引
        by_id[p.id] = p
        if doi_norm:
            by_doi[doi_norm] = p
        if title_h:
            by_title_hash[title_h] = p

    # 2. 全量重写文件
    save_all_papers(existing_papers, path)

    return actually_new
