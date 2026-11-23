"""Tests for the `plexmix doctor` CLI command.

The doctor command performs database health checks: detecting orphaned
embeddings, missing embeddings and untagged tracks. Its --force mode regenerates
the AI tags and the embedding of every track in place: it asks first, checks both
providers, and clears nothing.

The force tests run the real engine on a temporary library. Only the two providers
are fakes, so each test also checks what is left in the database.
"""

import json
import re
import sqlite3
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from unittest.mock import patch, MagicMock

import click
import pytest
from typer.models import OptionInfo
from typer.testing import CliRunner

from plexmix.cli import doctor_cmd
from plexmix.cli.embedding_failure import embedding_failure_lines
from plexmix.cli.main import app
from plexmix.config import credentials
from plexmix.config.settings import (
    Settings,
    PlexSettings,
    DatabaseSettings,
    AISettings,
    EmbeddingSettings,
    PlaylistSettings,
    AudioSettings,
)
from plexmix.database.models import Album, Artist, Embedding, SyncHistory, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex.client import PlexReadInterrupted
from plexmix.plex.sync import REASON_THRESHOLD, RegeneratePass, RegenerateResult, SyncOutcome
from plexmix.services.providers import ProviderFailure
from plexmix.services.readiness import Cause
from plexmix.services.regenerate_service import (
    CAUSE_NO_ANSWER,
    COMPONENT_AI,
    COMPONENT_EMBEDDINGS,
    REFUSED_NO_ANSWER,
    SCOPE_EMBEDDINGS,
    SCOPE_TAGS,
    CheckedProviders,
    RegenerateRefused,
    marker_path,
    run_provider,
)

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


class TestDoctorTagsQuestion:
    """The question "Generate AI tags for untagged tracks first?", answered with Yes."""

    @patch("plexmix.cli.doctor_cmd.typer.confirm")
    @patch("plexmix.cli.doctor_cmd.SQLiteManager")
    @patch("plexmix.cli.doctor_cmd.Settings.load_from_file")
    def test_yes_runs_the_tags_command_with_every_option_given(
        self, mock_load, mock_sm_cls, mock_confirm, tmp_path
    ):
        """The tags command is called as a plain function. An option the caller left
        out arrived as a placeholder object, and comparing it with a number raised."""
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_db = _mock_sqlite_manager(mock_sm_cls)
        mock_cursor = _make_cursor(mock_db)
        mock_cursor.fetchone.side_effect = [
            (100,),  # total_tracks
            (50,),  # tracks_with_embeddings
            (0,),  # orphaned_count
            (3,),  # untagged_count
            (50,),  # tracks_needing_embeddings
        ]
        # Tags first? Yes. Regenerate embeddings now? No. Run a sync? No.
        mock_confirm.side_effect = [True, False, False]

        # The real tags command, on a library in which every track has tags by now.
        tags_db = MagicMock()
        tags_db.__enter__ = MagicMock(return_value=tags_db)
        tags_db.__exit__ = MagicMock(return_value=False)
        tags_db.get_all_tracks.return_value = [
            Track(id=1, plex_key="t1", title="Song", artist_id=1, album_id=1, tags="calm,slow")
        ]
        with ExitStack() as stack:
            stack.enter_context(
                patch("plexmix.cli.tags_cmd.Settings.load_from_file", return_value=settings)
            )
            stack.enter_context(
                patch("plexmix.cli.tags_cmd._resolve_ai_api_key", return_value=DUMMY_KEY)
            )
            stack.enter_context(patch("plexmix.cli.tags_cmd.SQLiteManager", return_value=tags_db))
            result = runner.invoke(app, ["doctor"])

        assert result.exception is None, repr(result.exception)
        assert result.exit_code == 0, f"output={result.output!r}"
        assert "All tracks already have up-to-date tags!" in result.output
        assert "Tags generated!" in result.output
        # No stale-tag lookup: the option was 0, not a placeholder object.
        tags_db.get_tracks_by_filter.assert_not_called()

    def test_doctor_passes_every_option_of_the_tags_command(self) -> None:
        """A Typer command called as a function gets a placeholder for each option left out."""
        import inspect

        from plexmix.cli.tags_cmd import tags_generate

        options = [
            name
            for name, parameter in inspect.signature(tags_generate).parameters.items()
            if isinstance(parameter.default, OptionInfo)
        ]
        source = inspect.getsource(doctor_cmd)
        [call] = re.findall(r"tags_generate\(([^)]*)\)", source)
        assert options and all(f"{name}=" in call for name in options), call


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

FORCE_COMMAND = "plexmix doctor --force"
SUCCESS_LINE = "Force regeneration complete!"

# The only "secret" in this file is one of the project's dummy values.
DUMMY_KEY = "dummy-api-key-0000"

# Six tag requests of 20 tracks, and three embedding requests of 50, 50 and 20.
LIBRARY_SIZE = 120
LONG_AGO = datetime(2020, 1, 1, 9, 0, 0)
OLD_VECTOR = [0.1, 0.2, 0.3, 0.4]
NEW_VECTOR = [9.0, 1.0, 1.0, 1.0]
# The embedding provider of the force tests, as ``_force_settings`` names it.
VECTOR_MODEL = "openai"
# A provider error that stops the tagging for good, and one that only loses a batch.
FATAL_ERROR = RuntimeError("401 Unauthorized")
PASSING_ERROR = ConnectionRefusedError("connection refused")


def _squeezed(text: str) -> str:
    """The output without any white space, to look for a value that a wrap could split."""
    return "".join(text.split())


def _ids_in_prompt(prompt: str) -> List[int]:
    """The ids of the tracks a tagging request lists."""
    listed = prompt.split("Assign tags to the following songs:")[1]
    listed = listed.split("Return a JSON object")[0]
    return [int(entry["id"]) for entry in json.loads(listed)]


class _TaggingAI:
    """Stands in for the AI provider. It gives every track it is sent a new tag.

    From request ``fail_from`` on (counted from 1) it raises ``error`` in place of
    an answer. ``omit`` ids are left out of its replies. ``requests`` records the
    track ids of every request.
    """

    provider_name = "OpenAI"
    model = "gpt-test"
    api_key = DUMMY_KEY

    def __init__(
        self,
        error: Optional[BaseException] = None,
        fail_from: int = 1,
        omit: Iterable[int] = (),
    ) -> None:
        self.error = error
        self.fail_from = fail_from
        self.omit = set(omit)
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
                if track_id not in self.omit
            }
        )

    @property
    def sent(self) -> List[int]:
        """Every track that was sent to the provider, by id."""
        return sorted({track_id for request in self.requests for track_id in request})


