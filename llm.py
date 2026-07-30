#!/usr/bin/env python3
"""
Local production-assistance model access.

This module is the only place that talks to Ollama. It deliberately exposes
small, production-specific operations rather than a general chat interface:
the model may suggest stock-search terms (and, in later stages, other
production decisions), but it never writes or edits narration.

Every caller must have a deterministic non-model fallback. An unavailable
server, a missing model, an HTTP failure, or malformed model output therefore
reduces quality for that item; it never stops the pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

import requests


DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3.5:4b"
T = TypeVar("T")


class LLMError(RuntimeError):
    """Base class for recoverable local-model failures."""


class LLMUnavailable(LLMError):
    """Ollama cannot serve the configured model."""


class LLMResponseError(LLMError):
    """Ollama responded, but no attempt produced valid structured output."""


@dataclass(frozen=True)
class OllamaSettings:
    enabled: bool
    backend: str
    host: str
    model: str
    timeout_seconds: float
    context: int
    max_retries: int


@dataclass(frozen=True)
class OllamaStatus:
    reachable: bool
    model_present: bool
    detail: str
    installed_models: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.reachable and self.model_present


def settings_from_config(config: dict) -> OllamaSettings:
    raw = (config or {}).get("llm", {}) or {}
    host = str(raw.get("host", DEFAULT_HOST)).strip().rstrip("/")
    model = str(raw.get("model", DEFAULT_MODEL)).strip()
    backend = str(raw.get("backend", "ollama")).strip().lower()

    if not host:
        host = DEFAULT_HOST
    if not model:
        model = DEFAULT_MODEL

    return OllamaSettings(
        enabled=bool(raw.get("enabled", False)),
        backend=backend,
        host=host,
        model=model,
        timeout_seconds=max(1.0, float(raw.get("timeout_seconds", 120))),
        context=max(512, int(raw.get("context", 4096))),
        max_retries=max(0, int(raw.get("max_retries", 2))),
    )


def _model_aliases(name: str) -> set[str]:
    """Treat an omitted tag and :latest as the same local model name."""
    cleaned = name.strip().casefold()
    if not cleaned:
        return set()
    if ":" in cleaned:
        aliases = {cleaned}
        if cleaned.endswith(":latest"):
            aliases.add(cleaned.removesuffix(":latest"))
        return aliases
    return {cleaned, f"{cleaned}:latest"}


class OllamaClient:
    def __init__(self, config: dict, logger: logging.Logger | None = None):
        self.settings = settings_from_config(config)
        self.log = logger or logging.getLogger("llm")
        self._status: OllamaStatus | None = None

    def check_status(self, *, refresh: bool = False) -> OllamaStatus:
        """Check the server and model once, before inference starts.

        The reachability probe is intentionally capped at five seconds even
        when inference is allowed much longer. A stopped local service should
        degrade quickly to the heuristic, not hold up an otherwise valid run.
        """
        if self._status is not None and not refresh:
            return self._status

        s = self.settings
        if s.backend != "ollama":
            self._status = OllamaStatus(
                reachable=False,
                model_present=False,
                detail=f"unsupported llm.backend {s.backend!r}; expected 'ollama'",
            )
            return self._status

        try:
            response = requests.get(
                f"{s.host}/api/tags",
                timeout=min(5.0, s.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models")
            if not isinstance(models, list):
                raise ValueError("GET /api/tags did not return a models array")

            installed = tuple(
                str(item.get("name") or item.get("model") or "").strip()
                for item in models
                if isinstance(item, dict)
                and str(item.get("name") or item.get("model") or "").strip()
            )
            wanted = _model_aliases(s.model)
            present = any(wanted & _model_aliases(name) for name in installed)
            if present:
                detail = f"Ollama reachable; model {s.model!r} is installed"
            else:
                detail = (
                    f"Ollama reachable; model {s.model!r} is not installed "
                    f"(run: ollama pull {s.model})"
                )
            self._status = OllamaStatus(True, present, detail, installed)
        except (requests.RequestException, ValueError, TypeError) as exc:
            self._status = OllamaStatus(
                reachable=False,
                model_present=False,
                detail=f"Ollama unavailable at {s.host} ({exc})",
            )
        return self._status

    def _require_ready(self) -> None:
        if not self.settings.enabled:
            raise LLMUnavailable("local model is disabled by llm.enabled")
        status = self.check_status()
        if not status.ready:
            raise LLMUnavailable(status.detail)

    def generate_json(
        self,
        *,
        prompt: str,
        schema: dict,
        validate: Callable[[Any], T],
        purpose: str,
    ) -> T:
        """Generate JSON, validate its semantic shape, and retry bad output."""
        self._require_ready()
        s = self.settings
        last_error: Exception | None = None

        for attempt in range(1, s.max_retries + 2):
            try:
                response = requests.post(
                    f"{s.host}/api/generate",
                    json={
                        "model": s.model,
                        "prompt": prompt,
                        "stream": False,
                        "format": schema,
                        "options": {
                            "num_ctx": s.context,
                            "temperature": 0.2,
                        },
                    },
                    timeout=s.timeout_seconds,
                )
                response.raise_for_status()
                envelope = response.json()
                raw = envelope.get("response")
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError("Ollama response did not contain generated text")
                return validate(json.loads(raw))
            except (
                requests.RequestException,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc
                self.log.warning(
                    "Local model %s attempt %d/%d returned unusable output: %s",
                    purpose,
                    attempt,
                    s.max_retries + 1,
                    exc,
                )

        raise LLMResponseError(
            f"local model {purpose} failed after {s.max_retries + 1} attempt(s): "
            f"{last_error}"
        )

    def keywords_for_chunk(
        self,
        paragraph: str,
        previous_keywords: list[str],
        *,
        count: int = 3,
    ) -> list[str]:
        """Suggest concrete stock-library search phrases for one chunk."""
        count = min(3, max(2, int(count)))
        prior = [str(k).strip() for k in previous_keywords if str(k).strip()]
        avoid = json.dumps(prior, ensure_ascii=False)
        prompt = (
            "You select stock-photo search terms for a UK economics documentary. "
            "You do not write or edit narration.\n\n"
            f"Return exactly {count} concrete, visually searchable phrases for "
            "the narration below. Each phrase must be one to three words and name "
            "a visible object, person, place, or scene that a stock library could "
            "photograph. Prefer specific scenes such as \"prepayment meter\", "
            "\"supermarket checkout\", or \"terraced houses\". Never return "
            "abstractions such as \"wealth\", \"success\", \"poverty\", \"economy\", "
            "or \"inequality\". Do not repeat or lightly reword any avoided term.\n"
            f"Avoid terms used in the previous three chunks: {avoid}\n\n"
            "Return only a JSON object matching this shape: "
            "{\"keywords\":[\"term one\",\"term two\",\"term three\"]}\n\n"
            f"NARRATION:\n{paragraph}"
        )
        schema = {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "minItems": count,
                    "maxItems": count,
                    "items": {"type": "string"},
                }
            },
            "required": ["keywords"],
            "additionalProperties": False,
        }
        prior_folded = {k.casefold() for k in prior}
        banned_abstractions = {
            "wealth", "success", "poverty", "economy", "inequality",
        }

        def validate(data: Any) -> list[str]:
            if not isinstance(data, dict) or set(data) != {"keywords"}:
                raise ValueError("expected one JSON object key: keywords")
            values = data["keywords"]
            if not isinstance(values, list) or len(values) != count:
                raise ValueError(f"expected exactly {count} keyword strings")

            cleaned: list[str] = []
            seen: set[str] = set()
            for value in values:
                if not isinstance(value, str):
                    raise ValueError("each keyword must be a string")
                keyword = " ".join(value.strip().split()).lower()
                words = re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)?", keyword)
                if not keyword or not 1 <= len(words) <= 3:
                    raise ValueError(
                        f"keyword must contain one to three words: {value!r}"
                    )
                folded = keyword.casefold()
                if folded in seen:
                    raise ValueError(f"duplicate keyword: {keyword!r}")
                if folded in prior_folded:
                    raise ValueError(f"keyword repeats a recent chunk: {keyword!r}")
                if set(words) & banned_abstractions:
                    raise ValueError(f"abstract keyword is not searchable: {keyword!r}")
                seen.add(folded)
                cleaned.append(keyword)
            return cleaned

        return self.generate_json(
            prompt=prompt,
            schema=schema,
            validate=validate,
            purpose="keyword extraction",
        )
