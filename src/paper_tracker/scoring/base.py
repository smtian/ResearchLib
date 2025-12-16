from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol, Optional, Dict, Any, Iterable, List

import re

from paper_tracker.models import Paper, ScoringResult
from paper_tracker.config import load_keywords, load_venues


# =====================
# 工具函数
# =====================

_WORD_SPLIT_RE = re.compile(r"\W+", re.UNICODE)


def _normalize_text(text: str) -> str:
    """
    简单文本归一化：小写 + 去掉多余空白。
    """
    text = (text or "").lower()
    # 可以只用 lower + strip；如果后面需要更复杂的匹配再扩展
    return " ".join(text.split())


# =====================
# Scorer 抽象
# =====================

class Scorer(Protocol):
    """
    所有打分器的统一接口。
    """

    id: str               # 组件 ID，例如 "keyword_title"
    version: str          # 组件自身版本号，例如 "v1"

    def score(self, paper: Paper, full_abstract: Optional[str]) -> ScoringResult:
        """
        对单篇论文打分。

        参数：
            paper: 基本元数据来自 papers.jsonl。
            full_abstract: 从冷存储读取的全文摘要；如果没有则为 None。

        返回：
            ScoringResult，其中 total_score 通常就是该组件的分数，
            components 只需要包含自身组件即可，由 composite 统一汇总。
        """
        ...


# =====================
# 关键词配置加载
# =====================

@lru_cache()
def _load_keyword_config() -> Any:
    """
    封装一下，避免每次都从磁盘读配置。
    兼容 dataclass/pydantic 模型或 dict 返回值。
    """
    return load_keywords()


def _get_cfg_attr(cfg: Any, key: str, default: Iterable[str] | None = None) -> List[str]:
    """
    兼容 dataclass / pydantic 模型和 dict 的字段访问。
    """
    if default is None:
        default = []
    if hasattr(cfg, key):
        value = getattr(cfg, key) or default
    elif isinstance(cfg, dict):
        value = cfg.get(key, default) or default
    else:
        value = default
    return [str(x).strip() for x in value if str(x).strip()]


# =====================
# KeywordTitleScorer
# =====================

class KeywordTitleScorer:
    """
    基于标题的关键词打分。

    规则（按你的要求）：
    - keywords.yml 中有 include_keywords_1 / include_keywords_2 两组。
    - 必须“同时命中”两组（即文本中至少包含一条组1中的关键词，且至少包含一条组2中的关键词），才产生正分。
    - 简单实现：score = 命中组1的不同关键词数量 + 命中组2的不同关键词数量。
    """

    id = "keyword_title"
    version = "v1"

    def __init__(self) -> None:
        cfg = _load_keyword_config()
        self.include_1 = [kw.lower() for kw in _get_cfg_attr(cfg, "include_keywords_1")]
        self.include_2 = [kw.lower() for kw in _get_cfg_attr(cfg, "include_keywords_2")]

    def _match_keywords(self, text: str, keywords: List[str]) -> List[str]:
        text_norm = _normalize_text(text)
        hits = []
        for kw in keywords:
            if kw and kw in text_norm:
                hits.append(kw)
        # 去重
        return sorted(set(hits))

    def score(self, paper: Paper, full_abstract: Optional[str]) -> ScoringResult:
        title = paper.title or ""
        if not title or (not self.include_1 and not self.include_2):
            score_value = 0.0
            return ScoringResult(
                total_score=score_value,
                components={self.id: score_value},
                version=self.version,
            )

        hits_1 = self._match_keywords(title, self.include_1)
        hits_2 = self._match_keywords(title, self.include_2)

        if not hits_1 or not hits_2:
            score_value = 0.0
        else:
            score_value = float(len(hits_1) + len(hits_2))

        return ScoringResult(
            total_score=score_value,
            components={self.id: score_value},
            version=self.version,
        )


