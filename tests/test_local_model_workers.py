"""Local model checks run in short-lived worker processes, with time limits (fix-local-embedding-crash).

Covers ``local_embedding_deps_available``, the shared worker starter, ``probe_local_embedding_model``
and ``warm_up_local_llm``. No model is loaded and no model library is needed: each test patches the
worker function with a fake from ``tests/_fake_local_workers.py``, so the real starter spawns a real
child process that runs the fake (the child imports only the standard library).

Children: each test compares ``multiprocessing.active_children()`` before and after, instead of
asserting that the set is empty. On a Mac with the ``local`` extra, ``tests/test_embeddings.py``
leaves a cached worker alive until the session ends.
"""

from __future__ import annotations

import importlib.util
import multiprocessing as mp
import sys
import types
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Set
from unittest.mock import MagicMock, patch

import pytest

from plexmix.ai import local_provider
from plexmix.services import providers
from plexmix.services.readiness import Cause
from plexmix.utils import embeddings
from plexmix.utils.embeddings import (
    LocalEmbeddingProvider,
    _start_local_embedding_worker,
    local_embedding_deps_available,
    probe_local_embedding_model,
    stop_worker_process,
)
from tests import _fake_local_workers as fakes

MISSING_TEXT = "sentence-transformers not installed. Run: pip install sentence-transformers"
# A module that exists nowhere: stands in for the torch that a broken install lacks.
BROKEN_DEPENDENCY = "plexmix_test_module_that_does_not_exist"


def _children() -> Set[Any]:
    return set(mp.active_children())


@pytest.fixture
def no_stray_children() -> Iterator[None]:
    """Fail when the test leaves a child that it started."""
    before = _children()
    yield
    assert _children() == before, "the test left a worker process running"


@pytest.fixture
def embedding_worker(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., None]], None]:
    """Replace the embedding worker function with a fake, and report the package as installed."""

    def use(fake: Callable[..., None]) -> None:
        monkeypatch.setattr(embeddings, "_local_embedding_worker", fake)

    monkeypatch.setattr(embeddings, "local_embedding_deps_available", lambda: True)
    return use


@pytest.fixture
def llm_worker(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., None]], None]:
    """Replace the LLM worker with a fake; every package it needs is reported installed."""

    def use(fake: Callable[..., None]) -> None:
        monkeypatch.setattr(local_provider, "_local_llm_worker", fake)

    monkeypatch.setattr(local_provider, "_package_installed", lambda name: True)
    monkeypatch.setattr(local_provider, "LOCAL_LLM_DEVICE", "cpu")
    return use


# ── the availability helper ──────────────────────────────────────────────


class TestLocalEmbeddingDepsAvailable:
    def test_missing_package_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
        with patch("importlib.util.find_spec", return_value=None) as find_spec:
            assert local_embedding_deps_available() is False
        find_spec.assert_called_once_with("sentence_transformers")

    def test_a_none_entry_in_sys_modules_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        assert local_embedding_deps_available() is False

    def test_a_magicmock_stub_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "sentence_transformers", MagicMock())
        with pytest.raises(ValueError):  # what a bare find_spec does with this stub
            importlib.util.find_spec("sentence_transformers")
        assert local_embedding_deps_available() is False

    def test_a_bare_module_stub_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "sentence_transformers", types.ModuleType("stub"))
        with pytest.raises(ValueError):
            importlib.util.find_spec("sentence_transformers")
        assert local_embedding_deps_available() is False

    def test_an_import_error_while_looking_is_false(self) -> None:
        with patch("importlib.util.find_spec", side_effect=ImportError("parent failed")):
            assert local_embedding_deps_available() is False

    def test_a_found_spec_is_true(self) -> None:
        with patch("importlib.util.find_spec", return_value=object()):
            assert local_embedding_deps_available() is True

    def test_it_never_imports_the_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        was_loaded = "sentence_transformers" in sys.modules
        local_embedding_deps_available()
        assert ("sentence_transformers" in sys.modules) == was_loaded


