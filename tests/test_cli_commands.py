"""Integration tests for CLI commands using typer.testing.CliRunner.

Each test class focuses on one command group and heavily mocks external
dependencies (Plex, AI providers, credentials, database) so tests run
quickly without network or file-system side-effects.

The sync tests come in two sets. ``TestSyncIncremental`` and ``TestSyncRemovals``
patch the engine and check what the commands print and how they exit.
``TestSyncRemovalsOnRealEngine`` runs the same situations on the real engine with
a temporary database and a fake Plex, and checks what is left in the library.
"""

import ast
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, List, Optional, Tuple
from unittest.mock import patch, MagicMock

import pytest
import typer
from click.testing import Result
from typer.testing import CliRunner

import plexmix.cli
from plexmix.cli import sync_cmd
from plexmix.cli.console_helpers import format_size
from plexmix.cli.main import app
from plexmix.config.settings import (
    Settings,
    PlexSettings,
    DatabaseSettings,
    AISettings,
    EmbeddingSettings,
    PlaylistSettings,
    AudioSettings,
)
from plexmix.database.models import Album, Artist, Track, SyncHistory
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex.client import PlexFetchError, PlexReadInterrupted, PlexReadReport
from plexmix.plex.sync import (
    REASON_BACKUP_FAILED,
    REASON_EMPTY_LISTING,
    REASON_LISTING_UNKNOWN,
    REASON_THRESHOLD,
    REASON_TOO_MANY_UNREAD,
    REMOVAL_BACKUP_FAILED,
    REMOVAL_CANCELLED,
    REMOVAL_FAILED,
    REMOVAL_LISTED_AGAIN,
    REMOVAL_LISTING_FAILED,
    REMOVAL_MORE_MISSING,
    REMOVAL_REMOVED,
    RemovalResult,
    SyncOutcome,
)

runner = CliRunner()

# The only "secret" in this file is one of the project's dummy values.
DUMMY_PASSWORD = "dummy-ui-password-0000"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    """Build a Settings object directly (no load_from_file) to avoid conflicts
    with tests that patch Settings.load_from_file."""
    return Settings(
        plex=PlexSettings(url="http://localhost:32400", library_name="Music"),
        database=DatabaseSettings(
            path=str(tmp_path / "test.db"),
            faiss_index_path=str(tmp_path / "test.index"),
        ),
        ai=AISettings(),
        embedding=EmbeddingSettings(),
        playlist=PlaylistSettings(),
        audio=AudioSettings(),
    )


def _dummy_track(track_id: int = 1, title: str = "Song", tags: str = "") -> Track:
    return Track(
        id=track_id,
        plex_key=f"key-{track_id}",
        title=title,
        artist_id=1,
        album_id=1,
        genre="Rock",
        year=2023,
        tags=tags or None,
    )


# ---------------------------------------------------------------------------
# Helpers for the sync commands
# ---------------------------------------------------------------------------

SUCCESS_LINE = "completed successfully"

# Every command that runs a library sync, as it is typed after ``plexmix``.
SYNC_COMMANDS = [
    pytest.param(["sync"], id="sync"),
    pytest.param(["sync", "incremental"], id="incremental"),
    pytest.param(["sync", "full"], id="full"),
    pytest.param(["sync", "regenerate"], id="regenerate"),
]


def _flat(text: str) -> str:
    """The output as one line: the console wraps a long sentence at the terminal width."""
    return " ".join(text.split())


def _squeezed(text: str) -> str:
    """The output without any white space, to look for a value that a wrap could split."""
    return "".join(text.split())


