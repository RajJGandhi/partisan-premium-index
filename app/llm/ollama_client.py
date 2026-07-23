from __future__ import annotations

import json
import re
from typing import Any, Type, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)


class OllamaJSONClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: int = 60):
        settings = get_settings()
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout

    def _extract_json(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in LLM output")
        return json.loads(match.group(0))

    def generate_json(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        prompt = f"{system_prompt}\n\nInput JSON:\n{json.dumps(user_payload, ensure_ascii=False)}\n\nReturn JSON only."
        response = requests.post(
            f"{self.base_url}/api/generate",
            json={"model": self.model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("response", "")
        return self._extract_json(raw)

    def generate_validated(self, system_prompt: str, user_payload: dict[str, Any], schema: Type[T]) -> T:
        last_error: Exception | None = None
        for _attempt in range(2):
            try:
                data = self.generate_json(system_prompt, user_payload)
                return schema.model_validate(data)
            except (ValidationError, ValueError, json.JSONDecodeError, requests.RequestException) as exc:
                last_error = exc
                user_payload = {
                    "original_input": user_payload,
                    "previous_error": str(exc),
                    "instruction": "Return corrected JSON only matching the schema exactly.",
                }
        raise RuntimeError(f"LLM validation failed after retry: {last_error}")
