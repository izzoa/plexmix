"""Render smoke: the ⌘K command palette dialog, its trigger and the rail's data-route.

Covers tasks 3.1 and 3.2 of ``webui-input-responsiveness`` (design D6-D9, D14)
against real Reflex. Run in its own session: ``pytest tests/ui_render -q``.
"""

from __future__ import annotations

import re

import pytest
import reflex as rx

from plexmix.ui.components import navbar
from plexmix.ui.states.command_palette_state import ACTION_COMMANDS, NAV_COMMANDS
from tests.ui_render.conftest import rendered

_STATE = "command_palette_state____command_palette_state"


@pytest.fixture(scope="module")
def js() -> str:
    return rendered(navbar._command_palette())


def _option_ids(js: str) -> list[str]:
    return re.findall(r'id:"(cmdk-opt-[^"]+)",role:"option"', js)


def _option(js: str, option_id: str) -> str:
    """The ``jsx("div",{...})`` props of one static option row."""
    m = re.search(r'jsx\("div",\{([^{}]*id:"' + re.escape(option_id) + r'"[^{}]*)\}', js)
    assert m is not None, option_id
    return m.group(1)


def _close_wrapping(js: str, option_id: str) -> str:
    """The ``RadixThemesDialog.Close`` props that wrap ``option_id``."""
    end = js.index(f'id:"{option_id}"')
    start = js.rindex("jsx(RadixThemesDialog.Close,{", 0, end)
    return js[start:end]


class TestDialogStructure:
    def test_uncontrolled_root_refreshes_on_open_change(self, js: str) -> None:
        assert js.startswith("jsx(RadixThemesDialog.Root,{onOpenChange:")
        root_props = js[: js.index("jsx(RadixThemesDialog.Trigger")]
        assert f"{_STATE}.on_open_change" in root_props
        assert '["is_open"] : _ev_0' in root_props
        # No controlled open prop: opening never waits for the server.
        assert "open:" not in root_props

    def test_trigger_is_the_cmd_trigger_button_without_a_server_event(self, js: str) -> None:
        trigger = js[
            js.index("jsx(RadixThemesDialog.Trigger") : js.index("RadixThemesDialog.Content")
        ]
        assert trigger.startswith(
            'jsx(RadixThemesDialog.Trigger,{},jsx("button",{"aria-keyshortcuts":"Meta+K Control+K /",'
            'className:"cmd-trigger",type:"button"}'
        )
        assert "onClick" not in trigger
        assert "open_palette" not in js

    def test_trigger_shows_one_platform_label(self, js: str) -> None:
        assert (
            'jsx("span",{className:"kbd"},jsx("span",{className:"kbd-mac"},"\\u2318K"),'
            'jsx("span",{className:"kbd-other"},"Ctrl K"))'
        ) in js

    def test_content_is_a_named_modal_that_restores_focus(self, js: str) -> None:
        content = js[js.index("jsx(RadixThemesDialog.Content,") :]
        props = content[: content.index("jsx(RadixThemesDialog.Title")]
        assert '"aria-modal":"true"' in props
        assert 'className:"cmdk"' in props
        # Focus is restored by a client-side callback run inside Radix's
        # onCloseAutoFocus, never by a queued event (which waits behind server events).
        assert (
            "onCloseAutoFocus:((_e) => (window.__pmPalette && window.__pmPalette.closeAutoFocus"
            " && window.__pmPalette.closeAutoFocus(_e)))"
        ) in props
        for queued in ("addEvents", "ReflexEvent", "_call_script", "javascript_code"):
            assert queued not in props
        assert 'jsx(RadixThemesDialog.Title,{className:"sr-only"},"Command palette")' in js

    def test_footer_is_the_description(self, js: str) -> None:
        foot = js[js.index("jsx(RadixThemesDialog.Description,") :]
        assert foot.startswith('jsx(RadixThemesDialog.Description,{className:"cmdk-foot"}')
        for word in ('"move"', '"run"', '"close"'):
            assert word in foot

    def test_no_hand_built_overlay_or_old_state(self, js: str) -> None:
        for gone in ("cmdk-backdrop", "cmdk-close", "stop_propagation", "set_query", "run_vibe"):
            assert gone not in js
        assert "is_open_rx_state_" not in js
        assert "query_rx_state_" not in js


