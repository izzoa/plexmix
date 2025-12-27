import typer
from typing import Optional, Dict, Any
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich import print as rprint
import sys
import os

from ..config.settings import Settings, get_config_path
from ..config import credentials
from ..database.sqlite_manager import SQLiteManager
from ..database.vector_index import VectorIndex
from ..plex.client import PlexClient
from ..plex.sync import SyncEngine
from ..utils.embeddings import EmbeddingGenerator
from ..ai import get_ai_provider, LOCAL_LLM_MODELS, LOCAL_LLM_DEFAULT_MODEL
from ..ai.tag_generator import TagGenerator
from ..playlist.generator import PlaylistGenerator
from ..utils.logging import setup_logging

app = typer.Typer(
    name="plexmix",
    help="AI-powered Plex playlist generator",
    add_completion=False
)
console = Console()


def _canonical_ai_provider(name: Optional[str]) -> str:
    if not name:
        return "gemini"
    name = name.lower()
    return "claude" if name == "anthropic" else name


def _resolve_ai_api_key(provider_name: Optional[str]) -> Optional[str]:
    provider = _canonical_ai_provider(provider_name)
    if provider == "gemini":
        return credentials.get_google_api_key()
    if provider == "openai":
        return credentials.get_openai_api_key()
    if provider == "claude":
        return credentials.get_anthropic_api_key()
    if provider == "cohere":
        return credentials.get_cohere_api_key()
    return None


def _local_provider_kwargs(settings: Settings) -> Dict[str, Any]:
    return {
        "local_mode": settings.ai.local_mode,
        "local_endpoint": settings.ai.local_endpoint,
        "local_auth_token": settings.ai.local_auth_token,
        "local_max_output_tokens": settings.ai.local_max_output_tokens,
    }


def _build_ai_provider(
    settings: Settings,
    provider_name: Optional[str] = None,
    api_key_override: Optional[str] = None,
    silent: bool = False,
):
    name = provider_name or settings.ai.default_provider or "gemini"
    api_key = api_key_override if api_key_override is not None else _resolve_ai_api_key(name)
    try:
        return get_ai_provider(
            provider_name=name,
            api_key=api_key,
            model=settings.ai.model,
            temperature=settings.ai.temperature,
            **_local_provider_kwargs(settings),
        )
    except ValueError as exc:
        if not silent:
            console.print(f"[yellow]{exc}[/yellow]")
        return None


@app.callback()
def main(
    config: Optional[str] = typer.Option(None, help="Path to config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Quiet mode"),
):
    log_level = "DEBUG" if verbose else ("ERROR" if quiet else "INFO")
    setup_logging(level=log_level, log_file="~/.plexmix/plexmix.log")


