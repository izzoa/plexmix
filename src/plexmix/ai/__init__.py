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
from plexmix.services.registry import (
    AI_PROVIDERS,
    AI_PROVIDERS_REQUIRING_KEY,
    get_default_ai_model,
)

logger = logging.getLogger(__name__)

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

    if provider_name in AI_PROVIDERS_REQUIRING_KEY and not api_key:
        raise ValueError(f"API key required for {provider_name} provider")

    # Cloud providers
    cloud_ctor = _CLOUD_CONSTRUCTORS.get(provider_name)
    if cloud_ctor:
        model = model or get_default_ai_model(provider_name)
        assert api_key is not None  # guaranteed by the check above
        return cloud_ctor(api_key, model, temperature)

    # Local provider
    if provider_name == "local":
        model = model or LOCAL_LLM_DEFAULT_MODEL
        return LocalLLMProvider(
            model=model,
            temperature=temperature,
            mode=local_mode or "builtin",
            endpoint=local_endpoint,
            auth_token=local_auth_token,
            max_output_tokens=local_max_output_tokens or 800,
        )

    # Custom (OpenAI-compatible) provider
    if provider_name == "custom":
        if not custom_endpoint:
            raise ValueError("Endpoint URL required for custom provider")
        if not model:
            raise ValueError("Model name required for custom provider")
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
