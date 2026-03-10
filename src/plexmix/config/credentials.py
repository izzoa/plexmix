import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "plexmix"

# Map credential keys to environment variable names
_ENV_VAR_MAP = {
    "plex_token": ["PLEX_TOKEN"],
    "google_api_key": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "openai_api_key": ["OPENAI_API_KEY"],
    "anthropic_api_key": ["ANTHROPIC_API_KEY"],
    "cohere_api_key": ["COHERE_API_KEY"],
}


def _get_keyring() -> Optional[object]:
    """Import keyring, returning None if unavailable (e.g. in containers)."""
    try:
        import keyring  # noqa: F811
        return keyring
    except Exception:
        return None


def store_credential(key: str, value: str) -> bool:
    kr = _get_keyring()
    if kr is None:
        logger.warning(f"Keyring unavailable; cannot store credential: {key}")
        return False
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
            return value

    # Fall back to keyring
    kr = _get_keyring()
    if kr is None:
        return None
    try:
        value = kr.get_password(SERVICE_NAME, key)
        if value:
            logger.debug(f"Retrieved credential: {key}")
        return value
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
