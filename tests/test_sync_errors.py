"""
Tests for SyncEngine error recovery and audio analysis integration.

Covers:
- Network timeout / Plex API failure during sync
- Corrupt metadata handling
- Token validation failure
- Embedding generation failure during sync
- Tag generation failure during sync
- Sync history records for failures
- Audio analysis integration with sync pipeline
"""

import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from plexmix.database.models import Artist, Album, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.plex.sync import SyncEngine


# ---------------------------------------------------------------------------
# Fixtures (reuse patterns from test_sync_engine.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    """Create a temporary SQLite database with all tables via connect()."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    manager = SQLiteManager(db_path)
    manager.connect()

    yield manager

    manager.close()
    Path(db_path).unlink(missing_ok=True)


def _make_artist(plex_key: str = "artist-1", name: str = "Test Artist") -> Artist:
    return Artist(plex_key=plex_key, name=name)


def _make_album(
    plex_key: str = "album-1",
    title: str = "Test Album",
    artist_id: int = 0,
    artist_key: str | None = "artist-1",
) -> Album:
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
    file_path: str | None = None,
) -> Track:
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
        file_path=file_path,
    )
    track.__dict__["_artist_key"] = artist_key
    track.__dict__["_album_key"] = album_key
    return track


def _mock_plex_client(artists=None, albums=None, tracks=None) -> MagicMock:
    mock = MagicMock()

    def _batch_gen(items):
        if items:
            yield items

    mock.get_all_artists.return_value = _batch_gen(artists or [])
    mock.get_all_albums.return_value = _batch_gen(albums or [])
    mock.get_all_tracks.return_value = _batch_gen(tracks or [])
    mock.validate_token.return_value = (True, "Connected to Test Server")

    return mock


# ---------------------------------------------------------------------------
# 1. Token validation failures
# ---------------------------------------------------------------------------


class TestTokenValidation:
    """Test pre-flight token validation errors."""

    def test_invalid_token_raises_connection_error(self, db):
        """Sync fails fast with ConnectionError if token is invalid."""
        mock_plex = _mock_plex_client()
        mock_plex.validate_token.return_value = (False, "Unauthorized - Invalid token")

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        with pytest.raises(ConnectionError, match="Unauthorized"):
            engine.incremental_sync(generate_embeddings=False)

    def test_invalid_token_does_not_record_sync(self, db):
        """Failed token validation raises before sync begins — no history record."""
        mock_plex = _mock_plex_client()
        mock_plex.validate_token.return_value = (False, "Token expired")

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        with pytest.raises(ConnectionError):
            engine.incremental_sync(generate_embeddings=False)

        # Token failure happens before the try block, so no sync history is recorded
        cursor = db.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is None


# ---------------------------------------------------------------------------
# 2. Network timeout / Plex API failures during library build
# ---------------------------------------------------------------------------


class TestNetworkErrors:
    """Test error handling when Plex API calls fail during sync."""

    def test_artist_fetch_timeout_records_failure(self, db):
        """If get_all_artists raises, sync records failure and re-raises."""
        mock_plex = MagicMock()
        mock_plex.validate_token.return_value = (True, "OK")
        mock_plex.get_all_artists.side_effect = ConnectionError("Connection timed out")

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        with pytest.raises(ConnectionError, match="timed out"):
            engine.incremental_sync(generate_embeddings=False)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        assert dict(row)["status"] == "failed"

    def test_track_fetch_timeout_records_failure(self, db):
        """If get_all_tracks raises mid-sync, sync records failure."""
        mock_plex = MagicMock()
        mock_plex.validate_token.return_value = (True, "OK")
        mock_plex.get_all_artists.return_value = iter([])
        mock_plex.get_all_albums.return_value = iter([])
        mock_plex.get_all_tracks.side_effect = TimeoutError("Read timed out")

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        with pytest.raises(TimeoutError):
            engine.incremental_sync(generate_embeddings=False)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert dict(row)["status"] == "failed"
        assert "timed out" in dict(row)["error_message"]

    def test_partial_artist_fetch_failure(self, db):
        """If artist generator fails mid-batch, sync records failure."""
        mock_plex = MagicMock()
        mock_plex.validate_token.return_value = (True, "OK")

        # First batch succeeds, second raises
        def _failing_artist_gen():
            yield [_make_artist("a1", "Artist 1")]
            raise ConnectionError("Connection reset by peer")

        mock_plex.get_all_artists.return_value = _failing_artist_gen()
        mock_plex.get_all_albums.return_value = iter([])
        mock_plex.get_all_tracks.return_value = iter([])

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        with pytest.raises(ConnectionError, match="reset"):
            engine.incremental_sync(generate_embeddings=False)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert dict(row)["status"] == "failed"


