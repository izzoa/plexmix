"""Render smoke: History cards, detail dialog, delete alert dialog and the cover swatch.

Runs against real Reflex in its own session: ``pytest tests/ui_render``.
"""

from __future__ import annotations

import re
from pathlib import Path

import reflex as rx

from plexmix.ui.components.playlist_cover import playlist_cover
from plexmix.ui.pages import history as history_page
from plexmix.ui.states.history_state import HistoryState
from tests.ui_render.conftest import rendered

_RESTORE = '"javascript_code"] : "window.pmFocus&&pmFocus.restore()"'


class _CoverProbeState(rx.State):
    """Minimal state so the cover binds to real int and string Vars."""

    variant: int = 3
    rows: list[dict[str, str]] = [{"cover_variant": "2"}]


def _cards_js() -> str:
    return rendered(rx.foreach(HistoryState.filtered_playlists, history_page._pl_card))


def _dialog_js() -> str:
    return rendered(history_page._detail_dialog())


def _buttons(js: str) -> list[str]:
    """Return each ``jsx("button",{props}`` props string (props never nest braces here)."""
    return re.findall(r'jsx\("button",\{(.*?)\},', js)


class TestPlaylistCover:
    def test_int_variant_is_decorative_with_four_quadrants(self) -> None:
        js = rendered(playlist_cover(4))
        assert js.startswith('jsx("span",{"aria-hidden":"true",className:"pl-cover cover-v4"}')
        assert js.count('jsx("span",{},)') == 4

    def test_int_variant_wraps_into_range(self) -> None:
        assert 'className:"pl-cover cover-v1"' in rendered(playlist_cover(7))

    def test_var_variants_concatenate_without_json_quotes(self) -> None:
        js = rendered(playlist_cover(_CoverProbeState.variant, class_name="result-cover"))
        assert '"aria-hidden":"true"' in js
        assert re.search(r'className:\("result-cover cover-v"\+[^()]*variant', js)
        assert "JSON.stringify" not in js  # a string Var must not become cover-v"2"
        rows = rendered(
            rx.foreach(_CoverProbeState.rows, lambda r: playlist_cover(r["cover_variant"]))
        )
        assert 'className:("pl-cover cover-v"+r_rx_state_?.["cover_variant"])' in rows
        assert "JSON.stringify" not in rows


class TestPlaylistCards:
    def test_card_is_a_button_in_a_list_item(self) -> None:
        js = _cards_js()
        # Keyed by playlist id (not the foreach index): a deleted playlist's card
        # node is removed, never re-pointed at its neighbour (focus return).
        assert 'jsx("li",{key:pl_rx_state_?.["id"]}' in js
        li = js.index('jsx("li",')
        button = js.index('jsx("button",')
        assert li < button
        props = _buttons(js)[0]
        assert 'type:"button"' in props
        assert '"aria-haspopup":"dialog"' in props
        assert 'id:("pl-card-"+pl_rx_state_?.["id"])' in props
        assert '"aria-labelledby":("pl-name-"+pl_rx_state_?.["id"])' in props
        assert '"aria-describedby":("pl-meta-"+pl_rx_state_?.["id"])' in props
        assert 'className:"card pl-card hover-lift"' in props
        assert "select_playlist" in props and '["playlist_id"] : pl_rx_state_?.["id"]' in props

    def test_label_and_description_targets_exist(self) -> None:
        js = _cards_js()
        assert 'className:"pl-name",id:("pl-name-"+pl_rx_state_?.["id"])' in js
        assert 'className:"pl-facts",id:("pl-meta-"+pl_rx_state_?.["id"])' in js

    def test_facts_from_real_data_and_omitted_when_missing(self) -> None:
        js = _cards_js()
        # Prompt in quotes, only when present.
        assert '"\\u201c",pl_rx_state_?.["mood_query"],"\\u201d"' in js
        assert '(!((pl_rx_state_?.["mood_query"]?.valueOf?.() === ""?.valueOf?.()))' in js
        # Mono meta: "N tracks · YYYY-MM-DD" (date only when known).
        assert 'className:"pl-meta"},pl_rx_state_?.["track_count"]' in js
        assert '" \\u00b7 ",pl_rx_state_?.["created_date"]' in js
        # Top artists only when there are any.
        assert 'className:"pl-artists"},pl_rx_state_?.["top_artists"]' in js
        assert '(!((pl_rx_state_?.["top_artists"]?.valueOf?.() === ""?.valueOf?.()))' in js

    def test_cover_and_overlay_are_hidden_and_promise_no_playback(self) -> None:
        js = _cards_js()
        assert 'className:("pl-cover cover-v"+pl_rx_state_?.["cover_variant"])' in js
        assert '{"aria-hidden":"true",className:"pl-overlay"}' in js
        assert "LucideListMusic" in js
        assert '"Open"' in js
        assert "LucidePlay" not in js
        assert '"play"' not in js

    def test_grid_is_a_list(self) -> None:
        js = rendered(history_page.history())
        assert re.search(r'jsx\("ul",\{className:"pl-grid",css:\(\{[^}]*\}\),role:"list"\}', js)


