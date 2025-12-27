import reflex as rx
from typing import List, Dict, Any, Callable


def track_table_header() -> rx.Component:
    return rx.table.header(
        rx.table.row(
            rx.table.column_header_cell(""),
            rx.table.column_header_cell("Title"),
            rx.table.column_header_cell("Artist"),
            rx.table.column_header_cell("Album"),
            rx.table.column_header_cell("Genre"),
            rx.table.column_header_cell("Year"),
            rx.table.column_header_cell("Tags"),
            rx.table.column_header_cell("Embedded"),
        )
    )


def track_table_row(track: Dict[str, Any], is_selected: bool, on_toggle: Callable) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.checkbox(
                checked=is_selected,
                on_change=on_toggle,
            )
        ),
        rx.table.cell(track["title"]),
        rx.table.cell(track["artist_name"]),
        rx.table.cell(track["album_title"]),
        rx.table.cell(rx.cond(track["genre"], track["genre"], "-")),
        rx.table.cell(rx.cond(track["year"], track["year"], "-")),
        rx.table.cell(rx.cond(track["tags"], track["tags"], "-")),
        rx.table.cell(
            rx.cond(
                track["has_embedding"],
                rx.badge("Yes", color_scheme="green"),
                rx.badge("No", color_scheme="gray")
            )
        ),
    )


def track_table(tracks: List[Dict[str, Any]], selected_tracks: List[int], on_toggle_selection: Callable) -> rx.Component:
    return rx.table.root(
        track_table_header(),
        rx.table.body(
            rx.foreach(
                tracks,
                lambda track: track_table_row(
                    track,
                    selected_tracks.contains(track["id"]),
                    lambda _checked=None: on_toggle_selection(track["id"])
                )
            )
        ),
        variant="surface",
        size="3",
        width="100%",
    )
