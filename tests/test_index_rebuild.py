"""The search index is rebuilt from the stored embeddings only when it is complete.

``plexmix.database.index_rebuild.rebuild_index`` is the one function that builds the
index from the database and saves it. When even one track holds an embedding of
another model or vector size, it builds nothing and saves nothing: the saved files
stay as they are, byte for byte, and a new index object that opens them still
reports the dimension mismatch that tells every command to regenerate.

Every test uses a temporary database and real index files. No provider is built.
"""

import ast
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from plexmix.database import index_rebuild
from plexmix.database import vector_index as vector_index_module
from plexmix.database.index_rebuild import (
    REGENERATE_COMMAND,
    IndexRebuild,
    left_out_sentence,
    rebuild_index,
    tracks_in_the_way,
)
from plexmix.database.models import Album, Artist, Embedding, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex import sync as sync_module
from plexmix.services import tagging_service

MODEL = "custom"
OTHER_MODEL = "openai"
OLD_SIZE = 4
NEW_SIZE = 8
LOGGER_NAME = "plexmix.database.index_rebuild"
SRC = Path(__file__).resolve().parent.parent / "src" / "plexmix"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[SQLiteManager]:
    manager = SQLiteManager(str(tmp_path / "plexmix.db"))
    manager.connect()
    yield manager
    manager.close()


def _tracks(db: SQLiteManager, count: int) -> List[int]:
    artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist One"))
    album_id = db.insert_album(Album(plex_key="al1", title="Album One", artist_id=artist_id))
    return [
        db.insert_track(
            Track(plex_key=f"t{n}", title=f"Song {n}", artist_id=artist_id, album_id=album_id)
        )
        for n in range(count)
    ]


def _embed(
    db: SQLiteManager,
    track_ids: List[int],
    size: int = OLD_SIZE,
    model: str = MODEL,
    stated_size: Optional[int] = None,
) -> None:
    """Give every track a row of ``model`` whose vector holds ``size`` numbers."""
    for track_id in track_ids:
        db.insert_embedding(
            Embedding(
                track_id=track_id,
                embedding_model=model,
                embedding_dim=stated_size if stated_size is not None else size,
                vector=[0.1 * (n + 1) for n in range(size)],
                updated_at=datetime(2020, 1, 1, 9, 0, 0),
            )
        )


def _index_path(db: SQLiteManager) -> Path:
    return db.db_path.parent / "embeddings.index"


def _save_index(db: SQLiteManager, track_ids: List[int], size: int = OLD_SIZE) -> None:
    """Save the library's index as it was made before the settings changed."""
    index = VectorIndex(dimension=size, index_path=str(_index_path(db)))
    index.build_index([[0.1 * (n + 1) for n in range(size)] for _ in track_ids], track_ids)
    assert index.save_index(str(_index_path(db))) is True


def _files(db: SQLiteManager) -> Dict[str, bytes]:
    return {
        item.name: item.read_bytes()
        for item in sorted(db.db_path.parent.iterdir())
        if item.name.startswith("embeddings.")
    }


def _open(db: SQLiteManager, size: int) -> VectorIndex:
    return VectorIndex(dimension=size, index_path=str(_index_path(db)))


def _assert_left_as_loaded(db: SQLiteManager, index: VectorIndex, before: Dict[str, bytes]) -> None:
    """Nothing was built and nothing was saved after a size change from 4 to 8."""
    assert _files(db) == before
    # The object is as it was loaded: the vectors of the saved index and its flag.
    assert index.dimension_mismatch is True and index.loaded_dimension == OLD_SIZE
    assert index.index.d == OLD_SIZE and index.index.ntotal == len(index.track_ids)
    # Every command that opens the files still sees that they do not fit.
    reopened = _open(db, NEW_SIZE)
    assert reopened.dimension_mismatch is True
    assert sorted(reopened.track_ids) == sorted(index.track_ids)


# ---------------------------------------------------------------------------
# The sentence
# ---------------------------------------------------------------------------


