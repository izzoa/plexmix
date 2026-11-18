"""Plain-language error text for the Web UI (no Reflex import).

``friendly_error`` turns a raw provider, SDK or network exception into one short
sentence a user can act on. It started in ``ui/states/_settings_testing.py`` (the
Settings connection tests, which still re-export it as ``_friendly_error``) and
lives here so services and readiness copy can share it without importing Reflex.
"""

import re
from typing import Iterable, Optional

INVALID_API_KEY_MESSAGE = "Invalid API key. Please check your key and try again."
INVALID_REQUEST_MESSAGE = (
    "The provider rejected the request as invalid. Check the model name and settings, "
    "then try again."
)

# Key rejections as providers phrase them: "Invalid API key", "Incorrect API key
# provided: ... invalid ... key", Gemini's 400 "API key not valid. Please pass a
# valid API key." with reason API_KEY_INVALID.
_INVALID_KEY_RE = re.compile(r"invalid.*key|api key not valid|api_key_invalid", re.I)
# Google's HTTP 400 with status INVALID_ARGUMENT ("400 INVALID_ARGUMENT. {...}").
_HTTP_400_RE = re.compile(r"\b400\b")
_INVALID_ARGUMENT_RE = re.compile(r"invalid_argument", re.I)
# Rate limiting as providers phrase it ("rate limit", "rate_limit_error", "Rate
# limited", "Too Many Requests"). A bare "rate" substring is not enough: it also
# matches "generate", "separate" or "accurate" and would invent a false cause.
_RATE_LIMIT_RE = re.compile(r"\brate[\s_-]*limit|too many requests", re.I)


def friendly_error(e: BaseException, secrets: Iterable[Optional[str]] = ()) -> str:
    """Turn a raw exception into a short, user-friendly error message.

    Any of ``secrets`` echoed by an SDK or server error is redacted first, so a
    test status (which is client-visible state) never carries a credential.
    """
    msg = str(e)
    for secret in secrets:
        if secret and len(secret) >= 4:
            msg = msg.replace(secret, "[redacted]")

    # Common patterns -> plain-English rewrites
    if "401" in msg or "Unauthorized" in msg or _INVALID_KEY_RE.search(msg):
        return INVALID_API_KEY_MESSAGE
    if _HTTP_400_RE.search(msg) and _INVALID_ARGUMENT_RE.search(msg):
        return INVALID_REQUEST_MESSAGE
    if "403" in msg or "Forbidden" in msg:
        return "Access denied. Your API key may lack the required permissions."
    if "404" in msg or "Not Found" in msg:
        return "Endpoint not found. Please check the URL or model name."
    if "429" in msg or _RATE_LIMIT_RE.search(msg):
        return "Rate limited by the provider. Please wait a moment and try again."
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "Connection timed out. Check the server URL and your network."
    if "connection" in msg.lower() and ("refused" in msg.lower() or "error" in msg.lower()):
        return "Could not connect to the server. Is the URL correct and the service running?"
    if "resolve" in msg.lower() or "dns" in msg.lower() or "name or service" in msg.lower():
        return "Could not resolve the hostname. Check the server URL."
    if "ssl" in msg.lower() or "certificate" in msg.lower():
        return "SSL/TLS error. The server's certificate may be invalid or expired."
    if "No module named" in msg:
        pkg = msg.split("No module named")[-1].strip(" \"'")
        return f"Missing Python package: {pkg}"

    # Fallback: truncate and clean up raw messages
    # Strip common noise like 'headers: {...}' or JSON blobs
    msg = re.sub(r"\{[^{}]{50,}\}", "{...}", msg)  # collapse long dicts
    msg = re.sub(r"headers:\s*\{[^}]*\}", "", msg)  # strip header dumps
    msg = msg.strip(" ,.")
    if len(msg) > 120:
        msg = msg[:117] + "..."
    return msg or "An unexpected error occurred."


# The original private name, kept for ``_settings_testing`` and older imports.
_friendly_error = friendly_error

__all__ = [
    "INVALID_API_KEY_MESSAGE",
    "INVALID_REQUEST_MESSAGE",
    "friendly_error",
    "_friendly_error",
]
