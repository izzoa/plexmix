import reflex as rx
from typing import Optional


def error_message(
    message: str,
    title: Optional[str] = "Error",
    dismissible: bool = True,
    on_dismiss: Optional[callable] = None
) -> rx.Component:
    """Display an error message."""
    return rx.callout.root(
        rx.hstack(
            rx.callout.icon(
                rx.icon("triangle_alert", size=20)
            ),
            rx.vstack(
                rx.callout.text(
                    title,
                    weight="bold",
                ),
                rx.callout.text(message),
                spacing="1",
            ),
            rx.spacer(),
            rx.cond(
                dismissible,
                rx.button(
                    rx.icon("x", size=16),
                    on_click=on_dismiss,
                    variant="ghost",
                    size="1",
                ),
                rx.box(),
            ),
            width="100%",
            align="start",
        ),
        color="red",
        variant="surface",
        width="100%",
    )


def error_boundary(
    content: rx.Component,
    fallback_message: str = "Something went wrong. Please try again."
) -> rx.Component:
    """Wrap content with error boundary."""
    return rx.fragment(
        content,
        # Note: Reflex doesn't have built-in error boundaries yet,
        # but we can use this pattern for consistency
    )


def inline_error(message: str) -> rx.Component:
    """Inline error message for form validation."""
    return rx.text(
        message,
        color="red.500",
        size="2",
        margin_top="1",
    )


def retry_component(
    message: str,
    on_retry: callable,
    is_retrying: bool = False
) -> rx.Component:
    """Component with retry functionality."""
    return rx.vstack(
        rx.icon(
            "triangle_alert",
            size=48,
            color="orange.500",
        ),
        rx.text(
            message,
            size="4",
            text_align="center",
        ),
        rx.button(
            rx.cond(
                is_retrying,
                rx.hstack(
                            rx.icon("loader_2", size=16, class_name="animate-spin"),
                    rx.text("Retrying..."),
                    spacing="2",
                ),
                rx.hstack(
                    rx.icon("refresh_cw", size=16),
                    rx.text("Retry"),
                    spacing="2",
                ),
            ),
            on_click=on_retry,
            disabled=is_retrying,
            variant="soft",
            size="3",
        ),
        spacing="4",
        align="center",
        padding="8",
    )


def empty_state(
    icon: str,
    title: str,
    description: str,
    action_text: Optional[str] = None,
    on_action: Optional[callable] = None
) -> rx.Component:
    """Empty state component for when no data is available."""
    return rx.vstack(
        rx.icon(icon, size=64, color="gray.400"),
        rx.text(
            title,
            size="5",
            weight="bold",
            color_scheme="gray",
        ),
        rx.text(
            description,
            size="3",
            color_scheme="gray",
            text_align="center",
        ),
        rx.cond(
            action_text,
            rx.button(
                action_text,
                on_click=on_action,
                color_scheme="orange",
                size="3",
            ),
            rx.box(),
        ),
        spacing="4",
        align="center",
        justify="center",
        min_height="300px",
    )