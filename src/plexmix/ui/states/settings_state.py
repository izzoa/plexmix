import reflex as rx
import asyncio
from typing import Optional, List
from plexmix.ui.states.app_state import AppState
from plexmix.ui.utils.validation import (
    validate_url, validate_plex_token, validate_api_key,
    validate_temperature, validate_batch_size
)


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

    embedding_provider: str = "gemini"
    embedding_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 3072
    embedding_models: List[str] = []

    db_path: str = ""
    faiss_index_path: str = ""
    sync_batch_size: int = 100
    embedding_batch_size: int = 50
    log_level: str = "INFO"

    testing_connection: bool = False
    plex_test_status: str = ""
    ai_test_status: str = ""
    embedding_test_status: str = ""
    save_status: str = ""
    active_tab: str = "plex"

    # Validation errors
    plex_url_error: str = ""
    plex_token_error: str = ""
    ai_api_key_error: str = ""
    embedding_api_key_error: str = ""
    temperature_error: str = ""
    batch_size_error: str = ""

    def on_load(self):
        super().on_load()
        self.load_settings()
        self.update_model_lists()

    def load_settings(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import (
                get_plex_token,
                get_google_api_key,
                get_openai_api_key,
                get_anthropic_api_key,
                get_cohere_api_key
            )

            settings = Settings.load_from_file()

            self.plex_url = settings.plex.url or ""
            self.plex_library = settings.plex.library_name or ""
            self.plex_token = get_plex_token() or ""

            # If we have a configured library name, add it to the list so it shows in dropdown
            if self.plex_library:
                self.plex_libraries = [self.plex_library]

            self.ai_provider = settings.ai.default_provider
            self.ai_model = settings.ai.model or ""
            self.ai_temperature = settings.ai.temperature

            if self.ai_provider == "gemini":
                self.ai_api_key = get_google_api_key() or ""
            elif self.ai_provider == "openai":
                self.ai_api_key = get_openai_api_key() or ""
            elif self.ai_provider == "anthropic":
                self.ai_api_key = get_anthropic_api_key() or ""
            elif self.ai_provider == "cohere":
                self.ai_api_key = get_cohere_api_key() or ""

            self.embedding_provider = settings.embedding.default_provider
            self.embedding_model = settings.embedding.model
            self.embedding_dimension = settings.embedding.dimension

            if self.embedding_provider == "gemini":
                self.embedding_api_key = get_google_api_key() or ""
            elif self.embedding_provider == "openai":
                self.embedding_api_key = get_openai_api_key() or ""
            elif self.embedding_provider == "cohere":
                self.embedding_api_key = get_cohere_api_key() or ""

            self.db_path = settings.database.path
            self.faiss_index_path = settings.database.faiss_index_path
            self.log_level = settings.logging.level

        except Exception as e:
            print(f"Error loading settings: {e}")

    def update_model_lists(self):
        ai_model_map = {
            "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash-001"],
            "openai": ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
            "anthropic": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-3-5"],
            "cohere": ["command", "command-light", "command-r"]
        }
        self.ai_models = ai_model_map.get(self.ai_provider, [])

        embedding_model_map = {
            "gemini": ["gemini-embedding-001"],
            "openai": ["text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"],
            "cohere": ["embed-english-v3.0", "embed-multilingual-v3.0"],
            "local": ["all-MiniLM-L6-v2"]
        }
        self.embedding_models = embedding_model_map.get(self.embedding_provider, [])

    def set_ai_provider(self, provider: str):
        self.ai_provider = provider
        self.update_model_lists()
        if self.ai_models:
            self.ai_model = self.ai_models[0]

    def set_embedding_provider(self, provider: str):
        self.embedding_provider = provider
        self.update_model_lists()
        if self.embedding_models:
            self.embedding_model = self.embedding_models[0]

        dimension_map = {
            "gemini": 3072,
            "openai": 1536,
            "cohere": 1024,
            "local": 768
        }
        self.embedding_dimension = dimension_map.get(provider, 768)

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

    def set_ai_temperature(self, temperature: float):
        self.ai_temperature = temperature

    def set_embedding_api_key(self, api_key: str):
        self.embedding_api_key = api_key

    def set_embedding_model(self, model: str):
        self.embedding_model = model

    def set_log_level(self, level: str):
        self.log_level = level

    @rx.event(background=True)
    async def test_plex_connection(self):
        async with self:
            self.testing_connection = True
            self.plex_test_status = "Testing..."

        try:
            from plexapi.server import PlexServer

            await asyncio.sleep(0.5)

            server = PlexServer(self.plex_url, self.plex_token)
            libraries = [section.title for section in server.library.sections() if section.type == "artist"]

            async with self:
                self.plex_libraries = libraries
                self.plex_test_status = f"✓ Connected! Found {len(libraries)} music libraries"
                self.testing_connection = False

        except Exception as e:
            async with self:
                self.plex_test_status = f"✗ Connection failed: {str(e)}"
                self.testing_connection = False

    @rx.event(background=True)
    async def test_ai_provider(self):
        async with self:
            self.testing_connection = True
            self.ai_test_status = "Testing AI provider..."

        try:
            await asyncio.sleep(0.5)

            async with self:
                self.ai_test_status = "✓ AI provider test successful"
                self.testing_connection = False

        except Exception as e:
            async with self:
                self.ai_test_status = f"✗ Test failed: {str(e)}"
                self.testing_connection = False

    @rx.event(background=True)
    async def test_embedding_provider(self):
        async with self:
            self.testing_connection = True
            self.embedding_test_status = "Testing embedding provider..."

        try:
            await asyncio.sleep(0.5)

            async with self:
                self.embedding_test_status = "✓ Embedding provider test successful"
                self.testing_connection = False

        except Exception as e:
            async with self:
                self.embedding_test_status = f"✗ Test failed: {str(e)}"
                self.testing_connection = False

    def save_all_settings(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import (
                store_plex_token,
                store_google_api_key,
                store_openai_api_key,
                store_anthropic_api_key,
                store_cohere_api_key
            )

            settings = Settings.load_from_file()

            settings.plex.url = self.plex_url
            settings.plex.library_name = self.plex_library
            if self.plex_token:
                store_plex_token(self.plex_token)

            settings.ai.default_provider = self.ai_provider
            settings.ai.model = self.ai_model
            settings.ai.temperature = self.ai_temperature

            if self.ai_api_key:
                if self.ai_provider == "gemini":
                    store_google_api_key(self.ai_api_key)
                elif self.ai_provider == "openai":
                    store_openai_api_key(self.ai_api_key)
                elif self.ai_provider == "anthropic":
                    store_anthropic_api_key(self.ai_api_key)
                elif self.ai_provider == "cohere":
                    store_cohere_api_key(self.ai_api_key)

            settings.embedding.default_provider = self.embedding_provider
            settings.embedding.model = self.embedding_model
            settings.embedding.dimension = self.embedding_dimension

            if self.embedding_api_key and self.embedding_provider != "local":
                if self.embedding_provider == "gemini":
                    store_google_api_key(self.embedding_api_key)
                elif self.embedding_provider == "openai":
                    store_openai_api_key(self.embedding_api_key)
                elif self.embedding_provider == "cohere":
                    store_cohere_api_key(self.embedding_api_key)

            settings.logging.level = self.log_level

            settings.save_to_file()

            self.save_status = "✓ Settings saved successfully!"
            self.check_configuration_status()

        except Exception as e:
            self.save_status = f"✗ Failed to save settings: {str(e)}"

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
        is_valid, error = validate_api_key(key, self.ai_provider)
        self.ai_api_key_error = error if error else ""

    def validate_embedding_api_key(self, key: str):
        self.embedding_api_key = key
        if self.embedding_provider != "local":
            is_valid, error = validate_api_key(key, self.embedding_provider)
            self.embedding_api_key_error = error if error else ""
        else:
            self.embedding_api_key_error = ""

    def validate_temperature(self, temp: float):
        self.ai_temperature = temp
        is_valid, error = validate_temperature(temp)
        self.temperature_error = error if error else ""

    def validate_sync_batch_size(self, size: int):
        self.sync_batch_size = size
        is_valid, error = validate_batch_size(size)
        self.batch_size_error = error if error else ""

    def is_form_valid(self) -> bool:
        """Check if all form fields are valid."""
        return all([
            not self.plex_url_error,
            not self.plex_token_error,
            not self.ai_api_key_error,
            not self.embedding_api_key_error,
            not self.temperature_error,
            not self.batch_size_error,
            self.plex_url,
            self.plex_token,
        ])
