"""Tag and embedding generation orchestration — shared between CLI and UI."""

import logging
from typing import Any, Callable, Dict, List, Optional

from plexmix.config.constants import EMBEDDING_BATCH_SIZE
from plexmix.database.models import Embedding
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.utils.embeddings import EmbeddingGenerator, create_track_text

logger = logging.getLogger(__name__)


def build_track_embedding_data(db: SQLiteManager, track: Any) -> Dict[str, Any]:
    """Build a track data dict suitable for embedding text generation.

    Resolves artist and album names from the database.
    """
    artist = db.get_artist_by_id(track.artist_id)
    album = db.get_album_by_id(track.album_id)
    return {
        "id": track.id,
        "title": track.title,
        "artist": artist.name if artist else "Unknown",
        "album": album.title if album else "Unknown",
        "genre": track.genre or "",
        "year": track.year or "",
        "tags": track.tags or "",
        "environments": track.environments or "",
        "instruments": track.instruments or "",
    }


def generate_embeddings_for_tracks(
    db: SQLiteManager,
    embedding_generator: EmbeddingGenerator,
    tracks: List[Any],
    batch_size: int = EMBEDDING_BATCH_SIZE,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> int:
    """Generate and save embeddings for a list of Track objects.

    Args:
        db: Database connection.
        embedding_generator: Configured embedding generator.
        tracks: List of Track model objects.
        batch_size: Number of tracks per embedding batch.
        progress_callback: Optional ``callback(generated_count, total_count)``.

    Returns the number of embeddings generated.
    """
    total = len(tracks)
    generated = 0

    for i in range(0, total, batch_size):
        batch_tracks = tracks[i : i + batch_size]

        track_data_list = [build_track_embedding_data(db, t) for t in batch_tracks]
        texts = [create_track_text(td) for td in track_data_list]
        vectors = embedding_generator.generate_batch_embeddings(texts, batch_size=batch_size)

        for td, vector in zip(track_data_list, vectors):
            embedding = Embedding(
                track_id=td["id"],
                embedding_model=embedding_generator.provider_name,
                embedding_dim=embedding_generator.get_dimension(),
                vector=vector,
            )
            db.insert_embedding(embedding)
            generated += 1

        if progress_callback:
            progress_callback(generated, total)

    return generated


def rebuild_vector_index(
    db: SQLiteManager,
    vector_index: VectorIndex,
    index_path: str,
) -> int:
    """Rebuild and save the FAISS index from all embeddings in the database.

    Returns the number of embeddings in the rebuilt index.
    """
    all_embeddings = db.get_all_embeddings()
    track_ids = [emb[0] for emb in all_embeddings]
    vectors = [emb[1] for emb in all_embeddings]

    vector_index.build_index(vectors, track_ids)
    vector_index.save_index(index_path)

    return len(vectors)
