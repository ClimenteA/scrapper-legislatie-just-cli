"""Needle-in-the-haystack checks against the real downloaded corpus.

These tests need a generated BM25 index and are skipped automatically when
``legislatie-descarcata/4-processed/docs.json`` is missing (for example in CI).
Build the index with the indexer container first to enable them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapper_legislatie_just.query import LegalIndex, NotFoundError

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "legislatie-descarcata"
DOCS_PATH = DATA_DIR / "4-processed" / "docs.json"
INDEX_PATH = DATA_DIR / "4-processed" / "bm25"
QUESTIONS = Path(__file__).parent / "fixtures" / "real_questions.jsonl"

pytestmark = pytest.mark.skipif(
    not (DOCS_PATH.exists() and INDEX_PATH.exists()),
    reason="real corpus index not built (run the indexer container first)",
)


def _questions() -> list[dict]:
    return [
        json.loads(line)
        for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def index():
    instance = LegalIndex(data_dir=DATA_DIR)
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize(
    "case", _questions(), ids=[case["question"] for case in _questions()]
)
def test_expected_article_in_top_results(index: LegalIndex, case: dict) -> None:
    # A short keyword query is precise enough to demand the top 10. A long
    # question adds generic words ("se", "pentru", "cum") that BM25 spreads
    # across neighbouring articles, so allow a slightly wider window.
    limit = 10 if len(case["question"].split()) <= 5 else 20
    hits = index.search(case["question"], doc=case["doc"], limit=limit)
    assert hits, f"no hit for {case['question']!r}"

    if "anchors" in case:
        # Anchors no longer exist; the retrieval signal is the article text.
        joined = " ".join(hit["text"].lower() for hit in hits)
        for term in case.get("expected_terms", []):
            assert term.lower() in joined, (
                f"{case['question']!r}: expected term {term!r} in the hits"
            )
        return

    numbers = {hit["article_number"] for hit in hits}
    expected = set(case["articles"])
    assert numbers & expected, (
        f"{case['question']!r}: expected one of {sorted(expected)}, "
        f"got {sorted(number for number in numbers if number)}"
    )


def test_caen_code_fallback_returns_neighbouring_description(index: LegalIndex) -> None:
    data = index.article("caen-rev-3", "6210")
    assert "6210" in data["text"]
    assert "softului" in data["text"].lower()


def test_every_search_hit_has_a_markdown_source(index: LegalIndex) -> None:
    for case in _questions():
        for hit in index.search(case["question"], doc=case["doc"], limit=3):
            assert hit["source"].startswith("SURSA:")
            assert hit["md_rel"].endswith(".md")
            assert (DATA_DIR / hit["md_rel"]).exists()
            assert "#" not in hit["source"].split("https://")[0]
