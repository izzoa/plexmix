import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock
import numpy as np

from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.database.models import Artist, Album, Track, Embedding
from plexmix.playlist.generator import PlaylistGenerator


class MockAIProvider:
    def generate_playlist(self, mood_query, candidates, max_tracks):
        return [c['id'] for c in candidates[:max_tracks]]


class MockEmbeddingGenerator:
    def generate_embedding(self, text):
        return [0.1] * 384

    def get_dimension(self):
        return 384


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


@pytest.fixture
def vector_index():
    with tempfile.TemporaryDirectory() as tmpdir:
        index_path = Path(tmpdir) / "test_index.faiss"
        index = VectorIndex(dimension=384, index_path=str(index_path))
        yield index


@pytest.fixture
def playlist_generator(db_manager, vector_index):
    ai_provider = MockAIProvider()
    embedding_generator = MockEmbeddingGenerator()
    return PlaylistGenerator(db_manager, vector_index, ai_provider, embedding_generator)


@pytest.fixture
def sample_tracks(db_manager, vector_index):
    artist = Artist(plex_key="/library/metadata/1", name="Miles Davis")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/2", title="Kind of Blue", artist_id=artist_id, year=1959)
    album_id = db_manager.insert_album(album)

    tracks = []
    track_ids = []
    vectors = []

    for i in range(5):
        track = Track(
            plex_key=f"/library/metadata/{i+3}",
            title=f"Track {i+1}",
            artist_id=artist_id,
            album_id=album_id,
            genre="Jazz",
            year=1959,
            rating=4.5,
            tags="mellow, smooth, sophisticated",
            environments="relax, study",
            instruments="piano, bass, drums"
        )
        track_id = db_manager.insert_track(track)
        track_ids.append(track_id)
        tracks.append(track)

        vector = [0.1 + i * 0.01] * 384
        vectors.append(vector)

        embedding = Embedding(
            track_id=track_id,
            embedding_model="test",
            embedding_dim=384,
            vector=vector
        )
        db_manager.insert_embedding(embedding)

    vector_index.build_index(vectors, track_ids)

    return tracks, track_ids


def test_playlist_generator_initialization(playlist_generator):
    assert playlist_generator.db is not None
    assert playlist_generator.vector_index is not None
    assert playlist_generator.ai_provider is not None
    assert playlist_generator.embedding_generator is not None


def test_generate_playlist_basic(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    result = playlist_generator.generate(
        mood_query="relaxing jazz",
        max_tracks=3,
        candidate_pool_size=5
    )

    assert len(result) <= 3
    assert all('title' in track for track in result)
    assert all('artist' in track for track in result)


def test_generate_playlist_with_genre_filter(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    result = playlist_generator.generate(
        mood_query="energetic music",
        max_tracks=3,
        filters={'genre': 'Jazz'}
    )

    assert all(track['genre'] == 'Jazz' for track in result if 'genre' in track)


def test_generate_playlist_with_year_filter(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    result = playlist_generator.generate(
        mood_query="vintage music",
        max_tracks=3,
        filters={'year_min': 1950, 'year_max': 1960}
    )

    assert all(1950 <= track['year'] <= 1960 for track in result if 'year' in track)


def test_generate_playlist_no_candidates_returns_empty(playlist_generator, db_manager):
    result = playlist_generator.generate(
        mood_query="impossible query",
        max_tracks=10,
        filters={'genre': 'NonExistentGenre'}
    )

    assert len(result) == 0


def test_apply_filters_genre(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    filtered_ids = playlist_generator._apply_filters({'genre': 'Jazz'})

    assert len(filtered_ids) > 0
    assert all(tid in track_ids for tid in filtered_ids)


def test_apply_filters_year_range(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    filtered_ids = playlist_generator._apply_filters({'year_min': 1950, 'year_max': 1960})

    assert len(filtered_ids) > 0
    assert all(tid in track_ids for tid in filtered_ids)


def test_apply_filters_environment(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    filtered_ids = playlist_generator._apply_filters({'environment': 'relax'})

    assert len(filtered_ids) > 0


def test_apply_filters_instrument(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    filtered_ids = playlist_generator._apply_filters({'instrument': 'piano'})

    assert len(filtered_ids) > 0


def test_apply_filters_rating(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    filtered_ids = playlist_generator._apply_filters({'rating_min': 4.0})

    assert len(filtered_ids) > 0


def test_get_candidates(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    candidates = playlist_generator._get_candidates(
        mood_query="smooth jazz",
        pool_size=3
    )

    assert len(candidates) <= 3
    assert all('id' in c for c in candidates)
    assert all('title' in c for c in candidates)
    assert all('artist' in c for c in candidates)
    assert all('similarity' in c for c in candidates)


def test_get_candidates_with_filter(playlist_generator, sample_tracks):
    tracks, track_ids = sample_tracks

    filtered_ids = [track_ids[0], track_ids[1]]

    candidates = playlist_generator._get_candidates(
        mood_query="smooth jazz",
        pool_size=5,
        filtered_track_ids=filtered_ids
    )

    assert len(candidates) <= 2
    assert all(c['id'] in filtered_ids for c in candidates)


def test_save_playlist(playlist_generator, sample_tracks, db_manager):
    tracks, track_ids = sample_tracks

    playlist_id = playlist_generator.save_playlist(
        name="Test Playlist",
        track_ids=track_ids[:3],
        mood_query="relaxing jazz",
        description="A test playlist"
    )

    assert playlist_id > 0

    cursor = db_manager.get_connection().cursor()
    cursor.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,))
    playlist = cursor.fetchone()

    assert playlist is not None
    assert playlist['name'] == "Test Playlist"
    assert playlist['mood_query'] == "relaxing jazz"

    cursor.execute("SELECT COUNT(*) as count FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
    count = cursor.fetchone()['count']

    assert count == 3


def test_generate_removes_duplicates(playlist_generator, sample_tracks, db_manager):
    tracks, track_ids = sample_tracks

    artist = Artist(plex_key="/library/metadata/100", name="Miles Davis")
    artist_id = db_manager.insert_artist(artist)

    album = Album(plex_key="/library/metadata/101", title="Another Album", artist_id=artist_id)
    album_id = db_manager.insert_album(album)

    duplicate_track = Track(
        plex_key="/library/metadata/102",
        title="Track 1",
        artist_id=artist_id,
        album_id=album_id,
        genre="Jazz"
    )
    dup_track_id = db_manager.insert_track(duplicate_track)

    vector = [0.1] * 384
    embedding = Embedding(track_id=dup_track_id, embedding_model="test", embedding_dim=384, vector=vector)
    db_manager.insert_embedding(embedding)

    result = playlist_generator.generate(
        mood_query="jazz",
        max_tracks=10
    )

    titles_and_artists = [(track['title'], track['artist']) for track in result]
    assert len(titles_and_artists) == len(set(titles_and_artists))
