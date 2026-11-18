"""webui-honest-status-and-first-run, group 7: Generator honesty.

7.1 readiness gating (``can_generate``, ``use_vibe``, ``submit_prompt``,
``generate_playlist``), 7.2 specific and persistent generation failures
(``generation_error``), 7.3 removable constraint chips (``active_constraints``).

Reflex is mocked (tests/ui/conftest.py). Nothing here touches the network, the
real keyring or ~/.plexmix: every test runs on a scratch ``PLEXMIX_DATA_DIR`` with
its own config.yaml, the keyring lookup is patched out, providers, the vector index
and the playlist generator are fakes, and the readiness store is cleared around
each test.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional
from unittest.mock import MagicMock

import pytest

from plexmix.services import readiness as rd
from plexmix.services.readiness import readiness_store

SENTINEL_OPENAI = "sk-SENTINEL-generator-openai-0001"

_ENV_PREFIXES = (
    "PLEX_",
    "AI_",
    "EMBEDDING_",
    "DATABASE_",
    "AUDIO_",
    "MUSICBRAINZ_",
    "LOGGING_",
    "UI_",
    "GOOGLE_",
    "GEMINI_",
    "OPENAI_",
    "ANTHROPIC_",
    "COHERE_",
    "CUSTOM_",
    "PLEXMIX_",
)

LOCAL_YAML = "embedding:\n  default_provider: local\n  model: all-MiniLM-L6-v2\n"
OPENAI_YAML = "embedding:\n  default_provider: openai\n  model: text-embedding-3-small\n"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Scratch data dir with a config.yaml, no keyring, no env config, clean store."""
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


@pytest.fixture
def local_deps(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], None]:
    """Control whether the local embedding packages count as installed."""

    def set_available(available: bool) -> None:
        monkeypatch.setattr(rd, "_module_available", lambda name: available)

    set_available(False)
    return set_available


