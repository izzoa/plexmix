"""A sync removes a track only when Plex's own listing leaves it out.

The engine side of the sync deletion fix: unread tracks are kept, a stopped read
writes nothing, many failed reads block removals, a large removal is held or backed
up first, and a confirmed removal checks Plex again.

Two stand-ins are used, and neither makes a request. ``_real_client`` is the real
``PlexClient`` on top of a fake music library whose items can fail when a detail is
read, so the client and the engine are tested together. ``_FakePlex`` hands out
ready model objects with a read report, for the tests that only need a listing.
"""

import logging
import sqlite3
from pathlib import Path
from threading import Event
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest
from plexapi.exceptions import BadRequest

from plexmix.database.backup import BackupError, backup_library
from plexmix.database.models import Album, Artist, Embedding, Playlist, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex.client import PlexClient, PlexReadInterrupted, PlexReadReport
from plexmix.plex.sync import (
    MAX_FAILED_READS,
    MAX_FAILED_READS_PERCENT,
    REMOVAL_CONFIRM_PERCENT,
    REMOVAL_CONFIRM_TRACKS,
    REMOVAL_FAILED,
    RemovalResult,
    SyncEngine,
    SyncOutcome,
    removal_needs_confirmation,
    too_many_failed_reads,
)

# The only "secrets" in this file are the project's dummy values.
DUMMY_TOKEN = "dummy-plex-token-0000"
DUMMY_PASSWORD = "dummy-ui-password-0000"
PLEX_URL = f"http://leakuser:{DUMMY_PASSWORD}@127.0.0.1:9"

ARTIST_KEY = "a1"
ALBUM_KEY = "al1"
EMBEDDING_MODEL = "test-model"
DIMENSION = 4


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Iterator[SQLiteManager]:
    """A temporary database, opened the way the application opens it (cascades on)."""
    manager = SQLiteManager(str(tmp_path / "plexmix.db"))
    manager.connect()
    yield manager
    manager.close()


def _title(key: str) -> str:
    return f"Song {key}"


def _keys(count: int, start: int = 0) -> List[str]:
    return [f"t{n}" for n in range(start, start + count)]


def _seed(db: SQLiteManager, keys: Iterable[str]) -> Dict[str, int]:
    """Put one artist, one album and a tagged track per key into the database."""
    artist_id = db.insert_artist(Artist(plex_key=ARTIST_KEY, name="Artist One"))
    album_id = db.insert_album(Album(plex_key=ALBUM_KEY, title="Album One", artist_id=artist_id))
    ids: Dict[str, int] = {}
    with db.deferred_commits():
        for key in keys:
            ids[key] = db.insert_track(
                Track(
                    plex_key=key,
                    title=_title(key),
                    artist_id=artist_id,
                    album_id=album_id,
                    play_count=0,
                    tags=f"tag-{key}",
                    environments="study",
                    instruments="piano",
                )
            )
    return ids


def _vector(track_id: int) -> List[float]:
    return [float(track_id), 1.0, 0.5, 0.25]


def _add_embeddings_and_playlist(db: SQLiteManager, ids: Dict[str, int]) -> int:
    """Give every track an embedding row and a place in one saved playlist."""
    db.insert_embeddings_batch(
        [
            Embedding(
                track_id=track_id,
                embedding_model=EMBEDDING_MODEL,
                embedding_dim=DIMENSION,
                vector=_vector(track_id),
            )
            for track_id in ids.values()
        ]
    )
    playlist_id = db.insert_playlist(Playlist(name="Saved playlist"))
    db.add_tracks_to_playlist(playlist_id, list(ids.values()))
    return playlist_id


def _rows(db: SQLiteManager, table: str) -> List[Tuple[Any, ...]]:
    cursor = db.get_connection().cursor()
    cursor.execute(f"SELECT * FROM {table} ORDER BY 1, 2")
    return [tuple(row) for row in cursor.fetchall()]


def _snapshot(db: SQLiteManager) -> Dict[str, List[Tuple[Any, ...]]]:
    """Every row a sync could add, change or remove."""
    tables = ("artists", "albums", "tracks", "genres", "embeddings", "playlist_tracks")
    return {table: _rows(db, table) for table in tables}


def _track_keys(db: SQLiteManager) -> List[str]:
    return sorted(track.plex_key for track in db.get_all_tracks())


def _history(db: SQLiteManager) -> List[Dict[str, Any]]:
    cursor = db.get_connection().cursor()
    cursor.execute("SELECT * FROM sync_history ORDER BY id")
    return [dict(row) for row in cursor.fetchall()]


def _backup_folders(db: SQLiteManager) -> List[Path]:
    backups = db.db_path.parent / "backups"
    return sorted(backups.iterdir()) if backups.is_dir() else []


def _tracks_in_copy(database_file: Path) -> int:
    """Count the tracks in a backup copy without writing anything into its folder."""
    assert database_file.is_file()
    # ``immutable`` keeps SQLite from creating "-wal" and "-shm" files beside the copy.
    connection = sqlite3.connect(f"{database_file.as_uri()}?immutable=1", uri=True)
    try:
        count: int = connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        return count
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Stand-in 1: the real PlexClient on a fake music library
# ---------------------------------------------------------------------------


class _Item:
    """One listed Plex item. Its rating key is always there; reading its details can fail.

    ``errors`` are raised one per read attempt, in order, before the item answers:
    every attempt starts by reading ``title``, as the real client does.
    """

    genres: List[Any] = []

    def __init__(
        self, key: str, title: Optional[str] = None, errors: Iterable[BaseException] = ()
    ) -> None:
        self.ratingKey = key
        self.grandparentRatingKey = ARTIST_KEY
        self.parentRatingKey = ALBUM_KEY
        self._title = _title(key) if title is None else title
        self._errors = list(errors)

    @property
    def title(self) -> str:
        if self._errors:
            raise self._errors.pop(0)
        return self._title


class _Library:
    """A music library that lists the items it was given, or fails to list one kind."""

    def __init__(
        self,
        tracks: Iterable[_Item],
        artists: Optional[Iterable[_Item]] = None,
        albums: Optional[Iterable[_Item]] = None,
        listing_errors: Optional[Dict[str, BaseException]] = None,
    ) -> None:
        self._items = {
            "artists": [_artist_item()] if artists is None else list(artists),
            "albums": [_album_item()] if albums is None else list(albums),
            "tracks": list(tracks),
        }
        self._listing_errors = listing_errors or {}

    def _list(self, kind: str) -> List[_Item]:
        if kind in self._listing_errors:
            raise self._listing_errors[kind]
        return list(self._items[kind])

    def searchArtists(self) -> List[_Item]:  # noqa: N802 (PlexAPI's name)
        return self._list("artists")

    def searchAlbums(self) -> List[_Item]:  # noqa: N802
        return self._list("albums")

    def searchTracks(self) -> List[_Item]:  # noqa: N802
        return self._list("tracks")


def _artist_item(key: str = ARTIST_KEY, errors: Iterable[BaseException] = ()) -> _Item:
    return _Item(key, title="Artist One", errors=errors)


def _album_item(
    key: str = ALBUM_KEY, artist_key: str = ARTIST_KEY, errors: Iterable[BaseException] = ()
) -> _Item:
    item = _Item(key, title="Album One", errors=errors)
    item.parentRatingKey = artist_key
    return item


def _real_client(library: _Library) -> PlexClient:
    """The real client, reading from ``library``. Nothing connects to a server."""
    client = PlexClient(PLEX_URL, DUMMY_TOKEN)
    client.music_library = library  # type: ignore[assignment]
    client.validate_token = lambda: (True, "Connected")  # type: ignore[method-assign]
    return client


def _not_found(key: str) -> RuntimeError:
    """A read failure that is not a lost connection."""
    return RuntimeError(f"(404) not_found; {PLEX_URL}/library/metadata/{key}")


# ---------------------------------------------------------------------------
# Stand-in 2: a client that hands out model objects and says what it listed
# ---------------------------------------------------------------------------


def _model_track(key: str, title: Optional[str] = None) -> Track:
    track = Track(
        plex_key=key,
        title=_title(key) if title is None else title,
        artist_id=0,
        album_id=0,
        play_count=0,
    )
    track.__dict__["_artist_key"] = ARTIST_KEY
    track.__dict__["_album_key"] = ALBUM_KEY
    return track


class _FakePlex:
    """Stands in for ``PlexClient``.

    ``read_keys`` are the tracks it hands out. ``listed_keys`` are the tracks its
    listing held (the same ones unless given). ``failed`` are the failed reads per
    kind. ``relisted`` is what ``list_track_keys`` answers later, or the error it
    raises.
    """

    def __init__(
        self,
        read_keys: Iterable[str],
        listed_keys: Optional[Iterable[str]] = None,
        failed: Optional[Dict[str, int]] = None,
        relisted: Any = None,
    ) -> None:
        self.read_keys = list(read_keys)
        listed = frozenset(self.read_keys if listed_keys is None else listed_keys)
        counts = {"artists": 0, "albums": 0, "tracks": 0}
        counts.update(failed or {})
        self.read_report: Any = PlexReadReport(
            listed_track_keys=listed,
            listed={"artists": 1, "albums": 1, "tracks": len(listed)},
            failed=counts,
        )
        self.relisted = relisted
        self.full_reads = 0
        self.listings = 0

    def validate_token(self) -> Tuple[bool, str]:
        return True, "Connected"

    def get_all_artists(self, batch_size: int = 100) -> Iterator[List[Artist]]:
        yield [Artist(plex_key=ARTIST_KEY, name="Artist One")]

    def get_all_albums(self, batch_size: int = 100) -> Iterator[List[Album]]:
        album = Album(plex_key=ALBUM_KEY, title="Album One", artist_id=0)
        album.__dict__["_artist_key"] = ARTIST_KEY
        yield [album]

    def get_all_tracks(self, batch_size: int = 100) -> Iterator[List[Track]]:
        self.full_reads += 1
        if self.read_keys:
            yield [_model_track(key) for key in self.read_keys]

    def list_track_keys(self) -> Any:
        self.listings += 1
        if isinstance(self.relisted, BaseException):
            raise self.relisted
        return self.relisted


def _engine(plex: Any, db: SQLiteManager, **kwargs: Any) -> SyncEngine:
    return SyncEngine(plex_client=plex, db_manager=db, **kwargs)


# ===========================================================================
# 1. A track that was listed but not read is kept
# ===========================================================================


def test_unread_tracks_are_not_removed(db: SQLiteManager) -> None:
    ids = _seed(db, _keys(6))
    playlist_id = _add_embeddings_and_playlist(db, ids)
    # Plex lists all six tracks. Reading two of them fails, the connection is fine.
    library = _Library(
        [
            _Item("t0"),
            _Item("t1", errors=[_not_found("t1")]),
            _Item("t2"),
            _Item("t3", errors=[_not_found("t3")]),
            _Item("t4"),
            _Item("t5"),
        ]
    )
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert result.tracks_removed == 0
    assert _track_keys(db) == _keys(6)
    for key in ("t1", "t3"):
        kept = db.get_track_by_plex_key(key)
        assert kept is not None
        # Tags, the embedding row and the playlist entry are all still there.
        assert (kept.tags, kept.environments, kept.instruments) == (f"tag-{key}", "study", "piano")
        assert db.get_embedding_by_track_id(ids[key]) is not None
    assert len(_rows(db, "embeddings")) == 6
    assert len(db.get_playlist_tracks(playlist_id)) == 6
    # The run says how many tracks it could not read.
    outcome = engine.outcome
    assert outcome.unread == {"artists": 0, "albums": 0, "tracks": 2}
    assert outcome.unread_tracks == 2
    assert outcome.listed["tracks"] == 6
    assert (outcome.removed, outcome.held, outcome.reason) == (0, 0, "")
    assert result.status == "partial"
    assert result.error_message == "2 tracks could not be read and were kept."


