"""Shared utility functions for Reflex UI states."""


def str_dict(d: dict) -> dict[str, str]:
    """Convert a dict with mixed-type values to all-string values for Reflex."""
    return {k: ("" if v is None else str(v)) for k, v in d.items()}


def format_eta(seconds: float) -> str:
    """Format remaining seconds as a human-readable ETA string."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s remaining"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s remaining"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m remaining"
