"""
Identity resolution & confidence scoring for Google Places candidates.

This module has no network code in it on purpose -- it's pure logic,
so it can be unit-tested and reasoned about independently of the
Google client.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional


GEOGRAPHY_TOKENS = (
    "JEBEL ALI", "JAFZA", "DUBAI", "UAE", "UNITED ARAB EMIRATES",
)

# Legal-suffix / free-zone noise that inflates or deflates naive string
# similarity without saying anything about identity.
_NOISE_TOKENS = {
    "FZCO", "FZE", "FZ-LLC", "FZ LLC", "LLC", "LTD", "LIMITED", "CO",
    "COMPANY", "TRADING", "GENERAL", "TRDING", "TARDING",  # source has OCR typos
}


def normalize_name(name: str) -> str:
    name = name.upper()
    name = re.sub(r"[^A-Z0-9 &]", " ", name)
    name = " ".join(name.split())
    return name


def _core_tokens(name: str) -> set[str]:
    return {t for t in normalize_name(name).split() if t not in _NOISE_TOKENS}


def name_similarity(a: str, b: str) -> float:
    """
    0.0-1.0. Blends whole-string similarity with a noise-stripped
    token-overlap score, since these company names are dominated by
    generic suffixes (TRADING FZCO, GENERAL TRADING, etc.) that make
    raw SequenceMatcher over-reward unrelated companies with the same
    suffix.
    """
    a_norm, b_norm = normalize_name(a), normalize_name(b)
    if not a_norm or not b_norm:
        return 0.0

    whole = SequenceMatcher(None, a_norm, b_norm).ratio()

    a_core, b_core = _core_tokens(a), _core_tokens(b)
    if a_core or b_core:
        overlap = len(a_core & b_core) / max(len(a_core | b_core), 1)
    else:
        overlap = whole  # both names were pure noise tokens; fall back

    return 0.4 * whole + 0.6 * overlap


def geography_score(formatted_address: Optional[str]) -> float:
    """1.0 if the address clearly places the business in Jebel Ali/
    JAFZA/Dubai, 0.5 if merely UAE, 0.0 if outside the UAE or unknown."""
    if not formatted_address:
        return 0.0
    upper = formatted_address.upper()
    if "JEBEL ALI" in upper or "JAFZA" in upper:
        return 1.0
    if "DUBAI" in upper:
        return 0.7
    if "UNITED ARAB EMIRATES" in upper or re.search(r"\bUAE\b", upper):
        return 0.4
    return 0.0


def phone_matches(source_phone: Optional[str], candidate_phone: Optional[str]) -> bool:
    if not source_phone or not candidate_phone:
        return False
    digits_a = re.sub(r"\D", "", source_phone)
    digits_b = re.sub(r"\D", "", candidate_phone)
    if len(digits_a) < 6 or len(digits_b) < 6:
        return False
    # compare last 7 digits -- tolerant of country-code / leading-zero variance
    return digits_a[-7:] == digits_b[-7:]


def domain_matches(source_email: Optional[str], candidate_website: Optional[str]) -> bool:
    if not source_email or not candidate_website or "@" not in source_email:
        return False
    email_domain = source_email.split("@", 1)[1].lower()
    generic_domains = {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "emirates.net.ae", "emi.ae",  # very common shared/ISP-style domains in this dataset
    }
    if email_domain in generic_domains:
        return False
    return email_domain in candidate_website.lower()


@dataclass
class PlaceCandidate:
    place_id: str
    name: str
    formatted_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    business_types: Optional[str] = None
    maps_url: Optional[str] = None


@dataclass
class ScoredCandidate:
    candidate: PlaceCandidate
    name_score: float
    geo_score: float
    phone_bonus: bool
    domain_bonus: bool
    total_score: float
    reasoning: str


@dataclass
class MatchResult:
    """What gets cached and what drives the output columns.
    Everything here must come from the API response or from our own
    scoring math -- never fabricated.
    """
    status: str                      # "matched" | "unresolved" | "api_error"
    confidence: str                  # "high" | "medium" | "low" | "none"
    place_id: Optional[str] = None
    name: Optional[str] = None
    formatted_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    maps_url: Optional[str] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    business_types: Optional[str] = None
    reasoning: str = ""
    candidates_considered: int = 0
    error_message: Optional[str] = None
    # Diagnostic fields -- populated even when status is "unresolved",
    # so a rejected top candidate is visible instead of a bare "no
    # candidate cleared the bar" with nothing to inspect.
    top_candidate_name: Optional[str] = None
    top_candidate_address: Optional[str] = None
    top_candidate_score: Optional[float] = None
    top_candidate_reasoning: Optional[str] = None


# Confidence thresholds on the blended 0.0-1.0 total_score.
HIGH_THRESHOLD = 0.80
MEDIUM_THRESHOLD = 0.60


def score_candidate(
    source_name: str,
    source_email: Optional[str],
    source_phone: Optional[str],
    candidate: PlaceCandidate,
) -> ScoredCandidate:
    name_score = name_similarity(source_name, candidate.name)
    geo_score = geography_score(candidate.formatted_address)
    phone_bonus = phone_matches(source_phone, candidate.phone)
    domain_bonus = domain_matches(source_email, candidate.website)

    total = 0.55 * name_score + 0.35 * geo_score
    if phone_bonus:
        total += 0.10
    if domain_bonus:
        total += 0.10
    total = min(total, 1.0)

    reasons = [f"name_sim={name_score:.2f}", f"geo={geo_score:.2f}"]
    if phone_bonus:
        reasons.append("phone_match")
    if domain_bonus:
        reasons.append("domain_match")
    reasoning = ", ".join(reasons)

    return ScoredCandidate(
        candidate=candidate,
        name_score=name_score,
        geo_score=geo_score,
        phone_bonus=phone_bonus,
        domain_bonus=domain_bonus,
        total_score=total,
        reasoning=reasoning,
    )


def pick_best(
    source_name: str,
    source_email: Optional[str],
    source_phone: Optional[str],
    candidates: list[PlaceCandidate],
) -> tuple[Optional[ScoredCandidate], list[ScoredCandidate]]:
    """Returns (best_or_None, all_scored_sorted_desc)."""
    if not candidates:
        return None, []

    scored = [
        score_candidate(source_name, source_email, source_phone, c)
        for c in candidates
    ]
    scored.sort(key=lambda s: s.total_score, reverse=True)

    best = scored[0]
    # Geography is a hard-ish gate: a great name match in the wrong
    # country should not be silently accepted (per spec: "A result
    # outside the expected geography should NOT automatically be
    # accepted simply because its name is similar.")
    if best.geo_score == 0.0 and best.name_score < 0.95:
        return None, scored

    if best.total_score < MEDIUM_THRESHOLD:
        return None, scored

    return best, scored


def confidence_label(total_score: float) -> str:
    if total_score >= HIGH_THRESHOLD:
        return "high"
    if total_score >= MEDIUM_THRESHOLD:
        return "medium"
    return "low"