def test_removal_uses_the_listing_not_the_read_set(db: SQLiteManager) -> None:
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    # t5 is gone from Plex's listing. t2 is listed, and its read fails.
    library = _Library(
        [_Item("t0"), _Item("t1"), _Item("t2", errors=[_not_found("t2")]), _Item("t3"), _Item("t4")]
    )
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    # Only the track the listing leaves out is removed, with its embedding row.
    assert _track_keys(db) == _keys(5)
    assert result.tracks_removed == 1
    assert engine.outcome.removed == 1
    assert db.get_embedding_by_track_id(ids["t5"]) is None
    assert db.get_embedding_by_track_id(ids["t2"]) is not None
    assert engine.outcome.unread_tracks == 1
    assert result.status == "partial"


def test_track_with_empty_title_is_kept_as_it_was(db: SQLiteManager) -> None:
    _seed(db, _keys(3))
    library = _Library([_Item("t0"), _Item("t1", title=""), _Item("t2")])
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert _track_keys(db) == _keys(3)
    kept = db.get_track_by_plex_key("t1")
    assert kept is not None and kept.title == _title("t1")
    # An unusable title is not a failed read: the run is complete.
    assert engine.outcome.unread_tracks == 0
    assert result.status == "success"
    assert result.error_message is None


def test_detail_that_the_model_refuses_is_a_failed_read(db: SQLiteManager) -> None:
    _seed(db, _keys(40) + ["gone"])
    # Plex lists 40 tracks. 30 carry a rating of 12, which is 6.0 on PlexMix's scale
    # of 0 to 5: the track model refuses it with a ValueError of its own.
    items = [_Item(key) for key in _keys(10)]
    for key in _keys(30, start=10):
        item = _Item(key)
        item.userRating = 12  # type: ignore[attr-defined]
        items.append(item)
    plex = _real_client(_Library(items))
    engine = _engine(plex, db)

    result = engine.incremental_sync(generate_embeddings=False)

    # Failed reads, not "skipped (empty title)": they count toward the limit.
    assert (plex.read_report.failed["tracks"], plex.read_report.skipped["tracks"]) == (30, 0)
    assert engine.outcome.unread_tracks == 30
    assert engine.outcome.reason == "too_many_unread"
    assert db.get_track_by_plex_key("gone") is not None
    assert len(db.get_all_tracks()) == 41
    assert result.status == "partial"


@patch("plexmix.plex.client.time.sleep", return_value=None)
def test_one_dropped_request_that_recovers_is_a_clean_sync(
    mock_sleep: MagicMock, db: SQLiteManager
) -> None:
    """Spec "A single dropped request recovers": the sync continues and completes."""
    _seed(db, _keys(4))
    library = _Library(
        [
            _Item("t0"),
            _Item("t1", title="Renamed in Plex", errors=[ConnectionResetError("reset by peer")]),
            _Item("t2"),
            _Item("t3"),
        ]
    )
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    mock_sleep.assert_called_once_with(1.0)
    assert (result.status, result.error_message) == ("success", None)
    assert (result.tracks_updated, result.tracks_removed) == (1, 0)
    assert engine.outcome.is_partial is False
    assert engine.outcome.unread == {"artists": 0, "albums": 0, "tracks": 0}
    # The track whose first read was dropped was read by the retry.
    renamed = db.get_track_by_plex_key("t1")
    assert renamed is not None and renamed.title == "Renamed in Plex"
    assert _track_keys(db) == _keys(4)
    assert [row["status"] for row in _history(db)] == ["success"]


# ===========================================================================
# 2. Plex stops answering, or cannot list: nothing is written
# ===========================================================================


@patch("plexmix.plex.client.time.sleep", return_value=None)
def test_read_interrupted_writes_nothing_and_records_failed(
    mock_sleep: MagicMock, db: SQLiteManager, caplog: pytest.LogCaptureFixture
) -> None:
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    before = _snapshot(db)
    lost = f"connection refused; {PLEX_URL}/library/metadata/t2?X-Plex-Token={DUMMY_TOKEN}"
    # Plex lists a new track, a renamed one and two of the old ones, and no longer
    # lists t3 to t5. The connection is lost at t2 and the retry fails as well.
    library = _Library(
        [
            _Item("new0"),
            _Item("t0", title="Renamed in Plex"),
            _Item("t1"),
            _Item("t2", errors=[ConnectionRefusedError(lost), ConnectionRefusedError(lost)]),
            _Item("t9"),
        ]
    )
    engine = _engine(_real_client(library), db)
    stale_outcome = engine.outcome

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PlexReadInterrupted) as info:
            engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    sentence = "Plex stopped answering after 3 of 5 tracks were read, so nothing was changed."
    assert str(info.value) == sentence
    mock_sleep.assert_called_once_with(1.0)
    # Nothing was added, changed or removed: not a track, not an "Unknown Artist" row.
    assert _snapshot(db) == before
    assert _backup_folders(db) == []
    # One history row, failed, with the one sentence and no server address in it.
    history = _history(db)
    assert len(history) == 1
    assert history[0]["status"] == "failed"
    assert history[0]["error_message"] == sentence
    assert (history[0]["tracks_added"], history[0]["tracks_removed"]) == (0, 0)
    for piece in ("http", "127.0.0.1", "leakuser", DUMMY_PASSWORD, DUMMY_TOKEN):
        assert piece not in history[0]["error_message"]
    for secret in ("leakuser", DUMMY_PASSWORD, DUMMY_TOKEN):
        assert secret not in caplog.text
    # The outcome is this run's own, also for a run that raised.
    assert isinstance(engine.outcome, SyncOutcome)
    assert engine.outcome is not stale_outcome
    assert (engine.outcome.removed, engine.outcome.held) == (0, 0)


@pytest.mark.parametrize("kind", ["artists", "albums", "tracks"])
def test_listing_failure_writes_nothing_and_records_failed(
    kind: str, db: SQLiteManager, caplog: pytest.LogCaptureFixture
) -> None:
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    before = _snapshot(db)
    error = BadRequest(
        f"(500) internal_server_error; {PLEX_URL}/library/sections/1/all"
        f"?type=10&X-Plex-Token={DUMMY_TOKEN}"
    )
    library = _Library([_Item("t0"), _Item("new0")], listing_errors={kind: error})
    engine = _engine(_real_client(library), db)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PlexReadInterrupted) as info:
            engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    sentence = f"Plex could not list the library's {kind}, so nothing was changed."
    assert str(info.value) == sentence
    assert (info.value.kind, info.value.read, info.value.listed) == (kind, 0, None)
    assert _snapshot(db) == before
    history = _history(db)
    assert [row["status"] for row in history] == ["failed"]
    assert history[0]["error_message"] == sentence
    for piece in ("http", "127.0.0.1", "leakuser", DUMMY_PASSWORD, DUMMY_TOKEN):
        assert piece not in history[0]["error_message"]
    # The log has the reason, with the address credentials and the token masked.
    assert f"Failed to retrieve {kind}: (500) internal_server_error; http://***@" in caplog.text
    for secret in ("leakuser", DUMMY_PASSWORD, DUMMY_TOKEN):
        assert secret not in caplog.text


def test_outcome_is_new_in_every_run_even_when_it_raises(db: SQLiteManager) -> None:
    _seed(db, _keys(3))
    plex = _FakePlex(_keys(2))
    # With an AI provider: a regenerate run is refused without one, before it starts.
    engine = _engine(plex, db, ai_provider=MagicMock())
    engine.incremental_sync(generate_embeddings=False)
    first = engine.outcome
    assert first.removed == 1

    plex.validate_token = lambda: (False, "Plex token is invalid or expired.")  # type: ignore[method-assign]
    for run in (engine.incremental_sync, engine.full_sync, engine.regenerate_sync):
        with pytest.raises(ConnectionError):
            run(generate_embeddings=False)
        assert engine.outcome is not first
        assert engine.outcome == SyncOutcome()


# ===========================================================================
# 3. Many failed reads block removals; a few do not
# ===========================================================================


@pytest.mark.parametrize("allow_large_removals", [False, True])
def test_too_many_failed_reads_add_and_update_but_remove_nothing(
    allow_large_removals: bool, db: SQLiteManager
) -> None:
    _seed(db, _keys(30) + ["gone"])
    # Plex lists the 30 tracks and a new one; "gone" is no longer listed. 11 of the
    # 31 reads fail: more than 10 and more than 1 percent.
    items = [_Item("new0"), _Item("t0", title="Renamed in Plex")]
    items += [_Item(key, errors=[_not_found(key)]) for key in _keys(11, start=1)]
    items += [_Item(key) for key in _keys(18, start=12)]
    engine = _engine(_real_client(_Library(items)), db)

    with patch("plexmix.plex.sync.backup_library") as backup:
        result = engine.incremental_sync(
            generate_embeddings=False, allow_large_removals=allow_large_removals
        )

    # What could be read was added and updated.
    assert (result.tracks_added, result.tracks_updated, result.tracks_removed) == (1, 1, 0)
    assert db.get_track_by_plex_key("new0") is not None
    renamed = db.get_track_by_plex_key("t0")
    assert renamed is not None and renamed.title == "Renamed in Plex"
    # Nothing was removed, with or without the flag, and no backup was started.
    assert db.get_track_by_plex_key("gone") is not None
    assert len(db.get_all_tracks()) == 32
    backup.assert_not_called()
    outcome = engine.outcome
    assert outcome.reason == "too_many_unread"
    assert (outcome.removed, outcome.held) == (0, 0)
    assert outcome.unread["tracks"] == 11
    assert outcome.message == (
        "11 of 31 tracks could not be read and were kept. Nothing was removed."
    )
    assert result.status == "partial"
    assert result.error_message == outcome.message
    assert _history(db)[-1]["status"] == "partial"


@pytest.mark.parametrize("kind", ["artists", "albums"])
def test_too_many_failed_reads_of_another_kind_also_block_removals(
    kind: str, db: SQLiteManager
) -> None:
    _seed(db, _keys(3))
    plex = _FakePlex(_keys(2), failed={kind: 11})
    plex.read_report.listed[kind] = 40
    engine = _engine(plex, db)

    result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    assert _track_keys(db) == _keys(3)
    assert engine.outcome.reason == "too_many_unread"
    assert engine.outcome.message == (
        f"11 of 40 {kind} could not be read and were kept. Nothing was removed."
    )
    assert result.status == "partial"


