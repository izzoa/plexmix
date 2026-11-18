"""Pure accessibility helpers for the Web UI (no Reflex import).

Everything here is plain Python so it can be unit-tested without the ``ui``
extra (CI installs without it, and ``tests/ui`` replaces Reflex with a mock).
States and pages import these helpers; the scripts they return are passed to
``rx.call_script`` and rely on ``window.pmFocus`` from ``assets/plexmix.js``
(block 4, "Focus continuity").
"""

from __future__ import annotations

import json
import re

# Settings section ids, in tab order. Used for the tab values and for the
# ``/settings?section=<id>`` deep-link contract.
SETTINGS_SECTIONS: tuple[str, ...] = (
    "plex",
    "ai",
    "embedding",
    "audio",
    "musicbrainz",
    "advanced",
    "appearance",
)

# Element ids a focus script may target: a letter or underscore, then word
# characters or hyphens. Generated ids (``tag-edit-btn-12``, ``hist-move-up-3``)
# fit, and the restriction keeps the id safe inside a CSS selector and a JS string.
_SAFE_ELEMENT_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def resolve_settings_section(param: str | None, current: str) -> str:
    """Return the section a ``?section=`` value selects, or ``current``.

    Unknown, empty or missing values keep the currently selected section, so a
    bad link never errors and never resets the user's place.
    """
    if not param:
        return current
    candidate = param.strip().lower()
    return candidate if candidate in SETTINGS_SECTIONS else current


def settings_section_url_script(tab: str) -> str:
    """Return JS that rewrites the address bar to ``/settings?section=<tab>`` in place.

    Uses ``history.replaceState`` with the current ``history.state`` (keeps React
    Router's entry key), so no navigation happens and ``on_load`` does not re-run.

    Raises:
        ValueError: if ``tab`` is not one of :data:`SETTINGS_SECTIONS`.
    """
    if tab not in SETTINGS_SECTIONS:
        raise ValueError(f"Unknown settings section: {tab!r}")
    return f"history.replaceState(history.state,'','/settings?section={tab}')"


def focus_by_id_script(element_id: str) -> str:
    """Return JS that moves focus to ``#element_id`` once it exists (forced).

    Calls ``pmFocus.when('#id', {force: true})``, which polls for the element for
    about 60 animation frames and focuses it even if focus was not lost.

    Raises:
        ValueError: if ``element_id`` is not a plain id (letters, digits, ``_``, ``-``).
    """
    if not _SAFE_ELEMENT_ID.match(element_id):
        raise ValueError(f"Unsafe element id for a focus script: {element_id!r}")
    return f"window.pmFocus&&pmFocus.when('#{element_id}',{{force:true}})"


def focus_first_script(selectors: list[str], force: bool = False) -> str:
    """Return JS that focuses the first element matching one of ``selectors``.

    Calls ``pmFocus.when([...])``. Without ``force`` focus only moves when it was
    lost (the previously focused element left the DOM), so a fresh page load or a
    user who is already elsewhere is never hijacked.
    """
    selector_list = json.dumps([str(s) for s in selectors])
    if force:
        return f"window.pmFocus&&pmFocus.when({selector_list},{{force:true}})"
    return f"window.pmFocus&&pmFocus.when({selector_list})"


def aria_sort_value(column: str, sort_column: str, ascending: bool) -> str:
    """Return the ``aria-sort`` value for a column header.

    ``"ascending"``/``"descending"`` for the sorted column, ``"none"`` otherwise.
    """
    if column != sort_column:
        return "none"
    return "ascending" if ascending else "descending"


def _flag(value: object) -> bool:
    """Interpret a stringified DB flag (``"1"``/``"0"``, ``"True"``, ...) as a bool."""
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY


def enrichment_flags(row: dict[str, str]) -> dict[str, str]:
    """Return per-track enrichment flags and a screen-reader label for a Library row.

    Reads ``tags``, ``has_embedding``, ``has_audio_features`` and
    ``has_musicbrainz`` (missing keys count as absent). Returns ``enr_tags``,
    ``enr_embedded``, ``enr_audio`` and ``enr_mb`` as ``"1"``/``"0"``, plus
    ``enr_label``, for example
    "Tagged, embedded, audio not analyzed, MusicBrainz not matched".
    """
    tags_value = row.get("tags")
    # Same definition as the library-wide counts (``tags IS NOT NULL AND
    # tags != ''``): any non-empty value is tagged, whitespace included.
    tagged = tags_value is not None and str(tags_value) != ""
    embedded = _flag(row.get("has_embedding"))
    audio = _flag(row.get("has_audio_features"))
    musicbrainz = _flag(row.get("has_musicbrainz"))

    parts = [
        "tagged" if tagged else "not tagged",
        "embedded" if embedded else "not embedded",
        "audio analyzed" if audio else "audio not analyzed",
        "MusicBrainz matched" if musicbrainz else "MusicBrainz not matched",
    ]
    label = ", ".join(parts)
    label = label[0].upper() + label[1:]

    return {
        "enr_tags": "1" if tagged else "0",
        "enr_embedded": "1" if embedded else "0",
        "enr_audio": "1" if audio else "0",
        "enr_mb": "1" if musicbrainz else "0",
        "enr_label": label,
    }
