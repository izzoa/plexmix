"""Tests for the helpers the CLI commands share (``plexmix.cli.console_helpers``)."""

import ast
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple
from unittest.mock import MagicMock

import click
import pytest
from rich.console import Console
from typer.models import OptionInfo

from plexmix.cli import console_helpers
from plexmix.cli.console_helpers import (
    NO_TRACKS_TEXT,
    RegenerateCount,
    RegenerateQuestion,
    RegenerateReport,
    build_regenerate_report,
    can_prompt,
    confirm_regenerate,
    embeddings_count,
    format_size,
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
    print_regenerate_saved,
    print_regenerate_tip,
    print_unreadable_index,
    provider_text,
    regenerate_question,
    regenerate_report,
    require_terminal_or_yes,
    say,
    tracks_text,
)
from plexmix.database import vector_index as vector_index_module
from plexmix.database.index_rebuild import IndexRebuild, left_out_sentence
from plexmix.database.models import Album, Artist, Embedding, Track
from plexmix.database.sqlite_manager import SQLiteManager
from plexmix.database.vector_index import VectorIndex
from plexmix.plex.sync import RegeneratePass, RegeneratePlan, RegenerateResult
from plexmix.services import tagging_service
from plexmix.services.regenerate_service import (
    CAUSE_NO_ANSWER,
    CAUSE_NO_KEY,
    COMPONENT_AI,
    COMPONENT_EMBEDDINGS,
    REFUSED_NO_ANSWER,
    REFUSED_NO_KEY,
    SCOPE_EMBEDDINGS,
    SCOPE_TAGS,
    RegenerateRefused,
    RegenerateRun,
    RunProvider,
)
from plexmix.utils import redaction

# The only "secrets" in this file are two of the project's dummy values.
DUMMY_KEY = "dummy-api-key-0000"
DUMMY_PASSWORD = "dummy-ui-password-0000"


class _Stream:
    """A stand-in for standard input or output. ``is_tty=None`` makes it a closed stream."""

    def __init__(self, is_tty: Optional[bool]) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        if self._is_tty is None:
            raise ValueError("I/O operation on closed file")
        return self._is_tty


def _can_prompt_with(stdin: Optional[_Stream], stdout: Optional[_Stream]) -> bool:
    """Call ``can_prompt()`` with the two standard streams replaced, then put them back."""
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(sys, "stdin", stdin)
        patched.setattr(sys, "stdout", stdout)
        return can_prompt()


def test_can_prompt_when_input_and_output_are_terminals() -> None:
    assert _can_prompt_with(_Stream(True), _Stream(True)) is True


@pytest.mark.parametrize(
    "stdin_is_tty, stdout_is_tty",
    [(False, True), (True, False), (False, False)],
    ids=["input-piped", "output-redirected", "neither"],
)
def test_cannot_prompt_unless_both_are_terminals(stdin_is_tty: bool, stdout_is_tty: bool) -> None:
    assert _can_prompt_with(_Stream(stdin_is_tty), _Stream(stdout_is_tty)) is False


@pytest.mark.parametrize("broken", [None, _Stream(None)], ids=["missing", "closed"])
def test_cannot_prompt_when_a_stream_is_missing_or_closed(broken: Optional[_Stream]) -> None:
    assert _can_prompt_with(broken, _Stream(True)) is False
    assert _can_prompt_with(_Stream(True), broken) is False


@pytest.mark.parametrize(
    "size_bytes, expected",
    [
        (0, "0 bytes"),
        (1, "1 byte"),
        (1023, "1023 bytes"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 * 1024 - 1, "1.0 MB"),
        (13_002_342, "12.4 MB"),
        (3 * 1024**3, "3.0 GB"),
        (5000 * 1024**4, "5000.0 TB"),
    ],
)
def test_format_size(size_bytes: int, expected: str) -> None:
    assert format_size(size_bytes) == expected


# ---------------------------------------------------------------------------
# What the three regenerate commands share
# ---------------------------------------------------------------------------

COMMAND = "plexmix doctor --force"


class _Screen:
    """A console that keeps what was printed, and a terminal that is there or not."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, terminal: bool = True) -> None:
        self._stream = io.StringIO()
        self.console = Console(file=self._stream, width=80, color_system=None)
        self.questions: List[str] = []
        self.defaults: List[Any] = []
        self.answer = False
        monkeypatch.setattr(console_helpers, "can_prompt", lambda: terminal)
        monkeypatch.setattr(console_helpers.typer, "confirm", self._confirm)

    def _confirm(self, text: str, default: Any = None, **options: Any) -> bool:
        self.questions.append(text)
        self.defaults.append(default)
        return self.answer

    @property
    def raw(self) -> str:
        return self._stream.getvalue()

    @property
    def text(self) -> str:
        """What was printed, as one line: the console wraps a long sentence."""
        return " ".join(self.raw.split())


@pytest.fixture
def screen(monkeypatch: pytest.MonkeyPatch) -> _Screen:
    return _Screen(monkeypatch)


def _result(
    tags: Optional[RegeneratePass] = None, embeddings: Optional[RegeneratePass] = None
) -> RegenerateResult:
    return RegenerateResult(
        tags=tags or RegeneratePass(), embeddings=embeddings or RegeneratePass()
    )


def _done(written: int, kept: int = 0, finished: bool = True, **more: Any) -> RegeneratePass:
    """A pass that wrote ``written`` rows and left ``kept`` tracks of its to-do set."""
    return RegeneratePass(
        requested=True,
        started=True,
        finished=finished,
        to_do_ids=set(range(written + kept)),
        written_ids=set(range(written)),
        **more,
    )


@pytest.mark.parametrize(
    "count, expected", [(0, "0 tracks"), (1, "1 track"), (2, "2 tracks"), (5943, "5,943 tracks")]
)
def test_tracks_text(count: int, expected: str) -> None:
    assert tracks_text(count) == expected


def test_say_prints_markup_and_emoji_codes_as_they_are(screen: _Screen) -> None:
    say(screen.console, "[red]/music/[live] :smile: 3:45[/red]")

    assert screen.raw == "[red]/music/[live] :smile: 3:45[/red]\n"


def test_say_keeps_a_long_value_in_one_piece(screen: _Screen) -> None:
    path = "/data/" + "folder/" * 20 + "plexmix.db.regenerate.json"

    say(screen.console, f"delete {path} and run the command again.", soft_wrap=True)

    assert f"delete {path} and run the command again." in screen.raw


def test_plain_error_text_hides_keys_and_address_credentials() -> None:
    error = RuntimeError(
        f"Error code: 401 -\n  key {DUMMY_KEY} was rejected by "
        f"http://leakuser:{DUMMY_PASSWORD}@127.0.0.1:9/v1"
    )

    text = plain_error_text(error, [None, "", "abc", f"  {DUMMY_KEY}  "])

    # One line, the key replaced, the credentials masked. A value under four
    # characters is no key: replacing it would cut the text to pieces.
    assert text == "Error code: 401 - key [redacted] was rejected by http://***@127.0.0.1:9/v1"


def test_plain_error_text_hides_a_key_that_the_provider_shows_in_part() -> None:
    """A provider can repeat the key with its middle hidden. Both ends still show."""
    error = RuntimeError("Incorrect API key provided: dumm********0000. Check the key.")

    text = plain_error_text(error)

    assert text == "Incorrect API key provided: [redacted] Check the key."
    assert "dumm" not in text and "0000" not in text


def test_plain_error_text_of_a_long_text_without_spaces_is_made_quickly() -> None:
    """A provider or a proxy can answer with a long body that holds no space at all.

    Looking for a partly hidden key in it must take no longer the longer it is: a
    pattern that did took eight seconds for 40,000 characters, and the command hung.
    """
    import time

    body = '{"error":"x",' * 40000 + f" sk-ab********wxyz {DUMMY_KEY}"

    started = time.perf_counter()
    text = plain_error_text(RuntimeError(body), [DUMMY_KEY])
    taken = time.perf_counter() - started

    assert taken < 2.0, f"took {taken:.1f} s"
    assert len(text) == 300 and DUMMY_KEY not in text


def test_plain_error_text_replaces_the_whole_word_around_a_hidden_middle() -> None:
    error = RuntimeError("Incorrect key: sk-ab********wxyz.\tSee\nhttp://u:p@host/x a***b")

    # Three stars are the mask of an address and stay as they are.
    assert plain_error_text(error) == "Incorrect key: [redacted] See http://***@host/x a***b"


def test_plain_error_text_takes_the_rule_for_a_partly_hidden_key_from_one_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync engine, the regenerate service and the CLI hide such a key by one
    rule (``plexmix.utils.redaction.redact_partly_hidden_keys``), not by a copy each."""
    seen: List[str] = []

    def shared_rule(text: str) -> str:
        seen.append(text)
        return redaction.redact_partly_hidden_keys(text)

    monkeypatch.setattr(console_helpers, "redact_partly_hidden_keys", shared_rule)

    text = plain_error_text(RuntimeError(f"key sk-ab********wxyz and {DUMMY_KEY}"), [DUMMY_KEY])

    assert text == "key [redacted] and [redacted]"
    # The known key is replaced first, so the shared rule never sees it.
    assert seen == ["key sk-ab********wxyz and [redacted]"]
    source = Path(console_helpers.__file__).read_text(encoding="utf-8")
    assert '"****"' not in source and '"[redacted]"' not in source
    assert console_helpers.REDACTED is redaction.REDACTED