def test_sentence_names_the_count_and_the_command() -> None:
    assert REGENERATE_COMMAND == "plexmix embeddings generate --regenerate"
    assert left_out_sentence(380) == (
        "The search index was left as it is: 380 tracks hold an embedding that was made "
        "with other embedding settings. Run 'plexmix embeddings generate --regenerate' to "
        "replace them."
    )
    assert left_out_sentence(1) == (
        "The search index was left as it is: 1 track holds an embedding that was made "
        "with other embedding settings. Run 'plexmix embeddings generate --regenerate' to "
        "replace it."
    )
    assert left_out_sentence(5943).startswith("The search index was left as it is: 5,943 tracks")


@pytest.mark.parametrize("count", [0, -3, None, True, "7", MagicMock()])
def test_no_sentence_without_a_count(count: object) -> None:
    assert left_out_sentence(count) == ""  # type: ignore[arg-type]


def test_result_reads_as_refused_only_with_tracks_in_the_way() -> None:
    nothing = IndexRebuild()
    assert (nothing.vectors, nothing.left_out, nothing.saved) == (0, 0, False)
    assert nothing.refused is False and nothing.sentence == ""
    built = IndexRebuild(vectors=9, saved=True)
    assert built.refused is False and built.sentence == ""
    refused = IndexRebuild(left_out=2)
    assert refused.refused is True and refused.sentence == left_out_sentence(2)


# ---------------------------------------------------------------------------
# A complete index is built and saved
# ---------------------------------------------------------------------------


def test_complete_index_is_built_and_saved(db: SQLiteManager) -> None:
    ids = _tracks(db, 5)
    _embed(db, ids)
    index = _open(db, OLD_SIZE)

    result = rebuild_index(db, index, MODEL)

    assert result == IndexRebuild(vectors=5, left_out=0, saved=True)
    assert index.last_rebuild is result
    assert sorted(index.track_ids) == ids and index.index.ntotal == 5
    reopened = _open(db, OLD_SIZE)
    assert sorted(reopened.track_ids) == ids and reopened.dimension_mismatch is False


def test_track_without_any_embedding_is_not_in_the_way(db: SQLiteManager) -> None:
    """A library that is half embedded gets an index of that half, as it always did."""
    ids = _tracks(db, 6)
    _embed(db, ids[:3])
    index = _open(db, OLD_SIZE)

    assert tracks_in_the_way(db, index, MODEL) == 0
    assert rebuild_index(db, index, MODEL) == IndexRebuild(vectors=3, saved=True)
    assert sorted(_open(db, OLD_SIZE).track_ids) == ids[:3]


def test_rows_of_another_model_do_not_count_beside_a_row_that_fits(db: SQLiteManager) -> None:
    ids = _tracks(db, 3)
    _embed(db, ids)
    _embed(db, ids, size=16, model=OTHER_MODEL)
    index = _open(db, OLD_SIZE)

    assert rebuild_index(db, index, MODEL) == IndexRebuild(vectors=3, saved=True)
    assert sorted(_open(db, OLD_SIZE).track_ids) == ids


def test_index_shrinks_when_tracks_were_removed(db: SQLiteManager) -> None:
    """Their rows are gone with them, so nothing is in the way of a smaller index."""
    ids = _tracks(db, 6)
    _embed(db, ids)
    _save_index(db, ids)
    for track_id in ids[4:]:
        db.delete_track(track_id)
    index = _open(db, OLD_SIZE)
    assert sorted(index.track_ids) == ids

    result = rebuild_index(db, index, MODEL)

    assert result == IndexRebuild(vectors=4, saved=True)
    assert sorted(_open(db, OLD_SIZE).track_ids) == ids[:4]


def test_row_whose_track_is_gone_is_not_in_the_way(db: SQLiteManager) -> None:
    ids = _tracks(db, 3)
    _embed(db, ids, size=NEW_SIZE)
    conn = db.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    # Two rows without a track: one of the earlier size, one of another model.
    _embed(db, [901], size=OLD_SIZE)
    _embed(db, [902], size=16, model=OTHER_MODEL)
    conn.execute("PRAGMA foreign_keys = ON")
    index = _open(db, NEW_SIZE)

    assert tracks_in_the_way(db, index, MODEL) == 0
    result = rebuild_index(db, index, MODEL)

    assert result == IndexRebuild(vectors=3, saved=True)
    # The row of the earlier size cannot go into the index and is left out of it.
    assert sorted(_open(db, NEW_SIZE).track_ids) == ids


