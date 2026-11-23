"""The guards that the CLI audit named as already good, run again after the change.

``.impeccable/audit/2026-11-18T11-15-05Z__cli.md`` ("Positive Findings") lists what
the CLI did well on the day of the audit. The change ``fix-cli-secrets-and-data-loss``
reworked the sync, the regenerate commands, the removal step and the search index, so
each of those guards is run again here, with ``CliRunner`` and temporary folders.
Only Plex, the providers and the keyring are fakes. Every run checks what is left in
the library, not only what is printed.

Some tests are also in the files of the commands (the removal questions, the
regenerate questions). The ones here are the guards that no other file checks end to
end: ``db reset``, an aborted ``doctor --force`` and ``sync regenerate`` with the index
files and the run marker, Ctrl+C in a plain sync, a 401 from the AI provider, and the
promises that scripts rely on.
"""

import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from unittest.mock import patch

import click
import pytest
import typer
from click.testing import Result
from typer.testing import CliRunner

from plexmix.cli.main import app
from plexmix.config.settings import (
    AISettings,
    AudioSettings,
    DatabaseSettings,
    EmbeddingSettings,
    PlaylistSettings,
    PlexSettings,
    Settings,
)
from plexmix.database.models import Album, Artist, Embedding, Playlist, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex.client import PlexReadReport
from plexmix.services.regenerate_service import CheckedProviders, marker_path

# The only "secrets" in this file are the project's three dummy values.
DUMMY_KEY = "dummy-api-key-0000"
DUMMY_TOKEN = "dummy-plex-token-0000"
DUMMY_PASSWORD = "dummy-ui-password-0000"

LIBRARY_SIZE = 120
OLD_VECTOR = [0.1, 0.2, 0.3, 0.4]
NEW_VECTOR = [9.0, 1.0, 1.0, 1.0]
# The embedding provider of these tests, as the settings name it.
VECTOR_MODEL = "openai"
LONG_AGO = datetime(2020, 1, 1, 9, 0, 0)

_ESCAPE = "\x1b"


def _runner(charset: str = "utf-8") -> CliRunner:
    """A runner that keeps standard error apart (click 8.2 and later always does)."""
    try:
        return CliRunner(charset=charset, mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner(charset=charset)


runner = _runner()


# ---------------------------------------------------------------------------
# Fakes and the temporary library
# ---------------------------------------------------------------------------


def _flat(text: str) -> str:
    """The output as one line: the console wraps a long sentence at the terminal width."""
    return " ".join(text.split())


def _squeezed(text: str) -> str:
    """The output without any white space, to look for a path that a wrap could split."""
    return "".join(text.split())


def _settings(folder: Path) -> Settings:
    """Settings that point at ``folder``, with named providers and no side stages."""
    settings = Settings(
        plex=PlexSettings(url="http://localhost:32400", library_name="Music"),
        database=DatabaseSettings(
            path=str(folder / "test.db"), faiss_index_path=str(folder / "test.index")
        ),
        ai=AISettings(),
        embedding=EmbeddingSettings(),
        playlist=PlaylistSettings(),
        audio=AudioSettings(),
    )
    settings.musicbrainz.enabled = False
    settings.audio.analyze_on_sync = False
    settings.ai.default_provider = "openai"
    settings.ai.model = "gpt-test"
    settings.embedding.default_provider = VECTOR_MODEL
    settings.embedding.model = "embed-test"
    return settings


def _keys(count: int) -> List[str]:
    return [f"t{number}" for number in range(count)]


def _plex_track(key: str) -> Track:
    track = Track(plex_key=key, title=f"Song {key}", artist_id=0, album_id=0, play_count=0)
    track.__dict__["_artist_key"] = "a1"
    track.__dict__["_album_key"] = "al1"
    return track


class _Plex:
    """Stands in for the connected ``PlexClient``. It makes no request.

    ``keys`` are the tracks in its listing. ``stop`` is raised in place of the track
    reads (Ctrl+C, for one). ``token_error`` makes the connection check fail with that
    reason, as an unreachable or unauthorised server does.
    """

    def __init__(
        self,
        keys: Iterable[str],
        stop: Optional[BaseException] = None,
        token_error: Optional[str] = None,
    ) -> None:
        self.keys = list(keys)
        self.stop = stop
        self.token_error = token_error
        self.read_report = PlexReadReport(
            listed_track_keys=frozenset(self.keys),
            listed={"artists": 1, "albums": 1, "tracks": len(self.keys)},
            failed={"artists": 0, "albums": 0, "tracks": 0},
        )

    def validate_token(self) -> Tuple[bool, str]:
        if self.token_error is not None:
            return False, self.token_error
        return True, "Connected"

    def get_all_artists(self, batch_size: int = 100) -> Iterator[List[Artist]]:
        yield [Artist(plex_key="a1", name="Artist One")]

    def get_all_albums(self, batch_size: int = 100) -> Iterator[List[Album]]:
        album = Album(plex_key="al1", title="Album One", artist_id=0)
        album.__dict__["_artist_key"] = "a1"
        yield [album]

    def get_all_tracks(self, batch_size: int = 100) -> Iterator[List[Track]]:
        if self.stop is not None:
            raise self.stop
        if self.keys:
            yield [_plex_track(key) for key in self.keys]

    def list_track_keys(self) -> Any:
        return frozenset(self.keys)


def _ids_in_prompt(prompt: str) -> List[int]:
    """The ids of the tracks a tagging request lists."""
    listed = prompt.split("Assign tags to the following songs:")[1]
    listed = listed.split("Return a JSON object")[0]
    return [int(entry["id"]) for entry in json.loads(listed)]


class _Tagger:
    """Stands in for the AI provider. It gives every track it is sent a new tag.

    From request ``fail_from`` on (counted from 1) it raises ``error`` in place of an
    answer. ``requests`` holds the track ids of every request.
    """

    provider_name = "OpenAI"
    model = "gpt-test"
    api_key = DUMMY_KEY

    def __init__(self, error: Optional[BaseException] = None, fail_from: int = 1) -> None:
        self.error = error
        self.fail_from = fail_from
        self.requests: List[List[int]] = []

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30,
    ) -> str:
        ids = _ids_in_prompt(prompt)
        self.requests.append(ids)
        if self.error is not None and len(self.requests) >= self.fail_from:
            raise self.error
        return json.dumps(
            {
                str(track_id): {
                    "tags": [f"new-{track_id}"],
                    "environments": ["focus"],
                    "instruments": ["piano"],
                }
                for track_id in ids
            }
        )

    @property
    def sent(self) -> List[int]:
        """Every track that was sent to the provider, by id."""
        return sorted({track_id for request in self.requests for track_id in request})