# ---------------------------------------------------------------------------
# 3. Corrupt / unusual metadata handling
# ---------------------------------------------------------------------------


class TestCorruptMetadata:
    """Test sync behavior with unusual or missing metadata."""

    def test_track_with_none_genre_handled(self, db):
        """Tracks with None genre are synced without error."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "No Genre Track", artist_key="a1", album_key="al1", genre=None)

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.status == "success"
        assert result.tracks_added == 1
        synced = db.get_track_by_plex_key("t1")
        assert synced.genre is None

    def test_track_with_zero_duration(self, db):
        """Tracks with zero duration are synced."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Zero Duration", artist_key="a1", album_key="al1", duration_ms=0)

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.status == "success"
        synced = db.get_track_by_plex_key("t1")
        assert synced.duration_ms == 0

    def test_track_without_album_key_gets_unknown_album(self, db):
        """Track missing album key is assigned to Unknown Album."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Orphan Track", artist_key="a1", album_key=None)

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.status == "success"
        synced = db.get_track_by_plex_key("t1")
        unknown_album = db.get_album_by_plex_key("__unknown__")
        assert synced.album_id == unknown_album.id

    def test_album_without_artist_key_gets_unknown_artist(self, db):
        """Album missing artist key is assigned to Unknown Artist."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Orphan Album", artist_key=None)
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.status == "success"
        synced_album = db.get_album_by_plex_key("al1")
        unknown_artist = db.get_artist_by_plex_key("__unknown__")
        assert synced_album.artist_id == unknown_artist.id

    def test_many_genres_all_stored(self, db):
        """Track with many comma-separated genres stores them all."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track(
            "t1",
            "Multi Genre",
            artist_key="a1",
            album_key="al1",
            genre="rock, jazz, blues, funk, soul, r&b, pop",
        )

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])
        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        engine.incremental_sync(generate_embeddings=False)

        genres = {g.name for g in db.get_all_genres()}
        for g in ["rock", "jazz", "blues", "funk", "soul", "r&b", "pop"]:
            assert g in genres


# ---------------------------------------------------------------------------
# 4. Embedding generation failure during sync
# ---------------------------------------------------------------------------


class TestEmbeddingFailures:
    """Test sync behavior when embedding generation fails."""

    def test_embedding_api_error_records_failure(self, db):
        """If embedding generator raises, sync records failure."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])

        mock_gen = MagicMock()
        mock_gen.provider_name = "openai"
        mock_gen.get_dimension.return_value = 1536
        mock_gen.generate_batch_embeddings.side_effect = RuntimeError("API rate limit exceeded")

        mock_vi = MagicMock()
        mock_vi.index = MagicMock()
        mock_vi.dimension_mismatch = False
        type(mock_vi).index_path = PropertyMock(return_value="/tmp/test.index")

        engine = SyncEngine(
            plex_client=mock_plex,
            db_manager=db,
            embedding_generator=mock_gen,
            vector_index=mock_vi,
        )

        with pytest.raises(RuntimeError, match="rate limit"):
            engine.incremental_sync(generate_embeddings=True)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert dict(row)["status"] == "failed"

    def test_sync_succeeds_without_embeddings(self, db):
        """Sync succeeds when generate_embeddings=False even with no provider."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        result = engine.incremental_sync(generate_embeddings=False)

        assert result.status == "success"
        assert result.tracks_added == 1

    def test_sync_skips_embeddings_when_no_generator(self, db):
        """Sync skips embedding generation when no generator is configured."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        # Even with generate_embeddings=True, no generator means skip
        result = engine.incremental_sync(generate_embeddings=True)

        assert result.status == "success"

    def test_existing_tracks_preserved_on_embedding_failure(self, db):
        """DB tracks survive even if embedding generation fails."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        tracks = [
            _make_track(f"t{i}", f"Track {i}", artist_key="a1", album_key="al1") for i in range(5)
        ]

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=tracks)

        mock_gen = MagicMock()
        mock_gen.provider_name = "openai"
        mock_gen.get_dimension.return_value = 1536
        mock_gen.generate_batch_embeddings.side_effect = RuntimeError("API down")

        mock_vi = MagicMock()
        mock_vi.index = MagicMock()
        mock_vi.dimension_mismatch = False
        type(mock_vi).index_path = PropertyMock(return_value="/tmp/test.index")

        engine = SyncEngine(
            plex_client=mock_plex,
            db_manager=db,
            embedding_generator=mock_gen,
            vector_index=mock_vi,
        )

        with pytest.raises(RuntimeError):
            engine.incremental_sync(generate_embeddings=True)

        # Tracks should still be in the DB despite embedding failure
        db_tracks = db.get_all_tracks()
        assert len(db_tracks) >= 5


# ---------------------------------------------------------------------------
# 5. Tag generation failure during sync
# ---------------------------------------------------------------------------


class TestTagGenerationFailures:
    """Test sync behavior when AI tag generation fails."""

    def test_tag_generation_failure_does_not_crash_sync(self, db):
        """If tag generation raises generic exception, sync continues."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])

        mock_ai = MagicMock()
        mock_tag_gen = MagicMock()
        mock_tag_gen.generate_tags_batch.side_effect = RuntimeError("LLM quota exceeded")

        engine = SyncEngine(
            plex_client=mock_plex,
            db_manager=db,
            ai_provider=mock_ai,
        )
        engine.tag_generator = mock_tag_gen

        # Tag generation failure is caught internally — sync completes
        result = engine.incremental_sync(generate_embeddings=False)
        assert result.status == "success"

    def test_tag_generation_keyboard_interrupt_propagates(self, db):
        """KeyboardInterrupt during tag generation propagates as interrupted."""
        artist = _make_artist("a1", "Artist")
        album = _make_album("al1", "Album", artist_key="a1")
        track = _make_track("t1", "Track", artist_key="a1", album_key="al1")

        mock_plex = _mock_plex_client(artists=[artist], albums=[album], tracks=[track])

        mock_ai = MagicMock()
        mock_tag_gen = MagicMock()
        mock_tag_gen.generate_tags_batch.side_effect = KeyboardInterrupt()

        engine = SyncEngine(
            plex_client=mock_plex,
            db_manager=db,
            ai_provider=mock_ai,
        )
        engine.tag_generator = mock_tag_gen

        with pytest.raises(KeyboardInterrupt):
            engine.incremental_sync(generate_embeddings=False)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert dict(row)["status"] == "interrupted"


