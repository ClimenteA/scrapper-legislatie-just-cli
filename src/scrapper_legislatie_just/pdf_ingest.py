"""Discovery of the PDF corpus (``0-pdfs``) and its public copies (``5-pdfs``).

Only metadata lives here: the actual PDF -> Markdown conversion is done by
:mod:`pdf_convert`. Two PDF families are supported:

* ANAF declarations (``0-pdfs/declaratii-pdf/manifest.json``). XFA forms are
  dynamic, so the flattened ``simple_pdf`` sibling is the parseable source.
* EU acts (``0-pdfs/eu-pdfs/*.pdf``).

Every source PDF is hard-linked (or copied) into ``5-pdfs/<slug>/`` so the web
app can serve the exact form a taxpayer downloads, at ``/pdfs/*``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .config import (
    DATA_ROOT_NAME,
    PDF_SOURCES_DIRNAME,
    get_default_data_dir,
)
from .logger import log
from .misc import strip_diacritics

PDF_DATA_DIRNAME = "5-pdfs"
DECLARATIONS_DIRNAME = "declaratii-pdf"
EU_PDFS_DIRNAME = "eu-pdfs"
MANIFEST_FILENAME = "manifest.json"
PDF_DATE = "2000-01-01"
PDF_NUMBER_BASE = 9_000_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slugify(value: str) -> str:
    text = strip_diacritics(value).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-+", "-", text)[:80] or "document"


def resolve_pdf_sources_dir(
    explicit: str | Path | None = None,
    data_root: str | Path | None = None,
) -> Path:
    """Locate the PDF corpus (explicit argument, env var, then ``<data>/0-pdfs``)."""
    if explicit:
        return Path(explicit).expanduser().resolve()

    env_dir = os.environ.get("SCRAPPER_PDF_SOURCES_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    if data_root:
        return (Path(data_root).expanduser() / PDF_SOURCES_DIRNAME).resolve()

    # Walk up from cwd so the code also works when started from a subfolder.
    for base in (Path.cwd().resolve(), *Path(__file__).resolve().parents):
        candidate = base / DATA_ROOT_NAME / PDF_SOURCES_DIRNAME
        if candidate.is_dir():
            return candidate.resolve()
    return (get_default_data_dir() / PDF_SOURCES_DIRNAME).resolve()


def load_declaration_manifest(sources_dir: Path) -> list[dict[str, Any]]:
    """Return the usable records of ``declaratii-pdf/manifest.json``."""
    manifest_path = sources_dir / DECLARATIONS_DIRNAME / MANIFEST_FILENAME
    if not manifest_path.exists():
        if sources_dir.exists():
            log.warning(f"Lipseste manifestul declaratiilor: {manifest_path}")
        return []
    try:
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        log.warning(f"Nu pot citi {manifest_path}: {ex}")
        return []

    items: list[dict[str, Any]] = []
    base = sources_dir / DECLARATIONS_DIRNAME
    for record in records:
        if not isinstance(record, dict) or record.get("error"):
            continue
        relative = record.get("file")
        if not relative:
            continue
        # XFA forms are dynamic; the flattened sibling is the parseable one.
        if record.get("xfa") and record.get("simple_pdf"):
            relative = record["simple_pdf"]
        source_path = base / relative
        if not source_path.is_file():
            log.warning(f"PDF lipsa in manifest: {source_path}")
            continue
        code = str(record.get("declaration") or _slugify(relative))
        description = strip_diacritics(str(record.get("description") or ""))
        items.append(
            {
                "kind": "declaratie",
                "slug": f"declaratie-{_slugify(code)}",
                "code": code,
                "title": description[:300] or f"Declaratia {code}",
                "description": description,
                "source_url": record.get("url"),
                "source_path": source_path,
                "version": str(record.get("version") or "curent"),
                "xfa": bool(record.get("xfa")),
                "simple_pdf": record.get("simple_pdf"),
                "manifest_file": record.get("file"),
                "artifact": record.get("artifact") or "pdf",
            }
        )
    return items


def discover_eu_pdfs(sources_dir: Path) -> list[dict[str, Any]]:
    """List the static EU legal PDFs as ingestion descriptors."""
    folder = sources_dir / EU_PDFS_DIRNAME
    if not folder.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.pdf")):
        stem = strip_diacritics(path.stem)
        number = re.search(r"nr\.?\s*([0-9./]+)", stem, re.IGNORECASE)
        code = number.group(1).replace("/", "-") if number else _slugify(stem)
        items.append(
            {
                "kind": "act-ue",
                "slug": f"ue-{_slugify(stem)}",
                "code": code,
                "title": stem[:300],
                "description": stem,
                "source_url": None,
                "source_path": path,
                "version": "curent",
                "xfa": False,
                "simple_pdf": None,
                "manifest_file": None,
                "artifact": "pdf",
            }
        )
    return items


def pdf_descriptors(
    data_root: str | Path, sources_dir: str | Path | None = None
) -> list[dict[str, Any]]:
    """Discover PDFs and attach a stable synthetic number and Markdown name."""
    sources = resolve_pdf_sources_dir(sources_dir, data_root=data_root)
    descriptors = load_declaration_manifest(sources)
    descriptors += discover_eu_pdfs(sources)

    used: set[int] = set()
    for descriptor in sorted(descriptors, key=lambda item: item["slug"]):
        seed = int(
            hashlib.sha1(descriptor["slug"].encode("utf-8")).hexdigest()[:6], 16
        )
        number = PDF_NUMBER_BASE + seed % 1_000_000
        while number in used:
            number += 1
        used.add(number)
        descriptor["numar_link"] = number
        descriptor["date"] = PDF_DATE
        # One fixed Markdown name per PDF slug: the indexer can find it without
        # re-reading the manifest.
        descriptor["md_name"] = f"{descriptor['slug']}.md"
    return descriptors


def sync_pdf_into_data_dir(source: Path, data_root: Path, slug: str) -> Path:
    """Expose a source PDF under ``5-pdfs/<slug>/`` (hard link when possible)."""
    target_dir = data_root / PDF_DATA_DIRNAME / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if target.exists() and _sha256(target) == _sha256(source):
        return target
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def load_pdf_actions(
    data_root: str | Path,
    *,
    sources_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build ANAF action rows from the declaration manifest (no PDF parsing)."""
    root = Path(data_root).expanduser().resolve()
    sources = resolve_pdf_sources_dir(sources_dir, data_root=root)
    actions: list[dict[str, Any]] = []
    for descriptor in load_declaration_manifest(sources):
        served = (
            root
            / PDF_DATA_DIRNAME
            / descriptor["slug"]
            / descriptor["source_path"].name
        )
        actions.append(
            {
                "doc_slug": descriptor["slug"],
                "code": descriptor["code"],
                "title": descriptor["title"],
                "description": descriptor["description"],
                "source_url": descriptor.get("source_url"),
                "pdf_rel": relative_to_root(served, root),
                "xfa": int(descriptor["xfa"]),
                "simple_pdf": descriptor.get("simple_pdf"),
                "artifact": descriptor.get("artifact") or "pdf",
            }
        )
    return actions
