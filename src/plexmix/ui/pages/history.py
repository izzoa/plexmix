"""History — saved & generated playlists as keyboard-operable cards + detail dialog."""

import reflex as rx
from reflex.vars.object import ObjectVar

from plexmix.ui.components.inputs import filter_input
from plexmix.ui.components.navbar import layout
from plexmix.ui.components.playlist_cover import playlist_cover
from plexmix.ui.states.history_state import (
    MOVE_DOWN_ID_PREFIX,
    MOVE_UP_ID_PREFIX,
    SEARCH_FIELD_NAME,
    SEARCH_INPUT_ID,
    HistoryState,
)

# Return focus to the dialog's opener (or #main-heading when it is gone) after a
# Radix dialog closes; see pmFocus in assets/plexmix.js.
_RESTORE_FOCUS = "window.pmFocus&&pmFocus.restore()"


def _pl_card(pl: ObjectVar[dict[str, str]]) -> rx.Component:
    """One saved playlist: a real button (inside an ``li``) that opens its dialog.

    The button is named by the playlist name and described by its facts (prompt,
    count, date, top artists). Its content is phrasing-only ``span``s. The cover
    and the hover/focus overlay are decorative and hidden from assistive tech.
    """
    pid = pl["id"]
    return rx.el.li(
        rx.el.button(
            rx.el.span(
                playlist_cover(pl["cover_variant"]),
                rx.el.span(
                    rx.el.span(rx.icon("list-music", size=18), class_name="pob"),
                    rx.el.span("Open", class_name="pl-open"),
                    class_name="pl-overlay",
                    aria_hidden="true",
                ),
                class_name="pl-art",
            ),
            rx.el.span(
                rx.el.span(pl["name"], id="pl-name-" + pid, class_name="pl-name"),
                # Everything the card knows besides its name; the button's
                # description. Facts that don't exist are omitted.
                rx.el.span(
                    rx.cond(
                        pl["mood_query"] != "",
                        rx.el.span("“", pl["mood_query"], "”", class_name="pl-mood"),
                    ),
                    rx.el.span(
                        pl["track_count"],
                        rx.cond(pl["track_count"] == "1", " track", " tracks"),
                        rx.cond(
                            pl["created_date"] != "",
                            rx.el.span(" · ", pl["created_date"]),
                        ),
                        class_name="pl-meta",
                    ),
                    rx.cond(
                        pl["top_artists"] != "",
                        rx.el.span(pl["top_artists"], class_name="pl-artists"),
                    ),
                    id="pl-meta-" + pid,
                    class_name="pl-facts",
                ),
                class_name="pl-body",
            ),
            type="button",
            id="pl-card-" + pid,
            aria_labelledby="pl-name-" + pid,
            aria_describedby="pl-meta-" + pid,
            aria_haspopup="dialog",
            class_name="card pl-card hover-lift",
            # mypy can't model Reflex event-handler calls (see the other handlers here).
            on_click=HistoryState.select_playlist(pid),  # type: ignore[call-arg, arg-type]
        ),
        # Keyed by playlist id, not the foreach index: deleting a playlist then
        # removes its own card node instead of re-pointing it at the next one.
        key=pid,
    )


def _detail_track(track: ObjectVar[dict[str, str]], index: rx.Var) -> rx.Component:
    """One track row in the detail dialog's ordered list, with named reorder controls.

    Reorder ids are ``hist-move-up-<index>`` / ``hist-move-down-<index>`` (0-based)
    so the move handlers can return focus to the moved track's control.
    """
    return rx.el.li(
        rx.el.span(track["position"], class_name="pos"),
        rx.el.span(
            rx.el.span(track["title"], class_name="ttl"),
            rx.el.span(track["artist"], class_name="art"),
            class_name="info",
        ),
        rx.el.span(track["duration_formatted"], class_name="dur"),
        rx.el.button(
            rx.icon("chevron-up", size=15),
            id=MOVE_UP_ID_PREFIX + index.to_string(),
            aria_label="Move " + track["title"] + " up",
            class_name="icon-btn",
            disabled=index == 0,
            on_click=HistoryState.move_track_up(index.to_string()),
            type="button",
        ),
        rx.el.button(
            rx.icon("chevron-down", size=15),
            id=MOVE_DOWN_ID_PREFIX + index.to_string(),
            aria_label="Move " + track["title"] + " down",
            class_name="icon-btn",
            disabled=index == HistoryState.selected_track_count - 1,
            on_click=HistoryState.move_track_down(index.to_string()),
            type="button",
        ),
        class_name="pl-trk",
    )


