from typing import List, Dict, Any
import logging

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

    def generate_playlist(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int = 50
    ) -> List[int]:
        try:
            prompt = self._prepare_prompt(mood_query, candidate_tracks, max_tracks)

            model = self.genai.GenerativeModel(
                model_name=self.model,
                generation_config={
                    "temperature": self.temperature,
                    "max_output_tokens": 8192,
                }
            )

            response = model.generate_content(prompt)

            if not response:
                logger.error("Empty response from Gemini")
                return []

            try:
                response_text = response.text
            except ValueError:
                if response.candidates and response.candidates[0].content.parts:
                    response_text = "".join(part.text for part in response.candidates[0].content.parts)
                else:
                    logger.error("Could not extract text from Gemini response")
                    return []

            if not response_text:
                logger.error("Empty response text from Gemini")
                return []

            track_ids = self._parse_response(response_text)
            validated_ids = self._validate_selections(track_ids, candidate_tracks)

            logger.info(f"Gemini selected {len(validated_ids)} tracks for mood: {mood_query}")
            return validated_ids[:max_tracks]

        except Exception as e:
            logger.error(f"Failed to generate playlist with Gemini: {e}")
            return []
