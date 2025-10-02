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
