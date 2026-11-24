from __future__ import annotations

import json
import os
import shutil
import sys
import textwrap
from enum import Enum

import typer

from . import mcp_server as tools
from .config import load_settings
from .topics import load_topics
from .venues import Venue, load_venues

app = typer.Typer(no_args_is_help=True, help="Stateless real-time scientific literature search")


class Mode(str, Enum):
    quick = "quick"
    deep = "deep"


class Output(str, Enum):
    compact = "compact"
    full = "full"


class Format(str, Enum):
    text = "text"
    json = "json"


class Source(str, Enum):
    venues = "venues"
    preprints = "preprints"


def _show_json_or_error(data: dict, format: Format) -> bool:
    if format == Format.json:
        typer.echo(json.dumps(data, ensure_ascii=False))
        return True
    if "error" in data and data["error"]:
        raise typer.BadParameter(data["error"])
    return False


def _echo(text: str, *, fg: str | None = None, bold: bool = False, indent: str = "") -> None:
    """Wrap plain text before styling; never emit ANSI to redirected stdout."""
    width = max(20, shutil.get_terminal_size(fallback=(100, 24)).columns)
    for line in str(text).splitlines() or [""]:
        wrapped = textwrap.fill(
            line, width=width, initial_indent=indent, subsequent_indent=indent,
            break_long_words=False, break_on_hyphens=False,
        )
        typer.secho(wrapped, fg=fg, bold=bold,
                    color=sys.stdout.isatty() and "NO_COLOR" not in os.environ)


def _summary(data: dict, prefix: str = "") -> None:
    warning = data["truncated"] or data["failures"]
    _echo(
        f"{prefix}raw={data['raw_candidates']} | unique={data['unique_candidates']} | "
        f"truncated={'yes' if data['truncated'] else 'no'} | failures={len(data['failures'])}",
        fg=typer.colors.YELLOW if warning else typer.colors.GREEN,
    )
    _echo("-" * min(shutil.get_terminal_size(fallback=(100, 24)).columns, 64), fg=typer.colors.BRIGHT_BLACK)
    for source, message in data["failures"].items():
        _echo(f"Warning [{source}]: {message}", fg=typer.colors.YELLOW, indent="  ")


def _paper(paper: dict, number: int, *, sources: list[str] | None = None,
           score: float | None = None, abstract: bool = False) -> None:
    _echo(f"{number}. {paper['title']}", fg=typer.colors.CYAN, bold=True)
    metadata = f"Year: {paper.get('year') or '-'}"
    if "citation_count" in paper:
        metadata += f" | citations: {paper['citation_count'] if paper['citation_count'] is not None else '-'}"
    _echo(metadata, indent="   ")
    _echo(f"DOI: {paper.get('doi') or '-'}", indent="   ")
    origins = sources if sources is not None else ([paper["source"]] if paper.get("source") else [])
    metadata = f"Sources: {', '.join(origins)}" if origins else ""
    if score is not None:
        metadata += f"{' | ' if metadata else ''}score: {score:.4f}"
    if metadata:
        _echo(metadata, indent="   ")
    if abstract and paper.get("abstract"):
        _echo("Abstract:", fg=typer.colors.BRIGHT_BLACK, indent="   ")
        _echo(paper["abstract"], indent="   ")
    typer.echo()


@app.command()
def search(
    query: str = typer.Option(..., "--query", "-q"),
    mode: Mode = typer.Option(Mode.quick, help="quick or deep"),
    topic: str | None = typer.Option(None, help="Optional configured topic slug"),
    limit: int = typer.Option(10),
    from_year: int | None = typer.Option(None),
    to_year: int | None = typer.Option(None),
    output: Output = typer.Option(Output.compact),
    format: Format = typer.Option(Format.text, "--format"),
) -> None:
    """Search enabled scholarly APIs and return ranked results without storing them."""
    data = tools.search_literature(
        query=query, topic=topic, mode=mode.value, limit=limit,
        from_year=from_year, to_year=to_year, output=output.value,
    )
    if _show_json_or_error(data, format):
        return
    _summary(data)
    for number, hit in enumerate(data["results"], 1):
        _paper(hit.get("paper", hit), number, sources=hit["sources"], score=hit["score"])


@app.command()
def track(
    days: int = typer.Option(7),
    venue: str | None = typer.Option(None),
    topic: str | None = typer.Option(None),
    source: Source = typer.Option(Source.venues, help="venues or preprints"),
    limit: int = typer.Option(10),
    sources: list[str] | None = typer.Option(None, "--sources", help="Repeat for multiple preprint sources"),
    output: Output = typer.Option(Output.compact),
    format: Format = typer.Option(Format.text, "--format"),
) -> None:
    """Fetch a real-time date window from journals or preprint servers."""
    if source == Source.venues:
        data = tools.track_venues(days=days, venue=venue, limit=limit, output=output.value)
    else:
        data = tools.track_preprints(days=days, topic=topic, sources=sources, limit=limit, output=output.value)
    if _show_json_or_error(data, format):
        return
    _summary(data, prefix=f"source={source.value} | ")
    for number, paper in enumerate(data["results"], 1):
        _paper(paper, number)


