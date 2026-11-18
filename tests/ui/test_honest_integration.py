"""Cross-surface checks for webui-honest-status-and-first-run (integration step).

The Dashboard status strip, the Doctor connection rows, the Generator readiness line
and the rail badge all read the one ``AppState.readiness`` snapshot (webui-readiness:
"One readiness source for every status surface"). These tests build that snapshot
once with the real evaluator and check that every surface reports the same state and
the same cause for it.
"""

from pathlib import Path

import pytest

from plexmix.config.settings import Settings
from plexmix.services import readiness as rd
from plexmix.services.readiness import (
    Cause,
    Facts,
    Level,
    doctor_rows,
    evaluate,
    fingerprint,
    readiness_store,
    status_snapshot,
)
from plexmix.ui.states.doctor_state import build_row_map
from plexmix.ui.states.generator_state import generation_status

_ENV_KEYS = (
    "PLEX_TOKEN",
    "PLEX_URL",
    "PLEX_LIBRARY_NAME",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "COHERE_API_KEY",
    "AI_DEFAULT_PROVIDER",
    "EMBEDDING_DEFAULT_PROVIDER",
    "EMBEDDING_MODEL",
    "DATABASE_PATH",
)

LOCAL_PROFILE = (
    "plex:\n  url: http://plex.local:32400\n  library_name: Music\n"
    "embedding:\n  default_provider: local\n  model: all-MiniLM-L6-v2\n"
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("plexmix.config.credentials._get_keyring", lambda: None)
    readiness_store.clear()
    yield
    readiness_store.clear()


def _settings(tmp_path: Path, body: str) -> Settings:
    config = tmp_path / "config.yaml"
    config.write_text(
        "database:\n"
        f"  path: {tmp_path / 'plexmix.db'}\n"
        f"  faiss_index_path: {tmp_path / 'embeddings.index'}\n" + body
    )
    return Settings.load_from_file(str(config))


def _facts(settings: Settings, **overrides: object) -> Facts:
    base: dict = dict(
        db_exists=True,
        total_tracks=5943,
        untagged_tracks=0,
        embedded_tracks=5943,
        index_exists=True,
        index_dimension=384,
        index_vector_count=5943,
        expected_dimension=384,
        local_deps_available=True,
        plex_token_present=True,
    )
    base.update(overrides)
    facts = Facts(**base)
    facts.fingerprints = {
        name: fingerprint(name, settings, None)
        for name in ("plex", "ai", "embeddings", "musicbrainz")
    }
    return facts


def _surfaces(snapshot: dict) -> tuple:
    """(Doctor connection row, Generator readiness line) for one snapshot."""
    rows = doctor_rows({"total_tracks": 5943}, snapshot, {"scan_state": "done"})
    doctor = build_row_map(rows, {}, snapshot)["conn_embeddings"]
    line = generation_status(snapshot, loaded=True, total=5943, embedded=5943)
    return doctor, line


class TestSurfacesAgree:
    def test_failing_provider_has_the_same_cause_everywhere(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, LOCAL_PROFILE)
        facts = _facts(settings)
        # A real generation failed because the model could not load.
        readiness_store.record(
            "embeddings",
            Level.FAILING,
            Cause.MODEL_LOAD_FAILED,
            message="model failed to load",
            fingerprint=facts.fingerprints["embeddings"],
        )
        snapshot = status_snapshot(evaluate(settings, facts))
        strip = snapshot["embeddings"]  # what the Dashboard status strip renders
        assert strip["level"] == "failing" and strip["tone"] == "error"
        assert strip["title"]

        doctor, line = _surfaces(snapshot)
        assert doctor["state_word"] == strip["state"] == "Failing"
        assert doctor["tone"] == strip["tone"]
        assert doctor["summary"] == strip["title"]
        assert line["component"] == "embeddings"
        assert line["cause"] == strip["cause"] == Cause.MODEL_LOAD_FAILED.value
        assert line["title"] == strip["title"]
        # A past failure warns but does not block (generator-showpiece spec).
        assert line["blocked"] == "false" and rd.can_generate(snapshot) is True
        assert rd.needs_attention(snapshot) is True

    def test_blocking_cause_has_the_same_cause_everywhere(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, LOCAL_PROFILE)
        snapshot = status_snapshot(evaluate(settings, _facts(settings, local_deps_available=False)))
        strip = snapshot["embeddings"]
        assert strip["cause"] == Cause.MISSING_DEPENDENCY.value
        assert strip["blocking"] == "true"

        doctor, line = _surfaces(snapshot)
        assert doctor["summary"] == strip["title"]
        assert doctor["tone"] == strip["tone"]
        assert line["title"] == strip["title"] and line["cause"] == strip["cause"]
        assert line["blocked"] == "true" and rd.can_generate(snapshot) is False
        assert rd.needs_attention(snapshot) is True
        assert "API key" not in strip["title"] + strip["body"]

    def test_unchecked_providers_with_a_usable_index_show_no_badge(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, LOCAL_PROFILE)
        snapshot = status_snapshot(evaluate(settings, _facts(settings)))
        doctor, line = _surfaces(snapshot)
        assert snapshot["embeddings"]["tone"] != "error"
        assert doctor["tone"] == snapshot["embeddings"]["tone"]
        assert line["issue"] == "false" and line["blocked"] == "false"
        assert rd.needs_attention(snapshot) is False


class TestStoredTimesAgree:
    def test_library_checked_time_matches_the_ui_reading(self, tmp_path: Path) -> None:
        """readiness reads ``last_sync`` as naive UTC, like AppState's ``stored_utc``."""
        from plexmix.ui.states.app_state import _local_iso, stored_utc

        settings = _settings(tmp_path, LOCAL_PROFILE)
        facts = _facts(settings, last_sync="2025-12-27 20:35:19.327064")
        library = status_snapshot(evaluate(settings, facts))["library"]
        synced = stored_utc(facts.last_sync)
        assert synced is not None
        assert library["checked_at"] == _local_iso(synced)


class TestFailureLogsCarryNoSecrets:
    """Integration finding: two failure paths logged raw exception text."""

    def test_tagging_failure_log_redacts_the_ai_key(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import asyncio
        import logging
        from unittest.mock import MagicMock, patch

        from plexmix.services import readiness as readiness_module
        from plexmix.ui.job_manager import task_store
        from plexmix.ui.states.tagging_state import (
            TaggingState,
            normalize_tag_filters,
            tag_filter_signature,
        )

        secret = "sk-SENTINEL-integration-0001"

        class _Harness(TaggingState):  # type: ignore[misc, valid-type]
            async def __aenter__(self) -> "_Harness":
                return self

            async def __aexit__(self, *exc: object) -> bool:
                return False

            def access_allowed(self) -> bool:
                return True

        state = _Harness()
        state.preview_count = 3
        state._preview_signature = tag_filter_signature(
            normalize_tag_filters("", "", "", "", False, "")
        )
        db = MagicMock()
        db.get_tracks_by_filter.side_effect = RuntimeError(f"500 upstream error for key {secret}")
        monkeypatch.setattr(readiness_module, "resolve_secret", lambda component, s: secret)
        task_store.clear("tagging")
        caplog.set_level(logging.ERROR)
        try:
            with patch(
                "plexmix.config.settings.Settings.load_from_file", return_value=MagicMock()
            ), patch("plexmix.database.sqlite_manager.SQLiteManager", return_value=db):
                asyncio.run(state.start_tagging())
            entry = task_store.get("tagging")
            assert entry is not None and entry.status == "failed"
            assert secret not in entry.message
            assert "Tagging failed" in caplog.text
            assert secret not in caplog.text
        finally:
            task_store.clear("tagging")

    def test_unreadable_settings_log_no_config_text(self) -> None:
        from plexmix.ui.states.app_state import config_error_text

        assert config_error_text(ValueError("token sk-SENTINEL-x")) == "ValueError"
        assert "Permission denied" in config_error_text(PermissionError("Permission denied"))

    def test_settings_environment_error_names_the_variable_not_the_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.config.settings import Settings, SettingsEnvError
        from plexmix.ui.states.app_state import config_error_text

        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        monkeypatch.setenv("AUDIO_WORKERS", "SENTINELfour")
        with pytest.raises(SettingsEnvError) as info:
            Settings.load_from_file(str(config_file))
        text = config_error_text(info.value)
        assert "AUDIO_WORKERS" in text and "whole number" in text
        assert "SENTINELfour" not in text

    def test_yaml_errors_drop_the_quoted_line(self) -> None:
        import yaml

        from plexmix.ui.states.app_state import config_error_text
        from plexmix.ui.utils.helpers import safe_error_text

        secret = "SENTINELtoken0001"
        with pytest.raises(yaml.YAMLError) as info:
            yaml.safe_load(f"plex:\n  token: {secret}: [unclosed\n")
        assert secret in str(info.value)  # PyYAML quotes the offending line
        for text in (safe_error_text(info.value), config_error_text(info.value)):
            assert secret not in text
            assert text.startswith("Invalid YAML:") and "line 2" in text
