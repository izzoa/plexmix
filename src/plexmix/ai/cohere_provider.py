from typing import List, Dict, Any
import logging

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

    def generate_playlist(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int = 50
    ) -> List[int]:
        try:
            prompt = self._prepare_prompt(mood_query, candidate_tracks, max_tracks)

            response = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=self.temperature,
                max_tokens=4096
            )

            if not response.message or not response.message.content:
                logger.error("Empty response from Cohere")
                return []

            content = response.message.content[0].text
            track_ids = self._parse_response(content)
            validated_ids = self._validate_selections(track_ids, candidate_tracks)

            logger.info(f"Cohere selected {len(validated_ids)} tracks for mood: {mood_query}")
            return validated_ids[:max_tracks]

        except Exception as e:
            logger.error(f"Failed to generate playlist with Cohere: {e}")
            return []
