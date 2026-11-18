"""Application shell — glass icon rail, top-bar breadcrumb, and ⌘K command palette.

A single shared shell renders on every route (the rail and top bar appear
consistently across all pages); navigation uses real route redirects so
URL deep-links, refresh, and per-page ``on_load`` are preserved.
"""

from typing import Any, Optional, Union, cast

import reflex as rx
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.command_palette_state import (
    ACTION_COMMANDS,
    NAV_COMMANDS,
    CommandPaletteState,
)
from plexmix.ui.components.login import login_page
from plexmix.ui.changelog import ONLINE_CHANGELOG_URL
from plexmix.ui.utils.a11y import focus_by_id_script


# ── Navigation structure (grouped, with vim g+key hints) ──────────────
# (label, route, icon, key, badge-capable)
_NAV_GROUPS: list[list[tuple[str, str, str, str, bool]]] = [
    [
        ("Dashboard", "/dashboard", "layout-dashboard", "d", False),
        ("Generator", "/generator", "sparkles", "g", False),
        ("Library", "/library", "library", "l", False),
    ],
    [
        ("Tagging", "/tagging", "tags", "t", False),
        ("History", "/history", "history", "h", False),
    ],
    [
        ("Doctor", "/doctor", "stethoscope", "x", True),
        ("Settings", "/settings", "settings", "s", False),
    ],
]

# Per-route breadcrumb metadata for the top bar.
_PAGE_TITLES: list[tuple[str, str]] = [
    ("/dashboard", "Dashboard"),
    ("/generator", "Generator"),
    ("/library", "Library"),
    ("/tagging", "AI Tagging"),
    ("/history", "History"),
    ("/doctor", "Doctor"),
    ("/settings", "Settings"),
]
_PAGE_SUBS: list[tuple[str, str]] = [
    ("/dashboard", "Your library at a glance"),
    ("/generator", "Describe a vibe, let AI curate the mix"),
    ("/library", "Tracks synced from Plex"),
    ("/tagging", "Mood, environment & instrument tags"),
    ("/history", "Saved & generated playlists"),
    ("/doctor", "Diagnostics & system health"),
    ("/settings", "Connections, providers & advanced"),
]


# ── Icon rail ─────────────────────────────────────────────────────────

# Rail labels are portalled Radix tooltips beside the rail (webui-zoom-and-narrow-fit D2):
# the rail scrolls on short viewports, and a scroller clips in both axes, so an in-rail
# CSS label would be cut off exactly when it is needed. Radix Tooltip's Trigger is
# ``asChild``, so the wrapped control itself stays the trigger element (its aria-label,
# aria-current and data-route are untouched); it opens on hover and keyboard focus,
# closes on Escape and flips away from the viewport edges.
_RAIL_TIP_PROPS: dict[str, Any] = {"side": "right", "side_offset": 10, "delay_duration": 150}


def _rail_logo() -> rx.Component:
    """Theme-aware rail logo → Generator."""
    return rx.link(
        rx.color_mode_cond(
            light=rx.image(src="/logo-light.svg", alt="PlexMix", class_name="rail-logo"),
            dark=rx.image(src="/logo-dark.svg", alt="PlexMix", class_name="rail-logo"),
        ),
        href="/generator",
        title="PlexMix",
        class_name="rail-logo-link",
    )


def _rail_item(label: str, href: str, icon: str, key: str, badge: bool) -> rx.Component:
    """One rail navigation item with active indicator + hover/focus tooltip.

    The active item exposes ``aria-current="page"``; inactive items render no
    ``aria-current`` at all (the cond's ``None`` compiles to ``null``, which React
    omits), so only one item ever announces a current state. A badge-capable item
    (Doctor) says in its accessible name when its attention badge is shown. The
    label and its ``g``-key hint show in a Radix tooltip (``_RAIL_TIP_PROPS``).
    """
    is_active = AppState.router.page.path == href  # type: ignore[attr-defined]
    children: list[rx.Component] = [rx.icon(icon, size=20)]
    accessible_name: rx.Var | str = label
    if badge:
        show_badge = AppState.show_doctor_badge & ~is_active
        children.append(
            rx.cond(
                show_badge,
                rx.box(class_name="rail-badge"),
                rx.fragment(),
            )
        )
        accessible_name = rx.cond(show_badge, f"{label} (needs attention)", label)
    # A real <button> so the rail is reachable and operable from the keyboard.
    # ``data-route`` is the ``g``+key target: plexmix.js clicks it, so the chord
    # navigates through this button's client-side redirect (no document reload).
    button = rx.el.button(
        *children,
        class_name=rx.cond(is_active, "rail-item active", "rail-item"),
        on_click=rx.redirect(href),
        type="button",
        aria_current=rx.cond(is_active, "page", None),
        custom_attrs={"aria-label": accessible_name, "data-route": href},
    )
    return rx.tooltip(button, content=f"{label} · g {key}", **_RAIL_TIP_PROPS)


