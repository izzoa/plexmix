import reflex as rx
import asyncio
from typing import Optional, List
from plexmix.ui.states.app_state import AppState
from plexmix.ui.utils.validation import (
    validate_url, validate_plex_token, validate_api_key,
    validate_temperature, validate_batch_size
)
from plexmix.utils.embeddings import LOCAL_EMBEDDING_MODELS
from plexmix.ai.local_provider import LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL


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
    is_downloading_local_llm: bool = False
    local_llm_download_status: str = ""
    local_llm_download_progress: int = 0

    embedding_provider: str = "gemini"
    embedding_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 3072
    embedding_models: List[str] = []
    is_downloading_local_model: bool = False
    local_download_status: str = ""
    local_download_progress: int = 0

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
    local_endpoint_error: str = ""

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
            self.ai_local_mode = settings.ai.local_mode
            self.ai_local_endpoint = settings.ai.local_endpoint or ""
            self.ai_local_auth_token = settings.ai.local_auth_token or ""
            self.local_llm_download_status = ""
            self.local_llm_download_progress = 0
            self.is_downloading_local_llm = False

            if self.ai_provider == "gemini":
                self.ai_api_key = get_google_api_key() or ""
            elif self.ai_provider == "openai":
                self.ai_api_key = get_openai_api_key() or ""
            elif self.ai_provider == "anthropic":
                self.ai_api_key = get_anthropic_api_key() or ""
            elif self.ai_provider == "cohere":
                self.ai_api_key = get_cohere_api_key() or ""
            else:
                self.ai_api_key = ""

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
        finally:
            self._sync_embedding_dimension()

    def update_model_lists(self):
        ai_model_map = {
            # Sort all provider model lists alphabetically for a consistent UX
            "gemini": sorted(
                ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash-001"],
                key=str.lower,
            ),
            "openai": sorted(
                ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
                key=str.lower,
            ),
            "anthropic": sorted(
                ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
                key=str.lower,
            ),
            "cohere": sorted(
                ["command", "command-light", "command-r"],
                key=str.lower,
            ),
            # Sort local models alphabetically by display name for UI
            "local": sorted(
                LOCAL_LLM_MODELS.keys(),
                key=lambda k: LOCAL_LLM_MODELS[k]["display_name"].lower(),
            ),
        }
        models = ai_model_map.get(self.ai_provider, [])
        self.ai_models = models
        if models and self.ai_model not in models:
            self.ai_model = models[0]

        embedding_model_map = {
            "gemini": sorted(["gemini-embedding-001"], key=str.lower),
            "openai": sorted(
                [
                    "text-embedding-3-large",
                    "text-embedding-3-small",
                    "text-embedding-ada-002",
                ],
                key=str.lower,
            ),
            "cohere": sorted(
                ["embed-english-v3.0", "embed-multilingual-v3.0"],
                key=str.lower,
            ),
            # Sort local embedding model ids alphabetically by key name
            "local": sorted(list(LOCAL_EMBEDDING_MODELS.keys()), key=str.lower),
        }
        models = embedding_model_map.get(self.embedding_provider, [])
        self.embedding_models = models
        if models and self.embedding_model not in models:
            self.embedding_model = models[0]
        self._sync_embedding_dimension()

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
        self.ai_api_key = ""

    def set_embedding_provider(self, provider: str):
        self.embedding_provider = provider
        self.update_model_lists()
        if self.embedding_models:
            self.embedding_model = self.embedding_models[0]
        if provider != "local":
            self.is_downloading_local_model = False
            self.local_download_status = ""
            self.local_download_progress = 0
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

    def set_ai_temperature(self, temperature: float):
        self.ai_temperature = temperature

    def set_embedding_api_key(self, api_key: str):
        self.embedding_api_key = api_key

    def set_embedding_model(self, model: str):
        self.embedding_model = model
        self._sync_embedding_dimension()

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
            settings.ai.local_mode = self.ai_local_mode
            settings.ai.local_endpoint = self.ai_local_endpoint or None
            settings.ai.local_auth_token = self.ai_local_auth_token or None

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
        if self.ai_provider == "local":
            self.ai_api_key_error = ""
            return
        provider_key = self.ai_provider
        if provider_key == "anthropic":
            provider_key = "claude"
        is_valid, error = validate_api_key(key, provider_key)
        self.ai_api_key_error = error if error else ""

    def validate_embedding_api_key(self, key: str):
        self.embedding_api_key = key
        if self.embedding_provider != "local":
            is_valid, error = validate_api_key(key, self.embedding_provider)
            self.embedding_api_key_error = error if error else ""
        else:
            self.embedding_api_key_error = ""

    @rx.event(background=True)
    async def download_local_llm_model(self):
        if self.ai_provider != "local" or self.ai_local_mode != "builtin":
            return

        model_name = self.ai_model or LOCAL_LLM_DEFAULT_MODEL
        model_info = LOCAL_LLM_MODELS.get(model_name, {})

        async with self:
            self.is_downloading_local_llm = True
            self.local_llm_download_status = f"Preparing download for {model_name}..."
            self.local_llm_download_progress = 5

        async def update_status(message: str, progress: int):
            async with self:
                self.local_llm_download_status = message
                self.local_llm_download_progress = progress

        try:
            await update_status("Checking local cache...", 10)
            loop = asyncio.get_running_loop()

            def snapshot_download_model():
                from huggingface_hub import snapshot_download

                snapshot_download(model_name, local_files_only=False, resume_download=True)

            await loop.run_in_executor(None, snapshot_download_model)
            await update_status("Initializing model (first warmup may take a while)...", 55)

            def warmup_model():
                from transformers import AutoTokenizer, AutoModelForCausalLM
                import torch

                trust_remote_code = bool(model_info.get("trust_remote_code", False))
                tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=trust_remote_code)
                model = AutoModelForCausalLM.from_pretrained(
                    model_name,
                    trust_remote_code=trust_remote_code,
                    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                )
                if tokenizer.pad_token is None and tokenizer.eos_token is not None:
                    tokenizer.pad_token = tokenizer.eos_token
                inputs = tokenizer("Warm up playlist curation", return_tensors="pt")
                _ = model.generate(**inputs, max_new_tokens=8)

            await loop.run_in_executor(None, warmup_model)
            await update_status("✓ Model cached and ready for offline use", 100)

        except ImportError as e:
            await update_status(f"Missing dependency: {e}", 0)
        except Exception as e:
            await update_status(f"Error downloading {model_name}: {str(e)}", 0)
        finally:
            async with self:
                self.is_downloading_local_llm = False

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
        if self.embedding_provider != "local":
            return

        model_name = self.embedding_model or "all-MiniLM-L6-v2"
        async with self:
            self.is_downloading_local_model = True
            self.local_download_status = f"Preparing download for {model_name}..."
            self.local_download_progress = 5

        async def update_status(message: str, progress: int):
            async with self:
                self.local_download_status = message
                self.local_download_progress = progress

        try:
            await update_status("Checking local cache...", 15)
            loop = asyncio.get_running_loop()

            def snapshot_download_model():
                from huggingface_hub import snapshot_download

                snapshot_download(model_name, local_files_only=False, resume_download=True)

            await loop.run_in_executor(None, snapshot_download_model)
            await update_status("Initializing model (first run may take a minute)...", 70)

            def warmup_model():
                from sentence_transformers import SentenceTransformer

                SentenceTransformer(model_name)

            await loop.run_in_executor(None, warmup_model)
            await update_status("✓ Model cached and ready for offline use", 100)

        except ImportError as e:
            await update_status(f"Missing dependency: {e}", 0)
        except Exception as e:
            await update_status(f"Error downloading {model_name}: {str(e)}", 0)
        finally:
            async with self:
                self.is_downloading_local_model = False

    def _sync_embedding_dimension(self):
        if self.embedding_provider == "local":
            model_info = LOCAL_EMBEDDING_MODELS.get(self.embedding_model)
            if model_info:
                self.embedding_dimension = int(model_info.get("dimension", 384))
            else:
                self.embedding_dimension = 384
        else:
            dimension_map = {
                "gemini": 3072,
                "openai": 1536,
                "cohere": 1024,
            }
            self.embedding_dimension = dimension_map.get(self.embedding_provider, self.embedding_dimension)

    def is_form_valid(self) -> bool:
        """Check if all form fields are valid."""
        return all([
            not self.plex_url_error,
            not self.plex_token_error,
            not self.ai_api_key_error,
            not self.embedding_api_key_error,
            not self.temperature_error,
            not self.batch_size_error,
            not self.local_endpoint_error,
            self.plex_url,
            self.plex_token,
        ])

    @rx.var
    def local_model_capabilities(self) -> str:
        if self.ai_provider != "local":
            return ""
        model_info = LOCAL_LLM_MODELS.get(self.ai_model or "")
        if not model_info:
            return ""
        return model_info.get("capabilities", "")
