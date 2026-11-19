"""Reusable index rebuild used by the indexer (on start and on schedule).

It runs the full download → clean → expand → process pipeline.
``LegalIndexBuilder`` publishes ``law.db`` and ``graph.db`` with
atomic ``os.replace`` calls, so hosted workers always see a complete index.
A file lock guarantees a single rebuild at a time across processes.
"""

from __future__ import annotations

import fcntl
import time
from pathlib import Path
from typing import Any

from .config import resolve_data_dir
from .logger import log
from .sources import surse_legislatie_just

LOCK_NAME = ".reindex.lock"


def reindex(
    force: bool = False,
    limit: int | None = None,
    data_dir: str | Path | None = None,
    ocr: bool = True,
) -> dict[str, Any]:
    """Run the full pipeline once; return a small JSON-serialisable summary.

    ``ocr=True`` matches the CLI's ``run-all`` default: base64 images embedded
    in the downloaded HTML are replaced with their OCR text. That is why the
    indexer container installs the optional ``ocr`` extra.
    """
    root = resolve_data_dir(data_dir)
    lock_path = root / LOCK_NAME
    started = time.perf_counter()

    with open(lock_path, "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.warning("Reindex already running; skipping this trigger.")
            return {"skipped": True, "reason": "reindex already running"}

        try:
            return _run_pipeline(
                root, force=force, limit=limit, ocr=ocr, started=started
            )
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _run_pipeline(
    root: Path,
    force: bool,
    limit: int | None,
    ocr: bool,
    started: float,
) -> dict[str, Any]:
    from .cleaner import LegislatieJustCleaner
    from .downloader import LegislatieJustDownloader
    from .expander import LegislatieJustExpander
    from .processor import LegalIndexBuilder

    surse = list(surse_legislatie_just)

    log.info("Reindex: step 1/4 download")
    downloader = LegislatieJustDownloader(
        surse=surse,
        data_dir=root,
        limit=limit,
        force=force,
        dry_run=False,
        all_versions=False,
        known_surse=surse,
    )
    downloaded = downloader.download()

    log.info("Reindex: step 2/4 clean")
    cleaner = LegislatieJustCleaner(
        surse=surse,
        data_dir=root,
        limit=limit,
        force=force,
        use_ocr=ocr,
    )
    cleaned = cleaner.cleanify()

    log.info("Reindex: step 3/4 expand")
    expander = LegislatieJustExpander(
        surse=surse,
        data_dir=root,
        limit=limit,
        force=force,
    )
    expanded = expander.expand()

    log.info("Reindex: step 4/4 process")
    builder = LegalIndexBuilder(
        surse=surse,
        data_dir=root,
        limit=limit,
        force=force,
    )
    processed = builder.build()

    return {
        "skipped": bool(processed.get("skipped")),
        "downloaded": downloaded.get("downloaded", 0),
        "failed": downloaded.get("failed", 0),
        "cleaned": cleaned.get("cleaned", 0),
        "converted": expanded.get("converted", 0),
        "documents": processed.get("documents", 0),
        "nodes": processed.get("nodes", 0),
        "refs": processed.get("refs", 0),
        "seconds": round(time.perf_counter() - started, 2),
    }
