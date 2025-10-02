from typing import List, Dict, Any, Optional
import json
import logging
import time
import re
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
    ) -> Dict[int, Dict[str, Any]]:
        logger.debug(f"Generating tags for {len(tracks)} tracks")
        return self._generate_batch(tracks)

    def _generate_batch(self, tracks: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        prompt = self._prepare_tag_prompt(tracks)

        max_retries = 3
        base_delay = 1

        for attempt in range(max_retries):
            try:
                response = self._call_ai_provider(prompt)
                parsed_tags = self._parse_tag_response(response, tracks)
                return parsed_tags
            except json.JSONDecodeError as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (attempt + 1)
                    logger.warning(f"JSON parse error (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Failed to parse JSON after {max_retries} attempts: {e}")
                    return {track['id']: {'tags': [], 'environments': [], 'instruments': []} for track in tracks}
            except Exception as e:
                error_str = str(e)

                is_rate_limit = "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower()
                is_timeout = "504" in error_str or "timeout" in error_str.lower() or "timed out" in error_str.lower()
                is_server_error = "500" in error_str or "502" in error_str or "503" in error_str

                if is_rate_limit or is_timeout or is_server_error:
                    if attempt < max_retries - 1:
                        retry_after = self._extract_retry_delay(error_str)

                        if retry_after:
                            delay = retry_after * 1.5
                            logger.warning(f"API error (attempt {attempt + 1}/{max_retries}). Server suggested {retry_after}s, using {delay:.1f}s with backoff...")
                        else:
                            delay = base_delay * (2 ** attempt)
                            if is_rate_limit:
                                logger.warning(f"Rate limit hit (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s...")
                            elif is_timeout:
                                logger.warning(f"Request timeout (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s...")
                            else:
                                logger.warning(f"Server error (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s...")

                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"Failed after {max_retries} attempts: {e}")
                        return {track['id']: {'tags': [], 'environments': [], 'instruments': []} for track in tracks}
                else:
                    logger.error(f"Failed to generate tags for batch: {e}")
                    return {track['id']: {'tags': [], 'environments': [], 'instruments': []} for track in tracks}

        return {track['id']: {'tags': [], 'environments': [], 'instruments': []} for track in tracks}

    def _extract_retry_delay(self, error_message: str) -> Optional[float]:
        retry_match = re.search(r'retry_delay\s*\{\s*seconds:\s*(\d+)', error_message)
        if retry_match:
            return float(retry_match.group(1))

        retry_after_match = re.search(r'Retry-After:\s*(\d+)', error_message, re.IGNORECASE)
        if retry_after_match:
            return float(retry_after_match.group(1))

        return None

    def _prepare_tag_prompt(self, tracks: List[Dict[str, Any]]) -> str:
        system_prompt = """You are a music expert helping to categorize songs with descriptive tags, environment contexts, and primary instruments.

Your task is to analyze each song and provide:
1. **Tags** (3-5 descriptive tags)
2. **Environments** (top 3 best-fit contexts from: work, study, focus, relax, party, workout, sleep, driving, social)
3. **Instruments** (top 3 most prominent instruments from: piano, guitar, saxophone, trumpet, drums, bass, synth, vocals, strings, orchestra, flute, violin, cello, harmonica, accordion)

Tags should describe:
- Mood (e.g., energetic, melancholic, upbeat, chill, intense)
- Energy level (e.g., high-energy, low-energy, moderate)
- Tempo feel (e.g., fast-paced, slow, mid-tempo)
- Emotional tone (e.g., happy, sad, angry, romantic, nostalgic)

Rules:
1. Assign 3-5 tags per song
2. Assign 1-3 environments per song (ordered by best fit)
3. Assign 1-3 instruments per song (ordered by prominence)
4. Use lowercase for all fields
5. Be consistent with naming
6. Return ONLY a JSON object mapping track IDs to objects with tags, environments, and instruments

Example output format:
{
  "1": {
    "tags": ["energetic", "workout", "high-energy", "upbeat"],
    "environments": ["workout", "party", "driving"],
    "instruments": ["guitar", "drums", "bass"]
  },
  "2": {
    "tags": ["melancholic", "slow", "sad", "introspective"],
    "environments": ["study", "focus", "relax"],
    "instruments": ["piano", "strings"]
  }
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
                        "max_output_tokens": 8192,
                    }
                )
                response = model.generate_content(prompt)

                if not response:
                    raise ValueError("Empty response from Gemini")

                try:
                    response_text = response.text
                except ValueError:
                    if response.candidates and response.candidates[0].content.parts:
                        response_text = "".join(part.text for part in response.candidates[0].content.parts)
                    else:
                        raise ValueError("Could not extract text from Gemini response")

                return response_text

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
    ) -> Dict[int, Dict[str, Any]]:
        try:
            response = response.strip()

            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join([line for line in lines if not line.startswith("```")])

            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                response = json_match.group(0)

            response = re.sub(r',\s*}', '}', response)
            response = re.sub(r',\s*\]', ']', response)

            tags_dict = json.loads(response)

            result = {}
            for track in tracks:
                track_id = track['id']
                track_id_str = str(track_id)

                if track_id_str in tags_dict:
                    data = tags_dict[track_id_str]

                    if isinstance(data, dict):
                        tags = data.get('tags', [])
                        environments = data.get('environments', [])
                        instruments = data.get('instruments', [])

                        if isinstance(tags, list):
                            tags = [str(tag).lower().strip() for tag in tags[:5]]
                        else:
                            tags = []

                        if isinstance(environments, list):
                            environments = [str(env).lower().strip() for env in environments[:3]]
                        elif isinstance(environments, str):
                            environments = [str(environments).lower().strip()]
                        else:
                            environments = []

                        if isinstance(instruments, list):
                            instruments = [str(inst).lower().strip() for inst in instruments[:3]]
                        elif isinstance(instruments, str):
                            instruments = [str(instruments).lower().strip()]
                        else:
                            instruments = []

                        result[track_id] = {
                            'tags': tags,
                            'environments': environments,
                            'instruments': instruments
                        }
                    elif isinstance(data, list):
                        result[track_id] = {
                            'tags': [str(tag).lower().strip() for tag in data[:5]],
                            'environments': [],
                            'instruments': []
                        }
                    else:
                        result[track_id] = {'tags': [], 'environments': [], 'instruments': []}
                else:
                    result[track_id] = {'tags': [], 'environments': [], 'instruments': []}

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Problematic response (first 500 chars): {response[:500]}")
            raise
        except Exception as e:
            logger.error(f"Failed to parse tag response: {e}")
            raise