@app.command("ui")
def launch_ui(
    host: str = typer.Option("localhost", help="Host address for the UI server"),
    port: int = typer.Option(3000, help="Port for the UI frontend"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Enable hot-reloading for development"),
):
    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

        import reflex as rx

        console.print("[bold green]Launching PlexMix Web UI...[/bold green]")
        console.print(f"Frontend: http://{host}:{port}")
        console.print("Backend: http://localhost:8000")

        import subprocess

        cmd = [sys.executable, "-m", "reflex", "run", "--frontend-port", str(port), "--backend-host", host]
        if reload:
            cmd.append("--reload")

        subprocess.run(cmd, cwd=str(Path(__file__).parent.parent.parent.parent))

    except ImportError:
        console.print("[red]Reflex is not installed.[/red]")
        console.print("\nTo use the web UI, install PlexMix with UI extras:")
        console.print("  [bold]pip install plexmix[ui][/bold]")
        console.print("or")
        console.print("  [bold]poetry install -E ui[/bold]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Failed to launch UI: {e}[/red]")
        raise typer.Exit(1)


config_app = typer.Typer(name="config", help="Configuration management")
app.add_typer(config_app)

db_app = typer.Typer(name="db", help="Database management")
app.add_typer(db_app)


@config_app.command("init")
def config_init():
    console.print("[bold green]PlexMix Setup Wizard[/bold green]")
    console.print("This wizard will help you configure PlexMix.\n")

    plex_url = typer.prompt("Plex server URL", default="http://localhost:32400")
    plex_token = typer.prompt("Plex token", hide_input=True)

    credentials.store_plex_token(plex_token)

    plex_client = PlexClient(plex_url, plex_token)
    if not plex_client.connect():
        console.print("[red]Failed to connect to Plex server.[/red]\n")
        console.print("[yellow]Troubleshooting tips:[/yellow]")
        console.print("  1. Verify your Plex token is correct (no extra spaces)")
        console.print("  2. Check if your Plex server URL is accessible")
        console.print("  3. Try using https:// instead of http://")
        console.print("  4. If using a remote server, ensure the port is correct")
        console.print("  5. Check Plex server settings for 'Require authentication'")
        console.print("\n[dim]See logs above for detailed error information[/dim]")
        raise typer.Exit(1)

    libraries = plex_client.get_music_libraries()
    if not libraries:
        console.print("[red]No music libraries found on Plex server.[/red]")
        raise typer.Exit(1)

    console.print("\nAvailable music libraries:")
    for idx, lib in enumerate(libraries):
        console.print(f"  {idx + 1}. {lib}")

    lib_choice = typer.prompt(
        "Select library number",
        type=int,
        default=1
    )
    library_name = libraries[lib_choice - 1]

    ai_model = None
    local_mode = "builtin"
    local_endpoint: Optional[str] = None
    local_auth_token: Optional[str] = None

    console.print("\n[bold]Provider Configuration[/bold]")
    console.print("PlexMix supports multiple AI and embedding providers:\n")
    console.print("  1. Google Gemini (default) - Single API key for both AI and embeddings")
    console.print("  2. OpenAI - GPT models and embeddings")
    console.print("  3. Anthropic Claude - AI playlist generation only (no embeddings)")
    console.print("  4. Cohere - Command R models and embeddings")
    console.print("  5. Local embeddings - Free, offline (no API key needed)")
    console.print("  6. Local LLM - Offline playlist generation via Gemma/Yarn presets or your own endpoint\n")
    console.print("[dim]Note: Anthropic does not provide embeddings, so you'll need Gemini, OpenAI, Cohere, or local.[/dim]\n")

    use_gemini = typer.confirm("Use Google Gemini? (recommended)", default=True)

    google_api_key = None
    if use_gemini:
        google_api_key = typer.prompt("Google Gemini API key", hide_input=True)
        credentials.store_google_api_key(google_api_key)

    embedding_provider = "gemini" if use_gemini else None
    ai_provider = "gemini" if use_gemini else None

    use_openai = typer.confirm("\nConfigure OpenAI?", default=False)
    openai_key = None
    if use_openai:
        openai_key = typer.prompt("OpenAI API key", hide_input=True)
        credentials.store_openai_api_key(openai_key)

        if not use_gemini:
            console.print("\nOpenAI will be used for:")
            use_openai_embeddings = typer.confirm("  - Embeddings?", default=True)
            use_openai_ai = typer.confirm("  - Playlist generation?", default=True)

            if use_openai_embeddings:
                embedding_provider = "openai"
            if use_openai_ai:
                ai_provider = "openai"

    use_cohere = typer.confirm("\nConfigure Cohere?", default=False)
    cohere_key = None
    if use_cohere:
        cohere_key = typer.prompt("Cohere API key", hide_input=True)
        credentials.store_cohere_api_key(cohere_key)

        if not use_gemini and not use_openai:
            console.print("\nCohere will be used for:")
            use_cohere_embeddings = typer.confirm("  - Embeddings?", default=True)
            use_cohere_ai = typer.confirm("  - Playlist generation?", default=True)

            if use_cohere_embeddings:
                embedding_provider = "cohere"
            if use_cohere_ai:
                ai_provider = "cohere"

    use_anthropic = typer.confirm("\nConfigure Anthropic Claude?", default=False)
    if use_anthropic:
        anthropic_key = typer.prompt("Anthropic API key", hide_input=True)
        credentials.store_anthropic_api_key(anthropic_key)

        if not ai_provider:
            ai_provider = "claude"

        if not embedding_provider:
            console.print("\n[yellow]Anthropic selected for AI, but does not provide embeddings.[/yellow]")
            console.print("Choose an embedding provider:")
            console.print("  1. Google Gemini (3072 dimensions)")
            console.print("  2. OpenAI (1536 dimensions)")
            console.print("  3. Cohere (1024 dimensions)")
            console.print("  4. Local (384 dimensions, free, offline)")

            emb_choice = typer.prompt(
                "\nEmbedding provider",
                type=int,
                default=4,
                show_default=True
            )

            if emb_choice == 1:
                if not google_api_key:
                    google_api_key = typer.prompt("Google Gemini API key", hide_input=True)
                    credentials.store_google_api_key(google_api_key)
                embedding_provider = "gemini"
            elif emb_choice == 2:
                if not openai_key:
                    openai_key = typer.prompt("OpenAI API key", hide_input=True)
                    credentials.store_openai_api_key(openai_key)
                embedding_provider = "openai"
            elif emb_choice == 3:
                if not cohere_key:
                    cohere_key = typer.prompt("Cohere API key", hide_input=True)
                    credentials.store_cohere_api_key(cohere_key)
                embedding_provider = "cohere"
            else:
                embedding_provider = "local"

    use_local_llm = typer.confirm("\nUse Local LLM (offline or custom endpoint)?", default=False)
    if use_local_llm:
        ai_provider = "local"
        console.print("\nLocal LLM presets:")
        preset_items = list(LOCAL_LLM_MODELS.items())
        for idx, (model_id, meta) in enumerate(preset_items, start=1):
            console.print(f"  {idx}. {meta['display_name']} [{model_id}] - {meta['capabilities']}")
        model_prompt = "Enter local model (or number)"
        model_choice = typer.prompt(model_prompt, default=LOCAL_LLM_DEFAULT_MODEL)
        if model_choice.isdigit():
            idx = int(model_choice)
            if 1 <= idx <= len(preset_items):
                model_choice = preset_items[idx - 1][0]
        ai_model = model_choice

        use_endpoint = typer.confirm("Point to a custom local API endpoint instead?", default=False)
        if use_endpoint:
            local_mode = "endpoint"
            local_endpoint = typer.prompt(
                "Endpoint URL",
                default="http://localhost:11434/v1/chat/completions"
            )
            token_input = typer.prompt("Endpoint auth token (optional)", hide_input=True, default="")
            local_auth_token = token_input or None
        else:
            local_mode = "builtin"
            local_endpoint = None
            local_auth_token = None
            console.print("[dim]Models are cached on-demand. Use the UI settings page to pre-download if needed.[/dim]")

    if not embedding_provider:
        console.print("\n[yellow]No embedding provider selected. Using local embeddings (free, offline).[/yellow]")
        embedding_provider = "local"

    if not ai_provider:
        console.print("\n[red]Error: No AI provider configured for playlist generation.[/red]")
        console.print("You must configure at least one of: Gemini, OpenAI, Cohere, Anthropic, or Local LLM")
        raise typer.Exit(1)

    settings = Settings()
    settings.plex.url = plex_url
    settings.plex.library_name = library_name
    settings.embedding.default_provider = embedding_provider
    settings.ai.default_provider = ai_provider
    settings.ai.model = ai_model
    settings.ai.local_mode = local_mode
    settings.ai.local_endpoint = local_endpoint
    settings.ai.local_auth_token = local_auth_token

    config_path = get_config_path()
    settings.save_to_file(str(config_path))

    console.print(f"\n[green]Configuration saved to {config_path}[/green]")

    if typer.confirm("\nRun initial sync now? (May take 10-30 minutes for large libraries)", default=True):
        try:
            sync_incremental()
        except typer.Exit as e:
            if e.exit_code == 130:  # KeyboardInterrupt
                console.print("\n[yellow]You can resume the sync later with:[/yellow]")
                console.print("  plexmix sync")
            raise
    else:
        console.print("\nYou can run sync later with: plexmix sync")


@config_app.command("test")
def config_test():
    """Test Plex server connection."""
    settings = Settings()
    
    console.print("\n[bold cyan]Testing Plex Connection[/bold cyan]\n")
    
    if not settings.plex.url:
        console.print("[red]No Plex URL configured.[/red]")
        console.print("Run 'plexmix config init' to set up your configuration.")
        raise typer.Exit(1)
    
    plex_token = credentials.get_plex_token()
    if not plex_token:
        console.print("[red]No Plex token found.[/red]")
        console.print("Run 'plexmix config init' to set up your configuration.")
        raise typer.Exit(1)
    
    console.print(f"[dim]Server URL:[/dim] {settings.plex.url}")
    console.print(f"[dim]Token length:[/dim] {len(plex_token)} characters")
    console.print()
    
    plex_client = PlexClient(settings.plex.url, plex_token)
    
    console.print("Attempting to connect...")
    if plex_client.connect():
        console.print(f"[green]✓ Successfully connected to: {plex_client.server.friendlyName}[/green]")
        console.print(f"  Version: {plex_client.server.version}")
        console.print(f"  Platform: {plex_client.server.platform}")
        
        if settings.plex.library_name:
            console.print(f"\nTesting library access: {settings.plex.library_name}")
            if plex_client.select_library(settings.plex.library_name):
                console.print(f"[green]✓ Library '{settings.plex.library_name}' is accessible[/green]")
            else:
                console.print(f"[red]✗ Library '{settings.plex.library_name}' not found[/red]")
        
        console.print("\n[green]Connection test passed![/green]")
    else:
        console.print("[red]✗ Connection failed[/red]")
        console.print("\n[yellow]Troubleshooting:[/yellow]")
        console.print("  • Double-check your Plex token")
        console.print("  • Try using https:// instead of http://")
        console.print("  • Verify the server URL and port")
        console.print("  • Check if Plex server is running")
        raise typer.Exit(1)


@config_app.command("show")
def config_show():
    config_path = get_config_path()
    if not config_path.exists():
        console.print("[yellow]No configuration found. Run 'plexmix config init' first.[/yellow]")
        raise typer.Exit(1)

    settings = Settings.load_from_file(str(config_path))

    table = Table(title="PlexMix Configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Plex URL", settings.plex.url or "Not set")
    table.add_row("Library", settings.plex.library_name or "Not set")
    table.add_row("Database Path", settings.database.path)
    table.add_row("AI Provider", settings.ai.default_provider)
    table.add_row("Embedding Provider", settings.embedding.default_provider)
    table.add_row("Playlist Length", str(settings.playlist.default_length))

    console.print(table)


sync_app = typer.Typer(name="sync", help="Library synchronization", invoke_without_command=True)
app.add_typer(sync_app)

tags_app = typer.Typer(name="tags", help="AI-based tag generation")
app.add_typer(tags_app)

embeddings_app = typer.Typer(name="embeddings", help="Embedding generation")
app.add_typer(embeddings_app)


@sync_app.callback()
def sync_callback(
    ctx: typer.Context,
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync")
):
    if ctx.invoked_subcommand is None:
        sync_incremental(embeddings=embeddings)


@embeddings_app.command("generate")
def embeddings_generate(
    regenerate: bool = typer.Option(False, help="Regenerate all embeddings (including existing)"),
):
    console.print("[bold]Generating embeddings for tracks...[/bold]")

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    google_key = credentials.get_google_api_key()
    if not google_key:
        console.print("[red]Google API key required for embeddings.[/red]")
        console.print("Run: plexmix config init")
        raise typer.Exit(1)

    embedding_generator = EmbeddingGenerator(
        provider=settings.embedding.default_provider,
        api_key=google_key,
        model=settings.embedding.model
    )

    index_path = settings.database.get_index_path()
    vector_index = VectorIndex(
        dimension=embedding_generator.get_dimension(),
        index_path=str(index_path)
    )

    from ..utils.embeddings import create_track_text
    from ..database.models import Embedding
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

    with SQLiteManager(str(db_path)) as db:
        all_tracks = db.get_all_tracks()

        if regenerate:
            console.print(f"[yellow]Regenerating ALL embeddings for {len(all_tracks)} tracks[/yellow]")
            cursor = db.get_connection().cursor()
            cursor.execute('DELETE FROM embeddings')
            db.get_connection().commit()
            tracks_to_embed = all_tracks
        else:
            tracks_to_embed = [t for t in all_tracks if not db.get_embedding_by_track_id(t.id)]
            console.print(f"Found {len(tracks_to_embed)} tracks without embeddings")

        if not tracks_to_embed:
            console.print("[green]All tracks already have embeddings![/green]")
            console.print("Use --regenerate to regenerate all embeddings.")
            return

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Generating embeddings...", total=len(tracks_to_embed))

                batch_size = 50
                embeddings_saved = 0

                for i in range(0, len(tracks_to_embed), batch_size):
                    batch_tracks = tracks_to_embed[i:i + batch_size]

                    track_data_list = []
                    for track in batch_tracks:
                        artist = db.get_artist_by_id(track.artist_id)
                        album = db.get_album_by_id(track.album_id)

                        track_data = {
                            'id': track.id,
                            'title': track.title,
                            'artist': artist.name if artist else 'Unknown',
                            'album': album.title if album else 'Unknown',
                            'genre': track.genre or '',
                            'year': track.year or '',
                            'tags': track.tags or '',
                            'environments': track.environments or '',
                            'instruments': track.instruments or ''
                        }
                        track_data_list.append(track_data)

                    texts = [create_track_text(td) for td in track_data_list]
                    embeddings = embedding_generator.generate_batch_embeddings(texts, batch_size=batch_size)

                    for track_data, embedding_vector in zip(track_data_list, embeddings):
                        embedding = Embedding(
                            track_id=track_data['id'],
                            embedding_model=embedding_generator.provider_name,
                            embedding_dim=embedding_generator.get_dimension(),
                            vector=embedding_vector
                        )
                        db.insert_embedding(embedding)
                        embeddings_saved += 1
                        progress.update(task, advance=1)

            all_embeddings = db.get_all_embeddings()
            track_ids = [emb[0] for emb in all_embeddings]
            vectors = [emb[1] for emb in all_embeddings]

            vector_index.build_index(vectors, track_ids)
            vector_index.save_index(str(index_path))

            console.print(f"\n[green]✓ Successfully generated {embeddings_saved} embeddings![/green]")
            console.print(f"[green]✓ Vector index saved with {len(vectors)} total embeddings[/green]")

        except KeyboardInterrupt:
            console.print(f"\n[yellow]⚠ Interrupted. Saved {embeddings_saved} embeddings.[/yellow]")
            console.print("[yellow]Run 'plexmix embeddings generate' again to continue.[/yellow]")
            raise typer.Exit(130)


@sync_app.command("incremental")
def sync_incremental(
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync")
):
    console.print("[bold]Starting incremental library sync...[/bold]")

    settings = Settings.load_from_file()

    plex_token = credentials.get_plex_token()
    if not plex_token or not settings.plex.url:
        console.print("[red]Plex not configured. Run 'plexmix config init' first.[/red]")
        raise typer.Exit(1)

    plex_client = PlexClient(settings.plex.url, plex_token)
    if not plex_client.connect():
        console.print("[red]Failed to connect to Plex server.[/red]")
        raise typer.Exit(1)

    if settings.plex.library_name:
        plex_client.select_library(settings.plex.library_name)

    db_path = settings.database.get_db_path()
    with SQLiteManager(str(db_path)) as db:
        db.create_tables()

        embedding_generator = None
        vector_index = None
        ai_provider = _build_ai_provider(settings, silent=True)
        if not ai_provider and settings.ai.default_provider:
            console.print(
                f"[yellow]AI provider '{settings.ai.default_provider}' unavailable. Continuing without AI assistance.[/yellow]"
            )

        if embeddings:
            google_key = credentials.get_google_api_key()
            if google_key:
                embedding_generator = EmbeddingGenerator(
                    provider=settings.embedding.default_provider,
                    api_key=google_key,
                    model=settings.embedding.model
                )
                index_path = settings.database.get_index_path()
                vector_index = VectorIndex(
                    dimension=embedding_generator.get_dimension(),
                    index_path=str(index_path)
                )

        sync_engine = SyncEngine(plex_client, db, embedding_generator, vector_index, ai_provider)

        try:
            sync_result = sync_engine.incremental_sync(generate_embeddings=embeddings)

            console.print(f"\n[green]Incremental sync completed successfully![/green]")
            console.print(f"  Tracks added: {sync_result.tracks_added}")
            console.print(f"  Tracks updated: {sync_result.tracks_updated}")
            console.print(f"  Tracks removed: {sync_result.tracks_removed}")

        except KeyboardInterrupt:
            console.print(f"\n[yellow]Sync interrupted by user.[/yellow]")
            console.print("[green]Progress has been saved to database.[/green]")
            console.print("[yellow]Tip: Run 'plexmix sync' again to continue from where you left off.[/yellow]")
            raise typer.Exit(130)


@sync_app.command("regenerate")
def sync_regenerate(
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync")
):
    console.print("[bold red]⚠️  WARNING: This will delete ALL existing tags and embeddings![/bold red]")
    console.print("This operation will:")
    console.print("  - Clear all AI-generated tags")
    console.print("  - Delete all embeddings")
    console.print("  - Regenerate everything from scratch")

    if not typer.confirm("\nAre you sure you want to continue?", default=False):
        console.print("[yellow]Operation cancelled.[/yellow]")
        raise typer.Exit(0)

    console.print("\n[bold]Starting regenerate sync...[/bold]")

    settings = Settings.load_from_file()

    plex_token = credentials.get_plex_token()
    if not plex_token or not settings.plex.url:
        console.print("[red]Plex not configured. Run 'plexmix config init' first.[/red]")
        raise typer.Exit(1)

    plex_client = PlexClient(settings.plex.url, plex_token)
    if not plex_client.connect():
        console.print("[red]Failed to connect to Plex server.[/red]")
        raise typer.Exit(1)

    if settings.plex.library_name:
        plex_client.select_library(settings.plex.library_name)

    db_path = settings.database.get_db_path()
    with SQLiteManager(str(db_path)) as db:
        db.create_tables()

        embedding_generator = None
        vector_index = None
        ai_provider = _build_ai_provider(settings, silent=True)
        if not ai_provider and settings.ai.default_provider:
            console.print(
                f"[yellow]AI provider '{settings.ai.default_provider}' unavailable. Continuing without AI assistance.[/yellow]"
            )

        if embeddings:
            google_key = credentials.get_google_api_key()
            if google_key:
                embedding_generator = EmbeddingGenerator(
                    provider=settings.embedding.default_provider,
                    api_key=google_key,
                    model=settings.embedding.model
                )
                index_path = settings.database.get_index_path()
                vector_index = VectorIndex(
                    dimension=embedding_generator.get_dimension(),
                    index_path=str(index_path)
                )

        sync_engine = SyncEngine(plex_client, db, embedding_generator, vector_index, ai_provider)

        try:
            sync_result = sync_engine.regenerate_sync(generate_embeddings=embeddings)

            console.print(f"\n[green]Regenerate sync completed successfully![/green]")
            console.print(f"  Tracks added: {sync_result.tracks_added}")
            console.print(f"  Tracks updated: {sync_result.tracks_updated}")
            console.print(f"  Tracks removed: {sync_result.tracks_removed}")

        except KeyboardInterrupt:
            console.print(f"\n[yellow]Sync interrupted by user.[/yellow]")
            console.print("[green]Progress has been saved to database.[/green]")
            console.print("[yellow]Tip: Run 'plexmix sync regenerate' again to continue.[/yellow]")
            raise typer.Exit(130)


@sync_app.command("full")
def sync_full(
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync")
):
    console.print("[bold]Starting library sync (full is now an alias for incremental)...[/bold]")
    sync_incremental(embeddings=embeddings)


@app.command("doctor")
def doctor(
    force: bool = typer.Option(False, "--force", help="Force regenerate all tags and embeddings")
):
    console.print("[bold]🩺 PlexMix Doctor - Database Health Check[/bold]")

    settings = Settings.load_from_file()
    preferred_provider = settings.ai.default_provider or "gemini"
    db_path = settings.database.get_db_path()

    if force:
        console.print("\n[yellow]⚠️  FORCE MODE: Will delete all tags and embeddings and regenerate everything[/yellow]")
        if not typer.confirm("Are you sure you want to continue?", default=False):
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        with SQLiteManager(str(db_path)) as db:
            cursor = db.get_connection().cursor()

            cursor.execute('SELECT COUNT(*) FROM tracks')
            total_tracks = cursor.fetchone()[0]

            cursor.execute('SELECT COUNT(*) FROM embeddings')
            total_embeddings = cursor.fetchone()[0]

            console.print(f"\n[cyan]Current state:[/cyan]")
            console.print(f"  Total tracks: {total_tracks}")
            console.print(f"  Current embeddings: {total_embeddings}")

            console.print("\n[yellow]Deleting all tags and embeddings...[/yellow]")
            cursor.execute('UPDATE tracks SET tags = NULL, environments = NULL, instruments = NULL')
            cursor.execute('DELETE FROM embeddings')
            db.get_connection().commit()
            console.print("[green]✓ Deleted all tags and embeddings[/green]")

        console.print("\n[bold]Step 1: Generating tags for all tracks...[/bold]")
        tags_generate(provider=preferred_provider, regenerate_embeddings=False)

        console.print("\n[bold]Step 2: Generating embeddings for all tracks...[/bold]")
        embeddings_generate(regenerate=False)

        console.print("\n[green]✓ Force regeneration complete![/green]")
        return

    with SQLiteManager(str(db_path)) as db:
        cursor = db.get_connection().cursor()

        cursor.execute('SELECT COUNT(*) FROM tracks')
        total_tracks = cursor.fetchone()[0]

        cursor.execute('SELECT COUNT(DISTINCT track_id) FROM embeddings')
        tracks_with_embeddings = cursor.fetchone()[0]

        cursor.execute('''
            SELECT COUNT(*) FROM embeddings
            WHERE track_id NOT IN (SELECT id FROM tracks)
        ''')
        orphaned_count = cursor.fetchone()[0]

        console.print(f"\n[cyan]Database Status:[/cyan]")
        console.print(f"  Total tracks: {total_tracks}")
        console.print(f"  Tracks with embeddings: {tracks_with_embeddings}")
        console.print(f"  Orphaned embeddings: {orphaned_count}")

        missing_embeddings = total_tracks - tracks_with_embeddings

        if orphaned_count == 0 and missing_embeddings == 0:
            console.print("\n[green]✓ No orphaned embeddings found. All tracks have embeddings. Database is healthy![/green]")

            if typer.confirm("\nRun a sync to check for deleted tracks in Plex?", default=False):
                console.print("\n[bold]Running sync...[/bold]")
                sync_incremental(embeddings=False)
                console.print("\n[green]✓ Sync completed![/green]")
            return

        if orphaned_count == 0 and missing_embeddings > 0:
            console.print(f"\n[yellow]⚠ Found {missing_embeddings} tracks without embeddings[/yellow]")

        should_delete_orphaned = orphaned_count > 0
        should_generate_tags = False

        if should_delete_orphaned and typer.confirm(f"\nDelete {orphaned_count} orphaned embeddings?", default=True):
            cursor.execute('DELETE FROM embeddings WHERE track_id NOT IN (SELECT id FROM tracks)')
            deleted = cursor.rowcount
            db.get_connection().commit()
            console.print(f"[green]Deleted {deleted} orphaned embeddings[/green]")
        elif should_delete_orphaned:
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        # Check for untagged tracks first (tags should be generated before embeddings)
        cursor.execute('SELECT COUNT(*) FROM tracks WHERE tags IS NULL OR tags = ""')
        untagged_count = cursor.fetchone()[0]

        if untagged_count > 0:
            console.print(f"\n[cyan]Found {untagged_count} tracks without tags[/cyan]")
            if typer.confirm("\nGenerate AI tags for untagged tracks first? (recommended before embeddings)", default=True):
                should_generate_tags = True
            else:
                console.print("\n[yellow]Skipping tag generation. Tags improve embedding quality![/yellow]")

        cursor.execute('SELECT COUNT(*) FROM tracks WHERE id NOT IN (SELECT DISTINCT track_id FROM embeddings)')
        tracks_needing_embeddings = cursor.fetchone()[0]

        console.print(f"\n[cyan]Tracks needing embeddings: {tracks_needing_embeddings}[/cyan]")

        if tracks_needing_embeddings > 0:
            if typer.confirm("\nRegenerate embeddings now?", default=True):
                console.print("\n[bold]Regenerating embeddings...[/bold]")

                google_key = credentials.get_google_api_key()
                if not google_key:
                    console.print("[red]Google API key required for embeddings.[/red]")
                    console.print("Run: plexmix config init")
                    raise typer.Exit(1)

                embedding_generator = EmbeddingGenerator(
                    provider=settings.embedding.default_provider,
                    api_key=google_key,
                    model=settings.embedding.model
                )

                index_path = settings.database.get_index_path()
                vector_index = VectorIndex(
                    dimension=embedding_generator.get_dimension(),
                    index_path=str(index_path)
                )

                from ..utils.embeddings import create_track_text
                from ..database.models import Embedding
                from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

                all_tracks = db.get_all_tracks()
                tracks_to_embed = [t for t in all_tracks if not db.get_embedding_by_track_id(t.id)]

                try:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        TimeRemainingColumn(),
                    ) as progress:
                        task = progress.add_task("Generating embeddings...", total=len(tracks_to_embed))

                        batch_size = 50
                        embeddings_saved = 0

                        for i in range(0, len(tracks_to_embed), batch_size):
                            batch_tracks = tracks_to_embed[i:i + batch_size]

                            track_data_list = []
                            for track in batch_tracks:
                                artist = db.get_artist_by_id(track.artist_id)
                                album = db.get_album_by_id(track.album_id)

                                track_data = {
                                    'id': track.id,
                                    'title': track.title,
                                    'artist': artist.name if artist else 'Unknown',
                                    'album': album.title if album else 'Unknown',
                                    'genre': track.genre or '',
                                    'year': track.year or '',
                                    'tags': track.tags or ''
                                }
                                track_data_list.append(track_data)

                            texts = [create_track_text(td) for td in track_data_list]
                            embeddings = embedding_generator.generate_batch_embeddings(texts, batch_size=batch_size)

                            for track_data, embedding_vector in zip(track_data_list, embeddings):
                                embedding = Embedding(
                                    track_id=track_data['id'],
                                    embedding_model=embedding_generator.provider_name,
                                    embedding_dim=embedding_generator.get_dimension(),
                                    vector=embedding_vector
                                )
                                db.insert_embedding(embedding)
                                embeddings_saved += 1
                                progress.update(task, advance=1)

                    all_embeddings = db.get_all_embeddings()
                    track_ids = [emb[0] for emb in all_embeddings]
                    vectors = [emb[1] for emb in all_embeddings]

                    vector_index.build_index(vectors, track_ids)
                    vector_index.save_index(str(index_path))

                    console.print(f"\n[green]✓ Successfully generated {embeddings_saved} embeddings![/green]")

                except KeyboardInterrupt:
                    console.print(f"\n[yellow]⚠ Interrupted. Saved {embeddings_saved} embeddings.[/yellow]")
                    console.print("[yellow]Run 'plexmix doctor' again to continue.[/yellow]")
                    raise typer.Exit(130)
            else:
                console.print("\n[yellow]Run 'plexmix sync' later to generate embeddings.[/yellow]")

        console.print("\n[cyan]Checking for deleted tracks in Plex...[/cyan]")
        if typer.confirm("\nRun a sync to remove deleted tracks from database?", default=True):
            console.print("\n[bold]Running sync...[/bold]")
            sync_incremental(embeddings=False)
            console.print("\n[green]✓ Sync completed![/green]")

    # Generate tags outside the database context
    if should_generate_tags:
        tags_generate(provider=preferred_provider, regenerate_embeddings=True)
        console.print("\n[green]✓ Tags generated![/green]")