def test_no_row_at_all_builds_nothing_and_saves_nothing(db: SQLiteManager) -> None:
    ids = _tracks(db, 3)
    _embed(db, ids)
    _save_index(db, ids)
    before = _files(db)
    index = _open(db, OLD_SIZE)

    result = rebuild_index(db, index, "a model without rows of its own")
    # Those three tracks hold an embedding, and none of that model.
    assert result == IndexRebuild(left_out=3)

    db.get_connection().execute("DELETE FROM embeddings")
    db.get_connection().commit()
    assert rebuild_index(db, index, MODEL) == IndexRebuild()
    assert _files(db) == before and sorted(index.track_ids) == ids


# ---------------------------------------------------------------------------
# Tracks in the way: nothing is built, nothing is saved
# ---------------------------------------------------------------------------


def test_two_sizes_under_one_model_name_leave_the_saved_index(
    db: SQLiteManager, caplog: pytest.LogCaptureFixture
) -> None:
    """Eight tracks hold 4 numbers, the provider now makes 8, and one track is new."""
    ids = _tracks(db, 9)
    _embed(db, ids[:8], size=OLD_SIZE)
    _save_index(db, ids[:8])
    _embed(db, ids[8:], size=NEW_SIZE)
    before = _files(db)
    index = _open(db, NEW_SIZE)
    assert index.dimension_mismatch is True

    assert tracks_in_the_way(db, index, MODEL) == 8
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = rebuild_index(db, index, MODEL)

    assert result == IndexRebuild(vectors=0, left_out=8, saved=False)
    assert result.refused is True and index.last_rebuild is result
    said = [record.getMessage() for record in caplog.records if record.name == LOGGER_NAME]
    assert said == [left_out_sentence(8)]
    _assert_left_as_loaded(db, index, before)


def test_rows_that_all_have_another_size_are_no_error(db: SQLiteManager) -> None:
    """0.13.0 raised here. Now the count is returned and nothing is touched."""
    ids = _tracks(db, 5)
    _embed(db, ids, size=OLD_SIZE)
    _save_index(db, ids)
    before = _files(db)
    index = _open(db, NEW_SIZE)

    result = rebuild_index(db, index, MODEL)

    assert result == IndexRebuild(left_out=5)
    _assert_left_as_loaded(db, index, before)


def test_changed_provider_name_with_only_some_tracks_replaced(db: SQLiteManager) -> None:
    """Same vector size, another provider: three of eight tracks have a row of it."""
    ids = _tracks(db, 8)
    _embed(db, ids, model=OTHER_MODEL)
    _save_index(db, ids)
    _embed(db, ids[:3], model=MODEL)
    before = _files(db)
    index = _open(db, OLD_SIZE)
    # The size did not change, so nothing but this rule protects the saved index.
    assert index.dimension_mismatch is False

    result = rebuild_index(db, index, MODEL)

    assert result == IndexRebuild(left_out=5)
    assert _files(db) == before
    assert sorted(index.track_ids) == ids and index.index.ntotal == 8
    assert sorted(_open(db, OLD_SIZE).track_ids) == ids


def test_index_is_built_once_every_track_has_a_row_that_fits(db: SQLiteManager) -> None:
    ids = _tracks(db, 6)
    _embed(db, ids, size=OLD_SIZE)
    _save_index(db, ids)
    index = _open(db, NEW_SIZE)
    # The regenerate run replaces the rows one after the other.
    for done, track_id in enumerate(ids, start=1):
        before = _files(db)
        _embed(db, [track_id], size=NEW_SIZE)
        result = rebuild_index(db, index, MODEL)
        if done < len(ids):
            assert result == IndexRebuild(left_out=len(ids) - done)
            assert _files(db) == before
            assert _open(db, NEW_SIZE).dimension_mismatch is True

    assert result == IndexRebuild(vectors=6, saved=True)
    reopened = _open(db, NEW_SIZE)
    assert reopened.dimension_mismatch is False
    assert sorted(reopened.track_ids) == ids and reopened.index.d == NEW_SIZE


