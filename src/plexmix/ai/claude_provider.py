from typing import List, Dict, Any
import logging

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

    def generate_playlist(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int = 50
    ) -> List[int]:
        try:
            prompt = self._prepare_prompt(mood_query, candidate_tracks, max_tracks)

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=self.temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            if not response.content or not response.content[0].text:
                logger.error("Empty response from Claude")
                return []

            content = response.content[0].text
            track_ids = self._parse_response(content)
            validated_ids = self._validate_selections(track_ids, candidate_tracks)

            logger.info(f"Claude selected {len(validated_ids)} tracks for mood: {mood_query}")
            return validated_ids[:max_tracks]

        except Exception as e:
            logger.error(f"Failed to generate playlist with Claude: {e}")
            return []