def test_too_many_failed_reads_are_reported_for_a_first_sync_too(db: SQLiteManager) -> None:
    # An empty database and an empty track listing: nothing could be removed, and the
    # run still says that Plex failed too many reads.
    plex = _FakePlex([], failed={"artists": 11})
    plex.read_report.listed["artists"] = 40
    engine = _engine(plex, db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert engine.outcome.reason == "too_many_unread"
    assert result.status == "partial"


def test_failures_above_10_but_under_1_percent_are_a_few(db: SQLiteManager) -> None:
    assert (MAX_FAILED_READS, MAX_FAILED_READS_PERCENT) == (10, 1)
    # 40 of 5,943 is more than 10 items and under 1 percent: a few.
    assert too_many_failed_reads(40, 5943) is False
    assert too_many_failed_reads(200, 5943) is True
    # Both limits have to be passed, and "more than" is strict for each.
    assert too_many_failed_reads(59, 5943) is False
    assert too_many_failed_reads(60, 5943) is True
    assert too_many_failed_reads(10, 100) is False
    assert too_many_failed_reads(11, 100) is True
    assert too_many_failed_reads(11, 1100) is False
    assert too_many_failed_reads(12, 1100) is True
    assert too_many_failed_reads(0, 0) is False

    # In the engine: such a run still applies the removals that Plex reports.
    _seed(db, _keys(3))
    listed = _keys(2) + [f"other{n}" for n in range(5941)]
    engine = _engine(_FakePlex(_keys(2), listed_keys=listed, failed={"tracks": 40}), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert _track_keys(db) == _keys(2)
    assert (engine.outcome.removed, engine.outcome.reason) == (1, "")
    assert engine.outcome.unread_tracks == 40
    assert result.status == "partial"
    assert result.error_message == "40 tracks could not be read and were kept."


def test_few_failed_reads_complete_as_partial(db: SQLiteManager) -> None:
    _seed(db, _keys(20) + ["gone"])
    items = [_Item(key, errors=[_not_found(key)]) for key in _keys(3)]
    items += [_Item(key) for key in _keys(17, start=3)]
    engine = _engine(_real_client(_Library(items)), db)

    result = engine.incremental_sync(generate_embeddings=False)

    # The run completes, and the removal Plex reports is applied as usual.
    assert _track_keys(db) == sorted(_keys(20))
    assert result.tracks_removed == 1
    assert engine.outcome.reason == ""
    assert engine.outcome.unread_tracks == 3
    # The history row is honest about the three tracks.
    row = _history(db)[-1]
    assert row["status"] == "partial"
    assert row["error_message"] == "3 tracks could not be read and were kept."
    assert row["tracks_removed"] == 1


def test_one_unread_track_reads_as_singular() -> None:
    outcome = SyncOutcome(unread={"artists": 2, "albums": 0, "tracks": 1})

    assert outcome.is_partial is True
    assert outcome.unread_sentence() == "1 track could not be read and was kept."
    assert outcome.unread_sentence("albums") == ""
    # An unread artist may be new to PlexMix, so its sentence is about the tracks.
    assert outcome.unread_sentence("artists") == (
        "2 artists could not be read. Tracks keep the artist PlexMix already has for "
        "them. A new track whose artist is also new to PlexMix is filed under Unknown "
        "Artist until a later sync reads that artist."
    )
    assert outcome.describe() == (
        f"{outcome.unread_sentence('artists')} 1 track could not be read and was kept."
    )
    assert SyncOutcome(unread={"artists": 0, "albums": 1, "tracks": 0}).describe() == (
        "1 album could not be read. Tracks keep the album PlexMix already has for "
        "them. A new track whose album is also new to PlexMix is filed under Unknown "
        "Album until a later sync reads that album."
    )
    assert SyncOutcome().is_partial is False
    assert SyncOutcome().describe() == ""


def test_index_that_could_not_be_rebuilt_makes_the_outcome_partial() -> None:
    """The sync did its work, and the saved search index was left as it is."""
    assert SyncOutcome().index_left_out == 0 and SyncOutcome().index_sentence == ""
    outcome = SyncOutcome(index_left_out=380)

    assert outcome.is_partial is True and outcome.reason == ""
    assert outcome.index_sentence == (
        "The search index was left as it is: 380 tracks hold an embedding that was made "
        "with other embedding settings. Run 'plexmix embeddings generate --regenerate' to "
        "replace them."
    )
    assert outcome.describe() == outcome.index_sentence
    # After what the run has to say about its reads.
    both = SyncOutcome(unread={"artists": 0, "albums": 0, "tracks": 3}, index_left_out=1)
    assert both.describe() == (
        "3 tracks could not be read and were kept. The search index was left as it is: "
        "1 track holds an embedding that was made with other embedding settings. Run "
        "'plexmix embeddings generate --regenerate' to replace it."
    )
    assert SyncOutcome(index_left_out=2) != SyncOutcome()


# ===========================================================================
# 4. A failed artist or album read does not move tracks
# ===========================================================================


@pytest.mark.parametrize("album_read_fails", [False, True])
def test_failed_artist_read_keeps_track_links(album_read_fails: bool, db: SQLiteManager) -> None:
    ids = _seed(db, _keys(2))
    artist = db.get_artist_by_plex_key(ARTIST_KEY)
    album = db.get_album_by_plex_key(ALBUM_KEY)
    assert artist is not None and album is not None
    # The artist's read fails. Both tracks are read and renamed, so both are written.
    library = _Library(
        [_Item("t0", title="Renamed 0"), _Item("t1", title="Renamed 1")],
        artists=[_artist_item(errors=[_not_found(ARTIST_KEY)])],
        albums=[_album_item(errors=[_not_found(ALBUM_KEY)] if album_read_fails else [])],
    )
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert result.tracks_updated == 2
    for key in ("t0", "t1"):
        track = db.get_track_by_id(ids[key])
        assert track is not None
        assert track.title.startswith("Renamed")
        # The tracks keep their artist and their album.
        assert (track.artist_id, track.album_id) == (artist.id, album.id)
    album_now = db.get_album_by_plex_key(ALBUM_KEY)
    assert album_now is not None and album_now.artist_id == artist.id
    assert engine.outcome.unread["artists"] == 1
    assert engine.outcome.unread["albums"] == (1 if album_read_fails else 0)
    assert result.status == "partial"


def test_new_track_of_an_unread_artist_still_goes_to_unknown(db: SQLiteManager) -> None:
    _seed(db, _keys(1))
    new_track = _Item("new0")
    new_track.grandparentRatingKey = "a2"
    new_track.parentRatingKey = "al2"
    library = _Library(
        [_Item("t0"), new_track],
        artists=[_artist_item(), _artist_item("a2", errors=[_not_found("a2")])],
        albums=[_album_item(), _album_item("al2", "a2", errors=[_not_found("al2")])],
    )
    engine = _engine(_real_client(library), db)

    engine.incremental_sync(generate_embeddings=False)

    unknown_artist = db.get_artist_by_plex_key("__unknown__")
    unknown_album = db.get_album_by_plex_key("__unknown__")
    added = db.get_track_by_plex_key("new0")
    assert added is not None and unknown_artist is not None and unknown_album is not None
    assert (added.artist_id, added.album_id) == (unknown_artist.id, unknown_album.id)


def test_held_track_keeps_its_links_when_its_new_album_could_not_be_read(
    db: SQLiteManager,
) -> None:
    ids = _seed(db, _keys(1))
    artist = db.get_artist_by_plex_key(ARTIST_KEY)
    album = db.get_album_by_plex_key(ALBUM_KEY)
    assert artist is not None and album is not None
    # Plex now lists t0, renamed, under an artist and an album that are new to
    # PlexMix, and neither of them could be read.
    moved = _Item("t0", title="Renamed in Plex")
    moved.grandparentRatingKey = "a2"
    moved.parentRatingKey = "al2"
    library = _Library(
        [moved],
        artists=[_artist_item(), _artist_item("a2", errors=[_not_found("a2")])],
        albums=[_album_item(), _album_item("al2", "a2", errors=[_not_found("al2")])],
    )
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert result.tracks_updated == 1
    track = db.get_track_by_id(ids["t0"])
    assert track is not None and track.title == "Renamed in Plex"
    # The track is rewritten, and it is not moved to "Unknown Artist" or "Unknown Album".
    assert (track.artist_id, track.album_id) == (artist.id, album.id)
    assert engine.outcome.unread == {"artists": 1, "albums": 1, "tracks": 0}


def test_a_later_sync_links_the_tracks_that_were_filed_under_unknown(db: SQLiteManager) -> None:
    # The first sync of an empty library. The artist and the album cannot be read,
    # so the two new tracks go to "Unknown Artist" and "Unknown Album".
    first = _Library(
        [_Item("t0"), _Item("t1")],
        artists=[_artist_item(errors=[_not_found(ARTIST_KEY)])],
        albums=[_album_item(errors=[_not_found(ALBUM_KEY)])],
    )
    result = _engine(_real_client(first), db).incremental_sync(generate_embeddings=False)

    unknown_artist = db.get_artist_by_plex_key("__unknown__")
    unknown_album = db.get_album_by_plex_key("__unknown__")
    assert unknown_artist is not None and unknown_album is not None
    for track in db.get_all_tracks():
        assert (track.artist_id, track.album_id) == (unknown_artist.id, unknown_album.id)
    # The run says where the new tracks went. Nothing was "kept": nothing was held.
    assert result.status == "partial"
    assert result.error_message == (
        "1 artist could not be read. Tracks keep the artist PlexMix already has for "
        "them. A new track whose artist is also new to PlexMix is filed under Unknown "
        "Artist until a later sync reads that artist. "
        "1 album could not be read. Tracks keep the album PlexMix already has for "
        "them. A new track whose album is also new to PlexMix is filed under Unknown "
        "Album until a later sync reads that album."
    )

    # The next sync reads everything, and moves both tracks to their artist and album.
    second = _Library([_Item("t0"), _Item("t1")])
    result = _engine(_real_client(second), db).incremental_sync(generate_embeddings=False)

    artist = db.get_artist_by_plex_key(ARTIST_KEY)
    album = db.get_album_by_plex_key(ALBUM_KEY)
    assert artist is not None and album is not None
    assert (result.tracks_added, result.tracks_updated, result.status) == (0, 2, "success")
    for track in db.get_all_tracks():
        assert (track.artist_id, track.album_id) == (artist.id, album.id)

    # After that there is nothing left to repair.
    third = _Library([_Item("t0"), _Item("t1")])
    result = _engine(_real_client(third), db).incremental_sync(generate_embeddings=False)
    assert (result.tracks_added, result.tracks_updated) == (0, 0)


def test_held_album_keeps_its_artist_when_its_new_artist_could_not_be_read(
    db: SQLiteManager,
) -> None:
    ids = _seed(db, _keys(3))
    artist = db.get_artist_by_plex_key(ARTIST_KEY)
    assert artist is not None
    # Plex now puts the album under an artist that is new to PlexMix, and that
    # artist could not be read.
    library = _Library(
        [_Item(key) for key in _keys(3)],
        artists=[_artist_item(), _artist_item("a2", errors=[_not_found("a2")])],
        albums=[_album_item(ALBUM_KEY, "a2")],
    )
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    album = db.get_album_by_plex_key(ALBUM_KEY)
    assert album is not None
    # The album row keeps its artist. It is not moved to "Unknown Artist".
    assert album.artist_id == artist.id
    assert engine.outcome.unread == {"artists": 1, "albums": 0, "tracks": 0}
    assert (result.status, result.tracks_updated) == ("partial", 0)
    for track_id in ids.values():
        track = db.get_track_by_id(track_id)
        assert track is not None and track.artist_id == artist.id

    # The next sync reads the artist, and the album moves to it.
    second = _Library(
        [_Item(key) for key in _keys(3)],
        artists=[_artist_item(), _artist_item("a2")],
        albums=[_album_item(ALBUM_KEY, "a2")],
    )
    _engine(_real_client(second), db).incremental_sync(generate_embeddings=False)
    moved_to = db.get_artist_by_plex_key("a2")
    album = db.get_album_by_plex_key(ALBUM_KEY)
    assert moved_to is not None and album is not None and album.artist_id == moved_to.id


def test_new_album_of_an_unread_artist_still_goes_to_unknown(db: SQLiteManager) -> None:
    _seed(db, _keys(1))
    library = _Library(
        [_Item("t0")],
        artists=[_artist_item(), _artist_item("a2", errors=[_not_found("a2")])],
        albums=[_album_item(), _album_item("al2", "a2")],
    )

    _engine(_real_client(library), db).incremental_sync(generate_embeddings=False)

    unknown_artist = db.get_artist_by_plex_key("__unknown__")
    new_album = db.get_album_by_plex_key("al2")
    assert unknown_artist is not None and new_album is not None
    assert new_album.artist_id == unknown_artist.id


def test_new_track_of_a_held_artist_and_album_whose_reads_failed_is_linked_to_them(
    db: SQLiteManager,
) -> None:
    """PlexMix holds the artist and the album. A failed read of them is no reason for "Unknown"."""
    _seed(db, _keys(1))
    artist = db.get_artist_by_plex_key(ARTIST_KEY)
    album = db.get_album_by_plex_key(ALBUM_KEY)
    assert artist is not None and album is not None
    library = _Library(
        [_Item("t0"), _Item("new0")],
        artists=[_artist_item(errors=[_not_found(ARTIST_KEY)])],
        albums=[_album_item(errors=[_not_found(ALBUM_KEY)])],
    )
    engine = _engine(_real_client(library), db)

    result = engine.incremental_sync(generate_embeddings=False)

    added = db.get_track_by_plex_key("new0")
    assert added is not None
    assert (added.artist_id, added.album_id) == (artist.id, album.id)
    assert result.tracks_added == 1
    # The sentence does not claim that new tracks went to "Unknown Artist": it
    # limits that to a track whose artist is new to PlexMix as well.
    assert engine.outcome.unread_sentence("artists") == (
        "1 artist could not be read. Tracks keep the artist PlexMix already has for "
        "them. A new track whose artist is also new to PlexMix is filed under Unknown "
        "Artist until a later sync reads that artist."
    )


@pytest.mark.parametrize("moved", ["album", "artist"])
def test_track_that_plex_moved_is_updated_once(moved: str, db: SQLiteManager) -> None:
    """A changed album link, or a changed artist link, is by itself a reason to update."""
    ids = _seed(db, _keys(2))
    # Plex now files t0 under another album of the same artist, or under another
    # artist with the same album. Nothing else about the track changed.
    item = _Item("t0")
    if moved == "album":
        item.parentRatingKey = "al2"
    else:
        item.grandparentRatingKey = "a2"

    def plex() -> PlexClient:
        return _real_client(
            _Library(
                [item, _Item("t1")],
                artists=[_artist_item(), _artist_item("a2")],
                albums=[_album_item(), _album_item("al2")],
            )
        )

    result = _engine(plex(), db).incremental_sync(generate_embeddings=False)

    assert (result.tracks_added, result.tracks_updated) == (0, 1)
    track = db.get_track_by_id(ids["t0"])
    target = (
        db.get_album_by_plex_key("al2") if moved == "album" else db.get_artist_by_plex_key("a2")
    )
    assert track is not None and target is not None
    assert (track.album_id if moved == "album" else track.artist_id) == target.id
    # Its tags came along, and the other track was left alone.
    assert track.tags == "tag-t0"

    # The link is stored now, so the next sync has nothing to update.
    result = _engine(plex(), db).incremental_sync(generate_embeddings=False)
    assert (result.tracks_added, result.tracks_updated) == (0, 0)


# ===========================================================================
# 5. An empty or unknown listing removes nothing
# ===========================================================================


def test_empty_listing_removes_nothing(db: SQLiteManager, caplog: pytest.LogCaptureFixture) -> None:
    ids = _seed(db, _keys(3))
    _add_embeddings_and_playlist(db, ids)
    before = _snapshot(db)
    engine = _engine(_real_client(_Library([])), db)

    with caplog.at_level(logging.WARNING, logger="plexmix.plex.sync"):
        result = engine.incremental_sync(generate_embeddings=False)

    assert _snapshot(db)["tracks"] == before["tracks"]
    assert _snapshot(db)["embeddings"] == before["embeddings"]
    assert _snapshot(db)["playlist_tracks"] == before["playlist_tracks"]
    outcome = engine.outcome
    assert (outcome.removed, outcome.held, outcome.reason) == (0, 0, "empty_listing")
    # The message is for the user: callers print it as it is.
    assert outcome.message == (
        "Plex listed 0 tracks, but the PlexMix library holds 3. Nothing was removed. "
        "If the Plex library really is empty, run 'plexmix db reset' to clear the "
        "PlexMix library."
    )
    assert result.status == "partial"
    assert result.error_message == outcome.message
    # The warning of the old guard is still logged.
    assert "Plex returned 0 tracks but the database holds 3" in caplog.text


def test_empty_listing_and_empty_database_is_a_clean_run(db: SQLiteManager) -> None:
    engine = _engine(_real_client(_Library([])), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert result.status == "success"
    assert engine.outcome == SyncOutcome(listed={"artists": 1, "albums": 1, "tracks": 0})


def _report_of_another_listing() -> PlexReadReport:
    return PlexReadReport(
        listed_track_keys=frozenset({"x1", "x2"}), listed={"artists": 1, "albums": 1, "tracks": 2}
    )


def _report_with_a_plain_list() -> PlexReadReport:
    report = PlexReadReport(listed={"artists": 1, "albums": 1, "tracks": 2})
    report.listed_track_keys = ["t0", "t1"]  # type: ignore[assignment]
    return report


def _report_with_odd_counts() -> PlexReadReport:
    report = PlexReadReport(listed_track_keys=frozenset({"t0", "t1"}))
    report.failed = {"artists": 0, "albums": 0, "tracks": None}  # type: ignore[dict-item]
    return report


@pytest.mark.parametrize(
    "read_report",
    [
        None,
        MagicMock(),
        {"listed_track_keys": frozenset({"t0", "t1"})},
        _report_of_another_listing(),
        _report_with_a_plain_list(),
        _report_with_odd_counts(),
    ],
    ids=["none", "mock", "dict", "another-listing", "plain-list", "odd-counts"],
)
@pytest.mark.parametrize("allow_large_removals", [False, True])
def test_missing_read_report_removes_nothing(
    allow_large_removals: bool, read_report: Any, db: SQLiteManager
) -> None:
    _seed(db, _keys(4))
    # The client hands out two of the four tracks and gives no real report.
    plex = _FakePlex(_keys(2))
    plex.read_report = read_report
    engine = _engine(plex, db)

    result = engine.incremental_sync(
        generate_embeddings=False, allow_large_removals=allow_large_removals
    )

    assert _track_keys(db) == _keys(4)
    assert (engine.outcome.removed, engine.outcome.held) == (0, 0)
    assert engine.outcome.reason == "listing_unknown"
    assert engine.outcome.message == (
        "Plex's track listing was not available. Nothing was removed."
    )
    assert result.tracks_removed == 0
    assert result.status == "partial"


def test_client_without_a_report_attribute_removes_nothing(db: SQLiteManager) -> None:
    _seed(db, _keys(4))
    plex = _FakePlex(_keys(2))
    del plex.read_report
    engine = _engine(plex, db)

    engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    assert _track_keys(db) == _keys(4)
    assert engine.outcome.reason == "listing_unknown"


# ===========================================================================
# 6. Cancellation
# ===========================================================================


def test_cancel_during_compare_skips_the_removal_step(db: SQLiteManager) -> None:
    _seed(db, _keys(12) + ["gone"])
    cancel = Event()

    def cancel_while_comparing(progress: float, message: str) -> None:
        if message.startswith("Processing tracks"):
            cancel.set()

    engine = _engine(_FakePlex(_keys(12)), db)

    with pytest.raises(KeyboardInterrupt):
        engine.incremental_sync(
            generate_embeddings=False,
            progress_callback=cancel_while_comparing,
            cancel_event=cancel,
        )

    # The request came during the compare stage, so the removal step never ran.
    assert cancel.is_set()
    assert db.get_track_by_plex_key("gone") is not None
    assert (engine.outcome.removed, engine.outcome.reason) == (0, "")
    assert _history(db)[-1]["status"] == "interrupted"

    # The same sync without a cancel request does remove the track.
    engine = _engine(_FakePlex(_keys(12)), db)
    result = engine.incremental_sync(generate_embeddings=False)
    assert result.tracks_removed == 1
    assert db.get_track_by_plex_key("gone") is None


def test_removal_step_does_nothing_once_cancel_is_requested(db: SQLiteManager) -> None:
    _seed(db, _keys(3))
    plex = _FakePlex(_keys(2))
    engine = _engine(plex, db)
    cancel = Event()
    cancel.set()

    removed = engine._remove_tracks_plex_no_longer_lists(plex.read_report, True, cancel)

    assert removed == 0
    assert _track_keys(db) == _keys(3)


def test_interrupt_during_a_removal_removes_nothing(db: SQLiteManager) -> None:
    """Ctrl+C part-way through the deletes must not leave half of them committed."""
    _seed(db, _keys(40))
    engine = _engine(_FakePlex(_keys(20)), db)  # 20 tracks are gone from Plex
    delete_track = db.delete_track
    deleted: List[int] = []

    def interrupted_at_the_eighth(track_id: int) -> None:
        if len(deleted) == 7:
            raise KeyboardInterrupt()
        deleted.append(track_id)
        delete_track(track_id)

    with patch.object(db, "delete_track", side_effect=interrupted_at_the_eighth):
        with pytest.raises(KeyboardInterrupt):
            engine.incremental_sync(generate_embeddings=False)

    assert len(deleted) == 7
    # The seven deletes were rolled back before the history row was committed.
    assert _track_keys(db) == sorted(_keys(40))
    row = _history(db)[-1]
    assert (row["status"], row["tracks_removed"]) == ("interrupted", 0)


def _cancel_at_the_fifth_delete(db: SQLiteManager, cancel: Event) -> List[int]:
    """Make ``db.delete_track`` set ``cancel`` when its fifth delete starts."""
    delete_track = db.delete_track
    deleted: List[int] = []

    def delete(track_id: int) -> None:
        deleted.append(track_id)
        if len(deleted) == 5:
            cancel.set()  # the user cancels while the removal is being applied
        delete_track(track_id)

    db.delete_track = delete  # type: ignore[method-assign]
    return deleted


@pytest.mark.parametrize("allowed", [False, True], ids=["small", "allowed-large"])
def test_cancel_while_a_sync_deletes_tracks_removes_nothing(
    allowed: bool, db: SQLiteManager
) -> None:
    """The removal step stops at a cancel request, and undoes the deletes it had made."""
    held = 400 if allowed else 40
    listed = 100 if allowed else 20
    _seed(db, _keys(held))
    cancel = Event()
    deleted = _cancel_at_the_fifth_delete(db, cancel)
    engine = _engine(_FakePlex(_keys(listed)), db)

    with pytest.raises(KeyboardInterrupt):
        engine.incremental_sync(
            generate_embeddings=False, cancel_event=cancel, allow_large_removals=allowed
        )

    # It stopped at the request, not at the end of the loop.
    assert len(deleted) == 5
    assert _track_keys(db) == sorted(_keys(held))
    assert engine.outcome.removed == 0
    row = _history(db)[-1]
    assert (row["status"], row["tracks_removed"]) == ("interrupted", 0)
    # The backup of the large removal was made before the cancel, and the outcome names it.
    assert (engine.outcome.backup_folder is not None) is allowed
    assert _backup_folders(db) == ([engine.outcome.backup_folder] if allowed else [])


def test_sync_removal_does_not_run_after_a_cancel_during_the_backup(db: SQLiteManager) -> None:
    _seed(db, _keys(400))
    cancel = Event()

    def cancelled_during_backup(database: SQLiteManager, index_path: Optional[Path]) -> Any:
        made = backup_library(database, index_path)
        cancel.set()
        return made

    engine = _engine(_FakePlex(_keys(100)), db)
    with patch("plexmix.plex.sync.backup_library", cancelled_during_backup):
        with pytest.raises(KeyboardInterrupt):
            engine.incremental_sync(
                generate_embeddings=False, cancel_event=cancel, allow_large_removals=True
            )

    assert len(db.get_all_tracks()) == 400
    assert engine.outcome.removed == 0
    # The copy that was already made stays, and the outcome names it.
    assert engine.outcome.backup_folder is not None
    assert _backup_folders(db) == [engine.outcome.backup_folder]
    row = _history(db)[-1]
    assert (row["status"], row["tracks_removed"]) == ("interrupted", 0)


def test_failed_removal_step_keeps_the_counts_of_the_compare_stage(db: SQLiteManager) -> None:
    """The compare stage has saved its work when the removal step fails. The row says so."""
    _seed(db, _keys(40))
    # Plex lists 30 of the 40 tracks, all renamed, and 5 new ones.
    read = [_model_track(key, title=f"Renamed {key}") for key in _keys(30)]
    read += [_model_track(key) for key in _keys(5, start=100)]
    plex = _FakePlex([track.plex_key for track in read])
    plex.get_all_tracks = lambda batch_size=100: iter([read])  # type: ignore[method-assign]
    engine = _engine(plex, db)

    with patch.object(
        db, "delete_track", side_effect=sqlite3.OperationalError("database is locked")
    ):
        with pytest.raises(sqlite3.OperationalError):
            engine.incremental_sync(generate_embeddings=False)

    # Nothing was removed, and the adds and updates are in the database.
    assert len(db.get_all_tracks()) == 45
    outcome = engine.outcome
    assert (outcome.compared, outcome.added, outcome.updated, outcome.removed) == (True, 5, 30, 0)
    row = _history(db)[-1]
    assert (row["status"], row["error_message"]) == ("failed", "database is locked")
    assert (row["tracks_added"], row["tracks_updated"], row["tracks_removed"]) == (5, 30, 0)


def test_failure_after_the_removal_step_is_recorded_with_what_was_removed(
    db: SQLiteManager,
) -> None:
    """A later stage fails (here: the embedding provider's pipe breaks) after tracks were removed."""
    ids = _seed(db, _keys(40))
    _saved_index(db, ids)
    generator = MagicMock()
    generator.provider_name = EMBEDDING_MODEL
    generator.generate_batch_embeddings.side_effect = BrokenPipeError(32, "Broken pipe")
    index = VectorIndex(dimension=DIMENSION, index_path=str(db.db_path.parent / "embeddings.index"))
    plex = _FakePlex(_keys(30) + ["new0"])
    engine = _engine(plex, db, embedding_generator=generator, vector_index=index)

    with pytest.raises(BrokenPipeError):
        engine.incremental_sync(generate_embeddings=True)

    assert len(db.get_all_tracks()) == 31
    outcome = engine.outcome
    assert (outcome.compared, outcome.added, outcome.updated, outcome.removed) == (True, 1, 0, 10)
    row = _history(db)[-1]
    assert row["status"] == "failed"
    assert (row["tracks_added"], row["tracks_updated"], row["tracks_removed"]) == (1, 0, 10)


def test_outcome_of_a_run_that_stopped_before_the_compare_stage(db: SQLiteManager) -> None:
    _seed(db, _keys(6))
    engine = _engine(_StoppedPlex(_keys(6)), db)

    with pytest.raises(PlexReadInterrupted):
        engine.incremental_sync(generate_embeddings=False)

    outcome = engine.outcome
    assert (outcome.compared, outcome.added, outcome.updated, outcome.removed) == (False, 0, 0, 0)
    # The three fields take no part in comparing two outcomes.
    assert SyncOutcome(added=3, updated=2, compared=True) == SyncOutcome()


# ===========================================================================
# 7. The threshold: large removals are held, or backed up first
# ===========================================================================


def test_threshold_boundaries() -> None:
    assert (REMOVAL_CONFIRM_TRACKS, REMOVAL_CONFIRM_PERCENT) == (50, 5)
    # More than 50 tracks: 50 is applied, 51 needs confirmation.
    assert removal_needs_confirmation(50, 400) is False
    assert removal_needs_confirmation(51, 400) is True
    # More than 5 percent: 297 of 5,943 is under it, 298 is over it.
    assert removal_needs_confirmation(297, 5943) is False
    assert removal_needs_confirmation(298, 5943) is True
    # Exactly 5 percent is not "more than".
    assert removal_needs_confirmation(100, 2000) is False
    assert removal_needs_confirmation(101, 2000) is True
    # The scenarios of the spec.
    assert removal_needs_confirmation(120, 5943) is False
    assert removal_needs_confirmation(45, 400) is False
    assert removal_needs_confirmation(5443, 5943) is True
    # A library of 50 tracks or fewer is never asked.
    assert removal_needs_confirmation(50, 50) is False
    assert removal_needs_confirmation(51, 51) is True


def test_removal_at_the_threshold_is_applied_without_a_backup(db: SQLiteManager) -> None:
    _seed(db, _keys(400))
    engine = _engine(_FakePlex(_keys(350)), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert len(db.get_all_tracks()) == 350
    assert result.tracks_removed == 50
    assert result.status == "success"
    assert result.error_message is None
    assert engine.outcome == SyncOutcome(
        listed={"artists": 1, "albums": 1, "tracks": 350}, library_total=400, removed=50
    )
    assert _backup_folders(db) == []


def test_threshold_is_a_share_of_the_library_before_the_sync(db: SQLiteManager) -> None:
    _seed(db, _keys(400))
    # Plex lists 349 of the 400 tracks and 700 new ones. 51 of 400 is more than
    # 5 percent. Of the 1,100 tracks after the additions it would be under 5 percent.
    listed = _keys(349) + [f"new{number}" for number in range(700)]
    engine = _engine(_FakePlex(listed), db)

    result = engine.incremental_sync(generate_embeddings=False)

    assert (result.tracks_added, result.tracks_removed) == (700, 0)
    outcome = engine.outcome
    assert (outcome.removed, outcome.held, outcome.library_total) == (0, 51, 400)
    assert outcome.reason == "threshold"
    assert len(db.get_all_tracks()) == 1100
    # The held tracks are named, so a confirmation can be applied to exactly them.
    assert outcome.held_keys == frozenset(_keys(51, start=349))


@pytest.mark.parametrize("flag", [False, None, 1, "yes", MagicMock()])
def test_removal_above_threshold_is_held_by_default(flag: Any, db: SQLiteManager) -> None:
    _seed(db, _keys(400))
    engine = _engine(_FakePlex(_keys(349)), db)

    if flag is False:
        result = engine.incremental_sync(generate_embeddings=False)
    else:
        # Only a real True allows a large removal: a placeholder object does not.
        result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=flag)

    assert len(db.get_all_tracks()) == 400
    assert result.tracks_removed == 0
    outcome = engine.outcome
    assert (outcome.removed, outcome.held, outcome.library_total) == (0, 51, 400)
    assert outcome.reason == "threshold"
    assert outcome.message == (
        "Plex no longer lists 51 of 400 tracks. They were kept, because a removal this "
        "large needs confirmation."
    )
    assert outcome.backup_folder is None
    assert _backup_folders(db) == []
    assert result.status == "partial"
    assert _history(db)[-1]["error_message"] == outcome.message


def test_allow_large_removals_backs_up_then_removes(db: SQLiteManager) -> None:
    ids = _seed(db, _keys(400))
    _add_embeddings_and_playlist(db, ids)
    engine = _engine(_FakePlex(_keys(300)), db)

    result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    assert len(db.get_all_tracks()) == 300
    assert result.tracks_removed == 100
    outcome = engine.outcome
    assert (outcome.removed, outcome.held, outcome.reason) == (100, 0, "")
    # A dated folder beside the database holds a copy from before the removal.
    folder = outcome.backup_folder
    assert folder is not None
    assert _backup_folders(db) == [folder]
    assert [path.name for path in folder.iterdir()] == [db.db_path.name]
    assert outcome.backup_size_bytes == (folder / db.db_path.name).stat().st_size > 0
    assert _tracks_in_copy(folder / db.db_path.name) == 400
    # Embedding rows and playlist entries went with the removed tracks only.
    assert len(_rows(db, "embeddings")) == 300
    assert len(_rows(db, "playlist_tracks")) == 300
    assert result.status == "success"
    assert result.error_message is None


def test_full_and_regenerate_sync_pass_the_flag_on(db: SQLiteManager) -> None:
    # With an AI provider: a regenerate run is refused without one, before it starts.
    engine = _engine(_FakePlex([]), db, ai_provider=MagicMock())

    for run in (engine.full_sync, engine.regenerate_sync):
        with patch.object(engine, "incremental_sync") as incremental:
            run(generate_embeddings=False)
            assert incremental.call_args.kwargs["allow_large_removals"] is False
        with patch.object(engine, "incremental_sync") as incremental:
            run(generate_embeddings=False, allow_large_removals=True)
            assert incremental.call_args.kwargs["allow_large_removals"] is True


def test_backup_failure_removes_nothing(db: SQLiteManager) -> None:
    _seed(db, _keys(400))
    engine = _engine(_FakePlex(_keys(300)), db)
    error = BackupError("Could not create a backup folder in /data/backups: Permission denied.")

    with patch("plexmix.plex.sync.backup_library", side_effect=error) as backup:
        result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    backup.assert_called_once_with(db, None)
    assert len(db.get_all_tracks()) == 400
    outcome = engine.outcome
    assert (outcome.removed, outcome.held, outcome.reason) == (0, 0, "backup_failed")
    assert outcome.message == (
        "The backup could not be made: Could not create a backup folder in /data/backups: "
        "Permission denied. Nothing was removed."
    )
    assert outcome.backup_folder is None
    assert result.tracks_removed == 0
    assert result.status == "partial"
    assert result.error_message == outcome.message


def test_backup_folder_that_cannot_be_written_removes_nothing(db: SQLiteManager) -> None:
    _seed(db, _keys(400))
    # A file where the backups folder has to be: the real backup cannot write there.
    (db.db_path.parent / "backups").write_text("not a folder", encoding="utf-8")
    engine = _engine(_FakePlex(_keys(300)), db)

    result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    assert len(db.get_all_tracks()) == 400
    assert engine.outcome.reason == "backup_failed"
    assert engine.outcome.message.startswith("The backup could not be made: ")
    assert engine.outcome.message.endswith(" Nothing was removed.")
    assert result.status == "partial"


def test_empty_listing_removes_nothing_even_when_allowed(db: SQLiteManager) -> None:
    _seed(db, _keys(400))
    engine = _engine(_FakePlex([]), db)

    result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    assert len(db.get_all_tracks()) == 400
    outcome = engine.outcome
    # Not removed, not offered for confirmation, and no backup was made for it.
    assert (outcome.removed, outcome.held, outcome.reason) == (0, 0, "empty_listing")
    assert "Plex listed 0 tracks" in outcome.message
    assert _backup_folders(db) == []
    assert result.status == "partial"


# ===========================================================================
# 8. A confirmed removal checks Plex again
# ===========================================================================


def _held_removal(db: SQLiteManager, relisted: Any) -> Tuple[SyncEngine, _FakePlex]:
    """A library of 400 tracks after a sync in which Plex listed only 300 of them."""
    _seed(db, _keys(400))
    plex = _FakePlex(_keys(300), relisted=relisted)
    engine = _engine(plex, db)
    engine.incremental_sync(generate_embeddings=False)
    assert engine.outcome.held == 100
    assert len(db.get_all_tracks()) == 400
    return engine, plex


def test_remove_unlisted_tracks_relists_before_removing(db: SQLiteManager) -> None:
    # Since the sync, Plex lists 20 of the 100 missing tracks again.
    engine, plex = _held_removal(db, relisted=frozenset(_keys(320)))

    result = engine.remove_unlisted_tracks(100)

    assert isinstance(result, RemovalResult)
    assert result.status == "removed"
    assert (result.removed, result.missing, result.library_total) == (80, 80, 400)
    assert result.message == "Removed 80 tracks that Plex no longer lists."
    # Only the tracks that are still not listed are gone.
    assert _track_keys(db) == sorted(_keys(320))
    # Plex was listed again, keys only: no second full read.
    assert (plex.listings, plex.full_reads) == (1, 1)
    # The backup came first: its copy still holds all 400 tracks.
    assert result.backup_folder is not None
    assert _backup_folders(db) == [result.backup_folder]
    assert _tracks_in_copy(result.backup_folder / db.db_path.name) == 400
    assert result.backup_size_bytes > 0
    # The removal has its own history row.
    row = _history(db)[-1]
    assert (row["status"], row["tracks_removed"], row["error_message"]) == ("success", 80, None)


def test_remove_unlisted_tracks_refuses_when_more_are_missing(db: SQLiteManager) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(250)))
    rows_before = len(_history(db))

    result = engine.remove_unlisted_tracks(100)

    assert result.status == "more_missing"
    assert (result.removed, result.missing, result.library_total) == (0, 150, 400)
    assert result.message == (
        "Plex no longer lists 150 tracks, more than the 100 that were confirmed. "
        "Nothing was removed."
    )
    assert len(db.get_all_tracks()) == 400
    assert _backup_folders(db) == []
    assert len(_history(db)) == rows_before

    # Exactly the confirmed number is not "more".
    assert engine.remove_unlisted_tracks(150).status == "removed"
    assert len(db.get_all_tracks()) == 250


def test_remove_unlisted_tracks_does_nothing_when_listed_again(db: SQLiteManager) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(400) + ["new0"]))
    rows_before = len(_history(db))

    result = engine.remove_unlisted_tracks(100)

    assert result.status == "listed_again"
    assert (result.removed, result.missing) == (0, 0)
    assert result.message == "Plex lists those tracks again. Nothing was removed."
    assert len(db.get_all_tracks()) == 400
    assert _backup_folders(db) == []
    assert len(_history(db)) == rows_before


