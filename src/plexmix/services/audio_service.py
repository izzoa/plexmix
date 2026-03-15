"""Audio analysis orchestration — shared between CLI and UI."""

import logging
from typing import Any, Callable, List, Optional, Tuple

from plexmix.config.settings import Settings
from plexmix.database.sqlite_manager import SQLiteManager

logger = logging.getLogger(__name__)


def get_analyzable_tracks(db: SQLiteManager, force: bool = False) -> List[Any]:
    """Get tracks eligible for audio analysis.

    If *force* is True, returns all tracks with file paths.
    Otherwise returns only tracks missing audio features.
    """
    if force:
        return [t for t in db.get_all_tracks() if t.file_path]
    return db.get_tracks_without_audio_features()


def analyze_tracks(
    db: SQLiteManager,
    settings: Settings,
    tracks: List[Any],
    progress_callback: Optional[Callable[[int, int, int], None]] = None,
) -> Tuple[int, int]:
    """Run sequential audio analysis on the given tracks.

    Args:
        db: Database connection.
        settings: Application settings (audio section).
        tracks: List of Track objects to analyze.
        progress_callback: Optional ``callback(analyzed, errors, total)``.

    Returns ``(analyzed_count, error_count)``.

    Raises :class:`ImportError` if Essentia is not installed.
    """
    from plexmix.audio.analyzer import EssentiaAnalyzer

    analyzer = EssentiaAnalyzer()
    duration_limit = settings.audio.duration_limit

    total = len(tracks)
    analyzed = 0
    errors = 0

    for track in tracks:
        try:
            if track.file_path is None or track.id is None:
                continue
            resolved_path = settings.audio.resolve_path(track.file_path)
            features = analyzer.analyze(resolved_path, duration_limit=duration_limit)
            db.insert_audio_features(track.id, features.to_dict())
            analyzed += 1
        except Exception as e:
            errors += 1
            logger.debug("Audio analysis failed for %s: %s", getattr(track, "title", "?"), e)

        if progress_callback:
            progress_callback(analyzed, errors, total)

    return analyzed, errors
