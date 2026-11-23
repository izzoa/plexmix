"""fix-local-embedding-crash: the Settings buttons that load a local model.

"Test Embeddings" with Local, "Download / Cache Model" and "Download / Warm Up Model" run the
model in a short-lived worker process, so the server never imports torch. These tests cover
what each handler shows and records for every outcome. They patch ``probe_local_embedding_model``
and ``warm_up_local_llm``, so no process starts, and ``huggingface_hub.snapshot_download``, so
no download starts. Reflex is mocked (tests/ui/conftest.py); every test runs on a scratch
``PLEXMIX_DATA_DIR`` with its own config.yaml and no keyring.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest

from plexmix.services import readiness as rd
from plexmix.services.readiness import readiness_store

_ENV_PREFIXES = ("PLEX_", "AI_", "EMBEDDING_", "DATABASE_", "PLEXMIX_")
TIMEOUT_SENTENCE = "Local model all-MiniLM-L6-v2 did not finish loading within 600 seconds"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Scratch data dir with a config.yaml, no keyring, no env config, a clean store."""
    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("{}\n")  # never fall back to ~/.plexmix/config.yaml
    monkeypatch.setenv("PLEXMIX_DATA_DIR", str(data))
    monkeypatch.setattr("plexmix.config.credentials._get_keyring", lambda: None)
    readiness_store.clear()
    yield data
    readiness_store.clear()


class _Form:
    """A SettingsState stand-in for the ``_settings_testing`` and ``_settings_downloads`` helpers."""

    def __init__(self, **values: Any) -> None:
        self.testing_connection = False
        self.plex_test_status = self.ai_test_status = self.embedding_test_status = ""
        self.plex_url = "http://plex.local:32400"
        self.plex_library = "Music"
        self.ai_provider = "local"
        self.ai_model = "google/gemma-3-1b-it"
        self.ai_custom_endpoint = self.ai_custom_model = self.ai_local_endpoint = ""
        self.ai_local_mode = "builtin"
        self.embedding_provider = "local"
        self.embedding_model = "all-MiniLM-L6-v2"
        self.embedding_dimension = 384
        self.embedding_custom_endpoint = self.embedding_custom_model = ""
        self.embedding_custom_dimension = 1536
        self.is_downloading_local_model = False
        self.local_download_status = ""
        self.local_download_progress = 0
        self.is_downloading_local_llm = False
        self.local_llm_download_status = ""
        self.local_llm_download_progress = 0
        for key, value in values.items():
            setattr(self, key, value)

    def _resolve_secret(self, field: str) -> str:
        return ""

    async def __aenter__(self) -> "_Form":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def _run_test(form: _Form, probe: Any) -> None:
    from plexmix.ui.states._settings_testing import test_embedding_provider_impl

    with patch("plexmix.utils.embeddings.probe_local_embedding_model", probe):
        asyncio.run(test_embedding_provider_impl(form))


def _record() -> Any:
    rec = readiness_store.get("embeddings")
    assert rec is not None, "the test recorded nothing"
    return rec


# ── _detect_local_deps ───────────────────────────────────────────────────


class TestDetectLocalDeps:
    def _state(self) -> Any:
        from plexmix.ui.states.settings_state import SettingsState

        return SettingsState()

    @pytest.mark.parametrize("available", [True, False])
    def test_the_flag_comes_from_the_helper(self, available: bool) -> None:
        state = self._state()
        with patch(
            "plexmix.utils.embeddings.local_embedding_deps_available", return_value=available
        ):
            state._detect_local_deps()
        assert state.local_deps_available is available

    def test_it_never_imports_the_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        was_loaded = "sentence_transformers" in sys.modules
        state = self._state()
        state._detect_local_deps()
        assert isinstance(state.local_deps_available, bool)
        if not was_loaded:
            assert "sentence_transformers" not in sys.modules

    def test_a_stub_without_a_spec_reads_as_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "sentence_transformers", MagicMock())
        state = self._state()
        state._detect_local_deps()
        assert state.local_deps_available is False

    def test_the_two_notices_still_read_the_flag(self) -> None:
        text = (
            Path(__file__).resolve().parents[2] / "src/plexmix/ui/pages/_settings_sections.py"
        ).read_text()
        assert text.count("local_deps_available") >= 2


# ── "Test Embeddings" with Local ─────────────────────────────────────────


