# src/paper_tracker/cli.py
from __future__ import annotations

import sys
import argparse
import logging
from datetime import datetime, timezone
from typing import Optional

# ----------for venues--------------
from paper_tracker.config import load_venues
from paper_tracker.venues import create_fetchers_for_config
from paper_tracker.storage import upsert_papers, load_all_papers
from paper_tracker.utils import _effective_days_back_for_venue

# ----------for sources--------------
from paper_tracker.utils import (
    build_metadata_sources,
    store_full_abstract,
    select_best_fields,
    paper_needs_enrichment,
    ENRICHMENT_VERSION,
)
from paper_tracker.sources.base import SourceEnrichmentResult

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, force=True)

def _cmd_fetch_venues(args: argparse.Namespace) -> None:
    """
    拉取期刊最新论文：
    - 从 venues.yml 加载期刊配置
    - 为每个 venue 创建对应 fetcher
    - 调用 fetch_recent(days_back)
    - 通过 upsert_papers() 写入索引并去重
    """
    venues_cfg = load_venues()
    fetchers = create_fetchers_for_config(venues_cfg)

    total_fetched = 0
    total_new = 0

    for fetcher in fetchers:
        venue = fetcher.venue
        venue_id = venue.id

        days_back = _effective_days_back_for_venue(args, venue)
        candidates = fetcher.fetch_recent(days_back=days_back)
        fetched_count = len(candidates)
        total_fetched += fetched_count

        new_papers = upsert_papers(candidates)
        new_count = len(new_papers)
        total_new += new_count

        print(
            f"[fetch-venues] venue={venue_id} days_back={days_back} "
            f"fetched={fetched_count} new={new_count}"
        )

    print(
        f"[fetch-venues] total_fetched={total_fetched} total_new={total_new}"
    )

def _cmd_enrich_metadata(args: argparse.Namespace) -> None:
    """
    统一元数据富集协调：

    - 根据 sources.yml 中的配置，调用 crossref/pubmed/arxiv/biorxiv 等 source；
    - 每个 source 独立基于 Paper（来自 fetch-venues）尽力返回 SourceEnrichmentResult；
    - 按 priority * confidence 分别选出最佳 pdf_url 和 abstract；
    - 将 pdf_url、abstract_source、enrichment_version 更新回 papers.jsonl；
    - full abstract 写入冷存储 data/cold/abstracts/YYYY-MM.jsonl.gz。
    """
    since_days = getattr(args, "since_days", 0) or 0
    print(f"[enrich_metadata] since_days={since_days}")

    cfg_by_id, instances_by_id = build_metadata_sources()
    if not instances_by_id:
        print("[enrich_metadata] no enabled metadata sources, skip.")
        return

    papers = load_all_papers()
    now = datetime.now(timezone.utc)

    # 根据简单规则筛选本次需要富集的论文
    target_papers = [p for p in papers if paper_needs_enrichment(p, since_days, now)]
    if not target_papers:
        print("[enrich_metadata] no papers to enrich.")
        return

    updated = []
    total = len(target_papers)

    for idx, paper in enumerate(target_papers, start=1):
        per_source_results: dict[str, SourceEnrichmentResult] = {}

        for sid, src in instances_by_id.items():
            try:
                res = src.enrich(paper)
            except Exception as exc:
                logger.warning(
                    "enrich_metadata: source '%s' failed for paper id=%s: %s",
                    sid,
                    getattr(paper, "id", None),
                    exc,
                )
                continue

            if res.confidence > 0 and (res.pdf_url or res.abstract or res.metadata):
                per_source_results[sid] = res

        if not per_source_results:
            continue

        best_pdf_url, pdf_source_id, best_abstract, abs_source_id = select_best_fields(
            cfg_by_id, per_source_results
        )

        changed = False

        # 更新 pdf_url
        if best_pdf_url and best_pdf_url != getattr(paper, "pdf_url", None):
            paper.pdf_url = best_pdf_url
            changed = True

        # 更新摘要相关：full abstract 写冷存储 + abstract_source / enrichment_version 写回索引
        if best_abstract and abs_source_id:
            store_full_abstract(paper.id, abs_source_id, best_abstract)

            if hasattr(paper, "abstract_source"):
                if getattr(paper, "abstract_source", None) != abs_source_id:
                    paper.abstract_source = abs_source_id
                    changed = True

            if hasattr(paper, "enrichment_version"):
                if getattr(paper, "enrichment_version", None) != ENRICHMENT_VERSION:
                    paper.enrichment_version = ENRICHMENT_VERSION
                    changed = True

        if changed:
            updated.append(paper)

        if idx % 5 == 0:
            logger.info(
                "enrich_metadata progress: %d/%d papers processed, %d updated",
                idx,
                total,
                len(updated),
            )

    if not updated:
        print("[enrich_metadata] no papers updated.")
        return

    # 写回索引
    new_items = upsert_papers(updated)
    print(
        f"[enrich_metadata] processed={len(target_papers)}, "
        f"updated={len(updated)}, newly_inserted={len(new_items)}"
    )



