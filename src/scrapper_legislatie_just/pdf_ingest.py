"""Unified Docling graph ingestion for the PDF sources.

The HTML pipeline keeps source-of-truth anchors from ``2-clean``.  PDFs are
converted with Docling and then mapped, *deterministically* (no embeddings, no
LLM, no API calls), onto the same ``RawNode`` shape used by ``processor.py``.
The result is written as a graph document (``3-expanded/<slug>/graph.json``) so
HTML acts and PDF acts share one representation in the index and in the
exported Docling graph.

Sources:

* ``legislatie-descarcata/0-pdfs/declaratii-pdf/manifest.json`` — ANAF forms.
  XFA forms are dynamic and cannot be parsed reliably, so the flattened
  ``simple_pdf`` is ingested when the manifest marks ``xfa: true``.
* ``legislatie-descarcata/0-pdfs/eu-pdfs/*.pdf`` — EU VAT/customs acts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from .config import (
    DATA_ROOT_NAME,
    PDF_SOURCES_DIRNAME,
    get_default_data_dir,
    step_dir,
)
from .logger import log
from .misc import strip_diacritics
from .processor import RawNode, clean_text

PDF_DATA_DIRNAME = "5-pdfs"
DECLARATIONS_DIRNAME = "declaratii-pdf"
EU_PDFS_DIRNAME = "eu-pdfs"
MANIFEST_FILENAME = "manifest.json"
GRAPH_FILENAME = "graph.json"

# Deterministic structure recognition on Docling text items.
CHAPTER_RE = re.compile(r"^(?:CAPITOLUL|CAPITOL|CHAPTER)\b", re.IGNORECASE)
SECTION_RE = re.compile(r"^(?:SEC\w*TIUNEA|SECTION)\b", re.IGNORECASE)
ARTICLE_RE = re.compile(
    r"^(?:ARTICOLUL|ARTICOLELE|ART\.|ART)\s*([0-9]+(?:\s*[a-z])?)\b",
    re.IGNORECASE,
)
LETTER_RE = re.compile(r"^\(([a-z]{1,3})\)\s")
NUMBER_RE = re.compile(r"^\(?([0-9]{1,2})\)\s")


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
        items.append(
            {
                "kind": "declaratie",
                "slug": f"declaratie-{_slugify(code)}",
                "code": code,
                "title": clean_text(
                    str(record.get("description") or f"Declaratia {code}")
                )[:300],
                "description": clean_text(str(record.get("description") or "")),
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
        stem = path.stem
        number = re.search(r"nr\.?\s*([0-9./]+)", stem, re.IGNORECASE)
        code = number.group(1).replace("/", "-") if number else _slugify(stem)
        items.append(
            {
                "kind": "act-ue",
                "slug": f"ue-{_slugify(stem)}",
                "code": code,
                "title": clean_text(stem)[:300],
                "description": clean_text(stem),
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


def sync_pdf_into_data_dir(source: Path, data_root: Path, slug: str) -> Path:
    """Expose a source PDF under ``5-pdfs/<slug>/`` (hard link when possible).

    Keeping one data root makes the hosted links stable: Caddy serves
    ``/pdfs/*`` from that directory only.  Hard links avoid duplicating the
    120 MB declaration corpus on the same filesystem.
    """
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


def _item_page(item: Any, default: int = 1) -> int:
    for prov in getattr(item, "prov", []) or []:
        page = getattr(prov, "page_no", None)
        if isinstance(page, int) and page > 0:
            return page
    return default


def _iter_docling_items(document: Any) -> Iterable[tuple[Any, int]]:
    try:
        yield from document.iterate_items()
    except (AttributeError, TypeError):
        for item in getattr(document, "items", []):
            yield item, 0


def convert_pdf_to_graph_nodes(
    document: Any,
    *,
    slug: str,
    version: str,
    title: str,
    html_rel: str,
) -> list[RawNode]:
    """Map a DoclingDocument to deterministic, linkable nodes.

    Headings are recognised from the Docling outline level *and* from the legal
    wording (``Articolul``/``Capitolul``/...) so the same code works for EU
    regulations and for ANAF forms.
    """
    base = f"{slug}@{version}"
    root = RawNode(
        id=f"{base}#root",
        anchor=None,
        kind="document",
        label=title,
        text="",
        parent=None,
        path=title,
        depth=0,
        order_idx=0,
        number=None,
        article_id=None,
        doc_slug=slug,
        doc_version=version,
    )
    nodes: list[RawNode] = [root]
    current = root
    last_page = 1
    counter = 0

    for item, level in _iter_docling_items(document):
        text = clean_text(getattr(item, "text", "") or "")
        if not text or len(text) < 3:
            continue
        label = str(getattr(item, "label", "") or "").lower()
        page = _item_page(item, last_page)
        last_page = page
        counter += 1
        anchor = f"p{page}_g{counter}"

        kind: str | None = None
        number: str | None = None
        if ARTICLE_RE.match(text):
            kind, number = "articol", _article_number(text)
        elif CHAPTER_RE.match(text):
            kind = "capitol"
        elif SECTION_RE.match(text):
            kind = "sectiune"
        elif label in {"title", "subtitle"} or (isinstance(level, int) and level > 0):
            kind = "titlu" if counter < 6 else "sectiune"
        elif LETTER_RE.match(text):
            kind = "litera"
        elif NUMBER_RE.match(text):
            kind = "alineat"
        else:
            kind = "paragraf"

        if kind in {"articol", "capitol", "sectiune", "titlu"}:
            parent = root if kind in {"capitol", "titlu"} else (
                current if current.kind in {"capitol", "sectiune"} else root
            )
            node_text = ""
        else:
            parent = current
            node_text = text

        node = RawNode(
            id=f"{base}#{anchor}",
            anchor=anchor,
            kind=kind,
            label=text[:180] if kind != "paragraf" else "",
            text=node_text,
            parent=parent.id,
            path=f"{parent.path} > {text[:60]}" if kind != "paragraf" else parent.path,
            depth=parent.depth + 1,
            order_idx=counter,
            number=number,
            article_id=f"{base}#{anchor}" if kind == "articol" else parent.article_id,
            doc_slug=slug,
            doc_version=version,
        )
        nodes.append(node)
        if kind in {"articol", "capitol", "sectiune", "titlu"}:
            current = node
    return nodes


def _article_number(text: str) -> str | None:
    match = ARTICLE_RE.match(text)
    return match.group(1).strip() if match else None


_CONVERTERS: dict[bool, Any] = {}


def _docling_document(path: Path, *, ocr: bool):
    """Convert one PDF, reusing the (expensive to build) converter.

    Creating a ``DocumentConverter`` loads model configuration for every file;
    with hundreds of PDFs that dominates the runtime. Build it once per OCR
    mode and share it across all conversions in the process.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    key = bool(ocr)
    converter = _CONVERTERS.get(key)
    if converter is None:
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        options = PdfPipelineOptions()
        options.do_ocr = ocr
        options.do_table_structure = True
        if ocr:
            from docling.datamodel.pipeline_options import EasyOcrOptions

            options.ocr_options = EasyOcrOptions(lang=["ro", "en"])
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        _CONVERTERS[key] = converter
    return converter.convert(path).document


def _graph_doc_path(root: Path, slug: str) -> Path:
    return step_dir(root, "expanded") / slug / GRAPH_FILENAME


def write_graph_doc(root: Path, slug: str, payload: dict[str, Any]) -> Path:
    """Persist one uniform Docling graph document (also used for HTML acts)."""
    path = _graph_doc_path(root, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def load_graph_doc(root: Path, slug: str) -> dict[str, Any] | None:
    path = _graph_doc_path(root, slug)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        log.warning(f"Nu pot citi {path}: {ex}")
        return None


def _restore_nodes(payload: dict[str, Any], slug: str, version: str) -> list[RawNode]:
    nodes = []
    for raw in payload.get("nodes", []):
        data = dict(raw)
        data["doc_slug"] = slug
        data["doc_version"] = version
        data.pop("citations", None)
        nodes.append(RawNode(**data))
    return nodes


def ingest_pdf_sources(
    data_root: Path,
    *,
    sources_dir: Path | None = None,
    force: bool = False,
    ocr: bool = False,
) -> tuple[list[dict[str, Any]], list[RawNode], list[dict[str, Any]]]:
    """Convert every PDF source into cached graph documents.

    Returns ``(documents, nodes, actions)`` ready for ``LegalIndexBuilder``.
    Unchanged PDFs are loaded from ``graph.json``; a changed manifest or PDF
    triggers only that document's reconversion.
    """
    # Scope the PDF corpus to the data root: ``<data-dir>/0-pdfs``. A temp data
    # dir used by a test must never ingest the real corpus; deployments can
    # still override the location with SCRAPPER_PDF_SOURCES_DIR.
    sources_dir = resolve_pdf_sources_dir(sources_dir, data_root=data_root)
    descriptors = load_declaration_manifest(sources_dir) + discover_eu_pdfs(sources_dir)
    if not descriptors:
        return [], [], []

    documents: list[dict[str, Any]] = []
    nodes: list[RawNode] = []
    actions: list[dict[str, Any]] = []

    for descriptor in descriptors:
        source_path: Path = descriptor["source_path"]
        slug: str = descriptor["slug"]
        version = str(descriptor["version"])
        file_hash = _sha256(source_path)
        meta_hash = hashlib.sha256(
            json.dumps(
                {
                    "title": descriptor["title"],
                    "description": descriptor["description"],
                    "source_url": descriptor.get("source_url"),
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        cached = load_graph_doc(data_root, slug)
        fresh = (
            cached is not None
            and cached.get("source_hash") == file_hash
            and cached.get("meta_hash") == meta_hash
            and cached.get("version") == version
        )
        if fresh and not force:
            payload = cached
        else:
            try:
                docling_doc = _docling_document(source_path, ocr=ocr)
            except Exception as ex:  # a broken PDF must not kill the reindex
                log.warning(f"Nu pot converti {source_path}: {ex}")
                continue
            converted = convert_pdf_to_graph_nodes(
                docling_doc,
                slug=slug,
                version=version,
                title=descriptor["title"],
                html_rel="",
            )
            payload = {
                "source_type": descriptor["kind"],
                "source_hash": file_hash,
                "meta_hash": meta_hash,
                "version": version,
                "title": descriptor["title"],
                "description": descriptor["description"],
                "source_url": descriptor.get("source_url"),
                "source_path": source_path.resolve().as_posix(),
                "nodes": [
                    {
                        "id": node.id,
                        "anchor": node.anchor,
                        "kind": node.kind,
                        "label": node.label,
                        "text": node.text,
                        "parent": node.parent,
                        "path": node.path,
                        "depth": node.depth,
                        "order_idx": node.order_idx,
                        "number": node.number,
                        "article_id": node.article_id,
                    }
                    for node in converted
                ],
            }
            write_graph_doc(data_root, slug, payload)

        served = sync_pdf_into_data_dir(source_path, data_root, slug)
        html_rel = _relative_to_root(served, data_root)
        payload["html_rel"] = html_rel
        document = {
            "slug": slug,
            "version": version,
            "title": payload["title"],
            "description": payload["description"],
            "html_path": served,
            "html_rel": html_rel,
            "md_rel": None,
            "source_url": payload.get("source_url"),
            "numar_link": descriptor.get("code"),
            "date": version,
            "is_latest": True,
            "content_hash": file_hash,
            "source_type": descriptor["kind"],
        }
        documents.append(document)
        doc_nodes = _restore_nodes(payload, slug, version)
        nodes.extend(doc_nodes)
        if descriptor["kind"] == "declaratie":
            actions.append(
                {
                    "doc_slug": slug,
                    "code": descriptor["code"],
                    "title": descriptor["title"],
                    "description": descriptor["description"],
                    "source_url": descriptor.get("source_url"),
                    "pdf_rel": html_rel,
                    "xfa": int(descriptor["xfa"]),
                    "simple_pdf": descriptor.get("simple_pdf"),
                    "artifact": descriptor.get("artifact") or "pdf",
                }
            )
    log.info(
        f"PDF sources: {len(documents)} documente, {len(nodes)} noduri, "
        f"{len(actions)} declaratii."
    )
    return documents, nodes, actions


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def load_pdf_actions(
    data_root: str | Path,
    *,
    sources_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build ANAF action rows without running Docling.

    PDF text now reaches the index as ordinary HTML (see ``pdf_to_html``), so
    only the declaration *metadata* is still needed here. The link keeps
    pointing at the real form PDF under ``5-pdfs`` — that is what a taxpayer
    actually downloads and uploads in SPV.
    """
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
                "pdf_rel": _relative_to_root(served, root),
                "xfa": int(descriptor["xfa"]),
                "simple_pdf": descriptor.get("simple_pdf"),
                "artifact": descriptor.get("artifact") or "pdf",
            }
        )
    return actions


def export_docling_graph(
    root: Path, nodes: list[RawNode], documents: list[dict[str, Any]]
) -> dict[str, Path | None]:
    """Export the graph and build the official Docling-Graph visualization.

    Nodes/edges are written in the JSON layout consumed by
    ``docling_graph.core.visualizers.InteractiveVisualizer``
    (``display_cytoscape_graph(input_format='json')``), which produces the
    interactive Cytoscape HTML. No custom viewer is generated.
    """
    out_dir = step_dir(root, "processed") / "graph-docling"
    out_dir.mkdir(parents=True, exist_ok=True)
    slugs = {doc["slug"] for doc in documents}
    legal_nodes = [node for node in nodes if node.doc_slug in slugs]
    graph_nodes = [
        {
            "id": node.id,
            "label": node.label or node.kind,
            "type": node.kind,
            "doc": node.doc_slug,
            "text": (node.text or "")[:400],
            "anchor": node.anchor,
        }
        for node in legal_nodes
    ]
    edges = [
        {"source": node.parent, "target": node.id, "type": "CONTAINS"}
        for node in legal_nodes
        if node.parent
    ]
    for node in legal_nodes:
        for citation in node.citations:
            if citation.get("dst"):
                edges.append(
                    {
                        "source": node.id,
                        "target": citation["dst"],
                        "type": "CITEAZA",
                        "raw": citation.get("raw"),
                    }
                )

    node_types: dict[str, int] = {}
    for item in graph_nodes:
        node_types[item["type"]] = node_types.get(item["type"], 0) + 1
    edge_types: dict[str, int] = {}
    for edge in edges:
        edge_types[edge["type"]] = edge_types.get(edge["type"], 0) + 1
    stats = {
        "node_count": len(graph_nodes),
        "edge_count": len(edges),
        "node_types": node_types,
        "edge_types": edge_types,
        "documents": len(slugs),
    }

    nodes_path = out_dir / "nodes.json"
    edges_path = out_dir / "edges.json"
    stats_path = out_dir / "graph_stats.json"
    nodes_path.write_text(
        json.dumps(graph_nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    edges_path.write_text(
        json.dumps(edges, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    combined = out_dir / "graph_data.json"
    combined.write_text(
        json.dumps({"nodes": graph_nodes, "edges": edges}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    html_path = _build_official_visualization(combined, out_dir / "visualization.html")
    return {
        "nodes": nodes_path,
        "edges": edges_path,
        "stats": stats_path,
        "graph_data": combined,
        "visualization": html_path,
    }


def _build_official_visualization(
    graph_data: Path, output: Path
) -> Path | None:
    """Build the interactive Cytoscape HTML with Docling-Graph.

    ``docling-graph`` is a declared dependency of the ``ocr`` extra (installed
    by the indexer). If it is genuinely absent, the index still builds — only
    the visualization is skipped, with a clear warning.
    """
    try:
        from docling_graph.core.visualizers import InteractiveVisualizer
    except ImportError as ex:
        log.warning(
            "docling-graph nu este instalat; vizualizarea grafului nu a fost "
            f"generata. Instaleaza extra-ul 'ocr'. ({ex})"
        )
        return None
    try:
        result = InteractiveVisualizer().display_cytoscape_graph(
            path=graph_data,
            input_format="json",
            output_path=output,
            open_browser=False,
        )
        return Path(result) if result else (output if output.exists() else None)
    except Exception as ex:  # pragma: no cover - depends on the optional package
        log.warning(f"Vizualizatorul Docling-Graph nu a putut genera HTML: {ex}")
        return None
