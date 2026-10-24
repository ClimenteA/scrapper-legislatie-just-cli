"""Deterministic retrieval benchmark for the legal corpus.

Compares two ways of finding evidence for a question:

* ``MarkdownBaseline`` — choose an act from ``3-expanded/*/toc-*.json`` and
  search the act's Markdown file, mimicking the original agent workflow.
* ``IndexedMethod`` — use ``4-processed/law.db`` (FTS5/BM25) and the citation
  graph through :class:`~scrapper_legislatie_just.query.LegalIndex`.

The benchmark is offline and deterministic. It scores whether the expected
article/anchor appears in the top ``k`` results and records latency; it does not
judge generated answer prose.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import resolve_data_dir, step_dir
from .query import (
    LegalIndex,
    _proximity,
    _query_tokens,
    normalize,
)

ARTICLE_RE = re.compile(
    r"^(#{2,4})\s*Articolul\s+(\d+(?:\^\d+)?)\b\s*[-–—]?\s*(.*)$",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|")
MAX_CHUNK_CHARS = 4000
MAX_SECTION_CHARS = 8000


@dataclass(frozen=True)
class BenchmarkCase:
    question: str
    doc: str
    articles: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    expected_terms: tuple[str, ...] = ()
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkCase":
        return cls(
            question=str(data["question"]),
            doc=str(data["doc"]),
            articles=tuple(str(item) for item in data.get("articles", ())),
            anchors=tuple(str(item) for item in data.get("anchors", ())),
            expected_terms=tuple(
                str(item) for item in data.get("expected_terms", ())
            ),
            notes=str(data.get("notes", "")),
        )

    @property
    def expected_units(self) -> tuple[str, ...]:
        return self.articles or self.anchors


@dataclass
class MethodHit:
    doc: str
    article_number: str | None
    anchor: str | None
    link: str
    text: str
    rank: int = 0
    path: str | None = None
    section_text: str = ""

    @property
    def normalized_text(self) -> str:
        return normalize(self.text)


@dataclass
class _Section:
    doc: str
    article_number: str | None
    label: str
    start_line: int
    end_line: int
    text: str


@dataclass
class _Chunk:
    section_index: int
    start_line: int
    end_line: int
    text: str
    normalized: str
    label_normalized: str


@dataclass
class _Document:
    slug: str
    title: str
    description: str
    md_path: Path
    date: str | None
    sections: list[_Section] = field(default_factory=list)
    chunks: list[_Chunk] = field(default_factory=list)


def load_cases(path: str | Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(BenchmarkCase.from_dict(json.loads(line)))
    return cases


def _normalized_terms(question: str) -> list[tuple[str, str]]:
    return _query_tokens(question)


def _count_chunks(terms: list[tuple[str, str]], chunks: list[_Chunk]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token, stem in terms:
        count = 0
        for chunk in chunks:
            if token in chunk.normalized or stem in chunk.normalized:
                count += 1
        counts[token] = count
    return counts


def _score_chunk(
    chunk: _Chunk,
    terms: list[tuple[str, str]],
    idf: dict[str, float],
) -> tuple[float, set[str], set[str]]:
    exact_tokens: set[str] = set()
    matched_stems: set[str] = set()
    score = 0.0
    for token, stem in terms:
        exact = bool(
            re.search(rf"(?<![a-z0-9]){re.escape(token)}", chunk.normalized)
        )
        if exact:
            exact_tokens.add(token)
            matched_stems.add(stem)
            in_label = token in chunk.label_normalized
            score += idf.get(token, 0.0) * (1.8 if in_label else 1.0)
            continue
        if stem in chunk.normalized:
            matched_stems.add(stem)
            in_label = stem in chunk.label_normalized
            score += idf.get(token, 0.0) * 0.45 * (1.3 if in_label else 1.0)
    if not matched_stems:
        return 0.0, exact_tokens, matched_stems
    if len(exact_tokens) == len(terms):
        score *= 1.4
    score += (
        sum(idf.get(token, 0.0) for token, stem in terms if stem in matched_stems)
        * 0.5
        * _proximity(chunk.normalized, list(matched_stems))
    )
    return score, exact_tokens, matched_stems


class MarkdownBaseline:
    """Old/frontend retrieval path: TOC JSON + Markdown file search."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.root = resolve_data_dir(data_dir)
        self.expanded_dir = step_dir(self.root, "expanded")
        self._docs: list[dict[str, Any]] = []
        self._by_slug: dict[str, dict[str, Any]] = {}
        self._cache: dict[str, _Document] = {}
        self.setup_seconds = 0.0
        started = time.perf_counter()
        self._load_docs()
        self.setup_seconds = time.perf_counter() - started

    def _load_docs(self) -> None:
        for toc_path in sorted(self.expanded_dir.glob("*/toc-*.json")):
            try:
                data = json.loads(toc_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            slug = str(data.get("slug") or toc_path.parent.name)
            consolidari = data.get("consolidari") or []
            chosen = max(
                consolidari,
                key=lambda item: (
                    str(item.get("data_consolidarii") or ""),
                    str(item.get("html_name") or ""),
                ),
                default={},
            )
            html_name = str(chosen.get("html_name") or "")
            md_path = (
                toc_path.parent / Path(html_name).with_suffix(".md").name
                if html_name
                else toc_path.parent / "__missing__.md"
            )
            if not html_name or not md_path.exists():
                candidates = sorted(toc_path.parent.glob("*.md"))
                if not candidates:
                    continue
                md_path = candidates[-1]
            doc = {
                "slug": slug,
                "title": str(data.get("nume") or slug),
                "description": str(data.get("descriere") or ""),
                "md_path": md_path,
                "date": chosen.get("data_consolidarii"),
                "metadata": normalize(
                    " ".join(
                        (
                            str(data.get("nume") or ""),
                            str(data.get("descriere") or ""),
                        )
                    )
                ),
            }
            self._docs.append(doc)
            self._by_slug[slug] = doc

    @property
    def docs(self) -> list[dict[str, Any]]:
        return list(self._docs)

    def clear_cache(self) -> None:
        self._cache.clear()

    def select_doc(self, question: str, k: int = 3) -> list[dict[str, Any]]:
        terms = _normalized_terms(question)
        if not terms or not self._docs:
            return []
        df: dict[str, int] = {}
        for token, stem in terms:
            df[token] = sum(
                1
                for doc in self._docs
                if token in doc["metadata"] or stem in doc["metadata"]
            )
        idf = {
            token: math.log(1.0 + len(self._docs) / count)
            for token, count in df.items()
            if count
        }
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in self._docs:
            score = 0.0
            matched = 0
            for token, stem in terms:
                if token in doc["metadata"]:
                    score += idf.get(token, 0.0)
                    matched += 1
                elif stem in doc["metadata"]:
                    score += idf.get(token, 0.0) * 0.45
                    matched += 1
            if matched and score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: (-item[0], item[1]["slug"]))
        return [doc for _score, doc in scored[:k]]

    def _parse_doc(self, doc: dict[str, Any]) -> _Document:
        slug = doc["slug"]
        if slug in self._cache:
            return self._cache[slug]

        md_path: Path = doc["md_path"]
        try:
            lines = md_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

        article_headings = [
            index
            for index, line in enumerate(lines)
            if ARTICLE_RE.match(line)
        ]
        sections: list[_Section] = []
        if article_headings:
            current: _Section | None = None
            seen_article = False
            for index, line in enumerate(lines):
                heading = HEADING_RE.match(line)
                article = ARTICLE_RE.match(line)
                if article or (heading and len(heading.group(1)) <= 3):
                    if current is not None:
                        current.end_line = index
                        sections.append(current)
                        current = None
                    if article:
                        seen_article = True
                        number = article.group(2)
                        label = f"Articolul {number} {article.group(3)}".strip()
                        current = _Section(
                            doc=slug,
                            article_number=number,
                            label=label,
                            start_line=index + 1,
                            end_line=len(lines),
                            text=line.strip(),
                        )
                    elif seen_article and heading:
                        current = _Section(
                            doc=slug,
                            article_number=None,
                            label=heading.group(2),
                            start_line=index + 1,
                            end_line=len(lines),
                            text=heading.group(2),
                        )
                    continue
                if current is not None:
                    current.text += "\n" + line
            if current is not None:
                current.end_line = len(lines)
                sections.append(current)
        else:
            sections.append(
                _Section(
                    doc=slug,
                    article_number=None,
                    label=doc["title"],
                    start_line=1,
                    end_line=len(lines),
                    text="\n".join(lines),
                )
            )

        document = _Document(
            slug=slug,
            title=doc["title"],
            description=doc["description"],
            md_path=md_path,
            date=doc.get("date"),
            sections=sections,
        )
        for section_index, section in enumerate(sections):
            label_normalized = normalize(section.label)
            for chunk_lines, start, end in _split_section(section):
                text = "\n".join(chunk_lines).strip()
                if not text:
                    continue
                document.chunks.append(
                    _Chunk(
                        section_index=section_index,
                        start_line=start,
                        end_line=end,
                        text=text,
                        normalized=normalize(f"{section.label} {text}"),
                        label_normalized=label_normalized,
                    )
                )
        self._cache[slug] = document
        return document

    def search(
        self,
        question: str,
        doc: str | None = None,
        limit: int = 10,
    ) -> list[MethodHit]:
        selected = doc
        if selected is None:
            ranked_docs = self.select_doc(question, k=1)
            if not ranked_docs:
                return []
            selected = ranked_docs[0]["slug"]
        meta = self._by_slug.get(selected)
        if meta is None:
            return []
        document = self._parse_doc(meta)
        terms = _normalized_terms(question)
        if not terms or not document.chunks:
            return []

        counts = _count_chunks(terms, document.chunks)
        active = [(token, stem) for token, stem in terms if counts.get(token)]
        if not active:
            return []
        idf = {
            token: math.log(1.0 + len(document.chunks) / counts[token])
            for token, _stem_value in active
        }

        per_section: dict[int, list[tuple[float, set[str], set[str], _Chunk]]] = {}
        for chunk in document.chunks:
            score, exact, stems = _score_chunk(chunk, active, idf)
            if not stems:
                continue
            per_section.setdefault(chunk.section_index, []).append(
                (score, exact, stems, chunk)
            )
        if not per_section:
            return []

        max_exact = max(
            (len(item[1]) for items in per_section.values() for item in items),
            default=0,
        )
        ranked: list[tuple[int, int, float, _Section, _Chunk]] = []
        for section_index, items in per_section.items():
            if max_exact:
                items = [item for item in items if item[1]]
                if not items:
                    continue
            best_score, best_exact, best_stems, best_chunk = max(
                items,
                key=lambda item: (len(item[1]), len(item[2]), item[0]),
            )
            extra = sum(
                sorted((item[0] for item in items), reverse=True)[1:6]
            )
            section = document.sections[section_index]
            ranked.append(
                (
                    len(best_stems),
                    len(best_exact),
                    best_score + 0.35 * extra,
                    section,
                    best_chunk,
                )
            )
        ranked.sort(key=lambda item: (-item[1], -item[0], -item[2]))

        hits: list[MethodHit] = []
        for rank, (_stems, _exact, _score, section, chunk) in enumerate(
            ranked[:limit], 1
        ):
            section_text = section.text.strip()
            if len(section_text) > MAX_SECTION_CHARS:
                section_text = section_text[:MAX_SECTION_CHARS].rstrip()
            hits.append(
                MethodHit(
                    doc=selected,
                    article_number=section.article_number,
                    anchor=None,
                    link=(
                        f"{document.md_path.as_uri()}"
                        f"#L{section.start_line}-L{section.end_line}"
                    ),
                    text=chunk.text[:MAX_CHUNK_CHARS],
                    rank=rank,
                    path=str(document.md_path),
                    section_text=section_text,
                )
            )
        return hits

    def context(
        self,
        question: str,
        limit: int = 4,
        doc: str | None = None,
    ) -> str:
        hits = self.search(question, doc=doc, limit=max(limit * 3, 8))
        if not hits:
            return "Nu am gasit pasaje in fisierele Markdown pentru aceasta intrebare."
        lines = [
            "# Evidente din fisierele 3-expanded (Markdown)",
            "",
            f"Intrebare: **{question}**",
            "",
        ]
        seen: set[tuple[str, str | None, str]] = set()
        count = 0
        for hit in hits:
            key = (hit.doc, hit.article_number, hit.link)
            if key in seen:
                continue
            seen.add(key)
            count += 1
            meta = self._by_slug.get(hit.doc, {})
            lines.append(
                f"## {count}. {meta.get('title', hit.doc)} — "
                f"{hit.article_number or hit.text[:60]}"
            )
            lines.append(f"SURSA: {hit.link}")
            if hit.section_text:
                lines.append(hit.section_text)
            else:
                lines.append(hit.text)
            lines.append("")
            if count >= limit:
                break
        return "\n".join(lines)

    def doc_rank(self, question: str, expected_doc: str, k: int = 3) -> int:
        for rank, doc in enumerate(self.select_doc(question, k=k), 1):
            if doc["slug"] == expected_doc:
                return rank
        return 0


