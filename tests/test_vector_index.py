"""Tests for database/vector_index.py."""
import pytest
import numpy as np
import pickle
from pathlib import Path

from plexmix.database.vector_index import VectorIndex

DIM = 8  # Small dimension for fast tests


@pytest.fixture
def vi(tmp_path):
    """Create a VectorIndex with no persisted file."""
    return VectorIndex(dimension=DIM)


@pytest.fixture
def vi_with_path(tmp_path):
    """Create a VectorIndex backed by a file path."""
    path = str(tmp_path / "test.index")
    return VectorIndex(dimension=DIM, index_path=path)


def _random_embeddings(n, dim=DIM):
    return np.random.randn(n, dim).astype(np.float32).tolist()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_empty_index(self, vi):
        assert vi.index is not None
        assert vi.index.ntotal == 0
        assert vi.track_ids == []
        assert vi.dimension == DIM
        assert vi.dimension_mismatch is False

    def test_nonexistent_path_creates_new(self, tmp_path):
        path = str(tmp_path / "nonexistent.index")
        vi = VectorIndex(dimension=DIM, index_path=path)
        assert vi.index is not None
        assert vi.index.ntotal == 0


# ---------------------------------------------------------------------------
# Normalize
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_unit_length(self, vi):
        vectors = np.array([[3.0, 4.0] + [0.0] * (DIM - 2)], dtype=np.float32)
        normed = vi._normalize_vectors(vectors)
        length = np.linalg.norm(normed[0])
        assert abs(length - 1.0) < 1e-5

    def test_zero_vector_safe(self, vi):
        vectors = np.zeros((1, DIM), dtype=np.float32)
        normed = vi._normalize_vectors(vectors)
        # Should not produce NaN
        assert not np.any(np.isnan(normed))


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_basic(self, vi):
        embeddings = _random_embeddings(5)
        track_ids = [10, 20, 30, 40, 50]
        vi.build_index(embeddings, track_ids)
        assert vi.index.ntotal == 5
        assert vi.track_ids == track_ids

    def test_mismatched_lengths_raises(self, vi):
        with pytest.raises(ValueError):
            vi.build_index(_random_embeddings(3), [1, 2])

    def test_empty_is_noop(self, vi):
        vi.build_index([], [])
        assert vi.index.ntotal == 0

    def test_wrong_dimension_raises(self, vi):
        bad_embeddings = np.random.randn(3, DIM + 4).astype(np.float32).tolist()
        with pytest.raises(ValueError, match="dimension"):
            vi.build_index(bad_embeddings, [1, 2, 3])


# ---------------------------------------------------------------------------
# add_vectors
# ---------------------------------------------------------------------------

class TestAddVectors:
    def test_appends_to_existing(self, vi):
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.add_vectors(_random_embeddings(2), [4, 5])
        assert vi.index.ntotal == 5
        assert vi.track_ids == [1, 2, 3, 4, 5]

    def test_mismatched_lengths(self, vi):
        with pytest.raises(ValueError):
            vi.add_vectors(_random_embeddings(2), [1])

    def test_empty_is_noop(self, vi):
        vi.build_index(_random_embeddings(2), [1, 2])
        vi.add_vectors([], [])
        assert vi.index.ntotal == 2

    def test_wrong_dimension_raises(self, vi):
        vi.build_index(_random_embeddings(2), [1, 2])
        bad = np.random.randn(1, DIM + 2).astype(np.float32).tolist()
        with pytest.raises(ValueError, match="dimension"):
            vi.add_vectors(bad, [3])


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_returns_results(self, vi):
        embeddings = _random_embeddings(10)
        vi.build_index(embeddings, list(range(10)))
        results = vi.search(embeddings[0], k=5)
        assert len(results) <= 5
        assert all(isinstance(r, tuple) and len(r) == 2 for r in results)

    def test_respects_k(self, vi):
        vi.build_index(_random_embeddings(10), list(range(10)))
        results = vi.search(_random_embeddings(1)[0], k=3)
        assert len(results) <= 3

    def test_empty_index_returns_empty(self, vi):
        results = vi.search([0.0] * DIM, k=5)
        assert results == []

    def test_track_id_filter_works(self, vi):
        embeddings = _random_embeddings(10)
        vi.build_index(embeddings, list(range(10)))
        results = vi.search(embeddings[0], k=10, track_id_filter=[0, 1, 2])
        returned_ids = [r[0] for r in results]
        assert all(tid in [0, 1, 2] for tid in returned_ids)

    def test_wrong_dimension_raises(self, vi):
        vi.build_index(_random_embeddings(5), list(range(5)))
        with pytest.raises(ValueError, match="dimension"):
            vi.search([0.0] * (DIM + 3), k=3)


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_round_trip_preserves_data(self, tmp_path):
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        embeddings = _random_embeddings(5)
        track_ids = [100, 200, 300, 400, 500]
        vi.build_index(embeddings, track_ids)
        vi.save_index(index_path)

        # Load into new instance
        vi2 = VectorIndex(dimension=DIM, index_path=index_path)
        assert vi2.index.ntotal == 5
        assert vi2.track_ids == track_ids
        assert vi2.dimension_mismatch is False

    def test_creates_metadata_file(self, tmp_path):
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(index_path)
        assert (tmp_path / "test.metadata").exists()

    def test_none_index_save_is_noop(self, tmp_path):
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        vi.index = None
        vi.save_index(index_path)
        assert not Path(index_path).exists()


class TestLoadEdgeCases:
    def test_nonexistent_creates_new(self, tmp_path):
        vi = VectorIndex(dimension=DIM, index_path=str(tmp_path / "nope.index"))
        assert vi.index is not None
        assert vi.index.ntotal == 0

    def test_dimension_mismatch_sets_flag(self, tmp_path):
        index_path = str(tmp_path / "test.index")
        # Save with DIM
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(index_path)

        # Load with different expected dimension
        vi2 = VectorIndex(dimension=DIM * 2, index_path=index_path)
        assert vi2.dimension_mismatch is True
        assert vi2.loaded_dimension == DIM

    def test_missing_metadata_works(self, tmp_path):
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(index_path)

        # Delete metadata
        (tmp_path / "test.metadata").unlink()

        vi2 = VectorIndex(dimension=DIM, index_path=index_path)
        assert vi2.track_ids == []  # no metadata to load
        assert vi2.index.ntotal == 3  # index still loaded

    def test_corrupted_file_deletes_and_recreates(self, tmp_path):
        index_path = str(tmp_path / "test.index")
        Path(index_path).write_text("not a real faiss index")
        vi = VectorIndex(dimension=DIM, index_path=index_path)
        assert vi.index is not None
        assert vi.index.ntotal == 0
        # Corrupted file should be deleted
        assert not Path(index_path).exists()
