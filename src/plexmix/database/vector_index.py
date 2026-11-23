import numpy as np
import os
import pickle
import stat
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Tuple, Optional
import logging

if TYPE_CHECKING:
    from .index_rebuild import IndexRebuild

logger = logging.getLogger(__name__)


def _faiss() -> Any:
    """Import FAISS on first use instead of when this module is imported.

    The CLI imports this module at start-up, and a local-model worker that a CLI command
    spawns re-runs the CLI's main script. FAISS's OpenMP runtime next to torch's in one
    process crashes on macOS, so FAISS loads only where a vector index is used and
    never in a worker. ``tests/test_process_isolation.py`` keeps every other
    ``import faiss`` out of the source.
    """
    import faiss  # type: ignore[import-untyped]

    return faiss


# The command that builds the search index again from the stored embeddings.
_REBUILD_COMMAND = "plexmix embeddings generate"

# Endings tried for files moved aside in one second: <stamp>, <stamp>-2, <stamp>-3, ...
_MAX_MOVES_PER_SECOND = 100


def _unreadable_stamp() -> str:
    """The date and time that go into the name of a file that is moved aside."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _with_ending(file: Path, ending: str) -> Path:
    return file.parent / (file.name + ending)


def _unreadable_ending(files: List[Path]) -> Optional[str]:
    """An ``.unreadable-<date>-<time>`` ending that is free for every file in ``files``.

    All files of one load get the same ending, so the pair stays recognisable. A name
    that is already taken (an earlier failed load in the same second) is never reused:
    a rename would replace that earlier file. None when no free ending was found.
    """
    stamp = _unreadable_stamp()
    for attempt in range(1, _MAX_MOVES_PER_SECOND + 1):
        ending = f".unreadable-{stamp}" if attempt == 1 else f".unreadable-{stamp}-{attempt}"
        if not any(os.path.lexists(_with_ending(file, ending)) for file in files):
            return ending
    return None


def _move_aside(file: Path, ending: Optional[str]) -> Path:
    """Rename ``file`` by adding ``ending`` to its name and return the new path.

    Only a regular file or a symbolic link is renamed. A folder or a device at the
    configured path is not the index, and renaming it could take much more with it.

    Raises:
        OSError: the file was not renamed, and is where it was.
    """
    if ending is None:
        raise FileExistsError("no free name is left for this second")
    mode = os.lstat(file).st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
        raise OSError("it is not a file")
    target = _with_ending(file, ending)
    os.rename(file, target)
    return target


def _same_file(first: Path, second: Path) -> bool:
    """Whether two paths name one file. A path that does not exist is compared by name."""
    try:
        return os.path.samefile(first, second)
    except (OSError, ValueError):
        return os.path.realpath(first) == os.path.realpath(second)


def _reason(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__


def _size_of(vector: Any) -> Optional[int]:
    """How many numbers ``vector`` holds; None for something that is not a vector."""
    try:
        return len(vector)
    except TypeError:
        return None


class VectorIndex:
    def __init__(self, dimension: int, index_path: Optional[str] = None):
        self.dimension = dimension
        self.index_path = Path(index_path).expanduser() if index_path else None
        self.index: Optional[Any] = None
        self.track_ids: List[int] = []
        self.dimension_mismatch: bool = False
        self.loaded_dimension: Optional[int] = None
        # ``load_failed`` is set when the last load_index could not read the saved
        # files. They are never deleted: ``moved_paths`` holds the new names of the
        # files that were moved aside, and ``unmoved_paths`` the files that could not
        # be renamed. Those stay where they are and save_index never writes over them.
        self.load_failed: bool = False
        self.moved_paths: List[Path] = []
        self.unmoved_paths: List[Path] = []
        # What the last ``index_rebuild.rebuild_index`` call on this object did: the
        # vectors it put into the index, the tracks that were in the way, and
        # whether the files were written. None until this object was rebuilt.
        self.last_rebuild: Optional["IndexRebuild"] = None

        if self.index_path and self.index_path.exists():
            self.load_index(str(self.index_path))
        else:
            self._create_index()
            self.dimension_mismatch = False
            self.loaded_dimension = None

    def _create_index(self) -> None:
        faiss = _faiss()
        self.index = faiss.IndexIDMap(faiss.IndexFlatIP(self.dimension))
        logger.info(f"Created new FAISS IndexIDMap with dimension {self.dimension}")

    def _normalize_vectors(self, vectors: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        result: np.ndarray = vectors / norms
        return result

    def _checked_vectors(self, embeddings: List[List[float]]) -> np.ndarray:
        """``embeddings`` as one array, after a check that each has this index's dimension.

        Raises:
            ValueError: at least one vector has another size. Nothing was changed.
        """
        sizes = [_size_of(vector) for vector in embeddings]
        wrong = [size for size in sizes if size != self.dimension]
        if wrong:
            other = sorted({str(size) if size is not None else "unknown" for size in wrong})
            raise ValueError(
                f"{len(wrong)} of {len(sizes)} embeddings do not match index dimension "
                f"{self.dimension} (their size: {', '.join(other)}), so the index was not "
                "built and is as it was."
            )
        vectors = np.array(embeddings, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[1] != self.dimension:
            raise ValueError(
                f"Embedding dimension does not match index dimension {self.dimension}, so "
                "the index was not built and is as it was."
            )
        return vectors

    def build_index(self, embeddings: List[List[float]], track_ids: List[int]) -> None:
        """Replace what this object holds by an index of ``embeddings``.

        Every vector must have this index's dimension. When one does not, a
        ``ValueError`` is raised before anything is changed: the index object stays
        as it was. A caller that builds from the stored embeddings goes through
        ``index_rebuild.rebuild_index``, which tests that first.
        """
        if len(embeddings) != len(track_ids):
            raise ValueError("Number of embeddings must match number of track IDs")

        if not embeddings:
            logger.warning("No embeddings provided to build index")
            return

        vectors = self._checked_vectors(embeddings)
        self._create_index()
        self.track_ids = list(track_ids)

        vectors_normalized = self._normalize_vectors(vectors)
        ids = np.array(track_ids, dtype=np.int64)

        if self.index is not None:
            self.index.add_with_ids(vectors_normalized, ids)
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
        ids = np.array(track_ids, dtype=np.int64)

        if self.index is None:
            self._create_index()

        if self.index is not None:
            self.index.add_with_ids(vectors_normalized, ids)
            self.track_ids.extend(track_ids)
            logger.info(f"Added {len(track_ids)} vectors to index")

    def update_vectors(self, embeddings: List[List[float]], track_ids: List[int]) -> None:
        """Remove old vectors for *track_ids* and insert replacements.

        This avoids a full index rebuild when only a subset of embeddings change.
        """
        if not embeddings or not track_ids:
            return
        self.remove_vectors(track_ids)
        self.add_vectors(embeddings, track_ids)
        logger.info(f"Updated {len(track_ids)} vectors in index")

    def remove_vectors(self, track_ids: List[int]) -> None:
        """Remove vectors for the given track IDs from the index."""
        if not track_ids or self.index is None:
            return
        ids = np.array(track_ids, dtype=np.int64)
        n_removed = self.index.remove_ids(ids)
        removed_set = set(track_ids)
        self.track_ids = [tid for tid in self.track_ids if tid not in removed_set]
        logger.info(f"Removed {n_removed} vectors from index")

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

        filter_set = None
        if track_id_filter is not None:
            filter_set = (
                set(track_id_filter) if not isinstance(track_id_filter, set) else track_id_filter
            )
            if not filter_set:
                return []
            # Scan the whole index when filtering. A fixed 3x-k window starved the
            # candidate pool for selective filters (the matching tracks can sit far
            # down the ranking); with a flat IP index the search cost is the same.
            k_search = self.index.ntotal

        distances, indices = self.index.search(query_normalized, k_search)

        # With IndexIDMap, indices already contain track IDs (not positions).
        # A value of -1 means "no result at that rank".
        results = []
        for track_id_raw, distance in zip(indices[0], distances[0]):
            track_id = int(track_id_raw)
            if track_id < 0:
                continue
            if filter_set is None or track_id in filter_set:
                results.append((track_id, float(distance)))
                if len(results) >= k:
                    break

        logger.debug(f"Found {len(results)} results for search")
        return results

    def save_index(self, path: str) -> bool:
        """Write the index and its metadata to ``path``. Return True when they were written.

        False when there is nothing to save, and when a file at ``path`` could not
        be loaded and could not be moved aside: that file is never written over.
        """
        if self.index is None:
            logger.warning("Cannot save: index is None")
            return False

        save_path = Path(path).expanduser()
        metadata_path = save_path.with_suffix(".metadata")

        kept = [
            file
            for file in self.unmoved_paths
            if _same_file(file, save_path) or _same_file(file, metadata_path)
        ]
        if kept:
            logger.warning(
                "Not saving the search index to %s: %s could not be loaded and could not be "
                "moved aside, so it stays as it is and this run does not write over it.",
                save_path,
                " and ".join(str(file) for file in kept),
            )
            return False

        save_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp files and atomically replace, so an interrupted save can't
        # leave a half-written index/metadata pair. A partial file could not be
        # loaded again: load_index would move it aside and start with an empty index.
        tmp_index = save_path.parent / (save_path.name + ".tmp")
        tmp_metadata = metadata_path.parent / (metadata_path.name + ".tmp")

        _faiss().write_index(self.index, str(tmp_index))
        with open(tmp_metadata, "wb") as f:
            pickle.dump({"track_ids": self.track_ids, "dimension": self.dimension}, f)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_index, save_path)
        os.replace(tmp_metadata, metadata_path)

        logger.info(f"Saved FAISS index to {save_path}")
        return True

    def load_index(self, path: str) -> None:
        load_path = Path(path).expanduser()
        self.load_failed = False
        self.moved_paths = []

        if not load_path.exists():
            logger.warning(f"Index file not found: {load_path}")
            self._create_index()
            return

        # Above the try: a FAISS import failure inside it would be caught by the except
        # below, and the saved index would be moved aside as unreadable.
        faiss = _faiss()
        try:
            raw_index = faiss.read_index(str(load_path))

            metadata_path = load_path.with_suffix(".metadata")
            if metadata_path.exists():
                with open(metadata_path, "rb") as f:
                    metadata = pickle.load(f)
                    self.track_ids = metadata.get("track_ids", [])
                    loaded_dimension = metadata.get("dimension", self.dimension)

                    if loaded_dimension != self.dimension:
                        logger.warning(
                            f"Dimension mismatch: Existing embeddings have dimension {loaded_dimension}, "
                            f"but current provider expects {self.dimension}. "
                            f"You must regenerate embeddings to use the new provider."
                        )
                        self.dimension_mismatch = True
                        self.loaded_dimension = loaded_dimension
                    else:
                        self.dimension_mismatch = False
                        self.loaded_dimension = None
            else:
                # No metadata file: derive the dimension from the loaded index so a
                # wrong-dimension index is flagged here instead of crashing later
                # inside faiss during search/add_with_ids.
                index_dim = getattr(raw_index, "d", None)
                if index_dim is not None and index_dim != self.dimension:
                    logger.warning(
                        "Index dimension %s does not match current provider dimension %s "
                        "(no metadata file). Flagging for regeneration.",
                        index_dim,
                        self.dimension,
                    )
                    self.dimension_mismatch = True
                    self.loaded_dimension = index_dim

            # Migrate legacy IndexFlatIP → IndexIDMap(IndexFlatIP)
            if isinstance(raw_index, faiss.IndexFlatIP):
                n = raw_index.ntotal
                stored_dimension = int(raw_index.d)
                if n > 0 and (self.dimension_mismatch or stored_dimension != self.dimension):
                    # An older-format index that was made for another embedding
                    # dimension. Its vectors do not fit an index of the current
                    # dimension, and trying to add them raises. So it is not migrated,
                    # and the file is left alone: regenerating the embeddings writes its
                    # replacement. A warning is logged once: the checks above have
                    # logged theirs when they flagged the mismatch.
                    logger.log(
                        logging.INFO if self.dimension_mismatch else logging.WARNING,
                        "The saved index %s is in an older format and holds %d vectors of "
                        "dimension %s, but the current provider expects %s. It was not "
                        "migrated and the file was left as it is. You must regenerate "
                        "embeddings to use the new provider.",
                        load_path,
                        n,
                        stored_dimension,
                        self.dimension,
                    )
                    self._create_index()
                    self.track_ids = []
                    self.dimension_mismatch = True
                    if self.loaded_dimension is None:
                        self.loaded_dimension = stored_dimension
                elif n > 0 and len(self.track_ids) >= n:
                    logger.info("Migrating legacy IndexFlatIP to IndexIDMap")
                    self._create_index()
                    vectors = faiss.rev_swig_ptr(raw_index.get_xb(), n * raw_index.d)
                    vectors = np.reshape(vectors, (n, raw_index.d)).copy()
                    ids = np.array(self.track_ids[:n], dtype=np.int64)
                    self.index.add_with_ids(vectors, ids)  # type: ignore[union-attr]
                    self.track_ids = self.track_ids[:n]
                    logger.info(f"Migrated {n} vectors to IndexIDMap")
                elif n > 0:
                    # We have vectors but no usable track_id mapping — a bare
                    # IndexFlatIP cannot be migrated without it. Don't silently
                    # drop them (the old code produced an empty index while logging
                    # "Migrated N vectors"); flag for a rebuild from the database.
                    logger.warning(
                        "Legacy index has %d vectors but only %d track_ids; cannot migrate. "
                        "Flagging for rebuild from the database.",
                        n,
                        len(self.track_ids),
                    )
                    self._create_index()
                    self.track_ids = []
                    self.dimension_mismatch = True
                else:
                    self._create_index()
            else:
                self.index = raw_index

            logger.info(f"Loaded FAISS index from {load_path} with {len(self.track_ids)} vectors")
        except Exception as e:
            # Start with an empty index. If FAISS cannot even make one, the fault is
            # not in the files, and the error goes on before any file is touched.
            self._create_index()
            self.track_ids = []
            self.dimension_mismatch = False
            self.loaded_dimension = None
            self.load_failed = True
            self._move_unreadable_files_aside(load_path, e)

    def _move_unreadable_files_aside(self, load_path: Path, error: Exception) -> None:
        """Rename an index file that could not be loaded, together with its metadata file.

        Nothing is deleted: the stored embeddings can rebuild the index, and the user
        may want the old files. Both get the same ``.unreadable-<YYYYMMDD-HHMMSS>``
        ending (``moved_paths``). A file that cannot be renamed stays where it is and
        is remembered in ``unmoved_paths``, so that save_index does not write over it;
        a failed rename is not raised. One warning names the files and the command
        that rebuilds the index.
        """
        candidates = (load_path, load_path.with_suffix(".metadata"))
        files = [file for file in dict.fromkeys(candidates) if os.path.lexists(file)]
        ending = _unreadable_ending(files)

        unmoved: List[str] = []
        for file in files:
            try:
                self.moved_paths.append(_move_aside(file, ending))
            except Exception as rename_error:
                if not os.path.lexists(file):
                    # Gone since the load, so there is nothing left to protect.
                    continue
                if file not in self.unmoved_paths:
                    self.unmoved_paths.append(file)
                unmoved.append(f"{file} ({_reason(rename_error)})")

        parts = [f"The search index {load_path} could not be loaded ({_reason(error)})."]
        if self.moved_paths:
            parts.append(
                "Moved aside, not deleted: "
                + " and ".join(str(file) for file in self.moved_paths)
                + "."
            )
        if unmoved:
            parts.append(
                "Could not be moved aside, so left where it is and not written over in this "
                "run: " + " and ".join(unmoved) + ". Move it away yourself before you "
                "rebuild the index."
            )
        parts.append(
            f"The stored embeddings are not affected. Run '{_REBUILD_COMMAND}' to rebuild "
            "the search index."
        )
        logger.warning("%s", " ".join(parts))