class TestComboboxAndListbox:
    def test_input_is_an_uncontrolled_named_combobox(self, js: str) -> None:
        m = re.search(r'jsx\("input",\{([^}]*)\},\)', js)
        assert m is not None
        props = m.group(1)
        for attr in (
            'id:"cmdk-input"',
            'role:"combobox"',
            '"aria-label":"Search commands"',
            '"aria-controls":"cmdk-list"',
            '"aria-expanded":"true"',
            '"aria-autocomplete":"list"',
            'autoComplete:"off"',
            'className:"cmdk-input"',
        ):
            assert attr in props, attr
        assert "value:" not in props
        assert "onChange" not in props
        assert "aria-activedescendant" not in js  # set only by plexmix.js

    def test_list_is_a_named_listbox(self, js: str) -> None:
        assert re.search(
            r'jsx\("div",\{"aria-label":"Commands",className:"cmdk-list",id:"cmdk-list",'
            r'ref:ref_cmdk_list,role:"listbox"\}',
            js,
        )

    def test_groups_are_labelled_by_their_headings_in_order(self, js: str) -> None:
        groups = re.findall(
            r'jsx\("div",\{"aria-labelledby":"([^"]+)",className:"cmdk-group",'
            r'"data-group":"([^"]+)",(?:hidden:true,)?role:"group"\},'
            r'jsx\("div",\{className:"cmdk-group-label",id:"([^"]+)",role:"presentation"\},'
            r'"([^"]+)"\)',
            js,
        )
        assert [(g[1], g[3]) for g in groups] == [
            ("actions", "Actions"),
            ("jump", "Jump to"),
            ("recent", "Recent prompts"),
            ("generate", "Generate"),
        ]
        for labelledby, _gid, label_id, _label in groups:
            assert labelledby == label_id

    def test_status_region_is_polite_and_visually_hidden(self, js: str) -> None:
        assert (
            'jsx("div",{"aria-atomic":"true","aria-live":"polite",className:"sr-only",'
            'id:"cmdk-status",ref:ref_cmdk_status,role:"status"},)'
        ) in js

    def test_no_state_rendered_highlight_or_visibility(self, js: str) -> None:
        assert "aria-selected" not in js
        # The only static hidden flags are the Generate group and row (empty query on open).
        assert js.count("hidden:true") == 2


