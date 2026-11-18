"""Render smoke: Library selection honesty, bulk bar, Tag/Delete dialogs, loading versus
empty, sync error and AI-tagging note (webui-honest-status-and-first-run, task 9.3).

Runs against real Reflex only (see tests/ui_render/conftest.py for the JSX format).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plexmix.ui.pages import library as library_page
from tests.ui_render.conftest import rendered

_CSS = Path(__file__).resolve().parents[2] / "assets" / "redesign.css"
_LS = (
    "reflex___state____state__plexmix___ui___states___app_state____app_state__"
    "plexmix___ui___states___library_state____library_state"
)


@pytest.fixture(scope="module")
def content_js() -> str:
    """The Library page content without the app shell (layout is bypassed)."""
    original = library_page.layout
    library_page.layout = lambda content, **_kw: content  # type: ignore[assignment]
    try:
        return rendered(library_page.library()).replace(_LS, "LS")
    finally:
        library_page.layout = original


def _js(builder: str) -> str:
    return rendered(getattr(library_page, builder)()).replace(_LS, "LS")


class TestBulkBar:
    def test_sticky_class_and_selection_label(self) -> None:
        js = _js("_bulk_bar")
        assert "LS.selected_tracks_rx_state_.length > 0" in js
        assert 'jsx(RadixThemesBox,{className:"card bulkbar"}' in js
        assert 'jsx("span",{className:"bulkbar-count"},LS.selection_label_rx_state_)' in js
        # Tag, Delete and Clear selection are still the actions.
        assert ".open_bulk_tag_dialog" in js and ".open_delete_confirm" in js
        assert '"aria-label":"Clear selection"' in js and ".clear_selection" in js

    def test_bar_comes_after_the_table_and_before_the_pager(self, content_js: str) -> None:
        table = content_js.index("switch (JSON.stringify(LS.table_view_rx_state_))")
        bar = content_js.index('className:"card bulkbar"')
        pager = content_js.index('"aria-label":"Previous page"')
        filters = content_js.index('jsx("form",{className:"filterbar"')
        assert filters < table < bar < pager
        assert content_js.count('className:"card bulkbar"') == 1


class TestTagDialog:
    @pytest.fixture(scope="class")
    def js(self) -> str:
        return _js("_bulk_tag_dialog")

    def test_scope_line_is_the_description(self, js: str) -> None:
        assert (
            'jsx(RadixThemesDialog.Description,{css:({ ["size"] : "2" })},'
            "LS.bulk_tag_scope_line_rx_state_)"
        ) in js

    def test_add_replace_radios_in_the_form(self, js: str) -> None:
        form = js[js.index('jsx("form",') :]
        assert 'jsx("fieldset",{"aria-labelledby":"lib-bulk-mode-legend"' in form
        assert 'role:"radiogroup"' in form
        radios = re.findall(r'jsx\("input",\{(checked:.*?type:"radio".*?)\},\)', form)
        assert len(radios) == 2
        assert all('name:"mode"' in r for r in radios)
        assert '.set_bulk_tag_mode", ({ ["mode"] : "add" })' in form
        assert '.set_bulk_tag_mode", ({ ["mode"] : "replace" })' in form
        assert '"Add to them"' in form and '"Replace them"' in form
        # Add is the state default (bulk_tag_mode = "add").
        assert 'LS.bulk_tag_mode_rx_state_?.valueOf?.() === "add"' in radios[0]

    def test_replace_count_is_a_status_region(self, js: str) -> None:
        region = re.search(
            r'jsx\(RadixThemesBox,\{(className:[^{}]*?id:"lib-bulk-existing"[^}]*)\}', js
        )
        assert region and 'role:"status"' in region.group(1)
        assert '"callout callout-warning lib-bulk-existing" : "lib-bulk-existing")' in region.group(
            1
        )
        assert "LS.bulk_tag_existing_line_rx_state_" in js

    def test_confirm_turns_danger_for_replace(self, js: str) -> None:
        confirm = re.search(r'jsx\("button",\{(className:.*?id:"lib-bulk-tags-confirm".*?)\},', js)
        assert confirm
        props = confirm.group(1)
        assert (
            'LS.bulk_tag_mode_rx_state_?.valueOf?.() === "replace"?.valueOf?.()) ? '
            '"btn btn-3 btn-danger" : "btn btn-3 btn-primary")'
        ) in props
        assert "disabled:LS.bulk_tag_confirm_blocked_rx_state_" in props
        assert 'type:"submit"' in props

    def test_empty_text_message_region(self, js: str) -> None:
        assert (
            'jsx(RadixThemesBox,{id:"lib-bulk-tags-error",ref:ref_lib_bulk_tags_error,role:"alert"}'
            in js
        )
        assert '"aria-describedby":"lib-bulk-tags-error"' in js
        assert 'jsx("p",{className:"field-hint"}' in js

    def test_form_still_submits_through_submit_bulk_tags(self) -> None:
        dialog = library_page._bulk_tag_dialog()
        hooks = "\n".join(dialog._get_all_hooks())
        assert ".submit_bulk_tags" in hooks and "new FormData($form).entries()" in hooks


class TestDeleteDialog:
    def test_total_offpage_and_consequence(self) -> None:
        js = _js("_delete_confirm_dialog")
        assert 'jsx("span",{className:"lib-dialog-line"},LS.delete_scope_line_rx_state_)' in js
        assert (
            '"Tracks still in Plex come back on the next sync, without their tags or '
            'embeddings. Your Plex library is not affected."'
        ) in js


class TestActions:
    @pytest.fixture(scope="class")
    def js(self) -> str:
        return _js("_actions")

    def test_sync_note_when_ai_is_configured(self, js: str) -> None:
        assert 'LS.sync_tagging_note_rx_state_?.valueOf?.() === ""' in js
        assert (
            'jsx("p",{className:"lib-sync-note",id:"lib-sync-note",ref:ref_lib_sync_note},'
            "LS.sync_tagging_note_rx_state_)"
        ) in js
        assert '"aria-describedby":"lib-sync-note",className:"btn btn-3 btn-primary"' in js

    def test_sync_error_region_is_persistent(self, js: str) -> None:
        region = js.index('id:"lib-sync-error-region",ref:ref_lib_sync_error_region,role:"alert"}')
        cond = js.index("LS.sync_error_rx_state_?.valueOf?.()")
        assert region < cond  # the alert region wraps (and outlives) the callout
        assert 'className:"callout callout-error",id:"lib-sync-error"' in js
        assert 'className:"c-title"},LS.sync_error_rx_state_)' in js
        assert "to:LS.sync_error_action_href_rx_state_" in js
        assert '"aria-label":"Dismiss sync error"' in js and ".dismiss_sync_error" in js

    def test_embed_notice_region_is_persistent(self, js: str) -> None:
        region = js.index(
            'id:"lib-embed-notice-region",ref:ref_lib_embed_notice_region,role:"status"}'
        )
        cond = js.index("LS.embed_all_notice_rx_state_?.valueOf?.()")
        assert region < cond
        for cls in ("callout callout-error", "callout callout-warning", "callout callout-info"):
            assert cls in js
        assert ".dismiss_embed_all_notice" in js

    def test_embed_notice_links_to_the_fix(self, js: str) -> None:
        """A mismatch notice links to the rebuild and to switching back (not a dead end)."""
        notice = js[js.index('id:"lib-embed-notice-region"') :]
        assert "to:LS.embed_all_notice_action_href_rx_state_" in notice
        assert "LS.embed_all_notice_action_label_rx_state_" in notice
        assert "to:LS.embed_all_notice_secondary_href_rx_state_" in notice
        assert "LS.embed_all_notice_secondary_label_rx_state_" in notice
        assert '"notice-links"' in notice

    def test_generate_embeddings_busy_while_counting(self, js: str) -> None:
        assert '"aria-busy":LS.embed_all_checking_rx_state_' in js
        assert ".generate_embeddings" in js

    def test_no_cli_text(self, js: str) -> None:
        assert "plexmix config" not in js and "Run:" not in js


class TestTableArea:
    @pytest.fixture(scope="class")
    def js(self) -> str:
        return _js("_table_area")

    def test_states(self, js: str) -> None:
        for case in ("rows", "loading", "error", "no_match"):
            assert f'case JSON.stringify("{case}"):' in js
        assert "default:" in js
        assert '"No tracks synced yet"' in js and '"No tracks match"' in js
        assert '"No tracks")' not in js  # the old ambiguous empty title is gone

    def test_loading_placeholder_is_a_skeleton(self, js: str) -> None:
        loading = js[
            js.index('case JSON.stringify("loading"):') : js.index('case JSON.stringify("error"):')
        ]
        assert '"aria-busy":"true",className:"tbl-wrap lib-skeleton"' in loading
        assert loading.count('className:"skeleton"') == 6
        assert '"Loading tracks\\u2026"' in loading
        assert "No tracks" not in loading

    def test_error_offers_retry(self, js: str) -> None:
        error = js[
            js.index('case JSON.stringify("error"):') : js.index('case JSON.stringify("no_match"):')
        ]
        assert 'role:"alert"' in error and "LS.tracks_error_rx_state_" in error
        assert ".retry_load_tracks" in error

    def test_no_match_clears_the_filter_form(self, js: str) -> None:
        no_match = js[js.index('case JSON.stringify("no_match"):') : js.index("default:")]
        button = re.search(r'jsx\("button",\{(.*?)\},"Clear filters"\)', no_match)
        assert button
        props = button.group(1)
        assert 'form:"lib-filters"' in props and '"data-clear-form":""' in props
        assert '.clear_filters", ({  }), ({ ["debounce"] : 300 })' in props
        assert 'type:"button"' in props

    def test_not_synced_offers_sync(self, js: str) -> None:
        default = js[js.index("default:") :]
        assert ".start_sync_or_confirm" in default and '"Sync library"' in default
        assert "disabled:LS.is_syncing_rx_state_" in default


class TestWholePage:
    def test_embed_all_confirmation_is_on_the_page(self, content_js: str) -> None:
        assert content_js.count('id:"embed-all-confirm"') == 1
        assert ".confirm_embed_all" in content_js and ".set_embed_all_open" in content_js

    def test_pager_count_waits_for_the_first_load(self, content_js: str) -> None:
        pager = content_js[content_js.index("LS.tracks_loaded_rx_state_?") :]
        assert pager.index("LS.total_filtered_tracks_rx_state_") < pager.index('"Previous page"')

    def test_one_primary_button_outside_dialogs(self, content_js: str) -> None:
        # Sync in the action bar; the empty state's Sync library is soft.
        assert content_js.count('className:"btn btn-3 btn-primary",disabled:LS.is_syncing') == 1


class TestCss:
    def test_change_4_section_is_token_only(self) -> None:
        css = _CSS.read_text(encoding="utf-8")
        start = css.index("/* ── change 4: library (webui-honest-status-and-first-run) ── */")
        end = css.index("/* ── end change 4: library ── */")
        section = css[start:end]
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(", section)
        assert (
            ".bulkbar { position: sticky; bottom: 16px; z-index: 2; box-shadow: var(--shadow-lg);"
        ) in section
        assert "flex-wrap: wrap" in section
        # Intrinsic layout only: no fixed pixel widths on the new classes.
        assert not re.search(r"\bwidth:\s*\d+px", section)
