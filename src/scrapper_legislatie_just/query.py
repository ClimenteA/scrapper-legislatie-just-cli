"""Read-only query layer over the persisted BM25 index.

Every answer points at a source by **act name + slug + consolidation version**.
There are no HTML anchors any more: ``3-expanded`` holds Markdown, so a
citation is the act identity, not a fragment. ANAF form PDFs keep their public
``/pdfs/...`` link.

No embeddings and no language model: the retriever is a persisted
:class:`~llama_index.retrievers.bm25.BM25Retriever` built by
:mod:`bm25_index`.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .config import resolve_data_dir, step_dir
from .text import query_text, stem_tokens, stemmed_query_text, tokens

SPACE_RE = re.compile(r"\s+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
ARTICLE_RE = re.compile(r"Articolul\s+(\d+(?:\^\d+)?)", re.IGNORECASE)
PDF_MARKER = "5-pdfs/"

# A persisted BM25 retriever is read-only, so it can be shared by every tool
# call in the process. Reload only when a rebuild replaced the index.
_RETRIEVER_CACHE: dict[str, tuple[float, Any]] = {}
_CORPUS_TEXT_CACHE: dict[str, tuple[float, list[str]]] = {}
_DF_CACHE: dict[tuple[str, str], int] = {}


def _index_stamp(index_dir: Path) -> float:
    try:
        return (index_dir / "retriever.json").stat().st_mtime
    except OSError:
        return 0.0


def _corpus_texts(index_dir: Path, retriever: Any) -> list[str]:
    """Normalized *original* text of every indexed node, cached per build."""
    key = str(index_dir)
    stamp = _index_stamp(index_dir)
    cached = _CORPUS_TEXT_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    texts = [
        normalize(str(record.get("original_text") or ""))
        for record in retriever.corpus
    ]
    _CORPUS_TEXT_CACHE[key] = (stamp, texts)
    return texts


def _term_idfs(
    query: str, index_dir: Path, corpus_texts: list[str]
) -> list[tuple[str, str, float]]:
    """``(token, stem, idf)`` for the query words actually present in the corpus.

    The IDF and the exact-token check are computed over the folded *original*
    text, while BM25 itself runs over the stemmed index. Keeping both makes
    short, generic questions (e.g. "scutire impozit venit") resolve to the
    article whose heading or lead actually names the concept.
    """
    source_tokens = tokens(query, dedupe=True)
    stems = stem_tokens(source_tokens)
    total = len(corpus_texts)
    terms: list[tuple[str, str, float]] = []
    for token, stem in zip(source_tokens, stems):
        cache_key = (str(index_dir), f"{token}:{stem}")
        df = _DF_CACHE.get(cache_key)
        if df is None:
            pattern = re.compile(rf"(?<![a-z0-9]){re.escape(token)}")
            df = sum(
                1 for text in corpus_texts if stem in text or pattern.search(text)
            )
            _DF_CACHE[cache_key] = df
        if df:
            terms.append((token, stem, math.log(1.0 + total / df)))
    return terms


def _rerank(
    hits: list[Any],
    query: str,
    index_dir: Path,
    corpus_texts: list[str],
) -> list[tuple[float, Any]]:
    """Deterministic re-scoring of BM25 candidates; no model involved."""
    terms = _term_idfs(query, index_dir, corpus_texts)
    if not terms:
        return []
    ranked: list[tuple[float, Any]] = []
    best_bm25 = max((float(getattr(h, "score", 0.0) or 0.0) for h in hits), default=0.0)
    for hit in hits:
        bm25 = float(getattr(hit, "score", 0.0) or 0.0)
        if bm25 <= 0:
            continue
        metadata = dict(hit.node.metadata or {})
        section = normalize(str(metadata.get("section") or ""))
        body = str(metadata.get("original_text") or hit.node.get_content())
        body = normalize(body)
        lead = body[:300]
        haystack = " ".join((section, lead, body))
        score = 0.0
        matched = 0
        exact = 0
        positions: list[int] = []
        for token, stem, idf in terms:
            position = haystack.find(token)
            if position >= 0:
                exact += 1
                matched += 1
                boost = 1.8 if token in section or token in lead else 1.0
                score += idf * boost
            elif stem in haystack:
                position = haystack.find(stem)
                matched += 1
                boost = 1.3 if stem in section or stem in lead else 1.0
                score += idf * 0.5 * boost
            if position >= 0:
                positions.append(position)
        if not matched:
            continue
        if exact == len(terms):
            score *= 1.4
        if len(positions) > 1:
            span = max(positions) - min(positions)
            score += (
                sum(idf for _token, _stem, idf in terms)
                * 0.5
                * max(0.0, 1.0 - span / 600.0)
            )
        # BM25 is a useful tie-breaker but must not drown the exact-term signal.
        score += 2.0 * bm25 / best_bm25 if best_bm25 else 0.0
        ranked.append((score, hit))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def _load_retriever(index_dir: Path) -> Any:
    marker = index_dir / "retriever.json"
    try:
        stamp = marker.stat().st_mtime
    except OSError:
        stamp = 0.0
    key = str(index_dir)
    cached = _RETRIEVER_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    from llama_index.retrievers.bm25 import BM25Retriever

    retriever = BM25Retriever.from_persist_dir(str(index_dir))
    _RETRIEVER_CACHE[key] = (stamp, retriever)
    return retriever


class NotFoundError(KeyError):
    """A requested article or node is absent from the index.

    This is an anticipated lookup miss (typically a bad identifier supplied by
    an MCP client), not a bug. It subclasses :class:`KeyError` so the CLI's
    existing ``except KeyError`` handling keeps working, but ``str()`` returns
    the plain message instead of KeyError's quoted repr.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "Not found"


