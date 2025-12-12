# src/paper_tracker/venues/crossref.py
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
import html
import re

from paper_tracker.config import VenueConfig
from paper_tracker.models import Paper
from paper_tracker.venues.base import BaseVenueFetcher, register_fetcher


logger = logging.getLogger(__name__)


CROSSREF_BASE_URL = "https://api.crossref.org/works"
# 根据 Crossref 的使用要求，最好设置一个带邮箱的 User-Agent
# TODO: 把邮箱改成你自己的
CROSSREF_USER_AGENT = "paper-tracker/0.1 (mailto:your-email@example.com)"
_TITLE_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_crossref_title(raw: str) -> str:
    """
    清洗 Crossref 返回的标题字符串：
    - 解码 HTML 实体（&amp;、&beta; 等）
    - 去掉所有 HTML 标签（<sup>、<sub>、<i> 等）
    - 折叠换行和多余空白为单个空格
    """
    if not raw:
        return ""

    # 先解码 HTML 实体
    text = html.unescape(raw)

    # 去掉所有 HTML 标签
    text = _TITLE_HTML_TAG_RE.sub("", text)

    # 折叠各种空白（空格、换行、tab）为单个空格
    text = " ".join(text.split())

    return text

def _pick_crossref_date(item: Dict[str, Any]) -> Optional[datetime]:
    """
    解析 Crossref 记录中的日期字段，按优先级：
    published-online -> published-print -> published -> issued

    date-parts 格式一般为 [[YYYY, MM, DD]] 或 [[YYYY, MM]] / [[YYYY]]。
    """
    for key in ("published-online", "published-print", "published", "issued"):
        part = item.get(key)
        if not part:
            continue
        date_parts = part.get("date-parts")
        if not date_parts:
            continue
        first = date_parts[0]
        if not first:
            continue
        year = int(first[0])
        month = int(first[1]) if len(first) >= 2 else 1
        day = int(first[2]) if len(first) >= 3 else 1
        try:
            return datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            # 非法日期（极少出现），跳过该字段
            continue
    return None


def _build_filter_string(venue: VenueConfig, start_date: date, end_date: date) -> str:
    """
    构建 Crossref filter 字符串：
    - issn:xxx（可多个）
    - from-pub-date:YYYY-MM-DD
    - until-pub-date:YYYY-MM-DD
    - type:journal-article
    """
    filters: List[str] = []

    issn_obj = venue.issn
    issns: List[str] = []
    if issn_obj:
        if issn_obj.print:
            issns.append(issn_obj.print)
        if issn_obj.online:
            issns.append(issn_obj.online)

    for issn in issns:
        filters.append(f"issn:{issn}")

    filters.append(f"from-pub-date:{start_date.isoformat()}")
    filters.append(f"until-pub-date:{end_date.isoformat()}")
    filters.append("type:journal-article")

    return ",".join(filters)


def _extract_authors(item: Dict[str, Any]) -> (List[str], Optional[str]): # type: ignore
    """
    从 Crossref 记录中提取全部作者和第一作者。
    """
    authors_raw = item.get("author") or []
    authors: List[str] = []

    for a in authors_raw:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        if given or family:
            name = f"{given} {family}".strip()
        else:
            name = (a.get("name") or "").strip()
        if name:
            authors.append(name)

    first_author = authors[0] if authors else None
    return authors, first_author


@register_fetcher("crossref")
class CrossrefVenueFetcher(BaseVenueFetcher):
    """
    使用 Crossref API 按 ISSN 抓取期刊近期论文的抓取器实现。

    会根据 VenueConfig 中的 issn 和 fetcher_params.days_back 以及调用时的 days_back 参数，
    组合出一个最终的时间窗口（当前实现直接使用调用时的 days_back）。
    """

    def fetch_recent(self, days_back: int) -> List[Paper]:
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=days_back)
        end_date = today

        filter_str = _build_filter_string(self.venue, start_date, end_date)

        params = {
            "filter": filter_str,
            "sort": "published",
            "order": "desc",
            "rows": 200,  # 当前简单限制每个 venue 每次最多 100 条
        }

        headers = {
            "User-Agent": CROSSREF_USER_AGENT,
        }

        logger.info(
            "Fetching recent works from Crossref for venue=%s, filter=%s",
            self.venue.id,
            filter_str,
        )

        try:
            resp = requests.get(
                CROSSREF_BASE_URL,
                params=params,
                headers=headers,
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Crossref request failed for venue=%s: %s",
                self.venue.id,
                exc,
            )
            return []

        if resp.status_code != 200:
            logger.warning(
                "Crossref responded with status %s for venue=%s, params=%s",
                resp.status_code,
                self.venue.id,
                params,
            )
            return []

        try:
            data = resp.json()
        except ValueError:
            logger.warning("Failed to decode Crossref JSON for venue=%s", self.venue.id)
            return []

        items = data.get("message", {}).get("items", [])
        papers: List[Paper] = []

        for item in items:
            # 标题
            titles = item.get("title") or []
            raw_title = titles[0] if titles else ""
            title = _clean_crossref_title(raw_title) or "(no title)"

            # DOI
            doi = item.get("DOI") or None

            # 期刊名（优先 Crossref 记录中的 container-title，否则用配置里的 name）
            containers = item.get("container-title") or []
            journal = containers[0] if containers else self.venue.name

            # 论文 URL
            url = item.get("URL") or None

            # 发表日期
            published_at = _pick_crossref_date(item)

            # 作者
            authors, first_author = _extract_authors(item)

            p = Paper(
                id="",  # 让存储层根据 DOI 或 title+journal 统一生成 id
                title=title,
                doi=doi,
                journal=journal,
                venue_id=self.venue.id,
                url=url,
                authors=authors,
                first_author=first_author,
                published_at=published_at,
            )
            # short_abstract / score_summary / pdf_url 等后续阶段再填
            papers.append(p)

        logger.info(
            "Fetched %d candidate papers from Crossref for venue=%s",
            len(papers),
            self.venue.id,
        )
        return papers
