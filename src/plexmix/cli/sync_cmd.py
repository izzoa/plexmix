from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.console import Console

from ..config.settings import Settings
from ..database.models import SyncHistory
from ..database.sqlite_manager import SQLiteManager
from ..plex.client import READ_KINDS, PlexFetchError
from ..plex.sync import (
    REASON_THRESHOLD,
    REASON_TOO_MANY_UNREAD,
    REMOVAL_CONFIRM_PERCENT,
    REMOVAL_CONFIRM_TRACKS,
    REMOVAL_LISTED_AGAIN,
    REMOVAL_MORE_MISSING,
    REMOVAL_REMOVED,
    RemovalResult,
    SyncEngine,
    SyncOutcome,
    too_many_failed_reads,
)
from ..services.providers import (
    build_ai_provider as _build_ai_provider,
    build_embedding_generator as _build_embedding_generator,
)
from ..services.sync_service import PlexConnectionError, connect_plex, build_vector_index
from ..utils.redaction import mask_urls_in_text
from .console_helpers import can_prompt, format_size

sync_app = typer.Typer(
    name="sync",
    help="Library synchronization",
    invoke_without_command=True,
    pretty_exceptions_show_locals=False,
)
console = Console()

_ALLOW_REMOVALS_HELP = (
    "Remove tracks that Plex no longer lists without asking, even when that is more than "
    f"{REMOVAL_CONFIRM_TRACKS} tracks and more than {REMOVAL_CONFIRM_PERCENT} percent of "
    "the library. A backup is made first."
)


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


def _saved_index(settings: Settings) -> Dict[str, Any]:
    """Where the saved search index is, as arguments for ``SyncEngine``.

    A sync without embeddings has no index object. With these the engine still
    copies the index files into the backup of a large removal, and drops the removed
    tracks' vectors from the index once that backup is made.
    """
    provider = (settings.embedding.default_provider or "").strip().lower()
    return {
        "saved_index_path": settings.database.get_index_path(),
        "saved_index_dimension": settings.embedding.get_dimension_for_provider(provider),
    }


def _allowed_by_group(ctx: typer.Context) -> bool:
    """True when ``--allow-removals`` stands before the subcommand name.

    ``plexmix sync --allow-removals full`` gives the flag to the ``sync`` group, not
    to ``full``. It means the same in both places.
    """
    group = ctx.parent
    return group is not None and group.params.get("allow_removals") is True


def _say(text: str, style: Optional[str] = None, soft_wrap: bool = False) -> None:
    """Print a line that holds values (counts, a folder, an error text) exactly as it is.

    No console markup and no emoji code is read from the text, so a folder name or an
    error text with square brackets or colons is shown unchanged.
    """
    console.print(text, style=style, markup=False, emoji=False, soft_wrap=soft_wrap)


def _tracks(count: int) -> str:
    """``1 track`` or ``5,443 tracks``."""
    return f"{count:,} track{'' if count == 1 else 's'}"


def _print_sync_stopped(error: Exception) -> None:
    """Print the one sentence of a sync that stopped before it changed anything.

    That is a sync that Plex stopped (``PlexFetchError``) or whose connection check
    failed before it started. An error of a later stage is not printed here.
    """
    sentence = mask_urls_in_text(str(error)).strip() or "Plex could not be read"
    if not sentence.endswith((".", "!", "?")):
        sentence += "."
    console.print()
    _say(sentence, style="red")


def _stopped_before_any_change(sync_engine: Any, error: BaseException) -> bool:
    """True when ``error`` is the engine's own connection check failing.

    The engine raises a built-in ``ConnectionError`` for that check, before it reads
    or writes anything. Once its compare stage has saved its work, a connection
    error comes from a later stage (an embedding provider, for one) and is not about
    Plex: it must not be reported as a sync that changed nothing.
    """
    if not isinstance(error, ConnectionError):
        return False
    outcome = getattr(sync_engine, "outcome", None)
    return not (isinstance(outcome, SyncOutcome) and outcome.compared is True)


def _print_later_stage_stopped(error: BaseException) -> None:
    """Print the sentence of a sync that lost a connection after its compare stage.

    That is not Plex: the library sync was done by then. It is a later stage, such
    as an embedding provider whose worker went away. The sentence is printed here
    because a broken pipe that leaves a command ends it without a word.
    """
    reason = mask_urls_in_text(str(error)).strip().rstrip(".") or type(error).__name__
    console.print()
    _say(f"The sync stopped after the library was compared with Plex: {reason}.", style="red")


