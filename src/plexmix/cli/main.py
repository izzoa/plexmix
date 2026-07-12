"""PlexMix CLI — thin entry point that wires up command modules."""

import typer
from typing import Optional

from ..utils.logging import setup_logging

# Import sub-apps from command modules
from .config_cmd import config_app
from .sync_cmd import sync_app
from .tags_cmd import tags_app
from .embeddings_cmd import embeddings_app
from .db_cmd import db_app
from .audio_cmd import audio_app
from .musicbrainz_cmd import musicbrainz_app
from .playlist_cmd import playlist_app
from .ui_cmd import launch_ui
from .create_cmd import create_playlist
from .doctor_cmd import doctor

# Re-export service helpers so existing tests that import from plexmix.cli.main still work.
from ..services.providers import (  # noqa: F401
    canonical_ai_provider as _canonical_ai_provider,
    resolve_ai_api_key as _resolve_ai_api_key,
    build_ai_provider as _build_ai_provider,
    build_embedding_generator as _build_embedding_generator,
    local_provider_kwargs as _local_provider_kwargs,
)
from ..config.settings import Settings, get_config_path  # noqa: F401
from ..config import credentials  # noqa: F401

app = typer.Typer(name="plexmix", help="AI-powered Plex playlist generator", add_completion=False)

# Register sub-apps
app.add_typer(config_app)
app.add_typer(sync_app)
app.add_typer(tags_app)
app.add_typer(embeddings_app)
app.add_typer(db_app)
app.add_typer(audio_app)
app.add_typer(musicbrainz_app)
app.add_typer(playlist_app)

# Register top-level commands
app.command("ui")(launch_ui)
app.command("create")(create_playlist)
app.command("doctor")(doctor)


@app.callback()
def main(
    config: Optional[str] = typer.Option(None, help="Path to config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Quiet mode"),
) -> None:
    # Honor the global --config: register it so every Settings.load_from_file()
    # in subcommands reads from it (previously the option was accepted and ignored).
    if config:
        from ..config.settings import set_config_path_override

        set_config_path_override(config)

    # --verbose/--quiet override the configured level; otherwise use the config's
    # level. The log file honors LOG_FILE_PATH / PLEXMIX_DATA_DIR instead of a
    # hard-coded path. Loading is defensive so a bad/missing config can't stop the
    # CLI from starting.
    from pathlib import Path

    log_level = "DEBUG" if verbose else ("ERROR" if quiet else "INFO")
    log_file = "~/.plexmix/plexmix.log"
    try:
        settings = Settings.load_from_file()
        level = settings.logging.level
        if not verbose and not quiet and isinstance(level, str):
            log_level = level
        log_path = settings.logging.get_log_path()
        if isinstance(log_path, Path):
            log_file = str(log_path)
    except Exception:
        pass
    setup_logging(level=log_level, log_file=log_file)


if __name__ == "__main__":
    app()
