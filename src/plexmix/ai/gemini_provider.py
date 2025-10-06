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
            # Reduce candidate count if needed to fit in context (before preparing prompt)
            max_candidates = self.get_max_candidates()
            if len(candidate_tracks) > max_candidates:
                print(f"Truncating {len(candidate_tracks)} candidates to {max_candidates} for model {self.model}")
                candidate_tracks = candidate_tracks[:max_candidates]

            prompt = self._prepare_prompt(mood_query, candidate_tracks, max_tracks)

            model = self.genai.GenerativeModel(
                model_name=self.model,
                generation_config={
                    "temperature": self.temperature,
                    "max_output_tokens": 8192,  # Keep reasonable to avoid timeouts
                }
            )

            # Retry up to 2 times on timeout
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    print(f"Gemini API call attempt {attempt + 1}/{max_retries}")
                    response = model.generate_content(
                        prompt,
                        request_options={"timeout": 30}  # 30 second timeout
                    )
                    break
                except Exception as e:
                    if "504" in str(e) or "timeout" in str(e).lower():
                        if attempt < max_retries - 1:
                            print(f"Timeout on attempt {attempt + 1}, retrying...")
                            continue
                    raise

            if not response:
                print("ERROR: Empty response from Gemini")
                logger.error("Empty response from Gemini")
                return []

            # Check for safety blocks or other issues
            print(f"Gemini response received")
            if hasattr(response, 'prompt_feedback'):
                print(f"Gemini prompt_feedback: {response.prompt_feedback}")
            if hasattr(response, 'candidates'):
                print(f"Gemini returned {len(response.candidates)} candidates")
                if len(response.candidates) > 0:
                    candidate = response.candidates[0]
                    print(f"First candidate finish_reason: {candidate.finish_reason}")
                    if candidate.finish_reason == 1:  # MAX_TOKENS
                        print("WARNING: Response hit MAX_TOKENS limit, response may be truncated")

            response_text = None
            try:
                response_text = response.text
                print(f"Successfully got response.text: {len(response_text)} chars")
            except (ValueError, AttributeError) as e:
                print(f"Could not access response.text directly: {e}")
                try:
                    if response.candidates and len(response.candidates) > 0:
                        candidate = response.candidates[0]
                        print(f"Candidate has content: {hasattr(candidate, 'content')}")
                        if hasattr(candidate, 'content'):
                            print(f"Content has parts: {hasattr(candidate.content, 'parts')}")
                            if hasattr(candidate.content, 'parts'):
                                print(f"Number of parts: {len(candidate.content.parts)}")
                                response_text = "".join(part.text for part in candidate.content.parts if hasattr(part, 'text'))
                                print(f"Extracted from parts: {len(response_text) if response_text else 0} chars")
                        elif hasattr(candidate, 'text'):
                            response_text = candidate.text
                            print(f"Extracted from candidate.text: {len(response_text)} chars")
                except Exception as e2:
                    print(f"Failed to extract text from response candidates: {e2}")
                    import traceback
                    traceback.print_exc()

            if not response_text:
                print(f"ERROR: Could not extract text from Gemini response")
                print(f"Response attributes: {dir(response)}")
                if hasattr(response, 'prompt_feedback'):
                    print(f"Prompt feedback: {response.prompt_feedback}")
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
