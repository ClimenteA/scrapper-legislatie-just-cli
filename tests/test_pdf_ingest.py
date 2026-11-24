"""Tests for the PDF corpus discovery, the public ``5-pdfs`` copies and actions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scrapper_legislatie_just import pdf_ingest
from scrapper_legislatie_just.answers import template_warnings
from scrapper_legislatie_just.bm25_index import Bm25IndexBuilder
from scrapper_legislatie_just.config import step_dir
from scrapper_legislatie_just.query import LegalIndex

FIXTURES = Path(__file__).parent / "fixtures" / "needle"


def _sources(tmp_path: Path) -> Path:
    decl = tmp_path / "declaratii-pdf"
    decl.mkdir()
    (decl / "300").mkdir()
    (decl / "300_simple_pdf").mkdir()
    (decl / "300" / "D300.pdf").write_bytes(b"%PDF-xfa")
    (decl / "300_simple_pdf" / "D300.pdf").write_bytes(b"%PDF-flat")
    manifest = [
        {
            "declaration": "300",
            "artifact": "pdf",
            "version": 0,
            "description": "Decont de TVA",
            "file": "300/D300.pdf",
            "url": "https://static.anaf.ro/D300.pdf",
            "xfa": True,
            "simple_pdf": "300_simple_pdf/D300.pdf",
        },
        {"declaration": "999", "error": "no form versions found"},
    ]
    (decl / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    eu = tmp_path / "eu-pdfs"
    eu.mkdir()
    (eu / "Regulamentul UE nr.282.2011.pdf").write_bytes(b"%PDF-eu")
    return tmp_path


def test_manifest_prefers_simple_pdf_for_xfa(tmp_path: Path):
    records = pdf_ingest.load_declaration_manifest(_sources(tmp_path))
    assert len(records) == 1
    assert records[0]["code"] == "300"
    assert records[0]["source_path"].parent.name == "300_simple_pdf"
    assert records[0]["xfa"] is True


def test_sources_default_to_0_pdfs_inside_data_root(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SCRAPPER_PDF_SOURCES_DIR", raising=False)
    data = tmp_path / "data"
    sources = data / "0-pdfs"
    sources.mkdir(parents=True)
    _sources(sources)

    assert pdf_ingest.resolve_pdf_sources_dir(data_root=data) == sources.resolve()
    descriptors = pdf_ingest.pdf_descriptors(data)
    # One declaration (XFA flattened) + one EU act.
    assert len(descriptors) == 2
    assert {item["kind"] for item in descriptors} == {"declaratie", "act-ue"}
    # Every descriptor has a stable Markdown file name and a synthetic number.
    assert all(item["md_name"].endswith(".md") for item in descriptors)
    assert all(item["numar_link"] >= 9_000_000 for item in descriptors)


def test_sync_publishes_pdf_under_5_pdfs(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    sources = _sources(tmp_path)
    descriptor = next(
        item
        for item in pdf_ingest.pdf_descriptors(data, sources_dir=sources)
        if item["kind"] == "declaratie"
    )
    served = pdf_ingest.sync_pdf_into_data_dir(
        descriptor["source_path"], data, descriptor["slug"]
    )
    assert served == data / "5-pdfs" / "declaratie-300" / "D300.pdf"
    assert served.read_bytes() == b"%PDF-flat"  # flattened XFA


def test_load_pdf_actions_uses_served_pdf_path(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    sources = _sources(tmp_path)
    actions = pdf_ingest.load_pdf_actions(data, sources_dir=sources)

    assert len(actions) == 1
    assert actions[0]["code"] == "300"
    assert actions[0]["pdf_rel"] == "5-pdfs/declaratie-300/D300.pdf"
    assert actions[0]["source_url"] == "https://static.anaf.ro/D300.pdf"


@pytest.fixture()
def empty_index(tmp_path: Path):
    shutil.copytree(FIXTURES / "3-expanded", tmp_path / "3-expanded")
    Bm25IndexBuilder(surse=[], data_dir=tmp_path).build()
    index = LegalIndex(data_dir=tmp_path)
    try:
        yield index
    finally:
        index.close()


def test_actions_matches_codes_with_separators(empty_index: LegalIndex):
    """A natural ``D212`` query must find a stored ``D_212_2736_2025`` code."""
    empty_index.actions_data = [
        {
            "doc_slug": "declaratie-d-212-2736-2025",
            "code": "D_212_2736_2025",
            "title": "Declaratie unica",
            "description": "impozit pe venit si contributii sociale",
            "source_url": "https://example.invalid/d212.pdf",
            "pdf_rel": "5-pdfs/declaratie-d-212-2736-2025/D_212_2736_2025.pdf",
            "xfa": 1,
        }
    ]
    rows = empty_index.actions("D212")
    assert len(rows) == 1
    assert rows[0]["code"] == "D_212_2736_2025"
    assert rows[0]["link"].endswith(
        "declaratie-d-212-2736-2025/D_212_2736_2025.pdf"
    )


def test_actions_without_index_data_returns_empty(empty_index: LegalIndex):
    empty_index.actions_data = []
    assert empty_index.actions("D300") == []


def test_template_warnings_lists_missing_sections():
    warnings = template_warnings("## TL;DR\n\nDa.")
    assert any("INTREBARE" in item for item in warnings)
    assert any("TEMEI LEGAL" in item for item in warnings)
    complete = "## INTREBARE\nx\n## TL;DR\ny\n## TEMEI LEGAL\nz\n## Sursa\ns"
    assert template_warnings(complete) == []
