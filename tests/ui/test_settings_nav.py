"""Source-level guards for the Settings tabs / labels change (no Reflex needed).

The rendered structure is covered by ``tests/ui_render/test_settings_render.py``
(real Reflex, own session). These checks run in the default suite.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = _ROOT / "src" / "plexmix" / "ui" / "pages" / "settings.py"
_SECTIONS = _ROOT / "src" / "plexmix" / "ui" / "pages" / "_settings_sections.py"
_STATE = _ROOT / "src" / "plexmix" / "ui" / "states" / "settings_state.py"
_STYLES = _ROOT / "assets" / "styles.css"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestSettingsSource:
    def test_no_dotted_radix_color_strings(self) -> None:
        """Dotted Radix strings ("gray.11", "accent.3") are dead in this app: tokens only."""
        pattern = re.compile(r'"(accent|gray|red|orange)\.[0-9]+')
        for path in (_SETTINGS, _SECTIONS):
            hits = [
                f"{path.name}:{n}: {line.strip()}"
                for n, line in enumerate(_src(path).splitlines(), 1)
                if pattern.search(line)
            ]
            assert hits == [], hits

    def test_single_tree_no_duplicate_layouts(self) -> None:
        src = _src(_SETTINGS)
        for gone in ("hide-mobile", "hide-desktop", "_mobile_tabs", "_settings_sidebar"):
            assert gone not in src
        assert src.count("rx.tabs.root(") == 1
        assert 'orientation="vertical"' in src

    def test_secret_inputs_have_one_id_each(self) -> None:
        """No per-layout id suffixes remain now that each input renders once."""
        for path in (_SETTINGS, _SECTIONS, _STATE):
            src = _src(path)
            assert "-mobile" not in src
            assert "SECRET_INPUT_LAYOUTS" not in src

    def test_set_active_tab_never_navigates(self) -> None:
        src = _src(_STATE)
        body = src[src.index("def set_active_tab") : src.index("def _section_query_param")]
        assert "rx.redirect(" not in body  # (the docstring may name it; never a call)
        assert "settings_section_url_script" in body

    def test_no_slider_or_tooltip_in_settings(self) -> None:
        for path in (_SETTINGS, _SECTIONS):
            src = _src(path)
            assert "rx.slider(" not in src
            assert "rx.tooltip(" not in src

    def test_tab_trigger_focus_ring_and_narrow_stack(self) -> None:
        css = _src(_STYLES)
        ring = css[css.index("/* Focus ring for accessibility */") :]
        ring = ring[: ring.index("}")]
        assert ".rt-TabsTrigger:focus-visible" in ring
        assert ".settings-tab" in css
        assert ".settings-nav-item" not in css
        narrow = re.search(
            r"@media \(max-width: 768px\) \{\s*\.rt-TabsRoot\.settings-tabs[^@]*", css
        )
        assert narrow is not None
        assert "flex-direction: column" in narrow.group(0)
        assert "overflow-x: auto" in narrow.group(0)
