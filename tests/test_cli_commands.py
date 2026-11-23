"""Integration tests for CLI commands using typer.testing.CliRunner.

Each test class focuses on one command group and heavily mocks external
dependencies (Plex, AI providers, credentials, database) so tests run
quickly without network or file-system side-effects.

The sync tests come in two sets. ``TestSyncIncremental`` and ``TestSyncRemovals``
patch the engine and check what the commands print and how they exit.
``TestSyncRemovalsOnRealEngine`` runs the same situations on the real engine with
a temporary database and a fake Plex, and checks what is left in the library.

``TestSyncRegenerate`` and the regenerate tests of ``TestEmbeddingsGenerate`` run the
two regenerate commands on the real engine and a temporary library. Only Plex and
the providers are fakes, so each test also checks what is left in the database.
"""

import ast
import json
import logging
import sqlite3
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple
from unittest.mock import patch, MagicMock

import click
import pytest
import typer
from click.testing import Result
from typer.testing import CliRunner

import plexmix.cli
from plexmix.cli import embeddings_cmd, sync_cmd
from plexmix.cli.console_helpers import format_size
from plexmix.cli.embedding_failure import embedding_failure_lines, print_embedding_failure
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
from plexmix.database.index_rebuild import IndexRebuild
from plexmix.database.models import Album, Artist, Embedding, Track, SyncHistory
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
    RegeneratePass,
    RegenerateResult,
    RemovalResult,
    SyncOutcome,
)
from plexmix.services.providers import ProviderFailure, build_embedding_generator_with_reason
from plexmix.services.readiness import LOCAL_REMEDY, Cause
from plexmix.services.regenerate_service import (
    CAUSE_NO_ANSWER,
    CAUSE_NO_KEY,
    COMPONENT_AI,
    COMPONENT_EMBEDDINGS,
    REFUSED_NO_ANSWER,
    REFUSED_NO_KEY,
    SCOPE_EMBEDDINGS,
    SCOPE_TAGS,
    CheckedProviders,
    RegenerateRefused,
    marker_path,
    run_provider,
)

runner = CliRunner()

# The only "secrets" in this file are two of the project's dummy values.
DUMMY_PASSWORD = "dummy-ui-password-0000"
DUMMY_KEY = "dummy-api-key-0000"


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


def _regenerated(
    tags: int = 5,
    tags_kept: int = 0,
    embeddings: Optional[int] = None,
    embeddings_kept: int = 0,
    stopped_scope: str = "",
    error_message: str = "",
    unfinished: bool = False,
) -> RegenerateResult:
    """What the engine publishes after a regenerate run, built from counts.

    ``tags`` and ``embeddings`` rows were written, and the ``*_kept`` tracks hold
    what they had. ``embeddings=None`` is a run of tags alone. ``stopped_scope``
    names the pass that an error stopped part-way, with ``error_message`` to show.
    ``unfinished`` is a run that ended before its passes reached their end.
    """

    def part(written: int, kept: int, first_id: int, stopped: bool) -> RegeneratePass:
        ids = set(range(first_id, first_id + written + kept))
        return RegeneratePass(
            requested=True,
            started=True,
            finished=not (stopped or unfinished),
            to_do_ids=ids,
            written_ids=set(range(first_id, first_id + written)),
        )

    result = RegenerateResult(
        tags=part(tags, tags_kept, 1, stopped_scope == SCOPE_TAGS),
        stopped_scope=stopped_scope,
        error_message=error_message,
    )
    if embeddings is not None:
        result.embeddings = part(embeddings, embeddings_kept, 1, stopped_scope == SCOPE_EMBEDDINGS)
    return result


