from typing import List, Dict, Any, Optional
import logging
import time

from .base import AIProvider

logger = logging.getLogger(__name__)


class ClaudeProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5-20250929", temperature: float = 0.7):
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
        timeout: int = 30
    ) -> str:
        """Send a prompt to Claude and return the text response."""
        temp = temperature if temperature is not None else self.temperature

        # Retry with exponential backoff
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                response = self.client.with_options(timeout=float(timeout)).messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    temperature=temp,
                    messages=[{"role": "user", "content": prompt}]
                )

                if not response.content or not response.content[0].text:
                    raise ValueError("Empty response from Claude")

                return response.content[0].text

            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(x in error_str for x in ["timeout", "429", "rate", "overloaded"])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"[Claude] Retryable error on attempt {attempt + 1}: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Failed to get response from Claude after retries")

    def generate_playlist(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int = 50
    ) -> List[int]:
        try:
            prompt = self._prepare_prompt(mood_query, candidate_tracks, max_tracks)
            content = self.complete(prompt, max_tokens=4096, timeout=30)

            if not content:
                logger.error("[Claude] Empty response")
                return []

            track_ids = self._parse_response(content)
            validated_ids = self._validate_selections(track_ids, candidate_tracks)

            logger.info(f"[Claude] Selected {len(validated_ids)} tracks for mood: {mood_query}")
            return validated_ids[:max_tracks]

        except Exception as e:
            logger.error(f"[Claude] Failed to generate playlist: {e}")
            return []
