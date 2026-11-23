from typing import List, Dict, Any, Optional, Callable
import json
import logging
import time
import re
import threading

from .base import AIProvider
from .errors import FatalProviderError, classify_provider_error

logger = logging.getLogger(__name__)

# The built-in errors a provider wrapper raises for a reply it could not use: the
# provider answered, only without text to read ("Empty response from OpenAI",
# "Could not extract text from Gemini response"). An error of the connection or of
# the provider's own client library is none of these types.
_UNUSABLE_REPLY_ERRORS = (ValueError, LookupError, TypeError, AttributeError)


def clean_tag_list(value: Any) -> List[str]:
    """What a reply holds for "tags", "environments" or "instruments", as texts with content.

    A text item is stripped and kept only when something is left. A number is kept
    as text. Everything else is dropped: None, a bool, a dict, a list. A value that
    is not a list gives an empty list. So ``[""]``, ``[" "]`` and ``[None]`` are
    empty lists, and no caller can take one of them for tags to write.
    """
    if not isinstance(value, list):
        return []
    cleaned: List[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            text = str(item)
        else:
            continue
        if text:
            cleaned.append(text)
    return cleaned


def provider_did_not_answer(error: BaseException) -> bool:
    """Whether a failed request says that the provider did not answer.

    True for an error that ``classify_provider_error`` calls retryable (a timeout,
    a rate limit, a server error), and for any other error that is not one of the
    built-in types a provider wrapper raises for a reply it could not use. So a
    refused or closed connection counts, and a reply without text does not: that
    request was answered, like one whose reply is not JSON.
    """
    is_retryable, _is_fatal, _message = classify_provider_error(error)
    return is_retryable or not isinstance(error, _UNUSABLE_REPLY_ERRORS)


class RepeatedBatchFailure(FatalProviderError):
    """Several requests in a row were not answered, so the run was stopped.

    Raised by ``generate_tags_batch`` only for a caller that set
    ``max_failed_batches``. Each of those requests failed for a reason that is not
    fatal by itself (a timeout, a server error, a refused connection) and was
    retried first. A request that was answered without usable text is not such a
    failure (``provider_did_not_answer``). ``failed_batches`` is how many failed in
    a row, and ``cause`` is the error of the last one. ``user_message`` is a fixed
    sentence: it holds no text of the provider.
    """

    def __init__(
        self,
        failed_batches: int,
        user_message: str,
        cause: Optional[BaseException] = None,
        partial_results: Optional[Dict[Any, Any]] = None,
    ) -> None:
        super().__init__(
            user_message, user_message=user_message, cause=cause, partial_results=partial_results
        )
        self.failed_batches = failed_batches


class TagGenerator:
    def __init__(self, ai_provider: AIProvider):
        self.ai_provider = ai_provider
        # The error of the last ``_generate_batch`` call whose request the provider
        # did not answer (``provider_did_not_answer``). None when it answered, also
        # when its answer could not be read or held no text.
        self._last_batch_error: Optional[BaseException] = None

    def generate_tags_batch(
        self,
        tracks: List[Dict[str, Any]],
        batch_size: int = 20,
        progress_callback: Optional[Callable[[int, int, int], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        result_sink: Optional[Dict[int, Dict[str, Any]]] = None,
        on_batch: Optional[Callable[[Dict[int, Dict[str, Any]]], None]] = None,
        max_failed_batches: Optional[int] = None,
    ) -> Dict[int, Dict[str, Any]]:
        """Generate tags for ``tracks`` in batches and return the results by track id.

        ``on_batch`` is called after every batch with the results of that batch
        alone, before the next request is sent. A caller that saves there keeps
        every finished batch, whatever stops the run later. A batch that failed
        without a fatal error arrives with empty lists for each of its tracks, and
        so does a track the reply left out: an empty result is not a replacement
        for tags the track already has.

        ``max_failed_batches`` stops a run whose provider no longer answers. When
        that many batches in a row end with a request that was not answered (after
        the retries of each), ``RepeatedBatchFailure`` is raised with the results
        so far. A batch that was answered ends the row, whether its reply could be
        used or not: a reply that is not JSON, and a reply without text. Without
        the argument (None) every batch is tried, however many fail.
        """
        logger.debug(f"Generating tags for {len(tracks)} tracks")

        total_batches = (len(tracks) + batch_size - 1) // batch_size
        # If a sink is supplied, accumulate into it so a caller can recover the
        # partial results even if a KeyboardInterrupt propagates mid-run.
        all_results: Dict[int, Dict[str, Any]] = result_sink if result_sink is not None else {}
        tracks_tagged = 0
        failed_in_a_row = 0

        for batch_num in range(total_batches):
            # Check for cancellation
            if cancel_event and cancel_event.is_set():
                logger.info("Tag generation cancelled by user")
                break

            start_idx = batch_num * batch_size
            end_idx = min((batch_num + 1) * batch_size, len(tracks))
            batch_tracks = tracks[start_idx:end_idx]

            # Report progress
            if progress_callback:
                progress_callback(batch_num + 1, total_batches, tracks_tagged)

            # Generate tags for this batch
            self._last_batch_error = None
            try:
                batch_results = self._generate_batch(batch_tracks)
            except FatalProviderError as e:
                # Abort the whole run; hand back whatever succeeded so far.
                e.partial_results = all_results
                logger.error(
                    "Aborting tag generation after a fatal provider error: %s",
                    e.user_message,
                )
                raise
            all_results.update(batch_results)
            if on_batch is not None:
                on_batch(batch_results)
            tracks_tagged += len(batch_tracks)

            # Final progress update for this batch
            if progress_callback:
                progress_callback(batch_num + 1, total_batches, tracks_tagged)

            batch_error = self._last_batch_error
            failed_in_a_row = failed_in_a_row + 1 if batch_error is not None else 0
            if max_failed_batches is not None and 0 < max_failed_batches <= failed_in_a_row:
                raise self._repeated_failure(failed_in_a_row, batch_error, all_results)

        return all_results

    def _repeated_failure(
        self,
        failed_batches: int,
        error: Optional[BaseException],
        results: Dict[int, Dict[str, Any]],
    ) -> RepeatedBatchFailure:
        """The error that stops a run after ``failed_batches`` failed requests in a row."""
        message = f"The AI provider failed {failed_batches} requests in a row."
        if error is not None:
            is_retryable, _is_fatal, kind = classify_provider_error(error)
            if is_retryable:
                # One of the fixed sentences. The text of an unknown error is the
                # provider's own and is left to the caller, who can redact it.
                message = f"{message} {kind}"
        logger.error("Stopping tag generation: %s", message)
        return RepeatedBatchFailure(failed_batches, message, cause=error, partial_results=results)

    def _generate_batch(self, tracks: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        prompt = self._prepare_tag_prompt(tracks)

        max_retries = 3
        base_delay: float = 1

        for attempt in range(max_retries):
            try:
                response = self._call_ai_provider(prompt)
                parsed_tags = self._parse_tag_response(response, tracks)
                return parsed_tags
            except json.JSONDecodeError as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (attempt + 1)
                    logger.warning(
                        f"JSON parse error (attempt {attempt + 1}/{max_retries}). Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"Failed to parse JSON after {max_retries} attempts: {e}")
                    return {
                        track["id"]: {"tags": [], "environments": [], "instruments": []}
                        for track in tracks
                    }
            except Exception as e:
                is_retryable, is_fatal, user_message = classify_provider_error(e)

                # Fatal (auth / 4xx / HTML edge rejection): abort the whole run.
                if is_fatal:
                    logger.error(f"Fatal AI provider error during tagging: {e}")
                    raise FatalProviderError(str(e), user_message=user_message, cause=e)

                # Retryable (rate limit / timeout / 5xx): back off and retry.
                if is_retryable and attempt < max_retries - 1:
                    retry_after = self._extract_retry_delay(str(e))
                    if retry_after:
                        delay = retry_after * 1.5
                        logger.warning(
                            f"API error (attempt {attempt + 1}/{max_retries}). Server suggested "
                            f"{retry_after}s, using {delay:.1f}s with backoff..."
                        )
                    else:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            f"Retryable API error (attempt {attempt + 1}/{max_retries}). "
                            f"Retrying in {delay}s..."
                        )
                    time.sleep(delay)
                    continue

                # Retryable but exhausted, or unknown/non-fatal: degrade this batch only.
                logger.error(f"Failed to generate tags for batch: {e}")
                if provider_did_not_answer(e):
                    # Counts toward the row of requests that stops a regenerate run.
                    self._last_batch_error = e
                return {
                    track["id"]: {"tags": [], "environments": [], "instruments": []}
                    for track in tracks
                }

        return {
            track["id"]: {"tags": [], "environments": [], "instruments": []} for track in tracks
        }

    def _extract_retry_delay(self, error_message: str) -> Optional[float]:
        retry_match = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", error_message)
        if retry_match:
            return float(retry_match.group(1))

        retry_after_match = re.search(r"Retry-After:\s*(\d+)", error_message, re.IGNORECASE)
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
            entry: Dict[str, Any] = {
                "id": track["id"],
                "title": track["title"],
                "artist": track["artist"],
                "genre": track.get("genre", "unknown"),
            }
            if track.get("musicbrainz_genres"):
                entry["musicbrainz_genres"] = track["musicbrainz_genres"]
            if track.get("recording_type"):
                entry["recording_type"] = track["recording_type"]
            tracks_list.append(entry)

        tracks_json = json.dumps(tracks_list, indent=2)

        user_prompt = f"""Assign tags to the following songs:

{tracks_json}

Return a JSON object mapping each track ID to an array of 3-5 descriptive tags."""

        return system_prompt + "\n\n" + user_prompt

    def _call_ai_provider(self, prompt: str) -> str:
        """Call the AI provider using the uniform complete() interface."""
        try:
            # Use the uniform complete() interface with tagging-specific settings
            return self.ai_provider.complete(
                prompt=prompt,
                temperature=0.3,  # Lower temperature for consistent tagging
                max_tokens=8192,
                timeout=60,  # Longer timeout for batch tagging
            )
        except Exception as e:
            logger.error(f"AI provider call failed: {e}")
            raise

    @staticmethod
    def _capped(value: Any, limit: int) -> List[str]:
        """The texts with content in ``value``, in lower case, ``limit`` at most."""
        return [text.lower() for text in clean_tag_list(value)][:limit]

    def _parse_tag_response(
        self, response: str, tracks: List[Dict[str, Any]]
    ) -> Dict[int, Dict[str, Any]]:
        try:
            response = response.strip()

            if response.startswith("```"):
                lines = response.split("\n")
                response = "\n".join([line for line in lines if not line.startswith("```")])

            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                response = json_match.group(0)

            response = re.sub(r",\s*}", "}", response)
            response = re.sub(r",\s*\]", "]", response)

            tags_dict = json.loads(response)

            result = {}
            for track in tracks:
                track_id = track["id"]
                track_id_str = str(track_id)

                if track_id_str in tags_dict:
                    data = tags_dict[track_id_str]

                    if isinstance(data, dict):
                        environments = data.get("environments", [])
                        instruments = data.get("instruments", [])
                        # One text in place of a list is taken for a list of one.
                        if isinstance(environments, str):
                            environments = [environments]
                        if isinstance(instruments, str):
                            instruments = [instruments]

                        result[track_id] = {
                            "tags": self._capped(data.get("tags", []), 5),
                            "environments": self._capped(environments, 3),
                            "instruments": self._capped(instruments, 3),
                        }
                    elif isinstance(data, list):
                        result[track_id] = {
                            "tags": self._capped(data, 5),
                            "environments": [],
                            "instruments": [],
                        }
                    else:
                        result[track_id] = {"tags": [], "environments": [], "instruments": []}
                else:
                    result[track_id] = {"tags": [], "environments": [], "instruments": []}

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Problematic response (first 500 chars): {response[:500]}")
            raise
        except Exception as e:
            logger.error(f"Failed to parse tag response: {e}")
            raise
