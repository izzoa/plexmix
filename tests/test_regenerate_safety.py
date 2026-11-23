"""A regenerate run changes nothing before its replacement exists.

The service side (``plexmix.services.regenerate_service``): the provider check that
runs before any change, and the run marker that lets a later run continue an
unfinished one.

The engine side (``plexmix.plex.sync``): ``regenerate_sync`` and
``regenerate_local`` replace tags and embeddings in place, batch by batch. Whatever
stops a run, every track is left with its new or its previous data.

No provider is built and no request leaves the process. The check gets fake
providers through its two builders, and the marker tests use a temporary database
with times that are set by hand and a clock that the test moves. The engine tests
use fake providers, a fake Plex client, a temporary database and the real clock.
"""

import json
import logging
import os
import pickle
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

import pytest

from plexmix.ai import tag_generator as tag_generator_module
from plexmix.ai.tag_generator import RepeatedBatchFailure, TagGenerator
from plexmix.config import credentials
from plexmix.config.settings import Settings
from plexmix.database.index_rebuild import IndexRebuild, left_out_sentence
from plexmix.database.models import Album, Artist, Embedding, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex import sync as sync_module
from plexmix.plex.client import PlexReadReport
from plexmix.plex.sync import (
    RegeneratePass,
    RegenerateResult,
    SyncEngine,
    finish_embeddings_pass,
    follow_rows_after_stopped_pass,
    index_may_follow_rows,
    plan_regenerate,
    rebuild_index_from_rows,
    remove_other_model_embeddings,
)
from plexmix.services import regenerate_service
from plexmix.services.providers import ProviderFailure
from plexmix.services.readiness import Cause
from plexmix.services.regenerate_service import (
    CAUSE_NO_ANSWER,
    CAUSE_NO_KEY,
    CAUSE_NOT_GIVEN,
    CAUSE_NOT_STARTED,
    COMPONENT_AI,
    COMPONENT_EMBEDDINGS,
    COMPONENT_INDEX,
    REFUSED_NO_ANSWER,
    REFUSED_NO_KEY,
    REFUSED_NOT_GIVEN,
    REFUSED_NOT_STARTED,
    SCOPE_EMBEDDINGS,
    SCOPE_TAGS,
    CheckedProviders,
    RegenerateRefused,
    RegenerateRun,
    RunProvider,
    check_providers,
    finish_scope,
    marker_path,
    open_run,
    preview_run,
    provider_secrets,
    remaining_track_ids,
    run_provider,
)

# The only "secret" in this file is the project's dummy value.
DUMMY_KEY = "dummy-api-key-0000"
# The same value with spaces in it: a key that cannot be sent in a request header.
UNSENDABLE_KEY = DUMMY_KEY.replace("-", " ")

OPENAI_BOTH = (
    "ai:\n  default_provider: openai\n  model: gpt-5.5\n"
    "embedding:\n  default_provider: openai\n  model: text-embedding-3-small\n"
)


def _settings(tmp_path: Path, extra: str = OPENAI_BOTH) -> Settings:
    config = tmp_path / "config.yaml"
    config.write_text(
        "database:\n"
        f"  path: {tmp_path / 'plexmix.db'}\n"
        f"  faiss_index_path: {tmp_path / 'embeddings.index'}\n" + extra
    )
    return Settings.load_from_file(str(config))


# ---------------------------------------------------------------------------
# The provider check
# ---------------------------------------------------------------------------


class _FakeAI:
    """An AI provider that records each request and can fail it."""

    def __init__(self, error: Optional[BaseException] = None) -> None:
        self.error = error
        self.requests: List[Dict[str, Any]] = []

    def complete(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: int = 4096,
        timeout: int = 30,
    ) -> str:
        self.requests.append({"prompt": prompt, "max_tokens": max_tokens, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return "OK"


class _FakeEmbeddings:
    """An embedding generator that records each text and can fail it."""

    provider_name = "openai"

    def __init__(self, error: Optional[BaseException] = None) -> None:
        self.error = error
        self.texts: List[str] = []

    def generate_embedding(self, text: str) -> List[float]:
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return [0.1, 0.2, 0.3, 0.4]

    def get_dimension(self) -> int:
        return 4


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Give the check fake providers in place of the two builders and the key store.

    ``ai`` and ``embeddings`` are what the builders return (``None``: the provider
    could not be built, and ``embedding_failure`` is the reason the real builder
    would give). ``ai_builds`` and ``embedding_builds`` record the calls.
    """
    state = SimpleNamespace(
        ai=_FakeAI(),
        embeddings=_FakeEmbeddings(),
        embedding_failure=None,
        ai_key=DUMMY_KEY,
        embedding_key=DUMMY_KEY,
        ai_builds=[],
        embedding_builds=0,
    )

    def build_ai(
        settings: Settings,
        provider_name: Optional[str] = None,
        api_key_override: Optional[str] = None,
        silent: bool = False,
    ) -> Any:
        state.ai_builds.append({"provider_name": provider_name, "silent": silent})
        return state.ai

    def build_embeddings(settings: Settings) -> Tuple[Any, Optional[ProviderFailure]]:
        state.embedding_builds += 1
        if state.embeddings is None:
            return None, state.embedding_failure
        return state.embeddings, None

    monkeypatch.setattr(regenerate_service, "build_ai_provider", build_ai)
    monkeypatch.setattr(
        regenerate_service, "build_embedding_generator_with_reason", build_embeddings
    )
    monkeypatch.setattr(regenerate_service, "resolve_ai_api_key", lambda name: state.ai_key)
    monkeypatch.setattr(
        regenerate_service, "resolve_embedding_api_key", lambda name: state.embedding_key
    )
    return state


def _refusal(settings: Settings, need_ai: bool = True, need_embeddings: bool = True) -> Any:
    with pytest.raises(RegenerateRefused) as caught:
        check_providers(settings, need_ai=need_ai, need_embeddings=need_embeddings)
    return caught.value


def test_check_returns_both_providers_after_one_request_each(
    built: SimpleNamespace, tmp_path: Path
) -> None:
    checked = check_providers(_settings(tmp_path), need_ai=True, need_embeddings=True)

    assert isinstance(checked, CheckedProviders)
    ai_provider, generator = checked
    assert ai_provider is built.ai and generator is built.embeddings
    assert built.ai_builds == [{"provider_name": "openai", "silent": True}]
    assert built.embedding_builds == 1
    # One short request each, and nothing about the library in either.
    assert len(built.ai.requests) == 1 and len(built.embeddings.texts) == 1
    assert len(built.ai.requests[0]["prompt"]) < 100
    assert len(built.embeddings.texts[0]) < 100


def test_check_refuses_missing_ai_key(built: SimpleNamespace, tmp_path: Path) -> None:
    built.ai, built.ai_key = None, None

    refused = _refusal(_settings(tmp_path))

    assert refused.provider == "OpenAI"
    assert refused.cause == CAUSE_NO_KEY and refused.reason == REFUSED_NO_KEY
    assert refused.component == COMPONENT_AI
    assert str(refused) == "The AI provider OpenAI is not ready: no API key is stored."
    assert refused.failure == ProviderFailure(Cause.MISSING_API_KEY, "openai")
    # The run is refused at the first provider: the other one is not even built.
    assert built.embedding_builds == 0


def test_check_refuses_missing_keys_through_the_real_builders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no key in the environment or the key store, the real builders build
    nothing, so no request can be made, and the check names the missing key."""
    for variable in ("OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(credentials, "_get_keyring", lambda: None)
    settings = _settings(tmp_path)

    for need_ai, component in ((True, COMPONENT_AI), (False, COMPONENT_EMBEDDINGS)):
        with pytest.raises(RegenerateRefused) as caught:
            check_providers(settings, need_ai=need_ai, need_embeddings=True)
        assert caught.value.reason == REFUSED_NO_KEY
        assert caught.value.component == component
        assert caught.value.provider == "OpenAI"


@pytest.mark.parametrize(
    "error, expected_detail",
    [
        (ConnectionError("Connection refused"), "Connection refused."),
        (
            RuntimeError(f"Error code: 401 - Incorrect API key provided: {DUMMY_KEY}"),
            "rejected your credentials",
        ),
        (TimeoutError("Request timed out"), "Temporary AI provider error"),
        (RuntimeError(""), "RuntimeError."),
    ],
)
def test_check_refuses_unreachable_ai_provider(
    built: SimpleNamespace, tmp_path: Path, error: Exception, expected_detail: str
) -> None:
    built.ai = _FakeAI(error=error)

    refused = _refusal(_settings(tmp_path))

    assert refused.provider == "OpenAI" and refused.component == COMPONENT_AI
    assert refused.cause == CAUSE_NO_ANSWER and refused.reason == REFUSED_NO_ANSWER
    assert str(refused).startswith(
        "The AI provider OpenAI is not ready: it did not answer the test request. "
    )
    assert expected_detail in str(refused)
    assert DUMMY_KEY not in str(refused)
    # The provider's own error is kept for a caller that classifies it.
    assert refused.error is error and refused.failure is None
    assert len(built.ai.requests) == 1
    assert built.embedding_builds == 0


@pytest.mark.parametrize(
    "ai_yaml, ai_key, package_installed, expected_cause, expected_detail",
    [
        ("  default_provider: custom\n", None, True, Cause.MISSING_CONFIG, "No endpoint URL"),
        (
            "  default_provider: custom\n  custom_endpoint: http://127.0.0.1:39150/v1\n",
            None,
            True,
            Cause.MISSING_CONFIG,
            "No model name",
        ),
        (
            "  default_provider: local\n  local_mode: endpoint\n",
            None,
            True,
            Cause.MISSING_CONFIG,
            "No endpoint URL",
        ),
        ("  default_provider: nonsense\n", None, True, Cause.MISSING_CONFIG, "PlexMix knows"),
        (
            "  default_provider: openai\n",
            UNSENDABLE_KEY,
            True,
            Cause.INVALID_CREDENTIALS,
            "cannot be sent",
        ),
        (
            "  default_provider: claude\n",
            DUMMY_KEY,
            False,
            Cause.MISSING_DEPENDENCY,
            "The anthropic package is not installed",
        ),
        ("  default_provider: local\n", None, False, Cause.MISSING_DEPENDENCY, "transformers"),
        ("  default_provider: openai\n", DUMMY_KEY, True, Cause.UNKNOWN, ""),
    ],
)
def test_check_says_why_the_ai_provider_could_not_start(
    built: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ai_yaml: str,
    ai_key: Optional[str],
    package_installed: bool,
    expected_cause: Cause,
    expected_detail: str,
) -> None:
    built.ai, built.ai_key = None, ai_key
    monkeypatch.setattr(regenerate_service, "_module_available", lambda name: package_installed)

    refused = _refusal(_settings(tmp_path, "ai:\n" + ai_yaml), need_embeddings=False)

    assert refused.cause == CAUSE_NOT_STARTED and refused.reason == REFUSED_NOT_STARTED
    assert refused.component == COMPONENT_AI
    assert refused.failure is not None and refused.failure.cause == expected_cause
    assert expected_detail in refused.detail
    assert ": it could not be started." in str(refused)
    assert UNSENDABLE_KEY not in str(refused)
    if not expected_detail:
        assert str(refused) == "The AI provider OpenAI is not ready: it could not be started."


def test_unsendable_key_is_recognised_after_the_credentials_module_was_loaded_again(
    built: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Loading ``plexmix.config.credentials`` again makes a new ``InvalidAPIKeyError`` class.

    ``validate_api_key`` then raises the new class, and the check must still read
    that as a key that cannot be sent. Another test file loads the module again
    (``importlib.reload``), so without this the check passed alone and failed in a
    run of the whole suite. The new class is set here the way a reload sets it, and
    is taken back after the test.
    """

    class InvalidAPIKeyErrorAfterReload(ValueError):
        pass

    built.ai, built.ai_key = None, UNSENDABLE_KEY
    monkeypatch.setattr(regenerate_service, "_module_available", lambda name: True)
    monkeypatch.setattr(credentials, "InvalidAPIKeyError", InvalidAPIKeyErrorAfterReload)
    with pytest.raises(InvalidAPIKeyErrorAfterReload):
        credentials.validate_api_key(UNSENDABLE_KEY)

    refused = _refusal(
        _settings(tmp_path, "ai:\n  default_provider: openai\n"), need_embeddings=False
    )

    assert refused.failure is not None and refused.failure.cause == Cause.INVALID_CREDENTIALS
    assert "cannot be sent" in refused.detail
    assert UNSENDABLE_KEY not in str(refused)


def test_check_refuses_unreachable_embedding_provider(
    built: SimpleNamespace, tmp_path: Path
) -> None:
    error = ConnectionError("Connection refused")
    built.embeddings = _FakeEmbeddings(error=error)

    refused = _refusal(_settings(tmp_path))

    assert refused.provider == "OpenAI" and refused.component == COMPONENT_EMBEDDINGS
    assert refused.cause == CAUSE_NO_ANSWER and refused.reason == REFUSED_NO_ANSWER
    assert str(refused) == (
        "The embedding provider OpenAI is not ready: it did not answer the test request. "
        "Connection refused."
    )
    assert refused.error is error
    # The AI provider was asked first and passed.
    assert len(built.ai.requests) == 1 and len(built.embeddings.texts) == 1


@pytest.mark.parametrize(
    "failure, expected_reason, expected_text",
    [
        (ProviderFailure(Cause.MISSING_API_KEY, "openai"), REFUSED_NO_KEY, "no API key is stored."),
        (
            ProviderFailure(
                Cause.MISSING_DEPENDENCY,
                "local",
                package="sentence-transformers",
                detail="sentence-transformers not installed. Run: pip install sentence-transformers",
            ),
            REFUSED_NOT_STARTED,
            "it could not be started. sentence-transformers not installed.",
        ),
        (
            ProviderFailure(Cause.MISSING_DEPENDENCY, "openai", package="openai"),
            REFUSED_NOT_STARTED,
            "it could not be started. The openai package is not installed.",
        ),
        (None, REFUSED_NOT_STARTED, "it could not be started."),
    ],
)
def test_check_refuses_embedding_provider_that_cannot_start(
    built: SimpleNamespace,
    tmp_path: Path,
    failure: Optional[ProviderFailure],
    expected_reason: str,
    expected_text: str,
) -> None:
    built.embeddings, built.embedding_failure = None, failure

    refused = _refusal(_settings(tmp_path), need_ai=False)

    assert refused.component == COMPONENT_EMBEDDINGS and refused.reason == expected_reason
    assert str(refused).startswith("The embedding provider OpenAI is not ready: ")
    assert expected_text in str(refused)
    # The typed reason travels with the refusal, for a caller that records readiness.
    assert refused.failure is not None
    assert refused.failure.cause == (failure.cause if failure else Cause.UNKNOWN)


def test_check_skips_a_provider_that_is_not_needed(built: SimpleNamespace, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    # The embedding provider is not set up, and a run without embeddings does not mind.
    built.embeddings = None
    built.embedding_failure = ProviderFailure(Cause.MISSING_API_KEY, "openai")
    assert check_providers(settings, need_ai=True, need_embeddings=False) == (built.ai, None)
    assert built.embedding_builds == 0

    # The same the other way round.
    built.embeddings, built.ai, built.ai_key = _FakeEmbeddings(), None, None
    built.ai_builds.clear()
    checked = check_providers(settings, need_ai=False, need_embeddings=True)
    assert checked == (None, built.embeddings)
    assert built.ai_builds == []

    built.embedding_builds = 0
    assert check_providers(settings, need_ai=False, need_embeddings=False) == (None, None)
    assert built.ai_builds == [] and built.embedding_builds == 0


@pytest.mark.parametrize(
    "text",
    [
        "Empty response from OpenAI",
        "Empty text response from Gemini",
        "Could not extract text from Gemini response",
    ],
)
def test_check_accepts_a_reply_without_text(
    built: SimpleNamespace, tmp_path: Path, text: str
) -> None:
    """A provider that answered without text did answer: its key, address and model
    are proven. A model that reasons first can spend the whole limit on that."""
    built.ai = _FakeAI(error=ValueError(text))

    checked = check_providers(_settings(tmp_path), need_ai=True, need_embeddings=False)

    assert checked.ai_provider is built.ai
    # Any other ValueError is a failed request.
    built.ai = _FakeAI(error=ValueError("Unexpected response content type from Cohere"))
    assert _refusal(_settings(tmp_path), need_embeddings=False).reason == REFUSED_NO_ANSWER


def test_check_uses_the_named_ai_provider(built: SimpleNamespace, tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    check_providers(settings, need_ai=True, need_embeddings=False, ai_provider_name="anthropic")
    assert built.ai_builds == [{"provider_name": "anthropic", "silent": True}]

    built.ai = _FakeAI(error=ConnectionError("Connection refused"))
    with pytest.raises(RegenerateRefused) as caught:
        check_providers(settings, True, False, ai_provider_name="anthropic")
    assert caught.value.provider == "Claude"


def test_refusal_text_never_contains_the_key(built: SimpleNamespace, tmp_path: Path) -> None:
    """Every way a provider can put its key into an error text ends without it."""
    address = f"http://user:{DUMMY_KEY}@127.0.0.1:39150/v1"
    leaky = RuntimeError(f"request to {address} failed for key {DUMMY_KEY}\n  key={DUMMY_KEY}")
    custom = (
        "ai:\n  default_provider: custom\n  custom_endpoint: http://127.0.0.1:39150/v1\n"
        f"  custom_model: stand-in\n  custom_api_key: ' {DUMMY_KEY} '\n"
        "embedding:\n  default_provider: custom\n  custom_endpoint: http://127.0.0.1:39150/v1\n"
        f"  custom_model: stand-in\n  custom_api_key: {DUMMY_KEY}\n"
    )
    refusals = []

    # 1. The AI provider's test request fails with the key in the error text.
    built.ai = _FakeAI(error=leaky)
    refusals.append(_refusal(_settings(tmp_path)))
    # 2. The same with the key in the settings file and not in the key store.
    built.ai_key = built.embedding_key = None
    refusals.append(_refusal(_settings(tmp_path, custom)))
    # 3. The embedding provider's test request fails that way.
    built.ai, built.embeddings = _FakeAI(), _FakeEmbeddings(error=leaky)
    refusals.append(_refusal(_settings(tmp_path, custom)))
    built.embedding_key = DUMMY_KEY
    refusals.append(_refusal(_settings(tmp_path)))
    # 4. The embedding provider could not be built, and its reason holds the key.
    built.embeddings = None
    built.embedding_failure = ProviderFailure(
        Cause.UNKNOWN, "openai", detail=f"client rejected api_key={DUMMY_KEY}"
    )
    refusals.append(_refusal(_settings(tmp_path)))
    # 5. The provider repeats the key with its middle hidden: both ends still show.
    built.embeddings = _FakeEmbeddings(
        error=RuntimeError(
            f"Error code: 401 - Incorrect API key provided: {DUMMY_KEY[:8]}********{DUMMY_KEY[-4:]}."
        )
    )
    refusals.append(_refusal(_settings(tmp_path)))
    assert DUMMY_KEY[:8] not in str(refusals[-1]) and DUMMY_KEY[-4:] not in str(refusals[-1])
    assert "Error code: 401 - Incorrect API key provided: [redacted]" in refusals[-1].detail

    assert len(refusals) == 6
    for refused in refusals:
        shown = [str(refused), repr(refused), refused.message, refused.cause, refused.detail]
        shown += [str(arg) for arg in refused.args]
        assert all(DUMMY_KEY not in text for text in shown), shown
        assert "[redacted]" in refused.detail
        # The text is one line and of bounded length.
        assert "\n" not in str(refused) and len(refused.detail) <= 300
    assert "user:" not in refusals[0].detail and "***@127.0.0.1" in refusals[0].detail


def test_refusal_reads_as_plain_sentences() -> None:
    refused = RegenerateRefused("OpenAI", CAUSE_NO_KEY, component=COMPONENT_AI)
    assert str(refused) == "The AI provider OpenAI is not ready: no API key is stored."
    assert refused.role == "AI provider"

    with_detail = RegenerateRefused(
        "Local", "it could not be started.", component=COMPONENT_EMBEDDINGS, detail="No model"
    )
    assert str(with_detail) == (
        "The embedding provider Local is not ready: it could not be started. No model."
    )
    assert str(RegenerateRefused("OpenAI", CAUSE_NO_KEY)) == (
        "OpenAI is not ready: no API key is stored."
    )

    # What the engine raises when it was started without a provider or an index.
    for component, text in (
        (COMPONENT_AI, "The AI provider is not ready: none was given to this run."),
        (COMPONENT_EMBEDDINGS, "The embedding provider is not ready: none was given to this run."),
        (COMPONENT_INDEX, "The search index is not ready: none was given to this run."),
    ):
        not_given = RegenerateRefused.not_given(component)
        assert str(not_given) == text
        assert not_given.reason == REFUSED_NOT_GIVEN and not_given.cause == CAUSE_NOT_GIVEN
        assert not_given.provider == "" and not_given.component == component

    copy = pickle.loads(pickle.dumps(with_detail))
    assert isinstance(copy, RegenerateRefused) and str(copy) == str(with_detail)


# ---------------------------------------------------------------------------
# The run marker
# ---------------------------------------------------------------------------

UTC = timezone.utc
TAGS = RunProvider("openai", "gpt-5.5", "OpenAI")
EMBEDDINGS = RunProvider("openai", "text-embedding-3-small", "OpenAI")
TAGS_ENTRY = {"started_at": "2026-11-18T12:00:00Z", "provider": "openai", "model": "gpt-5.5"}
LONG_AGO = "2026-11-17 09:00:00"


class _Clock:
    """The service's clock, moved by the test. Waiting moves it and is recorded."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 18, 12, 0, 0, 250_000, tzinfo=UTC)
        self.waits: List[float] = []

    def read(self) -> datetime:
        return self.now

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += timedelta(seconds=seconds)

    def advance(self, **amount: float) -> None:
        self.now += timedelta(**amount)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    moved = _Clock()
    monkeypatch.setattr(regenerate_service, "_now", moved.read)
    monkeypatch.setattr(regenerate_service, "_wait", moved.wait)
    return moved


@pytest.fixture
def db(tmp_path: Path) -> Iterator[SQLiteManager]:
    """A temporary database, opened the way the application opens it."""
    manager = SQLiteManager(str(tmp_path / "plexmix.db"))
    manager.connect()
    yield manager
    manager.close()


def _tracks(db: SQLiteManager, count: int, tagged_at: Optional[str] = LONG_AGO) -> List[int]:
    """``count`` tracks with tags that were written at ``tagged_at`` (UTC)."""
    artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist One"))
    album_id = db.insert_album(Album(plex_key="al1", title="Album One", artist_id=artist_id))
    ids = [
        db.insert_track(
            Track(
                plex_key=f"t{n}",
                title=f"Song {n}",
                artist_id=artist_id,
                album_id=album_id,
                tags="old,tags",
            )
        )
        for n in range(count)
    ]
    for track_id in ids:
        _tag_at(db, track_id, tagged_at)
    return ids


def _tag_at(db: SQLiteManager, track_id: int, stamp: Optional[str]) -> None:
    """Set the time a track's tags were written, as ``update_track_tags`` stores it."""
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET tags_generated_at = ? WHERE id = ?", (stamp, track_id))
    conn.commit()


def _embed_at(db: SQLiteManager, track_id: int, model: str, updated_at: datetime) -> None:
    db.insert_embedding(
        Embedding(
            track_id=track_id,
            embedding_model=model,
            embedding_dim=4,
            vector=[0.1, 0.2, 0.3, 0.4],
            updated_at=updated_at,
        )
    )


def _marker(db: SQLiteManager) -> Dict[str, Any]:
    data: Dict[str, Any] = json.loads(marker_path(db).read_text(encoding="utf-8"))
    return data


def _leftovers(db: SQLiteManager) -> List[str]:
    """Files beside the database that are neither it nor its marker."""
    keep = (db.db_path.name, marker_path(db).name)
    return sorted(
        item.name
        for item in db.db_path.parent.iterdir()
        if item.name not in keep and not item.name.startswith(db.db_path.name + "-")
    )


def test_new_run_writes_a_start_time(db: SQLiteManager, clock: _Clock) -> None:
    ids = _tracks(db, 3)
    path = db.db_path.parent / "plexmix.db.regenerate.json"
    assert marker_path(db) == path == marker_path(db.db_path) == marker_path(str(db.db_path))
    assert not path.exists()

    run = open_run(db, SCOPE_TAGS, TAGS)

    assert isinstance(run, RegenerateRun)
    assert run.continued is False
    assert run.track_ids == tuple(ids) and run.remaining == 3 and run.total_tracks == 3
    # UTC, whole seconds.
    assert run.started_at == datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
    assert run.scope == SCOPE_TAGS and run.provider == TAGS and run.marker_path == path
    assert _marker(db) == {"tags": TAGS_ENTRY}
    assert clock.waits == [] and _leftovers(db) == []


def test_unfinished_run_is_continued(db: SQLiteManager, clock: _Clock) -> None:
    ids = _tracks(db, 3)
    first = open_run(db, SCOPE_TAGS, TAGS)
    written = marker_path(db).read_text()
    # The first run replaces two tracks and is then stopped.
    _tag_at(db, ids[0], "2026-11-18 12:00:00")
    _tag_at(db, ids[1], "2026-11-18 12:03:20")
    assert remaining_track_ids(db, first) == [ids[2]]
    clock.advance(hours=5)

    preview = preview_run(db, SCOPE_TAGS, TAGS)
    again = open_run(db, SCOPE_TAGS, TAGS)

    assert again == preview
    assert again.continued is True
    assert again.track_ids == (ids[2],) and again.remaining == 1 and again.total_tracks == 3
    assert again.started_at == first.started_at
    # Continuing writes nothing.
    assert marker_path(db).read_text() == written


@pytest.mark.parametrize(
    "changed",
    [RunProvider("openai", "gpt-5.4-mini", "OpenAI"), RunProvider("claude", "gpt-5.5", "Claude")],
    ids=["model", "provider"],
)
def test_changed_model_starts_a_new_run(
    db: SQLiteManager, clock: _Clock, changed: RunProvider
) -> None:
    ids = _tracks(db, 3)
    open_run(db, SCOPE_TAGS, TAGS)
    _tag_at(db, ids[0], "2026-11-18 12:00:30")
    clock.advance(minutes=10)

    assert preview_run(db, SCOPE_TAGS, changed).continued is False
    run = open_run(db, SCOPE_TAGS, changed)

    assert run.continued is False
    assert run.track_ids == tuple(ids)
    assert run.started_at == datetime(2026, 11, 18, 12, 10, 0, tzinfo=UTC)
    assert _marker(db) == {
        "tags": {
            "started_at": "2026-11-18T12:10:00Z",
            "provider": changed.provider,
            "model": changed.model,
        }
    }


def test_deleted_marker_starts_a_new_run(db: SQLiteManager, clock: _Clock) -> None:
    """Deleting the marker file is how the user starts over."""
    ids = _tracks(db, 3)
    first = open_run(db, SCOPE_TAGS, TAGS)
    _tag_at(db, ids[0], "2026-11-18 12:00:30")
    clock.advance(minutes=10)
    assert preview_run(db, SCOPE_TAGS, TAGS).continued is True

    first.marker_path.unlink()
    run = open_run(db, SCOPE_TAGS, TAGS)

    assert run.continued is False and run.track_ids == tuple(ids)
    assert _marker(db)["tags"]["started_at"] == "2026-11-18T12:10:00Z"


def test_continue_run_false_starts_a_new_run(db: SQLiteManager, clock: _Clock) -> None:
    ids = _tracks(db, 3)
    open_run(db, SCOPE_TAGS, TAGS)
    _tag_at(db, ids[0], "2026-11-18 12:00:30")
    clock.advance(minutes=10)

    assert preview_run(db, SCOPE_TAGS, TAGS, continue_run=False).continued is False
    run = open_run(db, SCOPE_TAGS, TAGS, continue_run=False)

    assert run.continued is False and run.track_ids == tuple(ids)
    assert run.started_at == datetime(2026, 11, 18, 12, 10, 0, tzinfo=UTC)
    assert _marker(db)["tags"]["started_at"] == "2026-11-18T12:10:00Z"
    # The run that follows continues the new one.
    _tag_at(db, ids[1], "2026-11-18 12:10:05")
    clock.advance(minutes=1)
    assert open_run(db, SCOPE_TAGS, TAGS).track_ids == (ids[0], ids[2])


def test_marker_with_nothing_left_is_discarded(db: SQLiteManager, clock: _Clock) -> None:
    """An earlier run that replaced every track leaves nothing to continue."""
    ids = _tracks(db, 3)
    first = open_run(db, SCOPE_TAGS, TAGS)
    for track_id in ids:
        _tag_at(db, track_id, "2026-11-18 12:00:40")
    assert remaining_track_ids(db, first) == []
    clock.advance(days=2)

    preview = preview_run(db, SCOPE_TAGS, TAGS)
    assert preview.continued is False and preview.remaining == 3 and preview.started_at is None
    run = open_run(db, SCOPE_TAGS, TAGS)

    assert run.continued is False and run.track_ids == tuple(ids)
    assert _marker(db)["tags"]["started_at"] == "2026-11-20T12:00:00Z"


def test_finished_scope_clears_the_marker(db: SQLiteManager, clock: _Clock) -> None:
    _tracks(db, 2)
    finish_scope(db, SCOPE_TAGS)  # no marker yet: nothing to do
    open_run(db, SCOPE_TAGS, TAGS)
    open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS)
    embeddings_entry = {**TAGS_ENTRY, "model": "text-embedding-3-small"}
    assert _marker(db) == {"tags": TAGS_ENTRY, "embeddings": embeddings_entry}

    finish_scope(db, SCOPE_TAGS)
    assert _marker(db) == {"embeddings": embeddings_entry}
    finish_scope(db, SCOPE_TAGS)  # finishing twice changes nothing
    assert _marker(db) == {"embeddings": embeddings_entry}

    # The database path is accepted as well as the manager.
    finish_scope(db.db_path, SCOPE_EMBEDDINGS)
    assert not marker_path(db).exists()
    assert _leftovers(db) == []
    with pytest.raises(ValueError, match="Unknown regenerate scope"):
        finish_scope(db, "playlists")


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not json at all",
        b"\xff\xfe\x00\x01",
        b"[1, 2, 3]",
        b'{"tags": "soon"}',
        b'{"tags": {"started_at": "yesterday", "provider": "openai", "model": "gpt-5.5"}}',
        b'{"tags": {"started_at": null, "provider": "openai", "model": "gpt-5.5"}}',
        b'{"tags": {"started_at": "2026-11-18T12:00:00Z", "provider": "openai"}}',
        b'{"tags": {"started_at": "2026-11-18T12:00:00Z", "provider": 7, "model": "gpt-5.5"}}',
        # A start time that is still to come is not a run that can be continued.
        b'{"tags": {"started_at": "2031-01-01T00:00:00Z", "provider": "openai", "model": "gpt-5.5"}}',
    ],
)
def test_unreadable_marker_is_treated_as_absent(
    db: SQLiteManager, clock: _Clock, content: bytes
) -> None:
    ids = _tracks(db, 3)
    _tag_at(db, ids[0], "2026-11-18 12:00:30")  # would be "done" for a 12:00:00 run
    clock.advance(minutes=10)
    marker_path(db).write_bytes(content)

    preview = preview_run(db, SCOPE_TAGS, TAGS)
    assert preview.continued is False and preview.track_ids == tuple(ids)
    assert marker_path(db).read_bytes() == content

    run = open_run(db, SCOPE_TAGS, TAGS)

    assert run.continued is False and run.track_ids == tuple(ids)
    assert _marker(db) == {"tags": {**TAGS_ENTRY, "started_at": "2026-11-18T12:10:00Z"}}


def test_marker_without_read_permission_is_treated_as_absent(
    db: SQLiteManager, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("this user can read a file without read permission")
    ids = _tracks(db, 2)
    open_run(db, SCOPE_TAGS, TAGS)
    _tag_at(db, ids[0], "2026-11-18 12:00:30")
    clock.advance(minutes=10)
    marker_path(db).chmod(0)

    with caplog.at_level(logging.WARNING, logger="plexmix.services.regenerate_service"):
        run = open_run(db, SCOPE_TAGS, TAGS)

    assert run.continued is False and run.track_ids == tuple(ids)
    assert "could not be read and is ignored" in caplog.text
    assert _marker(db)["tags"]["started_at"] == "2026-11-18T12:10:00Z"


def test_malformed_scope_leaves_the_other_scope_usable(db: SQLiteManager, clock: _Clock) -> None:
    ids = _tracks(db, 2)
    open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS)
    _embed_at(db, ids[0], "openai", datetime(2026, 11, 18, 12, 0, 30))
    embeddings_entry = _marker(db)["embeddings"]
    marker_path(db).write_text(json.dumps({"tags": ["broken"], "embeddings": embeddings_entry}))
    clock.advance(minutes=10)

    assert open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS).track_ids == (ids[1],)
    assert open_run(db, SCOPE_TAGS, TAGS).continued is False
    # The rewrite keeps the usable entry and drops the broken one.
    assert _marker(db) == {
        "tags": {**TAGS_ENTRY, "started_at": "2026-11-18T12:10:00Z"},
        "embeddings": embeddings_entry,
    }


