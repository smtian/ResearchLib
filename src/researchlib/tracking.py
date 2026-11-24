from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from .config import Settings
from .http import HTTPClient
from .merge import merge_records
from .models import PaperRecord, SearchFilters, TrackResult
from .providers.arxiv import ArxivProvider
from .providers.biorxiv import BioRxivProvider
from .providers.crossref import CrossrefProvider
from .topics import Topic, load_topics
from .venues import find_venue, load_venues


class RealtimeTracker:
    def __init__(self, http: HTTPClient, settings: Settings):
        self.http, self.settings = http, settings
        self.topics = load_topics(settings.tracking.topics_file)
        self.venues = load_venues(settings.tracking.venues_file)

    def track_venues(
        self, *, days: int = 7, venue: str | None = None, limit: int = 20,
        today: date | None = None,
    ) -> TrackResult:
        today = today or datetime.now(timezone.utc).date()
        start = today - timedelta(days=max(days - 1, 0))
        selected = [find_venue(self.venues, venue)] if venue else [item for item in self.venues.values() if item.enabled]
        jobs = []
        for item in selected:
            # Query both formats for one requested venue; use the preferred online ISSN in a broad sweep.
            issns = item.issns if venue else [item.issn_online or item.issn_print]
            jobs.extend((item, issn) for issn in issns if issn)
        quota = max(1, math.ceil(max(limit, 1) / max(len(jobs), 1)))
        records: list[PaperRecord] = []
        failures: dict[str, str] = {}
        cursors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=min(len(jobs), 8) or 1) as executor:
            futures = {
                executor.submit(self._venue_job, item, issn, start.isoformat(), today.isoformat(), quota): (item, issn)
                for item, issn in jobs
            }
            for future in as_completed(futures):
                item, issn = futures[future]
                key = f"{item.slug}:{issn}"
                try:
                    found, cursor = future.result()
                    records.extend(found)
                    if cursor:
                        cursors[key] = cursor
                except Exception as exc:
                    failures[key] = f"{type(exc).__name__}: {str(exc)[:240]}"
        merged = merge_records(records)
        papers = [item.paper for item in merged]
        papers.sort(key=lambda paper: (paper.published_date or f"{paper.year or 0:04d}", paper.title.casefold()), reverse=True)
        return TrackResult(
            source_type="venues", window_start=start.isoformat(), window_end=today.isoformat(),
            raw_candidates=len(records), unique_candidates=len(merged),
            truncated=bool(cursors) or len(papers) > limit, failures=failures, next_cursors=cursors,
            results=papers[:min(max(limit, 1), 100)],
        )

    def _venue_job(self, venue, issn: str, start: str, end: str, quota: int) -> tuple[list[PaperRecord], str | None]:
        provider = CrossrefProvider(self.http, min(venue.fetch.rows, quota))
        records = []
        cursor = None
        for _ in range(min(venue.fetch.max_pages, self.settings.tracking.max_pages_per_source)):
            page = provider.track_venue(
                issn, start, end, cursor=cursor, rows=min(venue.fetch.rows, quota)
            )
            for record in page.records:
                if record.journal and record.journal.casefold() not in venue.accepted_names:
                    continue
                records.append(record)
                if len(records) >= quota:
                    return records, page.next_cursor
            if page.done or not page.next_cursor:
                return records, None
            cursor = page.next_cursor
        return records, cursor

    def track_preprints(
        self, *, days: int = 7, topic: str | None = None, limit: int = 20,
        sources: list[str] | None = None, today: date | None = None,
    ) -> TrackResult:
        today = today or datetime.now(timezone.utc).date()
        start = today - timedelta(days=max(days - 1, 0))
        selected_topics = [self.topics[topic]] if topic else list(self.topics.values())
        requested = list(dict.fromkeys(sources or ["arxiv", "biorxiv", "medrxiv"]))
        invalid = set(requested) - {"arxiv", "biorxiv", "medrxiv"}
        if invalid:
            raise ValueError(f"unsupported preprint sources: {','.join(sorted(invalid))}")
        quota = max(1, math.ceil(max(limit, 1) / max(len(requested), 1)))
        records: list[PaperRecord] = []
        failures: dict[str, str] = {}
        cursors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(requested) or 1) as executor:
            futures = {}
            for source in requested:
                if source == "arxiv":
                    futures[executor.submit(self._arxiv_job, selected_topics, start.isoformat(), today.isoformat(), quota)] = source
                else:
                    futures[executor.submit(self._rxiv_job, source, selected_topics, start.isoformat(), today.isoformat(), quota)] = source
            for future in as_completed(futures):
                source = futures[future]
                try:
                    found, cursor = future.result()
                    records.extend(found)
                    if cursor:
                        cursors[source] = cursor
                except Exception as exc:
                    failures[source] = f"{type(exc).__name__}: {str(exc)[:240]}"
        merged = merge_records(records)
        papers = [item.paper for item in merged]
        papers.sort(key=lambda paper: (paper.published_date or f"{paper.year or 0:04d}", paper.title.casefold()), reverse=True)
        return TrackResult(
            source_type="preprints", window_start=start.isoformat(), window_end=today.isoformat(),
            raw_candidates=len(records), unique_candidates=len(merged),
            truncated=bool(cursors) or len(papers) > limit, failures=failures, next_cursors=cursors,
            results=papers[:min(max(limit, 1), 100)],
        )

    def _arxiv_job(self, topics: list[Topic], start: str, end: str, quota: int) -> tuple[list[PaperRecord], str | None]:
        provider = ArxivProvider(
            self.http, min(self.settings.tracking.arxiv_page_size, quota),
            self.settings.tracking.arxiv_min_interval,
        )
        query = self._arxiv_query(topics)
        records, cursor = [], None
        for _ in range(self.settings.tracking.max_pages_per_source):
            page = provider.discover(query, SearchFilters(start_date=start, end_date=end), cursor)
            records.extend(page.records[:max(0, quota - len(records))])
            if page.done or not page.next_cursor or len(records) >= quota:
                return records, page.next_cursor if len(records) >= quota else None
            cursor = page.next_cursor
        return records, cursor

    def _rxiv_job(
        self, source: str, topics: list[Topic], start: str, end: str, quota: int
    ) -> tuple[list[PaperRecord], str | None]:
        provider = BioRxivProvider(self.http, source, self.settings.tracking.preprint_page_size)
        records, cursor = [], None
        for _ in range(self.settings.tracking.max_pages_per_source):
            page = provider.discover("", SearchFilters(start_date=start, end_date=end), cursor)
            for record in page.records:
                if self._topic_match(record, topics):
                    records.append(record)
                    if len(records) >= quota:
                        break
            if page.done or not page.next_cursor or len(records) >= quota:
                cursor = page.next_cursor if len(records) >= quota else None
                break
            cursor = page.next_cursor
        links_cursor = None
        links = {}
        for _ in range(self.settings.tracking.max_pages_per_source if records else 0):
            page_links, links_cursor = provider.publication_links(start, end, links_cursor)
            links.update({link.preprint_doi: link.published_doi for link in page_links})
            if not links_cursor:
                break
        for record in records:
            if record.doi in links:
                record.published_doi = links[record.doi]
                record.publication_status = "published"
        return records, cursor or links_cursor

    @staticmethod
    def _topic_match(record: PaperRecord, topics: list[Topic]) -> bool:
        text = f"{record.title} {record.abstract or ''}".casefold()
        for topic in topics:
            if any(term.casefold() in text for term in topic.exclude):
                continue
            if any(term.casefold() in text for term in topic.synonyms + topic.include):
                return True
        return False

    @staticmethod
    def _arxiv_query(topics: list[Topic]) -> str:
        terms = list(dict.fromkeys(term for topic in topics for term in topic.synonyms + topic.include))
        return "(" + " OR ".join(f'all:"{term}"' for term in terms) + ")"
