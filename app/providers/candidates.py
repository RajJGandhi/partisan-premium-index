"""Candidate identity / incumbency providers (spec section 8).

Fallback order: OpenFEC (federal / Senate, needs a key) -> Wikipedia election infobox (Senate +
Governor, no key) -> race-config seed -> injected web-search extractor. Output feeds
``race_candidates`` and point-in-time ``candidate_status_snapshots``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.providers.base import BaseProvider, ProviderChain, ProviderError
from app.providers.normalize import abbr_to_state, normalize_name

CANDIDATE_KIND = "candidate"


@dataclass
class CandidateRecord:
    race_id: str
    name: str
    normalized_name: str
    party: str  # DEM / REP / OTHER
    is_incumbent: bool
    candidate_status: str  # confirmed / presumptive / unconfirmed / withdrawn
    fec_candidate_id: Optional[str] = None
    committee_ids: list[str] = field(default_factory=list)
    provider: str = "seed"
    source_url: Optional[str] = None


_PARTY_MAP = {
    "DEM": "DEM", "DEMOCRAT": "DEM", "DEMOCRATIC": "DEM", "D": "DEM", "DFL": "DEM",
    "REP": "REP", "REPUBLICAN": "REP", "R": "REP", "GOP": "REP",
}


def _party(value: Any) -> str:
    return _PARTY_MAP.get(str(value or "").strip().upper(), "OTHER")


class OpenFecCandidateProvider(BaseProvider):
    """Federal candidates for a Senate race via OpenFEC ``/candidates/search/``. Disabled without a key."""

    name = "openfec_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "openfec:candidates_search"

    def __init__(self, **kw):
        super().__init__(**kw)
        s = get_settings()
        self.api_key = s.fec_api_key
        self.base_url = (s.openfec_base_url or "https://api.open.fec.gov/v1").rstrip("/")

    def enabled(self) -> bool:
        return bool(self.api_key)

    def _cache_params(self, *, race_id: str = "", state: str = "", cycle: int = 0, **kwargs) -> dict:
        return {"race_id": race_id, "state": state, "cycle": cycle}

    def _do_fetch(self, *, race_id: str, state: str, cycle: int, office: str = "senate", **kwargs):  # type: ignore[override]
        if office != "senate":
            raise ProviderError(f"{self.name}: only federal (Senate) races supported; got {office}")
        url = f"{self.base_url}/candidates/search/"
        payload, status = self._http_get_json(
            url,
            params={
                "api_key": self.api_key,
                "state": state.upper()[:2],
                "office": "S",
                "election_year": cycle,
                "cycle": cycle,
                "sort": "-first_file_date",
                "per_page": 50,
            },
        )
        if not isinstance(payload, dict) or "results" not in payload:
            raise ProviderError(f"{self.name}: unexpected OpenFEC response")
        return {"race_id": race_id, "results": payload["results"]}, url, status

    def _normalize(self, raw: Any, *, race_id: str = "", **kwargs) -> list[CandidateRecord]:
        rid = (raw or {}).get("race_id", race_id) if isinstance(raw, dict) else race_id
        results = (raw or {}).get("results", []) if isinstance(raw, dict) else []
        out: list[CandidateRecord] = []
        for c in results:
            if not isinstance(c, dict):
                continue
            party = _party(c.get("party") or c.get("party_full"))
            if party == "OTHER":
                continue
            incumbent = str(c.get("incumbent_challenge") or "").upper() == "I" or bool(c.get("incumbent"))
            active = bool(c.get("has_raised_funds", True)) and c.get("candidate_status", "C") in {"C", "N", None}
            out.append(
                CandidateRecord(
                    race_id=rid,
                    name=str(c.get("name") or "").strip(),
                    normalized_name=normalize_name(c.get("name")),
                    party=party,
                    is_incumbent=incumbent,
                    candidate_status="presumptive" if active else "unconfirmed",
                    fec_candidate_id=c.get("candidate_id"),
                    committee_ids=list(c.get("principal_committees_ids", []) or []),
                    provider=self.name,
                    source_url=f"https://www.fec.gov/data/candidate/{c.get('candidate_id')}/",
                )
            )
        # keep only the most plausible D and R nominee (highest is usually first by first_file_date desc)
        best: dict[str, CandidateRecord] = {}
        for rec in out:
            best.setdefault(rec.party, rec)
        return list(best.values())


_WIKI_API = "https://en.wikipedia.org/w/api.php"


_H = r"[^\S\n]*"  # horizontal whitespace only -- must not swallow a newline into the next field


def _infobox_field(wikitext: str, field: str) -> Optional[str]:
    """Raw value of ``| <field> = ...`` on its own line, or None if absent/empty."""
    m = re.search(rf"(?m)^{_H}\|{_H}{re.escape(field)}{_H}={_H}([^\n]*?){_H}$", wikitext)
    val = (m.group(1).strip() if m else "")
    return val or None


def _clean_wikitext_name(raw: str) -> str:
    """Turn an infobox ``nominee`` value into a plain candidate name."""
    s = raw
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)   # HTML comments (may be unclosed on the line)
    s = re.sub(r"<!--.*$", "", s, flags=re.DOTALL)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<ref[^>]*/\s*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    # [[Target|Display]] -> Display ; [[Target]] -> Target
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)          # drop templates
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\([^)]*\)\s*$", "", s).strip()    # trailing "(politician)", "(replacing X)"
    s = re.sub(r"\([^)]*\)\s*$", "", s).strip()    # ... and a second trailing "(...)" if present
    return re.sub(r"\s+", " ", s).strip(" -–—|")


def _looks_like_person_name(s: str) -> bool:
    return (
        bool(s)
        and "=" not in s
        and 3 <= len(s) <= 60
        and " " in s.strip()
        and bool(re.fullmatch(r"[A-Za-z.'\- À-ɏ]+", s))
    )


def _parse_election_infobox(wikitext: str) -> dict[str, dict]:
    """``{'DEM': {'name': ..., 'is_incumbent': bool}, 'REP': {...}}`` from an Infobox election."""
    inc_raw = _infobox_field(wikitext, "incumbent")
    incumbent = normalize_name(_clean_wikitext_name(inc_raw)) if inc_raw else ""

    out: dict[str, dict] = {}
    for i in ("1", "2", "3", "4", "5", "6"):
        party_raw = _infobox_field(wikitext, f"party{i}")
        if not party_raw:
            continue
        low = party_raw.lower()
        party = "DEM" if "democratic" in low else "REP" if "republican" in low else None
        if not party or party in out:
            continue
        nom_raw = _infobox_field(wikitext, f"nominee{i}") or _infobox_field(wikitext, f"candidate{i}")
        if not nom_raw:
            continue
        name = _clean_wikitext_name(nom_raw)
        if not _looks_like_person_name(name):
            continue
        out[party] = {"name": name, "is_incumbent": bool(incumbent) and normalize_name(name) == incumbent}
    return out


def _wiki_key(title: str) -> str:
    return title.strip().replace("_", " ").lower()


def _election_article_title(state: str, cycle: int, office: str) -> Optional[str]:
    state_name = abbr_to_state(state)
    if not state_name:
        return None
    if office == "senate":
        return f"{cycle} United States Senate election in {state_name}"
    if office == "governor":
        return f"{cycle} {state_name} gubernatorial election"
    return None


class WikipediaCandidateProvider(BaseProvider):
    """Democratic + Republican nominees from the English Wikipedia *Infobox election* for each
    race's article (Senate + Governor, no key).

    All of the run's race articles are pulled in ONE batched ``action=query`` request (Wikimedia's
    recommended pattern; avoids the anonymous-API 429), so every race in a run shares a single
    cache entry and a single HTTP call. A stub / unparseable infobox yields no record for that
    race -- never a guessed name.
    """

    name = "wikipedia_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "wikipedia:election_infobox"
    _BATCH = 40

    def __init__(self, race_configs: dict | None = None, **kw):
        super().__init__(**kw)
        # {race_id: cfg}; cfg needs state / cycle / office (canonical_office already applied upstream)
        self._configs = race_configs or {}

    def _titles_by_race(self, extra: dict | None = None) -> dict[str, str]:
        out: dict[str, str] = {}
        for rid, cfg in {**self._configs, **(extra or {})}.items():
            t = _election_article_title(
                str(cfg.get("state") or ""), int(cfg.get("cycle") or 0),
                str(cfg.get("office") or ""),
            )
            if t:
                out[rid] = t
        return out

    def _cache_params(self, *, race_id: str = "", state: str = "", cycle: int = 0, office: str = "", **kwargs) -> dict:
        # identical for every race in the run -> one shared cache entry / one HTTP call
        extra = {race_id: {"state": state, "cycle": cycle, "office": office}} if race_id else None
        return {"titles": "|".join(sorted(set(self._titles_by_race(extra).values())))}

    def _do_fetch(self, *, race_id: str = "", state: str = "", cycle: int = 0, office: str = "senate", **kwargs):
        extra = {race_id: {"state": state, "cycle": cycle, "office": office}} if race_id else None
        titles_by_race = self._titles_by_race(extra)
        if not titles_by_race:
            raise ProviderError(f"{self.name}: no state/office to build an election-article title")
        want = sorted(set(titles_by_race.values()))

        pages: dict[str, str] = {}   # _wiki_key(resolved title) -> section-0 wikitext
        alias: dict[str, str] = {}   # _wiki_key(any requested/normalized title) -> _wiki_key(resolved)
        for i in range(0, len(want), self._BATCH):
            chunk = want[i : i + self._BATCH]
            payload, status = self._http_get_json(
                _WIKI_API,
                params={
                    "action": "query", "prop": "revisions", "rvprop": "content",
                    "rvslots": "main", "rvsection": "0", "redirects": "1",
                    "titles": "|".join(chunk), "format": "json", "formatversion": "2",
                },
            )
            if not isinstance(payload, dict) or "query" not in payload:
                err = (payload or {}).get("error", {}).get("info") if isinstance(payload, dict) else None
                raise ProviderError(f"{self.name}: MediaWiki query failed" + (f" ({err})" if err else ""))
            q = payload["query"]
            for pair in q.get("normalized", []) + q.get("redirects", []):
                if pair.get("from") and pair.get("to"):
                    alias[_wiki_key(pair["from"])] = _wiki_key(pair["to"])
            for page in q.get("pages", []):
                if page.get("missing") or "revisions" not in page:
                    continue
                try:
                    content = page["revisions"][0]["slots"]["main"]["content"]
                except (KeyError, IndexError, TypeError):
                    continue
                pages[_wiki_key(page.get("title", ""))] = content

        def resolve(key: str) -> str:
            seen = set()
            while key in alias and key not in seen:
                seen.add(key)
                key = alias[key]
            return key

        by_race = {
            rid: pages[resolve(_wiki_key(t))]
            for rid, t in titles_by_race.items()
            if resolve(_wiki_key(t)) in pages
        }
        if not by_race:
            raise ProviderError(f"{self.name}: none of {len(want)} election articles returned content")
        return {"by_race": by_race, "titles_by_race": titles_by_race}, _WIKI_API, status

    def _normalize(self, raw: Any, *, race_id: str = "", **kwargs) -> list[CandidateRecord]:
        if not isinstance(raw, dict):
            return []
        wikitext = (raw.get("by_race") or {}).get(race_id)
        if not wikitext:
            return []
        title = (raw.get("titles_by_race") or {}).get(race_id, "")
        src = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}" if title else "https://en.wikipedia.org/"
        out: list[CandidateRecord] = []
        for party, info in _parse_election_infobox(str(wikitext)).items():
            out.append(
                CandidateRecord(
                    race_id=race_id,
                    name=info["name"],
                    normalized_name=normalize_name(info["name"]),
                    party=party,
                    is_incumbent=bool(info.get("is_incumbent")),
                    candidate_status="presumptive",
                    provider=self.name,
                    source_url=src,
                )
            )
        return out


class SeedCandidateProvider(BaseProvider):
    """Reads candidate metadata already attached to the race config (seed / prior ingest)."""

    name = "seed_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "seed:candidates"

    def __init__(self, race_config: dict | None = None, **kw):
        super().__init__(**kw)
        self._config = race_config or {}

    def enabled(self) -> bool:
        return bool(self._config)

    def _cache_params(self, *, race_id: str = "", **kwargs) -> dict:
        return {"race_id": race_id}

    def _do_fetch(self, *, race_id: str, **kwargs):  # type: ignore[override]
        cfg = self._config.get(race_id) or {}
        if not cfg:
            raise ProviderError(f"{self.name}: no seed candidates for {race_id}")
        return {"race_id": race_id, **cfg}, "seed:candidates", 200

    def _normalize(self, raw: Any, *, race_id: str = "", **kwargs) -> list[CandidateRecord]:
        cfg = raw if isinstance(raw, dict) else {}
        rid = cfg.get("race_id", race_id)
        out: list[CandidateRecord] = []
        for key, default_party in (("dem_candidate", "DEM"), ("rep_candidate", "REP")):
            c = cfg.get(key)
            if not c:
                continue
            if isinstance(c, str):
                c = {"name": c}
            out.append(
                CandidateRecord(
                    race_id=rid,
                    name=str(c.get("name") or "").strip(),
                    normalized_name=normalize_name(c.get("name")),
                    party=_party(c.get("party") or default_party),
                    is_incumbent=bool(c.get("is_incumbent", False)),
                    candidate_status=c.get("status", "confirmed"),
                    provider=self.name,
                    source_url=c.get("source"),
                )
            )
        return out


class WebCandidateProvider(BaseProvider):
    """Governor / missing-metadata fallback via an injected web-search extractor (spec section 45).
    Without one -> EMPTY (never a guessed candidate)."""

    name = "web_search_candidates"
    kind = CANDIDATE_KIND
    endpoint_family = "web:candidates"

    def __init__(self, extractor=None, **kw):
        super().__init__(**kw)
        self._extractor = extractor

    def enabled(self) -> bool:
        return self._extractor is not None

    def _do_fetch(self, *, race_id: str, **kwargs):  # type: ignore[override]
        assert self._extractor is not None
        return {"race_id": race_id, "results": self._extractor(race_id=race_id, **kwargs) or []}, "web_search", 200

    def _normalize(self, raw: Any, *, race_id: str = "", **kwargs) -> list[CandidateRecord]:
        rid = (raw or {}).get("race_id", race_id) if isinstance(raw, dict) else race_id
        out: list[CandidateRecord] = []
        for c in (raw or {}).get("results", []) if isinstance(raw, dict) else []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            out.append(
                CandidateRecord(
                    race_id=rid,
                    name=str(c["name"]).strip(),
                    normalized_name=normalize_name(c["name"]),
                    party=_party(c.get("party")),
                    is_incumbent=bool(c.get("is_incumbent", False)),
                    candidate_status=c.get("status", "unconfirmed"),
                    provider=self.name,
                    source_url=c.get("source"),
                )
            )
        return out


def default_candidate_chain(*, race_config: dict | None = None, web_extractor=None) -> ProviderChain:
    return ProviderChain(
        CANDIDATE_KIND,
        [
            OpenFecCandidateProvider(),
            WikipediaCandidateProvider(race_configs=race_config),
            SeedCandidateProvider(race_config=race_config),
            WebCandidateProvider(extractor=web_extractor),
        ],
    )