def test_plain_error_text_names_an_error_without_text_and_caps_a_long_one() -> None:
    assert plain_error_text(ConnectionAbortedError()) == "ConnectionAbortedError"
    long_text = plain_error_text(RuntimeError("x" * 5000))
    assert len(long_text) == 300
    assert long_text.endswith("…")


def test_terminal_check_passes_with_a_terminal(screen: _Screen) -> None:
    require_terminal_or_yes(screen.console, False, COMMAND)

    assert screen.raw == ""


def test_terminal_check_passes_with_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = _Screen(monkeypatch, terminal=False)

    require_terminal_or_yes(screen.console, True, COMMAND)

    assert screen.raw == ""


@pytest.mark.parametrize(
    "yes", [False, OptionInfo(default=False), "yes", 1], ids=["false", "placeholder", "text", "one"]
)
def test_terminal_check_refuses_without_a_terminal_and_without_yes(
    monkeypatch: pytest.MonkeyPatch, yes: Any
) -> None:
    """Only a real True is ``--yes``: a placeholder object of a command that was called
    as a plain function is truthy, and it must not skip the check."""
    screen = _Screen(monkeypatch, terminal=False)

    with pytest.raises(click.exceptions.Exit) as refused:
        require_terminal_or_yes(screen.console, yes, COMMAND)

    assert refused.value.exit_code == 1
    assert "no terminal to answer in" in screen.text
    # The flag is named, with the command to run in place of this one.
    assert f"'{COMMAND} --yes'" in screen.text
    assert "Nothing was changed." in screen.text
    assert screen.questions == []


def test_question_of_a_new_run_names_the_count_and_the_providers(screen: _Screen) -> None:
    screen.answer = True
    question = RegenerateQuestion(
        what="AI tags and embeddings",
        total=5943,
        remaining=5943,
        providers=(("AI tags", "OpenAI · gpt-test"), ("Embeddings", "Gemini · embed-test")),
        marker_path=Path("/data/plexmix.db.regenerate.json"),
    )

    assert confirm_regenerate(screen.console, question, False) is True

    assert (
        "This replaces the AI tags and embeddings of all 5,943 tracks in the PlexMix library."
        in screen.text
    )
    assert "AI tags: OpenAI · gpt-test" in screen.text
    assert "Embeddings: Gemini · embed-test" in screen.text
    assert "Nothing is cleared first" in screen.text
    assert screen.questions == ["Regenerate the AI tags and embeddings of 5,943 tracks?"]
    # No is the default.
    assert screen.defaults == [False]
    # A new run has nothing to start over from, so the marker file is not named.
    assert "regenerate.json" not in screen.raw
    assert "earlier" not in screen.text


def test_question_of_a_continued_run_gives_the_remaining_count_and_how_to_start_over(
    screen: _Screen,
) -> None:
    screen.answer = True
    marker = Path("/data/plexmix.db.regenerate.json")
    question = RegenerateQuestion(
        what="AI tags and embeddings",
        total=5943,
        remaining=3543,
        continued=True,
        providers=(("AI tags", "OpenAI · gpt-test"),),
        marker_path=marker,
        note="That run had finished the AI tags",
    )

    assert confirm_regenerate(screen.console, question, False) is True

    assert (
        "An earlier regenerate run did not finish. This run continues it: "
        "3,543 of 5,943 tracks remain." in screen.text
    )
    assert "That run had finished the AI tags." in screen.text
    assert (
        f"To start over with all 5,943 tracks, delete {marker} and run the command again."
        in screen.raw
    )
    assert screen.questions == ["Continue with the remaining 3,543 tracks?"]
    assert screen.defaults == [False]


def test_question_of_a_continued_run_with_one_track_left(screen: _Screen) -> None:
    question = RegenerateQuestion("embeddings", total=400, remaining=1, continued=True)

    confirm_regenerate(screen.console, question, False)

    assert "This run continues it: 1 of 400 tracks remains." in screen.text
    assert screen.questions == ["Continue with the remaining 1 track?"]


def test_question_declined_says_cancelled_and_returns_false(screen: _Screen) -> None:
    screen.answer = False
    question = RegenerateQuestion("embeddings", total=400, remaining=400)

    assert confirm_regenerate(screen.console, question, False) is False

    assert screen.text.endswith("Operation cancelled.")
    assert screen.questions == ["Regenerate the embeddings of 400 tracks?"]


@pytest.mark.parametrize("yes", [OptionInfo(default=False), "yes", 1])
def test_question_is_asked_unless_yes_is_a_real_true(screen: _Screen, yes: Any) -> None:
    question = RegenerateQuestion("embeddings", total=400, remaining=400)

    assert confirm_regenerate(screen.console, question, yes) is False

    assert len(screen.questions) == 1


