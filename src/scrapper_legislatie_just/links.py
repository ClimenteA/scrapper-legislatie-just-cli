"""Link builders shared by the local and hosted modes.

Local mode (CLI / stdio MCP) keeps the original ``file://.../3-expanded/...``
links so they open in the downloaded HTML on the user's machine. Hosted mode
(FastAPI MCP) builds equivalent ``https://<host>/laws/3-expanded/...`` links,
which the FastAPI app serves from the read-only ``3-expanded`` folder.

Keeping the helpers here means the two modes share one mapping rule and the
local behaviour stays byte-for-byte compatible when ``base_url`` is None.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

EXPANDED_MARKER = "/3-expanded/"
PDF_MARKER = "/5-pdfs/"


def _pdf_fragment(rel: str, anchor: str | None) -> str | None:
    """PDF nodes use ``p<page>_g<n>`` anchors; open them at that page."""
    if not anchor or not rel.lower().endswith(".pdf"):
        return anchor
    match = re.match(r"p(\d+)_g\d+", anchor)
    return f"page={match.group(1)}" if match else anchor


def http_link(html_rel: str, anchor: str | None, base_url: str) -> str:
    """Build a public HTTP link from a data-relative source path.

    ``html_rel`` normally starts with ``3-expanded/`` (HTML acts) but PDF
    sources live under ``5-pdfs/`` and are served at ``/pdfs/*``. ``base_url``
    ends in the path mapped to ``3-expanded`` (e.g. ``https://host/laws``).
    """
    rel = html_rel.replace("\\", "/").lstrip("/")
    if rel.startswith("5-pdfs/"):
        root = base_url.rstrip("/")
        if root.endswith("/laws"):
            root = root[: -len("/laws")]
        url = f"{root}/pdfs/{rel[len('5-pdfs/'):]}"
    else:
        url = f"{base_url.rstrip('/')}/{rel}"
    fragment = _pdf_fragment(rel, anchor)
    return f"{url}#{fragment}" if fragment else url


def file_link(root: str | Path, html_rel: str, anchor: str | None) -> str:
    """Original local link: ``file://...#anchor`` (PDFs use ``#page=N``)."""
    uri = (Path(root) / html_rel).resolve().as_uri()
    fragment = _pdf_fragment(html_rel, anchor)
    return f"{uri}#{fragment}" if fragment else uri


def make_link(
    root: str | Path,
    html_rel: str,
    anchor: str | None,
    base_url: str | None = None,
) -> str:
    """Return an HTTP link when ``base_url`` is set, otherwise a file link."""
    if base_url:
        return http_link(html_rel, anchor, base_url)
    return file_link(root, html_rel, anchor)


def to_public_link(value: Any, base_url: str) -> Any:
    """Convert a stored ``file://`` link into a public HTTP link.

    Used at query time for ``graph.db`` rows, whose links were baked in when
    the graph was built locally. Non-link values pass through untouched.
    """
    if not isinstance(value, str) or not value.startswith("file://"):
        return value
    parsed = urlparse(value)
    path = unquote(parsed.path)
    idx = path.find(EXPANDED_MARKER)
    if idx < 0:
        idx = path.find(PDF_MARKER)
        if idx >= 0:
            rel = path[idx + 1 :]
            # PDF file links already carry #page=N; do not rewrite it.
            fragment = parsed.fragment or None
            if fragment and fragment.startswith("page="):
                return http_link(rel, None, base_url) + f"#{fragment}"
            return http_link(rel, fragment, base_url)
    rel = path[idx + 1 :] if idx >= 0 else path.lstrip("/")
    return http_link(rel, parsed.fragment or None, base_url)


def publicize_rows(rows: Any, base_url: str) -> Any:
    """Recursively rewrite every ``file://`` string in Cypher result rows."""

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return to_public_link(value, base_url)
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(item) for item in value]
        return value

    return walk(rows)
