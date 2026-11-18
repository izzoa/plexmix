"""Small helpers shared by the CLI commands."""

import sys
from typing import Optional, TextIO

_SIZE_UNITS = ("KB", "MB", "GB", "TB")


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
