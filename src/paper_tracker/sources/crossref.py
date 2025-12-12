from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Dict, Optional, List

import requests

from paper_tracker.config import SourceConfig
from paper_tracker.models import Paper
from paper_tracker.sources.base import (
    SourceEnrichmentResult,
    build_default_queries,
)
from paper_tracker.sources.base import register_source_type  # 按你当前结构保留

logger = logging.getLogger(__name__)

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
CROSSREF_USER_AGENT = "paper-tracker/0.1 (mailto:your-email@example.com)"


_TAG_RE = re.compile(r"<[^>]+>")
_JATS_P_RE = re.compile(r"</?jats:p[^>]*>", re.IGNORECASE)


def _normalize_doi(doi: Optional[str]) -> Optional[str]:
    """
    将 DOI 规范化为 Crossref 可接受的形式：
    - 去掉前后空白
    - 去掉 doi: / https://doi.org/ / http(s)://dx.doi.org/ 前缀
    - 压缩内部空白
    - 转为小写
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

    # 去掉所有空白
    s = "".join(s.split())
    s = s.lower()
    return s or None


def _clean_abstract(text: str) -> str:
    """
    简单清洗 Crossref 返回的 abstract（通常带 JATS 标签）：
    - HTML unescape
    - 去掉 <jats:p> 标签
    - 去掉所有残余标签
    - 压缩多余空白
    """
    if not text:
        return text

    s = unescape(text)
    s = _JATS_P_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = " ".join(s.split())
    return s


def _extract_pdf_url(msg: Dict[str, Any]) -> Optional[str]:
    """
    从 Crossref message.link 中尝试找到 PDF URL。
    更适合作为「补充信息」，主 PDF 仍建议依赖 pmc / arxiv / biorxiv。
    """
    links = msg.get("link") or []
    if isinstance(links, list):
        # 优先 content-type 包含 pdf 的链接
        for lk in links:
            if not isinstance(lk, dict):
                continue
            ct = (lk.get("content-type") or "").lower()
            if "pdf" in ct:
                url = lk.get("URL") or lk.get("url")
                if url:
                    return url
        # 其次任意有 URL 的 link
        for lk in links:
            if not isinstance(lk, dict):
                continue
            url = lk.get("URL") or lk.get("url")
            if url:
                return url

    # 最后兜底：如果 message.URL 本身看起来像 pdf
    url = msg.get("URL")
    if isinstance(url, str) and url.lower().endswith(".pdf"):
        return url

    return None


def _extract_relations(msg: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    解析 Crossref 的 relation 字段，特别关注 preprint ↔ journal 的 DOI 关系。

    返回：
    {
        "preprint_dois": [...],
        "related_dois": [...],
    }

    注意：实际生产中 relation 常常为空，因此这里的结果只作为 metadata 暴露，
    是否使用由上层汇总逻辑自行决定，不在本 source 内部做跨 source 协调。
    """
    result: Dict[str, List[str]] = {
        "preprint_dois": [],
        "related_dois": [],
    }

    rel = msg.get("relation") or {}
    if not isinstance(rel, dict):
        return result

    for rel_type, entries in rel.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            id_type = (entry.get("id-type") or "").lower()
            target_id = entry.get("id")
            if id_type != "doi" or not target_id:
                continue
            target_doi = _normalize_doi(target_id)
            if not target_doi:
                continue

            # 基于 Crossref 的约定：
            # - has-preprint: 当前记录是 journal，指向 preprint DOI
            # - is-preprint-of: 当前记录是 preprint，指向 journal DOI
            if rel_type == "has-preprint":
                result["preprint_dois"].append(target_doi)
            elif rel_type == "is-preprint-of":
                result["related_dois"].append(target_doi)
            else:
                # 其它关系一律归入 related_dois，让上层自己解读
                result["related_dois"].append(target_doi)

    # 去重
    result["preprint_dois"] = sorted(set(result["preprint_dois"]))
    result["related_dois"] = sorted(set(result["related_dois"]))

    return result


