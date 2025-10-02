import keyring
import logging
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "plexmix"


def store_credential(key: str, value: str) -> bool:
    try:
        keyring.set_password(SERVICE_NAME, key, value)
        logger.info(f"Stored credential: {key}")
        return True
    except Exception as e:
        logger.error(f"Failed to store credential {key}: {e}")
        return False


def get_credential(key: str) -> Optional[str]:
    try:
        value = keyring.get_password(SERVICE_NAME, key)
        if value:
            logger.debug(f"Retrieved credential: {key}")
        return value
    except Exception as e:
        logger.error(f"Failed to retrieve credential {key}: {e}")
        return None


def delete_credential(key: str) -> bool:
    try:
        keyring.delete_password(SERVICE_NAME, key)
        logger.info(f"Deleted credential: {key}")
        return True
    except keyring.errors.PasswordDeleteError:
        logger.warning(f"Credential not found: {key}")
        return False
    except Exception as e:
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
