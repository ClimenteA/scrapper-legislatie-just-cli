"""Read-only query layer over ``4-processed/law.db``.

Every result carries a ``file://.../document.html#anchor`` link that opens the
original downloaded HTML at the exact article/paragraph. No embeddings: FTS5
(BM25) for lookup, the reference graph for context expansion.
"""

from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from unidecode import unidecode

from .config import resolve_data_dir, step_dir

SPACE_RE = re.compile(r"\s+")

# Very small Romanian stop-word list: they would match almost every node and
# only add noise to BM25 / coverage scores.
STOPWORDS = {
    "a", "ai", "al", "ale", "alt", "alta", "as", "asa", "ca", "care", "ce",
    "cu", "cum", "da", "de", "din", "e", "este", "eu", "fara", "fi", "fie",
    "in", "intre", "la", "le", "lui", "mai", "mea", "meu", "nu", "o", "pe",
    "pentru", "prin", "sa", "sau", "se", "si", "sint", "sunt", "un", "una",
    "unei", "unui", "va", "vor", "sa", "pot", "poate", "trebuie",
}


def normalize(value: str) -> str:
    return SPACE_RE.sub(
        " ", re.sub(r"[^a-z0-9]+", " ", unidecode(value).lower())
    ).strip()


def _stem(token: str) -> str:
    return token[:-2] if len(token) >= 6 and not token.isdigit() else token


def _query_tokens(query: str) -> list[tuple[str, str]]:
    """Return ``(token, stem)`` pairs, dropping stop-words."""
    parts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in normalize(query).split():
        if token in STOPWORDS or len(token) < 2:
            continue
        stem = _stem(token)
        if stem in seen:
            continue
        seen.add(stem)
        parts.append((token, stem))
    return parts


def _fts_term(stem: str) -> str:
    # Numbers are codes/article numbers: match them exactly, not as prefixes.
    if stem.isdigit():
        return f'"{stem}"'
    return f'"{stem}"*'


def _proximity(normalized_text: str, stems: list[str]) -> float:
    """0..1 bonus when matched stems appear close together in the same node."""
    positions: list[int] = []
    for stem in stems:
        start = normalized_text.find(stem)
        if start >= 0:
            positions.append(start)
    if len(positions) < 2:
        return 0.0
    span = max(positions) - min(positions)
    return max(0.0, 1.0 - span / 600.0)