class IndexedMethod:
    """New path: ``4-processed/law.db`` + citation graph."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        started = time.perf_counter()
        self.index = LegalIndex(data_dir=data_dir)
        self.open_seconds = time.perf_counter() - started

    def close(self) -> None:
        self.index.close()

    def search(
        self,
        question: str,
        doc: str | None = None,
        limit: int = 10,
    ) -> list[MethodHit]:
        results = self.index.search(question, doc=doc, limit=limit)
        hits: list[MethodHit] = []
        for rank, item in enumerate(results, 1):
            link = item["link"]
            anchor = link.rsplit("#", 1)[1] if "#" in link else None
            hits.append(
                MethodHit(
                    doc=item["doc"],
                    article_number=item.get("article_number"),
                    anchor=anchor,
                    link=link,
                    text=item["text"],
                    rank=rank,
                    path=link.split("#", 1)[0].removeprefix("file://"),
                    section_text=item["text"],
                )
            )
        return hits

    def context(
        self,
        question: str,
        limit: int = 4,
        doc: str | None = None,
    ) -> str:
        return self.index.context(question, limit=limit, doc=doc)

    @staticmethod
    def doc_rank(hits: list[MethodHit], expected_doc: str, k: int = 3) -> int:
        for rank, hit in enumerate(hits[:k], 1):
            if hit.doc == expected_doc:
                return rank
        return 0


def _split_section(
    section: _Section,
) -> list[tuple[list[str], int, int]]:
    lines = section.text.splitlines()
    chunks: list[tuple[list[str], int, int]] = []
    buffer: list[str] = []
    start = section.start_line

    def flush(end_line: int) -> None:
        nonlocal buffer, start
        if buffer:
            text = "\n".join(buffer).strip()
            if text:
                chunks.append((buffer[:], start, end_line))
            buffer = []
        start = end_line + 1

    for offset, line in enumerate(lines):
        line_no = section.start_line + offset
        if TABLE_ROW_RE.match(line):
            flush(line_no - 1)
            chunks.append(([line], line_no, line_no))
            start = line_no + 1
            continue
        if not line.strip():
            flush(line_no - 1)
            start = line_no + 1
            continue
        if sum(len(item) for item in buffer) + len(line) > MAX_CHUNK_CHARS:
            flush(line_no - 1)
        if not buffer:
            start = line_no
        buffer.append(line)
    flush(section.end_line)
    return chunks


def _hit_matches(
    hit: MethodHit,
    unit: str,
    case: BenchmarkCase,
    method_name: str,
) -> bool:
    if hit.doc != case.doc:
        return False
    if case.articles:
        return hit.article_number == unit
    if method_name == "indexed":
        return hit.anchor == unit
    terms = [normalize(term) for term in case.expected_terms]
    return bool(terms) and all(term in hit.normalized_text for term in terms)


def _first_match_rank(
    hits: list[MethodHit],
    case: BenchmarkCase,
    method_name: str,
) -> int:
    for hit in hits:
        for unit in case.expected_units:
            if _hit_matches(hit, unit, case, method_name):
                return hit.rank
    return 0


def _coverage_at(
    hits: list[MethodHit],
    case: BenchmarkCase,
    method_name: str,
    k: int = 5,
) -> float:
    units = case.expected_units
    if not units:
        return 0.0
    found = 0
    for unit in units:
        if any(
            _hit_matches(hit, unit, case, method_name)
            for hit in hits
            if hit.rank <= k
        ):
            found += 1
    return found / len(units)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _timed(callable_):
    started = time.perf_counter()
    value = callable_()
    return value, (time.perf_counter() - started) * 1000.0


def _run_case(
    case: BenchmarkCase,
    mode: str,
    baseline: MarkdownBaseline,
    indexed: IndexedMethod,
    reps: int,
) -> dict[str, Any]:
    doc = case.doc if mode == "known_doc" else None
    row: dict[str, Any] = {
        "question": case.question,
        "doc": case.doc,
        "expected": list(case.expected_units),
        "mode": mode,
    }
    for method_name, method in (("legacy", baseline), ("indexed", indexed)):
        if method_name == "legacy":
            baseline.clear_cache()
        hits, cold_ms = _timed(lambda: method.search(case.question, doc=doc, limit=10))
        context, context_ms = _timed(
            lambda: method.context(case.question, doc=doc, limit=4)
        )
        warm: list[float] = []
        for _ in range(max(reps, 1)):
            _, elapsed = _timed(lambda: method.search(case.question, doc=doc, limit=10))
            warm.append(elapsed)
        doc_rank = (
            method.doc_rank(case.question, case.doc)
            if method_name == "legacy"
            else method.doc_rank(hits, case.doc)
        )
        row[method_name] = {
            "rank": _first_match_rank(hits, case, method_name),
            "coverage@5": round(_coverage_at(hits, case, method_name), 3),
            "doc_rank": doc_rank,
            "hits": len(hits),
            "cold_search_ms": round(cold_ms, 3),
            "warm_search_ms": round(_median(warm), 3),
            "search_p95_ms": round(_percentile(warm, 0.95), 3),
            "context_ms": round(context_ms, 3),
            "context_chars": len(context),
            "anchor_hits": sum(1 for hit in hits if hit.anchor),
        }
    return row


def _aggregate(rows: list[dict[str, Any]], method_name: str) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {}

    def hits_at(k: int) -> float:
        return sum(
            1
            for row in rows
            if 0 < row[method_name]["rank"] <= k
        ) / total

    ranks = [row[method_name]["rank"] for row in rows]
    doc_ranks = [row[method_name]["doc_rank"] for row in rows]
    return {
        "cases": total,
        "any_hit@1": round(hits_at(1), 3),
        "any_hit@5": round(hits_at(5), 3),
        "any_hit@10": round(hits_at(10), 3),
        "coverage@5": round(
            sum(row[method_name]["coverage@5"] for row in rows) / total, 3
        ),
        "mrr": round(
            sum(1.0 / rank if rank else 0.0 for rank in ranks) / total, 3
        ),
        "doc_hit@1": round(
            sum(1 for rank in doc_ranks if rank == 1) / total, 3
        ),
        "doc_hit@3": round(
            sum(1 for rank in doc_ranks if 0 < rank <= 3) / total, 3
        ),
        "cold_search_ms": round(
            _median([row[method_name]["cold_search_ms"] for row in rows]), 3
        ),
        "warm_search_ms": round(
            _median([row[method_name]["warm_search_ms"] for row in rows]), 3
        ),
        "search_p95_ms": round(
            _median([row[method_name]["search_p95_ms"] for row in rows]), 3
        ),
        "context_ms": round(
            _median([row[method_name]["context_ms"] for row in rows]), 3
        ),
        "context_chars": round(
            _median([row[method_name]["context_chars"] for row in rows]), 1
        ),
        "anchor_coverage": round(
            sum(row[method_name]["anchor_hits"] for row in rows)
            / max(1, sum(row[method_name]["hits"] for row in rows)),
            3,
        ),
    }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _corpus_overhead(
    root: Path,
    baseline_docs: list[dict[str, Any]],
    indexed: IndexedMethod,
) -> dict[str, Any]:
    manifest_path = step_dir(root, "processed") / "manifest.json"
    build_seconds = None
    documents = None
    nodes = None
    refs = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            build_seconds = manifest.get("build_seconds")
            documents = manifest.get("documents")
            nodes = manifest.get("nodes")
            refs = manifest.get("refs")
        except (OSError, json.JSONDecodeError):
            pass
    law_db = step_dir(root, "processed") / "law.db"
    graph_db = step_dir(root, "processed") / "graph.db"
    markdown_bytes = sum(_file_size(doc["md_path"]) for doc in baseline_docs)
    return {
        "documents": documents,
        "nodes": nodes,
        "refs": refs,
        "build_seconds": build_seconds,
        "markdown_bytes": markdown_bytes,
        "law_db_bytes": _file_size(law_db),
        "graph_db_bytes": _file_size(graph_db),
        "processed_bytes": _file_size(law_db) + _file_size(graph_db),
    }


def run_benchmark(
    data_dir: str | Path | None = None,
    cases_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    reps: int = 3,
) -> dict[str, Any]:
    root = resolve_data_dir(data_dir)
    cases_path = Path(cases_path) if cases_path else (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "fixtures"
        / "real_questions.jsonl"
    )
    output_dir = Path(output_dir) if output_dir else (
        Path(__file__).resolve().parents[2] / "tests" / "artifacts"
    )
    cases = load_cases(cases_path)

    baseline = MarkdownBaseline(data_dir=root)
    indexed = IndexedMethod(data_dir=root)
    try:
        rows: list[dict[str, Any]] = []
        for mode in ("known_doc", "blind_doc"):
            for case in cases:
                rows.append(_run_case(case, mode, baseline, indexed, reps))
        overhead = _corpus_overhead(root, baseline.docs, indexed)
    finally:
        indexed.close()

    summary: dict[str, dict[str, Any]] = {}
    for mode in ("known_doc", "blind_doc"):
        mode_rows = [row for row in rows if row["mode"] == mode]
        summary[mode] = {
            "legacy": _aggregate(mode_rows, "legacy"),
            "indexed": _aggregate(mode_rows, "indexed"),
        }

    known = summary["known_doc"]
    legacy_warm = known["legacy"]["warm_search_ms"]
    indexed_warm = known["indexed"]["warm_search_ms"]
    overhead["query_saving_ms"] = round(legacy_warm - indexed_warm, 3)
    if (
        overhead.get("build_seconds")
        and legacy_warm > indexed_warm
        and legacy_warm > 0
    ):
        overhead["break_even_queries"] = round(
            float(overhead["build_seconds"])
            * 1000.0
            / (legacy_warm - indexed_warm),
            1,
        )
    else:
        overhead["break_even_queries"] = None

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_dir": str(root),
        "cases_file": str(cases_path),
        "reps": reps,
        "cases": len(cases),
        "setup": {
            "legacy_toc_ms": round(baseline.setup_seconds * 1000.0, 3),
            "index_open_ms": round(indexed.open_seconds * 1000.0, 3),
        },
        "summary": summary,
        "overhead": overhead,
        "rows": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "retrieval_benchmark.json"
    md_path = output_dir / "retrieval_benchmark.md"
    report["artifacts"] = {"json": str(json_path), "markdown": str(md_path)}
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return report


def _method_table(report: dict[str, Any], mode: str) -> list[str]:
    data = report["summary"][mode]
    lines = [
        "| metric | Markdown baseline | 4-processed index |",
        "|---|---:|---:|",
    ]
    for key, label in (
        ("any_hit@1", "any_hit@1"),
        ("any_hit@5", "any_hit@5"),
        ("any_hit@10", "any_hit@10"),
        ("coverage@5", "coverage@5"),
        ("mrr", "MRR"),
        ("doc_hit@1", "doc_hit@1"),
        ("doc_hit@3", "doc_hit@3"),
        ("cold_search_ms", "cold search (ms)"),
        ("warm_search_ms", "warm search median (ms)"),
        ("search_p95_ms", "warm search p95 (ms)"),
        ("context_ms", "context build median (ms)"),
        ("context_chars", "context chars median"),
        ("anchor_coverage", "exact HTML anchor (index only)"),
    ):
        legacy_value = data["legacy"].get(key)
        if key == "anchor_coverage":
            legacy_value = "n/a"
        lines.append(
            f"| {label} | {legacy_value} | "
            f"{data['indexed'].get(key)} |"
        )
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    known = report["summary"]["known_doc"]
    blind = report["summary"]["blind_doc"]
    lines = [
        "# Retrieval benchmark: 3-expanded Markdown vs 4-processed index",
        "",
        f"Generated: {report['generated_at']}  ",
        f"Cases: {report['cases']}  ",
        f"Corpus: {report['data_dir']}",
        "",
        "## Verdict",
        "",
        (
            f"- Known doc: indexed any_hit@5 "
            f"**{known['indexed']['any_hit@5']:.0%}** vs Markdown "
            f"**{known['legacy']['any_hit@5']:.0%}**; warm search "
            f"**{known['indexed']['warm_search_ms']:.2f} ms** vs "
            f"**{known['legacy']['warm_search_ms']:.2f} ms**."
        ),
        (
            f"- Blind doc selection: indexed doc_hit@1 "
            f"**{blind['indexed']['doc_hit@1']:.0%}** vs Markdown "
            f"**{blind['legacy']['doc_hit@1']:.0%}**."
        ),
        "",
        "## Accuracy and speed — supplied doc",
        "",
        *_method_table(report, "known_doc"),
        "",
        "## Accuracy and speed — blind document selection",
        "",
        *_method_table(report, "blind_doc"),
        "",
        "## Corpus overhead",
        "",
        "| item | value |",
        "|---|---:|",
    ]
    overhead = report["overhead"]
    for key, label in (
        ("documents", "documents"),
        ("nodes", "indexed nodes"),
        ("refs", "indexed references"),
        ("build_seconds", "index build (s)"),
        ("markdown_bytes", "Markdown bytes"),
        ("law_db_bytes", "law.db bytes"),
        ("graph_db_bytes", "graph.db bytes"),
        ("processed_bytes", "4-processed bytes"),
        ("query_saving_ms", "warm query saving (ms)"),
        ("break_even_queries", "break-even queries"),
    ):
        lines.append(f"| {label} | {overhead.get(key)} |")
    lines.extend(
        [
            "",
            "The Markdown method's evidence is the source `.md` file; line ranges",
            "are only an optional locator, and missing HTML anchors are not",
            "penalized. The indexed method returns exact HTML anchors.",
            "",
            "## Per case",
            "",
            "| # | mode | question | expected | legacy rank | indexed rank | legacy warm ms | indexed warm ms |",
            "|---:|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for index, row in enumerate(report["rows"], 1):
        question = row["question"].replace("|", "\\|")
        expected = ", ".join(row["expected"]).replace("|", "\\|")
        legacy_rank = row["legacy"]["rank"] or "-"
        indexed_rank = row["indexed"]["rank"] or "-"
        lines.append(
            f"| {index} | {row['mode']} | {question} | {expected} | "
            f"{legacy_rank} | {indexed_rank} | "
            f"{row['legacy']['warm_search_ms']:.2f} | "
            f"{row['indexed']['warm_search_ms']:.2f} |"
        )
    lines.append("")
    return "\n".join(lines)


def default_output_dir() -> Path:
    override = os.getenv("BENCHMARK_REPORT_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "tests" / "artifacts"
