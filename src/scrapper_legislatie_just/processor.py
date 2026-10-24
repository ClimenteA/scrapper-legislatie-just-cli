"""Step 4: build a queryable legal index from the expanded HTML files.

The index keeps the source HTML as the single source of truth and stores only
pointers to it (file path + anchor id). Every node therefore renders as a
clickable ``file://.../document.html#s_art_87`` link.

No embeddings are used: retrieval is SQLite FTS5 (BM25) plus a deterministic
reference graph extracted from the HTML anchors and the article text.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from unidecode import unidecode

from .config import resolve_data_dir, step_dir
from .logger import log
from .misc import extract_file_meta
from .sources import Sursa, surse_legislatie_just

# HTML classes used by legislatie.just.ro for the legal structure.
STRUCT_CLASSES: dict[str, str] = {
    "S_TTL": "titlu",
    "S_CAP": "capitol",
    "S_SEC": "sectiune",
    "S_ART": "articol",
    "S_ALN": "alineat",
    "S_PCT": "punct",
    "S_LIT": "litera",
    "S_PAR": "paragraf",
    "S_LIN": "linie",
    "S_NTA": "nota",
    "S_NTA_PAR": "nota",
    "S_ANX": "anexa",
}

# Classes whose prefix names the label child (e.g. S_ART -> S_ART_TTL).
TTL_CLASS = {
    "titlu": "S_TTL_TTL",
    "capitol": "S_CAP_TTL",
    "sectiune": "S_SEC_TTL",
    "articol": "S_ART_TTL",
    "alineat": "S_ALN_TTL",
    "punct": "S_PCT_TTL",
    "litera": "S_LIT_TTL",
    "linie": "S_LIN_TTL",
    "nota": "S_NTA_TTL",
    "anexa": "S_ANX_TTL",
}

DEN_CLASS = {
    "titlu": "S_TTL_DEN",
    "capitol": "S_CAP_DEN",
    "sectiune": "S_SEC_DEN",
    "anexa": "S_ANX_TTL",
}

# Text inside these tags belongs to a nested node, not to the current one.
BLOCK_TAGS = {
    "ul",
    "ol",
    "li",
    "p",
    "div",
    "section",
    "article",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}

SPACE_RE = re.compile(r"\s+")
ART_NO_RE = re.compile(r"(?:articolul|art\.?)\s*([0-9]+(?:\^[0-9]+)?)", re.IGNORECASE)
ANCHOR_RE = re.compile(r"^#(s_[A-Za-z0-9_]+)$")
ACT_RE = re.compile(
    r"\b(legea|legii|lege|legile|oug|ordonanta de urgenta|"
    r"ordonantei de urgenta|hotararea|hotararii|hg|ordinul|ordin)\s*"
    r"(?:nr\.?\s*)?(\d+)\s*(?:/|din)\s*(\d{4})",
    re.IGNORECASE,
)
CROSS_ART_RE = re.compile(
    r"(?:art\.?|articolul)\s*([0-9]+(?:\^[0-9]+)?)", re.IGNORECASE
)


def clean_text(value: str) -> str:
    return SPACE_RE.sub(" ", value.replace("\u00a0", " ")).strip()


def normalize(value: str) -> str:
    """Lowercase and fold diacritics so queries match the ASCII source text."""
    return SPACE_RE.sub(" ", re.sub(r"[^a-z0-9]+", " ", unidecode(value).lower())).strip()


def short_title(nume: str) -> str:
    """Compact human label used in breadcrumbs (keeps the act type)."""
    text = re.split(r"\s+-\s+|\(| PRIVIND | privind ", nume, maxsplit=1)[0]
    text = clean_text(text) or nume
    return text[:72]


def _own_text(element: Tag) -> str:
    """Text of the node itself, excluding nested legal nodes and label spans."""
    parts: list[str] = []
    for child in element.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        classes = child.get("class") or []
        if any(cls.endswith("_TTL") for cls in classes):
            continue
        if any(cls in STRUCT_CLASSES for cls in classes):
            continue
        if child.name in BLOCK_TAGS:
            continue
        parts.append(child.get_text(" ", strip=True))
    return clean_text(" ".join(parts))


def _has_class(element: Tag, name: str) -> bool:
    return name in (element.get("class") or [])


def _find_class(element: Tag, name: str) -> Tag | None:
    """Find a label/denomination child, preferring a direct child."""
    for child in element.find_all(True, recursive=False):
        if _has_class(child, name):
            return child
    return element.find(class_=name)


def _label_for(element: Tag, kind: str) -> str:
    ttl_class = TTL_CLASS.get(kind)
    label = ""
    if ttl_class:
        ttl = _find_class(element, ttl_class)
        if ttl is not None:
            label = clean_text(ttl.get_text(" ", strip=True))

    den_class = DEN_CLASS.get(kind)
    if den_class:
        den = _find_class(element, den_class)
        if den is not None:
            den_text = clean_text(den.get_text(" ", strip=True))
            if den_text and den_text != label:
                label = f"{label} — {den_text}" if label else den_text
    return label


def _article_number(label: str) -> str | None:
    match = ART_NO_RE.search(label)
    return match.group(1) if match else None


@dataclass
class RawNode:
    id: str
    anchor: str | None
    kind: str
    label: str
    text: str
    parent: str | None
    path: str
    depth: int
    order_idx: int
    number: str | None
    article_id: str | None
    doc_slug: str
    doc_version: str
    citations: list[dict] = field(default_factory=list)


@dataclass
class LegalIndexBuilder:
    """Build ``4-processed/law.db`` plus the OpenCode instructions."""

    surse: list[Sursa] = field(default_factory=lambda: list(surse_legislatie_just))
    data_dir: str | Path | None = None
    expanded_dir: str | Path | None = None
    processed_dir: str | Path | None = None
    force: bool = False
    limit: int | None = None

    def __post_init__(self) -> None:
        self.root = resolve_data_dir(self.data_dir)
        self.expanded_dir = (
            Path(self.expanded_dir).expanduser().resolve()
            if self.expanded_dir
            else step_dir(self.root, "expanded")
        )
        self.processed_dir = (
            Path(self.processed_dir).expanduser().resolve()
            if self.processed_dir
            else step_dir(self.root, "processed")
        )
        self.expanded_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.processed_dir / "law.db"
        self.graph_path = self.processed_dir / "graph.db"
        self.manifest_path = self.processed_dir / "manifest.json"

    # ------------------------------------------------------------------ build
    def build(self) -> dict:
        started = time.perf_counter()
        documents = self._discover_documents()
        if self.limit is not None:
            documents = documents[: self.limit]

        digest = self._fingerprint(documents)
        if not self.force and self._is_fresh(digest):
            log.debug("Processed index is up to date")
            return {
                "documents": len(documents),
                "nodes": 0,
                "refs": 0,
                "skipped": True,
                "seconds": round(time.perf_counter() - started, 2),
            }

        tmp_db = self.processed_dir / "law.db.tmp"
        if tmp_db.exists():
            tmp_db.unlink()

        connection = sqlite3.connect(tmp_db)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        self._create_schema(connection)

        all_docs: list[dict] = []
        all_nodes: list[RawNode] = []
        all_anchors: dict[tuple[str, str, str], str] = {}

        for doc in documents:
            if doc["html_path"] is None:
                continue
            nodes = self._parse_document(doc, all_anchors)
            all_nodes.extend(nodes)
            all_docs.append(doc)
            log.debug(f"Processed {doc['slug']} ({len(nodes)} nodes)")

        self._insert_docs(connection, all_docs)
        self._insert_nodes(connection, all_nodes)
        all_refs = self._build_refs(connection, all_nodes, all_docs)
        self._create_search_index(connection)

        connection.commit()
        connection.close()

        if self.db_path.exists():
            self.db_path.unlink()
        tmp_db.replace(self.db_path)

        from .graph import build_graph

        graph_result = build_graph(
            nodes=self._graph_nodes(all_nodes, all_docs),
            refs=all_refs,
            root=self.root,
            graph_path=self.graph_path,
            force=True,
        )
        refs = len(all_refs)

        elapsed = round(time.perf_counter() - started, 2)
        self._write_catalog(all_docs, len(all_nodes), refs, elapsed)
        self._write_manifest(digest, all_docs, len(all_nodes), refs, elapsed)
        self._write_readme(all_docs, len(all_nodes), refs, elapsed)
        self._write_instructions()

        return {
            "documents": len(all_docs),
            "nodes": len(all_nodes),
            "refs": refs,
            "graph_edges": graph_result["edges"],
            "skipped": False,
            "seconds": elapsed,
        }

    # -------------------------------------------------------------- discovery
    def _discover_documents(self) -> list[dict]:
        selected = {s.slug: s for s in self.surse}
        documents: list[dict] = []

        for slug_dir in sorted(self.expanded_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            slug = slug_dir.name
            if selected and slug not in selected:
                continue

            toc = self._load_toc(slug_dir, slug)
            consolidari = {
                item.get("html_name"): item for item in toc.get("consolidari", [])
            }
            versions: list[dict] = []

            for html_path in sorted(slug_dir.glob("*.html")):
                meta = extract_file_meta(html_path.name) or {}
                consolidation = consolidari.get(html_path.name, {})
                version = (
                    meta.get("data_consolidarii")
                    or consolidation.get("data_consolidarii")
                    or "unknown"
                )
                md_path = html_path.with_suffix(".md")
                versions.append(
                    {
                        "slug": slug,
                        "version": str(version),
                        "title": toc.get("nume", slug),
                        "description": toc.get("descriere", ""),
                        "html_path": html_path,
                        "html_rel": self._relative_to_root(html_path),
                        "md_rel": (
                            self._relative_to_root(md_path)
                            if md_path.exists()
                            else None
                        ),
                        "source_url": consolidation.get("link")
                        or (
                            f"https://legislatie.just.ro/Public/DetaliiDocument/"
                            f"{meta.get('numar_link')}"
                            if meta.get("numar_link")
                            else None
                        ),
                        "numar_link": meta.get("numar_link"),
                        "date": version,
                        "is_latest": False,
                        "content_hash": self._sha256(html_path),
                    }
                )

            if not versions:
                continue
            latest = max(versions, key=lambda item: item["version"])
            for item in versions:
                item["is_latest"] = item is latest
            documents.extend(sorted(versions, key=lambda item: item["version"]))
        return documents

    def _relative_to_root(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def _load_toc(self, slug_dir: Path, slug: str) -> dict:
        toc_path = slug_dir / f"toc-{slug}.json"
        if not toc_path.exists():
            return {}
        try:
            return json.loads(toc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            log.warning(f"Cannot read {toc_path}: {ex}")
            return {}

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _fingerprint(self, documents: list[dict]) -> str:
        payload = "\n".join(
            f"{doc['html_rel']}:{doc['content_hash']}" for doc in documents
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _is_fresh(self, digest: str) -> bool:
        if (
            not self.db_path.exists()
            or not self.graph_path.exists()
            or not self.manifest_path.exists()
        ):
            return False
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return manifest.get("source_digest") == digest

    # ------------------------------------------------------------------ parse
    def _parse_document(
        self, doc: dict, anchors: dict[tuple[str, str, str], str]
    ) -> list[RawNode]:
        html = doc["html_path"].read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(html, "lxml")
        body = soup.body or soup
        slug, version = doc["slug"], doc["version"]
        base = f"{slug}@{version}"
        title = short_title(doc["title"])

        nodes: list[RawNode] = [
            RawNode(
                id=f"{base}#root",
                anchor=None,
                kind="document",
                label=title,
                text=clean_text(doc.get("description") or ""),
                parent=None,
                path=title,
                depth=0,
                order_idx=0,
                number=None,
                article_id=None,
                doc_slug=slug,
                doc_version=version,
            )
        ]
        captured: dict[int, RawNode] = {}
        seen_ids: set[str] = {nodes[0].id}
        synthetic = 0
        order = 0

        for element in body.descendants:
            if not isinstance(element, Tag):
                continue
            kind = next(
                (STRUCT_CLASSES[name] for name in (element.get("class") or []) if name in STRUCT_CLASSES),
                None,
            )
            if kind is None:
                continue

            label = _label_for(element, kind)
            text = _own_text(element)
            if kind == "paragraf" and len(text) < 3:
                continue
            if kind in {"titlu", "capitol", "sectiune"}:
                text = ""

            parent = self._nearest_captured(element, captured) or nodes[0]
            real_anchor = element.get("id")
            anchor = real_anchor
            if anchor:
                node_id = f"{base}#{anchor}"
            else:
                # Synthetic nodes (no id in the HTML) link to the closest
                # ancestor that has a real anchor, so a click still lands
                # inside the right article instead of at the file top.
                anchor = parent.anchor
                synthetic += 1
                node_id = f"{base}#syn-{kind}-{synthetic}"
            if node_id in seen_ids:
                continue
            seen_ids.add(node_id)

            order += 1
            path = f"{parent.path} > {label}" if label else parent.path
            node = RawNode(
                id=node_id,
                anchor=anchor,
                kind=kind,
                label=label,
                text=text,
                parent=parent.id,
                path=path,
                depth=parent.depth + 1,
                order_idx=order,
                number=_article_number(label) if kind == "articol" else None,
                article_id=(
                    node_id if kind == "articol" else parent.article_id
                ),
                doc_slug=slug,
                doc_version=version,
            )
            nodes.append(node)
            captured[id(element)] = node
            if real_anchor:
                anchors[(slug, version, real_anchor)] = node_id

        # Intra-document citations: every <a href="#s_..."> becomes an edge later.
        for link in body.find_all("a", href=True):
            match = ANCHOR_RE.match(link.get("href", ""))
            if not match:
                continue
            source = self._nearest_captured(link, captured)
            if source is None:
                continue
            target_id = anchors.get((slug, version, match.group(1)))
            if not target_id:
                continue
            source.citations.append(
                {
                    "dst": target_id,
                    "raw": clean_text(link.get_text(" ", strip=True)),
                    "rel": "citat",
                    "confidence": 1.0,
                }
            )

        log.debug(f"Parsed {doc['html_rel']}: {len(nodes)} nodes")
        return nodes

    @staticmethod
    def _nearest_captured(element: Tag, captured: dict[int, RawNode]) -> RawNode | None:
        for parent in element.parents:
            node = captured.get(id(parent))
            if node is not None:
                return node
        return None

    # ------------------------------------------------------------------- store
    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE docs(
                slug TEXT NOT NULL,
                version TEXT NOT NULL,
                title TEXT,
                description TEXT,
                html_rel TEXT NOT NULL,
                md_rel TEXT,
                source_url TEXT,
                numar_link TEXT,
                consolidation_date TEXT,
                is_latest INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT,
                processed_at TEXT,
                PRIMARY KEY(slug, version)
            );

            CREATE TABLE nodes(
                id TEXT PRIMARY KEY,
                doc_slug TEXT NOT NULL,
                doc_version TEXT NOT NULL,
                kind TEXT NOT NULL,
                label TEXT,
                number TEXT,
                text TEXT,
                path TEXT,
                parent TEXT,
                anchor TEXT,
                article_id TEXT,
                depth INTEGER,
                order_idx INTEGER
            );

            CREATE INDEX idx_nodes_parent ON nodes(parent);
            CREATE INDEX idx_nodes_doc ON nodes(doc_slug, doc_version);
            CREATE INDEX idx_nodes_kind ON nodes(kind);
            CREATE INDEX idx_nodes_article ON nodes(article_id);
            """
        )

    @staticmethod
    def _insert_docs(connection: sqlite3.Connection, docs: list[dict]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.executemany(
            """
            INSERT OR REPLACE INTO docs(
                slug, version, title, description, html_rel, md_rel, source_url,
                numar_link, consolidation_date, is_latest, content_hash, processed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    doc["slug"],
                    doc["version"],
                    doc["title"],
                    doc["description"],
                    doc["html_rel"],
                    doc["md_rel"],
                    doc["source_url"],
                    doc["numar_link"],
                    doc["date"],
                    int(doc["is_latest"]),
                    doc["content_hash"],
                    now,
                )
                for doc in docs
            ],
        )

    @staticmethod
    def _insert_nodes(connection: sqlite3.Connection, nodes: list[RawNode]) -> None:
        rows = [
            (
                node.id,
                node.doc_slug,
                node.doc_version,
                node.kind,
                node.label,
                node.number,
                node.text,
                node.path,
                node.parent,
                node.anchor,
                node.article_id,
                node.depth,
                node.order_idx,
            )
            for node in nodes
        ]
        connection.executemany(
            """
            INSERT INTO nodes(
                id, doc_slug, doc_version, kind, label, number, text, path,
                parent, anchor, article_id, depth, order_idx
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    @staticmethod
    def _graph_nodes(
        nodes: list[RawNode], docs: list[dict]
    ) -> list[dict]:
        """Flatten nodes for the GraphQLite export (one shared graph store)."""
        by_doc = {(doc["slug"], doc["version"]): doc for doc in docs}
        rows: list[dict] = []
        for node in nodes:
            doc = by_doc.get((node.doc_slug, node.doc_version), {})
            rows.append(
                {
                    "id": node.id,
                    "doc_slug": node.doc_slug,
                    "doc_version": node.doc_version,
                    "title": doc.get("title") or node.doc_slug,
                    "kind": node.kind,
                    "label": node.label,
                    "number": node.number,
                    "text": node.text,
                    "path": node.path,
                    "anchor": node.anchor,
                    "article_id": node.article_id,
                    "html_rel": doc.get("html_rel") or "",
                }
            )
        return rows

    def _create_search_index(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE VIRTUAL TABLE nodes_fts USING fts5(
                node_id UNINDEXED,
                label,
                path,
                text,
                tokenize = 'unicode61 remove_diacritics 2'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO nodes_fts(rowid, node_id, label, path, text)
            SELECT rowid, id, label, path, text FROM nodes
            """
        )

    # -------------------------------------------------------------------- refs
    def _build_refs(
        self,
        connection: sqlite3.Connection,
        nodes: list[RawNode],
        docs: list[dict],
    ) -> list[dict]:
        rows: list[dict] = []
        for node in nodes:
            for citation in node.citations:
                rows.append(
                    {
                        "src": node.id,
                        "dst": citation["dst"],
                        "src_doc": node.doc_slug,
                        "dst_doc": None,
                        "rel": citation["rel"],
                        "raw": citation["raw"],
                        "confidence": citation["confidence"],
                    }
                )

        rows.extend(self._cross_document_refs(connection, nodes, docs))
        return rows

    def _cross_document_refs(
        self,
        connection: sqlite3.Connection,
        nodes: list[RawNode],
        docs: list[dict],
    ) -> list[dict]:
        aliases: dict[str, tuple[str, str, float]] = {}
        root_by_doc: dict[tuple[str, str], str] = {}
        for doc in docs:
            for alias, value in self._aliases_for(doc).items():
                current = aliases.get(alias)
                if current is None or value[2] > current[2]:
                    aliases[alias] = value
            root_by_doc[(doc["slug"], doc["version"])] = (
                f"{doc['slug']}@{doc['version']}#root"
            )

        if not aliases:
            return []

        pattern = re.compile(
            "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
        )
        rows: list[dict] = []

        for node in nodes:
            if not node.text or len(node.text) < 12:
                continue
            haystack = normalize(node.text)
            matches = list(pattern.finditer(haystack))
            if not matches:
                continue

            emitted = 0
            for match in matches:
                target = aliases.get(match.group(0))
                if target is None:
                    continue
                dst_doc, dst_slug, confidence = target

                window = haystack[max(0, match.start() - 140) : match.end() + 140]
                dst = self._resolve_cross_article(
                    connection, dst_doc, window, root_by_doc
                )
                rows.append(
                    {
                        "src": node.id,
                        "dst": dst,
                        "src_doc": node.doc_slug,
                        "dst_doc": dst_slug,
                        "rel": "mentioneaza",
                        "raw": clean_text(match.group(0)),
                        "confidence": confidence,
                    }
                )
                emitted += 1
                if emitted >= 4:
                    break

            for act in ACT_RE.finditer(node.text):
                raw = clean_text(act.group(0))
                if aliases.get(normalize(raw)):
                    continue
                rows.append(
                    {
                        "src": node.id,
                        "dst": None,
                        "src_doc": node.doc_slug,
                        "dst_doc": None,
                        "rel": "act_extern",
                        "raw": raw,
                        "confidence": 0.5,
                    }
                )
                if emitted >= 7:
                    break
                emitted += 1
        return rows

    @staticmethod
    def _aliases_for(doc: dict) -> dict[str, tuple[str, str, float]]:
        name = normalize(doc["title"])
        candidates: dict[str, float] = {}

        if name:
            candidates[name] = 0.6
            candidates[re.sub(r"\s*\(.*?\)\s*", " ", name).strip()] = 0.6

        act = ACT_RE.search(doc["title"])
        if act:
            kind, number, year = act.group(1).lower(), act.group(2), act.group(3)
            variants = {
                "legea": ("legea", "legii", "lege"),
                "legii": ("legii", "legea", "lege"),
                "lege": ("lege", "legii", "legea"),
                "legile": ("legile", "legii", "legea"),
                "hotararea": ("hotararea", "hotararii"),
                "hotararii": ("hotararii", "hotararea"),
                "ordinul": ("ordinul", "ordinului"),
                "ordin": ("ordin", "ordinului"),
            }.get(kind, (kind,))
            for variant in variants:
                candidates[f"{variant} nr {number} {year}"] = 0.9
                candidates[f"{variant} {number} {year}"] = 0.85
                candidates[f"{variant} nr {number}/{year}"] = 0.9
                candidates[f"{variant} {number}/{year}"] = 0.85

        slug_words = normalize(doc["slug"].replace("-", " "))
        if len(slug_words) >= 12:
            candidates[slug_words] = 0.4

        return {
            alias: (doc["slug"], doc["slug"], confidence)
            for alias, confidence in candidates.items()
            if len(alias) >= 6
        }

    @staticmethod
    def _resolve_cross_article(
        connection: sqlite3.Connection,
        dst_doc: str,
        window: str,
        root_by_doc: dict[tuple[str, str], str],
    ) -> str | None:
        match = CROSS_ART_RE.search(window)
        if not match:
            return None
        number = match.group(1)
        row = connection.execute(
            """
            SELECT n.id FROM nodes n
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            WHERE n.doc_slug = ? AND n.kind = 'articol' AND n.number = ?
            ORDER BY d.is_latest DESC
            LIMIT 1
            """,
            (dst_doc, number),
        ).fetchone()
        if row:
            return row[0]

        row = connection.execute(
            """
            SELECT n.id FROM nodes n
            JOIN docs d ON d.slug = n.doc_slug AND d.version = n.doc_version
            WHERE n.doc_slug = ? AND n.kind = 'document'
            ORDER BY d.is_latest DESC, d.version DESC
            LIMIT 1
            """,
            (dst_doc,),
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ output
    def _write_catalog(
        self, docs: list[dict], nodes: int, refs: int, seconds: float
    ) -> None:
        catalog = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "documents": len(docs),
            "nodes": nodes,
            "refs": refs,
            "build_seconds": seconds,
            "items": [
                {
                    "slug": doc["slug"],
                    "title": doc["title"],
                    "version": doc["version"],
                    "date": doc["date"],
                    "is_latest": doc["is_latest"],
                    "html": doc["html_rel"],
                    "source_url": doc["source_url"],
                }
                for doc in docs
            ],
        }
        (self.processed_dir / "catalog.json").write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _write_manifest(
        self,
        digest: str,
        docs: list[dict],
        nodes: int,
        refs: int,
        seconds: float,
    ) -> None:
        manifest = {
            "source_digest": digest,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "documents": len(docs),
            "nodes": nodes,
            "refs": refs,
            "build_seconds": seconds,
            "files": {doc["html_rel"]: doc["content_hash"] for doc in docs},
        }
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def _write_instructions(self) -> None:
        template_dir = Path(__file__).with_name("templates")
        for name in ("AGENTS.md", "answer-format.md"):
            source = template_dir / name
            if not source.exists():
                continue
            shutil.copy2(source, self.processed_dir / name)

    def _write_readme(
        self, docs: list[dict], nodes: int, refs: int, seconds: float
    ) -> None:
        lines = [
            "# 4-processed",
            "",
            "Index de căutare peste legislația descărcată în `3-expanded`.",
            "Fiecare rezultat trimite la HTML-ul original, la ancora exactă a",
            "articolului/alineatului, deci linkul se deschide direct la sursă.",
            "",
            f"- documente: **{len(docs)}**",
            f"- noduri (articole, alineate, litere, note): **{nodes}**",
            f"- referințe (citate + trimiteri între acte): **{refs}**",
            "- `law.db` — text + index FTS5 (căutare)",
            "- `graph.db` — graful de citări (GraphQLite, interogabil cu Cypher)",
            f"- rebuild complet: **{seconds}s**",
            "",
            "## Utilizare",
            "",
            "```bash",
            "# căutare cu snippet și link către HTML",
            "uv run scrapper-legislatie-just-cli search \"cod caen software\"",
            "",
            "# un articol întreg, cu toate alineatele",
            "uv run scrapper-legislatie-just-cli article codul-fiscal 60",
            "",
            "# context gata de citit pentru un răspuns",
            "uv run scrapper-legislatie-just-cli context \"ce taxe plateste un PFA\"",
            "",
            "# graful de citări (GraphQLite/Cypher)",
            "uv run scrapper-legislatie-just-cli cypher \\",
            "  \"MATCH (a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol) RETURN a.label, b.label LIMIT 5\"",
            "```",
            "",
            "În OpenCode, serverul MCP `legislatie` expune aceleași operații",
            "ca tool-uri. Formatul de răspuns este în `AGENTS.md` și",
            "`answer-format.md`.",
            "",
            "## Documente indexate",
            "",
            "| Act | Versiune | HTML |",
            "|---|---|---|",
        ]
        for doc in sorted(docs, key=lambda item: item["slug"]):
            uri = (self.root / doc["html_rel"]).resolve().as_uri()
            lines.append(
                f"| {doc['title'][:80]} | {doc['version']} | [deschide]({uri}) |"
            )
        (self.processed_dir / "README.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