class _Vectors:
    """Stands in for the embedding generator. Every text gets ``NEW_VECTOR``.

    Request ``fail_on_request`` (counted from 1) raises ``error`` in place of an
    answer. ``short`` vectors are missing at the end of every reply: the provider
    answered, and left those texts out. ``requests`` records how many texts each
    request held.
    """

    provider_name = VECTOR_MODEL

    def __init__(
        self,
        fail_on_request: Optional[int] = None,
        error: Optional[BaseException] = None,
        short: int = 0,
    ) -> None:
        self.fail_on_request = fail_on_request
        self.error = error or RuntimeError("the embedding provider stopped answering")
        self.short = short
        self.requests: List[int] = []

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        self.requests.append(len(texts))
        if self.fail_on_request == len(self.requests):
            raise self.error
        return [list(NEW_VECTOR) for _ in texts[: len(texts) - self.short]]

    def generate_embedding(self, text: str) -> List[float]:
        return list(NEW_VECTOR)

    def get_dimension(self) -> int:
        return len(NEW_VECTOR)


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


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A temporary library where ``_make_settings`` points.

    It holds 120 tracks. Each has tags, and an embedding that was written long ago.
    """
    db_path = tmp_path / "test.db"
    with SQLiteManager(str(db_path)) as db:
        db.create_tables()
        artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist One"))
        album_id = db.insert_album(Album(plex_key="al1", title="Album One", artist_id=artist_id))
        with db.deferred_commits():
            for number in range(1, LIBRARY_SIZE + 1):
                db.insert_track(
                    Track(
                        plex_key=f"t{number}",
                        title=f"Song {number}",
                        artist_id=artist_id,
                        album_id=album_id,
                        play_count=0,
                        tags=f"old-{number}",
                    )
                )
    _seed_embeddings(db_path)
    return db_path


# An embedding of the vector size the library had before that size was changed.
WIDE_VECTOR = [0.5] * 8


def _index_files(folder: Path) -> Dict[str, bytes]:
    """Every file of the search index in ``folder``, moved-aside copies included."""
    return {
        item.name: item.read_bytes()
        for item in sorted(folder.iterdir())
        if item.name.startswith(("test.index", "test.metadata"))
    }


def _seed_earlier_size(
    library: Path, track_ids: Optional[Iterable[int]] = None
) -> Dict[str, bytes]:
    """The library as it was before the vector size changed from 8 numbers to 4.

    Every track (or each of ``track_ids``) holds an embedding of 8 numbers under the
    model name that is still in use, written long ago, and the saved index holds
    those vectors. Every other embedding row is removed. The provider makes 4
    numbers now (``_Vectors``), so an index that is opened for it reports a
    dimension mismatch. Returns the files of the saved index, byte for byte.
    """
    with SQLiteManager(str(library)) as db:
        ids = [track.id for track in db.get_all_tracks() if track.id is not None]
        if track_ids is not None:
            ids = sorted(set(ids) & set(track_ids))
        db.get_connection().execute("DELETE FROM embeddings")
        db._commit()
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
    files = _index_files(library.parent)
    assert sorted(files) == ["test.index", "test.metadata"]
    return files


def _assert_saved_index_is_untouched(library: Path, before: Dict[str, bytes]) -> None:
    """The index files are the ones from before the command, byte for byte, and an
    index opened on them for the current provider still reports the dimension
    mismatch that tells every command to regenerate."""
    assert _index_files(library.parent) == before
    opened = VectorIndex(dimension=len(NEW_VECTOR), index_path=str(library.parent / "test.index"))
    assert opened.dimension_mismatch is True
    assert opened.loaded_dimension == len(WIDE_VECTOR)
    assert opened.load_failed is False
    assert _index_files(library.parent) == before


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


def _force_settings(library: Path) -> Settings:
    """Settings for the force tests: named providers and models."""
    settings = _make_settings(library.parent)
    settings.ai.default_provider = "openai"
    settings.ai.model = "gpt-test"
    settings.embedding.default_provider = VECTOR_MODEL
    settings.embedding.model = "embed-test"
    return settings


def _query(database_file: Path, sql: str) -> List[Tuple[Any, ...]]:
    connection = sqlite3.connect(str(database_file))
    try:
        return list(connection.execute(sql).fetchall())
    finally:
        connection.close()


def _tag_rows(library: Path) -> List[Tuple[Any, ...]]:
    """Per track: its id, its three tag columns and the time they were written."""
    return _query(
        library,
        "SELECT id, tags, environments, instruments, tags_generated_at FROM tracks ORDER BY id",
    )


def _embedding_rows(library: Path) -> List[Tuple[Any, ...]]:
    return _query(
        library,
        "SELECT track_id, embedding_model, vector, updated_at FROM embeddings "
        "ORDER BY track_id, embedding_model",
    )


def _vectors_by_track(library: Path, model: str = VECTOR_MODEL) -> Dict[int, List[float]]:
    rows = _query(
        library,
        f"SELECT track_id, vector FROM embeddings WHERE embedding_model = '{model}' "
        "ORDER BY track_id",
    )
    return {row[0]: json.loads(row[1]) for row in rows}


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
                f"old-{track_id}",
                None,
                None,
                None,
            )
    return done


def _reembedded(library: Path) -> List[int]:
    """The tracks whose embedding is a new one. Every other track holds its old one."""
    vectors = _vectors_by_track(library)
    assert set(vectors) == set(range(1, LIBRARY_SIZE + 1))
    assert all(vector in (OLD_VECTOR, NEW_VECTOR) for vector in vectors.values())
    return [track_id for track_id, vector in vectors.items() if vector == NEW_VECTOR]


def _ids_in_saved_index(library: Path) -> List[int]:
    index = VectorIndex(dimension=len(NEW_VECTOR), index_path=str(library.parent / "test.index"))
    assert index.load_failed is False
    return sorted(index.track_ids)


_WRITES = ("insert", "update", "delete", "replace", "drop", "alter", "create")
_BLANKS_A_TAG_COLUMN = re.compile(r"\b(tags|environments|instruments)\s*=\s*null\b")


def _writes(statements: List[str]) -> List[str]:
    """The statements that change the database, of those the command ran."""
    return [text for text in statements if text.strip().lower().startswith(_WRITES)]


def _clearing(statements: List[str]) -> List[str]:
    """The statements that blank a tag column, or that delete embeddings other than
    the rows of another model (which a finished run removes once every track has its
    new embedding)."""
    found = []
    for statement in statements:
        text = " ".join(statement.lower().split())
        blanks = text.startswith("update tracks") and _BLANKS_A_TAG_COLUMN.search(text)
        deletes = text.startswith("delete from embeddings") and "embedding_model !=" not in text
        if blanks or deletes:
            found.append(statement)
    return found


def _traced_database(statements: List[str]) -> type:
    """``SQLiteManager``, with every statement the command runs on it recorded."""

    class _TracedDatabase(SQLiteManager):
        def connect(self) -> None:
            super().connect()
            self.get_connection().set_trace_callback(statements.append)

    return _TracedDatabase


def _regenerated(
    tags: int = 5,
    tags_kept: int = 0,
    embeddings: int = 5,
    embeddings_kept: int = 0,
    unfinished: bool = False,
) -> RegenerateResult:
    """What the engine publishes after a force run, built from counts."""

    def part(written: int, kept: int) -> RegeneratePass:
        return RegeneratePass(
            requested=True,
            started=True,
            finished=not unfinished,
            to_do_ids=set(range(1, written + kept + 1)),
            written_ids=set(range(1, written + 1)),
        )

    return RegenerateResult(
        tags=part(tags, tags_kept), embeddings=part(embeddings, embeddings_kept)
    )


def _run_force(
    library: Path,
    *args: str,
    ai: Optional[_TaggingAI] = None,
    vectors: Optional[_Vectors] = None,
    refused: Optional[BaseException] = None,
    terminal: bool = False,
    answers: str = "",
    settings: Optional[Settings] = None,
    engine: Optional[MagicMock] = None,
) -> SimpleNamespace:
    """Run ``doctor --force`` on the real engine and the temporary library.

    The two providers are fakes, and the provider check hands the fakes over, or
    raises ``refused`` (a refusal, or Ctrl+C). ``terminal`` says whether a question
    can be asked and ``answers`` is what the user types. ``engine`` replaces the
    engine object by a mock, for an outcome the real one cannot be brought to.

    The result says how far the command got: ``check`` is the provider check and
    ``load`` the settings. ``at_check`` is the library as the provider check found
    it, ``statements`` every statement the command ran on its database, and
    ``engine_cls`` the mock that built ``engine``.
    """
    settings = settings or _force_settings(library)
    ai = ai or _TaggingAI()
    vectors = vectors or _Vectors()
    statements: List[str] = []
    at_check = SimpleNamespace(tags=None, embeddings=None, writes=None, requests=None)

    def checked(
        settings: Settings,
        need_ai: bool,
        need_embeddings: bool,
        ai_provider_name: Optional[str] = None,
    ) -> CheckedProviders:
        at_check.tags, at_check.embeddings = _tag_rows(library), _embedding_rows(library)
        at_check.writes = _writes(statements)
        at_check.requests = [*ai.requests, *vectors.requests]
        if refused is not None:
            raise refused
        return CheckedProviders(ai if need_ai else None, vectors if need_embeddings else None)

    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        load = stack.enter_context(
            patch("plexmix.cli.doctor_cmd.Settings.load_from_file", return_value=settings)
        )
        check = stack.enter_context(
            patch("plexmix.cli.doctor_cmd.check_providers", side_effect=checked)
        )
        stack.enter_context(patch("plexmix.cli.console_helpers.can_prompt", return_value=terminal))
        stack.enter_context(
            patch("plexmix.cli.doctor_cmd.SQLiteManager", _traced_database(statements))
        )
        engine_cls = None
        if engine is not None:
            engine_cls = stack.enter_context(
                patch("plexmix.cli.doctor_cmd.SyncEngine", return_value=engine)
            )
        result = runner.invoke(app, ["doctor", "--force", *args], input=answers)
    return SimpleNamespace(
        result=result,
        out=_flat(result.stdout),
        load=load,
        check=check,
        at_check=at_check,
        statements=statements,
        ai=ai,
        vectors=vectors,
        engine_cls=engine_cls,
        settings=settings,
    )


class TestDoctorForce:
    """``plexmix doctor --force``: it asks, checks both providers, and replaces in place."""

    def test_confirmed_run_checks_the_providers_before_anything_changes(
        self, library: Path
    ) -> None:
        _seed_embeddings(library, model="gemini")
        tags, embeddings = _tag_rows(library), _embedding_rows(library)

        run = _run_force(library, terminal=True, answers="y\n")

        assert run.result.exit_code == 0, run.result.output
        # The counts, then the question: it names the count and both providers, and
        # No is the default.
        question = "Regenerate the AI tags and embeddings of 120 tracks? [y/N]"
        before_question, after_question = run.out.split(question)
        assert "Total tracks: 120" in before_question
        assert "Current embeddings: 240" in before_question
        assert (
            "This replaces the AI tags and embeddings of all 120 tracks in the PlexMix library."
            in before_question
        )
        assert "AI tags: OpenAI · gpt-test" in before_question
        assert "Embeddings: OpenAI · embed-test" in before_question
        assert "Nothing is cleared first" in before_question
        # Both providers were checked, after the answer and before anything changed.
        assert "Checking the AI provider and the embedding provider..." in after_question
        run.check.assert_called_once_with(run.settings, need_ai=True, need_embeddings=True)
        assert (run.at_check.tags, run.at_check.embeddings) == (tags, embeddings)
        assert run.at_check.writes == [] and run.at_check.requests == []
        # Then every track was replaced in place, and nothing was ever cleared.
        assert _writes(run.statements) != []
        assert _clearing(run.statements) == []
        assert _retagged(library) == list(range(1, LIBRARY_SIZE + 1))
        assert _reembedded(library) == list(range(1, LIBRARY_SIZE + 1))
        assert run.ai.sent == list(range(1, LIBRARY_SIZE + 1))
        assert run.vectors.requests == [50, 50, 20]
        assert f"✓ {SUCCESS_LINE}" in after_question
        assert "AI tags: 120 tracks regenerated." in after_question
        assert "Embeddings: 120 tracks regenerated." in after_question
        assert "keep" not in after_question
        # Every track has its new embedding, so the rows of the other model are gone,
        # and the saved index holds the new vectors.
        assert "Removed 120 embeddings of other models." in after_question
        assert _vectors_by_track(library, model="gemini") == {}
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()
        # Doctor's force run reads no Plex and records no sync.
        assert _query(library, "SELECT COUNT(*) FROM sync_history") == [(0,)]

    def test_run_that_fails_at_once_never_executes_a_clearing_statement(
        self, library: Path
    ) -> None:
        """The audited loss: the provider failed after everything had been cleared."""
        tags, embeddings = _tag_rows(library), _embedding_rows(library)
        ai = _TaggingAI(error=FATAL_ERROR)

        run = _run_force(library, "--yes", ai=ai)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert _clearing(run.statements) == []
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert "AI tagging stopped:" in run.out
        assert "AI tags: 0 tracks regenerated, 120 keep their previous tags." in run.out
        assert "Embeddings: 0 tracks regenerated, 120 keep their previous embedding." in run.out
        assert SUCCESS_LINE not in run.out
        assert DUMMY_KEY not in _squeezed(run.result.output)

    def test_refused_without_an_ai_key_names_the_provider_and_changes_nothing(
        self, library: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec "No AI key", with the real provider check: no key is stored anywhere."""
        for variable in ("OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(variable, raising=False)
        monkeypatch.setattr(credentials, "_get_keyring", lambda: None)
        tags, embeddings = _tag_rows(library), _embedding_rows(library)
        statements: List[str] = []

        with ExitStack() as stack:
            stack.enter_context(patch("plexmix.cli.main.setup_logging"))
            stack.enter_context(
                patch(
                    "plexmix.cli.doctor_cmd.Settings.load_from_file",
                    return_value=_force_settings(library),
                )
            )
            stack.enter_context(
                patch("plexmix.cli.doctor_cmd.SQLiteManager", _traced_database(statements))
            )
            engine_cls = stack.enter_context(patch("plexmix.cli.doctor_cmd.SyncEngine"))
            result = runner.invoke(app, ["doctor", "--force", "--yes"])

        out = _flat(result.stdout)
        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit)
        assert "The AI provider OpenAI is not ready: no API key is stored." in out
        assert "Nothing was changed." in out
        assert "Run 'plexmix config init' to store the key." in out
        assert SUCCESS_LINE not in out
        # The run never started: no engine, no statement that writes, no marker.
        engine_cls.assert_not_called()
        assert _writes(statements) == []
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert not marker_path(library).exists()

    def test_refusal_of_the_embedding_provider_shows_its_detail_without_the_key(
        self, library: Path
    ) -> None:
        tags, embeddings = _tag_rows(library), _embedding_rows(library)
        refused = RegenerateRefused(
            "OpenAI",
            CAUSE_NO_ANSWER,
            component=COMPONENT_EMBEDDINGS,
            reason=REFUSED_NO_ANSWER,
            detail="Error code: 401 - [redacted] was rejected",
            error=RuntimeError(f"Error code: 401 - {DUMMY_KEY} was rejected"),
        )

        run = _run_force(library, "--yes", refused=refused)

        assert run.result.exit_code == 1
        assert (
            "The embedding provider OpenAI is not ready: it did not answer the test request. "
            "Error code: 401 - [redacted] was rejected." in run.out
        )
        # The provider's own error is kept for classification only. It is never printed.
        assert DUMMY_KEY not in _squeezed(run.result.output)
        assert _writes(run.statements) == []
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert run.ai.requests == [] and run.vectors.requests == []

    @pytest.mark.parametrize("typed", ["\n", "n\n"], ids=["enter", "n"])
    def test_declined_exits_zero_and_changes_nothing(self, library: Path, typed: str) -> None:
        tags, embeddings = _tag_rows(library), _embedding_rows(library)

        run = _run_force(library, terminal=True, answers=typed)

        assert run.result.exit_code == 0
        question = "Regenerate the AI tags and embeddings of 120 tracks? [y/N]"
        assert question in run.out
        assert run.out.split(question)[1].strip().endswith("Operation cancelled.")
        # Declining is not a run: no provider was asked and nothing was written.
        run.check.assert_not_called()
        assert run.ai.requests == [] and run.vectors.requests == []
        assert _writes(run.statements) == []
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert not marker_path(library).exists()

    def test_refuses_without_terminal_and_without_yes(self, library: Path) -> None:
        tags, embeddings = _tag_rows(library), _embedding_rows(library)

        run = _run_force(library, terminal=False)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        # It names the flag and the whole command to run in its place.
        assert f"'{FORCE_COMMAND} --yes'" in run.out
        assert "no terminal to answer in" in run.out
        assert "Nothing was changed." in run.out
        assert "[y/N]" not in run.out
        assert SUCCESS_LINE not in run.out
        # It ended before it loaded anything: the one load is the entry point's own,
        # which reads the log level. The database was not even opened.
        assert run.load.call_count == 1
        assert run.statements == []
        run.check.assert_not_called()
        assert (_tag_rows(library), _embedding_rows(library)) == (tags, embeddings)
        assert not marker_path(library).exists()

    def test_yes_skips_the_question(self, library: Path) -> None:
        """Spec "Unattended run with the flag": no terminal, no question, the run is done."""
        run = _run_force(library, "--yes", terminal=False)

        assert run.result.exit_code == 0, run.result.output
        assert "[y/N]" not in run.out
        assert "Operation cancelled" not in run.out
        # What the run is about to do is still said: the count and the providers.
        assert (
            "This replaces the AI tags and embeddings of all 120 tracks in the PlexMix library."
            in run.out
        )
        assert "AI tags: OpenAI · gpt-test" in run.out
        assert f"✓ {SUCCESS_LINE}" in run.out
        assert len(_retagged(library)) == LIBRARY_SIZE
        assert len(_reembedded(library)) == LIBRARY_SIZE

    def test_interrupt_tip_names_the_same_command(self, library: Path) -> None:
        embeddings = _embedding_rows(library)
        ai = _TaggingAI(error=KeyboardInterrupt(), fail_from=3)

        run = _run_force(library, "--yes", ai=ai)

        assert run.result.exit_code == 130
        assert "Interrupted." in run.out
        assert "AI tags: 40 tracks regenerated, 80 keep their previous tags." in run.out
        assert "Embeddings: 0 tracks regenerated, 120 keep their previous embedding." in run.out
        assert "Every track that was not regenerated keeps its previous data." in run.out
        assert f"Tip: Run '{FORCE_COMMAND}' again to continue." in run.out
        assert "clears nothing" in run.out
        assert SUCCESS_LINE not in run.out
        # Old and new side by side: no track is without tags or without an embedding.
        assert _retagged(library) == list(range(1, 41))
        assert all(row[1] for row in _tag_rows(library))
        assert _embedding_rows(library) == embeddings
        assert _clearing(run.statements) == []
        # The run can be continued: its marker entry is still there.
        assert marker_path(library).exists()

    def test_interrupted_run_is_continued_by_the_same_command(self, library: Path) -> None:
        """Spec "Continuing": the second run says so, and sends only the tracks that are left."""
        first = _run_force(library, "--yes", ai=_TaggingAI(error=KeyboardInterrupt(), fail_from=3))
        assert first.result.exit_code == 130
        marker = marker_path(library)

        second = _run_force(library, terminal=True, answers="y\n")

        assert second.result.exit_code == 0, second.result.output
        question = "Continue with the remaining 80 tracks? [y/N]"
        assert question in second.out
        before_question = second.out.split(question)[0]
        assert (
            "An earlier regenerate run did not finish. This run continues it: "
            "80 of 120 tracks remain." in before_question
        )
        # How to start over: the marker file is named, in one piece.
        assert marker.name == "test.db.regenerate.json"
        assert (
            f"To start over with all 120 tracks, delete {marker} and run the command again."
            in second.result.stdout
        )
        assert second.ai.sent == list(range(41, LIBRARY_SIZE + 1))
        assert "AI tags: 80 tracks regenerated." in second.out
        assert "Embeddings: 120 tracks regenerated." in second.out
        assert f"✓ {SUCCESS_LINE}" in second.out
        assert len(_retagged(library)) == LIBRARY_SIZE
        assert len(_reembedded(library)) == LIBRARY_SIZE
        # The run reached its end, so nothing is left to continue.
        assert not marker.exists()

    def test_provider_failure_part_way_exits_nonzero_with_tip(self, library: Path) -> None:
        embeddings = _embedding_rows(library)
        ai = _TaggingAI(error=FATAL_ERROR, fail_from=3)

        run = _run_force(library, "--yes", ai=ai)

        assert run.result.exit_code == 1
        # The command caught the failure: there is no error for Typer to report.
        assert isinstance(run.result.exception, SystemExit)
        assert "Traceback" not in run.result.stdout
        assert "AI tagging stopped:" in run.out
        assert "AI tags: 40 tracks regenerated, 80 keep their previous tags." in run.out
        # The embeddings wait for the tags, so every track keeps its embedding.
        assert "Embeddings: 0 tracks regenerated, 120 keep their previous embedding." in run.out
        assert f"Run '{FORCE_COMMAND}' again to continue." in run.out
        assert SUCCESS_LINE not in run.out
        assert _retagged(library) == list(range(1, 41))
        assert _embedding_rows(library) == embeddings
        assert run.vectors.requests == []
        assert marker_path(library).exists()

    def test_embedding_failure_part_way_keeps_every_embedding(self, library: Path) -> None:
        """Spec "Provider fails part-way": new and previous embeddings side by side."""
        first = _run_force(library, "--yes", vectors=_Vectors(fail_on_request=2))

        assert first.result.exit_code == 1
        assert isinstance(first.result.exception, SystemExit)
        assert (
            "Embedding generation stopped: the embedding provider stopped answering." in first.out
        )
        assert "AI tags: 120 tracks regenerated." in first.out
        assert "Embeddings: 50 tracks regenerated, 70 keep their previous embedding." in first.out
        assert f"Run '{FORCE_COMMAND}' again to continue." in first.out
        assert SUCCESS_LINE not in first.out
        assert len(_reembedded(library)) == 50

        # The tags of that run are done: the run that continues pays for no tag again.
        second = _run_force(library, terminal=True, answers="y\n")

        assert second.result.exit_code == 0, second.result.output
        assert "This run continues it: 70 of 120 tracks remain." in second.out
        assert "Continue with the remaining 70 tracks? [y/N]" in second.out
        assert second.ai.requests == []
        assert second.vectors.requests == [50, 20]
        assert "AI tags: regenerated by the earlier run." in second.out
        assert "Embeddings: 70 tracks regenerated." in second.out
        assert f"✓ {SUCCESS_LINE}" in second.out
        assert len(_reembedded(library)) == LIBRARY_SIZE
        assert not marker_path(library).exists()

    def test_summary_states_regenerated_and_kept_counts(self, library: Path) -> None:
        """Spec "Part of the library regenerated": exit 0, both counts, no success line."""
        run = _run_force(library, "--yes", ai=_TaggingAI(omit=[7, 8, 9]))

        assert run.result.exit_code == 0, run.result.output
        assert "Force regeneration finished with warnings." in run.out
        assert "AI tags: 117 tracks regenerated, 3 keep their previous tags." in run.out
        assert "Embeddings: 120 tracks regenerated." in run.out
        assert SUCCESS_LINE not in run.out
        assert _retagged(library) == [n for n in range(1, LIBRARY_SIZE + 1) if n not in (7, 8, 9)]
        assert len(_reembedded(library)) == LIBRARY_SIZE

    def test_run_where_every_tag_request_fails_keeps_every_tag(self, library: Path) -> None:
        """Every request fails with an error that is not fatal by itself.

        After three in a row the run stops asking: the provider no longer answers.
        No tag changes, and no embedding is made from tags that were not replaced.
        Made anyway, all 120 would come from the text they were made from, and the
        run would end with status 0 although nothing new exists.
        """
        tags, embeddings = _tag_rows(library), _embedding_rows(library)
        ai = _TaggingAI(error=PASSING_ERROR)

        run = _run_force(library, "--yes", ai=ai)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        # Three batches of 20 were asked for, and none after them.
        assert ai.sent == list(range(1, 61))
        assert _tag_rows(library) == tags and _embedding_rows(library) == embeddings
        assert run.vectors.requests == []
        assert (
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            "Last error: connection refused." in run.out
        )
        assert "AI tags: 0 tracks regenerated, 120 keep their previous tags." in run.out
        assert "Embeddings: 0 tracks regenerated, 120 keep their previous embedding." in run.out
        assert f"Tip: Run '{FORCE_COMMAND}' again to continue." in run.out
        assert SUCCESS_LINE not in run.out and "finished with warnings" not in run.out
        assert _clearing(run.statements) == []
        # The run can be continued: its marker entry is still there.
        assert marker_path(library).exists()

    def test_provider_that_stops_answering_part_way_can_be_continued(self, library: Path) -> None:
        """Spec "Provider failure part-way", for an error that is not fatal by itself."""
        embeddings = _embedding_rows(library)
        ai = _TaggingAI(error=PASSING_ERROR, fail_from=3)

        first = _run_force(library, "--yes", ai=ai)

        assert first.result.exit_code == 1
        assert "AI tags: 40 tracks regenerated, 80 keep their previous tags." in first.out
        assert f"Tip: Run '{FORCE_COMMAND}' again to continue." in first.out
        assert ai.sent == list(range(1, 101))
        assert _retagged(library) == list(range(1, 41))
        assert _embedding_rows(library) == embeddings and first.vectors.requests == []

        second = _run_force(library, terminal=True, answers="y\n")

        assert second.result.exit_code == 0, second.result.output
        assert "This run continues it: 80 of 120 tracks remain." in second.out
        assert second.ai.sent == list(range(41, LIBRARY_SIZE + 1))
        assert SUCCESS_LINE in second.out
        assert len(_retagged(library)) == LIBRARY_SIZE
        assert len(_reembedded(library)) == LIBRARY_SIZE
        assert not marker_path(library).exists()

    def test_run_without_any_new_tag_exits_nonzero_and_makes_no_embedding(
        self, library: Path
    ) -> None:
        """The provider answers every request and gives nothing usable for any track."""
        tags, embeddings = _tag_rows(library), _embedding_rows(library)
        ai = _TaggingAI(omit=range(1, LIBRARY_SIZE + 1))

        run = _run_force(library, "--yes", ai=ai)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert ai.sent == list(range(1, LIBRARY_SIZE + 1))
        assert _tag_rows(library) == tags and _embedding_rows(library) == embeddings
        assert run.vectors.requests == []
        assert "No AI tags were regenerated, so no embedding was regenerated either." in run.out
        assert "AI tags: 0 tracks regenerated, 120 keep their previous tags." in run.out
        assert "Embeddings: 0 tracks regenerated, 120 keep their previous embedding." in run.out
        assert f"Tip: Run '{FORCE_COMMAND}' again to continue." in run.out
        assert SUCCESS_LINE not in run.out and "finished with warnings" not in run.out

    def test_force_says_so_when_no_index_could_be_saved(self, library: Path) -> None:
        """An index file that cannot be loaded or moved aside is never written over.

        The run then saved no search index, so it does not end with the success line.
        """
        index_path = library.parent / "test.index"
        index_path.write_bytes(b"this is not a search index")

        with patch(
            "plexmix.database.vector_index.os.rename", side_effect=PermissionError(13, "denied")
        ):
            run = _run_force(library, "--yes")

        assert run.result.exit_code == 0, run.result.output
        assert index_path.read_bytes() == b"this is not a search index"
        assert len(_reembedded(library)) == LIBRARY_SIZE
        assert "Could not be moved aside, so left where it is:" in run.out
        assert "Force regeneration finished with warnings." in run.out
        assert (
            "The search index was not saved in this run: a file that could not be loaded "
            "is still in its place. Move it away, then run 'plexmix embeddings generate'."
            in run.out
        )
        assert SUCCESS_LINE not in run.out

    def test_run_that_ends_with_tracks_in_the_way_saves_no_index_and_says_so(
        self, library: Path
    ) -> None:
        """The vector size changed, and the provider left 15 tracks out of its replies.

        The run reaches its end. Those 15 tracks hold the earlier size, so the last
        rebuild of the index is refused: the saved index stays as it is, byte for
        byte. Like ``sync regenerate``: no success line, the sentence with the count
        and the command, and status 0, because the run did not fail.
        """
        before = _seed_earlier_size(library)

        run = _run_force(library, "--yes", vectors=_Vectors(short=5))

        assert run.result.exit_code == 0, run.result.output
        assert run.result.exception is None
        assert "Force regeneration finished with warnings." in run.out
        assert "AI tags: 120 tracks regenerated." in run.out
        assert "Embeddings: 105 tracks regenerated, 15 keep their previous embedding." in run.out
        assert _index_sentence(15) in run.out
        assert run.out.count("The search index was left as it is") == 1
        assert SUCCESS_LINE not in run.out
        assert run.vectors.requests == [50, 50, 20]
        assert _embedding_sizes(library) == {4: 105, 8: 15}
        _assert_saved_index_is_untouched(library, before)
        # The tags are done and 15 embeddings are left: the embeddings entry of the
        # marker stays, so the next run makes only those and does not pay the
        # providers for the whole library again.
        assert set(json.loads(marker_path(library).read_text())) == {"embeddings"}

        again = _run_force(library, "--yes")

        assert again.result.exit_code == 0, again.result.output
        assert again.ai.requests == [] and again.vectors.requests == [15]
        assert f"✓ {SUCCESS_LINE}" in again.out
        assert "The search index was left as it is" not in again.out
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()

    def test_run_stopped_after_a_size_change_keeps_the_index_and_is_continued_to_a_whole_one(
        self, library: Path
    ) -> None:
        before = _seed_earlier_size(library)

        first = _run_force(library, "--yes", vectors=_Vectors(fail_on_request=2))

        assert first.result.exit_code == 1
        assert "Embeddings: 50 tracks regenerated, 70 keep their previous embedding." in first.out
        # The stop is what is reported. Nothing is said of the index, and it is untouched.
        assert "The search index was left as it is" not in first.out
        assert SUCCESS_LINE not in first.out
        assert _embedding_sizes(library) == {4: 50, 8: 70}
        _assert_saved_index_is_untouched(library, before)

        second = _run_force(library, "--yes")

        assert second.result.exit_code == 0, second.result.output
        assert second.ai.requests == [] and second.vectors.requests == [50, 20]
        assert f"✓ {SUCCESS_LINE}" in second.out
        assert _embedding_sizes(library) == {4: 120}
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))
        assert not marker_path(library).exists()

    def test_run_whose_index_was_left_as_it_is_gets_no_success_line(self, library: Path) -> None:
        """Every track was regenerated, and the engine says that its last rebuild of
        the index was refused. The run did its work, and it is not called complete."""
        engine = MagicMock()
        engine.regenerate_result = _regenerated(tags=120, embeddings=120)
        engine.regenerate_result.index_left_out = 7

        run = _run_force(library, "--yes", engine=engine)

        assert run.result.exit_code == 0, run.result.output
        assert "Force regeneration finished with warnings." in run.out
        assert "AI tags: 120 tracks regenerated." in run.out
        assert "Embeddings: 120 tracks regenerated." in run.out
        assert _index_sentence(7) in run.out
        assert SUCCESS_LINE not in run.out and "complete" not in run.out.lower()
        assert "again to continue" not in run.out

    def test_engine_gets_the_keys_from_the_settings(self, library: Path) -> None:
        """Not every provider object shows its key, so the engine is handed them."""
        engine = MagicMock()
        engine.regenerate_result = _regenerated()

        with patch("plexmix.cli.doctor_cmd.provider_secrets", return_value=[DUMMY_KEY]):
            run = _run_force(library, "--yes", engine=engine)

        assert run.result.exit_code == 0, run.result.output
        assert run.engine_cls.call_args.kwargs == {"known_secrets": [DUMMY_KEY]}
        assert DUMMY_KEY not in run.result.output

    def test_run_that_rebuilt_nothing_exits_nonzero(self, library: Path) -> None:
        engine = MagicMock()
        engine.regenerate_result = _regenerated(
            tags=0, tags_kept=120, embeddings=0, embeddings_kept=120
        )

        run = _run_force(library, "--yes", engine=engine)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert "No AI tags or embeddings were regenerated." in run.out
        assert "AI tags: 0 tracks regenerated, 120 keep their previous tags." in run.out
        assert "Embeddings: 0 tracks regenerated, 120 keep their previous embedding." in run.out
        assert SUCCESS_LINE not in run.out
        assert "complete" not in run.out.lower()

    def test_run_without_a_result_is_not_a_success(self, library: Path) -> None:
        """Only a real result of the engine counts: without one nothing is called a success."""
        engine = MagicMock()

        run = _run_force(library, "--yes", engine=engine)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert SUCCESS_LINE not in run.out

    def test_engine_runs_without_plex_and_with_the_providers_of_the_question(
        self, library: Path
    ) -> None:
        engine = MagicMock()
        engine.regenerate_result = _regenerated()

        run = _run_force(library, "--yes", engine=engine)

        assert run.result.exit_code == 0, run.result.output
        # No Plex client, the open database, and the two providers that passed the check.
        plex, db, generator, index, ai = run.engine_cls.call_args.args
        assert plex is None
        assert isinstance(db, SQLiteManager) and db.db_path == library
        assert (generator, ai) == (run.vectors, run.ai)
        assert isinstance(index, VectorIndex) and index.dimension == len(NEW_VECTOR)
        # The run marker gets the provider and model that the question named.
        engine.regenerate_local.assert_called_once_with(
            generate_embeddings=True,
            tags_provider=run_provider(run.settings, SCOPE_TAGS),
            embeddings_provider=run_provider(run.settings, SCOPE_EMBEDDINGS),
        )
        # It is not a sync: no Typer command and no other engine method was used.
        engine.regenerate_sync.assert_not_called()
        engine.incremental_sync.assert_not_called()

    def test_engine_refusal_is_printed_not_raised(self, library: Path) -> None:
        """The engine's own guard ends the command with the refusal, not with an error report."""
        engine = MagicMock()
        engine.regenerate_local.side_effect = RegenerateRefused.not_given(COMPONENT_AI)

        run = _run_force(library, "--yes", engine=engine)

        assert run.result.exit_code == 1
        assert isinstance(run.result.exception, SystemExit)
        assert "The AI provider is not ready: none was given to this run." in run.out
        assert "Nothing was changed." in run.out

    def test_interrupt_during_the_provider_check_changes_nothing(self, library: Path) -> None:
        tags = _tag_rows(library)

        run = _run_force(library, "--yes", refused=KeyboardInterrupt())

        assert run.result.exit_code == 130
        assert "Interrupted during the provider check. Nothing was changed." in run.out
        assert _writes(run.statements) == []
        assert _tag_rows(library) == tags
        assert not marker_path(library).exists()

    def test_error_part_way_says_what_was_regenerated(self, library: Path) -> None:
        error = RuntimeError("the database is locked")
        engine = MagicMock()
        engine.regenerate_result = _regenerated(
            tags=40, tags_kept=80, embeddings=0, embeddings_kept=120, unfinished=True
        )
        engine.regenerate_local.side_effect = error

        run = _run_force(library, "--yes", engine=engine)

        # The error itself is still reported. What the run had saved is said first.
        assert run.result.exception is error
        assert "AI tags: 40 tracks regenerated, 80 keep their previous tags." in run.out
        assert f"Run '{FORCE_COMMAND}' again to continue." in run.out
        assert SUCCESS_LINE not in run.out

    @pytest.mark.parametrize(
        "error, exit_code", [(RuntimeError("no space left"), 1), (KeyboardInterrupt(), 130)]
    )
    def test_stop_after_both_passes_ended_gives_no_tip_to_continue(
        self, library: Path, error: BaseException, exit_code: int
    ) -> None:
        """Nothing is left to continue then, and the same command would start over.
        Only the search index can be behind, and another command rebuilds it."""
        engine = MagicMock()
        engine.regenerate_result = _regenerated(tags=120, embeddings=120)
        engine.regenerate_local.side_effect = error

        run = _run_force(library, "--yes", engine=engine)

        assert run.result.exit_code == exit_code
        assert "AI tags: 120 tracks regenerated." in run.out
        assert "Embeddings: 120 tracks regenerated." in run.out
        assert "again to continue" not in run.out
        assert "Run 'plexmix embeddings generate' to rebuild the search index." in run.out
        assert SUCCESS_LINE not in run.out

    @pytest.mark.parametrize("tracks", [None, 0], ids=["no-database", "no-tracks"])
    def test_library_without_tracks_has_nothing_to_do(
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

        run = _run_force(database_file, terminal=True, answers="y\n")

        assert run.result.exit_code == 0, run.result.output
        assert "The PlexMix library holds no tracks, so there is nothing to regenerate." in run.out
        assert "Run 'plexmix sync' first." in run.out
        assert "[y/N]" not in run.out
        assert SUCCESS_LINE not in run.out
        run.check.assert_not_called()
        assert _writes(run.statements) == []
        assert run.ai.requests == [] and run.vectors.requests == []
        # Looking at a library that does not exist yet did not create one, and no
        # run marker was left behind.
        assert sorted(path.name for path in tmp_path.iterdir()) == files
        assert not marker_path(database_file).exists()

    def test_force_reports_a_moved_index_and_saves_a_new_one(self, library: Path) -> None:
        index_path = library.parent / "test.index"
        index = VectorIndex(dimension=len(OLD_VECTOR), index_path=str(index_path))
        index.build_index([OLD_VECTOR, OLD_VECTOR], [1, 2])
        index.save_index(str(index_path))
        index_path.with_suffix(".metadata").write_bytes(b"")

        run = _run_force(library, "--yes")

        assert run.result.exit_code == 0, run.result.output
        assert "The search index could not be loaded." in run.out
        moved = sorted(library.parent.glob("test.*.unreadable-*"))
        assert [path.name.split(".unreadable-")[0] for path in moved] == [
            "test.index",
            "test.metadata",
        ]
        # Each file is named in one piece, before the run starts.
        before_run = run.result.stdout.split("Regenerating AI tags and embeddings...")[0]
        assert all(str(path) in before_run for path in moved)
        # The run goes on and saves a new index beside the files that were moved aside.
        assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))

    def test_only_a_real_true_forces(self, tmp_path: Path) -> None:
        """Called as a plain function, an option that is left out is a truthy placeholder.
        It must not start a force run."""
        placeholder = OptionInfo(default=False)
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "plexmix.cli.doctor_cmd.Settings.load_from_file",
                    return_value=_make_settings(tmp_path),
                )
            )
            mock_sm_cls = stack.enter_context(patch("plexmix.cli.doctor_cmd.SQLiteManager"))
            stack.enter_context(patch("plexmix.cli.doctor_cmd.typer.confirm", return_value=False))
            force = stack.enter_context(patch("plexmix.cli.doctor_cmd._force_regenerate"))
            _make_cursor(_mock_sqlite_manager(mock_sm_cls)).fetchone.side_effect = [
                (100,),
                (100,),
                (0,),
            ]

            doctor_cmd.doctor(force=placeholder, yes=placeholder)
            force.assert_not_called()

            # A real True does, and the placeholder for ``--yes`` is not a yes.
            doctor_cmd.doctor(force=True, yes=placeholder)
            force.assert_called_once_with(False)

    def test_help_lists_yes(self) -> None:
        result = runner.invoke(app, ["doctor", "--help"])

        assert result.exit_code == 0
        # The plain text: a coloured help page splits the option name with colour codes.
        text = _flat(click.unstyle(result.output))
        assert "--force" in text
        assert "--yes" in text
        assert "Nothing is cleared first" in text