class TestLocalEmbeddingProviderWithoutThePackage:
    def test_raises_the_exact_text_and_starts_no_process(
        self, monkeypatch: pytest.MonkeyPatch, no_stray_children: None
    ) -> None:
        monkeypatch.setattr(embeddings, "local_embedding_deps_available", lambda: False)
        with patch.object(embeddings, "_start_local_embedding_worker") as starter:
            with pytest.raises(ImportError) as info:
                LocalEmbeddingProvider("all-MiniLM-L6-v2")
        assert str(info.value) == MISSING_TEXT
        starter.assert_not_called()

    def test_the_long_lived_provider_waits_without_a_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(embeddings, "local_embedding_deps_available", lambda: True)
        monkeypatch.setattr(LocalEmbeddingProvider, "worker_cache", {})
        monkeypatch.setattr(embeddings, "atexit", MagicMock())
        conn, process = MagicMock(), MagicMock()
        with patch.object(
            embeddings, "_start_local_embedding_worker", return_value=(conn, process, 3)
        ) as starter:
            provider = LocalEmbeddingProvider("fake-model")
        # Three positional arguments and no ready_timeout: Generate behaves as before.
        assert len(starter.call_args.args) == 3
        assert starter.call_args.kwargs == {}
        assert provider.get_dimension() == 3


# ── the shared starter ───────────────────────────────────────────────────


