"""Small helpers shared by the CLI commands.

The second half is what the three regenerate commands share (``sync regenerate``,
``embeddings generate --regenerate`` and ``doctor --force``): the terminal check, the
question, the refusal of a provider that is not ready, the lines that say what a run
regenerated and kept, and the tip that names the command that continues. Each takes
the console of the command that calls it.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Optional, Sequence, TextIO, Tuple

import typer
from rich.console import Console

from ..utils.redaction import REDACTED, mask_urls_in_text, redact_partly_hidden_keys

if TYPE_CHECKING:
    from ..database.index_rebuild import IndexRebuild
    from ..plex.sync import RegeneratePass, RegeneratePlan
    from ..services.regenerate_service import RegenerateRefused, RunProvider

_SIZE_UNITS = ("KB", "MB", "GB", "TB")

_MIN_SECRET_CHARS = 4
_ERROR_TEXT_MAX_CHARS = 300

# The labels of the two passes of a regenerate run, and what a track that was not
# regenerated keeps.
TAGS_LABEL = "AI tags"
EMBEDDINGS_LABEL = "Embeddings"
_PREVIOUS_TAGS = "tags"
_PREVIOUS_EMBEDDING = "embedding"
# The same two labels inside a sentence.
_LABEL_IN_A_SENTENCE = {TAGS_LABEL: "AI tags", EMBEDDINGS_LABEL: "embeddings"}

CANCELLED_TEXT = "Operation cancelled."
NOTHING_CHANGED_TEXT = "Nothing was changed."
_NO_RESULT_TEXT = "The regenerate run reported no result."
_NOT_FINISHED_TEXT = "The regenerate run stopped before it reached its end."
_NO_NEW_TAGS_TEXT = "No AI tags were regenerated, so no embedding was regenerated either."
NO_TRACKS_TEXT = "The PlexMix library holds no tracks, so there is nothing to regenerate."
NO_INDEX_SAVED_TEXT = (
    "The search index was not saved in this run: a file that could not be loaded is "
    "still in its place."
)

# The command that builds the search index again from the stored embeddings.
REBUILD_INDEX_COMMAND = "plexmix embeddings generate"


def can_prompt() -> bool:
    """Whether the user can be asked a question.

    True only when standard input and standard output are both terminals. A command
    that is piped, redirected, run by cron or run by ``docker exec`` without ``-it``
    must not wait for an answer that nobody can give.
    """
    return _is_terminal(sys.stdin) and _is_terminal(sys.stdout)


def format_size(size_bytes: int) -> str:
    """A file size as short text, for example ``12.4 MB`` (1 KB is 1,024 bytes)."""
    if size_bytes < 1024:
        return "1 byte" if size_bytes == 1 else f"{size_bytes} bytes"
    value = float(size_bytes)
    unit = _SIZE_UNITS[0]
    for unit in _SIZE_UNITS:
        value /= 1024
        # Compare the rounded value, so 1,048,575 bytes reads "1.0 MB", not "1024.0 KB".
        if round(value, 1) < 1024:
            break
    return f"{value:.1f} {unit}"


def _is_terminal(stream: Optional[TextIO]) -> bool:
    # A stream can be missing (None) or already closed; neither can show a question.
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def tracks_text(count: int) -> str:
    """``1 track`` or ``5,443 tracks``."""
    return f"{count:,} track{'' if count == 1 else 's'}"


def say(console: Console, text: str, style: Optional[str] = None, soft_wrap: bool = False) -> None:
    """Print a line that holds values (counts, a file, an error text) exactly as it is.

    No console markup and no emoji code is read from the text, so a file name or an
    error text with square brackets or colons is shown unchanged. ``soft_wrap`` keeps
    a long value such as a path in one piece, so it can be copied from the terminal.
    """
    console.print(text, style=style, markup=False, emoji=False, soft_wrap=soft_wrap)


def plain_error_text(error: BaseException, secrets: Iterable[Optional[str]] = ()) -> str:
    """An error as one line that is safe to print.

    Known key values (``secrets``) are replaced, and so is a key that the provider
    repeats with its middle hidden (``redact_partly_hidden_keys``, the rule the sync
    engine and the regenerate service use too). Credentials inside an address are
    masked, and the text is capped.
    """
    text = str(error) or type(error).__name__
    for secret in secrets:
        value = (secret or "").strip()
        if len(value) >= _MIN_SECRET_CHARS:
            text = text.replace(value, REDACTED)
    text = redact_partly_hidden_keys(text)
    text = " ".join(mask_urls_in_text(text).split())
    if len(text) > _ERROR_TEXT_MAX_CHARS:
        text = text[: _ERROR_TEXT_MAX_CHARS - 1] + "…"
    return text


def _sentence(text: str) -> str:
    """``text`` with a full stop when it ends without one; "" stays ""."""
    text = text.strip()
    if text and not text.endswith((".", "!", "?", "…")):
        text += "."
    return text


def _path_texts(value: object) -> List[str]:
    """The paths in a list of paths, as text. Anything else holds no path."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, (str, Path))]


