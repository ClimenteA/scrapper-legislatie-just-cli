"""Step 4: deterministic BM25 index over the Markdown corpus.

No embeddings, no language model, no graph database. The index is a persisted
:class:`llama_index.retrievers.bm25.BM25Retriever` built from the Markdown
files in ``3-expanded``:

1. :class:`MarkdownNodeParser` splits each act on its headings, so one node is
   usually one article (``### Articolul 60``) with its paragraphs and lists.
2. Every node is folded to ASCII and stripped of Romanian function words
   (:mod:`text`), then stored as a :class:`TextNode`. The original text stays
   in ``metadata["original_text"]`` for the answer.
3. The retriever is persisted under ``4-processed/bm25`` together with
   ``docs.json`` (act metadata) and ``actions.json`` (ANAF form sheets).

The build is deterministic: the same Markdown produces byte-identical nodes
and the same BM25 statistics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llama_index.core import Document
from llama_index.core.node_parser import MarkdownNodeParser
from llama_index.core.schema import TextNode
from llama_index.retrievers.bm25 import BM25Retriever

from .config import resolve_data_dir, step_dir
from .logger import log
from .misc import extract_file_meta
from .pdf_ingest import load_pdf_actions
from .sources import Sursa, surse_legislatie_just
from .text import indexed_text

INDEX_DIRNAME = "bm25"
DOCS_FILENAME = "docs.json"
ACTIONS_FILENAME = "actions.json"
MANIFEST_FILENAME = "manifest.json"
MAX_NODE_CHARS = 2400
CHUNK_OVERLAP = 200
ARTICLE_RE = re.compile(r"Articolul\s+(\d+(?:\^\d+)?)", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
BLANK_LINES_RE = re.compile(r"\n{3,}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_write(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def _chunks(text: str) -> list[str]:
    """Split an over-long section on paragraph boundaries, keeping context."""
    if len(text) <= MAX_NODE_CHARS:
        return [text]
    parts: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > MAX_NODE_CHARS:
            parts.append(current.strip())
            tail = current[-CHUNK_OVERLAP:]
            current = tail + "\n\n" + paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current.strip():
        parts.append(current.strip())
    return parts


@dataclass
class Bm25IndexBuilder:
    """Build the persisted BM25 index and the source metadata."""

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
        self.index_dir = self.processed_dir / INDEX_DIRNAME
        self.docs_path = self.processed_dir / DOCS_FILENAME
        self.actions_path = self.processed_dir / ACTIONS_FILENAME
        self.manifest_path = self.processed_dir / MANIFEST_FILENAME

    # ------------------------------------------------------------------ build
    def build(self) -> dict:
        started = time.perf_counter()
        documents = self._discover_documents()
        if self.limit is not None:
            documents = documents[: self.limit]
        log.info(f"Am gasit {len(documents)} documente Markdown de indexat.")

        actions = load_pdf_actions(self.root)
        digest = self._fingerprint(documents, actions)
        if not self.force and self._is_fresh(digest):
            log.info("Indexul BM25 este deja la zi; nu reconstruiesc (--force).")
            return {
                "documents": len(documents),
                "nodes": 0,
                "skipped": True,
                "seconds": round(time.perf_counter() - started, 2),
            }

        nodes = self._build_nodes(documents)
        if not nodes:
            raise RuntimeError(
                "Nu exista noduri de indexat. Ruleaza pasul 'expand' inainte."
            )

        tmp_dir = self.processed_dir / f"{INDEX_DIRNAME}.tmp"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        log.info(f"Construiesc indexul BM25 pentru {len(nodes)} noduri...")
        retriever = BM25Retriever.from_defaults(
            nodes=nodes,
            similarity_top_k=min(10, len(nodes)),
            skip_stemming=True,
            language=None,
        )
        retriever.persist(str(tmp_dir))

        old_dir = self.index_dir
        if old_dir.exists():
            shutil.rmtree(old_dir)
        os.replace(tmp_dir, old_dir)

        _json_write(
            self.docs_path,
            {
                "built_at": self._now(),
                "items": [
                    {key: value for key, value in doc.items() if key != "md_path"}
                    for doc in documents
                ],
            },
        )
        _json_write(
            self.actions_path,
            {"built_at": self._now(), "items": actions},
        )
        _json_write(
            self.manifest_path,
            {
                "source_digest": digest,
                "built_at": self._now(),
                "documents": len(documents),
                "nodes": len(nodes),
                "build_seconds": round(time.perf_counter() - started, 2),
                "files": {doc["md_rel"]: doc["content_hash"] for doc in documents},
            },
        )
        self._write_catalog(documents, len(nodes))
        self._write_readme(documents, len(nodes))
        self._write_instructions()

        elapsed = round(time.perf_counter() - started, 2)
        return {
            "documents": len(documents),
            "nodes": len(nodes),
            "skipped": False,
            "seconds": elapsed,
        }

    # -------------------------------------------------------------- discovery
    def _discover_documents(self) -> list[dict]:
        selected = {s.slug for s in self.surse}
        documents: list[dict] = []

        for slug_dir in sorted(self.expanded_dir.iterdir()):
            if not slug_dir.is_dir() or slug_dir.name.startswith("."):
                continue
            slug = slug_dir.name
            if selected and slug not in selected:
                continue

            toc = self._load_toc(slug_dir, slug)
            consolidari = {
                item.get("md_name"): item for item in toc.get("consolidari", [])
            }
            versions: list[dict] = []

            for md_path in sorted(slug_dir.glob("*.md")):
                meta = extract_file_meta(md_path.name) or {}
                consolidation = consolidari.get(md_path.name, {})
                version = str(
                    meta.get("data_consolidarii")
                    or consolidation.get("data_consolidarii")
                    or "unknown"
                )
                source_url = consolidation.get("link")
                if not source_url and meta.get("numar_link"):
                    source_url = (
                        "https://legislatie.just.ro/Public/DetaliiDocument/"
                        f"{meta['numar_link']}"
                    )
                versions.append(
                    {
                        "slug": slug,
                        "version": version,
                        "title": toc.get("nume", slug),
                        "description": toc.get("descriere", ""),
                        "md_rel": self._relative_to_root(md_path),
                        "source_url": source_url,
                        "source_pdf": consolidation.get("source_pdf"),
                        "numar_link": meta.get("numar_link")
                        or consolidation.get("numar_link"),
                        "date": version,
                        "is_latest": False,
                        "content_hash": _sha256(md_path),
                        "md_path": md_path,
                    }
                )

            if not versions:
                continue
            latest = max(versions, key=lambda item: item["version"])
            for item in versions:
                item["is_latest"] = item is latest
            documents.extend(sorted(versions, key=lambda item: item["version"]))

        return documents

    def _load_toc(self, slug_dir: Path, slug: str) -> dict:
        toc_path = slug_dir / f"toc-{slug}.json"
        if not toc_path.exists():
            return {}
        try:
            return json.loads(toc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            log.warning(f"Nu pot citi {toc_path}: {ex}")
            return {}

    def _relative_to_root(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def _fingerprint(self, documents: list[dict], actions: list[dict]) -> str:
        payload = "\n".join(
            f"{doc['md_rel']}:{doc['content_hash']}" for doc in documents
        )
        payload += "\nactions:" + json.dumps(actions, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _is_fresh(self, digest: str) -> bool:
        if not (
            self.index_dir.exists()
            and self.docs_path.exists()
            and self.actions_path.exists()
            and self.manifest_path.exists()
        ):
            return False
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return manifest.get("source_digest") == digest

    # ------------------------------------------------------------------ nodes
    def _build_nodes(self, documents: list[dict]) -> list[TextNode]:
        parser = MarkdownNodeParser()
        nodes: list[TextNode] = []
        total = len(documents)
        for index, doc in enumerate(documents, start=1):
            text = doc["md_path"].read_text(encoding="utf-8", errors="ignore")
            document = Document(
                text=text,
                metadata={
                    "slug": doc["slug"],
                    "version": doc["version"],
                    "title": doc["title"],
                    "description": doc["description"],
                    "md_rel": doc["md_rel"],
                    "source_url": doc["source_url"] or "",
                    "date": doc["date"],
                    "is_latest": bool(doc["is_latest"]),
                },
            )
            parsed = parser.get_nodes_from_documents([document])
            before = len(nodes)
            for part_index, node in enumerate(parsed):
                content = BLANK_LINES_RE.sub("\n\n", node.get_content()).strip()
                if not content:
                    continue
                header = str(node.metadata.get("header_path") or "")
                section = header.rstrip("/").rsplit("/", 1)[-1].strip()
                first_line = content.splitlines()[0].strip() if content else ""
                heading_match = HEADING_RE.match(first_line)
                if heading_match:
                    section = heading_match.group(2).strip()
                if section.lower().startswith("cuprins"):
                    continue
                match = ARTICLE_RE.search(section) or ARTICLE_RE.search(content[:200])
                article_number = match.group(1) if match else None
                kind = "articol" if article_number else "sectiune"
                if kind == "sectiune" and len(content) < 30:
                    continue
                for chunk_index, chunk in enumerate(_chunks(content)):
                    node_id = self._node_id(
                        doc, part_index, chunk_index, article_number
                    )
                    nodes.append(
                        TextNode(
                            id_=node_id,
                            text=indexed_text(chunk),
                            metadata={
                                "slug": doc["slug"],
                                "version": doc["version"],
                                "title": doc["title"],
                                "description": doc["description"],
                                "md_rel": doc["md_rel"],
                                "source_url": doc["source_url"] or "",
                                "date": doc["date"],
                                "is_latest": bool(doc["is_latest"]),
                                "section": section,
                                "kind": kind,
                                "article_number": article_number or "",
                                "original_text": chunk,
                            },
                        )
                    )
            log.info(
                f"[{index}/{total}] {doc['slug']} ({doc['version']}): "
                f"{len(nodes) - before} noduri."
            )
        return nodes

    @staticmethod
    def _node_id(
        doc: dict,
        part_index: int,
        chunk_index: int,
        article_number: str | None,
    ) -> str:
        base = f"{doc['slug']}@{doc['version']}"
        if article_number:
            suffix = f"art-{article_number}"
        else:
            suffix = f"sec-{part_index}"
        if chunk_index:
            suffix += f"-{chunk_index}"
        return f"{base}#{suffix}"

    # ----------------------------------------------------------------- output
    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _write_catalog(self, documents: list[dict], nodes: int) -> None:
        _json_write(
            self.processed_dir / "catalog.json",
            {
                "generated_at": self._now(),
                "documents": len(documents),
                "nodes": nodes,
                "items": [
                    {
                        "slug": doc["slug"],
                        "title": doc["title"],
                        "version": doc["version"],
                        "date": doc["date"],
                        "is_latest": doc["is_latest"],
                        "markdown": doc["md_rel"],
                        "source_url": doc["source_url"],
                    }
                    for doc in documents
                ],
            },
        )

    def _write_readme(self, documents: list[dict], nodes: int) -> None:
        lines = [
            "# 4-processed",
            "",
            "Index BM25 persistent peste legislația convertită în Markdown în",
            "`3-expanded`. Fără embeddings, fără model de limbaj: tokenizare",
            "deterministă, diacritice eliminate, stopwords românești scoase și",
            "cuvinte stemuite cu PyStemmer (reguli, nu rețele neuronale).",
            "",
            f"- documente: **{len(documents)}**",
            f"- noduri indexate: **{nodes}**",
            "- `bm25/` — indexul persistat (llama-index BM25)",
            "- `docs.json` — metadatele actelor și versiunile",
            "- `actions.json` — fișele de acțiune ANAF (formulare + PDF)",
            "",
            "## Utilizare",
            "",
            "Indexul este servit prin serverul MCP HTTP. Rulează",
            "`docker compose up -d` din rădăcina depozitului, ia un token din",
            "`/docs` și conectează clientul (OpenCode, Claude Code etc.) la `/mcp`.",
            "",
            "Tool-urile puse la dispoziție: `search`, `article`, `show`,",
            "`context`, `docs`, `select_docs`, `research`, `convert_currency`,",
            "`actions`, `answer_template` și `validate_answer`.",
            "",
            "Sursele se citează cu numele actului, slug-ul și versiunea",
            "consolidată; nu există linkuri HTML cu ancore. Formularele ANAF",
            "păstrează linkul PDF public din `actions`.",
            "",
            "## Documente indexate",
            "",
            "| Act | Versiune | Markdown |",
            "|---|---|---|",
        ]
        for doc in sorted(documents, key=lambda item: item["slug"]):
            lines.append(
                f"| {doc['title'][:80]} | {doc['version']} | `{doc['md_rel']}` |"
            )
        (self.processed_dir / "README.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _write_instructions(self) -> None:
        template_dir = Path(__file__).with_name("templates")
        for name in ("AGENTS.md", "answer-format.md"):
            source = template_dir / name
            if not source.exists():
                continue
            # ``copy2`` also copies timestamps, which fails on files owned by
            # root inside the bind-mounted data volume. Only the bytes matter.
            (self.processed_dir / name).write_bytes(source.read_bytes())