class TestStarterHandshake:
    def test_ready_returns_the_dimension(
        self, embedding_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        embedding_worker(fakes.embedding_ok)
        conn, process, dimension = _start_local_embedding_worker("fake", False, "cpu")
        try:
            assert dimension == 3
            assert process.is_alive()
        finally:
            stop_worker_process(conn, process)
        assert not process.is_alive()

    def test_a_load_error_is_a_runtime_error(
        self, embedding_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        embedding_worker(fakes.embedding_load_error)
        with pytest.raises(RuntimeError) as info:
            _start_local_embedding_worker("fake", False, "cpu")
        assert str(info.value).startswith("Failed to load local model")
        assert "no such model file" in str(info.value)

    def test_a_child_that_exits_first_is_a_start_failure(
        self, embedding_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        embedding_worker(fakes.embedding_exits_silently)
        with pytest.raises(RuntimeError) as info:
            _start_local_embedding_worker("fake", False, "cpu")
        assert str(info.value).startswith("Failed to start local embedding worker")

    def test_a_broken_import_reads_as_a_missing_dependency(
        self, embedding_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        embedding_worker(fakes.embedding_missing_module)
        with pytest.raises(ImportError) as info:
            _start_local_embedding_worker("fake", False, "cpu")
        assert str(info.value).startswith(MISSING_TEXT)
        assert info.value.name == "torch"
        failure = providers._classify_embedding_exception(info.value, "local")
        assert failure.cause == Cause.MISSING_DEPENDENCY
        assert failure.package == "sentence-transformers"

    def test_the_real_worker_turns_a_broken_import_into_a_missing_dependency(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        no_stray_children: None,
    ) -> None:
        """Runs the real ``_local_embedding_worker`` import guard, not a fake of it.

        The spawned child puts ``tmp_path`` on its path (spawn passes ``sys.path`` on), finds a
        ``sentence_transformers`` package that is present but whose first line imports a module
        that does not exist (what a missing torch looks like), and must answer with the
        missing module, not with "the model failed to load". No model library is needed.
        """
        package = tmp_path / "sentence_transformers"
        package.mkdir()
        (package / "__init__.py").write_text(f"import {BROKEN_DEPENDENCY}\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)

        with pytest.raises(ImportError) as info:
            _start_local_embedding_worker("fake", False, "cpu", ready_timeout=120)

        assert str(info.value).startswith(MISSING_TEXT)
        assert BROKEN_DEPENDENCY in str(info.value)
        assert info.value.name == BROKEN_DEPENDENCY
        failure = providers._classify_embedding_exception(info.value, "local")
        assert failure.cause == Cause.MISSING_DEPENDENCY
        assert failure.package == "sentence-transformers"

    def test_a_ready_timeout_stops_the_child(
        self,
        embedding_worker: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
        no_stray_children: None,
    ) -> None:
        embedding_worker(fakes.embedding_sleeps_before_ready)
        with pytest.raises(TimeoutError) as info:
            _start_local_embedding_worker("fake-model", False, "cpu", ready_timeout=0.5)
        assert "fake-model" in str(info.value)
        assert "did not finish loading" in str(info.value)
        assert "0.5 seconds" in str(info.value)


# ── the Settings check ───────────────────────────────────────────────────


class TestProbeLocalEmbeddingModel:
    def test_with_text_returns_the_vector_size(
        self, embedding_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        embedding_worker(fakes.embedding_ok)
        assert probe_local_embedding_model("fake", "some text") == 3

    def test_without_text_returns_the_reported_dimension(
        self, embedding_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        embedding_worker(fakes.embedding_ok)
        assert probe_local_embedding_model("fake") == 3

    def test_the_package_missing_raises_and_starts_no_process(
        self, monkeypatch: pytest.MonkeyPatch, no_stray_children: None
    ) -> None:
        monkeypatch.setattr(embeddings, "local_embedding_deps_available", lambda: False)
        with patch.object(embeddings, "_start_local_embedding_worker") as starter:
            with pytest.raises(ImportError) as info:
                probe_local_embedding_model("fake", "text")
        assert str(info.value) == MISSING_TEXT
        starter.assert_not_called()

    def test_a_child_that_exits_after_ready_fails_and_is_stopped(
        self, embedding_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        embedding_worker(fakes.embedding_ready_then_exits)
        with pytest.raises(RuntimeError) as info:
            probe_local_embedding_model("fake", "text")
        assert "stopped unexpectedly" in str(info.value)

    def test_the_shared_worker_cache_is_left_exactly_as_it_was(
        self, embedding_worker: Callable[..., None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel: Dict[str, Dict[str, Any]] = {"other-model": {"process": object()}}
        cache = dict(sentinel)
        monkeypatch.setattr(LocalEmbeddingProvider, "worker_cache", cache)
        embedding_worker(fakes.embedding_ok)
        assert probe_local_embedding_model("fake", "text") == 3
        assert LocalEmbeddingProvider.worker_cache is cache
        assert cache == sentinel

    def test_the_cache_is_untouched_when_the_check_fails(
        self, embedding_worker: Callable[..., None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache: Dict[str, Dict[str, Any]] = {}
        monkeypatch.setattr(LocalEmbeddingProvider, "worker_cache", cache)
        embedding_worker(fakes.embedding_load_error)
        with pytest.raises(RuntimeError):
            probe_local_embedding_model("fake")
        assert cache == {}

    def test_a_ready_timeout_raises_timeout_error_and_stops_the_child(
        self,
        embedding_worker: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
        no_stray_children: None,
    ) -> None:
        monkeypatch.setattr(embeddings, "LOCAL_MODEL_READY_TIMEOUT", 0.5)
        embedding_worker(fakes.embedding_sleeps_before_ready)
        with pytest.raises(TimeoutError) as info:
            probe_local_embedding_model("fake-model", "text")
        assert "fake-model" in str(info.value)
        assert "did not finish loading" in str(info.value)

    def test_an_answer_timeout_raises_timeout_error_and_stops_the_child(
        self,
        embedding_worker: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
        no_stray_children: None,
    ) -> None:
        # Only the answer limit is lowered: a slow child start cannot trip the ready limit.
        monkeypatch.setattr(embeddings, "LOCAL_MODEL_ANSWER_TIMEOUT", 0.5)
        embedding_worker(fakes.embedding_ready_then_silent)
        with pytest.raises(TimeoutError) as info:
            probe_local_embedding_model("fake-model", "text")
        assert "fake-model" in str(info.value)
        assert "did not answer" in str(info.value)

    def test_a_child_that_ignores_terminate_is_killed(
        self,
        embedding_worker: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
        no_stray_children: None,
    ) -> None:
        monkeypatch.setattr(embeddings, "LOCAL_MODEL_ANSWER_TIMEOUT", 0.5)
        monkeypatch.setattr(embeddings, "_WORKER_STOP_WAIT", 2.0)
        embedding_worker(fakes.embedding_ignores_terminate)
        with pytest.raises(TimeoutError):
            probe_local_embedding_model("fake-model", "text")

    def test_the_default_limits(self) -> None:
        assert embeddings.LOCAL_MODEL_READY_TIMEOUT == 600.0
        assert embeddings.LOCAL_MODEL_ANSWER_TIMEOUT == 300.0


# ── the local LLM warm-up ────────────────────────────────────────────────


class TestWarmUpLocalLlm:
    @pytest.mark.parametrize("missing", ["transformers", "torch"])
    def test_a_missing_package_raises_and_starts_no_process(
        self, monkeypatch: pytest.MonkeyPatch, missing: str, no_stray_children: None
    ) -> None:
        monkeypatch.setattr(local_provider, "_package_installed", lambda name: name != missing)
        monkeypatch.setattr(local_provider, "LOCAL_LLM_DEVICE", "cpu")
        with patch.object(local_provider.mp, "get_context") as get_context:
            with pytest.raises(ImportError) as info:
                local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert str(info.value) == f"{missing} not installed"
        get_context.assert_not_called()

    def test_auto_device_without_accelerate_is_a_missing_dependency(
        self, monkeypatch: pytest.MonkeyPatch, no_stray_children: None
    ) -> None:
        monkeypatch.setattr(local_provider, "_package_installed", lambda name: name != "accelerate")
        monkeypatch.setattr(local_provider, "LOCAL_LLM_DEVICE", "auto")
        with patch.object(local_provider.mp, "get_context") as get_context:
            with pytest.raises(ImportError) as info:
                local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert str(info.value).startswith("accelerate not installed")
        assert "PLEXMIX_LOCAL_LLM_DEVICE=cpu" in str(info.value)
        get_context.assert_not_called()

    def test_cpu_device_without_accelerate_starts_its_worker(
        self, monkeypatch: pytest.MonkeyPatch, no_stray_children: None
    ) -> None:
        monkeypatch.setattr(local_provider, "_package_installed", lambda name: name != "accelerate")
        monkeypatch.setattr(local_provider, "LOCAL_LLM_DEVICE", "cpu")
        monkeypatch.setattr(local_provider, "_local_llm_worker", fakes.llm_ok)
        assert local_provider.warm_up_local_llm("google/gemma-3-1b-it") is None

    def test_a_package_check_survives_a_stub_without_a_spec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "transformers", types.ModuleType("stub"))
        assert local_provider._package_installed("transformers") is False

    def test_ready_then_an_answer_returns_none(
        self, llm_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        llm_worker(fakes.llm_ok)
        assert local_provider.warm_up_local_llm("google/gemma-3-1b-it") is None

    def test_a_load_error_is_a_runtime_error(
        self, llm_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        llm_worker(fakes.llm_load_error)
        with pytest.raises(RuntimeError) as info:
            local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert str(info.value).startswith("Failed to load local LLM")
        assert "no weights found" in str(info.value)

    def test_a_generate_error_is_a_runtime_error(
        self, llm_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        llm_worker(fakes.llm_generate_error)
        with pytest.raises(RuntimeError) as info:
            local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert "generation exploded" in str(info.value)

    def test_a_child_that_exits_first_is_a_start_failure(
        self, llm_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        llm_worker(fakes.llm_exits_silently)
        with pytest.raises(RuntimeError) as info:
            local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert str(info.value).startswith("Failed to start local LLM worker")

    def test_a_child_that_exits_after_ready_is_stopped(
        self, llm_worker: Callable[..., None], no_stray_children: None
    ) -> None:
        llm_worker(fakes.llm_ready_then_exits)
        with pytest.raises(RuntimeError) as info:
            local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert "stopped unexpectedly" in str(info.value)

    def test_a_ready_timeout_raises_timeout_error_and_stops_the_child(
        self,
        llm_worker: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
        no_stray_children: None,
    ) -> None:
        monkeypatch.setattr(local_provider, "LOCAL_LLM_READY_TIMEOUT", 0.5)
        llm_worker(fakes.llm_sleeps_before_ready)
        with pytest.raises(TimeoutError) as info:
            local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert "google/gemma-3-1b-it" in str(info.value)
        assert "did not finish loading" in str(info.value)

    def test_an_answer_timeout_raises_timeout_error_and_stops_the_child(
        self,
        llm_worker: Callable[..., None],
        monkeypatch: pytest.MonkeyPatch,
        no_stray_children: None,
    ) -> None:
        monkeypatch.setattr(local_provider, "LOCAL_LLM_ANSWER_TIMEOUT", 0.5)
        llm_worker(fakes.llm_ready_then_silent)
        with pytest.raises(TimeoutError) as info:
            local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert "did not answer" in str(info.value)

    def test_the_shared_worker_cache_is_left_exactly_as_it_was(
        self, llm_worker: Callable[..., None], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel: Dict[str, Dict[str, Any]] = {"other-model": {"process": object()}}
        cache = dict(sentinel)
        monkeypatch.setattr(local_provider.LocalLLMProvider, "worker_cache", cache)
        llm_worker(fakes.llm_ok)
        local_provider.warm_up_local_llm("google/gemma-3-1b-it")
        assert local_provider.LocalLLMProvider.worker_cache is cache
        assert cache == sentinel

    def test_the_default_limits(self) -> None:
        assert local_provider.LOCAL_LLM_READY_TIMEOUT == 600.0
        assert local_provider.LOCAL_LLM_ANSWER_TIMEOUT == 300.0