def test_preview_writes_nothing(db: SQLiteManager, clock: _Clock) -> None:
    ids = _tracks(db, 3)
    changes_before = db.get_connection().total_changes

    preview = preview_run(db, SCOPE_TAGS, TAGS)

    assert preview.continued is False and preview.started_at is None
    assert preview.track_ids == tuple(ids) and preview.total_tracks == 3
    assert preview.marker_path == marker_path(db) and not marker_path(db).exists()
    assert remaining_track_ids(db, preview) == ids
    assert db.get_connection().total_changes == changes_before
    assert clock.waits == [] and _leftovers(db) == []
    with pytest.raises(ValueError, match="Unknown regenerate scope"):
        preview_run(db, "playlists", TAGS)


def test_new_run_waits_out_a_write_of_the_same_second(db: SQLiteManager, clock: _Clock) -> None:
    """Tags that were written a moment before the run started, in the same second,
    are old data. The times hold whole seconds, so the run starts one second later
    and waits for the clock: otherwise that track would pass for already replaced."""
    ids = _tracks(db, 3)
    _tag_at(db, ids[1], "2026-11-18 12:00:00")  # the clock reads 12:00:00.25

    run = open_run(db, SCOPE_TAGS, TAGS)

    assert run.track_ids == tuple(ids)
    assert run.started_at == datetime(2026, 11, 18, 12, 0, 1, tzinfo=UTC)
    assert clock.waits == [pytest.approx(0.75)]
    assert _marker(db)["tags"]["started_at"] == "2026-11-18T12:00:01Z"
    # What the run writes from now on counts as replaced.
    _tag_at(db, ids[0], "2026-11-18 12:00:01")
    assert remaining_track_ids(db, run) == [ids[1], ids[2]]


def test_new_run_does_not_wait_for_a_time_far_ahead(
    db: SQLiteManager, clock: _Clock, caplog: pytest.LogCaptureFixture
) -> None:
    """A time hours ahead means the clock was set back. The run waits a few seconds
    at most, says so in the log and goes on with the tracks it can tell apart."""
    ids = _tracks(db, 3)
    _tag_at(db, ids[2], "2026-11-18 15:00:00")

    with caplog.at_level(logging.WARNING, logger="plexmix.services.regenerate_service"):
        run = open_run(db, SCOPE_TAGS, TAGS)

    assert run.track_ids == (ids[0], ids[1]) and run.total_tracks == 3
    assert len(clock.waits) == 5 and sum(clock.waits) <= 5
    assert "1 tracks carry a time later than now" in caplog.text


