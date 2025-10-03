import re
from typing import Optional, Tuple
from urllib.parse import urlparse


def validate_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Validate a URL format.
    Returns (is_valid, error_message)
    """
    if not url:
        return False, "URL is required"

    try:
        result = urlparse(url)
        if not all([result.scheme, result.netloc]):
            return False, "Invalid URL format. Must include protocol (http:// or https://)"

        if result.scheme not in ["http", "https"]:
            return False, "URL must use http or https protocol"

        # Check for common Plex port
        if "32400" not in url and "plex" in url.lower():
            # Just a warning, not an error
            pass

        return True, None
    except Exception:
        return False, "Invalid URL format"


def validate_api_key(api_key: str, provider: str) -> Tuple[bool, Optional[str]]:
    """
    Validate API key format based on provider.
    Returns (is_valid, error_message)
    """
    if not api_key:
        return False, "API key is required"

    api_key = api_key.strip()

    if provider == "openai":
        if not api_key.startswith("sk-"):
            return False, "OpenAI API key should start with 'sk-'"
        if len(api_key) < 40:
            return False, "OpenAI API key seems too short"

    elif provider == "gemini":
        if len(api_key) < 39:
            return False, "Gemini API key seems too short"

    elif provider == "cohere":
        if len(api_key) < 40:
            return False, "Cohere API key seems too short"

    elif provider == "claude":
        if not api_key.startswith("sk-ant-"):
            return False, "Claude API key should start with 'sk-ant-'"

    return True, None


def validate_plex_token(token: str) -> Tuple[bool, Optional[str]]:
    """
    Validate Plex token format.
    Returns (is_valid, error_message)
    """
    if not token:
        return False, "Plex token is required"

    token = token.strip()

    # Plex tokens are typically 20 characters
    if len(token) != 20:
        return False, f"Plex token should be 20 characters (got {len(token)})"

    # Should be alphanumeric
    if not re.match(r'^[a-zA-Z0-9_-]+$', token):
        return False, "Plex token contains invalid characters"

    return True, None


def validate_number_range(
    value: any,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
    field_name: str = "Value"
) -> Tuple[bool, Optional[str]]:
    """
    Validate a number is within range.
    Returns (is_valid, error_message)
    """
    try:
        num = float(value)
    except (ValueError, TypeError):
        return False, f"{field_name} must be a number"

    if min_val is not None and num < min_val:
        return False, f"{field_name} must be at least {min_val}"

    if max_val is not None and num > max_val:
        return False, f"{field_name} must be at most {max_val}"

    return True, None


def validate_year(year: any) -> Tuple[bool, Optional[str]]:
    """
    Validate a year value.
    Returns (is_valid, error_message)
    """
    if year is None or year == "":
        return True, None  # Optional field

    try:
        year_int = int(year)
        if year_int < 1900:
            return False, "Year must be 1900 or later"
        if year_int > 2100:
            return False, "Year must be 2100 or earlier"
        return True, None
    except (ValueError, TypeError):
        return False, "Year must be a valid number"


def validate_batch_size(size: any) -> Tuple[bool, Optional[str]]:
    """
    Validate batch size for processing.
    Returns (is_valid, error_message)
    """
    return validate_number_range(size, 1, 100, "Batch size")


def validate_temperature(temp: any) -> Tuple[bool, Optional[str]]:
    """
    Validate AI temperature setting.
    Returns (is_valid, error_message)
    """
    return validate_number_range(temp, 0.0, 1.0, "Temperature")


def validate_max_tracks(count: any) -> Tuple[bool, Optional[str]]:
    """
    Validate maximum tracks for playlist.
    Returns (is_valid, error_message)
    """
    return validate_number_range(count, 1, 500, "Max tracks")


def validate_playlist_name(name: str) -> Tuple[bool, Optional[str]]:
    """
    Validate playlist name.
    Returns (is_valid, error_message)
    """
    if not name or not name.strip():
        return False, "Playlist name is required"

    if len(name) > 255:
        return False, "Playlist name is too long (max 255 characters)"

    # Check for invalid characters that might cause filesystem issues
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        if char in name:
            return False, f"Playlist name cannot contain '{char}'"

    return True, None


def validate_search_query(query: str) -> Tuple[bool, Optional[str]]:
    """
    Validate search query input.
    Returns (is_valid, error_message)
    """
    if len(query) > 500:
        return False, "Search query is too long (max 500 characters)"

    # Check for potential SQL injection patterns
    dangerous_patterns = [';', '--', '/*', '*/', 'DROP', 'DELETE', 'INSERT', 'UPDATE']
    query_upper = query.upper()
    for pattern in dangerous_patterns:
        if pattern in query_upper:
            return False, "Search query contains invalid characters"

    return True, None