# ---------------------------------------------------------------------------
# The search index that could not be loaded
# ---------------------------------------------------------------------------


def test_doctor_reports_a_moved_index_before_it_rebuilds_it(tmp_path: Path) -> None:
    """The repair of missing embeddings builds the index too, and says when the saved
    one could not be loaded. The stored embeddings rebuild it."""
    database_file = tmp_path / "test.db"
    with SQLiteManager(str(database_file)) as db:
        db.create_tables()
        artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist One"))
        album_id = db.insert_album(Album(plex_key="al1", title="Album One", artist_id=artist_id))
        for number in (1, 2, 3):
            db.insert_track(
                Track(
                    plex_key=f"t{number}",
                    title=f"Song {number}",
                    artist_id=artist_id,
                    album_id=album_id,
                    play_count=0,
                    tags="calm",
                )
            )
    index_path = tmp_path / "test.index"
    index_path.write_bytes(b"this is not a search index")

    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd.Settings.load_from_file",
                return_value=_make_settings(tmp_path),
            )
        )
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd._build_embedding_generator_with_reason",
                return_value=(_Vectors(), None),
            )
        )
        # "Regenerate embeddings now?" -> Yes, "Run a sync ...?" -> No
        stack.enter_context(
            patch("plexmix.cli.doctor_cmd.typer.confirm", side_effect=[True, False])
        )
        result = runner.invoke(app, ["doctor"])

    out = _flat(result.stdout)
    assert result.exit_code == 0, result.output
    assert "The search index could not be loaded." in out
    assert "To rebuild the search index from them, run 'plexmix embeddings generate'." in out
    [moved] = tmp_path.glob("test.index.unreadable-*")
    assert moved.read_bytes() == b"this is not a search index"
    assert str(moved) in result.stdout
    assert "Successfully generated 3 embeddings!" in out
    index = VectorIndex(dimension=len(NEW_VECTOR), index_path=str(index_path))
    assert index.load_failed is False and sorted(index.track_ids) == [1, 2, 3]