def test_marker_is_replaced_in_one_step(
    db: SQLiteManager, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker is written beside itself and moved into place, so a failed write
    leaves the old marker whole and no half-written file."""
    _tracks(db, 2)
    open_run(db, SCOPE_TAGS, TAGS)
    written = marker_path(db).read_text()
    clock.advance(minutes=10)
    moved: List[Tuple[str, str]] = []
    real_replace = os.replace

    def failing_replace(source: Any, target: Any) -> None:
        moved.append((Path(source).name, Path(target).name))
        raise OSError("disk full")

    monkeypatch.setattr(regenerate_service.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        open_run(db, SCOPE_TAGS, TAGS, continue_run=False)

    assert len(moved) == 1 and moved[0][1] == marker_path(db).name
    assert moved[0][0] != moved[0][1] and moved[0][0].startswith(marker_path(db).name)
    assert marker_path(db).read_text() == written
    assert _leftovers(db) == []

    # A marker that cannot be updated at the end of a run is logged, not raised.
    monkeypatch.setattr(regenerate_service.os, "replace", real_replace)
    open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS)
    monkeypatch.setattr(regenerate_service.os, "replace", failing_replace)
    finish_scope(db, SCOPE_TAGS)
    assert set(_marker(db)) == {"tags", "embeddings"} and _leftovers(db) == []


def test_embeddings_run_counts_the_rows_of_its_model(db: SQLiteManager, clock: _Clock) -> None:
    ids = _tracks(db, 4)
    long_ago = datetime(2026, 11, 17, 9, 0, 0)
    _embed_at(db, ids[0], "openai", long_ago)
    _embed_at(db, ids[1], "gemini", long_ago)
    _embed_at(db, ids[3], "openai", long_ago)
    open_run(db, SCOPE_TAGS, TAGS)

    run = open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS)

    assert run.continued is False and run.track_ids == tuple(ids)
    assert set(_marker(db)) == {"tags", "embeddings"}
    # The run replaces one row and adds one. A row of another model does not count.
    _embed_at(db, ids[0], "openai", datetime(2026, 11, 18, 12, 0, 0, 900_000))
    _embed_at(db, ids[2], "openai", datetime(2026, 11, 18, 12, 4, 0))
    _embed_at(db, ids[1], "gemini", datetime(2026, 11, 18, 12, 4, 0))
    clock.advance(minutes=30)

    again = open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS)

    assert again.continued is True and again.track_ids == (ids[1], ids[3])
    assert remaining_track_ids(db, again) == [ids[1], ids[3]]
    # The tags scope of the same file is a run of its own.
    assert preview_run(db, SCOPE_TAGS, TAGS).continued is True
    assert preview_run(db, SCOPE_TAGS, TAGS).remaining == 4


def test_run_follows_the_real_writers_and_the_real_clock(db: SQLiteManager) -> None:
    """Nothing is set by hand here: tags are stamped by ``update_track_tags``,
    embeddings by ``insert_embeddings_batch``, and the clock is the real one. Data
    written just before a run starts is all to do, and what the run writes is done.
    The run may wait for the next second to tell the two apart."""
    ids = _tracks(db, 4, tagged_at=None)

    def write_tags(track_ids: List[int]) -> None:
        for track_id in track_ids:
            db.update_track_tags(track_id, tags="new,tags", environments="", instruments="")

    def write_embeddings(track_ids: List[int]) -> None:
        db.insert_embeddings_batch(
            [
                Embedding(track_id=i, embedding_model="openai", embedding_dim=2, vector=[0.1, 0.2])
                for i in track_ids
            ]
        )

    write_tags(ids)
    write_embeddings(ids)

    tags = open_run(db, SCOPE_TAGS, TAGS)
    embeddings = open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS)
    assert tags.track_ids == tuple(ids) and embeddings.track_ids == tuple(ids)

    write_tags(ids[:2])
    write_embeddings(ids[:3])
    assert remaining_track_ids(db, tags) == ids[2:]
    assert remaining_track_ids(db, embeddings) == ids[3:]
    continued = open_run(db, SCOPE_TAGS, TAGS)
    assert continued.continued is True and continued.track_ids == tuple(ids[2:])
    assert open_run(db, SCOPE_EMBEDDINGS, EMBEDDINGS).track_ids == tuple(ids[3:])

    write_tags(ids[2:])
    write_embeddings(ids[3:])
    assert remaining_track_ids(db, tags) == [] and remaining_track_ids(db, embeddings) == []
    assert preview_run(db, SCOPE_TAGS, TAGS).continued is False


def test_run_provider_reads_the_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    tags = run_provider(settings, SCOPE_TAGS)
    assert tags == RunProvider("openai", "gpt-5.5", "OpenAI")
    assert tags.text == "OpenAI · gpt-5.5"
    # The embeddings scope also names the vector size: a run is not continued across
    # a change of it.
    assert run_provider(settings, SCOPE_EMBEDDINGS) == RunProvider(
        "openai", "text-embedding-3-small", "OpenAI", 1536
    )
    assert tags.dimension is None
    # Another provider than the configured one uses its own default model.
    claude = run_provider(settings, SCOPE_TAGS, ai_provider_name="anthropic")
    assert (claude.provider, claude.label) == ("claude", "Claude")
    assert claude.model and claude.model != "gpt-5.5"
    assert run_provider(settings, SCOPE_TAGS, ai_provider_name="OpenAI") == tags

    custom = _settings(
        tmp_path,
        "ai:\n  default_provider: custom\n  model: ignored\n  custom_model: stand-in-llm\n"
        "embedding:\n  default_provider: custom\n  custom_model: stand-in-embed\n",
    )
    assert run_provider(custom, SCOPE_TAGS) == RunProvider(
        "custom", "stand-in-llm", "Custom (OpenAI-compatible)"
    )
    assert run_provider(custom, SCOPE_EMBEDDINGS).model == "stand-in-embed"
    assert run_provider(custom, SCOPE_EMBEDDINGS).dimension == 1536
    resized = _settings(
        tmp_path,
        "embedding:\n  default_provider: custom\n  custom_model: stand-in-embed\n"
        "  custom_dimension: 16\n",
    )
    assert run_provider(resized, SCOPE_EMBEDDINGS) == RunProvider(
        "custom", "stand-in-embed", "Custom (OpenAI-compatible)", 16
    )
    assert run_provider(_settings(tmp_path, "ai:\n  default_provider: openai\n"), SCOPE_TAGS).model

    with pytest.raises(ValueError, match="Unknown regenerate scope"):
        run_provider(settings, "playlists")
    assert RunProvider("fake", "", "").text == "fake"


def test_the_sync_engine_can_import_the_service() -> None:
    """The engine raises ``RegenerateRefused`` and opens the marker, so the service
    must not import the engine, the CLI or the web UI, directly or through another
    module. Checked in a new interpreter, where nothing is imported yet."""
    code = (
        "import sys\n"
        "import plexmix.services.regenerate_service\n"
        "pulled = sorted(name for name in sys.modules "
        "if name.startswith(('plexmix.plex', 'plexmix.cli', 'plexmix.ui')))\n"
        "assert not pulled, pulled\n"
        "import plexmix.plex.sync\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]


# ---------------------------------------------------------------------------
# The engine: tags and embeddings are replaced in place
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "fake-embed"
OTHER_MODEL = "earlier-model"
OLD_VECTOR = [0.1, 0.2, 0.3, 0.4]
# These tests run on the real clock, so "long ago" is a time no clock is behind.
TAGGED_LONG_AGO = "2020-01-01 09:00:00"
EMBEDDED_LONG_AGO = datetime(2020, 1, 1, 9, 0, 0)
# What ``_old_tracks`` gives every track: the tag columns and the time they were written.
OLD_TAGS = ("old,tags", None, None, TAGGED_LONG_AGO)
FAKE_AI = RunProvider("fake", "fake-llm", "Fake")
FAKE_VECTORS = RunProvider(EMBEDDING_MODEL, "fake-vectors", "Fake")
FATAL = "401 Unauthorized"


def _ids_in_prompt(prompt: str) -> List[int]:
    """The ids of the tracks a tagging request lists."""
    listed = prompt.split("Assign tags to the following songs:")[1]
    listed = listed.split("Return a JSON object")[0]
    return [int(entry["id"]) for entry in json.loads(listed)]


class _TagAI:
    """An AI provider that gives every track it is sent new tags, and records the requests.

    ``errors`` maps a track id to what is raised by a request that holds that track.
    ``omit`` ids are left out of the reply and ``empty`` ids get three empty lists.
    ``no_tags`` ids get environments and instruments and an empty list of tags, and
    ``tags_only`` ids get tags and two empty lists.
    A request that holds an ``unreadable`` id is answered with text that is not JSON.
    ``on_request`` is called with the ids of every request before it is answered.
    """

    provider_name = "Fake"
    model = "fake-llm"
    api_key = DUMMY_KEY

    def __init__(
        self,
        errors: Optional[Dict[int, BaseException]] = None,
        omit: Iterable[int] = (),
        empty: Iterable[int] = (),
        unreadable: Iterable[int] = (),
        on_request: Optional[Callable[[List[int]], None]] = None,
        no_tags: Iterable[int] = (),
        tags_only: Iterable[int] = (),
    ) -> None:
        self.errors = errors or {}
        self.omit = set(omit)
        self.empty = set(empty)
        self.no_tags = set(no_tags)
        self.tags_only = set(tags_only)
        self.unreadable = set(unreadable)
        self.on_request = on_request
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
        if self.on_request is not None:
            self.on_request(ids)
        for track_id in ids:
            if track_id in self.errors:
                raise self.errors[track_id]
        if self.unreadable.intersection(ids):
            return "Sorry, I cannot help with that."
        reply: Dict[str, Any] = {}
        for track_id in ids:
            if track_id in self.omit:
                continue
            if track_id in self.empty:
                reply[str(track_id)] = {"tags": [], "environments": [], "instruments": []}
            else:
                tags, environments, instruments = _new_tags(track_id)
                reply[str(track_id)] = {
                    "tags": [] if track_id in self.no_tags else [tags],
                    "environments": [] if track_id in self.tags_only else [environments],
                    "instruments": [] if track_id in self.tags_only else [instruments],
                }
        return json.dumps(reply)

    @property
    def sent(self) -> List[int]:
        """Every track that was sent to the provider, by id."""
        return sorted({track_id for request in self.requests for track_id in request})


class _Vectors:
    """An embedding generator that records the texts of each request and can fail one."""

    provider_name = EMBEDDING_MODEL

    def __init__(
        self,
        fail_on_request: Optional[int] = None,
        error: Optional[BaseException] = None,
        dimension: int = 4,
        on_request: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.fail_on_request = fail_on_request
        self.error = error or RuntimeError("the embedding provider stopped answering")
        self.dimension = dimension
        # Called with the number of each request (from 1) after it was answered.
        self.on_request = on_request
        self.requests: List[List[str]] = []
        # Where the real providers keep their key: on the client of the inner provider.
        self.provider = SimpleNamespace(
            model_name="fake-vectors", client=SimpleNamespace(api_key=DUMMY_KEY)
        )

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        self.requests.append(list(texts))
        if self.fail_on_request == len(self.requests):
            raise self.error
        if self.on_request is not None:
            self.on_request(len(self.requests))
        return [_new_vector(text, self.dimension) for text in texts]

    def generate_embedding(self, text: str) -> List[float]:
        return _new_vector(text, self.dimension)

    def get_dimension(self) -> int:
        return self.dimension

    @property
    def texts(self) -> List[str]:
        return [text for request in self.requests for text in request]

    @property
    def songs(self) -> List[int]:
        """The numbers of the songs that were sent, in order ("Song 3 by ..." is 3)."""
        return [_song_number(text) for text in self.texts]


def _song_number(text: str) -> int:
    return int(text.split(" by ")[0].split("Song ")[1])


def _new_vector(text: str, dimension: int = 4) -> List[float]:
    """The vector the fake generator makes for an embedding text."""
    return [9.0, float(_song_number(text) + 1)] + [1.0] * (dimension - 2)


def _new_tags(track_id: int) -> Tuple[str, str, str]:
    """The tag columns the fake AI provider gives a track."""
    return (f"new-{track_id}", "focus", "piano")


class _Plex:
    """A Plex client that lists exactly the tracks ``_tracks`` put into the database."""

    def __init__(self, count: int) -> None:
        self.keys = [f"t{n}" for n in range(count)]
        self.read_report = PlexReadReport(
            listed_track_keys=frozenset(self.keys),
            listed={"artists": 1, "albums": 1, "tracks": count},
        )
        self.checks = 0
        self.reads = 0

    def validate_token(self) -> Tuple[bool, str]:
        self.checks += 1
        return True, "Connected"

    def get_all_artists(self, batch_size: int = 100) -> Iterator[List[Artist]]:
        self.reads += 1
        yield [Artist(plex_key="a1", name="Artist One")]

    def get_all_albums(self, batch_size: int = 100) -> Iterator[List[Album]]:
        album = Album(plex_key="al1", title="Album One", artist_id=0)
        album.__dict__["_artist_key"] = "a1"
        yield [album]

    def get_all_tracks(self, batch_size: int = 100) -> Iterator[List[Track]]:
        tracks = []
        for n, key in enumerate(self.keys):
            track = Track(plex_key=key, title=f"Song {n}", artist_id=0, album_id=0)
            track.__dict__["_artist_key"] = "a1"
            track.__dict__["_album_key"] = "al1"
            tracks.append(track)
        yield tracks

    def list_track_keys(self) -> Any:
        return frozenset(self.keys)


@pytest.fixture
def small_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two tracks per request, so that six tracks make three batches of each kind."""
    monkeypatch.setattr(sync_module, "_TAG_BATCH_SIZE", 2)
    monkeypatch.setattr(sync_module, "_EMBEDDING_BATCH_SIZE", 2)


def _old_tracks(db: SQLiteManager, count: int) -> List[int]:
    """``count`` tracks with tags that were written long ago."""
    return _tracks(db, count, tagged_at=TAGGED_LONG_AGO)


def _library(db: SQLiteManager, count: int = 6, other_model: bool = False) -> List[int]:
    """``count`` tracks with tags and an embedding that were both written long ago.

    With ``other_model`` every track also has an embedding row of an earlier model.
    """
    ids = _old_tracks(db, count)
    for track_id in ids:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
        if other_model:
            _embed_at(db, track_id, OTHER_MODEL, EMBEDDED_LONG_AGO)
    return ids


def _index_path(db: SQLiteManager) -> Path:
    return db.db_path.parent / "embeddings.index"


def _saved_index(db: SQLiteManager, ids: List[int]) -> VectorIndex:
    """The index of the library as it was saved before the run: one old vector per track."""
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    if ids:
        index.build_index([OLD_VECTOR for _ in ids], ids)
        index.save_index(str(_index_path(db)))
    return index


def _index_files(db: SQLiteManager) -> Dict[str, bytes]:
    return {
        item.name: item.read_bytes()
        for item in sorted(db.db_path.parent.iterdir())
        if item.name.startswith("embeddings.")
    }


def _ids_in_saved_index(db: SQLiteManager) -> List[int]:
    return sorted(VectorIndex(dimension=4, index_path=str(_index_path(db))).track_ids)


def _tag_rows(db: SQLiteManager) -> Dict[int, Tuple[Any, ...]]:
    """Per track: tags, environments, instruments and the time they were written."""
    cursor = db.get_connection().cursor()
    cursor.execute(
        "SELECT id, tags, environments, instruments, tags_generated_at FROM tracks ORDER BY id"
    )
    return {row["id"]: tuple(row)[1:] for row in cursor.fetchall()}


def _embedding_rows(db: SQLiteManager) -> List[Tuple[Any, ...]]:
    cursor = db.get_connection().cursor()
    cursor.execute(
        "SELECT track_id, embedding_model, embedding_dim, vector, updated_at FROM embeddings "
        "ORDER BY track_id, embedding_model"
    )
    return [tuple(row) for row in cursor.fetchall()]


def _vectors_of(db: SQLiteManager, model: str = EMBEDDING_MODEL) -> Dict[int, List[float]]:
    return dict(db.get_all_embeddings(model))


def _retagged(db: SQLiteManager) -> List[int]:
    """The tracks that hold the fake provider's tags. Every other track must be untouched."""
    done = []
    for track_id, row in _tag_rows(db).items():
        if row[:3] == _new_tags(track_id):
            assert row[3] is not None and row[3] != TAGGED_LONG_AGO
            done.append(track_id)
        else:
            assert row == OLD_TAGS
    return done


def _reembedded(db: SQLiteManager) -> List[int]:
    """The tracks whose embedding is a new one. Every other track must hold its old one."""
    done = []
    for track_id, vector in sorted(_vectors_of(db).items()):
        if vector == OLD_VECTOR:
            continue
        assert vector[0] == 9.0
        done.append(track_id)
    return done


def _history(db: SQLiteManager) -> List[Dict[str, Any]]:
    cursor = db.get_connection().cursor()
    cursor.execute("SELECT * FROM sync_history ORDER BY id")
    return [dict(row) for row in cursor.fetchall()]


def _engine(
    db: SQLiteManager,
    ai: Any = None,
    vectors: Any = None,
    index: Optional[VectorIndex] = None,
    plex: Any = None,
) -> SyncEngine:
    return SyncEngine(
        plex_client=plex,
        db_manager=db,
        embedding_generator=vectors,
        vector_index=index,
        ai_provider=ai,
    )


def _regenerate(engine: SyncEngine, **options: Any) -> RegenerateResult:
    """Run ``regenerate_sync`` with the providers the tests' marker entries name."""
    options.setdefault("tags_provider", FAKE_AI)
    if options.get("generate_embeddings", True):
        options.setdefault("embeddings_provider", FAKE_VECTORS)
    engine.regenerate_sync(**options)
    return engine.regenerate_result


@pytest.mark.parametrize("stop", ["provider error", "interrupt"])
def test_regenerate_sync_clears_nothing_before_rebuild(
    stop: str, db: SQLiteManager, small_batches: None
) -> None:
    """The provider fails on its first request. Every tag and embedding row is unchanged."""
    ids = _library(db, 6, other_model=True)
    index = _saved_index(db, ids)
    tags_before, embeddings_before = _tag_rows(db), _embedding_rows(db)
    files_before = _index_files(db)
    assert len(embeddings_before) == 12 and set(files_before) == {
        "embeddings.index",
        "embeddings.metadata",
    }
    error: BaseException = RuntimeError(FATAL) if stop == "provider error" else KeyboardInterrupt()
    ai, vectors = _TagAI(errors={ids[0]: error}), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(6))

    if stop == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            _regenerate(engine)
    else:
        result = _regenerate(engine)
        assert result.provider_failed is True and result.stopped_scope == SCOPE_TAGS
        assert result.error is engine.tagging_error and result.error is not None
        assert result.error_message.startswith("AI tagging stopped: The AI provider rejected")
        row = _history(db)[-1]
        assert row["status"] == "partial"
        assert row["error_message"] == (
            f"{result.error_message} 6 tracks keep their previous data."
        )

    assert _tag_rows(db) == tags_before
    assert _embedding_rows(db) == embeddings_before
    assert _index_files(db) == files_before
    # One request to the AI provider, none to the embedding provider.
    assert len(ai.requests) == 1 and vectors.requests == []
    result = engine.regenerate_result
    assert result.wrote_nothing is True
    assert (result.tags_written, result.embeddings_written, result.tracks_kept) == (0, 0, 6)
    assert result.finished is False
    assert result.embeddings.started is False and result.embeddings.kept == 6
    assert engine.tags_saved == 0 and engine.embeddings_saved == 0
    # The run can be continued: it is still open in the marker.
    assert set(_marker(db)) == {"tags"}


@pytest.mark.parametrize(
    "missing, component, text",
    [
        ("ai", COMPONENT_AI, "The AI provider is not ready: none was given to this run."),
        (
            "embeddings",
            COMPONENT_EMBEDDINGS,
            "The embedding provider is not ready: none was given to this run.",
        ),
        ("index", COMPONENT_INDEX, "The search index is not ready: none was given to this run."),
    ],
)
@pytest.mark.parametrize("local", [False, True], ids=["sync", "local"])
def test_regenerate_sync_refuses_without_ai_provider(
    local: bool, missing: str, component: str, text: str, db: SQLiteManager
) -> None:
    """The engine's own guard: a run that was not given what it needs changes nothing."""
    ids = _library(db, 3, other_model=True)
    before = (_tag_rows(db), _embedding_rows(db))
    plex = _Plex(3)
    ai, vectors = _TagAI(), _Vectors()
    engine = _engine(
        db,
        None if missing == "ai" else ai,
        None if missing == "embeddings" else vectors,
        None if missing == "index" else _saved_index(db, ids),
        plex,
    )
    files_before = _index_files(db)

    with pytest.raises(RegenerateRefused) as refused:
        if local:
            engine.regenerate_local()
        else:
            engine.regenerate_sync()

    assert str(refused.value) == text
    assert refused.value.component == component and refused.value.reason == REFUSED_NOT_GIVEN
    # Nothing was read or written, and nobody was asked: not Plex, not a provider.
    assert (_tag_rows(db), _embedding_rows(db)) == before
    assert _index_files(db) == files_before
    assert plex.checks == 0 and plex.reads == 0
    assert ai.requests == [] and vectors.requests == []
    assert not marker_path(db).exists() and _history(db) == []
    assert engine.regenerate_result.wrote_nothing is True


def test_run_of_tags_alone_needs_no_embedding_provider(db: SQLiteManager) -> None:
    """A provider that the run does not need is not a reason to refuse it."""
    ids = _library(db, 3)
    embeddings_before = _embedding_rows(db)
    engine = _engine(db, _TagAI(), plex=_Plex(3))

    result = _regenerate(engine, generate_embeddings=False)

    assert _retagged(db) == ids
    assert _embedding_rows(db) == embeddings_before
    assert result.tags_written == 3 and result.embeddings.requested is False
    assert result.tracks_kept == 0 and result.finished is True
    assert not marker_path(db).exists()


def test_failed_batch_keeps_existing_tags(db: SQLiteManager, small_batches: None) -> None:
    """A request that fails without a fatal error loses that batch only, and no tags."""
    ids = _library(db, 6)
    # The second of three requests is refused. That is not a fatal error.
    ai = _TagAI(errors={ids[2]: ConnectionError("Connection refused")})
    engine = _engine(db, ai, plex=_Plex(6))

    result = _regenerate(engine, generate_embeddings=False)

    rows = _tag_rows(db)
    # The two tracks of the failed batch: same tags, same time, not counted.
    assert rows[ids[2]] == OLD_TAGS and rows[ids[3]] == OLD_TAGS
    assert _retagged(db) == ids[:2] + ids[4:]
    assert ai.requests == [ids[:2], ids[2:4], ids[4:]]
    assert result.tags_written == 4 and engine.tags_saved == 4
    assert result.tags.kept_ids == set(ids[2:4]) and result.tracks_kept == 2
    assert result.provider_failed is False and result.finished is True
    assert _history(db)[-1]["status"] == "success"
    # They still count as not regenerated in the database's own terms.
    assert db.get_track_ids_to_retag_since(datetime(2021, 1, 1, tzinfo=UTC)) == ids[2:4]


@pytest.mark.parametrize("how", ["left out", "empty lists"])
def test_omitted_track_keeps_existing_tags(
    how: str, db: SQLiteManager, small_batches: None
) -> None:
    """A reply that holds nothing for a track is no replacement for that track's tags."""
    ids = _library(db, 6)
    missing = [ids[1], ids[4]]
    ai = _TagAI(omit=missing) if how == "left out" else _TagAI(empty=missing)
    engine = _engine(db, ai, plex=_Plex(6))

    result = _regenerate(engine, generate_embeddings=False)

    rows = _tag_rows(db)
    assert rows[ids[1]] == OLD_TAGS and rows[ids[4]] == OLD_TAGS
    assert _retagged(db) == [ids[0], ids[2], ids[3], ids[5]]
    assert ai.sent == ids
    assert result.tags_written == 4 and engine.tags_saved == 4
    assert result.tags.kept_ids == set(missing) and result.tracks_kept == 2


def test_unparseable_reply_keeps_existing_tags(
    db: SQLiteManager, small_batches: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reply that cannot be read, on every attempt, leaves the batch's tags alone."""
    # The generator waits between its attempts. Only its own clock is replaced.
    monkeypatch.setattr(tag_generator_module, "time", SimpleNamespace(sleep=lambda seconds: None))
    ids = _library(db, 6)
    ai = _TagAI(unreadable=[ids[3]])
    engine = _engine(db, ai, plex=_Plex(6))

    result = _regenerate(engine, generate_embeddings=False)

    rows = _tag_rows(db)
    assert rows[ids[2]] == OLD_TAGS and rows[ids[3]] == OLD_TAGS
    assert _retagged(db) == ids[:2] + ids[4:]
    # The batch was asked for three times before the generator gave up on it.
    assert ai.requests.count(ids[2:4]) == 3
    assert result.tags_written == 4 and result.tracks_kept == 2
    assert result.provider_failed is False


@pytest.mark.parametrize("mode", ["regenerate", "sync"])
def test_tags_are_saved_after_each_batch(
    mode: str, db: SQLiteManager, small_batches: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error that stops the generator after two batches leaves both of them saved."""
    ids = _library(db, 6)
    if mode == "sync":
        # A plain sync tags the tracks that have no tags at all.
        conn = db.get_connection()
        conn.execute("UPDATE tracks SET tags = NULL, tags_generated_at = NULL")
        conn.commit()
    real = TagGenerator._generate_batch
    batches: List[List[int]] = []

    def third_batch_fails(self: TagGenerator, tracks: List[Dict[str, Any]]) -> Any:
        batches.append([track["id"] for track in tracks])
        if len(batches) == 3:
            raise RuntimeError("the reply could not be handled")
        return real(self, tracks)

    monkeypatch.setattr(TagGenerator, "_generate_batch", third_batch_fails)
    engine = _engine(db, _TagAI(), plex=_Plex(6))

    if mode == "regenerate":
        history = engine.regenerate_sync(generate_embeddings=False, tags_provider=FAKE_AI)
    else:
        history = engine.incremental_sync(generate_embeddings=False)

    assert batches == [ids[:2], ids[2:4], ids[4:]]
    rows = _tag_rows(db)
    for track_id in ids[:4]:
        assert rows[track_id][:3] == _new_tags(track_id) and rows[track_id][3] is not None
    untouched = OLD_TAGS if mode == "regenerate" else (None, None, None, None)
    assert rows[ids[4]] == untouched and rows[ids[5]] == untouched
    assert engine.tags_saved == 4
    if mode == "regenerate":
        result = engine.regenerate_result
        assert result.tags_written == 4 and result.tracks_kept == 2
        assert result.provider_failed is True and result.stopped_scope == SCOPE_TAGS
        assert result.error_message == "AI tagging stopped: the reply could not be handled."
        assert history.status == "partial"
        assert set(_marker(db)) == {"tags"}
    else:
        # As before: a plain sync goes on after a tag stage that failed.
        assert history.status == "success"
        assert engine.regenerate_result.requested is False
        assert not marker_path(db).exists()


def test_interrupted_regenerate_keeps_old_and_new(db: SQLiteManager, small_batches: None) -> None:
    """Ctrl+C after the first batch: two tracks have new tags, the rest what they had."""
    ids = _library(db, 6, other_model=True)
    index = _saved_index(db, ids)
    embeddings_before, files_before = _embedding_rows(db), _index_files(db)
    ai, vectors = _TagAI(errors={ids[2]: KeyboardInterrupt()}), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(6))

    with pytest.raises(KeyboardInterrupt):
        _regenerate(engine)

    # No track is without tags, and no track is without its embedding.
    assert _retagged(db) == ids[:2]
    assert all(row[0] for row in _tag_rows(db).values())
    assert _embedding_rows(db) == embeddings_before
    assert _index_files(db) == files_before
    assert vectors.requests == []
    result = engine.regenerate_result
    assert result.tags_written == 2 and engine.tags_saved == 2
    assert result.tags.kept_ids == set(ids[2:])
    assert result.embeddings.started is False and result.embeddings.kept == 6
    assert result.tracks_kept == 6
    assert result.provider_failed is False and result.finished is False
    assert _history(db)[-1]["status"] == "interrupted"
    assert _marker(db)["tags"]["provider"] == "fake" and set(_marker(db)) == {"tags"}


def test_second_regenerate_continues_with_the_remaining_tracks(
    db: SQLiteManager, small_batches: None
) -> None:
    """The run after an interrupted one sends the provider only what is still to do."""
    ids = _library(db, 6, other_model=True)
    index = _saved_index(db, ids)
    first = _engine(db, _TagAI(errors={ids[2]: KeyboardInterrupt()}), _Vectors(), index, _Plex(6))
    with pytest.raises(KeyboardInterrupt):
        _regenerate(first)
    marker_before = marker_path(db).read_text()

    # What the command's question is made from, before anything is written.
    plan = plan_regenerate(db, FAKE_AI, FAKE_VECTORS)
    assert plan.continued is True and plan.tags_done_earlier is False
    assert (plan.remaining, plan.total_tracks) == (4, 6)
    assert plan.tags.track_ids == tuple(ids[2:])
    assert plan.embeddings is not None and plan.embeddings.remaining == 6
    assert plan.embeddings.provider == FAKE_VECTORS
    assert plan.marker_path == marker_path(db)
    assert marker_path(db).read_text() == marker_before

    ai, vectors = _TagAI(), _Vectors()
    result = _regenerate(_engine(db, ai, vectors, index, _Plex(6)))

    # Only the four tracks the first run did not replace went to the AI provider.
    assert ai.sent == ids[2:] and ai.requests == [ids[2:4], ids[4:]]
    assert _retagged(db) == ids
    assert result.tags.continued is True and result.tags.to_do == 4
    assert result.tags_written == 4
    # The embeddings wait for the tags, so the first run made none: all are made now,
    # and each from the track's new tags.
    assert vectors.songs == [0, 1, 2, 3, 4, 5]
    assert all(f"tags: new-{ids[n]}" in text for n, text in enumerate(vectors.texts))
    assert _reembedded(db) == ids
    assert result.embeddings.continued is False and result.embeddings_written == 6
    assert result.tracks_kept == 0 and result.finished is True
    assert not marker_path(db).exists()
    assert _history(db)[-1]["status"] == "success"


def test_continue_run_false_regenerates_every_track(db: SQLiteManager, small_batches: None) -> None:
    """The web UI's rule: its regenerate never takes up an earlier run."""
    ids = _library(db, 4)
    first = _engine(db, _TagAI(errors={ids[2]: KeyboardInterrupt()}), plex=_Plex(4))
    with pytest.raises(KeyboardInterrupt):
        _regenerate(first, generate_embeddings=False)
    assert preview_run(db, SCOPE_TAGS, FAKE_AI).remaining == 2

    plan = plan_regenerate(db, FAKE_AI, continue_run=False)
    assert plan.continued is False and plan.remaining == 4 and plan.embeddings is None
    ai = _TagAI()
    result = _regenerate(
        _engine(db, ai, plex=_Plex(4)), generate_embeddings=False, continue_run=False
    )

    assert ai.sent == ids
    assert result.tags.continued is False and result.tags_written == 4
    assert not marker_path(db).exists()


def test_embeddings_wait_for_the_tags_pass(db: SQLiteManager, small_batches: None) -> None:
    """A fatal error in the tags pass: no embedding row changes, in that run."""
    ids = _library(db, 6, other_model=True)
    index = _saved_index(db, ids)
    embeddings_before, files_before = _embedding_rows(db), _index_files(db)
    ai, vectors = _TagAI(errors={ids[2]: RuntimeError(FATAL)}), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(6))

    result = _regenerate(engine)

    assert _retagged(db) == ids[:2]
    assert _embedding_rows(db) == embeddings_before
    assert _index_files(db) == files_before
    assert vectors.requests == []
    assert result.provider_failed is True and result.stopped_scope == SCOPE_TAGS
    assert (result.tags_written, result.embeddings_written) == (2, 0)
    assert result.embeddings.requested is True and result.embeddings.started is False
    assert result.embeddings.kept == 6 and result.tracks_kept == 6
    # Only the tags are open in the marker. The embeddings were never begun.
    assert set(_marker(db)) == {"tags"}

    # The run that continues does the rest of the tags first, then every embedding.
    ai, vectors = _TagAI(), _Vectors()
    result = _regenerate(_engine(db, ai, vectors, index, _Plex(6)))
    assert ai.sent == ids[2:]
    assert vectors.songs == [0, 1, 2, 3, 4, 5]
    assert _reembedded(db) == ids and result.tracks_kept == 0


def test_completed_run_reports_regenerated_and_kept_counts(
    db: SQLiteManager, small_batches: None
) -> None:
    ids = _library(db, 6)
    index = _saved_index(db, ids)
    ai, vectors = _TagAI(omit=[ids[1], ids[4]]), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(6))
    messages: List[Tuple[float, str]] = []

    history = engine.regenerate_sync(
        tags_provider=FAKE_AI,
        embeddings_provider=FAKE_VECTORS,
        progress_callback=lambda fraction, message: messages.append((fraction, message)),
    )

    result = engine.regenerate_result
    assert isinstance(result, RegenerateResult) and result.requested is True
    assert result.tags_written == 4 and result.embeddings_written == 6
    assert result.rows_written == 10 and result.wrote_nothing is False
    # Two tracks keep their previous tags. Their embedding is a new one.
    assert result.tracks_kept == 2 and result.tags.kept == 2 and result.embeddings.kept == 0
    assert result.provider_failed is False and result.stopped_scope == ""
    assert result.error is None and result.error_message == ""
    assert result.finished is True and result.describe() == ""
    assert (result.tags.to_do, result.embeddings.to_do) == (6, 6)
    assert engine.tags_saved == 4 and engine.embeddings_saved == 6
    assert _retagged(db) == [ids[0], ids[2], ids[3], ids[5]] and _reembedded(db) == ids
    assert history.status == "success" and history.error_message is None
    assert not marker_path(db).exists()
    assert sorted(index.track_ids) == ids and _ids_in_saved_index(db) == ids
    fractions = [fraction for fraction, _ in messages]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_run_that_writes_nothing_says_so(db: SQLiteManager, small_batches: None) -> None:
    """Every request fails without a fatal error: nothing is written or counted."""
    ids = _library(db, 4)
    before = _tag_rows(db)
    error = ConnectionError("Connection refused")
    ai = _TagAI(errors={track_id: error for track_id in ids})
    engine = _engine(db, ai, plex=_Plex(4))

    history = engine.regenerate_sync(generate_embeddings=False, tags_provider=FAKE_AI)

    result = engine.regenerate_result
    assert _tag_rows(db) == before
    assert result.wrote_nothing is True and result.tags_written == 0 and result.tracks_kept == 4
    # No error stopped the pass, so it reached its end. It still replaced nothing.
    assert result.provider_failed is False and result.finished is True
    assert history.status == "partial"
    assert history.error_message == ("No tags were regenerated. 4 tracks keep their previous data.")
    assert engine.tags_saved == 0
    # A pass that wrote no row stays open in the marker.
    assert set(_marker(db)) == {"tags"}


