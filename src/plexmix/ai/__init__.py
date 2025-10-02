from typing import Optional
import os
import logging

from .base import AIProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider
from .claude_provider import ClaudeProvider
from .cohere_provider import CohereProvider

logger = logging.getLogger(__name__)


def get_ai_provider(
    provider_name: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7
) -> AIProvider:
    provider_name = provider_name.lower()

    if api_key is None:
        if provider_name == "gemini":
            api_key = os.getenv("GOOGLE_API_KEY")
        elif provider_name == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
        elif provider_name == "claude":
            api_key = os.getenv("ANTHROPIC_API_KEY")
        elif provider_name == "cohere":
            api_key = os.getenv("COHERE_API_KEY")

    if not api_key:
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
    else:
        raise ValueError(f"Unknown provider: {provider_name}. Choose from: gemini, openai, claude, cohere")


__all__ = [
    "AIProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "ClaudeProvider",
    "CohereProvider",
    "get_ai_provider"
]