def test_yes_skips_the_question_and_still_says_what_will_happen(screen: _Screen) -> None:
    question = RegenerateQuestion(
        "embeddings", total=400, remaining=400, providers=(("Embeddings", "OpenAI · embed-test"),)
    )

    assert confirm_regenerate(screen.console, question, True) is True

    assert screen.questions == []
    assert "This replaces the embeddings of all 400 tracks in the PlexMix library." in screen.text
    assert "Embeddings: OpenAI · embed-test" in screen.text
    assert "cancelled" not in screen.text


def test_question_for_an_empty_library_does_not_speak_of_zero_tracks(screen: _Screen) -> None:
    confirm_regenerate(screen.console, RegenerateQuestion("AI tags", total=0, remaining=0), False)

    assert "The PlexMix library holds no tracks yet." in screen.text
    assert "0 tracks" not in screen.text
    assert screen.questions == ["Continue?"]


def test_interrupted_provider_check_says_that_nothing_was_changed(screen: _Screen) -> None:
    print_interrupted_before_any_change(screen.console)

    assert screen.text == "Interrupted during the provider check. Nothing was changed."


AI = RunProvider("openai", "gpt-test", "OpenAI")
VECTORS = RunProvider("gemini", "embed-test", "Gemini")
MARKER = Path("/data/plexmix.db.regenerate.json")


def _run(scope: str, remaining: int, total: int = 400, continued: bool = False) -> RegenerateRun:
    """One scope of a plan: ``remaining`` of ``total`` tracks are to do."""
    return RegenerateRun(
        scope=scope,
        provider=AI if scope == SCOPE_TAGS else VECTORS,
        continued=continued,
        track_ids=tuple(range(remaining)),
        total_tracks=total,
        started_at=None,
        marker_path=MARKER,
    )


@pytest.mark.parametrize(
    "provider, expected",
    [
        (AI, "OpenAI · gpt-test"),
        (RunProvider("custom", "", "Custom (OpenAI-compatible)"), "Custom (OpenAI-compatible)"),
        (RunProvider("somebody", "", ""), "somebody"),
        (RunProvider("", "", ""), "not set"),
    ],
)
def test_provider_text(provider: RunProvider, expected: str) -> None:
    assert provider_text(provider) == expected


def test_plan_of_a_new_run_of_both_asks_about_every_track() -> None:
    plan = RegeneratePlan(tags=_run(SCOPE_TAGS, 400), embeddings=_run(SCOPE_EMBEDDINGS, 400))

    question = regenerate_question(plan, AI, VECTORS)

    assert question == RegenerateQuestion(
        what="AI tags and embeddings",
        total=400,
        remaining=400,
        continued=False,
        providers=(("AI tags", "OpenAI · gpt-test"), ("Embeddings", "Gemini · embed-test")),
        marker_path=MARKER,
    )


def test_plan_of_a_run_of_tags_alone_names_no_embedding_provider() -> None:
    question = regenerate_question(RegeneratePlan(tags=_run(SCOPE_TAGS, 400)), AI)

    assert question.what == "AI tags"
    assert question.providers == (("AI tags", "OpenAI · gpt-test"),)


def test_plan_that_continues_gives_the_remaining_count() -> None:
    plan = RegeneratePlan(
        tags=_run(SCOPE_TAGS, 360, continued=True), embeddings=_run(SCOPE_EMBEDDINGS, 400)
    )

    question = regenerate_question(plan, AI, VECTORS)

    assert (question.continued, question.remaining, question.total) == (True, 360, 400)
    # The 360 are the tracks still to be tagged. The embeddings pass comes after the
    # tags and makes every embedding again, because an embedding holds its track's
    # tags, so the question must not let 360 pass for the number of requests.
    assert question.note == (
        "That count is for the AI tags. All 400 tracks get a new embedding after them, "
        "because an embedding is made from its track's tags"
    )
    assert question.marker_path == MARKER


def test_question_of_a_continued_run_of_both_says_that_every_embedding_is_made(
    screen: _Screen,
) -> None:
    plan = RegeneratePlan(
        tags=_run(SCOPE_TAGS, 280, continued=True), embeddings=_run(SCOPE_EMBEDDINGS, 400)
    )

    confirm_regenerate(screen.console, regenerate_question(plan, AI, VECTORS), False)

    assert "This run continues it: 280 of 400 tracks remain." in screen.text
    assert (
        "That count is for the AI tags. All 400 tracks get a new embedding after them, "
        "because an embedding is made from its track's tags." in screen.text
    )


def test_question_of_a_continued_run_of_tags_alone_has_no_embedding_note() -> None:
    plan = RegeneratePlan(tags=_run(SCOPE_TAGS, 280, continued=True))

    question = regenerate_question(plan, AI)

    assert (question.continued, question.remaining, question.total) == (True, 280, 400)
    assert question.note == ""


def test_earlier_run_that_replaced_nothing_is_asked_as_a_new_run() -> None:
    plan = RegeneratePlan(tags=_run(SCOPE_TAGS, 400, continued=True))

    question = regenerate_question(plan, AI)

    assert (question.continued, question.remaining, question.total) == (False, 400, 400)


def test_earlier_run_that_finished_its_tags_is_continued_even_with_every_embedding_left() -> None:
    """The tags are not made again then, so the question must not promise new tags."""
    plan = RegeneratePlan(
        tags=_run(SCOPE_TAGS, 400),
        embeddings=_run(SCOPE_EMBEDDINGS, 400, continued=True),
        tags_done_earlier=True,
    )

    question = regenerate_question(plan, AI, VECTORS)

    assert (question.continued, question.remaining, question.total) == (True, 400, 400)
    assert "had finished the AI tags" in question.note


def test_no_plan_is_a_library_without_tracks() -> None:
    question = regenerate_question(None, AI, VECTORS)

    assert (question.total, question.remaining, question.continued) == (0, 0, False)
    assert question.marker_path is None
    assert len(question.providers) == 2


def test_refusal_names_the_provider_and_the_cause(screen: _Screen) -> None:
    refused = RegenerateRefused(
        "OpenAI", CAUSE_NO_KEY, component=COMPONENT_AI, reason=REFUSED_NO_KEY
    )

    print_regenerate_refused(screen.console, refused)

    assert "The AI provider OpenAI is not ready: no API key is stored." in screen.text
    assert "Nothing was changed." in screen.text
    # A missing key has one place to go.
    assert "Run 'plexmix config init' to store the key." in screen.text


def test_refusal_prints_the_safe_detail_and_never_the_provider_error(screen: _Screen) -> None:
    refused = RegenerateRefused(
        "Custom (OpenAI-compatible)",
        CAUSE_NO_ANSWER,
        component=COMPONENT_EMBEDDINGS,
        reason=REFUSED_NO_ANSWER,
        detail="Error code: 401 - [redacted] [was] rejected",
        error=RuntimeError(f"Error code: 401 - {DUMMY_KEY} [was] rejected"),
    )

    print_regenerate_refused(screen.console, refused)

    assert (
        "The embedding provider Custom (OpenAI-compatible) is not ready: it did not answer "
        "the test request. Error code: 401 - [redacted] [was] rejected." in screen.text
    )
    assert DUMMY_KEY not in screen.raw
    # The key is stored, so storing one is not the advice.
    assert "config init" not in screen.text


