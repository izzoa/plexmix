import os
import reflex as rx
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

_config_kwargs = {
    "app_name": "plexmix_ui",
    "title": "PlexMixUI",
}

# Allow overriding the backend API URL the frontend connects to.
# Critical for Docker/reverse-proxy setups where external ports differ from internal.
_api_url = os.getenv("PLEXMIX_API_URL")
if _api_url:
    _config_kwargs["api_url"] = _api_url

config = rx.Config(**_config_kwargs)
