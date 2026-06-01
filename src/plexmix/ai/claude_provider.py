from typing import Any, Optional
import logging
import time

from .base import AIProvider

logger = logging.getLogger(__name__)


def _claude_rejects_sampling_params(model: str) -> bool:
    """Return True for Claude models that reject non-default sampling params.

    Claude Opus 4.7 and later return a 400 if ``temperature``, ``top_p``, or
    ``top_k`` is set to a non-default value. Opus 4.6 and earlier, and all
    Sonnet/Haiku models, still accept ``temperature``.
    """
    m = model.lower()
    if not m.startswith("claude-opus-4-"):
        return False
    minor = m[len("claude-opus-4-") :].split("-")[0]
    return minor.isdigit() and int(minor) >= 7


class ClaudeProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6", temperature: float = 0.7):
        super().__init__(api_key, model, temperature)
        try:
            from anthropic import Anthropic

            self.client = Anthropic(api_key=api_key)
            logger.info(f"Initialized Claude provider with model {model}")
        except ImportError:
            raise ImportError("anthropic not installed. Run: pip install anthropic")

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30,
    ) -> str:
        """Send a prompt to Claude and return the text response."""
        temp = temperature if temperature is not None else self.temperature

        # Opus 4.7+ reject any non-default sampling parameter (temperature/
        # top_p/top_k) with a 400; omit temperature for those models.
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if not _claude_rejects_sampling_params(self.model):
            request_kwargs["temperature"] = temp

        # Retry with exponential backoff
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                response = self.client.with_options(timeout=float(timeout)).messages.create(
                    **request_kwargs
                )

                if not response.content:
                    raise ValueError("Empty response from Claude")

                # Extract text from the first TextBlock
                first_block = response.content[0]
                if not hasattr(first_block, "text") or not first_block.text:  # type: ignore[union-attr]
                    raise ValueError("Empty response from Claude")

                return str(first_block.text)

            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(x in error_str for x in ["timeout", "429", "rate", "overloaded"])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.warning(
                        f"[Claude] Retryable error on attempt {attempt + 1}: {e}. Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Failed to get response from Claude after retries")