class _Vectors:
    """Stands in for the embedding generator. Every text gets ``NEW_VECTOR``.

    Request ``interrupt_on`` (counted from 1) raises Ctrl+C in place of an answer.
    ``requests`` holds how many texts each request held.
    """

    provider_name = VECTOR_MODEL

    def __init__(self, interrupt_on: Optional[int] = None) -> None:
        self.interrupt_on = interrupt_on
        self.requests: List[int] = []

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        self.requests.append(len(texts))
        if self.interrupt_on == len(self.requests):
            raise KeyboardInterrupt()
        return [list(NEW_VECTOR) for _ in texts]

    def generate_embedding(self, text: str) -> List[float]:
        return list(NEW_VECTOR)

    def get_dimension(self) -> int:
        return len(NEW_VECTOR)


@pytest.fixture(autouse=True)
def _root_logging_restored() -> Iterator[None]:
    """Undo what the CLI's start-up does to the root logger.

    ``setup_logging`` replaces every handler with one that writes to the stream of that
    run. When ``CliRunner`` closes the stream, a later log record would print a
    "Logging error" into the output of another test. A handler that an earlier test
    module left behind with a closed stream (``test_cli_basic.py`` starts the CLI
    without patching the log setup) does the same to these tests, so it is dropped
    before each one starts: it can never write again.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(getattr(handler, "stream", None), "closed", False) is True:
            root.removeHandler(handler)
    handlers, level = list(root.handlers), root.level
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)


def _invoke(arguments: List[str], invoker: Optional[CliRunner] = None, answers: str = "") -> Result:
    """Run the CLI on ``arguments`` without its log setup (no handler, no log file)."""
    with patch("plexmix.cli.main.setup_logging"):
        return (invoker or runner).invoke(app, arguments, input=answers)


def _fill_library(database_file: Path, tags: bool, embeddings: bool) -> None:
    with SQLiteManager(str(database_file)) as db:
        db.create_tables()
        artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist One"))
        album_id = db.insert_album(Album(plex_key="al1", title="Album One", artist_id=artist_id))
        with db.deferred_commits():
            for number, key in enumerate(_keys(LIBRARY_SIZE), start=1):
                db.insert_track(
                    Track(
                        plex_key=key,
                        title=f"Song {key}",
                        artist_id=artist_id,
                        album_id=album_id,
                        play_count=0,
                        tags=f"old-{number}" if tags else None,
                    )
                )
        if embeddings:
            db.insert_embeddings_batch(
                [
                    Embedding(
                        track_id=track.id,
                        embedding_model=VECTOR_MODEL,
                        embedding_dim=len(OLD_VECTOR),
                        vector=OLD_VECTOR,
                        created_at=LONG_AGO,
                        updated_at=LONG_AGO,
                    )
                    for track in db.get_all_tracks()
                    if track.id is not None
                ]
            )


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A library of 120 tracks with old tags, old embeddings and a saved search index."""
    database_file = tmp_path / "test.db"
    _fill_library(database_file, tags=True, embeddings=True)
    index_path = tmp_path / "test.index"
    index = VectorIndex(dimension=len(OLD_VECTOR), index_path=str(index_path))
    index.build_index([OLD_VECTOR] * LIBRARY_SIZE, list(range(1, LIBRARY_SIZE + 1)))
    assert index.save_index(str(index_path)) is True
    assert sorted(item.name for item in tmp_path.iterdir() if "index" in item.name) == [
        "test.index"
    ]
    return database_file


@pytest.fixture
def untagged_library(tmp_path: Path) -> Path:
    """A library of 120 tracks that have no tags, no embeddings and no search index."""
    database_file = tmp_path / "test.db"
    _fill_library(database_file, tags=False, embeddings=False)
    return database_file


def _snapshot(folder: Path) -> Dict[str, Any]:
    """Everything in the temporary profile that a command could change.

    The database as the text of its dump, and every other file (the search index, its
    metadata, the run marker, a ``backups`` folder, a moved-aside index) by name and
    bytes. SQLite's own side files are left out: opening a database creates them.
    """
    database_file = folder / "test.db"
    dump: List[str] = []
    if database_file.exists():
        with closing(sqlite3.connect(str(database_file))) as connection:
            dump = list(connection.iterdump())
    files: Dict[str, Optional[bytes]] = {}
    for item in sorted(folder.rglob("*")):
        name = item.relative_to(folder).as_posix()
        if name == "test.db" or name.endswith(("-wal", "-shm", "-journal")):
            continue
        files[name] = item.read_bytes() if item.is_file() else None
    return {"database": dump, "files": files}


def _query(database_file: Path, sql: str) -> List[Tuple[Any, ...]]:
    with closing(sqlite3.connect(str(database_file))) as connection:
        return list(connection.execute(sql).fetchall())


def _history(database_file: Path) -> List[str]:
    """The status of each sync history row, oldest first."""
    return [row[0] for row in _query(database_file, "SELECT status FROM sync_history ORDER BY id")]


def _tagged_with(database_file: Path, prefix: str) -> List[int]:
    """The ids of the tracks whose tags start with ``prefix``."""
    rows = _query(database_file, "SELECT id, tags FROM tracks ORDER BY id")
    return [track_id for track_id, tags in rows if tags and tags.startswith(prefix)]


def _untagged(database_file: Path) -> List[int]:
    rows = _query(database_file, "SELECT id FROM tracks WHERE tags IS NULL ORDER BY id")
    return [row[0] for row in rows]


def _embedded(database_file: Path) -> List[int]:
    rows = _query(database_file, "SELECT track_id FROM embeddings ORDER BY track_id")
    return [row[0] for row in rows]


def _ids_in_saved_index(folder: Path) -> List[int]:
    index = VectorIndex(dimension=len(NEW_VECTOR), index_path=str(folder / "test.index"))
    assert index.load_failed is False
    return sorted(index.track_ids)


# ---------------------------------------------------------------------------
# The command runners
# ---------------------------------------------------------------------------


def _db_reset(folder: Path, *args: str, answers: str = "") -> Result:
    """Run ``plexmix db reset`` on the library in ``folder``."""
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch("plexmix.cli.db_cmd.Settings.load_from_file", return_value=_settings(folder))
        )
        return runner.invoke(app, ["db", "reset", *args], input=answers)


