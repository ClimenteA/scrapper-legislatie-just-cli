"""Step 0: turn the ``0-pdfs`` corpus into normal ``2-clean`` HTML files.

Docling PDF conversion is by far the slowest part of a rebuild. This module
runs it **once per file version** and writes the result as a regular clean HTML
document. From that point on a PDF is just another HTML source: the expand step
copies it into ``3-expanded``, the processor parses it with the regular
``_parse_document`` path and no Docling call happens during a rebuild.

The two PDF families have different shapes, so they are rendered differently:

* ANAF declarations (``declaratii-pdf``) are mostly forms/tables. Docling's
  Markdown export is wrapped verbatim in one ``<pre class="S_PRE">`` — the same
  representation the cleaner produces for the ASCII tables embedded in
  legislatie.just.ro forms. The monospace block keeps columns aligned and makes
  the Markdown export emit a fenced code block instead of a fake table.
* EU acts (``eu-pdfs``) share the structure of the legislative HTML, so they
  keep the full semantic rendering (``S_*`` classes, headings and anchors).

A small cache manifest next to the generated files records the source hash, so
re-running the step skips PDFs that have not changed (``force=True`` overrides).
"""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

from . import pdf_ingest
from .config import step_dir
from .logger import log
from .sources import Sursa

PDF_DATE = "2000-01-01"
PDF_NUMBER_BASE = 9_000_000
CACHE_FILENAME = ".pdf-html-cache.json"
# Mark declaratiile rendered as Markdown-inside-<pre> so old semantic-HTML
# cache entries are regenerated once after this format change.
CACHE_FORMAT_PRE = "markdown-pre"
# EU acts are semantic HTML. Bump this when the node mapping changes so old
# label-only HTML (all items misclassified as headings, text lost) is rebuilt.
CACHE_FORMAT_EU = "semantic-text-v2"