def _run_doctor_repair(library: Path, vectors: _Vectors) -> Any:
    """Run plain ``doctor`` and answer its questions for the repair of missing embeddings.

    "Regenerate embeddings now?" is answered with Yes, and the sync question after
    it with No. Every track has tags, so the tags question is not asked.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd.Settings.load_from_file",
                return_value=_force_settings(library),
            )
        )
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd._build_embedding_generator_with_reason",
                return_value=(vectors, None),
            )
        )
        confirm = stack.enter_context(
            patch("plexmix.cli.doctor_cmd.typer.confirm", side_effect=[True, False])
        )
        result = runner.invoke(app, ["doctor"])
    assert confirm.call_count == 2, result.output
    return result


def test_doctor_repair_leaves_the_saved_index_while_tracks_hold_another_vector_size(
    library: Path,
) -> None:
    """The vector size was changed, and 20 tracks have no embedding.

    The repair makes those 20 in the new size. It built the index from the rows
    itself and saved it. An index of 20 tracks would then take the place of the
    index of the library, and the dimension mismatch that tells ``plexmix create``
    to stop would go with it. Now the one guarded rebuild is asked: nothing is
    built, the saved index stays byte for byte, and the sentence says what to run.
    """
    before = _seed_earlier_size(library, track_ids=range(1, 101))
    vectors = _Vectors()

    result = _run_doctor_repair(library, vectors)

    out = _flat(result.stdout)
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "Found 20 tracks without embeddings" in out
    assert "Successfully generated 20 embeddings!" in out
    assert _index_sentence(100) in out
    assert out.count("The search index was left as it is") == 1
    # Nothing calls the index or the database healthy, rebuilt or saved.
    assert "healthy" not in out.lower()
    assert "index rebuilt" not in out.lower() and "index saved" not in out.lower()
    assert vectors.requests == [20]
    assert _embedding_sizes(library) == {4: 20, 8: 100}
    _assert_saved_index_is_untouched(library, before)


def _run_doctor_repair_with_failed_provider(
    library: Path, failure: Optional[ProviderFailure]
) -> Any:
    """Run plain ``doctor``, answer Yes to "Regenerate embeddings now?", and let the
    embedding provider fail to start. The command stops there, so one question is asked."""
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd.Settings.load_from_file",
                return_value=_force_settings(library),
            )
        )
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd._build_embedding_generator_with_reason",
                return_value=(None, failure),
            )
        )
        confirm = stack.enter_context(
            patch("plexmix.cli.doctor_cmd.typer.confirm", side_effect=[True])
        )
        result = runner.invoke(app, ["doctor"])
    assert confirm.call_count == 1, result.output
    return result


def test_doctor_repair_names_the_cause_when_the_embedding_provider_cannot_start(
    library: Path,
) -> None:
    """Tracks need embeddings, the user says yes, and the local model does not load. The
    command names that, as ``embeddings generate`` does, and does not ask for an API key."""
    with SQLiteManager(str(library)) as db:
        db.get_connection().execute("DELETE FROM embeddings WHERE track_id > 100")
        db._commit()
    embeddings = _embedding_rows(library)
    failure = ProviderFailure(
        Cause.MODEL_LOAD_FAILED,
        "local",
        detail="Failed to load local model all-MiniLM-L6-v2: the worker stopped",
    )

    result = _run_doctor_repair_with_failed_provider(library, failure)

    out = _flat(result.stdout)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "The local model couldn't load" in out
    # The same lines that every other command prints for this failure.
    for line in embedding_failure_lines(failure):
        assert _flat(line) in out
    assert "api key" not in out.lower()
    assert "Successfully generated" not in out
    # Nothing was written.
    assert _embedding_rows(library) == embeddings


def test_doctor_repair_still_says_a_missing_key_is_required(library: Path) -> None:
    with SQLiteManager(str(library)) as db:
        db.get_connection().execute("DELETE FROM embeddings WHERE track_id > 100")
        db._commit()

    result = _run_doctor_repair_with_failed_provider(
        library, ProviderFailure(Cause.MISSING_API_KEY, "openai")
    )

    out = _flat(result.stdout)
    assert result.exit_code == 1
    assert "API key required for embedding provider." in out
    assert "Run: plexmix config init" in out


def test_doctor_repair_with_one_vector_size_rebuilds_the_index(library: Path) -> None:
    """The healthy path is what it was: the index is rebuilt from every row."""
    with SQLiteManager(str(library)) as db:
        db.get_connection().execute("DELETE FROM embeddings WHERE track_id > 100")
        db._commit()
    vectors = _Vectors()

    result = _run_doctor_repair(library, vectors)

    out = _flat(result.stdout)
    assert result.exit_code == 0, result.output
    assert "Successfully generated 20 embeddings!" in out
    assert "The search index was left as it is" not in out
    assert vectors.requests == [20]
    assert _ids_in_saved_index(library) == list(range(1, LIBRARY_SIZE + 1))


def _run_plain_doctor(library: Path) -> Any:
    """Run plain ``doctor`` on a library where no track lacks an embedding.

    The one question it asks (a sync, to look for deleted tracks) is answered No.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("plexmix.cli.main.setup_logging"))
        stack.enter_context(
            patch(
                "plexmix.cli.doctor_cmd.Settings.load_from_file",
                return_value=_force_settings(library),
            )
        )
        confirm = stack.enter_context(
            patch("plexmix.cli.doctor_cmd.typer.confirm", return_value=False)
        )
        result = runner.invoke(app, ["doctor"])
    assert confirm.call_count == 1, result.output
    return result


