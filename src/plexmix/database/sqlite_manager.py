import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
import logging

from .models import Artist, Album, Track, Genre, Embedding, SyncHistory, Playlist

logger = logging.getLogger(__name__)


class SQLiteManager:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> 'SQLiteManager':
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def connect(self) -> None:
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        logger.info(f"Connected to database at {self.db_path}")
        self._run_migrations(self.conn.cursor())

    def get_connection(self) -> sqlite3.Connection:
        if not self.conn:
            self.connect()
        return self.conn  # type: ignore

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.info("Database connection closed")

    def create_tables(self) -> None:
        cursor = self.get_connection().cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plex_key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                genre TEXT,
                bio TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS albums (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plex_key TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                artist_id INTEGER NOT NULL,
                year INTEGER,
                genre TEXT,
                cover_art_url TEXT,
                FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plex_key TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                artist_id INTEGER NOT NULL,
                album_id INTEGER NOT NULL,
                duration_ms INTEGER,
                genre TEXT,
                year INTEGER,
                rating REAL,
                play_count INTEGER DEFAULT 0,
                last_played TIMESTAMP,
                file_path TEXT,
                tags TEXT,
                environments TEXT,
                instruments TEXT,
                FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE,
                FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS genres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS track_genres (
                track_id INTEGER NOT NULL,
                genre_id INTEGER NOT NULL,
                PRIMARY KEY (track_id, genre_id),
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                FOREIGN KEY (genre_id) REFERENCES genres(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                vector TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tracks_added INTEGER DEFAULT 0,
                tracks_updated INTEGER DEFAULT 0,
                tracks_removed INTEGER DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plex_key TEXT,
                name TEXT NOT NULL,
                description TEXT,
                created_by_ai INTEGER DEFAULT 0,
                mood_query TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                playlist_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (playlist_id, track_id, position),
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )
        ''')

        self._create_indexes(cursor)
        self._create_fts_table(cursor)
        self._run_migrations(cursor)
        self.get_connection().commit()
        logger.info("Database tables created successfully")

    def _create_indexes(self, cursor: sqlite3.Cursor) -> None:
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist_id)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_rating ON tracks(rating)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_year ON tracks(year)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_genre ON tracks(genre)",
            "CREATE INDEX IF NOT EXISTS idx_albums_artist ON albums(artist_id)",
            "CREATE INDEX IF NOT EXISTS idx_albums_year ON albums(year)",
            "CREATE INDEX IF NOT EXISTS idx_embeddings_track ON embeddings(track_id)",
            "CREATE INDEX IF NOT EXISTS idx_track_genres_track ON track_genres(track_id)",
            "CREATE INDEX IF NOT EXISTS idx_track_genres_genre ON track_genres(genre_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_artists_plex_key ON artists(plex_key)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_albums_plex_key ON albums(plex_key)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tracks_plex_key ON tracks(plex_key)",
        ]
        for index_sql in indexes:
            cursor.execute(index_sql)
        logger.debug("Database indexes created")

    def _create_fts_table(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
                title,
                artist_name,
                album_title,
                genres,
                track_id UNINDEXED
            )
        ''')

        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS tracks_fts_insert AFTER INSERT ON tracks
            BEGIN
                INSERT INTO tracks_fts(title, artist_name, album_title, genres, track_id)
                SELECT
                    NEW.title,
                    (SELECT name FROM artists WHERE id = NEW.artist_id),
                    (SELECT title FROM albums WHERE id = NEW.album_id),
                    NEW.genre,
                    NEW.id;
            END
        ''')

        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS tracks_fts_update AFTER UPDATE ON tracks
            BEGIN
                UPDATE tracks_fts
                SET title = NEW.title,
                    artist_name = (SELECT name FROM artists WHERE id = NEW.artist_id),
                    album_title = (SELECT title FROM albums WHERE id = NEW.album_id),
                    genres = NEW.genre
                WHERE track_id = NEW.id;
            END
        ''')

        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS tracks_fts_delete AFTER DELETE ON tracks
            BEGIN
                DELETE FROM tracks_fts WHERE track_id = OLD.id;
            END
        ''')
        logger.debug("FTS5 table and triggers created")

    def _run_migrations(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(tracks)")
        columns = {col[1] for col in cursor.fetchall()}

        migrations_run = False

        if 'environment' in columns and 'environments' not in columns:
            logger.info("Running migration: Renaming environment to environments")
            cursor.execute("ALTER TABLE tracks RENAME COLUMN environment TO environments")
            migrations_run = True
        elif 'environment' not in columns and 'environments' not in columns:
            logger.info("Running migration: Adding environments column to tracks")
            cursor.execute("ALTER TABLE tracks ADD COLUMN environments TEXT")
            migrations_run = True

        if 'primary_instrument' in columns and 'instruments' not in columns:
            logger.info("Running migration: Renaming primary_instrument to instruments")
            cursor.execute("ALTER TABLE tracks RENAME COLUMN primary_instrument TO instruments")
            migrations_run = True
        elif 'primary_instrument' not in columns and 'instruments' not in columns:
            logger.info("Running migration: Adding instruments column to tracks")
            cursor.execute("ALTER TABLE tracks ADD COLUMN instruments TEXT")
            migrations_run = True

        if migrations_run:
            self.get_connection().commit()
            logger.info("Database migrations completed")

    def insert_artist(self, artist: Artist) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO artists (plex_key, name, genre, bio)
            VALUES (?, ?, ?, ?)
        ''', (artist.plex_key, artist.name, artist.genre, artist.bio))
        self.get_connection().commit()
        return cursor.lastrowid

    def get_artist_by_id(self, artist_id: int) -> Optional[Artist]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM artists WHERE id = ?', (artist_id,))
        row = cursor.fetchone()
        if row:
            return Artist(**dict(row))
        return None

    def get_artist_by_plex_key(self, plex_key: str) -> Optional[Artist]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM artists WHERE plex_key = ?', (plex_key,))
        row = cursor.fetchone()
        if row:
            return Artist(**dict(row))
        return None

    def insert_album(self, album: Album) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO albums (plex_key, title, artist_id, year, genre, cover_art_url)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (album.plex_key, album.title, album.artist_id, album.year, album.genre, album.cover_art_url))
        self.get_connection().commit()
        return cursor.lastrowid

    def get_album_by_id(self, album_id: int) -> Optional[Album]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM albums WHERE id = ?', (album_id,))
        row = cursor.fetchone()
        if row:
            return Album(**dict(row))
        return None

    def get_album_by_plex_key(self, plex_key: str) -> Optional[Album]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM albums WHERE plex_key = ?', (plex_key,))
        row = cursor.fetchone()
        if row:
            return Album(**dict(row))
        return None

    def insert_track(self, track: Track) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO tracks
            (plex_key, title, artist_id, album_id, duration_ms, genre, year, rating, play_count, last_played, file_path, tags, environments, instruments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (track.plex_key, track.title, track.artist_id, track.album_id, track.duration_ms,
              track.genre, track.year, track.rating, track.play_count, track.last_played, track.file_path, track.tags,
              track.environments, track.instruments))
        self.get_connection().commit()
        return cursor.lastrowid

    def get_track_by_id(self, track_id: int) -> Optional[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM tracks WHERE id = ?', (track_id,))
        row = cursor.fetchone()
        if row:
            return Track(**dict(row))
        return None

    def get_track_by_plex_key(self, plex_key: str) -> Optional[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM tracks WHERE plex_key = ?', (plex_key,))
        row = cursor.fetchone()
        if row:
            return Track(**dict(row))
        return None

    def get_all_tracks(self) -> List[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM tracks')
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def delete_track(self, track_id: int) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute('DELETE FROM tracks WHERE id = ?', (track_id,))
        self.get_connection().commit()

    def insert_genre(self, genre: Genre) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('INSERT OR IGNORE INTO genres (name) VALUES (?)', (genre.name,))
        self.get_connection().commit()
        return cursor.lastrowid

    def get_genre_by_name(self, name: str) -> Optional[Genre]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM genres WHERE name = ?', (name.lower(),))
        row = cursor.fetchone()
        if row:
            return Genre(**dict(row))
        return None

    def insert_embedding(self, embedding: Embedding) -> int:
        cursor = self.get_connection().cursor()
        vector_json = json.dumps(embedding.vector)
        cursor.execute('''
            INSERT OR REPLACE INTO embeddings (track_id, embedding_model, embedding_dim, vector, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (embedding.track_id, embedding.embedding_model, embedding.embedding_dim,
              vector_json, embedding.created_at, embedding.updated_at))
        self.get_connection().commit()
        return cursor.lastrowid

    def get_embedding_by_track_id(self, track_id: int) -> Optional[Embedding]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM embeddings WHERE track_id = ?', (track_id,))
        row = cursor.fetchone()
        if row:
            data = dict(row)
            data['vector'] = json.loads(data['vector'])
            return Embedding(**data)
        return None

    def get_all_embeddings(self) -> List[Tuple[int, List[float]]]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT track_id, vector FROM embeddings')
        return [(row['track_id'], json.loads(row['vector'])) for row in cursor.fetchall()]

    def insert_sync_record(self, sync: SyncHistory) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            INSERT INTO sync_history (sync_date, tracks_added, tracks_updated, tracks_removed, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (sync.sync_date, sync.tracks_added, sync.tracks_updated, sync.tracks_removed,
              sync.status, sync.error_message))
        self.get_connection().commit()
        return cursor.lastrowid

    def get_latest_sync(self) -> Optional[SyncHistory]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM sync_history ORDER BY sync_date DESC LIMIT 1')
        row = cursor.fetchone()
        if row:
            return SyncHistory(**dict(row))
        return None

    def insert_playlist(self, playlist: Playlist) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            INSERT INTO playlists (plex_key, name, description, created_by_ai, mood_query, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (playlist.plex_key, playlist.name, playlist.description,
              int(playlist.created_by_ai), playlist.mood_query, playlist.created_at))
        self.get_connection().commit()
        return cursor.lastrowid

    def add_track_to_playlist(self, playlist_id: int, track_id: int, position: int) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            INSERT INTO playlist_tracks (playlist_id, track_id, position)
            VALUES (?, ?, ?)
        ''', (playlist_id, track_id, position))
        self.get_connection().commit()

    def search_tracks_fts(self, query: str) -> List[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT t.* FROM tracks t
            JOIN tracks_fts fts ON t.id = fts.track_id
            WHERE tracks_fts MATCH ?
            ORDER BY rank
        ''', (query,))
        return [Track(**dict(row)) for row in cursor.fetchall()]
