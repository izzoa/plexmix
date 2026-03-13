import json
import logging
import reflex as rx
import asyncio
from typing import Optional, List
from plexmix.ui.states.app_state import AppState

logger = logging.getLogger(__name__)
from plexmix.ui.utils.validation import (
    validate_url, validate_plex_token, validate_api_key,
    validate_temperature, validate_batch_size
)
from plexmix.utils.embeddings import LOCAL_EMBEDDING_MODELS
from plexmix.ai.local_provider import LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL


def _friendly_error(e: Exception) -> str:
    """Turn a raw exception into a short, user-friendly error message."""
    msg = str(e)

    # Common patterns → plain-English rewrites
    if "401" in msg or "Unauthorized" in msg or "invalid.*key" in msg.lower():
        return "Invalid API key. Please check your key and try again."
    if "403" in msg or "Forbidden" in msg:
        return "Access denied. Your API key may lack the required permissions."
    if "404" in msg or "Not Found" in msg:
        return "Endpoint not found. Please check the URL or model name."
    if "429" in msg or "rate" in msg.lower():
        return "Rate limited by the provider. Please wait a moment and try again."
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "Connection timed out. Check the server URL and your network."
    if "connection" in msg.lower() and ("refused" in msg.lower() or "error" in msg.lower()):
        return "Could not connect to the server. Is the URL correct and the service running?"
    if "resolve" in msg.lower() or "dns" in msg.lower() or "name or service" in msg.lower():
        return "Could not resolve the hostname. Check the server URL."
    if "ssl" in msg.lower() or "certificate" in msg.lower():
        return "SSL/TLS error. The server's certificate may be invalid or expired."
    if "No module named" in msg:
        pkg = msg.split("No module named")[-1].strip(" \"'")
        return f"Missing Python package: {pkg}"

    # Fallback: truncate and clean up raw messages
    # Strip common noise like 'headers: {...}' or JSON blobs
    import re
    msg = re.sub(r"\{[^{}]{50,}\}", "{...}", msg)  # collapse long dicts
    msg = re.sub(r"headers:\s*\{[^}]*\}", "", msg)  # strip header dumps
    msg = msg.strip(" ,.")
    if len(msg) > 120:
        msg = msg[:117] + "..."
    return msg or "An unexpected error occurred."


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
                ["command-r7b-12-2024", "command-r-plus", "command-r", "command-a-03-2025"],
                key=str.lower,
            ),
            # Sort local models alphabetically by display name for UI
            "local": sorted(
                LOCAL_LLM_MODELS.keys(),
                key=lambda k: LOCAL_LLM_MODELS[k]["display_name"].lower(),
            ),
            # Custom: no preset models — user types model name
            "custom": [],
        }
        models = ai_model_map.get(self.ai_provider, [])
        self.ai_models = models
        # Only auto-select if model is empty (preserve custom model names)
        if models and not self.ai_model:
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
                ["embed-v4.0", "embed-english-v3.0", "embed-multilingual-v3.0"],
                key=str.lower,
            ),
            # Sort local embedding model ids alphabetically by key name
            "local": sorted(list(LOCAL_EMBEDDING_MODELS.keys()), key=str.lower),
            # Custom: no preset models
            "custom": [],
        }
        models = embedding_model_map.get(self.embedding_provider, [])
        self.embedding_models = models
        # Only auto-select if model is empty (preserve custom model names)
        if models and not self.embedding_model:
            self.embedding_model = models[0]
        self._sync_embedding_dimension()

    def _load_ai_api_key_for_provider(self, provider: str):
        """Load the API key for the given AI provider from keyring/env vars."""
        from plexmix.config.credentials import (
            get_google_api_key, get_openai_api_key,
            get_anthropic_api_key, get_cohere_api_key,
        )
        if provider == "gemini":
            self.ai_api_key = get_google_api_key() or ""
        elif provider == "openai":
            self.ai_api_key = get_openai_api_key() or ""
        elif provider == "anthropic":
            self.ai_api_key = get_anthropic_api_key() or ""
        elif provider == "cohere":
            self.ai_api_key = get_cohere_api_key() or ""
        else:
            self.ai_api_key = ""

    def _load_embedding_api_key_for_provider(self, provider: str):
        """Load the API key for the given embedding provider from keyring/env vars."""
        from plexmix.config.credentials import (
            get_google_api_key, get_openai_api_key, get_cohere_api_key,
        )
        if provider == "gemini":
            self.embedding_api_key = get_google_api_key() or ""
        elif provider == "openai":
            self.embedding_api_key = get_openai_api_key() or ""
        elif provider == "cohere":
            self.embedding_api_key = get_cohere_api_key() or ""
        else:
            self.embedding_api_key = ""

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
                self.plex_test_status = f"✗ {_friendly_error(e)}"
                self.testing_connection = False

    @rx.event(background=True)
    async def test_ai_provider(self):
        async with self:
            self.testing_connection = True
            self.ai_test_status = "Testing AI provider..."

        try:
            loop = asyncio.get_running_loop()
            provider = self.ai_provider
            api_key = self.ai_api_key

            if provider == "gemini":
                async with self:
                    self.ai_test_status = "Testing Gemini API..."

                def test_gemini():
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=self.ai_model or "gemini-2.5-flash",
                        contents="Say 'test successful' in exactly two words.",
                    )
                    return response.text

                await loop.run_in_executor(None, test_gemini)

            elif provider == "openai":
                async with self:
                    self.ai_test_status = "Testing OpenAI API..."

                def test_openai():
                    from openai import OpenAI
                    client = OpenAI(api_key=api_key)
                    response = client.chat.completions.create(
                        model=self.ai_model or "gpt-5-mini",
                        messages=[{"role": "user", "content": "Say 'test' in one word."}],
                        max_tokens=10,
                    )
                    return response.choices[0].message.content

                await loop.run_in_executor(None, test_openai)

            elif provider == "anthropic":
                async with self:
                    self.ai_test_status = "Testing Anthropic API..."

                def test_anthropic():
                    import anthropic
                    client = anthropic.Anthropic(api_key=api_key)
                    response = client.messages.create(
                        model=self.ai_model or "claude-sonnet-4-5",
                        max_tokens=10,
                        messages=[{"role": "user", "content": "Say 'test' in one word."}],
                    )
                    return response.content[0].text

                await loop.run_in_executor(None, test_anthropic)

            elif provider == "cohere":
                async with self:
                    self.ai_test_status = "Testing Cohere API..."

                def test_cohere():
                    import cohere
                    client = cohere.ClientV2(api_key=api_key)
                    response = client.chat(
                        model=self.ai_model or "command-r7b-12-2024",
                        messages=[{"role": "user", "content": "Say 'test' in one word."}],
                        max_tokens=10,
                    )
                    return response.message.content[0].text

                await loop.run_in_executor(None, test_cohere)

            elif provider == "custom":
                async with self:
                    self.ai_test_status = "Testing custom endpoint..."

                def test_custom():
                    from openai import OpenAI
                    endpoint = self.ai_custom_endpoint
                    if not endpoint:
                        raise ValueError("No endpoint URL configured")
                    model = self.ai_custom_model
                    if not model:
                        raise ValueError("No model name configured")
                    client = OpenAI(
                        base_url=endpoint,
                        api_key=self.ai_custom_api_key or "no-key-required",
                    )
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": "Say 'test' in one word."}],
                        max_tokens=10,
                    )
                    return response.choices[0].message.content

                await loop.run_in_executor(None, test_custom)

            elif provider == "local":
                if self.ai_local_mode == "builtin":
                    async with self:
                        self.ai_test_status = "Checking local model availability..."

                    def check_local_model():
                        from huggingface_hub import try_to_load_from_cache
                        model_name = self.ai_model or LOCAL_LLM_DEFAULT_MODEL
                        # Check if model config exists in cache
                        cached = try_to_load_from_cache(model_name, "config.json")
                        if cached is None:
                            raise ValueError(f"Model {model_name} not cached. Click 'Download Model' first.")
                        return True

                    await loop.run_in_executor(None, check_local_model)

                elif self.ai_local_mode == "endpoint":
                    async with self:
                        self.ai_test_status = "Testing local endpoint..."

                    def test_endpoint():
                        import requests
                        endpoint = self.ai_local_endpoint
                        if not endpoint:
                            raise ValueError("No endpoint configured")
                        # Try common health check endpoints
                        for path in ["", "/health", "/v1/models"]:
                            try:
                                headers = {}
                                if self.ai_local_auth_token:
                                    headers["Authorization"] = f"Bearer {self.ai_local_auth_token}"
                                resp = requests.get(f"{endpoint.rstrip('/')}{path}", headers=headers, timeout=10)
                                if resp.status_code < 400:
                                    return True
                            except Exception:
                                continue
                        raise ValueError("Could not connect to endpoint")

                    await loop.run_in_executor(None, test_endpoint)

            async with self:
                self.ai_test_status = f"✓ {provider.title()} provider test successful"
                self.testing_connection = False

        except ImportError as e:
            async with self:
                self.ai_test_status = f"✗ {_friendly_error(e)}"
                self.testing_connection = False

        except Exception as e:
            async with self:
                self.ai_test_status = f"✗ {_friendly_error(e)}"
                self.testing_connection = False

    @rx.event(background=True)
    async def test_embedding_provider(self):
        async with self:
            self.testing_connection = True
            self.embedding_test_status = "Testing embedding provider..."

        try:
            loop = asyncio.get_running_loop()
            provider = self.embedding_provider
            api_key = self.embedding_api_key
            test_text = "This is a test string for embedding generation."

            if provider == "gemini":
                async with self:
                    self.embedding_test_status = "Testing Gemini embeddings..."

                def test_gemini_embed():
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    result = client.models.embed_content(
                        model=self.embedding_model or "gemini-embedding-001",
                        contents=test_text,
                    )
                    return len(result.embeddings[0].values)

                dim = await loop.run_in_executor(None, test_gemini_embed)

            elif provider == "openai":
                async with self:
                    self.embedding_test_status = "Testing OpenAI embeddings..."

                def test_openai_embed():
                    from openai import OpenAI
                    client = OpenAI(api_key=api_key)
                    response = client.embeddings.create(
                        model=self.embedding_model or "text-embedding-3-small",
                        input=test_text,
                    )
                    return len(response.data[0].embedding)

                dim = await loop.run_in_executor(None, test_openai_embed)

            elif provider == "cohere":
                async with self:
                    self.embedding_test_status = "Testing Cohere embeddings..."

                def test_cohere_embed():
                    import cohere
                    client = cohere.ClientV2(api_key=api_key)
                    embed_model = self.embedding_model or "embed-v4.0"
                    embed_kwargs = {
                        "texts": [test_text],
                        "model": embed_model,
                        "input_type": "search_document",
                        "embedding_types": ["float"],
                    }
                    if "v4" in embed_model:
                        embed_kwargs["output_dimension"] = self.embedding_dimension
                    response = client.embed(**embed_kwargs)
                    return len(response.embeddings.float_[0])

                dim = await loop.run_in_executor(None, test_cohere_embed)

            elif provider == "custom":
                async with self:
                    self.embedding_test_status = "Testing custom embedding endpoint..."

                def test_custom_embed():
                    from openai import OpenAI
                    endpoint = self.embedding_custom_endpoint
                    if not endpoint:
                        raise ValueError("No endpoint URL configured")
                    model = self.embedding_custom_model
                    if not model:
                        raise ValueError("No model name configured")
                    client = OpenAI(
                        base_url=endpoint,
                        api_key=self.embedding_custom_api_key or "no-key-required",
                    )
                    response = client.embeddings.create(
                        model=model,
                        input=test_text,
                    )
                    return len(response.data[0].embedding)

                dim = await loop.run_in_executor(None, test_custom_embed)

            elif provider == "local":
                async with self:
                    self.embedding_test_status = "Testing local embedding model..."

                def test_local_embed():
                    from sentence_transformers import SentenceTransformer
                    model_name = self.embedding_model or "all-MiniLM-L6-v2"
                    model = SentenceTransformer(model_name)
                    embedding = model.encode(test_text)
                    return len(embedding)

                dim = await loop.run_in_executor(None, test_local_embed)

            else:
                dim = 0

            # Verify dimension matches expected
            expected_dim = self.embedding_dimension
            if dim != expected_dim:
                async with self:
                    self.embedding_test_status = f"⚠ Embeddings work, but dimension mismatch (got {dim}, expected {expected_dim}). You may need to regenerate your embeddings."
                    self.testing_connection = False
                return

            async with self:
                self.embedding_test_status = f"✓ Embedding test successful (dimension: {dim})"
                self.testing_connection = False

        except ImportError as e:
            async with self:
                self.embedding_test_status = f"✗ {_friendly_error(e)}"
                self.testing_connection = False

        except Exception as e:
            async with self:
                self.embedding_test_status = f"✗ {_friendly_error(e)}"
                self.testing_connection = False

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
        if self.embedding_provider == "custom":
            self.embedding_dimension = self.embedding_custom_dimension
        elif self.embedding_provider == "local":
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

    def _get_settings_snapshot(self) -> str:
        """Return a JSON string of key settings fields for change detection."""
        return json.dumps({
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
        }, sort_keys=True)

    @rx.var(cache=True)
    def has_unsaved_changes(self) -> bool:
        if not self._settings_snapshot:
            return False
        return self._get_settings_snapshot() != self._settings_snapshot

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
