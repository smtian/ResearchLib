# src/paper_tracker/cli.py
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Optional


def _cmd_fetch_venues(args: argparse.Namespace) -> None:
    """
    拉取期刊最新论文（骨架版）。
    后续会在这里调用 venues fetcher，并写入索引。
    """
    print(f"[fetch-venues] days_back={args.days_back}")


def _cmd_resolve_sources(args: argparse.Namespace) -> None:
    """
    解析开放获取 PDF 源（骨架版）。
    后续会在这里根据 DOI/标题调用 arxiv/biorxiv 等 source 解析器。
    """
    print(f"[resolve-sources] since_days={args.since_days}")


def _cmd_enrich_and_score(args: argparse.Namespace) -> None:
    """
    丰富元信息 + 打分（骨架版）。
    后续会在这里拉摘要、计算打分、更新 score_summary。
    """
    print(f"[enrich-and-score] since_days={args.since_days}  version={args.version}")


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
        default=3,
        help="How many days back to fetch recent papers from venues (default: 3).",
    )
    p_fetch.set_defaults(func=_cmd_fetch_venues)

    # resolve-sources
    p_resolve = subparsers.add_parser(
        "resolve-sources",
        help="Resolve open-access PDF URLs from configured sources.",
    )
    p_resolve.add_argument(
        "--since-days",
        type=int,
        default=1,
        help="Only process papers created within the last N days (default: 1).",
    )
    p_resolve.set_defaults(func=_cmd_resolve_sources)

    # enrich-and-score
    p_score = subparsers.add_parser(
        "enrich-and-score",
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
    p_score.set_defaults(func=_cmd_enrich_and_score)

    # generate-daily-report
    p_report = subparsers.add_parser(
        "generate-daily-report",
        help="Generate daily report for a given date.",
    )
    p_report.add_argument(
        "--date",
        type=str,
        default=date.today().isoformat(),
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
