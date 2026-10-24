from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .config import STEP_DIRS, resolve_data_dir
from .logger import configure_logging
from .sources import surse_legislatie_just

app = typer.Typer(
    name="scrapper-legislatie-just-cli",
    add_completion=False,
    no_args_is_help=True,
    help="Download, clean, expand and index Romanian legislation from legislatie.just.ro.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"scrapper-legislatie-just-cli {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        is_eager=True,
        callback=_version_callback,
        help="Show the version and exit.",
    ),
) -> None:
    """Download, clean, expand and index Romanian legislation."""


def _setup_logging(verbose: bool) -> None:
    configure_logging("DEBUG" if verbose else None)


def _run_download(downloader) -> dict:
    from .downloader import MonolithNotFoundError

    try:
        return downloader.download()
    except MonolithNotFoundError as ex:
        typer.secho(str(ex), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from ex


def _select_surse(slugs: Optional[list[str]]):
    """Return the configured sources selected by slug (all when omitted)."""
    if not slugs:
        return list(surse_legislatie_just)

    by_slug = {s.slug: s for s in surse_legislatie_just}
    missing = [slug for slug in slugs if slug not in by_slug]
    if missing:
        raise typer.BadParameter(
            "Unknown slug(s): "
            + ", ".join(missing)
            + ". Run 'scrapper-legislatie-just-cli list-sources' to see valid values.",
            param_hint="--slug",
        )

    # De-duplicate while keeping the user's order.
    return [by_slug[slug] for slug in dict.fromkeys(slugs)]


@app.command()
def download(
    slug: Optional[list[str]] = typer.Option(
        None, "--slug", "-s", help="Source slug (repeatable). Default: all sources."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n", min=1, help="Maximum number of documents to download."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Download even if a local file already exists."
    ),
    all_versions: bool = typer.Option(
        False,
        "--all-versions",
        help="Download every consolidation version, not just the latest.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Discover and print URLs without downloading anything.",
    ),
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir",
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-descarcata.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Step 1: download printable HTML files into <cwd>/legislatie-descarcata/1-raw."""
    _setup_logging(verbose)

    from .downloader import LegislatieJustDownloader

    downloader = LegislatieJustDownloader(
        surse=_select_surse(slug),
        data_dir=data_dir,
        limit=limit,
        force=force,
        dry_run=dry_run,
        all_versions=all_versions,
    )
    result = _run_download(downloader)

    typer.secho(
        f"Download complete: {result['downloaded']} saved, "
        f"{result['failed']} failed.",
        fg=typer.colors.GREEN if not result["failed"] else typer.colors.YELLOW,
    )
    if result["failed_urls"]:
        for url in result["failed_urls"]:
            typer.echo(f"  failed: {url}", err=True)


@app.command()
def clean(
    slug: Optional[list[str]] = typer.Option(
        None, "--slug", "-s", help="Source slug (repeatable). Default: all sources."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-n", min=1, help="Maximum number of files to clean."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-clean files that already exist."
    ),
    ocr: bool = typer.Option(
        True,
        "--ocr/--no-ocr",
        help="Run OCR on embedded base64 images (slow).",
    ),
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir",
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-descarcata.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Step 2: clean raw HTML and write semantic HTML files into <cwd>/legislatie-descarcata/2-clean."""
    _setup_logging(verbose)

    from .cleaner import LegislatieJustCleaner

    cleaner = LegislatieJustCleaner(
        surse=_select_surse(slug),
        data_dir=data_dir,
        limit=limit,
        force=force,
        use_ocr=ocr,
    )
    result = cleaner.cleanify()

    typer.secho(
        f"Clean complete: {result['cleaned']} cleaned, {result['skipped']} skipped.",
        fg=typer.colors.GREEN,
    )


@app.command()
def expand(
    slug: Optional[list[str]] = typer.Option(
        None, "--slug", "-s", help="Source slug (repeatable). Default: all sources."
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-n",
        min=1,
        help="Maximum number of documents to convert to Markdown.",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-copy and re-convert existing files."
    ),
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir",
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-descarcata.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Step 3: group documents, build the TOC and convert HTML to Markdown."""
    _setup_logging(verbose)

    from .expander import LegislatieJustExpander

    expander = LegislatieJustExpander(
        surse=_select_surse(slug),
        data_dir=data_dir,
        limit=limit,
        force=force,
    )
    result = expander.expand()

    typer.secho(
        f"Expand complete: {result['copied']} copied, {result['tocs']} TOC files, "
        f"{result['converted']} Markdown files, {result['skipped']} skipped.",
        fg=typer.colors.GREEN,
    )


@app.command()
def process(
    slug: Optional[list[str]] = typer.Option(
        None, "--slug", "-s", help="Source slug (repeatable). Default: all sources."
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-n",
        min=1,
        help="Maximum number of documents to index.",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Rebuild even if the source files are unchanged."
    ),
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir",
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-descarcata.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Step 4: build the SQLite/FTS5 legal index and the OpenCode instructions."""
    _setup_logging(verbose)

    from .processor import LegalIndexBuilder

    builder = LegalIndexBuilder(
        surse=_select_surse(slug),
        data_dir=data_dir,
        limit=limit,
        force=force,
    )
    result = builder.build()
    if result["skipped"]:
        typer.secho(
            "Process complete: index is already up to date (use --force to rebuild).",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            f"Process complete: {result['documents']} documents, "
            f"{result['nodes']} nodes, {result['refs']} refs, "
            f"{result['graph_edges']} graph edges in {result['seconds']}s.",
            fg=typer.colors.GREEN,
        )
    typer.echo(f"index:    {builder.db_path}")
    typer.echo(f"graph:    {builder.graph_path}")
    typer.echo(f"catalog:  {builder.processed_dir / 'catalog.json'}")


def _open_index(data_dir: Optional[Path]):
    from .query import LegalIndex

    try:
        return LegalIndex(data_dir=data_dir)
    except FileNotFoundError as ex:
        typer.secho(str(ex), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from ex


@app.command()
def search(
    query: str = typer.Argument(..., help="Cuvinte-cheie sau intrebare."),
    doc: Optional[str] = typer.Option(
        None, "--doc", help="Limiteaza cautarea la un singur act (slug)."
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Limiteaza la un tip de nod (articol, alineat, ...)."
    ),
    limit: int = typer.Option(8, "--limit", "-n", min=1, max=50),
    all_versions: bool = typer.Option(
        False, "--all-versions", help="Include consolidarile vechi."
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Search the legal index and print clickable file:// links."""
    index = _open_index(data_dir)
    try:
        results = index.search(
            query,
            doc=doc,
            kind=kind,
            limit=limit,
            latest_only=not all_versions,
        )
        typer.echo(index.format_search(results))
    finally:
        index.close()


@app.command()
def article(
    doc: str = typer.Argument(..., help="Slug-ul actului (ex. codul-fiscal)."),
    number: str = typer.Argument(..., help="Numarul articolului sau codul (ex. 60, 6210)."),
    version: Optional[str] = typer.Option(
        None, "--version", help="Data consolidarii (implicit: ultima)."
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Print one article with all its paragraphs and clickable links."""
    index = _open_index(data_dir)
    try:
        data = index.article(doc, number, version=version)
        typer.secho(f"# {data['doc_title']} — {data['label']}", bold=True)
        typer.echo(f"SURSA: {data['link']}")
        if data.get("date"):
            typer.echo(f"Versiune: {data['date']}")
        typer.echo()
        typer.echo(data["text"])
    except KeyError as ex:
        typer.secho(str(ex), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from ex
    finally:
        index.close()


@app.command()
def show(
    node_id: str = typer.Argument(..., help="Node id exact (ex. codul-fiscal@2026-10-25#s_art_87)."),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Show one node, its ancestors and its citations."""
    index = _open_index(data_dir)
    try:
        data = index.show(node_id)
        typer.secho(f"# {data['doc_title']} — {data['label']}", bold=True)
        typer.echo(f"SURSA: {data['link']}")
        typer.echo()
        typer.echo(data["text"])
        if data["ancestors"]:
            typer.echo("\n**Parinti:**")
            for item in data["ancestors"]:
                typer.echo(f"- {item['label']} — {item['link']}")
        outgoing = data["refs"]["outgoing"]
        if outgoing:
            typer.echo("\n**Trimiteri:**")
            for ref in outgoing:
                label = ref["label"] or ref["raw"] or ref["target"]
                typer.echo(f"- [{label}] {ref['link'] or ref['raw']}")
    except KeyError as ex:
        typer.secho(str(ex), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from ex
    finally:
        index.close()


@app.command("refs")
def refs(
    node_id: str = typer.Argument(..., help="Node id exact."),
    subtree: bool = typer.Option(
        False, "--subtree", help="Include tot articolul, nu doar nodul."
    ),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Show incoming and outgoing citations for a node."""
    index = _open_index(data_dir)
    try:
        data = index.neighbors(node_id, subtree=subtree)
        for direction, label in (("outgoing", "Trimiteri din nod"), ("incoming", "Citat de")):
            typer.secho(f"== {label} ==", bold=True)
            if not data[direction]:
                typer.echo("(niciuna)")
                continue
            for ref in data[direction]:
                target = ref["label"] or ref["raw"] or ref["target"]
                typer.echo(f"- [{ref['rel']}] {target} — {ref['link'] or ref['raw']}")
    finally:
        index.close()


@app.command()
def context(
    question: str = typer.Argument(..., help="Intrebarea completa."),
    limit: int = typer.Option(4, "--limit", "-n", min=1, max=12),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Build an evidence pack (articles + citations) for a question."""
    index = _open_index(data_dir)
    try:
        typer.echo(index.context(question, limit=limit))
    finally:
        index.close()


@app.command()
def docs(
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """List the indexed acts and their consolidation dates."""
    index = _open_index(data_dir)
    try:
        for item in index.docs():
            mark = "latest" if item["is_latest"] else "older "
            typer.echo(
                f"{item['slug']:<45} {item['version']:<12} {mark} {item['title'][:70]}"
            )
    finally:
        index.close()


@app.command()
def cypher(
    query: str = typer.Argument(..., help="Interogare Cypher (GraphQLite)."),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
) -> None:
    """Run a Cypher query against the citation graph in 4-processed/graph.db."""
    from .config import step_dir

    from .graph import run_cypher

    root = resolve_data_dir(data_dir)
    try:
        rows = run_cypher(step_dir(root, "processed") / "graph.db", query)
    except (FileNotFoundError, RuntimeError) as ex:
        typer.secho(str(ex), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from ex
    for row in rows:
        typer.echo(row)


@app.command("run-all")
def run_all(
    slug: Optional[list[str]] = typer.Option(
        None, "--slug", "-s", help="Source slug (repeatable). Default: all sources."
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-n",
        min=1,
        help="Maximum number of documents handled by each step.",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Redo existing work in every step."
    ),
    all_versions: bool = typer.Option(
        False,
        "--all-versions",
        help="Download every consolidation version, not just the latest.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Only discover download URLs; the other steps are skipped.",
    ),
    ocr: bool = typer.Option(
        True,
        "--ocr/--no-ocr",
        help="Run OCR on embedded base64 images (slow).",
    ),
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir",
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-descarcata.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Run steps 1-4 in order (download, clean, expand, process)."""
    _setup_logging(verbose)

    from .cleaner import LegislatieJustCleaner
    from .downloader import LegislatieJustDownloader
    from .expander import LegislatieJustExpander
    from .processor import LegalIndexBuilder

    selected = _select_surse(slug)

    typer.secho("== Step 1/4: download ==", bold=True)
    downloader = LegislatieJustDownloader(
        surse=selected,
        data_dir=data_dir,
        limit=limit,
        force=force,
        dry_run=dry_run,
        all_versions=all_versions,
    )
    result = _run_download(downloader)
    typer.echo(
        f"Download complete: {result['downloaded']} saved, "
        f"{result['failed']} failed."
    )

    if dry_run:
        typer.secho(
            "Dry run: skipping clean, expand and process.",
            fg=typer.colors.YELLOW,
        )
        return

    typer.secho("== Step 2/4: clean ==", bold=True)
    cleaner = LegislatieJustCleaner(
        surse=selected,
        data_dir=data_dir,
        limit=limit,
        force=force,
        use_ocr=ocr,
    )
    result = cleaner.cleanify()
    typer.echo(f"Clean complete: {result['cleaned']} cleaned.")

    typer.secho("== Step 3/4: expand ==", bold=True)
    expander = LegislatieJustExpander(
        surse=selected,
        data_dir=data_dir,
        limit=limit,
        force=force,
    )
    result = expander.expand()
    typer.echo(
        f"Expand complete: {result['converted']} Markdown files, "
        f"{result['tocs']} TOC files."
    )

    typer.secho("== Step 4/4: process ==", bold=True)
    builder = LegalIndexBuilder(
        surse=selected,
        data_dir=data_dir,
        limit=limit,
        force=force,
    )
    result = builder.build()
    typer.echo(
        f"Process complete: {result['documents']} documents, "
        f"{result['nodes']} nodes, {result['refs']} refs, "
        f"{result['graph_edges']} graph edges."
    )

    typer.secho("All steps finished.", fg=typer.colors.GREEN, bold=True)


@app.command("list-sources")
def list_sources() -> None:
    """List the configured legislation sources and their slugs."""
    for s in surse_legislatie_just:
        typer.echo(f"{s.slug:<65} {s.numar_link:>8}  {s.nume}")


@app.command()
def paths(
    data_dir: Optional[Path] = typer.Option(
        None,
        "--data-dir",
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-descarcata.",
    ),
) -> None:
    """Show the resolved data directories."""
    root = resolve_data_dir(data_dir)
    typer.echo(f"data:     {root}")
    for name in STEP_DIRS.values():
        typer.echo(f"{name + ':':<12}{root / name}")