def print_unreadable_index(console: Console, vector_index: object) -> bool:
    """Say in one line that the saved search index could not be loaded.

    Call it right after a command has built its ``VectorIndex``. It prints only when
    that load failed (``load_failed`` is a real True), so a healthy index, None and a
    mock print nothing. The files of such an index are never deleted. The line names
    the files that were moved aside and the command that rebuilds the index. A file
    that could not be moved aside stays where it is and no index is saved over it in
    this run: the line then says to move it away first. Returns True when the line
    was printed.
    """
    if getattr(vector_index, "load_failed", False) is not True:
        return False
    moved = _path_texts(getattr(vector_index, "moved_paths", None))
    unmoved = _path_texts(getattr(vector_index, "unmoved_paths", None))

    parts = ["The search index could not be loaded."]
    if moved:
        parts.append(f"Moved aside, not deleted: {' and '.join(moved)}.")
    if unmoved:
        it, it_is = ("it", "it is") if len(unmoved) == 1 else ("them", "they are")
        parts.append(
            f"Could not be moved aside, so left where {it_is}: {' and '.join(unmoved)}. "
            f"This run does not write over {it} and saves no search index. Move {it} away "
            f"yourself, then run '{REBUILD_INDEX_COMMAND}' to rebuild the search index. "
            "The stored embeddings are not affected."
        )
    else:
        parts.append(
            "The stored embeddings are not affected. To rebuild the search index from them, "
            f"run '{REBUILD_INDEX_COMMAND}'."
        )
    # ``soft_wrap`` keeps each file name in one piece, so it can be copied from the terminal.
    say(console, " ".join(parts), style="yellow", soft_wrap=True)
    return True


def index_was_saved(vector_index: object) -> bool:
    """False when this run could not save its search index.

    A saved index file that could not be loaded and could not be moved aside stays
    where it is (``unmoved_paths``), and ``save_index`` never writes over it. A
    command must then not say that it saved the index. True for a healthy index,
    for None and for a mock.
    """
    return not _path_texts(getattr(vector_index, "unmoved_paths", None))


def last_index_rebuild(vector_index: object) -> Optional["IndexRebuild"]:
    """What the last rebuild of this index from the stored embeddings did, or None.

    ``plexmix.database.index_rebuild.rebuild_index`` is the one place that builds
    the index from the database and saves it, and it leaves its result on the index
    object (``last_rebuild``). A command that reached it through a function that
    returns a number reads the result here. Only a real ``IndexRebuild`` counts:
    None for an index that was never rebuilt, for None and for a mock.
    """
    from ..database.index_rebuild import IndexRebuild

    result = getattr(vector_index, "last_rebuild", None)
    return result if isinstance(result, IndexRebuild) else None


def index_left_out(vector_index: object) -> int:
    """How many tracks kept the last rebuild of this index from being built and saved.

    Those tracks hold an embedding and none of the current embedding settings, so
    an index built from the rows would not cover the library. Nothing was built
    then, and the saved index is as it was. 0 when the index was rebuilt, when no
    rebuild ran, and for None or a mock.
    """
    result = last_index_rebuild(vector_index)
    return result.left_out if result is not None else 0


