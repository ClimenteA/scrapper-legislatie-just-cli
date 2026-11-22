"""Source selection and evidence packaging for the research protocol.

A legal answer is complete only when every relevant *family* of sources is
covered: the act itself, its implementing norms, EU acts (for harmonised
taxes) and the ANAF forms that turn an obligation into an action. This module
implements the first, mechanical step of that process:

* :func:`classify_family` groups indexed acts into families using their slug
  and title — no per-question hints;
* :meth:`LegalIndex.select_docs` narrows the catalogue down to the acts whose
  description/title match the question, keeping at least one act per relevant
  family (so a VAT question can never be answered from the code alone);
* :meth:`LegalIndex.research` runs the evidence search once per selected act
  and reports, per family, whether evidence was found — the gaps are explicit
  so the agent knows what still needs a synonym search.

Everything here is generic corpus machinery: no article numbers, form codes
or scenario answers are hard-coded.
"""

from __future__ import annotations

import math
import re
from typing import Any

from .query import _query_tokens, normalize

# Ordered family definitions: the first matching pattern wins. Patterns are
# matched against the normalized ``slug + title`` of each act. Families are
# properties of the corpus (what kind of act it is), not of any question.
FAMILY_DEFS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "formulare",
        "Formulare si declaratii ANAF",
        ("declaratie", "formular", "instructiuni completare"),
    ),
    (
        "acte-ue",
        "Acte ale Uniunii Europene",
        ("ue ", "regulamentul ue", "directiva", "regulamentul ce", "tratat",
         "uniunii europene", "gdpr"),
    ),
    (
        "norme",
        "Norme de aplicare / reglementari de detaliu",
        ("norme", "norma ", "reglementari", "instructiuni"),
    ),
    (
        "coduri",
        "Coduri",
        ("codul", "cod "),
    ),
    (
        "proceduri",
        "Proceduri, ordine si hotarari de aprobare",
        ("ordin", "opan", "omfp", "procedura", "hotarare", " hg "),
    ),
    (
        "legi",
        "Legi si ordonante",
        ("lege", "oug", "ordonanta", "og "),
    ),
    (
        "alte-acte",
        "Alte acte (cataloage, clasificari, anexe)",
        (),
    ),
)

# Topic families that must be searched together whenever the question matches
# the topic. A tax harmonised at EU level is the classic case: the code gives
# the rule, the norms give the procedure/proofs and the EU act gives the
# directly-applicable conditions; forms give the filing obligation. This is a
# statement about the legal system, not about a particular question.
TOPIC_COMPANIONS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "tva",
        ("tva", "taxa pe valoarea adaugata", "taxare inversa", "vies",
         "intracomunitar", "plafon scutire", "inregistrare in scopuri",
         "neplatitor", "neplatitori", "platitor de", "decont"),
        ("norme", "acte-ue"),
    ),
    (
        "accize",
        ("accize",),
        ("norme", "acte-ue"),
    ),
    (
        "fiscal",
        ("impozit", "contributii", "taxe", "anulat", "inactiv",
         "declaratie", "scutire", "deducere"),
        ("norme",),
    ),
)

FAMILY_LABELS: dict[str, str] = {key: label for key, label, _ in FAMILY_DEFS}

# When a topic is detected, companion acts whose slug/title mentions the
# topic's own vocabulary are the relevant ones (the VAT directive for a VAT
# question, not the GDPR). This orders candidates inside a family; it never
# invents an act that is not in the catalogue.
TOPIC_TITLE_HINTS: dict[str, tuple[str, ...]] = {
    "tva": ("tva", "282", "2006 112", "112 ce", "904"),
    "accize": ("accize",),
}

# Scenario vocabulary -> legal vocabulary. Questions narrate facts ("nu imi
# comunica un cod", "cod anulat din oficiu"); the decisive texts state
# presumptions, proof rules and obligations ("poate considera", "dovada",
# "taxa colectata"). When a trigger phrase appears in the question, its
# expansions are appended to the concept query used on the required families,
# so condition-shaped paragraphs rank even when the user never uses the legal
# wording. These are search terms, not answers: no act, article or form is
# named here.
SCENARIO_SYNONYMS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "tva",
        ("nu comunica", "nu mi-a comunicat", "nu a comunicat", "fara cod",
         "cod valid", "vies", "neimpozabila", "persoana fizica"),
        ("poate considera", "dovada", "comunicat codul", "informatii contrare"),
    ),
    (
        "tva",
        ("anulat", "anulare", "inactiv", "reactivare", "sediu social"),
        ("taxa colectata", "drept de deducere", "cheltuieli",
         "declaratie", "reinregistrare"),
    ),
    (
        "tva",
        ("plafon", "neplatitor", "scutire", "inregistrare in scopuri"),
        ("cifra de afaceri", "depasirea plafonului", "regim special",
         "solicita inregistrarea"),
    ),
    (
        "fiscal",
        ("penalitati", "amenzi", "dobanzi", "nedepunere"),
        ("contraventie", "obligatii fiscale accesorii", "penalitate de intarziere"),
    ),
)


