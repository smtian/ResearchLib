# src/paper_tracker/sources/pubmed.py
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

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "paper-tracker"
TOOL_EMAIL = "your-email@example.com"  # TODO: 换成你自己的邮箱，符合 NCBI 要求


def _normalize_doi(doi: Optional[str]) -> Optional[str]:
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
    用于 PubMed 标题匹配（尽量严格，宁缺毋滥）。
    """
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = " ".join(s.split())
    return s


@dataclass
class _ArticleInfo:
    pmid: str
    title: Optional[str]
    abstract: Optional[str]
    dois: List[str]
    extra_metadata: Dict[str, Any]


@register_source_type("pubmed")
class PubMedMetadataSource:
    """
    PubMed 元数据源：

    - 优先使用 DOI（[AID]）查 PMID；
    - 如果 DOI 无法命中，fallback 到 "title + first author + year (+journal)" 检索；
    - 匹配成功后，用 EFetch 拉摘要，并收集所有 DOI（包括可能的 preprint DOI）。

    配置示例（sources.yml）：

      - id: pubmed
        type: pubmed
        enabled: true
        provides: ["metadata", "abstract"]
        search_by: ["doi", "title"]
        priority:
          abstract: 90
          pdf: 0
    """

    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg
        self.id = cfg.id
        # 用 search_by 控制是否允许通过 doi / title 检索
        self.search_by = [s.lower() for s in (cfg.search_by or ["doi", "title"])]

    # ---------- E-utilities 基础调用 ----------

    def _esearch_ids(self, term: str, max_results: int = 5) -> List[str]:
        params = {
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retmax": str(max_results),
            "tool": TOOL_NAME,
            "email": TOOL_EMAIL,
        }
        url = f"{EUTILS_BASE}/esearch.fcgi"

        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("PubMed esearch failed source=%s term=%r: %s", self.id, term, exc)
            return []

        try:
            data = r.json()
        except ValueError:
            logger.warning("PubMed esearch JSON decode failed source=%s term=%r", self.id, term)
            return []

        esr = data.get("esearchresult") or {}
        idlist = esr.get("idlist") or []
        if not isinstance(idlist, list):
            return []
        return [s for s in idlist if isinstance(s, str) and s.strip()]

    def _efetch_article(self, pmid: str) -> Optional[_ArticleInfo]:
        """
        用 EFetch 拉取 PubMed 文章详情，解析标题、摘要、DOI 列表等。
        """
        params = {
            "db": "pubmed",
            "id": pmid,
            "retmode": "xml",
            "rettype": "abstract",
            "tool": TOOL_NAME,
            "email": TOOL_EMAIL,
        }
        url = f"{EUTILS_BASE}/efetch.fcgi"

        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("PubMed efetch failed source=%s pmid=%s: %s", self.id, pmid, exc)
            return None

        try:
            root = ET.fromstring(r.text)
        except ET.ParseError:
            logger.warning("PubMed efetch XML parse failed source=%s pmid=%s", self.id, pmid)
            return None

        # 典型结构：PubmedArticleSet/PubmedArticle/MedlineCitation/Article
        article_el = root.find(".//Article")
        if article_el is None:
            return None

        # 标题
        title_el = article_el.find("ArticleTitle")
        if title_el is not None:
            title = "".join(title_el.itertext()).strip()
        else:
            title = None

        # 摘要（可能多段）
        abs_parts: List[str] = []
        for abs_el in article_el.findall(".//Abstract/AbstractText"):
            txt = "".join(abs_el.itertext()).strip()
            if txt:
                abs_parts.append(txt)
        abstract = "\n".join(abs_parts).strip() if abs_parts else None

        # DOI 列表
        dois: List[str] = []
        for id_el in article_el.findall(".//ArticleIdList/ArticleId"):
            id_type = (id_el.get("IdType") or "").lower()
            if id_type == "doi":
                v = (id_el.text or "").strip()
                if v:
                    dois.append(v)

        dois = sorted({d for d in dois if d})

        extra: Dict[str, Any] = {}
        journal_title_el = article_el.find(".//Journal/Title")
        if journal_title_el is not None:
            extra["journal"] = "".join(journal_title_el.itertext()).strip()

        return _ArticleInfo(
            pmid=pmid,
            title=title,
            abstract=abstract,
            dois=dois,
            extra_metadata=extra,
        )

    # ---------- 匹配策略：DOI 优先 + Title fallback ----------

    def _match_by_doi(self, doi: str) -> Optional[_ArticleInfo]:
        norm = _normalize_doi(doi)
        if not norm:
            return None

        term = f"{norm}[AID]"
        ids = self._esearch_ids(term, max_results=3)
        if not ids:
            return None

        # 一般只会返回一个 PMID，用第一个即可
        pmid = ids[0]
        return self._efetch_article(pmid)

    def _match_by_title(self, paper: Paper) -> Optional[_ArticleInfo]:
        """
        用 title + first_author(+year/+journal) 做严格 fallback。
        为避免误匹配，这里宁可 0 结果，也不要模糊命中。
        """
        if not paper.title:
            return None

        term_parts: List[str] = []
        # 精确标题，作为 [ti] 字段
        term_parts.append(f"{paper.title}[ti]")

        # 第一作者姓氏
        first_author = getattr(paper, "first_author", None)
        if first_author:
            last_name = first_author.split()[-1]
            if last_name:
                term_parts.append(f"{last_name}[au]")

        # 发表年份
        year = None
        if getattr(paper, "published_at", None) is not None:
            year = paper.published_at.year
        if year:
            term_parts.append(f"\"{year}\"[dp]")

        # 期刊名（注意 PubMed 用的是期刊缩写，这里只是 best-effort）
        journal = getattr(paper, "journal", None)
        if journal:
            term_parts.append(f"\"{journal}\"[jour]")

        term = " AND ".join(term_parts)
        ids = self._esearch_ids(term, max_results=5)
        if not ids:
            return None

        norm_target = _norm_title(paper.title)

        # 逐个候选 PMID，取第一个标题规范化后完全一致的
        for pmid in ids:
            info = self._efetch_article(pmid)
            if not info or not info.title:
                continue
            if _norm_title(info.title) == norm_target:
                return info

        return None

    # ---------- 对外接口：enrich ----------

    def enrich(self, paper: Paper) -> SourceEnrichmentResult:
        """
        使用 fetch-venues 阶段的 Paper，独立向 PubMed 请求摘要 / metadata。

        策略：
        1. 如果允许按 DOI 检索（search_by 包含 "doi"），先尝试 DOI->[AID]->PMID；
        2. 如果 DOI 匹配失败，且允许按 title 检索，则尝试 title+author(+year/+journal)；
        3. 成功匹配后，返回摘要 + DOI 列表 + PMID 等 metadata；
        4. 匹配失败则 confidence=0.0，仅返回空结果。
        """
        queries = build_default_queries(self.search_by, paper)

        info: Optional[_ArticleInfo] = None
        match_method: Optional[str] = None

        # 1) DOI 优先
        doi_query = next((q for q in queries if q.kind == "doi"), None)
        if doi_query:
            info = self._match_by_doi(doi_query.value)
            if info:
                match_method = "doi"

        # 2) fallback 到 title 检索
        if info is None:
            title_query = next((q for q in queries if q.kind == "title"), None)
            if title_query:
                info = self._match_by_title(paper)
                if info:
                    match_method = "title"

        # 匹配失败
        if info is None:
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        # 构造 metadata：pmid + 所有 DOI（包括可能的 preprint DOI）
        metadata: Dict[str, Any] = dict(info.extra_metadata)
        metadata["pmid"] = info.pmid
        if info.dois:
            metadata["dois"] = info.dois
        if match_method:
            metadata["match_method"] = match_method

        # 置信度：一旦我们通过 DOI 或严格标题检索确认了 PMID，就认为匹配可信
        confidence = 1.0

        return SourceEnrichmentResult(
            source_id=self.id,
            pdf_url=None,              # PubMed 一般不直接作为 PDF 来源
            abstract=info.abstract,    # 可能为 None（少数记录无摘要）
            metadata=metadata,
            confidence=confidence,
        )

if __name__ == "__main__":
    p = Paper(id="10.1038/s41467-025-67481-2", doi="10.1038/s41467-025-67481-2", title="scDrugMap: benchmarking large foundation models for drug response prediction")
    cfg = SourceConfig(id="crossref_refresh", type="crossref", enabled=True, provides= ["metadata", "abstract"], search_by=["doi", "title"])
    c_meta = PubMedMetadataSource(cfg)
    print(c_meta.enrich(p))