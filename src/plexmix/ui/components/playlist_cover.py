"""Deterministic, decorative playlist cover swatch shared by History and Generator.

The swatch is four quadrants whose colors come from ``.cover-v0`` … ``.cover-v5``
in ``assets/redesign.css`` (design tokens only). The variant is picked by
``plexmix.ui.utils.playlist_identity.cover_variant`` from the playlist's prompt
(or its name), so the same vibe shows the same swatch on every page and after a
server restart.

The cover is never album art and carries no information, so it is hidden from
assistive technology. It renders as a ``span`` so it is valid phrasing content
inside the History card ``<button>``; the container classes give it its grid.
"""

from __future__ import annotations

import reflex as rx

# Number of cover variants defined in assets/redesign.css (.cover-v0 … .cover-v5).
# Must match the ``n`` passed to ``cover_variant`` (its default is 6).
COVER_VARIANT_COUNT = 6

COVER_CLASS_PREFIX = "cover-v"


def playlist_cover(variant: rx.Var | int, class_name: str = "pl-cover") -> rx.Component:
    """Render the four-quadrant cover swatch for ``variant``.

    Args:
        variant: The cover variant, ``0`` … ``COVER_VARIANT_COUNT - 1``. Either a
            plain int or a state Var (an int Var, or the string Var that History
            stores in its ``dict[str, str]`` rows).
        class_name: The container class that sizes and lays out the swatch:
            ``"pl-cover"`` on History cards, ``"result-cover"`` on Generator results.

    Returns:
        A ``span`` with ``class_name`` plus ``cover-v<variant>``, four quadrant
        ``span`` children, and ``aria-hidden="true"``.
    """
    if isinstance(variant, int):
        variant = variant % COVER_VARIANT_COUNT
    # An f-string with a Var compiles to a JS concatenation ("pl-cover cover-v" + v)
    # for both int and string Vars; ``Var.to_string()`` would JSON-quote a string Var.
    classes = f"{class_name} {COVER_CLASS_PREFIX}{variant}"
    return rx.el.span(
        *[rx.el.span() for _ in range(4)],
        class_name=classes,
        aria_hidden="true",
    )