@pytest.fixture
def patched_sync(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """The sync commands with Plex, the providers, the database and the engine patched.

    ``engine`` stands in for the ``SyncEngine`` object. Its sync reports 5 added and
    2 updated tracks, and its outcome is a clean run until a test sets another one.
    Its regenerate run wrote the tags of 5 tracks and kept none. ``engine_cls``
    records how the command built the engine, and ``check`` is the provider check
    of ``sync regenerate``, which passes and hands over an AI provider.
    """
    engine = MagicMock()
    history = SyncHistory(tracks_added=5, tracks_updated=2, tracks_removed=0)
    engine.incremental_sync.return_value = history
    engine.regenerate_sync.return_value = history
    engine.outcome = SyncOutcome()
    engine.regenerate_result = _regenerated()
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
        check = stack.enter_context(
            patch(
                "plexmix.cli.sync_cmd.check_providers",
                return_value=CheckedProviders(MagicMock(), None),
            )
        )
        engine_cls = stack.enter_context(
            patch("plexmix.cli.sync_cmd.SyncEngine", return_value=engine)
        )
        yield SimpleNamespace(engine=engine, engine_cls=engine_cls, check=check, settings=settings)


def _invoke_sync(
    command: List[str], *flags: str, terminal: bool = False, answers: str = ""
) -> Result:
    """Run a sync command without embeddings, with or without a terminal to ask in.

    ``terminal`` and ``answers`` are about the removal question: whether it can be
    asked, and what the user types there. ``sync regenerate`` asks its own question
    first, and ``--yes`` answers that one. It answers nothing else.
    """
    yes = ["--yes"] if command[-1] == "regenerate" else []
    with patch("plexmix.cli.sync_cmd.can_prompt", return_value=terminal):
        return runner.invoke(app, [*command, "--no-embeddings", *yes, *flags], input=answers)


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
        # The run never reached the tags, so no count of regenerated tracks is printed.
        assert "regenerated" not in out

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
        yes = ["--yes"] if subcommand == "regenerate" else []
        with patch("plexmix.cli.sync_cmd.can_prompt", return_value=False):
            result = runner.invoke(
                app, ["sync", "--allow-removals", subcommand, "--no-embeddings", *yes]
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

    @pytest.mark.parametrize(
        "command",
        [["sync"], ["sync", "incremental"], ["sync", "full"]],
        ids=["sync", "incremental", "full"],
    )
    def test_index_that_was_left_as_it_is_ends_with_warnings_and_status_0(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        """The sync did its work, and the engine left the search index as it is.

        Tracks hold an embedding that was made with other embedding settings, so an
        index of the rows that fit would cover only part of the library. That is a
        warning, with the count and the command that replaces those embeddings.
        """
        patched_sync.engine.outcome = SyncOutcome(library_total=380, index_left_out=380)

        result = _invoke_sync(command)

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "Incremental sync finished with warnings." in out
        assert (
            "The search index was left as it is: 380 tracks hold an embedding that was made "
            "with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
            "to replace them." in out
        )
        assert out.count("The search index was left as it is") == 1
        # The counts of the library sync are still shown, and nothing is called a success.
        assert "Tracks added: 5" in out
        assert SUCCESS_LINE not in out.lower()
        assert "completed" not in out.lower()

    def test_one_track_in_the_way_of_the_index_is_said_in_the_singular(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.outcome = SyncOutcome(library_total=380, index_left_out=1)

        out = _flat(_invoke_sync(["sync"]).stdout)

        assert (
            "The search index was left as it is: 1 track holds an embedding that was made "
            "with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
            "to replace it." in out
        )

    def test_run_incremental_sync_returns_false_when_the_index_was_left_as_it_is(
        self, patched_sync: SimpleNamespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A caller prints its own "completed" line only after True (Doctor's sync does)."""
        patched_sync.engine.outcome = SyncOutcome(library_total=380, index_left_out=380)

        with patch("plexmix.cli.sync_cmd.can_prompt", return_value=False):
            assert sync_cmd.run_incremental_sync(embeddings=True) is False

        out = _flat(capsys.readouterr().out)
        assert "finished with warnings" in out and _index_sentence(380) in out
        assert SUCCESS_LINE not in out

    def test_index_left_as_it_is_does_not_hide_a_held_removal(
        self, patched_sync: SimpleNamespace
    ) -> None:
        outcome = _held_outcome()
        outcome.index_left_out = 380
        patched_sync.engine.outcome = outcome

        result = _invoke_sync(["sync"])

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert _index_sentence(380) in out
        assert "Plex no longer lists 5,443 of the 5,943 tracks" in out
        assert "--allow-removals" in out

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
        # On GitHub Actions Typer colours its help text, and the colour codes split
        # an option name such as "--allow-removals". Compare the plain text.
        help_text = click.unstyle(result.output)

        assert result.exit_code == 0
        assert "--allow-removals" in help_text
        # One plain flag that is off by default: there is no "--no-" form of it.
        assert "--no-allow-removals" not in help_text
        assert "backup" in help_text.lower()


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


def _ids_in_prompt(prompt: str) -> List[int]:
    """The ids of the tracks a tagging request lists."""
    listed = prompt.split("Assign tags to the following songs:")[1]
    listed = listed.split("Return a JSON object")[0]
    return [int(entry["id"]) for entry in json.loads(listed)]


class _TaggingAI:
    """Stands in for the AI provider. It gives every track it is sent a new tag.

    From request ``fail_from`` on (counted from 1) it raises ``error`` in place of
    an answer. ``omit`` ids are left out of its replies, and ``no_tags`` ids get
    environments and instruments with an empty list of tags. ``requests`` records
    the track ids of every request.
    """

    provider_name = "OpenAI"
    model = "gpt-test"
    api_key = DUMMY_KEY

    def __init__(
        self,
        error: Optional[BaseException] = None,
        fail_from: int = 1,
        omit: Iterable[int] = (),
        no_tags: Iterable[int] = (),
    ) -> None:
        self.error = error
        self.fail_from = fail_from
        self.omit = set(omit)
        self.no_tags = set(no_tags)
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
                    "tags": [] if track_id in self.no_tags else [f"new-{track_id}"],
                    "environments": ["focus"],
                    "instruments": ["piano"],
                }
                for track_id in ids
                if track_id not in self.omit
            }
        )

    @property
    def sent(self) -> List[int]:
        """Every track that was sent to the provider, by id."""
        return sorted({track_id for request in self.requests for track_id in request})


OLD_VECTOR = [0.1, 0.2, 0.3, 0.4]
NEW_VECTOR = [9.0, 1.0, 1.0, 1.0]
# The embedding provider of the regenerate tests, as ``_regenerate_settings`` names it.
VECTOR_MODEL = "openai"


class _Vectors:
    """Stands in for the embedding generator. Every text gets ``NEW_VECTOR``.

    Request ``fail_on_request`` (counted from 1) raises ``error`` in place of an
    answer. ``short`` vectors are missing at the end of every reply: the provider
    answered, and left those texts out. ``before_reply`` is called with the number
    of the request before it is answered. ``requests`` records how many texts each
    request held.
    """

    provider_name = VECTOR_MODEL

    def __init__(
        self,
        fail_on_request: Optional[int] = None,
        error: Optional[BaseException] = None,
        short: int = 0,
        before_reply: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.fail_on_request = fail_on_request
        self.error = error or RuntimeError("the embedding provider stopped answering")
        self.short = short
        self.before_reply = before_reply
        self.requests: List[int] = []

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        self.requests.append(len(texts))
        if self.fail_on_request == len(self.requests):
            raise self.error
        if self.before_reply is not None:
            self.before_reply(len(self.requests))
        return [list(NEW_VECTOR) for _ in texts[: len(texts) - self.short]]

    def generate_embedding(self, text: str) -> List[float]:
        return list(NEW_VECTOR)

    def get_dimension(self) -> int:
        return len(NEW_VECTOR)


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
    ai: Optional[_TaggingAI] = None,
) -> Result:
    """Run a sync command on the real engine. Only Plex is replaced, by ``plex``.

    ``sync regenerate`` never runs without an AI provider. Its provider check is
    replaced by one that passes and hands over ``ai`` (a fake that tags every track).
    """
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
        stack.enter_context(
            patch(
                "plexmix.cli.sync_cmd.check_providers",
                return_value=CheckedProviders(ai or _TaggingAI(), None),
            )
        )
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


def _tag_rows(library: Path) -> List[Tuple[Any, ...]]:
    """Per track: its id, its three tag columns and the time they were written."""
    return _query(
        library,
        "SELECT id, tags, environments, instruments, tags_generated_at FROM tracks ORDER BY id",
    )


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


def _index_files(library: Path) -> Dict[str, bytes]:
    """Every file of the search index beside the library, moved-aside copies included."""
    return {
        item.name: item.read_bytes()
        for item in sorted(library.parent.iterdir())
        if item.name.startswith(("test.index", "test.metadata"))
    }


# An embedding of the vector size the library had before that size was changed.
WIDE_VECTOR = [0.5] * 8


def _seed_earlier_size(
    library: Path, track_ids: Optional[Iterable[int]] = None
) -> Dict[str, bytes]:
    """The library as it was before the vector size changed from 8 numbers to 4.

    Every track (or each of ``track_ids``) holds an embedding of 8 numbers that was
    written long ago, under the model name that is still in use, and the saved
    index holds those vectors. The provider makes 4 numbers now (``_Vectors``), so
    an index that is opened for it reports a dimension mismatch. Returns the files
    of the saved index, byte for byte.
    """
    with SQLiteManager(str(library)) as db:
        ids = [track.id for track in db.get_all_tracks() if track.id is not None]
        if track_ids is not None:
            ids = sorted(set(ids) & set(track_ids))
        db.insert_embeddings_batch(
            [
                Embedding(
                    track_id=track_id,
                    embedding_model=VECTOR_MODEL,
                    embedding_dim=len(WIDE_VECTOR),
                    vector=WIDE_VECTOR,
                    created_at=LONG_AGO,
                    updated_at=LONG_AGO,
                )
                for track_id in ids
            ]
        )
    index_path = library.parent / "test.index"
    index = VectorIndex(dimension=len(WIDE_VECTOR), index_path=str(index_path))
    index.build_index([WIDE_VECTOR] * len(ids), ids)
    assert index.save_index(str(index_path)) is True
    files = _index_files(library)
    assert sorted(files) == ["test.index", "test.metadata"]
    return files


def _assert_saved_index_is_untouched(library: Path, before: Dict[str, bytes]) -> None:
    """The index files are the ones from before the command, byte for byte, and an
    index opened on them for the current provider still reports the dimension
    mismatch that tells every command to regenerate."""
    assert _index_files(library) == before
    opened = VectorIndex(dimension=len(NEW_VECTOR), index_path=str(library.parent / "test.index"))
    assert opened.dimension_mismatch is True
    assert opened.loaded_dimension == len(WIDE_VECTOR)
    assert opened.load_failed is False
    # Opening it changed nothing either.
    assert _index_files(library) == before


def _index_sentence(count: int) -> str:
    """What a command says of a search index that was left as it is (``count`` above 1)."""
    return (
        f"The search index was left as it is: {count:,} tracks hold an embedding that was "
        "made with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
        "to replace them."
    )


def _embedding_sizes(library: Path) -> Dict[int, int]:
    """How many embedding rows of the current model hold a vector of each size."""
    sizes: Dict[int, int] = {}
    for vector in _vectors_by_track(library).values():
        sizes[len(vector)] = sizes.get(len(vector), 0) + 1
    return sizes


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

    @pytest.mark.parametrize("confirmed_by", ["flag", "answer"])
    def test_unreadable_index_opened_for_a_removal_is_reported(
        self, library: Path, confirmed_by: str
    ) -> None:
        """Spec "An unreadable search index is kept": a CLI command prints that too.

        A sync without embeddings has no index object. The engine opens the saved
        index itself to drop the removed tracks from it. Files it cannot load are
        moved aside by that, and the command must say so, not only the log.
        """
        folder = library.parent
        (folder / "test.index").write_bytes(b"this is not a search index")
        plex = _FakePlex(_keys(100))

        if confirmed_by == "flag":
            result = _real_sync(library, plex, "--allow-removals")
        else:
            result = _real_sync(library, plex, terminal=True, answers="y\n")

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert _track_count(library) == 100
        # Said once, with the new name of the file and the command that rebuilds.
        assert out.count("The search index could not be loaded.") == 1
        assert "Moved aside, not deleted:" in out
        assert "run 'plexmix embeddings generate'" in out
        [moved] = [item for item in folder.iterdir() if ".unreadable-" in item.name]
        assert moved.name.startswith("test.index.unreadable-")
        assert moved.read_bytes() == b"this is not a search index"
        assert str(moved) in _squeezed(result.stdout)
        assert not (folder / "test.index").exists()

    def test_healthy_index_opened_for_a_removal_prints_no_notice(self, library: Path) -> None:
        _saved_index(library)

        result = _real_sync(library, _FakePlex(_keys(100)), "--allow-removals")

        assert result.exit_code == 0, result.output
        assert "could not be loaded" not in _flat(result.stdout)

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
        """The engine checks the connection itself, before it reads or writes anything."""
        plex = _FakePlex(_keys(LIBRARY_SIZE))
        plex.validate_token = lambda: (  # type: ignore[method-assign]
            False,
            "Cannot reach Plex server: the connection was closed",
        )
        before = _tag_rows(library)
        ai = _TaggingAI()

        for command in (("sync",), ("sync", "regenerate")):
            result = _real_sync(library, plex, command=command, ai=ai)

            out = _flat(result.stdout)
            assert result.exit_code == 1
            # The command ended by itself: there is no error for Typer to report.
            assert isinstance(result.exception, SystemExit)
            assert "Cannot reach Plex server: the connection was closed." in out
            assert "completed" not in out.lower()
        # Every track holds the tags it had, and the AI provider was never asked.
        assert _tag_rows(library) == before
        assert ai.requests == []
        assert not marker_path(library).exists()

    def test_stopped_regenerate_changes_nothing(self, library: Path) -> None:
        """A ``sync regenerate`` that Plex stops leaves every tag where it was.

        Nothing is cleared before the library is read, so "nothing was changed" is
        true for the tags too, and the command adds no line about cleared data.
        """
        plex = _FakePlex(
            _keys(LIBRARY_SIZE), stop=PlexReadInterrupted("tracks", read=22, listed=LIBRARY_SIZE)
        )
        before = _tag_rows(library)
        ai = _TaggingAI()

        result = _real_sync(library, plex, command=("sync", "regenerate"), ai=ai)

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert (
            "Plex stopped answering after 22 of 400 tracks were read, so nothing was changed."
            in out
        )
        assert "completed" not in out.lower()
        assert "already been cleared" not in out
        assert _tag_rows(library) == before
        assert [row[1] for row in before] == [f"tag-{key}" for key in _keys(LIBRARY_SIZE)]
        # The run never reached the tags: no request was sent and no run was opened.
        assert ai.requests == []
        assert not marker_path(library).exists()
        assert "regenerated" not in out

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
# sync regenerate
# ---------------------------------------------------------------------------

REGENERATE_COMMAND = "plexmix sync regenerate"
# The same command as a run of tags alone was started, which is what continues it.
REGENERATE_TAGS_COMMAND = "plexmix sync regenerate --no-embeddings"
LONG_AGO = datetime(2020, 1, 1, 9, 0, 0)
# A provider error that stops the tagging for good, and one that only loses a batch.
FATAL_ERROR = RuntimeError("401 Unauthorized")
PASSING_ERROR = ConnectionRefusedError("connection refused")


def _regenerate_settings(library: Path) -> Settings:
    """Settings for the regenerate tests: named providers and models, no side stages."""
    settings = _make_settings(library.parent)
    settings.musicbrainz.enabled = False
    settings.audio.analyze_on_sync = False
    settings.ai.default_provider = "openai"
    settings.ai.model = "gpt-test"
    settings.embedding.default_provider = VECTOR_MODEL
    settings.embedding.model = "embed-test"
    return settings


def _seed_embeddings(library: Path, model: str = VECTOR_MODEL) -> None:
    """Give every track an embedding of ``model`` that was written long ago."""
    with SQLiteManager(str(library)) as db:
        db.insert_embeddings_batch(
            [
                Embedding(
                    track_id=track.id,
                    embedding_model=model,
                    embedding_dim=len(OLD_VECTOR),
                    vector=OLD_VECTOR,
                    created_at=LONG_AGO,
                    updated_at=LONG_AGO,
                )
                for track in db.get_all_tracks()
                if track.id is not None
            ]
        )


def _vectors_by_track(library: Path, model: str = VECTOR_MODEL) -> Dict[int, List[float]]:
    rows = _query(
        library,
        f"SELECT track_id, vector FROM embeddings WHERE embedding_model = '{model}' "
        "ORDER BY track_id",
    )
    return {row[0]: json.loads(row[1]) for row in rows}


def _embedding_rows(library: Path) -> List[Tuple[Any, ...]]:
    return _query(
        library,
        "SELECT track_id, embedding_model, vector, updated_at FROM embeddings "
        "ORDER BY track_id, embedding_model",
    )


def _retagged(library: Path) -> List[int]:
    """The tracks that hold the fake provider's tags. Every other track holds its old ones."""
    done = []
    for track_id, tags, environments, instruments, written_at in _tag_rows(library):
        if tags == f"new-{track_id}":
            assert (environments, instruments) == ("focus", "piano")
            assert written_at is not None
            done.append(track_id)
        else:
            assert (tags, environments, instruments, written_at) == (
                f"tag-t{track_id - 1}",
                None,
                None,
                None,
            )
    return done


def _run_regenerate(
    library: Path,
    *args: str,
    plex: Optional[_FakePlex] = None,
    ai: Optional[_TaggingAI] = None,
    vectors: Optional[_Vectors] = None,
    refused: Optional[BaseException] = None,
    terminal: bool = False,
    answers: str = "",
    settings: Optional[Settings] = None,
) -> SimpleNamespace:
    """Run ``sync regenerate`` on the real engine and the temporary library.

    Plex and the two providers are fakes, and the provider check hands the fakes
    over, or raises ``refused`` (a refusal, or Ctrl+C). ``terminal`` says whether a question can be asked
    and ``answers`` is what the user types. The mocks in the result say how far the
    command got: ``check`` is the provider check, ``connect`` the Plex connection,
    ``load`` the settings, and ``build_ai`` the provider builder of the other sync
    commands, which this command never uses.
    """
    settings = settings or _regenerate_settings(library)
    plex = plex or _FakePlex(_keys(LIBRARY_SIZE))
    ai = ai or _TaggingAI()
    vectors = vectors or _Vectors()

    def checked(
        settings: Settings,
        need_ai: bool,
        need_embeddings: bool,
        ai_provider_name: Optional[str] = None,
    ) -> CheckedProviders:
        if refused is not None:
            raise refused
        return CheckedProviders(ai if need_ai else None, vectors if need_embeddings else None)

    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        load = stack.enter_context(
            patch("plexmix.cli.sync_cmd.Settings.load_from_file", return_value=settings)
        )
        connect = stack.enter_context(patch("plexmix.cli.sync_cmd.connect_plex", return_value=plex))
        build_ai = stack.enter_context(patch("plexmix.cli.sync_cmd._build_ai_provider"))
        check = stack.enter_context(
            patch("plexmix.cli.sync_cmd.check_providers", side_effect=checked)
        )
        # The regenerate question and the removal question ask the same terminal.
        stack.enter_context(patch("plexmix.cli.console_helpers.can_prompt", return_value=terminal))
        stack.enter_context(patch("plexmix.cli.sync_cmd.can_prompt", return_value=terminal))
        result = runner.invoke(app, ["sync", "regenerate", *args], input=answers)
    return SimpleNamespace(
        result=result,
        out=_flat(result.stdout),
        load=load,
        check=check,
        connect=connect,
        build_ai=build_ai,
        ai=ai,
        vectors=vectors,
    )


class TestSyncRegenerate:
    """``plexmix sync regenerate``: it asks, checks the providers, and replaces in place."""

    def test_refuses_without_terminal_and_without_yes(self, library: Path) -> None:
        _seed_embeddings(library)
        tags, embeddings = _tag_rows(library), _embedding_rows(library)

        run = _run_regenerate(library, terminal=False)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        # It names the flag and the whole command to run in its place.
        assert f"'{REGENERATE_COMMAND} --yes'" in run.out
        assert "no terminal to answer in" in run.out
        assert "Nothing was changed." in run.out
        assert "[y/N]" not in run.out
        assert SUCCESS_LINE not in run.out.lower()
        # It ended before it loaded anything: the one load is the entry point's own,
        # which reads the log level.
        assert run.load.call_count == 1
        run.check.assert_not_called()
        run.connect.assert_not_called()
        assert run.ai.requests == [] and run.vectors.requests == []
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert not marker_path(library).exists()
        assert _history(library) == []

    def test_yes_skips_the_question(self, library: Path) -> None:
        run = _run_regenerate(library, "--yes", "--no-embeddings", terminal=False)

        assert run.result.exit_code == 0, run.result.output
        assert "[y/N]" not in run.out
        assert "Operation cancelled" not in run.out
        # What the run is about to do is still said: the count and the provider.
        assert "This replaces the AI tags of all 400 tracks in the PlexMix library." in run.out
        assert "AI tags: OpenAI · gpt-test" in run.out
        assert "Regenerate sync completed successfully!" in run.out
        assert "AI tags: 400 tracks regenerated." in run.out
        assert len(_retagged(library)) == LIBRARY_SIZE
        assert run.ai.sent == list(range(1, LIBRARY_SIZE + 1))

    @pytest.mark.parametrize("typed", ["\n", "n\n"], ids=["enter", "n"])
    def test_declined_exits_zero_and_changes_nothing(self, library: Path, typed: str) -> None:
        _seed_embeddings(library)
        tags, embeddings = _tag_rows(library), _embedding_rows(library)

        run = _run_regenerate(library, terminal=True, answers=typed)

        assert run.result.exit_code == 0
        # The question names the count and both providers, and No is the default.
        question = "Regenerate the AI tags and embeddings of 400 tracks? [y/N]"
        assert question in run.out
        before_question = run.out.split(question)[0]
        assert (
            "This replaces the AI tags and embeddings of all 400 tracks in the PlexMix library."
            in before_question
        )
        assert "AI tags: OpenAI · gpt-test" in before_question
        assert "Embeddings: OpenAI · embed-test" in before_question
        assert "Nothing is cleared first" in before_question
        assert run.out.split(question)[1].strip().endswith("Operation cancelled.")
        # Declining is not a run: no provider was asked and nothing was written.
        run.check.assert_not_called()
        run.connect.assert_not_called()
        assert run.ai.requests == [] and run.vectors.requests == []
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert not marker_path(library).exists()
        assert _history(library) == []

    def test_yes_does_not_allow_large_removals(self, library: Path) -> None:
        """``--yes`` answers the regenerate question. The removal still needs its own flag."""
        run = _run_regenerate(
            library, "--yes", "--no-embeddings", plex=_FakePlex(_keys(100)), terminal=False
        )

        assert run.result.exit_code == 1
        assert "Plex no longer lists 300 of the 400 tracks" in run.out
        assert "300 tracks were kept." in run.out
        assert "--allow-removals" in run.out
        assert "Remove 300" not in run.out
        assert SUCCESS_LINE not in run.out.lower()
        assert _track_count(library) == LIBRARY_SIZE
        assert _backup_folders(library) == []

    def test_yes_reaches_the_engine_without_the_removal_flag(
        self, patched_sync: SimpleNamespace
    ) -> None:
        result = runner.invoke(app, ["sync", "regenerate", "--no-embeddings", "--yes"])

        assert result.exit_code == 0, result.output
        patched_sync.engine.regenerate_sync.assert_called_once()
        options = patched_sync.engine.regenerate_sync.call_args.kwargs
        assert options["allow_large_removals"] is False
        assert options["generate_embeddings"] is False
        # The run marker gets the provider and model that the question named.
        assert options["tags_provider"] == run_provider(patched_sync.settings, SCOPE_TAGS)
        assert options["embeddings_provider"] is None

    @pytest.mark.parametrize(
        "flags, need_embeddings", [((), True), (("--no-embeddings",), False)], ids=["both", "tags"]
    )
    def test_only_the_needed_providers_are_checked(
        self, patched_sync: SimpleNamespace, flags: Tuple[str, ...], need_embeddings: bool
    ) -> None:
        """Spec "Provider that is not needed": ``--no-embeddings`` asks no embedding provider."""
        generator = MagicMock()
        generator.get_dimension.return_value = 4
        patched_sync.check.return_value = CheckedProviders(
            MagicMock(), generator if need_embeddings else None
        )
        patched_sync.engine.regenerate_result = _regenerated(
            embeddings=5 if need_embeddings else None
        )

        with patch("plexmix.cli.sync_cmd._build_embedding_generator") as build_embeddings:
            result = runner.invoke(app, ["sync", "regenerate", "--yes", *flags])

        assert result.exit_code == 0, result.output
        patched_sync.check.assert_called_once_with(
            patched_sync.settings, need_ai=True, need_embeddings=need_embeddings
        )
        # The engine gets the providers that answered the check, and no other.
        build_embeddings.assert_not_called()
        built_with = patched_sync.engine_cls.call_args.args
        assert built_with[2] is (generator if need_embeddings else None)
        assert built_with[4] is patched_sync.check.return_value.ai_provider
        options = patched_sync.engine.regenerate_sync.call_args.kwargs
        expected = (
            run_provider(patched_sync.settings, SCOPE_EMBEDDINGS) if need_embeddings else None
        )
        assert options["embeddings_provider"] == expected

    def test_refuses_when_ai_provider_is_not_ready(self, library: Path) -> None:
        _seed_embeddings(library)
        tags, embeddings = _tag_rows(library), _embedding_rows(library)
        refused = RegenerateRefused(
            "OpenAI", CAUSE_NO_KEY, component=COMPONENT_AI, reason=REFUSED_NO_KEY
        )

        run = _run_regenerate(library, "--yes", refused=refused)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        # The provider and the cause, and what that means for the library.
        assert "Checking the AI provider and the embedding provider..." in run.out
        assert "The AI provider OpenAI is not ready: no API key is stored." in run.out
        assert "Nothing was changed." in run.out
        assert "Run 'plexmix config init' to store the key." in run.out
        # The run does not go on without AI tags, and it says nothing of the kind.
        assert "Continuing without AI assistance" not in run.out
        run.build_ai.assert_not_called()
        assert SUCCESS_LINE not in run.out.lower()
        assert "Starting regenerate sync" not in run.out
        run.connect.assert_not_called()
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert not marker_path(library).exists()
        assert _history(library) == []

    def test_refusal_of_the_embedding_provider_shows_its_detail_without_the_key(
        self, library: Path
    ) -> None:
        tags = _tag_rows(library)
        refused = RegenerateRefused(
            "OpenAI",
            CAUSE_NO_ANSWER,
            component=COMPONENT_EMBEDDINGS,
            reason=REFUSED_NO_ANSWER,
            detail="Error code: 401 - [redacted] was rejected",
            error=RuntimeError(f"Error code: 401 - {DUMMY_KEY} was rejected"),
        )

        run = _run_regenerate(library, "--yes", refused=refused)

        assert run.result.exit_code == 1
        assert (
            "The embedding provider OpenAI is not ready: it did not answer the test request. "
            "Error code: 401 - [redacted] was rejected." in run.out
        )
        # The provider's own error is kept for classification only. It is never printed.
        assert DUMMY_KEY not in _squeezed(run.result.output)
        assert _tag_rows(library) == tags
        assert run.ai.requests == []

    def test_interrupt_during_the_provider_check_changes_nothing(self, library: Path) -> None:
        tags = _tag_rows(library)

        run = _run_regenerate(library, "--yes", refused=KeyboardInterrupt())

        assert run.result.exit_code == 130
        assert "Interrupted during the provider check. Nothing was changed." in run.out
        run.connect.assert_not_called()
        assert _tag_rows(library) == tags
        assert not marker_path(library).exists()

    def test_question_for_a_library_without_a_database_creates_none(self, tmp_path: Path) -> None:
        """Looking at the library for the question must not write anything, not even a file."""
        database_file = tmp_path / "test.db"

        run = _run_regenerate(database_file, terminal=True, answers="n\n")

        assert run.result.exit_code == 0
        assert "The PlexMix library holds no tracks yet." in run.out
        assert "Continue? [y/N]" in run.out
        assert "0 tracks" not in run.out
        assert run.out.endswith("Operation cancelled.")
        assert list(tmp_path.iterdir()) == []

    def test_run_that_rebuilt_nothing_exits_nonzero(self, patched_sync: SimpleNamespace) -> None:
        patched_sync.engine.regenerate_result = _regenerated(tags=0, tags_kept=400)

        result = _invoke_sync(["sync", "regenerate"])

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "No AI tags were regenerated." in out
        assert "AI tags: 0 tracks regenerated, 400 keep their previous tags." in out
        assert SUCCESS_LINE not in out.lower()
        assert "completed" not in out.lower()
        # The library sync itself was done, and its counts are still shown.
        assert "Tracks added: 5" in out

    @pytest.mark.parametrize("command", [["sync"], ["sync", "regenerate"]])
    def test_sync_that_could_not_save_its_index_is_not_called_a_success(
        self, patched_sync: SimpleNamespace, command: List[str]
    ) -> None:
        """An index file that could not be loaded or moved aside is never written over.

        The run then saved no search index, so it must not end with the success line.
        """
        stuck = Path("/somewhere/test.index")
        patched_sync.engine.vector_index = SimpleNamespace(
            load_failed=True, moved_paths=[], unmoved_paths=[stuck]
        )

        result = _invoke_sync(command)

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "finished with warnings." in out
        assert SUCCESS_LINE not in out.lower()
        assert (
            "The search index was not saved in this run: a file that could not be loaded "
            "is still in its place. Move it away, then run 'plexmix embeddings generate'." in out
        )

    def test_healthy_index_keeps_the_success_line(self, patched_sync: SimpleNamespace) -> None:
        patched_sync.engine.vector_index = SimpleNamespace(
            load_failed=False, moved_paths=[], unmoved_paths=[]
        )

        result = _invoke_sync(["sync"])

        assert SUCCESS_LINE in result.output.lower()
        assert "was not saved" not in _flat(result.stdout)

    def test_run_whose_index_was_left_as_it_is_gets_no_success_line(
        self, patched_sync: SimpleNamespace
    ) -> None:
        """Every track was regenerated, and the last rebuild of the index was refused.

        The run did its work, so the status is 0. It ends without a rebuilt index,
        so it is not called a success, and the sentence says what to run.
        """
        done = _regenerated(tags=400, embeddings=400)
        done.index_left_out = 12
        patched_sync.engine.regenerate_result = done

        result = _invoke_sync(["sync", "regenerate"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "Regenerate sync finished with warnings." in out
        assert "AI tags: 400 tracks regenerated." in out
        assert "Embeddings: 400 tracks regenerated." in out
        assert _index_sentence(12) in out
        assert out.count("The search index was left as it is") == 1
        assert SUCCESS_LINE not in out.lower()
        assert "did not finish" not in out and "again to continue" not in out

    def test_stopped_run_says_nothing_of_the_index(self, patched_sync: SimpleNamespace) -> None:
        """After a stop, the stop is what is reported: the index was left without a word."""
        stopped = _regenerated(
            tags=400,
            embeddings=50,
            embeddings_kept=350,
            stopped_scope=SCOPE_EMBEDDINGS,
            error_message="Embedding generation stopped: Connection refused.",
        )
        stopped.index_left_out = 350
        patched_sync.engine.regenerate_result = stopped

        result = _invoke_sync(["sync", "regenerate"])

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "Embedding generation stopped: Connection refused." in out
        assert "The search index was left as it is" not in out
        assert f"Run '{REGENERATE_TAGS_COMMAND}' again to continue." in out

    @pytest.mark.parametrize("embeddings", [0, None], ids=["both", "tags alone"])
    def test_run_with_nothing_to_do_is_no_failure(
        self, patched_sync: SimpleNamespace, embeddings: Optional[int]
    ) -> None:
        """A library without tracks: the engine's run reached its end with no track to do."""
        patched_sync.engine.regenerate_result = _regenerated(tags=0, embeddings=embeddings)
        assert patched_sync.engine.regenerate_result.nothing_to_do is True

        result = _invoke_sync(["sync", "regenerate"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "The PlexMix library holds no tracks, so there is nothing to regenerate." in out
        assert "did not finish" not in out and "No AI tags" not in out
        assert "again to continue" not in out
        # No pass has a count to show.
        assert "tracks regenerated" not in out

    def test_refusal_without_a_terminal_names_the_command_as_it_was_run(
        self, patched_sync: SimpleNamespace
    ) -> None:
        with patch("plexmix.cli.console_helpers.can_prompt", return_value=False):
            result = runner.invoke(app, ["sync", "regenerate", "--no-embeddings"])

        assert result.exit_code == 1
        assert f"'{REGENERATE_TAGS_COMMAND} --yes'" in _flat(result.stdout)
        patched_sync.engine.regenerate_sync.assert_not_called()

    def test_engine_gets_the_keys_from_the_settings(self, patched_sync: SimpleNamespace) -> None:
        """Not every provider object shows its key, so the engine is handed them."""
        with patch("plexmix.cli.sync_cmd.provider_secrets", return_value=[DUMMY_KEY]) as secrets:
            for command in (["sync"], ["sync", "regenerate"]):
                patched_sync.engine_cls.reset_mock()
                result = _invoke_sync(command)
                assert result.exit_code == 0, result.output
                assert patched_sync.engine_cls.call_args.kwargs["known_secrets"] == [DUMMY_KEY]
        assert secrets.call_count == 2
        assert DUMMY_KEY not in result.output

    def test_run_without_a_result_is_not_a_success(self, patched_sync: SimpleNamespace) -> None:
        """Only a real result of the engine counts: without one nothing is called a success."""
        patched_sync.engine.regenerate_result = MagicMock()

        result = _invoke_sync(["sync", "regenerate"])

        assert result.exit_code == 1
        assert SUCCESS_LINE not in result.output.lower()

    def test_run_where_every_batch_fails_exits_nonzero(self, library: Path) -> None:
        """Spec "Every request fails": no tag changes, and the run is not a success.

        Each request fails with an error that is not fatal by itself. After three in
        a row the run stops asking: the provider no longer answers.
        """
        tags = _tag_rows(library)
        ai = _TaggingAI(error=PASSING_ERROR)

        run = _run_regenerate(library, "--yes", "--no-embeddings", ai=ai)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        # Three batches of 20 were asked for, and every track holds the tags it had.
        assert ai.sent == list(range(1, 61))
        assert _tag_rows(library) == tags
        assert (
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            "Last error: connection refused." in run.out
        )
        assert "AI tags: 0 tracks regenerated, 400 keep their previous tags." in run.out
        # The tip names the command as it was run: without the flag it would also
        # replace every embedding.
        assert f"Tip: Run '{REGENERATE_TAGS_COMMAND}' again to continue." in run.out
        assert SUCCESS_LINE not in run.out.lower()
        assert "completed" not in run.out.lower()
        assert _history(library) == ["partial"]

    @pytest.mark.parametrize("embeddings", [True, False], ids=["both", "tags alone"])
    def test_run_without_any_new_tag_exits_nonzero_and_makes_no_embedding(
        self, library: Path, embeddings: bool
    ) -> None:
        """The provider answers every request and gives nothing usable for any track.

        The embeddings are then left alone. Made anyway, each would come from the
        text it was made from, and the run would pass for one that did its work.
        """
        _seed_embeddings(library)
        tags, rows = _tag_rows(library), _embedding_rows(library)
        ai = _TaggingAI(omit=range(1, LIBRARY_SIZE + 1))
        flags = ["--yes"] if embeddings else ["--yes", "--no-embeddings"]

        run = _run_regenerate(library, *flags, ai=ai)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert ai.sent == list(range(1, LIBRARY_SIZE + 1))
        assert _tag_rows(library) == tags and _embedding_rows(library) == rows
        assert run.vectors.requests == []
        assert "Regenerate sync did not finish its work." in run.out
        assert "AI tags: 0 tracks regenerated, 400 keep their previous tags." in run.out
        if embeddings:
            assert "No AI tags were regenerated, so no embedding was regenerated either." in run.out
            assert "Embeddings: 0 tracks regenerated, 400 keep their previous embedding." in run.out
            assert f"Tip: Run '{REGENERATE_COMMAND}' again to continue." in run.out
        else:
            assert "No AI tags were regenerated." in run.out
            assert f"Tip: Run '{REGENERATE_TAGS_COMMAND}' again to continue." in run.out
        assert SUCCESS_LINE not in run.out.lower()
        assert _history(library) == ["partial"]

    def test_reply_without_tags_blanks_no_tags(self, library: Path) -> None:
        """A reply with environments and instruments and no tags for 30 of 400 tracks.

        Written as it came, those 30 tracks would end without tags, behind a success line.
        """
        _seed_embeddings(library)
        ai = _TaggingAI(no_tags=range(101, 131))

        run = _run_regenerate(library, "--yes", ai=ai)

        assert run.result.exit_code == 0, run.result.output
        assert "Regenerate sync finished with warnings." in run.out
        assert "AI tags: 370 tracks regenerated, 30 keep their previous tags." in run.out
        assert SUCCESS_LINE not in run.out.lower()
        rows = _tag_rows(library)
        assert all(row[1] for row in rows)
        # The 30 tracks hold every column they had (``_retagged`` checks the others).
        assert _retagged(library) == [n for n in range(1, 401) if not 101 <= n <= 130]

    def test_provider_that_stops_answering_exits_nonzero_and_can_be_continued(
        self, library: Path
    ) -> None:
        """Spec "Provider failure part-way", for an error that is not fatal by itself.

        Two requests are answered and every later one is refused. The run must not
        go on to the end, call its tags finished and make every embedding.
        """
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)
        ai = _TaggingAI(error=PASSING_ERROR, fail_from=3)

        first = _run_regenerate(library, "--yes", ai=ai)

        assert first.result.exit_code == 1
        assert isinstance(first.result.exception, SystemExit)
        assert "Regenerate sync did not finish its work." in first.out
        assert (
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            "Last error: connection refused." in first.out
        )
        assert "AI tags: 40 tracks regenerated, 360 keep their previous tags." in first.out
        assert "Embeddings: 0 tracks regenerated, 400 keep their previous embedding." in first.out
        assert f"Tip: Run '{REGENERATE_COMMAND}' again to continue." in first.out
        assert SUCCESS_LINE not in first.out.lower()
        # Two answered requests and three failed ones. Nothing was asked after them.
        assert ai.sent == list(range(1, 101))
        assert _retagged(library) == list(range(1, 41))
        assert _embedding_rows(library) == embeddings and first.vectors.requests == []
        assert _history(library) == ["partial"]
        assert marker_path(library).exists()

        second = _run_regenerate(library, terminal=True, answers="y\n")

        assert second.result.exit_code == 0, second.result.output
        assert "This run continues it: 360 of 400 tracks remain." in second.out
        assert second.ai.sent == list(range(41, LIBRARY_SIZE + 1))
        assert "Regenerate sync completed successfully!" in second.out
        assert len(_retagged(library)) == LIBRARY_SIZE
        assert not marker_path(library).exists()

    @pytest.mark.parametrize("stop", ["provider error", "interrupt"])
    def test_stopped_run_after_a_provider_change_keeps_the_saved_index(
        self, library: Path, stop: str
    ) -> None:
        """The saved index was made by an earlier provider and covers every track.

        The run is stopped after 50 new embeddings. An index of those 50 must not
        take the place of the saved one before a run has reached its end.
        """
        _seed_embeddings(library, model="gemini")
        _saved_index(library)
        before = _index_files(library)
        assert sorted(before) == ["test.index", "test.metadata"]
        error = KeyboardInterrupt() if stop == "interrupt" else None

        run = _run_regenerate(library, "--yes", vectors=_Vectors(fail_on_request=2, error=error))

        assert run.result.exit_code == (130 if stop == "interrupt" else 1)
        assert "Embeddings: 50 tracks regenerated, 350 keep their previous embedding." in run.out
        # Every earlier row is kept, 50 tracks have a new one, and the index files
        # are the ones from before the run, byte for byte.
        assert len(_vectors_by_track(library, model="gemini")) == LIBRARY_SIZE
        assert len(_vectors_by_track(library)) == 50
        assert _index_files(library) == before

    def test_run_stopped_after_a_size_change_keeps_the_index_and_is_continued_to_a_whole_one(
        self, library: Path
    ) -> None:
        """The vector size changed, and the embedding provider stops after 100 tracks.

        An index of those 100 must not take the place of the saved one. The same
        command continues the run, and only its end replaces the index: by one that
        holds every track in the new size.
        """
        before = _seed_earlier_size(library)

        first = _run_regenerate(library, "--yes", vectors=_Vectors(fail_on_request=3))

        assert first.result.exit_code == 1
        assert isinstance(first.result.exception, SystemExit)
        assert "Embeddings: 100 tracks regenerated, 300 keep their previous embedding." in first.out
        assert f"Tip: Run '{REGENERATE_COMMAND}' again to continue." in first.out
        assert SUCCESS_LINE not in first.out.lower()
        # The stop is what is reported. Nothing is said of the index, and it is untouched.
        assert "The search index was left as it is" not in first.out
        assert _embedding_sizes(library) == {4: 100, 8: 300}
        _assert_saved_index_is_untouched(library, before)
        assert marker_path(library).exists()

        second = _run_regenerate(library, "--yes")

        assert second.result.exit_code == 0, second.result.output
        assert "This run continues it: 300 of 400 tracks remain." in second.out
        assert second.ai.requests == [] and second.vectors.requests == [50] * 6
        assert "Regenerate sync completed successfully!" in second.out
        assert "The search index was left as it is" not in second.out
        assert _embedding_sizes(library) == {4: 400}
        index = VectorIndex(
            dimension=len(NEW_VECTOR), index_path=str(library.parent / "test.index")
        )
        assert index.dimension_mismatch is False
        assert sorted(index.track_ids) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()
        assert _history(library) == ["partial", "success"]

    def test_run_that_ends_with_tracks_in_the_way_saves_no_index_and_says_so(
        self, library: Path
    ) -> None:
        """The run reaches its end, and 40 tracks got no new embedding from the provider.

        They hold the earlier vector size, so an index of the rows that fit would
        lack them. The saved index stays as it is, the run ends with warnings and
        status 0, and a later run can still reach a whole index.
        """
        before = _seed_earlier_size(library)

        run = _run_regenerate(library, "--yes", vectors=_Vectors(short=5))

        assert run.result.exit_code == 0, run.result.output
        assert run.result.exception is None
        assert "Regenerate sync finished with warnings." in run.out
        assert "AI tags: 400 tracks regenerated." in run.out
        assert "Embeddings: 360 tracks regenerated, 40 keep their previous embedding." in run.out
        assert _index_sentence(40) in run.out
        assert run.out.count("The search index was left as it is") == 1
        assert SUCCESS_LINE not in run.out.lower()
        assert _embedding_sizes(library) == {4: 360, 8: 40}
        _assert_saved_index_is_untouched(library, before)
        rows = _query(library, "SELECT status, error_message FROM sync_history ORDER BY id")
        assert rows == [("partial", _index_sentence(40))]
        # The tags are done, and 40 embeddings are left: the embeddings entry of the
        # marker stays, so the next run makes only those. It does not start over for
        # every track and pay the providers for the whole library again.
        assert set(json.loads(marker_path(library).read_text())) == {"embeddings"}

        again = _run_regenerate(library, "--yes")

        assert again.result.exit_code == 0, again.result.output
        assert "Regenerate sync completed successfully!" in again.out
        assert again.ai.requests == []
        assert again.vectors.requests == [40]
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()

    def test_library_without_tracks_has_nothing_to_do(self, tmp_path: Path) -> None:
        """Real engine: no track in Plex and none in the library. No failure, no marker."""
        database_file = tmp_path / "test.db"
        with SQLiteManager(str(database_file)) as db:
            db.create_tables()

        for flags in (["--yes"], ["--yes", "--no-embeddings"]):
            run = _run_regenerate(database_file, *flags, plex=_FakePlex([]))

            assert run.result.exit_code == 0, run.result.output
            assert run.result.exception is None
            assert (
                "The PlexMix library holds no tracks, so there is nothing to regenerate." in run.out
            )
            assert "did not finish" not in run.out and "again to continue" not in run.out
            assert run.ai.requests == [] and run.vectors.requests == []
            assert not marker_path(database_file).exists()
        assert _history(database_file) == ["success", "success"]

    def test_provider_failure_part_way_exits_nonzero_with_tip(self, library: Path) -> None:
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)
        ai = _TaggingAI(error=FATAL_ERROR, fail_from=3)

        run = _run_regenerate(library, "--yes", ai=ai)

        assert run.result.exit_code == 1
        # The command caught the failure: there is no error for Typer to report.
        assert isinstance(run.result.exception, SystemExit)
        assert "Traceback" not in run.result.stdout
        assert "Regenerate sync did not finish its work." in run.out
        assert "AI tagging stopped:" in run.out
        assert "AI tags: 40 tracks regenerated, 360 keep their previous tags." in run.out
        # The embeddings wait for the tags, so every track keeps its embedding.
        assert "Embeddings: 0 tracks regenerated, 400 keep their previous embedding." in run.out
        assert f"Run '{REGENERATE_COMMAND}' again to continue." in run.out
        assert SUCCESS_LINE not in run.out.lower()
        assert "completed" not in run.out.lower()
        assert _retagged(library) == list(range(1, 41))
        assert _embedding_rows(library) == embeddings
        assert run.vectors.requests == []
        # The run can be continued: its marker entry is still there.
        assert marker_path(library).exists()

    def test_summary_states_regenerated_and_kept_counts(self, library: Path) -> None:
        """Spec "Part of the library regenerated": exit 0, both counts, no success line."""
        _seed_embeddings(library)
        ai = _TaggingAI(omit=[7, 8, 9])

        run = _run_regenerate(library, "--yes", ai=ai)

        assert run.result.exit_code == 0, run.result.output
        assert "Regenerate sync finished with warnings." in run.out
        assert "AI tags: 397 tracks regenerated, 3 keep their previous tags." in run.out
        assert "Embeddings: 400 tracks regenerated." in run.out
        assert SUCCESS_LINE not in run.out.lower()
        assert len(_retagged(library)) == LIBRARY_SIZE - 3
        assert set(map(tuple, _vectors_by_track(library).values())) == {tuple(NEW_VECTOR)}

    def test_complete_run_replaces_everything_and_says_so(self, library: Path) -> None:
        _seed_embeddings(library)
        _seed_embeddings(library, model="gemini")

        run = _run_regenerate(library, "--yes")

        assert run.result.exit_code == 0, run.result.output
        assert "Regenerate sync completed successfully!" in run.out
        assert "AI tags: 400 tracks regenerated." in run.out
        assert "Embeddings: 400 tracks regenerated." in run.out
        assert "keep" not in run.out.split("completed successfully!")[1]
        assert len(_retagged(library)) == LIBRARY_SIZE
        assert len(_vectors_by_track(library)) == LIBRARY_SIZE
        # Every track has its new embedding, so the rows of the other model are gone,
        # and the saved index holds the new vectors.
        assert _vectors_by_track(library, model="gemini") == {}
        index = VectorIndex(
            dimension=len(NEW_VECTOR), index_path=str(library.parent / "test.index")
        )
        assert len(index.track_ids) == LIBRARY_SIZE
        assert not marker_path(library).exists()
        assert _history(library) == ["success"]

    def test_continued_run_question_gives_remaining_count_and_start_over_hint(
        self, library: Path
    ) -> None:
        """Spec "Continuing": the second run says so, and sends only the tracks that are left."""
        first = _run_regenerate(
            library, "--yes", "--no-embeddings", ai=_TaggingAI(error=FATAL_ERROR, fail_from=3)
        )
        assert first.result.exit_code == 1
        assert _retagged(library) == list(range(1, 41))
        marker = marker_path(library)

        second = _run_regenerate(library, "--no-embeddings", terminal=True, answers="y\n")

        assert second.result.exit_code == 0, second.result.output
        question = "Continue with the remaining 360 tracks? [y/N]"
        assert question in second.out
        before_question = second.out.split(question)[0]
        assert (
            "An earlier regenerate run did not finish. This run continues it: "
            "360 of 400 tracks remain." in before_question
        )
        # How to start over: the marker file is named, in one piece.
        assert marker.name == "test.db.regenerate.json"
        assert (
            f"To start over with all 400 tracks, delete {marker} and run the command again."
            in second.result.stdout
        )
        assert second.ai.sent == list(range(41, LIBRARY_SIZE + 1))
        assert "AI tags: 360 tracks regenerated." in second.out
        assert "Regenerate sync completed successfully!" in second.out
        assert len(_retagged(library)) == LIBRARY_SIZE
        # The run reached its end, so nothing is left to continue.
        assert not marker.exists()

    def test_continued_tags_only_run_whose_remaining_tracks_get_nothing_ends_with_warnings(
        self, library: Path
    ) -> None:
        """The run it continues had replaced 40 tracks. The provider gives nothing for the rest.

        A run of tags alone used to end with status 1 ("No AI tags were regenerated")
        here, although the 40 were done, and its marker was cleared: the next run then
        started over for every track and paid the provider again. It ends with
        warnings and status 0 now, as a run of tags and embeddings does.
        """
        first = _run_regenerate(
            library, "--yes", "--no-embeddings", ai=_TaggingAI(error=FATAL_ERROR, fail_from=3)
        )
        assert first.result.exit_code == 1
        assert _retagged(library) == list(range(1, 41))
        assert marker_path(library).exists()

        second = _run_regenerate(
            library,
            "--yes",
            "--no-embeddings",
            ai=_TaggingAI(omit=range(41, LIBRARY_SIZE + 1)),
        )

        assert second.result.exit_code == 0, second.result.output
        assert second.ai.sent == list(range(41, LIBRARY_SIZE + 1))
        assert "Regenerate sync finished with warnings." in second.out
        assert (
            "AI tags: 0 tracks regenerated (the earlier run regenerated 40), "
            "360 keep their previous tags." in second.out
        )
        assert "did not finish its work" not in second.out
        assert SUCCESS_LINE not in second.out.lower()
        # The 360 keep their tags, the 40 have theirs, and nothing is left to continue.
        assert _retagged(library) == list(range(1, 41))
        assert not marker_path(library).exists()

        # The next run is a new one for every track, and it can reach its end.
        third = _run_regenerate(library, "--yes", "--no-embeddings")

        assert third.result.exit_code == 0, third.result.output
        assert third.ai.sent == list(range(1, LIBRARY_SIZE + 1))
        assert len(_retagged(library)) == LIBRARY_SIZE

    def test_run_stopped_in_its_embeddings_is_continued_without_new_tags(
        self, library: Path
    ) -> None:
        """The tags of the earlier run are done: the second run pays for no tag again."""
        _seed_embeddings(library)

        first = _run_regenerate(library, "--yes", vectors=_Vectors(fail_on_request=2))

        assert first.result.exit_code == 1
        assert isinstance(first.result.exception, SystemExit)
        assert (
            "Embedding generation stopped: the embedding provider stopped answering." in first.out
        )
        assert "AI tags: 400 tracks regenerated." in first.out
        assert "Embeddings: 50 tracks regenerated, 350 keep their previous embedding." in first.out
        assert f"Run '{REGENERATE_COMMAND}' again to continue." in first.out
        assert SUCCESS_LINE not in first.out.lower()
        # No track is without an embedding: 50 new ones, 350 previous ones.
        vectors = _vectors_by_track(library)
        assert len(vectors) == LIBRARY_SIZE
        assert sum(1 for vector in vectors.values() if vector == NEW_VECTOR) == 50

        second = _run_regenerate(library, terminal=True, answers="y\n")

        assert second.result.exit_code == 0, second.result.output
        assert "This run continues it: 350 of 400 tracks remain." in second.out
        assert (
            "That run had finished the AI tags, so only the embeddings that are left are made."
            in second.out
        )
        assert "Continue with the remaining 350 tracks? [y/N]" in second.out
        assert second.ai.requests == []
        assert second.vectors.requests == [50] * 7
        assert "AI tags: regenerated by the earlier run." in second.out
        assert "Embeddings: 350 tracks regenerated." in second.out
        assert "Regenerate sync completed successfully!" in second.out
        assert set(map(tuple, _vectors_by_track(library).values())) == {tuple(NEW_VECTOR)}
        assert not marker_path(library).exists()

    def test_new_run_does_not_speak_of_an_earlier_one(self, library: Path) -> None:
        run = _run_regenerate(library, "--no-embeddings", terminal=True, answers="n\n")

        assert "earlier" not in run.out
        assert "start over" not in run.out
        assert str(marker_path(library)) not in _squeezed(run.result.stdout)

    def test_changed_model_starts_over_and_says_nothing_of_the_earlier_run(
        self, library: Path
    ) -> None:
        """Spec "Provider or model changed since the interrupted run": a new run, all tracks."""
        _run_regenerate(
            library, "--yes", "--no-embeddings", ai=_TaggingAI(error=FATAL_ERROR, fail_from=3)
        )
        assert _retagged(library) == list(range(1, 41))
        settings = _regenerate_settings(library)
        settings.ai.model = "gpt-other"

        run = _run_regenerate(
            library, "--no-embeddings", terminal=True, answers="n\n", settings=settings
        )

        assert run.result.exit_code == 0
        assert "Regenerate the AI tags of 400 tracks? [y/N]" in run.out
        assert "AI tags: OpenAI · gpt-other" in run.out
        assert "earlier" not in run.out
        assert "Continue with" not in run.out

    def test_interrupt_tip_names_the_same_command(self, library: Path) -> None:
        """Spec "Tip after Ctrl+C" and "Ctrl+C during a regenerate sync"."""
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)
        ai = _TaggingAI(error=KeyboardInterrupt(), fail_from=3)

        run = _run_regenerate(library, "--yes", ai=ai)

        assert run.result.exit_code == 130
        assert "Sync interrupted by user." in run.out
        assert "AI tags: 40 tracks regenerated, 360 keep their previous tags." in run.out
        assert "Embeddings: 0 tracks regenerated, 400 keep their previous embedding." in run.out
        assert "Every track that was not regenerated keeps its previous data." in run.out
        assert f"Tip: Run '{REGENERATE_COMMAND}' again to continue." in run.out
        assert "clears nothing" in run.out
        # Not the plain sync, which would leave the regenerate unfinished.
        assert "Run 'plexmix sync' again" not in run.out
        assert SUCCESS_LINE not in run.out.lower()
        # Old and new side by side: no track is without tags or without an embedding.
        assert _retagged(library) == list(range(1, 41))
        assert all(row[1] for row in _tag_rows(library))
        assert _embedding_rows(library) == embeddings
        assert _history(library) == ["interrupted"]

    def test_interrupt_while_the_index_is_rebuilt_says_the_rows_are_saved(
        self, library: Path
    ) -> None:
        """Ctrl+C after the last embedding was saved, while the search index is rebuilt.

        Every track has its new tags and its new embedding, and the run marker is
        cleared, so nothing is left to continue: the same command would start over
        with every track and pay the providers again. The tip said to run it again.
        Only the index is behind, and the plain embeddings command rebuilds it.
        """
        _seed_embeddings(library)

        with patch("plexmix.plex.sync.rebuild_index", side_effect=KeyboardInterrupt()):
            run = _run_regenerate(library, "--yes")

        assert run.result.exit_code == 130
        assert "Sync interrupted by user." in run.out
        assert "AI tags: 400 tracks regenerated." in run.out
        assert "Embeddings: 400 tracks regenerated." in run.out
        assert (
            "The new tags and embeddings are saved. "
            "Run 'plexmix embeddings generate' to rebuild the search index." in run.out
        )
        assert "again to continue" not in run.out
        assert "keeps its previous data" not in run.out
        assert SUCCESS_LINE not in run.out.lower()
        # Every track has its new tags and embedding, and nothing is left to continue.
        assert len(_retagged(library)) == LIBRARY_SIZE
        assert not marker_path(library).exists()
        assert _history(library) == ["interrupted"]

    def test_error_after_both_passes_ended_names_the_command_that_rebuilds_the_index(
        self, patched_sync: SimpleNamespace
    ) -> None:
        error = RuntimeError("the index could not be saved")
        patched_sync.engine.outcome = SyncOutcome(library_total=400, compared=True)
        patched_sync.engine.regenerate_result = _regenerated(tags=400, embeddings=400)
        patched_sync.engine.regenerate_sync.side_effect = error

        result = _invoke_sync(["sync", "regenerate"])

        assert result.exception is error
        out = _flat(result.stdout)
        assert "AI tags: 400 tracks regenerated." in out
        assert "Embeddings: 400 tracks regenerated." in out
        assert "Run 'plexmix embeddings generate' to rebuild the search index." in out
        assert "again to continue" not in out

    def test_interrupt_with_a_mocked_engine_still_gives_the_tip(
        self, patched_sync: SimpleNamespace
    ) -> None:
        patched_sync.engine.regenerate_result = _regenerated(tags=2400, tags_kept=3543)
        patched_sync.engine.regenerate_sync.side_effect = KeyboardInterrupt()

        result = _invoke_sync(["sync", "regenerate"])

        out = _flat(result.stdout)
        assert result.exit_code == 130
        assert "AI tags: 2,400 tracks regenerated, 3,543 keep their previous tags." in out
        # ``_invoke_sync`` runs the command with ``--no-embeddings``, and the tip says so.
        assert f"Run '{REGENERATE_TAGS_COMMAND}' again to continue." in out

    def test_failed_run_still_asks_about_a_held_removal(
        self, patched_sync: SimpleNamespace
    ) -> None:
        """A failed regenerate does not swallow the removal question, and still exits 1."""
        patched_sync.engine.outcome = _held_outcome()
        patched_sync.engine.regenerate_result = _regenerated(
            tags=2400,
            tags_kept=3543,
            stopped_scope=SCOPE_TAGS,
            error_message="AI tagging stopped: The provider rejected the API key.",
        )

        result = _invoke_sync(["sync", "regenerate"], terminal=True, answers="n\n")

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "AI tagging stopped: The provider rejected the API key." in out
        assert "AI tags: 2,400 tracks regenerated, 3,543 keep their previous tags." in out
        assert "Remove 5,443 of 5,943 tracks? [y/N]" in out
        assert "5,443 tracks were kept." in out
        assert SUCCESS_LINE not in out.lower()
        patched_sync.engine.remove_unlisted_tracks.assert_not_called()

    def test_error_of_a_later_stage_says_what_was_regenerated(
        self, patched_sync: SimpleNamespace
    ) -> None:
        error = RuntimeError("the database is locked")
        patched_sync.engine.outcome = SyncOutcome(library_total=400, added=1, compared=True)
        patched_sync.engine.regenerate_result = _regenerated(
            tags=40, tags_kept=360, unfinished=True
        )
        patched_sync.engine.regenerate_sync.side_effect = error

        result = _invoke_sync(["sync", "regenerate"])

        # The error itself is still reported. What the run had saved is said first.
        assert result.exception is error
        out = _flat(result.stdout)
        assert "Saved before the sync stopped:" in out
        assert "AI tags: 40 tracks regenerated, 360 keep their previous tags." in out
        assert f"Run '{REGENERATE_TAGS_COMMAND}' again to continue." in out
        assert SUCCESS_LINE not in out.lower()

    def test_error_after_both_passes_ended_gives_no_tip_to_continue(
        self, patched_sync: SimpleNamespace
    ) -> None:
        """Nothing is left to continue then, and the same command would start over."""
        error = RuntimeError("the index could not be saved")
        patched_sync.engine.outcome = SyncOutcome(library_total=400, compared=True)
        patched_sync.engine.regenerate_result = _regenerated(tags=400)
        patched_sync.engine.regenerate_sync.side_effect = error

        result = _invoke_sync(["sync", "regenerate"])

        assert result.exception is error
        out = _flat(result.stdout)
        assert "AI tags: 400 tracks regenerated." in out
        assert "again to continue" not in out
        assert SUCCESS_LINE not in out.lower()

    def test_engine_refusal_is_printed_not_raised(self, patched_sync: SimpleNamespace) -> None:
        """The engine's own guard ends the command with the refusal, not with an error report."""
        patched_sync.engine.regenerate_sync.side_effect = RegenerateRefused.not_given(COMPONENT_AI)

        result = _invoke_sync(["sync", "regenerate"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        out = _flat(result.stdout)
        assert "The AI provider is not ready: none was given to this run." in out
        assert "Nothing was changed." in out

    def test_other_sync_commands_still_go_on_without_an_ai_provider(
        self, patched_sync: SimpleNamespace
    ) -> None:
        """Only ``sync regenerate`` lost the "Continuing without AI assistance" path."""
        with patch("plexmix.cli.sync_cmd._build_ai_provider", return_value=None) as build_ai:
            plain = _invoke_sync(["sync"])
            regenerate = _invoke_sync(["sync", "regenerate"])

        assert plain.exit_code == 0, plain.output
        assert "Continuing without AI assistance" in _flat(plain.stdout)
        assert regenerate.exit_code == 0, regenerate.output
        assert "Continuing without AI assistance" not in _flat(regenerate.stdout)
        # One call, by the plain sync. The regenerate takes its provider from the check.
        build_ai.assert_called_once()
        patched_sync.check.assert_called_once()

    def test_help_lists_yes(self) -> None:
        result = runner.invoke(app, ["sync", "regenerate", "--help"])

        assert result.exit_code == 0
        # The plain text: a coloured help page splits the option name with colour codes.
        text = _flat(click.unstyle(result.output))
        assert "--yes" in text
        assert "--allow-removals" in text
        assert "Nothing is cleared first" in text


# ---------------------------------------------------------------------------
# plain sync with embeddings, on the real engine
# ---------------------------------------------------------------------------


def _plain_sync_with_embeddings(
    library: Path,
    plex: _FakePlex,
    vectors: Optional[_Vectors] = None,
    command: Iterable[str] = ("sync",),
) -> SimpleNamespace:
    """Run a plain sync with its embeddings stage on the real engine.

    Plex and the embedding provider are fakes, and there is no AI provider, so no
    tags are made. Nobody can be asked a question.
    """
    vectors = vectors or _Vectors()
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.sync_cmd.Settings.load_from_file",
                return_value=_regenerate_settings(library),
            )
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.connect_plex", return_value=plex))
        stack.enter_context(patch("plexmix.cli.sync_cmd._build_ai_provider", return_value=None))
        stack.enter_context(
            patch("plexmix.cli.sync_cmd._build_embedding_generator", return_value=vectors)
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.can_prompt", return_value=False))
        result = runner.invoke(app, list(command))
    return SimpleNamespace(result=result, out=_flat(result.stdout), vectors=vectors)


class TestPlainSyncAndTheSearchIndex:
    """A plain sync never saves an index that covers only part of the library."""

    @pytest.mark.parametrize(
        "command",
        [("sync",), ("sync", "incremental"), ("sync", "full")],
        ids=["sync", "incremental", "full"],
    )
    def test_sync_after_a_size_change_leaves_the_saved_index_and_says_so(
        self, library: Path, command: Tuple[str, ...]
    ) -> None:
        """The vector size was changed, and Plex lists 20 tracks PlexMix does not have.

        The sync embeds those 20 in the new size. An index of them alone would take
        the place of the index of the whole library, and with it would go the
        dimension mismatch that tells ``plexmix create`` to stop. So nothing is
        built: the sync says what it did, with a warning, and ends with status 0.
        """
        before = _seed_earlier_size(library)

        run = _plain_sync_with_embeddings(library, _FakePlex(_keys(420)), command=command)

        assert run.result.exit_code == 0, run.result.output
        assert run.result.exception is None
        assert "Incremental sync finished with warnings." in run.out
        assert "Tracks added: 20" in run.out
        assert (
            "The search index was left as it is: 400 tracks hold an embedding that was made "
            "with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
            "to replace them." in run.out
        )
        assert run.out.count("The search index was left as it is") == 1
        assert SUCCESS_LINE not in run.out.lower()
        assert "completed" not in run.out.lower()
        # The 20 new tracks were embedded, and no other track was sent again.
        assert run.vectors.requests == [20]
        assert _track_count(library) == 420
        assert _embedding_sizes(library) == {4: 20, 8: 400}
        _assert_saved_index_is_untouched(library, before)
        rows = _query(library, "SELECT status, error_message FROM sync_history ORDER BY id")
        assert rows == [("partial", _index_sentence(400))]

    def test_sync_with_one_vector_size_updates_the_index_and_says_success(
        self, library: Path
    ) -> None:
        """The healthy path is what it was: the new vectors join the saved index."""
        _seed_embeddings(library)
        index_path = library.parent / "test.index"
        index = VectorIndex(dimension=len(OLD_VECTOR), index_path=str(index_path))
        index.build_index([OLD_VECTOR] * LIBRARY_SIZE, list(range(1, LIBRARY_SIZE + 1)))
        index.save_index(str(index_path))

        run = _plain_sync_with_embeddings(library, _FakePlex(_keys(420)))

        assert run.result.exit_code == 0, run.result.output
        assert "Incremental sync completed successfully!" in run.out
        assert "Tracks added: 20" in run.out
        assert "The search index was left as it is" not in run.out
        assert "finished with warnings" not in run.out
        assert run.vectors.requests == [20]
        assert _ids_in_saved_index(library) == list(range(1, 421))
        assert _history(library) == ["success"]

    def test_sync_after_a_regenerate_that_replaced_every_track_is_a_success_again(
        self, library: Path
    ) -> None:
        """No state that no command leaves: the regenerate command makes the index whole."""
        _seed_earlier_size(library)
        warned = _plain_sync_with_embeddings(library, _FakePlex(_keys(420)))
        assert "finished with warnings" in warned.out

        regenerated = _run_embeddings_regenerate(library, "--yes")

        assert regenerated.result.exit_code == 0, regenerated.result.output
        assert "✓ Embeddings regenerated successfully!" in regenerated.out
        assert "Vector index saved with 420 total embeddings" in regenerated.out
        assert _ids_in_saved_index(library) == list(range(1, 421))

        run = _plain_sync_with_embeddings(library, _FakePlex(_keys(430)))

        assert run.result.exit_code == 0, run.result.output
        assert "Incremental sync completed successfully!" in run.out
        assert "The search index was left as it is" not in run.out
        assert _ids_in_saved_index(library) == list(range(1, 431))


# ---------------------------------------------------------------------------
# embeddings generate
# ---------------------------------------------------------------------------

EMBEDDINGS_COMMAND = "plexmix embeddings generate --regenerate"


def _run_embeddings_regenerate(
    library: Path,
    *args: str,
    vectors: Optional[_Vectors] = None,
    refused: Optional[BaseException] = None,
    terminal: bool = False,
    answers: str = "",
) -> SimpleNamespace:
    """Run ``embeddings generate --regenerate`` on the temporary library.

    The embedding provider is a fake, and the provider check hands it over, or
    raises ``refused`` (a refusal, or Ctrl+C). ``terminal`` says whether the question can be asked and
    ``answers`` is what the user types. The mocks in the result say how far the
    command got: ``check`` is the provider check, ``load`` the settings, and
    ``build`` the provider builder of the plain command, which a regenerate never uses.
    """
    settings = _regenerate_settings(library)
    vectors = vectors or _Vectors()

    def checked(
        settings: Settings,
        need_ai: bool,
        need_embeddings: bool,
        ai_provider_name: Optional[str] = None,
    ) -> CheckedProviders:
        if refused is not None:
            raise refused
        assert (need_ai, need_embeddings) == (False, True)
        return CheckedProviders(None, vectors)

    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        load = stack.enter_context(
            patch("plexmix.cli.embeddings_cmd.Settings.load_from_file", return_value=settings)
        )
        check = stack.enter_context(
            patch("plexmix.cli.embeddings_cmd.check_providers", side_effect=checked)
        )
        build = stack.enter_context(
            patch("plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason")
        )
        # The stored key, so that a provider error that repeats it can be checked.
        stack.enter_context(
            patch("plexmix.cli.embeddings_cmd.resolve_embedding_api_key", return_value=DUMMY_KEY)
        )
        stack.enter_context(patch("plexmix.cli.console_helpers.can_prompt", return_value=terminal))
        result = runner.invoke(
            app, ["embeddings", "generate", "--regenerate", *args], input=answers
        )
    return SimpleNamespace(
        result=result,
        out=_flat(result.stdout),
        load=load,
        check=check,
        build=build,
        vectors=vectors,
    )


def _run_plain_embeddings(library: Path, vectors: Optional[_Vectors] = None) -> SimpleNamespace:
    """Run the plain ``embeddings generate`` on the temporary library with a fake provider."""
    vectors = vectors or _Vectors()
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.embeddings_cmd.Settings.load_from_file",
                return_value=_regenerate_settings(library),
            )
        )
        stack.enter_context(
            patch(
                "plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason",
                return_value=(vectors, None),
            )
        )
        result = runner.invoke(app, ["embeddings", "generate"])
    return SimpleNamespace(result=result, out=_flat(result.stdout), vectors=vectors)


def _new_embeddings(library: Path) -> List[int]:
    """The tracks whose embedding is a new one. Every other track must hold its old one."""
    done = []
    for track_id, vector in _vectors_by_track(library).items():
        if vector == NEW_VECTOR:
            done.append(track_id)
        else:
            assert vector == OLD_VECTOR
    return done


def _ids_in_saved_index(library: Path) -> List[int]:
    index = VectorIndex(dimension=len(NEW_VECTOR), index_path=str(library.parent / "test.index"))
    return sorted(index.track_ids)


class TestEmbeddingsGenerate:
    """Tests for `plexmix embeddings generate`."""

    @patch("plexmix.cli.embeddings_cmd.rebuild_vector_index", return_value=1)
    @patch("plexmix.cli.embeddings_cmd.SQLiteManager")
    @patch("plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason")
    @patch("plexmix.cli.embeddings_cmd.Settings.load_from_file")
    def test_no_tracks_need_embeddings(
        self, mock_load, mock_build_emb, mock_db_cls, mock_rebuild, tmp_path
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_gen = MagicMock()
        mock_gen.get_dimension.return_value = 1536
        mock_gen.provider_name = "OpenAI"
        mock_build_emb.return_value = (mock_gen, None)

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

    def test_plain_generate_with_two_vector_sizes_leaves_the_saved_index(
        self, library: Path
    ) -> None:
        """A regenerate run was stopped after the vector size changed: 100 tracks hold
        the new size and 300 the earlier one.

        Replaces the check of an index built from the rows that fit. Such an index
        would hold 100 of 400 tracks and would hide the dimension mismatch. The plain
        command builds nothing then, says how many tracks are in the way and what
        to run, and claims neither a rebuilt index nor that every track is embedded.
        """
        before = _seed_earlier_size(library)
        with SQLiteManager(str(library)) as db:
            db.insert_embeddings_batch(
                [
                    Embedding(
                        track_id=track_id,
                        embedding_model=VECTOR_MODEL,
                        embedding_dim=len(NEW_VECTOR),
                        vector=NEW_VECTOR,
                    )
                    for track_id in range(1, 101)
                ]
            )
        assert _embedding_sizes(library) == {4: 100, 8: 300}

        run = _run_plain_embeddings(library)

        assert run.result.exit_code == 0, run.result.output
        assert run.result.exception is None
        assert "Found 0 tracks without embeddings" in run.out
        assert (
            "The search index was left as it is: 300 tracks hold an embedding that was made "
            "with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
            "to replace them." in run.out
        )
        assert run.out.count("The search index was left as it is") == 1
        assert "All tracks already have embeddings!" not in run.out
        assert "Vector index rebuilt" not in run.out and "Vector index saved" not in run.out
        assert "✓" not in run.out
        assert run.vectors.requests == []
        assert _embedding_sizes(library) == {4: 100, 8: 300}
        _assert_saved_index_is_untouched(library, before)

    def test_plain_generate_that_embeds_new_tracks_beside_another_size_leaves_the_index(
        self, library: Path
    ) -> None:
        """The size changed, and 100 tracks have no embedding yet.

        The command makes those 100, and says so. It does not say that the index
        was saved: 300 tracks still hold the earlier size, so nothing was built.
        """
        before = _seed_earlier_size(library, track_ids=range(1, 301))

        run = _run_plain_embeddings(library)

        assert run.result.exit_code == 0, run.result.output
        assert "Found 100 tracks without embeddings" in run.out
        assert "✓ Successfully generated 100 embeddings!" in run.out
        assert _index_sentence(300) in run.out
        assert "Vector index saved" not in run.out and "Vector index rebuilt" not in run.out
        assert run.vectors.requests == [50, 50]
        assert _embedding_sizes(library) == {4: 100, 8: 300}
        _assert_saved_index_is_untouched(library, before)

    def test_plain_generate_with_one_vector_size_rebuilds_the_index(self, library: Path) -> None:
        """The healthy path is what it was: a lost index is rebuilt from the rows."""
        _seed_embeddings(library)
        assert _index_files(library) == {}

        run = _run_plain_embeddings(library)

        assert run.result.exit_code == 0, run.result.output
        assert "All tracks already have embeddings!" in run.out
        assert "✓ Vector index rebuilt with 400 embeddings." in run.out
        assert "Use --regenerate to regenerate all embeddings." in run.out
        assert "The search index was left as it is" not in run.out
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))

    def test_plain_generate_without_any_embedding_claims_no_index(self, tmp_path: Path) -> None:
        """A library without tracks has no row to build an index from, and none is written."""
        database_file = tmp_path / "test.db"
        with SQLiteManager(str(database_file)) as db:
            db.create_tables()

        run = _run_plain_embeddings(database_file)

        assert run.result.exit_code == 0, run.result.output
        assert "Found 0 tracks without embeddings" in run.out
        assert "Vector index" not in run.out
        assert _index_files(database_file) == {}

    def test_plain_generate_says_so_when_no_index_could_be_saved(self, library: Path) -> None:
        _seed_embeddings(library)
        index_path = library.parent / "test.index"
        index_path.write_bytes(b"this is not a search index")

        with patch(
            "plexmix.database.vector_index.os.rename", side_effect=PermissionError(13, "denied")
        ):
            run = _run_plain_embeddings(library)

        assert run.result.exit_code == 0, run.result.output
        assert index_path.read_bytes() == b"this is not a search index"
        assert "The search index was not saved in this run" in run.out
        assert "Vector index rebuilt" not in run.out and "Vector index saved" not in run.out

    @patch("plexmix.cli.embeddings_cmd.rebuild_vector_index", return_value=2)
    @patch("plexmix.cli.embeddings_cmd.generate_embeddings_for_tracks", return_value=2)
    @patch("plexmix.cli.embeddings_cmd.build_vector_index")
    @patch("plexmix.cli.embeddings_cmd.SQLiteManager")
    @patch("plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason")
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
        mock_build_emb.return_value = (mock_gen, None)

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

    @patch(
        "plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason",
        return_value=(None, None),
    )
    @patch("plexmix.cli.embeddings_cmd.Settings.load_from_file")
    def test_no_embedding_provider_exits(self, mock_load, mock_build_emb, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        result = runner.invoke(app, ["embeddings", "generate"])
        assert result.exit_code == 1
        assert "api key required" in result.output.lower()

    # -- embeddings generate --regenerate: real database, fake provider --------

    def test_regenerate_asks_first(self, library: Path) -> None:
        """Spec "Embeddings regenerate asks": the count and the provider, before any work."""
        _seed_embeddings(library)

        run = _run_embeddings_regenerate(library, terminal=True, answers="y\n")

        assert run.result.exit_code == 0, run.result.output
        question = "Regenerate the embeddings of 400 tracks? [y/N]"
        assert question in run.out
        before_question, after_question = run.out.split(question)
        assert (
            "This replaces the embeddings of all 400 tracks in the PlexMix library."
            in before_question
        )
        assert "Embeddings: OpenAI · embed-test" in before_question
        assert "Nothing is cleared first" in before_question
        # The provider is asked only after the answer.
        assert "Checking the embedding provider" in after_question
        assert "Checking" not in before_question
        run.check.assert_called_once()
        run.build.assert_not_called()
        # Every embedding was replaced in place, in batches, and the run was closed.
        assert run.vectors.requests == [50] * 8
        assert _new_embeddings(library) == list(range(1, LIBRARY_SIZE + 1))
        assert "✓ Embeddings regenerated successfully!" in run.out
        assert "Embeddings: 400 tracks regenerated." in run.out
        assert "Vector index saved with 400 total embeddings" in run.out
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()

    @pytest.mark.parametrize("typed", ["\n", "n\n"], ids=["enter", "n"])
    def test_regenerate_declined_changes_nothing(self, library: Path, typed: str) -> None:
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)

        run = _run_embeddings_regenerate(library, terminal=True, answers=typed)

        assert run.result.exit_code == 0
        assert "Regenerate the embeddings of 400 tracks? [y/N]" in run.out
        assert run.out.endswith("Operation cancelled.")
        # Declining is not a run: no provider was asked and nothing was written.
        run.check.assert_not_called()
        assert run.vectors.requests == []
        assert _embedding_rows(library) == embeddings
        assert not marker_path(library).exists()
        assert not (library.parent / "test.index").exists()

    def test_regenerate_without_terminal_refuses(self, library: Path) -> None:
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)

        run = _run_embeddings_regenerate(library, terminal=False)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert f"'{EMBEDDINGS_COMMAND} --yes'" in run.out
        assert "no terminal to answer in" in run.out
        assert "[y/N]" not in run.out
        # It ended before it loaded anything: the one load is the entry point's own.
        assert run.load.call_count == 1
        run.check.assert_not_called()
        assert _embedding_rows(library) == embeddings
        assert not marker_path(library).exists()

    def test_regenerate_with_yes_needs_no_terminal(self, library: Path) -> None:
        """Spec "Unattended run with the flag"."""
        _seed_embeddings(library)

        run = _run_embeddings_regenerate(library, "--yes", terminal=False)

        assert run.result.exit_code == 0, run.result.output
        assert "[y/N]" not in run.out
        assert "This replaces the embeddings of all 400 tracks" in run.out
        assert _new_embeddings(library) == list(range(1, LIBRARY_SIZE + 1))

    def test_regenerate_refuses_when_provider_is_not_ready(self, library: Path) -> None:
        """Spec "Embedding provider cannot be reached": all embeddings unchanged, exit 1."""
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)
        refused = RegenerateRefused(
            "OpenAI",
            CAUSE_NO_ANSWER,
            component=COMPONENT_EMBEDDINGS,
            reason=REFUSED_NO_ANSWER,
            detail="Connection error",
        )

        run = _run_embeddings_regenerate(library, "--yes", refused=refused)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert (
            "The embedding provider OpenAI is not ready: it did not answer the test request. "
            "Connection error." in run.out
        )
        assert "Nothing was changed." in run.out
        assert "successfully" not in run.out
        assert "Regenerating embeddings" not in run.out
        assert run.vectors.requests == []
        assert _embedding_rows(library) == embeddings
        assert not marker_path(library).exists()
        assert not (library.parent / "test.index").exists()

    def test_regenerate_interrupt_during_the_provider_check_changes_nothing(
        self, library: Path
    ) -> None:
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)

        run = _run_embeddings_regenerate(library, "--yes", refused=KeyboardInterrupt())

        assert run.result.exit_code == 130
        assert "Interrupted during the provider check. Nothing was changed." in run.out
        assert _embedding_rows(library) == embeddings
        assert not marker_path(library).exists()

    def test_regenerate_provider_failure_keeps_every_embedding(self, library: Path) -> None:
        """Spec "Provider fails part-way": new and previous embeddings, never a gap."""
        _seed_embeddings(library)
        error = RuntimeError(
            f"Error code: 401 - key {DUMMY_KEY} was rejected by "
            f"http://leakuser:{DUMMY_PASSWORD}@127.0.0.1:9/v1"
        )

        run = _run_embeddings_regenerate(
            library, "--yes", vectors=_Vectors(fail_on_request=2, error=error)
        )

        assert run.result.exit_code == 1
        # The command caught the failure: there is no error for Typer to report.
        assert isinstance(run.result.exception, SystemExit)
        assert "Traceback" not in run.result.stdout
        # Every track still has an embedding: 50 new ones and 350 previous ones.
        assert len(_vectors_by_track(library)) == LIBRARY_SIZE
        assert _new_embeddings(library) == list(range(1, 51))
        assert (
            "Embedding generation stopped: Error code: 401 - key [redacted] was rejected by "
            "http://***@127.0.0.1:9/v1." in run.out
        )
        assert "Embeddings: 50 tracks regenerated, 350 keep their previous embedding." in run.out
        assert f"Tip: Run '{EMBEDDINGS_COMMAND}' again to continue." in run.out
        assert "successfully" not in run.out
        shown = _squeezed(run.result.output)
        assert DUMMY_KEY not in shown
        assert DUMMY_PASSWORD not in shown
        # The index follows the rows, and the run can be continued.
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert marker_path(library).exists()

    @pytest.mark.parametrize("stop", ["provider error", "interrupt"])
    @pytest.mark.parametrize("earlier", ["another model", "another size"])
    def test_regenerate_stopped_after_a_provider_change_keeps_the_saved_index(
        self, library: Path, stop: str, earlier: str
    ) -> None:
        """The saved index covers all 400 tracks and was made before the change.

        The run stops after 50 embeddings. An index of those 50 must not replace it.
        ``another size``: the rows of the earlier size hold 8 numbers and the provider
        makes 4 now (the fixture of the first version of this test held 4 numbers in
        every row, which is no size change at all).
        """
        if earlier == "another model":
            # Rows and index of an earlier provider, with the same vector size.
            _seed_embeddings(library, model="gemini")
            index = VectorIndex(
                dimension=len(OLD_VECTOR), index_path=str(library.parent / "test.index")
            )
            index.build_index([OLD_VECTOR] * LIBRARY_SIZE, list(range(1, LIBRARY_SIZE + 1)))
            index.save_index(str(library.parent / "test.index"))
            before = _index_files(library)
        else:
            # Rows and index of the earlier vector size (a dimension mismatch now).
            before = _seed_earlier_size(library)
        error = KeyboardInterrupt() if stop == "interrupt" else None

        run = _run_embeddings_regenerate(
            library, "--yes", vectors=_Vectors(fail_on_request=2, error=error)
        )

        assert run.result.exit_code == (130 if stop == "interrupt" else 1)
        assert "Embeddings: 50 tracks regenerated, 350 keep their previous embedding." in run.out
        # The stop is what is reported: no word about the index, and no success line.
        assert "The search index was left as it is" not in run.out
        assert "Vector index" not in run.out and "successfully" not in run.out
        assert _index_files(library) == before
        assert marker_path(library).exists()
        if earlier == "another model":
            assert len(_vectors_by_track(library)) == 50
            assert len(_vectors_by_track(library, model="gemini")) == LIBRARY_SIZE
        else:
            assert _embedding_sizes(library) == {4: 50, 8: 350}
            _assert_saved_index_is_untouched(library, before)

    def test_regenerate_stopped_after_a_size_change_is_continued_to_a_whole_index(
        self, library: Path
    ) -> None:
        """The same command continues the stopped run, and its end replaces the index."""
        before = _seed_earlier_size(library)
        first = _run_embeddings_regenerate(library, "--yes", vectors=_Vectors(fail_on_request=3))
        assert first.result.exit_code == 1
        _assert_saved_index_is_untouched(library, before)

        second = _run_embeddings_regenerate(library, "--yes")

        assert second.result.exit_code == 0, second.result.output
        assert "This run continues it: 300 of 400 tracks remain." in second.out
        assert second.vectors.requests == [50] * 6
        assert "✓ Embeddings regenerated successfully!" in second.out
        assert "Vector index saved with 400 total embeddings" in second.out
        assert "The search index was left as it is" not in second.out
        assert _embedding_sizes(library) == {4: 400}
        index = VectorIndex(
            dimension=len(NEW_VECTOR), index_path=str(library.parent / "test.index")
        )
        assert index.dimension_mismatch is False
        assert sorted(index.track_ids) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()

    def test_regenerate_that_ends_with_tracks_in_the_way_saves_no_index_and_says_so(
        self, library: Path
    ) -> None:
        """The run reaches its end, and the provider left 40 tracks out of its replies.

        They hold the earlier vector size, so the last rebuild of the index is
        refused. Like ``sync regenerate``: no success line, the sentence with the
        count and the command, and status 0, because the run did not fail.
        """
        before = _seed_earlier_size(library)

        run = _run_embeddings_regenerate(library, "--yes", vectors=_Vectors(short=5))

        assert run.result.exit_code == 0, run.result.output
        assert run.result.exception is None
        assert "Embeddings regenerated with warnings." in run.out
        assert "Embeddings: 360 tracks regenerated, 40 keep their previous embedding." in run.out
        assert _index_sentence(40) in run.out
        assert run.out.count("The search index was left as it is") == 1
        assert "successfully" not in run.out
        assert "Vector index saved" not in run.out and "✓" not in run.out
        assert _embedding_sizes(library) == {4: 360, 8: 40}
        _assert_saved_index_is_untouched(library, before)
        # The 40 tracks the provider left out are still to do, so the marker stays and
        # the same command makes only their embeddings. It does not start over for
        # every track and pay the provider for the whole library again.
        assert marker_path(library).exists()

        again = _run_embeddings_regenerate(library, "--yes")

        assert again.result.exit_code == 0, again.result.output
        assert again.vectors.requests == [40]
        assert "✓ Embeddings regenerated successfully!" in again.out
        assert "Vector index saved with 400 total embeddings" in again.out
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()

    def test_regenerate_continued_run_whose_remaining_tracks_get_no_vector_ends_with_warnings(
        self, library: Path
    ) -> None:
        """The run it continues had replaced 360 tracks. The provider returns no vector for 40.

        Each later run continues with those 40 only, ends with warnings and status 0,
        and leaves the saved index as it is. It used to end with status 1 ("No
        embeddings were regenerated") every time. A provider that answers ends the run.
        """
        before = _seed_earlier_size(library)
        first = _run_embeddings_regenerate(library, "--yes", vectors=_Vectors(short=5))
        assert first.result.exit_code == 0, first.result.output
        assert marker_path(library).exists()

        for _ in range(2):
            run = _run_embeddings_regenerate(library, "--yes", vectors=_Vectors(short=40))

            assert run.result.exit_code == 0, run.result.output
            assert run.vectors.requests == [40]
            assert "Embeddings regenerated with warnings." in run.out
            assert (
                "Embeddings: 0 tracks regenerated (the earlier run regenerated 360), "
                "40 keep their previous embedding." in run.out
            )
            assert _index_sentence(40) in run.out
            assert "No embeddings were regenerated" not in run.out
            assert "successfully" not in run.out and "Vector index saved" not in run.out
            assert marker_path(library).exists()
            assert _embedding_sizes(library) == {4: 360, 8: 40}
            _assert_saved_index_is_untouched(library, before)

        done = _run_embeddings_regenerate(library, "--yes")

        assert done.result.exit_code == 0, done.result.output
        assert done.vectors.requests == [40]
        assert "✓ Embeddings regenerated successfully!" in done.out
        assert _embedding_sizes(library) == {4: 400}
        assert not marker_path(library).exists()

    def test_regenerate_of_every_track_with_one_track_in_the_way_is_not_called_a_success(
        self, library: Path
    ) -> None:
        """All 400 tracks are replaced, and one track still holds only another model's row.

        A sync beside the run added it (here: while the first request was out). No
        track of the run kept its previous embedding, and the index is still not
        whole, so the run ends with warnings and without a "saved" line.
        """
        _seed_embeddings(library)
        index_path = library.parent / "test.index"
        index = VectorIndex(dimension=len(OLD_VECTOR), index_path=str(index_path))
        index.build_index([OLD_VECTOR] * LIBRARY_SIZE, list(range(1, LIBRARY_SIZE + 1)))
        index.save_index(str(index_path))
        before = _index_files(library)

        def a_sync_adds_a_track(request: int) -> None:
            if request != 1:
                return
            with SQLiteManager(str(library)) as other:
                track_id = other.insert_track(
                    Track(plex_key="late", title="Late", artist_id=1, album_id=1, play_count=0)
                )
                other.insert_embedding(
                    Embedding(
                        track_id=track_id,
                        embedding_model="gemini",
                        embedding_dim=len(OLD_VECTOR),
                        vector=OLD_VECTOR,
                    )
                )

        run = _run_embeddings_regenerate(
            library, "--yes", vectors=_Vectors(before_reply=a_sync_adds_a_track)
        )

        assert run.result.exit_code == 0, run.result.output
        assert "Embeddings regenerated with warnings." in run.out
        assert "Embeddings: 400 tracks regenerated." in run.out
        assert (
            "The search index was left as it is: 1 track holds an embedding that was made "
            "with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
            "to replace it." in run.out
        )
        assert "successfully" not in run.out and "Vector index saved" not in run.out
        assert _new_embeddings(library) == list(range(1, LIBRARY_SIZE + 1))
        # The row of the other model is kept: its track has no replacement yet.
        assert len(_vectors_by_track(library, model="gemini")) == 1
        assert _index_files(library) == before

    def test_regenerate_reads_the_result_of_the_last_rebuild(self, library: Path) -> None:
        """The command asks the one rebuild for its result, and builds nothing itself."""
        _seed_embeddings(library)

        def refused(db: Any, vector_index: Any, model: str, run: Any = None) -> int:
            vector_index.last_rebuild = IndexRebuild(left_out=12)
            return 0

        with patch("plexmix.cli.embeddings_cmd.finish_embeddings_pass", side_effect=refused):
            run = _run_embeddings_regenerate(library, "--yes")

        assert run.result.exit_code == 0, run.result.output
        assert "Embeddings regenerated with warnings." in run.out
        assert "Embeddings: 400 tracks regenerated." in run.out
        assert _index_sentence(12) in run.out
        assert "successfully" not in run.out and "Vector index saved" not in run.out
        assert _index_files(library) == {}

    def test_regenerate_says_so_when_no_index_could_be_saved(self, library: Path) -> None:
        """An index file that cannot be loaded or moved aside is left alone, not written over.

        The run then saves no index, and must not say "Vector index saved".
        """
        _seed_embeddings(library)
        index_path = library.parent / "test.index"
        index_path.write_bytes(b"this is not a search index")

        with patch(
            "plexmix.database.vector_index.os.rename", side_effect=PermissionError(13, "denied")
        ):
            run = _run_embeddings_regenerate(library, "--yes")

        assert run.result.exit_code == 0, run.result.output
        assert len(_new_embeddings(library)) == LIBRARY_SIZE
        assert index_path.read_bytes() == b"this is not a search index"
        assert "Could not be moved aside, so left where it is:" in run.out
        assert "Embeddings regenerated with warnings." in run.out
        assert "Embeddings: 400 tracks regenerated." in run.out
        assert "The search index was not saved in this run" in run.out
        assert "Vector index saved" not in run.out
        assert "successfully" not in run.out

    def test_regenerate_that_writes_nothing_exits_nonzero(self, library: Path) -> None:
        """Spec "Nothing regenerated": the first request fails, so no row is new."""
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)

        run = _run_embeddings_regenerate(library, "--yes", vectors=_Vectors(fail_on_request=1))

        assert run.result.exit_code == 1
        assert "Embedding generation stopped: the embedding provider stopped answering." in run.out
        assert "Embeddings: 0 tracks regenerated, 400 keep their previous embedding." in run.out
        assert "successfully" not in run.out
        assert _embedding_rows(library) == embeddings

    def test_regenerate_continued_run_question_gives_remaining_count(self, library: Path) -> None:
        """Spec "Continuing": the question says so, and only the tracks that are left are sent."""
        _seed_embeddings(library)
        first = _run_embeddings_regenerate(library, "--yes", vectors=_Vectors(fail_on_request=2))
        assert first.result.exit_code == 1
        marker = marker_path(library)

        second = _run_embeddings_regenerate(library, terminal=True, answers="y\n")

        assert second.result.exit_code == 0, second.result.output
        question = "Continue with the remaining 350 tracks? [y/N]"
        assert question in second.out
        before_question = second.out.split(question)[0]
        assert (
            "An earlier regenerate run did not finish. This run continues it: "
            "350 of 400 tracks remain." in before_question
        )
        # How to start over: the marker file is named, in one piece.
        assert (
            f"To start over with all 400 tracks, delete {marker} and run the command again."
            in second.result.stdout
        )
        assert second.vectors.requests == [50] * 7
        assert "Embeddings: 350 tracks regenerated." in second.out
        assert "✓ Embeddings regenerated successfully!" in second.out
        assert _new_embeddings(library) == list(range(1, LIBRARY_SIZE + 1))
        # The run reached its end, so nothing is left to continue.
        assert not marker.exists()

    def test_regenerate_interrupt_tip_continues(self, library: Path) -> None:
        _seed_embeddings(library)

        run = _run_embeddings_regenerate(
            library, "--yes", vectors=_Vectors(fail_on_request=2, error=KeyboardInterrupt())
        )

        assert run.result.exit_code == 130
        assert "Interrupted." in run.out
        assert "Embeddings: 50 tracks regenerated, 350 keep their previous embedding." in run.out
        assert "Every track that was not regenerated keeps its previous data." in run.out
        assert f"Tip: Run '{EMBEDDINGS_COMMAND}' again to continue." in run.out
        # Not the plain command, which would leave the other 350 as they are.
        assert "Run 'plexmix embeddings generate' again" not in run.out
        assert len(_vectors_by_track(library)) == LIBRARY_SIZE
        assert _new_embeddings(library) == list(range(1, 51))
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert marker_path(library).exists()

    def test_regenerate_drops_other_models_only_when_every_track_is_done(
        self, library: Path
    ) -> None:
        """A row of another model goes only once its track has a new embedding."""
        _seed_embeddings(library, model="gemini")

        first = _run_embeddings_regenerate(library, "--yes", vectors=_Vectors(fail_on_request=2))

        assert first.result.exit_code == 1
        # 50 tracks have a row of the new model. Every track still has its old one.
        assert len(_vectors_by_track(library)) == 50
        assert len(_vectors_by_track(library, model="gemini")) == LIBRARY_SIZE

        second = _run_embeddings_regenerate(library, "--yes")

        assert second.result.exit_code == 0, second.result.output
        assert second.vectors.requests == [50] * 7
        assert len(_vectors_by_track(library)) == LIBRARY_SIZE
        assert _vectors_by_track(library, model="gemini") == {}
        assert "Removed 400 embeddings of other models." in second.out

    @pytest.mark.parametrize("tracks", [None, 0], ids=["no-database", "no-tracks"])
    def test_regenerate_with_an_empty_library_has_nothing_to_do(
        self, tmp_path: Path, tracks: Optional[int]
    ) -> None:
        """A library without tracks is a run with nothing to do, not a failed run.

        Replaces the check of status 1: nothing is asked, no provider is asked,
        nothing is written, and the command ends with status 0.
        """
        database_file = tmp_path / "test.db"
        if tracks is not None:
            with SQLiteManager(str(database_file)) as db:
                db.create_tables()
        files = sorted(path.name for path in tmp_path.iterdir())

        run = _run_embeddings_regenerate(database_file, "--yes")

        assert run.result.exit_code == 0, run.result.output
        assert "The PlexMix library holds no tracks, so there is nothing to regenerate." in run.out
        assert "Run 'plexmix sync' first." in run.out
        assert "[y/N]" not in run.out and "successfully" not in run.out
        run.check.assert_not_called()
        assert run.vectors.requests == []
        # Looking at a library that does not exist yet did not create one, and no
        # run marker was left behind.
        assert sorted(path.name for path in tmp_path.iterdir()) == files
        assert not marker_path(database_file).exists()

    def test_generate_as_a_function_never_regenerates(self, library: Path) -> None:
        """A caller that leaves the options out passes placeholder objects, not True."""
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)
        settings = _regenerate_settings(library)

        with ExitStack() as stack:
            stack.enter_context(
                patch("plexmix.cli.embeddings_cmd.Settings.load_from_file", return_value=settings)
            )
            stack.enter_context(
                patch(
                    "plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason",
                    return_value=(_Vectors(), None),
                )
            )
            regenerate = stack.enter_context(
                patch("plexmix.cli.embeddings_cmd._regenerate_embeddings")
            )
            embeddings_cmd.embeddings_generate()

        regenerate.assert_not_called()
        assert _embedding_rows(library) == embeddings

    def test_plain_generate_asks_nothing_and_embeds_only_what_is_missing(
        self, library: Path
    ) -> None:
        """Without ``--regenerate`` the command is what it was: no question, nothing replaced."""
        _seed_embeddings(library)
        with SQLiteManager(str(library)) as db:
            db.get_connection().execute("DELETE FROM embeddings WHERE track_id > 300")
            db._commit()
        settings = _regenerate_settings(library)
        vectors = _Vectors()

        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch("plexmix.cli.embeddings_cmd.Settings.load_from_file", return_value=settings)
            )
            stack.enter_context(
                patch(
                    "plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason",
                    return_value=(vectors, None),
                )
            )
            check = stack.enter_context(patch("plexmix.cli.embeddings_cmd.check_providers"))
            stack.enter_context(patch("plexmix.cli.console_helpers.can_prompt", return_value=False))
            result = runner.invoke(app, ["embeddings", "generate"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, result.output
        assert "[y/N]" not in out
        check.assert_not_called()
        assert "Found 100 tracks without embeddings" in out
        assert "Successfully generated 100 embeddings!" in out
        assert "Vector index saved with 400 total embeddings" in out
        assert vectors.requests == [50, 50]
        assert _new_embeddings(library) == list(range(301, LIBRARY_SIZE + 1))
        # The plain command opens no run.
        assert not marker_path(library).exists()

    def test_plain_generate_keeps_its_interrupt_tip(self, library: Path) -> None:
        settings = _regenerate_settings(library)
        vectors = _Vectors(fail_on_request=2, error=KeyboardInterrupt())

        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch("plexmix.cli.embeddings_cmd.Settings.load_from_file", return_value=settings)
            )
            stack.enter_context(
                patch(
                    "plexmix.cli.embeddings_cmd._build_embedding_generator_with_reason",
                    return_value=(vectors, None),
                )
            )
            result = runner.invoke(app, ["embeddings", "generate"])

        out = _flat(result.stdout)
        assert result.exit_code == 130
        assert "Run 'plexmix embeddings generate' again to continue." in out
        assert "--regenerate" not in out

    def test_help_lists_yes(self) -> None:
        result = runner.invoke(app, ["embeddings", "generate", "--help"])

        assert result.exit_code == 0
        # The plain text: a coloured help page splits the option name with colour codes.
        text = _flat(click.unstyle(result.output))
        assert "--yes" in text
        assert "--regenerate" in text


# ---------------------------------------------------------------------------
# tags generate
# ---------------------------------------------------------------------------


def _run_tags_generate(
    library: Path,
    *args: str,
    ai: Optional[_TaggingAI] = None,
    vectors: Optional[_Vectors] = None,
    tag_generator: Any = None,
    embedding_failure: Optional[ProviderFailure] = None,
) -> SimpleNamespace:
    """Run ``tags generate`` on the temporary library with fake providers.

    ``tag_generator`` replaces the real ``TagGenerator``, for results that did not
    come through its parser. ``embedding_failure`` makes the embedding provider fail
    to start for the follow-up embedding refresh.
    """
    ai = ai or _TaggingAI()
    vectors = vectors or _Vectors()
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.tags_cmd.Settings.load_from_file",
                return_value=_regenerate_settings(library),
            )
        )
        stack.enter_context(
            patch("plexmix.cli.tags_cmd._resolve_ai_api_key", return_value=DUMMY_KEY)
        )
        stack.enter_context(patch("plexmix.cli.tags_cmd._build_ai_provider", return_value=ai))
        stack.enter_context(
            patch(
                "plexmix.cli.tags_cmd._build_embedding_generator_with_reason",
                return_value=(None, embedding_failure) if embedding_failure else (vectors, None),
            )
        )
        if tag_generator is not None:
            stack.enter_context(
                patch("plexmix.cli.tags_cmd.TagGenerator", return_value=tag_generator)
            )
        result = runner.invoke(app, ["tags", "generate", "--provider", "openai", *args])
    return SimpleNamespace(result=result, out=_flat(result.stdout), ai=ai, vectors=vectors)


