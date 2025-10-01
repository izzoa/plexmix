from typing import List, Dict, Any
import json
import logging
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .base import AIProvider

logger = logging.getLogger(__name__)


class TagGenerator:
    def __init__(self, ai_provider: AIProvider):
        self.ai_provider = ai_provider

    def generate_tags_batch(
        self,
        tracks: List[Dict[str, Any]],
        batch_size: int = 20
    ) -> Dict[int, List[str]]:
        logger.info(f"Generating tags for {len(tracks)} tracks")

        all_tags = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
        ) as progress:
            task = progress.add_task("Generating tags...", total=len(tracks))

            for i in range(0, len(tracks), batch_size):
                batch = tracks[i:i + batch_size]
                batch_tags = self._generate_batch(batch)
                all_tags.update(batch_tags)
                progress.update(task, advance=len(batch))

        logger.info(f"Generated tags for {len(all_tags)} tracks")
        return all_tags

    def _generate_batch(self, tracks: List[Dict[str, Any]]) -> Dict[int, List[str]]:
        prompt = self._prepare_tag_prompt(tracks)

        try:
            response = self._call_ai_provider(prompt)
            parsed_tags = self._parse_tag_response(response, tracks)
            return parsed_tags
        except Exception as e:
            logger.error(f"Failed to generate tags for batch: {e}")
            return {track['id']: [] for track in tracks}

    def _prepare_tag_prompt(self, tracks: List[Dict[str, Any]]) -> str:
        system_prompt = """You are a music expert helping to categorize songs with descriptive tags.

Your task is to assign up to 5 tags per song based on the song title, artist, and genre.

Tags should describe:
- Mood (e.g., energetic, melancholic, upbeat, chill, intense)
- Energy level (e.g., high-energy, low-energy, moderate)
- Activity fit (e.g., workout, study, party, sleep, driving)
- Tempo feel (e.g., fast-paced, slow, mid-tempo)
- Emotional tone (e.g., happy, sad, angry, romantic, nostalgic)

Rules:
1. Assign 3-5 tags per song
2. Use lowercase, single words or hyphenated phrases
3. Be consistent with tag naming
4. Return ONLY a JSON object mapping track IDs to tag arrays

Example output format:
{
  "1": ["energetic", "workout", "high-energy", "upbeat"],
  "2": ["melancholic", "slow", "sad", "introspective", "chill"]
}"""

        tracks_list = []
        for track in tracks:
            tracks_list.append({
                'id': track['id'],
                'title': track['title'],
                'artist': track['artist'],
                'genre': track.get('genre', 'unknown')
            })

        tracks_json = json.dumps(tracks_list, indent=2)

        user_prompt = f"""Assign tags to the following songs:

{tracks_json}

Return a JSON object mapping each track ID to an array of 3-5 descriptive tags."""

        return system_prompt + "\n\n" + user_prompt

    def _call_ai_provider(self, prompt: str) -> str:
        try:
            import google.generativeai as genai

            if hasattr(self.ai_provider, 'genai'):
                model = self.ai_provider.genai.GenerativeModel(
                    model_name=self.ai_provider.model,
                    generation_config={
                        "temperature": 0.3,
                        "max_output_tokens": 4096,
                    }
                )
                response = model.generate_content(prompt)
                return response.text

            elif hasattr(self.ai_provider, 'client'):
                if hasattr(self.ai_provider.client, 'chat'):
                    response = self.ai_provider.client.chat.completions.create(
                        model=self.ai_provider.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.3,
                        max_tokens=4096
                    )
                    return response.choices[0].message.content
                else:
                    response = self.ai_provider.client.messages.create(
                        model=self.ai_provider.model,
                        max_tokens=4096,
                        temperature=0.3,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    return response.content[0].text

            else:
                raise ValueError("Unknown AI provider type")

        except Exception as e:
            logger.error(f"AI provider call failed: {e}")
            raise

    def _parse_tag_response(
        self,
        response: str,
        tracks: List[Dict[str, Any]]
    ) -> Dict[int, List[str]]:
        try:
            response = response.strip()

            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join([line for line in lines if not line.startswith("```")])

            tags_dict = json.loads(response)

            result = {}
            for track in tracks:
                track_id = track['id']
                track_id_str = str(track_id)

                if track_id_str in tags_dict:
                    tags = tags_dict[track_id_str]
                    if isinstance(tags, list):
                        tags = [str(tag).lower().strip() for tag in tags[:5]]
                        result[track_id] = tags
                    else:
                        result[track_id] = []
                else:
                    result[track_id] = []

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            return {track['id']: [] for track in tracks}
        except Exception as e:
            logger.error(f"Failed to parse tag response: {e}")
            return {track['id']: [] for track in tracks}