class TestLocalEmbeddingTest:
    def test_success_is_ready(self) -> None:
        form = _Form()
        probe = MagicMock(return_value=384)
        _run_test(form, probe)
        probe.assert_called_once()
        assert probe.call_args.args[0] == "all-MiniLM-L6-v2"
        assert isinstance(probe.call_args.args[1], str)
        assert form.embedding_test_status == "✓ Embedding test successful (dimension: 384)"
        assert _record().level == rd.Level.READY
        assert form.testing_connection is False

    def test_another_dimension_warns_with_both_sizes_and_stays_ready(self) -> None:
        form = _Form(embedding_dimension=1024)
        _run_test(form, MagicMock(return_value=384))
        assert form.embedding_test_status.startswith("⚠ Embeddings work, but dimension mismatch")
        assert "got 384" in form.embedding_test_status
        assert "expected 1024" in form.embedding_test_status
        assert _record().level == rd.Level.READY
        assert form.testing_connection is False

    def test_a_missing_package_is_a_missing_dependency(self) -> None:
        form = _Form()
        _run_test(form, MagicMock(side_effect=ImportError("sentence-transformers not installed")))
        rec = _record()
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.MISSING_DEPENDENCY)
        assert form.embedding_test_status.startswith("✗")
        assert form.testing_connection is False

    def test_a_timeout_shows_its_sentence_and_records_model_load_failed(self) -> None:
        form = _Form()
        _run_test(form, MagicMock(side_effect=TimeoutError(TIMEOUT_SENTENCE)))
        assert form.embedding_test_status == (
            f"✗ {TIMEOUT_SENTENCE}. Use Download first, or try a smaller model."
        )
        assert "Connection timed out" not in form.embedding_test_status
        assert "server URL" not in form.embedding_test_status
        rec = _record()
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.MODEL_LOAD_FAILED)
        assert rec.cause != rd.Cause.TIMEOUT
        assert form.testing_connection is False

    @pytest.mark.parametrize(
        "message",
        [
            "Failed to load local model all-MiniLM-L6-v2: no such file",
            "Failed to start local embedding worker for all-MiniLM-L6-v2. See logs for details.",
        ],
    )
    def test_a_load_failure_is_recorded_as_model_load_failed(self, message: str) -> None:
        form = _Form()
        _run_test(form, MagicMock(side_effect=RuntimeError(message)))
        assert form.embedding_test_status.startswith("✗")
        rec = _record()
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.MODEL_LOAD_FAILED)
        assert form.testing_connection is False

    def test_another_runtime_error_keeps_todays_recorded_cause(self) -> None:
        form = _Form()
        _run_test(form, MagicMock(side_effect=RuntimeError("something else broke")))
        assert form.embedding_test_status.startswith("✗")
        rec = _record()
        assert (rec.level, rec.cause) == (rd.Level.FAILING, rd.Cause.UNKNOWN)
        assert form.testing_connection is False

    def test_the_test_never_imports_the_model_library(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        was_loaded = "sentence_transformers" in sys.modules
        form = _Form()
        _run_test(form, MagicMock(return_value=384))
        if not was_loaded:
            assert "sentence_transformers" not in sys.modules


# ── the two downloads ────────────────────────────────────────────────────


def _download_embedding(form: _Form, probe: Any, snapshot: Any = None) -> MagicMock:
    from plexmix.ui.states._settings_downloads import download_local_embedding_impl

    snapshot = snapshot or MagicMock()
    with patch("huggingface_hub.snapshot_download", snapshot), patch(
        "plexmix.utils.embeddings.probe_local_embedding_model", probe
    ):
        asyncio.run(download_local_embedding_impl(form))
    return snapshot  # type: ignore[no-any-return]


def _download_llm(form: _Form, warm_up: Any, snapshot: Any = None) -> MagicMock:
    from plexmix.ui.states._settings_downloads import download_local_llm_impl

    snapshot = snapshot or MagicMock()
    with patch("huggingface_hub.snapshot_download", snapshot), patch(
        "plexmix.ui.states._settings_downloads.warm_up_local_llm", warm_up
    ):
        asyncio.run(download_local_llm_impl(form))
    return snapshot  # type: ignore[no-any-return]


class TestEmbeddingDownload:
    def test_success(self) -> None:
        form = _Form()
        probe = MagicMock(return_value=384)
        snapshot = _download_embedding(form, probe)
        snapshot.assert_called_once()
        probe.assert_called_once_with("all-MiniLM-L6-v2")
        assert form.local_download_status == "✓ Model cached and ready for offline use"
        assert form.local_download_progress == 100
        assert form.is_downloading_local_model is False

    def test_a_missing_dependency(self) -> None:
        form = _Form()
        _download_embedding(
            form, MagicMock(side_effect=ImportError("sentence-transformers not installed"))
        )
        assert (
            form.local_download_status == "Missing dependency: sentence-transformers not installed"
        )
        assert form.is_downloading_local_model is False

    def test_a_timeout_says_timed_out_and_not_to_check_the_url(self) -> None:
        form = _Form()
        _download_embedding(form, MagicMock(side_effect=TimeoutError(TIMEOUT_SENTENCE)))
        assert form.local_download_status == (
            f"Timed out: {TIMEOUT_SENTENCE}. "
            "The files are downloaded; try again, or pick a smaller model."
        )
        assert "Check the server URL" not in form.local_download_status
        assert form.local_download_progress == 0
        assert form.is_downloading_local_model is False

    def test_a_runtime_error(self) -> None:
        form = _Form()
        _download_embedding(form, MagicMock(side_effect=RuntimeError("Failed to load local model")))
        assert form.local_download_status.startswith("Error downloading all-MiniLM-L6-v2:")
        assert form.is_downloading_local_model is False

    def test_a_failed_snapshot_skips_the_warm_up(self) -> None:
        form = _Form()
        probe = MagicMock(return_value=384)
        _download_embedding(form, probe, MagicMock(side_effect=OSError("disk full")))
        probe.assert_not_called()
        assert form.local_download_status.startswith("Error downloading all-MiniLM-L6-v2:")
        assert form.is_downloading_local_model is False

    def test_a_network_timeout_in_the_snapshot_is_not_the_warm_up_timeout(self) -> None:
        form = _Form()
        probe = MagicMock(return_value=384)
        _download_embedding(form, probe, MagicMock(side_effect=TimeoutError("read timed out")))
        probe.assert_not_called()
        assert form.local_download_status.startswith("Error downloading")
        assert "Timed out:" not in form.local_download_status


class TestLlmDownload:
    def test_success(self) -> None:
        form = _Form()
        warm_up = MagicMock(return_value=None)
        snapshot = _download_llm(form, warm_up)
        snapshot.assert_called_once()
        warm_up.assert_called_once_with("google/gemma-3-1b-it")
        assert form.local_llm_download_status == "✓ Model cached and ready for offline use"
        assert form.local_llm_download_progress == 100
        assert form.is_downloading_local_llm is False

    def test_a_missing_package_names_accelerate(self) -> None:
        form = _Form()
        message = (
            "accelerate not installed. Run: pip install accelerate, "
            "or set PLEXMIX_LOCAL_LLM_DEVICE=cpu"
        )
        _download_llm(form, MagicMock(side_effect=ImportError(message)))
        assert form.local_llm_download_status == f"Missing dependency: {message}"
        assert form.is_downloading_local_llm is False

    def test_a_timeout_says_timed_out_and_not_to_check_the_url(self) -> None:
        form = _Form()
        sentence = "Local LLM google/gemma-3-1b-it did not answer within 300 seconds"
        _download_llm(form, MagicMock(side_effect=TimeoutError(sentence)))
        assert form.local_llm_download_status == (
            f"Timed out: {sentence}. "
            "The files are downloaded; try again, or pick a smaller model."
        )
        assert "Check the server URL" not in form.local_llm_download_status
        assert form.local_llm_download_progress == 0
        assert form.is_downloading_local_llm is False

    def test_a_runtime_error(self) -> None:
        form = _Form()
        _download_llm(form, MagicMock(side_effect=RuntimeError("Failed to load local LLM x: y")))
        assert form.local_llm_download_status.startswith(
            "Error downloading google/gemma-3-1b-it: Failed to load local LLM"
        )
        assert form.is_downloading_local_llm is False

    def test_a_failed_snapshot_skips_the_warm_up(self) -> None:
        form = _Form()
        warm_up = MagicMock(return_value=None)
        _download_llm(form, warm_up, MagicMock(side_effect=OSError("disk full")))
        warm_up.assert_not_called()
        assert form.local_llm_download_status.startswith("Error downloading google/gemma-3-1b-it:")
        assert form.is_downloading_local_llm is False

    def test_another_provider_does_nothing(self) -> None:
        form = _Form(ai_provider="openai")
        warm_up = MagicMock(return_value=None)
        snapshot = _download_llm(form, warm_up)
        snapshot.assert_not_called()
        warm_up.assert_not_called()


# ── the rule itself ──────────────────────────────────────────────────────


def test_the_settings_modules_import_no_model_library() -> None:
    root = Path(__file__).resolve().parents[2] / "src/plexmix/ui"
    for path in sorted(root.rglob("*.py")):
        for line in path.read_text().splitlines():
            stripped = line.strip()
            for package in ("torch", "transformers", "sentence_transformers"):
                assert not stripped.startswith((f"import {package}", f"from {package} import")), (
                    path,
                    line,
                )
