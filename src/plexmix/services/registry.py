"""Unified provider registry — single source of truth for all provider metadata.

This module is intentionally a leaf dependency (no heavy imports) so it can be
used by the AI factory, embedding factory, settings UI, CLI validation, and
doctor diagnostics without circular imports.

Local model catalogs are imported from their owning modules
(``local_provider.LOCAL_LLM_MODELS`` and ``embeddings.LOCAL_EMBEDDING_MODELS``)
since those are also lightweight leaf constants.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CloudAIProvider:
    """Metadata for a cloud (or custom) AI provider."""

    id: str
    display_name: str
    default_model: str
    models: List[str]
    requires_api_key: bool = True
    aliases: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CloudEmbeddingProvider:
    """Metadata for a cloud (or custom) embedding provider."""

    id: str
    display_name: str
    default_model: str
    models: List[str]
    default_dimension: int
    requires_api_key: bool = True


@dataclass(frozen=True)
class LocalEmbeddingModel:
    """Metadata for a local embedding model (mirrors LOCAL_EMBEDDING_MODELS values)."""

    dimension: int
    trust_remote_code: bool = False


# ---------------------------------------------------------------------------
# Cloud AI providers
# ---------------------------------------------------------------------------

AI_PROVIDERS: Dict[str, CloudAIProvider] = {
    "gemini": CloudAIProvider(
        id="gemini",
        display_name="Gemini",
        default_model="gemini-2.5-flash",
        models=["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash-001"],
    ),
    "openai": CloudAIProvider(
        id="openai",
        display_name="OpenAI",
        default_model="gpt-5-mini",
        models=["gpt-5", "gpt-5-mini", "gpt-5-nano"],
    ),
    "claude": CloudAIProvider(
        id="claude",
        display_name="Claude",
        default_model="claude-sonnet-4-5-20250929",
        models=[
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-1-20250414",
            "claude-haiku-4-5-20251001",
        ],
        aliases=["anthropic"],
    ),
    "cohere": CloudAIProvider(
        id="cohere",
        display_name="Cohere",
        default_model="command-r7b-12-2024",
        models=["command-r7b-12-2024", "command-r-plus", "command-r", "command-a-03-2025"],
    ),
    "custom": CloudAIProvider(
        id="custom",
        display_name="Custom (OpenAI-compatible)",
        default_model="",
        models=[],
        requires_api_key=False,
    ),
}

# Providers that need an API key (used by factory & service layer)
AI_PROVIDERS_REQUIRING_KEY = frozenset(pid for pid, p in AI_PROVIDERS.items() if p.requires_api_key)

# ---------------------------------------------------------------------------
# Cloud embedding providers
# ---------------------------------------------------------------------------

EMBEDDING_PROVIDERS: Dict[str, CloudEmbeddingProvider] = {
    "gemini": CloudEmbeddingProvider(
        id="gemini",
        display_name="Gemini",
        default_model="gemini-embedding-001",
        models=["gemini-embedding-001"],
        default_dimension=3072,
    ),
    "openai": CloudEmbeddingProvider(
        id="openai",
        display_name="OpenAI",
        default_model="text-embedding-3-small",
        models=["text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"],
        default_dimension=1536,
    ),
    "cohere": CloudEmbeddingProvider(
        id="cohere",
        display_name="Cohere",
        default_model="embed-v4.0",
        models=["embed-v4.0", "embed-english-v3.0", "embed-multilingual-v3.0"],
        default_dimension=1024,
    ),
    "custom": CloudEmbeddingProvider(
        id="custom",
        display_name="Custom (OpenAI-compatible)",
        default_model="",
        models=[],
        default_dimension=1536,
        requires_api_key=False,
    ),
}

EMBEDDING_PROVIDERS_REQUIRING_KEY = frozenset(
    pid for pid, p in EMBEDDING_PROVIDERS.items() if p.requires_api_key
)

# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_ai_models(provider: str) -> List[str]:
    """Return the sorted model list for an AI provider.

    For 'local', delegates to ``LOCAL_LLM_MODELS`` from the local provider module.
    For 'custom', returns an empty list (user supplies model name).
    """
    if provider == "local":
        from plexmix.ai.local_provider import LOCAL_LLM_MODELS

        return sorted(
            LOCAL_LLM_MODELS.keys(),
            key=lambda k: LOCAL_LLM_MODELS[k]["display_name"].lower(),
        )
    entry = AI_PROVIDERS.get(provider)
    if entry is None:
        return []
    return sorted(entry.models, key=str.lower)


def get_ai_models_display(provider: str) -> List[str]:
    """Return model list for UI display.

    For the 'anthropic' alias used in the settings UI, returns short display
    names (e.g. 'claude-sonnet-4-5') rather than full version-stamped IDs.
    """
    if provider == "anthropic":
        provider = "claude"
    if provider == "local":
        return get_ai_models(provider)
    entry = AI_PROVIDERS.get(provider)
    if entry is None:
        return []
    # Return short display names for Claude models in the UI
    if provider == "claude":
        return sorted(
            [_short_claude_name(m) for m in entry.models],
            key=str.lower,
        )
    return sorted(entry.models, key=str.lower)


def _short_claude_name(model_id: str) -> str:
    """Strip the date suffix from a Claude model ID for UI display."""
    # 'claude-sonnet-4-5-20250929' → 'claude-sonnet-4-5'
    parts = model_id.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        return parts[0]
    return model_id


def get_default_ai_model(provider: str) -> str:
    """Return the default AI model for a provider."""
    if provider == "local":
        from plexmix.ai.local_provider import LOCAL_LLM_DEFAULT_MODEL

        return LOCAL_LLM_DEFAULT_MODEL
    entry = AI_PROVIDERS.get(provider)
    return entry.default_model if entry else ""


def get_embedding_models(provider: str) -> List[str]:
    """Return the sorted model list for an embedding provider."""
    if provider == "local":
        from plexmix.utils.embeddings import LOCAL_EMBEDDING_MODELS

        return sorted(LOCAL_EMBEDDING_MODELS.keys(), key=str.lower)
    entry = EMBEDDING_PROVIDERS.get(provider)
    if entry is None:
        return []
    return sorted(entry.models, key=str.lower)


def get_default_embedding_model(provider: str) -> str:
    """Return the default embedding model for a provider."""
    if provider == "local":
        return "all-MiniLM-L6-v2"
    entry = EMBEDDING_PROVIDERS.get(provider)
    return entry.default_model if entry else ""


def get_embedding_dimension(
    provider: str,
    model: Optional[str] = None,
    custom_dimension: Optional[int] = None,
    fallback_dimension: Optional[int] = None,
) -> int:
    """Return the embedding dimension for a provider/model combination.

    For 'custom', returns *custom_dimension* (default 1536).
    For 'local', looks up the model in ``LOCAL_EMBEDDING_MODELS``.
    For cloud providers, returns the provider's default dimension.

    *fallback_dimension* is used when the model or provider is not recognised
    (e.g. a user-specified local model not in the catalog).
    """
    if provider == "custom":
        return custom_dimension or 1536

    if provider == "local":
        from plexmix.utils.embeddings import LOCAL_EMBEDDING_MODELS

        if model:
            info: Dict[str, Union[int, bool]] = LOCAL_EMBEDDING_MODELS.get(model, {})
            dim = int(info.get("dimension", 0))
            if dim:
                return dim
        # Unknown local model — use caller-provided fallback, default to 384
        # (smallest known local dimension; safe for FAISS index compatibility)
        return fallback_dimension or 384

    entry = EMBEDDING_PROVIDERS.get(provider)
    if entry:
        return entry.default_dimension
    # Unknown provider — use caller-provided fallback
    return fallback_dimension or 768


def requires_ai_api_key(provider: str) -> bool:
    """Check whether an AI provider requires an API key."""
    return provider in AI_PROVIDERS_REQUIRING_KEY


def requires_embedding_api_key(provider: str) -> bool:
    """Check whether an embedding provider requires an API key."""
    return provider in EMBEDDING_PROVIDERS_REQUIRING_KEY
