import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional, List, Dict, Any, Tuple
import logging

from .models import Artist, Album, Track, Genre, Embedding, SyncHistory, Playlist, PlaylistTemplate

logger = logging.getLogger(__name__)


def _utc_seconds(moment: datetime) -> str:
    """``moment`` in UTC as ``YYYY-MM-DD HH:MM:SS``, the form of ``CURRENT_TIMESTAMP``.

    A time without a zone is taken as UTC, the way the models write their times.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


class SQLiteManager:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None
        self._defer_commits: bool = False

    def __enter__(self) -> "SQLiteManager":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def connect(self) -> None:
        db_existed = self.db_path.exists()
        # check_same_thread=False: UI background handlers create the connection on
        # the asyncio event-loop thread and then run their DB work inside
        # loop.run_in_executor on a worker thread. Access is a strictly sequential
        # handoff (the coroutine awaits the executor, so the two threads never touch
        # the connection concurrently), which is exactly the case this flag is for.
        # Without it, the first cursor call on the worker thread raised
        # sqlite3.ProgrammingError and every UI sync/embedding/tagging/doctor job
        # crashed. busy_timeout below still guards against lock contention.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
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
                logger.warning(
                    f"Database exists but is empty. Initializing schema at {self.db_path}"
                )
            else:
                logger.warning(f"Database did not exist. Creating new database at {self.db_path}")
            self.create_tables()
        else:
            logger.info(f"Connected to database at {self.db_path}")
            # Order matters. Create the FTS table first (migration 6 backfills it),
            # then run migrations to bring a legacy schema up to date (rename/add
            # columns, create audio_features, dedupe embeddings), and only then
            # create indexes. The index list references columns and tables that the
            # migrations add, so building indexes first raised OperationalError/
            # IntegrityError on any database created by an older release — before the
            # very migrations meant to upgrade it could run.
            self._create_fts_table(cursor)
            self._run_migrations(cursor)
            self._create_indexes(cursor)
            self._commit()

    def get_connection(self) -> sqlite3.Connection:
        if not self.conn:
            self.connect()
        return self.conn  # type: ignore

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
            self._defer_commits = False
            logger.info("Database connection closed")

    def _commit(self) -> None:
        """Commit unless inside a deferred_commits() block."""
        if not self._defer_commits:
            self.get_connection().commit()

    @contextmanager
    def deferred_commits(self) -> Generator[None, None, None]:
        """Batch multiple writes into a single transaction.

        Individual insert/update methods call commit() per-row by default.
        Inside this context manager those commits become no-ops; a single
        commit is issued when the block exits (or rollback on error).
        """
        conn = self.get_connection()
        conn.execute("BEGIN")
        self._defer_commits = True
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._defer_commits = False

    def create_tables(self) -> None:
        cursor = self.get_connection().cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plex_key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                genre TEXT,
                bio TEXT
            )
        """
        )

        cursor.execute(
            """
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
        """
        )

        cursor.execute(
            """
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
                tags_generated_at TIMESTAMP,
                FOREIGN KEY (artist_id) REFERENCES artists(id) ON DELETE CASCADE,
                FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS genres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS track_genres (
                track_id INTEGER NOT NULL,
                genre_id INTEGER NOT NULL,
                PRIMARY KEY (track_id, genre_id),
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE,
                FOREIGN KEY (genre_id) REFERENCES genres(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            """
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
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tracks_added INTEGER DEFAULT 0,
                tracks_updated INTEGER DEFAULT 0,
                tracks_removed INTEGER DEFAULT 0,
                status TEXT NOT NULL,
                error_message TEXT
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plex_key TEXT,
                name TEXT NOT NULL,
                description TEXT,
                created_by_ai INTEGER DEFAULT 0,
                mood_query TEXT,
                generation_config TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                playlist_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (playlist_id, track_id, position),
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS audio_features (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL UNIQUE,
                tempo REAL,
                tempo_confidence REAL,
                key TEXT,
                scale TEXT,
                key_confidence REAL,
                loudness REAL,
                energy REAL,
                energy_level TEXT,
                danceability REAL,
                spectral_centroid REAL,
                mfcc TEXT,
                zero_crossing_rate REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS playlist_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mood_query TEXT DEFAULT '',
                max_tracks INTEGER DEFAULT 50,
                genre_filter TEXT DEFAULT '',
                year_min INTEGER,
                year_max INTEGER,
                tempo_min REAL,
                tempo_max REAL,
                energy_level TEXT DEFAULT '',
                key_filter TEXT DEFAULT '',
                danceability_min REAL,
                shuffle_mode TEXT DEFAULT 'similarity',
                candidate_pool_multiplier INTEGER DEFAULT 25,
                is_preset INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        self._create_indexes(cursor)
        self._create_fts_table(cursor)
        self._run_migrations(cursor)
        self._commit()
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
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_features_track ON audio_features(track_id)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_file_path ON tracks(file_path)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_tags ON tracks(tags)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_environments ON tracks(environments)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_instruments ON tracks(instruments)",
        ]
        for index_sql in indexes:
            cursor.execute(index_sql)
        logger.debug("Database indexes created")

    def _create_fts_table(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
                title,
                artist_name,
                album_title,
                genres,
                track_id UNINDEXED
            )
        """
        )

        cursor.execute(
            """
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
        """
        )

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS tracks_fts_update AFTER UPDATE ON tracks
            BEGIN
                UPDATE tracks_fts
                SET title = NEW.title,
                    artist_name = (SELECT name FROM artists WHERE id = NEW.artist_id),
                    album_title = (SELECT title FROM albums WHERE id = NEW.album_id),
                    genres = NEW.genre
                WHERE track_id = NEW.id;
            END
        """
        )

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS tracks_fts_delete AFTER DELETE ON tracks
            BEGIN
                DELETE FROM tracks_fts WHERE track_id = OLD.id;
            END
        """
        )
        logger.debug("FTS5 table and triggers created")

    def _get_applied_versions(self, cursor: sqlite3.Cursor) -> set:
        """Get the set of already-applied migration versions."""
        try:
            cursor.execute("SELECT version FROM schema_version")
            return {row[0] for row in cursor.fetchall()}
        except sqlite3.OperationalError:
            return set()

    def _record_version(self, cursor: sqlite3.Cursor, version: int) -> None:
        """Record that a migration version has been applied."""
        cursor.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (version,))

    def _run_migrations(self, cursor: sqlite3.Cursor) -> None:
        # Ensure the version-tracking table exists before recording any migration.
        # A database from a release predating the migration system has no
        # schema_version table; _record_version would otherwise raise "no such
        # table: schema_version" now that migrations run before index creation.
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        applied = self._get_applied_versions(cursor)
        migrations_run = False

        # Migration 1: Rename environment → environments column
        if 1 not in applied:
            cursor.execute("PRAGMA table_info(tracks)")
            columns = {col[1] for col in cursor.fetchall()}
            if "environment" in columns and "environments" not in columns:
                logger.info("Migration 1: Renaming environment to environments")
                cursor.execute("ALTER TABLE tracks RENAME COLUMN environment TO environments")
            elif "environments" not in columns:
                logger.info("Migration 1: Adding environments column to tracks")
                cursor.execute("ALTER TABLE tracks ADD COLUMN environments TEXT")
            self._record_version(cursor, 1)
            migrations_run = True

        # Migration 2: Rename primary_instrument → instruments column
        if 2 not in applied:
            cursor.execute("PRAGMA table_info(tracks)")
            columns = {col[1] for col in cursor.fetchall()}
            if "primary_instrument" in columns and "instruments" not in columns:
                logger.info("Migration 2: Renaming primary_instrument to instruments")
                cursor.execute("ALTER TABLE tracks RENAME COLUMN primary_instrument TO instruments")
            elif "instruments" not in columns:
                logger.info("Migration 2: Adding instruments column to tracks")
                cursor.execute("ALTER TABLE tracks ADD COLUMN instruments TEXT")
            self._record_version(cursor, 2)
            migrations_run = True

        # Migration 3: Add unique index on embeddings(track_id, embedding_model)
        if 3 not in applied:
            logger.info("Migration 3: Unique index on embeddings(track_id, embedding_model)")
            cursor.execute(
                """
                DELETE FROM embeddings WHERE id NOT IN (
                    SELECT MAX(id) FROM embeddings GROUP BY track_id, embedding_model
                )
            """
            )
            cursor.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_track_model "
                "ON embeddings(track_id, embedding_model)"
            )
            self._record_version(cursor, 3)
            migrations_run = True

        # Migration 4: Fix sync_history status values: 'completed' → 'success'
        if 4 not in applied:
            cursor.execute("UPDATE sync_history SET status = 'success' WHERE status = 'completed'")
            if cursor.rowcount > 0:
                logger.info(
                    "Migration 4: Fixed %d sync records 'completed' → 'success'",
                    cursor.rowcount,
                )
            self._record_version(cursor, 4)
            migrations_run = True

        # Migration 5: Create audio_features table
        if 5 not in applied:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='audio_features'"
            )
            if cursor.fetchone() is None:
                logger.info("Migration 5: Creating audio_features table")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audio_features (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        track_id INTEGER NOT NULL UNIQUE,
                        tempo REAL,
                        tempo_confidence REAL,
                        key TEXT,
                        scale TEXT,
                        key_confidence REAL,
                        loudness REAL,
                        energy REAL,
                        energy_level TEXT,
                        danceability REAL,
                        spectral_centroid REAL,
                        mfcc TEXT,
                        zero_crossing_rate REAL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (track_id) REFERENCES tracks(id) ON DELETE CASCADE
                    )
                """
                )
                cursor.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_features_track "
                    "ON audio_features(track_id)"
                )
            self._record_version(cursor, 5)
            migrations_run = True

        # Migration 6: Backfill FTS table if it exists but is empty
        if 6 not in applied:
            cursor.execute("SELECT COUNT(*) FROM tracks")
            track_count = cursor.fetchone()[0]
            if track_count > 0:
                cursor.execute("SELECT COUNT(*) FROM tracks_fts")
                fts_count = cursor.fetchone()[0]
                if fts_count == 0:
                    logger.info("Migration 6: Backfilling FTS index")
                    cursor.execute(
                        """
                        INSERT INTO tracks_fts(title, artist_name, album_title, genres, track_id)
                        SELECT
                            t.title,
                            (SELECT name FROM artists WHERE id = t.artist_id),
                            (SELECT title FROM albums WHERE id = t.album_id),
                            t.genre,
                            t.id
                        FROM tracks t
                    """
                    )
            self._record_version(cursor, 6)
            migrations_run = True

        # Migration 7: Create playlist_templates table
        if 7 not in applied:
            cursor.execute(
                "SELECT name FROM sqlite_master " "WHERE type='table' AND name='playlist_templates'"
            )
            if cursor.fetchone() is None:
                logger.info("Migration 7: Creating playlist_templates table")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS playlist_templates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        mood_query TEXT DEFAULT '',
                        max_tracks INTEGER DEFAULT 50,
                        genre_filter TEXT DEFAULT '',
                        year_min INTEGER,
                        year_max INTEGER,
                        tempo_min REAL,
                        tempo_max REAL,
                        energy_level TEXT DEFAULT '',
                        key_filter TEXT DEFAULT '',
                        danceability_min REAL,
                        shuffle_mode TEXT DEFAULT 'similarity',
                        candidate_pool_multiplier INTEGER DEFAULT 25,
                        is_preset INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """
                )
            self._record_version(cursor, 7)
            migrations_run = True

        # Migration 8: Add tags_generated_at column to tracks
        if 8 not in applied:
            cursor.execute("PRAGMA table_info(tracks)")
            columns = {col[1] for col in cursor.fetchall()}
            if "tags_generated_at" not in columns:
                logger.info("Migration 8: Adding tags_generated_at column to tracks")
                cursor.execute("ALTER TABLE tracks ADD COLUMN tags_generated_at TIMESTAMP")
                # Backfill: mark existing tagged tracks as "generated now" so they
                # aren't immediately considered stale.
                cursor.execute(
                    "UPDATE tracks SET tags_generated_at = CURRENT_TIMESTAMP "
                    "WHERE tags IS NOT NULL AND tags != ''"
                )
            self._record_version(cursor, 8)
            migrations_run = True

        # Migration 9: Add generation_config column to playlists
        if 9 not in applied:
            cursor.execute("PRAGMA table_info(playlists)")
            columns = {col[1] for col in cursor.fetchall()}
            if "generation_config" not in columns:
                logger.info("Migration 9: Adding generation_config column to playlists")
                cursor.execute("ALTER TABLE playlists ADD COLUMN generation_config TEXT")
            self._record_version(cursor, 9)
            migrations_run = True

        # Migration 10: MusicBrainz integration columns + cache table
        if 10 not in applied:
            logger.info("Migration 10: Adding MusicBrainz columns and cache table")

            # Add MB columns to tracks
            cursor.execute("PRAGMA table_info(tracks)")
            track_cols = {col[1] for col in cursor.fetchall()}
            for col, ctype in [
                ("musicbrainz_recording_id", "TEXT"),
                ("musicbrainz_genres", "TEXT"),
                ("recording_type", "TEXT"),
                ("musicbrainz_enriched_at", "TIMESTAMP"),
            ]:
                if col not in track_cols:
                    cursor.execute(f"ALTER TABLE tracks ADD COLUMN {col} {ctype}")

            # Add MB column to artists
            cursor.execute("PRAGMA table_info(artists)")
            artist_cols = {col[1] for col in cursor.fetchall()}
            if "musicbrainz_id" not in artist_cols:
                cursor.execute("ALTER TABLE artists ADD COLUMN musicbrainz_id TEXT")

            # Add MB column to albums
            cursor.execute("PRAGMA table_info(albums)")
            album_cols = {col[1] for col in cursor.fetchall()}
            if "musicbrainz_release_group_id" not in album_cols:
                cursor.execute("ALTER TABLE albums ADD COLUMN musicbrainz_release_group_id TEXT")

            # Create musicbrainz_cache table
            cursor.execute(
                "SELECT name FROM sqlite_master " "WHERE type='table' AND name='musicbrainz_cache'"
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    CREATE TABLE musicbrainz_cache (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        lookup_key TEXT NOT NULL,
                        entity_type TEXT NOT NULL,
                        mbid TEXT,
                        response_json TEXT,
                        confidence REAL,
                        expires_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mb_cache_lookup "
                    "ON musicbrainz_cache(lookup_key, entity_type)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_mb_cache_expires "
                    "ON musicbrainz_cache(expires_at)"
                )

            # Indexes on MB columns
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tracks_mb_recording "
                "ON tracks(musicbrainz_recording_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_artists_mb_id " "ON artists(musicbrainz_id)"
            )

            self._record_version(cursor, 10)
            migrations_run = True

        if migrations_run:
            self._commit()
            logger.info("Database migrations completed (applied up to version 9)")

    def insert_artist(self, artist: Artist) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            INSERT INTO artists (plex_key, name, genre, bio)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(plex_key) DO UPDATE SET
                name = excluded.name,
                genre = excluded.genre,
                bio = excluded.bio
        """,
            (artist.plex_key, artist.name, artist.genre, artist.bio),
        )
        self._commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute("SELECT id FROM artists WHERE plex_key = ?", (artist.plex_key,))
        row = cursor.fetchone()
        result: int = row["id"] if row else (cursor.lastrowid or 0)
        return result

    def get_artist_by_id(self, artist_id: int) -> Optional[Artist]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
        row = cursor.fetchone()
        if row:
            return Artist(**dict(row))
        return None

    def get_artist_by_plex_key(self, plex_key: str) -> Optional[Artist]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM artists WHERE plex_key = ?", (plex_key,))
        row = cursor.fetchone()
        if row:
            return Artist(**dict(row))
        return None

    def insert_album(self, album: Album) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            INSERT INTO albums (plex_key, title, artist_id, year, genre, cover_art_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(plex_key) DO UPDATE SET
                title = excluded.title,
                artist_id = excluded.artist_id,
                year = excluded.year,
                genre = excluded.genre,
                cover_art_url = excluded.cover_art_url
        """,
            (
                album.plex_key,
                album.title,
                album.artist_id,
                album.year,
                album.genre,
                album.cover_art_url,
            ),
        )
        self._commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute("SELECT id FROM albums WHERE plex_key = ?", (album.plex_key,))
        row = cursor.fetchone()
        result: int = row["id"] if row else (cursor.lastrowid or 0)
        return result

    def get_album_by_id(self, album_id: int) -> Optional[Album]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM albums WHERE id = ?", (album_id,))
        row = cursor.fetchone()
        if row:
            return Album(**dict(row))
        return None

    def get_album_by_plex_key(self, plex_key: str) -> Optional[Album]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM albums WHERE plex_key = ?", (plex_key,))
        row = cursor.fetchone()
        if row:
            return Album(**dict(row))
        return None

    def insert_track(self, track: Track) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
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
        """,
            (
                track.plex_key,
                track.title,
                track.artist_id,
                track.album_id,
                track.duration_ms,
                track.genre,
                track.year,
                track.rating,
                track.play_count,
                track.last_played,
                track.file_path,
                track.tags,
                track.environments,
                track.instruments,
            ),
        )
        self._commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute("SELECT id FROM tracks WHERE plex_key = ?", (track.plex_key,))
        row = cursor.fetchone()
        result: int = row["id"] if row else (cursor.lastrowid or 0)
        return result

    def get_track_by_id(self, track_id: int) -> Optional[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM tracks WHERE id = ?", (track_id,))
        row = cursor.fetchone()
        if row:
            return Track(**dict(row))
        return None

    def get_track_by_plex_key(self, plex_key: str) -> Optional[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM tracks WHERE plex_key = ?", (plex_key,))
        row = cursor.fetchone()
        if row:
            return Track(**dict(row))
        return None

    def get_track_by_file_path(self, file_path: str) -> Optional[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM tracks WHERE file_path = ?", (file_path,))
        row = cursor.fetchone()
        if row:
            return Track(**dict(row))
        return None

    def get_all_artists(self) -> List[Artist]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM artists")
        return [Artist(**dict(row)) for row in cursor.fetchall()]

    def get_all_albums(self) -> List[Album]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM albums")
        return [Album(**dict(row)) for row in cursor.fetchall()]

    def get_all_tracks(self) -> List[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM tracks")
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def get_tracks_by_ids(self, track_ids: List[int]) -> Dict[int, Track]:
        """Bulk fetch tracks by IDs. Returns dict mapping id -> Track."""
        if not track_ids:
            return {}
        cursor = self.get_connection().cursor()
        placeholders = ",".join("?" * len(track_ids))
        cursor.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})", track_ids)
        return {row["id"]: Track(**dict(row)) for row in cursor.fetchall()}

    def get_artists_by_ids(self, artist_ids: List[int]) -> Dict[int, Artist]:
        """Bulk fetch artists by IDs. Returns dict mapping id -> Artist."""
        if not artist_ids:
            return {}
        cursor = self.get_connection().cursor()
        placeholders = ",".join("?" * len(artist_ids))
        cursor.execute(f"SELECT * FROM artists WHERE id IN ({placeholders})", artist_ids)
        return {row["id"]: Artist(**dict(row)) for row in cursor.fetchall()}

    def get_albums_by_ids(self, album_ids: List[int]) -> Dict[int, Album]:
        """Bulk fetch albums by IDs. Returns dict mapping id -> Album."""
        if not album_ids:
            return {}
        cursor = self.get_connection().cursor()
        placeholders = ",".join("?" * len(album_ids))
        cursor.execute(f"SELECT * FROM albums WHERE id IN ({placeholders})", album_ids)
        return {row["id"]: Album(**dict(row)) for row in cursor.fetchall()}

    def get_track_details_by_ids(self, track_ids: List[int]) -> List[Dict[str, Any]]:
        """Bulk fetch tracks with artist/album info. Returns list of dicts.

        Chunks the ``IN (...)`` list so a large candidate pool can't exceed
        SQLite's bound-parameter limit (999 on older builds).
        """
        if not track_ids:
            return []
        cursor = self.get_connection().cursor()
        base_query = """
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
                t.file_path,
                t.artist_id,
                t.album_id,
                a.name as artist_name,
                a.musicbrainz_id as artist_mbid,
                al.title as album_title
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            WHERE t.id IN ({placeholders})
        """
        results: List[Dict[str, Any]] = []
        chunk_size = 900
        for i in range(0, len(track_ids), chunk_size):
            batch = track_ids[i : i + chunk_size]
            placeholders = ",".join("?" * len(batch))
            cursor.execute(base_query.format(placeholders=placeholders), batch)
            results.extend(dict(row) for row in cursor.fetchall())
        return results

    def delete_track(self, track_id: int) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        self._commit()

    def insert_genre(self, genre: Genre) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (genre.name,))
        self._commit()
        return cursor.lastrowid or 0

    def get_genre_by_name(self, name: str) -> Optional[Genre]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM genres WHERE name = ?", (name.lower(),))
        row = cursor.fetchone()
        if row:
            return Genre(**dict(row))
        return None

    def get_all_genres(self) -> List[Genre]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM genres")
        return [Genre(**dict(row)) for row in cursor.fetchall()]

    def insert_embedding(self, embedding: Embedding) -> int:
        cursor = self.get_connection().cursor()
        vector_json = json.dumps(embedding.vector)
        cursor.execute(
            """
            INSERT INTO embeddings (track_id, embedding_model, embedding_dim, vector, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id, embedding_model) DO UPDATE SET
                embedding_dim = excluded.embedding_dim,
                vector = excluded.vector,
                updated_at = excluded.updated_at
        """,
            (
                embedding.track_id,
                embedding.embedding_model,
                embedding.embedding_dim,
                vector_json,
                embedding.created_at,
                embedding.updated_at,
            ),
        )
        self._commit()
        # Return the existing id if updated, or new id if inserted
        cursor.execute(
            "SELECT id FROM embeddings WHERE track_id = ? AND embedding_model = ?",
            (embedding.track_id, embedding.embedding_model),
        )
        row = cursor.fetchone()
        result: int = row["id"] if row else (cursor.lastrowid or 0)
        return result

    def insert_embeddings_batch(self, embeddings: List[Embedding]) -> None:
        if not embeddings:
            return
        conn = self.get_connection()
        data = [
            (
                e.track_id,
                e.embedding_model,
                e.embedding_dim,
                json.dumps(e.vector),
                e.created_at,
                e.updated_at,
            )
            for e in embeddings
        ]
        try:
            conn.execute("BEGIN")
            conn.executemany(
                """
                INSERT INTO embeddings (track_id, embedding_model, embedding_dim, vector, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(track_id, embedding_model) DO UPDATE SET
                    embedding_dim = excluded.embedding_dim,
                    vector = excluded.vector,
                    updated_at = excluded.updated_at
            """,
                data,
            )
            self._commit()
        except Exception:
            conn.rollback()
            raise

    def get_embedding_by_track_id(self, track_id: int) -> Optional[Embedding]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM embeddings WHERE track_id = ?", (track_id,))
        row = cursor.fetchone()
        if row:
            data = dict(row)
            data["vector"] = json.loads(data["vector"])
            return Embedding(**data)
        return None

    def get_track_ids_with_embeddings(self, embedding_model: Optional[str] = None) -> set:
        """Track ids that already have an embedding.

        Pass *embedding_model* to scope to a single model so a provider/model
        switch correctly re-embeds tracks that were only embedded by the old
        model (rows for other models don't count as "already embedded").
        """
        cursor = self.get_connection().cursor()
        if embedding_model is not None:
            cursor.execute(
                "SELECT track_id FROM embeddings WHERE embedding_model = ?", (embedding_model,)
            )
        else:
            cursor.execute("SELECT track_id FROM embeddings")
        return {row["track_id"] for row in cursor.fetchall()}

    def get_all_embeddings(
        self, embedding_model: Optional[str] = None
    ) -> List[Tuple[int, List[float]]]:
        """All (track_id, vector) pairs, optionally scoped to one model.

        FAISS rebuilds must pass *embedding_model* so the index is built from a
        single model's vectors — mixing models yields ragged dimensions that crash
        the index build.
        """
        cursor = self.get_connection().cursor()
        if embedding_model is not None:
            cursor.execute(
                "SELECT track_id, vector FROM embeddings WHERE embedding_model = ?",
                (embedding_model,),
            )
        else:
            cursor.execute("SELECT track_id, vector FROM embeddings")
        return [(row["track_id"], json.loads(row["vector"])) for row in cursor.fetchall()]

    def insert_sync_record(self, sync: SyncHistory) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            INSERT INTO sync_history (sync_date, tracks_added, tracks_updated, tracks_removed, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                sync.sync_date,
                sync.tracks_added,
                sync.tracks_updated,
                sync.tracks_removed,
                sync.status,
                sync.error_message,
            ),
        )
        self._commit()
        return cursor.lastrowid or 0

    def get_latest_sync(self) -> Optional[SyncHistory]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM sync_history ORDER BY sync_date DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            return SyncHistory(**dict(row))
        return None

    # ---- Audio features CRUD ----

    def insert_audio_features(self, track_id: int, features: dict) -> int:
        cursor = self.get_connection().cursor()
        mfcc_json = json.dumps(features.get("mfcc")) if features.get("mfcc") else None
        cursor.execute(
            """
            INSERT INTO audio_features (
                track_id, tempo, tempo_confidence, key, scale, key_confidence,
                loudness, energy, energy_level, danceability,
                spectral_centroid, mfcc, zero_crossing_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(track_id) DO UPDATE SET
                tempo = excluded.tempo,
                tempo_confidence = excluded.tempo_confidence,
                key = excluded.key,
                scale = excluded.scale,
                key_confidence = excluded.key_confidence,
                loudness = excluded.loudness,
                energy = excluded.energy,
                energy_level = excluded.energy_level,
                danceability = excluded.danceability,
                spectral_centroid = excluded.spectral_centroid,
                mfcc = excluded.mfcc,
                zero_crossing_rate = excluded.zero_crossing_rate,
                updated_at = CURRENT_TIMESTAMP
        """,
            (
                track_id,
                features.get("tempo"),
                features.get("tempo_confidence"),
                features.get("key"),
                features.get("scale"),
                features.get("key_confidence"),
                features.get("loudness"),
                features.get("energy"),
                features.get("energy_level"),
                features.get("danceability"),
                features.get("spectral_centroid"),
                mfcc_json,
                features.get("zero_crossing_rate"),
            ),
        )
        self._commit()
        return cursor.lastrowid or 0

    def get_audio_features(self, track_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM audio_features WHERE track_id = ?", (track_id,))
        row = cursor.fetchone()
        if row:
            d = dict(row)
            if d.get("mfcc"):
                d["mfcc"] = json.loads(d["mfcc"])
            return d
        return None

    def get_tracks_without_audio_features(self) -> List[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT t.* FROM tracks t
            LEFT JOIN audio_features af ON t.id = af.track_id
            WHERE af.id IS NULL AND t.file_path IS NOT NULL
        """
        )
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def get_audio_features_by_track_ids(self, track_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        if not track_ids:
            return {}
        cursor = self.get_connection().cursor()
        placeholders = ",".join("?" * len(track_ids))
        cursor.execute(
            f"SELECT * FROM audio_features WHERE track_id IN ({placeholders})",
            track_ids,
        )
        result = {}
        for row in cursor.fetchall():
            d = dict(row)
            if d.get("mfcc"):
                d["mfcc"] = json.loads(d["mfcc"])
            result[d["track_id"]] = d
        return result

    def get_audio_features_count(self) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT COUNT(*) FROM audio_features")
        row = cursor.fetchone()
        count: int = row[0] if row else 0
        return count

    # ── MusicBrainz helpers ─────────────────────────────────────────

    def get_tracks_without_musicbrainz(self) -> List[Track]:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT * FROM tracks WHERE musicbrainz_recording_id IS NULL")
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def update_track_musicbrainz(
        self,
        track_id: int,
        recording_id: Optional[str] = None,
        genres: Optional[str] = None,
        recording_type: Optional[str] = None,
    ) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            UPDATE tracks
            SET musicbrainz_recording_id = ?,
                musicbrainz_genres = ?,
                recording_type = ?,
                musicbrainz_enriched_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """,
            (recording_id, genres, recording_type, track_id),
        )
        self._commit()

    def update_artist_musicbrainz_id(self, artist_id: int, mbid: str) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute(
            "UPDATE artists SET musicbrainz_id = ? WHERE id = ?",
            (mbid, artist_id),
        )
        self._commit()

    def update_album_musicbrainz_id(self, album_id: int, release_group_id: str) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute(
            "UPDATE albums SET musicbrainz_release_group_id = ? WHERE id = ?",
            (release_group_id, album_id),
        )
        self._commit()

    def get_musicbrainz_cache(self, lookup_key: str, entity_type: str) -> Optional[Dict[str, Any]]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT * FROM musicbrainz_cache
            WHERE lookup_key = ? AND entity_type = ?
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
            ORDER BY created_at DESC LIMIT 1
        """,
            (lookup_key, entity_type),
        )
        row = cursor.fetchone()
        if row:
            d = dict(row)
            if d.get("response_json"):
                d["response_json"] = json.loads(d["response_json"])
            return d
        return None

    def set_musicbrainz_cache(
        self,
        lookup_key: str,
        entity_type: str,
        mbid: Optional[str],
        response_json: Optional[dict],
        confidence: float,
        ttl_days: int = 90,
    ) -> None:
        cursor = self.get_connection().cursor()
        resp_str = json.dumps(response_json) if response_json else None
        cursor.execute(
            """
            INSERT INTO musicbrainz_cache
                (lookup_key, entity_type, mbid, response_json, confidence, expires_at)
            VALUES (?, ?, ?, ?, ?, datetime('now', '+' || ? || ' days'))
        """,
            (lookup_key, entity_type, mbid, resp_str, confidence, ttl_days),
        )
        self._commit()

    def get_musicbrainz_enrichment_count(self) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute("SELECT COUNT(*) FROM tracks WHERE musicbrainz_recording_id IS NOT NULL")
        row = cursor.fetchone()
        count: int = row[0] if row else 0
        return count

    def clear_expired_musicbrainz_cache(self) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute("DELETE FROM musicbrainz_cache WHERE expires_at < CURRENT_TIMESTAMP")
        deleted: int = cursor.rowcount
        self._commit()
        return deleted

    def insert_playlist(self, playlist: Playlist) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            INSERT INTO playlists (plex_key, name, description, created_by_ai, mood_query,
                                   generation_config, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                playlist.plex_key,
                playlist.name,
                playlist.description,
                int(playlist.created_by_ai),
                playlist.mood_query,
                playlist.generation_config,
                playlist.created_at,
            ),
        )
        self._commit()
        return cursor.lastrowid or 0

    def add_track_to_playlist(self, playlist_id: int, track_id: int, position: int) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            INSERT INTO playlist_tracks (playlist_id, track_id, position)
            VALUES (?, ?, ?)
        """,
            (playlist_id, track_id, position),
        )
        self._commit()

    def add_tracks_to_playlist(self, playlist_id: int, track_ids: List[int]) -> None:
        if not track_ids:
            return
        conn = self.get_connection()
        data = [(playlist_id, track_id, pos) for pos, track_id in enumerate(track_ids)]
        try:
            conn.execute("BEGIN")
            conn.executemany(
                """
                INSERT INTO playlist_tracks (playlist_id, track_id, position)
                VALUES (?, ?, ?)
            """,
                data,
            )
            self._commit()
        except Exception:
            conn.rollback()
            raise

    def get_playlists(self) -> List[Playlist]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT
                p.id, p.plex_key, p.name, p.description, p.created_by_ai,
                p.mood_query, p.generation_config, p.created_at,
                COUNT(pt.track_id) as track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON p.id = pt.playlist_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """
        )
        return [Playlist(**dict(row)) for row in cursor.fetchall()]

    def get_playlist_by_id(self, playlist_id: int) -> Optional[Playlist]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT id, plex_key, name, description, created_by_ai, mood_query,
                   generation_config, created_at
            FROM playlists
            WHERE id = ?
        """,
            (playlist_id,),
        )
        row = cursor.fetchone()
        return Playlist(**dict(row)) if row else None

    def get_playlist_tracks(self, playlist_id: int) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT
                t.id,
                t.plex_key,
                t.title,
                t.duration_ms,
                t.genre,
                t.year,
                t.file_path,
                a.name as artist_name,
                al.title as album_title,
                pt.position
            FROM playlist_tracks pt
            JOIN tracks t ON pt.track_id = t.id
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position
        """,
            (playlist_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_playlist_track_ids(self, max_playlists: int = 10) -> set:
        """Return track IDs used in the most recent *max_playlists* playlists."""
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT DISTINCT pt.track_id
            FROM playlist_tracks pt
            JOIN playlists p ON pt.playlist_id = p.id
            WHERE p.id IN (
                SELECT id FROM playlists ORDER BY created_at DESC LIMIT ?
            )
        """,
            (max_playlists,),
        )
        return {row[0] for row in cursor.fetchall()}

    def get_playlist_top_artists(self, limit: int = 3) -> Dict[int, List[str]]:
        """Return each playlist's most frequent artists, most frequent first.

        One grouped read over ``playlist_tracks ⨝ tracks ⨝ artists``, ordered by
        track count (descending) and then by artist name. The top ``limit``
        distinct, non-blank names per playlist are kept in Python. Playlists with
        no tracks are absent from the result. The SQL is static (no interpolated
        values), so there is nothing to bind.

        Returns:
            ``{playlist_id: [artist name, ...]}`` with at most ``limit`` names each;
            ``{}`` when ``limit`` is below 1 or no playlist has tracks.
        """
        if limit < 1:
            return {}
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT pt.playlist_id, a.name, COUNT(*) AS artist_tracks
            FROM playlist_tracks pt
            JOIN tracks t ON t.id = pt.track_id
            JOIN artists a ON a.id = t.artist_id
            GROUP BY pt.playlist_id, a.id
            ORDER BY pt.playlist_id, artist_tracks DESC, a.name COLLATE NOCASE, a.name
        """
        )
        top: Dict[int, List[str]] = {}
        seen: Dict[int, set[str]] = {}
        for row in cursor.fetchall():
            playlist_id = int(row[0])
            name = (row[1] or "").strip()
            if not name:
                continue
            names = top.setdefault(playlist_id, [])
            if len(names) >= limit:
                continue
            seen_names = seen.setdefault(playlist_id, set())
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            names.append(name)
        return top

    def delete_playlist(self, playlist_id: int) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        cursor.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self._commit()

    def update_playlist(
        self, playlist_id: int, name: Optional[str] = None, description: Optional[str] = None
    ) -> None:
        cursor = self.get_connection().cursor()
        updates: List[str] = []
        params: List[Any] = []

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
        self._commit()

    # ── Playlist Templates ─────────────────────────────────────────────

    def insert_template(self, template: PlaylistTemplate) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            INSERT INTO playlist_templates
                (name, mood_query, max_tracks, genre_filter, year_min, year_max,
                 tempo_min, tempo_max, energy_level, key_filter, danceability_min,
                 shuffle_mode, candidate_pool_multiplier, is_preset, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                template.name,
                template.mood_query,
                template.max_tracks,
                template.genre_filter,
                template.year_min,
                template.year_max,
                template.tempo_min,
                template.tempo_max,
                template.energy_level,
                template.key_filter,
                template.danceability_min,
                template.shuffle_mode,
                template.candidate_pool_multiplier,
                int(template.is_preset),
                template.created_at,
            ),
        )
        self._commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_templates(self) -> List[PlaylistTemplate]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT id, name, mood_query, max_tracks, genre_filter, year_min, year_max,
                   tempo_min, tempo_max, energy_level, key_filter, danceability_min,
                   shuffle_mode, candidate_pool_multiplier, is_preset, created_at
            FROM playlist_templates
            ORDER BY is_preset DESC, created_at DESC
        """
        )
        return [PlaylistTemplate(**dict(row)) for row in cursor.fetchall()]

    def get_template_by_id(self, template_id: int) -> Optional[PlaylistTemplate]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT id, name, mood_query, max_tracks, genre_filter, year_min, year_max,
                   tempo_min, tempo_max, energy_level, key_filter, danceability_min,
                   shuffle_mode, candidate_pool_multiplier, is_preset, created_at
            FROM playlist_templates
            WHERE id = ?
        """,
            (template_id,),
        )
        row = cursor.fetchone()
        return PlaylistTemplate(**dict(row)) if row else None

    def delete_template(self, template_id: int) -> None:
        cursor = self.get_connection().cursor()
        cursor.execute("DELETE FROM playlist_templates WHERE id = ?", (template_id,))
        self._commit()

    def search_tracks_fts(self, query: str) -> List[Track]:
        # Escape the raw query into a valid FTS5 MATCH expression. Binding the
        # user's text directly raised OperationalError on common punctuation
        # (apostrophes, hyphens, ampersands, periods), which callers swallow —
        # silently dropping tracks whose titles contain those characters.
        fts_term = self._build_fts_query(query)
        if not fts_term:
            return []
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT t.* FROM tracks t
            JOIN tracks_fts fts ON t.id = fts.track_id
            WHERE tracks_fts MATCH ?
            ORDER BY bm25(tracks_fts)
        """,
            (fts_term,),
        )
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def get_tracks(
        self,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        genre: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        tag: Optional[str] = None,
        has_audio: bool = False,
        sort_column: str = "title",
        sort_ascending: bool = True,
    ) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()

        query = """
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
                EXISTS(SELECT 1 FROM embeddings e WHERE e.track_id = t.id) as has_embedding,
                EXISTS(SELECT 1 FROM audio_features af WHERE af.track_id = t.id)
                    AS has_audio_features,
                (t.musicbrainz_recording_id IS NOT NULL) AS has_musicbrainz
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
        """
        params: list = []

        fts_term = self._build_fts_query(search) if search else None
        if fts_term:
            query += " JOIN tracks_fts fts ON t.id = fts.track_id WHERE tracks_fts MATCH ?"
            params.append(fts_term)
        else:
            query += " WHERE 1=1"

        if genre:
            query += " AND t.genre LIKE ?"
            params.append(f"%{genre}%")

        if year_min is not None:
            query += " AND t.year >= ?"
            params.append(year_min)

        if year_max is not None:
            query += " AND t.year <= ?"
            params.append(year_max)

        if tag:
            query += " AND t.tags LIKE ?"
            params.append(f"%{tag}%")

        if has_audio:
            query += " AND t.id IN (SELECT track_id FROM audio_features)"

        allowed_sort_columns = {
            "title": "t.title",
            "artist": "a.name",
            "album": "al.title",
            "genre": "t.genre",
            "year": "t.year",
        }
        text_sort_columns = {"title", "artist", "album", "genre"}
        sort_key = sort_column if sort_column in allowed_sort_columns else "title"
        order_col = allowed_sort_columns[sort_key]
        order_dir = "ASC" if sort_ascending else "DESC"
        # Names sort case-insensitively (SQLite's default BINARY collation puts
        # "deadmau5" after "ZZ Top"); the raw value and then the id break ties so
        # LIMIT/OFFSET pages are deterministic.
        if sort_key in text_sort_columns:
            order_by = f"{order_col} COLLATE NOCASE {order_dir}, {order_col} {order_dir}"
        else:
            order_by = f"{order_col} {order_dir}"
        query += f" ORDER BY {order_by}, t.id {order_dir} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _build_fts_query(search: str) -> Optional[str]:
        tokens = search.strip().split()
        if not tokens:
            return None
        escaped = ['"' + t.replace('"', '""') + '"' + "*" for t in tokens]
        return " ".join(escaped)

    def count_tracks(
        self,
        search: Optional[str] = None,
        genre: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        tag: Optional[str] = None,
        has_audio: bool = False,
    ) -> int:
        cursor = self.get_connection().cursor()

        query = """
            SELECT COUNT(*) as count
            FROM tracks t
            JOIN artists a ON t.artist_id = a.id
            JOIN albums al ON t.album_id = al.id
        """
        params: list = []

        fts_term = self._build_fts_query(search) if search else None
        if fts_term:
            query += " JOIN tracks_fts fts ON t.id = fts.track_id WHERE tracks_fts MATCH ?"
            params.append(fts_term)
        else:
            query += " WHERE 1=1"

        if genre:
            query += " AND t.genre LIKE ?"
            params.append(f"%{genre}%")

        if year_min is not None:
            query += " AND t.year >= ?"
            params.append(year_min)

        if year_max is not None:
            query += " AND t.year <= ?"
            params.append(year_max)

        if tag:
            query += " AND t.tags LIKE ?"
            params.append(f"%{tag}%")

        if has_audio:
            query += " AND t.id IN (SELECT track_id FROM audio_features)"

        cursor.execute(query, params)
        result = cursor.fetchone()
        return result["count"] if result else 0

    def count_tracks_with_embeddings(self) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT COUNT(DISTINCT e.track_id) as count
            FROM embeddings e
        """
        )
        result = cursor.fetchone()
        return result["count"] if result else 0

    def count_tracks_without_embeddings(self, embedding_model: Optional[str] = None) -> int:
        """Count tracks that have no embedding.

        With no *embedding_model*, a track counts as embedded when it has an
        embedding from any model, so ``count_tracks_with_embeddings() +
        count_tracks_without_embeddings()`` equals the track total. Pass
        *embedding_model* to count tracks missing an embedding from that model.
        """
        cursor = self.get_connection().cursor()
        if embedding_model is not None:
            cursor.execute(
                """
                SELECT COUNT(*) as count
                FROM tracks t
                WHERE NOT EXISTS (
                    SELECT 1 FROM embeddings e
                    WHERE e.track_id = t.id AND e.embedding_model = ?
                )
            """,
                (embedding_model,),
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) as count
                FROM tracks t
                WHERE NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.track_id = t.id)
            """
            )
        result = cursor.fetchone()
        return result["count"] if result else 0

    def count_tracks_with_only_other_embeddings(
        self, embedding_model: Optional[str] = None, embedding_dim: Optional[int] = None
    ) -> int:
        """Count the tracks whose stored embedding is none of one model and one vector size.

        That is a track that holds at least one embedding row, and none of
        ``embedding_model`` whose ``embedding_dim`` is ``embedding_dim``. A search
        index of that model and size cannot hold such a track. None for the model,
        or for the size, leaves that part of the test out. A track without any
        embedding row is not counted, and neither is a row whose track is gone.
        Read-only.
        """
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT COUNT(*) as count
            FROM tracks t
            WHERE EXISTS (SELECT 1 FROM embeddings e WHERE e.track_id = t.id)
              AND NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.track_id = t.id
                  AND (? IS NULL OR e.embedding_model = ?)
                  AND (? IS NULL OR e.embedding_dim = ?)
              )
        """,
            (embedding_model, embedding_model, embedding_dim, embedding_dim),
        )
        result = cursor.fetchone()
        return result["count"] if result else 0

    def get_tracks_without_embeddings(self, embedding_model: Optional[str] = None) -> List[Track]:
        """Tracks that have no embedding (same definition as
        :meth:`count_tracks_without_embeddings`), ordered by id."""
        cursor = self.get_connection().cursor()
        if embedding_model is not None:
            cursor.execute(
                """
                SELECT t.* FROM tracks t
                WHERE NOT EXISTS (
                    SELECT 1 FROM embeddings e
                    WHERE e.track_id = t.id AND e.embedding_model = ?
                )
                ORDER BY t.id
            """,
                (embedding_model,),
            )
        else:
            cursor.execute(
                """
                SELECT t.* FROM tracks t
                WHERE NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.track_id = t.id)
                ORDER BY t.id
            """
            )
        return [Track(**dict(row)) for row in cursor.fetchall()]

    def get_track_ids_to_retag_since(self, since: datetime) -> List[int]:
        """Ids of the tracks whose tags were not written at or after ``since``, by id.

        That is a track without a ``tags_generated_at`` time, or with one that is
        older than ``since`` or cannot be read as a time. Read-only. Times are
        compared in UTC and in whole seconds: ``update_track_tags`` stamps the
        column with ``CURRENT_TIMESTAMP``, which is UTC without fractions.
        """
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT id FROM tracks
            WHERE datetime(tags_generated_at) IS NULL
               OR datetime(tags_generated_at) < datetime(?)
            ORDER BY id
        """,
            (_utc_seconds(since),),
        )
        return [int(row["id"]) for row in cursor.fetchall()]

    def get_track_ids_to_reembed_since(self, embedding_model: str, since: datetime) -> List[int]:
        """Ids of the tracks whose embedding was not written at or after ``since``, by id.

        That is a track with no embedding row for ``embedding_model``, or with a row
        whose ``updated_at`` is older than ``since`` or cannot be read as a time.
        Rows of other models do not count. Read-only. Times are compared in UTC and
        in whole seconds: ``updated_at`` holds the UTC time of the ``Embedding``
        object that was written, with fractions of a second.
        """
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT t.id FROM tracks t
            WHERE NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.track_id = t.id
                  AND e.embedding_model = ?
                  AND datetime(e.updated_at) >= datetime(?)
            )
            ORDER BY t.id
        """,
            (embedding_model, _utc_seconds(since)),
        )
        return [int(row["id"]) for row in cursor.fetchall()]

    def get_tags_for_tracks(self, track_ids: List[int]) -> Dict[int, Dict[str, Optional[str]]]:
        """Read the stored tag columns of several tracks at once.

        Returns ``{track_id: {"tags", "environments", "instruments"}}`` with each
        value exactly as stored (comma-separated string, or ``None``). Ids that
        are not in the database are absent. The ``IN (...)`` list is chunked so
        a large selection never exceeds SQLite's bound-parameter limit.
        """
        ids = list(dict.fromkeys(int(tid) for tid in track_ids))
        if not ids:
            return {}
        cursor = self.get_connection().cursor()
        out: Dict[int, Dict[str, Optional[str]]] = {}
        chunk_size = 900
        for start in range(0, len(ids), chunk_size):
            chunk = ids[start : start + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            cursor.execute(
                f"SELECT id, tags, environments, instruments FROM tracks "
                f"WHERE id IN ({placeholders})",
                chunk,
            )
            for row in cursor.fetchall():
                out[int(row["id"])] = {
                    "tags": row["tags"],
                    "environments": row["environments"],
                    "instruments": row["instruments"],
                }
        return out

    def get_last_sync_time(self) -> Optional[str]:
        """When the library was last synced, or None.

        A ``partial`` sync counts as well as a ``success``: it read Plex and saved
        its changes, and only kept unread tracks or held a removal back. ``failed``
        and ``interrupted`` syncs do not count.
        """
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT MAX(sync_date) as last_sync
            FROM sync_history
            WHERE status IN ('success', 'partial')
        """
        )
        result = cursor.fetchone()
        return result["last_sync"] if result and result["last_sync"] else None

    def count_untagged_tracks(self) -> int:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT COUNT(*) as count
            FROM tracks
            WHERE tags IS NULL OR tags = ''
        """
        )
        result = cursor.fetchone()
        return result["count"] if result else 0

    def count_stale_tagged_tracks(self, days_old: int = 30) -> int:
        """Count tracks whose tags are older than *days_old* days."""
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
            SELECT COUNT(*) as count
            FROM tracks
            WHERE tags IS NOT NULL AND tags != ''
              AND (tags_generated_at IS NULL
                   OR tags_generated_at < datetime('now', ?))
        """,
            (f"-{days_old} days",),
        )
        result = cursor.fetchone()
        return result["count"] if result else 0

    def get_tracks_by_filter(
        self,
        genre: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        artist: Optional[str] = None,
        has_no_tags: bool = False,
        stale_days: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()

        query = """
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
        """
        params: List[Any] = []

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

        if stale_days is not None:
            # Tracks that have tags but those tags are older than stale_days
            query += (
                " AND t.tags IS NOT NULL AND t.tags != ''"
                " AND (t.tags_generated_at IS NULL"
                "      OR t.tags_generated_at < datetime('now', ?))"
            )
            params.append(f"-{stale_days} days")

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
        instruments: Optional[str] = None,
    ) -> None:
        cursor = self.get_connection().cursor()
        updates: List[str] = []
        params: List[Any] = []

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

        # Always record when tags were generated/updated
        updates.append("tags_generated_at = CURRENT_TIMESTAMP")

        params.append(track_id)
        query = f"UPDATE tracks SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        self._commit()

    def get_recently_tagged_tracks(self, limit: int = 100) -> List[Dict[str, Any]]:
        cursor = self.get_connection().cursor()
        cursor.execute(
            """
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
            ORDER BY (t.tags_generated_at IS NULL), t.tags_generated_at DESC, t.id DESC
            LIMIT ?
        """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