def _icon_rail() -> rx.Component:
    items: list[rx.Component] = [_rail_logo(), rx.box(class_name="rail-sep")]
    for gi, group in enumerate(_NAV_GROUPS):
        if gi > 0:
            items.append(rx.box(class_name="rail-sep"))
        items.append(rx.box(*[_rail_item(*it) for it in group], class_name="rail-group"))
    items.append(rx.box(class_name="rail-spacer"))

    bottom: list[rx.Component] = [
        # Changelog trigger — the dialog is controlled by show_changelog and had no
        # trigger, so it could never be opened.
        rx.tooltip(
            rx.el.button(
                rx.icon("file-text", size=19),
                class_name="rail-item",
                on_click=AppState.open_changelog,
                type="button",
                custom_attrs={"aria-label": "Changelog"},
            ),
            content="Changelog",
            **_RAIL_TIP_PROPS,
        ),
        rx.cond(
            AppState.auth_required,
            rx.tooltip(
                rx.el.button(
                    rx.icon("log-out", size=19),
                    class_name="rail-item",
                    on_click=AppState.logout,
                    type="button",
                    custom_attrs={"aria-label": "Log out"},
                ),
                content="Log out",
                **_RAIL_TIP_PROPS,
            ),
            rx.fragment(),
        ),
        rx.tooltip(
            rx.el.button(
                rx.color_mode_cond(
                    light=rx.icon("moon", size=19),
                    dark=rx.icon("sun", size=19),
                ),
                class_name="rail-item",
                on_click=rx.toggle_color_mode,
                type="button",
                custom_attrs={"aria-label": "Toggle color theme"},
            ),
            # Names the theme the toggle switches to.
            content=rx.color_mode_cond(light="Dark mode", dark="Light mode"),
            **_RAIL_TIP_PROPS,
        ),
    ]
    items.append(rx.box(*bottom, class_name="rail-group"))
    return rx.el.nav(*items, class_name="rail")


# ── Top bar ───────────────────────────────────────────────────────────


def _top_bar() -> rx.Component:
    """Breadcrumb, ⌘K trigger, theme toggle and settings avatar.

    At narrow widths the CSS compacts the bar (redesign.css, zoom-fit shell block):
    the subtitle and the ``PlexMix /`` prefix are hidden visually but stay in the
    accessibility tree, and the page name truncates with an ellipsis; its ``title``
    carries the full name on hover.
    """
    path = AppState.router.page.path
    return rx.el.header(
        rx.box(
            rx.el.span("PlexMix", class_name="brand"),
            rx.el.span("/", class_name="crumb-sep"),
            rx.el.span(
                rx.match(path, *_PAGE_TITLES, "PlexMix"),
                class_name="t",
                title=rx.match(path, *_PAGE_TITLES, "PlexMix"),
            ),
            rx.el.span(rx.match(path, *_PAGE_SUBS, ""), class_name="s"),
            class_name="topbar-title",
        ),
        rx.box(class_name="topbar-spacer"),
        # The ⌘K trigger and its (portalled) dialog.
        _command_palette(),
        rx.el.button(
            rx.color_mode_cond(
                light=rx.icon("moon", size=18),
                dark=rx.icon("sun", size=18),
            ),
            class_name="icon-btn",
            on_click=rx.toggle_color_mode,
            title="Toggle theme",
            type="button",
            custom_attrs={"aria-label": "Toggle color theme"},
        ),
        rx.el.button(
            "iz",
            class_name="avatar",
            title="izzo · Plex",
            on_click=rx.redirect("/settings"),
            type="button",
            custom_attrs={"aria-label": "Open settings"},
        ),
        class_name="topbar",
    )


