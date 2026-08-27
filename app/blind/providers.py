"""Blind-forecast LLM providers (spec sections 23, 24).

Each provider takes a system + user prompt and returns a :class:`BlindLLMCall` (raw text +
token/usage metadata). Parsing/validation and persistence happen in :mod:`app.blind.runner`.

- ``OpenAIBlindProvider`` / ``AnthropicBlindProvider`` -- real frontier models. The SDK is
  imported lazily; ``enabled()`` is False without both the key and the SDK, and the runner then
  records an explicit ``SKIPPED_PROVIDER`` row (never a fabricated value or a silent substitute).
- ``DeterministicBlindProvider`` -- **not a fallback**. A deterministic stub for exercising the
  runner + ensemble plumbing offline; every row it produces is flagged ``publication_status=STUB``
  and it is never in a default provider list.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.quant.types import EvidenceBundle

OPENAI = "openai"
ANTHROPIC = "anthropic"


@dataclass
class BlindLLMCall:
    raw_text: str
    model_name: str
    model_version: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    web_search_calls: int = 0
    request_summary: dict = field(default_factory=dict)


class BlindForecastProvider:
    provider_name: str = "base"
    model_name: str = "base"
    is_stub: bool = False

    def enabled(self) -> bool:  # pragma: no cover - overridden
        return False

    def generate(self, *, system: str, user: str) -> BlindLLMCall:  # pragma: no cover - interface
        raise NotImplementedError


# --------------------------------------------------------------------------------------------------
class OpenAIBlindProvider(BlindForecastProvider):
    provider_name = OPENAI

    def __init__(self, *, model: str | None = None):
        s = get_settings()
        self.model_name = model or s.openai_blind_model
        self._api_key = s.openai_api_key
        self._timeout = s.blind_request_timeout_seconds

    def enabled(self) -> bool:
        if not self._api_key:
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def generate(self, *, system: str, user: str) -> BlindLLMCall:
        import openai

        client = openai.OpenAI(api_key=self._api_key, timeout=self._timeout)
        req: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            resp = client.chat.completions.create(**req)
        except TypeError:
            # some model families reject response_format / extra params -- retry minimal
            req.pop("response_format", None)
            resp = client.chat.completions.create(**req)
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return BlindLLMCall(
            raw_text=text,
            model_name=self.model_name,
            model_version=getattr(resp, "model", self.model_name),
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            request_summary={"provider": OPENAI, "model": self.model_name, "response_format": "json_object"},
        )


class AnthropicBlindProvider(BlindForecastProvider):
    provider_name = ANTHROPIC

    def __init__(self, *, model: str | None = None):
        s = get_settings()
        self.model_name = model or s.anthropic_blind_model
        self._api_key = s.anthropic_api_key
        self._timeout = s.blind_request_timeout_seconds

    def enabled(self) -> bool:
        if not self._api_key:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def generate(self, *, system: str, user: str) -> BlindLLMCall:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout)
        req: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": 12000,
            "system": system,
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": user}],
        }
        try:
            resp = client.messages.create(**req)
        except TypeError:
            req.pop("thinking", None)
            resp = client.messages.create(**req)
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "input_tokens", None)
        completion_tokens = getattr(usage, "output_tokens", None)
        total = None
        if prompt_tokens is not None and completion_tokens is not None:
            total = prompt_tokens + completion_tokens
        return BlindLLMCall(
            raw_text=text,
            model_name=self.model_name,
            model_version=getattr(resp, "model", self.model_name),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            request_summary={"provider": ANTHROPIC, "model": self.model_name, "thinking": "adaptive"},
        )


# --------------------------------------------------------------------------------------------------
class DeterministicBlindProvider(BlindForecastProvider):
    """Offline plumbing stub. Derives a probability from the bundle deterministically; clearly
    labelled and never part of a default provider list. NOT a fallback for a real model."""

    is_stub = True
    model_name = "deterministic-benchmark-stub"

    def __init__(self, *, bundle: EvidenceBundle, standing_in_for: str, spread_pts: float = 3.0):
        self.provider_name = standing_in_for
        self._bundle = bundle
        self._spread = spread_pts

    def enabled(self) -> bool:
        return True

    def generate(self, *, system: str, user: str) -> BlindLLMCall:  # noqa: ARG002
        payload = self._bundle.payload
        fund = ((payload.get("fundamentals") or {}).get("fundamental_margin")) or 0.0
        pa = (payload.get("polling_average") or {}).get("polling_margin")
        base_margin = float(pa) if pa is not None else float(fund)
        # deterministic per-provider jitter so the two stub series differ but are reproducible
        seed = int(hashlib.sha256((self.provider_name + self._bundle.content_hash).encode()).hexdigest(), 16)
        jitter = ((seed % 2000) / 1000.0 - 1.0) * self._spread  # +/- spread_pts
        mu = base_margin + jitter
        prob = 0.5 * (1.0 + math.erf(mu / (6.0 * math.sqrt(2.0))))
        prob = min(max(prob, 0.02), 0.98)
        body = {
            "probability": round(prob, 4),
            "should_abstain": base_margin == 0.0 and pa is None,
            "rationale": f"[STUB] deterministic transform of base margin {base_margin:+.1f} with "
            f"reproducible jitter {jitter:+.1f} -> mu {mu:+.1f}.",
            "uncertainty_drivers": ["stub provider -- not a real model forecast"],
            "base_rate_notes": "[STUB]",
        }
        return BlindLLMCall(
            raw_text=json.dumps(body),
            model_name=self.model_name,
            model_version="stub",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            request_summary={"provider": self.provider_name, "model": self.model_name, "stub": True},
        )
