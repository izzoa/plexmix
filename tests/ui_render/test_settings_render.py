"""Render smoke: Settings as one tabs tree, labelled controls and status regions.

Real Reflex only; run in its own session: ``pytest tests/ui_render -q``.
Assertions use the compiled-JSX format documented in ``conftest.py``.
"""

from __future__ import annotations

import re
from collections import Counter

import reflex as rx

from plexmix.ui.pages import _settings_sections as sections
from plexmix.ui.pages import settings as settings_page
from plexmix.ui.states.settings_state import SECRET_FIELDS, SettingsState
from plexmix.ui.utils.a11y import SETTINGS_SECTIONS
from tests.ui_render.conftest import rendered

_PAGE_JS: str | None = None


def _page() -> str:
    global _PAGE_JS
    if _PAGE_JS is None:
        _PAGE_JS = rendered(settings_page.settings())
    return _PAGE_JS


def _subtree(js: str, start: int) -> str:
    """Return the balanced ``jsx(...)`` call starting at ``start`` (skips string literals)."""
    assert js.startswith("jsx(", start), js[start : start + 40]
    depth = 0
    i = start
    quote = ""
    while i < len(js):
        ch = js[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return js[start : i + 1]
        i += 1
    raise AssertionError("unbalanced jsx call")


def _element_head(js: str, needle: str, after: int = 0) -> str:
    """Return ``jsx(tag,{props}`` of the element whose props contain ``needle``.

    The first occurrence of ``needle`` at or after index ``after`` is used.
    """
    pos = js.index(needle, after)
    start = js.rindex("jsx(", 0, pos)
    brace = js.index("{", start)
    depth = 0
    for j in range(brace, len(js)):
        if js[j] == "{":
            depth += 1
        elif js[j] == "}":
            depth -= 1
            if depth == 0:
                return js[start : j + 1]
    raise AssertionError("unbalanced props")


def _main(js: str) -> str:
    """The shell's ``<main>`` subtree (excludes the login and boot branches)."""
    return _subtree(js, js.index('jsx("main",'))


def _panel(js: str, section: str) -> str:
    """The ``Tabs.Content`` subtree for one section."""
    for m in re.finditer(r"jsx\(RadixThemesTabs\.Content,", js):
        tree = _subtree(js, m.start())
        if f'value:"{section}"' in _element_head(tree, "RadixThemesTabs.Content"):
            return tree
    raise AssertionError(f"no panel for {section}")


def _ids(js: str) -> list[str]:
    return re.findall(r'\bid:"([^"]+)"', js)


def _html_fors(js: str) -> list[str]:
    return re.findall(r'htmlFor:"([^"]+)"', js)


_LABELS = (
    "Plex Server",
    "AI Provider",
    "Embeddings",
    "Audio Analysis",
    "MusicBrainz",
    "Advanced",
    "Appearance",
)


class TestSettingsTabs:
    def test_one_tabs_tree_vertical_bound_to_state(self) -> None:
        js = _page()
        assert js.count("jsx(RadixThemesTabs.Root,") == 1
        root = _element_head(js, 'className:"settings-tabs"')
        assert root.startswith("jsx(RadixThemesTabs.Root,")
        assert 'orientation:"vertical"' in root
        assert "active_tab_rx_state_" in root
        assert "set_active_tab" in root
        assert "hide-mobile" not in js and "hide-desktop" not in js

    def test_seven_triggers_with_full_names_in_order(self) -> None:
        js = _page()
        triggers = [
            _subtree(js, m.start()) for m in re.finditer(r"jsx\(RadixThemesTabs\.Trigger,", js)
        ]
        assert len(triggers) == 7
        values = [re.search(r'value:"([^"]+)"', t).group(1) for t in triggers]  # type: ignore[union-attr]
        assert tuple(values) == SETTINGS_SECTIONS
        for trigger, label in zip(triggers, _LABELS):
            assert f'"{label}")' in trigger
            assert 'className:"settings-tab"' in trigger
        tab_list = _element_head(js, 'className:"settings-tab-list"')
        assert tab_list.startswith("jsx(RadixThemesTabs.List,")
        assert '"aria-label":"Settings sections"' in tab_list

    def test_seven_panels_not_tab_stops(self) -> None:
        js = _page()
        heads = [
            _element_head(js, "RadixThemesTabs.Content", m.start())
            for m in re.finditer(r"jsx\(RadixThemesTabs\.Content,", js)
        ]
        assert len(heads) == 7
        for head, section in zip(heads, SETTINGS_SECTIONS):
            assert f'value:"{section}"' in head
            assert "tabIndex:-1" in head  # Tab from the list lands on the first control
            assert 'className:"settings-panel"' in head

    def test_each_field_rendered_once(self) -> None:
        js = _page()
        assert js.count('"Server URL"') == 1
        assert js.count('id:"set-plex-url"') == 1

    def test_ids_are_unique(self) -> None:
        dupes = [k for k, v in Counter(_ids(_page())).items() if v > 1]
        assert dupes == []


class TestSettingsHeadings:
    def test_single_h1_settings(self) -> None:
        main = _main(_page())
        headings = re.findall(r"jsx\(RadixThemesHeading,\{([^}]*)\}", main)
        h1s = [h for h in headings if "as:" not in h]
        assert len(h1s) == 1
        assert 'id:"main-heading"' in h1s[0] and "tabIndex:-1" in h1s[0]
        assert 'jsx("h1"' not in main  # layout(heading=False): no sr-only duplicate
        h1_text = main[main.index('id:"main-heading"') :]
        assert h1_text[h1_text.index("}") + 1 :].startswith(',"Settings")')

    def test_section_titles_are_h2(self) -> None:
        main = _main(_page())
        h2s = re.findall(r'jsx\(RadixThemesHeading,\{as:"h2"[^}]*\},"([^"]+)"\)', main)
        assert h2s == [
            "Plex Server",
            "AI Provider",
            "Embedding Provider",
            "Audio Analysis",
            "MusicBrainz",
            "Advanced Settings",
            "Appearance",
        ]


_EXPECTED_FIELD_IDS = {
    # Plex
    "set-plex-url",
    "secret-plex_token",
    "set-plex-library",
    # AI
    "set-ai-provider",
    "set-ai-custom-endpoint",
    "set-ai-custom-model",
    "secret-ai_custom_api_key",
    "secret-ai_api_key",
    "set-ai-local-model",
    "set-ai-model",
    "set-ai-model-custom",
    "set-ai-temperature",
    "set-ai-local-mode",
    "set-ai-local-endpoint",
    "secret-ai_local_auth_token",
    # Embedding
    "set-emb-provider",
    "set-emb-custom-endpoint",
    "set-emb-custom-model",
    "secret-embedding_custom_api_key",
    "set-emb-custom-dimension",
    "secret-embedding_api_key",
    "set-emb-model",
    "set-emb-model-custom",
    # Audio / MusicBrainz / Advanced
    "set-audio-enabled",
    "set-audio-analyze-on-sync",
    "set-audio-duration-limit",
    "set-mb-enabled",
    "set-mb-enrich-on-sync",
    "set-mb-confidence",
    "set-mb-contact-email",
    "set-adv-log-level",
}


class TestSettingsLabels:
    def test_every_label_for_has_a_matching_id(self) -> None:
        main = _main(_page())
        ids = set(_ids(main))
        fors = _html_fors(main)
        assert [f for f in fors if f not in ids] == []
        assert set(fors) == _EXPECTED_FIELD_IDS

    def test_labels_are_label_elements_with_field_label_class(self) -> None:
        js = _page()
        head = _element_head(js, 'htmlFor:"set-plex-url"')
        assert head.startswith('jsx("label",')
        assert 'className:"field-label"' in head
        assert '"gray.11"' not in js

    def test_custom_model_inputs_have_own_label(self) -> None:
        js = _page()
        # honest-status 10.4 relabelled the AI field (same id); Embeddings keeps its label.
        for field_id, text in (
            ("set-ai-model-custom", "Use a model that isn't listed"),
            ("set-emb-model-custom", "Custom model name"),
        ):
            label = _subtree(js, js.rindex("jsx(", 0, js.index(f'htmlFor:"{field_id}"')))
            assert f'"{text}")' in label

    def test_selects_named_by_id_on_trigger_not_aria_label(self) -> None:
        js = _page()
        for select_id in (
            "set-plex-library",
            "set-ai-provider",
            "set-ai-model",
            "set-ai-local-model",
            "set-ai-local-mode",
            "set-emb-provider",
            "set-emb-model",
            "set-adv-log-level",
        ):
            head = _element_head(js, f'id:"{select_id}"')
            assert head.startswith("jsx(RadixThemesSelect.Trigger,"), select_id
        for root in re.finditer(r"jsx\(RadixThemesSelect\.Root,", js):
            assert '"aria-label"' not in _element_head(js, "Select.Root", root.start())

    def test_secret_fields_labelled_once_and_keep_change1_behaviour(self) -> None:
        js = _page()
        assert len(SECRET_FIELDS) == 6
        for field in SECRET_FIELDS:
            input_id = f"secret-{field}"
            assert js.count(f'id:"{input_id}"') == 1, field
            assert js.count(f'htmlFor:"{input_id}"') == 1, field
            head = _element_head(js, f'id:"{input_id}"')
            assert 'type:"password"' in head
            assert 'defaultValue:""' in head
            assert "stage_secret" in head  # staged on blur
            assert f'"aria-describedby":"{input_id}-status"' in head
            assert js.count(f'id:"{input_id}-status"') == 1
        assert "-mobile" not in js


class TestSwitchesAndRanges:
    def test_four_switches_named_by_labels(self) -> None:
        js = _page()
        expected = {
            "set-audio-enabled": "Enable Audio Analysis",
            "set-audio-analyze-on-sync": "Analyze Audio on Sync",
            "set-mb-enabled": "Enable MusicBrainz Enrichment",
            "set-mb-enrich-on-sync": "Enrich on Sync",
        }
        switches = re.findall(r'jsx\(RadixThemesSwitch,\{[^}]*\bid:"([^"]+)"', js)
        assert sorted(switches) == sorted(expected)
        for switch_id, text in expected.items():
            label = _subtree(js, js.rindex("jsx(", 0, js.index(f'htmlFor:"{switch_id}"')))
            assert label.startswith('jsx("label",')
            assert f'"{text}")' in label

    def test_sliders_are_labelled_native_ranges(self) -> None:
        js = _page()
        assert "RadixThemesSlider" not in js
        heads = [
            _element_head(js, 'type:"range"', m.start()) for m in re.finditer('type:"range"', js)
        ]
        assert all(h.startswith('jsx("input",') for h in heads)
        ranges = [re.search(r'\bid:"([^"]+)"', h).group(1) for h in heads]  # type: ignore[union-attr]
        assert sorted(ranges) == ["set-ai-temperature", "set-mb-confidence"]
        for range_id, text in (
            ("set-ai-temperature", "Temperature"),
            ("set-mb-confidence", "Confidence Threshold"),
        ):
            label = _subtree(js, js.rindex("jsx(", 0, js.index(f'htmlFor:"{range_id}"')))
            assert f'"{text}")' in label
        temperature = _element_head(js, 'id:"set-ai-temperature"')
        assert "set_ai_temperature" in temperature and 'min:"0"' in temperature
        confidence = _element_head(js, 'id:"set-mb-confidence"')
        assert "set_musicbrainz_confidence_threshold" in confidence and 'max:"100"' in confidence

    def test_duration_help_is_visible_and_describes_the_field(self) -> None:
        js = _page()
        # No hover-only help in the page content. (The shell's rail labels are Radix
        # tooltips since webui-zoom-and-narrow-fit; they sit outside <main>.)
        assert "Tooltip" not in _main(js)
        text = "Seconds of audio to analyze per track. 0 = full track."
        assert js.count(f'"{text}"') == 1
        help_head = _element_head(js, 'id:"set-audio-duration-help"')
        assert js[js.index(help_head) + len(help_head) :].startswith(f',"{text}")')
        field = _element_head(js, 'id:"set-audio-duration-limit"')
        assert '"aria-describedby":"set-audio-duration-help"' in field


class TestModelChips:
    def test_chips_are_pressed_toggle_buttons_in_a_named_group(self) -> None:
        js = rendered(sections._model_chips("probe-models-label", SettingsState.ai_model))
        group = _element_head(js, 'role:"group"')
        assert '"aria-labelledby":"probe-models-label"' in group
        label = _element_head(js, 'id:"probe-models-label"')
        assert js[js.index(label) + len(label) :].startswith(',"Available models:")')
        button = _element_head(js, 'className:"badge badge-blue chip-btn"')
        assert button.startswith('jsx("button",')
        assert 'type:"button"' in button
        # Label in name: the visible model name starts the accessible name.
        assert re.search(r'"aria-label":\(\w+\+" \\u2013 use this model"\)', button), button
        assert "Use model" not in js
        assert '"aria-pressed":(' in button and '"true" : "false"' in button
        assert "ai_model_rx_state_" in button
        assert "select_discovered_model" in button
        assert "accent.4" not in js

    def test_page_has_both_chip_groups_with_their_settings(self) -> None:
        js = _page()
        custom = _element_head(js, '"aria-labelledby":"set-ai-custom-models-label"')
        local = _element_head(js, '"aria-labelledby":"set-ai-local-models-label"')
        assert 'role:"group"' in custom and 'role:"group"' in local
        assert js.count("chip-btn") == 2
        # The custom chips reflect the custom model name; local ones the model.
        custom_tree = js[js.index(custom) :]
        assert "ai_custom_model_rx_state_" in _element_head(custom_tree, "chip-btn")
        local_tree = js[js.index(local) :]
        assert "ai_model_rx_state_" in _element_head(local_tree, "chip-btn")


class TestTestButtons:
    """Test Connection / Provider / Embeddings keep focus while their test runs."""

    def test_spinner_without_native_disabled(self) -> None:
        js = _page()
        for section, label in (
            ("plex", "Test Connection"),
            ("ai", "Test Provider"),
            ("embedding", "Test Embeddings"),
        ):
            panel = _panel(js, section)
            end = panel.index(f'"{label}")')
            head = panel[panel.rindex("jsx(RadixThemesButton,", 0, end) : end]
            # Radix's loading would default disabled to true and drop focus to <body>.
            assert "disabled:false" in head, label
            assert "loading:" in head and "testing_connection_rx_state_" in head, label
            assert '"aria-busy":(' in head and '"aria-disabled":(' in head, label
            assert '"true" : "false"' in head, label


class TestAppearanceRadios:
    def test_five_radios_in_two_named_groups(self) -> None:
        panel = _panel(_page(), "appearance")
        assert panel.count('type:"radio"') == 5
        names = re.findall(r'\bname:"([^"]+)"', panel)
        assert Counter(names) == {"pm-density": 2, "pm-accent": 3}
        assert panel.count('role:"radiogroup"') == 2
        assert '"Density")' in panel and '"Accent intensity")' in panel
        assert '"aria-labelledby":"set-appearance-density-legend"' in panel
        assert '"aria-labelledby":"set-appearance-accent-legend"' in panel
        assert 'className:"seg"' in panel
        assert 'jsx("button",{className:' not in panel.split("Switch to")[-1]


class TestStatusRegions:
    def test_wrapper_is_persistent_and_outside_the_cond(self) -> None:
        js = rendered(sections._status_text(SettingsState.plex_test_status, "probe-status"))
        head = _element_head(js, 'id:"probe-status"')
        assert js.startswith(head)  # the box is the root; the cond is its child
        assert head.startswith("jsx(RadixThemesBox,")
        assert 'role:"status"' in head
        assert '"aria-live":"polite"' in head
        assert '"aria-atomic":"true"' in head
        # The first (only) child is the rx.cond expression, wrapped in a Fragment.
        assert js[len(head) :].startswith(",jsx(Fragment,{},(")

    def test_each_region_id_once_in_its_panel(self) -> None:
        js = _page()
        # honest-status 10.4: the per-section set-save-status-<section> regions became
        # one page-level set-save-status region (tests/ui_render/
        # test_settings_changes_render.py); Embeddings gained the index-warning region.
        expected: dict[str, list[str]] = {
            "plex": ["set-plex-status"],
            "ai": ["set-ai-status"],
            "embedding": ["set-emb-status", "set-emb-index-warning"],
            "audio": [],
            "musicbrainz": [],
            "advanced": [],
        }
        for section, region_ids in expected.items():
            panel = _panel(js, section)
            assert panel.count('role:"status"') == len(region_ids), section
            for region_id in region_ids:
                assert panel.count(f'id:"{region_id}"') == 1
                assert 'role:"status"' in _element_head(panel, f'id:"{region_id}"')
                assert js.count(f'id:"{region_id}"') == 1
        assert "set-save-status-" not in js


class TestEndpointErrorAndProgress:
    def test_local_endpoint_invalid_and_described(self) -> None:
        js = _page()
        field = _element_head(js, 'id:"set-ai-local-endpoint"')
        assert '"aria-describedby":"set-ai-local-endpoint-error"' in field
        assert '"aria-invalid":(' in field and "local_endpoint_error_rx_state_" in field
        error = _subtree(js, js.rindex("jsx(", 0, js.index('id:"set-ai-local-endpoint-error"')))
        assert "local_endpoint_error_rx_state_" in error

    def test_download_progress_bars_are_named(self) -> None:
        js = _page()
        assert js.count("jsx(RadixThemesProgress,") == 2
        assert '"aria-label":"Local model download progress"' in js
        assert '"aria-label":"Embedding model download progress"' in js


def test_rx_is_real() -> None:
    """Guard: these assertions are meaningless against the tests/ui mock."""
    assert hasattr(rx, "__version__") or hasattr(rx, "State")
