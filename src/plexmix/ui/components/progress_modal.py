import reflex as rx
from typing import Optional, Callable


def progress_modal(
    is_open: bool,
    progress: int,
    message: str,
    on_cancel: Optional[Callable[[], None]] = None
) -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.dialog.title("Operation in Progress"),
                rx.text(message, size="3", margin_bottom="4"),
                rx.progress(value=progress, max=100, width="100%"),
                rx.text(f"{progress}%", size="2", color_scheme="gray"),
                rx.dialog.close(
                    rx.button(
                        "Cancel",
                        on_click=on_cancel if on_cancel else lambda: None,
                        variant="soft",
                        color_scheme="red",
                    )
                ) if on_cancel else rx.box(),
                spacing="3",
                width="400px",
            )
        ),
        open=is_open,
    )
