"""Shared utility functions for Reflex UI states."""

import math
import re
from datetime import date, datetime, timedelta
from typing import Optional, Union

# A value a readout formatter accepts: a number, a numeric string as Reflex
# state often holds it (see ``str_dict``), or None while it is unknown.
Numeric = Union[int, float, str, None]

# A timestamp a date formatter accepts: a ``datetime`` (naive local time, as the
# models store it, or timezone-aware), a ``date``, an ISO string as SQLite returns
# it ("2025-12-27 20:35:19.327064", "2025-12-27T20:35:19Z"), or None.
Timestamp = Union[datetime, date, str, None]

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_FRACTION_RE = re.compile(r"\.(\d+)")
_COMPACT_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")


def str_dict(d: dict) -> dict[str, str]:
    """Convert a dict with mixed-type values to all-string values for Reflex."""
    return {k: ("" if v is None else str(v)) for k, v in d.items()}


def format_tags_for_display(tags: str) -> str:
    """Format a stored comma-separated tag list for display: ``"a,b"`` -> ``"a, b"``.

    Tags are stored comma-joined, usually without spaces (``"indie pop,dreamy"``),
    which gives the browser nowhere to wrap a long list. This splits on commas,
    strips whitespace around each tag, drops empty entries and repeats (keeping the
    first occurrence, in stored order) and joins with ``", "``. Empty input gives "".
    """
    if not tags:
        return ""
    shown: list[str] = []
    for part in tags.split(","):
        tag = part.strip()
        if tag and tag not in shown:
            shown.append(tag)
    return ", ".join(shown)


def format_eta(seconds: float) -> str:
    """Format remaining seconds as a human-readable ETA string."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s remaining"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s remaining"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m remaining"


def safe_error_text(exc: BaseException) -> str:
    """Describe *exc* for logs and toasts without echoing rejected input values.

    A pydantic ``ValidationError`` (for example from ``Settings.load_from_file`` on a
    hand-edited config.yaml) embeds each rejected ``input_value`` in its text, and
    that value can be a stored secret such as an all-digit token. For those only
    the field locations and messages are kept. A YAML parse error quotes the
    offending config.yaml line (which can hold a plaintext token), so only its
    problem and position are kept. Other exceptions keep their text.
    """
    from pydantic import ValidationError

    if not isinstance(exc, ValidationError):
        yaml_text = _yaml_error_text(exc)
        return yaml_text if yaml_text is not None else str(exc)
    problems = []
    for error in exc.errors():
        # Only the location and the message: never error["input"] or its context.
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        problems.append(f"{location}: {message}" if location else message)
    count = len(problems)
    noun = "setting" if count == 1 else "settings"
    return f"{count} invalid {noun} ({'; '.join(problems)})"


def is_yaml_error(exc: BaseException) -> bool:
    """True for a PyYAML error (checked by module, so PyYAML needs no type stubs)."""
    return type(exc).__module__.split(".", 1)[0] == "yaml"


def _yaml_error_text(exc: BaseException) -> Optional[str]:
    """A PyYAML parse error without its source snippet, or None for anything else."""
    if not is_yaml_error(exc):
        return None
    found = (getattr(exc, "context", None), getattr(exc, "problem", None))
    parts = [str(part) for part in found if part]
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    line, column = getattr(mark, "line", None), getattr(mark, "column", None)
    where = (
        f" (line {line + 1}, column {column + 1})"
        if isinstance(line, int) and isinstance(column, int)
        else ""
    )
    return f"Invalid YAML: {'; '.join(parts) or 'syntax error'}{where}"


# ── Readout formatting ──────────────────────────────────────────────────────
# Every formatter below returns "" when its input is missing or unparseable, so
# a caller can choose the wording for "unknown" (for example "Not synced yet")
# instead of the readout inventing a zero or echoing a raw value.


def _coerce_number(value: Numeric) -> Optional[float]:
    """Return *value* as a finite float, or None when it is not a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _round_half_up(number: float) -> int:
    """Round to the nearest integer, halves away from zero (2.5 -> 3, -2.5 -> -3)."""
    return int(math.floor(abs(number) + 0.5)) * (-1 if number < 0 else 1)


def format_count(value: Numeric) -> str:
    """Format a count with thousands separators: ``5943`` -> ``"5,943"``.

    Floats are rounded to a whole number. None, booleans and non-numeric values
    give "".
    """
    number = _coerce_number(value)
    if number is None:
        return ""
    return f"{_round_half_up(number):,}"