def _force(
    library: Path,
    *args: str,
    terminal: bool = False,
    answers: str = "",
    prompt_error: Optional[BaseException] = None,
) -> SimpleNamespace:
    """Run ``doctor --force`` on the library, with fake providers and no Plex.

    ``prompt_error`` is raised in place of the user's answer (Ctrl+C at the question).
    """
    ai, vectors = _Tagger(), _Vectors()
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd.Settings.load_from_file",
                return_value=_settings(library.parent),
            )
        )
        check = stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd.check_providers",
                return_value=CheckedProviders(ai, vectors),
            )
        )
        stack.enter_context(patch("plexmix.cli.console_helpers.can_prompt", return_value=terminal))
        if prompt_error is not None:
            stack.enter_context(
                patch("plexmix.cli.console_helpers.typer.confirm", side_effect=prompt_error)
            )
        result = runner.invoke(app, ["doctor", "--force", *args], input=answers)
    return SimpleNamespace(
        result=result, out=_flat(result.stdout), check=check, ai=ai, vectors=vectors, connect=None
    )


def _regenerate(
    library: Path,
    *args: str,
    plex: Optional[_Plex] = None,
    ai: Optional[_Tagger] = None,
    terminal: bool = False,
    answers: str = "",
    prompt_error: Optional[BaseException] = None,
    connect_error: Optional[BaseException] = None,
    invoker: Optional[CliRunner] = None,
) -> SimpleNamespace:
    """Run ``sync regenerate`` on the real engine and the library, with fakes for the rest.

    The provider check hands the fakes over. ``connect_error`` is raised by the Plex
    connection, as an unreachable server does. ``invoker`` is another runner (a console
    with a limited character set, for one).
    """
    plex = plex or _Plex(_keys(LIBRARY_SIZE))
    ai, vectors = ai or _Tagger(), _Vectors()
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.sync_cmd.Settings.load_from_file",
                return_value=_settings(library.parent),
            )
        )
        connect = stack.enter_context(
            patch(
                "plexmix.cli.sync_cmd.connect_plex",
                return_value=plex,
                side_effect=connect_error,
            )
        )
        check = stack.enter_context(
            patch(
                "plexmix.cli.sync_cmd.check_providers",
                return_value=CheckedProviders(ai, vectors),
            )
        )
        stack.enter_context(patch("plexmix.cli.console_helpers.can_prompt", return_value=terminal))
        stack.enter_context(patch("plexmix.cli.sync_cmd.can_prompt", return_value=terminal))
        if prompt_error is not None:
            stack.enter_context(
                patch("plexmix.cli.console_helpers.typer.confirm", side_effect=prompt_error)
            )
        result = (invoker or runner).invoke(app, ["sync", "regenerate", *args], input=answers)
    return SimpleNamespace(
        result=result,
        out=_flat(result.stdout),
        check=check,
        connect=connect,
        ai=ai,
        vectors=vectors,
    )


def _embeddings_regenerate(
    library: Path,
    *args: str,
    terminal: bool = False,
    answers: str = "",
    prompt_error: Optional[BaseException] = None,
) -> SimpleNamespace:
    """Run ``embeddings generate --regenerate`` on the library, with a fake provider."""
    ai, vectors = _Tagger(), _Vectors()
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.embeddings_cmd.Settings.load_from_file",
                return_value=_settings(library.parent),
            )
        )
        check = stack.enter_context(
            patch(
                "plexmix.cli.embeddings_cmd.check_providers",
                return_value=CheckedProviders(None, vectors),
            )
        )
        stack.enter_context(patch("plexmix.cli.console_helpers.can_prompt", return_value=terminal))
        if prompt_error is not None:
            stack.enter_context(
                patch("plexmix.cli.console_helpers.typer.confirm", side_effect=prompt_error)
            )
        result = runner.invoke(
            app, ["embeddings", "generate", "--regenerate", *args], input=answers
        )
    return SimpleNamespace(
        result=result, out=_flat(result.stdout), check=check, ai=ai, vectors=vectors, connect=None
    )