def test_completed_embeddings_pass_drops_other_models(
    db: SQLiteManager, small_batches: None
) -> None:
    ids = _library(db, 6, other_model=True)
    index = _saved_index(db, ids)
    assert len(db.get_all_embeddings(OTHER_MODEL)) == 6

    result = _regenerate(_engine(db, _TagAI(), _Vectors(), index, _Plex(6)))

    assert result.finished is True and result.embeddings_written == 6
    assert result.other_model_rows_removed == 6
    assert db.get_all_embeddings(OTHER_MODEL) == []
    assert {row[1] for row in _embedding_rows(db)} == {EMBEDDING_MODEL}
    assert _reembedded(db) == ids
    # The index was rebuilt from the current model's rows and saved.
    assert sorted(index.track_ids) == ids and index.index is not None
    assert index.index.ntotal == 6
    reloaded = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    assert sorted(reloaded.track_ids) == ids and reloaded.load_failed is False
    hits = reloaded.search(_new_vector("Song 3 by Artist One"), k=1)
    assert [track_id for track_id, _ in hits] == [ids[3]]
    assert not marker_path(db).exists()


def test_embedding_provider_error_stops_the_pass_and_keeps_every_embedding(
    db: SQLiteManager, small_batches: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The second embedding request fails. The error is reported, not raised."""
    ids = _library(db, 6, other_model=True)
    index = _saved_index(db, ids)
    error = RuntimeError(
        f"Error code: 401 - key {DUMMY_KEY} was rejected by "
        "http://leakuser:dummy-ui-password-0000@127.0.0.1:9/v1/embeddings"
    )
    ai, vectors = _TagAI(), _Vectors(fail_on_request=2, error=error)
    engine = _engine(db, ai, vectors, index, _Plex(6))

    with caplog.at_level(logging.DEBUG):
        history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    assert _retagged(db) == ids
    # Two new embeddings, four previous ones, and none missing.
    assert _reembedded(db) == ids[:2]
    assert sorted(_vectors_of(db)) == ids
    assert len(db.get_all_embeddings(OTHER_MODEL)) == 6
    assert result.provider_failed is True and result.stopped_scope == SCOPE_EMBEDDINGS
    assert result.error is error
    assert (result.tags_written, result.embeddings_written) == (6, 2)
    assert result.embeddings.kept_ids == set(ids[2:]) and result.tracks_kept == 4
    assert result.finished is False and result.other_model_rows_removed == 0
    assert engine.embeddings_saved == 2
    # The text to show names the pass and holds neither the key nor the password.
    assert result.error_message.startswith("Embedding generation stopped: Error code: 401")
    assert "http://***@127.0.0.1:9/v1/embeddings" in result.error_message
    for text in (result.error_message, history.error_message or "", caplog.text):
        assert DUMMY_KEY not in text and "dummy-ui-password-0000" not in text
    assert history.status == "partial"
    assert history.error_message == f"{result.error_message} 4 tracks keep their previous data."
    # The index follows the rows: all six tracks, two of them with a new vector.
    assert _ids_in_saved_index(db) == ids and sorted(index.track_ids) == ids
    # The tags pass is done, and the embeddings pass is open in the marker.
    assert set(_marker(db)) == {"embeddings"}
    assert _marker(db)["embeddings"]["provider"] == EMBEDDING_MODEL


def _stop_in_the_embeddings_pass(db: SQLiteManager) -> List[int]:
    """A run of both whose tags pass finished and whose second embedding request failed."""
    ids = _library(db, 6, other_model=True)
    engine = _engine(db, _TagAI(), _Vectors(fail_on_request=2), _saved_index(db, ids), _Plex(6))
    result = _regenerate(engine)
    assert result.stopped_scope == SCOPE_EMBEDDINGS and _reembedded(db) == ids[:2]
    return ids


def test_interrupted_embeddings_pass_is_continued_without_new_tags(
    db: SQLiteManager, small_batches: None
) -> None:
    """The tags were finished by the earlier run, so they are not paid for again."""
    ids = _stop_in_the_embeddings_pass(db)
    tags_before = _tag_rows(db)

    plan = plan_regenerate(db, FAKE_AI, FAKE_VECTORS)
    assert plan.continued is True and plan.tags_done_earlier is True
    assert (plan.remaining, plan.total_tracks) == (4, 6)
    assert plan.embeddings is not None and plan.embeddings.track_ids == tuple(ids[2:])
    assert plan.embeddings.provider == FAKE_VECTORS

    ai, vectors = _TagAI(), _Vectors()
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    result = _regenerate(_engine(db, ai, vectors, index, _Plex(6)))

    # No request to the AI provider, and only the four remaining embeddings.
    assert ai.requests == [] and _tag_rows(db) == tags_before
    assert vectors.songs == [2, 3, 4, 5]
    assert _reembedded(db) == ids
    assert result.tags.done_earlier is True and result.tags.to_do == 0
    assert result.tags_written == 0
    assert result.embeddings.continued is True and result.embeddings.to_do == 4
    assert result.embeddings_written == 4 and result.tracks_kept == 0
    assert result.finished is True and result.provider_failed is False
    # Every track is replaced now, so the earlier model's rows go and the marker too.
    assert result.other_model_rows_removed == 6
    assert db.get_all_embeddings(OTHER_MODEL) == []
    assert _ids_in_saved_index(db) == ids
    assert not marker_path(db).exists()


def test_track_added_since_is_tagged_before_its_embedding_is_made(
    db: SQLiteManager, small_batches: None
) -> None:
    """A track without any tags is tagged even when the tags pass was done earlier."""
    ids = _stop_in_the_embeddings_pass(db)
    artist_id = db.get_all_artists()[0].id
    album_id = db.get_all_albums()[0].id
    assert artist_id is not None and album_id is not None
    added = db.insert_track(
        Track(plex_key="t6", title="Song 6", artist_id=artist_id, album_id=album_id)
    )

    ai, vectors = _TagAI(), _Vectors()
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    result = _regenerate(_engine(db, ai, vectors, index, _Plex(7)))

    assert ai.sent == [added]
    assert result.tags.done_earlier is True and result.tags_written == 1
    assert vectors.songs == [2, 3, 4, 5, 6]
    assert f"tags: new-{added}" in vectors.texts[-1]
    assert sorted(_vectors_of(db)) == ids + [added]
    assert not marker_path(db).exists()


@pytest.mark.parametrize(
    "tags_provider, embeddings_provider",
    [
        (RunProvider("fake", "another-llm", "Fake"), FAKE_VECTORS),
        (RunProvider("other", "fake-llm", "Other"), FAKE_VECTORS),
        (FAKE_AI, RunProvider(EMBEDDING_MODEL, "other-vectors", "Fake")),
    ],
    ids=["AI model", "AI provider", "embedding model"],
)
def test_changed_model_starts_over_after_an_interrupted_embeddings_pass(
    tags_provider: RunProvider,
    embeddings_provider: RunProvider,
    db: SQLiteManager,
    small_batches: None,
) -> None:
    """The earlier run's tags were made with another model, so nothing is taken up."""
    ids = _stop_in_the_embeddings_pass(db)

    plan = plan_regenerate(db, tags_provider, embeddings_provider)
    assert plan.continued is False and plan.tags_done_earlier is False
    assert plan.remaining == 6
    assert plan.embeddings is not None and plan.embeddings.remaining == 6

    ai, vectors = _TagAI(), _Vectors()
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    result = _regenerate(
        _engine(db, ai, vectors, index, _Plex(6)),
        tags_provider=tags_provider,
        embeddings_provider=embeddings_provider,
    )

    assert ai.sent == ids and vectors.songs == [0, 1, 2, 3, 4, 5]
    assert result.tags.continued is False and result.tags.done_earlier is False
    assert (result.tags_written, result.embeddings_written) == (6, 6)
    assert not marker_path(db).exists()


def test_unfinished_embeddings_only_run_is_not_taken_for_a_run_of_both(
    db: SQLiteManager, small_batches: None
) -> None:
    """``embeddings generate --regenerate`` was stopped. ``sync regenerate`` still does all."""
    ids = _library(db, 6)
    index = _saved_index(db, ids)
    # What the embeddings command leaves behind: its own marker entry and two new rows.
    alone = open_run(db, SCOPE_EMBEDDINGS, FAKE_VECTORS)
    for track_id in ids[:2]:
        _embed_at(db, track_id, EMBEDDING_MODEL, datetime.now(UTC).replace(tzinfo=None))
    assert remaining_track_ids(db, alone) == ids[2:]
    assert preview_run(db, SCOPE_EMBEDDINGS, FAKE_VECTORS).continued is True

    plan = plan_regenerate(db, FAKE_AI, FAKE_VECTORS)
    assert plan.continued is False and plan.tags_done_earlier is False and plan.remaining == 6

    ai, vectors = _TagAI(), _Vectors()
    result = _regenerate(_engine(db, ai, vectors, index, _Plex(6)))

    assert ai.sent == ids and vectors.songs == [0, 1, 2, 3, 4, 5]
    assert (result.tags_written, result.embeddings_written) == (6, 6)
    assert result.tags.done_earlier is False and result.embeddings.continued is False
    assert not marker_path(db).exists()


def test_cancelled_regenerate_keeps_old_and_new(db: SQLiteManager, small_batches: None) -> None:
    """A cancel request (the web UI's Stop) after the first batch."""
    ids = _library(db, 6)
    index = _saved_index(db, ids)
    embeddings_before = _embedding_rows(db)
    cancel = Event()
    ai, vectors = _TagAI(on_request=lambda sent: cancel.set()), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(6))

    with pytest.raises(KeyboardInterrupt):
        _regenerate(engine, cancel_event=cancel)

    # The batch that was being answered is saved. Nothing else was asked for.
    assert ai.requests == [ids[:2]] and vectors.requests == []
    assert _retagged(db) == ids[:2]
    assert _embedding_rows(db) == embeddings_before
    result = engine.regenerate_result
    assert result.tags_written == 2 and result.tags.finished is False
    assert result.tracks_kept == 6 and result.provider_failed is False
    assert set(_marker(db)) == {"tags"}
    assert _history(db)[-1]["status"] == "interrupted"