def format_percent(part: Numeric, total: Numeric) -> str:
    """Format *part* as a whole-number share of *total*: ``(4120, 5943)`` -> ``"69%"``.

    The readout never claims more than it measured: a non-zero share that rounds
    to 0 shows "<1%", one that rounds up to 100 while incomplete shows ">99%", and
    a part above the total is capped at "100%". An empty total gives "0%". Missing,
    non-numeric or negative inputs give "".
    """
    part_number = _coerce_number(part)
    total_number = _coerce_number(total)
    if part_number is None or total_number is None or part_number < 0 or total_number < 0:
        return ""
    if total_number == 0:
        return "0%"
    percent = min(part_number / total_number, 1.0) * 100
    rounded = _round_half_up(percent)
    if rounded == 0 and percent > 0:
        return "<1%"
    if rounded == 100 and percent < 100:
        return ">99%"
    return f"{rounded}%"


def pluralize(count: Numeric, singular: str, plural: Optional[str] = None) -> str:
    """Return the grouped count and the matching noun: ``1`` -> ``"1 track"``.

    ``plural`` defaults to *singular* + "s"; pass it for irregular nouns
    (``pluralize(n, "match", "matches")``). A missing or non-numeric count gives "".
    """
    number = _coerce_number(count)
    if number is None:
        return ""
    whole = _round_half_up(number)
    noun = singular if abs(whole) == 1 else (plural if plural is not None else f"{singular}s")
    return f"{whole:,} {noun}"


def _coerce_datetime(value: Timestamp) -> Optional[datetime]:
    """Parse *value* into a ``datetime``; None when it is missing or unparseable.

    Accepts what the database hands back on Python 3.10+: ISO text with a space
    or "T" separator, any number of fractional-second digits, a "Z" suffix and a
    "+0000" or "+00:00" offset.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    text = _COMPACT_OFFSET_RE.sub(r"\1:\2", text) if "T" in text or " " in text else text
    # Python 3.10's fromisoformat only takes exactly 3 or 6 fractional digits.
    text = _FRACTION_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _as_local_naive(moment: datetime) -> Optional[datetime]:
    """Return *moment* as a naive local time (aware values are converted)."""
    if moment.tzinfo is None:
        return moment
    try:
        return moment.astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _short_date(moment: datetime, with_year: bool) -> str:
    text = f"{_MONTHS[moment.month - 1]} {moment.day}"
    return f"{text}, {moment.year}" if with_year else text


def format_short_datetime(dt: Timestamp, *, with_year: bool = False) -> str:
    """Format a timestamp as ``"Dec 27 · 20:35"`` (no seconds or microseconds).

    ``"2025-12-27 20:35:19.327064"`` -> ``"Dec 27 · 20:35"``. Timezone-aware values
    are shown in the server's local time, like the naive local times the models
    store. ``with_year=True`` gives ``"Dec 27, 2025 · 20:35"`` (for a ``title``).
    Missing or unparseable input gives "".
    """
    parsed = _coerce_datetime(dt)
    moment = _as_local_naive(parsed) if parsed is not None else None
    if moment is None:
        return ""
    return f"{_short_date(moment, with_year)} · {moment.hour:02d}:{moment.minute:02d}"


def format_relative(dt: Timestamp, now: Optional[datetime] = None) -> str:
    """Describe how long ago *dt* was: ``"just now"``, ``"5 min ago"``, ``"2 h ago"``.

    Steps: under a minute "just now", then whole minutes ("N min ago"), whole
    hours ("N h ago") and whole days ("N d ago") up to 30 days; anything older is
    the date ("Dec 27", or "Dec 27, 2024" in another year than *now*). A time
    more than a minute in the future (clock skew) is shown as its date and time
    rather than a made-up relative phrase.

    *now* defaults to ``datetime.now()``. Naive values are local times (the
    models store ``datetime.now()``); when one side is aware and the other naive
    the naive one is read as local time. Missing or unparseable input gives "".
    """
    parsed = _coerce_datetime(dt)
    if parsed is None:
        return ""
    reference = now if now is not None else datetime.now()
    try:
        if (parsed.tzinfo is None) != (reference.tzinfo is None):
            parsed = parsed.astimezone()
            reference = reference.astimezone()
        delta = reference - parsed
    except (OverflowError, OSError, ValueError):
        return ""
    seconds = delta.total_seconds()
    if seconds < -60:
        return format_short_datetime(parsed)
    if seconds < 60:
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(seconds // 60)} min ago"
    if delta < timedelta(days=1):
        return f"{int(seconds // 3600)} h ago"
    if delta < timedelta(days=30):
        return f"{delta.days} d ago"
    moment = _as_local_naive(parsed)
    reference_local = _as_local_naive(reference)
    if moment is None or reference_local is None:
        return ""
    return _short_date(moment, with_year=moment.year != reference_local.year)
