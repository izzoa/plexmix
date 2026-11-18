"""Dashboard — setup checklist, library coverage, readiness strip, quick actions, recent.

While setup is incomplete a headed "Set up PlexMix" checklist comes first; its
current step is the page's only primary action. The "Library overview" section
(keyboard-and-semantics) holds the header readout ("5,943 tracks · synced 2 h
ago"), the four coverage readouts, the readiness status strip and the dimension
warning. Recent playlist rows open their detail in History.
"""

from typing import Any, Union

import reflex as rx
from reflex.vars.object import ObjectVar

from plexmix.ui.components.navbar import layout
from plexmix.ui.components.scope_confirm import embed_all_confirm_dialog
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.dashboard_state import DashboardState
from plexmix.ui.states.library_state import LibraryState

# Event handlers this page calls with an argument. Reflex binds the argument
# into an EventSpec at render time, which mypy cannot model for decorated
# handlers, so they are referenced through Any-typed names.
_request_cancel: Any = AppState.request_cancel
_set_sync_mode: Any = LibraryState.set_sync_mode
_watch_setup_job: Any = DashboardState.watch_setup_job

# ── Coverage readouts ────────────────────────────────────────────────

# (tile id in DashboardState.coverage, label, icon, icon colour, icon chip fill)
_COVERAGE_TILES: tuple[tuple[str, str, str, str, str], ...] = (
    ("tagged", "Tagged", "tags", "var(--accent-fg)", "var(--brand-3)"),
    ("embedded", "Embedded", "cpu", "var(--info-fg)", "var(--info-bg)"),
    ("audio", "Audio analyzed", "audio-waveform", "var(--success-fg)", "var(--success-bg)"),
    ("musicbrainz", "MusicBrainz genres", "disc", "var(--purple-fg)", "var(--purple-bg)"),
)


def _tile_top(tile_id: str, label: str, icon: str, color: str, bg: str) -> rx.Component:
    return rx.box(
        rx.box(
            rx.icon(icon, size=18, color=color),
            class_name="ico",
            style={"background": bg},
            aria_hidden="true",
        ),
        rx.el.span(label, id=f"cov-{tile_id}-label", class_name="cov-label"),
        class_name="cov-top",
    )


def _coverage_tile(tile_id: str, label: str, icon: str, color: str, bg: str) -> rx.Component:
    """One readout: grouped mono count, share of the library, a meter, and a link
    to the page that fills the gap below 100% ("Not installed"/"Off" instead of
    a bare zero for the optional enrichments)."""
    tile = DashboardState.coverage[tile_id]
    return rx.box(
        _tile_top(tile_id, label, icon, color, bg),
        rx.cond(
            tile["show"] == "true",
            rx.fragment(
                rx.box(
                    rx.el.span(tile["count"], class_name="cov-num"),
                    rx.el.span(tile["percent"], class_name="cov-pct"),
                    class_name="cov-figs",
                ),
                rx.box(
                    rx.box(class_name="pfill", style={"width": tile["width"] + "%"}),
                    class_name="pbar cov-meter",
                    role="progressbar",
                    aria_valuemin="0",
                    aria_valuemax="100",
                    aria_valuenow=tile["width"],
                    aria_valuetext=tile["valuetext"],
                    aria_labelledby=f"cov-{tile_id}-label",
                ),
            ),
            rx.fragment(),
        ),
        rx.cond(tile["word"] != "", rx.el.p(tile["word"], class_name="cov-word"), rx.fragment()),
        rx.cond(tile["note"] != "", rx.el.p(tile["note"], class_name="cov-note"), rx.fragment()),
        rx.cond(
            tile["link_href"] != "",
            rx.el.a(
                tile["link_label"],
                href=tile["link_href"],
                aria_label=tile["link_aria"],
                class_name="cov-link",
            ),
            rx.fragment(),
        ),
        class_name="tile cov-tile",
    )


def _coverage_skeleton(tile_id: str, label: str, icon: str, color: str, bg: str) -> rx.Component:
    """The same tile while the figures load: never a zero before the real value."""
    return rx.box(
        _tile_top(f"{tile_id}-skel", label, icon, color, bg),
        rx.box(class_name="skeleton cov-skel-num"),
        rx.box(class_name="skeleton cov-skel-bar"),
        class_name="tile cov-tile",
        aria_hidden="true",
    )