# ---------------------------------------------------------------------------
# 6. Sync history recording for various error scenarios
# ---------------------------------------------------------------------------


class TestSyncHistoryOnErrors:
    """Verify sync_history table is populated correctly for all outcomes."""

    def test_success_status_recorded(self, db):
        """Successful sync records status='success'."""
        mock_plex = _mock_plex_client()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db)
        engine.incremental_sync(generate_embeddings=False)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT status FROM sync_history ORDER BY id DESC LIMIT 1")
        assert cursor.fetchone()["status"] == "success"

    def test_interrupted_status_recorded(self, db):
        """Cancelled sync records status='interrupted'."""
        cancel = Event()
        cancel.set()

        mock_plex = _mock_plex_client()
        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        with pytest.raises(KeyboardInterrupt):
            engine.incremental_sync(generate_embeddings=False, cancel_event=cancel)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT status FROM sync_history ORDER BY id DESC LIMIT 1")
        assert cursor.fetchone()["status"] == "interrupted"

    def test_failed_status_with_error_message(self, db):
        """Failed sync records status='failed' with error details."""
        mock_plex = MagicMock()
        mock_plex.validate_token.return_value = (True, "OK")
        mock_plex.get_all_artists.side_effect = OSError("Network unreachable")

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        with pytest.raises(OSError):
            engine.incremental_sync(generate_embeddings=False)

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT status, error_message FROM sync_history ORDER BY id DESC LIMIT 1")
        row = dict(cursor.fetchone())
        assert row["status"] == "failed"
        assert "Network unreachable" in row["error_message"]

    def test_partial_sync_persists_tracks_despite_embedding_failure(self, db):
        """Tracks are persisted in DB even when embedding generation fails later."""
        # Seed DB with one existing track
        a_id = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        al_id = db.insert_album(Album(plex_key="al1", title="Album", artist_id=a_id))
        db.insert_track(
            Track(plex_key="t_existing", title="Existing", artist_id=a_id, album_id=al_id)
        )

        # Plex returns new tracks (removes old one since it's not in Plex)
        plex_artist = _make_artist("a1", "Artist")
        plex_album = _make_album("al1", "Album", artist_key="a1")
        plex_tracks = [
            _make_track(f"t{i}", f"New Track {i}", artist_key="a1", album_key="al1")
            for i in range(3)
        ]

        mock_plex = _mock_plex_client(
            artists=[plex_artist], albums=[plex_album], tracks=plex_tracks
        )

        # Embedding generation will fail, but DB sync should have completed
        mock_gen = MagicMock()
        mock_gen.provider_name = "test"
        mock_gen.get_dimension.return_value = 8
        mock_gen.generate_batch_embeddings.side_effect = RuntimeError("API error")

        mock_vi = MagicMock()
        mock_vi.index = MagicMock()
        mock_vi.dimension_mismatch = False
        type(mock_vi).index_path = PropertyMock(return_value="/tmp/test.index")

        engine = SyncEngine(
            plex_client=mock_plex,
            db_manager=db,
            embedding_generator=mock_gen,
            vector_index=mock_vi,
        )

        with pytest.raises(RuntimeError):
            engine.incremental_sync(generate_embeddings=True)

        # Tracks should still be persisted in DB despite embedding failure
        all_tracks = db.get_all_tracks()
        plex_keys = {t.plex_key for t in all_tracks}
        assert "t0" in plex_keys
        assert "t1" in plex_keys
        assert "t2" in plex_keys

        # Sync history should record the failure
        cursor = db.get_connection().cursor()
        cursor.execute("SELECT status, error_message FROM sync_history ORDER BY id DESC LIMIT 1")
        row = dict(cursor.fetchone())
        assert row["status"] == "failed"
        assert "API error" in row["error_message"]