def _untag(library: Path, first: int, last: int) -> None:
    """Take the tags of the tracks ``first`` to ``last`` away, as if they were never tagged."""
    with SQLiteManager(str(library)) as db:
        db.get_connection().execute(
            "UPDATE tracks SET tags = NULL WHERE id BETWEEN ? AND ?", (first, last)
        )
        db._commit()


class TestTagsGenerate:
    """Tests for `plexmix tags generate`."""

    def test_tags_after_a_size_change_are_saved_and_the_index_is_left_as_it_is(
        self, library: Path
    ) -> None:
        """The vector size was changed, and 30 tracks have no tags yet.

        Their tags are saved and their embeddings are made in the new size. An index
        of those 30 must not take the place of the index of all 400, so nothing is
        built, and the command says so. It printed nothing about that before.
        """
        before = _seed_earlier_size(library)
        _untag(library, 201, 230)

        run = _run_tags_generate(library)

        assert run.result.exit_code == 0, run.result.output
        assert run.result.exception is None
        assert "Updated 30 tracks with tags!" in run.out
        assert "Regenerated 30 embeddings with tags!" in run.out
        assert (
            "The search index was left as it is: 370 tracks hold an embedding that was made "
            "with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
            "to replace them." in run.out
        )
        assert run.out.count("The search index was left as it is") == 1
        # The tags are saved: the 30 tracks hold the provider's tags.
        assert run.ai.sent == list(range(201, 231))
        tagged = {row[0]: row[1:4] for row in _tag_rows(library) if 201 <= row[0] <= 230}
        assert tagged == {n: (f"new-{n}", "focus", "piano") for n in range(201, 231)}
        assert _embedding_sizes(library) == {4: 30, 8: 370}
        _assert_saved_index_is_untouched(library, before)

    def test_tags_with_one_vector_size_rebuild_the_index(self, library: Path) -> None:
        """The healthy path is what it was: the index is rebuilt from the rows."""
        _seed_embeddings(library)
        _untag(library, 201, 230)

        run = _run_tags_generate(library)

        assert run.result.exit_code == 0, run.result.output
        assert "Updated 30 tracks with tags!" in run.out
        assert "Regenerated 30 embeddings with tags!" in run.out
        assert "The search index was left as it is" not in run.out
        assert run.vectors.requests == [30]
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))

    def test_a_list_of_blanks_is_never_written_and_never_counted(self, library: Path) -> None:
        """Results that did not come through the tag generator's parser are cleaned too.

        ``[""]``, ``[" "]`` and ``[None]`` hold no tag. Joined as they are, they would
        write an empty text or a comma over a column and count the track as tagged.
        """
        _untag(library, 1, 4)
        with SQLiteManager(str(library)) as db:
            db.get_connection().execute(
                "UPDATE tracks SET environments = 'relax', instruments = 'piano' WHERE id = 1"
            )
            db._commit()
        replies = {
            1: {"tags": [""], "environments": [" "], "instruments": [None]},
            2: {"tags": ["", "rock "], "environments": [""], "instruments": [" ", "guitar"]},
            3: ["", "calm"],
            4: {"tags": ["", ""], "environments": [None], "instruments": [{"a": 1}]},
        }
        generator = SimpleNamespace(generate_tags_batch=lambda batch, batch_size=20: replies)

        run = _run_tags_generate(library, "--no-regenerate-embeddings", tag_generator=generator)

        assert run.result.exit_code == 0, run.result.output
        assert "Updated 2 tracks with tags!" in run.out
        rows = {row[0]: row[1:] for row in _tag_rows(library) if row[0] <= 4}
        # Tracks 1 and 4 got nothing: no column changed and no time was stamped.
        assert rows[1] == (None, "relax", "piano", None)
        assert rows[4] == (None, None, None, None)
        # Tracks 2 and 3 got the items that have content, and only those.
        assert rows[2][:3] == ("rock", None, "guitar") and rows[2][3] is not None
        assert rows[3][:3] == ("calm", None, None) and rows[3][3] is not None
        assert run.vectors.requests == []

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

    @patch("plexmix.cli.tags_cmd._build_embedding_generator_with_reason", return_value=(None, None))
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
    @patch("plexmix.cli.create_cmd._build_embedding_generator_with_reason")
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
        mock_build_emb.return_value = (mock_emb, None)

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
    @patch("plexmix.cli.create_cmd._build_embedding_generator_with_reason")
    @patch("plexmix.cli.create_cmd.Settings.load_from_file")
    def test_no_tracks_found_exits(
        self, mock_load, mock_build_emb, mock_db_cls, mock_build_vi, mock_gen_cls, tmp_path
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_emb = MagicMock()
        mock_emb.get_dimension.return_value = 1536
        mock_build_emb.return_value = (mock_emb, None)

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
    @patch(
        "plexmix.cli.create_cmd._build_embedding_generator_with_reason", return_value=(None, None)
    )
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

    def test_create_after_a_size_change_names_the_command_that_replaces_the_embeddings(
        self, library: Path
    ) -> None:
        """The saved index was made with another vector size, and ``create`` stops there.

        It named ``plexmix sync incremental --embeddings``, which embeds only tracks
        without an embedding and so cannot replace vectors of another size.
        """
        before = _seed_earlier_size(library)

        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch(
                    "plexmix.cli.create_cmd.Settings.load_from_file",
                    return_value=_regenerate_settings(library),
                )
            )
            stack.enter_context(
                patch(
                    "plexmix.cli.create_cmd._build_embedding_generator_with_reason",
                    return_value=(_Vectors(), None),
                )
            )
            result = runner.invoke(
                app,
                ["create", "calm evening", "--limit", "5", "--name", "Calm", "--no-create-in-plex"],
            )

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "Embedding dimension mismatch!" in out
        assert "Existing embeddings are 8D but current provider 'openai' uses 4D." in out
        assert "You must regenerate embeddings to use the new provider:" in out
        assert "plexmix embeddings generate --regenerate" in out
        assert "sync incremental" not in out
        # No playlist was made from an index of another size, and the index is as it was.
        assert _query(library, "SELECT COUNT(*) FROM playlists") == [(0,)]
        _assert_saved_index_is_untouched(library, before)

    def test_create_reports_a_moved_index(self, library: Path) -> None:
        """Spec "Truncated metadata file": both files are kept under dated names, and the
        output says so and names the command that rebuilds the index."""
        _seed_embeddings(library)
        embeddings = _embedding_rows(library)
        folder = library.parent
        index_path = folder / "test.index"
        metadata_path = index_path.with_suffix(".metadata")
        index = VectorIndex(dimension=len(OLD_VECTOR), index_path=str(index_path))
        index.build_index([OLD_VECTOR, OLD_VECTOR], [1, 2])
        index.save_index(str(index_path))
        index_file = index_path.read_bytes()
        # The metadata file was cut short, as by a save that did not finish.
        cut_short = metadata_path.read_bytes()[:10]
        metadata_path.write_bytes(cut_short)

        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch(
                    "plexmix.cli.create_cmd.Settings.load_from_file",
                    return_value=_regenerate_settings(library),
                )
            )
            stack.enter_context(
                patch(
                    "plexmix.cli.create_cmd._build_embedding_generator_with_reason",
                    return_value=(_Vectors(), None),
                )
            )
            result = runner.invoke(
                app,
                ["create", "calm evening", "--limit", "5", "--name", "Calm", "--no-create-in-plex"],
            )

        out = _flat(result.stdout)
        assert "The search index could not be loaded." in out
        assert "The stored embeddings are not affected." in out
        assert "To rebuild the search index from them, run 'plexmix embeddings generate'." in out
        # Nothing was deleted: both files are there under a name that holds the date
        # and the time, with what they held, and the output names each in one piece.
        moved_index, moved_metadata = sorted(folder.glob("test.*.unreadable-*"))
        assert (moved_index.read_bytes(), moved_metadata.read_bytes()) == (index_file, cut_short)
        for moved, name in ((moved_index, "test.index"), (moved_metadata, "test.metadata")):
            assert moved.name.startswith(f"{name}.unreadable-")
            datetime.strptime(moved.name.split(".unreadable-")[1], "%Y%m%d-%H%M%S")
            assert str(moved) in result.stdout
        assert not index_path.exists() and not metadata_path.exists()
        assert _embedding_rows(library) == embeddings
        # The index is empty until it is rebuilt, so there is nothing to choose from.
        # The command says that after the notice, and it does not end in an error report.
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert out.index("could not be loaded") < out.index("No tracks found matching criteria.")


