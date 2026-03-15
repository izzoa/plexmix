"""Centralized constants for tunable parameters used across CLI, UI, and services.

These values appear in multiple modules. Changing them here propagates everywhere.
Domain-fixed values (sample rates, embedding dimensions) stay with their respective modules.
"""

# ── Batch processing ──────────────────────────────────────────────────

TAG_BATCH_SIZE: int = 20
"""Number of tracks per AI tag-generation request."""

EMBEDDING_BATCH_SIZE: int = 50
"""Number of tracks per embedding-generation batch."""

# ── Pagination ────────────────────────────────────────────────────────

LIBRARY_PAGE_SIZE: int = 50
"""Default page size for the Library UI table."""

# ── Playlist generation ───────────────────────────────────────────────

DEFAULT_PLAYLIST_LENGTH: int = 50
"""Default number of tracks in a generated playlist."""

DEFAULT_POOL_MULTIPLIER: int = 25
"""Default candidate-pool multiplier (pool = multiplier × playlist length)."""

# ── UI limits ─────────────────────────────────────────────────────────

GENERATION_LOG_MAX: int = 25
"""Maximum number of entries kept in the generator progress log."""

# ── Diversity constraints ─────────────────────────────────────────────

MAX_ARTIST_REPEATS: int = 3
"""Maximum times the same artist can appear in one playlist."""

MAX_ALBUM_REPEATS: int = 2
"""Maximum times the same album can appear in one playlist."""

# ── Retry defaults ────────────────────────────────────────────────────

DEFAULT_MAX_RETRIES: int = 3
"""Default retry count for API calls (AI providers, Plex, embeddings)."""

DEFAULT_RETRY_DELAY: float = 1.0
"""Base delay in seconds between retries (exponential backoff)."""
