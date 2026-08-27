"""Shared helpers for the English-Wikipedia-backed providers (candidates, presidential history).

Both pull a set of article *infoboxes* in ONE batched MediaWiki ``action=query`` request -- the
Wikimedia-recommended pattern; the anonymous API 429s on rapid per-page ``action=parse`` calls.
Nothing here does I/O directly: :func:`fetch_wikitext_batch` takes the caller's
``_http_get_json`` (so every request still gets ``BaseProvider``'s retry / backoff / cache).
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

WIKI_API = "https://en.wikipedia.org/w/api.php"

_H = r"[^\S\n]*"  # horizontal whitespace only -- must not swallow a newline into the next field


def wiki_key(title: str) -> str:
    return title.strip().replace("_", " ").lower()


def infobox_field(wikitext: str, field: str) -> str | None:
    """Raw value of ``| <field> = ...`` on its own line, or None if absent / empty."""
    m = re.search(rf"(?m)^{_H}\|{_H}{re.escape(field)}{_H}={_H}([^\n]*?){_H}$", wikitext)
    val = m.group(1).strip() if m else ""
    return val or None


def strip_wikitext(s: str) -> str:
    """Drop comments, refs, templates, wikilinks and quote markup -> plain text."""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.DOTALL)
    s = re.sub(r"<!--.*$", "", s, flags=re.DOTALL)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<ref[^>]*/\s*>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]", r"\1", s)   # [[T|D]] -> D ; [[T]] -> T
    s = re.sub(r"\{\{[^{}]*\}\}", " ", s)                       # templates -> space (e.g. {{nbsp}})
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_wikitext_batch(
    http_get_json: Callable[..., tuple[Any, int]],
    titles: list[str],
    *,
    batch: int = 50,
    section: str | None = "0",
    pace_seconds: float = 0.4,
) -> dict[str, str]:
    """``{requested title: wikitext}`` for the titles that exist (``section`` 0 by default; pass
    ``None`` for the whole article).

    ``http_get_json(url, params=...)`` must return ``(payload, status)`` (i.e.
    ``BaseProvider._http_get_json``). Title normalisation and redirects are followed so the keys
    of the returned dict are exactly the strings passed in.
    """
    want = sorted({t for t in titles if t})
    pages: dict[str, str] = {}   # wiki_key(resolved title) -> wikitext
    alias: dict[str, str] = {}   # wiki_key(requested / normalised) -> wiki_key(resolved)

    for i in range(0, len(want), batch):
        if i and pace_seconds:
            time.sleep(pace_seconds)  # be gentle with the anonymous MediaWiki API
        chunk = want[i : i + batch]
        params = {
            "action": "query", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "redirects": "1",
            "titles": "|".join(chunk), "format": "json", "formatversion": "2",
        }
        if section is not None:
            params["rvsection"] = section
        payload, _status = http_get_json(WIKI_API, params=params)
        if not isinstance(payload, dict) or "query" not in payload:
            err = (payload or {}).get("error", {}).get("info") if isinstance(payload, dict) else None
            raise ValueError("MediaWiki query failed" + (f" ({err})" if err else ""))
        q = payload["query"]
        for pair in q.get("normalized", []) + q.get("redirects", []):
            if pair.get("from") and pair.get("to"):
                alias[wiki_key(pair["from"])] = wiki_key(pair["to"])
        for page in q.get("pages", []):
            if page.get("missing") or "revisions" not in page:
                continue
            try:
                pages[wiki_key(page.get("title", ""))] = page["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                continue

    def resolve(key: str) -> str:
        seen: set[str] = set()
        while key in alias and key not in seen:
            seen.add(key)
            key = alias[key]
        return key

    out = {t: pages[resolve(wiki_key(t))] for t in want if resolve(wiki_key(t)) in pages}
    if not out and want:
        raise ValueError(f"none of {len(want)} Wikipedia articles returned content")
    return out
