"""Minimal stdio MCP server for the legal index (standard library only).

OpenCode starts this process and speaks JSON-RPC 2.0 over stdin/stdout. The
server exposes tools that mirror the CLI: search, article, show, neighbors,
context, docs, an optional Cypher query over ``graph.db`` and ``save_answer``,
which writes the final answer to ``raspunsuri-ai/<question-slug>.md``.

All tools except ``save_answer`` are read-only.

No third-party MCP SDK is required, so the server works in a plain virtualenv.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from .query import LegalIndex

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "legislatie"
SERVER_VERSION = "0.1.0"

_INDEX: LegalIndex | None = None


def _index() -> LegalIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = LegalIndex()
    return _INDEX


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": (
            "Cauta in indexul legislativ (BM25) si intoarce articole cu link "
            "file:// catre ancora exacta din HTML. Foloseste 2-4 variante de "
            "cuvinte-cheie."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Cuvinte-cheie."},
                "doc": {
                    "type": "string",
                    "description": "Limiteaza la un act (slug), ex. codul-fiscal.",
                },
                "kind": {
                    "type": "string",
                    "description": "Tip de nod: articol, alineat, litera, paragraf, nota.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "all_versions": {"type": "boolean"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "article",
        "description": (
            "Intoarce un articol intreg (alineate, litere, note) cu link catre "
            "ancora lui din HTML. Pentru acte fara articole (CAEN) intoarce "
            "codul si descrierea din jur."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc": {"type": "string", "description": "Slug-ul actului."},
                "number": {"type": "string", "description": "Numarul/codul."},
                "version": {"type": "string", "description": "Data consolidarii."},
            },
            "required": ["doc", "number"],
        },
    },
    {
        "name": "show",
        "description": (
            "Arata un nod dupa id exact, cu parintii si trimiterile lui."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"node_id": {"type": "string"}},
            "required": ["node_id"],
        },
    },
    {
        "name": "neighbors",
        "description": (
            "Trimiterile care pleaca din nod si cele care intra in nod. "
            "subtree=true include tot articolul."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "subtree": {"type": "boolean"},
            },
            "required": ["node_id"],
        },
    },
    {
        "name": "context",
        "description": (
            "Pachet de evidente pentru o intrebare: articole intregi, linii "
            "SURSA: cu link si trimiteri. Cel mai bun punct de plecare."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                "doc": {
                    "type": "string",
                    "description": "Limiteaza la un act (slug), ex. codul-fiscal.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "context_files",
        "description": (
            "Lista PDF-urile si imaginile puse de utilizator in folderul "
            "date-context, cu marime, data si link file://. Foloseste-l inainte "
            "de read_context cand nu stii numele fisierului."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "context_dir": {
                    "type": "string",
                    "description": (
                        "Folder alternativ. Implicit: ./date-context sau "
                        "$SCRAPPER_CONTEXT_DIR."
                    ),
                },
            },
        },
    },
    {
        "name": "read_context",
        "description": (
            "Citeste un PDF, o imagine sau un screenshot pus de utilizator in "
            "date-context (sau orice cale primita prin drag-and-drop) si il "
            "converteste in Markdown cu Docling OCR (romana/engleza). "
            "Rezultatul este cache-uit; a doua citire este instant. Intoarce "
            "textul extras si linkul file:// catre fisierul original. Daca "
            "lipseste 'path', listeaza fisierele din date-context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Cale absoluta/relativa, nume de fisier in date-context "
                        "sau URI file://."
                    ),
                },
                "pages": {
                    "type": "string",
                    "description": "Pentru PDF: '3' sau '1-5'. Implicit: toate.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000000,
                    "description": "Limita de caractere returnate. Implicit 120000.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Reface conversia, ignorand cache-ul.",
                },
                "ocr": {
                    "type": "boolean",
                    "description": "Ruleaza OCR. Implicit true.",
                },
                "context_dir": {
                    "type": "string",
                    "description": (
                        "Folder alternativ. Implicit: ./date-context sau "
                        "$SCRAPPER_CONTEXT_DIR."
                    ),
                },
            },
        },
    },
    {
        "name": "save_answer",
        "description": (
            "Salveaza raspunsul final in Markdown in folderul raspunsuri-ai, "
            "cu numele fisierului derivat din intrebare (slug ASCII, fara "
            "diacritice). Trimite intrebarea si raspunsul complet in formatul "
            "din answer-format.md. Intoarce calea fisierului si un link file://."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Intrebarea utilizatorului, nemodificata.",
                },
                "answer": {
                    "type": "string",
                    "description": (
                        "Raspunsul complet in Markdown, cu sectiunile "
                        "INTREBARE, TL;DR, TEMEI LEGAL, Exceptii / atentionari "
                        "si Sursa."
                    ),
                },
                "output_dir": {
                    "type": "string",
                    "description": (
                        "Folder alternativ. Implicit: ./raspunsuri-ai sau "
                        "$SCRAPPER_ANSWERS_DIR."
                    ),
                },
            },
            "required": ["question", "answer"],
        },
    },
    {
        "name": "docs",
        "description": "Lista actelor indexate, cu data consolidarii.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cypher",
        "description": (
            "Interogare Cypher peste graful de citari (4-processed/graph.db): "
            "noduri Articol/Alineat/Litera/Nota etc. si muchii CITEAZA / "
            "MENTIONEAZA, plus variantele agregate *_ARTICOL cu proprietatea "
            "count. Nu cauta in textul legii; pentru text foloseste search "
            "sau context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


def _format_article(data: dict[str, Any]) -> str:
    lines = [
        f"# {data['doc_title']} — {data['label']}",
        f"SURSA: {data['link']}",
    ]
    if data.get("date"):
        lines.append(f"Versiune: {data['date']}")
    lines.extend(["", data["text"]])
    if data.get("ancestors"):
        lines.extend(["", "**Parinti:**"])
        for item in data["ancestors"]:
            lines.append(f"- {item['label']} — {item['link']}")
    return "\n".join(lines)


def _format_neighbors(data: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    for direction, title in (
        ("outgoing", "Trimiteri din nod"),
        ("incoming", "Citat de"),
    ):
        lines.append(f"## {title}")
        if not data[direction]:
            lines.append("(niciuna)")
        for ref in data[direction]:
            target = ref["label"] or ref["raw"] or ref["target"]
            link = ref["link"] or ref["raw"] or ""
            lines.append(f"- [{ref['rel']}] {target} — {link}")
        lines.append("")
    return "\n".join(lines)


def _format_docs(rows: list[dict[str, Any]]) -> str:
    lines = ["| act | versiune | consolidare | titlu |", "|---|---|---|---|"]
    for item in rows:
        mark = "latest" if item["is_latest"] else "older"
        lines.append(
            f"| {item['slug']} | {item['version']} | {mark} | "
            f"{item['title'][:80]} |"
        )
    return "\n".join(lines)


def _call_tool(name: str, arguments: dict[str, Any]) -> str:
    # Writing an answer does not need the legal index; keep it usable even
    # before ``process`` has built law.db.
    if name == "save_answer":
        from .answers import save_answer

        saved = save_answer(
            arguments["question"],
            arguments["answer"],
            output_dir=arguments.get("output_dir"),
        )
        return (
            f"Raspuns salvat: {saved['path']}\n"
            f"Slug: {saved['slug']}\n"
            f"Link: {saved['link']}"
        )

    if name in {"context_files", "read_context"}:
        from .context_files import (
            MAX_CHARS_DEFAULT,
            format_context_list,
            format_context_result,
            list_context_files,
            read_context,
        )
        from .config import resolve_context_dir

        root = resolve_context_dir(arguments.get("context_dir"))
        if name == "context_files" or not arguments.get("path"):
            return format_context_list(list_context_files(root), root)

        result = read_context(
            arguments["path"],
            directory=root,
            pages=arguments.get("pages"),
            max_chars=int(arguments.get("max_chars", MAX_CHARS_DEFAULT)),
            force=bool(arguments.get("force", False)),
            ocr=bool(arguments.get("ocr", True)),
        )
        return format_context_result(result)

    if name == "cypher":
        from .config import resolve_data_dir, step_dir

        from .graph import run_cypher

        root = resolve_data_dir()
        rows = run_cypher(step_dir(root, "processed") / "graph.db", arguments["query"])
        return json.dumps(rows, ensure_ascii=False, indent=2)

    index = _index()

    if name == "search":
        results = index.search(
            arguments["query"],
            doc=arguments.get("doc"),
            kind=arguments.get("kind"),
            limit=int(arguments.get("limit", 8)),
            latest_only=not bool(arguments.get("all_versions", False)),
        )
        return index.format_search(results)

    if name == "article":
        return _format_article(
            index.article(
                arguments["doc"],
                arguments["number"],
                version=arguments.get("version"),
            )
        )

    if name == "show":
        return _format_article(index.show(arguments["node_id"]))

    if name == "neighbors":
        return _format_neighbors(
            index.neighbors(
                arguments["node_id"],
                subtree=bool(arguments.get("subtree", False)),
            )
        )

    if name == "context":
        return index.context(
            arguments["question"],
            limit=int(arguments.get("limit", 4)),
            doc=arguments.get("doc"),
        )

    if name == "docs":
        return _format_docs(index.docs())

    raise ValueError(f"Unknown tool: {name}")


def _response(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        return _response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method in {"notifications/initialized", "initialized"}:
        return None

    if method == "ping":
        return _response(request_id, {})

    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            text = _call_tool(name, arguments)
        except Exception as ex:  # noqa: BLE001 - surfaced to the client
            return _response(
                request_id,
                {
                    "content": [{"type": "text", "text": f"Eroare: {ex}"}],
                    "isError": True,
                },
            )
        return _response(
            request_id,
            {"content": [{"type": "text", "text": text}], "isError": False},
        )

    if method in {"resources/list", "prompts/list"}:
        key = "resources" if method == "resources/list" else "prompts"
        return _response(request_id, {key: []})

    if request_id is None:
        return None
    return _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
