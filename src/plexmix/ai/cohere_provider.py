from typing import Optional
import logging
import time

from .base import AIProvider

logger = logging.getLogger(__name__)


class CohereProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "command-r7b-12-2024", temperature: float = 0.3):
        super().__init__(api_key, model, temperature)
        try:
            import cohere
            self.client = cohere.ClientV2(api_key=api_key)
            logger.info(f"Initialized Cohere provider with model {model}")
        except ImportError:
            raise ImportError("cohere not installed. Run: pip install cohere")

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30
    ) -> str:
        """Send a prompt to Cohere and return the text response."""
        temp = temperature if temperature is not None else self.temperature

        # Retry with exponential backoff
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                response = self.client.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temp,
                    max_tokens=max_tokens,
                    request_options={"timeout_in_seconds": timeout}
                )

                if not response.message or not response.message.content:
                    raise ValueError("Empty response from Cohere")

                return response.message.content[0].text

            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(x in error_str for x in ["timeout", "429", "rate", "too many"])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"[Cohere] Retryable error on attempt {attempt + 1}: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Failed to get response from Cohere after retries")

