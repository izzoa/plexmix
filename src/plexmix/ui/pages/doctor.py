"""Doctor — diagnostics and system health.

A verdict hero derived from the rows (``DoctorState.verdict``), then two lists of
check rows: Connections (Plex, AI provider, embedding provider, from the shared
``AppState.readiness``) and the library checks from the last scan. Each row is a
list item headed by an ``h3``; fix buttons are named "<action> – <row>" and go
through a scope confirmation before anything starts.
"""

from typing import Any

import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.scope_confirm import scope_confirm, scope_provider_line
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.doctor_state import ANOTHER_FIX_RUNNING, DoctorState

# Bound once as Any: calling an ``@rx.event`` handler with an argument from a
# typed function trips mypy's view of ``rx.event`` (EventNamespace).
_START_FIX: Any = DoctorState.start_fix

# (row id, readiness component, accessible row name, icon)
_CONNECTION_ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("conn_plex", "plex", "Plex", "server"),
    ("conn_ai", "ai", "AI provider", "brain"),
    ("conn_embeddings", "embeddings", "Embedding provider", "layers"),
)
# (row id, row name, icon) — names match readiness.doctor_rows titles.
_LIBRARY_ROWS: tuple[tuple[str, str, str], ...] = (
    ("library", "Library", "database"),
    ("embeddings", "Embeddings", "cpu"),
    ("orphans", "Orphaned embeddings", "unlink"),
    ("tags", "Tags", "tags"),
    ("audio", "Audio features", "audio-waveform"),
    ("musicbrainz", "MusicBrainz genres", "disc"),
)


def _ico(icon: str, row: Any) -> rx.Component:
    """The row's icon chip: green check when it passed, accent when it needs work."""
    ok = row["state"] == "ok"
    needs_work = (row["state"] == "attention") | (row["state"] == "no_database")
    return rx.box(
        rx.cond(
            ok,
            rx.icon("circle-check", size=20, color="var(--success-fg)"),
            rx.icon(icon, size=20, color=rx.cond(needs_work, "var(--accent-fg)", "var(--fg-3)")),
        ),
        class_name="ico",
        style={
            "background": rx.cond(
                ok,
                "var(--success-bg)",
                rx.cond(needs_work, "var(--brand-3)", "var(--surface-sunken)"),
            )
        },
    )


def _state_badge(row: Any) -> rx.Component:
    """The row's state word ("Checking…", "Healthy", "Off", "Ready"…) in a kit badge."""
    return rx.el.span(
        rx.cond(
            row["state"] == "checking",
            rx.icon("loader", size=12, class_name="spin"),
            rx.fragment(),
        ),
        row["state_word"],
        class_name=row["badge"],
    )


def _summary(row: Any) -> rx.Component:
    """The row's one-line result, with its count in the mono data style."""
    return rx.box(
        row["lead"],
        rx.cond(row["num"] != "", rx.el.span(row["num"], class_name="mono"), rx.fragment()),
        row["tail"],
        class_name="cdetail",
    )


def _row_error(row: Any) -> rx.Component:
    """The row's last fix failure and the action that fixes its cause."""
    return rx.cond(
        row["error_title"] != "",
        rx.box(
            rx.box(rx.icon("circle-alert", size=14), class_name="c-ico"),
            rx.box(
                rx.el.strong(row["error_title"]),
                rx.cond(
                    row["error_body"] != "",
                    rx.el.span(" ", row["error_body"]),
                    rx.fragment(),
                ),
                rx.cond(
                    row["error_action_href"] != "",
                    rx.el.a(row["error_action_label"], href=row["error_action_href"]),
                    rx.fragment(),
                ),
            ),
            class_name="callout callout-error cerr",
        ),
        rx.fragment(),
    )


def _link_button(label: Any, href: Any, name: str, primary: Any = False) -> rx.Component:
    """A Settings (or page) link styled as a kit button, named "<label> – <name>"."""
    if isinstance(primary, bool):
        class_name: Any = "btn btn-sm btn-primary" if primary else "btn btn-sm btn-soft"
    else:
        class_name = rx.cond(primary, "btn btn-sm btn-primary", "btn btn-sm btn-soft")
    return rx.el.a(label, href=href, class_name=class_name, aria_label=f"{label} – {name}")


