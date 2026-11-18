"""Shared readiness model for the Web UI: one honest answer to "does it work?".

Every status surface (Dashboard strip, Doctor connection rows and verdict,
Generator readiness line, Doctor rail badge, first-run checklist) reads the
result of :func:`evaluate`, so no two surfaces can disagree about a component.

This module is deliberately free of Reflex and of ``plexmix.ui`` imports: the
service layer must not depend on the UI layer, and keeping it pure makes every
rule unit-testable.

Components (``COMPONENTS``): ``plex``, ``ai``, ``embeddings``, ``library``,
``index``, ``audio``, ``musicbrainz``.

Levels (:class:`Level`): ``not_set_up``, ``unchecked``, ``checking``, ``ready``,
``failing``, ``off``, ``not_installed``. A component is only ``ready`` after a
real check or a real successful run for the *current* configuration: results
are stored in the process-level :data:`readiness_store` with a fingerprint of
the configuration that produced them, and a record whose fingerprint differs
from the current one is ignored. The store lives in memory only, so a restart
shows everything as "not checked".

Secrets: fingerprints contain only a truncated hash of a hash of a secret. They
are never placed in :meth:`Facts.as_dict` or :meth:`ComponentStatus.as_dict`
(the dicts that reach client state) and never logged.

All user-facing copy lives in :func:`describe` (causes),
:func:`describe_job_scope` (job confirmations), :func:`doctor_rows` /
:func:`verdict` (Doctor) and :func:`setup_steps` (first-run checklist). Copy uses
provider display names, never CLI commands and never emoji.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import math
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from plexmix.config.constants import PLEX_READINESS_TIMEOUT_S, PLEX_READINESS_TTL_S
from plexmix.services.registry import (
    AI_PROVIDERS,
    ai_provider_display_name,
    embedding_provider_display_name,
    get_default_ai_model,
    get_default_embedding_model,
    requires_ai_api_key,
    requires_embedding_api_key,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from plexmix.config.settings import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and constants
# ---------------------------------------------------------------------------


class Level(str, Enum):
    """Readiness state of one component."""

    NOT_SET_UP = "not_set_up"
    UNCHECKED = "unchecked"
    CHECKING = "checking"
    READY = "ready"
    FAILING = "failing"
    OFF = "off"
    NOT_INSTALLED = "not_installed"

    def __str__(self) -> str:
        return str(self.value)


class Cause(str, Enum):
    """Why a component is not set up or failing."""

    MISSING_CONFIG = "missing_config"
    MISSING_API_KEY = "missing_api_key"
    MISSING_DEPENDENCY = "missing_dependency"
    MODEL_LOAD_FAILED = "model_load_failed"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    INVALID_CREDENTIALS = "invalid_credentials"
    LIBRARY_NOT_FOUND = "library_not_found"
    NOT_SYNCED = "not_synced"
    INDEX_EMPTY = "index_empty"
    DIMENSION_MISMATCH = "dimension_mismatch"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)


COMPONENTS: tuple[str, ...] = (
    "plex",
    "ai",
    "embeddings",
    "library",
    "index",
    "audio",
    "musicbrainz",
)
"""Every readiness component, in display order."""

REQUIRED_COMPONENTS: tuple[str, ...] = ("plex", "embeddings", "library", "index")
"""Components whose trouble shows the Doctor rail badge (AI is optional)."""

GENERATION_COMPONENTS: tuple[str, ...] = ("embeddings", "library", "index")
"""Components whose blocking causes disable Generate."""

BLOCKING_CAUSES: frozenset[Cause] = frozenset(
    {
        Cause.MISSING_CONFIG,
        Cause.MISSING_API_KEY,
        Cause.MISSING_DEPENDENCY,
        Cause.NOT_SYNCED,
        Cause.INDEX_EMPTY,
        Cause.DIMENSION_MISMATCH,
    }
)
"""Causes re-derived from local facts on every evaluation. Only these gate
actions; a network-derived failure (timeout, rejected key from an earlier run)
is shown but never disables anything, so a stale record can't lock anyone out."""

# Settings deep links. The ids are the ``?section=`` contract owned by
# webui-keyboard-and-semantics (``SETTINGS_SECTIONS`` in ui/utils/a11y.py:
# plex|ai|embedding|audio|musicbrainz|advanced|appearance). Hardcoded here
# because services must not import the UI layer.
SETTINGS_SECTION_HREFS: Dict[str, str] = {
    "plex": "/settings?section=plex",
    "ai": "/settings?section=ai",
    "embeddings": "/settings?section=embedding",
    "audio": "/settings?section=audio",
    "musicbrainz": "/settings?section=musicbrainz",
}
DASHBOARD_HREF = "/dashboard"
LIBRARY_HREF = "/library"
DOCTOR_HREF = "/doctor"
GENERATOR_HREF = "/generator"
TAGGING_HREF = "/tagging"

LEVEL_WORDS: Dict[Level, str] = {
    Level.NOT_SET_UP: "Not set up",
    Level.UNCHECKED: "Set up · not checked",
    Level.CHECKING: "Checking…",
    Level.READY: "Ready",
    Level.FAILING: "Failing",
    Level.OFF: "Off",
    Level.NOT_INSTALLED: "Not installed",
}
"""State words shown next to a component."""

LEVEL_TONES: Dict[Level, str] = {
    Level.NOT_SET_UP: "warning",
    Level.UNCHECKED: "idle",
    Level.CHECKING: "idle",
    Level.READY: "success",
    Level.FAILING: "error",
    Level.OFF: "idle",
    Level.NOT_INSTALLED: "idle",
}
"""Kit tone per level (``.dot-<tone>``): only ``ready`` is ``success``."""

COMPONENT_LABELS: Dict[str, str] = {
    "plex": "Plex",
    "ai": "AI provider",
    "embeddings": "Embedding provider",
    "library": "Library",
    "index": "Search index",
    "audio": "Audio analysis",
    "musicbrainz": "MusicBrainz",
}

LOCAL_REMEDY = "Use the :latest-local Docker image or install PlexMix with the plexmix[local] extra"
AUDIO_REMEDY = (
    "Install PlexMix with the plexmix[audio] extra; the standard Docker image includes it "
    "on x86-64 machines"
)
BATCH_DURATION_LINE = (
    "Runs in batches · progress and time remaining show once it starts · you can cancel"
)

_LOCAL_PACKAGES = frozenset({"sentence-transformers", "transformers", "torch"})
_IN_FLIGHT_MAX_S = 120.0
_DETAIL_MAX_CHARS = 200


def _str_enum(value: Union[Level, Cause, str, None], enum_cls: Any) -> Any:
    if value is None or value == "":
        return None
    return enum_cls(value)


def _normalize_component(component: str) -> str:
    key = (component or "").strip().lower()
    return "embeddings" if key == "embedding" else key


# ---------------------------------------------------------------------------
# Check records and the process-level store
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckRecord:
    """The outcome of one real check or real use of a component.

    ``checked_at`` is wall-clock epoch seconds. ``message`` is optional plain
    text (already secret-free) from the check. ``fingerprint`` identifies the
    configuration that was checked; it is excluded from ``repr``.
    """

    level: Level
    cause: Optional[Cause]
    message: str
    checked_at: float
    fingerprint: str = field(default="", repr=False)


