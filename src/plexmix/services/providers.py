"""Centralized provider factory — resolves settings + credentials into provider instances.

Both the CLI and UI layers should call these functions instead of duplicating
provider construction logic inline.
"""

import logging
from typing import Any, Dict, Optional

from plexmix.config import credentials
from plexmix.config.settings import Settings
from plexmix.ai import get_ai_provider
from plexmix.utils.embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)


def discover_endpoint_models(
    base_url: str, api_key: Optional[str] = None, timeout: int = 5
) -> list:
    """Discover available models from an OpenAI-compatible endpoint (Ollama, LM Studio, etc.).

    Hits ``{base_url}/models`` (or ``{base_url}/v1/models`` as fallback).
    Returns a list of model ID strings, or an empty list on failure.
    """
    import urllib.request
    import urllib.error
    import json as _json

    # Normalize URL: strip trailing slash and /v1 suffix for consistency
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]

    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Try /v1/models first (standard OpenAI-compatible), then /api/tags (Ollama native)
    urls_to_try = [f"{url}/v1/models", f"{url}/api/tags"]

    for models_url in urls_to_try:
        try:
            req = urllib.request.Request(models_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = _json.loads(resp.read().decode("utf-8"))

                # OpenAI format: {"data": [{"id": "model-name", ...}, ...]}
                if "data" in data and isinstance(data["data"], list):
                    return sorted(
                        [m["id"] for m in data["data"] if "id" in m],
                        key=str.lower,
                    )

                # Ollama format: {"models": [{"name": "model:tag", ...}, ...]}
                if "models" in data and isinstance(data["models"], list):
                    return sorted(
                        [m["name"] for m in data["models"] if "name" in m],
                        key=str.lower,
                    )

        except (urllib.error.URLError, OSError, ValueError, KeyError):
            continue

    return []


def canonical_ai_provider(name: Optional[str]) -> str:
    """Normalize a provider name (e.g. 'anthropic' → 'claude')."""
    if not name:
        return "gemini"
    name = name.lower()
    return "claude" if name == "anthropic" else name


def resolve_ai_api_key(provider_name: Optional[str]) -> Optional[str]:
    """Look up an AI provider's API key from the credential store."""
    provider = canonical_ai_provider(provider_name)
    _key_getters = {
        "gemini": credentials.get_google_api_key,
        "openai": credentials.get_openai_api_key,
        "claude": credentials.get_anthropic_api_key,
        "cohere": credentials.get_cohere_api_key,
        "custom": credentials.get_custom_ai_api_key,
    }
    getter = _key_getters.get(provider)
    return getter() if getter else None


def resolve_embedding_api_key(provider: str) -> Optional[str]:
    """Look up an embedding provider's API key from the credential store."""
    _key_getters = {
        "gemini": credentials.get_google_api_key,
        "openai": credentials.get_openai_api_key,
        "cohere": credentials.get_cohere_api_key,
        "custom": credentials.get_custom_embedding_api_key,
    }
    getter = _key_getters.get(provider)
    return getter() if getter else None


def local_provider_kwargs(settings: Settings) -> Dict[str, Any]:
    """Extract local-LLM settings as kwargs for the AI provider factory."""
    return {
        "local_mode": settings.ai.local_mode,
        "local_endpoint": settings.ai.local_endpoint,
        "local_auth_token": settings.ai.local_auth_token,
        "local_max_output_tokens": settings.ai.local_max_output_tokens,
    }


def build_ai_provider(
    settings: Settings,
    provider_name: Optional[str] = None,
    api_key_override: Optional[str] = None,
    silent: bool = False,
) -> Optional[Any]:
    """Build an AI provider instance from settings, resolving credentials automatically.

    Returns ``None`` if the provider cannot be created (e.g. missing API key).
    """
    name = provider_name or settings.ai.default_provider or "gemini"
    api_key = api_key_override if api_key_override is not None else resolve_ai_api_key(name)
    model = settings.ai.model
    if canonical_ai_provider(name) == "custom":
        model = settings.ai.custom_model or model
    try:
        return get_ai_provider(
            provider_name=name,
            api_key=api_key,
            model=model,
            temperature=settings.ai.temperature,
            **local_provider_kwargs(settings),
            custom_endpoint=settings.ai.custom_endpoint,
            custom_api_key=settings.ai.custom_api_key or resolve_ai_api_key("custom"),
        )
    except ValueError as exc:
        if not silent:
            logger.warning("AI provider init failed: %s", exc)
        return None


def build_embedding_generator(settings: Settings) -> Optional[EmbeddingGenerator]:
    """Build an EmbeddingGenerator from settings, resolving API keys automatically.

    Returns ``None`` if the provider requires an API key that isn't configured.
    """
    provider = settings.embedding.default_provider
    api_key = resolve_embedding_api_key(provider)

    from plexmix.services.registry import requires_embedding_api_key

    if requires_embedding_api_key(provider) and not api_key:
        return None

    kwargs: Dict[str, Any] = {
        "provider": provider,
        "api_key": api_key,
        "model": settings.embedding.model,
    }
    if provider == "custom":
        kwargs["model"] = settings.embedding.custom_model or settings.embedding.model
        kwargs["custom_endpoint"] = settings.embedding.custom_endpoint
        kwargs["custom_api_key"] = (
            settings.embedding.custom_api_key or credentials.get_custom_embedding_api_key()
        )
        kwargs["custom_dimension"] = settings.embedding.custom_dimension

    return EmbeddingGenerator(**kwargs)
