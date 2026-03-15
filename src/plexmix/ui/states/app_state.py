import hmac
import logging
import reflex as rx
from typing import Optional

logger = logging.getLogger(__name__)


class AppState(rx.State):
    # Authentication state
    is_authenticated: bool = False
    auth_required: bool = False
    login_password: str = ""
    login_error: str = ""

    plex_configured: bool = False
    ai_provider_configured: bool = False
    embedding_provider_configured: bool = False

    # Configuration details
    plex_library_name: str = ""
    plex_server_url: str = ""
    ai_provider_name: str = ""
    ai_model_name: str = ""
    embedding_provider_name: str = ""
    embedding_model_name: str = ""

    total_tracks: str = "0"
    embedded_tracks: str = "0"
    audio_analyzed_tracks: str = "0"
    last_sync: Optional[str] = None

    embedding_dimension_warning: str = ""

    current_task: Optional[str] = None
    task_progress: int = 0

    # Mobile navigation state
    is_mobile_nav_open: bool = False

    # Page loading state for navigation transitions
    is_page_loading: bool = True

    # Cross-page rerun config (set by History, consumed by Generator)
    _rerun_generation_config: str = ""

    @rx.event
    def set_page_loading(self, loading: bool):
        """Set the page loading state."""
        self.is_page_loading = loading

    @rx.event
    def toggle_mobile_nav(self):
        """Toggle the mobile navigation sidebar."""
        self.is_mobile_nav_open = not self.is_mobile_nav_open

    @rx.event
    def close_mobile_nav(self):
        """Close the mobile navigation sidebar."""
        self.is_mobile_nav_open = False

    def check_auth(self) -> bool:
        """Check if authentication is required and whether user is authenticated.

        Returns True if the user is authenticated (or no password is set).
        Returns False if the user needs to log in.
        """
        import os

        try:
            from plexmix.config.settings import Settings

            settings = Settings.load_from_file()
            configured_password = settings.ui.password
        except Exception:
            configured_password = None

        # Also check env var directly as fallback
        if not configured_password:
            configured_password = os.environ.get("PLEXMIX_UI_PASSWORD")

        if not configured_password:
            # No password configured — auto-authenticate
            self.auth_required = False
            self.is_authenticated = True
            return True

        self.auth_required = True
        return self.is_authenticated

    @rx.event
    def attempt_login(self, form_data: dict):
        """Validate the submitted password."""
        import os

        password = form_data.get("password", "")

        try:
            from plexmix.config.settings import Settings

            settings = Settings.load_from_file()
            configured_password = settings.ui.password
        except Exception:
            configured_password = None

        if not configured_password:
            configured_password = os.environ.get("PLEXMIX_UI_PASSWORD", "")

        if configured_password and hmac.compare_digest(password, configured_password):
            self.is_authenticated = True
            self.login_error = ""
            self.login_password = ""
        else:
            self.login_error = "Incorrect password"
            self.login_password = ""

    @rx.event
    def logout(self):
        """Clear authentication state."""
        self.is_authenticated = False
        self.login_password = ""

    @rx.event
    def on_load(self):
        """Load app data when the page loads."""
        logger.debug("AppState.on_load called")
        if not self.check_auth():
            self.is_page_loading = False
            return

        # Cancel orphaned background tasks from dead browser sessions.
        # When a user closes and reopens the browser they get a new session
        # token; old tasks keep pushing deltas to the dead WebSocket which
        # floods the console with "delta to disconnected client" warnings.
        from plexmix.ui.job_manager import jobs

        token = self.router.session.client_token
        jobs.cancel_stale_clients(token)

        self.check_configuration_status()
        self.load_library_stats()
        return rx.console_log("App state loaded")

    def check_configuration_status(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import (
                get_plex_token,
                get_google_api_key,
                get_openai_api_key,
                get_anthropic_api_key,
                get_cohere_api_key,
            )
            import os

            settings = Settings.load_from_file()

            # Check Plex configuration
            plex_token = get_plex_token()
            logger.debug(
                "Plex URL: %s, Token: %s, Library: %s",
                settings.plex.url,
                bool(plex_token),
                settings.plex.library_name,
            )
            self.plex_configured = bool(
                settings.plex.url and plex_token and settings.plex.library_name
            )
            self.plex_library_name = settings.plex.library_name or ""
            self.plex_server_url = settings.plex.url or ""

            # Check AI provider configuration — only check the key for the *selected* provider
            from plexmix.config.credentials import get_custom_ai_api_key

            ai_provider = settings.ai.default_provider
            ai_key_map = {
                "gemini": lambda: get_google_api_key() or os.environ.get("GOOGLE_API_KEY"),
                "openai": lambda: get_openai_api_key() or os.environ.get("OPENAI_API_KEY"),
                "anthropic": lambda: get_anthropic_api_key() or os.environ.get("ANTHROPIC_API_KEY"),
                "cohere": lambda: get_cohere_api_key() or os.environ.get("COHERE_API_KEY"),
                "custom": lambda: (
                    settings.ai.custom_api_key
                    or get_custom_ai_api_key()
                    or os.environ.get("CUSTOM_AI_API_KEY")
                ),
                "local": lambda: True,
            }
            ai_key_fn = ai_key_map.get(ai_provider, lambda: None)
            self.ai_provider_configured = bool(ai_key_fn())
            self.ai_provider_name = ai_provider.title() if ai_provider else ""

            # Resolve the displayed model name based on provider
            if ai_provider == "custom":
                self.ai_model_name = settings.ai.custom_model or settings.ai.model or ""
            else:
                self.ai_model_name = settings.ai.model or ""

            # Check embedding provider configuration — only check the key for the *selected* provider
            from plexmix.config.credentials import get_custom_embedding_api_key

            embed_provider = settings.embedding.default_provider
            embed_key_map = {
                "gemini": lambda: get_google_api_key() or os.environ.get("GOOGLE_API_KEY"),
                "openai": lambda: get_openai_api_key() or os.environ.get("OPENAI_API_KEY"),
                "cohere": lambda: get_cohere_api_key() or os.environ.get("COHERE_API_KEY"),
                "custom": lambda: (
                    settings.embedding.custom_api_key
                    or get_custom_embedding_api_key()
                    or os.environ.get("CUSTOM_EMBEDDING_API_KEY")
                ),
                "local": lambda: True,
            }
            embed_key_fn = embed_key_map.get(embed_provider, lambda: None)
            self.embedding_provider_configured = bool(embed_key_fn())
            self.embedding_provider_name = embed_provider.title() if embed_provider else ""
            self.embedding_model_name = settings.embedding.model or ""

        except Exception as e:
            logger.error("Error checking configuration: %s", e)
            self.plex_configured = False
            self.ai_provider_configured = False
            self.embedding_provider_configured = False

    def load_library_stats(self):
        """Load library statistics using SQLiteManager for consistency."""
        try:
            from plexmix.config.settings import Settings
            from plexmix.database.sqlite_manager import SQLiteManager

            settings = Settings.load_from_file()
            db_path = settings.database.get_db_path()

            if not db_path.exists():
                self.total_tracks = "0"
                self.embedded_tracks = "0"
                self.audio_analyzed_tracks = "0"
                self.last_sync = None
                self.embedding_dimension_warning = ""
                return

            with SQLiteManager(str(db_path)) as db:
                cursor = db.get_connection().cursor()

                # Get total tracks count
                cursor.execute("SELECT COUNT(*) FROM tracks")
                self.total_tracks = str(cursor.fetchone()[0])

                # Get embedded tracks count from DB (consistent with doctor)
                cursor.execute("SELECT COUNT(DISTINCT track_id) FROM embeddings")
                self.embedded_tracks = str(cursor.fetchone()[0])

                # Get audio analyzed tracks count
                try:
                    audio_count = db.get_audio_features_count()
                    self.audio_analyzed_tracks = str(audio_count)
                except Exception:
                    self.audio_analyzed_tracks = "0"

                # Check for dimension mismatch using metadata file
                import pickle

                faiss_path = settings.database.get_index_path()
                metadata_path = faiss_path.with_suffix(".metadata")
                if metadata_path.exists():
                    with open(metadata_path, "rb") as f:
                        metadata = pickle.load(f)
                        loaded_dimension = metadata.get("dimension", 0)
                        expected_dimension = settings.embedding.get_dimension_for_provider(
                            settings.embedding.default_provider
                        )
                        if loaded_dimension != expected_dimension:
                            self.embedding_dimension_warning = (
                                f"⚠️ Embedding dimension mismatch: Existing embeddings are {loaded_dimension}D "
                                f"but current provider '{settings.embedding.default_provider}' uses {expected_dimension}D. "
                                f"Please regenerate embeddings."
                            )
                        else:
                            self.embedding_dimension_warning = ""
                else:
                    self.embedding_dimension_warning = ""

                # Use last_played as a proxy for last sync
                cursor.execute("SELECT MAX(last_played) FROM tracks")
                last_update = cursor.fetchone()[0]
                self.last_sync = last_update if last_update else None

        except Exception as e:
            logger.error("Error loading library stats: %s", e)
            self.total_tracks = "0"
            self.embedded_tracks = "0"
            self.audio_analyzed_tracks = "0"
            self.last_sync = None
            self.embedding_dimension_warning = ""