class ReadinessStore:
    """Thread-safe, in-memory store of check records keyed by component.

    One instance per process (:data:`readiness_store`), like ``task_store``: a
    sync started in one tab updates readiness for every tab.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, CheckRecord] = {}
        self._in_flight: Dict[str, float] = {}

    def record(
        self,
        component: str,
        level: Union[Level, str],
        cause: Union[Cause, str, None] = None,
        message: str = "",
        fingerprint: str = "",
        checked_at: Optional[float] = None,
    ) -> CheckRecord:
        """Store the outcome of a real check or real use and return it.

        ``level`` is normally ``ready`` or ``failing``; ``checking`` is not a
        record (use :meth:`begin_check`). ``message`` must not contain secrets.
        """
        lvl = Level(level)
        if lvl == Level.CHECKING:
            raise ValueError("Use begin_check()/end_check() for an in-flight check")
        typed_cause = _str_enum(cause, Cause)
        if lvl == Level.FAILING and typed_cause is None:
            typed_cause = Cause.UNKNOWN
        rec = CheckRecord(
            level=lvl,
            cause=typed_cause,
            message=message or "",
            checked_at=time.time() if checked_at is None else float(checked_at),
            fingerprint=fingerprint or "",
        )
        with self._lock:
            self._records[_normalize_component(component)] = rec
        return rec

    def get(self, component: str) -> Optional[CheckRecord]:
        """Return the latest record for ``component`` (any fingerprint)."""
        with self._lock:
            return self._records.get(_normalize_component(component))

    def get_current(self, component: str, fingerprint: str) -> Optional[CheckRecord]:
        """Return the latest record only if it was made for ``fingerprint``."""
        rec = self.get(component)
        if rec is None or rec.fingerprint != fingerprint:
            return None
        return rec

    def needs_check(
        self,
        component: str,
        fingerprint: str,
        now: Optional[float] = None,
        ttl_s: float = PLEX_READINESS_TTL_S,
    ) -> bool:
        """True when there is no record for ``fingerprint`` or it is older than ``ttl_s``."""
        rec = self.get_current(component, fingerprint)
        if rec is None:
            return True
        current = time.time() if now is None else now
        return (current - rec.checked_at) >= ttl_s

    def begin_check(self, component: str, now: Optional[float] = None) -> bool:
        """Claim the right to run a check. Returns False if one is already in flight.

        A claim older than two minutes is treated as abandoned. Always pair a
        successful claim with :meth:`end_check` (``try/finally``).
        """
        key = _normalize_component(component)
        current = time.time() if now is None else now
        with self._lock:
            started = self._in_flight.get(key)
            if started is not None and (current - started) < _IN_FLIGHT_MAX_S:
                return False
            self._in_flight[key] = current
            return True

    def end_check(self, component: str) -> None:
        """Release a claim taken with :meth:`begin_check`."""
        with self._lock:
            self._in_flight.pop(_normalize_component(component), None)

    def is_checking(self, component: str, now: Optional[float] = None) -> bool:
        """True while a non-abandoned check for ``component`` is in flight."""
        current = time.time() if now is None else now
        with self._lock:
            started = self._in_flight.get(_normalize_component(component))
        return started is not None and (current - started) < _IN_FLIGHT_MAX_S

    def discard(self, component: str) -> None:
        """Forget the record for one component."""
        with self._lock:
            self._records.pop(_normalize_component(component), None)

    def clear(self) -> None:
        """Forget every record and in-flight claim (tests, and nothing else)."""
        with self._lock:
            self._records.clear()
            self._in_flight.clear()


readiness_store = ReadinessStore()
"""The process-level store every surface reads (reset with ``readiness_store.clear()``)."""


# ---------------------------------------------------------------------------
# Configuration fingerprints (server-only)
# ---------------------------------------------------------------------------


def _canonical_ai_provider(provider: Optional[str]) -> str:
    """Same normalisation as ``providers.canonical_ai_provider`` (no heavy import)."""
    name = (provider or "gemini").strip().lower() or "gemini"
    return "claude" if name == "anthropic" else name


def ai_model_for(settings: "Settings") -> str:
    """The AI model the configured provider will use (display and fingerprints)."""
    provider = _canonical_ai_provider(settings.ai.default_provider)
    if provider == "custom":
        return settings.ai.custom_model or settings.ai.model or ""
    if settings.ai.model:
        return settings.ai.model
    try:
        return get_default_ai_model(provider)
    except Exception:  # pragma: no cover - registry lookups don't normally fail
        return ""


def embedding_model_for(settings: "Settings") -> str:
    """The embedding model the configured provider will use."""
    provider = (settings.embedding.default_provider or "").strip().lower()
    if provider == "custom":
        return settings.embedding.custom_model or settings.embedding.model or ""
    return settings.embedding.model or get_default_embedding_model(provider)


def fingerprint_values(
    component: str,
    *,
    provider: str = "",
    model: str = "",
    url: str = "",
    library: str = "",
    endpoint: str = "",
    secret: Optional[str] = None,
) -> str:
    """Fingerprint a component configuration from explicit (e.g. form) values.

    sha256 over ``component|provider|model|url|library|endpoint|sha256(secret)[:16]``.
    Values are normalised (trimmed, provider lower-cased with ``anthropic`` →
    ``claude``, trailing ``/`` dropped from URLs) so the same configuration typed
    in a Settings form and loaded from config gives the same fingerprint. The
    result never contains the secret.
    """
    comp = _normalize_component(component)
    prov = (provider or "").strip().lower()
    if comp == "ai" and prov:
        prov = _canonical_ai_provider(prov)
    secret_hash = (
        hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()[:16]
        if secret and secret.strip()
        else ""
    )
    payload = "|".join(
        [
            comp,
            prov,
            (model or "").strip(),
            (url or "").strip().rstrip("/"),
            (library or "").strip(),
            (endpoint or "").strip().rstrip("/"),
            secret_hash,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_fields(component: str, settings: "Settings") -> Dict[str, str]:
    """The non-secret fields :func:`fingerprint` hashes for ``component``.

    Exposed so a caller fingerprinting form values (Settings tests) can use the
    same field mapping: ``plex`` → url + library; ``ai`` → provider + effective
    model + endpoint (Custom endpoint, or ``<local_mode>:<local_endpoint>`` for
    Local); ``embeddings`` → provider + effective model + endpoint (Custom
    ``<endpoint>#<dimension>``); anything else → the component id as provider.
    """
    comp = _normalize_component(component)
    if comp == "plex":
        return {
            "provider": "plex",
            "model": "",
            "url": settings.plex.url or "",
            "library": settings.plex.library_name or "",
            "endpoint": "",
        }
    if comp == "ai":
        provider = _canonical_ai_provider(settings.ai.default_provider)
        if provider == "custom":
            endpoint = settings.ai.custom_endpoint or ""
        elif provider == "local":
            mode = settings.ai.local_mode or "builtin"
            endpoint = f"{mode}:{settings.ai.local_endpoint or ''}" if mode == "endpoint" else mode
        else:
            endpoint = ""
        return {
            "provider": provider,
            "model": ai_model_for(settings),
            "url": "",
            "library": "",
            "endpoint": endpoint,
        }
    if comp == "embeddings":
        provider = (settings.embedding.default_provider or "").strip().lower()
        endpoint = ""
        if provider == "custom":
            endpoint = (
                f"{settings.embedding.custom_endpoint or ''}#{settings.embedding.custom_dimension}"
            )
        return {
            "provider": provider,
            "model": embedding_model_for(settings),
            "url": "",
            "library": "",
            "endpoint": endpoint,
        }
    return {"provider": comp, "model": "", "url": "", "library": "", "endpoint": ""}


def fingerprint(component: str, settings: "Settings", secret: Optional[str]) -> str:
    """Fingerprint the current configuration of ``component`` (see :func:`fingerprint_values`)."""
    return fingerprint_values(component, secret=secret, **fingerprint_fields(component, settings))


def resolve_secret(component: str, settings: "Settings") -> Optional[str]:
    """The secret that belongs in ``component``'s fingerprint (server-side only).

    Plex: the stored token (credential store, then config). AI: the selected
    provider's key (Custom: config key then store; Local endpoint: its auth
    token). Embeddings: the selected provider's key (Custom: config then store).
    Never log or return the value to a client.
    """
    from plexmix.services import providers

    comp = _normalize_component(component)
    try:
        if comp == "plex":
            from plexmix.config import credentials

            return credentials.get_plex_token() or settings.plex.token
        if comp == "ai":
            provider = _canonical_ai_provider(settings.ai.default_provider)
            if provider == "custom":
                return settings.ai.custom_api_key or providers.resolve_ai_api_key("custom")
            if provider == "local":
                return settings.ai.local_auth_token
            return providers.resolve_ai_api_key(provider)
        if comp == "embeddings":
            provider = (settings.embedding.default_provider or "").strip().lower()
            if provider == "custom":
                return settings.embedding.custom_api_key or providers.resolve_embedding_api_key(
                    "custom"
                )
            return providers.resolve_embedding_api_key(provider)
    except Exception as exc:  # keyring backends can raise; never log the value
        logger.warning("Could not read the %s credential: %s", comp, type(exc).__name__)
    return None


def current_fingerprint(component: str, settings: "Settings") -> str:
    """Fingerprint ``component`` with its resolved secret."""
    return fingerprint(component, settings, resolve_secret(component, settings))


# ---------------------------------------------------------------------------
# Facts (cheap, local, free)
# ---------------------------------------------------------------------------


@dataclass
class Facts:
    """Locally gathered facts the evaluator reads. Gathering them costs nothing
    and never touches the network.

    ``fingerprints`` holds the current configuration fingerprints per
    component; it is server-only and excluded from :meth:`as_dict` and ``repr``.
    """

    db_exists: bool = False
    db_error: bool = False
    total_tracks: int = 0
    untagged_tracks: int = 0
    embedded_tracks: int = 0
    audio_tracks: int = 0
    musicbrainz_tracks: int = 0
    last_sync: Optional[str] = None
    index_exists: bool = False
    index_dimension: int = 0
    index_vector_count: int = 0
    expected_dimension: int = 0
    local_deps_available: bool = False
    essentia_available: bool = False
    plex_token_present: bool = False
    ai_key_present: bool = False
    embedding_key_present: bool = False
    audio_enabled: bool = False
    musicbrainz_enabled: bool = False
    fingerprints: Dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def tagged_tracks(self) -> int:
        """Tracks with tags (total minus ``count_untagged_tracks``)."""
        return max(self.total_tracks - self.untagged_tracks, 0)

    @property
    def missing_embeddings(self) -> int:
        """Tracks without an embedding."""
        return max(self.total_tracks - self.embedded_tracks, 0)

    def as_dict(self) -> Dict[str, Any]:
        """Plain values for UI state (no fingerprints, no secrets)."""
        return {
            "db_exists": self.db_exists,
            "db_error": self.db_error,
            "total_tracks": self.total_tracks,
            "untagged_tracks": self.untagged_tracks,
            "tagged_tracks": self.tagged_tracks,
            "embedded_tracks": self.embedded_tracks,
            "missing_embeddings": self.missing_embeddings,
            "audio_tracks": self.audio_tracks,
            "musicbrainz_tracks": self.musicbrainz_tracks,
            "last_sync": self.last_sync,
            "index_exists": self.index_exists,
            "index_dimension": self.index_dimension,
            "index_vector_count": self.index_vector_count,
            "expected_dimension": self.expected_dimension,
            "local_deps_available": self.local_deps_available,
            "essentia_available": self.essentia_available,
            "plex_token_present": self.plex_token_present,
            "ai_key_present": self.ai_key_present,
            "embedding_key_present": self.embedding_key_present,
            "audio_enabled": self.audio_enabled,
            "musicbrainz_enabled": self.musicbrainz_enabled,
        }


def _module_available(name: str) -> bool:
    """``importlib.util.find_spec`` without importing the package."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def read_index_metadata(settings: "Settings") -> Dict[str, int]:
    """Read the FAISS ``.metadata`` pickle next to the index (no FAISS import).

    Returns ``{"exists": 0|1, "dimension": int, "vector_count": int}``;
    ``dimension`` is 0 when there is no (readable) metadata file. This is the
    read ``AppState.load_library_stats`` used for its dimension-mismatch check.
    """
    import pickle

    result = {"exists": 0, "dimension": 0, "vector_count": 0}
    try:
        metadata_path = settings.database.get_index_path().with_suffix(".metadata")
        if not metadata_path.exists():
            return result
        result["exists"] = 1
        with open(metadata_path, "rb") as f:
            metadata = pickle.load(f)
        if isinstance(metadata, dict):
            result["dimension"] = int(metadata.get("dimension", 0) or 0)
            track_ids = metadata.get("track_ids") or []
            result["vector_count"] = len(track_ids) if hasattr(track_ids, "__len__") else 0
    except Exception as exc:
        logger.warning("Could not read the search index metadata: %s", type(exc).__name__)
    return result


