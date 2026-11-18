"""Reusable stat tile component used on dashboard and doctor pages."""

import reflex as rx


def stat_tile(
    label: str,
    value,  # type: ignore[no-untyped-def]
    icon_name: str,
    icon_color: str = "var(--accent-fg)",
    icon_bg: str = "var(--brand-3)",
    stagger: str = "",
) -> rx.Component:
    """Single metric tile -- large mono number, muted label, tinted icon.

    ``icon_color``/``icon_bg`` are CSS colors. On a tint chip use a text-safe
    ``--*-fg`` token for the icon, never the semantic solid (fills only).
    """
    return rx.hstack(
        rx.box(
            rx.icon(icon_name, size=20, color=icon_color),
            padding="8px",
            border_radius="50%",
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
            rx.text(label, size="1", color="var(--fg-3)", weight="medium"),
            spacing="0",
            align="start",
        ),
        spacing="3",
        align="center",
        class_name=f"animate-fade-in-up {stagger}".strip(),
    )
