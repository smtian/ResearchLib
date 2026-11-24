from datetime import date

from researchlib.config import Settings
from researchlib.models import DiscoveryPage, PaperRecord
from researchlib.providers.crossref import CrossrefProvider
from researchlib.tracking import RealtimeTracker


def test_realtime_venue_tracking_has_no_persistent_state(monkeypatch, http_settings):
    def tracked(self, issn, start_date, end_date, **kwargs):
        return DiscoveryPage(
            records=[PaperRecord(title="Tracked", doi="10.1/x", journal="Bioinformatics", source="crossref")],
            done=True, actual_query=issn,
        )
    monkeypatch.setattr(CrossrefProvider, "track_venue", tracked)
    from researchlib.http import HTTPClient
    tracker = RealtimeTracker(HTTPClient(http_settings), Settings())
    result = tracker.track_venues(days=7, venue="bioinformatics", limit=10, today=date(2026, 11, 12))
    assert result.window_start == "2026-11-06"
    assert result.raw_candidates == 2 and result.unique_candidates == 1
    assert result.next_cursors == {}
