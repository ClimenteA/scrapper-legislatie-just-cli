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
    }