# ---------------------------------------------------------------------------
# The embedding provider cannot start: the commands name the real cause
# ---------------------------------------------------------------------------

LOCAL_MISSING_PACKAGE = ProviderFailure(
    Cause.MISSING_DEPENDENCY,
    "local",
    package="sentence-transformers",
    detail="sentence-transformers not installed. Run: pip install sentence-transformers",
)
LOCAL_LOAD_FAILED = ProviderFailure(
    Cause.MODEL_LOAD_FAILED,
    "local",
    detail="Failed to load local model all-MiniLM-L6-v2: the worker stopped",
)


class TestEmbeddingFailureLines:
    """``embedding_failure_lines``: the text a command prints when the provider cannot start."""

    @pytest.mark.parametrize(
        "failure",
        [None, ProviderFailure(Cause.MISSING_API_KEY, "openai")],
        ids=["no-failure-object", "missing-api-key"],
    )
    def test_no_failure_and_a_missing_key_give_the_two_legacy_lines(
        self, failure: Optional[ProviderFailure]
    ) -> None:
        assert embedding_failure_lines(failure) == [
            "API key required for embedding provider.",
            "Run: plexmix config init",
        ]

    def test_a_local_model_that_could_not_load_is_named(self) -> None:
        lines = embedding_failure_lines(LOCAL_LOAD_FAILED)
        text = "\n".join(lines)

        assert "couldn't load" in text
        assert "API key" not in text
        assert lines[1] == LOCAL_LOAD_FAILED.detail
        assert "plexmix config init" in lines[-1]

    def test_missing_local_models_name_how_to_install_them(self) -> None:
        lines = embedding_failure_lines(LOCAL_MISSING_PACKAGE)
        text = "\n".join(lines)

        assert "Local models aren't installed" in text
        assert "plexmix[local]" in text
        assert LOCAL_REMEDY in lines[-1]
        assert "choose another embedding provider: plexmix config init" in lines[-1]
        assert "API key" not in text

    def test_a_detail_is_passed_through_unchanged(self) -> None:
        detail = "The endpoint answered 503 [Errno 32] (twice)"
        failure = ProviderFailure(Cause.UNKNOWN, "custom", detail=detail)

        lines = embedding_failure_lines(failure)

        assert lines[1] == detail
        assert len(lines) == 3
        assert lines[-1] == "Check the embedding settings: plexmix config init"

    def test_no_detail_leaves_out_the_detail_line(self) -> None:
        lines = embedding_failure_lines(ProviderFailure(Cause.MISSING_CONFIG, "custom"))

        assert len(lines) == 2
        assert "API key" not in "\n".join(lines)

    @pytest.mark.parametrize("provider", ["local", "openai"])
    @pytest.mark.parametrize("cause", [c for c in Cause if c != Cause.MISSING_API_KEY])
    def test_only_a_missing_key_says_that_a_key_is_required(
        self, cause: Cause, provider: str
    ) -> None:
        lines = embedding_failure_lines(ProviderFailure(cause, provider, detail="it broke"))

        assert all(lines)
        assert "API key required" not in "\n".join(lines)
        # Plain text: Rich markup is added where the line is printed.
        assert not any("[/" in line or line.startswith("[") for line in lines)

    def test_printing_escapes_the_square_brackets_of_a_line(self) -> None:
        from io import StringIO

        from rich.console import Console

        failure = ProviderFailure(Cause.MISSING_DEPENDENCY, "local", detail="[Errno 32] [bold]x")
        out = StringIO()

        print_embedding_failure(Console(file=out, width=300, color_system=None), failure)

        text = out.getvalue()
        assert "plexmix[local]" in text
        assert "[Errno 32] [bold]x" in text


