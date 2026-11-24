"""Deterministic text folding shared by the index builder and the query layer.

The corpus is stored without Romanian diacritics (the cleaner already folds the
downloaded HTML, and PDF Markdown is folded on write). Queries are folded the
same way, so a user typing ``diacritice`` matches ``diacritice`` written
without them.

No embeddings and no language model: BM25 over folded, deterministically
stemmed tokens. Negations (``nu``, ``fara``) are deliberately **kept** in the
index because legal answers hinge on phrases such as ``nu se aplica``.
"""

from __future__ import annotations

import re
from typing import Any

from unidecode import unidecode

SPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[a-z0-9_]+")

# Function words with no retrieval signal. ``nu``/``fara`` stay: they carry
# the exclusion, which is exactly what a legal search must not lose.
RO_STOPWORDS = {
    "si",
    "in",
    "la",
    "de",
    "cu",
    "pe",
    "din",
    "pentru",
    "un",
    "o",
    "ai",
    "ale",
    "al",
    "lor",
    "este",
    "sunt",
    "a",
    "au",
    "care",
    "ce",
    "mai",
    "dar",
    "sau",
    "ca",
    "se",
    "prin",
    "dupa",
    "dinspre",
}


def fold(text: str) -> str:
    """Lowercase and transliterate to ASCII without touching layout."""
    return unidecode(str(text)).lower()


def normalize(text: str) -> str:
    """Fold and keep only alphanumeric words, separated by single spaces."""
    return SPACE_RE.sub(
        " ", re.sub(r"[^a-z0-9]+", " ", fold(text))
    ).strip()


def tokens(text: str, dedupe: bool = False) -> list[str]:
    """Folded, stop-word-filtered tokens in reading order.

    Documents keep repeated tokens (BM25 uses term frequency); a query is
    de-duplicated so a word repeated in the question does not weigh twice.
    """
    seen: set[str] = set()
    result: list[str] = []
    for token in TOKEN_RE.findall(fold(text)):
        if len(token) < 2 or token in RO_STOPWORDS:
            continue
        if dedupe and token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


_STEMMER: Any = None


def stem_tokens(words: list[str] | tuple[str, ...]) -> list[str]:
    """Deterministic Romanian stems for a sequence of folded tokens.

    ``PyStemmer`` is the same stemmer bundled with ``bm25s``, so the query and
    the corpus always agree. It is a local, rule-based stemmer — no model, no
    network and no state beyond the shared instance.
    """
    global _STEMMER
    if _STEMMER is None:
        from Stemmer import Stemmer

        _STEMMER = Stemmer("romanian")
    return _STEMMER.stemWords(list(words))


def indexed_text(text: str) -> str:
    """Text stored in the BM25 index: folded, stemmed content words."""
    return " ".join(stem_tokens(tokens(text)))


def query_text(query: str) -> str:
    """Fold a user question into the same token space as the documents.

    The answer/snippet layer uses this readable, unstemmed form. Retrieval
    uses :func:`stemmed_query_text` instead, matching what the index stores.
    """
    return " ".join(tokens(query, dedupe=True))


def stemmed_query_text(query: str) -> str:
    """Fold and stem a user question exactly like the indexed documents."""
    return " ".join(stem_tokens(tokens(query, dedupe=True)))


def leading_int(value: str) -> int:
    """Numeric prefix of an article number (``8^1`` -> 8)."""
    match = re.match(r"\d+", str(value))
    return int(match.group()) if match else 0