def _fix_button(row: Any, row_id: str, name: str) -> rx.Component:
    """The row's fix: opens the scope confirmation (DoctorState.start_fix).

    While another fix runs it is disabled with the visible reason; the running
    fix's own button stays focusable (aria-disabled + aria-busy) so focus that
    returns to it after the confirmation is not lost.
    """
    running_here = DoctorState.is_fixing & (DoctorState.current_fix_target == row["fix"])
    other_running = DoctorState.is_fixing & (DoctorState.current_fix_target != row["fix"])
    busy_id = f"doctor-busy-{row_id}"
    return rx.fragment(
        rx.el.button(
            rx.cond(running_here, rx.icon("loader", size=14, class_name="spin"), rx.fragment()),
            row["fix_label"],
            class_name="btn btn-sm btn-soft",
            on_click=_START_FIX(row["fix"]),
            disabled=other_running,
            aria_disabled=rx.cond(running_here, "true", None),
            aria_busy=rx.cond(running_here, "true", None),
            aria_describedby=rx.cond(other_running, busy_id, None),
            aria_label=f"{row['fix_label']} – {name}",
            type="button",
        ),
        rx.cond(
            other_running,
            rx.el.span(ANOTHER_FIX_RUNNING, id=busy_id, class_name="cbusy"),
            rx.fragment(),
        ),
    )


def _check_row(row_id: str, name: str, icon: str) -> rx.Component:
    """One library check: state, measured result, why it matters, fix or link.

    The state comes from the row (``DoctorState.row_map``), never from a count
    that might still be a default: before the scan finishes it reads "Checking…".
    """
    row = DoctorState.row_map[row_id]
    return rx.el.li(
        _ico(icon, row),
        rx.box(
            rx.el.h3(name, class_name="cname"),
            _summary(row),
            rx.box(row["why"], class_name="cwhy"),
            _row_error(row),
            class_name="cinfo",
        ),
        rx.box(
            _state_badge(row),
            rx.cond(
                row["fix"] != "",
                _fix_button(row, row_id, name),
                rx.cond(
                    row["action_href"] != "",
                    _link_button(row["action_label"], row["action_href"], name),
                    rx.fragment(),
                ),
            ),
            class_name="cact",
        ),
        class_name="check",
    )


def _connection_check(row: Any, component: str, name: str) -> rx.Component:
    """Check: Plex runs its free reachability check in place; providers open Settings."""
    if component != "plex":
        return _link_button(row["check_label"], row["check_href"], name)
    level = AppState.readiness["plex"]["level"]
    return rx.cond(
        level == "not_set_up",
        _link_button(row["check_label"], row["check_href"], name),
        rx.el.button(
            row["check_label"],
            class_name="btn btn-sm btn-soft",
            on_click=AppState.refresh_plex_readiness(True),
            aria_busy=rx.cond(level == "checking", "true", None),
            aria_label=f"{row['check_label']} – {name}",
            type="button",
        ),
    )


def _connection_row(row_id: str, component: str, name: str, icon: str) -> rx.Component:
    """One connection (Plex, AI, embeddings) from the shared readiness snapshot."""
    row = DoctorState.row_map[row_id]
    return rx.cond(
        row["state"] != "hidden",
        rx.el.li(
            _ico(icon, row),
            rx.box(
                rx.el.h3(row["title"], class_name="cname"),
                rx.box(
                    rx.cond(row["model"] != "", rx.el.span(row["model"], class_name="mono"), ""),
                    rx.cond(
                        row["checked_ago"] != "",
                        rx.el.span(
                            rx.cond(row["model"] != "", " · ", ""),
                            "checked ",
                            row["checked_ago"],
                        ),
                        rx.fragment(),
                    ),
                    class_name="cdetail",
                ),
                rx.cond(
                    row["state"] == "attention",
                    rx.box(
                        rx.el.strong(row["summary"]),
                        rx.cond(row["body"] != "", rx.el.span(" ", row["body"]), rx.fragment()),
                        class_name="cdetail",
                    ),
                    rx.fragment(),
                ),
                rx.box(row["why"], class_name="cwhy"),
                class_name="cinfo",
            ),
            rx.box(
                _state_badge(row),
                _connection_check(row, component, name),
                rx.cond(
                    row["action_href"] != "",
                    _link_button(row["action_label"], row["action_href"], name),
                    rx.fragment(),
                ),
                class_name="cact",
            ),
            class_name="check",
        ),
        rx.fragment(),
    )


