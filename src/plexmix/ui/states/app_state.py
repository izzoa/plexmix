import reflex as rx
from pathlib import Path
from typing import Optional


class AppState(rx.State):
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
    last_sync: Optional[str] = None
    
    embedding_dimension_warning: str = ""

    current_task: Optional[str] = None
    task_progress: int = 0

    # Page loading state for navigation transitions
    is_page_loading: bool = True

    @rx.event
    def set_page_loading(self, loading: bool):
        """Set the page loading state."""
        self.is_page_loading = loading

    @rx.event
    def on_load(self):
        """Load app data when the page loads."""
        print("AppState.on_load called")
        self.check_configuration_status()
        self.load_library_stats()
        return rx.console_log("App state loaded")

    def check_configuration_status(self):
        try:
            from plexmix.config.settings import Settings
            from plexmix.config.credentials import get_plex_token, get_google_api_key, get_openai_api_key, get_anthropic_api_key, get_cohere_api_key
            import os

            settings = Settings.load_from_file()

            # Check Plex configuration
            plex_token = get_plex_token()
            print(f"Plex URL: {settings.plex.url}, Token: {bool(plex_token)}, Library: {settings.plex.library_name}")
            self.plex_configured = bool(
                settings.plex.url and
                plex_token and
                settings.plex.library_name
            )
            self.plex_library_name = settings.plex.library_name or ""
            self.plex_server_url = settings.plex.url or ""

            # Check AI provider configuration
            # Check both the credentials module and environment variables
            ai_configured = False

            # Check Google API key
            google_key = get_google_api_key()
            if not google_key:
                google_key = os.environ.get("GOOGLE_API_KEY")

            # Check OpenAI API key
            openai_key = get_openai_api_key()
            if not openai_key:
                openai_key = os.environ.get("OPENAI_API_KEY")

            # Check Anthropic API key
            anthropic_key = get_anthropic_api_key()
            if not anthropic_key:
                anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

            # Check Cohere API key
            cohere_key = get_cohere_api_key()
            if not cohere_key:
                cohere_key = os.environ.get("COHERE_API_KEY")

            ai_keys = [google_key, openai_key, anthropic_key, cohere_key]
            if settings.ai.default_provider == "local":
                self.ai_provider_configured = True
            else:
                self.ai_provider_configured = any(ai_keys)
            self.ai_provider_name = settings.ai.default_provider.title() if settings.ai.default_provider else ""
            self.ai_model_name = settings.ai.model or ""

            # Check embedding provider configuration
            embedding_keys = [google_key, openai_key, cohere_key]
            self.embedding_provider_configured = any(embedding_keys) or settings.embedding.default_provider == "local"
            self.embedding_provider_name = settings.embedding.default_provider.title() if settings.embedding.default_provider else ""
            self.embedding_model_name = settings.embedding.model or ""

        except Exception as e:
            print(f"Error checking configuration: {e}")
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

                # Check for dimension mismatch using metadata file
                import pickle
                from pathlib import Path
                faiss_path = Path(settings.database.faiss_index_path).expanduser()
                metadata_path = faiss_path.with_suffix('.metadata')
                if metadata_path.exists():
                    with open(metadata_path, 'rb') as f:
                        metadata = pickle.load(f)
                        loaded_dimension = metadata.get('dimension', 0)
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
            print(f"Error loading library stats: {e}")
            self.total_tracks = "0"
            self.embedded_tracks = "0"
            self.last_sync = None
            self.embedding_dimension_warning = ""