@app.command("paper-details")
def paper_details(
    identifiers: list[str] = typer.Option(..., "--identifier", "--identifiers", help="Repeat for 1–15 identifiers"),
    format: Format = typer.Option(Format.text, "--format"),
) -> None:
    """Retrieve paper metadata and available abstracts by identifier."""
    data = tools.get_paper_details(identifiers=identifiers)
    if _show_json_or_error(data, format):
        return
    for number, item in enumerate(data["results"], 1):
        _paper(item["record"], number, sources=item.get("sources"), abstract=True)
    for key in ("invalid", "not_found", "unresolved", "failures"):
        if data.get(key):
            _echo(f"{key}: {data[key]}", fg=typer.colors.RED if key in {"invalid", "failures"} else typer.colors.YELLOW)


@app.command("related-papers")
def related_papers(
    doi: str = typer.Option(..., "--doi"),
    limit: int = typer.Option(10),
    output: Output = typer.Option(Output.compact),
    format: Format = typer.Option(Format.text, "--format"),
) -> None:
    """Find references and citing papers, each sorted by citation count."""
    data = tools.get_related_papers(doi=doi, limit=limit, output=output.value)
    if _show_json_or_error(data, format):
        return
    _echo(f"DOI: {data['doi']} | status={data['status']} | failures={len(data['failures'])}",
          fg=(typer.colors.RED if data["status"] in {"error", "invalid"} else
              typer.colors.GREEN if data["status"] == "ok" else typer.colors.YELLOW))
    for relation in ("references", "citations"):
        branch = data[relation]
        _echo(f"{relation}: total={branch['total']} | truncated={branch['truncated']} | complete={branch['complete']}",
              fg=typer.colors.CYAN, bold=True)
        for number, paper in enumerate(branch["results"], 1):
            _paper(paper, number)
    for source, message in data["failures"].items():
        _echo(f"Warning [{source}]: {message}", fg=typer.colors.YELLOW, indent="  ")


@app.command("lookup-doi")
def lookup_doi(
    doi: str = typer.Option(..., "--doi"),
    format: Format = typer.Option(Format.text, "--format"),
) -> None:
    """Verify a DOI via Crossref."""
    data = tools.lookup_doi(doi=doi)
    if _show_json_or_error(data, format):
        return
    _echo(f"DOI: {data['doi']} | status={data['status']} | verified={data['verified']}",
          fg=typer.colors.GREEN if data["verified"] else typer.colors.YELLOW)
    if data.get("record"):
        _paper(data["record"], 1)


@app.command("topics")
def list_topics(format: Format = typer.Option(Format.text, "--format")) -> None:
    data = tools.list_topics()
    if _show_json_or_error(data, format):
        return
    for slug, topic in data.items():
        _echo(f"{slug} | {topic['name']}", fg=typer.colors.CYAN, bold=True)
        _echo(f"strategy={topic['strategy'].upper()}", indent="   ")


@app.command("venues")
def list_venue_config(format: Format = typer.Option(Format.text, "--format")) -> None:
    data = tools.list_venues()
    if _show_json_or_error(data, format):
        return
    for slug, venue in data.items():
        _echo(f"{slug} | {venue['name']}", fg=typer.colors.CYAN, bold=True)
        _echo(f"ISSN: {', '.join(Venue.model_validate(venue).issns)}", indent="   ")


@app.command("expand-query")
def expand_query(
    topic: str = typer.Option(..., "--topic"),
    format: Format = typer.Option(Format.text, "--format"),
) -> None:
    data = tools.expand_query(topic=topic)
    if _show_json_or_error(data, format):
        return
    for number, query in enumerate(data["queries"], 1):
        _echo(f"{number}. {query}", fg=typer.colors.CYAN)


@app.command()
def doctor(format: Format = typer.Option(Format.text, "--format")) -> None:
    settings = load_settings()
    topics = load_topics(settings.tracking.topics_file)
    venues = load_venues(settings.tracking.venues_file)
    enabled = [name for name, config in settings.providers.items() if config.enabled]
    data = {"mode": "stateless", "providers": enabled, "topics": len(topics), "venues": len(venues)}
    if not _show_json_or_error(data, format):
        _echo(f"mode=stateless providers={','.join(enabled)} topics={len(topics)} venues={len(venues)}",
              fg=typer.colors.GREEN)
