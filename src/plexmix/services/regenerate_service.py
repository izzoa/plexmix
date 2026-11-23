"""What every "regenerate" action shares: the provider check and the run marker.

A regenerate action replaces tags and embeddings in place. Nothing is cleared first,
so a run that fails or is stopped leaves every track with its new or its old data.

``check_providers`` runs before any change. It builds the providers the job needs,
sends one small request to each and raises :class:`RegenerateRefused` when one of
them is not ready.

The run marker is what lets a later run continue an unfinished one. It is a small
JSON file beside the database that holds, for tags and for embeddings, when the run
started and with which provider and model (and, for embeddings, which vector size).
A track is still to do when its tags or its embedding were last written before that
start time. ``preview_run`` reads the marker without writing, ``open_run`` starts or
continues a run, and ``finish_scope`` removes a scope from the marker. Callers never
read the file themselves.

This module imports nothing from ``plexmix.plex``, so the sync engine can import it.
"""

import contextlib
import importlib.util
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple, Union

from plexmix.ai.errors import classify_provider_error
from plexmix.config.credentials import validate_api_key
from plexmix.config.settings import Settings
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.services.providers import (
    ProviderFailure,
    build_ai_provider,
    build_embedding_generator_with_reason,
    canonical_ai_provider,
    resolve_ai_api_key,
    resolve_embedding_api_key,
)
from plexmix.services.readiness import Cause, embedding_model_for, scope_provider_text
from plexmix.services.registry import (
    AI_PROVIDERS,
    ai_provider_display_name,
    embedding_provider_display_name,
    get_default_ai_model,
    requires_ai_api_key,
)
from plexmix.utils.embeddings import EmbeddingGenerator
from plexmix.utils.redaction import REDACTED, mask_urls_in_text, redact_partly_hidden_keys

logger = logging.getLogger(__name__)

# The two things a regenerate run rebuilds. Each has its own entry in the marker.
SCOPE_TAGS = "tags"
SCOPE_EMBEDDINGS = "embeddings"
SCOPES: Tuple[str, ...] = (SCOPE_TAGS, SCOPE_EMBEDDINGS)

# What a refusal is about (``RegenerateRefused.component``). The first two are the
# names the readiness store uses for the same components.
COMPONENT_AI = "ai"
COMPONENT_EMBEDDINGS = "embeddings"
COMPONENT_INDEX = "index"

# Why a run was refused (``RegenerateRefused.reason``), with the plain cause of each.
REFUSED_NO_KEY = "no_key"
REFUSED_NOT_STARTED = "not_started"
REFUSED_NO_ANSWER = "no_answer"
REFUSED_NOT_GIVEN = "not_given"

CAUSE_NO_KEY = "no API key is stored"
CAUSE_NOT_STARTED = "it could not be started"
CAUSE_NO_ANSWER = "it did not answer the test request"
CAUSE_NOT_GIVEN = "none was given to this run"

_CAUSES: Dict[str, str] = {
    REFUSED_NO_KEY: CAUSE_NO_KEY,
    REFUSED_NOT_STARTED: CAUSE_NOT_STARTED,
    REFUSED_NO_ANSWER: CAUSE_NO_ANSWER,
    REFUSED_NOT_GIVEN: CAUSE_NOT_GIVEN,
}
_ROLES: Dict[str, str] = {
    COMPONENT_AI: "AI provider",
    COMPONENT_EMBEDDINGS: "embedding provider",
    COMPONENT_INDEX: "search index",
}

# The test requests. The reply is one word, but the limit is generous: a model that
# reasons before it answers spends part of the limit on that, and a limit that is
# too low makes a working provider look broken. The time limit is the providers' own
# default, so a local server that still has to load its model is not refused.
_TEST_PROMPT = "Reply with the single word OK."
_TEST_MAX_TOKENS = 512
_TEST_TIMEOUT_SECONDS = 30
_TEST_TEXT = "PlexMix provider check"

# How the built-in AI providers word a reply that came back without text.
_EMPTY_REPLY_PREFIXES = (
    "Empty response from",
    "Empty text response from",
    "Could not extract text from",
)

# The Python module each AI provider needs, and the package that holds it.
_AI_PACKAGES: Dict[str, Tuple[str, str]] = {
    "gemini": ("google.genai", "google-genai"),
    "openai": ("openai", "openai"),
    "claude": ("anthropic", "anthropic"),
    "cohere": ("cohere", "cohere"),
    "custom": ("openai", "openai"),
    "local": ("transformers", "transformers"),
}

