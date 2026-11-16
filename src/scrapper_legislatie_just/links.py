"""Link builders shared by the local and hosted modes.

Local mode (CLI / stdio MCP) keeps the original ``file://.../3-expanded/...``
links so they open in the downloaded HTML on the user's machine. Hosted mode
(FastAPI MCP) builds equivalent ``https://<host>/laws/3-expanded/...`` links,
which Caddy serves from the read-only ``3-expanded`` folder.

Keeping the helpers here means the two modes share one mapping rule and the
local behaviour stays byte-for-byte compatible when ``base_url`` is None.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

EXPANDED_MARKER = "/3-expanded/"


def http_link(html_rel: str, anchor: str | None, base_url: str) -> str:
    """Build a public HTTP link from a data-relative HTML path.

    ``html_rel`` is stored in law.db/graph.db relative to the data root and
    normally starts with ``3-expanded/``. ``base_url`` already ends in the
    path that maps to ``3-expanded`` (e.g. ``https://host/laws``).
    """
    rel = html_rel.replace("\\", "/").lstrip("/")
    url = f"{base_url.rstrip('/')}/{rel}"
    return f"{url}#{anchor}" if anchor else url


def file_link(root: str | Path, html_rel: str, anchor: str | None) -> str:
    """Original local link: ``file://...#anchor`` (unchanged behaviour)."""
    uri = (Path(root) / html_rel).resolve().as_uri()
    return f"{uri}#{anchor}" if anchor else uri


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