@pytest.mark.parametrize(
    "count, expected",
    [
        (RegenerateCount("AI tags", "tags", 5943, 0), "AI tags: 5,943 tracks regenerated."),
        (
            RegenerateCount("AI tags", "tags", 2400, 3543),
            "AI tags: 2,400 tracks regenerated, 3,543 keep their previous tags.",
        ),
        (
            RegenerateCount("Embeddings", "embedding", 1, 1),
            "Embeddings: 1 track regenerated, 1 keeps its previous embedding.",
        ),
        (
            RegenerateCount("Embeddings", "embedding", 0, 400),
            "Embeddings: 0 tracks regenerated, 400 keep their previous embedding.",
        ),
        (
            RegenerateCount("AI tags", "tags", 0, 0, done_earlier=True),
            "AI tags: regenerated by the earlier run.",
        ),
        (
            RegenerateCount("AI tags", "tags", 3, 0, done_earlier=True),
            "AI tags: 3 tracks regenerated.",
        ),
    ],
)
def test_count_line(count: RegenerateCount, expected: str) -> None:
    assert count.text == expected


def test_embeddings_count_is_the_entry_of_an_embeddings_pass() -> None:
    assert embeddings_count(50, 350) == RegenerateCount("Embeddings", "embedding", 50, 350)


def test_report_of_a_run_that_did_its_work() -> None:
    report = build_regenerate_report(
        [RegenerateCount("AI tags", "tags", 400), embeddings_count(400, 0)]
    )

    assert (report.failed, report.failure, report.kept_some) == (False, "", False)
    assert (report.index_left_out, report.nothing_to_do, report.clean) == (0, False, True)


def test_report_of_a_run_that_kept_some_tracks_is_not_a_failure() -> None:
    report = build_regenerate_report([RegenerateCount("AI tags", "tags", 397, 3)])

    assert (report.failed, report.kept_some) == (False, True)
    # It did its work, and it is not a clean run: it gets no success line.
    assert report.clean is False


def test_report_of_a_run_whose_index_was_left_as_it_is_is_no_failure_and_not_clean() -> None:
    """The run replaced every track it was to replace, and its last rebuild of the
    index was refused. That is a warning: status 0, and no success line."""
    report = build_regenerate_report([embeddings_count(400, 0)], index_left_out=380)

    assert (report.failed, report.kept_some, report.index_left_out) == (False, False, 380)
    assert report.clean is False


@pytest.mark.parametrize("left_out", [0, -3, True, None, "380", MagicMock()])
def test_report_takes_only_a_count_for_the_tracks_in_the_way(left_out: Any) -> None:
    report = build_regenerate_report([embeddings_count(400, 0)], index_left_out=left_out)

    assert report.index_left_out == 0
    assert report.clean is True


def test_report_of_a_library_without_tracks_is_no_failure() -> None:
    """Nothing to do is not "regenerated nothing": the run ends with status 0."""
    counts = [RegenerateCount("AI tags", "tags"), embeddings_count(0, 0)]

    nothing_to_do = build_regenerate_report(counts, nothing_to_do=True)

    assert (nothing_to_do.failed, nothing_to_do.failure) == (False, "")
    assert nothing_to_do.nothing_to_do is True and nothing_to_do.clean is True
    # Without that word the same counts are a run that regenerated nothing.
    assert build_regenerate_report(counts).failure == "No AI tags or embeddings were regenerated."


@pytest.mark.parametrize("word", [MagicMock(), 1, "yes", None, False])
def test_only_a_real_true_says_that_a_run_had_nothing_to_do(word: Any) -> None:
    report = build_regenerate_report([embeddings_count(0, 0)], nothing_to_do=word)

    assert report.failed is True and report.nothing_to_do is False


def test_a_run_that_an_error_stopped_never_had_nothing_to_do() -> None:
    report = build_regenerate_report(
        [embeddings_count(0, 0)], "Embedding generation stopped: Connection refused", 0, True
    )

    assert report.failed is True and report.nothing_to_do is False


@pytest.mark.parametrize(
    "counts, sentence",
    [
        ([RegenerateCount("AI tags", "tags", 0, 400)], "No AI tags were regenerated."),
        ([embeddings_count(0, 400)], "No embeddings were regenerated."),
        (
            [RegenerateCount("AI tags", "tags", 0, 400), embeddings_count(0, 400)],
            "No AI tags or embeddings were regenerated.",
        ),
        ([], "No tags or embeddings were regenerated."),
    ],
    ids=["tags", "embeddings", "both", "no-pass"],
)
def test_report_of_a_run_that_regenerated_nothing_fails(
    counts: List[RegenerateCount], sentence: str
) -> None:
    report = build_regenerate_report(counts)

    assert report.failed is True
    assert report.failure == sentence


def test_report_of_a_run_that_an_error_stopped_fails_even_with_rows_written() -> None:
    report = build_regenerate_report(
        [embeddings_count(50, 350)], "Embedding generation stopped: Connection refused"
    )

    assert report.failed is True
    assert report.failure == "Embedding generation stopped: Connection refused."


def test_engine_result_becomes_one_count_per_requested_pass() -> None:
    report = regenerate_report(_result(tags=_done(397, 3), embeddings=_done(400)))

    assert report.counts == (
        RegenerateCount("AI tags", "tags", 397, 3),
        RegenerateCount("Embeddings", "embedding", 400, 0),
    )
    assert (report.failed, report.kept_some) == (False, True)


def test_engine_result_of_a_run_of_tags_alone_has_no_embeddings_line() -> None:
    report = regenerate_report(_result(tags=_done(400)))

    assert [count.label for count in report.counts] == ["AI tags"]
    assert report.failed is False


def test_engine_result_with_a_stopped_pass_fails_with_the_engine_s_text() -> None:
    # The embeddings pass waited for the tags and never started: every track is kept.
    waiting = RegeneratePass(requested=True, to_do_ids=set(range(5943)))
    result = _result(tags=_done(2400, 3543, finished=False), embeddings=waiting)
    result.stopped_scope = SCOPE_TAGS
    result.error = RuntimeError(f"401 for key {DUMMY_KEY}")
    result.error_message = "AI tagging stopped: The provider rejected the API key."

    report = regenerate_report(result)

    assert report.failed is True
    # The engine's safe text, never the text of the error it keeps for classification.
    assert report.failure == "AI tagging stopped: The provider rejected the API key."
    assert DUMMY_KEY not in report.failure
    assert [count.text for count in report.counts] == [
        "AI tags: 2,400 tracks regenerated, 3,543 keep their previous tags.",
        "Embeddings: 0 tracks regenerated, 5,943 keep their previous embedding.",
    ]


def test_engine_result_that_did_not_reach_its_end_fails() -> None:
    report = regenerate_report(_result(tags=_done(40, 360, finished=False)))

    assert report.failed is True
    assert report.failure == "The regenerate run stopped before it reached its end."


