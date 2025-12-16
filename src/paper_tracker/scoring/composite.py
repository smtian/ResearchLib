from __future__ import annotations

from typing import Optional, Sequence, Dict

from paper_tracker.models import Paper, ScoringResult
from .base import Scorer, KeywordTitleScorer, KeywordAbstractScorer, VenueScorer


# 整体打分流水线的版本号（与单个 scorer 的 version 区分开）
SCORING_PIPELINE_VERSION = "v1"


def get_default_scorers() -> list[Scorer]:
    """
    默认使用的打分器列表。
    目前包含：
      - 标题关键词打分
      - 摘要关键词打分
      - 期刊 tier 打分
    """
    return [
        KeywordTitleScorer(),
        KeywordAbstractScorer(),
        VenueScorer(),
    ]


def score_paper(
    paper: Paper,
    full_abstract: Optional[str],
    scorers: Optional[Sequence[Scorer]] = None,
) -> ScoringResult:
    """
    使用给定的一组 Scorer 对单篇论文进行打分，并生成综合 ScoringResult。

    返回：
        - total_score: 所有 scorer total_score 之和
        - components: {scorer_id: component_score}
        - version: 整体流水线版本号（SCORING_PIPELINE_VERSION）
    """
    if scorers is None:
        scorers = get_default_scorers()

    components: Dict[str, float] = {}
    total = 0.0

    for scorer in scorers:
        partial = scorer.score(paper, full_abstract)
        # 约定：partial.total_score 就是该 scorer 的分数
        comp_score = float(partial.total_score)
        components[scorer.id] = components.get(scorer.id, 0.0) + comp_score
        total += comp_score

    return ScoringResult(
        total_score=total,
        components=components,
        version=SCORING_PIPELINE_VERSION,
    )
