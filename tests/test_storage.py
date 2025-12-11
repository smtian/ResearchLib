# tests/test_storage.py

from pathlib import Path
import pytest
import sys
ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
    
from paper_tracker.models import Paper
from paper_tracker.storage import (
    load_all_papers,
    save_all_papers,
    upsert_papers,
    generate_paper_id,
)


def test_load_all_papers_missing_file_returns_empty(tmp_path: Path):
    """当索引文件不存在时，load_all_papers 返回空列表。"""
    index_path = tmp_path / "papers.jsonl"
    assert not index_path.exists()

    papers = load_all_papers(index_path=index_path)
    assert papers == []


def test_save_and_load_roundtrip(tmp_path: Path):
    """save_all_papers + load_all_papers 能正确完成一轮读写。"""
    index_path = tmp_path / "papers.jsonl"

    p1 = Paper(id="p1", title="Paper One", doi="10.1/abc")
    p2 = Paper(id="p2", title="Paper Two", doi="10.2/def")

    save_all_papers([p1, p2], index_path=index_path)
    assert index_path.is_file()

    loaded = load_all_papers(index_path=index_path)
    assert len(loaded) == 2

    # 简单检查属性一致
    titles = {p.title for p in loaded}
    dois = {p.doi for p in loaded}
    assert titles == {"Paper One", "Paper Two"}
    assert dois == {"10.1/abc", "10.2/def"}


def test_generate_paper_id_prefers_doi(tmp_path: Path):
    """generate_paper_id 优先使用 DOI 构造 id。"""
    p = Paper(
        id="",  # 空字符串 -> 覆盖为新 id
        title="Some title",
        doi="10.1234/ABC Def",  # 含大写与空格
    )

    pid = generate_paper_id(p)
    # 归一化后应为 doi:10.1234/abcdef
    assert pid.startswith("doi:")
    assert "10.1234/abcdef" in pid


def test_generate_paper_id_uses_title_hash_when_no_doi(tmp_path: Path):
    """没有 DOI 时，generate_paper_id 使用 title+journal hash。"""
    p = Paper(
        id="",
        title="My Interesting Title",
        journal="Some Journal",
        doi=None,
    )

    id1 = generate_paper_id(p)
    id2 = generate_paper_id(p)

    assert id1.startswith("tj:")
    assert id1 == id2  # 对同一对象调用应稳定


def test_upsert_papers_inserts_new_papers(tmp_path: Path):
    """空索引文件时，upsert_papers 应插入所有新论文。"""
    index_path = tmp_path / "papers.jsonl"

    p1 = Paper(id="", title="Paper One", doi="10.1/abc")
    p2 = Paper(id="", title="Paper Two", doi="10.2/def")

    new = upsert_papers([p1, p2], index_path=index_path)
    assert len(new) == 2

    loaded = load_all_papers(index_path=index_path)
    assert len(loaded) == 2
    titles = {p.title for p in loaded}
    assert titles == {"Paper One", "Paper Two"}


def test_upsert_papers_deduplicate_by_doi(tmp_path: Path):
    """相同 DOI 的论文应被视为重复，不再插入。"""
    index_path = tmp_path / "papers.jsonl"

    p1 = Paper(id="", title="Original", doi="10.1/abc")
    p_dup = Paper(id="", title="Duplicate", doi="10.1/ABC")  # 同一 DOI（不同大小写）

    # 第一次插入
    new1 = upsert_papers([p1], index_path=index_path)
    assert len(new1) == 1

    # 第二次使用相同 DOI 插入，应被去重
    new2 = upsert_papers([p_dup], index_path=index_path)
    assert len(new2) == 0

    loaded = load_all_papers(index_path=index_path)
    assert len(loaded) == 1
    assert loaded[0].title == "Original"


def test_upsert_papers_deduplicate_by_title_hash_when_no_doi(tmp_path: Path):
    """在 DOI 为空时，title+journal hash 相同的论文应被视为重复。"""
    index_path = tmp_path / "papers.jsonl"

    p1 = Paper(
        id="",
        title="Same Title",
        journal="Same Journal",
        doi=None,
    )
    p2 = Paper(
        id="",
        title="Same Title",  # 完全相同
        journal="Same Journal",
        doi=None,
    )

    new1 = upsert_papers([p1], index_path=index_path)
    assert len(new1) == 1

    new2 = upsert_papers([p2], index_path=index_path)
    assert len(new2) == 0

    loaded = load_all_papers(index_path=index_path)
    assert len(loaded) == 1
    assert loaded[0].title == "Same Title"


def test_upsert_papers_deduplicate_by_id(tmp_path: Path):
    """当 DOI 和 title 不同但 id 相同，后插入的论文也应被视为重复。"""
    index_path = tmp_path / "papers.jsonl"

    # 先插入一条
    p1 = Paper(
        id="paper-1",
        title="First Version",
        doi="10.1/abc",
    )
    new1 = upsert_papers([p1], index_path=index_path)
    assert len(new1) == 1

    # 新论文 id 相同，但 DOI / title 不同，应通过 id 去重
    p2 = Paper(
        id="paper-1",
        title="Second Version",
        doi="10.1/xyz",
    )
    new2 = upsert_papers([p2], index_path=index_path)
    assert len(new2) == 0

    loaded = load_all_papers(index_path=index_path)
    assert len(loaded) == 1
    assert loaded[0].title == "First Version"


def test_load_all_papers_ignores_invalid_json_lines(tmp_path: Path):
    """load_all_papers 应忽略 JSON 解析失败的行。"""
    index_path = tmp_path / "papers.jsonl"

    # 手动写入一行坏 JSON 和一行好 JSON
    bad_line = "{this is not valid json}\n"
    good_line = '{"id": "p1", "title": "Valid Paper"}\n'

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(bad_line + good_line, encoding="utf-8")

    loaded = load_all_papers(index_path=index_path)
    # 只应加载成功解析的一条
    assert len(loaded) == 1
    assert loaded[0].id == "p1"
    assert loaded[0].title == "Valid Paper"
