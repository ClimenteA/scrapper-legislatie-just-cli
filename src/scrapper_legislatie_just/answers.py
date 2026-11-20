"""Persist final legal answers as Markdown files in ``raspunsuri-ai``.

The index and the MCP server only retrieve evidence; the assistant composes
the final answer. This module provides the deterministic part of the
convention: a filesystem-safe slug derived from the question (ASCII, no
diacritics) and a writer that always keeps the ``## INTREBARE`` heading of the
answer template.

Typical use from the CLI or an MCP client::

    save_answer("Pot deduce TVA la autoturism?", "...markdown...")
    # -> ./raspunsuri-ai/pot-deduce-tva-la-autoturism.md
"""

from __future__ import annotations

import re
from pathlib import Path

from unidecode import unidecode

from .config import resolve_answers_dir
from .misc import strip_diacritics

SLUG_MAX_LENGTH = 120

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_MULTI_DASH_RE = re.compile(r"-{2,}")
_QUESTION_HEADING_RE = re.compile(
    r"^#{1,3}\s*INTREBARE\b", re.IGNORECASE | re.MULTILINE
)


def slugify(question: str) -> str:
    """Turn a question into a filename-safe ASCII slug without diacritics.

    ``"Pot deduce TVA-ul la autoturism?"`` becomes
    ``"pot-deduce-tva-ul-la-autoturism"``.
    """
    ascii_text = unidecode(str(question)).lower()
    slug = _NON_WORD_RE.sub("-", ascii_text).strip("-")
    slug = _MULTI_DASH_RE.sub("-", slug)
    if len(slug) > SLUG_MAX_LENGTH:
        slug = slug[:SLUG_MAX_LENGTH].rstrip("-")
    return slug or "intrebare"


REQUIRED_SECTIONS = (
    "INTREBARE",
    "TL;DR",
    "TEMEI LEGAL",
    "SURSA",
)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_BOLD_LABEL_RE = re.compile(r"^\*\*(.+?)\*\*\s*$", re.MULTILINE)
_ANCHOR_LINK_RE = re.compile(r"\]\([^)\s]+#[^)\s]+\)")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_FORM_AS_ARTICLE_RE = re.compile(
    r"\b(decontul|decont|formularul|formular)\s+\d{2,4}\b", re.IGNORECASE
)
_FORM_CODE_RE = re.compile(r"\b(?:D\s?\d{3,4}|0\d{2,3}|311)\b", re.IGNORECASE)
_D311_RE = re.compile(r"\b(?:D?311|declaratia 311|formularul 311)\b", re.IGNORECASE)
_NORME_LINK_RE = re.compile(r"/3-expanded/norme-metodologice-[^)\s]+#[^)\s]+", re.I)
_UE_LINK_RE = re.compile(r"/3-expanded/ue-(?:regulamentul|directiva)[^)\s]+#[^)\s]+", re.I)
_ANULARE_RE = re.compile(r"\b(?:anulat|anularea|inactiv|inactivitate)\b", re.I)
_ART11_RE = re.compile(r"art\.?\s*11\b", re.I)

_LABEL_ALIASES = {
    "intrebare": "INTREBARE",
    "tl;dr": "TL;DR",
    "temei legal": "TEMEI LEGAL",
    "exceptii / atentionari": "EXCEPTII",
    "exceptii/atentionari": "EXCEPTII",
    "exceptii": "EXCEPTII",
    "actiuni": "ACTIUNI",
    "sursa": "SURSA",
    "sursa si versiunea": "SURSA",
}


def answer_template() -> str:
    """Return the mandatory answer format so an MCP client can fetch it."""
    path = Path(__file__).with_name("templates") / "answer-format.md"
    return path.read_text(encoding="utf-8")


def _normalise_label(raw: str) -> str:
    """Turn a Markdown heading/bold label into a canonical section name."""
    text = re.sub(r"\s+", " ", strip_diacritics(raw).strip().strip("*").strip())
    lowered = text.lower()
    # "### Intrebarea 1 - ..." introduces a per-question answer block.
    if lowered.startswith("intrebarea"):
        return "INTREBARE_BLOCK"
    return _LABEL_ALIASES.get(lowered, text.upper())


