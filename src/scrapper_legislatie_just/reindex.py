"""Reusable index rebuild used by the indexer (on start and on schedule).

It runs the full download -> clean -> expand -> process pipeline.
``Bm25IndexBuilder`` publishes ``4-processed/bm25`` and the metadata JSON files
atomically, so hosted workers always see a complete index. A file lock
guarantees a single rebuild at a time across processes.
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
) -> dict[str, Any]:
    """Run the full pipeline once; return a small JSON-serialisable summary."""
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
            return _run_pipeline(root, force=force, limit=limit, started=started)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _run_pipeline(
    root: Path,
    force: bool,
    limit: int | None,
    started: float,
) -> dict[str, Any]:
    from .bm25_index import Bm25IndexBuilder
    from .cleaner import LegislatieJustCleaner
    from .downloader import LegislatieJustDownloader
    from .expander import LegislatieJustExpander, pdf_sources_as_surse
    from .pdf_convert import ensure_ocr_model

    surse = list(surse_legislatie_just)

    log.info("Reindex: step 0/5 model OCR LiteParse (o singura data)")
    ensure_ocr_model(root)

    # PDF sources are discovered from 0-pdfs and published under 5-pdfs. They
    # are not downloaded from legislatie.just.ro, but the downloader must know
    # them so it does not treat their expanded folders as orphans.
    pdf_surse = pdf_sources_as_surse(root)
    all_surse = surse + pdf_surse

    log.info("Reindex: step 1/5 download")
    downloader = LegislatieJustDownloader(
        surse=surse,
        data_dir=root,
        limit=limit,
        force=force,
        dry_run=False,
        all_versions=False,
        known_surse=all_surse,
    )
    downloaded = downloader.download()

    log.info("Reindex: step 2/5 clean")
    cleaner = LegislatieJustCleaner(
        surse=all_surse,
        data_dir=root,
        limit=limit,
        force=force,
    )
    cleaned = cleaner.cleanify()

    log.info("Reindex: step 3/5 expand (HTML + PDF -> Markdown)")
    expander = LegislatieJustExpander(
        surse=surse,
        data_dir=root,
        limit=limit,
        force=force,
    )
    expanded = expander.expand()

    log.info("Reindex: step 4/5 process (BM25)")
    builder = Bm25IndexBuilder(
        surse=all_surse,
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
        "pdf_converted": expanded.get("pdf_converted", 0),
        "documents": processed.get("documents", 0),
        "nodes": processed.get("nodes", 0),
        "seconds": round(time.perf_counter() - started, 2),
    }