# ── Command palette ───────────────────────────────────────────────────
#
# An uncontrolled Radix dialog (design D6-D9 of webui-input-responsiveness):
# the browser opens it locally (no server round trip), and the controller in
# assets/plexmix.js (window.__pmPalette) filters rows on ``data-search``, keeps
# one highlighted row (``aria-selected`` plus the input's
# ``aria-activedescendant``), runs it on Enter and restores focus on close.
# Python renders none of ``hidden``/``aria-selected``/``aria-activedescendant``
# from state, so React never overwrites what the controller sets.
#
# DOM contract with plexmix.js: #cmdk-input (combobox), #cmdk-list (listbox),
# #cmdk-status (polite status), .cmdk-group (role=group), .cmdk-item rows
# (role=option, id "cmdk-opt-<id>", data-search), the Generate row's empty
# .cmdk-echo span, .cmd-trigger, and data-route on the rail and Jump to rows.

CMDK_INPUT_ID = "cmdk-input"
CMDK_LIST_ID = "cmdk-list"
CMDK_STATUS_ID = "cmdk-status"
CMDK_OPTION_PREFIX = "cmdk-opt-"
# The "Generate “<query>”" row (filled and shown by plexmix.js when the query is not empty).
CMDK_GENERATE_OPTION_ID = f"{CMDK_OPTION_PREFIX}generate-query"

# The dialog's onCloseAutoFocus: focus goes back to the element focused before ⌘K
# or "/" (when no command ran), else to the trigger. It is a client-side callback,
# not a queued rx.call_script: queued events wait behind in-flight server events
# (the palette's own on_open_change(False) among them), which would leave focus on
# the trigger for a round trip and send keys typed meanwhile to it. plexmix.js
# preventDefault()s Radix's own focus of the trigger when it placed focus.
_PALETTE_CLOSE_AUTO_FOCUS_JS = (
    "((_e) => (window.__pmPalette && window.__pmPalette.closeAutoFocus "
    "&& window.__pmPalette.closeAutoFocus(_e)))"
)
# Read synchronously inside the Generate row's click handler, while the input is
# still mounted, so the query is captured before the dialog closes.
_PALETTE_QUERY_JS = f"document.getElementById('{CMDK_INPUT_ID}')?.value ?? ''"

_Text = Union[str, rx.Var, rx.Component]

# mypy types Reflex state vars and @rx.event handlers as their Python values
# (a list, an EventNamespace); through Any they are the Vars/EventSpecs they are.
_PALETTE: Any = cast(Any, CommandPaletteState)


def _search_text(*parts: str) -> str:
    """Lower-cased ``data-search`` text the browser matches every typed term against."""
    return " ".join(p for p in parts if p).lower()


def _cmd_option(
    option_id: Union[str, rx.Var],
    icon: rx.Component,
    title: _Text,
    sub: _Text,
    on_click: Any,
    *extra: rx.Component,
    search: Optional[Union[str, rx.Var]] = None,
    attrs: Optional[dict[str, Any]] = None,
    key: Optional[Any] = None,
    hidden: bool = False,
) -> rx.Component:
    """One palette row: a ``role=option`` div inside ``rx.dialog.close``.

    The handler sits on the Close part (Radix Close is ``asChild``), so the row
    stays the option element and running it also closes the palette. Rows are
    not tabbable: the combobox reaches them through ``aria-activedescendant``.
    The id goes through ``custom_attrs`` so no React ref is created per row.
    """
    row_attrs: dict[str, Any] = {"id": option_id}
    if search is not None:
        row_attrs["data-search"] = search
    row_attrs.update(attrs or {})
    row_props: dict[str, Any] = {"hidden": True} if hidden else {}
    row = rx.el.div(
        rx.el.div(icon, class_name="ci-ico", custom_attrs={"aria-hidden": "true"}),
        rx.el.div(
            rx.el.div(title, class_name="ci-title"),
            rx.el.div(sub, class_name="ci-sub"),
            class_name="ci-text",
        ),
        *extra,
        role="option",
        class_name="cmdk-item",
        custom_attrs=row_attrs,
        **row_props,
    )
    close_props: dict[str, Any] = {} if key is None else {"key": key}
    return rx.dialog.close(row, on_click=on_click, **close_props)


