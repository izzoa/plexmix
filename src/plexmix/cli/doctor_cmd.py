import logging

import typer
from typing import Any, Dict, NoReturn
from rich.console import Console

from ..config.settings import Settings
from ..database.index_rebuild import rebuild_index
from ..database.sqlite_manager import SQLiteManager
from ..database.vector_index import VectorIndex
from ..plex.sync import SyncEngine, plan_regenerate
from ..services.providers import (
    build_embedding_generator_with_reason as _build_embedding_generator_with_reason,
)
from ..services.regenerate_service import (
    SCOPE_EMBEDDINGS,
    SCOPE_TAGS,
    RegenerateRefused,
    check_providers,
    provider_secrets,
    run_provider,
)
from ..services.sync_service import build_vector_index
from .console_helpers import (
    NO_INDEX_SAVED_TEXT,
    NO_TRACKS_TEXT,
    REBUILD_INDEX_COMMAND,
    confirm_regenerate,
    index_was_saved,
    print_index_left_as_it_is,
    print_interrupted_before_any_change,
    print_regenerate_counts,
    print_regenerate_failed,
    print_regenerate_interrupted,
    print_regenerate_refused,
    print_regenerate_saved,
    print_unreadable_index,
    regenerate_question,
    regenerate_report,
    require_terminal_or_yes,
    say,
)
from .embedding_failure import print_embedding_failure

logger = logging.getLogger(__name__)

console = Console()

# The command that continues an unfinished force run: the same one.
_FORCE_COMMAND = "plexmix doctor --force"


def _tracks_in_the_way(db: SQLiteManager, settings: Settings) -> int:
    """How many tracks hold an embedding, and none of the embedding settings in use now.

    After a change of the embedding provider, of the model or of the vector size, a
    track can have a row for every track and still hold only a vector the search
    index cannot take (``plexmix.database.index_rebuild``). Such a library is not
    healthy, whatever the row counts say. Read-only, and it builds no provider and
    loads no index. 0 for a library that is whole, and when it cannot be told.
    """
    try:
        embeddings = run_provider(settings, SCOPE_EMBEDDINGS)
        count = db.count_tracks_with_only_other_embeddings(
            embeddings.provider, embeddings.dimension
        )
    except Exception as e:
        # The health check goes on without this line of it.
        logger.debug("Could not count the embeddings of other settings: %s", type(e).__name__)
        return 0
    return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 0