def _sync(
    folder: Path,
    plex: _Plex,
    *args: str,
    ai: Optional[Any] = None,
    vectors: Optional[_Vectors] = None,
) -> SimpleNamespace:
    """Run a plain ``plexmix sync`` on the real engine and the library in ``folder``.

    Plex, the AI provider and the embedding provider are fakes (``ai`` and ``vectors``;
    without one, that stage is left out, as it is for a user who set up neither).
    Nobody can be asked a question.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch("plexmix.cli.sync_cmd.Settings.load_from_file", return_value=_settings(folder))
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.connect_plex", return_value=plex))
        stack.enter_context(patch("plexmix.cli.sync_cmd._build_ai_provider", return_value=ai))
        stack.enter_context(
            patch("plexmix.cli.sync_cmd._build_embedding_generator", return_value=vectors)
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.can_prompt", return_value=False))
        result = runner.invoke(app, ["sync", *args])
    return SimpleNamespace(result=result, out=_flat(result.stdout), ai=ai, vectors=vectors)


# ---------------------------------------------------------------------------
# a. "db reset" still defaults to No and backs up first
# ---------------------------------------------------------------------------


class TestDbResetGuard:
    """``plexmix db reset`` is the model for a destructive command (audit, Positive Findings)."""

    @pytest.mark.parametrize(
        "answers, exit_code, said",
        [
            ("\n", 0, "Reset cancelled."),
            ("n\n", 0, "Reset cancelled."),
            ("", 1, "Aborted"),
        ],
        ids=["enter", "n", "end-of-input"],
    )
    def test_defaults_to_no_and_changes_nothing(
        self, library: Path, answers: str, exit_code: int, said: str
    ) -> None:
        before = _snapshot(library.parent)

        result = _db_reset(library.parent, answers=answers)

        assert result.exit_code == exit_code
        assert said in result.output
        # The question shows its default, and nothing was deleted or backed up.
        assert "Are you sure you want to reset the database? [y/N]" in result.output
        assert "Deleting database files" not in result.output
        assert "Database reset complete" not in result.output
        assert _snapshot(library.parent) == before
        assert not (library.parent / "backups").exists()

    def test_says_what_is_lost_and_kept_with_the_counts(self, library: Path) -> None:
        result = _db_reset(library.parent, answers="n\n")

        out = _flat(result.stdout)
        assert "This will permanently delete:" in out
        assert "What will be lost:" in out and "What will be preserved:" in out
        assert "Current database contains:" in out
        assert f"{LIBRARY_SIZE:,} tracks" in out and f"{LIBRARY_SIZE:,} embeddings" in out
        assert "PlexMix configuration" in out and "API keys" in out

    def test_counts_use_thousands_separators(self, tmp_path: Path) -> None:
        database_file = tmp_path / "test.db"
        with SQLiteManager(str(database_file)) as db:
            db.create_tables()
            artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist"))
            album_id = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist_id))
            with db.deferred_commits():
                for number in range(1250):
                    db.insert_track(
                        Track(
                            plex_key=f"k{number}",
                            title=f"Song {number}",
                            artist_id=artist_id,
                            album_id=album_id,
                        )
                    )

        result = _db_reset(tmp_path, answers="n\n")

        assert "1,250 tracks" in _flat(result.stdout)

    def test_confirmed_reset_backs_up_before_it_deletes(
        self, library: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        index_file = library.parent / "test.index"
        index_bytes = index_file.read_bytes()
        original_rows = _snapshot(library.parent)["database"]
        seen_at_delete: List[Tuple[str, List[str]]] = []
        real_unlink = Path.unlink

        def unlink_and_look(path: Path, *args: Any, **kwargs: Any) -> None:
            # What the backup folder holds at the moment a live file is deleted.
            backups = sorted((library.parent / "backups").glob("*/*"))
            seen_at_delete.append((path.name, [item.name for item in backups]))
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink_and_look)

        result = _db_reset(library.parent, answers="y\n")

        assert result.exit_code == 0, result.output
        out = _flat(result.stdout)
        assert "Database reset complete!" in out
        # Both live files were backed up before the first one was deleted.
        assert seen_at_delete[0][1] == ["test.db", "test.index"]
        assert [name for name, _ in seen_at_delete] == ["test.db", "test.index"]
        assert not (library.parent / "test.db").exists() and not index_file.exists()
        # The copy is the library as it was.
        [folder] = sorted((library.parent / "backups").iterdir())
        assert (folder / "test.index").read_bytes() == index_bytes
        with closing(sqlite3.connect(str(folder / "test.db"))) as connection:
            assert list(connection.iterdump()) == original_rows
        assert _squeezed(f"Backup saved to: {folder}") in _squeezed(result.stdout)
        # It ends with numbered next steps.
        assert "1. Run 'plexmix sync' to re-sync your Plex library" in out
        assert "2. (Optional) Run 'plexmix tags generate' to re-tag tracks" in out

    def test_force_skips_the_question_but_still_backs_up(self, library: Path) -> None:
        result = _db_reset(library.parent, "--force")

        assert result.exit_code == 0, result.output
        assert "[y/N]" not in result.output
        assert not (library.parent / "test.db").exists()
        [folder] = sorted((library.parent / "backups").iterdir())
        assert sorted(item.name for item in folder.iterdir()) == ["test.db", "test.index"]

    def test_no_backup_flag_is_the_only_way_to_skip_the_backup(self, library: Path) -> None:
        result = _db_reset(library.parent, "--force", "--no-backup")

        assert result.exit_code == 0, result.output
        assert not (library.parent / "test.db").exists()
        assert not (library.parent / "backups").exists()

    @pytest.mark.parametrize("failure", ["folder", "copy"])
    def test_a_backup_that_cannot_be_made_leaves_the_library_untouched(
        self, library: Path, failure: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        if failure == "folder":
            # A file where the backups folder has to be.
            (library.parent / "backups").write_text("not a folder")
        else:

            def full_disk(*args: Any, **kwargs: Any) -> None:
                raise OSError(28, "No space left on device")

            monkeypatch.setattr("shutil.copy2", full_disk)
        before = {
            name: data
            for name, data in _snapshot(library.parent)["files"].items()
            if not name.startswith("backups")
        }
        rows_before = _snapshot(library.parent)["database"]

        result = _db_reset(library.parent, "--force")

        assert result.exit_code == 1
        assert isinstance(result.exception, OSError)
        assert "Deleting database files" not in result.output
        assert "Database reset complete" not in result.output
        assert (library.parent / "test.db").exists()
        after = _snapshot(library.parent)
        assert after["database"] == rows_before
        assert {k: v for k, v in after["files"].items() if not k.startswith("backups")} == before

    def test_nothing_to_reset_creates_nothing(self, tmp_path: Path) -> None:
        result = _db_reset(tmp_path)

        assert result.exit_code == 0
        assert "Nothing to reset." in _flat(result.stdout)
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# b and c. An aborted "doctor --force" and "sync regenerate" change nothing
# ---------------------------------------------------------------------------

# (what is typed, an error raised in place of the answer, whether there is a terminal,
# the exit statuses that are right, what is printed). A Ctrl+C at a question ends with
# 130 and prints nothing since click 8.2, and with 1 and "Aborted!" before.
ABORTS = [
    pytest.param("\n", None, True, (0,), "Operation cancelled.", id="enter"),
    pytest.param("n\n", None, True, (0,), "Operation cancelled.", id="n"),
    pytest.param("", None, True, (1,), "Aborted", id="end-of-input"),
    pytest.param("", KeyboardInterrupt(), True, (1, 130), "", id="ctrl-c-at-the-question"),
    pytest.param("", None, False, (1,), "Nothing was changed.", id="no-terminal-and-no-yes"),
]


def _regenerate_command(kind: str) -> Any:
    """The runner of the regenerate command that ``kind`` names."""
    return {
        "doctor --force": _force,
        "sync regenerate": _regenerate,
        "embeddings generate --regenerate": _embeddings_regenerate,
    }[kind]


@pytest.fixture(params=["doctor --force", "sync regenerate", "embeddings generate --regenerate"])
def regenerate_command(request: pytest.FixtureRequest) -> str:
    """Each of the three commands that replace tags or embeddings (CLAUDE.md)."""
    return str(request.param)


@pytest.fixture(params=["doctor --force", "sync regenerate"])
def resumable_command(request: pytest.FixtureRequest) -> str:
    """The two commands that replace the tags and so can continue a stopped tags run."""
    return str(request.param)


class TestAbortedRegenerateChangesNothing:
    """An aborted ``doctor --force`` or ``sync regenerate`` leaves the library as it was.

    Tags, embeddings, the search index files, the run marker and the history stay as
    they were, no provider is asked, and (for ``sync regenerate``) Plex is not asked.
    """

    @pytest.mark.parametrize("answers, prompt_error, terminal, exit_codes, said", ABORTS)
    def test_aborted_run_changes_nothing(
        self,
        library: Path,
        regenerate_command: str,
        answers: str,
        prompt_error: Optional[BaseException],
        terminal: bool,
        exit_codes: Tuple[int, ...],
        said: str,
    ) -> None:
        before = _snapshot(library.parent)

        run = _regenerate_command(regenerate_command)(
            library, terminal=terminal, answers=answers, prompt_error=prompt_error
        )

        assert run.result.exit_code in exit_codes, run.result.output
        assert said in run.result.output
        # The question was asked with No as the answer to fall back on. (When the answer
        # is an error raised in its place, the question itself is replaced as well.)
        if terminal and prompt_error is None:
            assert "[y/N]" in run.out
        assert "completed" not in run.out.lower() and "complete!" not in run.out.lower()
        run.check.assert_not_called()
        if run.connect is not None:
            run.connect.assert_not_called()
        assert run.ai.requests == [] and run.vectors.requests == []
        assert _snapshot(library.parent) == before
        assert not marker_path(library).exists()
        assert not (library.parent / "backups").exists()

    def test_aborted_run_leaves_an_unreadable_index_in_its_place(
        self, library: Path, regenerate_command: str
    ) -> None:
        """An index that cannot be loaded is moved aside only by a run that goes on."""
        (library.parent / "test.metadata").write_bytes(b"xy")
        before = _snapshot(library.parent)

        run = _regenerate_command(regenerate_command)(library, terminal=True, answers="n\n")

        assert run.result.exit_code == 0, run.result.output
        assert _snapshot(library.parent) == before
        assert not [item for item in library.parent.iterdir() if "unreadable" in item.name]

    def test_aborted_run_leaves_the_marker_of_an_earlier_run_alone(
        self, library: Path, resumable_command: str
    ) -> None:
        """Declining to continue an interrupted run neither deletes nor rewrites its marker."""
        stopped = _Tagger(error=RuntimeError("401 Unauthorized"), fail_from=2)
        first = _regenerate(library, "--yes", "--no-embeddings", ai=stopped)
        assert first.result.exit_code == 1, first.result.output
        assert marker_path(library).is_file()
        marker = marker_path(library).read_bytes()
        before = _snapshot(library.parent)

        if resumable_command == "sync regenerate":
            run = _regenerate(library, "--no-embeddings", terminal=True, answers="n\n")
        else:
            run = _force(library, terminal=True, answers="n\n")

        assert run.result.exit_code == 0, run.result.output
        assert marker_path(library).read_bytes() == marker
        assert _snapshot(library.parent) == before


class TestUnreachablePlexChangesNothing:
    """``sync regenerate`` checks the Plex connection before it replaces anything."""

    def test_a_server_that_cannot_be_connected_to(self, library: Path) -> None:
        from plexmix.services.sync_service import PlexConnectionError

        before = _snapshot(library.parent)

        run = _regenerate(
            library,
            "--yes",
            connect_error=PlexConnectionError("Cannot reach Plex server: connection refused."),
        )

        assert run.result.exit_code == 1
        assert "Cannot reach Plex server" in run.out
        assert "completed" not in run.out.lower()
        assert run.ai.requests == [] and run.vectors.requests == []
        assert _snapshot(library.parent) == before

    def test_a_connection_that_the_pre_flight_check_refuses(self, library: Path) -> None:
        before = _snapshot(library.parent)

        run = _regenerate(
            library, "--yes", plex=_Plex(_keys(LIBRARY_SIZE), token_error="Invalid token")
        )

        assert run.result.exit_code == 1
        assert "Nothing was changed." in run.out
        assert "completed" not in run.out.lower()
        assert run.ai.requests == [] and run.vectors.requests == []
        assert _snapshot(library.parent) == before


# ---------------------------------------------------------------------------
# d. Ctrl+C during "sync" still exits 130 and writes an "interrupted" row
# ---------------------------------------------------------------------------


class TestCtrlCDuringSync:
    """The engine's own check is ``test_sync_errors.py::TestSyncHistoryOnErrors``."""

    def test_ctrl_c_while_plex_is_read(self, library: Path) -> None:
        before = _query(library, "SELECT id, tags FROM tracks ORDER BY id")

        run = _sync(library.parent, _Plex(_keys(LIBRARY_SIZE), stop=KeyboardInterrupt()))

        assert run.result.exit_code == 130
        assert "Sync interrupted by user." in run.out
        assert "Progress has been saved to database." in run.out
        assert "Tip: Run 'plexmix sync' again to continue from where you left off." in run.out
        assert "completed" not in run.out.lower()
        assert "Traceback" not in run.result.output
        assert _history(library) == ["interrupted"]
        assert _query(library, "SELECT error_message FROM sync_history") == [
            ("User interrupted sync",)
        ]
        assert _query(library, "SELECT id, tags FROM tracks ORDER BY id") == before

    def test_ctrl_c_in_the_tagging_stage_keeps_the_finished_batches(
        self, untagged_library: Path
    ) -> None:
        ai = _Tagger(error=KeyboardInterrupt(), fail_from=3)

        run = _sync(untagged_library.parent, _Plex(_keys(LIBRARY_SIZE)), ai=ai)

        assert run.result.exit_code == 130
        assert "Sync interrupted by user." in run.out
        assert _history(untagged_library) == ["interrupted"]
        # The library was synced, and two batches of 20 were saved before Ctrl+C.
        assert len(_untagged(untagged_library)) == LIBRARY_SIZE - 40
        assert len(_tagged_with(untagged_library, "new-")) == 40

    def test_ctrl_c_in_the_embedding_stage_keeps_the_saved_batches_and_the_index(
        self, untagged_library: Path
    ) -> None:
        vectors = _Vectors(interrupt_on=2)

        run = _sync(untagged_library.parent, _Plex(_keys(LIBRARY_SIZE)), vectors=vectors)

        assert run.result.exit_code == 130
        assert "Sync interrupted by user." in run.out
        assert _history(untagged_library) == ["interrupted"]
        # One batch of 50 was saved, and the index holds exactly those vectors.
        assert vectors.requests == [50, 50]
        assert len(_embedded(untagged_library)) == 50
        assert _ids_in_saved_index(untagged_library.parent) == _embedded(untagged_library)


