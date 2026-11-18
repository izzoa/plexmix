"""Centralized provider factory — resolves settings + credentials into provider instances.

Both the CLI and UI layers should call these functions instead of duplicating
provider construction logic inline.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from plexmix.config import credentials
from plexmix.config.credentials import InvalidAPIKeyError
from plexmix.config.settings import Settings
from plexmix.ai import get_ai_provider
from plexmix.services.readiness import Cause
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
    # Only forward the configured model when building the configured provider.
    # Forwarding settings.ai.model to a *different* provider (e.g. a Gemini model
    # name to OpenAI) sends an invalid model; None lets the factory pick that
    # provider's own default model.
    if canonical_ai_provider(name) == canonical_ai_provider(settings.ai.default_provider):
        model = settings.ai.model
    else:
        model = None
    if canonical_ai_provider(name) == "custom":
        model = settings.ai.custom_model or settings.ai.model
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
    except Exception as exc:
        # Honor the "returns None if the provider cannot be created" contract for
        # ANY construction failure, not just ValueError. The local provider raises
        # RuntimeError on model-load failure, which previously escaped and crashed
        # callers (e.g. `plexmix sync`) despite their explicit fallback path.
        if not silent:
            logger.warning("AI provider init failed: %s", exc)
        return None


@dataclass(frozen=True)
class ProviderFailure:
    """Why an embedding provider could not be built (or would not start).

    ``cause`` is a :class:`plexmix.services.readiness.Cause`. ``provider`` is the
    provider id from settings (``"local"``, ``"openai"`` …). ``package`` names the
    missing Python package for ``Cause.MISSING_DEPENDENCY`` (for example
    ``"sentence-transformers"``), otherwise ``""``. ``detail`` is the underlying
    error text with any resolved API key redacted, for logs and as input to the
    UI's plain-language error helper; it is never user-facing copy on its own.
    """

    cause: Cause
    provider: str
    package: str = ""
    detail: str = ""


# Python module name -> pip distribution name, for ImportErrors that carry
# ``exc.name`` instead of an "X not installed" message.
_MODULE_PACKAGES: Dict[str, str] = {
    "sentence_transformers": "sentence-transformers",
    "google": "google-genai",
    "openai": "openai",
    "cohere": "cohere",
    "torch": "torch",
    "transformers": "transformers",
}

_NOT_INSTALLED_RE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+) not installed", re.I)
_LOCAL_MODEL_FAILURE_PREFIXES = (
    "Failed to load local model",
    "Failed to start local embedding worker",
)
_MISSING_CONFIG_PREFIXES = (
    "Endpoint URL required",
    "Model name required",
    "Unknown provider",
)
_DETAIL_MAX_CHARS = 300


def _missing_package(exc: ImportError) -> str:
    """Return the pip package an ImportError is about ("" if it can't be told)."""
    match = _NOT_INSTALLED_RE.match(str(exc))
    if match:
        return match.group(1)
    name = getattr(exc, "name", None) or ""
    if not name:
        return ""
    root = name.split(".")[0]
    return _MODULE_PACKAGES.get(root, root.replace("_", "-"))


def _failure_detail(exc: BaseException, *secrets: Optional[str]) -> str:
    """Return the exception text with any known secret redacted and length capped."""
    text = str(exc) or type(exc).__name__
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    text = " ".join(text.split())
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[: _DETAIL_MAX_CHARS - 1] + "…"
    return text


def _classify_embedding_exception(
    exc: BaseException, provider: str, *secrets: Optional[str]
) -> ProviderFailure:
    """Map an EmbeddingGenerator construction error to a typed failure."""
    detail = _failure_detail(exc, *secrets)
    if isinstance(exc, InvalidAPIKeyError):
        return ProviderFailure(Cause.INVALID_CREDENTIALS, provider, detail=detail)
    if isinstance(exc, ImportError):
        return ProviderFailure(
            Cause.MISSING_DEPENDENCY, provider, package=_missing_package(exc), detail=detail
        )
    message = str(exc).strip()
    if isinstance(exc, RuntimeError) and message.startswith(_LOCAL_MODEL_FAILURE_PREFIXES):
        return ProviderFailure(Cause.MODEL_LOAD_FAILED, provider, detail=detail)
    if isinstance(exc, ValueError):
        if message.startswith("API key required"):
            return ProviderFailure(Cause.MISSING_API_KEY, provider, detail=detail)
        if message.startswith(_MISSING_CONFIG_PREFIXES):
            return ProviderFailure(Cause.MISSING_CONFIG, provider, detail=detail)
    return ProviderFailure(Cause.UNKNOWN, provider, detail=detail)


def diagnose_embedding_provider(
    settings: Settings,
    *,
    key_present: Optional[bool] = None,
    local_deps: Optional[bool] = None,
) -> Optional[ProviderFailure]:
    """Cheaply tell whether the configured embedding provider can start.

    Builds nothing: it checks key presence for providers that need one, the
    ``sentence_transformers`` package for Local (``importlib.util.find_spec``) and
    the endpoint/model for Custom. Returns ``None`` when nothing is known to be
    wrong (which is not proof that the provider works).

    ``key_present`` and ``local_deps`` let a caller that already collected those
    facts (the readiness evaluator) skip the credential and package lookups.
    """
    from plexmix.services.registry import EMBEDDING_PROVIDERS, requires_embedding_api_key

    provider = (settings.embedding.default_provider or "").strip().lower()
    if provider != "local" and provider not in EMBEDDING_PROVIDERS:
        return ProviderFailure(
            Cause.MISSING_CONFIG, provider, detail=f"Unknown embedding provider: {provider}"
        )

    if requires_embedding_api_key(provider):
        if key_present is None:
            key_present = bool(resolve_embedding_api_key(provider))
        if not key_present:
            return ProviderFailure(Cause.MISSING_API_KEY, provider)

    if provider == "local":
        if local_deps is None:
            import importlib.util

            try:
                local_deps = importlib.util.find_spec("sentence_transformers") is not None
            except (ImportError, ValueError):
                local_deps = False
        if not local_deps:
            return ProviderFailure(
                Cause.MISSING_DEPENDENCY,
                provider,
                package="sentence-transformers",
                detail="sentence-transformers not installed",
            )

    if provider == "custom":
        if not settings.embedding.custom_endpoint:
            return ProviderFailure(
                Cause.MISSING_CONFIG,
                provider,
                detail="Endpoint URL required for custom embedding provider",
            )
        if not (settings.embedding.custom_model or settings.embedding.model):
            return ProviderFailure(
                Cause.MISSING_CONFIG,
                provider,
                detail="Model name required for custom embedding provider",
            )
    return None


def build_embedding_generator_with_reason(
    settings: Settings,
) -> Tuple[Optional[EmbeddingGenerator], Optional[ProviderFailure]]:
    """Build an EmbeddingGenerator, or say why it could not be built.

    Returns ``(generator, None)`` on success and ``(None, failure)`` otherwise:

    - the provider needs an API key and none is stored → ``Cause.MISSING_API_KEY``;
    - an ``ImportError`` → ``Cause.MISSING_DEPENDENCY`` with the package name;
    - a ``RuntimeError`` "Failed to load local model …" (or the local worker
      failing to start) → ``Cause.MODEL_LOAD_FAILED``;
    - a key the transport can't send → ``Cause.INVALID_CREDENTIALS``;
    - a missing Custom endpoint/model or unknown provider → ``Cause.MISSING_CONFIG``;
    - anything else → ``Cause.UNKNOWN``.

    Construction failures are logged once ("Embedding provider init failed: …",
    with any resolved key redacted); a missing key is not logged.
    """
    provider = settings.embedding.default_provider
    api_key = resolve_embedding_api_key(provider)

    from plexmix.services.registry import requires_embedding_api_key

    if requires_embedding_api_key(provider) and not api_key:
        return None, ProviderFailure(Cause.MISSING_API_KEY, provider)

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

    try:
        return EmbeddingGenerator(**kwargs), None
    except Exception as exc:
        # Don't let ValueError/ImportError/RuntimeError escape: classify them so
        # the Web UI can name the real cause instead of "check your API keys".
        failure = _classify_embedding_exception(
            exc, provider, api_key, kwargs.get("custom_api_key")
        )
        logger.warning("Embedding provider init failed: %s", failure.detail)
        return None, failure


def build_embedding_generator(settings: Settings) -> Optional[EmbeddingGenerator]:
    """Build an EmbeddingGenerator from settings, resolving API keys automatically.

    Returns ``None`` if the provider requires an API key that isn't configured, or
    if construction fails (e.g. missing optional dependency, local model load
    error) — callers treat None as "embeddings unavailable". Use
    :func:`build_embedding_generator_with_reason` to learn why.
    """
    generator, _failure = build_embedding_generator_with_reason(settings)
    return generator
