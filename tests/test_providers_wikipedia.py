"""Shared English-Wikipedia helpers (app/providers/wikipedia.py)."""

from __future__ import annotations

import pytest

from app.providers.wikipedia import (
    fetch_wikitext_batch,
    infobox_field,
    strip_wikitext,
    wiki_key,
)


def test_infobox_field_does_not_cross_a_newline_into_the_next_field():
    wt = "{{Infobox election\n| nominee1 = \n| party1 = Democratic Party (United States)\n}}"
    assert infobox_field(wt, "nominee1") is None          # empty value, not "party1 = ..."
    assert infobox_field(wt, "party1") == "Democratic Party (United States)"
    assert infobox_field(wt, "absent") is None


def test_strip_wikitext_removes_markup_and_comments():
    assert strip_wikitext("[[Roy Cooper]]") == "Roy Cooper"
    assert strip_wikitext("[[Target|Shown]]") == "Shown"
    assert strip_wikitext("'''2,898,423'''") == "2,898,423"
    assert strip_wikitext("Troy Jackson <!-- note -->(more)<!-- unclosed") == "Troy Jackson (more)"
    assert strip_wikitext("A{{nbsp}}B <ref>x</ref>") == "A B"


class _FakeHTTP:
    def __init__(self, pages, *, normalized=None, redirects=None):
        self._pages = pages
        self._normalized = normalized or []
        self._redirects = redirects or []
        self.calls = 0

    def __call__(self, url, params=None):
        self.calls += 1
        asked = (params or {}).get("titles", "").split("|")
        pages = []
        for t in asked:
            key = wiki_key(t)
            for norm in self._normalized:
                if wiki_key(norm["from"]) == key:
                    key = wiki_key(norm["to"])
            for red in self._redirects:
                if wiki_key(red["from"]) == key:
                    key = wiki_key(red["to"])
            if key in self._pages:
                pages.append({"title": key, "revisions": [{"slots": {"main": {"content": self._pages[key]}}}]})
            else:
                pages.append({"title": t, "missing": True})
        return {"query": {"normalized": self._normalized, "redirects": self._redirects, "pages": pages}}, 200


def test_fetch_wikitext_batch_maps_results_back_to_requested_titles():
    http = _FakeHTTP(
        {"2026 x": "AAA", "2026 z (state)": "ZZZ"},
        redirects=[{"from": "2026 z", "to": "2026 z (state)"}],
    )
    out = fetch_wikitext_batch(http, ["2026 X", "2026 z", "2026 missing"], pace_seconds=0)
    assert out == {"2026 X": "AAA", "2026 z": "ZZZ"}   # keys are exactly what was asked
    assert "2026 missing" not in out


def test_fetch_wikitext_batch_chunks_large_title_lists():
    http = _FakeHTTP({f"t{i}": f"c{i}" for i in range(120)})
    out = fetch_wikitext_batch(http, [f"t{i}" for i in range(120)], batch=50, pace_seconds=0)
    assert len(out) == 120
    assert http.calls == 3  # 50 + 50 + 20


def test_fetch_wikitext_batch_raises_when_nothing_resolves():
    http = _FakeHTTP({})
    with pytest.raises(ValueError):
        fetch_wikitext_batch(http, ["nope one", "nope two"], pace_seconds=0)
