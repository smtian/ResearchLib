# src/paper_tracker/cli.py
from __future__ import annotations

from pathlib import Path
import sys
import argparse
import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed


from paper_tracker.storage import upsert_papers, load_all_papers, update_papers


# ----------for venues--------------
from paper_tracker.config import load_venues
from paper_tracker.venues import create_fetchers_for_config
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

# ----------for scoring-------------
from paper_tracker.scoring.composite import (
    score_paper,
    SCORING_PIPELINE_VERSION,
)
from paper_tracker.utils import load_full_abstract  

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

    # 为所有 paper 复用一个线程池，避免频繁创建/销毁线程
    max_workers = max(1, min(8, len(instances_by_id)))  # 8 可以根据需要调小/调大
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for idx, paper in enumerate(target_papers, start=1):
            per_source_results: dict[str, SourceEnrichmentResult] = {}

            # 并发调用各个 source 的 enrich(paper)
            future_to_sid = {
                executor.submit(src.enrich, paper): sid
                for sid, src in instances_by_id.items()
            }

            for future in as_completed(future_to_sid):
                sid = future_to_sid[future]
                try:
                    res = future.result()
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
    updated_count = update_papers(updated)
    print(
        f"[enrich_metadata] processed={len(target_papers)}, "
        f"updated={len(updated)}, newly_inserted={updated_count}"
    )



def _cmd_score_papers(args: argparse.Namespace) -> None:
    """
    纯离线打分：

    - 根据 since_days 选取需要打分的论文；
    - 从冷存储加载 full abstract（如有）；
    - 调用 scoring.composite.score_paper 计算分数；
    - 更新 Paper.score_summary / score_version / updated_at，并写回索引。
    """
    now = datetime.now(timezone.utc)

    cutoff: datetime | None = None
    if getattr(args, "since_days", None) is not None:
        cutoff = now - timedelta(days=args.since_days)

    # 目标 score 版本：优先使用命令行参数，否则使用当前流水线版本
    target_version: str = getattr(args, "version", None) or SCORING_PIPELINE_VERSION

    papers = load_all_papers()
    updated = 0
    skipped = 0
    total_candidates = 0

    for paper in papers:
        # 1) 按时间过滤
        if cutoff is not None:
            created_at = getattr(paper, "created_at", None)
            created_dt: datetime | None

            if isinstance(created_at, datetime):
                created_dt = created_at
            elif isinstance(created_at, str):
                try:
                    # Python3.11+ 支持 fromisoformat 解析带 offset 的时间
                    created_dt = datetime.fromisoformat(created_at)
                except ValueError:
                    created_dt = None
            else:
                created_dt = None

            if created_dt is not None and created_dt < cutoff:
                continue

        # 2) 按版本过滤：已有 score_version 且已是目标版本，则跳过
        # current_sv = getattr(paper, "score_version", None)
        # if current_sv == target_version:
        #     skipped += 1
        #     continue

        total_candidates += 1

        # 3) 从冷存储读取 full abstract（如果不存在则返回 None）
        full_abstract = load_full_abstract(paper.id)

        # 4) 调用打分流水线
        result = score_paper(paper, full_abstract)

        # 5) 写回 score_summary / score_version / updated_at
        summary: dict[str, object] = {
            "total_score": float(result.total_score),
            "components": dict(result.components),
        }
        tags = getattr(result, "tags", None)
        if tags:
            summary["tags"] = list(tags)

        paper.score_summary = summary
        paper.score_version = target_version
        paper.updated_at = now

        updated += 1

    # 6) 持久化到索引文件
    update_papers(papers)

    print(
        f"[score_papers] since_days={getattr(args, 'since_days', None)} "
        f"target_version={target_version!r} "
        f"candidates={total_candidates} updated={updated} skipped={skipped}"
    )


def _cmd_generate_daily_report(args: argparse.Namespace) -> None:
    """
    生成日报：

    - 从索引中读取所有论文；
    - 选取 created_at 落在指定日期的论文；
    - 过滤掉没有打分或分数低于 min_score 的论文；
    - 按 total_score 降序排序；
    - 输出 reports/daily/YYYY-MM-DD.md。
    """
    # 解析日期参数
    try:
        target_date: date = datetime.fromisoformat(args.date).date()
    except ValueError as exc:
        raise SystemExit(f"Invalid --date value: {args.date!r}, expected YYYY-MM-DD") from exc

    min_score = float(getattr(args, "min_score", 0.0))

    # 读取索引
    papers = load_all_papers()

    # 过滤出指定日期 + 分数足够的论文
    candidates: list[tuple[float, Any]] = []  # (score, paper)

    for paper in papers:
        created_at = getattr(paper, "created_at", None)

        created_dt: datetime | None
        if isinstance(created_at, datetime):
            created_dt = created_at
        elif isinstance(created_at, str):
            try:
                created_dt = datetime.fromisoformat(created_at)
            except ValueError:
                created_dt = None
        else:
            created_dt = None

        if created_dt is None:
            continue

        if created_dt.date() != target_date:
            continue

        summary = getattr(paper, "score_summary", None)
        if not isinstance(summary, dict):
            continue

        score = summary.get("total_score")
        if score is None:
            continue

        try:
            score_val = float(score)
        except (TypeError, ValueError):
            continue

        if score_val < min_score:
            continue

        candidates.append((score_val, paper))

    # 按分数降序排序
    candidates.sort(key=lambda x: x[0], reverse=True)

    report_dir = Path("reports") / "daily"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{target_date.isoformat()}.md"

    lines: list[str] = []
    lines.append(f"# Daily Report for {target_date.isoformat()}")
    lines.append("")
    lines.append(f"- Minimum score: {min_score}")
    lines.append(f"- Papers: {len(candidates)}")
    lines.append("")

    if not candidates:
        lines.append("_No papers matched the criteria._")
    else:
        for idx, (score_val, paper) in enumerate(candidates, start=1):
            title = getattr(paper, "title", "") or ""
            journal = getattr(paper, "journal", "") or ""
            doi = getattr(paper, "doi", "") or ""
            pdf_url = getattr(paper, "pdf_url", "") or ""
            abstract = load_full_abstract(paper.id)

            lines.append(f"## {idx}. {title}")
            if journal:
                lines.append(f"- Journal: {journal}")
            lines.append(f"- Score: {score_val:.2f}")
            if doi:
                lines.append(f"- DOI: {doi}")
            if pdf_url:
                lines.append(f"- PDF: {pdf_url}")
            if abstract:
                lines.append("")
                lines.append("**Abstract**")
                lines.append("")
                lines.append(abstract)
            lines.append("")  # 分隔空行

    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(
        f"[generate-daily-report] date={target_date.isoformat()} "
        f"min_score={min_score} output={report_path}"
    )



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
        "enrich-metadata",
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
    p_report.add_argument(
        "--min-score",
        type=float,
        default=11.0,
        help="Min score of selected papers in the report.",
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
