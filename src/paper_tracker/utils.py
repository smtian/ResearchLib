# src/paper_tracker/utils.py
from __future__ import annotations

import json
import gzip
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Tuple, Optional

# -------for venues---------
import argparse


# -------for sources---------
from paper_tracker.config import load_sources, SourceConfig
from paper_tracker.models import Paper
from paper_tracker.sources.base import SourceEnrichmentResult, create_source

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, force=True)

# 统一的富集版本号（后续策略调整时可以升级这个）
ENRICHMENT_VERSION = "v1"

# full abstract 冷存储目录
COLD_ABSTRACT_BASE = Path("data/cold/abstracts")


def _effective_days_back_for_venue(args: argparse.Namespace, venue) -> int:
    """
    计算某个 venue 最终使用的 days_back：

    优先级：
    1. CLI 显式传入 --days-back
    2. venues.yml 中该 venue 的 fetcher_params.days_back
    3. 一个安全的兜底默认值（例如 3）
    """
    if args.days_back is not None:
        return args.days_back

    # 来自配置的默认值
    cfg_days = None
    if getattr(venue, "fetcher_params", None) is not None:
        cfg_days = getattr(venue.fetcher_params, "days_back", None)

    if isinstance(cfg_days, int) and cfg_days > 0:
        return cfg_days

    # 最后兜底
    return 3



def build_metadata_sources() -> Tuple[Dict[str, SourceConfig], Dict[str, object]]:
    """
    根据 sources.yml 创建启用的 metadata sources 实例。

    使用 sources.base 中的注册表 + create_source：
      - cfg_by_id: source_id -> SourceConfig
      - instances_by_id: source_id -> source 实例
    """
    sources_cfg = load_sources()

    cfg_by_id: Dict[str, SourceConfig] = {}
    instances_by_id: Dict[str, object] = {}

    for cfg in sources_cfg.sources:
        if not cfg.enabled:
            continue

        try:
            instance = create_source(cfg)
        except ValueError as exc:
            # 未注册的 type 或构造失败都在这里统一处理
            logger.warning("Failed to create source '%s': %s", cfg.id, exc)
            continue

        cfg_by_id[cfg.id] = cfg
        instances_by_id[cfg.id] = instance

    return cfg_by_id, instances_by_id


def select_best_fields(
    cfg_by_id: Dict[str, SourceConfig],
    results: Dict[str, SourceEnrichmentResult],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    根据 sources.yml 中的 priority * confidence，分别选出最佳 pdf_url 和 abstract。

    返回：
      (best_pdf_url, pdf_source_id, best_abstract, abstract_source_id)
    """
    best_pdf_url = None
    best_pdf_source = None
    best_pdf_weight = 0.0

    best_abstract = None
    best_abs_source = None
    best_abs_weight = 0.0

    for sid, res in results.items():
        cfg = cfg_by_id.get(sid)
        if cfg is None:
            continue

        prio = cfg.priority or {}

        # 选择 PDF
        if res.pdf_url:
            weight = float(prio.get("pdf", 0)) * float(res.confidence)
            if weight > best_pdf_weight:
                best_pdf_weight = weight
                best_pdf_url = res.pdf_url
                best_pdf_source = sid

        # 选择摘要
        if res.abstract:
            weight = float(prio.get("abstract", 0)) * float(res.confidence)
            if weight > best_abs_weight:
                best_abs_weight = weight
                best_abstract = res.abstract
                best_abs_source = sid
    
    return best_pdf_url, best_pdf_source, best_abstract, best_abs_source


def paper_needs_enrichment(paper: Paper, since_days: int, now: datetime) -> bool:
    """
    判断某篇 paper 是否需要执行元数据富集。

    规则（尽量简单可解释）：
      1. 如果缺 pdf_url → 一定需要；
      2. 如果缺 abstract_source → 一定需要；
      3. 如果 enrichment_version != 当前版本 → 一定需要；
      4. 否则：
         - since_days <= 0 → 不再按时间过滤，认为都可以跳过；
         - since_days > 0 → 仅在 updated_at / created_at 在最近 since_days 内时才需要。
    """
    # 1) 缺关键字段 → 必须富集
    if getattr(paper, "pdf_url", None) is None:
        return True
    if getattr(paper, "abstract_source", None) is None:
        return True

    ev = getattr(paper, "enrichment_version", None)
    if ev != ENRICHMENT_VERSION:
        return True

    # 2) 没有时间窗口限制，则到这里说明已经富集过且版本一致，可以跳过
    if since_days <= 0:
        return False

    cutoff = now - timedelta(days=since_days)
    ts = getattr(paper, "updated_at", None) or getattr(paper, "created_at", None)
    if ts is None:
        # 没有时间信息时，保守起见做一次富集
        return True

    try:
        return ts >= cutoff
    except TypeError:
        # 混用 naïve/aware datetime 时，简单起见认为需要
        return True
    

def store_full_abstract(paper_id: str, source_id: str, abstract: str) -> None:
    """
    full abstract 冷存储：按 YYYY-MM 分片写入 data/cold/abstracts/YYYY-MM.jsonl.gz
    """
    if not abstract:
        return

    COLD_ABSTRACT_BASE.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    shard_name = now.strftime("%Y-%m") + ".jsonl.gz"
    shard_path = COLD_ABSTRACT_BASE / shard_name

    record = {
        "paper_id": paper_id,
        "source_id": source_id,
        "abstract": abstract,
        "enrichment_version": ENRICHMENT_VERSION,
        "fetched_at": now.isoformat(),
    }

    with gzip.open(shard_path, "at", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def load_full_abstract(paper_id: str) -> Optional[str]:
    """
    从冷存储中读取指定 paper_id 的 full abstract。

    - 遍历 data/cold/abstracts/*.jsonl.gz 分片；
    - 过滤出 paper_id 匹配的记录；
    - 如果有多条，按 fetched_at 取最新一条；
    - 找不到则返回 None。

    目前是简单实现：线性扫描所有分片。
    后续如果摘要量级很大，可以再加索引或缓存优化。
    """
    if not paper_id:
        return None

    if not COLD_ABSTRACT_BASE.exists():
        return None

    latest_abstract: Optional[str] = None
    latest_ts: Optional[datetime] = None

    # 遍历所有分片文件（按文件名排序只是为了稳定性）
    for shard_path in sorted(COLD_ABSTRACT_BASE.glob("*.jsonl.gz")):
        try:
            with gzip.open(shard_path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        # 略过坏行
                        continue

                    if record.get("paper_id") != paper_id:
                        continue

                    abstract = record.get("abstract")
                    if not abstract:
                        continue

                    fetched_at_raw = record.get("fetched_at")
                    ts: Optional[datetime] = None
                    if fetched_at_raw:
                        try:
                            ts = datetime.fromisoformat(fetched_at_raw)
                        except ValueError:
                            ts = None

                    # 选 fetched_at 最新的一条记录
                    if latest_abstract is None:
                        latest_abstract = abstract
                        latest_ts = ts
                    else:
                        if ts is not None and (latest_ts is None or ts > latest_ts):
                            latest_abstract = abstract
                            latest_ts = ts
        except FileNotFoundError:
            # 分片刚好被删除，忽略即可
            continue

    return latest_abstract