def test_row_that_states_another_size_than_it_holds_is_in_the_way(db: SQLiteManager) -> None:
    """The count query reads the stated size. The vectors themselves are read too."""
    ids = _tracks(db, 4)
    _embed(db, ids[:3], size=NEW_SIZE)
    _embed(db, ids[3:], size=OLD_SIZE, stated_size=NEW_SIZE)
    _save_index(db, ids)
    before = _files(db)
    index = _open(db, NEW_SIZE)
    assert db.count_tracks_with_only_other_embeddings(MODEL, NEW_SIZE) == 0

    result = rebuild_index(db, index, MODEL)

    assert result == IndexRebuild(left_out=1)
    _assert_left_as_loaded(db, index, before)


def test_refusal_takes_no_vector_into_memory(db: SQLiteManager) -> None:
    """With tracks in the way the answer comes from one count, not from every vector."""
    ids = _tracks(db, 4)
    _embed(db, ids, size=OLD_SIZE)
    index = _open(db, NEW_SIZE)

    with patch.object(db, "get_all_embeddings", side_effect=AssertionError("not read")):
        assert rebuild_index(db, index, MODEL) == IndexRebuild(left_out=4)
        assert tracks_in_the_way(db, index, MODEL) == 4


# ---------------------------------------------------------------------------
# Whether the files were written
# ---------------------------------------------------------------------------


def test_index_without_a_path_is_built_and_not_saved(db: SQLiteManager) -> None:
    ids = _tracks(db, 3)
    _embed(db, ids)
    index = VectorIndex(dimension=OLD_SIZE)

    assert rebuild_index(db, index, MODEL) == IndexRebuild(vectors=3, saved=False)
    assert sorted(index.track_ids) == ids and _files(db) == {}


def test_given_path_is_saved_to(db: SQLiteManager, tmp_path: Path) -> None:
    ids = _tracks(db, 3)
    _embed(db, ids)
    index = VectorIndex(dimension=OLD_SIZE)
    target = tmp_path / "elsewhere" / "copy.index"

    result = rebuild_index(db, index, MODEL, index_path=str(target))

    assert result == IndexRebuild(vectors=3, saved=True)
    assert sorted(VectorIndex(dimension=OLD_SIZE, index_path=str(target)).track_ids) == ids


def test_unreadable_file_that_could_not_be_moved_aside_is_not_written_over(
    db: SQLiteManager,
) -> None:
    ids = _tracks(db, 3)
    _embed(db, ids)
    _index_path(db).write_bytes(b"not a search index")
    with patch.object(vector_index_module.os, "rename", side_effect=PermissionError(13, "no")):
        index = _open(db, OLD_SIZE)
    assert index.load_failed is True and index.unmoved_paths == [_index_path(db)]

    result = rebuild_index(db, index, MODEL)

    # The index was built in memory, and the files were not written.
    assert result == IndexRebuild(vectors=3, left_out=0, saved=False)
    assert _index_path(db).read_bytes() == b"not a search index"


# ---------------------------------------------------------------------------
# Rows of every model, and objects that are not what they should be
# ---------------------------------------------------------------------------


def test_without_a_model_the_rows_of_every_model_count(db: SQLiteManager) -> None:
    ids = _tracks(db, 4)
    _embed(db, ids[:2], model=MODEL)
    _embed(db, ids[1:], model=OTHER_MODEL)
    index = _open(db, OLD_SIZE)

    result = rebuild_index(db, index, None)

    # Track 2 holds a row of both models and is in the index once.
    assert result == IndexRebuild(vectors=4, saved=True)
    assert sorted(index.track_ids) == ids and index.index.ntotal == 4

    _embed(db, ids[3:], size=NEW_SIZE, model=OTHER_MODEL)
    assert rebuild_index(db, index, None) == IndexRebuild(left_out=1)


