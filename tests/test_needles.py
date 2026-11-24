"""Needle-in-the-haystack checks on a tiny synthetic Markdown corpus.

The corpus is Markdown (no HTML anchors): a hit is identified by the act slug,
the consolidation version and the article number, and the ``SURSA:`` line
carries that identity.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scrapper_legislatie_just.bm25_index import Bm25IndexBuilder
from scrapper_legislatie_just.query import LegalIndex, NotFoundError

FIXTURES = Path(__file__).parent / "fixtures" / "needle"


@pytest.fixture()
def needle_index(tmp_path):
    shutil.copytree(FIXTURES / "3-expanded", tmp_path / "3-expanded")
    builder = Bm25IndexBuilder(surse=[], data_dir=tmp_path)
    result = builder.build()
    index = LegalIndex(data_dir=tmp_path)
    try:
        yield builder, index, result
    finally:
        index.close()


def test_needle_finds_the_article_text(needle_index):
    _, index, _ = needle_index
    hits = index.search("handicap accentuat opta impozitare", limit=5)
    assert hits, "the synthetic needle should be found"
    assert hits[0]["article_number"] == "2"
    assert "handicap accentuat" in hits[0]["text"]
    # Citation is act identity, not an HTML anchor.
    assert hits[0]["doc"] == "act-a"
    assert hits[0]["version"] == "2024-01-01"
    assert "`act-a`" in hits[0]["source"]
    assert "#" not in hits[0]["source"]


def test_needle_finds_article_across_synonyms(needle_index):
    _, index, _ = needle_index
    hits = index.search("scutire programatori", limit=5)
    assert hits
    assert hits[0]["article_number"] == "1"


def test_article_renders_the_full_section(needle_index):
    _, index, _ = needle_index
    data = index.article("act-a", "1")
    text = data["text"]
    assert text.startswith("### Articolul 1")
    assert "Scutiri pentru programatori" in text
    assert "Persoanele fizice cu handicap grav" in text
    assert "prevederea se aplica incepand cu 2027" in text
    assert data["doc"] == "act-a"
    assert data["version"] == "2024-01-01"


def test_missing_article_suggests_neighbours(needle_index):
    _, index, _ = needle_index
    with pytest.raises(NotFoundError, match="Articole disponibile"):
        index.article("act-a", "999")


def test_manifest_skips_second_build_and_rebuilds_after_change(needle_index):
    builder, _, _ = needle_index
    assert builder.build()["skipped"] is True

    md = builder.expanded_dir / "act-a" / "100-act-a-2024-01-01.md"
    md.write_text(
        md.read_text(encoding="utf-8").replace(
            "Scutiri pentru programatori", "Scutiri pentru programatori si alte persoane"
        ),
        encoding="utf-8",
    )
    assert builder.build()["skipped"] is False


def test_context_contains_source_lines(needle_index):
    _, index, _ = needle_index
    text = index.context("exceptie de la exceptie handicap", limit=4)
    assert "SURSA:" in text
    assert "`act-a`" in text
    assert "versiunea 2024-01-01" in text
    assert "handicap" in text.lower()
    assert "file://" not in text


def test_show_returns_the_node_from_search(needle_index):
    _, index, _ = needle_index
    hit = index.search("handicap accentuat", limit=1)[0]
    data = index.show(hit["id"])
    assert data["doc"] == "act-a"
    assert "handicap accentuat" in data["text"]


def test_docs_lists_the_latest_version(needle_index):
    _, index, _ = needle_index
    rows = index.docs()
    slugs = {row["slug"] for row in rows}
    assert slugs == {"act-a", "act-b"}
    assert all(row["is_latest"] for row in rows)
    assert all(row["source_url"].startswith("https://legislatie.just.ro/") for row in rows)
