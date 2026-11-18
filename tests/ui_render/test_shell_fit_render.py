"""Render smoke: rail tooltips and top-bar hooks of ``webui-zoom-and-narrow-fit``.

Covers tasks 2.3 and 3.1 (design D2, D4) against real Reflex. The rail labels are Radix
Themes tooltips: ``Tooltip`` renders ``Tooltip.Trigger asChild`` around its child, so the
child must stay the rail's own ``<button>`` (it keeps its aria-label, aria-current and
data-route, and the Changelog button still opens the changelog dialog). Run in its own
session: ``pytest tests/ui_render -q``.
"""

from __future__ import annotations

import re

import reflex as rx
from reflex.components.component import StatefulComponent
from reflex.components.radix.themes.components.tooltip import Tooltip

from plexmix.ui.components import navbar
from tests.ui_render.conftest import rendered

_TIP_PROPS = 'delayDuration:150,side:"right",sideOffset:10'
_THEME_CONTENT = (
    'content:((resolvedColorMode?.valueOf?.() === "light"?.valueOf?.()) '
    '? "Dark mode" : "Light mode")'
)
_APP = "app_state____app_state"


def _rail_js() -> str:
    return rendered(navbar._icon_rail())


def _balanced(js: str, start: int) -> str:
    """The ``{...}`` object literal opening at ``js[start]`` (braces in strings skipped)."""
    assert js[start] == "{", js[start : start + 20]
    depth = 0
    quote = ""
    index = start
    while index < len(js):
        char = js[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return js[start : index + 1]
        index += 1
    raise AssertionError("unbalanced props block")


def _tooltips(js: str) -> list[tuple[str, str]]:
    """``(tooltip props, child)`` for every tooltip in ``js``.

    The child is its opening ``jsx("<tag>",{props}`` when it is an element, else the
    first 80 characters after the tooltip's props.
    """
    found: list[tuple[str, str]] = []
    for m in re.finditer(r"jsx\(RadixThemesTooltip,", js):
        props = _balanced(js, m.end())
        after = m.end() + len(props) + 1  # skip the comma
        element = re.match(r'jsx\("[a-z]+",', js[after:])
        if element:
            child = element.group(0) + _balanced(js, after + element.end())
        else:
            child = js[after : after + 80]
        found.append((props[1:-1], child))
    return found


def _nav_items() -> list[tuple[str, str, str, str, bool]]:
    return [item for group in navbar._NAV_GROUPS for item in group]


class TestRailTooltips:
    def test_every_rail_control_is_a_tooltip_around_its_own_button(self) -> None:
        tips = _tooltips(_rail_js())
        # Seven navigation items, Changelog, Log out and the theme toggle.
        assert len(tips) == 10
        for props, child in tips:
            assert props.endswith(_TIP_PROPS), props
            # asChild: the button itself is the trigger, no wrapper element in between.
            assert child.startswith('jsx("button",{'), child[:80]

    def test_navigation_labels_carry_the_g_key_hint(self) -> None:
        tips = _tooltips(_rail_js())
        nav = [(props, child) for props, child in tips if '"data-route"' in child]
        assert len(nav) == 7
        for (props, child), (label, href, _icon, key, _badge) in zip(nav, _nav_items()):
            assert props.startswith(f'content:"{label} \\u00b7 g {key}"'), props
            assert f'"data-route":"{href}"' in child

    def test_buttons_keep_their_names_current_state_and_route(self) -> None:
        js = _rail_js()
        assert js.count('jsx("button",{"aria-current":') == 7
        assert js.count('? "page" : null)') == 7
        for label, *_ in _nav_items():
            if label != "Doctor":
                assert f'"aria-label":"{label}"' in js
        assert '? "Doctor (needs attention)" : "Doctor")' in js
        routes = re.findall(r'"data-route":"([^"]+)"', js)
        assert routes == [href for _l, href, *_r in _nav_items()]
        for label in ("Changelog", "Log out", "Toggle color theme"):
            assert f'"aria-label":"{label}"' in js

    def test_changelog_button_still_opens_the_changelog_dialog(self) -> None:
        tips = dict(_tooltips(_rail_js()))
        child = tips[f'content:"Changelog",{_TIP_PROPS}']
        assert child.startswith('jsx("button",{"aria-label":"Changelog",className:"rail-item",')
        assert f"{_APP}.open_changelog" in child
        dialog = rendered(navbar._changelog_dialog())
        assert re.search(r"open:\S*show_changelog_rx_state_", dialog)

    def test_log_out_and_theme_toggle_labels(self) -> None:
        tips = dict(_tooltips(_rail_js()))
        assert f"{_APP}.logout" in tips[f'content:"Log out",{_TIP_PROPS}']
        theme = tips[f"{_THEME_CONTENT},{_TIP_PROPS}"]
        assert theme.startswith('jsx("button",{"aria-label":"Toggle color theme",')
        assert "toggleColorMode" in theme

    def test_no_css_rail_tips_remain(self) -> None:
        js = rendered(navbar.layout(rx.box()))
        assert "rail-tip" not in js
        assert js.count("jsx(RadixThemesTooltip,") == 10

    def test_compiled_memo_components_keep_raw_button_triggers(self) -> None:
        """Reflex memoizes stateful subtrees; Tooltip's non-recursive memoization keeps its
        child a plain ``<button>`` (a memo component there would break asChild)."""
        compiled = StatefulComponent.compile_from(navbar.layout(rx.box("x")))
        memos: dict[str, StatefulComponent] = {}

        def walk(component: object) -> None:
            if isinstance(component, StatefulComponent):
                memos[component.tag or ""] = component
                walk(component.component)
                return
            for child in getattr(component, "children", None) or []:
                walk(child)

        walk(compiled)
        code = "\n".join(memo._render_stateful_code(export=False) for memo in memos.values())
        tips = _tooltips(code)
        assert len(tips) == 10
        for props, child in tips:
            assert child.startswith('jsx("button",{'), (props[:40], child[:80])
        assert sum(isinstance(memo.component, Tooltip) for memo in memos.values()) >= 1


class TestTopBarHooks:
    def test_page_name_has_its_full_name_as_title(self) -> None:
        js = rendered(navbar._top_bar())
        m = re.search(r'jsx\("span",\{className:"t",title:(.*?)\},(.*?)\),jsx\("span"', js)
        assert m is not None
        title, text = m.group(1), m.group(2)
        assert title == text  # the same rx.match as the visible name
        for _href, page in navbar._PAGE_TITLES:
            assert f'return ("{page}")' in title
        assert 'return ("AI Tagging")' in title

    def test_trigger_label_is_the_cmd_label_hook(self) -> None:
        js = rendered(navbar._top_bar())
        assert 'jsx("span",{className:"cmd-label"},"Search or jump to\\u2026")' in js
        # The trigger itself is unchanged: no aria-label, so the label stays its name.
        assert (
            'jsx(RadixThemesDialog.Trigger,{},jsx("button",{"aria-keyshortcuts":'
            '"Meta+K Control+K /",className:"cmd-trigger",type:"button"}'
        ) in js

    def test_breadcrumb_text_is_still_in_the_dom(self) -> None:
        js = rendered(navbar._top_bar())
        assert 'jsx("span",{className:"brand"},"PlexMix")' in js
        assert 'jsx("span",{className:"crumb-sep"},"/")' in js
        assert 'className:"s"' in js

    def test_rail_logo_link_has_its_layout_hook(self) -> None:
        js = rendered(navbar._rail_logo())
        assert 'className:"rail-logo-link"' in js
        assert 'title:"PlexMix"' in js
