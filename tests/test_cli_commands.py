"""Integration tests for CLI commands using typer.testing.CliRunner.

Each test class focuses on one command group and heavily mocks external
dependencies (Plex, AI providers, credentials, database) so tests run
quickly without network or file-system side-effects.
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
from plexmix.database.models import Track, SyncHistory

runner = CliRunner()


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
        mock_connect_plex.side_effect = PlexConnectionError(
            "Failed to connect to Plex server."
        )

        result = runner.invoke(app, ["sync", "incremental"])
        assert result.exit_code == 1
        assert "failed to connect" in result.output.lower()


# ---------------------------------------------------------------------------
# embeddings generate
# ---------------------------------------------------------------------------


class TestEmbeddingsGenerate:
    """Tests for `plexmix embeddings generate`."""

    @patch("plexmix.cli.embeddings_cmd.SQLiteManager")
    @patch("plexmix.cli.embeddings_cmd._build_embedding_generator")
    @patch("plexmix.cli.embeddings_cmd.Settings.load_from_file")
    def test_no_tracks_need_embeddings(self, mock_load, mock_build_emb, mock_db_cls, tmp_path):
        settings = _make_settings(tmp_path)
        mock_load.return_value = settings

        mock_gen = MagicMock()
        mock_gen.get_dimension.return_value = 1536
        mock_build_emb.return_value = mock_gen

        mock_db = MagicMock()
        mock_db.get_all_tracks.return_value = [_dummy_track(1)]
        mock_db.get_embedding_by_track_id.return_value = MagicMock()  # embedding exists
        mock_db.__enter__ = MagicMock(return_value=mock_db)
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db_cls.return_value = mock_db

        result = runner.invoke(app, ["embeddings", "generate"])
        assert result.exit_code == 0
        assert "all tracks already have embeddings" in result.output.lower()

    @patch("plexmix.cli.embeddings_cmd.rebuild_vector_index", return_value=2)
    @patch("plexmix.cli.embeddings_cmd.generate_embeddings_for_tracks", return_value=2)
    @patch("plexmix.cli.embeddings_cmd.build_vector_index")
    @patch("plexmix.cli.embeddings_cmd.SQLiteManager")
    @patch("plexmix.cli.embeddings_cmd._build_embedding_generator")
    @patch("plexmix.cli.embeddings_cmd.Settings.load_from_file")
    def test_generate_for_n_tracks(
        self, mock_load, mock_build_emb, mock_db_cls, mock_bvi, mock_gen_emb, mock_rebuild,
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
        mock_settings_cls.return_value = settings

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
        mock_settings_cls.return_value = settings

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
