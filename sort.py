#!/usr/bin/env python3
"""Movie Story Sorter — discover franchise media and sort by in-universe story order."""

import sys
from pathlib import Path

import typer
from rich.console import Console

from agents.orchestrator import Orchestrator
from services.config import ensure_env_file, has_tmdb_api_key

console = Console()
DEFAULT_QUERY = "Marvel"


def run_sort(
    query: str | None = None,
    release_order: bool = False,
    demo: bool = False,
    no_export: bool = False,
    output_dir: Path | None = None,
    verbose: bool = False,
) -> None:
    """Core entry point — callable from IDE Run button or CLI."""
    ensure_env_file()
    output_dir = output_dir or Path("output")

    if not query or not query.strip():
        if sys.stdin.isatty():
            query = typer.prompt("Franchise or movie name", default=DEFAULT_QUERY)
        else:
            query = DEFAULT_QUERY
            console.print(f"[dim]No search term provided — using default: {query}[/dim]\n")

    query = query.strip()
    use_demo = demo or not has_tmdb_api_key()

    if use_demo and not demo:
        console.print(
            "[yellow]No TMDb API key found — running in demo mode.[/yellow]\n"
            "Edit .env and set TMDB_API_KEY for full data (free at themoviedb.org/settings/api)\n"
        )
    elif demo:
        console.print("[cyan]Demo mode[/cyan] — curated story order only (no TMDb calls)\n")

    try:
        with Orchestrator(verbose=verbose, demo=use_demo) as orchestrator:
            orchestrator.run(
                query=query,
                release_order=release_order,
                export=not no_export,
                output_dir=output_dir,
            )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]Unexpected error:[/red] {exc}")
        if verbose:
            raise
        raise typer.Exit(code=1) from exc


def main(
    query: str | None = typer.Argument(
        None,
        help='Franchise or title to search, e.g. "Marvel" or "Iron Man"',
    ),
    release_order: bool = typer.Option(
        False,
        "--release-order",
        help="Sort by release date instead of story order.",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Run without TMDb API key using curated timeline data only.",
    ),
    no_export: bool = typer.Option(False, "--no-export", help="Skip JSON/TXT export."),
    output_dir: Path = typer.Option(Path("output"), "--output-dir", help="Export folder."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show agent logging."),
) -> None:
    """Search TMDb, fetch timelines, and print a story-order watch list."""
    run_sort(
        query=query,
        release_order=release_order,
        demo=demo,
        no_export=no_export,
        output_dir=output_dir,
        verbose=verbose,
    )


if __name__ == "__main__":
    # typer.run() binds args directly to main() — no extra "sort" subcommand needed.
    # Running this file from the IDE (Run button) works with default query "Marvel".
    typer.run(main)
