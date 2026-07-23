from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.ppi import security


@dataclass
class FakeResponse:
    status_code: int
    headers: dict[str, str]


def test_safe_get_revalidates_redirect_target(monkeypatch):
    calls: list[str] = []

    def fake_private(host: str) -> bool:
        return host in {"127.0.0.1", "localhost"}

    def fake_get(url, **_kwargs):
        calls.append(url)
        return FakeResponse(302, {"Location": "https://127.0.0.1/admin"})

    monkeypatch.setattr(security, "_is_private_host", fake_private)
    monkeypatch.setattr(security.requests, "get", fake_get)

    with pytest.raises(ValueError, match="Private or internal"):
        security.safe_get("https://example.com/feed")
    assert calls == ["https://example.com/feed"]


def test_validate_external_url_rejects_credentials(monkeypatch):
    monkeypatch.setattr(security, "_is_private_host", lambda _host: False)
    with pytest.raises(ValueError, match="Invalid external URL"):
        security.validate_external_url("https://user:pass@example.com/feed")