def collect_facts(settings: "Settings") -> Facts:
    """Gather every local fact :func:`evaluate` needs. Never creates the DB.

    Counts come from ``SQLiteManager`` only when the database file exists
    (``count_untagged_tracks`` defines "untagged" for every surface). Blocking
    I/O: call it from ``run_in_executor`` in async handlers.
    """
    facts = Facts()
    db_path = settings.database.get_db_path()
    facts.db_exists = db_path.exists()
    if facts.db_exists:
        try:
            from plexmix.database.sqlite_manager import SQLiteManager

            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()
                cursor.execute("SELECT COUNT(*) FROM tracks")
                row = cursor.fetchone()
                facts.total_tracks = int(row[0]) if row else 0
                facts.untagged_tracks = int(db.count_untagged_tracks())
                facts.embedded_tracks = int(db.count_tracks_with_embeddings())
                facts.audio_tracks = int(db.get_audio_features_count())
                facts.musicbrainz_tracks = int(db.get_musicbrainz_enrichment_count())
                facts.last_sync = db.get_last_sync_time()
        except Exception as exc:
            facts.db_error = True
            facts.total_tracks = facts.untagged_tracks = facts.embedded_tracks = 0
            facts.audio_tracks = facts.musicbrainz_tracks = 0
            logger.warning("Could not read library stats: %s: %s", type(exc).__name__, exc)

    meta = read_index_metadata(settings)
    # The FAISS file itself: VectorIndex starts empty without it (and deletes a
    # corrupt one), whatever the metadata next to it says.
    try:
        facts.index_exists = settings.database.get_index_path().exists()
    except Exception as exc:
        logger.warning("Could not check the search index file: %s", type(exc).__name__)
    facts.index_dimension = meta["dimension"]
    facts.index_vector_count = meta["vector_count"]
    try:
        facts.expected_dimension = int(
            settings.embedding.get_dimension_for_provider(settings.embedding.default_provider)
        )
    except Exception as exc:
        logger.warning("Could not resolve the embedding dimension: %s", type(exc).__name__)

    facts.local_deps_available = _module_available("sentence_transformers")
    facts.essentia_available = _module_available("essentia")

    # Key presence for the selected providers only: each lookup can be a slow
    # keyring call, and this runs on every page load.
    plex_secret = resolve_secret("plex", settings)
    facts.plex_token_present = bool(plex_secret)

    ai_provider = _canonical_ai_provider(settings.ai.default_provider)
    ai_secret = resolve_secret("ai", settings)
    facts.ai_key_present = bool(ai_secret) if requires_ai_api_key(ai_provider) else True

    emb_provider = (settings.embedding.default_provider or "").strip().lower()
    emb_secret = resolve_secret("embeddings", settings)
    facts.embedding_key_present = (
        bool(emb_secret) if requires_embedding_api_key(emb_provider) else True
    )

    facts.audio_enabled = bool(settings.audio.enabled)
    facts.musicbrainz_enabled = bool(settings.musicbrainz.enabled)
    facts.fingerprints = {
        "plex": fingerprint("plex", settings, plex_secret),
        "ai": fingerprint("ai", settings, ai_secret),
        "embeddings": fingerprint("embeddings", settings, emb_secret),
        "musicbrainz": fingerprint("musicbrainz", settings, None),
    }
    return facts


# ---------------------------------------------------------------------------
# Copy helpers
# ---------------------------------------------------------------------------