def _library_block() -> rx.Component:
    """The single "No library yet" (or scan failed) item that replaces the library rows."""
    row = DoctorState.row_map["library"]
    plex_not_set_up = AppState.readiness["plex"]["level"] == "not_set_up"
    return rx.el.li(
        _ico("database", row),
        rx.box(
            rx.el.h3(row["title"], class_name="cname"),
            rx.box(row["summary"], class_name="cdetail"),
            rx.box(row["why"], class_name="cwhy"),
            class_name="cinfo",
        ),
        rx.box(
            # "No library yet" is already the heading: show the two ways forward
            # (Connect Plex, Sync library) instead of repeating it in a badge.
            rx.cond(
                row["state"] == "no_database",
                rx.fragment(
                    _link_button(
                        row["action_label"], row["action_href"], "Library", plex_not_set_up
                    ),
                    _link_button(
                        row["secondary_label"], row["secondary_href"], "Library", ~plex_not_set_up
                    ),
                ),
                _state_badge(row),
            ),
            class_name="cact",
        ),
        class_name="check doctor-nolib",
    )


def _fix_progress_bar() -> rx.Component:
    """Doctor fix progress, exposed in tracks (value N within 0..total).

    When the job's total is not known yet (orphan cleanup, or the first moments
    of any fix) the bar is the kit's indeterminate ``.loadbar``: still a named
    progressbar, with ``aria-valuenow``/``aria-valuemax`` omitted.
    """
    known = DoctorState.fix_total > 0
    return rx.box(
        rx.cond(
            known,
            rx.box(class_name="pfill", style={"width": f"{DoctorState.fix_percent}%"}),
            rx.box(class_name="ind"),
        ),
        class_name=rx.cond(known, "pbar doctor-fix-pbar", "loadbar doctor-fix-loadbar"),
        role="progressbar",
        aria_label="Fix progress",
        aria_valuemin="0",
        aria_valuemax=rx.cond(known, DoctorState.fix_total, None),
        aria_valuenow=rx.cond(known, DoctorState.fix_progress, None),
        aria_valuetext=rx.cond(known, f"{DoctorState.fix_progress_label} tracks", None),
    )


def _fix_confirm_dialog() -> rx.Component:
    """Scope confirmation for every Doctor fix (count, provider, data, duration)."""
    scope = DoctorState.fix_scope
    return scope_confirm(
        open_var=DoctorState.fix_confirm_open,
        on_open_change=DoctorState.set_fix_confirm_open,
        title=scope["title"],
        lines=[
            scope["count_line"],
            scope["replace_line"],
            rx.cond(
                scope["provider_label"] != "",
                scope_provider_line(scope["provider_label"], scope["model"]),
                rx.fragment(),
            ),
            scope["duration_line"],
        ],
        privacy_note=scope["privacy_line"],
        confirm_label=scope["confirm_label"],
        on_confirm=DoctorState.confirm_fix,
        danger=scope["danger"] == "true",
        id_prefix="doctor-fix-confirm",
    )


