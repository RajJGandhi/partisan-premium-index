"""Cross-provider normalization helpers (spec sections 6, 7).

Turns provider-specific poll payloads into the canonical shapes the DB (``poll_observations``,
``national_environment_observations``) and the Quant engine expect, and computes the deterministic
identifiers used for race matching and de-duplication.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any, Optional

# --- population -----------------------------------------------------------------------------------
_POP_MAP = {
    "lv": "LV",
    "likely": "LV",
    "likely voters": "LV",
    "rv": "RV",
    "registered": "RV",
    "registered voters": "RV",
    "a": "A",
    "all": "A",
    "adults": "A",
    "adult": "A",
    "v": "RV",
    "voters": "RV",
}


# --- US state names -> USPS abbreviation (shared by market + poll providers) --------------------
STATE_ABBR: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_ABBR_SET = {v for v in STATE_ABBR.values()}


def state_to_abbr(value: Any) -> Optional[str]:
    """Full state name (any case) or an already-2-letter code -> USPS abbreviation, else None."""
    s = str(value or "").strip()
    if len(s) == 2 and s.upper() in _ABBR_SET:
        return s.upper()
    return STATE_ABBR.get(s.lower())


def normalize_population(value: Any) -> Optional[str]:
    if value is None:
        return None
    key = str(value).strip().lower()
    return _POP_MAP.get(key, None if key in {"", "unknown", "n/a"} else None)


# --- pollster grade -----------------------------------------------------------------------------
def bucket_pollster_grade(grade: Any) -> Optional[str]:
    """Return a coarse 'A'/'B'/'C' bucket the config understands, or None if unknown."""
    if grade is None:
        return None
    g = str(grade).strip().upper()
    if not g:
        return None
    first = g[0]
    if first in {"A", "B", "C"}:
        return first
    if first in {"D", "F"}:
        return "C"
    return None


# --- partisan / internal detection -------------------------------------------------------------
_PARTISAN_SPONSOR_RE = re.compile(
    r"\b(committee|pac\b|super\s?pac|for\s+(senate|governor|congress)|campaign|"
    r"republican|democrat(ic)?|gop|dccc|nrcc|dscc|nrsc|dga|rga|"
    r"action\s+fund|victory\s+fund|senate\s+leadership|congressional\s+leadership|"
    r"club\s+for\s+growth|end\s+citizens\s+united|americans\s+for)\b",
    re.IGNORECASE,
)
_INTERNAL_RE = re.compile(r"\b(internal|campaign\s+internal|released\s+by\s+the\s+campaign)\b", re.IGNORECASE)


def detect_partisan_sponsor(sponsor: Any, *, notes: str | None = None) -> Optional[str]:
    text = " ".join(x for x in (str(sponsor or ""), notes or "") if x).strip()
    if not text:
        return None
    return text if _PARTISAN_SPONSOR_RE.search(text) else None


def detect_internal(sponsor: Any, *, notes: str | None = None) -> bool:
    text = " ".join(x for x in (str(sponsor or ""), notes or "") if x)
    return bool(_INTERNAL_RE.search(text))


# --- names / race ids -------------------------------------------------------------------------
_OFFICE_MAP = {
    "senate": "sen",
    "sen": "sen",
    "us senate": "sen",
    "u.s. senate": "sen",
    "governor": "gov",
    "gov": "gov",
    "house": "house",
}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "md", "phd"}


def normalize_name(name: Any) -> str:
    """Lowercase, drop punctuation, honorifics and suffixes, collapse whitespace."""
    if not name:
        return ""
    text = re.sub(r"[^a-z\s]", " ", str(name).lower())
    tokens = [t for t in text.split() if t and t not in {"mr", "mrs", "ms", "dr", "sen", "gov", "rep"}]
    tokens = [t for t in tokens if t not in _SUFFIXES]
    return " ".join(tokens)


def last_name(name: Any) -> str:
    toks = normalize_name(name).split()
    return toks[-1] if toks else ""


def canonical_race_id(state: str, office: str, cycle: int) -> str:
    st = str(state).strip().lower()[:2]
    off = _OFFICE_MAP.get(str(office).strip().lower(), str(office).strip().lower())
    return f"{st}-{off}-{int(cycle)}"


def canonical_office(office: str) -> str:
    """Return the Quant engine's office literal ('senate'/'governor') or the lowercased input."""
    o = str(office).strip().lower()
    if o in {"sen", "senate", "us senate", "u.s. senate"}:
        return "senate"
    if o in {"gov", "governor"}:
        return "governor"
    return o


# --- de-duplication -----------------------------------------------------------------------------
def poll_content_hash(
    *,
    pollster: str,
    start_date: Optional[date],
    end_date: Optional[date],
    sample_size: Optional[int],
    population: Optional[str],
    dem_pct: float,
    rep_pct: float,
    dem_candidate: Optional[str] = None,
    rep_candidate: Optional[str] = None,
    race_id: Optional[str] = None,
) -> str:
    """Stable hash identifying a poll *release* -- two providers surfacing the same poll collide."""
    material = "|".join(
        [
            normalize_name(pollster),
            race_id or "",
            start_date.isoformat() if start_date else "",
            end_date.isoformat() if end_date else "",
            str(int(sample_size)) if sample_size else "",
            (population or "").upper(),
            f"{float(dem_pct):.1f}",
            f"{float(rep_pct):.1f}",
            last_name(dem_candidate),
            last_name(rep_candidate),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
