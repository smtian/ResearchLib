# ResearchLib — real-time literature search MCP server

ResearchLib is a Python **MCP server (stdio)** and CLI for research literature discovery. It queries scholarly APIs, merges duplicate bibliographic records across sources, ranks candidates locally, and optionally retrieves full abstracts for papers you select.

**Stateless by design:** it does not create a paper database or integrate with Zotero; it does not persist searches, metadata, abstracts, or any PDF/full text. The only request cache is a bounded, short-lived **in-process memory cache** (reset when the server exits). `config/` contains manually maintained topic and journal definitions, not a literature library.

## Coverage and behavior

| Feature | Implementation / important qualification |
|---|---|
| Live topic search | OpenAlex, Europe PMC, PubMed, Crossref, **arXiv** (enabled unless explicitly disabled) |
| Journal tracking | Crossref, using configured journals and ISSNs |
| Preprint tracking | arXiv, bioRxiv, medRxiv, over a recent date window |
| Deduplication | Conservative DOI/PMID/PMCID/OpenAlex/arXiv matching and transitive record merges when identifiers agree |
| Search ranking | Deterministic keyword matching, BM25, source/query rank fusion; citation counts are metadata only |
| Query expansion | Configured English topic terms and explicit Chinese-to-English term mappings; no LLM translation |
| Optional semantic retrieval | OpenAlex semantic search in **deep** mode when enabled in config; external API limits apply |
| Abstracts | On-demand via `get_paper_details`, for records whose providers supply abstracts; no full text |
| Citation neighborhoods | `get_related_papers`: references and citing papers via OpenAlex, each sorted by citation count |
| Errors / coverage | Per-source failures and truncation markers; coverage is **not** guaranteed exhaustive |

For reproducible scientific reviews, independently verify the final source records, DOI links, metadata and coverage. Search relevance scores are relative ordering signals, **not** probabilities or scientific quality scores. A missing record or empty search does not prove that a paper does not exist.

## Requirements and installation

Python **3.11+**; works on macOS, Linux and other platforms supporting the dependencies. From the project root (the directory containing `pyproject.toml`, `config/`, and `src/`):

```bash
python3 -m venv .venv
source .venv/bin/activate          # macOS/Linux
python -m pip install -e '.[test]'
python -m pytest -q
```

If you already have a virtual environment from an earlier version, rerun the editable install: copying `src/` alone will not install newly required dependencies or console entry points.

Environment variables (optional; put in a local `.env` file or in your MCP host configuration):

```dotenv
CONTACT_EMAIL=you@example.org
OPENALEX_API_KEY=
NCBI_API_KEY=
RESEARCHLIB_CONFIG=config/default.yaml
```

**Never commit or share real keys in `.env`.** The application reads `.env` from its current working directory. Public scholarly APIs may enforce quotas, require an API key for specific functionality, or change their rules. An OpenAlex key is recommended when available for cursor-based paging; semantic search may use limited quota. The code reports upstream errors; it cannot override provider restrictions.

## MCP server setup

Start in the project root:

```bash
researchlib-mcp
```

This starts a **stdio** MCP server: it is meant to be launched by an MCP client, so it will not display a web interface or an interactive prompt. Example MCP host config (adapt to your Codex / CC Switch client):

```json
{
  "mcpServers": {
    "researchlib": {
      "command": "/absolute/path/to/research-library-agent/.venv/bin/researchlib-mcp",
      "cwd": "/absolute/path/to/research-library-agent",
      "env": {
        "CONTACT_EMAIL": "you@example.org"
      }
    }
  }
}
```

The exact configuration **wrapper and field names depend on the host**; use the host's MCP settings UI or format. The key operational requirement is to launch the installed `researchlib-mcp` command with the project root as its working directory, or provide an absolute `RESEARCHLIB_CONFIG` plus accessible topic/venue config paths. No remote MCP hosting is needed.

### Nine MCP tools (v0.2.0, extended)

| Tool | Arguments | Output / purpose |
|---|---|---|
| `search_literature` | `query` (required), `topic`, `mode="quick"\|"deep"`, `limit=10`, `from_year`, `to_year`, `output="compact"\|"full"` | Ranked deduplicated search candidates; compact by default |
| `get_paper_details` | `identifiers: list[str]` (1–15) | Source-verified metadata and available abstracts for selected identifiers |
| `get_related_papers` | `doi`, `limit=10`, `output="compact"\|"full"` | References and citing papers, separately ranked by citation count descending; limit clamped to 1–100 per list |
| `lookup_doi` | `doi` | Crossref DOI lookup and explicit verification/not-found/error status |
| `track_venues` | `days=7`, `venue`, `limit=10`, `output="compact"\|"full"` | Recent journals by configured venue slug; dates and cursors |
| `track_preprints` | `days=7`, `topic`, `sources`, `limit=10`, `output="compact"\|"full"` | Recent arXiv/bioRxiv/medRxiv preprints |
| `list_topics` | none | Available topic slugs and deterministic term dictionaries |
| `expand_query` | `topic` | Query expansion for a configured topic |
| `list_venues` | none | Enabled tracked journals, ISSNs and tags |