EMBEDDING_COMMANDS = [
    pytest.param(
        ["create", "calm evening", "--limit", "5", "--name", "Calm"], "create", id="create"
    ),
    pytest.param(["embeddings", "generate"], "embeddings", id="embeddings-generate"),
]


def _run_with_failed_provider(
    tmp_path: Path, module: str, args: List[str], failure: Optional[ProviderFailure]
) -> Result:
    """Run a command whose embedding provider cannot start, with the settings file patched."""
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                f"plexmix.cli.{module}_cmd.Settings.load_from_file",
                return_value=_make_settings(tmp_path),
            )
        )
        stack.enter_context(
            patch(
                f"plexmix.cli.{module}_cmd._build_embedding_generator_with_reason",
                return_value=(None, failure),
            )
        )
        return runner.invoke(app, args)


class TestEmbeddingProviderCannotStart:
    """The four commands print the cause of the failure, and say "API key" only for a key."""

    @pytest.mark.parametrize(("args", "module"), EMBEDDING_COMMANDS)
    def test_a_model_that_could_not_load_is_named_not_an_api_key(
        self, tmp_path: Path, args: List[str], module: str
    ) -> None:
        args = [*args, "--no-create-in-plex"] if module == "create" else args

        result = _run_with_failed_provider(tmp_path, module, args, LOCAL_LOAD_FAILED)

        out = _flat(result.output)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "couldn't load" in out
        assert LOCAL_LOAD_FAILED.detail in out
        assert "api key" not in out.lower()

    @pytest.mark.parametrize(("args", "module"), EMBEDDING_COMMANDS)
    def test_missing_local_models_are_named_with_how_to_install_them(
        self, tmp_path: Path, args: List[str], module: str
    ) -> None:
        args = [*args, "--no-create-in-plex"] if module == "create" else args

        result = _run_with_failed_provider(tmp_path, module, args, LOCAL_MISSING_PACKAGE)

        out = _flat(result.output)
        assert result.exit_code == 1
        assert "Local models aren't installed" in out
        assert "plexmix[local]" in out
        assert "api key" not in out.lower()

    @pytest.mark.parametrize(("args", "module"), EMBEDDING_COMMANDS)
    @pytest.mark.parametrize(
        "failure",
        [None, ProviderFailure(Cause.MISSING_API_KEY, "openai")],
        ids=["no-failure-object", "missing-api-key"],
    )
    def test_a_missing_key_is_still_reported_as_one(
        self, tmp_path: Path, args: List[str], module: str, failure: Optional[ProviderFailure]
    ) -> None:
        args = [*args, "--no-create-in-plex"] if module == "create" else args

        result = _run_with_failed_provider(tmp_path, module, args, failure)

        out = _flat(result.output)
        assert result.exit_code == 1
        assert "API key required for embedding provider." in out
        assert "Run: plexmix config init" in out

    def test_tags_generate_names_the_cause_skips_the_refresh_and_keeps_its_tags(
        self, library: Path
    ) -> None:
        """The tags were saved before the embedding refresh. Failing to start the provider
        skips the refresh, says why, and leaves the saved tags and the exit status as they were."""
        _seed_embeddings(library)
        _untag(library, 201, 230)
        embeddings = _embedding_rows(library)

        run = _run_tags_generate(library, embedding_failure=LOCAL_LOAD_FAILED)

        assert run.result.exit_code == 0, run.result.output
        assert run.result.exception is None
        assert "Updated 30 tracks with tags!" in run.out
        assert "The local model couldn't load" in run.out
        assert LOCAL_LOAD_FAILED.detail in run.out
        assert "Skipping." in run.out
        assert "api key" not in run.out.lower()
        # The tags are saved, and no embedding was made or replaced.
        assert run.ai.sent == list(range(201, 231))
        tagged = {row[0]: row[1:4] for row in _tag_rows(library) if 201 <= row[0] <= 230}
        assert tagged == {n: (f"new-{n}", "focus", "piano") for n in range(201, 231)}
        assert run.vectors.requests == []
        assert _embedding_rows(library) == embeddings

    def test_tags_generate_still_says_a_missing_key_is_required(self, library: Path) -> None:
        _untag(library, 201, 210)

        run = _run_tags_generate(
            library, embedding_failure=ProviderFailure(Cause.MISSING_API_KEY, "openai")
        )

        assert run.result.exit_code == 0, run.result.output
        assert "API key required for embedding provider." in run.out
        assert "Skipping." in run.out

    @pytest.mark.parametrize(
        ("error", "cause"),
        [
            pytest.param(RuntimeError(f"upstream refused {DUMMY_KEY}"), Cause.UNKNOWN, id="other"),
            pytest.param(
                RuntimeError(f"Failed to load local model {DUMMY_KEY}"),
                Cause.MODEL_LOAD_FAILED,
                id="model-load-failed",
            ),
            pytest.param(
                ImportError(f"cohere not installed ({DUMMY_KEY})"),
                Cause.MISSING_DEPENDENCY,
                id="missing-dependency",
            ),
        ],
    )
    def test_no_line_holds_the_key_that_an_error_repeated(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        error: BaseException,
        cause: Cause,
    ) -> None:
        """The real builder redacts the stored key from the error text, so no printed
        line repeats it, whichever way the provider failed."""
        # A logging handler that an earlier test left on the root logger, with a stream
        # that is closed, would print "--- Logging error ---" and the chained provider
        # error (which holds the key). That is test pollution, not what this checks.
        monkeypatch.setattr(logging, "raiseExceptions", False)
        settings = _make_settings(tmp_path)
        settings.embedding.default_provider = "openai"
        with ExitStack() as stack:
            stack.enter_context(
                patch("plexmix.services.providers.EmbeddingGenerator", side_effect=error)
            )
            stack.enter_context(
                patch(
                    "plexmix.services.providers.resolve_embedding_api_key",
                    return_value=DUMMY_KEY,
                )
            )
            generator, failure = build_embedding_generator_with_reason(settings)
            assert generator is None and failure is not None
            assert failure.cause == cause

            lines = embedding_failure_lines(failure)
            assert DUMMY_KEY not in "\n".join(lines)
            assert "[redacted]" in "\n".join(lines)

            # The same through a command, from the key that the builder resolved to the screen.
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch("plexmix.cli.create_cmd.Settings.load_from_file", return_value=settings)
            )
            result = runner.invoke(
                app,
                ["create", "calm evening", "--limit", "5", "--name", "Calm", "--no-create-in-plex"],
            )

        assert result.exit_code == 1
        assert DUMMY_KEY not in result.output
        assert DUMMY_KEY not in _squeezed(result.output)
        assert DUMMY_KEY not in result.stdout


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
