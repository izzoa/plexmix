"""Tests for the `plexmix doctor` CLI command.

The doctor command performs database health checks: detecting orphaned
embeddings, missing embeddings, untagged tracks, and offers a --force mode
to wipe and regenerate everything.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

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
