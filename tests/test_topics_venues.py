from pathlib import Path

import pytest

from researchlib.topics import UnreliableTranslationError, expand_topic, expand_user_query, load_topics
from researchlib.venues import load_venues

ROOT = Path(__file__).parents[1]


def test_topics_expand_and_chinese_mapping():
    topics = load_topics(ROOT / "config/topics.yml")
    assert len(topics) == 7 and topics["small-orfs"].strategy == "OR"
    assert len(expand_topic(topics["small-orfs"])) == 2
    assert expand_user_query("微蛋白", topics)[0] == "microprotein"
    with pytest.raises(UnreliableTranslationError):
        expand_user_query("未知主题", topics)


def test_verified_venue_catalog():
    venues = load_venues(ROOT / "config/venues.yml")
    assert len(venues) == 22
    assert venues["bioinformatics"].issns == ["1367-4803", "1367-4811"]

