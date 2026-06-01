"""Application shell — glass icon rail, top-bar breadcrumb, and ⌘K command palette.

A single shared shell renders on every route (the rail and top bar appear
consistently across all pages); navigation uses real route redirects so
URL deep-links, refresh, and per-page ``on_load`` are preserved.
"""

import reflex as rx
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.command_palette_state import CommandPaletteState
from plexmix.ui.components.login import login_page


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


def _rail_logo() -> rx.Component:
    """Theme-aware rail logo → Generator."""
    return rx.link(
        rx.color_mode_cond(
            light=rx.image(src="/logo-light.svg", alt="PlexMix", class_name="rail-logo"),
            dark=rx.image(src="/logo-dark.svg", alt="PlexMix", class_name="rail-logo"),
        ),
        href="/generator",
        title="PlexMix",
    )


def _rail_item(label: str, href: str, icon: str, key: str, badge: bool) -> rx.Component:
    """One rail navigation item with active indicator + hover tooltip."""
    is_active = AppState.router.page.path == href
    children: list[rx.Component] = [rx.icon(icon, size=20)]
    if badge:
        children.append(
            rx.cond(
                AppState.show_doctor_badge & ~is_active,
                rx.box(class_name="rail-badge"),
                rx.fragment(),
            )
        )
    children.append(
        rx.el.span(
            label,
            rx.el.span(f"g {key}", class_name="k"),
            class_name="rail-tip",
        )
    )
    return rx.box(
        *children,
        class_name=rx.cond(is_active, "rail-item active", "rail-item"),
        on_click=rx.redirect(href),
    )


def _icon_rail() -> rx.Component:
    items: list[rx.Component] = [_rail_logo(), rx.box(class_name="rail-sep")]
    for gi, group in enumerate(_NAV_GROUPS):
        if gi > 0:
            items.append(rx.box(class_name="rail-sep"))
        items.append(rx.box(*[_rail_item(*it) for it in group], class_name="rail-group"))
    items.append(rx.box(class_name="rail-spacer"))

    bottom: list[rx.Component] = [
        rx.cond(
            AppState.auth_required,
            rx.box(
                rx.icon("log-out", size=19),
                rx.el.span("Log out", class_name="rail-tip"),
                class_name="rail-item",
                on_click=AppState.logout,
            ),
            rx.fragment(),
        ),
        rx.box(
            rx.color_mode_cond(
                light=rx.icon("moon", size=19),
                dark=rx.icon("sun", size=19),
            ),
            rx.el.span(
                rx.color_mode_cond(light="Dark mode", dark="Light mode"),
                class_name="rail-tip",
            ),
            class_name="rail-item",
            on_click=rx.toggle_color_mode,
        ),
    ]
    items.append(rx.box(*bottom, class_name="rail-group"))
    return rx.el.nav(*items, class_name="rail")


# ── Top bar ───────────────────────────────────────────────────────────


def _top_bar() -> rx.Component:
    path = AppState.router.page.path
    return rx.el.header(
        rx.box(
            rx.el.span("PlexMix", class_name="brand"),
            rx.el.span("/", class_name="crumb-sep"),
            rx.el.span(rx.match(path, *_PAGE_TITLES, "PlexMix"), class_name="t"),
            rx.el.span(rx.match(path, *_PAGE_SUBS, ""), class_name="s"),
            class_name="topbar-title",
        ),
        rx.box(class_name="topbar-spacer"),
        rx.box(
            rx.icon("search", size=15),
            rx.el.span("Search or jump to…"),
            rx.el.span("⌘K", class_name="kbd"),
            class_name="cmd-trigger",
            on_click=CommandPaletteState.open_palette,
        ),
        rx.el.button(
            rx.color_mode_cond(
                light=rx.icon("moon", size=18),
                dark=rx.icon("sun", size=18),
            ),
            class_name="icon-btn",
            on_click=rx.toggle_color_mode,
            title="Toggle theme",
            type="button",
        ),
        rx.box(
            "iz",
            class_name="avatar",
            title="izzo · Plex",
            on_click=rx.redirect("/settings"),
        ),
        class_name="topbar",
    )


# ── Command palette ───────────────────────────────────────────────────


def _cmd_item(icon: str, title, sub: str, on_click) -> rx.Component:
    return rx.box(
        rx.box(rx.icon(icon, size=16), class_name="ci-ico"),
        rx.box(
            rx.el.div(title, class_name="ci-title"),
            rx.el.div(sub, class_name="ci-sub"),
            style={"flex": "1"},
        ),
        class_name="cmdk-item",
        on_click=on_click,
    )


