"""Reusable stat tile component used on dashboard and doctor pages."""

import reflex as rx


def stat_tile(
    label: str,
    value,  # type: ignore[no-untyped-def]
    icon_name: str,
    icon_color: str = "accent.9",
    icon_bg: str = "accent.3",
    stagger: str = "",
) -> rx.Component:
    """Single metric tile -- large mono number, muted label, tinted icon."""
    return rx.hstack(
        rx.box(
            rx.icon(icon_name, size=20, color=icon_color),
            padding="8px",
            border_radius="var(--radius-md)",
            background_color=icon_bg,
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(
                value,
                size="6",
                weight="bold",
                style={"fontFamily": "var(--font-mono)"},
            ),
            rx.text(label, size="1", color="gray.9", weight="medium"),
            spacing="0",
            align="start",
        ),
        spacing="3",
        align="center",
        class_name=f"animate-fade-in-up {stagger}".strip(),
    )
