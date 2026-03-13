import reflex as rx
from typing import Optional


def error_message(
    message: str,
    title: Optional[str] = "Error",
    dismissible: bool = True,
    on_dismiss: Optional[callable] = None,
) -> rx.Component:
    """Display an error message using a Radix callout with red color scheme."""
    return rx.callout.root(
        rx.hstack(
            rx.callout.icon(rx.icon("triangle_alert", size=20)),
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
                    color_scheme="red",
                    title="Dismiss",
                ),
                rx.box(),
            ),
            width="100%",
            align="start",
        ),
        color_scheme="red",
        variant="surface",
        width="100%",
        class_name="animate-fade-in",
    )


def error_boundary(
    content: rx.Component,
    fallback_message: str = "Something went wrong. Please try again.",
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
        color="var(--pm-error)",
        size="2",
        margin_top="1",
    )


def retry_component(
    message: str,
    on_retry: callable,
    is_retrying: bool = False,
) -> rx.Component:
    """Component with retry functionality."""
    return rx.vstack(
        rx.icon(
            "triangle_alert",
            size=48,
            color="var(--pm-warning)",
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
            class_name="pm-button",
        ),
        spacing="4",
        align="center",
        padding="8",
        class_name="animate-fade-in-up",
    )


def empty_state(
    icon: str,
    title: str,
    description: str,
    action_text: Optional[str] = None,
    on_action: Optional[callable] = None,
) -> rx.Component:
    """Empty state component for when no data is available."""
    return rx.vstack(
        rx.icon(icon, size=48, color="var(--pm-gray-9)"),
        rx.text(
            title,
            size="5",
            weight="bold",
        ),
        rx.text(
            description,
            size="3",
            color="var(--pm-gray-9)",
            text_align="center",
            max_width="400px",
        ),
        rx.cond(
            action_text,
            rx.button(
                action_text,
                on_click=on_action,
                color_scheme="orange",
                size="3",
                class_name="pm-button",
            ),
            rx.box(),
        ),
        spacing="4",
        align="center",
        justify="center",
        min_height="300px",
        class_name="animate-fade-in-up",
    )
