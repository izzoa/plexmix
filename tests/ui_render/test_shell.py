"""Render smoke: shell rail semantics, cancel AlertDialog, changelog focus and login.

Covers tasks 2.2 and 2.4-2.6 of ``webui-keyboard-and-semantics``. Run in its own
session: ``pytest tests/ui_render -q``.
"""

from __future__ import annotations

import re

import reflex as rx

from plexmix.ui.components import navbar
from plexmix.ui.components.login import login_page
from tests.ui_render.conftest import rendered

_RESTORE = '"javascript_code"] : "window.pmFocus&&pmFocus.restore()"'


def _rail_js() -> str:
    return rendered(navbar._icon_rail())


def _rail_items(js: str) -> list[str]:
    """Each rail navigation button's opening ``jsx("button",{...}`` props block."""
    return re.findall(r'jsx\("button",\{"aria-current":.*?type:"button"\}', js)


class TestRailSemantics:
    def test_every_nav_item_exposes_aria_current_cond(self) -> None:
        js = _rail_js()
        items = _rail_items(js)
        assert len(items) == 7
        for label, href, *_ in (it for group in navbar._NAV_GROUPS for it in group):
            assert (
                f'=== "{href}"?.valueOf?.()) ? "page" : null)' in js
            ), f"{label} lacks the aria-current cond"

    def test_inactive_items_never_render_empty_aria_current(self) -> None:
        js = _rail_js()
        assert '"aria-current":""' not in js
        assert ': "")' not in "".join(re.findall(r'"aria-current":\([^,]*', js))
        # The false branch is null, which React omits, so no item announces "false" either.
        assert js.count('? "page" : null)') == 7
        assert '"page" : "false"' not in js

    def test_doctor_name_announces_the_badge(self) -> None:
        js = _rail_js()
        doctor = [i for i in _rail_items(js) if '"/doctor"' in i]
        assert len(doctor) == 1
        assert '? "Doctor (needs attention)" : "Doctor")' in doctor[0]
        # Same condition as the badge dot: badge flag AND not on the Doctor page.
        assert "show_doctor_badge_rx_state_ && !(" in doctor[0]
        assert "rail-badge" in js

    def test_other_items_keep_their_plain_label(self) -> None:
        js = _rail_js()
        for label in ("Dashboard", "Generator", "Library", "Tagging", "History", "Settings"):
            assert f'"aria-label":"{label}"' in js
        assert "needs attention" not in js.replace('"Doctor (needs attention)"', "")

    def test_bottom_buttons_unchanged(self) -> None:
        js = _rail_js()
        for label in ("Changelog", "Log out", "Toggle color theme"):
            assert f'"aria-label":"{label}"' in js


class TestCancelConfirmDialog:
    def test_is_an_alert_dialog_bound_to_pending_job(self) -> None:
        js = rendered(navbar._cancel_confirm_dialog())
        assert js.startswith("jsx(RadixThemesAlertDialog.Root,")
        assert "jsx(RadixThemesAlertDialog.Content," in js
        assert "open:!((" in js and "pending_cancel_job_rx_state_" in js
        assert "app_state.set_cancel_dialog_open" in js
        assert '["is_open"] : _ev_0' in js

    def test_title_and_description(self) -> None:
        js = rendered(navbar._cancel_confirm_dialog())
        assert "jsx(RadixThemesAlertDialog.Title,{css:" in js
        assert '"Cancel running task?"' in js
        desc = js[js.index("jsx(RadixThemesAlertDialog.Description,") :]
        assert '"Stop "' in desc
        assert '"all running tasks"' in desc
        assert '"? Work completed so far is kept."' in desc

    def test_keep_running_is_the_cancel_part(self) -> None:
        js = rendered(navbar._cancel_confirm_dialog())
        assert (
            'jsx(RadixThemesAlertDialog.Cancel,{},jsx("button",'
            '{className:"btn btn-3 btn-soft",type:"button"},"Keep running"))'
        ) in js

    def test_cancel_task_is_a_plain_danger_button(self) -> None:
        js = rendered(navbar._cancel_confirm_dialog())
        assert "btn-danger" in js
        assert "btn-primary" not in js
        assert "AlertDialog.Action" not in js  # confirm closes by state, not by Action
        m = re.search(
            r'jsx\("button",\{className:"btn btn-3 btn-danger",onClick:(.*?),type:"button"\},'
            r'"Cancel task"\)',
            js,
        )
        assert m is not None
        on_click = m.group(1)
        # The client-side confirm flag is queued before the server event.
        mark = on_click.index('"javascript_code"] : "window.__pmCancelConfirmed=true"')
        assert mark < on_click.index("app_state.confirm_cancel")
        # Not nested in the Cancel part either: it is the Box's direct child.
        cancel_part = js[js.index("jsx(RadixThemesAlertDialog.Cancel,") : m.start()]
        assert cancel_part.endswith('"Keep running")),')

    def test_focus_restore_and_no_div_overlay(self) -> None:
        js = rendered(navbar._cancel_confirm_dialog())
        content = js[js.index("jsx(RadixThemesAlertDialog.Content,") :]
        props = content[: content.index("jsx(RadixThemesAlertDialog.Title")]
        assert props.count("onCloseAutoFocus:") == 1
        # Dismissed: pmFocus.restore() (opener, or #main-heading if gone).
        # Confirmed: restore() then a forced move to #main-heading.
        assert (
            '"if(window.__pmCancelConfirmed){window.__pmCancelConfirmed=false;'
            "window.pmFocus&&pmFocus.restore();"
            "window.pmFocus&&pmFocus.when('#main-heading',{force:true})}"
            'else{window.pmFocus&&pmFocus.restore()}"'
        ) in props
        assert "modal-backdrop" not in js
        assert 'className:"modal"' not in js
        assert "stop_propagation" not in js  # no hand-built backdrop click handling


