import logging
import re
import reflex as rx
import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from plexmix.ui.secret_drafts import ACTION_CLEAR, ACTION_REPLACE, secret_drafts
from plexmix.ui.states.app_state import AppState
from plexmix.ui.utils.a11y import (
    SETTINGS_SECTIONS,
    resolve_settings_section,
    settings_section_url_script,
)
from plexmix.ui.utils.helpers import format_count, pluralize, safe_error_text
from plexmix.ui.utils.validation import (
    validate_url,
    validate_plex_token,
    validate_api_key,
    validate_temperature,
    validate_batch_size,
)
from plexmix.ai.local_provider import LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL

logger = logging.getLogger(__name__)


# ── Secret fields ─────────────────────────────────────────────────────
#
# Secret values (the Plex token, provider API keys, custom-endpoint keys and the
# local LLM auth token) never live in Reflex state: public vars are synced to the
# browser and every var, backend-only ones included, is pickled into ``.states``.
# The state only carries value-free status per field id (``secret_stored``,
# ``secret_source``, ``secret_staged``). Typed replacements are held server-side
# in :data:`plexmix.ui.secret_drafts.secret_drafts`, and stored values are resolved
# on the server only when a connection test, model discovery or save needs them.


@dataclass(frozen=True)
class SecretField:
    """One replace-only secret input on the Settings page."""

    field: str  # field id; the input's DOM id is ``secret-<field>``
    label: str  # human-readable name for messages (never the value)
    optional: bool  # optional secrets can be cleared
    scope: str  # "plex" | "ai" | "embedding"; provider switches drop drafts in their scope
    provider_keyed: bool = False  # credential key depends on the selected provider
    credential_key: Optional[str] = None  # fixed credential-store key, if any
    getter: Optional[str] = None  # ``plexmix.config.credentials`` getter for the stored value
    config_attr: Optional[Tuple[str, str]] = None  # (section, attr) in config.yaml
    config_first: bool = False  # today's order reads the config file before env/keyring
    error_attr: Optional[str] = None  # existing ``*_error`` var for format warnings


SECRET_FIELDS: Dict[str, SecretField] = {
    spec.field: spec
    for spec in (
        SecretField(
            "plex_token",
            "Plex token",
            optional=False,
            scope="plex",
            credential_key="plex_token",
            getter="get_plex_token",
            config_attr=("plex", "token"),
            error_attr="plex_token_error",
        ),
        SecretField(
            "ai_api_key",
            "AI provider API key",
            optional=False,
            scope="ai",
            provider_keyed=True,
            error_attr="ai_api_key_error",
        ),
        SecretField(
            "ai_custom_api_key",
            "custom AI endpoint API key",
            optional=True,
            scope="ai",
            credential_key="custom_ai_api_key",
            getter="get_custom_ai_api_key",
            config_attr=("ai", "custom_api_key"),
            config_first=True,
        ),
        SecretField(
            "ai_local_auth_token",
            "local endpoint token",
            optional=True,
            scope="ai",
            config_attr=("ai", "local_auth_token"),
        ),
        SecretField(
            "embedding_api_key",
            "embedding provider API key",
            optional=False,
            scope="embedding",
            provider_keyed=True,
            error_attr="embedding_api_key_error",
        ),
        SecretField(
            "embedding_custom_api_key",
            "custom embedding endpoint API key",
            optional=True,
            scope="embedding",
            credential_key="custom_embedding_api_key",
            getter="get_custom_embedding_api_key",
            config_attr=("embedding", "custom_api_key"),
            config_first=True,
        ),
    )
}

# Credential-store key for each provider whose key the AI / embedding API-key
# field edits. Keyless providers (custom, local) are absent: their secrets have
# dedicated fields.
_AI_KEY_BY_PROVIDER: Dict[str, str] = {
    "gemini": "google_api_key",
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "claude": "anthropic_api_key",
    "cohere": "cohere_api_key",
}
_EMBEDDING_KEY_BY_PROVIDER: Dict[str, str] = {
    "gemini": "google_api_key",
    "openai": "openai_api_key",
    "cohere": "cohere_api_key",
}

# The existing ``store_*`` helper in ``plexmix.config.credentials`` per key.
_STORE_HELPERS: Dict[str, str] = {
    "plex_token": "store_plex_token",
    "google_api_key": "store_google_api_key",
    "openai_api_key": "store_openai_api_key",
    "anthropic_api_key": "store_anthropic_api_key",
    "cohere_api_key": "store_cohere_api_key",
    "custom_ai_api_key": "store_custom_ai_api_key",
    "custom_embedding_api_key": "store_custom_embedding_api_key",
}


def secret_input_id(field: str) -> str:
    """DOM id (and ``rx.set_value`` ref) of a secret input: ``secret-<field>``.

    Settings renders its sections once, in a single tabs tree, so each secret
    input exists at most once in the DOM and this id is unique.
    """
    return f"secret-{field}"


# validate_plex_token reports the typed length ("(got 19)"); a length is a
# fragment of the secret, so it is stripped before the message reaches state.
_LENGTH_HINT_RE = re.compile(r"\s*\(got \d+\)")


def _reset_secret_inputs(fields: Iterable[str]) -> List[Any]:
    """Client events that empty the rendered secret input of each given field."""
    return [rx.set_value(secret_input_id(field), "") for field in fields]


def _parse_number(value: Any) -> Optional[float]:
    """Parse a control value into a finite float, or None when it is not a number.

    Accepts a number, a numeric string (native ``<input type=range>`` sends its
    value as a string) or a one-element list/tuple (the Radix slider's shape).
    Booleans are rejected rather than read as 0/1.
    """
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _blank_secret_stored() -> Dict[str, bool]:
    return {f: False for f in SECRET_FIELDS}


def _blank_secret_text() -> Dict[str, str]:
    return {f: "" for f in SECRET_FIELDS}


def _redact(text: str, secrets: Iterable[Optional[str]]) -> str:
    """Replace every occurrence of a (non-trivial) secret in ``text``."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return text


def _config_secret(spec: SecretField, settings: Any) -> str:
    """The secret's value in the loaded config model ("" if absent)."""
    from plexmix.config.credentials import sanitize_credential_value

    if spec.config_attr is None or settings is None:
        return ""
    section, attr = spec.config_attr
    raw = getattr(getattr(settings, section, None), attr, None)
    return sanitize_credential_value(raw if isinstance(raw, str) else "") or ""


# Every secret-bearing key in config.yaml. The loaded ``Settings`` model is not the
# file as written: ``load_from_file`` overlays ``PLEX_TOKEN`` onto ``plex.token``,
# and pydantic-settings fills keys missing from the file from the environment
# (``AI_CUSTOM_API_KEY``, ``AI_LOCAL_AUTH_TOKEN``, ``EMBEDDING_CUSTOM_API_KEY``,
# ``PLEXMIX_UI_PASSWORD``). A save that round-tripped the model would copy those
# environment secrets into config.yaml, where they would then shadow the variable
# (rotating ``PLEXMIX_UI_PASSWORD`` would stop changing the password). Saves reset
# these keys to the file's own values before applying staged changes.
_CONFIG_SECRET_KEYS: Tuple[Tuple[str, str], ...] = (
    ("plex", "token"),
    ("ai", "custom_api_key"),
    ("ai", "local_auth_token"),
    ("embedding", "custom_api_key"),
    ("ui", "password"),
)