@tags_app.command("generate")
def tags_generate(
    provider: str = typer.Option("gemini", help="AI provider (gemini, openai, claude, local)"),
    regenerate_embeddings: bool = typer.Option(True, help="Regenerate embeddings after tagging")
):
    console.print("[bold]Generating tags for tracks...[/bold]")

    settings = Settings.load_from_file()
    provider = provider.lower()
    canonical = _canonical_ai_provider(provider)

    api_key = _resolve_ai_api_key(canonical)
    if canonical != "local" and not api_key:
        console.print(f"[red]{canonical.title()} API key not configured.[/red]")
        raise typer.Exit(1)

    db_path = settings.database.get_db_path()
    with SQLiteManager(str(db_path)) as db:
        all_tracks = db.get_all_tracks()

        tracks_needing_tags = [t for t in all_tracks if not t.tags or not t.get_tags_list()]

        if not tracks_needing_tags:
            console.print("[green]All tracks already have tags![/green]")
            return

        console.print(f"Found {len(tracks_needing_tags)} tracks without tags")

        ai_provider = _build_ai_provider(
            settings,
            provider_name=canonical,
            api_key_override=api_key,
            silent=True,
        )
        if not ai_provider:
            console.print(
                f"[red]AI provider '{provider}' is not ready. Configure credentials or endpoint first.[/red]"
            )
            raise typer.Exit(1)
        tag_generator = TagGenerator(ai_provider)

        track_data_list = []
        for track in tracks_needing_tags:
            artist = db.get_artist_by_id(track.artist_id)
            track_data_list.append({
                'id': track.id,
                'title': track.title,
                'artist': artist.name if artist else 'Unknown',
                'genre': track.genre or 'unknown'
            })

        updated_count = 0
        batch_size = 20

        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Generating tags...", total=len(track_data_list))

                for i in range(0, len(track_data_list), batch_size):
                    batch = track_data_list[i:i + batch_size]
                    batch_num = i // batch_size + 1
                    total_batches = (len(track_data_list) + batch_size - 1) // batch_size

                    progress.update(task, description=f"Generating tags (batch {batch_num}/{total_batches})...")

                    tags_dict = tag_generator.generate_tags_batch(batch, batch_size=batch_size)

                    for track in tracks_needing_tags:
                        if track.id in tags_dict and tags_dict[track.id]:
                            tag_data = tags_dict[track.id]
                            if isinstance(tag_data, dict):
                                track.set_tags_list(tag_data.get('tags', []))
                                environments = tag_data.get('environments', [])
                                if isinstance(environments, list):
                                    track.environments = ', '.join(environments) if environments else None
                                else:
                                    track.environments = environments
                                instruments = tag_data.get('instruments', [])
                                if isinstance(instruments, list):
                                    track.instruments = ', '.join(instruments) if instruments else None
                                else:
                                    track.instruments = instruments
                            else:
                                track.set_tags_list(tag_data if isinstance(tag_data, list) else [])
                            db.insert_track(track)
                            updated_count += 1

                    progress.update(task, advance=len(batch))

        except KeyboardInterrupt:
            console.print(f"\n[yellow]Tag generation interrupted by user.[/yellow]")
            console.print(f"[green]Successfully saved {updated_count} tracks with tags before interruption.[/green]")
            if updated_count > 0 and regenerate_embeddings:
                console.print("[yellow]Tip: Run the command again to continue tagging remaining tracks.[/yellow]")

        console.print(f"[green]Updated {updated_count} tracks with tags![/green]")

        if regenerate_embeddings and updated_count > 0:
            console.print("\n[bold]Regenerating embeddings for newly tagged tracks...[/bold]")

            google_key = credentials.get_google_api_key()
            if not google_key:
                console.print("[yellow]Google API key required for embeddings. Skipping.[/yellow]")
                return

            embedding_generator = EmbeddingGenerator(
                provider=settings.embedding.default_provider,
                api_key=google_key,
                model=settings.embedding.model
            )

            index_path = settings.database.get_index_path()
            vector_index = VectorIndex(
                dimension=embedding_generator.get_dimension(),
                index_path=str(index_path)
            )

            from ..utils.embeddings import create_track_text
            from ..database.models import Embedding
            from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

            newly_tagged_track_ids = {track.id for track in tracks_needing_tags if track.id in tags_dict and tags_dict[track.id]}
            tagged_tracks = [t for t in tracks_needing_tags if t.id in newly_tagged_track_ids]

            try:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeRemainingColumn(),
                ) as progress:
                    task = progress.add_task("Regenerating embeddings...", total=len(tagged_tracks))

                    emb_batch_size = 50
                    embeddings_saved = 0

                    for i in range(0, len(tagged_tracks), emb_batch_size):
                        batch_tracks = tagged_tracks[i:i + emb_batch_size]

                        track_data_list = []
                        for track in batch_tracks:
                            artist = db.get_artist_by_id(track.artist_id)
                            album = db.get_album_by_id(track.album_id)

                            track_data = {
                                'id': track.id,
                                'title': track.title,
                                'artist': artist.name if artist else 'Unknown',
                                'album': album.title if album else 'Unknown',
                                'genre': track.genre or '',
                                'year': track.year or '',
                                'tags': track.tags or ''
                            }
                            track_data_list.append(track_data)

                        texts = [create_track_text(td) for td in track_data_list]
                        embeddings = embedding_generator.generate_batch_embeddings(texts, batch_size=emb_batch_size)

                        for track_data, embedding_vector in zip(track_data_list, embeddings):
                            embedding = Embedding(
                                track_id=track_data['id'],
                                embedding_model=embedding_generator.provider_name,
                                embedding_dim=embedding_generator.get_dimension(),
                                vector=embedding_vector
                            )
                            db.insert_embedding(embedding)
                            embeddings_saved += 1
                            progress.update(task, advance=1)

                all_embeddings = db.get_all_embeddings()
                track_ids = [emb[0] for emb in all_embeddings]
                vectors = [emb[1] for emb in all_embeddings]

                vector_index.build_index(vectors, track_ids)
                vector_index.save_index(str(index_path))

                console.print(f"[green]Regenerated {embeddings_saved} embeddings with tags![/green]")

            except KeyboardInterrupt:
                console.print(f"\n[yellow]Embedding regeneration interrupted by user.[/yellow]")
                console.print(f"[green]Successfully saved {embeddings_saved} embeddings before interruption.[/green]")
                console.print("[yellow]Note: Vector index not rebuilt. Run 'plexmix sync' to rebuild index with all embeddings.[/yellow]")
                return


