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
        db_existed = self.db_path.exists()
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row

        # Enable foreign key constraints and optimize for concurrency
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'")
        has_tables = cursor.fetchone() is not None

        if not db_existed or not has_tables:
            if db_existed and not has_tables:
                logger.warning(f"Database exists but is empty. Initializing schema at {self.db_path}")
            else:
                logger.warning(f"Database did not exist. Creating new database at {self.db_path}")
            self.create_tables()
        else:
            logger.info(f"Connected to database at {self.db_path}")
            self._run_migrations(cursor)

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
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_track_model ON embeddings(track_id, embedding_model)",
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

        # Add unique index on embeddings(track_id, embedding_model) for existing databases
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name='idx_embeddings_track_model'
        """)
        if cursor.fetchone() is None:
            logger.info("Running migration: Adding unique index on embeddings(track_id, embedding_model)")
            # Remove duplicates before adding unique constraint (keep most recent)
            cursor.execute("""
                DELETE FROM embeddings WHERE id NOT IN (
                    SELECT MAX(id) FROM embeddings GROUP BY track_id, embedding_model
                )
            """)
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_track_model "
                "ON embeddings(track_id, embedding_model)"
            )
            migrations_run = True

        # Fix sync_history status values: 'completed' -> 'success'
        cursor.execute("UPDATE sync_history SET status = 'success' WHERE status = 'completed'")
        if cursor.rowcount > 0:
            logger.info(f"Running migration: Fixed {cursor.rowcount} sync records with 'completed' -> 'success'")
            migrations_run = True

        if migrations_run:
            self.get_connection().commit()
            logger.info("Database migrations completed")

    def insert_artist(self, artist: Artist) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            INSERT INTO artists (plex_key, name, genre, bio)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(plex_key) DO UPDATE SET
                name = excluded.name,
                genre = excluded.genre,
                bio = excluded.bio
        ''', (artist.plex_key, artist.name, artist.genre, artist.bio))
        self.get_connection().commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute('SELECT id FROM artists WHERE plex_key = ?', (artist.plex_key,))
        row = cursor.fetchone()
        return row['id'] if row else cursor.lastrowid

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
            INSERT INTO albums (plex_key, title, artist_id, year, genre, cover_art_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(plex_key) DO UPDATE SET
                title = excluded.title,
                artist_id = excluded.artist_id,
                year = excluded.year,
                genre = excluded.genre,
                cover_art_url = excluded.cover_art_url
        ''', (album.plex_key, album.title, album.artist_id, album.year, album.genre, album.cover_art_url))
        self.get_connection().commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute('SELECT id FROM albums WHERE plex_key = ?', (album.plex_key,))
        row = cursor.fetchone()
        return row['id'] if row else cursor.lastrowid

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
            INSERT INTO tracks
            (plex_key, title, artist_id, album_id, duration_ms, genre, year, rating, play_count, last_played, file_path, tags, environments, instruments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plex_key) DO UPDATE SET
                title = excluded.title,
                artist_id = excluded.artist_id,
                album_id = excluded.album_id,
                duration_ms = excluded.duration_ms,
                genre = excluded.genre,
                year = excluded.year,
                rating = excluded.rating,
                play_count = excluded.play_count,
                last_played = excluded.last_played,
                file_path = excluded.file_path,
                tags = COALESCE(excluded.tags, tracks.tags),
                environments = COALESCE(excluded.environments, tracks.environments),
                instruments = COALESCE(excluded.instruments, tracks.instruments)
        ''', (track.plex_key, track.title, track.artist_id, track.album_id, track.duration_ms,
              track.genre, track.year, track.rating, track.play_count, track.last_played, track.file_path, track.tags,
              track.environments, track.instruments))
        self.get_connection().commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute('SELECT id FROM tracks WHERE plex_key = ?', (track.plex_key,))
        row = cursor.fetchone()
        return row['id'] if row else cursor.lastrowid

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

    def get_all_artists(self) -> List[Artist]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM artists')
        return [Artist(**dict(row)) for row in cursor.fetchall()]

    def get_all_albums(self) -> List[Album]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM albums')
        return [Album(**dict(row)) for row in cursor.fetchall()]

    def get_all_tracks(self) -> List[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute('SELECT * FROM tracks')
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def get_tracks_by_ids(self, track_ids: List[int]) -> Dict[int, Track]:
        """Bulk fetch tracks by IDs. Returns dict mapping id -> Track."""
        if not track_ids:
            return {}
        cursor = self.get_connection().cursor()
        placeholders = ','.join('?' * len(track_ids))
        cursor.execute(f'SELECT * FROM tracks WHERE id IN ({placeholders})', track_ids)
        return {row['id']: Track(**dict(row)) for row in cursor.fetchall()}

    def get_artists_by_ids(self, artist_ids: List[int]) -> Dict[int, Artist]:
        """Bulk fetch artists by IDs. Returns dict mapping id -> Artist."""
        if not artist_ids:
            return {}
        cursor = self.get_connection().cursor()
        placeholders = ','.join('?' * len(artist_ids))
        cursor.execute(f'SELECT * FROM artists WHERE id IN ({placeholders})', artist_ids)
        return {row['id']: Artist(**dict(row)) for row in cursor.fetchall()}

    def get_albums_by_ids(self, album_ids: List[int]) -> Dict[int, Album]:
        """Bulk fetch albums by IDs. Returns dict mapping id -> Album."""
        if not album_ids:
            return {}
        cursor = self.get_connection().cursor()
        placeholders = ','.join('?' * len(album_ids))
        cursor.execute(f'SELECT * FROM albums WHERE id IN ({placeholders})', album_ids)
        return {row['id']: Album(**dict(row)) for row in cursor.fetchall()}

    def get_track_details_by_ids(self, track_ids: List[int]) -> List[Dict[str, Any]]:
        """Bulk fetch tracks with artist/album info. Returns list of dicts."""
        if not track_ids:
            return []
        cursor = self.get_connection().cursor()
        placeholders = ','.join('?' * len(track_ids))
        cursor.execute(f'''
            SELECT
                t.id,
                t.plex_key,
                t.title,
                t.duration_ms,
                t.genre,
                t.year,
                t.rating,
                t.tags,
                t.environments,
                t.instruments,
                t.artist_id,
                t.album_id,
                a.name as artist_name,
                al.title as album_title
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            WHERE t.id IN ({placeholders})
        ''', track_ids)
        return [dict(row) for row in cursor.fetchall()]

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
            INSERT INTO embeddings (track_id, embedding_model, embedding_dim, vector, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id, embedding_model) DO UPDATE SET
                embedding_dim = excluded.embedding_dim,
                vector = excluded.vector,
                updated_at = excluded.updated_at
        ''', (embedding.track_id, embedding.embedding_model, embedding.embedding_dim,
              vector_json, embedding.created_at, embedding.updated_at))
        self.get_connection().commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute(
            'SELECT id FROM embeddings WHERE track_id = ? AND embedding_model = ?',
            (embedding.track_id, embedding.embedding_model)
        )
        row = cursor.fetchone()
        return row['id'] if row else cursor.lastrowid

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

    def get_playlists(self) -> List[Playlist]:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT
                p.id, p.plex_key, p.name, p.description, p.created_by_ai,
                p.mood_query, p.created_at,
                COUNT(pt.track_id) as track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        ''')
        return [Playlist(**dict(row)) for row in cursor.fetchall()]

    def get_playlist_by_id(self, playlist_id: int) -> Optional[Playlist]:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT id, plex_key, name, description, created_by_ai, mood_query, created_at
            FROM playlists
            WHERE id = ?
        ''', (playlist_id,))
        row = cursor.fetchone()
        return Playlist(**dict(row)) if row else None

    def get_playlist_tracks(self, playlist_id: int) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT
                t.id,
                t.plex_key,
                t.title,
                t.duration_ms,
                t.genre,
                t.year,
                a.name as artist_name,
                al.title as album_title,
                pt.position
            FROM playlist_tracks pt
            JOIN tracks t ON pt.track_id = t.id
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position
        ''', (playlist_id,))
        return [dict(row) for row in cursor.fetchall()]

    def delete_playlist(self, playlist_id: int) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute('DELETE FROM playlist_tracks WHERE playlist_id = ?', (playlist_id,))
        cursor.execute('DELETE FROM playlists WHERE id = ?', (playlist_id,))
        self.get_connection().commit()

    def update_playlist(self, playlist_id: int, name: Optional[str] = None, description: Optional[str] = None) -> None:
        cursor = self.get_connection().cursor()
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if not updates:
            return

        params.append(playlist_id)
        query = f"UPDATE playlists SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        self.get_connection().commit()

    def search_tracks_fts(self, query: str) -> List[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT t.* FROM tracks t
            JOIN tracks_fts fts ON t.id = fts.track_id
            WHERE tracks_fts MATCH ?
            ORDER BY bm25(tracks_fts)
        ''', (query,))
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def get_tracks(
        self,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        genre: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()

        query = '''
            SELECT
                t.id,
                t.plex_key,
                t.title,
                t.duration_ms,
                t.genre,
                t.year,
                t.rating,
                t.play_count,
                t.tags,
                a.name as artist_name,
                al.title as album_title,
                CASE WHEN e.id IS NOT NULL THEN 1 ELSE 0 END as has_embedding
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            LEFT JOIN embeddings e ON t.id = e.track_id
            WHERE 1=1
        '''
        params = []

        if search:
            query += " AND (t.title LIKE ? OR a.name LIKE ? OR al.title LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        if genre:
            query += " AND t.genre LIKE ?"
            params.append(f"%{genre}%")

        if year_min is not None:
            query += " AND t.year >= ?"
            params.append(year_min)

        if year_max is not None:
            query += " AND t.year <= ?"
            params.append(year_max)

        query += " ORDER BY t.title LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def count_tracks(
        self,
        search: Optional[str] = None,
        genre: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None
    ) -> int:
        cursor = self.get_connection().cursor()

        query = '''
            SELECT COUNT(*) as count
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            WHERE 1=1
        '''
        params = []

        if search:
            query += " AND (t.title LIKE ? OR a.name LIKE ? OR al.title LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        if genre:
            query += " AND t.genre LIKE ?"
            params.append(f"%{genre}%")

        if year_min is not None:
            query += " AND t.year >= ?"
            params.append(year_min)

        if year_max is not None:
            query += " AND t.year <= ?"
            params.append(year_max)

        cursor.execute(query, params)
        result = cursor.fetchone()
        return result['count'] if result else 0

    def count_tracks_with_embeddings(self) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT COUNT(DISTINCT e.track_id) as count
            FROM embeddings e
        ''')
        result = cursor.fetchone()
        return result['count'] if result else 0

    def get_last_sync_time(self) -> Optional[str]:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT MAX(sync_date) as last_sync
            FROM sync_history
            WHERE status = 'success'
        ''')
        result = cursor.fetchone()
        return result['last_sync'] if result and result['last_sync'] else None

    def count_untagged_tracks(self) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT COUNT(*) as count
            FROM tracks
            WHERE tags IS NULL OR tags = ''
        ''')
        result = cursor.fetchone()
        return result['count'] if result else 0

    def get_tracks_by_filter(
        self,
        genre: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        artist: Optional[str] = None,
        has_no_tags: bool = False,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()

        query = '''
            SELECT
                t.id,
                t.title,
                t.genre,
                t.year,
                t.tags,
                t.environments,
                t.instruments,
                a.name as artist,
                al.title as album
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            WHERE 1=1
        '''
        params = []

        if genre:
            query += " AND t.genre LIKE ?"
            params.append(f"%{genre}%")

        if year_min is not None:
            query += " AND t.year >= ?"
            params.append(year_min)

        if year_max is not None:
            query += " AND t.year <= ?"
            params.append(year_max)

        if artist:
            query += " AND a.name LIKE ?"
            params.append(f"%{artist}%")

        if has_no_tags:
            query += " AND (t.tags IS NULL OR t.tags = '')"

        query += " ORDER BY t.id"

        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def update_track_tags(
        self,
        track_id: int,
        tags: Optional[str] = None,
        environments: Optional[str] = None,
        instruments: Optional[str] = None
    ) -> None:
        cursor = self.get_connection().cursor()
        updates = []
        params = []

        if tags is not None:
            updates.append("tags = ?")
            params.append(tags)

        if environments is not None:
            updates.append("environments = ?")
            params.append(environments)

        if instruments is not None:
            updates.append("instruments = ?")
            params.append(instruments)

        if not updates:
            return

        params.append(track_id)
        query = f"UPDATE tracks SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        self.get_connection().commit()

    def get_recently_tagged_tracks(self, limit: int = 100) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()
        cursor.execute('''
            SELECT
                t.id,
                t.title,
                t.tags,
                t.environments,
                t.instruments,
                a.name as artist,
                al.title as album
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            WHERE t.tags IS NOT NULL AND t.tags != ''
            ORDER BY t.id DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]
