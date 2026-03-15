"""Reusable form field components for Reflex UI pages."""

import reflex as rx


def form_field(label: str, child: rx.Component) -> rx.Component:
    """Labeled form field: a label above an input component.

    Usage::

        form_field("Genre", rx.input(
            placeholder="e.g., rock, jazz",
            value=SomeState.genre_filter,
            on_change=SomeState.set_genre_filter,
            width="100%",
        ))
    """
    return rx.vstack(
        rx.text(label, size="2", weight="medium", color="gray.11"),
        child,
        spacing="1",
        width="100%",
    )


def year_range_field(
    label: str,
    min_value: rx.Var,
    on_min_change: rx.EventHandler,
    max_value: rx.Var,
    on_max_change: rx.EventHandler,
) -> rx.Component:
    """Labeled year range picker (Min – Max)."""
    return rx.vstack(
        rx.text(label, size="2", weight="medium", color="gray.11"),
        rx.hstack(
            rx.input(
                placeholder="Min",
                type="number",
                value=min_value,
                on_change=on_min_change,
                width="100%",
            ),
            rx.text("-", size="3", color="gray.9"),
            rx.input(
                placeholder="Max",
                type="number",
                value=max_value,
                on_change=on_max_change,
                width="100%",
            ),
            spacing="2",
            align="center",
            width="100%",
        ),
        spacing="1",
        width="100%",
    )


def help_text(text: str) -> rx.Component:
    """Muted helper text below a form field."""
    return rx.text(text, size="1", color="gray.9")
