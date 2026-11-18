from typing import Optional
import os
import logging

from .base import AIProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider
from .claude_provider import ClaudeProvider
from .cohere_provider import CohereProvider
from .local_provider import LocalLLMProvider, LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL
from .custom_provider import CustomProvider
from plexmix.config.credentials import sanitize_credential_value, validate_api_key
from plexmix.services.registry import (
    AI_PROVIDERS,
    AI_PROVIDERS_REQUIRING_KEY,
    get_default_ai_model,
)

logger = logging.getLogger(__name__)

_INVALID_LOCAL_TOKEN_MESSAGE = (
    "Local endpoint auth token contains invalid characters (a space, a line break or "
    "a non-ASCII character). Enter it again."
)

# Provider name → constructor (avoids long if/elif chain)
_CLOUD_CONSTRUCTORS = {
    "gemini": lambda api_key, model, temp: GeminiProvider(api_key, model, temp),
    "openai": lambda api_key, model, temp: OpenAIProvider(api_key, model, temp),
    "claude": lambda api_key, model, temp: ClaudeProvider(api_key, model, temp),
    "cohere": lambda api_key, model, temp: CohereProvider(api_key, model, temp),
}

# Env var fallbacks for API keys (only checked when no key is passed in)
_ENV_KEY_LOOKUP = {
    "gemini": lambda: os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
    "openai": lambda: os.getenv("OPENAI_API_KEY"),
    "claude": lambda: os.getenv("ANTHROPIC_API_KEY"),
    "cohere": lambda: os.getenv("COHERE_API_KEY"),
}


def get_ai_provider(
    provider_name: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    local_mode: Optional[str] = None,
    local_endpoint: Optional[str] = None,
    local_auth_token: Optional[str] = None,
    local_max_output_tokens: Optional[int] = None,
    custom_endpoint: Optional[str] = None,
    custom_api_key: Optional[str] = None,
) -> AIProvider:
    # Resolve aliases (e.g. "anthropic" → "claude")
    for pid, p in AI_PROVIDERS.items():
        if provider_name.lower() in p.aliases:
            provider_name = pid
            break
    else:
        provider_name = provider_name.lower()

    # Env-var fallback for API key
    if api_key is None:
        env_getter = _ENV_KEY_LOOKUP.get(provider_name)
        if env_getter:
            api_key = env_getter()

    # Normalize keys from any source (keyring, direct env fallback, or YAML
    # settings) before they reach a provider/HTTP client.
    api_key = sanitize_credential_value(api_key)
    custom_api_key = sanitize_credential_value(custom_api_key)

    if provider_name in AI_PROVIDERS_REQUIRING_KEY and not api_key:
        raise ValueError(f"API key required for {provider_name} provider")

    # Cloud providers
    cloud_ctor = _CLOUD_CONSTRUCTORS.get(provider_name)
    if cloud_ctor:
        model = model or get_default_ai_model(provider_name)
        assert api_key is not None  # guaranteed by the check above
        validate_api_key(api_key, provider_name)
        return cloud_ctor(api_key, model, temperature)

    # Local provider
    if provider_name == "local":
        model = model or LOCAL_LLM_DEFAULT_MODEL
        # The token is sent as a request header, so it gets the treatment API keys
        # get: the ends are stripped, then a token that cannot be sent is refused.
        # An endpoint without a token is a normal setup, and validate_api_key
        # accepts an empty or missing one. Only endpoint mode sends the token, so a
        # leftover one does not stop the builtin model.
        local_auth_token = sanitize_credential_value(local_auth_token)
        if (local_mode or "builtin") == "endpoint":
            try:
                validate_api_key(local_auth_token, "local")
            except ValueError as invalid:
                # Callers build a provider quietly and carry on without one, so the
                # reason is logged here. The text is fixed and never holds the token.
                # type(invalid) is InvalidAPIKeyError, as for the other providers.
                logger.error(_INVALID_LOCAL_TOKEN_MESSAGE)
                raise type(invalid)(_INVALID_LOCAL_TOKEN_MESSAGE) from None
        return LocalLLMProvider(
            model=model,
            temperature=temperature,
            mode=local_mode or "builtin",
            endpoint=local_endpoint,
            auth_token=local_auth_token,
            # Pass through (may be None) so the provider can fall back to the
            # model's catalog cap instead of a hard-coded 800.
            max_output_tokens=local_max_output_tokens,
        )

    # Custom (OpenAI-compatible) provider
    if provider_name == "custom":
        if not custom_endpoint:
            raise ValueError("Endpoint URL required for custom provider")
        if not model:
            raise ValueError("Model name required for custom provider")
        validate_api_key(custom_api_key, "custom")
        return CustomProvider(
            base_url=custom_endpoint,
            model=model,
            api_key=custom_api_key,
            temperature=temperature,
        )

    raise ValueError(
        f"Unknown provider: {provider_name}. "
        f"Choose from: gemini, openai, claude, cohere, local, custom"
    )


__all__ = [
    "AIProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "ClaudeProvider",
    "CohereProvider",
    "LocalLLMProvider",
    "CustomProvider",
    "LOCAL_LLM_MODELS",
    "LOCAL_LLM_DEFAULT_MODEL",
    "get_ai_provider",
]
