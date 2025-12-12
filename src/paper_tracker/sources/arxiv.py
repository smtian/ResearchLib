# src/paper_tracker/sources/arxiv.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET

import requests

from paper_tracker.config import SourceConfig
from paper_tracker.models import Paper
from paper_tracker.sources.base import (
    SourceEnrichmentResult,
    build_default_queries,
    register_source_type,
)

logger = logging.getLogger(__name__)

ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_USER_AGENT = "paper-tracker/0.1 (mailto:your-email@example.com)"


def _normalize_doi(doi: Optional[str]) -> Optional[str]:
    """
    简单 DOI 规范化：
    - 去掉前缀（doi:, https://doi.org/... 等）
    - 去空白
    - 小写
    """
    if not doi:
        return None
    s = doi.strip()
    s = s.replace("DOI:", "").replace("doi:", "")
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if s.lower().startswith(prefix):
            s = s[len(prefix) :]
            break
    s = "".join(s.split())
    s = s.lower()
    return s or None


def _norm_title(s: str) -> str:
    """
    标题规范化：小写、去掉非字母数字、压缩空白。
    用于严格匹配，宁缺毋滥。
    """
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = " ".join(s.split())
    return s


def _last_name(full_name: str) -> str:
    """
    简单取作者姓：用最后一个 token（英文名场景）。
    """
    parts = full_name.strip().split()
    return parts[-1].lower() if parts else ""


@dataclass
class _ArxivEntry:
    arxiv_id: Optional[str]
    title: Optional[str]
    summary: Optional[str]
    doi: Optional[str]
    authors: List[str]
    pdf_url: Optional[str]
    primary_category: Optional[str]
    categories: List[str]


