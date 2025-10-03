import reflex as rx
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

config = rx.Config(
    app_name="plexmix_ui",
)
