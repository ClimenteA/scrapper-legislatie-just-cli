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
    help="Download, clean and expand Romanian legislation from legislatie.just.ro.",
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
    """Scrapper fiscal command line interface."""


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
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-fiscala.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Step 1: download printable HTML files into <cwd>/legislatie-fiscala/1-raw."""
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
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-fiscala.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Step 2: clean raw HTML and write semantic HTML files into <cwd>/legislatie-fiscala/2-clean."""
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
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-fiscala.",
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
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-fiscala.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Run steps 1-3 in order (download, clean, expand)."""
    _setup_logging(verbose)

    from .cleaner import LegislatieJustCleaner
    from .downloader import LegislatieJustDownloader
    from .expander import LegislatieJustExpander

    selected = _select_surse(slug)

    typer.secho("== Step 1/3: download ==", bold=True)
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
        typer.secho("Dry run: skipping clean and expand.", fg=typer.colors.YELLOW)
        return

    typer.secho("== Step 2/3: clean ==", bold=True)
    cleaner = LegislatieJustCleaner(
        surse=selected,
        data_dir=data_dir,
        limit=limit,
        force=force,
        use_ocr=ocr,
    )
    result = cleaner.cleanify()
    typer.echo(f"Clean complete: {result['cleaned']} cleaned.")

    typer.secho("== Step 3/3: expand ==", bold=True)
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
        help="Data root. Default: $SCRAPPER_DATA_DIR or <cwd>/legislatie-fiscala.",
    ),
) -> None:
    """Show the resolved data directories."""
    root = resolve_data_dir(data_dir)
    typer.echo(f"data:     {root}")
    for name in STEP_DIRS.values():
        typer.echo(f"{name + ':':<12}{root / name}")