def print_index_left_as_it_is(console: Console, left_out: int) -> bool:
    """Say that the search index was not rebuilt, and which command replaces the embeddings.

    ``left_out`` tracks were in the way (``index_left_out``, or the count the sync
    engine recorded). The sentence is the one of ``index_rebuild.left_out_sentence``,
    so the CLI, the log and the web UI say the same. Prints nothing for 0 and for
    anything that is not a count. A command that prints it must not also say that
    the index was rebuilt or saved. Returns True when the line was printed.
    """
    from ..database.index_rebuild import left_out_sentence

    sentence = left_out_sentence(left_out)
    if not sentence:
        return False
    say(console, sentence, style="yellow")
    return True


# ── Regenerate commands: the terminal check and the question ─────────────────


def require_terminal_or_yes(console: Console, yes: bool, command: str) -> None:
    """End a regenerate command with status 1 when nobody can answer its question.

    Call it first, before anything is loaded. ``yes`` is the command's ``--yes``
    option, and only a real True counts. ``command`` is the command as the user
    types it (``plexmix sync regenerate``). With a terminal or with ``--yes`` this
    returns and prints nothing.
    """
    if yes is True or can_prompt():
        return
    say(
        console,
        f"'{command}' asks for confirmation before it changes anything, and there is no "
        "terminal to answer in.",
        style="red",
    )
    say(
        console,
        f"Run it in a terminal, or run '{command} --yes' to go on without the question. "
        f"{NOTHING_CHANGED_TEXT}",
    )
    raise typer.Exit(1)


@dataclass(frozen=True)
class RegenerateQuestion:
    """What a regenerate command is about to do, for the question it asks first.

    ``what`` names what is replaced ("AI tags and embeddings"). ``total`` tracks are
    in the library, and ``remaining`` of them are to do in this run: all of them
    unless ``continued`` says that an unfinished earlier run is taken up again.
    ``providers`` are the lines that name the provider and model of each pass, as
    (label, provider) pairs. ``marker_path`` is the file to delete to start over; it
    is named only for a continued run. ``note`` is one more sentence about a
    continued run.
    """

    what: str
    total: int
    remaining: int
    continued: bool = False
    providers: Sequence[Tuple[str, str]] = ()
    marker_path: Optional[Path] = None
    note: str = ""


def provider_text(provider: "RunProvider") -> str:
    """Display name and model of a provider for the question: "OpenAI · gpt-5-mini"."""
    return provider.text or provider.label or provider.provider or "not set"


def regenerate_question(
    plan: Optional["RegeneratePlan"],
    tags_provider: "RunProvider",
    embeddings_provider: Optional["RunProvider"] = None,
) -> RegenerateQuestion:
    """The question of a run of tags, or of tags and embeddings, from its plan.

    ``plan`` is what ``plexmix.plex.sync.plan_regenerate`` says for the two providers,
    or None when there is no library to look at yet. ``embeddings_provider`` is None
    for a run of tags alone. Pass the same providers to the engine, so that the
    question and the run follow one rule.
    """
    providers = [(TAGS_LABEL, provider_text(tags_provider))]
    if embeddings_provider is not None:
        providers.append((EMBEDDINGS_LABEL, provider_text(embeddings_provider)))
    what = "AI tags and embeddings" if embeddings_provider is not None else "AI tags"
    if plan is None:
        return RegenerateQuestion(what=what, total=0, remaining=0, providers=tuple(providers))

    total, remaining = plan.total_tracks, plan.remaining
    # An earlier run that replaced nothing is not worth a word: every track is to do.
    # One that had finished its tags is, also when all of its embeddings are left:
    # the tags are not made again.
    continued = plan.continued and (plan.tags_done_earlier or remaining < total)
    note = ""
    if continued and plan.tags_done_earlier:
        note = "That run had finished the AI tags, so only the embeddings that are left are made"
    elif continued and embeddings_provider is not None:
        # The tags pass comes first, and an embedding is made from its track's tags:
        # after it every embedding is made again, so the count above is the tags'.
        note = (
            f"That count is for the AI tags. All {tracks_text(total)} get a new embedding "
            "after them, because an embedding is made from its track's tags"
        )
    return RegenerateQuestion(
        what=what,
        total=total,
        remaining=remaining if continued else total,
        continued=continued,
        providers=tuple(providers),
        marker_path=plan.marker_path,
        note=note,
    )


