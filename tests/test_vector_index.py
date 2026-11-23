"""Tests for database/vector_index.py."""
import logging
import os
import pickle
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import faiss
import pytest
import numpy as np
from pathlib import Path

from plexmix.database import vector_index as vector_index_module
from plexmix.database.vector_index import VectorIndex

DIM = 8  # Small dimension for fast tests
LOGGER_NAME = "plexmix.database.vector_index"


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


class TestVectorsOfAnotherSize:
    """build_index refuses vectors of another size before it changes anything.

    A change of the vector size leaves embeddings of two sizes in the database. An
    index of the vectors that fit would cover part of the library and would take the
    place of the saved one, so the build is refused as a whole. The caller that
    builds from the database (``index_rebuild.rebuild_index``) tests for it first.
    """

    @staticmethod
    def _saved_index(tmp_path: Path, dimension: int = 4) -> Path:
        """Three vectors of ``dimension`` numbers, saved for tracks 1, 2 and 3."""
        path = tmp_path / "embeddings.index"
        saved = VectorIndex(dimension=dimension)
        saved.build_index(_random_embeddings(3, dimension), [1, 2, 3])
        assert saved.save_index(str(path)) is True
        return path

    @staticmethod
    def _files(path: Path) -> Dict[str, bytes]:
        return {item.name: item.read_bytes() for item in sorted(path.parent.iterdir())}

    @pytest.mark.parametrize(
        "sizes",
        [[DIM, DIM + 4, DIM, DIM + 4, DIM], [DIM + 4] * 3, [DIM, 0], [DIM - 1]],
        ids=["two sizes", "all of another size", "an empty vector", "one short vector"],
    )
    def test_build_is_refused_before_anything_is_changed(
        self, sizes: List[int], tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = self._saved_index(tmp_path, DIM)
        before = self._files(path)
        index = VectorIndex(dimension=DIM, index_path=str(path))
        loaded = index.index
        assert index.track_ids == [1, 2, 3] and loaded.ntotal == 3

        # Only what the refused build logs counts: the saved index above was made
        # with INFO lines of its own when another test left the logger at INFO.
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            with pytest.raises(ValueError, match="do not match index dimension") as refused:
                index.build_index(
                    [[0.5] * size for size in sizes], list(range(10, 10 + len(sizes)))
                )

        wrong = sum(1 for size in sizes if size != DIM)
        assert str(refused.value).startswith(f"{wrong} of {len(sizes)} embeddings do not match")
        assert "was not built" in str(refused.value)
        # The object holds what it held: the same index, the same tracks, the same flags.
        assert index.index is loaded and index.index.ntotal == 3
        assert index.track_ids == [1, 2, 3] and index.dimension == DIM
        assert index.dimension_mismatch is False and index.load_failed is False
        # No new index was made in memory, so there is nothing a save could write.
        assert "Created new FAISS IndexIDMap" not in caplog.text
        assert self._files(path) == before

    def test_refused_build_keeps_the_saved_index_and_its_dimension_mismatch(
        self, tmp_path: Path
    ) -> None:
        """The index was saved with 4 numbers per vector and the provider now makes 8."""
        path = self._saved_index(tmp_path, dimension=4)
        before = self._files(path)
        index = VectorIndex(dimension=8, index_path=str(path))
        assert index.dimension_mismatch is True and index.loaded_dimension == 4
        loaded, tracks = index.index, list(index.track_ids)

        # One track was embedded again with 8 numbers; three rows still hold 4.
        embeddings = [[0.5] * 8, [0.1] * 4, [0.1] * 4, [0.1] * 4]
        with pytest.raises(ValueError, match="3 of 4 embeddings do not match index dimension 8"):
            index.build_index(embeddings, [9, 1, 2, 3])

        assert index.index is loaded and index.track_ids == tracks
        assert index.dimension_mismatch is True and index.loaded_dimension == 4
        # Saving nothing: the files are the ones from before, byte for byte.
        assert self._files(path) == before
        reopened = VectorIndex(dimension=8, index_path=str(path))
        assert reopened.dimension_mismatch is True and reopened.track_ids == [1, 2, 3]
        assert not hasattr(index, "skipped_vectors")

    def test_build_refuses_what_is_not_a_vector(self, vi: VectorIndex) -> None:
        vi.build_index(_random_embeddings(2), [1, 2])
        with pytest.raises(ValueError, match="1 of 2 embeddings do not match index dimension"):
            vi.build_index([_random_embeddings(1)[0], 7], [3, 4])  # type: ignore[list-item]
        assert vi.track_ids == [1, 2] and vi.index.ntotal == 2

    def test_build_of_vectors_that_all_fit_replaces_the_index(self, tmp_path: Path) -> None:
        path = self._saved_index(tmp_path, DIM)
        index = VectorIndex(dimension=DIM, index_path=str(path))
        fitting = _random_embeddings(4)

        index.build_index(fitting, [5, 6, 7, 8])

        assert index.track_ids == [5, 6, 7, 8] and index.index.ntotal == 4
        assert index.search(fitting[2], k=1)[0][0] == 7

    def test_save_says_whether_the_files_were_written(self, tmp_path: Path) -> None:
        path = tmp_path / "embeddings.index"
        index = VectorIndex(dimension=DIM)
        index.build_index(_random_embeddings(2), [1, 2])
        assert index.save_index(str(path)) is True and path.exists()

        index.index = None
        assert index.save_index(str(tmp_path / "other.index")) is False
        assert not (tmp_path / "other.index").exists()

    def test_save_over_a_file_that_could_not_be_moved_aside_says_no(self, tmp_path: Path) -> None:
        index_file = _unreadable_pair(tmp_path)
        with patch.object(vector_index_module.os, "rename", side_effect=OSError("denied")):
            index = VectorIndex(dimension=DIM, index_path=str(index_file))
        index.build_index(_random_embeddings(2), [1, 2])

        assert index.save_index(str(index_file)) is False
        assert index_file.read_bytes() == NOT_AN_INDEX


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

    def test_save_is_atomic_no_temp_files_left(self, tmp_path):
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(index_path)
        # Atomic replace must leave no .tmp artifacts behind.
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
        # And the round trip still works.
        vi2 = VectorIndex(dimension=DIM, index_path=index_path)
        assert vi2.index.ntotal == 3

    def test_missing_metadata_flags_dimension_mismatch(self, tmp_path):
        """Loading an index whose dimension differs from the current provider,
        with no metadata file, must flag a mismatch rather than accept it and
        crash later inside faiss."""
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(index_path)
        # Remove the metadata so only the raw index remains.
        (tmp_path / "test.metadata").unlink()

        vi2 = VectorIndex(dimension=DIM + 4, index_path=index_path)
        assert vi2.dimension_mismatch is True
        assert vi2.loaded_dimension == DIM


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

    def test_healthy_load_reports_no_failure(self, tmp_path: Path) -> None:
        index_path = str(tmp_path / "test.index")
        vi = VectorIndex(dimension=DIM)
        assert (vi.load_failed, vi.moved_paths, vi.unmoved_paths) == (False, [], [])
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(index_path)

        vi2 = VectorIndex(dimension=DIM, index_path=index_path)
        assert vi2.index.ntotal == 3
        assert (vi2.load_failed, vi2.moved_paths, vi2.unmoved_paths) == (False, [], [])
        assert _names(tmp_path) == ["test.index", "test.metadata"]


# ---------------------------------------------------------------------------
# An index that cannot be loaded is moved aside, never deleted
# ---------------------------------------------------------------------------

NOT_AN_INDEX = b"not a real faiss index"
NOT_METADATA = b"not its metadata"
MOVED_NAME = re.compile(r"test\.(index|metadata)\.unreadable-(\d{8}-\d{6})")


def _names(folder: Path) -> List[str]:
    return sorted(path.name for path in folder.iterdir())


def _warnings(caplog: pytest.LogCaptureFixture) -> List[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME and record.levelno >= logging.WARNING
    ]


def _unreadable_pair(folder: Path) -> Path:
    """Write an index file and a metadata file that cannot be loaded. Returns the index path."""
    index_file = folder / "test.index"
    index_file.write_bytes(NOT_AN_INDEX)
    (folder / "test.metadata").write_bytes(NOT_METADATA)
    return index_file


class TestUnreadableIndex:
    def test_unreadable_index_is_moved_aside_not_deleted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        index_file = _unreadable_pair(tmp_path)
        before = datetime.now().replace(microsecond=0)

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            vi = VectorIndex(dimension=DIM, index_path=str(index_file))

        # The object starts with an empty index and says what happened.
        assert vi.index is not None
        assert vi.index.ntotal == 0
        assert vi.track_ids == []
        assert vi.load_failed is True
        assert vi.dimension_mismatch is False
        assert vi.unmoved_paths == []

        # Both files still exist, byte for byte, under names with the date and time.
        moved_index, moved_metadata = vi.moved_paths
        assert _names(tmp_path) == sorted([moved_index.name, moved_metadata.name])
        assert moved_index.read_bytes() == NOT_AN_INDEX
        assert moved_metadata.read_bytes() == NOT_METADATA
        index_match = MOVED_NAME.fullmatch(moved_index.name)
        metadata_match = MOVED_NAME.fullmatch(moved_metadata.name)
        assert index_match is not None and index_match.group(1) == "index"
        assert metadata_match is not None and metadata_match.group(1) == "metadata"
        assert index_match.group(2) == metadata_match.group(2)
        stamp = datetime.strptime(index_match.group(2), "%Y%m%d-%H%M%S")
        assert before <= stamp <= datetime.now() + timedelta(seconds=1)

        # One warning names the files and the command that rebuilds the index.
        (warning,) = _warnings(caplog)
        assert "could not be loaded" in warning
        assert str(index_file) in warning
        assert str(moved_index) in warning
        assert str(moved_metadata) in warning
        assert "plexmix embeddings generate" in warning

    def test_index_saved_after_a_failed_load_leaves_the_moved_files(self, tmp_path: Path) -> None:
        index_file = _unreadable_pair(tmp_path)
        vi = VectorIndex(dimension=DIM, index_path=str(index_file))
        moved_index, moved_metadata = vi.moved_paths

        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(str(index_file))

        assert moved_index.read_bytes() == NOT_AN_INDEX
        assert moved_metadata.read_bytes() == NOT_METADATA
        rebuilt = VectorIndex(dimension=DIM, index_path=str(index_file))
        assert rebuilt.load_failed is False
        assert rebuilt.index.ntotal == 3
        assert rebuilt.track_ids == [1, 2, 3]

    @pytest.mark.parametrize("kept_bytes", [0, 10], ids=["empty", "cut-short"])
    def test_truncated_metadata_keeps_the_index_file(self, tmp_path: Path, kept_bytes: int) -> None:
        index_file = tmp_path / "test.index"
        metadata_file = tmp_path / "test.metadata"
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(str(index_file))
        index_bytes = index_file.read_bytes()
        truncated = metadata_file.read_bytes()[:kept_bytes]
        metadata_file.write_bytes(truncated)

        vi2 = VectorIndex(dimension=DIM, index_path=str(index_file))

        assert vi2.load_failed is True
        assert vi2.index.ntotal == 0
        assert vi2.track_ids == []
        moved_index, moved_metadata = vi2.moved_paths
        assert moved_index.name.startswith("test.index.unreadable-")
        assert moved_metadata.name.startswith("test.metadata.unreadable-")
        assert moved_index.read_bytes() == index_bytes
        assert moved_metadata.read_bytes() == truncated
        assert _names(tmp_path) == sorted([moved_index.name, moved_metadata.name])

    def test_metadata_that_is_not_a_mapping_is_moved_aside(self, tmp_path: Path) -> None:
        index_file = tmp_path / "test.index"
        metadata_file = tmp_path / "test.metadata"
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(str(index_file))
        metadata_file.write_bytes(pickle.dumps([1, 2, 3]))

        vi2 = VectorIndex(dimension=DIM, index_path=str(index_file))

        assert vi2.load_failed is True
        assert vi2.track_ids == []
        assert len(vi2.moved_paths) == 2
        assert all(path.exists() for path in vi2.moved_paths)

    def test_unmovable_index_is_not_overwritten(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        index_file = _unreadable_pair(tmp_path)
        metadata_file = tmp_path / "test.metadata"

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            with patch.object(
                vector_index_module.os, "rename", side_effect=PermissionError(13, "denied")
            ):
                vi = VectorIndex(dimension=DIM, index_path=str(index_file))

            # The files are left where they are, and the object remembers them.
            assert vi.load_failed is True
            assert vi.moved_paths == []
            assert vi.unmoved_paths == [index_file, metadata_file]
            assert vi.index is not None and vi.index.ntotal == 0
            (warning,) = _warnings(caplog)
            assert str(index_file) in warning and str(metadata_file) in warning
            assert "plexmix embeddings generate" in warning
            caplog.clear()

            # The rename works again here: only the object's memory stops the save.
            vi.build_index(_random_embeddings(3), [1, 2, 3])
            vi.save_index(str(index_file))

            (skipped,) = _warnings(caplog)
            assert "Not saving" in skipped and str(index_file) in skipped

        assert index_file.read_bytes() == NOT_AN_INDEX
        assert metadata_file.read_bytes() == NOT_METADATA
        assert _names(tmp_path) == ["test.index", "test.metadata"]

        # Another path is still saved to.
        other = tmp_path / "elsewhere" / "copy.index"
        vi.save_index(str(other))
        assert VectorIndex(dimension=DIM, index_path=str(other)).index.ntotal == 3

    def test_unmovable_index_is_not_overwritten_under_another_spelling(
        self, tmp_path: Path
    ) -> None:
        index_file = _unreadable_pair(tmp_path)
        with patch.object(vector_index_module.os, "rename", side_effect=OSError("no")):
            vi = VectorIndex(dimension=DIM, index_path=str(index_file))
        vi.build_index(_random_embeddings(3), [1, 2, 3])

        vi.save_index(str(tmp_path / "sub" / ".." / "test.index"))
        # A different index name that shares the metadata file.
        vi.save_index(str(tmp_path / "test.faiss"))

        assert index_file.read_bytes() == NOT_AN_INDEX
        assert (tmp_path / "test.metadata").read_bytes() == NOT_METADATA
        assert _names(tmp_path) == ["test.index", "test.metadata"]

    def test_unmovable_metadata_also_stops_the_save(self, tmp_path: Path) -> None:
        index_file = _unreadable_pair(tmp_path)
        metadata_file = tmp_path / "test.metadata"
        real_rename = os.rename

        def rename_index_only(source: Any, target: Any) -> None:
            if Path(source) == metadata_file:
                raise PermissionError(13, "denied")
            real_rename(source, target)

        with patch.object(vector_index_module.os, "rename", side_effect=rename_index_only):
            vi = VectorIndex(dimension=DIM, index_path=str(index_file))

        (moved_index,) = vi.moved_paths
        assert moved_index.read_bytes() == NOT_AN_INDEX
        assert vi.unmoved_paths == [metadata_file]

        # Saving the index would write its metadata over the unreadable file.
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(str(index_file))

        assert metadata_file.read_bytes() == NOT_METADATA
        assert _names(tmp_path) == sorted([moved_index.name, "test.metadata"])

    @pytest.mark.skipif(
        os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
        reason="needs a folder the test may not write to",
    )
    def test_index_in_a_read_only_folder_is_left_in_place(self, tmp_path: Path) -> None:
        folder = tmp_path / "locked"
        folder.mkdir()
        index_file = _unreadable_pair(folder)
        folder.chmod(0o500)
        try:
            vi = VectorIndex(dimension=DIM, index_path=str(index_file))
            vi.build_index(_random_embeddings(3), [1, 2, 3])
            vi.save_index(str(index_file))
            names = _names(folder)
        finally:
            folder.chmod(0o700)

        assert vi.load_failed is True
        assert vi.moved_paths == []
        assert vi.unmoved_paths == [index_file, folder / "test.metadata"]
        assert names == ["test.index", "test.metadata"]
        assert index_file.read_bytes() == NOT_AN_INDEX

    def test_folder_at_the_index_path_is_not_moved(self, tmp_path: Path) -> None:
        """A folder is not the index. Renaming it would take everything inside with it."""
        folder = tmp_path / "test.index"
        folder.mkdir()
        (folder / "plexmix.db").write_bytes(b"the library")

        vi = VectorIndex(dimension=DIM, index_path=str(folder))
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        vi.save_index(str(folder))

        assert vi.load_failed is True
        assert vi.moved_paths == []
        assert vi.unmoved_paths == [folder]
        assert _names(tmp_path) == ["test.index"]
        assert (folder / "plexmix.db").read_bytes() == b"the library"

    def test_second_failure_in_the_same_second_keeps_the_first_moved_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vector_index_module, "_unreadable_stamp", lambda: "20261118-101500")
        index_file = tmp_path / "test.index"
        metadata_file = tmp_path / "test.metadata"

        index_file.write_bytes(b"first unreadable index")
        metadata_file.write_bytes(b"first metadata")
        first = VectorIndex(dimension=DIM, index_path=str(index_file))
        index_file.write_bytes(b"second unreadable index")
        metadata_file.write_bytes(b"second metadata")
        second = VectorIndex(dimension=DIM, index_path=str(index_file))

        assert [path.name for path in first.moved_paths] == [
            "test.index.unreadable-20261118-101500",
            "test.metadata.unreadable-20261118-101500",
        ]
        assert [path.name for path in second.moved_paths] == [
            "test.index.unreadable-20261118-101500-2",
            "test.metadata.unreadable-20261118-101500-2",
        ]
        assert [path.read_bytes() for path in first.moved_paths + second.moved_paths] == [
            b"first unreadable index",
            b"first metadata",
            b"second unreadable index",
            b"second metadata",
        ]
        assert len(_names(tmp_path)) == 4

    def test_moved_pair_shares_one_ending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vector_index_module, "_unreadable_stamp", lambda: "20261118-101500")
        index_file = tmp_path / "test.index"

        # First failure: an index file without a metadata file.
        index_file.write_bytes(b"first unreadable index")
        first = VectorIndex(dimension=DIM, index_path=str(index_file))
        # Second failure in the same second: this time with a metadata file.
        _unreadable_pair(tmp_path)
        second = VectorIndex(dimension=DIM, index_path=str(index_file))

        assert [path.name for path in first.moved_paths] == [
            "test.index.unreadable-20261118-101500"
        ]
        assert [path.name for path in second.moved_paths] == [
            "test.index.unreadable-20261118-101500-2",
            "test.metadata.unreadable-20261118-101500-2",
        ]
        assert first.moved_paths[0].read_bytes() == b"first unreadable index"

    def test_no_free_name_leaves_the_files_in_place(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vector_index_module, "_unreadable_stamp", lambda: "20261118-101500")
        monkeypatch.setattr(vector_index_module, "_MAX_MOVES_PER_SECOND", 2)
        index_file = _unreadable_pair(tmp_path)
        (tmp_path / "test.index.unreadable-20261118-101500").write_bytes(b"earlier one")
        (tmp_path / "test.index.unreadable-20261118-101500-2").write_bytes(b"earlier two")

        vi = VectorIndex(dimension=DIM, index_path=str(index_file))

        assert vi.load_failed is True
        assert vi.moved_paths == []
        assert vi.unmoved_paths == [index_file, tmp_path / "test.metadata"]
        assert index_file.read_bytes() == NOT_AN_INDEX
        assert (tmp_path / "test.index.unreadable-20261118-101500").read_bytes() == b"earlier one"
        assert (tmp_path / "test.index.unreadable-20261118-101500-2").read_bytes() == b"earlier two"

    def test_files_are_untouched_when_no_empty_index_can_be_made(self, tmp_path: Path) -> None:
        """Then the fault is not in the files, so they are not moved."""
        index_file = _unreadable_pair(tmp_path)

        with patch.object(VectorIndex, "_create_index", side_effect=RuntimeError("no faiss")):
            with pytest.raises(RuntimeError, match="no faiss"):
                VectorIndex(dimension=DIM, index_path=str(index_file))

        assert _names(tmp_path) == ["test.index", "test.metadata"]
        assert index_file.read_bytes() == NOT_AN_INDEX

    def test_faiss_import_failure_does_not_delete_the_index(self, tmp_path: Path) -> None:
        """FAISS loads on first use. When it cannot be imported, the saved files are not
        taken for a corrupt index: the error is raised and the files stay as they are."""
        index_file = tmp_path / "test.index"
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(3), [1, 2, 3])
        assert vi.save_index(str(index_file)) is True
        metadata_file = tmp_path / "test.metadata"
        index_bytes = index_file.read_bytes()
        metadata_bytes = metadata_file.read_bytes()

        with patch(
            "plexmix.database.vector_index._faiss", side_effect=ImportError("no faiss here")
        ):
            with pytest.raises(ImportError, match="no faiss here"):
                VectorIndex(dimension=DIM, index_path=str(index_file))

        assert index_file.exists()
        assert metadata_file.exists()
        assert index_file.read_bytes() == index_bytes
        assert metadata_file.read_bytes() == metadata_bytes
        assert _names(tmp_path) == ["test.index", "test.metadata"]

        # With FAISS importable again the same files load.
        healthy = VectorIndex(dimension=DIM, index_path=str(index_file))
        assert healthy.load_failed is False
        assert healthy.index.ntotal == 3
        assert healthy.track_ids == [1, 2, 3]

    def test_faiss_import_failure_is_not_treated_as_an_unreadable_index(
        self, tmp_path: Path
    ) -> None:
        """The first FAISS call of ``load_index`` sits above its ``try``. When FAISS fails
        there and works afterwards, the saved files must still not be moved aside: a
        failure inside the ``try`` would send them to ``.unreadable-<date>-<time>``."""
        index_file = tmp_path / "test.index"
        vi = VectorIndex(dimension=DIM)
        vi.build_index(_random_embeddings(2), [7, 8])
        vi.save_index(str(index_file))

        calls: List[int] = []

        def fails_once() -> Any:
            calls.append(1)
            if len(calls) == 1:
                raise ImportError("no faiss yet")
            return faiss

        other = VectorIndex(dimension=DIM)
        with patch("plexmix.database.vector_index._faiss", side_effect=fails_once):
            with pytest.raises(ImportError, match="no faiss yet"):
                other.load_index(str(index_file))

        assert len(calls) == 1
        assert other.load_failed is False
        assert other.moved_paths == []
        assert other.unmoved_paths == []
        assert _names(tmp_path) == ["test.index", "test.metadata"]


