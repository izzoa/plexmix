"""Render smoke: every blocking overlay exposes a modal state (``aria-modal="true"``).

Radix Dialog/AlertDialog set the role and ``aria-labelledby`` but not
``aria-modal``; each content passes it, and Radix Themes spreads it onto the
primitive content element. Real Reflex only: ``pytest tests/ui_render -q``.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
import reflex as rx

from plexmix.ui.components import navbar
from plexmix.ui.components.progress_modal import progress_modal
from plexmix.ui.pages import history as history_page
from plexmix.ui.pages import library as library_page
from tests.ui_render.conftest import rendered

_CONTENT = re.compile(r"jsx\(RadixThemes(?:Alert)?Dialog\.Content,\{")

_BUILDERS: list[tuple[str, Callable[[], rx.Component]]] = [
    ("history detail", history_page._detail_dialog),
    ("history delete confirm", history_page._delete_confirm_dialog),
    ("library bulk tag", library_page._bulk_tag_dialog),
    ("library regenerate confirm", library_page._regenerate_confirm_dialog),
    ("library delete confirm", library_page._delete_confirm_dialog),
    ("shell cancel confirm", navbar._cancel_confirm_dialog),
    ("changelog", navbar._changelog_dialog),
    ("progress modal", lambda: progress_modal(True, 10, "Working")),
]


@pytest.mark.parametrize("name,builder", _BUILDERS, ids=[n for n, _ in _BUILDERS])
def test_dialog_content_is_modal(name: str, builder: Callable[[], rx.Component]) -> None:
    js = rendered(builder())
    starts = [m.end() for m in _CONTENT.finditer(js)]
    assert len(starts) == 1, name
    # Props are emitted with sorted keys, so the quoted "aria-modal" comes first.
    assert js[starts[0] :].startswith('"aria-modal":"true"'), name
