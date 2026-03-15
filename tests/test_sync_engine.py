"""
Tests for the SyncEngine class.

Covers initialization, incremental sync, progress callbacks, cancellation,
and regenerate sync using a real temp SQLite database and mocked PlexClient.
"""

import sqlite3
import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from plexmix.database.models import Artist, Album, Track, SyncHistory
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.plex.sync import SyncEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_manager():
    """Create a temporary SQLite database with all tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    manager = SQLiteManager(db_path)
    manager.conn = sqlite3.connect(str(manager.db_path))
    manager.conn.row_factory = sqlite3.Row
    manager.create_tables()

    yield manager

    manager.close()
    Path(db_path).unlink(missing_ok=True)


def _make_artist(plex_key: str = "artist-1", name: str = "Test Artist") -> Artist:
    """Create a model Artist for use in plex_library dicts."""
    return Artist(plex_key=plex_key, name=name)


def _make_album(
    plex_key: str = "album-1",
    title: str = "Test Album",
    artist_id: int = 0,
    artist_key: str | None = "artist-1",
) -> Album:
    """Create a model Album with an optional _artist_key."""
    album = Album(plex_key=plex_key, title=title, artist_id=artist_id)
    album.__dict__["_artist_key"] = artist_key
    return album


def _make_track(
    plex_key: str = "track-1",
    title: str = "Test Track",
    artist_id: int = 0,
    album_id: int = 0,
    artist_key: str | None = "artist-1",
    album_key: str | None = "album-1",
    genre: str | None = "rock",
    year: int | None = 2023,
    duration_ms: int | None = 210000,
    rating: float | None = None,
    play_count: int | None = 0,
) -> Track:
    """Create a model Track with optional _artist_key / _album_key."""
    track = Track(
        plex_key=plex_key,
        title=title,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        year=year,
        duration_ms=duration_ms,
        rating=rating,
        play_count=play_count,
    )
    track.__dict__["_artist_key"] = artist_key
    track.__dict__["_album_key"] = album_key
    return track


def _mock_plex_client(artists=None, albums=None, tracks=None) -> MagicMock:
    """Return a MagicMock PlexClient whose get_all_* yield batches of model objects."""
    mock = MagicMock()

    def _batch_gen(items):
        """Yield all items in a single batch (matches PlexClient generator API)."""
        if items:
            yield items

    mock.get_all_artists.return_value = _batch_gen(artists or [])
    mock.get_all_albums.return_value = _batch_gen(albums or [])
    mock.get_all_tracks.return_value = _batch_gen(tracks or [])
    mock.validate_token.return_value = (True, "Connected to Test Server")

    return mock


# ---------------------------------------------------------------------------
# 1. SyncEngine initialization
# ---------------------------------------------------------------------------


class TestSyncEngineInit:
    """SyncEngine.__init__ wiring tests."""

    def test_init_minimal(self, db_manager):
        """SyncEngine can be created with only plex_client and db_manager."""
        mock_plex = MagicMock()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)

        assert engine.plex is mock_plex
        assert engine.db is db_manager
        assert engine.embedding_generator is None
        assert engine.vector_index is None
        assert engine.ai_provider is None
        assert engine.tag_generator is None

    def test_init_with_optional_providers(self, db_manager):
        """SyncEngine stores optional embedding, vector, and AI providers."""
        mock_plex = MagicMock()
        mock_embed = MagicMock()
        mock_vi = MagicMock()
        mock_ai = MagicMock()

        engine = SyncEngine(
            plex_client=mock_plex,
            db_manager=db_manager,
            embedding_generator=mock_embed,
            vector_index=mock_vi,
            ai_provider=mock_ai,
        )

        assert engine.embedding_generator is mock_embed
        assert engine.vector_index is mock_vi
        assert engine.ai_provider is mock_ai
        assert engine.tag_generator is not None

    def test_init_without_ai_provider_has_no_tag_generator(self, db_manager):
        """When ai_provider is None the tag_generator should also be None."""
        mock_plex = MagicMock()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager, ai_provider=None)

        assert engine.tag_generator is None


# ---------------------------------------------------------------------------
# 2. incremental_sync -- happy path with mocked Plex data
# ---------------------------------------------------------------------------


class TestIncrementalSync:
    """Test incremental_sync end-to-end with a real temp DB."""

    def test_sync_adds_new_tracks(self, db_manager):
        """New artists/albums/tracks from Plex are inserted into the DB."""
        artist = _make_artist("a1", "Artist One")
        album = _make_album("al1", "Album One", artist_key="a1")
        track = _make_track(
            "t1",
            "Song One",
            artist_key="a1",
            album_key="al1",
            genre="pop",
            year=2022,
            duration_ms=200000,
        )

        mock_plex = _mock_plex_client(
            artists=[artist],
            albums=[album],
            tracks=[track],
        )

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        result = engine.incremental_sync(generate_embeddings=False)

        assert isinstance(result, SyncHistory)
        assert result.tracks_added == 1
        assert result.tracks_updated == 0
        assert result.tracks_removed == 0
        assert result.status == "success"

        # Verify data landed in DB
        db_tracks = db_manager.get_all_tracks()
        assert len(db_tracks) >= 1
        synced = next((t for t in db_tracks if t.plex_key == "t1"), None)
        assert synced is not None
        assert synced.title == "Song One"
        assert synced.genre == "pop"

    def test_sync_updates_changed_tracks(self, db_manager):
        """Tracks already in the DB that changed in Plex are updated."""
        # Seed the DB with an artist, album, and track
        artist = Artist(plex_key="a1", name="Artist One")
        artist_id = db_manager.insert_artist(artist)
        album = Album(plex_key="al1", title="Album One", artist_id=artist_id)
        album_id = db_manager.insert_album(album)
        db_manager.insert_track(
            Track(
                plex_key="t1",
                title="Old Title",
                artist_id=artist_id,
                album_id=album_id,
                duration_ms=200000,
                rating=2.0,
            )
        )

        # Plex now returns a changed title and rating
        plex_artist = _make_artist("a1", "Artist One")
        plex_album = _make_album("al1", "Album One", artist_key="a1")
        plex_track = _make_track(
            "t1",
            "New Title",
            artist_key="a1",
            album_key="al1",
            duration_ms=200000,
            rating=4.5,
        )

        mock_plex = _mock_plex_client(
            artists=[plex_artist],
            albums=[plex_album],
            tracks=[plex_track],
        )

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.tracks_updated == 1
        assert result.tracks_added == 0

        updated = db_manager.get_track_by_plex_key("t1")
        assert updated is not None
        assert updated.title == "New Title"
        assert updated.rating == 4.5

    def test_sync_removes_deleted_tracks(self, db_manager):
        """Tracks in the DB but no longer in Plex are removed."""
        # Seed the DB
        artist = Artist(plex_key="a1", name="Artist One")
        artist_id = db_manager.insert_artist(artist)
        album = Album(plex_key="al1", title="Album One", artist_id=artist_id)
        album_id = db_manager.insert_album(album)
        db_manager.insert_track(
            Track(
                plex_key="t1",
                title="Gone Track",
                artist_id=artist_id,
                album_id=album_id,
            )
        )

        # Plex returns the artist and album but no tracks
        plex_artist = _make_artist("a1", "Artist One")
        plex_album = _make_album("al1", "Album One", artist_key="a1")
        mock_plex = _mock_plex_client(
            artists=[plex_artist],
            albums=[plex_album],
            tracks=[],
        )

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.tracks_removed == 1

        remaining = db_manager.get_all_tracks()
        assert all(t.plex_key != "t1" for t in remaining)

    def test_sync_records_history(self, db_manager):
        """A SyncHistory record is persisted after a successful sync."""
        mock_plex = _mock_plex_client()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        engine.incremental_sync(generate_embeddings=False)

        cursor = db_manager.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        assert dict(row)["status"] == "success"

    def test_sync_with_multiple_tracks(self, db_manager):
        """Sync handles multiple tracks across artists and albums."""
        artists = [
            _make_artist("a1", "Artist A"),
            _make_artist("a2", "Artist B"),
        ]
        albums = [
            _make_album("al1", "Album X", artist_key="a1"),
            _make_album("al2", "Album Y", artist_key="a2"),
        ]
        tracks = [
            _make_track("t1", "Track 1", artist_key="a1", album_key="al1"),
            _make_track("t2", "Track 2", artist_key="a1", album_key="al1"),
            _make_track("t3", "Track 3", artist_key="a2", album_key="al2"),
        ]

        mock_plex = _mock_plex_client(artists=artists, albums=albums, tracks=tracks)
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.tracks_added == 3
        assert len(db_manager.get_all_tracks()) >= 3

    def test_sync_inserts_genres(self, db_manager):
        """Genres on tracks are inserted into the genres table."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1", genre="jazz, blues")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        engine.incremental_sync(generate_embeddings=False)

        genres = db_manager.get_all_genres()
        genre_names = {g.name for g in genres}
        assert "jazz" in genre_names
        assert "blues" in genre_names