_REDACTED = REDACTED
_MIN_SECRET_CHARS = 4
_DETAIL_MAX_CHARS = 300

MARKER_SUFFIX = ".regenerate.json"
_MARKER_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Later than any time a row can hold: with it the two queries return every track.
_EVERY_TRACK = datetime(9999, 1, 1)

# How long a new run waits for the clock to pass the newest row (see ``_new_start``).
_MAX_START_WAIT_SECONDS = 5


class RegenerateRefused(Exception):
    """A regenerate run was refused before it changed anything.

    ``provider`` is the provider's display name ("OpenAI"; "" when the run was given
    no provider at all) and ``cause`` a plain cause without a full stop. ``str()``
    gives one or two sentences made of both and of ``detail``. A refusal that
    ``check_providers`` raises holds no API key in any of that text: known key
    values are replaced first.

    For callers that record the outcome: ``component`` is ``COMPONENT_AI``,
    ``COMPONENT_EMBEDDINGS`` or ``COMPONENT_INDEX``; ``reason`` is one of the
    ``REFUSED_*`` codes; ``failure`` is the typed reason of a provider that has no
    key or could not be started; ``error`` is the provider's own exception when the
    test request failed. ``error`` is there to be classified. Its text is not
    redacted, so never print or log it.
    """

    def __init__(
        self,
        provider: str,
        cause: str,
        *,
        component: str = "",
        reason: str = "",
        detail: str = "",
        failure: Optional[ProviderFailure] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(provider, cause)
        self.provider = provider
        self.cause = cause
        self.component = component
        self.reason = reason
        self.detail = detail
        self.failure = failure
        self.error = error

    @classmethod
    def not_given(cls, component: str) -> "RegenerateRefused":
        """The refusal of a run that was started without a provider or an index."""
        return cls("", CAUSE_NOT_GIVEN, component=component, reason=REFUSED_NOT_GIVEN)

    @property
    def role(self) -> str:
        """What the refusal is about, for example "AI provider" ("" when unknown)."""
        return _ROLES.get(self.component, "")

    @property
    def message(self) -> str:
        """The whole refusal as text that is safe to print and to log."""
        if self.role and self.provider:
            subject = f"The {self.role} {self.provider}"
        elif self.role:
            subject = f"The {self.role}"
        else:
            subject = self.provider or "A provider"
        text = f"{subject} is not ready: {self.cause.rstrip('. ')}."
        detail = _sentence(self.detail)
        return f"{text} {detail}" if detail else text

    def __str__(self) -> str:
        return self.message


class CheckedProviders(NamedTuple):
    """What ``check_providers`` returns. A provider that was not needed is ``None``."""

    ai_provider: Optional[Any]
    embedding_generator: Optional[EmbeddingGenerator]


@dataclass(frozen=True)
class RunProvider:
    """The provider and model of one scope, as the marker records them.

    ``provider`` is the provider id from the settings ("openai"). For embeddings it
    is also the ``embedding_model`` value of the rows that provider writes. ``label``
    is the display name for a message. ``dimension`` is the vector size of an
    embedding provider, and None for an AI provider. A run of embeddings is
    continued only while the size stays the same: a custom endpoint or a local
    model can change its size and keep its name.
    """

    provider: str
    model: str
    label: str = ""
    dimension: Optional[int] = None

    @property
    def text(self) -> str:
        """Display name and model for a question: "OpenAI · gpt-5.4-mini"."""
        return scope_provider_text(self.label or self.provider, self.model)


@dataclass(frozen=True)
class RegenerateRun:
    """One scope of a regenerate run: which tracks are still to do, and why.

    ``continued`` is True when an unfinished earlier run is taken up again, and then
    ``track_ids`` holds only the tracks that run did not replace. For a new run it
    holds every track. ``started_at`` is the run's start time in UTC and whole
    seconds. It is ``None`` only in a preview of a run that has not been started.
    """

    scope: str
    provider: RunProvider
    continued: bool
    track_ids: Tuple[int, ...]
    total_tracks: int
    started_at: Optional[datetime]
    marker_path: Path

    @property
    def remaining(self) -> int:
        """How many tracks are still to do."""
        return len(self.track_ids)


# ── Provider check ───────────────────────────────────────────────────────────


def check_providers(
    settings: Settings,
    need_ai: bool,
    need_embeddings: bool,
    ai_provider_name: Optional[str] = None,
) -> CheckedProviders:
    """Build the providers a regenerate run needs and send one test request to each.

    Only a needed provider is built and asked. The AI provider gets one short
    completion and the embedding provider embeds one short text. Returns the
    providers that passed, ready for the run. ``ai_provider_name`` selects another
    AI provider than the configured one.

    Raises:
        RegenerateRefused: a needed provider has no key stored, could not be
            started or did not answer. Nothing has been changed.
    """
    ai_provider = _checked_ai_provider(settings, ai_provider_name) if need_ai else None
    generator = _checked_embedding_generator(settings) if need_embeddings else None
    return CheckedProviders(ai_provider, generator)


def provider_secrets(settings: Settings, ai_provider_name: Optional[str] = None) -> List[str]:
    """The key values of the providers a regenerate run uses, for redaction only.

    The stored key of the AI provider and of the embedding provider, and the keys
    and the token that the settings hold for a custom or a local endpoint. Only the
    values that are set. Give them to ``SyncEngine(known_secrets=...)``, which
    replaces them in every error text it logs, stores or hands back: not every
    provider object keeps its key where the engine can find it. Never print or log
    what this returns. A key store that cannot be read gives no value and no error.
    """
    values: List[Optional[str]] = [
        settings.ai.custom_api_key,
        settings.ai.local_auth_token,
        settings.embedding.custom_api_key,
    ]
    try:
        name = ai_provider_name or settings.ai.default_provider or "gemini"
        values.append(resolve_ai_api_key(canonical_ai_provider(name)))
        embedding_id = (settings.embedding.default_provider or "").strip().lower()
        values.append(resolve_embedding_api_key(embedding_id))
    except Exception as exc:
        logger.debug("Could not read a provider key for redaction: %s", type(exc).__name__)
    secrets: List[str] = []
    for value in values:
        text = value.strip() if isinstance(value, str) else ""
        if len(text) >= _MIN_SECRET_CHARS and text not in secrets:
            secrets.append(text)
    return secrets


def _checked_ai_provider(settings: Settings, ai_provider_name: Optional[str]) -> Any:
    name = ai_provider_name or settings.ai.default_provider or "gemini"
    provider_id = canonical_ai_provider(name)
    label = ai_provider_display_name(provider_id) or name
    key = resolve_ai_api_key(provider_id)
    secrets = [key, settings.ai.custom_api_key, settings.ai.local_auth_token]

    provider = build_ai_provider(settings, provider_name=name, silent=True)
    if provider is None:
        raise _not_started(
            COMPONENT_AI, label, _diagnose_ai_provider(settings, provider_id, key), secrets
        )
    try:
        provider.complete(_TEST_PROMPT, max_tokens=_TEST_MAX_TOKENS, timeout=_TEST_TIMEOUT_SECONDS)
    except Exception as exc:
        if not _is_empty_reply(exc):
            _retryable, _fatal, user_message = classify_provider_error(exc)
            raise _no_answer(COMPONENT_AI, label, user_message, exc, secrets) from None
        # The provider answered, only without text. That proves the key, the
        # address and the model, which is all this check is for.
    return provider


def _checked_embedding_generator(settings: Settings) -> EmbeddingGenerator:
    provider_id = (settings.embedding.default_provider or "").strip().lower()
    label = embedding_provider_display_name(provider_id) or provider_id
    secrets = [resolve_embedding_api_key(provider_id), settings.embedding.custom_api_key]

    generator, failure = build_embedding_generator_with_reason(settings)
    if generator is None:
        failure = failure or ProviderFailure(Cause.UNKNOWN, provider_id)
        raise _not_started(COMPONENT_EMBEDDINGS, label, failure, secrets)
    try:
        generator.generate_embedding(_TEST_TEXT)
    except Exception as exc:
        raise _no_answer(COMPONENT_EMBEDDINGS, label, str(exc), exc, secrets) from None
    return generator


def _diagnose_ai_provider(
    settings: Settings, provider_id: str, key: Optional[str]
) -> ProviderFailure:
    """Say why ``build_ai_provider`` returned nothing, without building again.

    It reports no reason of its own, so the usual ones are checked here: no key, a
    missing endpoint or model, a key that cannot be sent, a missing package.
    """
    if requires_ai_api_key(provider_id) and not key:
        return ProviderFailure(Cause.MISSING_API_KEY, provider_id)

    # A local endpoint is called over HTTP: it has an address and may have a token,
    # and it needs none of the packages that run a model on this machine.
    local_endpoint = provider_id == "local" and (settings.ai.local_mode or "builtin") == "endpoint"
    missing = ""
    if provider_id == "custom":
        if not settings.ai.custom_endpoint:
            missing = "No endpoint URL is set"
        elif not (settings.ai.custom_model or settings.ai.model):
            missing = "No model name is set"
    elif provider_id == "local":
        if local_endpoint and not settings.ai.local_endpoint:
            missing = "No endpoint URL is set"
    elif provider_id not in AI_PROVIDERS:
        missing = "It is not an AI provider that PlexMix knows"
    if missing:
        return ProviderFailure(Cause.MISSING_CONFIG, provider_id, detail=missing)

    sent = [key]
    if provider_id == "custom":
        sent.append(settings.ai.custom_api_key)
    elif local_endpoint:
        sent.append(settings.ai.local_auth_token)
    for secret in sent:
        try:
            validate_api_key((secret or "").strip())
        except ValueError:
            # ``InvalidAPIKeyError``, caught by its base class: the class object this
            # module imported is no longer the one that is raised once
            # ``plexmix.config.credentials`` has been loaded again.
            return ProviderFailure(
                Cause.INVALID_CREDENTIALS,
                provider_id,
                detail="Its stored key holds characters that cannot be sent",
            )

    module, package = _AI_PACKAGES.get(provider_id, ("", ""))
    if module and not local_endpoint and not _module_available(module):
        return ProviderFailure(
            Cause.MISSING_DEPENDENCY,
            provider_id,
            package=package,
            detail=f"The {package} package is not installed",
        )
    return ProviderFailure(Cause.UNKNOWN, provider_id)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _is_empty_reply(error: BaseException) -> bool:
    """True when a built-in AI provider got an answer that held no text."""
    return isinstance(error, ValueError) and str(error).startswith(_EMPTY_REPLY_PREFIXES)


def _not_started(
    component: str, label: str, failure: ProviderFailure, secrets: Sequence[Optional[str]]
) -> RegenerateRefused:
    if failure.cause == Cause.MISSING_API_KEY:
        return RegenerateRefused(
            label, CAUSE_NO_KEY, component=component, reason=REFUSED_NO_KEY, failure=failure
        )
    detail = failure.detail
    if not detail and failure.package:
        detail = f"The {failure.package} package is not installed"
    return RegenerateRefused(
        label,
        CAUSE_NOT_STARTED,
        component=component,
        reason=REFUSED_NOT_STARTED,
        detail=_plain(detail, secrets),
        failure=failure,
    )


def _no_answer(
    component: str,
    label: str,
    text: str,
    error: BaseException,
    secrets: Sequence[Optional[str]],
) -> RegenerateRefused:
    return RegenerateRefused(
        label,
        CAUSE_NO_ANSWER,
        component=component,
        reason=REFUSED_NO_ANSWER,
        detail=_plain(text or type(error).__name__, secrets),
        error=error,
    )


def _plain(text: str, secrets: Sequence[Optional[str]]) -> str:
    """``text`` on one line, capped, without known key values or address credentials."""
    for secret in secrets:
        value = (secret or "").strip()
        if len(value) >= _MIN_SECRET_CHARS:
            text = text.replace(value, _REDACTED)
    # A key that the provider repeats with its middle hidden goes as a whole word.
    text = redact_partly_hidden_keys(text)
    text = " ".join(mask_urls_in_text(text).split())
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[: _DETAIL_MAX_CHARS - 1] + "…"
    return text


def _sentence(text: str) -> str:
    text = text.strip()
    if text and not text.endswith((".", "!", "?", "…")):
        text += "."
    return text


# ── Run marker ───────────────────────────────────────────────────────────────


def run_provider(
    settings: Settings, scope: str, ai_provider_name: Optional[str] = None
) -> RunProvider:
    """The provider and model that ``settings`` give the ``scope`` of a run.

    The marker records them, and a run is continued only while they stay the same.
    Take them from here for the preview and for the run itself, never from a built
    provider object, so that both compare the same values. ``ai_provider_name``
    selects another AI provider than the configured one, as in ``check_providers``.
    """
    _check_scope(scope)
    if scope == SCOPE_EMBEDDINGS:
        provider = (settings.embedding.default_provider or "").strip().lower()
        return RunProvider(
            provider,
            embedding_model_for(settings),
            embedding_provider_display_name(provider),
            _vector_size(settings.embedding.get_dimension_for_provider(provider)),
        )

    # The model follows ``build_ai_provider``: the configured model belongs to the
    # configured provider only, and any other provider uses its own default.
    provider = canonical_ai_provider(ai_provider_name or settings.ai.default_provider)
    if provider == "custom":
        model = settings.ai.custom_model or settings.ai.model or ""
    elif provider == canonical_ai_provider(settings.ai.default_provider) and settings.ai.model:
        model = settings.ai.model
    else:
        model = get_default_ai_model(provider)
    return RunProvider(provider, model, ai_provider_display_name(provider))


def marker_path(db: Union[SQLiteManager, Path, str]) -> Path:
    """Where the run marker of a database is: ``<database file name>.regenerate.json``.

    Takes the database manager or the path of the database file. A message names
    this file as the one to delete to start a regenerate run over.
    """
    db_path = Path(db) if isinstance(db, (str, Path)) else Path(db.db_path)
    return db_path.with_name(db_path.name + MARKER_SUFFIX)


def preview_run(
    db: SQLiteManager, scope: str, provider: RunProvider, continue_run: bool = True
) -> RegenerateRun:
    """Say what ``open_run`` would do, without writing anything.

    For the question a command asks before it starts: whether an earlier run would
    be continued (``continued``) and how many tracks are to do (``remaining``).
    """
    _check_scope(scope)
    path = marker_path(db)
    entry = _read_marker(path).get(scope) if continue_run else None
    return _plan(db, scope, provider, path, entry)


def open_run(
    db: SQLiteManager, scope: str, provider: RunProvider, continue_run: bool = True
) -> RegenerateRun:
    """Start a run for ``scope``, or continue the unfinished one.

    An earlier run is continued when the marker holds a start time for the scope,
    that run used the same provider and model (and, for embeddings, the same vector
    size), and at least one track is still to do. The marker is then left as it is.
    In every other case a new start time is written and every track is to do.
    ``continue_run=False`` always starts a new run.

    A new run can pause first, for a second as a rule and for a few at most (see
    ``_new_start``), so do not call this on an event loop.

    Raises:
        OSError: the marker could not be written. Nothing else has been changed.
    """
    _check_scope(scope)
    path = marker_path(db)
    entries = _read_marker(path)
    run = _plan(db, scope, provider, path, entries.get(scope) if continue_run else None)
    if run.continued:
        return run
    started_at, to_do = _new_start(db, scope, provider, run.total_tracks)
    entries[scope] = _MarkerEntry(
        started_at, provider.provider, provider.model, _vector_size(provider.dimension)
    )
    _write_marker(path, entries)
    return replace(run, started_at=started_at, track_ids=tuple(to_do))


def remaining_track_ids(db: SQLiteManager, run: RegenerateRun) -> List[int]:
    """The tracks of ``run`` that are still to do now, by id.

    Empty once every track has been written since the run started.
    """
    if run.started_at is None:
        return list(run.track_ids)
    return _tracks_to_do(db, run.scope, run.provider, run.started_at)


def finish_scope(db: Union[SQLiteManager, Path, str], scope: str) -> None:
    """Remove ``scope`` from the marker. The file is deleted when no scope is left.

    Call it when the scope's pass reached its end. A marker that cannot be updated
    is logged and left in place, never raised: the pass itself is done either way,
    and a later run only finds fewer tracks to do, or none and then starts a new run.
    """
    _check_scope(scope)
    path = marker_path(db)
    try:
        if not path.exists():
            return
        entries = _read_marker(path)
        entries.pop(scope, None)
        if entries:
            _write_marker(path, entries)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(
            "The regenerate marker %s could not be updated (%s).", path, type(exc).__name__
        )


@dataclass(frozen=True)
class _MarkerEntry:
    started_at: datetime
    provider: str
    model: str
    # The vector size of an embeddings run. None for tags, and for a marker that
    # was written without one.
    dimension: Optional[int] = None


def _vector_size(value: Any) -> Optional[int]:
    """``value`` as a vector size; None for anything that is not a positive whole number."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _same_identity(entry: _MarkerEntry, provider: RunProvider) -> bool:
    """Whether the run in the marker was started with what ``provider`` names now.

    The provider and the model must be the same. When ``provider`` names a vector
    size, the marker must hold the same one: after a change of the size the rows
    the earlier run wrote no longer fit, although they are newer than its start.
    A marker without a size is then another identity, so a new run starts.
    """
    if entry.provider != provider.provider or entry.model != provider.model:
        return False
    size = _vector_size(provider.dimension)
    return size is None or entry.dimension == size


def _check_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"Unknown regenerate scope: {scope!r}")


def _plan(
    db: SQLiteManager,
    scope: str,
    provider: RunProvider,
    path: Path,
    entry: Optional[_MarkerEntry],
) -> RegenerateRun:
    """The run that ``entry`` leads to: a continued one, or a new one not yet started."""
    every_track = _tracks_to_do(db, scope, provider, _EVERY_TRACK)
    if (
        entry is not None
        and _same_identity(entry, provider)
        # A start time that is still to come would never see a track as done.
        and entry.started_at <= _now()
    ):
        to_do = _tracks_to_do(db, scope, provider, entry.started_at)
        if to_do:
            return RegenerateRun(
                scope=scope,
                provider=provider,
                continued=True,
                track_ids=tuple(to_do),
                total_tracks=len(every_track),
                started_at=entry.started_at,
                marker_path=path,
            )
    return RegenerateRun(
        scope=scope,
        provider=provider,
        continued=False,
        track_ids=tuple(every_track),
        total_tracks=len(every_track),
        started_at=None,
        marker_path=path,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _wait(seconds: float) -> None:
    time.sleep(seconds)


def _tracks_to_do(
    db: SQLiteManager, scope: str, provider: RunProvider, since: datetime
) -> List[int]:
    if scope == SCOPE_TAGS:
        return db.get_track_ids_to_retag_since(since)
    return db.get_track_ids_to_reembed_since(provider.provider, since)


def _new_start(
    db: SQLiteManager, scope: str, provider: RunProvider, total_tracks: int
) -> Tuple[datetime, List[int]]:
    """Pick the start time of a new run and return it with the tracks to do.

    The times in the database are compared in whole seconds. A track that was
    written earlier in the very second the run starts would therefore pass for one
    this run already replaced, and would be left out. So when not every track is to
    do, the start moves one second on and the run waits for the clock to reach it.
    """
    started_at = _now().replace(microsecond=0)
    to_do = _tracks_to_do(db, scope, provider, started_at)
    waited = 0
    while len(to_do) < total_tracks and waited < _MAX_START_WAIT_SECONDS:
        started_at += timedelta(seconds=1)
        _wait(max(0.0, (started_at - _now()).total_seconds()))
        waited += 1
        to_do = _tracks_to_do(db, scope, provider, started_at)
    if len(to_do) < total_tracks:
        # Only a clock that was set back explains a time this far ahead.
        logger.warning(
            "%d tracks carry a time later than now and are taken for already regenerated.",
            total_tracks - len(to_do),
        )
    return started_at, to_do


def _read_marker(path: Path) -> Dict[str, _MarkerEntry]:
    """The usable entries of the marker. A missing or malformed file has none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError) as exc:
        logger.warning(
            "The regenerate marker %s could not be read and is ignored (%s).",
            path,
            type(exc).__name__,
        )
        return {}
    if not isinstance(data, dict):
        logger.warning("The regenerate marker %s is not valid and is ignored.", path)
        return {}
    entries: Dict[str, _MarkerEntry] = {}
    for scope in SCOPES:
        entry = _parse_entry(data.get(scope))
        if entry is not None:
            entries[scope] = entry
    return entries