class TestDetailDialog:
    def test_is_a_radix_dialog_with_title_and_description(self) -> None:
        js = _dialog_js()
        assert js.startswith("jsx(RadixThemesDialog.Root,{")
        assert "onOpenChange:" in js and "set_detail_modal_open" in js
        assert "open:" in js and "is_detail_modal_open" in js
        assert re.search(
            r'RadixThemesDialog\.Title,\{className:"pl-dialog-title"\},[^)]*\["name"\]', js
        )
        assert "RadixThemesDialog.Description" in js
        assert "modal-backdrop" not in js

    def test_description_has_prompt_count_duration_and_date(self) -> None:
        js = _dialog_js()
        desc = js[js.index("RadixThemesDialog.Description") :]
        desc = desc[: desc.index("RadixThemesDialog.Close")]
        for key in ("mood_query", "track_count", "total_duration_formatted", "created_date"):
            assert f'["{key}"]' in desc, key

    def test_close_button_is_named_and_restores_focus(self) -> None:
        js = _dialog_js()
        assert (
            'jsx(RadixThemesDialog.Close,{},jsx("button",{"aria-label":"Close",'
            'className:"icon-btn",type:"button"}'
        ) in js
        assert "onCloseAutoFocus:" in js and _RESTORE in js

    def test_tracks_are_an_ordered_list(self) -> None:
        js = _dialog_js()
        assert 'jsx("ol",{"aria-label":"Tracks",className:"modal-body pl-tracks",role:"list"}' in js
        assert 'jsx("li",{className:"pl-trk",key:' in js
        assert 'className:"pos"},track_rx_state_?.["position"]' in js
        assert 'className:"dur"},track_rx_state_?.["duration_formatted"]' in js

    def test_reorder_controls_are_named_identified_and_bounded(self) -> None:
        js = _dialog_js()
        up = next(b for b in _buttons(js) if "move_track_up" in b)
        down = next(b for b in _buttons(js) if "move_track_down" in b)
        assert '"aria-label":(("Move "+track_rx_state_?.["title"])+" up")' in up
        assert '"aria-label":(("Move "+track_rx_state_?.["title"])+" down")' in down
        assert 'id:("hist-move-up-"+(JSON.stringify(index_rx_state_)))' in up
        assert 'id:("hist-move-down-"+(JSON.stringify(index_rx_state_)))' in down
        assert "disabled:(index_rx_state_?.valueOf?.() === 0?.valueOf?.())" in up
        assert "selected_track_count_rx_state_ - 1" in down
        assert "ref_hist_move" not in js  # Var ids must not produce bogus refs

    def test_reorder_announcement_is_a_polite_status(self) -> None:
        js = _dialog_js()
        assert re.search(
            r'jsx\("div",\{"aria-atomic":"true","aria-live":"polite",className:"sr-only",'
            r'id:"hist-reorder-status",ref:ref_hist_reorder_status,role:"status"\},'
            r"[^)]*reorder_announcement",
            js,
        )

    def test_no_button_is_unnamed(self) -> None:
        js = _dialog_js()
        buttons = _buttons(js)
        named_by_label = [b for b in buttons if '"aria-label":' in b]
        assert len(named_by_label) == 3  # Close, Move up, Move down
        for text in ("Rerun", "Export to Plex", "Export M3U", "Export JSON", "Save order"):
            assert re.search(r"\}," + r"jsx\(Lucide\w+,\{size:16\},\),\"" + text + '"', js), text
        assert '"Delete")' in js
        assert len(buttons) == 3 + 6

    def test_footer_delete_is_danger_and_opens_a_dialog(self) -> None:
        js = _dialog_js()
        delete = next(b for b in _buttons(js) if "show_delete_confirmation" in b)
        assert 'className:"btn btn-3 btn-danger"' in delete
        assert '"aria-haspopup":"dialog"' in delete