def _print_changes_so_far(sync_engine: Any) -> None:
    """After a sync that was interrupted or failed: say what it had already saved.

    The compare stage and the removal step save their work when they end, so a stop
    in a later stage leaves it in the library. A backup that was made is named even
    when the removal after it did not happen.
    """
    outcome = getattr(sync_engine, "outcome", None)
    if not isinstance(outcome, SyncOutcome):
        return
    if outcome.compared is True and (outcome.added or outcome.updated or outcome.removed):
        _say("Saved before the sync stopped:", style="yellow")
        console.print(f"  Tracks added: {outcome.added}")
        console.print(f"  Tracks updated: {outcome.updated}")
        console.print(f"  Tracks removed: {outcome.removed}")
    if isinstance(outcome.backup_folder, Path) and not outcome.removed:
        # The backup of a large removal was made, and the removal did not happen.
        _say("No track was removed.", style="yellow")
    _print_backup(outcome.backup_folder, outcome.backup_size_bytes)


def _print_interrupted_removal(sync_engine: Any) -> None:
    """After Ctrl+C in a confirmed removal: say whether tracks are gone, and name the backup."""
    state = getattr(sync_engine, "last_removal", None)
    if state is None:
        # Interrupted before the backup was finished.
        _say("Nothing was removed.", style="yellow")
        return
    if not isinstance(state, RemovalResult):
        return
    if state.removed > 0:
        _say(
            f"{_tracks(state.removed)} had already been removed from the PlexMix library.",
            style="yellow",
        )
    else:
        _say("Nothing was removed.", style="yellow")
    _print_backup(state.backup_folder, state.backup_size_bytes)


def _print_cleared_note(sync_engine: Any) -> None:
    """Say that ``sync regenerate`` had already cleared the tags and embeddings.

    ``regenerate_sync`` still clears them before it reads Plex, so a run that stops
    after that did change something. Remove this when the engine stops clearing first.
    """
    if getattr(sync_engine, "cleared_before_sync", False) is True:
        console.print(
            "[yellow]The existing tags and embeddings had already been cleared. "
            "Run 'plexmix sync' to rebuild them.[/yellow]"
        )


def _print_backup(folder: Optional[Path], size_bytes: int) -> None:
    """Say where the backup of a large removal is and how large it is."""
    if not isinstance(folder, Path):
        return
    # ``soft_wrap`` keeps the folder in one piece, so it can be copied from the terminal.
    _say(
        f"  Backup made before the removal: {folder} ({format_size(size_bytes)}). "
        "PlexMix keeps it until you delete it.",
        soft_wrap=True,
    )


def _unread_lines(outcome: SyncOutcome) -> List[str]:
    """One sentence per kind of item that could not be read and was kept."""
    lines: List[str] = []
    for kind in READ_KINDS:
        # The message of a run with too many failed reads already gives that kind's counts.
        named = outcome.reason == REASON_TOO_MANY_UNREAD and too_many_failed_reads(
            outcome.unread.get(kind, 0), outcome.listed.get(kind, 0)
        )
        sentence = "" if named else outcome.unread_sentence(kind)
        if sentence:
            lines.append(sentence)
    return lines


def _settle_held_removal(sync_engine: SyncEngine, outcome: SyncOutcome) -> None:
    """Ask about the removal that the sync held back, and apply it on Yes.

    Without a terminal nobody can answer, so the tracks stay and the command ends
    with status 1. It also ends with status 1 when a confirmed removal could not be
    applied. It returns after No, after a removal, and when Plex lists the tracks again.
    """
    held = outcome.held
    total = outcome.library_total
    kept = f"{_tracks(held)} {'was' if held == 1 else 'were'} kept."

    console.print()
    _say(
        f"Plex no longer lists {held:,} of the {total:,} tracks in the PlexMix library.",
        style="yellow",
    )

    if not can_prompt():
        _say(f"{kept} A removal this large needs confirmation.", style="red")
        _say(
            "To remove them, run 'plexmix sync' in a terminal and answer the question, or "
            "run 'plexmix sync --allow-removals' to remove them without a question "
            "(a backup is made first)."
        )
        raise typer.Exit(1)

    _say("Removing them also removes their tags, embeddings, audio features and playlist entries.")
    _say(
        "A backup of the database and the search index is made first, and Plex is checked "
        "again before anything is removed."
    )
    if not typer.confirm(f"Remove {held:,} of {total:,} tracks?", default=False):
        _say(kept, style="yellow")
        return

    console.print("Checking Plex again...")
    try:
        # With the keys, a track that went missing after the question is kept. An
        # outcome without keys is checked by the count alone.
        result = sync_engine.remove_unlisted_tracks(held, held_keys=outcome.held_keys or None)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted while the removal was being applied.[/yellow]")
        _print_interrupted_removal(sync_engine)
        console.print("[yellow]Run 'plexmix sync' again to compare the library with Plex.[/yellow]")
        raise typer.Exit(130)

    if result.status == REMOVAL_REMOVED:
        _print_backup(result.backup_folder, result.backup_size_bytes)
        _say(result.message or f"Removed {_tracks(result.removed)}.", style="green")
        return
    if result.status == REMOVAL_LISTED_AGAIN:
        _say(result.message or "Plex lists those tracks again. Nothing was removed.")
        return

    # More tracks are missing now, Plex could not be listed, the backup failed, or the
    # tracks could not be deleted. A backup that was made for it stays: say where.
    _say(result.message or "Nothing was removed.", style="red")
    _print_backup(result.backup_folder, result.backup_size_bytes)
    if result.status == REMOVAL_MORE_MISSING:
        _say("Run 'plexmix sync' again to review the new count.")
    raise typer.Exit(1)