def _cmd_group(
    group_id: str, label: str, *rows: rx.Component, hidden: bool = False
) -> rx.Component:
    """A ``role=group`` block named by its visible heading."""
    label_id = f"cmdk-group-{group_id}-label"
    group_props: dict[str, Any] = {"hidden": True} if hidden else {}
    return rx.el.div(
        rx.el.div(
            label,
            role="presentation",
            class_name="cmdk-group-label",
            custom_attrs={"id": label_id},
        ),
        *rows,
        role="group",
        class_name="cmdk-group",
        custom_attrs={"aria-labelledby": label_id, "data-group": group_id},
        **group_props,
    )


def _action_option(cmd: dict[str, str]) -> rx.Component:
    """An Actions row. Route actions and the theme toggle run in the browser."""
    option_id = f"{CMDK_OPTION_PREFIX}action-{cmd['id']}"
    search = _search_text(cmd["title"], cmd["sub"], cmd["keywords"])
    if cmd["id"] == "theme":
        # Client-only toggle (a backend handler cannot return it); the title
        # names the theme it switches to.
        return _cmd_option(
            option_id,
            rx.color_mode_cond(light=rx.icon("moon", size=16), dark=rx.icon("sun", size=16)),
            rx.color_mode_cond("Switch to dark mode", "Switch to light mode"),
            cmd["sub"],
            rx.toggle_color_mode,
            search=_search_text("switch to dark mode light mode", cmd["sub"], cmd["keywords"]),
        )
    if cmd["id"] == "cancel":
        # Listed only while something is running; re-checked when it runs.
        return rx.cond(
            CommandPaletteState.has_running_job,
            _cmd_option(
                option_id,
                rx.icon(cmd["icon"], size=16),
                cmd["title"],
                cmd["sub"],
                CommandPaletteState.request_global_cancel,
                search=search,
            ),
            rx.fragment(),
        )
    return _cmd_option(
        option_id,
        rx.icon(cmd["icon"], size=16),
        cmd["title"],
        cmd["sub"],
        rx.redirect(cmd["href"]),
        search=search,
    )


def _nav_option(cmd: dict[str, str]) -> rx.Component:
    """A Jump to row with its ``g``+key hint."""
    return _cmd_option(
        f"{CMDK_OPTION_PREFIX}nav-{cmd['id']}",
        rx.icon(cmd["icon"], size=16),
        cmd["title"],
        cmd["sub"],
        rx.redirect(cmd["href"]),
        rx.el.kbd(f"g {cmd['gkey']}", class_name="cmdk-kbd"),
        search=_search_text(cmd["title"], cmd["sub"], cmd["keywords"]),
        attrs={"data-route": cmd["href"]},
    )


def _recent_option(item: Any, index: Any) -> rx.Component:
    """A Recent prompts row: prefills the Generator, never generates by itself."""
    return _cmd_option(
        f"{CMDK_OPTION_PREFIX}recent-{index}",
        rx.icon("history", size=16),
        item["prompt"],
        item["sub"],
        _PALETTE.use_recent_prompt(item["prompt"]),
        search=item["search"],
        key=item["prompt"],
    )


def _generate_option() -> rx.Component:
    """The final "Generate “<query>”" row.

    Rendered hidden with an empty ``.cmdk-echo``: plexmix.js shows it and writes
    the query into the echo whenever the query is not empty. It has no
    ``data-search`` because it matches every non-empty query.
    """
    query: rx.Var[str] = rx.Var(_PALETTE_QUERY_JS, _var_type=str)
    return _cmd_option(
        CMDK_GENERATE_OPTION_ID,
        rx.icon("wand-sparkles", size=16),
        rx.fragment("Generate “", rx.el.span(class_name="cmdk-echo"), "”"),
        "Create a playlist from this prompt",
        _PALETTE.generate_from_query(query),
        hidden=True,
    )


