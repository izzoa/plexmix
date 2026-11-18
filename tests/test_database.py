import pytest
import re
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from plexmix.database import backup as backup_module
from plexmix.database.backup import BackupError, backup_library
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.models import Artist, Album, Track, Embedding, SyncHistory, Playlist


@pytest.fixture
def db_manager():
    import sqlite3

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    manager = SQLiteManager(db_path)
    manager.db_path.parent.mkdir(parents=True, exist_ok=True)
    manager.conn = sqlite3.connect(str(manager.db_path))
    manager.conn.row_factory = sqlite3.Row
    manager.create_tables()

    yield manager

    manager.close()
    Path(db_path).unlink(missing_ok=True)


def test_connect_upgrades_legacy_database():
    """connect() upgrades a legacy DB (old column names, no audio_features table)
    without crashing. Regression for the migrate-after-index ordering bug: indexes
    referencing audio_features / the environments+instruments columns were created
    before the migrations that add them, so connect() crashed on any old database."""
    import sqlite3

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE artists (id INTEGER PRIMARY KEY, plex_key TEXT, name TEXT,
                                  genre TEXT, bio TEXT);
            CREATE TABLE albums (id INTEGER PRIMARY KEY, plex_key TEXT, title TEXT,
                                 artist_id INTEGER, year INTEGER, genre TEXT, cover_art_url TEXT);
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY, plex_key TEXT, title TEXT, artist_id INTEGER,
                album_id INTEGER, duration_ms INTEGER, genre TEXT, year INTEGER, rating REAL,
                play_count INTEGER, last_played TIMESTAMP, file_path TEXT, tags TEXT,
                environment TEXT, primary_instrument TEXT, tags_generated_at TIMESTAMP,
                musicbrainz_recording_id TEXT, musicbrainz_genres TEXT, recording_type TEXT,
                musicbrainz_enriched_at TIMESTAMP
            );
            CREATE TABLE genres (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE track_genres (track_id INTEGER, genre_id INTEGER);
            CREATE TABLE embeddings (id INTEGER PRIMARY KEY, track_id INTEGER, embedding_model TEXT,
                                     embedding_dim INTEGER, vector BLOB,
                                     created_at TIMESTAMP, updated_at TIMESTAMP);
            CREATE TABLE sync_history (id INTEGER PRIMARY KEY, sync_date TIMESTAMP,
                                       tracks_added INTEGER, tracks_updated INTEGER,
                                       tracks_removed INTEGER, status TEXT, error_message TEXT);
            CREATE TABLE playlists (id INTEGER PRIMARY KEY, plex_key TEXT, name TEXT);
            CREATE TABLE playlist_tracks (playlist_id INTEGER, track_id INTEGER, position INTEGER);
            INSERT INTO artists (plex_key, name) VALUES ('a1', 'Artist');
            INSERT INTO albums (plex_key, title, artist_id, year) VALUES ('al1', 'Album', 1, 1990);
            INSERT INTO tracks (plex_key, title, artist_id, album_id, environment)
                VALUES ('t1', 'Song', 1, 1, 'club');
            """
        )
        conn.commit()
        conn.close()

        manager = SQLiteManager(db_path)
        manager.connect()  # must not raise despite the legacy schema

        cursor = manager.get_connection().cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audio_features'"
        )
        assert cursor.fetchone() is not None  # migration 5 created it
        cursor.execute("PRAGMA table_info(tracks)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "environments" in cols  # migration 1 renamed environment
        assert "instruments" in cols  # migration 2 renamed primary_instrument
        manager.close()
    finally:
        Path(db_path).unlink(missing_ok=True)


def _seed_track_with_embeddings(db_manager, models_and_dims):
    """Insert one artist/album/track plus one embedding per (model, dim) pair."""
    artist_id = db_manager.insert_artist(Artist(plex_key="a1", name="Artist"))
    album_id = db_manager.insert_album(Album(plex_key="al1", title="Album", artist_id=artist_id))
    track_id = db_manager.insert_track(
        Track(plex_key="t1", title="Song", artist_id=artist_id, album_id=album_id)
    )
    for model, dim in models_and_dims:
        db_manager.insert_embedding(
            Embedding(
                track_id=track_id,
                embedding_model=model,
                embedding_dim=dim,
                vector=[0.1] * dim,
            )
        )
    return track_id


def test_embedding_reads_are_model_scoped(db_manager):
    """get_all_embeddings / get_track_ids_with_embeddings filter by model so a
    provider switch never mixes dimensions or skips re-embedding."""
    _seed_track_with_embeddings(db_manager, [("Gemini", 4), ("OpenAI", 2)])

    # Unscoped returns both rows (one per model).
    assert len(db_manager.get_all_embeddings()) == 2

    gemini = db_manager.get_all_embeddings("Gemini")
    assert len(gemini) == 1 and len(gemini[0][1]) == 4
    openai = db_manager.get_all_embeddings("OpenAI")
    assert len(openai) == 1 and len(openai[0][1]) == 2

    # A track embedded only by Gemini is "missing" for OpenAI, so it re-embeds.
    assert db_manager.get_track_ids_with_embeddings("OpenAI")  # OpenAI row exists here
    assert db_manager.get_track_ids_with_embeddings("Cohere") == set()


def test_get_tracks_no_duplicate_rows_with_multi_model_embeddings(db_manager):
    """get_tracks must not duplicate a track that has embeddings from more than
    one model (previously a LEFT JOIN produced one row per embedding)."""
    _seed_track_with_embeddings(db_manager, [("Gemini", 4), ("OpenAI", 2)])

    rows = db_manager.get_tracks(limit=50, offset=0)
    matching = [r for r in rows if r["plex_key"] == "t1"]
    assert len(matching) == 1
    assert matching[0]["has_embedding"] == 1


def test_create_tables(db_manager):
    cursor = db_manager.get_connection().cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}

    assert "artists" in tables
    assert "albums" in tables
    assert "tracks" in tables
    assert "embeddings" in tables
    assert "playlists" in tables
    assert "sync_history" in tables


def test_insert_and_get_artist(db_manager):
    artist = Artist(
        plex_key="/library/metadata/12345",
        name="Miles Davis",
        genre="Jazz",
        bio="Legendary jazz musician",
    )

    artist_id = db_manager.insert_artist(artist)
    assert artist_id > 0

    retrieved = db_manager.get_artist_by_id(artist_id)
    assert retrieved is not None
    assert retrieved.name == "Miles Davis"
    assert retrieved.genre == "Jazz"

    retrieved_by_key = db_manager.get_artist_by_plex_key("/library/metadata/12345")
    assert retrieved_by_key is not None
    assert retrieved_by_key.id == artist_id


def test_insert_and_get_album(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="John Coltrane")
    artist_id = db_manager.insert_artist(artist)

    album = Album(
        plex_key="/library/metadata/2",
        title="A Love Supreme",
        artist_id=artist_id,
        year=1965,
        genre="Jazz",
    )

    album_id = db_manager.insert_album(album)
    assert album_id > 0

    retrieved = db_manager.get_album_by_id(album_id)
    assert retrieved is not None
    assert retrieved.title == "A Love Supreme"
    assert retrieved.year == 1965


def test_insert_and_get_track(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="Bill Evans")
    artist_id = db_manager.insert_artist(artist)

    album = Album(
        plex_key="/library/metadata/2", title="Sunday at the Village Vanguard", artist_id=artist_id
    )
    album_id = db_manager.insert_album(album)

    track = Track(
        plex_key="/library/metadata/3",
        title="Gloria's Step",
        artist_id=artist_id,
        album_id=album_id,
        duration_ms=360000,
        genre="Jazz",
        year=1961,
        rating=4.5,
        tags="contemplative, melodic, sophisticated",
        environments="study, relax, focus",
        instruments="piano, bass, drums",
    )

    track_id = db_manager.insert_track(track)
    assert track_id > 0

    retrieved = db_manager.get_track_by_id(track_id)
    assert retrieved is not None
    assert retrieved.title == "Gloria's Step"
    assert retrieved.year == 1961
    assert retrieved.tags == "contemplative, melodic, sophisticated"
    assert retrieved.environments == "study, relax, focus"
    assert retrieved.instruments == "piano, bass, drums"


def test_insert_and_get_embedding(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track = Track(
        plex_key="/library/metadata/3", title="Track", artist_id=artist_id, album_id=album_id
    )
    track_id = db_manager.insert_track(track)

    embedding = Embedding(
        track_id=track_id,
        embedding_model="gemini-embedding-001",
        embedding_dim=3072,
        vector=[0.1] * 3072,
    )

    embedding_id = db_manager.insert_embedding(embedding)
    assert embedding_id > 0

    retrieved = db_manager.get_embedding_by_track_id(track_id)
    assert retrieved is not None
    assert retrieved.embedding_model == "gemini-embedding-001"
    assert retrieved.embedding_dim == 3072
    assert len(retrieved.vector) == 3072


def test_get_all_embeddings(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track1 = Track(
        plex_key="/library/metadata/3", title="Track 1", artist_id=artist_id, album_id=album_id
    )
    track1_id = db_manager.insert_track(track1)

    track2 = Track(
        plex_key="/library/metadata/4", title="Track 2", artist_id=artist_id, album_id=album_id
    )
    track2_id = db_manager.insert_track(track2)

    embedding1 = Embedding(
        track_id=track1_id, embedding_model="test", embedding_dim=384, vector=[0.1] * 384
    )
    embedding2 = Embedding(
        track_id=track2_id, embedding_model="test", embedding_dim=384, vector=[0.2] * 384
    )

    db_manager.insert_embedding(embedding1)
    db_manager.insert_embedding(embedding2)

    all_embeddings = db_manager.get_all_embeddings()
    assert len(all_embeddings) == 2
    assert all_embeddings[0][0] in [track1_id, track2_id]
    assert len(all_embeddings[0][1]) == 384


def test_insert_sync_record(db_manager):
    sync = SyncHistory(tracks_added=100, tracks_updated=50, tracks_removed=10, status="success")

    sync_id = db_manager.insert_sync_record(sync)
    assert sync_id > 0

    latest = db_manager.get_latest_sync()
    assert latest is not None
    assert latest.tracks_added == 100
    assert latest.status == "success"


def test_insert_playlist(db_manager):
    playlist = Playlist(
        plex_key="/library/metadata/playlist/1",
        name="Chill Vibes",
        description="Relaxing music",
        created_by_ai=True,
        mood_query="relaxing evening vibes",
    )

    playlist_id = db_manager.insert_playlist(playlist)
    assert playlist_id > 0


def test_add_track_to_playlist(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track = Track(
        plex_key="/library/metadata/3", title="Track", artist_id=artist_id, album_id=album_id
    )
    track_id = db_manager.insert_track(track)

    playlist = Playlist(name="Test Playlist", created_by_ai=False)
    playlist_id = db_manager.insert_playlist(playlist)

    db_manager.add_track_to_playlist(playlist_id, track_id, 0)

    cursor = db_manager.get_connection().cursor()
    cursor.execute(
        "SELECT * FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?",
        (playlist_id, track_id),
    )
    result = cursor.fetchone()

    assert result is not None
    assert result["position"] == 0


def test_delete_track(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track = Track(
        plex_key="/library/metadata/3", title="Track", artist_id=artist_id, album_id=album_id
    )
    track_id = db_manager.insert_track(track)

    db_manager.delete_track(track_id)

    retrieved = db_manager.get_track_by_id(track_id)
    assert retrieved is None


def test_migration_adds_environments_column(db_manager):
    cursor = db_manager.get_connection().cursor()
    cursor.execute("PRAGMA table_info(tracks)")
    columns = {col[1] for col in cursor.fetchall()}

    assert "environments" in columns
    assert "instruments" in columns


# ============================================================================
# Phase G: Regression Tests for Data Integrity
# ============================================================================


def test_upsert_keeps_stable_id(db_manager):
    """Test that upserting a track with the same plex_key keeps the same ID."""
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    # Insert initial track
    track = Track(
        plex_key="/library/metadata/3",
        title="Original Title",
        artist_id=artist_id,
        album_id=album_id,
        duration_ms=180000,
    )
    first_id = db_manager.insert_track(track)

    # Upsert with same plex_key but different title
    track_updated = Track(
        plex_key="/library/metadata/3",  # Same plex_key
        title="Updated Title",
        artist_id=artist_id,
        album_id=album_id,
        duration_ms=190000,
    )
    second_id = db_manager.insert_track(track_updated)

    # ID should remain stable
    assert first_id == second_id, "UPSERT changed the row ID (data integrity issue!)"

    # Verify the update was applied
    retrieved = db_manager.get_track_by_id(first_id)
    assert retrieved.title == "Updated Title"
    assert retrieved.duration_ms == 190000


def test_foreign_keys_enforced(db_manager):
    """Test that foreign key constraints are enforced."""
    # Enable foreign keys explicitly for this test
    cursor = db_manager.get_connection().cursor()
    cursor.execute("PRAGMA foreign_keys=ON")

    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    # Try to insert track with non-existent album_id
    track = Track(
        plex_key="/library/metadata/3",
        title="Orphan Track",
        artist_id=artist_id,
        album_id=99999,  # Non-existent album
        duration_ms=180000,
    )

    # This should raise an error due to foreign key constraint
    with pytest.raises(Exception):
        db_manager.insert_track(track)


def test_get_last_sync_time_returns_success_status(db_manager):
    """Test that get_last_sync_time correctly queries for 'success' status."""
    # Insert a sync record with 'success' status
    sync_success = SyncHistory(
        tracks_added=100, tracks_updated=50, tracks_removed=10, status="success"
    )
    db_manager.insert_sync_record(sync_success)

    # Insert a failed sync record
    sync_failed = SyncHistory(tracks_added=0, tracks_updated=0, tracks_removed=0, status="failed")
    db_manager.insert_sync_record(sync_failed)

    # get_last_sync_time should return the success record's date
    last_sync = db_manager.get_last_sync_time()
    assert last_sync is not None, "get_last_sync_time returned None (sync status mismatch bug!)"


def test_track_upsert_preserves_existing_tags(db_manager):
    """Test that upserting a track without tags preserves existing tags."""
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    # Insert track with tags
    track_with_tags = Track(
        plex_key="/library/metadata/3",
        title="Tagged Track",
        artist_id=artist_id,
        album_id=album_id,
        duration_ms=180000,
        tags="energetic,upbeat",
        environments="workout,party",
        instruments="drums,guitar",
    )
    track_id = db_manager.insert_track(track_with_tags)

    # Upsert without tags (simulating sync that doesn't include tags)
    track_without_tags = Track(
        plex_key="/library/metadata/3",  # Same plex_key
        title="Tagged Track Updated",
        artist_id=artist_id,
        album_id=album_id,
        duration_ms=185000,
        tags=None,  # No tags provided
        environments=None,
        instruments=None,
    )
    db_manager.insert_track(track_without_tags)

    # Existing tags should be preserved
    retrieved = db_manager.get_track_by_id(track_id)
    assert retrieved.tags == "energetic,upbeat", "Tags were wiped on upsert!"
    assert retrieved.environments == "workout,party", "Environments were wiped on upsert!"
    assert retrieved.instruments == "drums,guitar", "Instruments were wiped on upsert!"
    # Title should still be updated
    assert retrieved.title == "Tagged Track Updated"


def test_bulk_fetch_tracks_by_ids(db_manager):
    """Test bulk fetching tracks by IDs."""
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    # Insert multiple tracks
    track_ids = []
    for i in range(5):
        track = Track(
            plex_key=f"/library/metadata/{100 + i}",
            title=f"Track {i}",
            artist_id=artist_id,
            album_id=album_id,
        )
        track_ids.append(db_manager.insert_track(track))

    # Bulk fetch
    tracks = db_manager.get_tracks_by_ids(track_ids)

    assert len(tracks) == 5
    for tid in track_ids:
        assert tid in tracks


def test_bulk_fetch_track_details_by_ids(db_manager):
    """Test bulk fetching track details with artist/album info."""
    artist = Artist(plex_key="/library/metadata/1", name="Test Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Test Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track = Track(
        plex_key="/library/metadata/3", title="Test Track", artist_id=artist_id, album_id=album_id
    )
    track_id = db_manager.insert_track(track)

    details = db_manager.get_track_details_by_ids([track_id])

    assert len(details) == 1
    assert details[0]["title"] == "Test Track"
    assert details[0]["artist_name"] == "Test Artist"
    assert details[0]["album_title"] == "Test Album"
    assert "file_path" in details[0]


def test_bulk_fetch_track_details_includes_file_path(db_manager):
    """Test that file_path is included and correct in bulk track details."""
    artist = Artist(plex_key="/library/metadata/10", name="Path Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/11", title="Path Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track = Track(
        plex_key="/library/metadata/12",
        title="Path Track",
        artist_id=artist_id,
        album_id=album_id,
        file_path="/music/artist/album/track.flac",
    )
    track_id = db_manager.insert_track(track)

    details = db_manager.get_track_details_by_ids([track_id])

    assert len(details) == 1
    assert details[0]["file_path"] == "/music/artist/album/track.flac"


def _create_test_track(db_manager, title="Track", plex_key="t1", file_path=None):
    """Helper to create a track with an artist and album."""
    artist = Artist(plex_key="a1", name="Artist")
    artist_id = db_manager.insert_artist(artist)
    album = Album(plex_key="al1", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)
    track = Track(
        plex_key=plex_key,
        title=title,
        artist_id=artist_id,
        album_id=album_id,
        file_path=file_path,
    )
    return db_manager.insert_track(track)


def test_insert_and_get_audio_features(db_manager):
    track_id = _create_test_track(db_manager, file_path="/music/track.mp3")

    features = {
        "tempo": 120.5,
        "tempo_confidence": 0.9,
        "key": "C",
        "scale": "major",
        "key_confidence": 0.85,
        "loudness": -10.0,
        "energy": 0.6,
        "energy_level": "medium",
        "danceability": 0.75,
        "spectral_centroid": 2000.0,
        "mfcc": [1.0, 2.0, 3.0],
        "zero_crossing_rate": 0.1,
    }
    db_manager.insert_audio_features(track_id, features)

    result = db_manager.get_audio_features(track_id)
    assert result is not None
    assert result["tempo"] == 120.5
    assert result["key"] == "C"
    assert result["scale"] == "major"
    assert result["danceability"] == 0.75
    assert result["mfcc"] == [1.0, 2.0, 3.0]


def test_upsert_audio_features(db_manager):
    track_id = _create_test_track(db_manager, file_path="/music/track.mp3")

    db_manager.insert_audio_features(track_id, {"tempo": 100.0})
    db_manager.insert_audio_features(track_id, {"tempo": 120.0})

    result = db_manager.get_audio_features(track_id)
    assert result["tempo"] == 120.0


def test_get_tracks_without_audio_features(db_manager):
    t1 = _create_test_track(db_manager, plex_key="t1", file_path="/music/a.mp3")
    _create_test_track(db_manager, plex_key="t2", file_path="/music/b.mp3")
    _create_test_track(db_manager, plex_key="t3")  # no file_path

    db_manager.insert_audio_features(t1, {"tempo": 120.0})

    pending = db_manager.get_tracks_without_audio_features()
    # Only t2 should be pending (t1 has features, t3 has no file_path)
    assert len(pending) == 1
    assert pending[0].plex_key == "t2"


def test_get_audio_features_by_track_ids(db_manager):
    t1 = _create_test_track(db_manager, plex_key="t1", file_path="/a.mp3")
    t2 = _create_test_track(db_manager, plex_key="t2", file_path="/b.mp3")

    db_manager.insert_audio_features(t1, {"tempo": 100.0})
    db_manager.insert_audio_features(t2, {"tempo": 140.0})

    result = db_manager.get_audio_features_by_track_ids([t1, t2])
    assert len(result) == 2
    assert result[t1]["tempo"] == 100.0
    assert result[t2]["tempo"] == 140.0


def test_audio_features_count(db_manager):
    t1 = _create_test_track(db_manager, plex_key="t1", file_path="/a.mp3")
    assert db_manager.get_audio_features_count() == 0

    db_manager.insert_audio_features(t1, {"tempo": 120.0})
    assert db_manager.get_audio_features_count() == 1


# ============================================================================
# Playlist CRUD operations
# ============================================================================


def _setup_track_with_metadata(db_manager, plex_key="t1", title="Track", genre=None, year=None):
    """Create a track with artist/album and return (track_id, artist_id, album_id)."""
    artist = Artist(plex_key=f"a-{plex_key}", name=f"Artist for {title}")
    artist_id = db_manager.insert_artist(artist)
    album = Album(plex_key=f"al-{plex_key}", title=f"Album for {title}", artist_id=artist_id)
    album_id = db_manager.insert_album(album)
    track = Track(
        plex_key=plex_key,
        title=title,
        artist_id=artist_id,
        album_id=album_id,
        genre=genre,
        year=year,
    )
    track_id = db_manager.insert_track(track)
    return track_id, artist_id, album_id


def test_get_playlists_empty(db_manager):
    assert db_manager.get_playlists() == []


def test_get_playlists_with_track_count(db_manager):
    t1, _, _ = _setup_track_with_metadata(db_manager, "t1", "Song 1")
    t2, _, _ = _setup_track_with_metadata(db_manager, "t2", "Song 2")

    p1 = db_manager.insert_playlist(Playlist(name="Two Songs", created_by_ai=True))
    db_manager.add_track_to_playlist(p1, t1, 0)
    db_manager.add_track_to_playlist(p1, t2, 1)

    db_manager.insert_playlist(Playlist(name="Empty Playlist", created_by_ai=False))

    playlists = db_manager.get_playlists()
    assert len(playlists) == 2

    by_name = {p.name: p for p in playlists}
    assert by_name["Two Songs"].track_count == 2
    assert by_name["Empty Playlist"].track_count == 0


def test_get_playlist_by_id_found(db_manager):
    pid = db_manager.insert_playlist(
        Playlist(name="My Playlist", description="desc", created_by_ai=True, mood_query="chill")
    )
    result = db_manager.get_playlist_by_id(pid)
    assert result is not None
    assert result.name == "My Playlist"
    assert result.description == "desc"
    assert result.mood_query == "chill"


def test_get_playlist_by_id_not_found(db_manager):
    assert db_manager.get_playlist_by_id(99999) is None


def test_get_playlist_tracks_ordered(db_manager):
    t1, _, _ = _setup_track_with_metadata(db_manager, "t1", "First")
    t2, _, _ = _setup_track_with_metadata(db_manager, "t2", "Second")
    t3, _, _ = _setup_track_with_metadata(db_manager, "t3", "Third")

    pid = db_manager.insert_playlist(Playlist(name="Ordered", created_by_ai=False))
    db_manager.add_track_to_playlist(pid, t3, 0)
    db_manager.add_track_to_playlist(pid, t1, 1)
    db_manager.add_track_to_playlist(pid, t2, 2)

    tracks = db_manager.get_playlist_tracks(pid)
    assert len(tracks) == 3
    assert tracks[0]["title"] == "Third"
    assert tracks[1]["title"] == "First"
    assert tracks[2]["title"] == "Second"
    assert tracks[0]["position"] == 0
    assert "artist_name" in tracks[0]
    assert "album_title" in tracks[0]


def test_get_playlist_tracks_empty(db_manager):
    pid = db_manager.insert_playlist(Playlist(name="Empty", created_by_ai=False))
    assert db_manager.get_playlist_tracks(pid) == []


def test_add_tracks_to_playlist_bulk(db_manager):
    t1, _, _ = _setup_track_with_metadata(db_manager, "t1", "Song 1")
    t2, _, _ = _setup_track_with_metadata(db_manager, "t2", "Song 2")
    t3, _, _ = _setup_track_with_metadata(db_manager, "t3", "Song 3")

    pid = db_manager.insert_playlist(Playlist(name="Bulk", created_by_ai=False))
    db_manager.add_tracks_to_playlist(pid, [t1, t2, t3])

    tracks = db_manager.get_playlist_tracks(pid)
    assert len(tracks) == 3
    assert tracks[0]["position"] == 0
    assert tracks[2]["position"] == 2


def test_add_tracks_to_playlist_empty_list(db_manager):
    pid = db_manager.insert_playlist(Playlist(name="Nothing", created_by_ai=False))
    db_manager.add_tracks_to_playlist(pid, [])
    assert db_manager.get_playlist_tracks(pid) == []


def test_delete_playlist(db_manager):
    t1, _, _ = _setup_track_with_metadata(db_manager, "t1", "Song")
    pid = db_manager.insert_playlist(Playlist(name="ToDelete", created_by_ai=False))
    db_manager.add_track_to_playlist(pid, t1, 0)

    db_manager.delete_playlist(pid)
    assert db_manager.get_playlist_by_id(pid) is None
    assert db_manager.get_playlist_tracks(pid) == []


def test_update_playlist_name(db_manager):
    pid = db_manager.insert_playlist(Playlist(name="Old Name", created_by_ai=False))
    db_manager.update_playlist(pid, name="New Name")

    result = db_manager.get_playlist_by_id(pid)
    assert result.name == "New Name"


def test_update_playlist_description(db_manager):
    pid = db_manager.insert_playlist(
        Playlist(name="P", description="old desc", created_by_ai=False)
    )
    db_manager.update_playlist(pid, description="new desc")

    result = db_manager.get_playlist_by_id(pid)
    assert result.description == "new desc"
    assert result.name == "P"  # unchanged


def test_update_playlist_no_changes(db_manager):
    pid = db_manager.insert_playlist(Playlist(name="NoChange", created_by_ai=False))
    db_manager.update_playlist(pid)  # no args → no-op
    result = db_manager.get_playlist_by_id(pid)
    assert result.name == "NoChange"


# ============================================================================
# FTS and recently tagged
# ============================================================================


def test_search_tracks_fts(db_manager):
    _setup_track_with_metadata(db_manager, "t1", "Bohemian Rhapsody", genre="Rock")
    _setup_track_with_metadata(db_manager, "t2", "Stairway to Heaven", genre="Rock")
    _setup_track_with_metadata(db_manager, "t3", "Take Five", genre="Jazz")

    results = db_manager.search_tracks_fts("Bohemian")
    assert len(results) == 1
    assert results[0].title == "Bohemian Rhapsody"


def test_search_tracks_fts_no_results(db_manager):
    _setup_track_with_metadata(db_manager, "t1", "Hello World")
    results = db_manager.search_tracks_fts("nonexistent")
    assert results == []


def test_get_recently_tagged_tracks(db_manager):
    artist = Artist(plex_key="a1", name="Artist")
    artist_id = db_manager.insert_artist(artist)
    album = Album(plex_key="al1", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    # Track with tags
    tagged = Track(
        plex_key="t1",
        title="Tagged Song",
        artist_id=artist_id,
        album_id=album_id,
        tags="energetic,upbeat",
        environments="gym,party",
        instruments="drums,bass",
    )
    db_manager.insert_track(tagged)

    # Track without tags
    untagged = Track(
        plex_key="t2",
        title="Untagged Song",
        artist_id=artist_id,
        album_id=album_id,
    )
    db_manager.insert_track(untagged)

    results = db_manager.get_recently_tagged_tracks(limit=10)
    assert len(results) == 1
    assert results[0]["title"] == "Tagged Song"
    assert results[0]["tags"] == "energetic,upbeat"
    assert results[0]["artist"] == "Artist"


def test_get_audio_features_by_track_ids_empty(db_manager):
    result = db_manager.get_audio_features_by_track_ids([])
    assert result == {}


def test_get_audio_features_nonexistent(db_manager):
    result = db_manager.get_audio_features(99999)
    assert result is None


class TestGetTracksEnrichmentColumns:
    """get_tracks exposes per-track audio and MusicBrainz state for the Library table.

    ``has_musicbrainz`` uses the same definition as the library-wide count
    (``musicbrainz_recording_id IS NOT NULL``), not ``musicbrainz_enriched_at``,
    which is stamped even when the lookup found no match.
    """

    @staticmethod
    def _seed(db_manager: SQLiteManager, specs: list[tuple[str, str, str]]) -> dict[str, int]:
        """Insert tracks as (plex_key, title, artist_name); returns plex_key -> track id."""
        ids: dict[str, int] = {}
        for plex_key, title, artist_name in specs:
            artist_id = db_manager.insert_artist(
                Artist(plex_key=f"artist-{artist_name}", name=artist_name)
            )
            album_id = db_manager.insert_album(
                Album(
                    plex_key=f"album-{artist_name}", title=f"{artist_name} LP", artist_id=artist_id
                )
            )
            ids[plex_key] = db_manager.insert_track(
                Track(plex_key=plex_key, title=title, artist_id=artist_id, album_id=album_id)
            )
        return ids

    @staticmethod
    def _rows_by_key(db_manager: SQLiteManager, **kwargs: Any) -> dict[str, dict]:
        return {row["plex_key"]: row for row in db_manager.get_tracks(limit=50, **kwargs)}

    def test_plain_track_has_neither_flag(self, db_manager: SQLiteManager) -> None:
        self._seed(db_manager, [("t1", "Plain", "Artist")])
        row = self._rows_by_key(db_manager)["t1"]
        assert row["has_audio_features"] == 0
        assert row["has_musicbrainz"] == 0

    def test_track_with_audio_features_is_flagged(self, db_manager: SQLiteManager) -> None:
        ids = self._seed(db_manager, [("t1", "Analyzed", "Artist"), ("t2", "Raw", "Artist")])
        db_manager.insert_audio_features(ids["t1"], {"tempo": 120.0, "key": "C"})

        rows = self._rows_by_key(db_manager)
        assert rows["t1"]["has_audio_features"] == 1
        assert rows["t2"]["has_audio_features"] == 0

    def test_track_with_recording_id_is_flagged(self, db_manager: SQLiteManager) -> None:
        ids = self._seed(db_manager, [("t1", "Matched", "Artist")])
        db_manager.update_track_musicbrainz(ids["t1"], recording_id="rec-0001")

        assert self._rows_by_key(db_manager)["t1"]["has_musicbrainz"] == 1

    def test_enriched_at_without_recording_id_is_not_flagged(
        self, db_manager: SQLiteManager
    ) -> None:
        ids = self._seed(db_manager, [("t1", "Looked up", "Artist")])
        # A lookup that found no match still stamps musicbrainz_enriched_at.
        db_manager.update_track_musicbrainz(ids["t1"], recording_id=None)
        cursor = db_manager.get_connection().cursor()
        cursor.execute(
            "SELECT musicbrainz_enriched_at, musicbrainz_recording_id FROM tracks WHERE id = ?",
            (ids["t1"],),
        )
        enriched_at, recording_id = cursor.fetchone()
        assert enriched_at is not None and recording_id is None

        assert self._rows_by_key(db_manager)["t1"]["has_musicbrainz"] == 0

    def test_musicbrainz_flag_matches_library_wide_count(self, db_manager: SQLiteManager) -> None:
        ids = self._seed(
            db_manager,
            [("t1", "A", "Artist"), ("t2", "B", "Artist"), ("t3", "C", "Artist")],
        )
        db_manager.update_track_musicbrainz(ids["t1"], recording_id="rec-1")
        db_manager.update_track_musicbrainz(ids["t2"], recording_id=None)

        rows = db_manager.get_tracks(limit=50)
        assert sum(row["has_musicbrainz"] for row in rows) == (
            db_manager.get_musicbrainz_enrichment_count()
        )
        assert db_manager.get_musicbrainz_enrichment_count() == 1

    def test_tags_flag_matches_library_wide_count(self, db_manager: SQLiteManager) -> None:
        """The Library row's Tags chip (enrichment_flags over get_tracks rows) agrees
        with count_untagged_tracks, including whitespace-only and NULL tags."""
        from plexmix.ui.utils.a11y import enrichment_flags
        from plexmix.ui.utils.helpers import str_dict

        ids = self._seed(
            db_manager,
            [
                ("t1", "Tagged", "Artist"),
                ("t2", "Blank", "Artist"),
                ("t3", "Space", "Artist"),
                ("t4", "Never", "Artist"),
            ],
        )
        db_manager.update_track_tags(ids["t1"], tags="chill, late night")
        db_manager.update_track_tags(ids["t2"], tags="")
        db_manager.update_track_tags(ids["t3"], tags=" ")

        flags = [enrichment_flags(str_dict(row)) for row in db_manager.get_tracks(limit=50)]
        untagged = sum(1 for f in flags if f["enr_tags"] == "0")
        assert untagged == db_manager.count_untagged_tracks() == 2

    def test_flags_are_one_row_per_track_with_filters(self, db_manager: SQLiteManager) -> None:
        ids = self._seed(db_manager, [("t1", "Analyzed", "Artist"), ("t2", "Raw", "Artist")])
        db_manager.insert_audio_features(ids["t1"], {"tempo": 100.0})
        # Re-analysis replaces the row; it must not duplicate the track.
        db_manager.insert_audio_features(ids["t1"], {"tempo": 101.0})

        rows = db_manager.get_tracks(limit=50, has_audio=True)
        assert [row["plex_key"] for row in rows] == ["t1"]
        assert rows[0]["has_audio_features"] == 1
        # Existing columns are unchanged.
        assert {"tags", "has_embedding", "artist_name", "album_title"} <= set(rows[0].keys())

    def test_artist_sort_spans_pages_and_reverses(self, db_manager: SQLiteManager) -> None:
        self._seed(
            db_manager,
            [("t1", "x", "Charlie"), ("t2", "y", "Alpha"), ("t3", "z", "Bravo")],
        )
        first = db_manager.get_tracks(limit=2, offset=0, sort_column="artist")
        second = db_manager.get_tracks(limit=2, offset=2, sort_column="artist")
        assert [r["artist_name"] for r in first + second] == ["Alpha", "Bravo", "Charlie"]

        desc = db_manager.get_tracks(limit=50, sort_column="artist", sort_ascending=False)
        assert [r["artist_name"] for r in desc] == ["Charlie", "Bravo", "Alpha"]

    def test_name_sorts_ignore_case(self, db_manager: SQLiteManager) -> None:
        self._seed(
            db_manager,
            [
                ("t1", "zebra", "Miles Davis"),
                ("t2", "Apple", "bill evans"),
                ("t3", "mango", "Ahmad Jamal"),
                ("t4", "Banana", "ZZ Top"),
            ],
        )
        asc = db_manager.get_tracks(limit=50, sort_column="artist")
        assert [r["artist_name"] for r in asc] == [
            "Ahmad Jamal",
            "bill evans",
            "Miles Davis",
            "ZZ Top",
        ]
        desc = db_manager.get_tracks(limit=50, sort_column="artist", sort_ascending=False)
        assert [r["artist_name"] for r in desc] == [
            "ZZ Top",
            "Miles Davis",
            "bill evans",
            "Ahmad Jamal",
        ]
        titles = db_manager.get_tracks(limit=50, sort_column="title")
        assert [r["title"] for r in titles] == ["Apple", "Banana", "mango", "zebra"]

    def test_unknown_sort_column_falls_back_to_title(self, db_manager: SQLiteManager) -> None:
        self._seed(db_manager, [("t1", "b", "X"), ("t2", "A", "Y")])
        rows = db_manager.get_tracks(limit=50, sort_column="t.id; DROP TABLE tracks")
        assert [r["title"] for r in rows] == ["A", "b"]


# ============================================================================
# Playlist top artists (History card identity, webui-keyboard-and-semantics)
# ============================================================================


class TestPlaylistTopArtists:
    """``SQLiteManager.get_playlist_top_artists``: one grouped read, top N in Python."""

    @staticmethod
    def _artist(db_manager: SQLiteManager, name: str) -> tuple[int, int]:
        """Insert an artist with one album; return ``(artist_id, album_id)``."""
        key = name.lower().replace(" ", "-")
        artist_id = db_manager.insert_artist(Artist(plex_key=f"ta-{key}", name=name))
        album_id = db_manager.insert_album(
            Album(plex_key=f"tal-{key}", title=f"{name} LP", artist_id=artist_id)
        )
        return artist_id, album_id

    def _playlist(
        self, db_manager: SQLiteManager, name: str, artist_counts: list[tuple[str, int]]
    ) -> int:
        """Create a playlist holding ``count`` tracks by each named artist.

        Artists are created once per test and reused across playlists.
        """
        ids: dict[str, tuple[int, int]] = self.__dict__.setdefault("_artist_ids", {})
        track_ids: list[int] = []
        for artist_name, count in artist_counts:
            if artist_name not in ids:
                ids[artist_name] = self._artist(db_manager, artist_name)
            artist_id, album_id = ids[artist_name]
            for i in range(count):
                track_ids.append(
                    db_manager.insert_track(
                        Track(
                            plex_key=f"tt-{name}-{artist_name}-{i}",
                            title=f"{artist_name} {i}",
                            artist_id=artist_id,
                            album_id=album_id,
                        )
                    )
                )
        pid = db_manager.insert_playlist(Playlist(name=name, created_by_ai=True))
        db_manager.add_tracks_to_playlist(pid, track_ids)
        return pid

    def test_empty_db_returns_empty_dict(self, db_manager: SQLiteManager) -> None:
        assert db_manager.get_playlist_top_artists() == {}

    def test_ordered_by_frequency(self, db_manager: SQLiteManager) -> None:
        pid = self._playlist(
            db_manager, "Jazz", [("Chet Baker", 2), ("Bill Evans", 5), ("Miles Davis", 3)]
        )
        assert db_manager.get_playlist_top_artists() == {
            pid: ["Bill Evans", "Miles Davis", "Chet Baker"]
        }

    def test_ties_broken_by_name(self, db_manager: SQLiteManager) -> None:
        pid = self._playlist(
            db_manager, "Ties", [("Zed", 2), ("Alpha", 2), ("Mike", 2), ("Bravo", 1)]
        )
        assert db_manager.get_playlist_top_artists() == {pid: ["Alpha", "Mike", "Zed"]}

    def test_ties_broken_by_name_ignoring_case(self, db_manager: SQLiteManager) -> None:
        pid = self._playlist(db_manager, "Case", [("beta", 1), ("Alpha", 1), ("Gamma", 1)])
        assert db_manager.get_playlist_top_artists() == {pid: ["Alpha", "beta", "Gamma"]}

    def test_limit_honored(self, db_manager: SQLiteManager) -> None:
        pid = self._playlist(db_manager, "Many", [("A", 4), ("B", 3), ("C", 2), ("D", 1)])
        assert db_manager.get_playlist_top_artists() == {pid: ["A", "B", "C"]}
        assert db_manager.get_playlist_top_artists(limit=1) == {pid: ["A"]}
        assert db_manager.get_playlist_top_artists(limit=10) == {pid: ["A", "B", "C", "D"]}
        assert db_manager.get_playlist_top_artists(limit=0) == {}

    def test_playlist_without_tracks_is_absent(self, db_manager: SQLiteManager) -> None:
        full = self._playlist(db_manager, "Full", [("Solo", 2)])
        empty = db_manager.insert_playlist(Playlist(name="Empty", created_by_ai=False))
        top = db_manager.get_playlist_top_artists()
        assert top == {full: ["Solo"]}
        assert empty not in top

    def test_playlists_are_independent(self, db_manager: SQLiteManager) -> None:
        first = self._playlist(db_manager, "One", [("Shared", 1), ("Only One", 3)])
        second = self._playlist(db_manager, "Two", [("Shared", 4), ("Only Two", 1)])
        assert db_manager.get_playlist_top_artists() == {
            first: ["Only One", "Shared"],
            second: ["Shared", "Only Two"],
        }

    def test_repeated_track_counts_each_occurrence(self, db_manager: SQLiteManager) -> None:
        a_id, a_album = self._artist(db_manager, "Repeat")
        b_id, b_album = self._artist(db_manager, "Twice")
        rep = db_manager.insert_track(
            Track(plex_key="rep-1", title="Again", artist_id=a_id, album_id=a_album)
        )
        tw1 = db_manager.insert_track(
            Track(plex_key="tw-1", title="One", artist_id=b_id, album_id=b_album)
        )
        tw2 = db_manager.insert_track(
            Track(plex_key="tw-2", title="Two", artist_id=b_id, album_id=b_album)
        )
        pid = db_manager.insert_playlist(Playlist(name="Loop", created_by_ai=False))
        db_manager.add_tracks_to_playlist(pid, [rep, tw1, rep, tw2, rep])
        assert db_manager.get_playlist_top_artists() == {pid: ["Repeat", "Twice"]}


# ── webui-honest-status-and-first-run 9.1: tag reads and missing-embedding counts ──


class TestTagsAndMissingEmbeddings:
    """``get_tags_for_tracks``, ``count_tracks_without_embeddings`` and
    ``get_tracks_without_embeddings`` (parameterised; used by Library bulk tags and
    the whole-library "Build search index" flow)."""

    @staticmethod
    def _tracks(db_manager: SQLiteManager, n: int) -> list[int]:
        artist_id = db_manager.insert_artist(Artist(plex_key="a-tm", name="Artist"))
        album_id = db_manager.insert_album(
            Album(plex_key="al-tm", title="Album", artist_id=artist_id)
        )
        return [
            db_manager.insert_track(
                Track(plex_key=f"tm-{i}", title=f"Song {i}", artist_id=artist_id, album_id=album_id)
            )
            for i in range(n)
        ]

    @staticmethod
    def _embed(db_manager: SQLiteManager, track_id: int, model: str = "Local") -> None:
        db_manager.insert_embedding(
            Embedding(track_id=track_id, embedding_model=model, embedding_dim=2, vector=[0.1, 0.2])
        )

    def test_get_tags_for_tracks_returns_stored_columns(self, db_manager: SQLiteManager) -> None:
        a, b, c = self._tracks(db_manager, 3)
        db_manager.update_track_tags(a, tags="chill,piano", environments="study", instruments="")
        db_manager.update_track_tags(b, tags="Upbeat, Dance")
        tags = db_manager.get_tags_for_tracks([a, b, c])
        assert tags[a] == {"tags": "chill,piano", "environments": "study", "instruments": ""}
        assert tags[b]["tags"] == "Upbeat, Dance"
        assert tags[b]["environments"] is None
        assert tags[c] == {"tags": None, "environments": None, "instruments": None}

    def test_get_tags_for_tracks_ignores_unknown_and_duplicate_ids(
        self, db_manager: SQLiteManager
    ) -> None:
        (a,) = self._tracks(db_manager, 1)
        db_manager.update_track_tags(a, tags="x")
        assert db_manager.get_tags_for_tracks([]) == {}
        assert db_manager.get_tags_for_tracks([a, a, 999_999]) == {
            a: {"tags": "x", "environments": None, "instruments": None}
        }

    def test_get_tags_for_tracks_is_parameterised(self, db_manager: SQLiteManager) -> None:
        """Ids go through bound parameters: a string id that is not a number fails
        int() rather than ever reaching the SQL text."""
        (a,) = self._tracks(db_manager, 1)
        with pytest.raises(ValueError):
            db_manager.get_tags_for_tracks([a, "1) OR 1=1 --"])  # type: ignore[list-item]
        assert db_manager.get_tags_for_tracks([str(a)]) == {  # type: ignore[list-item]
            a: {"tags": None, "environments": None, "instruments": None}
        }

    def test_get_tags_for_tracks_chunks_large_selections(self, db_manager: SQLiteManager) -> None:
        ids = self._tracks(db_manager, 1200)
        db_manager.update_track_tags(ids[-1], tags="last")
        tags = db_manager.get_tags_for_tracks(ids)
        assert len(tags) == 1200
        assert tags[ids[-1]]["tags"] == "last"

    def test_count_without_embeddings_any_model(self, db_manager: SQLiteManager) -> None:
        ids = self._tracks(db_manager, 5)
        assert db_manager.count_tracks_without_embeddings() == 5
        self._embed(db_manager, ids[0], "Local")
        self._embed(db_manager, ids[1], "Gemini")
        self._embed(db_manager, ids[1], "Local")  # two models: still one embedded track
        assert db_manager.count_tracks_without_embeddings() == 3
        total = len(db_manager.get_all_tracks())
        assert (
            db_manager.count_tracks_with_embeddings() + db_manager.count_tracks_without_embeddings()
            == total
        )

    def test_count_without_embeddings_scoped_to_a_model(self, db_manager: SQLiteManager) -> None:
        ids = self._tracks(db_manager, 3)
        self._embed(db_manager, ids[0], "Local")
        self._embed(db_manager, ids[1], "Gemini")
        assert db_manager.count_tracks_without_embeddings("Local") == 2
        assert db_manager.count_tracks_without_embeddings("Gemini") == 2
        assert db_manager.count_tracks_without_embeddings("Cohere") == 3

    def test_count_without_embeddings_empty_library(self, db_manager: SQLiteManager) -> None:
        assert db_manager.count_tracks_without_embeddings() == 0
        assert db_manager.get_tracks_without_embeddings() == []

    def test_get_tracks_without_embeddings(self, db_manager: SQLiteManager) -> None:
        ids = self._tracks(db_manager, 4)
        self._embed(db_manager, ids[1])
        self._embed(db_manager, ids[3], "Gemini")
        missing = db_manager.get_tracks_without_embeddings()
        assert [t.id for t in missing] == [ids[0], ids[2]]
        assert all(isinstance(t, Track) for t in missing)
        assert [t.id for t in db_manager.get_tracks_without_embeddings("Local")] == [
            ids[0],
            ids[2],
            ids[3],
        ]


# ── fix-cli-secrets-and-data-loss 2.6: a partial sync counts as the last sync ──


def test_last_sync_time_counts_partial(db_manager: SQLiteManager) -> None:
    """A partial sync read Plex and saved its changes (it only kept unread tracks or
    held a removal back), so the "last sync" date must move. Failed and interrupted
    syncs still do not count."""

    def record(day: int, status: str) -> None:
        db_manager.insert_sync_record(
            SyncHistory(sync_date=datetime(2026, 11, day, 12, 0, 0), status=status)
        )

    record(1, "partial")
    only_partial = db_manager.get_last_sync_time()
    assert only_partial is not None and only_partial.startswith("2026-11-01")

    record(2, "success")
    record(3, "partial")
    record(4, "failed")
    record(5, "interrupted")
    last_sync = db_manager.get_last_sync_time()
    assert last_sync is not None and last_sync.startswith("2026-11-03")


# ── fix-cli-secrets-and-data-loss 2.4: the backup made before a large removal ──


@pytest.fixture
def library_db(tmp_path: Path) -> Iterator[SQLiteManager]:
    """A database opened the way PlexMix opens it (write-ahead log on), alone in a folder.

    A backup is written beside the database, so these tests do not use ``db_manager``:
    its file sits in the shared temporary folder.
    """
    manager = SQLiteManager(str(tmp_path / "plexmix.db"))
    manager.connect()
    # No automatic checkpoint: what a test writes stays in the write-ahead log, which
    # is the state a plain file copy gets wrong.
    manager.get_connection().execute("PRAGMA wal_autocheckpoint=0")
    yield manager
    manager.close()


def _add_tracks(db: SQLiteManager, count: int, prefix: str = "bk") -> None:
    artist_id = db.insert_artist(Artist(plex_key=f"{prefix}-artist", name="Artist"))
    album_id = db.insert_album(
        Album(plex_key=f"{prefix}-album", title="Album", artist_id=artist_id)
    )
    for i in range(count):
        db.insert_track(
            Track(
                plex_key=f"{prefix}-{i}", title=f"Song {i}", artist_id=artist_id, album_id=album_id
            )
        )


def _count_tracks_in_copy(db_file: Path) -> int:
    with closing(sqlite3.connect(str(db_file))) as copy:
        count: int = copy.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    return count


def _backup_folders(db: SQLiteManager) -> list[Path]:
    backups = db.db_path.parent / "backups"
    return sorted(backups.iterdir()) if backups.exists() else []


def test_backup_library_copies_database_and_index(
    library_db: SQLiteManager, tmp_path: Path
) -> None:
    _add_tracks(library_db, 3)
    index_path = tmp_path / "embeddings.index"
    index_path.write_bytes(b"index-bytes")
    index_path.with_suffix(".metadata").write_bytes(b"metadata-bytes, a little longer")

    result = backup_library(library_db, index_path)

    assert result.folder.parent == tmp_path / "backups"
    assert re.fullmatch(r"\d{8}_\d{6}", result.folder.name)
    files = sorted(result.folder.iterdir())
    assert [f.name for f in files] == ["embeddings.index", "embeddings.metadata", "plexmix.db"]
    assert result.size_bytes == sum(f.stat().st_size for f in files)
    assert (result.folder / "embeddings.index").read_bytes() == b"index-bytes"
    assert (result.folder / "embeddings.metadata").read_bytes() == (
        b"metadata-bytes, a little longer"
    )
    # The tracks were still in the write-ahead log, so the database file alone was
    # smaller than the copy: copying only that file would have lost them.
    copied_db = result.folder / "plexmix.db"
    assert library_db.db_path.stat().st_size < copied_db.stat().st_size
    assert _count_tracks_in_copy(copied_db) == 3
    # The library itself is untouched.
    assert library_db.count_tracks() == 3


@pytest.mark.parametrize(
    "index_files, expected",
    [
        (None, ["plexmix.db"]),
        ([], ["plexmix.db"]),
        (["embeddings.index"], ["embeddings.index", "plexmix.db"]),
    ],
    ids=["no-index-path", "no-index-files", "index-without-metadata"],
)
def test_backup_library_copies_only_the_index_files_that_exist(
    library_db: SQLiteManager,
    tmp_path: Path,
    index_files: Optional[list[str]],
    expected: list[str],
) -> None:
    _add_tracks(library_db, 1)
    for name in index_files or []:
        (tmp_path / name).write_bytes(b"index-bytes")
    index_path = None if index_files is None else tmp_path / "embeddings.index"

    result = backup_library(library_db, index_path)

    files = sorted(result.folder.iterdir())
    assert [f.name for f in files] == expected
    assert result.size_bytes == sum(f.stat().st_size for f in files)
    assert _count_tracks_in_copy(result.folder / "plexmix.db") == 1


@pytest.mark.parametrize(
    "raised, expected",
    [
        (OSError(28, "No space left on device"), BackupError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
    ids=["copy-fails", "ctrl-c"],
)
def test_backup_library_failure_leaves_no_partial_folder(
    library_db: SQLiteManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raised: BaseException,
    expected: type[BaseException],
) -> None:
    _add_tracks(library_db, 2)
    index_path = tmp_path / "embeddings.index"
    index_path.write_bytes(b"index-bytes")
    database_copied_first = []

    def failing_copy(source: Any, target: Any) -> None:
        database_copied_first.append((Path(target).parent / "plexmix.db").exists())
        raise raised

    monkeypatch.setattr(backup_module.shutil, "copy2", failing_copy)

    with pytest.raises(expected) as caught:
        backup_library(library_db, index_path)

    # The folder already held the database copy when the index copy failed.
    assert database_copied_first == [True]
    assert _backup_folders(library_db) == []
    if expected is BackupError:
        assert "No space left on device" in str(caught.value)
        assert caught.value.__cause__ is raised
    # The library itself is untouched.
    assert library_db.count_tracks() == 2
    assert index_path.read_bytes() == b"index-bytes"


def test_backup_library_twice_in_one_second_keeps_both(
    library_db: SQLiteManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup_module, "_timestamp", lambda: "20261118_101500")

    _add_tracks(library_db, 1, prefix="first")
    first = backup_library(library_db, None)
    _add_tracks(library_db, 2, prefix="second")
    second = backup_library(library_db, None)
    third = backup_library(library_db, None)

    assert [result.folder.name for result in (first, second, third)] == [
        "20261118_101500",
        "20261118_101500_2",
        "20261118_101500_3",
    ]
    assert _backup_folders(library_db) == [first.folder, second.folder, third.folder]
    # The earlier backup was not overwritten by the later ones.
    assert _count_tracks_in_copy(first.folder / "plexmix.db") == 1
    assert _count_tracks_in_copy(second.folder / "plexmix.db") == 3
    assert _count_tracks_in_copy(third.folder / "plexmix.db") == 3


def test_backup_library_refuses_inside_an_open_transaction(library_db: SQLiteManager) -> None:
    """SQLite's online backup waits without end for its own connection's transaction,
    so the backup must refuse at once."""
    _add_tracks(library_db, 1)

    with library_db.deferred_commits():
        library_db.insert_artist(Artist(plex_key="uncommitted", name="Uncommitted"))
        with pytest.raises(BackupError, match="not committed"):
            backup_library(library_db, None)

    assert _backup_folders(library_db) == []


def test_backup_library_gives_up_when_the_database_stays_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database that another connection keeps locked ends in BackupError, not in an
    endless wait."""
    db_path = tmp_path / "plexmix.db"
    manager = SQLiteManager(str(db_path))
    # Rollback-journal mode and no busy wait: an exclusive lock blocks the copy at once.
    manager.conn = sqlite3.connect(str(db_path), timeout=0)
    manager.conn.row_factory = sqlite3.Row
    manager.create_tables()
    locker = sqlite3.connect(str(db_path), timeout=0, check_same_thread=False)
    locker.execute("BEGIN EXCLUSIVE")
    # Safety net: if the wait limit were ignored, this releases the lock, the backup
    # completes and the test fails. It does not hang.
    release = threading.Timer(10, locker.rollback)
    release.start()
    monkeypatch.setattr(backup_module, "_LOCK_WAIT_SECONDS", 0.0)

    try:
        with pytest.raises(BackupError, match="stayed locked"):
            backup_library(manager, None)
        assert _backup_folders(manager) == []
    finally:
        release.cancel()
        release.join()
        locker.close()
        manager.close()


def test_backup_library_reports_a_backup_folder_that_cannot_be_created(
    library_db: SQLiteManager, tmp_path: Path
) -> None:
    _add_tracks(library_db, 1)
    in_the_way = tmp_path / "backups"
    in_the_way.write_text("a file where the backups folder would go")

    with pytest.raises(BackupError, match="Could not write a backup") as caught:
        backup_library(library_db, None)

    assert isinstance(caught.value.__cause__, OSError)
    assert in_the_way.read_text() == "a file where the backups folder would go"
    assert library_db.count_tracks() == 1


def test_backup_library_without_a_database_file_creates_nothing(tmp_path: Path) -> None:
    manager = SQLiteManager(str(tmp_path / "plexmix.db"))

    with pytest.raises(BackupError, match="no database file"):
        backup_library(manager, None)

    assert list(tmp_path.iterdir()) == []
