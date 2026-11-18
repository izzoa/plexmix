"""Tests for the helpers the CLI commands share (``plexmix.cli.console_helpers``)."""

import sys
from typing import Optional

import pytest

from plexmix.cli.console_helpers import can_prompt, format_size


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