class TestDeleteConfirm:
    def test_is_an_alert_dialog_with_safe_cancel_and_danger_delete(self) -> None:
        js = rendered(history_page._delete_confirm_dialog())
        assert js.startswith("jsx(RadixThemesAlertDialog.Root,{")
        assert "set_delete_confirmation_open" in js and "is_delete_confirmation_open" in js
        assert (
            'jsx(RadixThemesAlertDialog.Cancel,{},jsx("button",'
            '{className:"btn btn-3 btn-soft",type:"button"},"Cancel"))'
        ) in js
        delete = next(b for b in _buttons(js) if "confirm_delete" in b)
        assert 'className:"btn btn-3 btn-danger"' in delete
        # Confirm closes by state, not via alert_dialog.action (design D3).
        assert "AlertDialog.Action" not in js
        assert "onCloseAutoFocus:" in js and _RESTORE in js
        assert "RadixThemesAlertDialog.Title" in js
        assert "RadixThemesAlertDialog.Description" in js
        assert '["size"]' not in js  # no props leaked into CSS


class TestHistoryPage:
    def test_filters_are_named(self) -> None:
        js = rendered(history_page.history())
        assert 'jsx("input",{"aria-label":"Search playlists",' in js
        sort = next(b for b in _buttons(js) if "toggle_sort_order" in b)
        assert re.search(r'"aria-label":[\w.]*sort_button_label_rx_state_', sort)

    def test_single_level_one_heading_from_layout(self) -> None:
        js = rendered(history_page.history())
        # The page adds no heading of its own: the shell's sr-only h1 is the only one.
        assert js.count('jsx("h1",') == 1
        assert js.count('id:"main-heading"') == 1
        # One shell main landmark (the login branch of the auth cond has its own).
        assert js.count('id:"main-content"') == 1

    def test_source_has_no_hard_coded_cover_colors(self) -> None:
        source = Path(history_page.__file__).read_text(encoding="utf-8")
        for needle in ("_COVER_GRADIENTS", "#e94560", "#f39c12", "#b55f18"):
            assert needle not in source
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)


class TestSearchFormClientOwned:
    """History search is a client-owned field in a form (webui-input-responsiveness 7.3)."""

    @staticmethod
    def _search_props(js: str) -> str:
        start = js.index('jsx("input",{') + len('jsx("input",{')
        return js[start : js.index("},)", start)]

    def test_filter_bar_is_a_form_that_submits_on_enter(self) -> None:
        comp = history_page._filter_bar()
        js = rendered(comp)
        assert js.startswith(
            'jsx("form",{className:"filterbar","data-enter-submit":"",onSubmit:handleSubmit_'
        )
        hooks = "".join(comp._get_all_hooks())
        assert "new FormData($form)" in hooks
        assert ".apply_search_form" in hooks and '["form_data"] : form_data' in hooks

    def test_search_is_uncontrolled_debounced_and_named_q(self) -> None:
        js = rendered(history_page._filter_bar())
        assert js.count('jsx("input",{') == 1
        props = self._search_props(js)
        assert props.startswith('"aria-label":"Search playlists",')
        assert not re.search(r'(?<![\w"])value:', props)
        assert re.search(r"defaultValue:[\w.]*\.search_query_rx_state_", props)
        assert 'id:"hist-search"' in props and "ref:ref_hist_search" in props
        assert 'name:"q"' in props
        assert 'placeholder:"Search playlists\\u2026"' in props
        on_change = props[props.index("onChange:") : props.index("placeholder:")]
        assert '.set_search_query"' in on_change
        assert '({ ["debounce"] : 300 })' in on_change
        on_blur = props[props.index("onBlur:") : props.index("onChange:")]
        assert '.set_search_query"' in on_blur and "debounce" not in on_blur
        assert 'autoComplete:"off"' in props and "spellCheck:false" in props

    def test_sort_toggle_never_submits_the_form(self) -> None:
        js = rendered(history_page._filter_bar())
        buttons = _buttons(js)
        assert len(buttons) == 1
        assert "toggle_sort_order" in buttons[0]
        assert 'type:"button"' in buttons[0]
        assert 'type:"submit"' not in js


