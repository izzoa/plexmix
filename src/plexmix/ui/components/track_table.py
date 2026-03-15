import reflex as rx
from typing import Callable, Optional


def tag_badges(tags_str) -> rx.Component:
    """Render a comma-separated tag string as colored badge pills.

    Shows at most 3 badges with a "+N" overflow indicator when there are more.
    """
    return rx.cond(
        tags_str,
        rx.flex(
            rx.foreach(
                tags_str.split(",").to(list[str])[:3],
                lambda tag: rx.cond(
                    tag.strip() != "",
                    rx.badge(tag.strip(), variant="soft", size="1"),
                    rx.fragment(),
                ),
            ),
            rx.cond(
                tags_str.split(",").length() > 3,
                rx.text(
                    rx.cond(
                        tags_str.split(",").length() > 3,
                        "+" + (tags_str.split(",").length() - 3).to(str),
                        "",
                    ),
                    size="1",
                    color="var(--pm-gray-9)",
                    weight="medium",
                ),
                rx.fragment(),
            ),
            wrap="wrap",
            gap="1",
            align="center",
        ),
        rx.text("-", size="2", color="var(--pm-gray-9)"),
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


def _embedding_dot(has_embedding) -> rx.Component:
    """Render a small dot indicator for embedding status.

    Green dot for embedded tracks, gray dot otherwise.
    """
    return rx.box(
        width="6px",
        height="6px",
        border_radius="50%",
        flex_shrink="0",
        background_color=rx.cond(
            has_embedding == "1",
            "var(--pm-success)",
            "var(--pm-gray-6)",
        ),
    )


def track_table_header(
    sort_column: Optional[rx.Var] = None,
    sort_ascending: Optional[rx.Var] = None,
    on_sort: Optional[Callable] = None,
    all_selected: Optional[rx.Var] = None,
    on_toggle_all: Optional[Callable] = None,
) -> rx.Component:
    select_all_cell = (
        rx.table.column_header_cell(
            rx.checkbox(
                checked=all_selected,
                on_change=on_toggle_all,
            )
        )
        if all_selected is not None and on_toggle_all is not None
        else rx.table.column_header_cell("")
    )

    if sort_column is not None and on_sort is not None:
        return rx.table.header(
            rx.table.row(
                select_all_cell,
                _sortable_header("Title", "title", sort_column, sort_ascending, on_sort),
                _sortable_header("Artist", "artist", sort_column, sort_ascending, on_sort),
                _sortable_header("Album", "album", sort_column, sort_ascending, on_sort),
                _sortable_header(
                    "Genre", "genre", sort_column, sort_ascending, on_sort, class_name="hide-mobile"
                ),
                _sortable_header(
                    "Year", "year", sort_column, sort_ascending, on_sort, class_name="hide-mobile"
                ),
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
        rx.table.cell(
            rx.text(track["title"], size="2", weight="medium", trim="both"),
        ),
        rx.table.cell(
            rx.text(track["artist_name"], size="2", trim="both"),
        ),
        rx.table.cell(
            rx.text(track["album_title"], size="2", trim="both"),
        ),
        rx.table.cell(
            rx.cond(
                track["genre"],
                rx.text(track["genre"], size="2"),
                rx.text("-", size="2", color="var(--pm-gray-9)"),
            ),
            class_name="hide-mobile",
        ),
        rx.table.cell(
            rx.cond(
                track["year"],
                rx.text(track["year"], size="2"),
                rx.text("-", size="2", color="var(--pm-gray-9)"),
            ),
            class_name="hide-mobile",
        ),
        rx.table.cell(tag_badges(track["tags"])),
        rx.table.cell(
            rx.flex(
                _embedding_dot(track["has_embedding"]),
                align="center",
                justify="center",
                height="100%",
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
                        lambda _checked=None: on_toggle_selection(track["id"]),
                    ),
                )
            ),
            variant="surface",
            size="3",
            width="100%",
        ),
        overflow_x="auto",
        width="100%",
    )