def _cmd_score_papers(args: argparse.Namespace) -> None:
    """
    丰富元信息 + 打分（骨架版）。
    后续会在这里拉摘要、计算打分、更新 score_summary。
    """
    print(f"[score_papers] since_days={args.since_days}  version={args.version}")


def _cmd_generate_daily_report(args: argparse.Namespace) -> None:
    """
    生成日报（骨架版）。
    后续会在这里从索引中筛选指定日期新增的论文并输出到 reports/daily。
    """
    print(f"[generate-daily-report] date={args.date}")


def _cmd_cleanup(args: argparse.Namespace) -> None:
    """
    清理历史数据（骨架版）。
    包括：删除过旧的日报、冷数据分片等。
    """
    print(f"[cleanup] keep_days={args.keep_days}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-tracker",
        description="Paper tracker CLI for journal-based ML/bioinformatics pipelines.",
    )

    subparsers = parser.add_subparsers(
        title="subcommands",
        dest="command",
        required=True,
    )

    # fetch-venues
    p_fetch = subparsers.add_parser(
        "fetch-venues",
        help="Fetch recent papers from configured venues.",
    )
    p_fetch.add_argument(
        "--days-back",
        type=int,
        default=None,
        help=(
            "How many days back to fetch recent papers. "
            "If not set, use per-venue fetcher_params.days_back (or its default)."
        ),
    )
    p_fetch.set_defaults(func=_cmd_fetch_venues)

    # resolve-sources
    p_enrich = subparsers.add_parser(
        "enrich_metadata",
        help="Resolve open-access PDF URLs from configured sources.",
    )
    p_enrich.add_argument(
        "--since-days",
        type=int,
        default=1,
        help="Only process papers created within the last N days (default: 1).",
    )
    p_enrich.set_defaults(func=_cmd_enrich_metadata)

    # enrich-and-score
    p_score = subparsers.add_parser(
        "score-papers",
        help="Enrich papers with additional metadata and compute scores.",
    )
    p_score.add_argument(
        "--since-days",
        type=int,
        default=1,
        help="Only process papers created/updated within the last N days (default: 1).",
    )
    p_score.add_argument(
        "--version",
        type=str,
        default="v1",
        help="Scoring algorithm version label (default: v1).",
    )
    p_score.set_defaults(func=_cmd_score_papers)

    # generate-daily-report
    p_report = subparsers.add_parser(
        "generate-daily-report",
        help="Generate daily report for a given date.",
    )
    p_report.add_argument(
        "--date",
        type=str,
        default=datetime.today().isoformat(),
        help="Date for the report in YYYY-MM-DD format (default: today).",
    )
    p_report.set_defaults(func=_cmd_generate_daily_report)

    # cleanup
    p_cleanup = subparsers.add_parser(
        "cleanup",
        help="Cleanup old reports and optional cold data.",
    )
    p_cleanup.add_argument(
        "--keep-days",
        type=int,
        default=90,
        help="Number of days of reports to keep (default: 90).",
    )
    p_cleanup.set_defaults(func=_cmd_cleanup)

    return parser


def main(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    # 每个子命令都设置了 func 回调
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        sys.exit(1)
    func(args)


if __name__ == "__main__":
    main()