# (tag, class, label tag, label class) for every node kind the PDF converter
# can emit. Structural kinds carry their text in a *_TTL label; content kinds
# keep it as direct text so processor._own_text() picks it up unchanged.
_NODE_TAGS: dict[str, tuple[str, str, str | None, str | None]] = {
    "document": ("div", "", None, None),
    "titlu": ("section", "S_TTL", "h1", "S_TTL_TTL"),
    "capitol": ("section", "S_CAP", "h2", "S_CAP_TTL"),
    "sectiune": ("section", "S_SEC", "h3", "S_SEC_TTL"),
    "articol": ("article", "S_ART", "h3", "S_ART_TTL"),
    "anexa": ("section", "S_ANX", "h3", "S_ANX_TTL"),
    "alineat": ("li", "S_ALN", None, None),
    "punct": ("li", "S_PCT", None, None),
    "litera": ("li", "S_LIT", None, None),
    "linie": ("div", "S_LIN", None, None),
    "paragraf": ("p", "S_PAR", None, None),
    "nota": ("div", "S_NTA", None, None),
    "preformatat": ("pre", "S_PRE", None, None),
}
_STRUCTURAL = {"document", "titlu", "capitol", "sectiune", "articol", "anexa"}
_LIST_KINDS = {"alineat", "punct", "litera"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _meta_hash(descriptor: dict[str, Any]) -> str:
    return hashlib.sha256(
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


def pdf_descriptors(
    data_root: str | Path, sources_dir: str | Path | None = None
) -> list[dict[str, Any]]:
    """Discover PDFs and enrich them with a stable source number/file name."""
    sources = pdf_ingest.resolve_pdf_sources_dir(sources_dir, data_root=data_root)
    descriptors = pdf_ingest.load_declaration_manifest(sources)
    descriptors += pdf_ingest.discover_eu_pdfs(sources)

    used: set[int] = set()
    for descriptor in sorted(descriptors, key=lambda item: item["slug"]):
        seed = int(hashlib.sha1(descriptor["slug"].encode("utf-8")).hexdigest()[:6], 16)
        number = PDF_NUMBER_BASE + seed % 1_000_000
        while number in used:
            number += 1
        used.add(number)
        descriptor["numar_link"] = number
        descriptor["date"] = PDF_DATE
        descriptor["html_name"] = f"{number}-{descriptor['slug']}-{PDF_DATE}.html"
    return descriptors


def pdf_sources(
    data_root: str | Path, sources_dir: str | Path | None = None
) -> list[Sursa]:
    """Synthetic source entries so PDF HTML travels through the HTML pipeline."""
    return [
        Sursa(
            nume=descriptor["title"],
            descriere=descriptor["description"] or descriptor["title"],
            numar_link=descriptor["numar_link"],
            slug=descriptor["slug"],
        )
        for descriptor in pdf_descriptors(data_root, sources_dir)
    ]


def _render_node(node: Any, children: dict[str, list[Any]]) -> str:
    tag, css_class, label_tag, label_class = _NODE_TAGS.get(
        node.kind, ("div", "", None, None)
    )
    attrs = f' class="{css_class}"' if css_class else ""
    if node.anchor:
        attrs += f' id="{html.escape(node.anchor, quote=True)}"'

    inner = ""
    if node.kind in _STRUCTURAL:
        if label_tag and label_class and node.label:
            inner += (
                f"<{label_tag} class=\"{label_class}\">"
                f"{html.escape(node.label)}</{label_tag}>"
            )
    else:
        inner += html.escape(node.text or "")

    rendered_children = [_render_node(child, children) for child in children.get(node.id, [])]
    if any(child.kind in _LIST_KINDS for child in children.get(node.id, [])):
        # Group consecutive list items like the cleaner does, for valid HTML.
        chunks: list[str] = []
        open_list = False
        for child, rendered in zip(children.get(node.id, []), rendered_children):
            is_item = child.kind in _LIST_KINDS
            if is_item and not open_list:
                chunks.append('<ul style="list-style-type: none;">')
                open_list = True
            elif not is_item and open_list:
                chunks.append("</ul>")
                open_list = False
            chunks.append(rendered)
        if open_list:
            chunks.append("</ul>")
        inner += "".join(chunks)
    else:
        inner += "".join(rendered_children)

    return f"<{tag}{attrs}>{inner}</{tag}>"


def _render_clean_shell(descriptor: dict[str, Any], body: str) -> str:
    """Wrap rendered content in the standard 2-clean HTML shell."""
    from .cleaner import HEAD_LINKS, STYLES_CSS

    title = html.escape(descriptor["title"])
    description = html.escape(descriptor.get("description") or "")
    source_url = descriptor.get("source_url")
    cover = (
        '<header class="S_BOOK_COVER">'
        f'<h1 class="S_BOOK_TITLE">{title}</h1>'
        f'<p class="S_BOOK_DESC">{description}</p>'
    )
    if source_url:
        cover += (
            f'<a class="S_BOOK_LINK" href="{html.escape(source_url, quote=True)}" '
            'target="_blank">Sursa PDF</a>'
        )
    cover += "</header>"

    return (
        '<!doctype html>\n<html lang="ro"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title>{HEAD_LINKS}<style>{STYLES_CSS}</style>"
        f"</head><body>{cover}{body}</body></html>\n"
    )


def render_pdf_html(descriptor: dict[str, Any], nodes: list[Any]) -> str:
    """Render converter nodes as a clean, semantic HTML document (EU acts)."""
    children: dict[str, list[Any]] = {}
    roots: list[Any] = []
    for node in nodes:
        if node.parent:
            children.setdefault(node.parent, []).append(node)
        else:
            roots.append(node)

    body = "".join(_render_node(node, children) for node in roots)
    return _render_clean_shell(descriptor, body)


def render_pdf_markdown_html(descriptor: dict[str, Any], markdown: str) -> str:
    """Render a form PDF as Docling Markdown inside a single ``S_PRE`` block.

    Mirrors how the cleaner stores the ASCII tables of legislatie.just.ro
    forms: one ``<pre class="S_PRE">`` per document, with an anchor so the
    processor indexes the whole text as one ``preformatat`` node.
    """
    text = (
        "\n".join(
            line.rstrip()
            for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ).strip("\n")
    )
    if not text.strip():
        text = descriptor.get("description") or descriptor["title"]
    body = f'<pre class="S_PRE" id="s_pre_1">{html.escape(text, quote=False)}</pre>'
    return _render_clean_shell(descriptor, body)


def _manifest_path(clean_dir: Path) -> Path:
    return clean_dir / CACHE_FILENAME


def _load_manifest(clean_dir: Path) -> dict[str, dict[str, str]]:
    path = _manifest_path(clean_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        log.warning(f"Nu pot citi cache-ul PDF->HTML {path}: {ex}")
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(clean_dir: Path, manifest: dict[str, dict[str, str]]) -> None:
    path = _manifest_path(clean_dir)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def convert_pdfs_to_clean_html(
    data_root: str | Path,
    *,
    sources_dir: str | Path | None = None,
    force: bool = False,
    ocr: bool = False,
) -> dict[str, Any]:
    """Convert every changed PDF once and write one HTML file per PDF in 2-clean.

    Returns a summary that also carries the synthetic sources, so the caller
    can hand them to the expand/process steps without re-discovering the corpus.
    """
    root = Path(data_root).expanduser().resolve()
    clean_dir = step_dir(root, "clean")
    clean_dir.mkdir(parents=True, exist_ok=True)

    descriptors = pdf_descriptors(root, sources_dir)
    manifest = _load_manifest(clean_dir)
    converted = skipped = removed = 0

    for descriptor in descriptors:
        slug: str = descriptor["slug"]
        file_hash = _sha256(descriptor["source_path"])
        meta_hash = _meta_hash(descriptor)
        target = clean_dir / descriptor["html_name"]
        cached = manifest.get(slug, {})
        # Declaratiile are Markdown in <pre>; EU acts keep semantic HTML. A
        # format change must invalidate an otherwise fresh cache entry.
        is_form = descriptor["kind"] == "declaratie"
        expected_format = CACHE_FORMAT_PRE if is_form else CACHE_FORMAT_EU
        fresh = (
            cached.get("file_hash") == file_hash
            and cached.get("meta_hash") == meta_hash
            and cached.get("html_name") == descriptor["html_name"]
            and cached.get("format") == expected_format
            and target.exists()
        )
        if fresh and not force:
            log.debug(f"[0-pdfs] Deja convertit: {slug}; sar peste.")
            skipped += 1
        else:
            try:
                docling_doc = pdf_ingest._docling_document(
                    descriptor["source_path"], ocr=ocr
                )
            except Exception as ex:  # a broken PDF must not kill the reindex
                log.warning(f"[0-pdfs] Nu pot converti {descriptor['source_path']}: {ex}")
                continue
            if is_form:
                markdown = docling_doc.export_to_markdown()
                target.write_text(
                    render_pdf_markdown_html(descriptor, markdown), encoding="utf-8"
                )
            else:
                nodes = pdf_ingest.convert_pdf_to_graph_nodes(
                    docling_doc,
                    slug=slug,
                    version=str(descriptor["version"]),
                    title=descriptor["title"],
                    html_rel="",
                )
                target.write_text(render_pdf_html(descriptor, nodes), encoding="utf-8")
            manifest[slug] = {
                "file_hash": file_hash,
                "meta_hash": meta_hash,
                "html_name": descriptor["html_name"],
                "source_path": descriptor["source_path"].resolve().as_posix(),
                "format": expected_format,
            }
            converted += 1
            log.info(f"[0-pdfs] HTML creat: {target.name}")

        # Keep the served PDF even when the text came from the cache, so the
        # actions tool and anyone downloading an ANAF form still find it.
        pdf_ingest.sync_pdf_into_data_dir(descriptor["source_path"], root, slug)

    current_slugs = {descriptor["slug"] for descriptor in descriptors}
    for slug in list(manifest):
        if slug in current_slugs:
            continue
        stale = manifest.pop(slug)
        (clean_dir / stale["html_name"]).unlink(missing_ok=True)
        removed += 1

    _write_manifest(clean_dir, manifest)
    log.info(
        f"PDF->HTML: {converted} convertite, {skipped} deja existente, "
        f"{removed} sterse."
    )
    return {
        "converted": converted,
        "skipped": skipped,
        "removed": removed,
        "sources": pdf_sources(root, sources_dir),
    }