class TestChangelogDialog:
    def test_content_restores_focus_on_close(self) -> None:
        js = rendered(navbar._changelog_dialog())
        content = js[js.index("jsx(RadixThemesDialog.Content,") :]
        props = content[: content.index("jsx(RadixThemesDialog.Title")]
        assert "onCloseAutoFocus:" in props
        assert _RESTORE in props

    def test_server_html_body_kept(self) -> None:
        js = rendered(navbar._changelog_dialog())
        assert 'className:"rx-Html changelog-body",dangerouslySetInnerHTML' in js


class TestShellLayoutDialogs:
    def test_one_alert_dialog_and_auth_branches(self) -> None:
        js = rendered(navbar.layout(rx.box()))
        assert js.count("jsx(RadixThemesAlertDialog.Root,") == 1
        assert js.count("jsx(RadixThemesDialog.Root,") == 2  # changelog + ⌘K palette (top bar)
        assert "modal-backdrop" not in js
        assert 'className:"boot-screen"' in js  # auth unknown
        assert 'htmlFor:"login-password"' in js  # login branch
        assert 'className:"shell"' in js


class TestLoginView:
    def test_one_main_landmark_and_one_h1(self) -> None:
        js = rendered(login_page())
        assert js.startswith('jsx("main",')
        assert js.count('jsx("main",') == 1
        # rx.heading defaults to <h1>; no other heading in the view.
        assert js.count("jsx(RadixThemesHeading,") == 1
        assert '"PlexMix")' in js

    def test_prompt_is_the_field_label(self) -> None:
        js = rendered(login_page())
        assert re.search(
            r'jsx\("label",\{[^}]*\}\),htmlFor:"login-password"\},"Enter the password to continue"\)',
            js,
        )

    def test_input_is_named_invalid_and_described(self) -> None:
        js = rendered(login_page())
        field = re.search(r"jsx\(RadixThemesTextField\.Root,\{(.*?)\},\)", js)
        assert field is not None
        props = field.group(1)
        assert 'id:"login-password"' in props
        assert '"aria-describedby":"login-error"' in props
        assert re.search(
            r'"aria-invalid":\(!\(\(\S*login_error_rx_state_\S* === ""\?\.valueOf\?\.\(\)\)\) '
            r'\? "true" : "false"\)',
            props,
        )
        # Security's autofocus and the form field name are unchanged.
        assert "autoFocus:true" in props
        assert 'name:"password"' in props
        assert 'type:"password"' in props

    def test_error_region_is_persistent_alert(self) -> None:
        js = rendered(login_page())
        region = 'jsx(RadixThemesBox,{id:"login-error",ref:ref_login_error,role:"alert"},'
        assert js.count('id:"login-error"') == 1
        assert region in js
        after = js[js.index(region) + len(region) :]
        # The region wraps the cond (always mounted); the error text is inside it.
        assert after.startswith("jsx(Fragment,{},(!((")
        assert after.index("login_error_rx_state_))") < after.index("Sign In")
        # Keyed by the failure count: a repeated message is a new node, re-announced.
        text = re.search(r"jsx\(RadixThemesText,\{(.*?)\},", after)
        assert text is not None
        assert re.search(r"\bkey:[\w.]*login_failures_rx_state_", text.group(1)), text.group(1)

    def test_submit_still_wired(self) -> None:
        js = rendered(login_page())
        assert "onSubmit:handleSubmit_" in js
        assert 'type:"submit"},"Sign In")' in js