def confirm_regenerate(console: Console, question: RegenerateQuestion, yes: bool) -> bool:
    """Say what a regenerate command will do, then ask whether to go on (default No).

    Returns True on Yes. With ``yes`` (the command's ``--yes`` option; only a real
    True counts) the description is printed and nothing is asked. On No this prints
    "Operation cancelled." and returns False: the caller then ends with status 0,
    having written nothing and asked no provider.
    """
    total, remaining = question.total, question.remaining
    console.print()
    if question.continued:
        say(
            console,
            "An earlier regenerate run did not finish. This run continues it: "
            f"{remaining:,} of {tracks_text(total)} {'remains' if remaining == 1 else 'remain'}.",
            style="yellow",
        )
        if question.note:
            say(console, _sentence(question.note))
    elif total:
        say(
            console,
            f"This replaces the {question.what} of all {tracks_text(total)} in the "
            "PlexMix library.",
            style="yellow",
        )
    else:
        say(console, "The PlexMix library holds no tracks yet.", style="yellow")
    for label, provider in question.providers:
        say(console, f"  {label}: {provider}")
    say(
        console,
        "Nothing is cleared first: each track keeps what it has until its replacement is saved.",
    )
    if question.continued and question.marker_path is not None:
        say(
            console,
            f"To start over with all {tracks_text(total)}, delete {question.marker_path} "
            "and run the command again.",
            soft_wrap=True,
        )

    if yes is True:
        return True
    if question.continued:
        asked = f"Continue with the remaining {tracks_text(remaining)}?"
    elif total:
        asked = f"Regenerate the {question.what} of {tracks_text(total)}?"
    else:
        asked = "Continue?"
    console.print()
    if typer.confirm(asked, default=False):
        return True
    say(console, CANCELLED_TEXT, style="yellow")
    return False


def print_interrupted_before_any_change(console: Console) -> None:
    """After Ctrl+C during the provider check: say that nothing was changed.

    The caller ends with status 130.
    """
    console.print()
    say(console, f"Interrupted during the provider check. {NOTHING_CHANGED_TEXT}", style="yellow")


def print_regenerate_refused(console: Console, refused: "RegenerateRefused") -> None:
    """Print which provider is not ready and why, and that nothing was changed.

    The text of a ``RegenerateRefused`` holds no API key. The caller ends with
    status 1.
    """
    from ..services.regenerate_service import REFUSED_NO_KEY

    console.print()
    say(console, _sentence(str(refused)), style="red")
    say(console, f"{NOTHING_CHANGED_TEXT} Every track keeps its tags and its embedding.")
    if refused.reason == REFUSED_NO_KEY:
        say(console, "Run 'plexmix config init' to store the key.")


# ── Regenerate commands: what a run did ──────────────────────────────────────


@dataclass(frozen=True)
class RegenerateCount:
    """What one pass of a regenerate run did, for its summary line.

    ``label`` names the pass ("AI tags"), and ``previous`` what a track that was not
    regenerated keeps ("tags", "embedding"). ``regenerated`` tracks got new data in
    this run and ``kept`` tracks of its to-do set hold what they had before.
    ``done_earlier`` is True when the earlier run that this one continues had
    already finished this pass. ``earlier`` tracks were replaced by that earlier
    run, since the start time the run marker holds: they are no part of
    ``regenerated``, but a continued run that replaced nothing itself has still
    done its work when the run before it did the rest.
    """

    label: str
    previous: str
    regenerated: int = 0
    kept: int = 0
    done_earlier: bool = False
    earlier: int = 0

    @property
    def text(self) -> str:
        """The summary line, such as "AI tags: 2,400 tracks regenerated, 3,543 keep
        their previous tags."."""
        if self.done_earlier and not self.regenerated and not self.kept:
            return f"{self.label}: regenerated by the earlier run."
        line = f"{self.label}: {tracks_text(self.regenerated)} regenerated"
        if self.earlier and not self.regenerated:
            # What the run that this one continues did, so that a line that reads
            # "0 tracks regenerated" is not taken for a run that did nothing at all.
            line += f" (the earlier run regenerated {self.earlier:,})"
        if self.kept:
            keep = "keeps its" if self.kept == 1 else "keep their"
            line += f", {self.kept:,} {keep} previous {self.previous}"
        return line + "."


