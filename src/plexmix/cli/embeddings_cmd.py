from pathlib import Path
from typing import Any, List, Optional

import typer
from rich.console import Console

from ..config.constants import EMBEDDING_BATCH_SIZE
from ..config.settings import Settings
from ..database.sqlite_manager import SQLiteManager
from ..database.vector_index import VectorIndex
from ..plex.sync import finish_embeddings_pass, follow_rows_after_stopped_pass
from ..services.providers import (
    build_embedding_generator_with_reason as _build_embedding_generator_with_reason,
    resolve_embedding_api_key,
)
from ..services.regenerate_service import (
    COMPONENT_EMBEDDINGS,
    SCOPE_EMBEDDINGS,
    RegenerateRefused,
    RegenerateRun,
    RunProvider,
    check_providers,
    open_run,
    preview_run,
    run_provider,
)
from ..services.sync_service import build_vector_index
from ..services.tagging_service import generate_embeddings_for_tracks, rebuild_vector_index
from .console_helpers import (
    EMBEDDINGS_LABEL,
    NO_INDEX_SAVED_TEXT,
    NO_TRACKS_TEXT,
    RegenerateQuestion,
    build_regenerate_report,
    confirm_regenerate,
    embeddings_count,
    index_left_out,
    index_was_saved,
    last_index_rebuild,
    plain_error_text,
    print_index_left_as_it_is,
    print_interrupted_before_any_change,
    print_regenerate_counts,
    print_regenerate_failed,
    print_regenerate_interrupted,
    print_regenerate_refused,
    print_unreadable_index,
    provider_text,
    require_terminal_or_yes,
    say,
)
from .embedding_failure import print_embedding_failure

embeddings_app = typer.Typer(
    name="embeddings", help="Embedding generation", pretty_exceptions_show_locals=False
)
console = Console()

# The command that continues an unfinished regenerate run: the same one.
_REGENERATE_COMMAND = "plexmix embeddings generate --regenerate"


def _print_index_result(vector_index: Any, in_index: int, verb: str, end: str = "") -> bool:
    """Say what became of the search index once it was rebuilt from the stored embeddings.

    Call it after ``rebuild_vector_index`` or ``finish_embeddings_pass``: both go
    through ``index_rebuild.rebuild_index``, which leaves its result on the index.

    The green line says "saved" or "rebuilt" (``verb``) with the ``in_index``
    vectors of the index, and only when the index was built and written. It is not
    printed, and one yellow line is, in two cases:

    - Tracks hold an embedding that was made with other embedding settings. An
      index of the rows that fit would cover only part of the library, so nothing
      was built and the saved index is as it was. The line names the count and the
      command that replaces those embeddings.
    - A file that could not be loaded and could not be moved aside is still in the
      place of the index. It is never written over.

    Nothing is said of an index when there is no embedding to build one from.
    Returns True when the green line was printed.
    """
    if print_index_left_as_it_is(console, index_left_out(vector_index)):
        return False
    if not index_was_saved(vector_index):
        say(console, NO_INDEX_SAVED_TEXT, style="yellow")
        return False
    rebuilt = last_index_rebuild(vector_index)
    if rebuilt is not None and not rebuilt.saved:
        # No row at all: no index was built, and none was written.
        return False
    console.print(f"[green]✓ Vector index {verb} with {in_index} {end}[/green]")
    return True


