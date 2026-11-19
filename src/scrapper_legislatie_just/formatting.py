"""Rendering helpers shared by the local stdio MCP and the hosted FastAPI MCP.

Both transports return the same Markdown shapes for articles, neighbor lists
and the act listing. Keeping them here avoids duplicating formatting rules
between ``mcp_server.py`` (local) and the server package's ``mcp_http.py``.
"""

from __future__ import annotations

from typing import Any


def format_article(data: dict[str, Any]) -> str:
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


def format_neighbors(data: dict[str, list[dict[str, Any]]]) -> str:
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


def format_docs(rows: list[dict[str, Any]]) -> str:
    lines = ["| act | versiune | consolidare | titlu |", "|---|---|---|---|"]
    for item in rows:
        mark = "latest" if item["is_latest"] else "older"
        lines.append(
            f"| {item['slug']} | {item['version']} | {mark} | "
            f"{item['title'][:80]} |"
        )
    return "\n".join(lines)


def format_actions(rows: list[dict[str, Any]]) -> str:
    """Render ANAF declaration sheets with a link to the source PDF."""
    if not rows:
        return "Nicio declaratie gasita in corpus."
    lines = ["| cod | declaratie | sursa PDF |", "|---|---|---|"]
    for item in rows:
        lines.append(
            f"| {item['code']} | {item['title'][:120]} | {item['link']} |"
        )
    return "\n".join(lines)
