"""Race identity and candidate matching (spec section 7).

Attach an incoming poll (state, office, cycle, candidate names) to a canonical race
(``nc-sen-2026``), using:

1. **deterministic** match on state + office + cycle (+ party where known);
2. **fuzzy** match on normalized candidate names (``difflib``) when the deterministic key is
   ambiguous or names disagree;
3. an optional **LLM resolver** hook for genuinely ambiguous entity resolution -- it must return a
   mapping, confidence, rationale, and citations;
4. otherwise **abstain** (return ``None``) rather than silently attach the wrong poll.

Confidence below ``race_match_min_confidence`` (config) abstains.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional, Protocol

from app.config import get_settings
from app.providers.normalize import (
    canonical_office,
    canonical_race_id,
    last_name,
    normalize_name,
)

DETERMINISTIC = "deterministic"
FUZZY = "fuzzy"
LLM = "llm"
ABSTAIN = "abstain"


@dataclass(frozen=True)
class KnownRace:
    race_id: str
    state: str
    office: str  # senate / governor
    cycle: int
    dem_candidate: Optional[str] = None
    rep_candidate: Optional[str] = None


@dataclass(frozen=True)
class RaceMatch:
    race_id: Optional[str]
    confidence: float
    method: str  # deterministic / fuzzy / llm / abstain
    rationale: str
    citations: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.race_id is not None


class LLMRaceResolver(Protocol):
    def __call__(
        self, *, state: str, office: str, cycle: int, candidate_names: list[str], candidates: list[KnownRace]
    ) -> RaceMatch: ...


def _name_similarity(a: str, b: str) -> float:
    a_n, b_n = normalize_name(a), normalize_name(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    ratio = SequenceMatcher(None, a_n, b_n).ratio()
    if last_name(a) and last_name(a) == last_name(b):
        ratio = max(ratio, 0.9)  # shared surname is a strong signal
    return ratio


def _best_name_overlap(poll_names: Iterable[str], race: KnownRace) -> float:
    race_names = [n for n in (race.dem_candidate, race.rep_candidate) if n]
    if not race_names:
        return 0.0
    scores = []
    for pn in poll_names:
        best = max((_name_similarity(pn, rn) for rn in race_names), default=0.0)
        scores.append(best)
    return sum(scores) / len(scores) if scores else 0.0


def match_to_race(
    *,
    state: str,
    office: str,
    cycle: int,
    candidate_names: list[str] | None = None,
    known_races: list[KnownRace],
    llm_resolver: Optional[LLMRaceResolver] = None,
    min_confidence: float | None = None,
) -> RaceMatch:
    threshold = min_confidence if min_confidence is not None else get_settings().race_match_min_confidence
    off = canonical_office(office)
    st = (state or "").strip().upper()[:2]
    poll_names = [n for n in (candidate_names or []) if n and n.strip()]

    key_matches = [
        r for r in known_races
        if r.state.upper()[:2] == st and canonical_office(r.office) == off and int(r.cycle) == int(cycle)
    ]

    # 1. deterministic: exactly one race for this state/office/cycle
    if len(key_matches) == 1:
        r = key_matches[0]
        if poll_names:
            overlap = _best_name_overlap(poll_names, r)
            if overlap >= 0.5 or not (r.dem_candidate or r.rep_candidate):
                return RaceMatch(r.race_id, max(0.9, overlap), DETERMINISTIC,
                                 f"unique {st} {off} {cycle} race; candidate-name overlap {overlap:.2f}")
            # names disagree with the only candidate race -> fall through to fuzzy/abstain
        else:
            return RaceMatch(r.race_id, 0.9, DETERMINISTIC, f"unique {st} {off} {cycle} race; no candidate names on poll")

    # 2. fuzzy: pick the state/office/cycle race with the best candidate-name overlap
    if key_matches and poll_names:
        scored = sorted(((_best_name_overlap(poll_names, r), r) for r in key_matches), key=lambda x: x[0], reverse=True)
        top_score, top_race = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if top_score >= threshold and top_score - runner_up >= 0.1:
            return RaceMatch(top_race.race_id, top_score, FUZZY,
                             f"best candidate-name overlap {top_score:.2f} (next {runner_up:.2f})")

    # 3. LLM resolver for genuine ambiguity
    if llm_resolver is not None and (key_matches or known_races):
        try:
            m = llm_resolver(state=st, office=off, cycle=int(cycle), candidate_names=poll_names,
                             candidates=key_matches or known_races)
        except Exception as exc:  # a resolver failure must not attach a wrong race
            return RaceMatch(None, 0.0, ABSTAIN, f"LLM resolver errored: {exc}")
        if m.matched and m.confidence >= threshold:
            return RaceMatch(m.race_id, m.confidence, LLM, m.rationale, m.citations)
        return RaceMatch(None, m.confidence, ABSTAIN, f"LLM resolver below threshold: {m.rationale}", m.citations)

    # 4. abstain
    if not key_matches:
        return RaceMatch(None, 0.0, ABSTAIN, f"no known race for {st} {off} {cycle}")
    return RaceMatch(None, 0.0, ABSTAIN,
                     f"{len(key_matches)} candidate races for {st} {off} {cycle}; name match insufficient")


def resolve_candidate_party(
    name: str,
    *,
    dem_candidate: str | None,
    rep_candidate: str | None,
) -> Optional[str]:
    """Best-effort party for a poll answer name given the race's known nominees."""
    if not name:
        return None
    if dem_candidate and _name_similarity(name, dem_candidate) >= 0.8:
        return "DEM"
    if rep_candidate and _name_similarity(name, rep_candidate) >= 0.8:
        return "REP"
    return None


def canonical_race_id_for(state: str, office: str, cycle: int) -> str:
    return canonical_race_id(state, office, cycle)