def test_doctor_does_not_call_a_library_healthy_while_tracks_hold_another_vector_size(
    library: Path,
) -> None:
    """Every track has a row, and every row is of the vector size before the change.

    The row counts alone made that "healthy". The saved index is of the old size
    too, and every command refuses it, so the health check says what to run.
    """
    before = _seed_earlier_size(library)
    rows = _embedding_rows(library)

    result = _run_plain_doctor(library)

    out = _flat(result.stdout)
    assert result.exit_code == 0, result.output
    assert "Tracks with embeddings: 120" in out and "Orphaned embeddings: 0" in out
    assert "healthy" not in out.lower()
    assert _index_sentence(LIBRARY_SIZE) in out
    assert out.count("The search index was left as it is") == 1
    # Looking at the library changes nothing.
    assert _embedding_rows(library) == rows
    _assert_saved_index_is_untouched(library, before)


def test_doctor_calls_a_library_whose_rows_fit_the_settings_healthy(library: Path) -> None:
    """The healthy path is what it was: rows of the model in use, in the size it makes now."""
    with SQLiteManager(str(library)) as db:
        # What the settings give the provider that ``_force_settings`` names.
        size = _force_settings(library).embedding.get_dimension_for_provider(VECTOR_MODEL)
        db.get_connection().execute("UPDATE embeddings SET embedding_dim = ?", (size,))
        db._commit()

    result = _run_plain_doctor(library)

    out = _flat(result.stdout)
    assert result.exit_code == 0, result.output
    assert "Database is healthy!" in out
    assert "The search index was left as it is" not in out