class LegalIndex:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.root = resolve_data_dir(data_dir)
        self.db_path = (
            Path(db_path).expanduser().resolve()
            if db_path
            else step_dir(self.root, "processed") / "law.db"
        )
        self.graph_path = self.db_path.parent / "graph.db"
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Index not found: {self.db_path}. Run "
                "'scrapper-legislatie-just-cli process' first."
            )
        self.connection = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True
        )
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    # ----------------------------------------------------------------- helpers
    def link(self, row: sqlite3.Row | dict[str, Any]) -> str:
        html_rel = row["html_rel"]
        anchor = row["anchor"]
        uri = (self.root / html_rel).resolve().as_uri()
        return f"{uri}#{anchor}" if anchor else uri

    @staticmethod
    def _row_text(row: sqlite3.Row) -> str:
        return SPACE_RE.sub(" ", (row["text"] or "")).strip()

    def _doc_title(self, slug: str) -> str:
        row = self.connection.execute(
            "SELECT title FROM docs WHERE slug = ? ORDER BY is_latest DESC LIMIT 1",
            (slug,),
        ).fetchone()
        return row["title"] if row else slug

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str,
        doc: str | None = None,
        kind: str | None = None,
        limit: int = 8,
        latest_only: bool = True,
    ) -> list[dict[str, Any]]:
        parts = _query_tokens(query)
        if not parts:
            return []

        total_nodes = self.connection.execute(
            "SELECT count(*) FROM nodes"
        ).fetchone()[0]
        if not total_nodes:
            return []

        # Drop query terms that do not occur in the corpus (they only turn an
        # otherwise good query into an empty result) and compute IDF so rare,
        # specific words outrank frequent ones.
        terms: list[tuple[str, str, str, float]] = []
        for token, stem in parts:
            term = _fts_term(stem)
            try:
                df = self.connection.execute(
                    "SELECT count(*) FROM nodes_fts WHERE nodes_fts MATCH ?",
                    (term,),
                ).fetchone()[0]
            except sqlite3.OperationalError:
                df = 0
            if df:
                idf = math.log(1.0 + total_nodes / df)
                terms.append((token, stem, term, idf))
        if not terms:
            return []

        sql = """
            SELECT n.id, n.doc_slug, n.doc_version, n.kind, n.label,
                   n.number, n.text, n.path, n.parent, n.anchor,
                   n.article_id, n.depth, n.order_idx,
                   d.title, d.html_rel, d.consolidation_date,
                   d.is_latest, d.source_url,
                   a.label AS article_label, a.number AS article_number,
                   a.anchor AS article_anchor,
                   bm25(nodes_fts, 0.0, 4.0, 2.0, 1.0) AS score,
                   snippet(nodes_fts, 3, '>>>', '<<<', '…', 36) AS snip
            FROM nodes_fts
            JOIN nodes n ON n.id = nodes_fts.node_id
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            LEFT JOIN nodes a ON a.id = n.article_id
            WHERE nodes_fts MATCH ?
              AND (? IS NULL OR n.doc_slug = ?)
              AND (? IS NULL OR n.kind = ?)
              AND n.kind NOT IN ('titlu', 'capitol', 'sectiune', 'document')
              AND (? = 0 OR d.is_latest = 1)
            ORDER BY score
            LIMIT ?
        """

        def fetch(match: str, count: int) -> list[sqlite3.Row]:
            try:
                return self.connection.execute(
                    sql,
                    (
                        match,
                        doc,
                        doc,
                        kind,
                        kind,
                        int(latest_only),
                        count,
                    ),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

        collected: dict[str, sqlite3.Row] = {}

        def collect(rows: list[sqlite3.Row]) -> None:
            for row in rows:
                collected.setdefault(row["id"], row)

        # 1. Per-term retrieval. This guarantees that a rare term such as
        #    "programator" is represented even when a combined OR query lets
        #    frequent words dominate the BM25 ordering.
        per_term = max(limit * 20, 120)
        for _token, _stem, term, _idf in terms:
            collect(fetch(term, per_term))

        # 2. Pairwise AND. A node containing two query terms ("scutire" +
        #    "impozit") is usually much more relevant than one containing only
        #    a rare word with a different meaning.
        if len(terms) > 1:
            for index, (_, _, left, _) in enumerate(terms):
                for _, _, right, _ in terms[index + 1 :]:
                    collect(fetch(f"{left} AND {right}", max(limit * 8, 80)))

        # 3. Full AND for queries where every term is present in one node.
        if len(terms) > 1:
            collect(
                fetch(
                    " AND ".join(term for _, _, term, _ in terms),
                    max(limit * 4, 20),
                )
            )

        scored: list[tuple[set[str], set[str], float, sqlite3.Row]] = []
        for row in collected.values():
            label_text = normalize(row["label"] or "")
            article_text = normalize(row["article_label"] or "")
            doc_text = normalize(row["title"] or "")
            body_text = normalize(row["text"] or "")
            haystack = " ".join((label_text, article_text, doc_text, body_text))
            exact_tokens: set[str] = set()
            matched_stems: set[str] = set()
            score = 0.0
            for token, stem, _term, idf in terms:
                # Full token first: "programator" must not get the same credit
                # as "programat" (scheduled), even if both share a prefix.
                exact = bool(
                    re.search(
                        rf"(?<![a-z0-9]){re.escape(token)}",
                        haystack,
                    )
                )
                if exact:
                    exact_tokens.add(token)
                    matched_stems.add(stem)
                    in_label = token in label_text or token in article_text
                    score += idf * (1.8 if in_label else 1.0)
                    continue
                if stem in haystack:
                    matched_stems.add(stem)
                    in_label = stem in label_text or stem in article_text
                    score += idf * 0.45 * (1.3 if in_label else 1.0)
            if not matched_stems:
                continue
            if len(exact_tokens) == len(terms):
                score *= 1.4
            idf_sum = sum(idf for _t, stem, _term, idf in terms if stem in matched_stems)
            score += idf_sum * 0.5 * _proximity(haystack, list(matched_stems))
            score += min(2.0, max(0.0, -row["score"]) / 8.0)
            scored.append((exact_tokens, matched_stems, score, row))

        # Aggregate per article: an article often answers a question across
        # several paragraphs, so coverage of the whole article matters more
        # than the single best paragraph. The best paragraph stays the
        # clickable evidence anchor.
        articles: dict[str, dict[str, Any]] = {}
        for exact_tokens, matched_stems, score, row in scored:
            key = row["article_id"] or row["id"]
            bucket = articles.setdefault(
                key,
                {
                    "exact": set(),
                    "stems": set(),
                    "nodes": [],
                },
            )
            bucket["exact"] |= exact_tokens
            bucket["stems"] |= matched_stems
            bucket["nodes"].append((score, exact_tokens, matched_stems, row))

        ranked: list[tuple[int, int, float, sqlite3.Row]] = []
        for bucket in articles.values():
            nodes = bucket["nodes"]
            best_score = max(item[0] for item in nodes)
            # The evidence anchor is the paragraph that covers the most query
            # terms (not merely the highest BM25), so the click lands on the
            # sentence that actually answers the question.
            _, _, _, best_row = max(
                nodes,
                key=lambda item: (len(item[1]), len(item[2]), item[0]),
            )
            # Reward articles where several paragraphs match (e.g. an article
            # with a title, a general rule and two exceptions).
            extra = sum(
                sorted((item[0] for item in nodes), reverse=True)[1:6]
            )
            article_score = best_score + 0.35 * extra
            ranked.append(
                (
                    len(bucket["stems"]),
                    len(bucket["exact"]),
                    article_score,
                    best_row,
                )
            )

        # If the corpus contains exact matches for the query, do not let
        # stem-only approximations (e.g. "programat" for "programator") win.
        max_exact = max((item[1] for item in ranked), default=0)
        if max_exact > 0:
            ranked = [item for item in ranked if item[1] > 0]

        results: list[dict[str, Any]] = []
        for stem_coverage, exact_coverage, score, row in sorted(
            ranked, key=lambda item: (-item[1], -item[0], -item[2])
        ):
            if len(results) >= limit:
                break
            article_label = row["article_label"]
            article_link = None
            if row["article_anchor"]:
                uri = (self.root / row["html_rel"]).resolve().as_uri()
                article_link = f"{uri}#{row['article_anchor']}"
            results.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "label": row["label"],
                    "number": row["number"],
                    "text": self._row_text(row),
                    "snippet": row["snip"],
                    "path": row["path"],
                    "doc": row["doc_slug"],
                    "doc_title": row["title"],
                    "version": row["doc_version"],
                    "date": row["consolidation_date"],
                    "link": self.link(row),
                    "source_url": row["source_url"],
                    "score": round(score, 3),
                    "matched": exact_coverage,
                    "matched_stem": stem_coverage,
                    "article_id": row["article_id"],
                    "article_label": article_label,
                    "article_number": row["article_number"],
                    "article_link": article_link,
                }
            )
        return results

    # --------------------------------------------------------------- rendering
    def content(self, node_id: str, max_chars: int = 12000) -> dict[str, Any]:
        node = self.connection.execute(
            """
            SELECT n.*, d.title, d.html_rel, d.consolidation_date, d.source_url
            FROM nodes n
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            WHERE n.id = ?
            """,
            (node_id,),
        ).fetchone()
        if node is None:
            raise KeyError(f"Unknown node: {node_id}")

        rows = self.connection.execute(
            """
            WITH RECURSIVE tree(id, depth) AS (
                SELECT id, 0 FROM nodes WHERE id = ?
                UNION ALL
                SELECT n.id, tree.depth + 1
                FROM nodes n JOIN tree ON n.parent = tree.id
            )
            SELECT n.*, tree.depth AS rel_depth
            FROM tree JOIN nodes n ON n.id = tree.id
            ORDER BY n.order_idx
            """,
            (node_id,),
        ).fetchall()

        lines: list[str] = []
        links: list[dict[str, str]] = []
        used = 0
        truncated = False
        for row in rows:
            prefix = ""
            indent = "  " * max(0, row["rel_depth"] - 1)
            if row["kind"] in {"alineat", "punct", "litera", "linie"} and row["label"]:
                prefix = f"{row['label']} "
            text = self._row_text(row)
            if row["kind"] in {"titlu", "capitol", "sectiune"}:
                text = ""
            if row["kind"] == "nota":
                line = f"> **Nota:** {text}"
            else:
                line = f"{indent}{prefix}{text}".rstrip()
            if not line and row["id"] != node_id:
                continue
            if used + len(line) > max_chars and row["id"] != node_id:
                truncated = True
                break
            lines.append(line)
            used += len(line)
            if row["anchor"]:
                links.append(
                    {
                        "label": row["label"] or row["kind"],
                        "kind": row["kind"],
                        "link": self.link(
                            {"html_rel": node["html_rel"], "anchor": row["anchor"]}
                        ),
                    }
                )

        if node["kind"] == "articol" and node["label"]:
            lines.insert(0, f"**{node['label']}**")

        body = "\n".join(line for line in lines if line != "")
        if truncated:
            body += "\n\n… (articol lung; cere o subdiviziune pentru textul complet)"

        return {
            "id": node["id"],
            "kind": node["kind"],
            "label": node["label"],
            "path": node["path"],
            "doc": node["doc_slug"],
            "doc_title": node["title"],
            "version": node["doc_version"],
            "date": node["consolidation_date"],
            "link": self.link(node),
            "source_url": node["source_url"],
            "text": body,
            "links": links,
            "truncated": truncated,
        }

    def article(
        self, doc: str, number: str, version: str | None = None
    ) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT n.id FROM nodes n
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            WHERE n.doc_slug = ? AND n.kind = 'articol' AND n.number = ?
              AND (? IS NULL OR n.doc_version = ?)
            ORDER BY d.is_latest DESC, d.version DESC
            LIMIT 1
            """,
            (doc, str(number), version, version),
        ).fetchone()
        if row is not None:
            return self.content(row["id"])

        # Documents without ``S_ART`` structure (e.g. CAEN) store codes in
        # ordinary paragraphs. Fall back to a search and show the matched
        # paragraph together with its immediate neighbours (code + label).
        hits = self.search(
            str(number),
            doc=doc,
            limit=5,
            latest_only=version is None,
        )
        for hit in hits:
            if version is None or hit["version"] == version:
                return self.window(hit["id"])
        raise KeyError(f"Article {number} not found in {doc}")

    def window(
        self, node_id: str, before: int = 1, after: int = 1
    ) -> dict[str, Any]:
        """Render a node plus nearby siblings (useful for table-like docs)."""
        node = self.connection.execute(
            """
            SELECT n.*, d.title, d.html_rel, d.consolidation_date, d.source_url
            FROM nodes n
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            WHERE n.id = ?
            """,
            (node_id,),
        ).fetchone()
        if node is None:
            raise KeyError(f"Unknown node: {node_id}")

        rows = self.connection.execute(
            """
            SELECT n.* FROM nodes n
            WHERE n.doc_slug = ? AND n.doc_version = ?
              AND n.order_idx BETWEEN ? AND ?
            ORDER BY n.order_idx
            """,
            (
                node["doc_slug"],
                node["doc_version"],
                max(0, node["order_idx"] - before),
                node["order_idx"] + after,
            ),
        ).fetchall()

        lines: list[str] = []
        links: list[dict[str, str]] = []
        for row in rows:
            text = self._row_text(row)
            if not text:
                continue
            prefix = f"{row['label']} " if row["label"] else ""
            lines.append(f"{prefix}{text}".strip())
            if row["anchor"]:
                links.append(
                    {
                        "label": row["label"] or row["kind"],
                        "kind": row["kind"],
                        "link": self.link(
                            {"html_rel": node["html_rel"], "anchor": row["anchor"]}
                        ),
                    }
                )

        return {
            "id": node["id"],
            "kind": node["kind"],
            "label": node["label"] or node["text"],
            "path": node["path"],
            "doc": node["doc_slug"],
            "doc_title": node["title"],
            "version": node["doc_version"],
            "date": node["consolidation_date"],
            "link": self.link(node),
            "source_url": node["source_url"],
            "text": "\n".join(lines),
            "links": links,
            "truncated": False,
        }

    def show(self, node_id: str) -> dict[str, Any]:
        data = self.content(node_id)
        ancestors: list[dict[str, str]] = []
        parent = self.connection.execute(
            "SELECT parent FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        seen: set[str] = set()
        while parent and parent["parent"] and parent["parent"] not in seen:
            seen.add(parent["parent"])
            row = self.connection.execute(
                "SELECT id, kind, label, anchor, doc_slug, doc_version FROM nodes WHERE id = ?",
                (parent["parent"],),
            ).fetchone()
            if row is None:
                break
            doc = self.connection.execute(
                "SELECT html_rel FROM docs WHERE slug=? AND version=?",
                (row["doc_slug"], row["doc_version"]),
            ).fetchone()
            ancestors.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "label": row["label"] or row["kind"],
                    "link": self.link(
                        {"html_rel": doc["html_rel"], "anchor": row["anchor"]}
                    ),
                }
            )
            parent = self.connection.execute(
                "SELECT parent FROM nodes WHERE id = ?", (row["id"],)
            ).fetchone()
        data["ancestors"] = list(reversed(ancestors))
        data["refs"] = self.neighbors(node_id)
        return data

    def neighbors(
        self, node_id: str, subtree: bool = False
    ) -> dict[str, list[dict[str, Any]]]:
        """Read incoming/outgoing citations from ``graph.db`` (GraphQLite)."""
        from .graph import cypher_literal, run_cypher

        row = self.connection.execute(
            "SELECT article_id FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown node: {node_id}")
        article_id = row["article_id"] or node_id
        node_lit = cypher_literal(node_id)
        article_lit = cypher_literal(article_id)

        def query(direction: str) -> list[dict[str, Any]]:
            if direction == "outgoing":
                where_var, target = "a", "b"
            else:
                where_var, target = "b", "a"
            if subtree:
                where = (
                    f"({where_var}.id = {node_lit} OR "
                    f"{where_var}.article_id = {article_lit})"
                )
            else:
                where = f"{where_var}.id = {node_lit}"
            cypher = (
                f"MATCH (a)-[r]->(b) "
                f"WHERE {where} AND r.aggregate = 0 "
                f"RETURN type(r) AS edge_type, r.rel AS rel, r.raw AS raw, "
                f"r.confidence AS confidence, {target}.id AS target, "
                f"{target}.label AS label, {target}.kind AS kind, "
                f"{target}.doc AS doc, {target}.doc_title AS doc_title, "
                f"{target}.anchor AS anchor, {target}.link AS link"
            )
            try:
                return run_cypher(self.graph_path, cypher)
            except FileNotFoundError as ex:
                raise FileNotFoundError(
                    f"Citation graph not found: {self.graph_path}. Run "
                    "'scrapper-legislatie-just-cli process' first."
                ) from ex

        def convert(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            seen: set[tuple] = set()
            out: list[dict[str, Any]] = []
            for row in rows:
                rel = row["rel"] or (row["edge_type"] or "").lower()
                key = (rel, row["target"], row["raw"])
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {
                        "rel": rel,
                        "raw": row["raw"],
                        "confidence": row["confidence"],
                        "target": row["target"],
                        "label": row["label"],
                        "kind": row["kind"],
                        "doc": row["doc"],
                        "doc_title": row["doc_title"],
                        "link": row["link"],
                    }
                )
            return out

        return {
            "outgoing": convert(query("outgoing")),
            "incoming": convert(query("incoming")),
        }

    def docs(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT slug, version, title, description, html_rel, source_url,
                   consolidation_date, is_latest
            FROM docs
            ORDER BY slug, is_latest DESC, version DESC
            """
        ).fetchall()
        return [
            {
                "slug": row["slug"],
                "version": row["version"],
                "title": row["title"],
                "description": row["description"],
                "link": self.link({"html_rel": row["html_rel"], "anchor": None}),
                "source_url": row["source_url"],
                "date": row["consolidation_date"],
                "is_latest": bool(row["is_latest"]),
            }
            for row in rows
        ]

    # ----------------------------------------------------------------- context
    def context(self, question: str, limit: int = 4) -> str:
        hits = self.search(question, limit=max(limit * 3, 8))
        if not hits:
            return (
                "Nu am găsit pasaje în corpus pentru această întrebare.\n"
                "Încearcă sinonime sau numele actului."
            )

        articles: list[str] = []
        for hit in hits:
            article_id = self.connection.execute(
                "SELECT article_id FROM nodes WHERE id = ?", (hit["id"],)
            ).fetchone()
            target = (
                article_id["article_id"]
                if article_id and article_id["article_id"]
                else hit["id"]
            )
            if target not in articles:
                articles.append(target)
            if len(articles) >= limit:
                break

        blocks: list[str] = [
            "# Evidențe din corpus",
            "",
            f"Întrebare: **{question}**",
            "",
            "Fiecare link deschide HTML-ul original la ancora exactă.",
            "",
        ]
        for index, article_id in enumerate(articles, 1):
            data = self.content(article_id, max_chars=8000)
            refs = self.neighbors(article_id, subtree=True)
            blocks.append(f"## {index}. {data['doc_title']} — {data['label']}")
            blocks.append(f"SURSA: {data['link']}")
            if data["date"]:
                blocks.append(f"Versiune consolidată: {data['date']}")
            blocks.append("")
            blocks.append(data["text"])
            blocks.append("")

            useful = [
                ref
                for ref in refs["outgoing"]
                if ref["rel"] in {"citat", "mentioneaza"} and ref["link"]
            ][:8]
            if useful:
                blocks.append("**Trimiteri din acest text:**")
                for ref in useful:
                    label = ref["label"] or ref["raw"] or ref["target"]
                    blocks.append(f"- {label} — {ref['link']}")
                blocks.append("")

            cited_by = [
                ref
                for ref in refs["incoming"]
                if ref["rel"] == "citat" and ref["link"]
            ][:5]
            if cited_by:
                blocks.append("**Este citat de:**")
                for ref in cited_by:
                    label = ref["label"] or ref["raw"] or ref["target"]
                    blocks.append(f"- {label} — {ref['link']}")
                blocks.append("")
            if data["truncated"]:
                blocks.append(
                    "Text trunchiat. Cere `show` pe subdiviziuni pentru restul."
                )
                blocks.append("")
        return "\n".join(blocks)

    # ---------------------------------------------------------------- markdown
    def format_search(self, results: list[dict[str, Any]]) -> str:
        if not results:
            return "Niciun rezultat."
        lines: list[str] = []
        for index, item in enumerate(results, 1):
            title = item["article_label"] or item["label"] or item["kind"]
            lines.append(f"### {index}. {item['doc_title']} — {title}")
            lines.append(f"SURSA: {item['link']}")
            if item["article_link"] and item["article_link"] != item["link"]:
                lines.append(f"ARTICOL: {item['article_link']}")
            lines.append(
                f"Fragment: {item['kind']} · potriviri: {item['matched']}"
            )
            if item["date"]:
                lines.append(f"Versiune: {item['date']}")
            lines.append("")
            snippet = (item["snippet"] or item["text"][:600]).replace(
                ">>>", "**"
            ).replace("<<<", "**")
            lines.append(snippet)
            lines.append("")
        return "\n".join(lines)


def open_index(data_dir: str | Path | None = None) -> LegalIndex:
    return LegalIndex(data_dir=data_dir)
