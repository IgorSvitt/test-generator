"""Configuration loader for LLM-backed test generation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal

from dotenv import load_dotenv


Provider = Literal["openai", "gemini", "vertex", "ollama", "custom"]


@dataclass(frozen=True)
class LLMConfig:
    """Runtime configuration for LiteLLM requests."""

    provider: Provider
    model: str
    api_key: str
    api_base: str | None = None


def load_config() -> LLMConfig:
    """Load LLM settings from environment variables.

    Returns:
        Parsed :class:`LLMConfig` object.

    Raises:
        ValueError: If required variables are missing.
    """
    load_dotenv()
    provider_raw = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    provider = _parse_provider(provider_raw)
    model = os.getenv("LLM_MODEL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    api_base = os.getenv("LLM_API_BASE", "").strip() or None

    if not model:
        raise ValueError("LLM_MODEL is required.")
    if provider != "vertex" and not api_key:
        raise ValueError("LLM_API_KEY is required.")

    normalized_model = _normalize_model(provider=provider, model=model)
    return LLMConfig(
        provider=provider,
        model=normalized_model,
        api_key=api_key,
        api_base=api_base,
    )


def _parse_provider(provider: str) -> Provider:
    valid: tuple[Provider, ...] = ("openai", "gemini", "vertex", "ollama", "custom")
    if provider not in valid:
        raise ValueError(f"Unsupported LLM_PROVIDER='{provider}'. Use one of: {', '.join(valid)}")
    return provider


def _normalize_model(provider: Provider, model: str) -> str:
    if provider == "gemini" and model.startswith("vertex_ai/"):
        raise ValueError(
            "LLM_PROVIDER=gemini cannot be used with a vertex model. "
            "Use LLM_MODEL=gemini-... (or gemini/...) for API-key auth."
        )
    if provider == "vertex" and model.startswith("gemini/"):
        raise ValueError(
            "LLM_PROVIDER=vertex cannot be used with a direct gemini model. "
            "Use LLM_MODEL=gemini-... (it will become vertex_ai/...) and configure ADC."
        )
    if provider == "gemini" and not model.startswith("gemini/"):
        return f"gemini/{model}"
    if provider == "vertex" and not model.startswith("vertex_ai/"):
        return f"vertex_ai/{model}"
    return model
