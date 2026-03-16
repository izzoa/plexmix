import json
import logging
import reflex as rx
import asyncio
from typing import List
from plexmix.ui.states.app_state import AppState
from plexmix.ui.utils.validation import (
    validate_url,
    validate_plex_token,
    validate_api_key,
    validate_temperature,
    validate_batch_size,
)
from plexmix.ai.local_provider import LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL

logger = logging.getLogger(__name__)


class SettingsState(AppState):
    plex_url: str = ""
    plex_username: str = ""
    plex_token: str = ""
    plex_library: str = ""
    plex_libraries: List[str] = []

    ai_provider: str = "gemini"
    ai_api_key: str = ""
    ai_model: str = ""
    ai_temperature: float = 0.7
    ai_models: List[str] = []
    ai_local_mode: str = "builtin"
    ai_local_endpoint: str = ""
    ai_local_auth_token: str = ""
    ai_custom_endpoint: str = ""
    ai_custom_model: str = ""
    ai_custom_api_key: str = ""
    is_downloading_local_llm: bool = False
    local_llm_download_status: str = ""
    local_llm_download_progress: int = 0
    discovered_models: List[str] = []
    is_discovering_models: bool = False

    embedding_provider: str = "gemini"
    embedding_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 3072
    embedding_models: List[str] = []
    embedding_custom_endpoint: str = ""
    embedding_custom_model: str = ""
    embedding_custom_api_key: str = ""
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
    save_status: str = ""
    active_tab: str = "plex"
    _settings_snapshot: str = ""

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

    def set_active_tab(self, tab: str):
        self.active_tab = tab

    def on_load(self):
        if not self.check_auth():
            self.is_page_loading = False
            return
        super().on_load()
        self._detect_local_deps()
        self.load_settings()
        self.update_model_lists()
        self.is_page_loading = False

    def _detect_local_deps(self):
        """Check if local AI dependencies (sentence-transformers/PyTorch) are installed."""
        try:
            import sentence_transformers  # noqa: F401

            self.local_deps_available = True
        except ImportError:
            self.local_deps_available = False

    def load_settings(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import (
                get_plex_token,
                get_custom_ai_api_key,
                get_custom_embedding_api_key,
            )

            settings = Settings.load_from_file()

            self.plex_url = settings.plex.url or ""
            self.plex_library = settings.plex.library_name or ""
            self.plex_token = get_plex_token() or settings.plex.token or ""

            # If we have a configured library name, add it to the list so it shows in dropdown
            if self.plex_library:
                self.plex_libraries = [self.plex_library]

            self.ai_provider = settings.ai.default_provider
            self.ai_model = settings.ai.model or ""
            self.ai_temperature = settings.ai.temperature
            self.ai_local_mode = settings.ai.local_mode
            self.ai_local_endpoint = settings.ai.local_endpoint or ""
            self.ai_local_auth_token = settings.ai.local_auth_token or ""
            self.ai_custom_endpoint = settings.ai.custom_endpoint or ""
            self.ai_custom_model = settings.ai.custom_model or ""
            self.ai_custom_api_key = settings.ai.custom_api_key or get_custom_ai_api_key() or ""
            self.local_llm_download_status = ""
            self.local_llm_download_progress = 0
            self.is_downloading_local_llm = False

            self._load_ai_api_key_for_provider(self.ai_provider)

            self.embedding_provider = settings.embedding.default_provider
            self.embedding_model = settings.embedding.model
            self.embedding_dimension = settings.embedding.dimension
            self.embedding_custom_endpoint = settings.embedding.custom_endpoint or ""
            self.embedding_custom_model = settings.embedding.custom_model or ""
            self.embedding_custom_api_key = (
                settings.embedding.custom_api_key or get_custom_embedding_api_key() or ""
            )
            self.embedding_custom_dimension = settings.embedding.custom_dimension

            self._load_embedding_api_key_for_provider(self.embedding_provider)

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

        except Exception as e:
            logger.error("Error loading settings: %s", e)
        finally:
            self._sync_embedding_dimension()
            # Ensure paths show defaults when config hasn't set them
            if not self.db_path:
                from plexmix.config.settings import _data_dir

                self.db_path = str(_data_dir() / "plexmix.db")
            if not self.faiss_index_path:
                from plexmix.config.settings import _data_dir

                self.faiss_index_path = str(_data_dir() / "embeddings.index")
            self._settings_snapshot = self._get_settings_snapshot()

    def update_model_lists(self):
        from plexmix.services.registry import (
            get_ai_models_display,
            get_embedding_models,
        )

        models = get_ai_models_display(self.ai_provider)
        self.ai_models = models
        # Only auto-select if model is empty (preserve custom model names)
        if models and not self.ai_model:
            self.ai_model = models[0]

        models = get_embedding_models(self.embedding_provider)
        self.embedding_models = models
        # Only auto-select if model is empty (preserve custom model names)
        if models and not self.embedding_model:
            self.embedding_model = models[0]
        self._sync_embedding_dimension()

    def _load_ai_api_key_for_provider(self, provider: str) -> None:
        """Load the API key for the given AI provider from keyring/env vars."""
        from plexmix.services.providers import resolve_ai_api_key

        # Settings UI uses "anthropic" as the display name; service handles alias
        self.ai_api_key = resolve_ai_api_key(provider) or ""

    def _load_embedding_api_key_for_provider(self, provider: str) -> None:
        """Load the API key for the given embedding provider from keyring/env vars."""
        from plexmix.services.providers import resolve_embedding_api_key

        self.embedding_api_key = resolve_embedding_api_key(provider) or ""

    def set_ai_provider(self, provider: str):
        self.ai_provider = provider
        self.update_model_lists()
        if self.ai_models:
            self.ai_model = self.ai_models[0]
        if provider == "local" and not self.ai_model:
            self.ai_model = LOCAL_LLM_DEFAULT_MODEL
        if provider != "local":
            self.ai_local_mode = "builtin"
            self.ai_local_endpoint = ""
            self.ai_local_auth_token = ""
            self.local_llm_download_status = ""
            self.local_llm_download_progress = 0
        if provider != "custom":
            self.ai_custom_endpoint = ""
            self.ai_custom_model = ""
            self.ai_custom_api_key = ""
        self._load_ai_api_key_for_provider(provider)

    def set_embedding_provider(self, provider: str):
        self.embedding_provider = provider
        self.update_model_lists()
        if self.embedding_models:
            self.embedding_model = self.embedding_models[0]
        if provider != "local":
            self.is_downloading_local_model = False
            self.local_download_status = ""
            self.local_download_progress = 0
        if provider != "custom":
            self.embedding_custom_endpoint = ""
            self.embedding_custom_model = ""
            self.embedding_custom_api_key = ""
            self.embedding_custom_dimension = 1536
        self._load_embedding_api_key_for_provider(provider)
        self._sync_embedding_dimension()

    def set_plex_url(self, url: str):
        self.plex_url = url

    def set_plex_token(self, token: str):
        self.plex_token = token

    def set_plex_library(self, library: str):
        self.plex_library = library

    def set_plex_username(self, username: str):
        self.plex_username = username

    def set_ai_api_key(self, api_key: str):
        self.ai_api_key = api_key

    def set_ai_model(self, model: str):
        self.ai_model = model

    def set_ai_local_mode(self, mode: str):
        self.ai_local_mode = mode
        if mode != "endpoint":
            self.local_endpoint_error = ""

    def set_ai_local_endpoint(self, endpoint: str):
        self.ai_local_endpoint = endpoint

    def set_ai_local_auth_token(self, token: str):
        self.ai_local_auth_token = token

    def set_ai_custom_endpoint(self, endpoint: str):
        self.ai_custom_endpoint = endpoint

    def set_ai_custom_model(self, model: str):
        self.ai_custom_model = model

    def set_ai_custom_api_key(self, key: str):
        self.ai_custom_api_key = key

    @rx.event(background=True)
    async def discover_models(self):
        """Auto-discover models from the configured custom or local endpoint."""
        async with self:
            endpoint = ""
            api_key = ""
            if self.ai_provider == "custom":
                endpoint = self.ai_custom_endpoint
                api_key = self.ai_custom_api_key
            elif self.ai_provider == "local" and self.ai_local_mode == "endpoint":
                endpoint = self.ai_local_endpoint
                api_key = self.ai_local_auth_token
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
            yield rx.toast.error(f"Discovery failed: {str(e)}")

    def select_discovered_model(self, model: str):
        """Apply a discovered model to the appropriate field."""
        if self.ai_provider == "custom":
            self.ai_custom_model = model
        elif self.ai_provider == "local":
            self.ai_model = model

    def set_ai_temperature(self, temperature: float):
        self.ai_temperature = temperature

    def set_embedding_api_key(self, api_key: str):
        self.embedding_api_key = api_key

    def set_embedding_model(self, model: str):
        self.embedding_model = model
        self._sync_embedding_dimension()

    def set_embedding_custom_endpoint(self, endpoint: str):
        self.embedding_custom_endpoint = endpoint

    def set_embedding_custom_model(self, model: str):
        self.embedding_custom_model = model

    def set_embedding_custom_api_key(self, key: str):
        self.embedding_custom_api_key = key

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

    def set_musicbrainz_confidence_threshold(self, value: list):
        if value:
            self.musicbrainz_confidence_threshold = float(value[0])

    def set_musicbrainz_contact_email(self, email: str):
        self.musicbrainz_contact_email = email

    @rx.event(background=True)
    async def test_plex_connection(self):
        from ._settings_testing import test_plex_connection_impl

        await test_plex_connection_impl(self)

    @rx.event(background=True)
    async def test_ai_provider(self):
        from ._settings_testing import test_ai_provider_impl

        await test_ai_provider_impl(self)

    @rx.event(background=True)
    async def test_embedding_provider(self):
        from ._settings_testing import test_embedding_provider_impl

        await test_embedding_provider_impl(self)

    def save_all_settings(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import (
                store_plex_token,
                store_google_api_key,
                store_openai_api_key,
                store_anthropic_api_key,
                store_cohere_api_key,
                store_custom_ai_api_key,
                store_custom_embedding_api_key,
            )

            settings = Settings.load_from_file()

            settings.plex.url = self.plex_url
            settings.plex.library_name = self.plex_library
            settings.plex.token = self.plex_token or None
            if self.plex_token:
                store_plex_token(self.plex_token)

            settings.ai.default_provider = self.ai_provider
            settings.ai.model = self.ai_model
            settings.ai.temperature = self.ai_temperature
            settings.ai.local_mode = self.ai_local_mode
            settings.ai.local_endpoint = self.ai_local_endpoint or None
            settings.ai.local_auth_token = self.ai_local_auth_token or None
            settings.ai.custom_endpoint = self.ai_custom_endpoint or None
            settings.ai.custom_model = self.ai_custom_model or None
            settings.ai.custom_api_key = self.ai_custom_api_key or None

            if self.ai_api_key:
                if self.ai_provider == "gemini":
                    store_google_api_key(self.ai_api_key)
                elif self.ai_provider == "openai":
                    store_openai_api_key(self.ai_api_key)
                elif self.ai_provider == "anthropic":
                    store_anthropic_api_key(self.ai_api_key)
                elif self.ai_provider == "cohere":
                    store_cohere_api_key(self.ai_api_key)
            if self.ai_custom_api_key and self.ai_provider == "custom":
                store_custom_ai_api_key(self.ai_custom_api_key)

            settings.embedding.default_provider = self.embedding_provider
            settings.embedding.model = self.embedding_model
            settings.embedding.dimension = self.embedding_dimension
            settings.embedding.custom_endpoint = self.embedding_custom_endpoint or None
            settings.embedding.custom_model = self.embedding_custom_model or None
            settings.embedding.custom_api_key = self.embedding_custom_api_key or None
            settings.embedding.custom_dimension = self.embedding_custom_dimension

            if self.embedding_api_key and self.embedding_provider not in ("local", "custom"):
                if self.embedding_provider == "gemini":
                    store_google_api_key(self.embedding_api_key)
                elif self.embedding_provider == "openai":
                    store_openai_api_key(self.embedding_api_key)
                elif self.embedding_provider == "cohere":
                    store_cohere_api_key(self.embedding_api_key)
            if self.embedding_custom_api_key and self.embedding_provider == "custom":
                store_custom_embedding_api_key(self.embedding_custom_api_key)

            settings.logging.level = self.log_level

            settings.audio.enabled = self.audio_enabled
            settings.audio.analyze_on_sync = self.audio_analyze_on_sync
            settings.audio.duration_limit = self.audio_duration_limit

            settings.musicbrainz.enabled = self.musicbrainz_enabled
            settings.musicbrainz.enrich_on_sync = self.musicbrainz_enrich_on_sync
            settings.musicbrainz.confidence_threshold = self.musicbrainz_confidence_threshold
            settings.musicbrainz.contact_email = self.musicbrainz_contact_email

            settings.save_to_file()

            self.save_status = ""
            self._settings_snapshot = self._get_settings_snapshot()
            self.check_configuration_status()
            return rx.toast.success("Settings saved successfully!")

        except Exception as e:
            self.save_status = ""
            return rx.toast.error(f"Failed to save settings: {str(e)}")

    def validate_plex_url(self, url: str):
        self.plex_url = url
        is_valid, error = validate_url(url)
        self.plex_url_error = error if error else ""

    def validate_plex_token(self, token: str):
        self.plex_token = token
        is_valid, error = validate_plex_token(token)
        self.plex_token_error = error if error else ""

    def validate_ai_api_key(self, key: str):
        self.ai_api_key = key
        if self.ai_provider in ("local", "custom"):
            self.ai_api_key_error = ""
            return
        provider_key = self.ai_provider
        if provider_key == "anthropic":
            provider_key = "claude"
        is_valid, error = validate_api_key(key, provider_key)
        self.ai_api_key_error = error if error else ""

    def validate_embedding_api_key(self, key: str):
        self.embedding_api_key = key
        if self.embedding_provider not in ("local", "custom"):
            is_valid, error = validate_api_key(key, self.embedding_provider)
            self.embedding_api_key_error = error if error else ""
        else:
            self.embedding_api_key_error = ""

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

    def _get_settings_snapshot(self) -> str:
        """Return a JSON string of key settings fields for change detection."""
        return json.dumps(
            {
                "plex_url": self.plex_url,
                "plex_token": self.plex_token,
                "plex_library": self.plex_library,
                "ai_provider": self.ai_provider,
                "ai_api_key": self.ai_api_key,
                "ai_model": self.ai_model,
                "ai_temperature": self.ai_temperature,
                "ai_local_mode": self.ai_local_mode,
                "ai_local_endpoint": self.ai_local_endpoint,
                "ai_local_auth_token": self.ai_local_auth_token,
                "ai_custom_endpoint": self.ai_custom_endpoint,
                "ai_custom_model": self.ai_custom_model,
                "ai_custom_api_key": self.ai_custom_api_key,
                "embedding_provider": self.embedding_provider,
                "embedding_api_key": self.embedding_api_key,
                "embedding_model": self.embedding_model,
                "embedding_custom_endpoint": self.embedding_custom_endpoint,
                "embedding_custom_model": self.embedding_custom_model,
                "embedding_custom_api_key": self.embedding_custom_api_key,
                "embedding_custom_dimension": self.embedding_custom_dimension,
                "log_level": self.log_level,
                "audio_enabled": self.audio_enabled,
                "audio_analyze_on_sync": self.audio_analyze_on_sync,
                "audio_duration_limit": self.audio_duration_limit,
                "musicbrainz_enabled": self.musicbrainz_enabled,
                "musicbrainz_enrich_on_sync": self.musicbrainz_enrich_on_sync,
                "musicbrainz_confidence_threshold": self.musicbrainz_confidence_threshold,
                "musicbrainz_contact_email": self.musicbrainz_contact_email,
            },
            sort_keys=True,
        )

    @rx.var(cache=True)
    def has_unsaved_changes(self) -> bool:
        if not self._settings_snapshot:
            return False
        return self._get_settings_snapshot() != self._settings_snapshot

    def is_form_valid(self) -> bool:
        """Check if all form fields are valid."""
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
                self.plex_token,
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

    def set_ai_provider_from_display(self, display_name: str):
        value = self._AI_PROVIDER_MAP.get(display_name, "gemini")
        self.set_ai_provider(value)

    @rx.var(cache=True)
    def embedding_provider_display(self) -> str:
        return self._EMBEDDING_PROVIDER_REVERSE.get(self.embedding_provider, "Gemini")

    def set_embedding_provider_from_display(self, display_name: str):
        value = self._EMBEDDING_PROVIDER_MAP.get(display_name, "gemini")
        self.set_embedding_provider(value)
