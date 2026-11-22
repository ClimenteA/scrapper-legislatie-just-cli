"""Rendering helpers for the HTTP MCP tool layer.

These functions produce the Markdown shapes returned for articles, neighbor
lists and the act listing, so formatting rules stay in one place.
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
    """Catalogue grouped by source family, with the act description.

    The description is what makes source filtering possible before any text
    search: the agent reads it to decide which acts (and families) a question
    needs. Older consolidations are counted, not listed; ``all_versions``
    brings them back.
    """
    from .research import FAMILY_LABELS, classify_family

    latest = [item for item in rows if item["is_latest"]]
    older_count = len(rows) - len(latest)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in latest:
        family = item.get("family") or classify_family(
            item["slug"], item.get("title", "")
        )
        grouped.setdefault(family, []).append(item)

    lines = [f"# Catalog de surse ({len(latest)} acte, ultima consolidare)"]
    for family, label in FAMILY_LABELS.items():
        items = grouped.get(family)
        if not items:
            continue
        lines.append("")
        lines.append(f"## {label} ({len(items)})")
        for item in sorted(items, key=lambda row: row["slug"]):
            lines.append(f"- `{item['slug']}` — {item['title'][:100]}")
            description = (item.get("description") or "").strip()
            if description:
                lines.append(f"  {description[:400]}")
            lines.append(f"  consolidare: {item['date']}")
    if older_count:
        lines.append("")
        lines.append(
            f"(+{older_count} consolidari mai vechi; foloseste "
            "docs(all_versions=True) pentru ele.)"
        )
    return "\n".join(lines)


def format_selection(plan: dict[str, Any]) -> str:
    """Render the shortlist produced by ``LegalIndex.select_docs``."""
    labels = plan["family_labels"]
    lines = [
        f"# Selectie de surse ({len(plan['docs'])}/{plan['total_docs']} acte)",
        "",
        f"Intrebare: {plan['question']}",
    ]
    if plan["topics"]:
        lines.append(
            "Teme: " + ", ".join(plan["topics"])
            + " · familii obligatorii: "
            + ", ".join(labels.get(f, f) for f in plan["required_families"])
        )
    lines.append("")
    for doc in plan["docs"]:
        family = labels.get(doc["family"], doc["family"])
        lines.append(f"- [{family}] `{doc['slug']}` — {doc['title'][:100]}")
        description = (doc.get("description") or "").strip()
        if description:
            lines.append(f"  {description[:300]}")
    if plan["missing_families"]:
        lines.append("")
        lines.append(
            "ATENTIE: familii obligatorii fara act in selectie: "
            + ", ".join(labels.get(f, f) for f in plan["missing_families"])
            + ". Cauta manual in catalog (docs) inainte de a raspunde."
        )
    lines.append("")
    lines.append(
        "Urmatorul pas: research(question) pentru dovezi din fiecare act, "
        "apoi article/show/neighbors pentru textul integral si exceptii."
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
