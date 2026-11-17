"""Hosted MCP tool layer (FastAPI / Streamable HTTP).

Only the read-only, stateless tools are exposed here: the legal index queries,
the citation graph and BNR currency conversion. Personal-file tools (PDF OCR,
fiscal context, saved answers) stay in the local stdio server and write only to
the user's own machine.

Each tool opens its own read-only SQLite connection, so it is safe with
multiple uvicorn workers and with the index being atomically republished by the
monthly reindex job.
"""

from __future__ import annotations

import json
from functools import wraps
from typing import Any, Callable

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import resolve_data_dir, step_dir
from .graph import run_cypher
from .logger import log
from .mcp_server import _format_article, _format_docs, _format_neighbors
from .misc import strip_diacritics
from .query import LegalIndex, NotFoundError
from .settings import get_settings

SERVER_NAME = "legislatie"


def public_base_url() -> str:
    """Base URL that maps to ``3-expanded`` on the public web server."""
    return get_settings().laws_base_url


def _index() -> LegalIndex:
    return LegalIndex(public_base_url=public_base_url())


def _anticipated_failure(fn: Callable[..., str]) -> Callable[..., str]:
    """Report index lookup misses as anticipated MCP tool failures.

    Clients occasionally ask for an article/node that does not exist (e.g. a
    hallucinated article number). A bare exception makes the SDK log a full
    ERROR traceback and hide the message from the model. ``ToolError`` is the
    SDK's deliberate-failure channel: the model gets our message with
    ``is_error=True`` and the SDK logs one quiet INFO line. We also emit a
    concise WARNING so operators still see the failed lookup without a
    traceback.
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return fn(*args, **kwargs)
        except NotFoundError as ex:
            message = str(ex)
            log.warning("MCP tool '{}': {}", fn.__name__, message)
            raise ToolError(message) from None

    return wrapper


mcp = MCPServer(
    SERVER_NAME,
    instructions=(
        "Index legislativ romanesc. Cauta mai intai cu search/context, "
        "citeste articolul cu article, verifica exceptiile cu neighbors/show "
        "si citeaza linkurile HTTPS returnate."
    ),
)


@mcp.tool()
def search(
    query: str,
    doc: str | None = None,
    kind: str | None = None,
    limit: int = 8,
    all_versions: bool = False,
) -> str:
    """Cauta in indexul legislativ (BM25) si intoarce articole cu link HTTPS catre ancora exacta. Foloseste 2-4 variante de cuvinte-cheie."""
    with _index() as index:
        results = index.search(
            strip_diacritics(query),
            doc=doc,
            kind=kind,
            limit=max(1, min(limit, 50)),
            latest_only=not all_versions,
        )
        return strip_diacritics(index.format_search(results))


@mcp.tool()
@_anticipated_failure
def article(doc: str, number: str, version: str | None = None) -> str:
    """Intoarce un articol intreg (alineate, litere, note) cu link catre ancora lui. Pentru acte fara articole intoarce codul si descrierea din jur."""
    with _index() as index:
        data = index.article(doc, number, version=version)
        return strip_diacritics(_format_article(data))


@mcp.tool()
@_anticipated_failure
def show(node_id: str) -> str:
    """Arata un nod dupa id exact, cu parintii si trimiterile lui."""
    with _index() as index:
        return strip_diacritics(_format_article(index.show(node_id)))


@mcp.tool()
@_anticipated_failure
def neighbors(node_id: str, subtree: bool = False) -> str:
    """Trimiterile care pleaca din nod si cele care intra in nod. subtree=True include tot articolul."""
    with _index() as index:
        data = index.neighbors(node_id, subtree=subtree)
        return strip_diacritics(_format_neighbors(data))


@mcp.tool()
def context(question: str, limit: int = 4, doc: str | None = None) -> str:
    """Pachet de evidente pentru o intrebare: articole intregi, linii SURSA: cu link si trimiteri. Cel mai bun punct de plecare."""
    with _index() as index:
        return index.context(
            strip_diacritics(question),
            limit=max(1, min(limit, 12)),
            doc=doc,
        )


@mcp.tool()
def docs() -> str:
    """Lista actelor indexate, cu data consolidarii."""
    with _index() as index:
        return strip_diacritics(_format_docs(index.docs()))


@mcp.tool()
def cypher(query: str) -> str:
    """Interogare Cypher peste graful de citari (graph.db). Nu cauta in textul legii; pentru text foloseste search sau context."""
    root = resolve_data_dir()
    rows = run_cypher(
        step_dir(root, "processed") / "graph.db",
        query,
        base_url=public_base_url(),
    )
    return json.dumps(rows, ensure_ascii=False, indent=2)


@mcp.tool()
def convert_currency(amount: float, currency: str, date: str | None = None) -> str:
    """Converteste o suma dintr-o valuta straina in RON cu cursul de referinta BNR. Intoarce suma in RON, cursul aplicat si data exacta a cursului."""
    from .currency import convert_to_ron, format_conversion

    result = convert_to_ron(amount, currency, date)
    return format_conversion(result)
