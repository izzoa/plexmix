"""Render smoke: shared radio-segment component and the shell's page structure."""

from __future__ import annotations

import re

import reflex as rx

from plexmix.ui.components.navbar import layout
from plexmix.ui.components.seg_radio import seg_radio_group
from tests.ui_render.conftest import rendered


class _SegProbeState(rx.State):
    """Minimal state so the radio group binds to a real Var and handler."""

    choice: str = "similarity"

    @rx.event
    def set_choice(self, value: str) -> None:
        self.choice = value


def _seg_js() -> str:
    return rendered(
        seg_radio_group(
            name="probe-order",
            legend="Track ordering",
            options=[("similarity", "Similarity"), ("random", "Random")],
            current=_SegProbeState.choice,
            on_select=_SegProbeState.set_choice,
            legend_id="probe-order-legend",
        )
    )


class TestSegRadioGroup:
    def test_two_native_radios(self) -> None:
        js = _seg_js()
        assert js.count('jsx("input",') == 2
        assert js.count('type:"radio"') == 2

    def test_single_shared_name(self) -> None:
        names = re.findall(r'\bname:"([^"]*)"', _seg_js())
        assert names == ["probe-order", "probe-order"]
        assert len(set(names)) == 1

    def test_fieldset_legend_and_group_semantics(self) -> None:
        js = _seg_js()
        assert js.startswith('jsx("fieldset",')
        assert 'className:"seg-field"' in js
        assert 'jsx("legend",{className:"field-label",id:"probe-order-legend"' in js
        assert '"Track ordering"' in js
        assert '"aria-labelledby":"probe-order-legend"' in js
        assert 'role:"radiogroup"' in js
        assert '["role"]' not in js  # never leaked into CSS

    def test_segments_are_label_input_span(self) -> None:
        js = _seg_js()
        assert 'jsx("div",{className:"seg"}' in js
        assert js.count('jsx("label",') == 2
        assert js.count('className:"sr-only"') == 2
        assert '"Similarity"' in js and '"Random"' in js

    def test_checked_and_change_are_bound(self) -> None:
        js = _seg_js()
        assert js.count("checked:(") == 2
        assert js.count("onChange:") == 2
        assert "set_choice" in js


class TestLayoutStructure:
    def test_skip_link_is_first_child_of_shell_before_rail(self) -> None:
        js = rendered(layout(rx.box()))
        shell = js.index('className:"shell"')
        skip = js.index('className:"skip-link"')
        rail = js.index('className:"rail"')
        assert shell < skip < rail
        # Nothing renders between the shell's props and the skip link.
        assert js[shell:skip].count("jsx(") == 1
        assert '"Skip to main content"' in js
        assert 'to:"#main-content"' in js

    def test_exactly_one_main_landmark(self) -> None:
        js = rendered(layout(rx.box()))
        # One main per branch: the shell's, plus the login view's own (task 2.6).
        # The auth cond renders only one branch at a time.
        assert js.count('jsx("main",{className:"scroll"') == 1
        assert js.count('jsx("main",') == 2
        assert js.count('id:"main-content"') == 1
        assert re.search(r'jsx\("main",\{className:"scroll",id:"main-content",[^}]*tabIndex:-1', js)

    def test_rail_and_top_bar_are_outside_main(self) -> None:
        js = rendered(layout(rx.box()))
        main = js.index('jsx("main",')
        assert js.index('className:"rail"') < main
        assert js.index('className:"topbar"') < main

    def test_sr_only_h1_is_first_child_of_main(self) -> None:
        js = rendered(layout(rx.box()))
        assert js.count('id:"main-heading"') == 1
        main = js.index('jsx("main",')
        first_child = js.index("jsx(", main + 1)
        assert js.startswith('jsx("h1",{className:"sr-only",id:"main-heading"', first_child)
        assert re.search(r'jsx\("h1",\{className:"sr-only",id:"main-heading",[^}]*tabIndex:-1', js)
        # Text comes from the top bar's title map.
        h1 = js[first_child : js.index("jsx(", first_child + 1)]
        assert 'case JSON.stringify("/tagging"):  return ("AI Tagging")' in h1
        assert '"PlexMix"' in h1

    def test_heading_false_omits_h1_but_keeps_main(self) -> None:
        js = rendered(layout(rx.box(), heading=False))
        # No sr-only h1. (The shell's dialog focus scripts still *target*
        # '#main-heading', which the page itself then provides.)
        assert 'id:"main-heading"' not in js
        assert 'jsx("h1",' not in js
        assert js.count('jsx("main",{className:"scroll"') == 1
        assert js.count('className:"skip-link"') == 1

    def test_full_bleed_keeps_structure(self) -> None:
        js = rendered(layout(rx.box(), full_bleed=True))
        assert js.count('jsx("main",{className:"scroll"') == 1
        assert js.count('id:"main-heading"') == 1

    def test_auth_branches_preserved(self) -> None:
        js = rendered(layout(rx.box()))
        assert 'className:"boot-screen"' in js
        assert "auth_checked" in js
        assert "is_authenticated" in js
