"""Tests for the one-time PDF -> 2-clean HTML conversion step."""

from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup

from scrapper_legislatie_just import pdf_ingest, pdf_to_html
from scrapper_legislatie_just.config import step_dir


class _Prov:
    def __init__(self, page: int):
        self.page_no = page


class _Item:
    def __init__(self, text: str, label: str = "text", page: int = 2):
        self.text = text
        self.label = label
        self.prov = [_Prov(page)]


MARKDOWN = "# Decont de TVA\n\n| Camp | Valoare |\n| --- | --- |\n| Total | 100 |\n"


class _Doc:
    def export_to_markdown(self):
        return MARKDOWN

    def iterate_items(self):
        return iter(
            [
                (_Item("Regulamentul de punere in aplicare", "title", 1), 0),
                (_Item("Capitolul I", "title", 1), 1),
                (_Item("ARTICOLUL 3", "title", 2), 1),
                (_Item("Fara a aduce atingere articolului 59a", "text", 2), 0),
                (_Item("(1) Primul alineat", "text", 2), 0),
            ]
        )


def _sources(tmp_path: Path) -> Path:
    decl = tmp_path / "declaratii-pdf"
    (decl / "300").mkdir(parents=True)
    (decl / "300" / "D300.pdf").write_bytes(b"%PDF-declaration")
    (decl / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "declaration": "300",
                    "artifact": "pdf",
                    "version": 0,
                    "description": "Decont de TVA",
                    "file": "300/D300.pdf",
                    "url": "https://static.anaf.ro/D300.pdf",
                }
            ]
        ),
        encoding="utf-8",
    )
    eu = tmp_path / "eu-pdfs"
    eu.mkdir()
    (eu / "Regulamentul UE nr.282.2011.pdf").write_bytes(b"%PDF-eu")
    return tmp_path


def test_pdf_descriptors_are_stable(tmp_path: Path):
    sources = _sources(tmp_path)
    first = pdf_to_html.pdf_descriptors(tmp_path / "data", sources)
    second = pdf_to_html.pdf_descriptors(tmp_path / "data", sources)

    assert [d["slug"] for d in first] == ["declaratie-300", "ue-regulamentul-ue-nr-282-2011"]
    assert [d["html_name"] for d in first] == [d["html_name"] for d in second]
    assert all(d["date"] == pdf_to_html.PDF_DATE for d in first)


def test_convert_writes_clean_html_once(tmp_path: Path, monkeypatch):
    sources = _sources(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(pdf_ingest, "_docling_document", lambda path, ocr=False: _Doc())

    result = pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)
    assert result["converted"] == 2
    assert result["skipped"] == 0
    assert len(result["sources"]) == 2

    clean_dir = step_dir(data, "clean")
    files = sorted(clean_dir.glob("*.html"))
    assert len(files) == 2
    declaration_html = next(
        path.read_text(encoding="utf-8")
        for path in files
        if "declaratie-300" in path.name
    )
    soup = BeautifulSoup(declaration_html, "html.parser")
    # ANAF forms are Markdown wrapped in one S_PRE, not semantic HTML nodes.
    pre = soup.find("pre", class_="S_PRE")
    assert pre is not None and pre["id"] == "s_pre_1"
    assert MARKDOWN.strip() == pre.get_text()
    assert soup.find("article", class_="S_ART") is None
    assert soup.find("header", class_="S_BOOK_COVER") is not None

    # EU acts keep the full semantic rendering.
    eu_html = next(
        path.read_text(encoding="utf-8")
        for path in files
        if "ue-regulamentul" in path.name
    )
    eu_soup = BeautifulSoup(eu_html, "html.parser")
    assert eu_soup.find("article", class_="S_ART") is not None
    assert eu_soup.find("h3", class_="S_ART_TTL") is not None
    assert eu_soup.find("section", class_="S_CAP") is not None
    assert eu_soup.find("li", class_="S_ALN") is not None
    anchor = eu_soup.find("article", class_="S_ART")["id"]
    assert anchor.startswith("p2_g")

    # The served PDFs still exist for the actions tool.
    assert (data / "5-pdfs" / "declaratie-300" / "D300.pdf").read_bytes() == b"%PDF-declaration"

    # A second pass must not call Docling again.
    def fail(*args, **kwargs):
        raise AssertionError("PDF was converted twice")

    monkeypatch.setattr(pdf_ingest, "_docling_document", fail)
    again = pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)
    assert again["converted"] == 0
    assert again["skipped"] == 2


def test_old_declaration_cache_is_regenerated(tmp_path: Path, monkeypatch):
    sources = _sources(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(pdf_ingest, "_docling_document", lambda path, ocr=False: _Doc())
    pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)

    # Simulate a cache written before declaratiile became Markdown-in-<pre>.
    manifest_path = step_dir(data, "clean") / pdf_to_html.CACHE_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["declaratie-300"]["format"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)
    assert result["converted"] == 1  # only the declaration is redone
    assert result["skipped"] == 1  # the EU act cache stays valid
    new_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert new_manifest["declaratie-300"]["format"] == pdf_to_html.CACHE_FORMAT_PRE


