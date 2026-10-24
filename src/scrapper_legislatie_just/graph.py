"""Citation graph backed by GraphQLite.

GraphQLite is an embedded SQLite extension that adds Cypher and graph
algorithms. The legal index uses it as the **only** graph store:

- ``law.db`` keeps documents, nodes and the FTS5 search index;
- ``graph.db`` keeps the citation graph (nodes + edges) and is queried with
  Cypher.

The graph is rebuilt by ``process`` together with ``law.db``, so there is no
second SQL implementation to keep in sync.

Install (already a project dependency)::

    uv pip install graphqlite
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .logger import log

LABELS = {
    "document": "Act",
    "titlu": "Titlu",
    "capitol": "Capitol",
    "sectiune": "Sectiune",
    "articol": "Articol",
    "alineat": "Alineat",
    "punct": "Punct",
    "litera": "Litera",
    "paragraf": "Paragraf",
    "linie": "Linie",
    "nota": "Nota",
    "anexa": "Anexa",
}

REL_TYPES = {
    "citat": "CITEAZA",
    "mentioneaza": "MENTIONEAZA",
}


def _import_graphqlite():
    try:
        import graphqlite  # type: ignore
    except ImportError as ex:  # pragma: no cover - depends on optional package
        raise RuntimeError(
            "GraphQLite is not installed. Install it with "
            "'uv pip install graphqlite' or 'pip install graphqlite'."
        ) from ex
    return graphqlite


def _link(root: Path, html_rel: str, anchor: str | None) -> str:
    path = Path(html_rel)
    if not path.is_absolute():
        path = root / path
    uri = path.resolve().as_uri()
    return f"{uri}#{anchor}" if anchor else uri


def _article_edges(
    refs: list[dict[str, Any]],
    article_of: dict[str, str],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Aggregate paragraph-level citations to article-level edges.

    Most citations start in a letter or paragraph, so the raw graph has almost
    no ``Articol -> Articol`` edges. Aggregated edges let Cypher answer the
    question lawyers actually ask ("which articles cite art. 60?").
    """
    aggregated: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref in refs:
        src, dst = ref.get("src"), ref.get("dst")
        if not src or not dst:
            continue
        src_article = article_of.get(src)
        dst_article = article_of.get(dst)
        if not src_article or not dst_article or src_article == dst_article:
            continue
        rel = ref.get("rel") or "citat"
        key = (src_article, dst_article, rel)
        entry = aggregated.setdefault(key, {"count": 0, "raw": ref.get("raw") or ""})
        entry["count"] += 1
    return aggregated


def build_graph(
    nodes: list[dict[str, Any]],
    refs: list[dict[str, Any]],
    root: str | Path,
    graph_path: str | Path,
    force: bool = False,
) -> dict[str, Any]:
    """Write the citation graph to ``graph.db`` and return a small summary.

    ``nodes`` and ``refs`` are plain dictionaries produced by the index
    builder, so this function does not need to read ``law.db`` and the graph
    remains a single derived artifact.
    """
    graphqlite = _import_graphqlite()
    root = Path(root).expanduser().resolve()
    graph_path = Path(graph_path).expanduser().resolve()

    if graph_path.exists() and not force:
        graph = graphqlite.Graph(str(graph_path))
        try:
            stats = graph.stats()
            return {
                "nodes": stats.get("node_count", 0),
                "edges": stats.get("edge_count", 0),
                "path": str(graph_path),
                "skipped": True,
            }
        finally:
            graph.close()

    graph_path.parent.mkdir(parents=True, exist_ok=True)
    if graph_path.exists():
        graph_path.unlink()

    graph = graphqlite.Graph(str(graph_path))
    try:
        bulk_nodes: list[tuple[str, dict[str, Any], str]] = []
        article_of: dict[str, str] = {}
        for node in nodes:
            article_id = node.get("article_id")
            if article_id:
                article_of[node["id"]] = article_id
            props = {
                "id": node["id"],
                "label": node.get("label") or "",
                "kind": node.get("kind") or "",
                "doc": node.get("doc_slug") or "",
                "doc_title": node.get("title") or "",
                "version": node.get("doc_version") or "",
                "anchor": node.get("anchor") or "",
                "number": node.get("number") or "",
                "article_id": article_id or "",
                "html": node.get("html_rel") or "",
                "link": _link(
                    root, node.get("html_rel") or "", node.get("anchor")
                ),
            }
            bulk_nodes.append(
                (node["id"], props, LABELS.get(node.get("kind"), "Nod"))
            )

        node_ids = graph.insert_nodes_bulk(bulk_nodes)

        bulk_edges: list[tuple[str, str, dict[str, Any], str]] = []
        for ref in refs:
            src, dst = ref.get("src"), ref.get("dst")
            if not src or not dst:
                continue
            if src not in node_ids or dst not in node_ids:
                continue
            rel = ref.get("rel") or "citat"
            bulk_edges.append(
                (
                    src,
                    dst,
                    {
                        "rel": rel,
                        "raw": ref.get("raw") or "",
                        "confidence": float(ref.get("confidence") or 0),
                        "aggregate": 0,
                    },
                    REL_TYPES.get(rel, "CITEAZA"),
                )
            )

        for (src_article, dst_article, rel), entry in _article_edges(
            refs, article_of
        ).items():
            bulk_edges.append(
                (
                    src_article,
                    dst_article,
                    {
                        "rel": rel,
                        "raw": entry["raw"],
                        "count": entry["count"],
                        "aggregate": 1,
                    },
                    f"{REL_TYPES.get(rel, 'CITEAZA')}_ARTICOL",
                )
            )

        if bulk_edges:
            graph.insert_edges_bulk(bulk_edges, node_ids)

        stats = graph.stats()
        log.debug(f"Graph written to {graph_path}: {stats}")
        return {
            "nodes": stats.get("node_count", len(bulk_nodes)),
            "edges": stats.get("edge_count", len(bulk_edges)),
            "path": str(graph_path),
            "skipped": False,
        }
    finally:
        graph.close()


def run_cypher(graph_path: str | Path, query: str) -> list[dict[str, Any]]:
    graphqlite = _import_graphqlite()
    graph_path = Path(graph_path).expanduser().resolve()
    if not graph_path.exists():
        raise FileNotFoundError(
            f"Graph not found: {graph_path}. Run "
            "'scrapper-legislatie-just-cli process' first."
        )
    graph = graphqlite.Graph(str(graph_path))
    try:
        return graph.query(query)
    finally:
        graph.close()


def cypher_literal(value: str) -> str:
    """Quote a string for a Cypher literal (GraphQLite has no bind params)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def format_rows(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2)
