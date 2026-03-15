import typer
from rich.console import Console

from ..config.constants import EMBEDDING_BATCH_SIZE
from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager
from ..services.providers import build_embedding_generator as _build_embedding_generator
from ..services.sync_service import build_vector_index
from ..services.tagging_service import generate_embeddings_for_tracks, rebuild_vector_index

embeddings_app = typer.Typer(name="embeddings", help="Embedding generation")
console = Console()


@embeddings_app.command("generate")
def embeddings_generate(
    regenerate: bool = typer.Option(False, help="Regenerate all embeddings (including existing)"),
) -> None:
    console.print("[bold]Generating embeddings for tracks...[/bold]")

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    embedding_generator = _build_embedding_generator(settings)
    if not embedding_generator:
        console.print("[red]API key required for embedding provider.[/red]")
        console.print("Run: plexmix config init")
        raise typer.Exit(1)

    vector_index = build_vector_index(settings, embedding_generator)
    index_path = str(settings.database.get_index_path())

    with SQLiteManager(str(db_path)) as db:
        all_tracks = db.get_all_tracks()

        if regenerate:
            console.print(
                f"[yellow]Regenerating ALL embeddings for {len(all_tracks)} tracks[/yellow]"
            )
            cursor = db.get_connection().cursor()
            cursor.execute("DELETE FROM embeddings")
            db._commit()
            tracks_to_embed = all_tracks
        else:
            tracks_to_embed = [
                t for t in all_tracks if t.id is not None and not db.get_embedding_by_track_id(t.id)
            ]
            console.print(f"Found {len(tracks_to_embed)} tracks without embeddings")

        if not tracks_to_embed:
            console.print("[green]All tracks already have embeddings![/green]")
            console.print("Use --regenerate to regenerate all embeddings.")
            return

        from rich.progress import (
            Progress,
            SpinnerColumn,
            TextColumn,
            BarColumn,
            TaskProgressColumn,
            TimeRemainingColumn,
        )

        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Generating embeddings...", total=len(tracks_to_embed))

                def on_progress(generated: int, total: int) -> None:
                    progress.update(task, completed=generated)

                embeddings_saved = generate_embeddings_for_tracks(
                    db,
                    embedding_generator,
                    tracks_to_embed,
                    batch_size=EMBEDDING_BATCH_SIZE,
                    progress_callback=on_progress,
                )

            total_in_index = rebuild_vector_index(db, vector_index, index_path)
            console.print(
                f"\n[green]✓ Successfully generated {embeddings_saved} embeddings![/green]"
            )
            console.print(
                f"[green]✓ Vector index saved with {total_in_index} total embeddings[/green]"
            )

        except KeyboardInterrupt:
            console.print(f"\n[yellow]⚠ Interrupted. Saved {embeddings_saved} embeddings.[/yellow]")
            console.print("[yellow]Run 'plexmix embeddings generate' again to continue.[/yellow]")
            raise typer.Exit(130)
