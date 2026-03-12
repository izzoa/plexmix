from typing import Optional
import logging
import time

from .base import AIProvider

logger = logging.getLogger(__name__)


class CustomProvider(AIProvider):
    """AI provider for any OpenAI-compatible API endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: Optional[str] = None,
        temperature: float = 0.7,
    ):
        # The openai library requires a non-empty api_key string
        super().__init__(api_key or "no-key-required", model, temperature)
        self.provider_name = "Custom"
        try:
            from openai import OpenAI
            self.client = OpenAI(base_url=base_url, api_key=self.api_key)
            logger.info(f"Initialized custom provider: {base_url} with model {model}")
        except ImportError:
            raise ImportError("openai not installed. Run: pip install openai")

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30,
    ) -> str:
        temp = temperature if temperature is not None else self.temperature

        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temp,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )

                if not response.choices or not response.choices[0].message.content:
                    raise ValueError("Empty response from custom endpoint")

                return response.choices[0].message.content

            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(x in error_str for x in ["timeout", "429", "rate", "overloaded"])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"[Custom] Retryable error on attempt {attempt + 1}: {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Failed to get response from custom endpoint after retries")
