"""Tests for the `plexmix doctor` CLI command.

The doctor command performs database health checks: detecting orphaned
embeddings, missing embeddings, untagged tracks, and offers a --force mode
to wipe and regenerate everything.
"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

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
from plexmix.database.models import SyncHistory
from plexmix.plex.client import PlexReadInterrupted
from plexmix.plex.sync import REASON_THRESHOLD, SyncOutcome

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    """Build a Settings object with paths pointing at tmp_path."""
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


def _mock_sqlite_manager(mock_sm_cls: MagicMock) -> MagicMock:
    """Configure the SQLiteManager class mock as a context manager and return
    the mock db instance."""
    mock_db = MagicMock()
    mock_sm_cls.return_value.__enter__ = MagicMock(return_value=mock_db)
    mock_sm_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_db


def _make_cursor(mock_db: MagicMock) -> MagicMock:
    """Create and wire a mock cursor to the mock db, returning the cursor."""
    mock_cursor = MagicMock()
    mock_db.get_connection.return_value.cursor.return_value = mock_cursor
    return mock_cursor


# ---------------------------------------------------------------------------
# Normal mode tests (no --force)
# ---------------------------------------------------------------------------


class TestDoctorHealthy:
    """Healthy database: no orphans, all tracks have embeddings."""

    @patch("plexmix.cli.doctor_cmd.typer.confirm")
    @patch("plexmix.cli.doctor_cmd.SQLiteManager")
    @patch("plexmix.cli.doctor_cmd.Settings.load_from_file")
    def test_healthy_database(self, mock_load, mock_sm_cls, mock_confirm, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = _mock_sqlite_manager(mock_sm_cls)
        mock_cursor = _make_cursor(mock_db)

        # total_tracks=100, tracks_with_embeddings=100, orphaned=0
        mock_cursor.fetchone.side_effect = [
            (100,),  # SELECT COUNT(*) FROM tracks
            (100,),  # SELECT COUNT(DISTINCT track_id) FROM embeddings
            (0,),  # orphaned_count
        ]

        # "Run a sync to check for deleted tracks in Plex?" -> No
        mock_confirm.return_value = False

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, f"output={result.output!r}"
        assert "healthy" in result.output.lower()


class TestDoctorMissingEmbeddings:
    """Missing embeddings but no orphans."""

    @patch("plexmix.cli.doctor_cmd.typer.confirm")
    @patch("plexmix.cli.doctor_cmd.SQLiteManager")
    @patch("plexmix.cli.doctor_cmd.Settings.load_from_file")
    def test_missing_embeddings_user_declines(self, mock_load, mock_sm_cls, mock_confirm, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = _mock_sqlite_manager(mock_sm_cls)
        mock_cursor = _make_cursor(mock_db)

        # total_tracks=100, tracks_with_embeddings=50, orphaned=0,
        # untagged=0, tracks_needing_embeddings=50
        mock_cursor.fetchone.side_effect = [
            (100,),  # total_tracks
            (50,),  # tracks_with_embeddings
            (0,),  # orphaned_count
            (0,),  # untagged_count
            (50,),  # tracks_needing_embeddings
        ]

        # "Regenerate embeddings now?" -> No
        # "Run a sync to remove deleted tracks?" -> No
        mock_confirm.return_value = False

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, f"output={result.output!r}"
        assert "tracks without embeddings" in result.output.lower()


class TestDoctorOrphanedEmbeddings:
    """Orphaned embeddings detected."""

    @patch("plexmix.cli.doctor_cmd.typer.confirm")
    @patch("plexmix.cli.doctor_cmd.SQLiteManager")
    @patch("plexmix.cli.doctor_cmd.Settings.load_from_file")
    def test_orphaned_user_confirms_deletion(self, mock_load, mock_sm_cls, mock_confirm, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = _mock_sqlite_manager(mock_sm_cls)
        mock_cursor = _make_cursor(mock_db)

        # total_tracks=100, tracks_with_embeddings=100, orphaned=5,
        # untagged=0, tracks_needing_embeddings=0
        mock_cursor.fetchone.side_effect = [
            (100,),  # total_tracks
            (100,),  # tracks_with_embeddings
            (5,),  # orphaned_count
            (0,),  # untagged_count
            (0,),  # tracks_needing_embeddings
        ]
        mock_cursor.rowcount = 5

        # "Delete 5 orphaned embeddings?" -> Yes
        # "Run a sync to remove deleted tracks?" -> No
        mock_confirm.side_effect = [True, False]

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, f"output={result.output!r}"
        assert "deleted 5" in result.output.lower()

    @patch("plexmix.cli.doctor_cmd.typer.confirm")
    @patch("plexmix.cli.doctor_cmd.SQLiteManager")
    @patch("plexmix.cli.doctor_cmd.Settings.load_from_file")
    def test_orphaned_user_cancels(self, mock_load, mock_sm_cls, mock_confirm, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = _mock_sqlite_manager(mock_sm_cls)
        mock_cursor = _make_cursor(mock_db)

        # total_tracks=100, tracks_with_embeddings=100, orphaned=5
        mock_cursor.fetchone.side_effect = [
            (100,),  # total_tracks
            (100,),  # tracks_with_embeddings
            (5,),  # orphaned_count
        ]

        # "Delete 5 orphaned embeddings?" -> No (cancels entire operation)
        mock_confirm.return_value = False

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0, f"output={result.output!r}"
        assert "cancelled" in result.output.lower()


# ---------------------------------------------------------------------------
# The sync that doctor offers
# ---------------------------------------------------------------------------

# How to reach each of doctor's two "run a sync" questions: the counts its queries
# return, and its own questions answered up to and including that one.
SYNC_PROMPTS = [
    pytest.param([(100,), (100,), (0,)], [True], id="healthy-database"),
    pytest.param([(100,), (100,), (5,), (0,), (0,)], [True, True], id="after-repairs"),
]


def _flat(text: str) -> str:
    """The output as one line: the console wraps a long sentence at the terminal width."""
    return " ".join(text.split())


@contextmanager
def _doctor_at_sync_prompt(
    tmp_path: Path, counts: List[Tuple[int]], answers: List[bool]
) -> Iterator[MagicMock]:
    """Doctor on a mock database, answered up to a "run a sync" question.

    Yields the mock that answers ``typer.confirm``. It replaces the function for
    every module, so a question asked by the sync itself is answered by it too.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd.Settings.load_from_file",
                return_value=_make_settings(tmp_path),
            )
        )
        mock_sm_cls = stack.enter_context(patch("plexmix.cli.doctor_cmd.SQLiteManager"))
        mock_confirm = stack.enter_context(patch("plexmix.cli.doctor_cmd.typer.confirm"))
        mock_cursor = _make_cursor(_mock_sqlite_manager(mock_sm_cls))
        mock_cursor.fetchone.side_effect = list(counts)
        mock_cursor.rowcount = 5
        mock_confirm.side_effect = list(answers)
        yield mock_confirm