def _n(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _count(n: int) -> str:
    return f"{n:,}"


def _plural(n: int, singular: str, plural: Optional[str] = None) -> str:
    word = singular if n == 1 else (plural or f"{singular}s")
    return f"{n:,} {word}"


def format_duration(seconds: float) -> str:
    """Duration as "1 h 37 min" / "12 min" / "less than a minute" (rounded up)."""
    if seconds < 60:
        return "less than a minute"
    total_min = int(math.ceil(seconds / 60.0))
    hours, minutes = divmod(total_min, 60)
    if hours == 0:
        return f"{minutes} min"
    if minutes == 0:
        return f"{hours} h"
    return f"{hours} h {minutes} min"


# Sentences dropped from raw error text: CLI instructions and the generic
# "check your API keys" advice that hid the real cause.
_CLI_HINT_RE = re.compile(
    r"(run:|pip install|poetry |plexmix config|plexmix [a-z]+ |check your api key)", re.I
)
_EMOJI_RE = re.compile("[☀-➿️\U0001f000-\U0001faff]")


def _clean_detail(value: Any) -> str:
    """Make raw error text safe for UI copy: no CLI hints, no emoji, short."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = _EMOJI_RE.sub("", text).strip()
    kept = [s for s in re.split(r"(?<=[.!?])\s+", text) if s and not _CLI_HINT_RE.search(s)]
    text = " ".join(kept).strip()
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[: _DETAIL_MAX_CHARS - 1].rstrip() + "…"
    return text


def _is_local(ctx: Mapping[str, Any]) -> bool:
    provider = str(ctx.get("provider") or "").lower()
    package = str(ctx.get("package") or "").lower()
    return provider == "local" or ctx.get("label") == "Local" or package in _LOCAL_PACKAGES


def describe(
    component: str,
    cause: Union[Cause, str],
    context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """The single source of user-facing readiness copy.

    Returns ``{"title", "body", "action_label", "action_href", "secondary_label",
    "secondary_href"}`` (all strings, ``""`` when absent). Settings actions use
    ``/settings?section=<plex|ai|embedding|audio|musicbrainz>``.

    ``context`` keys (all optional): ``label`` (provider display name),
    ``provider`` (provider id), ``model``, ``url`` (Plex), ``library`` (Plex),
    ``code`` (``PlexConnectionError.code``), ``package`` (missing package),
    ``detail`` (secret-free error text; cleaned of CLI hints before use),
    ``index_dimension``, ``expected_dimension``, ``total_tracks``, ``timeout``,
    ``index_missing`` (index_empty: tracks have embeddings but the index file is
    missing or holds no vectors).
    """
    comp = _normalize_component(component)
    c = Cause(cause)
    ctx: Mapping[str, Any] = context or {}
    label = str(ctx.get("label") or "").strip()
    model = str(ctx.get("model") or "").strip()
    url = str(ctx.get("url") or "").strip()
    library = str(ctx.get("library") or "").strip()
    package = str(ctx.get("package") or "").strip()
    detail = _clean_detail(ctx.get("detail"))
    section = SETTINGS_SECTION_HREFS.get(comp, "")
    provider_comp = comp in ("ai", "embeddings", "index")
    who = label or {
        "plex": "Plex",
        "ai": "the AI provider",
        "embeddings": "the embedding provider",
        "index": "the embedding provider",
    }.get(comp, COMPONENT_LABELS.get(comp, "PlexMix"))
    who_cap = who[:1].upper() + who[1:]
    # The Settings section that fixes this component ("index" → Embeddings).
    href = section or (SETTINGS_SECTION_HREFS["embeddings"] if comp == "index" else "")
    model_part = f" · {model}" if model else ""
    your_key = f"your {label} API key" if label else "the API key"

    out = {
        "title": "",
        "body": "",
        "action_label": "",
        "action_href": "",
        "secondary_label": "",
        "secondary_href": "",
    }

    def _set(title: str, body: str, action_label: str = "", action_href: str = "") -> None:
        out["title"] = title
        out["body"] = body
        out["action_label"] = action_label if action_href else ""
        out["action_href"] = action_href

    if c == Cause.MISSING_CONFIG:
        if comp == "plex":
            if ctx.get("code") == "no_library":
                _set(
                    "No music library chosen",
                    "Choose which Plex music library PlexMix should use in Settings.",
                    "Choose library",
                    href,
                )
            else:
                _set(
                    "Plex isn't connected yet",
                    "Add your Plex server address, token and music library in Settings so "
                    "PlexMix can read your music.",
                    "Connect Plex",
                    href,
                )
        elif comp == "ai":
            _set(
                f"{who_cap} isn't set up yet",
                f"Finish setting up {who} in Settings (it needs an endpoint URL and a model), "
                "or choose another AI provider. An AI provider is optional; it adds tags that "
                "improve results.",
                "Open AI settings",
                href,
            )
        elif comp == "embeddings":
            _set(
                f"{who_cap} isn't set up yet",
                f"Finish setting up {who} in Settings (it needs an endpoint URL and a model), "
                "or choose another embedding provider.",
                "Open embedding settings",
                href,
            )
        else:
            _set(
                f"{who_cap} isn't set up yet",
                "Finish its setup in Settings.",
                "Open settings",
                href,
            )
    elif c == Cause.MISSING_API_KEY:
        if comp == "ai":
            _set(
                f"No API key for {who}",
                f"Add {your_key} in Settings, or pick a different AI provider. An AI "
                "provider is optional; it adds tags that improve results.",
                "Add API key",
                href,
            )
        elif provider_comp:
            _set(
                f"No API key for {who}",
                f"Search needs an embedding provider. Add {your_key} in Settings, or "
                "switch to another provider.",
                "Add API key",
                SETTINGS_SECTION_HREFS["embeddings"],
            )
        else:
            _set(
                f"No API key for {who}",
                "Add the API key in Settings.",
                "Add API key",
                href,
            )
    elif c == Cause.MISSING_DEPENDENCY:
        if comp == "audio":
            _set(
                "Audio analysis isn't installed",
                "Audio analysis needs the Essentia package, which this install doesn't "
                f"include. {AUDIO_REMEDY}.",
            )
        elif provider_comp and _is_local(ctx):
            missing = f" (the {package} package is missing)" if package else ""
            _set(
                "Local models aren't installed",
                f"This install doesn't include local models{missing}. {LOCAL_REMEDY}, or "
                "switch to a cloud provider in Settings.",
                "Switch provider",
                href,
            )
        else:
            pkg = f"the {package} package" if package else "a required package"
            if provider_comp:
                _set(
                    f"{who_cap} needs a package that isn't installed",
                    f"This install is missing {pkg}. Reinstall or update PlexMix, or switch to "
                    "another provider in Settings.",
                    "Switch provider",
                    href,
                )
            else:
                _set(
                    f"{who_cap} needs a package that isn't installed",
                    f"This install is missing {pkg}. Reinstall or update PlexMix.",
                )
    elif c == Cause.MODEL_LOAD_FAILED:
        name = model or "The model"
        _set(
            "The local model couldn't load" if _is_local(ctx) else f"{who_cap} couldn't load",
            f"{name} failed to load on this machine. Try a smaller model or switch provider "
            "in Settings. Details are in the server log.",
            "Change model",
            href,
        )
    elif c == Cause.UNREACHABLE:
        if comp == "plex":
            _set(
                f"Can't reach your Plex server at {url}" if url else "Can't reach your Plex server",
                "Check that Plex is running and that the server address in Settings is right.",
                "Fix connection",
                href,
            )
        elif comp == "musicbrainz":
            _set(
                "Can't reach MusicBrainz",
                "MusicBrainz didn't answer. Check your network connection and try again later.",
            )
        else:
            _set(
                f"Can't reach {who}",
                f"{who_cap} didn't answer. Check your network connection, or the endpoint URL "
                "in Settings.",
                "Open settings",
                href,
            )
    elif c == Cause.TIMEOUT:
        if comp == "plex":
            seconds = _n(ctx.get("timeout")) or PLEX_READINESS_TIMEOUT_S
            where = f" at {url}" if url else ""
            _set(
                "Your Plex server didn't answer in time",
                f"Plex{where} didn't respond within {seconds} seconds. It may be busy or asleep; "
                "PlexMix checks again in a few minutes.",
                "Check connection settings",
                href,
            )
        else:
            _set(
                f"{who_cap} took too long to answer",
                "The request timed out. This is often temporary; try again, or check the "
                "endpoint in Settings.",
                "Open settings",
                href,
            )
    elif c == Cause.INVALID_CREDENTIALS:
        if comp == "plex":
            _set(
                "Plex rejected the saved token",
                "Your Plex server refused PlexMix's sign-in token. Paste a new token in Settings.",
                "Update token",
                href,
            )
        else:
            _set(
                f"{who_cap} rejected the API key",
                f"The saved {who} API key isn't valid. Enter a new key in Settings.",
                "Update API key",
                href,
            )
    elif c == Cause.LIBRARY_NOT_FOUND:
        name = f'named "{library}"' if library else "with that name"
        _set(
            "Music library not found",
            f"Plex has no music library {name}. It may have been renamed or removed; choose a "
            "library in Settings.",
            "Choose library",
            SETTINGS_SECTION_HREFS["plex"],
        )
    elif c == Cause.NOT_SYNCED:
        _set(
            "Your library hasn't been synced yet",
            "Sync your Plex music library so PlexMix has tracks to search. The setup checklist "
            "on the Dashboard walks you through it.",
            "Finish setup",
            DASHBOARD_HREF,
        )
    elif c == Cause.INDEX_EMPTY:
        total = _n(ctx.get("total_tracks"))
        tracks = f"your {_plural(total, 'track')}" if total else "your tracks"
        if ctx.get("index_missing"):
            _set(
                "The search index needs rebuilding",
                "Your tracks have embeddings, but the search index built from them is missing "
                "or empty, so there's nothing to search. Build the search index to recreate it "
                "from the embeddings you already have.",
                "Build search index",
                DASHBOARD_HREF,
            )
        else:
            _set(
                "The search index hasn't been built yet",
                f"None of {tracks} have embeddings, so there's nothing to search. Build the "
                "search index to create them.",
                "Build search index",
                DASHBOARD_HREF,
            )
    elif c == Cause.DIMENSION_MISMATCH:
        have = _n(ctx.get("index_dimension"))
        want = _n(ctx.get("expected_dimension"))
        have_txt = f"{_count(have)}-dimension" if have else "differently sized"
        want_txt = f"{_count(want)}-dimension" if want else "differently sized"
        _set(
            "The search index doesn't match your embedding provider",
            f"Your search index holds {have_txt} embeddings, but {who}{model_part} makes "
            f"{want_txt} ones. Rebuild the embeddings, or switch back to the provider that "
            "built the index.",
            "Rebuild embeddings",
            DOCTOR_HREF,
        )
        out["secondary_label"] = "Switch back"
        out["secondary_href"] = SETTINGS_SECTION_HREFS["embeddings"]
    else:  # Cause.UNKNOWN
        if comp == "library":
            title = "The library database couldn't be read"
        elif comp == "plex":
            title = "The Plex check failed"
        elif comp in ("ai", "embeddings"):
            title = f"{who_cap} couldn't start"
        else:
            title = "Something went wrong"
        _set(
            title,
            detail or "An unexpected error occurred. Details are in the server log.",
            "Open settings" if href else "",
            href,
        )
    return out


def describe_failure(
    component: str, failure: Any, context: Optional[Mapping[str, Any]] = None
) -> Dict[str, str]:
    """:func:`describe` for a ``providers.ProviderFailure`` (cause/provider/package/detail)."""
    ctx: Dict[str, Any] = dict(context or {})
    provider = str(getattr(failure, "provider", "") or "")
    ctx.setdefault("provider", provider)
    if "label" not in ctx and provider:
        comp = _normalize_component(component)
        ctx["label"] = (
            ai_provider_display_name(provider)
            if comp == "ai"
            else embedding_provider_display_name(provider)
        )
    if getattr(failure, "package", ""):
        ctx.setdefault("package", failure.package)
    if getattr(failure, "detail", ""):
        ctx.setdefault("detail", failure.detail)
    return describe(component, getattr(failure, "cause", Cause.UNKNOWN), ctx)


def cause_for_plex_error_code(code: Optional[str]) -> Cause:
    """Map ``PlexConnectionError.code`` to a readiness cause."""
    return {
        "not_configured": Cause.MISSING_CONFIG,
        "no_library": Cause.MISSING_CONFIG,
        "unreachable": Cause.UNREACHABLE,
        "library_missing": Cause.LIBRARY_NOT_FOUND,
    }.get(code or "", Cause.UNKNOWN)


def is_blocking(cause: Union[Cause, str, None]) -> bool:
    """True for causes that gate actions (see :data:`BLOCKING_CAUSES`)."""
    if cause is None or cause == "":
        return False
    try:
        return Cause(cause) in BLOCKING_CAUSES
    except ValueError:
        return False


def is_cloud_provider(provider: Optional[str]) -> bool:
    """True when a provider sends data off this machine (everything but Local).

    Provider id only: a Local endpoint or a Custom endpoint can point anywhere,
    so job confirmations use :func:`data_destination` instead.
    """
    return bool(provider) and (provider or "").strip().lower() != "local"


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def endpoint_host(url: Optional[str]) -> str:
    """The host of an endpoint URL, lower-case ("" when absent or unparseable).

    Only the host: never a scheme, port, path or user information.
    """
    from urllib.parse import urlparse

    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    try:
        return (urlparse(text).hostname or "").strip().lower()
    except ValueError:
        return ""


def _endpoint_destination(url: Optional[str], fallback: str) -> str:
    host = endpoint_host(url)
    if not host:
        return fallback
    if host in _LOOPBACK_HOSTS or host.startswith("127."):
        return ""
    return host


def ai_sends_to(settings: "Settings") -> str:
    """Where tagging sends track metadata: "" when it stays on this machine.

    A cloud provider → its display name; Local built-in → ""; Local endpoint
    mode and Custom → the endpoint's host ("" for a loopback address).
    """
    provider = _canonical_ai_provider(settings.ai.default_provider)
    label = ai_provider_display_name(provider)
    if provider == "local":
        if (settings.ai.local_mode or "builtin") == "endpoint":
            return _endpoint_destination(settings.ai.local_endpoint, label)
        return ""
    if provider == "custom":
        return _endpoint_destination(settings.ai.custom_endpoint, label)
    return label


def embedding_sends_to(settings: "Settings") -> str:
    """Where embedding sends track metadata: "" when it stays on this machine.

    A cloud provider → its display name; Local → ""; Custom → the endpoint's
    host ("" for a loopback address).
    """
    provider = (settings.embedding.default_provider or "").strip().lower()
    label = embedding_provider_display_name(provider)
    if provider == "local":
        return ""
    if provider == "custom":
        return _endpoint_destination(settings.embedding.custom_endpoint, label)
    return label


def data_destination(entry: Any) -> str:
    """Where a provider's readiness entry says track metadata goes ("" = this machine).

    Reads the entry's ``sends_to`` (``ComponentStatus`` or ``as_dict`` form); an
    entry without one falls back to :func:`is_cloud_provider` and its label.
    """
    if entry is None:
        return ""
    if isinstance(entry, Mapping):
        if "sends_to" in entry:
            return str(entry.get("sends_to") or "")
    elif hasattr(entry, "sends_to"):
        return str(getattr(entry, "sends_to", "") or "")
    if not is_cloud_provider(_get(entry, "provider")):
        return ""
    return _get(entry, "label") or "a cloud provider"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class ComponentStatus:
    """The evaluated readiness of one component.

    ``action_label`` is the short state action for status strips ("Set up",
    "Check", "Fix", "Turn on" or ""); ``fix_label`` is the descriptive action
    from :func:`describe` ("Add API key", "Switch provider"…), sharing
    ``action_href``.
    """

    component: str
    level: Level
    cause: Optional[Cause] = None
    label: str = ""
    provider: str = ""
    model: str = ""
    detail: str = ""
    title: str = ""
    body: str = ""
    message: str = ""
    action_label: str = ""
    action_href: str = ""
    fix_label: str = ""
    secondary_label: str = ""
    secondary_href: str = ""
    sends_to: str = ""
    checked_at: Optional[float] = None
    from_record: bool = False

    @property
    def blocking(self) -> bool:
        """True only for a blocking cause derived from local facts.

        A cause read back from a check record (``from_record``) never blocks,
        even if it is a blocking kind: a stale record must not lock anyone out.
        """
        return is_blocking(self.cause) and not self.from_record

    def as_dict(self) -> Dict[str, str]:
        """String-only view for Reflex state (``dict[str, dict[str, str]]``).

        Keys: component, level, cause, state, tone, label, provider, model,
        detail, title, body, message, action_label, action_href, fix_label,
        secondary_label, secondary_href, sends_to (AI/embeddings: where track
        metadata is sent, "" when it stays on this machine; see
        :func:`ai_sends_to`), checked_at (local ISO time or ""), checked_ts
        (epoch seconds or ""), blocking ("true"/"false").
        """
        checked_iso = ""
        checked_ts = ""
        if self.checked_at:
            checked_iso = datetime.fromtimestamp(self.checked_at).isoformat(timespec="seconds")
            checked_ts = str(int(self.checked_at))
        return {
            "component": self.component,
            "level": self.level.value,
            "cause": self.cause.value if self.cause else "",
            "state": LEVEL_WORDS[self.level],
            "tone": LEVEL_TONES[self.level],
            "label": self.label,
            "provider": self.provider,
            "model": self.model,
            "detail": self.detail,
            "title": self.title,
            "body": self.body,
            "message": _clean_detail(self.message),
            "action_label": self.action_label,
            "action_href": self.action_href,
            "fix_label": self.fix_label,
            "secondary_label": self.secondary_label,
            "secondary_href": self.secondary_href,
            "sends_to": self.sends_to,
            "checked_at": checked_iso,
            "checked_ts": checked_ts,
            "blocking": "true" if self.blocking else "false",
        }


_CHECK_HREFS: Dict[str, str] = {
    "plex": SETTINGS_SECTION_HREFS["plex"],
    "ai": SETTINGS_SECTION_HREFS["ai"],
    "embeddings": SETTINGS_SECTION_HREFS["embeddings"],
}


def _finish(
    status: ComponentStatus, context: Mapping[str, Any], message: str = ""
) -> ComponentStatus:
    """Fill copy and the state action from level + cause."""
    status.message = message
    comp = status.component
    if status.cause is not None and status.level in (
        Level.NOT_SET_UP,
        Level.FAILING,
        Level.NOT_INSTALLED,
    ):
        ctx = dict(context)
        if message and "detail" not in ctx:
            ctx["detail"] = message
        copy = describe(comp, status.cause, ctx)
        status.title = copy["title"]
        status.body = copy["body"]
        status.fix_label = copy["action_label"]
        status.action_href = copy["action_href"]
        status.secondary_label = copy["secondary_label"]
        status.secondary_href = copy["secondary_href"]
    if status.level == Level.NOT_SET_UP:
        status.action_label = "Set up" if status.action_href else ""
    elif status.level == Level.FAILING:
        status.action_label = "Fix" if status.action_href else ""
    elif status.level in (Level.UNCHECKED, Level.READY) and comp in _CHECK_HREFS:
        status.action_label = "Check"
        status.action_href = _CHECK_HREFS[comp]
    elif status.level == Level.OFF and comp in SETTINGS_SECTION_HREFS:
        status.action_label = "Turn on"
        status.action_href = SETTINGS_SECTION_HREFS[comp]
    return status


def _apply_records(status: ComponentStatus, fp: str, store: ReadinessStore, now: float) -> str:
    """Set level/cause/checked_at from the store (checking > current record >
    unchecked) and return the record's message."""
    status.cause = None
    if store.is_checking(status.component, now):
        status.level = Level.CHECKING
        return ""
    rec = store.get_current(status.component, fp)
    if rec is None:
        status.level = Level.UNCHECKED
        return ""
    status.level, status.cause, status.checked_at = rec.level, rec.cause, rec.checked_at
    status.from_record = True
    return rec.message


def _parse_ts(value: Optional[str]) -> Optional[float]:
    """Epoch seconds for a stored timestamp, or None.

    ``SyncHistory.sync_date`` (and SQLite's ``CURRENT_TIMESTAMP``) are naive UTC, so a
    naive value is read as UTC, not local time; an aware value keeps its offset.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def evaluate(
    settings: "Settings",
    facts: Facts,
    store: Optional[ReadinessStore] = None,
    now: Optional[float] = None,
) -> Dict[str, ComponentStatus]:
    """Evaluate every component (design D2). Pure apart from the store read.

    Blocking causes come only from ``facts``; ``ready``/``failing`` for Plex, AI,
    Embeddings and MusicBrainz come only from store records whose fingerprint
    matches ``facts.fingerprints`` (when a fingerprint is missing there, it is
    computed without a secret). A check in flight shows ``checking``.
    """
    st = store if store is not None else readiness_store
    current = time.time() if now is None else now
    out: Dict[str, ComponentStatus] = {}

    def _fp(component: str) -> str:
        return facts.fingerprints.get(component) or fingerprint(component, settings, None)

    # ── Plex ──
    url = settings.plex.url or ""
    library = settings.plex.library_name or ""
    plex_ctx = {
        "label": "Plex",
        "url": url,
        "library": library,
        "timeout": PLEX_READINESS_TIMEOUT_S,
    }
    plex = ComponentStatus("plex", Level.NOT_SET_UP, label="Plex", model=library, detail=url)
    message = ""
    if not (url and facts.plex_token_present):
        plex.cause = Cause.MISSING_CONFIG
    elif not library:
        plex.cause = Cause.MISSING_CONFIG
        plex_ctx["code"] = "no_library"
    else:
        message = _apply_records(plex, _fp("plex"), st, current)
    out["plex"] = _finish(plex, plex_ctx, message)

    # ── AI provider ──
    ai_provider = _canonical_ai_provider(settings.ai.default_provider)
    ai_label = ai_provider_display_name(ai_provider)
    ai_ctx: Dict[str, Any] = {"label": ai_label, "provider": ai_provider}
    ai = ComponentStatus(
        "ai", Level.NOT_SET_UP, label=ai_label, provider=ai_provider, model=ai_model_for(settings)
    )
    ai_ctx["model"] = ai.model
    ai.sends_to = ai_sends_to(settings)
    message = ""
    if ai_provider == "local":
        if (settings.ai.local_mode or "builtin") == "endpoint":
            if not settings.ai.local_endpoint:
                ai.cause = Cause.MISSING_CONFIG
        elif not facts.local_deps_available:
            ai.cause = Cause.MISSING_DEPENDENCY
            ai_ctx["package"] = "transformers"
    elif ai_provider == "custom":
        if not settings.ai.custom_endpoint:
            ai.cause = Cause.MISSING_CONFIG
    elif ai_provider not in AI_PROVIDERS:
        ai.cause = Cause.MISSING_CONFIG
    elif not facts.ai_key_present:
        ai.cause = Cause.MISSING_API_KEY
    if ai.cause is None:
        message = _apply_records(ai, _fp("ai"), st, current)
    out["ai"] = _finish(ai, ai_ctx, message)

    # ── Embedding provider ──
    from plexmix.services.providers import diagnose_embedding_provider

    emb_provider = (settings.embedding.default_provider or "").strip().lower()
    emb_label = embedding_provider_display_name(emb_provider)
    emb = ComponentStatus(
        "embeddings",
        Level.NOT_SET_UP,
        label=emb_label,
        provider=emb_provider,
        model=embedding_model_for(settings),
        detail=f"{facts.expected_dimension}d" if facts.expected_dimension else "",
    )
    emb_ctx: Dict[str, Any] = {"label": emb_label, "provider": emb_provider, "model": emb.model}
    emb.sends_to = embedding_sends_to(settings)
    message = ""
    failure = diagnose_embedding_provider(
        settings,
        key_present=facts.embedding_key_present,
        local_deps=facts.local_deps_available,
    )
    if failure is not None:
        emb.cause = failure.cause
        emb.level = Level.FAILING if failure.cause == Cause.MISSING_DEPENDENCY else Level.NOT_SET_UP
        if failure.package:
            emb_ctx["package"] = failure.package
    else:
        message = _apply_records(emb, _fp("embeddings"), st, current)
    out["embeddings"] = _finish(emb, emb_ctx, message)

    # ── Library ──
    total = facts.total_tracks
    lib = ComponentStatus("library", Level.READY, label="Library")
    if facts.db_error:
        lib.level, lib.cause = Level.FAILING, Cause.UNKNOWN
    elif not facts.db_exists or total == 0:
        lib.level, lib.cause = Level.NOT_SET_UP, Cause.NOT_SYNCED
    else:
        lib.detail = _plural(total, "track")
        lib.checked_at = _parse_ts(facts.last_sync)
    out["library"] = _finish(lib, {"total_tracks": total})

    # ── Search index ──
    idx = ComponentStatus(
        "index",
        Level.READY,
        label="Search index",
        provider=emb_provider,
        model=emb.model,
    )
    idx_ctx: Dict[str, Any] = {
        "label": emb_label,
        "provider": emb_provider,
        "model": emb.model,
        "total_tracks": total,
        "index_dimension": facts.index_dimension,
        "expected_dimension": facts.expected_dimension,
    }
    if lib.level != Level.READY:
        idx.level, idx.cause = Level.NOT_SET_UP, Cause.NOT_SYNCED
    elif facts.embedded_tracks == 0:
        idx.level, idx.cause = Level.FAILING, Cause.INDEX_EMPTY
    elif (
        facts.index_dimension
        and facts.expected_dimension
        and facts.index_dimension != facts.expected_dimension
    ):
        idx.level, idx.cause = Level.FAILING, Cause.DIMENSION_MISMATCH
        idx.detail = f"{facts.index_dimension}d index · provider makes {facts.expected_dimension}d"
    elif not facts.index_exists or (facts.index_dimension and not facts.index_vector_count):
        # Tracks have embeddings but the FAISS file is gone (deleted, or removed
        # by VectorIndex as corrupt) or holds no vectors: searches find nothing.
        idx.level, idx.cause = Level.FAILING, Cause.INDEX_EMPTY
        idx_ctx["index_missing"] = True
    else:
        dim = facts.index_dimension or facts.expected_dimension
        idx.detail = f"{_count(facts.embedded_tracks)} of {_plural(total, 'track')} embedded" + (
            f" · {dim}d" if dim else ""
        )
    out["index"] = _finish(idx, idx_ctx)

    # ── Audio analysis (optional) ──
    audio = ComponentStatus("audio", Level.READY, label="Audio analysis")
    if not facts.essentia_available:
        audio.level, audio.cause = Level.NOT_INSTALLED, Cause.MISSING_DEPENDENCY
    elif not facts.audio_enabled:
        audio.level = Level.OFF
    else:
        audio.detail = f"{_count(facts.audio_tracks)} of {_plural(total, 'track')} analyzed"
    out["audio"] = _finish(audio, {"package": "essentia"})

    # ── MusicBrainz (optional) ──
    mb = ComponentStatus("musicbrainz", Level.OFF, label="MusicBrainz")
    message = ""
    if facts.musicbrainz_enabled:
        message = _apply_records(mb, _fp("musicbrainz"), st, current)
        mb.detail = f"{_count(facts.musicbrainz_tracks)} of {_plural(total, 'track')} enriched"
    out["musicbrainz"] = _finish(mb, {"label": "MusicBrainz"}, message)
    return out


def status_snapshot(status: Mapping[str, ComponentStatus]) -> Dict[str, Dict[str, str]]:
    """``{component: ComponentStatus.as_dict()}`` for ``AppState.readiness``."""
    return {name: comp.as_dict() for name, comp in status.items()}


def _get(entry: Any, key: str) -> str:
    """Read a field from a ComponentStatus or its ``as_dict()`` form.

    ``Mapping`` is ``collections.abc.Mapping`` on purpose: Reflex hands state
    dicts to computed vars as ``MutableProxy`` objects, which pass an ABC
    ``isinstance`` check but fail ``typing.Mapping``'s.
    """
    if entry is None:
        return ""
    if isinstance(entry, Mapping):
        return str(entry.get(key, "") or "")
    if key == "blocking":
        return "true" if getattr(entry, "blocking", False) else "false"
    value = getattr(entry, key, "")
    if isinstance(value, Enum):
        return str(value.value)
    return "" if value is None else str(value)


def _blocked(entry: Any) -> bool:
    """Blocking per the entry's own ``blocking`` flag (record causes never block)."""
    if entry is None:
        return False
    if isinstance(entry, Mapping) and "blocking" not in entry:
        return is_blocking(_get(entry, "cause"))
    return _get(entry, "blocking") == "true"


def generation_blockers(status: Mapping[str, Any]) -> List[str]:
    """Components (embeddings, library, index) whose blocking cause stops generation."""
    return [c for c in GENERATION_COMPONENTS if _blocked(status.get(c))]


def can_generate(status: Mapping[str, Any]) -> bool:
    """True unless a blocking cause stops generation (network failures never block)."""
    return not generation_blockers(status)


def needs_attention(status: Mapping[str, Any]) -> bool:
    """Doctor rail badge: a required component is not set up, failing or blocked."""
    for comp in REQUIRED_COMPONENTS:
        entry = status.get(comp)
        if entry is None:
            continue
        if _get(entry, "level") in (Level.NOT_SET_UP.value, Level.FAILING.value):
            return True
        if _blocked(entry):
            return True
    return False


# ---------------------------------------------------------------------------
# Job scope confirmations
# ---------------------------------------------------------------------------

#: Providers that have no model and that the scope copy already names ("5,807 tracks
#: will be looked up on MusicBrainz", "MusicBrainz allows about one lookup per
#: second"). A provider line for them would only repeat the name: screen readers read
#: the description as one run of text ("…on MusicBrainz. MusicBrainz MusicBrainz
#: allows…").
SCOPE_COPY_NAMES_PROVIDER: frozenset[str] = frozenset({"MusicBrainz"})


def scope_provider_text(provider_label: str, model: str = "") -> str:
    """The text of a confirmation's provider line ("" when there is no line).

    "<Provider> · <model>" when there is a model; the bare provider name when there is
    none, unless the scope copy already names that provider
    (:data:`SCOPE_COPY_NAMES_PROVIDER`), so a model-less provider is named once.
    ``scope_confirm.scope_provider_line`` renders the same rule.
    """
    label = (provider_label or "").strip()
    name = (model or "").strip()
    if label and name:
        return f"{label} · {name}"
    if not label or label in SCOPE_COPY_NAMES_PROVIDER:
        return ""
    return label


def describe_job_scope(
    kind: str,
    count: int,
    provider_label: str = "",
    model: str = "",
    is_cloud: bool = False,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """Copy for a confirmation before long, cloud-bound or overwriting work.

    ``kind``: ``tagging``, ``embeddings``, ``index`` (rebuild the search index
    from the embeddings ``count`` tracks already have; nothing is sent anywhere),
    ``musicbrainz``, ``audio`` or ``cleanup``. ``extra`` keys: ``replace_count``
    (tagging: tracks whose tags will be replaced), ``rebuild`` (embeddings:
    replaces existing embeddings), ``rate_s`` (MusicBrainz seconds per lookup,
    default 1.0), ``sends_to`` (where track metadata goes when ``is_cloud``, from
    :func:`data_destination`; defaults to ``provider_label``).

    Returns ``{"title", "count_line", "replace_line", "provider_line",
    "privacy_line", "duration_line", "confirm_label", "secondary_label",
    "danger"}`` (strings; ``danger`` is "true" when the confirmed action
    replaces data; ``provider_line`` is :func:`scope_provider_text`). No money
    estimates: there is no defensible price data.
    """
    ex: Mapping[str, Any] = extra or {}
    n = max(_n(count), 0)
    tracks = _plural(n, "track")
    provider_line = scope_provider_text(provider_label, model)
    destination = str(ex.get("sends_to") or "").strip() or provider_label
    if is_cloud and destination and kind != "index":
        sends = (
            "Track titles, artists, albums and genres"
            if kind == "tagging"
            else "Track titles, artists, albums, genres and tags"
        )
        privacy = f"{sends} are sent to {destination}."
    else:
        privacy = "Runs on this machine. Nothing is sent to a cloud provider."
    out = {
        "title": "",
        "count_line": "",
        "replace_line": "",
        "provider_line": provider_line,
        "privacy_line": privacy,
        "duration_line": BATCH_DURATION_LINE,
        "confirm_label": "",
        "secondary_label": "",
        "danger": "false",
    }
    if kind == "tagging":
        replace = min(max(_n(ex.get("replace_count")), 0), n)
        untagged = n - replace
        out["title"] = f"Tag {tracks}?"
        out["count_line"] = f"{tracks} will be tagged."
        if replace:
            out["replace_line"] = f"{_plural(replace, 'existing tag set')} will be replaced."
            out["danger"] = "true"
            if untagged:
                out["secondary_label"] = f"Tag only the {_plural(untagged, 'untagged track')}"
        out["confirm_label"] = "Tag tracks"
    elif kind == "embeddings":
        if ex.get("rebuild"):
            out["title"] = f"Rebuild embeddings for {tracks}?"
            out["count_line"] = f"The embeddings for {tracks} will be replaced."
            out["confirm_label"] = "Rebuild embeddings"
            out["danger"] = "true"
        else:
            out["title"] = f"Generate embeddings for {tracks}?"
            out["count_line"] = f"{tracks} will get embeddings so search can find them."
            out["confirm_label"] = "Generate embeddings"
    elif kind == "index":
        out["title"] = "Rebuild the search index?"
        out["count_line"] = (
            f"The search index will be rebuilt from the embeddings {_plural(n, 'track')} "
            f"already {'has' if n == 1 else 'have'}. No embeddings are generated."
        )
        out["duration_line"] = "Progress shows once it starts · you can cancel"
        out["confirm_label"] = "Build search index"
    elif kind == "musicbrainz":
        rate = float(ex.get("rate_s") or 1.0)
        pace = (
            "about one lookup per second"
            if abs(rate - 1.0) < 1e-9
            else f"about one lookup every {rate:g} seconds"
        )
        out["title"] = f"Enrich {tracks} from MusicBrainz?"
        out["count_line"] = f"{tracks} will be looked up on MusicBrainz."
        # The count, duration and privacy lines name MusicBrainz: no provider line.
        out["provider_line"] = scope_provider_text("MusicBrainz")
        out["privacy_line"] = (
            "Track titles, artists and albums are sent to MusicBrainz, a free public "
            "music database."
        )
        out["duration_line"] = (
            f"MusicBrainz allows {pace}, so this can take up to about "
            f"{format_duration(n * rate)}. You can cancel at any time."
        )
        out["confirm_label"] = "Enrich tracks"
    elif kind == "audio":
        out["title"] = f"Analyze audio for {tracks}?"
        out["count_line"] = f"{tracks} will be analyzed for tempo, key, energy and more."
        out["privacy_line"] = "Runs on this machine. Nothing is sent to a cloud provider."
        out["confirm_label"] = "Analyze audio"
    elif kind == "cleanup":
        entries = _plural(n, "orphaned embedding")
        out["title"] = f"Remove {entries}?"
        out["count_line"] = (
            f"{entries} for tracks no longer in your library will be removed. "
            "This is safe: no track loses its embedding."
        )
        out["privacy_line"] = "Runs on this machine. Nothing is sent to a cloud provider."
        out["duration_line"] = ""
        out["confirm_label"] = "Remove orphaned embeddings"
    else:
        out["title"] = f"Process {tracks}?"
        out["count_line"] = f"{tracks} will be processed."
        out["confirm_label"] = "Start"
    return out


# ---------------------------------------------------------------------------
# Doctor rows and verdict
# ---------------------------------------------------------------------------

ROW_WHY: Dict[str, str] = {
    "conn_plex": "PlexMix reads your library from Plex and saves playlists back to it.",
    "conn_ai": "The AI provider writes tags for your tracks. It's optional, but improves results.",
    "conn_embeddings": "The embedding provider turns your vibe into a search. Generation needs it.",
    "library": "PlexMix can only search the tracks it has synced from Plex.",
    "embeddings": "Tracks without embeddings can't be found by vibe search or appear in playlists.",
    "orphans": (
        "Leftover search entries for tracks no longer in your library. Cleaning them up is safe."
    ),
    "tags": "Tags describe mood and style, which helps playlists match the vibe you describe.",
    "audio": "Tempo, key and energy from the audio itself sharpen search and audio filters.",
    "musicbrainz": "MusicBrainz adds community genres that make search results more precise.",
}

ROW_STATE_WORDS: Dict[str, str] = {
    "checking": "Checking…",
    "ok": "Healthy",
    "attention": "Needs attention",
    "unchecked": "Not checked",
    "off": "Off",
    "not_installed": "Not installed",
    "no_database": "No library yet",
}

_LIBRARY_ROWS: tuple[tuple[str, str, str], ...] = (
    ("library", "Library", "required"),
    ("embeddings", "Embeddings", "required"),
    ("orphans", "Orphaned embeddings", "recommended"),
    ("tags", "Tags", "recommended"),
    ("audio", "Audio features", "optional"),
    ("musicbrainz", "MusicBrainz genres", "optional"),
)


def _row(
    row_id: str,
    group: str,
    tier: str,
    state: str,
    title: str,
    *,
    count: str = "",
    model: str = "",
    summary: str = "",
    body: str = "",
    action_label: str = "",
    action_href: str = "",
    check_label: str = "",
    check_href: str = "",
    secondary_label: str = "",
    secondary_href: str = "",
    fix: str = "",
    fix_label: str = "",
) -> Dict[str, str]:
    if state == "ok":
        tone = "success"
    elif state == "attention":
        tone = "error" if tier == "required" else "warning"
    elif state == "no_database":
        tone = "warning"
    else:
        tone = "idle"
    word = ROW_STATE_WORDS.get(state, state)
    return {
        "id": row_id,
        "group": group,
        "tier": tier,
        "state": state,
        "state_word": word,
        "tone": tone,
        "title": title,
        "count": count,
        "model": model,
        "summary": summary,
        "body": body,
        "why": ROW_WHY.get(row_id, ""),
        "action_label": action_label,
        "action_href": action_href,
        "check_label": check_label,
        "check_href": check_href,
        "secondary_label": secondary_label,
        "secondary_href": secondary_href,
        "fix": fix,
        "fix_label": fix_label,
    }


def _level_of(entry: Any) -> Optional[Level]:
    try:
        return Level(_get(entry, "level"))
    except ValueError:
        return None


def _connection_rows(status: Mapping[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for comp, row_id, title, tier in (
        ("plex", "conn_plex", "Plex", "required"),
        ("ai", "conn_ai", "AI provider", "recommended"),
        ("embeddings", "conn_embeddings", "Embedding provider", "required"),
    ):
        entry = status.get(comp)
        level = _level_of(entry)
        if entry is None or level is None:
            continue
        if level == Level.READY:
            state = "ok"
        elif level == Level.CHECKING:
            state = "checking"
        elif level == Level.UNCHECKED:
            state = "unchecked"
        else:
            state = "attention"
        label = _get(entry, "label")
        fix_href = _get(entry, "action_href") if state == "attention" else ""
        row = _row(
            row_id,
            "connections",
            tier,
            state,
            f"{title}: {label}" if comp != "plex" and label else title,
            model=_get(entry, "model"),
            summary=_get(entry, "title") or LEVEL_WORDS[level],
            body=_get(entry, "body"),
            action_label="Fix" if fix_href else "",
            action_href=fix_href,
            check_label="Check",
            check_href=_CHECK_HREFS[comp],
        )
        # Connection rows speak readiness words ("Ready", "Set up · not checked"),
        # never "Healthy", so a fresh install can't show a healthy row.
        row["state_word"] = LEVEL_WORDS[level]
        row["tone"] = LEVEL_TONES[level]
        rows.append(row)
    return rows


def doctor_rows(
    stats: Optional[Mapping[str, Any]],
    status: Optional[Mapping[str, Any]] = None,
    flags: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Doctor rows (design D8): connection rows first, then library rows.

    ``stats`` uses the keys of ``DoctorState._compute_health_stats``
    (total_tracks, tracks_with_embeddings, orphaned_count, untagged_count,
    tracks_needing_embeddings, audio_features_count, tracks_without_audio,
    mb_enriched, mb_without) or is ``None`` before the scan / without a DB.
    ``status`` is the readiness result (``ComponentStatus`` or ``as_dict`` form).
    ``flags``: ``scan_state`` (idle|checking|done|no_database|error; defaults
    to ``done`` with stats and ``checking`` without), ``audio_available``,
    ``audio_enabled``, ``musicbrainz_enabled`` (each defaults from ``status``).

    Every row is a ``dict[str, str]`` with keys: id (conn_plex|conn_ai|
    conn_embeddings|library|embeddings|orphans|tags|audio|musicbrainz), group
    (connections|library), tier (required|recommended|optional), state
    (checking|ok|attention|unchecked|off|not_installed|no_database), state_word,
    tone (success|warning|error|idle), title, count (grouped number), model
    (connection rows), summary, body (connection rows: the cause body), why,
    action_label/action_href (Fix, Turn on, Connect Plex…), check_label/
    check_href (connection rows: Check → the Settings test), secondary_label/
    secondary_href (no_database: Sync library), fix (the DoctorState fix target:
    embeddings_incremental|embeddings_full|cleanup|tags|audio_analysis|
    musicbrainz, or "") and fix_label.

    A missing or empty library collapses the library group into one
    ``no_database`` row; until the scan finishes every library row is
    ``checking`` (never "Healthy").
    """
    st: Mapping[str, Any] = status or {}
    fl: Mapping[str, Any] = flags or {}
    rows = _connection_rows(st)

    scan_state = str(fl.get("scan_state") or ("done" if stats is not None else "checking"))
    audio_level = _level_of(st.get("audio"))
    mb_level = _level_of(st.get("musicbrainz"))

    def _flag(name: str, default: bool) -> bool:
        return bool(fl[name]) if name in fl else default

    audio_available = _flag("audio_available", audio_level != Level.NOT_INSTALLED)
    audio_enabled = _flag(
        "audio_enabled", audio_level not in (None, Level.OFF, Level.NOT_INSTALLED)
    )
    mb_enabled = _flag("musicbrainz_enabled", mb_level not in (None, Level.OFF))

    def _no_database() -> Dict[str, str]:
        return _row(
            "library",
            "library",
            "required",
            "no_database",
            "No library yet",
            summary="Connect Plex and sync your library, then Doctor can check it.",
            action_label="Connect Plex",
            action_href=SETTINGS_SECTION_HREFS["plex"],
            secondary_label="Sync library",
            secondary_href=LIBRARY_HREF,
        )

    if scan_state in ("idle", "checking") and stats is None:
        for row_id, title, tier in _LIBRARY_ROWS:
            rows.append(_row(row_id, "library", tier, "checking", title, summary="Checking…"))
        return rows
    if scan_state == "error":
        rows.append(
            _row(
                "library",
                "library",
                "required",
                "attention",
                "Library",
                summary="The health check couldn't read your library.",
                action_label="Re-scan",
                action_href=DOCTOR_HREF,
            )
        )
        return rows
    if scan_state == "no_database" or stats is None or _n(stats.get("total_tracks")) == 0:
        rows.append(_no_database())
        return rows

    total = _n(stats.get("total_tracks"))
    need_emb = _n(stats.get("tracks_needing_embeddings"))
    orphans = _n(stats.get("orphaned_count"))
    untagged = _n(stats.get("untagged_count"))
    no_audio = _n(stats.get("tracks_without_audio"))
    no_mb = _n(stats.get("mb_without"))
    meta = {row_id: (title, tier) for row_id, title, tier in _LIBRARY_ROWS}

    def add(row_id: str, state: str, **kwargs: str) -> None:
        title, tier = meta[row_id]
        rows.append(_row(row_id, "library", tier, state, title, **kwargs))

    def every(singular: str, plural: str) -> str:
        return f"The 1 track {singular}" if total == 1 else f"All {_count(total)} tracks {plural}"

    add("library", "ok", count=_count(total), summary=f"{_plural(total, 'track')} synced")

    def not_installed(row_id: str, entry: Any) -> None:
        """A fix whose provider package is missing: how to enable it, no fix button."""
        add(
            row_id,
            "not_installed",
            summary=_get(entry, "body") or _get(entry, "title"),
            action_label=_get(entry, "fix_label") or _get(entry, "action_label"),
            action_href=_get(entry, "action_href"),
        )

    index_entry = st.get("index")
    emb_entry = st.get("embeddings")
    emb_missing_dep = _get(emb_entry, "cause") == Cause.MISSING_DEPENDENCY.value
    index_cause = _get(index_entry, "cause")
    if index_cause == Cause.DIMENSION_MISMATCH.value:
        if emb_missing_dep:
            not_installed("embeddings", emb_entry)
        else:
            add(
                "embeddings",
                "attention",
                count=_count(total),
                summary=_get(index_entry, "title"),
                fix="embeddings_full",
                fix_label="Rebuild embeddings",
            )
    elif need_emb:
        if emb_missing_dep:
            not_installed("embeddings", emb_entry)
        else:
            add(
                "embeddings",
                "attention",
                count=_count(need_emb),
                summary=f"{_plural(need_emb, 'track')} without embeddings",
                fix="embeddings_incremental",
                fix_label="Generate embeddings",
            )
    elif index_cause == Cause.INDEX_EMPTY.value:
        # Every track has an embedding, but the search index file is missing or
        # empty: rebuilding it from the stored vectors is the Dashboard's job.
        add(
            "embeddings",
            "attention",
            count=_count(total),
            summary=_get(index_entry, "title"),
            action_label=_get(index_entry, "fix_label") or "Build search index",
            action_href=_get(index_entry, "action_href") or DASHBOARD_HREF,
        )
    else:
        add(
            "embeddings",
            "ok",
            count=_count(total),
            summary=every("has an embedding", "have embeddings"),
        )

    if orphans:
        add(
            "orphans",
            "attention",
            count=_count(orphans),
            summary=f"{_plural(orphans, 'orphaned embedding')} to clean up",
            fix="cleanup",
            fix_label="Clean up",
        )
    else:
        add("orphans", "ok", count="0", summary="No leftover search entries")

    ai_entry = st.get("ai")
    if untagged and _get(ai_entry, "cause") == Cause.MISSING_DEPENDENCY.value:
        not_installed("tags", ai_entry)
    elif untagged:
        add(
            "tags",
            "attention",
            count=_count(untagged),
            summary=f"{_plural(untagged, 'track')} without tags",
            fix="tags",
            fix_label="Tag tracks",
        )
    else:
        add("tags", "ok", count=_count(total), summary=every("has tags", "have tags"))

    if not audio_available:
        add("audio", "not_installed", summary=describe("audio", Cause.MISSING_DEPENDENCY)["body"])
    elif not audio_enabled:
        add(
            "audio",
            "off",
            summary="Audio analysis is turned off.",
            action_label="Turn on",
            action_href=SETTINGS_SECTION_HREFS["audio"],
        )
    elif no_audio:
        add(
            "audio",
            "attention",
            count=_count(no_audio),
            summary=f"{_plural(no_audio, 'track')} not analyzed yet",
            fix="audio_analysis",
            fix_label="Analyze",
        )
    else:
        add(
            "audio",
            "ok",
            count=_count(_n(stats.get("audio_features_count"))),
            summary="Every track with a file has audio features",
        )

    if not mb_enabled:
        add(
            "musicbrainz",
            "off",
            summary="MusicBrainz enrichment is turned off.",
            action_label="Turn on",
            action_href=SETTINGS_SECTION_HREFS["musicbrainz"],
        )
    elif no_mb:
        add(
            "musicbrainz",
            "attention",
            count=_count(no_mb),
            summary=f"{_plural(no_mb, 'track')} without MusicBrainz data",
            fix="musicbrainz",
            fix_label="Enrich",
        )
    else:
        add(
            "musicbrainz",
            "ok",
            count=_count(_n(stats.get("mb_enriched"))),
            summary="Every track has MusicBrainz data",
        )
    return rows


def verdict(rows: List[Mapping[str, str]]) -> Dict[str, str]:
    """The Doctor hero, derived only from ``rows`` (design D8).

    Returns ``{"state", "title", "sub", "tone"}``; ``state`` is one of
    ``checking``, ``no_database``, ``attention`` (a required row needs
    attention), ``fixes_available`` (only recommended/optional rows do),
    ``ready`` (all pass, some connections not checked yet) or ``healthy``.
    "All systems healthy" is only used for ``healthy``; optional rows that are
    off or not installed never count against the verdict.
    """
    library_rows = [r for r in rows if r.get("group") == "library"]
    if not library_rows or any(r.get("state") == "checking" for r in library_rows):
        return {
            "state": "checking",
            "title": "Checking your library…",
            "sub": "This takes a few seconds.",
            "tone": "idle",
        }
    if any(r.get("state") == "no_database" for r in rows):
        return {
            "state": "no_database",
            "title": "No library yet",
            "sub": "Connect Plex and sync your library to get started.",
            "tone": "warning",
        }
    attention = [r for r in rows if r.get("state") == "attention"]
    required = [r for r in attention if r.get("tier") == "required"]
    if required:
        gaps = "; ".join(r.get("summary") or r.get("title", "") for r in required)
        return {"state": "attention", "title": "Attention needed", "sub": gaps, "tone": "error"}
    recommended = sum(1 for r in attention if r.get("tier") == "recommended")
    optional = sum(1 for r in attention if r.get("tier") == "optional")
    if recommended or optional:
        parts = []
        if recommended:
            parts.append(f"{recommended} recommended {'fix' if recommended == 1 else 'fixes'}")
        if optional:
            parts.append(f"{optional} optional {'fix' if optional == 1 else 'fixes'}")
        return {
            "state": "fixes_available",
            "title": "Search is ready",
            "sub": f"{' and '.join(parts)} available.",
            "tone": "warning",
        }
    unchecked = [
        r.get("title", "")
        for r in rows
        if r.get("group") == "connections" and r.get("state") in ("unchecked", "checking")
    ]
    if unchecked:
        return {
            "state": "ready",
            "title": "Search is ready",
            "sub": f"Library checks passed · not checked yet: {', '.join(unchecked)}.",
            "tone": "success",
        }
    return {
        "state": "healthy",
        "title": "All systems healthy",
        "sub": "Every check passed.",
        "tone": "success",
    }


# ---------------------------------------------------------------------------
# First-run checklist
# ---------------------------------------------------------------------------

SETUP_STEP_IDS: tuple[str, ...] = ("plex", "providers", "sync", "index", "vibe")


def _job_field(job: Any, key: str) -> Any:
    if job is None:
        return None
    if isinstance(job, Mapping):
        return job.get(key)
    return getattr(job, key, None)


def _step_done(step_id: str, status: Mapping[str, Any]) -> bool:
    if step_id == "plex":
        level = _get(status.get("plex"), "level")
        return bool(level) and level != Level.NOT_SET_UP.value
    if step_id == "providers":
        entry = status.get("embeddings")
        level = _get(entry, "level")
        return bool(level) and level != Level.NOT_SET_UP.value and not _blocked(entry)
    if step_id == "sync":
        return _get(status.get("library"), "level") == Level.READY.value
    if step_id == "index":
        return _get(status.get("index"), "level") == Level.READY.value
    return False  # "vibe" is the hand-off step: never done


def setup_incomplete(status: Mapping[str, Any]) -> bool:
    """True while any of Connect Plex, Choose providers, Sync or Build index is open."""
    return not all(_step_done(s, status) for s in ("plex", "providers", "sync", "index"))


def setup_steps(
    status: Mapping[str, Any],
    stats: Optional[Mapping[str, Any]] = None,
    jobs: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, str]]:
    """The five first-run steps (design D13), in order.

    ``status``: readiness (``ComponentStatus`` or ``as_dict`` form). ``stats``:
    ``Facts.as_dict()``-style counts (``total_tracks``, ``missing_embeddings``…).
    ``jobs``: ``{"sync": entry, "embedding": entry}`` where an entry is a
    ``TaskEntry`` or a dict with ``status``/``progress``/``message``.

    Each step is a ``dict[str, str]`` with keys: id (plex|providers|sync|index|
    vibe), title, desc, note, state (done|current|todo|running: a step whose
    job runs is "running" wherever it sits; "current" is the first open step
    before any running one), primary ("true" only for the current step),
    action_label, href ("" for steps whose
    action dispatches an event: sync → ``LibraryState.start_sync``, index →
    ``LibraryState.request_embed_all``), action (link|sync|embed_all), progress
    ("0"-"100" while running, "" otherwise) and message (job message).
    """
    sts: Mapping[str, Any] = stats or {}
    jbs: Mapping[str, Any] = jobs or {}
    ai = status.get("ai")
    emb = status.get("embeddings")
    ai_level = _get(ai, "level")
    ai_label = _get(ai, "label")
    ai_model = _get(ai, "model")
    ai_ready_ish = bool(ai_level) and ai_level != Level.NOT_SET_UP.value
    emb_label = _get(emb, "label")
    emb_model = _get(emb, "model")
    emb_dest = data_destination(emb)
    total = _n(sts.get("total_tracks"))
    missing = _n(sts.get("missing_embeddings"))

    if ai_ready_ish:
        ai_dest = data_destination(ai)
        ai_note = f"AI: {ai_label}" + (f" · {ai_model}" if ai_model else "")
        sync_note = f"Sync also tags new tracks with {ai_label}" + (
            f" · track metadata is sent to {ai_dest}" if ai_dest else ""
        )
    else:
        ai_note = "An AI provider is optional; it adds tags for better results."
        sync_note = ""
    if emb_label:
        index_note = (
            f"Uses {emb_label}"
            + (f" · {emb_model}" if emb_model else "")
            + (
                f" · track metadata is sent to {emb_dest}"
                if emb_dest
                else " · runs on this machine"
            )
        )
    else:
        index_note = ""
    index_entry = status.get("index")
    index_cause = _get(index_entry, "cause")
    if index_cause == Cause.DIMENSION_MISMATCH.value or (
        index_cause == Cause.INDEX_EMPTY.value and not missing and total
    ):
        # The index doesn't match the provider, or its file is missing: say so
        # instead of "N tracks need embeddings" (Build checks what it can do).
        index_desc = _get(index_entry, "title") + "."
    elif missing:
        index_desc = (
            f"{_plural(missing, 'track')} need embeddings so PlexMix can find them by vibe."
        )
    else:
        index_desc = "Create embeddings so PlexMix can find tracks by vibe."

    plex_entry = status.get("plex")
    plex_note = ""
    if _get(plex_entry, "level") == Level.FAILING.value:
        plex_note = _get(plex_entry, "title")

    specs = [
        {
            "id": "plex",
            "title": "Connect Plex",
            "desc": "Add your Plex server, token and music library.",
            "note": plex_note,
            "action_label": "Connect Plex",
            "href": SETTINGS_SECTION_HREFS["plex"],
            "action": "link",
            "job": "",
        },
        {
            "id": "providers",
            "title": "Choose providers",
            "desc": (
                f"Search uses {emb_label}" + (f" · {emb_model}" if emb_model else "") + "."
                if emb_label and _step_done("providers", status)
                else "Pick the embedding provider that powers search."
            ),
            "note": ai_note,
            "action_label": "Choose providers",
            "href": SETTINGS_SECTION_HREFS["embeddings"],
            "action": "link",
            "job": "",
        },
        {
            "id": "sync",
            "title": "Sync library",
            "desc": (
                f"{_plural(total, 'track')} synced."
                if total
                else "Copy your Plex music library into PlexMix."
            ),
            "note": sync_note,
            "action_label": "Sync library",
            "href": "",
            "action": "sync",
            "job": "sync",
        },
        {
            "id": "index",
            "title": "Build search index",
            "desc": index_desc,
            "note": index_note,
            "action_label": "Build search index",
            "href": "",
            "action": "embed_all",
            "job": "embedding",
        },
        {
            "id": "vibe",
            "title": "Describe your first vibe",
            "desc": "Open the Generator and describe a mood, an activity or a moment.",
            "note": "",
            "action_label": "Open Generator",
            "href": GENERATOR_HREF,
            "action": "link",
            "job": "",
        },
    ]

    steps: List[Dict[str, str]] = []
    current_found = False
    for spec in specs:
        step_id = spec["id"]
        done = _step_done(step_id, status)
        job = jbs.get(spec["job"]) if spec["job"] else None
        running = _job_field(job, "status") == "running"
        progress = ""
        message = ""
        if done:
            state = "done"
        elif running:
            # A step's job shows as running wherever it is in the order (a sync
            # started while an earlier step is still open shows its progress and
            # Cancel too); a running first open step holds the "current" slot.
            state = "running"
            current_found = True
        elif not current_found:
            current_found = True
            state = "current"
        else:
            state = "todo"
        if state == "running":
            progress = str(max(0, min(100, _n(_job_field(job, "progress")))))
            message = str(_job_field(job, "message") or "")
        steps.append(
            {
                "id": step_id,
                "title": spec["title"],
                "desc": spec["desc"],
                "note": spec["note"],
                "state": state,
                "primary": "true" if state == "current" else "false",
                "action_label": spec["action_label"],
                "href": spec["href"],
                "action": spec["action"],
                "progress": progress,
                "message": message,
            }
        )
    return steps


__all__ = [
    "AUDIO_REMEDY",
    "BATCH_DURATION_LINE",
    "BLOCKING_CAUSES",
    "COMPONENTS",
    "COMPONENT_LABELS",
    "DASHBOARD_HREF",
    "DOCTOR_HREF",
    "GENERATION_COMPONENTS",
    "GENERATOR_HREF",
    "LEVEL_TONES",
    "LEVEL_WORDS",
    "LIBRARY_HREF",
    "LOCAL_REMEDY",
    "REQUIRED_COMPONENTS",
    "ROW_STATE_WORDS",
    "ROW_WHY",
    "SCOPE_COPY_NAMES_PROVIDER",
    "SETTINGS_SECTION_HREFS",
    "SETUP_STEP_IDS",
    "TAGGING_HREF",
    "Cause",
    "CheckRecord",
    "ComponentStatus",
    "Facts",
    "Level",
    "ReadinessStore",
    "ai_model_for",
    "ai_sends_to",
    "can_generate",
    "cause_for_plex_error_code",
    "collect_facts",
    "current_fingerprint",
    "data_destination",
    "describe",
    "describe_failure",
    "describe_job_scope",
    "doctor_rows",
    "embedding_model_for",
    "embedding_sends_to",
    "endpoint_host",
    "evaluate",
    "fingerprint",
    "fingerprint_fields",
    "fingerprint_values",
    "format_duration",
    "generation_blockers",
    "is_blocking",
    "is_cloud_provider",
    "needs_attention",
    "read_index_metadata",
    "readiness_store",
    "resolve_secret",
    "scope_provider_text",
    "setup_incomplete",
    "setup_steps",
    "status_snapshot",
    "verdict",
]
