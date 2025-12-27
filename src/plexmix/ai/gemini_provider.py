from typing import List, Dict, Any, Optional
import logging
import time

from .base import AIProvider

logger = logging.getLogger(__name__)


class GeminiProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", temperature: float = 0.7):
        super().__init__(api_key, model, temperature)
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self.genai = genai
            logger.info(f"Initialized Gemini AI provider with model {model}")
        except ImportError:
            raise ImportError("google-generativeai not installed. Run: pip install google-generativeai")

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30
    ) -> str:
        """Send a prompt to Gemini and return the text response."""
        temp = temperature if temperature is not None else self.temperature

        model = self.genai.GenerativeModel(
            model_name=self.model,
            generation_config={
                "temperature": temp,
                "max_output_tokens": max_tokens,
            }
        )

        # Retry with exponential backoff
        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                logger.debug(f"[Gemini] API call attempt {attempt + 1}/{max_retries}")
                response = model.generate_content(
                    prompt,
                    request_options={"timeout": timeout}
                )

                if not response:
                    raise ValueError("Empty response from Gemini")

                # Extract text from response
                try:
                    return response.text
                except (ValueError, AttributeError):
                    if response.candidates and response.candidates[0].content.parts:
                        return "".join(
                            part.text for part in response.candidates[0].content.parts
                            if hasattr(part, 'text')
                        )
                    raise ValueError("Could not extract text from Gemini response")

            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(x in error_str for x in ["504", "timeout", "429", "quota", "rate"])

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"[Gemini] Retryable error on attempt {attempt + 1}: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                raise

        raise RuntimeError("Failed to get response from Gemini after retries")

    def generate_playlist(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int = 50
    ) -> List[int]:
        try:
            # Reduce candidate count if needed to fit in context
            max_candidates = self.get_max_candidates()
            if len(candidate_tracks) > max_candidates:
                logger.warning(f"[Gemini] Truncating {len(candidate_tracks)} candidates to {max_candidates}")
                candidate_tracks = candidate_tracks[:max_candidates]

            prompt = self._prepare_prompt(mood_query, candidate_tracks, max_tracks)
            response_text = self.complete(prompt, max_tokens=8192, timeout=30)

            if not response_text:
                logger.error("[Gemini] Empty response text")
                return []

            track_ids = self._parse_response(response_text)
            validated_ids = self._validate_selections(track_ids, candidate_tracks)

            logger.info(f"[Gemini] Selected {len(validated_ids)} tracks for mood: {mood_query}")
            return validated_ids[:max_tracks]

        except Exception as e:
            logger.error(f"[Gemini] Failed to generate playlist: {e}")
            return []