def _sync_engine(
    outcome: Optional[SyncOutcome] = None, error: Optional[BaseException] = None
) -> MagicMock:
    """A mock for the ``SyncEngine`` object: a clean run unless told otherwise."""
    engine = MagicMock()
    engine.incremental_sync.return_value = SyncHistory(tracks_added=5, tracks_updated=2)
    engine.incremental_sync.side_effect = error
    engine.outcome = outcome or SyncOutcome()
    return engine


@contextmanager
def _patched_sync(engine: MagicMock, terminal: bool = False) -> Iterator[MagicMock]:
    """The sync behind doctor's question, with everything it would reach patched.

    Plex, the AI provider, the sync's own database and the engine are mocks, and
    ``terminal`` says whether there is a terminal to ask in. Yields the mock for
    the ``SyncEngine`` class, which records how the engine was built.
    """
    mock_db = MagicMock()
    mock_db.__enter__ = MagicMock(return_value=mock_db)
    mock_db.__exit__ = MagicMock(return_value=False)
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.sync_cmd.connect_plex", return_value=MagicMock()))
        stack.enter_context(
            patch("plexmix.cli.sync_cmd._build_ai_provider", return_value=MagicMock())
        )
        stack.enter_context(patch("plexmix.cli.sync_cmd.SQLiteManager", return_value=mock_db))
        stack.enter_context(patch("plexmix.cli.sync_cmd.can_prompt", return_value=terminal))
        yield stack.enter_context(patch("plexmix.cli.sync_cmd.SyncEngine", return_value=engine))


