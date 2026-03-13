import reflex as rx
from typing import Optional


def loading_spinner(size: int = 24, color: str = "purple") -> rx.Component:
    """Simple loading spinner."""
    return rx.icon(
        "loader-2",
        size=size,
        color=f"{color}.500",
        class_name="animate-spin",
    )


def skeleton_line(width: str = "100%", height: str = "20px") -> rx.Component:
    """Single skeleton line for loading states.

    Uses the design-system shimmer animation via the 'skeleton' CSS class.
    """
    return rx.box(
        width=width,
        height=height,
        border_radius="var(--radius-md)",
        class_name="skeleton",
    )


def skeleton_card() -> rx.Component:
    """Skeleton card for loading playlist/track cards."""
    return rx.card(
        rx.vstack(
            skeleton_line(height="150px"),
            skeleton_line(width="80%", height="24px"),
            skeleton_line(width="60%", height="16px"),
            rx.hstack(
                skeleton_line(width="60px", height="16px"),
                skeleton_line(width="80px", height="16px"),
                spacing="2",
            ),
            spacing="3",
            padding="4",
            width="100%",
        ),
        width="100%",
    )


def skeleton_table_row() -> rx.Component:
    """Skeleton row for table loading states."""
    return rx.table.row(
        rx.table.cell(skeleton_line(width="40px", height="16px")),
        rx.table.cell(skeleton_line(width="200px", height="16px")),
        rx.table.cell(skeleton_line(width="150px", height="16px")),
        rx.table.cell(skeleton_line(width="150px", height="16px")),
        rx.table.cell(skeleton_line(width="80px", height="16px")),
    )


def skeleton_table(rows: int = 5) -> rx.Component:
    """Skeleton table for loading states."""
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell(""),
                rx.table.column_header_cell(""),
                rx.table.column_header_cell(""),
                rx.table.column_header_cell(""),
                rx.table.column_header_cell(""),
            )
        ),
        rx.table.body(*[skeleton_table_row() for _ in range(rows)]),
        variant="surface",
        size="2",
        width="100%",
    )


def loading_overlay(message: Optional[str] = None) -> rx.Component:
    """Full-screen loading overlay for page transitions."""
    return rx.box(
        rx.vstack(
            loading_spinner(size=48),
            rx.cond(
                message,
                rx.text(
                    message,
                    size="4",
                    color="var(--pm-gray-11)",
                    weight="medium",
                ),
                rx.box(),
            ),
            spacing="4",
            align="center",
            justify="center",
        ),
        position="fixed",
        top="0",
        left="0",
        right="0",
        bottom="0",
        z_index="1000",
        display="flex",
        align_items="center",
        justify_content="center",
        class_name="glass",
    )


def button_with_loading(
    text: str,
    loading: bool,
    on_click: callable,
    **kwargs,
) -> rx.Component:
    """Button that shows loading state."""
    return rx.button(
        rx.cond(
            loading,
            rx.hstack(
                loading_spinner(size=16, color="white"),
                rx.text(text),
                spacing="2",
                align="center",
            ),
            rx.text(text),
        ),
        on_click=on_click,
        disabled=loading,
        **kwargs,
    )