@pytest.fixture
def patched_sync(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """The sync commands with Plex, the providers, the database and the engine patched.

    ``engine`` stands in for the ``SyncEngine`` object. Its sync reports 5 added and
    2 updated tracks, and its outcome is a clean run until a test sets another one.
    ``engine_cls`` records how the command built the engine.
    """
    engine = MagicMock()
    history = SyncHistory(tracks_added=5, tracks_updated=2, tracks_removed=0)
    engine.incremental_sync.return_value = history
    engine.regenerate_sync.return_value = history
    engine.outcome = SyncOutcome()
    mock_db = MagicMock()
    mock_db.__enter__ = MagicMock(return_value=mock_db)
    mock_db.__exit__ = MagicMock(return_value=False)
    settings = _make_settings(tmp_path)
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch("plexmix.cli.sync_cmd.Settings.load_from_file", return_value=settings)
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.connect_plex", return_value=MagicMock()))
        # An AI provider is there, so no "Continuing without AI assistance" line is printed.
        stack.enter_context(
            patch("plexmix.cli.sync_cmd._build_ai_provider", return_value=MagicMock())
        )
        stack.enter_context(
            patch("plexmix.cli.sync_cmd._build_embedding_generator", return_value=None)
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.SQLiteManager", return_value=mock_db))
        engine_cls = stack.enter_context(
            patch("plexmix.cli.sync_cmd.SyncEngine", return_value=engine)
        )
        yield SimpleNamespace(engine=engine, engine_cls=engine_cls)


def _invoke_sync(
    command: List[str], *flags: str, terminal: bool = False, answers: str = ""
) -> Result:
    """Run a sync command without embeddings, with or without a terminal to ask in.

    ``answers`` is what the user types at the removal question. ``sync regenerate``
    asks its own question first, and that one is answered with Yes.
    """
    typed = ("y\n" if command[-1] == "regenerate" else "") + answers
    with patch("plexmix.cli.sync_cmd.can_prompt", return_value=terminal):
        return runner.invoke(app, [*command, "--no-embeddings", *flags], input=typed)


def _sync_call(engine: MagicMock, command: List[str]) -> MagicMock:
    """The engine method that ``command`` runs."""
    return engine.regenerate_sync if command[-1] == "regenerate" else engine.incremental_sync


def _held_outcome(held: int = 5443, total: int = 5943) -> SyncOutcome:
    """What the engine publishes after it held a removal above the threshold."""
    return SyncOutcome(
        listed={"artists": 1, "albums": 1, "tracks": total - held},
        library_total=total,
        held=held,
        held_keys=frozenset(f"gone{number}" for number in range(held)),
        reason=REASON_THRESHOLD,
        message=(
            f"Plex no longer lists {held:,} of {total:,} tracks. They were kept, because "
            "a removal this large needs confirmation."
        ),
    )


def _unread_outcome(kind: str, count: int) -> SyncOutcome:
    """What the engine publishes after a few items of one kind could not be read."""
    outcome = SyncOutcome(listed={"artists": 30, "albums": 400, "tracks": 5943}, library_total=5943)
    outcome.unread[kind] = count
    return outcome


def _sync_command_calls(source: str) -> List[Tuple[int, str]]:
    """Where ``source`` calls a sync command function: (line, name) for each call."""
    commands = {"sync_incremental", "sync_full", "sync_regenerate"}
    found: List[Tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in commands:
            found.append((node.lineno, str(name)))
    return found


# ---------------------------------------------------------------------------
# sync commands
# ---------------------------------------------------------------------------


class TestSyncIncremental:
    """Tests for `plexmix sync incremental`."""

    @patch("plexmix.cli.sync_cmd.SyncEngine")
    @patch("plexmix.cli.sync_cmd.SQLiteManager")
    @patch("plexmix.cli.sync_cmd.connect_plex")
    @patch("plexmix.cli.sync_cmd.Settings.load_from_file")
    @patch("plexmix.cli.sync_cmd._build_embedding_generator", return_value=None)
    @patch("plexmix.cli.sync_cmd._build_ai_provider", return_value=None)
    def test_successful_sync(
        self,
        mock_build_ai,
        mock_build_emb,
        mock_load,
        mock_connect_plex,
        mock_db_cls,
        mock_engine_cls,
        tmp_path,
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_plex = MagicMock()
        mock_connect_plex.return_value = mock_plex

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        sync_result = SyncHistory(tracks_added=5, tracks_updated=2, tracks_removed=1)
        mock_engine = MagicMock()
        mock_engine.incremental_sync.return_value = sync_result
        # Every listed item was read and nothing was held: the run is a clean one.
        mock_engine.outcome = SyncOutcome(removed=1)
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(app, ["sync", "incremental", "--no-embeddings"])
        assert result.exit_code == 0, f"output={result.output!r}"
        assert "sync completed successfully" in result.output.lower()
        assert "5" in result.output
        assert "2" in result.output
        assert "1" in result.output

    @patch("plexmix.cli.sync_cmd.connect_plex")
    @patch("plexmix.cli.sync_cmd.Settings.load_from_file")
    def test_missing_plex_token_exits(self, mock_load, mock_connect_plex, tmp_path):
        from plexmix.services.sync_service import PlexConnectionError

        settings = _make_settings(tmp_path)
        mock_load.return_value = settings
        mock_connect_plex.side_effect = PlexConnectionError(
            "Plex not configured. Run 'plexmix config init' first."
        )

        result = runner.invoke(app, ["sync", "incremental"])
        assert result.exit_code == 1
        assert "not configured" in result.output.lower()

    @patch("plexmix.cli.sync_cmd.connect_plex")
    @patch("plexmix.cli.sync_cmd.Settings.load_from_file")
    def test_plex_connect_failure_exits(self, mock_load, mock_connect_plex, tmp_path):
        from plexmix.services.sync_service import PlexConnectionError

        settings = _make_settings(tmp_path)
        mock_load.return_value = settings
        mock_connect_plex.side_effect = PlexConnectionError("Failed to connect to Plex server.")

        result = runner.invoke(app, ["sync", "incremental"])
        assert result.exit_code == 1
        assert "failed to connect" in result.output.lower()

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_stopped_sync_prints_the_sentence_and_no_completed_line(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        _sync_call(patched_sync.engine, command).side_effect = PlexReadInterrupted(
            "tracks", read=522, listed=5943
        )

        result = _invoke_sync(command)

        assert result.exit_code == 1
        assert (
            "Plex stopped answering after 522 of 5,943 tracks were read, "
            "so nothing was changed." in _flat(result.stdout)
        )
        assert "completed" not in result.output.lower()
        # The command ended by itself: there is no error for Typer to report.
        assert isinstance(result.exception, SystemExit)
        assert "Traceback" not in result.output

    def test_listing_failure_prints_one_sentence_and_no_traceback(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.incremental_sync.side_effect = PlexReadInterrupted("tracks")

        result = _invoke_sync(["sync"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        # The start line and that one sentence, and nothing else on either stream.
        assert [line for line in result.stdout.splitlines() if line.strip()] == [
            "Starting incremental library sync...",
            "Plex could not list the library's tracks, so nothing was changed.",
        ]
        assert result.stderr == ""

    def test_other_fetch_error_ends_with_its_sentence(self, patched_sync: SimpleNamespace) -> None:
        patched_sync.engine.incremental_sync.side_effect = PlexFetchError(
            "No music library selected"
        )

        result = _invoke_sync(["sync"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "No music library selected." in result.stdout
        assert "completed" not in result.output.lower()

    def test_fetch_error_text_never_shows_url_credentials(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.incremental_sync.side_effect = PlexFetchError(
            f"Could not read http://leakuser:{DUMMY_PASSWORD}@plexhost:32400/library"
        )

        result = _invoke_sync(["sync"])

        assert result.exit_code == 1
        shown = _squeezed(result.output)
        assert "http://***@plexhost:32400/library" in shown
        assert DUMMY_PASSWORD not in shown
        assert "leakuser" not in shown

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_failed_connection_check_prints_one_sentence_and_no_traceback(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        """Plex went away after the command connected and before the engine started."""
        _sync_call(patched_sync.engine, command).side_effect = ConnectionError(
            f"Cannot reach Plex server: http://leakuser:{DUMMY_PASSWORD}@plexhost:32400 "
            "closed the connection"
        )

        result = _invoke_sync(command)

        assert result.exit_code == 1
        # The command ended by itself: there is no error for Typer to report.
        assert isinstance(result.exception, SystemExit)
        out = _flat(result.stdout)
        assert "Cannot reach Plex server: http://***@plexhost:32400 closed the connection." in out
        assert DUMMY_PASSWORD not in _squeezed(result.output)
        assert "Traceback" not in result.output
        assert "completed" not in result.output.lower()
        # The mock engine cleared nothing, so nothing is said about tags.
        assert "cleared" not in out

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_interrupted_sync_still_exits_130(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        _sync_call(patched_sync.engine, command).side_effect = KeyboardInterrupt()

        result = _invoke_sync(command)

        assert result.exit_code == 130
        assert "Sync interrupted by user." in result.stdout
        assert "completed" not in result.output.lower()

    @pytest.mark.parametrize(
        "error, reason",
        [
            (BrokenPipeError(32, "Broken pipe"), "[Errno 32] Broken pipe"),
            (ConnectionAbortedError(), "ConnectionAbortedError"),
            (
                ConnectionResetError(f"http://leakuser:{DUMMY_PASSWORD}@127.0.0.1:9/v1 reset"),
                "http://***@127.0.0.1:9/v1 reset",
            ),
        ],
        ids=["broken-pipe", "no-text", "address"],
    )
    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_connection_error_of_a_later_stage_is_not_reported_as_plex(
        self, patched_sync: SimpleNamespace, command: List[str], error: ConnectionError, reason: str
    ) -> None:
        """A provider's broken pipe, after the library was synced, is not "Plex could not be read"."""
        patched_sync.engine.outcome = SyncOutcome(
            library_total=400, removed=5, added=3, updated=0, compared=True
        )
        _sync_call(patched_sync.engine, command).side_effect = error

        result = _invoke_sync(command)

        assert result.exit_code == 1
        # The command says it itself: a broken pipe that leaves a command ends it silently.
        assert isinstance(result.exception, SystemExit)
        out = _flat(result.stdout)
        assert f"The sync stopped after the library was compared with Plex: {reason}." in out
        assert "Plex could not be read" not in out
        assert "nothing was changed" not in out.lower()
        assert "completed" not in out.lower()
        assert DUMMY_PASSWORD not in _squeezed(result.output)
        # What the library sync had already saved is said before the command ends.
        assert "Saved before the sync stopped:" in out
        assert "Tracks added: 3 Tracks updated: 0 Tracks removed: 5" in out

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_failure_after_an_allowed_removal_names_the_backup(
        self, patched_sync: SimpleNamespace, command: List[str], tmp_path: Path
    ) -> None:
        """Spec "Large removals are backed up first": the user is told where the copy is."""
        backup_folder = tmp_path / "backups" / "20261118_101500"
        patched_sync.engine.outcome = SyncOutcome(
            library_total=400,
            removed=300,
            added=1,
            compared=True,
            backup_folder=backup_folder,
            backup_size_bytes=13_002_342,
        )
        error = RuntimeError("worker died")
        _sync_call(patched_sync.engine, command).side_effect = error

        result = _invoke_sync(command, "--allow-removals")

        assert result.exception is error
        out = _flat(result.stdout)
        assert "Tracks added: 1 Tracks updated: 0 Tracks removed: 300" in out
        # The folder is printed in one piece (``soft_wrap``), with its size.
        assert f"{backup_folder} (12.4 MB)" in result.stdout
        assert "completed" not in out.lower()

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_failure_before_anything_was_saved_adds_no_summary(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        error = RuntimeError("could not start")
        _sync_call(patched_sync.engine, command).side_effect = error

        result = _invoke_sync(command)

        assert result.exception is error
        assert "Saved before the sync stopped" not in result.stdout
        assert "Backup made" not in result.stdout

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_interrupted_sync_says_what_was_already_removed(
        self, patched_sync: SimpleNamespace, command: List[str], tmp_path: Path
    ) -> None:
        """Ctrl+C after the removal step: the tracks are gone, and the output says so."""
        backup_folder = tmp_path / "backups" / "20261118_101500"
        patched_sync.engine.outcome = SyncOutcome(
            library_total=400,
            removed=300,
            compared=True,
            backup_folder=backup_folder,
            backup_size_bytes=2048,
        )
        _sync_call(patched_sync.engine, command).side_effect = KeyboardInterrupt()

        result = _invoke_sync(command, "--allow-removals")

        assert result.exit_code == 130
        out = _flat(result.stdout)
        assert "Sync interrupted by user." in out
        assert "Saved before the sync stopped:" in out
        assert "Tracks removed: 300" in out
        assert str(backup_folder) in _squeezed(result.stdout)

    def test_interrupt_after_a_backup_without_a_removal_names_the_backup(
        self, patched_sync: SimpleNamespace, tmp_path: Path
    ) -> None:
        backup_folder = tmp_path / "backups" / "20261118_101500"
        patched_sync.engine.outcome = SyncOutcome(
            library_total=400, compared=True, backup_folder=backup_folder, backup_size_bytes=2048
        )
        patched_sync.engine.incremental_sync.side_effect = KeyboardInterrupt()

        result = _invoke_sync(["sync"], "--allow-removals")

        assert result.exit_code == 130
        # Nothing was added, updated or removed, so no counts are printed.
        assert "Saved before the sync stopped" not in result.stdout
        assert "No track was removed." in result.stdout
        assert str(backup_folder) in _squeezed(result.stdout)


class TestSyncRemovals:
    """What a sync prints and how it exits, row by row of design D11 (engine patched)."""

    def test_too_many_unread_reads_print_the_sentence_and_exit_1(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.outcome = SyncOutcome(
            unread={"artists": 0, "albums": 0, "tracks": 200},
            listed={"artists": 30, "albums": 400, "tracks": 5943},
            library_total=5943,
            reason=REASON_TOO_MANY_UNREAD,
            message="200 of 5,943 tracks could not be read and were kept. Nothing was removed.",
        )

        # The flag changes nothing here: the engine decides, and it removed nothing.
        result = _invoke_sync(["sync"], "--allow-removals")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        # What was read is still added and updated, and the counts are shown.
        assert "Tracks added: 5" in out
        assert "Tracks updated: 2" in out
        assert "200 of 5,943 tracks could not be read and were kept. Nothing was removed." in out
        assert out.count("could not be read") == 1  # the counts are said once
        assert "completed" not in out.lower()
        patched_sync.engine.remove_unlisted_tracks.assert_not_called()

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_held_removal_without_a_terminal_exits_1_and_names_the_flag(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        patched_sync.engine.outcome = _held_outcome()

        result = _invoke_sync(command, terminal=False)

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "Plex no longer lists 5,443 of the 5,943 tracks" in out
        assert "5,443 tracks were kept." in out
        assert "--allow-removals" in out
        assert "completed" not in out.lower()
        # Nobody was asked, and nothing was removed.
        assert "Remove 5,443" not in out
        patched_sync.engine.remove_unlisted_tracks.assert_not_called()

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_allow_removals_reaches_the_engine_as_true(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        result = _invoke_sync(command, "--allow-removals")

        assert result.exit_code == 0, result.output
        sync_call = _sync_call(patched_sync.engine, command)
        sync_call.assert_called_once()
        assert sync_call.call_args.kwargs["allow_large_removals"] is True

    @pytest.mark.parametrize("subcommand", ["incremental", "full", "regenerate"])
    def test_allow_removals_before_the_subcommand_name_counts_too(
        self, patched_sync: SimpleNamespace, subcommand: str
    ) -> None:
        """``plexmix sync --allow-removals full`` gives the flag to the group."""
        typed = "y\n" if subcommand == "regenerate" else ""
        with patch("plexmix.cli.sync_cmd.can_prompt", return_value=False):
            result = runner.invoke(
                app, ["sync", "--allow-removals", subcommand, "--no-embeddings"], input=typed
            )

        assert result.exit_code == 0, result.output
        sync_call = _sync_call(patched_sync.engine, ["sync", subcommand])
        sync_call.assert_called_once()
        assert sync_call.call_args.kwargs["allow_large_removals"] is True

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_without_the_flag_the_engine_gets_false(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        result = _invoke_sync(command)

        assert result.exit_code == 0, result.output
        sync_call = _sync_call(patched_sync.engine, command)
        sync_call.assert_called_once()
        assert sync_call.call_args.kwargs["allow_large_removals"] is False

    @pytest.mark.parametrize("typed", ["\n", "n\n"], ids=["enter", "n"])
    def test_held_removal_in_a_terminal_asks_and_no_keeps_the_tracks(
        self, patched_sync: SimpleNamespace, typed: str
    ) -> None:
        patched_sync.engine.outcome = _held_outcome()

        result = _invoke_sync(["sync"], terminal=True, answers=typed)

        out = _flat(result.stdout)
        assert result.exit_code == 0
        # The question names the count and the library size, and No is the default.
        question = "Remove 5,443 of 5,943 tracks? [y/N]"
        assert question in out
        # Before it: what is lost with the tracks, and that a backup is made first.
        before_question = out.split(question)[0]
        for words in ("tags", "embeddings", "playlist entries", "A backup", "is made first"):
            assert words in before_question
        assert "5,443 tracks were kept." in out.split(question)[1]
        assert "--allow-removals" not in out
        assert "completed" not in out.lower()
        patched_sync.engine.remove_unlisted_tracks.assert_not_called()

    def test_yes_removes_the_held_count_and_names_the_backup(
        self, patched_sync: SimpleNamespace, tmp_path: Path
    ) -> None:
        backup_folder = tmp_path / "backups" / "20261118_101500"
        patched_sync.engine.outcome = _held_outcome()
        patched_sync.engine.remove_unlisted_tracks.return_value = RemovalResult(
            status=REMOVAL_REMOVED,
            removed=5443,
            missing=5443,
            library_total=5943,
            message="Removed 5,443 tracks that Plex no longer lists.",
            backup_folder=backup_folder,
            backup_size_bytes=13_002_342,
        )

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        assert result.exit_code == 0, result.output
        # The held count, and the keys of the held tracks: no other track may go.
        held_keys = patched_sync.engine.outcome.held_keys
        assert len(held_keys) == 5443
        patched_sync.engine.remove_unlisted_tracks.assert_called_once_with(
            5443, held_keys=held_keys
        )
        # The folder is printed in one piece, with its size.
        assert f"{backup_folder} (12.4 MB)" in result.stdout
        assert "Removed 5,443 tracks that Plex no longer lists." in _flat(result.stdout)

    def test_yes_when_plex_lists_the_tracks_again_exits_0(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.outcome = _held_outcome()
        patched_sync.engine.remove_unlisted_tracks.return_value = RemovalResult(
            status=REMOVAL_LISTED_AGAIN,
            library_total=5943,
            message="Plex lists those tracks again. Nothing was removed.",
        )

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        patched_sync.engine.remove_unlisted_tracks.assert_called_once_with(
            5443, held_keys=patched_sync.engine.outcome.held_keys
        )
        assert "Plex lists those tracks again. Nothing was removed." in out
        assert "completed" not in out.lower()

    @pytest.mark.parametrize(
        "removal, expected",
        [
            (
                RemovalResult(
                    status=REMOVAL_MORE_MISSING,
                    missing=900,
                    library_total=5943,
                    message=(
                        "Plex no longer lists 900 tracks, more than the 300 that were "
                        "confirmed. Nothing was removed."
                    ),
                ),
                "Plex no longer lists 900 tracks, more than the 300 that were confirmed.",
            ),
            (
                RemovalResult(
                    status=REMOVAL_LISTING_FAILED,
                    message="Plex could not list the library's tracks. Nothing was removed.",
                ),
                "Plex could not list the library's tracks.",
            ),
            (
                RemovalResult(
                    status=REMOVAL_BACKUP_FAILED,
                    missing=300,
                    library_total=5943,
                    message=(
                        "The backup could not be made: [Errno 28] No space left on device. "
                        "Nothing was removed."
                    ),
                ),
                "The backup could not be made: [Errno 28] No space left on device.",
            ),
        ],
        ids=["more-missing", "listing-failed", "backup-failed"],
    )
    def test_yes_that_cannot_be_applied_exits_1(
        self, patched_sync: SimpleNamespace, removal: RemovalResult, expected: str
    ) -> None:
        patched_sync.engine.outcome = _held_outcome(held=300)
        patched_sync.engine.remove_unlisted_tracks.return_value = removal

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        patched_sync.engine.remove_unlisted_tracks.assert_called_once_with(
            300, held_keys=patched_sync.engine.outcome.held_keys
        )
        assert expected in out
        assert "Nothing was removed." in out
        assert "completed" not in out.lower()

    def test_interrupted_confirmed_removal_exits_130(self, patched_sync: SimpleNamespace) -> None:
        patched_sync.engine.outcome = _held_outcome()
        patched_sync.engine.remove_unlisted_tracks.side_effect = KeyboardInterrupt()

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        assert result.exit_code == 130
        assert "Interrupted" in result.stdout
        assert "completed" not in result.output.lower()

    @pytest.mark.parametrize(
        "state, expected, names_backup",
        [
            (None, "Nothing was removed.", False),
            (
                RemovalResult(
                    status=REMOVAL_CANCELLED,
                    backup_folder=Path("/library/backups/20261118_101500"),
                    backup_size_bytes=2048,
                ),
                "Nothing was removed.",
                True,
            ),
            (
                RemovalResult(
                    status=REMOVAL_REMOVED,
                    removed=5443,
                    backup_folder=Path("/library/backups/20261118_101500"),
                    backup_size_bytes=2048,
                ),
                "5,443 tracks had already been removed from the PlexMix library.",
                True,
            ),
        ],
        ids=["before-the-backup", "after-the-backup", "after-the-deletes"],
    )
    def test_interrupted_confirmed_removal_says_how_far_it_got(
        self,
        patched_sync: SimpleNamespace,
        state: Optional[RemovalResult],
        expected: str,
        names_backup: bool,
    ) -> None:
        """Ctrl+C gives no result, so the command reads ``last_removal`` of the engine."""
        patched_sync.engine.outcome = _held_outcome()
        patched_sync.engine.last_removal = state
        patched_sync.engine.remove_unlisted_tracks.side_effect = KeyboardInterrupt()

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        assert result.exit_code == 130
        out = _flat(result.stdout)
        assert "Interrupted while the removal was being applied." in out
        assert expected in out
        assert ("/library/backups/20261118_101500 (2.0 KB)" in out) is names_backup
        assert ("Nothing was removed." in out) is (state is None or state.removed == 0)

    def test_removal_that_failed_after_its_backup_names_the_backup(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.outcome = _held_outcome(held=300)
        patched_sync.engine.remove_unlisted_tracks.return_value = RemovalResult(
            status=REMOVAL_FAILED,
            missing=300,
            library_total=5943,
            message="The tracks could not be removed: database is locked. Nothing was removed.",
            backup_folder=Path("/library/backups/20261118_101500"),
            backup_size_bytes=2048,
        )

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "The tracks could not be removed: database is locked. Nothing was removed." in out
        assert "Backup made before the removal: /library/backups/20261118_101500 (2.0 KB)" in out
        assert "completed" not in out.lower()

    def test_held_outcome_without_keys_is_confirmed_by_the_count_alone(
        self, patched_sync: SimpleNamespace
    ) -> None:
        """An empty set of keys would match no track. The engine refuses it, so None is passed."""
        outcome = _held_outcome(held=300)
        outcome.held_keys = frozenset()
        patched_sync.engine.outcome = outcome
        patched_sync.engine.remove_unlisted_tracks.return_value = RemovalResult(
            status=REMOVAL_REMOVED, removed=300, message="Removed 300 tracks."
        )

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        assert result.exit_code == 0, result.output
        patched_sync.engine.remove_unlisted_tracks.assert_called_once_with(300, held_keys=None)

    def test_failed_backup_says_nothing_was_removed_and_exits_1(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.outcome = SyncOutcome(
            listed={"artists": 1, "albums": 1, "tracks": 500},
            library_total=5943,
            reason=REASON_BACKUP_FAILED,
            message=(
                "The backup could not be made: [Errno 28] No space left on device. "
                "Nothing was removed."
            ),
        )

        result = _invoke_sync(["sync"], "--allow-removals")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "The backup could not be made: [Errno 28] No space left on device." in out
        assert "Nothing was removed." in out
        assert "completed" not in out.lower()
        patched_sync.engine.remove_unlisted_tracks.assert_not_called()

    def test_allowed_removal_names_the_backup_and_the_removed_count(
        self, patched_sync: SimpleNamespace, tmp_path: Path
    ) -> None:
        # Square brackets and colons in a folder name are shown as they are: they are
        # not read as console markup or as an emoji code.
        backup_folder = tmp_path / "[bold]library :100:" / "backups" / "20261118_101500"
        patched_sync.engine.incremental_sync.return_value = SyncHistory(tracks_removed=5443)
        patched_sync.engine.outcome = SyncOutcome(
            listed={"artists": 1, "albums": 1, "tracks": 500},
            library_total=5943,
            removed=5443,
            backup_folder=backup_folder,
            backup_size_bytes=13_002_342,
        )

        result = _invoke_sync(["sync"], "--allow-removals")

        assert result.exit_code == 0, result.output
        assert SUCCESS_LINE in result.stdout
        assert "Tracks removed: 5443" in result.stdout
        assert f"{backup_folder} (12.4 MB)" in result.stdout

    @pytest.mark.parametrize("flags", [(), ("--allow-removals",)], ids=["plain", "allow-removals"])
    def test_empty_listing_exits_1(
        self, patched_sync: SimpleNamespace, flags: Tuple[str, ...]
    ) -> None:
        patched_sync.engine.outcome = SyncOutcome(
            library_total=5943,
            reason=REASON_EMPTY_LISTING,
            message=(
                "Plex listed 0 tracks, but the PlexMix library holds 5,943. Nothing was "
                "removed. If the Plex library really is empty, run 'plexmix db reset' to "
                "clear the PlexMix library."
            ),
        )

        # A terminal and a ready "y": the removal is still not offered.
        result = _invoke_sync(["sync"], *flags, terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Plex listed 0 tracks" in out
        assert "Nothing was removed." in out
        assert "plexmix db reset" in out
        assert "completed" not in out.lower()
        assert "[y/N]" not in out
        patched_sync.engine.remove_unlisted_tracks.assert_not_called()

    def test_unknown_listing_exits_1(self, patched_sync: SimpleNamespace) -> None:
        patched_sync.engine.outcome = SyncOutcome(
            library_total=5943,
            reason=REASON_LISTING_UNKNOWN,
            message="Plex's track listing was not available. Nothing was removed.",
        )

        result = _invoke_sync(["sync"], terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Plex's track listing was not available. Nothing was removed." in out
        assert "completed" not in out.lower()
        patched_sync.engine.remove_unlisted_tracks.assert_not_called()

    def test_a_few_unread_tracks_exit_0_with_the_count_line(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.outcome = _unread_outcome("tracks", 3)

        result = _invoke_sync(["sync"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "Tracks added: 5" in out
        assert "3 tracks could not be read and were kept." in out
        # Something was kept back, so this is not the green success line.
        assert "completed" not in out.lower()

    def test_an_unread_artist_is_reported_and_is_not_a_clean_run(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.outcome = _unread_outcome("artists", 1)

        result = _invoke_sync(["sync"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert (
            "1 artist could not be read. Tracks keep the artist PlexMix already has for "
            "them. A new track whose artist is also new to PlexMix is filed under Unknown "
            "Artist until a later sync reads that artist." in out
        )
        assert "completed" not in out.lower()

    def test_run_incremental_sync_returns_true_only_after_the_success_line(
        self, patched_sync: SimpleNamespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        engine = patched_sync.engine

        with patch("plexmix.cli.sync_cmd.can_prompt", return_value=False):
            # A clean run: the green line, and True.
            assert sync_cmd.run_incremental_sync(embeddings=False) is True
            assert SUCCESS_LINE in capsys.readouterr().out

            # Unread tracks were kept: the run ended well, without the green line.
            engine.outcome = _unread_outcome("tracks", 3)
            assert sync_cmd.run_incremental_sync(embeddings=False) is False
            assert SUCCESS_LINE not in capsys.readouterr().out

            # A held removal that nobody can confirm ends the command.
            engine.outcome = _held_outcome()
            with pytest.raises(typer.Exit) as refused:
                sync_cmd.run_incremental_sync(embeddings=False)
            assert refused.value.exit_code == 1
            assert SUCCESS_LINE not in capsys.readouterr().out

        with patch("plexmix.cli.sync_cmd.can_prompt", return_value=True), patch(
            "plexmix.cli.sync_cmd.typer.confirm", return_value=False
        ) as ask:
            # Declined: the tracks were kept, so no green line either.
            assert sync_cmd.run_incremental_sync(embeddings=False) is False
            assert SUCCESS_LINE not in capsys.readouterr().out
        ask.assert_called_once()
        assert "5,443 of 5,943" in ask.call_args.args[0]
        assert ask.call_args.kwargs["default"] is False

    def test_run_incremental_sync_does_not_allow_removals_by_default(
        self, patched_sync: SimpleNamespace
    ) -> None:
        with patch("plexmix.cli.sync_cmd.can_prompt", return_value=False):
            sync_cmd.run_incremental_sync()
            sync_cmd.run_incremental_sync(allow_removals=True)
            # A truthy object that is not True, as a Typer command called as a plain
            # function would pass for an option it was not given.
            sync_cmd.run_incremental_sync(allow_removals=MagicMock())  # type: ignore[arg-type]

        allowed = [
            call.kwargs["allow_large_removals"]
            for call in patched_sync.engine.incremental_sync.call_args_list
        ]
        assert allowed == [False, True, False]

    @pytest.mark.parametrize("command", SYNC_COMMANDS)
    def test_sync_help_lists_allow_removals(self, command: List[str]) -> None:
        result = runner.invoke(app, [*command, "--help"])

        assert result.exit_code == 0
        assert "--allow-removals" in result.output
        # One plain flag that is off by default: there is no "--no-" form of it.
        assert "--no-allow-removals" not in result.output
        assert "backup" in result.output.lower()


def test_cli_modules_never_call_sync_commands_as_functions() -> None:
    """Other code calls ``run_incremental_sync``, never a Typer command function.

    Called as a plain function, a command gets a truthy placeholder object for every
    option that was not passed. For ``--allow-removals`` that would read as "true".
    """
    cli_folder = Path(plexmix.cli.__file__).parent
    modules = sorted(cli_folder.rglob("*.py"))
    assert cli_folder / "sync_cmd.py" in modules
    assert cli_folder / "doctor_cmd.py" in modules

    offenders = [
        f"{module.relative_to(cli_folder)}:{line} calls {name}()"
        for module in modules
        for line, name in _sync_command_calls(module.read_text(encoding="utf-8"))
    ]
    assert offenders == []

    # The check itself sees such a call, plain or through the module.
    assert _sync_command_calls("sync_incremental(embeddings=False)\n") == [(1, "sync_incremental")]
    assert _sync_command_calls("x = 1\nsync_cmd.sync_full()\n") == [(2, "sync_full")]
    assert _sync_command_calls("def sync_regenerate() -> None:\n    pass\n") == []


# ---------------------------------------------------------------------------
# sync on the real engine, with a temporary library
# ---------------------------------------------------------------------------

LIBRARY_SIZE = 400


def _keys(count: int) -> List[str]:
    return [f"t{number}" for number in range(count)]


def _plex_track(key: str) -> Track:
    track = Track(plex_key=key, title=f"Song {key}", artist_id=0, album_id=0, play_count=0)
    track.__dict__["_artist_key"] = "a1"
    track.__dict__["_album_key"] = "al1"
    return track


class _FakePlex:
    """Stands in for the connected ``PlexClient``. It makes no request.

    ``listed`` are the track keys in its listing, and ``unread`` the ones among them
    whose details could not be read. ``relisted`` is what a later listing answers
    (the same keys unless given), or the error it raises. ``stop`` is raised in
    place of the track reads.
    """

    def __init__(
        self,
        listed: Iterable[str],
        unread: Iterable[str] = (),
        relisted: Any = None,
        stop: Optional[BaseException] = None,
    ) -> None:
        self.listed = list(listed)
        self.unread = set(unread)
        self.relisted = relisted
        self.stop = stop
        self.relistings = 0
        self.read_report = PlexReadReport(
            listed_track_keys=frozenset(self.listed),
            listed={"artists": 1, "albums": 1, "tracks": len(self.listed)},
            failed={"artists": 0, "albums": 0, "tracks": len(self.unread)},
        )

    def validate_token(self) -> Tuple[bool, str]:
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
        tracks = [_plex_track(key) for key in self.listed if key not in self.unread]
        if tracks:
            yield tracks

    def list_track_keys(self) -> Any:
        self.relistings += 1
        if isinstance(self.relisted, BaseException):
            raise self.relisted
        return frozenset(self.listed if self.relisted is None else self.relisted)


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A temporary library of 400 tagged tracks, where ``_make_settings`` points."""
    db_path = tmp_path / "test.db"
    with SQLiteManager(str(db_path)) as db:
        db.create_tables()
        artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist One"))
        album_id = db.insert_album(Album(plex_key="al1", title="Album One", artist_id=artist_id))
        with db.deferred_commits():
            for key in _keys(LIBRARY_SIZE):
                db.insert_track(
                    Track(
                        plex_key=key,
                        title=f"Song {key}",
                        artist_id=artist_id,
                        album_id=album_id,
                        play_count=0,
                        tags=f"tag-{key}",
                    )
                )
    return db_path


def _real_sync(
    library: Path,
    plex: _FakePlex,
    *flags: str,
    terminal: bool = False,
    answers: str = "",
    command: Iterable[str] = ("sync",),
) -> Result:
    """Run a sync command on the real engine. Only Plex is replaced, by ``plex``."""
    settings = _make_settings(library.parent)
    # The library sync alone, whatever the environment of the test run says.
    settings.musicbrainz.enabled = False
    settings.audio.analyze_on_sync = False
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch("plexmix.cli.sync_cmd.Settings.load_from_file", return_value=settings)
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.connect_plex", return_value=plex))
        stack.enter_context(patch("plexmix.cli.sync_cmd._build_ai_provider", return_value=None))
        return _invoke_sync(list(command), *flags, terminal=terminal, answers=answers)


def _query(database_file: Path, sql: str, immutable: bool = False) -> List[Tuple[Any, ...]]:
    """Read rows from a database file. ``immutable`` writes nothing beside a backup copy."""
    assert database_file.is_file()
    if immutable:
        connection = sqlite3.connect(f"{database_file.as_uri()}?immutable=1", uri=True)
    else:
        connection = sqlite3.connect(str(database_file))
    try:
        return list(connection.execute(sql).fetchall())
    finally:
        connection.close()


def _track_count(database_file: Path, immutable: bool = False) -> int:
    count: int = _query(database_file, "SELECT COUNT(*) FROM tracks", immutable)[0][0]
    return count


def _history(library: Path) -> List[str]:
    """The status of each sync history row, oldest first."""
    return [row[0] for row in _query(library, "SELECT status FROM sync_history ORDER BY id")]


def _backup_folders(library: Path) -> List[Path]:
    backups = library.parent / "backups"
    return sorted(backups.iterdir()) if backups.is_dir() else []


def _saved_index(library: Path) -> Path:
    """Save a search index with one vector per track where ``_make_settings`` points."""
    track_ids = [row[0] for row in _query(library, "SELECT id FROM tracks ORDER BY id")]
    index_path = library.parent / "test.index"
    dimension = _make_settings(library.parent).embedding.get_dimension_for_provider("gemini")
    index = VectorIndex(dimension=dimension, index_path=str(index_path))
    index.build_index([[1.0] + [0.0] * (dimension - 1) for _ in track_ids], track_ids)
    index.save_index(str(index_path))
    return index_path


def _assert_backup_is_named(result: Result, library: Path) -> None:
    """One backup folder exists, holds the whole library, and the output names it with its size."""
    [folder] = _backup_folders(library)
    size = sum(item.stat().st_size for item in folder.iterdir() if item.is_file())
    assert f"{folder} ({format_size(size)})" in result.stdout
    assert _track_count(folder / library.name, immutable=True) == LIBRARY_SIZE


class TestSyncRemovalsOnRealEngine:
    """The rows of design D11 again, end to end: real engine, real database, fake Plex."""

    def test_removal_at_the_threshold_is_applied_without_a_question(self, library: Path) -> None:
        # 50 of 400 tracks are gone: not more than 50, so there is nothing to confirm.
        result = _real_sync(library, _FakePlex(_keys(350)))

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "Incremental sync completed successfully!" in out
        assert "Tracks removed: 50" in out
        assert _track_count(library) == 350
        assert _backup_folders(library) == []
        assert _history(library) == ["success"]

    def test_large_removal_without_a_terminal_removes_nothing(self, library: Path) -> None:
        plex = _FakePlex(_keys(100))

        result = _real_sync(library, plex, terminal=False)

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Plex no longer lists 300 of the 400 tracks" in out
        assert "300 tracks were kept." in out
        assert "--allow-removals" in out
        assert "completed" not in out.lower()
        assert _track_count(library) == LIBRARY_SIZE
        assert plex.relistings == 0
        assert _backup_folders(library) == []
        assert _history(library) == ["partial"]

    def test_large_removal_declined_in_a_terminal_removes_nothing(self, library: Path) -> None:
        plex = _FakePlex(_keys(100))

        result = _real_sync(library, plex, terminal=True, answers="\n")

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "Remove 300 of 400 tracks? [y/N]" in out
        assert "300 tracks were kept." in out
        assert "completed" not in out.lower()
        assert _track_count(library) == LIBRARY_SIZE
        assert plex.relistings == 0
        assert _backup_folders(library) == []

    def test_large_removal_confirmed_in_a_terminal_backs_up_then_removes(
        self, library: Path
    ) -> None:
        plex = _FakePlex(_keys(100))

        result = _real_sync(library, plex, terminal=True, answers="y\n")

        assert result.exit_code == 0, result.output
        assert "Removed 300 tracks that Plex no longer lists." in _flat(result.stdout)
        assert plex.relistings == 1  # Plex was checked again before the removal
        assert _track_count(library) == 100
        _assert_backup_is_named(result, library)
        assert _history(library) == ["partial", "success"]

    def test_confirmed_removal_when_plex_lists_the_tracks_again(self, library: Path) -> None:
        plex = _FakePlex(_keys(100), relisted=_keys(LIBRARY_SIZE))

        result = _real_sync(library, plex, terminal=True, answers="y\n")

        assert result.exit_code == 0, result.output
        assert "Plex lists those tracks again. Nothing was removed." in _flat(result.stdout)
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    def test_confirmed_removal_when_more_tracks_are_missing(self, library: Path) -> None:
        # 300 tracks were confirmed; the new listing leaves out 360.
        plex = _FakePlex(_keys(100), relisted=_keys(40))

        result = _real_sync(library, plex, terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Plex no longer lists 360 tracks" in out
        assert "Nothing was removed." in out
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    def test_confirmed_removal_when_plex_cannot_be_listed(self, library: Path) -> None:
        plex = _FakePlex(_keys(100), relisted=PlexReadInterrupted("tracks"))

        result = _real_sync(library, plex, terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Plex could not list the library's tracks. Nothing was removed." in out
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    def test_allow_removals_backs_up_then_removes_without_a_question(self, library: Path) -> None:
        result = _real_sync(library, _FakePlex(_keys(100)), "--allow-removals", terminal=False)

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "Incremental sync completed successfully!" in out
        assert "Tracks removed: 300" in out
        assert "[y/N]" not in out
        assert _track_count(library) == 100
        _assert_backup_is_named(result, library)
        assert _history(library) == ["success"]

    @pytest.mark.parametrize("confirmed_by", ["flag", "answer"])
    def test_failed_backup_removes_nothing_and_exits_1(
        self, library: Path, confirmed_by: str
    ) -> None:
        # A file where the backups folder has to be: the backup cannot be written.
        (library.parent / "backups").write_text("not a folder", encoding="utf-8")
        plex = _FakePlex(_keys(100))

        if confirmed_by == "flag":
            result = _real_sync(library, plex, "--allow-removals")
        else:
            result = _real_sync(library, plex, terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "The backup could not be made" in out
        assert "Nothing was removed." in out
        assert "completed" not in out.lower()
        assert _track_count(library) == LIBRARY_SIZE

    @pytest.mark.parametrize("flags", [(), ("--allow-removals",)], ids=["plain", "allow-removals"])
    def test_empty_listing_removes_nothing_and_exits_1(
        self, library: Path, flags: Tuple[str, ...]
    ) -> None:
        plex = _FakePlex([])

        # A terminal and a ready "y": the removal is still not offered.
        result = _real_sync(library, plex, *flags, terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Plex listed 0 tracks" in out
        assert "Nothing was removed." in out
        assert "plexmix db reset" in out
        assert "completed" not in out.lower()
        assert "[y/N]" not in out
        assert plex.relistings == 0
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    def test_stopped_sync_changes_nothing(self, library: Path) -> None:
        plex = _FakePlex(
            _keys(100), stop=PlexReadInterrupted("tracks", read=22, listed=LIBRARY_SIZE)
        )

        result = _real_sync(library, plex, "--allow-removals")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert (
            "Plex stopped answering after 22 of 400 tracks were read, so nothing was changed."
            in out
        )
        assert "completed" not in out.lower()
        assert _track_count(library) == LIBRARY_SIZE
        assert _history(library) == ["failed"]

    def test_too_many_unread_tracks_remove_nothing(self, library: Path) -> None:
        # Plex lists 100 tracks and 20 of them cannot be read: Plex is not trusted
        # about the 300 it leaves out, with the flag or without it.
        plex = _FakePlex(_keys(100), unread=_keys(20))

        result = _real_sync(library, plex, "--allow-removals")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Tracks added: 0" in out
        assert "20 of 100 tracks could not be read and were kept. Nothing was removed." in out
        assert "completed" not in out.lower()
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    def test_a_few_unread_tracks_are_kept_and_counted(self, library: Path) -> None:
        plex = _FakePlex(_keys(LIBRARY_SIZE), unread=_keys(3))

        result = _real_sync(library, plex)

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "3 tracks could not be read and were kept." in out
        assert "completed" not in out.lower()
        assert _track_count(library) == LIBRARY_SIZE
        assert _history(library) == ["partial"]

    def test_flag_before_the_subcommand_name_removes_too(self, library: Path) -> None:
        result = _real_sync(
            library, _FakePlex(_keys(100)), command=("sync", "--allow-removals", "full")
        )

        assert result.exit_code == 0, result.output
        assert "Tracks removed: 300" in _flat(result.stdout)
        assert _track_count(library) == 100
        _assert_backup_is_named(result, library)

    @pytest.mark.parametrize("confirmed_by", ["flag", "answer"])
    def test_sync_without_embeddings_backs_up_and_updates_the_index(
        self, library: Path, confirmed_by: str
    ) -> None:
        """Spec "Large removals are backed up first": the search index files too.

        ``--no-embeddings`` gives the engine no index object. The backup still holds
        the index files, and the removed tracks' vectors leave the index.
        """
        index_path = _saved_index(library)
        plex = _FakePlex(_keys(100))

        # ``_real_sync`` runs every command with ``--no-embeddings``.
        if confirmed_by == "flag":
            result = _real_sync(library, plex, "--allow-removals")
        else:
            result = _real_sync(library, plex, terminal=True, answers="y\n")

        assert result.exit_code == 0, result.output
        assert _track_count(library) == 100
        [folder] = _backup_folders(library)
        assert sorted(item.name for item in folder.iterdir()) == [
            "test.db",
            "test.index",
            "test.metadata",
        ]
        # The copy is from before the removal, and the live index follows the library.
        dimension = _make_settings(library.parent).embedding.get_dimension_for_provider("gemini")
        copied = VectorIndex(dimension=dimension, index_path=str(folder / "test.index"))
        assert len(copied.track_ids) == LIBRARY_SIZE
        live = VectorIndex(dimension=dimension, index_path=str(index_path))
        left = [row[0] for row in _query(library, "SELECT id FROM tracks ORDER BY id")]
        assert sorted(live.track_ids) == left

    def test_confirmed_removal_keeps_tracks_that_went_missing_after_the_question(
        self, library: Path
    ) -> None:
        """Spec: a confirmed removal removes "only tracks that are still not listed"."""
        # The sync: Plex lists t0 to t99, so 300 tracks are held. Before the answer,
        # Plex lists the held tracks again and leaves out the first 100.
        plex = _FakePlex(_keys(100), relisted=_keys(LIBRARY_SIZE)[100:])

        result = _real_sync(library, plex, terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "Remove 300 of 400 tracks? [y/N]" in out
        assert "Plex lists those tracks again. Nothing was removed." in out
        assert "100 other tracks are missing from Plex now." in out
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    def test_connection_check_that_fails_changes_nothing(self, library: Path) -> None:
        """The engine checks the connection itself before it starts (and before it clears)."""
        plex = _FakePlex(_keys(LIBRARY_SIZE))
        plex.validate_token = lambda: (  # type: ignore[method-assign]
            False,
            "Cannot reach Plex server: the connection was closed",
        )

        for command in (("sync",), ("sync", "regenerate")):
            result = _real_sync(library, plex, command=command)

            out = _flat(result.stdout)
            assert result.exit_code == 1
            # The command ended by itself: there is no error for Typer to report.
            assert isinstance(result.exception, SystemExit)
            assert "Cannot reach Plex server: the connection was closed." in out
            assert "completed" not in out.lower()
            # Nothing was cleared, and nothing says so.
            assert "cleared" not in out
        tagged: int = _query(
            library, "SELECT COUNT(*) FROM tracks WHERE tags IS NOT NULL AND tags != ''"
        )[0][0]
        assert (_track_count(library), tagged) == (LIBRARY_SIZE, LIBRARY_SIZE)

    def test_stopped_regenerate_says_what_happened_to_the_tags(self, library: Path) -> None:
        """A stopped ``sync regenerate`` must not let "nothing was changed" stand alone.

        The engine's sentence is about the library sync. Today ``regenerate_sync``
        clears the tags before it reads Plex, so the command adds a line that says so.
        The line and the database have to agree: when the engine stops clearing
        first, the line has to go.
        """
        plex = _FakePlex(
            _keys(LIBRARY_SIZE), stop=PlexReadInterrupted("tracks", read=22, listed=LIBRARY_SIZE)
        )

        result = _real_sync(library, plex, command=("sync", "regenerate"))

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Plex stopped answering after 22 of 400 tracks were read" in out
        assert "completed" not in out.lower()
        assert _track_count(library) == LIBRARY_SIZE
        tagged: int = _query(
            library, "SELECT COUNT(*) FROM tracks WHERE tags IS NOT NULL AND tags != ''"
        )[0][0]
        says_cleared = "tags and embeddings had already been cleared" in out
        assert says_cleared == (tagged == 0)

    def test_connection_check_that_fails_says_that_nothing_was_changed(self, library: Path) -> None:
        """Design D11, first row: the sentence ends with "Nothing was changed."."""
        plex = _FakePlex(_keys(100))
        plex.validate_token = lambda: (  # type: ignore[method-assign]
            False,
            "Cannot reach Plex server: the connection was closed",
        )

        for command in (("sync",), ("sync", "--allow-removals"), ("sync", "regenerate")):
            result = _real_sync(library, plex, command=command)

            assert result.exit_code == 1
            assert isinstance(result.exception, SystemExit)
            assert (
                "Cannot reach Plex server: the connection was closed. Nothing was changed."
                in _flat(result.stdout)
            )
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    @pytest.mark.parametrize("confirmed_by", ["flag", "answer"])
    def test_interrupt_while_the_index_is_updated_is_reported_as_a_removal(
        self, library: Path, confirmed_by: str
    ) -> None:
        """Ctrl+C after the deletes were saved: the output and the history agree with the library."""
        index_path = _saved_index(library)
        plex = _FakePlex(_keys(100))
        save_index = VectorIndex.save_index
        saves: List[str] = []

        def interrupted_once(index: VectorIndex, path: str) -> None:
            saves.append(path)
            if len(saves) == 1:
                raise KeyboardInterrupt()
            save_index(index, path)

        with patch.object(VectorIndex, "save_index", interrupted_once):
            if confirmed_by == "flag":
                result = _real_sync(library, plex, "--allow-removals")
            else:
                result = _real_sync(library, plex, terminal=True, answers="y\n")

        assert result.exit_code == 130
        assert _track_count(library) == 100
        out = _flat(result.stdout)
        if confirmed_by == "flag":
            assert "Tracks removed: 300" in out
        else:
            assert "300 tracks had already been removed from the PlexMix library." in out
        [folder] = _backup_folders(library)
        assert str(folder) in _squeezed(result.stdout)
        rows = _query(library, "SELECT status, tracks_removed FROM sync_history ORDER BY id")
        assert rows[-1] == (("interrupted" if confirmed_by == "flag" else "success"), 300)
        # The index update was finished before the command ended.
        dimension = _make_settings(library.parent).embedding.get_dimension_for_provider("gemini")
        live = VectorIndex(dimension=dimension, index_path=str(index_path))
        assert len(live.track_ids) == 100


# ---------------------------------------------------------------------------
# embeddings generate
# ---------------------------------------------------------------------------


class TestEmbeddingsGenerate:
    """Tests for `plexmix embeddings generate`."""

    @patch("plexmix.cli.embeddings_cmd.rebuild_vector_index", return_value=1)
    @patch("plexmix.cli.embeddings_cmd.SQLiteManager")
    @patch("plexmix.cli.embeddings_cmd._build_embedding_generator")
    @patch("plexmix.cli.embeddings_cmd.Settings.load_from_file")
    def test_no_tracks_need_embeddings(
        self, mock_load, mock_build_emb, mock_db_cls, mock_rebuild, tmp_path
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_gen = MagicMock()
        mock_gen.get_dimension.return_value = 1536
        mock_gen.provider_name = "OpenAI"
        mock_build_emb.return_value = mock_gen

        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = [_dummy_track(1)]
        # Track 1 already has an embedding for the active model.
        mock_db.get_track_ids_with_embeddings.return_value = {1}
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        result = runner.invoke(app, ["embeddings", "generate"])
        assert result.exit_code == 0
        assert "all tracks already have embeddings" in result.output.lower()
        # The index is still rebuilt so a missing/lost index recovers.
        mock_rebuild.assert_called_once()

    @patch("plexmix.cli.embeddings_cmd.rebuild_vector_index", return_value=2)
    @patch("plexmix.cli.embeddings_cmd.generate_embeddings_for_tracks", return_value=2)
    @patch("plexmix.cli.embeddings_cmd.build_vector_index")
    @patch("plexmix.cli.embeddings_cmd.SQLiteManager")
    @patch("plexmix.cli.embeddings_cmd._build_embedding_generator")
    @patch("plexmix.cli.embeddings_cmd.Settings.load_from_file")
    def test_generate_for_n_tracks(
        self,
        mock_load,
        mock_build_emb,
        mock_db_cls,
        mock_bvi,
        mock_gen_emb,
        mock_rebuild,
        tmp_path,
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_gen = MagicMock()
        mock_gen.get_dimension.return_value = 1536
        mock_build_emb.return_value = mock_gen

        tracks = [_dummy_track(1, "Song A"), _dummy_track(2, "Song B")]
        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = tracks
        mock_db.get_embedding_by_track_id.return_value = None  # no embeddings yet
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        result = runner.invoke(app, ["embeddings", "generate"])
        assert result.exit_code == 0
        assert "2" in result.output  # "Found 2 tracks without embeddings" or success msg
        mock_gen_emb.assert_called_once()
        mock_rebuild.assert_called_once()

    @patch("plexmix.cli.embeddings_cmd._build_embedding_generator", return_value=None)
    @patch("plexmix.cli.embeddings_cmd.Settings.load_from_file")
    def test_no_embedding_provider_exits(self, mock_load, mock_build_emb, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        result = runner.invoke(app, ["embeddings", "generate"])
        assert result.exit_code == 1
        assert "api key required" in result.output.lower()


# ---------------------------------------------------------------------------
# tags generate
# ---------------------------------------------------------------------------


class TestTagsGenerate:
    """Tests for `plexmix tags generate`."""

    @patch("plexmix.cli.tags_cmd.SQLiteManager")
    @patch("plexmix.cli.tags_cmd._resolve_ai_api_key", return_value="key123")
    @patch("plexmix.cli.tags_cmd.Settings.load_from_file")
    def test_all_tracks_already_tagged(self, mock_load, mock_resolve, mock_db_cls, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        tagged = _dummy_track(1, tags="rock, upbeat")
        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = [tagged]
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        result = runner.invoke(app, ["tags", "generate", "--provider", "gemini"])
        assert result.exit_code == 0
        assert "all tracks already have up-to-date tags" in result.output.lower()

    @patch("plexmix.cli.tags_cmd._build_embedding_generator", return_value=None)
    @patch("plexmix.cli.tags_cmd.TagGenerator")
    @patch("plexmix.cli.tags_cmd._build_ai_provider")
    @patch("plexmix.cli.tags_cmd.SQLiteManager")
    @patch("plexmix.cli.tags_cmd._resolve_ai_api_key", return_value="key123")
    @patch("plexmix.cli.tags_cmd.Settings.load_from_file")
    def test_normal_tag_generation(
        self,
        mock_load,
        mock_resolve,
        mock_db_cls,
        mock_build_ai,
        mock_tag_gen_cls,
        mock_build_emb,
        tmp_path,
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        untagged = _dummy_track(1, "Untagged Song")
        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = [untagged]
        mock_db.get_artist_by_id.return_value = MagicMock(name="Artist")
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        mock_ai = MagicMock()
        mock_build_ai.return_value = mock_ai

        mock_tg = MagicMock()
        mock_tg.generate_tags_batch.return_value = {
            1: {"tags": ["rock", "upbeat"], "environments": ["party"], "instruments": ["guitar"]},
        }
        mock_tag_gen_cls.return_value = mock_tg

        result = runner.invoke(
            app, ["tags", "generate", "--provider", "gemini", "--no-regenerate-embeddings"]
        )
        assert result.exit_code == 0
        assert (
            "1 tracks without tags" in result.output.lower() or "updated" in result.output.lower()
        )
        mock_tg.generate_tags_batch.assert_called_once()

    @patch("plexmix.cli.tags_cmd._resolve_ai_api_key", return_value=None)
    @patch("plexmix.cli.tags_cmd.Settings.load_from_file")
    def test_missing_api_key_exits(self, mock_load, mock_resolve, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        result = runner.invoke(app, ["tags", "generate", "--provider", "gemini"])
        assert result.exit_code == 1
        assert "api key not configured" in result.output.lower()


# ---------------------------------------------------------------------------
# create (playlist generation)
# ---------------------------------------------------------------------------


class TestCreatePlaylist:
    """Tests for `plexmix create`."""

    @patch("plexmix.cli.create_cmd.PlaylistGenerator")
    @patch("plexmix.cli.create_cmd.build_vector_index")
    @patch("plexmix.cli.create_cmd.SQLiteManager")
    @patch("plexmix.cli.create_cmd._build_embedding_generator")
    @patch("plexmix.cli.create_cmd.Settings.load_from_file")
    def test_successful_create(
        self,
        mock_load,
        mock_build_emb,
        mock_db_cls,
        mock_build_vi,
        mock_gen_cls,
        tmp_path,
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_emb = MagicMock()
        mock_emb.get_dimension.return_value = 1536
        mock_build_emb.return_value = mock_emb

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        mock_vi = MagicMock()
        mock_vi.dimension_mismatch = False
        mock_build_vi.return_value = mock_vi

        generated_tracks = [
            {"id": 1, "title": "Chill Vibes", "artist": "DJ Cool", "album": "Relax"},
            {"id": 2, "title": "Sunset", "artist": "Mellow", "album": "Evening"},
        ]
        mock_gen = MagicMock()
        mock_gen.generate.return_value = generated_tracks
        mock_gen_cls.return_value = mock_gen

        result = runner.invoke(
            app,
            [
                "create",
                "chill vibes",
                "--limit",
                "2",
                "--name",
                "My Playlist",
                "--no-create-in-plex",
            ],
        )
        assert result.exit_code == 0
        assert "Chill Vibes" in result.output
        assert "DJ Cool" in result.output
        assert "saved" in result.output.lower()
        mock_gen.save_playlist.assert_called_once()

    @patch("plexmix.cli.create_cmd.PlaylistGenerator")
    @patch("plexmix.cli.create_cmd.build_vector_index")
    @patch("plexmix.cli.create_cmd.SQLiteManager")
    @patch("plexmix.cli.create_cmd._build_embedding_generator")
    @patch("plexmix.cli.create_cmd.Settings.load_from_file")
    def test_no_tracks_found_exits(
        self, mock_load, mock_build_emb, mock_db_cls, mock_build_vi, mock_gen_cls, tmp_path
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_emb = MagicMock()
        mock_emb.get_dimension.return_value = 1536
        mock_build_emb.return_value = mock_emb

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        mock_vi = MagicMock()
        mock_vi.dimension_mismatch = False
        mock_build_vi.return_value = mock_vi

        mock_gen = MagicMock()
        mock_gen.generate.return_value = []
        mock_gen_cls.return_value = mock_gen

        result = runner.invoke(
            app,
            ["create", "metal blast", "--limit", "5", "--name", "X", "--no-create-in-plex"],
        )
        assert result.exit_code == 1
        assert "no tracks found" in result.output.lower()

    @patch("plexmix.cli.create_cmd.SQLiteManager")
    @patch("plexmix.cli.create_cmd._build_embedding_generator", return_value=None)
    @patch("plexmix.cli.create_cmd.Settings.load_from_file")
    def test_no_embedding_provider_exits(self, mock_load, mock_build_emb, mock_db_cls, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = MagicMock()
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        result = runner.invoke(
            app,
            ["create", "jazzy", "--limit", "5", "--name", "Jazz", "--no-create-in-plex"],
        )
        assert result.exit_code == 1
        assert "api key required" in result.output.lower()


# ---------------------------------------------------------------------------
# db info
# ---------------------------------------------------------------------------


class TestDbInfo:
    """Tests for `plexmix db info`."""

    @patch("plexmix.cli.db_cmd.Settings")
    def test_nonexistent_db_shows_message(self, mock_settings_cls, tmp_path):
        settings = _make_settings(tmp_path)
        # Point at a DB path that doesn't exist
        settings.database.path = str(tmp_path / "nonexistent.db")
        mock_settings_cls.load_from_file.return_value = settings

        result = runner.invoke(app, ["db", "info"])
        assert result.exit_code == 0
        assert "does not exist" in result.output or "not initialized" in result.output.lower()

    @patch("plexmix.cli.db_cmd.Settings")
    def test_existing_db_shows_stats(self, mock_settings_cls, tmp_path):
        from plexmix.database.sqlite_manager import SQLiteManager

        db_path = tmp_path / "plexmix.db"
        index_path = tmp_path / "test.index"

        # Create a real DB with the schema initialised
        with SQLiteManager(str(db_path)) as db:
            db.create_tables()

        settings = _make_settings(tmp_path)
        settings.database.path = str(db_path)
        settings.database.faiss_index_path = str(index_path)
        mock_settings_cls.load_from_file.return_value = settings

        result = runner.invoke(app, ["db", "info"])
        assert result.exit_code == 0
        assert "tracks" in result.output.lower()
        assert "artists" in result.output.lower()
        assert "albums" in result.output.lower()


# ---------------------------------------------------------------------------
# audio info
# ---------------------------------------------------------------------------


class TestAudioInfo:
    """Tests for `plexmix audio info`."""

    @patch("plexmix.cli.audio_cmd.SQLiteManager")
    @patch("plexmix.cli.audio_cmd.Settings.load_from_file")
    def test_audio_info_table_output(self, mock_load, mock_db_cls, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = [
            _dummy_track(1, "A"),
            _dummy_track(2, "B"),
        ]
        mock_db.get_audio_features_count.return_value = 1
        mock_db.get_tracks_without_audio_features.return_value = [_dummy_track(2, "B")]
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        result = runner.invoke(app, ["audio", "info"])
        assert result.exit_code == 0
        assert "total tracks" in result.output.lower()
        assert "tracks analyzed" in result.output.lower()

    @patch("plexmix.cli.audio_cmd.SQLiteManager")
    @patch("plexmix.cli.audio_cmd.Settings.load_from_file")
    def test_audio_info_zero_tracks(self, mock_load, mock_db_cls, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = []
        mock_db.get_audio_features_count.return_value = 0
        mock_db.get_tracks_without_audio_features.return_value = []
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        result = runner.invoke(app, ["audio", "info"])
        assert result.exit_code == 0
        # Coverage should be "N/A" when no tracks with file paths
        assert "n/a" in result.output.lower()
