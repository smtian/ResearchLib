# src/paper_tracker/models.py
from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional


ScoreComponents = Dict[str, float]


@dataclass
class ScoringResult:
    """
    一次完整打分的结果，用于写入 features 层或内部计算。

    - total_score: 综合得分
    - components: 各子打分组件，例如 {"keyword_title": 10.0, "keyword_abs": 12.0, "venue": 5.0}
    - tags: 从关键词/规则中抽取出的标签，用于标记主题/方法等
    - version: 打分算法版本，例如 "v1" / "v2"
    """
    total_score: float
    components: ScoreComponents = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    version: str = "v1"

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可写入 JSON 的字典。"""
        return {
            "total_score": self.total_score,
            "components": dict(self.components),
            "tags": list(self.tags),
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScoringResult":
        """从字典反序列化，忽略未知字段。"""
        return cls(
            total_score=float(data.get("total_score", 0.0)),
            components=dict(data.get("components", {})) if data.get("components") else {},
            tags=list(data.get("tags", [])) if data.get("tags") else [],
            version=str(data.get("version", "v1")),
        )


@dataclass
class Paper:
    """
    论文主索引对象，对应 data/index/papers.jsonl 中的一行。

    核心字段：
    - id: 内部唯一 ID（优先使用 DOI，或 title+journal 的 hash）
    - title: 标题
    - doi: DOI（如有）
    - journal: 期刊名
    - venue_id: 对应 venues.yml 中的 id
    - url: 期刊页面 URL
    - pdf_url: 解析得到的开放获取 PDF 链接（如有）
    - short_abstract: 摘要的精简版本，用于展示和轻量重算
    - score_summary: 简要打分结果（可以只保留 total_score + tags 等）
    - score_version: 当前索引中使用的打分版本（便于筛选/重打分）
    - authors: 作者列表（按原始顺序）
    - first_author: 第一作者（可选，通常取 authors[0]）
    - published_at: 发表时间（可选）
    - created_at: 首次被系统收录的时间
    - updated_at: 最近一次被更新（打分/解析 PDF 等）的时间
    """
    id: str
    title: str
    doi: Optional[str] = None
    journal: Optional[str] = None
    venue_id: Optional[str] = None

    url: Optional[str] = None
    pdf_url: Optional[str] = None
    
    abstract_source: Optional[str] = None
    # oa_status: Optional[str] = None
    # oa_source: Optional[str] = None
    metadata_sources: List[str] = field(default_factory=list)
    enrichment_version: Optional[str] = None

    short_abstract: Optional[str] = None

    score_summary: Optional[Dict[str, Any]] = None
    score_version: Optional[str] = None

    authors: List[str] = field(default_factory=list)
    first_author: Optional[str] = None
    published_at: Optional[datetime] = None

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def touch_updated(self) -> None:
        """更新时间戳，在修改字段后调用。"""
        self.updated_at = datetime.now(timezone.utc)

    # ===== 序列化/反序列化，便于 JSONL 存储 =====

    def to_dict(self) -> Dict[str, Any]:
        """
        将 Paper 对象转为可写入 JSON 的字典：
        - datetime 字段转为 ISO8601 字符串
        - 其余使用 dataclasses.asdict
        """
        data = asdict(self)

        for key in ("published_at", "created_at", "updated_at"):
            value = data.get(key)
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            elif value is None:
                data[key] = None

        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Paper":
        """
        从字典构建 Paper：
        - 只使用已知字段，忽略未知字段
        - 将 ISO8601 字符串解析为 datetime
        """
        field_names = {f.name for f in fields(cls)}
        kwargs: Dict[str, Any] = {}

        for name in field_names:
            if name in ("published_at", "created_at", "updated_at"):
                raw = data.get(name)
                if raw is None:
                    kwargs[name] = None if name == "published_at" else datetime.now(timezone.utc)
                elif isinstance(raw, datetime):
                    kwargs[name] = raw
                else:
                    try:
                        kwargs[name] = datetime.fromisoformat(str(raw))
                    except Exception:
                        # 解析失败时退回当前时间（或 None）
                        kwargs[name] = (
                            None if name == "published_at" else datetime.now(timezone.utc)
                        )
            else:
                if name in data:
                    kwargs[name] = data[name]

        # 必需字段如果缺失，会在这里抛出 TypeError，调用方可以捕获
        return cls(**kwargs)  # type: ignore[arg-type]