# ---------------------------------------------------------------------------
# e. An HTTP 401 from the AI provider still stops tagging after one batch
# ---------------------------------------------------------------------------

_AUTH_SENTENCE = (
    "The AI provider rejected your credentials (authentication failed). "
    "Re-check your API key in Settings."
)


class _AuthRefusingProvider(BaseHTTPRequestHandler):
    """A stand-in OpenAI-compatible endpoint that answers every request with HTTP 401."""

    requests: List[str] = []

    def do_POST(self) -> None:  # noqa: N802 (the name http.server calls)
        self.requests.append(self.path)
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.dumps(
            {
                "error": {
                    "message": f"Incorrect API key provided: {DUMMY_KEY}",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            }
        ).encode()
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the test output quiet."""


@contextmanager
def _refusing_endpoint() -> Iterator[Tuple[int, List[str]]]:
    """Run the stand-in on this machine only (127.0.0.1, a free port); yield port and requests."""
    handler = type("Handler", (_AuthRefusingProvider,), {"requests": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], handler.requests  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestHttp401StopsTagging:
    """``tests/test_sync_tagging_failfast.py`` checks the engine. These run the commands."""

    def test_plain_sync_stops_after_one_batch(
        self, untagged_library: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        ai = _Tagger(error=RuntimeError("Error code: 401 - Incorrect API key"))

        with caplog.at_level(logging.INFO):
            run = _sync(untagged_library.parent, _Plex(_keys(LIBRARY_SIZE)), ai=ai)

        # 120 tracks are 6 batches of 20. The first request ended the stage.
        assert run.result.exit_code == 0, run.result.output
        assert len(ai.requests) == 1 and len(ai.requests[0]) == 20
        assert len(_untagged(untagged_library)) == LIBRARY_SIZE
        # The library was still synced: the stage failed, the sync did not.
        assert _query(untagged_library, "SELECT COUNT(*) FROM tracks") == [(LIBRARY_SIZE,)]
        assert _history(untagged_library) == ["success"]
        assert DUMMY_KEY not in run.result.output
        # The reason is one plain sentence, said once by the engine. The user sees it in
        # the log line, because a plain sync prints no line of its own for this stage.
        stopped = [r.getMessage() for r in caplog.records if "AI tagging stopped" in r.getMessage()]
        assert stopped == [f"AI tagging stopped after 0 tracks: {_AUTH_SENTENCE}"]

    def test_a_real_http_401_gives_a_plain_reason_after_one_request(
        self, untagged_library: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole path: the OpenAI-compatible client, the classifier, the engine, the log."""
        from plexmix.ai.custom_provider import CustomProvider

        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(name.lower(), raising=False)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1")
        messages: List[str] = []

        with _refusing_endpoint() as (port, requests):
            provider = CustomProvider(
                base_url=f"http://127.0.0.1:{port}/v1", model="stand-in", api_key=DUMMY_KEY
            )
            from plexmix.plex.sync import SyncEngine

            with SQLiteManager(str(untagged_library)) as db:
                engine = SyncEngine(_Plex(_keys(LIBRARY_SIZE)), db, ai_provider=provider)
                engine.incremental_sync(
                    generate_embeddings=False, progress_callback=lambda _p, m: messages.append(m)
                )

        assert len(requests) == 1
        stopped = [m for m in messages if m.startswith("AI tagging stopped")]
        assert stopped == [f"AI tagging stopped: {_AUTH_SENTENCE}"]
        assert not any(DUMMY_KEY in message for message in messages)
        assert engine.tags_saved == 0
        assert len(_untagged(untagged_library)) == LIBRARY_SIZE

    def test_regenerate_stops_at_the_first_401_and_keeps_every_tag(self, library: Path) -> None:
        """A key that fails after the provider check: one request, no tag lost, run can be continued."""
        before_tags = _query(library, "SELECT id, tags FROM tracks ORDER BY id")
        ai = _Tagger(error=RuntimeError("401 Unauthorized"))

        run = _regenerate(library, "--yes", "--no-embeddings", ai=ai)

        assert run.result.exit_code == 1, run.result.output
        assert len(ai.requests) == 1
        assert _AUTH_SENTENCE in run.out
        assert "AI tags: 0 tracks regenerated" in run.out
        assert "completed" not in run.out.lower()
        assert DUMMY_KEY not in run.result.output
        assert _query(library, "SELECT id, tags FROM tracks ORDER BY id") == before_tags
        assert marker_path(library).is_file()
        assert "Run 'plexmix sync regenerate --no-embeddings' again to continue." in run.out


# ---------------------------------------------------------------------------
# Work is saved as it goes, and a rerun continues
# ---------------------------------------------------------------------------


class TestWorkIsSavedAsItGoesAndResumes:
    """Tags are saved per batch of 20 and embeddings per batch of 50 (audit, Positive Findings)."""

    def test_a_stopped_tagging_stage_is_continued_by_the_next_sync(
        self, untagged_library: Path
    ) -> None:
        stopped = _sync(
            untagged_library.parent,
            _Plex(_keys(LIBRARY_SIZE)),
            ai=_Tagger(error=KeyboardInterrupt(), fail_from=3),
        )
        assert stopped.result.exit_code == 130
        assert "Tip: Run 'plexmix sync' again to continue from where you left off." in stopped.out
        done = _tagged_with(untagged_library, "new-")
        assert done == list(range(1, 41))

        ai = _Tagger()
        again = _sync(untagged_library.parent, _Plex(_keys(LIBRARY_SIZE)), ai=ai)

        assert again.result.exit_code == 0, again.result.output
        assert "Incremental sync completed successfully!" in again.out
        # Only the tracks without tags were sent, in batches of 20.
        assert ai.sent == list(range(41, LIBRARY_SIZE + 1))
        assert [len(request) for request in ai.requests] == [20, 20, 20, 20]
        assert _untagged(untagged_library) == []
        assert _history(untagged_library) == ["interrupted", "success"]

    def test_a_stopped_embedding_stage_is_continued_by_the_next_sync(
        self, untagged_library: Path
    ) -> None:
        stopped = _sync(
            untagged_library.parent,
            _Plex(_keys(LIBRARY_SIZE)),
            vectors=_Vectors(interrupt_on=2),
        )
        assert stopped.result.exit_code == 130
        assert "Tip: Run 'plexmix sync' again to continue from where you left off." in stopped.out
        assert len(_embedded(untagged_library)) == 50

        vectors = _Vectors()
        again = _sync(untagged_library.parent, _Plex(_keys(LIBRARY_SIZE)), vectors=vectors)

        assert again.result.exit_code == 0, again.result.output
        assert "Incremental sync completed successfully!" in again.out
        # The 50 saved tracks were not sent again: 70 are left, in batches of 50.
        assert vectors.requests == [50, 20]
        assert _embedded(untagged_library) == list(range(1, LIBRARY_SIZE + 1))
        assert _ids_in_saved_index(untagged_library.parent) == list(range(1, LIBRARY_SIZE + 1))


class TestFirstRunPath:
    """A first sync into an empty profile, then ``plexmix create``, still works end to end."""

    def test_first_sync_then_create(self, tmp_path: Path) -> None:
        database_file = tmp_path / "test.db"
        assert not database_file.exists()
        ai, vectors = _Tagger(), _Vectors()

        run = _sync(tmp_path, _Plex(_keys(LIBRARY_SIZE)), ai=ai, vectors=vectors)

        assert run.result.exit_code == 0, run.result.output
        assert "Incremental sync completed successfully!" in run.out
        assert f"Tracks added: {LIBRARY_SIZE}" in run.out
        assert "finished with warnings" not in run.out
        assert _history(database_file) == ["success"]
        assert _untagged(database_file) == []
        assert _embedded(database_file) == list(range(1, LIBRARY_SIZE + 1))
        assert _ids_in_saved_index(tmp_path) == list(range(1, LIBRARY_SIZE + 1))
        # The fake providers did the work, in the usual batches.
        assert [len(request) for request in ai.requests] == [20] * 6
        assert vectors.requests == [50, 50, 20]

        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch(
                    "plexmix.cli.create_cmd.Settings.load_from_file",
                    return_value=_settings(tmp_path),
                )
            )
            stack.enter_context(
                patch(
                    "plexmix.cli.create_cmd._build_embedding_generator_with_reason",
                    return_value=(vectors, None),
                )
            )
            created = runner.invoke(
                app,
                ["create", "chill evening", "--limit", "5", "--no-create-in-plex"],
                input="\n",
            )

        assert created.exit_code == 0, created.output
        # The generator keeps the tracks of one artist apart, so it may find fewer than 5.
        saved = re.search(
            r"Playlist 'chill evening - (\d+) tracks' saved with (\d+) tracks!",
            _flat(created.stdout),
        )
        assert saved is not None and saved.group(1) == saved.group(2)
        found = int(saved.group(1))
        assert 1 <= found <= 5
        assert _query(database_file, "SELECT name FROM playlists") == [
            (f"chill evening - {found} tracks",)
        ]
        assert _query(database_file, "SELECT COUNT(*) FROM playlist_tracks") == [(found,)]


