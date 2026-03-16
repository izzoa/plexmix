"""MusicBrainz API client with lazy dependency import and rate limiting."""

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MusicBrainzAPIError(Exception):
    """Raised on transient MusicBrainz API failures (network, rate-limit, server error).

    Callers should NOT cache these results — only definitive no-match results
    should be cached.
    """


class MusicBrainzClient:
    """Wraps musicbrainzngs with lazy import, rate limiting, and confidence filtering."""

    def __init__(
        self,
        contact_email: str = "",
        confidence_threshold: float = 80.0,
        rate_limit_delay: float = 1.0,
    ) -> None:
        try:
            import musicbrainzngs

            self.mb = musicbrainzngs
        except ImportError:
            raise ImportError("musicbrainzngs not installed. Run: pip install musicbrainzngs")

        app_name = "PlexMix"
        app_version = "0.8.5"
        self.mb.set_useragent(app_name, app_version, contact_email or "plexmix-user")

        self.confidence_threshold = confidence_threshold
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time: float = 0.0

    def _rate_limit(self) -> None:
        """Enforce minimum delay between API calls."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.monotonic()

    def search_recording(self, title: str, artist: str) -> Optional[Dict[str, Any]]:
        """Fuzzy match a recording by title and artist.

        Returns the best result above the confidence threshold, or None.
        """
        self._rate_limit()
        try:
            result = self.mb.search_recordings(
                query=f'recording:"{title}" AND artist:"{artist}"',
                limit=5,
            )
        except Exception as e:
            raise MusicBrainzAPIError(f"Recording search failed for '{title}': {e}") from e

        recordings = result.get("recording-list", [])
        if not recordings:
            return None

        best = recordings[0]
        score = int(best.get("ext:score", 0))
        if score < self.confidence_threshold:
            logger.debug(
                "Recording match below threshold: %s by %s (score=%d)",
                title,
                artist,
                score,
            )
            return None

        return {
            "mbid": best["id"],
            "title": best.get("title", ""),
            "score": score,
            "artist_credit": best.get("artist-credit", []),
            "release_list": best.get("release-list", []),
        }

    def get_recording_details(self, mbid: str) -> Dict[str, Any]:
        """Fetch full details for a recording MBID including tags and releases.

        Raises :class:`MusicBrainzAPIError` on transient failures.
        """
        self._rate_limit()
        try:
            result = self.mb.get_recording_by_id(
                mbid, includes=["tags", "releases", "artist-credits"]
            )
        except Exception as e:
            raise MusicBrainzAPIError(f"Recording details failed for {mbid}: {e}") from e

        recording = result.get("recording", {})

        tags = self._extract_tags(recording.get("tag-list", []))
        recording_type = self._detect_recording_type(recording)

        release_group_id = None
        releases = recording.get("release-list", [])
        if releases:
            rg = releases[0].get("release-group", {})
            release_group_id = rg.get("id")

        return {
            "mbid": mbid,
            "title": recording.get("title", ""),
            "tags": tags,
            "recording_type": recording_type,
            "release_group_id": release_group_id,
        }

    def search_artist(self, name: str) -> Optional[Dict[str, Any]]:
        """Look up an artist by name. Returns best match above threshold.

        Raises :class:`MusicBrainzAPIError` on transient failures.
        """
        self._rate_limit()
        try:
            result = self.mb.search_artists(artist=name, limit=3)
        except Exception as e:
            raise MusicBrainzAPIError(f"Artist search failed for '{name}': {e}") from e

        artists = result.get("artist-list", [])
        if not artists:
            return None

        best = artists[0]
        score = int(best.get("ext:score", 0))
        if score < self.confidence_threshold:
            return None

        return {
            "mbid": best["id"],
            "name": best.get("name", ""),
            "score": score,
        }

    def get_artist_tags(self, mbid: str) -> List[str]:
        """Get community genre tags for an artist MBID.

        Raises :class:`MusicBrainzAPIError` on transient failures.
        """
        self._rate_limit()
        try:
            result = self.mb.get_artist_by_id(mbid, includes=["tags"])
        except Exception as e:
            raise MusicBrainzAPIError(f"Artist tags failed for {mbid}: {e}") from e

        artist = result.get("artist", {})
        return self._extract_tags(artist.get("tag-list", []))

    @staticmethod
    def _extract_tags(tag_list: List[Dict[str, Any]]) -> List[str]:
        """Normalize MusicBrainz tags to lowercase sorted list."""
        tags = []
        for tag_entry in tag_list:
            name = tag_entry.get("name", "").strip().lower()
            if name:
                tags.append(name)
        # Sort by count (descending) if available, then alphabetically
        return sorted(set(tags))

    @staticmethod
    def _detect_recording_type(recording: Dict[str, Any]) -> Optional[str]:
        """Detect if a recording is live, remix, cover, etc. from disambiguation."""
        disambiguation = recording.get("disambiguation", "").lower()

        type_keywords = {
            "live": "live",
            "remix": "remix",
            "cover": "cover",
            "acoustic": "acoustic",
            "demo": "demo",
            "instrumental": "instrumental",
            "radio edit": "radio edit",
        }
        for keyword, rtype in type_keywords.items():
            if keyword in disambiguation:
                return rtype

        # Check title for common patterns
        title = recording.get("title", "").lower()
        if "(live" in title or "[live" in title:
            return "live"
        if "(remix" in title or "[remix" in title:
            return "remix"
        if "(acoustic" in title or "[acoustic" in title:
            return "acoustic"

        return None
