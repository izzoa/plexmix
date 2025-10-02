from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import json
import logging

logger = logging.getLogger(__name__)


class AIProvider(ABC):
    def __init__(self, api_key: str, model: str, temperature: float = 0.7):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature

    @abstractmethod
    def generate_playlist(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int = 50
    ) -> List[int]:
        pass

    def get_max_candidates(self) -> int:
        """Return maximum candidate pool size based on model context window."""
        context_limits = {
            'gemini-2.5-flash': 1000,
            'gpt-5-mini': 500,
            'gpt-5-nano': 500,
            'claude-sonnet-4-5': 300,
            'claude-sonnet-4-5-20250929': 300,
            'claude-3-5-haiku-20241022': 300,
            'claude-3-haiku': 200,
            'command-r7b-12-2024': 500,
            'command-r-plus-08-2024': 500,
            'command-r-08-2024': 400,
        }
        return context_limits.get(self.model, 200)

    def _prepare_prompt(
        self,
        mood_query: str,
        candidate_tracks: List[Dict[str, Any]],
        max_tracks: int
    ) -> str:
        max_candidates = self.get_max_candidates()
        if len(candidate_tracks) > max_candidates:
            logger.warning(f"Truncating {len(candidate_tracks)} candidates to {max_candidates} for model {self.model}")
            candidate_tracks = candidate_tracks[:max_candidates]
        system_prompt = """You are an expert music curator helping create the perfect playlist.
Your task is to select tracks from the provided candidate list that best match the user's mood query.

Rules:
1. Select exactly the requested number of tracks
2. Only select from the provided candidate list
3. Order tracks by relevance to the mood query
4. Consider the track's title, artist, album, genre, and year
5. **CRITICAL: Do NOT select the same track title + artist combination more than once (no duplicates)**
6. **IMPORTANT: Prioritize artist diversity - avoid selecting multiple tracks from the same artist unless necessary**
7. **IMPORTANT: Prioritize album diversity - avoid selecting multiple tracks from the same album unless necessary**
8. Return ONLY a JSON array of track IDs, nothing else

Response format: [1, 5, 12, 23, ...]"""

        tracks_json = json.dumps(candidate_tracks, indent=2)

        user_prompt = f"""Mood Query: "{mood_query}"

Number of tracks to select: {max_tracks}

Candidate tracks:
{tracks_json}

Select {max_tracks} tracks that best match the mood "{mood_query}". Return only a JSON array of track IDs."""

        return system_prompt + "\n\n" + user_prompt

    def _parse_response(self, response: str) -> List[int]:
        try:
            response = response.strip()

            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join([line for line in lines if not line.startswith("```")])

            track_ids = json.loads(response)

            if not isinstance(track_ids, list):
                logger.error("Response is not a list")
                return []

            return [int(tid) for tid in track_ids]

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to parse response: {e}")
            return []

    def _validate_selections(
        self,
        selections: List[int],
        candidate_tracks: List[Dict[str, Any]]
    ) -> List[int]:
        valid_ids = {track['id'] for track in candidate_tracks}
        validated = [tid for tid in selections if tid in valid_ids]

        if len(validated) < len(selections):
            logger.warning(
                f"Filtered out {len(selections) - len(validated)} invalid track IDs"
            )

        return validated