@pytest.mark.parametrize(
    "relisted, message",
    [
        (
            PlexReadInterrupted("tracks"),
            "Plex could not list the library's tracks. Nothing was removed.",
        ),
        (
            ConnectionError(f"refused; {PLEX_URL}/library/sections/1/all"),
            "Plex could not list the library's tracks. Nothing was removed.",
        ),
        # Not a set of keys at all, as a client stand-in would give.
        (MagicMock(), "Plex could not list the library's tracks. Nothing was removed."),
        (None, "Plex could not list the library's tracks. Nothing was removed."),
        # An empty listing never removes anything, confirmed or not.
        (frozenset(), "Plex listed 0 tracks. Nothing was removed."),
    ],
    ids=["interrupted", "connection-error", "mock", "none", "empty"],
)
def test_remove_unlisted_tracks_does_nothing_when_listing_fails(
    relisted: Any, message: str, db: SQLiteManager, caplog: pytest.LogCaptureFixture
) -> None:
    engine, plex = _held_removal(db, relisted=relisted)
    rows_before = len(_history(db))

    with caplog.at_level(logging.DEBUG):
        result = engine.remove_unlisted_tracks(400)

    assert result.status == "listing_failed"
    assert result.removed == 0
    assert result.message == message
    assert len(db.get_all_tracks()) == 400
    assert _backup_folders(db) == []
    assert len(_history(db)) == rows_before
    assert plex.listings == 1
    for secret in ("leakuser", DUMMY_PASSWORD):
        assert secret not in caplog.text