def test_engine_result_without_any_new_tag_says_why_no_embedding_was_made() -> None:
    """The tags pass reached its end and replaced nothing, so the engine left the
    embeddings pass out: the run failed, and the report says which of the two it was."""
    waiting = RegeneratePass(requested=True, to_do_ids=set(range(400)))
    report = regenerate_report(_result(tags=_done(0, 400), embeddings=waiting))

    assert report.failed is True
    assert report.failure == (
        "No AI tags were regenerated, so no embedding was regenerated either."
    )
    assert [count.text for count in report.counts] == [
        "AI tags: 0 tracks regenerated, 400 keep their previous tags.",
        "Embeddings: 0 tracks regenerated, 400 keep their previous embedding.",
    ]


def test_engine_result_that_wrote_nothing_fails() -> None:
    report = regenerate_report(_result(tags=_done(0, 400), embeddings=_done(0, 400)))

    assert report.failure == "No AI tags or embeddings were regenerated."


@pytest.mark.parametrize("embeddings", [True, False], ids=["both", "tags alone"])
def test_engine_result_of_a_library_without_tracks_is_a_run_with_nothing_to_do(
    screen: _Screen, embeddings: bool
) -> None:
    """Every pass reached its end and had no track to do. That is no failed run."""
    result = _result(tags=_done(0), embeddings=_done(0) if embeddings else None)
    assert result.nothing_to_do is True and result.wrote_nothing is True

    report = regenerate_report(result)

    assert (report.failed, report.failure, report.nothing_to_do) == (False, "", True)
    assert report.clean is True
    print_regenerate_counts(screen.console, report)
    assert screen.raw == f"  {NO_TRACKS_TEXT}\n"
    assert NO_TRACKS_TEXT == (
        "The PlexMix library holds no tracks, so there is nothing to regenerate."
    )


def test_engine_result_whose_last_rebuild_was_refused_carries_the_count() -> None:
    """The embeddings pass reached its end and tracks were still in the way of the
    index. The run did its work, and the report keeps it from a success line."""
    result = _result(tags=_done(400), embeddings=_done(400))
    result.index_left_out = 380

    report = regenerate_report(result)

    assert (report.failed, report.kept_some, report.index_left_out) == (False, False, 380)
    assert report.clean is False


def test_engine_result_of_a_stopped_run_says_nothing_of_the_index() -> None:
    """After a stop, the stop is what is reported. The index was left without a word."""
    result = _result(tags=_done(400), embeddings=_done(50, 350, finished=False))
    result.stopped_scope = SCOPE_EMBEDDINGS
    result.error_message = "Embedding generation stopped: Connection refused."
    result.index_left_out = 350

    report = regenerate_report(result)

    assert report.failure == "Embedding generation stopped: Connection refused."
    assert report.index_left_out == 0


def test_engine_result_of_a_stopped_embeddings_pass_names_that_pass() -> None:
    result = _result(tags=_done(400), embeddings=_done(50, 350, finished=False))
    result.stopped_scope = SCOPE_EMBEDDINGS
    result.error_message = "Embedding generation stopped: Connection refused."

    report = regenerate_report(result)

    assert report.failure == "Embedding generation stopped: Connection refused."
    assert report.counts[1] == RegenerateCount("Embeddings", "embedding", 50, 350)


def test_continued_run_that_replaced_nothing_itself_is_no_failure() -> None:
    """The run it continues had replaced 5,900 tracks: the 43 that are left keep their data.

    Judged by what this run wrote alone, it failed with "No AI tags were
    regenerated", although the run before it had done the work. The same run would
    then fail again on the next call.
    """
    result = _result(tags=_done(0, 43, replaced_earlier=5900))

    report = regenerate_report(result)

    assert report.failed is False and report.failure == ""
    assert report.kept_some is True and report.clean is False
    assert [count.text for count in report.counts] == [
        "AI tags: 0 tracks regenerated (the earlier run regenerated 5,900), "
        "43 keep their previous tags."
    ]


def test_continued_embeddings_pass_that_replaced_nothing_itself_is_no_failure() -> None:
    report = regenerate_report(
        _result(tags=_done(0, done_earlier=True), embeddings=_done(0, 1, replaced_earlier=399))
    )

    assert report.failed is False
    assert report.counts[1].text == (
        "Embeddings: 0 tracks regenerated (the earlier run regenerated 399), "
        "1 keeps its previous embedding."
    )


def test_new_run_that_replaced_nothing_still_fails() -> None:
    """Nothing was replaced in this run or in an earlier one: the provider gave nothing."""
    report = regenerate_report(_result(tags=_done(0, 400)))

    assert report.failed is True
    assert report.failure == "No AI tags were regenerated."
    assert report.counts[0].earlier == 0


def test_a_run_that_replaced_some_names_no_earlier_run_in_its_line() -> None:
    """The earlier run is spoken of only for a run that wrote nothing itself."""
    assert (
        RegenerateCount("AI tags", "tags", 3, 2, earlier=10).text
        == "AI tags: 3 tracks regenerated, 2 keep their previous tags."
    )
    assert (
        RegenerateCount("AI tags", "tags", 0, 1, earlier=1).text
        == "AI tags: 0 tracks regenerated (the earlier run regenerated 1), "
        "1 keeps its previous tags."
    )


def test_build_report_counts_the_earlier_run_as_work() -> None:
    counts = [embeddings_count(0, 40, earlier=360)]

    assert build_regenerate_report(counts).failed is False
    assert build_regenerate_report([embeddings_count(0, 40)]).failed is True
    # A stop is a failure whatever an earlier run did.
    assert build_regenerate_report(counts, "Embedding generation stopped: x.").failed is True


def test_tags_that_an_earlier_run_finished_are_said_to_be_done() -> None:
    earlier = RegeneratePass(requested=True, started=True, finished=True, done_earlier=True)

    report = regenerate_report(_result(tags=earlier, embeddings=_done(3543)))

    assert report.failed is False
    assert report.counts[0].text == "AI tags: regenerated by the earlier run."


@pytest.mark.parametrize(
    "result", [None, object(), "done", RegenerateResult()], ids=["none", "object", "text", "empty"]
)
def test_anything_but_the_result_of_a_regenerate_run_is_a_failed_run(result: Any) -> None:
    """A run is called a success only on the engine's own word."""
    report = regenerate_report(result)

    assert report.failed is True
    assert report.counts == ()
    assert report.failure == "The regenerate run reported no result."


def test_counts_are_printed_one_line_per_pass(screen: _Screen) -> None:
    report = RegenerateReport(
        (RegenerateCount("AI tags", "tags", 397, 3), embeddings_count(400, 0))
    )

    print_regenerate_counts(screen.console, report)

    assert screen.raw.splitlines() == [
        "  AI tags: 397 tracks regenerated, 3 keep their previous tags.",
        "  Embeddings: 400 tracks regenerated.",
    ]


def test_tip_names_the_command_that_was_run(screen: _Screen) -> None:
    print_regenerate_tip(screen.console, COMMAND)

    assert f"Tip: Run '{COMMAND}' again to continue." in screen.text
    assert "only the tracks that are left" in screen.text
    assert "clears nothing" in screen.text