@dataclass(frozen=True)
class RegenerateReport:
    """The outcome of a regenerate run, as a command prints it.

    ``counts`` has one entry per pass the run was asked to do. ``failure`` is the
    sentence of a run that did not do its work: an error stopped a pass part-way, or
    the run regenerated nothing. It is "" for a run that did its work, and such a
    run can still have kept tracks (``kept_some``). A failed run prints no success
    line and ends with status 1.

    ``index_left_out`` is not 0 when the run reached its end and the search index
    could still not be rebuilt: that many tracks hold only an embedding that was
    made with other embedding settings, so the saved index was left as it is. Such
    a run is no failure, and it gets no success line either (``clean``).
    ``nothing_to_do`` is True for a run on a library without tracks: it wrote
    nothing, and that is no failure.
    """

    counts: Tuple[RegenerateCount, ...] = ()
    failure: str = ""
    index_left_out: int = 0
    nothing_to_do: bool = False

    @property
    def failed(self) -> bool:
        """True when the command must end with status 1 and without a success line."""
        return bool(self.failure)

    @property
    def kept_some(self) -> bool:
        """True when at least one track holds the data it had before the run."""
        return any(count.kept for count in self.counts)

    @property
    def clean(self) -> bool:
        """True when the run may get its success line: it did its work, every track
        was replaced, and the search index was rebuilt."""
        return not self.failed and not self.kept_some and self.index_left_out <= 0


def embeddings_count(regenerated: int, kept: int, earlier: int = 0) -> RegenerateCount:
    """The summary entry of an embeddings pass, from its counts.

    ``earlier`` is the number of tracks that the run this one continues had
    replaced (0 for a new run).
    """
    return RegenerateCount(
        EMBEDDINGS_LABEL, _PREVIOUS_EMBEDDING, regenerated, kept, earlier=earlier
    )


def build_regenerate_report(
    counts: Sequence[RegenerateCount],
    stopped: str = "",
    index_left_out: int = 0,
    nothing_to_do: bool = False,
) -> RegenerateReport:
    """Make the report of a run from its counts. This is where a run is judged.

    ``stopped`` is the sentence of the error that stopped a pass part-way ("" for a
    run that reached its end). A run that regenerated nothing at all fails too,
    unless it had ``nothing_to_do`` (a library without tracks; only a real True
    counts) or it continued an earlier run that had replaced tracks (``earlier``):
    the tracks that are left then keep their previous data, and the run ends with
    warnings, so that it does not fail again and again. ``index_left_out`` is the
    number of tracks that kept the search index from being rebuilt at the end of the
    run (0 when it was rebuilt).
    """
    failure = _sentence(stopped)
    nothing_to_do = nothing_to_do is True and not failure
    if (
        not failure
        and not nothing_to_do
        and not any(count.regenerated or count.earlier for count in counts)
    ):
        names = [_LABEL_IN_A_SENTENCE.get(count.label, count.label) for count in counts]
        failure = f"No {' or '.join(names) or 'tags or embeddings'} were regenerated."
    left_out = index_left_out if _is_count(index_left_out) else 0
    return RegenerateReport(tuple(counts), failure, left_out, nothing_to_do)