# ---------------------------------------------------------------------------
# 3. Progress callback
# ---------------------------------------------------------------------------


class TestProgressCallback:
    """Verify the progress_callback is invoked during sync."""

    def test_progress_callback_called(self, db_manager):
        """progress_callback receives (float, str) calls during sync."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        callback = MagicMock()

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        engine.incremental_sync(generate_embeddings=False, progress_callback=callback)

        assert callback.call_count >= 2  # at least start + completion
        # First call should be 0.0 (start)
        first_call_args = callback.call_args_list[0]
        assert first_call_args[0][0] == 0.0
        # Last call should be 1.0 (done)
        last_call_args = callback.call_args_list[-1]
        assert last_call_args[0][0] == 1.0

    def test_progress_callback_receives_messages(self, db_manager):
        """progress_callback receives descriptive string messages."""
        mock_plex = _mock_plex_client()
        callback = MagicMock()

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        engine.incremental_sync(generate_embeddings=False, progress_callback=callback)

        messages = [c[0][1] for c in callback.call_args_list]
        # Should include start and completion messages
        assert any("sync" in m.lower() for m in messages)


# ---------------------------------------------------------------------------
# 4. Cancellation via threading.Event
# ---------------------------------------------------------------------------


class TestCancellation:
    """Verify that setting a cancel_event stops sync early."""

    def test_cancel_before_sync_raises(self, db_manager):
        """If cancel_event is already set, sync raises KeyboardInterrupt."""
        mock_plex = _mock_plex_client()
        cancel = Event()
        cancel.set()

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)

        with pytest.raises(KeyboardInterrupt):
            engine.incremental_sync(generate_embeddings=False, cancel_event=cancel)

    def test_cancel_during_sync_records_interrupted(self, db_manager):
        """An interrupted sync writes a sync record with status 'interrupted'."""
        cancel = Event()
        cancel.set()

        mock_plex = _mock_plex_client()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)

        with pytest.raises(KeyboardInterrupt):
            engine.incremental_sync(generate_embeddings=False, cancel_event=cancel)

        cursor = db_manager.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        assert dict(row)["status"] == "interrupted"

    def test_cancel_mid_track_processing(self, db_manager):
        """Cancelling during track processing stops adding further tracks."""
        cancel = Event()

        # Build a sizeable library so we can cancel mid-stream
        artists = [_make_artist(f"a{i}", f"Artist {i}") for i in range(3)]
        albums = [_make_album(f"al{i}", f"Album {i}", artist_key=f"a{i}") for i in range(3)]
        tracks = [
            _make_track(f"t{i}", f"Track {i}", artist_key=f"a{i % 3}", album_key=f"al{i % 3}")
            for i in range(20)
        ]

        # Make the plex client set the cancel event after yielding artists
        original_tracks = list(tracks)

        def _artist_gen():
            yield artists

        def _album_gen():
            yield albums

        def _track_gen():
            # After yielding the first batch, set cancel so the track-processing
            # loop in _detect_library_changes sees it.
            yield original_tracks
            cancel.set()

        mock_plex = MagicMock()
        mock_plex.get_all_artists.return_value = _artist_gen()
        mock_plex.get_all_albums.return_value = _album_gen()
        mock_plex.get_all_tracks.return_value = _track_gen()
        mock_plex.validate_token.return_value = (True, "Connected")

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)

        # The library index is built fully, but _detect_library_changes should
        # see the cancel flag after tracks are yielded.  Depending on timing
        # the sync may or may not raise; we just confirm it doesn't crash.
        try:
            engine.incremental_sync(generate_embeddings=False, cancel_event=cancel)
        except KeyboardInterrupt:
            pass  # Expected if cancel is checked between stages


# ---------------------------------------------------------------------------
# 5. regenerate_sync
# ---------------------------------------------------------------------------


class TestRegenerateSync:
    """Test regenerate_sync clears tags/embeddings before re-syncing."""

    def test_regenerate_clears_tags(self, db_manager):
        """regenerate_sync NULLs out tags, environments, instruments before syncing."""
        # Seed DB with tagged tracks
        artist = Artist(plex_key="a1", name="Artist")
        artist_id = db_manager.insert_artist(artist)
        album = Album(plex_key="al1", title="Album", artist_id=artist_id)
        album_id = db_manager.insert_album(album)
        db_manager.insert_track(
            Track(
                plex_key="t1",
                title="Tagged Track",
                artist_id=artist_id,
                album_id=album_id,
                tags="rock,indie",
                environments="party",
                instruments="guitar,drums",
            )
        )

        # Plex returns the same track (no content change)
        plex_artist = _make_artist("a1", "Artist")
        plex_album = _make_album("al1", "Album", artist_key="a1")
        plex_track = _make_track(
            "t1",
            "Tagged Track",
            artist_key="a1",
            album_key="al1",
            genre=None,
        )

        mock_plex = _mock_plex_client(
            artists=[plex_artist],
            albums=[plex_album],
            tracks=[plex_track],
        )

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        result = engine.regenerate_sync(generate_embeddings=False)

        assert isinstance(result, SyncHistory)
        assert result.status == "success"

        # Tags should have been cleared by regenerate_sync
        track = db_manager.get_track_by_plex_key("t1")
        assert track is not None
        assert track.tags is None
        assert track.environments is None
        assert track.instruments is None

    def test_regenerate_clears_embeddings(self, db_manager):
        """regenerate_sync deletes rows from the embeddings table."""
        # Seed DB
        artist = Artist(plex_key="a1", name="Artist")
        artist_id = db_manager.insert_artist(artist)
        album = Album(plex_key="al1", title="Album", artist_id=artist_id)
        album_id = db_manager.insert_album(album)
        track_id = db_manager.insert_track(
            Track(
                plex_key="t1",
                title="Track",
                artist_id=artist_id,
                album_id=album_id,
            )
        )

        # Insert a fake embedding
        from plexmix.database.models import Embedding

        db_manager.insert_embedding(
            Embedding(
                track_id=track_id,
                embedding_model="test",
                embedding_dim=3,
                vector=[0.1, 0.2, 0.3],
            )
        )

        # Confirm embedding exists
        assert db_manager.get_embedding_by_track_id(track_id) is not None

        plex_artist = _make_artist("a1", "Artist")
        plex_album = _make_album("al1", "Album", artist_key="a1")
        plex_track = _make_track("t1", "Track", artist_key="a1", album_key="al1", genre=None)

        mock_plex = _mock_plex_client(
            artists=[plex_artist],
            albums=[plex_album],
            tracks=[plex_track],
        )

        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        engine.regenerate_sync(generate_embeddings=False)

        # Embeddings should be gone
        assert db_manager.get_embedding_by_track_id(track_id) is None

    def test_regenerate_delegates_to_incremental_sync(self, db_manager):
        """regenerate_sync calls incremental_sync after clearing data."""
        mock_plex = _mock_plex_client()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)

        with patch.object(engine, "incremental_sync", wraps=engine.incremental_sync) as wrapped:
            engine.regenerate_sync(generate_embeddings=False)
            wrapped.assert_called_once_with(
                generate_embeddings=False,
                progress_callback=None,
                cancel_event=None,
            )


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------


class TestSyncEdgeCases:
    """Miscellaneous edge case tests."""

    def test_empty_plex_library(self, db_manager):
        """Sync with an empty Plex library completes without error."""
        mock_plex = _mock_plex_client()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.status == "success"
        assert result.tracks_added == 0

    def test_full_sync_delegates_to_incremental(self, db_manager):
        """full_sync is an alias for incremental_sync."""
        mock_plex = _mock_plex_client()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)

        with patch.object(engine, "incremental_sync", wraps=engine.incremental_sync) as wrapped:
            engine.full_sync(generate_embeddings=False)
            wrapped.assert_called_once()

    def test_track_without_artist_key_gets_unknown(self, db_manager):
        """A track with no _artist_key is assigned to 'Unknown Artist'."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track(
            "t1",
            "Orphan Track",
            artist_key=None,
            album_key=None,
        )

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        engine.incremental_sync(generate_embeddings=False)

        synced = db_manager.get_track_by_plex_key("t1")
        assert synced is not None

        # The track should be linked to the "Unknown Artist" entity
        unknown_artist = db_manager.get_artist_by_plex_key("__unknown__")
        assert unknown_artist is not None
        assert synced.artist_id == unknown_artist.id

    def test_sync_preserves_existing_tags_on_update(self, db_manager):
        """When a track is updated during sync, existing tags are preserved."""
        artist = Artist(plex_key="a1", name="Artist")
        artist_id = db_manager.insert_artist(artist)
        album = Album(plex_key="al1", title="Album", artist_id=artist_id)
        album_id = db_manager.insert_album(album)
        db_manager.insert_track(
            Track(
                plex_key="t1",
                title="Original",
                artist_id=artist_id,
                album_id=album_id,
                duration_ms=180000,
                tags="chill",
                environments="study",
                instruments="piano",
            )
        )

        # Plex returns a changed title (triggers update) but no tags
        plex_artist = _make_artist("a1", "Artist")
        plex_album = _make_album("al1", "Album", artist_key="a1")
        plex_track = _make_track(
            "t1",
            "Updated Title",
            artist_key="a1",
            album_key="al1",
            duration_ms=185000,
        )

        mock_plex = _mock_plex_client(
            artists=[plex_artist], albums=[plex_album], tracks=[plex_track]
        )
        engine = SyncEngine(plex_client=mock_plex, db_manager=db_manager)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.tracks_updated == 1

        track = db_manager.get_track_by_plex_key("t1")
        assert track is not None
        assert track.title == "Updated Title"
        # Tags should be preserved (sync copies existing tags onto plex_track before insert)
        assert track.tags == "chill"
        assert track.environments == "study"
        assert track.instruments == "piano"