def _detail_footer() -> rx.Component:
    pl = HistoryState.selected_playlist
    return rx.box(
        rx.el.button(
            rx.icon("refresh-cw", size=16),
            "Rerun",
            class_name="btn btn-3 btn-primary",
            on_click=HistoryState.rerun_playlist,
            type="button",
        ),
        rx.el.button(
            rx.icon("server", size=16),
            "Export to Plex",
            class_name="btn btn-3 btn-blue",
            on_click=HistoryState.export_to_plex(pl["id"]),
            type="button",
        ),
        rx.el.button(
            rx.icon("download", size=16),
            "Export M3U",
            class_name="btn btn-3 btn-soft",
            on_click=HistoryState.export_to_m3u(pl["id"]),
            type="button",
        ),
        rx.el.button(
            rx.icon("braces", size=16),
            "Export JSON",
            class_name="btn btn-3 btn-soft",
            on_click=HistoryState.export_to_json(pl["id"]),
            type="button",
        ),
        rx.el.button(
            rx.icon("list-ordered", size=16),
            "Save order",
            class_name="btn btn-3 btn-soft",
            on_click=HistoryState.save_track_order,
            type="button",
        ),
        rx.el.button(
            rx.icon("trash-2", size=16),
            "Delete",
            class_name="btn btn-3 btn-danger",
            on_click=HistoryState.show_delete_confirmation(pl["id"]),
            aria_haspopup="dialog",
            type="button",
        ),
        class_name="modal-foot",
    )


def _detail_dialog() -> rx.Component:
    """The playlist detail view: a modal Radix dialog (Escape/backdrop close it).

    Titled by the playlist name and described by its prompt, count, duration and
    date. Focus returns to the opening card on close (pmFocus).
    """
    pl = HistoryState.selected_playlist
    return rx.dialog.root(
        rx.dialog.content(
            rx.box(
                rx.box(
                    rx.dialog.title(pl["name"], class_name="pl-dialog-title"),
                    rx.dialog.description(
                        rx.cond(
                            pl["mood_query"] != "",
                            rx.el.span("“", pl["mood_query"], "”", class_name="pl-dialog-mood"),
                        ),
                        rx.el.span(
                            pl["track_count"],
                            rx.cond(pl["track_count"] == "1", " track · ", " tracks · "),
                            pl["total_duration_formatted"],
                            rx.cond(
                                pl["created_date"] != "",
                                rx.el.span(" · ", pl["created_date"]),
                            ),
                            class_name="pl-dialog-stats",
                        ),
                        class_name="pl-dialog-desc",
                    ),
                    class_name="pl-dialog-heading",
                ),
                rx.dialog.close(
                    rx.el.button(
                        rx.icon("x", size=18),
                        class_name="icon-btn",
                        aria_label="Close",
                        type="button",
                    ),
                ),
                class_name="modal-head",
            ),
            rx.el.ol(
                rx.foreach(HistoryState.selected_playlist_tracks, _detail_track),
                class_name="modal-body pl-tracks",
                role="list",
                aria_label="Tracks",
            ),
            # Announces each reorder ("Moved <title> to position k of N").
            # Persistent and empty on open, so only real moves are spoken.
            rx.el.div(
                HistoryState.reorder_announcement,
                id="hist-reorder-status",
                role="status",
                aria_live="polite",
                aria_atomic="true",
                class_name="sr-only",
            ),
            _detail_footer(),
            class_name="pl-dialog",
            max_width="620px",
            aria_modal="true",
            on_close_auto_focus=rx.call_script(_RESTORE_FOCUS),
        ),
        open=HistoryState.is_detail_modal_open,
        on_open_change=HistoryState.set_detail_modal_open,
    )


