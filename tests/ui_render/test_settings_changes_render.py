"""Render smoke: Settings' one Save/Discard bar, mismatch warning and model choices.

webui-honest-status-and-first-run task 10.4. Real Reflex only; run in its own
session: ``pytest tests/ui_render -q``. Assertions use the compiled-JSX format
documented in ``conftest.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import reflex as rx

from plexmix.ui.pages import _settings_sections as sections
from plexmix.ui.pages import settings as settings_page
from plexmix.ui.states.settings_state import AI_MODEL_TEXT_ID, SECTION_LABELS
from plexmix.ui.utils.a11y import SETTINGS_SECTIONS
from tests.ui_render.conftest import rendered
from tests.ui_render.test_settings_render import _element_head, _page, _panel, _subtree

_REDESIGN_CSS = Path(__file__).resolve().parents[2] / "assets" / "redesign.css"


def _tabs_root(js: str) -> str:
    return _subtree(js, js.rindex("jsx(", 0, js.index('className:"settings-tabs"')))


def _cond_branch(js: str, needle: str) -> str:
    """The ``(<test> ? (<a>) : (<b>))`` text around ``needle``, starting at its test."""
    start = js.rindex("(", 0, js.index(needle))
    return js[start : js.index(needle) + len(needle)]


class TestNoSectionSave:
    def test_panels_have_no_save_button(self) -> None:
        js = _page()
        for section in SETTINGS_SECTIONS:
            panel = _panel(js, section)
            assert '"Save")' not in panel, section
            assert "save_all_settings" not in panel, section

    def test_test_buttons_stay_in_their_sections(self) -> None:
        js = _page()
        for section, label, handler in (
            ("plex", "Test Connection", "test_plex_connection"),
            ("ai", "Test Provider", "test_ai_provider"),
            ("embedding", "Test Embeddings", "test_embedding_provider"),
        ):
            panel = _panel(js, section)
            assert f'"{label}")' in panel and handler in panel, section

    def test_button_row_holds_only_the_test_button(self) -> None:
        js = rendered(sections._button_row("Probe", rx.console_log("x"), False))
        assert js.count("jsx(RadixThemesButton,") == 1
        assert '"Probe")' in js and "Save" not in js

    def test_tab_labels_are_the_bar_section_names(self) -> None:
        labels = [label for _, _, label, _ in settings_page._SECTIONS]
        assert labels == [SECTION_LABELS[s] for s in SETTINGS_SECTIONS]


class TestSavebar:
    def test_one_bar_outside_the_tabs_shown_only_when_dirty(self) -> None:
        js = _page()
        assert js.count('id:"set-savebar"') == 1
        assert 'id:"set-savebar"' not in _tabs_root(js)
        head = _element_head(js, 'id:"set-savebar"')
        assert head.startswith('jsx("div",')
        assert 'className:"savebar animate-slide-up"' in head
        # Rendered inside the has_unsaved_changes condition.
        bar_start = js.rindex("jsx(", 0, js.index('id:"set-savebar"'))
        cond_start = js.rindex("has_unsaved_changes_rx_state_", 0, bar_start)
        assert js[cond_start:bar_start].count("jsx(") <= 1  # only the cond's Fragment between

    def test_bar_lists_sections_and_offers_discard_and_save(self) -> None:
        js = _page()
        bar = _subtree(js, js.rindex("jsx(", 0, js.index('id:"set-savebar"')))
        assert "unsaved_from_earlier_rx_state_" in bar
        assert '"Unsaved changes from earlier"' in bar and '"Unsaved changes"' in bar
        assert "changed_sections_label_rx_state_" in bar
        discard = _element_head(bar, 'id:"set-discard"')
        save = _element_head(bar, 'id:"set-save"')
        for button, cls, handler in (
            (discard, "btn btn-3 btn-soft", "discard_changes"),
            (save, "btn btn-3 btn-primary", "save_all_settings"),
        ):
            assert button.startswith('jsx("button",')
            assert f'className:"{cls}"' in button
            assert 'type:"button"' in button
            assert handler in button
        assert bar.index('"Discard")') < bar.index('"Save")')  # Save is the last action
        dot = _element_head(bar, 'className:"dot dot-warning pulse"')
        assert '"aria-hidden":"true"' in dot

    def test_save_status_region_is_persistent_and_beside_the_bar(self) -> None:
        js = _page()
        assert js.count('id:"set-save-status"') == 1
        head = _element_head(js, 'id:"set-save-status"')
        assert 'role:"status"' in head
        assert '"aria-live":"polite"' in head and '"aria-atomic":"true"' in head
        region = _subtree(js, js.rindex("jsx(", 0, js.index('id:"set-save-status"')))
        assert "save_status_message_rx_state_" in region
        # Not inside the bar or its condition: it follows the bar's whole cond.
        bar_cond = js.rindex("has_unsaved_changes_rx_state_", 0, js.index('id:"set-savebar"'))
        bar_cond_start = js.rindex("jsx(Fragment,{},(", 0, bar_cond)
        cond_tree = _subtree(js, bar_cond_start)
        assert 'id:"set-save-status"' not in cond_tree
        assert js.index('id:"set-save-status"') > bar_cond_start + len(cond_tree) - 1
        assert 'id:"set-save-status"' not in _tabs_root(js)
        assert "set-save-status-" not in js

    def test_savebar_css_is_sticky_and_uses_tokens(self) -> None:
        css = _REDESIGN_CSS.read_text()
        start = css.index("/* ── change 4: settings")
        end = css.index("/* ── end change 4: settings ── */")
        section = css[start:end]
        rule = re.search(r"\.savebar \{([^}]*)\}", section)
        assert rule is not None
        body = rule.group(1)
        assert "position: sticky" in body and "bottom: 16px" in body
        assert "box-shadow: var(--shadow-lg)" in body
        assert "flex-wrap: wrap" in body
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", section)


class TestEmbeddingWarning:
    def test_warning_replaces_the_static_help(self) -> None:
        js = _page()
        panel = _panel(js, "embedding")
        assert "Must match existing embeddings or regenerate" not in js
        head = _element_head(panel, 'id:"set-emb-index-warning"')
        assert 'role:"status"' in head and '"aria-live":"polite"' in head
        region = _subtree(panel, panel.rindex("jsx(", 0, panel.index('id:"set-emb-index-warning"')))
        assert "embedding_change_warning_rx_state_" in region
        assert 'className:"callout callout-warning"' in region
        assert '"Doesn\'t match your search index")' in region
        assert 'className:"c-body"' in region


class TestModelChoices:
    def test_select_uses_the_choices_and_describes_the_note(self) -> None:
        js = _page()
        trigger = _element_head(js, 'id:"set-ai-model"')
        assert trigger.startswith("jsx(RadixThemesSelect.Trigger,")
        assert '"aria-describedby":"set-ai-model-note"' in trigger
        root_start = js.rindex("jsx(RadixThemesSelect.Root,", 0, js.index('id:"set-ai-model"'))
        select = _subtree(js, root_start)
        assert "ai_model_choices_rx_state_" in select
        assert "ai_models_rx_state_" not in select
        local = _element_head(js, 'id:"set-ai-local-model"')
        assert '"aria-describedby":"set-ai-model-note"' in local

    def test_not_in_current_list_badge(self) -> None:
        js = _page()
        assert js.count('id:"set-ai-model-note"') == 1
        note = _subtree(js, js.rindex("jsx(", 0, js.index('id:"set-ai-model-note"')))
        assert "ai_model_unlisted_rx_state_" in note
        assert 'jsx("span",{className:"badge badge-gray"},"Not in current list")' in note

    def test_local_select_adds_the_unlisted_model(self) -> None:
        js = _page()
        root_start = js.rindex(
            "jsx(RadixThemesSelect.Root,", 0, js.index('id:"set-ai-local-model"')
        )
        select = _subtree(js, root_start)
        branch = select[select.index("ai_model_unlisted_rx_state_") :]
        assert branch.startswith("ai_model_unlisted_rx_state_?(jsx(Fragment,{},jsx(")
        assert "RadixThemesSelect.Item,{value:" in branch
        assert "ai_model_rx_state_" in branch[: branch.index("))")]

    def test_unlisted_field_is_client_owned_and_relabelled(self) -> None:
        js = _page()
        assert AI_MODEL_TEXT_ID == "set-ai-model-custom"
        field = _element_head(js, f'id:"{AI_MODEL_TEXT_ID}"')
        assert field.startswith('jsx("input",')
        assert "defaultValue:" in field and "ai_model_text_rx_state_" in field
        assert "value:" not in field.replace("defaultValue:", "")
        assert "set_ai_model_text" in field
        assert '["debounce"] : 300' in field and "onBlur:" in field
        assert 'className:"input"' in field
        assert '"aria-describedby":"set-ai-model-custom-help"' in field
        label = _subtree(js, js.rindex("jsx(", 0, js.index(f'htmlFor:"{AI_MODEL_TEXT_ID}"')))
        assert '"Use a model that isn\'t listed")' in label
        assert "Custom model name" not in label


class TestEmbeddingConfirm:
    def test_alert_dialog_with_keep_and_save_later(self) -> None:
        js = _page()
        assert js.count('id:"set-emb-confirm"') == 1
        content = _element_head(js, 'id:"set-emb-confirm"')
        assert content.startswith("jsx(RadixThemesAlertDialog.Content,")
        assert '"aria-modal":"true"' in content
        assert "pmFocus.restore()" in content
        root_start = js.rindex("jsx(RadixThemesAlertDialog.Root,", 0, js.index(content))
        dialog = _subtree(js, root_start)
        root = _element_head(dialog, "RadixThemesAlertDialog.Root")
        assert "embedding_confirm_open_rx_state_" in root
        assert "set_embedding_confirm_open" in root
        assert "embedding_change_warning_rx_state_" in dialog
        # Cancel is the only AlertDialog part button; the others close through state.
        assert dialog.count("jsx(RadixThemesAlertDialog.Cancel,") == 1
        assert "AlertDialog.Action" not in dialog
        cancel = _element_head(dialog, 'id:"set-emb-confirm-cancel"')
        keep = _element_head(dialog, 'id:"set-emb-confirm-secondary"')
        save = _element_head(dialog, 'id:"set-emb-confirm-confirm"')
        assert 'className:"btn btn-3 btn-soft"' in cancel
        assert 'className:"btn btn-3 btn-soft"' in keep and "revert_embedding_change" in keep
        assert 'className:"btn btn-3 btn-primary"' in save and "confirm_embedding_change" in save
        assert '"Keep current provider")' in dialog
        assert '"Save and rebuild later")' in dialog

    def test_dialog_is_outside_the_tabs(self) -> None:
        js = _page()
        assert 'id:"set-emb-confirm"' not in _tabs_root(js)
        order = re.findall(r'id:"(set-savebar|set-save-status|set-emb-confirm)"', js)
        assert order == ["set-savebar", "set-save-status", "set-emb-confirm"]


def test_rx_is_real() -> None:
    """Guard: these assertions are meaningless against the tests/ui mock."""
    assert hasattr(rx, "__version__") or hasattr(rx, "State")