@embeddings_app.command("generate")
def embeddings_generate(
    regenerate: bool = typer.Option(False, help="Regenerate all embeddings (including existing)"),
    yes: bool = typer.Option(
        False, "--yes", help="With --regenerate: regenerate without asking for confirmation"
    ),
) -> None:
    # Only a real True counts. An option that a caller of this function leaves out
    # arrives as a truthy placeholder object.
    if regenerate is True:
        _regenerate_embeddings(yes is True)
        return

    console.print("[bold]Generating embeddings for tracks...[/bold]")

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()

    embedding_generator, failure = _build_embedding_generator_with_reason(settings)
    if not embedding_generator:
        print_embedding_failure(console, failure)
        raise typer.Exit(1)

    vector_index = build_vector_index(settings, embedding_generator)
    print_unreadable_index(console, vector_index)
    index_path = str(settings.database.get_index_path())

    with SQLiteManager(str(db_path)) as db:
        all_tracks = db.get_all_tracks()

        # Scope existence to the active model: after a provider/model switch,
        # tracks embedded only by the previous model must be re-embedded.
        existing_ids = db.get_track_ids_with_embeddings(embedding_generator.provider_name)
        tracks_to_embed = [t for t in all_tracks if t.id is not None and t.id not in existing_ids]
        console.print(f"Found {len(tracks_to_embed)} tracks without embeddings")

        if not tracks_to_embed:
            # Still rebuild the FAISS index from the embeddings already in the DB,
            # so a missing/lost/corrupt index is recovered rather than left broken.
            # It is built and saved only when it is complete.
            total_in_index = rebuild_vector_index(
                db, vector_index, index_path, embedding_generator.provider_name
            )
            left_out = index_left_out(vector_index)
            if left_out:
                # Every track has a row of this provider, and some rows were made
                # with other embedding settings (another vector size). Those tracks
                # do not have the embedding the provider makes now, so the line
                # "All tracks already have embeddings!" is not true of them, and
                # the saved index was left as it is.
                print_index_left_as_it_is(console, left_out)
                return
            console.print("[green]All tracks already have embeddings![/green]")
            _print_index_result(vector_index, total_in_index, "rebuilt", "embeddings.")
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

        embeddings_saved = 0  # bound before the try so the interrupt handler is safe
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

            total_in_index = rebuild_vector_index(
                db, vector_index, index_path, embedding_generator.provider_name
            )
            console.print(
                f"\n[green]✓ Successfully generated {embeddings_saved} embeddings![/green]"
            )
            _print_index_result(vector_index, total_in_index, "saved", "total embeddings")

        except KeyboardInterrupt:
            console.print(f"\n[yellow]⚠ Interrupted. Saved {embeddings_saved} embeddings.[/yellow]")
            console.print("[yellow]Run 'plexmix embeddings generate' again to continue.[/yellow]")
            raise typer.Exit(130)


def _preview_regenerate(db_path: Path, provider: RunProvider) -> Optional[RegenerateRun]:
    """What a regenerate run would do with the library as it is now. Nothing is written.

    None when there is no database yet: looking at the library must not create one.
    """
    if not db_path.exists():
        return None
    with SQLiteManager(str(db_path)) as db:
        return preview_run(db, SCOPE_EMBEDDINGS, provider)


def _index_follows_rows(db: SQLiteManager, vector_index: VectorIndex, model: str) -> None:
    """After a pass that stopped part-way: let the index follow the rows, new and previous.

    Only when the index is complete then (``index_rebuild.rebuild_index``). After a
    change of the provider, of the model or of the vector size, the tracks the pass
    did not reach hold an embedding that was made with other settings. The saved
    index then stays as it is, and so does its dimension mismatch, until a run has
    replaced every track. Nothing is printed for that: the stop is what the command
    reports, and the log holds the line.
    """
    follow_rows_after_stopped_pass(db, vector_index, model)


