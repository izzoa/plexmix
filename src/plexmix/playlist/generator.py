from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

from ..database.sqlite_manager import SQLiteManager
from ..database.vector_index import VectorIndex
from ..ai.base import AIProvider
from ..utils.embeddings import EmbeddingGenerator
from ..database.models import Playlist

logger = logging.getLogger(__name__)


class PlaylistGenerator:
    def __init__(
        self,
        db_manager: SQLiteManager,
        vector_index: VectorIndex,
        ai_provider: AIProvider,
        embedding_generator: EmbeddingGenerator
    ):
        self.db = db_manager
        self.vector_index = vector_index
        self.ai_provider = ai_provider
        self.embedding_generator = embedding_generator

    def generate(
        self,
        mood_query: str,
        max_tracks: int = 50,
        candidate_pool_size: int = 500,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        logger.info(f"Generating playlist for mood: {mood_query}")

        filtered_track_ids = self._apply_filters(filters) if filters else None

        candidates = self._get_candidates(
            mood_query,
            candidate_pool_size,
            filtered_track_ids
        )

        if not candidates:
            logger.warning("No candidate tracks found")
            return []

        selected_ids = self.ai_provider.generate_playlist(
            mood_query,
            candidates,
            max_tracks
        )

        if not selected_ids:
            logger.warning("AI provider returned no tracks, using top candidates")
            selected_ids = [c['id'] for c in candidates[:max_tracks]]

        seen_tracks = set()
        seen_combinations = set()
        playlist_tracks = []
        for track_id in selected_ids:
            if track_id in seen_tracks:
                continue

            track = self.db.get_track_by_id(track_id)
            if not track:
                continue

            artist = self.db.get_artist_by_id(track.artist_id)
            album = self.db.get_album_by_id(track.album_id)

            track_key = (track.title.lower(), artist.name.lower() if artist else 'unknown')
            if track_key in seen_combinations:
                logger.debug(f"Skipping duplicate: {track.title} by {artist.name if artist else 'Unknown'}")
                continue

            seen_tracks.add(track_id)
            seen_combinations.add(track_key)

            playlist_tracks.append({
                'id': track.id,
                'title': track.title,
                'artist': artist.name if artist else 'Unknown',
                'album': album.title if album else 'Unknown',
                'duration_ms': track.duration_ms,
                'genre': track.genre,
                'year': track.year
            })

        logger.info(f"Generated playlist with {len(playlist_tracks)} tracks")
        return playlist_tracks

    def _apply_filters(self, filters: Dict[str, Any]) -> List[int]:
        cursor = self.db.get_connection().cursor()

        where_clauses = []
        params = []

        if 'genre' in filters:
            where_clauses.append("genre LIKE ?")
            params.append(f"%{filters['genre']}%")

        if 'year' in filters:
            where_clauses.append("year = ?")
            params.append(filters['year'])

        if 'year_min' in filters:
            where_clauses.append("year >= ?")
            params.append(filters['year_min'])

        if 'year_max' in filters:
            where_clauses.append("year <= ?")
            params.append(filters['year_max'])

        if 'environment' in filters:
            where_clauses.append("environments LIKE ?")
            params.append(f"%{filters['environment'].lower()}%")

        if 'instrument' in filters:
            where_clauses.append("instruments LIKE ?")
            params.append(f"%{filters['instrument'].lower()}%")

        if 'rating_min' in filters:
            where_clauses.append("rating >= ?")
            params.append(filters['rating_min'])

        if 'artist' in filters:
            where_clauses.append("artist_id IN (SELECT id FROM artists WHERE name LIKE ?)")
            params.append(f"%{filters['artist']}%")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        query = f"SELECT id FROM tracks WHERE {where_sql}"

        cursor.execute(query, params)
        return [row[0] for row in cursor.fetchall()]

    def _get_candidates(
        self,
        mood_query: str,
        pool_size: int,
        filtered_track_ids: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        query_embedding = self.embedding_generator.generate_embedding(mood_query)

        similar_tracks = self.vector_index.search(
            query_embedding,
            k=pool_size,
            track_id_filter=filtered_track_ids
        )

        candidates = []
        for track_id, similarity_score in similar_tracks:
            track = self.db.get_track_by_id(track_id)
            if not track:
                continue

            artist = self.db.get_artist_by_id(track.artist_id)
            album = self.db.get_album_by_id(track.album_id)

            candidates.append({
                'id': track.id,
                'title': track.title,
                'artist': artist.name if artist else 'Unknown',
                'album': album.title if album else 'Unknown',
                'genre': track.genre or '',
                'year': track.year or '',
                'similarity': similarity_score
            })

        logger.info(f"Retrieved {len(candidates)} candidate tracks")
        return candidates

    def save_playlist(
        self,
        name: str,
        track_ids: List[int],
        mood_query: str,
        description: Optional[str] = None,
        plex_key: Optional[str] = None
    ) -> int:
        playlist = Playlist(
            plex_key=plex_key,
            name=name,
            description=description,
            created_by_ai=True,
            mood_query=mood_query
        )

        playlist_id = self.db.insert_playlist(playlist)

        for position, track_id in enumerate(track_ids):
            self.db.add_track_to_playlist(playlist_id, track_id, position)

        logger.info(f"Saved playlist '{name}' with {len(track_ids)} tracks")
        return playlist_id
