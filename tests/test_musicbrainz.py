"""Tests for MusicBrainz integration: client, service, and database methods."""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.models import Track, Artist


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def db_manager():
    """Create a temporary database with schema for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    manager = SQLiteManager(tmp_path)
    manager.db_path = Path(tmp_path)
    manager.conn = sqlite3.connect(tmp_path)
    manager.conn.row_factory = sqlite3.Row
    manager.create_tables()

    yield manager

    try:
        manager.close()
    except Exception:
        pass
    Path(tmp_path).unlink(missing_ok=True)


@pytest.fixture
def sample_tracks(db_manager):
    """Insert sample artist, album, and tracks for testing."""
    artist = Artist(plex_key="artist-1", name="Radiohead")
    artist_id = db_manager.insert_artist(artist)

    from plexmix.database.models import Album

    album = Album(plex_key="album-1", title="OK Computer", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    tracks = []
    for i, title in enumerate(["Paranoid Android", "Karma Police", "No Surprises"]):
        track = Track(
            plex_key=f"track-{i}",
            title=title,
            artist_id=artist_id,
            album_id=album_id,
            genre="alternative rock",
        )
        track_id = db_manager.insert_track(track)
        track.id = track_id
        tracks.append(track)

    return tracks, artist_id, album_id


# ── Database Migration & CRUD Tests ────────────────────────────────


class TestMusicBrainzDatabase:
    def test_migration_10_creates_columns(self, db_manager):
        """Migration 10 should add MB columns to tracks, artists, albums."""
        cursor = db_manager.get_connection().cursor()

        # Check tracks columns
        cursor.execute("PRAGMA table_info(tracks)")
        track_cols = {col[1] for col in cursor.fetchall()}
        assert "musicbrainz_recording_id" in track_cols
        assert "musicbrainz_genres" in track_cols
        assert "recording_type" in track_cols
        assert "musicbrainz_enriched_at" in track_cols

        # Check artists columns
        cursor.execute("PRAGMA table_info(artists)")
        artist_cols = {col[1] for col in cursor.fetchall()}
        assert "musicbrainz_id" in artist_cols

        # Check albums columns
        cursor.execute("PRAGMA table_info(albums)")
        album_cols = {col[1] for col in cursor.fetchall()}
        assert "musicbrainz_release_group_id" in album_cols

    def test_migration_10_creates_cache_table(self, db_manager):
        """Migration 10 should create musicbrainz_cache table."""
        cursor = db_manager.get_connection().cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='musicbrainz_cache'"
        )
        assert cursor.fetchone() is not None

    def test_get_tracks_without_musicbrainz(self, db_manager, sample_tracks):
        tracks, _, _ = sample_tracks
        result = db_manager.get_tracks_without_musicbrainz()
        assert len(result) == 3

    def test_update_track_musicbrainz(self, db_manager, sample_tracks):
        tracks, _, _ = sample_tracks
        db_manager.update_track_musicbrainz(
            tracks[0].id,
            recording_id="mb-recording-123",
            genres="alternative rock, art rock",
            recording_type=None,
        )
        remaining = db_manager.get_tracks_without_musicbrainz()
        assert len(remaining) == 2

    def test_update_artist_musicbrainz_id(self, db_manager, sample_tracks):
        _, artist_id, _ = sample_tracks
        db_manager.update_artist_musicbrainz_id(artist_id, "mb-artist-456")

        artist = db_manager.get_artist_by_id(artist_id)
        assert artist.musicbrainz_id == "mb-artist-456"

    def test_musicbrainz_cache_roundtrip(self, db_manager):
        db_manager.set_musicbrainz_cache(
            "paranoid android::radiohead",
            "recording",
            "mb-123",
            {"tags": ["alternative rock"], "recording_type": None},
            95.0,
        )

        result = db_manager.get_musicbrainz_cache("paranoid android::radiohead", "recording")
        assert result is not None
        assert result["mbid"] == "mb-123"
        assert result["confidence"] == 95.0
        assert result["response_json"]["tags"] == ["alternative rock"]

    def test_musicbrainz_cache_miss(self, db_manager):
        result = db_manager.get_musicbrainz_cache("nonexistent", "recording")
        assert result is None

    def test_get_musicbrainz_enrichment_count(self, db_manager, sample_tracks):
        tracks, _, _ = sample_tracks
        assert db_manager.get_musicbrainz_enrichment_count() == 0

        db_manager.update_track_musicbrainz(tracks[0].id, recording_id="mb-123")
        assert db_manager.get_musicbrainz_enrichment_count() == 1

    def test_clear_expired_musicbrainz_cache(self, db_manager):
        # Insert a cache entry that is already expired
        cursor = db_manager.get_connection().cursor()
        cursor.execute(
            """
            INSERT INTO musicbrainz_cache
                (lookup_key, entity_type, mbid, confidence, expires_at)
            VALUES (?, ?, ?, ?, datetime('now', '-1 day'))
        """,
            ("old-key", "recording", "mb-old", 50.0),
        )
        db_manager._commit()

        deleted = db_manager.clear_expired_musicbrainz_cache()
        assert deleted == 1

    def test_update_album_musicbrainz_id(self, db_manager, sample_tracks):
        _, _, album_id = sample_tracks
        db_manager.update_album_musicbrainz_id(album_id, "rg-789")

        cursor = db_manager.get_connection().cursor()
        cursor.execute(
            "SELECT musicbrainz_release_group_id FROM albums WHERE id = ?",
            (album_id,),
        )
        assert cursor.fetchone()[0] == "rg-789"

    def test_get_track_details_by_ids_includes_artist_mbid(self, db_manager, sample_tracks):
        tracks, artist_id, _ = sample_tracks
        db_manager.update_artist_musicbrainz_id(artist_id, "mb-artist-789")

        details = db_manager.get_track_details_by_ids([tracks[0].id])
        assert len(details) == 1
        assert details[0]["artist_mbid"] == "mb-artist-789"


# ── Client Tests ───────────────────────────────────────────────────


class TestMusicBrainzClient:
    def test_import_error_handling(self):
        """Client should raise ImportError when musicbrainzngs is missing."""
        with patch.dict("sys.modules", {"musicbrainzngs": None}):
            with pytest.raises(ImportError, match="musicbrainzngs not installed"):
                from plexmix.musicbrainz.client import MusicBrainzClient  # noqa: F401

                # Force reimport
                import importlib
                import plexmix.musicbrainz.client

                importlib.reload(plexmix.musicbrainz.client)
                plexmix.musicbrainz.client.MusicBrainzClient()

    @patch("plexmix.musicbrainz.client.MusicBrainzClient.__init__", return_value=None)
    def test_extract_tags(self, mock_init):
        from plexmix.musicbrainz.client import MusicBrainzClient

        tags = MusicBrainzClient._extract_tags(
            [
                {"name": "Rock", "count": "5"},
                {"name": "alternative rock", "count": "3"},
                {"name": "Rock"},  # duplicate, different case
            ]
        )
        assert "rock" in tags
        assert "alternative rock" in tags

    @patch("plexmix.musicbrainz.client.MusicBrainzClient.__init__", return_value=None)
    def test_detect_recording_type_live(self, mock_init):
        from plexmix.musicbrainz.client import MusicBrainzClient

        result = MusicBrainzClient._detect_recording_type(
            {"disambiguation": "live at Glastonbury", "title": "Test"}
        )
        assert result == "live"

    @patch("plexmix.musicbrainz.client.MusicBrainzClient.__init__", return_value=None)
    def test_detect_recording_type_remix(self, mock_init):
        from plexmix.musicbrainz.client import MusicBrainzClient

        result = MusicBrainzClient._detect_recording_type(
            {"disambiguation": "", "title": "Song (Remix)"}
        )
        assert result == "remix"

    @patch("plexmix.musicbrainz.client.MusicBrainzClient.__init__", return_value=None)
    def test_detect_recording_type_none(self, mock_init):
        from plexmix.musicbrainz.client import MusicBrainzClient

        result = MusicBrainzClient._detect_recording_type(
            {"disambiguation": "", "title": "Normal Song"}
        )
        assert result is None

    @patch("plexmix.musicbrainz.client.MusicBrainzClient.__init__", return_value=None)
    def test_genre_normalization(self, mock_init):
        from plexmix.musicbrainz.client import MusicBrainzClient

        tags = MusicBrainzClient._extract_tags(
            [{"name": "  Electronic "}, {"name": "HIP-HOP"}, {"name": ""}]
        )
        assert "electronic" in tags
        assert "hip-hop" in tags
        assert "" not in tags


# ── Service Tests ──────────────────────────────────────────────────


class TestMusicBrainzService:
    def test_get_enrichable_tracks_no_force(self, db_manager, sample_tracks):
        tracks, _, _ = sample_tracks
        from plexmix.services.musicbrainz_service import get_enrichable_tracks

        result = get_enrichable_tracks(db_manager, force=False)
        assert len(result) == 3

    def test_get_enrichable_tracks_force(self, db_manager, sample_tracks):
        tracks, _, _ = sample_tracks
        # Enrich one track
        db_manager.update_track_musicbrainz(tracks[0].id, recording_id="mb-123")

        from plexmix.services.musicbrainz_service import get_enrichable_tracks

        # Without force, skip enriched
        result = get_enrichable_tracks(db_manager, force=False)
        assert len(result) == 2

        # With force, return all
        result = get_enrichable_tracks(db_manager, force=True)
        assert len(result) == 3

    def test_enrich_tracks_uses_cache(self, db_manager, sample_tracks):
        tracks, artist_id, _ = sample_tracks

        # Pre-populate cache for the first track
        cache_key = "paranoid android::radiohead"
        db_manager.set_musicbrainz_cache(
            cache_key,
            "recording",
            "mb-cached-123",
            {"tags": ["rock"], "recording_type": None, "mbid": "mb-cached-123"},
            95.0,
        )

        from plexmix.config.settings import MusicBrainzSettings
        from plexmix.services.musicbrainz_service import enrich_tracks

        settings = MusicBrainzSettings(
            confidence_threshold=80.0,
            rate_limit_delay=0.0,
        )

        # Mock the client to avoid real API calls
        with patch("plexmix.musicbrainz.client.MusicBrainzClient") as MockClient:
            mock_client = MagicMock()
            mock_client.search_recording.return_value = None  # No match for non-cached
            mock_client.search_artist.return_value = None
            MockClient.return_value = mock_client

            # Only enrich the first track (which has cache)
            enriched, cached, errors = enrich_tracks(db_manager, settings, [tracks[0]])

            assert cached == 1
            assert enriched == 0

    def test_enrich_tracks_cancellation(self, db_manager, sample_tracks):
        tracks, _, _ = sample_tracks
        from threading import Event

        from plexmix.config.settings import MusicBrainzSettings
        from plexmix.services.musicbrainz_service import enrich_tracks

        settings = MusicBrainzSettings(rate_limit_delay=0.0)
        cancel = Event()
        cancel.set()  # Cancel immediately

        with patch("plexmix.musicbrainz.client.MusicBrainzClient") as MockClient:
            MockClient.return_value = MagicMock()
            enriched, cached, errors = enrich_tracks(
                db_manager, settings, tracks, cancel_event=cancel
            )

        # Should stop immediately
        assert enriched + cached + errors == 0

    def test_transient_error_not_cached(self, db_manager, sample_tracks):
        """Transient API errors should NOT create negative cache entries."""
        tracks, _, _ = sample_tracks
        from plexmix.musicbrainz.client import MusicBrainzAPIError
        from plexmix.config.settings import MusicBrainzSettings
        from plexmix.services.musicbrainz_service import enrich_tracks

        settings = MusicBrainzSettings(rate_limit_delay=0.0)

        with patch("plexmix.musicbrainz.client.MusicBrainzClient") as MockClient:
            mock_client = MagicMock()
            mock_client.search_recording.side_effect = MusicBrainzAPIError(
                "503 Service Unavailable"
            )
            mock_client.search_artist.side_effect = MusicBrainzAPIError("503 Service Unavailable")
            MockClient.return_value = mock_client

            enriched, cached, errors = enrich_tracks(db_manager, settings, [tracks[0]])

        assert errors == 1
        assert enriched == 0
        assert cached == 0

        # Cache should be empty — transient error was NOT cached
        cache_key = f"{tracks[0].title}::radiohead".lower()
        assert db_manager.get_musicbrainz_cache(cache_key, "recording") is None

    def test_album_release_group_persisted(self, db_manager, sample_tracks):
        """When recording details include release_group_id, album should be updated."""
        tracks, artist_id, album_id = sample_tracks
        from plexmix.config.settings import MusicBrainzSettings
        from plexmix.services.musicbrainz_service import enrich_tracks

        settings = MusicBrainzSettings(rate_limit_delay=0.0)

        with patch("plexmix.musicbrainz.client.MusicBrainzClient") as MockClient:
            mock_client = MagicMock()
            mock_client.search_recording.return_value = {
                "mbid": "rec-123",
                "title": "Paranoid Android",
                "score": 95,
                "artist_credit": [],
                "release_list": [],
            }
            mock_client.get_recording_details.return_value = {
                "mbid": "rec-123",
                "title": "Paranoid Android",
                "tags": ["rock"],
                "recording_type": None,
                "release_group_id": "rg-456",
            }
            mock_client.search_artist.return_value = None
            MockClient.return_value = mock_client

            enriched, cached, errors = enrich_tracks(db_manager, settings, [tracks[0]])

        assert enriched == 1

        # Verify album was updated
        cursor = db_manager.get_connection().cursor()
        cursor.execute(
            "SELECT musicbrainz_release_group_id FROM albums WHERE id = ?",
            (album_id,),
        )
        row = cursor.fetchone()
        assert row[0] == "rg-456"

    def test_progress_callback_includes_errors(self, db_manager, sample_tracks):
        """Progress callback should receive (enriched, cached, errors, total)."""
        tracks, _, _ = sample_tracks
        from plexmix.musicbrainz.client import MusicBrainzAPIError
        from plexmix.config.settings import MusicBrainzSettings
        from plexmix.services.musicbrainz_service import enrich_tracks

        settings = MusicBrainzSettings(rate_limit_delay=0.0)
        progress_calls = []

        def on_progress(enriched, cached, errors, total):
            progress_calls.append((enriched, cached, errors, total))

        with patch("plexmix.musicbrainz.client.MusicBrainzClient") as MockClient:
            mock_client = MagicMock()
            mock_client.search_recording.side_effect = MusicBrainzAPIError("timeout")
            mock_client.search_artist.side_effect = MusicBrainzAPIError("timeout")
            MockClient.return_value = mock_client

            enriched, cached, errors = enrich_tracks(
                db_manager,
                settings,
                [tracks[0]],
                progress_callback=on_progress,
            )

        assert errors == 1
        assert len(progress_calls) > 0
        # Last progress call should show error counted
        last = progress_calls[-1]
        assert last[2] == 1  # errors
        assert last[3] == 1  # total


# ── Embedding Text Enrichment Tests ────────────────────────────────


class TestEmbeddingTextEnrichment:
    def test_create_track_text_with_mb_genres(self):
        from plexmix.utils.embeddings import create_track_text

        track_data = {
            "title": "Paranoid Android",
            "artist": "Radiohead",
            "album": "OK Computer",
            "genre": "alternative",
            "year": "1997",
            "tags": "",
            "environments": "",
            "instruments": "",
            "musicbrainz_genres": "art rock, alternative rock",
            "recording_type": "",
        }
        text = create_track_text(track_data)
        assert "mb-genres: art rock, alternative rock" in text

    def test_create_track_text_with_recording_type(self):
        from plexmix.utils.embeddings import create_track_text

        track_data = {
            "title": "Creep",
            "artist": "Radiohead",
            "album": "Pablo Honey",
            "genre": "",
            "year": "",
            "tags": "",
            "environments": "",
            "instruments": "",
            "musicbrainz_genres": "",
            "recording_type": "live",
        }
        text = create_track_text(track_data)
        assert "type: live" in text

    def test_create_track_text_without_mb_data(self):
        from plexmix.utils.embeddings import create_track_text

        track_data = {
            "title": "Test",
            "artist": "Artist",
            "album": "Album",
            "genre": "",
            "year": "",
            "tags": "",
            "environments": "",
            "instruments": "",
            "musicbrainz_genres": "",
            "recording_type": "",
        }
        text = create_track_text(track_data)
        assert "mb-genres" not in text
        assert "type:" not in text


# ── Settings Tests ─────────────────────────────────────────────────


class TestMusicBrainzSettings:
    def test_default_settings(self):
        from plexmix.config.settings import MusicBrainzSettings

        settings = MusicBrainzSettings()
        assert settings.enabled is False
        assert settings.enrich_on_sync is False
        assert settings.confidence_threshold == 80.0
        assert settings.rate_limit_delay == 1.0
        assert settings.contact_email == ""

    def test_settings_in_main_config(self):
        from plexmix.config.settings import Settings

        settings = Settings()
        assert hasattr(settings, "musicbrainz")
        assert settings.musicbrainz.enabled is False
