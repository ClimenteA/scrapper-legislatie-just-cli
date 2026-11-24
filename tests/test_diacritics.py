"""Diacritics folding and the cleaner's ASCII-only output."""

from __future__ import annotations

from bs4 import BeautifulSoup

from scrapper_legislatie_just.cleaner import LegislatieJustCleaner
from scrapper_legislatie_just.config import STEP_DIRS
from scrapper_legislatie_just.misc import strip_diacritics
from scrapper_legislatie_just.sources import surse_legislatie_just

SLUG = "legea-contabilitatii"


def test_strip_diacritics_folds_romanian_letters():
    text = "Dispozițiile se aplică și raporturilor dintre profesioniști."

    assert (
        strip_diacritics(text)
        == "Dispozitiile se aplica si raporturilor dintre profesionisti."
    )


def test_strip_diacritics_keeps_table_layout_and_punctuation():
    text = "│ Total ─── „citat” – 10 €"

    # Only accented letters change; box drawing, quotes and dashes stay.
    assert strip_diacritics(text) == text
    assert strip_diacritics("ĂÂÎȘȚ șțăîâ") == "AAIST staia"


def test_query_folding_matches_the_cleaner():
    from scrapper_legislatie_just.text import query_text

    assert query_text("Dispozițiile se aplică") == "dispozitiile aplica"


def test_cleaner_writes_ascii_only_html(tmp_path):
    raw_dir = tmp_path / STEP_DIRS["raw"]
    raw_dir.mkdir(parents=True)
    raw = raw_dir / f"1576-{SLUG}-2024-01-15.html"
    raw.write_text(
        "<html><head><meta charset='UTF-8'><title>Test</title></head><body>"
        "<span class='S_ART'><span class='S_ART_TTL'>Art. 1</span>"
        "<span class='S_ART_DEN'>Dispoziții generale</span>"
        "<span class='S_ART_BDY'><span class='S_ALN'>"
        "<span class='S_ALN_TTL'>(1)</span>"
        "<span class='S_ALN_BDY'>Prezentul act se aplică și în cazul "
        "profesioniștilor.</span></span></span></span></body></html>",
        encoding="utf-8",
    )
    source = next(s for s in surse_legislatie_just if s.slug == SLUG)

    clean_result = LegislatieJustCleaner(surse=[source], data_dir=tmp_path).cleanify()

    assert clean_result == {"cleaned": 1, "skipped": 0}
    cleaned = (tmp_path / STEP_DIRS["clean"] / raw.name).read_text(encoding="utf-8")
    body_text = BeautifulSoup(cleaned, "html.parser").body.get_text(" ", strip=True)
    assert body_text.isascii()
    assert "Prezentul act se aplica si in cazul profesionistilor." in cleaned
    assert "Dispozitii generale" in cleaned