def doctor(
    force: bool = typer.Option(
        False,
        "--force",
        help="Regenerate the AI tags and embeddings of every track. Nothing is cleared first.",
    ),
    yes: bool = typer.Option(
        False, "--yes", help="With --force: regenerate without asking for confirmation"
    ),
) -> None:
    console.print("[bold]🩺 PlexMix Doctor - Database Health Check[/bold]")

    # Only a real True counts: an option that a caller of this function leaves out
    # arrives as a truthy placeholder object.
    if force is True:
        _force_regenerate(yes is True)
        return

    settings = Settings.load_from_file()
    preferred_provider = settings.ai.default_provider or "gemini"
    db_path = settings.database.get_db_path()

    with SQLiteManager(str(db_path)) as db:
        cursor = db.get_connection().cursor()

        cursor.execute("SELECT COUNT(*) FROM tracks")
        total_tracks = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT track_id) FROM embeddings")
        tracks_with_embeddings = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM embeddings
            WHERE track_id NOT IN (SELECT id FROM tracks)
        """
        )
        orphaned_count = cursor.fetchone()[0]

        console.print("\n[cyan]Database Status:[/cyan]")
        console.print(f"  Total tracks: {total_tracks}")
        console.print(f"  Tracks with embeddings: {tracks_with_embeddings}")
        console.print(f"  Orphaned embeddings: {orphaned_count}")

        missing_embeddings = total_tracks - tracks_with_embeddings

        if orphaned_count == 0 and missing_embeddings == 0:
            in_the_way = _tracks_in_the_way(db, settings)
            if in_the_way:
                # Every track has a row, but some rows were made with other embedding
                # settings: the library is not healthy, and the saved index is left as
                # it is until they are replaced. Say what to run.
                console.print()
                say(console, "No orphaned embeddings found. All tracks have embeddings.")
                print_index_left_as_it_is(console, in_the_way)
            else:
                console.print(
                    "\n[green]✓ No orphaned embeddings found. All tracks have embeddings."
                    " Database is healthy![/green]"
                )

            if typer.confirm("\nRun a sync to check for deleted tracks in Plex?", default=False):
                from .sync_cmd import run_incremental_sync

                console.print("\n[bold]Running sync...[/bold]")
                # True only when the sync printed its own success line. A sync that
                # stopped or held a removal has already said so (or ended the command).
                if run_incremental_sync(embeddings=False):
                    console.print("\n[green]✓ Sync completed![/green]")
            return

        if orphaned_count == 0 and missing_embeddings > 0:
            console.print(
                f"\n[yellow]⚠ Found {missing_embeddings} tracks without embeddings[/yellow]"
            )

        should_delete_orphaned = orphaned_count > 0
        should_generate_tags = False

        if should_delete_orphaned and typer.confirm(
            f"\nDelete {orphaned_count} orphaned embeddings?", default=True
        ):
            cursor.execute("DELETE FROM embeddings WHERE track_id NOT IN (SELECT id FROM tracks)")
            deleted = cursor.rowcount
            db._commit()
            console.print(f"[green]Deleted {deleted} orphaned embeddings[/green]")
        elif should_delete_orphaned:
            console.print("[yellow]Operation cancelled.[/yellow]")
            return

        # Check for untagged tracks first (tags should be generated before embeddings)
        cursor.execute('SELECT COUNT(*) FROM tracks WHERE tags IS NULL OR tags = ""')
        untagged_count = cursor.fetchone()[0]

        if untagged_count > 0:
            console.print(f"\n[cyan]Found {untagged_count} tracks without tags[/cyan]")
            if typer.confirm(
                "\nGenerate AI tags for untagged tracks first?" " (recommended before embeddings)",
                default=True,
            ):
                should_generate_tags = True
            else:
                console.print(
                    "\n[yellow]Skipping tag generation." " Tags improve embedding quality![/yellow]"
                )

        cursor.execute(
            "SELECT COUNT(*) FROM tracks"
            " WHERE id NOT IN (SELECT DISTINCT track_id FROM embeddings)"
        )
        tracks_needing_embeddings = cursor.fetchone()[0]

        console.print(f"\n[cyan]Tracks needing embeddings: {tracks_needing_embeddings}[/cyan]")

        if tracks_needing_embeddings > 0:
            if typer.confirm("\nRegenerate embeddings now?", default=True):
                console.print("\n[bold]Regenerating embeddings...[/bold]")

                embedding_generator, failure = _build_embedding_generator_with_reason(settings)
                if not embedding_generator:
                    print_embedding_failure(console, failure)
                    raise typer.Exit(1)

                index_path = settings.database.get_index_path()
                vector_index = VectorIndex(
                    dimension=embedding_generator.get_dimension(),
                    index_path=str(index_path),
                )
                print_unreadable_index(console, vector_index)

                from ..utils.embeddings import create_track_text
                from ..database.models import Embedding
                from rich.progress import (
                    Progress,
                    SpinnerColumn,
                    TextColumn,
                    BarColumn,
                    TaskProgressColumn,
                    TimeRemainingColumn,
                )

                all_tracks = db.get_all_tracks()
                tracks_to_embed = [
                    t
                    for t in all_tracks
                    if t.id is not None and not db.get_embedding_by_track_id(t.id)
                ]

                try:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        BarColumn(),
                        TaskProgressColumn(),
                        TimeRemainingColumn(),
                    ) as progress:
                        task = progress.add_task(
                            "Generating embeddings...", total=len(tracks_to_embed)
                        )

                        batch_size = 50
                        embeddings_saved = 0

                        for i in range(0, len(tracks_to_embed), batch_size):
                            batch_tracks = tracks_to_embed[i : i + batch_size]

                            track_data_list: list[Dict[str, Any]] = []
                            for track in batch_tracks:
                                artist = db.get_artist_by_id(track.artist_id)
                                album = db.get_album_by_id(track.album_id)

                                assert track.id is not None
                                track_data: Dict[str, Any] = {
                                    "id": track.id,
                                    "title": track.title,
                                    "artist": artist.name if artist else "Unknown",
                                    "album": album.title if album else "Unknown",
                                    "genre": track.genre or "",
                                    "year": track.year or "",
                                    "tags": track.tags or "",
                                }
                                track_data_list.append(track_data)

                            texts = [create_track_text(td) for td in track_data_list]
                            embeddings = embedding_generator.generate_batch_embeddings(
                                texts, batch_size=batch_size
                            )

                            for track_data, embedding_vector in zip(track_data_list, embeddings):
                                embedding = Embedding(
                                    track_id=track_data["id"],
                                    embedding_model=embedding_generator.provider_name,
                                    embedding_dim=embedding_generator.get_dimension(),
                                    vector=embedding_vector,
                                )
                                db.insert_embedding(embedding)
                                embeddings_saved += 1
                                progress.update(task, advance=1)

                    # The index is rebuilt from the rows of the active model, in the
                    # one place that does that. It is built and saved only when it
                    # is complete: while a track holds only an embedding that was
                    # made with other embedding settings (another provider, model
                    # or vector size), the saved index is left as it is.
                    rebuilt = rebuild_index(
                        db,
                        vector_index,
                        embedding_generator.provider_name,
                        index_path=str(index_path),
                    )

                    console.print(
                        f"\n[green]✓ Successfully generated"
                        f" {embeddings_saved} embeddings![/green]"
                    )
                    if rebuilt.refused:
                        print_index_left_as_it_is(console, rebuilt.left_out)
                    elif not index_was_saved(vector_index):
                        say(console, NO_INDEX_SAVED_TEXT, style="yellow")

                except KeyboardInterrupt:
                    console.print(
                        f"\n[yellow]⚠ Interrupted."
                        f" Saved {embeddings_saved} embeddings.[/yellow]"
                    )
                    console.print("[yellow]Run 'plexmix doctor' again to continue.[/yellow]")
                    raise typer.Exit(130)
            else:
                console.print(
                    "\n[yellow]Run 'plexmix sync' later" " to generate embeddings.[/yellow]"
                )

        console.print("\n[cyan]Checking for deleted tracks in Plex...[/cyan]")
        if typer.confirm("\nRun a sync to remove deleted tracks from database?", default=True):
            # The plain function, never the Typer command: called as a function, a
            # command's unpassed options arrive as truthy placeholder objects, and
            # that would switch --allow-removals on.
            from .sync_cmd import run_incremental_sync

            console.print("\n[bold]Running sync...[/bold]")
            if run_incremental_sync(embeddings=False):
                console.print("\n[green]✓ Sync completed![/green]")

    # Generate tags outside the database context
    if should_generate_tags:
        from .tags_cmd import tags_generate

        # Every option is passed: called as a function, a Typer command gets a
        # placeholder object for each option the caller leaves out, and comparing
        # one with a number raises.
        tags_generate(provider=preferred_provider, regenerate_embeddings=True, retag_stale=0)
        console.print("\n[green]✓ Tags generated![/green]")


def _force_regenerate(yes: bool) -> None:
    """``doctor --force``: replace the AI tags and the embedding of every track in place.

    Nothing is cleared first. A track keeps its tags and its embedding until the new
    ones are saved, so a run that is refused, fails or is interrupted leaves every
    track with what it had. The run marker lets the same command continue such a
    run. The order is fixed: the terminal check, the counts, the question, the
    provider check, the run.
    """
    # Nothing is loaded before the terminal check, and nothing is written and no
    # provider is asked before the answer.
    console.print()
    require_terminal_or_yes(console, yes, _FORCE_COMMAND)

    settings = Settings.load_from_file()
    db_path = settings.database.get_db_path()
    # The provider and model of each pass, from the settings. The question, the run
    # marker and the engine all take them from here, so that they compare the same.
    tags_run = run_provider(settings, SCOPE_TAGS)
    embeddings_run = run_provider(settings, SCOPE_EMBEDDINGS)

    # Looking at the library must not create one.
    if not db_path.exists():
        _nothing_to_regenerate()

    with SQLiteManager(str(db_path)) as db:
        cursor = db.get_connection().cursor()
        cursor.execute("SELECT COUNT(*) FROM tracks")
        total_tracks = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM embeddings")
        total_embeddings = cursor.fetchone()[0]
        if not total_tracks:
            _nothing_to_regenerate()

        console.print("[cyan]Current state:[/cyan]")
        console.print(f"  Total tracks: {total_tracks}")
        console.print(f"  Current embeddings: {total_embeddings}")

        plan = plan_regenerate(db, tags_run, embeddings_run)
        question = regenerate_question(plan, tags_run, embeddings_run)
        if not confirm_regenerate(console, question, yes):
            raise typer.Exit(0)

        # One small request to each provider, before anything is changed. A provider
        # that is not ready ends the command.
        console.print("\nChecking the AI provider and the embedding provider...")
        try:
            checked = check_providers(settings, need_ai=True, need_embeddings=True)
        except RegenerateRefused as e:
            print_regenerate_refused(console, e)
            raise typer.Exit(1)
        except KeyboardInterrupt:
            print_interrupted_before_any_change(console)
            raise typer.Exit(130)

        embedding_generator = checked.embedding_generator
        vector_index = None
        if embedding_generator is not None:
            vector_index = build_vector_index(settings, embedding_generator)
            print_unreadable_index(console, vector_index)

        console.print("\n[bold]Regenerating AI tags and embeddings...[/bold]")
        # No Plex client: the run works on the tracks the database holds.
        engine = SyncEngine(
            None,
            db,
            embedding_generator,
            vector_index,
            checked.ai_provider,
            # The keys from the settings, so that no error text of a provider can
            # carry one into the output or the log.
            known_secrets=provider_secrets(settings),
        )
        try:
            engine.regenerate_local(
                generate_embeddings=True,
                tags_provider=tags_run,
                embeddings_provider=embeddings_run,
            )
        except RegenerateRefused as e:
            # The engine's own guard: it was given no provider or no index. Nothing
            # was read or written.
            print_regenerate_refused(console, e)
            raise typer.Exit(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]⚠ Interrupted.[/yellow]")
            _print_stopped_run(engine)
            raise typer.Exit(130)
        except Exception:
            # Not a provider that stopped a pass: the engine ends such a run itself and
            # its result says so. What the run had saved is said before the error is
            # reported.
            _print_stopped_run(engine)
            raise

        # The report decides the outcome: a run that regenerated nothing, or that a
        # provider error stopped part-way, gets no success line and ends with status 1.
        result = engine.regenerate_result
        report = regenerate_report(result)
        console.print()
        if report.failed:
            print_regenerate_failed(console, report, _FORCE_COMMAND)
            raise typer.Exit(1)

        # A run that left tracks with their previous data gets no success line, and
        # neither does one that ended without a rebuilt search index: a file that
        # could not be loaded is still in its place, or tracks hold an embedding
        # that was made with other embedding settings (the index was then left as
        # it is, and the sentence names the count and the command).
        index_saved = index_was_saved(vector_index)
        if report.clean and index_saved:
            console.print("[green]✓ Force regeneration complete![/green]")
        else:
            console.print("[yellow]Force regeneration finished with warnings.[/yellow]")
        print_regenerate_counts(console, report)
        if result.other_model_rows_removed:
            say(
                console,
                f"  Removed {result.other_model_rows_removed:,} embeddings of other models.",
            )
        if not index_saved:
            say(
                console,
                f"{NO_INDEX_SAVED_TEXT} Move it away, then run '{REBUILD_INDEX_COMMAND}'.",
                style="yellow",
            )
        print_index_left_as_it_is(console, report.index_left_out)


def _nothing_to_regenerate() -> NoReturn:
    """End ``doctor --force`` for a library without tracks. Nothing was changed.

    Nothing to do is no failure, so the command ends with status 0: no question was
    asked, no provider was asked, and no run marker was written.
    """
    say(console, NO_TRACKS_TEXT, style="yellow")
    say(console, "Run 'plexmix sync' first.")
    raise typer.Exit(0)


def _print_stopped_run(engine: SyncEngine) -> None:
    """After Ctrl+C or an error ended a force run: what it regenerated, and what to run.

    The same command continues a run that did not reach its end. When both passes
    had reached their end, nothing is left to continue, and the same command would
    start over with every track. Only the search index can be behind then, and the
    embeddings command rebuilds it from the saved rows without asking a provider.
    """
    result = engine.regenerate_result
    report = regenerate_report(result)
    if print_regenerate_saved(console, result, report):
        return
    print_regenerate_interrupted(console, report, _FORCE_COMMAND)