def _raw_config_data() -> Dict[str, Any]:
    """The config file as written, without env-var overlays ({} if absent or unreadable).

    Mirrors ``load_from_file``'s path resolution (override, data dir, legacy).
    """
    from pathlib import Path

    import yaml  # type: ignore[import-untyped]

    from plexmix.config import settings as cfg

    override = cfg._config_path_override
    path = Path(override or str(cfg._data_dir() / "config.yaml"))
    if not path.exists() and override is None:
        legacy = Path("~/.plexmix/config.yaml").expanduser()
        if legacy.exists() and str(legacy) != str(path):
            path = legacy
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error("Could not read the config file: %s", type(e).__name__)
        return {}
    return data if isinstance(data, dict) else {}


def _raw_config_value(
    section: str, key: str, data: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """One value from the config file as written (``data`` from ``_raw_config_data``)."""
    if data is None:
        data = _raw_config_data()
    section_data = data.get(section)
    value = section_data.get(key) if isinstance(section_data, dict) else None
    return str(value) if value else None


# ── One save model (webui-honest-status-and-first-run, design D12) ─────
#
# Settings has a single Save/Discard bar for every section. Unsaved changes are
# found by comparing a per-field snapshot of the saved form with the current one;
# ``_FIELD_SECTION`` names the section each snapshot key belongs to, so the bar can
# list which sections changed. Staged secrets take part through their action
# ("replace"/"clear"), never their value.

# Section id (``SETTINGS_SECTIONS``) -> the label its tab shows.
SECTION_LABELS: Dict[str, str] = {
    "plex": "Plex Server",
    "ai": "AI Provider",
    "embedding": "Embeddings",
    "audio": "Audio Analysis",
    "musicbrainz": "MusicBrainz",
    "advanced": "Advanced",
    "appearance": "Appearance",
}


def _secret_snapshot_key(field: str) -> str:
    """Snapshot key of a secret field's staged action (``secret:<field>``)."""
    return f"secret:{field}"


# Snapshot key -> section id. Every field Save writes is here; a test keeps it in
# step with ``SettingsState._get_settings_snapshot``.
_FIELD_SECTION: Dict[str, str] = {
    "plex_url": "plex",
    "plex_library": "plex",
    "ai_provider": "ai",
    "ai_model": "ai",
    "ai_temperature": "ai",
    "ai_local_mode": "ai",
    "ai_local_endpoint": "ai",
    "ai_custom_endpoint": "ai",
    "ai_custom_model": "ai",
    "embedding_provider": "embedding",
    "embedding_model": "embedding",
    "embedding_custom_endpoint": "embedding",
    "embedding_custom_model": "embedding",
    "embedding_custom_dimension": "embedding",
    "audio_enabled": "audio",
    "audio_analyze_on_sync": "audio",
    "audio_duration_limit": "audio",
    "musicbrainz_enabled": "musicbrainz",
    "musicbrainz_enrich_on_sync": "musicbrainz",
    "musicbrainz_confidence_threshold": "musicbrainz",
    "musicbrainz_contact_email": "musicbrainz",
    "log_level": "advanced",
    # A staged secret marks its section changed (scope ids are section ids).
    **{_secret_snapshot_key(field): spec.scope for field, spec in SECRET_FIELDS.items()},
}

# The embedding fields "Keep current provider" restores from the snapshot; a change
# to any of them that mismatches the search index needs confirmation on Save.
_EMBEDDING_FIELDS: Tuple[str, ...] = (
    "embedding_provider",
    "embedding_model",
    "embedding_custom_endpoint",
    "embedding_custom_model",
    "embedding_custom_dimension",
)

# DOM id of the client-owned "Use a model that isn't listed" field (also the
# ``rx.set_value`` target of every server write to it; webui-input-responsiveness D3).
AI_MODEL_TEXT_ID = "set-ai-model-custom"

# The Save/Discard bar unmounts when the form is clean again, taking the focused
# button with it: put focus back on the open section (only if it was lost).
FOCUS_AFTER_SAVEBAR_JS = (
    "window.pmFocus&&pmFocus.when(['.settings-panel[data-state=\"active\"]','#main-heading'])"
)

# Save results, written to the persistent ``set-save-status`` region (the ✓/✗
# prefix picks the status style, as for the connection tests).
SAVE_OK_STATUS = "✓ Settings saved."
SAVE_FAILED_PREFIX = "✗ Failed to save settings: "


def join_names(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    items = [n for n in names if n]
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _is_unlisted(model: str, models: Sequence[str]) -> bool:
    """True when a provider has a curated list and ``model`` is set but not in it."""
    return bool(model) and bool(models) and model not in models


def embedding_mismatch_warning(
    index_dimension: int, index_tracks: int, dimension: int, provider: str
) -> str:
    """Warning for an embedding choice that doesn't fit the existing search index.

    "" when there is no index (``index_dimension`` 0), the dimensions match, or the
    new dimension is unknown. Names both dimensions, the number of embedded tracks
    (when known) and the provider, and says what to do about it.
    """
    if index_dimension <= 0 or dimension <= 0 or index_dimension == dimension:
        return ""
    from plexmix.services.registry import embedding_provider_display_name

    tracks = f" of {pluralize(index_tracks, 'track')}" if index_tracks > 0 else ""
    if (provider or "").strip().lower() == "custom":
        new = f"{format_count(dimension)}-dimension embeddings from your custom endpoint"
    else:
        label = embedding_provider_display_name(provider) or provider
        new = f"{format_count(dimension)}-dimension {label} embeddings"
    return (
        f"Your {format_count(index_dimension)}-dimension search index{tracks} won't work "
        f"with {new}. Search stops working until you rebuild embeddings or switch back."
    )


class SettingsState(AppState):
    plex_url: str = ""
    plex_username: str = ""
    plex_library: str = ""
    plex_libraries: List[str] = []

    ai_provider: str = "gemini"
    ai_model: str = ""
    ai_temperature: float = 0.7
    ai_models: List[str] = []
    ai_local_mode: str = "builtin"
    ai_local_endpoint: str = ""
    ai_custom_endpoint: str = ""
    ai_custom_model: str = ""
    is_downloading_local_llm: bool = False
    local_llm_download_status: str = ""
    local_llm_download_progress: int = 0
    discovered_models: List[str] = []
    is_discovering_models: bool = False

    embedding_provider: str = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 3072
    embedding_models: List[str] = []
    embedding_custom_endpoint: str = ""
    embedding_custom_model: str = ""
    embedding_custom_dimension: int = 1536
    is_downloading_local_model: bool = False
    local_download_status: str = ""
    local_download_progress: int = 0

    db_path: str = ""
    faiss_index_path: str = ""
    sync_batch_size: int = 100
    embedding_batch_size: int = 50
    log_level: str = "INFO"

    audio_enabled: bool = False
    audio_analyze_on_sync: bool = False
    audio_duration_limit: int = 60

    musicbrainz_enabled: bool = False
    musicbrainz_enrich_on_sync: bool = False
    musicbrainz_confidence_threshold: float = 80.0
    musicbrainz_contact_email: str = ""

    testing_connection: bool = False
    plex_test_status: str = ""
    ai_test_status: str = ""
    embedding_test_status: str = ""
    # Result of the last Save ("✓ …" / "✗ …"), shown in the persistent
    # ``set-save-status`` region through ``save_status_message``.
    save_status: str = ""
    active_tab: str = "plex"
    # The saved form, per field (keys and sections in ``_FIELD_SECTION``); {} until
    # the first load. Taken at the end of on_load, after update_model_lists.
    _settings_snapshot: Dict[str, Any] = {}
    # on_load found edits from an earlier visit in this session and kept them.
    unsaved_from_earlier: bool = False
    # The existing search index (read on load): its dimension (0 = no index) and
    # how many tracks it holds.
    index_dimension: int = 0
    index_track_count: int = 0
    # The "this embedding change breaks your search index" confirmation.
    embedding_confirm_open: bool = False

    # Value-free secret status, keyed by SECRET_FIELDS id. These are the only
    # secret-related vars the browser sees; values stay on the server.
    secret_stored: Dict[str, bool] = _blank_secret_stored()
    # "env:<VAR>", "keyring", "config" or "" (where the stored value resolves from)
    secret_source: Dict[str, str] = _blank_secret_text()
    # "replace", "clear" or "" (what Save will do for this session)
    secret_staged: Dict[str, str] = _blank_secret_text()

    # Local dependency availability (sentence-transformers / PyTorch)
    local_deps_available: bool = False

    # Validation errors
    plex_url_error: str = ""
    plex_token_error: str = ""
    ai_api_key_error: str = ""
    embedding_api_key_error: str = ""
    temperature_error: str = ""
    batch_size_error: str = ""
    local_endpoint_error: str = ""

    def set_active_tab(self, tab: str) -> Any:
        """Select a Settings section and rewrite the address bar to its deep link.

        The address is updated in place with ``history.replaceState`` (never
        ``rx.redirect``): a navigation would re-run ``on_load``, which reloads the
        form from disk and would drop unsaved edits in every section. Unknown ids
        are ignored.
        """
        if tab not in SETTINGS_SECTIONS:
            return None
        self.active_tab = tab
        return rx.call_script(settings_section_url_script(tab))

    def _section_query_param(self) -> Optional[str]:
        """The ``?section=`` value of the current page URL, or None."""
        try:
            value = self.router.url.query_parameters.get("section")
        except Exception:
            return None
        if isinstance(value, (list, tuple)):  # a repeated ?section=a&section=b
            value = value[0] if value else None
        return value if isinstance(value, str) else None

    @rx.event
    def on_load(self) -> Any:
        # Deep link (/settings?section=<id>) first, before anything loads: unknown
        # or missing ids keep the current section. It is a validated UI string, so
        # applying it before the auth check is harmless and survives a login.
        self.active_tab = resolve_settings_section(self._section_query_param(), self.active_tab)
        if not self.check_auth():
            self.is_page_loading = False
            return None
        self.load_app_data()
        self._detect_local_deps()
        model_text = self._ai_model_text()
        self.save_status = ""
        self.embedding_confirm_open = False
        events: List[Any] = []
        if self._has_unsaved_edits():
            # Back on Settings with edits from earlier in this session: keep them
            # (and the snapshot of the saved values) rather than reloading the file
            # over them. The bar says "from earlier" and offers Discard. Staged
            # secrets are kept too; the input of any draft that is gone (reaped
            # while the tab was disconnected) is emptied to match.
            self.unsaved_from_earlier = True
            events.extend(self._resync_secret_drafts())
            self._read_index_facts(self._load_settings_for_secrets())
            self.update_model_lists()
        else:
            self.unsaved_from_earlier = False
            # Also runs when a backgrounded tab reconnects, while its secret inputs
            # are still mounted: the returned events empty them to match the
            # dropped drafts.
            events.extend(self.load_settings() or [])
            self.update_model_lists()
            # Snapshot only now: update_model_lists fills an empty model, and a
            # snapshot taken before it read that fill as an unsaved change.
            self._take_snapshot()
        events.extend(self._ai_model_text_events(model_text))
        self.is_page_loading = False
        return events

    def _detect_local_deps(self) -> None:
        """Check if local AI dependencies (sentence-transformers/PyTorch) are installed.

        Asks the package finder and never imports the package: importing it here loaded
        torch into the server process, which blocked the event loop for 10 s or more and put
        torch's OpenMP runtime next to FAISS's (a crash on macOS). Models run in worker
        processes (``plexmix.utils.embeddings``).
        """
        from plexmix.utils.embeddings import local_embedding_deps_available

        self.local_deps_available = local_embedding_deps_available()

    def load_settings(self) -> Any:
        """Reload the form from the config file and refresh secret status.

        Returns client events that empty every secret input: the inputs are
        uncontrolled, so text typed before this reload (for example in a tab that
        reconnected after being backgrounded) would otherwise stay visible while
        its draft is gone, and Save would silently ignore it.
        """
        # Server-side auth gate: Reflex exposes public methods as events, so an
        # unauthenticated client could dispatch load_settings directly. Secret
        # values are never loaded into state, but configuration and stored-status
        # details are still withheld from unauthenticated clients.
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated load_settings request")
            return None
        # A fresh load is a fresh form: the secret inputs are emptied (returned
        # events below), so any replacement staged earlier in this session is
        # dropped too.
        self._discard_secret_drafts()
        try:
            from plexmix.config.settings import Settings

            settings = Settings.load_from_file()

            self.plex_url = settings.plex.url or ""
            self.plex_library = settings.plex.library_name or ""

            # If we have a configured library name, add it to the list so it shows in dropdown
            if self.plex_library:
                self.plex_libraries = [self.plex_library]

            self.ai_provider = settings.ai.default_provider
            self.ai_model = settings.ai.model or ""
            self.ai_temperature = settings.ai.temperature
            self.ai_local_mode = settings.ai.local_mode
            self.ai_local_endpoint = settings.ai.local_endpoint or ""
            self.ai_custom_endpoint = settings.ai.custom_endpoint or ""
            self.ai_custom_model = settings.ai.custom_model or ""
            self.local_llm_download_status = ""
            self.local_llm_download_progress = 0
            self.is_downloading_local_llm = False

            self.embedding_provider = settings.embedding.default_provider
            self.embedding_model = settings.embedding.model
            self.embedding_dimension = settings.embedding.dimension
            self.embedding_custom_endpoint = settings.embedding.custom_endpoint or ""
            self.embedding_custom_model = settings.embedding.custom_model or ""
            self.embedding_custom_dimension = settings.embedding.custom_dimension

            # Only where each secret resolves from; never the values.
            self._refresh_secret_status(settings)

            self.db_path = str(settings.database.get_db_path())
            self.faiss_index_path = str(settings.database.get_index_path())
            self.log_level = settings.logging.level

            self.audio_enabled = settings.audio.enabled
            self.audio_analyze_on_sync = settings.audio.analyze_on_sync
            self.audio_duration_limit = settings.audio.duration_limit

            self.musicbrainz_enabled = settings.musicbrainz.enabled
            self.musicbrainz_enrich_on_sync = settings.musicbrainz.enrich_on_sync
            self.musicbrainz_confidence_threshold = settings.musicbrainz.confidence_threshold
            self.musicbrainz_contact_email = settings.musicbrainz.contact_email

            self._read_index_facts(settings)

        except Exception as e:
            # Never the raw text: a pydantic ValidationError echoes the rejected
            # input, which can be a stored secret from a hand-edited config.yaml.
            logger.error("Error loading settings: %s", safe_error_text(e))
        finally:
            self._sync_embedding_dimension()
            # Ensure paths show defaults when config hasn't set them
            if not self.db_path:
                from plexmix.config.settings import _data_dir

                self.db_path = str(_data_dir() / "plexmix.db")
            if not self.faiss_index_path:
                from plexmix.config.settings import _data_dir

                self.faiss_index_path = str(_data_dir() / "embeddings.index")
            # on_load and discard_changes snapshot again after update_model_lists;
            # this one serves direct callers.
            self._take_snapshot()
        return _reset_secret_inputs(SECRET_FIELDS)

    def _read_index_facts(self, settings: Any) -> None:
        """Read the existing search index's dimension and track count (no FAISS import).

        ``readiness.read_index_metadata`` never raises; with no index both are 0.
        Old metadata without track ids falls back to the embedded-track count.
        """
        if settings is None:
            return
        from plexmix.services.readiness import read_index_metadata

        meta = read_index_metadata(settings)
        self.index_dimension = int(meta.get("dimension", 0) or 0)
        count = int(meta.get("vector_count", 0) or 0)
        if not count and self.index_dimension:
            count = int(self.stat_embedded or 0)
        self.index_track_count = count if self.index_dimension else 0

    def _resync_secret_drafts(self) -> List[Any]:
        """Mirror the drafts that still exist; empty the input of each one that is gone."""
        before = dict(self.secret_staged)
        self._refresh_secret_staged()
        gone = [f for f in SECRET_FIELDS if before.get(f) and not self.secret_staged.get(f)]
        for field in gone:
            self._set_secret_error(field, "")
        return _reset_secret_inputs(gone)

    def update_model_lists(self):
        from plexmix.services.registry import (
            get_ai_models_display,
            get_default_ai_model_display,
            get_embedding_models,
        )

        models = get_ai_models_display(self.ai_provider)
        self.ai_models = models
        # Only auto-select if model is empty (preserve custom model names)
        if models and not self.ai_model:
            self.ai_model = get_default_ai_model_display(self.ai_provider) or models[0]

        models = get_embedding_models(self.embedding_provider)
        self.embedding_models = models
        # Only auto-select if model is empty (preserve custom model names)
        if models and not self.embedding_model:
            self.embedding_model = models[0]
        self._sync_embedding_dimension()

    # ── Secret handling (values stay on the server) ────────────────────

    def _client_token(self) -> str:
        """This session's Reflex client token ("" when unavailable)."""
        try:
            return str(self.router.session.client_token or "")
        except Exception:
            return ""

    def _credential_key(self, field: str) -> Optional[str]:
        """Credential-store key that ``field`` edits for the selected provider."""
        spec = SECRET_FIELDS[field]
        if not spec.provider_keyed:
            return spec.credential_key
        if spec.scope == "ai":
            return _AI_KEY_BY_PROVIDER.get(self.ai_provider)
        return _EMBEDDING_KEY_BY_PROVIDER.get(self.embedding_provider)

    def _load_settings_for_secrets(self) -> Any:
        """Load the config file for secret resolution (``None`` on failure)."""
        try:
            from plexmix.config.settings import Settings

            return Settings.load_from_file()
        except Exception as e:
            logger.error("Could not load settings for secret status: %s", type(e).__name__)
            return None

    def _secret_source_for(self, field: str, settings: Any) -> str:
        """Where ``field``'s stored value resolves from, in today's lookup order."""
        from plexmix.config import credentials

        spec = SECRET_FIELDS[field]
        config_value = _config_secret(spec, settings)
        if spec.config_first and config_value:
            return "config"
        key = self._credential_key(field)
        if key is not None:
            source = credentials.get_credential_source(key)
            if source:
                return source
        return "config" if config_value else ""

    def _stored_secret(self, field: str, settings: Any) -> str:
        """The stored value of ``field`` (server-side only; never put it in state)."""
        from plexmix.config import credentials

        spec = SECRET_FIELDS[field]
        config_value = _config_secret(spec, settings)
        if spec.config_first and config_value:
            return config_value
        value: Optional[str] = None
        if spec.provider_keyed:
            if self._credential_key(field) is not None:
                from plexmix.services.providers import (
                    resolve_ai_api_key,
                    resolve_embedding_api_key,
                )

                if spec.scope == "ai":
                    value = resolve_ai_api_key(self.ai_provider)
                else:
                    value = resolve_embedding_api_key(self.embedding_provider)
        elif spec.getter is not None:
            value = getattr(credentials, spec.getter)()
        return credentials.sanitize_credential_value(value or config_value or "") or ""

    def _resolve_secret(self, field: str) -> str:
        """Secret to use for a test or discovery: the staged draft, else the stored value.

        Resolved on the server only, and only for an authorized session; an
        unauthenticated client must never make the server send a stored secret to
        an endpoint it controls.
        """
        if field not in SECRET_FIELDS or not self.access_allowed():
            return ""
        token = self._client_token()
        if token:
            action = secret_drafts.action(token, field)
            if action == ACTION_REPLACE:
                return secret_drafts.get(token, field) or ""
            if action == ACTION_CLEAR:
                return ""
        settings = self._load_settings_for_secrets() if SECRET_FIELDS[field].config_attr else None
        return self._stored_secret(field, settings)

    def _refresh_secret_staged(self) -> None:
        """Mirror this session's staged actions (never values) into ``secret_staged``."""
        token = self._client_token()
        staged = secret_drafts.staged_fields(token) if token else {}
        self.secret_staged = {f: staged.get(f, "") for f in SECRET_FIELDS}

    def _refresh_secret_status(
        self, settings: Any = None, fields: Optional[Iterable[str]] = None
    ) -> None:
        """Recompute ``secret_stored`` / ``secret_source`` / ``secret_staged``.

        Unauthorized sessions get blank status: no stored-status details at all.
        """
        if not self.access_allowed():
            self.secret_stored = _blank_secret_stored()
            self.secret_source = _blank_secret_text()
            self.secret_staged = _blank_secret_text()
            return
        targets = list(fields) if fields is not None else list(SECRET_FIELDS)
        if settings is None and any(SECRET_FIELDS[f].config_attr for f in targets):
            settings = self._load_settings_for_secrets()
        stored = dict(self.secret_stored)
        source = dict(self.secret_source)
        for field in targets:
            try:
                where = self._secret_source_for(field, settings)
            except Exception as e:
                logger.error("Could not resolve where %s is stored: %s", field, type(e).__name__)
                where = ""
            source[field] = where
            stored[field] = bool(where)
        self.secret_stored = stored
        self.secret_source = source
        self._refresh_secret_staged()

    def _set_secret_error(self, field: str, message: str) -> None:
        attr = SECRET_FIELDS[field].error_attr
        if attr is not None:
            setattr(self, attr, message)

    def _secret_format_error(self, field: str, value: str) -> str:
        """Advisory format warning for a staged value; never echoes any of it."""
        error: Optional[str] = None
        if field == "plex_token":
            _, error = validate_plex_token(value)
        elif field == "ai_api_key":
            provider = "claude" if self.ai_provider in ("anthropic", "claude") else self.ai_provider
            _, error = validate_api_key(value, provider)
        elif field == "embedding_api_key":
            _, error = validate_api_key(value, self.embedding_provider)
        return _LENGTH_HINT_RE.sub("", error or "")

    def _discard_secret_drafts(self, fields: Optional[Iterable[str]] = None) -> None:
        """Drop this session's staged secrets (all, or just ``fields``)."""
        token = self._client_token()
        targets = list(fields) if fields is not None else list(SECRET_FIELDS)
        if token:
            if fields is None:
                secret_drafts.discard(token)
            else:
                for field in targets:
                    secret_drafts.discard(token, field)
        for field in targets:
            self._set_secret_error(field, "")
        self._refresh_secret_staged()

    def stage_secret(self, field: str, value: str) -> None:
        """Stage a typed replacement for a secret (sent once, on blur).

        The value is held server-side in ``secret_drafts``, never in state. An
        empty value drops a staged replacement, so leaving the field empty keeps
        the stored secret (a staged Clear is kept: the input is empty then too).
        """
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated stage_secret request")
            return
        if field not in SECRET_FIELDS:
            logger.warning("Rejected stage_secret for an unknown field")
            return
        token = self._client_token()
        if not token:
            logger.warning("Rejected stage_secret without a client token")
            return
        from plexmix.config.credentials import sanitize_credential_value

        cleaned = sanitize_credential_value(value if isinstance(value, str) else "") or ""
        # A provider-keyed field has nowhere to be saved while a keyless provider
        # (custom, local) is selected.
        storable = (
            self._credential_key(field) is not None or SECRET_FIELDS[field].config_attr is not None
        )
        if cleaned and storable:
            secret_drafts.stage(token, field, cleaned)
            self._set_secret_error(field, self._secret_format_error(field, cleaned))
        else:
            if secret_drafts.action(token, field) == ACTION_REPLACE:
                secret_drafts.discard(token, field)
            self._set_secret_error(field, "")
        self._refresh_secret_staged()

    def clear_secret(self, field: str) -> Any:
        """Stage removal of an optional secret (applied on Save)."""
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated clear_secret request")
            return None
        spec = SECRET_FIELDS.get(field)
        if spec is None or not spec.optional:
            logger.warning("Rejected clear_secret for a field that cannot be cleared")
            return None
        token = self._client_token()
        if not token:
            logger.warning("Rejected clear_secret without a client token")
            return None
        secret_drafts.mark_clear(token, field)
        self._set_secret_error(field, "")
        self._refresh_secret_staged()
        # Also empty the input in case a replacement had been typed into it.
        return _reset_secret_inputs([field])

    def discard_secret(self, field: str) -> Any:
        """Drop a staged replacement or clear for ``field`` and empty its input."""
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated discard_secret request")
            return None
        if field not in SECRET_FIELDS:
            logger.warning("Rejected discard_secret for an unknown field")
            return None
        self._discard_secret_drafts([field])
        return _reset_secret_inputs([field])

    def set_ai_provider(self, provider: str) -> Any:
        from plexmix.services.registry import get_default_ai_model_display

        model_text = self._ai_model_text()
        self.ai_provider = provider
        self.update_model_lists()
        if self.ai_models:
            self.ai_model = get_default_ai_model_display(provider) or self.ai_models[0]
        if provider == "local" and not self.ai_model:
            self.ai_model = LOCAL_LLM_DEFAULT_MODEL
        if provider != "local":
            self.ai_local_mode = "builtin"
            self.ai_local_endpoint = ""
            self.local_llm_download_status = ""
            self.local_llm_download_progress = 0
        if provider != "custom":
            self.ai_custom_endpoint = ""
            self.ai_custom_model = ""
        # A key typed for one provider must never be saved as another's, and the
        # stored custom key / local token are no longer blanked (untouched secrets
        # are not written on save).
        discarded = [f for f, s in SECRET_FIELDS.items() if s.scope == "ai"]
        self._discard_secret_drafts(discarded)
        self._refresh_secret_status(fields=["ai_api_key"])
        # The inputs are uncontrolled and stay mounted across cloud providers, so
        # empty them too: typed text left in the field would otherwise be staged
        # (and saved) for the new provider on its next blur. The client-owned
        # "model that isn't listed" field follows the newly selected model.
        return _reset_secret_inputs(discarded) + self._ai_model_text_events(model_text)

    def set_embedding_provider(self, provider: str) -> Any:
        from plexmix.services.registry import get_default_embedding_model

        self.embedding_provider = provider
        self.update_model_lists()
        if self.embedding_models:
            # Prefer the provider's declared default model over the alphabetically
            # first one, so the auto-selected model and the recorded dimension stay
            # consistent (e.g. OpenAI defaults to the 1536-dim small model, not the
            # 3072-dim large model that sorts first).
            self.embedding_model = get_default_embedding_model(provider) or self.embedding_models[0]
        if provider != "local":
            self.is_downloading_local_model = False
            self.local_download_status = ""
            self.local_download_progress = 0
        if provider != "custom":
            self.embedding_custom_endpoint = ""
            self.embedding_custom_model = ""
            self.embedding_custom_dimension = 1536
        # Same rules as set_ai_provider: drop drafts typed for the old provider
        # (and empty their inputs), keep the stored custom key, refresh only the
        # provider-keyed status.
        discarded = [f for f, s in SECRET_FIELDS.items() if s.scope == "embedding"]
        self._discard_secret_drafts(discarded)
        self._refresh_secret_status(fields=["embedding_api_key"])
        self._sync_embedding_dimension()
        return _reset_secret_inputs(discarded)

    def set_plex_url(self, url: str):
        self.plex_url = url

    def set_plex_library(self, library: str):
        self.plex_library = library

    def set_plex_username(self, username: str):
        self.plex_username = username

    def set_ai_model(self, model: str) -> List[Any]:
        """Select a model in the AI model select (curated list or the unlisted one)."""
        model_text = self._ai_model_text()
        self.ai_model = model
        return self._ai_model_text_events(model_text)

    def set_ai_model_text(self, model: str) -> None:
        """Commit of the client-owned "Use a model that isn't listed" field.

        Idempotent (a debounced commit and the blur commit can both arrive), and
        never echoes the text back. An empty field keeps the current model: pick
        one from the list to change it.
        """
        text = model.strip() if isinstance(model, str) else ""
        if not text or text == self.ai_model:
            return
        self.ai_model = text

    def _ai_model_text(self) -> str:
        """What the "model that isn't listed" field shows: the model, if it is unlisted."""
        return self.ai_model if _is_unlisted(self.ai_model, self.ai_models) else ""

    def _ai_model_text_events(self, before: str) -> List[Any]:
        """``rx.set_value`` for the client-owned model field when its text changed.

        Every server write to ``ai_model`` other than the field's own commit goes
        through this (webui-input-responsiveness D3). Nothing is sent when the text
        is unchanged, so text the user is still typing is never overwritten.
        """
        after = self._ai_model_text()
        return [] if after == before else [rx.set_value(AI_MODEL_TEXT_ID, after)]

    def set_ai_local_mode(self, mode: str):
        self.ai_local_mode = mode
        if mode != "endpoint":
            self.local_endpoint_error = ""

    def set_ai_local_endpoint(self, endpoint: str):
        self.ai_local_endpoint = endpoint

    def set_ai_custom_endpoint(self, endpoint: str):
        self.ai_custom_endpoint = endpoint

    def set_ai_custom_model(self, model: str):
        self.ai_custom_model = model

    @rx.event(background=True)
    async def discover_models(self) -> Any:
        """Auto-discover models from the configured custom or local endpoint."""
        async with self:
            # The endpoint is client-supplied and the key is resolved on the
            # server, so only an authorized session may run discovery.
            if not self.access_allowed():
                logger.warning("Blocked unauthenticated discover_models request")
                return
            endpoint = ""
            api_key = ""
            if self.ai_provider == "custom":
                endpoint = self.ai_custom_endpoint
                api_key = self._resolve_secret("ai_custom_api_key")
            elif self.ai_provider == "local" and self.ai_local_mode == "endpoint":
                endpoint = self.ai_local_endpoint
                api_key = self._resolve_secret("ai_local_auth_token")
            else:
                return

            if not endpoint:
                return

            self.is_discovering_models = True
            self.discovered_models = []

        try:
            from plexmix.services.providers import discover_endpoint_models

            loop = asyncio.get_running_loop()
            models = await loop.run_in_executor(
                None, lambda: discover_endpoint_models(endpoint, api_key)
            )

            async with self:
                self.discovered_models = models
                self.is_discovering_models = False

            if models:
                yield rx.toast.success(f"Found {len(models)} model(s)")
            else:
                yield rx.toast.warning("No models found at this endpoint")

        except Exception as e:
            async with self:
                self.is_discovering_models = False
            yield rx.toast.error(f"Discovery failed: {_redact(str(e), [api_key])}")

    def select_discovered_model(self, model: str) -> List[Any]:
        """Apply a discovered model to the appropriate field."""
        model_text = self._ai_model_text()
        if self.ai_provider == "custom":
            self.ai_custom_model = model
        elif self.ai_provider == "local":
            self.ai_model = model
        return self._ai_model_text_events(model_text)

    def set_ai_temperature(self, temperature: float | str) -> None:
        """Set the temperature from the native range (a string) or a number, clamped to 0–1.

        A value that is not a number leaves the temperature unchanged.
        """
        number = _parse_number(temperature)
        if number is None:
            return
        self.ai_temperature = max(0.0, min(1.0, number))

    def set_embedding_model(self, model: str):
        self.embedding_model = model
        self._sync_embedding_dimension()

    def set_embedding_custom_endpoint(self, endpoint: str):
        self.embedding_custom_endpoint = endpoint

    def set_embedding_custom_model(self, model: str):
        self.embedding_custom_model = model

    def set_embedding_custom_dimension(self, value: str):
        try:
            v = int(value)
            self.embedding_custom_dimension = max(1, v)
        except (ValueError, TypeError):
            self.embedding_custom_dimension = 1536
        self._sync_embedding_dimension()

    def set_log_level(self, level: str):
        self.log_level = level

    def set_audio_enabled(self, enabled: bool):
        self.audio_enabled = enabled

    def set_audio_analyze_on_sync(self, enabled: bool):
        self.audio_analyze_on_sync = enabled

    def set_audio_duration_limit(self, value: str):
        try:
            v = int(value)
            self.audio_duration_limit = max(0, min(300, v))
        except (ValueError, TypeError):
            self.audio_duration_limit = 60

    def set_musicbrainz_enabled(self, enabled: bool):
        self.musicbrainz_enabled = enabled

    def set_musicbrainz_enrich_on_sync(self, enabled: bool):
        self.musicbrainz_enrich_on_sync = enabled

    def set_musicbrainz_confidence_threshold(self, value: float | str | list) -> None:
        """Set the match threshold, clamped to 0–100.

        Accepts the native range's string value, a number, or a one-element list
        (the old Radix slider shape). A value that is not a number is ignored.
        """
        number = _parse_number(value)
        if number is None:
            return
        self.musicbrainz_confidence_threshold = max(0.0, min(100.0, number))

    def set_musicbrainz_contact_email(self, email: str):
        self.musicbrainz_contact_email = email

    @rx.event(background=True)
    async def test_plex_connection(self) -> None:
        # The URL is client-supplied and the token is resolved on the server, so
        # an unauthenticated client must not be able to aim a test at its own host.
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated test_plex_connection request")
            return
        from ._settings_testing import test_plex_connection_impl

        await test_plex_connection_impl(self)

    @rx.event(background=True)
    async def test_ai_provider(self) -> None:
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated test_ai_provider request")
            return
        from ._settings_testing import test_ai_provider_impl

        await test_ai_provider_impl(self)

    @rx.event(background=True)
    async def test_embedding_provider(self) -> None:
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated test_embedding_provider request")
            return
        from ._settings_testing import test_embedding_provider_impl

        await test_embedding_provider_impl(self)

    def _write_staged_secrets(self, settings: Any, token: str, staged: Dict[str, str]) -> List[str]:
        """Apply this session's staged secrets to the credential store / config model.

        Only fields with a staged action are touched:

        - ``replace`` stores through the existing ``store_*`` helper (keyring
          first). The plaintext config fallback is kept only where it exists
          today (Plex token, custom keys; the local endpoint token is config-only).
        - ``clear`` (optional fields) deletes the keyring entry and the config value.

        Returns user-facing warnings (never containing a value).
        """
        from plexmix.config import credentials

        warnings: List[str] = []
        for field, spec in SECRET_FIELDS.items():
            action = staged.get(field, "")
            if not action:
                continue
            key = self._credential_key(field)
            section = getattr(settings, spec.config_attr[0]) if spec.config_attr else None
            if action == ACTION_REPLACE:
                value = secret_drafts.get(token, field)
                if not value:
                    continue
                stored = bool(getattr(credentials, _STORE_HELPERS[key])(value)) if key else False
                if spec.config_attr is not None:
                    # Keyring copy wins; the config copy is dropped so it can't shadow it.
                    setattr(section, spec.config_attr[1], None if stored else value)
                elif not stored:
                    warnings.append(
                        f"Could not store the {spec.label}: the system keyring is "
                        "unavailable. Set it with an environment variable instead."
                    )
            elif action == ACTION_CLEAR and spec.optional:
                if key is not None:
                    credentials.delete_credential(key)
                if spec.config_attr is not None:
                    setattr(section, spec.config_attr[1], None)
        return warnings

    def save_all_settings(self) -> Any:
        """The bar's Save: commit every section.

        When the pending embedding change doesn't fit the existing search index
        (and differs from the saved values), nothing is written yet: the
        confirmation opens instead ("Save and rebuild later" saves through
        :meth:`confirm_embedding_change`, "Keep current provider" reverts through
        :meth:`revert_embedding_change`).
        """
        # Server-side auth gate: an unauthenticated client must not be able to
        # overwrite configuration by dispatching this event directly.
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated save_all_settings request")
            return rx.toast.error("Not authenticated.")
        if self._embedding_change_needs_confirm():
            self.embedding_confirm_open = True
            return None
        return self._save_settings()

    def confirm_embedding_change(self) -> Any:
        """ "Save and rebuild later": close the confirmation and save everything.

        The search index is left as it is; readiness then reports that the
        embeddings must be rebuilt (Dashboard, Generator, Doctor).
        """
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated confirm_embedding_change request")
            return rx.toast.error("Not authenticated.")
        self.embedding_confirm_open = False
        return self._save_settings()

    def revert_embedding_change(self) -> Any:
        """ "Keep current provider": restore the saved embedding fields; save nothing.

        Other unsaved changes stay pending. When the provider goes back, drafts
        typed for the other provider's key are dropped (and their inputs emptied),
        as on any provider switch.
        """
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated revert_embedding_change request")
            return None
        self.embedding_confirm_open = False
        saved = self._settings_snapshot
        if not saved:
            return None
        from plexmix.services.registry import get_embedding_models

        provider_changed = saved.get("embedding_provider") != self.embedding_provider
        self.embedding_provider = str(saved.get("embedding_provider") or "gemini")
        self.embedding_model = str(saved.get("embedding_model") or "")
        self.embedding_custom_endpoint = str(saved.get("embedding_custom_endpoint") or "")
        self.embedding_custom_model = str(saved.get("embedding_custom_model") or "")
        try:
            self.embedding_custom_dimension = int(saved.get("embedding_custom_dimension") or 1536)
        except (TypeError, ValueError):
            self.embedding_custom_dimension = 1536
        self.embedding_models = get_embedding_models(self.embedding_provider)
        self._sync_embedding_dimension()
        if not provider_changed:
            return None
        if self.embedding_provider != "local":
            self.is_downloading_local_model = False
            self.local_download_status = ""
            self.local_download_progress = 0
        discarded = [f for f, s in SECRET_FIELDS.items() if s.scope == "embedding"]
        self._discard_secret_drafts(discarded)
        self._refresh_secret_status(fields=["embedding_api_key"])
        return _reset_secret_inputs(discarded)

    def set_embedding_confirm_open(self, is_open: bool) -> None:
        """The confirmation's ``on_open_change`` (Escape, Cancel): close it, keep the edits.

        Idempotent; it never opens the dialog (only :meth:`save_all_settings` does).
        """
        if not is_open:
            self.embedding_confirm_open = False

    def discard_changes(self) -> Any:
        """The bar's Discard: restore every field to its saved value.

        Reloads the form from the config file and snapshots it again. Staged
        secrets live outside the form, so each one is discarded through
        :meth:`discard_secret` (dropping the server-side draft and emptying its
        uncontrolled input); every other secret input is emptied too.
        """
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated discard_changes request")
            return None
        token = self._client_token()
        staged = secret_drafts.staged_fields(token) if token else {}
        events: List[Any] = []
        discarded: List[str] = []
        for field in SECRET_FIELDS:
            if staged.get(field) or self.secret_staged.get(field):
                events.extend(self.discard_secret(field) or [])
                discarded.append(field)
        self.load_settings()
        events.extend(_reset_secret_inputs([f for f in SECRET_FIELDS if f not in discarded]))
        self.update_model_lists()
        self._take_snapshot()
        self.unsaved_from_earlier = False
        self.embedding_confirm_open = False
        self.save_status = ""
        # Format warnings of the discarded edits (secret ones went with their drafts).
        self.plex_url_error = ""
        self.temperature_error = ""
        self.batch_size_error = ""
        self.local_endpoint_error = ""
        # The bar is gone: the client-owned model field shows the saved model, and
        # focus (on the removed Discard button) returns to the open section.
        events.append(rx.set_value(AI_MODEL_TEXT_ID, self._ai_model_text()))
        events.append(rx.call_script(FOCUS_AFTER_SAVEBAR_JS))
        return events

    def _embedding_change_needs_confirm(self) -> bool:
        """True when Save would commit an embedding change that mismatches the index."""
        saved = self._settings_snapshot
        if not saved or not self._embedding_warning():
            return False
        return any(saved.get(field) != getattr(self, field) for field in _EMBEDDING_FIELDS)

    def _save_settings(self) -> Any:
        """Write every section to the config file (and staged secrets to their store)."""
        if not self.access_allowed():
            logger.warning("Blocked unauthenticated settings save")
            return rx.toast.error("Not authenticated.")
        token = self._client_token()
        staged = secret_drafts.staged_fields(token) if token else {}
        # Only for redacting error messages; never stored or returned.
        staged_values = [secret_drafts.get(token, field) for field in staged]
        try:
            from plexmix.config.settings import Settings

            settings = Settings.load_from_file()

            settings.plex.url = self.plex_url
            settings.plex.library_name = self.plex_library
            # The loaded model carries environment secrets (PLEX_TOKEN overlaid onto
            # plex.token; AI_*/EMBEDDING_*/PLEXMIX_UI_* filled into keys missing from
            # the file). Keep each secret key's own file value so no environment
            # secret is ever copied into config.yaml; staged changes below may still
            # replace or clear one.
            raw_config = _raw_config_data()
            for section, key in _CONFIG_SECRET_KEYS:
                setattr(
                    getattr(settings, section), key, _raw_config_value(section, key, raw_config)
                )

            settings.ai.default_provider = self.ai_provider
            settings.ai.model = self.ai_model
            settings.ai.temperature = self.ai_temperature
            settings.ai.local_mode = self.ai_local_mode
            settings.ai.local_endpoint = self.ai_local_endpoint or None
            settings.ai.custom_endpoint = self.ai_custom_endpoint or None
            settings.ai.custom_model = self.ai_custom_model or None

            settings.embedding.default_provider = self.embedding_provider
            settings.embedding.model = self.embedding_model
            settings.embedding.dimension = self.embedding_dimension
            settings.embedding.custom_endpoint = self.embedding_custom_endpoint or None
            settings.embedding.custom_model = self.embedding_custom_model or None
            settings.embedding.custom_dimension = self.embedding_custom_dimension

            # Secrets are written only when this session staged a replacement or a
            # clear since the last save; untouched secrets are left exactly as stored.
            warnings = self._write_staged_secrets(settings, token, staged)

            settings.logging.level = self.log_level

            settings.audio.enabled = self.audio_enabled
            settings.audio.analyze_on_sync = self.audio_analyze_on_sync
            settings.audio.duration_limit = self.audio_duration_limit

            settings.musicbrainz.enabled = self.musicbrainz_enabled
            settings.musicbrainz.enrich_on_sync = self.musicbrainz_enrich_on_sync
            settings.musicbrainz.confidence_threshold = self.musicbrainz_confidence_threshold
            settings.musicbrainz.contact_email = self.musicbrainz_contact_email

            settings.save_to_file()

            # Saved: drop the drafts, show fresh stored status, and empty each
            # staged input (it is uncontrolled, so the browser still holds the text).
            if token:
                secret_drafts.discard(token)
            for field in staged:
                self._set_secret_error(field, "")
            self._refresh_secret_status(settings)
            # The persistent set-save-status region announces the result after the
            # bar (and the focused Save button) is gone.
            self.save_status = SAVE_OK_STATUS
            self._take_snapshot()
            self.unsaved_from_earlier = False
            self.embedding_confirm_open = False
            # Readiness follows the saved settings: a confirmed embedding change
            # that mismatches the index now reads "rebuild embeddings" everywhere.
            self.check_configuration_status()
            events: List[Any] = [rx.toast.success("Settings saved successfully!")]
            events.extend(rx.toast.warning(message) for message in warnings)
            events.extend(_reset_secret_inputs(staged))
            events.append(rx.call_script(FOCUS_AFTER_SAVEBAR_JS))
            return events

        except Exception as e:
            # safe_error_text drops the rejected input of a pydantic ValidationError
            # (it can be a stored secret); staged values are redacted on top.
            message = _redact(safe_error_text(e), staged_values)
            self.save_status = SAVE_FAILED_PREFIX + message
            return rx.toast.error(f"Failed to save settings: {message}")

    def validate_plex_url(self, url: str):
        self.plex_url = url
        is_valid, error = validate_url(url)
        self.plex_url_error = error if error else ""

    @rx.event(background=True)
    async def download_local_llm_model(self):
        from ._settings_downloads import download_local_llm_impl

        await download_local_llm_impl(self)

    def validate_temperature(self, temp: float):
        self.ai_temperature = temp
        is_valid, error = validate_temperature(temp)
        self.temperature_error = error if error else ""

    def validate_sync_batch_size(self, size: int):
        self.sync_batch_size = size
        is_valid, error = validate_batch_size(size)
        self.batch_size_error = error if error else ""

    def validate_local_endpoint(self, endpoint: str):
        self.ai_local_endpoint = endpoint
        if self.ai_provider != "local" or self.ai_local_mode != "endpoint":
            self.local_endpoint_error = ""
            return
        is_valid, error = validate_url(endpoint)
        self.local_endpoint_error = error if error else ""

    @rx.event(background=True)
    async def download_local_embedding_model(self):
        from ._settings_downloads import download_local_embedding_impl

        await download_local_embedding_impl(self)

    def _sync_embedding_dimension(self):
        from plexmix.services.registry import get_embedding_dimension

        self.embedding_dimension = get_embedding_dimension(
            self.embedding_provider,
            model=self.embedding_model,
            custom_dimension=self.embedding_custom_dimension,
        )

    def _get_settings_snapshot(self) -> Dict[str, Any]:
        """Every field Save writes, keyed as in ``_FIELD_SECTION``.

        Staged secrets appear as their action ("replace"/"clear" or ""), never a
        value. The attributes are read explicitly so Reflex can track them as the
        dependencies of the computed vars below.
        """
        snapshot: Dict[str, Any] = {
            "plex_url": self.plex_url,
            "plex_library": self.plex_library,
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_temperature": self.ai_temperature,
            "ai_local_mode": self.ai_local_mode,
            "ai_local_endpoint": self.ai_local_endpoint,
            "ai_custom_endpoint": self.ai_custom_endpoint,
            "ai_custom_model": self.ai_custom_model,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_custom_endpoint": self.embedding_custom_endpoint,
            "embedding_custom_model": self.embedding_custom_model,
            "embedding_custom_dimension": self.embedding_custom_dimension,
            "log_level": self.log_level,
            "audio_enabled": self.audio_enabled,
            "audio_analyze_on_sync": self.audio_analyze_on_sync,
            "audio_duration_limit": self.audio_duration_limit,
            "musicbrainz_enabled": self.musicbrainz_enabled,
            "musicbrainz_enrich_on_sync": self.musicbrainz_enrich_on_sync,
            "musicbrainz_confidence_threshold": self.musicbrainz_confidence_threshold,
            "musicbrainz_contact_email": self.musicbrainz_contact_email,
        }
        staged = self.secret_staged
        for field in SECRET_FIELDS:
            snapshot[_secret_snapshot_key(field)] = staged.get(field, "")
        return snapshot

    def _take_snapshot(self) -> None:
        """Record the current form as the saved one (a new dict: never mutated)."""
        self._settings_snapshot = self._get_settings_snapshot()

    def _changed_section_ids(self) -> List[str]:
        """Ids of the sections with a field that differs from the snapshot, in tab order."""
        saved = self._settings_snapshot
        if not saved:
            return []
        current = self._get_settings_snapshot()
        changed = {_FIELD_SECTION[key] for key, value in current.items() if saved.get(key) != value}
        return [section for section in SETTINGS_SECTIONS if section in changed]

    def _has_unsaved_edits(self) -> bool:
        """True when a snapshot exists and the form differs from it."""
        saved = self._settings_snapshot
        return bool(saved) and self._get_settings_snapshot() != saved

    def _embedding_warning(self) -> str:
        return embedding_mismatch_warning(
            self.index_dimension,
            self.index_track_count,
            self.embedding_dimension,
            self.embedding_provider,
        )

    @rx.var(cache=True)
    def has_unsaved_changes(self) -> bool:
        return self._has_unsaved_edits()

    @rx.var(cache=True)
    def changed_sections(self) -> List[str]:
        """Labels of the sections with unsaved changes, in tab order."""
        return [SECTION_LABELS[section] for section in self._changed_section_ids()]

    @rx.var(cache=True)
    def changed_sections_label(self) -> str:
        """ "Embeddings and Audio Analysis": the bar's list of changed sections."""
        return join_names([SECTION_LABELS[section] for section in self._changed_section_ids()])

    @rx.var(cache=True)
    def save_status_message(self) -> str:
        """The last save result while it is still current.

        A failure stays until the next save; "saved" hides again once the form
        has new unsaved changes.
        """
        status = self.save_status
        if status.startswith("✗") or not self._has_unsaved_edits():
            return status
        return ""

    @rx.var(cache=True)
    def embedding_change_warning(self) -> str:
        """Why the selected embedding provider/model doesn't fit the search index ("" if it does)."""
        return self._embedding_warning()

    @rx.var(cache=True)
    def ai_model_unlisted(self) -> bool:
        """The selected AI model is set but not in the provider's curated list."""
        return _is_unlisted(self.ai_model, self.ai_models)

    @rx.var(cache=True)
    def ai_model_choices(self) -> List[str]:
        """The curated models, plus the current model when it isn't one of them.

        The select can always show the saved or entered model instead of a blank.
        """
        choices = list(self.ai_models)
        if _is_unlisted(self.ai_model, self.ai_models):
            choices.append(self.ai_model)
        return choices

    @rx.var(cache=True)
    def ai_model_text(self) -> str:
        """Initial text of the "model that isn't listed" field (the unlisted model or "")."""
        return self._ai_model_text()

    def is_form_valid(self) -> bool:
        """Check if all form fields are valid (a Plex token stored or staged)."""
        has_plex_token = bool(self.secret_stored.get("plex_token")) or (
            self.secret_staged.get("plex_token") == ACTION_REPLACE
        )
        return all(
            [
                not self.plex_url_error,
                not self.plex_token_error,
                not self.ai_api_key_error,
                not self.embedding_api_key_error,
                not self.temperature_error,
                not self.batch_size_error,
                not self.local_endpoint_error,
                self.plex_url,
                has_plex_token,
            ]
        )

    @rx.var
    def local_model_capabilities(self) -> str:
        if self.ai_provider != "local":
            return ""
        model_info = LOCAL_LLM_MODELS.get(self.ai_model or "")
        if not model_info:
            return ""
        return model_info.get("capabilities", "")

    # --- Provider display name mappings for simple rx.select ---

    _AI_PROVIDER_MAP: dict[str, str] = {
        "Google": "gemini",
        "OpenAI": "openai",
        "Anthropic": "anthropic",
        "Cohere": "cohere",
        "Custom (OpenAI-Compatible)": "custom",
        "Local (Offline)": "local",
    }
    _AI_PROVIDER_REVERSE: dict[str, str] = {v: k for k, v in _AI_PROVIDER_MAP.items()}

    _EMBEDDING_PROVIDER_MAP: dict[str, str] = {
        "Gemini": "gemini",
        "OpenAI": "openai",
        "Cohere": "cohere",
        "Custom (OpenAI-Compatible)": "custom",
        "Local (Offline)": "local",
    }
    _EMBEDDING_PROVIDER_REVERSE: dict[str, str] = {v: k for k, v in _EMBEDDING_PROVIDER_MAP.items()}

    @rx.var(cache=True)
    def ai_provider_display(self) -> str:
        return self._AI_PROVIDER_REVERSE.get(self.ai_provider, "Google")

    def set_ai_provider_from_display(self, display_name: str) -> Any:
        value = self._AI_PROVIDER_MAP.get(display_name, "gemini")
        return self.set_ai_provider(value)

    @rx.var(cache=True)
    def embedding_provider_display(self) -> str:
        return self._EMBEDDING_PROVIDER_REVERSE.get(self.embedding_provider, "Gemini")

    def set_embedding_provider_from_display(self, display_name: str) -> Any:
        value = self._EMBEDDING_PROVIDER_MAP.get(display_name, "gemini")
        return self.set_embedding_provider(value)