@app.command("create")
def create_playlist(
    mood: str = typer.Argument(..., help="Mood description for playlist"),
    limit: Optional[int] = typer.Option(None, help="Number of tracks"),
    name: Optional[str] = typer.Option(None, help="Playlist name"),
    genre: Optional[str] = typer.Option(None, help="Filter by genre"),
    year: Optional[int] = typer.Option(None, help="Filter by year"),
    environment: Optional[str] = typer.Option(None, help="Filter by environment (work, study, focus, relax, party, workout, sleep, driving, social)"),
    instrument: Optional[str] = typer.Option(None, help="Filter by instrument (piano, guitar, saxophone, trumpet, drums, bass, synth, vocals, strings, orchestra)"),
    pool_multiplier: Optional[int] = typer.Option(None, help="Candidate pool multiplier (default: 25x playlist length)"),
    create_in_plex: bool = typer.Option(True, help="Create playlist in Plex"),
):
    """
    Create a playlist based on mood using semantic similarity search.

    Playlist generation uses FAISS vector search with pre-computed embeddings.
    No AI provider API key is required for creation (embeddings are generated during sync).
    """
    console.print(f"[bold]Creating playlist for mood: {mood}[/bold]")

    settings = Settings.load_from_file()

    if limit is None:
        limit = typer.prompt(
            "How many tracks?",
            type=int,
            default=settings.playlist.default_length
        )

    db_path = settings.database.get_db_path()
    index_path = settings.database.get_index_path()

    with SQLiteManager(str(db_path)) as db:
        embedding_provider = settings.embedding.default_provider
        embedding_model = settings.embedding.model

        # Get embedding provider API key (only needed for query embedding generation)
        embedding_api_key = None
        if embedding_provider == "gemini":
            embedding_api_key = credentials.get_google_api_key()
        elif embedding_provider == "openai":
            embedding_api_key = credentials.get_openai_api_key()
        elif embedding_provider == "cohere":
            embedding_api_key = credentials.get_cohere_api_key()

        embedding_generator = EmbeddingGenerator(
            provider=embedding_provider,
            api_key=embedding_api_key,
            model=embedding_model,
        )

        vector_index = VectorIndex(
            dimension=embedding_generator.get_dimension(),
            index_path=str(index_path)
        )
        
        # Check for dimension mismatch
        if vector_index.dimension_mismatch:
            console.print("[red]⚠️ Embedding dimension mismatch![/red]")
            console.print(
                f"[yellow]Existing embeddings are {vector_index.loaded_dimension}D but current provider '{embedding_provider}' uses {embedding_generator.get_dimension()}D.[/yellow]"
            )
            console.print("\n[cyan]You must regenerate embeddings to use the new provider:[/cyan]")
            console.print("  plexmix sync incremental --embeddings")
            raise typer.Exit(1)

        generator = PlaylistGenerator(db, vector_index, embedding_generator)

        filters = {}
        if genre:
            filters['genre'] = genre
        if year:
            filters['year'] = year
        if environment:
            filters['environment'] = environment
        if instrument:
            filters['instrument'] = instrument

        tracks = generator.generate(
            mood,
            max_tracks=limit,
            candidate_pool_size=settings.playlist.candidate_pool_size,
            candidate_pool_multiplier=pool_multiplier if pool_multiplier is not None else settings.playlist.candidate_pool_multiplier,
            filters=filters if filters else None
        )

        if not tracks:
            console.print("[yellow]No tracks found matching criteria.[/yellow]")
            raise typer.Exit(1)

        table = Table(title=f"Generated Playlist: {mood}")
        table.add_column("#", style="cyan", width=4)
        table.add_column("Title", style="green")
        table.add_column("Artist", style="blue")
        table.add_column("Album", style="magenta")

        for idx, track in enumerate(tracks, 1):
            table.add_row(
                str(idx),
                track['title'],
                track['artist'],
                track['album']
            )

        console.print(table)

        if name is None:
            name = typer.prompt(
                "Playlist name",
                default=f"{mood} - {len(tracks)} tracks"
            )

        track_ids = [t['id'] for t in tracks]

        plex_key = None
        if create_in_plex:
            plex_token = credentials.get_plex_token()
            if plex_token and settings.plex.url:
                plex_client = PlexClient(settings.plex.url, plex_token)
                if plex_client.connect() and plex_client.select_library(settings.plex.library_name):
                    plex_rating_keys = []
                    # Reuse existing db connection instead of opening a new one
                    for track_id in track_ids:
                        track = db.get_track_by_id(track_id)
                        if track and track.plex_key:
                            plex_rating_keys.append(int(track.plex_key))

                    playlist = plex_client.create_playlist(name, plex_rating_keys, f"AI-generated playlist: {mood}")
                    if playlist:
                        plex_key = str(playlist.ratingKey)
                        console.print(f"[green]Created playlist in Plex![/green]")

        generator.save_playlist(name, track_ids, mood, plex_key=plex_key)
        console.print(f"[green]Playlist '{name}' saved with {len(tracks)} tracks![/green]")