# ---------------------------------------------------------------------------
# What scripts can rely on
# ---------------------------------------------------------------------------


def _every_command_path(command: click.Command, path: Tuple[str, ...] = ()) -> Iterator[List[str]]:
    """The name of every command and group under ``command``, as arguments."""
    yield list(path)
    if isinstance(command, click.Group):
        for name, sub_command in sorted(command.commands.items()):
            yield from _every_command_path(sub_command, (*path, name))


class TestWhatScriptsCanRelyOn:
    @pytest.mark.parametrize(
        "arguments, said",
        [
            (["creat"], [r"No such command 'creat'\.", r"Did you mean 'create'\?"]),
            (
                ["create", "chill", "--limit", "abc"],
                [r"Invalid value for '--limit': 'abc' is not a valid integer\."],
            ),
            # The wording of the next two differs a little between click versions.
            (["sync", "regenerate", "--bogus"], [r"No such option:? '?--bogus'?"]),
            (["doctor", "--force", "--yes=maybe"], [r"Option '--yes' does not take a value\."]),
        ],
        ids=["unknown-command", "bad-number", "unknown-option", "bad-flag-value"],
    )
    def test_usage_errors_are_short_on_stderr_and_exit_2(
        self, arguments: List[str], said: List[str]
    ) -> None:
        result = _invoke(arguments)

        assert result.exit_code == 2
        assert result.stdout == ""
        error = _flat(click.unstyle(result.stderr))
        for sentence in said:
            assert re.search(sentence, error), sentence
        assert "Traceback" not in result.stderr
        assert len(result.stderr.splitlines()) <= 12

    def test_every_help_screen_exits_0_with_clean_streams(self) -> None:
        root = typer.main.get_command(app)
        paths = list(_every_command_path(root))
        assert len(paths) >= 30

        # Typer colours its help when one of these says so (GitHub Actions sets the first).
        forced = any(
            os.environ.get(name) for name in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS")
        )
        for path in paths:
            result = _invoke([*path, "--help"])
            name = " ".join(["plexmix", *path])
            assert result.exit_code == 0, name
            assert result.stderr == "", name
            if not forced:
                assert _ESCAPE not in result.stdout, name
            assert "Usage:" in click.unstyle(result.stdout), name

    def test_regenerate_commands_name_yes_in_their_help(self) -> None:
        for path in (["sync", "regenerate"], ["doctor"], ["embeddings", "generate"]):
            result = _invoke([*path, "--help"])
            assert "--yes" in click.unstyle(result.stdout), path

    def test_output_of_a_piped_run_has_no_colour_codes(self, library: Path) -> None:
        # A refusal, a question, a cancelled question and a failed run: all styled
        # lines, all printed without a terminal.
        refused = _regenerate(library, terminal=False)
        asked = _force(library, terminal=True, answers="n\n")
        failed = _regenerate(
            library, "--yes", "--no-embeddings", ai=_Tagger(error=RuntimeError("401 Unauthorized"))
        )
        reset = _db_reset(library.parent, answers="n\n")

        for result in (refused.result, asked.result, failed.result, reset):
            assert result.output.strip()
            assert _ESCAPE not in result.output
            assert _ESCAPE not in result.stderr

    def test_no_console_forces_colour_or_a_terminal(self) -> None:
        """A forced terminal or colour system would put codes into a pipe and ignore NO_COLOR."""
        import ast

        forbidden = {
            "force_terminal",
            "force_interactive",
            "color_system",
            "no_color",
            "force_jupyter",
        }
        folder = Path(__file__).resolve().parents[1] / "src" / "plexmix" / "cli"
        calls = 0
        for source in sorted(folder.glob("*.py")):
            for node in ast.walk(ast.parse(source.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if getattr(func, "id", getattr(func, "attr", None)) != "Console":
                    continue
                calls += 1
                used = {keyword.arg for keyword in node.keywords}
                assert not (used & forbidden), f"{source.name}:{node.lineno}"
        assert calls >= 10

    def test_playlist_export_writes_only_data_to_stdout_and_import_restores_it(
        self, library: Path
    ) -> None:
        with SQLiteManager(str(library)) as db:
            playlist_id = db.insert_playlist(Playlist(name="Road trip", created_by_ai=True))
            # Not in id order, to show that the order is the playlist's own.
            ordered = list(range(30, 5, -1))
            db.add_tracks_to_playlist(playlist_id, ordered)

        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch(
                    "plexmix.cli.playlist_cmd.Settings.load_from_file",
                    return_value=_settings(library.parent),
                )
            )
            exported = runner.invoke(app, ["playlist", "export", str(playlist_id)])
            assert exported.exit_code == 0, exported.output
            assert exported.stderr == ""
            document = json.loads(exported.stdout)
            assert [item["plex_key"] for item in document["tracks"]] == [
                f"t{number - 1}" for number in ordered
            ]
            file = library.parent / "road-trip.json"
            file.write_text(exported.stdout, encoding="utf-8")
            imported = runner.invoke(
                app, ["playlist", "import", str(file), "--name", "Road trip again"]
            )

        assert imported.exit_code == 0, imported.output
        assert f"Imported playlist 'Road trip again' with {len(ordered)} tracks" in _flat(
            imported.stdout
        )
        with SQLiteManager(str(library)) as db:
            [restored] = [p for p in db.get_playlists() if p.name == "Road trip again"]
            assert restored.id is not None and restored.id != playlist_id
            assert [t["id"] for t in db.get_playlist_tracks(restored.id)] == ordered

    @pytest.mark.parametrize(
        "arguments, patched",
        [(["db", "info"], "db_cmd"), (["playlist", "list"], "playlist_cmd")],
        ids=["db-info", "playlist-list"],
    )
    def test_reads_work_while_another_process_holds_the_write_lock(
        self, library: Path, arguments: List[str], patched: str
    ) -> None:
        with SQLiteManager(str(library)) as db:
            db.insert_playlist(Playlist(name="Road trip", created_by_ai=False))
        writer = sqlite3.connect(str(library), timeout=0)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE tracks SET play_count = 1 WHERE id = 1")
            with ExitStack() as stack:
                stack.enter_context(patch("plexmix.cli.main.setup_logging"))
                stack.enter_context(
                    patch(
                        f"plexmix.cli.{patched}.Settings.load_from_file",
                        return_value=_settings(library.parent),
                    )
                )
                result = runner.invoke(app, arguments)
        finally:
            writer.rollback()
            writer.close()

        assert result.exit_code == 0, result.output
        assert "locked" not in result.output.lower()
        assert "Traceback" not in result.output
        assert ("Road trip" if patched == "playlist_cmd" else f"Tracks: {LIBRARY_SIZE}") in _flat(
            result.stdout
        )


class TestRichDrawingDegradesOnALimitedConsole:
    """With a Windows or an ASCII console, tables and help fall back to plain characters."""

    @pytest.mark.parametrize("charset", ["ascii", "cp1252"])
    @pytest.mark.parametrize(
        "arguments",
        [
            ["--help"],
            ["sync", "regenerate", "--help"],
            ["doctor", "--help"],
            ["embeddings", "generate", "--help"],
            ["db", "reset", "--help"],
        ],
        ids=["main", "sync-regenerate", "doctor", "embeddings-generate", "db-reset"],
    )
    def test_help_falls_back(self, charset: str, arguments: List[str]) -> None:
        result = _invoke(arguments, _runner(charset))

        assert result.exit_code == 0, result.output
        assert result.stderr == ""
        assert "Usage:" in click.unstyle(result.stdout)
        if charset == "ascii":
            assert result.stdout.isascii()

    def test_table_falls_back(self, library: Path) -> None:
        with SQLiteManager(str(library)) as db:
            db.insert_playlist(Playlist(name="Road trip", created_by_ai=False))
        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch(
                    "plexmix.cli.playlist_cmd.Settings.load_from_file",
                    return_value=_settings(library.parent),
                )
            )
            result = _runner("ascii").invoke(app, ["playlist", "list"])

        assert result.exit_code == 0, result.output
        assert "Road trip" in result.stdout
        assert result.stdout.isascii()
        assert "+--" in result.stdout

    def test_the_regenerate_question_is_shown_on_a_windows_console(self, library: Path) -> None:
        """The question and its answer use only characters of code page 1252."""
        before = _snapshot(library.parent)

        run = _regenerate(library, terminal=True, answers="n\n", invoker=_runner("cp1252"))

        assert run.result.exit_code == 0, run.result.output
        assert "Regenerate the AI tags and embeddings of 120 tracks? [y/N]" in run.out
        assert "Operation cancelled." in run.out
        assert _snapshot(library.parent) == before