def _coverage_grid() -> rx.Component:
    """Four coverage readouts (skeletons until the first stats load).

    Hidden once loaded when the library has no tracks (the header readout and
    the setup checklist say so instead of four empty tiles).
    """
    return rx.cond(
        DashboardState.library_stats_loaded,
        rx.cond(
            DashboardState.stat_total > 0,
            rx.box(
                *[_coverage_tile(*spec) for spec in _COVERAGE_TILES],
                class_name="stat-grid cov-grid",
                style={"width": "100%"},
            ),
            rx.fragment(),
        ),
        rx.box(
            *[_coverage_skeleton(*spec) for spec in _COVERAGE_TILES],
            class_name="stat-grid cov-grid",
            style={"width": "100%"},
            aria_busy="true",
        ),
    )


def _library_readout() -> rx.Component:
    """ "5,943 tracks · synced 2 h ago" with the absolute sync time on the same element."""
    return rx.el.p(
        rx.cond(
            DashboardState.library_stats_loaded,
            rx.fragment(
                rx.cond(
                    DashboardState.stat_total > 0,
                    rx.el.span(
                        rx.el.span(DashboardState.library_count, class_name="cov-total"),
                        " ",
                        DashboardState.library_noun,
                    ),
                    rx.el.span("No tracks yet"),
                ),
                rx.el.span(" · ", aria_hidden="true"),
                rx.cond(
                    DashboardState.last_sync_ago != "",
                    rx.el.time(
                        DashboardState.sync_readout,
                        date_time=DashboardState.last_sync_at,
                        title=DashboardState.last_sync_title,
                        class_name="cov-sync",
                    ),
                    rx.el.span(DashboardState.sync_readout, class_name="cov-sync"),
                ),
            ),
            rx.fragment(
                rx.el.span(class_name="skeleton cov-skel-head", aria_hidden="true"),
                rx.el.span("Loading library figures…", class_name="sr-only"),
            ),
        ),
        class_name="cov-head",
        id="dash-library-readout",
    )


# ── Readiness status strip ───────────────────────────────────────────

# (component, visible name, accessible name for its action, icon)
_STATUS_ITEMS: tuple[tuple[str, str, str, str], ...] = (
    ("plex", "Plex", "Plex", "server"),
    ("ai", "AI", "AI provider", "brain"),
    ("embeddings", "Embeddings", "embedding provider", "layers"),
)


def _status_item(component: str, name: str, aria_name: str, icon: str) -> rx.Component:
    """One readiness readout: state-coloured dot, display name + mono model, the
    state in words (with the cause when something is wrong), when it was last
    checked, and the action for that state (Set up / Check / Fix)."""
    entry = DashboardState.readiness[component]
    level = entry["level"]
    if component == "plex":
        # Plex: the music library name (the "model" field), no model id.
        value: rx.Component = rx.cond(
            entry["model"] != "",
            rx.el.span(entry["model"], class_name="val"),
            rx.fragment(),
        )
    else:
        value = rx.el.span(
            rx.el.span(entry["label"], class_name="val"),
            rx.cond(
                entry["model"] != "",
                rx.fragment(" · ", rx.el.span(entry["model"], class_name="mono st-model")),
                rx.fragment(),
            ),
        )
    problem = (level == "failing") | (level == "not_set_up") | (level == "not_installed")
    return rx.box(
        rx.el.span(class_name="dot dot-" + entry["tone"], aria_hidden="true"),
        rx.icon(icon, size=14, color="var(--fg-3)", aria_hidden="true"),
        rx.box(
            rx.el.span(
                rx.el.span(name + ": ", class_name="lab"),
                value,
                class_name="st-name",
            ),
            rx.el.span(entry["state"], class_name="st-state"),
            rx.cond(
                problem & (entry["title"] != ""),
                rx.el.span(entry["title"], class_name="st-cause"),
                rx.fragment(),
            ),
            rx.cond(
                entry["checked_ago"] != "",
                rx.el.span(
                    "checked ",
                    entry["checked_ago"],
                    title=entry["checked_title"],
                    class_name="st-when",
                ),
                rx.fragment(),
            ),
            class_name="st-text",
        ),
        rx.cond(
            entry["action_label"] != "",
            rx.el.a(
                entry["action_label"],
                href=entry["action_href"],
                aria_label=entry["action_label"] + " " + aria_name,
                class_name="st-action",
            ),
            rx.fragment(),
        ),
        class_name="item st-item",
        id=f"dash-status-{component}",
    )


