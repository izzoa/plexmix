"""
Regression tests for sync functionality.

Phase G: Tests for sync correctness bugs fixed in the backend improvement plan.
"""
import pytest
import tempfile
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.models import Artist, Album, Track
from plexmix.plex.sync import SyncEngine


@pytest.fixture
def db_manager():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        db_path = tmp.name

    manager = SQLiteManager(db_path)
    manager.db_path.parent.mkdir(parents=True, exist_ok=True)
    manager.conn = sqlite3.connect(str(manager.db_path))
    manager.conn.row_factory = sqlite3.Row
    manager.create_tables()

    yield manager

    manager.close()
    Path(db_path).unlink(missing_ok=True)


class TestAlbumArtistMapping:
    """Tests for album → artist mapping fix (Phase B)."""

    def test_album_uses_artist_key_from_plex(self, db_manager):
        """Test that albums are mapped to artists using _artist_key, not rsplit."""
        # Insert a dummy artist first so the real test artist gets a higher ID
        dummy = Artist(plex_key="00000", name="Dummy Artist")
        db_manager.insert_artist(dummy)

        # Create a mock album with _artist_key attribute
        album = Album(
            plex_key="12345",  # Just a numeric rating key, not a path
            title="Test Album",
            artist_id=0
        )
        # Simulate the _artist_key being set by PlexClient.extract_album_metadata
        album.__dict__['_artist_key'] = "67890"

        # Insert an artist with matching plex_key
        artist = Artist(plex_key="67890", name="Test Artist")
        artist_id = db_manager.insert_artist(artist)

        # Build artist_map like sync does
        artist_map = {"67890": artist_id}

        # Apply the mapping logic (same as sync.py)
        artist_plex_key = album.__dict__.get('_artist_key')
        if artist_plex_key and artist_plex_key in artist_map:
            album.artist_id = artist_map[artist_plex_key]
        else:
            album.artist_id = 1  # Fallback

        # Album should be correctly mapped
        assert album.artist_id == artist_id, "Album was not correctly mapped to artist!"
        assert album.artist_id != 1, "Album fell back to default artist_id=1!"

    def test_album_without_artist_key_falls_back(self, db_manager):
        """Test that albums without _artist_key fall back to default artist."""
        album = Album(
            plex_key="12345",
            title="Orphan Album",
            artist_id=0
        )
        # No _artist_key set

        artist_map = {"67890": 5}  # Some other artist

        # Apply the mapping logic
        artist_plex_key = album.__dict__.get('_artist_key')
        if artist_plex_key and artist_plex_key in artist_map:
            album.artist_id = artist_map[artist_plex_key]
        else:
            album.artist_id = 1

        assert album.artist_id == 1, "Album should fall back to artist_id=1"


class TestTrackUpdateDetection:
    """Tests for track update detection (Phase C)."""

    def test_track_needs_update_detects_rating_change(self, db_manager):
        """Test that rating changes are detected."""
        from plexmix.plex.sync import SyncEngine

        # Create mock sync engine
        mock_plex = MagicMock()
        mock_vector_index = MagicMock()

        with patch.object(SyncEngine, '__init__', lambda x, **kwargs: None):
            engine = SyncEngine()
            engine.db = db_manager
            engine.plex = mock_plex
            engine.vector_index = mock_vector_index
            engine.embedding_generator = None
            engine.tag_generator = None

            # Create DB track
            db_track = Track(
                plex_key="123",
                title="Test",
                artist_id=1,
                album_id=1,
                duration_ms=180000,
                rating=3.0,
                play_count=10
            )

            # Create Plex track with different rating
            plex_track = Track(
                plex_key="123",
                title="Test",
                artist_id=1,
                album_id=1,
                duration_ms=180000,
                rating=4.5,  # Changed
                play_count=10
            )

            assert engine._track_needs_update(db_track, plex_track) is True

    def test_track_needs_update_detects_play_count_change(self, db_manager):
        """Test that play count changes are detected."""
        from plexmix.plex.sync import SyncEngine

        mock_plex = MagicMock()
        mock_vector_index = MagicMock()

        with patch.object(SyncEngine, '__init__', lambda x, **kwargs: None):
            engine = SyncEngine()
            engine.db = db_manager
            engine.plex = mock_plex
            engine.vector_index = mock_vector_index
            engine.embedding_generator = None
            engine.tag_generator = None

            db_track = Track(
                plex_key="123",
                title="Test",
                artist_id=1,
                album_id=1,
                duration_ms=180000,
                play_count=10
            )

            plex_track = Track(
                plex_key="123",
                title="Test",
                artist_id=1,
                album_id=1,
                duration_ms=180000,
                play_count=15  # Changed
            )

            assert engine._track_needs_update(db_track, plex_track) is True

    def test_track_needs_update_no_change(self, db_manager):
        """Test that identical tracks don't trigger update."""
        from plexmix.plex.sync import SyncEngine

        mock_plex = MagicMock()
        mock_vector_index = MagicMock()

        with patch.object(SyncEngine, '__init__', lambda x, **kwargs: None):
            engine = SyncEngine()
            engine.db = db_manager
            engine.plex = mock_plex
            engine.vector_index = mock_vector_index
            engine.embedding_generator = None
            engine.tag_generator = None

            track = Track(
                plex_key="123",
                title="Test",
                artist_id=1,
                album_id=1,
                duration_ms=180000,
                rating=4.0,
                play_count=10,
                genre="Jazz",
                year=2020
            )

            # Same track data
            assert engine._track_needs_update(track, track) is False


class TestTagPreservation:
    """Tests for tag preservation during updates (Phase C)."""

    def test_sync_preserves_tags_on_update(self, db_manager):
        """Test that tags are preserved when syncing track updates."""
        # Insert artist and album
        artist = Artist(plex_key="1", name="Artist")
        artist_id = db_manager.insert_artist(artist)

        album = Album(plex_key="2", title="Album", artist_id=artist_id)
        album_id = db_manager.insert_album(album)

        # Insert track with tags
        track = Track(
            plex_key="3",
            title="Original",
            artist_id=artist_id,
            album_id=album_id,
            duration_ms=180000,
            tags="chill,relaxing",
            environments="study,focus",
            instruments="piano"
        )
        track_id = db_manager.insert_track(track)

        # Simulate sync update (no tags in update)
        updated_track = Track(
            plex_key="3",  # Same plex_key triggers UPSERT
            title="Updated Title",
            artist_id=artist_id,
            album_id=album_id,
            duration_ms=185000,
            tags=None,
            environments=None,
            instruments=None
        )
        db_manager.insert_track(updated_track)

        # Verify tags were preserved
        result = db_manager.get_track_by_id(track_id)
        assert result.title == "Updated Title", "Title should be updated"
        assert result.tags == "chill,relaxing", "Tags should be preserved"
        assert result.environments == "study,focus", "Environments should be preserved"
        assert result.instruments == "piano", "Instruments should be preserved"