def test_remove_unlisted_tracks_keeps_everything_when_backup_fails(db: SQLiteManager) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))
    rows_before = len(_history(db))

    with patch("plexmix.plex.sync.backup_library", side_effect=BackupError("The disk is full")):
        result = engine.remove_unlisted_tracks(100)

    assert result.status == "backup_failed"
    assert (result.removed, result.missing) == (0, 100)
    assert result.message == (
        "The backup could not be made: The disk is full. Nothing was removed."
    )
    assert result.backup_folder is None
    assert len(db.get_all_tracks()) == 400
    assert len(_history(db)) == rows_before


OTHER_50 = (
    " 50 other tracks are missing from Plex now. They were not part of this "
    "confirmation and were kept. Run a sync to review them."
)


def test_remove_unlisted_tracks_removes_only_the_tracks_that_were_held(db: SQLiteManager) -> None:
    """Spec: a confirmed removal removes "only tracks that are still not listed"."""
    # The sync held t300 to t399. Now Plex lists t50 to t349: half of the held tracks
    # are listed again, and t0 to t49, which nobody was asked about, are missing.
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300, start=50)))
    held_keys = engine.outcome.held_keys
    assert held_keys == frozenset(_keys(100, start=300))

    result = engine.remove_unlisted_tracks(100, held_keys=held_keys)

    assert result.status == "removed"
    assert (result.removed, result.missing, result.other_missing) == (50, 100, 50)
    # The user confirmed 100 tracks: the result also says where the other 50 went.
    assert result.listed_again == 50
    assert result.message == (
        "Removed 50 tracks that Plex no longer lists. Plex lists 50 of the confirmed "
        f"tracks again, and they were kept.{OTHER_50}"
    )
    # Only held tracks that are still not listed are gone.
    assert _track_keys(db) == sorted(_keys(350))
    row = _history(db)[-1]
    assert (row["status"], row["tracks_removed"]) == ("partial", 50)
    assert row["error_message"] == OTHER_50.strip()