def _status_bar() -> rx.Component:
    items: list[rx.Component] = []
    for i, spec in enumerate(_STATUS_ITEMS):
        if i:
            items.append(rx.box(class_name="vsep", aria_hidden="true"))
        items.append(_status_item(*spec))
    skeleton: list[rx.Component] = []
    for i, (_component, name, _aria, icon) in enumerate(_STATUS_ITEMS):
        if i:
            skeleton.append(rx.box(class_name="vsep", aria_hidden="true"))
        skeleton.append(
            rx.box(
                rx.el.span(class_name="dot dot-idle", aria_hidden="true"),
                rx.icon(icon, size=14, color="var(--fg-3)", aria_hidden="true"),
                rx.el.span(name + ": ", class_name="lab"),
                rx.el.span(class_name="skeleton st-skel", aria_hidden="true"),
                class_name="item st-item",
            )
        )
    return rx.cond(
        DashboardState.library_stats_loaded,
        rx.box(*items, class_name="card statusbar"),
        rx.box(*skeleton, class_name="card statusbar", aria_busy="true"),
    )


def _dimension_warning() -> rx.Component:
    """The dimension-mismatch warning: names both sizes and offers the two ways
    out from readiness copy ("Rebuild embeddings" → Doctor, "Switch back" →
    Settings at the Embeddings section)."""
    index = DashboardState.readiness["index"]
    return rx.cond(
        DashboardState.embedding_dimension_warning != "",
        rx.box(
            rx.box(rx.icon("triangle-alert", size=16), class_name="c-ico", aria_hidden="true"),
            rx.box(
                rx.box(DashboardState.embedding_dimension_warning, class_name="c-body"),
                rx.box(
                    rx.cond(
                        index["action_href"] != "",
                        rx.el.a(
                            index["fix_label"],
                            href=index["action_href"],
                            class_name="btn btn-sm btn-soft",
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        index["secondary_href"] != "",
                        rx.el.a(
                            index["secondary_label"],
                            href=index["secondary_href"],
                            class_name="btn btn-sm btn-ghost",
                        ),
                        rx.fragment(),
                    ),
                    class_name="notice-links",
                ),
                class_name="notice-text",
            ),
            class_name="callout callout-warning",
            id="dash-dimension-warning",
        ),
        rx.fragment(),
    )


def _unreadable_settings() -> rx.Component:
    """In place of the figures when the settings can't be read: the counts are
    unknown, so no "No tracks yet" readout, coverage tiles or setup steps."""
    return rx.box(
        rx.box(rx.icon("circle-alert", size=16), class_name="c-ico", aria_hidden="true"),
        rx.box(
            rx.box("PlexMix couldn't read its settings", class_name="c-title"),
            rx.box(
                "Your library figures and setup steps show once the settings can be read. "
                "Details are in the server log.",
                class_name="c-body",
            ),
            rx.box(
                rx.el.a("Open settings", href="/settings", class_name="btn btn-sm btn-soft"),
                rx.el.button(
                    "Try again",
                    class_name="btn btn-sm btn-ghost",
                    on_click=DashboardState.on_load,
                    type="button",
                ),
                class_name="notice-links",
            ),
            class_name="notice-text",
        ),
        class_name="callout callout-error",
        id="dash-settings-error",
    )


def _overview_section() -> rx.Component:
    """Header readout, coverage readouts, readiness strip and the dimension
    warning, under one sr-only h2 (keyboard-and-semantics).

    The inner vstack keeps the page's 'spacing 5' rhythm between the blocks.
    When the settings can't be read, an error takes the place of the readout and
    the coverage readouts (their figures would be unknown, not zero).
    """
    return rx.el.section(
        rx.el.h2("Library overview", id="dash-overview", class_name="sr-only"),
        rx.vstack(
            rx.cond(
                DashboardState.settings_unreadable,
                _unreadable_settings(),
                rx.fragment(_library_readout(), _coverage_grid()),
            ),
            _status_bar(),
            _dimension_warning(),
            spacing="5",
            width="100%",
        ),
        aria_labelledby="dash-overview",
        style={"width": "100%"},
    )


# ── First-run checklist ──────────────────────────────────────────────

_STEP_BADGES = (
    ("done", "Done", "badge badge-green"),
    ("current", "Next step", "badge badge-orange"),
    ("running", "Running", "badge badge-blue"),
)


def _step_button_class(step: Any) -> Any:
    """Only the current step is primary-styled (the page's single primary action)."""
    return rx.cond(step["primary"] == "true", "btn btn-3 btn-primary", "btn btn-3 btn-soft")


def _step_badge(step: Any) -> Any:
    """The step's state in words ("Done", "Next step", "Running"; sr-only otherwise)."""
    state = step["state"]
    return rx.match(
        state,
        *[(value, rx.el.span(text, class_name=cls)) for value, text, cls in _STEP_BADGES],
        rx.el.span("Not started", class_name="sr-only"),
    )


def _step_link(step: Any) -> rx.Component:
    """A step that opens a page (Settings section or the Generator)."""
    return rx.cond(
        step["state"] != "done",
        rx.el.a(step["action_label"], href=step["href"], class_name=_step_button_class(step)),
        rx.fragment(),
    )


def _cancel_button(job: str, job_name: str) -> rx.Component:
    return rx.el.button(
        rx.icon("x", size=14, aria_hidden="true"),
        "Cancel",
        class_name="btn btn-sm btn-ghost",
        on_click=_request_cancel(job),
        aria_label=f"Cancel {job_name}",
        aria_haspopup="dialog",
        type="button",
    )


def _step_progress(step: Any, step_id: str, job_name: str) -> rx.Component:
    """The running job's message, percentage and a named progress bar."""
    msg_id = f"setup-{step_id}-msg"
    return rx.cond(
        step["state"] == "running",
        rx.box(
            rx.box(
                rx.el.span(
                    rx.el.span(job_name[:1].upper() + job_name[1:] + ": ", class_name="sr-only"),
                    step["message"],
                    id=msg_id,
                    class_name="setup-msg",
                ),
                rx.el.span(step["progress"] + "%", class_name="mono fg3 setup-pct"),
                class_name="setup-progress-head",
            ),
            rx.box(
                rx.box(class_name="pfill", style={"width": step["progress"] + "%"}),
                class_name="pbar",
                role="progressbar",
                aria_valuemin="0",
                aria_valuemax="100",
                aria_valuenow=step["progress"],
                aria_valuetext=step["progress"] + "%",
                aria_labelledby=msg_id,
            ),
            class_name="setup-progress",
        ),
        rx.fragment(),
    )


def _step_error(step_id: str) -> rx.Component:
    """Persistent alert region for the step's last job failure (inline, with an action)."""
    error = DashboardState.setup_error
    return rx.el.div(
        rx.cond(
            error["step"] == step_id,
            rx.box(
                rx.box(rx.icon("circle-alert", size=16), class_name="c-ico", aria_hidden="true"),
                rx.box(
                    rx.box(error["title"], class_name="c-title"),
                    rx.box(error["body"], class_name="c-body"),
                    rx.box(
                        rx.cond(
                            error["action_href"] != "",
                            rx.el.a(
                                error["action_label"],
                                href=error["action_href"],
                                class_name="btn btn-sm btn-soft",
                            ),
                            rx.fragment(),
                        ),
                        rx.el.button(
                            "Dismiss",
                            class_name="btn btn-sm btn-ghost",
                            on_click=DashboardState.dismiss_setup_error,
                            type="button",
                        ),
                        class_name="setup-error-actions",
                    ),
                ),
                class_name="callout callout-error",
            ),
            rx.fragment(),
        ),
        role="alert",
        id=f"setup-{step_id}-error",
        class_name="setup-error",
    )


def _sync_action(step: Any) -> rx.Component:
    """Sync library: an incremental sync followed by the setup poller."""
    return rx.cond(
        step["state"] == "running",
        _cancel_button("sync", "library sync"),
        rx.cond(
            step["state"] != "done",
            rx.el.button(
                rx.icon("refresh-cw", size=16, aria_hidden="true"),
                step["action_label"],
                class_name=_step_button_class(step),
                # The checklist always runs an incremental sync, whatever mode
                # the Library page last had selected. The poller is told first,
                # so the entry the sync replaces is never mistaken for it.
                on_click=[
                    _set_sync_mode("incremental"),
                    _watch_setup_job("sync"),
                    LibraryState.start_sync,
                ],
                type="button",
            ),
            rx.fragment(),
        ),
    )


def _index_action(step: Any) -> rx.Component:
    """Build search index: counts the missing embeddings and asks to confirm."""
    return rx.cond(
        step["state"] == "running",
        _cancel_button("embedding", "building the search index"),
        rx.cond(
            step["state"] != "done",
            rx.el.button(
                rx.icon("cpu", size=16, aria_hidden="true"),
                step["action_label"],
                class_name=_step_button_class(step),
                on_click=LibraryState.request_embed_all,
                aria_busy=LibraryState.embed_all_checking,
                aria_haspopup="dialog",
                type="button",
            ),
            rx.fragment(),
        ),
    )


def _setup_step(
    index: int,
    step_id: str,
    action: rx.Component,
    extra: Union[list[rx.Component], None] = None,
) -> rx.Component:
    step = DashboardState.setup_steps[index]
    state = step["state"]
    return rx.el.li(
        rx.el.span(
            rx.cond(state == "done", rx.icon("check", size=15), str(index + 1)),
            class_name="setup-mark",
            aria_hidden="true",
        ),
        rx.box(
            rx.box(
                rx.el.h3(step["title"], id=f"setup-{step_id}-title", class_name="setup-title"),
                _step_badge(step),
                class_name="setup-title-row",
            ),
            rx.el.p(step["desc"], class_name="setup-desc"),
            rx.cond(
                step["note"] != "", rx.el.p(step["note"], class_name="setup-note"), rx.fragment()
            ),
            *(extra or []),
            class_name="setup-body",
        ),
        rx.box(action, class_name="setup-action"),
        class_name="setup-step is-" + state,
        aria_current=rx.cond(state == "current", "step", None),
    )


def _embed_all_notice_links() -> rx.Component:
    """The embed-all notice's links (a mismatch: Rebuild embeddings / Switch back)."""
    return rx.cond(
        LibraryState.embed_all_notice_action_href != "",
        rx.box(
            rx.el.a(
                LibraryState.embed_all_notice_action_label,
                href=LibraryState.embed_all_notice_action_href,
                class_name="btn btn-sm btn-soft",
            ),
            rx.cond(
                LibraryState.embed_all_notice_secondary_href != "",
                rx.el.a(
                    LibraryState.embed_all_notice_secondary_label,
                    href=LibraryState.embed_all_notice_secondary_href,
                    class_name="btn btn-sm btn-ghost",
                ),
                rx.fragment(),
            ),
            class_name="notice-links",
        ),
        rx.fragment(),
    )


def _setup_checklist() -> rx.Component:
    """The first-run checklist (design D13): five explicit rows, in order."""
    steps = DashboardState.setup_steps
    return rx.el.section(
        rx.box(
            rx.el.h2("Set up PlexMix", id="dash-setup", class_name="setup-h"),
            rx.el.p(
                "Finish these steps to make your first playlist. Each one runs here in "
                "the browser.",
                class_name="setup-intro",
            ),
            class_name="setup-head",
        ),
        rx.el.ol(
            _setup_step(0, "plex", _step_link(steps[0])),
            _setup_step(1, "providers", _step_link(steps[1])),
            _setup_step(
                2,
                "sync",
                _sync_action(steps[2]),
                [_step_progress(steps[2], "sync", "library sync"), _step_error("sync")],
            ),
            _setup_step(
                3,
                "index",
                _index_action(steps[3]),
                [
                    _step_progress(steps[3], "index", "search index build"),
                    rx.el.div(
                        rx.cond(
                            LibraryState.embed_all_notice != "",
                            rx.fragment(
                                rx.el.p(LibraryState.embed_all_notice, class_name="setup-notice"),
                                _embed_all_notice_links(),
                            ),
                            rx.fragment(),
                        ),
                        role="status",
                        id="setup-index-notice",
                    ),
                    _step_error("index"),
                ],
            ),
            _setup_step(4, "vibe", _step_link(steps[4])),
            class_name="setup-steps",
            role="list",
        ),
        aria_labelledby="dash-setup",
        class_name="card setup",
        id="dash-setup-section",
    )


def _embed_all_dialog() -> rx.Component:
    """The whole-library "Build search index" confirmation, bound to LibraryState.

    The shared ``embed_all_confirm_dialog``; its confirm also tells the Dashboard
    poller to follow the job (first, so the entry the job replaces is never
    mistaken for it).
    """
    return embed_all_confirm_dialog(before_confirm=_watch_setup_job("embedding"))


def _setup_poller() -> rx.Component:
    """Polls the setup jobs every 2 s, mounted only while one runs."""
    return rx.cond(
        DashboardState.setup_polling,
        rx.box(
            rx.moment(interval=2000, on_change=DashboardState.poll_setup_progress),
            display="none",
        ),
        rx.fragment(),
    )


# ── Quick actions and recent playlists ───────────────────────────────


def _quick_action(
    title: str,
    desc: str,
    icon: str,
    href: str,
    color: str,
    bg: str,
    primary: Union[bool, rx.Var],
) -> rx.Component:
    if isinstance(primary, bool):
        card_class: Any = "card qa primary hover-lift" if primary else "card qa hover-lift"
    else:
        card_class = rx.cond(primary, "card qa primary hover-lift", "card qa hover-lift")
    return rx.link(
        rx.box(
            rx.box(rx.icon(icon, size=22, color=color), class_name="ico", style={"background": bg}),
            rx.box(
                rx.box(title, class_name="qt"),
                rx.box(desc, class_name="qd"),
                style={"flex": "1"},
            ),
            rx.icon("arrow-right", size=18, class_name="arrow"),
            class_name=card_class,
        ),
        href=href,
        underline="none",
        class_name="qa-link",
    )


def _recent_row(playlist: ObjectVar[dict[str, str]]) -> rx.Component:
    """A recent playlist: a link that opens its detail in History."""
    return rx.el.li(
        rx.el.a(
            rx.icon("list-music", size=14, color="var(--fg-3)", aria_hidden="true"),
            rx.el.span(playlist["name"], class_name="recent-name"),
            rx.el.span(
                rx.el.span(playlist["track_count"], class_name="mono"),
                " ",
                playlist["track_word"],
                class_name="fg3 recent-count",
            ),
            rx.cond(
                playlist["created_label"] != "",
                rx.el.span(
                    playlist["created_label"],
                    title=playlist["created_title"],
                    class_name="mono fg3 recent-date",
                ),
                rx.fragment(),
            ),
            href="/history?playlist=" + playlist["id"],
            class_name="recent-link hover-row",
        ),
        key=playlist["id"],
    )


def dashboard() -> rx.Component:
    content = rx.vstack(
        _setup_poller(),
        rx.cond(DashboardState.setup_incomplete, _setup_checklist(), rx.fragment()),
        _overview_section(),
        rx.box(
            rx.el.h2("Quick Actions", style={"fontSize": "17px", "fontWeight": "700"}),
            class_name="section-head",
            style={"width": "100%"},
        ),
        rx.box(
            _quick_action(
                "Generate Playlist",
                "Create an AI-powered playlist from a mood",
                "sparkles",
                "/generator",
                "var(--accent-fg)",
                "var(--brand-3)",
                DashboardState.generate_primary,
            ),
            _quick_action(
                "Sync Library",
                "Pull latest tracks from your Plex server",
                "refresh-cw",
                "/library",
                "var(--fg-2)",
                "var(--surface-sunken)",
                False,
            ),
            class_name="qa-grid",
            style={"width": "100%"},
        ),
        rx.cond(
            DashboardState.has_recent_playlists,
            rx.box(
                rx.box(
                    rx.el.h2("Recent Playlists", style={"fontSize": "17px", "fontWeight": "700"}),
                    rx.link("View all →", href="/history", class_name="more", underline="none"),
                    class_name="section-head",
                ),
                # role="list": WebKit drops list semantics for ``list-style: none``.
                rx.el.ul(
                    rx.foreach(DashboardState.recent_playlists[:5], _recent_row),
                    role="list",
                    class_name="card recent-list",
                    style={"padding": "6px"},
                ),
                style={"width": "100%"},
            ),
            rx.fragment(),
        ),
        _embed_all_dialog(),
        spacing="5",
        width="100%",
        on_mount=DashboardState.on_load,
    )
    return layout(content)
