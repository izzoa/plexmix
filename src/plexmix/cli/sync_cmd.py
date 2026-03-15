from typing import Tuple

import typer
from rich.console import Console

from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager
from ..plex.sync import SyncEngine
from ..services.providers import (
    build_ai_provider as _build_ai_provider,
    build_embedding_generator as _build_embedding_generator,
)
from ..services.sync_service import PlexConnectionError, connect_plex, build_vector_index

sync_app = typer.Typer(name="sync", help="Library synchronization", invoke_without_command=True)
console = Console()


def _connect_and_build(settings: Settings, generate_embeddings: bool = True) -> Tuple:
    """Returns (plex_client, ai_provider, embedding_generator, vector_index).

    Raises :class:`PlexConnectionError` if Plex connection fails.
    """
    plex_client = connect_plex(settings)

    ai_provider = _build_ai_provider(settings, silent=True)
    if not ai_provider and settings.ai.default_provider:
        console.print(
            f"[yellow]AI provider '{settings.ai.default_provider}' unavailable. Continuing without AI assistance.[/yellow]"
        )

    embedding_generator = None
    vector_index = None
    if generate_embeddings:
        embedding_generator = _build_embedding_generator(settings)
        if embedding_generator:
            vector_index = build_vector_index(settings, embedding_generator)

    return plex_client, ai_provider, embedding_generator, vector_index


@sync_app.callback()
def sync_callback(
    ctx: typer.Context,
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
    audio: bool = typer.Option(False, help="Extract audio features during sync"),
) -> None:
    if ctx.invoked_subcommand is None:
        sync_incremental(embeddings=embeddings, audio=audio)


@sync_app.command("incremental")
def sync_incremental(
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
    audio: bool = typer.Option(False, help="Extract audio features during sync"),
) -> None:
    console.print("[bold]Starting incremental library sync...[/bold]")

    settings = Settings.load_from_file()

    try:
        plex_client, ai_provider, embedding_generator, vector_index = _connect_and_build(
            settings, generate_embeddings=embeddings
        )
    except PlexConnectionError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    with SQLiteManager(str(settings.database.get_db_path())) as db:
        db.create_tables()

        sync_engine = SyncEngine(plex_client, db, embedding_generator, vector_index, ai_provider)

        try:
            sync_result = sync_engine.incremental_sync(generate_embeddings=embeddings)

            console.print("\n[green]Incremental sync completed successfully![/green]")
            console.print(f"  Tracks added: {sync_result.tracks_added}")
            console.print(f"  Tracks updated: {sync_result.tracks_updated}")
            console.print(f"  Tracks removed: {sync_result.tracks_removed}")

            # Run audio analysis if requested
            run_audio = audio or settings.audio.analyze_on_sync
            if run_audio:
                from .audio_cmd import _run_audio_analysis

                _run_audio_analysis(db, settings)

        except KeyboardInterrupt:
            console.print("\n[yellow]Sync interrupted by user.[/yellow]")
            console.print("[green]Progress has been saved to database.[/green]")
            console.print(
                "[yellow]Tip: Run 'plexmix sync' again to continue from where you left off.[/yellow]"
            )
            raise typer.Exit(130)


@sync_app.command("regenerate")
def sync_regenerate(
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
) -> None:
    console.print(
        "[bold red]⚠️  WARNING: This will delete ALL existing tags and embeddings![/bold red]"
    )
    console.print("This operation will:")
    console.print("  - Clear all AI-generated tags")
    console.print("  - Delete all embeddings")
    console.print("  - Regenerate everything from scratch")

    if not typer.confirm("\nAre you sure you want to continue?", default=False):
        console.print("[yellow]Operation cancelled.[/yellow]")
        raise typer.Exit(0)

    console.print("\n[bold]Starting regenerate sync...[/bold]")

    settings = Settings.load_from_file()

    try:
        plex_client, ai_provider, embedding_generator, vector_index = _connect_and_build(
            settings, generate_embeddings=embeddings
        )
    except PlexConnectionError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    with SQLiteManager(str(settings.database.get_db_path())) as db:
        db.create_tables()

        sync_engine = SyncEngine(plex_client, db, embedding_generator, vector_index, ai_provider)

        try:
            sync_result = sync_engine.regenerate_sync(generate_embeddings=embeddings)

            console.print("\n[green]Regenerate sync completed successfully![/green]")
            console.print(f"  Tracks added: {sync_result.tracks_added}")
            console.print(f"  Tracks updated: {sync_result.tracks_updated}")
            console.print(f"  Tracks removed: {sync_result.tracks_removed}")

        except KeyboardInterrupt:
            console.print("\n[yellow]Sync interrupted by user.[/yellow]")
            console.print("[green]Progress has been saved to database.[/green]")
            console.print("[yellow]Tip: Run 'plexmix sync regenerate' again to continue.[/yellow]")
            raise typer.Exit(130)


@sync_app.command("full")
def sync_full(
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
) -> None:
    console.print("[bold]Starting library sync (full is now an alias for incremental)...[/bold]")
    sync_incremental(embeddings=embeddings)
