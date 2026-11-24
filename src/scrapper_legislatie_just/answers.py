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
# A source citation is the act slug in backticks (``codul-fiscal``), the
# explicit SURSA label, or the act name plus a version. HTML anchors no longer
# exist: the corpus is Markdown.
_SOURCE_REF_RE = re.compile(
    r"`[a-z0-9][a-z0-9-]+`|\bSURSA\b|/pdfs/[^\s)\]]+", re.IGNORECASE
)
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_FORM_AS_ARTICLE_RE = re.compile(
    r"\b(decontul|decont|formularul|formular)\s+(\d{2,4})\b", re.IGNORECASE
)
# Links to ANAF form PDFs as returned by ``actions``. The tool returns them
# without a ``#page=N`` fragment, so a bare ``.pdf`` link is a valid citation.
_PDF_LINK_RE = re.compile(r"https?://[^\s)\]]+?\.pdf(?:#[^\s)\]]*)?", re.IGNORECASE)
# Claims that a form/provision does not exist. These deserve a second-search
# reminder: both times the model missed an existing form (registration after
# threshold, collected-VAT declaration), a synonym query in ``actions`` found
# it immediately.
_NEGATIVE_CLAIM_RE = re.compile(
    r"nu\s+(?:am\s+gasit|exista|este\s+prevazut|e\s+prevazut)\b", re.IGNORECASE
)
# Explicit numbering of questions inside the user prompt. Used only when the
# caller passes ``question`` so the audit can check that every numbered
# question got its own answer block.
_PROMPT_NUMBERED_RE = re.compile(
    r"(?:^|\n)\s*(?:intrebarea|intrebare)\s*(\d{1,2})\s*[:.\-)]", re.IGNORECASE
)
# Generic ANAF form codes: D followed by 3 digits (D300, D311, D390, D406...)
# or the legacy zero-padded codes (010, 088, 093...). No specific form is
# hard-coded here; which form applies is decided by the sources, not by us.
_FORM_CODE_RE = re.compile(r"\b(?:D\s?\d{3,4}|0\d{2,3})\b", re.IGNORECASE)
_NORME_REF_RE = re.compile(r"`?norme[a-z0-9-]*`?", re.I)
_UE_REF_RE = re.compile(r"`?ue-[a-z0-9-]+`?|/pdfs/ue-[^)\s]+", re.I)
_INACTIVITY_RE = re.compile(r"\binactiv\w*", re.I)
_CANCEL_RE = re.compile(
    r"\banula\w*\b[^.\n]{0,80}\btva\b|\btva\b[^.\n]{0,80}\banula\w*\b", re.I
)
_DEDUCTION_RE = re.compile(r"deduc\w*|drept de deducere", re.I)
_BENEFICIARY_RE = re.compile(r"beneficiar\w*|client\w*", re.I)
_EXPENSES_RE = re.compile(r"cheltuiel\w*", re.I)
_NEGATION_RE = re.compile(
    r"\bnu\b|\bfara\b|\binterzis\w*\b|\bnepermis\w*\b|\bpierd\w*\b", re.I
)

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


def _pdf_form_codes(answer: str) -> set[str]:
    """Form codes covered by PDF links anywhere in the answer.

    ``actions`` returns PDF links without a ``#page`` fragment, so both bare
    ``.pdf`` URLs and ``.pdf#page=N`` URLs count. The code is read from the
    URL path (``declaratie-311-...``, ``D300_v12...``): every 2-4 digit run
    is a candidate, which is deliberately generous — the audit only uses
    this to *suppress* false "form without link" warnings, never to invent
    a citation.
    """
    codes: set[str] = set()
    for match in _PDF_LINK_RE.finditer(answer):
        url = match.group(0)
        for digits in re.findall(r"(?<!\d)\d{2,4}(?!\d)", url):
            codes.add(digits.lstrip("0") or "0")
            codes.add(digits)
    return codes