def _sections(answer: str) -> list[tuple[str, int, int]]:
    """Return (label, content_start, content_end) for every section label.

    Both ``## Heading`` and the ``**TL;DR**`` labels used inside multi-question
    answers are recognised, so the self-audit no longer reports false "missing
    section" warnings for answers split on ``### Intrebarea N``.
    """
    marks: list[tuple[int, int, str]] = []
    for regex in (_HEADING_RE, _BOLD_LABEL_RE):
        for match in regex.finditer(answer):
            marks.append((match.start(), match.end(), _normalise_label(match.group(1))))
    marks.sort()
    result: list[tuple[str, int, int]] = []
    for index, (_, end, label) in enumerate(marks):
        stop = marks[index + 1][0] if index + 1 < len(marks) else len(answer)
        result.append((label, end, stop))
    return result


def template_warnings(answer: str) -> list[str]:
    """List mandatory sections missing from ``answer`` (non-blocking)."""
    present = {label for label, _, _ in _sections(answer)}
    return [
        f"Lipseste sectiunea obligatorie '## {section}'."
        for section in REQUIRED_SECTIONS
        if section not in present
    ]


def _section_bodies(answer: str) -> dict[str, str]:
    """Map each recognised section label to the text below it."""
    bodies: dict[str, str] = {}
    for label, start, end in _sections(answer):
        bodies[label] = (bodies.get(label, "") + "\n" + answer[start:end].strip()).strip()
    return bodies


