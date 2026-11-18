"""Render smoke: Dashboard structure, setup checklist, coverage, status strip and recent rows.

Runs against real Reflex in its own session: ``pytest tests/ui_render``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plexmix.ui.pages import dashboard as dashboard_page
from plexmix.ui.pages.dashboard import dashboard
from tests.ui_render.conftest import rendered

_RESTORE = '"javascript_code"] : "window.pmFocus&&pmFocus.restore()"'


@pytest.fixture(scope="module")
def js() -> str:
    return rendered(dashboard())


def _section(js: str, label_id: str) -> str:
    """The compiled ``section`` labelled by ``label_id`` (up to the next h2 section)."""
    start = js.index(f'jsx("section",{{"aria-labelledby":"{label_id}"')
    return js[start:]


def _buttons(js: str) -> list[str]:
    return re.findall(r'jsx\("button",\{(.*?)\},', js)


class TestDashboardStructure:
    def test_heading_order(self, js: str) -> None:
        h1 = js.index('jsx("h1",{className:"sr-only",id:"main-heading"')
        setup = js.index('"Set up PlexMix")')
        overview = js.index('"Library overview")')
        quick = js.index('"Quick Actions")')
        recent = js.index('"Recent Playlists")')
        assert h1 < setup < overview < quick < recent
        assert js.count('jsx("h1",') == 1
        levels = re.findall(r'jsx\("(h[1-6])",', js)
        # h1, the checklist h2 with its five step h3s, then the three page h2s.
        assert levels == ["h1", "h2", "h3", "h3", "h3", "h3", "h3", "h2", "h2", "h2"]

    def test_overview_section_is_labelled_by_sr_only_h2(self, js: str) -> None:
        assert js.count('jsx("section",') == 2  # setup checklist + library overview
        assert 'jsx("section",{"aria-labelledby":"dash-overview"' in js
        assert 'jsx("h2",{className:"sr-only",id:"dash-overview"' in js

    def test_overview_wraps_readout_tiles_status_and_warning(self, js: str) -> None:
        section = _section(js, "dash-overview")
        section = section[: section.index('"Quick Actions")')]
        assert section.index('"Library overview")') < section.index('id:"dash-library-readout"')
        assert section.index('id:"dash-library-readout"') < section.index("stat-grid cov-grid")
        assert section.index("stat-grid cov-grid") < section.index('className:"card statusbar"')
        assert "embedding_dimension_warning" in section
        assert '"callout callout-warning"' in section

    def test_recent_playlists_are_a_list_of_links_to_history(self, js: str) -> None:
        assert js.count('jsx("ul",') == 1
        ul = js[js.index('jsx("ul",') :]
        assert ul.startswith('jsx("ul",{className:"card recent-list"')
        # Explicit list role: WebKit drops list semantics for list-style: none.
        assert 'role:"list"' in ul[: ul.index('jsx("li",')]
        assert "recent_playlists" in ul
        # Keyed rows; each row is a router link to that playlist's History detail.
        assert 'jsx("li",{key:playlist_rx_state_?.["id"]},jsx(ReactRouterLink,' in ul
        assert (
            'className:"recent-link hover-row",to:("/history?playlist="+playlist_rx_state_?.["id"])'
            in ul
        )
        # Date without seconds, the year-qualified time in title.
        assert (
            'title:playlist_rx_state_?.["created_title"]},playlist_rx_state_?.["created_label"]'
            in ul
        )
        assert '["created_at"]' not in ul

    def test_no_terminal_commands_or_old_labels(self, js: str) -> None:
        assert "plexmix config" not in js
        assert "MB Enriched" not in js
        assert "Total Tracks" not in js

    def test_source_uses_tokens_only(self) -> None:
        source = Path(dashboard_page.__file__).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)
        assert "rgb(" not in source and "rgba(" not in source


class TestSetupChecklist:
    def test_shown_first_while_setup_is_incomplete(self, js: str) -> None:
        cond = js.index("setup_incomplete_rx_state_")
        assert cond < js.index('"Set up PlexMix")') < js.index('"Library overview")')

    def test_section_has_a_visible_h2_and_an_ordered_list(self, js: str) -> None:
        section = _section(js, "dash-setup")
        assert section.startswith(
            'jsx("section",{"aria-labelledby":"dash-setup",className:"card setup"'
        )
        assert 'jsx("h2",{className:"setup-h",id:"dash-setup"' in section
        ol = section[section.index('jsx("ol",') :]
        assert ol.startswith('jsx("ol",{className:"setup-steps",role:"list"}')
        overview = ol.index('"Library overview")')
        assert ol[:overview].count('jsx("li",') == 5

    def test_each_step_has_an_h3_and_marks_the_current_step(self, js: str) -> None:
        for index, step_id in enumerate(("plex", "providers", "sync", "index", "vibe")):
            assert f'jsx("h3",{{className:"setup-title",id:"setup-{step_id}-title"' in js
            assert (
                f'setup_steps_rx_state_?.at?.({index})?.["state"]?.valueOf?.() === '
                f'"current"?.valueOf?.()) ? "step" : null)'
            ) in js
            assert 'className:("setup-step is-"+' in js

    def test_only_the_current_step_is_primary(self, js: str) -> None:
        # Every step button/link takes btn-primary only from its step's "primary".
        hits = re.findall(
            r'\["primary"\]\?\.valueOf\?\.\(\) === "true"\?\.valueOf\?\.\(\)\) \? '
            r'"btn btn-3 btn-primary" : "btn btn-3 btn-soft"',
            js,
        )
        assert len(hits) == 5
        # No other unconditional primary button on the page.
        assert 'className:"btn btn-3 btn-primary"' not in js

    def test_generate_is_primary_only_once_setup_is_complete(self, js: str) -> None:
        assert re.search(
            r'className:\([\w.]*generate_primary_rx_state_ \? "card qa primary hover-lift" : '
            r'"card qa hover-lift"\)',
            js,
        )

    def test_connect_and_providers_link_to_settings_sections(self) -> None:
        # The hrefs come from readiness.setup_steps (/settings?section=plex|embedding).
        js = rendered(dashboard_page._step_link(dashboard_page.DashboardState.setup_steps[0]))
        assert js.startswith("jsx(Fragment,{},(!((")
        assert "jsx(ReactRouterLink,{className:" in js
        assert "to:reflex___state" in js and '["href"]' in js

    def test_sync_runs_an_incremental_sync_told_to_the_poller_first(self, js: str) -> None:
        sync = next(b for b in _buttons(js) if "start_sync" in b)
        order = [
            sync.index('set_sync_mode", ({ ["mode"] : "incremental" })'),
            sync.index('watch_setup_job", ({ ["job"] : "sync" })'),
            sync.index("library_state.start_sync"),
        ]
        assert order == sorted(order)
        assert 'type:"button"' in sync

    def test_build_index_asks_for_confirmation(self, js: str) -> None:
        build = next(b for b in _buttons(js) if "request_embed_all" in b)
        assert '"aria-haspopup":"dialog"' in build
        assert re.search(r'"aria-busy":[\w.]*embed_all_checking_rx_state_', build)
        assert 'type:"button"' in build

    def test_running_steps_show_a_named_progress_bar_and_cancel(self, js: str) -> None:
        for step_id, index in (("sync", 2), ("index", 3)):
            assert re.search(
                r'jsx\(RadixThemesBox,\{"aria-labelledby":"setup-' + step_id + r'-msg",'
                r'"aria-valuemax":"100","aria-valuemin":"0","aria-valuenow":[\w.]*setup_steps'
                r"_rx_state_\?\.at\?\.\(" + str(index) + r'\)\?\.\["progress"\],',
                js,
            ), step_id
            assert f'id:"setup-{step_id}-msg"' in js
        assert 'role:"progressbar"' in js
        sync_cancel = next(b for b in _buttons(js) if '"Cancel library sync"' in b)
        assert 'request_cancel", ({ ["job_type"] : "sync" })' in sync_cancel
        index_cancel = next(b for b in _buttons(js) if '"Cancel building the search index"' in b)
        assert 'request_cancel", ({ ["job_type"] : "embedding" })' in index_cancel

    def test_persistent_alert_and_status_regions(self, js: str) -> None:
        for step_id in ("sync", "index"):
            assert re.search(
                r'jsx\("div",\{className:"setup-error",id:"setup-' + step_id + r'-error",'
                r"ref:ref_setup_" + step_id + r'_error,role:"alert"\}',
                js,
            ), step_id
        assert re.search(
            r'jsx\("div",\{id:"setup-index-notice",ref:ref_setup_index_notice,role:"status"\}'
            r",[^)]*embed_all_notice",
            js,
        )
        dismiss = next(b for b in _buttons(js) if "dismiss_setup_error" in b)
        assert 'type:"button"' in dismiss

    def test_poller_is_mounted_only_while_a_setup_job_runs(self, js: str) -> None:
        poll = js.index("poll_setup_progress")
        moment = js.rindex("Moment,{interval:2000", 0, poll)
        cond = js.rindex("setup_polling_rx_state_", 0, moment)
        assert cond < moment < poll
        assert js.count("poll_setup_progress") == 1


class TestEmbedAllDialog:
    def test_confirmation_is_the_shared_alert_dialog_bound_to_library_state(self, js: str) -> None:
        root = js[js.index("jsx(RadixThemesAlertDialog.Root,") :]
        assert "set_embed_all_open" in root[:400] and "embed_all_open_rx_state_" in root[:900]
        assert 'id:"embed-all-confirm"' in root
        assert (
            'jsx(RadixThemesAlertDialog.Cancel,{},jsx("button",{className:"btn btn-3 btn-soft",'
            'id:"embed-all-confirm-cancel"'
        ) in root
        assert "AlertDialog.Action" not in root
        assert _RESTORE in root

    def test_confirm_tells_the_poller_before_starting_the_job(self, js: str) -> None:
        confirm = next(b for b in _buttons(js) if 'id:"embed-all-confirm-confirm"' in b)
        watch = confirm.index('watch_setup_job", ({ ["job"] : "embedding" })')
        start = confirm.index("library_state.confirm_embed_all")
        assert watch < start


class TestCoverage:
    def test_four_readouts_with_labelled_meters(self, js: str) -> None:
        for tile_id, label in (
            ("tagged", "Tagged"),
            ("embedded", "Embedded"),
            ("audio", "Audio analyzed"),
            ("musicbrainz", "MusicBrainz genres"),
        ):
            assert f'id:"cov-{tile_id}-label",ref:ref_cov_{tile_id}_label}},"{label}")' in js
            assert re.search(
                r'jsx\(RadixThemesBox,\{"aria-labelledby":"cov-' + tile_id + r'-label",'
                r'"aria-valuemax":"100","aria-valuemin":"0","aria-valuenow":[^,]*coverage'
                r'_rx_state_\?\.\["' + tile_id + r'"\]\?\.\["width"\],"aria-valuetext":',
                js,
            ), tile_id
        assert js.count('className:"pbar cov-meter"') == 4

    def test_counts_and_shares_use_the_mono_classes(self, js: str) -> None:
        assert 'jsx("span",{className:"cov-num"},' in js
        assert 'jsx("span",{className:"cov-pct"},' in js

    def test_links_are_named(self, js: str) -> None:
        links = re.findall(
            r'jsx\(ReactRouterLink,\{"aria-label":[^,]*\["link_aria"\],' r'className:"cov-link"', js
        )
        assert len(links) == 4

    def test_skeletons_until_the_stats_have_loaded(self, js: str) -> None:
        grid = js.index('className:"stat-grid cov-grid"')
        assert "library_stats_loaded_rx_state_" in js[js.index('id:"dash-library-readout"') : grid]
        start = js.index('"aria-busy":"true",className:"stat-grid cov-grid"')
        skel = js[start : js.index('className:"card statusbar"', start)]
        assert skel.count('className:"skeleton cov-skel-num"') == 4
        assert skel.count('"aria-hidden":"true",className:"tile cov-tile"') == 4
        # The placeholders hold no figures at all: never a zero before the real value.
        assert "coverage_rx_state_" not in skel
        assert "stat_total" not in skel

    def test_header_readout_has_the_absolute_time_on_the_same_element(self, js: str) -> None:
        assert re.search(
            r'jsx\("time",\{className:"cov-sync",dateTime:[\w.]*last_sync_at_rx_state_,'
            r"title:[\w.]*last_sync_title_rx_state_\},[\w.]*sync_readout_rx_state_\)",
            js,
        )
        assert 'className:"cov-total"},' in js and "library_count_rx_state_" in js


class TestStatusStrip:
    def test_three_items_from_readiness(self, js: str) -> None:
        for component in ("plex", "ai", "embeddings"):
            assert f'id:"dash-status-{component}"' in js
            assert (
                '{"aria-hidden":"true",className:("dot dot-"+'
                f"reflex___state____state__plexmix___ui___states___app_state____app_state."
                f'readiness_rx_state_?.["{component}"]?.["tone"])}}'
            ) in js

    def test_state_words_cause_and_last_check(self, js: str) -> None:
        for key in ("state", "label", "model", "title", "checked_ago"):
            assert f'?.["ai"]?.["{key}"]' in js, key
        assert re.search(
            r'title:[\w.]*readiness_rx_state_\?\.\["plex"\]\?\.\["checked_title"\]', js
        )
        assert 'className:"mono st-model"' in js

    def test_action_link_is_named_by_action_and_component(self, js: str) -> None:
        assert re.search(
            r'jsx\(ReactRouterLink,\{"aria-label":\(\([\w.]*readiness_rx_state_\?\.\["ai"\]\?\.'
            r'\["action_label"\]\+" "\)\+"AI provider"\),className:"st-action",to:[\w.]*'
            r'readiness_rx_state_\?\.\["ai"\]\?\.\["action_href"\]\}',
            js,
        )

    def test_skeleton_until_loaded(self, js: str) -> None:
        assert '{"aria-busy":"true",className:"card statusbar"}' in js
        assert js.count('className:"skeleton st-skel"') == 3


class TestReviewFixes:
    """Mismatch callout actions, the unreadable-settings state, notice links."""

    def test_dimension_warning_offers_rebuild_and_switch_back(self, js: str) -> None:
        start = js.index('className:"callout callout-warning",id:"dash-dimension-warning"')
        callout = js[start : js.index('"Quick Actions")', start)]
        assert "embedding_dimension_warning" in callout
        for key in ("fix_label", "action_href", "secondary_label", "secondary_href"):
            assert f'readiness_rx_state_?.["index"]?.["{key}"]' in callout, key
        assert '"notice-links"' in callout
        assert "⚠" not in callout

    def test_unreadable_settings_replace_the_readout_and_tiles(self, js: str) -> None:
        section = _section(js, "dash-overview")
        cond = section.index("settings_unreadable_rx_state_")
        error = section.index('id:"dash-settings-error"')
        readout = section.index('id:"dash-library-readout"')
        assert cond < error < readout  # the error is the true branch, the readout the false one
        assert '"PlexMix couldn\'t read its settings"' in section
        assert re.search(
            r'jsx\(ReactRouterLink,\{className:"btn btn-sm btn-soft",to:"/settings"\}', section
        )
        retry = next(b for b in _buttons(section) if "dashboard_state.on_load" in b)
        assert 'type:"button"' in retry

    def test_setup_index_notice_carries_its_links(self, js: str) -> None:
        notice = js[js.index('id:"setup-index-notice"') :]
        notice = notice[: notice.index('id:"setup-index-error"')]
        assert "embed_all_notice_action_href_rx_state_" in notice
        assert "embed_all_notice_secondary_href_rx_state_" in notice
        assert '"notice-links"' in notice