def _delete_confirm_dialog() -> rx.Component:
    """Destructive confirm for deleting a saved playlist: an alert dialog.

    Cancel (the safe option) gets initial focus; outside clicks are inert and
    Escape dismisses. Delete closes the dialog by state (not ``alert_dialog.action``).
    """
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Delete this playlist?"),
            rx.alert_dialog.description(
                "This removes the saved playlist from PlexMix. Any copy already "
                "exported to Plex is not affected.",
            ),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.el.button(
                        "Cancel",
                        class_name="btn btn-3 btn-soft",
                        type="button",
                    ),
                ),
                rx.el.button(
                    "Delete",
                    class_name="btn btn-3 btn-danger",
                    on_click=HistoryState.confirm_delete,
                    type="button",
                ),
                spacing="2",
                margin_top="16px",
                justify="end",
            ),
            max_width="440px",
            aria_modal="true",
            on_close_auto_focus=rx.call_script(_RESTORE_FOCUS),
        ),
        open=HistoryState.is_delete_confirmation_open,
        on_open_change=HistoryState.set_delete_confirmation_open,
    )


def _filter_bar() -> rx.Component:
    """Search and sort. The bar is a form so Enter in the search applies it at once.

    The search field is client-owned (``filter_input``): it commits 300 ms after
    typing pauses and on blur, and the form's submit reads the text on screen
    (``apply_search_form``). The sort toggle is a plain ``type="button"``.
    """
    return rx.el.form(
        rx.box(
            rx.icon("search", size=15, color="var(--fg-3)"),
            filter_input(
                id=SEARCH_INPUT_ID,
                name=SEARCH_FIELD_NAME,
                value=HistoryState.search_query,
                on_commit=HistoryState.set_search_query,
                placeholder="Search playlists…",
                aria_label="Search playlists",
            ),
            class_name="search",
        ),
        rx.el.button(
            rx.icon("arrow-up-down", size=14),
            HistoryState.sort_by_label,
            class_name="btn btn-3 btn-soft",
            on_click=HistoryState.toggle_sort_order,
            aria_label=HistoryState.sort_button_label,
            type="button",
        ),
        on_submit=HistoryState.apply_search_form,
        custom_attrs={"data-enter-submit": ""},
        class_name="filterbar",
    )


def _skeleton_cards(count: int = 6) -> rx.Component:
    """Placeholder cards while the first load runs (never "No playlists yet")."""
    return rx.box(
        *[
            rx.box(
                rx.box(class_name="skeleton hist-skel-art"),
                rx.box(
                    rx.box(class_name="skeleton hist-skel-line"),
                    rx.box(class_name="skeleton hist-skel-line hist-skel-short"),
                    class_name="hist-skel-body",
                ),
                class_name="card hist-skel-card",
            )
            for _ in range(count)
        ],
        class_name="pl-grid hist-skeleton",
        aria_hidden="true",
        style={"width": "100%"},
    )


def _load_error() -> rx.Component:
    """A failed load, in the playlist area: the cause and Retry."""
    return rx.box(
        rx.box(rx.icon("circle-alert", size=16), class_name="c-ico", aria_hidden="true"),
        rx.box(
            rx.box("Couldn't load your playlists", class_name="c-title"),
            rx.box(HistoryState.error_message, class_name="c-body"),
            rx.el.button(
                rx.icon("refresh-cw", size=14, aria_hidden="true"),
                "Retry",
                class_name="btn btn-sm btn-soft hist-error-action",
                on_click=HistoryState.retry_load,
                type="button",
            ),
        ),
        class_name="callout callout-error",
    )


