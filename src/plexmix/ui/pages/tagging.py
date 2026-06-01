"""Tagging — batch AI tagging with preview, progress, and inline tag editing."""

import reflex as rx
from plexmix.ui.components.navbar import layout
from plexmix.ui.states.tagging_state import TaggingState


def _filter_bar() -> rx.Component:
    return rx.box(
        rx.box(
            rx.icon("search", size=15, color="var(--fg-3)"),
            rx.el.input(
                placeholder="Filter by artist…",
                value=TaggingState.artist_filter,
                on_change=TaggingState.set_artist_filter,
            ),
            class_name="search",
        ),
        rx.el.input(
            placeholder="Genre",
            value=TaggingState.genre_filter,
            on_change=TaggingState.set_genre_filter,
            class_name="minisel",
            style={"maxWidth": "160px"},
        ),
        rx.el.input(
            placeholder="Stale days",
            value=TaggingState.stale_days,
            on_change=TaggingState.set_stale_days,
            class_name="minisel",
            style={"maxWidth": "120px"},
        ),
        rx.el.label(
            rx.el.input(
                type="checkbox",
                checked=TaggingState.has_no_tags,
                on_change=lambda _v: TaggingState.toggle_has_no_tags(),
            ),
            "Untagged only",
            style={"display": "flex", "alignItems": "center", "gap": "7px", "fontSize": "13px"},
        ),
        rx.el.button(
            rx.icon("scan-search", size=15),
            "Preview",
            class_name="btn btn-3 btn-soft",
            on_click=TaggingState.preview_selection,
            type="button",
        ),
        class_name="filterbar",
    )


def _tagged_row(track: rx.Var) -> rx.Component:
    editing = TaggingState.editing_track_id == track["id"]
    return rx.box(
        rx.cond(
            editing,
            rx.box(
                rx.el.input(
                    value=TaggingState.edit_tags,
                    on_change=TaggingState.set_edit_tags,
                    class_name="input",
                    placeholder="moods (comma-separated)",
                ),
                rx.el.input(
                    value=TaggingState.edit_environments,
                    on_change=TaggingState.set_edit_environments,
                    class_name="input",
                    placeholder="environments",
                ),
                rx.el.input(
                    value=TaggingState.edit_instruments,
                    on_change=TaggingState.set_edit_instruments,
                    class_name="input",
                    placeholder="instruments",
                ),
                rx.el.button(
                    "Save",
                    class_name="btn btn-sm btn-primary",
                    on_click=TaggingState.save_tag_edit,
                    type="button",
                ),
                rx.el.button(
                    "Cancel",
                    class_name="btn btn-sm btn-ghost",
                    on_click=TaggingState.cancel_edit,
                    type="button",
                ),
                style={
                    "display": "flex",
                    "gap": "8px",
                    "flexWrap": "wrap",
                    "alignItems": "center",
                    "width": "100%",
                },
            ),
            rx.box(
                rx.box(
                    rx.box(track["title"], style={"fontWeight": "500", "fontSize": "14px"}),
                    rx.box(track["artist"], class_name="fg3", style={"fontSize": "13px"}),
                    style={"flex": "1", "minWidth": "0"},
                ),
                rx.el.span(track["tags"], class_name="fg2", style={"fontSize": "13px"}),
                rx.el.button(
                    rx.icon("pencil", size=14),
                    "Edit",
                    class_name="btn btn-sm btn-soft",
                    on_click=TaggingState.start_edit_tag(
                        track["id"],
                        track["tags"],
                        track["environments"],
                        track["instruments"],
                    ),
                    type="button",
                ),
                style={"display": "flex", "gap": "14px", "alignItems": "center", "width": "100%"},
            ),
        ),
        style={"padding": "12px 14px", "borderBottom": "1px solid var(--border-subtle)"},
    )


def tagging() -> rx.Component:
    content = rx.vstack(
        _filter_bar(),
        # Preview + start
        rx.box(
            rx.icon("sparkles", size=18, color="var(--brand-9)"),
            rx.box(
                rx.el.span(
                    TaggingState.preview_count,
                    style={"fontWeight": "700", "fontFamily": "var(--font-mono)"},
                ),
                " tracks match the current filter",
                style={"flex": "1", "fontSize": "14px"},
            ),
            rx.el.button(
                rx.icon("tags", size=16),
                "Tag tracks",
                class_name="btn btn-3 btn-primary glow",
                on_click=TaggingState.start_tagging,
                disabled=TaggingState.is_tagging,
                type="button",
            ),
            class_name="card",
            style={
                "display": "flex",
                "alignItems": "center",
                "gap": "14px",
                "padding": "16px 18px",
                "width": "100%",
            },
        ),
        # Batch progress
        rx.cond(
            TaggingState.is_tagging,
            rx.box(
                rx.box(
                    rx.el.span(
                        TaggingState.tagging_message,
                        style={"fontSize": "14px", "fontWeight": "500"},
                    ),
                    rx.spacer(),
                    rx.el.span(
                        "Batch ",
                        TaggingState.current_batch,
                        "/",
                        TaggingState.total_batches,
                        class_name="mono fg3",
                        style={"fontSize": "13px"},
                    ),
                    style={"display": "flex", "alignItems": "center", "marginBottom": "10px"},
                ),
                rx.box(
                    rx.box(
                        class_name="pfill",
                        style={"width": TaggingState.tagging_progress.to_string() + "%"},
                    ),
                    class_name="pbar",
                ),
                rx.box(
                    rx.el.span(
                        TaggingState.tags_generated_count, " tags generated", class_name="fg3"
                    ),
                    rx.spacer(),
                    rx.el.button(
                        "Cancel",
                        class_name="btn btn-sm btn-ghost",
                        on_click=TaggingState.cancel_tagging,
                        type="button",
                    ),
                    style={
                        "display": "flex",
                        "alignItems": "center",
                        "marginTop": "10px",
                        "fontSize": "13px",
                    },
                ),
                class_name="card",
                style={"padding": "16px 18px", "width": "100%"},
            ),
            rx.fragment(),
        ),
        # Recently tagged
        rx.cond(
            TaggingState.recently_tagged_tracks.length() > 0,
            rx.box(
                rx.box(
                    rx.el.h2("Recently tagged", style={"fontSize": "17px", "fontWeight": "700"}),
                    class_name="section-head",
                ),
                rx.box(
                    rx.foreach(TaggingState.recently_tagged_tracks, _tagged_row),
                    class_name="card",
                    style={"width": "100%", "overflow": "hidden"},
                ),
                style={"width": "100%"},
            ),
            rx.fragment(),
        ),
        spacing="4",
        width="100%",
        on_mount=TaggingState.on_load,
    )
    return layout(content)