def test_test_doubles_are_not_taken_for_tracks_in_the_way() -> None:
    """A mock database answers with mocks. That is no count and no row."""
    index = MagicMock()
    result = rebuild_index(MagicMock(), index, MODEL)

    assert result == IndexRebuild()
    index.build_index.assert_not_called()
    index.save_index.assert_not_called()
    assert index.last_rebuild is result
    assert tracks_in_the_way(MagicMock(), MagicMock(), MODEL) == 0


def test_index_double_without_a_dimension_is_built_from_the_rows_of_the_model(
    db: SQLiteManager,
) -> None:
    ids = _tracks(db, 3)
    _embed(db, ids[:2])
    _embed(db, ids[2:], model=OTHER_MODEL)
    index = MagicMock()
    index.index_path = "/somewhere/embeddings.index"

    # Without a stated size only the model is compared: one track is in the way.
    assert rebuild_index(db, index, MODEL) == IndexRebuild(left_out=1)
    index.build_index.assert_not_called()

    _embed(db, ids[2:])
    assert rebuild_index(db, index, MODEL) == IndexRebuild(vectors=3, saved=True)
    index.build_index.assert_called_once()
    index.save_index.assert_called_once_with("/somewhere/embeddings.index")


def test_result_is_returned_for_an_object_that_takes_no_attribute(db: SQLiteManager) -> None:
    class Slotted:
        __slots__ = ("dimension", "index_path")

        def __init__(self) -> None:
            self.dimension = OLD_SIZE
            self.index_path = None

        def build_index(self, embeddings: List[List[float]], track_ids: List[int]) -> None:
            return None

    _embed(db, _tracks(db, 2))
    assert rebuild_index(db, Slotted(), MODEL) == IndexRebuild(vectors=2)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The count query
# ---------------------------------------------------------------------------


def test_count_of_tracks_with_only_other_embeddings(db: SQLiteManager) -> None:
    ids = _tracks(db, 6)
    _embed(db, ids[:2], size=NEW_SIZE)  # fits
    _embed(db, ids[2:4], size=OLD_SIZE)  # the model, another size
    _embed(db, ids[4:5], size=NEW_SIZE, model=OTHER_MODEL)  # the size, another model
    # ids[5] holds no embedding at all.
    count = db.count_tracks_with_only_other_embeddings

    assert count(MODEL, NEW_SIZE) == 3
    assert count(MODEL, OLD_SIZE) == 3
    assert count(OTHER_MODEL, NEW_SIZE) == 4
    # None leaves that part of the test out.
    assert count(MODEL, None) == 1
    assert count(None, NEW_SIZE) == 2
    assert count(None, None) == 0 and count() == 0
    assert count("a model without rows", NEW_SIZE) == 5


def test_count_reads_and_never_writes(db: SQLiteManager) -> None:
    ids = _tracks(db, 2)
    _embed(db, ids)
    changes = db.get_connection().total_changes
    assert db.count_tracks_with_only_other_embeddings(MODEL, NEW_SIZE) == 2
    assert db.get_connection().total_changes == changes


def test_model_name_is_a_parameter_not_part_of_the_statement(db: SQLiteManager) -> None:
    ids = _tracks(db, 2)
    _embed(db, ids)
    hostile = "x' OR '1'='1"
    assert db.count_tracks_with_only_other_embeddings(hostile, OLD_SIZE) == 2
    assert len(db.get_all_tracks()) == 2


# ---------------------------------------------------------------------------
# The wrappers that other code imports
# ---------------------------------------------------------------------------


