import faiss
import numpy as np
import pickle
from pathlib import Path
from typing import List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class VectorIndex:
    def __init__(self, dimension: int, index_path: Optional[str] = None):
        self.dimension = dimension
        self.index_path = Path(index_path).expanduser() if index_path else None
        self.index: Optional[faiss.IndexFlatIP] = None
        self.track_ids: List[int] = []

        if self.index_path and self.index_path.exists():
            self.load_index(str(self.index_path))
        else:
            self._create_index()

    def _create_index(self) -> None:
        self.index = faiss.IndexFlatIP(self.dimension)
        logger.info(f"Created new FAISS index with dimension {self.dimension}")

    def _normalize_vectors(self, vectors: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vectors / norms

    def build_index(self, embeddings: List[List[float]], track_ids: List[int]) -> None:
        if len(embeddings) != len(track_ids):
            raise ValueError("Number of embeddings must match number of track IDs")

        if not embeddings:
            logger.warning("No embeddings provided to build index")
            return

        self._create_index()
        self.track_ids = track_ids

        vectors = np.array(embeddings, dtype=np.float32)

        if vectors.shape[1] != self.dimension:
            raise ValueError(
                f"Embedding dimension {vectors.shape[1]} does not match index dimension {self.dimension}"
            )

        vectors_normalized = self._normalize_vectors(vectors)

        if self.index is not None:
            self.index.add(vectors_normalized)
            logger.info(f"Built FAISS index with {len(track_ids)} vectors")

    def add_vectors(self, embeddings: List[List[float]], track_ids: List[int]) -> None:
        if len(embeddings) != len(track_ids):
            raise ValueError("Number of embeddings must match number of track IDs")

        if not embeddings:
            return

        vectors = np.array(embeddings, dtype=np.float32)

        if vectors.shape[1] != self.dimension:
            raise ValueError(
                f"Embedding dimension {vectors.shape[1]} does not match index dimension {self.dimension}"
            )

        vectors_normalized = self._normalize_vectors(vectors)

        if self.index is None:
            self._create_index()

        if self.index is not None:
            self.index.add(vectors_normalized)
            self.track_ids.extend(track_ids)
            logger.info(f"Added {len(track_ids)} vectors to index")

    def search(
        self, query_vector: List[float], k: int = 25, track_id_filter: Optional[List[int]] = None
    ) -> List[Tuple[int, float]]:
        if self.index is None or self.index.ntotal == 0:
            logger.warning("Cannot search: index is empty")
            return []

        query_np = np.array([query_vector], dtype=np.float32)

        if query_np.shape[1] != self.dimension:
            raise ValueError(
                f"Query vector dimension {query_np.shape[1]} does not match index dimension {self.dimension}"
            )

        query_normalized = self._normalize_vectors(query_np)

        k_search = min(k, self.index.ntotal)

        if track_id_filter:
            k_search = min(k_search * 3, self.index.ntotal)

        distances, indices = self.index.search(query_normalized, k_search)

        results = []
        for idx, distance in zip(indices[0], distances[0]):
            if idx < len(self.track_ids):
                track_id = self.track_ids[idx]
                if track_id_filter is None or track_id in track_id_filter:
                    results.append((track_id, float(distance)))
                    if len(results) >= k:
                        break

        logger.debug(f"Found {len(results)} results for search")
        return results

    def save_index(self, path: str) -> None:
        if self.index is None:
            logger.warning("Cannot save: index is None")
            return

        save_path = Path(path).expanduser()
        save_path.parent.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self.index, str(save_path))

        metadata_path = save_path.with_suffix('.metadata')
        with open(metadata_path, 'wb') as f:
            pickle.dump({
                'track_ids': self.track_ids,
                'dimension': self.dimension
            }, f)

        logger.info(f"Saved FAISS index to {save_path}")

    def load_index(self, path: str) -> None:
        load_path = Path(path).expanduser()

        if not load_path.exists():
            logger.warning(f"Index file not found: {load_path}")
            self._create_index()
            return

        try:
            self.index = faiss.read_index(str(load_path))

            metadata_path = load_path.with_suffix('.metadata')
            if metadata_path.exists():
                with open(metadata_path, 'rb') as f:
                    metadata = pickle.load(f)
                    self.track_ids = metadata.get('track_ids', [])
                    loaded_dimension = metadata.get('dimension', self.dimension)

                    if loaded_dimension != self.dimension:
                        logger.warning(
                            f"Loaded index dimension {loaded_dimension} does not match expected {self.dimension}"
                        )

            logger.info(f"Loaded FAISS index from {load_path} with {len(self.track_ids)} vectors")
        except Exception as e:
            logger.error(f"Failed to load corrupted index file: {e}")
            logger.info("Deleting corrupted index and creating new one")

            if load_path.exists():
                load_path.unlink()

            metadata_path = load_path.with_suffix('.metadata')
            if metadata_path.exists():
                metadata_path.unlink()

            self._create_index()
