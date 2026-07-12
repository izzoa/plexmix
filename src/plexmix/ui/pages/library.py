"""Library — synced tracks: filters, table, sync/embed/enrich jobs, bulk actions."""

import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.app_state import AppState
from plexmix.ui.states.library_state import LibraryState


def _job_bar(active, message, progress, job_type: str) -> rx.Component:
    return rx.cond(
        active,
        rx.box(
            rx.box(
                rx.el.span(message, style={"fontSize": "13px", "fontWeight": "500"}),
                rx.spacer(),
                rx.el.span(
                    progress.to_string() + "%",
                    class_name="mono fg3",
                    style={"fontSize": "13px"},
                ),
                rx.el.button(
                    rx.icon("x", size=14),
                    "Cancel",
                    class_name="btn btn-sm btn-ghost",
                    on_click=AppState.request_cancel(job_type),
                    type="button",
                ),
                style={
                    "display": "flex",
                    "alignItems": "center",
                    "gap": "12px",
                    "marginBottom": "8px",
                },
            ),
            rx.box(
                rx.box(class_name="pfill", style={"width": progress.to_string() + "%"}),
                class_name="pbar",
            ),
            class_name="card",
            style={"padding": "14px 16px", "width": "100%"},
        ),
        rx.fragment(),
    )


def _action_bar() -> rx.Component:
    return rx.box(
        rx.el.select(
            rx.el.option("Incremental sync", value="incremental"),
            rx.el.option("Full sync", value="full"),
            rx.el.option("Regenerate (clear tags + embeddings)", value="regenerate"),
            value=LibraryState.sync_mode,
            on_change=LibraryState.set_sync_mode,
            class_name="minisel",
        ),
        rx.el.button(
            rx.icon("refresh-cw", size=16),
            "Sync",
            class_name="btn btn-3 btn-primary",
            # Route through a dispatcher so the destructive 'regenerate' mode is
            # confirmed first (and is actually reachable).
            on_click=LibraryState.start_sync_or_confirm,
            disabled=LibraryState.is_syncing,
            type="button",
        ),
        rx.el.button(
            rx.icon("cpu", size=16),
            "Generate embeddings",
            class_name="btn btn-3 btn-soft",
            on_click=LibraryState.generate_embeddings,
            disabled=LibraryState.is_embedding,
            type="button",
        ),
        rx.el.button(
            rx.icon("disc", size=16),
            "Enrich MusicBrainz",
            class_name="btn btn-3 btn-soft",
            on_click=LibraryState.enrich_musicbrainz,
            disabled=LibraryState.is_enriching_musicbrainz,
            type="button",
        ),
        class_name="card",
        style={
            "display": "flex",
            "alignItems": "center",
            "gap": "10px",
            "padding": "14px 16px",
            "width": "100%",
            "flexWrap": "wrap",
        },
    )


def _filter_bar() -> rx.Component:
    return rx.box(
        rx.box(
            rx.icon("search", size=15, color="var(--fg-3)"),
            rx.el.input(
                placeholder="Search tracks…",
                value=LibraryState.search_query,
                on_change=LibraryState.set_search_query,
            ),
            class_name="search",
        ),
        rx.el.input(
            placeholder="Genre",
            value=LibraryState.genre_filter,
            on_change=LibraryState.set_genre_filter,
            class_name="minisel",
            style={"maxWidth": "160px"},
        ),
        rx.el.button(
            "Clear",
            class_name="btn btn-3 btn-ghost",
            on_click=LibraryState.clear_filters,
            type="button",
        ),
        class_name="filterbar",
    )


def _bulk_bar() -> rx.Component:
    return rx.cond(
        LibraryState.selected_tracks.length() > 0,
        rx.box(
            rx.el.span(
                LibraryState.selected_tracks.length(),
                " selected",
                style={"fontWeight": "600", "fontSize": "14px"},
            ),
            rx.spacer(),
            rx.el.button(
                rx.icon("tags", size=15),
                "Tag",
                class_name="btn btn-sm btn-soft",
                on_click=LibraryState.open_bulk_tag_dialog,
                type="button",
            ),
            rx.el.button(
                rx.icon("trash-2", size=15),
                "Delete",
                class_name="btn btn-sm btn-soft",
                on_click=LibraryState.open_delete_confirm,
                type="button",
            ),
            rx.el.button(
                "Clear",
                class_name="btn btn-sm btn-ghost",
                on_click=LibraryState.clear_selection,
                type="button",
            ),
            class_name="card",
            style={
                "display": "flex",
                "alignItems": "center",
                "gap": "10px",
                "padding": "10px 16px",
                "width": "100%",
            },
        ),
        rx.fragment(),
    )