def audit_answer(answer: str) -> list[str]:
    """Deterministic self-audit run by ``legislatie_validate_answer``.

    Checks structure, citation hygiene and the domain guards that address the
    known failure modes (VIES status, VAT-code cancellation, form vs. article).
    It cannot judge whether a cited article actually supports the conclusion.
    """
    warnings = template_warnings(answer)
    bodies = _section_bodies(answer)
    sections = _sections(answer)

    tldr = bodies.get("TL;DR", "")
    if tldr and ("http" in tldr.lower() or "](" in tldr):
        warnings.append("TL;DR nu trebuie sa contina linkuri.")

    legal = bodies.get("TEMEI LEGAL", "")
    if legal:
        bullets = [line for line in legal.splitlines() if line.lstrip().startswith("- ")]
        if not bullets:
            warnings.append("TEMEI LEGAL nu contine puncte cu linkuri.")
        for bullet in bullets:
            if not _ANCHOR_LINK_RE.search(bullet):
                warnings.append(
                    "Punct din TEMEI LEGAL fara link catre o ancora exacta: "
                    + bullet.strip()[:80]
                )
    else:
        warnings.append("Nu pot verifica linkurile: lipseste sectiunea '## TEMEI LEGAL'.")

    source = bodies.get("SURSA", "")
    if source and not _DATE_RE.search(source):
        warnings.append(
            "Sectiunea Sursa nu mentioneaza o data de versiune (YYYY-MM-DD)."
        )

    for match in _FORM_AS_ARTICLE_RE.finditer(answer):
        line_start = answer.rfind("\n", 0, match.start()) + 1
        line_end = answer.find("\n", match.end())
        line = answer[line_start : line_end if line_end != -1 else len(answer)]
        if "/pdfs/" not in line and "actions" not in line.lower():
            warnings.append(
                f"'{match.group(0)}' folosit ca formular fara link din "
                "`actions` (un numar de articol nu este cod de formular)."
            )

    # Every "### Intrebarea N" block must be self-contained. Block boundaries
    # are the question headings themselves, not the nested **TL;DR** labels.
    intro_marks = [
        match
        for match in _HEADING_RE.finditer(answer)
        if _normalise_label(match.group(1)) == "INTREBARE_BLOCK"
    ]
    for index, mark in enumerate(intro_marks):
        block_start = mark.end()
        block_end = (
            intro_marks[index + 1].start()
            if index + 1 < len(intro_marks)
            else len(answer)
        )
        inner = {label for label, _, _ in _sections(answer[block_start:block_end])}
        for needed in ("TL;DR", "TEMEI LEGAL", "ACTIUNI"):
            if needed not in inner:
                warnings.append(
                    f"O sub-intrebare nu are sectiunea '{needed}' "
                    "(raspunsul e incomplet)."
                )

    # Forms mentioned in ACȚIUNI must come with an ANAF PDF link.
    for label, start, end in sections:
        if label != "ACTIUNI":
            continue
        body = answer[start:end]
        codes = sorted(set(_FORM_CODE_RE.findall(body)))
        if codes and "/pdfs/" not in body and "actions" not in body.lower():
            warnings.append(
                "ACTIUNI: formular(e) "
                + ", ".join(code.replace(" ", "") for code in codes)
                + " fara link PDF returnat de `actions`."
            )

    # Domain guards for the two known problematic scenarios.
    text = strip_diacritics(answer)
    lowered = text.lower()
    if "vies" in lowered or "neimpozabil" in lowered:
        if "neimpozabil" in lowered and not _NORME_LINK_RE.search(answer):
            warnings.append(
                "Caz VIES: lipseste linkul catre Normele metodologice, pct. (9) "
                "('poate considera clientul neimpozabil')."
            )
        if not _UE_LINK_RE.search(answer):
            warnings.append(
                "Caz VIES: lipseste linkul catre Reg. 282/2011 art. 17/18 sau "
                "Directiva 2006/112/CE."
            )
    if _ANULARE_RE.search(text) and "tva" in lowered:
        if not _D311_RE.search(text):
            warnings.append(
                "Anulare cod TVA: lipseste D311 (declaratia TVA colectata datorata "
                "de persoanele cu codul anulat) din sectiunea ACTIUNI."
            )
        if not _ART11_RE.search(text):
            warnings.append(
                "Anulare cod TVA: lipseste citarea art. 11 alin. (6)/(7) privind "
                "nededucerea cheltuielilor si a TVA."
            )

    # Generic process guard: a tax answer drawn from a single source family is
    # a search failure regardless of the scenario. No act/article/form number
    # is encoded here — the check only requires family diversity (Gate A).
    if "tva" in lowered and not (
        _NORME_LINK_RE.search(answer) or _UE_LINK_RE.search(answer)
    ):
        warnings.append(
            "Subiect de TVA cu o singura familie de surse: lipseste un link "
            "catre normele de aplicare sau catre un act al Uniunii (Gate A)."
        )

    return list(dict.fromkeys(warnings))


def ensure_template(question: str, answer: str) -> str:
    """Return the answer with the ``## INTREBARE`` section guaranteed."""
    body = answer.strip()
    if not _QUESTION_HEADING_RE.search(body):
        body = f"## INTREBARE\n\n{question.strip()}\n\n{body}"
    return body.rstrip() + "\n"


def save_answer(
    question: str,
    answer: str,
    output_dir: str | Path | None = None,
    overwrite: bool = True,
) -> dict[str, str]:
    """Write ``answer`` to ``<output_dir>/<slug>.md`` and return its location.

    The file name is the question slugified without diacritics. Re-asking the
    same question overwrites the previous answer unless ``overwrite=False``.
    """
    if not str(question).strip():
        raise ValueError("Question must not be empty")
    if not str(answer).strip():
        raise ValueError("Answer must not be empty")

    directory = resolve_answers_dir(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    slug = slugify(question)
    path = directory / f"{slug}.md"
    if path.exists() and not overwrite:
        raise FileExistsError(f"Answer already exists: {path}")

    path.write_text(ensure_template(question, answer), encoding="utf-8")
    return {
        "question": str(question).strip(),
        "slug": slug,
        "path": str(path),
        "link": path.resolve().as_uri(),
        "warnings": template_warnings(answer),
    }