def _report_sync(sync_engine: SyncEngine, sync_result: SyncHistory, name: str) -> bool:
    """Print what a finished sync did, from ``sync_engine.outcome``.

    Returns True only when the green success line was printed: every listed item was
    read and no removal was held or skipped. Returns False when the run ended well
    but kept something back: a few items could not be read, or a held removal was
    answered. Ends the command with status 1 when removals were skipped (too many
    failed reads, an empty listing, a failed backup, an unknown listing), when a held
    removal could not be asked about, or when a confirmed removal could not be applied.
    """
    outcome = sync_engine.outcome
    clean = not outcome.is_partial

    if clean:
        console.print(f"\n[green]{name} completed successfully![/green]")
    else:
        console.print(f"\n[yellow]{name} finished with warnings.[/yellow]")
    console.print(f"  Tracks added: {sync_result.tracks_added}")
    console.print(f"  Tracks updated: {sync_result.tracks_updated}")
    console.print(f"  Tracks removed: {sync_result.tracks_removed}")
    _print_backup(outcome.backup_folder, outcome.backup_size_bytes)
    if clean:
        return True

    for line in _unread_lines(outcome):
        _say(line, style="yellow")

    if not outcome.reason:
        # Only unread items: they were kept, and nothing else was held back.
        return False

    if outcome.reason == REASON_THRESHOLD and outcome.held > 0:
        _settle_held_removal(sync_engine, outcome)
        return False

    # Removals were skipped. The engine's message says why, and that nothing was removed.
    _say(outcome.message or "Nothing was removed.", style="red")
    raise typer.Exit(1)


@sync_app.callback()
def sync_callback(
    ctx: typer.Context,
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
    audio: bool = typer.Option(False, help="Extract audio features during sync"),
    musicbrainz: bool = typer.Option(False, help="Enrich with MusicBrainz metadata during sync"),
    allow_removals: bool = typer.Option(False, "--allow-removals", help=_ALLOW_REMOVALS_HELP),
) -> None:
    if ctx.invoked_subcommand is None:
        run_incremental_sync(
            embeddings=embeddings,
            audio=audio,
            musicbrainz=musicbrainz,
            allow_removals=allow_removals,
        )


@sync_app.command("incremental")
def sync_incremental(
    ctx: typer.Context,
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
    audio: bool = typer.Option(False, help="Extract audio features during sync"),
    musicbrainz: bool = typer.Option(False, help="Enrich with MusicBrainz metadata during sync"),
    allow_removals: bool = typer.Option(False, "--allow-removals", help=_ALLOW_REMOVALS_HELP),
) -> None:
    run_incremental_sync(
        embeddings=embeddings,
        audio=audio,
        musicbrainz=musicbrainz,
        allow_removals=allow_removals or _allowed_by_group(ctx),
    )