def test_remove_unlisted_tracks_keeps_tracks_that_went_missing_after_the_question(
    db: SQLiteManager,
) -> None:
    # Every held track is listed again; 50 other tracks are missing now. That is not
    # more than the 100 that were confirmed, so the count alone would let them go.
    engine, plex = _held_removal(db, relisted=frozenset(_keys(350, start=50)))
    rows_before = len(_history(db))

    result = engine.remove_unlisted_tracks(100, held_keys=engine.outcome.held_keys)

    assert result.status == "listed_again"
    assert (result.removed, result.missing, result.other_missing) == (0, 50, 50)
    assert result.message == f"Plex lists those tracks again. Nothing was removed.{OTHER_50}"
    assert len(db.get_all_tracks()) == 400
    assert _backup_folders(db) == []
    assert len(_history(db)) == rows_before


def test_remove_unlisted_tracks_with_keys_still_refuses_when_more_are_missing(
    db: SQLiteManager,
) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(250)))

    result = engine.remove_unlisted_tracks(100, held_keys=engine.outcome.held_keys)

    # The count rule comes first: 150 are missing, 100 were confirmed.
    assert result.status == "more_missing"
    assert (result.removed, result.missing) == (0, 150)
    # The tracks of the new count are named, for a caller that asks again.
    assert result.missing_keys == frozenset(_keys(150, start=250))
    assert len(db.get_all_tracks()) == 400
    assert _backup_folders(db) == []


@pytest.mark.parametrize("held_keys", [["t300"], "t300", {"t300": 1}, MagicMock()])
def test_remove_unlisted_tracks_needs_real_keys(held_keys: Any, db: SQLiteManager) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))

    with pytest.raises(ValueError):
        engine.remove_unlisted_tracks(100, held_keys=held_keys)

    assert len(db.get_all_tracks()) == 400
    assert plex.listings == 0


def test_remove_unlisted_tracks_stops_when_cancelled_while_plex_is_listed(
    db: SQLiteManager,
) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))
    rows_before = len(_history(db))
    cancel = Event()
    list_track_keys = plex.list_track_keys

    def cancelled_while_listing() -> Any:
        cancel.set()
        return list_track_keys()

    plex.list_track_keys = cancelled_while_listing  # type: ignore[method-assign]

    result = engine.remove_unlisted_tracks(100, cancel_event=cancel)

    assert result.status == "cancelled"
    assert result.removed == 0
    assert result.message == "The removal was cancelled. Nothing was removed."
    assert len(db.get_all_tracks()) == 400
    # No backup was started for a removal that will not happen.
    assert result.backup_folder is None and _backup_folders(db) == []
    assert len(_history(db)) == rows_before


def test_remove_unlisted_tracks_stops_when_cancelled_while_the_backup_is_written(
    db: SQLiteManager,
) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))
    rows_before = len(_history(db))
    cancel = Event()

    def cancelled_during_backup(database: SQLiteManager, index_path: Optional[Path]) -> Any:
        made = backup_library(database, index_path)
        cancel.set()
        return made

    with patch("plexmix.plex.sync.backup_library", cancelled_during_backup):
        result = engine.remove_unlisted_tracks(100, cancel_event=cancel)

    assert result.status == "cancelled"
    assert result.removed == 0
    assert len(db.get_all_tracks()) == 400
    assert len(_history(db)) == rows_before
    # The copy that was already made stays, and the result names it.
    assert result.backup_folder is not None
    assert _backup_folders(db) == [result.backup_folder]
    assert result.backup_size_bytes > 0

    # Without a cancel request the same removal goes through.
    cancel.clear()
    assert engine.remove_unlisted_tracks(100, cancel_event=cancel).status == "removed"
    assert len(db.get_all_tracks()) == 300


@pytest.mark.parametrize("confirmed", [True, None, "100", 99.5, -1, MagicMock()])
def test_remove_unlisted_tracks_needs_a_real_count(confirmed: Any, db: SQLiteManager) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))

    with pytest.raises(ValueError):
        engine.remove_unlisted_tracks(confirmed)

    assert len(db.get_all_tracks()) == 400
    assert plex.listings == 0


