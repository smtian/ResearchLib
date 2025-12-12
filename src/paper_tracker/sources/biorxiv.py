# src/paper_tracker/sources/biorxiv.py
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from paper_tracker.config import SourceConfig
from paper_tracker.models import Paper
from paper_tracker.sources.base import (
    SourceEnrichmentResult,
    build_default_queries,
    register_source_type,
)

logger = logging.getLogger(__name__)

BIORXIV_API_BASE = "https://api.biorxiv.org"
BIORXIV_SERVER = "biorxiv"


def _normalize_doi(doi: Optional[str]) -> Optional[str]:
    """
    简单 DOI 规范化：
    - 去掉常见前缀（doi:, https://doi.org/... 等）
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


@register_source_type("biorxiv")
class BiorxivMetadataSource:
    """
    bioRxiv 元数据源：

    主要用途：
    - 根据 DOI（可以是 preprint DOI，或最终期刊的 published DOI）从 /pubs 接口拿到：
      * preprint_title / preprint_abstract
      * biorxiv_doi / published_doi
      * preprint_date / published_date
      * preprint_authors / preprint_category / published_journal 等

    设计原则：
    - 只依赖 fetch-venues 阶段的 Paper（即期刊侧的 DOI 等），不依赖其它 source 的结果；
    - 尽量把「preprint ↔ journal」链路信息放到 metadata 中，方便上层在 merge 阶段综合使用；
    - 目前 bioRxiv 官方 API 没有提供 title/author JSON 检索端点，因此这里不做真正的
      “title+author fallback 搜索”，而是专注于 DOI→preprint 的高质量映射。

    配置示例（sources.yml）：

      - id: biorxiv
        type: biorxiv
        enabled: true
        provides: ["abstract", "metadata"]
        search_by: ["doi"]
        priority:
          abstract: 80
          pdf: 0      # PDF 建议仍主要依赖 pmc/arxiv 等 OA 源
    """

    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg
        self.id = cfg.id
        # 当前 bioRxiv API 只可靠地支持 DOI 检索；search_by 中的其他字段忽略
        self.search_by = [s.lower() for s in (cfg.search_by or ["doi"])]

    # ---------- 调用 /pubs/biorxiv/{DOI}/na/json ----------

    def _fetch_pubs_record(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        调用 https://api.biorxiv.org/pubs/biorxiv/{DOI}/na/json

        支持：
        - DOI 是 preprint DOI；
        - DOI 是 published DOI；
        两种情况都会返回 preprint 相关 metadata。
        """
        norm_doi = _normalize_doi(doi)
        if not norm_doi:
            return None

        url = f"{BIORXIV_API_BASE}/pubs/{BIORXIV_SERVER}/{norm_doi}/na/json"

        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "biorxiv /pubs request failed source=%s doi=%s: %s",
                self.id,
                norm_doi,
                exc,
            )
            return None

        try:
            data = resp.json()
        except ValueError:
            logger.warning(
                "biorxiv /pubs JSON decode failed source=%s doi=%s",
                self.id,
                norm_doi,
            )
            return None

        coll = data.get("collection") or []
        if not isinstance(coll, list) or not coll:
            return None

        # /pubs/{DOI}/na/json 理论上只会返回一个逻辑记录（可能包含多个版本），
        # 但为了稳妥起见，我们仍然遍历一次，优先匹配 published_doi / biorxiv_doi。
        best: Optional[Dict[str, Any]] = None

        for rec in coll:
            if not isinstance(rec, dict):
                continue
            biorxiv_doi = rec.get("biorxiv_doi")
            published_doi = rec.get("published_doi")

            # 正常情况：published_doi 与输入 DOI 匹配
            if published_doi and _normalize_doi(published_doi) == norm_doi:
                best = rec
                break

            # 如果输入 DOI 本身就是 preprint DOI，则可能匹配 biorxiv_doi
            if biorxiv_doi and _normalize_doi(biorxiv_doi) == norm_doi:
                best = rec
                # 不 break，先继续看是否有 published_doi 的更明确记录
                continue

            # 若都不完全匹配，就先保留第一个合法记录（兜底）
            if best is None:
                best = rec

        return best

    # ---------- 对外接口：enrich ----------

    def enrich(self, paper: Paper) -> SourceEnrichmentResult:
        """
        使用 fetch-venues 阶段的 Paper 中的 DOI，通过 /pubs 接口尝试找到对应的 bioRxiv preprint。

        策略：
        1. 如果 search_by 包含 "doi" 且 Paper 有 doi，则尝试 /pubs/biorxiv/{doi}/na/json；
        2. 找到记录后，将 preprint_abstract 作为摘要；同时把 preprint/journal 关联信息
           放入 metadata（biorxiv_doi/published_doi 等）；
        3. 暂不构造 PDF URL（PDF 主要由 pmc / arxiv 等 source 提供）。
        """
        queries = build_default_queries(self.search_by, paper)

        doi_query = next((q for q in queries if q.kind == "doi"), None)
        if not doi_query or not doi_query.value:
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        rec = self._fetch_pubs_record(str(doi_query.value))
        if rec is None:
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        # 提取 preprint 相关字段
        biorxiv_doi = rec.get("biorxiv_doi") or None
        published_doi = rec.get("published_doi") or None
        preprint_title = rec.get("preprint_title") or None
        preprint_authors = rec.get("preprint_authors") or None
        preprint_abstract = rec.get("preprint_abstract") or None
        preprint_category = rec.get("preprint_category") or None
        preprint_date = rec.get("preprint_date") or None
        published_date = rec.get("published_date") or None
        published_journal = rec.get("published_journal") or None
        preprint_platform = rec.get("preprint_platform") or None

        metadata: Dict[str, Any] = {}

        if biorxiv_doi:
            metadata["biorxiv_doi"] = biorxiv_doi
        if published_doi:
            metadata["published_doi"] = published_doi
        if preprint_title:
            metadata["preprint_title"] = preprint_title
        if preprint_authors:
            metadata["preprint_authors"] = preprint_authors
        if preprint_category:
            metadata["preprint_category"] = preprint_category
        if preprint_date:
            metadata["preprint_date"] = preprint_date
        if published_date:
            metadata["published_date"] = published_date
        if published_journal:
            metadata["published_journal"] = published_journal
        if preprint_platform:
            metadata["preprint_platform"] = preprint_platform

        # 上层可以使用这些 metadata 做：
        # - preprint ↔ journal DOI 映射；
        # - 用 preprint_title 与 Paper.title 做相似度比对；
        # - 结合 PubMed / arxiv 等 source 做更强的 title+author 联合验证。

        # 摘要：直接使用 preprint_abstract（可能为 None）
        abstract = preprint_abstract

        # pdf_url 暂不构造（bioRxiv PDF URL 依赖版本号，且模式可能调整）
        pdf_url: Optional[str] = None

        # 只要能找到一条 /pubs 记录，就认为匹配可靠，confidence=1.0；
        # 真正的“重要性”由 sources.yml 中的 priority 控制。
        confidence = 1.0

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
    c_meta = BiorxivMetadataSource(cfg)
    print(c_meta.enrich(p))