"""MusicBrainz enrichment orchestration — shared between CLI and UI."""

import logging
from threading import Event
from typing import Any, Callable, Dict, List, Optional, Tuple

from plexmix.config.settings import MusicBrainzSettings
from plexmix.database.sqlite_manager import SQLiteManager

logger = logging.getLogger(__name__)


def get_enrichable_tracks(db: SQLiteManager, force: bool = False) -> List[Any]:
    """Get tracks eligible for MusicBrainz enrichment.

    If *force* is True, returns all tracks.
    Otherwise returns only tracks without MusicBrainz data.
    """
    if force:
        return db.get_all_tracks()
    return db.get_tracks_without_musicbrainz()


def enrich_tracks(
    db: SQLiteManager,
    settings: MusicBrainzSettings,
    tracks: List[Any],
    progress_callback: Optional[Callable[[int, int, int, int], None]] = None,
    cancel_event: Optional[Event] = None,
) -> Tuple[int, int, int]:
    """Orchestrate MusicBrainz enrichment for a list of tracks.

    Args:
        db: Database connection.
        settings: MusicBrainz settings.
        tracks: List of Track model objects.
        progress_callback: Optional ``callback(enriched, cached, errors, total)``.
        cancel_event: Optional threading.Event for cancellation.

    Returns ``(enriched_count, cached_count, error_count)``.

    Raises :class:`ImportError` if musicbrainzngs is not installed.
    """
    from plexmix.musicbrainz.client import MusicBrainzClient, MusicBrainzAPIError

    client = MusicBrainzClient(
        contact_email=settings.contact_email,
        confidence_threshold=settings.confidence_threshold,
        rate_limit_delay=settings.rate_limit_delay,
    )

    total = len(tracks)
    enriched = 0
    cached = 0
    errors = 0

    # Batch artist resolution: resolve each artist name once
    artist_mbid_cache: Dict[int, Optional[str]] = {}

    # Pre-fetch artist names for all tracks
    artist_ids = list(set(t.artist_id for t in tracks if t.artist_id))
    artists_map = db.get_artists_by_ids(artist_ids) if artist_ids else {}

    for track in tracks:
        if cancel_event and cancel_event.is_set():
            break

        try:
            artist = artists_map.get(track.artist_id)
            artist_name = artist.name if artist else "Unknown"
            track_title = track.title

            # Check cache first
            cache_key = f"{track_title}::{artist_name}".lower()
            cached_result = db.get_musicbrainz_cache(cache_key, "recording")

            if cached_result:
                _apply_cached_result(db, track, cached_result)
                cached += 1
            else:
                # Search MusicBrainz — MusicBrainzAPIError means transient failure
                try:
                    match = client.search_recording(track_title, artist_name)
                except MusicBrainzAPIError as e:
                    # Transient failure — do NOT cache, count as error
                    logger.debug("Transient API error for '%s': %s", track_title, e)
                    errors += 1
                    if progress_callback:
                        progress_callback(enriched, cached, errors, total)
                    continue

                if match:
                    try:
                        details = client.get_recording_details(match["mbid"])
                    except MusicBrainzAPIError as e:
                        logger.debug(
                            "Transient API error getting details for '%s': %s", track_title, e
                        )
                        errors += 1
                        if progress_callback:
                            progress_callback(enriched, cached, errors, total)
                        continue

                    genres_str = ", ".join(details["tags"]) if details["tags"] else None
                    db.update_track_musicbrainz(
                        track.id,
                        recording_id=details["mbid"],
                        genres=genres_str,
                        recording_type=details["recording_type"],
                    )

                    # Persist release_group_id on the album if available
                    if details.get("release_group_id") and track.album_id:
                        db.update_album_musicbrainz_id(track.album_id, details["release_group_id"])

                    # Cache the result
                    db.set_musicbrainz_cache(
                        cache_key,
                        "recording",
                        details["mbid"],
                        details,
                        match["score"],
                    )

                    enriched += 1
                else:
                    # Definitive no-match — safe to cache
                    db.set_musicbrainz_cache(cache_key, "recording", None, None, 0.0)

            # Resolve artist MBID (once per artist)
            if track.artist_id not in artist_mbid_cache and artist:
                existing_mbid = getattr(artist, "musicbrainz_id", None)
                if existing_mbid:
                    artist_mbid_cache[track.artist_id] = existing_mbid
                else:
                    artist_cache = db.get_musicbrainz_cache(artist_name.lower(), "artist")
                    if artist_cache is not None:
                        # Cache entry exists — honor it (even if mbid is None = negative cache)
                        if artist_cache.get("mbid"):
                            db.update_artist_musicbrainz_id(track.artist_id, artist_cache["mbid"])
                        artist_mbid_cache[track.artist_id] = artist_cache.get("mbid")
                    else:
                        try:
                            artist_match = client.search_artist(artist_name)
                        except MusicBrainzAPIError as e:
                            logger.debug("Transient API error for artist '%s': %s", artist_name, e)
                            artist_mbid_cache[track.artist_id] = None
                            if progress_callback:
                                progress_callback(enriched, cached, errors, total)
                            continue

                        if artist_match:
                            db.update_artist_musicbrainz_id(track.artist_id, artist_match["mbid"])
                            db.set_musicbrainz_cache(
                                artist_name.lower(),
                                "artist",
                                artist_match["mbid"],
                                artist_match,
                                artist_match["score"],
                            )
                            artist_mbid_cache[track.artist_id] = artist_match["mbid"]
                        else:
                            # Definitive no-match — cache it
                            db.set_musicbrainz_cache(artist_name.lower(), "artist", None, None, 0.0)
                            artist_mbid_cache[track.artist_id] = None

        except Exception as e:
            errors += 1
            logger.debug(
                "MusicBrainz enrichment failed for %s: %s",
                getattr(track, "title", "?"),
                e,
            )

        if progress_callback:
            progress_callback(enriched, cached, errors, total)

    return enriched, cached, errors


def _apply_cached_result(db: SQLiteManager, track: Any, cached: Dict[str, Any]) -> None:
    """Apply a cached MusicBrainz result to a track."""
    resp = cached.get("response_json")
    if not resp or not cached.get("mbid"):
        return

    genres_str = ", ".join(resp.get("tags", [])) if resp.get("tags") else None
    db.update_track_musicbrainz(
        track.id,
        recording_id=cached["mbid"],
        genres=genres_str,
        recording_type=resp.get("recording_type"),
    )
