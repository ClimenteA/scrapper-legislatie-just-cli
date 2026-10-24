"""Needle-in-the-haystack checks against the real downloaded corpus.

These tests need a generated index and are skipped automatically when
``legislatie-descarcata/4-processed/law.db`` is missing (for example in CI).
Run ``scrapper-legislatie-just-cli process`` first to enable them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapper_legislatie_just.query import LegalIndex

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "legislatie-descarcata"
DB_PATH = DATA_DIR / "4-processed" / "law.db"
QUESTIONS = Path(__file__).parent / "fixtures" / "real_questions.jsonl"

pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(),
    reason="real corpus index not built (run 'scrapper-legislatie-just-cli process')",
)


def _questions() -> list[dict]:
    return [
        json.loads(line)
        for line in QUESTIONS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture(scope="module")
def index():
    instance = LegalIndex(data_dir=DATA_DIR)
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize(
    "case", _questions(), ids=[case["question"] for case in _questions()]
)
def test_expected_article_in_top_ten(index: LegalIndex, case: dict) -> None:
    hits = index.search(case["question"], doc=case["doc"], limit=10)
    assert hits, f"no hit for {case['question']!r}"

    if "anchors" in case:
        anchors = {hit["link"].rsplit("#", 1)[-1] for hit in hits}
        assert anchors & set(case["anchors"]), (
            f"{case['question']!r}: expected one of {case['anchors']}, "
            f"got {sorted(anchors)}"
        )
        return

    numbers = {hit["article_number"] for hit in hits}
    expected = set(case["articles"])
    assert numbers & expected, (
        f"{case['question']!r}: expected one of {sorted(expected)}, "
        f"got {sorted(number for number in numbers if number)}"
    )


def test_caen_code_fallback_returns_neighbouring_description(index: LegalIndex) -> None:
    data = index.article("caen-rev-3", "6210")
    assert "6210" in data["text"]
    assert "softului" in data["text"].lower()
    assert data["link"].endswith("#v-1708")


def test_every_search_hit_points_at_a_real_html_anchor(index: LegalIndex) -> None:
    for case in _questions():
        for hit in index.search(case["question"], doc=case["doc"], limit=3):
            link = hit["link"]
            assert link.startswith("file://")
            path, _, anchor = link.partition("#")
            assert Path(path.removeprefix("file://")).exists()
            assert anchor, f"missing anchor in {link}"
