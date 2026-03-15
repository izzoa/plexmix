import typer
from rich.console import Console

from ..config.constants import EMBEDDING_BATCH_SIZE, TAG_BATCH_SIZE
from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager
from ..ai.tag_generator import TagGenerator
from ..services.providers import (
    canonical_ai_provider as _canonical_ai_provider,
    resolve_ai_api_key as _resolve_ai_api_key,
    build_ai_provider as _build_ai_provider,
    build_embedding_generator as _build_embedding_generator,
)
from ..services.sync_service import build_vector_index
from ..services.tagging_service import generate_embeddings_for_tracks, rebuild_vector_index

tags_app = typer.Typer(name="tags", help="AI-based tag generation")
console = Console()


@tags_app.command("generate")
def tags_generate(
    provider: str = typer.Option("gemini", help="AI provider (gemini, openai, claude, local)"),
    regenerate_embeddings: bool = typer.Option(True, help="Regenerate embeddings after tagging"),
    retag_stale: int = typer.Option(
        0,
        help="Also retag tracks whose tags are older than N days (0 = only untagged)",
    ),
) -> None:
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

        # Untagged tracks
        tracks_needing_tags = [t for t in all_tracks if not t.tags or not t.get_tags_list()]

        # Stale tracks (have tags, but generated > N days ago)
        stale_tracks: list = []
        if retag_stale > 0:
            stale_rows = db.get_tracks_by_filter(stale_days=retag_stale)
            stale_ids = {r["id"] for r in stale_rows}
            stale_tracks = [t for t in all_tracks if t.id in stale_ids]
            if stale_tracks:
                console.print(
                    f"Found {len(stale_tracks)} tracks with tags older than {retag_stale} days"
                )
            tracks_needing_tags.extend(stale_tracks)

        if not tracks_needing_tags:
            console.print("[green]All tracks already have up-to-date tags![/green]")
            return

        label = f"{len(tracks_needing_tags)} tracks"
        if stale_tracks:
            untagged = len(tracks_needing_tags) - len(stale_tracks)
            label = f"{untagged} untagged + {len(stale_tracks)} stale"
        console.print(f"Found {label} tracks to tag")

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
            track_data_list.append(
                {
                    "id": track.id,
                    "title": track.title,
                    "artist": artist.name if artist else "Unknown",
                    "genre": track.genre or "unknown",
                }
            )

        updated_count = 0
        batch_size = TAG_BATCH_SIZE

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
                task = progress.add_task("Generating tags...", total=len(track_data_list))

                for i in range(0, len(track_data_list), batch_size):
                    batch = track_data_list[i : i + batch_size]
                    batch_num = i // batch_size + 1
                    total_batches = (len(track_data_list) + batch_size - 1) // batch_size

                    progress.update(
                        task,
                        description=f"Generating tags (batch {batch_num}/{total_batches})...",
                    )

                    tags_dict = tag_generator.generate_tags_batch(batch, batch_size=batch_size)

                    for track in tracks_needing_tags:
                        if track.id in tags_dict and tags_dict[track.id]:
                            tag_data = tags_dict[track.id]
                            if isinstance(tag_data, dict):
                                track.set_tags_list(tag_data.get("tags", []))
                                environments = tag_data.get("environments", [])
                                if isinstance(environments, list):
                                    track.environments = (
                                        ", ".join(environments) if environments else None
                                    )
                                else:
                                    track.environments = environments
                                instruments = tag_data.get("instruments", [])
                                if isinstance(instruments, list):
                                    track.instruments = (
                                        ", ".join(instruments) if instruments else None
                                    )
                                else:
                                    track.instruments = instruments
                            else:
                                track.set_tags_list(tag_data if isinstance(tag_data, list) else [])
                            db.insert_track(track)
                            updated_count += 1

                    progress.update(task, advance=len(batch))

        except KeyboardInterrupt:
            console.print("\n[yellow]Tag generation interrupted by user.[/yellow]")
            console.print(
                f"[green]Successfully saved {updated_count} tracks with tags before interruption.[/green]"
            )
            if updated_count > 0 and regenerate_embeddings:
                console.print(
                    "[yellow]Tip: Run the command again to continue tagging remaining tracks.[/yellow]"
                )

        console.print(f"[green]Updated {updated_count} tracks with tags![/green]")

        if regenerate_embeddings and updated_count > 0:
            console.print("\n[bold]Regenerating embeddings for newly tagged tracks...[/bold]")

            embedding_generator = _build_embedding_generator(settings)
            if not embedding_generator:
                console.print("[yellow]API key required for embedding provider. Skipping.[/yellow]")
                return

            vector_index = build_vector_index(settings, embedding_generator)
            index_path = str(settings.database.get_index_path())

            newly_tagged_track_ids = {
                track.id
                for track in tracks_needing_tags
                if track.id in tags_dict and tags_dict[track.id]
            }
            tagged_tracks = [t for t in tracks_needing_tags if t.id in newly_tagged_track_ids]

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
                    task = progress.add_task("Regenerating embeddings...", total=len(tagged_tracks))

                    def on_progress(generated: int, total: int) -> None:
                        progress.update(task, completed=generated)

                    embeddings_saved = generate_embeddings_for_tracks(
                        db,
                        embedding_generator,
                        tagged_tracks,
                        batch_size=EMBEDDING_BATCH_SIZE,
                        progress_callback=on_progress,
                    )

                total_in_index = rebuild_vector_index(db, vector_index, index_path)
                console.print(
                    f"[green]Regenerated {embeddings_saved} embeddings with tags![/green]"
                )

            except KeyboardInterrupt:
                console.print("\n[yellow]Embedding regeneration interrupted by user.[/yellow]")
                console.print(
                    "[yellow]Note: Vector index not rebuilt. Run 'plexmix sync' to rebuild.[/yellow]"
                )
                return