def _command_palette() -> rx.Component:
    """The ⌘K trigger and the command palette dialog (placed in the top bar)."""
    trigger = rx.dialog.trigger(
        rx.el.button(
            rx.icon("search", size=15),
            # The trigger's accessible name: visually hidden (never display: none)
            # when the trigger collapses to an icon at narrow widths.
            rx.el.span("Search or jump to…", class_name="cmd-label"),
            # One label shows, per html[data-platform] (set pre-paint by plexmix.js).
            rx.el.span(
                rx.el.span("⌘K", class_name="kbd-mac"),
                rx.el.span("Ctrl K", class_name="kbd-other"),
                class_name="kbd",
            ),
            class_name="cmd-trigger",
            type="button",
            custom_attrs={"aria-keyshortcuts": "Meta+K Control+K /"},
        )
    )
    input_row = rx.el.div(
        rx.icon("search", size=18, color="var(--fg-3)"),
        rx.el.input(
            id=CMDK_INPUT_ID,
            type="text",
            role="combobox",
            placeholder="Search pages, actions, prompts…",
            class_name="cmdk-input",
            auto_complete="off",
            spell_check=False,
            custom_attrs={
                "aria-label": "Search commands",
                "aria-expanded": "true",
                "aria-controls": CMDK_LIST_ID,
                "aria-autocomplete": "list",
            },
        ),
        rx.el.span("esc", class_name="kbd", custom_attrs={"aria-hidden": "true"}),
        class_name="cmdk-input-row",
    )
    results = rx.el.div(
        _cmd_group("actions", "Actions", *[_action_option(c) for c in ACTION_COMMANDS]),
        _cmd_group("jump", "Jump to", *[_nav_option(c) for c in NAV_COMMANDS]),
        rx.cond(
            _PALETTE.recent_prompts.length() > 0,
            _cmd_group(
                "recent",
                "Recent prompts",
                rx.foreach(_PALETTE.recent_prompts, _recent_option),
            ),
            rx.fragment(),
        ),
        _cmd_group("generate", "Generate", _generate_option(), hidden=True),
        id=CMDK_LIST_ID,
        role="listbox",
        class_name="cmdk-list",
        custom_attrs={"aria-label": "Commands"},
    )
    footer = rx.dialog.description(
        rx.el.span(rx.el.kbd("↑↓", class_name="cmdk-kbd"), "move"),
        rx.el.span(rx.el.kbd("↵", class_name="cmdk-kbd"), "run"),
        rx.el.span(rx.el.kbd("esc", class_name="cmdk-kbd"), "close"),
        class_name="cmdk-foot",
    )
    return rx.dialog.root(
        trigger,
        rx.dialog.content(
            rx.dialog.title("Command palette", class_name="sr-only"),
            input_row,
            results,
            rx.el.div(
                id=CMDK_STATUS_ID,
                role="status",
                class_name="sr-only",
                custom_attrs={"aria-live": "polite", "aria-atomic": "true"},
            ),
            footer,
            class_name="cmdk",
            custom_attrs={"aria-modal": "true"},
            on_close_auto_focus=rx.Var(_PALETTE_CLOSE_AUTO_FOCUS_JS, _var_type=rx.EventChain),
        ),
        # Refreshes recent prompts and the running flag; never gates opening.
        on_open_change=CommandPaletteState.on_open_change,
    )


# ── Changelog dialog (preserved) ──────────────────────────────────────

# Run by every dialog's on_close_auto_focus: return focus to the control that
# opened the dialog, or to #main-heading when it no longer exists (pmFocus in
# assets/plexmix.js). Radix's own handler focuses a null trigger first.
_RESTORE_FOCUS_JS = "window.pmFocus&&pmFocus.restore()"

