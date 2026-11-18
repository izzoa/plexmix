"""Pure playlist-identity helpers for the Web UI (no Reflex import).

Covers, dates and artist summaries shown on History cards and Generator
results. Everything is deterministic across processes and server restarts:
the cover variant uses ``zlib.crc32``, never the per-process salted ``hash()``.
"""

from __future__ import annotations

import datetime as _dt
import re
import zlib

# "YYYY-MM-DD", optionally followed by a time ("T" or space separated), any
# fractional-second precision and a "Z" or "+HH:MM" offset. Python 3.10's
# ``fromisoformat`` rejects "Z" and odd fraction lengths, so parse explicitly.
_DATE_PREFIX = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?)?$"
)


def cover_variant(seed: str, n: int = 6) -> int:
    """Return a stable cover variant in ``range(n)`` for ``seed``.

    The seed is stripped and lower-cased first, so "Late night jazz " and
    "late night jazz" share a swatch.

    Raises:
        ValueError: if ``n`` is less than 1.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    normalized = (seed or "").strip().lower()
    return zlib.crc32(normalized.encode("utf-8")) % n


def cover_seed(mood_query: str, name: str) -> str:
    """Return the cover seed: the prompt when there is one, otherwise the name."""
    prompt = (mood_query or "").strip()
    return prompt if prompt else (name or "").strip()


def short_date(created_at: str) -> str:
    """Return ``created_at`` as ``YYYY-MM-DD``, or ``""`` when it can't be parsed.

    Accepts SQLite timestamps (``"2025-12-27 20:35:19.327064"``), ISO 8601 with
    ``T``, ``Z`` or an offset, and bare dates. The date is taken as written (no
    timezone conversion).
    """
    if not isinstance(created_at, str):
        return ""
    match = _DATE_PREFIX.match(created_at.strip())
    if not match:
        return ""
    year, month, day = (int(part) for part in match.groups())
    try:
        return _dt.date(year, month, day).isoformat()
    except ValueError:
        return ""


def top_artists_label(names: list[str], limit: int = 3) -> str:
    """Join up to ``limit`` distinct, non-empty artist names with ", ".

    Order is preserved (callers pass names most-frequent first).
    """
    if limit < 1:
        return ""
    picked: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = (raw or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        picked.append(name)
        if len(picked) >= limit:
            break
    return ", ".join(picked)
