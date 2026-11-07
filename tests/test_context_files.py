from pathlib import Path

import pytest
from typer.testing import CliRunner

from scrapper_legislatie_just import context_files
from scrapper_legislatie_just.cli import app
from scrapper_legislatie_just.context_files import (
    find_context_file,
    format_context_list,
    format_context_result,
    list_context_files,
    read_context,
)
from scrapper_legislatie_just.mcp_server import handle


def _fake_convert(path, *, ocr, pages):
    suffix = " fara OCR" if not ocr else ""
    page_text = f" pagini {pages[0]}-{pages[1]}" if pages else ""
    return f"Text OCR pentru {Path(path).name}{suffix}{page_text}"


def test_resolve_context_dir_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCRAPPER_CONTEXT_DIR", raising=False)

    from scrapper_legislatie_just.config import resolve_context_dir

    assert resolve_context_dir() == (tmp_path / "date-context").resolve()


def test_resolve_context_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRAPPER_CONTEXT_DIR", str(tmp_path / "custom"))

    from scrapper_legislatie_just.config import resolve_context_dir

    assert resolve_context_dir() == (tmp_path / "custom").resolve()


def test_list_context_files_reports_supported_and_other_files(tmp_path):
    (tmp_path / "factura.pdf").write_bytes(b"%PDF-1.4 test")
    (tmp_path / "poza.PNG").write_bytes(b"png")
    (tmp_path / "note.txt").write_text("text", encoding="utf-8")
    (tmp_path / ".ascuns.pdf").write_bytes(b"hidden")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "alt.pdf").write_bytes(b"nested")

    rows = list_context_files(tmp_path)

    assert [row["name"] for row in rows] == ["factura.pdf", "note.txt", "poza.PNG"]
    kinds = {row["name"]: row["kind"] for row in rows}
    assert kinds["factura.pdf"] == "PDF"
    assert kinds["poza.PNG"] == "imagine"
    assert kinds["note.txt"] == "nesuportat"
    assert all(row["link"].startswith("file://") for row in rows)


def test_find_context_file_accepts_bare_name_relative_and_uri(tmp_path):
    target = tmp_path / "contract.pdf"
    target.write_bytes(b"%PDF-1.4 test")

    assert find_context_file("contract.pdf", tmp_path) == target.resolve()
    assert find_context_file(target, tmp_path) == target.resolve()
    assert find_context_file(target.as_uri(), tmp_path) == target.resolve()
    assert find_context_file(f"@{target}", tmp_path) == target.resolve()
    assert (
        find_context_file(f"[contract.pdf]({target.as_uri()})", tmp_path)
        == target.resolve()
    )


def test_find_context_file_searches_subdirectories(tmp_path):
    nested = tmp_path / "scanari" / "factura.pdf"
    nested.parent.mkdir()
    nested.write_bytes(b"%PDF-1.4 test")

    assert find_context_file("factura.pdf", tmp_path) == nested.resolve()


def test_find_context_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_context_file("nu-exista.pdf", tmp_path)


def test_read_context_uses_cache(tmp_path, monkeypatch):
    source = tmp_path / "factura.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    calls = []

    def fake(path, *, ocr, pages):
        calls.append((Path(path).name, ocr, pages))
        return _fake_convert(path, ocr=ocr, pages=pages)

    monkeypatch.setattr(context_files, "_convert_with_docling", fake)

    first = read_context("factura.pdf", tmp_path)
    second = read_context("factura.pdf", tmp_path)

    assert first["cached"] is False
    assert second["cached"] is True
    assert first["markdown"] == second["markdown"] == "Text OCR pentru factura.pdf"
    assert calls == [("factura.pdf", True, None)]
    assert Path(first["cache_path"]).is_file()


def test_read_context_force_reconverts(tmp_path, monkeypatch):
    source = tmp_path / "factura.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    calls = []

    def fake(path, *, ocr, pages):
        calls.append(1)
        return f"varianta {len(calls)}"

    monkeypatch.setattr(context_files, "_convert_with_docling", fake)

    first = read_context("factura.pdf", tmp_path)
    second = read_context("factura.pdf", tmp_path, force=True)

    assert first["markdown"] == "varianta 1"
    assert second["markdown"] == "varianta 2"
    assert len(calls) == 2


def test_read_context_truncates_long_output(tmp_path, monkeypatch):
    (tmp_path / "lung.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(
        context_files,
        "_convert_with_docling",
        lambda path, *, ocr, pages: "x" * 50,
    )

    result = read_context("lung.pdf", tmp_path, max_chars=10)

    assert result["total_chars"] == 50
    assert result["returned_chars"] == 10
    assert result["truncated"] is True
    assert result["markdown"] == "x" * 10


def test_read_context_rejects_unsupported_format(tmp_path):
    (tmp_path / "note.txt").write_text("text", encoding="utf-8")

    with pytest.raises(ValueError, match="Format nesuportat"):
        read_context("note.txt", tmp_path)


def test_read_context_pages_only_for_pdf(tmp_path):
    (tmp_path / "poza.png").write_bytes(b"png")

    with pytest.raises(ValueError, match="doar fisierelor PDF"):
        read_context("poza.png", tmp_path, pages="1-2")


def test_format_context_list_empty(tmp_path):
    text = format_context_list([], tmp_path)

    assert "Nu exista fisiere" in text
    assert str(tmp_path) in text


def test_format_context_result_marks_truncation(tmp_path, monkeypatch):
    (tmp_path / "lung.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(
        context_files,
        "_convert_with_docling",
        lambda path, *, ocr, pages: "y" * 30,
    )

    text = format_context_result(read_context("lung.pdf", tmp_path, max_chars=5))

    assert "Text trunchiat: 5 din 30" in text
    assert "SURSA: file://" in text


def test_mcp_context_tools(tmp_path, monkeypatch):
    (tmp_path / "factura.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(context_files, "_convert_with_docling", _fake_convert)

    listing = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "context_files",
                "arguments": {"context_dir": str(tmp_path)},
            },
        }
    )
    assert listing is not None
    assert listing["result"]["isError"] is False
    assert "factura.pdf" in listing["result"]["content"][0]["text"]

    reading = handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "read_context",
                "arguments": {
                    "path": "factura.pdf",
                    "context_dir": str(tmp_path),
                },
            },
        }
    )
    assert reading is not None
    assert reading["result"]["isError"] is False
    assert "Text OCR pentru factura.pdf" in reading["result"]["content"][0]["text"]


def test_cli_context_files_and_read_context(tmp_path, monkeypatch):
    (tmp_path / "factura.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(context_files, "_convert_with_docling", _fake_convert)
    runner = CliRunner()

    listing = runner.invoke(app, ["context-files", "--context-dir", str(tmp_path)])
    assert listing.exit_code == 0, listing.stdout
    assert "factura.pdf" in listing.stdout

    reading = runner.invoke(
        app,
        ["read-context", "factura.pdf", "--context-dir", str(tmp_path)],
    )
    assert reading.exit_code == 0, reading.stdout
    assert "Text OCR pentru factura.pdf" in reading.stdout