Valid detail identifiers include:

```json
{
  "identifiers": [
    "10.1038/s41586-024-01234-5",
    "pmid:12345678",
    "openalex:W123456789",
    "arxiv:2401.12345"
  ]
}
```

The values above illustrate **formats only**, not verified real papers. A PMID may also be supplied as a bare number; DOI URLs and some provider URLs are accepted. Use IDs returned by search whenever possible. A DOI lookup through Crossref might not include an abstract; `get_paper_details` uses multiple relevant sources for DOI enrichment. The server explicitly differentiates `invalid`, `not_found`, `unresolved` and per-source failures when applicable.

### Find references and citing papers by DOI

Call `get_related_papers` with `{"doi": "10.1000/example", "limit": 20}` (illustrative DOI only).
It returns the target `paper` and two separate lists:

- `references.results`: papers cited by the target (outgoing references).
- `citations.results`: papers that cite the target (incoming citations).

Each list contains at most `limit` papers ordered by **that paper's own OpenAlex citation count**,
highest first. This explicit citation ordering does not change `search_literature` relevance ranking.
Unknown counts remain `null` and sort after known counts; they are not estimated.
Compact output includes `citation_count`, identifiers, URLs, source provenance and `abstract_available`;
`output="full"` includes the detailed paper records and available abstracts.