def _hero() -> rx.Component:
    """The verdict, derived from the rows: never "healthy" before the scan measured it."""
    verdict = DoctorState.verdict
    tone = verdict["tone"]
    return rx.box(
        rx.box(
            rx.cond(
                verdict["state"] == "checking",
                rx.icon("loader", size=26, color="var(--fg-3)", class_name="spin"),
                rx.cond(
                    tone == "success",
                    rx.icon("shield-check", size=26, color="var(--success-fg)"),
                    rx.icon(
                        "stethoscope",
                        size=26,
                        color=rx.cond(tone == "error", "var(--error-fg)", "var(--accent-fg)"),
                    ),
                ),
            ),
            class_name="ico",
            style={
                "background": rx.cond(
                    tone == "success",
                    "var(--success-bg)",
                    rx.cond(
                        tone == "error",
                        "var(--error-bg)",
                        rx.cond(tone == "idle", "var(--surface-sunken)", "var(--brand-3)"),
                    ),
                ),
                "width": "52px",
                "height": "52px",
                "borderRadius": "var(--radius-lg)",
                "flexShrink": "0",
            },
        ),
        rx.box(
            rx.el.h2(
                verdict["title"],
                style={
                    "fontSize": "18px",
                    "fontWeight": "700",
                    "fontFamily": "var(--font-display)",
                    # Keep the box metrics the old <div> inherited.
                    "lineHeight": "inherit",
                    "letterSpacing": "inherit",
                },
            ),
            # Persistent polite live region: the scan result (verdict and its
            # sub-line) is announced without moving focus.
            rx.box(
                rx.el.span(verdict["title"], ". ", class_name="sr-only"),
                DoctorState.scan_summary,
                role="status",
                aria_live="polite",
                aria_atomic="true",
                id="doctor-check-status",
                class_name="fg3",
                style={"fontSize": "13px", "marginTop": "2px"},
            ),
            # 12rem basis: when the hero is too narrow for icon + text + button on
            # one line, the text takes its own line instead of shrinking to a sliver.
            style={"flex": "1 1 12rem", "minWidth": "0"},
        ),
        # Quiet: the scan runs on every visit; this only refreshes it. It stays
        # focusable while a scan runs (aria-busy); a second scan never starts.
        rx.el.button(
            rx.cond(
                DoctorState.is_checking,
                rx.icon("refresh-cw", size=14, class_name="spin"),
                rx.icon("refresh-cw", size=14),
            ),
            "Re-scan",
            class_name="btn btn-sm btn-ghost",
            on_click=DoctorState.run_health_check,
            aria_busy=rx.cond(DoctorState.is_checking, "true", None),
            type="button",
        ),
        class_name="card doctor-hero",
        style={
            "display": "flex",
            "flexWrap": "wrap",
            "alignItems": "center",
            "gap": "16px",
            "padding": "18px 20px",
            "width": "100%",
        },
    )


def doctor() -> rx.Component:
    content = rx.vstack(
        # Hidden poller so Doctor fix progress updates live (not just on page load).
        rx.box(
            rx.moment(interval=2000, on_change=DoctorState.poll_task_progress),
            display="none",
        ),
        _hero(),
        # Fix-in-progress callout
        rx.cond(
            DoctorState.is_fixing,
            rx.box(
                rx.box(rx.icon("loader", size=16, class_name="spin"), class_name="c-ico"),
                rx.box(
                    rx.box(
                        DoctorState.fix_title,
                        rx.cond(
                            DoctorState.fix_total > 0,
                            rx.el.span(
                                " · ",
                                rx.el.span(DoctorState.fix_progress_label, class_name="mono"),
                                " tracks",
                            ),
                            rx.fragment(),
                        ),
                    ),
                    _fix_progress_bar(),
                    class_name="c-body",
                    style={"flex": "1"},
                ),
                rx.el.button(
                    rx.icon("x", size=14),
                    "Cancel",
                    class_name="btn btn-sm btn-ghost",
                    on_click=AppState.request_cancel("doctor_fix"),
                    type="button",
                ),
                class_name="callout callout-info",
                style={"width": "100%", "alignItems": "center"},
            ),
            rx.fragment(),
        ),
        # Connections: Plex, AI provider, embedding provider (shared readiness).
        rx.el.p("Connections", id="doctor-conn-label", class_name="eyebrow doctor-group-label"),
        rx.el.ul(
            *[_connection_row(*spec) for spec in _CONNECTION_ROWS],
            # role="list": WebKit drops list semantics for ``list-style: none``.
            role="list",
            aria_labelledby="doctor-conn-label",
            class_name="card doctor-checks",
            style={"width": "100%", "overflow": "hidden"},
        ),
        # Library checks from the last scan, or one "No library yet" item.
        rx.el.p("Library checks", id="doctor-lib-label", class_name="eyebrow doctor-group-label"),
        rx.el.ul(
            rx.cond(
                DoctorState.library_collapsed,
                _library_block(),
                rx.fragment(*[_check_row(*spec) for spec in _LIBRARY_ROWS]),
            ),
            role="list",
            aria_labelledby="doctor-lib-label",
            class_name="card doctor-checks",
            style={"width": "100%", "overflow": "hidden"},
        ),
        _fix_confirm_dialog(),
        spacing="4",
        width="100%",
        on_mount=DoctorState.on_load,
    )
    return layout(content)