def test_failed_run_prints_the_reason_the_counts_and_the_tip(screen: _Screen) -> None:
    report = build_regenerate_report(
        [RegenerateCount("AI tags", "tags", 2400, 3543)], "AI tagging stopped: [401] rejected"
    )

    print_regenerate_failed(screen.console, report, COMMAND)

    # The reason comes first, as it is: square brackets are not console markup here.
    assert screen.raw.splitlines()[0] == "AI tagging stopped: [401] rejected."
    assert "AI tags: 2,400 tracks regenerated, 3,543 keep their previous tags." in screen.text
    assert f"Run '{COMMAND}' again to continue." in screen.text
    assert "successfully" not in screen.text


def test_interrupted_run_prints_the_counts_what_is_kept_and_the_tip(screen: _Screen) -> None:
    report = build_regenerate_report([RegenerateCount("AI tags", "tags", 2400, 3543)])

    print_regenerate_interrupted(screen.console, report, COMMAND)

    assert "AI tags: 2,400 tracks regenerated, 3,543 keep their previous tags." in screen.text
    assert "Every track that was not regenerated keeps its previous data." in screen.text
    assert f"Tip: Run '{COMMAND}' again to continue." in screen.text


def test_stop_after_both_passes_reached_their_end_says_only_the_index_is_behind(
    screen: _Screen,
) -> None:
    """Every embedding was replaced and the marker is cleared: no tip to continue."""
    result = _result(_done(400), _done(400))
    report = regenerate_report(result)

    assert print_regenerate_saved(screen.console, result, report) is True

    assert "AI tags: 400 tracks regenerated." in screen.text
    assert "Embeddings: 400 tracks regenerated." in screen.text
    assert (
        "The new tags and embeddings are saved. "
        "Run 'plexmix embeddings generate' to rebuild the search index." in screen.text
    )
    assert "again to continue" not in screen.text


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(_result(_done(400)), id="tags-alone"),
        pytest.param(_result(_done(400, finished=False)), id="tags-not-finished"),
        pytest.param(_result(_done(400), _done(100, 300, finished=False)), id="embeddings-stopped"),
        pytest.param(_result(), id="not-a-regenerate-run"),
        pytest.param(None, id="no-result"),
        pytest.param(MagicMock(), id="a-mock"),
    ],
)
def test_stop_that_left_a_pass_unfinished_prints_nothing_of_its_own(
    screen: _Screen, result: Any
) -> None:
    """The caller prints the tip that continues the run instead."""
    assert print_regenerate_saved(screen.console, result, regenerate_report(result)) is False

    assert screen.text == ""


# ---------------------------------------------------------------------------
# The search index that could not be loaded
# ---------------------------------------------------------------------------

MOVED_INDEX = Path("/data/plexmix/embeddings.index.unreadable-20261118-101500")
MOVED_METADATA = Path("/data/plexmix/embeddings.metadata.unreadable-20261118-101500")
LEFT_INDEX = Path("/data/plexmix/embeddings.index")
LEFT_METADATA = Path("/data/plexmix/embeddings.metadata")


def _index(load_failed: Any = True, moved: Any = (), unmoved: Any = ()) -> SimpleNamespace:
    """What a command's ``VectorIndex`` says about its last load."""
    return SimpleNamespace(
        load_failed=load_failed, moved_paths=list(moved), unmoved_paths=list(unmoved)
    )


@pytest.mark.parametrize(
    "vector_index",
    [
        None,
        _index(load_failed=False, moved=[MOVED_INDEX], unmoved=[LEFT_METADATA]),
        _index(load_failed=1, moved=[MOVED_INDEX]),
        _index(load_failed="yes", moved=[MOVED_INDEX]),
        MagicMock(),
        object(),
    ],
    ids=["none", "loaded", "one", "text", "mock", "object"],
)
def test_index_whose_load_did_not_fail_prints_nothing(screen: _Screen, vector_index: Any) -> None:
    """Only a real True is a failed load. A mock index of a test is truthy all over,
    and a sync without embeddings has no index at all."""
    assert print_unreadable_index(screen.console, vector_index) is False
    assert screen.raw == ""


def test_moved_index_line_names_the_new_files_and_the_command(screen: _Screen) -> None:
    printed = print_unreadable_index(screen.console, _index(moved=[MOVED_INDEX, MOVED_METADATA]))

    assert printed is True
    # One line, on a console of 80 columns too: a file name is never cut in two.
    assert screen.raw == (
        "The search index could not be loaded. Moved aside, not deleted: "
        f"{MOVED_INDEX} and {MOVED_METADATA}. The stored embeddings are not affected. To "
        "rebuild the search index from them, run 'plexmix embeddings generate'.\n"
    )


def test_unmovable_index_line_says_that_it_stays_and_that_no_index_is_saved(
    screen: _Screen,
) -> None:
    assert print_unreadable_index(screen.console, _index(unmoved=[LEFT_INDEX])) is True

    assert screen.raw == (
        "The search index could not be loaded. Could not be moved aside, so left where it "
        f"is: {LEFT_INDEX}. This run does not write over it and saves no search index. Move "
        "it away yourself, then run 'plexmix embeddings generate' to rebuild the search "
        "index. The stored embeddings are not affected.\n"
    )


def test_two_unmovable_files_are_spoken_of_as_two(screen: _Screen) -> None:
    print_unreadable_index(screen.console, _index(unmoved=[LEFT_INDEX, LEFT_METADATA]))

    assert f"so left where they are: {LEFT_INDEX} and {LEFT_METADATA}." in screen.raw
    assert "does not write over them and saves no search index. Move them away" in screen.raw


def test_line_names_the_file_that_was_moved_and_the_one_that_was_not(screen: _Screen) -> None:
    print_unreadable_index(screen.console, _index(moved=[MOVED_INDEX], unmoved=[LEFT_METADATA]))

    assert screen.raw.count("\n") == 1
    assert f"Moved aside, not deleted: {MOVED_INDEX}." in screen.raw
    assert f"Could not be moved aside, so left where it is: {LEFT_METADATA}." in screen.raw
    assert "run 'plexmix embeddings generate' to rebuild the search index" in screen.raw


def test_failed_load_without_any_file_left_still_names_the_command(screen: _Screen) -> None:
    """Both files were gone by the time they were to be moved."""
    assert print_unreadable_index(screen.console, _index()) is True

    assert screen.raw == (
        "The search index could not be loaded. The stored embeddings are not affected. To "
        "rebuild the search index from them, run 'plexmix embeddings generate'.\n"
    )


def test_moved_file_names_are_printed_as_they_are(screen: _Screen) -> None:
    """A folder name with square brackets or an emoji code is no console markup here."""
    moved = Path("/music [live]/[red]:smile:/embeddings.index.unreadable-20261118-101500")

    print_unreadable_index(screen.console, _index(moved=[moved]))

    assert f"Moved aside, not deleted: {moved}." in screen.raw


def test_line_ignores_what_is_not_a_list_of_paths(screen: _Screen) -> None:
    index = SimpleNamespace(load_failed=True, moved_paths="embeddings.index", unmoved_paths=None)

    assert print_unreadable_index(screen.console, index) is True

    assert "Moved aside" not in screen.raw and "Could not be moved" not in screen.raw