def _held_outcome() -> SyncOutcome:
    """What the engine publishes after it held a removal above the threshold."""
    return SyncOutcome(
        listed={"artists": 1, "albums": 1, "tracks": 500},
        library_total=5943,
        held=5443,
        reason=REASON_THRESHOLD,
        message=(
            "Plex no longer lists 5,443 of 5,943 tracks. They were kept, because a "
            "removal this large needs confirmation."
        ),
    )


@pytest.mark.parametrize("counts, answers", SYNC_PROMPTS)
class TestDoctorSync:
    """Both sync questions of doctor start the same sync, under the same rules."""

    def test_sync_prompt_calls_run_incremental_sync_with_real_booleans(
        self, counts: List[Tuple[int]], answers: List[bool], tmp_path: Path
    ) -> None:
        with _doctor_at_sync_prompt(tmp_path, counts, answers), patch(
            "plexmix.cli.sync_cmd.run_incremental_sync", return_value=True
        ) as run_sync:
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0, f"output={result.output!r}"
        run_sync.assert_called_once_with(embeddings=False)
        # Real booleans only: no placeholder object of a Typer option is passed on.
        passed: List[Any] = [*run_sync.call_args.args, *run_sync.call_args.kwargs.values()]
        assert all(isinstance(value, bool) for value in passed)
        assert "Sync completed!" in result.output

    def test_sync_prompt_starts_a_plain_sync(
        self, counts: List[Tuple[int]], answers: List[bool], tmp_path: Path
    ) -> None:
        """No removal permission, no audio analysis and no MusicBrainz reach the engine.

        Called as a plain function, the ``sync incremental`` command would get a
        truthy placeholder object for each of those options.
        """
        engine = _sync_engine()
        with _doctor_at_sync_prompt(tmp_path, counts, answers), _patched_sync(engine) as engine_cls:
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0, f"output={result.output!r}"
        engine.incremental_sync.assert_called_once()
        sync_options = engine.incremental_sync.call_args.kwargs
        assert sync_options["generate_embeddings"] is False
        assert sync_options["allow_large_removals"] is False
        assert engine_cls.call_args.kwargs["audio_settings"] is None
        assert engine_cls.call_args.kwargs["musicbrainz_settings"] is None
        # Doctor's sync runs without embeddings. The engine still knows where the
        # saved index is, for the backup of a large removal.
        assert engine_cls.call_args.kwargs["saved_index_path"] == tmp_path / "test.index"
        assert "Sync completed!" in result.output

    def test_completed_line_needs_a_sync_that_said_so(
        self, counts: List[Tuple[int]], answers: List[bool], tmp_path: Path
    ) -> None:
        # The sync ended well but kept something back, so it returned False.
        with _doctor_at_sync_prompt(tmp_path, counts, answers), patch(
            "plexmix.cli.sync_cmd.run_incremental_sync", return_value=False
        ) as run_sync:
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 0, f"output={result.output!r}"
        run_sync.assert_called_once_with(embeddings=False)
        assert "Sync completed" not in result.output

    def test_doctor_sync_stopped_prints_no_completed_line(
        self, counts: List[Tuple[int]], answers: List[bool], tmp_path: Path
    ) -> None:
        engine = _sync_engine(error=PlexReadInterrupted("tracks", read=522, listed=5943))
        with _doctor_at_sync_prompt(tmp_path, counts, answers), _patched_sync(engine):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)  # no error report
        assert (
            "Plex stopped answering after 522 of 5,943 tracks were read, "
            "so nothing was changed." in _flat(result.stdout)
        )
        assert "completed" not in result.output.lower()

    def test_doctor_sync_with_a_failed_connection_check_prints_one_sentence(
        self, counts: List[Tuple[int]], answers: List[bool], tmp_path: Path
    ) -> None:
        """Plex went away between doctor's question and the engine's own check."""
        engine = _sync_engine(error=ConnectionError("Cannot reach Plex server: no answer"))
        with _doctor_at_sync_prompt(tmp_path, counts, answers), _patched_sync(engine):
            result = runner.invoke(app, ["doctor"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)  # no error report
        assert "Cannot reach Plex server: no answer." in _flat(result.stdout)
        assert "Traceback" not in result.output
        assert "completed" not in result.output.lower()

    def test_doctor_sync_with_held_removal_does_not_remove(
        self, counts: List[Tuple[int]], answers: List[bool], tmp_path: Path
    ) -> None:
        engine = _sync_engine(outcome=_held_outcome())
        with _doctor_at_sync_prompt(tmp_path, counts, answers) as mock_confirm, _patched_sync(
            engine, terminal=False
        ):
            result = runner.invoke(app, ["doctor"])

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert "5,443 tracks were kept." in out
        assert "--allow-removals" in out
        assert "completed" not in out.lower()
        assert engine.incremental_sync.call_args.kwargs["allow_large_removals"] is False
        engine.remove_unlisted_tracks.assert_not_called()
        # Only doctor's own questions were asked: without a terminal the sync asks none.
        assert mock_confirm.call_count == len(answers)

    def test_doctor_sync_in_a_terminal_asks_before_removing(
        self, counts: List[Tuple[int]], answers: List[bool], tmp_path: Path
    ) -> None:
        engine = _sync_engine(outcome=_held_outcome())
        # One more question than doctor's own, and it is answered with No.
        with _doctor_at_sync_prompt(
            tmp_path, counts, [*answers, False]
        ) as mock_confirm, _patched_sync(engine, terminal=True):
            result = runner.invoke(app, ["doctor"])

        out = _flat(result.stdout)
        assert result.exit_code == 0, f"output={result.output!r}"
        assert mock_confirm.call_count == len(answers) + 1
        question = mock_confirm.call_args
        assert question.args[0] == "Remove 5,443 of 5,943 tracks?"
        assert question.kwargs["default"] is False
        assert "5,443 tracks were kept." in out
        assert "completed" not in out.lower()
        engine.remove_unlisted_tracks.assert_not_called()


# ---------------------------------------------------------------------------
# Force mode tests
# ---------------------------------------------------------------------------


class TestDoctorForce:
    """--force mode: wipe and regenerate all tags + embeddings."""

    @patch("plexmix.cli.embeddings_cmd.embeddings_generate")
    @patch("plexmix.cli.tags_cmd.tags_generate")
    @patch("plexmix.cli.doctor_cmd.typer.confirm")
    @patch("plexmix.cli.doctor_cmd.SQLiteManager")
    @patch("plexmix.cli.doctor_cmd.Settings.load_from_file")
    def test_force_user_confirms(
        self,
        mock_load,
        mock_sm_cls,
        mock_confirm,
        mock_tags_generate,
        mock_embeddings_generate,
        tmp_path,
    ):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = _mock_sqlite_manager(mock_sm_cls)
        mock_cursor = _make_cursor(mock_db)

        # Force mode queries: total_tracks, total_embeddings
        mock_cursor.fetchone.side_effect = [
            (100,),  # total_tracks
            (80,),  # total_embeddings
        ]

        # "Are you sure you want to continue?" -> Yes
        mock_confirm.return_value = True

        result = runner.invoke(app, ["doctor", "--force"])
        assert result.exit_code == 0, f"output={result.output!r}"
        mock_tags_generate.assert_called_once()
        mock_embeddings_generate.assert_called_once()

    @patch("plexmix.cli.doctor_cmd.typer.confirm")
    @patch("plexmix.cli.doctor_cmd.Settings.load_from_file")
    def test_force_user_cancels(self, mock_load, mock_confirm, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        # "Are you sure you want to continue?" -> No
        mock_confirm.return_value = False

        result = runner.invoke(app, ["doctor", "--force"])
        assert result.exit_code == 0, f"output={result.output!r}"
        assert "cancelled" in result.output.lower()