def _regenerate_embeddings(yes: bool) -> None:
    """``embeddings generate --regenerate``: replace every embedding in place.

    Nothing is deleted first. A track keeps its embedding until the new one is saved
    over it, so a run that is refused, fails or is interrupted leaves every track
    with an embedding. The run marker lets the same command continue such a run.
    The order is fixed: the terminal check, the question, the provider check, the run.
    """
    require_terminal_or_yes(console, yes, _REGENERATE_COMMAND)

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()
    # The provider and model from the settings: the question and the run marker
    # compare the same values.
    provider = run_provider(settings, SCOPE_EMBEDDINGS)

    preview = _preview_regenerate(db_path, provider)
    if preview is None or preview.total_tracks == 0:
        # Nothing to do is no failure: no question, no provider request, no marker.
        say(console, NO_TRACKS_TEXT, style="yellow")
        say(console, "Run 'plexmix sync' first.")
        raise typer.Exit(0)

    total = preview.total_tracks
    # An earlier run that replaced nothing is not worth a word: every track is to do.
    continued = preview.continued and preview.remaining < total
    question = RegenerateQuestion(
        what="embeddings",
        total=total,
        remaining=preview.remaining if continued else total,
        continued=continued,
        providers=((EMBEDDINGS_LABEL, provider_text(provider)),),
        marker_path=preview.marker_path,
    )
    if not confirm_regenerate(console, question, yes):
        raise typer.Exit(0)

    # One small request to the provider, before anything is changed.
    console.print("\nChecking the embedding provider...")
    try:
        checked = check_providers(settings, need_ai=False, need_embeddings=True)
        embedding_generator = checked.embedding_generator
        if embedding_generator is None:
            # ``check_providers`` returns the generator it was asked for, or refuses.
            raise RegenerateRefused.not_given(COMPONENT_EMBEDDINGS)
    except RegenerateRefused as e:
        print_regenerate_refused(console, e)
        raise typer.Exit(1)
    except KeyboardInterrupt:
        print_interrupted_before_any_change(console)
        raise typer.Exit(130)

    vector_index = build_vector_index(settings, embedding_generator)
    print_unreadable_index(console, vector_index)
    model = embedding_generator.provider_name
    secrets = [resolve_embedding_api_key(provider.provider), settings.embedding.custom_api_key]

    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
        TimeRemainingColumn,
    )

    console.print("\n[bold]Regenerating embeddings...[/bold]")
    with SQLiteManager(str(db_path)) as db:
        # Bound before the try, so the interrupt handler is safe.
        tracks: List[Any] = []
        embeddings_saved = 0
        # Tracks the run this one continues had replaced (0 for a new run).
        earlier = 0
        run: Optional[RegenerateRun] = None
        error: Optional[Exception] = None
        try:
            to_do = None
            try:
                run = open_run(db, SCOPE_EMBEDDINGS, provider)
                to_do = set(run.track_ids)
                if run.continued:
                    earlier = max(run.total_tracks - run.remaining, 0)
            except OSError as e:
                # Only the chance to continue this run later is lost: every track is to do.
                say(
                    console,
                    f"The run marker could not be written ({type(e).__name__}), so this run "
                    "cannot be continued later.",
                    style="yellow",
                )
            tracks = [
                track
                for track in db.get_all_tracks()
                if track.id is not None and (to_do is None or track.id in to_do)
            ]

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
            ) as progress:
                task = progress.add_task("Regenerating embeddings...", total=len(tracks))

                def on_progress(generated: int, _total: int) -> None:
                    # Called after each batch is saved, so the count is of rows that exist.
                    nonlocal embeddings_saved
                    embeddings_saved = generated
                    progress.update(task, completed=generated)

                try:
                    generate_embeddings_for_tracks(
                        db,
                        embedding_generator,
                        tracks,
                        batch_size=EMBEDDING_BATCH_SIZE,
                        progress_callback=on_progress,
                    )
                except Exception as e:
                    # The pass ends here and the command reports it. Every track that
                    # was not reached keeps the embedding it has.
                    error = e

        except KeyboardInterrupt:
            if embeddings_saved:
                _index_follows_rows(db, vector_index, model)
            console.print("\n[yellow]⚠ Interrupted.[/yellow]")
            interrupted = build_regenerate_report(
                [embeddings_count(embeddings_saved, len(tracks) - embeddings_saved, earlier)]
            )
            print_regenerate_interrupted(console, interrupted, _REGENERATE_COMMAND)
            raise typer.Exit(130)

        stopped = ""
        if error is not None:
            stopped = f"Embedding generation stopped: {plain_error_text(error, secrets)}"
        report = build_regenerate_report(
            [embeddings_count(embeddings_saved, len(tracks) - embeddings_saved, earlier)], stopped
        )

        other_models_removed = 0
        if error is None and (embeddings_saved or earlier):
            # The pass reached its end: its marker entry is cleared when no track is left
            # to do (it stays while the provider returned no vector for some track, so
            # that the next run makes only those), the rows of other models go when
            # every track has its new embedding, and the index is rebuilt. A continued
            # run whose remaining tracks all got nothing is closed the same way.
            try:
                other_models_removed = finish_embeddings_pass(db, vector_index, model, run)
            except (KeyboardInterrupt, Exception) as e:
                # The rows are saved and the run is closed. Only the index is behind,
                # and the plain command rebuilds it from the rows without a request.
                interrupted_here = isinstance(e, KeyboardInterrupt)
                reason = "interrupted" if interrupted_here else plain_error_text(e, secrets)
                console.print()
                say(
                    console,
                    "The embeddings were saved, but the search index was not rebuilt: "
                    f"{reason.rstrip('.')}.",
                    style="red",
                )
                print_regenerate_counts(console, report)
                say(console, "Run 'plexmix embeddings generate' to rebuild the search index.")
                raise typer.Exit(130 if interrupted_here else 1)
        elif embeddings_saved:
            _index_follows_rows(db, vector_index, model)

        console.print()
        if report.failed:
            print_regenerate_failed(console, report, _REGENERATE_COMMAND)
            raise typer.Exit(1)

        # The pass reached its end. Its last step rebuilt the index from the rows,
        # unless tracks are still in the way: the saved index was then left as it is.
        left_out = index_left_out(vector_index)
        index_saved = index_was_saved(vector_index)
        if report.kept_some or left_out or not index_saved:
            console.print("[yellow]Embeddings regenerated with warnings.[/yellow]")
        else:
            console.print("[green]✓ Embeddings regenerated successfully![/green]")
        print_regenerate_counts(console, report)
        if other_models_removed:
            say(console, f"  Removed {other_models_removed:,} embeddings of other models.")
        _print_index_result(vector_index, len(vector_index.track_ids), "saved", "total embeddings")