def _saved_index_with_empty_metadata(folder: Path) -> Path:
    """Save a real index in ``folder``, then empty its metadata file. Returns the index path."""
    index_path = folder / "embeddings.index"
    saved = VectorIndex(dimension=4, index_path=str(index_path))
    saved.build_index([[1.0, 0.0, 0.0, 0.0]], [7])
    saved.save_index(str(index_path))
    index_path.with_suffix(".metadata").write_bytes(b"")
    return index_path


def test_line_for_a_real_index_whose_metadata_cannot_be_read(
    screen: _Screen, tmp_path: Path
) -> None:
    index_path = _saved_index_with_empty_metadata(tmp_path)

    loaded = VectorIndex(dimension=4, index_path=str(index_path))

    assert print_unreadable_index(screen.console, loaded) is True
    moved_index, moved_metadata = sorted(tmp_path.iterdir())
    assert moved_index.name.startswith("embeddings.index.unreadable-")
    assert moved_metadata.name.startswith("embeddings.metadata.unreadable-")
    assert f"Moved aside, not deleted: {moved_index} and {moved_metadata}." in screen.raw
    # The index that the command saves afterwards loads again, and nothing is said then.
    loaded.build_index([[0.0, 1.0, 0.0, 0.0]], [8])
    loaded.save_index(str(index_path))
    again = VectorIndex(dimension=4, index_path=str(index_path))
    assert again.track_ids == [8]
    assert print_unreadable_index(Console(file=io.StringIO()), again) is False


def test_line_for_a_real_index_that_cannot_be_moved_aside(
    screen: _Screen, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_path = _saved_index_with_empty_metadata(tmp_path)
    metadata_path = index_path.with_suffix(".metadata")

    def no_rename(source: Any, target: Any) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(vector_index_module.os, "rename", no_rename)
    loaded = VectorIndex(dimension=4, index_path=str(index_path))

    assert print_unreadable_index(screen.console, loaded) is True
    assert sorted(tmp_path.iterdir()) == [index_path, metadata_path]
    assert f"so left where they are: {index_path} and {metadata_path}." in screen.raw
    assert "saves no search index" in screen.raw
    assert "Moved aside" not in screen.raw


CLI_FOLDER = Path(console_helpers.__file__).parent
_INDEX_BUILDERS = {"build_vector_index", "VectorIndex"}
_STATEMENT_LISTS = ("body", "orelse", "finalbody")


def _called_name(node: Optional[ast.AST]) -> Optional[str]:
    """The name of the function a call node calls, without its module."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _built_index(statement: ast.stmt) -> Optional[str]:
    """The variable ``statement`` assigns a newly built search index to, if it does."""
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return None
    target = statement.targets[0]
    if isinstance(target, ast.Name) and _called_name(statement.value) in _INDEX_BUILDERS:
        return target.id
    return None


def _reports_index(statement: Optional[ast.stmt], name: str) -> bool:
    """Whether ``statement`` is ``print_unreadable_index(<console>, <name>)``."""
    if not isinstance(statement, ast.Expr):
        return False
    call = statement.value
    if _called_name(call) != "print_unreadable_index" or not isinstance(call, ast.Call):
        return False
    return len(call.args) == 2 and isinstance(call.args[1], ast.Name) and call.args[1].id == name


def _index_builds(source: str) -> List[Tuple[int, bool]]:
    """Where ``source`` builds a search index: the line, and whether the statement right
    after it hands that index to ``print_unreadable_index``."""
    found: List[Tuple[int, bool]] = []
    for node in ast.walk(ast.parse(source)):
        for field in _STATEMENT_LISTS:
            statements = getattr(node, field, None)
            if not isinstance(statements, list):
                continue
            for position, statement in enumerate(statements):
                name = _built_index(statement)
                if name is not None:
                    following = statements[position + 1 : position + 2]
                    reported = bool(following) and _reports_index(following[0], name)
                    found.append((statement.lineno, reported))
    return sorted(found)


@pytest.mark.parametrize(
    "vector_index, saved",
    [
        (None, True),
        (MagicMock(), True),
        (SimpleNamespace(unmoved_paths=[]), True),
        (SimpleNamespace(unmoved_paths="test.index"), True),
        (SimpleNamespace(unmoved_paths=[Path("/data/test.index")]), False),
    ],
)
def test_index_counts_as_saved_unless_a_file_could_not_be_moved_aside(
    vector_index: Any, saved: bool
) -> None:
    assert index_was_saved(vector_index) is saved


def test_real_index_that_cannot_be_moved_aside_was_not_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_file = tmp_path / "test.index"
    index_file.write_bytes(b"this is not a search index")

    def refuse(source: Any, target: Any) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(vector_index_module.os, "rename", refuse)
    vector_index = VectorIndex(dimension=4, index_path=str(index_file))
    vector_index.build_index([[1.0, 0.0, 0.0, 0.0]], [1])
    vector_index.save_index(str(index_file))

    assert index_was_saved(vector_index) is False
    assert index_file.read_bytes() == b"this is not a search index"
    assert index_was_saved(VectorIndex(dimension=4, index_path=str(tmp_path / "new.index")))


# ---------------------------------------------------------------------------
# The search index that was left as it is
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "count, line",
    [
        (
            1,
            "The search index was left as it is: 1 track holds an embedding that was made "
            "with other embedding settings. Run 'plexmix embeddings generate --regenerate' "
            "to replace it.",
        ),
        (
            1350,
            "The search index was left as it is: 1,350 tracks hold an embedding that was "
            "made with other embedding settings. Run 'plexmix embeddings generate "
            "--regenerate' to replace them.",
        ),
    ],
)
def test_index_that_was_left_as_it_is_is_said_with_the_count_and_the_command(
    screen: _Screen, count: int, line: str
) -> None:
    assert print_index_left_as_it_is(screen.console, count) is True
    assert screen.text == line
    # One sentence for the CLI, the log and the web UI: the one of the rebuild itself.
    assert line == left_out_sentence(count)


@pytest.mark.parametrize("count", [0, -1, True, None, "380", 3.0, MagicMock()])
def test_nothing_is_said_of_an_index_without_tracks_in_the_way(screen: _Screen, count: Any) -> None:
    assert print_index_left_as_it_is(screen.console, count) is False
    assert screen.raw == ""


@pytest.mark.parametrize(
    "vector_index, left_out",
    [
        (None, 0),
        (MagicMock(), 0),
        (SimpleNamespace(), 0),
        (SimpleNamespace(last_rebuild=None), 0),
        # Only the result of a rebuild counts, not a number or a mock in its place.
        (SimpleNamespace(last_rebuild=380), 0),
        (SimpleNamespace(last_rebuild=MagicMock(left_out=380)), 0),
        (SimpleNamespace(last_rebuild=IndexRebuild(vectors=400, saved=True)), 0),
        (SimpleNamespace(last_rebuild=IndexRebuild()), 0),
        (SimpleNamespace(last_rebuild=IndexRebuild(left_out=380)), 380),
    ],
)
def test_tracks_in_the_way_are_read_from_the_last_rebuild(vector_index: Any, left_out: int) -> None:
    assert index_left_out(vector_index) == left_out
    result = getattr(vector_index, "last_rebuild", None)
    expected = result if isinstance(result, IndexRebuild) else None
    assert last_index_rebuild(vector_index) is expected


def _library_with_two_vector_sizes(folder: Path) -> Tuple[Path, Path]:
    """Three tracks and a saved index of 4-number vectors that covers all of them.

    Then the vector size changed to 8 and only track 3 got its new embedding, so
    tracks 1 and 2 still hold one of 4 numbers under the same model name. Returns
    the database file and the index file.
    """
    database_file, index_file = folder / "test.db", folder / "test.index"
    with SQLiteManager(str(database_file)) as db:
        db.create_tables()
        artist_id = db.insert_artist(Artist(plex_key="a1", name="Artist One"))
        album_id = db.insert_album(Album(plex_key="al1", title="Album One", artist_id=artist_id))
        for number in (1, 2, 3):
            db.insert_track(
                Track(
                    plex_key=f"t{number}",
                    title=f"Song {number}",
                    artist_id=artist_id,
                    album_id=album_id,
                    play_count=0,
                )
            )
        old = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        db.insert_embeddings_batch(
            [
                Embedding(track_id=number, embedding_model="custom", embedding_dim=4, vector=vector)
                for number, vector in zip((1, 2, 3), old)
            ]
        )
        saved = VectorIndex(dimension=4, index_path=str(index_file))
        saved.build_index(old, [1, 2, 3])
        assert saved.save_index(str(index_file)) is True
        db.insert_embedding(
            Embedding(track_id=3, embedding_model="custom", embedding_dim=8, vector=[0.5] * 8)
        )
    return database_file, index_file


def test_refused_rebuild_of_a_real_index_is_reported_and_keeps_the_saved_index(
    screen: _Screen, tmp_path: Path
) -> None:
    """Replaces the check of the count that a partial build left out: nothing is
    built from the rows that fit any more. The saved index stays, byte for byte, and
    with it the dimension mismatch that tells every command to regenerate."""
    database_file, index_file = _library_with_two_vector_sizes(tmp_path)
    files = [index_file, index_file.with_suffix(".metadata")]
    before = [file.read_bytes() for file in files]
    vector_index = VectorIndex(dimension=8, index_path=str(index_file))
    assert vector_index.dimension_mismatch is True
    assert index_left_out(vector_index) == 0  # no rebuild ran yet

    with SQLiteManager(str(database_file)) as db:
        built = tagging_service.rebuild_vector_index(db, vector_index, str(index_file), "custom")

    assert built == 0
    assert index_left_out(vector_index) == 2
    rebuilt = last_index_rebuild(vector_index)
    assert rebuilt is not None and (rebuilt.vectors, rebuilt.saved, rebuilt.refused) == (
        0,
        False,
        True,
    )
    assert print_index_left_as_it_is(screen.console, index_left_out(vector_index)) is True
    assert screen.text == (
        "The search index was left as it is: 2 tracks hold an embedding that was made with "
        "other embedding settings. Run 'plexmix embeddings generate --regenerate' to "
        "replace them."
    )
    # Nothing was built and nothing was saved.
    assert [file.read_bytes() for file in files] == before
    assert vector_index.dimension_mismatch is True
    # No file was moved aside and none was added beside the index.
    assert sorted(path.name for path in tmp_path.iterdir() if "test.db" not in path.name) == [
        "test.index",
        "test.metadata",
    ]
    reopened = VectorIndex(dimension=8, index_path=str(index_file))
    assert reopened.dimension_mismatch is True and reopened.loaded_dimension == 4
    # An unsaved index is another matter, and its line is not printed for this one.
    assert index_was_saved(vector_index) is True


def test_rebuild_that_is_complete_leaves_no_tracks_in_the_way(tmp_path: Path) -> None:
    database_file, index_file = _library_with_two_vector_sizes(tmp_path)
    with SQLiteManager(str(database_file)) as db:
        db.insert_embeddings_batch(
            [
                Embedding(track_id=number, embedding_model="custom", embedding_dim=8, vector=vector)
                for number, vector in ((1, [0.1] * 8), (2, [0.2] * 8))
            ]
        )
        vector_index = VectorIndex(dimension=8, index_path=str(index_file))

        built = tagging_service.rebuild_vector_index(db, vector_index, str(index_file), "custom")

    assert built == 3 and index_left_out(vector_index) == 0
    rebuilt = last_index_rebuild(vector_index)
    assert rebuilt is not None and rebuilt.saved is True
    reopened = VectorIndex(dimension=8, index_path=str(index_file))
    assert reopened.dimension_mismatch is False and sorted(reopened.track_ids) == [1, 2, 3]


def _index_writes(source: str) -> List[Tuple[int, str]]:
    """Where ``source`` builds or saves a search index by itself: (line, method)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"build_index", "save_index"}:
                found.append((node.lineno, node.func.attr))
    return sorted(found)


