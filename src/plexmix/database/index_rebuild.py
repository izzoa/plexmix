"""The one place that builds the search index from the stored embeddings and saves it.

An index that is built from the rows takes the place of the saved one, so it may be
saved only when it is complete. Complete means: every track that holds any
embedding row holds a row of the active embedding model whose size is the
dimension of the index. After a change of the embedding provider, of the model or
of the vector size, the rows of the tracks that were not yet replaced are of another
model or size. An index of the rows that fit would then cover only part of the
library, and with the saved index would go the dimension mismatch that tells every
command and the web UI to regenerate.

So when even one track is in the way, ``rebuild_index`` builds nothing and saves
nothing. The saved files stay as they are, byte for byte, the index object stays as
it was loaded, and the caller is told how many tracks are in the way. A row whose
track is gone does not count, and neither does a track without any embedding row.

Every caller that builds the index from the database goes through ``rebuild_index``:
the sync engine, the CLI commands and the web UI. This module imports the database
manager and the index, and nothing from ``plexmix.plex``, the services, the CLI or
the web UI.
"""

import contextlib
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .sqlite_manager import SQLiteManager
from .vector_index import VectorIndex

logger = logging.getLogger(__name__)

# The command that replaces every stored embedding with one of the current settings.
REGENERATE_COMMAND = "plexmix embeddings generate --regenerate"

# Track ids per query when rows are looked up by id.
_IDS_PER_QUERY = 500


@dataclass(frozen=True)
class IndexRebuild:
    """What one ``rebuild_index`` call did.

    ``left_out`` is the number of tracks in the way: tracks that hold an embedding
    and none of the active model and size. When it is not 0, nothing was built and
    nothing was saved. ``vectors`` is the number of vectors in the index that was
    built (0 when none was). ``saved`` is True only when the index files were
    written: it is False when nothing was built, when the index has no path, and
    when a file that could not be loaded is still in the way of the save.
    """

    vectors: int = 0
    left_out: int = 0
    saved: bool = False

    @property
    def refused(self) -> bool:
        """True when tracks were in the way, so the saved index was left as it is."""
        return self.left_out > 0

    @property
    def sentence(self) -> str:
        """What to tell the user about a refused rebuild; "" when none was refused."""
        return left_out_sentence(self.left_out)


def left_out_sentence(count: int) -> str:
    """The fixed sentence for ``count`` tracks in the way of the search index; "" for none.

    It names the count and the command that replaces the embeddings. For the log
    and for every caller, so that the CLI and the web UI say the same.
    """
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return ""
    if count == 1:
        return (
            "The search index was left as it is: 1 track holds an embedding that was made "
            f"with other embedding settings. Run '{REGENERATE_COMMAND}' to replace it."
        )
    return (
        f"The search index was left as it is: {count:,} tracks hold an embedding that was "
        f"made with other embedding settings. Run '{REGENERATE_COMMAND}' to replace them."
    )


def tracks_in_the_way(
    db: SQLiteManager, vector_index: VectorIndex, embedding_model: Optional[str]
) -> int:
    """How many tracks keep an index of ``embedding_model`` from being complete.

    A track that holds an embedding row, and none of ``embedding_model`` with the
    dimension of ``vector_index``. Read-only: nothing is built. 0 means that
    ``rebuild_index`` would build and save.
    """
    return _as_count(
        db.count_tracks_with_only_other_embeddings(embedding_model, _dimension_of(vector_index))
    )


def rebuild_index(
    db: SQLiteManager,
    vector_index: VectorIndex,
    embedding_model: Optional[str],
    index_path: Optional[str] = None,
) -> IndexRebuild:
    """Build the search index from the rows of ``embedding_model`` and save it, if complete.

    When a track is in the way (see the module text), nothing is built and nothing
    is saved: the result gives the count in ``left_out``, the saved files and the
    index object are untouched, and one warning with ``left_out_sentence`` is
    logged. With no row at all, nothing is built either, and ``left_out`` is 0.

    Otherwise the index is built from every row and saved to ``index_path``, or to
    the path of the index object when none is given. ``embedding_model`` None
    builds from the rows of every model.

    The result is also kept on the index object as ``last_rebuild``, for a caller
    that reached this function through a wrapper that returns a number.
    """
    result = _rebuild(db, vector_index, embedding_model, index_path)
    # An object that takes no attribute gets none: the result is returned anyway.
    with contextlib.suppress(AttributeError, TypeError):
        vector_index.last_rebuild = result
    return result


def _rebuild(
    db: SQLiteManager,
    vector_index: VectorIndex,
    embedding_model: Optional[str],
    index_path: Optional[str],
) -> IndexRebuild:
    dimension = _dimension_of(vector_index)
    left_out = _as_count(db.count_tracks_with_only_other_embeddings(embedding_model, dimension))
    rows: List[Tuple[int, List[float]]] = []
    if not left_out:
        rows, left_out = _rows_that_fit(db, embedding_model, dimension)
    if left_out:
        logger.warning("%s", left_out_sentence(left_out))
        return IndexRebuild(left_out=left_out)
    if not rows:
        return IndexRebuild()

    vector_index.build_index([vector for _, vector in rows], [track_id for track_id, _ in rows])
    path: Any = index_path if index_path is not None else getattr(vector_index, "index_path", None)
    # ``save_index`` answers False when it wrote nothing.
    saved = path is not None and vector_index.save_index(str(path)) is not False
    return IndexRebuild(vectors=len(rows), saved=saved)


def _rows_that_fit(
    db: SQLiteManager, embedding_model: Optional[str], dimension: Optional[int]
) -> Tuple[List[Tuple[int, List[float]]], int]:
    """The rows to build from, one per track, and the tracks whose stored vector is in the way.

    The count query compares the size each row states. This reads the vectors
    themselves, so a row that states one size and holds another keeps the index
    from being built as well. A row of another size whose track is gone is left out.
    """
    fitting: Dict[int, List[float]] = {}
    other_size: Set[int] = set()
    for track_id, vector in db.get_all_embeddings(embedding_model):
        if dimension is None or _size_of(vector) == dimension:
            fitting.setdefault(track_id, vector)
        else:
            other_size.add(track_id)
    other_size -= set(fitting)
    return list(fitting.items()), _existing_tracks(db, other_size)


def _existing_tracks(db: SQLiteManager, track_ids: Iterable[int]) -> int:
    """How many of ``track_ids`` are tracks of the library."""
    ids = sorted(track_ids)
    found = 0
    for start in range(0, len(ids), _IDS_PER_QUERY):
        found += len(db.get_tracks_by_ids(ids[start : start + _IDS_PER_QUERY]))
    return found


def _dimension_of(vector_index: Any) -> Optional[int]:
    """The dimension of the index; None for an object that states none (a test double)."""
    dimension = getattr(vector_index, "dimension", None)
    if isinstance(dimension, int) and not isinstance(dimension, bool):
        return dimension
    return None


def _size_of(vector: Any) -> Optional[int]:
    try:
        return len(vector)
    except TypeError:
        return None


def _as_count(value: Any) -> int:
    """``value`` as a count; 0 for anything that is not a whole number (a test double)."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0
