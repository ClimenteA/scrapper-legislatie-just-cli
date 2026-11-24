"""Regression needles for the two pastebin questions that exposed failures.

Scenario: a VAT answer was "AI slop" because (1) the decisive EU/norms
presumption for a client without a VIES code never surfaced from the user's
narrative wording, and (2) the special collected-VAT declaration for a
cancelled VAT code was missed. These tests pin the retrieval behaviour on the
real corpus so the failures cannot silently return.

Skipped automatically when the real index is not built (e.g. in CI).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrapper_legislatie_just.query import LegalIndex
from scrapper_legislatie_just.research import scenario_expansions

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "legislatie-descarcata"
DOCS_PATH = DATA_DIR / "4-processed" / "docs.json"
INDEX_PATH = DATA_DIR / "4-processed" / "bm25"

pytestmark = pytest.mark.skipif(
    not (DOCS_PATH.exists() and INDEX_PATH.exists()),
    reason="real corpus index not built (run the indexer container first)",
)

VIES_QUESTION = (
    "Daca am un PFA, si pe PFA facturez servicii pentru o firma din UE, iar "
    "acea firma nu imi comunica un cod valid de inregistrare in scopuri de "
    "TVA verificabil in VIES, atunci trebuie sa il tratez ca o persoana "
    "fizica (persoana neimpozabila), sau statutul de persoana impozabila nu "
    "depinde de inregistrarea in scopuri de TVA?"
)
CANCELLED_QUESTION = (
    "Tocmai am avut codul de inregistrare in scopuri de TVA anulat din "
    "oficiu din cauza expirarii sediului social. Ce obligatii declarative "
    "am, penalitati, amenzi? Dupa reinnoirea sediului devin neplatitor de TVA?"
)


@pytest.fixture(scope="module")
def index():
    instance = LegalIndex(data_dir=DATA_DIR)
    try:
        yield instance
    finally:
        instance.close()


def test_scenario_expansions_fire_for_vies_narrative():
    extra = scenario_expansions(VIES_QUESTION, ["tva"])
    assert "poate considera" in extra
    assert "dovada" in extra


def test_scenario_expansions_do_not_fire_across_topics():
    # A labour-law question about a "plafon" must not pull VAT vocabulary.
    extra = scenario_expansions("plafon pentru tichete de masa", ["fiscal"])
    assert "poate considera" not in extra
    assert "cifra de afaceri" not in extra


def test_research_surfaces_the_vies_presumption_in_norme_and_eu(
    index: LegalIndex,
) -> None:
    report = index.research(VIES_QUESTION, compact=True)
    # Norms: "poate considera ... neimpozabila" presumption.
    assert "poate considera" in report, (
        "the norms presumption for a client that did not communicate its "
        "VAT code must surface from the user's narrative wording"
    )
    assert "`norme-" in report, "the norms act must be cited"
    # EU regulation 282/2011: same presumption, directly applicable.
    assert "`ue-" in report, (
        "a directly applicable EU act must surface alongside the norms rule"
    )


def test_cancelled_code_question_surfaces_the_collected_vat_declaration(
    index: LegalIndex,
) -> None:
    report = index.research(CANCELLED_QUESTION, compact=True)
    assert "declaratie-311" in report, (
        "the special declaration for VAT collected while the code was "
        "cancelled must appear in the action sheets"
    )


def test_actions_finds_collected_vat_declaration_by_obligation_words(
    index: LegalIndex,
) -> None:
    forms = index.actions("taxa colectata cod anulat", limit=5)
    assert any(form["code"].startswith("311") for form in forms)


def test_actions_finds_registration_form_by_obligation_words(
    index: LegalIndex,
) -> None:
    # The audit's "not found" guard exists because this form was missed when
    # searched by guessed code instead of by the obligation's description.
    forms = index.actions(
        "inregistrare modificarea obligatii fiscale declarative vector fiscal",
        limit=8,
    )
    assert any(form["code"].startswith("700") for form in forms), (
        f"expected form 700 among {[f['code'] for f in forms]}"
    )
