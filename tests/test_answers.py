from pathlib import Path

import pytest
from typer.testing import CliRunner

from scrapper_legislatie_just.answers import ensure_template, save_answer, slugify
from scrapper_legislatie_just.cli import app
from scrapper_legislatie_just.mcp_server import handle


def test_slugify_removes_diacritics_and_punctuation():
    assert (
        slugify("Pot deduce TVA-ul la autoturism?")
        == "pot-deduce-tva-ul-la-autoturism"
    )
    assert slugify("Ce spune art. 60 despre șomaj?") == "ce-spune-art-60-despre-somaj"
    assert slugify("Ăâîșț") == "aaist"
    assert slugify("   ") == "intrebare"


def test_slugify_limits_length():
    slug = slugify("cuvant " * 100)
    assert len(slug) <= 120
    assert not slug.endswith("-")


def test_ensure_template_adds_question_heading():
    text = ensure_template("Intrebarea mea?", "## TL;DR\n\nDa.")
    assert text.startswith("## INTREBARE\n\nIntrebarea mea?")
    assert "## TL;DR" in text
    assert text.endswith("\n")


def test_ensure_template_keeps_existing_question_heading():
    answer = "## INTREBARE\n\nIntrebarea mea?\n\n## TL;DR\n\nNu."
    assert ensure_template("Intrebarea mea?", answer) == answer + "\n"


def test_save_answer_writes_slug_file(tmp_path: Path):
    result = save_answer(
        "Pot deduce TVA la autoturism?",
        "## TL;DR\n\nDa.",
        output_dir=tmp_path,
    )

    path = tmp_path / "pot-deduce-tva-la-autoturism.md"
    assert result["path"] == str(path)
    assert result["slug"] == "pot-deduce-tva-la-autoturism"
    assert result["link"] == path.resolve().as_uri()
    assert path.read_text(encoding="utf-8").startswith("## INTREBARE")


def test_save_answer_can_refuse_overwrite(tmp_path: Path):
    save_answer("Aceeasi intrebare", "unu", output_dir=tmp_path)
    with pytest.raises(FileExistsError):
        save_answer(
            "Aceeasi intrebare",
            "doi",
            output_dir=tmp_path,
            overwrite=False,
        )
    assert (tmp_path / "aceeasi-intrebare.md").read_text(encoding="utf-8") == (
        "## INTREBARE\n\nAceeasi intrebare\n\nunu\n"
    )


def test_cli_save_answer(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "save-answer",
            "Ce spune art. 60?",
            "--answer",
            "## TL;DR\n\nDa.",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "ce-spune-art-60.md").exists()


def test_mcp_save_answer_tool(tmp_path: Path):
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "save_answer",
                "arguments": {
                    "question": "Test intrebare?",
                    "answer": "## TL;DR\n\nDa.",
                    "output_dir": str(tmp_path),
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is False
    assert "raspuns salvat" in response["result"]["content"][0]["text"].lower()
    assert (tmp_path / "test-intrebare.md").exists()