def _is_count(value: object) -> bool:
    """True for a whole number above 0. A bool, a mock and anything else is no count."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def regenerate_report(result: object) -> RegenerateReport:
    """The report of an engine run, from ``SyncEngine.regenerate_result``.

    Only a real ``RegenerateResult`` of a regenerate run counts. Anything else is a
    run without a result, and that is a failed run. Read the result after
    ``regenerate_sync`` or ``regenerate_local`` returned, and also after Ctrl+C: it
    then holds the counts so far.
    """
    from ..plex.sync import RegenerateResult

    if not isinstance(result, RegenerateResult) or not result.requested:
        return RegenerateReport((), _NO_RESULT_TEXT)
    counts: List[RegenerateCount] = []
    if result.tags.requested:
        counts.append(_pass_count(TAGS_LABEL, _PREVIOUS_TAGS, result.tags))
    if result.embeddings.requested:
        counts.append(_pass_count(EMBEDDINGS_LABEL, _PREVIOUS_EMBEDDING, result.embeddings))
    stopped = ""
    if result.provider_failed:
        # ``error_message`` is the engine's text to show. ``error`` is never printed.
        stopped = result.error_message or "A provider error stopped the run."
    elif result.no_new_tags and result.embeddings.requested and not result.embeddings.started:
        # The AI provider gave nothing usable for any track. The engine then leaves
        # the embeddings alone: their text would be the one they were made from.
        stopped = _NO_NEW_TAGS_TEXT
    elif not result.finished:
        stopped = _NOT_FINISHED_TEXT
    # The index is spoken of only for a run that reached its end. After a stop, the
    # stop is what the run reports, and the index was left as it is without a word.
    return build_regenerate_report(
        counts,
        stopped,
        index_left_out=0 if stopped else result.index_left_out,
        nothing_to_do=result.nothing_to_do,
    )


def _pass_count(label: str, previous: str, record: "RegeneratePass") -> RegenerateCount:
    return RegenerateCount(
        label, previous, record.written, record.kept, record.done_earlier, record.replaced_earlier
    )


def print_regenerate_counts(console: Console, report: RegenerateReport) -> None:
    """Print one line per pass: how many tracks were regenerated and how many kept theirs.

    A run on a library without tracks had nothing to count, and gets one line that
    says so.
    """
    if report.nothing_to_do:
        say(console, f"  {NO_TRACKS_TEXT}")
        return
    for count in report.counts:
        say(console, f"  {count.text}")


def print_regenerate_tip(console: Console, command: str) -> None:
    """Name the command that continues an unfinished regenerate run.

    ``command`` is the command that was run. Run again, it regenerates only the
    tracks that are left.
    """
    say(
        console,
        f"Tip: Run '{command}' again to continue. It regenerates only the tracks that are "
        "left, and it clears nothing.",
        style="yellow",
    )


def print_regenerate_failed(console: Console, report: RegenerateReport, command: str) -> None:
    """Print the outcome of a failed run: why, the counts, and the tip.

    The caller prints no success line and ends with status 1.
    """
    say(console, report.failure, style="red")
    print_regenerate_counts(console, report)
    print_regenerate_tip(console, command)


def print_regenerate_interrupted(console: Console, report: RegenerateReport, command: str) -> None:
    """After Ctrl+C: the counts so far, that the rest is kept, and the tip.

    The caller ends with status 130.
    """
    print_regenerate_counts(console, report)
    say(console, "Every track that was not regenerated keeps its previous data.")
    print_regenerate_tip(console, command)


def print_regenerate_saved(console: Console, result: object, report: RegenerateReport) -> bool:
    """After a stop that came once both passes had reached their end: what is saved, and what to run.

    Ctrl+C or an error can arrive after the embeddings pass has replaced every
    embedding and before the search index was rebuilt from them. Every track then
    has its new tags and its new embedding, the run marker is cleared, and nothing
    is left to continue: the same command would start over with every track and pay
    the providers again. Only the index is behind, and ``REBUILD_INDEX_COMMAND``
    rebuilds it from the saved rows without a request to a provider.

    Prints the counts and that command, and returns True, for the result of a run of
    tags and embeddings whose passes both reached their end. Returns False and
    prints nothing for any other result: the caller then prints the tip that
    continues the run (``print_regenerate_interrupted``).
    """
    from ..plex.sync import RegenerateResult

    if not (
        isinstance(result, RegenerateResult)
        and result.requested
        and result.embeddings.requested
        and result.finished
    ):
        return False
    print_regenerate_counts(console, report)
    say(
        console,
        "The new tags and embeddings are saved. "
        f"Run '{REBUILD_INDEX_COMMAND}' to rebuild the search index.",
        style="yellow",
    )
    return True
