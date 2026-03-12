import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.models import Artist, Album, Track, Genre, Embedding, SyncHistory, Playlist


@pytest.fixture
def db_manager():
    import sqlite3

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


def test_create_tables(db_manager):
    cursor = db_manager.get_connection().cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}

    assert 'artists' in tables
    assert 'albums' in tables
    assert 'tracks' in tables
    assert 'embeddings' in tables
    assert 'playlists' in tables
    assert 'sync_history' in tables


def test_insert_and_get_artist(db_manager):
    artist = Artist(
        plex_key="/library/metadata/12345",
        name="Miles Davis",
        genre="Jazz",
        bio="Legendary jazz musician"
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
        genre="Jazz"
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

    album = Album(plex_key="/library/metadata/2", title="Sunday at the Village Vanguard", artist_id=artist_id)
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
        instruments="piano, bass, drums"
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

    track = Track(plex_key="/library/metadata/3", title="Track", artist_id=artist_id, album_id=album_id)
    track_id = db_manager.insert_track(track)

    embedding = Embedding(
        track_id=track_id,
        embedding_model="gemini-embedding-001",
        embedding_dim=3072,
        vector=[0.1] * 3072
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

    track1 = Track(plex_key="/library/metadata/3", title="Track 1", artist_id=artist_id, album_id=album_id)
    track1_id = db_manager.insert_track(track1)

    track2 = Track(plex_key="/library/metadata/4", title="Track 2", artist_id=artist_id, album_id=album_id)
    track2_id = db_manager.insert_track(track2)

    embedding1 = Embedding(track_id=track1_id, embedding_model="test", embedding_dim=384, vector=[0.1] * 384)
    embedding2 = Embedding(track_id=track2_id, embedding_model="test", embedding_dim=384, vector=[0.2] * 384)

    db_manager.insert_embedding(embedding1)
    db_manager.insert_embedding(embedding2)

    all_embeddings = db_manager.get_all_embeddings()
    assert len(all_embeddings) == 2
    assert all_embeddings[0][0] in [track1_id, track2_id]
    assert len(all_embeddings[0][1]) == 384


def test_insert_sync_record(db_manager):
    sync = SyncHistory(
        tracks_added=100,
        tracks_updated=50,
        tracks_removed=10,
        status='success'
    )

    sync_id = db_manager.insert_sync_record(sync)
    assert sync_id > 0

    latest = db_manager.get_latest_sync()
    assert latest is not None
    assert latest.tracks_added == 100
    assert latest.status == 'success'


def test_insert_playlist(db_manager):
    playlist = Playlist(
        plex_key="/library/metadata/playlist/1",
        name="Chill Vibes",
        description="Relaxing music",
        created_by_ai=True,
        mood_query="relaxing evening vibes"
    )

    playlist_id = db_manager.insert_playlist(playlist)
    assert playlist_id > 0


def test_add_track_to_playlist(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track = Track(plex_key="/library/metadata/3", title="Track", artist_id=artist_id, album_id=album_id)
    track_id = db_manager.insert_track(track)

    playlist = Playlist(name="Test Playlist", created_by_ai=False)
    playlist_id = db_manager.insert_playlist(playlist)

    db_manager.add_track_to_playlist(playlist_id, track_id, 0)

    cursor = db_manager.get_connection().cursor()
    cursor.execute("SELECT * FROM playlist_tracks WHERE playlist_id = ? AND track_id = ?", (playlist_id, track_id))
    result = cursor.fetchone()

    assert result is not None
    assert result['position'] == 0


def test_delete_track(db_manager):
    artist = Artist(plex_key="/library/metadata/1", name="Artist")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    track = Track(plex_key="/library/metadata/3", title="Track", artist_id=artist_id, album_id=album_id)
    track_id = db_manager.insert_track(track)

    db_manager.delete_track(track_id)

    retrieved = db_manager.get_track_by_id(track_id)
    assert retrieved is None


def test_migration_adds_environments_column(db_manager):
    cursor = db_manager.get_connection().cursor()
    cursor.execute("PRAGMA table_info(tracks)")
    columns = {col[1] for col in cursor.fetchall()}

    assert 'environments' in columns
    assert 'instruments' in columns


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
        duration_ms=180000
    )
    first_id = db_manager.insert_track(track)

    # Upsert with same plex_key but different title
    track_updated = Track(
        plex_key="/library/metadata/3",  # Same plex_key
        title="Updated Title",
        artist_id=artist_id,
        album_id=album_id,
        duration_ms=190000
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
        duration_ms=180000
    )

    # This should raise an error due to foreign key constraint
    with pytest.raises(Exception):
        db_manager.insert_track(track)


def test_get_last_sync_time_returns_success_status(db_manager):
    """Test that get_last_sync_time correctly queries for 'success' status."""
    # Insert a sync record with 'success' status
    sync_success = SyncHistory(
        tracks_added=100,
        tracks_updated=50,
        tracks_removed=10,
        status='success'
    )
    db_manager.insert_sync_record(sync_success)

    # Insert a failed sync record
    sync_failed = SyncHistory(
        tracks_added=0,
        tracks_updated=0,
        tracks_removed=0,
        status='failed'
    )
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
        instruments="drums,guitar"
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
        instruments=None
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
            album_id=album_id
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
        plex_key="/library/metadata/3",
        title="Test Track",
        artist_id=artist_id,
        album_id=album_id
    )
    track_id = db_manager.insert_track(track)

    details = db_manager.get_track_details_by_ids([track_id])

    assert len(details) == 1
    assert details[0]['title'] == "Test Track"
    assert details[0]['artist_name'] == "Test Artist"
    assert details[0]['album_title'] == "Test Album"
    assert 'file_path' in details[0]


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
        file_path="/music/artist/album/track.flac"
    )
    track_id = db_manager.insert_track(track)

    details = db_manager.get_track_details_by_ids([track_id])

    assert len(details) == 1
    assert details[0]['file_path'] == "/music/artist/album/track.flac"


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