def test_no_cli_command_builds_or_saves_the_index_by_itself() -> None:
    """An index that is built from the rows may be saved only when it is complete.

    ``plexmix.database.index_rebuild.rebuild_index`` is the one place that knows, so
    no command builds or saves the index itself, and none reads what the partial
    build of an earlier version left behind.
    """
    assert _index_writes("index.build_index(vectors, ids)\nindex.save_index(path)\n") == [
        (1, "build_index"),
        (2, "save_index"),
    ]
    assert _index_writes("rebuilt = rebuild_index(db, index, model)\n") == []

    modules = sorted(CLI_FOLDER.glob("*.py"))
    assert CLI_FOLDER / "doctor_cmd.py" in modules
    offenders = [
        f"{module.name}:{line} calls {method}()"
        for module in modules
        for line, method in _index_writes(module.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    for module in modules:
        source = module.read_text(encoding="utf-8")
        for gone in ("skipped_vectors", "print_left_out_of_index", "not in the search index"):
            assert gone not in source, f"{module.name} still holds {gone!r}"


def test_index_build_check_sees_a_build_without_the_notice() -> None:
    source = (
        "def run():\n"
        "    first = build_vector_index(settings, generator)\n"
        "    use(first)\n"
        "    if ready:\n"
        "        second = VectorIndex(dimension=4)\n"
        "        print_unreadable_index(console, second)\n"
        "    third = module.VectorIndex(4)\n"
        "    print_unreadable_index(console, first)\n"
        "    fourth = build_vector_index(settings, generator)\n"
    )

    assert _index_builds(source) == [(2, False), (5, True), (7, False), (9, False)]


def test_every_cli_command_that_builds_the_index_reports_a_failed_load() -> None:
    """Design D18: ``create``, ``embeddings generate``, ``tags generate``, the sync
    commands and ``doctor`` each say so when the saved index could not be loaded. A
    command added later that builds the index must do the same."""
    builds = {}
    for module in sorted(CLI_FOLDER.glob("*.py")):
        found = _index_builds(module.read_text(encoding="utf-8"))
        if found:
            builds[module.name] = found

    assert set(builds) >= {
        "create_cmd.py",
        "doctor_cmd.py",
        "embeddings_cmd.py",
        "sync_cmd.py",
        "tags_cmd.py",
    }
    unreported = [
        f"{name}:{line}"
        for name, found in builds.items()
        for line, reported in found
        if not reported
    ]
    assert unreported == []