def test_force_reconverts(tmp_path: Path, monkeypatch):
    sources = _sources(tmp_path)
    data = tmp_path / "data"
    calls = []
    monkeypatch.setattr(
        pdf_ingest,
        "_docling_document",
        lambda path, ocr=False: calls.append(path) or _Doc(),
    )
    pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)
    pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources, force=True)
    assert len(calls) == 4


def test_stale_pdf_html_is_removed(tmp_path: Path, monkeypatch):
    sources = _sources(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(pdf_ingest, "_docling_document", lambda path, ocr=False: _Doc())
    pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)
    name = next(
        path.name
        for path in step_dir(data, "clean").glob("*.html")
        if "ue-regulamentul" in path.name
    )

    (sources / "eu-pdfs" / "Regulamentul UE nr.282.2011.pdf").unlink()
    result = pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)
    assert result["removed"] == 1
    assert not (step_dir(data, "clean") / name).exists()


def test_generated_html_is_parseable_by_processor(tmp_path: Path, monkeypatch):
    from scrapper_legislatie_just.processor import LegalIndexBuilder

    sources = _sources(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(pdf_ingest, "_docling_document", lambda path, ocr=False: _Doc())
    pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)

    builder = LegalIndexBuilder(data_dir=data, surse=[])
    descriptors = pdf_to_html.pdf_descriptors(data, sources)

    def parse(descriptor):
        html_path = step_dir(data, "clean") / descriptor["html_name"]
        doc = {
            "slug": descriptor["slug"],
            "version": descriptor["date"],
            "title": descriptor["title"],
            "description": descriptor["description"],
            "html_path": html_path,
            "html_rel": "2-clean/" + descriptor["html_name"],
            "md_rel": None,
            "source_url": descriptor.get("source_url"),
        }
        return builder._parse_document(doc, {})

    declaration = next(d for d in descriptors if d["code"] == "300")
    decl_nodes = parse(declaration)
    decl_kinds = [node.kind for node in decl_nodes]
    assert "document" in decl_kinds
    assert "preformatat" in decl_kinds
    assert "articol" not in decl_kinds
    pre_node = next(node for node in decl_nodes if node.kind == "preformatat")
    assert "| Camp | Valoare |" in pre_node.text
    assert pre_node.anchor == "s_pre_1"

    eu_act = next(d for d in descriptors if d["kind"] == "act-ue")
    eu_nodes = parse(eu_act)
    eu_kinds = [node.kind for node in eu_nodes]
    assert "document" in eu_kinds
    assert "articol" in eu_kinds
    assert "capitol" in eu_kinds
    assert "alineat" in eu_kinds
    article = next(node for node in eu_nodes if node.kind == "articol")
    assert article.number == "3"


def test_pdfs_flow_through_expand_and_process_as_html(tmp_path: Path, monkeypatch):
    from scrapper_legislatie_just.expander import LegislatieJustExpander
    from scrapper_legislatie_just.processor import LegalIndexBuilder

    sources = _sources(tmp_path)
    data = tmp_path / "data"
    monkeypatch.setattr(pdf_ingest, "_docling_document", lambda path, ocr=False: _Doc())
    converted = pdf_to_html.convert_pdfs_to_clean_html(data, sources_dir=sources)
    surse = converted["sources"]

    expander = LegislatieJustExpander(surse=surse, data_dir=data)
    assert expander.expand()["copied"] == 2

    builder = LegalIndexBuilder(surse=surse, data_dir=data)
    result = builder.build()
    assert result["skipped"] is False
    assert result["documents"] == 2
    assert result["nodes"] > 2

    from scrapper_legislatie_just.query import LegalIndex

    index = LegalIndex(data_dir=data)
    try:
        rows = index.connection.execute(
            "SELECT slug, html_rel FROM docs ORDER BY slug"
        ).fetchall()
        assert [row["slug"] for row in rows] == [
            "declaratie-300",
            "ue-regulamentul-ue-nr-282-2011",
        ]
        assert all(row["html_rel"].startswith("3-expanded/") for row in rows)
        articles = index.connection.execute(
            "SELECT id FROM nodes WHERE kind = 'articol'"
        ).fetchall()
        assert len(articles) == 1  # only the EU act has legal articles
        form_nodes = index.connection.execute(
            "SELECT id FROM nodes WHERE kind = 'preformatat'"
        ).fetchall()
        assert len(form_nodes) == 1  # the declaration is Markdown in <pre>
    finally:
        index.close()


def test_load_pdf_actions_without_docling(tmp_path: Path, monkeypatch):
    sources = _sources(tmp_path)
    data = tmp_path / "data"

    def fail(*args, **kwargs):
        raise AssertionError("Docling must not run for actions")

    monkeypatch.setattr(pdf_ingest, "_docling_document", fail)
    actions = pdf_ingest.load_pdf_actions(data, sources_dir=sources)

    assert len(actions) == 1
    assert actions[0]["code"] == "300"
    assert actions[0]["pdf_rel"].endswith("D300.pdf")