class TestLoadingErrorAndEmptyStates:
    """Loading is not empty; failures stay in place; two different empty states (11.1)."""

    @staticmethod
    def _page() -> str:
        return rendered(history_page.history())

    def test_skeleton_while_nothing_is_listed_yet(self) -> None:
        js = rendered(history_page._playlist_area())
        grid = js.index('jsx("ul",{className:"pl-grid"')
        skeleton = js.index('className:"pl-grid hist-skeleton"')
        loading = js.index("show_loading_rx_state_")
        # Cards first when there are any; the placeholder while loading; empty last.
        assert grid < loading < skeleton < js.index('"No playlists match your search"')
        assert js.index('"No playlists match your search"') < js.index('"No playlists yet"')
        skel = js[skeleton - 40 : js.index('className:"empty"', skeleton)]
        assert '"aria-hidden":"true",className:"pl-grid hist-skeleton"' in skel
        assert skel.count('className:"skeleton hist-skel-art"') == 6
        # Placeholders are never cards or empty states (the integrity probe samples both).
        assert "pl-card" not in skel and '"empty"' not in skel

    def test_failed_load_renders_nothing_in_the_grid_area(self) -> None:
        js = rendered(history_page._playlist_area())
        assert js.startswith("jsx(Fragment,{},(")
        first = js[: js.index('jsx("ul",{className:"pl-grid"')]
        assert "load_failed_rx_state_" in first

    def test_error_region_is_a_persistent_alert_with_retry(self) -> None:
        js = rendered(history_page._error_region())
        assert js.startswith(
            'jsx("div",{css:({ ["width"] : "100%" }),id:"hist-error-region",'
            'ref:ref_hist_error_region,role:"alert"}'
        )
        assert '"Couldn\'t load your playlists")' in js
        assert "error_message_rx_state_" in js
        retry = next(b for b in _buttons(js) if "retry_load" in b)
        assert 'type:"button"' in retry and "btn btn-sm btn-soft" in retry
        dismiss = next(b for b in _buttons(js) if "dismiss_error" in b)
        assert 'type:"button"' in dismiss
        assert '"callout callout-error"' in js and '"callout callout-warning"' in js

    def test_no_playlists_offers_the_generator(self) -> None:
        js = rendered(history_page._no_playlists())
        assert '"No playlists yet"' in js
        assert re.search(
            r'jsx\(ReactRouterLink,\{className:"btn btn-3 btn-primary hist-empty-action",'
            r'to:"/generator"\}.*"Open Generator"\)',
            js,
        )

    def test_no_matches_offers_to_clear_the_search(self) -> None:
        js = rendered(history_page._no_matches())
        assert '"No playlists match your search"' in js
        assert "search_query_rx_state_" in js
        clear = next(b for b in _buttons(js) if "clear_search" in b)
        assert 'type:"button"' in clear
        assert '"Clear search")' in js

    def test_loading_is_announced_in_a_persistent_status_region(self) -> None:
        js = self._page()
        assert re.search(
            r'jsx\("div",\{className:"sr-only",id:"hist-load-status",ref:ref_hist_load_status,'
            r'role:"status"\},\([\w.]*show_loading_rx_state_ \? "Loading playlists\\u2026" : ""\)',
            js,
        )
        assert '"Loading playlists\\u2026"' in js

    def test_page_order_filter_status_error_grid_dialogs(self) -> None:
        js = self._page()
        order = [
            js.index('jsx("form",{className:"filterbar"'),
            js.index('id:"hist-load-status"'),
            js.index('id:"hist-error-region"'),
            js.index('jsx("ul",{className:"pl-grid"'),
            js.index("set_detail_modal_open"),
        ]
        assert order == sorted(order)