The server resolves the complete DOI (bare, `doi:` prefix or DOI URL), then requests the
first globally sorted page for each relationship. It does not fetch an arbitrary candidate
sample and rank that sample. A successful call normally needs only three OpenAlex requests;
the existing in-memory cache, rate limits, retries and request budgets apply.
`OPENALEX_API_KEY` and `CONTACT_EMAIL` are passed when configured.
The relationship filters and sorting follow the [OpenAlex works filters](https://github.com/ourresearch/openalex-docs/blob/main/api-entities/works/filter-works.md)
and [sorting documentation](https://help.openalex.org/api/sorting/).

Each list reports the API's `total`, `raw_candidates`, `unique_candidates`, `failures`,
`candidates_truncated`, `display_truncated`, `truncated` and `complete`.
`total` is the relationship query's count, not the target's possibly lagging citation-count field.
`display_truncated=true` means lower-ranked works were omitted by the display limit;
`candidates_truncated=true` means retrieval failed or returned fewer unique works than needed
for the requested top N. `complete=true` means the requested top N was returned without
reported retrieval truncation/failure; it does **not** mean every relationship was returned.
The root `complete` requires both lists to be complete.

`status` distinguishes `invalid`, `not_found` (target DOI absent from OpenAlex), `error`
(target lookup failed), `partial` (at least one relationship failed/incomplete), and `ok`.
A successful list is retained if the other fails. A successfully queried empty list has
`total=0`; an unqueried/failed list has `total=null`. Coverage and citation counts are limited
to [OpenAlex's indexed citation links](https://help.openalex.org/data/works/citations/),
which may omit references present in a paper. No PDF/full-text retrieval is performed.

### Suggested low-token workflow

1. Call `search_literature` with a specific question and `limit=10`–`30`. The default `output="compact"` returns title, publication information, available IDs/URLs, score, data sources and `abstract_available`, **without abstract text or large metadata blobs**.
2. Inspect `queries`, `raw_candidates`, `unique_candidates`, `failures`, `complete`, `candidates_truncated` and `display_truncated`. Broaden the search or use `mode="deep"` if needed; `complete` is an operational flag, not proof of comprehensive scientific coverage.
3. Select approximately 5–10 promising items and call `get_paper_details` with their identifiers (maximum 15 per call). Inspect each result's `sources`, `record.abstract`, `invalid`, `not_found`, `unresolved`, and `failures`. Missing abstract text must be reported as missing, never filled in by inference.
4. Read the available abstracts and discuss hypotheses, methods, evidence and limitations with your research assistant. Use `output="full"` when you want the search tool's entire nested paper/rank representation in one call.

`search_literature` and the tracking tools clamp displayed results to **1–100**. Increasing `limit` changes how many ranked results are displayed, **not necessarily how deeply each upstream source can be searched**. The `quick` and `deep` modes primarily change the candidate budget; deep mode also uses additional query variants and optionally OpenAlex semantic retrieval. Search may take longer and make more API calls in deep mode.

#### Search output fields

- **Compact result:** `id`, `title`, `year`, `published_date`, `first_author`, `journal`, `doi`, `pmid`, `arxiv_id`, `openalex_id`, `url`, `abstract_available`, `publication_status`, plus search `score` and `sources`.
- **Full result:** original structured `SearchHit` containing `paper`, source ranks and ranking components, including available abstract, author list and provenance.
- **Search envelope:** `queries`, `generated_at`, `raw_candidates`, `unique_candidates`, `truncated`, `candidates_truncated`, `display_truncated`, `complete`, `failures`, `results`.
- **Tracking envelope:** `source_type`, `window_start`, `window_end`, `generated_at`, `raw_candidates`, `unique_candidates`, `truncated`, `failures`, `next_cursors`, `results`. Tracking **does not expose** a `complete` field.

If an upstream service fails, returns fewer results, exhausts pagination limits or throttles requests, do not assume the returned list is exhaustive. `truncated` can reflect candidate retrieval truncation **or** simply a longer result list than the display limit; examine the finer flags when available.

## CLI

```bash
researchlib search --query "protein language model" --mode quick --limit 10
researchlib search --query "微蛋白" --topic small-orfs --limit 10 # only limited Chinese words are supported
researchlib search --query "protein" --from-year 2020 --to-year 2026 --output full --format json

researchlib track --source venues --venue bioinformatics --days 30 --limit 10
researchlib track --source preprints --topic small-orfs --days 7 --limit 10
researchlib track --source preprints --sources arxiv --sources biorxiv --output compact --format json

researchlib paper-details --identifier "pmid:12345678" --identifier "arxiv:2401.12345" --format json
researchlib related-papers --doi "10.1000/example" --limit 20 --format json
researchlib lookup-doi --doi "10.1000/example" --format json

researchlib topics
researchlib venues
researchlib expand-query --topic small-orfs

researchlib doctor
```

All nine MCP tools are available through the CLI: `search`, `paper-details`, `related-papers`,
`lookup-doi`, `track --source venues`, `track --source preprints`, `topics`, `venues`, and
`expand-query`. Existing commands, including `doctor`, default to **human-readable text**.
Add `--format json` to any command for JSON; the nine tool routes return exactly the corresponding
MCP business payload, including validation errors and coverage metadata, without an extra wrapper.
JSON business errors are printed as data; malformed CLI options still produce a CLI usage error.

`search`, `track` and `related-papers` accept `--output compact|full` (default `compact`),
independently of `--format text|json` (default `text`). Other defaults and validation are delegated
to the MCP functions: `limit=10` is clamped to 1–100, tracking defaults to 7 days (minimum 1),
and `search` supports `--mode quick|deep`, `--topic`, `--from-year`, and `--to-year`.
Repeat `--sources` to select multiple preprint providers (omitting it preserves MCP defaults);
repeat `--identifier` (alias `--identifiers`) for 1–15 detail identifiers.
The sample identifiers above illustrate formats only. Unknown Chinese expressions without a
reliable mapping in `config/topics.yml` are rejected instead of being guessed.

## Configuration

- `config/default.yaml`: HTTP timeouts, retries, TTL cache, source settings, quick/deep budgets, and tracking pagination.
- `config/topics.yml`: topic synonyms, include/exclude/related terms and explicit Chinese mappings.
- `config/venues.yml`: configured journal names, ISSNs, tags and fetch limits.

To opt into OpenAlex semantic search, edit `config/default.yaml`:

```yaml
providers:
  openalex: {enabled: true, per_page: 50, semantic_search: true}
```

Keep the other provider entries in the file; the snippet is illustrative. An `arxiv` provider entry is **optional** because search enables arXiv by default, but you can explicitly disable it with `arxiv: {enabled: false, per_page: 50}`. `bioRxiv` and `medRxiv` are date-window tracking sources via `track_preprints`; they are not included in the normal `search_literature` registry.

## Testing and maintenance

```bash
python -m pytest -q                             # all tests (SDK integration skips if SDK is absent)
python -m pytest -q tests/test_mcp_contract_offline.py
python -m pytest -q tests/test_mcp_server.py     # requires official MCP Python SDK
```

Offline tests use mock API responses, including failure, year filter, query conversion, duplicate-ID, citation direction/top-N ordering, secret redaction and compact/full output checks. SDK integration tests exercise an **in-process MCP client**, including a mocked citation lookup, and launch the installed `researchlib-mcp` entrypoint over **stdio** to call all nine tools without external API queries (network tools use invalid inputs). They do not verify your separate Codex/CC Switch host configuration. Live API results/rate limits are **not** guaranteed by these tests. If an MCP test is skipped, install `mcp>=2,<3` / the console entrypoint as applicable and rerun it in the project's active environment. An actual client smoke test is still recommended after deployment.

Never check in `.env`, `.venv`, `.git` copies, API keys or downloaded papers. Keep the tests and MCP contract in this README up to date when changing tool names, parameters or output schemas.


### Troubleshooting: `DecodingError` after a DOI lookup (HTTP cache)

Versions of the in-memory cache before the gzip replay fix stored HTTPX's **already
uncompressed** response body together with the upstream `Content-Encoding: gzip`
header. On a later cache hit, replaying that header could cause HTTPX to attempt
a second decompression and raise `DecodingError: incorrect header check`.

The fix keeps the decoded body and removes `Content-Encoding`, `Content-Length`
and `Transfer-Encoding` before replaying cached responses. Restart the MCP server
after updating so the prior process cache is discarded. A `status: error`
response from `lookup_doi` does not establish whether the DOI exists.
Run `python -m pytest -q tests/test_http.py tests/test_providers.py` for the
corresponding gzip/deflate and Crossref lookup regression tests.
