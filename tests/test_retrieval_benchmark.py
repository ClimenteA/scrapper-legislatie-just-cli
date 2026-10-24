"""Compare the 3-expanded Markdown workflow with the 4-processed index.

The corpus tests skip when the downloaded corpus or the generated index is
missing. The unit test for the Markdown baseline runs everywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scrapper_legislatie_just.benchmark import (
    MarkdownBaseline,
    load_cases,
    run_benchmark,
)
from scrapper_legislatie_just.query import LegalIndex

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "legislatie-descarcata"
EXPANDED_DIR = DATA_DIR / "3-expanded"
DB_PATH = DATA_DIR / "4-processed" / "law.db"
QUESTIONS = Path(__file__).parent / "fixtures" / "real_questions.jsonl"
ARTIFACTS = Path(__file__).parent / "artifacts"

ARTICLE_HEADING = re.compile(
    r"^#{2,4}\s*Articolul\s+(\d+(?:\^\d+)?)\b",
    re.IGNORECASE | re.MULTILINE,
)

requires_corpus = pytest.mark.skipif(
    not DB_PATH.exists() or not EXPANDED_DIR.exists(),
    reason="real corpus/index not built (run 'scrapper-legislatie-just-cli process')",
)


def _write_fixture(tmp_path: Path) -> Path:
    doc_dir = tmp_path / "3-expanded" / "act-a"
    doc_dir.mkdir(parents=True)
    toc = {
        "nume": "Legea de proba",
        "descriere": "Reglementeaza scutirile pentru programatori si impozitul.",
        "slug": "act-a",
        "consolidari": [
            {
                "numar_link": 100,
                "html_name": "100-act-a-2024-01-01.html",
                "data_consolidarii": "2024-01-01",
            }
        ],
    }
    (doc_dir / "toc-act-a.json").write_text(
        json.dumps(toc), encoding="utf-8"
    )
    (doc_dir / "100-act-a-2024-01-01.md").write_text(
        "\n".join(
            [
                "# Legea de proba",
                "",
                "## Capitolul I",
                "",
                "### Articolul 1",
                "",
                "Persoanele fizice cu handicap grav beneficiaza de scutire.",
                "",
                "### Articolul 2",
                "",
                "Programatorii nu mai beneficiaza de scutirea de impozit.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_markdown_baseline_unit(tmp_path):
    root = _write_fixture(tmp_path)
    baseline = MarkdownBaseline(data_dir=root)

    selected = baseline.select_doc("scutire programatori", k=1)
    assert selected and selected[0]["slug"] == "act-a"

    hits = baseline.search("handicap scutire", doc="act-a", limit=5)
    assert hits
    assert hits[0].article_number == "1"
    assert hits[0].path and hits[0].path.endswith(".md")

    context = baseline.context("handicap scutire", doc="act-a", limit=2)
    assert "SURSA: file://" in context
    assert "handicap" in context.lower()


@requires_corpus
def test_gold_questions_are_resolvable():
    baseline = MarkdownBaseline(data_dir=DATA_DIR)
    docs = {doc["slug"]: doc for doc in baseline.docs}
    index = LegalIndex(data_dir=DATA_DIR)
    try:
        for case in load_cases(QUESTIONS):
            assert case.doc in docs, f"unknown doc in {case.question!r}"
            md_path = docs[case.doc]["md_path"]
            markdown = md_path.read_text(encoding="utf-8")
            headings = set(ARTICLE_HEADING.findall(markdown))

            for article in case.articles:
                assert article in headings, (
                    f"{case.question!r}: article {article} missing from {md_path}"
                )
                data = index.article(case.doc, article)
                assert data["link"].startswith("file://")

            if case.anchors:
                assert case.expected_terms, (
                    f"{case.question!r}: anchor cases need expected_terms "
                    "for the Markdown baseline"
                )
                html_path = md_path.with_suffix(".html")
                html = html_path.read_text(encoding="utf-8")
                for anchor in case.anchors:
                    assert (
                        f'id="{anchor}"' in html or f"id='{anchor}'" in html
                    ), f"{case.question!r}: anchor {anchor} missing from {html_path}"
    finally:
        index.close()


@requires_corpus
def test_retrieval_benchmark():
    report = run_benchmark(
        data_dir=DATA_DIR,
        cases_path=QUESTIONS,
        output_dir=ARTIFACTS,
        reps=3,
    )
    known = report["summary"]["known_doc"]
    legacy = known["legacy"]
    indexed = known["indexed"]

    print("\n== Retrieval benchmark ==")
    print(
        f"known doc: legacy hit@5={legacy['any_hit@5']:.1%} "
        f"indexed hit@5={indexed['any_hit@5']:.1%}"
    )
    print(
        f"warm search median: legacy={legacy['warm_search_ms']:.2f} ms "
        f"indexed={indexed['warm_search_ms']:.2f} ms"
    )
    print(
        f"blind doc: legacy doc@1={report['summary']['blind_doc']['legacy']['doc_hit@1']:.1%} "
        f"indexed doc@1={report['summary']['blind_doc']['indexed']['doc_hit@1']:.1%}"
    )
    print(f"report: {report['artifacts']['markdown']}")

    for row in report["rows"]:
        if row["mode"] != "known_doc":
            continue
        assert row["legacy"]["hits"] > 0, (
            f"Markdown baseline returned no evidence for {row['question']!r}"
        )
        assert row["indexed"]["hits"] > 0, (
            f"indexed method returned no evidence for {row['question']!r}"
        )

    assert indexed["any_hit@5"] >= 0.80
    assert indexed["any_hit@5"] >= legacy["any_hit@5"]
    assert Path(report["artifacts"]["json"]).exists()
    assert Path(report["artifacts"]["markdown"]).exists()