def _cmd_group(label: str, body: rx.Component) -> rx.Component:
    return rx.box(rx.el.div(label, class_name="cmdk-group-label"), body)


def _command_palette() -> rx.Component:
    return rx.cond(
        CommandPaletteState.is_open,
        rx.box(
            rx.box(
                # input row
                rx.box(
                    rx.icon("search", size=18, color="var(--fg-3)"),
                    rx.el.input(
                        placeholder="Search pages, actions, vibes…",
                        value=CommandPaletteState.query,
                        on_change=CommandPaletteState.set_query,
                        class_name="cmdk-input",
                        auto_focus=True,
                    ),
                    rx.el.span("esc", class_name="kbd"),
                    class_name="cmdk-input-row",
                ),
                # results
                rx.box(
                    rx.cond(
                        ~CommandPaletteState.has_results,
                        rx.box(
                            "No matches",
                            style={
                                "padding": "28px 12px",
                                "textAlign": "center",
                                "color": "var(--fg-3)",
                                "fontSize": "14px",
                            },
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        CommandPaletteState.action_results.length() > 0,
                        _cmd_group(
                            "Actions",
                            rx.foreach(
                                CommandPaletteState.action_results,
                                lambda c: _cmd_item(
                                    c["icon"],
                                    c["title"],
                                    c["sub"],
                                    CommandPaletteState.run_action(c["id"]),
                                ),
                            ),
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        CommandPaletteState.nav_results.length() > 0,
                        _cmd_group(
                            "Jump to",
                            rx.foreach(
                                CommandPaletteState.nav_results,
                                lambda c: _cmd_item(
                                    c["icon"],
                                    c["title"],
                                    c["sub"],
                                    CommandPaletteState.goto(c["href"]),
                                ),
                            ),
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        CommandPaletteState.vibe_results.length() > 0,
                        _cmd_group(
                            "Quick vibes",
                            rx.foreach(
                                CommandPaletteState.vibe_results,
                                lambda v: _cmd_item(
                                    "music",
                                    v,
                                    "Generate this vibe",
                                    CommandPaletteState.run_vibe(v),
                                ),
                            ),
                        ),
                        rx.fragment(),
                    ),
                    class_name="cmdk-list",
                ),
                # hidden close target for the Escape key handler
                rx.el.button(
                    class_name="cmdk-close",
                    on_click=CommandPaletteState.close_palette,
                    type="button",
                    style={"display": "none"},
                ),
                class_name="cmdk",
                on_click=CommandPaletteState.stop.stop_propagation,
            ),
            class_name="cmdk-backdrop",
            on_click=CommandPaletteState.close_palette,
        ),
        rx.fragment(),
    )


# ── Changelog dialog (preserved) ──────────────────────────────────────


def _changelog_dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title(
                rx.hstack(
                    rx.icon("file-text", size=18),
                    rx.text("Changelog"),
                    spacing="2",
                    align="center",
                ),
            ),
            rx.dialog.description(
                rx.text("Release history for PlexMix", size="2", color="gray.9"),
            ),
            rx.scroll_area(
                rx.markdown(AppState.changelog_content),
                max_height="60vh",
                padding_right="12px",
            ),
            rx.dialog.close(
                rx.button("Close", variant="soft", size="2", margin_top="16px"),
            ),
            max_width="640px",
        ),
        open=AppState.show_changelog,
        on_open_change=lambda is_open: rx.cond(
            is_open, AppState.open_changelog, AppState.close_changelog
        ),
    )


# ── Global keyboard (consolidated, idempotent) ────────────────────────


def _keyboard_shortcuts() -> rx.Component:
    """⌘K / '/' open the palette; g+key page nav; Escape closes the palette.

    Installed once (window-flag guarded) so the per-route shell re-render
    does not stack duplicate listeners. Bridges to Reflex via synthetic
    clicks on the always-present ``.cmd-trigger`` and ``.cmdk-close`` elements.
    """
    return rx.script(
        """
        if (!window.__plexmix_keys__) {
          window.__plexmix_keys__ = true;
          document.addEventListener('keydown', function (e) {
            var t = e.target;
            var typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
            if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
              e.preventDefault();
              var trg = document.querySelector('.cmd-trigger'); if (trg) trg.click();
              return;
            }
            if (e.key === 'Escape') {
              var c = document.querySelector('.cmdk-close'); if (c) c.click();
              return;
            }
            var __cmdk = document.querySelector('.cmdk');
            if (__cmdk) {
              var __items = __cmdk.querySelectorAll('.cmdk-item');
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                if (!__items.length) return;
                if (window.__cmdk_i == null) window.__cmdk_i = -1;
                window.__cmdk_i += (e.key === 'ArrowDown' ? 1 : -1);
                if (window.__cmdk_i < 0) window.__cmdk_i = __items.length - 1;
                if (window.__cmdk_i >= __items.length) window.__cmdk_i = 0;
                __items.forEach(function(it, ix){ it.classList.toggle('sel', ix === window.__cmdk_i); });
                __items[window.__cmdk_i].scrollIntoView({block: 'nearest'});
                return;
              }
              if (e.key === 'Enter' && window.__cmdk_i != null && __items[window.__cmdk_i]) {
                e.preventDefault(); __items[window.__cmdk_i].click(); return;
              }
            } else { window.__cmdk_i = null; }
            if (typing) return;
            if (e.key === '/') {
              e.preventDefault();
              var t2 = document.querySelector('.cmd-trigger'); if (t2) t2.click();
              return;
            }
            if (e.key === 'g') {
              window.__plexmix_g__ = true;
              setTimeout(function () { window.__plexmix_g__ = false; }, 600);
              return;
            }
            if (window.__plexmix_g__) {
              window.__plexmix_g__ = false;
              var routes = {d:'/dashboard', g:'/generator', l:'/library', t:'/tagging', h:'/history', x:'/doctor', s:'/settings'};
              if (routes[e.key]) { window.location.href = routes[e.key]; e.preventDefault(); }
            }
          });
        }
        """
    )


# ── Layout shell ──────────────────────────────────────────────────────


def _appearance_restore() -> rx.Component:
    """Re-apply persisted density/accent to <html> on load."""
    return rx.script(
        """
        (function(){
          try {
            var d=localStorage.getItem('pm_density'); if(d) document.documentElement.setAttribute('data-density', d);
            var a=localStorage.getItem('pm_accent'); if(a) document.documentElement.setAttribute('data-accent', a);
          } catch(e){}
        })();
        """
    )


def _cancel_confirm_dialog() -> rx.Component:
    """Shared, shell-level confirm for cancelling a running job (or all)."""
    return rx.cond(
        AppState.pending_cancel_job != "",
        rx.box(
            rx.box(
                rx.box(
                    rx.el.h2(
                        "Cancel running task?",
                        style={"fontSize": "18px", "fontWeight": "700"},
                    ),
                    class_name="modal-head",
                ),
                rx.box(
                    rx.el.p(
                        "Stop ",
                        rx.cond(
                            AppState.pending_cancel_job == "__all__",
                            "all running tasks",
                            AppState.pending_cancel_job,
                        ),
                        "? Work completed so far is kept.",
                        style={"fontSize": "14px", "color": "var(--fg-2)"},
                    ),
                    class_name="modal-body",
                ),
                rx.box(
                    rx.el.button(
                        "Keep running",
                        class_name="btn btn-3 btn-soft",
                        on_click=AppState.dismiss_cancel,
                        type="button",
                    ),
                    rx.el.button(
                        "Cancel task",
                        class_name="btn btn-3 btn-primary",
                        on_click=AppState.confirm_cancel,
                        type="button",
                    ),
                    class_name="modal-foot",
                ),
                class_name="modal",
                style={"maxWidth": "440px"},
                on_click=CommandPaletteState.stop.stop_propagation,
            ),
            class_name="modal-backdrop",
            on_click=AppState.dismiss_cancel,
        ),
        rx.fragment(),
    )


def _loadbar() -> rx.Component:
    return rx.box(rx.box(class_name="ind"), class_name="loadbar")


def layout(content: rx.Component, full_bleed: bool = False) -> rx.Component:
    """Wrap page content in the shared shell (auth-gated).

    ``full_bleed`` skips the centered ``.page`` wrapper for pages that manage
    their own full-height stage (e.g. the Generator showpiece).
    """
    inner = content if full_bleed else rx.box(content, class_name="page")
    return rx.cond(
        AppState.is_authenticated,
        rx.box(
            _icon_rail(),
            rx.box(
                _top_bar(),
                rx.box(
                    rx.cond(AppState.is_page_loading, _loadbar(), rx.fragment()),
                    inner,
                    class_name="scroll",
                ),
                class_name="main",
            ),
            _command_palette(),
            _cancel_confirm_dialog(),
            rx.toast.provider(),
            _changelog_dialog(),
            _keyboard_shortcuts(),
            _appearance_restore(),
            class_name="shell",
        ),
        login_page(),
    )
