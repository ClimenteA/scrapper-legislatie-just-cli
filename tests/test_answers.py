from pathlib import Path

import pytest

from scrapper_legislatie_just.answers import (
    audit_answer,
    ensure_template,
    save_answer,
    slugify,
)


GOOD_ANSWER = """## INTREBARE

Test?

## TL;DR

Da.

## TEMEI LEGAL

- Conform [Codul fiscal, art. 269 alin. (1)](http://localhost:8000/laws/x.html#s_art_256) — text.

## Sursa

_Versiune consolidata: 2026-10-25._
"""


def test_audit_answer_accepts_clean_answer():
    assert audit_answer(GOOD_ANSWER) == []


def test_audit_answer_flags_missing_sections_and_bad_citations():
    bad = """## TL;DR

Da, conform [art. 269](http://localhost:8000/laws/x.html#s_art_256).

## TEMEI LEGAL

- Conform Codului fiscal, art. 269 — text.
- Scrii „decontul 323" in loc de formular.

## Sursa

_Versiune consolidata._
"""
    warnings = audit_answer(bad)
    assert any("INTREBARE" in warning for warning in warnings)
    assert any("TL;DR" in warning for warning in warnings)
    assert any("ancora exacta" in warning for warning in warnings)
    assert any("numar de articol nu este cod de formular" in warning for warning in warnings)
    assert any("YYYY-MM-DD" in warning for warning in warnings)


def test_audit_answer_accepts_form_when_actions_link_present():
    answer = GOOD_ANSWER.replace(
        "- Conform [Codul fiscal, art. 269 alin. (1)]"
        "(http://localhost:8000/laws/x.html#s_art_256) — text.",
        "- Depune [D300](http://localhost:8000/pdfs/declaratie-300/D300.pdf#page=1) "
        "conform art. 323.",
    )
    assert audit_answer(answer) == []


MULTI_QUESTION_ANSWER = """## INTREBARE

Intrebare?

### Intrebarea 1 - Subiect

**TL;DR**

Da.

**TEMEI LEGAL**

- Conform [Codul fiscal, art. 269](http://localhost:8000/laws/x.html#s_art_256) — text.

**ACTIUNI**

- Verifica.

### Intrebarea 2 - Subiect

**TL;DR**

Nu.

**TEMEI LEGAL**

- Conform [Codul fiscal, art. 310](http://localhost:8000/laws/x.html#s_art_302) — text.

**ACTIUNI**

- Verifica.

## Sursa

_Versiune consolidata: 2026-10-25._
"""


def test_audit_answer_accepts_multi_question_answer():
    assert audit_answer(MULTI_QUESTION_ANSWER) == []


def test_audit_answer_flags_single_family_tva_answer():
    answer = GOOD_ANSWER.replace("Test?", "Cum se stabileste regimul TVA?")
    assert any("Gate A" in warning for warning in audit_answer(answer))


def test_audit_answer_flags_vies_answer_without_norme_or_eu_source():
    answer = GOOD_ANSWER.replace(
        "Test?", "Clientul nu are cod VIES valid; il pot trata ca neimpozabil?"
    )
    warnings = audit_answer(answer)
    assert any("Normele metodologice" in warning for warning in warnings)
    assert any("282/2011" in warning for warning in warnings)


def test_audit_answer_flags_vat_cancellation_without_d311_and_art11():
    answer = GOOD_ANSWER.replace(
        "Test?", "Codul de TVA mi-a fost anulat din cauza inactivitatii."
    )
    warnings = audit_answer(answer)
    assert any("D311" in warning for warning in warnings)
    assert any("art. 11" in warning for warning in warnings)


def test_audit_answer_flags_form_in_actions_without_pdf_link():
    answer = GOOD_ANSWER.replace(
        "## Sursa", "## ACTIUNI\n\n- Depune D093 in SPV.\n\n## Sursa"
    )
    assert any("D093" in warning for warning in audit_answer(answer))


def test_audit_answer_accepts_form_in_actions_with_pdf_link():
    answer = GOOD_ANSWER.replace(
        "## Sursa",
        "## ACTIUNI\n\n- Depune [D311](http://localhost:8000/pdfs/"
        "declaratie-311/D311_XML_2021_290121.pdf#page=1) in SPV.\n\n## Sursa",
    )
    assert audit_answer(answer) == []


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