def normalize(value: str) -> str:
    return SPACE_RE.sub(" ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def leading_int(value: str) -> int:
    match = re.match(r"\d+", str(value))
    return int(match.group()) if match else 0


class LegalIndex:
    def __init__(
        self,
        data_dir: str | Path | None = None,
        processed_dir: str | Path | None = None,
        public_base_url: str | None = None,
    ) -> None:
        self.root = resolve_data_dir(data_dir)
        self.public_base_url = public_base_url
        self.processed_dir = (
            Path(processed_dir).expanduser().resolve()
            if processed_dir
            else step_dir(self.root, "processed")
        )
        self.index_dir = self.processed_dir / "bm25"
        self.docs_path = self.processed_dir / "docs.json"
        self.actions_path = self.processed_dir / "actions.json"
        self.manifest_path = self.processed_dir / "manifest.json"

        if not self.docs_path.exists() or not self.index_dir.exists():
            raise FileNotFoundError(
                f"Index BM25 not found in {self.processed_dir}. Run the indexer "
                "with REINDEX_ON_START=1 first (or wait for the scheduled rebuild)."
            )

        self.documents: list[dict[str, Any]] = json.loads(
            self.docs_path.read_text(encoding="utf-8")
        ).get("items", [])
        self.actions_data: list[dict[str, Any]] = []
        if self.actions_path.exists():
            self.actions_data = json.loads(
                self.actions_path.read_text(encoding="utf-8")
            ).get("items", [])
        self._retriever: Any | None = None
        self._by_slug: dict[str, list[dict[str, Any]]] = {}
        for doc in self.documents:
            self._by_slug.setdefault(doc["slug"], []).append(doc)
        for versions in self._by_slug.values():
            versions.sort(key=lambda item: item["version"], reverse=True)

    def close(self) -> None:
        """Kept for API compatibility; the retriever holds no open handle."""

    def __enter__(self) -> LegalIndex:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ----------------------------------------------------------------- helpers
    def _ensure_retriever(self) -> Any:
        if self._retriever is None:
            self._retriever = _load_retriever(self.index_dir)
        return self._retriever

    def _pdf_link(self, pdf_rel: str | None) -> str | None:
        if not pdf_rel:
            return None
        rel = pdf_rel.replace("\\", "/").lstrip("/")
        if rel.startswith(PDF_MARKER):
            rel = rel[len(PDF_MARKER) :]
        if not self.public_base_url:
            return (self.root / PDF_MARKER / rel).resolve().as_uri()
        root = self.public_base_url.rstrip("/")
        if root.endswith("/laws"):
            root = root[: -len("/laws")]
        return f"{root}/pdfs/{rel}"

    def source_line(self, doc: dict[str, Any], section: str | None = None) -> str:
        """Human citation: act name, slug and consolidation version."""
        title = doc.get("title") or doc["slug"]
        line = (
            f"SURSA: {title} — `{doc['slug']}` — versiunea {doc['version']}"
        )
        if section:
            line += f" — {section}"
        if doc.get("source_url"):
            line += f" — {doc['source_url']}"
        pdf = self._pdf_link(doc.get("source_pdf"))
        if pdf:
            line += f" — PDF: {pdf}"
        return line

    def _resolve_doc(self, slug: str, version: str | None = None) -> dict[str, Any]:
        versions = self._by_slug.get(slug)
        if not versions:
            raise NotFoundError(
                f"Actul '{slug}' nu exista in index. Foloseste docs/select_docs "
                "pentru slug-uri valide."
            )
        if version:
            for doc in versions:
                if doc["version"] == version:
                    return doc
            raise NotFoundError(
                f"Versiunea {version} nu exista pentru '{slug}'. "
                f"Versiuni: {', '.join(doc['version'] for doc in versions[:6])}."
            )
        for doc in versions:
            if doc.get("is_latest"):
                return doc
        return versions[0]

    def _doc_meta_for_node(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "slug": metadata.get("slug"),
            "version": metadata.get("version"),
            "title": metadata.get("title"),
            "description": metadata.get("description", ""),
            "md_rel": metadata.get("md_rel"),
            "source_url": metadata.get("source_url") or None,
            "date": metadata.get("date"),
            "is_latest": bool(metadata.get("is_latest")),
        }

    @staticmethod
    def _snippet(text: str, query: str, width: int = 700) -> str:
        folded = query_text(query)
        words = folded.split()
        lowered = text.lower()
        position = -1
        for word in words:
            position = lowered.find(word)
            if position >= 0:
                break
        if position < 0:
            return text[:width]
        start = max(0, position - width // 3)
        end = min(len(text), start + width)
        prefix = "…" if start else ""
        suffix = "…" if end < len(text) else ""
        return prefix + text[start:end].strip() + suffix

    # ------------------------------------------------------------------ search
    def search(
        self,
        query: str,
        doc: str | None = None,
        kind: str | None = None,
        limit: int = 8,
        latest_only: bool = True,
    ) -> list[dict[str, Any]]:
        folded = stemmed_query_text(query)
        if not folded or limit <= 0:
            return []
        retriever = self._ensure_retriever()
        corpus_size = len(getattr(retriever, "corpus", []) or [])
        if not corpus_size:
            return []
        # BM25 only generates candidates. We take every node with a positive
        # score, then re-rank deterministically before applying the doc/kind
        # filters — otherwise a doc filter applied to the first 32 hits would
        # discard the very article the user asked for.
        retriever.similarity_top_k = corpus_size
        try:
            hits = retriever.retrieve(folded)
        except (ValueError, IndexError):
            return []
        corpus_texts = _corpus_texts(self.index_dir, retriever)
        ranked = _rerank(hits, query, self.index_dir, corpus_texts)

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for score, hit in ranked:
            node = hit.node
            metadata = dict(node.metadata or {})
            slug = metadata.get("slug")
            if not slug:
                continue
            if doc is not None and slug != doc:
                continue
            if latest_only and not metadata.get("is_latest"):
                continue
            node_kind = metadata.get("kind") or "sectiune"
            if kind is not None and node_kind != kind:
                continue

            article_number = metadata.get("article_number") or None
            section = metadata.get("section") or ""
            key = f"{slug}@{metadata.get('version')}#{article_number or section}"
            if key in seen:
                continue
            seen.add(key)

            doc_meta = self._doc_meta_for_node(metadata)
            text = str(metadata.get("original_text") or node.get_content())
            source = self.source_line(doc_meta, section or None)
            results.append(
                {
                    "id": node.node_id,
                    "kind": node_kind,
                    "label": section or doc_meta["title"],
                    "number": article_number,
                    "text": text,
                    "snippet": self._snippet(text, query),
                    "path": f"{doc_meta['title']} > {section}"
                    if section
                    else doc_meta["title"],
                    "doc": slug,
                    "doc_title": doc_meta["title"],
                    "version": doc_meta["version"],
                    "date": doc_meta["date"],
                    "source_url": doc_meta["source_url"],
                    "md_rel": doc_meta.get("md_rel"),
                    "source": source,
                    "score": round(score, 3),
                    "article_number": article_number,
                    "article_label": section if article_number else None,
                    "article_id": (
                        f"{slug}@{doc_meta['version']}#art-{article_number}"
                        if article_number
                        else node.node_id
                    ),
                }
            )
            if len(results) >= limit:
                break
        return results

    # -------------------------------------------------------------- documents
    def _read_doc(self, doc: dict[str, Any]) -> str:
        path = self.root / doc["md_rel"]
        if not path.exists():
            raise NotFoundError(
                f"Fisierul Markdown pentru '{doc['slug']}' lipseste: {path}."
            )
        return path.read_text(encoding="utf-8", errors="ignore")

    @staticmethod
    def _headings(text: str) -> list[re.Match[str]]:
        return list(HEADING_RE.finditer(text))

    def _article_numbers(self, doc: dict[str, Any]) -> list[str]:
        text = self._read_doc(doc)
        numbers: list[str] = []
        for heading in self._headings(text):
            match = ARTICLE_RE.search(heading.group(2))
            if match:
                numbers.append(match.group(1))
        return numbers

    def suggest_articles(
        self, doc: str, number: str, version: str | None = None, limit: int = 5
    ) -> list[str]:
        try:
            meta = self._resolve_doc(doc, version)
            numbers = self._article_numbers(meta)
        except (NotFoundError, OSError):
            return []
        target = leading_int(str(number))
        return sorted(
            set(numbers), key=lambda value: (abs(leading_int(value) - target), value)
        )[:limit]

    @staticmethod
    def _section_at(
        text: str, start: int, level: int, headings: list[re.Match[str]]
    ) -> str:
        end = len(text)
        for heading in headings:
            if heading.start() <= start:
                continue
            if len(heading.group(1)) <= level:
                end = heading.start()
                break
        return text[start:end].strip()

    def _extract_article(self, text: str, number: str) -> str | None:
        target = str(number).strip()
        headings = self._headings(text)
        for heading in headings:
            match = ARTICLE_RE.search(heading.group(2))
            if match and match.group(1) == target:
                return self._section_at(
                    text, heading.start(), len(heading.group(1)), headings
                )
        return None

    def _fallback_window(self, text: str, number: str) -> str | None:
        pattern = re.compile(
            rf"(?<![0-9]){re.escape(str(number))}(?![0-9])"
        )
        match = pattern.search(text)
        if not match:
            return None
        lines = text[: match.start()].count("\n")
        all_lines = text.splitlines()
        start = max(0, lines - 2)
        end = min(len(all_lines), lines + 4)
        return "\n".join(all_lines[start:end]).strip()

    def article(
        self, doc: str, number: str, version: str | None = None
    ) -> dict[str, Any]:
        meta = self._resolve_doc(doc, version)
        text = self._read_doc(meta)
        section = self._extract_article(text, str(number))
        if section is None:
            section = self._fallback_window(text, str(number))
        if section is None:
            near = self.suggest_articles(doc, str(number), version)
            hint = (
                f" Articole disponibile in apropiere: {', '.join(near)}." if near else ""
            )
            raise NotFoundError(
                f"Articolul {number} nu exista in {doc}.{hint} "
                "Foloseste search sau docs pentru valori valide."
            )
        label = f"Articolul {number}"
        return {
            "id": f"{doc}@{meta['version']}#art-{number}",
            "kind": "articol",
            "label": label,
            "path": f"{meta['title']} > {label}",
            "doc": doc,
            "doc_title": meta["title"],
            "version": meta["version"],
            "date": meta["date"],
            "source": self.source_line(meta, label),
            "source_url": meta.get("source_url"),
            "text": section,
            "ancestors": [],
            "truncated": False,
        }

    def _section_by_index(self, text: str, index: int) -> str | None:
        headings = self._headings(text)
        if not headings:
            return text if index == 0 else None
        if index >= len(headings):
            return None
        heading = headings[index]
        return self._section_at(
            text, heading.start(), len(heading.group(1)), headings
        )

    def _node_from_corpus(self, node_id: str) -> dict[str, Any] | None:
        retriever = self._ensure_retriever()
        for entry in getattr(retriever, "corpus", []):
            if entry.get("node_id") != node_id:
                continue
            metadata = entry
            # bm25s stores the serialized node under ``_node_content``.
            content = entry.get("_node_content")
            if content:
                try:
                    from llama_index.core.vector_stores.utils import (
                        metadata_dict_to_node,
                    )

                    node = metadata_dict_to_node(json.loads(content))
                    metadata = dict(node.metadata or {})
                    text = str(metadata.get("original_text") or node.get_content())
                except (ValueError, TypeError):
                    text = str(entry.get("original_text") or "")
            else:
                text = str(entry.get("original_text") or "")
            doc_meta = self._doc_meta_for_node(metadata)
            section = metadata.get("section") or ""
            return {
                "id": node_id,
                "kind": metadata.get("kind") or "sectiune",
                "label": section or doc_meta["title"],
                "path": f"{doc_meta['title']} > {section}"
                if section
                else doc_meta["title"],
                "doc": doc_meta["slug"],
                "doc_title": doc_meta["title"],
                "version": doc_meta["version"],
                "date": doc_meta["date"],
                "source": self.source_line(doc_meta, section or None),
                "source_url": doc_meta["source_url"],
                "text": text,
                "ancestors": [],
                "truncated": False,
            }
        return None

    def show(self, node_id: str) -> dict[str, Any]:
        """Show a node id returned by ``search`` (``slug@version#...``)."""
        if "@" not in node_id or "#" not in node_id:
            raise NotFoundError(
                f"ID invalid: {node_id}. Foloseste un id din rezultatele search, "
                "de forma 'slug@YYYY-MM-DD#art-N'."
            )
        base, _, suffix = node_id.partition("#")
        slug, _, version = base.partition("@")
        meta = self._resolve_doc(slug, version or None)
        article_match = re.match(r"art-(\d+(?:\^\d+)?)", suffix)
        if article_match:
            return self.article(slug, article_match.group(1), version or None)

        found = self._node_from_corpus(node_id)
        if found is not None:
            return found

        text = self._read_doc(meta)
        index = 0
        match = re.match(r"sec-(\d+)", suffix)
        if match:
            index = int(match.group(1))
        section = self._section_by_index(text, index)
        if section is None:
            raise NotFoundError(
                f"Nodul {node_id} nu exista in index. Foloseste search pentru "
                "id-uri valide."
            )
        label = section.splitlines()[0].lstrip("# ").strip()
        return {
            "id": node_id,
            "kind": "sectiune",
            "label": label,
            "path": f"{meta['title']} > {label}",
            "doc": slug,
            "doc_title": meta["title"],
            "version": meta["version"],
            "date": meta["date"],
            "source": self.source_line(meta, label),
            "source_url": meta.get("source_url"),
            "text": section,
            "ancestors": [],
            "truncated": False,
        }

    def docs(self, latest_only: bool = True) -> list[dict[str, Any]]:
        from .research import classify_family

        rows = [doc for doc in self.documents if not latest_only or doc.get("is_latest")]
        rows.sort(key=lambda item: (item["slug"], not item.get("is_latest"), item["version"]))
        return [
            {
                "slug": doc["slug"],
                "version": doc["version"],
                "title": doc["title"],
                "description": doc.get("description", ""),
                "markdown": doc["md_rel"],
                "source_url": doc.get("source_url"),
                "date": doc["date"],
                "is_latest": bool(doc.get("is_latest")),
                "family": classify_family(doc["slug"], doc.get("title") or ""),
            }
            for doc in rows
        ]

    # ---------------------------------------------------------------- research
    def select_docs(self, question: str, max_docs: int = 12) -> dict[str, Any]:
        """Narrow the catalogue to the acts worth searching for ``question``."""
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
        by_slug = {doc["slug"]: doc for doc in docs}
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
                bonus = top_score * (1.0 - 0.04 * (len(seen_docs) - 1))
                if doc["family"] == "formulare":
                    bonus *= 0.5
                previous = merged.get(doc["slug"])
                if previous:
                    score, matched, _ = previous
                    merged[doc["slug"]] = (score + bonus * 0.5, matched, doc)
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
        """Evidence package across source families, with explicit gaps."""
        from .research import FAMILY_LABELS, scenario_expansions

        plan = self.select_docs(question, max_docs=max_docs + 4)
        limit = max(1, min(limit, 6))
        blocks: list[str] = [
            "# Cercetare pe familii de surse",
            "",
            f"Întrebare: **{question}**",
            "",
            "Selecție de acte după descriere/titlu, apoi căutare de text în "
            "fiecare. Citește textul și declară explicit orice familie rămasă "
            "fără dovezi.",
            "",
        ]
        if compact:
            blocks.extend(
                [
                    "Mod compact: pasaje scurte. Pentru text integral foloseste "
                    "`article`/`show` pe articolele indicate.",
                    "",
                ]
            )
        if plan["topics"]:
            blocks.append(
                "Teme detectate: "
                + ", ".join(plan["topics"])
                + ". Familii obligatorii de verificat: "
                + ", ".join(
                    FAMILY_LABELS.get(f, f) for f in plan["required_families"]
                )
                + "."
            )
            blocks.append("")

        evidence_families: set[str] = set()
        per_doc_hits: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for doc in plan["docs"]:
            if doc["family"] == "formulare":
                continue
            hits = self.search(question, doc=doc["slug"], limit=limit)
            if hits:
                evidence_families.add(doc["family"])
                per_doc_hits.append((doc, hits))

        narrative = {
            "pfa", "srl", "ii", "if", "firma", "firmei", "imi", "mie", "meu",
            "mea", "am", "avem", "facturez", "facturezi", "doresc", "vreau",
            "multumesc",
        }
        concept_tokens = [token for token in tokens(question) if token not in narrative]
        concept_query = " ".join(concept_tokens)
        if "tva" in plan["topics"]:
            concept_query += " client neimpozabil"
        expansions = scenario_expansions(question, plan["topics"])
        if expansions:
            concept_query += " " + " ".join(expansions)
        if concept_query:
            second_docs = [
                doc
                for doc in plan["docs"]
                if doc["family"] in plan["required_families"]
            ]
            second_limit = 4 if expansions else 3
            for doc in second_docs:
                extra = self.search(concept_query, doc=doc["slug"], limit=second_limit)
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
            blocks.append(
                f"Act: `{doc['slug']}` · versiune: {doc['date']}"
            )
            blocks.append("")
            for hit in hits:
                title = hit["article_label"] or hit["label"] or hit["kind"]
                blocks.append(f"### {title}")
                blocks.append(hit["source"])
                blocks.append("")
                text = SPACE_RE.sub(" ", hit["text"]).strip()
                if compact:
                    excerpt = text[:320]
                    if len(text) > 320:
                        excerpt = excerpt.rstrip() + " …"
                    blocks.append(excerpt)
                else:
                    blocks.append(hit["text"][:1600])
                blocks.append("")

            top = hits[0]
            if top.get("article_number"):
                try:
                    article = self.article(
                        top["doc"], top["article_number"], top.get("version")
                    )
                    if article["text"][:1600] != top["text"][:1600]:
                        blocks.append(
                            "**Articolul integral (context pentru excepții):**"
                        )
                        blocks.append(article["source"])
                        blocks.append("")
                        blocks.append(article["text"])
                        blocks.append("")
                except NotFoundError:
                    pass

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
    @staticmethod
    def _action_haystack(action: dict[str, Any]) -> str:
        code = str(action.get("code") or "")
        compact = re.sub(r"[^a-z0-9]", "", code.lower())
        return (
            f"{code} {compact} d{compact} declaratia-{compact} "
            f"{action.get('title') or ''} {action.get('description') or ''}"
        ).lower()

    def actions(
        self, query: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """ANAF declaration sheets: the form to complete and upload in SPV."""
        limit = max(1, min(limit, 50))
        if not self.actions_data:
            return []

        def item(action: dict[str, Any], score: float) -> dict[str, Any]:
            return {
                "doc_slug": action["doc_slug"],
                "code": action["code"],
                "title": action.get("title") or "",
                "description": action.get("description") or "",
                "source_url": action.get("source_url"),
                "link": self._pdf_link(action.get("pdf_rel")),
                "xfa": bool(action.get("xfa")),
                "score": score,
            }

        if not query:
            return [item(action, 0.0) for action in self.actions_data[:limit]]

        query_tokens = set(tokens(query, dedupe=True))
        if not query_tokens:
            return []
        matches: list[tuple[float, dict[str, Any]]] = []
        for action in self.actions_data:
            haystack = self._action_haystack(action)
            score = sum(1.0 for token in query_tokens if token in haystack)
            if score:
                matches.append((score, action))
        matches.sort(key=lambda entry: (-entry[0], str(entry[1]["code"])))
        return [item(action, score) for score, action in matches[:limit]]

    # ----------------------------------------------------------------- context
    def content(self, node_id: str, max_chars: int = 12000) -> dict[str, Any]:
        data = self.show(node_id)
        text = data["text"]
        if len(text) > max_chars:
            data = dict(data)
            data["text"] = text[:max_chars] + "\n\n… (text trunchiat)"
            data["truncated"] = True
        return data

    def context(
        self, question: str, limit: int = 4, doc: str | None = None
    ) -> str:
        hits = self.search(question, doc=doc, limit=max(limit * 3, 8))
        no_text_hits = not hits

        from .research import classify_family, topic_families

        _topics, required = topic_families(question) if doc is None else ([], [])
        picked: list[tuple[str, dict[str, Any]]] = []
        picked_families: set[str] = set()

        def pick(hit: dict[str, Any]) -> None:
            target = hit.get("article_id") or hit["id"]
            if any(target == existing for existing, _ in picked):
                return
            picked.append((target, hit))
            picked_families.add(classify_family(hit["doc"], hit["doc_title"]))

        for family in required:
            if family in picked_families or len(picked) >= limit:
                continue
            for hit in hits:
                if classify_family(hit["doc"], hit["doc_title"]) == family:
                    pick(hit)
                    break
        for hit in hits:
            if len(picked) >= limit:
                break
            pick(hit)

        blocks: list[str] = [
            "# Evidențe din corpus",
            "",
            f"Întrebare: **{question}**",
            "",
            "Fiecare sursă este citată cu numele actului, slug-ul și versiunea "
            "consolidată. Formularele ANAF păstrează linkul PDF public.",
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
        for index, node_id in enumerate((target for target, _ in picked), 1):
            data = self.content(node_id, max_chars=8000)
            blocks.append(f"## {index}. {data['doc_title']} — {data['label']}")
            blocks.append(data["source"])
            blocks.append("")
            blocks.append(data["text"])
            blocks.append("")
            if data.get("truncated"):
                blocks.append(
                    "Text trunchiat. Cere `show`/`article` pentru restul."
                )
                blocks.append("")

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
            lines.append(item["source"])
            lines.append(
                f"Fragment: {item['kind']} · scor BM25: {item['score']}"
            )
            lines.append("")
            lines.append(item["snippet"] or item["text"][:600])
            lines.append("")
        return "\n".join(lines)


def open_index(data_dir: str | Path | None = None) -> LegalIndex:
    return LegalIndex(data_dir=data_dir)