class TestRows:
    def test_static_option_ids_are_unique_and_prefixed(self, js: str) -> None:
        ids = _option_ids(js)
        assert len(ids) == len(set(ids))
        expected = [f"cmdk-opt-action-{c['id']}" for c in ACTION_COMMANDS] + [
            f"cmdk-opt-nav-{c['id']}" for c in NAV_COMMANDS
        ]
        for option_id in expected:
            assert option_id in ids
        assert "cmdk-opt-generate-query" in js

    def test_every_option_is_wrapped_in_a_close_with_its_handler(self, js: str) -> None:
        for option_id in _option_ids(js):
            close = _close_wrapping(js, option_id)
            assert close.startswith("jsx(RadixThemesDialog.Close,{onClick:"), option_id
            # The option itself carries no handler: the Close part runs it and closes.
            assert "onClick" not in _option(js, option_id)

    def test_static_rows_are_searchable_lower_case(self, js: str) -> None:
        for cmd in NAV_COMMANDS + ACTION_COMMANDS:
            prefix = "nav" if cmd in NAV_COMMANDS else "action"
            props = _option(js, f"cmdk-opt-{prefix}-{cmd['id']}")
            m = re.search(r'"data-search":"([^"]+)"', props)
            assert m is not None, cmd["id"]
            assert m.group(1) == m.group(1).lower()
            for word in cmd["keywords"].split():
                assert word in m.group(1)

    def test_only_history_rows_match_hist(self, js: str) -> None:
        searches = re.findall(r'"data-search":"([^"]+)",id:"(cmdk-opt-[^"]+)"', js)
        assert [i for s, i in searches if "hist" in s] == ["cmdk-opt-nav-history"]

    def test_route_actions_redirect_on_the_client(self, js: str) -> None:
        for cmd in ACTION_COMMANDS:
            if not cmd["href"]:
                continue
            close = _close_wrapping(js, f"cmdk-opt-action-{cmd['id']}")
            assert f'ReflexEvent("_redirect", ({{ ["path"] : "{cmd["href"]}"' in close

    def test_theme_row_toggles_on_the_client_and_names_the_next_theme(self, js: str) -> None:
        close = _close_wrapping(js, "cmdk-opt-action-theme")
        # Client-only toggle: no server event, so no backend error.
        assert close.startswith('jsx(RadixThemesDialog.Close,{onClick:toggleColorMode},jsx("div",{')
        assert "ReflexEvent" not in close
        row = js[js.index('id:"cmdk-opt-action-theme"') :]
        row = row[: row.index("jsx(RadixThemesDialog.Close", 1)]
        assert '? "Switch to dark mode" : "Switch to light mode")' in row
        assert "LucideMoon" in row and "LucideSun" in row

    def test_cancel_row_only_while_running(self, js: str) -> None:
        idx = js.index('id:"cmdk-opt-action-cancel"')
        before = js[:idx]
        cond = before.rindex("has_running_job_rx_state_?(")
        assert before.rindex("jsx(RadixThemesDialog.Close,{onClick:") > cond
        assert f"{_STATE}.request_global_cancel" in _close_wrapping(js, "cmdk-opt-action-cancel")

    def test_jump_rows_show_g_key_hint_and_route(self, js: str) -> None:
        rail_keys = {href: key for g in navbar._NAV_GROUPS for (_l, href, _i, key, _b) in g}
        for cmd in NAV_COMMANDS:
            option_id = f"cmdk-opt-nav-{cmd['id']}"
            assert f'"data-route":"{cmd["href"]}"' in _option(js, option_id)
            assert rail_keys[cmd["href"]] == cmd["gkey"]
            row = js[js.index(f'id:"{option_id}"') :]
            row = row[: row.index("jsx(RadixThemesDialog.Close", 1)]
            assert f'jsx("kbd",{{className:"cmdk-kbd"}},"g {cmd["gkey"]}")' in row
            assert f'["path"] : "{cmd["href"]}"' in _close_wrapping(js, option_id)

    def test_recent_rows_from_state_with_static_icon(self, js: str) -> None:
        recent = js[js.index('"data-group":"recent"') :]
        recent = recent[: recent.index('"data-group":"generate"')]
        assert "recent_prompts_rx_state_.length > 0" in js
        assert "Array.prototype.map.call(" in recent
        assert re.search(
            r'jsx\(RadixThemesDialog\.Close,\{key:item_rx_state_\?\.\["prompt"\],onClick:'
            r'.*?use_recent_prompt", \(\{ \["prompt"\] : item_rx_state_\?\.\["prompt"\]',
            recent,
        )
        assert 'id:("cmdk-opt-recent-"+index_rx_state_),role:"option"' in recent
        assert '"data-search":item_rx_state_?.["search"]' in recent
        assert "jsx(LucideHistory," in recent
        assert "ref_cmdk_opt_recent" not in js  # no per-row React refs

    def test_generate_row_reads_the_query_synchronously(self, js: str) -> None:
        close = _close_wrapping(js, "cmdk-opt-generate-query")
        assert f"{_STATE}.generate_from_query" in close
        assert "[\"query\"] : document.getElementById('cmdk-input')?.value ?? ''" in close
        props = _option(js, "cmdk-opt-generate-query")
        assert "hidden:true" in props
        assert "data-search" not in props
        assert 'jsx("span",{className:"cmdk-echo"},)' in js

    def test_icons_are_static(self, js: str) -> None:
        assert "DynamicIcon" not in js


class TestShellIntegration:
    def test_rail_items_carry_data_route(self) -> None:
        rail = rendered(navbar._icon_rail())
        routes = re.findall(r'"data-route":"([^"]+)"', rail)
        assert routes == [href for g in navbar._NAV_GROUPS for (_l, href, *_r) in g]
        assert len(routes) == 7
        # Element type unchanged: still the rail's real buttons.
        assert rail.count('jsx("button",{"aria-current":') == 7

    def test_palette_lives_in_the_top_bar_not_the_layout_tail(self) -> None:
        bar = rendered(navbar._top_bar())
        assert bar.count("jsx(RadixThemesDialog.Root,") == 1
        assert bar.index('className:"topbar-spacer"') < bar.index("cmd-trigger")
        assert bar.index("cmd-trigger") < bar.index('"aria-label":"Toggle color theme"')

    def test_layout_renders_one_palette_and_no_dynamic_icon(self) -> None:
        js = rendered(navbar.layout(rx.box()))
        assert js.count('id:"cmdk-input"') == 1
        assert js.count("jsx(RadixThemesDialog.Root,") == 2  # palette + changelog
        assert "DynamicIcon" not in js
        main = js.index('jsx("main",')
        assert js.index("cmd-trigger") < main
        # Rail (7) + palette Jump to rows (7).
        assert js.count('"data-route"') == 14
        assert '"aria-keyshortcuts":"Meta+K Control+K /"' in js
        # Change-2 shell elements are untouched.
        assert js.count('className:"skip-link"') == 1
        assert js.count('id:"main-content"') == 1
