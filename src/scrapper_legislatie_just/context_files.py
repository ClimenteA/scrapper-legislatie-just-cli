"""Read PDFs, images and screenshots supplied by the user.

The legal index answers questions from ``legislatie-descarcata``. This module
covers the other half of a consultation: files the user drops into
``date-context`` (invoices, contracts, notices, screenshots). Docling is
imported lazily, so starting the MCP server does not pay the OCR cost until a
file is actually read.

Conversion results are cached under ``date-context/.converted`` keyed by the
file content, the OCR flag, the page range and a converter version. Reading the
same file twice is instant.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .config import resolve_context_dir

CACHE_DIR_NAME = ".converted"
CONVERTER_VERSION = "docling-v1"

SUPPORTED_EXTENSIONS = {
    ".pdf": "PDF",
    ".png": "imagine",
    ".jpg": "imagine",
    ".jpeg": "imagine",
    ".webp": "imagine",
    ".tif": "imagine",
    ".tiff": "imagine",
    ".bmp": "imagine",
    ".gif": "imagine",
}

MAX_CHARS_DEFAULT = 120_000
MAX_CHARS_LIMIT = 1_000_000

#: Extensions accepted by ``read_context``, for error messages.
SUPPORTED_LIST = ", ".join(sorted(SUPPORTED_EXTENSIONS))


def _context_root(directory: str | Path | None = None) -> Path:
    root = resolve_context_dir(directory)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _path_from_file_uri(raw: str) -> Path | None:
    if not raw.lower().startswith("file://"):
        return None
    parsed = urlparse(raw)
    value = unquote(parsed.path)
    # Windows file URIs look like file:///C:/path; drop the leading slash.
    if os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == ":":
        value = value[1:]
    return Path(value)


_MARKDOWN_LINK_RE = re.compile(r"^\[[^\]]*\]\((.+)\)$")


def _clean_raw_path(raw: str) -> str:
    """Accept plain paths, ``@path`` mentions and Markdown ``[x](path)`` links."""
    value = str(raw).strip().strip('"').strip("'")
    if value.startswith("@"):
        value = value[1:].strip().strip('"').strip("'")
    match = _MARKDOWN_LINK_RE.match(value)
    if match:
        value = match.group(1).strip().strip("<>")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_pages(pages: str | int | tuple[int, int] | None) -> tuple[int, int] | None:
    """Turn ``"1-5"``, ``"3"`` or ``(1, 5)`` into a Docling page range."""
    if pages is None or pages == "":
        return None
    if isinstance(pages, int):
        start = end = pages
    elif isinstance(pages, (tuple, list)) and len(pages) == 2:
        start, end = int(pages[0]), int(pages[1])
    else:
        text = str(pages).strip()
        if "-" in text:
            first, _, last = text.partition("-")
            start, end = int(first.strip()), int(last.strip())
        else:
            start = end = int(text)
    if start < 1 or end < start:
        raise ValueError(
            f"Interval de pagini invalid: {pages!r}. Foloseste '3' sau '1-5'."
        )
    return start, end


def _cache_path(
    path: Path,
    directory: Path,
    pages: tuple[int, int] | None,
    ocr: bool,
) -> Path:
    digest = hashlib.sha256()
    digest.update(_sha256(path).encode("ascii"))
    digest.update(
        f"|v={CONVERTER_VERSION}|ocr={int(ocr)}|pages={pages or ''}".encode("ascii")
    )
    return directory / CACHE_DIR_NAME / f"{digest.hexdigest()}.md"


def _write_cache(cache_path: Path, markdown: str) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.replace(cache_path)


def _convert_with_docling(
    path: Path,
    *,
    ocr: bool,
    pages: tuple[int, int] | None,
) -> str:
    """Convert one PDF/image to Markdown with Docling (CPU, OCR ro/en)."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            EasyOcrOptions,
            PdfPipelineOptions,
        )
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )
    except ImportError as ex:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "Docling nu este instalat. Ruleaza 'uv sync' (docling este o "
            "dependinta a proiectului)."
        ) from ex

    pipeline_options = PdfPipelineOptions()
    pipeline_options.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.CPU
    )
    pipeline_options.do_ocr = bool(ocr)
    if ocr:
        pipeline_options.ocr_options = EasyOcrOptions(lang=["ro", "en"])
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options.do_cell_matching = True

    if path.suffix.lower() == ".pdf":
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
    else:
        converter = DocumentConverter(
            format_options={
                InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options)
            }
        )

    kwargs: dict[str, Any] = {}
    if pages is not None:
        kwargs["page_range"] = pages
    result = converter.convert(path, **kwargs)
    return result.document.export_to_markdown().strip()


def find_context_file(
    path: str | Path,
    directory: str | Path | None = None,
) -> Path:
    """Resolve a user path to a real file.

    Accepts absolute paths, paths relative to the current directory, paths
    relative to ``date-context``, bare file names (searched recursively in
    ``date-context``) and ``file://`` URIs.
    """
    raw = _clean_raw_path(str(path))
    if not raw:
        raise ValueError("Lipseste calea fisierului.")

    root = _context_root(directory)
    from_uri = _path_from_file_uri(raw)
    candidate = from_uri if from_uri is not None else Path(raw).expanduser()

    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate)
    else:
        candidates.append(Path.cwd() / candidate)
        candidates.append(root / candidate)

    for item in candidates:
        if item.is_file():
            return item.resolve()

    if from_uri is None and len(candidate.parts) == 1:
        matches = [
            item
            for item in root.rglob(candidate.name)
            if item.is_file() and CACHE_DIR_NAME not in item.parts
        ]
        if len(matches) == 1:
            return matches[0].resolve()
        if len(matches) > 1:
            options = "\n".join(f"- {item}" for item in matches)
            raise ValueError(
                f"Mai multe fisiere cu numele {candidate.name!r} in {root}:\n"
                f"{options}\nTrimite calea completa."
            )

    raise FileNotFoundError(
        f"Nu am gasit fisierul {raw!r}. Pus in {root} sau trimite calea "
        f"completa. Formate acceptate: {SUPPORTED_LIST}."
    )


