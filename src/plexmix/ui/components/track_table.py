import reflex as rx
from typing import Callable, Optional


def tag_badges(tags_str) -> rx.Component:
    """Render a comma-separated tag string as colored badge pills."""
    return rx.cond(
        tags_str,
        rx.flex(
            rx.foreach(
                tags_str.split(","),
                lambda tag: rx.cond(
                    tag.strip() != "",
                    rx.badge(tag.strip(), variant="soft", size="1"),
                    rx.fragment(),
                ),
            ),
            wrap="wrap",
            gap="1",
        ),
        rx.text("-", size="2", color="gray"),
    )


def _sort_indicator(sort_column: rx.Var, sort_ascending: rx.Var, column: str) -> rx.Component:
    """Show an arrow indicator for the currently sorted column."""
    return rx.cond(
        sort_column == column,
        rx.cond(
            sort_ascending,
            rx.icon("chevron-up", size=14),
            rx.icon("chevron-down", size=14),
        ),
        rx.fragment(),
    )


def _sortable_header(
    label: str,
    column: str,
    sort_column: rx.Var,
    sort_ascending: rx.Var,
    on_sort: Callable,
    class_name: Optional[str] = None,
) -> rx.Component:
    """A clickable column header that triggers sorting."""
    cell_kwargs = {}
    if class_name:
        cell_kwargs["class_name"] = class_name
    return rx.table.column_header_cell(
        rx.hstack(
            rx.text(label),
            _sort_indicator(sort_column, sort_ascending, column),
            spacing="1",
            align="center",
            class_name="sortable-header",
            on_click=on_sort(column),
        ),
        **cell_kwargs,
    )


def track_table_header(
    sort_column: Optional[rx.Var] = None,
    sort_ascending: Optional[rx.Var] = None,
    on_sort: Optional[Callable] = None,
    all_selected: Optional[rx.Var] = None,
    on_toggle_all: Optional[Callable] = None,
) -> rx.Component:
    select_all_cell = rx.table.column_header_cell(
        rx.checkbox(
            checked=all_selected,
            on_change=on_toggle_all,
        )
    ) if all_selected is not None and on_toggle_all is not None else rx.table.column_header_cell("")

    if sort_column is not None and on_sort is not None:
        return rx.table.header(
            rx.table.row(
                select_all_cell,
                _sortable_header("Title", "title", sort_column, sort_ascending, on_sort),
                _sortable_header("Artist", "artist", sort_column, sort_ascending, on_sort),
                _sortable_header("Album", "album", sort_column, sort_ascending, on_sort),
                _sortable_header("Genre", "genre", sort_column, sort_ascending, on_sort, class_name="hide-mobile"),
                _sortable_header("Year", "year", sort_column, sort_ascending, on_sort, class_name="hide-mobile"),
                rx.table.column_header_cell("Tags"),
                rx.table.column_header_cell("Embedded", class_name="hide-mobile"),
            )
        )
    return rx.table.header(
        rx.table.row(
            select_all_cell,
            rx.table.column_header_cell("Title"),
            rx.table.column_header_cell("Artist"),
            rx.table.column_header_cell("Album"),
            rx.table.column_header_cell("Genre", class_name="hide-mobile"),
            rx.table.column_header_cell("Year", class_name="hide-mobile"),
            rx.table.column_header_cell("Tags"),
            rx.table.column_header_cell("Embedded", class_name="hide-mobile"),
        )
    )


def track_table_row(track: dict[str, str], is_selected: bool, on_toggle: Callable) -> rx.Component:
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
        rx.table.cell(rx.cond(track["genre"], track["genre"], "-"), class_name="hide-mobile"),
        rx.table.cell(rx.cond(track["year"], track["year"], "-"), class_name="hide-mobile"),
        rx.table.cell(tag_badges(track["tags"])),
        rx.table.cell(
            rx.cond(
                track["has_embedding"],
                rx.badge("Yes", color_scheme="green"),
                rx.badge("No", color_scheme="gray")
            ),
            class_name="hide-mobile",
        ),
    )


def track_table(
    tracks: list[dict[str, str]],
    selected_tracks: list[str],
    on_toggle_selection: Callable,
    sort_column: Optional[rx.Var] = None,
    sort_ascending: Optional[rx.Var] = None,
    on_sort: Optional[Callable] = None,
    all_selected: Optional[rx.Var] = None,
    on_toggle_all: Optional[Callable] = None,
) -> rx.Component:
    return rx.box(
        rx.table.root(
            track_table_header(sort_column, sort_ascending, on_sort, all_selected, on_toggle_all),
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
        ),
        overflow_x="auto",
        width="100%",
    )