@db_app.command("info")
def db_info():
    """Show database information and statistics."""
    settings = Settings()
    db_path = settings.database.get_db_path()
    index_path = settings.database.get_index_path()
    
    console.print("\n[bold cyan]Database Information[/bold cyan]\n")
    
    console.print("[bold]File Locations:[/bold]")
    console.print(f"  Database: {db_path}")
    console.print(f"  Embeddings: {index_path}")
    
    if db_path.exists():
        db_size = db_path.stat().st_size / (1024 * 1024)
        console.print(f"  Database size: {db_size:.2f} MB")
    else:
        console.print("  [yellow]Database file does not exist[/yellow]")
    
    if index_path.exists():
        index_size = index_path.stat().st_size / (1024 * 1024)
        console.print(f"  Index size: {index_size:.2f} MB")
    else:
        console.print("  [yellow]Embeddings index does not exist[/yellow]")
    
    if db_path.exists():
        try:
            with SQLiteManager(str(db_path)) as db:
                console.print("\n[bold]Statistics:[/bold]")
                
                track_count = db.count_tracks()
                embedding_count = db.count_tracks_with_embeddings()
                artist_count = len(db.get_all_artists())
                album_count = len(db.get_all_albums())
                untagged_count = db.count_untagged_tracks()
                playlists = db.get_playlists()
                
                console.print(f"  Tracks: {track_count:,}")
                console.print(f"  Artists: {artist_count:,}")
                console.print(f"  Albums: {album_count:,}")
                console.print(f"  Embeddings: {embedding_count:,}")
                console.print(f"  Untagged tracks: {untagged_count:,}")
                console.print(f"  Playlists: {len(playlists):,}")
                
                if track_count > 0:
                    embedding_coverage = (embedding_count / track_count) * 100
                    console.print(f"\n  Embedding coverage: {embedding_coverage:.1f}%")
                
                last_sync = db.get_last_sync_time()
                if last_sync:
                    console.print(f"\n[bold]Last Sync:[/bold] {last_sync}")
                else:
                    console.print("\n[yellow]No sync history found[/yellow]")
                    
        except Exception as e:
            console.print(f"\n[red]Error reading database: {e}[/red]")
    else:
        console.print("\n[yellow]Database not initialized. Run 'plexmix sync' to create it.[/yellow]")
    
    console.print()


