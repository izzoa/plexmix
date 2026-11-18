import typer
from typing import Any, Optional
from rich.console import Console

from ..config.constants import EMBEDDING_BATCH_SIZE, TAG_BATCH_SIZE
from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager
from ..ai.tag_generator import TagGenerator
from ..ai.errors import FatalProviderError
from ..services.providers import (
    canonical_ai_provider as _canonical_ai_provider,
    resolve_ai_api_key as _resolve_ai_api_key,
    build_ai_provider as _build_ai_provider,
    build_embedding_generator as _build_embedding_generator,
)
from ..services.sync_service import build_vector_index
from ..services.tagging_service import generate_embeddings_for_tracks, rebuild_vector_index

tags_app = typer.Typer(
    name="tags", help="AI-based tag generation", pretty_exceptions_show_locals=False
)
console = Console()


def _join_tag_list(value: Any) -> Optional[str]:
    """Join a tag list into the comma-separated storage form, or None if empty."""
    if isinstance(value, list):
        return ",".join(value) if value else None
    return value or None


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
    # Only providers that actually require a key are blocked when it's missing;
    # the custom (OpenAI-compatible) provider is keyless-capable, like local.
    from ..services.registry import requires_ai_api_key

    if requires_ai_api_key(canonical) and not api_key:
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
        # Accumulate across ALL batches; using the last batch's tags_dict alone
        # meant embeddings were regenerated for only the final batch's tracks.
        newly_tagged_track_ids: set = set()
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

                    try:
                        tags_dict = tag_generator.generate_tags_batch(batch, batch_size=batch_size)
                    except FatalProviderError as e:
                        console.print(f"\n[red]AI tagging stopped:[/red] {e.user_message}")
                        break

                    for track in tracks_needing_tags:
                        if track.id is None:
                            continue
                        tag_data = tags_dict.get(track.id)
                        if tag_data is None:
                            continue

                        if isinstance(tag_data, dict):
                            tags_list = tag_data.get("tags") or []
                            env_list = tag_data.get("environments") or []
                            inst_list = tag_data.get("instruments") or []
                        else:
                            tags_list = tag_data if isinstance(tag_data, list) else []
                            env_list = []
                            inst_list = []

                        # Skip results with no actual content so failed/empty
                        # batches aren't counted as updated or re-embedded.
                        if not (tags_list or env_list or inst_list):
                            continue

                        # update_track_tags stamps tags_generated_at (insert_track
                        # did not), so CLI-tagged tracks are no longer permanently
                        # classified as stale.
                        db.update_track_tags(
                            track.id,
                            tags=_join_tag_list(tags_list),
                            environments=_join_tag_list(env_list),
                            instruments=_join_tag_list(inst_list),
                        )
                        newly_tagged_track_ids.add(track.id)
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

            # Use the ids accumulated across all batches (not just the last one).
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

                rebuild_vector_index(
                    db, vector_index, index_path, embedding_generator.provider_name
                )
                console.print(
                    f"[green]Regenerated {embeddings_saved} embeddings with tags![/green]"
                )

            except KeyboardInterrupt:
                console.print("\n[yellow]Embedding regeneration interrupted by user.[/yellow]")
                console.print(
                    "[yellow]Note: Vector index not rebuilt. Run 'plexmix sync' to rebuild.[/yellow]"
                )
                return
