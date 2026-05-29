"""Shared error classification and typed errors for AI providers.

Providers wrap different SDKs (google-genai, openai, anthropic, cohere, httpx)
whose exceptions have different shapes, so classification works on the
*stringified* exception — the common denominator across all of them.
"""

from typing import Any, Dict, Optional, Tuple

# Human-readable messages surfaced to users. Kept concise and actionable.
_RETRYABLE_MSG = "Temporary AI provider error (timeout, rate limit, or server error)."
_AUTH_MSG = (
    "The AI provider rejected your credentials (authentication failed). "
    "Re-check your API key in Settings."
)
_EDGE_MSG = (
    "The request was rejected before it reached the model — an HTTP edge or proxy "
    "returned an error page instead of the provider's API. This usually means the "
    "API key is malformed or invalid, or a network proxy/firewall is interfering. "
    "Re-check your API key in Settings (and any corporate proxy/VPN)."
)
_FATAL_MSG = (
    "The AI provider rejected the request and it cannot be retried. "
    "Check your API key, model name, and provider settings."
)


class FatalProviderError(Exception):
    """A non-recoverable provider error that should abort the current run.

    Carries a user-facing message, the original cause, and any results produced
    before the failure so callers can persist partial progress.
    """

    def __init__(
        self,
        message: str,
        user_message: Optional[str] = None,
        cause: Optional[BaseException] = None,
        partial_results: Optional[Dict[Any, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or message
        self.cause = cause
        self.partial_results: Dict[Any, Any] = partial_results or {}


def _looks_like_html(text: str) -> bool:
    """True if the error body looks like an HTML page rather than a JSON API error."""
    low = text.lower()
    return "<html" in low or "<!doctype html" in low


def classify_provider_error(exc: BaseException) -> Tuple[bool, bool, str]:
    """Classify a provider exception.

    Returns ``(is_retryable, is_fatal, user_message)``. An error that is neither
    retryable nor fatal is unknown — callers should degrade gracefully (e.g. skip
    the current batch) rather than abort the whole run.
    """
    s = str(exc)
    low = s.lower()

    # 1) Retryable transient errors (checked first).
    if (
        "timeout" in low
        or "timed out" in low
        or "429" in s
        or "quota" in low
        or "rate limit" in low
        or "rate_limit" in low
        or "ratelimit" in low
        or "408" in s
        or "500" in s
        or "502" in s
        or "503" in s
        or "504" in s
    ):
        return (True, False, _RETRYABLE_MSG)

    # 2) An HTML body on an error => rejected at an HTTP edge/intermediary (fatal).
    if _looks_like_html(s):
        return (False, True, _EDGE_MSG)

    # 3) Authentication / authorization failures (fatal).
    if (
        "401" in s
        or "403" in s
        or "unauthorized" in low
        or "forbidden" in low
        or "api key" in low
        or "api_key" in low
        or "api_key_invalid" in low
        or "invalid_api_key" in low
        or "permission" in low
    ):
        return (False, True, _AUTH_MSG)

    # 4) Other fatal client errors (400/404/409/422 and similar).
    if (
        "400" in s
        or "404" in s
        or "409" in s
        or "422" in s
        or "invalid_argument" in low
        or "bad request" in low
        or "not found" in low
    ):
        return (False, True, _FATAL_MSG)

    # 5) Unknown — let the caller degrade gracefully.
    return (False, False, s)