@db_app.command("reset")
def db_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
    backup: bool = typer.Option(True, "--backup/--no-backup", help="Create backup before reset"),
):
    """
    Reset the PlexMix database and embeddings.
    
    This will delete all synced data, embeddings, playlists, and tags.
    Your Plex library and configuration will not be affected.
    """
    settings = Settings()
    db_path = settings.database.get_db_path()
    index_path = settings.database.get_index_path()
    
    console.print("\n[bold red]⚠️  Database Reset[/bold red]\n")
    console.print("This will permanently delete:")
    console.print("  • SQLite database:", str(db_path))
    console.print("  • FAISS embeddings index:", str(index_path))
    console.print("\n[yellow]What will be lost:[/yellow]")
    console.print("  • All synced music metadata from Plex")
    console.print("  • AI-generated embeddings for tracks")
    console.print("  • User-applied tags (moods, environments, instruments)")
    console.print("  • Playlist history")
    console.print("  • Sync history")
    console.print("\n[green]What will be preserved:[/green]")
    console.print("  • Your actual music files on Plex server")
    console.print("  • Plex server metadata")
    console.print("  • PlexMix configuration (.env and config.yaml)")
    console.print("  • API keys")
    
    if db_path.exists() or index_path.exists():
        if db_path.exists():
            try:
                with SQLiteManager(str(db_path)) as db:
                    track_count = db.count_tracks()
                    embedding_count = db.count_tracks_with_embeddings()
                    console.print(f"\n[dim]Current database contains:[/dim]")
                    console.print(f"  • {track_count:,} tracks")
                    console.print(f"  • {embedding_count:,} embeddings")
            except Exception:
                pass
    else:
        console.print("\n[yellow]Database files don't exist. Nothing to reset.[/yellow]")
        raise typer.Exit(0)
    
    if not force:
        console.print()
        confirm = typer.confirm("Are you sure you want to reset the database?", default=False)
        if not confirm:
            console.print("[yellow]Reset cancelled.[/yellow]")
            raise typer.Exit(0)
    
    backup_dir = None
    if backup:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = db_path.parent / "backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        console.print(f"\n[cyan]Creating backup in {backup_dir}...[/cyan]")
        
        if db_path.exists():
            import shutil
            backup_db = backup_dir / db_path.name
            shutil.copy2(db_path, backup_db)
            console.print(f"  ✓ Database backed up to {backup_db}")
        
        if index_path.exists():
            import shutil
            backup_index = backup_dir / index_path.name
            shutil.copy2(index_path, backup_index)
            console.print(f"  ✓ Embeddings backed up to {backup_index}")
    
    console.print("\n[cyan]Deleting database files...[/cyan]")
    
    if db_path.exists():
        db_path.unlink()
        console.print(f"  ✓ Deleted {db_path}")
    
    if index_path.exists():
        index_path.unlink()
        console.print(f"  ✓ Deleted {index_path}")
    
    console.print("\n[bold green]✓ Database reset complete![/bold green]")
    
    if backup and backup_dir:
        console.print(f"\n[dim]Backup saved to: {backup_dir}[/dim]")
    
    console.print("\n[yellow]Next steps:[/yellow]")
    console.print("  1. Run 'plexmix sync' to re-sync your Plex library")
    console.print("  2. (Optional) Run 'plexmix tags generate' to re-tag tracks")


if __name__ == "__main__":
    app()