def _bulk_tag_dialog() -> rx.Component:
    """Confirm dialog for applying tags to the selected tracks (previously the
    'Tag' button set a flag but no dialog was rendered, so nothing happened)."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Tag selected tracks"),
            rx.dialog.description(
                "Comma-separated tags to apply to the selected tracks.",
                size="2",
            ),
            rx.el.input(
                placeholder="e.g. chill, focus, instrumental",
                value=LibraryState.bulk_tag_input,
                on_change=LibraryState.set_bulk_tag_input,
                class_name="input",
                style={"width": "100%", "marginTop": "12px"},
            ),
            rx.hstack(
                rx.dialog.close(
                    rx.el.button("Cancel", class_name="btn btn-soft", type="button"),
                ),
                rx.el.button(
                    "Apply tags",
                    class_name="btn btn-soft",
                    on_click=LibraryState.apply_bulk_tags,
                    type="button",
                ),
                spacing="2",
                margin_top="16px",
                justify="end",
            ),
            max_width="440px",
        ),
        open=LibraryState.show_bulk_tag_dialog,
        on_open_change=LibraryState.set_bulk_tag_dialog_open,
    )


def _regenerate_confirm_dialog() -> rx.Component:
    """Confirm dialog for the destructive regenerate sync (clears all tags +
    embeddings). Wiring this makes the regenerate mode reachable."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Regenerate all tags & embeddings?"),
            rx.dialog.description(
                "This clears every AI tag and embedding and regenerates them from "
                "scratch during the sync. It can take a while and re-incurs AI API "
                "costs. Your Plex library is not affected.",
                size="2",
            ),
            rx.hstack(
                rx.dialog.close(
                    rx.el.button(
                        "Cancel",
                        class_name="btn btn-soft",
                        on_click=LibraryState.cancel_regenerate_confirm,
                        type="button",
                    ),
                ),
                rx.el.button(
                    "Regenerate",
                    class_name="btn btn-soft",
                    on_click=LibraryState.start_sync,
                    type="button",
                ),
                spacing="2",
                margin_top="16px",
                justify="end",
            ),
            max_width="480px",
        ),
        open=LibraryState.show_regenerate_confirm,
        on_open_change=LibraryState.set_regenerate_confirm_open,
    )


def _delete_confirm_dialog() -> rx.Component:
    """Confirm dialog for deleting the selected tracks (previously unrendered)."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Delete selected tracks?"),
            rx.dialog.description(
                "This removes the selected tracks and their tags/embeddings from the "
                "PlexMix database. Your Plex library is not affected.",
                size="2",
            ),
            rx.hstack(
                rx.dialog.close(
                    rx.el.button("Cancel", class_name="btn btn-soft", type="button"),
                ),
                rx.el.button(
                    "Delete",
                    class_name="btn btn-soft",
                    on_click=LibraryState.delete_selected_tracks,
                    type="button",
                ),
                spacing="2",
                margin_top="16px",
                justify="end",
            ),
            max_width="440px",
        ),
        open=LibraryState.show_delete_confirm,
        on_open_change=LibraryState.set_delete_confirm_open,
    )


def _track_row(track: rx.Var) -> rx.Component:
    tid = track["id"]
    return rx.el.tr(
        rx.el.td(
            rx.checkbox(
                checked=LibraryState.selected_tracks.contains(tid),
                on_change=lambda _v: LibraryState.toggle_track_selection(tid),
            ),
            style={"width": "40px"},
        ),
        rx.el.td(track["title"], style={"fontWeight": "500"}),
        rx.el.td(track["artist_name"], class_name="fg2"),
        rx.el.td(track["album_title"], class_name="fg3"),
    )


def _track_table() -> rx.Component:
    return rx.box(
        rx.el.table(
            rx.el.thead(
                rx.el.tr(
                    rx.el.th(
                        rx.checkbox(
                            checked=LibraryState.all_page_selected,
                            on_change=LibraryState.toggle_select_all,
                        ),
                    ),
                    rx.el.th("Title"),
                    rx.el.th("Artist"),
                    rx.el.th("Album"),
                )
            ),
            rx.el.tbody(rx.foreach(LibraryState.tracks, _track_row)),
            class_name="tbl",
        ),
        class_name="tbl-wrap",
        style={"width": "100%"},
    )


def _pagination() -> rx.Component:
    return rx.box(
        rx.el.span(
            LibraryState.total_filtered_tracks,
            " tracks",
            class_name="fg3",
            style={"fontSize": "13px"},
        ),
        rx.spacer(),
        rx.el.button(
            rx.icon("chevron-left", size=16),
            class_name="btn btn-sm btn-soft",
            on_click=LibraryState.previous_page,
            type="button",
        ),
        rx.el.span(
            "Page ",
            LibraryState.current_page,
            class_name="mono",
            style={"fontSize": "13px", "padding": "0 8px"},
        ),
        rx.el.button(
            rx.icon("chevron-right", size=16),
            class_name="btn btn-sm btn-soft",
            on_click=LibraryState.next_page,
            type="button",
        ),
        style={"display": "flex", "alignItems": "center", "gap": "6px", "width": "100%"},
    )


def library() -> rx.Component:
    content = rx.vstack(
        # Hidden poller: drives poll_task_progress every 2s so long-running job
        # progress updates live (on_load alone only polled once, so bars froze).
        rx.box(
            rx.moment(interval=2000, on_change=LibraryState.poll_task_progress),
            display="none",
        ),
        _action_bar(),
        _job_bar(
            LibraryState.is_syncing,
            LibraryState.sync_message,
            LibraryState.sync_progress,
            "sync",
        ),
        _job_bar(
            LibraryState.is_embedding,
            LibraryState.embedding_message,
            LibraryState.embedding_progress,
            "embedding",
        ),
        _job_bar(
            LibraryState.is_enriching_musicbrainz,
            LibraryState.musicbrainz_message,
            LibraryState.musicbrainz_progress,
            "musicbrainz",
        ),
        _job_bar(
            LibraryState.is_analyzing_audio,
            LibraryState.audio_analysis_message,
            LibraryState.audio_analysis_progress,
            "audio",
        ),
        _filter_bar(),
        _bulk_bar(),
        _bulk_tag_dialog(),
        _delete_confirm_dialog(),
        _regenerate_confirm_dialog(),
        rx.cond(
            LibraryState.tracks.length() > 0,
            _track_table(),
            rx.box(
                rx.box(rx.icon("music", size=22, color="var(--fg-3)"), class_name="e-ico"),
                rx.box("No tracks", class_name="e-title"),
                rx.box("Sync your Plex library to populate tracks.", class_name="e-desc"),
                class_name="empty",
                style={"width": "100%"},
            ),
        ),
        _pagination(),
        spacing="4",
        width="100%",
        on_mount=LibraryState.on_load,
    )
    return layout(content)
