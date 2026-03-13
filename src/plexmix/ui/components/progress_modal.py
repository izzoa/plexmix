import reflex as rx
from typing import Optional, Callable


def progress_modal(
    is_open: bool,
    progress: int,
    message: str,
    on_cancel: Optional[Callable[[], None]] = None,
) -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.dialog.title(
                    "Operation in Progress",
                    size="5",
                    weight="bold",
                ),
                rx.text(
                    message,
                    size="3",
                    color="var(--pm-gray-11)",
                ),
                rx.vstack(
                    rx.progress(
                        value=progress,
                        max=100,
                        width="100%",
                    ),
                    rx.text(
                        f"{progress}%",
                        size="3",
                        weight="medium",
                        font_family="var(--font-mono)",
                        color="var(--pm-gray-11)",
                    ),
                    spacing="2",
                    width="100%",
                    align="center",
                ),
                rx.dialog.close(
                    rx.button(
                        "Cancel",
                        on_click=on_cancel if on_cancel else lambda: None,
                        variant="soft",
                        color_scheme="red",
                        size="2",
                        class_name="pm-button",
                    )
                )
                if on_cancel
                else rx.box(),
                spacing="4",
                width=rx.breakpoints(initial="90vw", sm="420px"),
                padding="4",
            ),
            class_name="animate-scale-in",
        ),
        open=is_open,
    )
