from typing import Any, Optional
import logging
import time

from .base import AIProvider

logger = logging.getLogger(__name__)


def is_openai_reasoning_model(model: str) -> bool:
    """Return True for OpenAI reasoning models (GPT-5 / o-series).

    These models reject a non-default ``temperature`` and require
    ``max_completion_tokens`` instead of ``max_tokens`` on the Chat
    Completions API.
    """
    return model.lower().startswith(("gpt-5", "o1", "o3", "o4"))


class OpenAIProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "gpt-5.4-mini", temperature: float = 0.7):
        super().__init__(api_key, model, temperature)
        try:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key)
            logger.info(f"Initialized OpenAI provider with model {model}")
        except ImportError:
            raise ImportError("openai not installed. Run: pip install openai")

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30,
    ) -> str:
        """Send a prompt to OpenAI and return the text response."""
        temp = temperature if temperature is not None else self.temperature

        # Reasoning models (GPT-5 / o-series) reject a custom temperature and
        # require max_completion_tokens instead of max_tokens.
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout,
        }
        if is_openai_reasoning_model(self.model):
            request_kwargs["max_completion_tokens"] = max_tokens
            request_kwargs["reasoning_effort"] = "low"
        else:
            request_kwargs["max_tokens"] = max_tokens
            request_kwargs["temperature"] = temp

        # Retry with exponential backoff
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(**request_kwargs)

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty response from OpenAI")

                return str(response.choices[0].message.content)

            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(x in error_str for x in ["timeout", "429", "rate", "overloaded"])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"[OpenAI] Retryable error on attempt {attempt + 1}: {e}. Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Failed to get response from OpenAI after retries")