@register_source_type("crossref")
class CrossrefMetadataSource:
    """
    Crossref 元数据源，用于：

    - 根据 DOI 拉取最新 metadata（包括摘要、publisher、container-title 等）；
    - 尝试抽取 PDF 链接（如有，多数作为补充信息）；
    - 解析 preprint ↔ journal 的 DOI 关系（relation.has-preprint / is-preprint-of），
      仅作为 metadata 暴露，不在本 source 内部驱动其他 source 调用。

    配置示例（sources.yml）：

      - id: crossref_refresh
        type: crossref
        enabled: true
        provides: ["metadata", "abstract"]
        search_by: ["doi"]
        priority:
          abstract: 30
          pdf: 5
    """

    def __init__(self, cfg: SourceConfig) -> None:
        self.cfg = cfg
        self.id = cfg.id
        self.base_url = (cfg.base_url or CROSSREF_WORKS_URL).rstrip("/")
        # 默认只按 doi 搜索；如果你将来希望扩展为 title 查询，可以在 sources.yml 中加对应的 search_by
        self.search_by = [s.lower() for s in (cfg.search_by or ["doi"])]

    def enrich(self, paper: Paper) -> SourceEnrichmentResult:
        """
        使用 fetch-venues 阶段产出的 Paper（主要是 DOI）独立向 Crossref 请求元数据。

        不做跨 source 协调；只返回本 source 能看到的 abstract/pdf/metadata，
        confidence 简化为：「请求成功（找到记录）=1.0，失败=0.0」。
        具体重要性由 sources.yml 中的 priority 来决定。
        """
        # 1. 根据 search_by 构建查询列表，目前 Crossref 只真正使用 doi
        queries = build_default_queries(self.search_by, paper)
        doi_query = next((q for q in queries if q.kind == "doi"), None)
        if not doi_query:
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        doi_norm = _normalize_doi(doi_query.value)
        if not doi_norm:
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        url = f"{self.base_url}/{doi_norm}"
        headers = {"User-Agent": CROSSREF_USER_AGENT}

        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "Crossref request failed source=%s doi=%s: %s",
                self.id,
                doi_norm,
                exc,
            )
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        try:
            data = resp.json()
        except ValueError:
            logger.warning(
                "Crossref JSON decode failed source=%s doi=%s",
                self.id,
                doi_norm,
            )
            return SourceEnrichmentResult(source_id=self.id, confidence=0.0)

        msg: Dict[str, Any] = data.get("message") or {}

        # 2. 摘要（可能为空）
        abstract_raw = msg.get("abstract")
        abstract = _clean_abstract(abstract_raw) if abstract_raw else None

        # 3. PDF URL 候选（通常不作为主 PDF，只是补充）
        pdf_url = _extract_pdf_url(msg)

        # 4. metadata 聚合（只放小字段；大字段不在这里处理）
        metadata: Dict[str, Any] = {}

        if msg.get("URL"):
            metadata["crossref_url"] = msg["URL"]
        if msg.get("publisher"):
            metadata["publisher"] = msg["publisher"]

        container = msg.get("container-title")
        if isinstance(container, list) and container:
            metadata["journal"] = container[0]

        issns = msg.get("ISSN")
        if isinstance(issns, list) and issns:
            metadata["issn"] = issns

        # 5. preprint / journal DOI 关系解析（作为可选 metadata 暴露）
        rel_info = _extract_relations(msg)
        if rel_info["preprint_dois"]:
            metadata["preprint_dois"] = rel_info["preprint_dois"]
        if rel_info["related_dois"]:
            metadata["related_dois"] = rel_info["related_dois"]

        # 6. 置信度：简化为「请求成功=1.0，失败=0.0」
        # 是否采用其 abstract/pdf 由上层根据 priority 决定
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
    cfg = SourceConfig(id="crossref_refresh", type="crossref", enabled=True, provides= ["metadata", "abstract"], search_by=["doi"])
    c_meta = CrossrefMetadataSource(cfg)
    print(c_meta.enrich(p))