# Cancel confirm: "Cancel task" sets this client flag before confirm_cancel is
# sent. The opener (a job bar's Cancel button) disappears once the job stops,
# which is usually after the dialog has closed, so a confirmed close sends focus
# to #main-heading instead of the soon-to-vanish opener. A dismiss (Escape,
# "Keep running") still returns focus to the opener.
_CANCEL_CONFIRMED_FLAG = "window.__pmCancelConfirmed"
_MARK_CANCEL_CONFIRMED_JS = f"{_CANCEL_CONFIRMED_FLAG}=true"
_CANCEL_CLOSE_FOCUS_JS = (
    f"if({_CANCEL_CONFIRMED_FLAG}){{{_CANCEL_CONFIRMED_FLAG}=false;"
    f"{_RESTORE_FOCUS_JS};{focus_by_id_script('main-heading')}}}"
    f"else{{{_RESTORE_FOCUS_JS}}}"
)


def _changelog_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(
                rx.hstack(
                    rx.icon("file-text", size=18),
                    rx.text(
                        rx.cond(
                            AppState.changelog_version != "",
                            f"Changelog · v{AppState.changelog_version}",
                            "Changelog",
                        )
                    ),
                    spacing="2",
                    align="center",
                ),
            ),
            rx.dialog.description(
                rx.text("Release history for PlexMix", size="2", color="var(--fg-3)"),
            ),
            rx.scroll_area(
                # Server-rendered, HTML-escaped release notes (plexmix.ui.changelog):
                # no client markdown/KaTeX/highlighter bundle ships with any route.
                rx.cond(
                    AppState.changelog_available,
                    rx.html(AppState.changelog_html, class_name="changelog-body"),
                    rx.cond(
                        AppState.changelog_version != "",
                        rx.vstack(
                            rx.text(
                                "Release notes are not bundled with this install.",
                                size="2",
                                color="var(--fg-2)",
                            ),
                            rx.link(
                                "Read the changelog online",
                                href=ONLINE_CHANGELOG_URL,
                                is_external=True,
                                size="2",
                            ),
                            spacing="2",
                        ),
                        rx.text("Loading release notes…", size="2", color="var(--fg-3)"),
                    ),
                ),
                max_height="60vh",
                padding_right="12px",
            ),
            rx.dialog.close(
                rx.button("Close", variant="soft", size="2", margin_top="16px"),
            ),
            max_width="640px",
            aria_modal="true",
            # Back to the opener (the rail's Changelog button), or #main-heading.
            on_close_auto_focus=rx.call_script(_RESTORE_FOCUS_JS),
        ),
        open=AppState.show_changelog,
        on_open_change=lambda is_open: rx.cond(
            is_open, AppState.open_changelog, AppState.close_changelog
        ),
    )


# ── Layout shell ──────────────────────────────────────────────────────