def run_incremental_sync(
    embeddings: bool = True,
    audio: bool = False,
    musicbrainz: bool = False,
    allow_removals: bool = False,
) -> bool:
    """Core incremental sync shared by the CLI command and internal callers.

    Uses plain bool defaults (not ``typer.Option`` sentinels) so callers that
    invoke it as a function — the wizard, doctor, and ``sync full`` — pass real
    values instead of truthy ``OptionInfo`` objects that force audio/MusicBrainz on.
    Other code calls this function, never a Typer command function: a removal above
    the confirmation threshold is then held unless ``allow_removals`` is a real True.

    Returns True only when the sync ended with the green success line (see
    ``_report_sync``). Raises ``typer.Exit`` with status 1 when the sync stopped,
    skipped removals, or held a removal that nobody could confirm.
    """
    console.print("[bold]Starting incremental library sync...[/bold]")

    settings = Settings.load_from_file()

    try:
        plex_client, ai_provider, embedding_generator, vector_index = _connect_and_build(
            settings, generate_embeddings=embeddings
        )
    except PlexConnectionError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Enable MusicBrainz if --musicbrainz flag or config says so (gated by enabled)
    mb_settings = None
    run_mb = musicbrainz or (settings.musicbrainz.enabled and settings.musicbrainz.enrich_on_sync)
    if run_mb:
        mb_settings = settings.musicbrainz

    # Audio analysis runs as a pipeline stage before embeddings (so embeddings can
    # incorporate audio features), not as a post-sync step.
    run_audio = audio or settings.audio.analyze_on_sync
    audio_settings = settings.audio if run_audio else None

    with SQLiteManager(str(settings.database.get_db_path())) as db:
        db.create_tables()

        sync_engine = SyncEngine(
            plex_client,
            db,
            embedding_generator,
            vector_index,
            ai_provider,
            musicbrainz_settings=mb_settings,
            audio_settings=audio_settings,
            **_saved_index(settings),
        )

        try:
            sync_result = sync_engine.incremental_sync(
                generate_embeddings=embeddings,
                # Only a real True allows it: a placeholder object does not.
                allow_large_removals=allow_removals is True,
            )
        except PlexFetchError as e:
            # Plex stopped answering or could not list the library. The engine wrote
            # nothing, and its error is one plain sentence.
            _print_sync_stopped(e)
            raise typer.Exit(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Sync interrupted by user.[/yellow]")
            console.print("[green]Progress has been saved to database.[/green]")
            _print_changes_so_far(sync_engine)
            console.print(
                "[yellow]Tip: Run 'plexmix sync' again to continue from where you left off.[/yellow]"
            )
            raise typer.Exit(130)
        except Exception as e:
            if _stopped_before_any_change(sync_engine, e):
                # The engine's own check of the connection failed before the sync started.
                _print_sync_stopped(e)
                raise typer.Exit(1)
            # A later stage failed. What the run had saved by then is named: the
            # counts, and the backup of a large removal.
            if isinstance(e, ConnectionError):
                _print_later_stage_stopped(e)
                _print_changes_so_far(sync_engine)
                raise typer.Exit(1)
            _print_changes_so_far(sync_engine)
            raise

        return _report_sync(sync_engine, sync_result, "Incremental sync")


@sync_app.command("regenerate")
def sync_regenerate(
    ctx: typer.Context,
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
    allow_removals: bool = typer.Option(False, "--allow-removals", help=_ALLOW_REMOVALS_HELP),
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

        sync_engine = SyncEngine(
            plex_client,
            db,
            embedding_generator,
            vector_index,
            ai_provider,
            **_saved_index(settings),
        )

        try:
            sync_result = sync_engine.regenerate_sync(
                generate_embeddings=embeddings,
                # Only a real True allows it: a placeholder object does not.
                allow_large_removals=(allow_removals or _allowed_by_group(ctx)) is True,
            )
        except PlexFetchError as e:
            _print_sync_stopped(e)
            # The sentence above is about the library sync: say what was cleared.
            _print_cleared_note(sync_engine)
            raise typer.Exit(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Sync interrupted by user.[/yellow]")
            console.print("[green]Progress has been saved to database.[/green]")
            _print_changes_so_far(sync_engine)
            console.print("[yellow]Tip: Run 'plexmix sync regenerate' again to continue.[/yellow]")
            raise typer.Exit(130)
        except Exception as e:
            if _stopped_before_any_change(sync_engine, e):
                # The engine's connection check failed, before anything was cleared.
                _print_sync_stopped(e)
                _print_cleared_note(sync_engine)
                raise typer.Exit(1)
            if isinstance(e, ConnectionError):
                _print_later_stage_stopped(e)
            _print_changes_so_far(sync_engine)
            _print_cleared_note(sync_engine)
            if isinstance(e, ConnectionError):
                raise typer.Exit(1)
            raise

        _report_sync(sync_engine, sync_result, "Regenerate sync")


@sync_app.command("full")
def sync_full(
    ctx: typer.Context,
    embeddings: bool = typer.Option(True, help="Generate embeddings during sync"),
    allow_removals: bool = typer.Option(False, "--allow-removals", help=_ALLOW_REMOVALS_HELP),
) -> None:
    console.print("[bold]Starting library sync (full is now an alias for incremental)...[/bold]")
    # Explicit values — audio/musicbrainz default off (previously they were
    # OptionInfo sentinels that always evaluated truthy and forced both on).
    run_incremental_sync(
        embeddings=embeddings,
        audio=False,
        musicbrainz=False,
        allow_removals=allow_removals or _allowed_by_group(ctx),
    )
