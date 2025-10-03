import reflex as rx
from typing import Literal


ToastType = Literal["success", "error", "warning", "info"]


def toast_notification(
    message: str,
    toast_type: ToastType = "info",
    duration: int = 3000
) -> rx.Component:
    color_scheme_map = {
        "success": "green",
        "error": "red",
        "warning": "orange",
        "info": "blue",
    }

    icon_map = {
        "success": "✓",
        "error": "✗",
        "warning": "⚠",
        "info": "ⓘ",
    }

    color_scheme = color_scheme_map.get(toast_type, "blue")
    icon = icon_map.get(toast_type, "ⓘ")

    return rx.callout(
        rx.text(icon, size="5", margin_right="2"),
        rx.text(message, size="3"),
        color_scheme=color_scheme,
        role="alert",
    )
