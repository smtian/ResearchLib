# ResearchLib Agent Instructions

## Mission and boundaries

Maintain **ResearchLib**, a local, real-time scientific literature discovery MCP server and CLI.
The application searches scholarly APIs, normalizes/merges bibliographic records, ranks results, tracks journals/preprints, and retrieves abstracts on demand.

- **No persistence:** no SQLite, Zotero integration, database, vector store, stored paper library, saved search history, or downloaded PDFs/full texts. A bounded TTL HTTP cache **in process memory only** is permitted.
- **No LLM dependency:** query expansion, ranking, and metadata processing must work deterministically without paid model APIs.
- **Evidence integrity:** never invent paper titles, DOIs, IDs, abstracts, citations, source provenance, or API results. Never imply that a limited/failed search was exhaustive.
- **API etiquette:** use the provider's documented query syntax, pagination, date filters, rate limits, retry policy, and contact/API-key requirements. Prefer mocked responses in tests; keep opt-in live smoke tests small.
- **Safety:** keep credentials in environment variables or an untracked `.env`; never log/return secrets. Never edit `.venv/`, `.git/`, or unrelated user files.

## Architecture

- `src/researchlib/mcp_server.py`: MCP stdio entrypoint and tool response formatting.
- `src/researchlib/service.py`: search orchestration, source budgets, query variants, and partial-coverage reporting.
- `src/researchlib/query.py`, `topics.py`: provider-aware query compilation and deterministic topic expansion.
- `src/researchlib/providers/`: live provider adapters (OpenAlex, Europe PMC, PubMed, Crossref, arXiv, bioRxiv/medRxiv).
- `src/researchlib/merge.py`, `ranking.py`: conservative cross-ID deduplication and local relevance ranking.
- `src/researchlib/details.py`: on-demand abstract/metadata lookup, with per-ID validation and failure reporting.
- `src/researchlib/related.py`: DOI citation neighborhoods via OpenAlex, independently returning references and citing papers by citation count.
- `src/researchlib/tracking.py`, `venues.py`: live journal/preprint date-window tracking.
- `src/researchlib/http.py`: request limits, retries, backoff and process-local TTL cache.
- `config/`: local configuration and curated dictionaries; never confuse these with stored search results.
- `tests/`: offline unit/contract tests and an optional MCP-SDK integration test.

## MCP public contract (v0.2.0, extended)

Preserve existing tool names and argument behavior unless an explicit request requires a change:

1. `search_literature(query, topic=None, mode="quick", limit=10, from_year=None, to_year=None, output="compact")`
2. `get_paper_details(identifiers)` — 1–15 DOI/PMID/OpenAlex/arXiv identifiers; return metadata and abstracts when available.
3. `lookup_doi(doi)` — verify one DOI via Crossref; errors must differ from not-found.
4. `track_venues(days=7, venue=None, limit=10, output="compact")`
5. `track_preprints(days=7, topic=None, sources=None, limit=10, output="compact")`
6. `list_topics()`
7. `expand_query(topic)`
8. `list_venues()`
9. `get_related_papers(doi, limit=10, output="compact")` — references (outgoing) and citations (incoming), each limited to 1–100 and sorted by each paper's OpenAlex citation count descending. Preserve independent failures, source totals and compact/full behavior; DOI not-found differs from lookup errors. No relevance scores are generated.

`compact` **must not** include abstracts, raw provider payloads, or complete author lists. It must preserve identifiable metadata such as DOI/PMID/arXiv/OpenAlex IDs, source URLs and provenance, ranking score where applicable, and `abstract_available`.
`full` retains the model's detailed nested records. Keep the `output="full"` compatibility path.
Displayed search/tracking `limit` is clamped to 1–100; search-detail lookup accepts at most 15 input IDs.

Distinguish `raw_candidates`, `unique_candidates`, `truncated`, `candidates_truncated`, `display_truncated`, `complete`, and `failures` accurately. `complete=True` only means no **reported** budget/pagination/failure truncation; it is not a recall guarantee. Tracking uses `next_cursors`/`truncated` instead of a search `complete` field.
Scores are **relative ranking signals**, never calibrated probabilities of relevance. Citation counts must never implicitly boost relevance. The explicit citation-count ordering in `get_related_papers` is separate from search relevance. For this tool each list's `complete` refers to the requested top N; `display_truncated` marks lower-ranked results omitted by the limit, while `candidates_truncated` marks incomplete top-N retrieval. `total` is the relationship query count, or `None` if unknown. Root `complete` requires both lists to complete; coverage remains limited to OpenAlex's indexed links.

## Engineering workflow

1. Inspect the relevant modules and tests before changing public behavior. Keep changes targeted and dependencies minimal; maintain Python >=3.11 support.
2. Add regression tests for every bug, especially mixed identifiers, source-specific filters, partial failures, pagination, compact-output token savings, and retrieval status.
3. Run `python -m pytest -q`. If the MCP SDK is missing, the actual SDK test may **skip**; report it as unverified, not as passed. Offline contract tests must still pass.
4. When possible, separately verify `researchlib-mcp` launches and the nine tools can be called with the official MCP SDK; do not claim a fake registry is a full client integration test.
5. Never run broad paid/live API sweeps without explicit direction; do not overwrite configs or secrets while debugging.
6. Keep `README.md`, `AGENTS.md`, and the tool tests synchronized with API changes.

## Reporting after changes

Report briefly: files modified, test commands and pass/skip/fail counts, whether real MCP SDK and external APIs were exercised, and remaining limitations. Show a small example instead of printing full search result JSON or large sets of abstracts.
