from typing import Optional
import os
import logging

from .base import AIProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider
from .claude_provider import ClaudeProvider
from .cohere_provider import CohereProvider
from .local_provider import LocalLLMProvider, LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL

logger = logging.getLogger(__name__)


def get_ai_provider(
    provider_name: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    local_mode: Optional[str] = None,
    local_endpoint: Optional[str] = None,
    local_auth_token: Optional[str] = None,
    local_max_output_tokens: Optional[int] = None,
) -> AIProvider:
    alias_map = {"anthropic": "claude"}
    provider_name = alias_map.get(provider_name.lower(), provider_name.lower())

    if api_key is None:
        if provider_name == "gemini":
            api_key = os.getenv("GOOGLE_API_KEY")
        elif provider_name == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
        elif provider_name == "claude":
            api_key = os.getenv("ANTHROPIC_API_KEY")
        elif provider_name == "cohere":
            api_key = os.getenv("COHERE_API_KEY")

    requires_api_key = provider_name in {"gemini", "openai", "claude", "cohere"}

    if requires_api_key and not api_key:
        raise ValueError(f"API key required for {provider_name} provider")

    if provider_name == "gemini":
        model = model or "gemini-2.5-flash"
        return GeminiProvider(api_key, model, temperature)
    elif provider_name == "openai":
        model = model or "gpt-5-mini"
        return OpenAIProvider(api_key, model, temperature)
    elif provider_name == "claude":
        model = model or "claude-sonnet-4-5-20250929"
        return ClaudeProvider(api_key, model, temperature)
    elif provider_name == "cohere":
        model = model or "command-r7b-12-2024"
        return CohereProvider(api_key, model, temperature)
    elif provider_name == "local":
        model = model or LOCAL_LLM_DEFAULT_MODEL
        return LocalLLMProvider(
            model=model,
            temperature=temperature,
            mode=local_mode or "builtin",
            endpoint=local_endpoint,
            auth_token=local_auth_token,
            max_output_tokens=local_max_output_tokens or 800,
        )
    else:
        raise ValueError(
            f"Unknown provider: {provider_name}. Choose from: gemini, openai, claude, cohere, local"
        )


__all__ = [
    "AIProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "ClaudeProvider",
    "CohereProvider",
    "LocalLLMProvider",
    "LOCAL_LLM_MODELS",
    "LOCAL_LLM_DEFAULT_MODEL",
    "get_ai_provider"
]