# ---------------------------------------------------------------------------
# Secrets stay out of normal output
# ---------------------------------------------------------------------------


class TestSecretsStayOutOfNormalOutput:
    def test_config_show_omits_the_token_and_the_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path))
        for name in ("PLEX_URL", "PLEX_TOKEN", "PLEX_LIBRARY_NAME", "UI_PASSWORD"):
            monkeypatch.delenv(name, raising=False)
        (tmp_path / "config.yaml").write_text(
            "plex:\n"
            "  url: http://plexhost:32400\n"
            "  library_name: Music\n"
            f"  token: {DUMMY_TOKEN}\n"
            "ai:\n"
            "  default_provider: custom\n"
            f"  custom_api_key: {DUMMY_KEY}\n"
            "embedding:\n"
            f"  custom_api_key: {DUMMY_KEY}\n"
            "ui:\n"
            f"  password: {DUMMY_PASSWORD}\n"
        )

        result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0, result.output
        assert "PlexMix Configuration" in result.stdout
        for secret in (DUMMY_TOKEN, DUMMY_KEY, DUMMY_PASSWORD):
            assert secret not in result.stdout
            assert secret not in result.stderr
        assert "http://plexhost:32400" in _flat(result.stdout)

    def test_every_prompt_for_a_secret_hides_the_input(self) -> None:
        import ast

        folder = Path(__file__).resolve().parents[1] / "src" / "plexmix" / "cli"
        asked = 0
        for source in sorted(folder.glob("*.py")):
            for node in ast.walk(ast.parse(source.read_text())):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "prompt"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    continue
                if not re.search(r"token|api key|password", node.args[0].value, re.IGNORECASE):
                    continue
                asked += 1
                hidden = {
                    keyword.arg
                    for keyword in node.keywords
                    if isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                }
                assert "hide_input" in hidden, f"{source.name}:{node.lineno}"
        assert asked >= 5

    def test_the_config_file_is_written_owner_only_by_atomic_replace(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        settings.plex.token = DUMMY_TOKEN
        target = tmp_path / "config.yaml"
        settings.save_to_file(str(target))

        assert target.stat().st_mode & 0o777 == 0o600
        assert [item.name for item in tmp_path.iterdir()] == ["config.yaml"]
        saved = target.read_bytes()

        # A save that fails half-way leaves the old file, whole, and no temporary file.
        with patch("yaml.dump", side_effect=OSError("no space left on device")):
            with pytest.raises(OSError):
                settings.save_to_file(str(target))

        assert target.read_bytes() == saved
        assert [item.name for item in tmp_path.iterdir()] == ["config.yaml"]

    def test_a_provider_error_that_repeats_the_key_is_not_printed(self, library: Path) -> None:
        error = RuntimeError(f"Error code: 401 - Incorrect API key provided: {DUMMY_KEY}")

        run = _regenerate(library, "--yes", "--no-embeddings", ai=_Tagger(error=error))

        assert run.result.exit_code == 1
        assert DUMMY_KEY not in _squeezed(run.result.output)
        assert DUMMY_KEY not in _squeezed(" ".join(str(row) for row in _history_rows(library)))


def _history_rows(database_file: Path) -> List[Tuple[Any, ...]]:
    return _query(database_file, "SELECT status, error_message FROM sync_history")
