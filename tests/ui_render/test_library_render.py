"""Render smoke: Library sort headers, enrichment column, names, progress bars, dialogs.

Runs against real Reflex only (see tests/ui_render/conftest.py for the JSX format).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import reflex as rx

from plexmix.ui.pages import library as library_page
from plexmix.ui.states.library_state import LibraryState
from plexmix.ui.utils.a11y import aria_sort_value
from tests.ui_render.conftest import rendered

_SIZE_CLASSES = {"btn-3", "btn-4", "btn-sm", "btn-icon"}
_CSS = Path(__file__).resolve().parents[2] / "assets" / "redesign.css"


@pytest.fixture(scope="module")
def content_js() -> str:
    """The Library page content without the app shell (layout is bypassed)."""
    original = library_page.layout
    library_page.layout = lambda content, **_kw: content  # type: ignore[assignment]
    try:
        return rendered(library_page.library())
    finally:
        library_page.layout = original


@pytest.fixture(scope="module")
def page_js() -> str:
    return rendered(library_page.library())


@pytest.fixture(scope="module")
def table_js() -> str:
    return rendered(library_page._track_table())


_BUTTON = re.compile(r'jsx\("button",\{(.*?)\},')


def _buttons(js: str) -> list[tuple[str, str]]:
    """Return (props, start of children) for every native <button>, by position."""
    return [(m.group(1), js[m.end() : m.end() + 400]) for m in _BUTTON.finditer(js)]


# A text child chosen by rx.cond: ``(<condition> ? "A" : "B")``.
_COND_TEXT = re.compile(r'\(.{1,300}? \? "[^"]+" : "[^"]+"\)')
# A text child read from state (e.g. the scope confirmation's confirm label).
_STATE_TEXT = re.compile(r"reflex___state\w*\.\w+_rx_state_")


def _is_named(props: str, children: str) -> bool:
    """True when a button has an aria-label or a text child (after an optional icon).

    The text child may be a literal, an ``rx.cond`` between two literals (the
    Tag dialog's "Add tags" / "Replace tags") or a state var (the embed-all
    confirmation's label).
    """
    has_text = bool(re.match(r'(jsx\(Lucide\w+,\{[^}]*\},\),)?"[^"]+"', children))
    has_text = has_text or bool(_COND_TEXT.match(children) or _STATE_TEXT.match(children))
    return has_text or '"aria-label":' in props


class TestSortHeaders:
    def test_three_sort_buttons_with_aria_sort(self, table_js: str) -> None:
        assert table_js.count('"aria-sort":') == 3
        assert table_js.count('className:"th-sort"') == 3
        assert table_js.count('jsx("th",{"aria-sort":') == 3

    def test_each_header_is_a_typed_button_wired_to_set_sort(self, table_js: str) -> None:
        for label, column in (("Title", "title"), ("Artist", "artist"), ("Album", "album")):
            js = rendered(library_page._sort_header(label, column))
            assert js.startswith('jsx("th",{"aria-sort":')
            assert 'jsx("button",{className:"th-sort",onClick:' in js
            assert 'type:"button"' in js
            assert f'"{label}"' in js
            assert f'.set_sort", ({{ ["column"] : "{column}" }})' in js
        # The table has no other sortable header and no unwired one.
        assert table_js.count(".set_sort") == 3

    def test_aria_sort_is_the_three_way_value(self) -> None:
        js = rendered(library_page._sort_header("Artist", "artist"))
        aria = js[js.index('"aria-sort":') : js.index('jsx("button"')]
        # sorted column ? (ascending ? "ascending" : "descending") : "none"
        assert 'sort_column_rx_state_?.valueOf?.() === "artist"' in aria
        assert re.search(
            r'sort_ascending_rx_state_ \? "ascending" : "descending"\) : "none"\)', aria
        )
        # Same contract as the pure helper the unit tests cover.
        assert aria_sort_value("artist", "artist", True) == "ascending"
        assert aria_sort_value("artist", "artist", False) == "descending"
        assert aria_sort_value("artist", "title", True) == "none"

    def test_sort_icons_are_hidden(self) -> None:
        js = rendered(library_page._sort_header("Album", "album"))
        icons = re.findall(r"jsx\((Lucide\w+),\{([^}]*)\}", js)
        assert {name for name, _ in icons} == {
            "LucideArrowUp",
            "LucideArrowDown",
            "LucideChevronsUpDown",
        }
        assert all('"aria-hidden":"true"' in props for _, props in icons)


class TestEnrichmentColumn:
    def test_header_and_cell(self, table_js: str) -> None:
        assert 'jsx("th",{},"Enrichment")' in table_js
        assert 'jsx("span",{className:"enr-set"}' in table_js
        assert 'jsx("span",{className:"sr-only"},track_rx_state_?.["enr_label"])' in table_js

    def test_four_hidden_chips_with_titles_and_state_classes(self, table_js: str) -> None:
        chips = re.findall(
            r'jsx\("span",\{"aria-hidden":"true",className:\((.*?)\),title:', table_js
        )
        assert len(chips) == 4
        for key in ("enr_tags", "enr_embedded", "enr_audio", "enr_mb"):
            assert f'track_rx_state_?.["{key}"]?.valueOf?.() === "1"' in table_js
        assert table_js.count('? "enr enr-on" : "enr enr-off"') == 4
        for on_title, off_title in (
            ("Tagged", "Not tagged"),
            ("Embedded", "Not embedded"),
            ("Audio analyzed", "Audio not analyzed"),
            ("MusicBrainz matched", "MusicBrainz not matched"),
        ):
            assert f'? "{on_title}" : "{off_title}"' in table_js

    def test_chip_icons(self, table_js: str) -> None:
        for icon in ("LucideTags", "LucideCpu", "LucideAudioWaveform", "LucideDisc"):
            assert f"jsx({icon},{{size:12}}" in table_js

    def test_css_section_is_token_only(self) -> None:
        css = _CSS.read_text(encoding="utf-8")
        start = css.index("/* ── change 2: library table")
        end = css.index("/* ── end change 2: library table ── */")
        section = css[start:end]
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(", section)
        assert ".enr-on { background: var(--success-bg); color: var(--success-fg); }" in section
        assert "border: 1px dashed var(--border-strong); color: var(--fg-3);" in section
        assert ".th-sort:hover { color: var(--fg-1); }" in section
        assert "@media (forced-colors: active)" in section


class TestNames:
    def test_every_checkbox_is_named(self, table_js: str) -> None:
        boxes = re.findall(r"jsx\(RadixThemesCheckbox,\{(.*?)\},", table_js)
        assert len(boxes) == 2
        assert all(props.startswith('"aria-label":') for props in boxes)
        assert '"aria-label":"Select all tracks on this page"' in table_js
        assert (
            '"aria-label":((("Select "+track_rx_state_?.["title"])+" by ")'
            '+track_rx_state_?.["artist_name"])'
        ) in table_js

    def test_filters_and_sync_mode_are_named(self, content_js: str) -> None:
        for name in (
            "Sync mode",
            "Search tracks",
            "Filter by genre",
            "Clear filters",
            "Clear selection",
            "Tags to apply",
        ):
            assert f'"aria-label":"{name}"' in content_js, name
        assert re.search(r'jsx\("select",\{"aria-label":"Sync mode"', content_js)

    def test_pagination_named_and_bounded(self, content_js: str) -> None:
        prev = re.search(r'jsx\("button",\{"aria-label":"Previous page",(.*?)\},', content_js)
        nxt = re.search(r'jsx\("button",\{"aria-label":"Next page",(.*?)\},', content_js)
        assert prev and nxt
        assert "disabled:(" in prev.group(1) and "current_page_rx_state_ <= 1" in prev.group(1)
        assert "disabled:!" in nxt.group(1) and "has_next_page_rx_state_" in nxt.group(1)
        # Ids the state targets when a bound disables the activated button.
        assert 'id:"lib-page-prev"' in prev.group(1)
        assert 'id:"lib-page-next"' in nxt.group(1)

    def test_no_icon_only_button_lacks_a_name(self, content_js: str) -> None:
        buttons = _buttons(content_js)
        assert len(buttons) >= 20
        for props, children in buttons:
            assert _is_named(props, children), props[:120]

    def test_name_check_catches_an_unnamed_icon_button(self) -> None:
        """Negative control for the check above (identical props, one unnamed)."""
        js = rendered(
            rx.box(
                rx.el.button(rx.icon("x", size=14), "Cancel", class_name="btn", type="button"),
                rx.el.button(rx.icon("chevron-left", size=16), class_name="btn", type="button"),
            )
        )
        assert [_is_named(p, c) for p, c in _buttons(js)] == [True, False]
        cond = rendered(
            rx.el.button(
                rx.cond(LibraryState.is_syncing, "Busy", "Idle"), class_name="btn", type="button"
            )
        )
        assert [_is_named(p, c) for p, c in _buttons(cond)] == [True]

    def test_repeated_cancel_buttons_name_their_job(self, content_js: str) -> None:
        for label in (
            "Cancel library sync",
            "Cancel embedding generation",
            "Cancel MusicBrainz enrichment",
            "Cancel audio analysis",
        ):
            assert f'"aria-label":"{label}"' in content_js


class TestJobProgressBars:
    def test_four_progressbars_labelled_by_their_message(self, content_js: str) -> None:
        bars = re.findall(
            r'jsx\(RadixThemesBox,\{("aria-labelledby".*?role:"progressbar")\}', content_js
        )
        assert len(bars) == 4
        assert content_js.count('role:"progressbar"') == 4
        for job in ("sync", "embedding", "musicbrainz", "audio"):
            msg_id = f"job-{job}-msg"
            assert content_js.count(f'id:"{msg_id}"') == 1
            assert sum(f'"aria-labelledby":"{msg_id}"' in bar for bar in bars) == 1

    def test_progressbar_values(self, content_js: str) -> None:
        bars = re.findall(
            r'jsx\(RadixThemesBox,\{("aria-labelledby".*?role:"progressbar")\}', content_js
        )
        for bar, var in zip(
            bars,
            (
                "sync_progress",
                "embedding_progress",
                "musicbrainz_progress",
                "audio_analysis_progress",
            ),
        ):
            assert '"aria-valuemin":"0"' in bar and '"aria-valuemax":"100"' in bar
            assert '"aria-valuenow":reflex___state' in bar and f".{var}_rx_state_," in bar
            assert '"aria-valuetext":((JSON.stringify(' in bar and '+"%")' in bar
            assert 'className:"pbar"' in bar

    def test_message_span_starts_with_the_job_name(self) -> None:
        js = rendered(
            library_page._job_bar(
                LibraryState.is_syncing,
                LibraryState.sync_message,
                LibraryState.sync_progress,
                "sync",
                "library sync",
            )
        )
        assert 'id:"job-sync-msg",ref:ref_job_sync_msg},jsx("span",{className:"sr-only"},' in js
        assert '"Library sync: "' in js
        assert "aria-live" not in js  # values are not announced on every update


class TestDialogs:
    def test_four_alert_dialogs_in_the_library(self, content_js: str) -> None:
        # Delete, Regenerate, the held removal and the whole-library embeddings
        # scope confirmation.
        assert content_js.count("jsx(RadixThemesAlertDialog.Root,") == 4
        assert content_js.count('id:"embed-all-confirm"') == 1
        assert content_js.count("jsx(RadixThemesDialog.Root,") == 1  # Tag selected tracks
        assert "RadixThemesAlertDialog.Action" not in content_js

    @pytest.mark.parametrize(
        "builder,confirm,handler",
        [
            ("_delete_confirm_dialog", "Delete", "delete_selected_tracks"),
            ("_regenerate_confirm_dialog", "Regenerate", "start_sync"),
            ("_removal_confirm_dialog", "Remove tracks", "confirm_removal"),
        ],
    )
    def test_alert_dialog_shape(self, builder: str, confirm: str, handler: str) -> None:
        js = rendered(getattr(library_page, builder)())
        assert js.startswith("jsx(RadixThemesAlertDialog.Root,")
        assert (
            'jsx(RadixThemesAlertDialog.Cancel,{},jsx("button",{className:"btn btn-3 btn-soft",'
            'type:"button"},"Cancel"))'
        ) in js
        confirm_btn = re.search(
            r'jsx\("button",\{className:"btn btn-3 btn-danger",onClick:(.*?)\},"' + confirm + '"',
            js,
        )
        assert confirm_btn and f".{handler}" in confirm_btn.group(1)
        assert "window.pmFocus&&pmFocus.restore()" in js
        assert "onCloseAutoFocus:" in js
        assert "RadixThemesAlertDialog.Title" in js and "RadixThemesAlertDialog.Description" in js

    def test_removal_confirm_is_an_alert_dialog_with_safe_default(self) -> None:
        """Spec "Confirmation dialog": an alert dialog, focus on Cancel, the scope stated."""
        js = rendered(library_page._removal_confirm_dialog())
        # An alert dialog: Escape dismisses through on_open_change, outside clicks
        # are ignored, and assistive technology is told it is modal.
        assert js.startswith("jsx(RadixThemesAlertDialog.Root,{onOpenChange:")
        root = js[: js.index("jsx(RadixThemesAlertDialog.Content,")]
        assert '.set_removal_confirm_open", ({ ["is_open"] : _ev_0 })' in root
        assert re.search(r"open:reflex___state[\w.]*\.show_removal_confirm_rx_state_\}", root)
        content = js[js.index("jsx(RadixThemesAlertDialog.Content,{") :]
        assert content.startswith('jsx(RadixThemesAlertDialog.Content,{"aria-modal":"true"')
        assert '"window.pmFocus&&pmFocus.restore()"' in content  # focus returns to the opener
        assert 'jsx(RadixThemesAlertDialog.Title,{},"Remove tracks Plex no longer lists?")' in js

        # Cancel is the AlertDialog Cancel part, so it is the safe default with focus.
        cancel = (
            'jsx(RadixThemesAlertDialog.Cancel,{},jsx("button",{className:"btn btn-3 btn-soft",'
            'type:"button"},"Cancel"))'
        )
        assert js.count("RadixThemesAlertDialog.Cancel") == 1 and cancel in js
        # The confirm is a plain danger button after Cancel, never an Action part.
        confirm = re.search(
            r'jsx\("button",\{className:"btn btn-3 btn-danger",onClick:(.*?),type:"button"\},'
            r'"Remove tracks"\)',
            js,
        )
        assert confirm and ".confirm_removal" in confirm.group(1)
        assert js.index(cancel) < confirm.start()
        assert "RadixThemesAlertDialog.Action" not in js
        assert js.count('jsx("button",') == 2

        # What it states: the count, what is lost, the backup and the second check.
        description = js[
            js.index("jsx(RadixThemesAlertDialog.Description,") : js.index("jsx(RadixThemesFlex,")
        ]
        assert re.search(
            r'jsx\("span",\{className:"lib-dialog-line"\},'
            r"reflex___state[\w.]*\.removal_scope_line_rx_state_\)",
            description,
        )
        assert "tags, embeddings and playlist entries are removed with them" in description
        assert "Your Plex library is not affected." in description
        assert "A backup of your PlexMix library is made first." in description
        assert "Plex is checked again" in description
        assert description.count('className:"lib-dialog-line"') == 3

    def test_removal_notice_renders_only_with_a_held_count(self, content_js: str) -> None:
        """Spec "Notice after the sync": shown while a removal is held, and only then."""
        js = rendered(library_page._removal_notice_region())
        # A persistent polite region: it exists before the notice is written into it.
        assert js.startswith(
            'jsx(RadixThemesBox,{id:"lib-removal-notice-region",'
            'ref:ref_lib_removal_notice_region,role:"status"},'
        )
        cond = re.search(r"\(\(reflex___state[\w.]*\.removal_notice_count_rx_state_ > 0\)\?\(", js)
        assert cond, "the notice must be under removal_notice_count > 0"
        assert js.index('id:"lib-removal-notice-region"') < cond.start()
        notice = js[cond.end() :]
        assert notice.startswith(
            'jsx(Fragment,{},jsx(RadixThemesBox,{className:"callout callout-warning",'
            'id:"lib-removal-notice"'
        )
        # Nothing at all without a held count (an empty listing offers no removal).
        assert js.endswith('"Review removal"))))):(jsx(Fragment,{},)))))')
        assert js.count("removal_notice_count_rx_state_") == 1

        assert re.search(
            r'className:"c-title"\},reflex___state[\w.]*\.removal_notice_line_rx_state_\)', notice
        )
        assert '"They were kept. Removing this many tracks needs your confirmation."' in notice
        assert 'jsx(LucideTriangleAlert,{"aria-hidden":"true",size:16},)' in notice
        review = re.search(r'jsx\("button",\{(.*?)\},"Review removal"\)', notice)
        assert review
        props = review.group(1)
        assert 'className:"btn btn-sm btn-soft lib-callout-action"' in props
        assert 'id:"lib-removal-review"' in props and 'type:"button"' in props
        assert '.open_removal_confirm", ({  })' in props
        assert "plexmix " not in js  # plain copy: no terminal command

        # On the page it sits with the sync controls, between the sync error and
        # the embeddings notice, and its confirmation is rendered once.
        actions = rendered(library_page._actions())
        order = [
            actions.index(f'id:"{region}"')
            for region in (
                "lib-sync-error-region",
                "lib-removal-notice-region",
                "lib-embed-notice-region",
            )
        ]
        assert order == sorted(order)
        assert actions.index('className:"minisel"') < order[1]
        assert content_js.count('id:"lib-removal-notice-region"') == 1
        assert content_js.count('"Remove tracks Plex no longer lists?"') == 1

    def test_bulk_tag_dialog(self) -> None:
        js = rendered(library_page._bulk_tag_dialog())
        assert js.startswith("jsx(RadixThemesDialog.Root,")
        assert 'jsx("button",{className:"btn btn-3 btn-soft",type:"button"},"Cancel")' in js
        # The confirm submits the form (Enter in the field does too); no click handler.
        confirm = re.search(r'jsx\("button",\{(className:.*?id:"lib-bulk-tags-confirm".*?)\},', js)
        assert confirm and 'type:"submit"' in confirm.group(1)
        assert "onClick" not in confirm.group(1)
        # btn-primary in Add mode, btn-danger while Replace is chosen.
        assert '? "btn btn-3 btn-danger" : "btn btn-3 btn-primary")' in confirm.group(1)
        assert '? "Replace tags" : "Add tags")' in js
        assert '"aria-label":"Tags to apply"' in js
        assert "window.pmFocus&&pmFocus.restore()" in js

    def test_no_collapsed_buttons(self, content_js: str) -> None:
        classes = re.findall(r'className:"(btn(?: [^"]*)?)"', content_js)
        assert classes
        for cls in classes:
            assert _SIZE_CLASSES & set(cls.split()), cls
        assert 'className:"btn btn-soft"' not in content_js


class TestWholePage:
    def test_page_renders_inside_the_shell(self, page_js: str) -> None:
        # One shell <main> (the login branch has its own, in another auth branch).
        assert page_js.count('jsx("main",{className:"scroll",id:"main-content"') == 1
        assert 'id:"main-heading"' in page_js
        assert page_js.count('role:"progressbar"') == 4

    def test_tracks_bound_to_state(self) -> None:
        assert isinstance(rx.foreach(LibraryState.tracks, library_page._track_row), rx.Component)


class TestTableScrollRegion:
    """webui-zoom-and-narrow-fit 6.1: the track table scrolls inside its own box."""

    def test_table_wrapper_carries_the_scroll_modifier(self, table_js: str) -> None:
        assert table_js.startswith('jsx(RadixThemesBox,{className:"tbl-wrap tbl-scroll",')
        assert table_js.count("tbl-scroll") == 1
        assert 'jsx("table",{className:"tbl"}' in table_js

    def test_only_the_track_table_scrolls(self, content_js: str) -> None:
        # The loading placeholder keeps the plain shared wrapper (nothing to scroll).
        assert content_js.count("tbl-scroll") == 1
        assert 'className:"tbl-wrap lib-skeleton"' in content_js


# ── Library filters (webui-input-responsiveness, tasks 6.2 and 6.3) ──────────

# One childless <input>: its props run to the element's closing "},)".
_INPUT = re.compile(r'jsx\("input",\{(.*?)\},\)')
# A value prop (controlled field), not defaultValue / "aria-valuenow".
_VALUE_PROP = re.compile(r'(?<![\w"])value:')
_RAW_VALUE = '_e?.["target"]?.["value"]'

# (element id, form name, setter, accessible name)
_TEXT_FILTERS: tuple[tuple[str, str, str, str], ...] = (
    ("lib-search", "search", "set_search_query", "Search tracks"),
    ("lib-genre", "genre", "set_genre_filter", "Filter by genre"),
    ("lib-year-min", "year_min", "set_year_min", "From year"),
    ("lib-year-max", "year_max", "set_year_max", "To year"),
    ("lib-tag", "tag", "set_tag_filter", "Filter by tag"),
)


def _input_props(js: str, field_id: str) -> str:
    matches = [m.group(1) for m in _INPUT.finditer(js) if f'id:"{field_id}"' in m.group(1)]
    assert len(matches) == 1, field_id
    return matches[0]


def _handler(props: str, trigger: str) -> str:
    """The text of one event-trigger prop (``onChange``/``onBlur``) up to its ReflexEvent end."""
    start = props.index(f"{trigger}:")
    return props[start : props.index("[_e]", start)]


def _hooks(component: rx.Component) -> str:
    return "\n".join(component._get_all_hooks())


@pytest.fixture(scope="module")
def filter_bar() -> rx.Component:
    return library_page._filter_bar()


@pytest.fixture(scope="module")
def filter_js(filter_bar: rx.Component) -> str:
    return rendered(filter_bar)


class TestFilterBar:
    def test_is_a_form_submitting_every_filter_on_enter(
        self, filter_bar: rx.Component, filter_js: str
    ) -> None:
        assert filter_js.startswith(
            'jsx("form",{className:"filterbar","data-enter-submit":"",id:"lib-filters",'
            "noValidate:true,onSubmit:handleSubmit_"
        )
        hooks = _hooks(filter_bar)
        assert "new FormData($form).entries()" in hooks
        assert ".apply_filter_form" in hooks
        assert ".apply_filter_form" not in filter_js.split("handleSubmit_", 1)[0]

    @pytest.mark.parametrize("field_id,name,setter,label", _TEXT_FILTERS)
    def test_text_filters_are_client_owned(
        self, filter_js: str, field_id: str, name: str, setter: str, label: str
    ) -> None:
        props = _input_props(filter_js, field_id)
        assert f'name:"{name}"' in props
        assert f'"aria-label":"{label}"' in props
        assert "defaultValue:reflex___state" in props
        assert not _VALUE_PROP.search(props), "a client-owned field has no value prop"
        on_change = _handler(props, "onChange")
        assert f'.{setter}"' in on_change and '({ ["debounce"] : 300 })' in on_change
        on_blur = _handler(props, "onBlur")
        assert f'.{setter}"' in on_blur and "debounce" not in on_blur
        assert _RAW_VALUE in on_change and _RAW_VALUE in on_blur
        assert 'autoComplete:"off"' in props and "spellCheck:false" in props

    def test_ids_match_the_state_constants(self, filter_js: str) -> None:
        from plexmix.ui.states.library_state import LIBRARY_FILTER_IDS

        assert LIBRARY_FILTER_IDS == tuple(f[0] for f in _TEXT_FILTERS)
        for field_id in LIBRARY_FILTER_IDS:
            assert filter_js.count(f'id:"{field_id}"') == 1
            assert f"ref:ref_{field_id.replace('-', '_')}" in filter_js

    @pytest.mark.parametrize(
        "field_id,label", [("lib-year-min", "From year"), ("lib-year-max", "To year")]
    )
    def test_year_fields(self, filter_js: str, field_id: str, label: str) -> None:
        props = _input_props(filter_js, field_id)
        assert 'type:"number"' in props and 'inputMode:"numeric"' in props
        assert 'min:"1000"' in props and 'max:"9999"' in props
        assert 'className:"minisel lib-year"' in props
        assert f'placeholder:"{label}"' in props
        assert '"data-digits":""' in props  # plexmix.js admits digits only
        # The raw text reaches the sanitizing setter ("" stays "", never Number("") = 0).
        assert "Number(" not in props

    @pytest.mark.parametrize("field_id,placeholder", [("lib-genre", "Genre"), ("lib-tag", "Tag")])
    def test_genre_and_tag_are_minisel(
        self, filter_js: str, field_id: str, placeholder: str
    ) -> None:
        props = _input_props(filter_js, field_id)
        assert 'className:"minisel"' in props
        assert f'placeholder:"{placeholder}"' in props

    def test_search_keeps_its_wrapper_and_hidden_icon(self, filter_js: str) -> None:
        assert re.search(
            r'jsx\(RadixThemesBox,\{className:"search"\},jsx\(LucideSearch,\{"aria-hidden":"true"',
            filter_js,
        )
        assert 'placeholder:"Search tracks\\u2026"' in _input_props(filter_js, "lib-search")

    def test_audio_checkbox_only_when_tracks_are_analyzed(self, filter_js: str) -> None:
        cond = re.search(
            r"\(!\(\(reflex___state[\w.]*audio_analyzed_tracks_rx_state_\?\.valueOf\?\.\(\) === "
            r'"0"\?\.valueOf\?\.\(\)\)\)\?\(jsx\(Fragment,\{\},jsx\("label",',
            filter_js,
        )
        assert cond, "the checkbox must be under audio_analyzed_tracks != '0'"
        label = filter_js[cond.end() : filter_js.index('"Has audio analysis")', cond.end())]
        assert 'type:"checkbox"' in label
        assert "checked:reflex___state" in label and "has_audio_rx_state_" in label
        assert '.toggle_has_audio", ({ ["checked"] : _e?.["target"]?.["checked"] })' in label
        # Otherwise nothing is rendered.
        assert '"Has audio analysis"))):(jsx(Fragment,{},))' in filter_js

    def test_clear_is_a_typed_button_debounced_behind_field_commits(self, filter_js: str) -> None:
        clear = re.search(
            r'jsx\("button",\{"aria-label":"Clear filters",(.*?)\},"Clear"\)', filter_js
        )
        assert clear
        props = clear.group(1)
        assert 'type:"button"' in props and 'className:"btn btn-3 btn-ghost"' in props
        assert '.clear_filters", ({  }), ({ ["debounce"] : 300 })' in props
        # plexmix.js empties the form's text fields in the click itself.
        assert '"data-clear-form":""' in props

    def test_no_controlled_text_field_on_the_page(self, content_js: str) -> None:
        inputs = [m.group(1) for m in _INPUT.finditer(content_js)]
        assert len(inputs) >= 7  # five filters, the audio checkbox, the bulk-tag field
        # The Tag dialog's Add/Replace radios are discrete controls (stay controlled).
        assert sum('type:"radio"' in props for props in inputs) == 2
        for props in inputs:
            if 'type:"checkbox"' in props or 'type:"radio"' in props:
                continue
            assert not _VALUE_PROP.search(props), props[:120]
            assert "defaultValue:" in props


class TestBulkTagForm:
    def test_field_commits_on_blur_and_the_form_submits(self) -> None:
        dialog = library_page._bulk_tag_dialog()
        js = rendered(dialog)
        assert re.search(r'jsx\("form",\{onSubmit:handleSubmit_\w+\},jsx\("input",', js)
        props = _input_props(js, "lib-bulk-tags")
        assert 'name:"tags"' in props and "defaultValue:reflex___state" in props
        assert not _VALUE_PROP.search(props)
        assert "onChange" not in props
        assert '.set_bulk_tag_input", ({ ["value"] : _e?.["target"]?.["value"] })' in props
        assert 'className:"input"' in props and '"aria-label":"Tags to apply"' in props
        hooks = _hooks(dialog)
        assert ".submit_bulk_tags" in hooks and "new FormData($form).entries()" in hooks

    def test_id_matches_the_state_constant(self) -> None:
        from plexmix.ui.states.library_state import LIBRARY_BULK_TAGS_ID

        assert LIBRARY_BULK_TAGS_ID == "lib-bulk-tags"


class TestFilterCss:
    def test_minisel_placeholder_and_cursor(self) -> None:
        css = _CSS.read_text(encoding="utf-8")
        assert ".minisel::placeholder { color: var(--fg-3); }" in css
        assert "input.minisel { cursor: text; }" in css
        # Next to the .minisel block, which keeps its select-style pointer for <select>.
        block = css.index(".minisel { height: 40px;")
        assert block < css.index(".minisel::placeholder") < css.index("/* modal */", block)

    def test_change_3_section_is_token_only(self) -> None:
        css = _CSS.read_text(encoding="utf-8")
        start = css.index("/* ── change 3: library filters")
        end = css.index("/* ── end change 3: library filters ── */")
        section = css[start:end]
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(", section)
        assert ".minisel.lib-year { width: 96px;" in section
