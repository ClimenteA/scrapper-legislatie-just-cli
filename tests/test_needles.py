import json
import shutil
from pathlib import Path

import pytest

from scrapper_legislatie_just.processor import LegalIndexBuilder
from scrapper_legislatie_just.query import LegalIndex

FIXTURES = Path(__file__).parent / "fixtures" / "needle"


@pytest.fixture()
def needle_index(tmp_path):
    shutil.copytree(FIXTURES / "3-expanded", tmp_path / "3-expanded")
    builder = LegalIndexBuilder(surse=[], data_dir=tmp_path)
    result = builder.build()
    index = LegalIndex(data_dir=tmp_path)
    try:
        yield builder, index, result
    finally:
        index.close()


def test_needle_finds_exact_anchor(needle_index):
    _, index, _ = needle_index
    hits = index.search("handicap accentuat opta impozitare", limit=5)
    assert hits, "the synthetic needle should be found"
    anchor = hits[0]["link"].rsplit("#", 1)[-1]
    assert anchor == "v-3"
    assert hits[0]["article_number"] == "2"
    assert hits[0]["article_link"].endswith("#s_art_2")


def test_needle_finds_article_across_synonyms(needle_index):
    _, index, _ = needle_index
    hits = index.search("scutire programatori", limit=5)
    assert hits
    assert hits[0]["article_number"] == "1"
    # The link must point at a real anchor inside the source HTML.
    assert "#" in hits[0]["link"]
    assert hits[0]["link"].startswith("file://")


def test_article_renders_children_in_order_and_note_last(needle_index):
    _, index, _ = needle_index
    data = index.article("act-a", "1")
    text = data["text"]
    assert text.startswith("**Articolul 1**")
    assert "Scutiri pentru programatori" in text
    assert "Persoanele fizice cu handicap grav" in text
    assert text.rstrip().splitlines()[-1].startswith("> **Nota:**")
    # The note must not leak into the article's own text at the top.
    assert not text.splitlines()[1].startswith("Nota")


def test_synthetic_nodes_inherit_parent_anchor(needle_index):
    builder, _, _ = needle_index
    import sqlite3

    connection = sqlite3.connect(builder.db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT anchor FROM nodes WHERE kind = 'paragraf' AND text = ?",
            ("Scutiri fara ancora proprie",),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row["anchor"] == "s_art_1"


def test_cross_document_reference_resolves_to_article(needle_index):
    builder, _, _ = needle_index
    from scrapper_legislatie_just.graph import run_cypher

    rows = run_cypher(
        builder.graph_path,
        "MATCH (a)-[r:MENTIONEAZA]->(b) WHERE a.doc = 'act-b' "
        "RETURN a.id AS src, b.id AS dst, r.rel AS rel",
    )
    assert rows, "act-b mentions act-a, so a cross-document edge is expected"
    assert any(row["dst"] == "act-a@2024-01-01#s_art_1" for row in rows)


def test_intra_document_citation_edge(needle_index):
    builder, _, _ = needle_index
    from scrapper_legislatie_just.graph import run_cypher

    rows = run_cypher(
        builder.graph_path,
        "MATCH (a)-[r:CITEAZA]->(b) "
        "WHERE a.id = 'act-a@2024-01-01#s_aln_2' "
        "RETURN a.id AS src, b.id AS dst, r.rel AS rel",
    )
    assert any(
        row["src"] == "act-a@2024-01-01#s_aln_2"
        and row["dst"] == "act-a@2024-01-01#s_art_2"
        for row in rows
    )


def test_citation_graph_is_not_duplicated_in_law_db(needle_index):
    builder, _, _ = needle_index
    import sqlite3

    connection = sqlite3.connect(builder.db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()
    assert "refs" not in tables
    assert builder.graph_path.exists()


def test_neighbors_reads_the_graph_store(needle_index):
    _, index, _ = needle_index
    data = index.neighbors("act-a@2024-01-01#s_aln_2")
    assert data["outgoing"]
    assert any(
        ref["target"] == "act-a@2024-01-01#s_art_2" for ref in data["outgoing"]
    )


def test_manifest_skips_second_build_and_rebuilds_after_change(needle_index):
    builder, _, _ = needle_index
    second = builder.build()
    assert second["skipped"] is True

    html = builder.expanded_dir / "act-a" / "100-act-a-2024-01-01.html"
    html.write_text(
        html.read_text(encoding="utf-8").replace(
            "Scutiri pentru programatori", "Scutiri pentru programatori si alte persoane"
        ),
        encoding="utf-8",
    )
    third = builder.build()
    assert third["skipped"] is False


def test_context_contains_source_lines(needle_index):
    _, index, _ = needle_index
    text = index.context("exceptie de la exceptie handicap", limit=4)
    assert "SURSA: file://" in text
    assert "handicap" in text.lower()


def test_graph_export_roundtrip(needle_index):
    graphqlite = pytest.importorskip("graphqlite")
    builder, _, _ = needle_index
    from scrapper_legislatie_just.graph import run_cypher

    graph_path = builder.graph_path
    assert graph_path.exists()

    rows = run_cypher(
        graph_path,
        "MATCH (a)-[:CITEAZA]->(b:Articol) RETURN a.label, b.label",
    )
    assert rows
    assert any(row["b.label"] == "Articolul 2" for row in rows)

    article_edges = run_cypher(
        graph_path,
        "MATCH (a:Articol)-[r:CITEAZA_ARTICOL]->(b:Articol) "
        "RETURN a.label, b.label, r.count ORDER BY r.count DESC",
    )
    assert article_edges
    assert any(
        row["a.label"] == "Articolul 1" and row["b.label"] == "Articolul 2"
        for row in article_edges
    )
