"""Render smoke: the shared scope confirmation (webui-honest-status-and-first-run 4.1).

``scope_confirm`` must render as a Radix AlertDialog (role=alertdialog, focus
trap, Escape through ``onOpenChange``) whose safe button is the AlertDialog
Cancel part, whose confirm/secondary buttons are plain buttons (never an
AlertDialog Action), which returns focus through ``pmFocus.restore()`` and adds
no colour literals. Real Reflex only: ``pytest tests/ui_render -q``.
"""

from __future__ import annotations

import re
from pathlib import Path

import reflex as rx

from plexmix.ui.components import scope_confirm as sc
from tests.ui_render.conftest import rendered

_RESTORE = '"javascript_code"] : "window.pmFocus&&pmFocus.restore()"'
_SOURCE = Path(sc.__file__)


class _ScopeProbeState(rx.State):
    """Vars shaped like a page state's pending scope."""

    probe_open: bool = False
    probe_scope: dict[str, str] = {}
    probe_replace: bool = False

    @rx.event
    def set_probe_open(self, is_open: bool) -> None:
        if not is_open:
            self.probe_open = False

    @rx.event
    def probe_confirm(self) -> None:
        self.probe_open = False

    @rx.event
    def probe_secondary(self) -> None:
        self.probe_open = False


def _probe_dialog(**overrides: object) -> rx.Component:
    scope = _ScopeProbeState.probe_scope
    kwargs: dict[str, object] = dict(
        open_var=_ScopeProbeState.probe_open,
        on_open_change=_ScopeProbeState.set_probe_open,
        title=scope["title"],
        lines=[
            scope["count_line"],
            scope["replace_line"],
            sc.scope_provider_line(scope["provider_label"], scope["model"]),
            scope["duration_line"],
        ],
        privacy_note=scope["privacy_line"],
        confirm_label=scope["confirm_label"],
        on_confirm=_ScopeProbeState.probe_confirm,
        secondary_label=scope["secondary_label"],
        on_secondary=_ScopeProbeState.probe_secondary,
        danger=_ScopeProbeState.probe_replace,
        id_prefix="probe-confirm",
    )
    kwargs.update(overrides)
    return sc.scope_confirm(**kwargs)  # type: ignore[arg-type]


def _buttons(js: str) -> list[str]:
    """Each ``jsx("button",{...}`` props block."""
    return re.findall(r'jsx\("button",\{.*?type:"button"\}', js)


class TestScopeConfirmRender:
    def test_is_a_controlled_alert_dialog(self) -> None:
        js = rendered(_probe_dialog())
        assert js.startswith("jsx(RadixThemesAlertDialog.Root,")
        assert "jsx(RadixThemesAlertDialog.Content," in js
        assert "jsx(RadixThemesAlertDialog.Title," in js
        assert "jsx(RadixThemesAlertDialog.Description," in js
        assert "probe_open_rx_state_" in js
        assert "_scope_probe_state.set_probe_open" in js
        assert '["is_open"] : _ev_0' in js

    def test_content_is_modal_and_restores_focus(self) -> None:
        js = rendered(_probe_dialog())
        content = js[js.index("jsx(RadixThemesAlertDialog.Content,{") :]
        assert content.startswith('jsx(RadixThemesAlertDialog.Content,{"aria-modal":"true"')
        assert "onCloseAutoFocus:" in content
        assert _RESTORE in js
        assert 'id:"probe-confirm"' in js

    def test_only_the_safe_button_is_an_alert_dialog_part(self) -> None:
        js = rendered(_probe_dialog())
        assert js.count("jsx(RadixThemesAlertDialog.Cancel,") == 1
        assert "AlertDialog.Action" not in js
        cancel = js[js.index("jsx(RadixThemesAlertDialog.Cancel,") :]
        assert cancel.startswith(
            'jsx(RadixThemesAlertDialog.Cancel,{},jsx("button",{className:"btn btn-3 btn-soft",'
            'id:"probe-confirm-cancel",ref:ref_probe_confirm_cancel,type:"button"},"Cancel"))'
        )

    def test_confirm_and_secondary_are_plain_buttons(self) -> None:
        js = rendered(_probe_dialog())
        buttons = _buttons(js)
        assert len(buttons) == 3
        confirm = [b for b in buttons if 'id:"probe-confirm-confirm"' in b]
        secondary = [b for b in buttons if 'id:"probe-confirm-secondary"' in b]
        assert len(confirm) == 1 and len(secondary) == 1
        assert "_scope_probe_state.probe_confirm" in confirm[0]
        assert "_scope_probe_state.probe_secondary" in secondary[0]
        assert 'className:"btn btn-3 btn-soft"' in secondary[0]
        # Danger is a state flag here: btn-danger while replacing, else btn-primary.
        assert '"btn btn-3 btn-danger"' in confirm[0] and '"btn btn-3 btn-primary"' in confirm[0]
        # The secondary action renders only while its label is non-empty.
        assert '?.["secondary_label"]?.valueOf?.() === ""' in js

    def test_static_danger_and_no_secondary(self) -> None:
        js = rendered(
            sc.scope_confirm(
                _ScopeProbeState.probe_open,
                _ScopeProbeState.set_probe_open,
                "Rebuild embeddings for 5,943 tracks?",
                ["The embeddings for 5,943 tracks will be replaced."],
                "Runs on this machine. Nothing is sent to a cloud provider.",
                "Rebuild embeddings",
                _ScopeProbeState.probe_confirm,
                danger=True,
            )
        )
        buttons = _buttons(js)
        assert len(buttons) == 2
        assert 'className:"btn btn-3 btn-danger"' in js
        assert "btn-primary" not in js
        assert '"Rebuild embeddings for 5,943 tracks?"' in js
        assert '"Runs on this machine. Nothing is sent to a cloud provider."' in js

    def test_scope_lines_are_block_spans_and_the_model_is_mono(self) -> None:
        js = rendered(_probe_dialog())
        assert js.count('className:"scope-line"') == 4
        assert 'className:"scope-note"' in js
        assert 'jsx("span",{className:"mono"},' in js
        # Empty lines (e.g. no replace line) render nothing.
        assert '?.["replace_line"]?.valueOf?.() === ""' in js

    def test_no_colour_literals(self) -> None:
        source = _SOURCE.read_text()
        assert re.search(r"color:|#[0-9a-fA-F]{3,6}", source) is None
        js = rendered(_probe_dialog())
        assert re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", js) is None


class TestEmbedAllConfirmRender:
    def test_bound_to_library_state(self) -> None:
        js = rendered(sc.embed_all_confirm_dialog())
        assert js.startswith("jsx(RadixThemesAlertDialog.Root,")
        assert "embed_all_open_rx_state_" in js
        assert "library_state.set_embed_all_open" in js
        assert "library_state.confirm_embed_all" in js
        assert 'id:"embed-all-confirm"' in js
        assert _RESTORE in js
        assert "AlertDialog.Action" not in js
        for key in ("title", "count_line", "duration_line", "privacy_line", "confirm_label"):
            assert f'embed_all_scope_rx_state_?.["{key}"]' in js
        assert 'jsx("span",{className:"mono"},' in js