def list_context_files(directory: str | Path | None = None) -> list[dict[str, Any]]:
    """Return the files in ``date-context`` with metadata and ``file://`` links."""
    root = _context_root(directory)
    rows: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if path.name.startswith(".") or not path.is_file():
            continue
        stat = path.stat()
        rows.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "link": _file_uri(path),
                "kind": SUPPORTED_EXTENSIONS.get(path.suffix.lower(), "nesuportat"),
                "supported": _is_supported(path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                    timespec="seconds"
                ),
            }
        )
    return rows


def read_context(
    path: str | Path,
    directory: str | Path | None = None,
    *,
    pages: str | int | tuple[int, int] | None = None,
    max_chars: int = MAX_CHARS_DEFAULT,
    force: bool = False,
    ocr: bool = True,
) -> dict[str, Any]:
    """Convert a PDF/image to Markdown and return it with metadata.

    ``path`` may point outside ``date-context`` (for drag-and-drop paths in
    other clients). The cache always lives in ``date-context/.converted``.
    """
    source = find_context_file(path, directory)
    if not _is_supported(source):
        raise ValueError(
            f"Format nesuportat: {source.suffix or '(fara extensie)'}. "
            f"Formate acceptate: {SUPPORTED_LIST}."
        )

    page_range = _parse_pages(pages)
    if page_range is not None and source.suffix.lower() != ".pdf":
        raise ValueError("Optiunea 'pages' se aplica doar fisierelor PDF.")

    if max_chars < 1:
        raise ValueError("'max_chars' trebuie sa fie cel putin 1.")
    limit = min(int(max_chars), MAX_CHARS_LIMIT)

    root = _context_root(directory)
    cache_path = _cache_path(source, root, page_range, ocr)
    cached = cache_path.is_file() and not force
    if cached:
        markdown = cache_path.read_text(encoding="utf-8")
    else:
        markdown = _convert_with_docling(source, ocr=ocr, pages=page_range)
        _write_cache(cache_path, markdown)

    total_chars = len(markdown)
    truncated = total_chars > limit
    text = markdown[:limit] if truncated else markdown

    return {
        "name": source.name,
        "path": str(source),
        "link": _file_uri(source),
        "kind": SUPPORTED_EXTENSIONS.get(source.suffix.lower(), "fisier"),
        "pages": page_range,
        "ocr": ocr,
        "cached": cached,
        "cache_path": str(cache_path),
        "total_chars": total_chars,
        "returned_chars": len(text),
        "truncated": truncated,
        "markdown": text,
    }


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def format_context_list(
    files: list[dict[str, Any]],
    directory: str | Path | None = None,
) -> str:
    root = resolve_context_dir(directory)
    lines = [
        "# Fisiere in date-context",
        f"Folder: {root}",
        f"Formate acceptate: {SUPPORTED_LIST}",
        "",
    ]
    if not files:
        lines.append(
            "Nu exista fisiere. Pune un PDF sau o imagine in folder si "
            "reapeleaza tool-ul."
        )
        return "\n".join(lines)

    lines.extend(
        [
            "| fisier | tip | marime | modificat | link |",
            "|---|---|---|---|---|",
        ]
    )
    for item in files:
        kind = item["kind"] if item["supported"] else f"{item['kind']} (nu)"
        lines.append(
            f"| {item['name']} | {kind} | {_format_size(item['size'])} | "
            f"{item['modified']} | [deschide]({item['link']}) |"
        )
    unsupported = [item["name"] for item in files if not item["supported"]]
    if unsupported:
        lines.extend(
            [
                "",
                "Fisierele marcate '(nu)' nu pot fi citite de acest tool: "
                + ", ".join(unsupported),
            ]
        )
    return "\n".join(lines)


def format_context_result(result: dict[str, Any]) -> str:
    pages = result["pages"]
    page_label = f"{pages[0]}-{pages[1]}" if pages else "toate"
    lines = [
        f"# {result['name']}",
        f"SURSA: {result['link']}",
        (
            f"Tip: {result['kind']} | pagini: {page_label} | "
            f"OCR: {'da' if result['ocr'] else 'nu'} | "
            f"cache: {'hit' if result['cached'] else 'miss'}"
        ),
        (
            f"Caractere extrase: {result['total_chars']} "
            f"(afisate: {result['returned_chars']})"
        ),
        "",
        "---",
        "",
        result["markdown"],
    ]
    if not result["markdown"].strip():
        lines.extend(
            [
                "",
                "---",
                (
                    "[Nu s-a extras text. Verifica fisierul sau reapeleaza cu "
                    "force=true / ocr=true.]"
                ),
            ]
        )
    if result["truncated"]:
        lines.extend(
            [
                "",
                "---",
                (
                    f"[Text trunchiat: {result['returned_chars']} din "
                    f"{result['total_chars']} caractere. Reapeleaza cu "
                    "max_chars mai mare sau citeste un interval de pagini.]"
                ),
            ]
        )
    return "\n".join(lines)