def test_remove_unlisted_tracks_stops_when_cancelled_while_the_tracks_are_deleted(
    db: SQLiteManager,
) -> None:
    """Deleting is where a removal spends its time, so a cancel has to work there too."""
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))
    rows_before = len(_history(db))
    cancel = Event()
    deleted = _cancel_at_the_fifth_delete(db, cancel)

    result = engine.remove_unlisted_tracks(
        100, held_keys=engine.outcome.held_keys, cancel_event=cancel
    )

    assert result.status == "cancelled"
    assert result.removed == 0
    assert result.message == "The removal was cancelled. Nothing was removed."
    # It stopped at the request, and the deletes it had made were undone.
    assert len(deleted) == 5
    assert len(db.get_all_tracks()) == 400
    assert len(_history(db)) == rows_before
    # The backup was made before the deletes started, and the result names it.
    assert result.backup_folder is not None
    assert _backup_folders(db) == [result.backup_folder]
    assert engine.last_removal == result


@pytest.mark.parametrize("fails", ["delete", "history-row"])
def test_remove_unlisted_tracks_that_cannot_delete_names_its_backup(
    fails: str, db: SQLiteManager, caplog: pytest.LogCaptureFixture
) -> None:
    """A removal that fails after its backup returns a result. The copy is not left unnamed."""
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))
    rows_before = len(_history(db))
    error = sqlite3.OperationalError(f"database is locked; {PLEX_URL}/library")
    broken = "delete_track" if fails == "delete" else "insert_sync_record"

    with patch.object(db, broken, side_effect=error), caplog.at_level(logging.DEBUG):
        result = engine.remove_unlisted_tracks(100, held_keys=engine.outcome.held_keys)

    assert result.status == REMOVAL_FAILED == "failed"
    assert result.removed == 0
    assert result.message == (
        "The tracks could not be removed: database is locked; "
        "http://***@127.0.0.1:9/library. Nothing was removed."
    )
    # The deletes and the history row are one transaction: neither was saved.
    assert len(db.get_all_tracks()) == 400
    assert len(_history(db)) == rows_before
    assert result.backup_folder is not None and result.backup_size_bytes > 0
    assert _backup_folders(db) == [result.backup_folder]
    assert engine.last_removal == result
    for secret in ("leakuser", DUMMY_PASSWORD):
        assert secret not in caplog.text


def test_interrupted_removal_says_how_far_it_got(db: SQLiteManager) -> None:
    """Ctrl+C gives the caller no result, so the engine keeps what the call had done."""
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))
    assert engine.last_removal is None
    rows_before = len(_history(db))
    delete_track = db.delete_track
    deleted: List[int] = []

    def interrupted_at_the_eighth(track_id: int) -> None:
        if len(deleted) == 7:
            raise KeyboardInterrupt()
        deleted.append(track_id)
        delete_track(track_id)

    with patch.object(db, "delete_track", side_effect=interrupted_at_the_eighth):
        with pytest.raises(KeyboardInterrupt):
            engine.remove_unlisted_tracks(100, held_keys=engine.outcome.held_keys)

    # Interrupted while the tracks were deleted: nothing is removed, and the backup is named.
    assert len(db.get_all_tracks()) == 400
    assert len(_history(db)) == rows_before
    state = engine.last_removal
    assert state is not None and state.removed == 0
    assert state.backup_folder is not None and _backup_folders(db) == [state.backup_folder]

    # A new call starts without the old state.
    plex.relisted = PlexReadInterrupted("tracks")
    assert engine.remove_unlisted_tracks(100).status == "listing_failed"
    assert engine.last_removal is None


@pytest.mark.parametrize("confirmed", [False, True], ids=["allowed", "confirmed"])
def test_interrupt_after_the_deletes_were_saved_is_recorded_as_a_removal(
    confirmed: bool, db: SQLiteManager
) -> None:
    """Ctrl+C while the index is updated: the tracks are gone, and the history says so."""
    ids = _seed(db, _keys(400))
    _saved_index(db, ids)
    index_path = db.db_path.parent / "embeddings.index"
    plex = _FakePlex(_keys(300), relisted=frozenset(_keys(300)))
    engine = _engine(plex, db, saved_index_path=index_path, saved_index_dimension=DIMENSION)
    if confirmed:
        engine.incremental_sync(generate_embeddings=False)
        assert engine.outcome.held == 100
    save_index = VectorIndex.save_index
    saves: List[str] = []

    def interrupted_once(index: VectorIndex, path: str) -> None:
        saves.append(path)
        if len(saves) == 1:
            raise KeyboardInterrupt()
        save_index(index, path)

    with patch.object(VectorIndex, "save_index", interrupted_once):
        with pytest.raises(KeyboardInterrupt):
            if confirmed:
                engine.remove_unlisted_tracks(100, held_keys=engine.outcome.held_keys)
            else:
                engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    # The deletes had been saved before the interrupt.
    assert len(db.get_all_tracks()) == 300
    row = _history(db)[-1]
    assert row["tracks_removed"] == 100
    assert row["status"] == ("success" if confirmed else "interrupted")
    if confirmed:
        state = engine.last_removal
        assert state is not None and (state.status, state.removed) == ("removed", 100)
        assert state.backup_folder is not None
    else:
        assert engine.outcome.removed == 100
        assert engine.outcome.backup_folder is not None
    # The index update was finished before the interrupt went on: the saved index
    # no longer holds the removed tracks, and no half-written file is left.
    assert len(saves) == 2
    assert _ids_in_saved_index(db) == sorted(ids[key] for key in _keys(300))
    assert sorted(path.name for path in index_path.parent.glob("embeddings.*")) == [
        "embeddings.index",
        "embeddings.metadata",
    ]


@pytest.mark.parametrize(
    "held_keys",
    [frozenset(), set(), frozenset({300, 301}), frozenset({"t300", 301})],
    ids=["empty-frozenset", "empty-set", "numbers", "mixed"],
)
def test_remove_unlisted_tracks_refuses_keys_that_can_match_nothing(
    held_keys: Any, db: SQLiteManager
) -> None:
    """Empty keys or keys of another type would make every missing track "not confirmed"."""
    engine, plex = _held_removal(db, relisted=frozenset(_keys(300)))

    with pytest.raises(ValueError):
        engine.remove_unlisted_tracks(100, held_keys=held_keys)

    assert len(db.get_all_tracks()) == 400
    # Refused before Plex was asked and before a backup was made.
    assert plex.listings == 0
    assert _backup_folders(db) == []


def test_confirmed_tracks_that_left_the_library_are_not_called_listed_again(
    db: SQLiteManager,
) -> None:
    engine, plex = _held_removal(db, relisted=frozenset(_keys(250, start=50)))
    held_keys = engine.outcome.held_keys
    # The held tracks were deleted from PlexMix by hand after the sync. Plex still
    # does not list them, and 50 other tracks (t0 to t49) are missing now.
    with db.deferred_commits():
        for track in db.get_all_tracks():
            if track.plex_key in held_keys and track.id is not None:
                db.delete_track(track.id)
    rows_before = len(_history(db))

    result = engine.remove_unlisted_tracks(100, held_keys=held_keys)

    assert result.status == "listed_again"
    assert (result.removed, result.missing, result.other_missing) == (0, 50, 50)
    assert result.listed_again == 0
    # No held track is listed again, so the result does not say so.
    assert result.message == (
        f"The confirmed tracks are no longer in the PlexMix library. Nothing was removed.{OTHER_50}"
    )
    assert len(db.get_all_tracks()) == 300
    assert _backup_folders(db) == []
    assert len(_history(db)) == rows_before


def test_removal_result_counts_the_confirmed_tracks_that_are_listed_again(
    db: SQLiteManager,
) -> None:
    # Plex lists 1 of the 100 held tracks again, and no other track is missing.
    engine, plex = _held_removal(db, relisted=frozenset(_keys(301)))

    result = engine.remove_unlisted_tracks(100, held_keys=engine.outcome.held_keys)

    assert (result.status, result.removed, result.listed_again) == ("removed", 99, 1)
    assert result.message == (
        "Removed 99 tracks that Plex no longer lists. Plex lists 1 of the confirmed "
        "tracks again, and it was kept."
    )
    row = _history(db)[-1]
    assert (row["status"], row["tracks_removed"], row["error_message"]) == ("success", 99, None)

    # Without the keys only the count is known, so nothing is said about the rest:
    # 150 were confirmed, and 101 of the 301 tracks are missing now.
    plex.relisted = frozenset(_keys(200))
    result = engine.remove_unlisted_tracks(150)
    assert (result.status, result.removed, result.listed_again) == ("removed", 101, 0)
    assert result.message == "Removed 101 tracks that Plex no longer lists."


# ===========================================================================
# 9. Removed tracks leave the vector index too
# ===========================================================================


def _saved_index(db: SQLiteManager, ids: Dict[str, int]) -> VectorIndex:
    index_path = db.db_path.parent / "embeddings.index"
    index = VectorIndex(dimension=DIMENSION, index_path=str(index_path))
    index.build_index([_vector(track_id) for track_id in ids.values()], list(ids.values()))
    index.save_index(str(index_path))
    return index


def _ids_in_saved_index(db: SQLiteManager) -> List[int]:
    reloaded = VectorIndex(
        dimension=DIMENSION, index_path=str(db.db_path.parent / "embeddings.index")
    )
    return sorted(reloaded.track_ids)


def test_removed_tracks_leave_the_index(db: SQLiteManager) -> None:
    ids = _seed(db, _keys(8))
    _add_embeddings_and_playlist(db, ids)
    index = _saved_index(db, ids)
    assert _ids_in_saved_index(db) == sorted(ids.values())

    # A small removal during a sync: t6 and t7 are no longer listed.
    engine = _engine(_FakePlex(_keys(6)), db, vector_index=index)
    result = engine.incremental_sync(generate_embeddings=False)

    assert result.tracks_removed == 2
    kept = sorted(ids[key] for key in _keys(6))
    assert sorted(index.track_ids) == kept
    assert _ids_in_saved_index(db) == kept
    assert index.index is not None and index.index.ntotal == 6

    # A confirmed removal: t3 to t5 are no longer listed either.
    engine = _engine(_FakePlex([], relisted=frozenset(_keys(3))), db, vector_index=index)
    removal = engine.remove_unlisted_tracks(3)

    assert removal.status == "removed" and removal.removed == 3
    kept = sorted(ids[key] for key in _keys(3))
    assert sorted(index.track_ids) == kept
    assert _ids_in_saved_index(db) == kept
    # The backup made before it holds the index files as they were.
    assert removal.backup_folder is not None
    saved = sorted(path.name for path in removal.backup_folder.iterdir())
    assert saved == ["embeddings.index", "embeddings.metadata", "plexmix.db"]
    assert _tracks_in_copy(removal.backup_folder / "plexmix.db") == 6


def test_saved_index_is_backed_up_and_updated_without_an_index_object(db: SQLiteManager) -> None:
    """A sync without embeddings has no index object. The index files are still copied."""
    ids = _seed(db, _keys(400))
    _saved_index(db, ids)
    index_path = db.db_path.parent / "embeddings.index"
    engine = _engine(
        _FakePlex(_keys(300)), db, saved_index_path=index_path, saved_index_dimension=DIMENSION
    )

    result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    assert result.tracks_removed == 100
    folder = engine.outcome.backup_folder
    assert folder is not None
    saved = sorted(path.name for path in folder.iterdir())
    assert saved == ["embeddings.index", "embeddings.metadata", "plexmix.db"]
    # The copy holds the index from before the removal.
    copied = VectorIndex(dimension=DIMENSION, index_path=str(folder / "embeddings.index"))
    assert len(copied.track_ids) == 400
    # The live index no longer holds the removed tracks.
    assert _ids_in_saved_index(db) == sorted(ids[key] for key in _keys(300))