def test_wrappers_return_the_number_of_vectors_and_keep_the_whole_result(
    db: SQLiteManager,
) -> None:
    ids = _tracks(db, 9)
    _embed(db, ids[:8], size=OLD_SIZE)
    _save_index(db, ids[:8])
    _embed(db, ids[8:], size=NEW_SIZE)
    before = _files(db)
    path = str(_index_path(db))

    index = _open(db, NEW_SIZE)
    assert tagging_service.rebuild_vector_index(db, index, path, MODEL) == 0
    assert index.last_rebuild == IndexRebuild(left_out=8)
    _assert_left_as_loaded(db, index, before)

    index = _open(db, NEW_SIZE)
    assert sync_module.rebuild_index_from_rows(db, index, MODEL) == 0
    assert index.last_rebuild == IndexRebuild(left_out=8)
    _assert_left_as_loaded(db, index, before)

    index = _open(db, NEW_SIZE)
    assert sync_module.index_may_follow_rows(db, index, MODEL) is False
    assert index.last_rebuild is None  # a question, not a rebuild
    assert sync_module.follow_rows_after_stopped_pass(db, index, MODEL) is False
    assert index.last_rebuild == IndexRebuild(left_out=8)
    _assert_left_as_loaded(db, index, before)

    index = _open(db, NEW_SIZE)
    assert sync_module.finish_embeddings_pass(db, index, MODEL) == 0
    assert index.last_rebuild == IndexRebuild(left_out=8)
    _assert_left_as_loaded(db, index, before)

    # Every track has a row that fits: each wrapper builds and saves all nine.
    _embed(db, ids[:8], size=NEW_SIZE)
    index = _open(db, NEW_SIZE)
    assert sync_module.index_may_follow_rows(db, index, MODEL) is True
    assert tagging_service.rebuild_vector_index(db, index, path, MODEL) == 9
    assert index.last_rebuild == IndexRebuild(vectors=9, saved=True)
    assert _open(db, NEW_SIZE).dimension_mismatch is False
    assert sync_module.rebuild_index_from_rows(db, _open(db, NEW_SIZE), MODEL) == 9
    assert sync_module.follow_rows_after_stopped_pass(db, _open(db, NEW_SIZE), MODEL) is True
    assert sorted(_open(db, NEW_SIZE).track_ids) == ids


def test_plain_index_object_starts_without_a_rebuild_result() -> None:
    assert VectorIndex(dimension=OLD_SIZE).last_rebuild is None
    assert not hasattr(VectorIndex(dimension=OLD_SIZE), "skipped_vectors")


# ---------------------------------------------------------------------------
# One place builds the index from the database
# ---------------------------------------------------------------------------


def _calls(path: Path, method: str) -> List[int]:
    """The lines of ``path`` that call ``<something>.<method>(...)``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
    ]


@pytest.mark.parametrize(
    "module",
    ["plex/sync.py", "services/tagging_service.py", "services/regenerate_service.py"],
)
def test_engine_and_services_never_build_the_index_themselves(module: str) -> None:
    """They hand the build to ``rebuild_index``, which holds the rule."""
    assert _calls(SRC / module, "build_index") == []


def test_only_the_incremental_update_and_the_removal_save_in_the_engine() -> None:
    """Two saves are left in ``plex/sync.py``, and neither follows a build from the rows:
    the update of an index that loaded cleanly, and the drop of removed tracks."""
    assert len(_calls(SRC / "plex" / "sync.py", "save_index")) == 2
    assert _calls(SRC / "services" / "tagging_service.py", "save_index") == []


def test_nothing_outside_the_database_package_builds_or_saves_the_index_but_the_engine() -> None:
    """The CLI, the web UI and the services call ``rebuild_index`` (or a wrapper): no module
    outside ``plexmix/database`` calls ``build_index``, and ``save_index`` is called only by
    the two writers of ``plex/sync.py`` that the one rule does not cover."""
    builders: List[str] = []
    savers: List[str] = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith("database/"):
            continue
        builders += [f"{relative}:{line}" for line in _calls(path, "build_index")]
        savers += [f"{relative}:{line}" for line in _calls(path, "save_index")]
    assert builders == []
    assert sorted(name.split(":")[0] for name in savers) == ["plex/sync.py", "plex/sync.py"]


def test_the_module_imports_only_the_database_and_the_index() -> None:
    code = (
        "import sys\n"
        "import plexmix.database.index_rebuild\n"
        "pulled = sorted(name for name in sys.modules if name.startswith(("
        "'plexmix.plex', 'plexmix.services', 'plexmix.cli', 'plexmix.ui')))\n"
        "assert not pulled, pulled\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert index_rebuild.rebuild_index is rebuild_index