@pytest.fixture
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the 5 s auto-dismiss pause after a successful generation."""
    real_sleep = asyncio.sleep

    async def no_wait(delay: float, result: Any = None) -> Any:
        return await real_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", no_wait)


# ── Helpers ──────────────────────────────────────────────────────────────


def write_config(data: Path, body: str) -> None:
    (data / "config.yaml").write_text(body or "{}\n")


def make_db(data: Path, *, tracks: int = 3, embedded: int = 0, model: str = "openai") -> list[int]:
    """A real SQLite library in the scratch data dir; returns the track ids."""
    from plexmix.database.models import Album, Artist, Embedding, SyncHistory, Track
    from plexmix.database.sqlite_manager import SQLiteManager

    with SQLiteManager(str(data / "plexmix.db")) as db:
        artist = db.insert_artist(Artist(plex_key="a1", name="Artist"))
        album = db.insert_album(Album(plex_key="al1", title="Album", artist_id=artist))
        ids = [
            db.insert_track(
                Track(plex_key=f"t{i}", title=f"Song {i}", artist_id=artist, album_id=album)
            )
            for i in range(tracks)
        ]
        for tid in ids[:embedded]:
            db.insert_embedding(
                Embedding(track_id=tid, embedding_model=model, embedding_dim=2, vector=[0.1, 0.2])
            )
        db.insert_sync_record(
            SyncHistory(tracks_added=tracks, tracks_updated=0, tracks_removed=0, status="success")
        )
    return ids


def write_index_metadata(data: Path, dimension: int, track_ids: list[int]) -> None:
    """The FAISS index file (readiness checks that it exists) and its metadata."""
    (data / "embeddings.index").write_bytes(b"faiss")
    with open(data / "embeddings.metadata", "wb") as f:
        pickle.dump({"dimension": dimension, "track_ids": list(track_ids)}, f)


def gen_state(token: str = "tok-generator") -> Any:
    """A GeneratorState with what a background handler needs from rx.State."""
    from plexmix.ui.states.generator_state import GeneratorState

    class _Harness(GeneratorState):  # type: ignore[misc, valid-type]
        async def __aenter__(self) -> "_Harness":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        def access_allowed(self) -> bool:
            return True

    state = _Harness()
    state.router = SimpleNamespace(session=SimpleNamespace(client_token=token))
    return state


def loaded(state: Any) -> Any:
    """Refresh ``readiness`` and the library stats from the scratch data dir."""
    state.check_configuration_status()
    return state


def loadable(state: Any) -> Any:
    """Make ``on_load`` refresh real readiness without the session sweep or DB extras."""
    state.check_auth = MagicMock(return_value=True)
    state.load_app_data = state.check_configuration_status
    state._load_audio_stats = MagicMock()
    state._load_templates = MagicMock()
    state._rerun_generation_config = ""
    return state


def set_values(events: Any) -> list[Any]:
    items = events if isinstance(events, list) else [events]
    return [e for e in items if isinstance(e, tuple) and e and e[0] == "set_value"]


def run(coro: Any) -> None:
    asyncio.run(coro)


class FakePlaylistGenerator:
    """Stands in for ``PlaylistGenerator``: embeds the prompt through the provider it
    was handed (as the real one does), then returns ``tracks``."""

    calls: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []

    def __init__(self, *, db_manager: Any, vector_index: Any, embedding_generator: Any) -> None:
        self.embedding_generator = embedding_generator

    def generate(self, **kwargs: Any) -> list[dict[str, Any]]:
        FakePlaylistGenerator.calls.append(kwargs)
        self.embedding_generator.generate_embedding(kwargs["mood_query"])
        return [dict(t) for t in FakePlaylistGenerator.tracks]


def install_generation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: Any = None,
    failure: Any = None,
    ntotal: int = 3,
    mismatch: Optional[int] = None,
    tracks: Optional[list[dict[str, Any]]] = None,
    real_build: bool = False,
) -> None:
    """Fake the provider build, the FAISS index and the playlist generator."""
    if not real_build:
        built = provider if provider is not None else fake_provider()
        monkeypatch.setattr(
            "plexmix.services.providers.build_embedding_generator_with_reason",
            lambda settings: (None, failure) if failure is not None else (built, None),
        )
    monkeypatch.setattr(
        "plexmix.database.vector_index.VectorIndex",
        lambda **kw: SimpleNamespace(
            index=SimpleNamespace(ntotal=ntotal),
            dimension_mismatch=mismatch is not None,
            loaded_dimension=mismatch,
        ),
    )
    FakePlaylistGenerator.calls = []
    FakePlaylistGenerator.tracks = (
        tracks
        if tracks is not None
        else [{"id": 1, "title": "Song 0", "artist": "Artist", "duration_ms": 180000}]
    )
    monkeypatch.setattr("plexmix.playlist.generator.PlaylistGenerator", FakePlaylistGenerator)


def fake_provider(error: Optional[BaseException] = None, dimension: int = 1536) -> Any:
    def generate_embedding(text: str) -> list[float]:
        if error is not None:
            raise error
        return [0.1] * 4

    return SimpleNamespace(get_dimension=lambda: dimension, generate_embedding=generate_embedding)


def ready_openai(data: Path, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """OpenAI embeddings with a key and a usable 1536d index over 3 tracks."""
    write_config(data, OPENAI_YAML)
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
    ids = make_db(data, tracks=3, embedded=3)
    write_index_metadata(data, 1536, ids)
    return ids


def state_text(state: Any) -> str:
    return json.dumps(
        {
            "error": state.generation_error,
            "readiness": state.readiness,
            "log": state.generation_log,
            "message": state.generation_message,
        },
        sort_keys=True,
    )


# ── 7.1 Readiness gating ─────────────────────────────────────────────────


class TestCanGenerate:
    def test_empty_index_blocks(self, _isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        write_config(_isolated, OPENAI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        make_db(_isolated, tracks=3, embedded=0)
        state = loaded(gen_state())
        assert state.readiness["index"]["cause"] == "index_empty"
        assert state.can_generate is False
        status = state.gen_status
        assert status["blocked"] == "true"
        assert status["state"] == "not ready"
        assert status["title"] == "The search index hasn't been built yet"
        assert status["action_label"] == "Build search index"
        assert status["action_href"] == "/dashboard"
        assert status["issue"] == "true"

    def test_missing_local_deps_block(
        self, _isolated: Path, local_deps: Callable[[bool], None]
    ) -> None:
        write_config(_isolated, LOCAL_YAML)
        ids = make_db(_isolated, tracks=3, embedded=3, model="local")
        write_index_metadata(_isolated, 384, ids)
        state = loaded(gen_state())
        assert state.can_generate is False
        status = state.gen_status
        assert status["component"] == "embeddings"
        assert status["cause"] == "missing_dependency"
        assert status["title"] == "Local models aren't installed"
        assert ":latest-local" in status["body"] and "plexmix[local]" in status["body"]
        assert status["action_label"] == "Switch provider"
        assert status["action_href"] == "/settings?section=embedding"
        assert "API key" not in json.dumps(status)
        assert status["tone"] == "error"

    def test_unsynced_library_blocks(self, _isolated: Path) -> None:
        state = loaded(gen_state())
        assert state.can_generate is False
        assert "library" in rd.generation_blockers(state.readiness)

    def test_timeout_record_alone_keeps_generation_enabled(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.config.settings import Settings

        ready_openai(_isolated, monkeypatch)
        fp = rd.current_fingerprint("embeddings", Settings.load_from_file())
        readiness_store.record("embeddings", "failing", "timeout", fingerprint=fp)
        state = loaded(gen_state())

        emb = state.readiness["embeddings"]
        assert emb["level"] == "failing" and emb["cause"] == "timeout"
        assert emb["blocking"] == "false"
        assert state.can_generate is True
        status = state.gen_status
        assert status["blocked"] == "false"
        assert status["kind"] == "warning"
        assert status["tone"] == "warning"
        assert status["state"] == "last attempt failed"
        assert status["title"] == "OpenAI took too long to answer"
        assert status["issue"] == "true"
        # A warning never stops anything.
        assert state.submit_prompt({"prompt": "late night drive"}) is not None

    def test_ready_index_with_unchecked_provider(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        state = loaded(gen_state())
        assert state.readiness["embeddings"]["level"] == "unchecked"
        assert state.can_generate is True
        status = state.gen_status
        assert (status["state"], status["tone"], status["issue"]) == ("ready", "success", "false")
        assert status["privacy"] == "prompt sent to OpenAI"
        assert SENTINEL_OPENAI not in json.dumps(status)

    def test_nothing_loaded_yet_does_not_block(self) -> None:
        state = gen_state()
        assert state.readiness == {}
        assert state.can_generate is True
        assert state.gen_status["loaded"] == "false"


class TestUseVibe:
    VIBE = "Chill rainy day vibes with acoustic guitar"

    def test_blocked_only_fills_the_prompt(self, _isolated: Path) -> None:
        state = loaded(gen_state())  # nothing synced: blocked
        result = state.use_vibe(self.VIBE)
        assert result == ("set_value", "gen-prompt", self.VIBE)
        assert state.mood_query == self.VIBE
        assert state.is_generating is False

    def test_missing_deps_only_fills_the_prompt(
        self, _isolated: Path, local_deps: Callable[[bool], None]
    ) -> None:
        write_config(_isolated, LOCAL_YAML)
        ids = make_db(_isolated, tracks=3, embedded=3, model="local")
        write_index_metadata(_isolated, 384, ids)
        state = loaded(gen_state())
        result = state.use_vibe(self.VIBE)
        assert result == ("set_value", "gen-prompt", self.VIBE)

    def test_ready_fills_chases_focus_and_generates(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.states import generator_state as gs

        ready_openai(_isolated, monkeypatch)
        state = loaded(gen_state())
        scripts: list[str] = []
        monkeypatch.setattr(gs.rx, "call_script", lambda code: scripts.append(code) or "chase")
        result = state.use_vibe(self.VIBE)
        assert result == [
            ("set_value", "gen-prompt", self.VIBE),
            "chase",
            gs.GeneratorState.generate_playlist,
        ]
        assert scripts == [gs.FAILURE_FOCUS_SCRIPT]
        assert "#gen-error" in gs.FAILURE_FOCUS_SCRIPT and "force:true" in gs.FAILURE_FOCUS_SCRIPT

    def test_while_generating_nothing_starts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states import generator_state as gs

        state = gen_state()
        state.mood_query = "first vibe"
        state.is_generating = True
        toast = MagicMock()
        monkeypatch.setattr(gs.rx, "toast", toast)
        result = state.use_vibe(self.VIBE)
        assert result is toast.info.return_value
        assert state.mood_query == "first vibe"

    def test_vibe_clears_a_blank_prompt_message(self, _isolated: Path) -> None:
        state = loaded(gen_state())
        state.prompt_error = "Describe a vibe first."
        state.use_vibe(self.VIBE)
        assert state.prompt_error == ""


class TestSubmitPromptGate:
    def test_valid_prompt_while_blocked_starts_nothing(self, _isolated: Path) -> None:
        state = loaded(gen_state())
        assert state.can_generate is False
        assert state.submit_prompt({"prompt": "late night drive", "genre": "synthwave"}) is None
        # The typed prompt is kept, and it is not the blank-prompt case.
        assert state.mood_query == "late night drive"
        assert state.genre_filter == "synthwave"
        assert state.prompt_error == ""

    def test_blank_prompt_message_wins_over_the_gate(self, _isolated: Path) -> None:
        state = loaded(gen_state())
        assert state.submit_prompt({"prompt": "  "}) is None
        assert state.prompt_error == "Describe a vibe first."

    def test_ready_returns_generate(self, _isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from plexmix.ui.states.generator_state import GeneratorState

        ready_openai(_isolated, monkeypatch)
        state = loaded(gen_state())
        assert state.submit_prompt({"prompt": "late night drive"}) is (
            GeneratorState.generate_playlist
        )


class TestGeneratePlaylistGate:
    def test_blocked_generation_returns_early(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.ui.job_manager import jobs

        install_generation(monkeypatch)
        state = loaded(gen_state("tok-blocked"))
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        assert state.is_generating is False
        assert FakePlaylistGenerator.calls == []
        assert jobs.get_cancel_event("tok-blocked", "generation") is None
        assert state.generation_error["title"] == ""

    def test_regenerate_is_gated_too(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Regenerate returns generate_playlist, which refuses while blocked.
        write_config(_isolated, OPENAI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        make_db(_isolated, tracks=3, embedded=0)  # synced, but the index is empty
        install_generation(monkeypatch)
        state = loaded(gen_state())
        assert state.can_generate is False
        state.mood_query = "late night drive"
        handler = state.regenerate()
        run(handler(state))
        assert FakePlaylistGenerator.calls == []
        # Refused at the gate: nothing ran, so there is no failure to show either.
        assert state.generation_error["title"] == ""
        assert state.generation_log == []


# ── 7.2 Specific, persistent failures ────────────────────────────────────


class TestGenerationErrors:
    def test_missing_local_package_names_the_remedy_not_api_keys(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        local_deps: Callable[[bool], None],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        write_config(_isolated, LOCAL_YAML)
        ids = make_db(_isolated, tracks=3, embedded=3, model="local")
        write_index_metadata(_isolated, 384, ids)

        def missing(**kwargs: Any) -> Any:
            raise ImportError(
                "sentence-transformers not installed. Run: pip install sentence-transformers"
            )

        # The real build path, with the package missing at construction time.
        monkeypatch.setattr("plexmix.services.providers.EmbeddingGenerator", missing)
        install_generation(monkeypatch, real_build=True)
        state = gen_state()
        state.mood_query = "late night drive"  # readiness not loaded yet: not blocked
        with caplog.at_level(logging.INFO):
            run(state.generate_playlist())

        error = state.generation_error
        assert error["title"] == "Local models aren't installed"
        assert "sentence-transformers" in error["body"]
        assert ":latest-local" in error["body"] and "plexmix[local]" in error["body"]
        assert error["action_label"] == "Switch provider"
        assert error["action_href"] == "/settings?section=embedding"
        assert (error["component"], error["cause"]) == ("embeddings", "missing_dependency")
        text = json.dumps(error)
        assert "API key" not in text and "api key" not in text.lower()
        assert "Check your API keys" not in caplog.text + state_text(state)
        assert "Run:" not in text and "pip install" not in text
        # generation_message is progress only; the failure is the callout.
        assert state.generation_message == ""
        assert state.is_generating is False
        assert FakePlaylistGenerator.calls == []
        # Recorded, and the readiness line now agrees (without repeating the callout).
        record = readiness_store.get("embeddings")
        assert record is not None and record.cause == rd.Cause.MISSING_DEPENDENCY
        assert state.readiness["embeddings"]["cause"] == "missing_dependency"
        assert state.gen_status["blocked"] == "true"
        assert state.gen_status["issue"] == "false"

    def test_old_api_key_text_is_gone(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        source = (repo / "src/plexmix/ui/states/generator_state.py").read_text(encoding="utf-8")
        assert "Check your API keys" not in source
        assert "⚠" not in source

    def test_missing_cloud_key_says_so(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, OPENAI_YAML)
        ids = make_db(_isolated, tracks=3, embedded=3)
        write_index_metadata(_isolated, 1536, ids)
        install_generation(monkeypatch, real_build=True)  # no key stored
        state = gen_state()
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "No API key for OpenAI"
        assert error["action_href"] == "/settings?section=embedding"
        assert error["cause"] == "missing_api_key"

    def test_empty_index_offers_to_build_it(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch, ntotal=0)
        state = loaded(gen_state())
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "The search index hasn't been built yet"
        assert error["action_label"] == "Build search index"
        assert error["action_href"] == "/dashboard"
        assert (error["component"], error["cause"]) == ("index", "index_empty")

    def test_dimension_mismatch_names_both_sizes_without_emoji(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch, mismatch=384)
        state = loaded(gen_state())
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "The search index doesn't match your embedding provider"
        assert "384-dimension" in error["body"] and "1,536-dimension" in error["body"]
        assert error["action_label"] == "Rebuild embeddings"
        assert error["secondary_label"] == "Switch back"
        assert error["secondary_href"] == "/settings?section=embedding"
        assert "⚠" not in json.dumps(error) and "⚠" not in json.dumps(state.generation_log)

    def test_missing_database_says_sync_first(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_config(_isolated, OPENAI_YAML)
        install_generation(monkeypatch)
        state = gen_state()
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "Your library hasn't been synced yet"
        assert (error["component"], error["cause"]) == ("library", "not_synced")
        assert not (_isolated / "plexmix.db").exists()

    def test_rejected_key_during_generation_is_recorded_without_the_key(
        self,
        _isolated: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        boom = RuntimeError(f"401 Unauthorized: Incorrect API key provided: {SENTINEL_OPENAI}")
        install_generation(monkeypatch, provider=fake_provider(boom))
        state = loaded(gen_state())
        state.mood_query = "late night drive"
        with caplog.at_level(logging.DEBUG):
            run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "OpenAI rejected the API key"
        assert error["cause"] == "invalid_credentials"
        record = readiness_store.get("embeddings")
        assert record is not None and record.level == rd.Level.FAILING
        assert record.cause == rd.Cause.INVALID_CREDENTIALS
        # Recorded failures don't block, and the line shows it as a warning.
        assert state.readiness["embeddings"]["level"] == "failing"
        assert state.can_generate is True
        assert SENTINEL_OPENAI not in state_text(state)
        assert SENTINEL_OPENAI not in caplog.text
        assert SENTINEL_OPENAI not in record.message

    def test_unexpected_provider_error_is_plain_and_redacted(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        boom = RuntimeError(f"Weird failure near {SENTINEL_OPENAI}")
        install_generation(monkeypatch, provider=fake_provider(boom))
        state = loaded(gen_state())
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "OpenAI couldn't start"
        assert error["cause"] == "unknown"
        assert "[redacted]" in error["body"]
        assert SENTINEL_OPENAI not in state_text(state)

    def test_success_records_the_provider_ready(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, fast_sleep: None
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch)
        state = loaded(gen_state())
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        assert [t["id"] for t in state.generated_playlist] == ["1"]
        assert state.generation_error["title"] == ""
        record = readiness_store.get("embeddings")
        assert record is not None and record.level == rd.Level.READY
        assert state.readiness["embeddings"]["level"] == "ready"

    def test_no_matching_tracks_is_a_warning_callout(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch, tracks=[])
        state = loaded(gen_state())
        state.mood_query = "late night drive"
        state.load_generation_config(json.dumps({"mood_query": "x", "tempo_min": 200}))
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "No tracks matched this prompt"
        assert "filters" in error["body"]
        assert error["tone"] == "warning"
        assert error["component"] == "generation"
        assert "Check logs" not in state_text(state)

    def test_unexpected_error_is_described_plainly(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch)

        def broken(**kwargs: Any) -> Any:
            raise RuntimeError("database is locked")

        monkeypatch.setattr("plexmix.database.vector_index.VectorIndex", broken)
        state = loaded(gen_state())
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        error = state.generation_error
        assert error["title"] == "Something went wrong"
        assert "database is locked" in error["body"]
        assert error["action_href"] == ""
        assert state.is_generating is False

    def test_new_generation_clears_the_error(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, fast_sleep: None
    ) -> None:
        from plexmix.ui.states.generator_state import generation_error_for

        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch)
        state = loaded(gen_state())
        state.generation_error = generation_error_for(
            "generation", "", {"title": "No tracks matched this prompt", "body": "x"}
        )
        state.mood_query = "late night drive"
        run(state.generate_playlist())
        assert state.generation_error["title"] == ""

    def test_dismiss_clears_the_error(self) -> None:
        from plexmix.ui.states.generator_state import GENERATION_ERROR_KEYS, generation_error_for

        state = gen_state()
        state.generation_error = generation_error_for(
            "index", "index_empty", rd.describe("index", rd.Cause.INDEX_EMPTY)
        )
        state.dismiss_error()
        assert state.generation_error == {key: "" for key in GENERATION_ERROR_KEYS}


class TestErrorPersistence:
    def test_survives_on_load_while_the_cause_persists(
        self, _isolated: Path, local_deps: Callable[[bool], None]
    ) -> None:
        from plexmix.ui.states.generator_state import generation_error_for

        write_config(_isolated, LOCAL_YAML)
        ids = make_db(_isolated, tracks=3, embedded=3, model="local")
        write_index_metadata(_isolated, 384, ids)
        state = loadable(gen_state())
        error = generation_error_for(
            "embeddings",
            "missing_dependency",
            rd.describe("embeddings", rd.Cause.MISSING_DEPENDENCY, {"provider": "local"}),
        )
        state.generation_error = error

        state.on_load()  # navigate away and back
        assert state.generation_error == error

        local_deps(True)  # the package was installed: the cause is resolved
        state.on_load()
        assert state.generation_error["title"] == ""

    def test_index_empty_clears_once_the_index_is_built(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.database.models import Embedding
        from plexmix.database.sqlite_manager import SQLiteManager
        from plexmix.ui.states.generator_state import generation_error_for

        write_config(_isolated, OPENAI_YAML)
        monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI)
        ids = make_db(_isolated, tracks=3, embedded=0)
        state = loadable(gen_state())
        state.generation_error = generation_error_for(
            "index", "index_empty", rd.describe("index", rd.Cause.INDEX_EMPTY)
        )
        state.on_load()
        assert state.generation_error["cause"] == "index_empty"

        with SQLiteManager(str(_isolated / "plexmix.db")) as db:
            for tid in ids:
                db.insert_embedding(
                    Embedding(
                        track_id=tid, embedding_model="openai", embedding_dim=2, vector=[0.1, 0.2]
                    )
                )
        write_index_metadata(_isolated, 1536, ids)
        state.on_load()
        assert state.generation_error["title"] == ""

    def test_recorded_failure_persists_until_the_provider_works(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from plexmix.config.settings import Settings
        from plexmix.ui.states.generator_state import generation_error_for

        ready_openai(_isolated, monkeypatch)
        fp = rd.current_fingerprint("embeddings", Settings.load_from_file())
        readiness_store.record("embeddings", "failing", "timeout", fingerprint=fp)
        state = loadable(gen_state())
        state.generation_error = generation_error_for(
            "embeddings", "timeout", rd.describe("embeddings", rd.Cause.TIMEOUT)
        )
        state.on_load()
        assert state.generation_error["cause"] == "timeout"
        readiness_store.record("embeddings", "ready", fingerprint=fp)
        state.on_load()
        assert state.generation_error["title"] == ""

    def test_errors_readiness_cannot_see_stay_until_dismissed(self, _isolated: Path) -> None:
        from plexmix.ui.states.generator_state import NO_MATCH_TITLE, generation_error_for

        state = loadable(gen_state())
        error = generation_error_for(
            "generation", "", {"title": NO_MATCH_TITLE, "body": "b"}, tone="warning"
        )
        state.generation_error = error
        state.on_load()
        assert state.generation_error == error

    def test_plain_on_load_returns_no_extra_events(self, _isolated: Path) -> None:
        state = loadable(gen_state())
        assert state.on_load() is None


# ── 7.3 Constraint chips ─────────────────────────────────────────────────


def _capture_filters(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the keyword arguments ``build_generation_filters`` receives."""
    from plexmix.services import playlist_service

    calls: list[dict[str, Any]] = []
    real = playlist_service.build_generation_filters

    def recording(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(playlist_service, "build_generation_filters", recording)
    return calls


class TestConstraintChips:
    def test_rerun_tempo_shows_a_chip(self) -> None:
        state = gen_state()
        state.load_generation_config('{"tempo_min": 90, "tempo_max": 120}')
        labels = [c["label"] for c in state.active_constraints]
        assert "Tempo 90–120" in labels
        chip = state.active_constraints[0]
        assert chip == {"key": "tempo", "name": "Tempo", "value": "90–120", "label": "Tempo 90–120"}

    def test_removing_tempo_means_no_tempo_filter(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, fast_sleep: None
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch)
        calls = _capture_filters(monkeypatch)
        state = loaded(gen_state())
        state.load_generation_config('{"tempo_min": 90, "tempo_max": 120}')
        state.mood_query = "late night drive"

        run(state.generate_playlist())
        assert (calls[-1]["tempo_min"], calls[-1]["tempo_max"]) == (90.0, 120.0)
        assert FakePlaylistGenerator.calls[-1]["filters"] == {"tempo_min": 90.0, "tempo_max": 120.0}

        assert state.remove_constraint("tempo") is None  # no client-owned field to echo
        assert state.active_constraints == []
        run(state.generate_playlist())
        assert calls[-1]["tempo_min"] is None and calls[-1]["tempo_max"] is None
        assert FakePlaylistGenerator.calls[-1]["filters"] is None

    def test_every_hidden_constraint_is_a_chip(self) -> None:
        state = gen_state()
        state.load_generation_config(
            json.dumps(
                {
                    "mood_query": "focus",
                    "year_min": "1990",
                    "year_max": "1999",
                    "tempo_min": "92.5",
                    "energy_level": "high",
                    "key_filter": "Am",
                    "danceability_min": "0.5",
                    "avoid_recent": 3,
                }
            )
        )
        assert [c["label"] for c in state.active_constraints] == [
            "Year 1990–1999",
            "Tempo ≥ 92.5",
            "Energy high",
            "Key Am",
            "Danceability ≥ 0.5",
            "Skip tracks from last 3 playlists",
        ]
        assert [c["key"] for c in state.active_constraints] == [
            "year",
            "tempo",
            "energy",
            "key",
            "danceability",
            "avoid_recent",
        ]

    def test_one_sided_ranges_and_values_generation_ignores(self) -> None:
        from plexmix.ui.states.generator_state import constraint_chips

        assert [c["label"] for c in constraint_chips(year_max="2005", tempo_max="140")] == [
            "Year ≤ 2005",
            "Tempo ≤ 140",
        ]
        # A value generation can't parse is never applied, so it is no constraint.
        assert constraint_chips(year_min="199x", tempo_min="fast", danceability_min="?") == []
        assert constraint_chips(avoid_recent=1)[0]["label"] == "Skip tracks from last 1 playlist"
        assert constraint_chips(avoid_recent=0) == []

    def test_clear_all_means_no_constraint_later(
        self, _isolated: Path, monkeypatch: pytest.MonkeyPatch, fast_sleep: None
    ) -> None:
        ready_openai(_isolated, monkeypatch)
        install_generation(monkeypatch)
        calls = _capture_filters(monkeypatch)
        state = loaded(gen_state())
        state.load_generation_config(
            json.dumps(
                {
                    "mood_query": "focus",
                    "year_min": "1990",
                    "tempo_max": "120",
                    "energy_level": "low",
                    "key_filter": "C",
                    "danceability_min": "0.4",
                    "avoid_recent": 2,
                }
            )
        )
        assert len(state.active_constraints) == 6
        assert state.clear_constraints() is None
        assert state.active_constraints == []
        for _ in range(2):  # later generations in the session apply none of them
            state.mood_query = "late night drive"
            run(state.generate_playlist())
            kwargs = calls[-1]
            for name in ("year_min", "year_max", "tempo_min", "tempo_max", "danceability_min"):
                assert kwargs[name] is None, name
            assert kwargs["energy_level"] is None and kwargs["key"] is None
            assert FakePlaylistGenerator.calls[-1]["avoid_recent"] == 0
            assert FakePlaylistGenerator.calls[-1]["filters"] is None

    def test_remove_each_key(self) -> None:
        from plexmix.ui.states.generator_state import CONSTRAINT_KEYS

        state = gen_state()
        config = {
            "year_min": "1990",
            "tempo_min": "90",
            "energy_level": "high",
            "key_filter": "C",
            "danceability_min": "0.5",
            "avoid_recent": 1,
        }
        for key in CONSTRAINT_KEYS:
            state.load_generation_config(json.dumps(config))
            state.remove_constraint(key)
            assert key not in [c["key"] for c in state.active_constraints]
            assert len(state.active_constraints) == len(CONSTRAINT_KEYS) - 1
        state.remove_constraint("nonsense")  # ignored
        assert len(state.active_constraints) == len(CONSTRAINT_KEYS) - 1

    def test_chip_filters_no_longer_open_options(self) -> None:
        state = gen_state()
        state.show_advanced = False
        state.load_generation_config('{"tempo_min": 90, "energy_level": "high"}')
        assert state.show_advanced is False  # chips show them; Options has no control
        state.load_generation_config('{"genre_filter": "jazz"}')
        assert state.show_advanced is True  # Genre focus lives in the Options panel

    def test_null_values_in_a_saved_config_are_not_constraints(self) -> None:
        state = gen_state()
        state.load_generation_config('{"energy_level": null, "key_filter": null}')
        assert state.energy_level == "" and state.key_filter == ""
        assert state.active_constraints == []


# ── Readiness line content (pure) ────────────────────────────────────────


def _snapshot(provider: str, model: str, *, total: int, embedded: int, dim: int) -> Any:
    from plexmix.config.settings import Settings

    settings = Settings()
    settings.embedding.default_provider = provider
    settings.embedding.model = model
    facts = rd.Facts(
        db_exists=True,
        total_tracks=total,
        embedded_tracks=embedded,
        index_exists=True,
        index_dimension=dim,
        index_vector_count=embedded,
        expected_dimension=dim,
        local_deps_available=True,
        embedding_key_present=True,
    )
    return rd.status_snapshot(rd.evaluate(settings, facts, store=rd.ReadinessStore()))


class TestReadinessLine:
    def test_ready_with_a_local_provider(self) -> None:
        from plexmix.ui.states.generator_state import generation_status

        snap = _snapshot("local", "all-MiniLM-L6-v2", total=5943, embedded=5943, dim=384)
        line = generation_status(snap, loaded=True, total=5943, embedded=5943)
        assert line["tracks"] == "5,943 tracks"
        assert line["provider"] == "Local"
        assert line["model_dim"] == "all-MiniLM-L6-v2 (384d)"
        assert (line["state"], line["tone"]) == ("ready", "success")
        assert "stays on this machine" in line["privacy"]
        assert line["issue"] == "false" and line["blocked"] == "false"

    def test_cloud_provider_disclosure(self) -> None:
        from plexmix.ui.states.generator_state import generation_status

        snap = _snapshot("gemini", "gemini-embedding-001", total=12, embedded=12, dim=3072)
        line = generation_status(snap, loaded=True, total=12, embedded=12)
        assert line["provider"] == "Gemini"
        assert "sent to Gemini" in line["privacy"]
        assert line["model_dim"] == "gemini-embedding-001 (3072d)"

    def test_partly_embedded_library(self) -> None:
        from plexmix.ui.states.generator_state import generation_status

        snap = _snapshot("openai", "text-embedding-3-small", total=5943, embedded=4120, dim=1536)
        line = generation_status(snap, loaded=True, total=5943, embedded=4120)
        assert line["tracks"] == "4,120 of 5,943 tracks searchable"
        assert line["provider"] == "OpenAI"

    def test_not_loaded_shows_nothing_yet(self) -> None:
        from plexmix.ui.states.generator_state import GENERATION_STATUS_KEYS, generation_status

        line = generation_status({}, loaded=False, total=0, embedded=0)
        assert set(line) == set(GENERATION_STATUS_KEYS)
        assert line["loaded"] == "false"
        assert all(isinstance(v, str) for v in line.values())

    def test_the_line_and_the_callout_never_repeat_each_other(self) -> None:
        from plexmix.ui.states.generator_state import generation_error_for, generation_status

        snap = _snapshot("local", "all-MiniLM-L6-v2", total=3, embedded=0, dim=384)
        line = generation_status(snap, loaded=True, total=3, embedded=0)
        assert line["cause"] == "index_empty" and line["issue"] == "true"
        error = generation_error_for(
            "index", "index_empty", rd.describe("index", rd.Cause.INDEX_EMPTY)
        )
        line = generation_status(snap, loaded=True, total=3, embedded=0, error=error)
        assert line["issue"] == "false"
        assert line["state"] == "not ready"  # still says why Generate is off


# ── Readiness line privacy matches the job confirmations (carry-over) ────


def _endpoint_snapshot(provider: str, endpoint: str = "", *, dim: int = 768) -> Any:
    """A ready readiness snapshot for ``provider`` (``custom`` uses ``endpoint``)."""
    from plexmix.config.settings import Settings

    settings = Settings()
    settings.embedding.default_provider = provider
    if provider == "custom":
        settings.embedding.custom_endpoint = endpoint
        settings.embedding.custom_model = "nomic-embed-text"
        settings.embedding.custom_dimension = dim
    elif provider == "local":
        settings.embedding.model = "all-MiniLM-L6-v2"
    else:
        settings.embedding.model = "text-embedding-3-small"
    facts = rd.Facts(
        db_exists=True,
        total_tracks=12,
        embedded_tracks=12,
        index_exists=True,
        index_dimension=dim,
        index_vector_count=12,
        expected_dimension=dim,
        local_deps_available=True,
        embedding_key_present=True,
    )
    return rd.status_snapshot(rd.evaluate(settings, facts, store=rd.ReadinessStore()))


def _confirmation_privacy(snap: Any) -> str:
    """The embeddings job confirmation's privacy line for the same snapshot."""
    emb = snap["embeddings"]
    destination = rd.data_destination(emb)
    scope = rd.describe_job_scope(
        "embeddings", 12, emb["label"], emb["model"], bool(destination), {"sends_to": destination}
    )
    return scope["privacy_line"]


class TestReadinessLinePrivacy:
    @pytest.mark.parametrize(
        ("provider", "endpoint", "privacy", "confirmation"),
        [
            ("local", "", "prompt stays on this machine", "Runs on this machine."),
            (
                "custom",
                "http://localhost:11434/v1",
                "prompt stays on this machine",
                "Runs on this machine.",
            ),
            (
                "custom",
                "http://127.0.0.1:8080/v1",
                "prompt stays on this machine",
                "Runs on this machine.",
            ),
            ("custom", "http://[::1]:8080/v1", "prompt stays on this machine", "Runs on this"),
            (
                "custom",
                "https://embed.example.com/v1",
                "prompt sent to embed.example.com",
                "are sent to embed.example.com.",
            ),
            ("openai", "", "prompt sent to OpenAI", "are sent to OpenAI."),
        ],
        ids=["local", "custom-localhost", "custom-127", "custom-ipv6-loopback", "remote", "cloud"],
    )
    def test_privacy_uses_the_confirmation_destination(
        self, provider: str, endpoint: str, privacy: str, confirmation: str
    ) -> None:
        from plexmix.ui.states.generator_state import generation_status

        dim = 1536 if provider == "openai" else (384 if provider == "local" else 768)
        snap = _endpoint_snapshot(provider, endpoint, dim=dim)
        line = generation_status(snap, loaded=True, total=12, embedded=12)
        assert line["privacy"] == privacy
        assert confirmation in _confirmation_privacy(snap)

    def test_loopback_custom_never_says_sent_to_the_provider(self) -> None:
        from plexmix.ui.states.generator_state import generation_status

        snap = _endpoint_snapshot("custom", "http://localhost:11434/v1")
        line = generation_status(snap, loaded=True, total=12, embedded=12)
        assert line["provider"] == "Custom (OpenAI-compatible)"
        assert "sent to" not in line["privacy"]
        assert _confirmation_privacy(snap).startswith("Runs on this machine")

    def test_remote_endpoint_names_only_the_host(self) -> None:
        from plexmix.ui.states.generator_state import generation_status

        snap = _endpoint_snapshot("custom", "https://user:pw@embed.example.com:8443/v1/embed")
        line = generation_status(snap, loaded=True, total=12, embedded=12)
        assert line["privacy"] == "prompt sent to embed.example.com"
        for leak in ("pw", "8443", "https", "/v1"):
            assert leak not in line["privacy"]
        assert "sent to embed.example.com" in _confirmation_privacy(snap)

    def test_entry_without_a_destination_falls_back_to_the_provider(self) -> None:
        from plexmix.ui.states.generator_state import generation_status

        # An older snapshot entry without ``sends_to``: cloud by provider id, as before.
        cloud = {"embeddings": {"provider": "gemini", "label": "Gemini", "model": "m"}}
        local = {"embeddings": {"provider": "local", "label": "Local", "model": "m"}}
        unset = {"embeddings": {"provider": "", "label": "", "model": ""}}
        status = generation_status(cloud, loaded=True, total=1, embedded=1)
        assert status["privacy"] == "prompt sent to Gemini"
        status = generation_status(local, loaded=True, total=1, embedded=1)
        assert status["privacy"] == "prompt stays on this machine"
        # No provider at all: no claim either way.
        assert generation_status(unset, loaded=True, total=1, embedded=1)["privacy"] == ""