def _parse_entry(value: Any) -> Optional[_MarkerEntry]:
    """One scope's entry, or None when it has no start time or is malformed."""
    if not isinstance(value, dict):
        return None
    started_at, provider, model = value.get("started_at"), value.get("provider"), value.get("model")
    if not (isinstance(started_at, str) and isinstance(provider, str) and isinstance(model, str)):
        return None
    try:
        moment = datetime.strptime(started_at, _MARKER_TIME_FORMAT)
    except ValueError:
        return None
    return _MarkerEntry(
        moment.replace(tzinfo=timezone.utc), provider, model, _vector_size(value.get("dimension"))
    )


def _write_marker(path: Path, entries: Dict[str, _MarkerEntry]) -> None:
    """Replace the marker in one step, so a reader never sees half a file."""
    payload: Dict[str, Dict[str, Any]] = {}
    for scope, entry in entries.items():
        payload[scope] = {
            "started_at": entry.started_at.strftime(_MARKER_TIME_FORMAT),
            "provider": entry.provider,
            "model": entry.model,
        }
        if entry.dimension is not None:
            payload[scope]["dimension"] = entry.dimension
    # A new file beside the marker, under a name of its own, then moved over it.
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        # BaseException: Ctrl+C must not leave the temporary file behind either.
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