@pytest.mark.parametrize("confirmed", [False, True], ids=["allowed", "confirmed"])
def test_saved_index_is_copied_before_it_is_opened(confirmed: bool, db: SQLiteManager) -> None:
    """An index that cannot be loaded does not survive being opened, so the backup goes first."""
    _seed(db, _keys(400))
    index_path = db.db_path.parent / "embeddings.index"
    index_path.write_bytes(b"not a search index")
    index_path.with_suffix(".metadata").write_bytes(b"not its metadata")
    steps: List[str] = []

    def backup_step(database: SQLiteManager, path: Optional[Path]) -> Any:
        steps.append("backup")
        return backup_library(database, path)

    def open_step(**kwargs: Any) -> VectorIndex:
        steps.append("open")
        return VectorIndex(**kwargs)

    plex = _FakePlex(_keys(300), relisted=frozenset(_keys(300)))
    engine = _engine(plex, db, saved_index_path=index_path, saved_index_dimension=DIMENSION)
    with patch("plexmix.plex.sync.backup_library", backup_step), patch(
        "plexmix.plex.sync.VectorIndex", side_effect=open_step
    ):
        if confirmed:
            engine.incremental_sync(generate_embeddings=False)
            folder = engine.remove_unlisted_tracks(100).backup_folder
        else:
            engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)
            folder = engine.outcome.backup_folder

    assert steps == ["backup", "open"]
    assert len(db.get_all_tracks()) == 300
    assert folder is not None
    assert (folder / "embeddings.index").read_bytes() == b"not a search index"
    assert (folder / "embeddings.metadata").read_bytes() == b"not its metadata"


def test_small_removal_leaves_a_saved_index_alone(db: SQLiteManager) -> None:
    """No backup is made for a small removal, so a saved index is not opened for it."""
    ids = _seed(db, _keys(8))
    _saved_index(db, ids)
    index_path = db.db_path.parent / "embeddings.index"
    before = (index_path.read_bytes(), index_path.with_suffix(".metadata").read_bytes())
    engine = _engine(
        _FakePlex(_keys(6)), db, saved_index_path=index_path, saved_index_dimension=DIMENSION
    )

    with patch("plexmix.plex.sync.VectorIndex") as opened:
        result = engine.incremental_sync(generate_embeddings=False)

    assert result.tracks_removed == 2
    assert _backup_folders(db) == []
    opened.assert_not_called()
    assert (index_path.read_bytes(), index_path.with_suffix(".metadata").read_bytes()) == before


def test_saved_index_without_a_dimension_is_copied_and_left_alone(db: SQLiteManager) -> None:
    ids = _seed(db, _keys(400))
    _saved_index(db, ids)
    index_path = db.db_path.parent / "embeddings.index"
    engine = _engine(_FakePlex(_keys(300)), db, saved_index_path=index_path)

    with patch("plexmix.plex.sync.VectorIndex") as opened:
        engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    opened.assert_not_called()
    folder = engine.outcome.backup_folder
    assert folder is not None and (folder / "embeddings.index").is_file()
    assert _ids_in_saved_index(db) == sorted(ids.values())


def test_unreadable_saved_index_is_not_replaced_by_an_empty_one(db: SQLiteManager) -> None:
    """An index that cannot be loaded has no vectors to drop. No empty index takes its place."""
    _seed(db, _keys(400))
    index_path = db.db_path.parent / "embeddings.index"
    index_path.write_bytes(b"not a search index")
    index_path.with_suffix(".metadata").write_bytes(b"not its metadata")
    engine = _engine(
        _FakePlex(_keys(300)), db, saved_index_path=index_path, saved_index_dimension=DIMENSION
    )

    with patch.object(VectorIndex, "save_index") as saved:
        result = engine.incremental_sync(generate_embeddings=False, allow_large_removals=True)

    assert result.tracks_removed == 100
    saved.assert_not_called()
    # The backup holds the file as it was.
    folder = engine.outcome.backup_folder
    assert folder is not None
    assert (folder / "embeddings.index").read_bytes() == b"not a search index"
    # No valid, empty index stands at the path as if it were the library's index.
    if index_path.exists():
        assert index_path.read_bytes() == b"not a search index"


# ===========================================================================
# 10. A stopped regenerate has changed nothing
# ===========================================================================


class _StoppedPlex(_FakePlex):
    """Lists six tracks and stops answering while they are read."""

    def get_all_tracks(self, batch_size: int = 100) -> Iterator[List[Track]]:
        raise PlexReadInterrupted("tracks", read=3, listed=6)
        yield []  # pragma: no cover  (makes this a generator, like the real method)


def _regenerate_marker(db: SQLiteManager) -> Path:
    """The file a regenerate run writes when it starts to replace tags or embeddings."""
    return db.db_path.with_name(db.db_path.name + ".regenerate.json")


def test_stopped_regenerate_changes_nothing_and_its_history_row_says_so(
    db: SQLiteManager,
) -> None:
    """ "Nothing was changed" is the whole truth: a regenerate clears nothing first.

    ``regenerate_sync`` used to clear the tags and embeddings before it read Plex,
    and a sentence about that was added to the row. Both are gone.
    """
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    before = _snapshot(db)
    ai_provider = MagicMock()
    engine = _engine(_StoppedPlex(_keys(6)), db, ai_provider=ai_provider)
    sentence = "Plex stopped answering after 3 of 6 tracks were read, so nothing was changed."

    with pytest.raises(PlexReadInterrupted):
        engine.regenerate_sync(generate_embeddings=False)

    # Every tag and every embedding row is still there, and no provider was asked.
    assert _snapshot(db) == before
    assert all(track.tags for track in db.get_all_tracks())
    assert len(_rows(db, "embeddings")) == 6
    ai_provider.complete.assert_not_called()
    assert not _regenerate_marker(db).exists()
    row = _history(db)[-1]
    assert row["status"] == "failed" and row["error_message"] == sentence
    # The run's own result agrees: nothing written, every track kept.
    result = engine.regenerate_result
    assert result.requested is True and result.wrote_nothing is True
    assert result.tags.started is False and result.tracks_kept == 6
    for gone in ("cleared_before_sync", "_failure_note"):
        assert not hasattr(engine, gone)

    # A plain sync on the same engine writes the same sentence.
    with pytest.raises(PlexReadInterrupted):
        engine.incremental_sync(generate_embeddings=False)
    assert _history(db)[-1]["error_message"] == sentence


def test_regenerate_that_cannot_reach_plex_clears_nothing(db: SQLiteManager) -> None:
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    before = _snapshot(db)
    plex = _FakePlex(_keys(6))
    plex.validate_token = lambda: (False, "Cannot reach Plex server")  # type: ignore[method-assign]
    engine = _engine(plex, db, ai_provider=MagicMock())

    with pytest.raises(ConnectionError):
        engine.regenerate_sync(generate_embeddings=False)

    assert _snapshot(db) == before
    assert not _regenerate_marker(db).exists()
    assert engine.regenerate_result.wrote_nothing is True
    assert engine.regenerate_result.tracks_kept == 6


def test_a_plain_sync_does_not_keep_the_result_of_an_earlier_regenerate(
    db: SQLiteManager,
) -> None:
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    engine = _engine(_StoppedPlex(_keys(6)), db, ai_provider=MagicMock())
    with pytest.raises(PlexReadInterrupted):
        engine.regenerate_sync(generate_embeddings=False)
    stopped = engine.regenerate_result
    assert stopped.requested is True and stopped.tracks_kept == 6

    # The next run on the same engine regenerates nothing, so the result must not
    # say that a regenerate run kept tracks.
    with pytest.raises(PlexReadInterrupted):
        engine.incremental_sync(generate_embeddings=False)
    assert engine.regenerate_result is not stopped
    assert engine.regenerate_result.requested is False
    assert engine.regenerate_result.tracks_kept == 0
    with pytest.raises(PlexReadInterrupted):
        engine.full_sync(generate_embeddings=False)
    assert engine.regenerate_result.requested is False


@pytest.mark.parametrize(
    "reason, sentence",
    [
        (
            "Cannot reach Plex server: no answer",
            "Cannot reach Plex server: no answer. Nothing was changed.",
        ),
        (
            "Plex token is invalid or expired. Please update your token.",
            "Plex token is invalid or expired. Please update your token. Nothing was changed.",
        ),
        ("", "The Plex connection could not be checked. Nothing was changed."),
    ],
    ids=["no-answer", "token", "no-reason"],
)
@pytest.mark.parametrize("regenerate", [False, True], ids=["sync", "regenerate"])
def test_failed_connection_check_says_that_nothing_was_changed(
    regenerate: bool, reason: str, sentence: str, db: SQLiteManager
) -> None:
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    before = _snapshot(db)
    plex = _FakePlex(_keys(3))
    plex.validate_token = lambda: (False, reason)  # type: ignore[method-assign]
    # With an AI provider: a regenerate run is refused without one, before it starts.
    ai_provider = MagicMock()
    engine = _engine(plex, db, ai_provider=ai_provider)

    with pytest.raises(ConnectionError) as stopped:
        if regenerate:
            engine.regenerate_sync(generate_embeddings=False)
        else:
            engine.incremental_sync(generate_embeddings=False)

    assert str(stopped.value) == sentence
    # And that is true: the check comes before any read or write, and a regenerate
    # run clears nothing and has not started to replace anything.
    assert _snapshot(db) == before
    assert engine.outcome.compared is False
    assert plex.full_reads == 0
    ai_provider.complete.assert_not_called()
    assert not _regenerate_marker(db).exists()


def test_regenerate_checks_the_connection_once_and_before_any_change(
    db: SQLiteManager,
) -> None:
    """A regenerate used to check Plex, clear, and check again in the sync it started.

    The second check could fail after the tags and embeddings were gone, and its
    text then ended with "The library was not synced." Now there is one check, it
    comes before any change, and its text says so.
    """
    ids = _seed(db, _keys(6))
    _add_embeddings_and_playlist(db, ids)
    before = _snapshot(db)
    plex = _FakePlex(_keys(6))
    # One answer only: a second check would find the list empty and fail the test.
    answers = [(False, "Cannot reach Plex server: no answer")]
    plex.validate_token = lambda: answers.pop(0)  # type: ignore[method-assign]
    ai_provider = MagicMock()
    ai_provider.complete.return_value = "{}"  # a reply that tags no track
    engine = _engine(plex, db, ai_provider=ai_provider)

    with pytest.raises(ConnectionError) as stopped:
        engine.regenerate_sync(generate_embeddings=False)

    assert answers == []
    assert str(stopped.value) == "Cannot reach Plex server: no answer. Nothing was changed."
    assert _snapshot(db) == before
    assert len(_rows(db, "embeddings")) == 6
    assert plex.full_reads == 0

    ai_provider.complete.assert_not_called()

    # A run that passes the check asks Plex once as well.
    answers.append((True, "Connected"))
    engine.regenerate_sync(generate_embeddings=False)
    assert answers == []
    ai_provider.complete.assert_called_once()
    # The reply held no tags, so every track still has the tags it had.
    assert sorted(track.tags or "" for track in db.get_all_tracks()) == sorted(
        f"tag-{key}" for key in _keys(6)
    )
    assert _snapshot(db)["embeddings"] == before["embeddings"]