def audit_answer(answer: str, question: str | None = None) -> list[str]:
    """Deterministic self-audit run by ``legislatie_validate_answer``.

    Checks structure, citation hygiene and the domain guards that address the
    known failure modes (VIES status, VAT-code cancellation, form vs. article).
    When the original ``question`` is passed, also checks that every numbered
    question in the prompt received its own answer block.
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
            warnings.append("TEMEI LEGAL nu contine puncte cu sursa citata.")
        for bullet in bullets:
            if not _SOURCE_REF_RE.search(bullet):
                warnings.append(
                    "Punct din TEMEI LEGAL fara sursa citata (slug in backticks "
                    "sau eticheta SURSA): " + bullet.strip()[:80]
                )
    else:
        warnings.append("Nu pot verifica sursele: lipseste sectiunea '## TEMEI LEGAL'.")

    source = bodies.get("SURSA", "")
    if source and not _DATE_RE.search(source):
        warnings.append(
            "Sectiunea Sursa nu mentioneaza o data de versiune (YYYY-MM-DD)."
        )

    linked_codes = _pdf_form_codes(answer)
    for match in _FORM_AS_ARTICLE_RE.finditer(answer):
        code = match.group(2)
        # The form may be cited on a different line (TL;DR mentions it, the
        # ACȚIUNI bullet carries the PDF link). Only flag codes with no PDF
        # link anywhere in the answer.
        if code in linked_codes or code.lstrip("0") in linked_codes:
            continue
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

    # Generic process guards. No scenario answers or act/article/form numbers
    # are encoded here: the checks only enforce the research process (family
    # coverage, explicit consequences, forms as actions) that a tax adviser
    # would apply to any question.
    text = strip_diacritics(answer)
    lowered = text.lower()

    # Family coverage (Gate A): harmonised taxes are governed by the code +
    # implementing norms + EU acts together; an answer citing only one family
    # is a search failure regardless of the scenario. Topic detection is the
    # same generic keyword map used by source selection.
    from .research import topic_families

    topics, _required = topic_families(text)
    if "tva" in topics:
        if not _NORME_REF_RE.search(answer):
            warnings.append(
                "Subiect de TVA fara citarea normelor de aplicare: regula "
                "din cod se aplica impreuna cu normele (Gate A). Daca ai "
                "cautat si nu exista text, spune explicit."
            )
        if not _UE_REF_RE.search(answer):
            warnings.append(
                "Subiect de TVA fara citarea unui act al Uniunii: TVA este "
                "armonizata, regulamentul/directiva UE se aplica direct "
                "(Gate A). Daca ai cautat si nu exista text, spune explicit."
            )

    # Status-change scenarios (cancellation/inactivity) have consequences for
    # both sides of the transaction and a special filing regime. Require the
    # answer to address them; which article/form applies comes from the
    # sources, not from this audit.
    if (_INACTIVITY_RE.search(text) or _CANCEL_RE.search(text)) and "tva" in lowered:
        actions = bodies.get("ACTIUNI", "")
        if not (_FORM_CODE_RE.search(actions) and "/pdfs/" in actions):
            warnings.append(
                "Anulare cod TVA/inactivitate cu activitate: ACTIUNI nu "
                "citeaza niciun formular ANAF cu link PDF din `actions`. "
                "Cauta in actions declaratia pentru taxa colectata in "
                "perioada cu codul anulat si precizeaza termenul de depunere."
            )
        if not _DEDUCTION_RE.search(text):
            warnings.append(
                "Anulare cod TVA/inactivitate: raspunsul nu spune nimic "
                "despre dreptul de deducere in perioada respectiva. Verifica "
                "in surse regimul deducerii TVA si al cheltuielilor."
            )
        else:
            if not _EXPENSES_RE.search(text):
                warnings.append(
                    "Anulare cod TVA/inactivitate: se mentioneaza deducerea "
                    "TVA, dar nu si deductibilitatea cheltuielilor (impozit "
                    "pe profit/venit). Ambele efecte trebuie tratate, cu sursa."
                )
            if not _BENEFICIARY_RE.search(text):
                warnings.append(
                    "Anulare cod TVA/inactivitate: lipsesc efectele pentru "
                    "beneficiarii/clientii entitatii (deducerea TVA si a "
                    "cheltuielilor aferente achizitiilor de la ea)."
                )
            if not _NEGATION_RE.search(text):
                warnings.append(
                    "Anulare cod TVA/inactivitate: efectele asupra deducerii "
                    "trebuie spuse explicit (cine NU poate deduce ce), nu "
                    "doar prin trimitere la articol."
                )

    # Completeness for multi-question prompts: when the caller passes the
    # original prompt and it numbers its questions, every numbered question
    # must have its own "### Intrebarea N" block in the answer. Without this,
    # an answer that silently drops a whole question passes the audit.
    if question:
        expected = {
            int(number)
            for number in _PROMPT_NUMBERED_RE.findall(strip_diacritics(question))
        }
        if len(expected) > 1:
            answered = {
                int(match.group(1))
                for match in re.finditer(
                    r"^#{1,6}\s+Intrebarea\s+(\d{1,2})\b",
                    strip_diacritics(answer),
                    re.IGNORECASE | re.MULTILINE,
                )
            }
            missing = sorted(expected - answered)
            if missing:
                warnings.append(
                    "Promptul numeroteaza intrebarile "
                    + ", ".join(str(n) for n in sorted(expected))
                    + ", dar raspunsul nu are blocuri '### Intrebarea N' "
                    "pentru: "
                    + ", ".join(str(n) for n in missing)
                    + ". Fiecare intrebare are nevoie de un raspuns complet."
                )

    # "Not found / does not exist" claims deserve a second search before they
    # are sent: a synonym query in `actions`/`search` often finds the form or
    # provision immediately (the audit cannot check the corpus itself).
    if _NEGATIVE_CLAIM_RE.search(text):
        warnings.append(
            "Raspunsul afirma ca ceva nu exista / nu a fost gasit. Inainte "
            "de trimitere ruleaza cel putin doua cautari cu sinonime "
            "(`actions` dupa descrierea obligatiei, `search(doc=...)` pe "
            "familia vizata); o cautare esuata nu dovedeste inexistenta."
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