def test_regenerate_local_needs_no_plex(
    db: SQLiteManager, small_batches: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``doctor --force``: both passes on the open database, under one progress display."""
    ids = _library(db, 6, other_model=True)
    index = _saved_index(db, ids)
    displays: List[Any] = []
    real_progress = sync_module.Progress

    def counted_progress(*columns: Any, **options: Any) -> Any:
        display = real_progress(*columns, **options)
        displays.append(display)
        return display

    monkeypatch.setattr(sync_module, "Progress", counted_progress)
    ai, vectors = _TagAI(omit=[ids[5]]), _Vectors()
    engine = _engine(db, ai, vectors, index, plex=None)
    messages: List[Tuple[float, str]] = []

    result = engine.regenerate_local(
        tags_provider=FAKE_AI,
        embeddings_provider=FAKE_VECTORS,
        progress_callback=lambda fraction, message: messages.append((fraction, message)),
    )

    assert result is engine.regenerate_result
    assert _retagged(db) == ids[:5] and _reembedded(db) == ids
    assert (result.tags_written, result.embeddings_written, result.tracks_kept) == (5, 6, 1)
    assert result.finished is True and result.provider_failed is False
    assert result.other_model_rows_removed == 6
    assert _ids_in_saved_index(db) == ids
    assert not marker_path(db).exists()
    # One display for both passes, and the tags come first in the progress.
    assert len(displays) == 1
    fractions = [fraction for fraction, _ in messages]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert max(f for f, message in messages if "AI tags" in message) <= 0.5
    # Plex was never needed: the engine has no client, and no sync was recorded.
    assert _history(db) == []
    with pytest.raises(RuntimeError, match="no Plex client"):
        engine.plex


def test_regenerate_local_follows_the_same_rules(db: SQLiteManager, small_batches: None) -> None:
    """Stopped in the tags pass, continued by the next call, and cancelled by an event."""
    ids = _library(db, 6)
    index = _saved_index(db, ids)
    embeddings_before = _embedding_rows(db)
    first = _engine(db, _TagAI(errors={ids[2]: RuntimeError(FATAL)}), _Vectors(), index)

    stopped = first.regenerate_local(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    assert stopped.provider_failed is True and stopped.stopped_scope == SCOPE_TAGS
    assert (stopped.tags_written, stopped.embeddings_written, stopped.tracks_kept) == (2, 0, 6)
    assert _retagged(db) == ids[:2] and _embedding_rows(db) == embeddings_before
    assert set(_marker(db)) == {"tags"}

    cancel = Event()
    ai = _TagAI(on_request=lambda sent: cancel.set())
    second = _engine(db, ai, _Vectors(), index)
    with pytest.raises(KeyboardInterrupt):
        second.regenerate_local(
            tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS, cancel_event=cancel
        )
    assert ai.requests == [ids[2:4]] and _retagged(db) == ids[:4]
    assert _embedding_rows(db) == embeddings_before

    ai, vectors = _TagAI(), _Vectors()
    third = _engine(db, ai, vectors, index)
    done = third.regenerate_local(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)
    assert ai.sent == ids[4:] and vectors.songs == [0, 1, 2, 3, 4, 5]
    assert done.tags.continued is True and done.finished is True and done.tracks_kept == 0
    assert _retagged(db) == ids and _reembedded(db) == ids
    assert not marker_path(db).exists()


def test_marker_that_cannot_be_written_does_not_stop_the_run(
    db: SQLiteManager,
    small_batches: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without a marker the run cannot be continued later. It still replaces every track."""
    ids = _library(db, 4, other_model=True)
    index = _saved_index(db, ids)

    def no_marker(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(sync_module, "open_run", no_marker)
    ai, vectors = _TagAI(), _Vectors()

    with caplog.at_level(logging.WARNING, logger="plexmix.plex.sync"):
        result = _regenerate(_engine(db, ai, vectors, index, _Plex(4)))

    assert ai.sent == ids and vectors.songs == [0, 1, 2, 3]
    assert _retagged(db) == ids and _reembedded(db) == ids
    assert result.finished is True and result.tracks_kept == 0
    assert result.other_model_rows_removed == 4
    assert caplog.text.count("The regenerate marker could not be written") == 2
    assert not marker_path(db).exists()


def test_run_marker_records_the_given_providers(db: SQLiteManager, small_batches: None) -> None:
    """The marker holds what the caller's own preview compares, not the objects' names."""
    ids = _library(db, 4)
    tags = RunProvider("openai", "gpt-5.5", "OpenAI")
    engine = _engine(db, _TagAI(errors={ids[2]: KeyboardInterrupt()}), plex=_Plex(4))
    with pytest.raises(KeyboardInterrupt):
        engine.regenerate_sync(generate_embeddings=False, tags_provider=tags)

    assert _marker(db)["tags"]["provider"] == "openai"
    assert _marker(db)["tags"]["model"] == "gpt-5.5"
    assert preview_run(db, SCOPE_TAGS, tags).continued is True

    # Without given providers they are read from the provider objects.
    finish_scope(db, SCOPE_TAGS)
    engine = _engine(db, _TagAI(errors={ids[2]: KeyboardInterrupt()}), plex=_Plex(4))
    with pytest.raises(KeyboardInterrupt):
        engine.regenerate_sync(generate_embeddings=False, continue_run=False)
    assert _marker(db)["tags"]["provider"] == "fake"
    assert _marker(db)["tags"]["model"] == "fake-llm"


def test_plain_sync_tags_and_embeds_only_what_is_missing(
    db: SQLiteManager, small_batches: None
) -> None:
    """Not a regenerate: untagged tracks and tracks without an embedding, as before."""
    ids = _old_tracks(db, 6)
    conn = db.get_connection()
    conn.execute(
        "UPDATE tracks SET tags = NULL, tags_generated_at = NULL WHERE id IN (?, ?)",
        (ids[1], ids[4]),
    )
    conn.commit()
    for track_id in ids:
        _embed_at(db, track_id, OTHER_MODEL, EMBEDDED_LONG_AGO)
    for track_id in ids[:3]:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    embeddings_before = _embedding_rows(db)
    index = _saved_index(db, ids[:3])
    ai, vectors = _TagAI(), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(6))

    history = engine.incremental_sync()

    # Only the two untagged tracks went to the AI provider.
    assert ai.sent == [ids[1], ids[4]]
    rows = _tag_rows(db)
    for track_id in (ids[1], ids[4]):
        assert rows[track_id][:3] == _new_tags(track_id) and rows[track_id][3] is not None
    for track_id in (ids[0], ids[2], ids[3], ids[5]):
        assert rows[track_id] == OLD_TAGS
    # Only the three tracks without an embedding of the active model were embedded.
    assert vectors.songs == [3, 4, 5]
    after = _embedding_rows(db)
    assert [row for row in after if row in embeddings_before] == embeddings_before
    assert len(after) == len(embeddings_before) + 3
    assert len(db.get_all_embeddings(OTHER_MODEL)) == 6
    assert sorted(index.track_ids) == ids and _ids_in_saved_index(db) == ids
    assert engine.tags_saved == 2 and engine.embeddings_saved == 3
    assert history.status == "success"
    # No regenerate run: no marker and an empty result.
    assert not marker_path(db).exists()
    assert engine.regenerate_result == RegenerateResult()
    assert engine.regenerate_result.requested is False


def test_plain_sync_goes_on_to_the_embeddings_after_a_fatal_tag_error(
    db: SQLiteManager, small_batches: None
) -> None:
    """As before: only a regenerate run holds the embeddings back."""
    ids = _old_tracks(db, 4)
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET tags = NULL, tags_generated_at = NULL")
    conn.commit()
    ai, vectors = _TagAI(errors={ids[2]: RuntimeError(FATAL)}), _Vectors()
    engine = _engine(db, ai, vectors, _saved_index(db, []), _Plex(4))

    history = engine.incremental_sync()

    assert engine.tags_saved == 2 and engine.tagging_error is not None
    assert len(ai.requests) == 2  # the run stopped at the fatal error
    assert vectors.songs == [0, 1, 2, 3]
    assert history.status == "success"
    assert _ids_in_saved_index(db) == ids


def test_plain_sync_does_not_stamp_a_track_whose_result_was_empty(
    db: SQLiteManager, small_batches: None
) -> None:
    """An untagged track with an empty result stays untagged, unstamped and uncounted."""
    ids = _tracks(db, 4, tagged_at=None)
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET tags = NULL")
    conn.commit()
    ai = _TagAI(empty=[ids[0]], omit=[ids[3]])
    engine = _engine(db, ai, plex=_Plex(4))

    engine.incremental_sync(generate_embeddings=False)

    rows = _tag_rows(db)
    assert rows[ids[0]] == (None, None, None, None)
    assert rows[ids[3]] == (None, None, None, None)
    assert rows[ids[1]][:3] == _new_tags(ids[1]) and rows[ids[2]][:3] == _new_tags(ids[2])
    assert engine.tags_saved == 2
    # The next sync tries the two again.
    again = _TagAI()
    _engine(db, again, plex=_Plex(4)).incremental_sync(generate_embeddings=False)
    assert again.sent == [ids[0], ids[3]]


def test_unreadable_index_is_rebuilt_from_the_rows_in_a_plain_sync(
    db: SQLiteManager, small_batches: None
) -> None:
    """After a failed load the index object is empty. Saving only the new vectors would
    leave a nearly empty index at the path, so the index is built from the database."""
    ids = _old_tracks(db, 6)
    for track_id in ids[:4]:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    _index_path(db).write_bytes(b"not a search index")
    _index_path(db).with_suffix(".metadata").write_bytes(b"not its metadata")
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    assert index.load_failed is True and index.track_ids == []
    vectors = _Vectors()
    engine = _engine(db, None, vectors, index, _Plex(6))

    engine.incremental_sync()

    assert vectors.songs == [4, 5]
    assert sorted(index.track_ids) == ids
    assert _ids_in_saved_index(db) == ids
    # The files that could not be read are still there, under their new names.
    assert len(index.moved_paths) == 2 and all(path.exists() for path in index.moved_paths)


@pytest.mark.parametrize("how", ["provider error", "interrupt", "no embeddings stage"])
def test_removal_saves_no_empty_index_over_an_unreadable_one(
    how: str, db: SQLiteManager, small_batches: None
) -> None:
    """A removal drops no vector from an index that failed to load, and saves nothing.

    That index is empty, its files are moved aside. Saved, it would stand at the path as
    the library's index when the embeddings stage that follows fails, is interrupted or
    does not run. The next sync would then add only the tracks it embeds and save a
    partial index with its success line. The stage builds it from all the rows instead.
    """
    ids = _old_tracks(db, 6)
    for track_id in ids[:4]:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    _index_path(db).write_bytes(b"not a search index")
    _index_path(db).with_suffix(".metadata").write_bytes(b"not its metadata")
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    assert index.load_failed is True and len(index.moved_paths) == 2
    stop: BaseException = KeyboardInterrupt() if how == "interrupt" else RuntimeError(FATAL)
    # Plex lists t0 to t4: t5 is removed, and t4 has no embedding yet.
    engine = _engine(db, None, _Vectors(fail_on_request=1, error=stop), index, _Plex(5))

    if how == "no embeddings stage":
        engine.incremental_sync(generate_embeddings=False)
    else:
        with pytest.raises(type(stop)):
            engine.incremental_sync()

    assert engine.outcome.removed == 1 and len(db.get_all_tracks()) == 5
    assert not _index_path(db).exists()
    assert not _index_path(db).with_suffix(".metadata").exists()
    assert all(path.exists() for path in index.moved_paths)

    # The next sync embeds the track that is left and saves an index of every track.
    vectors = _Vectors()
    fresh = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    history = _engine(db, None, vectors, fresh, _Plex(5)).incremental_sync()

    assert vectors.songs == [4]
    assert _ids_in_saved_index(db) == ids[:5]
    assert history.status == "success"


def test_removal_writes_no_index_when_the_index_holds_no_vector(
    db: SQLiteManager, small_batches: None
) -> None:
    """There is no vector to drop from an empty index, and no file is written for it."""
    _library(db, 6)
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    assert not _index_path(db).exists()
    engine = _engine(db, None, _Vectors(), index, _Plex(5))

    engine.incremental_sync(generate_embeddings=False)

    assert engine.outcome.removed == 1 and len(db.get_all_tracks()) == 5
    assert _index_files(db) == {}


@pytest.mark.parametrize("saved", ["no file", "empty file"])
@pytest.mark.parametrize("embedded", [4, 6], ids=["two tracks to embed", "nothing to embed"])
def test_index_without_vectors_is_built_from_all_rows_in_a_plain_sync(
    saved: str, embedded: int, db: SQLiteManager, small_batches: None
) -> None:
    """An index that holds no vector is rebuilt from the rows, never extended by this run.

    Its files may be gone (moved aside by an earlier failed load, or deleted) or saved
    empty. Adding only the vectors of this run saves a partial index, and a run with
    nothing to embed would leave the index as it is.
    """
    ids = _old_tracks(db, 6)
    for track_id in ids[:embedded]:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    if saved == "empty file":
        assert index.save_index(str(_index_path(db))) is True
        index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
        assert index.index is not None and index.index.ntotal == 0
    else:
        assert not _index_path(db).exists()
    vectors = _Vectors()

    history = _engine(db, None, vectors, index, _Plex(6)).incremental_sync()

    assert vectors.songs == list(range(embedded, 6))
    assert _ids_in_saved_index(db) == ids
    assert sorted(index.track_ids) == ids
    assert history.status == "success"


def test_other_model_rows_are_removed_only_where_a_replacement_exists(db: SQLiteManager) -> None:
    ids = _old_tracks(db, 3)
    for track_id in ids:
        _embed_at(db, track_id, OTHER_MODEL, EMBEDDED_LONG_AGO)
    for track_id in ids[:2]:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))

    # A pass without a marker: not every track has a row of the model, so no row of
    # another model is removed. The third track holds only a row of the other model,
    # so an index of the model's rows would not be complete: none is saved.
    assert finish_embeddings_pass(db, index, EMBEDDING_MODEL) == 0
    assert len(db.get_all_embeddings(OTHER_MODEL)) == 3
    assert index.last_rebuild == IndexRebuild(left_out=1)
    assert _index_files(db) == {} and index.track_ids == []

    # The statement itself never takes a track's only embedding.
    assert remove_other_model_embeddings(db, EMBEDDING_MODEL) == 2
    assert dict(db.get_all_embeddings(OTHER_MODEL)) == {ids[2]: OLD_VECTOR}
    assert sorted(_vectors_of(db)) == ids[:2]
    assert remove_other_model_embeddings(db, EMBEDDING_MODEL) == 0
    assert remove_other_model_embeddings(db, "a model without rows") == 0
    assert len(_embedding_rows(db)) == 3

    # Once every track has a row of the model, the pass removes the last other row.
    _embed_at(db, ids[2], EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    assert finish_embeddings_pass(db, index, EMBEDDING_MODEL) == 1
    assert db.get_all_embeddings(OTHER_MODEL) == []
    assert index.last_rebuild == IndexRebuild(vectors=3, saved=True)
    assert _ids_in_saved_index(db) == ids


def test_finished_embeddings_pass_keeps_other_models_while_tracks_remain(
    db: SQLiteManager, clock: _Clock
) -> None:
    """With a marker run: rows of other models go only when no track is left to do.

    The marker entry stays while a track is left, so that the next run makes only
    that embedding. It is cleared when the last track has its new one.
    """
    ids = _old_tracks(db, 3)
    for track_id in ids:
        _embed_at(db, track_id, OTHER_MODEL, EMBEDDED_LONG_AGO)
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    run = open_run(db, SCOPE_EMBEDDINGS, FAKE_VECTORS)
    # The pass replaced two of the three rows.
    for track_id in ids[:2]:
        _embed_at(db, track_id, EMBEDDING_MODEL, datetime(2026, 11, 18, 12, 0, 30))

    assert finish_embeddings_pass(db, index, EMBEDDING_MODEL, run) == 0
    assert len(db.get_all_embeddings(OTHER_MODEL)) == 3
    assert set(_marker(db)) == {SCOPE_EMBEDDINGS}
    assert remaining_track_ids(db, run) == [ids[2]]
    assert _ids_in_saved_index(db) == ids

    _embed_at(db, ids[2], EMBEDDING_MODEL, datetime(2026, 11, 18, 12, 0, 40))
    assert finish_embeddings_pass(db, index, EMBEDDING_MODEL, run) == 3
    assert db.get_all_embeddings(OTHER_MODEL) == []
    assert not marker_path(db).exists()


def test_index_is_not_rebuilt_while_a_track_holds_another_size(db: SQLiteManager) -> None:
    """A stopped run that replaces vectors of another size leaves both sizes behind.

    An index of the rows that fit would cover part of the library, so none is built.
    """
    ids = _old_tracks(db, 3)
    for track_id in ids:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    _saved_index(db, ids)
    db.insert_embedding(
        Embedding(track_id=ids[2], embedding_model=EMBEDDING_MODEL, embedding_dim=2, vector=[1, 2])
    )
    saved = _index_files(db)
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))

    assert rebuild_index_from_rows(db, index, EMBEDDING_MODEL) == 0
    assert index.last_rebuild == IndexRebuild(left_out=1)
    assert _index_files(db) == saved and sorted(index.track_ids) == ids
    # With no row of the model, the tracks that hold another one are in the way too.
    assert rebuild_index_from_rows(db, index, "a model without rows") == 0
    assert index.last_rebuild == IndexRebuild(left_out=3)
    assert _index_files(db) == saved and sorted(index.track_ids) == ids

    # Once the last row has the size of the index, it is built from all three.
    _embed_at(db, ids[2], EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    assert rebuild_index_from_rows(db, index, EMBEDDING_MODEL) == 3
    assert index.last_rebuild == IndexRebuild(vectors=3, saved=True)
    assert _ids_in_saved_index(db) == ids


def test_regenerate_result_counts_and_describes() -> None:
    assert RegenerateResult().requested is False and RegenerateResult().describe() == ""
    assert RegenerateResult().tracks_kept == 0 and RegenerateResult().finished is True

    tags = RegeneratePass(requested=True, started=True, finished=True)
    tags.to_do_ids, tags.written_ids = {1, 2, 3}, {1, 2}
    embeddings = RegeneratePass(requested=True, started=True, finished=True)
    embeddings.to_do_ids, embeddings.written_ids = {1, 2, 3, 4}, {1, 3, 4}
    result = RegenerateResult(tags=tags, embeddings=embeddings)
    assert (tags.to_do, tags.written, tags.kept) == (3, 2, 1)
    assert (result.tags_written, result.embeddings_written, result.rows_written) == (2, 3, 5)
    # Track 3 keeps its tags and track 2 its embedding: two tracks, counted once each.
    assert result.tracks_kept == 2
    assert result.finished is True and result.describe() == ""

    embeddings.finished = False
    assert result.finished is False
    assert result.describe() == (
        "The regenerate run was stopped before it reached its end. "
        "2 tracks keep their previous data."
    )
    result.stopped_scope = SCOPE_EMBEDDINGS
    result.error_message = "Embedding generation stopped: no answer."
    assert result.provider_failed is True
    assert result.describe() == (
        "Embedding generation stopped: no answer. 2 tracks keep their previous data."
    )

    nothing = RegenerateResult(
        tags=RegeneratePass(requested=True, started=True, finished=True),
        embeddings=RegeneratePass(requested=True, started=True, finished=True),
    )
    nothing.tags.to_do_ids = {7}
    assert nothing.wrote_nothing is True and nothing.nothing_to_do is False
    assert nothing.describe() == (
        "No tags or embeddings were regenerated. 1 track keeps its previous data."
    )

    # A library without tracks: nothing was to do, and that is no failure.
    empty = RegenerateResult(
        tags=RegeneratePass(requested=True, started=True, finished=True),
        embeddings=RegeneratePass(requested=True, started=True, finished=True),
    )
    assert empty.wrote_nothing is True and empty.nothing_to_do is True
    assert empty.describe() == "" and empty.no_new_tags is False
    assert RegenerateResult().nothing_to_do is False
    unfinished = RegenerateResult(tags=RegeneratePass(requested=True, started=True))
    assert unfinished.nothing_to_do is False

    # A run that did its work and could not rebuild the index says so, and only that.
    assert result.index_left_out == 0 and result.index_sentence == ""
    done = RegenerateResult(tags=tags, embeddings=RegeneratePass(requested=True, started=True))
    done.embeddings.finished = True
    done.embeddings.to_do_ids, done.embeddings.written_ids = {1, 2, 3}, {1, 2}
    assert done.describe() == ""
    done.index_left_out = 1
    assert done.index_sentence == left_out_sentence(1) == done.describe()
    assert done.finished is True


# ---------------------------------------------------------------------------
# A reply that holds only part of a result
# ---------------------------------------------------------------------------


def test_reply_without_tags_blanks_nothing(db: SQLiteManager, small_batches: None) -> None:
    """A reply with environments and instruments and no tags is no replacement for the tags.

    Written as it came, it would put an empty tags column where the track's tags were.
    """
    ids = _library(db, 6)
    missing = [ids[1], ids[4]]
    ai = _TagAI(no_tags=missing)
    engine = _engine(db, ai, plex=_Plex(6))

    result = _regenerate(engine, generate_embeddings=False)

    rows = _tag_rows(db)
    # Both tracks hold every column and the time they had, and count as kept.
    assert rows[ids[1]] == OLD_TAGS and rows[ids[4]] == OLD_TAGS
    assert all(row[0] for row in rows.values())
    assert _retagged(db) == [ids[0], ids[2], ids[3], ids[5]]
    assert ai.sent == ids
    assert result.tags_written == 4 and engine.tags_saved == 4
    assert result.tags.kept_ids == set(missing) and result.tracks_kept == 2
    # They are still to do, so a run that continues asks for them again.
    assert db.get_track_ids_to_retag_since(datetime(2021, 1, 1, tzinfo=UTC)) == missing


def test_reply_with_tags_only_keeps_the_other_columns(
    db: SQLiteManager, small_batches: None
) -> None:
    """An empty list never overwrites a column: the environments and instruments stay."""
    ids = _library(db, 6)
    conn = db.get_connection()
    conn.execute(
        "UPDATE tracks SET environments = 'old-env', instruments = 'old-inst' WHERE id = ?",
        (ids[2],),
    )
    conn.commit()
    ai = _TagAI(tags_only=[ids[2]])

    result = _regenerate(_engine(db, ai, plex=_Plex(6)), generate_embeddings=False)

    row = _tag_rows(db)[ids[2]]
    assert row[:3] == (f"new-{ids[2]}", "old-env", "old-inst")
    # The track got its new tags, so it is regenerated and stamped.
    assert row[3] is not None and row[3] != TAGGED_LONG_AGO
    assert result.tags_written == 6 and result.tracks_kept == 0


def test_plain_sync_writes_the_part_of_a_reply_that_has_content(
    db: SQLiteManager, small_batches: None
) -> None:
    """A plain sync tags tracks that have nothing. What a reply holds for one is written."""
    ids = _tracks(db, 2, tagged_at=None)
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET tags = NULL")
    conn.commit()
    engine = _engine(db, _TagAI(no_tags=[ids[0]]), plex=_Plex(2))

    engine.incremental_sync(generate_embeddings=False)

    rows = _tag_rows(db)
    # No empty text is written into the tags column for the list that was empty.
    assert rows[ids[0]][:3] == (None, "focus", "piano") and rows[ids[0]][3] is not None
    assert rows[ids[1]][:3] == _new_tags(ids[1])


# ---------------------------------------------------------------------------
# An AI provider that stops answering, or that gives nothing usable
# ---------------------------------------------------------------------------

RETRYABLE_SENTENCE = "Temporary AI provider error (timeout, rate limit, or server error)."


def _client_connection_error() -> Exception:
    """What the OpenAI client raises for a refused and for a closed connection."""
    import httpx
    import openai

    request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
    return openai.APIConnectionError(request=request)


@pytest.mark.parametrize(
    "error, attempts, said",
    [
        (
            ConnectionError("Connection refused"),
            1,
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            "Last error: Connection refused.",
        ),
        (
            RuntimeError(f"Error code: 500 - upstream failed for key {DUMMY_KEY}"),
            3,
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            f"{RETRYABLE_SENTENCE} Last error: Error code: 500 - upstream failed for key "
            "[redacted].",
        ),
        (
            RuntimeError("Error code: 429 - rate limit reached"),
            3,
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            f"{RETRYABLE_SENTENCE} Last error: Error code: 429 - rate limit reached.",
        ),
        (
            _client_connection_error(),
            1,
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            "Last error: Connection error.",
        ),
        (
            TimeoutError("no answer from the provider"),
            1,
            "AI tagging stopped: The AI provider failed 3 requests in a row. "
            "Last error: no answer from the provider.",
        ),
    ],
    ids=[
        "refused connection",
        "server error",
        "rate limit",
        "client connection error",
        "built-in TimeoutError",
    ],
)
def test_provider_that_stops_answering_stops_the_tags_pass(
    error: Exception,
    attempts: int,
    said: str,
    db: SQLiteManager,
    small_batches: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec "Provider failure part-way": the run ends as failed and can be continued.

    The first request is answered and every later one fails with an error that is
    not fatal by itself. Going on would keep every track that is left, call the pass
    finished and then make every embedding from tags that were not replaced.
    """
    # The generator waits between its attempts. Only its own clock is replaced.
    monkeypatch.setattr(tag_generator_module, "time", SimpleNamespace(sleep=lambda seconds: None))
    ids = _library(db, 12)
    index = _saved_index(db, ids)
    embeddings_before, index_before = _embedding_rows(db), _index_files(db)
    ai, vectors = _TagAI(errors={track_id: error for track_id in ids[2:]}), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(12))

    history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    # One answered request, then three failed ones, each tried as often as it is retried.
    assert ai.requests == [ids[:2]] + [
        batch for batch in (ids[2:4], ids[4:6], ids[6:8]) for _ in range(attempts)
    ]
    assert result.provider_failed is True and result.stopped_scope == SCOPE_TAGS
    assert isinstance(result.error, RepeatedBatchFailure)
    assert result.error.failed_batches == 3 and result.error.cause is error
    assert isinstance(engine.tagging_error, RepeatedBatchFailure)
    assert result.error_message == said
    assert result.tags_written == 2 and result.tags.kept_ids == set(ids[2:])
    assert result.tags.finished is False and result.finished is False
    assert _retagged(db) == ids[:2]
    # No embedding was made from tags that were not replaced, and the index is as it was.
    assert vectors.requests == [] and result.embeddings.started is False
    assert _embedding_rows(db) == embeddings_before and _index_files(db) == index_before
    assert history.status == "partial"
    # The embeddings that were not made count as kept too: twelve tracks, in the
    # result and in the history row alike.
    assert result.embeddings.kept_ids == set(ids) and result.tracks_kept == 12
    assert history.error_message == f"{said} 12 tracks keep their previous data."
    assert history.error_message == result.describe() == _history(db)[-1]["error_message"]
    assert DUMMY_KEY not in result.error_message and DUMMY_KEY not in history.error_message
    # The pass stays open in the marker, so the same call continues it.
    assert set(_marker(db)) == {"tags"}

    again, new_vectors = _TagAI(), _Vectors()
    later = _regenerate(
        _engine(
            db,
            again,
            new_vectors,
            VectorIndex(dimension=4, index_path=str(_index_path(db))),
            _Plex(12),
        )
    )

    assert again.sent == ids[2:]
    assert later.tags.continued is True and later.finished is True
    assert _retagged(db) == ids and _reembedded(db) == ids
    assert not marker_path(db).exists()


def test_failed_requests_that_are_not_three_in_a_row_do_not_stop_the_pass(
    db: SQLiteManager, small_batches: None
) -> None:
    """An answered request ends the row. Only the failed batches keep their tags."""
    ids = _library(db, 12)
    error = ConnectionError("Connection refused")
    # Requests 2, 3 and 5 of six fail.
    failing = ids[2:6] + ids[8:10]
    ai = _TagAI(errors={track_id: error for track_id in failing})
    engine = _engine(db, ai, plex=_Plex(12))

    result = _regenerate(engine, generate_embeddings=False)

    assert ai.sent == ids
    assert result.provider_failed is False and result.finished is True
    assert result.tags_written == 6 and result.tags.kept_ids == set(failing)
    assert _retagged(db) == ids[:2] + ids[6:8] + ids[10:]


def test_plain_sync_tries_every_batch_however_many_fail(
    db: SQLiteManager, small_batches: None
) -> None:
    """The stop after three failed requests belongs to a regenerate run alone."""
    ids = _tracks(db, 8, tagged_at=None)
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET tags = NULL")
    conn.commit()
    error = ConnectionError("Connection refused")
    ai = _TagAI(errors={track_id: error for track_id in ids})
    engine = _engine(db, ai, plex=_Plex(8))

    engine.incremental_sync(generate_embeddings=False)

    assert ai.sent == ids
    assert engine.tags_saved == 0 and engine.tagging_error is None


@pytest.mark.parametrize("how", ["left out", "empty lists", "unreadable", "requests fail"])
def test_run_without_any_new_tag_makes_no_embedding(
    how: str, db: SQLiteManager, small_batches: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AI provider gives nothing usable for any track: the embeddings are left alone.

    Made anyway, every one of them would come from the text it was made from: the
    provider would be paid for 4 embeddings and nothing new would exist.
    """
    monkeypatch.setattr(tag_generator_module, "time", SimpleNamespace(sleep=lambda seconds: None))
    # Four tracks are two requests: fewer than the three in a row that stop a pass.
    ids = _library(db, 4)
    index = _saved_index(db, ids)
    tags_before, embeddings_before = _tag_rows(db), _embedding_rows(db)
    index_before = _index_files(db)
    ai = {
        "left out": _TagAI(omit=ids),
        "empty lists": _TagAI(empty=ids),
        "unreadable": _TagAI(unreadable=ids),
        "requests fail": _TagAI(
            errors={track_id: ConnectionError("Connection refused") for track_id in ids}
        ),
    }[how]
    vectors = _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(4))

    history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    assert ai.sent == ids
    assert result.no_new_tags is True and result.tags.finished is True
    assert result.provider_failed is False and result.wrote_nothing is True
    # The embeddings pass was left out, so the run did not reach its end.
    assert vectors.requests == [] and result.embeddings.started is False
    assert result.finished is False and result.embeddings.kept_ids == set(ids)
    assert _tag_rows(db) == tags_before and _embedding_rows(db) == embeddings_before
    assert _index_files(db) == index_before
    assert result.describe() == (
        "No tags were regenerated, so no embedding was regenerated either. "
        "4 tracks keep their previous data."
    )
    assert history.status == "partial" and history.error_message == result.describe()
    # Nothing was written, so the pass stays open in the marker.
    assert set(_marker(db)) == {"tags"}


def test_no_new_tags_is_not_said_of_a_pass_that_had_nothing_to_do() -> None:
    done = RegeneratePass(requested=True, started=True, finished=True)
    assert RegenerateResult(tags=done).no_new_tags is False

    earlier = RegeneratePass(requested=True, started=True, finished=True, done_earlier=True)
    earlier.to_do_ids = {1}
    assert RegenerateResult(tags=earlier).no_new_tags is False

    stopped = RegeneratePass(requested=True, started=True, finished=False)
    stopped.to_do_ids = {1}
    assert RegenerateResult(tags=stopped).no_new_tags is False

    nothing = RegeneratePass(requested=True, started=True, finished=True)
    nothing.to_do_ids = {1}
    assert RegenerateResult(tags=nothing).no_new_tags is True

    # The run this one continues had replaced tags: the track that is left keeps its
    # previous tags, and that is a warning, not a run without any new tag.
    behind = RegeneratePass(
        requested=True, started=True, finished=True, continued=True, replaced_earlier=5
    )
    behind.to_do_ids = {1}
    assert RegenerateResult(tags=behind).no_new_tags is False


# ---------------------------------------------------------------------------
# A pass that is stopped after the provider or the vector size changed
# ---------------------------------------------------------------------------


def _library_of_an_earlier_provider(db: SQLiteManager, how: str) -> Tuple[List[int], Any, int]:
    """Six embedded tracks and their saved index, made before the provider was changed.

    ``another model``: the rows belong to an earlier model with the same vector
    size. ``another size``: the rows belong to the current model name and hold 4
    numbers, and the provider now makes 8. Returns the track ids, the index as the
    run opens it now, and the vector size the provider makes now.
    """
    ids = _old_tracks(db, 6)
    made_by = OTHER_MODEL if how == "another model" else EMBEDDING_MODEL
    for track_id in ids:
        _embed_at(db, track_id, made_by, EMBEDDED_LONG_AGO)
    _saved_index(db, ids)
    dimension = 4 if how == "another model" else 8
    opened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert opened.dimension_mismatch is (how == "another size")
    return ids, opened, dimension


@pytest.mark.parametrize("how", ["another model", "another size"])
@pytest.mark.parametrize("stop", ["provider error", "interrupt", "cancel"])
def test_stopped_pass_after_a_provider_change_keeps_the_saved_index(
    how: str, stop: str, db: SQLiteManager, small_batches: None
) -> None:
    """The saved index covers every track. An index of the few new rows must not replace it.

    With it would go the search over the rest of the library and, after a change of
    the vector size, the mismatch that tells every command to regenerate.
    """
    ids, index, dimension = _library_of_an_earlier_provider(db, how)
    rows_before, index_before = _embedding_rows(db), _index_files(db)
    cancel = Event()
    if stop == "provider error":
        vectors = _Vectors(fail_on_request=2, dimension=dimension)
    elif stop == "interrupt":
        vectors = _Vectors(fail_on_request=2, error=KeyboardInterrupt(), dimension=dimension)
    else:
        vectors = _Vectors(dimension=dimension, on_request=lambda number: cancel.set())
    engine = _engine(db, _TagAI(), vectors, index, _Plex(6))

    if stop == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            _regenerate(engine)
    else:
        # A provider error and a cancel request end the pass without raising.
        _regenerate(engine, cancel_event=cancel)

    result = engine.regenerate_result
    assert result.embeddings_written == 2 and result.embeddings.finished is False
    # Every row from before is still there, and two tracks have a new one.
    rows = _embedding_rows(db)
    new_rows = [row for row in rows if row[1] == EMBEDDING_MODEL and row[2] == dimension]
    if how == "another model":
        assert [row for row in rows if row[1] == OTHER_MODEL] == rows_before
        assert [row[0] for row in new_rows] == ids[:2]
    else:
        assert [row[0] for row in rows] == ids
        assert [row[2] for row in rows] == [8, 8, 4, 4, 4, 4]
    # The index files are the ones from before the run, byte for byte.
    assert _index_files(db) == index_before
    reopened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert sorted(reopened.track_ids) == ids
    assert reopened.dimension_mismatch is (how == "another size")


@pytest.mark.parametrize("how", ["another model", "another size"])
def test_finished_pass_after_a_provider_change_replaces_the_saved_index(
    how: str, db: SQLiteManager, small_batches: None
) -> None:
    """The run that reaches its end writes the replacement: an index of every track."""
    ids, index, dimension = _library_of_an_earlier_provider(db, how)
    engine = _engine(db, _TagAI(), _Vectors(dimension=dimension), index, _Plex(6))

    result = _regenerate(engine)

    assert result.finished is True and result.embeddings_written == 6
    assert db.get_all_embeddings(OTHER_MODEL) == []
    reopened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert sorted(reopened.track_ids) == ids
    assert reopened.dimension_mismatch is False and reopened.index.ntotal == 6


def test_index_follows_the_rows_only_when_it_is_complete(db: SQLiteManager) -> None:
    ids = _library(db, 3)
    index = _saved_index(db, ids)
    # Every track that holds an embedding holds one of the model and size: it may follow.
    assert index_may_follow_rows(db, index, EMBEDDING_MODEL) is True
    assert follow_rows_after_stopped_pass(db, index, EMBEDDING_MODEL) is True
    assert index.last_rebuild == IndexRebuild(vectors=3, saved=True)

    # No track has a row of this model, and each holds a row of another one.
    before = _index_files(db)
    assert index_may_follow_rows(db, index, "a model without rows") is False
    assert follow_rows_after_stopped_pass(db, index, "a model without rows") is False
    assert index.last_rebuild == IndexRebuild(left_out=3)
    assert _index_files(db) == before

    # The provider makes 8 numbers now and the rows hold 4: all three are in the way.
    mismatched = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert mismatched.dimension_mismatch is True
    assert index_may_follow_rows(db, mismatched, EMBEDDING_MODEL) is False
    assert follow_rows_after_stopped_pass(db, mismatched, EMBEDDING_MODEL) is False
    assert _index_files(db) == before
    assert VectorIndex(dimension=8, index_path=str(_index_path(db))).dimension_mismatch is True

    # The rule reads the rows, not the flags of the index object. An index that was
    # opened with a mismatch is rebuilt once every row has its size.
    for track_id in ids:
        db.insert_embedding(
            Embedding(
                track_id=track_id, embedding_model=EMBEDDING_MODEL, embedding_dim=8, vector=[1] * 8
            )
        )
    assert index_may_follow_rows(db, mismatched, EMBEDDING_MODEL) is True
    assert follow_rows_after_stopped_pass(db, mismatched, EMBEDDING_MODEL) is True
    reopened = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert reopened.dimension_mismatch is False and sorted(reopened.track_ids) == ids


def test_follow_after_a_stopped_pass_never_raises(
    db: SQLiteManager, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ids = _library(db, 3)
    index = _saved_index(db, ids)

    def fail(*args: Any, **kwargs: Any) -> int:
        raise OSError(f"no space left for key {DUMMY_KEY} at http://u:secretpass@127.0.0.1:9/x")

    monkeypatch.setattr(sync_module, "rebuild_index", fail)
    with caplog.at_level(logging.WARNING):
        assert follow_rows_after_stopped_pass(db, index, EMBEDDING_MODEL) is False
    assert "Failed to rebuild the vector index after a stopped pass" in caplog.text
    assert "secretpass" not in caplog.text


# ---------------------------------------------------------------------------
# A continued embeddings pass and tags that are new in this run
# ---------------------------------------------------------------------------


def test_track_tagged_only_in_the_continuing_run_gets_a_new_embedding(
    db: SQLiteManager, small_batches: None
) -> None:
    """Its earlier embedding was made from text without the tags it has now.

    Track 1 has no tags and the first run gets none for it. That run embeds it and
    then stops in its embeddings pass. The run that continues tags it, so its
    embedding is made again although the first run had made one.
    """
    ids = _library(db, 6)
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET tags = NULL, tags_generated_at = NULL WHERE id = ?", (ids[0],))
    conn.commit()
    first = _engine(
        db, _TagAI(omit=[ids[0]]), _Vectors(fail_on_request=2), _saved_index(db, ids), _Plex(6)
    )
    stopped = _regenerate(first)
    assert stopped.stopped_scope == SCOPE_EMBEDDINGS and _reembedded(db) == ids[:2]
    assert _tag_rows(db)[ids[0]][0] is None

    ai, vectors = _TagAI(), _Vectors()
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    result = _regenerate(_engine(db, ai, vectors, index, _Plex(6)))

    assert result.tags.done_earlier is True and ai.sent == [ids[0]]
    assert _tag_rows(db)[ids[0]][0] == f"new-{ids[0]}"
    # Song 0 is track 1: embedded again, now with its tags in the text.
    assert vectors.songs == [0, 2, 3, 4, 5]
    assert f"tags: new-{ids[0]}" in vectors.texts[0]
    assert result.embeddings_written == 5 and result.finished is True
    assert not marker_path(db).exists()


# ---------------------------------------------------------------------------
# Key values the provider objects do not show
# ---------------------------------------------------------------------------


def test_given_secrets_are_redacted_when_the_provider_objects_hold_no_key(
    db: SQLiteManager, small_batches: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Not every provider object keeps its key where the engine finds it (Gemini, Cohere).

    The caller knows the keys from the settings and hands them to the engine.
    """
    ids = _library(db, 6)
    index = _saved_index(db, ids)
    error = RuntimeError(f"Error: the key {DUMMY_KEY} was rejected")
    vectors = _Vectors(fail_on_request=2, error=error)
    # No ``api_key`` anywhere on the generator.
    vectors.provider = SimpleNamespace(model_name="fake-vectors")
    ai = _TagAI()
    ai.api_key = ""  # type: ignore[misc]
    engine = SyncEngine(
        plex_client=_Plex(6),
        db_manager=db,
        embedding_generator=vectors,
        vector_index=index,
        ai_provider=ai,
        known_secrets=[DUMMY_KEY, None, "", "abc"],
    )
    assert engine._known_secrets() == [DUMMY_KEY]

    with caplog.at_level(logging.DEBUG):
        history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    message = engine.regenerate_result.error_message
    assert message == "Embedding generation stopped: Error: the key [redacted] was rejected."
    for text in (message, history.error_message or "", caplog.text):
        assert DUMMY_KEY not in text
    assert _history(db)[-1]["error_message"].startswith(message)


def test_plain_sync_failure_text_holds_no_given_secret(
    db: SQLiteManager, caplog: pytest.LogCaptureFixture
) -> None:
    """The history row and the log line of a sync that a later stage failed."""
    ids = _tracks(db, 2)
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    error = RuntimeError(f"Error: the key {DUMMY_KEY} was rejected")
    vectors = _Vectors(fail_on_request=1, error=error)
    vectors.provider = SimpleNamespace(model_name="fake-vectors")
    engine = SyncEngine(
        plex_client=_Plex(2),
        db_manager=db,
        embedding_generator=vectors,
        vector_index=index,
        known_secrets=[DUMMY_KEY],
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        engine.incremental_sync()

    row = _history(db)[-1]
    assert row["status"] == "failed" and len(ids) == 2
    assert row["error_message"] == "Error: the key [redacted] was rejected"
    assert DUMMY_KEY not in caplog.text


def test_provider_secrets_lists_the_keys_of_both_providers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(regenerate_service, "resolve_ai_api_key", lambda name: "dummy-api-key-0000")
    monkeypatch.setattr(regenerate_service, "resolve_embedding_api_key", lambda name: " ")
    settings = _settings(
        tmp_path,
        "ai:\n  default_provider: custom\n  custom_api_key: dummy-plex-token-0000\n"
        "embedding:\n  default_provider: custom\n  custom_api_key: dummy-api-key-0000\n",
    )

    # Each value once, and nothing for a key that is not set.
    assert provider_secrets(settings) == ["dummy-plex-token-0000", "dummy-api-key-0000"]

    def unreadable(name: str) -> str:
        raise RuntimeError("the key store is locked")

    monkeypatch.setattr(regenerate_service, "resolve_ai_api_key", unreadable)
    assert provider_secrets(settings) == ["dummy-plex-token-0000", "dummy-api-key-0000"]


# ---------------------------------------------------------------------------
# Error text of any length
# ---------------------------------------------------------------------------


def test_refusal_detail_of_a_long_text_without_spaces_is_made_quickly(
    built: SimpleNamespace, tmp_path: Path
) -> None:
    """A provider can answer with a long body that holds no space at all.

    Looking for a partly hidden key in it must not take longer the longer it is: a
    pattern that did took seconds for a few thousand characters and a hang for more.
    """
    import time

    body = '{"error":"x",' * 40000 + f"sk-ab********wxyz {DUMMY_KEY}"
    built.embeddings = _FakeEmbeddings(error=RuntimeError(body))

    started = time.perf_counter()
    refused = _refusal(_settings(tmp_path), need_ai=False)
    taken = time.perf_counter() - started

    assert taken < 2.0, f"took {taken:.1f} s"
    assert DUMMY_KEY not in str(refused) and "wxyz" not in str(refused)


def test_partly_hidden_key_is_replaced_as_a_whole_word(
    built: SimpleNamespace, tmp_path: Path
) -> None:
    built.embeddings = _FakeEmbeddings(
        error=RuntimeError("Incorrect API key provided: sk-ab********wxyz. Check\tit: a***b")
    )

    refused = _refusal(_settings(tmp_path), need_ai=False)

    # The word with the hidden middle goes. Three stars are the mask of an address and stay.
    assert refused.detail == "Incorrect API key provided: [redacted] Check it: a***b"


# ---------------------------------------------------------------------------
# Lists that hold nothing but blanks
# ---------------------------------------------------------------------------

# Each of these is a list with an item in it and no text to write.
NO_CONTENT = [[""], [" "], ["", ""], [None], [{"a": 1}]]
NO_CONTENT_IDS = ["empty text", "blank", "two empty texts", "null", "a dict"]
COLUMNS = ("tags", "environments", "instruments")


class _ReplyAI:
    """An AI provider that answers the tracks in ``entries`` with exactly that entry.

    Every other track gets new tags, environments and instruments.
    """

    provider_name = "Fake"
    model = "fake-llm"

    def __init__(self, entries: Dict[int, Any]) -> None:
        self.entries = entries
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
        reply: Dict[str, Any] = {}
        for track_id in ids:
            if track_id in self.entries:
                reply[str(track_id)] = self.entries[track_id]
            else:
                tags, environments, instruments = _new_tags(track_id)
                reply[str(track_id)] = {
                    "tags": [tags],
                    "environments": [environments],
                    "instruments": [instruments],
                }
        return json.dumps(reply)


def _fully_tagged_library(db: SQLiteManager, count: int = 4) -> List[int]:
    """Tracks that hold tags, environments and instruments, all written long ago."""
    ids = _library(db, count)
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET environments = 'old-env', instruments = 'old-inst'")
    conn.commit()
    return ids


FULL_OLD = ("old,tags", "old-env", "old-inst", TAGGED_LONG_AGO)


@pytest.mark.parametrize("column", COLUMNS)
@pytest.mark.parametrize("items", NO_CONTENT, ids=NO_CONTENT_IDS)
def test_regenerate_never_empties_a_column_for_a_list_of_blanks(
    column: str, items: List[Any], db: SQLiteManager
) -> None:
    """``"tags": [""]`` is a list with an item in it, and still no tags to write."""
    ids = _fully_tagged_library(db)
    target = ids[1]
    entry: Dict[str, Any] = {"tags": ["x"], "environments": ["y"], "instruments": ["z"]}
    entry[column] = items
    engine = _engine(db, _ReplyAI({target: entry}), plex=_Plex(4))

    result = _regenerate(engine, generate_embeddings=False)

    rows = _tag_rows(db)
    row = rows[target]
    # No column that had content is empty now, in this track or in any other.
    assert all(all(value for value in other[:3]) for other in rows.values())
    # Nor does a column hold what the blanks used to be written as.
    assert not {"", ",", "none", "{'a': 1}"}.intersection(row[:3])
    if column == "tags":
        # Without tags the reply is no result: the track keeps all it had, is not
        # stamped and is not counted.
        assert row == FULL_OLD
        assert result.tags_written == 3 and result.tags.kept_ids == {target}
        assert engine.tags_saved == 3
    else:
        expected = {"tags": "x", "environments": "y", "instruments": "z"}
        expected[column] = {"environments": "old-env", "instruments": "old-inst"}[column]
        assert row[:3] == tuple(expected[name] for name in COLUMNS)
        assert row[3] != TAGGED_LONG_AGO
        assert result.tags_written == 4 and result.tracks_kept == 0
    # What the run counted is what the database holds.
    stamped = sorted(track_id for track_id, other in rows.items() if other[3] != TAGGED_LONG_AGO)
    assert stamped == sorted(result.tags.written_ids)


@pytest.mark.parametrize("column", COLUMNS)
@pytest.mark.parametrize(
    "items, written",
    [(["x", ""], "x"), ([" ", "x", None, "y"], "x,y"), ([1, 2], "1,2")],
    ids=["a text and a blank", "blanks between texts", "numbers"],
)
def test_regenerate_writes_the_items_that_have_content(
    column: str, items: List[Any], written: str, db: SQLiteManager
) -> None:
    ids = _fully_tagged_library(db)
    target = ids[2]
    entry: Dict[str, Any] = {"tags": ["a"], "environments": ["b"], "instruments": ["c"]}
    entry[column] = items

    result = _regenerate(
        _engine(db, _ReplyAI({target: entry}), plex=_Plex(4)), generate_embeddings=False
    )

    expected = {"tags": "a", "environments": "b", "instruments": "c"}
    expected[column] = written
    assert _tag_rows(db)[target][:3] == tuple(expected[name] for name in COLUMNS)
    assert result.tags_written == 4


def _untagged_library(db: SQLiteManager, count: int = 4) -> List[int]:
    ids = _tracks(db, count, tagged_at=None)
    conn = db.get_connection()
    conn.execute("UPDATE tracks SET tags = NULL")
    conn.commit()
    return ids


@pytest.mark.parametrize("items", NO_CONTENT, ids=NO_CONTENT_IDS)
def test_plain_sync_does_not_stamp_a_track_that_got_only_blanks(
    items: List[Any], db: SQLiteManager
) -> None:
    """An untagged track with ``[""]`` in every list stays untagged, unstamped and uncounted."""
    ids = _untagged_library(db)
    # One track of the library has tags. The run does not ask for it, and the reply
    # names it anyway.
    tagged = ids[3]
    db.update_track_tags(tagged, tags="kept", environments="kept-env", instruments="kept-inst")
    _tag_at(db, tagged, TAGGED_LONG_AGO)
    target = ids[0]
    blanks = {"tags": items, "environments": items, "instruments": items}
    ai = _ReplyAI({target: blanks, tagged: blanks})
    engine = _engine(db, ai, plex=_Plex(4))

    history = engine.incremental_sync(generate_embeddings=False)

    rows = _tag_rows(db)
    assert rows[target] == (None, None, None, None)
    assert rows[tagged] == ("kept", "kept-env", "kept-inst", TAGGED_LONG_AGO)
    assert rows[ids[1]][:3] == _new_tags(ids[1]) and rows[ids[2]][:3] == _new_tags(ids[2])
    assert engine.tags_saved == 2 and history.status == "success"
    assert ai.requests == [ids[:3]]
    # The track is still untagged, so the next sync asks for it again.
    again = _ReplyAI({})
    _engine(db, again, plex=_Plex(4)).incremental_sync(generate_embeddings=False)
    assert again.requests == [[target]]


@pytest.mark.parametrize("column", COLUMNS)
@pytest.mark.parametrize("items", NO_CONTENT, ids=NO_CONTENT_IDS)
def test_plain_sync_writes_no_empty_text_for_a_list_of_blanks(
    column: str, items: List[Any], db: SQLiteManager
) -> None:
    """The columns with content are written. The one with only blanks stays NULL."""
    ids = _untagged_library(db, 2)
    entry: Dict[str, Any] = {"tags": ["x", ""], "environments": ["y"], "instruments": ["z"]}
    entry[column] = items
    engine = _engine(db, _ReplyAI({ids[0]: entry}), plex=_Plex(2))

    engine.incremental_sync(generate_embeddings=False)

    row = _tag_rows(db)[ids[0]]
    expected: Dict[str, Optional[str]] = {"tags": "x", "environments": "y", "instruments": "z"}
    expected[column] = None
    assert row[:3] == tuple(expected[name] for name in COLUMNS)
    assert row[3] is not None and engine.tags_saved == 2


@pytest.mark.parametrize("column", COLUMNS)
@pytest.mark.parametrize(
    "items, written",
    [([1, 2], "1,2"), (["x", ""], "x"), ([None, " y ", {"a": 1}], "y")],
    ids=["numbers", "a text and a blank", "a text between what is none"],
)
def test_plain_sync_writes_the_items_that_have_content(
    column: str, items: List[Any], written: str, db: SQLiteManager
) -> None:
    ids = _untagged_library(db, 2)
    entry: Dict[str, Any] = {"tags": ["a"], "environments": ["b"], "instruments": ["c"]}
    entry[column] = items
    engine = _engine(db, _ReplyAI({ids[1]: entry}), plex=_Plex(2))

    engine.incremental_sync(generate_embeddings=False)

    expected = {"tags": "a", "environments": "b", "instruments": "c"}
    expected[column] = written
    row = _tag_rows(db)[ids[1]]
    assert row[:3] == tuple(expected[name] for name in COLUMNS) and row[3] is not None
    assert engine.tags_saved == 2


@pytest.mark.parametrize("mode", ["regenerate", "sync"])
def test_results_that_did_not_come_through_the_parser_are_cleaned_too(
    mode: str, db: SQLiteManager
) -> None:
    """The engine cleans the lists itself: it can be handed results by any generator."""
    ids = _fully_tagged_library(db) if mode == "regenerate" else _untagged_library(db)
    raw = {
        ids[0]: {"tags": [""], "environments": [" "], "instruments": [None]},
        ids[1]: {"tags": ["", ""], "environments": ["new-env"], "instruments": []},
        ids[2]: {"tags": [" kept as given ", ""], "environments": None, "instruments": [7]},
        ids[3]: {"tags": "one text is not a list", "environments": [{"a": 1}]},
    }
    engine = _engine(db, _TagAI(), plex=_Plex(4))
    engine.tag_generator = SimpleNamespace(generate_tags_batch=lambda tracks, **options: raw)

    if mode == "regenerate":
        _regenerate(engine, generate_embeddings=False)
    else:
        engine.incremental_sync(generate_embeddings=False)

    rows = _tag_rows(db)
    nothing = FULL_OLD if mode == "regenerate" else (None, None, None, None)
    assert rows[ids[0]] == nothing and rows[ids[3]] == nothing
    if mode == "regenerate":
        # A reply without tags is no result in a regenerate run.
        assert rows[ids[1]] == FULL_OLD
        assert rows[ids[2]][:3] == ("kept as given", "old-env", "7")
        assert engine.tags_saved == 1
    else:
        assert rows[ids[1]][:3] == (None, "new-env", None) and rows[ids[1]][3] is not None
        assert rows[ids[2]][:3] == ("kept as given", None, "7")
        assert engine.tags_saved == 2


# ---------------------------------------------------------------------------
# Requests that were answered without usable text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Could not extract text from Gemini response"),
        ValueError("Empty response from OpenAI"),
        ValueError("Empty response from custom endpoint"),
        IndexError("list index out of range"),
    ],
    ids=["Gemini", "OpenAI", "custom endpoint", "IndexError"],
)
def test_replies_without_text_do_not_stop_a_regenerate(
    error: Exception, db: SQLiteManager, small_batches: None
) -> None:
    """Three batches side by side each hold a track the provider gives no text for.

    Counted as failed requests, they stopped every regenerate at the same place for
    good: the tracks behind them were never reached and no embedding was ever made.
    """
    ids = _library(db, 16)
    index = _saved_index(db, ids)
    refused = [ids[4], ids[6], ids[8]]
    ai, vectors = _TagAI(errors={track_id: error for track_id in refused}), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(16))

    history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    # Every batch was sent once: the pass reached its end.
    assert ai.requests == [ids[n : n + 2] for n in range(0, 16, 2)]
    assert result.provider_failed is False and result.tags.finished is True
    assert engine.tagging_error is None
    # The tracks of the three batches keep their tags, and every other track has new ones.
    assert result.tags.kept_ids == set(ids[4:10])
    assert _retagged(db) == ids[:4] + ids[10:]
    # The marker entry is cleared and the embeddings pass ran for every track.
    assert not marker_path(db).exists()
    assert vectors.songs == list(range(16)) and result.embeddings_written == 16
    assert result.finished is True and history.status == "success"
    assert _ids_in_saved_index(db) == ids

    # A later run is a new run that reaches its end as well.
    assert plan_regenerate(db, FAKE_AI, FAKE_VECTORS).continued is False
    again = _TagAI(errors={track_id: error for track_id in refused})
    later = _regenerate(
        _engine(
            db,
            again,
            _Vectors(),
            VectorIndex(dimension=4, index_path=str(_index_path(db))),
            _Plex(16),
        )
    )
    assert len(again.requests) == 8 and later.finished is True
    assert not marker_path(db).exists()


@pytest.mark.parametrize("answer", ["tags", "no text"])
def test_two_failed_requests_an_answer_and_three_failed_stop_after_the_sixth(
    answer: str, db: SQLiteManager, small_batches: None
) -> None:
    ids = _library(db, 16)
    index = _saved_index(db, ids)
    embeddings_before, index_before = _embedding_rows(db), _index_files(db)
    refused = ConnectionError("Connection refused")
    errors: Dict[int, BaseException] = {
        ids[n]: refused for n in (0, 2, 6, 8, 10, 12, 14)  # requests 1, 2, 4, 5, 6, 7, 8
    }
    if answer == "no text":
        errors[ids[4]] = ValueError("Could not extract text from Gemini response")
    ai, vectors = _TagAI(errors=errors), _Vectors()
    engine = _engine(db, ai, vectors, index, _Plex(16))

    history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    assert ai.requests == [ids[n : n + 2] for n in range(0, 12, 2)]
    assert result.stopped_scope == SCOPE_TAGS and isinstance(result.error, RepeatedBatchFailure)
    assert result.error.failed_batches == 3 and result.error.cause is refused
    assert _retagged(db) == (ids[4:6] if answer == "tags" else [])
    # As before: the marker is kept, and the embeddings pass does not start.
    assert set(_marker(db)) == {"tags"} and vectors.requests == []
    assert _embedding_rows(db) == embeddings_before and _index_files(db) == index_before
    assert history.status == "partial"
    assert history.error_message.endswith("16 tracks keep their previous data.")


def test_plain_sync_tries_every_batch_whatever_the_error_is(
    db: SQLiteManager, small_batches: None
) -> None:
    ids = _untagged_library(db, 8)
    errors: Dict[int, BaseException] = {
        track_id: ConnectionError("Connection refused") for track_id in ids[:6]
    }
    ai = _TagAI(errors=errors)
    engine = _engine(db, ai, plex=_Plex(8))

    engine.incremental_sync(generate_embeddings=False)

    assert ai.sent == ids and engine.tagging_error is None
    assert engine.tags_saved == 2


# ---------------------------------------------------------------------------
# A continued tags pass whose remaining tracks all get nothing
# ---------------------------------------------------------------------------


def test_continued_pass_whose_last_track_gets_nothing_goes_on_to_the_embeddings(
    db: SQLiteManager, small_batches: None
) -> None:
    """The first run is cancelled when only a track the provider never tags is left.

    The run that continues gets nothing for it either. It used to fail with "No
    tags were regenerated", keep the marker and so fail the same way on every later
    run. The earlier run of the same marker did replace tags, so this run finishes
    with a warning and makes the embeddings.
    """
    ids = _library(db, 6)
    index = _saved_index(db, ids)
    never = ids[4]
    cancel = Event()
    first = _TagAI(omit=[never])
    first.on_request = lambda request: cancel.set() if len(first.requests) == 3 else None
    with pytest.raises(KeyboardInterrupt):
        _regenerate(_engine(db, first, _Vectors(), index, _Plex(6)), cancel_event=cancel)
    assert _retagged(db) == [track_id for track_id in ids if track_id != never]
    assert set(_marker(db)) == {"tags"}

    plan = plan_regenerate(db, FAKE_AI, FAKE_VECTORS)
    assert plan.continued is True and plan.remaining == 1

    ai, vectors = _TagAI(omit=[never]), _Vectors()
    engine = _engine(
        db, ai, vectors, VectorIndex(dimension=4, index_path=str(_index_path(db))), _Plex(6)
    )
    history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    assert ai.requests == [[never]]
    assert result.tags.continued is True and result.tags.replaced_earlier == 5
    assert result.tags.finished is True and result.tags_written == 0
    assert result.tags.kept_ids == {never} and result.tracks_kept == 1
    # Not a run without any new tag: the run it continues replaced five.
    assert result.no_new_tags is False and result.provider_failed is False
    assert vectors.songs == [0, 1, 2, 3, 4, 5] and result.embeddings_written == 6
    assert result.finished is True and result.describe() == ""
    assert history.status == "success" and history.error_message is None
    assert _tag_rows(db)[never] == OLD_TAGS
    assert not marker_path(db).exists()

    # No later run repeats a failure: each is a new run that reaches its end.
    for _ in range(2):
        assert plan_regenerate(db, FAKE_AI, FAKE_VECTORS).continued is False
        later = _regenerate(
            _engine(
                db,
                _TagAI(omit=[never]),
                _Vectors(),
                VectorIndex(dimension=4, index_path=str(_index_path(db))),
                _Plex(6),
            )
        )
        assert later.finished is True and later.no_new_tags is False
        assert not marker_path(db).exists()


def test_continued_tags_only_pass_whose_tracks_get_nothing_is_closed(
    db: SQLiteManager, small_batches: None
) -> None:
    """The same for a run of tags alone: its marker entry does not stay for ever."""
    ids = _library(db, 6)
    first = _TagAI(errors={ids[4]: KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        _regenerate(_engine(db, first, plex=_Plex(6)), generate_embeddings=False)
    assert _retagged(db) == ids[:4] and set(_marker(db)) == {"tags"}

    engine = _engine(db, _TagAI(omit=ids[4:]), plex=_Plex(6))
    history = engine.regenerate_sync(generate_embeddings=False, tags_provider=FAKE_AI)

    result = engine.regenerate_result
    assert result.tags.replaced_earlier == 4 and result.tags.kept_ids == set(ids[4:])
    assert result.no_new_tags is False and result.finished is True
    assert not marker_path(db).exists()
    # This run wrote nothing itself, and its history row says so.
    assert result.wrote_nothing is True and history.status == "partial"
    assert history.error_message == "No tags were regenerated. 2 tracks keep their previous data."
    # The command's report judges the run by what the whole marker run replaced: the
    # four tracks of the run before it count, so it ends with warnings, not as a failure.
    from plexmix.cli.console_helpers import regenerate_report

    report = regenerate_report(result)
    assert report.failed is False and report.kept_some is True and report.clean is False
    assert [count.text for count in report.counts] == [
        "AI tags: 0 tracks regenerated (the earlier run regenerated 4), "
        "2 keep their previous tags."
    ]


def test_fresh_run_without_any_new_tag_fails_again_and_can_still_be_left(
    db: SQLiteManager, small_batches: None
) -> None:
    """Nothing usable for any track is a failure, also on the run that continues it.

    Nothing was replaced since the marker's start, so the marker stays. A provider
    that answers ends it: no state is left that no command leaves.
    """
    ids = _library(db, 4)
    index = _saved_index(db, ids)
    embeddings_before = _embedding_rows(db)
    for _ in range(2):
        vectors = _Vectors()
        engine = _engine(db, _TagAI(empty=ids), vectors, index, _Plex(4))
        history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)
        result = engine.regenerate_result
        assert result.no_new_tags is True and result.tags.replaced_earlier == 0
        assert vectors.requests == [] and _embedding_rows(db) == embeddings_before
        assert history.error_message == (
            "No tags were regenerated, so no embedding was regenerated either. "
            "4 tracks keep their previous data."
        )
        assert set(_marker(db)) == {"tags"}

    done = _regenerate(_engine(db, _TagAI(), _Vectors(), index, _Plex(4)))
    assert done.finished is True and done.tags_written == 4 and done.embeddings_written == 4
    assert not marker_path(db).exists()


# ---------------------------------------------------------------------------
# A library without tracks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("embeddings", [True, False], ids=["tags and embeddings", "tags alone"])
@pytest.mark.parametrize("local", [False, True], ids=["sync", "local"])
def test_library_without_tracks_has_nothing_to_do(
    local: bool, embeddings: bool, db: SQLiteManager
) -> None:
    """No failure, no marker file left behind, and no request to a provider."""
    ai, vectors = _TagAI(), _Vectors()
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    engine = _engine(db, ai, vectors, index, None if local else _Plex(0))
    options: Dict[str, Any] = {"generate_embeddings": embeddings, "tags_provider": FAKE_AI}
    if embeddings:
        options["embeddings_provider"] = FAKE_VECTORS

    if local:
        result = engine.regenerate_local(**options)
    else:
        engine.regenerate_sync(**options)
        result = engine.regenerate_result

    assert result.nothing_to_do is True and result.finished is True
    assert result.wrote_nothing is True and result.no_new_tags is False
    assert result.provider_failed is False and result.tracks_kept == 0
    assert result.describe() == "" and result.index_left_out == 0
    assert ai.requests == [] and vectors.requests == []
    assert not marker_path(db).exists() and _leftovers(db) == []
    assert _index_files(db) == {}
    if not local:
        # Whatever the row says about the empty listing, it says nothing of a failed
        # regenerate.
        message = _history(db)[-1]["error_message"] or ""
        assert "regenerated" not in message and "previous data" not in message


# ---------------------------------------------------------------------------
# A plain sync after the vector size changed
# ---------------------------------------------------------------------------


def _library_with_a_saved_index_of_another_size(db: SQLiteManager, count: int = 8) -> List[int]:
    """``count`` tracks whose rows and saved index hold 4 numbers. The provider now makes 8."""
    ids = _old_tracks(db, count)
    for track_id in ids:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    _saved_index(db, ids)
    return ids


def test_plain_sync_after_a_size_change_leaves_the_saved_index(
    db: SQLiteManager, small_batches: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Plex lists one new track. Its embedding is made, and the index is not rebuilt.

    An index of that one track took the place of the library's index, with a
    success line, and the dimension mismatch that tells every command to
    regenerate was gone. 0.13.0 failed here with an error of the array library.
    """
    ids = _library_with_a_saved_index_of_another_size(db)
    index_before = _index_files(db)
    index = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert index.dimension_mismatch is True
    vectors = _Vectors(dimension=8)
    engine = _engine(db, None, vectors, index, _Plex(9))

    with caplog.at_level(logging.INFO, logger="plexmix.plex.sync"):
        history = engine.incremental_sync()

    # The sync did its work: the new track is in the library and has its embedding.
    new_track = db.get_track_by_plex_key("t8")
    assert new_track is not None and history.tracks_added == 1
    assert vectors.songs == [8] and engine.embeddings_saved == 1
    rows = _embedding_rows(db)
    assert [(row[0], row[2]) for row in rows] == [(i, 4) for i in ids] + [(new_track.id, 8)]
    # The index files are the ones from before, and still say that they do not fit.
    assert _index_files(db) == index_before
    reopened = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert reopened.dimension_mismatch is True and sorted(reopened.track_ids) == ids
    assert index.dimension_mismatch is True and sorted(index.track_ids) == ids
    # The run is partial, and says why in the words every caller uses.
    sentence = left_out_sentence(8)
    assert engine.outcome.index_left_out == 8 and engine.outcome.is_partial is True
    assert engine.outcome.index_sentence == sentence == engine.outcome.describe()
    assert index.last_rebuild == IndexRebuild(left_out=8)
    assert history.status == "partial" and history.error_message == sentence
    assert _history(db)[-1]["status"] == "partial"
    assert _history(db)[-1]["error_message"] == sentence
    assert "Rebuilt FAISS index" not in caplog.text
    assert "'plexmix embeddings generate --regenerate'" in sentence


def test_plain_sync_after_a_size_change_with_nothing_to_embed_is_partial(
    db: SQLiteManager, small_batches: None
) -> None:
    """Every track has a row of the model by name, so no embedding is made.

    The rows are of the old size, and the saved index is too. That sync ended with the
    success line and a "success" history row: the guard ran only after an embeddings
    pass that wrote rows, so the first sync said "finished with warnings" and the next
    one said nothing. The check runs when there is nothing to embed as well.
    """
    ids = _library_with_a_saved_index_of_another_size(db)
    index_before = _index_files(db)
    index = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert index.dimension_mismatch is True
    vectors = _Vectors(dimension=8)
    engine = _engine(db, None, vectors, index, _Plex(8))

    history = engine.incremental_sync()

    assert vectors.requests == [] and engine.embeddings_saved == 0
    assert [(row[0], row[2]) for row in _embedding_rows(db)] == [(i, 4) for i in ids]
    # The files are the ones from before, and the object keeps its mismatch.
    assert _index_files(db) == index_before
    assert index.dimension_mismatch is True and sorted(index.track_ids) == ids
    sentence = left_out_sentence(8)
    assert engine.outcome.index_left_out == 8 and engine.outcome.is_partial is True
    assert engine.outcome.index_sentence == sentence == engine.outcome.describe()
    assert index.last_rebuild == IndexRebuild(left_out=8)
    assert history.status == "partial" and history.error_message == sentence
    assert _history(db)[-1]["status"] == "partial"
    # Every later sync says the same until the embeddings are replaced.
    again = _engine(db, None, _Vectors(dimension=8), index, _Plex(8))
    assert again.incremental_sync().status == "partial"
    assert again.outcome.index_left_out == 8
    assert _index_files(db) == index_before


def test_plain_sync_with_nothing_to_embed_rebuilds_an_index_of_another_size(
    db: SQLiteManager, small_batches: None
) -> None:
    """Every row is of the new size and the saved index is not: it is rebuilt from the rows.

    This is the state after a regenerate that was stopped while it rebuilt the index.
    The rebuild is the guarded one, so it happens only because the index is complete.
    """
    ids = _old_tracks(db, 6)
    for track_id in ids:
        db.insert_embedding(
            Embedding(
                track_id=track_id,
                embedding_model=EMBEDDING_MODEL,
                embedding_dim=8,
                vector=[0.5] * 8,
                updated_at=EMBEDDED_LONG_AGO,
            )
        )
    _saved_index(db, ids)
    index = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert index.dimension_mismatch is True
    engine = _engine(db, None, _Vectors(dimension=8), index, _Plex(6))

    history = engine.incremental_sync()

    assert history.status == "success" and engine.outcome.is_partial is False
    assert engine.outcome.index_left_out == 0
    assert index.last_rebuild == IndexRebuild(vectors=6, saved=True)
    opened = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert opened.dimension_mismatch is False and sorted(opened.track_ids) == ids


def test_plain_sync_with_nothing_to_embed_and_an_index_that_fits_builds_nothing(
    db: SQLiteManager, small_batches: None
) -> None:
    """The ordinary case: the saved index fits, so the sync touches neither it nor the rows."""
    ids = _library(db, 4)
    _saved_index(db, ids)
    before = _index_files(db)
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    assert index.dimension_mismatch is False
    engine = _engine(db, None, _Vectors(), index, _Plex(4))

    history = engine.incremental_sync()

    assert history.status == "success" and engine.outcome.index_left_out == 0
    assert index.last_rebuild is None or index.last_rebuild == IndexRebuild()
    assert _index_files(db) == before


def test_plain_sync_after_a_size_change_is_whole_again_after_a_regenerate(
    db: SQLiteManager, small_batches: None
) -> None:
    ids = _library_with_a_saved_index_of_another_size(db, 4)
    index = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    first = _engine(db, None, _Vectors(dimension=8), index, _Plex(5))
    assert first.incremental_sync().status == "partial"

    done = _regenerate(_engine(db, _TagAI(), _Vectors(dimension=8), index, _Plex(5)))
    assert done.finished is True and done.index_left_out == 0 and done.describe() == ""

    opened = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert opened.dimension_mismatch is False and len(opened.track_ids) == 5
    # The next plain sync adds a track with an ordinary update of the index.
    engine = _engine(db, None, _Vectors(dimension=8), opened, _Plex(6))
    history = engine.incremental_sync()
    assert history.status == "success" and engine.outcome.is_partial is False
    assert engine.outcome.index_left_out == 0 and engine.outcome.index_sentence == ""
    again = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert again.dimension_mismatch is False and len(again.track_ids) == 6
    assert set(ids) < set(again.track_ids)


def test_interrupted_plain_sync_after_a_size_change_leaves_the_saved_index(
    db: SQLiteManager, small_batches: None
) -> None:
    ids = _library_with_a_saved_index_of_another_size(db)
    index_before = _index_files(db)
    index = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    vectors = _Vectors(fail_on_request=2, error=KeyboardInterrupt(), dimension=8)
    engine = _engine(db, None, vectors, index, _Plex(12))

    with pytest.raises(KeyboardInterrupt):
        engine.incremental_sync()

    # Two of the four new tracks were embedded before the interrupt.
    assert sorted(row[2] for row in _embedding_rows(db)) == [4] * 8 + [8, 8]
    assert _index_files(db) == index_before
    reopened = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert reopened.dimension_mismatch is True and sorted(reopened.track_ids) == ids


def _vectors_in_saved_index(db: SQLiteManager) -> Dict[int, List[float]]:
    """The vector the saved index holds for each track, as the index stores it (normalised)."""
    import faiss

    saved = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    assert saved.dimension_mismatch is False and saved.index is not None
    id_map = faiss.downcast_index(saved.index)
    positions = faiss.vector_to_array(id_map.id_map)
    flat = faiss.downcast_index(id_map.index)
    return {int(track_id): list(flat.reconstruct(n)) for n, track_id in enumerate(positions)}


def _stale_in_saved_index(db: SQLiteManager) -> List[int]:
    """The tracks whose vector in the saved index is not the one their row holds."""
    held = _vectors_in_saved_index(db)
    stale = []
    for track_id, row in _vectors_of(db).items():
        norm = sum(value * value for value in row) ** 0.5
        wanted = [value / norm for value in row]
        got = held.get(track_id)
        if got is None or any(abs(a - b) > 1e-5 for a, b in zip(got, wanted)):
            stale.append(track_id)
    return sorted(stale)


@pytest.mark.parametrize("stop", ["interrupt", "provider error"])
def test_stopped_plain_sync_after_a_provider_change_leaves_no_stale_vectors(
    stop: str, db: SQLiteManager, small_batches: None
) -> None:
    """The provider changed and the vector size did not (OpenAI to Custom, both 1536).

    The saved index loads without a mismatch and holds the earlier provider's vector of
    every track. A plain sync that is stopped after two of the six embeddings must put
    those two into the index. A rebuild from the rows is refused then (four tracks
    hold only the earlier row), and the next sync only embeds those four: the first two
    would keep the earlier provider's vectors in the index for good while their rows
    are new, and search would compare new query vectors with old ones, silently.
    """
    ids = _old_tracks(db, 6)
    for track_id in ids:
        _embed_at(db, track_id, OTHER_MODEL, EMBEDDED_LONG_AGO)
    _saved_index(db, ids)
    error: BaseException = KeyboardInterrupt() if stop == "interrupt" else RuntimeError(FATAL)
    first = _engine(
        db,
        None,
        _Vectors(fail_on_request=2, error=error),
        VectorIndex(dimension=4, index_path=str(_index_path(db))),
        _Plex(6),
    )
    with pytest.raises((KeyboardInterrupt, RuntimeError)):
        first.incremental_sync()

    # Two tracks hold a new row, and the index holds that vector for them. The other
    # four hold the earlier row and the earlier vector. Nothing is left out or stale.
    assert _vectors_of(db).keys() == set(ids[:2])
    assert _stale_in_saved_index(db) == []
    held = _vectors_in_saved_index(db)
    assert sorted(held) == ids
    norm = sum(value * value for value in OLD_VECTOR) ** 0.5
    for track_id in ids[2:]:
        assert held[track_id] == pytest.approx([value / norm for value in OLD_VECTOR], abs=1e-5)

    second = _engine(
        db,
        None,
        _Vectors(),
        VectorIndex(dimension=4, index_path=str(_index_path(db))),
        _Plex(6),
    )
    history = second.incremental_sync()
    assert history.status == "success" and second.outcome.is_partial is False
    assert sorted(_vectors_of(db)) == ids
    assert _stale_in_saved_index(db) == [] and sorted(_vectors_in_saved_index(db)) == ids


def test_plain_sync_logs_the_number_of_vectors_in_the_index(
    db: SQLiteManager, small_batches: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A row whose track is gone is in the table and not in the count that is logged."""
    ids = _old_tracks(db, 4)
    for track_id in ids[:2]:
        _embed_at(db, track_id, EMBEDDING_MODEL, EMBEDDED_LONG_AGO)
    conn = db.get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    db.insert_embedding(
        Embedding(track_id=900, embedding_model=EMBEDDING_MODEL, embedding_dim=2, vector=[1, 2])
    )
    conn.execute("PRAGMA foreign_keys = ON")
    assert len(db.get_all_embeddings(EMBEDDING_MODEL)) == 3
    # No index file yet, so the index is built from the rows.
    index = VectorIndex(dimension=4, index_path=str(_index_path(db)))
    index.index = None
    engine = _engine(db, None, _Vectors(), index, _Plex(4))

    with caplog.at_level(logging.INFO, logger="plexmix.plex.sync"):
        history = engine.incremental_sync()

    assert history.status == "success" and engine.outcome.index_left_out == 0
    assert "Rebuilt FAISS index with 4 vectors" in caplog.text
    assert _ids_in_saved_index(db) == ids


# ---------------------------------------------------------------------------
# A regenerate run and the complete index
# ---------------------------------------------------------------------------


def test_stopped_run_after_a_size_change_is_continued_to_a_complete_index(
    db: SQLiteManager, small_batches: None
) -> None:
    """Stopped part-way: the saved index stays. Continued to its end: every track is in it."""
    ids, index, dimension = _library_of_an_earlier_provider(db, "another size")
    index_before = _index_files(db)
    stopped = _regenerate(
        _engine(db, _TagAI(), _Vectors(fail_on_request=2, dimension=dimension), index, _Plex(6))
    )
    assert stopped.stopped_scope == SCOPE_EMBEDDINGS and stopped.embeddings_written == 2
    assert _index_files(db) == index_before
    # The stop is what the run reports. The index that stayed is no second error.
    assert stopped.index_left_out == 0 and "search index" not in stopped.describe()
    assert index.last_rebuild == IndexRebuild(left_out=4)
    # The marker names the vector size the run was started with.
    assert _marker(db)["embeddings"]["dimension"] == dimension

    ai, vectors = _TagAI(), _Vectors(dimension=dimension)
    opened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert opened.dimension_mismatch is True
    result = _regenerate(_engine(db, ai, vectors, opened, _Plex(6)))

    assert ai.requests == [] and vectors.songs == [2, 3, 4, 5]
    assert result.embeddings.continued is True and result.finished is True
    assert result.index_left_out == 0 and result.describe() == ""
    assert opened.last_rebuild == IndexRebuild(vectors=6, saved=True)
    reopened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert reopened.dimension_mismatch is False
    assert sorted(reopened.track_ids) == ids and reopened.index.ntotal == 6
    assert not marker_path(db).exists()


class _OneShort(_Vectors):
    """An embedding generator whose second request comes back one vector short."""

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        made = super().generate_batch_embeddings(texts, batch_size)
        return made[:-1] if len(self.requests) == 2 else made


@pytest.mark.parametrize("how", ["another model", "another size"])
def test_run_that_ends_with_a_track_in_the_way_saves_no_index(
    how: str, db: SQLiteManager, small_batches: None
) -> None:
    """The pass reached its end, and one track still holds only its earlier embedding.

    An index of the other five would take the place of the index of all six. So
    none is saved, the run is not a clean finish, and the callers can read why.
    """
    ids, index, dimension = _library_of_an_earlier_provider(db, how)
    index_before = _index_files(db)
    vectors = _OneShort(dimension=dimension)
    engine = _engine(db, _TagAI(), vectors, index, _Plex(6))

    history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    left = ids[3]
    assert result.embeddings.finished is True and result.finished is True
    assert result.embeddings_written == 5 and result.embeddings.kept_ids == {left}
    assert result.provider_failed is False
    # The track keeps the embedding it had.
    kept = [row for row in _embedding_rows(db) if row[0] == left]
    assert len(kept) == 1 and kept[0][2] == 4
    assert kept[0][1] == (OTHER_MODEL if how == "another model" else EMBEDDING_MODEL)
    # No index was saved: the files are the ones from before, byte for byte.
    assert _index_files(db) == index_before
    reopened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert sorted(reopened.track_ids) == ids
    assert reopened.dimension_mismatch is (how == "another size")
    # Not a clean finish, and the callers are told.
    sentence = left_out_sentence(1)
    assert result.index_left_out == 1 and result.index_sentence == sentence
    assert result.describe() == sentence
    assert index.last_rebuild == IndexRebuild(left_out=1)
    assert history.status == "partial" and history.error_message == sentence
    # The embeddings entry stays while that track is left, so that the next run
    # makes only its embedding (see the tests below). The tags are done.
    assert set(_marker(db)) == {SCOPE_EMBEDDINGS}

    # Started over by hand (the marker is deleted), the same through
    # regenerate_local, which writes no history row.
    marker_path(db).unlink()
    local = _engine(db, _TagAI(), _OneShort(dimension=dimension), index)
    again = local.regenerate_local(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)
    assert again.index_left_out == 1 and again.describe() == sentence
    assert _index_files(db) == index_before


class _Omitting(_Vectors):
    """An embedding generator that never returns a vector for some songs.

    It drops the vector of a song only when that song is the last of its request,
    so that the vectors that are returned stay in step with the tracks that were sent.
    """

    def __init__(self, songs: Iterable[int], dimension: int = 4) -> None:
        super().__init__(dimension=dimension)
        self.never = set(songs)

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        made = super().generate_batch_embeddings(texts, batch_size)
        if texts and _song_number(texts[-1]) in self.never:
            return made[:-1]
        return made


@pytest.mark.parametrize("how", ["another model", "another size"])
def test_run_that_left_a_track_in_the_way_continues_with_only_that_track(
    how: str, db: SQLiteManager, small_batches: None
) -> None:
    """The provider returned no vector for one track, so the index could not be saved.

    The marker entry stays while that track is left. The next run makes only its
    embedding and then saves the index, in place of starting over for every track
    and paying the provider for the whole library again.
    """
    ids, index, dimension = _library_of_an_earlier_provider(db, how)
    index_before = _index_files(db)
    left = ids[3]
    first = _engine(db, _TagAI(), _OneShort(dimension=dimension), index, _Plex(6))
    first.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)
    assert first.regenerate_result.index_left_out == 1
    assert set(_marker(db)) == {SCOPE_EMBEDDINGS}
    assert _index_files(db) == index_before

    ai, vectors = _TagAI(), _Vectors(dimension=dimension)
    again = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    engine = _engine(db, ai, vectors, again, _Plex(6))
    history = engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    # Only the track that was left is sent, and no tag is paid for again.
    assert vectors.songs == [3] and ai.requests == []
    assert result.embeddings.continued is True and result.embeddings.replaced_earlier == 5
    assert result.embeddings_written == 1 and result.embeddings.kept_ids == set()
    assert result.finished is True and result.index_left_out == 0
    assert history.status == "success" and history.error_message is None
    assert not marker_path(db).exists()
    # Nothing is in the way now: the index covers every track and has its new size.
    assert _index_files(db) != index_before
    reopened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert sorted(reopened.track_ids) == ids and reopened.dimension_mismatch is False
    assert all(row[0] == left or row[2] == dimension for row in _embedding_rows(db))
    assert {row[2] for row in _embedding_rows(db)} == {dimension}


def test_continued_run_whose_last_track_gets_no_vector_ends_with_warnings_every_time(
    db: SQLiteManager, small_batches: None
) -> None:
    """The provider never returns a vector for one track, and the index stays unsaved.

    Each run continues with that one track, so it costs one request and no more. It
    ends with warnings and with exit status 0: the run before it had replaced the
    others. It never fails for ever and never starts over for the whole library.
    """
    ids, index, dimension = _library_of_an_earlier_provider(db, "another size")
    index_before = _index_files(db)
    left = ids[3]
    first = _engine(db, _TagAI(), _Omitting([3], dimension), index, _Plex(6))
    first.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)
    assert first.regenerate_result.embeddings.kept_ids == {left}

    from plexmix.cli.console_helpers import regenerate_report

    for _ in range(3):
        ai, vectors = _TagAI(), _Omitting([3], dimension)
        again = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
        engine = _engine(db, ai, vectors, again, _Plex(6))
        engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

        result = engine.regenerate_result
        assert vectors.songs == [3] and ai.requests == []
        assert result.embeddings.continued is True and result.embeddings.replaced_earlier == 5
        assert result.embeddings_written == 0 and result.embeddings.kept_ids == {left}
        assert result.finished is True and result.provider_failed is False
        assert result.index_left_out == 1
        # The run is not a failure, and it is not a clean finish either.
        report = regenerate_report(result)
        assert report.failed is False and report.clean is False
        assert report.kept_some is True and report.index_left_out == 1
        assert set(_marker(db)) == {SCOPE_EMBEDDINGS}
        assert _index_files(db) == index_before

    # A provider that returns it ends the run, and nothing is left behind.
    done = _engine(
        db,
        _TagAI(),
        _Vectors(dimension=dimension),
        VectorIndex(dimension=dimension, index_path=str(_index_path(db))),
        _Plex(6),
    )
    done.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)
    assert done.regenerate_result.index_left_out == 0 and done.regenerate_result.finished
    assert not marker_path(db).exists()
    reopened = VectorIndex(dimension=dimension, index_path=str(_index_path(db)))
    assert sorted(reopened.track_ids) == ids and reopened.dimension_mismatch is False


def test_fresh_embeddings_run_that_gets_no_vector_at_all_keeps_its_entry_and_every_track(
    db: SQLiteManager, small_batches: None
) -> None:
    """A new run in which the provider returns no vector for any track.

    Nothing was replaced, so no earlier run exists to count: the entry stays and
    every track keeps its embedding. (The command of the embeddings alone ends as a
    failed run then; this run still made new tags.)"""
    ids, index, dimension = _library_of_an_earlier_provider(db, "another size")
    index_before = _index_files(db)
    engine = _engine(db, _TagAI(), _AnswersNothing(dimension), index, _Plex(6))

    engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=FAKE_VECTORS)

    result = engine.regenerate_result
    assert result.embeddings_written == 0 and result.embeddings.replaced_earlier == 0
    assert result.embeddings.kept_ids == set(ids) and result.index_left_out == 0
    # The entry stays, and a later run can still end it: nothing is left that no command leaves.
    assert set(_marker(db)) == {SCOPE_EMBEDDINGS}
    assert _index_files(db) == index_before


class _AnswersNothing(_Vectors):
    """An embedding generator that answers every request with no vector."""

    def __init__(self, dimension: int = 4) -> None:
        super().__init__(dimension=dimension)

    def generate_batch_embeddings(
        self, texts: List[str], batch_size: int = 100
    ) -> List[List[float]]:
        super().generate_batch_embeddings(texts, batch_size)
        return []


# ---------------------------------------------------------------------------
# The vector size in the run marker
# ---------------------------------------------------------------------------

SIZED = RunProvider("custom", "stand-in-embed", "Custom", 8)


def test_embeddings_marker_records_the_vector_size(db: SQLiteManager, clock: _Clock) -> None:
    ids = _tracks(db, 3)

    first = open_run(db, SCOPE_EMBEDDINGS, SIZED)

    assert first.continued is False and first.provider == SIZED
    assert _marker(db) == {
        "embeddings": {
            "started_at": "2026-11-18T12:00:00Z",
            "provider": "custom",
            "model": "stand-in-embed",
            "dimension": 8,
        }
    }
    _embed_at(db, ids[0], "custom", datetime(2026, 11, 18, 12, 0, 30))
    clock.advance(minutes=10)
    again = open_run(db, SCOPE_EMBEDDINGS, SIZED)
    assert again.continued is True and again.track_ids == (ids[1], ids[2])


def test_changed_vector_size_starts_a_new_embeddings_run(db: SQLiteManager, clock: _Clock) -> None:
    """A custom endpoint or a local model can change its size and keep its name.

    The rows the earlier run wrote are newer than its start and no longer fit, so
    a run that continued would leave them as they are.
    """
    ids = _tracks(db, 3)
    open_run(db, SCOPE_EMBEDDINGS, SIZED)
    _embed_at(db, ids[0], "custom", datetime(2026, 11, 18, 12, 0, 30))
    clock.advance(minutes=10)
    assert preview_run(db, SCOPE_EMBEDDINGS, SIZED).continued is True
    resized = RunProvider("custom", "stand-in-embed", "Custom", 16)

    assert preview_run(db, SCOPE_EMBEDDINGS, resized).continued is False
    run = open_run(db, SCOPE_EMBEDDINGS, resized)

    assert run.continued is False and run.track_ids == tuple(ids)
    assert _marker(db)["embeddings"]["dimension"] == 16
    assert _marker(db)["embeddings"]["started_at"] == "2026-11-18T12:10:00Z"


@pytest.mark.parametrize("stored", ["absent", None, True, "8", 0, -8, 8.0])
def test_marker_without_a_usable_size_is_another_identity(
    stored: Any, db: SQLiteManager, clock: _Clock
) -> None:
    """A marker that was written without a size, or with something that is none."""
    ids = _tracks(db, 3)
    unsized = RunProvider("custom", "stand-in-embed", "Custom")
    open_run(db, SCOPE_EMBEDDINGS, unsized)
    assert "dimension" not in _marker(db)["embeddings"]
    if stored != "absent":
        data = _marker(db)
        data["embeddings"]["dimension"] = stored
        marker_path(db).write_text(json.dumps(data), encoding="utf-8")
    _embed_at(db, ids[0], "custom", datetime(2026, 11, 18, 12, 0, 30))
    clock.advance(minutes=10)

    # A caller that names a size does not take such a run up.
    assert preview_run(db, SCOPE_EMBEDDINGS, SIZED).continued is False
    # One that names none compares the provider and the model alone, as before.
    assert preview_run(db, SCOPE_EMBEDDINGS, unsized).continued is True


def test_tags_marker_holds_no_size(db: SQLiteManager, clock: _Clock) -> None:
    _tracks(db, 2)
    open_run(db, SCOPE_TAGS, TAGS)
    assert _marker(db) == {"tags": TAGS_ENTRY}


def test_size_change_between_two_runs_starts_a_new_embeddings_run(
    db: SQLiteManager, small_batches: None
) -> None:
    """The engine reads the size from the generator when the caller names none."""
    ids = _stop_in_the_embeddings_pass(db)
    assert _marker(db)["embeddings"]["dimension"] == 4
    sized = RunProvider(EMBEDDING_MODEL, "fake-vectors", "Fake", 4)
    assert plan_regenerate(db, FAKE_AI, sized).tags_done_earlier is True
    resized = RunProvider(EMBEDDING_MODEL, "fake-vectors", "Fake", 8)
    plan = plan_regenerate(db, FAKE_AI, resized)
    assert plan.continued is False and plan.remaining == 6

    # The same names, and the provider makes 8 numbers now.
    ai, vectors = _TagAI(), _Vectors(dimension=8)
    index = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    result = _regenerate(_engine(db, ai, vectors, index, _Plex(6)))

    # A new run: every track is tagged and embedded again, the two the earlier run
    # had embedded with 4 numbers included.
    assert ai.sent == ids and vectors.songs == [0, 1, 2, 3, 4, 5]
    assert result.tags.done_earlier is False and result.embeddings.continued is False
    assert result.finished is True and result.index_left_out == 0
    assert [row[2] for row in _embedding_rows(db)] == [8] * 6
    reopened = VectorIndex(dimension=8, index_path=str(_index_path(db)))
    assert reopened.dimension_mismatch is False and sorted(reopened.track_ids) == ids
    assert not marker_path(db).exists()


def test_given_size_is_kept_and_a_missing_one_is_read_from_the_generator(
    db: SQLiteManager, small_batches: None
) -> None:
    ids = _library(db, 4)
    for given, recorded in ((FAKE_VECTORS, 4), (RunProvider(EMBEDDING_MODEL, "m", "Fake", 99), 99)):
        engine = _engine(db, _TagAI(), _Vectors(fail_on_request=1), _saved_index(db, ids), _Plex(4))
        engine.regenerate_sync(tags_provider=FAKE_AI, embeddings_provider=given, continue_run=False)
        assert _marker(db)["embeddings"]["dimension"] == recorded

    # Without given providers the size comes from the generator as well.
    engine = _engine(db, _TagAI(), _Vectors(fail_on_request=1), _saved_index(db, ids), _Plex(4))
    engine.regenerate_sync(continue_run=False)
    assert _marker(db)["embeddings"]["dimension"] == 4
    assert _marker(db)["embeddings"]["provider"] == EMBEDDING_MODEL


# ---------------------------------------------------------------------------
# A key that the provider repeats with its middle hidden
# ---------------------------------------------------------------------------

PARTLY_HIDDEN = "Incorrect API key provided: sk-ab********wxyz. Check\tit: a***b"


def test_engine_redaction_replaces_a_partly_hidden_key() -> None:
    """One rule for the engine, the provider check and the CLI: the whole word goes."""
    from plexmix.utils.redaction import REDACTED, redact_partly_hidden_keys

    cleaned = "Incorrect API key provided: [redacted] Check\tit: a***b"
    assert redact_partly_hidden_keys(PARTLY_HIDDEN) == cleaned
    assert sync_module._redact(PARTLY_HIDDEN) == cleaned
    assert sync_module._redact(f"{PARTLY_HIDDEN} {DUMMY_KEY}", [DUMMY_KEY]).endswith("[redacted]")
    assert sync_module._plain_error_text(RuntimeError(PARTLY_HIDDEN)) == (
        "Incorrect API key provided: [redacted] Check it: a***b"
    )
    assert REDACTED == "[redacted]" == sync_module._REDACTED == regenerate_service._REDACTED
    # A text without such a word comes back as it is, white space included.
    plain = "two  spaces,\na line break and a***b"
    assert redact_partly_hidden_keys(plain) is plain
    assert redact_partly_hidden_keys("") == ""
    assert redact_partly_hidden_keys("a\n\n ****\tb") == "a\n\n [redacted]\tb"


def test_partly_hidden_key_is_found_quickly_in_a_long_text_without_spaces() -> None:
    import time

    from plexmix.utils.redaction import redact_partly_hidden_keys

    body = '{"error":"x",' * 40000 + "sk-ab********wxyz tail"
    started = time.perf_counter()
    cleaned = redact_partly_hidden_keys(body)
    taken = time.perf_counter() - started

    assert taken < 2.0, f"took {taken:.1f} s"
    assert cleaned == "[redacted] tail"


@pytest.mark.parametrize("mode", ["regenerate", "sync"])
def test_partly_hidden_key_stays_out_of_the_result_and_the_history_row(
    mode: str, db: SQLiteManager, small_batches: None, caplog: pytest.LogCaptureFixture
) -> None:
    ids = _library(db, 4) if mode == "regenerate" else _old_tracks(db, 4)
    error = RuntimeError(f"Error code: 401 - {PARTLY_HIDDEN}")
    vectors = _Vectors(fail_on_request=1, error=error)
    engine = _engine(db, _TagAI(), vectors, _saved_index(db, ids), _Plex(4))

    with caplog.at_level(logging.DEBUG, logger="plexmix.plex.sync"):
        if mode == "regenerate":
            _regenerate(engine)
            assert "wxyz" not in engine.regenerate_result.error_message
            assert "[redacted]" in engine.regenerate_result.error_message
        else:
            with pytest.raises(RuntimeError):
                engine.incremental_sync()

    row = _history(db)[-1]
    assert row["status"] == ("partial" if mode == "regenerate" else "failed")
    assert "wxyz" not in row["error_message"] and "sk-ab" not in row["error_message"]
    assert "[redacted]" in row["error_message"]
    assert "wxyz" not in caplog.text
