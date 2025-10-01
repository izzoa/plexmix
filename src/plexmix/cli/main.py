import typer
from typing import Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich import print as rprint
import sys

from ..config.settings import Settings, get_config_path
from ..config import credentials
from ..database.sqlite_manager import SQLiteManager
from ..database.vector_index import VectorIndex
from ..plex.client import PlexClient
from ..plex.sync import SyncEngine
from ..utils.embeddings import EmbeddingGenerator
from ..ai import get_ai_provider
from ..playlist.generator import PlaylistGenerator
from ..utils.logging import setup_logging

app = typer.Typer(
    name="plexmix",
    help="AI-powered Plex playlist generator",
    add_completion=False
)
console = Console()


@app.callback()
def main(
    config: Optional[str] = typer.Option(None, "--config", help="Path to config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Quiet mode"),
):
    log_level = "DEBUG" if verbose else ("ERROR" if quiet else "INFO")
    setup_logging(level=log_level, log_file="~/.plexmix/plexmix.log")


config_app = typer.Typer(name="config", help="Configuration management")
app.add_typer(config_app)


@config_app.command("init")
def config_init():
    console.print("[bold green]PlexMix Setup Wizard[/bold green]")
    console.print("This wizard will help you configure PlexMix.\n")

    plex_url = typer.prompt("Plex server URL", default="http://localhost:32400")
    plex_token = typer.prompt("Plex token", hide_input=True)

    credentials.store_plex_token(plex_token)

    plex_client = PlexClient(plex_url, plex_token)
    if not plex_client.connect():
        console.print("[red]Failed to connect to Plex server. Please check your URL and token.[/red]")
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

    console.print("\n[bold]Google Gemini API Key (required for default AI and embeddings)[/bold]")
    google_api_key = typer.prompt("Google API key", hide_input=True)
    credentials.store_google_api_key(google_api_key)

    configure_alternatives = typer.confirm(
        "\nWould you like to configure alternative providers (OpenAI, Anthropic)?",
        default=False
    )

    if configure_alternatives:
        if typer.confirm("Configure OpenAI?", default=False):
            openai_key = typer.prompt("OpenAI API key", hide_input=True)
            credentials.store_openai_api_key(openai_key)

        if typer.confirm("Configure Anthropic Claude?", default=False):
            anthropic_key = typer.prompt("Anthropic API key", hide_input=True)
            credentials.store_anthropic_api_key(anthropic_key)

    settings = Settings()
    settings.plex.url = plex_url
    settings.plex.library_name = library_name

    config_path = get_config_path()
    settings.save_to_file(str(config_path))

    console.print(f"\n[green]Configuration saved to {config_path}[/green]")

    if typer.confirm("\nRun initial sync now? (May take 10-30 minutes for large libraries)", default=True):
        sync_full()
    else:
        console.print("\nYou can run sync later with: plexmix sync full")


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


sync_app = typer.Typer(name="sync", help="Library synchronization")
app.add_typer(sync_app)


@sync_app.command("full")
def sync_full(
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync")
):
    console.print("[bold]Starting full library sync...[/bold]")

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

        sync_engine = SyncEngine(plex_client, db, embedding_generator, vector_index)
        sync_result = sync_engine.full_sync(generate_embeddings=embeddings)

        console.print(f"\n[green]Sync completed successfully![/green]")
        console.print(f"  Tracks added: {sync_result.tracks_added}")
        console.print(f"  Tracks updated: {sync_result.tracks_updated}")
        console.print(f"  Tracks removed: {sync_result.tracks_removed}")


@app.command("create")
def create_playlist(
    mood: str = typer.Argument(..., help="Mood description for playlist"),
    provider: str = typer.Option("gemini", help="AI provider (gemini, openai, claude)"),
    limit: Optional[int] = typer.Option(None, help="Number of tracks"),
    name: Optional[str] = typer.Option(None, help="Playlist name"),
    genre: Optional[str] = typer.Option(None, help="Filter by genre"),
    year: Optional[int] = typer.Option(None, help="Filter by year"),
    create_in_plex: bool = typer.Option(True, help="Create playlist in Plex"),
):
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

    google_key = credentials.get_google_api_key()
    openai_key = credentials.get_openai_api_key()
    anthropic_key = credentials.get_anthropic_api_key()

    if provider == "gemini" and not google_key:
        console.print("[red]Google API key not configured.[/red]")
        raise typer.Exit(1)
    elif provider == "openai" and not openai_key:
        console.print("[red]OpenAI API key not configured.[/red]")
        raise typer.Exit(1)
    elif provider == "claude" and not anthropic_key:
        console.print("[red]Anthropic API key not configured.[/red]")
        raise typer.Exit(1)

    api_key = google_key if provider == "gemini" else (openai_key if provider == "openai" else anthropic_key)

    with SQLiteManager(str(db_path)) as db:
        embedding_generator = EmbeddingGenerator(
            provider=settings.embedding.default_provider,
            api_key=google_key
        )

        vector_index = VectorIndex(
            dimension=embedding_generator.get_dimension(),
            index_path=str(index_path)
        )

        ai_provider = get_ai_provider(provider, api_key=api_key)

        generator = PlaylistGenerator(db, vector_index, ai_provider, embedding_generator)

        filters = {}
        if genre:
            filters['genre'] = genre
        if year:
            filters['year'] = year

        tracks = generator.generate(
            mood,
            max_tracks=limit,
            candidate_pool_size=settings.playlist.candidate_pool_size,
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
                    playlist = plex_client.create_playlist(name, track_ids, f"AI-generated playlist: {mood}")
                    if playlist:
                        plex_key = str(playlist.ratingKey)
                        console.print(f"[green]Created playlist in Plex![/green]")

        generator.save_playlist(name, track_ids, mood, plex_key=plex_key)
        console.print(f"[green]Playlist '{name}' saved with {len(tracks)} tracks![/green]")


if __name__ == "__main__":
    app()
