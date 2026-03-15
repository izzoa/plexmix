"""Playlist generation orchestration — shared filter building."""

from typing import Any, Dict, Optional


def safe_int(value: str) -> Optional[int]:
    """Parse a string to int, returning None on failure or empty input."""
    try:
        return int(value) if value else None
    except (ValueError, TypeError):
        return None


def safe_float(value: str) -> Optional[float]:
    """Parse a string to float, returning None on failure or empty input."""
    try:
        return float(value) if value else None
    except (ValueError, TypeError):
        return None


def build_generation_filters(
    *,
    genre: Optional[str] = None,
    year: Optional[int] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    environment: Optional[str] = None,
    instrument: Optional[str] = None,
    tempo_min: Optional[float] = None,
    tempo_max: Optional[float] = None,
    energy_level: Optional[str] = None,
    key: Optional[str] = None,
    danceability_min: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a filter dict for :meth:`PlaylistGenerator.generate`.

    Only includes parameters with non-None values.
    """
    filters: Dict[str, Any] = {}
    if genre:
        filters["genre"] = genre
    if year is not None:
        filters["year"] = year
    if year_min is not None:
        filters["year_min"] = year_min
    if year_max is not None:
        filters["year_max"] = year_max
    if environment:
        filters["environment"] = environment
    if instrument:
        filters["instrument"] = instrument
    if tempo_min is not None:
        filters["tempo_min"] = tempo_min
    if tempo_max is not None:
        filters["tempo_max"] = tempo_max
    if energy_level:
        filters["energy_level"] = energy_level
    if key:
        filters["key"] = key
    if danceability_min is not None:
        filters["danceability_min"] = danceability_min
    return filters