# ---------------------------------------------------------------------------
# Older-format index files (a bare IndexFlatIP, positions mapped by the metadata)
# ---------------------------------------------------------------------------


def _write_legacy_index(
    index_file: Path, track_ids: List[int], metadata: Optional[Dict[str, Any]]
) -> np.ndarray:
    """Write an index in the older format, with ``metadata`` beside it unless it is None."""
    vectors = np.random.randn(len(track_ids), DIM).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    flat = faiss.IndexFlatIP(DIM)
    flat.add(vectors)
    faiss.write_index(flat, str(index_file))
    if metadata is not None:
        with open(index_file.with_suffix(".metadata"), "wb") as f:
            pickle.dump(metadata, f)
    return vectors


class TestLegacyIndex:
    def test_legacy_index_with_the_same_dimension_is_still_migrated(self, tmp_path: Path) -> None:
        index_file = tmp_path / "test.index"
        vectors = _write_legacy_index(
            index_file, [10, 20, 30], {"track_ids": [10, 20, 30], "dimension": DIM}
        )

        vi = VectorIndex(dimension=DIM, index_path=str(index_file))

        assert vi.load_failed is False
        assert vi.dimension_mismatch is False
        assert vi.index.ntotal == 3
        assert vi.track_ids == [10, 20, 30]
        # Results are track ids now, not positions.
        assert vi.search(vectors[1].tolist(), k=1)[0][0] == 20
        assert _names(tmp_path) == ["test.index", "test.metadata"]

    @pytest.mark.parametrize(
        "metadata",
        [
            {"track_ids": [10, 20, 30], "dimension": DIM},
            {"track_ids": [10, 20, 30]},
            None,
        ],
        ids=["metadata-with-dimension", "metadata-without-dimension", "no-metadata"],
    )
    def test_legacy_index_with_another_dimension_is_flagged_not_migrated(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        index_file = tmp_path / "test.index"
        _write_legacy_index(index_file, [10, 20, 30], metadata)
        files_before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            vi = VectorIndex(dimension=DIM + 4, index_path=str(index_file))

        # Flagged for the user, not treated as a file that cannot be loaded.
        assert vi.dimension_mismatch is True
        assert vi.loaded_dimension == DIM
        assert vi.load_failed is False
        assert vi.moved_paths == []
        assert vi.unmoved_paths == []
        # Nothing was migrated into the index of the new dimension.
        assert vi.index is not None
        assert vi.index.ntotal == 0
        assert vi.track_ids == []
        # The files are left alone.
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == files_before
        # One warning, and it says what to do.
        (warning,) = _warnings(caplog)
        assert "regenerat" in warning.lower()

    def test_legacy_index_is_replaced_when_the_embeddings_are_regenerated(
        self, tmp_path: Path
    ) -> None:
        index_file = tmp_path / "test.index"
        _write_legacy_index(index_file, [10, 20, 30], {"track_ids": [10, 20, 30]})
        vi = VectorIndex(dimension=DIM + 4, index_path=str(index_file))

        vi.build_index(_random_embeddings(2, DIM + 4), [10, 20])
        vi.save_index(str(index_file))

        rebuilt = VectorIndex(dimension=DIM + 4, index_path=str(index_file))
        assert rebuilt.dimension_mismatch is False
        assert rebuilt.index.ntotal == 2
        assert rebuilt.track_ids == [10, 20]