# =====================
# KeywordAbstractScorer
# =====================

class KeywordAbstractScorer:
    """
    基于摘要的关键词打分。

    规则同 KeywordTitleScorer：
    - 摘要文本中必须同时命中 include_keywords_1 和 include_keywords_2，才计分。
    - 若没有 full_abstract，则直接返回 0 分。
    """

    id = "keyword_abstract"
    version = "v1"

    def __init__(self) -> None:
        cfg = _load_keyword_config()
        self.include_1 = [kw.lower() for kw in _get_cfg_attr(cfg, "include_keywords_1")]
        self.include_2 = [kw.lower() for kw in _get_cfg_attr(cfg, "include_keywords_2")]

    def _match_keywords(self, text: str, keywords: List[str]) -> List[str]:
        text_norm = _normalize_text(text)
        hits = []
        for kw in keywords:
            if kw and kw in text_norm:
                hits.append(kw)
        return sorted(set(hits))

    def score(self, paper: Paper, full_abstract: Optional[str]) -> ScoringResult:
        if not full_abstract:
            score_value = 0.0
            return ScoringResult(
                total_score=score_value,
                components={self.id: score_value},
                version=self.version,
            )

        hits_1 = self._match_keywords(full_abstract, self.include_1)
        hits_2 = self._match_keywords(full_abstract, self.include_2)

        if not hits_1 or not hits_2:
            score_value = 0.0
        else:
            score_value = float(len(hits_1) + len(hits_2))

        return ScoringResult(
            total_score=score_value,
            components={self.id: score_value},
            version=self.version,
        )


# =====================
# VenueScorer
# =====================

@lru_cache()
def _load_venue_tiers() -> Dict[str, str]:
    """
    从 venues.yml 中取出每个 venue 的 tier，返回 {venue_id: tier_str}。
    """
    cfg = load_venues()

    if hasattr(cfg, "venues"):
        venues_iter = getattr(cfg, "venues")
    elif isinstance(cfg, dict):
        venues_iter = cfg.get("venues", [])
    else:
        venues_iter = []

    mapping: Dict[str, str] = {}
    for v in venues_iter:
        if hasattr(v, "id"):
            vid = getattr(v, "id", None)
        elif isinstance(v, dict):
            vid = v.get("id")
        else:
            vid = None

        if not vid:
            continue

        if hasattr(v, "tier"):
            tier = getattr(v, "tier", None)
        elif isinstance(v, dict):
            tier = v.get("tier")
        else:
            tier = None

        if tier:
            mapping[str(vid)] = str(tier)

    return mapping


def _tier_to_score(tier: Optional[str]) -> float:
    """
    将 venues.yml 中的 tier 映射为数值分数。

    这里给一个默认映射，你可以根据自己的期刊分层再微调：
        S   -> 30
        A+  -> 25
        A   -> 20
        A-  -> 18
        B+  -> 15
        B   -> 10
        C   -> 5
        其它/缺失 -> 0
    """
    if not tier:
        return 0.0
    t = tier.strip().lower()

    mapping = {
        "a+": 10.0,
        "a": 9.5,
        "b": 8.5,
    }

    return mapping.get(t, 0.0)


class VenueScorer:
    """
    按照 venues.yml 中定义的 tier 对期刊进行打分。

    - 从 load_venues() 中读取每个 venue 的 tier 字段；
    - 通过 venue_id 找到对应 tier，再映射成数值分数。
    """

    id = "venue"
    version = "v1"

    def __init__(self) -> None:
        self._tiers = _load_venue_tiers()

    def score(self, paper: Paper, full_abstract: Optional[str]) -> ScoringResult:
        venue_id = getattr(paper, "venue_id", None)
        tier = self._tiers.get(venue_id) if venue_id else None
        score_value = _tier_to_score(tier)

        return ScoringResult(
            total_score=score_value,
            components={self.id: score_value},
            version=self.version,
        )
