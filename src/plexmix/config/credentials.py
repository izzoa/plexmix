import os
import re
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "plexmix"

# Permissive superset of characters seen in real cloud API keys. A transport-safe
# key that does not match this only triggers a non-blocking warning (key formats
# vary and change), and the check is scoped to known cloud providers.
_API_KEY_SHAPE_RE = re.compile(r"^[A-Za-z0-9_.+/=-]+$")
_SHAPE_CHECKED_PROVIDERS = {"gemini", "openai", "claude", "anthropic", "cohere"}


class InvalidAPIKeyError(ValueError):
    """Raised when an API key cannot be sent as an HTTP header value.

    Subclasses ``ValueError`` so existing provider-construction handlers that
    already catch ``ValueError`` continue to work.
    """


def sanitize_credential_value(value: Optional[str]) -> Optional[str]:
    """Strip surrounding whitespace/newlines from a credential value.

    Only the surrounding characters are removed; the interior is never altered.
    Returns ``None`` unchanged so callers can distinguish "missing" from "empty".
    """
    if value is None:
        return None
    return value.strip()


def validate_api_key(key: Optional[str], provider: Optional[str] = None) -> None:
    """Validate a resolved API key before it is used to construct a provider.

    Raises :class:`InvalidAPIKeyError` when the key contains characters that
    cannot be sent in an HTTP header (interior whitespace, control characters,
    or non-ASCII) — these would otherwise surface as a cryptic transport error
    mid-run. Logs a non-blocking warning when a known cloud provider's key has an
    unexpected shape. Never raises for an empty/None key (that is handled
    elsewhere as "not configured").
    """
    if not key:
        return
    # HTTP header values must be printable ASCII without spaces; surrounding
    # whitespace is already stripped, so anything outside 0x21-0x7E here is an
    # interior anomaly the transport cannot send.
    if any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in key):
        raise InvalidAPIKeyError(
            "API key contains invalid characters (whitespace, control, or "
            "non-ASCII characters). Re-enter it in Settings."
        )
    if provider and provider.lower() in _SHAPE_CHECKED_PROVIDERS:
        if not _API_KEY_SHAPE_RE.match(key):
            logger.warning(
                "API key for provider '%s' has an unexpected format; it may be malformed.",
                provider,
            )


# Map credential keys to environment variable names
_ENV_VAR_MAP = {
    "plex_token": ["PLEX_TOKEN"],
    "google_api_key": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "openai_api_key": ["OPENAI_API_KEY"],
    "anthropic_api_key": ["ANTHROPIC_API_KEY"],
    "cohere_api_key": ["COHERE_API_KEY"],
    "custom_ai_api_key": ["CUSTOM_AI_API_KEY"],
    "custom_embedding_api_key": ["CUSTOM_EMBEDDING_API_KEY"],
}


def _get_keyring() -> Any:
    """Import keyring, returning None if unavailable (e.g. in containers)."""
    try:
        import keyring  # type: ignore[import-untyped]  # noqa: F811

        return keyring
    except Exception:
        return None


def store_credential(key: str, value: str) -> bool:
    kr = _get_keyring()
    if kr is None:
        logger.warning(f"Keyring unavailable; cannot store credential: {key}")
        return False
    value = sanitize_credential_value(value) or ""
    try:
        kr.set_password(SERVICE_NAME, key, value)
        logger.info(f"Stored credential: {key}")
        return True
    except Exception as e:
        logger.error(f"Failed to store credential {key}: {e}")
        return False


def get_credential(key: str) -> Optional[str]:
    # Check environment variables first
    env_vars = _ENV_VAR_MAP.get(key, [])
    for env_var in env_vars:
        value = os.getenv(env_var)
        if value:
            logger.debug(f"Retrieved credential {key} from env var {env_var}")
            return sanitize_credential_value(value)

    # Fall back to keyring
    kr = _get_keyring()
    if kr is None:
        return None
    try:
        kr_value: Optional[str] = kr.get_password(SERVICE_NAME, key)
        if kr_value:
            logger.debug(f"Retrieved credential: {key}")
            return sanitize_credential_value(kr_value)
        return kr_value
    except Exception as e:
        logger.error(f"Failed to retrieve credential {key}: {e}")
        return None


def delete_credential(key: str) -> bool:
    kr = _get_keyring()
    if kr is None:
        logger.warning(f"Keyring unavailable; cannot delete credential: {key}")
        return False
    try:
        kr.delete_password(SERVICE_NAME, key)
        logger.info(f"Deleted credential: {key}")
        return True
    except Exception as e:
        if "PasswordDeleteError" in type(e).__name__:
            logger.warning(f"Credential not found: {key}")
            return False
        logger.error(f"Failed to delete credential {key}: {e}")
        return False


def get_plex_token() -> Optional[str]:
    return get_credential("plex_token")


def store_plex_token(token: str) -> bool:
    return store_credential("plex_token", token)


def get_google_api_key() -> Optional[str]:
    return get_credential("google_api_key")


def store_google_api_key(api_key: str) -> bool:
    return store_credential("google_api_key", api_key)


def get_openai_api_key() -> Optional[str]:
    return get_credential("openai_api_key")


def store_openai_api_key(api_key: str) -> bool:
    return store_credential("openai_api_key", api_key)


def get_anthropic_api_key() -> Optional[str]:
    return get_credential("anthropic_api_key")


def store_anthropic_api_key(api_key: str) -> bool:
    return store_credential("anthropic_api_key", api_key)


def get_cohere_api_key() -> Optional[str]:
    return get_credential("cohere_api_key")


def store_cohere_api_key(api_key: str) -> bool:
    return store_credential("cohere_api_key", api_key)


def get_custom_ai_api_key() -> Optional[str]:
    return get_credential("custom_ai_api_key")


def store_custom_ai_api_key(api_key: str) -> bool:
    return store_credential("custom_ai_api_key", api_key)


def get_custom_embedding_api_key() -> Optional[str]:
    return get_credential("custom_embedding_api_key")


def store_custom_embedding_api_key(api_key: str) -> bool:
    return store_credential("custom_embedding_api_key", api_key)