def _cancel_confirm_dialog() -> rx.Component:
    """Shared, shell-level confirm for cancelling a running job (or all).

    A Radix AlertDialog (role=alertdialog, focus trapped): "Keep running" is the
    AlertDialog Cancel part, so it takes focus on open; Escape dismisses through
    ``on_open_change`` and outside clicks are ignored. "Cancel task" is a plain
    button, deliberately not ``alert_dialog.action``: an Action would also queue
    ``on_open_change(False)``, which clears ``pending_cancel_job`` and could run
    before ``confirm_cancel`` reads it. ``confirm_cancel`` closes the dialog by
    clearing the pending job itself.
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title(
                "Cancel running task?",
                style={"fontSize": "18px", "fontWeight": "700", "color": "var(--fg-1)"},
            ),
            rx.alert_dialog.description(
                "Stop ",
                rx.cond(
                    AppState.pending_cancel_job == "__all__",
                    "all running tasks",
                    AppState.pending_cancel_job,
                ),
                "? Work completed so far is kept.",
                style={"fontSize": "14px", "color": "var(--fg-2)"},
            ),
            rx.box(
                rx.alert_dialog.cancel(
                    rx.el.button(
                        "Keep running",
                        class_name="btn btn-3 btn-soft",
                        type="button",
                    ),
                ),
                rx.el.button(
                    "Cancel task",
                    class_name="btn btn-3 btn-danger",
                    # Client flag first (runs locally, in order), then the server event.
                    on_click=[
                        rx.call_script(_MARK_CANCEL_CONFIRMED_JS),
                        AppState.confirm_cancel,
                    ],
                    type="button",
                ),
                style={
                    "display": "flex",
                    "flexWrap": "wrap",
                    "justifyContent": "flex-end",
                    "gap": "10px",
                    "marginTop": "20px",
                },
            ),
            max_width="440px",
            aria_modal="true",
            # Dismissed: back to the opener (or #main-heading if it is gone).
            # Confirmed: #main-heading, since the opener is about to disappear.
            on_close_auto_focus=rx.call_script(_CANCEL_CLOSE_FOCUS_JS),
        ),
        open=AppState.pending_cancel_job != "",
        on_open_change=AppState.set_cancel_dialog_open,
    )


def _loadbar() -> rx.Component:
    return rx.box(rx.box(class_name="ind"), class_name="loadbar")


# Hide text visually while keeping it in the accessibility tree.
_VISUALLY_HIDDEN: dict[str, str] = {
    "position": "absolute",
    "width": "1px",
    "height": "1px",
    "padding": "0",
    "margin": "-1px",
    "overflow": "hidden",
    "clip": "rect(0, 0, 0, 0)",
    "whiteSpace": "nowrap",
    "border": "0",
}


def _boot_screen() -> rx.Component:
    """Neutral placeholder shown until the server confirms whether a password is required.

    The app background plus the shared top loading bar. It renders neither the
    login card nor page content and has no focusable elements, so a cold load on
    a no-password install never flashes (or focuses) the sign-in form.
    """
    return rx.box(
        _loadbar(),
        rx.el.span("Loading PlexMix", style=_VISUALLY_HIDDEN),
        class_name="boot-screen",
        custom_attrs={"aria-busy": "true"},
        style={"position": "relative", "minHeight": "100vh", "background": "var(--surface-app)"},
    )


def _page_heading() -> rx.Component:
    """Visually hidden level-1 page heading, text taken from the top bar's title map.

    ``#main-heading`` is the focus target for route changes and for dialogs whose
    opener was removed (``pmFocus`` in assets/plexmix.js).
    """
    return rx.el.h1(
        # Same path expression as the top bar, so the h1 always matches the breadcrumb.
        rx.match(AppState.router.page.path, *_PAGE_TITLES, "PlexMix"),  # type: ignore[attr-defined]
        id="main-heading",
        tab_index=-1,
        class_name="sr-only",
    )


def layout(content: rx.Component, full_bleed: bool = False, heading: bool = True) -> rx.Component:
    """Wrap page content in the shared shell (auth-gated).

    Three states: auth unknown (boot screen), authenticated (shell) and sign-in
    required (login card). ``full_bleed`` skips the centered ``.page`` wrapper
    for pages that manage their own full-height stage (e.g. the Generator showpiece).

    The shell starts with a "Skip to main content" link and puts the page inside
    the single ``<main id="main-content">`` landmark (the rail and top bar stay
    outside it). With ``heading=True`` (the default) ``main`` starts with a visually
    hidden ``<h1 id="main-heading">`` naming the page. Pages that render their own
    visible h1 (Generator, Settings) pass ``heading=False`` and give that h1
    ``id="main-heading"`` and ``tab_index=-1``.
    """
    inner = content if full_bleed else rx.box(content, class_name="page")
    main_children: list[rx.Component] = [_page_heading()] if heading else []
    main_children.extend([rx.cond(AppState.is_page_loading, _loadbar(), rx.fragment()), inner])
    return rx.cond(
        ~AppState.auth_checked,
        _boot_screen(),
        rx.cond(
            AppState.is_authenticated,
            rx.box(
                # First Tab stop. reload_document keeps it a native in-page anchor
                # (rx.el.a is a React Router Link); pmFocus enhances the click.
                rx.el.a(
                    "Skip to main content",
                    href="#main-content",
                    class_name="skip-link",
                    reload_document=True,
                ),
                _icon_rail(),
                rx.box(
                    _top_bar(),
                    rx.el.main(
                        *main_children,
                        id="main-content",
                        tab_index=-1,
                        class_name="scroll",
                    ),
                    class_name="main",
                ),
                _cancel_confirm_dialog(),
                rx.toast.provider(),
                _changelog_dialog(),
                class_name="shell",
            ),
            login_page(),
        ),
    )
