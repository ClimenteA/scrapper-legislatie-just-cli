"""PDF to Markdown conversion with LiteParse and the ocria-nano OCR model.

The model is pulled **once** into a persistent directory and reused by every
build. The directory is ``$LITEPARSE_MODEL_DIR`` when set, otherwise
``<data-dir>/models`` (bind-mounted with the rest of the corpus in Docker), so
recreating the container never downloads the ~60 MB package again.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import resolve_data_dir
from .logger import log
from .misc import strip_diacritics

OCR_MODEL = "ocria-nano"
MODEL_DIRNAME = "models"

_PARSER: Any | None = None


def model_dir(data_root: str | Path | None = None) -> Path:
    """Directory holding the LiteParse model packages."""
    env_dir = os.getenv("LITEPARSE_MODEL_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return (resolve_data_dir(data_root) / MODEL_DIRNAME).resolve()


def configure_model_dir(data_root: str | Path | None = None) -> Path:
    """Point LiteParse at the persistent model directory (set before import)."""
    directory = model_dir(data_root)
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["LITEPARSE_MODEL_DIR"] = str(directory)
    return directory


def ensure_ocr_model(data_root: str | Path | None = None) -> bool:
    """Pull the ocria-nano package if it is not installed yet.

    Returns ``True`` when a download happened. Safe to call at every startup:
    an installed package is detected without network access.
    """
    configure_model_dir(data_root)
    try:
        from liteparse import models
    except ImportError as ex:  # pragma: no cover - dependency is declared
        raise RuntimeError(
            "LiteParse is required for PDF ingestion. Install it with 'uv sync'."
        ) from ex

    installed = {item.name for item in models.list() if item.complete}
    if OCR_MODEL in installed:
        log.info(f"LiteParse OCR model '{OCR_MODEL}' already installed.")
        return False

    log.info(f"Descarc modelul LiteParse '{OCR_MODEL}' (o singura data)...")
    models.pull(OCR_MODEL)
    log.info("Modelul LiteParse a fost instalat in cache-ul persistent.")
    return True


def _parser() -> Any:
    """Build the LiteParse parser once per process."""
    global _PARSER
    if _PARSER is None:
        configure_model_dir()
        from liteparse import LiteParse

        _PARSER = LiteParse(
            output_format="markdown",
            ocr_backend=OCR_MODEL,
            dpi=250,
            extract_links=False,
            image_mode="off",
            extract_images=False,
            quiet=True,
            continue_on_page_error=True,
        )
    return _PARSER


def pdf_to_markdown(path: str | Path) -> str:
    """Convert one PDF to ASCII-folded Markdown.

    Diacritics are stripped on write, like the cleaned HTML, so the index and
    the user queries speak the same alphabet.
    """
    result = _parser().parse(str(path))
    text = (result.text or "").replace("\r\n", "\n").replace("\r", "\n")
    return strip_diacritics(text).strip()
