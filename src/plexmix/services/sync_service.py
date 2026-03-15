"""Sync orchestration service — shared infrastructure for CLI and UI.

Provides Plex connection, database setup, and vector index building
used across sync, playlist, tagging, and other workflows.
"""

import logging
from typing import Optional

from plexmix.config import credentials
from plexmix.config.settings import Settings
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex.client import PlexClient
from plexmix.utils.embeddings import EmbeddingGenerator

logger = logging.getLogger(__name__)


class PlexConnectionError(Exception):
    """Raised when Plex connection cannot be established."""

    pass


def connect_plex(settings: Settings) -> PlexClient:
    """Connect to Plex server and select the configured music library.

    Raises :class:`PlexConnectionError` if the token is missing, the server
    is unreachable, or the configured library cannot be selected.
    """
    plex_token = credentials.get_plex_token()
    if not plex_token or not settings.plex.url:
        raise PlexConnectionError("Plex not configured. Run 'plexmix config init' first.")

    plex_client = PlexClient(settings.plex.url, plex_token)
    if not plex_client.connect():
        raise PlexConnectionError("Failed to connect to Plex server.")

    if settings.plex.library_name:
        plex_client.select_library(settings.plex.library_name)

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


def build_vector_index(
    settings: Settings, embedding_generator: EmbeddingGenerator
) -> VectorIndex:
    """Create a :class:`VectorIndex` using the embedding generator's dimension."""
    index_path = settings.database.get_index_path()
    return VectorIndex(
        dimension=embedding_generator.get_dimension(),
        index_path=str(index_path),
    )