def _notice() -> rx.Component:
    """A message above the list, e.g. a deep-linked playlist that no longer exists."""
    return rx.box(
        rx.box(rx.icon("info", size=16), class_name="c-ico", aria_hidden="true"),
        rx.box(
            rx.box(HistoryState.error_message, class_name="c-body"),
            rx.el.button(
                "Dismiss",
                class_name="btn btn-sm btn-ghost hist-error-action",
                on_click=HistoryState.dismiss_error,
                type="button",
            ),
        ),
        class_name="callout callout-warning",
    )


def _error_region() -> rx.Component:
    """Persistent alert region: a load failure (with Retry) or a notice above the list."""
    return rx.el.div(
        rx.cond(
            HistoryState.error_message != "",
            rx.cond(HistoryState.load_failed, _load_error(), _notice()),
            rx.fragment(),
        ),
        role="alert",
        id="hist-error-region",
        style={"width": "100%"},
    )


def _no_playlists() -> rx.Component:
    """Empty after a completed load: nothing saved yet."""
    return rx.box(
        rx.box(rx.icon("inbox", size=22, color="var(--fg-3)"), class_name="e-ico"),
        rx.box("No playlists yet", class_name="e-title"),
        rx.box(
            "Describe a vibe in the Generator and save the result to see it here.",
            class_name="e-desc",
        ),
        rx.el.a(
            rx.icon("sparkles", size=16, aria_hidden="true"),
            "Open Generator",
            href="/generator",
            class_name="btn btn-3 btn-primary hist-empty-action",
        ),
        class_name="empty",
        style={"width": "100%"},
    )


def _no_matches() -> rx.Component:
    """Playlists exist but none match the search: offer to clear it."""
    return rx.box(
        rx.box(rx.icon("search-x", size=22, color="var(--fg-3)"), class_name="e-ico"),
        rx.box("No playlists match your search", class_name="e-title"),
        rx.box(
            "No playlist name or prompt contains “",
            HistoryState.search_query,
            "”.",
            class_name="e-desc",
        ),
        rx.el.button(
            rx.icon("x", size=16, aria_hidden="true"),
            "Clear search",
            class_name="btn btn-3 btn-soft hist-empty-action",
            on_click=HistoryState.clear_search,
            type="button",
        ),
        class_name="empty",
        style={"width": "100%"},
    )


def _playlist_area() -> rx.Component:
    """The grid, or why there is none: loading, failed, nothing saved, no match.

    A revisit keeps showing the last real list while it reloads; the skeleton is
    only shown while nothing is listed yet. "No playlists yet" appears only
    after a completed load with no playlists.
    """
    return rx.cond(
        HistoryState.load_failed,
        rx.fragment(),  # the error callout (with Retry) is in the alert region above
        rx.cond(
            HistoryState.filtered_playlists.length() > 0,
            rx.el.ul(
                rx.foreach(HistoryState.filtered_playlists, _pl_card),
                class_name="pl-grid",
                role="list",
                style={"width": "100%"},
            ),
            rx.cond(
                HistoryState.show_loading,
                _skeleton_cards(),
                rx.cond(HistoryState.has_playlists, _no_matches(), _no_playlists()),
            ),
        ),
    )


def history() -> rx.Component:
    content = rx.vstack(
        _filter_bar(),
        # Says "Loading playlists…" to assistive tech while the placeholder shows.
        rx.el.div(
            rx.cond(HistoryState.show_loading, "Loading playlists…", ""),
            role="status",
            id="hist-load-status",
            class_name="sr-only",
        ),
        _error_region(),
        _playlist_area(),
        _detail_dialog(),
        _delete_confirm_dialog(),
        spacing="4",
        width="100%",
        on_mount=HistoryState.on_load,
    )
    return layout(content)
