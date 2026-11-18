"""Sync orchestration service — shared infrastructure for CLI and UI.

Provides Plex connection, database setup, and vector index building
used across sync, playlist, tagging, and other workflows.
"""

import logging
from typing import Literal

from plexmix.config import credentials
from plexmix.config.settings import Settings
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex.client import PlexClient
from plexmix.utils.embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)


# Why a Plex connection failed, for callers (the Web UI) that need more than the
# message. The messages themselves are written for CLI users and stay unchanged.
PlexErrorCode = Literal["not_configured", "unreachable", "no_library", "library_missing"]
PLEX_ERROR_CODES: tuple[PlexErrorCode, ...] = (
    "not_configured",
    "unreachable",
    "no_library",
    "library_missing",
)


class PlexConnectionError(Exception):
    """Raised when Plex connection cannot be established.

    ``code`` names the failure: ``not_configured`` (no server URL or token),
    ``unreachable`` (the server did not answer), ``no_library`` (no music library
    chosen) or ``library_missing`` (the chosen library was not found). It is
    keyword-only and defaults to ``unreachable``, so ``str(e)`` and the positional
    arguments are exactly what they were before the code existed.
    """

    def __init__(self, *args: object, code: PlexErrorCode = "unreachable") -> None:
        super().__init__(*args)
        self.code: PlexErrorCode = code


def connect_plex(settings: Settings) -> PlexClient:
    """Connect to Plex server and select the configured music library.

    Raises :class:`PlexConnectionError` if the token is missing, the server
    is unreachable, or the configured library cannot be selected.
    """
    # Fall back to the token stored in config.yaml when the keyring is unavailable
    # (headless/container) — otherwise a keyring-less token is unrecoverable.
    plex_token = credentials.get_plex_token() or settings.plex.token
    if not plex_token or not settings.plex.url:
        raise PlexConnectionError(
            "Plex not configured. Run 'plexmix config init' first.", code="not_configured"
        )

    plex_client = PlexClient(settings.plex.url, plex_token)
    if not plex_client.connect():
        raise PlexConnectionError("Failed to connect to Plex server.", code="unreachable")

    if not settings.plex.library_name:
        raise PlexConnectionError(
            "No Plex music library configured. Run 'plexmix config init' to select one.",
            code="no_library",
        )

    # A failed selection leaves music_library=None, which would make every fetch
    # return empty and trick sync into deleting the whole local library. Fail loudly.
    if not plex_client.select_library(settings.plex.library_name):
        # A lost connection says nothing about the library setting, so it is not
        # reported as a missing library. Compared with ``==`` on purpose: only the
        # client's own "unreachable" takes this path (a mocked client does not).
        if plex_client.last_select_failure == "unreachable":
            raise PlexConnectionError(
                "Plex stopped answering while the music library was being selected, "
                "so the library setting was not checked.",
                code="unreachable",
            )
        raise PlexConnectionError(
            f"Failed to select Plex library '{settings.plex.library_name}'. "
            "It may have been renamed or removed — check your configuration.",
            code="library_missing",
        )

    return plex_client


def open_db(settings: Settings) -> SQLiteManager:
    """Open the database, run migrations, and ensure tables exist.

    Caller is responsible for closing the returned connection.
    """
    db_path = settings.database.get_db_path()
    db = SQLiteManager(str(db_path))
    db.connect()
    db.create_tables()
    return db


def build_vector_index(settings: Settings, embedding_generator: EmbeddingGenerator) -> VectorIndex:
    """Create a :class:`VectorIndex` using the embedding generator's dimension."""
    index_path = settings.database.get_index_path()
    return VectorIndex(
        dimension=embedding_generator.get_dimension(),
        index_path=str(index_path),
    )
