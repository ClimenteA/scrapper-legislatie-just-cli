"""Source-family selection and the cross-family research package.

These tests use synthetic acts only: the machinery must be generic (no
scenario answers baked in), so the fixtures are a miniature code + norms +
EU regulation + form catalogue.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scrapper_legislatie_just.processor import LegalIndexBuilder
from scrapper_legislatie_just.query import LegalIndex
from scrapper_legislatie_just.research import (
    base_act_names,
    classify_family,
    score_docs,
    select_families,
    topic_families,
)

FIXTURES = Path(__file__).parent / "fixtures" / "needle"


def _write_act(root: Path, slug: str, title: str, description: str, body: str,
               number: int = 100, date: str = "2024-01-01") -> None:
    doc_dir = root / "3-expanded" / slug
    doc_dir.mkdir(parents=True, exist_ok=True)
    html_name = f"{number}-{slug}-{date}.html"
    (doc_dir / f"toc-{slug}.json").write_text(
        json.dumps(
            {
                "nume": title,
                "descriere": description,
                "slug": slug,
                "consolidari": [
                    {
                        "numar_link": number,
                        "html_name": html_name,
                        "data_consolidarii": date,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (doc_dir / html_name).write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>{body}</body></html>",
        encoding="utf-8",
    )


@pytest.fixture()
def family_index(tmp_path):
    shutil.copytree(FIXTURES / "3-expanded", tmp_path / "3-expanded")
    _write_act(
        tmp_path,
        "codul-probe",
        "CODUL PROBE",
        "Codul probe stabileste taxa pe valoarea adaugata si scutirile.",
        """
        <article class="S_ART" id="s_art_1">
          <h3 class="S_ART_TTL">Articolul 1</h3>
          <p class="S_PAR" id="v-1">Taxa pe valoarea adaugata se aplica
          livrarilor de bunuri si prestarilor de servicii.</p>
        </article>
        """,
        number=300,
    )
    _write_act(
        tmp_path,
        "norme-metodologice-de-aplicare-a-codului-probe",
        "NORME METODOLOGICE DE APLICARE A CODULUI PROBE",
        "Detaliaza aplicarea codului probe pentru taxa pe valoarea adaugata.",
        """
        <article class="S_ART" id="s_art_1">
          <h3 class="S_ART_TTL">Articolul 1</h3>
          <p class="S_PAR" id="v-1">Pentru taxa pe valoarea adaugata,
          prestatorul poate considera clientul neimpozabil cand demonstreaza
          ca acesta nu i-a comunicat codul de inregistrare.</p>
        </article>
        """,
        number=301,
    )
    _write_act(
        tmp_path,
        "ue-regulamentul-de-punere-in-aplicare-tva-probe",
        "REGULAMENTUL DE PUNERE IN APLICARE TVA PROBE",
        "Regulament UE de punere in aplicare a taxei pe valoarea adaugata.",
        """
        <article class="S_ART" id="s_art_1">
          <h3 class="S_ART_TTL">Articolul 1</h3>
          <p class="S_PAR" id="v-1">Prestatorul poate considera clientul
          persoana impozabila cand acesta i-a comunicat codul individual de
          inregistrare in scopuri de TVA si codul este confirmat.</p>
        </article>
        """,
        number=302,
    )
    builder = LegalIndexBuilder(surse=[], data_dir=tmp_path)
    builder.build()
    index = LegalIndex(data_dir=tmp_path)
    try:
        yield index
    finally:
        index.close()


def test_classify_family_is_generic():
    assert classify_family("codul-fiscal", "CODUL FISCAL") == "coduri"
    assert classify_family("cod-procedura-fiscala", "CODUL DE PROCEDURA FISCALA") == "coduri"
    assert classify_family(
        "norme-metodologice-de-aplicare-a-codului-fiscal",
        "NORME METODOLOGICE",
    ) == "norme"
    assert classify_family(
        "ue-regulamentul-de-punere-in-aplicare-tva-ue-nr-282-2011",
        "REGULAMENTUL DE PUNERE IN APLICARE TVA UE NR.282.2011",
    ) == "acte-ue"
    assert classify_family("gdpr-protectia-datelor", "REGULAMENT (UE) NR. 679") == "acte-ue"
    assert classify_family("declaratie-311-opanaf-779-2024", "311 - Declaratie") == "formulare"
    # An arbitrary form file name must not be misread as a code.
    assert classify_family("declaratie-o3711-15-a10", "COD FISCAL something") == "formulare"
    assert classify_family("lege-telemunca", "LEGEA NR. 81/2018") == "legi"


def test_topic_families_requires_companions_for_harmonised_taxes():
    topics, required = topic_families("Cum facturez TVA pentru un client din UE?")
    assert "tva" in topics
    assert "norme" in required and "acte-ue" in required
    topics, required = topic_families("Ce este telemunca?")
    assert topics == [] and required == []


def test_base_act_names_links_norms_to_their_code():
    doc = {
        "slug": "norme-metodologice-de-aplicare-a-codului-probe",
        "title": "NORME METODOLOGICE DE APLICARE A CODULUI PROBE",
        "description": "",
    }
    assert "codul probe" in base_act_names(doc)
    assert base_act_names({"slug": "codul-probe", "title": "CODUL PROBE"}) == []


def test_select_docs_covers_all_required_families(family_index):
    plan = family_index.select_docs(
        "Prestator TVA client UE fara cod de inregistrare comunicat"
    )
    families = {doc["family"] for doc in plan["docs"]}
    assert plan["missing_families"] == []
    assert {"coduri", "norme", "acte-ue"} <= families
    slugs = {doc["slug"] for doc in plan["docs"]}
    # The norms travel with their base code even when ranked low.
    assert "codul-probe" in slugs
    assert "norme-metodologice-de-aplicare-a-codului-probe" in slugs


def test_research_reports_evidence_per_family_and_gaps(family_index):
    text = family_index.research(
        "Prestator TVA client UE fara cod de inregistrare comunicat", limit=2
    )
    assert "## [Coduri]" in text
    assert "## [Norme de aplicare" in text
    assert "## [Acte ale Uniunii Europene]" in text
    assert "nu i-a comunicat codul" in text
    assert "bilanț pe familii" in text
    assert "SURSA: file://" in text


def test_research_compact_keeps_anchors_but_drops_full_text(family_index):
    question = "Prestator TVA client UE fara cod de inregistrare comunicat"
    verbose = family_index.research(question, limit=2)
    compact = family_index.research(question, limit=2, compact=True)
    assert len(compact) < len(verbose)
    assert "nu i-a comunicat codul" in compact
    assert "SURSA: file://" in compact
    assert "bilanț pe familii" in compact
    assert "Articolul integral" not in compact
    assert "Mod compact" in compact


def test_research_flags_family_without_evidence(tmp_path):
    # A corpus with no EU act at all: a VAT question still requires the EU
    # family, so the balance sheet must flag the gap explicitly instead of
    # silently answering from the remaining acts.
    shutil.copytree(FIXTURES / "3-expanded", tmp_path / "3-expanded")
    LegalIndexBuilder(surse=[], data_dir=tmp_path).build()
    index = LegalIndex(data_dir=tmp_path)
    try:
        text = index.research("TVA scutire impozit", limit=2)
        assert "FĂRĂ dovezi" in text
        assert "Acte ale Uniunii Europene" in text
    finally:
        index.close()


def test_context_reserves_slots_for_companion_families(family_index):
    text = family_index.context(
        "Prestator TVA client UE fara cod de inregistrare comunicat", limit=3
    )
    assert "norme-metodologice-de-aplicare-a-codului-probe" in text
    assert "ue-regulamentul-de-punere-in-aplicare-tva-probe" in text


def test_score_docs_prefers_specific_concept_over_boilerplate():
    docs = [
        {"slug": "a", "title": "ACT A", "description": "inregistrare declaratii",
         "family": "legi"},
        {"slug": "b", "title": "ACT B", "description": "telemunca munca la distanta",
         "family": "legi"},
    ]
    ranked = score_docs("telemunca", docs)
    assert ranked and ranked[0][2]["slug"] == "b"


def test_select_families_caps_the_form_wall():
    ranked = [
        (10.0 - i, {"tva"}, {
            "slug": f"declaratie-{i}", "title": f"DECLARATIE {i}",
            "description": "tva inregistrare", "family": "formulare",
        })
        for i in range(20)
    ] + [(1.0, {"tva"}, {"slug": "codul-x", "title": "CODUL X",
                        "description": "tva", "family": "coduri"})]
    selected = select_families(ranked, ["norme", "acte-ue"], max_docs=10)
    forms = [doc for doc in selected if doc["family"] == "formulare"]
    assert len(forms) <= 3
    assert any(doc["slug"] == "codul-x" for doc in selected)