def scenario_expansions(question: str, topics: list[str] | tuple[str, ...]) -> list[str]:
    """Extra search terms for the concept query, from scenario synonyms.

    Only triggers whose topic matches the question fire, so a labour-law
    question about a "plafon" never pulls VAT vocabulary. Terms already
    present in the normalized question are not repeated.
    """
    normalized = normalize(question)
    extra: list[str] = []
    for topic, triggers, expansions in SCENARIO_SYNONYMS:
        if topic not in topics:
            continue
        if not any(trigger in normalized for trigger in triggers):
            continue
        for term in expansions:
            if term not in normalized and term not in extra:
                extra.append(term)
    return extra


def classify_family(slug: str, title: str = "") -> str:
    """Return the family key of an act, from its slug/title only."""
    normalized = normalize(f"{slug.replace('-', ' ')} {title}")
    tokens = set(normalized.split())
    # ANAF form PDFs carry arbitrary file names ("o3711-15-a10"): the slug
    # prefix is the reliable signal, before any title keyword can mislead.
    if normalized.startswith("declaratie") or "declaratie" in tokens:
        return "formulare"
    # The fiscal procedure code is a code: check it before the generic
    # "procedura" pattern of the procedures family.
    if "procedura fiscala" in normalized:
        return "coduri"
    for key, _label, patterns in FAMILY_DEFS:
        if not patterns:
            return key  # catch-all, defined last
        for pattern in patterns:
            if " " in pattern:
                if pattern in normalized:
                    return key
            elif pattern in tokens:
                return key
    return "alte-acte"


# Acts whose own subject implements another act. When such an act makes the
# shortlist, its base act joins it: a question answered from the implementing
# norms alone misses the rule they implement, and vice versa.
IMPLEMENTING_PREFIXES = ("norme", "norma", "instructiuni")
BASE_ACT_RE = re.compile(
    r"\b(?:codul\s+\w+|codului\s+\w+|(?:legea|legii|oug|og|hg|ordonanta)\s*"
    r"(?:nr\.?\s*)?\d+(?:\s*[/ ]\s*\d{4})?)",
    re.IGNORECASE,
)


def base_act_names(doc: dict[str, Any]) -> list[str]:
    """Base acts referenced by an implementing act's slug/title/description."""
    normalized = normalize(
        f"{doc.get('slug', '').replace('-', ' ')} {doc.get('title', '')} "
        f"{doc.get('description', '')}"
    )
    tokens = normalized.split()
    if not tokens or not any(
        tokens[0].startswith(prefix) for prefix in IMPLEMENTING_PREFIXES
    ):
        return []
    names: list[str] = []
    for match in BASE_ACT_RE.finditer(normalized):
        name = match.group(0).strip()
        # Genitive ("codului fiscal") and nominative ("codul fiscal") name
        # the same act; keep both spellings as lookup candidates.
        for variant in (name, name.replace("codului", "codul")):
            if variant not in names:
                names.append(variant)
    return names