def _parse_arxiv_feed(xml_text: str) -> List[_ArxivEntry]:
    """
    解析 arXiv ATOM feed，抽取我们需要的字段。
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Failed to parse arXiv Atom feed")
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    entries: List[_ArxivEntry] = []

    for entry_el in root.findall("atom:entry", ns):
        # title
        title_el = entry_el.find("atom:title", ns)
        title = "".join(title_el.itertext()).strip() if title_el is not None else None

        # summary / abstract
        summary_el = entry_el.find("atom:summary", ns)
        summary = "".join(summary_el.itertext()).strip() if summary_el is not None else None

        # id / arxiv_id
        id_el = entry_el.find("atom:id", ns)
        id_url = (id_el.text or "").strip() if id_el is not None else None
        arxiv_id = None
        if id_url:
            # 典型：http://arxiv.org/abs/1234.5678v1
            arxiv_id = id_url.rsplit("/", 1)[-1]

        # authors
        authors: List[str] = []
        for a_el in entry_el.findall("atom:author", ns):
            name_el = a_el.find("atom:name", ns)
            if name_el is not None:
                name = (name_el.text or "").strip()
                if name:
                    authors.append(name)

        # doi
        doi_el = entry_el.find("arxiv:doi", ns)
        doi = (doi_el.text or "").strip() if doi_el is not None else None

        # categories
        categories: List[str] = []
        for cat_el in entry_el.findall("atom:category", ns):
            term = cat_el.get("term")
            if term:
                categories.append(term)

        primary_category = None
        pc_el = entry_el.find("arxiv:primary_category", ns)
        if pc_el is not None:
            t = pc_el.get("term")
            if t:
                primary_category = t

        # pdf link
        pdf_url: Optional[str] = None
        for link_el in entry_el.findall("atom:link", ns):
            href = link_el.get("href")
            title_attr = (link_el.get("title") or "").lower()
            rel = (link_el.get("rel") or "").lower()
            if title_attr == "pdf" and href:
                pdf_url = href
                break
            # 兜底：某些 feed 中 pdf 链接可能标在 rel="related"
            if rel == "related" and href and href.lower().endswith(".pdf"):
                pdf_url = href

        entries.append(
            _ArxivEntry(
                arxiv_id=arxiv_id,
                title=title,
                summary=summary,
                doi=doi,
                authors=authors,
                pdf_url=pdf_url,
                primary_category=primary_category,
                categories=categories,
            )
        )

    return entries


@register_source_type("arxiv")
class ArxivMetadataSource:
    """
    arXiv 元数据源：

    - 优先通过 DOI 检索（search_query=doi:{doi}）；
    - 如果 DOI 无法命中，则通过 "title + first_author" 检索；
    - 匹配成功后返回 preprint 摘要 + PDF URL + arxiv_id 等信息。

    配置示例（sources.yml）：

      - id: arxiv
        type: arxiv
        enabled: true
        provides: ["abstract", "pdf"]
        search_by: ["doi", "title"]
        priority:
          abstract: 70
          pdf: 70
    """

    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg
        self.id = cfg.id
        self.search_by = [s.lower() for s in (cfg.search_by or ["doi", "title"])]

    def _request_feed(self, search_query: str, max_results: int = 5) -> List[_ArxivEntry]:
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
        }
        headers = {
            "User-Agent": ARXIV_USER_AGENT,
        }

        try:
            r = requests.get(ARXIV_API_URL, params=params, headers=headers, timeout=20)
            r.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "arXiv request failed source=%s query=%r: %s",
                self.id,
                search_query,
                exc,
            )
            return []

        return _parse_arxiv_feed(r.text)

    def _match_entry_to_paper(
        self,
        entry: _ArxivEntry,
        paper: Paper,
        require_doi_match: bool = False,
    ) -> bool:
        """
        严格匹配规则：
        - 标题规范化后必须完全一致；
        - 如果 Paper 有 first_author，则要求第一作者姓一致；
        - 如果 require_doi_match=True，则 arxiv entry 的 doi 必须和 Paper.doi 匹配。
        """
        if not entry.title or not paper.title:
            return False

        if require_doi_match:
            if not entry.doi or not paper.doi:
                return False
            if _normalize_doi(entry.doi) != _normalize_doi(paper.doi):
                return False

        if _norm_title(entry.title) != _norm_title(paper.title):
            return False

        first_author = getattr(paper, "first_author", None)
        if first_author and entry.authors:
            paper_last = _last_name(first_author)
            arxiv_first_last = _last_name(entry.authors[0])
            if paper_last and arxiv_first_last and paper_last != arxiv_first_last:
                return False

        return True

    def _match_by_doi(self, paper: Paper) -> Optional[_ArxivEntry]:
        """
        通过 DOI 检索 arXiv：search_query=doi:{doi}，然后严格匹配标题/作者。
        """
        if not paper.doi:
            return None

        norm_doi = _normalize_doi(paper.doi)
        if not norm_doi:
            return None

        search_query = f"doi:{norm_doi}"
        entries = self._request_feed(search_query, max_results=5)
        if not entries:
            return None

        for entry in entries:
            if self._match_entry_to_paper(entry, paper, require_doi_match=True):
                return entry

        return None

    def _match_by_title_author(self, paper: Paper) -> Optional[_ArxivEntry]:
        """
        通过 title + first_author 做 fallback 检索。
        为避免误匹配，这里宁缺毋滥：
        - 搜索式：ti:"title" AND au:"last_name"
        - 然后用 _match_entry_to_paper 做严格匹配。
        """
        if not paper.title:
            return None

        # 为了安全起见，把标题中的双引号去掉，避免干扰搜索语句
        title_for_query = paper.title.replace('"', " ")
        q_parts: List[str] = [f'ti:"{title_for_query}"']

        first_author = getattr(paper, "first_author", None)
        if first_author:
            last = _last_name(first_author)
            if last:
                q_parts.append(f'au:"{last}"')

        search_query = " AND ".join(q_parts)
        entries = self._request_feed(search_query, max_results=5)
        if not entries:
            return None

        candidates: List[_ArxivEntry] = [
            e for e in entries if self._match_entry_to_paper(e, paper, require_doi_match=False)
        ]

        if not candidates:
            return None

        # 出现多个时，简单取第一个（feed 已按相关性排序），你也可以按日期再排序
        return candidates[0]

    def enrich(self, paper: Paper) -> SourceEnrichmentResult:
        """
        使用 fetch-venues 阶段的 Paper，独立向 arXiv 请求 preprint 摘要 / PDF：

        策略：
        1. 如果 search_by 包含 "doi"，先尝试 DOI 检索（严格匹配标题+作者+doi）；
        2. 如果失败且 search_by 包含 "title"，再尝试 title+author fallback；
        3. 成功则返回 preprint 摘要 + PDF + arxiv_id 等 metadata。
        """
        queries = build_default_queries(self.search_by, paper)

        # 1) DOI 优先（仅以 Paper.doi 为准，不依赖其它 source 的结果）
        entry: Optional[_ArxivEntry] = None
        match_method: Optional[str] = None

        doi_query = next((q for q in queries if q.kind == "doi"), None)
        if doi_query and paper.doi:
            entry = self._match_by_doi(paper)
            if entry:
                match_method = "doi"

        # 2) fallback: title + first_author
        if entry is None:
            title_query = next((q for q in queries if q.kind == "title"), None)
            if title_query and paper.title:
                entry = self._match_by_title_author(paper)
                if entry:
                    match_method = "title_author"

        if entry is None:
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        metadata: Dict[str, Any] = {}
        if entry.arxiv_id:
            metadata["arxiv_id"] = entry.arxiv_id
        if entry.primary_category:
            metadata["primary_category"] = entry.primary_category
        if entry.categories:
            metadata["categories"] = entry.categories
        if match_method:
            metadata["match_method"] = match_method

        # 摘要：arxiv summary 直接作为 preprint abstract
        abstract = entry.summary
        pdf_url = entry.pdf_url

        # 置信度：
        # - DOI 严格匹配：1.0
        # - 仅 title+author 匹配：0.9（仍然较高，但次于 DOI）
        confidence = 1.0 if match_method == "doi" else 0.9

        return SourceEnrichmentResult(
            source_id=self.id,
            pdf_url=pdf_url,
            abstract=abstract,
            metadata=metadata,
            confidence=confidence,
        )


if __name__ == "__main__":
    p = Paper(id="10.1038/s41467-025-67481-2", doi="10.1038/s41467-025-67481-2", title="scDrugMap: benchmarking large foundation models for drug response prediction")
    cfg = SourceConfig(id="crossref_refresh", type="crossref", enabled=True, provides= ["metadata", "abstract"], search_by=["doi", "title"])
    c_meta = ArxivMetadataSource(cfg)
    print(c_meta.enrich(p))