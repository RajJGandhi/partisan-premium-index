from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Literal, cast

import requests
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings


class EvidenceClassification(BaseModel):
    relevant: bool
    relevance_score: float = Field(ge=0.0, le=1.0)
    source_quality: float = Field(ge=0.0, le=1.0)
    changes_probability: bool
    direction: Literal["YES", "NO", "MIXED", "NONE"]
    estimated_magnitude: float = Field(ge=-0.20, le=0.20)
    category: Literal[
        "polling",
        "forecast",
        "ballot_access",
        "candidacy",
        "rules",
        "resolution",
        "endorsement",
        "fundraising",
        "campaign",
        "news",
        "official",
        "other",
    ]
    summary: str
    reason: str
    needs_human_review: bool = True


SYSTEM_PROMPT = """You classify evidence for a single prediction-market outcome.
An item is relevant only if it could materially affect the tracked outcome probability, resolution,
polling, ballot access, candidacy status, election rules, or a major forecast input.
Do not mark an item relevant merely because it mentions a country, office, candidate, or party.
Return strict JSON matching the requested schema. Never infer market prices.
"""


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("No JSON object in model response")
        return json.loads(match.group(0))


def _normalize_classification_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Tolerantly repair known live-model field-name drift before schema validation.

    Observed live against qwen3:8b: the model sometimes answers the boolean "relevant" field
    under the key "relevance" (presumably blending it with the neighboring "relevance_score"
    field name). This corrects the *key name* for a value the model already produced -- it never
    invents, fills in, or substitutes a value the model didn't actually return.
    """
    if "relevant" not in data and isinstance(data.get("relevance"), bool):
        data = dict(data)
        data["relevant"] = data.pop("relevance")
    return data


class EvidenceClassifierProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def classify(self, payload: dict[str, Any]) -> EvidenceClassification:
        raise NotImplementedError


class DeterministicClassifier(EvidenceClassifierProvider):
    name = "deterministic"
    model = "rules-v1"

    MATERIAL_TERMS = {
        "poll",
        "polling",
        "survey",
        "ballot",
        "primary",
        "candidate",
        "withdraw",
        "withdrew",
        "drops out",
        "nomination",
        "endorsement",
        "fundraising",
        "election",
        "vote",
        "voters",
        "court",
        "ruling",
        "rule",
        "rules",
        "deadline",
        "certification",
        "forecast",
        "rating",
        "approval",
        "coalition",
        "leadership",
        "resign",
        "resignation",
        "disqualified",
    }
    HIGH_QUALITY_DOMAINS = {
        "reuters.com",
        "apnews.com",
        "elections.ca",
        "fec.gov",
        "nytimes.com",
        "washingtonpost.com",
        "bbc.com",
        "cbc.ca",
        "cnn.com",
        "foxnews.com",
        "nbcnews.com",
        "theguardian.com",
        "electoral-vote.com",
        "cookpolitical.com",
        "insideelections.com",
        "racetothewh.com",
    }

    def classify(self, payload: dict[str, Any]) -> EvidenceClassification:
        title = str(payload.get("title", ""))
        content = str(payload.get("content_text", ""))
        question = str(payload.get("market_question", ""))
        aliases = [str(x).lower() for x in payload.get("aliases", [])]
        hay = f"{title} {content}".lower()
        q_tokens = {x for x in re.findall(r"[a-z0-9]+", question.lower()) if len(x) > 3}
        matched = {t for t in q_tokens if t in hay}
        alias_match = any(a and a in hay for a in aliases)
        material = any(term in hay for term in self.MATERIAL_TERMS)
        relevant = bool(material and (len(matched) >= 1 or alias_match))
        score = min(0.95, 0.15 + 0.12 * len(matched) + (0.25 if alias_match else 0) + (0.25 if material else 0))
        if not relevant:
            score = min(score, 0.45)
        url = str(payload.get("url", "")).lower()
        quality = 0.85 if any(domain in url for domain in self.HIGH_QUALITY_DOMAINS) else 0.58
        category = "polling" if any(x in hay for x in ("poll", "polling", "survey")) else "news"
        if any(x in hay for x in ("ballot", "disqualified", "nomination", "primary")):
            category = "ballot_access"
        if any(x in hay for x in ("rule", "ruling", "court", "certification")):
            category = "rules"
        changes = relevant and any(
            x in hay for x in ("new poll", "leads", "ahead", "withdraw", "drops out", "ruling", "endorses", "resign")
        )
        return EvidenceClassification(
            relevant=relevant,
            relevance_score=round(score, 3),
            source_quality=quality,
            changes_probability=changes,
            direction="NONE",
            estimated_magnitude=0.0,
            category=cast(
                Literal[
                    "polling",
                    "forecast",
                    "ballot_access",
                    "candidacy",
                    "rules",
                    "resolution",
                    "endorsement",
                    "fundraising",
                    "campaign",
                    "news",
                    "official",
                    "other",
                ],
                category,
            ),
            summary=title[:500] or "Evidence item",
            reason="Matched market-specific terms and a material election/forecast trigger."
            if relevant
            else "No sufficiently specific material trigger was detected.",
            needs_human_review=changes or score < 0.80,
        )


class OllamaClassifier(EvidenceClassifierProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def classify(self, payload: dict[str, Any]) -> EvidenceClassification:
        schema = EvidenceClassification.model_json_schema()
        prompt = f"{SYSTEM_PROMPT}\nSchema:\n{json.dumps(schema)}\nInput:\n{json.dumps(payload, ensure_ascii=False)}\nReturn JSON only."
        last_error: Exception | None = None
        # Bounded retry with a corrective follow-up prompt, matching OllamaJSONClient.generate_validated.
        # Without a low temperature, models occasionally drift on exact field names (e.g. "relevance"
        # instead of the schema's "relevant") even though format="json" only guarantees valid JSON syntax,
        # not schema-conformant keys.
        for _attempt in range(2):
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.15},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            try:
                parsed = _normalize_classification_dict(_extract_json(response.json().get("response", "")))
                return EvidenceClassification.model_validate(parsed)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                prompt = (
                    f"{SYSTEM_PROMPT}\nSchema:\n{json.dumps(schema)}\nInput:\n"
                    f"{json.dumps(payload, ensure_ascii=False)}\nYour previous response was invalid: {exc}\n"
                    "Return corrected JSON only, matching the schema's field names exactly."
                )
        assert last_error is not None  # both loop iterations raise before falling through here
        raise last_error


class OpenAICompatibleClassifier(EvidenceClassifierProvider):
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def classify(self, payload: dict[str, Any]) -> EvidenceClassification:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"schema": EvidenceClassification.model_json_schema(), "input": payload}, ensure_ascii=False
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        response = requests.post(
            f"{self.base_url}/v1/chat/completions", headers=headers, json=cast(Any, body), timeout=self.timeout
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        return EvidenceClassification.model_validate(_extract_json(text))


def get_classifier() -> EvidenceClassifierProvider:
    settings = get_settings()
    provider = settings.llm_provider.lower().strip()
    if provider == "ollama":
        return OllamaClassifier(
            settings.llm_base_url or settings.ollama_base_url,
            settings.llm_model or settings.ollama_model,
            settings.llm_timeout_seconds,
        )
    if provider == "openai_compatible":
        return OpenAICompatibleClassifier(
            settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_timeout_seconds
        )
    return DeterministicClassifier()


def classify_with_fallback(
    payload: dict[str, Any], provider: EvidenceClassifierProvider | None = None
) -> tuple[EvidenceClassification, str | None]:
    provider = provider or get_classifier()
    try:
        return provider.classify(payload), None
    except (ValidationError, ValueError, KeyError, requests.RequestException) as exc:
        fallback = DeterministicClassifier().classify(payload)
        return fallback, f"{provider.name} failed; deterministic fallback used: {type(exc).__name__}"