def _expand_companions(
    shortlist: list[dict[str, Any]],
    docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add the base act of every implementing act in the shortlist.

    Companion slots can displace a weakly-ranked act, so expansion may exceed
    ``max_docs`` by the number of base acts added — coverage of the rule and
    its implementing norms matters more than a strict budget.
    """
    by_name: dict[str, dict[str, Any]] = {}
    for doc in docs:
        key = normalize(f"{doc['slug'].replace('-', ' ')} {doc.get('title', '')}")
        by_name[doc["slug"]] = doc
        by_name[key] = doc
    seen = {doc["slug"] for doc in shortlist}
    extra: list[dict[str, Any]] = []
    for doc in list(shortlist):
        for name in base_act_names(doc):
            target = by_name.get(name)
            if target is None:
                # "codul fiscal" / "codului fiscal" -> slug candidate
                candidate = name.replace("codului", "codul").replace(" ", "-")
                target = by_name.get(candidate)
            if target and target["slug"] not in seen:
                seen.add(target["slug"])
                extra.append(target)
    return shortlist + extra


def topic_families(question: str) -> tuple[list[str], list[str]]:
    """Return ``(matched_topics, required_companion_families)`` for a question.

    The required families are a *minimum coverage* rule derived from the
    topic, not a promise that each family contains an answer: when a family
    turns out to have nothing, the agent must say so explicitly.
    """
    normalized = normalize(question)
    topics: list[str] = []
    required: list[str] = []
    for topic, keywords, families in TOPIC_COMPANIONS:
        if any(keyword in normalized for keyword in keywords):
            topics.append(topic)
            for family in families:
                if family not in required:
                    required.append(family)
    return topics, required


def score_docs(
    question: str, docs: list[dict[str, Any]]
) -> list[tuple[float, set[str], dict[str, Any]]]:
    """Rank catalogue entries by title+description overlap with the question.

    Uses the same tokenizer/stems as the text search so the two stages agree
    on what a match is. Two deliberate biases keep the shortlist useful:

    * matching a rare term (high IDF) earns a bonus, so the act that defines
      the specific concept outranks acts sharing only boilerplate words;
    * ANAF form metadata is formulaic, so a form needs at least two matched
      terms to qualify; forms compete on their specific words, not on the
      boilerplate every declaration shares.

    Returns ``(score, matched_terms, doc)`` triples, highest first.
    """
    terms = _query_tokens(question)
    if not terms or not docs:
        return []
    prepared = [
        (
            doc,
            normalize(f"{doc.get('slug', '').replace('-', ' ')} "
                      f"{doc.get('title', '')} {doc.get('description', '')}"),
        )
        for doc in docs
    ]
    # Document frequency over the catalogue keeps rare, specific words
    # (e.g. "telemunca") ahead of ubiquitous ones (e.g. "impozit").
    df: dict[str, int] = {}
    for _token, stem in terms:
        df[stem] = sum(1 for _doc, text in prepared if stem in text)
    idf = {
        stem: math.log(1.0 + len(docs) / df[stem])
        for _token, stem in terms
        if df.get(stem)
    }
    if not idf:
        return []
    max_idf = max(idf.values())
    rare_stems = {stem for stem, value in idf.items() if value >= 0.5 * max_idf}
    scored: list[tuple[float, set[str], dict[str, Any]]] = []
    for doc, text in prepared:
        score = 0.0
        matched: set[str] = set()
        matched_stems: set[str] = set()
        for token, stem in terms:
            weight = idf.get(stem, 0.0)
            if not weight:
                continue
            if token in text:
                score += weight
                matched.add(token)
                matched_stems.add(stem)
            elif stem in text:
                score += weight * 0.45
                matched.add(token)
                matched_stems.add(stem)
        if not matched:
            continue
        if matched_stems & rare_stems:
            # The act touches the specific concept of the question, not just
            # its generic vocabulary.
            score *= 1.5
        if doc.get("family") == "formulare" and len(matched) < 2:
            continue
        scored.append((score, matched, doc))
    scored.sort(key=lambda item: (-item[0], item[2]["slug"]))
    return scored


def select_families(
    ranked: list[tuple[float, set[str], dict[str, Any]]],
    required: list[str],
    max_docs: int = 12,
    per_family: int = 2,
    question: str = "",
    topics: tuple[str, ...] | list[str] = (),
) -> list[dict[str, Any]]:
    """Pick the shortlist: best acts overall + companions per required family.

    ``ranked`` is the output of :func:`score_docs` (each doc already carries a
    ``family`` key). The shortlist keeps the strongest matches first, then
    guarantees at least one act from every required family, so the subsequent
    per-family search has somewhere to look even when the top hits all come
    from one family. When several acts compete for a companion slot, the one
    implementing another selected act wins (its base act travels with it).
    """
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(doc: dict[str, Any]) -> None:
        if doc["slug"] not in seen and len(selected) < max_docs:
            seen.add(doc["slug"])
            selected.append(doc)

    scores = {doc["slug"]: score for score, _matched, doc in ranked}
    question_terms = _query_tokens(question)
    hints = tuple(
        hint
        for topic in topics
        for hint in TOPIC_TITLE_HINTS.get(topic, ())
    )

    def companion_key(doc: dict[str, Any]) -> tuple[int, int, int, float]:
        # Prefer the act that implements another act (its base act travels
        # with it via _expand_companions), then the one whose title carries
        # the topic's vocabulary / the question's specific terms, over
        # unrelated acts of the family.
        title = normalize(
            f"{doc['slug'].replace('-', ' ')} {doc.get('title', '')}"
        )
        topical = 0 if any(hint in title for hint in hints) else 1
        overlap = sum(1 for _token, stem in question_terms if stem in title)
        return (
            0 if base_act_names(doc) else 1,
            topical,
            -overlap,
            -scores.get(doc["slug"], 0.0),
        )

    # Reserve slots per family before the general fill, so a catalogue where
    # one family dominates the ranking (600+ formulaic form descriptions)
    # can never crowd out the acts that actually state the rules.
    family_slots = {"coduri": 3, "legi": 3, "proceduri": 1}
    for family in required:
        family_slots.setdefault(family, per_family)
    for family, slots in family_slots.items():
        candidates = [
            doc for _score, _matched, doc in ranked
            if doc.get("family") == family
        ]
        candidates.sort(key=companion_key)
        for doc in candidates[:slots]:
            add(doc)
    # Cap the form family: forms are actions, not legal grounds, and their
    # metadata is formulaic — a wall of declarations must not crowd out acts.
    form_slots = max(2, max_docs // 4)
    forms_taken = sum(1 for doc in selected if doc.get("family") == "formulare")
    for _score, _matched, doc in ranked:
        if len(selected) >= max_docs:
            break
        if doc.get("family") == "formulare":
            if forms_taken >= form_slots:
                continue
            forms_taken += 1
        add(doc)
    # Keep the overall ranking order for the final shortlist.
    order = {doc["slug"]: index for index, (_s, _m, doc) in enumerate(ranked)}
    selected.sort(key=lambda doc: order.get(doc["slug"], len(order)))
    return selected