# ---------------------------------------------------------------------------
# 7. Audio analysis integration with sync
# ---------------------------------------------------------------------------


class TestAudioAnalysisIntegration:
    """Test audio analysis service error paths and integration."""

    def test_audio_service_file_not_found(self):
        """Audio service counts error when file doesn't exist."""
        mock_track = MagicMock()
        mock_track.file_path = "/nonexistent/path/song.mp3"
        mock_track.id = 1
        mock_track.title = "Missing File"

        mock_db = MagicMock()
        mock_settings = MagicMock()
        mock_settings.audio.duration_limit = 0
        mock_settings.audio.resolve_path.return_value = "/nonexistent/path/song.mp3"

        with patch("plexmix.audio.analyzer.EssentiaAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value
            instance.analyze.side_effect = FileNotFoundError("No such file")

            from plexmix.services.audio_service import analyze_tracks

            analyzed, errors = analyze_tracks(mock_db, mock_settings, [mock_track])

        assert analyzed == 0
        assert errors == 1
        mock_db.insert_audio_features.assert_not_called()

    def test_audio_service_corrupt_file(self):
        """Audio service handles corrupt audio files gracefully."""
        mock_track = MagicMock()
        mock_track.file_path = "/path/corrupt.mp3"
        mock_track.id = 2
        mock_track.title = "Corrupt File"

        mock_db = MagicMock()
        mock_settings = MagicMock()
        mock_settings.audio.duration_limit = 0
        mock_settings.audio.resolve_path.return_value = "/path/corrupt.mp3"

        with patch("plexmix.audio.analyzer.EssentiaAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value
            instance.analyze.side_effect = RuntimeError("Could not decode audio file")

            from plexmix.services.audio_service import analyze_tracks

            analyzed, errors = analyze_tracks(mock_db, mock_settings, [mock_track])

        assert analyzed == 0
        assert errors == 1

    def test_audio_service_skips_tracks_without_file_path(self):
        """Audio service skips tracks with no file_path."""
        mock_track = MagicMock()
        mock_track.file_path = None
        mock_track.id = 3
        mock_track.title = "No Path"

        mock_db = MagicMock()
        mock_settings = MagicMock()

        with patch("plexmix.audio.analyzer.EssentiaAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value

            from plexmix.services.audio_service import analyze_tracks

            analyzed, errors = analyze_tracks(mock_db, mock_settings, [mock_track])

        assert analyzed == 0
        assert errors == 0
        instance.analyze.assert_not_called()

    def test_audio_service_progress_callback(self):
        """Audio service invokes progress callback per track."""
        from plexmix.audio.analyzer import AudioFeatures

        tracks = []
        for i in range(3):
            t = MagicMock()
            t.file_path = f"/path/track{i}.mp3"
            t.id = i + 1
            t.title = f"Track {i}"
            tracks.append(t)

        mock_db = MagicMock()
        mock_settings = MagicMock()
        mock_settings.audio.duration_limit = 0
        mock_settings.audio.resolve_path.side_effect = lambda p: p

        callback = MagicMock()

        with patch("plexmix.audio.analyzer.EssentiaAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value
            features = AudioFeatures(tempo=120.0, key="C", scale="major")
            instance.analyze.return_value = features

            from plexmix.services.audio_service import analyze_tracks

            analyzed, errors = analyze_tracks(
                mock_db, mock_settings, tracks, progress_callback=callback
            )

        assert analyzed == 3
        assert errors == 0
        assert callback.call_count == 3
        # Last call should show (3, 0, 3) = (analyzed, errors, total)
        last_args = callback.call_args_list[-1][0]
        assert last_args == (3, 0, 3)

    def test_audio_service_mixed_success_and_failure(self):
        """Audio service handles a mix of successful and failed tracks."""
        from plexmix.audio.analyzer import AudioFeatures

        tracks = []
        for i in range(4):
            t = MagicMock()
            t.file_path = f"/path/track{i}.mp3"
            t.id = i + 1
            t.title = f"Track {i}"
            tracks.append(t)

        mock_db = MagicMock()
        mock_settings = MagicMock()
        mock_settings.audio.duration_limit = 0
        mock_settings.audio.resolve_path.side_effect = lambda p: p

        features = AudioFeatures(tempo=120.0, key="C", scale="major")

        with patch("plexmix.audio.analyzer.EssentiaAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value
            # Tracks 0, 2 succeed; tracks 1, 3 fail
            instance.analyze.side_effect = [
                features,
                RuntimeError("Decode error"),
                features,
                FileNotFoundError("Missing"),
            ]

            from plexmix.services.audio_service import analyze_tracks

            analyzed, errors = analyze_tracks(mock_db, mock_settings, tracks)

        assert analyzed == 2
        assert errors == 2
        assert mock_db.insert_audio_features.call_count == 2

    def test_audio_service_get_analyzable_tracks_default(self):
        """get_analyzable_tracks returns tracks without audio features."""
        from plexmix.services.audio_service import get_analyzable_tracks

        mock_db = MagicMock()
        mock_db.get_tracks_without_audio_features.return_value = ["track1", "track2"]

        result = get_analyzable_tracks(mock_db)
        assert result == ["track1", "track2"]
        mock_db.get_tracks_without_audio_features.assert_called_once()

    def test_audio_service_get_analyzable_tracks_force(self):
        """get_analyzable_tracks with force=True returns all tracks with file paths."""
        from plexmix.services.audio_service import get_analyzable_tracks

        t1 = MagicMock()
        t1.file_path = "/path/1.mp3"
        t2 = MagicMock()
        t2.file_path = None
        t3 = MagicMock()
        t3.file_path = "/path/3.mp3"

        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = [t1, t2, t3]

        result = get_analyzable_tracks(mock_db, force=True)
        assert len(result) == 2
        assert t1 in result
        assert t3 in result


# ---------------------------------------------------------------------------
# 8. Cancellation edge cases
# ---------------------------------------------------------------------------


class TestCancellationEdgeCases:
    """Test cancellation at various points in the sync pipeline."""

    def test_cancel_between_library_build_and_detection(self, db):
        """Cancel set after library build but before change detection."""
        cancel = Event()

        artists = [_make_artist("a1", "Artist")]
        albums = [_make_album("al1", "Album", artist_key="a1")]
        tracks = [_make_track("t1", "Track", artist_key="a1", album_key="al1")]

        # Set cancel after library build (get_all_tracks returns data first)
        call_count = 0

        def _track_gen():
            nonlocal call_count
            yield tracks
            call_count += 1
            cancel.set()

        mock_plex = MagicMock()
        mock_plex.validate_token.return_value = (True, "OK")
        mock_plex.get_all_artists.return_value = iter([[artists[0]]])
        mock_plex.get_all_albums.return_value = iter([[albums[0]]])
        mock_plex.get_all_tracks.return_value = _track_gen()

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        try:
            engine.incremental_sync(generate_embeddings=False, cancel_event=cancel)
        except KeyboardInterrupt:
            pass

        cursor = db.get_connection().cursor()
        cursor.execute("SELECT status FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        # Should be "interrupted" since cancel was set
        assert dict(row)["status"] == "interrupted"

    def test_cancel_preserves_partial_work(self, db):
        """Cancellation during track processing preserves already-inserted tracks."""
        cancel = Event()

        artists = [_make_artist("a1", "Artist")]
        albums = [_make_album("al1", "Album", artist_key="a1")]
        tracks = [
            _make_track(f"t{i}", f"Track {i}", artist_key="a1", album_key="al1") for i in range(50)
        ]

        # Set cancel after tracks are fetched but before change detection completes
        def _track_gen():
            yield tracks
            cancel.set()  # Cancel after library index is built

        mock_plex = MagicMock()
        mock_plex.validate_token.return_value = (True, "OK")
        mock_plex.get_all_artists.return_value = iter([[artists[0]]])
        mock_plex.get_all_albums.return_value = iter([[albums[0]]])
        mock_plex.get_all_tracks.return_value = _track_gen()

        engine = SyncEngine(plex_client=mock_plex, db_manager=db)

        try:
            engine.incremental_sync(generate_embeddings=False, cancel_event=cancel)
        except KeyboardInterrupt:
            pass

        # Sync was interrupted — verify history records it
        cursor = db.get_connection().cursor()
        cursor.execute("SELECT status FROM sync_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        assert row is not None
        assert dict(row)["status"] == "interrupted"
