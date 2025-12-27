from typing import List, Dict, Any, Optional, Callable
import logging
from datetime import datetime

from ..database.sqlite_manager import SQLiteManager
from ..database.vector_index import VectorIndex
from ..utils.embeddings import EmbeddingGenerator
from ..database.models import Playlist

logger = logging.getLogger(__name__)


class PlaylistGenerator:
    def __init__(
        self,
        db_manager: SQLiteManager,
        vector_index: VectorIndex,
        embedding_generator: EmbeddingGenerator
    ):
        self.db = db_manager
        self.vector_index = vector_index
        self.embedding_generator = embedding_generator

    def generate(
        self,
        mood_query: str,
        max_tracks: int = 50,
        candidate_pool_size: Optional[int] = None,
        candidate_pool_multiplier: int = 25,
        filters: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> List[Dict[str, Any]]:
        logger.info(f"Generating playlist for mood: {mood_query}")

        if progress_callback:
            progress_callback(0.0, "Starting playlist generation...")

        # Calculate effective candidate pool size
        if candidate_pool_size is None:
            candidate_pool_size = max_tracks * candidate_pool_multiplier

        logger.info(f"Using candidate pool size: {candidate_pool_size} (max_tracks: {max_tracks}, multiplier: {candidate_pool_multiplier})")

        filtered_track_ids = self._apply_filters(filters) if filters else None

        if progress_callback:
            progress_callback(0.1, "Searching for candidate tracks...")

        candidates = self._get_candidates(
            mood_query,
            candidate_pool_size,
            filtered_track_ids
        )

        if not candidates:
            logger.warning("No candidate tracks found")
            if progress_callback:
                progress_callback(1.0, "No candidate tracks found")
            return []

        if progress_callback:
            progress_callback(0.4, f"Found {len(candidates)} candidates, selecting tracks with diversity...")

        selected_ids = self._select_diverse_tracks(candidates, max_tracks)

        if progress_callback:
            progress_callback(0.7, "Building final playlist...")

        # Bulk fetch all selected track details in one query
        track_details = self.db.get_track_details_by_ids(selected_ids)
        track_details_map = {d['id']: d for d in track_details}

        seen_tracks = set()
        seen_combinations = set()
        playlist_tracks = []

        for track_id in selected_ids:
            if track_id in seen_tracks:
                continue

            detail = track_details_map.get(track_id)
            if not detail:
                continue

            artist_name = detail['artist_name'] or 'Unknown'
            track_key = (detail['title'].lower(), artist_name.lower())
            if track_key in seen_combinations:
                logger.debug(f"Skipping duplicate: {detail['title']} by {artist_name}")
                continue

            seen_tracks.add(track_id)
            seen_combinations.add(track_key)

            playlist_tracks.append({
                'id': detail['id'],
                'plex_key': detail['plex_key'],
                'title': detail['title'],
                'artist': artist_name,
                'album': detail['album_title'] or 'Unknown',
                'duration_ms': detail['duration_ms'],
                'genre': detail['genre'],
                'year': detail['year']
            })

        if progress_callback:
            progress_callback(1.0, f"Playlist generated with {len(playlist_tracks)} tracks")

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

        if not similar_tracks:
            return []

        # Bulk fetch all track details in one query (eliminates N+1)
        track_ids = [track_id for track_id, _ in similar_tracks]
        track_details = self.db.get_track_details_by_ids(track_ids)

        # Create lookup map for similarity scores
        similarity_map = {track_id: score for track_id, score in similar_tracks}

        # Build candidates with similarity scores
        candidates = []
        for detail in track_details:
            candidates.append({
                'id': detail['id'],
                'title': detail['title'],
                'artist': detail['artist_name'] or 'Unknown',
                'album': detail['album_title'] or 'Unknown',
                'genre': detail['genre'] or '',
                'year': detail['year'] or '',
                'similarity': similarity_map.get(detail['id'], 0.0)
            })

        # Sort by similarity to maintain ranking from vector search
        candidates.sort(key=lambda x: x['similarity'], reverse=True)

        logger.info(f"Retrieved {len(candidates)} candidate tracks")
        return candidates

    def _select_diverse_tracks(
        self,
        candidates: List[Dict[str, Any]],
        max_tracks: int
    ) -> List[int]:
        """Select tracks with artist and album diversity."""
        selected_ids = []
        artist_counts = {}
        album_counts = {}
        seen_combinations = set()

        for candidate in candidates:
            if len(selected_ids) >= max_tracks:
                break

            track_id = candidate['id']
            artist = candidate['artist']
            album = candidate['album']
            title = candidate['title']

            track_key = (title.lower(), artist.lower())
            if track_key in seen_combinations:
                continue

            artist_count = artist_counts.get(artist, 0)
            album_count = album_counts.get(album, 0)

            if artist_count >= 3:
                continue
            if album_count >= 2:
                continue

            selected_ids.append(track_id)
            seen_combinations.add(track_key)
            artist_counts[artist] = artist_count + 1
            album_counts[album] = album_count + 1

        logger.info(f"Selected {len(selected_ids)} diverse tracks from {len(candidates)} candidates")
        return selected_ids

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
