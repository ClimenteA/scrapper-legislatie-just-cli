"""Read-only query layer over ``4-processed/law.db``.

Every result carries a ``file://.../document.html#anchor`` link that opens the
original downloaded HTML at the exact article/paragraph. No embeddings: FTS5
(BM25) for lookup, the reference graph for context expansion.
"""

from __future__ import annotations

import math
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from unidecode import unidecode

from .config import resolve_data_dir, step_dir

SPACE_RE = re.compile(r"\s+")


class NotFoundError(KeyError):
    """A requested article or node is absent from the index.

    This is an anticipated lookup miss (typically a bad identifier supplied by
    an MCP client), not a bug. It subclasses :class:`KeyError` so the CLI's
    existing ``except KeyError`` handling keeps working, but ``str()`` returns
    the plain message instead of KeyError's quoted repr.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "Not found"


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
        public_base_url: str | None = None,
    ) -> None:
        self.root = resolve_data_dir(data_dir)
        self.public_base_url = public_base_url
        self.db_path = (
            Path(db_path).expanduser().resolve()
            if db_path
            else step_dir(self.root, "processed") / "law.db"
        )
        self.graph_path = self.db_path.parent / "graph.db"
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Index not found: {self.db_path}. Run the indexer with "
                "REINDEX_ON_START=1 first (or wait for the scheduled rebuild)."
            )
        self.connection = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True
        )
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> LegalIndex:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----------------------------------------------------------------- helpers
    def link(self, row: sqlite3.Row | dict[str, Any]) -> str:
        from .links import make_link

        return make_link(
            self.root, row["html_rel"], row["anchor"], self.public_base_url
        )

    @staticmethod
    def _row_text(row: sqlite3.Row) -> str:
        return SPACE_RE.sub(" ", (row["text"] or "")).strip()

    def _doc_title(self, slug: str) -> str:
        row = self.connection.execute(
            "SELECT title FROM docs WHERE slug = ? ORDER BY is_latest DESC LIMIT 1",
            (slug,),
        ).fetchone()
        return row["title"] if row else slug

    def suggest_articles(
        self, doc: str, number: str, version: str | None = None, limit: int = 5
    ) -> list[str]:
        """Article numbers in ``doc`` closest to ``number`` (as a hint)."""
        def leading_int(value: str) -> int:
            match = re.match(r"\d+", value)
            return int(match.group()) if match else 0

        rows = self.connection.execute(
            """
            SELECT DISTINCT n.number FROM nodes n
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            WHERE n.doc_slug = ? AND n.kind = 'articol' AND n.number IS NOT NULL
              AND (? IS NULL OR n.doc_version = ?)
            ORDER BY d.is_latest DESC, d.version DESC
            """,
            (doc, version, version),
        ).fetchall()
        target = leading_int(str(number))
        numbers = {row["number"] for row in rows}
        return sorted(
            numbers,
            key=lambda value: (abs(leading_int(value) - target), value),
        )[:limit]

    def resolve_node_id(self, node_id: str) -> str:
        """Accept a full ``slug@version#anchor`` id or a bare ``#anchor``.

        MCP clients often copy the bare anchor from a ``SURSA:`` link. Anchors
        are not unique across acts, so when several nodes share one we raise a
        :class:`NotFoundError` listing the full ids instead of guessing.
        """
        if self.connection.execute(
            "SELECT 1 FROM nodes WHERE id = ?", (node_id,)
        ).fetchone():
            return node_id
        if "@" in node_id:
            return node_id  # a full id that simply isn't in the index

        anchor = node_id.lstrip("#")
        rows = self.connection.execute(
            """
            SELECT n.id FROM nodes n
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            WHERE n.anchor = ?
            ORDER BY d.is_latest DESC, d.version DESC
            """,
            (anchor,),
        ).fetchall()
        ids = list(dict.fromkeys(row["id"] for row in rows))
        if not ids:
            return node_id
        if len(ids) == 1:
            return ids[0]
        candidates = ", ".join(ids[:5])
        raise NotFoundError(
            f"ID-ul {node_id} este ambiguu (ancora folosita in mai multe acte). "
            f"Foloseste un ID complet, ex: {candidates}."
        )

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
                   (SELECT t.text FROM nodes t
                     WHERE t.article_id = n.article_id
                       AND t.kind IN ('paragraf', 'alineat', 'litera', 'punct')
                     ORDER BY t.order_idx
                     LIMIT 1) AS article_lead,
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
        #    frequent words dominate the BM25 ordering. The cap keeps a long
        #    natural-language question from turning this into thousands of
        #    scanned rows; the ranking below re-scores everything anyway.
        per_term = min(max(limit * 20, 120), 400)
        for _token, _stem, term, _idf in terms:
            collect(fetch(term, per_term))

        # 2. Pairwise AND. A node containing two query terms ("scutire" +
        #    "impozit") is usually much more relevant than one containing only
        #    a rare word with a different meaning. Only the most discriminating
        #    terms are paired: with 20 question terms this would otherwise be
        #    ~190 FTS queries, which dominates tool latency for no recall gain.
        pair_terms = sorted(terms, key=lambda item: -item[3])[:8]
        if len(pair_terms) > 1:
            for index, (_, _, left, _) in enumerate(pair_terms):
                for _, _, right, _ in pair_terms[index + 1 :]:
                    collect(fetch(f"{left} AND {right}", min(max(limit * 8, 80), 200)))

        # 3. Full AND on the discriminating terms: a natural-language question
        #    almost never has all of its words in one paragraph.
        if len(pair_terms) > 1:
            collect(
                fetch(
                    " AND ".join(term for _, _, term, _ in pair_terms),
                    max(limit * 4, 20),
                )
            )

        scored: list[tuple[set[str], set[str], float, sqlite3.Row]] = []
        for row in collected.values():
            label_text = normalize(row["label"] or "")
            article_text = normalize(row["article_label"] or "")
            # The first short paragraph after the article heading is usually
            # its title ("Scutiri", "Definitia microintreprinderii", ...).
            # Include it in the label context so a title term outranks an
            # incidental mention in a later paragraph.
            if row["article_lead"]:
                article_text = (
                    article_text
                    + " "
                    + normalize(str(row["article_lead"])[:300])
                )
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
            # Definition-shaped nodes ("telemunca - forma de organizare...")
            # should win against paragraphs that merely mention the term.
            for token, stem, _term, idf in terms:
                if body_text.startswith(token + " ") or body_text.startswith(
                    token + " -"
                ):
                    score += idf * 0.8
                elif body_text.startswith(stem):
                    score += idf * 0.35
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
                from .links import make_link

                article_link = make_link(
                    self.root,
                    row["html_rel"],
                    row["article_anchor"],
                    self.public_base_url,
                )
            link = self.link(row)
            if "#" not in link:
                # A document-level note has no HTML anchor. Fall back to the
                # article anchor when possible; otherwise skip it so every
                # returned evidence link is clickable.
                if not article_link:
                    continue
                link = article_link
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
                    "link": link,
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
            raise NotFoundError(
                f"Nodul {node_id} nu exista in index. "
                "Foloseste search sau context pentru a obtine ID-uri valide."
            )

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
        near = self.suggest_articles(doc, str(number), version)
        hint = f" Articole disponibile in apropiere: {', '.join(near)}." if near else ""
        raise NotFoundError(
            f"Articolul {number} nu exista in {doc}.{hint} "
            "Foloseste search sau docs pentru valori valide."
        )

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
            raise NotFoundError(
                f"Nodul {node_id} nu exista in index. "
                "Foloseste search sau context pentru a obtine ID-uri valide."
            )

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
        node_id = self.resolve_node_id(node_id)
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

        node_id = self.resolve_node_id(node_id)
        row = self.connection.execute(
            "SELECT article_id FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"Nodul {node_id} nu exista in index. "
                "Foloseste search sau context pentru a obtine ID-uri valide."
            )
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
                return run_cypher(
                    self.graph_path, cypher, base_url=self.public_base_url
                )
            except FileNotFoundError as ex:
                raise FileNotFoundError(
                    f"Citation graph not found: {self.graph_path}. Run the "
                    "indexer with REINDEX_ON_START=1 first (or wait for the "
                    "scheduled rebuild)."
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

    def docs(self, latest_only: bool = True) -> list[dict[str, Any]]:
        from .research import classify_family

        rows = self.connection.execute(
            """
            SELECT slug, version, title, description, html_rel, source_url,
                   consolidation_date, is_latest
            FROM docs
            WHERE (? = 0 OR is_latest = 1)
            ORDER BY slug, is_latest DESC, version DESC
            """,
            (int(latest_only),),
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
                "family": classify_family(row["slug"], row["title"] or ""),
            }
            for row in rows
        ]

    # ---------------------------------------------------------------- research
    def _doc_has_terms(self, question: str, slug: str) -> bool:
        """Cheap existence probe: does the act contain the question's terms?

        The full ranked search scans every matching node; this probe stops
        at the first row, which keeps per-family research fast on large acts.
        Common words ("cod", "inregistrare") occur in every act, so the probe
        uses the rarer half of the question's vocabulary: an act that contains
        at least one specific concept is worth the full search, while
        boilerplate-only acts are skipped. The rarest words may be
        question-specific ("VIES") and absent everywhere, hence several
        probes instead of one.
        """
        parts = _query_tokens(question)
        if not parts:
            return False
        total = self.connection.execute(
            "SELECT count(*) FROM nodes"
        ).fetchone()[0]
        weighted: list[tuple[float, str]] = []
        for _token, stem in parts:
            term = _fts_term(stem)
            try:
                df = self.connection.execute(
                    "SELECT count(*) FROM nodes_fts WHERE nodes_fts MATCH ?",
                    (term,),
                ).fetchone()[0]
            except sqlite3.OperationalError:
                df = 0
            if df:
                weighted.append((math.log(1.0 + total / df), term))
        if not weighted:
            return False
        weighted.sort(key=lambda item: -item[0])
        # Common words ("cod", "taxa") occur in every act, so the probe uses
        # the rarer half of the question's vocabulary: an act that contains
        # at least one specific concept is worth the full search, while
        # boilerplate-only acts are skipped. The rarest words may be
        # question-specific ("VIES") and absent everywhere, hence several
        # probes instead of one.
        cutoff = max(1, len(weighted) // 2)
        try:
            for _idf, term in weighted[:cutoff]:
                row = self.connection.execute(
                    """
                    SELECT 1 FROM nodes_fts
                    JOIN nodes n ON n.id = nodes_fts.node_id
                    JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
                    WHERE nodes_fts MATCH ? AND n.doc_slug = ? AND d.is_latest = 1
                    LIMIT 1
                    """,
                    (term, slug),
                ).fetchone()
                if row is not None:
                    return True
        except sqlite3.OperationalError:
            return True  # malformed expression: let the real search decide
        return False

    def select_docs(
        self, question: str, max_docs: int = 12
    ) -> dict[str, Any]:
        """Narrow the catalogue to the acts worth searching for ``question``.

        Ranks the latest consolidation of every act by title+description
        overlap, then guarantees the shortlist covers the companion families
        of the topic (norms, EU acts for harmonised taxes, ...). This is the
        mechanical part of the source map: the agent still has to search each
        family and read the text.
        """
        from .research import (
            FAMILY_LABELS,
            _expand_companions,
            score_docs,
            select_families,
            topic_families,
        )

        docs = self.docs(latest_only=True)
        topics, required = topic_families(question)
        ranked = score_docs(question, docs)
        # Descriptions are short and sometimes generic; the text index knows
        # better which acts actually contain the question's concepts. Merge
        # the documents of the strongest text hits into the ranking so an act
        # with a thin description is not lost (EU acts, older orders...).
        by_slug = {doc["slug"]: doc for doc in docs}
        # Form PDF text is boilerplate ("cod de inregistrare in scopuri de
        # TVA" appears in every declaration): it must not drown the acts that
        # state the rules. Filter form hits out of the selection signal; the
        # form family is covered by metadata scoring and the action sheets.
        text_hits = [
            hit
            for hit in self.search(question, limit=50)
            if by_slug.get(hit["doc"], {}).get("family") != "formulare"
        ]
        if text_hits:
            top_score = ranked[0][0] if ranked else max(
                hit["score"] for hit in text_hits
            )
            merged: dict[str, tuple[float, set[str], dict[str, Any]]] = {
                doc["slug"]: (score, matched, doc)
                for score, matched, doc in ranked
            }
            seen_docs: set[str] = set()
            for hit in text_hits:
                doc = by_slug.get(hit["doc"])
                if doc is None or doc["slug"] in seen_docs:
                    continue
                seen_docs.add(doc["slug"])
                # Decay per distinct act, not per hit: one verbose act must
                # not consume the whole bonus budget with repeated hits.
                # Forms carry formulaic boilerplate, so their text-hit bonus
                # is halved: metadata already describes them well enough.
                bonus = top_score * (1.0 - 0.04 * (len(seen_docs) - 1))
                if doc["family"] == "formulare":
                    bonus *= 0.5
                previous = merged.get(doc["slug"])
                if previous:
                    score, matched, _ = previous
                    merged[doc["slug"]] = (
                        score + bonus * 0.5, matched, doc
                    )
                else:
                    merged[doc["slug"]] = (bonus, set(), doc)
            ranked = sorted(
                merged.values(), key=lambda item: (-item[0], item[2]["slug"])
            )
        max_docs = max(4, min(max_docs, 20))
        shortlist = select_families(
            ranked, required, max_docs=max_docs, question=question, topics=topics
        )
        shortlist = _expand_companions(shortlist, docs)
        covered = {doc["family"] for doc in shortlist}
        return {
            "question": question,
            "topics": topics,
            "required_families": required,
            "missing_families": [f for f in required if f not in covered],
            "family_labels": FAMILY_LABELS,
            "docs": shortlist,
            "total_docs": len(docs),
        }

    def research(
        self,
        question: str,
        limit: int = 3,
        max_docs: int = 10,
        compact: bool = False,
    ) -> str:
        """Evidence package across source families, with explicit gaps.

        For every act in the shortlist it runs the text search and renders
        the best hits (exact anchor + article link + text), then lists the
        outgoing citations of the top hits so exceptions can be followed,
        and finally reports which required families produced no evidence.

        ``compact=True`` keeps the anchors, links, family balance and form
        sheets but replaces the full node/article text with a short excerpt
        and drops the citation hop: the client then drills down with
        ``article``/``show``/``neighbors`` only where the answer depends on it.
        It also runs the independent per-act searches in a thread pool; each
        worker opens its own read-only connection because sqlite3 objects are
        bound to the thread that created them.
        """
        from .research import FAMILY_LABELS, scenario_expansions

        plan = self.select_docs(question, max_docs=max_docs + 4)
        limit = max(1, min(limit, 6))
        blocks: list[str] = [
            "# Cercetare pe familii de surse",
            "",
            f"Întrebare: **{question}**",
            "",
            "Selecție de acte după descriere/titlu, apoi căutare de text în "
            "fiecare. Citește textul, urmărește trimiterile pentru excepții și "
            "declară explicit orice familie rămasă fără dovezi.",
            "",
        ]
        if compact:
            blocks.extend([
                "Mod compact: pasaje scurte cu ancora exacta. Pentru text "
                "integral foloseste `article`/`show`; pentru exceptii "
                "`neighbors(node_id=...)`.",
                "",
            ])
        if plan["topics"]:
            blocks.append(
                "Teme detectate: " + ", ".join(plan["topics"])
                + ". Familii obligatorii de verificat: "
                + ", ".join(
                    FAMILY_LABELS.get(f, f) for f in plan["required_families"]
                )
                + "."
            )
            blocks.append("")

        evidence_families: set[str] = set()
        per_doc_hits: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        always_searched = set(plan["required_families"]) | {"coduri"}
        first_docs: list[dict[str, Any]] = []
        for doc in plan["docs"]:
            # Forms are actions, not legal grounds: their PDF text is full of
            # boilerplate that matches any fiscal question. They are already
            # surfaced through the action sheets below, so research skips
            # their text and spends the budget on acts that state rules.
            if doc["family"] == "formulare":
                continue
            # Required families and the codes always get the full search:
            # their wording can differ from the question's (an EU act says
            # "persoana impozabila" where the question says "firma"), and a
            # false gap is worse than a slow query. For the remaining acts a
            # cheap rare-stem probe skips those that cannot contain the
            # question's concepts.
            if (
                doc["family"] not in always_searched
                and not self._doc_has_terms(question, doc["slug"])
            ):
                continue
            first_docs.append(doc)

        def search_in_worker(text: str, slug: str, count: int) -> list[dict[str, Any]]:
            worker = LegalIndex(
                data_dir=self.root,
                db_path=self.db_path,
                public_base_url=self.public_base_url,
            )
            try:
                return worker.search(text, doc=slug, limit=count)
            finally:
                worker.close()

        def parallel_search(
            texts: list[tuple[dict[str, Any], str, int]]
        ) -> dict[str, list[dict[str, Any]]]:
            if not texts:
                return {}
            if len(texts) == 1:
                doc, text, count = texts[0]
                return {doc["slug"]: search_in_worker(text, doc["slug"], count)}
            with ThreadPoolExecutor(max_workers=min(4, len(texts))) as pool:
                results = pool.map(
                    lambda item: search_in_worker(item[1], item[0]["slug"], item[2]),
                    texts,
                )
                return {item[0]["slug"]: hits for item, hits in zip(texts, results)}

        first_hits = parallel_search(
            [(doc, question, limit) for doc in first_docs]
        )
        for doc in first_docs:
            hits = first_hits.get(doc["slug"], [])
            if hits:
                evidence_families.add(doc["family"])
                per_doc_hits.append((doc, hits))

        # Second pass on the required families with the question's narrative
        # stripped to its legal concepts: questions narrate facts ("PFA",
        # "nu imi comunica"), while the decisive text states the rule ("nu
        # i-a comunicat codul"). Dropping the personal/narrative tokens lets
        # the condition-shaped paragraphs (presumptions, proof rules) rank.
        narrative = {"pfa", "srl", "ii", "if", "firma", "firmei", "imi", "mie",
                     "meu", "mea", "am", "avem", "facturez", "facturezi",
                     "doresc", "vreau", "multumesc"}
        tokens = [token for token, _stem in _query_tokens(question)
                  if token not in narrative]
        concept_query = " ".join(tokens)
        # The legal status vocabulary makes the condition-shaped paragraphs
        # (presumptions, proof rules) rank: the question asks about a status
        # ("persoana fizica/neimpozabila") even when it never says the word.
        if "tva" in plan["topics"]:
            concept_query += " client neimpozabil"
        # Scenario synonyms translate the user's narrative into the legal
        # vocabulary of presumptions, proofs and obligations, so the decisive
        # paragraph ranks even when the question never uses its wording.
        expansions = scenario_expansions(question, plan["topics"])
        if expansions:
            concept_query += " " + " ".join(expansions)
        if concept_query and concept_query != normalize(question):
            second_docs = [
                doc for doc in plan["docs"]
                if doc["family"] in plan["required_families"]
            ]
            # With scenario expansions the query is long; a couple more hits
            # keep both sides of a presumption pair (e.g. the proof rule and
            # the "may treat as non-taxable" rule) in the evidence package.
            second_limit = 4 if expansions else 3
            extra_hits = parallel_search(
                [(doc, concept_query, second_limit) for doc in second_docs]
            )
            for doc in second_docs:
                extra = extra_hits.get(doc["slug"], [])
                if not extra:
                    continue
                existing = next(
                    (hits for d, hits in per_doc_hits if d["slug"] == doc["slug"]),
                    None,
                )
                if existing is None:
                    evidence_families.add(doc["family"])
                    per_doc_hits.append((doc, extra))
                else:
                    for hit in extra:
                        if all(other["id"] != hit["id"] for other in existing):
                            existing.append(hit)

        for doc, hits in per_doc_hits:
            family = FAMILY_LABELS.get(doc["family"], doc["family"])
            blocks.append(f"## [{family}] {doc['title']}")
            blocks.append(f"Act: `{doc['slug']}` · versiune: {doc['date']}")
            blocks.append("")
            for hit in hits:
                title = hit["article_label"] or hit["label"] or hit["kind"]
                blocks.append(f"### {title}")
                blocks.append(f"SURSA: {hit['link']}")
                if hit["article_link"] and hit["article_link"] != hit["link"]:
                    blocks.append(f"ARTICOL: {hit['article_link']}")
                blocks.append("")
                if compact:
                    text = SPACE_RE.sub(" ", hit["text"]).strip()
                    excerpt = text[:320]
                    if len(text) > 320:
                        excerpt = excerpt.rstrip() + " …"
                    blocks.append(excerpt)
                else:
                    blocks.append(hit["text"][:1600])
                blocks.append("")
            top = hits[0]
            target = top["article_id"] or top["id"]
            if compact:
                blocks.append(
                    f"- Pentru excepții: `neighbors(node_id=\"{target}\")` "
                    "sau `article` pe articolul sursei de mai sus."
                )
                blocks.append("")
            else:
                # One hop of citations from the best hit, plus the full article
                # when the hit is a subdivision: consequences for third parties
                # and exceptions often sit in the next paragraph of the same
                # article, outside any citation edge.
                if top["article_id"] and top["article_id"] != top["id"]:
                    try:
                        article_data = self.content(
                            top["article_id"], max_chars=12000
                        )
                        if article_data["text"][:1600] != top["text"][:1600]:
                            blocks.append(
                                "**Articolul integral (context pentru excepții):**"
                            )
                            blocks.append(f"SURSA: {article_data['link']}")
                            blocks.append("")
                            blocks.append(article_data["text"])
                            blocks.append("")
                    except NotFoundError:
                        pass
                try:
                    refs = self.neighbors(target, subtree=True)
                except (FileNotFoundError, NotFoundError):
                    refs = {"outgoing": [], "incoming": []}
                useful = [
                    ref
                    for ref in refs["outgoing"]
                    if ref["rel"] in {"citat", "mentioneaza"} and ref["link"]
                ][:8]
                if useful:
                    blocks.append(
                        "**Trimiteri din textul de mai sus (verifică excepțiile):**"
                    )
                    for ref in useful:
                        label = ref["label"] or ref["raw"] or ref["target"]
                        blocks.append(f"- {label} — {ref['link']}")
                    blocks.append("")

        gaps = [
            family
            for family in plan["required_families"]
            if family not in evidence_families
        ]
        gaps += [f for f in plan["missing_families"] if f not in gaps]
        blocks.append("## bilanț pe familii")
        for family in plan["required_families"]:
            label = FAMILY_LABELS.get(family, family)
            if family in evidence_families:
                blocks.append(f"- {label}: dovezi găsite mai sus.")
            else:
                blocks.append(
                    f"- {label}: FĂRĂ dovezi. Caută din nou cu sinonime și "
                    f"`search(doc=...)` pe actele din familie; dacă tot nu "
                    "există text, spune explicit în răspuns că sursa nu "
                    "acoperă acest aspect."
                )
        blocks.append("")

        forms = self.actions(question, limit=3)
        # A second form search with the scenario vocabulary: form metadata
        # describes the obligation ("taxa colectata ... cod anulat"), not the
        # user's narrative, so the direct question can miss the right form.
        if expansions:
            seen_forms = {form["code"] for form in forms}
            for form in self.actions(" ".join(expansions), limit=3):
                if form["code"] not in seen_forms and len(forms) < 5:
                    forms.append(form)
                    seen_forms.add(form["code"])
        if forms:
            blocks.append("## Fișe de acțiune (declarații ANAF)")
            for form in forms:
                blocks.append(
                    f"- {form['code']} — {form['title'][:160]} — SURSA: {form['link']}"
                )
            blocks.append("")
        return "\n".join(blocks)

    # ----------------------------------------------------------------- actions
    def _table_exists(self, name: str) -> bool:
        return bool(
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
        )

    @staticmethod
    def _action_item(
        row: sqlite3.Row, score: float, link: str
    ) -> dict[str, Any]:
        return {
            "doc_slug": row["doc_slug"],
            "code": row["code"],
            "title": row["title"],
            "description": row["description"],
            "source_url": row["source_url"],
            "link": link,
            "xfa": bool(row["xfa"]),
            "score": score,
        }

    def _actions_fts_search(
        self, query: str, limit: int, make_link: Any
    ) -> list[dict[str, Any]]:
        """BM25 search over declaration metadata (no PDF text, no Docling)."""
        parts = _query_tokens(query)
        match = " OR ".join(_fts_term(stem) for _, stem in parts)
        if not match:
            return []
        sql = """
            SELECT a.doc_slug, a.code, a.title, a.description, a.source_url,
                   a.pdf_rel, a.xfa, a.simple_pdf, a.artifact,
                   bm25(actions_fts) AS score
            FROM actions_fts
            JOIN actions a ON a.rowid = actions_fts.rowid
            WHERE actions_fts MATCH ?
            ORDER BY score, a.code
            LIMIT ?
        """
        rows = self.connection.execute(sql, (match, limit)).fetchall()
        return [
            self._action_item(
                row,
                float(row["score"]),
                make_link(self.root, row["pdf_rel"], None, self.public_base_url),
            )
            for row in rows
        ]

    def _actions_manual(
        self, query: str, limit: int, make_link: Any
    ) -> list[dict[str, Any]]:
        """Fallback for indexes built before ``actions_fts`` existed."""
        rows = self.connection.execute(
            "SELECT doc_slug, code, title, description, source_url, pdf_rel, "
            "xfa, simple_pdf, artifact FROM actions"
        ).fetchall()
        tokens = [
            token for token in re.findall(r"[a-z0-9]{3,}", query.lower())
        ]
        matches: list[dict[str, Any]] = []
        for row in rows:
            code = str(row["code"])
            compact_code = re.sub(r"[^a-z0-9]", "", code.lower())
            haystack = (
                f"{code} d{code} declaratia-{code} {compact_code} "
                f"d{compact_code} declaratia-{compact_code} "
                f"{row['title']} {row['description']}"
            ).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score == 0:
                continue
            matches.append(
                self._action_item(
                    row,
                    float(score),
                    make_link(self.root, row["pdf_rel"], None, self.public_base_url),
                )
            )
        matches.sort(key=lambda item: (-item["score"], item["code"]))
        return matches[:limit]

    def actions(self, query: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """ANAF declaration sheets: what form to complete and upload in SPV.

        These are source documents, separate from the legal text. Searching a
        form code (e.g. ``D300``, ``D212``) or a keyword returns the formular
        PDF link that answers can cite in the ACȚIUNI section.
        """
        from .links import make_link

        limit = max(1, min(limit, 50))
        if not self._table_exists("actions"):
            # An index built before the actions table existed is still valid
            # for legal text; declarations simply are not available.
            return []

        if not query:
            rows = self.connection.execute(
                "SELECT doc_slug, code, title, description, source_url, pdf_rel, "
                "xfa, simple_pdf, artifact FROM actions ORDER BY code"
            ).fetchall()
            return [
                self._action_item(
                    row,
                    0.0,
                    make_link(self.root, row["pdf_rel"], None, self.public_base_url),
                )
                for row in rows[:limit]
            ]

        if self._table_exists("actions_fts"):
            try:
                return self._actions_fts_search(query, limit, make_link)
            except sqlite3.OperationalError:
                # Malformed match expression or an old FTS layout: fall back
                # to the manual scan instead of returning nothing.
                pass
        return self._actions_manual(query, limit, make_link)

    # ----------------------------------------------------------------- context
    def context(
        self, question: str, limit: int = 4, doc: str | None = None
    ) -> str:
        hits = self.search(question, doc=doc, limit=max(limit * 3, 8))
        no_text_hits = not hits

        # Keep per-family diversity: without this, a question whose best BM25
        # hits all live in one act (usually the code) would never surface the
        # implementing norms or the EU act. Reserve one slot per companion
        # family before filling the rest with the strongest hits.
        from .research import classify_family, topic_families

        _topics, required = topic_families(question) if doc is None else ([], [])
        picked: list[tuple[str, dict[str, Any]]] = []
        picked_families: set[str] = set()

        def article_target(hit: dict[str, Any]) -> str:
            row = self.connection.execute(
                "SELECT article_id FROM nodes WHERE id = ?", (hit["id"],)
            ).fetchone()
            return row["article_id"] if row and row["article_id"] else hit["id"]

        def pick(hit: dict[str, Any]) -> None:
            target = article_target(hit)
            if any(target == existing for existing, _ in picked):
                return
            picked.append((target, hit))
            picked_families.add(classify_family(hit["doc"], hit["doc_title"]))

        def family_of(hit: dict[str, Any]) -> str:
            return classify_family(hit["doc"], hit["doc_title"])

        for family in required:
            if family in picked_families or len(picked) >= limit:
                continue
            for hit in hits:
                if family_of(hit) == family:
                    pick(hit)
                    break
        for hit in hits:
            if len(picked) >= limit:
                break
            pick(hit)
        articles = [target for target, _ in picked]

        blocks: list[str] = [
            "# Evidențe din corpus",
            "",
            f"Întrebare: **{question}**",
            "",
            "Fiecare link deschide sursa indicată (HTML la ancora exactă sau "
            "PDF la pagina relevantă).",
            "",
        ]
        missing = [f for f in required if f not in picked_families]
        if missing:
            from .research import FAMILY_LABELS

            blocks.append(
                "**Familii obligatorii fără dovezi în acest pachet:** "
                + ", ".join(FAMILY_LABELS.get(f, f) for f in missing)
                + ". Caută separat cu `search(doc=...)`/`research` înainte de "
                "a concluziona; nu răspunde dintr-o singură familie."
            )
            blocks.append("")
        if no_text_hits:
            blocks.append(
                "Nu am găsit pasaje de lege pentru această întrebare. "
                "Verifică mai jos fișele de acțiune; altfel încearcă sinonime "
                "sau numele actului."
            )
            blocks.append("")
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

        # Declarations are source documents too: surface the formular PDFs that
        # matter for the "ACȚIUNI" part of the answer (SPV upload steps).
        forms = self.actions(question, limit=3)
        if forms:
            blocks.append("## Fise de actiune (declaratii ANAF)")
            for form in forms:
                blocks.append(
                    f"- {form['code']} — {form['title'][:160]} — SURSA: {form['link']}"
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
